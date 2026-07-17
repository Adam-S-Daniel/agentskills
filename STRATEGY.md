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

Both repos use the **same** plugin + marketplace structure
(`plugins/<bundle>/skills/<skill>/`, `.claude-plugin/marketplace.json`,
`defaultEnabled`, the `setup.sh` de-dup), so the installer and `sync-skills`
behave identically across them (Phase 2 in issue #18). The public registry
groups its skills into three bundle plugins — `adam` (cloud-safe,
default-enabled), `adam-local` (machine-bound, opt-in), `fastmail` (opt-in) —
see [ADR 0001](docs/decisions/0001-consolidate-plugins-into-bundles.md).

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

1. Add it as a directory under the right bundle's skills/ —
   `plugins/adam/skills/<skill>/SKILL.md` if it works in a headless cloud
   session of an arbitrary repo, `plugins/adam-local/skills/<skill>/` if it is
   machine-bound, `plugins/fastmail/skills/<skill>/` for the Fastmail domain.
   No new plugin.json or marketplace entry is needed — the bundle already has
   both. Skill directory basenames must be unique across the repo and must
   never change afterwards (they key `setup.sh` symlinks and claude.ai
   uploads).
2. Creating a **new bundle** is the rare, deliberate exception — it means a
   new `plugins/<bundle>/` manifest, a new marketplace entry, and (if any
   plugin is renamed away) entries in the marketplace `renames` map, which is
   **append-only forever**: users may update from any old version, so every
   historical name must keep resolving.
3. Validate with `claude plugin validate .` and run
   `python3 scripts/check_consistency.py`.
4. Replace the consumer-repo copy with the installed/marketplace version so there
   is only one source.

Skills that are **inherently bound** to one repo's internals stay local — do not
promote them just because they exist.

## Conditional loading — how to say "local only" / "needs X"

Researched 2026-07 across the Claude Code docs/changelog, the agentskills.io spec,
and the Codex/Cursor/Gemini docs. The blunt findings:

- **No first-class local-vs-cloud (or OS / tool-availability) gating exists**
  anywhere. Claude Code has no `when:`/`environment:` field for plugins or
  skills; the only enforced knob is `defaultEnabled` (static install-time
  state). The marketplace `relevance` field only affects *suggestions*, not
  loading.
- The spec's **`compatibility` frontmatter field is a free-text string
  (1–500 chars) and is machine-ignored by every consumer** (Claude Code, Codex,
  Cursor, Gemini all parse only `name` + `description`; several strip the rest).
  It is documentation for humans and, sometimes, the model.
- The only load-bearing portability mechanisms are the **`description`** (every
  consumer uses it for activation) and a **fail-fast preflight in the body or
  bundled script**.

Policy for skills in these registries, in priority order:

1. **Constraint in `description`** — if a skill is environment-bound, the
   description must say so and say when *not* to use it (e.g. "local only; do
   NOT use in headless/cloud sessions"). `fastmail` is the model example.
2. **Spec-valid `compatibility` string** — one sentence, ≤500 chars, never a
   YAML map (maps are off-spec and were migrated away 2026-07).
3. **Fail-fast preflight** — the body's first step or the bundled script checks
   its requirements (`command -v …`) and stops with a clear error.
4. **`defaultEnabled: false`** in the marketplace entry for niche or
   environment-bound plugins, so they install dormant.
5. If Claude Code ever ships real conditional loading (a `when:` for
   plugins/skills, or environment-aware settings), revisit this section. The
   closest supported workaround today is a `SessionStart` hook that checks
   `CLAUDE_CODE_REMOTE` and returns `reloadSkills: true` / runs
   `claude plugin enable|disable` — adopt only if the static approach proves
   insufficient.

## Memory — portable, in-repo (researched 2026-07)

Claude Code auto memory defaults to `~/.claude/projects/<munged-absolute-path>/memory/`
— keyed per machine, so the same project accumulates divergent stores on WSL vs
Windows, stores orphan when workspaces are deleted, and hosted agents (Claude Code
on the web, claude.ai) see none of it. The docs are explicit: auto memory is
machine-local and "not shared across machines or cloud environments".

Policy for repos in this ecosystem:

1. **Redirect memory into the repo.** Committed `.claude/settings.json` sets
   `"autoMemoryDirectory": "~/repos/<name>/.claude/memory"` (the setting accepts
   only absolute or `~/` paths — this works because repos live at `~/repos/<name>`
   on every machine, WSL and Windows alike). Memory is then git-tracked: it syncs
   between machines through normal push/pull and rides into hosted-session clones.
2. **CLAUDE.md points at it in prose** (not `@import`, which would double-load it
   where auto memory is active) so harnesses without the setting can still find it.
3. **Public repos publish their memory.** That is the accepted trade-off for
   hosted availability — the MEMORY.md header says so, and memory diffs get
   reviewed like any other change. A repo whose memory can't be public keeps the
   default machine-local store, or moves to `agentskills-private`-style hosting.
4. **Migration and hygiene** — the `migrate-claude-memory` plugin in this registry
   inventories `~/.claude/projects/` stores, flags orphans, and migrates a store
   into a repo. The portable-memory playbook lives in
   [`claude-memory-map`](https://github.com/Adam-S-Daniel/claude-memory-map).
5. `CLAUDE_MEMORY_STORES` (mounted team memory stores, changelog v2.1.172) may
   eventually supersede this pattern for hosted sessions; it is undocumented as of
   2026-07 — revisit when real docs land.

## Out of scope (for now)

- `civic-platform-agents` — intentionally excluded from this consolidation.
- Using `GHA-bench` as an eval harness — evals get a dedicated `skills-evals` repo
  (issue #18, Phase 5). `GHA-bench` is treated only as a source of skills to
  extract (Phase 4).
