#!/usr/bin/env bash
# skills-bootstrap.sh — install the canonical skill registry at session start,
# so an ephemeral Claude surface (cloud session, CI runner, container) gets the
# fleet's skills WITHOUT any repo vendoring a copy of them.
#
# What the surrounding evidence establishes (docs/experiments/E2-*.md):
#   * A SessionStart hook that copies skill DIRECTORIES into ~/.claude/skills
#     makes them visible to the model for turn one, on every Claude Code
#     surface including cloud. Verified twice.
#   * `claude plugin install` is NOT a substitute (measurement C1): it succeeds
#     and the skills are still absent from that same session, because
#     `reloadSkills` re-scans skill directories, not installed plugins. So this
#     hook copies directories and never shells out to the plugin CLI.
#   * Personal ~/.claude/skills SHADOWS a project's .claude/skills (C3). That
#     is the collision hazard the guard below exists for: repo-owned wins.
#   * Subagents do not fire this hook but DO inherit what it installed (C7), so
#     there is deliberately no per-subagent logic here.
#
# Surface-aware by design: on a developer's own machine the marketplace plugin
# install is authoritative and writing the same skills into ~/.claude/skills
# would double-load them, so this is a no-op unless the session is ephemeral.
#
# Pinned and verified: what to install comes from `skills.lock` — an immutable
# commit SHA plus a per-skill sha256 — never from `main`, and never from the
# environment by default. Fetching instruction text at session start is a
# supply-chain surface; an unpinned, unverified fetch is the whole risk.
#
# Nothing this hook fetches is ever EXECUTED. It downloads instruction text and
# hashes it; it does not run a single line of it. That is why the digest is
# implemented inline below (`digest_dir`) instead of being loaded out of a
# fetched registry's `scripts/generate_skills_lock.py`: that lookup made the
# integrity check itself attacker-supplied — with the primary unreachable (a
# condition anyone on the network path can induce) a federated source, of a
# different owner and possibly shipping zero locked skills, supplied the code
# that decides whether a digest matches. It wrote into $HOME and echoed each
# skill's expected sha256 straight back out of the lock, so tampered content
# installed under a clean `skills: N/N … OK` verdict.
#
# The cost of removing that is a SECOND copy of the digest algorithm, which the
# original design deliberately avoided: an independently written copy is exactly
# the class of bug that produces an "expected" number nobody can explain. So the
# copy is not independent — it is ten lines of hashlib mirroring
# generate_skills_lock.py's `digest_skill_dir` line for line, with
# `test_the_hooks_digest_agrees_with_the_generators_on_a_tricky_skill`
# asserting the two agree on a non-trivial fixture. A hook that runs at SessionStart with NO approval
# prompt must not execute code it just downloaded; ten lines of hash under an
# equality test is a far smaller risk than remote code execution.
#
# The same claim would be empty without `-I`, which is why EVERY python3 below
# carries it. Python puts the process's cwd on sys.path, and this hook's cwd is
# the session's PROJECT DIRECTORY: a project shipping a `hashlib.py` beside its
# skills.lock supplied the sha256 the integrity check compares against, so a
# lock naming an attacker's registry with a bogus `0000…` digest installed
# attacker content under a clean `skills: 1/1 … — OK`. A `json.py` or `re.py`
# shadow replaces the lock READER the same way — validation and all. `-I`
# (isolated mode: no cwd or script dir on sys.path, no user site, PYTHON*
# environment ignored) is what makes "it hashes it" mean THIS python's hashlib.
# It has existed since Python 3.4, so it is safe on every platform this targets,
# and it costs nothing here: these snippets import stdlib only, and the ordinary
# environment variables they read ($SKILLS_VERDICT, $LOCK_PATH, $OUT_DIR) still
# arrive through os.environ — -I ignores PYTHON* variables and nothing else.
# Preferred over -P, which is 3.11+.
#
# One lock, possibly several registries. The lock's `registry`/`ref` are the
# PRIMARY source; an optional `sources` array federates bundles that live in
# their own repo (cms-platform's, which keeps them at `skills/` rather than
# this repo's `plugins/<bundle>/skills`). Each source is pinned, fetched and
# verified separately, and one unreachable registry degrades only its own
# skills — the rest of the session still gets the rest of the fleet.
#
# ...and possibly SEVERAL LOCKS. A Claude Code on the web session opened on more
# than one repo sets the project directory to their PARENT (ADR 0005), so the
# locks that session was started against all sit one level down and the parent
# has none. Reading only the parent's delivered nothing at all there. So
# discovery is the project's own lock if it has one, else every immediate CHILD
# directory's — and the session ends up with the UNION of what each single-repo
# session would have delivered.
#
# A union of valid inputs must not be invalid, which is what shapes the rules
# below: routing is validated PER LOCK (two sibling repos both declaring the
# bundle `adam` is the ordinary fleet shape, not a collision), a registry pinned
# at one ref by fourteen repos is fetched ONCE, and a skill fourteen locks agree
# on installs once rather than reading as fourteen duplicates. A lock that
# cannot be read degrades only its own skills, for the same reason an
# unreachable registry does: with discovery, the number of locks read is no
# longer the adopting repo's to control.
#
# What it installs, it also REMOVES when the lock stops naming it — driven by a
# RECORD of this hook's own installs (~/.claude/skills/.skills-bootstrap-installed.json),
# never by "delete whatever I do not recognise": that directory is the user's
# own, and the claude.ai account-sync channel writes into it too. See "skills
# that have LEFT the lock" near the bottom for the four conditions a removal
# needs and the one contention case this deliberately does not resolve.
#
# Fails SOFT within its own execution: every failure path below emits a verdict
# naming the exact file or binary at fault and exits 0, because a hook that
# exits non-zero can block a session. The guarantee stops at the environment
# bash starts in — `BASH_ENV` is sourced before line one here, so a `BASH_ENV`
# that exits 7 gives rc=7, empty stdout, no verdict, and `PATH` picks which
# `bash` runs at all (#72). A precondition, not an open bug: whoever sets either
# can equally replace this file or `.claude/settings.json`. Stated because
# "this hook can never block a session" is false, and gets designed against.
set -uo pipefail
cat >/dev/null || true   # drain the hook's stdin JSON

HOOK_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
# The repo that ships this hook (…/<repo>/.claude/hooks/ → <repo>), resolved so
# the paths named in a failure verdict are ones a reader can paste.
SELF_ROOT="$(cd -- "$HOOK_DIR/../.." >/dev/null 2>&1 && pwd -P)" || SELF_ROOT="$HOOK_DIR/../.."

# The clause a bail-out appends when it CANNOT know which destinations the lock
# names — because the lock is missing, unreadable, or was rejected. Everywhere
# else, `purge_locked_destinations` makes the disclaimer unnecessary by making
# it true; here the honest thing is to say so, because "could not read the lock"
# otherwise reads as "so nothing is installed".
LEFT_IN_PLACE="; any previously-installed skills in ~/.claude/skills were LEFT IN PLACE (this run never read a lock, so it cannot say which of them are stale)"

# emit <verdict> — print the SessionStart payload and exit 0.
#
# The verdict always leads with the literal token `skills:` so it is greppable
# from a transcript, and it is the whole "readily knowable" contract: whoever
# reads it must be able to tell installed-and-verified from degraded, and if
# degraded, which knob fixes it. A verdict is therefore ALWAYS printed: the
# python encoder is attempted, and anything at all going wrong there falls
# through to the printf branch rather than leaving stdout empty.
emit () {
  # json.dumps, not string concatenation: the verdict can carry names read out
  # of the lock file, and hand-built JSON is how a stray quote turns a fail-soft
  # notice into malformed hook output.
  #
  # ensure_ascii + an explicit .encode("ascii") onto stdout.buffer, NOT `print`:
  # every verdict here contains an em-dash, and `print` encodes through a text
  # layer whose encoding the ENVIRONMENT picks — under `PYTHONIOENCODING=ascii`
  # it raised UnicodeEncodeError, stdout stayed empty, and the hook still exited
  # 0, so the "readily knowable" contract silently produced nothing at all.
  #
  # ensure_ascii=True is the half that carries the weight, and the only half a
  # test can isolate: it rewrites the em-dash as a 7-bit backslash-u escape, so
  # what leaves json.dumps is pure ASCII and NO output layer can fail on it.
  # Writing bytes is then a redundant second guard (it skips the text layer
  # entirely), and `-I` is a third — it implies -E, so PYTHONIOENCODING no
  # longer reaches this process at all. Redundant, and kept: each covers the
  # others being changed, which is exactly why replacing the byte write with
  # `print` cannot be made to fail on its own. Do not read that as it being
  # free to remove.
  #
  # `-I` on every python3 the hook runs: see the header. $SKILLS_VERDICT is an
  # ordinary variable, so it still arrives through os.environ — -I ignores
  # PYTHON* and nothing else, which is what keeps this from falling through to
  # the printf branch below on a machine that sets PYTHONPATH.
  if command -v python3 >/dev/null 2>&1 \
     && SKILLS_VERDICT="$1" python3 -I -c '
import json, os, sys
payload = json.dumps({
    "reloadSkills": True,
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "reloadSkills": True,
        "additionalContext": os.environ["SKILLS_VERDICT"],
    },
}, ensure_ascii=True)
sys.stdout.buffer.write(payload.encode("ascii") + b"\n")
sys.stdout.buffer.flush()'; then
    exit 0
  fi
  # Fallback: no python3, or the encoder failed. A verdict must still be
  # printed, and it is no longer guaranteed to be a fixed literal, so escape
  # the two bytes that can break a JSON string and flatten the control
  # characters a path could theoretically carry. Backslash first — escaping it
  # after the quote would double-escape what the quote rule just inserted.
  local safe
  safe="$1"
  safe="${safe//\\/\\\\}"
  safe="${safe//\"/\\\"}"
  safe="${safe//$'\n'/ }"
  safe="${safe//$'\r'/ }"
  safe="${safe//$'\t'/ }"
  printf '{"reloadSkills":true,"hookSpecificOutput":{"hookEventName":"SessionStart","reloadSkills":true,"additionalContext":"%s"}}\n' "$safe"
  exit 0
}

# join_names <name>... — ", "-joined list for the verdict.
join_names () {
  local out="" item
  for item in "$@"; do
    if [ -z "$out" ]; then out="$item"; else out="$out, $item"; fi
  done
  printf '%s' "$out"
}

# plural <count> <singular> <plural>
plural () { if [ "$1" -eq 1 ]; then printf '%s' "$2"; else printf '%s' "$3"; fi; }

# --- surface guard: ephemeral sessions only --------------------------------
# The verdict NAMES the two values this decision was made from, because "durable
# session" on its own is indistinguishable from a MISCLASSIFIED remote surface,
# and that ambiguity is what kept the exact-match `!= "remote"` test's fragility
# invisible: $CLAUDE_CODE_ENTRYPOINT has at least six `remote_*` spellings
# (`remote_desktop` among them), none of which this test matches. Printing the
# inputs does not change the decision — widening the guard is held pending a
# measurement on a durable Cowork machine, since `remote_cowork` may well BE
# durable — it just makes a wrong one legible to whoever reads the transcript.
if [ -z "${CLAUDE_CODE_REMOTE_SESSION_ID:-}" ] && [ "${CLAUDE_CODE_ENTRYPOINT:-}" != "remote" ] \
   && [ -z "${SKILLS_BOOTSTRAP_FORCE:-}" ]; then
  emit "skills: skipped — durable session (entrypoint=${CLAUDE_CODE_ENTRYPOINT:-unset}, no remote session id), marketplace install is authoritative"
fi

# --- locate the lock(s) ----------------------------------------------------
# The project directory Claude Code named is the whole search, and it is not
# always a repo: a session opened on several repos sets it to their PARENT
# (ADR 0005), where the only locks in the session sit one level down. So:
#
#   own lock ->              [$CLAUDE_PROJECT_DIR/skills.lock]
#   else, project dir set -> every immediate CHILD DIRECTORY's, sorted
#   else (dir UNSET) ->      [$SELF_ROOT/skills.lock], if it is a file
#
# CHILDREN of the project dir, never SIBLINGS of anything, and one level only.
# A sibling scan reaches whatever happens to sit beside the project — a scratch
# clone, a vendored checkout, another customer's tree — and lets a directory
# nobody attached to this session decide what gets written into $HOME;
# recursing reaches `node_modules`. Children-of-the-named-project-dir is also
# exactly the shape the multi-repo session has.
#
# Mirrors `discover_locks` in the skills-doctor's check_provenance.py — own lock
# wins, one level, directories only — deliberately, so the hook and the
# diagnostic that judges it cannot disagree about what a session contains.
#
# The $SELF_ROOT fallback applies ONLY when CLAUDE_PROJECT_DIR is UNSET. When a
# session names a project dir, that dir plus its immediate children is the whole
# search and a lockless project is a project that did not opt in — falling back
# to the hook's own repo there would install THIS registry's skills into every
# session that merely borrowed the file. The fallback is for the invocation that
# names no project at all (a hand run, a harness that does not set it), which is
# the sequencing constraint that makes a user-scope wiring of this hook safe to
# write: such a wiring fires everywhere, and everywhere-without-a-lock has to
# install nothing.

# resolve_lock_path <path> — <path> with its DIRECTORY canonicalised.
#
# Resolution rather than plain string handling is what makes a symlinked project
# dir and the directory it points at read as ONE place: the verdict then names a
# path the reader can paste, not the spelling the environment happened to use.
resolve_lock_path () {
  local raw="$1" dir base resolved
  dir="$(dirname -- "$raw")"
  base="$(basename -- "$raw")"
  if resolved="$(cd -- "$dir" >/dev/null 2>&1 && pwd -P)"; then
    printf '%s/%s' "$resolved" "$base"
  else
    # Nothing to canonicalise — the directory does not exist. Keep the literal
    # path so the verdict still names something the reader can act on.
    printf '%s' "$raw"
  fi
}

