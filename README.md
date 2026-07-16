# agentskills

Adam Daniel's reusable agent skills, packaged as **Claude Code plugins** and as
cross-agent skills that follow the
[Agent Skills specification](https://agentskills.io/specification).

Each skill lives in its own plugin under `plugins/<name>/`, and the repo root is a
Claude Code **plugin marketplace** (`.claude-plugin/marketplace.json`). The exact
same `SKILL.md` files are consumed unchanged by Codex, Gemini, Cursor, and any other
agent that reads the Agent Skills format — so a skill is authored once and installs
everywhere.

This repo is the **canonical upstream registry** for reusable skills. For where
skills live across repos, the public/private rule, and how a skill graduates into
this registry, see [`STRATEGY.md`](STRATEGY.md).

## Install — Claude Code

Add the marketplace once, then install whichever skills you want:

```bash
/plugin marketplace add Adam-S-Daniel/agentskills
/plugin install pin-actions-to-sha@agentskills
/plugin install rename-pdfs@agentskills
# …or browse and pick interactively:
/plugin
```

Plugin skills are namespaced by plugin, e.g. `/pin-actions-to-sha:pin-actions-to-sha`.
Update later with `/plugin marketplace update agentskills`.

Available plugins:

<!-- BEGIN GENERATED PLUGIN TABLE -->
| Plugin | Invocation | Description |
| --- | --- | --- |
| `adam-writing-style` | `/adam-writing-style:adam-writing-style` | Write in Adam Daniel's voice — professional but warm, direct, em-dash-friendly, free of corporate buzzwords. |
| `compare-pdfpairs` | `/compare-pdfpairs:compare-pdfpairs` | Compare pairs of PDFs (name.pdf + name<suffix>.pdf in the same folder) to determine whether they would produce identical printouts and whether their embedded text differs — e.g. |
| `debug-github-workflows` | `/debug-github-workflows:debug-github-workflows` | Debugging GitHub Actions workflow failures. |
| `fastmail` | `/fastmail:fastmail` | Automate Fastmail email workflows via a local browser session. |
| `fastmail-identities` | `/fastmail-identities:add-from-address` | Add one or more email addresses to a Fastmail account as selectable "From" (sending) identities by triggering the add-from-address GitHub Actions workflow in the Adam-S-Daniel/fastmail-actions repo (which does the JMAP work with the FASTMAIL_API_TOKEN repo secret). |
| `fastmail-identities` | `/fastmail-identities:add-received-from-addresses` | Discover which of a Fastmail account's own alias addresses are worth being able to send from, and add them as "From" identities, by triggering the add-received-from-addresses GitHub Actions workflow in the Adam-S-Daniel/fastmail-actions repo (which does the JMAP work with the FASTMAIL_API_TOKEN repo secret). |
| `github-actions-repo-settings` | `/github-actions-repo-settings:github-actions-repo-settings` | Configure and enforce GitHub repository security settings as code: require actions to be pinned to full-length commit SHAs, require approval for all outside collaborators' fork pull-request workflow runs, and protect the default branch via a repository ruleset. |
| `launch-wsl-claude-session` | `/launch-wsl-claude-session:launch-wsl-claude-session` | Launch a detached, interactive Claude Code session inside WSL from a Windows Claude Code session — in a specific repo/folder, optionally remote-controllable and optionally seeded with an initial prompt. |
| `migrate-claude-memory` | `/migrate-claude-memory:migrate-claude-memory` | Inventory, clean up, and migrate Claude Code auto-memory stores found under ~/.claude/projects/<munged-path>/memory/ on this machine. |
| `pin-actions-to-sha` | `/pin-actions-to-sha:pin-actions-to-sha` | Audit and fix GitHub Actions workflow files to ensure every `uses` reference is pinned to a full-length commit SHA (40 hex characters) with a version comment that includes the release date. |
| `rename-pdfs` | `/rename-pdfs:rename-pdfs` | Rename already-searchable PDFs in a specified folder to descriptive, date-prefixed names, proposing each name from the PDF's own content and prompting for per-file confirmation or edit before applying. |
| `review-bash-ci-reliability` | `/review-bash-ci-reliability:review-bash-ci-reliability` | Review bash scripts for CI/CD reliability issues. |
| `sync-cc-settings-between-wsl-and-windows` | `/sync-cc-settings-between-wsl-and-windows:sync-cc-settings-between-wsl-and-windows` | Sync Claude Code settings.json between a Windows home and a WSL home. |
| `sync-skills` | `/sync-skills:sync-skills` | Sync local skill folders from git repos to Claude.ai (and other agent targets) via the upload-skill API. |
| `wj-next-break` | `/wj-next-break:wj-next-break` | Answer questions about the current or next class period, break, passing period, lunch, or bell at Walter Johnson High School (WJ / WJHS, Bethesda MD). |
| `workflow-path-audit` | `/workflow-path-audit:workflow-path-audit` | Audit GitHub Actions workflows for salient-path conditionals — every workflow that triggers on pull_request or push must filter on the files and directories its steps actually depend on, and skip with success when nothing salient changed. |
| `writing-adrs` | `/writing-adrs:writing-adrs` | Write a lightweight Nygard-style Architecture Decision Record under `docs/decisions/` when a non-obvious decision needs context that won't fit in a code comment and would rot if left only in a PR description. |
<!-- END GENERATED PLUGIN TABLE -->

## Install — Codex, Gemini, Cursor, and local use

These tools discover skills from per-agent directories rather than a marketplace.
Run `setup.sh` once **in each environment** (Windows Git Bash *and* WSL — they have
separate `$HOME`s):

```bash
bash setup.sh
```

It links every skill under `plugins/*/skills/*` into the standard skill homes:

- `~/.agents/skills/` — Codex (and the generic agents dir)
- `~/.agent/skills/`
- `~/.gemini/skills/`, `~/.gemini/antigravity/skills/`
- `~/.cursor/skills/`

**Claude Code is deliberately not in that list** — it's served by the marketplace
above. Linking the same skills into `~/.claude/skills` too would double-load them
(once as a namespaced plugin, once as a personal skill), so `setup.sh` now removes
any such links it created in earlier versions. Background and rationale:
[`docs/2026-06-05-skill-discovery-and-centralized-strategy.md`](docs/2026-06-05-skill-discovery-and-centralized-strategy.md).

On Windows it uses directory junctions (`mklink /J`) — no admin required. The script
is idempotent and migrates the old whole-directory links left by earlier versions.

After running `setup.sh`, you don't need to restart an open Claude Code session —
run `/reload-skills` to re-scan the skill directories in place.

> Codex reads `~/.agents/skills`; that link is what makes these skills available in
> Codex. See the [Codex skills docs](https://developers.openai.com/codex/skills).

## Hosted agents — Claude Code on the web, claude.ai

Hosted sessions get **nothing from `~/.claude`** (no user plugins, skills, or
marketplace adds) — the repo clone is the only channel. What works where:

- **Claude Code on the web / cloud sessions**: files committed to the repo being
  worked on — `CLAUDE.md`, `AGENTS.md`, `.claude/settings.json`, `.claude/skills/`,
  `.claude/memory/` — are all picked up. A consumer repo can auto-offer this
  marketplace's plugins to hosted (and teammate) sessions by declaring
  `extraKnownMarketplaces` + `enabledPlugins` in its committed
  `.claude/settings.json` (users get a consent prompt).
- **claude.ai chat**: skills upload as ZIPs via Settings → Capabilities; the
  [`sync-skills`](plugins/sync-skills) plugin automates pushing this registry's
  skills there.
- **Memory**: hosted sessions see a repo's git-tracked `.claude/memory/` (see the
  Memory section in [`STRATEGY.md`](STRATEGY.md) and the
  [portable-memory guide](https://github.com/Adam-S-Daniel/claude-memory-map/blob/main/docs/portable-memory.md);
  migrate existing machine-local stores with the `migrate-claude-memory` plugin).

## Repo layout

```
.claude-plugin/marketplace.json       # marketplace catalog (lists every plugin)
plugins/
  <name>/
    .claude-plugin/plugin.json        # plugin manifest
    skills/<name>/SKILL.md            # the skill (+ scripts/, tests/, hooks/ as needed)
setup.sh                              # link skills into per-agent dirs (non-Claude-Code)
```

Validate the marketplace and any plugin with `claude plugin validate <path>`.

## Global Instructions

I put the following in Claude desktop app -> Settings -> Cowork -> Global instructions 🤞:

> When it seems likely to be beneficial, create/update skills. Follow
> https://agentskills.io/specification and validate with `claude plugin validate`.
> Each skill is a Claude Code plugin: add it under
> `plugins/<name>/skills/<name>/SKILL.md` with a
> `plugins/<name>/.claude-plugin/plugin.json` manifest, and add a matching entry to
> `.claude-plugin/marketplace.json`. Do **not** use `claude plugin init` — it
> scaffolds into `.claude/skills`, which is not this repo's marketplace layout.
> Mark niche/personal plugins `"defaultEnabled": false` in their marketplace entry
> so they stay dormant until invoked. Push to `main` in
> https://github.com/Adam-S-Daniel/agentskills. Then fetch and pull in WSL and Windows
> under `~/repos` and `%USERPROFILE%\repos`, and run `bash setup.sh` in both WSL and
> Windows Git Bash so the skills are linked into the standard locations
> (`.agents/skills/`, `.gemini/skills/`, `.cursor/skills/`, etc.) — Claude Code itself
> uses the marketplace, not `.claude/skills`. Run `/reload-skills` to pick up changes
> without restarting the session.
