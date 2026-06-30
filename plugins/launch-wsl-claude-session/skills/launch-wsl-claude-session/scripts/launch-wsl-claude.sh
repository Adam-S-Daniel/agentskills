#!/usr/bin/env bash
# Launch a detached, interactive Claude Code session inside WSL — for use WHEN CLAUDE
# ITSELF IS RUNNING INSIDE WSL/Linux. It uses Windows interop (wt.exe) to open a real
# Windows Terminal window hosting the WSL session, so the result is identical to the
# Windows-host path (launch-wsl-claude.ps1): a new, interactive, remote-controllable
# Claude session in the target WSL directory.
#
# Usage:
#   ./launch-wsl-claude.sh --dir /home/passp/repos/GHA-bench
#   ./launch-wsl-claude.sh --dir /home/passp/repos/GHA-bench --prompt "Stand by for instructions."
set -euo pipefail

DIR=""; PROMPT=""; DISTRO="Ubuntu"; RC_NAME=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dir)                 DIR="$2"; shift 2;;
    --prompt)              PROMPT="$2"; shift 2;;
    --distro)              DISTRO="$2"; shift 2;;
    --remote-control-name) RC_NAME="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$DIR" ] || { echo "--dir is required (a WSL path, e.g. /home/passp/repos/GHA-bench)" >&2; exit 2; }

# Resolve the absolute claude binary. command -v usually works in-context here, but a
# non-login shell may lack ~/.local/bin on PATH, so fall back to known install paths.
CLAUDE="$(command -v claude || true)"
if [ -z "$CLAUDE" ]; then
  for p in "$HOME/.local/bin/claude" "$HOME/.claude/local/claude" /usr/local/bin/claude /usr/bin/claude; do
    [ -x "$p" ] && CLAUDE="$p" && break
  done
fi
[ -n "$CLAUDE" ] || { echo "claude not found in this WSL environment — is Claude Code installed?" >&2; exit 1; }

# wt.exe is reachable from WSL via Windows interop (appendWindowsPath). It gives the
# session a proper ConPTY — a bare wsl/console spawn gets a malformed TTY and an
# initial-prompt session would exit immediately.
WT="$(command -v wt.exe || true)"
[ -n "$WT" ] || { echo "wt.exe not found — needs WSL with Windows interop + Windows Terminal installed" >&2; exit 1; }

claude_args=()
[ -n "$RC_NAME" ] && claude_args+=(--remote-control "$RC_NAME")
if [ -n "$PROMPT" ]; then
  # Initial-prompt mode: the WHOLE prompt is ONE argument or Claude gets only the first
  # word. No -p/--print, so the session stays interactive after the first turn.
  claude_args+=("$PROMPT")
  MODE="initial-prompt"
else
  # Default: open a fresh session directly by id (skips the agents-view landing).
  claude_args+=(--session-id "$(cat /proc/sys/kernel/random/uuid)")
  MODE="session-id"
fi

# Give the new session the FULL login PATH (/snap/bin -> pwsh, ~/.bun/bin -> bun,
# ~/.npm-global/bin, ~/.dotnet, ~/.local/bin, ...) so the agent's own subprocesses don't
# fail with "pwsh: command not found". We capture it from an interactive login shell
# (`bash -lic` — bun/npm-global are added in ~/.bashrc, so plain `-lc` misses them) and
# inject it with `env PATH=...`. We must NOT wrap claude in an interactive shell to do
# this: an interactive bash grabs the ConPTY's process group and the claude TUI then
# exits immediately. `env` is a transparent exec, so claude stays a direct child holding
# the ConPTY (exactly like the bare-claude launch that works) — just with the right PATH.
LOGIN_PATH="$(bash -lic 'printf %s "$PATH"' 2>/dev/null)"
"$WT" wsl.exe -d "$DISTRO" --cd "$DIR" -- env "PATH=${LOGIN_PATH:-$PATH}" "$CLAUDE" "${claude_args[@]}" &
disown 2>/dev/null || true
echo "Launched detached Claude ($MODE) in ${DISTRO}:${DIR}"
