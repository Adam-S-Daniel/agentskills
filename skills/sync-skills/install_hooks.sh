#!/usr/bin/env bash
# install_hooks.sh — install post-push git hooks for agentskills repos.
#
# Usage:
#   ./install_hooks.sh                         # uses default repo paths
#   ./install_hooks.sh ~/repos/agentskills     # custom paths
set -euo pipefail

# Default repos; override by passing paths as arguments.
DEFAULT_REPOS=(
  "$HOME/repos/agentskills"
  "$HOME/repos/agentskills-private"
)

REPOS=("${@:-${DEFAULT_REPOS[@]}}")

# The hook content is a self-contained bash script.
# shellcheck disable=SC2016  # single-quote intentional — written verbatim
read -r -d '' HOOK_CONTENT <<'HOOK_EOF' || true
#!/usr/bin/env bash
# post-push hook: remind to sync skills if any skills/ folders changed.

changed_skills=$(git diff --name-only HEAD@{push} HEAD 2>/dev/null \
  | grep '^skills/' \
  | awk -F/ '{print $2}' \
  | sort -u)

if [[ -n "$changed_skills" ]]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Skills changed — run 'sync-skills' in Claude to push to claude.ai"
  echo "  Changed: $(echo "$changed_skills" | tr '\n' ' ')"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
fi
HOOK_EOF

for repo in "${REPOS[@]}"; do
  if [[ ! -d "$repo/.git" ]]; then
    echo "SKIP  $repo  (not a git repository)"
    continue
  fi

  hook_path="$repo/.git/hooks/post-push"
  printf '%s\n' "$HOOK_CONTENT" > "$hook_path"
  chmod +x "$hook_path"
  echo "OK    $repo  → .git/hooks/post-push installed"
done
