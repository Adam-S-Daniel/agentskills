#!/usr/bin/env python3
"""generate_skills_lock.py — build and verify `skills.lock`.

`skills.lock` is the pinned, integrity-checked manifest that
`.claude/hooks/skills-bootstrap.sh` installs from on an ephemeral Claude
surface (cloud session, CI runner, container). It answers three questions the
hook must not have to guess at: *which registry*, *at which immutable commit*,
and *is the content it just installed byte-for-byte the content that was
reviewed*.

Why JSON and not YAML
---------------------
The hook is bash. Bash has no YAML or JSON parser, and this repo forbids
hand-rolled parsers for structured formats, so the hook shells out to
`python3` to read the lock. JSON keeps that dependency to a *stdlib* module
(`json`); YAML would require PyYAML to be installed on every surface the hook
runs on, which is exactly the assumption an ephemeral container breaks.

Why the content is read from git, not from the working tree
-----------------------------------------------------------
The lock pins `ref` — an immutable commit — and the hook fetches *that commit*
from the registry and verifies what it installed against the digests recorded
here. So the digests have to describe the content **at that commit**. Reading a
mutable working tree instead would let a lock claim digests for bytes that were
never published at `ref`; every subsequent hook run would then report a digest
mismatch, and the integrity check would be reporting the generator's staleness
rather than a real tampering signal. Content is therefore materialised from
`git archive <ref>` into a scratch directory and hashed there.

A consequence worth knowing: editing a skill and regenerating the lock before
committing changes nothing, because the edit is not in `ref` yet. Regenerate
(or re-pin with `--ref`) *after* the content is published.

What `--check` asserts
----------------------
That the lock on disk is a faithful description of the registry **at the ref it
pins** — not that it pins the newest commit. Values not passed as flags are
inherited from the lock, so the check survives a dirty tree and a CI merge
commit. That inheritance is not a convenience: committing anything moves HEAD,
so a `--check` that silently re-pinned to HEAD could never be green on a
committed lock, and in CI HEAD is a merge commit that is never the pinned SHA.
To assert *which* commit is pinned, pass `--ref` explicitly.

Re-pinning is therefore its own deliberate step. After skills change and the
change is published, regenerate — otherwise the hook keeps faithfully
delivering the previous commit's content.

The digest
----------
Per skill, sha256 over a manifest built from the skill directory: for every
file under it, sorted by POSIX relative path,

    "<relpath>\\0<sha256 of the file's raw bytes>\\n"

concatenated, then sha256 of that whole string (UTF-8).

Raw bytes, with **no** line-ending normalisation: the registry is LF-only and a
byte-exact check is the entire point — normalising would hide precisely the
kind of silent rewrite the lock exists to catch. Paths are included in the
manifest (not just contents) so that renaming or moving a file changes the
digest, and the sort is over the POSIX relative path string so the result never
depends on filesystem iteration order.

`--digest DIR` exposes that same function on any directory. The bootstrap hook
calls it to re-hash what it installed. That is deliberate: the hook must not
carry its own reimplementation of this algorithm in bash, because an
independently written second copy is exactly how the two silently drift apart
and the check starts reporting a number nobody can explain.

Usage:
  python3 scripts/generate_skills_lock.py [--registry OWNER/REPO] [--ref REF]
                                          [--bundles a,b] [-o PATH]
  python3 scripts/generate_skills_lock.py --check [same flags]
  python3 scripts/generate_skills_lock.py --digest DIR
"""

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = "Adam-S-Daniel/agentskills"
# The cloud-safe bundle. `adam-local` and `fastmail` are machine-bound and
# opt-in, so they have no business in an ephemeral-surface bootstrap.
DEFAULT_BUNDLES = ("adam",)
DEFAULT_LOCK = REPO_ROOT / "skills.lock"
# Field order of the emitted document. Explicit so a regenerated lock is a
# stable, reviewable diff rather than a reshuffle.
FIELD_ORDER = ("registry", "ref", "bundles", "skills", "generated_from")


class GeneratorError(Exception):
    """A user-facing failure: reported as a message, never a traceback."""


def digest_skill_dir(path: Path) -> str:
    """Return the sha256 digest of a skill directory. See module docstring."""
    root = Path(path).resolve()
    if not root.is_dir():
        raise GeneratorError(f"not a directory: {path}")
    entries = []
    for candidate in root.rglob("*"):
        if not candidate.is_file():
            continue  # directories carry no bytes; broken symlinks carry none either
        entries.append((candidate.relative_to(root).as_posix(), candidate))
    manifest = "".join(
        f"{relpath}\0{hashlib.sha256(file_path.read_bytes()).hexdigest()}\n"
        for relpath, file_path in sorted(entries, key=lambda entry: entry[0])
    )
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def resolve_ref(repo: Path, ref: str) -> str:
    """Resolve a tree-ish to its full 40-hex commit SHA."""
    proc = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if proc.returncode != 0:
        raise GeneratorError(
            f"cannot resolve ref '{ref}' in {repo}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}\n"
            "  (a shallow clone may simply not contain that commit — "
            "fetch it, or pass --ref explicitly)"
        )
    return proc.stdout.decode("ascii").strip()


