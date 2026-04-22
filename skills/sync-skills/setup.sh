#!/usr/bin/env bash
# setup.sh — register post-push git hooks via git config (git 2.54+).
#
# Usage:
#   bash skills/sync-skills/setup.sh
#
# Registers a global config-based hook so every push in any repo fires
# the reminder. Removes any legacy .git/hooks/post-push entries.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_PATH="$SCRIPT_DIR/hooks/post-push"

if [[ ! -f "$HOOK_PATH" ]]; then
  echo "ERROR: Hook script not found at $HOOK_PATH" >&2
  exit 1
fi

chmod +x "$HOOK_PATH"

# Register the global config-based hook for agentskills
git config --global hook.sync-skills-reminder.event post-push
git config --global hook.sync-skills-reminder.command "bash \"$HOOK_PATH\""
echo "OK    global hook registered: sync-skills-reminder → $HOOK_PATH"

# Register for agentskills-private if it exists and has its own hook
PRIVATE_REPOS=(
  "$HOME/repos/agentskills-private"
  "${USERPROFILE:-}/repos/agentskills-private"
)
for private_repo in "${PRIVATE_REPOS[@]}"; do
  [[ -z "$private_repo" ]] && continue
  [[ ! -d "$private_repo/.git" ]] && continue

  private_hook="$private_repo/skills/sync-skills/hooks/post-push"
  if [[ -f "$private_hook" ]]; then
    target_hook="$private_hook"
  else
    target_hook="$HOOK_PATH"
  fi

  git config --global hook.sync-skills-private-reminder.event post-push
  git config --global hook.sync-skills-private-reminder.command "bash \"$target_hook\""
  echo "OK    global hook registered: sync-skills-private-reminder → $target_hook"
  break
done

# Remove legacy .git/hooks/post-push entries from agentskills repos
LEGACY_REPOS=(
  "$SCRIPT_DIR/../.."
  "$HOME/repos/agentskills"
  "$HOME/repos/agentskills-private"
)
for repo in "${LEGACY_REPOS[@]}"; do
  legacy_hook="$repo/.git/hooks/post-push"
  if [[ -f "$legacy_hook" ]]; then
    rm "$legacy_hook"
    echo "REMOVED  legacy hook at $legacy_hook"
  fi
done

echo ""
echo "Registered hooks:"
git hook list post-push
