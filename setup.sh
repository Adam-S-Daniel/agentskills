#!/usr/bin/env bash
# setup.sh — one-time setup for the agentskills repo.
#
# This repo is a Claude Code *plugin marketplace*: skills ship in bundle
# plugins under plugins/<bundle>/skills/<skill>/. Claude Code users can
# install a bundle with
#
#   /plugin marketplace add Adam-S-Daniel/agentskills
#   /plugin install adam@agentskills
#
# invoke its skills as /<bundle>:<skill> (e.g. /adam:pin-actions-to-sha),
# and don't need this script at all.
#
# This script is for the *other* agent tools (Codex, Gemini, Cursor, the
# generic .agents/.agent dirs) and for using the skills locally without
# installing the marketplace. It links every skill found under
# plugins/*/skills/* into the standard per-agent skill directories:
#
#   ~/.agents/skills             ~/.gemini/skills
#   ~/.agent/skills              ~/.gemini/antigravity/skills
#   ~/.cursor/skills
#
# Claude Code is intentionally NOT in that list. It is served by the plugin
# marketplace (/plugin marketplace add Adam-S-Daniel/agentskills). Linking the
# same skills into ~/.claude/skills as well would double-load them — once as a
# namespaced marketplace plugin and once as a personal skill — which wastes
# context and makes invocation ambiguous. This script removes any such links it
# created in earlier versions (see dedup_claude_code_dir below). Background:
# docs/2026-06-05-skill-discovery-and-centralized-strategy.md.
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
  ".gemini/skills"
  ".gemini/antigravity/skills"
  ".cursor/skills"
)

# PowerShell parses its own quoting sanely (unlike cmd.exe, which cannot
# digest the \"-escaped inner quotes MSYS builds into the command line —
# live-debugged 2026-07-17: `MSYS_NO_PATHCONV=1 cmd.exe /c "mklink /J
# \"C:\path\" \"C:\path\""` fails with "The filename, directory name, or
# volume label syntax is incorrect", but the identical command with the
# inner quotes stripped succeeds — the quoting layering was the bug, not the
# operation). Single quotes inside the -Command string below are safe
# because Windows paths in this repo's layout never contain single quotes or
# apostrophes. -LiteralPath + -Force also sees BOTH reparse point flavors
# (Junction and SymbolicLink), which the old fsutil-based detection missed —
# legacy links created from Git Bash/WSL can be POSIX-style symlinks, not
# junctions. Uses powershell.exe (Windows PowerShell 5.1), not pwsh, since a
# stock Windows install isn't guaranteed to have PowerShell 7.

# win_link_type <msys-path> — echoes Junction/SymbolicLink/empty; rc 0 iff reparse point
win_link_type() {
  local p; p="$(cygpath -w "$1")"
  local t
  t=$(MSYS_NO_PATHCONV=1 powershell.exe -NoProfile -NonInteractive -Command \
    "try { (Get-Item -LiteralPath '$p' -Force -ErrorAction Stop).LinkType } catch { '' }" 2>/dev/null </dev/null | tr -d '\r')
  [[ -n "$t" ]] && { echo "$t"; return 0; } || return 1
}

# win_remove_link <msys-path> — removes the reparse point itself, never recursing into the target
win_remove_link() {
  local p; p="$(cygpath -w "$1")"
  MSYS_NO_PATHCONV=1 powershell.exe -NoProfile -NonInteractive -Command \
    "[System.IO.Directory]::Delete('$p')" </dev/null >/dev/null 2>&1
}

# win_make_junction <msys-link> <msys-target> — rc 0 iff the junction exists afterwards
win_make_junction() {
  local l t; l="$(cygpath -w "$1")"; t="$(cygpath -w "$2")"
  MSYS_NO_PATHCONV=1 powershell.exe -NoProfile -NonInteractive -Command \
    "New-Item -ItemType Junction -Path '$l' -Target '$t' | Out-Null" </dev/null >/dev/null 2>&1
  [[ -d "$1" ]]
}

