<#
.SYNOPSIS
    Sync Claude Code settings.json between Windows and WSL.

.DESCRIPTION
    Backs up both settings files with a timestamp prefix (Eastern time) into
    their own directories, then merges per-key according to these rules:

        permissions.allow                  : union
        permissions.ask                    : union
        permissions.deny                   : union
        permissions.additionalDirectories  : per-file (each file keeps its own; paths are OS-bound)
        permissions.defaultMode            : more-recently-modified / only-existing wins, PROMPT
        [per-file keys, see $perFileKeys]  : per-file (each file keeps its own; never copied across)
                                             hooks, statusLine, subagentStatusLine, fileSuggestion,
                                             apiKeyHelper, awsAuthRefresh, awsCredentialExport,
                                             gcpAuthRefresh, otelHeadersHelper, processWrapper,
                                             autoMemoryDirectory, claudeMdExcludes, sandbox,
                                             defaultShell, syncClaudeAiSkills, syncClaudeAiPlugins
        autoDreamEnabled                   : more-recently-modified / only-existing wins, PROMPT
        showMessageTimestamps              : more-recently-modified / only-existing wins (no prompt)
        spinnerVerbs.verbs                 : union
        env.<key>                          : per-key merge (more-recently-modified / only-existing wins, PROMPT)
        env.CCSTATUSLINE_WIDTH             : per-file (each file keeps its own; WSL auto-detects, Windows needs a fixed value)
        effortLevel                        : more-recently-modified / only-existing wins, PROMPT
        tui                                : more-recently-modified / only-existing wins, PROMPT
        skipDangerousModePermissionPrompt  : more-recently-modified / only-existing wins, PROMPT
        theme                              : more-recently-modified / only-existing wins, PROMPT
        verbose                            : more-recently-modified / only-existing wins, PROMPT
        remoteControlAtStartup             : more-recently-modified / only-existing wins, PROMPT
        agentPushNotifEnabled              : more-recently-modified / only-existing wins, PROMPT
        model                              : more-recently-modified / only-existing wins, PROMPT
        [any other key]                    : PROMPT

    A PROMPT key present on only one side is copied to the other without a
    prompt; only a key present on both sides with different values prompts.
    [s]kip at a prompt leaves each file with its own current value.

    Preserves each file's existing newline style (CRLF or LF, whichever it
    already uses), UTF-8 BOM presence and trailing newline.

.PARAMETER WindowsSettingsPath
    Path to the Windows settings.json.
    Default: "$env:USERPROFILE\.claude\settings.json"
    Can also be set via env var CLAUDE_SETTINGS_WINDOWS.

