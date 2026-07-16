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
| `adam-writing-style` | `/adam-writing-style:adam-writing-style` | Write in Adam Daniel's voice — professional but warm, direct, em-dash-friendly, free of corporate buzzwords. Trigger whenever the user asks Claude to write something that will go out under Adam's name or be lifted into his materials: emails, replies, bios, proposal blurbs, cover letters, LinkedIn posts, performance self-appraisals, comments on other people's drafts, "ghostwrite this for me", or any "in my voice / sound like me" request. Also trigger when polishing or rewriting Adam's own draft. Do NOT trigger for generic third-party content the user is helping someone else produce (e.g. "draft a press release for the company") unless they ask for Adam's voice specifically. |
| `compare-pdfpairs` | `/compare-pdfpairs:compare-pdfpairs` | Compare pairs of PDFs (name.pdf + name<suffix>.pdf in the same folder) to determine whether they would produce identical printouts and whether their embedded text differs — e.g. to safely delete redundant "-signed" or "-needsocr" duplicates. Recursively finds every pair under a directory, rasterizes pages and compares hashes, and diffs extracted text. Triggers: "compare pdf pairs", "find duplicate pdfs", "are these pdfs identical", "which suffixed pdfs can I delete", "dedupe scanned pdfs". |
| `debug-github-workflows` | `/debug-github-workflows:debug-github-workflows` | Debugging GitHub Actions workflow failures. Use when workflows are failing, showing unexpected results, or when you need to read workflow run logs and diagnose CI/CD issues. |
| `fastmail` | `/fastmail:fastmail` | Automate Fastmail email workflows via a local browser session. Use this skill ONLY when running on Adam's computer with access to his browser (e.g. via Claude desktop / Cowork mode with Claude in Chrome). Do NOT use in headless environments such as the Claude Code CLI, CI pipelines, or any context without an interactive browser available. Trigger when the user wants to: search Fastmail for emails by sender, subject, or keyword; read email threads or attachment contents (including spreadsheets); compose and send new messages; or draft and send replies. Trigger on any mention of "Fastmail", "check my email", "search my inbox", "reply to", or similar email-management requests. |
| `fastmail-identities` | `/fastmail-identities:add-from-address` | Add one or more email addresses to a Fastmail account as selectable "From" (sending) identities by triggering the add-from-address GitHub Actions workflow in the Adam-S-Daniel/fastmail-actions repo (which does the JMAP work with the FASTMAIL_API_TOKEN repo secret). Trigger when the user wants to "add a from address", "add a sending identity", "let me send as X", "add an alias I can send from", or "register a new From address in Fastmail". Supports a dry-run (whatif) preview. For discovering which received alias addresses are worth adding, use the add-received-from-addresses skill instead. |
| `fastmail-identities` | `/fastmail-identities:add-received-from-addresses` | Discover which of a Fastmail account's own alias addresses are worth being able to send from, and add them as "From" identities, by triggering the add-received-from-addresses GitHub Actions workflow in the Adam-S-Daniel/fastmail-actions repo (which does the JMAP work with the FASTMAIL_API_TOKEN repo secret). It scans every message for distinct X-Delivered-To addresses, keeps only those you actually correspond through, drops any that are already identities, and adds the rest. Trigger when the user wants to "add From addresses for aliases I actually use", "find alias addresses worth sending from", or "set up identities for the addresses that receive my mail". Supports a dry-run (whatif) preview. To add a specific known address, use the add-from-address skill instead. |
| `github-actions-repo-settings` | `/github-actions-repo-settings:github-actions-repo-settings` | Configure and enforce GitHub repository security settings as code: require actions to be pinned to full-length commit SHAs, require approval for all outside collaborators' fork pull-request workflow runs, and protect the default branch via a repository ruleset. Includes a generate/diff/apply engine (introspect current state -> emit YAML; detect drift; apply desired state) and a central fan-out workflow to enforce a baseline across many repos. Trigger when: setting up a new repo, running a security audit, onboarding a repo to org standards, enforcing settings across a fleet, or when asked to configure or harden Actions security settings. Trigger on mentions of "actions settings", "repo security settings", "repo settings as code", "settings drift", "fork approval", "outside collaborators", "actions policy", "branch protection", "ruleset", or "harden repo". |
| `launch-wsl-claude-session` | `/launch-wsl-claude-session:launch-wsl-claude-session` | Launch a detached, interactive Claude Code session inside WSL from a Windows Claude Code session — in a specific repo/folder, optionally remote-controllable and optionally seeded with an initial prompt. Use this WHENEVER the user wants to open / launch / spawn / start / fire off a separate (or background, detached, standalone, "and ignore it") Claude session in WSL, in a given directory, especially so it shows up in their remote Claude sessions list, even if they don't name every detail. It handles the Windows→WSL launch quirks that silently break naive attempts: ConPTY via Windows Terminal, PowerShell path passing, session-id vs initial-prompt openers, prompt quoting, and the workspace-trust gate. |
| `migrate-claude-memory` | `/migrate-claude-memory:migrate-claude-memory` | Inventory, clean up, and migrate Claude Code auto-memory stores found under ~/.claude/projects/<munged-path>/memory/ on this machine. Use this skill to list every memory store with its decoded project path, file count, size, and freshness; to identify ORPHANED stores whose original workspace no longer exists (so a human can review and delete them); and to migrate a chosen store into a repo's git-tracked .claude/memory/ directory so the memory travels with the repo across machines and is visible to hosted/cloud Claude sessions. Trigger on requests like "clean up claude memory", "migrate claude memory", "inventory memory stores", "orphaned memory", "sync memory across machines", "make memory portable", or any mention of `~/.claude/projects` or `autoMemoryDirectory`. LOCAL-ONLY: this skill reads and writes files under this machine's `~/.claude` directory and CANNOT run in a hosted/cloud Claude session that has no local `~/.claude` on disk — do not invoke it there. |
| `pin-actions-to-sha` | `/pin-actions-to-sha:pin-actions-to-sha` | Audit and fix GitHub Actions workflow files to ensure every `uses` reference is pinned to a full-length commit SHA (40 hex characters) with a version comment that includes the release date. Enforces a 7-day cooling-off period before adopting new releases. Trigger when: creating or modifying GitHub Actions workflows, running security audits, reviewing pull requests that touch workflow files, or when asked to pin, audit, or harden actions. Trigger on mentions of "pin actions", "SHA pinning", "actions security", "supply chain", or "harden workflows". |
| `rename-pdfs` | `/rename-pdfs:rename-pdfs` | Rename already-searchable PDFs in a specified folder to descriptive, date-prefixed names, proposing each name from the PDF's own content and prompting for per-file confirmation or edit before applying. Use after running `ocr-pdfs` to clean up scanner-output filenames like "Scan from 2024-03-15.pdf", "QuickScan_001.pdf", or "Document(47).pdf" — or for any folder of PDFs that already have text layers but unhelpful filenames. Triggers: "rename my pdfs", "clean up pdf filenames", "rename searchable pdfs", "give my pdfs descriptive names", "rename scanned pdfs", "tidy pdf names". |
| `review-bash-ci-reliability` | `/review-bash-ci-reliability:review-bash-ci-reliability` | Review bash scripts for CI/CD reliability issues. Use when writing or reviewing shell scripts that run in GitHub Actions or other CI environments to catch silent failure patterns, missing error propagation, and environment assumptions. |
| `sync-cc-settings-between-wsl-and-windows` | `/sync-cc-settings-between-wsl-and-windows:sync-cc-settings-between-wsl-and-windows` | Sync Claude Code settings.json between a Windows home and a WSL home. Triggers on requests to "sync Claude Code settings", "merge my settings.json", "keep WSL and Windows Claude settings in sync", or mentions of reconciling %USERPROFILE%\.claude\settings.json with ~/.claude/settings.json in WSL. Backs up both files with an Eastern-time-stamped prefix, then merges per property (union for permissions.allow/deny and spinnerVerbs.verbs; more-recently-modified-wins with optional prompt for scalars like theme, model, effortLevel, tui, verbose, etc.; statusLine and defaultShell are kept per-file). Preserves each file's native newline format (CRLF for Windows, LF for WSL) and UTF-8 BOM presence. Windows-only (needs PowerShell 7+ and access to the WSL UNC share, e.g. \\wsl.localhost\Ubuntu\...). Use when the user wants the two settings.json files reconciled, not when they want a single file edited in place. |
| `sync-skills` | `/sync-skills:sync-skills` | Sync local skill folders from git repos to Claude.ai (and other agent targets) via the upload-skill API. Trigger when the user says "sync skills", "push skills to Claude", "upload skill", or after editing SKILL.md files locally. Requires a claude.ai tab open in Chrome (uses browser session cookies via javascript_tool). Works on Adam's computer where the agentskills repos live under ~/repos/ or %USERPROFILE%\repos\. |
| `wj-next-break` | `/wj-next-break:wj-next-break` | Answer questions about the current or next class period, break, passing period, lunch, or bell at Walter Johnson High School (WJ / WJHS, Bethesda MD). Use whenever the user asks about Walter Johnson's schedule — "when does lunch end", "what period is it right now", "when's the next break", "is school on today", "what time do classes end" — or mentions WJ bells, periods, or dismissal. Knows all six WJ bell-schedule variants (regular, early dismissal, 2-hour delay, homeroom, two assembly variants) plus spring testing-day adjusted schedules, and relies on the agent's judgment — informed by the user's subscribed WJHS calendar, web search, and other context — to pick which one applies today. |
| `workflow-path-audit` | `/workflow-path-audit:workflow-path-audit` | Audit GitHub Actions workflows for salient-path conditionals — every workflow that triggers on pull_request or push must filter on the files and directories its steps actually depend on, and skip with success when nothing salient changed. Use when adding a new workflow, modifying an existing one's steps, renaming/moving files a workflow depends on, or when a CI bill spike points at workflows running on irrelevant changes. |
| `writing-adrs` | `/writing-adrs:writing-adrs` | Write a lightweight Nygard-style Architecture Decision Record under `docs/decisions/` when a non-obvious decision needs context that won't fit in a code comment and would rot if left only in a PR description. Trigger when the user asks "should I document this", "add an ADR", "why did we do X" referring to a past choice with no comment trail, or when you find yourself drafting a multi-paragraph PR description justifying a one-line change. Also covers the bootstrap case (creating `docs/decisions/README.md` + the first ADR from scratch) if the folder doesn't exist yet. |
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
