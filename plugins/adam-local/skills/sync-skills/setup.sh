#!/usr/bin/env bash
# setup.sh — register pre-push git hooks via git config (git 2.54+).
#
# Usage:
#   bash plugins/adam-local/skills/sync-skills/setup.sh
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
# $AGENTSKILLS_REPOS first, matching sync_skills.py: the helper stopped
# guessing clone locations after a stale ~/repos directory outranked the
# real checkout, and this loop should not go on guessing behind its back.
# The two ~/repos paths stay only as a trailing fallback for the machines
# that do use that layout; being wrong here costs a hook registration, not
# an upload.
PRIVATE_REPOS=()
if [[ -n "${AGENTSKILLS_REPOS:-}" ]]; then
  # Split on ';' when the value contains one, else on ':' — the same
  # os.pathsep the helper reads, and the only rule that does not shred a
  # Windows path: 'D:\repos\...' has a colon in it by nature.
  if [[ "$AGENTSKILLS_REPOS" == *";"* ]]; then
    IFS=';' read -r -a agentskills_repo_list <<< "$AGENTSKILLS_REPOS"
  else
    IFS=':' read -r -a agentskills_repo_list <<< "$AGENTSKILLS_REPOS"
  fi
  for repo_entry in "${agentskills_repo_list[@]}"; do
    [[ -n "$repo_entry" ]] && PRIVATE_REPOS+=("$repo_entry")
  done
fi
PRIVATE_REPOS+=(
  "$HOME/repos/agentskills-private"
  "${USERPROFILE:-}/repos/agentskills-private"
)
for private_repo in "${PRIVATE_REPOS[@]}"; do
  [[ -z "$private_repo" ]] && continue
  [[ ! -d "$private_repo/.git" ]] && continue
  # $AGENTSKILLS_REPOS lists every registry clone, not just the private
  # one; only the private repo gets this hook registration.
  [[ "$(basename "$private_repo")" != *private* ]] && continue

  # The private repo may use either the legacy skills/ layout or the newer
  # plugins/<plugin>/skills/sync-skills/ layout (any plugin — resolve by
  # glob so this doesn't hardcode the bundle name); prefer whichever exists.
  private_hook_plugin=""
  for candidate in "$private_repo"/plugins/*/skills/sync-skills/hooks/pre-push; do
    if [[ -f "$candidate" ]]; then
      private_hook_plugin="$candidate"
      break
    fi
  done
  private_hook_legacy="$private_repo/skills/sync-skills/hooks/pre-push"
  if [[ -n "$private_hook_plugin" ]]; then
    target_hook="$private_hook_plugin"
  elif [[ -f "$private_hook_legacy" ]]; then
    target_hook="$private_hook_legacy"
  else
    target_hook="$HOOK_PATH"
  fi

  git config --global hook.sync-skills-private-reminder.event pre-push
  git config --global hook.sync-skills-private-reminder.command "bash \"$target_hook\""
  echo "OK       global hook registered: sync-skills-private-reminder → $target_hook"
  break
done

# ── Remove legacy file-based hooks ───────────────────────────────────
# These paths are archaeology, not resolution: they name where earlier
# versions of this script INSTALLED a file-based hook, so they have to stay
# even though nothing looks for repos there any more. A miss is a no-op.
LEGACY_REPOS=(
  "$SCRIPT_DIR/../../../.."
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
