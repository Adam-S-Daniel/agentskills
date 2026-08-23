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

What `--check-format` asserts, and why it is a THIRD flag
---------------------------------------------------------
Neither flag above reads a lock's STORED digest values *as values*.
`--check-current` never touches them at all — it compares two freshly digested
trees, which is exactly what `_label_digests` below promises so that labelling
could not disturb it — and `--check` reads them only inside a whole-document
comparison, where a wrong SHAPE comes out in the same words as content drift:
`digest changed`.

That combination is what stranded eight consumer locks (cms-platform,
GHA-bench, _agent-guidance, agentskills-private, claude-memory-map,
fastmail-actions, repo-settings, wsl-automation — every one of them pinning
94cdcc81). All eight store bare 64-hex where the canonical shape is
`sha256:<hex>`. The fleet bumper (_agent-guidance's
`scripts/bump-consumer-locks.sh`) would heal them, because it heals by
re-pinning and `--repin` relabels — measured on a copy of one of those locks,
8 bare in, 8 labelled out. It never will, because its anti-churn gate is
`--check-current`, which answers `OK: the working tree still matches 94cdcc81
(8 skills).` at exit 0: the bundle content genuinely has not moved, so the
bumper skips, and the shape is never fixed. Silently, and for as long as the
`adam` bundle stands still.

`--check` does fail on such a lock — exit 1, eight `digest changed` lines — and
that is deliberately not the answer to borrow. The bumper's gate comment says
why about `sources`: handing the generator a different QUESTION cannot drift
with the generator's wording, while reinterpreting another question's combined
verdict can, and does, the first time that wording changes. A distinct
question therefore gets a distinct flag.

So `--check-format` asks one thing — are this lock's stored digests in the
canonical shape — and answers it from the FILE ALONE: no registry checkout, no
network, not one git call. That is the calling convention rather than an
economy. The bumper runs it per consumer lock, before it has cloned anything.
`--repo` is accepted and never read on this path — it stays legal because the
flag composes with `--check`, which does read it, and refusing it in one
composition while requiring it in the other would make a NIT into a
mode-dependent argparse error across a repo boundary. Accepted-and-ignored is
therefore the promise, and `test_check_format_ignores_repo_entirely` is what
holds it: the verdict is byte-identical with no `--repo`, with a nonexistent
one, and with a real clone of a DIFFERENT registry.

An EMPTY `skills` map FAILS THE RUN rather than passing vacuously. A generate
over a bundle with no skills writes one legitimately (see
`test_an_empty_bundle_yields_an_empty_skills_map`), so "every digest is
well-shaped" is trivially true of it — and a gate that cannot tell "nothing to
fix" from "nothing there" is the shape every green check that was measuring
nothing has had.

It fails as `ERROR:`, not `FAILED:`, and so does a missing or non-map `skills`.
The prefix is a CONTRACT with the fleet bumper, which greps `^FAILED:` to
decide whether to re-pin a consumer's lock: `FAILED:` means "these digests are
malformed, and a re-pin is the repair", and nothing else may say it. See
`report_digest_format` for what that closes — including a nightly re-pin loop
an empty map would otherwise have had no exit from.

What `--repin` inherits, and the one field it must not
------------------------------------------------------
Re-pinning is a WRITE, but it is not a fresh generate: it advances an existing
lock rather than deciding afresh what that lock means. So `--repin` loads the
lock at `--output` and inherits its identity — `registry`, `bundles` and the
whole `sources` array — the same way `--check` does, and re-resolves only `ref`.

`ref` is the one field a re-pin must NOT inherit, because advancing it is the
entire operation; inheriting it would produce a lock identical to the one on
disk and report success for having done nothing. The new value is HEAD of the
`--repo` checkout, or `--ref`. Nothing here enforces that it is NEWER — a
re-pin onto a reviewed commit, or back onto a known-good one after a bad bump,
is a legitimate use — so "advance" describes the intent, not a check.

The inheritance is what makes the ADR's named trap unrepresentable rather than
merely avoidable. A plain generate deliberately does not inherit — a fresh lock
means exactly what its flags say — so re-pinning a FEDERATED lock by rerunning
the generate command silently drops every source the command line does not
repeat, writes a de-federated lock, and exits 0. Nothing downstream notices: the
lock is internally consistent, `--check` is green against it, and the missing
half simply stops being delivered.

`--repin` cannot express that, and the word is literal: the flags that would
override an inherited identity — `--registry`, `--bundles`, `--source` — are an
argparse ERROR alongside it. Leaving them merely unnecessary was not enough.
`--source` is the one way `--repin` offers to advance a federated source's own
pin, so an operator whose cms-platform sibling had moved would reach for exactly
`--repin --source '...'` — which took precedence over the inherited array and
REPLACED it, dropping every other registry. The trap, through the flag written
to close it. Changing a lock's identity and advancing its pin are two different
decisions and are now two different commands.

What that left with no command at all was the legitimate half of the operator's
intent: a federated source whose registry really has moved. Inheritance is
still the only thing that carries a source's ref forward, and a bare `--repin`
still brings every source through verbatim. `--repin-source
'<REGISTRY>@[<ref>]'` is now the one way to advance ONE of them — literally one:
a registry the lock happens to federate twice is REFUSED rather than fanned
out, because one spec cannot say which of the two entries it means. It merges
by registry KEY into the inherited array — an empty ref meaning that source's
HEAD, resolved before it is written — so it can never add, drop or reorder a
source. Bundles and layout are not expressible on it, because they are the
lock's identity. `--source` therefore stays an error alongside `--repin`
forever: the distinction being preserved is merge-by-key versus
replace-the-array, not "which flag names a source", and a flag that could
restate bundles and layout would be `--source` under a second name.

Inheritance also means the lock's fields are REQUIRED, not merely preferred.
A plain generate falls back to `DEFAULT_REGISTRY` / `DEFAULT_BUNDLES` because
nothing was inherited and a default is the only answer; under `--repin` that
same fallback silently re-points a consumer at a different repository, or
narrows its bundle set and drops a bundle's skills, whenever the lock's own
field is missing, empty or the wrong type — at exit 0, with `--check` green
afterwards because it repeats the identical substitution. So `--repin` reads
those fields strictly and refuses the lock by name instead. The bundle names in
particular are VALIDATED on this path: `bundles` is substituted into a
filesystem path (`layout_dir`) and into every skills key, and before `--repin`
existed the inherited value could only ever be read by `--check`, which does not
write. A `"bundles": ["../../../outside"]` digests content from outside the
`git archive` extraction entirely, and writes an attestation over bytes that
were in no commit of any registry.

`--repin` refuses to CREATE a lock. A lock that does not exist yet has no
identity to inherit, and deciding a consumer's registry and bundle set is a
deliberate act — that is a plain generate, run by whoever is making the
decision.

