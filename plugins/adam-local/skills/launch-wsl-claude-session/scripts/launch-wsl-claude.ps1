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
  [switch] $NoWindowsTerminal,                    # fallback: bare wsl.exe (malformed TTY — avoid)
  [switch] $PrintArgs                             # test hook: print the final command line(s)
                                                   # instead of launching anything
)

# wt.exe re-parses its OWN command line and treats an unescaped ';' as a
# subcommand separator (new-tab) — even when the ';' sits inside an argument
# that already arrived as a single, correctly quoted Win32 argv element. Only
# wt's own documented escape protects it: a literal backslash before the
# semicolon (`\;`). Apply this ONLY to arguments headed for wt.exe — the
# -NoWindowsTerminal fallback below invokes wsl.exe directly, with no wt
# tokenizer to strip the backslash back out again.
function ConvertTo-WtEscaped {
  param([string] $Value)
  $Value -replace ';', '\;'
}

# Start-Process -ArgumentList joins array elements with plain spaces and does
# NOT quote them, so a multi-word prompt (or one containing '"') would arrive
# at the child as several argv entries instead of one. This reproduces the
# quoting rules the Win32 C runtime uses to parse a command line back into
# argv (CommandLineToArgvW): backslashes are only special immediately before
# a quote or the end of the string, where they must be doubled, and an
# embedded quote is escaped with one extra backslash.
function ConvertTo-WindowsCommandLineArg {
  param([string] $Value)
  if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
    return $Value
  }
  $sb = [System.Text.StringBuilder]::new()
  [void] $sb.Append('"')
  $i = 0
  while ($i -lt $Value.Length) {
    $backslashes = 0
    while ($i -lt $Value.Length -and $Value[$i] -eq '\') {
      $backslashes++
      $i++
    }
    if ($i -eq $Value.Length) {
      [void] $sb.Append('\' * ($backslashes * 2))
    }
    elseif ($Value[$i] -eq '"') {
      [void] $sb.Append('\' * ($backslashes * 2 + 1))
      [void] $sb.Append('"')
      $i++
    }
    else {
      [void] $sb.Append('\' * $backslashes)
      [void] $sb.Append($Value[$i])
      $i++
    }
  }
  [void] $sb.Append('"')
  return $sb.ToString()
}

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

# Give the new session the FULL login PATH (/snap/bin -> pwsh, ~/.bun/bin -> bun,
# ~/.npm-global/bin, ~/.dotnet, ~/.local/bin, ...) so the agent's subprocesses don't fail
# with "pwsh: command not found". Capture it from an interactive login shell in the distro
# (`bash -lic` — bun/npm-global are added in ~/.bashrc, which plain `-lc` skips) and inject
# it with `env PATH=...`. Do NOT wrap claude in an interactive shell: that grabs the
# ConPTY's process group and the claude TUI exits immediately. `env` is a transparent exec,
# so claude stays a direct child holding the ConPTY (like the working bare-claude launch).
$loginPath = (wsl.exe -d $Distro -- bash -lic 'printf %s "$PATH"' 2>$null | Select-Object -First 1)
$pathArg = if ($loginPath) { "PATH=$loginPath" } else { "PATH=$env:PATH" }
$wslArgs = @('-d', $Distro, '--cd', $Dir, '--', 'env', $pathArg, $claude) + $claudeArgs

if ($NoWindowsTerminal) {
  # Bare wsl.exe gets a malformed TTY; initial-prompt sessions exit immediately here.
  # No wt.exe involved, so no semicolon escaping — just proper Win32 quoting.
  $cmdLine = ($wslArgs | ForEach-Object { ConvertTo-WindowsCommandLineArg $_ }) -join ' '
  if ($PrintArgs) { Write-Output $cmdLine; return }
  Start-Process wsl.exe -ArgumentList $cmdLine
}
else {
  # Windows Terminal provides a proper ConPTY, which the interactive session needs.
  # Escape ';' for wt's own tokenizer, then quote each argument so a
  # multi-word prompt survives as one argv element.
  $wtArgs = @('wsl.exe') + $wslArgs
  $cmdLine = ($wtArgs | ForEach-Object { ConvertTo-WindowsCommandLineArg (ConvertTo-WtEscaped $_) }) -join ' '
  if ($PrintArgs) { Write-Output $cmdLine; return }
  Start-Process wt.exe -ArgumentList $cmdLine
}

Write-Host "Launched detached Claude ($mode) in ${Distro}:${Dir}"
