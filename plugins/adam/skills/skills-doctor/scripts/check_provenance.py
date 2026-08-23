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
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

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
    state: str
    entries: Dict[str, Entry]
    skipped: int


class Lock(NamedTuple):
    state: str
    names: Set[str]
    claims: Set[Tuple[str, str]]
    reason: Optional[str] = None


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


def digest_shared_payload(path: Path, fold: bool = False) -> Optional[str]:
    """`digest_skill_dir`'s manifest shape over the files BOTH channels carry.

    For COMPARING two copies of one skill with each other, and for nothing else.
    Never for judging a copy against a RECORDED digest: the record and the lock
    both carry `digest_skill_dir`'s answer over the whole directory, so using
    this against one of those would report edited bytes as unchanged — precisely
    the lie `_cause` exists to avoid telling.

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
    for raw in record["installed"]:
        entry = _entry(raw)
        if entry is None:
            skipped += 1
            continue
        entries[entry.name] = entry
    return Record(PRESENT, entries, skipped)


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
    """The destination names a lock declares, and the (registry, bundle) it claims.

    `claims` is what decides whether a stale skill gets removed or kept: the hook
    removes only within the pairs its own lock declares, so that two repos sharing
    one ~/.claude/skills do not reap each other's installs.
    """
    try:
        lock = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Lock(ABSENT, set(), set())
    except (OSError, ValueError):
        return Lock(UNREADABLE, set(), set())
    if not isinstance(lock, dict):
        return Lock(UNREADABLE, set(), set())

    skills = lock.get("skills")
    if not isinstance(skills, dict):
        skills = {}
    # The destination is the key's LAST segment, the same reading the hook
    # installs by — `adam/foo` and `other/foo` are one directory, not two.
    names = {key.rsplit("/", 1)[-1] for key in skills if isinstance(key, str)}

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

    for key in skills:
        if isinstance(key, str) and "/" in key and key.split("/", 1)[0] not in owner:
            return Lock(REJECTED, set(), set(),
                        f"skill {key!r} names a bundle no source claims")
    return Lock(PRESENT, names, claims)


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


def skill_names(directory: Path) -> Set[str]:
    """Directory names under `directory` that hold a SKILL.md.

    Used for the two channels this script only needs to ANSWER a question about
    — the account store and the project's own skills — so the SKILL.md test is
    the cheap way to avoid counting an incidental directory as a skill.
    """
    try:
        children = sorted(directory.iterdir())
    except OSError:
        return set()
    return {child.name for child in children if (child / "SKILL.md").is_file()}


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


def foreign_notes(foreign: Dict[str, str]) -> List[Finding]:
    """One note per directory no name-keyed channel here could have produced.

    A NOTE and not a finding, for the reason `record_findings` gives about an
    absent record: this is the correct resting state of a whole class of
    sessions, and a doctor that calls the correct state a defect trains its
    reader to skip the findings section. Measured on two independent hosted
    cloud sessions (#123): the surface seeds `~/.claude/skills/
    session-start-hook/` before the bootstrap hook runs at all, so leaving it as
    an `untracked` FINDING makes exit 1 the permanent resting state of every
    healthy session on that surface — an exit code that can never be green is
    one that has stopped carrying information.
    """
    return [Finding(
        "foreign", name,
        f"its SKILL.md declares `name: {declared}`, which is not this "
        f"directory's basename. Every channel this script models keys on the "
        f"basename — the lock installs to its key's last segment, the install "
        f"record names directories, the account manifest names directories — "
        f"and the registry's own CI refuses a skill whose frontmatter name "
        f"disagrees with its directory (scripts/check_skills.py, kind "
        f"name-dir-mismatch). So no bundle here delivered it, and it is "
        f"reported as a state rather than as something to fix: on a hosted "
        f"surface the harness seeds directories under this HOME before the "
        f"bootstrap hook runs, and none of those is a decision anyone here "
        f"made. What this does NOT establish is WHO placed it — only that no "
        f"name-keyed channel here did. It is still always-on context, and the "
        f"hook will never remove it, because the hook removes only what its "
        f"record proves it installed.")
        for name, declared in sorted(foreign.items())]


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
             account: Set[str] = frozenset(), repo_owned: Set[str] = frozenset(),
             store_state: str = PRESENT, surface: str = DURABLE,
             foreign: Dict[str, str] = None,
             ) -> Tuple[List[Row], List[Finding], List[Finding]]:
    """One row per directory on disk, plus the findings and notes they imply.

    A finding is something a human has to decide about. A note is something the
    next bootstrap will handle by itself — reported because "the hook is about to
    delete this" is worth seeing, not because anything is wrong.

    `surface` is what separates those two for a locked skill that is simply not
    here. It is the same fact on both kinds of machine and the opposite verdict:
    correct on a durable one, where the marketplace is authoritative, and a
    delivery failure on an ephemeral one, where the hook is the only channel
    there is. Defaults to DURABLE because that is the reading under which this
    stays quiet, and a diagnostic should need evidence to raise a finding rather
    than evidence to withhold one.

    Every judgement of the form "this should not be here" or "this is missing"
    needs BOTH sides: the lock says what was expected, the record says who put
    what there. With no readable lock there is no expectation to fall short of,
    so those findings are withheld rather than fabricated against an empty set —
    otherwise a missing lock reports the entire store as stale.

    `account` and `repo_owned` are the names the OTHER two channels deliver. They
    are here only so that "not in the personal store" does not get reported as
    "not delivered": both of those channels satisfy a locked name without the
    hook installing anything.

    `foreign` is `foreign_names`' reading, passed in rather than measured here
    because the note it carries is store-wide (see `foreign_notes`). It changes
    the origin COLUMN only where the name is not in this lock: a lock that names
    the directory is about to have the hook overwrite it, and that consequence
    is real whatever the frontmatter says, so `hand-placed-over-locked` keeps
    priority over the label.
    """
    rows: List[Row] = []
    findings: List[Finding] = []
    notes: List[Finding] = []
    attributable = record.state == PRESENT
    expected = lock.state == PRESENT
    foreign = {} if foreign is None else foreign

    for name in names:
        entry = record.entries.get(name) if attributable else None
        in_lock = name in lock.names

        if entry is None:
            if name in foreign and not in_lock:
                # The `untracked` finding this replaces is withheld rather than
                # softened, and that is the whole change: the finding asks the
                # reader to account for a directory, and there is no account to
                # give for one the surface placed. `foreign_notes` says so once,
                # store-wide.
                rows.append(Row(name, FOREIGN, None, None, None, in_lock))
                continue
            origin = UNATTRIBUTED if attributable else UNKNOWN
            rows.append(Row(name, origin, None, None, None, in_lock))
            if not expected:
                continue
            if origin == UNATTRIBUTED and not in_lock:
                findings.append(Finding(
                    "untracked", name,
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
                    "is recognised and reported as a `foreign` NOTE instead "
                    "when its SKILL.md declares a name other than its own "
                    "basename; this one does not, so that evidence is absent "
                    "here rather than the cause being ruled out."))
            elif origin == UNATTRIBUTED:
                findings.append(Finding(
                    "hand-placed-over-locked", name,
                    "the lock names it and the record does not name it, so "
                    "nothing establishes it as the hook's — and it will not "
                    "survive the next session "
                    "start, which replaces the directory with the registry's "
                    "copy, or removes it outright if the project ships a skill "
                    "of the same name. Move it if you want to keep it."))
            elif not in_lock:
                # Same directory, same consequence, weaker evidence: without a
                # record nothing can say who installed it, and the hook removes
                # only what it can show it installed. Reported anyway — staying
                # silent here would mute the doctor exactly where it knows least.
                findings.append(Finding(
                    "untracked", name,
                    "not named by the lock, and there is no readable record to "
                    "say whether the hook installed it. Nothing will remove it "
                    "either way: the hook only removes what it can prove it put "
                    "there. The four causes the attributable case lists apply "
                    "here too, the surface having seeded it among them — and "
                    "with no record, even 'the hook installed it' cannot be "
                    "ruled out."))
            continue

        measured = digest_skill_dir(skills_dir / name)
        if measured is None:
            integrity = UNMEASURABLE
        elif measured == entry.digest:
            integrity = UNCHANGED
        else:
            integrity = EDITED
        rows.append(Row(name, HOOK, entry.registry, entry.bundle, integrity, in_lock))

        if not expected:
            continue
        in_scope = (entry.registry, entry.bundle) in lock.claims
        if integrity != UNCHANGED and in_lock:
            findings.append(Finding(
                f"{integrity}-and-locked", name,
                f"{_cause(integrity)} and the lock still names it. The directory "
                f"does not survive the next session start, so any local change "
                f"here is lost without a prompt."))
        elif not in_lock and not in_scope:
            # Scope is checked BEFORE integrity, because out of scope the planner
            # short-circuits to `keep` without ever consulting the digest — so the
            # edited-and-stale verdict degrade below simply does not happen, and
            # promising it would send the reader looking for a signal the hook
            # never emits.
            findings.append(Finding(
                "stale-out-of-scope", name,
                f"left the lock, and the lock no longer declares the bundle "
                f"{entry.bundle!r} at the registry it came from. Removal is "
                f"scoped to what the lock claims, so nothing here will ever "
                f"clean it up — and the hook does not mention it either, because "
                f"it is not in scope to have an opinion."))
        elif integrity != UNCHANGED:
            findings.append(Finding(
                f"{integrity}-and-stale", name,
                f"{_cause(integrity)} and it has left the lock. The hook leaves "
                f"it in place and degrades its verdict for as long as that holds "
                f"— but what preserves it is the MISMATCH, not having left the "
                f"lock: restore the original bytes and the next run removes it. "
                f"Move it out of the store to keep it."))
        elif not in_lock:
            notes.append(Finding(
                "stale", name,
                "left the lock, is untouched since install, and its registry and "
                "bundle are still declared — the next bootstrap removes it. "
                "Unless AGENTSKILLS_BUNDLE narrows that run away from its "
                "bundle, which this cannot see from here: a narrowed run claims "
                "authority over one bundle and leaves the rest alone."))

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
            if missing in repo_owned:
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


def record_findings(record: Record, record_path: Path) -> List[Finding]:
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
            "`skills:` verdict, which names what stopped it."))
    if record.skipped:
        findings.append(Finding(
            "record-entries-skipped", str(record_path),
            f"{record.skipped} entry/entries do not match the shape the hook "
            f"accepts, so the hook skips them. Those installs are invisible to "
            f"the prune: it can never remove them, whatever the lock says."))
    return findings


def shadow_findings(skills_dir: Path, names: List[str], account: Set[str]
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
            notes.append(Finding(
                "shadowed-by-the-account-store", name,
                f"{both} The two could NOT be compared: {unread} could not be "
                f"read, so whether they hold the same instructions is unmeasured "
                f"rather than confirmed. {clocks}"))
            continue

        if exact_mine == exact_theirs:
            sameness = "The two copies are byte-identical"
        else:
            norm_mine = digest_shared_payload(mine, fold=True)
            norm_theirs = digest_shared_payload(theirs, fold=True)
            if norm_mine is None or norm_theirs is None:
                notes.append(Finding(
                    "shadowed-by-the-account-store", name,
                    f"{both} The two differ byte-for-byte and could not be "
                    f"re-compared with line endings normalised, so whether that "
                    f"difference is only CRLF-vs-LF is unmeasured. {clocks}"))
                continue
            if norm_mine != norm_theirs:
                findings.append(Finding(
                    "shadow-copies-differ", name,
                    f"{both} And the two are NOT the same content: they still "
                    f"differ once CRLF line endings are folded to LF "
                    f"({norm_mine[:12]} here, {norm_theirs[:12]} in the account "
                    f"store). So the model is reading one of two different sets "
                    f"of instructions under this name and nothing records which. "
                    f"{clocks} The usual cause is the account copy being behind — "
                    f"a skill edited and re-locked, and never re-uploaded. "
                    f"Compare them with the account-drift procedure in "
                    f"skills-doctor's SKILL.md, then either re-upload or accept "
                    f"the drift deliberately."))
                continue
            sameness = ("The two copies are byte-identical once CRLF line "
                        "endings are folded to LF — the account store is CRLF "
                        "where the registry is LF")

        notes.append(Finding(
            "shadowed-by-the-account-store", name,
            f"{both} {sameness}, so which one wins does not change what the "
            f"model reads TODAY. That is a property of this moment and not of "
            f"the design. {clocks} Reported so the shadow is a measured "
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


def lock_findings(lock: Lock, lock_path: Path) -> List[Finding]:
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
            f"it with scripts/generate_skills_lock.py.")]
    if lock.state == UNREADABLE:
        return [Finding(
            "lock-unreadable", str(lock_path),
            "the file is there and is not valid JSON, so the hook cannot read "
            "it either: it installs nothing and reports DEGRADED at every "
            "session start. Regenerate it with scripts/generate_skills_lock.py.")]
    return []


def render(record: Record, record_path: Path, skills_dir: Path,
           results: List[LockResult], findings: List[Finding],
           notes: List[Finding], stamped: List[Tuple[str, float]],
           store_state: str = PRESENT,
           surface: Surface = Surface(DURABLE, "", "", False)) -> str:
    """The verdict line first, then the evidence behind it."""
    rows = results[0].rows if results else []
    hook_rows = [row for row in rows if row.origin == HOOK]
    unattributed = [row for row in rows if row.origin == UNATTRIBUTED]
    foreign_rows = [row for row in rows if row.origin == FOREIGN]
    declared: Set[str] = set()
    for result in results:
        declared |= result.lock.names
    # Counted from the locks, not from the findings: a locked name absent from
    # the store is a finding on an ephemeral surface and a note on a durable one,
    # and the headline count must not change with that judgement.
    missing = declared - {row.name for row in rows}
    out = [
        f"provenance: {len(rows)} on disk, "
        f"{_tally(record.state, len(rows), len(hook_rows), len(unattributed), len(foreign_rows))}, "
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


def _tally(state: str, total: int, hook: int, unattributed: int,
           foreign: int = 0) -> str:
    """The attribution counts, or the one honest count when there are none.

    Without a readable record nothing is attributable, and printing
    "0 hook-installed, 0 unattributed" beside "3 on disk" reads as a
    contradiction — or worse, as "the store is empty".

    `foreign` is subtracted from the unattributable count rather than added
    beside it, and is counted in BOTH branches. Those rows are not
    unattributable: the disagreement between a directory's frontmatter name and
    its own basename is a measurement the record plays no part in, so it is
    readable on a machine with no record at all. Leaving them inside the other
    count would print a headline whose parts do not sum to "N on disk" — the
    same arithmetic that makes "0 hook-installed" beside "3 on disk" unreadable,
    which is the defect this function exists for.
    """
    tail = f", {foreign} foreign" if foreign else ""
    if state != PRESENT:
        return f"{total - foreign} unattributable (no readable record){tail}"
    return f"{hook} hook-installed, {unattributed} unattributed{tail}"


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
    repo_owned = skill_names(project_dir / ".claude" / "skills")
    foreign = foreign_names(skills_dir, names)

    # One store, judged once per declared expectation. See `LockResult`: the
    # locks are deliberately not merged first.
    results: List[LockResult] = []
    for lock_path in discover_locks(args.lock, project_dir):
        lock = read_lock(lock_path)
        rows, findings, notes = classify(
            skills_dir, names, record, lock, account=account,
            repo_owned=repo_owned, store_state=store_state, surface=surface[0],
            foreign=foreign)
        tagged = [finding._replace(lock=str(lock_path))
                  for finding in lock_findings(lock, lock_path) + findings]
        results.append(LockResult(
            lock_path, lock, rows, tagged,
            [note._replace(lock=str(lock_path)) for note in notes]))

    # Store-wide, like `store_findings` and for its reason: the collision is a
    # property of the two stores, not of any lock, so raising it per lock would
    # report one shadowed name N times in a multi-repo session.
    shadow_raised, shadow_noted = shadow_findings(skills_dir, names, account)

    here, user, children = hook_wiring(project_dir)
    hook_raised, hook_noted = hook_findings(
        project_dir, here, user, children,
        any(result.lock.state == PRESENT for result in results), surface[0])
    findings = dedupe(
        hook_raised
        + shadow_raised
        + store_findings(store_state, skills_dir)
        + record_findings(record, record_path)
        + [finding for result in results for finding in result.findings])
    notes = dedupe(hook_noted
                   + shadow_noted
                   + foreign_notes(foreign)
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
