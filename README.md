# agentskills

[![pin-actions-to-sha eval](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FAdam-S-Daniel%2Fskills-evals%2Feval-results%2Fbadges%2Fpin-actions-to-sha.json)](https://github.com/Adam-S-Daniel/skills-evals)

Measures the `pin-actions-to-sha` skill's with-vs-without eval, run weekly.

Adam Daniel's reusable agent skills, packaged as **Claude Code plugins** and as
cross-agent skills that follow the
[Agent Skills specification](https://agentskills.io/specification).

Skills are grouped into three **bundle plugins** under `plugins/<bundle>/skills/<skill>/`,
and the repo root is a Claude Code **plugin marketplace**
(`.claude-plugin/marketplace.json`). The exact same `SKILL.md` files are consumed
unchanged by Codex, Cursor, VS Code, and any other agent that reads the Agent Skills
format — so a skill is authored once and installs everywhere.

This repo is the **canonical upstream registry** for reusable skills. For where
skills live across repos, the public/private rule, and how a skill graduates into
this registry, see [`STRATEGY.md`](STRATEGY.md).

## Install — Claude Code

Add the marketplace once, then install whichever bundles you want:

```bash
/plugin marketplace add Adam-S-Daniel/agentskills
/plugin install adam@agentskills
# opt-in bundles:
/plugin install adam-local@agentskills
/plugin install fastmail@agentskills
# …or browse and pick interactively:
/plugin
```

Skills are namespaced by bundle — invoke them as `/<bundle>:<skill>`, e.g.
`/adam:pin-actions-to-sha`. Update later with `/plugin marketplace update agentskills`.

### Bundles

Membership follows where a skill can run: skills usable in a **headless cloud
session of an arbitrary repo** go in `adam` (installed by default);
**machine-bound / local-resource** skills (WSL/Windows homes, local files, a
signed-in browser) go in `adam-local` (opt-in); the **Fastmail domain** is
`fastmail` (opt-in).

If you installed the old per-skill plugins (`pin-actions-to-sha`,
`rename-pdfs`, …), they migrate to their bundle automatically on
`/plugin marketplace update agentskills` via the marketplace `renames` map.
That map is **append-only forever** — JSON has no comments, so it's said here:
never delete or repoint an entry, because users may update from any old
version and every historical name must keep resolving.

**Migrating an existing machine:** re-run `bash setup.sh` immediately after
pulling this restructure — the global sync-skills pre-push hook still points
at the old path and **blocks every `git push` from any repo** until
re-registered (setup.sh also relinks the now-dangling per-agent skill links).
If you had both `fastmail` and `fastmail-identities` installed, the merged
`fastmail` keeps its previous enabled/disabled state and a version-pinned
cache lacks the two migrated-in skills — run
`claude plugin update fastmail@agentskills` (and
`claude plugin enable fastmail@agentskills` if it ended up disabled).

Available skills:

<!-- BEGIN GENERATED PLUGIN TABLE -->
| Plugin | Invocation | Description |
| --- | --- | --- |
| `adam` | `/adam:adam-writing-style` | Write in Adam Daniel's voice — professional but warm, direct, em-dash-friendly, free of corporate buzzwords. |
| `adam` | `/adam:debug-github-workflows` | Debugging GitHub Actions workflow failures. |
| `adam` | `/adam:finding-unknowns` | Surface and resolve the ambiguities in a task before, during, and after implementation — the blind-spot pass, the self-interview, reference-driven specs, implementation notes, and a post-hoc explainer or quiz. |
| `adam` | `/adam:github-actions-repo-settings` | Configure and enforce GitHub repository security settings as code: require actions to be pinned to full-length commit SHAs, require approval for all outside collaborators' fork pull-request workflow runs, and protect the default branch via a repository ruleset. |
| `adam` | `/adam:pin-actions-to-sha` | Audit and fix GitHub Actions workflow files to ensure every `uses` reference is pinned to a full-length commit SHA (40 hex characters) with a version comment that includes the release date. |
| `adam` | `/adam:review-bash-ci-reliability` | Review bash scripts for CI/CD reliability issues. |
| `adam` | `/adam:skills-doctor` | Diagnose skill DELIVERY health for the current session: name the surface, diff the expected set in `skills.lock` against what actually loaded (the session's own skill listing, `~/.claude/skills/`, the account `synced/manifest.json`, `claude plugin list`), attribute every skill to the channel that delivered it, and flag silent shadowing, account-store staleness, dangling payload references, and always-on context cost. |
| `adam` | `/adam:workflow-path-audit` | Audit GitHub Actions workflows for salient-path conditionals — every workflow that triggers on pull_request or push must filter on the files and directories its steps actually depend on, and skip with success when nothing salient changed. |
| `adam` | `/adam:writing-adrs` | Write a lightweight Nygard-style Architecture Decision Record under `docs/decisions/` when a non-obvious decision needs context that won't fit in a code comment and would rot if left only in a PR description. |
| `adam-local` | `/adam-local:compare-pdfpairs` | Compare pairs of PDFs (name.pdf + name<suffix>.pdf in the same folder) to determine whether they would produce identical printouts and whether their embedded text differs — e.g. to safely delete redundant "-signed" or "-needsocr" duplicates. |
| `adam-local` | `/adam-local:launch-wsl-claude-session` | Launch a detached, interactive Claude Code session inside WSL from a Windows Claude Code session — in a specific repo/folder, optionally remote-controllable and optionally seeded with an initial prompt. |
| `adam-local` | `/adam-local:migrate-claude-memory` | Inventory, clean up, and migrate Claude Code auto-memory stores found under ~/.claude/projects/<munged-path>/memory/ on this machine. |
| `adam-local` | `/adam-local:ocr-pdfs` | Batch-OCR scanned PDFs flagged as needing OCR, then visually review results with a WPF side-by-side comparison tool. |
| `adam-local` | `/adam-local:pdf-ocr-audit` | Audit PDF files to determine whether OCR (optical character recognition) is needed to make them fully text-searchable. |
| `adam-local` | `/adam-local:rename-pdfs` | Rename already-searchable PDFs in a specified folder to descriptive, date-prefixed names, proposing each name from the PDF's own content and prompting for per-file confirmation or edit before applying. |
| `adam-local` | `/adam-local:sync-cc-settings-between-wsl-and-windows` | Sync Claude Code settings.json between a Windows home and a WSL home. |
| `adam-local` | `/adam-local:sync-skills` | Sync local skill folders from git repos to Claude.ai (and other agent targets) via the upload-skill API. |
| `adam-local` | `/adam-local:wj-next-break` | Answer questions about the current or next class period, break, passing period, lunch, or bell at Walter Johnson High School (WJ / WJHS, Bethesda MD). |
| `fastmail` | `/fastmail:add-from-address` | Add one or more email addresses to a Fastmail account as selectable "From" (sending) identities by triggering the add-from-address GitHub Actions workflow in the Adam-S-Daniel/fastmail-actions repo (which does the JMAP work with the FASTMAIL_API_TOKEN repo secret). |
| `fastmail` | `/fastmail:add-received-from-addresses` | Discover which of a Fastmail account's own alias addresses are worth being able to send from, and add them as "From" identities, by triggering the add-received-from-addresses GitHub Actions workflow in the Adam-S-Daniel/fastmail-actions repo (which does the JMAP work with the FASTMAIL_API_TOKEN repo secret). |
| `fastmail` | `/fastmail:fastmail` | Automate Fastmail email workflows via a local browser session. |
<!-- END GENERATED PLUGIN TABLE -->

## Install — Codex, Cursor, and local use

These tools discover skills from per-agent directories rather than a marketplace.
Run `setup.sh` once **in each environment** (Windows Git Bash *and* WSL — they have
separate `$HOME`s):

```bash
bash setup.sh
```

It links every skill under `plugins/*/skills/*` into the standard skill homes:

- `~/.agents/skills/` — Codex (and the generic agents dir)
- `~/.agent/skills/`
- `~/.cursor/skills/`

**Claude Code is deliberately not in that list** — it's served by the marketplace
above. Linking the same skills into `~/.claude/skills` too would double-load them
(once as a namespaced plugin, once as a personal skill), so `setup.sh` now removes
any such links it created in earlier versions. Background and rationale:
[`docs/2026-06-05-skill-discovery-and-centralized-strategy.md`](docs/2026-06-05-skill-discovery-and-centralized-strategy.md).

> **Gemini / Antigravity was retired as a target (2026-08-14).** That is an owner
> **scope decision, not** a finding that those paths were dead — Gemini/Antigravity is
> four separately-versioned products, three of which read skills from three
> *different* directories, and the Antigravity IDE genuinely does read
> `~/.gemini/antigravity/skills`. So this removes a link that was doing real work.
> Because un-listing a home leaves the old links behind — still feeding an
> unmanaged copy of the skill set, and dangling as soon as a skill is renamed —
> `setup.sh` also **sweeps** `~/.gemini/skills/` and
> `~/.gemini/antigravity/skills/`: it removes only links that resolve into this
> repo's `plugins/` tree, then removes each directory only if that left it empty.
> Your own files and links there are untouched. Re-run `bash setup.sh` on any
> machine set up before this change.

On Windows it uses directory junctions (`mklink /J`) — no admin required. The script
is idempotent and migrates the old whole-directory links left by earlier versions.

After running `setup.sh`, you don't need to restart an open Claude Code session —
run `/reload-skills` to re-scan the skill directories in place.

> Codex reads `~/.agents/skills`; that link is what makes these skills available in
> Codex. See the [Codex skills docs](https://developers.openai.com/codex/skills).

### Agent Plugins v1 — the root `plugin.json`

Each bundle ships **two** manifests, on purpose:

| File | Read by |
| --- | --- |
| `plugins/<bundle>/plugin.json` | [Agent Plugins 1.0.0](https://agent-plugins.org) clients — Codex, VS Code, Cursor, GitHub Copilot |
| `plugins/<bundle>/.claude-plugin/plugin.json` | Claude Code |

Claude Code is **not** an Agent Plugins conformant client and is absent from the
spec's client roster, so it keeps its own manifest; the two coexist rather than
one replacing the other. Neither declares the skills — the spec discovers them
by convention at `<plugin-root>/skills/`, which this repo's layout already
satisfies. Because both are shipped, both can drift, so
`scripts/check_agent_plugins.py` validates the root manifests against the
schema **vendored** at `schemas/agent-plugins-1.0.0-plugin.schema.json` (the
spec repo publishes no tags or releases, so there is nothing to pin a fetch to)
and cross-checks `name` + `version` between each pair. It runs in CI.

The schema is closed, and requires only `$schema` and `name`. The
marketplace-only keys `category` and `defaultEnabled` are **invalid** in a root
manifest, and there is no `skills` key at all.

Measured minimum client versions:

- **Codex ≥ 0.147.0** — established by source-diffing release tags
  `rust-v0.146.0` vs `rust-v0.147.0`. It accepts **only** the exact canonical
  `$schema` string; anything else is rejected as "unsupported Agent Plugins
  schema".
- **VS Code ≥ 1.131.0** — established by tag-bisecting
  `src/vs/platform/agentPlugins/common/agentPluginParser.ts`. Never announced
  in the release notes.
- **Cursor** — reads it (verified in the newest CLI build); no changelog names
  it and no minimum is established.
- **GitHub Copilot** — has read a root `plugin.json` as its own long-standing
  format; declaring `$schema` is what opts into Agent Plugins v1 semantics
  (GA 2026-08-12). No minimum established; measured working on 1.0.79 and 1.0.80.

**Codex needs no extra marketplace file.** Its marketplace search path includes
`.claude-plugin/marketplace.json` alongside `.agents/plugins/marketplace.json`,
so the file this repo already has is the one it reads — verified live
(`codex plugin add adam@agentskills` installed every skill).

<!-- Do NOT add .agents/plugins/marketplace.json. Codex 0.147.0's
     MARKETPLACE_MANIFEST_RELATIVE_PATHS is [".agents/plugins/marketplace.json",
     ".agents/plugins/api_marketplace.json", ".claude-plugin/marketplace.json",
     ".cursor-plugin/marketplace.json"] — this repo's existing
     .claude-plugin/marketplace.json is already read. A second marketplace file
     would create a second source of truth for zero gain. -->


## Hosted agents — Claude Code on the web, claude.ai

Hosted sessions start with **nothing in `~/.claude`** (no user plugins, skills,
or marketplace adds), and the repo clone is the only thing they arrive with —
but the clone can *write* into `~/.claude`. That is the delivery channel for
ephemeral surfaces. What works where:

- **Claude Code on the web / cloud sessions**: files committed to the repo being
  worked on — `CLAUDE.md`, `AGENTS.md`, `.claude/settings.json`, `.claude/skills/`,
  `.claude/memory/` — are all picked up. Repo-declared `extraKnownMarketplaces` +
  `enabledPlugins` are honored for *local* teammate sessions, but as of 2026-07
  they do **not** install anything in cloud sessions (verified by experiment —
  see [ADR 0001](docs/decisions/0001-consolidate-plugins-into-bundles.md),
  "Experiment evidence"; matches anthropics/claude-code#32606).
- **The bootstrap hook** — [`.claude/hooks/skills-bootstrap.sh`](.claude/hooks/skills-bootstrap.sh),
  armed as a `SessionStart` hook in `.claude/settings.json`. It fetches the
  registry and copies the bundle's skill directories into `~/.claude/skills`, so
  the skills are live for **turn one** of a hosted session and are inherited by
  any subagent that session spawns. A committed hook, not a vendored mirror, is
  therefore all a consumer repo needs (experiment
  [E2](docs/experiments/E2-sessionstart-skill-bootstrap.md), incl. why
  `claude plugin install` is *not* a substitute).
  What it installs is pinned and integrity-checked by
  [`skills.lock`](skills.lock) — registry, an immutable commit SHA, and a sha256
  per skill — because fetching instruction text at session start is a
  supply-chain surface; regenerate it with
  `python3 scripts/generate_skills_lock.py`. The hook is a no-op on a durable
  machine (the marketplace install is authoritative there), it skips any skill
  the project already owns in `.claude/skills/` (personal skills shadow project
  ones), and it always exits 0 — a failure downgrades to a one-line
  `skills: DEGRADED — …` notice naming the knob to fix.
- **claude.ai chat**: skills upload as ZIPs via Settings → Capabilities; the
  [`sync-skills`](plugins/adam-local/skills/sync-skills) skill (in the
  `adam-local` bundle) automates pushing this registry's skills there.
- **Memory**: hosted sessions see a repo's git-tracked `.claude/memory/` (see the
  Memory section in [`STRATEGY.md`](STRATEGY.md) and the
  [portable-memory guide](https://github.com/Adam-S-Daniel/claude-memory-map/blob/main/docs/portable-memory.md);
  migrate existing machine-local stores with the `migrate-claude-memory` plugin).

## Repo layout

```
.claude-plugin/marketplace.json       # marketplace catalog (3 bundles + renames map)
plugins/
  <bundle>/                           # adam | adam-local | fastmail
    plugin.json                       # Agent Plugins 1.0.0 manifest
    .claude-plugin/plugin.json        # Claude Code bundle manifest
    skills/<skill>/SKILL.md           # one dir per skill (+ scripts/, tests/, hooks/)
schemas/                              # vendored Agent Plugins schema (pinned by sha256)
docs/decisions/                       # ADRs (see 0001 for the bundle restructure)
setup.sh                              # link skills into per-agent dirs (non-Claude-Code)
```

Validate the marketplace and any plugin with `claude plugin validate <path>`.

## Global Instructions

I put the following in Claude desktop app -> Settings -> Cowork -> Global instructions 🤞:

> When it seems likely to be beneficial, create/update skills. Follow
> https://agentskills.io/specification and validate with `claude plugin validate`.
> Skills live in bundle plugins: add a new skill as
> `plugins/<bundle>/skills/<skill>/SKILL.md` in the right bundle — `adam` for
> cloud-safe general-purpose skills, `adam-local` for machine-bound ones,
> `fastmail` for Fastmail — no new plugin.json or marketplace entry needed.
> Do **not** use `claude plugin init` — it
> scaffolds into `.claude/skills`, which is not this repo's marketplace layout.
> Never rename skill directories, and never delete or repoint entries in the
> marketplace `renames` map (append-only). Push to `main` in
> https://github.com/Adam-S-Daniel/agentskills. Then fetch and pull in WSL and Windows
> under `~/repos` and `%USERPROFILE%\repos`, and run `bash setup.sh` in both WSL and
> Windows Git Bash so the skills are linked into the standard locations
> (`.agents/skills/`, `.agent/skills/`, `.cursor/skills/`) — Claude Code itself
> uses the marketplace, not `.claude/skills`. Run `/reload-skills` to pick up changes
> without restarting the session.
