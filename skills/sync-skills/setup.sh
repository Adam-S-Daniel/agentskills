#!/usr/bin/env bash
# setup.sh — register pre-push git hooks via git config (git 2.54+).
#
# Usage:
#   bash skills/sync-skills/setup.sh
#
# Registers a global config-based hook so every push in any repo fires
# the reminder. Cleans up legacy file-based hooks and any stale post-push
# config left over from earlier versions of this script.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_PATH="$SCRIPT_DIR/hooks/pre-push"

if [[ ! -f "$HOOK_PATH" ]]; then
  echo "ERROR: Hook script not found at $HOOK_PATH" >&2
  exit 1
fi

chmod +x "$HOOK_PATH"

# ── Clean up stale config from earlier versions ──────────────────────
# Earlier setup.sh / install_hooks.sh registered the (non-existent)
# post-push event. Remove those stale entries so they don't show up in
# `git hook list`.
for section in hook.sync-skills-reminder hook.sync-skills-private-reminder; do
  if [[ -n "$(git config --global --get "${section}.event" 2>/dev/null || true)" ]]; then
    old_event=$(git config --global --get "${section}.event")
    if [[ "$old_event" = "post-push" ]]; then
      git config --global --remove-section "$section" 2>/dev/null || true
      echo "CLEANED  stale global hook section: $section (was post-push)"
    fi
  fi
done

# ── Register the global pre-push hook for agentskills ────────────────
git config --global hook.sync-skills-reminder.event pre-push
git config --global hook.sync-skills-reminder.command "bash \"$HOOK_PATH\""
echo "OK       global hook registered: sync-skills-reminder → $HOOK_PATH"

# ── Register for agentskills-private if it exists ────────────────────
PRIVATE_REPOS=(
  "$HOME/repos/agentskills-private"
  "${USERPROFILE:-}/repos/agentskills-private"
)
for private_repo in "${PRIVATE_REPOS[@]}"; do
  [[ -z "$private_repo" ]] && continue
  [[ ! -d "$private_repo/.git" ]] && continue

  private_hook="$private_repo/skills/sync-skills/hooks/pre-push"
  if [[ -f "$private_hook" ]]; then
    target_hook="$private_hook"
  else
    target_hook="$HOOK_PATH"
  fi

  git config --global hook.sync-skills-private-reminder.event pre-push
  git config --global hook.sync-skills-private-reminder.command "bash \"$target_hook\""
  echo "OK       global hook registered: sync-skills-private-reminder → $target_hook"
  break
done

# ── Remove legacy file-based hooks ───────────────────────────────────
LEGACY_REPOS=(
  "$SCRIPT_DIR/../.."
  "$HOME/repos/agentskills"
  "$HOME/repos/agentskills-private"
  "${USERPROFILE:-}/repos/agentskills"
  "${USERPROFILE:-}/repos/agentskills-private"
)
for repo in "${LEGACY_REPOS[@]}"; do
  [[ -z "$repo" ]] && continue
  for event in post-push pre-push; do
    legacy_hook="$repo/.git/hooks/$event"
    if [[ -f "$legacy_hook" ]]; then
      rm "$legacy_hook"
      echo "REMOVED  legacy file-based hook: $legacy_hook"
    fi
  done
done

echo ""
echo "Registered pre-push hooks (git hook list):"
git hook list pre-push