The `--repo` checkout must BE the registry the lock names
---------------------------------------------------------
`registry` says which repository the lock describes; `--repo` says which local
clone the new pin and every primary digest are read OUT of. A plain generate
takes both off the command line, side by side, where a mismatch is the
operator's own assertion. `--repin` reads one from the file and the other from
the filesystem, and nothing correlates them — so re-pinning a consumer's lock
with the wrong `--repo` (the multi-registry workflow this flag exists for is
exactly where the two differ) writes a lock naming a commit its registry does
not contain, at exit 0, with `--check` green because it re-derives from that
same wrong clone. The damage lands at somebody else's session start, where the
hook cannot fetch that sha from that registry and downgrades to
`skills: DEGRADED` — delivery silently stops.

The pin the lock already carries is the deterministic probe for this: a clone
that IS the registry has that commit. `--repin` checks it before writing, and
names both sides when it does not.

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
  python3 scripts/generate_skills_lock.py --repin [--ref REF] [--repo PATH]
                                          [--repin-source 'OWNER/REPO@[REF]']
                                          [--source-repo 'KEY=PATH'] [-o PATH]
  python3 scripts/generate_skills_lock.py --check [same flags]
  python3 scripts/generate_skills_lock.py --check-current [--only REGISTRY]
                                          [same flags]
  python3 scripts/generate_skills_lock.py --check-format [-o PATH]
  python3 scripts/generate_skills_lock.py --digest DIR

`--source` is repeatable and adds one federated source; `--source-repo` says
where that source's git checkout lives on this machine, defaulting to the
sibling `../<repo-name>` (the convention scripts/skills_registries.yml already
uses), and is keyed by the source's registry, its comma-joined bundle list, or
any one of its bundle names. `--check` / `--check-current` inherit `sources`
from the lock the same way they inherit `registry` / `ref` / `bundles`;
`--repin` inherits all of those EXCEPT `ref`, requires them to be present and
well-formed rather than falling back to a default, and refuses the flags that
would override them. `--check-format` inherits nothing and reads `skills`,
plus `ref` — only to name it in the remediation it prints, never to resolve
it — so it is still the one mode that never asks a registry anything.
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


# Every git this script runs has EOL translation turned off. `git archive`
# honours `core.autocrlf`, so on Windows -- where it is the default, and where
# it is set on ZENDA -- `materialize` extracted the pinned ref with every LF
# rewritten to CRLF and digested THAT. The lock is the authoritative record of
# what a skill's bytes are, and one generated here would have named a digest no
# Linux run could ever reproduce, for content that never changed. It is not
# enough for the repo to carry `.gitattributes`: the ref being archived may
# predate it, and a federated source may not carry one at all.
_GIT_VERBATIM = ("-c", "core.autocrlf=false", "-c", "core.eol=lf")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *_GIT_VERBATIM, "-C", str(repo), *args],
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


def parse_repin_source(spec: str) -> Tuple[str, str]:
    """Parse `--repin-source '<OWNER/REPO>@[<ref>]'`. Empty ref = that source's HEAD.

    Deliberately NOT `parse_source`, and the omissions are the point: bundles
    and layout are the lock's IDENTITY, so they stay inherited and are not
    expressible here at all. A flag that could restate them would be `--source`
    under another name, and `--source` alongside `--repin` is an error for a
    reason this repo has already been bitten by.

    `rpartition('@')` for the same reason `parse_source` uses it: a `file://`
    registry carries colons, not '@', so the registry ends at the LAST '@'.
    """
    registry, sep, ref = spec.rpartition("@")
    if not sep:
        raise GeneratorError(
            f"--repin-source {spec!r}: expected '<OWNER/REPO>@[<ref>]' "
            "(empty ref = advance to that source's HEAD)"
        )
    registry = validate_registry(registry, f"--repin-source {spec!r}")
    return registry, (validate_ref(ref, f"--repin-source {spec!r}") if ref else "")


def _apply_repin_sources(
    extras: Sequence[dict],
    specs: Sequence[str],
    repo: Path,
    primary_registry: str,
    overrides: Dict[str, str],
) -> List[dict]:
    """Merge --repin-source pins into the INHERITED array. Never adds, never drops.

    Never advances a source the caller did not name either: a registry this
    lock federates TWICE is refused below rather than fanned out, so one spec
    moves exactly one pin or none.

    Merge by registry KEY, never replace the array: that distinction is the
    whole difference between this flag and `--source`, which took precedence
    over the inherited `sources` and dropped every registry the command line
    did not repeat. A source this flag does not name comes back by reference,
    so it serializes byte-identically.

    ADDING a source is refused rather than allowed as a convenience — it
    changes what the lock means, which is a plain generate's decision — and so
    is naming the primary, whose pin is what `--ref` (or a bare `--repin`)
    advances.
    """
    wanted: Dict[str, str] = {}
    for spec in specs:
        reg, ref = parse_repin_source(spec)
        if reg in wanted:
            raise GeneratorError(f"--repin-source names {reg} twice; one pin per source")
        wanted[reg] = ref
    known: Dict[str, List[dict]] = {}
    for source in extras:
        known.setdefault(source["registry"], []).append(source)
    for reg in wanted:
        if reg == primary_registry:
            raise GeneratorError(
                f"--repin-source {reg} is this lock's PRIMARY registry, not a federated "
                "source; the primary's pin is what --ref (or bare --repin) advances"
            )
        if reg not in known:
            raise GeneratorError(
                f"--repin-source {reg} is not a source this lock federates "
                f"({', '.join(sorted(known)) or 'none'}); ADDING a source changes what the "
                "lock means and is a plain generate, not a re-pin"
            )
        # A registry the lock federates TWICE is representable and --check
        # green: plan_sources' uniqueness check is keyed on BUNDLE, so two
        # entries may share a registry while carrying different bundles, a
        # different layout and independent pins. Merging by registry key would
        # then advance BOTH from one spec — moving a pin nobody named, with its
        # digests rewritten to content nobody reviewed, at exit 0. This flag
        # cannot say which one is meant: bundles are the lock's identity and
        # are deliberately not expressible here.
        #
        # So it refuses, which is the answer _select_sources already gives to
        # the analogous ambiguity on the read-only path — "scoping to it has
        # two answers, so it gets none". Refusing in one place and guessing in
        # the other, in the direction that moves MORE pins, is the asymmetry
        # worth not having.
        if len(known[reg]) > 1:
            claimed = "; ".join(
                ", ".join(source["bundles"]) or "no bundles" for source in known[reg]
            )
            raise GeneratorError(
                f"--repin-source {reg}: this lock federates that registry twice, under "
                f"[{claimed}], each with its own pin — so one spec names two sources and "
                "advancing 'it' has two answers. Bundles are the lock's identity and are "
                "not expressible on this flag, so it will not pick one for you: give that "
                "registry a single 'sources' entry, or restate the whole array with a "
                "plain generate, which is where identity is decided."
            )
    merged: List[dict] = []
    for source in extras:
        ref = wanted.get(source["registry"])
        if ref is None:
            merged.append(source)            # untouched, byte-identical
            continue
        path = source_checkout(repo, source, overrides)
        if not path.is_dir():
            raise GeneratorError(
                f"{source['registry']}: no checkout at {path} — clone it there, or point "
                f"at it with --source-repo '{','.join(source['bundles'])}=<path>'"
            )
        # The same identity probe the primary's --repin does before it writes,
        # and for the same reason: the lock names a registry, the sibling
        # lookup (or --source-repo) names a directory, and NOTHING else ties
        # the two together. A fork, or a same-named repo under a different
        # owner, sits at `../cms-platform` just as happily, and `HEAD` resolves
        # in any git repo at all — so without this the wrong clone's HEAD is
        # written under the right registry's name at exit 0, and --check is
        # green afterwards because it re-derives from that same wrong clone.
        # The pin the lock ALREADY carries is the proof: a checkout that is
        # this registry has that commit.
        #
        # Every source this flag does NOT name gets the equivalent check for
        # free downstream — plan_sources resolves its inherited ref in this
        # same clone and fails there. The named source is precisely the one
        # that loses it, because its ref is replaced before plan_sources sees
        # it. This restores the guard rather than adding one.
        if _git(path, "cat-file", "-e", f"{source['ref']}^{{commit}}").returncode != 0:
            raise GeneratorError(
                f"{path} does not contain {source['ref']}, the commit this lock pins for "
                f"'{source['registry']}' — so this checkout is not that registry, and "
                "re-pinning from it would write a commit the registry does not have (the "
                "hook then cannot fetch it, and every consumer session reports DEGRADED). "
                f"Point --source-repo '{','.join(source['bundles'])}=<path>' at a clone of "
                "that registry, or fetch the pinned commit into this one."
            )
        # Resolved HERE, so the literal `HEAD` can never reach the extras array
        # and be written into a lock: an extra source has no `generated_from` to
        # record a resolution in, so an unresolved ref there is the one unpinned
        # half of a document whose whole purpose is pinning.
        merged.append({**source, "ref": resolve_ref(path, ref or "HEAD")})
    return merged


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


