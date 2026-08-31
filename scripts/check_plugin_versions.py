#!/usr/bin/env python3
"""check_plugin_versions.py — a PR that touches plugins/<bundle>/ must also
raise that bundle's declared version above its value at the PR base.

Why this exists
----------------
`claude plugin update` gates ONLY on the declared `version` string (ADR 0009,
docs/decisions/0009-bump-bundle-versions-on-every-release.md). Every bundle
has shipped `1.0.0` since the field was introduced, so `update` has never
been able to fire for any consumer of this registry — however many commits a
bundle's content moves. This script does not decide what a bundle's next
version SHOULD be; a maintainer still picks that, as always. It only refuses
to let a content change land with no version movement at all, because that is
the one property `update` actually depends on.

What "changed" means
---------------------
Every file under plugins/<bundle>/ — tracked or not — as it stands in the
CURRENT WORKING TREE, compared against `--base`. Deliberately "working tree",
not "HEAD": in CI the working tree already IS the checked-out PR head (a
clean checkout, so the two coincide), but this script is also meant to be run
against an in-progress, not-yet-committed change — which is how it is
demonstrated against this very repo (see the ADR's "How to verify"). `git
diff <base> -- <path>` alone answers exactly that ("working tree vs base"),
except for one gap: it cannot see a file that exists on disk but was never
`git add`ed, because git diff never looks at untracked files regardless of
what it is being compared to. See _changed_paths() for how that gap is
closed.

What "the version" means
-------------------------
Each bundle carries its version TWICE (ADR 0009's second constraint) —
plugins/<bundle>/plugin.json (Agent Plugins v1) and
plugins/<bundle>/.claude-plugin/plugin.json (Claude Code). This script checks
each one INDEPENDENTLY against its own value at `--base`: both must have
moved forward, not just one. check_agent_plugins.py separately asserts the
two AGREE with each other; this script does not re-derive that, it only adds
the "moved forward" requirement on top — the same "one reader, one answer"
reasoning check_agent_plugins.py itself uses for classify_source().

The base version is read out of the git object store — `git show
<base>:<path>` — never off disk, per the ADR: a lock read from disk would be
answering "what does the file on disk say NOW", which is not "at base"
however the caller phrases the question. The version is always PARSED as
JSON, never grepped: a version-string line scan reads clean on a manifest it
was never pointed at, which is the exact failure this repo has already been
bitten by once (see AGENTS.md, "the watch finished" / `python3 test_foo.py`).

Usage:
  python3 scripts/check_plugin_versions.py --base <ref-or-sha>

Exits 0 when every bundle whose content changed also raised its version (or
no bundle changed at all, or a changed bundle is brand new at `--base` and so
carries no prior version to raise above). Exits 1 and lists every failure —
not just the first — otherwise.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# Same directory, so running this as a script puts it on sys.path — but be
# explicit, because pytest and `python -m` do not both agree on that.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Reused rather than re-derived: check_agent_plugins.py already answers "what
# counts as a bundle directory" (discover_bundles) and "what are the two
# manifest paths called" (ROOT_MANIFEST / CLAUDE_MANIFEST). A second copy of
# either here is exactly how this script and check_agent_plugins.py would
# eventually disagree about which directories are bundles or where their
# manifests live.
from check_agent_plugins import CLAUDE_MANIFEST, ROOT_MANIFEST, discover_bundles  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# Same rationale as generate_skills_lock.py's identical constant: every git
# invocation here has EOL translation turned off so a Windows runner's default
# `core.autocrlf` cannot make `git diff`/`git show` disagree with a Linux run
# about whether a file "changed". This script never hashes bytes — it only
# lists changed paths and parses a JSON version field — so the stakes are
# lower than generate_skills_lock.py's digesting, but the fix costs nothing
# and removes a platform variable this script would otherwise carry silently.
_GIT_VERBATIM = ("-c", "core.autocrlf=false", "-c", "core.eol=lf")

# A plain X.Y.Z, nothing else. Deliberately narrower than the full semver 2.0
# grammar (no pre-release/build metadata): every version this repo has ever
# shipped is X.Y.Z (ADR 0009's own before/after is 1.0.0 -> 1.1.0), so a
# pre-release suffix showing up here is far more likely to be a typo than an
# intentional scheme this script would then have to define an ordering for.
# Unparseable is reported as a problem — never silently treated as "lower" or
# "equal", either of which would hide a real manifest defect behind this
# script's own verdict.
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class _CheckError(Exception):
    """One bundle's check could not be completed — report it as a failure.

    Distinct from "this manifest simply did not exist yet" (see
    _manifest_version_at_ref/_manifest_version_on_disk returning (None, None)
    for that case, which is NOT an error): this is for a git command failing,
    a manifest that exists but will not parse, or one with no `version` field
    at all. All of those are real problems the run must surface, not skip.
    """


def parse_semver(raw: object) -> Optional[Tuple[int, int, int]]:
    """Parse an 'X.Y.Z' version string into a comparable (int, int, int).

    Returns None for anything that does not match — a non-string value, extra
    components, a pre-release/build suffix, leading '+'/'v', etc. The caller
    turns that into a named problem rather than a bare comparison failure, so
    a malformed version reads as "this needs fixing", not as "unchanged".
    """
    if not isinstance(raw, str):
        return None
    match = _SEMVER_RE.match(raw.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *_GIT_VERBATIM, "-C", str(repo_root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _validate_base(repo_root: Path, base: str) -> None:
    """Fail fast and clearly when `--base` does not resolve here.

    Every downstream `git show <base>:...` / `git diff <base> ...` treats a
    non-zero exit as "path did not exist at base" — a reading that is only
    correct once `base` itself is known to resolve. Without this check, a
    typo'd --base would silently read as "every bundle is brand new", which
    is the opposite of a gate: it would report success on a PR whose bundle
    content changed with no bump, for the wrong reason.
    """
    proc = _git(repo_root, "rev-parse", "--verify", f"{base}^{{commit}}")
    if proc.returncode != 0:
        sys.exit(
            f"ERROR: --base {base!r} does not resolve to a commit in {repo_root} "
            f"({proc.stderr.decode('utf-8', 'replace').strip()})"
        )


def _changed_paths(repo_root: Path, base: str, rel_dir: str) -> List[str]:
    """Every path under rel_dir that differs between `base` and the current
    working tree, tracked or not.

    Two git calls, unioned, because neither alone covers both shapes of
    "changed" this script needs to see:
      * `git diff --name-only <base> -- rel_dir` sees every TRACKED
        difference between base and the working tree (staged, unstaged, or
        already committed on top of base — whichever combination is present).
      * `git ls-files --others --exclude-standard -- rel_dir` sees a file
        that exists on disk but was never `git add`ed at all, which the diff
        above cannot report regardless of what it is compared against — git
        diff simply does not look at untracked files.
    Raises _CheckError (rather than returning a partial/empty answer) on a
    git failure other than "found nothing" — a silent empty result here would
    read downstream as "this bundle did not change", which is the one wrong
    answer this function must never give quietly.
    """
    tracked = _git(repo_root, "diff", "--name-only", base, "--", rel_dir)
    if tracked.returncode != 0:
        raise _CheckError(
            f"git diff --name-only {base} -- {rel_dir} failed: "
            f"{tracked.stderr.decode('utf-8', 'replace').strip()}"
        )
    untracked = _git(repo_root, "ls-files", "--others", "--exclude-standard", "--", rel_dir)
    if untracked.returncode != 0:
        raise _CheckError(
            f"git ls-files --others -- {rel_dir} failed: "
            f"{untracked.stderr.decode('utf-8', 'replace').strip()}"
        )
    paths = set(tracked.stdout.decode("utf-8").splitlines())
    paths |= set(untracked.stdout.decode("utf-8").splitlines())
    paths.discard("")
    return sorted(paths)


def _version_from_manifest_json(raw_bytes: bytes, location: str) -> Tuple[str, Tuple[int, int, int]]:
    """Parse `version` out of a manifest's raw bytes, or raise _CheckError.

    `location` is a human-readable description of where these bytes came
    from (a repo-relative path, or that path plus a ref), used only to make
    the resulting problem message point somewhere specific.
    """
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _CheckError(f"{location}: not valid JSON ({exc})")
    raw_version = data.get("version") if isinstance(data, dict) else None
    if raw_version is None:
        raise _CheckError(f'{location}: has no "version" field')
    parsed = parse_semver(raw_version)
    if parsed is None:
        raise _CheckError(f"{location}: version {raw_version!r} is not X.Y.Z semver")
    return raw_version, parsed


def _manifest_version_at_ref(
    repo_root: Path, ref: str, rel_path: str
) -> Tuple[Optional[str], Optional[Tuple[int, int, int]]]:
    """Read `version` from a manifest as it existed at `ref`.

    Returns (None, None) when the manifest simply did not exist at `ref` — a
    brand-new manifest imposes no bump requirement (ADR 0009's "newly added
    bundle" case), so this is a normal outcome, not an error. Raises
    _CheckError when the manifest DID exist at `ref` but could not be read as
    a valid version: that is a real problem the caller must report.
    """
    proc = _git(repo_root, "show", f"{ref}:{rel_path}")
    if proc.returncode != 0:
        return (None, None)  # did not exist at ref
    return _version_from_manifest_json(proc.stdout, f"{rel_path} at {ref}")


def _manifest_version_on_disk(
    repo_root: Path, rel_path: str
) -> Tuple[Optional[str], Optional[Tuple[int, int, int]]]:
    """Same contract as _manifest_version_at_ref, but reads the working tree."""
    path = repo_root / rel_path
    if not path.is_file():
        return (None, None)
    return _version_from_manifest_json(path.read_bytes(), rel_path)


def check_bundle(
    repo_root: Path, base: str, bundle: Path, problems: List[str], notices: List[str]
) -> None:
    """Check one bundle directory; append to `problems` or `notices`, never both.

    Mirrors check_agent_plugins.py's check_bundle() shape (problems is an
    accumulator the caller prints later, not raised), so a failure in one
    bundle never stops the run from checking the rest — see the module
    docstring: every failure is reported, not just the first.
    """
    bundle_rel = bundle.relative_to(repo_root).as_posix()
    try:
        changed = _changed_paths(repo_root, base, bundle_rel)
    except _CheckError as exc:
        problems.append(str(exc))
        return

    if not changed:
        notices.append(f"{bundle.name}: unchanged since {base} — no bump required")
        return

    root_rel = f"{bundle_rel}/{ROOT_MANIFEST}"
    claude_rel = f"{bundle_rel}/{CLAUDE_MANIFEST.as_posix()}"

    any_existed_at_base = False
    bundle_problems: List[str] = []
    for rel_path in (root_rel, claude_rel):
        try:
            base_raw, base_parsed = _manifest_version_at_ref(repo_root, base, rel_path)
        except _CheckError as exc:
            bundle_problems.append(f"{bundle.name}: {exc}")
            continue
        if base_parsed is None:
            # This manifest itself did not exist at base — nothing to bump
            # above. Handled per-manifest (not just per-bundle) so a bundle
            # that existed at base only partially — one manifest present, one
            # not — still gets the requirement enforced on the manifest that
            # did exist.
            continue
        any_existed_at_base = True

        try:
            cur_raw, cur_parsed = _manifest_version_on_disk(repo_root, rel_path)
        except _CheckError as exc:
            bundle_problems.append(f"{bundle.name}: {exc}")
            continue
        if cur_parsed is None:
            bundle_problems.append(
                f"{bundle.name}: {rel_path} existed at {base} (version {base_raw!r}) "
                "but is missing from the working tree now"
            )
            continue

        if not (cur_parsed > base_parsed):
            bundle_problems.append(
                f"{bundle.name}: {rel_path} version did not increase for changed "
                f"content — {base} has {base_raw!r}, working tree has {cur_raw!r}. "
                "Bump it strictly above its base value (ADR 0009)."
            )

    if not any_existed_at_base:
        notices.append(f"{bundle.name}: newly added since {base} — no bump required")
        return

    if bundle_problems:
        problems.extend(bundle_problems)
    else:
        notices.append(f"{bundle.name}: content changed since {base} and version bumped — OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", required=True, metavar="REF",
        help="Commit/ref to diff against — the PR base sha in CI.",
    )
    parser.add_argument(
        "--repo", metavar="PATH", default=None,
        help="Repo root to check (default: this checkout). Lets tests point "
             "the gate at a throwaway fixture repo instead of this one.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve() if args.repo else REPO_ROOT
    _validate_base(repo_root, args.base)

    plugins_dir = repo_root / "plugins"
    bundles = discover_bundles(plugins_dir)
    if not bundles:
        print(f"FAIL: no plugin bundles found under {plugins_dir}")
        return 1

    problems: List[str] = []
    notices: List[str] = []
    for bundle in bundles:
        check_bundle(repo_root, args.base, bundle, problems, notices)

    # Printed before the verdict, and on the failure path too — a passing
    # bundle's status is only useful if it is visible in the run someone
    # actually reads, same reasoning as check_agent_plugins.py's NOTE lines.
    for notice in notices:
        print(f"NOTE: {notice}")

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        print(f"\n{len(problems)} problem(s) found.")
        return 1

    print(
        f"OK: {len(bundles)} bundle(s) checked against {args.base} — every "
        "bundle whose content changed also raised its version (ADR 0009)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
