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

| Plugin | What it does |
| --- | --- |
| `adam-writing-style` | Write in Adam Daniel's voice |
| `fastmail` | Automate Fastmail via a local Claude-in-Chrome session |
| `github-actions-repo-settings` | Enforce GitHub Actions security settings |
| `pin-actions-to-sha` | Pin Actions `uses:` refs to full commit SHAs |
| `rename-pdfs` | Rename searchable PDFs from their own content |
| `sync-cc-settings-between-wsl-and-windows` | Reconcile Claude Code settings across WSL/Windows |
| `sync-skills` | Sync skill folders to claude.ai |
| `wj-next-break` | Walter Johnson HS bell schedule |
| `workflow-path-audit` | Audit workflows for salient-path conditionals |

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
