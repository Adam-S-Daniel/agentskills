#!/usr/bin/env bash
# setup.sh — one-time setup for the agentskills repo.
#
# Creates symlinks (or directory junctions on Windows) from the standard
# agent-tool skill directories to this repo's skills/ folder:
#
#   ~/.agents/skills             ~/.gemini/skills
#   ~/.agent/skills              ~/.gemini/antigravity/skills
#   ~/.claude/skills             ~/.cursor/skills
#
# Also registers the sync-skills pre-push reminder hook.
#
# Safe to re-run (idempotent). On Windows (Git Bash) it uses `mklink /J`
# directory junctions — no admin required. Run on Windows AND in WSL
# separately; each has its own filesystem and its own $HOME.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$REPO_ROOT/skills"

if [[ ! -d "$SKILLS_DIR" ]]; then
  echo "ERROR: Skills directory not found at $SKILLS_DIR" >&2
  exit 1
fi

# Detect platform: Git Bash / MSYS / Cygwin on Windows → junctions.
case "${OSTYPE:-}" in
  msys*|cygwin*|win32*) PLATFORM="windows" ;;
  *)                    PLATFORM="unix" ;;
esac

if [[ "$PLATFORM" = "windows" ]]; then
  TARGET_WIN="$(cygpath -w "$SKILLS_DIR")"
  echo "Platform:       Windows (junctions)"
  echo "Target (win):   $TARGET_WIN"
else
  echo "Platform:       Unix (symlinks)"
  echo "Target:         $SKILLS_DIR"
fi
echo "\$HOME:          $HOME"
echo ""

LINKS=(
  ".agents/skills"
  ".agent/skills"
  ".claude/skills"
  ".gemini/skills"
  ".gemini/antigravity/skills"
  ".cursor/skills"
)

for rel in "${LINKS[@]}"; do
  link="$HOME/$rel"
  parent="$(dirname "$link")"
  [[ ! -d "$parent" ]] && mkdir -p "$parent"

  if [[ -L "$link" ]]; then
    echo "ALREADY   $link"
    continue
  fi
  if [[ -e "$link" ]]; then
    echo "CONFLICT  $link  (exists but not a symlink — skipping)"
    continue
  fi

  if [[ "$PLATFORM" = "windows" ]]; then
    link_win="$(cygpath -w "$link")"
    MSYS_NO_PATHCONV=1 cmd.exe //c "mklink /J \"$link_win\" \"$TARGET_WIN\"" >/dev/null 2>&1
    if [[ -d "$link" ]]; then
      echo "JUNCTION  $link"
    else
      echo "FAILED    $link"
    fi
  else
    ln -s "$SKILLS_DIR" "$link"
    echo "SYMLINK   $link"
  fi
done

echo ""
echo "=== Registering sync-skills pre-push hook ==="
bash "$REPO_ROOT/skills/sync-skills/setup.sh"

echo ""
echo "Setup complete."