# remove_stale_repo_link <link-path> — if <link> is a link/junction whose
# target lies under $PLUGINS_DIR but no longer exists (stale after a repo
# restructure moved the skill to a new bundle path), remove it so link_one
# can recreate it against the new path. Links pointing anywhere outside
# $PLUGINS_DIR are NEVER touched, even when dangling — they're the user's.
# Returns 0 if a stale link was removed, 1 otherwise.
remove_stale_repo_link() {
  local link="$1" existing
  if [[ "$PLATFORM" = "windows" ]]; then
    # Reparse point exists (junction or symlink)…
    win_link_type "$link" >/dev/null || return 1
    existing="$(readlink "$link" 2>/dev/null || true)"
    # MSYS can't always read a junction's target; when it can't, leave the
    # link untouched rather than guess (same conservative posture as unix).
    [[ -n "$existing" ]] || return 1
    case "$existing" in
      "$PLUGINS_DIR"/*)
        # …its target is ours, and the target directory is gone → stale.
        if [[ ! -e "$existing" ]]; then
          echo "  RELINK   $(basename "$link") (stale plugins/ target)"
          win_remove_link "$link"
          return 0
        fi ;;
    esac
  elif [[ -L "$link" ]]; then
    existing="$(readlink "$link")"
    case "$existing" in
      "$PLUGINS_DIR"/*)
        if [[ ! -e "$link" ]]; then
          echo "  RELINK   $(basename "$link") (stale plugins/ target)"
          rm "$link"
          return 0
        fi ;;
    esac
  fi
  return 1
}

# link_one <link-path> <target-dir> — create one skill link, idempotently.
link_one() {
  local link="$1" target="$2" parent
  parent="$(dirname "$link")"
  [[ -d "$parent" ]] || mkdir -p "$parent"

  remove_stale_repo_link "$link" || true

  if [[ -L "$link" ]]; then
    echo "  ALREADY  $(basename "$link")"
    return
  fi
  # MSYS does not report junctions as symlinks (-L is false for them), so a
  # healthy junction from a prior run would otherwise fall through to
  # CONFLICT below — check reparse-point-ness explicitly on Windows first.
  if [[ "$PLATFORM" = "windows" ]] && [[ -e "$link" ]] && win_link_type "$link" >/dev/null; then
    echo "  ALREADY  $(basename "$link")"
    return
  fi
  if [[ -e "$link" ]]; then
    echo "  CONFLICT $(basename "$link") (exists, not a symlink — skipping)"
    return
  fi

  if [[ "$PLATFORM" = "windows" ]]; then
    if win_make_junction "$link" "$target"; then
      echo "  JUNCTION $(basename "$link")"
    else
      echo "  FAILED   $(basename "$link")"
    fi
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
    if win_link_type "$home_skills" >/dev/null; then
      echo "  MIGRATE  removing legacy reparse point"
      win_remove_link "$home_skills"
    fi
  elif [[ -L "$home_skills" ]]; then
    echo "  MIGRATE  removing legacy directory symlink"
    rm "$home_skills"
  fi
}

# dedup_claude_code_dir — earlier versions of this script also linked skills
# into ~/.claude/skills. Claude Code is now served by the marketplace, so remove
# any links we previously created there to avoid double-loading. Only links that
# point back into THIS repo (plus a legacy whole-directory link) are removed;
# real personal skills the user keeps in ~/.claude/skills are left untouched.
dedup_claude_code_dir() {
  local cc="$HOME/.claude/skills"
  migrate_legacy "$cc"            # legacy whole-directory link at ~/.claude/skills
  [[ -d "$cc" ]] || return 0
  echo "=== $cc (de-dup: marketplace owns Claude Code) ==="
  local sd link
  for sd in "${SKILL_DIRS[@]}"; do
    link="$cc/$(basename "$sd")"
    if [[ "$PLATFORM" = "windows" ]]; then
      if win_link_type "$link" >/dev/null; then
        echo "  UNLINK   $(basename "$link")"
        win_remove_link "$link"
      fi
    elif [[ -L "$link" ]]; then
      case "$(readlink "$link")" in
        "$PLUGINS_DIR"/*) echo "  UNLINK   $(basename "$link")"; rm "$link" ;;
      esac
    fi
  done
}

dedup_claude_code_dir

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
# Resolve the sync-skills setup script by glob so this file doesn't hardcode
# which bundle plugin the skill lives in.
SYNC_SKILLS_SETUP=""
for candidate in "$PLUGINS_DIR"/*/skills/sync-skills/setup.sh; do
  if [[ -f "$candidate" ]]; then
    SYNC_SKILLS_SETUP="$candidate"
    break
  fi
done
if [[ -z "$SYNC_SKILLS_SETUP" ]]; then
  echo "ERROR: sync-skills setup.sh not found under $PLUGINS_DIR/*/skills/sync-skills/" >&2
  exit 1
fi
bash "$SYNC_SKILLS_SETUP"

echo ""
echo "=== Converging ~/.claude/settings.json (marketplace + plugin enablement) ==="
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "  WARNING  no python3/python on PATH — skipping settings.json convergence"
else
  "$PYTHON_BIN" - <<'PYEOF'
import copy
import io
import json
import os

SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")

# Marketplace registration + bundle enablement every machine should converge
# on. autoUpdate is a real (optional) boolean field on marketplace entries —
# verified against the claude 2.1.211 binary's zod schema.
TARGET_MARKETPLACES = {
    "agentskills": {
        "source": {"source": "github", "repo": "Adam-S-Daniel/agentskills"},
        "autoUpdate": True,
    },
    "agentskills-private": {
        "source": {"source": "github", "repo": "Adam-S-Daniel/agentskills-private"},
        "autoUpdate": True,
    },
}
TARGET_ENABLED_PLUGINS = {"adam@agentskills": True}


def deep_merge(dst, src):
    """Merge src into dst in place, recursing into nested dicts and
    overwriting only the leaf keys src specifies. Keys in dst that src
    doesn't mention (sibling marketplaces, other plugins, extra fields
    like installLocation) are left alone."""
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = copy.deepcopy(value)


settings = {}
if os.path.exists(SETTINGS_PATH):
    with io.open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        raw = f.read()
    if raw.strip():
        try:
            loaded = json.loads(raw)
        except ValueError as exc:
            print("settings: WARNING invalid JSON in %s (%s) - left untouched" % (SETTINGS_PATH, exc))
            settings = None
        else:
            if isinstance(loaded, dict):
                settings = loaded
            else:
                print("settings: WARNING %s does not contain a JSON object - left untouched" % SETTINGS_PATH)
                settings = None

if settings is not None:
    original = copy.deepcopy(settings)

    settings.setdefault("extraKnownMarketplaces", {})
    deep_merge(settings["extraKnownMarketplaces"], TARGET_MARKETPLACES)

    settings.setdefault("enabledPlugins", {})
    deep_merge(settings["enabledPlugins"], TARGET_ENABLED_PLUGINS)

    if settings == original:
        print("settings: unchanged")
    else:
        settings_dir = os.path.dirname(SETTINGS_PATH)
        if settings_dir and not os.path.isdir(settings_dir):
            os.makedirs(settings_dir)
        tmp_path = SETTINGS_PATH + ".tmp"
        with io.open(tmp_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(settings, indent=2))
            f.write("\n")
        if os.path.exists(SETTINGS_PATH):
            os.remove(SETTINGS_PATH)
        os.rename(tmp_path, SETTINGS_PATH)
        print("settings: updated")
PYEOF
fi

echo ""
echo "Setup complete."
