---
name: sync-claude-settings-between-wsl-and-windows
description: Sync Claude Code settings.json between a Windows home and a WSL home. Triggers on requests to "sync Claude Code settings", "merge my settings.json", "keep WSL and Windows Claude settings in sync", or mentions of reconciling %USERPROFILE%\.claude\settings.json with ~/.claude/settings.json in WSL. Backs up both files with an Eastern-time-stamped prefix, then merges per property (union for permissions.allow/deny and spinnerVerbs.verbs; more-recently-modified-wins with optional prompt for scalars like theme, model, effortLevel, tui, verbose, etc.; statusLine and defaultShell are kept per-file). Preserves each file's native newline format (CRLF for Windows, LF for WSL) and UTF-8 BOM presence. Windows-only (needs PowerShell 7+ and access to \\wsl.localhost\<distro>\...). Use when the user wants the two settings.json files reconciled, not when they want a single file edited in place.
license: MIT
---

# sync-claude-settings-between-wsl-and-windows

Keep a user's Claude Code `settings.json` in sync across their Windows host and a WSL distro, without clobbering per-environment preferences.

## When to use

Trigger on any of:

- "Sync my Claude Code settings between Windows and WSL"
- "Merge settings.json from both sides"
- "My WSL settings.json drifted from the Windows one, reconcile them"
- Mentions of `%USERPROFILE%\.claude\settings.json` AND `~/.claude/settings.json`

Do **not** trigger for single-file edits, or for syncing unrelated config (e.g., `.vscode/settings.json`, `git config`).

## How it works

Given two files:

- Windows: `%USERPROFILE%\.claude\settings.json` (or `$env:CLAUDE_SETTINGS_WINDOWS`)
- WSL:     `~/.claude/settings.json` in the default distro (or `$env:CLAUDE_SETTINGS_WSL`, or auto-detected via `wsl.exe -- bash -lc 'wslpath -w "$HOME/.claude/settings.json"'`)

The script:

1. Writes a timestamped backup of each file next to the original (`YYYYMMDD-HHMMSS-ET-settings.json.bak`). Timestamp is US/Eastern.
2. Loads both as JSON (PowerShell 7+ `ConvertFrom-Json -AsHashtable`).
3. Applies per-key merge rules (below).
4. Re-serializes each side, **preserving the original newline style (CRLF vs LF) and UTF-8 BOM presence** of that file, and any trailing newline.

## Merge rules

| Key | Rule |
| --- | --- |
| `permissions.allow` | Union of both sides' arrays (deduped). |
| `permissions.deny` | Union of both sides' arrays (deduped). |
| `permissions.defaultMode` | More-recently-modified side wins, or only-existing side wins — **prompt** for confirmation. |
| `permissions.*` (other sub-keys) | **Prompt**. |
| `statusLine` | Ignored — each file keeps its own value. |
| `defaultShell` | Ignored — each file keeps its own value. |
| `autoDreamEnabled` | Newer-wins, **prompt**. |
| `showMessageTimestamps` | Newer-wins, no prompt. |
| `spinnerVerbs.verbs` | Union of both sides' arrays (deduped). |
| `spinnerVerbs.*` (other sub-keys) | **Prompt**. |
| `effortLevel` | Newer-wins, **prompt**. |
| `tui` | Newer-wins, **prompt**. |
| `skipDangerousModePermissionPrompt` | Newer-wins, **prompt**. |
| `theme` | Newer-wins, **prompt**. |
| `verbose` | Newer-wins, **prompt**. |
| `remoteControlAtStartup` | Newer-wins, **prompt**. |
| `agentPushNotifEnabled` | Newer-wins, **prompt**. |
| `model` | Newer-wins, **prompt**. |
| Anything else (top-level or under `permissions`/`spinnerVerbs`) | **Prompt**. |

"Newer" = whichever settings.json has the more recent `LastWriteTimeUtc` (compared once, at the start of the run).

Prompts offer: `[w]indows` / `[l]inux` (WSL) / `[n]ewer` (default) / `[s]kip` (each file keeps its own current value for that key).

## Invocation

The logic lives in `scripts/Sync-ClaudeSettings.ps1`. Run it from Windows PowerShell 7+:

```powershell
# Interactive (prompts for conflicts on listed + unlisted keys)
pwsh -File .\scripts\Sync-ClaudeSettings.ps1

# Non-interactive — apply newer-wins for every PROMPT key
pwsh -File .\scripts\Sync-ClaudeSettings.ps1 -AssumeYes

# Dry run — show the plan, no writes
pwsh -File .\scripts\Sync-ClaudeSettings.ps1 -DryRun

# Point at a specific WSL distro, override paths
pwsh -File .\scripts\Sync-ClaudeSettings.ps1 -WslDistro Ubuntu-22.04
pwsh -File .\scripts\Sync-ClaudeSettings.ps1 `
    -WindowsSettingsPath "$env:USERPROFILE\.claude\settings.json" `
    -WslSettingsPath "\\wsl.localhost\Ubuntu\home\$env:USER\.claude\settings.json"
```

### Parameters

| Parameter | Env var | Default |
| --- | --- | --- |
| `-WindowsSettingsPath` | `CLAUDE_SETTINGS_WINDOWS` | `$env:USERPROFILE\.claude\settings.json` |
| `-WslSettingsPath`     | `CLAUDE_SETTINGS_WSL`     | Auto-detected via `wsl.exe -- wslpath -w "$HOME/.claude/settings.json"` |
| `-WslDistro`           | `CLAUDE_SETTINGS_WSL_DISTRO` | empty (uses WSL's default distro) |
| `-AssumeYes`           | — | off (interactive) |
| `-DryRun`              | — | off |

No username is hardcoded anywhere in the script; paths come from `$env:USERPROFILE`, `$env:CLAUDE_SETTINGS_*`, or `wsl.exe` auto-detection.

## Requirements

- Windows host (the script uses `\\wsl.localhost\...` UNC paths via the WSL adapter, and invokes `wsl.exe`).
- **PowerShell 7+** — required for `ConvertFrom-Json -AsHashtable`.
- WSL installed with at least one distro, if `-WslSettingsPath` isn't supplied.
- Both `settings.json` files already exist. The script does not create missing files.

## Operational notes

- The script rewrites the JSON via PowerShell's serializer; exotic formatting (custom indent, trailing commas) is **not** preserved. Newlines, BOM, and trailing newline **are** preserved.
- Comments in settings.json are not supported (Claude Code uses plain JSON, not JSONC).
- Backups live next to the originals. Clean them up periodically if you run this often.
- If you need to roll back: the backup filenames are `YYYYMMDD-HHMMSS-ET-settings.json.bak`; overwrite the live file with the backup.

## Troubleshooting

- **"Could not auto-detect WSL settings.json path"** — pass `-WslSettingsPath` explicitly, or set `$env:CLAUDE_SETTINGS_WSL`, or install WSL.
- **"This script requires PowerShell 7+"** — install PowerShell 7 (`winget install Microsoft.PowerShell`), then invoke with `pwsh` rather than `powershell`.
- **UNC access denied to `\\wsl.localhost\...`** — the WSL distro has to be running, or at least startable, for the UNC adapter to serve files. `wsl.exe -l -v` should show the distro.