# How many discovered locks are read. The measured multi-repo session had 14
# repos, so a cap anywhere near MAX_SOURCES would break the real shape; what
# this bounds is a project directory with hundreds of children. Over the cap the
# first $MAX_LOCKS in sorted order are read and the rest are NAMED in the
# verdict — a cap that is not silent. A dropped lock contributes no rows and no
# claims, so nothing it once installed becomes a prune candidate, which is the
# safe direction of the same rule as a rejected lock.
#
# Deliberately NOT spelled `MAX_SOURCES`: that constant is the PER-LOCK
# federation cap, it is read back out of this file by the doctor's
# `test_the_source_limits_are_the_hooks_own` with `^MAX_SOURCES = `, and it must
# keep meaning exactly what the generator's copy of it means.
MAX_LOCKS=32

LOCKS=()
SEARCHED=()
DROPPED_LOCKS=()
SCOPE=""
# Set when the child enumeration could not be completed — see the block below.
scan_incomplete=""

if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then
  own="$(resolve_lock_path "$CLAUDE_PROJECT_DIR/skills.lock")"
  SEARCHED+=("$own")
  if [ -f "$own" ]; then
    LOCKS=("$own")
  else
    # A DISCOVERY SCAN THAT MISSES A REPO MUST NOT MAKE THAT REPO'S SKILLS
    # REAPABLE. This is the AGENTSKILLS_BUNDLE rule one level up: narrowing
    # means "install a subset", never "this subset is now the whole truth". If
    # enumeration is incomplete the run discovers FEWER locks, `claims.nul`
    # shrinks, and the previously-installed skills of the repo that was missed
    # stop having an owner in this run — so the orphan prune at the bottom would
    # delete them. A transient permissions error must not reap a repo.
    #
    # So an enumeration that cannot be completed suppresses the prune for this
    # run (by claiming authority over nothing) and SAYS so, exactly as an
    # unreadable install record already does. Installing on partial information
    # is fine; deleting on it is not.
    #
    # DETECTED, not assumed: a project dir this hook cannot read or search, and
    # a child directory it cannot search — the permission shapes. A dangling or
    # looping symlink is deliberately NOT counted: `*/` never matches one, and a
    # path that cannot be followed cannot be holding a lock, so that absence is
    # knowable rather than uncertain. A clean scan that finds ZERO child locks
    # is likewise not an error — it is the ordinary no-lock case below.
    if [ ! -r "$CLAUDE_PROJECT_DIR" ] || [ ! -x "$CLAUDE_PROJECT_DIR" ]; then
      scan_incomplete="$CLAUDE_PROJECT_DIR cannot be read or searched"
    fi
    # `*/` matches directories only, and bash sorts a glob (LC_COLLATE) — the
    # ORDER is load-bearing, not tidiness: the install record lets the last
    # writer win and deliberately carries no timestamp, so two identical runs
    # that visited the locks in different orders would write different bytes.
    #
    # dotglob because the doctor's copy of this rule uses `iterdir()`, which
    # sees dotted directories; bash's `*` skips them unless it is on. `.` and
    # `..` stay excluded either way, so nothing can walk upward.
    dotglob_was_on=0
    shopt -q dotglob && dotglob_was_on=1
    shopt -s dotglob
    children=0
    for child in "$CLAUDE_PROJECT_DIR"/*/; do
      [ -d "$child" ] || continue
      children=$((children + 1))
      if [ ! -x "$child" ] || [ ! -r "$child" ]; then
        scan_incomplete="${child%/} cannot be read or searched"
        continue
      fi
      [ -f "$child/skills.lock" ] || continue
      if [ "${#LOCKS[@]}" -lt "$MAX_LOCKS" ]; then
        LOCKS+=("$(resolve_lock_path "$child/skills.lock")")
      else
        DROPPED_LOCKS+=("$(resolve_lock_path "$child/skills.lock")")
      fi
    done
    [ "$dotglob_was_on" -eq 1 ] || shopt -u dotglob
    SCOPE="; also searched the $children immediate child $(plural "$children" directory directories) of $CLAUDE_PROJECT_DIR"
    # Said HERE too, not only in the problem clause far below: this branch can
    # end at the no-lock verdict, which exits, and "searched 0 children" would
    # otherwise read as a clean scan that found nothing rather than as a scan
    # that could not be made.
    if [ -n "$scan_incomplete" ]; then
      SCOPE="$SCOPE (INCOMPLETE: $scan_incomplete)"
    fi
  fi
else
  own="$(resolve_lock_path "$SELF_ROOT/skills.lock")"
  SEARCHED+=("$own")
  if [ -f "$own" ]; then LOCKS=("$own"); fi
  SCOPE="; CLAUDE_PROJECT_DIR is unset, so only the repo that ships this hook was searched"
fi

if [ "${#LOCKS[@]}" -eq 0 ]; then
  emit "skills: DEGRADED — no skills.lock found, looked in $(join_names "${SEARCHED[@]}") (generate it with scripts/generate_skills_lock.py)$SCOPE$LEFT_IN_PLACE"
fi

LOCK="${LOCKS[0]}"
LOCK_DIR="$(cd -- "$(dirname -- "$LOCK")" >/dev/null 2>&1 && pwd -P)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$LOCK_DIR}"
# Resolved, because the collision guard below compares it against lock
# directories that WERE resolved, and a project dir reached through a symlink
# would otherwise read as a second, different repo.
PROJECT_DIR_REAL="$(cd -- "$PROJECT_DIR" >/dev/null 2>&1 && pwd -P)" || PROJECT_DIR_REAL="$PROJECT_DIR"

# What a verdict calls "the lock". With one there is a file to name, and every
# single-lock verdict says exactly what it has always said; with several there
# is no single file and listing them is the honest replacement.
if [ "${#LOCKS[@]}" -gt 1 ]; then
  LOCK_LABEL="$(join_names "${LOCKS[@]}")"
else
  LOCK_LABEL="$LOCK"
fi

# AGENTSKILLS_REPO / AGENTSKILLS_REF address THE primary source. With N locks
# there are N primaries: applying the override to one is a guess about which,
# and applying it to all silently repoints every attached repo at one registry
# and one ref — which is how a debug knob turns into an unpinned session-wide
# substitution. They are debug knobs, so an ambiguous case is refused rather
# than resolved. With exactly one lock nothing changes.
override=""
if [ -n "${AGENTSKILLS_REPO:-}" ]; then override="AGENTSKILLS_REPO"; fi
if [ -n "${AGENTSKILLS_REF:-}" ]; then
  if [ -n "$override" ]; then override="$override and AGENTSKILLS_REF"; else override="AGENTSKILLS_REF"; fi
fi
if [ -n "$override" ] && [ "${#LOCKS[@]}" -gt 1 ]; then
  emit "skills: DEGRADED — $override is set and this session discovered ${#LOCKS[@]} locks ($(join_names "${LOCKS[@]}")); that override names ONE lock's primary registry and there is no single primary here (unset it, or start a session on one repo)$LEFT_IN_PLACE"
fi

# --- prerequisites ---------------------------------------------------------
# Only the two the hook needs to READ the lock and to know where it installs.
# `git` is checked later, immediately before the fetch that needs it, so that
# its failure path can still purge the destinations the lock names — a
# prerequisite check that fires before the lock is read cannot.
if ! command -v python3 >/dev/null 2>&1; then
  emit "skills: DEGRADED — python3 not found on PATH (needed to read the lock and verify digests; install python3)$LEFT_IN_PLACE"
fi
if [ -z "${HOME:-}" ]; then
  emit "skills: DEGRADED — HOME is unset (needed to locate ~/.claude/skills; export HOME)$LEFT_IN_PLACE"
fi

# Known this early because every failure path from here on has to be able to
# REMOVE from it. Deliberately not created yet: a lock rejected at the trust
# boundary must leave no trace at all, not an empty ~/.claude/skills.
DEST="$HOME/.claude/skills"

tmp="$(mktemp -d)" || emit "skills: DEGRADED — could not create a temp directory (check ${TMPDIR:-/tmp})$LEFT_IN_PLACE"
trap 'rm -rf "$tmp"' EXIT

# A FIXED path in a world-writable directory, appended to with `>>`, is a file
# anyone can pre-create as a symlink and have this hook write through — and the
# hook appends plenty, including git's own stdout/stderr, which a cloned
# registry influences via `remote:` lines. `mktemp` gives an unpredictable name
# with no pre-existing target to follow. It sits OUTSIDE $tmp on purpose: $tmp
# is removed on exit, and the verdicts name $LOG for a reader to go and read
# after the session has started.
LOG="$(mktemp "${TMPDIR:-/tmp}/skills-bootstrap.XXXXXX")" || LOG="$tmp/skills-bootstrap.log"

# --- read + validate every discovered lock ---------------------------------
# Parsed with python3's stdlib json — never a hand-rolled shell parser — and
# every field that reaches a URL or a filesystem path is validated here, in one
# place, before bash ever sees it. Env overrides are applied on the same side
# of that boundary so they get the identical validation.
#
# A lock may name SEVERAL registries: `registry`/`ref`/`bundles` are the
# primary source, and an optional `sources` array adds federated ones (a bundle
# that lives in its own repo, on its own cadence — cms-platform's). Every one
# of them goes through the same validation below, because validating only the
# primary is precisely how the extra ones become the hole.
#
# ONE invocation for ALL discovered locks, never one per lock: a row's `index`
# field points into a GLOBAL source array, so concatenating per-lock streams
# would resolve every row from the second lock onward against the first lock's
# registries — installing one repo's bytes under another's name, with the only
# tripwire being the range check further down reporting the wrong cause.
#
# `-I` (isolated): see the header. Without it the project directory is on
# sys.path, and a `json.py` or `re.py` sitting there IS this reader's parser and
# validator.
# TWO spellings per lock, and the reason is a real one rather than belt and
# braces. Under Git Bash the shell's paths are MSYS ones (`/c/Users/...`, and
# `/tmp` is wherever the mount table says), which a NATIVE Windows python
# cannot open. `LOCK_PATH` survives that only because MSYS rewrites the values
# of environment variables when it spawns a native process — and it does NOT
# rewrite the CONTENT of a file. So the moment the lock list moved into
# `locks.nul` every Windows session started reporting
# `could not read <lock> (invalid JSON or a bad field)` with an ENOENT in the
# log, on a file sitting right there: 105 tests, one cause.
#
# `cygpath -m` is the deterministic answer — it asks the very shell the hook is
# running under, so it tracks the real mount table instead of guessing that
# `/c/` means `C:\` (which would be wrong for `/tmp` alone). Off Windows there
# is no cygpath and nothing to translate.
#
# The SECOND field is the spelling bash itself uses. Python echoes it back in
# `accepted.nul`, `rejected.nul` and the conflict notices, so every path that
# reaches a verdict, or is compared against `PROJECT_DIR_REAL` by the collision
# guard, is the one bash would have printed anyway. Converting on the way in and
# labelling on the way out is what keeps that one-directional.
CYGPATH="$(command -v cygpath 2>/dev/null || true)"
python_path () {
  if [ -n "$CYGPATH" ]; then "$CYGPATH" -m -- "$1" 2>/dev/null || printf '%s' "$1"
  else printf '%s' "$1"; fi
}
: >"$tmp/locks.nul"
for lock_path in "${LOCKS[@]}"; do
  printf '%s\0%s\0' "$(python_path "$lock_path")" "$lock_path" >>"$tmp/locks.nul"
done
if ! LOCK_PATH="$LOCK" LOCKS_PATH="$tmp/locks.nul" OUT_DIR="$tmp" python3 -I - <<'PY' >>"$LOG" 2>&1
import json, os, re, sys

out_dir = os.environ["OUT_DIR"]
only_bundle = os.environ.get("AGENTSKILLS_BUNDLE") or ""

# The locks to read. `LOCKS_PATH` is a NUL-framed file bash writes — a path may
# contain anything except a NUL, so nothing a discovered directory is NAMED can
# forge an extra entry.
#
# TWO fields per record: the path to OPEN and the path to SAY. They differ only
# under Git Bash, where the shell's `/c/Users/...` is not something a native
# Windows python can open — see the comment on `python_path` above for why a
# file's content does not get the rewrite an environment variable's value does.
# Opening one spelling and reporting the other is what keeps every path in a
# verdict, and every path the collision guard compares, in bash's own spelling.
#
# `LOCK_PATH` remains the SINGLE-LOCK spelling and is what this falls back to,
# which is what keeps this reader liftable: scripts/test_generate_skills_lock.py
# extracts it and runs it with only LOCK_PATH, OUT_DIR and PATH in the
# environment, and some forty validation tests ride on that. In that shape the
# two spellings are necessarily the same one.
lock_paths = []
lock_labels = []
locks_path = os.environ.get("LOCKS_PATH") or ""
if locks_path:
    with open(locks_path, encoding="utf-8", newline="") as handle:
        fields = handle.read().split("\0")
    if fields and fields[-1] == "":
        fields.pop()                    # the NUL terminating the final field
    for at in range(0, len(fields) - 1, 2):
        lock_paths.append(fields[at])
        lock_labels.append(fields[at + 1])
if not lock_paths:
    lock_paths = [os.environ["LOCK_PATH"]]
    lock_labels = list(lock_paths)

# Where a bundle's skills sit inside its own repo. This repo's shape is the
# default; a federated source keeps them wherever it keeps them.
DEFAULT_LAYOUT = "plugins/{bundle}/skills"
SOURCE_FIELDS = ("registry", "ref", "bundles", "layout")

# The four trust-boundary patterns. They are byte-identical to the generator's
# (_NAME_RE / _REF_RE / _URL_RE / _CONTROL_RE in generate_skills_lock.py), and
# test_the_two_validators_accept_the_same_set proves neither copy drifts: a lock
# the generator writes, this reader must accept, and vice-versa.
#
#   * NAME requires a LEADING ALPHANUMERIC, so a bundle or skill name can never
#     be '.' or '..' — the key 'adam/..' that made $name '..' and turned
#     `cp -R "$src" "$DEST/$name"` into a write to $HOME/.claude.
#   * URL is a real URL charset, not merely 'starts with https://'.
#   * CONTROL rejects EVERY whitespace/control byte in a registry, ref or
#     layout. It is DEFENCE IN DEPTH, not the fix for anything: the framing
#     forgery it was written for — a TAB or NEWLINE inside one field creating
#     extra records or shifting columns in the old positional TSV — is closed by
#     the NUL framing below, which no field's CONTENT can forge. And every
#     charset it guards (URL / NAME / REF, and the layout segment class) is
#     already ASCII-only, so removing this check today still rejects TAB, LF,
#     NBSP and SPACE. It becomes load-bearing only if one of those charsets is
#     ever widened — so widen one and you are relying on this line; do not
#     assume it is covering you before then.
NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
REF = r"[A-Za-z0-9._/+:@-]+"
URL = r"(?:https|file)://[A-Za-z0-9._~:/?#@%!$&()*+,;=\[\]-]+"
CONTROL = re.compile(r"[\s\x00-\x1f\x7f]")
# Each source is fetched at SESSION START, so the list length is a stall
# multiplier: an unbounded one is an unbounded delay before the model can be
# used. Generous next to any real lock (this repo's has none; a consumer
# federating cms-platform has one).
#
# `skills` carries no equivalent cap, and that asymmetry is a decision rather
# than an oversight: a source costs a NETWORK ROUND TRIP before the session
# starts — unbounded in wall-clock time no matter how short the list — whereas a
# row costs only local work over a lock this repo already committed, linear and
# measurable (thousands of rows are seconds; twenty thousand, a 1.7MB lock, is
# tens of seconds). A cap also needs a number knowable in advance, and a
# legitimate bundle's row count is not one, so a row cap could only refuse an
# honest large lock — and authoring a lock big enough to matter already means
# write access to skills.lock, which costs far more than session-start latency.
#
# PER LOCK, and unchanged by the union: it counts FEDERATED sources (`extra`)
# only, it is spelled exactly once, and its literal text is what binds this file
# to the generator (test_hook_and_generator_share_the_source_cap) and to the
# skills-doctor (test_the_source_limits_are_the_hooks_own, which reads it back
# with `^MAX_SOURCES = ` and takes the FIRST match). A whole-run cap needs a
# different name, which is the next constant.
MAX_SOURCES = 8
# The whole-run cap, on DEDUPED fetches rather than on declared sources.
#
# It exists because MAX_SOURCES and FETCH_BUDGET were calibrated against each
# other on the one-lock assumption, and discovery breaks that arithmetic:
# eleven locks may each legally declare a primary plus eight federated sources,
# so 11 x (1 + 8) = 99 fetches are a VALID input to one 60-second FETCH_BUDGET,
# inside the 90-second hook timeout .claude/settings.json sets. Nothing in
# FETCH_BUDGET stops that — it stops the run, which means the locks late in the
# scan silently get nothing and no verdict has words for why.
#
# So the cap is on the deduped list, which is what makes it affordable at all:
# fourteen repos pinning agentskills@eb25bd6 are ONE clone. Over the cap, the
# rows that would need a further fetch are dropped and NAMED, never silently
# truncated.
MAX_FETCHES = 16


class LockRejected(Exception):
    """One lock is unusable. Every OTHER discovered lock still installs.

    Every condition that used to `sys.exit` the whole run raises this instead,
    with the identical message text — several tests read those strings back out
    of $LOG, and for the operator that message is the only statement of which
    field is wrong (the verdict renders one fixed sentence for any failure here).

    A rejected lock contributes NO rows and NO claims, and that is what keeps the
    fail-closed refusals fail-closed at one lock's width. The `synced` refusal is
    the one to check this against: it works by keeping that name out of
    `skills.nul` BEFORE the stream exists, so neither destructive consumer — the
    install loop's `rm -rf` nor `purge_locked_destinations` — can ever see it.
    Scoping it to its own lock preserves that exactly; it does not weaken it.

    Narrowing a previously run-wide refusal is deliberate, and this is the
    argument for it: discovery means the number of locks read is no longer the
    adopting repo's to control, so without per-lock degradation any one of
    fourteen attached repos could deny delivery to the other thirteen — a
    fragility the union would itself have introduced.
    """


def _no_control(value, where):
    if CONTROL.search(value):
        raise LockRejected("lock: %s must not contain whitespace or control characters" % where)


def remote_url(registry, where):
    """Validate a registry and return the git remote URL it stands for.

    A git remote is not an inert string: `ext::sh -c ...` is a remote helper
    git will happily execute. So restrict it to shapes we intend — OWNER/REPO
    on GitHub, or an explicit https:// / file:// URL matching a real URL charset
    (not merely 'starts with https://'). file:// exists so the hook's own tests
    can run against a local fixture with no network.
    """
    if not isinstance(registry, str) or not registry:
        raise LockRejected("lock: %s must be OWNER/REPO or an https:// / file:// URL" % where)
    _no_control(registry, where)
    if "://" in registry:
        if not re.fullmatch(URL, registry):
            raise LockRejected("lock: %s must be an https:// or file:// URL" % where)
        return registry
    if re.fullmatch(NAME + "/" + NAME, registry):
        return "https://github.com/%s.git" % registry
    raise LockRejected("lock: %s must be OWNER/REPO or an https:// / file:// URL" % where)


def clean_ref(ref, where):
    if not isinstance(ref, str) or not ref:
        raise LockRejected("lock: %s is missing or not a plausible git ref" % where)
    _no_control(ref, where)
    if not re.fullmatch(REF, ref):
        raise LockRejected("lock: %s is missing or not a plausible git ref" % where)
    return ref


def clean_layout(layout, where):
    """Validate a layout template and return it.

    A layout is joined onto the root of a tree this hook has just fetched from
    somewhere else, so it is the one source field that becomes a filesystem
    path: an absolute path or a '..' segment reads outside that tree entirely.
    '{bundle}' is the only placeholder that means anything — an unknown one
    survives substitution and names a directory that cannot exist, reporting
    the whole bundle as 'not installed' with nothing saying why.
    """
    if not isinstance(layout, str) or not layout:
        raise LockRejected("lock: %s must be a non-empty string" % where)
    _no_control(layout, where)
    literal = layout.replace("{bundle}", "")
    if "{" in literal or "}" in literal:
        raise LockRejected("lock: %s may only use the '{bundle}' placeholder" % where)
    for segment in layout.split("/"):
        if segment in ("", ".", "..") or not re.fullmatch(r"[A-Za-z0-9._{}-]+", segment):
            raise LockRejected("lock: %s must be a relative path with no '..' segment" % where)
    return layout


def read_lock(lock_path):
    """Validate ONE lock and stage its rows. Everything in here is per lock.

    Returns (sources, rows, claim): this lock's own source list, its rows as
    (key, digest, name, relpath, index-into-THIS-lock's-sources), and its own
    routing map. The caller merges; nothing here knows another lock exists.

    THE `claim` MAP MUST STAY PER LOCK. Two sibling repos both declaring the
    bundle `adam` is the ordinary fleet shape — it is what every consumer lock
    says — and merging their routing into one dict trips the
    "claimed by two sources" refusal below, rejecting the whole union. A union of
    individually valid inputs must not be invalid, and this is the one place
    where a plausible-looking simplification makes it so.
    """
    try:
        with open(lock_path, encoding="utf-8") as handle:
            lock = json.load(handle)
    except (OSError, ValueError) as exc:
        # A traceback here used to be survivable because there was one lock and
        # the run was over either way. With several it would take the whole
        # session's delivery down with it, so an unparseable file becomes this
        # lock's rejection like any other. A genuine BUG in this reader still
        # crashes loudly — only the shapes named here are caught.
        raise LockRejected("lock: could not be parsed as JSON (%s)" % exc)
    if not isinstance(lock, dict):
        raise LockRejected("lock: the top level must be an object")

    skills = lock.get("skills") or {}
    if not isinstance(skills, dict):
        raise LockRejected("lock: 'skills' must be an object")

    # The primary source. Env overrides sit on this side of the trust boundary
    # so they get the identical validation; they address the primary only, which
    # is the source they have always meant. Bash refuses the run outright when
    # either is set and more than one lock was discovered, so "the primary" is
    # never ambiguous by the time it is read here.
    primary = os.environ.get("AGENTSKILLS_REPO") or lock.get("registry") or ""
    sources = [{
        "name": primary,
        "ref": clean_ref(os.environ.get("AGENTSKILLS_REF") or lock.get("ref") or "", "'ref'"),
        "url": remote_url(primary, "'registry'"),
        "layout": DEFAULT_LAYOUT,
    }]

    extra = lock.get("sources")
    if extra is None:
        extra = []
    if not isinstance(extra, list):
        raise LockRejected("lock: 'sources' must be a list")
    if len(extra) > MAX_SOURCES:
        raise LockRejected("lock: 'sources' lists %d entries; at most %d are allowed — every one "
                           "is fetched before the session starts" % (len(extra), MAX_SOURCES))

    # ROUTING IS TOTAL: every bundle a skill row names is claimed by exactly one
    # source. The primary claims exactly its top-level `bundles` — at its own
    # index 0, before any federated source is read — and each extra source
    # claims exactly its own. Claimed by NOBODY, or by more than one, is a lock
    # error; there is no default index 0 to fall through to.
    #
    # Total rather than defaulted because THIS is the side that consumes a lock
    # authored elsewhere (a consumer carrying only the hook and a lock never runs
    # the generator's --check at all), so a lock whose routing has two readings
    # must be REFUSED, not resolved by a default that happens to look safe. The
    # default was not safe: `claim.get(bundle, 0)` sent an unclaimed bundle to
    # the primary silently, and seeding `claim` only from what the primary
    # explicitly listed meant a federated source could claim `adam` unopposed
    # whenever that list was omitted, empty or naming something else — every
    # adam/* row then fetched from the other repo under a clean
    # `skills: 1/1 … OK`. Requiring the primary's list the same way each extra
    # source's is already required (non-empty, real names) is what closes the
    # omitted/empty forms; the collision check below closes the form where the
    # primary still lists it. What remains — a source explicitly claiming a
    # bundle the primary does not list — is a single-reading route the lock
    # states outright and the verdict names the registry for, which is the most a
    # routing rule can do: it is structurally identical to the federation this
    # file exists to support.
    primary_bundles = lock.get("bundles")
    if not isinstance(primary_bundles, list) or not primary_bundles or not all(
            isinstance(bundle, str) and re.fullmatch(NAME, bundle) for bundle in primary_bundles):
        raise LockRejected(
            "lock: 'bundles' must be a non-empty list of bundle names — it is what "
            "says which bundles come from 'registry', and nothing is assumed for it")
    claim = {bundle: 0 for bundle in primary_bundles}

    for position, raw in enumerate(extra, start=1):
        where = "sources[%d]" % position
        if not isinstance(raw, dict):
            raise LockRejected("lock: %s must be an object" % where)
        # Unknown keys are an ERROR, not ignored — the same rule the generator
        # enforces. The hook is what consumes a lock authored elsewhere, so a
        # 'commit'/'branch' key added believing it pins something, read by
        # nothing, is precisely the appears-to-say-vs-is gap the lock exists to
        # close.
        unknown = sorted(set(raw) - set(SOURCE_FIELDS))
        if unknown:
            raise LockRejected(
                "lock: %s: unknown key(s) %s; a source carries exactly %s"
                % (where, ", ".join(repr(key) for key in unknown), ", ".join(SOURCE_FIELDS)))
        bundles = raw.get("bundles")
        if not isinstance(bundles, list) or not bundles or not all(
                isinstance(bundle, str) and re.fullmatch(NAME, bundle) for bundle in bundles):
            raise LockRejected("lock: %s.bundles must be a non-empty list of bundle names" % where)
        for bundle in bundles:
            # `len(sources)` is the index this source is about to take, so a
            # source listing the same bundle twice is not a collision with
            # itself.
            prior = claim.get(bundle)
            if prior is not None and prior != len(sources):
                held = sources[prior]["name"] or ("'registry'" if prior == 0 else "sources[%d]" % prior)
                taking = raw.get("registry")
                if not isinstance(taking, str) or not taking:
                    taking = where
                raise LockRejected(
                    "lock: bundle %r is claimed by two sources, %s and %s; a bundle has "
                    "one registry and one layout" % (bundle, held, taking))
            claim[bundle] = len(sources)
        sources.append({
            "name": raw.get("registry") or "",
            "ref": clean_ref(raw.get("ref"), where + ".ref"),
            "url": remote_url(raw.get("registry") or "", where + ".registry"),
            "layout": clean_layout(raw.get("layout") or DEFAULT_LAYOUT, where + ".layout"),
        })

    rows = []
    for key in sorted(skills):
        digest = skills[key]
        if not re.fullmatch(NAME + "/" + NAME, key):
            raise LockRejected("lock: skill key %r is not '<bundle>/<skill>'" % key)
        # BOTH shapes, normalised to bare hex. A committed lock of bare 64-hex
        # values trips gitleaks' `generic-api-key` rule: a keyword-bearing skill
        # basename (`secrets`, `oauth`, `token`, `api-…`) is the keyword, and the
        # digest clears the 3.5 entropy gate. `sha256:` defuses it because `:`
        # sits outside the rule's capture class, cutting the capture to 6
        # characters — under its 10-character floor. Entropy is not a lever (only
        # ~0.02% of real digests fall below the gate) and a green lock today can
        # flip red on a content change, so the value's SHAPE is the only durable
        # fix.
        #
        # DO NOT DELETE THIS AS DEAD CODE because the lock in front of you is
        # bare hex. This tolerance ships FIRST and must reach every consumer
        # BEFORE the generator emits the prefix: the hook is pinned by sha256 in
        # `_agent-guidance/repos.yml`, so a consumer that receives a prefixed lock
        # while still carrying an intolerant hook rejects the whole lock and every
        # ephemeral session there reports `skills: DEGRADED`. Both shapes stay
        # readable for as long as any consumer might be running an older pin.
        #
        # Normalising HERE is what keeps this a one-site change: the bare hex
        # flows into `skills.nul`, and from there into the `[ "$got" = "$want" ]`
        # integrity check (`digest_dir` always prints bare hex) and into the
        # install record — so both record readers' own `[0-9a-f]{64}` still
        # match, and neither a re-install loop nor a purge loop can be opened by a
        # prefixed lock. It is also what makes the CROSS-LOCK comparison below
        # sound: this repo's lock is `sha256:`-prefixed and eight consumer locks
        # are bare hex, so comparing raw values would read every shared skill as
        # a conflict and install none of them.
        #
        # `isinstance` FIRST, and no `str()` coercion: a JSON *number* of exactly
        # 64 decimal digits stringifies into this very character class, so
        # `str(digest)` would hand the pattern something that matches and the
        # bogus value would flow on into the install loop's
        # `rm -rf "$DEST/$name"` — deleting a skill this hook had already
        # installed and verified. Before the normalisation above existed the same
        # int reached the record writer, crashed the reader, and the lock landed
        # on the "could not read $LOCK" verdict, which carries $LEFT_IN_PLACE and
        # removes nothing. Refusing every non-string here keeps that fail-closed
        # outcome rather than trading it for a destructive one.
        matched = (re.fullmatch(r"(?:sha256:)?([0-9a-f]{64})", digest)
                   if isinstance(digest, str) else None)
        if not matched:
            raise LockRejected("lock: skill %r has no sha256 digest" % key)
        digest = matched.group(1)
        bundle, name = key.split("/", 1)
        # `synced/` is the claude.ai account-sync channel's own directory inside
        # $DEST. Nothing this hook installs is ever called that, so a lock naming
        # it is wrong by construction — and that store is the ONLY channel
        # reaching claude.ai chat, Cowork, Claude in Chrome and mobile, with no
        # delete or restore API behind it. Losing it is total and unrecoverable.
        #
        # Refused HERE because `skills.nul` — which this reader has not written
        # yet — feeds TWO destructive consumers, and the name reaches both:
        #   * the install loop, which does `rm -rf "$DEST/$name"` then `cp -R`,
        #     so it reports `skills: n/n … — OK` while annihilating the account
        #     store;
        #   * purge_locked_destinations, which removes every destination the lock
        #     NAMES — on an unreachable source it deletes the store, installs
        #     nothing, and its verdict never says which name it took.
        # Raising before either stream exists is what covers both at once.
        #
        # FAIL-CLOSED — the WHOLE lock, not a skipped row like the `dup` status
        # below. That branch is only survivable because the install loop has
        # ALREADY run `rm -rf "$DEST/$name"` by the time it skips; for this one
        # name that removal is the whole harm, so a row-skip cannot be the
        # answer. Under discovery "the whole lock" means THIS lock: its names
        # never reach `skills.nul`, which is the identical protection at one
        # lock's width, while the other repos in the session still get theirs.
        # One bad upstream directory name therefore costs that repo every skill
        # in its lock — and scripts/generate_skills_lock.py refuses to WRITE such
        # a lock, so sanctioned tooling cannot produce one in the first place.
        #
        # Sits above the AGENTSKILLS_BUNDLE filter for the reason the routing
        # check below spells out, with a poisoned row in place of an unroutable
        # one.
        if name == "synced":
            raise LockRejected(
                "lock: skill %r would install over ~/.claude/skills/synced, the "
                "claude.ai account-sync directory — this hook never installs a skill "
                "by that name, and that store is not its to replace or delete; rename "
                "the skill directory in the registry that ships it" % key)
        # Checked BEFORE the AGENTSKILLS_BUNDLE filter below: narrowing a session
        # to one bundle must not be able to hide an unroutable row in the rest of
        # the lock. A bundle nobody claims has no registry, no ref and no layout,
        # so there is nothing to resolve it against — say so and refuse the lock.
        if bundle not in claim:
            raise LockRejected(
                "lock: skill %r names bundle %r, which no source claims; list it in the "
                "top-level 'bundles' or in a source's 'bundles' — this hook does not "
                "guess which registry a bundle comes from" % (key, bundle))
        if only_bundle and bundle != only_bundle:
            continue
        index = claim[bundle]
        relpath = sources[index]["layout"].replace("{bundle}", bundle) + "/" + name
        rows.append((key, digest, name, relpath, index))
    return sources, rows, claim


# --- merge every accepted lock ---------------------------------------------
# `sources` is the DEDUPED fetch list — what `sources.nul` holds and what a row's
# `index` points into. Deduping on (url, ref) is what makes discovery affordable:
# fourteen repos pinning one registry at one ref are ONE clone, not fourteen.
#
# LAYOUT IS NOT PART OF THE KEY, and that is deliberate rather than an
# oversight: `relpath` is already resolved per row above (layout + bundle +
# name), and bash uses SRC_LAYOUT only for a log line, so one checkout serves
# rows with different layouts perfectly well. `remote_url` has already
# normalised `OWNER/REPO` into its https URL, so two spellings of one registry
# dedupe too.
sources = []
fetch_at = {}
staged = []
claims = set()
accepted = []
rejected = []
# (kind, subject, detail) for the verdict's own problem clauses. Kept as its own
# stream rather than folded into `skills.nul` because that record's five fields
# are a byte-level contract with every `read -r -d ''` chain that walks it — in
# bash and in the prune planner alike — and a sixth field would mean changing all
# of them to carry something none of them acts on.
notices = []

for lock_index, lock_path in enumerate(lock_paths):
    # OPEN `lock_path`, SAY `lock_labels[lock_index]`. Everything that leaves
    # this reader as text is the label, so bash never has to translate a path
    # back out of a spelling only python could produce.
    label = lock_labels[lock_index]
    try:
        local_sources, rows, claim = read_lock(lock_path)
    except LockRejected as exc:
        rejected.append((label, str(exc)))
        # To stderr as well as to `rejected.nul`: stderr is $LOG, which is where
        # every verdict sends the operator, and it is the only surface that ever
        # states WHICH field was wrong.
        sys.stderr.write("%s (%s)\n" % (exc, label))
        continue
    # Only a lock that fully validated contributes anything at all.
    accepted.append(label)
    # EVERY source this lock declares gets a fetch slot, needed by a row or not.
    # That is today's behaviour and it is load-bearing in two directions: a
    # pinned registry nobody can reach is worth reporting even when this run
    # wanted nothing from it (the "no locked skill needed it" clause exists for
    # exactly that), and `fetched` is what the all-sources-unreachable bail-out
    # counts — build the list from ROWS instead and a lock narrowed to zero rows
    # produces an empty source list, no fetches at all, and a bail-out that says
    # "could not fetch" with nothing named after it.
    local_index = {}
    for at, source in enumerate(local_sources):
        fetch_key = (source["url"], source["ref"])
        if fetch_key not in fetch_at:
            if len(sources) >= MAX_FETCHES:
                continue        # no slot: its rows are dropped and named below
            fetch_at[fetch_key] = len(sources)
            sources.append(source)
        local_index[at] = fetch_at[fetch_key]
    # Two rows for one destination INSIDE one lock is an authoring error the
    # generator refuses to write and ADR 0001 forbids, and it stays an error
    # whatever the digests say — which is why it is counted here, per lock,
    # rather than falling out of the cross-lock rule below.
    local_count = {}
    for _key, _digest, name, _relpath, _index in rows:
        local_count[name] = local_count.get(name, 0) + 1
    for key, digest, name, relpath, index in rows:
        if index not in local_index:
            notices.append(("dropped", key, "%s@%s" % (local_sources[index]["name"],
                                                       local_sources[index]["ref"])))
            continue
        staged.append((lock_index, key, digest, local_index[index], relpath, name,
                       local_count[name] > 1))
    # The (registry, bundle) pairs the ACCEPTED locks declare — the exact scope
    # in which the orphan prune at the bottom is allowed to delete. Written from
    # `claim`, the routing map, rather than from rows, so that a bundle a lock
    # still declares but has emptied of skills reaps its old skills instead of
    # silently keeping them: no row would name that bundle at all.
    #
    # The UNION across accepted locks, which is what dissolves the documented
    # contention WITHIN a session: one run that sees every lock reaps nothing
    # another lock still wants.
    #
    # NARROWED WITH THE ROWS, and that is the decision AGENTSKILLS_BUNDLE turns
    # on: narrowing means "install a subset", never "this subset is now the whole
    # truth". A run narrowed to one bundle claims authority over that bundle
    # only, so a debug flag can never reap the bundles it deliberately skipped.
    #
    # The planner reaches that same answer one step earlier — it short-circuits
    # an out-of-scope entry into a NAMED "narrowed" outcome before it ever
    # consults this set — so dropping this filter alone changes no behaviour
    # today, and no test can isolate it. Kept for two reasons anyway: it is what
    # makes the stream MEAN what its name says (the scope this run claims), and
    # it is the guard left standing if that branch is ever reordered or dropped.
    # Reverting BOTH is what reaps another bundle, and that is the mutation the
    # narrowing test fails on.
    for bundle in sorted(claim):
        if only_bundle and bundle != only_bundle:
            continue
        claims.add((local_sources[claim[bundle]]["url"], bundle))

# --- cross-lock destination arbitration ------------------------------------
# The install dir is FLAT, so one destination name is one directory with one
# owner. Three sub-cases, and conflating them breaks the ordinary input:
#
#   (a) benign     same name, same digest, DIFFERENT locks -> ONE install row
#   (b) conflict   same name, DIFFERENT digests            -> install NEITHER
#   (c) intra-lock one lock contributed two rows for it    -> as today, `dup`
#
# Formally: a name installs iff every row naming it agrees on one normalised
# digest AND no single lock contributed more than one row for it.
#
# (a) IS THE COMMON CASE — the fleet's locks largely declare the same `adam`
# bundle at the same ref — so collapsing has to happen BEFORE anything counts
# names. A union that counted first would mark every shared skill a duplicate,
# `rm -rf` its destination and install nothing: `skills: 0/N` for the ordinary
# multi-repo session, which is worse than the bug this change fixes.
#
# (b) answers ADR 0005's open question, relocated from "which run wins over
# time" to "which row wins within one run". NEITHER. Serving bytes under a name
# whose own lock names different bytes breaks the integrity guarantee this file
# exists for, and there is no non-invented tiebreak between two pinned locks.
# Failing closed is PER NAME, so when two refs differ only in the skills that
# actually changed, every unchanged skill still unions cleanly — and it never
# regresses, because today a multi-repo session delivers nothing at all.
by_name = {}
for row in staged:
    by_name.setdefault(row[5], []).append(row)

emitted = []
for name in sorted(by_name):
    group = sorted(by_name[name], key=lambda row: (row[0], row[1]))
    intra = any(row[6] for row in group)
    digests = {row[2] for row in group}
    if intra or len(digests) > 1:
        # ONE status for both, and one arm in the install loop: what the loop has
        # to DO is identical — remove the destination, install neither row — and
        # a second arm would be a second `rm -rf` in the most destructive loop in
        # this file for no behavioural difference. What differs is the REMEDY, so
        # (b) additionally emits a notice naming the locks that disagree and the
        # verdict reports the two apart; (c) is one lock to correct, (b) is two
        # to reconcile.
        if not intra:
            notices.append(("conflict", name,
                            ", ".join(sorted({lock_labels[row[0]] for row in group}))))
        for row in group:
            emitted.append(((row[1], row[0]), (row[1], row[2], str(row[3]), row[4], "dup")))
    else:
        # Case (a): one row, the first in (lock_index, key) order.
        first = group[0]
        emitted.append(((first[1], first[0]),
                        (first[1], first[2], str(first[3]), first[4], "install")))

# Sorted by lock key, which for a single lock is exactly `sorted(skills)` — the
# order this reader has always emitted — and for several is still a total order
# that two identical runs reproduce byte for byte.
emitted.sort(key=lambda item: item[0])
rows = [record for _sort, record in emitted]

# Not one lock could be read. Bash renders its existing whole-run "could not read
# the lock" verdict, which carries $LEFT_IN_PLACE and runs no purge — so exiting
# BEFORE any stream is written is what makes that verdict true.
if not accepted:
    sys.exit("lock: none of the %d discovered lock(s) could be read" % len(lock_paths))


# NUL-DELIMITED framing, plus a record count the bash side cross-checks. Every
# field written here was validated control-char-free above, so none can contain
# a NUL: the number of records and the number of fields per record are fixed by
# the writer and cannot be changed by any field's CONTENT — unlike the earlier
# positional, newline/tab-split TSV, where one hostile value forged whole rows.
def _write_records(path, records):
    # newline="" on every writer that bash reads back: python's text mode
    # translates "\n" to os.linesep, so on Windows the separators became CRLF
    # and bash read a trailing "\r" as part of the field. That is what made
    # `meta` fail its `^[0-9]+$` check and report a bogus framing error on
    # every Windows session. These files are a byte-level contract with bash;
    # the writer decides what is in them, not the platform.
    with open(path, "w", encoding="utf-8", newline="") as handle:
        for record in records:
            for field in record:
                handle.write(field)
                handle.write("\0")


_write_records(
    os.path.join(out_dir, "sources.nul"),
    [(s["name"], s["url"], s["ref"], s["layout"]) for s in sources],
)
_write_records(os.path.join(out_dir, "skills.nul"), rows)
_write_records(os.path.join(out_dir, "claims.nul"), sorted(claims))
# Which locks contributed, and which did not and why. The verdict needs both:
# `23/23` out of a multi-repo session says nothing about who supplied it, and a
# lock silently contributing nothing is the failure this whole file argues
# against.
_write_records(os.path.join(out_dir, "accepted.nul"), [(path,) for path in accepted])
_write_records(os.path.join(out_dir, "rejected.nul"), rejected)
_write_records(os.path.join(out_dir, "notices.nul"), notices)
# The verifiable python<->bash contract: the counts bash must read back. If
# bash reads a different number of COMPLETE records, the stream was truncated
# or desynced and every skill's source index is suspect.
with open(os.path.join(out_dir, "meta"), "w", encoding="utf-8",
          newline="") as handle:  # LF only -- see _write_records
    handle.write("%d\n%d\n%d\n%d\n" % (len(sources), len(rows), len(accepted), len(rejected)))
PY
then
  emit "skills: DEGRADED — could not read $LOCK_LABEL (invalid JSON or a bad field; regenerate it with scripts/generate_skills_lock.py, details in $LOG)$LEFT_IN_PLACE"
fi

# A discovery scan that could not be completed claims authority over NOTHING.
# `claims.nul` is the scope the orphan prune deletes in, so emptying it turns
# every recorded install into a "keep" — the run still installs what it found and
# deletes nothing, which is the safe direction. See the enumeration block above
# for why a missed repo must not have its skills reaped.
if [ -n "$scan_incomplete" ]; then
  : >"$tmp/claims.nul"
fi

# --- the lock's names are known from here on -------------------------------
# purge_locked_destinations — remove every install destination the lock names.
#
# The invariant: a verdict reporting a failure to install must not leave the
# PREVIOUS run's unverified bytes live in ~/.claude/skills for the model to load
# on turn one. The install loop already removed a destination on every path that
# skips a skill — but the bail-outs ABOVE it returned before any removal, so a
# seeded `alpha/SKILL.md` survived verbatim under "skills: DEGRADED — could not
# fetch …", which a reader takes to mean nothing was installed. Two of those
# bail-outs needed only an environment variable to reach.
#
# Reachable only once the lock has been READ — that read is what supplies the
# names. Above it the names are unknowable, and those verdicts say so
# ($LEFT_IN_PLACE) rather than implying a clean slate.
#
# Every removal is `${DEST:?}/$name` with $name re-validated here: these names
# arrive over the wire, and a destructive op must not trust one on its own. A
# '.'/'..'/empty or non-matching name is skipped, never removed — its
# `$DEST/$name` would be the install dir or its parent.
#
# ITS BLAST RADIUS WIDENED WITH THE UNION, and that is stated rather than gated.
# `skills.nul` is now the union across every accepted lock, so on a bail-out this
# removes every DISCOVERED repo's destinations, not just the one repo whose lock
# was read — ungated by provenance, exactly as before. Gating it is a separate
# argument with its own trade (the function exists so a verdict reporting a
# failed install cannot leave the previous run's UNVERIFIED bytes live, and its
# tests assert that ungated behaviour, provenance and all), so it is named as the
# next question rather than changed in the same breath as the union. Per-lock
# degradation already narrows it usefully: a REJECTED lock contributes no rows,
# so its names never enter this stream and are never purged.
purge_locked_destinations () {
  local key want index relpath status name
  [ -f "$tmp/skills.nul" ] || return 0
  while IFS= read -r -d '' key \
     && IFS= read -r -d '' want \
     && IFS= read -r -d '' index \
     && IFS= read -r -d '' relpath \
     && IFS= read -r -d '' status; do
    name="${key##*/}"
    case "$name" in "" | . | .. ) continue ;; esac
    if [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
      rm -rf "${DEST:?}/$name" >>"$LOG" 2>&1
    fi
  done < "$tmp/skills.nul"
}

