---
name: sync-cc-settings-between-wsl-and-windows
description: Sync Claude Code settings.json between a Windows home and a WSL home. Triggers on requests to "sync Claude Code settings", "merge my settings.json", "keep WSL and Windows Claude settings in sync", or mentions of reconciling %USERPROFILE%\.claude\settings.json with ~/.claude/settings.json in WSL. Backs up both files with an Eastern-time-stamped prefix, then merges per property (union for permissions.allow/ask/deny and spinnerVerbs.verbs; per-key merge for env; more-recently-modified-wins with optional prompt for scalars like theme, model, effortLevel; OS-bound keys such as hooks, statusLine, defaultShell, sandbox, apiKeyHelper, permissions.additionalDirectories and env.CCSTATUSLINE_WIDTH are kept per-file and never copied across). Preserves each file's existing newline style, UTF-8 BOM presence and trailing newline. Windows-only (needs PowerShell 7+ and access to the WSL UNC share). Use when the user wants the two settings.json files reconciled, not when they want a single file edited in place.
license: MIT
compatibility: Requires Windows with PowerShell 7+ and WSL2 (UNC access to \\wsl.localhost); local execution only
---

# sync-cc-settings-between-wsl-and-windows

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
3. Applies per-key merge rules (below), building each file's output separately.
4. Emits top-level and nested keys in a canonical order (known keys first, unknown keys alphabetical), and sorts the unioned arrays ordinally, so both files get the same content for every shared key. They still differ on purpose in per-file keys, skipped conflicts, newline style and BOM.
5. Re-serializes each side, **preserving that file's existing newline style (CRLF or LF, whatever it already uses), UTF-8 BOM presence and trailing newline**. It does not assume Windows means CRLF.

## Merge rules

| Key | Rule |
| --- | --- |
| `permissions.allow` | Union of both sides' arrays (deduped, sorted ordinally). |
| `permissions.ask` | Union of both sides' arrays (deduped, sorted ordinally). |
| `permissions.deny` | Union of both sides' arrays (deduped, sorted ordinally). |
| `permissions.additionalDirectories` | **Per-file** — each file keeps its own value; never copied across. |
| `permissions.defaultMode` | Newer-wins, **prompt**. |
| `permissions.*` (other sub-keys) | Newer-wins, **prompt**. |
| `env.<name>` | Merged one variable at a time: newer-wins, **prompt**. |
| `env.CCSTATUSLINE_WIDTH` | **Per-file** (WSL auto-detects width; Windows needs a fixed value). |
| Per-file keys (list below) | **Per-file** — each file keeps its own value; never copied across. |
| `spinnerVerbs.verbs` | Union of both sides' arrays (deduped, sorted ordinally). |
| `spinnerVerbs.*` (other sub-keys) | Newer-wins, **prompt**. |
| `showMessageTimestamps` | Newer-wins, no prompt. |
| `autoDreamEnabled`, `effortLevel`, `tui`, `skipDangerousModePermissionPrompt`, `theme`, `verbose`, `remoteControlAtStartup`, `agentPushNotifEnabled`, `model` | Newer-wins, **prompt**. |
| Anything else at top level | Newer-wins, **prompt**. The whole value is one unit, so for an object such as `enabledPlugins` one side's object wins entire. |

