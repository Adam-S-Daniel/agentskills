---
name: add-received-from-addresses
description: >
  Discover which of a Fastmail account's own alias addresses are worth being
  able to send from, and add them as "From" identities. It scans every message
  for distinct X-Delivered-To addresses (the aliases mail was delivered to),
  keeps only those you actually correspond through (at least one sender you have
  also emailed), drops any that are already sending identities, and adds the
  rest. Uses the JMAP API with a Fastmail API token — no browser needed, runs
  headless. Trigger when the user wants to "add From addresses for aliases I
  actually use", "find alias addresses worth sending from", "set up identities
  for the addresses that receive my mail", or similar. To add a specific known
  address, use the add-from-address skill instead.
allowed-tools: Bash Read
compatibility: Requires Python 3 (stdlib only) and a Fastmail API token with read-write Mail access in FASTMAIL_API_TOKEN (or ~/.fastmail_token)
---

# Add "From" addresses for aliases you actually correspond through

Personal mail often arrives at many alias addresses (`X-Delivered-To`), but only
some are aliases you have real two-way correspondence through and would ever want
to send *as*. This skill finds those and adds them as sending identities.

## Auth

Same as the `add-from-address` skill: a Fastmail API token with **read-write
Mail** access, in `FASTMAIL_API_TOKEN` or `~/.fastmail_token`. See that skill for
how to create it.

## How it decides (three internal stages)

1. **Distinct delivered-to.** Every distinct `X-Delivered-To` address across all
   messages in the account.
2. **Known correspondents.** Keep an alias only if at least one message delivered
   to it came from a sender you have *also sent mail to*. This drops aliases that
   only ever received one-way mail (newsletters, signup-only addresses). *(If you
   ever want the stricter "every sender must be known" rule instead, that is a
   one-line change in `filter_known_correspondents`.)*
3. **Not already set up.** Drop any that are already sending identities.

The survivors are added via the same `Identity/set` call as `add-from-address`.

## Run

Preview (default — makes no changes):

```
python3 ~/repos/agentskills/plugins/fastmail-identities/skills/add-received-from-addresses/discover_and_add.py
```

It prints stage counts and the exact list it would add, each annotated with the
correspondent that qualified it. **Show this list to the user and confirm**, then
apply:

```
python3 ~/repos/agentskills/plugins/fastmail-identities/skills/add-received-from-addresses/discover_and_add.py --apply
```

Options:

| Flag | Effect |
|---|---|
| `--apply` | Actually create the identities (default is a dry run). |
| `--name "Full Name"` | Display name for new identities. Defaults to an existing identity's name. |
| `--max N` | Scan only the newest N messages (quick sample). Prints a WARNING that results are not exhaustive. |

## Notes

- Scans **all** mail (including Junk/Trash); the correspondent filter removes
  aliases that only ever got junk, so this is safe.
- Idempotent: re-running skips anything already added.
- Output verification states read the same as in `add-from-address` — own-domain
  aliases come back `autoverified` and are usable immediately.