# Checked HERE, not with python3/HOME above, so that its failure path is one the
# purge can run on: git is what the next section needs, and by now the lock has
# named every destination.
if ! command -v git >/dev/null 2>&1; then
  purge_locked_destinations
  emit "skills: DEGRADED — git not found on PATH (needed to fetch the registry; install git)"
fi

# --- fetch every source at its pinned ref ----------------------------------
# `git clone --depth 1 --branch <SHA>` FAILS with exit 128 ("Remote branch
# <sha> not found in upstream origin") — --branch takes a ref NAME, so a clone
# structurally cannot pin to a commit. Pinning needs an explicit init + fetch
# of the object. Branches and tags still take the clone path. One function, so
# a second source cannot grow a second, wronger copy of that rule.
#
# Bounded in time, because a hook that HANGS blocks the session at least as hard
# as one that exits non-zero — the very failure this file's header argues is the
# one to avoid — and `git` against a TCP tarpit blocks forever by default (still
# running after 45s with no output). The bound is one overall wall-clock BUDGET
# for all fetches together, not a per-fetch limit, so that N sources cannot
# multiply the stall.
#
# The budget is enforced by `run_git` below, and it holds WITHOUT `timeout(1)`.
# It did not use to: `timeout` is absent on stock macOS and in a minimal
# container — precisely the bash-3.2 platform this hook targets — and there the
# cap simply did not apply, so a remote that accepted TCP and then stalled the
# TLS/connect phase hung the hook past 150s. git's own stall detector
# (GIT_HTTP_LOW_SPEED_*, set below) is NOT the second guard that was claimed for
# it: it measures TRANSFER throughput, so it never fires before the first byte,
# and it covers https only. It is kept because it also catches a transfer that
# starts and then crawls, which the budget alone would let run to the full 60s.
#
# Blowing the budget degrades exactly like an unreachable registry: the verdict
# names the source, and the session still starts.
# Seconds, for ALL sources together. Generous next to a `--depth 1` fetch of a
# skills registry, small next to a session a human is waiting on.
FETCH_BUDGET=60
fetch_deadline=$(( SECONDS + FETCH_BUDGET ))
export GIT_HTTP_LOW_SPEED_LIMIT="${GIT_HTTP_LOW_SPEED_LIMIT:-1000}"
export GIT_HTTP_LOW_SPEED_TIME="${GIT_HTTP_LOW_SPEED_TIME:-20}"
HAVE_TIMEOUT=0
if command -v timeout >/dev/null 2>&1; then HAVE_TIMEOUT=1; fi