# The lock records digests as `sha256:<64 hex>`, not bare hex, and the prefix is
# a SECRETS-SCANNING fix rather than decoration. gitleaks' default
# `generic-api-key` rule fires on `"<keyword-bearing name>": "<high-entropy
# value>"`, and a skill basename containing any of `access api auth key
# credential creds passwd password secret token` is that keyword — `oauth` via
# `auth`, `api-keys` via both. A real sha256 clears its 3.5 entropy gate with
# near-certainty (measured: only ~0.01-0.04% of digests fall below it), so
# entropy is not a lever and truncating is both probabilistic and fatal to the
# pin. `:` is outside the rule's capture class `[\w.=-]`, so prefixing cuts the
# capture to 6 characters — under its 10-character floor — and the rule cannot
# fire at all. Measured over 3000 varying digests: 2991-2995 leaks bare, 0
# prefixed.
#
# The point of fixing it HERE rather than in each consumer's `.gitleaks.toml` is
# that a brand-new adopter's first lock commit is then green with no config at
# all — nothing to deliver, nothing to time, nothing to hand-add after a red.
# The alternative that looks equivalent is not: a lock can pass gitleaks BY LUCK
# (~0.18% of digests happen to contain a hex-spellable stopword like `dead` or
# `feed`), so a green scan today is not evidence the next content change stays
# green. See agentskills#87.
#
# Readers normalise the prefix away rather than storing it: the bootstrap hook
# strips it at its lock reader and everything downstream — the integrity
# comparison, the install record, skills-doctor's `check_provenance` — keeps
# seeing bare hex. That tolerance had to be DELIVERED to every consumer before
# this began emitting, or a consumer would receive a lock its own hook rejects.
LOCK_DIGEST_PREFIX = "sha256:"


def _label_digests(skills: Dict[str, str]) -> Dict[str, str]:
    """Tag each bare-hex digest with the algorithm that produced it.

    Applied at the document boundary, not inside `collect_skills`, so every
    comparison BETWEEN builder outputs keeps working on bare hex. That is what
    leaves `--check-current` alone: it compares a pinned tree against a working
    tree, both freshly digested, and never reads the lock's stored values.
    """
    return {name: LOCK_DIGEST_PREFIX + digest for name, digest in skills.items()}


# The canonical STORED shape, derived from LOCK_DIGEST_PREFIX rather than
# re-typed. One constant decides what a lock's digests look like; a second
# spelling of it inside the validator is how a checker ends up asserting a
# shape the writer stopped emitting, agreeing with nothing and reporting so at
# exit 0.
_LOCK_DIGEST_RE = re.compile(re.escape(LOCK_DIGEST_PREFIX) + r"[0-9a-f]{64}")
# How many offending names a --check-format failure prints before summarising
# the rest. Every one of a lock's digests can be wrong at once — all eight of
# the stranded consumer locks were, and a 22-skill consumer would be 22 — and a
# report that scrolls its own remediation line off the top of a CI log is a
# report nobody acts on.
_FORMAT_REPORT_CAP = 10


def digest_format_offenders(skills: Dict[str, str]) -> List[str]:
    """Every `skills` name whose stored digest is not `sha256:<64 lowercase hex>`.

    Names, and TYPE names for a non-string — never the offending VALUE. In the
    case that motivated this flag the offending value is precisely a bare
    64-hex string, which is the token gitleaks' `generic-api-key` rule fires on
    beside a keyword-bearing name; that is the whole reason LOCK_DIGEST_PREFIX
    exists. Echoing one into a CI log in order to complain about it would put
    it back into scanned text, and this report is written for CI logs.

    Case is part of the shape rather than pedantry: `hexdigest()` is lowercase,
    so an uppercase digest was not written by this generator at all, and every
    reader compares it byte-for-byte against a freshly computed one — the
    bootstrap hook included, where the mismatch is reported as tampering.
    """
    offenders: List[str] = []
    for name in sorted(skills):
        value = skills[name]
        if isinstance(value, str) and _LOCK_DIGEST_RE.fullmatch(value):
            continue
        offenders.append(
            name if isinstance(value, str) else f"{name} ({type(value).__name__})"
        )
    return offenders


def _suggested_repin_ref(document: dict) -> Optional[str]:
    """The lock's own `ref`, if it is one this generator would have written.

    Charset-guarded because the caller below prints it into a COPY-PASTEABLE
    shell command, and the fleet bumper slices that command verbatim into a PR
    body. The document arrives as found on disk, so a hand-edited `ref` is
    arbitrary text; `_REF_RE` is the predicate the rest of this file already
    uses for "a ref we would write and the hook would accept", and its charset
    is exactly what is safe unquoted in a shell. Reused rather than re-spelled,
    for the reason stated above `_LOCK_DIGEST_RE`.

    Shell-safe is not the whole job, though, so `_REF_RE` alone is not the
    whole guard: its charset admits a leading `-`, and a ref of `--repo`
    renders as `--ref --repo --repo <clone>`, where the echoed value is no
    longer a value but an OPTION to the command it lands in. Measured across
    `--repo` / `-o` / `--repin` / `-1` / `-`: every one fails loudly and
    leaves the lock untouched (argparse exit 2, or exit 1 from git), so this
    is a printed command that cannot RUN rather than one that runs wrong. That
    is still the defect this pair exists to close — a remediation line that
    does not do what the sentence above it promises — so a dash-leading ref
    takes the placeholder path instead. Nothing legitimate is lost: a commit
    sha never starts with `-`, and git itself will not take a refname that
    does.
    """
    ref = document.get("ref")
    if isinstance(ref, str) and _REF_RE.fullmatch(ref) and not ref.startswith("-"):
        return ref
    return None