.PARAMETER WslSettingsPath
    Path to the WSL settings.json (Windows-accessible UNC form).
    Default: auto-detected via `wsl.exe` (uses the default distro's $HOME).
    Can also be set via env var CLAUDE_SETTINGS_WSL.

.PARAMETER WslDistro
    Optional WSL distro name to target when auto-detecting the WSL path.
    Default: empty (uses WSL's default distro).
    Can also be set via env var CLAUDE_SETTINGS_WSL_DISTRO.

.PARAMETER AssumeYes
    Non-interactive mode. For PROMPT keys, auto-apply the more-recently-modified /
    only-existing rule without asking.

.PARAMETER DryRun
    Compute the merge, show the plan, but do not write files. Backups are also skipped.
    The plan lists key names and whether each file's value would change; it never
    prints settings values (env values can be credentials).

.EXAMPLE
    .\Sync-ClaudeSettings.ps1

.EXAMPLE
    .\Sync-ClaudeSettings.ps1 -AssumeYes

.EXAMPLE
    .\Sync-ClaudeSettings.ps1 -WslDistro Ubuntu-22.04
#>

[CmdletBinding()]
param(
    [string]$WindowsSettingsPath,
    [string]$WslSettingsPath,
    [string]$WslDistro,
    [switch]$AssumeYes,
    [switch]$DryRun
)

# Resolve parameter defaults from env vars, then conventional locations.
if (-not $WindowsSettingsPath) {
    $WindowsSettingsPath = if ($env:CLAUDE_SETTINGS_WINDOWS) {
        $env:CLAUDE_SETTINGS_WINDOWS
    } else {
        Join-Path $env:USERPROFILE '.claude\settings.json'
    }
}
if (-not $WslDistro -and $env:CLAUDE_SETTINGS_WSL_DISTRO) {
    $WslDistro = $env:CLAUDE_SETTINGS_WSL_DISTRO
}
if (-not $WslSettingsPath -and $env:CLAUDE_SETTINGS_WSL) {
    $WslSettingsPath = $env:CLAUDE_SETTINGS_WSL
}

function Resolve-WslSettingsPath {
    param([string]$Distro)
    # Invoke via `bash -lc` so $HOME expands inside Linux. Use wslpath -w to
    # return a Windows-accessible path (UNC or drive-letter form).
    # Avoid the automatic variable `$args`; use `$wslArgs` instead.
    $cmd = 'wslpath -w "$HOME/.claude/settings.json"'
    $wslArgs = @()
    if ($Distro) { $wslArgs += @('-d', $Distro) }
    $wslArgs += @('--', 'bash', '-lc', $cmd)
    try {
        $out = & wsl.exe @wslArgs 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) {
            $p = ($out | Out-String).Trim()
            if ($p) { return $p }
        }
    } catch { }
    return $null
}

if (-not $WslSettingsPath) {
    $WslSettingsPath = Resolve-WslSettingsPath -Distro $WslDistro
    if (-not $WslSettingsPath) {
        throw "Could not auto-detect WSL settings.json path. Pass -WslSettingsPath explicitly, set `$env:CLAUDE_SETTINGS_WSL, or specify -WslDistro. (Is WSL installed and a distro available?)"
    }
}

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "This script requires PowerShell 7+ (needs ConvertFrom-Json -AsHashtable). Installed: $($PSVersionTable.PSVersion)"
}

# ---------- Helpers ----------

function Get-EasternTimeStamp {
    # Prefer IANA name (works on PS7 cross-platform); fall back to Windows ID.
    $tz = $null
    foreach ($id in @('America/New_York', 'Eastern Standard Time')) {
        try { $tz = [System.TimeZoneInfo]::FindSystemTimeZoneById($id); break } catch { }
    }
    if (-not $tz) { throw "Could not resolve Eastern time zone." }
    $now = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tz)
    return $now.ToString('yyyyMMdd-HHmmss')
}

function Get-FileNewline {
    param([Parameter(Mandatory)][string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    for ($i = 0; $i -lt $bytes.Length - 1; $i++) {
        if ($bytes[$i] -eq 13 -and $bytes[$i + 1] -eq 10) { return "`r`n" }
        if ($bytes[$i] -eq 10) { return "`n" }
        if ($bytes[$i] -eq 13) { return "`r" }
    }
    # No newline found — fall back based on path heuristic.
    if ($Path -match '^(\\\\wsl|/)') { return "`n" }
    return "`r`n"
}

function Get-FileEncoding {
    param([Parameter(Mandatory)][string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        return [System.Text.UTF8Encoding]::new($true)
    }
    return [System.Text.UTF8Encoding]::new($false)
}

function Read-JsonOrdered {
    param([Parameter(Mandatory)][string]$Path)
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) { return [ordered]@{} }
    $obj = $raw | ConvertFrom-Json -AsHashtable -Depth 100
    if ($null -eq $obj) { return [ordered]@{} }
    # Convert to [ordered] to stabilize key order.
    $ordered = [ordered]@{}
    foreach ($k in $obj.Keys) { $ordered[$k] = $obj[$k] }
    return $ordered
}

function Write-JsonPreservingFormat {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)]$Object,
        [Parameter(Mandatory)][string]$Newline,
        [Parameter(Mandatory)][System.Text.Encoding]$Encoding,
        [switch]$EndsWithNewline
    )
    $json = ConvertTo-Json -InputObject $Object -Depth 100
    $normalized = $json -replace "`r`n", "`n" -replace "`r", "`n"
    $final = if ($Newline -eq "`n") { $normalized } else { $normalized -replace "`n", $Newline }
    if ($EndsWithNewline -and -not $final.EndsWith($Newline)) { $final += $Newline }
    [System.IO.File]::WriteAllText($Path, $final, $Encoding)
}

