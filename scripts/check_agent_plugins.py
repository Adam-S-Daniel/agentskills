#!/usr/bin/env python3
"""Validate this repo's Agent Plugins v1 root manifests.

Each bundle under plugins/<bundle>/ carries TWO manifests, by design:

  plugins/<bundle>/plugin.json                 Agent Plugins 1.0.0 (Codex,
                                               VS Code, Cursor, Copilot)
  plugins/<bundle>/.claude-plugin/plugin.json  Claude Code

Claude Code is not an Agent Plugins conformant client and reads only the
second; the conformant clients read only the first. They coexist rather than
one replacing the other, which means the two can drift — so this script
checks the root manifests against the spec schema AND cross-checks the pair.

It then cross-checks the set it validated against the marketplace, because
"bundle" here means A DIRECTORY UNDER plugins/ and the marketplace also
publishes FEDERATED entries whose plugin root lives in another repo. Those are
never discovered, so their manifests are never validated — and this script
used to print OK anyway. It now names each one and the repo that lints it,
fails on any marketplace entry that is neither validated here nor federated,
and fails on any that is BOTH (a federated entry shadowed by a local
plugins/<name>/), so its verdict cannot disagree with check_consistency.py's
on the same tree. See check_marketplace_coverage().

VENDORED SCHEMA PROVENANCE
--------------------------
  file            schemas/agent-plugins-1.0.0-plugin.schema.json
  source          https://agent-plugins.org/schemas/1.0.0/plugin.schema.json
  mirror          https://raw.githubusercontent.com/agentplugins/
                  agent-plugins-spec/main/schemas/1.0.0/plugin.schema.json
                  (verified byte-identical to the canonical host)
  size            1805 bytes
  sha256          0a4aad95ce337878ad38802ebf0daa3fde76abe3f65400c86bcbb1ec0b3ab883
  retrieved       2026-08-14

The schema is VENDORED rather than fetched because the spec repo publishes no
git tags and no releases — there is nothing to pin a fetch to, so a fetch
would silently track whatever `main` says today. JSON has no comment syntax,
so the provenance lives in this docstring, and SCHEMA_SHA256 below turns it
into an assertion: tamper with the vendored bytes and this script fails
rather than validating against something nobody reviewed.

Notes on the schema, since they drive what the manifests may contain:
  * it is CLOSED (additionalProperties: false) — the marketplace-only keys
    `category` and `defaultEnabled` are invalid here, as is any `skills` key
    (skills are discovered by convention at <plugin-root>/skills/);
  * only `$schema` and `name` are required, and `$schema` is a `const`;
  * `author` is closed too — name/email/url only;
  * client-specific data belongs under `extensions`, keyed by reverse domain.

Usage:  python3 scripts/check_agent_plugins.py
Exits 0 when every check passes, 1 otherwise, listing every problem found.
Requires: jsonschema (pip install jsonschema)
"""

import hashlib
import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only without the dep
    sys.stderr.write(
        "ERROR: the 'jsonschema' package is required.\n"
        "       Install it with: python3 -m pip install jsonschema\n"
    )
    raise SystemExit(1)

# Same directory, so running this as a script puts it on sys.path — but be
# explicit, because pytest and `python -m` do not both agree on that.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Reuse the classifier rather than re-deriving "local or federated?" here. Two
# copies would eventually disagree, and the disagreement would present as one
# CI job passing an entry the other rejects — with no way to tell which was right.
from check_consistency import MARKETPLACE_PATH, classify_source  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO_ROOT / "plugins"
SCHEMA_PATH = REPO_ROOT / "schemas" / "agent-plugins-1.0.0-plugin.schema.json"

# sha256 of the bytes retrieved 2026-08-14 — see the provenance block above.
SCHEMA_SHA256 = "0a4aad95ce337878ad38802ebf0daa3fde76abe3f65400c86bcbb1ec0b3ab883"

# The one value the spec pins by `const`. Codex rejects anything else outright
# ("unsupported Agent Plugins schema"), so a typo here is not a soft failure.
SCHEMA_URI = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"