def report_digest_format(document: dict, output: Path) -> int:
    """Print --check-format's verdict for one lock and return its exit status.

    Reads `skills`, and `ref` only to name it in the remediation command below
    — no `registry`, no `sources`, and no git — because the fleet bumper calls
    this per consumer lock before it has a clone of anything to read from.
    Written defensively for the same reason `_render_sources` is: the document
    arrives as found ON DISK and may be hand-edited into any shape at all, and
    a verdict that raises instead of printing is a verdict nobody gets.

    WHICH PREFIX, and why it is load-bearing rather than cosmetic. `FAILED:` is
    reserved for ONE verdict — "this lock's stored digests are malformed" —
    because a caller keys a WRITE off it. _agent-guidance's
    `scripts/bump-consumer-locks.sh` greps `^FAILED:` in this flag's output to
    set `repin_reason=format` and re-pin the consumer's lock; anything else it
    routes to a `fail` that reports and counts the repo without rewriting it,
    under a comment reading "Only the flag's own FAILED: means 'these digests
    are malformed'; anything else means the question could not be answered".
    That was true of a missing file, a directory at -o, a top-level array and
    invalid JSON — all `ERROR:`, from the GeneratorError handler — and FALSE of
    the two conditions below, which said `FAILED:` while being no answer about
    digest shape at all: no `skills` map, and an empty one. Both mean "there is
    nothing here whose shape could be wrong", which is a different answer and
    one a re-pin is the wrong repair for. They say `ERROR:` now, the prefix
    this generator already uses for "the question could not be answered", so
    the caller's existing safe branch takes them. Exit status cannot carry the
    distinction: all three exit 1.

    TWO SIBLING SITES IN _agent-guidance MOVE WITH THIS, because the grep
    above is a WRITE trigger in another repo: change the prefix here and you
    change what that repo rewrites. Neither is editable from here. One is the
    STUB generator its bump tests run against (`test/run-tests.sh`), which
    reproduces this prefix split on purpose and says so. The other is the
    bumper's own prose about what an empty map costs. Both AGREE with this
    file today — the stub prints `ERROR:` for both conditions above, and the
    bumper describes such a lock as counted and LEFT ALONE rather than
    re-pinned nightly. Nothing compares the three copies automatically, so a
    prefix change here still has to be carried over there by hand.

    So the agreement is worth re-checking rather than assuming, and it is
    checkable by name from either end. HERE:
    `test_nothing_to_check_is_an_error_not_a_failed` pins, per condition, the
    sentence the caller's comment depends on. THERE:
    `test_bump_format_gate_empty_skills` drives the real bumper against a
    lock whose `skills` map is empty and asserts it reports, counts, and
    pushes no branch — the only test in that repo that reaches either branch
    above, and therefore the only thing that goes red if the stub drifts back
    to `FAILED:`. It covers the EMPTY map only; the missing / non-map
    condition has no test on that side at all, so this file's own is the
    whole guard for it. If either name stops existing, that side is
    unguarded.
    """
    skills = document.get("skills")
    if not isinstance(skills, dict):
        print(f"ERROR: {output} has no usable 'skills' map "
              f"(got {type(skills).__name__}), so it holds no digests whose shape "
              "could be right. The bootstrap hook refuses a lock of this shape "
              "outright; regenerate it.")
        return 1
    # An empty map still FAILS THE RUN rather than passing vacuously, and the
    # distinction is the point of the flag: a generate over a bundle with no
    # skills writes one legitimately, so "every digest is well-shaped" is
    # trivially true of it. This flag gates a REPAIR, where "nothing to fix"
    # and "nothing there" are different answers — collapsing them is the shape
    # of every green check that turned out to be measuring nothing.
    #
    # THE ONE SHAPE NO REPAIR REACHES, named here because here is where it is
    # decided rather than left to be met as a red scheduled run. A re-pin over
    # a registry with no skills writes the same empty map straight back, and
    # the bumper's shrink guard then refuses to propose the result — correctly,
    # because an emptied lock reaps the installed skills of every ephemeral
    # session in that repo. While this verdict said `FAILED:`, that composed
    # into a loop with no automated exit: re-pin nightly, have the re-pin
    # refused nightly, go red nightly. The `ERROR:` prefix closes the churn
    # half — the bumper's `^FAILED:` no longer matches, so it reports and
    # counts the repo without cloning it, re-pinning it or opening a PR. It
    # stays RED, which is right: an empty map means a registry whose bundles
    # have all vanished, and that is a human's decision, not a lock chore. What
    # must not happen is someone meeting that red while holding a re-pin that
    # cannot work and "fixing" it by loosening the shrink guard.
    if not skills:
        print(f"ERROR: {output} lists no skills at all, so nothing in it has a "
              "digest to be in the right shape — 'no work' is not 'no errors'. "
              "Regenerate it against the bundles it means to install.")
        return 1
    offenders = digest_format_offenders(skills)
    if not offenders:
        print(f"OK: every digest in {output} is "
              f"{LOCK_DIGEST_PREFIX}<64 hex> ({len(skills)} skills).")
        return 0
    print(f"FAILED: {len(offenders)} of {len(skills)} digests in {output} are not "
          f"{LOCK_DIGEST_PREFIX}<64 lowercase hex>. The fix is a RE-PIN, which "
          "recomputes every digest from the pinned ref and labels it on the way "
          "out — not a hand edit, which would paste a label onto a value nobody "
          "recomputed and turn the lock into an attestation over unverified "
          "bytes:")
    # `--ref` is part of the command, not decoration. `--repin` deliberately
    # does NOT inherit `ref` (advancing it is the whole operation), so a
    # remediation printed without one falls through to `resolve_ref(repo,
    # "HEAD")` and repairs the shape against whatever commit that clone happens
    # to be sitting on. Measured on a copy of repo-settings' real lock before
    # this line carried a ref: the printed command moved the pin off 94cdcc81
    # onto the clone's HEAD and recomputed all eight digests from the NEW
    # tree. They came out byte-identical only because the `adam` bundle had not
    # moved between the two commits; re-pin that same lock at 283b2f0c, where
    # it had, and three of the eight differ (further back, at a9828bf, four
    # do). The count is whatever the two trees make it — the ref is named here
    # because an unanchored "N of eight" is a number the next reader cannot
    # check, and two readers of this comment already disagreed about it. So the
    # latent failure is a "format repair" that
    # silently re-attests a lock over a different tree — one line under a
    # sentence promising the digests are recomputed "from the pinned ref".
    #
    # Naming the lock's own ref makes that sentence true and keeps the repair
    # what it claims to be: with it, the re-pin is a pure RELABEL of the same
    # stored values (measured on that same copy — 8 bare in, the same 8 hexes
    # labelled out), which is the only repair a complaint about SHAPE has any
    # business proposing. Advancing a pin is a separate decision, and the fleet
    # bumper already treats it as one — its own comment calls the extra `ref`
    # churn "the honest cost" of healing through a re-pin. A cost stated
    # deliberately there must not be one an operator's terminal hands them by
    # omission here.
    #
    # The fleet bumper anchors a format repair the same way, so there is no
    # asymmetry left to expect. Its `repin_reason` is `format` exactly when this
    # verdict fired, and on that branch it builds `repin_ref_args=(--ref
    # "$old_ref")` before invoking --repin. The two paths therefore AGREE: a
    # shape repair holds the pin whether a human at a terminal or the nightly
    # performs it. That is what makes quoting this report verbatim into a PR
    # body honest — the command a reviewer reads there is the command that
    # produced the diff beneath it. Checkable from the other side, in
    # scripts/bump-consumer-locks.sh; nothing compares the two copies
    # automatically, so a change to either half still has to be carried across
    # by hand, exactly like the prefix split above.
    suggested_ref = _suggested_repin_ref(document)
    print(f"  python3 scripts/generate_skills_lock.py --repin "
          f"--ref {suggested_ref or '<the commit this lock pins>'} "
          "--repo <a clone of the registry this lock names> -o <this lock>")
    if suggested_ref is None:
        print("  (this lock carries no usable 'ref' of its own, so name the commit it "
              "should describe: --repin will not repair a lock whose pin it cannot "
              "read.)")
    for name in offenders[:_FORMAT_REPORT_CAP]:
        print(f"  - {name}")
    if len(offenders) > _FORMAT_REPORT_CAP:
        print(f"  - ... and {len(offenders) - _FORMAT_REPORT_CAP} more")
    return 1


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
        "skills": _label_digests(collect_from_sources(sources)),
        "generated_from": resolved,
    }
    if len(sources) > 1:
        document["sources"] = [
            {field: source[field] for field in SOURCE_FIELDS} for source in sources[1:]
        ]
    # `if key in document` is what keeps `sources` out of a single-source lock
    # entirely rather than writing it as `[]`.
    return {key: document[key] for key in FIELD_ORDER if key in document}


