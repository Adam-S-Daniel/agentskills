<#
.SYNOPSIS
  Launch a detached, interactive Claude Code session inside WSL from Windows.

.DESCRIPTION
  Opens a new Windows Terminal window running `claude` inside a WSL distro, rooted at
  -Dir. By default opens a brand-new session via --session-id (bypasses the agents-view
  landing). If -Prompt is given, seeds an interactive session with that prompt instead
  (no -p, so it stays open). See the skill's SKILL.md for the prerequisites (directory
  trust, remoteControlAtStartup) and the reasons behind each choice.

.EXAMPLE
  .\launch-wsl-claude.ps1 -Dir /home/passp/repos/GHA-bench
.EXAMPLE
  .\launch-wsl-claude.ps1 -Dir /home/passp/repos/GHA-bench -Prompt "Stand by for instructions."
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)] [string] $Dir,   # WSL path, e.g. /home/passp/repos/GHA-bench
  [string] $Prompt,                               # optional initial prompt -> initial-prompt mode
  [string] $Distro = 'Ubuntu',                    # WSL distro
  [string] $RemoteControlName,                    # optional: adds --remote-control <name>
  [switch] $NoWindowsTerminal                     # fallback: bare wsl.exe (malformed TTY — avoid)
)

# Resolve the absolute claude binary path inside WSL. `command -v claude` often comes
# back empty over `wsl.exe` (PATH from ~/.local/bin isn't set in that shell), so fall
# back to known install locations before giving up.
$resolver = 'command -v claude || for p in "$HOME/.local/bin/claude" "$HOME/.claude/local/claude" /usr/local/bin/claude /usr/bin/claude; do [ -x "$p" ] && echo "$p" && break; done'
$claude = wsl.exe -d $Distro -- bash -lc $resolver 2>$null | Select-Object -First 1
if ($claude) { $claude = $claude.Trim() }
if (-not $claude) {
  Write-Error "claude not found in WSL distro '$Distro' — is Claude Code installed there?"
  exit 1
}

# Build the claude argument list.
$claudeArgs = @()
if ($RemoteControlName) { $claudeArgs += @('--remote-control', $RemoteControlName) }

if ($Prompt) {
  # Initial-prompt mode. The WHOLE prompt must be ONE argument, or Claude only receives
  # the first word. No -p/--print, so the session stays interactive after the first turn.
  $claudeArgs += $Prompt
  $mode = 'initial-prompt'
}
else {
  # Default: open a fresh session directly by id (skips the agents-view landing).
  $claudeArgs += @('--session-id', [guid]::NewGuid().ToString())
  $mode = 'session-id'
}

# wsl.exe args: set the working dir, then run claude under a login+interactive shell so
# the new session gets the FULL login PATH (/snap/bin -> pwsh, ~/.bun/bin -> bun,
# ~/.dotnet, ~/.npm-global/bin, ~/.local/bin, ...). Without `bash -lic`, `wsl.exe -- claude`
# runs under WSL's reduced default PATH and the agent's subprocesses fail with
# "pwsh: command not found" / "bun: not found". `exec "$@"` then replaces the shell with
# claude, which inherits the PATH and the ConPTY.
$wslArgs = @('-d', $Distro, '--cd', $Dir, '--', 'bash', '-lic', 'exec "$@"', 'bash', $claude) + $claudeArgs

if ($NoWindowsTerminal) {
  # Bare wsl.exe gets a malformed TTY; initial-prompt sessions exit immediately here.
  Start-Process wsl.exe -ArgumentList $wslArgs
}
else {
  # Windows Terminal provides a proper ConPTY, which the interactive session needs.
  Start-Process wt.exe -ArgumentList (@('wsl.exe') + $wslArgs)
}

Write-Host "Launched detached Claude ($mode) in ${Distro}:${Dir}"