# run_git <args>... — git, capped at whatever is left of the fetch budget.
#
# `timeout(1)` when it is there: simpler, and precise. When it is NOT — stock
# macOS, minimal containers — the cap is enforced here instead, because a
# deadline that silently does not apply on half the platforms is not a deadline.
# The fallback only has to be correct:
#
#   * git runs in the BACKGROUND and `wait` blocks on it, so the normal path
#     pays no polling latency and reports git's real exit status.
#   * a watchdog subshell kills it when the budget expires. It signals the
#     PROCESS GROUP: `git fetch` does the network in a helper child
#     (git-remote-https/curl), and killing only the parent orphans the helper —
#     the stall would survive the thing meant to end it.
#   * `set -m` (job control) is what makes a group killable at all: without it a
#     background job shares the shell's own process group, and `kill -- -$pid`
#     would either fail or signal this hook. It is turned on only around the two
#     launches and restored, so nothing else in the file changes behaviour.
#
# The watchdog gets its own group for the same reason — killing the subshell
# alone would leave its `sleep` running — and its fds go to /dev/null so a
# lingering child can never hold the descriptor a caller is being read through.
#
# Every group signal is `|| kill <pid>`. That is the belt for a platform where
# job control cannot be enabled and the group therefore does not exist: the
# deadline still lands on git itself, and the only thing lost is the reaping of
# its helper. Degrading to a late orphan is acceptable; degrading to no deadline
# is what this whole function exists to prevent.
#
# Every git here runs with EOL translation OFF. This hook decides whether to
# install a skill by comparing a sha256 of the fetched bytes against the one
# the lock recorded, so what it checks out has to be what the blob holds. With
# `core.autocrlf=true` — the Windows default — git rewrites every LF on the way
# out, so a lock generated anywhere else disagrees with every clone made here
# and nothing is ever installed. The registry's own `.gitattributes` is not
# enough to rely on: a federated source may not carry one.
GIT_VERBATIM=(-c core.autocrlf=false -c core.eol=lf)