def _select_sources(
    registry: str,
    extras: Sequence[dict],
    only: Optional[str],
) -> Tuple[List[dict], bool]:
    """Narrow --check-current to ONE registry, before anything is located.

    Filtering `extras` up front rather than filtering plan_sources' output
    keeps an UNRELATED source's missing sibling checkout from deciding this
    source's answer: plan_sources raises on the first `no checkout at ...` it
    meets, so a lock federating two registries could not be asked about one of
    them on a machine that has only that one cloned.

    Returns (extras subset, include_primary).

    What a scoped run stops asserting is everything it did not select.
    plan_sources' bundle-uniqueness check still runs, but over the SELECTED set
    alone, so a conflict between two un-selected sources goes unreported on
    that run. That is correct for a scoped question and wrong to lean on: a
    scoped run is a gate input, never the place a lock's whole-document
    validity is established. `--check` and the bootstrap hook are that place.

    The comparison is against the lock's own registry STRINGS, which are what
    identify a source here. A lock recording OWNER/REPO does not match an
    https:// URL naming the same repository, so the refusal below lists every
    registry the lock plans rather than saying only "unknown" — the mismatch
    has to be diagnosable from the one line the caller sees.
    """
    if only is None:
        return list(extras), True
    only = validate_registry(only, "--only")
    matched = [source for source in extras if source["registry"] == only]
    if only == registry:
        if matched:
            # Representable, and nothing else refuses it: plan_sources rejects a
            # BUNDLE claimed twice, and says nothing about one registry standing
            # as both the primary and a source with different bundles and a
            # different layout. Scoping to it has two answers, so it gets none.
            raise GeneratorError(
                f"--only {only}: this lock names it as BOTH its primary registry and a "
                "federated source, and those two carry different bundles and different "
                "layouts — so scoping to it has two different answers. Fix the lock, or "
                "ask about the whole document with an unscoped --check-current."
            )
        return [], True
    if not matched:
        planned = ", ".join(
            dict.fromkeys([registry] + [source["registry"] for source in extras])
        )
        raise GeneratorError(
            f"--only {only}: not a registry this lock plans. It plans {planned} — "
            "name one of those exactly as the lock spells it."
        )
    return matched, False


def check_current(
    repo: Path,
    registry: str,
    ref: str,
    bundles: Sequence[str],
    extras: Sequence[dict] = (),
    overrides: Optional[Dict[str, str]] = None,
    *,
    only: Optional[str] = None,
) -> Tuple[Dict[str, str], List[Tuple[dict, List[str]]]]:
    """Compare the content at each source's pinned ref with its working tree.

    Returns (working-tree skill map, [(source, its differences), ...]), with an
    entry only for a source that actually drifted. An empty list means every
    pinned commit still describes its bundles as they stand, i.e. the lock is
    current as well as faithful.

    GROUPED BY SOURCE, not one flat list: a caller is told which registry
    drifted without reading a message. The differences themselves still name
    the ref they came from, but that was the weak form of the guarantee — it
    put the attribution in prose, where a reader had to notice that the sha in
    a detail line was not the sha in the headline above it.

    Each returned source carries an extra `is_primary` key alongside its
    planned fields. It is set here rather than derived by the caller because
    plan order is what decides it (the primary is the first source planned) and
    a filtered list no longer carries that index.

    `only` narrows the question to ONE registry the lock plans rather than
    reinterpreting a combined answer. That distinction is the whole reason the
    parameter exists: a caller asking "has the federated half moved" off a
    full-lock verdict reads a primary-only drift as federated drift, because
    one drifted source and one drifted primary produce the same single verdict.
    Asking a different question cannot drift with the wording of an answer.
    """
    selected, include_primary = _select_sources(registry, extras, only)
    sources = plan_sources(repo, registry, ref, bundles, selected, overrides or {})
    if not include_primary:
        # Dropped AFTER planning, not before it: plan_sources is what validates
        # the primary's registry and ref and what refuses a bundle claimed
        # twice, and a scoped run should still refuse a lock that cannot be
        # planned at all. It is the READING of the primary — a `git archive` of
        # its whole tree — that a scoped question has no business doing.
        sources = sources[1:]
    working: Dict[str, str] = {}
    drifted: List[Tuple[dict, List[str]]] = []
    for index, source in enumerate(sources):
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
        # These three strings are quoted verbatim into a PR body by
        # _agent-guidance's bumper and asserted by several tests here. Grouping
        # changed which LIST they land in; it must not change their bytes.
        differences: List[str] = []
        for name in sorted(set(here) - set(pinned)):
            differences.append(f"added: '{name}' is in the working tree but not at {at}")
        for name in sorted(set(pinned) - set(here)):
            differences.append(f"removed: '{name}' is at {at} but not in the working tree")
        for name in sorted(set(pinned) & set(here)):
            if pinned[name] != here[name]:
                differences.append(f"changed: '{name}' differs from its content at {at}")
        if differences:
            drifted.append(({**source, "is_primary": include_primary and index == 0},
                            differences))
    return working, drifted