def materialize(repo: Path, ref: str, dest: Path) -> None:
    """Extract the tree at `ref` into `dest` (pure stdlib — no `tar` binary)."""
    proc = _git(repo, "archive", "--format=tar", ref)
    if proc.returncode != 0:
        raise GeneratorError(
            f"git archive {ref} failed: {proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
        try:
            archive.extractall(dest, filter="data")
        except TypeError:  # Python < 3.11.4 has no extraction filters
            archive.extractall(dest)


def collect_skills(tree_root: Path, bundles: Iterable[str]) -> Dict[str, str]:
    """Map '<bundle>/<skill>' -> digest for every skill in `bundles`.

    Derived entirely from the tree: no skill name or count is hardcoded, and a
    bundle with no skills simply contributes nothing.
    """
    skills: Dict[str, str] = {}
    for bundle in bundles:
        skills_root = tree_root / "plugins" / bundle / "skills"
        if not skills_root.is_dir():
            continue
        for skill_md in sorted(skills_root.glob("*/SKILL.md")):
            skill_dir = skill_md.parent
            skills[f"{bundle}/{skill_dir.name}"] = digest_skill_dir(skill_dir)
    return dict(sorted(skills.items()))


def build_lock(repo: Path, registry: str, ref: str, bundles: Sequence[str]) -> dict:
    resolved = resolve_ref(repo, ref)
    with tempfile.TemporaryDirectory(prefix="skills-lock-") as scratch:
        tree_root = Path(scratch)
        materialize(repo, ref, tree_root)
        skills = collect_skills(tree_root, bundles)
    document = {
        "registry": registry,
        "ref": ref,
        "bundles": list(bundles),
        "skills": skills,
        "generated_from": resolved,
    }
    return {key: document[key] for key in FIELD_ORDER}


def serialize(document: dict) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def load_lock(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GeneratorError(f"{path} does not exist; generate it first") from None
    except json.JSONDecodeError as exc:
        raise GeneratorError(f"{path} is not valid JSON: {exc}") from None


def _parse_bundles(raw: Optional[str]) -> Optional[List[str]]:
    if raw is None:
        return None
    bundles = [item.strip() for item in raw.split(",") if item.strip()]
    if not bundles:
        raise GeneratorError("--bundles was given but names no bundle")
    return bundles


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build or verify skills.lock.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--registry", metavar="OWNER/REPO", default=None,
                        help=f"registry the lock pins (default: {DEFAULT_REGISTRY})")
    parser.add_argument("--ref", metavar="REF", default=None,
                        help="commit SHA, tag or branch to pin (default: HEAD of this repo)")
    parser.add_argument("--bundles", metavar="a,b", default=None,
                        help=f"comma-separated bundles to lock (default: {','.join(DEFAULT_BUNDLES)})")
    parser.add_argument("-o", "--output", metavar="PATH", default=None,
                        help=f"where to write the lock (default: {DEFAULT_LOCK})")
    parser.add_argument("--check", action="store_true",
                        help="verify the lock on disk is a faithful description of the "
                             "registry at the ref it pins, and exit 1 if not. Values not "
                             "given as flags are inherited from the lock, so this stays "
                             "meaningful on a dirty tree and under a CI merge commit; "
                             "pass --ref explicitly to also assert *which* commit is pinned.")
    parser.add_argument("--digest", metavar="DIR", default=None,
                        help="print the sha256 digest of one skill directory and exit "
                             "(the bootstrap hook's integrity check calls this)")
    parser.add_argument("--repo", metavar="PATH", default=None,
                        help=f"git repo to read content from (default: {REPO_ROOT})")
    args = parser.parse_args(argv)

    if args.digest is not None:
        print(digest_skill_dir(Path(args.digest)))
        return 0

    repo = Path(args.repo) if args.repo else REPO_ROOT
    output = Path(args.output) if args.output else DEFAULT_LOCK

    existing = load_lock(output) if args.check else {}
    registry = args.registry or existing.get("registry") or DEFAULT_REGISTRY
    bundles = (
        _parse_bundles(args.bundles)
        or (existing.get("bundles") if isinstance(existing.get("bundles"), list) else None)
        or list(DEFAULT_BUNDLES)
    )
    ref = args.ref or existing.get("ref") or resolve_ref(repo, "HEAD")

    document = build_lock(repo, registry, ref, bundles)
    rendered = serialize(document)

    if args.check:
        on_disk = output.read_text(encoding="utf-8")
        if on_disk == rendered:
            print(f"OK: {output} is current ({len(document['skills'])} skills at {ref}).")
            return 0
        print(f"FAILED: {output} is stale — regenerate it with:")
        print(f"  python3 {Path(__file__).name} --ref {ref}")
        for line in _differences(json.loads(on_disk) if on_disk.strip() else {}, document):
            print(f"  - {line}")
        return 1

    try:
        output.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise GeneratorError(f"cannot write {output}: {exc}") from None
    print(f"Wrote {output}: {len(document['skills'])} skills from {registry}@{ref}.")
    return 0


def _differences(on_disk: dict, expected: dict) -> List[str]:
    """Human-readable summary of why --check failed."""
    out: List[str] = []
    for key in FIELD_ORDER:
        if key == "skills":
            continue
        if on_disk.get(key) != expected.get(key):
            out.append(f"{key}: {on_disk.get(key)!r} on disk, {expected.get(key)!r} expected")
    disk_skills = on_disk.get("skills") or {}
    expected_skills = expected["skills"]
    for name in sorted(set(expected_skills) - set(disk_skills)):
        out.append(f"skills: '{name}' missing from the lock")
    for name in sorted(set(disk_skills) - set(expected_skills)):
        out.append(f"skills: '{name}' in the lock but not in the tree")
    for name in sorted(set(disk_skills) & set(expected_skills)):
        if disk_skills[name] != expected_skills[name]:
            out.append(f"skills: '{name}' digest changed")
    return out


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GeneratorError as error:
        sys.exit(f"ERROR: {error}")
