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

Federated sources: more than one registry in one lock
-----------------------------------------------------
`registry` / `ref` / `bundles` describe the PRIMARY source, exactly as they
always have. An optional `sources` array adds more, each with its own registry,
ref, bundles and layout:

    "sources": [
      {"registry": "Adam-S-Daniel/cms-platform",
       "ref": "<40-hex sha>",
       "bundles": ["cms-platform"],
       "layout": "skills"}
    ]

That exists so cms-platform can keep its skills in ITS repo, on its own cadence
and review path, instead of rsyncing a vendored mirror into every consumer —
one registry, several owners. A consumer wanting both this repo's `adam` bundle
and cms-platform's gets them from one lock, in one session.

`layout` says where a bundle's skills sit inside its own repo. It defaults to
`plugins/{bundle}/skills` (this repo's shape); cms-platform's is `skills`, at
its repo root. `{bundle}` — the only placeholder there is — is substituted per
bundle, so one layout covers a multi-bundle source. The primary source is just
a source with the default layout and goes through the identical code path: a
second, parallel implementation "for the primary" is exactly how the two drift
until one of them is wrong.

`sources` is OMITTED from a lock that has none, never written as an empty
array. A single-source lock must serialize byte-identically to one written
before any of this existed, or every consumer's committed lock churns to say
nothing.

Two uniqueness rules are enforced here, both hard errors:

  * bundle names, across all sources — a bundle claimed twice has no defined
    layout or registry;
  * skill directory BASENAMES, across all bundles — the bootstrap hook installs
    into a FLAT `~/.claude/skills/<name>/`, so two bundles shipping a `deploy`
    would have one silently overwrite the other, and the loser is decided by
    dictionary order rather than by anyone.

An extra source's `ref` is recorded RESOLVED to its 40-hex commit SHA (the
primary's `ref` stays verbatim, with `generated_from` recording its
resolution). There is no per-source `generated_from` to hold the resolution, so
storing a branch name there would leave that half of the lock unpinned — which
is the one thing the lock exists to prevent.

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

What `--check-current` asserts, and why it is a SEPARATE flag
-------------------------------------------------------------
`--check` is structurally blind to the failure this repo actually has: because
it asks "is the lock faithful to the ref it pins", a lock that pins a commit
from before a skill was added is perfectly faithful and perfectly green. The
new skill is simply never delivered to any ephemeral surface, and nothing says
so. A silent no-op.

`--check-current` closes that: it compares the bundle content **at the pinned
ref** against the **working tree** and fails, listing added / removed / changed
skills, when they differ. The two flags assert different things and both are
wanted — together they mean "the lock is honest AND the lock is current".

It reads the working tree verbatim, which is the point (a brand-new skill
directory is untracked, and must still count). What git itself ignores is
excluded from that read — `ignored_paths` asks `git ls-files --others
--ignored --exclude-standard`, not a fixed list maintained here — so leaving a
`__pycache__` behind after a local test run no longer reds the check. Anything
untracked and NOT ignored still counts, which is the brand-new-skill case the
first sentence names.

Order of operations this creates: change bundle content -> commit -> regenerate
the lock, pinning THAT commit -> commit the lock. The lock commit only touches
`skills.lock`, so the bundle content at the pinned commit stays current.

A re-pin is an assertion — "the tree at this ref is what the lock now
describes" — and it holds only while nothing else touches a locked skill
between the content commit and the re-pin. A base that has already moved is
worth checking before a merge, not after one goes wrong.

Two measured outcomes fall out of that, and only one merges silently. When
both branches carry a proper pair (content plus its own re-pin), `skills.lock`
conflicts on every merge, because both sides rewrite the same `ref` and
`generated_from` lines, and a human is forced to look — true whether the two
pairs touch the same skill or different ones. A clean merge instead needs
`main` to already be sitting between a content commit and its re-pin — already
red on `--check-current` — before the merge lands; merging a different locked
skill's pair into that state merges cleanly, and the result is red, naming the
newly pinned ref. The merge itself never manufactures a red check out of two
green branches; it carries an already-red `main` past the point where someone
was looking, and relabels which ref the complaint names.

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

`--digest DIR` exposes that same function on any directory, for anyone who wants
to check one by hand.

The bootstrap hook does NOT call it. It used to, and the lookup that found this
file — beside the hook, then in the project, then in any FETCHED registry — is
what let a federated source supply the code that decides whether a digest
matches: with the primary unreachable, an attacker's `generate_skills_lock.py`
ran at session start and echoed each skill's expected sha256 straight back out
of the lock. A hook that runs with no approval prompt must not execute code it
just downloaded, so `.claude/hooks/skills-bootstrap.sh` now carries its own
`digest_dir`, inline.