function Test-EndsWithNewline {
    param([Parameter(Mandatory)][string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -eq 0) { return $false }
    return $bytes[-1] -eq 10 -or $bytes[-1] -eq 13
}

function Backup-SettingsFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Stamp
    )
    $dir = Split-Path -Parent $Path
    $name = Split-Path -Leaf $Path
    $backupPath = Join-Path $dir "$Stamp-ET-$name.bak"
    Copy-Item -LiteralPath $Path -Destination $backupPath -Force
    return $backupPath
}

function ConvertTo-ComparableJson {
    param($Value)
    if ($null -eq $Value) { return 'null' }
    # -InputObject, not the pipeline: piping unrolls an array, so [] would
    # compare as nothing and ["x"] as "x".
    return (ConvertTo-Json -InputObject $Value -Depth 100 -Compress)
}

function Get-UnionArray {
    param($A, $B)
    $seen = @{}
    $out = [System.Collections.Generic.List[object]]::new()
    foreach ($src in @($A, $B)) {
        if ($null -eq $src) { continue }
        # Ensure we iterate even if a single scalar was passed in.
        $items = if ($src -is [System.Collections.IEnumerable] -and $src -isnot [string]) { $src } else { @($src) }
        foreach ($item in $items) {
            if ($null -eq $item) { continue }
            $key = ConvertTo-ComparableJson $item
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                $out.Add($item) | Out-Null
            }
        }
    }
    $arr = $out.ToArray()
    # If every item is a string, sort ordinally so the two files get the same
    # array order regardless of which side contributed which items.
    if ($arr.Count -gt 1) {
        $allStrings = $true
        foreach ($x in $arr) { if ($x -isnot [string]) { $allStrings = $false; break } }
        if ($allStrings) {
            $typed = [string[]]$arr
            [Array]::Sort($typed, [System.StringComparer]::Ordinal)
            return ,@($typed)
        }
    }
    return ,@($arr)
}

# Canonical key orders. Keys listed here are emitted first, in this order;
# any unlisted keys fall through and are emitted alphabetically after.
$script:CanonicalTopOrder = @(
    'permissions',
    'env',
    'model',
    'theme',
    'tui',
    'effortLevel',
    'verbose',
    'autoDreamEnabled',
    'showMessageTimestamps',
    'skipDangerousModePermissionPrompt',
    'remoteControlAtStartup',
    'agentPushNotifEnabled',
    'statusLine',
    'defaultShell',
    'spinnerVerbs',
    'extraKnownMarketplaces'
)
$script:CanonicalPermissionsOrder = @('allow','ask','deny','defaultMode')
$script:CanonicalSpinnerVerbsOrder = @('mode','verbs')

function Sort-DictByCanonicalOrder {
    param(
        [Parameter(Mandatory)]$Dict,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$CanonicalOrder
    )
    $out = [ordered]@{}
    foreach ($k in $CanonicalOrder) {
        if ($Dict.Contains($k)) { $out[$k] = $Dict[$k] }
    }
    $remaining = @($Dict.Keys | Where-Object { $CanonicalOrder -notcontains $_ } | Sort-Object -Culture ([cultureinfo]::InvariantCulture))
    foreach ($k in $remaining) { $out[$k] = $Dict[$k] }
    return $out
}

