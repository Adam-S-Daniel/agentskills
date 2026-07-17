#!/usr/bin/env bash
# memory-migrate.sh — copy a Claude Code auto-memory store into a repo's
# git-tracked .claude/memory/ directory, and print the settings.json snippet
# needed to point autoMemoryDirectory at it.
#
# Usage: memory-migrate.sh [--force] <store-dir> <repo-dir>
#   --force must appear BEFORE the two positional arguments if given.
#
# Never deletes anything. Never edits <repo-dir>/.claude/settings.json itself —
# only prints the JSON snippet the human should add.
set -euo pipefail

usage() {
  echo "Usage: memory-migrate.sh [--force] <store-dir> <repo-dir>" >&2
  echo "  (--force must appear before the two positional arguments, if given)" >&2
}

force=0
if [ "${1-}" = "--force" ]; then
  force=1
  shift
fi

if [ "$#" -ne 2 ]; then
  usage
  exit 2
fi

STORE_DIR="$1"
REPO_DIR="$2"

[ -d "$HOME/.claude/projects" ] || { echo "ERROR: ~/.claude/projects not found — is this a machine with Claude Code auto-memory?" >&2; exit 1; }

[ -d "$STORE_DIR" ] || { echo "ERROR: store directory not found: $STORE_DIR" >&2; exit 1; }

# Count regular files directly inside STORE_DIR (maxdepth 1), safely.
# NUL-delimited so filenames containing spaces/newlines are handled correctly
# by the read loops below.
store_find_tmp="$(mktemp)"
trap 'rm -f "$store_find_tmp"' EXIT
if ! find "$STORE_DIR" -maxdepth 1 -type f -print0 > "$store_find_tmp" 2>/dev/null; then
  echo "ERROR: 'find' failed while scanning $STORE_DIR" >&2
  exit 1
fi
store_file_count=$(tr -cd '\0' < "$store_find_tmp" | wc -c | tr -d ' ')
[ "$store_file_count" -ge 1 ] || { echo "ERROR: store is empty, nothing to migrate" >&2; exit 1; }

[ -d "$REPO_DIR" ] || { echo "ERROR: repo directory not found: $REPO_DIR" >&2; exit 1; }

git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "ERROR: $REPO_DIR is not a git repository" >&2; exit 1; }

dest="$REPO_DIR/.claude/memory"
mkdir -p "$dest"

# Preflight conflict check: collect ALL conflicting filenames before deciding.
conflicts=()
while IFS= read -r -d '' f; do
  fname="$(basename "$f")"
  if [ -e "$dest/$fname" ]; then
    conflicts+=("$fname")
  fi
done < "$store_find_tmp"

if [ "${#conflicts[@]}" -gt 0 ] && [ "$force" -ne 1 ]; then
  echo "ERROR: the following files already exist in $dest and would be overwritten:" >&2
  for c in "${conflicts[@]}"; do
    echo "  $c" >&2
  done
  echo "Re-run with --force to overwrite." >&2
  exit 1
fi

# Copy every regular file directly inside STORE_DIR into dest, preserving mtimes.
# Read filenames (NUL-delimited) from the real tmp file, not a pipe, so this
# loop is not a subshell.
copied_count=0
while IFS= read -r -d '' f; do
  cp -p -- "$f" "$dest/"
  copied_count=$((copied_count + 1))
done < "$store_find_tmp"

echo "Copied $copied_count file(s) into $dest"

# Compute the autoMemoryDirectory value.
repo_abs=$(cd "$REPO_DIR" && pwd -P)
home_abs=$(cd "$HOME" && pwd -P)

auto_memory_dir=""
if [ "$repo_abs" = "$home_abs" ]; then
  auto_memory_dir="~/.claude/memory"
elif [[ "$repo_abs" == "$home_abs"/* ]]; then
  rel="${repo_abs#"$home_abs"/}"
  auto_memory_dir="~/$rel/.claude/memory"
else
  auto_memory_dir="$repo_abs/.claude/memory"
  echo "WARNING: $REPO_DIR is not under \$HOME ($HOME); using an absolute path for autoMemoryDirectory. This form is NOT portable across machines with different home/repo layouts since it isn't ~-relative." >&2
fi

echo
echo "Add this to $REPO_DIR/.claude/settings.json:"
printf '{"autoMemoryDirectory": "%s"}\n' "$auto_memory_dir"

echo
echo "REMINDER: if $REPO_DIR is a PUBLIC repo, review the copied memory files in"
echo "$dest for secrets, PII, credentials, or internal-only details before committing —"
echo "once migrated in-repo, this memory becomes as public as the repo."
