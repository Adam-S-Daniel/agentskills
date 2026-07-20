#!/usr/bin/env python3
"""
repo_settings.py -- repo-settings-as-code for GitHub Actions security settings
and default-branch protection (via GitHub repository rulesets).

Three subcommands:

  generate   Introspect a repo's CURRENT settings and emit a repo-settings YAML
             document to stdout (so you can commit it and edit it).

  diff       Compare DESIRED settings (from a config file) against the CURRENT
             live settings and print a drift report. Makes no changes. Exits 1
             if there is drift, 0 if everything already matches, 2 on error.

  apply      Apply DESIRED settings from a config file to the live repo(s).
             Prints per-setting CHANGED / ok / skipped. Use --dry-run to preview.
             Exits non-zero if any apply errored.

Managed settings:
  * actions.sha_pinning_required           -> repos/{repo}/actions/permissions
  * actions.fork_pr_approval               -> .../actions/permissions/fork-pr-contributor-approval
  * ruleset (default-branch protection)    -> repos/{repo}/rulesets  (idempotent BY NAME)

We use repository RULESETS rather than classic branch protection so the fleet
speaks the same primitive as the cms-platform-managed repos. A managed ruleset
is identified by its `name`: apply creates it if absent, updates it in place if
present, and never touches other rulesets.

All GitHub access goes through the `gh` CLI, so authentication is whatever `gh`
is configured with: locally your `gh auth` keyring, or in CI the `GH_TOKEN`
environment variable. Writing these settings requires repo-admin (a fine-grained
PAT with "Administration: read and write", a classic PAT with `repo` scope, or a
GitHub App token). The default Actions `GITHUB_TOKEN` cannot change repo settings.

Config schema: see ../assets/repo-settings.schema.md. Two shapes are accepted:

  * Single-repo -- top-level `actions:` / `ruleset:` blocks, optional top-level
    `repo: owner/name` (or pass --repo).

  * Fleet / fan-out -- a top-level `repos:` list, with an optional `defaults:`
    block that every repo inherits and per-repo `overrides:`. A repo entry with
    `manage: false` is skipped (recorded as excluded).

The engine auto-detects repo visibility and downgrades gracefully: fork-PR
approval (API 422) and rulesets (API 403 "upgrade to Pro") are skipped on private
repos with a logged reason, so one baseline can target public and private repos.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("error: PyYAML is required. Install with `pip install pyyaml`.\n")
    sys.exit(2)

DEFAULT_RULESET_NAME = "default branch protection"
ADMIN_ROLE_ID = 5  # RepositoryRole id for "Admin" (used for bypass actors)
BYPASS_ACTOR_TYPES = {
    "Integration",
    "OrganizationAdmin",
    "RepositoryRole",
    "Team",
    "DeployKey",
}


# --------------------------------------------------------------------------- #
# gh plumbing
# --------------------------------------------------------------------------- #
class GhError(Exception):
    def __init__(self, status: int | None, message: str):
        self.status = status
        self.message = message
        super().__init__(f"HTTP {status}: {message}" if status else message)


def gh_api(path: str, method: str = "GET", body: dict | None = None) -> Any:
    """Call `gh api`. Return parsed JSON (or None for empty). Raise GhError on
    HTTP failure, parsing the HTTP status out of gh's output when possible."""
    cmd = ["gh", "api", "-X", method, path]
    stdin = None
    if body is not None:
        cmd += ["--input", "-"]
        stdin = json.dumps(body)
    proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    out = proc.stdout.strip()
    err = proc.stderr.strip()
    if proc.returncode != 0:
        status = None
        for token in ("400", "401", "402", "403", "404", "409", "422", "500"):
            if token in err or token in out:
                status = int(token)
                break
        message = err or out
        try:
            payload = json.loads(out)
            if isinstance(payload, dict) and payload.get("message"):
                message = payload["message"]
                if payload.get("errors"):
                    message += f" ({payload['errors']})"
        except (ValueError, TypeError):
            pass
        raise GhError(status, message)
    if not out:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return out


def get_repo(repo: str) -> dict:
    return gh_api(f"repos/{repo}")


def default_branch(repo: str) -> str:
    return get_repo(repo)["default_branch"]