function Resolve-ScalarConflict {
    param(
        [Parameter(Mandatory)][string]$KeyPath,
        $WindowsValue,
        [bool]$WindowsHasKey,
        $WslValue,
        [bool]$WslHasKey,
        [Parameter(Mandatory)][ValidateSet('auto','prompt')][string]$Policy,
        [Parameter(Mandatory)][ValidateSet('windows','wsl')][string]$NewerSide,
        [bool]$NonInteractive
    )

    if (-not $WindowsHasKey -and -not $WslHasKey) {
        return [pscustomobject]@{ Present = $false; Value = $null; Source = 'absent' }
    }
    if ($WindowsHasKey -and -not $WslHasKey) {
        return [pscustomobject]@{ Present = $true; Value = $WindowsValue; Source = 'windows (only existing)' }
    }
    if ($WslHasKey -and -not $WindowsHasKey) {
        return [pscustomobject]@{ Present = $true; Value = $WslValue; Source = 'wsl (only existing)' }
    }

    # Both have it. If equal, no conflict.
    if ((ConvertTo-ComparableJson $WindowsValue) -eq (ConvertTo-ComparableJson $WslValue)) {
        return [pscustomobject]@{ Present = $true; Value = $WindowsValue; Source = 'both equal' }
    }

    # Plain assignment, never `$x = if (...) { $v }`: an if-expression sends $v
    # through the pipeline, which unrolls arrays ([] -> $null, ["x"] -> "x").
    $newerValue = $WslValue
    if ($NewerSide -eq 'windows') { $newerValue = $WindowsValue }

    if ($Policy -eq 'auto' -or $NonInteractive) {
        return [pscustomobject]@{ Present = $true; Value = $newerValue; Source = "$NewerSide (newer)" }
    }

    Write-Host ""
    Write-Host "Conflict on: $KeyPath" -ForegroundColor Yellow
    Write-Host ("  windows : " + (ConvertTo-ComparableJson $WindowsValue))
    Write-Host ("  wsl     : " + (ConvertTo-ComparableJson $WslValue))
    Write-Host ("  newer   : $NewerSide")
    while ($true) {
        $choice = Read-Host "  [w]indows / [l]inux / [n]ewer / [s]kip (keep each file's current value)"
        switch -Regex ($choice) {
            '^[wW]' { return [pscustomobject]@{ Present = $true; Value = $WindowsValue; Source = 'windows (chosen)' } }
            '^[lL]' { return [pscustomobject]@{ Present = $true; Value = $WslValue;     Source = 'wsl (chosen)' } }
            '^[nN]' { return [pscustomobject]@{ Present = $true; Value = $newerValue;   Source = "$NewerSide (chosen: newer)" } }
            '^[sS]' { return [pscustomobject]@{ Present = $true; Value = $null;         Source = 'skip (each file keeps its own)'; Skip = $true } }
        }
    }
}

# Apply a Resolve-ScalarConflict result to the two per-side dictionaries.
# A skip writes each side's OWN value back to that side (never deletes);
# anything else writes the resolved value to both.
function Set-ResolvedValue {
    param(
        [Parameter(Mandatory)][System.Collections.IDictionary]$WindowsDict,
        [Parameter(Mandatory)][System.Collections.IDictionary]$WslDict,
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)]$Resolution,
        $WindowsValue, [bool]$WindowsHasKey,
        $WslValue,     [bool]$WslHasKey
    )
    if (-not $Resolution.Present) { return }
    if ($Resolution.PSObject.Properties.Name -contains 'Skip' -and $Resolution.Skip) {
        if ($WindowsHasKey) { $WindowsDict[$Key] = $WindowsValue }
        if ($WslHasKey)     { $WslDict[$Key]     = $WslValue }
        return
    }
    $WindowsDict[$Key] = $Resolution.Value
    $WslDict[$Key]     = $Resolution.Value
}

# ---------- Main ----------

foreach ($p in @($WindowsSettingsPath, $WslSettingsPath)) {
    if (-not (Test-Path -LiteralPath $p)) {
        throw "Settings file not found: $p"
    }
}

$stamp = Get-EasternTimeStamp
Write-Host "=== Claude Code settings sync ===" -ForegroundColor Cyan
Write-Host "Timestamp (Eastern): $stamp"
Write-Host "Windows file:        $WindowsSettingsPath"
Write-Host "WSL file:            $WslSettingsPath"
if ($DryRun)    { Write-Host "Mode:                DRY RUN (no files will be written)" -ForegroundColor Magenta }
if ($AssumeYes) { Write-Host "Mode:                NON-INTERACTIVE (-AssumeYes)" -ForegroundColor Magenta }

# Detect formatting
$winNewline = Get-FileNewline -Path $WindowsSettingsPath
$wslNewline = Get-FileNewline -Path $WslSettingsPath
$winEncoding = Get-FileEncoding -Path $WindowsSettingsPath
$wslEncoding = Get-FileEncoding -Path $WslSettingsPath
$winEndsNL = Test-EndsWithNewline -Path $WindowsSettingsPath
$wslEndsNL = Test-EndsWithNewline -Path $WslSettingsPath

