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
3. Make the token available to the skill, by any one of:
   - `export FASTMAIL_API_TOKEN=fmu1-...` in the shell (also how Claude Code web
     injects it — see below);
   - set `FASTMAIL_TOKEN_CMD` to a command that prints the token, e.g.
     `pass show fastmail/api-token` or `op read "op://Private/Fastmail/token"`
     (keeps the token out of a plaintext file and out of a visible env config);
   - write the token (single line) to `~/.fastmail_token`.

The token is read from the environment/command/file only; it is never printed or
logged.

### Running from Claude Code web

These skills also run in a hosted Claude Code web session, with two setup steps:

1. **Provide the token.** In the cloud environment's **Configure your
   environment** panel, add `FASTMAIL_API_TOKEN=...` (`.env` format, no quotes) —
   or add a secret-manager bootstrap credential plus `FASTMAIL_TOKEN_CMD`. Note:
   web env vars are **visible to anyone who can edit that environment and are not
   masked** (there is no dedicated secret store yet), so use a tightly-scoped,
   rotatable Mail-only token.
2. **Allow network egress to Fastmail.** The default *Trusted* network policy
   only reaches package registries and GitHub, so the JMAP calls will be blocked.
   Set the environment's network access to **Full**, or a **Custom allowlist that
   includes `api.fastmail.com`** (plus your secret-manager host if you use
   `FASTMAIL_TOKEN_CMD`).

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
