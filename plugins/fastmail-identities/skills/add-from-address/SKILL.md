---
name: add-from-address
description: >
  Add one or more email addresses to a Fastmail account as selectable "From"
  (sending) identities by triggering the add-from-address GitHub Actions workflow
  in the Adam-S-Daniel/fastmail-actions repo (which does the JMAP work with the
  FASTMAIL_API_TOKEN repo secret). Trigger when the user wants to "add a from
  address", "add a sending identity", "let me send as X", "add an alias I can
  send from", or "register a new From address in Fastmail". Supports a dry-run
  (whatif) preview. For discovering which received alias addresses are worth
  adding, use the add-received-from-addresses skill instead.
allowed-tools: Bash Read
compatibility: Requires the GitHub CLI (gh) authenticated with workflow scope, and the Adam-S-Daniel/fastmail-actions repo with its FASTMAIL_API_TOKEN secret configured. Optionally pwsh 7 to use the bundled trigger.ps1 helper.
---

# Add a Fastmail "From" address (via GitHub Actions)

This skill is a thin wrapper. The actual JMAP work lives in the
[**fastmail-actions**](https://github.com/Adam-S-Daniel/fastmail-actions) repo as
the **`add-from-address.yml`** workflow, which reads the Fastmail token from the
repository secret **`FASTMAIL_API_TOKEN`**. This skill dispatches that workflow,
waits for it, and shows its report. Nothing here touches the token.

## Prerequisites (one-time)

1. The `Adam-S-Daniel/fastmail-actions` repo exists and its `FASTMAIL_API_TOKEN`
   secret is set (see that repo's README — create a read-write Mail API token in
   Fastmail and `gh secret set FASTMAIL_API_TOKEN --repo Adam-S-Daniel/fastmail-actions`).
2. `gh` is authenticated with the `workflow` scope (`gh auth status`).

## whatif (dry run)

The workflow takes a **`whatif`** input. In whatif mode it lists the pre-existing
From addresses and the ones that **would be added**, changing nothing. Otherwise
it adds them and lists what was already present and what was **newly added**.

Because the user is giving explicit addresses (explicit intent), this skill
**applies by default**. Pass whatif when the user wants a preview first.

## Run

Preferred — the bundled helper dispatches, waits, and prints the report:

```
# apply (default)
pwsh trigger.ps1 -Address new-alias@example.com,another@example.com

# preview only
pwsh trigger.ps1 -Address new-alias@example.com -WhatIf

# custom display name
pwsh trigger.ps1 -Address new-alias@example.com -Name "Full Name"
```

Or drive `gh` directly:

```
gh workflow run add-from-address.yml --repo Adam-S-Daniel/fastmail-actions \
  -f addresses="new-alias@example.com another@example.com" -f whatif=false
# then find the run and watch it:
gh run list --workflow=add-from-address.yml --repo Adam-S-Daniel/fastmail-actions --limit 1
gh run watch <run-id> --repo Adam-S-Daniel/fastmail-actions --exit-status
gh run view  <run-id> --repo Adam-S-Daniel/fastmail-actions --log
```

## Inputs

| Input | Maps to | Notes |
|---|---|---|
| `-Address a,b` | `addresses` | One or more addresses (helper accepts an array or comma list). |
| `-Name "…"` | `name` | Display name; defaults to an existing identity's name. |
| `-WhatIf` | `whatif=true` | Dry run; omit to apply. |

## Reading the output

The workflow prints (and writes to the run's job summary) a report with two
sections that always appear: **Pre-existing From addresses** and either **Would
be added** (dry run) or **Added** (applied), plus **Skipped** for addresses that
were already identities. An added address shows `verification=autoverified` when
it is an alias on a domain you control (usable immediately) or a pending/failed
state otherwise — in which case add it as a Fastmail alias/domain first and
re-run.