run_git () {
  local left=$(( fetch_deadline - SECONDS ))
  if [ "$left" -lt 1 ]; then left=1; fi
  if [ "$HAVE_TIMEOUT" -eq 1 ]; then
    timeout -k 5 "$left" git "${GIT_VERBATIM[@]}" "$@"
    return $?
  fi
  local monitor=0 git_pid watchdog status
  case "$-" in *m*) monitor=1 ;; esac
  set -m
  git "${GIT_VERBATIM[@]}" "$@" &
  git_pid=$!
  ( sleep "$left"
    kill -TERM -"$git_pid" 2>/dev/null || kill -TERM "$git_pid" 2>/dev/null
    sleep 5
    kill -KILL -"$git_pid" 2>/dev/null || kill -KILL "$git_pid" 2>/dev/null
  ) >/dev/null 2>&1 </dev/null &
  watchdog=$!
  [ "$monitor" -eq 1 ] || set +m
  wait "$git_pid"
  status=$?
  kill -TERM -"$watchdog" 2>/dev/null || kill -TERM "$watchdog" 2>/dev/null
  wait "$watchdog" 2>/dev/null
  return "$status"
}

fetch_source () {
  local dest="$1" url="$2" ref="$3"
  # </dev/null so a git child can never read this hook's own stdin (the
  # SessionStart JSON was drained at the top); keep git strictly
  # non-interactive regardless of surface.
  if [[ "$ref" =~ ^[0-9a-f]{40}$ ]]; then
    { run_git init -q "$dest" \
      && run_git -C "$dest" remote add origin "$url" \
      && run_git -C "$dest" fetch --depth 1 -q origin "$ref" \
      && run_git -C "$dest" checkout -q FETCH_HEAD; } >>"$LOG" 2>&1 </dev/null
  else
    run_git clone --depth 1 --branch "$ref" -q "$url" "$dest" >>"$LOG" 2>&1 </dev/null
  fi
}

# --- read the framed source + skill records --------------------------------
# Records are NUL-delimited (never split on a field's own bytes) and python
# states its record counts in `meta` up front, so bash can DETECT a desync
# rather than silently resolving a skill against the wrong tree. `read -r -d ''`
# reads one NUL-terminated field; a complete source record is four of them.
#
# Four counts, not two: the union added two streams that the verdict reads back
# (which locks contributed, and which were refused and why), and a stream with
# no declared count is one whose truncation nothing would notice.
if ! { read -r NSOURCES && read -r NSKILLS && read -r NACCEPTED && read -r NREJECTED; } < "$tmp/meta" \
   || ! [[ "$NSOURCES" =~ ^[0-9]+$ ]] || ! [[ "$NSKILLS" =~ ^[0-9]+$ ]] \
   || ! [[ "$NACCEPTED" =~ ^[0-9]+$ ]] || ! [[ "$NREJECTED" =~ ^[0-9]+$ ]]; then
  purge_locked_destinations
  emit "skills: DEGRADED — could not read $LOCK_LABEL (framing error; regenerate it with scripts/generate_skills_lock.py, details in $LOG)"
fi

# Which discovered locks contributed, and which were refused. Both are read
# before anything is fetched, because the collision guard below needs the
# accepted locks' own directories and every verdict from here on names them.
ACCEPTED=()
if [ -f "$tmp/accepted.nul" ]; then
  while IFS= read -r -d '' accepted_lock; do
    ACCEPTED+=("$accepted_lock")
  done < "$tmp/accepted.nul"
fi
if [ "${#ACCEPTED[@]}" -ne "$NACCEPTED" ]; then
  purge_locked_destinations
  emit "skills: DEGRADED — could not read $LOCK_LABEL (accepted-lock framing mismatch: $NACCEPTED declared, ${#ACCEPTED[@]} read; see $LOG)"
fi

REJ_LOCK=()
REJ_WHY=()
if [ -f "$tmp/rejected.nul" ]; then
  while IFS= read -r -d '' rejected_lock && IFS= read -r -d '' rejected_why; do
    REJ_LOCK+=("$rejected_lock"); REJ_WHY+=("$rejected_why")
  done < "$tmp/rejected.nul"
fi
if [ "${#REJ_LOCK[@]}" -ne "$NREJECTED" ]; then
  purge_locked_destinations
  emit "skills: DEGRADED — could not read $LOCK_LABEL (rejected-lock framing mismatch: $NREJECTED declared, ${#REJ_LOCK[@]} read; see $LOG)"
fi

# Advisory records the verdict renders as problem clauses: a destination two
# locks disagree about, and a row dropped because the run had already reached
# its fetch cap. Deliberately WITHOUT a declared count — nothing acts on them,
# so a truncated stream can only under-report, and a framing bail-out here
# would trade a complete install for a missing sentence.
CONFLICT_NAME=()
CONFLICT_LOCKS=()
DROPPED_ROW=()
if [ -f "$tmp/notices.nul" ]; then
  while IFS= read -r -d '' notice_kind \
     && IFS= read -r -d '' notice_subject \
     && IFS= read -r -d '' notice_detail; do
    case "$notice_kind" in
      conflict) CONFLICT_NAME+=("$notice_subject"); CONFLICT_LOCKS+=("$notice_detail") ;;
      dropped)  DROPPED_ROW+=("$notice_subject (from $notice_detail)") ;;
    esac
  done < "$tmp/notices.nul"
