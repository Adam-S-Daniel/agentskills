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
# Fails SOFT, always. A hook that exits non-zero can block a session, so every
# failure path here emits a verdict naming the exact file or binary at fault
# and exits 0.
set -uo pipefail
cat >/dev/null || true   # drain the hook's stdin JSON

HOOK_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
# The repo that ships this hook (…/<repo>/.claude/hooks/ → <repo>), resolved so
# the paths named in a failure verdict are ones a reader can paste.
SELF_ROOT="$(cd -- "$HOOK_DIR/../.." >/dev/null 2>&1 && pwd -P)" || SELF_ROOT="$HOOK_DIR/../.."
LOG="${TMPDIR:-/tmp}/skills-bootstrap.log"

# emit <verdict> — print the SessionStart payload and exit 0.
#
# The verdict always leads with the literal token `skills:` so it is greppable
# from a transcript, and it is the whole "readily knowable" contract: whoever
# reads it must be able to tell installed-and-verified from degraded, and if
# degraded, which knob fixes it.
emit () {
  if command -v python3 >/dev/null 2>&1; then
    # json.dumps, not string concatenation: the verdict can carry names read
    # out of the lock file, and hand-built JSON is how a stray quote turns a
    # fail-soft notice into malformed hook output.
    SKILLS_VERDICT="$1" python3 -c '
import json, os
print(json.dumps({
    "reloadSkills": True,
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "reloadSkills": True,
        "additionalContext": os.environ["SKILLS_VERDICT"],
    },
}, ensure_ascii=False))'
  else
    # No python3 means no JSON encoder. This branch is only ever reached with a
    # fixed literal verdict that contains no quote or backslash, so printf is
    # safe here and nowhere else.
    printf '{"reloadSkills":true,"hookSpecificOutput":{"hookEventName":"SessionStart","reloadSkills":true,"additionalContext":"%s"}}\n' "$1"
  fi
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
if [ -z "${CLAUDE_CODE_REMOTE_SESSION_ID:-}" ] && [ "${CLAUDE_CODE_ENTRYPOINT:-}" != "remote" ] \
   && [ -z "${SKILLS_BOOTSTRAP_FORCE:-}" ]; then
  emit "skills: skipped — durable session, marketplace install is authoritative"
fi

# --- locate the lock -------------------------------------------------------
# Two places, in priority order: the project Claude Code named, then the repo
# that ships this hook. In the common case — a session whose project dir IS
# this repo — those are the SAME file, and a verdict that listed the one path
# twice read like two separate lookups had failed. So each candidate is
# canonicalised and added only if it is new; genuinely distinct locations are
# still both listed, in order.
LOCK_CANDIDATES=()

# add_lock_candidate <path> — resolve <path>'s directory to a physical path and
# append it, unless an equal path is already in the list. Resolution (not plain
# string comparison) is what makes the de-duplication hold when the two spell
# the same directory differently — a trailing slash, a `.`, or a symlinked
# project dir.
add_lock_candidate () {
  local raw="$1" dir base resolved existing
  dir="$(dirname -- "$raw")"
  base="$(basename -- "$raw")"
  if resolved="$(cd -- "$dir" >/dev/null 2>&1 && pwd -P)"; then
    resolved="$resolved/$base"
  else
    # Nothing to canonicalise — the directory does not exist. Keep the literal
    # path so the verdict still names something the reader can act on.
    resolved="$raw"
  fi
  if [ "${#LOCK_CANDIDATES[@]}" -gt 0 ]; then
    for existing in "${LOCK_CANDIDATES[@]}"; do
      if [ "$existing" = "$resolved" ]; then return 0; fi
    done
  fi
  LOCK_CANDIDATES+=("$resolved")
}

if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then
  add_lock_candidate "$CLAUDE_PROJECT_DIR/skills.lock"
fi
add_lock_candidate "$SELF_ROOT/skills.lock"

LOCK=""
for candidate in "${LOCK_CANDIDATES[@]}"; do
  if [ -f "$candidate" ]; then LOCK="$candidate"; break; fi
done
if [ -z "$LOCK" ]; then
  emit "skills: DEGRADED — no skills.lock found, looked in $(join_names "${LOCK_CANDIDATES[@]}") (generate it with scripts/generate_skills_lock.py)"