def serialize(document: dict) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def load_lock(path: Path) -> dict:
    # OSError, not FileNotFoundError alone: `-o <a directory>` and an unreadable
    # file are both "the lock is not usable", and both used to escape as a raw
    # traceback naming internal line numbers. GeneratorError is this file's
    # stated convention — "reported as a message, never a traceback" — and the
    # reader arriving here has usually been sent by the hook's DEGRADED verdict,
    # which is the worst moment to hand them a stack trace.
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GeneratorError(f"{path} does not exist; generate it first") from None
    except OSError as exc:
        raise GeneratorError(f"cannot read {path}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise GeneratorError(f"{path} is not valid JSON: {exc}") from None
    # Valid JSON is not enough: a top-level array or string reaches every
    # `.get()` below as an AttributeError.
    if not isinstance(document, dict):
        raise GeneratorError(
            f"{path} is valid JSON but not an object: a lock is "
            f"{{registry, ref, bundles, skills, ...}}, got {type(document).__name__}"
        )
    return document


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


def _inherited_registry(existing: dict, output: Path) -> str:
    """The primary registry, read off a lock that `--repin` is advancing.

    Strict where the generate path is permissive, and that asymmetry is the
    point: a plain generate falls back to DEFAULT_REGISTRY because nothing was
    inherited, while a re-pin whose lock has lost its `registry` — a botched
    merge-conflict resolution is the realistic route — would be silently
    re-pointed at THIS repo, a different repository than the consumer declared,
    at exit 0. `--check` reports that same lock as stale and names the field;
    the write path must not launder what the verify path correctly rejects.
    """
    registry = existing.get("registry")
    if not isinstance(registry, str) or not registry:
        raise GeneratorError(
            f"{output}: 'registry' is missing or unusable ({registry!r}), so there is "
            "nothing for --repin to inherit — and defaulting would silently re-point "
            "this lock at another repository. Fix the field, or generate the lock "
            "without --repin."
        )
    return registry


def _inherited_bundles(existing: dict, output: Path) -> List[str]:
    """The primary bundle list, read off a lock that `--repin` is advancing.

    Validated per element, not merely type-checked as a list. A bundle name is
    substituted into a filesystem path by `layout_dir` and into every skills
    key, and until `--repin` existed the inherited value could only be reached
    by `--check`, which never writes — so nothing downstream of it was ever a
    write. `["../../../outside"]` escapes the `git archive` extraction and
    digests content that is in no commit of any registry, writing an
    attestation over bytes nobody published; a non-string element reaches
    `str.replace` or a dict lookup and escapes as a traceback. Both are refused
    here, before `plan_sources` can be handed either.

    An empty list is refused for the same reason a missing key is: it is falsy,
    so the generate path's `or list(DEFAULT_BUNDLES)` would narrow the lock to
    the default bundle and drop every other bundle's skills, at exit 0.
    """
    bundles = existing.get("bundles")
    if not isinstance(bundles, list) or not bundles:
        raise GeneratorError(
            f"{output}: 'bundles' is missing or is not a non-empty list ({bundles!r}), "
            "so there is nothing for --repin to inherit — and defaulting would silently "
            f"narrow this lock to {list(DEFAULT_BUNDLES)}, dropping every other bundle's "
            "skills. Fix the field, or generate the lock without --repin."
        )
    for bundle in bundles:
        if not isinstance(bundle, str) or not _NAME_RE.fullmatch(bundle):
            raise GeneratorError(
                f"{output}: {bundle!r} in 'bundles' is not a plausible bundle name "
                f"(must match {_NAME_RE.pattern}) — a bundle name becomes a directory "
                "path under the fetched tree and a key in 'skills', so re-pinning one "
                "would digest content from outside the pinned tree and write a lock "
                "the bootstrap hook refuses WHOLESALE. Fix the field."
            )
    return list(bundles)


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
    parser.add_argument("--only", metavar="REGISTRY", default=None,
                        help="scope --check-current to ONE registry this lock plans — the "
                             "primary, or one federated source — so a caller learns WHICH "
                             "half has moved from the exit code of the question it asked, "
                             "rather than from the wording of a combined answer. Name the "
                             "registry exactly as the lock spells it. A scoped run asserts "
                             "nothing about the sources it did not select, so it is a gate "
                             "input and not a substitute for --check.")
    parser.add_argument("--check-format", action="store_true",
                        help="verify every STORED digest in the lock is "
                             f"'{LOCK_DIGEST_PREFIX}<64 lowercase hex>', and exit 1 if not, "
                             "naming the skills that are not. A THIRD question, not a "
                             "widening of the two above: --check-current never reads the "
                             "stored values at all, and --check reads them only inside a "
                             "whole-document comparison that reports a wrong shape as "
                             "'digest changed' — so a lock of bare hex is green on the "
                             "first and indistinguishable from content drift on the "
                             "second. Reads the file ALONE: no registry checkout, no "
                             "network, not one git call. --repo is accepted (it is "
                             "meaningful when this composes with --check) but is never "
                             "READ here, so the verdict cannot depend on which clone, or "
                             "no clone, is at hand. An empty 'skills' map is an ERROR "
                             "rather than a vacuous pass; only malformed digests are "
                             "reported as FAILED.")
    parser.add_argument("--repin", action="store_true",
                        help="advance an EXISTING lock onto another commit, and write it. "
                             "The lock's own identity is inherited — registry, bundles and "
                             "the whole 'sources' array — and only 'ref' is re-resolved, to "
                             "HEAD of --repo or to --ref. That inheritance is the point: a "
                             "plain generate takes 'sources' from the command line alone, so "
                             "re-pinning a federated lock by rerunning it drops every "
                             "--source not repeated, writes a de-federated lock and exits "
                             "0, with --check green against the result. --registry / "
                             "--bundles / --source are therefore an ERROR alongside it — "
                             "changing a lock's identity is a separate decision, and a "
                             "separate command. The inherited fields must be present and "
                             "well-formed; a default would silently re-point or narrow the "
                             "lock. --repo must be a clone of the registry the lock names "
                             "(checked against the commit it already pins), because that is "
                             "where the new pin and every digest are read from. Refuses to "
                             "create a lock that does not exist yet: there is no identity to "
                             "inherit, and choosing one is a plain generate.")
    parser.add_argument("--repin-source", metavar="'OWNER/REPO@[REF]'", action="append",
                        default=None,
                        help="with --repin, advance the pin of ONE federated source the "
                             "lock ALREADY names. An empty REF means that source's HEAD. "
                             "Repeatable, one per source. It merges by registry key, so it "
                             "can never add, drop or reorder a source — which is what "
                             "separates it from --source, and why --source stays an error "
                             "alongside --repin. Bundles and layout are the lock's identity "
                             "and are not expressible here; they stay inherited.")
    parser.add_argument("--digest", metavar="DIR", default=None,
                        help="print the sha256 digest of one skill directory and exit "
                             "(the bootstrap hook's integrity check calls this)")
    parser.add_argument("--repo", metavar="PATH", default=None,
                        help=f"git repo to read content from (default: {REPO_ROOT})")
    args = parser.parse_args(argv)

    # An argparse error, not a precedence rule: --repin writes and --check
    # verifies, so a run asking for both has no single answer to give, and
    # silently picking one would report on a lock the caller did not mean.
    if args.repin and (args.check or args.check_current or args.check_format):
        parser.error("--repin writes a lock; --check / --check-current / --check-format "
                     "verify one. Run them as separate commands.")

    # The same reasoning one field further in. --repin's premise is that the
    # lock's identity is authoritative, so a flag that OVERRIDES that identity
    # has no coherent meaning here — and `--source` did not merely override, it
    # REPLACED the entire inherited array, so naming one source dropped every
    # other registry silently, at exit 0. That is the ADR's de-federation trap
    # reached through the flag written to close it. Refusing is what makes
    # "inherits its identity" a property of the code rather than of the
    # docstring. --source-repo is deliberately NOT here: it says where a
    # source's checkout lives on this machine, which is not the lock's identity
    # and is exactly what a federated re-pin needs on a machine whose siblings
    # are not at the default path.
    if args.repin:
        overriding = [
            flag for flag, value in (("--registry", args.registry),
                                     ("--bundles", args.bundles),
                                     ("--source", args.source))
            if value
        ]
        if overriding:
            parser.error(
                f"--repin inherits the lock's identity; {', '.join(overriding)} would "
                "override it (--source REPLACES the whole inherited 'sources' array, "
                "de-federating the lock). Advance the pin and change what the lock "
                "means as separate commands: a plain generate is where identity is "
                "decided."
            )

    # Deliberately NOT in the `overriding` list above. --repin-source merges by
    # registry key and never replaces the inherited array, so folding it in
    # would make the one flag that can fix a federated pin an error alongside
    # the only flag it means anything with.
    if args.repin_source and not args.repin:
        parser.error(
            "--repin-source advances a pin the lock already carries, so it only "
            "means anything alongside --repin; a plain generate states its "
            "sources with --source.")
    if args.only and not args.check_current:
        parser.error("--only scopes --check-current; pass it alongside that flag.")
    if args.only and (args.check or args.check_format):
        parser.error(
            "--only scopes --check-current alone. --check compares the WHOLE "
            "document and --check-format reads the file alone, so neither can be "
            "narrowed to one registry — and a run whose exit code is the worst of "
            "three verdicts, only one of them scoped, answers no question anyone "
            "asked.")

    if args.digest is not None:
        print(digest_skill_dir(Path(args.digest)))
        return 0

    repo = Path(args.repo) if args.repo else REPO_ROOT
    output = Path(args.output) if args.output else DEFAULT_LOCK

    verifying = args.check or args.check_current or args.check_format
    # --repin joins the verify modes in reading the lock's identity back out of
    # it, and departs from them on `ref` alone (below). load_lock already
    # reports a missing file as a GeneratorError; --repin restates it, because
    # "generate it first" is the wrong instruction for a flag whose entire job
    # is advancing something that already exists.
    inheriting = verifying or args.repin
    # `is_file`, not `exists`: a directory at --output exists, and would fall
    # through to `read_text` as a raw IsADirectoryError traceback.
    if args.repin and not output.is_file():
        what = "is not a file" if output.exists() else "does not exist"
        raise GeneratorError(
            f"{output} {what}; --repin advances an existing lock and cannot "
            "create one. A new lock decides which registry and which bundles a "
            "consumer installs, which is a deliberate act — generate it without "
            "--repin."
        )
    existing = load_lock(output) if inheriting else {}

    # --check-format is answered HERE — off `existing` alone, above the `ref`
    # resolution and everything that reaches for a checkout — because reading
    # nothing but the file is this flag's CALLING CONVENTION and not merely how
    # it happens to be written: the fleet bumper runs it per consumer lock
    # before it has cloned any registry. The early return is what keeps that
    # literal. One git call reachable on this path (`resolve_ref(repo, "HEAD")`
    # below, taken whenever the lock's own `ref` is missing) and the bumper's
    # very first use of it fails on a machine that has no clone yet — for a
    # question that never needed one.
    #
    # When another verify flag is also present the run continues instead, and
    # the verdict already printed is folded into the exit code below: the three
    # compose the way --check and --check-current always have.
    format_status = 0
    if args.check_format:
        format_status = report_digest_format(existing, output)
        if not (args.check or args.check_current):
            return format_status
    if args.repin:
        # Strict, because this is the path that WRITES what it inherited. See
        # the helpers: the generate path's fall-through to DEFAULT_REGISTRY /
        # DEFAULT_BUNDLES is correct when nothing was inherited and silently
        # destructive when something should have been.
        registry = _inherited_registry(existing, output)
        bundles = _inherited_bundles(existing, output)
    else:
        registry = args.registry or existing.get("registry") or DEFAULT_REGISTRY
        bundles = (
            _parse_bundles(args.bundles)
            or (existing.get("bundles") if isinstance(existing.get("bundles"), list) else None)
            or list(DEFAULT_BUNDLES)
        )
    if args.repin:
        # The lock names a registry; --repo names a clone. Nothing else ties the
        # two together, and when they are different repositories the re-pin
        # writes a commit from one under the name of the other — exit 0, and
        # --check green because it re-derives from the same wrong clone. The pin
        # already in the lock is the probe: a clone that IS this registry has
        # that commit. Deliberately checked even when --ref is given, because
        # the question is whether this clone is the registry, not which commit
        # was asked for.
        pinned = existing.get("ref")
        if not isinstance(pinned, str) or not pinned:
            raise GeneratorError(
                f"{output}: 'ref' is missing or unusable ({pinned!r}); --repin advances "
                "an existing pin and this lock has none to advance."
            )
        if _git(repo, "cat-file", "-e", f"{pinned}^{{commit}}").returncode != 0:
            raise GeneratorError(
                f"{repo} does not contain {pinned}, the commit {output} pins for "
                f"'{registry}' — so this checkout is not that registry, and re-pinning "
                "from it would write a commit the registry does not have (the hook then "
                "cannot fetch it, and every consumer session reports DEGRADED). Point "
                "--repo at a clone of that registry, or fetch the pinned commit into "
                "this one."
            )
    # `ref` is the ONE field --repin does not inherit, and the asymmetry is the
    # whole flag: advancing the pin is the operation, so inheriting it would
    # rewrite the lock to what it already said and report success for a no-op.
    # A verify mode inherits it for the opposite reason — see the docstring:
    # --check asks whether the lock is faithful to the ref it PINS.
    ref = args.ref or (existing.get("ref") if verifying else None) or resolve_ref(repo, "HEAD")
    # Inherited by every mode that reads the lock at all, verify and re-pin
    # alike — the one field with a mode-dependent answer is `ref` above. A
    # --check that silently dropped the federated half would go green while
    # verifying only some of what the lock promises; a --repin that dropped it
    # would WRITE that half away, and then pass --check for having done so.
    raw_sources = existing.get("sources")
    if args.source:
        extras = [parse_source(spec) for spec in args.source]
    elif args.repin and raw_sources is not None and not isinstance(raw_sources, list):
        # Present but the wrong JSON type. Falling through to "no sources" is
        # harmless on --check (the rebuilt document has none, so the comparison
        # goes red and names it) and is a DELETION here: the federated half
        # would be written away under a normal `Wrote ...` line at exit 0, and
        # --check green afterwards. The hook calls the same shape fatal
        # ("lock: 'sources' must be a list"), so the repair tool must not
        # "fix" it by discarding the array.
        raise GeneratorError(
            f"{output}: 'sources' must be a list, got {type(raw_sources).__name__} — "
            "--repin will not silently drop a federated source list it cannot read. "
            "Fix the field (the bootstrap hook refuses this lock for the same reason)."
        )
    elif isinstance(raw_sources, list):
        extras = [
            normalize_source(raw, f"sources[{index}]")
            for index, raw in enumerate(raw_sources)
        ]
    else:
        extras = []
    overrides = dict(parse_source_repo(spec) for spec in args.source_repo or [])
    # Guarded on BOTH flags, which is not redundant with the parser.error above:
    # the two fail differently and that is the point. The parser.error is the
    # exit-2 contract a test pins; this guard is what makes "--repin-source
    # mutated a plain generate's sources" unrepresentable even if a later editor
    # moves the parse. It has to sit after `overrides` is built, because
    # resolving an empty ref goes through source_checkout's override lookup.
    if args.repin and args.repin_source:
        extras = _apply_repin_sources(extras, args.repin_source, repo, registry, overrides)

    if verifying:
        # Seeded with --check-format's verdict, printed above: the exit code is
        # the worst of whichever flags ran, which is what already made --check
        # and --check-current safe to pass together.
        status = format_status
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
            # Asked here as well as inside check_current so the OK line can name
            # the ref this run actually looked at. Both calls answer off the same
            # inherited `extras`, and the helper is pure.
            selected, include_primary = _select_sources(registry, extras, args.only)
            working, drifted = check_current(
                repo, registry, ref, bundles, extras, overrides, only=args.only
            )
            # The ref of the FIRST source the run planned. Unscoped that is the
            # primary, so these bytes are what they always were; scoped to a
            # source it is the only ref the run read. One rule rather than a
            # branch, so there is no new way for this line to name a ref nobody
            # checked.
            scoped_ref = ref if include_primary else selected[0]["ref"]
            if not drifted:
                print(f"OK: the working tree still matches {scoped_ref} "
                      f"({len(working)} skills).")
            else:
                # THE CROSS-REPO CONTRACT this loop must keep, as three facts a
                # reader can check rather than a promise:
                #
                #   1. Every verdict line begins at column 0 with the literal
                #      `FAILED:`. _agent-guidance's
                #      scripts/bump-consumer-locks.sh branches on
                #      `grep -q '^FAILED:'`, and anything else there is an
                #      ERROR it refuses to act on.
                #   2. The primary's block comes FIRST. That script slices
                #      `sed -n '/^FAILED:/,$p' | head -20` into a PR body, and
                #      the path substitution beside that slice names the
                #      primary lock alone.
                #   3. Every headline is IMMEDIATELY followed by its own
                #      remediation line. That is what makes the 20-line cap
                #      safe: a truncation can drop a whole trailing block, but
                #      it can never separate a headline from the command that
                #      fixes it.
                #
                # Plan order gives (2) for free — plan_sources puts the primary
                # first — and `test_check_current_names_both_when_primary_and_source_both_drift`
                # and `test_every_failed_line_is_followed_by_its_own_remediation_command`
                # are what say so out loud.
                # Whether the primary's own block is about to tell the reader
                # to advance it decides whether the federated blocks below hold
                # its pin. See the --ref anchor there.
                primary_drifted = any(entry["is_primary"] for entry, _ in drifted)
                for source, differences in drifted:
                    if source["is_primary"]:
                        print(f"FAILED: the bundle has moved on since {ref}, which {output} "
                              "still pins — nothing added or changed since then reaches an "
                              "ephemeral surface. Re-pin it (after committing the content) "
                              "with:")
                        # --repin, not a bare re-run: this lock may federate, and a
                        # plain generate takes `sources` from the command line alone,
                        # so following that instruction literally would de-federate it
                        # at exit 0. The remediation line is the one place a reader is
                        # told which command to type, so it must name the safe one.
                        print("  python3 scripts/generate_skills_lock.py --repin")
                        # PRIMARY-ONLY, deliberately. This note reasons about the
                        # reader's own merge base, and is simply false about another
                        # registry's drift.
                        print("  (Seeing this on a freshly merged branch usually means the re-pin was cut")
                        print("  before another commit touched a locked skill: the lock is still faithful")
                        print("  to the ref it pins — that ref just is not the bundle. Same fix, re-pin.)")
                    else:
                        print(f"FAILED: {source['registry']}'s bundles have moved on since "
                              f"{source['ref']}, which {output} still pins for it — nothing "
                              "added or changed there reaches an ephemeral surface. Advance "
                              "that source's pin (after committing the content in that "
                              "registry) with:")
                        # --ref is part of the command, not decoration, and
                        # this is the same defect #108 fixed for
                        # --check-format's line: --repin deliberately does not
                        # inherit `ref`, so a command printed without one falls
                        # through to resolve_ref(repo, "HEAD") and advances the
                        # PRIMARY pin — a content advance this verdict just
                        # said had not happened, arriving as a side effect of a
                        # source-only repair. Measured by the verifier on a
                        # fixture where only the source drifted: the printed
                        # command took the lock's primary ref from 60f17465 to
                        # the clone's HEAD 4bd46e75, at exit 0. The fleet
                        # bumper quotes these lines into a PR body as the
                        # command that produced the diff beneath it, which is
                        # only honest if running it produces that diff and no
                        # other.
                        #
                        # Dropped when the primary drifted too, because its own
                        # block above is then telling the reader to advance it:
                        # anchoring here would hand them two lines that
                        # contradict each other, and one bare
                        # `--repin --repin-source` is what advances both.
                        anchor = "" if primary_drifted else f"--ref {ref} "
                        print("  python3 scripts/generate_skills_lock.py --repin "
                              f"{anchor}--repin-source '{source['registry']}@'")
                    for line in differences:
                        print(f"  - {line}")
                status = 1
        return status

    document = build_lock(repo, registry, ref, bundles, extras, overrides)
    try:
        # newline="": the lock is a COMMITTED artifact whose bytes are compared
        # (test_this_repos_committed_lock_regenerates_byte_identically, and
        # `--check`). Text mode would end its lines with os.linesep, so a lock
        # regenerated on Windows would differ from the same lock regenerated on
        # Linux in every single line.
        output.write_text(serialize(document), encoding="utf-8", newline="")
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
