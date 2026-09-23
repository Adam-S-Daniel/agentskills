# 0010. Let pinned channels own the terminal and leave the account channel the surfaces with nothing else

- **Status:** Accepted (2026-09-23)
- **Date:** 2026-09-18
- **Deciders:** Adam Daniel

## Context

Claude Code 2.1.275's changelog: *"Added syncing of the skills and plugins
enabled on your claude.ai account to terminal sessions signed in with it; opt
out with `syncClaudeAiSkills: false` or `syncClaudeAiPlugins: false`."* The
docs date the terminal half to v2.1.273
([Where synced skills load](https://code.claude.com/docs/en/skills#where-synced-skills-load)).

**Before**, the account store loaded in chat, Cowork, mobile, Claude in Chrome
and cloud sessions. A terminal on the laptop got it only from a `-p` run with
`CLAUDE_CODE_SYNC_SKILLS=1`, which is how `sync_skills.py --verify` refreshed
its mirror.

**Now**, every terminal session signed in with the account downloads all 21
account skills at start, re-checks claude.ai every ~10 minutes, and loads them
as `anthropic-skills:<name>`. A same-named local, plugin or bundled skill keeps
the short name and the synced copy stays loaded under the long one
([When a synced skill name matches another command](https://code.claude.com/docs/en/skills#when-a-synced-skill-name-matches-another-command)).

Three properties of the switch decide the shape of any answer:

- `syncClaudeAiSkills` honours **only `false`**, and only from **user, local or
  managed** settings. A repo's `.claude/settings.json` cannot set it. So a
  cloud session cannot opt out and this decision cannot reach one.
- On `false` the CLI stops downloading, stops loading what was synced, and
  moves it to `~/.claude/skills/.trash/`.
- `setup.sh` deliberately leaves `~/.claude/skills` alone — "the marketplace
  owns Claude Code" — and converges only `extraKnownMarketplaces` and
  `enabledPlugins: {"adam@agentskills": true}`.

### What a converged laptop terminal now loads

| Source | Skills | Pinned / verified? |
|---|---|---|
| `adam@agentskills` (marketplace) | 9 | yes, by marketplace version |
| account sync, `custom` | 10 | **no** |
| account sync, Anthropic | 11 | Anthropic's, unversioned here |

### The cost, measured rather than asserted

`/context` was not available to the session that wrote this, so the figure is
estimated from the live manifest the way ADR 0002 estimated its own: the
always-on listing carries `name` plus `description` per skill, at ~4 characters
per token. Read from
`~/.claude/skills/synced/<org>_<account>/manifest.json`, 21 skills, 2026-09-18:

| Group | Skills | ~tokens |
|---|---:|---:|
| duplicates a skill `adam@agentskills` already delivers | 3 | **497** |
| duplicates a skill `adam-local` holds (not converged today) | 6 | 848 |
| account-only `custom` (`fastmail`) | 1 | 173 |
| Anthropic (`docx`, `pptx`, `xlsx`, `pdf`, `docs`, `learn`, `skill-creator`, `theme-factory`, `web-artifacts-builder`, `doc-coauthoring`, `import-memory`) | 11 | 1,718 |
| **total** | **21** | **~3,236** |

The three duplicates are `adam-writing-style` (~175), `finding-unknowns` (~176)
and `writing-adrs` (~146). The per-skill spread matters more than the average
ADR 0002 used: `docx` alone is ~255 tokens, more than the three duplicates'
smallest two combined, and #54 records that at the default budget the
least-used descriptions are silently dropped — so the cost is not only tokens,
it is which descriptions survive.

### Why this needs a decision and not a doc update

1. **The drifting channel now serves the terminal**, `sync-skills` included.
   E5 measured that skill's account copy 127 lines behind the registry, and
   unless `adam-local` is installed by hand the account copy is the only
   terminal copy of the one skill that must run on the laptop. E5 §7 called the
   exposure "the exact inverse of the delivery"; it is no longer inverse, it is
   everywhere.
2. **Always-on context doubles for 3 skills and adds 18 more**, per the table
   above.
3. **The mirror is no longer laptop-only.** A cloud session always has it and
   cannot opt out, so `--verify` and `--record-account-state` can run there now
   that #157 has landed — measured in that PR: `Recorded 10/10 declared skills`
   from a cloud session. The record half of ADR 0006's loop stops needing the
   laptop.

## Decision

**Pinned channels own the terminal; the account channel owns the surfaces that
have nothing else.**

- On a durable machine that runs `setup.sh`: converge `syncClaudeAiSkills:
  false` into `~/.claude/settings.json`, in the same deep-merge block that
  converges the marketplace registration, and converge
  `"adam-local@agentskills": true` beside `"adam@agentskills": true` so the
  machine-bound bundle arrives pinned from the marketplace, where it has been
  drifting on the account.
- `fastmail` and `wj-next-break` stay account-only **on purpose**: the first
  says "do NOT use in the Claude Code CLI", the second is descoped by ADR 0002.
  Opting the terminal out is what makes that split real rather than nominal.
- **Leave `syncClaudeAiPlugins` alone** until E6
  ([#160](https://github.com/Adam-S-Daniel/agentskills/issues/160)) answers what
  the plugin channel reaches. The bucket under `~/.claude/plugins/synced/` is
  empty today — nothing is enabled — so the switch costs nothing to leave
  alone and would be decided on no evidence.
- **Cloud sessions: unchanged**, because they cannot opt out. E5 already shows
  the hook copy wins the short name there, and #157 has made the account arm
  readable from one, which is a gain rather than a cost.

## Consequences

- **Opting out removes Anthropic's `docx`/`pptx`/`xlsx`/`pdf` skills from
  laptop terminals.** Stated plainly because it is the real price: ~950 tokens
  of the ~1,718 Anthropic total is those four, and they are genuinely useful
  there. The pinned way back is Anthropic's own marketplace plugin
  (`document-skills` in `anthropics/skills`). That is an owner call and out of
  scope here; this ADR does not make it.
- **`adam-local` becomes a marketplace install on the laptop**, so its skills
  gain the digest-checked delivery they have never had and `sync-skills` stops
  being reachable only through the channel it exists to police.
- **Two surfaces now behave differently on purpose.** A laptop terminal and a
  cloud session load different sets, and anyone debugging "why does this skill
  not trigger here" must check which. `skills-doctor` is extended in the same
  change to report the settings-chain value of `syncClaudeAiSkills` and
  `syncClaudeAiPlugins`, every account skill that duplicates another copy with
  which one owns the short name, and the `.trash/` state after an opt-out — so
  the difference is reported rather than rediscovered.
- **The `CLAUDE_CODE_SYNC_SKILLS=1 claude -p 'ok'` refresh step is now wrong on
  both branches** and is rewritten: on a syncing terminal the mirror refreshes
  itself, and on an opted-out laptop the env var has nothing to refresh. The
  verify and record steps move to a cloud session against the bucketed mirror.
- **Nothing here binds a cloud session or CI**, which cannot set the key. The
  decision is therefore about one class of machine, and a reader must not take
  a green laptop as evidence about any other surface — the same limit ADR 0002
  records about CI and the account store.

## Alternatives considered

**Keep sync on everywhere and accept the duplicates**, on the grounds that the
short-name rule already makes the pinned copy win. **Rejected**, on the
measurement rather than the principle: it costs ~3,236 tokens of always-on
description per terminal session, of which ~497 buys a second copy of three
skills the machine already has pinned, and it leaves a measured-stale
`anthropic-skills:sync-skills` reachable by its long name in every laptop
session. The short-name rule protects which copy is *read*; it does nothing
about what is *listed*, and #54 is about the listing.

Worth recording that this alternative is the right answer for a machine that
does NOT run `setup.sh` — the account channel is the only channel there, and
the argument above depends entirely on the pinned copy already being present.

**Opt out of `syncClaudeAiPlugins` at the same time**, for symmetry.
**Rejected** as a decision taken on no evidence: the plugin bucket is empty, so
nothing was measured and E6 (#160) exists to measure it.

## How to verify

- `scripts/test_setup_settings_convergence.py` runs `setup.sh`'s convergence
  against a temporary `HOME` and **parses the resulting JSON** — no regex over
  the file — asserting both new keys, that a second run is idempotent, and that
  unrelated keys and sibling marketplaces survive the deep merge.
- `check_provenance.py --account-channel <settings.json>` reports the resolved
  `syncClaudeAiSkills` / `syncClaudeAiPlugins` and the duplicate map; its tests
  cover a machine that has opted out, one that has not, and one whose
  `.trash/` holds what an opt-out moved.

## References

- [#158](https://github.com/Adam-S-Daniel/agentskills/issues/158) — the issue this answers
- [#157](https://github.com/Adam-S-Daniel/agentskills/issues/157) — the bucket-layout fix this depends on
- [#160](https://github.com/Adam-S-Daniel/agentskills/issues/160) — E6, the account plugin channel
- [#54](https://github.com/Adam-S-Daniel/agentskills/issues/54) — descriptions dropped at the default budget
- [ADR 0002](0002-limit-account-store-to-repo-independent-skills.md) — account-store membership, and the ~185 tok/skill estimate this table refines
- [ADR 0006](0006-drive-the-account-store-drift-loop-from-one-published-artifact.md) — the drift loop whose record half stops needing the laptop
- [E5](../experiments/E5-account-store-vs-hook-precedence.md) — precedence, and the measured `sync-skills` staleness
- Claude Code docs: [`syncClaudeAiSkills`](https://code.claude.com/docs/en/settings-reference#syncclaudeaiskills), [`syncClaudeAiPlugins`](https://code.claude.com/docs/en/settings-reference#syncclaudeaiplugins), [synced plugins](https://code.claude.com/docs/en/plugins-reference#synced-plugins)