**Per-file keys.** These carry a shell command, an absolute path or an OS-only feature, so a value from one OS is wrong on the other (checked against the [settings reference](https://code.claude.com/docs/en/settings-reference) on 2026-09-22):
`hooks`, `statusLine`, `subagentStatusLine`, `fileSuggestion`, `apiKeyHelper`, `awsAuthRefresh`, `awsCredentialExport`, `gcpAuthRefresh`, `otelHeadersHelper`, `processWrapper`, `autoMemoryDirectory`, `claudeMdExcludes`, `sandbox`, `defaultShell`, plus `permissions.additionalDirectories` and `env.CCSTATUSLINE_WIDTH`.
`syncClaudeAiSkills` and `syncClaudeAiPlugins` are per-file too, as a machine-local choice: `false` in user settings moves that home's synced skills or plugins into `.trash/`, so a copied `false` would trash the other home's content.
The list is `$perFileKeys` in the script; add a key there when the reference gains another command- or path-valued setting.

"Newer" = whichever settings.json has the more recent `LastWriteTimeUtc`, compared once, at the start of the run, for the **whole file**. Claude Code rewrites the file itself (`/config`, `/plugin`, `/tui`), so the newer side for `theme` may just be the side where an unrelated key changed last.

A prompt appears only when **both** files have the key with different values. It offers `[w]indows` / `[l]inux` (WSL) / `[n]ewer` / `[s]kip`. Skip leaves each file with its own current value for that key.

### Known limitations

- **Deletions do not propagate.** There is no record of what was deleted, so a key you removed from one file comes back from the other file on the next run. Remove it from both files (or from the one you then sync from) before running.
- **A prompt key on one side only is copied without prompting.** Only the per-file keys above are exempt. Review a `-DryRun` first to see which keys each file would gain.
- Values stored under a key the script does not list are merged as a unit, not per entry (see the last table row).

## Invocation

The logic lives in `scripts/Sync-ClaudeSettings.ps1`. Run it from Windows PowerShell 7+:

```powershell
# Interactive (prompts for conflicts on listed + unlisted keys)
pwsh -File .\scripts\Sync-ClaudeSettings.ps1

# Non-interactive — apply newer-wins for every PROMPT key
pwsh -File .\scripts\Sync-ClaudeSettings.ps1 -AssumeYes

# Dry run — list what would change, no writes
pwsh -File .\scripts\Sync-ClaudeSettings.ps1 -DryRun

# Point at a specific WSL distro, override paths
pwsh -File .\scripts\Sync-ClaudeSettings.ps1 -WslDistro Ubuntu-22.04
pwsh -File .\scripts\Sync-ClaudeSettings.ps1 `
    -WindowsSettingsPath "$env:USERPROFILE\.claude\settings.json" `
    -WslSettingsPath "\\wsl.localhost\Ubuntu\home\<wsl-user>\.claude\settings.json"
```

In the last example, replace `<wsl-user>` with your **WSL** username, which can differ from the Windows one (`$env:USER` is empty in Windows pwsh). `wsl.exe -- whoami` prints it.

### Parameters

| Parameter | Env var | Default |
| --- | --- | --- |
| `-WindowsSettingsPath` | `CLAUDE_SETTINGS_WINDOWS` | `$env:USERPROFILE\.claude\settings.json` |
| `-WslSettingsPath`     | `CLAUDE_SETTINGS_WSL`     | Auto-detected via `wsl.exe -- bash -lc 'wslpath -w "$HOME/.claude/settings.json"'` |
| `-WslDistro`           | `CLAUDE_SETTINGS_WSL_DISTRO` | empty (uses WSL's default distro) |
| `-AssumeYes`           | — | off (interactive) |
| `-DryRun`              | — | off. Prints key names with a `same` / `changed` / `added` / `removed` marker per file, never values: `env` values can be credentials. |

No username is hardcoded anywhere in the script; paths come from `$env:USERPROFILE`, `$env:CLAUDE_SETTINGS_*`, or `wsl.exe` auto-detection. Auto-detection runs `bash -lc`, so anything your login dotfiles print on stdout ends up in the detected path; pass `-WslSettingsPath` if that happens.

## Requirements

- Windows host (the script uses `\\wsl.localhost\...` UNC paths via the WSL adapter, and invokes `wsl.exe`).
- **PowerShell 7+** — required for `ConvertFrom-Json -AsHashtable`.
- WSL installed with at least one distro, if `-WslSettingsPath` isn't supplied.
- Both `settings.json` files already exist. The script does not create missing files.

## Tests

`tests/test_sync_claude_settings.py` drives the script through `pwsh` against two fixture files in a temp directory (`python3 -m pytest plugins/adam-local/skills/sync-cc-settings-between-wsl-and-windows/tests/ -q`). The module skips when `pwsh` is not on `PATH`. `SYNC_CC_PWSH` names another pwsh (a Windows `pwsh.exe` from inside WSL works), and `SYNC_CC_SETTINGS_SCRIPT` runs the tests against another copy of the script.

## Operational notes

- The script rewrites the JSON via PowerShell's serializer; exotic formatting (custom indent, trailing commas) is **not** preserved. Newlines, BOM, and trailing newline **are** preserved.
- Comments in settings.json are not supported (Claude Code uses plain JSON, not JSONC).
- Backups live next to the originals. Clean them up periodically if you run this often.
- If you need to roll back: the backup filenames are `YYYYMMDD-HHMMSS-ET-settings.json.bak`; overwrite the live file with the backup.

## Troubleshooting

- **"Could not auto-detect WSL settings.json path"** — pass `-WslSettingsPath` explicitly, or set `$env:CLAUDE_SETTINGS_WSL`, or install WSL.
- **"This script requires PowerShell 7+"** — install PowerShell 7 (`winget install Microsoft.PowerShell`), then invoke with `pwsh` rather than `powershell`.
- **UNC access denied to `\\wsl.localhost\...`** — the WSL distro has to be running, or at least startable, for the UNC adapter to serve files. `wsl.exe -l -v` should show the distro.
- **Invoked from a CLI running inside WSL** — pass `-WindowsSettingsPath` and `-WslSettingsPath` explicitly to skip the auto-detect, which shells out to `wsl.exe` and is re-entrant (and unreliable) when the caller is itself already inside WSL. Also pass `-AssumeYes`: the conflict prompt's `while ($true)` loop around `Read-Host` has no EOF branch, so it can loop with no output when stdin is redirected or closed. This is not the same as always hanging — under `pwsh -NonInteractive`, `Read-Host` instead raises a terminating error (the script sets `$ErrorActionPreference = 'Stop'`) and the run dies rather than looping; the silent loop is specifically a redirected/closed-stdin failure mode. Do a `-DryRun -AssumeYes` pass first and review which keys each file would gain or change before running for real.