fi

# is_conflicted <name> — true when two LOCKS named different digests for it.
#
# A linear scan over a list that holds one entry per conflicting DESTINATION,
# which in any healthy session is empty: bash 3.2 (still what macOS ships) has
# no associative arrays, and this is the same trade the record lookup in
# `may_replace` already makes.
is_conflicted () {
  local subject="$1" at=0
  while [ "$at" -lt "${#CONFLICT_NAME[@]}" ]; do
    if [ "${CONFLICT_NAME[$at]}" = "$subject" ]; then return 0; fi
    at=$((at + 1))
  done
  return 1
}

# The directories a repo-owned skill can win from: the project dir, plus every
# ACCEPTED lock's own repo. In a multi-repo session the project dir is the
# PARENT of the repos and has no `.claude/skills` at all, so the guard that
# exists for the C3 shadowing hazard would be inert there — a repo shipping both
# a lock naming X and its own `.claude/skills/X/SKILL.md` has made the more
# specific declaration, and overwriting it in the flat personal dir is exactly
# what the guard is for.
#
# The project dir is excluded from this list rather than repeated in it, so the
# guard below can still say plainly which directory won.
REPO_OWNED_DIRS=()
if [ "${#ACCEPTED[@]}" -gt 0 ]; then
  for accepted_lock in "${ACCEPTED[@]}"; do
    accepted_dir="${accepted_lock%/*}"
    if [ "$accepted_dir" = "$PROJECT_DIR_REAL" ]; then continue; fi
    seen_dir=0
    if [ "${#REPO_OWNED_DIRS[@]}" -gt 0 ]; then
      for existing_dir in "${REPO_OWNED_DIRS[@]}"; do
        if [ "$existing_dir" = "$accepted_dir" ]; then seen_dir=1; break; fi
      done
    fi
    if [ "$seen_dir" -eq 0 ]; then REPO_OWNED_DIRS+=("$accepted_dir"); fi
  done
fi

# Parallel indexed arrays rather than one associative array: bash 3.2 (still
# what macOS ships) has no associative arrays, and the indices here are the
# source numbers python already assigned to every skill row.
SRC_NAME=()
SRC_URL=()
SRC_REF=()
SRC_LAYOUT=()
while IFS= read -r -d '' name \
   && IFS= read -r -d '' url \
   && IFS= read -r -d '' ref \
   && IFS= read -r -d '' layout; do
  SRC_NAME+=("$name"); SRC_URL+=("$url"); SRC_REF+=("$ref"); SRC_LAYOUT+=("$layout")
done < "$tmp/sources.nul"
# The verifiable python<->bash contract: python said how many sources it wrote;
# if bash did not read exactly that many COMPLETE 4-field records, the stream
# desynced and every skill's source index is now suspect. Refuse, don't guess.
if [ "${#SRC_NAME[@]}" -ne "$NSOURCES" ]; then
  purge_locked_destinations
  emit "skills: DEGRADED — could not read $LOCK_LABEL (source framing mismatch: $NSOURCES declared, ${#SRC_NAME[@]} read; see $LOG)"
fi

SRC_SHORT=()
SRC_OK=()
SRC_LOST=()      # ", "-joined skill names a failed source could not supply
SRC_LOST_N=()
fetched=0
unreachable=()
index=0
while [ "$index" -lt "${#SRC_NAME[@]}" ]; do
  name="${SRC_NAME[$index]}"
  url="${SRC_URL[$index]}"
  ref="${SRC_REF[$index]}"
  layout="${SRC_LAYOUT[$index]}"
  short="$ref"
  if [ "${#short}" -eq 40 ]; then short="${short:0:7}"; fi
  SRC_SHORT+=("$short"); SRC_LOST+=(""); SRC_LOST_N+=(0)
  if fetch_source "$tmp/reg-$index" "$url" "$ref"; then
    SRC_OK+=(1)
    fetched=$((fetched + 1))
  else
    SRC_OK+=(0)
    unreachable+=("${name}@${ref}")
  fi
  echo "source[$index] name=$name ref=$ref layout=$layout ok=${SRC_OK[$index]}" >>"$LOG"
  index=$((index + 1))
done

# One unreachable registry degrades only its own skills — the rest still
# install. All of them unreachable is the old single-source failure, and keeps
# its verdict: there is nothing to install and nothing further to say — but the
# purge is what makes "nothing to install" also mean "nothing is installed".
if [ "$fetched" -eq 0 ]; then
  purge_locked_destinations
  emit "skills: DEGRADED — could not fetch $(join_names "${unreachable[@]}") (network, or a bad ref in $LOCK_LABEL; see $LOG)"
fi

# --- the digest ------------------------------------------------------------
# digest_dir <dir> — print the sha256 digest of one installed skill directory.
#
# INLINE, and deliberately so: see the header. This hook never executes anything
# it fetched, and the previous design — look for `scripts/generate_skills_lock.py`
# beside the hook, then in the project, then in ANY fetched registry — handed the
# integrity check itself to whichever repo answered first. There is no $GEN
# lookup left to fall back through.
#
# This is a SECOND copy of the algorithm, which the original design avoided on
# the sound grounds that an independently written copy drifts. It is therefore
# not independent: it mirrors `digest_skill_dir` in
# scripts/generate_skills_lock.py line for line (see the module docstring's
# "The digest" section for the specification), and
# `test_the_hooks_digest_agrees_with_the_generators_on_a_tricky_skill` asserts
# the two produce the same digest for a non-trivial directory. Change one, change the other, and
# let that test say so.
digest_dir () {
  # `-I` (isolated): see the header. This is the invocation the isolation
  # matters most for — without it a `hashlib.py` in the project directory is the
  # hasher, and every digest in the lock verifies against whatever it says.
  python3 -I - "$1" 2>>"$LOG" <<'DIGEST_PY'
import hashlib, pathlib, sys

root = pathlib.Path(sys.argv[1]).resolve()
if not root.is_dir():
    sys.exit("not a directory: %s" % sys.argv[1])
entries = []
for candidate in root.rglob("*"):
    if not candidate.is_file():
        continue  # directories carry no bytes; broken symlinks carry none either
    entries.append((candidate.relative_to(root).as_posix(), candidate))
manifest = "".join(
    f"{relpath}\0{hashlib.sha256(file_path.read_bytes()).hexdigest()}\n"
    for relpath, file_path in sorted(entries, key=lambda entry: entry[0])
)
print(hashlib.sha256(manifest.encode("utf-8")).hexdigest())
DIGEST_PY
}

# --- install + verify ------------------------------------------------------
mkdir -p "$DEST" || { purge_locked_destinations; emit "skills: DEGRADED — could not create $DEST (check permissions on \$HOME)"; }

# --- what this hook may overwrite ------------------------------------------
# $DEST is the USER's directory. The orphan prune at the bottom has always known
# that — it removes a stale skill only when the install RECORD says this hook
# put it there and its bytes are still exactly what was installed. The install
# loop below did not: it opened by unconditionally `rm -rf`-ing its destination,
# before consulting anything, so a hand-placed ~/.claude/skills/<name> sharing a
# name with a locked skill was destroyed and the verdict still read
# `skills: N/N … — OK`. That is the shadowing hazard C3 documented on the
# personal-store side and never closed on this one.
#
# The bound on that blast radius today is an ACCIDENT — the hook does not fire
# in a multi-repo session (#84) — and every proposal that fixes #84 removes it.
# So the same test the prune uses is applied here, BEFORE the first removal:
# what this hook cannot show it installed, it does not overwrite either.
#
# Deliberately NOT extended to `purge_locked_destinations`: that function exists
# so a verdict reporting a failed install cannot leave the previous run's
# UNVERIFIED bytes live, and its tests assert exactly that, provenance and all.
# Gating it is a separate argument with its own trade, not part of this one.
RECORD="$DEST/.skills-bootstrap-installed.json"

# The (name, digest) pairs the record claims as this hook's own installs.
#
# Its exit status is deliberately IGNORED: a record that is missing (first run),
# unreadable, or malformed yields no stream, hence no attributions, hence an
# install loop that overwrites nothing — the safe direction of the same rule.
# The corruption itself is still reported, by the planner below, which fails on
# the identical file and sets $record_unreadable.
#
# `-I` (isolated): see the header. This decides which of the user's directories
# the loop below may `rm -rf`, so a `json.py` or `re.py` in the project dir must
# not be what reads and validates it.
RECORD_PATH="$RECORD" TMP_DIR="$tmp" python3 -I - >>"$LOG" 2>&1 <<'RECORDED_PY'
import json, os, re, sys

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
DIGEST = re.compile(r"[0-9a-f]{64}")

try:
    with open(os.environ["RECORD_PATH"], encoding="utf-8") as handle:
        record = json.load(handle)
except FileNotFoundError:
    record = {"installed": []}          # first run: nothing was ever recorded
except (OSError, ValueError):
    sys.exit(3)
if not isinstance(record, dict) or not isinstance(record.get("installed"), list):
    sys.exit(3)

rows = []
for entry in record["installed"]:
    if not isinstance(entry, dict):
        continue                        # one bad entry is skipped, not fatal
    name, digest = entry.get("name"), entry.get("digest")
    if not isinstance(name, str) or not isinstance(digest, str):
        continue
    # The lock reader's charsets, applied again on the way OUT of a file sitting
    # in a directory anyone with the user's shell can write. A name that cannot
    # be validated can never be shown safe to overwrite.
    if NAME.fullmatch(name) and DIGEST.fullmatch(digest):
        rows.append((name, digest))

with open(os.path.join(os.environ["TMP_DIR"], "recorded.nul"), "w",
          encoding="utf-8", newline="") as handle:  # LF only -- see above
    for row in rows:
        for field in row:
            handle.write(field)
            handle.write("\0")
RECORDED_PY

# Parallel indexed arrays, scanned linearly: this targets bash 3.2, which has no
# associative arrays, and a lock holds tens of skills rather than thousands.
REC_NAME=()
REC_DIGEST=()
if [ -f "$tmp/recorded.nul" ]; then
  while IFS= read -r -d '' rec_name && IFS= read -r -d '' rec_digest; do
    REC_NAME+=("$rec_name")
    REC_DIGEST+=("$rec_digest")
  done < "$tmp/recorded.nul"
fi

# may_replace <name> <locked-digest> — is $DEST/<name> this hook's to overwrite?
#
# True in exactly three cases, and false for everything else:
#
#   * nothing is there — there is no directory to destroy;
#   * what IS there already digests to the digest the lock names, so replacing
#     it cannot change a byte anyone would notice. Provenance is irrelevant when
#     the outcome is content-identical, and this is the clause that keeps an
#     UNREADABLE record a one-run blind spot rather than a permanent stall: with
#     no attributions at all, the skills already correctly installed still
#     re-install, and the run rewrites the record it could not read. Without it,
#     one corrupt file would refuse every skill forever — and the only other way
#     out of that, "overwrite freely when the record is unreadable", hands the
#     whole guard to anyone who can corrupt one file;
#   * the record claims that exact name AND the bytes on disk still digest to
#     what was installed — this hook's own prior install, untouched. Replacing
#     it is how an UPDATE is delivered, so this case must stay.
#
# Everything else — never installed by this hook, installed but EDITED since
# (the user has taken ownership of it), or unmeasurable — is somebody else's.
#
# `-n "$have"` is not redundant with either equality: `digest_dir` prints NOTHING
# when it fails, so two empty strings would compare EQUAL and hand back a
# directory nothing was ever measured against.
may_replace () {
  local want_name="$1" locked="$2" at=0 recorded="" have
  if [ ! -e "$DEST/$want_name" ] && [ ! -L "$DEST/$want_name" ]; then return 0; fi
  have="$(digest_dir "$DEST/$want_name")"
  [ -n "$have" ] || return 1
  if [ "$have" = "$locked" ]; then return 0; fi
  while [ "$at" -lt "${#REC_NAME[@]}" ]; do
    if [ "${REC_NAME[$at]}" = "$want_name" ]; then recorded="${REC_DIGEST[$at]}"; break; fi
    at=$((at + 1))
  done
  [ -n "$recorded" ] && [ "$have" = "$recorded" ]
}

total=0
ok=0
mismatch=()
collision=()
duplicate=()
absent=()
shadowed=()

