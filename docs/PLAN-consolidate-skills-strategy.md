# Plan: consolidate the centralized skills strategy

> Tracking plan. Issues are disabled on this repo, so this lives as a doc; check
> the boxes as phases land. Source analysis:
> [`docs/2026-06-05-skill-discovery-and-centralized-strategy.md`](2026-06-05-skill-discovery-and-centralized-strategy.md)
> and the portfolio review across the `Adam-S-Daniel` + `jodidaniel` repos.

## Goal

Turn the loosely-related skills/agent repos into a coherent strategy with **one
declared source of truth**. Today skills live in at least three "homes"
(`agentskills`, `agentskills-private`, `jodidaniel/_agent-guidance/skills/`) plus
embedded copies in consumer repos, and `_agent-guidance` already ships a
`drift-report.md` — i.e. drift is a known problem. This is the implementation
plan to fix that.

### Caveats baked into this plan (from review)

- **Evals:** `GHA-bench` is **not** the eval mechanism for these skills. Instead
  we (a) pull *unique* skills out of GHA-bench into the central registry, and
  (b) design and build a **dedicated skills-eval repo**.
- **`civic-platform-agents`:** out of scope for now — ignored throughout.
- **Sync engine location:** `_agent-guidance` moves from the `jodidaniel` org to
  `Adam-S-Daniel` so all canonical agent infra lives in one account.

### Repos in scope

- `Adam-S-Daniel/agentskills` (this repo — canonical registry)
- `Adam-S-Daniel/agentskills-private`
- `jodidaniel/_agent-guidance` → to be moved to `Adam-S-Daniel/_agent-guidance`
- `Adam-S-Daniel/GHA-bench` (source of skills to extract only)
- `Adam-S-Daniel/tools`
- New: `Adam-S-Daniel/skills-evals` (to be created)

---

## Phase 1 — Declare the canonical registry & relationships

- [ ] Add a `STRATEGY.md` (or a README section) stating that **`agentskills` is
  the single upstream skill registry**; every other repo either consumes from it
  or feeds into it. Define the two layers explicitly: **skills** (this repo) vs
  **guidance / AGENTS.md + sync** (`_agent-guidance`).
- [ ] Document the **public vs private rule**: public = generally reusable / no
  secrets; private = sensitive, credentialed, or PII. Audit current placement
  against the rule (e.g. confirm whether personal skills like `wj-next-break`
  belong public).
- [ ] Define the **promotion path**: when a skill embedded in a consumer repo is
  reused ≥2 times or is generally useful, it graduates into this registry as a
  plugin; domain-bound skills stay local.

## Phase 2 — Align `agentskills-private` with this repo

- [ ] Apply the same plugin + marketplace structure used here (`plugins/<name>/`,
  `.claude-plugin/marketplace.json`, `defaultEnabled`, `setup.sh` de-dup) to
  `agentskills-private`, so the installer and `sync-skills` behave identically.
- [ ] Decide the mechanism: private **second marketplace** vs private **overlay**
  on the public one. Document the choice.
- [ ] Ensure `setup.sh` / `sync-skills` operate over both registries without
  double-loading (reuse the de-dup logic added in #16).

## Phase 3 — Move & repurpose the sync engine (`_agent-guidance`)

- [ ] **Transfer `jodidaniel/_agent-guidance` → `Adam-S-Daniel/_agent-guidance`**
  (GitHub repo transfer preserves history/issues and sets up redirects). Update
  references/sync configs afterward.
- [ ] Repoint `_agent-guidance` to **consume `agentskills` (+
  `agentskills-private`) as its upstream skill source** instead of maintaining a
  parallel `skills/` copy. Its `.agents-sync.yml` should pull from the
  marketplace / `plugins/` tree.
- [ ] Once it consumes upstream, the parallel `skills/` copy and
  `drift-report.md` become unnecessary — **remove the drift surface** (no
  parallel copy = no drift).
- [ ] Keep `_agent-guidance` focused on the **guidance layer**: AGENTS.md
  propagation + the sync mechanism into consumer repos.
- [ ] Source this repo's README "Global Instructions" snippet from
  `_agent-guidance` rather than hand-maintaining a copy here (single source for
  the guidance layer).

## Phase 4 — Extract unique skills from `GHA-bench`

- [ ] Inventory `GHA-bench/skills/` and diff against this repo's existing
  GitHub-Actions plugins (`pin-actions-to-sha`, `workflow-path-audit`,
  `github-actions-repo-settings`).
- [ ] **Pull the unique/generally-useful skills** into this registry as plugins
  (proper `plugins/<name>/` + marketplace entry + `claude plugin validate`).
- [ ] Leave benchmark-specific harness skills in `GHA-bench`. **Do not** wire
  GHA-bench in as an eval harness for these skills.

## Phase 5 — Design & build a dedicated skills-eval repo

- [ ] Create `Adam-S-Daniel/skills-evals`.
- [ ] **Design** the eval approach: for a given skill, measure agent
  performance/quality **with vs. without** the skill installed; support
  LLM-as-judge scoring + objective checks; emit per-skill regression results.
- [ ] Decide harness shape (task fixtures per skill, model matrix, cost/token
  capture) and how it pulls skills from this registry (marketplace install or
  `plugins/` path).
- [ ] **Implement** a first eval for one existing skill end-to-end (proposed:
  `pin-actions-to-sha` or `rename-pdfs`) as the reference pattern.
- [ ] Wire results back as a quality signal for the registry (badge / report
  link from this repo).

## Phase 6 — Tooling overlap & housekeeping

- [ ] Resolve the PDF-tooling overlap: `tools/compare-pdfpairs` (PowerShell) vs
  this repo's `rename-pdfs` (Python). Either promote `compare-pdfpairs` into the
  registry as a skill or give `tools` an explicit "promote to skill" path so it
  doesn't become a fourth skill home.
- [ ] Archive experiment/noise repos so the account inventory reflects the real
  strategy: `scratch-claude-001`, `scratch-jules-001`,
  `jodidaniel/scratch-claude-002`, `jodidaniel/squarespacetemp`.

---

## Sequencing

- Phase 1 unblocks everything (it sets the rules). Phases 2–4 can proceed in
  parallel once Phase 1 lands. Phase 5 depends on the registry being canonical
  (Phase 1) but not on 2–4. Phase 6 is independent cleanup.
- **Out of scope (deliberate):** `civic-platform-agents`, and using `GHA-bench`
  as an eval harness.
