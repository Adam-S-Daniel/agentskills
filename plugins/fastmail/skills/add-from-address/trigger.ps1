#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Dispatch the add-from-address workflow in Adam-S-Daniel/fastmail-actions, wait
  for the run, and print its report.

.DESCRIPTION
  Thin wrapper over `gh`. The workflow does the JMAP work using the repo's
  FASTMAIL_API_TOKEN secret; this script never handles the token. Applies by
  default (explicit addresses = explicit intent); pass -WhatIf to preview.

.EXAMPLE
  ./trigger.ps1 -Address new-alias@example.com
  ./trigger.ps1 -Address a@example.com,b@example.com -WhatIf
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [string[]]$Address,

    [string]$Name,

    [switch]$WhatIf,

    [string]$Repo = 'Adam-S-Daniel/fastmail-actions'
)

$ErrorActionPreference = 'Stop'
$workflow = 'add-from-address.yml'

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) not found. Install it and run 'gh auth login' (needs the 'workflow' scope)."
}

$addresses = @($Address | ForEach-Object { $_ -split '[,\s]+' } | Where-Object { $_ }) -join ' '
$whatifValue = if ($WhatIf) { 'true' } else { 'false' }

$dispatchArgs = @('workflow', 'run', $workflow, '--repo', $Repo,
    '-f', "addresses=$addresses", '-f', "whatif=$whatifValue")
if ($Name) { $dispatchArgs += @('-f', "name=$Name") }

Write-Host "Dispatching $workflow (whatif=$whatifValue) for: $addresses" -ForegroundColor Cyan
$since = (Get-Date).ToUniversalTime().AddSeconds(-10)
& gh @dispatchArgs
if ($LASTEXITCODE -ne 0) { throw "gh workflow run failed (exit $LASTEXITCODE)." }

# Find the run we just dispatched (gh workflow run does not return its id).
$runId = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    $json = & gh run list --workflow=$workflow --repo $Repo --event workflow_dispatch --limit 5 --json databaseId,createdAt 2>$null
    if ($json) {
        $runs = $json | ConvertFrom-Json
        $cand = $runs | Where-Object { ([datetime]$_.createdAt).ToUniversalTime() -ge $since } |
            Sort-Object { [datetime]$_.createdAt } -Descending | Select-Object -First 1
        if ($cand) { $runId = $cand.databaseId; break }
    }
}
if (-not $runId) { throw "Could not locate the dispatched run. Check: gh run list --workflow=$workflow --repo $Repo" }

Write-Host "Watching run $runId ..." -ForegroundColor Cyan
& gh run watch $runId --repo $Repo --exit-status --interval 3
$runExit = $LASTEXITCODE

# For privacy, the workflow does NOT print the report (addresses) to its public
# log — it emails the report to the configured FASTMAIL_REPORT_TO address.
if ($runExit -eq 0) {
    Write-Host "`nDone. The report (pre-existing + added/would-add addresses) was emailed to your FASTMAIL_REPORT_TO address — check your inbox." -ForegroundColor Green
} else {
    Write-Host "`nThe run did not succeed. See the run for the (non-sensitive) status." -ForegroundColor Yellow
}
Write-Host "Run: https://github.com/$Repo/actions/runs/$runId"
exit $runExit
