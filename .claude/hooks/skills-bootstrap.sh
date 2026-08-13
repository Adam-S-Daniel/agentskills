#!/usr/bin/env bash
# PROTOTYPE / PROBE (cms-platform#249): fetch the canonical skills registry at
# session start and install a bundle into ~/.claude/skills, so an ephemeral
# Claude surface (cloud session, CI runner, container) gets the fleet's skills
# WITHOUT any repo vendoring a copy of them.
#
# Surface-aware by design: on a developer's own machine the marketplace plugin
# install is authoritative, and writing the same skills into ~/.claude/skills
# would double-load them, so this is a no-op unless the session is ephemeral.
# Fails SOFT: no network / no registry => a notice, never a crash.
set -uo pipefail
cat >/dev/null || true   # drain the hook's stdin JSON

emit () { printf '%s\n' "$1"; exit 0; }
ctx  () { printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":%s}}\n' "$1"; exit 0; }

# --- surface guard: ephemeral sessions only --------------------------------
if [ -z "${CLAUDE_CODE_REMOTE_SESSION_ID:-}" ] && [ "${CLAUDE_CODE_ENTRYPOINT:-}" != "remote" ] \
   && [ -z "${SKILLS_BOOTSTRAP_FORCE:-}" ]; then
  ctx '"skills-bootstrap: durable/local session — marketplace install is authoritative, no-op."'
fi

REG_REPO="${AGENTSKILLS_REPO:-Adam-S-Daniel/agentskills}"
REG_REF="${AGENTSKILLS_REF:-main}"
BUNDLE="${AGENTSKILLS_BUNDLE:-adam}"
DEST="$HOME/.claude/skills"
LOG="${TMPDIR:-/tmp}/skills-bootstrap.log"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

if ! git clone --depth 1 --branch "$REG_REF" --quiet \
      "https://github.com/${REG_REPO}.git" "$tmp/reg" >>"$LOG" 2>&1; then
  ctx "\"skills-bootstrap: could not fetch ${REG_REPO}@${REG_REF} — continuing without registry skills.\""
fi

n=0
mkdir -p "$DEST"
for d in "$tmp/reg/plugins/${BUNDLE}/skills"/*/; do
  [ -f "${d}SKILL.md" ] || continue
  name="$(basename "$d")"
  rm -rf "${DEST:?}/${name}"
  cp -R "$d" "$DEST/$name"
  n=$((n + 1))
done
sha="$(git -C "$tmp/reg" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "installed=$n ref=$REG_REF sha=$sha bundle=$BUNDLE" >>"$LOG"

emit "$(printf '{"reloadSkills":true,"hookSpecificOutput":{"hookEventName":"SessionStart","reloadSkills":true,"additionalContext":"skills-bootstrap: installed %d skills from %s@%s (%s), bundle=%s, into ~/.claude/skills."}}' \
  "$n" "$REG_REPO" "$REG_REF" "$sha" "$BUNDLE")"