# Fields the two manifests must agree on. `name` keys the plugin identity
# across both ecosystems; `version` is carried twice and is therefore a
# standing drift hazard — bump one, forget the other, and the client that
# reads the stale one reports the wrong version forever.
CROSS_CHECKED_FIELDS = ("name", "version")

ROOT_MANIFEST = "plugin.json"
CLAUDE_MANIFEST = Path(".claude-plugin") / "plugin.json"


def _rel(path):
    """Path relative to the repo root, for readable error messages."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def check_schema_integrity(problems, schema_path=SCHEMA_PATH, expected=SCHEMA_SHA256):
    """Verify the vendored schema is the reviewed one, and return it parsed.

    Returns None when the schema is missing or altered — the caller then has
    nothing trustworthy to validate against and must stop.
    """
    if not schema_path.is_file():
        problems.append("%s: vendored schema is missing" % _rel(schema_path))
        return None

    raw = schema_path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        problems.append(
            "%s: sha256 mismatch — expected %s, got %s. The vendored schema was "
            "modified; re-fetch it from %s and update SCHEMA_SHA256 only after "
            "reviewing the diff."
            % (_rel(schema_path), expected, actual, SCHEMA_URI)
        )
        return None

    try:
        return json.loads(raw)
    except ValueError as exc:
        problems.append("%s: not valid JSON (%s)" % (_rel(schema_path), exc))
        return None


def _load_json(path, problems):
    """Parse a JSON file, recording a problem and returning None on failure."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError as exc:
        problems.append("%s: not valid JSON (%s)" % (_rel(path), exc))
    except OSError as exc:
        problems.append("%s: cannot be read (%s)" % (_rel(path), exc))
    return None


def discover_bundles(plugins_dir=PLUGINS_DIR):
    """Every bundle directory, derived from the filesystem — no hardcoded names.

    A bundle is any directory under plugins/ that carries either manifest, so
    a half-built bundle is discovered and then reported as incomplete rather
    than skipped into silence.
    """
    if not plugins_dir.is_dir():
        return []
    bundles = []
    for entry in sorted(plugins_dir.iterdir()):
        if not entry.is_dir():
            continue
        if (entry / ROOT_MANIFEST).is_file() or (entry / CLAUDE_MANIFEST).is_file():
            bundles.append(entry)
    return bundles


def check_bundle(bundle, schema, problems):
    """Validate one bundle's root manifest and cross-check it against Claude's."""
    root_path = bundle / ROOT_MANIFEST
    claude_path = bundle / CLAUDE_MANIFEST

    if not root_path.is_file():
        # A new bundle must not be able to ship without a conformant manifest.
        problems.append(
            "%s: has %s but no root %s — every bundle needs an Agent Plugins "
            "manifest or it is invisible to Codex/VS Code/Cursor/Copilot."
            % (bundle.name, _rel(claude_path), ROOT_MANIFEST)
        )
        return

    root = _load_json(root_path, problems)
    if root is None:
        return

    validator = jsonschema.Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(root), key=lambda e: list(e.absolute_path)):
        location = "/".join(str(part) for part in error.absolute_path) or "(document root)"
        problems.append("%s: %s — %s" % (_rel(root_path), location, error.message))

    if not claude_path.is_file():
        problems.append(
            "%s: root %s exists but %s does not"
            % (bundle.name, ROOT_MANIFEST, _rel(claude_path))
        )
        return

    claude = _load_json(claude_path, problems)
    if claude is None:
        return

    for field in CROSS_CHECKED_FIELDS:
        root_value = root.get(field)
        claude_value = claude.get(field)
        if root_value != claude_value:
            problems.append(
                "%s: %r disagrees between manifests — %s has %r, %s has %r. "
                "Both are shipped; they must describe the same plugin."
                % (
                    bundle.name,
                    field,
                    _rel(root_path),
                    root_value,
                    _rel(claude_path),
                    claude_value,
                )
            )


