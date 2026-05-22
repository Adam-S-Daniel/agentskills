#!/usr/bin/env bash
# setup.sh — one-time setup for the agentskills repo.
#
# This repo is a Claude Code *plugin marketplace*: every skill ships as a
# plugin under plugins/<name>/. Claude Code users can install skills with
#
#   /plugin marketplace add Adam-S-Daniel/agentskills
#   /plugin install <skill>@agentskills
#
# and don't need this script at all.
#
# This script is for the *other* agent tools (Codex, Gemini, Cursor, the
# generic .agents/.agent dirs) and for using the skills locally without
# installing the marketplace. It links every skill found under
# plugins/*/skills/* into the standard per-agent skill directories:
#
#   ~/.agents/skills             ~/.gemini/skills
#   ~/.agent/skills              ~/.gemini/antigravity/skills
#   ~/.claude/skills             ~/.cursor/skills
#
# Codex discovers skills in ~/.agents/skills, so that link is what makes
# these skills installable to Codex.
#
# Also registers the sync-skills pre-push reminder hook.
#
# Safe to re-run (idempotent). On Windows (Git Bash) it uses `mklink /J`
# directory junctions — no admin required. Run on Windows AND in WSL
# separately; each has its own filesystem and its own $HOME.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGINS_DIR="$REPO_ROOT/plugins"

if [[ ! -d "$PLUGINS_DIR" ]]; then
  echo "ERROR: plugins directory not found at $PLUGINS_DIR" >&2
  exit 1
fi

# Collect every skill directory: plugins/<plugin>/skills/<skill>/SKILL.md
SKILL_DIRS=()
for skill_md in "$PLUGINS_DIR"/*/skills/*/SKILL.md; do
  [[ -f "$skill_md" ]] || continue
  SKILL_DIRS+=("$(dirname "$skill_md")")
done

if [[ ${#SKILL_DIRS[@]} -eq 0 ]]; then
  echo "ERROR: no skills found under $PLUGINS_DIR/*/skills/*" >&2
  exit 1
fi

# Detect platform: Git Bash / MSYS / Cygwin on Windows → junctions.
case "${OSTYPE:-}" in
  msys*|cygwin*|win32*) PLATFORM="windows" ;;
  *)                    PLATFORM="unix" ;;
esac

echo "Platform:  $PLATFORM"
echo "Repo:      $REPO_ROOT"
echo "Skills:    ${#SKILL_DIRS[@]}"
echo "\$HOME:     $HOME"
echo ""

# Per-agent skill homes. Each becomes a real directory holding one link per
# skill (older versions of this script linked the whole directory instead;
# that legacy link is migrated away below).
HOMES=(
  ".agents/skills"
  ".agent/skills"
  ".claude/skills"
  ".gemini/skills"
  ".gemini/antigravity/skills"
  ".cursor/skills"
)

# link_one <link-path> <target-dir> — create one skill link, idempotently.
link_one() {
  local link="$1" target="$2" parent
  parent="$(dirname "$link")"
  [[ -d "$parent" ]] || mkdir -p "$parent"

  if [[ -L "$link" ]]; then
    echo "  ALREADY  $(basename "$link")"
    return
  fi
  if [[ -e "$link" ]]; then
    echo "  CONFLICT $(basename "$link") (exists, not a symlink — skipping)"
    return
  fi

  if [[ "$PLATFORM" = "windows" ]]; then
    local link_win target_win
    link_win="$(cygpath -w "$link")"
    target_win="$(cygpath -w "$target")"
    MSYS_NO_PATHCONV=1 cmd.exe //c "mklink /J \"$link_win\" \"$target_win\"" >/dev/null 2>&1
    if [[ -d "$link" ]]; then echo "  JUNCTION $(basename "$link")"; else echo "  FAILED   $(basename "$link")"; fi
  else
    ln -s "$target" "$link"
    echo "  SYMLINK  $(basename "$link")"
  fi
}

# migrate_legacy <home-skills-path> — remove a legacy whole-directory link so
# we can replace it with a real directory of per-skill links. Removing a
# symlink/junction never touches the directory it points at.
migrate_legacy() {
  local home_skills="$1"
  if [[ "$PLATFORM" = "windows" ]]; then
    local win_path; win_path="$(cygpath -w "$home_skills")"
    if MSYS_NO_PATHCONV=1 cmd.exe //c "fsutil reparsepoint query \"$win_path\"" >/dev/null 2>&1; then
      echo "  MIGRATE  removing legacy junction"
      MSYS_NO_PATHCONV=1 cmd.exe //c "rmdir \"$win_path\"" >/dev/null 2>&1
    fi
  elif [[ -L "$home_skills" ]]; then
    echo "  MIGRATE  removing legacy directory symlink"
    rm "$home_skills"
  fi
}

for rel in "${HOMES[@]}"; do
  home_skills="$HOME/$rel"
  echo "=== $home_skills ==="
  migrate_legacy "$home_skills"
  mkdir -p "$home_skills"
  for sd in "${SKILL_DIRS[@]}"; do
    link_one "$home_skills/$(basename "$sd")" "$sd"
  done
done

echo ""
echo "=== Registering sync-skills pre-push hook ==="
bash "$REPO_ROOT/plugins/sync-skills/skills/sync-skills/setup.sh"

echo ""
echo "Setup complete."