function Format-Newline { param($nl) switch ($nl) { "`r`n" {'CRLF'} "`n" {'LF'} "`r" {'CR'} default {'?'} } }
$winBom = ($winEncoding.GetPreamble().Length -gt 0)
$wslBom = ($wslEncoding.GetPreamble().Length -gt 0)
Write-Host ("Windows: newline={0}, bom={1}, trailing-nl={2}" -f (Format-Newline $winNewline), $winBom, $winEndsNL)
Write-Host ("WSL:     newline={0}, bom={1}, trailing-nl={2}" -f (Format-Newline $wslNewline), $wslBom, $wslEndsNL)

# Modification times
$winMTime = (Get-Item -LiteralPath $WindowsSettingsPath).LastWriteTimeUtc
$wslMTime = (Get-Item -LiteralPath $WslSettingsPath).LastWriteTimeUtc
$newerSide = if ($winMTime -ge $wslMTime) { 'windows' } else { 'wsl' }
Write-Host "Windows mtime (UTC): $winMTime"
Write-Host "WSL mtime (UTC):     $wslMTime"
Write-Host "Newer side:          $newerSide"

# Backups
if (-not $DryRun) {
    $winBackup = Backup-SettingsFile -Path $WindowsSettingsPath -Stamp $stamp
    $wslBackup = Backup-SettingsFile -Path $WslSettingsPath    -Stamp $stamp
    Write-Host "Backup (Windows):    $winBackup"
    Write-Host "Backup (WSL):        $wslBackup"
}

# Load JSON
$win = Read-JsonOrdered -Path $WindowsSettingsPath
$wsl = Read-JsonOrdered -Path $WslSettingsPath

# Rule tables
# Top-level keys kept per-file: each file keeps its own value and the key is
# never copied to the other file, even when only one side has it. Each one
# carries a shell command, an absolute path or an OS-only feature (checked
# against https://code.claude.com/docs/en/settings-reference on 2026-09-22),
# or is a machine-local choice: syncClaudeAiSkills / syncClaudeAiPlugins set
# to false in user settings move that home's synced skills / plugins into
# .trash/, so a copied `false` would trash the other home's content.
$perFileKeys = @(
    'hooks', 'statusLine', 'subagentStatusLine', 'fileSuggestion',
    'apiKeyHelper', 'awsAuthRefresh', 'awsCredentialExport', 'gcpAuthRefresh',
    'otelHeadersHelper', 'processWrapper', 'autoMemoryDirectory',
    'claudeMdExcludes', 'sandbox', 'defaultShell',
    'syncClaudeAiSkills', 'syncClaudeAiPlugins'
)
# permissions sub-keys: unioned, and kept per-file (directory paths are OS-bound).
$unionPermissionKeys   = @('allow','ask','deny')
$perFilePermissionKeys = @('additionalDirectories')
# env sub-keys kept per-file (each side keeps its own; never copied across).
# CCSTATUSLINE_WIDTH: WSL auto-detects terminal width and must NOT be pinned,
# while Windows (win32) cannot auto-detect and needs a fixed value.
$perFileEnvKeys      = @('CCSTATUSLINE_WIDTH')
$autoScalarKeys      = @('showMessageTimestamps')           # merge w/o prompt
$promptScalarKeys    = @(                                   # merge w/ prompt
    'autoDreamEnabled','effortLevel','tui','skipDangerousModePermissionPrompt',
    'theme','verbose','remoteControlAtStartup','agentPushNotifEnabled','model'
)

# Collect all top-level keys
$allKeys = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach ($k in $win.Keys) { [void]$allKeys.Add($k) }
foreach ($k in $wsl.Keys) { [void]$allKeys.Add($k) }

# The two outputs are built side by side, so a per-file key or a skipped
# conflict can hold a different value on each side.
$winMerged = [ordered]@{}
$wslMerged = [ordered]@{}
$log       = [System.Collections.Generic.List[string]]::new()

