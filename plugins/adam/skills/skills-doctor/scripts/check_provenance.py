#!/usr/bin/env python3
"""Where every skill in ~/.claude/skills came from — as fact where the record allows it.

The bootstrap hook writes `.skills-bootstrap-installed.json` into that directory,
one entry per skill IT installed, carrying the registry it was fetched from, the
bundle it belongs to, and the digest the bytes had at the moment the hook verified
them. That file is an exact answer to the question this skill used to infer from
file mtimes: for each directory here, did the hook put it there, or did a human?

The heuristic it replaces degrades in both directions and gives no signal that it
has. A hand-copied skill created in the same minute as an install clusters with
the install and reads as hook-owned; a hook-installed skill an editor has touched
since falls out of the cluster and reads as hand-placed. Both are silent, and a
doctor whose attribution column is silently wrong is worse than one that has none.
So: every row marked `hook` below is the writer's own account of what it wrote,
and the mtime clustering survives only as the clearly-labelled fallback for the
states where the record cannot answer — absent, or unreadable.

The record's three states are three different machines, not three shades of one.
ABSENT means the hook has never run here — correct on a durable machine, a
delivery failure on an ephemeral one. UNREADABLE means the hook could not read it
either: it pruned nothing that run and rewrote the file from scratch, so entries
from before the corruption are forgotten and anything that left the lock during
that window is now left alone forever. They take different actions, which is why
this reports them as different words rather than both as "no record".

THE CENTRAL PROPERTY, and the one every judgement below is measured against:
NO ORDINARY SESSION MAY RED. Exit 1 means "a human has to decide about this".
A state that is the correct, expected resting state of a whole class of
sessions is a NOTE however surprising it looks — an absent record on a durable
machine, a harness-seeded directory, one bare name arriving from both delivery
channels, a `__pycache__` left behind by running a skill's own suite. An exit
code that can never be green on an ordinary machine has stopped carrying
information, and its findings are the ones a reader learns to scroll past.
`test_no_ordinary_session_reddens` enumerates those sessions and asserts it.

Reports only. It never installs, copies, deletes or repairs anything.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import textwrap
from pathlib import Path
from types import MappingProxyType
from typing import (Dict, FrozenSet, List, Mapping, NamedTuple, Optional, Set,
                    Tuple)

RECORD_NAME = ".skills-bootstrap-installed.json"
LOCK_NAME = "skills.lock"
# The claude.ai account-sync channel's own directory. It is manifest-gated and is
# nobody else's to attribute, so it is excluded from the scan rather than reported
# as an unattributed skill.
ACCOUNT_DIR = "synced"

# The one difference the two delivery channels are known to have that is not a
# difference in content: account-store copies are CRLF, the registry is LF. Only
# `digest_shared_payload` folds them, and only to compare two copies of one
# skill with each other.
CRLF, LF = b"\r\n", b"\n"

# What the uploader drops on the way into the account store, mirrored from
# `_SKIP_DIRS`, `_SKIP_DIR_PREFIXES` and `_SKIP_EXTS` in sync-skills'
# `sync_skills.py`. The account copy of a skill is not the directory the registry
# holds: it is the ZIP `zip_skill` built out of it, and these never went in. A
# comparison that digests the personal directory whole therefore reads an
# ordinary build artefact as a second, divergent set of instructions — which is
# the ONE thing this reporting must not do, because the shadow it describes is
# the resting state of every cloud session here.
#
# A hand copy for `digest_skill_dir`'s reason: this file ships into a
# `~/.claude/skills` that holds no sync-skills to import from.
# `test_the_upload_filter_matches_the_uploaders` binds each set below to the
# uploader's own, so the copy cannot drift silently.
UPLOAD_SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache", ".git", ".venv",
                              "node_modules"})
UPLOAD_SKIP_DIR_PREFIXES = ("pytest-cache-files-",)
UPLOAD_SKIP_EXTS = frozenset({".pyc", ".pyo", ".b64"})

# What every sentence about "the two copies" is a statement about, said in the
# REPORT and not only in the docstrings here. Narrowing the comparison to the
# filtered set above left the sentence "The two copies are byte-identical"
# standing beside both absolute paths — false for any pair whose only
# difference is a build artefact, checkable with one `diff -r`, and printed in
# a report that may be calling the same directory edited since install a few
# lines above. The word carrying the narrowing is "instructions".
PAYLOAD_SCOPE = (
    "\"Instructions\" here means the files an upload carries: the account copy "
    "is the ZIP zip_skill built out of a directory, never the directory, so "
    "what the upload filter drops is excluded from both sides — __pycache__, "
    ".pytest_cache, .pyc, .b64 and node_modules among them. A diff -r of the "
    "two paths above can differ over those and change nothing about what the "
    "model reads.")

# What the bootstrap hook does with a directory it will not overwrite, quoted
# into every finding that has to say what happens next. `may_replace` in
# .claude/hooks/skills-bootstrap.sh returns true in exactly three cases —
# nothing is there, what IS there already digests to the digest the LOCK names,
# or the install record names it and the bytes still digest to what was
# recorded — and refuses everything else. A refusal is not a deferral: the run
# copies nothing, deletes nothing, and lists the name after `DEGRADED`.
#
# One constant rather than the same promise re-typed at each call site, and
# bound to the hook by `test_the_refusal_claim_is_the_hooks_own_may_replace`,
# which extracts `may_replace` from the hook and RUNS it against the states
# these findings describe — and by its mirror over the states the hook
# REPLACES, without which a refusal claim over a store the hook is happy with
# goes unmeasured. `REFUSAL_KINDS` below is what makes "these findings" a list
# rather than a phrase: `_observed` refuses to build a finding that quotes this
# constant under a kind not in it, and the test asserts every kind in it is
# either exercised by a real store or named as one the suite cannot reach.
# Every earlier version of these sentences said the opposite — that the
# directory is replaced, or removed, at the next session start — which is the
# one thing the hook is written never to do.
HOOK_REFUSAL = (
    "The hook does not overwrite a directory it cannot show it installed "
    "unchanged: the next session start copies nothing here, leaves these bytes "
    "exactly as they are, and names this directory after `DEGRADED` in the "
    "session's `skills:` verdict as shadowed, so the locked copy is not "
    "delivered for as long as this holds.")

# Every finding kind whose text quotes HOOK_REFUSAL, enforced by `_observed`.
# Kept because the comment above wants to say which of these the binding test
# actually runs the hook against, and a claim like that is worth nothing unless
# the set it is about is closed.
REFUSAL_KINDS = frozenset({
    "hand-placed-over-locked", "unattributable-over-locked",
    "edited-and-locked", "unmeasurable-and-locked", "artefacts-and-locked",
})

# The other side of the same rule, quoted into the note that stands where a
# refusal finding used to when the second clause DOES apply. Bound by
# `test_the_states_the_hook_replaces_carry_no_refusal`, which puts each such
# store to the extracted `may_replace` first and to the rendered report second —
# the same two halves as the refusal binding, in the direction round 4 had no
# test for at all.
THE_BYTES_THE_LOCK_NAMES = (
    "the bytes here digest to exactly the digest the lock names for this "
    "skill. That is the one state in which the hook overwrites a directory it "
    "cannot show it installed unchanged, because replacing these bytes with "
    "the bundle's copy could not change a byte anyone would observe — so "
    "delivery is unaffected and nothing here needs deciding. It stops being "
    "true the moment the lock's digest for this name moves, and the same "
    "directory becomes a refusal then.")

# WHAT THE NEXT HOOK RUN DOES TO ONE DIRECTORY THE LOCK NAMES — the closed set,
# read off `.claude/hooks/skills-bootstrap.sh`'s own control flow IN ORDER. It is
# a ladder and not a set of independent tests: the hook asks these questions one
# after another and the FIRST one to answer decides the directory's whole fate,
# so a doctor that models one rung and reports its answer as the answer is wrong
# exactly where the rungs disagree.
#
# That is not hypothetical. `may_replace` is the rung immediately after the
# whole-lock gate, and round 5 reported its "yes" as the end of the story:
# measured end to end against the real hook, two stores reached
# `bytes-are-the-locked-ones` — "delivery is unaffected and nothing here needs
# deciding", exit 0 — while the next run DELETED the directory and reported
# DEGRADED. A project collision and a lock whose rows share a destination name
# both reach an `rm -rf "${DEST:?}/$name"` that sits BELOW the gate whose answer
# was being quoted. `hook_fate` walks the whole ladder; see its docstring for
# where each rung lives and for the rungs it deliberately does not model.
#
# `hook_fate` is total over this set and RAISES rather than defaulting. A default
# arm here would mean "no rung objected", which is precisely the reading that was
# wrong — the rungs it had not asked were the ones that delete.
LOCK_REFUSED = "not-reached-the-lock-is-refused"
LEFT_UNREPLACED = "left-in-place-unreplaced"
DELETED_BY_THE_DUP_GUARD = "deleted-by-the-dup-guard"
DELETED_BY_THE_COLLISION_GUARD = "deleted-by-the-collision-guard"
COLLISION_UNMEASURED = "collision-guard-unmeasured"
REPLACED = "replaced-by-the-locked-copy"
FATES = (LOCK_REFUSED, LEFT_UNREPLACED, DELETED_BY_THE_DUP_GUARD,
         DELETED_BY_THE_COLLISION_GUARD, COLLISION_UNMEASURED, REPLACED)

# The fates under which the locked skill is still delivered out of THIS
# directory, and therefore the only ones any sentence may say "delivery is
# unaffected" about. One member, and it is a whitelist rather than a blacklist
# for the reason above: a fate added tomorrow is not delivering until somebody
# says it is.
STILL_DELIVERS = frozenset({REPLACED})

# The deleting fates and the unmeasured one, as the sentences a reader gets.
# Module-level so that both call sites in `classify` — a hook-installed directory
# and one nothing attributes to the hook — say the same thing about the same
# rung, which is the mistake the ladder exists to stop being possible.
DUP_GUARD_DELETES = (
    "two rows of this lock fold onto this one destination name. The install "
    "directory is FLAT, so `<bundle-a>/{name}` and `<bundle-b>/{name}` are one "
    "directory — the hook's lock reader stamps both rows `dup`, and the install "
    "loop removes the destination and installs NEITHER. So this directory is "
    "deleted and the skill is not delivered by any row: the verdict names it "
    "after `DEGRADED` as `lock rows share a destination name, none installed`. "
    "`scripts/generate_skills_lock.py` refuses to write such a lock, so this is "
    "a hand-edited one, or one written before that rule existed. Rename the "
    "skill directory in one of the two registries.")
COLLISION_GUARD_DELETES = (
    "the project ships `.claude/skills/{name}/SKILL.md`, and personal "
    "`~/.claude/skills` shadows the project's — so the hook DELETES this "
    "directory to let the repo-owned copy win, skips the install, and names it "
    "after `DEGRADED` as `collision(s) skipped, repo-owned wins`. Nothing is "
    "broken by that and nothing needs deciding about the project's copy; it is "
    "recorded because these bytes go away, which is not what the rest of this "
    "report would lead a reader to expect of a directory whose digest the lock "
    "names.")
COLLISION_GUARD_UNMEASURED = (
    "the project's `.claude/skills` is there and could not be listed, so "
    "whether the project ships a `{name}` of its own is UNMEASURED — and that "
    "is the one question left between this directory and the hook's copy. If it "
    "does, the next run deletes these bytes so the repo-owned copy wins; if it "
    "does not, the bundle's copy is installed over them. Reported rather than "
    "resolved, because an unreadable directory answers neither way and the "
    "answer this script would otherwise assume is the benign one. Make "
    "`.claude/skills` readable — or point --project-dir at the directory the "
    "session actually opens on — and re-run.")

# The hook's charsets, applied to the same fields on the way out of the same file.
# An entry failing any of them is one the hook SKIPS, so it is invisible to the
# pruner — counted and reported here rather than quietly parsed anyway.
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
DIGEST = re.compile(r"[0-9a-f]{64}")
CONTROL = re.compile(r"[\s\x00-\x1f\x7f]")

# Two directories written by the same `cp -R` loop land within one copy loop of
# each other; anything beyond this is a different event. Only ever used for the
# fallback, where the answer is labelled inference regardless of the gap.
MTIME_CLUSTER_GAP = 60.0

PRESENT, ABSENT, UNREADABLE = "present", "absent", "unreadable"
# A lock that parses as JSON but that the hook's reader REFUSES. It is a fourth
# state and not a shade of "unreadable": the file is legible, and the reason the
# hook gives is nameable. Judging against one produces findings whose stated
# cause never happened, because nothing was installed or removed at all.
REJECTED = "rejected"

UNCHANGED, EDITED, UNMEASURABLE = "unchanged", "edited", "unmeasurable"
# The fourth integrity reading, and it exists because the whole-directory one
# reddened an ordinary workflow. `digest_skill_dir` covers everything, which is
# what the hook compares — so running a skill's own suite from the installed
# copy at `~/.claude/skills/<skill>/scripts`, which `_walk_up`'s docstring
# blesses, drops a `__pycache__` and a `.pytest_cache` beside the scripts and
# turns the doctor permanently red with `edited-and-locked`. The finding is
# true and useless: nothing a reader would want back is at stake.
#
# The separating measurement is `digest_shared_payload`, which is
# `digest_skill_dir`'s manifest algorithm over the files an upload carries.
# Comparing it to a whole-directory digest is normally a lie (its own docstring
# says so) — but comparing EQUAL is not. Any filtered file present when the
# hook recorded the digest is inside that digest and excluded from this one, so
# equality can only hold when there were none then, none of the remaining files
# has changed, and none has been added or removed. It is an extra fact, not a
# weakened one.
ARTEFACTS_ONLY = "unchanged-but-for-build-artefacts"
# FOREIGN is the fourth origin, and it is named for what was MEASURED rather
# than for who is suspected. The measurement is that the directory's SKILL.md
# declares a `name:` other than the directory's own basename, and every channel
# modelled here keys on that basename: the lock installs to its key's last
# segment, the record names directories, the account manifest names directories.
# `scripts/check_skills.py` refuses a skill in that state outright — kind
# `name-dir-mismatch`, run on every CI push, unwaived — so no skill this
# registry ships can be one. That premise is asserted against the checkout by
# `test_no_registry_skill_could_be_read_as_foreign` rather than left as a claim
# here, so it goes red the day the lint is waived or dropped.
#
# So FOREIGN establishes "no name-keyed channel here produced this" and stops
# there. It deliberately does NOT say `seeded`, which the issue proposing this
# reached for (#123 option 1): that word names an agent — the hosted harness —
# and nothing readable from this process can show the harness rather than a
# hand copy with a typo. The same issue rejects an mtime/mode recogniser for
# exactly that over-claim, and picking its conclusion while keeping its word
# would smuggle the over-claim back in through the column heading.
HOOK, UNATTRIBUTED, UNKNOWN, FOREIGN = ("hook", "unattributed", "unknown",
                                        "foreign")

# Every origin there is. `assign_origins` is a four-arm ladder ending in an
# unconditional `else`, so every directory on disk leaves it carrying one of
# these and no directory can carry two. That totality is what the observation
# table below rests on and what the matrix test enumerates — a fifth origin
# cannot be added without appearing in both.
ORIGINS = (HOOK, FOREIGN, UNATTRIBUTED, UNKNOWN)

# Every observation this makes about a DIRECTORY, and the origins it may be
# raised about. Before this table each observation carried its own idea of
# which directories it applied to, wired pair by pair at the call sites, and
# the pairs nobody enumerated were the defects. The one that made the table:
# a directory the report itself labelled `foreign` was still fed to the shadow
# comparison, so an account store holding the same basename produced a
# `shadow-copies-differ` FINDING at exit 1 beside a note saying nothing here
# delivered the directory and there was nothing to fix.
#
# `shadow` therefore excludes FOREIGN, and says so here rather than at the call
# site. That comparison's whole remedy is "the account copy is behind,
# re-upload it", which presumes the two directories are one skill arriving
# twice. A directory whose frontmatter declares another name is not that
# skill's second copy, so no upload could reconcile the pair and the finding
# could never go green — the central property inverted. `foreign_notes`
# reports the basename collision instead.
OBSERVATION_ORIGINS: Dict[str, FrozenSet[str]] = {
    # What the lock expects of a directory that IS here.
    "lock-expectation": frozenset({HOOK, UNATTRIBUTED, UNKNOWN}),
    # The bytes against the digest the record vouches for. Only a recorded
    # directory has one to be measured against.
    "integrity": frozenset({HOOK}),
    # One bare name delivered into one session by both channels.
    "shadow": frozenset({HOOK, UNATTRIBUTED, UNKNOWN}),
    # The frontmatter-name disagreement itself.
    "foreign": frozenset({FOREIGN}),
}

# Which observation each per-directory kind belongs to. Two families are
# deliberately absent, both because they have no directory and therefore no
# origin to check against: the store-wide kinds (the record's, the lock's, the
# store's, the hook wiring's), and the kinds about a locked name that is NOT on
# disk (`missing`, `not-in-the-store`, `delivered-by-*`).
OBSERVATION_KINDS: Dict[str, Tuple[str, ...]] = {
    "lock-expectation": ("untracked", "hand-placed-over-locked",
                         "unattributable-over-locked",
                         "bytes-are-the-locked-ones",
                         DELETED_BY_THE_DUP_GUARD,
                         DELETED_BY_THE_COLLISION_GUARD,
                         COLLISION_UNMEASURED,
                         "stale-out-of-scope", "stale"),
    "integrity": ("edited-and-locked", "unmeasurable-and-locked",
                  "edited-and-stale", "unmeasurable-and-stale",
                  "artefacts-and-locked", "artefacts-and-stale"),
    "shadow": ("shadow-copies-differ", "shadowed-by-the-account-store"),
    "foreign": ("foreign",),
}

OBSERVATION_OF: Dict[str, str] = {
    kind: observation
    for observation, kinds in OBSERVATION_KINDS.items() for kind in kinds}

# Which kind of machine this is, which is what decides whether an empty personal
# store is the correct state or a delivery failure. EPHEMERAL and DURABLE are the
# two rows of the skill's own surface table; UNSURE is the third state that table
# does not have, and it exists so that a reading nobody has measured is reported
# as unmeasured rather than rounded to whichever row is convenient.
EPHEMERAL, DURABLE, UNSURE = "ephemeral", "durable", "unsure"

# The two names a USER-scope settings file can have. The binary SELECTS one of
# them by whether the surface is in Cowork plugin mode (`coworkPlugins` /
# `CLAUDE_CODE_USE_COWORK_PLUGINS`); it reads that one and does not fall back to
# the other. So this is a selection, NOT a chain, and the two rules differ in the
# direction that matters. Checking both — `any(...)` over the pair — answers
# "does either file wire a hook", which on an ordinary machine reports the user
# scope as WIRED because `cowork_settings.json` does, while the only file that
# machine consults, `settings.json`, wires nothing. That SUPPRESSES
# `hook-not-wired` on a machine whose hook genuinely cannot fire: the quiet wrong
# answer this whole check exists to eliminate, arrived at from the other side.
# `user_settings_name` applies the selection.
#
# Contrast PROJECT_SETTINGS_NAMES below, which takes the opposite rule for the
# opposite reason: ADR 0005 records the project scope as a real CHAIN, read in
# order and merged by precedence, so "either file wires it" is the right
# question there.
USER_SETTINGS_DEFAULT = "settings.json"
USER_SETTINGS_COWORK = "cowork_settings.json"

# The two files a PROJECT-scope settings chain can be called, in the order the
# chain reads them. ADR 0005 records `<cwd>/.claude/settings.local.json` AHEAD of
# `<cwd>/.claude/settings.json`, and `settings.local.json` is the GITIGNORED
# machine-local one — which is exactly where someone applies the `hook-not-wired`
# fix without committing it to a repo they may not own. Reading only
# `settings.json` therefore fired the finding at the one person who had already
# fixed it, over a file sitting one line higher in the same chain, and told them
# "nothing here or at the user scope does" while their own file did. Same defect
# class as the `cowork_settings.json` name above, one scope down — though the
# REMEDY differs, because that scope selects where this one chains.
PROJECT_SETTINGS_NAMES = ("settings.local.json", "settings.json")

# What this check CANNOT see, emitted verbatim on every `hook-not-wired`. Three
# links of the resolution chain are not files a process can open, and a finding
# that lists only the ones it read reads as exhaustive — so a reader wired
# through any of these three is told, with exit 1, that nothing wires the hook,
# and has no way to tell a real defect from this check's blind spot. Naming it in
# SKILL.md alone does not reach that reader: the person who runs the script and
# reads its output is not the person reading the skill. Kept as one constant so
# both the finding and the durable-surface note carry the same sentence.
UNREADABLE_LINKS = (
    "Three links in that chain are unreadable from here, and none of them is a "
    "file this can open: a managed/policy settings file, a --settings path "
    "given on the command line, and the `coworkPlugins` config flag that "
    "selects cowork_settings.json over settings.json at the user scope. If the "
    "hook is wired through one of those, this finding is wrong about it — that "
    "is the gap, not a defect on your machine."
)


class Entry(NamedTuple):
    name: str
    registry: str
    bundle: str
    digest: str


class Record(NamedTuple):
    """The hook's install record, as much of it as the hook itself would use.

    `skipped_names` is the names on entries the shape check REJECTED, and it is
    carried because `entries` alone cannot tell "the record is silent about this
    directory" from "the record names it in an entry the hook throws away". The
    file on disk still holds the name in the second case, so a report that says
    the record does not name it is falsifiable by opening the record the same
    report is already complaining about with `record-entries-skipped`. Only
    entries whose `name` is a string can contribute one; there is nothing to
    carry otherwise, and `skipped` counts those too.
    """
    state: str
    entries: Dict[str, Entry]
    skipped: int
    skipped_names: Set[str] = frozenset()


# The default `digests` every `Lock` built without one shares. A NamedTuple's
# default is ONE object evaluated at class-creation time, so a plain `{}` here
# hands the same dict to every lock in the session — a single
# `lock.digests.setdefault(...)` anywhere would leak across all of them, and the
# `Dict[...]` annotation said nothing about that. Nothing mutates it today; a
# read-only view is what keeps that a property of the type rather than of the
# current call sites, so the next edit fails loudly here instead of quietly
# somewhere else. `Mapping` on the field is the other half: it is the annotation
# under which `.setdefault` does not typecheck.
NO_DIGESTS: Mapping[str, FrozenSet[str]] = MappingProxyType({})


class Lock(NamedTuple):
    """A lock file's expectation, as much of it as changes what is reported.

    `digests` is the `locked` argument `may_replace` takes, and the one this
    file did not carry: a destination name maps to the digests the lock itself
    names for it, and a directory whose bytes are one of them is a
    directory the hook overwrites no matter who put it there. A SET per name
    because `names` folds `bundle/skill` keys to their last segment — two keys
    can land on one directory, and the hook asks `may_replace` once per key.

    `duplicates` is the destination names TWO OR MORE lock keys fold onto. The
    install dir is flat, so `adam/alpha` and `fastmail/alpha` are one directory:
    the hook's lock reader marks both rows `dup` and the install loop removes the
    destination and installs neither. A name here is one no lock row can deliver,
    whatever else is true of it.
    """
    state: str
    names: Set[str]
    claims: Set[Tuple[str, str]]
    reason: Optional[str] = None
    digests: Mapping[str, FrozenSet[str]] = NO_DIGESTS
    duplicates: FrozenSet[str] = frozenset()


class Surface(NamedTuple):
    """What kind of machine this is, and the three readings that decided it.

    `forced` is carried rather than folded away because the verdict PRINTS the
    inputs it judged from: an ephemeral reading with an unset entrypoint and no
    session id looks like a contradiction unless the third arm is named.
    """
    kind: str
    entrypoint: str
    remote: str
    forced: bool = False


class Origin(NamedTuple):
    """One directory's origin, and the measurement that chose it.

    `declared` is the frontmatter name when it disagrees with the basename, and
    None otherwise — carried so that the row, the note and the finding are views
    of ONE reading rather than readings that can disagree about one directory.

    Carried for UNATTRIBUTED as well as for FOREIGN, because the two arms are
    separated by things that have nothing to do with the frontmatter: a lock
    naming the basename, or a record entry naming it that the hook's shape check
    rejected. Without it, `untracked` could only GUESS at why no `foreign` note
    accompanied it, and it guessed wrong — it told the reader the SKILL.md
    agreed with the basename in exactly the two states where it does not.
    """
    kind: str
    declared: Optional[str] = None


class Row(NamedTuple):
    name: str
    origin: str
    registry: Optional[str]
    bundle: Optional[str]
    integrity: Optional[str]
    in_lock: bool


class Finding(NamedTuple):
    kind: str
    subject: str
    detail: str
    # Which lock produced it, once there is more than one to produce it. A
    # multi-repo session judges the one store against several declared
    # expectations, and "alpha is missing" is not a statement anyone can act on
    # without knowing which repo declared alpha. None means store-wide — the
    # record, the store itself, the hook wiring — and prints nothing extra.
    lock: Optional[str] = None


class LockResult(NamedTuple):
    """One declared expectation, and everything judged against it.

    The store is scanned once and judged once per lock, rather than the locks
    being merged into one expectation first. Merging would be an answer to
    "which lock wins in a multi-repo session", which is an open policy question
    (see docs/decisions/0005) and not one a diagnostic gets to settle by being
    convenient. Reporting per lock needs no winner: every sentence stays
    attributable to the repo that declared it.
    """
    path: Path
    lock: Lock
    rows: List[Row]
    findings: List[Finding]
    notes: List[Finding]


def _observed(kind: str, origin: str, subject: str, detail: str) -> Finding:
    """A per-directory finding or note, checked against the observation table.

    A raise and not a filter, because both failures it catches are programming
    errors rather than states of anyone's machine: a kind nobody registered in
    `OBSERVATION_KINDS`, or a kind raised about an origin its observation does
    not cover. Deciding those pairs at the call sites is what let a note and a
    finding disagree about one directory, and a table nothing consults would
    drift the same way — so every per-directory finding is built through here.
    """
    observation = OBSERVATION_OF.get(kind)
    if observation is None:
        raise KeyError(f"{kind!r} is not registered in OBSERVATION_KINDS")
    if origin not in OBSERVATION_ORIGINS[observation]:
        raise ValueError(f"{kind!r} belongs to the {observation!r} observation, "
                         f"which is not raised about a {origin!r} directory")
    if HOOK_REFUSAL in detail and kind not in REFUSAL_KINDS:
        raise ValueError(f"{kind!r} quotes HOOK_REFUSAL and is not in "
                         f"REFUSAL_KINDS, so the binding test's claim to cover "
                         f"the states these findings describe no longer holds")
    return Finding(kind, subject, detail)


def digest_skill_dir(path: Path) -> Optional[str]:
    """The sha256 of a skill directory: sha256 over `<relpath>\\0<sha256>\\n` lines.

    A THIRD copy of the algorithm that `digest_skill_dir` in
    `scripts/generate_skills_lock.py` specifies and `digest_dir` in the bootstrap
    hook already mirrors, and it exists for the same reason the hook's does: this
    file ships inside the skill, into a `~/.claude/skills` where the registry's
    `scripts/` are not present, so it cannot import the original. It is therefore
    not independent — it mirrors the generator line for line, and
    `test_the_digest_matches_the_generators` binds the two. Change one, change
    all three.

    Returns None when the bytes cannot be read, which is NOT the same answer as a
    digest that differs: reporting "edited" for a directory nobody could measure
    would be a guess dressed as a measurement.
    """
    root = Path(path)
    try:
        if not root.is_dir():
            return None
        entries = []
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue  # directories carry no bytes; broken symlinks carry none either
            entries.append((candidate.relative_to(root).as_posix(), candidate))
        manifest = "".join(
            f"{relpath}\0{hashlib.sha256(file_path.read_bytes()).hexdigest()}\n"
            for relpath, file_path in sorted(entries, key=lambda entry: entry[0])
        )
    except OSError:
        return None
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def uploaded_files(root: Path) -> Optional[List[Tuple[str, Path]]]:
    """(relpath, path) for every file the ACCOUNT channel would have carried.

    The uploader's own selection rule, applied to a directory here so that a
    personal copy and an account copy can be compared over the same set of
    files. Sorted by relpath, so a caller can fold it straight into a manifest.

    Returns None when the directory cannot be walked, which is `digest_skill_dir`'s
    contract and for its reason: "not measured" is a different answer from "no
    files".
    """
    try:
        if not root.is_dir():
            return None
        found: List[Tuple[str, Path]] = []
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue  # directories carry no bytes; broken symlinks carry none
            relpath = candidate.relative_to(root)
            parts = relpath.parts
            if any(part in UPLOAD_SKIP_DIRS for part in parts):
                continue
            if any(part.startswith(prefix)
                   for part in parts for prefix in UPLOAD_SKIP_DIR_PREFIXES):
                continue
            if candidate.suffix in UPLOAD_SKIP_EXTS:
                continue
            found.append((relpath.as_posix(), candidate))
        found.sort(key=lambda entry: entry[0])
    except OSError:
        return None
    return found


def dropped_files(skill_dir: Path) -> List[str]:
    """Relpaths in `skill_dir` that `uploaded_files` would NOT carry.

    So that a finding about build artefacts can name the directory's own extra
    files instead of the three that are usually the cause. The old sentence
    listed "a __pycache__, a .pytest_cache, a .pyc" unconditionally, and printed
    that list over a directory whose only extra was a node_modules/.

    Empty is a failure answer as much as a real one — an unwalkable directory
    gives [] rather than raising — and callers phrase around it rather than
    printing "()". Not because either of them has been seen empty: both are
    guarded by ARTEFACTS_ONLY, which needs the uploaded-file digest to have
    succeeded AND the whole-directory digest to differ from it, and that
    difference IS a dropped file. What the guard is actually for is the gap
    between the two: the classification reads the directory, this re-reads it,
    and a build that finishes in between can empty the list under a finding
    already decided on.
    """
    kept = uploaded_files(skill_dir)
    if kept is None:
        return []
    keep = {relpath for relpath, _ in kept}
    try:
        return sorted(
            child.relative_to(skill_dir).as_posix()
            for child in skill_dir.rglob("*")
            if child.is_file()
            and child.relative_to(skill_dir).as_posix() not in keep)
    except OSError:
        return []


def name_list(items: List[str], cap: int = 3) -> str:
    """`items` as a short backticked list, capped so a finding stays readable."""
    shown = [f"`{item}`" for item in items[:cap]]
    rest = len(items) - len(shown)
    return ", ".join(shown) + (f", and {rest} more" if rest > 0 else "")


def digest_shared_payload(path: Path, fold: bool = False) -> Optional[str]:
    """`digest_skill_dir`'s manifest shape over the files BOTH channels carry.

    For COMPARING two copies of one skill with each other — and for exactly one
    thing besides, which used to be forbidden here in capitals while the code
    below did it. `classify`'s ARTEFACTS_ONLY test asks whether this equals the
    RECORDED digest, and it is not the naive comparison the prohibition was
    aimed at. Both functions emit the same `<relpath>\0<sha256>\n` manifest, so
    the equality can only hold when the uploaded subset of the files here IS the
    whole set of files that were here at install — which, taken with
    `digest_skill_dir` differing from that same recorded digest, says the
    difference is entirely files the upload filter drops. That is a measurement,
    not the lie `_cause` exists to avoid telling.

    What stays forbidden is using this ALONE as a verdict on whether a directory
    is unchanged: on its own it cannot see a dropped file, so it would call an
    edited copy untouched. The recorded-digest comparison above is safe because
    the whole-directory one is asked FIRST and has already said no.

    Two things separate it from `digest_skill_dir`, and both exist because the
    account copy is not a directory anyone copied — it is the ZIP `zip_skill`
    uploaded:

    * It digests only `uploaded_files`. A `__pycache__` beside a skill's scripts
      is in the personal copy and was never in the account one, and calling that
      a divergence reddens a session where nothing is wrong.
    * `fold=True` folds CRLF to LF, because the two channels disagree about line
      endings and, so far, about nothing else that survives the filter above.
      Account-store copies are CRLF where the registry is LF, which this skill's
      own account-drift procedure already works around by piping both sides
      through `tr -d '\r'` before diffing. Comparing exact bytes alone marks
      every account copy as differing from every personal one — a signal that
      fires on all of them and therefore says nothing about any of them.

    The `fold` flag is safe here in a way it would not be on `digest_skill_dir`.
    That one is held to the generator's algorithm by
    `test_the_digest_matches_the_generators`, and a parameter that changes what
    it hashes is a way for the copy to drift while the binding still passes. This
    function is bound to nothing outside this file; its answer is only ever used
    as "do these two directories match", never as an identity for anything.

    Byte-level substitution, applied to every file including binary ones. Safe
    for the same reason: a normalisation that collided for two payloads would
    have to collide across both copies, and the unfolded digests are compared
    first anyway.
    """
    entries = uploaded_files(Path(path))
    if entries is None:
        return None
    try:
        manifest = "".join(
            f"{relpath}\0"
            f"{hashlib.sha256(_folded(file_path.read_bytes(), fold)).hexdigest()}\n"
            for relpath, file_path in entries
        )
    except OSError:
        return None
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def _folded(data: bytes, fold: bool) -> bytes:
    return data.replace(CRLF, LF) if fold else data


def carries_crlf(path: Path) -> Optional[bool]:
    """Does any file both channels carry hold a CRLF? None if unreadable.

    Only so that a note about line endings can say WHICH copy has them rather
    than assuming. Folding CRLF reconciled the pair is evidence that they
    disagree about line endings; it is not evidence about the direction, and a
    reader who checks the bytes is entitled to find the sentence true.
    """
    entries = uploaded_files(Path(path))
    if entries is None:
        return None
    try:
        return any(CRLF in file_path.read_bytes() for _, file_path in entries)
    except OSError:
        return None


def remote_url(registry: object) -> Optional[str]:
    """The git remote URL a lock's `registry` field stands for, or None.

    Mirrors `remote_url` in the hook, and it is load-bearing rather than cosmetic:
    the record stores this RESOLVED form (`https://github.com/OWNER/REPO.git`)
    while the lock states the slug (`OWNER/REPO`), so a doctor that compares the
    two with `==` finds nothing equal and reports every hook-installed skill as
    coming from a registry its own lock does not declare. None means a shape the
    hook would have refused outright; here it just fails to match anything.
    """
    if not isinstance(registry, str) or not registry:
        return None
    if "://" in registry:
        return registry
    if re.fullmatch(NAME.pattern + "/" + NAME.pattern, registry):
        return "https://github.com/%s.git" % registry
    return None


def read_surface(env: Optional[Dict[str, str]] = None) -> Surface:
    """Which kind of machine this is, decided by the hook's own three arms.

    The whole point of asking is that an empty personal store means opposite
    things on the two kinds of machine. On a durable one the marketplace install
    is authoritative and the store is SUPPOSED to hold no bundle skills; on an
    ephemeral one the bootstrap hook is the only channel there is, so the same
    empty store is a delivery failure. Judging both the same way is how the
    doctor ended up reporting "healthy" in precisely the session where it was not
    (#85).

    THE THREE ARMS ARE COPIED FROM `.claude/hooks/skills-bootstrap.sh`, which
    installs when a remote session id is set, OR `CLAUDE_CODE_ENTRYPOINT` is
    EXACTLY `remote`, OR `SKILLS_BOOTSTRAP_FORCE` is set, and skips otherwise.
    Reading a narrower test than the hook it diagnoses is not caution, it is
    disagreement — and it is silent, because the narrower reading returns
    `unsure`/`durable`, which is the quiet answer. Measured: on a surface the
    hook treats as ephemeral and installs onto, a doctor keyed on the session id
    alone reported `surface unsure`, withheld every promotion and exited 0 over
    eight undelivered locked skills. That is #85's headline defect surviving on a
    surface the hook itself installs on.

    WHAT IS NOT COPIED, AND MUST NOT BE: any widening to the six `remote_*`
    spellings. A prefix match on `remote` is the fix that looks equivalent and is
    held deliberately (#85 §5) — the binary's own display classifier groups
    `remote_cowork` with `local-agent`, so "no durable entrypoint starts with
    `remote`" is unproven, and assuming it would call a durable Cowork machine
    ephemeral and report its correctly-empty store as a delivery failure. The
    EXACT value `remote` is a different question, already settled in this repo's
    own hook, so matching it is agreement rather than a widening. `remote_cowork`
    stays UNSURE.

    Anything else — an entrypoint with no session id — is UNSURE rather than
    durable. It is treated as durable everywhere a judgement depends on it,
    because the conservative direction is to keep a note a note; it is PRINTED
    as unsure so the reader is not told a fact nobody measured.
    """
    env = os.environ if env is None else env
    entrypoint = env.get("CLAUDE_CODE_ENTRYPOINT", "") or ""
    remote = env.get("CLAUDE_CODE_REMOTE_SESSION_ID", "") or ""
    # PRESENCE, not value, and that is the hook's semantics rather than a
    # shortcut: it tests `[ -z "${SKILLS_BOOTSTRAP_FORCE:-}" ]`, so
    # `SKILLS_BOOTSTRAP_FORCE=0` forces the install too. Reading the value here
    # would disagree with the hook in the one direction nobody thinks to check.
    forced = bool(env.get("SKILLS_BOOTSTRAP_FORCE", ""))
    if remote or entrypoint == "remote" or forced:
        return Surface(EPHEMERAL, entrypoint, remote, forced)
    if not entrypoint:
        return Surface(DURABLE, entrypoint, remote, forced)
    return Surface(UNSURE, entrypoint, remote, forced)


def when_the_hook_runs(surface: str) -> str:
    """What a sentence about the NEXT run has to add, given this surface.

    Every "the next session start …" clause in this file is a claim about a hook
    that may never execute. `.claude/hooks/skills-bootstrap.sh` tests the surface
    BEFORE it reads the lock — no remote session id, entrypoint not exactly
    `remote`, `SKILLS_BOOTSTRAP_FORCE` unset — and on a machine failing all three
    it prints `skills: skipped — durable session …, marketplace install is
    authoritative` and returns. Nothing is installed, nothing is refused, no
    `skills:` verdict exists to read `DEGRADED`.

    Printing the surface in its own block further up is not the fix: that is
    what the report already did while a finding below it promised a run. The
    caveat goes on the sentence that needs it.

    Empty on EPHEMERAL, where those clauses are true exactly as written. UNSURE
    gets its own arm rather than being folded into either: `read_surface` treats
    it as durable for judgements, and saying "this machine reads durable" about
    a machine nobody could classify is the kind of unmeasured fact this file
    exists not to print.
    """
    if surface == EPHEMERAL:
        return ""
    if surface == DURABLE:
        return (" None of that happens on THIS machine: the SURFACE block above "
                "reads durable, where the hook returns `skills: skipped` before "
                "it reads the lock — so nothing here is installed, removed or "
                "refused, and no session-start verdict is emitted to degrade. "
                "What is described is what an ephemeral session on these bytes "
                "would do.")
    return (" Whether any of that happens here is unmeasured: the SURFACE block "
            "above reads unsure, and the hook returns `skills: skipped` before "
            "it reads the lock on any session it does not take for a remote "
            "one. Those sentences hold on an ephemeral surface and are not "
            "known to hold on this one.")


def lock_names_the_bytes(lock: Lock, name: str,
                         measured: Optional[str]) -> bool:
    """`may_replace`'s second clause: are these bytes what the lock names?

    The hook overwrites a directory in three cases and this is the middle one —
    `[ "$have" = "$locked" ]`, tested BEFORE the install record is consulted at
    all. It is the clause that makes provenance irrelevant: whoever put the
    directory there, if what is there already digests to the digest the lock
    names, replacing it could not change a byte anyone could observe, so the
    hook does it and delivery proceeds.

    `measured is None` is `digest_skill_dir`'s "could not read", and it answers
    False for the hook's own reason: `may_replace` refuses outright when
    `digest_dir` prints nothing, because two empty strings would otherwise
    compare equal and hand back a directory nothing was ever measured against.

    Bound to the hook by `test_the_refusal_claim_is_the_hooks_own_may_replace`
    and its REPLACE control, which run the extracted `may_replace` over the same
    stores these findings are rendered from.
    """
    return measured is not None and measured in lock.digests.get(name, frozenset())


def hook_fate(lock: Lock, name: str, *, replaceable: bool,
              repo_owned: Optional[Set[str]]) -> str:
    """What the next hook run does to `~/.claude/skills/<name>`, as one of `FATES`.

    The install loop's own ladder, in the hook's order, and TOTAL: every path
    ends in a named fate and the paths that cannot be named raise. See the
    `FATES` comment for why a default arm is the specific thing being avoided.

    The rungs this models, in `.claude/hooks/skills-bootstrap.sh`'s order:

      * the whole-lock gate, which exits inside the lock reader before the
        install loop exists. Nothing is installed and nothing is removed;
      * `may_replace` — false means the directory is left exactly as it is and
        named after `DEGRADED` as shadowed. THIS IS THE RUNG ROUND 5 MODELLED,
        and everything below it is what it reported nothing about;
      * the `dup` guard — `rm -rf "${DEST:?}/$name"`, then the row is skipped.
        Both rows sharing the destination are stamped, so neither is installed;
      * the collision guard — the project ships `.claude/skills/<name>/SKILL.md`,
        so `rm -rf "${DEST:?}/$name"` and repo-owned wins;
      * the copy itself.

    NOT MODELLED, and named here rather than silently folded into the copy: the
    source-index framing check, an unreachable source, the skill being absent at
    the pinned ref, the `cp` failing, and the post-copy digest mismatch. Every
    one of them ALSO removes the destination — so `REPLACED` is not a promise
    that the bytes survive, it is "the ladder's local rungs are all clear and the
    run reaches the copy". None of the five is a property of this store: they are
    properties of a framing bug, a network, a remote tree and a filesystem write,
    and no measurement of a reader's own disk predicts any of them. The rungs
    above ARE decided by that disk, which is what makes them the doctor's
    business and these five somebody else's.

    `replaceable` is the CALLER's answer to the `may_replace` rung, because the
    two call sites have different evidence for it: a hook-origin directory can
    satisfy `may_replace`'s record clause, and a directory nothing attributes to
    the hook can only satisfy the lock clause. Passing the answer in keeps one
    ladder rather than two copies of it that can disagree.

    `repo_owned` is `None` when the project's skills directory is there and could
    not be listed. That is the collision rung with no answer, and it gets a fate
    of its own instead of the empty set's: an unreadable directory is not an
    empty one, and rounding it to "no collision" is how the benign note this
    ladder exists to gate would come back through the one input nobody measured.
    """
    if lock.state in (REJECTED, UNREADABLE):
        # Rung zero, and it is answered for every name at once: the reader exits
        # before the loop, so there is no per-name question left to ask and
        # `lock.names` is empty for the same reason.
        return LOCK_REFUSED
    if lock.state != PRESENT:
        raise ValueError(
            f"a {lock.state} lock is not one the hook refuses — there is no "
            f"lock for it to refuse — so {name!r} has no fate on this ladder; "
            f"callers guard on the lock being PRESENT before asking")
    if name not in lock.names:
        raise ValueError(
            f"{name!r} is not a destination this lock names, so the install "
            f"loop never visits it and it has no fate on this ladder")
    if not replaceable:
        return LEFT_UNREPLACED
    if name in lock.duplicates:
        return DELETED_BY_THE_DUP_GUARD
    if repo_owned is None:
        return COLLISION_UNMEASURED
    if name in repo_owned:
        return DELETED_BY_THE_COLLISION_GUARD
    return REPLACED


def discover_locks(explicit: Optional[str], project_dir: Path) -> List[Path]:
    """Every lock to judge against, and where a multi-repo session hides them.

    An explicit `--lock` is taken exactly as given: a caller who names a file has
    said which expectation they mean, and quietly scanning for others would judge
    their store against locks they did not ask about.

    Otherwise the project's own `skills.lock` answers when it exists. When it
    does not, the reason is usually not "this machine has no expectation" but
    "the project dir is the PARENT of several repos" — the shape a multi-repo
    session actually has, where every lock sits one level down. Resolving only
    the bare default there reported the absence of a lock as though it were the
    absence of a problem, and exited 0 over nine undelivered skills.

    One level only, and directories only. Recursing would sweep in vendored
    checkouts and `node_modules`, and a lock found four levels down is not one
    any session was started against.

    Falls back to the project's own path when nothing is found, so a machine that
    genuinely has no lock still reports `absent` rather than reporting nothing.
    An absent lock is not a finding: it is a machine this cannot verdict on.
    """
    if explicit is not None:
        return [Path(explicit).expanduser()]
    own = project_dir / LOCK_NAME
    if own.is_file():
        return [own]
    try:
        children = sorted(project_dir.iterdir())
    except OSError:
        return [own]
    found = [child / LOCK_NAME for child in children
             if child.is_dir() and (child / LOCK_NAME).is_file()]
    return found or [own]


def wires_session_start(path: Path) -> bool:
    """Whether this settings file declares a SessionStart hook command.

    Parsed as JSON rather than grepped: `"SessionStart"` appears in a settings
    file that mentions it in a disabled block, in a comment-shaped key, or in
    some unrelated string, and a line scan cannot tell any of those from a wired
    hook. The question is structural, so the answer comes from the parser.

    Deliberately shallow about the command itself — any entry carrying a
    non-empty `command` counts. Whether that command WORKS is not knowable from
    here, and the failure this exists to name (#84) is a hook that is never
    consulted at all, whatever its command string.
    """
    try:
        settings = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(settings, dict):
        return False
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    matchers = hooks.get("SessionStart")
    if not isinstance(matchers, list):
        return False
    for matcher in matchers:
        if not isinstance(matcher, dict):
            continue
        entries = matcher.get("hooks")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("command"), str) \
                    and entry["command"].strip():
                return True
    return False


def _settings_wired(claude_dir: Path, names: Tuple[str, ...] = ("settings.json",)
                    ) -> bool:
    """Whether any of `names` under `claude_dir` wires a SessionStart hook.

    For a CHAIN only — a set of files all of which are read and merged by
    precedence, which is what ADR 0005 records the project scope to be. The user
    scope is a SELECTION and must not come through here; see
    `user_settings_name` for why "either one wires it" is the wrong question
    there, and which direction the wrong answer points.
    """
    return any(wires_session_start(claude_dir / name) for name in names)


def user_settings_name(env: Optional[Dict[str, str]] = None) -> str:
    """The ONE user-scope settings file this surface's binary consults.

    Selection, not merge. A Cowork surface reads `cowork_settings.json` and an
    ordinary one reads `settings.json`; neither falls back to the other. So the
    two names cannot both be consulted, and treating them as a chain reports the
    user scope as wired off a file the machine in front of you never opens —
    suppressing `hook-not-wired` exactly where the hook cannot fire.

    `CLAUDE_CODE_USE_COWORK_PLUGINS` is the arm a process can read, and a
    non-empty value is taken as on. That truthiness rule is NOT copied from a
    source the way `read_surface`'s is: this repo's own hook is where
    `SKILLS_BOOTSTRAP_FORCE`'s presence-not-value semantics were read off, and
    there is no equivalent source here, so an exported empty string reads as off
    on a convention rather than on a measurement.

    The OTHER arm, `coworkPlugins`, is a config flag and not an environment
    variable — nothing here can see it. A machine in Cowork mode by that route
    reads as ordinary, which points `hook-not-wired` the FALSE-POSITIVE way
    rather than the suppressing way, and is disclosed on the finding itself
    alongside the other two links this cannot open.
    """
    env = os.environ if env is None else env
    if (env.get("CLAUDE_CODE_USE_COWORK_PLUGINS", "") or "").strip():
        return USER_SETTINGS_COWORK
    return USER_SETTINGS_DEFAULT


def hook_wiring(project_dir: Path, home: Optional[Path] = None,
                env: Optional[Dict[str, str]] = None
                ) -> Tuple[bool, bool, List[Path]]:
    """(wired at the project, wired for the user, children that wire it instead).

    Claude Code resolves hooks from the settings chain rooted at `cwd` and at
    `$HOME` — never from an `--add-dir` grant, whose directories contribute
    skills, commands, agents and CLAUDE.md but no hooks and no settings. So in a
    session whose project dir is the parent of several repos, each repo's
    `.claude/settings.json` is enumerated and none of them is CONSULTED, and no
    repo's SessionStart hook can fire whatever its command string says.

    That state is invisible from inside any one repo — every file it needs is
    present and correct — which is why it is worth a finding rather than a
    comment. See docs/decisions/0005.

    NEITHER scope is a single filename, and the two are not the same shape. The
    project scope is a CHAIN — `settings.local.json` then `settings.json`, both
    read and merged by precedence — so either of them wiring the hook is enough,
    and `settings.local.json` is the likelier to carry the fix, being the
    gitignored file you reach for in a repo you would rather not commit to. The
    user scope is a SELECTION: one name, chosen by Cowork mode, and the other
    name is not consulted at all. Reading the project scope too narrowly reports
    `hook-not-wired` at a machine where the fix is already applied; reading the
    user scope as a chain does the opposite and SUPPRESSES the finding at a
    machine whose hook cannot fire. See `user_settings_name`.

    The child scan reads the same PROJECT chain each child would read if it were
    the cwd, and counts the first name in it that wires — one path per repo, so a
    child wiring only through `settings.local.json` is counted rather than
    silently dropped.

    What remains genuinely unreadable from here is named on the finding, by
    `hook_findings`, and not only in prose no reader of the output ever sees.
    """
    home = Path.home() if home is None else home
    here = _settings_wired(project_dir / ".claude", PROJECT_SETTINGS_NAMES)
    user = wires_session_start(home / ".claude" / user_settings_name(env))
    children: List[Path] = []
    try:
        candidates = sorted(project_dir.iterdir())
    except OSError:
        candidates = []
    for child in candidates:
        if not child.is_dir():
            continue
        for name in PROJECT_SETTINGS_NAMES:
            settings = child / ".claude" / name
            if wires_session_start(settings):
                children.append(settings)
                break
    return here, user, children


def read_record(path: Path) -> Record:
    """Parse the hook's install record, distinguishing absent from unreadable.

    The acceptance rules are the hook's planner's, deliberately: a doctor that
    accepts an entry the pruner rejects would report a skill as hook-owned and
    removable when the hook will in fact leave it forever.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except FileNotFoundError:
        return Record(ABSENT, {}, 0)
    except OSError:
        # Any other OSError may be about the ENVIRONMENT rather than the file:
        # with the process out of file descriptors, `open` fails before the path
        # is resolved, and a record that simply does not exist was reported as
        # one that is corrupt — asserting "the file is there" about a file that
        # is not, and prescribing a clean session for a machine where the hook
        # has never run. That is the exact conflation this script exists to end,
        # so it is re-checked with `stat`, which needs no descriptor.
        return Record(ABSENT if not path.exists() else UNREADABLE, {}, 0)
    except ValueError:
        return Record(UNREADABLE, {}, 0)
    if not isinstance(record, dict) or not isinstance(record.get("installed"), list):
        return Record(UNREADABLE, {}, 0)

    entries: Dict[str, Entry] = {}
    skipped = 0
    skipped_names: Set[str] = set()
    for raw in record["installed"]:
        entry = _entry(raw)
        if entry is None:
            skipped += 1
            if isinstance(raw, dict) and isinstance(raw.get("name"), str):
                skipped_names.add(raw["name"])
            continue
        entries[entry.name] = entry
    return Record(PRESENT, entries, skipped, skipped_names)


def _entry(raw: object) -> Optional[Entry]:
    """One record entry, or None if the hook's planner would skip it."""
    if not isinstance(raw, dict):
        return None
    name, registry = raw.get("name"), raw.get("registry")
    bundle, digest = raw.get("bundle"), raw.get("digest")
    if not all(isinstance(field, str) for field in (name, registry, bundle, digest)):
        return None
    if not NAME.fullmatch(name) or not NAME.fullmatch(bundle):
        return None
    if not DIGEST.fullmatch(digest) or CONTROL.search(registry):
        return None
    return Entry(name, registry, bundle, digest)


def read_lock(path: Path) -> Lock:
    """The destination names a lock declares, their digests, and its claims.

    `claims` is what decides whether a stale skill gets removed or kept: the hook
    removes only within the pairs its own lock declares, so that two repos sharing
    one ~/.claude/skills do not reap each other's installs.

    `digests` is what decides whether a directory nothing attributes to the hook
    gets overwritten anyway — `may_replace`'s second clause. See `Lock`.

    THE WHOLE-LOCK GATE COMES FIRST, and every check below that can return
    REJECTED is part of it. The hook validates the entire lock and `sys.exit`s
    BEFORE the install loop exists, so a lock failing one of these is not a lock
    with one bad row: it is a lock that installs NOTHING, on every session start,
    while `$LEFT_IN_PLACE` keeps whatever the store already had. Anything this
    function reported about such a lock — a name declared, a digest expected, a
    skill "missing" — would describe a run that does not happen.

    That is why a bad row is REJECTED here and not skipped. Round 5 parsed each
    digest and `continue`d past a malformed one, then returned PRESENT and let
    the whole report proceed: measured end to end, one digest hand-edited to
    `"not-a-digest"` gave `skills: DEGRADED — could not read …/skills.lock` at
    every session start and an empty store, and the doctor answered exit 0,
    `FINDINGS (0)`, `2 skill(s) declared`. Skipping the row is the one answer
    that cannot be right, because the row is not what the hook refused.

    THE ORDER IS THE HOOK'S OWN, so the reason this reports is the reason the
    hook's `$LOG` carries: `'skills'` shape, then routing, then a pass over
    `sorted(skills)` doing key shape, digest, the `synced` refusal and the
    unclaimed bundle. A lock failing two of them is refused for the first,
    exactly as the hook exits on the first.

    Still a SUBSET, and deliberately: `ref`, `layout`, the `sources` cap and a
    source's unknown keys are refusals this does not model, so a lock carrying
    only one of those still reads PRESENT here while the hook refuses it. Each
    of those is a field this script never judges anything against, so modelling
    it would add a second copy of the hook's parser without changing a sentence.
    `test_a_lock_this_rejects_is_one_the_hook_rejects_too` binds the direction
    that can lie — everything called REJECTED here is refused by the real hook —
    and the skill's known limitations carry the other.
    """
    try:
        lock = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Lock(ABSENT, set(), set())
    except (OSError, ValueError):
        return Lock(UNREADABLE, set(), set())
    if not isinstance(lock, dict):
        return Lock(UNREADABLE, set(), set())

    # `lock.get("skills") or {}` is the hook's own line: absent, null and empty
    # are all "no skills", and only a truthy non-object is the error.
    skills = lock.get("skills") or {}
    if not isinstance(skills, dict):
        return Lock(REJECTED, set(), set(),
                    "'skills' must be an object")

    # The subset of the hook's lock validation that changes what this reports.
    # Not the whole of it — but a lock failing any of these is one the hook
    # REFUSES outright, so every finding derived from it would name a cause that
    # never happened while the real defect (nothing is delivered at all) went
    # unreported. Routing is TOTAL there: a bundle claimed by nobody, or by two
    # sources, is an error rather than something resolved by a default.
    claims: Set[Tuple[str, str]] = set()
    owner: Dict[str, object] = {}
    for position, (registry, bundles) in enumerate(_sources(lock)):
        url = remote_url(registry)
        if url is None:
            return Lock(REJECTED, set(), set(),
                        "a source's 'registry' is not OWNER/REPO or an "
                        "https:// / file:// URL")
        if not isinstance(bundles, list) or not bundles or not all(
                isinstance(bundle, str) and NAME.fullmatch(bundle)
                for bundle in bundles):
            return Lock(REJECTED, set(), set(),
                        "'bundles' must be a non-empty list of bundle names — it "
                        "is what says which bundles come from that registry")
        for bundle in bundles:
            if owner.setdefault(bundle, position) != position:
                return Lock(REJECTED, set(), set(),
                            f"bundle {bundle!r} is claimed by two sources; a "
                            f"bundle has one registry and one layout")
            claims.add((url, bundle))

    # `sorted(skills)`, the hook's own iteration order, so that a lock with two
    # bad rows is refused for the row the hook's log names.
    names: Set[str] = set()
    digests: Dict[str, Set[str]] = {}
    landings: Dict[str, int] = {}
    for key in sorted(skills):
        if not isinstance(key, str) or not re.fullmatch(
                NAME.pattern + "/" + NAME.pattern, key):
            return Lock(REJECTED, set(), set(),
                        f"skill key {key!r} is not '<bundle>/<skill>'")
        bundle, name = key.split("/", 1)
        # BOTH shapes, normalised to bare hex exactly as the hook normalises
        # before it writes `skills.nul` — `digest_dir`'s answer, which
        # `may_replace` compares against, is always bare hex. Neither shape is
        # the whole-lock refusal the round-5 comment here called a skipped row.
        digest = skills[key]
        matched = (re.fullmatch(r"(?:sha256:)?([0-9a-f]{64})", digest)
                   if isinstance(digest, str) else None)
        if matched is None:
            return Lock(REJECTED, set(), set(),
                        f"skill {key!r} has no sha256 digest")
        if name == ACCOUNT_DIR:
            return Lock(REJECTED, set(), set(),
                        f"skill {key!r} would install over ~/.claude/skills/"
                        f"{ACCOUNT_DIR}, the claude.ai account-sync directory, "
                        f"which this hook never installs into and is not its to "
                        f"replace or delete")
        if bundle not in owner:
            return Lock(REJECTED, set(), set(),
                        f"skill {key!r} names a bundle no source claims")
        # The destination is the key's LAST segment, the same reading the hook
        # installs by — `adam/foo` and `other/foo` are one directory, not two.
        names.add(name)
        digests.setdefault(name, set()).add(matched.group(1))
        # Counted per KEY, not per name: the hook's `seen[row[4]] > 1` over the
        # rows it is about to write, which is what stamps both rows `dup`.
        landings[name] = landings.get(name, 0) + 1

    return Lock(PRESENT, names, claims,
                digests={name: frozenset(values)
                         for name, values in digests.items()},
                duplicates=frozenset(name for name, count in landings.items()
                                     if count > 1))


def _sources(lock: dict) -> List[Tuple[object, object]]:
    """(registry, bundles) for the primary and each federated source, in order."""
    sources = [(lock.get("registry"), lock.get("bundles"))]
    extra = lock.get("sources")
    if isinstance(extra, list):
        sources += [(source.get("registry"), source.get("bundles"))
                    for source in extra if isinstance(source, dict)]
    return sources


def scan(skills_dir: Path) -> Tuple[str, List[str]]:
    """(state, skill directories) for the personal store.

    Everything but the account store and dotfiles: the record itself is a dotfile
    in here, as is its staging file mid-write, so the dot rule is what keeps this
    from reporting the record as a skill.

    The state is returned rather than folded into an empty list because a store
    that could not be READ is not an empty one, and the difference is the same
    one this whole script exists to make about the record. Swallowing it printed
    a clean all-clear about a path that does not exist, and — with a lock — an
    assertion that every locked skill was absent from a disk nobody read.
    """
    try:
        children = sorted(skills_dir.iterdir())
    except FileNotFoundError:
        return ABSENT, []
    except OSError:
        return UNREADABLE, []
    return PRESENT, [child.name for child in children
                     if child.is_dir() and child.name != ACCOUNT_DIR
                     and not child.name.startswith(".")]


def readable_skill_names(directory: Path) -> Optional[Set[str]]:
    """`skill_names`, with "could not be read" kept apart from "nothing there".

    Not a distinction `skill_names` can make, and one rung of `hook_fate` needs
    it: whether the project ships a skill of some name decides whether the next
    run DELETES the personal copy, and a directory that is there and unlistable
    answers that question neither way. Folding it into the empty set answers it
    "no", which is the benign reading, which is the reading this ladder exists
    to stop being assumed.

    FileNotFoundError is the empty set and not None on purpose: a project with
    no `.claude/skills` at all ships no skill of any name, and that IS measured.
    Everything else — a plain file where the directory should be, a permission
    refusal, an I/O error — is None.
    """
    try:
        children = sorted(directory.iterdir())
    except FileNotFoundError:
        return set()
    except OSError:
        return None
    return {child.name for child in children if (child / "SKILL.md").is_file()}


def skill_names(directory: Path) -> Set[str]:
    """Directory names under `directory` that hold a SKILL.md.

    Used for the channel this script only needs to ANSWER a question about — the
    account store — so the SKILL.md test is the cheap way to avoid counting an
    incidental directory as a skill. Unreadable reads as empty here because the
    question it answers ("is there a second copy of this name?") is one an
    absent answer already leaves unraised; the project channel needs the other
    treatment and uses `readable_skill_names` directly.
    """
    names = readable_skill_names(directory)
    return set() if names is None else names


def declared_name(skill_dir: Path) -> Optional[str]:
    """The `name:` this directory's SKILL.md frontmatter declares, or None.

    A deliberately small reader rather than a YAML parse: this file ships into a
    `~/.claude/skills` where nothing is installed but the standard library, the
    same constraint that forces `digest_skill_dir` to be a hand copy.

    Conservative in ONE direction, on purpose. Every shape it cannot read with
    confidence — no SKILL.md, no frontmatter, a block scalar, an anchor, a flow
    collection, anything that might carry a trailing comment — returns None, and
    None leaves the caller reporting exactly what it would have reported without
    this function. The expensive mistake here is the other direction: a
    misparsed value that differs from the basename downgrades a real `untracked`
    FINDING into a note, which is the one outcome a reader cannot recover from
    by reading more carefully.

    `re.match` anchors at column 0, which is load-bearing rather than incidental:
    an indented `name:` is a key nested under something else, and reading it as
    the skill's own name is how a `field_types: {name: string}` block would be
    mistaken for the declaration.
    """
    try:
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None                      # frontmatter closed, no name in it
        match = re.match(r"name:[ \t]*(.*)$", line)
        if match is None:
            continue
        raw = match.group(1).strip()
        # "#" anywhere is enough to stop: ` # comment` is a comment YAML strips
        # and `a#b` is not, and telling those apart is a parse this does not do.
        if not raw or raw[0] in "|>&*!{[#" or "#" in raw:
            return None
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1].strip()
        return raw or None
    return None


def foreign_names(skills_dir: Path, names: List[str]) -> Dict[str, str]:
    """{directory: the OTHER name its SKILL.md declares}, for each disagreement.

    Computed once over the whole store rather than per lock, for the reason
    `store_findings` gives: it is a property of the directory, not of any
    expectation, so raising it per lock would report one fact N times in a
    multi-repo session.
    """
    found: Dict[str, str] = {}
    for name in names:
        declared = declared_name(skills_dir / name)
        if declared is not None and declared != name:
            found[name] = declared
    return found


def assign_origins(skills_dir: Path, names: List[str], record: Record,
                   locked: Set[str]) -> Dict[str, Origin]:
    """Exactly one origin for every directory on disk, decided once, store-wide.

    Total by construction: four arms ending in an unconditional `else`, so
    there is no directory the ladder declines to place and none two arms can
    claim. That is the property `OBSERVATION_ORIGINS` and the matrix test rest
    on. It replaces an arrangement in which `classify` decided the row, a
    separate function decided the note, and each re-applied its own subset of
    the gates — which is how one directory came to be described two ways in one
    report, and how a gate that could no longer fire went on being described as
    live.

    Store-wide, and computed BEFORE any lock is judged against, because the
    gates are questions about the whole session rather than about one
    expectation. `locked` is the union of every readable lock's names: a name
    ANY lock declares is a name some bundle here delivers, so the foreign
    premise fails for it under every lock and not only under the one naming it.

    The arms are the evidence, strongest first.

    * No readable record: nothing is attributable at all, not even against the
      frontmatter. `untracked`'s own no-record text says even "the hook
      installed it" cannot be ruled out there, and a note asserting the
      opposite over the top of it would be the report contradicting itself.
    * The record names it in an entry the hook accepts: the hook says it
      installed this directory. That outranks the frontmatter — a recorded
      directory with a mistyped `name:` is the hook's, whatever the typo says.
    * Some other name-keyed channel here names it, so "no name-keyed channel
      produced this" is already false: a lock declares it — and the hook is
      about to overwrite the directory whatever its frontmatter says — or the
      record carries an entry for it that the hook's shape check REJECTED. The
      rejected entry counts because `read_record` drops it from `entries` while
      the file on disk still holds the name. Its frontmatter agreeing with its
      basename lands here too: there is no disagreement to measure.
    * Otherwise its SKILL.md declares a name that is not its basename, and no
      name-keyed channel here could have produced it: FOREIGN.
    """
    declared = foreign_names(skills_dir, names)
    assigned: Dict[str, Origin] = {}
    for name in names:
        if record.state != PRESENT:
            assigned[name] = Origin(UNKNOWN)
        elif name in record.entries:
            assigned[name] = Origin(HOOK)
        elif (name in locked or name in record.skipped_names
                or name not in declared):
            assigned[name] = Origin(UNATTRIBUTED, declared.get(name))
        else:
            assigned[name] = Origin(FOREIGN, declared[name])
    return assigned


def _why_not_foreign(origin: Origin) -> str:
    """Why this UNATTRIBUTED directory got no `foreign` note, from the reading.

    Reads `assign_origins`' own answer rather than re-deciding, because the two
    reasons are not visible from the frontmatter at all: a lock declaring the
    basename, or an install-record entry naming it that the hook's shape check
    rejected, both outrank a disagreeing `name:`. The sentence this replaced
    asserted the opposite of both — "its SKILL.md declares its own basename" —
    over directories whose SKILL.md plainly does not, with the contradicting
    evidence printed a few lines above in the same report.
    """
    if origin.declared is None:
        return ("its SKILL.md declares this directory's own basename, or "
                "declares nothing this script can read.")
    return (f"its SKILL.md declares `name: {origin.declared}`, but some "
            f"name-keyed channel here does name the basename — a lock read "
            f"this run declares it, or the install record carries an entry for "
            f"it that the hook's own shape check rejected. Either outranks the "
            f"frontmatter, so the seeded reading is ruled out rather than "
            f"untested.")


def foreign_notes(skills_dir: Path, origins: Dict[str, Origin],
                  account: Set[str] = frozenset()) -> List[Finding]:
    """One note per directory no name-keyed channel here could have produced.

    A NOTE and not a finding, for the central property: this is the correct
    resting state of a whole class of sessions. The hosted harness seeds
    `~/.claude/skills/session-start-hook/` before the bootstrap hook runs at
    all (#123), so leaving it an `untracked` FINDING makes exit 1 the permanent
    resting state of every healthy session on that surface.

    Reads `assign_origins`' answer rather than re-deciding, so the note and the
    origin column are two views of one decision.

    `account` is passed because the account manifest is one of the three
    name-keyed channels this rules out, and it is the one that can name the
    BASENAME while having nothing to do with the directory. Saying "no
    name-keyed channel names it" while the same report shows `synced/<name>`
    is a claim a reader can falsify in one `ls`.

    `skills_dir` is passed for the rest of that clause. Knowing the basename is
    taken says nothing about WHAT is under it, and the first version of this
    text asserted three things about the account copy's frontmatter — that it is
    "a skill of its own", that "this one declares something else", and that "no
    re-upload could reconcile them" — off the directory's existence alone. All
    three are false for a byte-identical twin, and one `cat` shows it. So the
    frontmatter is read, and the sentence says which of the four states below
    was found.
    """
    notes: List[Finding] = []
    for name, origin in sorted(origins.items()):
        if origin.kind != FOREIGN:
            continue
        if name in account:
            theirs = declared_name(skills_dir / ACCOUNT_DIR / name)
            if theirs == origin.declared:
                collision = (
                    f"The account store does hold a {ACCOUNT_DIR}/{name}/, and "
                    f"its SKILL.md declares `name: {theirs}` too — the same "
                    f"disagreement, so the two may well be one skill delivered "
                    f"twice under a basename neither of them claims. They are "
                    f"still not diffed against each other here: the shadow "
                    f"comparison is declared over the origins a channel here "
                    f"accounts for, and this directory is not one of them.")
            elif theirs == name:
                collision = (
                    f"The account store does hold a {ACCOUNT_DIR}/{name}/, and "
                    f"its SKILL.md declares `name: {name}` — the basename. So "
                    f"it is a skill of its own under that name and not another "
                    f"copy of this directory, which declares something else.")
            elif theirs is not None:
                collision = (
                    f"The account store does hold a {ACCOUNT_DIR}/{name}/, and "
                    f"its SKILL.md declares `name: {theirs}` — a third name, "
                    f"neither this directory's basename nor what this one "
                    f"declares. Two things colliding on one basename, then, "
                    f"rather than one skill's two deliveries.")
            else:
                collision = (
                    f"The account store does hold a {ACCOUNT_DIR}/{name}/, and "
                    f"this script cannot read a `name:` out of its SKILL.md — "
                    f"so whether it is another copy of THIS directory is not "
                    f"established either way.")
        else:
            collision = "Nor does the account manifest."
        notes.append(_observed(
            "foreign", origin.kind, name,
            f"its SKILL.md declares `name: {origin.declared}`, which is not "
            f"this directory's basename. Every channel this script models keys "
            f"on the basename — the lock installs to its key's last segment, "
            f"the install record names directories, the account manifest names "
            f"directories — and the registry's own CI refuses a skill whose "
            f"frontmatter name disagrees with its directory "
            f"(scripts/check_skills.py, kind name-dir-mismatch). No lock read "
            f"here names it, and the install record does not name it: not in an "
            f"entry the hook accepts, and not in one it skips. {collision} So "
            f"nothing this script can see delivered THIS directory, and it is "
            f"reported as a state rather than as something to fix: on a hosted "
            f"surface the harness seeds directories under this HOME before the "
            f"bootstrap hook runs, and none of those is a decision anyone here "
            f"made. Two things this does NOT establish. WHO placed it — only "
            f"that no name-keyed channel here names it. And that no bundle EVER "
            f"delivered it: a hook that fails to read the record rewrites it "
            f"from scratch, which forgets every install before that run, and a "
            f"registry whose CI does not run the name-dir-mismatch check could "
            f"have shipped this frontmatter. It is still always-on context, and "
            f"the hook will not remove it, because the hook removes only what "
            f"its record proves it installed."))
    return notes


def newest_mtime(directory: Path) -> Optional[float]:
    """When this directory was last written, for the fallback only.

    The newest file in it rather than the directory's own mtime: `cp -R` stamps
    both with the copy time, but a later edit to one file moves only the file.
    """
    try:
        times = [child.stat().st_mtime
                 for child in directory.rglob("*") if child.is_file()]
    except OSError:
        return None
    if not times:
        try:
            return directory.stat().st_mtime
        except OSError:
            return None
    return max(times)


def cluster(stamped: List[Tuple[str, float]]) -> List[List[Tuple[str, float]]]:
    """Split the stamped names wherever the gap to the next one exceeds the window.

    Neighbour-to-neighbour rather than distance from the first: one `cp -R` loop
    writes its directories in sequence, so a slow run is a chain of small gaps
    and not a single wide one.
    """
    clusters: List[List[Tuple[str, float]]] = []
    for name, when in sorted(stamped, key=lambda pair: pair[1]):
        if clusters and when - clusters[-1][-1][1] <= MTIME_CLUSTER_GAP:
            clusters[-1].append((name, when))
        else:
            clusters.append([(name, when)])
    return clusters


def classify(skills_dir: Path, names: List[str], record: Record, lock: Lock,
             origins: Dict[str, Origin], account: Set[str] = frozenset(),
             repo_owned: Optional[Set[str]] = frozenset(),
             store_state: str = PRESENT,
             surface: str = DURABLE,
             ) -> Tuple[List[Row], List[Finding], List[Finding]]:
    """One row per directory on disk, plus the findings and notes they imply.

    A finding is something a human has to decide about. A note is something the
    next bootstrap will handle by itself, or the correct resting state of this
    kind of session — reported because "the hook is about to delete this" is
    worth seeing, not because anything is wrong. The module docstring's central
    property is what that split serves.

    `origins` is `assign_origins`' answer and is required rather than
    recomputed: the origin is a property of the DIRECTORY and of the whole
    session, while this function is called once per lock, so measuring it here
    would give one directory as many origins as the session has locks. Every
    branch below switches on it, and the switch is exhaustive over `ORIGINS` —
    there is no `else` that guesses.

    `surface` is what separates finding from note for a locked skill that is
    simply not here. It is the same fact on both kinds of machine and the
    opposite verdict: correct on a durable one, where the marketplace is
    authoritative, and a delivery failure on an ephemeral one, where the hook is
    the only channel there is. Defaults to DURABLE because that is the reading
    under which this stays quiet, and a diagnostic should need evidence to raise
    a finding rather than evidence to withhold one.

    Every judgement of the form "this should not be here" or "this is missing"
    needs BOTH sides: the lock says what was expected, the record says who put
    what there. With no readable lock there is no expectation to fall short of,
    so those findings are withheld rather than fabricated against an empty set —
    otherwise a missing lock reports the entire store as stale.

    `account` and `repo_owned` are the names the OTHER two channels deliver. They
    are here only so that "not in the personal store" does not get reported as
    "not delivered": both of those channels satisfy a locked name without the
    hook installing anything. `repo_owned` is None when the project's skills
    directory could not be listed — see `readable_skill_names` — and every use
    of it below has to say what it does with an answer nobody has.
    """
    rows: List[Row] = []
    findings: List[Finding] = []
    notes: List[Finding] = []
    attributable = record.state == PRESENT
    expected = lock.state == PRESENT
    # Bound once: every sentence below that says what a next run does is only
    # true where a next run happens. See `when_the_hook_runs`.
    but_here = when_the_hook_runs(surface)

    for name in names:
        origin = origins[name].kind
        in_lock = name in lock.names

        if origin == FOREIGN:
            # The `untracked` finding this replaces is withheld rather than
            # softened, and that is the whole of it: the finding asks the reader
            # to account for a directory, and there is no account to give for
            # one the surface placed. `foreign_notes` says so once, store-wide.
            rows.append(Row(name, FOREIGN, None, None, None, in_lock))
            continue

        if origin != HOOK:
            rows.append(Row(name, origin, None, None, None, in_lock))
            if not expected:
                continue
            # The ladder, not the rung: `may_replace` saying yes is the SECOND
            # of five questions, and the two below it delete the directory.
            fate = (hook_fate(lock, name, repo_owned=repo_owned,
                              replaceable=lock_names_the_bytes(
                                  lock, name, digest_skill_dir(skills_dir / name)))
                    if in_lock else None)
            if fate == DELETED_BY_THE_DUP_GUARD:
                findings.append(_observed(
                    DELETED_BY_THE_DUP_GUARD, origin, name,
                    DUP_GUARD_DELETES.format(name=name) + but_here))
            elif fate == DELETED_BY_THE_COLLISION_GUARD:
                notes.append(_observed(
                    DELETED_BY_THE_COLLISION_GUARD, origin, name,
                    COLLISION_GUARD_DELETES.format(name=name) + but_here))
            elif fate == COLLISION_UNMEASURED:
                findings.append(_observed(
                    COLLISION_UNMEASURED, origin, name,
                    COLLISION_GUARD_UNMEASURED.format(name=name) + but_here))
            elif fate in STILL_DELIVERS:
                notes.append(_observed(
                    "bytes-are-the-locked-ones", origin, name,
                    THE_BYTES_THE_LOCK_NAMES + but_here))
            elif in_lock and origin == UNATTRIBUTED:
                findings.append(_observed(
                    "hand-placed-over-locked", origin, name,
                    "the lock names it and the record does not name it, so "
                    "nothing establishes it as the hook's. " + HOOK_REFUSAL +
                    " The one state that would be overwritten anyway — bytes "
                    "that already digest to exactly what the lock names — was "
                    "tested here and does not hold. So it is not "
                    "this copy that is at risk, it is the locked skill's "
                    "delivery — including the project-collision path, which "
                    "the hook reaches only after deciding it may replace this "
                    "directory. Move it out of the store if you want the "
                    "bundle's copy instead." + but_here))
            elif in_lock:
                # UNKNOWN and locked, and the bytes are not the ones the lock
                # names. Both of the clauses that would let the hook overwrite
                # have now been asked: the record clause cannot be satisfied by
                # a record nothing can read, and the lock clause was measured
                # above. What is left is a refusal, which the absent record
                # note does NOT already say — it says nothing about delivery.
                findings.append(_observed(
                    "unattributable-over-locked", origin, name,
                    "the lock names it, and neither of the two clauses that "
                    "let the hook overwrite a directory holds: what is here "
                    "does not digest to the digest the lock names — a "
                    "directory that cannot be measured at all fails that the "
                    "same way, and the hook refuses both — and there is no "
                    "readable record to say the hook installed it. " +
                    HOOK_REFUSAL + " Who put it here is exactly what the "
                    "unreadable record cannot say, so this is not an "
                    "accusation: it is the delivery consequence, which holds "
                    "whoever the answer turns out to be. Move the directory "
                    "out of the store and the next run installs the locked "
                    "copy." + but_here))
            elif origin == UNATTRIBUTED:
                findings.append(_observed(
                    "untracked", origin, name,
                    "in the skills directory, named by neither the install "
                    "record nor the lock this was judged against (the LOCK "
                    "line below says which). Nothing that lock declares would "
                    "update it, and the hook removes only what the record "
                    "proves it installed — so on that expectation alone it is "
                    "left alone indefinitely. Where a session has several locks "
                    "this is one lock's reading, not a verdict about the name: "
                    "another lock may name it, and would report it separately. "
                    "Four ways to land here: it is yours (right), you expected "
                    "the bundle to own it (a delivery gap), the hook "
                    "installed it and then rewrote the record after failing to "
                    "read one, which forgets what came before, or the SURFACE "
                    "seeded it — a hosted harness places skill directories "
                    "under this HOME before the bootstrap hook runs, and one of "
                    "those is nobody's decision to review. A seeded directory "
                    "is recognised and reported as a `foreign` NOTE instead, "
                    f"and this one is not: {_why_not_foreign(origins[name])}"))
            else:
                # Same directory, same consequence, weaker evidence: without a
                # record nothing can say who installed it, and the hook removes
                # only what it can show it installed. Reported anyway — staying
                # silent here would mute the doctor exactly where it knows least.
                findings.append(_observed(
                    "untracked", origin, name,
                    "not named by the lock, and there is no readable record to "
                    "say whether the hook installed it. Nothing will remove it "
                    "either way: the hook only removes what it can prove it put "
                    "there. The four causes the attributable case lists apply "
                    "here too, the surface having seeded it among them — and "
                    "with no record, even 'the hook installed it' cannot be "
                    "ruled out."))
            continue

        entry = record.entries[name]
        measured = digest_skill_dir(skills_dir / name)
        if measured is None:
            integrity = UNMEASURABLE
        elif measured == entry.digest:
            integrity = UNCHANGED
        elif digest_shared_payload(skills_dir / name) == entry.digest:
            integrity = ARTEFACTS_ONLY
        else:
            integrity = EDITED
        rows.append(Row(name, HOOK, entry.registry, entry.bundle, integrity, in_lock))

        if not expected:
            continue
        in_scope = (entry.registry, entry.bundle) in lock.claims
        if in_lock:
            # `may_replace`'s three clauses, as this origin can satisfy them:
            # the record vouches for exactly these bytes (UNCHANGED), or the
            # lock names them. Then the LADDER, because a yes here is only the
            # second of five questions.
            fate = hook_fate(
                lock, name, repo_owned=repo_owned,
                replaceable=(integrity == UNCHANGED
                             or lock_names_the_bytes(lock, name, measured)))
            if fate == DELETED_BY_THE_DUP_GUARD:
                findings.append(_observed(
                    DELETED_BY_THE_DUP_GUARD, HOOK, name,
                    DUP_GUARD_DELETES.format(name=name) + but_here))
            elif fate == DELETED_BY_THE_COLLISION_GUARD:
                notes.append(_observed(
                    DELETED_BY_THE_COLLISION_GUARD, HOOK, name,
                    COLLISION_GUARD_DELETES.format(name=name) + but_here))
            elif fate == COLLISION_UNMEASURED:
                findings.append(_observed(
                    COLLISION_UNMEASURED, HOOK, name,
                    COLLISION_GUARD_UNMEASURED.format(name=name) + but_here))
            elif fate in STILL_DELIVERS:
                # The record vouches for older bytes than these and the lock has
                # moved on to exactly the ones here. Reported as a note and not
                # withheld, because "the record and the directory disagree" is
                # worth seeing even where the disagreement costs nothing.
                # UNCHANGED is excluded rather than left to fall through: there
                # the record, the lock and the disk all agree, and a note on
                # every healthy locked skill is noise.
                if integrity != UNCHANGED:
                    notes.append(_observed(
                        "bytes-are-the-locked-ones", HOOK, name,
                        THE_BYTES_THE_LOCK_NAMES + but_here))
            elif integrity == ARTEFACTS_ONLY:
                extra = dropped_files(skills_dir / name)
                findings.append(_observed(
                    "artefacts-and-locked", HOOK, name,
                    f"every file an upload would carry is byte-for-byte the one "
                    f"the hook installed, and the whole difference is files the "
                    f"upload filter drops"
                    f"{' (' + name_list(extra) + ')' if extra else ''}. No "
                    f"instruction byte is at risk. The hook's digest is the WHOLE "
                    f"directory, though, so what is here matches neither the "
                    f"locked digest nor the recorded one. {HOOK_REFUSAL} Delete "
                    f"those files and the next run installs normally. Running a "
                    f"skill's own test suite from inside the installed copy gets "
                    f"you here.{but_here}"))
            else:
                # LEFT_UNREPLACED with UNCHANGED is unreachable by construction —
                # UNCHANGED is one of the clauses that makes it replaceable — and
                # `_observed` is what proves it rather than an assert nobody
                # reads: `unchanged-and-locked` is in no observation, so the
                # impossible arm raises instead of printing.
                findings.append(_observed(
                    f"{integrity}-and-locked", HOOK, name,
                    f"{_cause(integrity)} and the lock still names it. "
                    f"{HOOK_REFUSAL} Nothing here is lost; what stops is this "
                    f"skill's updates. Restore the bytes the record vouches for, "
                    f"or move the directory out of the store, and the next run "
                    f"installs the locked copy.{but_here}"))
            continue
        if not in_scope:
            # Scope is checked BEFORE integrity, because out of scope the planner
            # short-circuits to `keep` without ever consulting the digest — so the
            # edited-and-stale verdict degrade below simply does not happen, and
            # promising it would send the reader looking for a signal the hook
            # never emits.
            findings.append(_observed(
                "stale-out-of-scope", HOOK, name,
                f"left the lock, and the lock no longer declares the bundle "
                f"{entry.bundle!r} at the registry it came from. Removal is "
                f"scoped to what the lock claims, so nothing here will ever "
                f"clean it up — and the hook does not mention it either, because "
                f"it is not in scope to have an opinion."))
        elif integrity in (EDITED, UNMEASURABLE):
            findings.append(_observed(
                f"{integrity}-and-stale", HOOK, name,
                f"{_cause(integrity)} and it has left the lock. The hook leaves "
                f"it in place and degrades its verdict for as long as that holds "
                f"— but what preserves it is the MISMATCH, not having left the "
                f"lock: restore the original bytes and the next run removes it. "
                f"Move it out of the store to keep it.{but_here}"))
        elif integrity == ARTEFACTS_ONLY:
            extra = dropped_files(skills_dir / name)
            findings.append(_observed(
                "artefacts-and-stale", HOOK, name,
                f"it has left the lock, and every file an upload would carry "
                f"is byte-for-byte the one the hook installed — but files the "
                f"upload filter drops"
                f"{' (' + name_list(extra) + ')' if extra else ''} make the "
                f"hook's whole-directory digest differ from the recorded one. "
                f"The hook removes only what it can show it installed "
                f"unchanged, so the removal this would otherwise get does not "
                f"happen. That run's `skills:` verdict names it after "
                f"`DEGRADED`, as `no longer in the lock left in place, edited "
                f"since install`. Delete those files and the next run cleans "
                f"it up.{but_here}"))
        else:
            notes.append(_observed(
                "stale", HOOK, name,
                "left the lock, is untouched since install, and its registry and "
                "bundle are still declared — the next bootstrap removes it. "
                "Unless AGENTSKILLS_BUNDLE narrows that run away from its "
                "bundle, which this cannot see from here: a narrowed run claims "
                "authority over one bundle and leaves the rest alone." +
                but_here))

    on_disk = set(names)
    # An unreadable store is not an empty one: "declared by the lock and not in
    # the personal store" is an assertion about a disk nobody read.
    if expected and store_state == PRESENT:
        for missing in sorted(lock.names - on_disk):
            # "Not in the personal store" is NOT "the session never sees it", and
            # saying so was wrong on three ordinary machines: one where the
            # account channel delivers the same name out of synced/, one where the
            # project ships it and the hook deliberately removed the personal copy
            # so repo-owned wins, and a durable one where the marketplace is
            # authoritative and this store is correctly empty. Each of those is a
            # skill the model can trigger. Only the session's own listing settles
            # it, and this script cannot see that — so it reports the absence and
            # names the channel that explains it, rather than a conclusion.
            if missing in account:
                notes.append(Finding(
                    "delivered-by-the-account-store", missing,
                    "not in the personal store, but the account store has a copy "
                    "under that name. The session sees that one; the hook did not "
                    "put it there and does not manage it."))
                continue
            if repo_owned and missing in repo_owned:
                notes.append(Finding(
                    "delivered-by-the-project", missing,
                    "not in the personal store because the project ships a skill "
                    "of that name and repo-owned wins — the hook removes its own "
                    "copy on purpose. The session sees the project's."))
                continue
            if not attributable:
                # No record means the hook has never delivered into this store.
                # On a durable machine that is the CORRECT state — §1's "should
                # hold no hook-installed bundle skills" — and reporting all nine
                # as defects is how a doctor teaches its reader to skip the
                # findings section. On an ephemeral surface the same three facts
                # are the delivery failure itself: the hook is the only channel
                # there is, it has never run, and a lock says what should have
                # arrived. Same evidence, opposite verdict, so the surface has to
                # be part of the judgement rather than a paragraph beside it.
                #
                # ABSENT only, not UNREADABLE. A record that is there and corrupt
                # already raises `record-unreadable` as a finding of its own,
                # which names the same delivery gap once; promoting here too
                # would report one defect N times over, once per locked name.
                if surface == EPHEMERAL and record.state == ABSENT:
                    findings.append(Finding(
                        "not-in-the-store", missing,
                        "declared by the lock and not in the personal store, on "
                        "an ephemeral surface where the bootstrap hook is the "
                        "only channel that delivers it — and the install record "
                        "is absent, so no hook run has ever finished here. This "
                        "is a delivery failure, not the empty store a durable "
                        "machine correctly has. Read the session-start `skills:` "
                        "verdict; if there was none, nothing ran the hook at all "
                        "— see the hook-not-wired finding if one is reported "
                        "above."))
                    continue
                notes.append(Finding(
                    "not-in-the-store", missing,
                    "declared by the lock and not in the personal store — which "
                    "is what a machine the hook has never run on looks like. If "
                    "the marketplace bundle is authoritative here, that is right; "
                    "confirm against the session's own skill listing."))
                continue
            detail = ("declared by the lock and not in the personal store, and no "
                      "other channel this script can see accounts for it. Confirm "
                      "against the session's own skill listing, which is the only "
                      "signal that says what the model can actually trigger.")
            if missing not in record.entries:
                # Two causes, and the record cannot separate them: it carries no
                # ref and no timestamp on purpose, so nothing in it can be dated
                # against the current lock. Naming only the install failure was
                # wrong on this repo's own workflow, where the lock is regenerated
                # in a commit of its own and no session has started since.
                detail += (" The record is readable and does not name it either, "
                           "which means the last hook run never saw it — the lock "
                           "has moved since, and no session has started — or it "
                           "saw it and could not install it (unreachable source, "
                           "digest mismatch, name collision, absent at the pinned "
                           "ref). The record is undated by design and cannot say "
                           "which; start a session and read its `skills:` verdict.")
            findings.append(Finding("missing", missing, detail))
    return rows, findings, notes


def _cause(integrity: str) -> str:
    """Why a directory is not the bytes the record vouches for.

    EDITED is a measurement; UNMEASURABLE is the ABSENCE of one. The hook gives
    them the same treatment — it removes only what it can show it installed
    unchanged — and folding them into one sentence here would borrow that
    convenience to accuse the user of an edit nobody observed.
    """
    if integrity == EDITED:
        return "the bytes are no longer the ones the hook verified,"
    return ("the bytes could not be read, so they cannot be shown to be the "
            "ones the hook installed,")


def record_findings(record: Record, record_path: Path,
                    surface: str = DURABLE) -> List[Finding]:
    """What the record's own state costs, when it costs anything.

    An absent record is NOT one of these. On a durable machine it is exactly
    right, and a doctor that calls it a defect trains its reader to ignore
    findings. It is reported as a state, and it downgrades attribution to
    inference, which is the whole of its consequence.
    """
    findings: List[Finding] = []
    if record.state == UNREADABLE:
        findings.append(Finding(
            "record-unreadable", str(record_path),
            "present but not readable as the record's own shape. The hook cannot "
            "read it either: it prunes nothing while it is like this, and rewrites "
            "it from scratch at the next session start — so it self-heals in one "
            "run, but everything installed before the corruption is forgotten and "
            "anything that left the lock in that window is left alone forever. "
            "Start one clean session. If it is still like this afterwards the run "
            "never reached the rewrite — the record is written last, after the "
            "lock read, the git probe and the fetch — so read that session's "
            "`skills:` verdict, which names what stopped it." +
            when_the_hook_runs(surface)))
    if record.skipped:
        findings.append(Finding(
            "record-entries-skipped", str(record_path),
            f"{record.skipped} entry/entries do not match the shape the hook "
            f"accepts, so the hook skips them. Those installs are invisible to "
            f"the prune: it can never remove them, whatever the lock says."))
    return findings


def _crlf_side(mine: Path, theirs: Path) -> str:
    """Which copy carries the CRLF, as a clause, or "" when nothing was measured.

    Folding CRLF reconciled a pair says the two disagree about line endings. It
    does NOT say which one is CRLF, and the earlier text asserted the account
    store was — an assumption, true of the copies that prompted it and not
    measured on the pair in front of the reader. Both directories are already in
    hand, so the direction is readable rather than assumable.
    """
    ours, yours = carries_crlf(mine), carries_crlf(theirs)
    if ours is None or yours is None:
        return ""                     # unmeasured: say nothing rather than guess
    if yours and not ours:
        return " — the account copy carries CRLF and this one does not"
    if ours and not yours:
        return " — this copy carries CRLF and the account copy does not"
    if ours and yours:
        return " — both carry CRLF, and they disagree about where"
    return ""


def shadow_findings(skills_dir: Path, names: List[str], account: Set[str],
                    origins: Dict[str, Origin]
                    ) -> Tuple[List[Finding], List[Finding]]:
    """(findings, notes) for every bare name BOTH channels deliver into one session.

    Three names arrive from both arms in this repo's own cloud sessions (#122):
    the hook installs `~/.claude/skills/<name>/` from the lock, and the account
    store carries `~/.claude/skills/synced/<name>/` from an earlier upload. Both
    present to the session's skill listing as the same bare name, the listing
    shows it once, and nothing in the listing, on disk, or in any log says which
    copy the model actually read. Before this, the doctor called such a session
    clean — three of its eight locked skills silently shadowed — which is the
    gap #122 was filed for. Both directories were already in hand here; only the
    comparison was missing.

    ADR 0002 anticipated that account-synced skills feed the same per-session
    listing and rejected pushing the `adam` bundle to the account on that basis.
    What it costed was the duplicated CONTEXT. It says nothing about the same
    NAME arriving from both arms, which is a question about which bytes get
    read, so this reports the collision rather than resolving it: naming a
    winner between the channels is a policy decision and is item 1 of #122, not
    something a diagnostic settles by being convenient — the same restraint
    `LockResult` keeps about which lock wins.

    Matching copies are a NOTE and divergent ones a FINDING, and that split is
    the whole of the judgement here. A note keeps a healthy session at exit 0,
    which it must: the collision is today the correct and expected state of
    every cloud session this registry delivers into, and a doctor that reddens
    the ordinary case is one whose findings get skipped — `record_findings`'
    argument again. Divergence is the opposite: two different sets of
    instructions under one name, one of them silently chosen, and it is
    actionable in a way the benign case is not.

    Both texts say the benign case is a property of THIS MOMENT rather than of
    the design, because it is. The copies update on different clocks — the
    personal one at every session start from `skills.lock`, the account one only
    when someone runs `sync-skills` — so "edit a skill, regenerate the lock,
    forget to re-upload" turns the note into the finding with nothing having
    gone wrong in between, and nothing in CI can see it: the collision exists
    only on a surface CI never stands on.
    """
    findings: List[Finding] = []
    notes: List[Finding] = []
    # A directory with no SKILL.md is not a skill and the session's listing never
    # sees it, so it collides with nothing: an empty `beta/` left behind beside a
    # real account `beta` is ONE delivered skill, and calling it two — then
    # finding the two "different", which a directory holding nothing next to a
    # skill unavoidably is — invents both the collision and the divergence.
    for name in sorted(set(names) & set(account) & skill_names(skills_dir)):
        # `shadow` is not raised about every origin, and `OBSERVATION_ORIGINS`
        # is where that is decided rather than here — see its comment for why
        # a FOREIGN directory is not one of a skill's two deliveries and why
        # comparing it produces a finding no upload could ever clear.
        if origins[name].kind not in OBSERVATION_ORIGINS["shadow"]:
            continue
        mine = skills_dir / name
        theirs = skills_dir / ACCOUNT_DIR / name
        both = (f"delivered by BOTH channels under one bare name — {mine} and "
                f"{theirs}. Both present to the session's skill listing as "
                f"`{name}`, the listing shows it once, and nothing there, on "
                f"disk or in any log says which copy the model read.")
        clocks = ("The two copies update on different clocks: the personal one "
                  "tracks skills.lock and is refreshed at every session start, "
                  "the account one changes only when someone runs sync-skills "
                  "from a machine with a browser. `--account-drift` compares the "
                  "recorded account state against the registry and has no notion "
                  "of a session where both copies coexist, and CI never stands "
                  "on the surface where they do.")

        exact_mine = digest_shared_payload(mine)
        exact_theirs = digest_shared_payload(theirs)
        if exact_mine is None or exact_theirs is None:
            unread = " and ".join(
                str(path) for path, value in ((mine, exact_mine), (theirs, exact_theirs))
                if value is None)
            notes.append(_observed(
                "shadowed-by-the-account-store", origins[name].kind, name,
                f"{both} The two could NOT be compared: {unread} could not be "
                f"read, so whether they hold the same instructions is unmeasured "
                f"rather than confirmed. {clocks}"))
            continue

        if exact_mine == exact_theirs:
            sameness = "The two copies carry byte-identical instructions"
        else:
            norm_mine = digest_shared_payload(mine, fold=True)
            norm_theirs = digest_shared_payload(theirs, fold=True)
            if norm_mine is None or norm_theirs is None:
                notes.append(_observed(
                    "shadowed-by-the-account-store", origins[name].kind, name,
                    f"{both} The two differ byte-for-byte and could not be "
                    f"re-compared with line endings normalised, so whether that "
                    f"difference is only CRLF-vs-LF is unmeasured. {clocks}"))
                continue
            if norm_mine != norm_theirs:
                findings.append(_observed(
                    "shadow-copies-differ", origins[name].kind, name,
                    f"{both} And the two do NOT carry the same instructions: "
                    f"they still differ once CRLF line endings are folded to LF "
                    f"({norm_mine[:12]} here, {norm_theirs[:12]} in the account "
                    f"store). So the model is reading one of two different sets "
                    f"of instructions under this name and nothing records which. "
                    f"{PAYLOAD_SCOPE} "
                    f"{clocks} The usual cause is the account copy being behind — "
                    f"a skill edited and re-locked, and never re-uploaded. "
                    f"Compare them with the account-drift procedure in "
                    f"skills-doctor's SKILL.md, then either re-upload or accept "
                    f"the drift deliberately."))
                continue
            sameness = ("The two copies carry byte-identical instructions "
                        "once CRLF line endings are folded to LF"
                        + _crlf_side(mine, theirs))

        notes.append(_observed(
            "shadowed-by-the-account-store", origins[name].kind, name,
            f"{both} {sameness}, so which one wins does not change what the "
            f"model reads TODAY. That is a property of this moment and not of "
            f"the design. {PAYLOAD_SCOPE} {clocks} Reported so the shadow is a "
            f"measured "
            f"condition rather than an invisible one; naming a winner between "
            f"the two channels is a policy question this does not answer. See "
            f"docs/decisions/0002, which costed the duplicated context and not "
            f"this collision."))
    return findings, notes


def store_findings(store_state: str, skills_dir: Path) -> List[Finding]:
    """What the personal store's own state costs.

    Store-wide rather than per-lock: with several locks in a multi-repo session
    this would otherwise be raised once per lock, reporting one unreadable
    directory as N defects.
    """
    if store_state != UNREADABLE:
        return []
    return [Finding(
        "store-unreadable", str(skills_dir),
        "the personal store could not be read, so nothing above was measured. "
        "An empty report here means nothing was looked at, not that nothing is "
        "wrong.")]


def hook_findings(project_dir: Path, here: bool, user: bool,
                  children: List[Path], any_lock: bool,
                  surface: str = DURABLE) -> Tuple[List[Finding], List[Finding]]:
    """The lock is right, the hook is right, and nothing will ever run it.

    #84's signature exactly, and the reason it took an investigation to find:
    every file is present and correct, so nothing inside any one repo looks
    wrong. What is missing is a settings file at a level the chain actually
    reads. Claude Code resolves hooks from `cwd` and `$HOME` only — an
    `--add-dir` grant contributes skills, commands, agents and CLAUDE.md, and
    never hooks — so a session opened on the PARENT of several repos consults
    none of their settings files, and every SessionStart hook they declare is
    inert.

    Requires a lock as well as the wiring, because the finding is about delivery
    failing: a child repo with a hook and no lock has nothing to deliver, and
    saying its hook never fires would be true and pointless.

    Returns (findings, notes), because the same wiring costs different things on
    the two surfaces. On an ephemeral one the hook is the only channel there is,
    so nothing consulting it means nothing is delivered. On a durable one the
    hook makes ITSELF a no-op — `skills: skipped — durable session` — and the
    marketplace install is authoritative, so a hook that never fires costs
    nothing at all. Reporting it as a defect there would be this change's own
    thesis inverted: a finding that is harmless on the ordinary case is one the
    reader learns to scroll past. It stays a NOTE, because it is still the
    answer to "why was there no `skills:` verdict?".
    """
    if here or user or not children or not any_lock:
        return [], []
    listed = ", ".join(str(path) for path in children[:5])
    if len(children) > 5:
        listed += f", and {len(children) - 5} more"
    where = (f"{len(children)} settings file(s) below this directory wire a "
             f"SessionStart hook and nothing here or at the user scope does: "
             f"{listed}. Hooks resolve from the settings chain at cwd and at "
             f"$HOME, never from an --add-dir grant — so with the session's "
             f"project dir set to the parent of these repos, none of those "
             f"hooks is consulted, whatever each lock declares. Nothing reports "
             f"it either: there is no `skills:` verdict, because the script "
             f"that prints one never runs.")
    if surface != EPHEMERAL:
        return [], [Finding(
            "hook-not-wired", str(project_dir),
            f"{where} Not a defect on this surface: the hook makes itself a "
            f"no-op on a durable machine, where the marketplace install is "
            f"authoritative — so nothing was lost by its not being consulted. "
            f"Recorded because it is the answer to why no `skills:` verdict "
            f"appeared, and because the same wiring IS a delivery failure on an "
            f"ephemeral surface. {UNREADABLE_LINKS} See docs/decisions/0005.")]
    return [Finding(
        "hook-not-wired", str(project_dir),
        f"{where} So no bundle is installed here at all, and the hook is the "
        f"only channel that would install one on this surface. Fix it at a "
        f"level the chain reads — a settings file at this directory, or at the "
        f"user scope — not inside the repos, which are already correct. "
        f"{UNREADABLE_LINKS} See docs/decisions/0005.")], []


def lock_findings(lock: Lock, lock_path: Path,
                  surface: str = DURABLE) -> List[Finding]:
    """A lock the hook refuses is the loudest delivery failure there is.

    An ABSENT lock is not a finding: a machine with no lock is one this script
    cannot verdict on, not one that is broken. A lock that is THERE and unusable
    is the opposite — it is being relied on and it delivers nothing.
    """
    if lock.state == REJECTED:
        return [Finding(
            "lock-rejected", str(lock_path),
            f"the hook's lock reader refuses this file ({lock.reason}), so it "
            f"installs nothing from it at all — every session start reports "
            f"DEGRADED and the store keeps whatever it already had. Regenerate "
            f"it with scripts/generate_skills_lock.py."
            f"{when_the_hook_runs(surface)}")]
    if lock.state == UNREADABLE:
        return [Finding(
            "lock-unreadable", str(lock_path),
            "the file is there and is not valid JSON, so the hook cannot read "
            "it either: it installs nothing and reports DEGRADED at every "
            "session start. Regenerate it with scripts/generate_skills_lock.py."
            + when_the_hook_runs(surface))]
    return []


def render(record: Record, record_path: Path, skills_dir: Path,
           results: List[LockResult], findings: List[Finding],
           notes: List[Finding], stamped: List[Tuple[str, float]],
           store_state: str = PRESENT,
           surface: Surface = Surface(DURABLE, "", "", False)) -> str:
    """The verdict line first, then the evidence behind it."""
    rows = results[0].rows if results else []
    declared: Set[str] = set()
    for result in results:
        declared |= result.lock.names
    # Counted from the locks, not from the findings: a locked name absent from
    # the store is a finding on an ephemeral surface and a note on a durable one,
    # and the headline count must not change with that judgement.
    missing = declared - {row.name for row in rows}
    out = [
        f"provenance: {len(rows)} on disk, "
        f"{_tally(record.state, rows)}, "
        f"{len(missing)} not in the store — record {record.state} — "
        f"surface {surface[0]} — "
        f"{len(findings)} finding{'' if len(findings) == 1 else 's'}",
        "",
        f"RECORD   {record_path}",
    ]
    if record.state == PRESENT and record.entries:
        # Deliberately weaker than "every hook row is fact". The integrity column
        # IS measured — the digest is recomputed here. `registry` and `bundle` are
        # the record's own testimony, and nothing on disk corroborates them; the
        # hook treats that file as sitting somewhere anyone with the user's shell
        # can write. It beats an mtime cluster by a distance without being proof.
        out += _para(f"present — {len(record.entries)} install(s) recorded. The rows "
                     f"below are the hook's own account of what it wrote, not an "
                     f"inference from the filesystem; the integrity column is "
                     f"measured against it.")
    elif record.state == PRESENT:
        # Not the same as absent, and the difference is the whole point: the hook
        # only writes this file at the END of a run, so an empty one is proof a
        # run finished and installed nothing — where an absent one is proof none
        # ever finished.
        out += _para("present and empty — a run completed and recorded no install. "
                     "Either it installed nothing, or it rewrote the record after "
                     "failing to READ one, which forgets everything from before. "
                     "Either way nothing below is the hook's to remove.")
    elif record.state == ABSENT:
        out += _para("absent — no hook run has ever reached the point of writing it "
                     "under this HOME. Right on a durable machine, where the "
                     "marketplace install is authoritative. On an ephemeral surface "
                     "it means delivery never happened, or a run bailed out early: "
                     "the session-start `skills:` verdict says which.")
    else:
        out += _para("unreadable — the file is there and is not the shape the hook "
                     "writes. See FINDINGS.")

    kind, entrypoint, remote, forced = surface
    out += ["", f"SURFACE  {kind}"]
    # All THREE arms, because the reading has to account for the verdict. An
    # ephemeral call made on `SKILLS_BOOTSTRAP_FORCE` alone prints an unset
    # entrypoint and no session id, and a reader who cannot see the third input
    # is looking at what appears to be a contradiction.
    reading = (f"CLAUDE_CODE_ENTRYPOINT={entrypoint or '(unset)'}, "
               f"CLAUDE_CODE_REMOTE_SESSION_ID="
               f"{'set' if remote else '(unset)'}, "
               f"SKILLS_BOOTSTRAP_FORCE={'set' if forced else '(unset)'}.")
    if kind == EPHEMERAL:
        para = (f"{reading} A cloud session, CI runner or container — the "
                f"same three readings the bootstrap hook installs on. It is "
                f"the only channel that delivers a locked bundle here, so a "
                f"locked skill missing from the personal store is a delivery "
                f"failure rather than the empty store a durable machine "
                f"correctly has.")
        # The one ephemeral reading that a DURABLE machine can produce by hand.
        # Agreeing with the hook's third arm is deliberate (see `read_surface`),
        # and it costs this: export the variable in a shell on your laptop and
        # every locked skill the marketplace install owns is reported as an
        # undelivered one. Disclosed rather than narrowed, because narrowing the
        # arm would disagree with the hook silently — the failure this file
        # exists to stop — whereas a reader who is told which input carried the
        # verdict can check it in one command.
        if forced and not remote and entrypoint != "remote":
            para += (" That verdict rests on SKILLS_BOOTSTRAP_FORCE ALONE — no "
                     "remote session id, and no entrypoint of exactly `remote`. "
                     "The hook agrees, testing only whether the variable is SET "
                     "(so even =0 forces an install). But if you exported it by "
                     "hand on a durable machine, this is that machine being read "
                     "as the hook would read it, and every delivery failure "
                     "below is about a store that is correctly empty. Unset it "
                     "and re-run to get the durable reading.")
        out += _para(para)
    elif kind == DURABLE:
        out += _para(f"{reading} A durable machine: the marketplace install is "
                     f"authoritative and the personal store is SUPPOSED to hold "
                     f"no hook-installed bundle skills. Finding a full set here "
                     f"is double delivery.")
    else:
        out += _para(f"{reading} Neither shape this can name: an entrypoint with "
                     f"no remote session id. Judged as durable, which is the "
                     f"quiet reading — so a delivery failure on such a machine "
                     f"would be reported below as a note rather than a finding. "
                     f"Settle it against the session's own `skills:` verdict.")

    for result in results:
        lock = result.lock
        out += ["", f"LOCK     {result.path}"]
        if lock.state == PRESENT:
            out.append(f"  {len(lock.names)} skill(s) declared across "
                       f"{len(lock.claims)} (registry, bundle) claim(s).")
        elif lock.state == REJECTED:
            out += _para(f"rejected — the hook refuses this lock ({lock.reason}), "
                         f"so it installs NOTHING from it. Nothing below can be "
                         f"called stale or missing against a lock that never "
                         f"applies. Regenerate it with "
                         f"scripts/generate_skills_lock.py.")
        else:
            out.append(f"  {lock.state} — nothing can be called stale or missing "
                       f"without a declared expectation.")
    if len(results) > 1:
        # Said once, plainly, rather than left for the reader to infer from a
        # column: several locks judging one store is the shape in which "which
        # one wins" stops being obvious, and this script deliberately does not
        # answer that (docs/decisions/0005).
        out += _para(f"{len(results)} locks were discovered one level below the "
                     f"project directory and each is reported separately. This "
                     f"names no winner among them: every finding below says "
                     f"which lock declared it.", "  ")

    out += ["", f"SKILLS   {skills_dir} ({len(rows)} directories, "
                f"excluding the account store {ACCOUNT_DIR}/)"]
    if store_state == ABSENT:
        out += _para("the directory does not exist — which is not the same as "
                     "empty, and is what a machine that has never had a personal "
                     "skill looks like. Check the path first.")
    elif store_state == UNREADABLE:
        out += _para("the directory could not be read. Nothing below was measured. "
                     "See FINDINGS.")
    elif not rows:
        out.append("  (none)")
    for row in rows:
        source = f"{row.registry} # {row.bundle}" if row.registry else "—"
        out.append(f"  {row.name:<28} {row.origin:<13} {source}")
        state = _membership(row.name, results)
        if row.integrity:
            state = f"{state}, {row.integrity} since install"
        out.append(f"  {'':<28} {'':<13} {state}")

    out += ["", f"FINDINGS ({len(findings)})"]
    for finding in findings or []:
        out.append(f"  [{finding.kind}] {finding.subject}{_whose(finding, results)}")
        out += _para(finding.detail, "      ")
    if not findings:
        out.append("  (none)")

    if notes:
        out += ["", f"NOTES ({len(notes)}) — expected states, or things the next "
                    f"bootstrap handles itself"]
        for note in notes:
            out.append(f"  [{note.kind}] {note.subject}{_whose(note, results)}")
            out += _para(note.detail, "      ")

    # `main` computes `stamped` only in the states where the fallback applies, so
    # a non-empty one IS the decision — re-testing `record.state` here would be a
    # second copy of it that no test could tell apart from the first.
    if stamped:
        # "no record" would contradict the RECORD block twenty lines above it in
        # the unreadable state, where the file is emphatically there. The fallback
        # applies whenever the record cannot ANSWER, which is both states.
        out += ["", "INFERENCE — the record cannot answer, so the mtime heuristic "
                    "is all there is"]
        out += _para("Directories that were written together, newest cluster last. "
                     "A cluster is CONSISTENT WITH one install run and is not "
                     "evidence of one: it cannot tell a hand copy made in the same "
                     "minute as an install from an install, nor an install an editor "
                     "has touched since from a hand copy. Those are the two failures "
                     "the record exists to end.")
        for group in cluster(stamped):
            out.append(f"    {_stamp(group[0][1])}  "
                       f"{', '.join(name for name, _ in group)}")
    return "\n".join(out)


def _membership(name: str, results: List[LockResult]) -> str:
    """The lock column for one directory, phrased for however many locks there are.

    With one lock this is the two words it has always been. With several, "not in
    lock" would be a claim about a lock the reader cannot identify, so the ones
    naming it are named — and when none do, the count says how many were asked.
    """
    usable = [result for result in results if result.lock.state == PRESENT]
    if not usable:
        # "not in lock" would read as "a lock exists and omits it", which is a
        # different and much worse fact than "there is no lock".
        return f"lock {results[0].lock.state}" if results else "lock absent"
    naming = [result for result in usable if name in result.lock.names]
    if len(results) == 1:
        return "in lock" if naming else "not in lock"
    if not naming:
        return f"in none of the {len(usable)} readable lock(s)"
    return "in lock: " + ", ".join(str(result.path) for result in naming)


def _whose(finding: Finding, results: List[LockResult]) -> str:
    """Which lock a finding belongs to, said only when there is a choice."""
    if finding.lock is None or len(results) < 2:
        return ""
    return f" — declared by {finding.lock}"


def dedupe(findings: List[Finding]) -> List[Finding]:
    """Fold findings several locks raise about the same thing into one.

    In a multi-repo session every lock is judged against the same store, and the
    locks largely declare the same bundle — so an undelivered skill is raised
    once per lock that names it. Measured on the session that produced #85's
    repro: eleven locks turned twenty-four distinct defects into ninety-five
    findings, and `session-start-hook` alone appeared eleven times. That is the
    same "one defect, N times" inflation `store_findings` exists to avoid, and
    it is worse here because it scales with the number of repos open rather than
    with anything wrong.

    Identity is (kind, subject, detail), not (kind, subject). Two locks CAN say
    different things about one directory — one naming it, the other not — and
    those are two facts that happen to share a name, so they stay apart.

    Attribution survives the fold: the merged finding names every lock that
    raised it, which is what keeps "report per-lock" true of the output rather
    than only of the computation.
    """
    order: List[Tuple[str, str, str]] = []
    locks: Dict[Tuple[str, str, str], List[str]] = {}
    for finding in findings:
        key = (finding.kind, finding.subject, finding.detail)
        if key not in locks:
            order.append(key)
            locks[key] = []
        if finding.lock is not None and finding.lock not in locks[key]:
            locks[key].append(finding.lock)
    merged: List[Finding] = []
    for kind, subject, detail in order:
        named = locks[(kind, subject, detail)]
        merged.append(Finding(kind, subject, detail, _joined(named)))
    return merged


def _joined(names: List[str]) -> Optional[str]:
    """Up to three lock paths, then a count — a header line stays one line."""
    if not names:
        return None
    if len(names) <= 3:
        return ", ".join(names)
    return f"{', '.join(names[:3])} and {len(names) - 3} more"


def _para(text: str, indent: str = "  ") -> List[str]:
    """One paragraph, wrapped here rather than by hand at the call site.

    Hand-wrapped prose put the line breaks in the source, so a sentence could not
    be edited without re-wrapping it — and assertions ended up bound to substrings
    that existed only because of where a break happened to fall.

    `break_long_words=False` because the long words here are FILE PATHS, and the
    path is the actionable half of a finding. textwrap's default chops anything
    wider than the column, so a store under a long prefix came out as two halves
    the reader cannot select, copy or grep for — the same defect as truncating
    it, arrived at by accident. Overflowing the column is the cheaper cost.
    """
    return textwrap.fill(" ".join(text.split()), width=78,
                         initial_indent=indent, subsequent_indent=indent,
                         break_long_words=False,
                         break_on_hyphens=False).splitlines()


def _tally(state: str, rows: List[Row]) -> str:
    """The attribution counts, or the one honest count when there are none.

    Counted from the rows by origin rather than from arguments the caller
    tallied, so the parts sum to "N on disk" by construction. Without a readable
    record nothing is attributable and every row is UNKNOWN; printing
    "0 hook-installed, 0 unattributed" beside "3 on disk" reads as a
    contradiction — or worse, as "the store is empty".

    `counts` is keyed by `ORIGINS` and indexed, not `.get`-ed: an origin the
    tuple does not list raises here rather than being silently dropped from a
    headline that then does not add up.
    """
    counts = {origin: 0 for origin in ORIGINS}
    for row in rows:
        counts[row.origin] += 1
    if state != PRESENT:
        return f"{counts[UNKNOWN]} unattributable (no readable record)"
    tail = f", {counts[FOREIGN]} foreign" if counts[FOREIGN] else ""
    return (f"{counts[HOOK]} hook-installed, "
            f"{counts[UNATTRIBUTED]} unattributed{tail}")


def _stamp(when: float) -> str:
    return datetime.datetime.fromtimestamp(when).strftime("%Y-%m-%d %H:%M:%S")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Attribute every skill in the personal store to the registry "
                    "and bundle it came from, using the bootstrap hook's own "
                    "install record. Reports; never repairs.")
    parser.add_argument("--skills-dir", default="~/.claude/skills", metavar="DIR",
                        help="the personal skill store (default: ~/.claude/skills)")
    parser.add_argument("--lock", default=None, metavar="PATH",
                        help="the declared expectation. Default: the project "
                             "dir's own skills.lock, or every */skills.lock one "
                             "level below it when the project dir is the parent "
                             "of several repos")
    parser.add_argument("--project-dir", default=".", metavar="DIR",
                        help="the session's project; its .claude/skills/ is a "
                             "delivery channel this has to know about before "
                             "calling a locked skill missing, and its settings "
                             "chain is what decides whether any hook runs "
                             "(default: .)")
    args = parser.parse_args(argv)

    skills_dir = Path(args.skills_dir).expanduser()
    project_dir = Path(args.project_dir).expanduser()
    record_path = skills_dir / RECORD_NAME
    surface = read_surface()

    record = read_record(record_path)
    store_state, names = scan(skills_dir)
    account = skill_names(skills_dir / ACCOUNT_DIR)
    repo_owned = readable_skill_names(project_dir / ".claude" / "skills")
    # Every lock is read BEFORE any of them is judged against, because the
    # foreign gate is store-wide: "some bundle here delivers this name" is a
    # question about all the locks at once, and asking it lock by lock is how the
    # row and the note came to disagree about one directory.
    locks = [(path, read_lock(path))
             for path in discover_locks(args.lock, project_dir)]
    origins = assign_origins(
        skills_dir, names, record,
        {name for _, lock in locks for name in lock.names})

    # One store, judged once per declared expectation. See `LockResult`: the
    # locks are deliberately not merged first.
    results: List[LockResult] = []
    for lock_path, lock in locks:
        rows, findings, notes = classify(
            skills_dir, names, record, lock, origins, account=account,
            repo_owned=repo_owned, store_state=store_state, surface=surface[0])
        tagged = [finding._replace(lock=str(lock_path))
                  for finding in lock_findings(lock, lock_path, surface[0])
                  + findings]
        results.append(LockResult(
            lock_path, lock, rows, tagged,
            [note._replace(lock=str(lock_path)) for note in notes]))

    # Store-wide, like `store_findings` and for its reason: the collision is a
    # property of the two stores, not of any lock, so raising it per lock would
    # report one shadowed name N times in a multi-repo session.
    shadow_raised, shadow_noted = shadow_findings(skills_dir, names, account,
                                                  origins)

    here, user, children = hook_wiring(project_dir)
    hook_raised, hook_noted = hook_findings(
        project_dir, here, user, children,
        any(result.lock.state == PRESENT for result in results), surface[0])
    findings = dedupe(
        hook_raised
        + shadow_raised
        + store_findings(store_state, skills_dir)
        + record_findings(record, record_path, surface[0])
        + [finding for result in results for finding in result.findings])
    notes = dedupe(hook_noted
                   + shadow_noted
                   + foreign_notes(skills_dir, origins, account)
                   + [note for result in results for note in result.notes])

    stamped: List[Tuple[str, float]] = []
    if record.state != PRESENT:
        for name in names:
            when = newest_mtime(skills_dir / name)
            if when is not None:
                stamped.append((name, when))

    print(render(record, record_path, skills_dir, results,
                 findings, notes, stamped, store_state, surface))
    # 1 means "there are findings", never "the tool failed" — this is a doctor and
    # a finding is its normal output. Argparse keeps 2 for a usage error.
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
