# Skills strategy — source of truth

This document declares how skills and agent guidance are organized across the
`Adam-S-Daniel` (and formerly `jodidaniel`) repos. It is the answer to "where does
a skill live, and which repo wins when they disagree?"

It implements Phase 1 of the consolidation plan in
[issue #18](https://github.com/Adam-S-Daniel/agentskills/issues/18). Background:
[`docs/2026-06-05-skill-discovery-and-centralized-strategy.md`](docs/2026-06-05-skill-discovery-and-centralized-strategy.md).

## The rule, in one line

**`agentskills` is the single upstream registry for reusable skills.** Every other
repo either *consumes* skills from here or *feeds* skills into here. Nothing else
is a canonical skill home.

## Two layers, two homes

The agent setup is two distinct layers. Keep them in separate repos so neither
bloats the other:

| Layer | What it is | Canonical home |
| --- | --- | --- |
| **Skills** | Reusable `SKILL.md` capabilities, packaged as Claude Code plugins + a marketplace, and as cross-agent skills (agentskills.io spec). | **`agentskills`** (this repo) — public — and **`agentskills-private`** for the sensitive subset. |
| **Guidance / sync** | `AGENTS.md` + global instructions + the mechanism that propagates them (and selected skills) into consumer repos, with drift detection. | **`_agent-guidance`** (to move to `Adam-S-Daniel` — see issue #18, Phase 3). |

This repo owns the **skills** layer only. It deliberately does not carry
`AGENTS.md`/behavioral guidance — that belongs to `_agent-guidance`, which consumes
this registry as its upstream skill source rather than keeping a parallel copy.

## Public vs private

A skill's repo is decided by **sensitivity, not by how personal it is**:

- **Public (`agentskills`)** — generally reusable and contains **no secrets,
  credentials, or PII**. "Personal" is fine here: a skill can be idiosyncratic to
  Adam and still be public, as long as it leaks nothing sensitive.
- **Private (`agentskills-private`)** — anything that embeds or depends on
  secrets, credentials, tokens, private endpoints, or personal/identifying data;
  or that you simply don't want disclosed.

Both repos use the **same** plugin + marketplace structure (`plugins/<name>/`,
`.claude-plugin/marketplace.json`, `defaultEnabled`, the `setup.sh` de-dup), so the
installer and `sync-skills` behave identically across them (Phase 2 in issue #18).

### Placement audit (2026-06-05)

Against the rule above, the current public skills are correctly placed — none
embed secrets or PII:

- `wj-next-break` — a public high school's bell schedule. Personal interest, but
  not sensitive. **Public is correct.**
- `fastmail` — drives email through a live, already-authenticated Claude-in-Chrome
  session; the `SKILL.md` carries no credentials of its own. **Public is correct.**
- `sync-cc-settings-between-wsl-and-windows`, `sync-skills` — operate on local
  paths / the user's own browser session; no embedded secrets. **Public is correct.**
- The remaining skills (`adam-writing-style`, `pin-actions-to-sha`,
  `workflow-path-audit`, `github-actions-repo-settings`, `rename-pdfs`) are
  generically reusable. **Public is correct.**

No skill currently needs to move. Re-run this audit whenever a skill starts to
embed a secret, token, private hostname, or personal data — at that point it moves
to `agentskills-private`.

## Promotion path — when an embedded skill graduates

Consumer repos (e.g. `GHA-bench`, and other downstream projects) may define skills
locally while they are still domain-bound to that repo. A skill **graduates into
this registry** when either is true:

1. It is **reused in ≥2 repos** (or you can see the second use coming), or
2. It is **generally useful** independent of its origin repo.

When a skill graduates:

1. Move it into `plugins/<name>/skills/<name>/SKILL.md` with a
   `plugins/<name>/.claude-plugin/plugin.json` manifest.
2. Add a matching entry to `.claude-plugin/marketplace.json` (set
   `"defaultEnabled": false` if it is niche).
3. Validate with `claude plugin validate .`.
4. Replace the consumer-repo copy with the installed/marketplace version so there
   is only one source.

Skills that are **inherently bound** to one repo's internals stay local — do not
promote them just because they exist.

## Out of scope (for now)

- `civic-platform-agents` — intentionally excluded from this consolidation.
- Using `GHA-bench` as an eval harness — evals get a dedicated `skills-evals` repo
  (issue #18, Phase 5). `GHA-bench` is treated only as a source of skills to
  extract (Phase 4).