# --------------------------------------------------------------------------- #
# Actions settings readers/writers
# --------------------------------------------------------------------------- #
def read_actions(repo: str) -> dict:
    return gh_api(f"repos/{repo}/actions/permissions")


def read_fork_approval(repo: str) -> str | None:
    try:
        data = gh_api(f"repos/{repo}/actions/permissions/fork-pr-contributor-approval")
        return data.get("approval_policy")
    except GhError as e:
        if e.status in (403, 404, 422):
            return None
        raise


def write_sha_pinning(repo: str, current: dict, desired: bool) -> None:
    gh_api(
        f"repos/{repo}/actions/permissions",
        method="PUT",
        body={
            # enabled + allowed_actions are required in the PUT; preserve them.
            "enabled": current.get("enabled", True),
            "allowed_actions": current.get("allowed_actions", "all"),
            "sha_pinning_required": desired,
        },
    )


def write_fork_approval(repo: str, desired: str) -> None:
    gh_api(
        f"repos/{repo}/actions/permissions/fork-pr-contributor-approval",
        method="PUT",
        body={"approval_policy": desired},
    )


# --------------------------------------------------------------------------- #
# Ruleset handling
# --------------------------------------------------------------------------- #
def list_rulesets(repo: str) -> list[dict]:
    """Return branch rulesets, or raise GhError (403 on private-no-Pro)."""
    data = gh_api(f"repos/{repo}/rulesets")
    return data or []


def get_ruleset(repo: str, ruleset_id: int) -> dict:
    return gh_api(f"repos/{repo}/rulesets/{ruleset_id}")


def find_ruleset(repo: str, name: str) -> dict | None:
    """Return the full ruleset with the given name, or None."""
    summary = next((r for r in list_rulesets(repo) if r.get("name") == name), None)
    if summary is None:
        return None
    return get_ruleset(repo, summary["id"])


def build_ruleset_body(cfg: dict) -> dict:
    """Build the POST/PUT body for a desired ruleset from config."""
    name = cfg.get("name", DEFAULT_RULESET_NAME)
    if cfg.get("target_branches"):
        include = [f"refs/heads/{b}" for b in cfg["target_branches"]]
    else:
        include = ["~DEFAULT_BRANCH"]
    rules: list[dict] = []
    if cfg.get("block_deletions", True):
        rules.append({"type": "deletion"})
    if cfg.get("block_force_pushes", True):
        rules.append({"type": "non_fast_forward"})
    pr = cfg.get("require_pull_request")
    if pr is not None:
        rules.append(
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": pr.get(
                        "required_approving_review_count", 0
                    ),
                    "dismiss_stale_reviews_on_push": pr.get(
                        "dismiss_stale_reviews", False
                    ),
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": False,
                    "allowed_merge_methods": ["merge", "squash", "rebase"],
                },
            }
        )
    checks = cfg.get("required_status_checks") or []
    if checks:
        rules.append(
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "required_status_checks": [
                        {"context": c} for c in checks
                    ],
                },
            }
        )
    bypass: list[dict] = []
    if cfg.get("admin_bypass", True):
        bypass.append(
            {
                "actor_id": ADMIN_ROLE_ID,
                "actor_type": "RepositoryRole",
                "bypass_mode": "always",
            }
        )
    for actor in cfg.get("bypass_actors") or []:
        if not isinstance(actor, dict) or "actor_id" not in actor:
            raise SystemExit(
                f"error: bypass_actors entries must be mappings with an "
                f"actor_id (got {actor!r})"
            )
        actor_type = actor.get("actor_type")
        if actor_type not in BYPASS_ACTOR_TYPES:
            raise SystemExit(
                f"error: bypass_actors entry has invalid actor_type "
                f"{actor_type!r} (expected one of "
                f"{', '.join(sorted(BYPASS_ACTOR_TYPES))})"
            )
        entry = {
            "actor_id": int(actor["actor_id"]),
            "actor_type": actor_type,
            "bypass_mode": actor.get("bypass_mode", "always"),
        }
        if entry not in bypass:
            bypass.append(entry)
    return {
        "name": name,
        "target": "branch",
        "enforcement": cfg.get("enforcement", "active"),
        "conditions": {"ref_name": {"include": include, "exclude": []}},
        "rules": rules,
        "bypass_actors": bypass,
    }