# `relpath` is where this skill sits inside its source's tree, already resolved
# (layout + bundle + name) and validated by the lock reader above — bash never
# assembles a path out of lock fields itself. Records are NUL-delimited: five
# `read -r -d ''` fields per row, never split on a field's own bytes.
#
# EVERY path that skips a skill removes its destination first, and every removal
# is `${DEST:?}/$name` with $name re-validated in bash immediately below — so a
# verdict that says a skill is unavailable MEANS it is not there, and no
# destructive op trusts a name that arrived over the wire alone.
while IFS= read -r -d '' key \
   && IFS= read -r -d '' want \
   && IFS= read -r -d '' index \
   && IFS= read -r -d '' relpath \
   && IFS= read -r -d '' status; do
  total=$((total + 1))
  name="${key##*/}"

  # Re-check $name in bash, before any rm -rf/cp uses it. The lock reader
  # already rejects a key whose skill part is '.'/'..' or non-leading-alnum
  # (the 'adam/..' escape that made $name '..' and turned the install `cp` into
  # a write to $HOME/.claude), so this is belt-and-braces — the destructive ops
  # must not rely SOLELY on a wire value. A '.'/'..'/empty name is NOT removed
  # (its `$DEST/$name` would be the install dir or its parent); it is skipped.
  case "$name" in
    "" | . | .. )
      absent+=("$name")
      continue
      ;;
  esac
  if ! [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    absent+=("$name")
    continue
  fi

  # Ahead of EVERY `rm -rf` below, which is what lets those stay as they are:
  # past this point $DEST/$name either does not exist (the removal is a no-op)
  # or is this hook's own prior install (replacing it is the whole delivery
  # mechanism — a locked skill must still be able to receive an update). A
  # directory that is neither is not removed, not overwritten, and not silently
  # skipped: the skill is dropped from this run and NAMED in the verdict, so
  # "your hand-placed skill won" is something the reader is told rather than
  # something they infer from a count.
  if ! may_replace "$name" "$want"; then
    shadowed+=("$name")
    continue
  fi

  # The python<->bash source-index contract: a row's index must name a source
  # bash actually read. Out of range means the framing desynced — remove any
  # stale copy and refuse, rather than resolve against an arbitrary tree.
  if ! [[ "$index" =~ ^[0-9]+$ ]] || [ "$index" -ge "${#SRC_NAME[@]}" ]; then
    rm -rf "${DEST:?}/$name" >>"$LOG" 2>&1
    absent+=("$name")
    continue
  fi

  if [ "$status" = "dup" ]; then
    rm -rf "${DEST:?}/$name" >>"$LOG" 2>&1
    # Two rows wanting one destination, in one of two shapes, and the removal is
    # the same for both — which is why this is ONE arm and not two: a second
    # `rm -rf` in this loop for no behavioural difference is a second arm to
    # audit. What differs is the REMEDY, so they are reported apart. Two of ONE
    # lock's bundles shipping the same skill basename is one lock to correct;
    # two LOCKS naming different digests for one destination is two locks to
    # reconcile, and it is reported by the conflict clause below — which names
    # them, because "alpha was not installed" would otherwise send the reader to
    # whichever lock they thought of first.
    if ! is_conflicted "$name"; then
      duplicate+=("$key")
    fi
    continue
  fi
  if [ "${SRC_OK[$index]}" -ne 1 ]; then
    # Remove first: a verdict that reports this skill unavailable must not
    # leave a stale, unverified copy of it live in the session.
    rm -rf "${DEST:?}/$name" >>"$LOG" 2>&1
    # Bare `[index]` on the left: an assignment subscript is an arithmetic
    # context, where `$index` is redundant (shellcheck SC2004).
    SRC_LOST_N[index]=$(( SRC_LOST_N[index] + 1 ))
    if [ -z "${SRC_LOST[$index]}" ]; then
      SRC_LOST[index]="$name"
    else
      SRC_LOST[index]="${SRC_LOST[$index]}, $name"
    fi
    continue
  fi

  src="$tmp/reg-$index/$relpath"
  if [ ! -f "$src/SKILL.md" ]; then
    rm -rf "${DEST:?}/$name" >>"$LOG" 2>&1
    absent+=("$name")
    continue
  fi
  # Collision guard. Personal ~/.claude/skills shadows the project's
  # .claude/skills (C3), so a stale personal copy would keep shadowing the
  # repo-owned skill. Remove it so repo-owned actually wins; the skip is
  # reported.
  #
  # The project dir first, then every accepted lock's own repo. In a multi-repo
  # session the project dir is the PARENT of the repos, so it has no
  # `.claude/skills` and the project-dir arm alone would be INERT — the guard
  # would silently stop existing in exactly the shape that made it reachable.
  # A repo shipping both a lock naming X and its own `.claude/skills/X/SKILL.md`
  # has made the more specific declaration about X, and overwriting that in the
  # flat personal dir is the C3 hazard this exists for.
  #
  # OPEN POLICY QUESTION, recorded rather than answered: under a union, ONE
  # repo's project-owned skill now suppresses delivery of a locked skill the
  # OTHER repos in the session asked for. Near-dead in practice — agentskills
  # ships no `.claude/skills` at all — and the alternative (let a locked skill
  # overwrite a repo's own declaration) is the failure the guard was written for,
  # so the conservative arm is kept and the question is named.
  #
  # WHICH directory won is named only when it is not the project dir. That is
  # not squeamishness about the clause's length: with one lock the winner is
  # always the project dir and the sentence must stay the one it has always
  # been, while in a multi-repo session "repo-owned" no longer identifies a
  # single repo and a bare name would leave the reader unable to find the file
  # they have to move.
  owner=""
  if [ -f "$PROJECT_DIR/.claude/skills/$name/SKILL.md" ]; then
    owner="$PROJECT_DIR"
  elif [ "${#REPO_OWNED_DIRS[@]}" -gt 0 ]; then
    for repo_dir in "${REPO_OWNED_DIRS[@]}"; do
      if [ -f "$repo_dir/.claude/skills/$name/SKILL.md" ]; then owner="$repo_dir"; break; fi
    done
  fi
  if [ -n "$owner" ]; then
    rm -rf "${DEST:?}/$name" >>"$LOG" 2>&1
    if [ "$owner" = "$PROJECT_DIR" ]; then
      collision+=("$name")
    else
      collision+=("$name ($owner)")
    fi
    continue
  fi

  rm -rf "${DEST:?}/$name" >>"$LOG" 2>&1
  if ! cp -R "$src" "$DEST/$name" >>"$LOG" 2>&1; then
    absent+=("$name")
    continue
  fi
  got="$(digest_dir "$DEST/$name")"
  if [ "$got" = "$want" ]; then
    ok=$((ok + 1))
    # The install record's raw material: what this hook put there, which
    # (registry, bundle) it came from, and the digest it had at install — the
    # one just VERIFIED, so it is the digest of the bytes now on disk rather
    # than a second measurement of them. Read on the NEXT run to tell "this
    # hook installed it and nobody has touched it since" (safe to remove once
    # the lock stops naming it) from "somebody else's, or edited" (never).
    printf '%s\0%s\0%s\0%s\0' \
      "$name" "${SRC_URL[$index]}" "${key%%/*}" "$want" >>"$tmp/installed.nul"
  else
    # Integrity FAILED: leave nothing behind. The unverified bytes must not
    # stay in ~/.claude/skills for the model to load on turn one.
    rm -rf "${DEST:?}/$name" >>"$LOG" 2>&1
    mismatch+=("$name")
  fi
done < "$tmp/skills.nul"

# The other half of the framing contract: python said how many skill rows it
# wrote; bash must have processed exactly that many complete 5-field records.
if [ "$total" -ne "$NSKILLS" ]; then
  # Including whatever this run just installed: the framing is what says which
  # rows were even meant to exist, so none of it is trustworthy now.
  purge_locked_destinations
  emit "skills: DEGRADED — could not read $LOCK_LABEL (skill framing mismatch: $NSKILLS declared, $total read; see $LOG)"
fi

# --- skills that have LEFT the lock ----------------------------------------
# `purge_locked_destinations` removes what the lock NAMES. Nothing removed what
# it no longer names, and nothing ran on the success path at all: a skill
# dropped from the lock — withdrawn, renamed, or retired BECAUSE it was wrong —
# stayed live in ~/.claude/skills indefinitely while the verdict read
# `skills: N/N … — OK`, and a rename left BOTH names loaded. Reproduced before
# this was written: run 1 with a two-skill lock, run 2 with a one-skill lock,
# and the dropped skill's body still sat there under `skills: 1/1 … — OK`.
#
# The obvious fix — delete whatever in $DEST the lock does not name — would be a
# far worse bug than the leak. ~/.claude/skills is the USER's directory: a
# developer populates it by hand, and the claude.ai account-sync channel writes
# into `synced/`. So removal is driven by a RECORD OF THIS HOOK'S OWN INSTALLS,
# and a directory is removed only when ALL FOUR of these hold:
#
#   * the record says this hook installed it, under that exact name;
#   * its bytes are still EXACTLY what this hook installed (digest unchanged);
#   * the lock still declares the (registry, bundle) it was installed from; and
#   * the lock no longer names it.
#
# Everything else is left alone, and the two cases a reader would otherwise have
# to guess at get named in the verdict: a skill left in place because it was
# EDITED since install — the user has taken ownership, and deleting their work
# to satisfy a lock they may not control is the worse failure — and skills left
# alone because AGENTSKILLS_BUNDLE narrowed this run away from their bundle.
#
# SCOPED PER (registry, bundle) because two repos on one machine SHARE
# ~/.claude/skills: unscoped, repo B's run reaps every skill repo A installed,
# since none of A's names appear in B's lock. KNOWN RESIDUAL, deliberately not
# resolved: two repos declaring the SAME (registry, bundle) at different pinned
# refs with different skill sets still contend, each run removing what the other
# installed. Neither lock is more authoritative than the other, so a tiebreak
# here could only be invented — the limit is stated rather than guessed at.
#
# WITHIN one session the union dissolves that contention outright: `claims.nul`
# is the union across every ACCEPTED lock, so a run that sees all of them reaps
# nothing another one still wants. What survives is the CROSS-SESSION case —
# a union session installs the union, a later single-repo session reaps the
# names its own lock lacks, the next union session reinstalls them — which is
# the same mechanism, the same bound (the symmetric difference of the two name
# sets) and the same names as the A-then-B churn above. Fixing it needs a `lock`
# provenance field in the install record so a run never reaps what a lock it did
# not discover, and that is deliberately NOT landed here: a record-schema change
# plus a new planner condition, in the most destructive code path in this file,
# is not something to ship in the same change as the union.
# $RECORD is set above the install loop, which now reads it too — the same file,
# for the same question, asked once per direction: may this be overwritten, and
# may this be removed.
removed=()
left=()
narrowed=()
record_unreadable=0
record_unwritable=0

# The plan, computed where the JSON already is: which recorded installs are
# candidates for removal, which are carried forward untouched, and which this
# run is deliberately not deciding about. Written NUL-framed into $tmp like
# every other python->bash stream here, so no field's content can forge a
# record.
#
# DEGRADES TO PRUNING NOTHING, never to pruning everything. A missing record is
# a first run (no entries, exit 0). A record that is unreadable, is not JSON, or
# is not the shape this writes exits 3 and produces no plan at all. A truncated
# stream drops trailing records, which can only mean FEWER removals. And a
# single malformed entry is skipped rather than failing the file, for the same
# reason in miniature: an entry that cannot be validated can never be shown safe
# to delete.
#
# `-I` (isolated): see the header. As exposed as the lock reader — a `json.py`
# in the project directory would otherwise be what decides which of the user's
# directories this hook is about to `rm -rf`.
if ! RECORD_PATH="$RECORD" TMP_DIR="$tmp" python3 -I - >>"$LOG" 2>&1 <<'PLAN_PY'
import json, os, re, sys

record_path = os.environ["RECORD_PATH"]
tmp_dir = os.environ["TMP_DIR"]
# Read straight from the environment, and used ONLY to compare against a
# recorded bundle name — never echoed into a verdict, never part of a path.
only_bundle = os.environ.get("AGENTSKILLS_BUNDLE") or ""

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
DIGEST = re.compile(r"[0-9a-f]{64}")
CONTROL = re.compile(r"[\s\x00-\x1f\x7f]")


def read_records(path, width):
    """The NUL-framed records in `path`, as `width`-field tuples.

    A trailing INCOMPLETE record is dropped rather than padded — bash's
    `read -r -d ''` chain ends on a short record the same way, and reading too
    little can only prune too little.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            fields = handle.read().split("\0")
    except OSError:
        return []
    if fields and fields[-1] == "":
        fields.pop()                    # the NUL terminating the final field
    return [tuple(fields[at:at + width])
            for at in range(0, len(fields) - width + 1, width)]


# What the lock names right now (post-narrowing: exactly the rows the install
# loop just processed) and which (registry, bundle) pairs it declares.
locked = {key.rsplit("/", 1)[-1]
          for key, _digest, _index, _relpath, _status
          in read_records(os.path.join(tmp_dir, "skills.nul"), 5)}
claims = set(read_records(os.path.join(tmp_dir, "claims.nul"), 2))

try:
    with open(record_path, encoding="utf-8") as handle:
        record = json.load(handle)
except FileNotFoundError:
    record = {"installed": []}          # first run: nothing was ever recorded
except (OSError, ValueError):
    sys.exit(3)
if not isinstance(record, dict) or not isinstance(record.get("installed"), list):
    sys.exit(3)

plan = []
for entry in record["installed"]:
    if not isinstance(entry, dict):
        continue
    name = entry.get("name")
    registry = entry.get("registry")
    bundle = entry.get("bundle")
    digest = entry.get("digest")
    if not all(isinstance(field, str) for field in (name, registry, bundle, digest)):
        continue
    # The lock reader's charsets, applied again on the way OUT of a file that
    # sits in a directory anyone with the user's shell can write. A registry is
    # only compared and re-recorded — never fetched from here — so it needs no
    # URL shape, but it must not carry a NUL or it could forge records below.
    if not NAME.fullmatch(name) or not NAME.fullmatch(bundle):
        continue
    if not DIGEST.fullmatch(digest) or CONTROL.search(registry):
        continue
    if name in locked:
        # The lock still names it, so the install loop above already decided
        # what happens to that directory; bash records THIS run's outcome for
        # it, and a stale record of an earlier one must not outlive that.
        continue
    if only_bundle and bundle != only_bundle:
        plan.append(("narrowed", name, registry, bundle, digest))
    elif (registry, bundle) in claims:
        plan.append(("candidate", name, registry, bundle, digest))
    else:
        # Another repo's lock installed it, or this lock never declared that
        # bundle. Not ours to remove, and not ours to forget either.
        plan.append(("keep", name, registry, bundle, digest))

with open(os.path.join(tmp_dir, "plan.nul"), "w", encoding="utf-8",
          newline="") as handle:  # LF only -- see above
    for row in plan:
        for field in row:
            handle.write(field)
            handle.write("\0")
PLAN_PY
then
  record_unreadable=1
fi

if [ "$record_unreadable" -eq 0 ] && [ -f "$tmp/plan.nul" ]; then
  while IFS= read -r -d '' action \
     && IFS= read -r -d '' name \
     && IFS= read -r -d '' registry \
     && IFS= read -r -d '' bundle \
     && IFS= read -r -d '' digest; do
    if [ "$action" != "candidate" ]; then
      if [ "$action" = "narrowed" ]; then narrowed+=("$name"); fi
      printf '%s\0%s\0%s\0%s\0' "$name" "$registry" "$bundle" "$digest" >>"$tmp/keep.nul"
      continue
    fi
    # The same rule `purge_locked_destinations` follows, for the same reason: a
    # name reaching a destructive op is re-validated in bash immediately before
    # it, and a '.'/'..'/empty or non-matching one is SKIPPED, never removed —
    # its `$DEST/$name` would be the install dir or its parent. This name comes
    # out of a JSON file rather than off the wire, which is no reason to trust
    # it more.
    case "$name" in "" | . | .. ) continue ;; esac
    if ! [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then continue; fi
    # `synced/` is the claude.ai account-sync channel's own directory. Nothing
    # this hook installs is ever called that, so a record saying otherwise is
    # wrong by construction — and the account store is not ours to delete under
    # any lock.
    if [ "$name" = "synced" ]; then continue; fi
    # Already gone — removed by hand, or by an earlier run. Nothing to remove,
    # and nothing left to keep a record of.
    if [ ! -d "$DEST/$name" ]; then continue; fi
    got="$(digest_dir "$DEST/$name")"
    # `-n "$digest"` is not redundant with the equality: `digest_dir` prints
    # NOTHING when it fails, so two empty strings would compare EQUAL and delete
    # a directory nothing was ever measured against.
    if [ -n "$digest" ] && [ "$got" = "$digest" ]; then
      rm -rf "${DEST:?}/$name" >>"$LOG" 2>&1
      removed+=("$name")
      continue
    fi
    # EDITED since this hook installed it (or unmeasurable, which gets the same
    # answer): the user owns it now, so it stays and is NAMED instead. The
    # record keeps the ORIGINAL install digest on purpose — re-recording the
    # edited one would make the next run's comparison succeed and delete the
    # user's work one run later, and the notice would fall silent after one run
    # while the withdrawn skill stayed loaded.
    left+=("$name")
    printf '%s\0%s\0%s\0%s\0' "$name" "$registry" "$bundle" "$digest" >>"$tmp/keep.nul"
  done < "$tmp/plan.nul"
fi

# Rewritten from what is true NOW: this run's verified installs plus the entries
# carried forward untouched. Keyed by NAME, because the install dir is FLAT — a
# name is one directory with one owner, the same rule that makes two lock rows
# sharing a destination an error — and this run's install wins the key, since it
# is what just wrote those bytes.
#
# Staged in $DEST and moved into place with os.replace: a crashed or half-written
# run leaves the PREVIOUS record intact rather than a truncated one, and the
# write can never follow a symlink left at that path. Carries no timestamp —
# two runs installing the same thing must produce a byte-identical file, or
# every session start would rewrite it for nothing.
#
# It is rewritten even when the record could not be READ, which is what makes
# that state self-healing in one run. The cost is that entries from before the
# corruption are forgotten, so a skill that left the lock during that window is
# left alone forever rather than removed — the safe direction of the same rule:
# what this hook cannot show it installed, it does not delete.
if ! RECORD_PATH="$RECORD" TMP_DIR="$tmp" python3 -I - >>"$LOG" 2>&1 <<'RECORD_PY'
import json, os, tempfile

record_path = os.environ["RECORD_PATH"]
tmp_dir = os.environ["TMP_DIR"]


def read_records(path, width):
    """As in the planner above — two processes, so no module to share it."""
    try:
        with open(path, encoding="utf-8") as handle:
            fields = handle.read().split("\0")
    except OSError:
        return []
    if fields and fields[-1] == "":
        fields.pop()
    return [tuple(fields[at:at + width])
            for at in range(0, len(fields) - width + 1, width)]


entries = {}
# Carried-forward first, this run's installs second, so a name claimed by both
# resolves to the run that actually wrote the directory.
for name, registry, bundle, digest in (
        read_records(os.path.join(tmp_dir, "keep.nul"), 4)
        + read_records(os.path.join(tmp_dir, "installed.nul"), 4)):
    entries[name] = {"name": name, "registry": registry,
                     "bundle": bundle, "digest": digest}

payload = {"version": 1, "installed": [entries[name] for name in sorted(entries)]}
directory = os.path.dirname(record_path) or "."
staged_fd, staged = tempfile.mkstemp(
    dir=directory, prefix=".skills-bootstrap-installed.", suffix=".tmp")
try:
    # LF only: the install record is a JSON artifact a human reads and another
    # run parses. Its bytes should not depend on which platform wrote it.
    with os.fdopen(staged_fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(payload, indent=2) + "\n")
    os.replace(staged, record_path)
except BaseException:
    try:
        os.unlink(staged)
    except OSError:
        pass
    raise
RECORD_PY
then
  record_unwritable=1
fi

# --- verdict ---------------------------------------------------------------
# Every source is named, including one that could not be fetched: the reader
# needs to know which registries this lock draws on, and the problem list right
# after says which of them broke.
FROM=()
problems=()
index=0
while [ "$index" -lt "${#SRC_NAME[@]}" ]; do
  FROM+=("${SRC_NAME[$index]}@${SRC_SHORT[$index]}")
  if [ "${SRC_OK[$index]}" -ne 1 ]; then
    if [ "${SRC_LOST_N[$index]}" -gt 0 ]; then
      problems+=("could not fetch ${SRC_NAME[$index]}@${SRC_SHORT[$index]} (${SRC_LOST_N[$index]} $(plural "${SRC_LOST_N[$index]}" skill skills) unavailable: ${SRC_LOST[$index]}; see $LOG)")
    else
      # Unreachable, but this session wanted nothing from it — AGENTSKILLS_BUNDLE
      # narrowed the lock past its bundles. Still reported (a pinned registry
      # nobody can reach is worth knowing) but not counted as a loss, and never
      # rendered as the nonsense "0 skills unavailable: ".
      problems+=("could not fetch ${SRC_NAME[$index]}@${SRC_SHORT[$index]} (no locked skill needed it; see $LOG)")
    fi
  fi
  index=$((index + 1))
done

if [ "${#mismatch[@]}" -gt 0 ]; then
  problems+=("${#mismatch[@]} digest $(plural "${#mismatch[@]}" mismatch mismatches) ($(join_names "${mismatch[@]}"))")
fi
if [ "${#collision[@]}" -gt 0 ]; then
  problems+=("${#collision[@]} $(plural "${#collision[@]}" collision collisions) skipped, repo-owned wins ($(join_names "${collision[@]}"))")
fi
if [ "${#duplicate[@]}" -gt 0 ]; then
  problems+=("${#duplicate[@]} lock rows share a destination name, none installed ($(join_names "${duplicate[@]}"))")
fi
# The cross-lock conflict, reported by DESTINATION and by the locks that
# disagree — not merely by the skill. "alpha was not installed" sends a reader
# looking at one lock; "these two locks name different bytes for alpha" is the
# thing they can act on, and with fourteen repos attached it is the only way to
# find them.
if [ "${#CONFLICT_NAME[@]}" -gt 0 ]; then
  conflict_pairs=()
  at=0
  while [ "$at" -lt "${#CONFLICT_NAME[@]}" ]; do
    conflict_pairs+=("${CONFLICT_NAME[$at]} (${CONFLICT_LOCKS[$at]})")
    at=$((at + 1))
  done
  problems+=("${#CONFLICT_NAME[@]} destination $(plural "${#CONFLICT_NAME[@]}" name names) claimed at different digests by different locks, none installed ($(join_names "${conflict_pairs[@]}"))")
fi
# A lock that contributed NOTHING is the failure this file argues against, so it
# is named with its reason rather than left to be inferred from a count. The
# other locks in the session still installed: one bad lock among fourteen
# attached repos must not deny delivery to the other thirteen.
if [ "${#REJ_LOCK[@]}" -gt 0 ]; then
  rejected_pairs=()
  at=0
  while [ "$at" -lt "${#REJ_LOCK[@]}" ]; do
    rejected_pairs+=("${REJ_LOCK[$at]}: ${REJ_WHY[$at]}")
    at=$((at + 1))
  done
  problems+=("${#REJ_LOCK[@]} discovered $(plural "${#REJ_LOCK[@]}" lock locks) rejected, contributing nothing ($(join_names "${rejected_pairs[@]}"))")
fi
# Both caps are NAMED rather than silent, for the reason every cap here is: a
# truncation nobody is told about looks exactly like a registry that had nothing
# to give.
if [ "${#DROPPED_LOCKS[@]}" -gt 0 ]; then
  problems+=("${#DROPPED_LOCKS[@]} discovered $(plural "${#DROPPED_LOCKS[@]}" lock locks) past the $MAX_LOCKS-lock cap were not read ($(join_names "${DROPPED_LOCKS[@]}"))")
fi
if [ "${#DROPPED_ROW[@]}" -gt 0 ]; then
  problems+=("${#DROPPED_ROW[@]} $(plural "${#DROPPED_ROW[@]}" skill skills) dropped, this run had already reached its registry-fetch cap ($(join_names "${DROPPED_ROW[@]}"))")
fi
if [ "${#absent[@]}" -gt 0 ]; then
  problems+=("${#absent[@]} not installed ($(join_names "${absent[@]}"))")
fi
# A locked skill this run did NOT deliver, because delivering it would have meant
# destroying a directory of the user's. It degrades the verdict for the ordinary
# reason — the lock names it and it is not installed — and says which way the
# conflict was resolved, since the fix (move or delete your copy, or drop the
# row from the lock) is the reader's to choose, not this hook's.
if [ "${#shadowed[@]}" -gt 0 ]; then
  problems+=("${#shadowed[@]} shadowed — refusing to overwrite a directory this hook did not install, or that was edited since ($(join_names "${shadowed[@]}"))")
fi
# A skill still LIVE that the lock no longer names is exactly the condition this
# file exists to make knowable — "content that should not be there is, and
# nothing says so" — so it DEGRADES the verdict rather than riding along as a
# footnote. A removal does not: once it has happened the installed set matches
# the lock, which is the clean state, and the note below is the record of the
# action that got there.
if [ "${#left[@]}" -gt 0 ]; then
  problems+=("${#left[@]} $(plural "${#left[@]}" skill skills) no longer in the lock left in place, edited since install ($(join_names "${left[@]}"))")
fi
# Reported for the same reason an unreachable registry nobody needed is: this
# run could not tell whether stale skills are live, and staying quiet about that
# is the bug the section above was written to end.
if [ "$record_unreadable" -eq 1 ]; then
  problems+=("could not read the install record $RECORD, so no stale skill could be removed this run (see $LOG)")
fi
if [ "$record_unwritable" -eq 1 ]; then
  problems+=("could not write the install record $RECORD, so a later run cannot remove what this one installed (see $LOG)")
fi
# The same shape as the unreadable-record clause above, one level up: this run
# could not enumerate the project's children, so it does not know which repos
# were in the session and has claimed authority over nothing. Everything it did
# find still installed; nothing was removed.
if [ -n "$scan_incomplete" ]; then
  problems+=("could not fully enumerate $PROJECT_DIR ($scan_incomplete), so some repo's lock may have been missed and no stale skill could be removed this run")
fi

# Notes, not problems: something this run DID, or deliberately did not do, on a
# run that is otherwise exactly as healthy as its counts say. They are appended
# after the OK/DEGRADED word, and only when non-empty, so the ordinary all-clear
# case — nothing removed, nothing narrowed — still ends at `— OK` with nothing
# extra to read.
notes=()
if [ "${#removed[@]}" -gt 0 ]; then
  notes+=("removed ${#removed[@]} $(plural "${#removed[@]}" skill skills) no longer in the lock ($(join_names "${removed[@]}"))")
fi
if [ "${#narrowed[@]}" -gt 0 ]; then
  notes+=("AGENTSKILLS_BUNDLE narrowed this run to one bundle, leaving ${#narrowed[@]} other-bundle $(plural "${#narrowed[@]}" skill skills) alone ($(join_names "${narrowed[@]}"))")
fi
suffix=""
if [ "${#notes[@]}" -gt 0 ]; then
  for note in "${notes[@]}"; do suffix="$suffix; $note"; done
fi

# Who contributed. `23/23 from <registry>` out of a multi-repo session says
# nothing about WHICH of the attached repos asked for those 23, and a union whose
# inputs are invisible is one nobody can audit. Repo directory BASENAMES, because
# the full lock paths are already in every problem clause that needs them and a
# fourteen-path list in the headline is a sentence nobody finishes.
#
# EMPTY with one lock, deliberately: the single-lock verdict is byte-for-byte the
# one this hook has always emitted, and the ordinary case must not acquire noise
# to describe a shape it is not in.
ACROSS=""
if [ "${#ACCEPTED[@]}" -gt 1 ]; then
  lock_names=()
  for accepted_lock in "${ACCEPTED[@]}"; do
    accepted_dir="${accepted_lock%/*}"
    lock_names+=("${accepted_dir##*/}")
  done
  ACROSS=" across ${#ACCEPTED[@]} locks ($(join_names "${lock_names[@]}"))"
fi

echo "installed=$ok/$total sources=$(join_names "${FROM[@]}") locks=${#ACCEPTED[@]} dest=$DEST" >>"$LOG"

if [ "${#problems[@]}" -eq 0 ]; then
  emit "skills: $ok/$total from $(join_names "${FROM[@]}")$ACROSS — OK$suffix"
fi
emit "skills: $ok/$total from $(join_names "${FROM[@]}")$ACROSS — DEGRADED: $(join_names "${problems[@]}")$suffix"