# Merge the sub-keys of an object-valued key (permissions, env, spinnerVerbs)
# into one dictionary per side.
function Merge-NestedKey {
    param(
        [Parameter(Mandatory)][string]$Prefix,
        $WindowsObject,
        $WslObject,
        [string[]]$UnionKeys = @(),
        [string[]]$PerFileSubKeys = @()
    )
    $wo = @{}; if ($WindowsObject -is [System.Collections.IDictionary]) { $wo = $WindowsObject }
    $lo = @{}; if ($WslObject     -is [System.Collections.IDictionary]) { $lo = $WslObject }
    $subKeys = [System.Collections.Generic.List[string]]::new()
    foreach ($k in $wo.Keys) { $subKeys.Add($k) }
    foreach ($k in $lo.Keys) { if (-not $subKeys.Contains($k)) { $subKeys.Add($k) } }

    $outWin = [ordered]@{}
    $outWsl = [ordered]@{}
    foreach ($sk in $subKeys) {
        $wsHas = $wo.Contains($sk)
        $lsHas = $lo.Contains($sk)
        $wsVal = $null; if ($wsHas) { $wsVal = $wo[$sk] }
        $lsVal = $null; if ($lsHas) { $lsVal = $lo[$sk] }

        if ($PerFileSubKeys -contains $sk) {
            if ($wsHas) { $outWin[$sk] = $wsVal }
            if ($lsHas) { $outWsl[$sk] = $lsVal }
            $log.Add("[$Prefix.$sk] per-file (each file keeps its own value)") | Out-Null
            continue
        }
        if ($UnionKeys -contains $sk) {
            $u = Get-UnionArray -A $wsVal -B $lsVal
            $outWin[$sk] = $u
            $outWsl[$sk] = $u
            $log.Add("[$Prefix.$sk] union (count=$($u.Count))") | Out-Null
            continue
        }
        $r = Resolve-ScalarConflict -KeyPath "$Prefix.$sk" `
                -WindowsValue $wsVal -WindowsHasKey $wsHas `
                -WslValue $lsVal -WslHasKey $lsHas `
                -Policy 'prompt' -NewerSide $newerSide -NonInteractive:$AssumeYes
        Set-ResolvedValue -WindowsDict $outWin -WslDict $outWsl -Key $sk -Resolution $r `
            -WindowsValue $wsVal -WindowsHasKey $wsHas -WslValue $lsVal -WslHasKey $lsHas
        $log.Add("[$Prefix.$sk] -> $($r.Source)") | Out-Null
    }
    return [pscustomobject]@{ Windows = $outWin; Wsl = $outWsl }
}

