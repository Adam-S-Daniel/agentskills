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
`test_the_hooks_digest_agrees_with_the_generators_on_a_tricky_skill` asserts
the two agree on a non-trivial directory (nested dirs, an empty file, CRLF, a
UTF-8 filename, no trailing newline). Change either copy and change the other;
that test is what says so.

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
import shlex
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import (Collection, Dict, Iterable, List, Mapping, NamedTuple, Optional,
                    Sequence, Set, Tuple)

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
# The same set MINUS the space, for a caller-supplied local path — which may
# legitimately contain one. See `_reject_line_breaks`.
_LINE_UNSAFE_RE = re.compile(r"[\x00-\x1f\x7f]")
# A ref that IDENTIFIES a commit, as against `_REF_RE`, which only says a ref
# is safe to hand to git and the hook. What it decides: whether a --repin may
# treat the lock's own pin as proof that the clone in front of it is the
# registry the lock names. That probe asks git whether the checkout contains
# the pinned commit, and proves nothing for a ref that is not one —
# `main^{commit}` resolves in ANY clone with a main branch.
#
# It decides that on TWO paths, not one. `--repin` refuses (the blockers), and
# `--check-current` reads the same blockers to decide whether it may print a
# re-pin command at all — so flipping this regex changes what a read-only mode
# prints. The earlier note here said "load-bearing on the --repin path alone",
# which was already false when it was written: the commit that wired
# check_current to `repin_source_blocker` is the parent of the one that added
# this comment. That both ends read the same predicates is held by
# `test_every_refusal_a_repin_can_give_is_one_both_paths_read`.
#
# The two sides differ in how a non-sha pin GETS there, measured rather than
# assumed: a source's ref is resolved before it is written (`--source
# 'b=reg@main:skills'` lands in the lock as a 40-hex sha), so a branch name
# there is hand-written. The PRIMARY's is not — `--ref main` is written
# verbatim, `"ref": "main"`, at exit 0 — so that half is reachable without
# editing a lock at all.
#
# Case-insensitive, though this generator writes only lowercase: git resolves
# an UPPERCASE 40-hex sha (`git cat-file -e <UPPER>^{commit}` exits 0,
# measured), so refusing one with "which is not a commit sha" hands the reader
# a sentence they can check and find false — and then sends them to a repair.
# Accepting it also normalises the lock, because the re-pin writes
# `resolve_ref`'s lowercase output back. Which matters beyond tidiness: the
# hook's `fetch_source` branches on `^[0-9a-f]{40}$` and would try to clone
# `--branch <UPPER>`, so an uppercase pin is a lock the hook cannot fetch.
_COMMIT_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")
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
    `test_the_hooks_digest_agrees_with_the_generators_on_a_tricky_skill` binds
    the two; edit one and you must edit the other.

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
        # A SYMLINK IS REFUSED, NOT DIGESTED, and refusing is what keeps this
        # digest a COMMITMENT TO THE DIRECTORY rather than to a subset of it.
        # `is_file()` FOLLOWS symlinks, so before this guard a symlink to a
        # DIRECTORY and a DANGLING symlink both answered false and were skipped
        # by the `not is_file()` arm below — contributing no manifest entry at
        # all, not the link name and not the target. Two materially different
        # skill directories therefore produced the SAME digest, and the hook
        # installed the tampered one under a digest that verified. The old
        # comment ("broken symlinks carry none either") stated the intent
        # correctly for the dangling case and was silent on the one that costs:
        # a symlink to a directory is not "no bytes", it is a whole subtree
        # this walk never sees. A symlink to a FILE is refused too — it does
        # contribute an entry, but the bytes it commits to live OUTSIDE the
        # directory, so the digest stops being a statement about this tree.
        #
        # Refusing rather than re-encoding is what makes this a NON-EVENT for
        # the fleet: measured across all three registries that feed a lock
        # (agentskills `plugins/`, cms-platform `skills/`, agentskills-private)
        # there are ZERO symlinks under any skill directory, so no digest any
        # committed `skills.lock` names changes. That is what lets this land
        # without the coordinated fleet-wide re-pin a change to the digest
        # ENCODING would have forced, and without the forward/backward pin trap
        # the `sha256:` prefix rollout had to be sequenced around. If a bundle
        # ever genuinely needs a symlink, THAT is the change that has to carry
        # the migration — and it will be visible, because this refuses.
        #
        # `rglob` surfaces all three shapes as entries with `is_symlink()` true
        # and does not descend into a symlinked directory, so this one test
        # before the `is_file()` arm covers the whole class. Mirrored in the
        # hook's `digest_dir`; `test_both_digest_implementations_refuse_a_
        # symlink` binds them.
        if candidate.is_symlink():
            raise GeneratorError(
                "symlink in skill directory: "
                f"{candidate.relative_to(root).as_posix()} (in {path}) — a "
                "digest cannot commit to a symlink's target, so a skill "
                "directory may not contain one"
            )
        if not candidate.is_file():
            continue  # directories carry no bytes
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
    try:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
            try:
                archive.extractall(dest, filter="data")
            except TypeError:  # Python < 3.11.4 has no extraction filters
                archive.extractall(dest)
    except tarfile.TarError as exc:
        # A commit whose tree has NO ENTRIES is the reachable case, and it is
        # not the "no bytes at all" it looks like: git still writes its
        # `pax_global_header` plus the usual padding (measured: 10240 bytes,
        # first bytes `pax_global_header\0...`), and python's tarfile refuses
        # that stream because a pax global header must be followed by a member
        # header — `ReadError('end of file header')`.
        #
        # GeneratorError rather than the traceback that used to escape here:
        # every other unusable input in this module is reported as a message,
        # and _agent-guidance's bumper reads this tool's failures by shape —
        # `^FAILED:` at column 0 for drift, an exit code for everything else. A
        # traceback is neither, so a registry that emptied a source tree
        # arrived there as an unclassifiable run.
        empty = _git(repo, "ls-tree", "-r", "--name-only", ref)
        if empty.returncode == 0 and not empty.stdout.strip():
            raise GeneratorError(
                f"{repo}: the tree at {ref} has no files in it at all, so there is "
                "nothing to read skills from. Check the ref, and check that this clone "
                "is the repository you meant — an emptied registry is a registry-side "
                "decision, not something a lock can be re-pinned through."
            ) from None
        raise GeneratorError(
            f"cannot read `git archive {ref}` in {repo}: {exc}"
        ) from None


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


