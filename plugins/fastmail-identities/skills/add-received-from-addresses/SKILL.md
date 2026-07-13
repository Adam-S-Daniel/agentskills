---
name: add-received-from-addresses
description: >
  Discover which of a Fastmail account's own alias addresses are worth being able
  to send from, and add them as "From" identities, by triggering the
  add-received-from-addresses GitHub Actions workflow in the
  Adam-S-Daniel/fastmail-actions repo (which does the JMAP work with the
  FASTMAIL_API_TOKEN repo secret). It scans every message for distinct
  X-Delivered-To addresses, keeps only those you actually correspond through,
  drops any that are already identities, and adds the rest. Trigger when the user
  wants to "add From addresses for aliases I actually use", "find alias addresses
  worth sending from", or "set up identities for the addresses that receive my
  mail". Supports a dry-run (whatif) preview. To add a specific known address, use
  the add-from-address skill instead.
allowed-tools: Bash Read
compatibility: Requires the GitHub CLI (gh) authenticated with workflow scope, and the Adam-S-Daniel/fastmail-actions repo with its FASTMAIL_API_TOKEN secret configured. Optionally pwsh 7 to use the bundled trigger.ps1 helper.
---

# Add "From" addresses for aliases you actually correspond through (via GitHub Actions)

This skill is a thin wrapper. The discovery + JMAP work lives in the
[**fastmail-actions**](https://github.com/Adam-S-Daniel/fastmail-actions) repo as
the **`add-received-from-addresses.yml`** workflow, which reads the Fastmail token
from the repository secret **`FASTMAIL_API_TOKEN`**. This skill dispatches that
workflow, waits for it, and shows its report. Nothing here touches the token.

## Prerequisites (one-time)

1. The `Adam-S-Daniel/fastmail-actions` repo exists and its `FASTMAIL_API_TOKEN`
   secret is set (Fastmail API token with **Mail** read-write **+ Email
   Submission** scopes — see that repo's README).
2. Report routing secrets `FASTMAIL_REPORT_FROM` / `FASTMAIL_REPORT_TO` are set.
   The workflow **emails** the report there; it never prints addresses,
   correspondents, or mailbox totals to the public run log.
3. `gh` is authenticated with the `workflow` scope (`gh auth status`).

## How it decides (three stages, inside the workflow)

1. **Distinct delivered-to.** Every distinct `X-Delivered-To` address across all
   messages.
2. **Known correspondents.** Keep an alias only if at least one message delivered
   to it came from a sender you have *also sent mail to* — dropping one-way
   addresses (newsletters, signup-only).
3. **Not already set up.** Drop any that are already sending identities.

## whatif (dry run)

The workflow takes a **`whatif`** input, and this discovery skill **defaults to a
dry run**: it emails a report of the pre-existing From addresses and the aliases
that **would be added** (each annotated with the correspondent that qualified
it). Review that email and confirm, then re-run with whatif off to apply — after
which it emails what was already present and what was **newly added**.

## Run

Preferred — the bundled helper dispatches, waits, and reports run success (the
detailed report is emailed to `FASTMAIL_REPORT_TO`, not printed):

```
# preview (default)
pwsh trigger.ps1

# apply after confirming
pwsh trigger.ps1 -Apply

# quick sample of the newest N messages
pwsh trigger.ps1 -Max 2000
```

Or drive `gh` directly:

```
gh workflow run add-received-from-addresses.yml --repo Adam-S-Daniel/fastmail-actions -f whatif=true
gh run list  --workflow=add-received-from-addresses.yml --repo Adam-S-Daniel/fastmail-actions --limit 1
gh run watch <run-id> --repo Adam-S-Daniel/fastmail-actions --exit-status
# The run log holds only a status line; the report is emailed to FASTMAIL_REPORT_TO.
```

## Inputs

| Input | Maps to | Notes |
|---|---|---|
| `-Apply` | `whatif=false` | Actually create the identities (default is a dry run). |
| `-Name "…"` | `name` | Display name; defaults to an existing identity's name. |
| `-Max N` | `max` | Scan only the newest N messages (quick sample). |

## Notes

- **Privacy:** the report (funnel counts, candidate aliases, correspondents,
  mailbox totals) is emailed to `FASTMAIL_REPORT_TO` — never printed to the
  public run log. This workflow takes no address input, so nothing about your
  addresses reaches any GitHub surface.
- Scans **all** mail (including Junk/Trash); the correspondent filter removes
  aliases that only ever got junk, so this is safe.
- Idempotent: re-running skips anything already added.
- Own-domain aliases come back `verification=autoverified` and are usable
  immediately.