foreach ($key in $allKeys) {
    $wHas = $win.Contains($key)
    $lHas = $wsl.Contains($key)
    # Plain assignment, never `$x = if (...) { $h[$k] }`: an if-expression
    # unrolls arrays ([] -> $null, ["x"] -> "x").
    $wVal = $null; if ($wHas) { $wVal = $win[$key] }
    $lVal = $null; if ($lHas) { $lVal = $wsl[$key] }

    # --- per-file: preserve each file's own value, never copy across ---
    if ($perFileKeys -contains $key) {
        if ($wHas) { $winMerged[$key] = $wVal }
        if ($lHas) { $wslMerged[$key] = $lVal }
        $log.Add("[$key] per-file (each file keeps its own value)") | Out-Null
        continue
    }

    # --- object-valued keys merged per sub-key ---
    if ($key -eq 'permissions' -or $key -eq 'env' -or $key -eq 'spinnerVerbs') {
        if ($key -eq 'permissions') {
            $n = Merge-NestedKey -Prefix $key -WindowsObject $wVal -WslObject $lVal `
                    -UnionKeys $unionPermissionKeys -PerFileSubKeys $perFilePermissionKeys
            $order = $script:CanonicalPermissionsOrder
        } elseif ($key -eq 'env') {
            $n = Merge-NestedKey -Prefix $key -WindowsObject $wVal -WslObject $lVal `
                    -PerFileSubKeys $perFileEnvKeys
            $order = @()
        } else {
            $n = Merge-NestedKey -Prefix $key -WindowsObject $wVal -WslObject $lVal `
                    -UnionKeys @('verbs')
            $order = $script:CanonicalSpinnerVerbsOrder
        }
        if ($wHas -or $n.Windows.Count -gt 0) {
            $winMerged[$key] = Sort-DictByCanonicalOrder -Dict $n.Windows -CanonicalOrder $order
        }
        if ($lHas -or $n.Wsl.Count -gt 0) {
            $wslMerged[$key] = Sort-DictByCanonicalOrder -Dict $n.Wsl -CanonicalOrder $order
        }
        continue
    }

    # --- scalar keys: auto (no prompt), known prompt keys, unlisted (prompt) ---
    $policy = 'prompt'; $label = 'unlisted'
    if ($autoScalarKeys -contains $key)       { $policy = 'auto'; $label = 'auto' }
    elseif ($promptScalarKeys -contains $key) { $label = 'prompt' }
    $r = Resolve-ScalarConflict -KeyPath $key -WindowsValue $wVal -WindowsHasKey $wHas `
            -WslValue $lVal -WslHasKey $lHas -Policy $policy -NewerSide $newerSide -NonInteractive:$AssumeYes
    Set-ResolvedValue -WindowsDict $winMerged -WslDict $wslMerged -Key $key -Resolution $r `
        -WindowsValue $wVal -WindowsHasKey $wHas -WslValue $lVal -WslHasKey $lHas
    $log.Add("[$key] $label -> $($r.Source)") | Out-Null
}

$winOut = Sort-DictByCanonicalOrder -Dict $winMerged -CanonicalOrder $script:CanonicalTopOrder
$wslOut = Sort-DictByCanonicalOrder -Dict $wslMerged -CanonicalOrder $script:CanonicalTopOrder

Write-Host ""
Write-Host "Merge decisions:" -ForegroundColor Cyan
foreach ($line in $log) { Write-Host "  $line" }

# Key names and a same/changed/added/removed marker per file, one level deep.
# Never values: env values can be credentials, and a dry run is what an agent
# is told to run first, so its output lands in a transcript.
function Write-PlanForSide {
    param([Parameter(Mandatory)]$Before, [Parameter(Mandatory)]$After, [string]$Indent = '  ')
    $names = [System.Collections.Generic.SortedSet[string]]::new([StringComparer]::Ordinal)
    foreach ($k in $Before.Keys) { [void]$names.Add($k) }
    foreach ($k in $After.Keys)  { [void]$names.Add($k) }
    foreach ($k in $names) {
        $bHas = $Before.Contains($k)
        $aHas = $After.Contains($k)
        $b = $null; if ($bHas) { $b = $Before[$k] }
        $a = $null; if ($aHas) { $a = $After[$k] }
        $mark = 'same'
        if (-not $bHas) { $mark = 'added' }
        elseif (-not $aHas) { $mark = 'removed' }
        elseif ((ConvertTo-ComparableJson $b) -ne (ConvertTo-ComparableJson $a)) { $mark = 'changed' }
        Write-Host "$Indent$k : $mark"
        if ($Indent -eq '  ' -and ($b -is [System.Collections.IDictionary] -or $a -is [System.Collections.IDictionary])) {
            $bd = @{}; if ($b -is [System.Collections.IDictionary]) { $bd = $b }
            $ad = @{}; if ($a -is [System.Collections.IDictionary]) { $ad = $a }
            Write-PlanForSide -Before $bd -After $ad -Indent '      '
        }
    }
}

if ($DryRun) {
    Write-Host ""
    Write-Host "DRY RUN — no files written. Values are not shown." -ForegroundColor Magenta
    Write-Host "Windows would become:"; Write-PlanForSide -Before $win -After $winOut
    Write-Host "WSL would become:";     Write-PlanForSide -Before $wsl -After $wslOut
    return
}

# Write outputs, preserving each side's native format.
Write-JsonPreservingFormat -Path $WindowsSettingsPath -Object $winOut `
    -Newline $winNewline -Encoding $winEncoding -EndsWithNewline:$winEndsNL
Write-JsonPreservingFormat -Path $WslSettingsPath -Object $wslOut `
    -Newline $wslNewline -Encoding $wslEncoding -EndsWithNewline:$wslEndsNL

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "Backups:"
Write-Host "  $winBackup"
Write-Host "  $wslBackup"
