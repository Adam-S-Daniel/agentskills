<#
.SYNOPSIS
    Sync Claude Code settings.json between Windows and WSL.

.DESCRIPTION
    Backs up both settings files with a timestamp prefix (Eastern time) into
    their own directories, then merges per-key according to these rules:

        permissions.allow                  : union
        permissions.deny                   : union
        permissions.defaultMode            : more-recently-modified / only-existing wins, PROMPT
        statusLine                         : ignore (each file keeps its own)
        autoDreamEnabled                   : more-recently-modified / only-existing wins, PROMPT
        defaultShell                       : ignore (each file keeps its own)
        showMessageTimestamps              : more-recently-modified / only-existing wins (no prompt)
        spinnerVerbs.verbs                 : union
        effortLevel                        : more-recently-modified / only-existing wins, PROMPT
        tui                                : more-recently-modified / only-existing wins, PROMPT
        skipDangerousModePermissionPrompt  : more-recently-modified / only-existing wins, PROMPT
        theme                              : more-recently-modified / only-existing wins, PROMPT
        verbose                            : more-recently-modified / only-existing wins, PROMPT
        remoteControlAtStartup             : more-recently-modified / only-existing wins, PROMPT
        agentPushNotifEnabled              : more-recently-modified / only-existing wins, PROMPT
        model                              : more-recently-modified / only-existing wins, PROMPT
        [any other key]                    : PROMPT

    Preserves each file's native newline format (CRLF for Windows, LF for WSL)
    and UTF-8 BOM presence.

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
    $json = $Object | ConvertTo-Json -Depth 100
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
    return ($Value | ConvertTo-Json -Depth 100 -Compress)
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
$script:CanonicalPermissionsOrder = @('allow','deny','defaultMode')
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

    $newerValue = if ($NewerSide -eq 'windows') { $WindowsValue } else { $WslValue }

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
            '^[sS]' { return [pscustomobject]@{ Present = $true; Value = $null;         Source = 'skip'; Skip = $true } }
        }
    }
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
$ignoreKeys          = @('statusLine','defaultShell')
$autoScalarKeys      = @('showMessageTimestamps')           # merge w/o prompt
$promptScalarKeys    = @(                                   # merge w/ prompt
    'autoDreamEnabled','effortLevel','tui','skipDangerousModePermissionPrompt',
    'theme','verbose','remoteControlAtStartup','agentPushNotifEnabled','model'
)

# Collect all top-level keys
$allKeys = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach ($k in $win.Keys) { [void]$allKeys.Add($k) }
foreach ($k in $wsl.Keys) { [void]$allKeys.Add($k) }

# Marker for "ignored — each file keeps its own value"
$perFileMarker = [pscustomobject]@{ __PerFile = $true; WindowsValue = $null; WindowsHas = $false; WslValue = $null; WslHas = $false }

$merged = [ordered]@{}
$log    = [System.Collections.Generic.List[string]]::new()