def check_marketplace_coverage(bundles, problems, marketplace_path=MARKETPLACE_PATH):
    """Account for every marketplace entry; return the notices to print.

    discover_bundles() defines a bundle as a DIRECTORY under plugins/ carrying
    a manifest. A federated marketplace entry has no such directory, so it is
    never discovered, its manifests are never schema-validated — and before
    this function the script still reported success. That is the same shape of
    hole as a `claude plugin validate --strict` that passes a bundle whose
    frontmatter YAML does not parse: a green check asserting nothing.

    Closing it by cloning the remote repo would be the wrong trade. That repo
    lints its own manifests in its own CI, in the same change that adds them,
    and a cross-repo clone here would buy a duplicate check at the cost of a
    network dependency in a pure-filesystem job. So the gap is made VISIBLE
    and NAMED instead of closed — the same posture this fleet already mandates
    for a credential-dependent workflow: absent capability produces a clear
    notice naming the exact thing, never a crash and never a silent no-op.

    What is NOT tolerated is an entry that is neither: a locally sourced entry
    with no discovered bundle is published and validated by nothing at all,
    and an entry whose source cannot be classified cannot be reasoned about.
    Both fail.

    Nor is an entry that is BOTH. A federated entry shadowed by a local
    plugins/<name>/ bundle makes the notice self-contradicting — "validated by
    that repo's CI, not here" printed about a bundle this very run validated
    here — and, worse, makes this script's verdict differ from
    check_consistency.py's on the identical tree. The shadowing rule therefore
    lives in both places rather than in whichever one happens to run first.
    """
    notices = []
    if not marketplace_path.is_file():
        problems.append(
            "%s: marketplace manifest is missing, so there is no way to tell "
            "whether every published plugin was validated" % _rel(marketplace_path)
        )
        return notices

    marketplace = _load_json(marketplace_path, problems)
    if marketplace is None:
        return notices

    local_names = {bundle.name for bundle in bundles}
    for entry in marketplace.get("plugins", []):
        name = entry.get("name") or "(unnamed entry)"
        kind, detail = classify_source(entry)
        if kind == "federated" and name in local_names:
            # The notice below is a claim about WHERE this bundle is validated.
            # If a local plugins/<name>/ was also discovered, that claim is
            # false in the same breath it is made: the run would print
            # "validated by that repo's own CI, not here" AND count <name>
            # among the manifests it just validated here. check_consistency.py
            # already rejects exactly this shadowing; if this script only
            # noticed it, the two would return different verdicts on the same
            # tree and CI would have no single answer.
            problems.append(
                "%s: marketplace.json federates it from %s, but a local bundle under "
                "%s/%s was discovered and validated here — so this run cannot say "
                "which tree ships under that name. A name cannot be both a local "
                "plugin root and a remote one; delete one of them "
                "(check_consistency.py rejects the same shadowing)."
                % (name, detail, _rel(PLUGINS_DIR), name)
            )
        elif kind == "federated":
            notices.append(
                "%s: federated from %s — its Agent Plugins manifests are validated "
                "by that repo's own CI, not here." % (name, detail)
            )
        elif kind == "invalid":
            problems.append(
                "%s: marketplace entry %s, so this script cannot tell whether its "
                "manifests are validated anywhere." % (name, detail)
            )
        elif name not in local_names:
            problems.append(
                "%s: marketplace.json publishes it from the local source '%s', but no "
                "bundle manifest was discovered under %s/%s — nothing validates it."
                % (name, detail, _rel(PLUGINS_DIR), name)
            )
    return notices


def main():
    problems = []

    schema = check_schema_integrity(problems)
    if schema is None:
        for problem in problems:
            print("FAIL: %s" % problem)
        return 1

    bundles = discover_bundles()
    if not bundles:
        print("FAIL: no plugin bundles found under %s" % _rel(PLUGINS_DIR))
        return 1

    for bundle in bundles:
        check_bundle(bundle, schema, problems)

    # Printed before the verdict, and on the failure path too: a named skip is
    # only useful if it is visible in the run someone actually reads.
    for notice in check_marketplace_coverage(bundles, problems):
        print("NOTE: %s" % notice)

    if problems:
        for problem in problems:
            print("FAIL: %s" % problem)
        print("\n%d problem(s) found." % len(problems))
        return 1

    print(
        "OK: %d Agent Plugins 1.0.0 manifest(s) valid and consistent with their "
        ".claude-plugin/ counterparts (%s)."
        % (len(bundles), ", ".join(b.name for b in bundles))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
