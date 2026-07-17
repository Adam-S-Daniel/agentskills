#!/usr/bin/env bash
# memory-inventory.sh — read-only inventory of Claude Code auto-memory stores
# under ~/.claude/projects/<munged-path>/memory/.
#
# Usage: bash scripts/memory-inventory.sh [--json]
#
# Never deletes or modifies anything on disk.
set -euo pipefail

usage() {
  echo "Usage: memory-inventory.sh [--json]" >&2
}

json_mode=0
for arg in "$@"; do
  case "$arg" in
    --json)
      json_mode=1
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

[ -d "$HOME/.claude/projects" ] || { echo "ERROR: ~/.claude/projects not found — is this a machine with Claude Code auto-memory?" >&2; exit 1; }

# decode_munged_path <munged_name>
# Prints ONE best-guess absolute path to stdout and returns 0 if a fully-existing
# decode was found; returns 1 (prints nothing) if no candidate exists on disk.
#
# KNOWN, DOCUMENTED LIMITATION: Claude Code's real munging also replaces literal `.`
# in path components with `-` (verified empirically: a repo directory literally named
# `adamdaniel.ai` produces the memory-store folder name `...-adamdaniel-ai`,
# indistinguishable from a repo actually named `adamdaniel-ai`). This algorithm only
# tries the `-`-was-`/` vs `-`-is-literal-hyphen split; it does NOT also try
# substituting `.` for `-`, so directories whose real name contains a literal dot will
# be reported ORPHANED even though the workspace still exists. This is an accepted
# best-guess limitation, not a bug — do not add dot-substitution search.
decode_munged_path() {
  local munged="$1"
  [[ "$munged" == -* ]] || return 1
  _decode_try "/" "${munged:1}"
}

# _decode_try <current_existing_dir> <remaining_munged_suffix>
# DFS backtracking: current_existing_dir is a path already confirmed via [ -d ].
# Tries splitting remaining at each '-' (and at "no split, consume everything as
# the final literal component"), longest final component first.
_decode_try() {
  local current="$1" remaining="$2"
  if [[ -z "$remaining" ]]; then
    [[ -d "$current" ]] && { printf '%s\n' "$current"; return 0; }
    return 1
  fi
  local n=${#remaining} i component candidate_dir
  for (( i=n; i>=0; i-- )); do
    if (( i < n )) && [[ "${remaining:i:1}" != "-" ]]; then
      continue
    fi
    component="${remaining:0:i}"
    [[ -n "$component" ]] || continue
    if [[ "$current" == "/" ]]; then
      candidate_dir="/$component"
    else
      candidate_dir="$current/$component"
    fi
    [[ -d "$candidate_dir" ]] || continue
    if (( i == n )); then
      printf '%s\n' "$candidate_dir"
      return 0
    fi
    if _decode_try "$candidate_dir" "${remaining:i+1}"; then
      return 0
    fi
  done
  return 1
}

# json_escape <string>
# Escapes backslashes and double-quotes for embedding in a JSON string.
json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

shopt -s nullglob

find_tmp="$(mktemp)"
trap 'rm -f "$find_tmp"' EXIT

total_count=0
orphaned_count=0
json_entries=()
text_blocks=()

for memory_dir in "$HOME"/.claude/projects/*/memory; do
  [ -d "$memory_dir" ] || continue

  # Count regular files directly inside memory_dir (maxdepth 1), safely.
  if ! find "$memory_dir" -maxdepth 1 -type f > "$find_tmp" 2>/dev/null; then
    echo "WARNING: 'find' failed while scanning $memory_dir — skipping (this is NOT the same as zero files)" >&2
    continue
  fi
  file_count=$(wc -l < "$find_tmp" | tr -d ' ')

  if [ "$file_count" -eq 0 ]; then
    continue
  fi

  total_count=$((total_count + 1))

  parent_dir="$(dirname "$memory_dir")"
  munged="$(basename "$parent_dir")"

  decoded_path=""
  orphaned=0
  if decoded_path="$(decode_munged_path "$munged")"; then
    orphaned=0
  else
    decoded_path=""
    orphaned=1
    orphaned_count=$((orphaned_count + 1))
  fi

  size_bytes=$(du -sb "$memory_dir" 2>/dev/null | cut -f1) || true
  [ -n "$size_bytes" ] || size_bytes=0
  size_human=$(du -sh "$memory_dir" 2>/dev/null | cut -f1) || true
  [ -n "$size_human" ] || size_human="unknown"

  newest_epoch=$(find "$memory_dir" -maxdepth 1 -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f1) || true
  newest_mtime="unknown"
  if [ -n "$newest_epoch" ]; then
    newest_epoch_int="${newest_epoch%.*}"
    newest_mtime=$(date -d "@$newest_epoch_int" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "unknown")
  fi

  if [ "$json_mode" -eq 1 ]; then
    if [ "$orphaned" -eq 1 ]; then
      path_json="null"
    else
      path_json="\"$(json_escape "$decoded_path")\""
    fi
    entry=$(printf '{"munged":"%s","path":%s,"orphaned":%s,"file_count":%s,"total_size_bytes":%s,"newest_mtime":"%s"}' \
      "$(json_escape "$munged")" \
      "$path_json" \
      "$([ "$orphaned" -eq 1 ] && echo true || echo false)" \
      "$file_count" \
      "$size_bytes" \
      "$(json_escape "$newest_mtime")")
    json_entries+=("$entry")
  else
    block="Store: $munged"$'\n'
    if [ "$orphaned" -eq 1 ]; then
      guess="/${munged:1}"
      guess="${guess//-//}"
      block+="  Path (GUESS, unverified): $guess  [ORPHANED: workspace path no longer exists]"$'\n'
    else
      block+="  Path: $decoded_path"$'\n'
    fi
    block+="  Files: $file_count"$'\n'
    block+="  Size: $size_human"$'\n'
    block+="  Newest file: $newest_mtime"
    text_blocks+=("$block")
  fi
done

if [ "$json_mode" -eq 1 ]; then
  printf '['
  first=1
  for entry in "${json_entries[@]+"${json_entries[@]}"}"; do
    if [ "$first" -eq 1 ]; then
      first=0
    else
      printf ','
    fi
    printf '%s' "$entry"
  done
  printf ']\n'
else
  for block in "${text_blocks[@]+"${text_blocks[@]}"}"; do
    printf '%s\n\n' "$block"
  done
  echo "$total_count stores, $orphaned_count orphaned (workspace path no longer exists)"
fi
