---
name: add-from-address
description: >
  Add one or more email addresses to a Fastmail account as selectable "From"
  (sending) identities, so they appear in the From dropdown when composing in
  the Fastmail UI. Uses the JMAP API with a Fastmail API token (bearer auth) —
  no browser session required, so it works headless (CLI, CI, cloud). Trigger
  when the user wants to "add a from address", "add a sending identity", "let me
  send as X", "add an alias I can send from", or "register a new From address in
  Fastmail". For discovering which received alias addresses are worth adding,
  use the add-received-from-addresses skill instead.
allowed-tools: Bash Read
compatibility: Requires Python 3 (stdlib only) and a Fastmail API token with read-write Mail access in FASTMAIL_API_TOKEN (or ~/.fastmail_token)
---

# Add a Fastmail "From" address

Adds each given address as a sending identity via a single JMAP `Identity/set`
call — the same operation the Fastmail web UI performs when you add a From
address under Settings. Because it talks to the JMAP API directly with an API
token, it needs no logged-in browser and runs anywhere.

## Auth (one-time setup)

1. In Fastmail: **Settings → Privacy & Security → Integrations → API tokens →
   New API token**.
2. Grant it **read-write access to Mail**. (Managing sending identities uses the
   JMAP `submission` capability, which the Mail scope covers.)
3. Make the token available to the skill, either:
   - `export FASTMAIL_API_TOKEN=fmu1-...` in the shell, or
   - write the token (single line) to `~/.fastmail_token`.

The token is read from the environment/file only; it is never printed or logged.

## Run

```
python3 ~/repos/agentskills/plugins/fastmail-identities/skills/add-from-address/add_identity.py \
    new-alias@example.com another@example.com
```

Options:

| Flag | Effect |
|---|---|
| `--name "Full Name"` | Display name for the new identities. Defaults to the name on an existing identity. |
| `--dry-run` | Print what would be added; make no changes. |

By default it **applies** (the user gave explicit addresses = explicit intent).
Use `--dry-run` first if you want to preview.

## What it does

- Fetches existing identities and **skips** any address already present
  (idempotent — safe to re-run).
- Looks up the Sent mailbox so mail sent from the new identity is saved there.
- Creates each identity with sensible defaults (shown in compose, usable for
  auto-reply).

## Reading the output

One line per address: `added`, `skipped` (already an identity), `would-add`
(dry run), or `failed`.

- `added ... verification=autoverified` — ready to use immediately. This is what
  you get for an alias on a domain you control.
- `added ... verification=pending` / `failed` — Fastmail could not auto-verify
  the address (you don't control it, or it isn't a configured alias). Add it as
  a Fastmail alias/domain first, then re-run.
