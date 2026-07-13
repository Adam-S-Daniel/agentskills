#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Dispatch the add-received-from-addresses workflow in
  Adam-S-Daniel/fastmail-actions, wait for the run, and print its report.

.DESCRIPTION
  Thin wrapper over `gh`. The workflow does the discovery + JMAP work using the
  repo's FASTMAIL_API_TOKEN secret; this script never handles the token. Defaults
  to a dry run; pass -Apply to actually create the identities.

.EXAMPLE
  ./trigger.ps1            # preview
  ./trigger.ps1 -Apply     # apply
  ./trigger.ps1 -Max 2000  # sample newest 2000 messages, preview
#>
[CmdletBinding()]
param(
    [switch]$Apply,

    [string]$Name,

    [int]$Max,

    [string]$Repo = 'Adam-S-Daniel/fastmail-actions'
)

$ErrorActionPreference = 'Stop'
$workflow = 'add-received-from-addresses.yml'

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) not found. Install it and run 'gh auth login' (needs the 'workflow' scope)."
}

$whatifValue = if ($Apply) { 'false' } else { 'true' }
$dispatchArgs = @('workflow', 'run', $workflow, '--repo', $Repo, '-f', "whatif=$whatifValue")
if ($Name) { $dispatchArgs += @('-f', "name=$Name") }
if ($PSBoundParameters.ContainsKey('Max')) { $dispatchArgs += @('-f', "max=$Max") }

Write-Host "Dispatching $workflow (whatif=$whatifValue)" -ForegroundColor Cyan
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

Write-Host "`n--- workflow report ---" -ForegroundColor Cyan
$log = & gh run view $runId --repo $Repo --log 2>$null
$log | Select-String -Pattern '(##\s|^\s*\*\*Mode|###\s|^\s*-\s|correspondent:|would-add|verification=|DRY RUN|APPLIED|stage \d|scanned )' |
    ForEach-Object { ($_.Line -split "`t")[-1] }

Write-Host "`nRun: https://github.com/$Repo/actions/runs/$runId"
exit $runExit