def normalize_ruleset(rs: dict | None) -> dict | None:
    """Reduce a ruleset (live or a desired body) to the fields we manage, so
    current vs desired can be compared field-for-field."""
    if rs is None:
        return None
    rules_by_type: dict[str, dict] = {}
    for r in rs.get("rules", []):
        rules_by_type[r["type"]] = r.get("parameters", {}) or {}
    pr = rules_by_type.get("pull_request", {})
    checks = sorted(
        c["context"]
        for c in rules_by_type.get("required_status_checks", {}).get(
            "required_status_checks", []
        )
    )
    include = sorted(
        rs.get("conditions", {}).get("ref_name", {}).get("include", [])
    )
    bypass = sorted(
        (b.get("actor_type"), b.get("actor_id"), b.get("bypass_mode"))
        for b in rs.get("bypass_actors", [])
    )
    return {
        "enforcement": rs.get("enforcement"),
        "include": include,
        "rule_types": sorted(rules_by_type.keys()),
        "pr_approvals": pr.get("required_approving_review_count"),
        "pr_dismiss_stale": bool(pr.get("dismiss_stale_reviews_on_push", False)),
        "checks": checks,
        "bypass": bypass,
    }


def ruleset_doc(branch_rs: dict) -> dict:
    """Reduce a live branch ruleset to the config-schema `ruleset:` block
    (the inverse of build_ruleset_body, for `generate`)."""
    norm = normalize_ruleset(branch_rs)
    doc = {
        "enabled": True,
        "name": branch_rs.get("name"),
        "enforcement": branch_rs.get("enforcement"),
        "require_pull_request": {
            "required_approving_review_count": norm["pr_approvals"] or 0
        }
        if "pull_request" in norm["rule_types"]
        else None,
        "block_force_pushes": "non_fast_forward" in norm["rule_types"],
        "block_deletions": "deletion" in norm["rule_types"],
        "required_status_checks": norm["checks"],
        "admin_bypass": any(
            t == "RepositoryRole" and i == ADMIN_ROLE_ID
            for (t, i, _m) in norm["bypass"]
        ),
    }
    extra = [
        {"actor_type": t, "actor_id": i, "bypass_mode": m}
        for (t, i, m) in norm["bypass"]
        if not (t == "RepositoryRole" and i == ADMIN_ROLE_ID)
    ]
    if extra:
        doc["bypass_actors"] = extra
    return doc


def write_ruleset(repo: str, body: dict, existing_id: int | None) -> None:
    if existing_id is not None:
        gh_api(f"repos/{repo}/rulesets/{existing_id}", method="PUT", body=body)
    else:
        gh_api(f"repos/{repo}/rulesets", method="POST", body=body)


# --------------------------------------------------------------------------- #
# Config handling
# --------------------------------------------------------------------------- #
def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def resolve_targets(cfg: dict, repo_arg: str | None) -> list[dict]:
    if "repos" in cfg:  # fleet mode
        defaults = cfg.get("defaults", {})
        targets = []
        for entry in cfg["repos"]:
            if isinstance(entry, str):
                entry = {"name": entry}
            targets.append(
                {
                    "repo": entry["name"],
                    "settings": deep_merge(defaults, entry.get("overrides", {})),
                    "manage": entry.get("manage", True),
                }
            )
        return targets
    repo = repo_arg or cfg.get("repo")
    if not repo:
        raise SystemExit(
            "error: single-repo config needs a top-level `repo: owner/name` "
            "or a --repo argument."
        )
    settings = {k: v for k, v in cfg.items() if k in ("actions", "ruleset")}
    return [{"repo": repo, "settings": settings, "manage": True}]