That is the second copy this docstring's older wording warned about, and the
warning was right — an independently written copy is how two hashers silently
drift. It is therefore not independent: the hook's copy mirrors
`digest_skill_dir` below line for line, and
`test_the_hooks_inline_digest_matches_the_generators` asserts the two agree on a
non-trivial directory (nested dirs, an empty file, CRLF, a UTF-8 filename, no
trailing newline). Change either copy and change the other; that test is what
says so.

Usage:
  python3 scripts/generate_skills_lock.py [--registry OWNER/REPO] [--ref REF]
                                          [--bundles a,b] [-o PATH]
                                          [--source 'b1,b2=OWNER/REPO@REF[:LAYOUT]']
                                          [--source-repo 'KEY=PATH']
  python3 scripts/generate_skills_lock.py --check [same flags]
  python3 scripts/generate_skills_lock.py --check-current [same flags]
  python3 scripts/generate_skills_lock.py --digest DIR

`--source` is repeatable and adds one federated source; `--source-repo` says
where that source's git checkout lives on this machine, defaulting to the
sibling `../<repo-name>` (the convention scripts/skills_registries.yml already
uses), and is keyed by the source's registry, its comma-joined bundle list, or
any one of its bundle names. `--check` / `--check-current` inherit `sources`
from the lock the same way they inherit `registry` / `ref` / `bundles`.
"""

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = "Adam-S-Daniel/agentskills"
# The cloud-safe bundle. `adam-local` and `fastmail` are machine-bound and
# opt-in, so they have no business in an ephemeral-surface bootstrap.
DEFAULT_BUNDLES = ("adam",)
DEFAULT_LOCK = REPO_ROOT / "skills.lock"
# This repo's own shape, and therefore the layout a source that does not say
# otherwise is assumed to have.
DEFAULT_LAYOUT = "plugins/{bundle}/skills"
# Field order of the emitted document. Explicit so a regenerated lock is a
# stable, reviewable diff rather than a reshuffle. `sources` is listed here for
# its POSITION only — it is dropped from a lock that has none (see the module
# docstring: an empty array would churn every single-source lock).
FIELD_ORDER = ("registry", "ref", "bundles", "sources", "skills", "generated_from")
SOURCE_FIELDS = ("registry", "ref", "bundles", "layout")
# The trust-boundary patterns. The bootstrap hook's lock reader re-states each
# of these VERBATIM (NAME / REF / URL / CONTROL in skills-bootstrap.sh), and
# test_the_two_validators_accept_the_same_set proves neither copy drifts — a
# lock this generator writes, the hook must accept, and vice-versa. They are
# deliberately narrow because these strings become git remotes and filesystem
# paths.
#
#   * _NAME_RE requires a LEADING ALPHANUMERIC, so a bundle or skill name can
#     never be '.' or '..'. A key like 'adam/..' made the hook's $name '..' and
#     turned its install `cp` into a write to $HOME/.claude.
#   * _URL_RE is a real URL charset, not merely 'starts with https://'.
#   * _CONTROL_RE is DEFENCE IN DEPTH, not the fix for anything. The framing
#     forgery it was written for — a TAB or NEWLINE in a registry/ref/layout
#     creating records or shifting columns on the bash side — is closed by the
#     hook's NUL framing, which no field's CONTENT can forge. Every charset it
#     guards here is already ASCII-only, so removing it today still rejects TAB,
#     LF, NBSP and SPACE. It only becomes load-bearing if one of those charsets
#     is widened — widen one and you are relying on this check; do not assume it
#     is covering you before then.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_REF_RE = re.compile(r"[A-Za-z0-9._/+:@-]+")
_URL_RE = re.compile(r"(?:https|file)://[A-Za-z0-9._~:/?#@%!$&()*+,;=\[\]-]+")
_CONTROL_RE = re.compile(r"[\s\x00-\x1f\x7f]")
# Shared with the hook the same way, and by the same test: the number is stated
# once per file, byte-identically, and drift between the two is an alarm rather
# than a discovery. The hook's reason for the cap is that each source is fetched
# at SESSION START, so the list length is a stall multiplier. The generator's
# reason is that the hook rejects a lock exceeding it WHOLESALE — a nine-source
# lock generated at exit 0, passed --check, and then installed nothing at all,
# which is the same "don't write a lock the hook refuses" rule as
# validate_registry / validate_ref / the skill-directory name check.
MAX_SOURCES = 8


class GeneratorError(Exception):
    """A user-facing failure: reported as a message, never a traceback."""


def digest_skill_dir(path: Path, skip: frozenset = frozenset()) -> str:
    """Return the sha256 digest of a skill directory. See module docstring.

    MIRRORED, line for line, by `digest_dir` in
    `.claude/hooks/skills-bootstrap.sh` — the hook hashes what it installed
    itself rather than executing a copy of this file it just fetched.
    `test_the_hooks_inline_digest_matches_the_generators` binds the two; edit
    one and you must edit the other.

    `skip` excludes specific, already-resolved paths from the manifest —
    `--check-current` passes it the working tree's `ignored_paths` so a
    gitignored build artefact does not enter the digest at all. The hook has
    no counterpart to this parameter, and that is not a gap in the mirror: it
    hashes what it just installed, under `~/.claude/skills`, where there is no
    git repository and therefore no ignore rules to ask about — the filtering
    is a caller-side concern that only exists on the git-backed side. The
    ALGORITHM the two share — walk, sort, concatenate, hash — is untouched by
    any of this; `skip` only prunes what is handed to it.
    """
    root = Path(path).resolve()
    if not root.is_dir():
        raise GeneratorError(f"not a directory: {path}")
    entries = []
    for candidate in root.rglob("*"):
        if not candidate.is_file():
            continue  # directories carry no bytes; broken symlinks carry none either
        if candidate in skip:
            continue
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


def ignored_paths(repo: Path) -> frozenset:
    """Every path under `repo` that git ignores, as absolute paths.

    `--others` restricts the query to untracked files, so a TRACKED file can
    never come back from this — gitignore does not apply to tracked content,
    and `git archive` ships it regardless of what a local `.gitignore` says.
    `--ignored` then narrows untracked down to the ones git actually ignores,
    which is the distinction that matters: an untracked-but-NOT-ignored file
    is exactly what a brand-new skill directory looks like, and it must keep
    counting as a difference. A fixed list — `{__pycache__, .pytest_cache,
    *.pyc}` — was the alternative; asking git instead is what keeps this
    honest when the ignore rules change, rather than drifting the moment
    somebody adds a new build tool with its own cache directory.

    What this can never do is HIDE a real difference, which is the only
    direction that would matter: everything present at the pinned ref is
    tracked there, and a tracked path is exactly what `--others` excludes from
    this set. Measured on the awkward case — a file committed at the pinned ref
    and later `git rm --cached`ed while still matching an ignore rule — the
    pinned side still carries it, the working side no longer does, and
    `--check-current` reports the skill as changed.

    `--exclude-standard` also honours `core.excludesFile` and
    `.git/info/exclude`, so a developer whose `__pycache__` rule lives in their
    global config is covered too — measured, not assumed. The cost is that a
    file someone has globally ignored stops counting here; the reason that is
    acceptable is the paragraph above. Such a file cannot be committed without
    `-f`, so it can never reach a pinned ref for this check to be wrong about.

    `-z` NUL-terminates each record and emits raw, unquoted bytes, so a path
    that is not valid UTF-8 — the suite's own fixtures carry one — still comes
    back intact; `os.fsdecode` is the matching decode on this side.
    """
    proc = _git(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    if proc.returncode != 0:
        raise GeneratorError(
            f"git ls-files --ignored failed in {repo}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    root = Path(repo).resolve()
    return frozenset(
        root / os.fsdecode(name)
        for name in proc.stdout.split(b"\0")
        if name
    )


def _reject_control(value: str, where: str) -> None:
    """Reject any whitespace/control byte — the same guard the hook applies.

    A TAB or NEWLINE surviving into a registry, ref or layout is precisely how a
    single field forged extra records or shifted columns on the bash side. This
    is one of the two independent guards (the other is the hook's NUL framing);
    the generator refuses to WRITE such a value so the failure lands at
    authoring time, not at somebody else's session start.
    """
    if _CONTROL_RE.search(value):
        raise GeneratorError(f"{where}: must not contain whitespace or control characters")


def validate_registry(registry: str, where: str) -> str:
    """Reject a registry the bootstrap hook would refuse at session start.

    The registry becomes a git REMOTE, and git remotes are not inert strings:
    `ext::sh -c ...` is a remote helper that runs a command. The hook validates
    this before it fetches, but a lock the hook will reject has no business
    being written either — the failure belongs at authoring time, where a human
    is watching, not at somebody else's session start. Accepts OWNER/REPO or an
    explicit https:// / file:// URL matching a real URL charset (not merely
    'starts with https://').
    """
    if not isinstance(registry, str) or not registry:
        raise GeneratorError(
            f"{where}: must be OWNER/REPO or an https:// / file:// URL, got {registry!r}"
        )
    _reject_control(registry, where)
    if "://" in registry:
        if not _URL_RE.fullmatch(registry):
            raise GeneratorError(f"{where}: must be an https:// or file:// URL, got {registry!r}")
        return registry
    if re.fullmatch(_NAME_RE.pattern + "/" + _NAME_RE.pattern, registry):
        return registry
    raise GeneratorError(
        f"{where}: must be OWNER/REPO or an https:// / file:// URL, got {registry!r}"
    )


def validate_ref(ref: str, where: str) -> str:
    """Reject a ref the bootstrap hook's `clean_ref` would refuse.

    The hook accepts only `[A-Za-z0-9._/+:@-]+`, so a lock written with e.g.
    `--ref 'HEAD~1'` — which the generator would otherwise resolve happily and
    record verbatim — is one the hook then rejects WHOLESALE, installing zero
    skills and naming no field. Same principle as validate_registry: don't write
    a lock the hook will reject.
    """
    if not isinstance(ref, str) or not ref:
        raise GeneratorError(f"{where}: 'ref' must be a non-empty string")
    _reject_control(ref, where)
    if not _REF_RE.fullmatch(ref):
        raise GeneratorError(f"{where}: {ref!r} is not a plausible git ref")
    return ref


def validate_layout(layout: str, where: str) -> str:
    """Reject a layout that would read outside the tree it is joined onto.

    A layout is the one source field that becomes a filesystem path, under a
    tree the hook has just fetched from somewhere else. An absolute path or a
    `..` segment escapes that tree entirely. `{bundle}` is the only placeholder
    that means anything: an unknown one survives substitution intact, quietly
    naming a directory that cannot exist, and the whole bundle then reports as
    "not installed" with nothing saying why.
    """
    if not isinstance(layout, str) or not layout:
        raise GeneratorError(f"{where}: must be a non-empty string")
    _reject_control(layout, where)
    literal = layout.replace("{bundle}", "")
    if "{" in literal or "}" in literal:
        raise GeneratorError(f"{where}: '{{bundle}}' is the only placeholder, got {layout!r}")
    for segment in layout.split("/"):
        if segment in ("", ".", "..") or not re.fullmatch(r"[A-Za-z0-9._{}-]+", segment):
            raise GeneratorError(
                f"{where}: must be a relative path with no '..' segment, got {layout!r}"
            )
    return layout


def layout_dir(layout: str, bundle: str) -> str:
    """The directory, relative to a source's repo root, holding `bundle`'s skills."""
    return layout.replace("{bundle}", bundle)


def collect_skills(
    tree_root: Path, bundles: Iterable[str], layout: str = DEFAULT_LAYOUT,
    skip: frozenset = frozenset()
) -> Dict[str, str]:
    """Map '<bundle>/<skill>' -> digest for every skill in `bundles`.

    Derived entirely from the tree: no skill name or count is hardcoded, and a
    bundle with no skills simply contributes nothing. `layout` is what makes
    this work for a federated source whose skills live somewhere other than
    this repo's `plugins/<bundle>/skills`.

    A skill's key is derived from a DIRECTORY NAME, which is the one lock field
    no human ever types — so it is the one that silently produced a lock the
    hook rejects WHOLESALE. `plugins/adam/skills/_template/SKILL.md` generated
    at exit 0 ("10 skills") and passed every repo gate, and then the hook, whose
    skill-name charset is `[A-Za-z0-9][A-Za-z0-9._-]*`, refused the entire lock
    and installed 0 of 10 — pointing the reader at the command that had just
    printed OK. A leading '.' or '-', an embedded space and a non-ASCII name do
    the same. Same principle as validate_registry / validate_ref: a lock the
    hook will reject has no business being written, and the failure belongs at
    authoring time where a human is watching.
    """
    skills: Dict[str, str] = {}
    for bundle in bundles:
        skills_root = tree_root / layout_dir(layout, bundle)
        if not skills_root.is_dir():
            continue
        for skill_md in sorted(skills_root.glob("*/SKILL.md")):
            skill_dir = skill_md.parent
            if not _NAME_RE.fullmatch(skill_dir.name):
                # Named relative to the tree root: content is digested out of a
                # scratch `git archive` extraction, and a /tmp/skills-lock-XXXX
                # path is not one the reader can go and rename.
                raise GeneratorError(
                    f"{skill_dir.relative_to(tree_root).as_posix()}: skill directory "
                    f"name {skill_dir.name!r} is not a "
                    f"plausible skill name (must match {_NAME_RE.pattern}) — the "
                    "bootstrap hook rejects the WHOLE lock over one such key and "
                    "installs nothing, so it must not be written; rename the "
                    "directory, or move it out of the bundle's skills/ root"
                )
            # `synced` passes the charset above and is still unwritable: the hook
            # installs into ~/.claude/skills, where `synced/` is the claude.ai
            # ACCOUNT-SYNC channel's own directory — the only channel reaching
            # claude.ai chat, Cowork, Claude in Chrome and mobile, with no delete
            # or restore API behind it. A lock naming it aims two of the hook's
            # destructive paths at that store: the install loop `rm -rf`s the
            # destination before `cp -R` (reporting OK while the store is gone),
            # and the unreachable-source purge removes every destination the lock
            # names without installing anything.
            #
            # The hook now refuses such a lock WHOLESALE — so, exactly like the
            # charset check above, a lock it will reject has no business being
            # written, and the failure belongs at authoring time where a human is
            # watching. This half is what keeps the hook's own verdict honest: it
            # tells the operator to "regenerate it with
            # scripts/generate_skills_lock.py", which without this check would
            # hand back the identical poisoned lock.
            if skill_dir.name == "synced":
                raise GeneratorError(
                    f"{skill_dir.relative_to(tree_root).as_posix()}: a skill "
                    "directory named 'synced' cannot be locked — the bootstrap hook "
                    "installs into ~/.claude/skills, where 'synced/' is the claude.ai "
                    "account-sync store, and installing over it would destroy an "
                    "account skill store that has no restore API; the hook refuses "
                    "the WHOLE lock over such a key, so it must not be written — "
                    "rename the directory"
                )
            skills[f"{bundle}/{skill_dir.name}"] = digest_skill_dir(skill_dir, skip=skip)
    return dict(sorted(skills.items()))


def normalize_source(raw: dict, where: str) -> dict:
    """Validate one source descriptor and fill in its default layout.

    Unknown keys are an ERROR, not ignored. A `"commit"` or `"branch"` key
    added in the belief that it pins something would sit there reading as a pin
    while nothing consumed it — the lock's entire job is that no such gap
    exists between what a file appears to say and what is fetched.
    """
    if not isinstance(raw, dict):
        raise GeneratorError(f"{where}: must be an object")
    unknown = sorted(set(raw) - set(SOURCE_FIELDS))
    if unknown:
        raise GeneratorError(
            f"{where}: unknown key(s) {', '.join(repr(key) for key in unknown)}; "
            f"a source carries exactly {', '.join(SOURCE_FIELDS)}"
        )
    bundles = raw.get("bundles")
    if not isinstance(bundles, list) or not bundles:
        raise GeneratorError(f"{where}: 'bundles' must be a non-empty list")
    for bundle in bundles:
        if not isinstance(bundle, str) or not _NAME_RE.fullmatch(bundle):
            raise GeneratorError(f"{where}: {bundle!r} is not a plausible bundle name")
    return {
        "registry": validate_registry(raw.get("registry") or "", f"{where}.registry"),
        "ref": validate_ref(raw.get("ref"), f"{where}.ref"),
        "bundles": list(bundles),
        "layout": validate_layout(raw.get("layout") or DEFAULT_LAYOUT, f"{where}.layout"),
    }


def parse_source(spec: str) -> dict:
    """Parse `--source '<bundles>=<OWNER/REPO>@<ref>[:<layout>]'`.

    Split points are chosen so a `file://` registry — which carries a colon of
    its own, and is what the hermetic tests pin against — stays parseable: the
    registry ends at the LAST `@`, and because git forbids `:` in a ref name,
    the first colon after that unambiguously starts the layout.
    """
    bundles_raw, sep, rest = spec.partition("=")
    if not sep:
        raise GeneratorError(
            f"--source {spec!r}: expected '<bundles>=<OWNER/REPO>@<ref>[:<layout>]'"
        )
    if "@" not in rest:
        raise GeneratorError(f"--source {spec!r}: no '@<ref>' — a source must pin a commit")
    registry, _, ref_and_layout = rest.rpartition("@")
    ref, _, layout = ref_and_layout.partition(":")
    return normalize_source(
        {
            "registry": registry,
            "ref": ref,
            "bundles": _parse_bundles(bundles_raw, f"--source {spec!r}") or [],
            **({"layout": layout} if layout else {}),
        },
        f"--source {spec!r}",
    )


def parse_source_repo(spec: str) -> Tuple[str, str]:
    """Parse `--source-repo '<key>=<local path>'`."""
    key, sep, path = spec.partition("=")
    if not sep or not key.strip() or not path.strip():
        raise GeneratorError(f"--source-repo {spec!r}: expected '<key>=<local path>'")
    return key.strip(), path.strip()


def source_checkout(primary_repo: Path, source: dict, overrides: Dict[str, str]) -> Path:
    """Where this source's git checkout lives on this machine.

    Digests are read from git and never from a working tree (see the module
    docstring), so a federated source needs a local clone to read its `ref` out
    of. The default is the sibling `../<repo-name>` — the same convention
    scripts/skills_registries.yml uses for the conformance census, so a machine
    set up for one is already set up for the other.
    """
    for key in (source["registry"], ",".join(source["bundles"]), *source["bundles"]):
        if key in overrides:
            return Path(overrides[key]).expanduser()
    name = source["registry"].rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    return Path(primary_repo).resolve().parent / name


def plan_sources(
    repo: Path,
    registry: str,
    ref: str,
    bundles: Sequence[str],
    extras: Sequence[dict],
    overrides: Dict[str, str],
) -> List[dict]:
    """Every source, primary first, with its checkout located and `ref` resolved.

    The primary is materialised as an ordinary source with the default layout
    precisely so everything downstream — digesting, currency checking — has one
    code path rather than a special case that only the primary exercises.
    """
    # Before anything is resolved or read: the cap is a property of the SPEC,
    # so a lock over it must fail here rather than after a checkout lookup
    # sends the reader chasing a missing sibling clone. `extras` (not `extras`
    # + the primary) is what the hook counts, and the two must agree or the
    # boundary case is a lock the generator writes and the hook refuses.
    if len(extras) > MAX_SOURCES:
        raise GeneratorError(
            f"'sources' lists {len(extras)} entries; at most {MAX_SOURCES} are allowed — "
            "the bootstrap hook fetches every one of them before the session starts, and "
            "refuses the WHOLE lock over the limit, installing nothing"
        )
    primary = {
        # Labelled by FIELD, not by flag: on --check this value was inherited
        # from the lock, so blaming `--registry` would name something the
        # reader never passed. `ref` is charset-checked here too — a primary
        # `--ref 'HEAD~1'` resolves fine but writes a lock the hook rejects
        # wholesale, the same trap validate_ref closes for a source ref.
        "registry": validate_registry(registry, "registry"),
        "ref": validate_ref(ref, "ref"),
        "bundles": list(bundles),
        "layout": DEFAULT_LAYOUT,
        "path": Path(repo),
    }
    sources = [primary]
    for source in extras:
        path = source_checkout(repo, source, overrides)
        if not path.is_dir():
            raise GeneratorError(
                f"{source['registry']}: no checkout at {path} — clone it there, or point "
                f"at it with --source-repo '{','.join(source['bundles'])}=<path>'"
            )
        # Resolved, not verbatim: an extra source has no `generated_from` field
        # to record the resolution in, so a branch name left here would be the
        # one unpinned half of a lock whose whole purpose is pinning.
        sources.append({**source, "path": path, "ref": resolve_ref(path, source["ref"])})

    claimed: Dict[str, str] = {}
    for source in sources:
        for bundle in source["bundles"]:
            if bundle in claimed:
                raise GeneratorError(
                    f"bundle '{bundle}' is claimed by both {claimed[bundle]} and "
                    f"{source['registry']}; a bundle has one registry and one layout"
                )
            claimed[bundle] = source["registry"]
    return sources


def collect_from_sources(sources: Sequence[dict]) -> Dict[str, str]:
    """Digest every locked skill across every source, into one flat map."""
    skills: Dict[str, str] = {}
    for source in sources:
        with tempfile.TemporaryDirectory(prefix="skills-lock-") as scratch:
            tree_root = Path(scratch)
            materialize(source["path"], source["ref"], tree_root)
            skills.update(collect_skills(tree_root, source["bundles"], source["layout"]))
    return _reject_basename_collisions(dict(sorted(skills.items())))


def _reject_basename_collisions(skills: Dict[str, str]) -> Dict[str, str]:
    """Fail when two bundles ship a skill with the same directory name.

    The bootstrap hook installs into a FLAT `~/.claude/skills/<name>/`, so a
    basename shared by two bundles means one silently overwrites the other and
    which one survives is decided by iteration order. Federating a second
    registry is what makes this reachable at all: within one repo the bundles
    were curated together.
    """
    owner: Dict[str, str] = {}
    for key in skills:
        name = key.split("/", 1)[1]
        if name in owner:
            raise GeneratorError(
                f"'{owner[name]}' and '{key}' are both installed as "
                f"~/.claude/skills/{name}, so one would silently overwrite the other; "
                "rename one of them"
            )
        owner[name] = key
    return skills


def build_lock(
    repo: Path,
    registry: str,
    ref: str,
    bundles: Sequence[str],
    extras: Sequence[dict] = (),
    overrides: Optional[Dict[str, str]] = None,
) -> dict:
    sources = plan_sources(repo, registry, ref, bundles, extras, overrides or {})
    resolved = resolve_ref(repo, ref)
    document = {
        "registry": registry,
        "ref": ref,
        "bundles": list(bundles),
        "skills": collect_from_sources(sources),
        "generated_from": resolved,
    }
    if len(sources) > 1:
        document["sources"] = [
            {field: source[field] for field in SOURCE_FIELDS} for source in sources[1:]
        ]
    # `if key in document` is what keeps `sources` out of a single-source lock
    # entirely rather than writing it as `[]`.
    return {key: document[key] for key in FIELD_ORDER if key in document}


def check_current(
    repo: Path,
    registry: str,
    ref: str,
    bundles: Sequence[str],
    extras: Sequence[dict] = (),
    overrides: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, str], List[str]]:
    """Compare the content at each source's pinned ref with its working tree.

    Returns (working-tree skill map, human-readable differences). An empty
    difference list means every pinned commit still describes its bundles as
    they stand, i.e. the lock is current as well as faithful. Each difference
    names the ref of the source it came from, so a multi-source failure says
    which registry to re-pin.
    """
    sources = plan_sources(repo, registry, ref, bundles, extras, overrides or {})
    working: Dict[str, str] = {}
    differences: List[str] = []
    for source in sources:
        # Resolve first. `git archive` on a commit this clone does not have
        # reports a bare "not a valid object name", where resolve_ref names the
        # shallow clone that actually causes it — the exact failure a CI
        # checkout produces without `fetch-depth: 0`.
        resolve_ref(source["path"], source["ref"])
        with tempfile.TemporaryDirectory(prefix="skills-current-") as scratch:
            tree_root = Path(scratch)
            materialize(source["path"], source["ref"], tree_root)
            pinned = collect_skills(tree_root, source["bundles"], source["layout"])
        # The pinned side needs no ignore filtering of its own: it is
        # materialised by `git archive`, which emits only tracked files, so an
        # ignored path cannot be in it to begin with. Passing the working
        # tree's ignore set into that scratch extraction would be a no-op with
        # a cost, and the paths would not even line up — they are rooted under
        # this source's repo, not under the scratch `tree_root`.
        here = collect_skills(source["path"], source["bundles"], source["layout"],
                              skip=ignored_paths(source["path"]))
        working.update(here)
        at = source["ref"]
        for name in sorted(set(here) - set(pinned)):
            differences.append(f"added: '{name}' is in the working tree but not at {at}")
        for name in sorted(set(pinned) - set(here)):
            differences.append(f"removed: '{name}' is at {at} but not in the working tree")
        for name in sorted(set(pinned) & set(here)):
            if pinned[name] != here[name]:
                differences.append(f"changed: '{name}' differs from its content at {at}")
    return working, differences


def serialize(document: dict) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def load_lock(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GeneratorError(f"{path} does not exist; generate it first") from None
    except json.JSONDecodeError as exc:
        raise GeneratorError(f"{path} is not valid JSON: {exc}") from None


def _parse_bundles(raw: Optional[str], where: str = "--bundles") -> Optional[List[str]]:
    if raw is None:
        return None
    bundles = [item.strip() for item in raw.split(",") if item.strip()]
    if not bundles:
        raise GeneratorError(f"{where} was given but names no bundle")
    for bundle in bundles:
        if not _NAME_RE.fullmatch(bundle):
            raise GeneratorError(f"{where}: {bundle!r} is not a plausible bundle name")
    return bundles


def _source_spec(source: dict) -> str:
    """Render a source back as the `--source` flag that would recreate it.

    A --check that fails without printing the command to fix it is a check
    people learn to route around, and with federated sources the fix is no
    longer just `--ref`.
    """
    spec = f"{','.join(source['bundles'])}={source['registry']}@{source['ref']}"
    layout = source.get("layout", DEFAULT_LAYOUT)
    return spec if layout == DEFAULT_LAYOUT else f"{spec}:{layout}"


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
    parser.add_argument("--source", metavar="'b=OWNER/REPO@REF[:LAYOUT]'", action="append",
                        default=None,
                        help="add a FEDERATED source: bundles that live in another repo, "
                             "on its own cadence and review path, listed in the lock's "
                             "'sources' array alongside the primary --registry. Repeatable. "
                             f"LAYOUT says where that repo keeps a bundle's skills (default: "
                             f"{DEFAULT_LAYOUT}; cms-platform's is 'skills'), with '{{bundle}}' "
                             "substituted per bundle. REF is recorded resolved to a commit SHA. "
                             "Bundle names, and skill directory names, must stay unique across "
                             "all sources — the hook installs into one flat directory.")
    parser.add_argument("--source-repo", metavar="'KEY=PATH'", action="append", default=None,
                        help="where a --source's git checkout lives on this machine (digests "
                             "are read from git, never a working tree). KEY is that source's "
                             "registry, its comma-separated bundle list, or any one of its "
                             "bundle names. Default: the sibling ../<repo-name>, the same "
                             "convention scripts/skills_registries.yml uses.")
    parser.add_argument("-o", "--output", metavar="PATH", default=None,
                        help=f"where to write the lock (default: {DEFAULT_LOCK})")
    parser.add_argument("--check", action="store_true",
                        help="verify the lock on disk is a faithful description of the "
                             "registry at the ref it pins, and exit 1 if not. Values not "
                             "given as flags — including the whole 'sources' array — are "
                             "inherited from the lock, so this stays meaningful on a dirty "
                             "tree and under a CI merge commit; pass --ref explicitly to "
                             "also assert *which* commit is pinned.")
    parser.add_argument("--check-current", action="store_true",
                        help="verify the bundle content at the pinned ref still matches the "
                             "WORKING TREE, and exit 1 if not, listing the added / removed / "
                             "changed skills. Distinct from --check: a lock pinned before a "
                             "skill was added is faithful to its ref (so --check is green) "
                             "while that skill reaches no ephemeral surface at all. The "
                             "working tree is read verbatim, so an untracked skill directory "
                             "counts; what git ignores is excluded, so a local __pycache__ "
                             "does not.")
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

    verifying = args.check or args.check_current
    existing = load_lock(output) if verifying else {}
    registry = args.registry or existing.get("registry") or DEFAULT_REGISTRY
    bundles = (
        _parse_bundles(args.bundles)
        or (existing.get("bundles") if isinstance(existing.get("bundles"), list) else None)
        or list(DEFAULT_BUNDLES)
    )
    ref = args.ref or existing.get("ref") or resolve_ref(repo, "HEAD")
    # Sources are inherited from the lock for exactly the reason `ref` is: a
    # --check that silently dropped the federated half would go green while
    # verifying only some of what the lock promises.
    if args.source:
        extras = [parse_source(spec) for spec in args.source]
    elif isinstance(existing.get("sources"), list):
        extras = [
            normalize_source(raw, f"sources[{index}]")
            for index, raw in enumerate(existing["sources"])
        ]
    else:
        extras = []
    overrides = dict(parse_source_repo(spec) for spec in args.source_repo or [])

    if verifying:
        status = 0
        # Faithfulness first, currency second: "is the lock an honest
        # description of the commit it pins", then "is that commit still the
        # bundle". A lock can pass either one and fail the other.
        if args.check:
            document = build_lock(repo, registry, ref, bundles, extras, overrides)
            rendered = serialize(document)
            on_disk = output.read_text(encoding="utf-8")
            if on_disk == rendered:
                print(f"OK: {output} is current ({len(document['skills'])} skills at {ref}).")
            else:
                print(f"FAILED: {output} is stale — regenerate it with:")
                sources_flags = "".join(
                    f" --source '{_source_spec(source)}'"
                    for source in document.get("sources", [])
                )
                print(f"  python3 {Path(__file__).name} --ref {ref}{sources_flags}")
                for line in _differences(
                    json.loads(on_disk) if on_disk.strip() else {}, document
                ):
                    print(f"  - {line}")
                status = 1
        if args.check_current:
            working, differences = check_current(
                repo, registry, ref, bundles, extras, overrides
            )
            if not differences:
                print(f"OK: the working tree still matches {ref} ({len(working)} skills).")
            else:
                print(f"FAILED: the bundle has moved on since {ref}, which {output} still "
                      "pins — nothing added or changed since then reaches an ephemeral "
                      "surface. Re-pin it (after committing the content) with:")
                print("  python3 scripts/generate_skills_lock.py")
                print("  (Seeing this on a freshly merged branch usually means the re-pin was cut")
                print("  before another commit touched a locked skill: the lock is still faithful")
                print("  to the ref it pins — that ref just is not the bundle. Same fix, re-pin.)")
                for line in differences:
                    print(f"  - {line}")
                status = 1
        return status

    document = build_lock(repo, registry, ref, bundles, extras, overrides)
    try:
        output.write_text(serialize(document), encoding="utf-8")
    except OSError as exc:
        raise GeneratorError(f"cannot write {output}: {exc}") from None
    origins = ", ".join(
        [f"{registry}@{ref}"]
        + [f"{source['registry']}@{source['ref']}" for source in document.get("sources", [])]
    )
    print(f"Wrote {output}: {len(document['skills'])} skills from {origins}.")
    return 0


def _render_sources(value) -> str:
    """One-line rendering of a 'sources' array for a --check difference.

    Read defensively: this runs against the lock as found ON DISK, which may be
    hand-edited into any shape at all, and a difference report that itself
    raises is a report nobody gets to read.
    """
    if not value:
        return "none"
    if not isinstance(value, list):
        return repr(value)
    rendered = []
    for source in value:
        if isinstance(source, dict):
            rendered.append(f"{source.get('registry')}@{source.get('ref')}")
        else:
            rendered.append(repr(source))
    return ", ".join(rendered)


def _differences(on_disk: dict, expected: dict) -> List[str]:
    """Human-readable summary of why --check failed."""
    out: List[str] = []
    for key in FIELD_ORDER:
        if key == "skills":
            continue
        if on_disk.get(key) != expected.get(key):
            if key == "sources":
                out.append(
                    f"sources: {_render_sources(on_disk.get(key))} on disk, "
                    f"{_render_sources(expected.get(key))} expected"
                )
                continue
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