def _reject_line_breaks(value: str, where: str) -> str:
    """The same guard for a caller-supplied PATH, minus the space.

    Every value `_addressing` echoes — `--repo`, `--source-repo`, `-o` — lands
    inside a line this file tells a reader to run, on the stream
    _agent-guidance's scripts/bump-consumer-locks.sh greps for `^FAILED:` and
    slices into a PR body. A newline in one of them writes a second line into
    that stream: a fabricated `FAILED:` headline at column 0, or a second
    `python3 ...` under a headline that did not produce it, which is exactly
    what `report_drift`'s cross-repo contract says cannot happen.
    `shlex.quote` does not help — it preserves a newline inside single quotes
    rather than rejecting it.

    Separate from `_reject_control` for ONE reason, and it is the reason this
    is not simply that function: a local path may legitimately contain a
    SPACE, and `_CONTROL_RE` rejects one. A registry, ref or layout may not,
    which is why those keep the stricter guard. Everything else about the two
    is the same, deliberately — TAB, LF, CR, NUL and DEL are refused here too.
    """
    if _LINE_UNSAFE_RE.search(value):
        raise GeneratorError(
            f"{where}: must not contain control characters. This value is echoed "
            "into a command this tool prints, and a line break there forges a "
            "second line in a stream other tools read by line.")
    return value


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
            #
            # FOLDED, not compared. Exact equality answers a question about
            # bytes; the question is which DIRECTORY `~/.claude/skills/<name>`
            # resolves to, and the filesystem decides that. APFS and NTFS are
            # case-insensitive by default, so `Synced` and `SYNCED` ARE that
            # store there, and Win32 strips trailing dots and spaces from a path
            # component, so `synced.` opens `synced` — and all three pass
            # `_NAME_RE` above. Measured on the exact-equality version: this
            # generator wrote `adam/Synced` at exit 0, and the hook's own purge
            # then removed the canary planted at that name. The hook's reader
            # and its record-driven prune fold identically; a lock this
            # generator writes and that hook refuses is the gap all three exist
            # to close.
            if skill_dir.name.rstrip(". ").lower() == "synced":
                raise GeneratorError(
                    f"{skill_dir.relative_to(tree_root).as_posix()}: a skill "
                    f"directory named {skill_dir.name!r} cannot be locked — it names "
                    "'synced' on a case-insensitive filesystem; the bootstrap hook "
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
    """Parse `--source-repo '<key>=<local path>'`.

    THE WHOLE SPEC is line-checked, before it is split and before either half
    is stripped, because the whole spec is what gets echoed: `_addressing`
    restates the caller's string verbatim (`--source-repo {shlex.quote(spec)}`)
    into every command a report prints, and the fleet bumper slices that stream
    by line. Checking the halves instead left a gap the shape of `.strip()` —
    a leading newline was removed before the guard saw it and kept in the raw
    spec, so `--source-repo $'cms-platform=\nFAILED: ...'` printed two column-0
    `FAILED:` lines out of one verdict. The rule this is an instance of: guard
    the value that is echoed, not a value derived from it.
    """
    where = f"--source-repo {spec!r}"
    _reject_line_breaks(spec, where)
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


def restated_sources(extras: Sequence[dict], unproven: Collection[str] = ()) -> str:
    """The `--source` flags a plain generate needs to leave this lock federated.

    Appended to every refusal that sends a reader to a plain generate, because
    a plain generate takes `sources` from the COMMAND LINE ALONE: the inherited
    array is replaced by whatever --source flags the command carries, so advice
    that names one source, or none, silently drops the rest at exit 0 — and
    --check is green afterwards for having done so.

    Not a hypothetical. Measured on this branch before this clause existed, by
    following each refusal's own words literally: a two-source lock refused on
    the source side, repaired with the single `--source` the message named,
    came back with ONE source at exit 0; a federated lock refused on the
    primary side, repaired with the "(--registry / --ref / --bundles)" the
    message named, came back with no `sources` key at all. The file already
    knew — report_drift's primary remediation is a `--repin` for exactly this
    reason, and the dup-registry refusal says "the whole array" — but each new
    refusal had to remember, and the third one did not.

    `unproven` names registries whose recorded ref is the thing being refused;
    each is rendered `<sha>` rather than echoed back, since echoing one would
    advise restating the pin as the string that is not a pin. A plain `str`
    would be a Collection of its own characters and match nothing, so callers
    pass a tuple or a set.

    WHERE THOSE CLONES ARE is named as a RULE and not as a path, and that is
    the derived answer rather than a hedge. A `--source` is read from a local
    checkout that `source_checkout` looks for beside `--repo`, and the plain
    generate this sentence describes carries no `--repo` of its own — the
    reader picks one. So there is no single path to state: the answer that
    holds for every `--repo` the reader might use is the lookup rule plus the
    flag that overrides it. (The `--repin` LINES a report prints are the
    opposite case and are addressed the opposite way: each names the `--repo`
    it will run with, so `_addressing` derives a real path per source and
    marks the line a template when it cannot.) Without this clause, following
    the sentence verbatim on a machine whose clone is not the default sibling
    exits 1 at "no checkout at ..." — measured on the layout
    `_source_at_a_non_default_checkout` builds, and held by
    `test_the_plain_generate_a_refusal_names_says_where_its_clones_are_read_from`.
    """
    assert not isinstance(unproven, str), "unproven is a collection of registries"
    if not extras:
        return ""
    flags = " ".join(
        "--source '{}'".format(_source_spec(
            {**source, "ref": "<sha>"} if source["registry"] in unproven else source))
        for source in extras
    )
    return (
        " Note that a plain generate takes `sources` from the command line alone, so it "
        "must carry a --source for every source the lock is to keep, or the ones it "
        f"omits are dropped at exit 0 — this lock's are: {flags}. Each of those is read "
        "from a local git checkout, looked for at the sibling ../<repo-name> of "
        "whatever --repo that generate is given, so a source whose clone is anywhere "
        "else needs a --source-repo '<bundles>=<path>' beside its --source or the run "
        "stops at 'no checkout at ...'."
    )


def repin_source_blocker(
    extras: Sequence[dict], registry: str, primary_registry: str
) -> Optional[str]:
    """Why `--repin-source <registry>@` cannot advance that source — or None.

    ONE predicate, read by the flag that REFUSES it (`_apply_repin_sources`)
    and by the report that RECOMMENDS it (`report_drift`). Those two answering
    separately is how a tool comes to print a command it then rejects at exit
    1: --check-current reported the drift and named the repair, and the repair
    was refused, leaving a drifted lock with no route the tool itself would
    accept. The whole sentence is returned rather than re-worded at each site,
    so the refusal and the report cannot come to say different things —
    `test_the_report_and_the_refusal_give_the_same_reason` compares the two
    strings.

    EVERY reason this flag refuses a spec lives here, including the two that
    read as command-line mistakes rather than lock defects. Leaving one out is
    not a saving: the "not a federated source" refusal was left in
    `_apply_repin_sources` on the reasoning that check_current only ever asks
    about a source it has just read — true of THAT one and false of the
    primary-registry refusal beside it, because a lock may name one registry as
    both its primary and a federated source, and then check_current does read
    such a source and did print a `--repin-source` the flag rejected. A
    predicate that is total needs no such reasoning to stay correct.
    """
    matched = [source for source in extras if source["registry"] == registry]
    if registry == primary_registry:
        if matched:
            # BOTH halves under one name. `--check` is green on it —
            # plan_sources' uniqueness check is keyed on bundle — and
            # `_select_sources` already refuses to SCOPE to such a registry
            # for the same reason: one name, two entries, two answers. So the
            # drift is reported by the unscoped run alone, and before this
            # refusal was visible to the report it was reported with a
            # `--repin-source` command that exited 1.
            return (
                "this lock names that registry as BOTH its primary registry and a "
                "federated source. --repin-source advances a FEDERATED pin and refuses "
                "the primary's name outright, so under one name there is no spec that "
                "reaches the federated entry alone. Give the two halves two names — the "
                "federated entry is the one to re-point, since the primary's pin is what "
                "--ref (or a bare --repin) advances." + restated_sources(extras)
            )
        return (
            "that is this lock's PRIMARY registry, not a federated source; the primary's "
            "pin is what --ref (or bare --repin) advances"
        )
    if not matched:
        federated = ", ".join(
            dict.fromkeys(source["registry"] for source in extras)
        ) or "none"
        return (
            f"that is not a source this lock federates ({federated}); ADDING a source "
            "changes what the lock means and is a plain generate, not a re-pin."
            + restated_sources(extras)
        )
    if len(matched) > 1:
        # A registry the lock federates TWICE is representable and --check
        # green: plan_sources' uniqueness check is keyed on BUNDLE, so two
        # entries may share a registry while carrying different bundles, their
        # own layout and independent pins. Merging by registry key would then
        # advance BOTH from one spec — moving a pin nobody named, with its
        # digests rewritten to content nobody reviewed, at exit 0. This flag
        # cannot say which one is meant: bundles are the lock's identity and
        # are deliberately not expressible here.
        #
        # So it refuses, which is the answer _select_sources already gives to
        # the analogous ambiguity on the read-only path — "scoping to it has
        # two answers, so it gets none". Refusing in one place and guessing in
        # the other, in the direction that moves MORE pins, is the asymmetry
        # worth not having.
        claimed = "; ".join(
            ", ".join(source["bundles"]) or "no bundles" for source in matched
        )
        return (
            f"this lock federates that registry twice, under [{claimed}], each with its "
            "own pin — so one spec names two sources and advancing 'it' has two answers. "
            "Bundles are the lock's identity and are not expressible on this flag, so it "
            "will not pick one for you: give that registry a single 'sources' entry, or "
            "restate the whole array with a plain generate, which is where identity is "
            "decided." + restated_sources(extras)
        )
    # A source pinned at something that is not a commit cannot be re-pinned
    # from an unproven clone, because the pin is the whole proof. `validate_ref`
    # accepts a BRANCH name in a source's ref, and while this generator always
    # RESOLVES a source's ref before writing it (measured: `--source
    # 'b=reg@main:skills'` lands as a 40-hex sha, unlike the primary's `--ref`),
    # a hand-written lock can carry `"ref": "main"` — and `main^{commit}`
    # resolves in a fork, in a same-named
    # repo under another owner, in any clone at all. Measured before this
    # refusal existed: with the sibling `../cms-platform` replaced by a wholly
    # different repository that also has `main`, `--repin --repin-source` wrote
    # the impostor's HEAD under the named registry at exit 0.
    #
    # Refused rather than resolved-and-hoped, because the alternative repairs
    # the lock's ref by writing a commit nobody has verified belongs to that
    # registry — which is the failure the probe exists to prevent, arriving
    # through the repair.
    #
    # SUBSUMED as things stand, and deliberately kept: the same condition makes
    # `repin_unproven_sources_blocker` refuse the whole invocation, and both
    # paths consult that one first — so this branch is not what makes
    # `test_repin_source_refuses_a_source_pinned_at_a_branch` pass today, and a
    # reader should not go looking for the coverage here. It stays because this
    # predicate's contract is "every reason THIS flag refuses a spec", and a
    # total predicate does not need another one's call order to be right.
    if not _COMMIT_SHA_RE.fullmatch(matched[0]["ref"]):
        return (
            f"this lock pins that source at {matched[0]['ref']!r}, which is not a commit "
            "sha — and the commit the lock pins is the ONLY thing that proves the "
            "checkout this would re-pin from is that registry at all. A branch name "
            "resolves in any clone, so re-pinning against one would write whatever some "
            "unverified directory is sitting on. Restate that source at the commit it is "
            "actually on with a plain generate, then advance it."
            + restated_sources(extras, unproven=(registry,))
        )
    return None


def repin_plan_blocker(
    bundles: Sequence[str], extras: Sequence[dict], registry: str
) -> Optional[str]:
    """Why the document this lock declares cannot be PLANNED at all — or None.

    `plan_sources` raises exactly these two, and it is the first thing
    `build_lock` does — so both are refusals every `--repin` meets, and both
    are decidable from the lock's own fields without a checkout. They lived as
    raises inside `plan_sources` and were therefore invisible to the report,
    which is one half of how a SCOPED `--check-current` came to print a
    `--repin` this generator refuses: `_select_sources` narrows `extras` before
    `plan_sources` sees them, so a scoped run plans one source, meets neither
    condition, and reports a whole-document defect as repairable.

    Measured on this branch before this predicate, on a three-source lock whose
    second source was hand-edited to claim the first's bundle: `--check-current`
    unscoped exited 1 with no command, while `--check-current --only <the third
    source>` printed `--repin --ref <sha> --repin-source '<third>@'`, and
    running that line at that lock exited 1 with "bundle 'cms-platform' is
    claimed by both ...". A nine-source lock did the same with the cap. (At that
    line, addressed at that lock: the printed command carried no `--repo` or
    `-o` either, which is the same class and is `_addressing`'s half of it.)

    Asked over the WHOLE document — the primary's bundles plus every entry in
    `sources` — because that is the document any `--repin` rebuilds, whatever
    the run that reported it was scoped to.
    """
    if len(extras) > MAX_SOURCES:
        return (
            f"'sources' lists {len(extras)} entries; at most {MAX_SOURCES} are allowed — "
            "the bootstrap hook fetches every one of them before the session starts, and "
            "refuses the WHOLE lock over the limit, installing nothing"
        )
    claimed: Dict[str, str] = {}
    for source in [{"registry": registry, "bundles": list(bundles)}, *extras]:
        for bundle in source["bundles"]:
            if bundle in claimed:
                return (
                    f"bundle '{bundle}' is claimed by both {claimed[bundle]} and "
                    f"{source['registry']}; a bundle has one registry and one layout"
                )
            claimed[bundle] = source["registry"]
    return None


def _per_bundle(skills: Mapping[str, str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for key in skills:
        bundle = key.split("/", 1)[0]
        counts[bundle] = counts.get(bundle, 0) + 1
    return counts


def _movable_bundles(
    bundles: Sequence[str],
    extras: Sequence[dict],
    *,
    primary_moves: bool,
    named: Collection[str],
) -> Set[str]:
    """The bundles whose content a given `--repin` can change.

    A re-pin rebuilds the WHOLE document, but only the pins it actually
    advances can change what a bundle contains: every other source is
    re-planned at the 40-hex sha the lock already records, so `git archive`
    hands back the same tree and the same digests. `primary_moves` is false
    exactly when the command carries `--ref <the lock's own ref>`; `named` is
    the set of registries on `--repin-source`.

    One function because the answer decides two things that must agree: which
    bundles `repin_shrink_blocker` compares on the APPLY path, and which it
    compares when a report asks the same question about a command it is about
    to print. Round 4 had those two asking different questions, and both
    directions of the disagreement bit — the report suppressed a source's
    remediation over a shrink its command could not cause, and the apply path
    refused a bare `--repin` over a `skills` key for a bundle the lock does not
    declare at all, which no command can ever repopulate.
    """
    movable = set(bundles) if primary_moves else set()
    for source in extras:
        if source["registry"] in named:
            movable.update(source["bundles"])
    return movable


def repin_shrink_blocker(
    before: Mapping[str, str],
    after: Mapping[str, str],
    movable: Collection[str],
    extras: Sequence[dict] = (),
) -> Optional[str]:
    """Why a re-pin must not be written — a bundle that lost every skill it had.

    The condition `_agent-guidance`'s bump-consumer-locks.sh has refused to
    PROPOSE since ADR 0009 (`skills_shrink_reason`): a bundle DIRECTORY stopped
    existing at the commit this would pin — a rename, a deleted plugin, a
    layout change — which is a registry-side decision and not a lock chore.

    Stated per BUNDLE and never as "the whole lock is empty", although the
    bumper says both: an emptied lock is every bundle at once and needs no
    second sentence, and a second sentence is one the two callers can pick
    differently. They did, while it existed — a `--only` run narrowed to one
    bundle said "declares no skills at all" where the whole-document refusal
    said "bundle(s) cms-platform", so the report handed the reader a sentence
    the flag never says.

    The fleet bumper refusing it was never cover for this repo: measured on
    this branch before this guard, a plain `--repin` whose registry had emptied
    a bundle wrote the smaller lock and exited 0, identically with and without
    `--repin-source`. Anyone re-pinning by hand, and every consumer whose
    re-pin is not the bumper's, got the silent version.

    `movable` narrows BOTH sides to the bundles the command in question can
    actually change — `_movable_bundles` is the one place that set is decided,
    and the apply path and the report ask it the same way. An empty set means
    the command moves no pin, so there is nothing for it to empty.

    Narrowing is not only a scoping nicety; it is what keeps this refusal from
    being a dead end. A `skills` key whose bundle the lock does not declare —
    a merge artifact, a hand edit — is missing from every rebuild, so an
    unnarrowed comparison calls it a shrink that no `--repin` can ever clear,
    and the sentence below ("a bundle directory stopped existing") is one the
    reader can check against `bundles` and find false.
    """
    keep = set(movable)
    before = {key: value for key, value in before.items()
              if key.split("/", 1)[0] in keep}
    after = {key: value for key, value in after.items()
             if key.split("/", 1)[0] in keep}
    if not before:
        return None
    counts = _per_bundle(after)
    gone = sorted(bundle for bundle, count in _per_bundle(before).items()
                  if count and not counts.get(bundle))
    if not gone:
        return None
    return (
        f"the re-pin would leave bundle(s) {', '.join(gone)} with no skills at all"
        " — a bundle directory stopped existing at the commit this would pin: a "
        "rename, a deleted plugin, a layout change. A skill that leaves the lock stops "
        "being installed in every ephemeral session that reads it, so this is a "
        "registry-side decision rather than a lock chore. Fix it where it broke, or "
        "decide deliberately, with a plain generate, that the lock should stop "
        "declaring that bundle. "
        "(_agent-guidance's bump-consumer-locks.sh refuses to PROPOSE such a re-pin for "
        "the same reason; this refuses to write one.)" + restated_sources(extras)
    )


def _repin_shrink_guard(
    existing: dict,
    document: dict,
    output: Path,
    movable: Collection[str],
    extras: Sequence[dict],
) -> None:
    """Refuse to WRITE a re-pin that empties a bundle.

    Asked after build_lock rather than from a blocker the earlier guards read,
    because the answer is not in the lock: it is in the content at the commit
    the re-pin would write, which is exactly what build_lock has just gone and
    read. `remediation` asks the same predicate, with the same `movable` set
    from `_movable_bundles`, off `--check-current`'s own reading of that
    content.
    """
    skills = existing.get("skills")
    blocker = repin_shrink_blocker(
        skills if isinstance(skills, dict) else {},
        document.get("skills") or {}, movable, extras)
    if blocker:
        raise GeneratorError(f"{output}: {blocker}")


def repin_unproven_sources_blocker(extras: Sequence[dict], output: Path) -> Optional[str]:
    """Why NO --repin of this lock can be trusted — a source pinned at a branch.

    Scoped to the whole invocation rather than to one registry, because that is
    the scope of the damage: plan_sources resolves EVERY source's ref before
    build_lock writes it, whether or not `--repin-source` named that source, so
    a `"ref": "main"` anywhere in the array is re-pinned to whatever the sibling
    clone is sitting on. The per-source identity probe cannot help — it asks
    whether the checkout contains the commit the lock pins, and `main^{commit}`
    resolves in any clone with a main branch, which is the whole reason a branch
    name is refused rather than resolved-and-hoped.

    Measured before this refusal, with srcB hand-pinned at 'main' and its
    sibling clone replaced by a wholly unrelated repository: a BARE `--repin`
    naming nothing wrote the impostor's HEAD under srcB's registry at exit 0.

    `validate_ref` accepts a branch name in a source's ref and this generator
    never writes one — a `--source 'b=reg@main:skills'` lands as a 40-hex sha —
    so the only route in is a hand-edited lock. That makes the shape rare and
    not unreachable, which is the same standing the primary's branch pin has.
    """
    unproven = [source for source in extras
                if not _COMMIT_SHA_RE.fullmatch(source["ref"])]
    if not unproven:
        return None
    named = ", ".join(f"{source['registry']} at {source['ref']!r}" for source in unproven)
    return (
        f"{output} pins a federated source at something that is not a commit sha "
        f"({named}). EVERY --repin re-resolves EVERY source's ref, named on "
        "--repin-source or not, so a branch name there is re-pinned to whatever some "
        "sibling clone is sitting on — and the commit the lock pins is the only thing "
        "that proves that clone is the registry at all. Restate those sources at the "
        "commits they are actually on with a plain generate, then advance them."
        + restated_sources(extras,
                           unproven={source["registry"] for source in unproven})
    )


def repin_primary_blocker(
    existing: dict, extras: Sequence[dict], registry: str, output: Path
) -> Optional[str]:
    """Why a `--repin` cannot advance this lock's PRIMARY pin — or None.

    `repin_source_blocker`'s counterpart, and it exists because that one was
    not enough: EVERY command --check-current prints is a `--repin`, the
    federated `--repin --ref <r> --repin-source '<reg>@'` included, so a
    primary-side refusal rejects the source-side remediation too. Consulting
    only the source predicate is how the report came to print `--repin` for a
    lock this same generator refuses at exit 1.

    Read by the guard that REFUSES (`_repin_primary_guard`) and by the report
    that RECOMMENDS (`report_drift`), by the same rule as its counterpart: one
    sentence, returned rather than re-worded, so the two cannot disagree.
    """
    pinned = existing.get("ref")
    if not isinstance(pinned, str) or not pinned:
        return (
            f"{output}: 'ref' is missing or unusable ({pinned!r}); --repin advances "
            "an existing pin and this lock has none to advance."
        )
    if not _COMMIT_SHA_RE.fullmatch(pinned):
        return (
            f"{output} pins '{registry}' at {pinned!r}, which is not a commit sha — "
            "and the commit the lock pins is the ONLY thing that proves the clone "
            "--repin reads is that registry. A branch name resolves in any clone, so "
            "re-pinning against one would write whatever that directory is sitting "
            "on. Restate the pin at the commit it is actually on with a plain "
            "generate (--registry / --ref <sha> / --bundles), then advance it."
            + restated_sources(extras)
        )
    return None


def _repin_primary_guard(
    existing: dict, extras: Sequence[dict], registry: str, output: Path, repo: Path
) -> None:
    """Refuse a --repin this lock and this clone cannot support.

    The lock names a registry; --repo names a clone. Nothing else ties the two
    together, and when they are different repositories the re-pin writes a
    commit from one under the name of the other — exit 0, and --check green
    because it re-derives from the same wrong clone. The pin already in the
    lock is the probe: a clone that IS this registry has that commit.
    Deliberately checked even when --ref is given, because the question is
    whether this clone is the registry, not which commit was asked for.

    Which needs the pin to BE a commit — `main^{commit}` resolves in every
    clone that has a main branch, so a branch-name pin turns the probe into a
    formality any impostor passes. That half is `repin_primary_blocker`'s,
    because a report can foresee it from the lock alone and must not recommend
    a command it would trip. The probe below is the half a report cannot
    foresee: it depends on the clone in front of this process, which is not in
    the lock.
    """
    blocker = (repin_primary_blocker(existing, extras, registry, output)
               or repin_unproven_sources_blocker(extras, output))
    if blocker:
        raise GeneratorError(blocker)
    pinned = existing["ref"]
    if _git(repo, "cat-file", "-e", f"{pinned}^{{commit}}").returncode != 0:
        raise GeneratorError(
            f"{repo} does not contain {pinned}, the commit {output} pins for "
            f"'{registry}' — so this checkout is not that registry, and re-pinning "
            "from it would write a commit the registry does not have (the hook then "
            "cannot fetch it, and every consumer session reports DEGRADED). Point "
            "--repo at a clone of that registry, or fetch the pinned commit into "
            "this one."
        )


def _parse_repin_specs(specs: Sequence[str]) -> Dict[str, str]:
    """`--repin-source` specs as {registry: ref}, refusing a repeated registry.

    A COMMAND-LINE refusal, not a lock one: it is about what this invocation
    asked for twice, so there is nothing for a report to foresee and it stays
    out of `repin_source_blocker`.
    """
    wanted: Dict[str, str] = {}
    for spec in specs:
        reg, ref = parse_repin_source(spec)
        if reg in wanted:
            raise GeneratorError(f"--repin-source names {reg} twice; one pin per source")
        wanted[reg] = ref
    return wanted


def _apply_repin_sources(
    extras: Sequence[dict],
    specs: Sequence[str],
    repo: Path,
    primary_registry: str,
    overrides: Dict[str, str],
) -> List[dict]:
    """Merge --repin-source pins into the INHERITED array. Never adds, never drops.

    Never fans one spec out across two entries either: a registry this lock
    federates TWICE is refused below rather than merged into both, so a spec
    moves exactly one pin or none. (Said this way, not as "never advances a
    source the caller did not name", because that would be an end-to-end claim
    and this function is not the end — see the branch-ref paragraph below.)

    Merge by registry KEY, never replace the array: that distinction is the
    whole difference between this flag and `--source`, which took precedence
    over the inherited `sources` and dropped every registry the command line
    did not repeat. A source this flag does not name comes back by REFERENCE —
    nothing here rewrites it, reorders it or drops it.

    That is a promise about this function and NOT about the bytes that reach
    disk, and the two used to part company for a ref this generator would never
    have written: `validate_ref` accepts a BRANCH name in a source's ref, so a
    lock can carry `"ref": "main"`, and plan_sources resolves every source's
    ref downstream — so such a source was re-resolved, and could advance,
    under any --repin at all, this flag or a bare one. `--repin` now refuses
    that lock outright (`repin_unproven_sources_blocker`), which closes the gap
    rather than narrowing this promise around it.
    `test_an_unnamed_source_pinned_at_a_branch_refuses_the_whole_repin` is the
    measurement.

    "By reference" is therefore not quite "byte-identical", and the remaining
    gap is deliberate. `_COMMIT_SHA_RE` is case-insensitive, so an UPPERCASE
    40-hex sha is a pin a --repin accepts — and plan_sources re-resolves every
    inherited source ref downstream, writing `resolve_ref`'s lowercase back.
    `test_an_unnamed_source_with_an_uppercase_pin_comes_back_lowercase` is the
    measurement, and it asserts the lowercasing rather than describing it. That
    normalisation is wanted rather than tolerated — see the note above
    `_COMMIT_SHA_RE`: the bootstrap hook's `fetch_source` branches on
    `^[0-9a-f]{40}$`, so an uppercase pin is a lock the hook cannot fetch. The
    promise this function keeps is that it moves no pin the caller did not
    name; the bytes downstream may still be canonicalised.

    ADDING a source is refused rather than allowed as a convenience — it
    changes what the lock means, which is a plain generate's decision — and so
    is naming the primary, whose pin is what `--ref` (or a bare `--repin`)
    advances. Both of those refusals live in `repin_source_blocker` with the
    rest, so the report that recommends this flag sees every one of them.
    """
    wanted = _parse_repin_specs(specs)
    for reg in wanted:
        # Read, never re-derived: `repin_source_blocker` is what
        # --check-current consults before it recommends this flag, and a second
        # copy of any of its conditions here is how the two drift into
        # recommending and refusing the same command.
        blocker = repin_source_blocker(extras, reg, primary_registry)
        if blocker:
            raise GeneratorError(f"--repin-source {reg}: {blocker}")
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
        # this registry has that commit. That sentence is only true because
        # `repin_unproven_sources_blocker` has already refused a lock any of
        # whose sources is pinned at something other than a commit sha —
        # `main^{commit}` resolves in any clone with a main branch, so a
        # branch-name pin proves nothing and this probe would pass an impostor.
        # The two belong together; neither is the guard alone.
        #
        # Every source this flag does NOT name is probed the same way by
        # accident downstream: plan_sources resolves its inherited ref in this
        # same clone and fails there. That accident holds for a sha pin only,
        # which is why the blocker above refuses the whole invocation rather
        # than the named registry — a branch name resolves anywhere, so an
        # unnamed source carrying one used to advance against an unproven clone
        # (`test_an_unnamed_source_pinned_at_a_branch_refuses_the_whole_repin`
        # is the measurement, and was written the other way round in round 3).
        # The NAMED source is what loses even the sha half of that accident,
        # because its ref is replaced before plan_sources sees it. This
        # restores the guard rather than adding one.
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
    # Before anything is resolved or read: both of these are properties of the
    # SPEC, so a lock that trips one must fail here rather than after a
    # checkout lookup sends the reader chasing a missing sibling clone. Read
    # from `repin_plan_blocker` rather than raised inline, so the report that
    # recommends a `--repin` can foresee them — see that predicate. (The cap
    # counts `extras`, not `extras` + the primary, because that is what the
    # hook counts, and the two must agree or the boundary case is a lock the
    # generator writes and the hook refuses.)
    blocker = repin_plan_blocker(bundles, extras, registry)
    if blocker:
        raise GeneratorError(blocker)
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


def report_digest_format(document: dict, output: Path, repo: Path,
                         source_repos: Sequence[str] = ()) -> int:
    """Print --check-format's verdict for one lock and return its exit status.

    TOUCHES NO CLONE — no checkout, no network, not one git call — because the
    fleet bumper calls this per consumer lock before it has a clone of
    anything to read from. That is the flag's calling convention rather than
    an accident of how it is written, and it is what the early return in
    `main` keeps literal.

    WHAT IT READS is wider than that, and was described here as `skills` and
    `ref` alone until the remediation below became `remediation`'s: it now
    also reads `registry`, `bundles` and `sources` — the identity a `--repin`
    inherits, so a lock that could not be re-pinned gets the refusal instead
    of a line — and this run's `--repo` and `--source-repo`, which that line
    restates so it can be pasted. Every clause of that is measured in
    `test_check_format_reads_the_lock_and_its_addressing_and_no_clone`,
    including the no-clone half, against a `--repo` that does not exist. That
    the list is COMPLETE — no read field left out of it, which is how `ref`
    went missing from the argparse help's copy — is the separate measurement in
    `test_check_format_reads_exactly_the_lock_fields_its_help_names`, which
    mutates each field of a real lock in turn; `CHECK_FORMAT_LOCK_READS` is the
    one enumeration both the help and that test read.

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
    headline = (f"{len(offenders)} of {len(skills)} digests in {output} are not "
                f"{LOCK_DIGEST_PREFIX}<64 lowercase hex>. The fix is a RE-PIN, which "
                "recomputes every digest from the pinned ref and labels it on the "
                "way out — not a hand edit, which would paste a label onto a value "
                "nobody recomputed and turn the lock into an attestation over "
                "unverified bytes")
    answer = remediation("format", existing=document, output=output, repo=repo,
                         source_repos=source_repos)
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
    # produced the diff beneath it.
    #
    # THE COUNTERPART BLOCK, named so neither half is a pointer to nowhere:
    # _agent-guidance's scripts/bump-consumer-locks.sh, under the comment
    # beginning "A SIBLING SITE MOVES WITH THIS". It asks a future reader to
    # rewrite a paragraph in THIS docstring that began "One consequence to
    # expect rather than re-discover" — the paragraph these lines replaced, so
    # that request is already discharged and the block over there is stale.
    # Removing it is the remaining half of this edit and can only be made in
    # that repo. Nothing compares the two copies automatically, so a change to
    # either half still has to be carried across by hand, exactly like the
    # prefix split above; naming the block is what keeps "by hand" from meaning
    # "by search".
    if answer.reason:
        # SAME VERDICT, no command. `FAILED:` still leads the line — the digests
        # really are malformed, which is the question this flag answers and the
        # string the fleet bumper branches on — but this lock has some OTHER
        # defect that makes the re-pin above impossible, and printing the line
        # anyway sends the bumper to a nightly exit 1 with no automated exit.
        # Measured before this, with the line's `<a clone ...>` / `<this lock>`
        # placeholders filled in as it instructs: a lock pinned at a branch, one
        # with a SOURCE pinned at a branch, and one whose `bundles` had been
        # lost each printed a --repin that this same generator then refused at
        # exit 1. (A lock whose bundle had merely emptied was fine at exit 0 —
        # this line holds the pin, so no content moves and nothing can shrink.)
        print(f"FAILED: {headline}. No re-pin command is printed for it because "
              f"this generator would refuse one: {answer.reason}")
    else:
        print(f"FAILED: {headline}:")
        print(f"  {answer.command}")
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
            # Representable, and nothing else refuses it: plan_sources rejects
            # a BUNDLE claimed twice, and says nothing about one registry
            # standing as both the primary and a source. Scoping to it selects
            # two entries, so it has two answers and gets none.
            #
            # The refusal says only what is forced. The bundle lists differ —
            # plan_sources' uniqueness check is what guarantees that, and it is
            # the sole difference guaranteed. Layout and ref are per-entry
            # fields that MAY differ and here often do not: a source omitting
            # `layout` inherits DEFAULT_LAYOUT, which is the primary's own, so
            # the earlier wording ("those two carry different bundles and
            # different layouts") told a reader something false about the lock
            # in front of them and then instructed them to fix it.
            raise GeneratorError(
                f"--only {only}: this lock names it as BOTH its primary registry and a "
                "federated source. That is two entries with two bundle lists, each with "
                "its own layout and ref, so scoping to that one name asks about two "
                "things and has two answers. Fix the lock, or ask about the whole "
                "document with an unscoped --check-current."
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
) -> Tuple[Dict[str, str], List[Tuple[dict, List[str]]], List[dict]]:
    """Compare the content at each source's pinned ref with its working tree.

    Returns (working-tree skill map, [(source, its differences), ...], the
    sources actually read), with a differences entry only for a source that
    actually drifted. An empty list means every pinned commit still describes
    its bundles as they stand, i.e. the lock is current as well as faithful.

    The third element is the PLANNED sources — every ref resolved, the primary
    already dropped when the question was scoped away from it. It is returned
    rather than re-derived by the caller because it is the only record of what
    this run looked at: a scoped OK line naming a ref off the raw lock can name
    a branch name nobody resolved, or one entry of two the scope matched.

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
    return working, drifted, sources


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


def repin_inherit_blocker(
    existing: dict, output: Path, extras: Sequence[dict] = ()
) -> Optional[str]:
    """Why this lock has nothing --repin can safely inherit — or None.

    Strict where the generate path is permissive, and that asymmetry is the
    point: a plain generate falls back to DEFAULT_REGISTRY / DEFAULT_BUNDLES
    because nothing was inherited, while a re-pin whose lock has lost one of
    those fields — a botched merge-conflict resolution is the realistic route —
    would silently re-point the lock at another repository, or narrow it to the
    default bundle and drop every other bundle's skills, at exit 0. `--check`
    reports that same lock as stale and names the field; the write path must
    not launder what the verify path correctly rejects.

    A PREDICATE rather than three raises inside the two readers it replaced,
    because --check-current recommends `--repin` and every one of these
    refusals rejects it. Measured on this branch before this predicate, on a
    lock that had really drifted: with `registry` removed, with `bundles`
    removed, and with `sources` set to a JSON object, --check-current printed
    `python3 scripts/generate_skills_lock.py --repin` under its FAILED verdict
    each time, and running that line verbatim exited 1 each time.

    The bundle list is validated per element, not merely type-checked. A bundle
    name is substituted into a filesystem path by `layout_dir` and into every
    skills key: `["../../../outside"]` escapes the `git archive` extraction and
    digests content that is in no commit of any registry, and a non-string
    element escapes as a traceback out of `str.replace` or a dict lookup. An
    empty list is refused for the same reason a missing key is — it is falsy,
    so the generate path's `or list(DEFAULT_BUNDLES)` would silently narrow the
    lock.

    `sources` is checked for TYPE only, and only on this path: falling through
    to "no sources" is harmless on --check (the rebuilt document has none, so
    the comparison goes red and names it) and is a DELETION on a re-pin — the
    federated half written away under a normal `Wrote ...` line at exit 0, and
    --check green afterwards. The hook calls the same shape fatal ("lock:
    'sources' must be a list"), so the repair tool must not "fix" it by
    discarding the array.
    """
    registry = existing.get("registry")
    if not isinstance(registry, str) or not registry:
        return (
            f"{output}: 'registry' is missing or unusable ({registry!r}), so there is "
            "nothing for --repin to inherit — and defaulting would silently re-point "
            "this lock at another repository. Fix the field, or generate the lock "
            "without --repin." + restated_sources(extras)
        )
    bundles = existing.get("bundles")
    if not isinstance(bundles, list) or not bundles:
        return (
            f"{output}: 'bundles' is missing or is not a non-empty list ({bundles!r}), "
            "so there is nothing for --repin to inherit — and defaulting would silently "
            f"narrow this lock to {list(DEFAULT_BUNDLES)}, dropping every other bundle's "
            "skills. Fix the field, or generate the lock without --repin."
            + restated_sources(extras)
        )
    for bundle in bundles:
        if not isinstance(bundle, str) or not _NAME_RE.fullmatch(bundle):
            return (
                f"{output}: {bundle!r} in 'bundles' is not a plausible bundle name "
                f"(must match {_NAME_RE.pattern}) — a bundle name becomes a directory "
                "path under the fetched tree and a key in 'skills', so re-pinning one "
                "would digest content from outside the pinned tree and write a lock "
                "the bootstrap hook refuses WHOLESALE. Fix the field."
            )
    raw_sources = existing.get("sources")
    if raw_sources is not None and not isinstance(raw_sources, list):
        return (
            f"{output}: 'sources' must be a list, got {type(raw_sources).__name__} — "
            "--repin will not silently drop a federated source list it cannot read. "
            "Fix the field (the bootstrap hook refuses this lock for the same reason)."
        )
    return None


def _repin_inherit_guard(existing: dict, output: Path, extras: Sequence[dict]) -> None:
    """Refuse a --repin whose lock cannot be read for what it declares.

    FIRST of the re-pin guards, matching the order `report_drift` composes the
    predicates in: everything after this reads `registry`, `bundles` and
    `sources` as though they are there.
    """
    blocker = repin_inherit_blocker(existing, output, extras)
    if blocker:
        raise GeneratorError(blocker)


class Remediation(NamedTuple):
    """A report's answer to "what should the reader type" — one or the other.

    `command` is the line the reader is told to type, carrying the `--repo`,
    `--source-repo` and `-o` that line needs to reach the same lock and the
    same clones. Two shapes, and which one is which is visible in the string
    itself rather than left for the reader to discover by running it:

      * a COMMAND, runnable as printed, as far as this run can tell — every
        checkout it needs is one this run either was handed or found. "As far
        as this run can tell" is the honest limit: it locates a checkout, it
        does not verify that the clone there is the registry the lock names or
        holds the pinned commit, and `plan_sources` still refuses at exit 1 if
        it is not.
      * a TEMPLATE, prefixed with `TEMPLATE_MARK` and carrying a `<...>` for
        every source whose checkout this run could not locate. It is NOT
        runnable as printed and says so in its first word.

    `reason` is the sentence the apply path would refuse with, for a lock where
    no line exists at all. Exactly one of the two is set, and `remediation` is
    the only thing that builds either.
    """

    command: Optional[str] = None
    reason: Optional[str] = None


# Every remediation this file prints, by the verdict that prints it. Named so
# `remediation` is total over a closed set rather than over whatever strings
# four call sites happened to pass.
#
#   stale   --check: the lock's bytes are not what its own pinned ref describes
#   format  --check-format: its stored digests are the wrong shape
#   primary --check-current: the primary's bundles moved past the pinned commit
#   source  --check-current: one federated source's bundles did
REMEDIATION_KINDS = ("stale", "format", "primary", "source")

# The lock fields `--check-format` reads BEYOND the digests it judges. A
# constant because two things have to agree about it and they live apart: the
# argparse help enumerates them for a cross-repo caller who cannot read this
# file, and `test_check_format_reads_exactly_the_lock_fields_its_help_names`
# measures the enumeration against the verdict one field at a time. The list
# went stale the round it was written — `ref` was missing from it, on a flag
# whose whole printed line is `--repin --ref <that field>`.
CHECK_FORMAT_LOCK_READS = ("ref", "registry", "bundles", "sources")


def _and_list(names: Sequence[str]) -> str:
    """`'a', 'b' and 'c'` — an enumeration built from the tuple, not retyped."""
    quoted = [f"'{name}'" for name in names]
    return " and ".join([", ".join(quoted[:-1]), quoted[-1]] if len(quoted) > 1
                        else quoted)


_SCRIPT = f"python3 scripts/{Path(__file__).name}"


TEMPLATE_MARK = "TEMPLATE, not a runnable command — fill in each <...> first: "


class Addressing(NamedTuple):
    """The flags a printed line needs, and the sources it could not address.

    `unlocated` names every registry whose checkout this run could not put a
    path to, so `flags` carries a `<...>` for it. Non-empty means the line is a
    template and not a command.
    """

    flags: str
    unlocated: Tuple[str, ...] = ()


def _override_spec(source: dict,
                   spec_by_key: Mapping[str, str]) -> Optional[str]:
    """This run's `--source-repo` spec for `source`, as the caller typed it.

    The key order is `source_checkout`'s, so a spec this returns is the one
    that function would really use for this source, and a spec it does not
    return is one no rebuild would consult — which is why an unmatched spec is
    dropped from a printed line rather than echoed into it.

    Restated as typed rather than rebuilt from the parsed map: the key half is
    a registry, a bundle list or one bundle name, and picking one back out
    would be this file deciding which spelling the caller meant.
    """
    for key in (source["registry"], ",".join(source["bundles"]), *source["bundles"]):
        if key in spec_by_key:
            return spec_by_key[key]
    return None


def _is_a_checkout(path: Path) -> bool:
    """Is there a git checkout here? Answered without opening one.

    `source_checkout`'s own locator is `path.is_dir()`, and that is the
    question a REBUILD asks — but it is not the question a PRINTED LINE needs
    answered. An empty `../cms-platform` directory passes `is_dir` and then
    fails the rebuild at `resolve_ref`'s "fatal: not a git repository", so
    addressing off `is_dir` alone printed a plain command that silently does
    not run. Measured on one lock with one fixed `--check-format -o <lock>`,
    changing only whether that directory exists: absent, a TEMPLATE naming the
    registry; present and empty, a bare `--repin` that exits 1.

    So git's own marker is asked for too — `.git`, which is a directory in a
    clone and a FILE in a worktree, hence `exists`; or the two entries a BARE
    repository always has at its root. Still no clone opened, no file read and
    no process spawned, which is what keeps `--check-format`'s "TOUCHES NO
    CLONE" convention.
    """
    if not path.is_dir():
        return False
    return ((path / ".git").exists()
            or ((path / "HEAD").is_file() and (path / "objects").is_dir()))


def _addressing(output: Path, repo: Path, source_repos: Sequence[str] = (),
                sources: Sequence[dict] = ()) -> Addressing:
    """The flags a printed line needs to reach the same lock and clones.

    Omitted when they are the defaults this script would pick anyway, so this
    repo's own remediation lines stay the short ones people already know. A
    CONSUMER lock is the case that needs them: without `-o` the printed command
    resolves its output to DEFAULT_LOCK (see `main`), which is not the lock the
    verdict was about — so following it either fails or rewrites this repo's own
    lock instead of theirs.

    ASKED OF THE LINE, NOT OF THIS RUN, and that is the whole point of taking
    `sources`. Every line a report prints rebuilds the WHOLE document, so it
    needs a checkout for every source THAT DOCUMENT federates — which is a
    property of the lock the line names, not of the flags this run happened to
    receive. Restating only the run's own `--source-repo` specs answered the
    wrong question, and the two invocations that legitimately pass none are
    exactly the ones it left unaddressed: `--check-format`, whose calling
    convention is a pre-clone call with no `--repo` and no `--source-repo` at
    all, and `--check-current --only <the primary registry>`, where
    `_select_sources` drops the sources so the caller has no reason to hold a
    path to one. Both printed a bare `--repin` that exits 1 with "no checkout
    at ..." on any layout whose federated clone is not the default sibling.

    So each source is addressed from what can be derived about IT:

      * this run's own spec for it, if it was given one (`_override_spec`);
      * nothing, if a checkout is already at `source_checkout`'s default
        sibling `../<repo-name>` — the line finds it there exactly as this run
        would (`_is_a_checkout`);
      * otherwise a `<...>` placeholder naming the registry, and the caller
        marks the whole line a template. A line a reader is told to run is
        runnable as printed or visibly not a command; it is never a command
        that silently does not run.

    WHAT THE PROBE MAY AND MAY NOT DECIDE, since the two halves of a verdict
    are not alike and one earlier draft of this paragraph claimed they were.
    The VERDICT — which digests are judged, the headline, the exit code — is
    decided off the lock's own bytes and cannot depend on which clone, or no
    clone, is at hand; that is `--check-format`'s contract and it is intact.
    The ADDRESSING is the opposite by construction: "where will this line find
    its clones" is a question ABOUT this machine, so its answer moves when the
    machine does, and a run beside a checkout rightly prints a shorter line
    than the same run without one. What must hold across every such answer is
    the bullet above — command or visibly a template, never a command that
    fails. All three states of the sibling are measured for exactly that by
    `test_the_addressing_moves_with_the_machine_and_the_verdict_does_not`.

    The probe is still compatible with `--check-format`'s "TOUCHES NO CLONE"
    convention, which is about what the run may DO and not about what its
    answer may depend on: it opens no checkout, reads no file, spawns no git
    and reaches no network. That is pinned by
    `test_check_format_asks_no_git_even_to_address_its_own_line`, with a `git`
    on PATH that fails the run if it is ever spawned.
    """
    flags = ""
    if repo.resolve() != REPO_ROOT:
        flags += f" --repo {shlex.quote(str(repo))}"
    # Last spelling of a key wins, exactly as main's `overrides` map does, so a
    # printed line restates the spec that would actually take effect.
    spec_by_key = {key: spec for key, spec in
                   ((parse_source_repo(spec)[0], spec) for spec in source_repos)}
    unlocated: List[str] = []
    emitted: List[str] = []
    for source in sources:
        spec = _override_spec(source, spec_by_key)
        if spec is None:
            if _is_a_checkout(source_checkout(repo, source, {})):
                continue
            unlocated.append(source["registry"])
            spec = (f"{','.join(source['bundles'])}="
                    f"<path to a checkout of {source['registry']}>")
        if spec in emitted:
            continue
        emitted.append(spec)
        flags += f" --source-repo {shlex.quote(spec)}"
    if output.resolve() != DEFAULT_LOCK:
        flags += f" -o {shlex.quote(str(output))}"
    return Addressing(flags, tuple(unlocated))


def _printable(command: str, address: Addressing) -> Remediation:
    """One line, marked for what it is — see `Remediation`."""
    return Remediation(command=(TEMPLATE_MARK + command
                                if address.unlocated else command))


def _declared_sources(existing: dict) -> Tuple[List[dict], Optional[str]]:
    """This lock's `sources`, or why they cannot be read. Never raises.

    `--check-format` is answered off the file alone, above the point where main
    parses this array, so the one path that has no `extras` to hand parses its
    own here. `normalize_source` is the same validator main uses, so the two
    cannot come to disagree about what a source is.
    """
    raw = existing.get("sources")
    if not isinstance(raw, list):
        return [], None
    try:
        return [normalize_source(entry, f"sources[{index}]")
                for index, entry in enumerate(raw)], None
    except GeneratorError as error:
        return [], str(error)


def remediation(
    kind: str,
    *,
    existing: dict,
    output: Path,
    repo: Path,
    registry: Optional[str] = None,
    extras: Optional[Sequence[dict]] = None,
    ref: Optional[str] = None,
    document: Optional[dict] = None,
    source_registry: Optional[str] = None,
    working: Optional[Mapping[str, str]] = None,
    primary_drifted: bool = False,
    primary_read: bool = True,
    source_repos: Sequence[str] = (),
) -> Remediation:
    """THE one place a remediation command is decided, for every report path.

    Four verdicts tell a reader what to type — `--check`, `--check-format`, and
    `--check-current` both scoped and unscoped — and each used to compose its
    own line and consult its own subset of the refusals. That is the shape of
    the defect class this function exists to make unrepresentable: a report
    prints a command the same generator then refuses, or one whose literal
    execution destroys part of the lock. Four rounds closed instances of it in
    pairs and each round re-opened one somewhere else, because "which refusals
    apply to this line" was answered four times.

    So it is answered once. Every report path calls this and prints what comes
    back; none of them formats a command
    (`test_no_report_path_writes_a_command_of_its_own` reads the AST for it).
    Every apply-path guard reads the same `*_blocker` predicates this composes
    (`test_every_refusal_a_repin_can_give_is_one_both_paths_read`). And
    `test_every_report_path_against_every_refusal` runs the whole matrix — every
    report path against every lock shape a refusal answers — requiring of each
    cell either no command with the reason inside the headline, or a command
    that RUNS at exit 0 and leaves `registry`, `bundles` and the `sources`
    array's identity as it found them (bar a field the verdict itself named as
    wrong). Its companion enumerates the reasons off this module's AST and
    requires every one to be produced, so a refusal cannot be added to a
    predicate without a lock shape that reaches it.

    The two things a command must therefore be, which no single call site kept:

      * ACCEPTED. Three of the four kinds print a `--repin`, so each meets
        every re-pin refusal, composed here in the order the apply path meets
        them so the sentence quoted is the sentence the flag says. Each with
        the guard it mirrors: inheritance (`_repin_inherit_guard`), the
        primary's pin and an unproven source (`_repin_primary_guard`), the
        named source (`_apply_repin_sources`), a document that cannot be
        planned (`plan_sources`, inside `build_lock`), and a re-pin that would
        empty a bundle (`_repin_shrink_guard`). The last two used to sit the
        other way round here, and a lock tripping both quoted the wrong one —
        `test_the_report_quotes_the_refusal_the_flag_reaches_first`.
      * COMPLETE. A plain generate takes its whole identity from the command
        line, so `stale`'s line restates `--registry`, `--bundles` and every
        `--source`; omitting them re-pointed the lock at DEFAULT_REGISTRY and
        narrowed it to DEFAULT_BUNDLES at exit 0, which is data loss from
        following the tool's own advice.

    `ref` is the effective ref of the run asking (the lock's own, on every
    verify path). `working` is `--check-current`'s reading of the working tree,
    and its absence is what tells the shrink question there is nothing to
    predict — `format`'s line holds the pin, so it moves no content.
    `primary_read` is whether that reading covers the primary's bundles at all;
    see the anchor below for why a command may not move a pin whose content the
    run asking has not looked at.
    """
    assert kind in REMEDIATION_KINDS, kind

    if kind == "stale":
        # The one remediation that is NOT a --repin: --check's verdict is
        # "these bytes do not describe the ref this lock pins", and the repair
        # is to rebuild the same document at the same pin. `document` is the
        # rebuild that just happened, so the command restates what it holds
        # rather than what the lock on disk claims — which is the point, since
        # the lock on disk is the thing that was found wrong.
        rebuilt = document.get("sources", [])
        address = _addressing(output, repo, source_repos, rebuilt)
        sources = "".join(f" --source '{_source_spec(source)}'" for source in rebuilt)
        return _printable(
            f"{_SCRIPT} --registry {shlex.quote(document['registry'])} "
            f"--ref {shlex.quote(document['ref'])} "
            f"--bundles {shlex.quote(','.join(document['bundles']))}"
            f"{sources}{address.flags}", address)

    # THE LOCK'S OWN sources, never the run's `extras`, because the line below
    # is a `--repin` and a `--repin` rebuilds the whole document. `extras` may
    # be narrower than that: `--check-current --only <the primary registry>`
    # hands `remediation` an empty array, and addressing the line off it left
    # a federated lock's re-pin with no way to find the clone it would need.
    declared, unreadable = _declared_sources(existing)
    if extras is None:
        if unreadable:
            return Remediation(reason=unreadable)
        extras = declared
    address = _addressing(output, repo, source_repos, declared)
    if registry is None:
        registry = existing.get("registry")

    # Inheritance first: everything after it reads `registry` and `bundles`
    # straight off the lock, exactly as main does once that guard has passed.
    blocked = repin_inherit_blocker(existing, output, extras)
    if blocked:
        return Remediation(reason=blocked)
    bundles = list(existing["bundles"])
    blocked = (repin_primary_blocker(existing, extras, registry, output)
               or repin_unproven_sources_blocker(extras, output))
    if blocked:
        return Remediation(reason=blocked)

    # The NAMED source before the document, because that is the order main
    # meets them: `_apply_repin_sources` runs on the inherited array, and
    # `plan_sources` only later, inside `build_lock`. Asked the other way
    # round, a lock that is both over the cap and federating one registry
    # twice was told to fix `sources`' length while the flag would have
    # refused the spec — a reader sent to the wrong field first.
    if kind == "source":
        blocked = repin_source_blocker(extras, source_registry, registry)
        if blocked:
            return Remediation(reason=blocked)
    blocked = repin_plan_blocker(bundles, extras, registry)
    if blocked:
        return Remediation(reason=blocked)

    if kind == "format":
        # A RELABEL, so it holds the lock's own pin — see report_digest_format
        # for why the ref is part of the command. `repin_primary_blocker` above
        # has already refused every lock whose `ref` is not a commit sha, which
        # is exactly the set this used to print a `<placeholder>` for; a line a
        # reader must edit before it runs is not a command this can promise.
        #
        # That refusal is also what retired `_suggested_repin_ref`, whose whole
        # job was keeping a hand-edited `ref` from being echoed into a shell
        # command — a ref of `--repo` rendered as `--ref --repo --repo <clone>`,
        # where the echoed value is an OPTION to the command it lands in. A
        # 40-hex sha needs no charset guard of its own, and anything else now
        # gets a reason instead of a line.
        return _printable(
            f"{_SCRIPT} --repin --ref {existing['ref']}{address.flags}", address)

    # --ref is part of the source command, not decoration: --repin deliberately
    # does not inherit `ref`, so a source-only repair printed without one falls
    # through to resolve_ref(repo, "HEAD") and advances the PRIMARY pin too.
    # Dropped when the primary drifted as well, because its own block is then
    # telling the reader to advance it and one bare --repin does both.
    #
    # Whether the PIN then moves is a second question, and the one
    # `_movable_bundles` needs: an anchored line moves it only if the ref it
    # anchors to is not the one the lock already carries. Spelled as a
    # comparison rather than as `not primary_drifted`, so it stays the same
    # question the apply path asks (`args.ref != existing["ref"]`) even for a
    # `--check-current --ref <some other commit>`, where the two would
    # otherwise part company and the report would recommend a line the guard
    # refuses.
    #
    # WHICH ref it anchors to is the run's own only when that run READ the
    # primary. `--check-current --only <source>` never does: `_select_sources`
    # drops the primary before anything is materialised, so a `--ref` there
    # names a commit the verdict looked at nothing from. Anchoring the printed
    # line at it advances a pin on the strength of content nobody read, and the
    # shrink question below then compares the whole lock against a `working`
    # map covering one source — reporting the PRIMARY's bundle as emptied and
    # withholding a command the generator accepts. Anchored at the lock's own
    # pin instead, so a scoped line moves exactly the source it is about.
    anchoring_ref = ref if primary_read else existing["ref"]
    primary_moves = (kind == "primary" or primary_drifted
                     or anchoring_ref != existing.get("ref"))
    if kind == "primary":
        command = f"{_SCRIPT} --repin{address.flags}"
    else:
        anchor = "" if primary_drifted else f"--ref {anchoring_ref} "
        command = (f"{_SCRIPT} --repin {anchor}"
                   f"--repin-source '{source_registry}@'{address.flags}")

    if working is not None:
        declared = existing.get("skills")
        blocked = repin_shrink_blocker(
            declared if isinstance(declared, dict) else {},
            working,
            _movable_bundles(bundles, extras, primary_moves=primary_moves,
                             named=() if kind == "primary" else (source_registry,)),
            extras)
        if blocked:
            return Remediation(reason=blocked)
    return _printable(command, address)


def report_drift(
    drifted: Sequence[Tuple[dict, List[str]]],
    *,
    ref: str,
    output: Path,
    existing: dict,
    extras: Sequence[dict],
    registry: str,
    repo: Path,
    working: Mapping[str, str],
    primary_read: bool,
    source_repos: Sequence[str],
) -> None:
    """Print one FAILED block per drifted source, with its repair or its reason.

    Framing only: WHICH command, or which refusal, comes from `remediation` —
    the one function every report path in this file asks, and the only one that
    builds either. This loop decides the wording around the answer and nothing
    about the answer.
    """
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
    #   3. A block's repair belongs to that block — in the
    #      UNTRUNCATED stream. The line IMMEDIATELY under its
    #      headline is the repair for it, in one of the two
    #      shapes `Remediation` describes — a command, or a
    #      template that says so in its first word — or there is
    #      no line to print and the headline carries the reason
    #      itself (see `remediation`). Never a command under one
    #      headline that repairs a different block. That is the
    #      property this loop holds and all it holds.
    #
    # (3) was first written here claiming it made the 20-line cap
    # SAFE — "a truncation can drop a whole trailing block, but it
    # can never separate a headline from the command that fixes
    # it". That is false, and the arithmetic is short enough that
    # it should have been done: the primary's block is 5 fixed
    # lines (headline, remediation, three note lines) plus one line
    # per difference, so with 14 differences the first federated
    # headline lands on line 20 — kept — and its remediation on
    # line 21 — cut. Both adversarial verifiers reproduced exactly
    # that against this generator, and
    # `test_the_bumper_cap_can_cut_a_later_headline_from_its_command`
    # measures the sliced output rather than the raw stream, so the
    # absolute cannot be restated without a red test.
    #
    # Its replacement then asserted two more things nobody had
    # checked — that the bumper slices "only the primary-scoped,
    # single-block run", and that a scoped per-source slice "would
    # be a NEW consumer" — and both were false when written. The
    # four statements below are a dated reading of that script
    # rather than a standing promise about it: measured in
    # _agent-guidance's scripts/bump-consumer-locks.sh at 4c505e3,
    # 2026-08-22. Re-read it before relying on the first one; the
    # other three are about this file and are held by tests.
    #
    #   * That script applies `sed -n '/^FAILED:/,$p' | head -20`
    #     to THREE streams, two of them from this report. One is
    #     `check_out`, a single UNSCOPED `--check-current`. The
    #     other is `fed_check_out`, which it builds by
    #     CONCATENATING one `--check-current --only <registry>`
    #     block per drifted source — a multi-block scoped stream,
    #     sliced today, under a heading that says as much ("each
    #     block below was produced by `--check-current --only <that
    #     registry>`").
    #   * What no cap above two lines can split is the FIRST
    #     block's headline from the command under it, when it has
    #     one: headline on line 1 of the slice, command on line 2,
    #     whichever block is first. In the unscoped
    #     stream that is the primary's block whenever the primary
    #     drifted, by (2) — plan_sources puts it first.
    #   * A LATER block whose command is a separate line has no
    #     such protection, in either stream, and both are
    #     reachable. Unscoped: the primary's 5 fixed lines plus 14
    #     differences puts the first federated headline on line 20
    #     and its command on 21. Scoped and concatenated: a source
    #     block is 2 fixed lines plus its differences, so 17
    #     differences in the first source's block orphans the
    #     SECOND source's headline the same way — and that stream
    #     carries no primary block at all (`_select_sources`
    #     returns include_primary False when `only` names a
    #     source), so "the primary's pair survives" is not merely
    #     unhelpful there, it is about a block that is not present.
    #   * A block the refusal above left without a command cannot
    #     be orphaned by any cap, because its answer is inside its
    #     headline.
    #
    # Plan order gives (2) for free, and the tests that measure the
    # SLICED output rather than the raw stream are what keep any of
    # this from being restated on intuition:
    # `test_the_bumper_cap_always_keeps_the_primary_headline_with_its_command`,
    # `test_the_bumper_cap_can_cut_a_later_headline_from_its_command`
    # and
    # `test_the_bumper_cap_can_cut_a_scoped_headline_from_its_command`.
    # `test_check_current_names_both_when_primary_and_source_both_drift`
    # and `test_every_failed_line_is_followed_by_its_own_remediation_command`
    # hold (2) and (3).
    # Whether the primary's own block is about to tell the reader
    # to advance it decides whether the federated blocks below hold
    # its pin. See the --ref anchor in `remediation`.
    primary_drifted = any(entry["is_primary"] for entry, _ in drifted)
    for source, differences in drifted:
        # ASKED BEFORE THE COMMAND IS PRINTED, not after it is
        # rejected — and asked of `remediation`, which is the only
        # thing in this file that decides either half. A report that
        # printed the command anyway sends its reader, or the fleet
        # bumper that builds the same flag from its own list, to an
        # exit 1 with nothing else offered. The reason the flag
        # would give is the reason printed here, verbatim and in the
        # headline itself, so the block carries its own answer
        # instead of a command that has none.
        answer = remediation(
            "primary" if source["is_primary"] else "source",
            existing=existing, output=output, repo=repo, registry=registry,
            extras=extras, ref=ref, working=working,
            source_registry=None if source["is_primary"] else source["registry"],
            primary_drifted=primary_drifted, primary_read=primary_read,
            source_repos=source_repos)
        if source["is_primary"]:
            headline = (f"the bundle has moved on since {ref}, which {output} "
                        "still pins — nothing added or changed since then reaches "
                        "an ephemeral surface.")
            refused = "No re-pin command is printed for it"
            invite = "Re-pin it (after committing the content) with:"
        else:
            # "the commit its pin resolves to", not "which the lock still
            # pins": `source` here is PLANNED, so its ref is resolved, and
            # the reason below may be about a lock that records a branch
            # name. Naming the resolved sha as what the lock pins and then
            # quoting the recorded ref one clause later asserted two
            # different pins in one sentence.
            headline = (f"{source['registry']}'s bundles have moved on since "
                        f"{source['ref']}, the commit {output}'s pin for it resolves "
                        "to — nothing added or changed there reaches an ephemeral "
                        "surface.")
            refused = "No --repin-source command is printed for it"
            invite = ("Advance that source's pin (after committing the content in "
                      "that registry) with:")
        if answer.reason:
            print(f"FAILED: {headline} {refused} because this generator would "
                  f"refuse one: {answer.reason}")
        else:
            print(f"FAILED: {headline} {invite}")
            print(f"  {answer.command}")
            if source["is_primary"]:
                # PRIMARY-ONLY, deliberately. This note reasons about the
                # reader's own merge base, and is simply false about another
                # registry's drift.
                print("  (Seeing this on a freshly merged branch usually means the re-pin was cut")
                print("  before another commit touched a locked skill: the lock is still faithful")
                print("  to the ref it pins — that ref just is not the bundle. Same fix, re-pin.)")
        for line in differences:
            print(f"  - {line}")


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
                             "second. TOUCHES NO CLONE: no registry checkout, no "
                             "network, not one git call — so the VERDICT cannot depend "
                             "on which clone, or no clone, is at hand, which is what "
                             "lets the fleet bumper ask it before cloning anything. "
                             "(The line it prints is addressed off this machine and "
                             "does move with it — see `_addressing`; what never moves "
                             "is the verdict, and a moved line is a command or is "
                             "marked a template.) It does read more of the LOCK than "
                             "the digests it judges: "
                             f"{_and_list(CHECK_FORMAT_LOCK_READS)}, plus this run's "
                             "--repo and --source-repo, because the re-pin command it "
                             "prints is the same one --check-current prints and meets "
                             "the same refusals. An empty 'skills' map is an ERROR "
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
    # `is not None`, never truthiness, in all three: argparse leaves an unpassed
    # flag as None, so presence is what these guards are about and an EMPTY
    # value is still a value the caller passed. `--only "$REG"` with an unset
    # REG is the ordinary route in a shell caller — which the fleet bumper is —
    # so the empty string is the failure mode of the intended input, not an
    # exotic one. Testing truth let `--only ''` slip both guards and degrade a
    # run into a DIFFERENT command silently: on a plain generate, one that
    # rebuilds the lock from the command line alone, de-federating it and
    # resetting `registry` to DEFAULT_REGISTRY at exit 0 with --check green
    # afterwards. (`--repin-source ''` was safe only by accident — argparse's
    # append action makes it the truthy `['']` — and is spelled the same way
    # here so the next flag added beside it copies the right pattern.)
    if args.repin_source is not None and not args.repin:
        parser.error(
            "--repin-source advances a pin the lock already carries, so it only "
            "means anything alongside --repin; a plain generate states its "
            "sources with --source.")
    if args.only is not None and not args.check_current:
        parser.error("--only scopes --check-current; pass it alongside that flag.")
    if args.only is not None and (args.check or args.check_format):
        parser.error(
            "--only scopes --check-current alone. --check compares the WHOLE "
            "document and --check-format reads the file alone, so neither can be "
            "narrowed to one registry — and a run whose exit code is the worst of "
            "three verdicts, only one of them scoped, answers no question anyone "
            "asked.")

    if args.digest is not None:
        print(digest_skill_dir(Path(args.digest)))
        return 0

    # Line-checked before anything is printed, because both are echoed into
    # every command a verdict prints (`_addressing`). The third such value,
    # `--source-repo`, is checked by its own parser below.
    repo = Path(_reject_line_breaks(args.repo, "--repo")) if args.repo else REPO_ROOT
    output = (Path(_reject_line_breaks(args.output, "-o")) if args.output
              else DEFAULT_LOCK)
    # Parsed HERE, above --check-format's early return, although only the paths
    # below the return consult the map: every verdict now restates these specs
    # in the command it prints (see `_addressing`), and a malformed one echoed
    # into a line a reader is told to run is a line that exits 2 on argparse.
    # Pure string work — no clone is touched — so --check-format's "reads the
    # file alone, not one git call" is untouched by moving it up.
    overrides = dict(parse_source_repo(spec) for spec in args.source_repo or [])

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
        format_status = report_digest_format(existing, output, repo,
                                             args.source_repo or [])
        if not (args.check or args.check_current):
            return format_status
    # Inherited by every mode that reads the lock at all, verify and re-pin
    # alike — the one field with a mode-dependent answer is `ref` below. A
    # --check that silently dropped the federated half would go green while
    # verifying only some of what the lock promises; a --repin that dropped it
    # would WRITE that half away, and then pass --check for having done so.
    #
    # Read BEFORE the inheritance guard because that guard's refusals send the
    # reader to a plain generate, which takes `sources` from the command line
    # alone — so the sentence has to name every source the lock federates or it
    # is an instruction to de-federate the lock at exit 0.
    raw_sources = existing.get("sources")
    # `declared` is `extras` as the LOCK spells it, kept apart from the merged
    # array `--repin-source` produces: the refusal below restates the sources a
    # plain generate would need, and the array to restate is the one that
    # exists on disk, not the one this run was about to write.
    if args.source:
        extras = [parse_source(spec) for spec in args.source]
    elif isinstance(raw_sources, list):
        extras = [
            normalize_source(raw, f"sources[{index}]")
            for index, raw in enumerate(raw_sources)
        ]
    else:
        extras = []
    declared = extras
    if args.repin:
        # Strict, because this is the path that WRITES what it inherited: the
        # generate path's fall-through to DEFAULT_REGISTRY / DEFAULT_BUNDLES is
        # correct when nothing was inherited and silently destructive when
        # something should have been. Every reason it refuses is in
        # `repin_inherit_blocker`, which --check-current reads before it
        # recommends this flag.
        _repin_inherit_guard(existing, output, extras)
        registry = existing["registry"]
        bundles = list(existing["bundles"])
    else:
        registry = args.registry or existing.get("registry") or DEFAULT_REGISTRY
        bundles = (
            _parse_bundles(args.bundles)
            or (existing.get("bundles") if isinstance(existing.get("bundles"), list) else None)
            or list(DEFAULT_BUNDLES)
        )
    # `ref` is the ONE field --repin does not inherit, and the asymmetry is the
    # whole flag: advancing the pin is the operation, so inheriting it would
    # rewrite the lock to what it already said and report success for a no-op.
    # A verify mode inherits it for the opposite reason — see the docstring:
    # --check asks whether the lock is faithful to the ref it PINS.
    ref = args.ref or (existing.get("ref") if verifying else None) or resolve_ref(repo, "HEAD")
    if args.repin:
        # Every reason this refuses is in `repin_primary_blocker` or in the
        # clone probe beside it — see `_repin_primary_guard`. After `extras`,
        # because the primary's pin is not the only thing a bare --repin
        # re-resolves, and BEFORE `_apply_repin_sources`, because the primary's
        # pin is what anchors whatever that flag then advances.
        _repin_primary_guard(existing, extras, registry, output, repo)
    # Guarded on BOTH flags, which is not redundant with the parser.error above:
    # the two fail differently and that is the point. The parser.error is the
    # exit-2 contract a test pins; this guard is what makes "--repin-source
    # mutated a plain generate's sources" unrepresentable even if a later editor
    # moves the parse. It has to sit after `overrides` is built, because
    # resolving an empty ref goes through source_checkout's override lookup.
    if args.repin and args.repin_source:
        declared = extras
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
                print("  " + remediation("stale", existing=existing, output=output,
                                         repo=repo, document=document,
                                         source_repos=args.source_repo or []).command)
                for line in _differences(
                    json.loads(on_disk) if on_disk.strip() else {}, document
                ):
                    print(f"  - {line}")
                status = 1
        if args.check_current:
            # Asked here as well as inside check_current so the OK line can name
            # the ref this run actually looked at. Both calls answer off the same
            # inherited `extras`, and the helper is pure.
            _selected, include_primary = _select_sources(registry, extras, args.only)
            working, drifted, read = check_current(
                repo, registry, ref, bundles, extras, overrides, only=args.only
            )
            # What the run READ, off the planned sources rather than off the
            # lock as found. Unscoped this is the primary's ref, so these bytes
            # are what they always were.
            #
            # Scoped, it is every ref the scope matched, RESOLVED. Both halves
            # of that matter and both were wrong: `validate_ref` accepts a
            # branch name in a source's ref, and everything that reads a source
            # resolves it first — so the raw string named a ref no part of the
            # run had looked at, and named the same source differently from the
            # way the FAILED headline names it. And `_select_sources` returns
            # every entry matching the registry, so a lock federating one
            # registry twice is scoped to two pins; naming the first and
            # calling it "the" ref is a one-of-two the reader cannot detect.
            scoped_ref = ref if include_primary else ", ".join(
                dict.fromkeys(source["ref"] for source in read))
            if not drifted:
                print(f"OK: the working tree still matches {scoped_ref} "
                      f"({len(working)} skills).")
            else:
                report_drift(drifted, ref=ref, output=output, existing=existing,
                             extras=extras, registry=registry, repo=repo,
                             working=working, primary_read=include_primary,
                             source_repos=args.source_repo or [])
                status = 1
        return status

    document = build_lock(repo, registry, ref, bundles, extras, overrides)
    if args.repin:
        # The same `_movable_bundles` question `remediation` asks about the
        # command it prints, so the report and this refusal cannot disagree
        # about which bundles a given re-pin could empty. The primary moves
        # unless this run pinned it back where the lock already had it — which
        # is exactly what the source-block remediation's `--ref` anchor does.
        _repin_shrink_guard(
            existing, document, output,
            _movable_bundles(bundles, declared,
                             primary_moves=args.ref != existing.get("ref"),
                             named=set(_parse_repin_specs(args.repin_source or []))),
            declared)
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