# --------------------------------------------------------------------------- #
# Planning: desired vs current -> list of change records
# --------------------------------------------------------------------------- #
def plan_repo(repo: str, settings: dict) -> list[dict]:
    records: list[dict] = []
    meta: dict = {}  # cache for private lookup
    actions_cfg = settings.get("actions") or {}
    ruleset_cfg = settings.get("ruleset") or {}

    def is_private() -> bool:
        if "private" not in meta:
            meta["private"] = bool(get_repo(repo)["private"])
        return meta["private"]

    # -- sha pinning -------------------------------------------------------- #
    if "sha_pinning_required" in actions_cfg:
        desired = bool(actions_cfg["sha_pinning_required"])
        try:
            cur = read_actions(repo)
            current = bool(cur.get("sha_pinning_required"))
            records.append(
                {
                    "setting": "actions.sha_pinning_required",
                    "status": "match" if current == desired else "drift",
                    "current": current,
                    "desired": desired,
                    "_writer": ("sha", cur, desired),
                }
            )
        except GhError as e:
            records.append(_err("actions.sha_pinning_required", desired, e))

    # -- fork PR approval --------------------------------------------------- #
    fork = actions_cfg.get("fork_pr_approval", "unset")
    if fork != "unset" and fork is not None:
        if is_private():
            records.append(_skip("actions.fork_pr_approval", fork,
                                 "private repo -- fork-PR approval not applicable (API 422)"))
        else:
            try:
                current = read_fork_approval(repo)
                records.append(
                    {
                        "setting": "actions.fork_pr_approval",
                        "status": "match" if current == fork else "drift",
                        "current": current,
                        "desired": fork,
                        "_writer": ("fork", None, fork),
                    }
                )
            except GhError as e:
                records.append(_err("actions.fork_pr_approval", fork, e))

    # -- ruleset ------------------------------------------------------------ #
    if ruleset_cfg.get("enabled"):
        name = ruleset_cfg.get("name", DEFAULT_RULESET_NAME)
        body = build_ruleset_body(ruleset_cfg)
        try:
            existing = find_ruleset(repo, name)
            existing_id = existing["id"] if existing else None
            same = normalize_ruleset(existing) == normalize_ruleset(body)
            records.append(
                {
                    "setting": f"ruleset[{name}]",
                    "status": "match" if same else "drift",
                    "current": normalize_ruleset(existing),
                    "desired": normalize_ruleset(body),
                    "_writer": ("ruleset", body, existing_id),
                }
            )
        except GhError as e:
            if e.status in (402, 403) and is_private():
                records.append(_skip(f"ruleset[{name}]", "enabled",
                                     "private repo without GitHub Pro -- rulesets "
                                     "not available (API 403)"))
            else:
                records.append(_err(f"ruleset[{name}]", "enabled", e))

    return records


def plan_repo_records(repo: str, settings: dict) -> list[dict]:
    """plan_repo with per-repo fault isolation: a repo-level API failure
    (deleted or renamed repo, no access) becomes one error record instead of
    aborting the whole fleet run."""
    try:
        return plan_repo(repo, settings)
    except GhError as e:
        return [
            {
                "setting": "repo",
                "status": "error",
                "current": None,
                "desired": None,
                "note": e.message,
            }
        ]


def _err(setting: str, desired: Any, e: GhError) -> dict:
    return {"setting": setting, "status": "error", "current": None,
            "desired": desired, "note": e.message}


def _skip(setting: str, desired: Any, note: str) -> dict:
    return {"setting": setting, "status": "skipped", "current": None,
            "desired": desired, "note": note}


def apply_record(repo: str, rec: dict) -> None:
    kind, a, b = rec["_writer"]
    if kind == "sha":
        write_sha_pinning(repo, a, b)
    elif kind == "fork":
        write_fork_approval(repo, b)
    elif kind == "ruleset":
        write_ruleset(repo, a, b)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
GREEN, YELLOW, RED, DIM, RESET = (
    "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
)


def _sym(status: str) -> str:
    return {
        "match": f"{GREEN}ok{RESET}",
        "drift": f"{YELLOW}DRIFT{RESET}",
        "changed": f"{YELLOW}CHANGED{RESET}",
        "skipped": f"{DIM}skip{RESET}",
        "error": f"{RED}ERROR{RESET}",
        "would-change": f"{YELLOW}would change{RESET}",
    }.get(status, status)


def render_records(repo: str, records: list[dict]) -> None:
    print(f"\n{repo}")
    if not records:
        print("  (no managed settings)")
        return
    for r in records:
        line = f"  [{_sym(r['status'])}] {r['setting']}"
        if r["status"] in ("drift", "would-change"):
            line += f"\n      from: {r['current']!r}\n      to:   {r['desired']!r}"
        elif r["status"] == "changed":
            line += f"  -> {r['desired']!r}"
        elif r["status"] in ("skipped", "error"):
            line += f"  ({r.get('note', '')})"
        print(line)