fi

LOCK_DIR="$(cd -- "$(dirname -- "$LOCK")" >/dev/null 2>&1 && pwd -P)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$LOCK_DIR}"

# --- prerequisites ---------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  emit "skills: DEGRADED — python3 not found on PATH (needed to read the lock and verify digests; install python3)"
fi
if ! command -v git >/dev/null 2>&1; then
  emit "skills: DEGRADED — git not found on PATH (needed to fetch the registry; install git)"
fi
if [ -z "${HOME:-}" ]; then
  emit "skills: DEGRADED — HOME is unset (needed to locate ~/.claude/skills; export HOME)"
fi

tmp="$(mktemp -d)" || emit "skills: DEGRADED — could not create a temp directory (check ${TMPDIR:-/tmp})"
trap 'rm -rf "$tmp"' EXIT

# --- read + validate the lock ----------------------------------------------
# Parsed with python3's stdlib json — never a hand-rolled shell parser — and
# every field that reaches a URL or a filesystem path is validated here, in one
# place, before bash ever sees it. Env overrides are applied on the same side
# of that boundary so they get the identical validation.
if ! LOCK_PATH="$LOCK" OUT_DIR="$tmp" python3 - <<'PY' >>"$LOG" 2>&1
import json, os, re, sys

lock_path = os.environ["LOCK_PATH"]
out_dir = os.environ["OUT_DIR"]
with open(lock_path, encoding="utf-8") as handle:
    lock = json.load(handle)

registry = os.environ.get("AGENTSKILLS_REPO") or lock.get("registry") or ""
ref = os.environ.get("AGENTSKILLS_REF") or lock.get("ref") or ""
only_bundle = os.environ.get("AGENTSKILLS_BUNDLE") or ""
skills = lock.get("skills") or {}

if not isinstance(skills, dict):
    sys.exit("lock: 'skills' must be an object")
if not re.fullmatch(r"[A-Za-z0-9._/+:@-]+", ref or ""):
    sys.exit("lock: 'ref' is missing or not a plausible git ref")

# The registry becomes a git remote URL, so restrict it to shapes we intend:
# OWNER/REPO on GitHub, or an explicit https:// / file:// URL. file:// exists
# so the hook's own tests can run against a local fixture with no network.
if "://" in registry:
    if not registry.startswith(("https://", "file://")):
        sys.exit("lock: 'registry' URL must be https:// or file://")
    url = registry
elif re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", registry):
    url = "https://github.com/%s.git" % registry
else:
    sys.exit("lock: 'registry' must be OWNER/REPO or an https:// / file:// URL")

rows = []
for key in sorted(skills):
    digest = skills[key]
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", key):
        sys.exit("lock: skill key %r is not '<bundle>/<skill>'" % key)
    if not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
        sys.exit("lock: skill %r has no sha256 digest" % key)
    if only_bundle and key.split("/", 1)[0] != only_bundle:
        continue
    rows.append("%s\t%s" % (key, digest))

with open(os.path.join(out_dir, "meta"), "w", encoding="utf-8") as handle:
    handle.write("%s\n%s\n%s\n" % (registry, url, ref))
with open(os.path.join(out_dir, "skills.tsv"), "w", encoding="utf-8") as handle:
    handle.write("".join(row + "\n" for row in rows))
PY
then
  emit "skills: DEGRADED — could not read $LOCK (invalid JSON or a bad field; regenerate it with scripts/generate_skills_lock.py, details in $LOG)"
fi

{ read -r REG_NAME; read -r REG_URL; read -r REG_REF; } < "$tmp/meta"

# --- fetch the registry at the pinned ref ----------------------------------
# `git clone --depth 1 --branch <SHA>` FAILS with exit 128 ("Remote branch
# <sha> not found in upstream origin") — --branch takes a ref NAME, so a clone
# structurally cannot pin to a commit. Pinning needs an explicit init + fetch
# of the object. Branches and tags still take the clone path.
fetch_ok=1
if [[ "$REG_REF" =~ ^[0-9a-f]{40}$ ]]; then
  { git init -q "$tmp/reg" \
    && git -C "$tmp/reg" remote add origin "$REG_URL" \
    && git -C "$tmp/reg" fetch --depth 1 -q origin "$REG_REF" \
    && git -C "$tmp/reg" checkout -q FETCH_HEAD; } >>"$LOG" 2>&1 || fetch_ok=0