foreach ($key in $allKeys) {
    $wHas = $win.Contains($key)
    $lHas = $wsl.Contains($key)
    $wVal = if ($wHas) { $win[$key] } else { $null }
    $lVal = if ($lHas) { $wsl[$key] } else { $null }

    # --- ignore: preserve each file's own value ---
    if ($ignoreKeys -contains $key) {
        $perFile = [pscustomobject]@{
            __PerFile   = $true
            WindowsValue = $wVal; WindowsHas = $wHas
            WslValue     = $lVal; WslHas     = $lHas
        }
        $merged[$key] = $perFile
        $log.Add("[$key] ignored (each file keeps its own value)") | Out-Null
        continue
    }

    # --- permissions: allow/deny union, defaultMode prompt, other sub-keys prompt ---
    if ($key -eq 'permissions') {
        $p = [ordered]@{}
        $wp = if ($wHas -and $wVal -is [System.Collections.IDictionary]) { $wVal } else { @{} }
        $lp = if ($lHas -and $lVal -is [System.Collections.IDictionary]) { $lVal } else { @{} }
        $permKeys = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($k in $wp.Keys) { [void]$permKeys.Add($k) }
        foreach ($k in $lp.Keys) { [void]$permKeys.Add($k) }
        foreach ($pk in $permKeys) {
            if ($pk -eq 'allow' -or $pk -eq 'deny') {
                $wpv = if ($wp.Contains($pk)) { $wp[$pk] } else { @() }
                $lpv = if ($lp.Contains($pk)) { $lp[$pk] } else { @() }
                $p[$pk] = Get-UnionArray -A $wpv -B $lpv
                $log.Add("[permissions.$pk] union (count=$(@($p[$pk]).Count))") | Out-Null
            }
            else {
                # Everything else under permissions (incl. defaultMode) -> prompt
                $wpHas = $wp.Contains($pk)
                $lpHas = $lp.Contains($pk)
                $wpVal = if ($wpHas) { $wp[$pk] } else { $null }
                $lpVal = if ($lpHas) { $lp[$pk] } else { $null }
                $r = Resolve-ScalarConflict -KeyPath "permissions.$pk" `
                        -WindowsValue $wpVal -WindowsHasKey $wpHas `
                        -WslValue $lpVal -WslHasKey $lpHas `
                        -Policy 'prompt' -NewerSide $newerSide -NonInteractive:$AssumeYes
                if ($r.Present -and -not ($r.PSObject.Properties.Name -contains 'Skip' -and $r.Skip)) {
                    $p[$pk] = $r.Value
                }
                $log.Add("[permissions.$pk] -> $($r.Source)") | Out-Null
            }
        }
        $merged[$key] = Sort-DictByCanonicalOrder -Dict $p -CanonicalOrder $script:CanonicalPermissionsOrder
        continue
    }

    # --- spinnerVerbs: verbs union, other sub-keys prompt ---
    if ($key -eq 'spinnerVerbs') {
        $s = [ordered]@{}
        $ws = if ($wHas -and $wVal -is [System.Collections.IDictionary]) { $wVal } else { @{} }
        $ls = if ($lHas -and $lVal -is [System.Collections.IDictionary]) { $lVal } else { @{} }
        $svKeys = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($k in $ws.Keys) { [void]$svKeys.Add($k) }
        foreach ($k in $ls.Keys) { [void]$svKeys.Add($k) }
        foreach ($sk in $svKeys) {
            if ($sk -eq 'verbs') {
                $wsv = if ($ws.Contains($sk)) { $ws[$sk] } else { @() }
                $lsv = if ($ls.Contains($sk)) { $ls[$sk] } else { @() }
                $s[$sk] = Get-UnionArray -A $wsv -B $lsv
                $log.Add("[spinnerVerbs.$sk] union (count=$(@($s[$sk]).Count))") | Out-Null
            }
            else {
                $wsHas = $ws.Contains($sk)
                $lsHas = $ls.Contains($sk)
                $wsVal = if ($wsHas) { $ws[$sk] } else { $null }
                $lsVal = if ($lsHas) { $ls[$sk] } else { $null }
                $r = Resolve-ScalarConflict -KeyPath "spinnerVerbs.$sk" `
                        -WindowsValue $wsVal -WindowsHasKey $wsHas `
                        -WslValue $lsVal -WslHasKey $lsHas `
                        -Policy 'prompt' -NewerSide $newerSide -NonInteractive:$AssumeYes
                if ($r.Present -and -not ($r.PSObject.Properties.Name -contains 'Skip' -and $r.Skip)) {
                    $s[$sk] = $r.Value
                }
                $log.Add("[spinnerVerbs.$sk] -> $($r.Source)") | Out-Null
            }
        }
        $merged[$key] = Sort-DictByCanonicalOrder -Dict $s -CanonicalOrder $script:CanonicalSpinnerVerbsOrder
        continue
    }

    # --- auto-merge (no prompt) ---
    if ($autoScalarKeys -contains $key) {
        $r = Resolve-ScalarConflict -KeyPath $key -WindowsValue $wVal -WindowsHasKey $wHas `
                -WslValue $lVal -WslHasKey $lHas -Policy 'auto' -NewerSide $newerSide -NonInteractive:$AssumeYes
        if ($r.Present -and -not ($r.PSObject.Properties.Name -contains 'Skip' -and $r.Skip)) {
            $merged[$key] = $r.Value
        }
        $log.Add("[$key] auto -> $($r.Source)") | Out-Null
        continue
    }

    # --- known prompt keys ---
    if ($promptScalarKeys -contains $key) {
        $r = Resolve-ScalarConflict -KeyPath $key -WindowsValue $wVal -WindowsHasKey $wHas `
                -WslValue $lVal -WslHasKey $lHas -Policy 'prompt' -NewerSide $newerSide -NonInteractive:$AssumeYes
        if ($r.Present -and -not ($r.PSObject.Properties.Name -contains 'Skip' -and $r.Skip)) {
            $merged[$key] = $r.Value
        }
        $log.Add("[$key] prompt -> $($r.Source)") | Out-Null
        continue
    }

    # --- unlisted top-level keys: prompt ---
    $r = Resolve-ScalarConflict -KeyPath $key -WindowsValue $wVal -WindowsHasKey $wHas `
            -WslValue $lVal -WslHasKey $lHas -Policy 'prompt' -NewerSide $newerSide -NonInteractive:$AssumeYes
    if ($r.Present -and -not ($r.PSObject.Properties.Name -contains 'Skip' -and $r.Skip)) {
        $merged[$key] = $r.Value
    }
    $log.Add("[$key] unlisted -> $($r.Source)") | Out-Null
}

# Build per-side outputs: for ignored keys, splice in each side's own value.
function New-SideOutput {
    param([Parameter(Mandatory)][ValidateSet('windows','wsl')][string]$Side)
    $out = [ordered]@{}
    foreach ($kv in $merged.GetEnumerator()) {
        $v = $kv.Value
        if ($v -is [pscustomobject] -and ($v.PSObject.Properties.Name -contains '__PerFile') -and $v.__PerFile) {
            if ($Side -eq 'windows' -and $v.WindowsHas) { $out[$kv.Key] = $v.WindowsValue }
            elseif ($Side -eq 'wsl' -and $v.WslHas)     { $out[$kv.Key] = $v.WslValue }
            # otherwise: omit
        } else {
            $out[$kv.Key] = $v
        }
    }
    return $out
}

$winOut = Sort-DictByCanonicalOrder -Dict (New-SideOutput -Side 'windows') -CanonicalOrder $script:CanonicalTopOrder
$wslOut = Sort-DictByCanonicalOrder -Dict (New-SideOutput -Side 'wsl')     -CanonicalOrder $script:CanonicalTopOrder

Write-Host ""
Write-Host "Merge decisions:" -ForegroundColor Cyan
foreach ($line in $log) { Write-Host "  $line" }

if ($DryRun) {
    Write-Host ""
    Write-Host "DRY RUN — no files written." -ForegroundColor Magenta
    Write-Host "Windows would become:"; $winOut | ConvertTo-Json -Depth 100
    Write-Host "WSL would become:";     $wslOut | ConvertTo-Json -Depth 100
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