# --------------------------------------------------------------------------- #
# generate
# --------------------------------------------------------------------------- #
def cmd_generate(args) -> int:
    repo = args.repo
    info = get_repo(repo)
    private = bool(info["private"])
    actions = read_actions(repo)
    doc: dict = {
        "repo": repo,
        "actions": {"sha_pinning_required": bool(actions.get("sha_pinning_required"))},
    }
    if not private:
        fork = read_fork_approval(repo)
        if fork is not None:
            doc["actions"]["fork_pr_approval"] = fork
    # Emit a ruleset block from the branch ruleset targeting the default branch,
    # if any is present; otherwise a disabled stub for public repos.
    try:
        rulesets = list_rulesets(repo)
        branch_rs = None
        for summary in rulesets:
            if summary.get("target") == "branch":
                branch_rs = get_ruleset(repo, summary["id"])
                break
        if branch_rs:
            doc["ruleset"] = ruleset_doc(branch_rs)
        elif not private:
            doc["ruleset"] = {"enabled": False, "name": DEFAULT_RULESET_NAME}
    except GhError:
        pass  # rulesets unavailable (e.g. private-no-Pro)
    header = (
        "# Generated by repo_settings.py generate. Edit to describe DESIRED state,\n"
        "# then `repo_settings.py apply --config <this file>`.\n"
    )
    sys.stdout.write(header)
    yaml.safe_dump(doc, sys.stdout, sort_keys=False, default_flow_style=False)
    return 0


# --------------------------------------------------------------------------- #
# diff / apply
# --------------------------------------------------------------------------- #
def _filter_owner(targets: list[dict], owner: str | None) -> list[dict]:
    """Keep only targets under `owner` (the part before '/'). Used by the
    fan-out workflow to run one pass per account with that account's
    short-lived GitHub App installation token."""
    if not owner:
        return targets
    o = owner.lower()
    return [t for t in targets if t["repo"].split("/", 1)[0].lower() == o]


def cmd_diff(args) -> int:
    cfg = load_config(args.config)
    targets = _filter_owner(resolve_targets(cfg, args.repo), getattr(args, "owner", None))
    any_drift = any_error = False
    for t in targets:
        if not t["manage"]:
            print(f"\n{t['repo']}\n  {DIM}excluded (manage: false){RESET}")
            continue
        records = plan_repo_records(t["repo"], t["settings"])
        render_records(t["repo"], records)
        any_drift |= any(r["status"] == "drift" for r in records)
        any_error |= any(r["status"] == "error" for r in records)
    if any_error:
        return 2
    return 1 if any_drift else 0


def cmd_apply(args) -> int:
    cfg = load_config(args.config)
    targets = _filter_owner(resolve_targets(cfg, args.repo), getattr(args, "owner", None))
    any_error = False
    for t in targets:
        if not t["manage"]:
            print(f"\n{t['repo']}\n  {DIM}excluded (manage: false){RESET}")
            continue
        records = plan_repo_records(t["repo"], t["settings"])
        for r in records:
            if r["status"] == "drift":
                if args.dry_run:
                    r["status"] = "would-change"
                else:
                    try:
                        apply_record(t["repo"], r)
                        r["status"] = "changed"
                    except GhError as e:
                        r["status"] = "error"
                        r["note"] = e.message
        render_records(t["repo"], records)
        any_error |= any(r["status"] == "error" for r in records)
    return 2 if any_error else 0


# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description="repo-settings-as-code for GitHub")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="emit current settings as YAML")
    g.add_argument("--repo", required=True, help="owner/name")
    g.set_defaults(func=cmd_generate)

    d = sub.add_parser("diff", help="drift report; exit 1 if drift")
    d.add_argument("--config", required=True)
    d.add_argument("--repo", help="override/set target for single-repo config")
    d.add_argument("--owner", help="only process repos under this owner (account)")
    d.set_defaults(func=cmd_diff)

    a = sub.add_parser("apply", help="apply desired settings")
    a.add_argument("--config", required=True)
    a.add_argument("--repo", help="override/set target for single-repo config")
    a.add_argument("--owner", help="only process repos under this owner (account)")
    a.add_argument("--dry-run", action="store_true", help="preview only")
    a.set_defaults(func=cmd_apply)

    args = p.parse_args()
    try:
        return args.func(args)
    except GhError as e:
        sys.stderr.write(f"gh error: {e}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