else
  git clone --depth 1 --branch "$REG_REF" -q "$REG_URL" "$tmp/reg" >>"$LOG" 2>&1 || fetch_ok=0
fi
if [ "$fetch_ok" -ne 1 ]; then
  emit "skills: DEGRADED — could not fetch ${REG_NAME}@${REG_REF} (network, or a bad ref in $LOCK; see $LOG)"
fi

# --- locate the digest implementation --------------------------------------
# The hook does NOT reimplement the digest in bash. A second, independently
# written copy of a hash algorithm is exactly the class of bug that produces an
# "expected" number nobody can explain, so both sides call the one
# implementation in generate_skills_lock.py --digest.
#
# Preference order: the copy shipped beside this hook, then the consuming
# project's, then the registry's own — the last is the consumer case (a repo
# that carries the hook but no scripts/ of its own), and it is pinned to the
# same immutable ref as the skills being verified.
GEN=""
for candidate in "$SELF_ROOT/scripts/generate_skills_lock.py" \
                 "$PROJECT_DIR/scripts/generate_skills_lock.py" \
                 "$tmp/reg/scripts/generate_skills_lock.py"; do
  if [ -f "$candidate" ]; then GEN="$candidate"; break; fi
done
if [ -z "$GEN" ]; then
  emit "skills: DEGRADED — generate_skills_lock.py not found beside the hook, in $PROJECT_DIR/scripts, or in ${REG_NAME}@${REG_REF} (needed to verify digests)"
fi

# --- install + verify ------------------------------------------------------
DEST="$HOME/.claude/skills"
mkdir -p "$DEST" || emit "skills: DEGRADED — could not create $DEST (check permissions on \$HOME)"

total=0
ok=0
mismatch=()
collision=()
absent=()

while IFS=$'\t' read -r key want; do
  [ -n "$key" ] || continue
  total=$((total + 1))
  bundle="${key%%/*}"
  name="${key##*/}"
  src="$tmp/reg/plugins/$bundle/skills/$name"

  if [ ! -f "$src/SKILL.md" ]; then
    absent+=("$name")
    continue
  fi
  # Collision guard. Personal ~/.claude/skills shadows the project's
  # .claude/skills (C3), so installing a same-named fleet skill would silently
  # override one this repo owns. Repo-owned wins; the skip is reported.
  if [ -f "$PROJECT_DIR/.claude/skills/$name/SKILL.md" ]; then
    collision+=("$name")
    continue
  fi

  rm -rf "${DEST:?}/$name"
  if ! cp -R "$src" "$DEST/$name" >>"$LOG" 2>&1; then
    absent+=("$name")
    continue
  fi
  got="$(python3 "$GEN" --digest "$DEST/$name" 2>>"$LOG")"
  if [ "$got" = "$want" ]; then
    ok=$((ok + 1))
  else
    mismatch+=("$name")
  fi
done < "$tmp/skills.tsv"

# --- verdict ---------------------------------------------------------------
short="$REG_REF"
if [ "${#short}" -eq 40 ]; then short="${short:0:7}"; fi

problems=()
if [ "${#mismatch[@]}" -gt 0 ]; then
  problems+=("${#mismatch[@]} digest $(plural "${#mismatch[@]}" mismatch mismatches) ($(join_names "${mismatch[@]}"))")
fi
if [ "${#collision[@]}" -gt 0 ]; then
  problems+=("${#collision[@]} $(plural "${#collision[@]}" collision collisions) skipped, repo-owned wins ($(join_names "${collision[@]}"))")
fi
if [ "${#absent[@]}" -gt 0 ]; then
  problems+=("${#absent[@]} not installed ($(join_names "${absent[@]}"))")
fi

echo "installed=$ok/$total registry=$REG_NAME ref=$REG_REF dest=$DEST" >>"$LOG"

if [ "${#problems[@]}" -eq 0 ]; then
  emit "skills: $ok/$total from ${REG_NAME}@${short} — OK"
fi
emit "skills: $ok/$total from ${REG_NAME}@${short} — DEGRADED: $(join_names "${problems[@]}")"
