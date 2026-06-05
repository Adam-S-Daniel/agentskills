# Skill discovery changes (late May 2026) and what they mean for this repo

*Written 2026-06-05. Subject: recent Claude Code releases that streamline how
skills/plugins are discovered, and the implications for `agentskills` and the
broader "author once, run everywhere" strategy.*

## What actually shipped

A cluster of Claude Code releases over the last ~three weeks changed how skills
are discovered and loaded. The headline item — and the one worth reacting to —
is **automatic loading from `.claude/skills`, with no marketplace required.**

| Version | Date | Change (paraphrased from the changelog) |
| --- | --- | --- |
| 2.1.142 | 2026-05-14 | A plugin with a root-level `SKILL.md` and no `skills/` subdir is now surfaced as a skill. |
| 2.1.152 | 2026-05-27 | Added `/reload-skills` to re-scan skill dirs without restarting. `SessionStart` hooks can return `reloadSkills: true` to make hook-installed skills available in the same session. Skills/commands can set `disallowed-tools` in frontmatter. |
| 2.1.154 | 2026-05-28 | The `/plugin` **Discover** tab now pins plugins whose relevance signals match the current directory, annotated **"suggested for this directory."** Plugins can declare `defaultEnabled: false`. |
| 2.1.157 | 2026-05-29 | **Plugins in `.claude/skills` directories are now automatically loaded, no marketplace required.** Added `claude plugin init <name>` to scaffold a new plugin in `.claude/skills`. |

Two earlier-but-related signals worth keeping in mind:

- **2.1.143** added *projected context cost* (per-turn and per-invocation token
  estimates) to the `/plugin` browse pane, and started showing when a plugin was
  last updated.

The throughline: Anthropic is collapsing the distinction between a "skill" and a
"plugin," making `.claude/skills` the universal drop-zone, and adding
directory-aware discovery so the right skills surface without the user
hand-installing each one.

> Sources: [Claude Code changelog](https://code.claude.com/docs/en/changelog),
> [anthropics/claude-code CHANGELOG](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md),
> [Claude Code release notes (Releasebot)](https://releasebot.io/updates/anthropic/claude-code),
> [Claude release notes (Help Center)](https://support.claude.com/en/articles/12138966-release-notes).

## How this repo works today (for contrast)

`agentskills` is built around the **marketplace** model:

- The repo root is a plugin marketplace (`.claude-plugin/marketplace.json`).
- Each skill is its own plugin: `plugins/<name>/.claude-plugin/plugin.json` +
  `plugins/<name>/skills/<name>/SKILL.md`.
- Claude Code users do `/plugin marketplace add Adam-S-Daniel/agentskills` then
  `/plugin install <name>@agentskills`. Skills are namespaced
  (`/pin-actions-to-sha:pin-actions-to-sha`).
- Non-Claude-Code agents (Codex, Gemini, Cursor) and local use are served by
  `setup.sh`, which symlinks/junctions each skill dir into the per-agent homes —
  including `~/.claude/skills`.

So there are already **three** routes by which one of these skills can reach a
Claude Code session:

1. Marketplace install → namespaced plugin skill.
2. `setup.sh` symlink into `~/.claude/skills` → user-global personal skill.
3. **(New emphasis)** a `.claude/skills` directory auto-loading the skill/plugin
   with no install at all.

## Implications

### 1. The marketplace is no longer the *only* low-friction path — but it's still the best one for distribution

The new auto-load lowers the floor: anyone can vendor a skill into a project's
`.claude/skills/` and every session opened there just has it. That's genuinely
useful for **project-scoped** skills (a skill that only makes sense inside one
repo).

But for a *centralized, personal, cross-machine, cross-agent* library — which is
exactly what this repo is — the marketplace still wins on the things that matter:

- **Versioning + sync:** `git pull` + `/plugin marketplace update agentskills`
  is a clean update story across machines. Loose `.claude/skills` copies drift.
- **Namespacing:** avoids collisions between same-named skills.
- **Discoverability:** the new "suggested for this directory" Discover tab works
  off marketplace relevance signals. Being *in* a marketplace is now an
  advantage for findability, not a tax. This is an argument to **keep and
  enrich** the marketplace, not retire it.

**Recommendation:** keep the marketplace as the canonical distribution channel.
Treat `.claude/skills` auto-load as a complementary quick-path, not a
replacement.

### 2. There's a latent double-loading wrinkle to resolve

`setup.sh` links every skill into `~/.claude/skills`. If a user *also* installs
the marketplace plugin, the same skill can be present twice: once as a namespaced
plugin skill, once as a personal skill in `~/.claude/skills`. With auto-load now
front-and-center, this overlap is more visible and more wasteful (duplicate
descriptions burn context; ambiguous invocation).

**Recommendation — pick a lane for Claude Code specifically:**

- **Option A (cleanest):** drop `~/.claude/skills` from `setup.sh`'s `HOMES`
  list. Let the *marketplace* own Claude Code, and let `setup.sh` own only the
  non-Claude-Code agents (`.agents`, `.agent`, `.gemini`, `.cursor`). The README
  already tells Claude Code users they "don't need this script at all" — this
  makes the code match the docs.
- **Option B:** lean *into* auto-load and treat `~/.claude/skills` as the Claude
  Code path, dropping the marketplace requirement for personal use. Cheaper to
  set up, but loses versioning/namespacing/discovery. Probably the wrong trade
  for a library meant to be shared.

Option A is the better fit for this repo's goals.

### 3. Context budget is now a first-class concern

Anthropic added projected per-turn/per-invocation token costs to the browse pane
and added `defaultEnabled: false`. Read together, the message is: *don't load
every skill into every session.* This repo has 9 plugins today and will grow.

**Recommendations:**

- Consider marking niche/personal plugins (`wj-next-break`, `fastmail`,
  `sync-cc-settings-between-wsl-and-windows`) as `defaultEnabled: false` in their
  `plugin.json` / marketplace entries, so they're installed-but-dormant until
  invoked. Keep broadly useful ones (`adam-writing-style`, `pin-actions-to-sha`,
  `workflow-path-audit`) enabled.
- Keep frontmatter `description` fields tight and trigger-focused — they are the
  text the model reads to decide whether to surface a skill, and they're what the
  "suggested for this directory" matcher keys off of.

### 4. `setup.sh` should pair with `/reload-skills` or `reloadSkills: true`

Today the global-instructions workflow runs `setup.sh` after a push. With 2.1.152,
a freshly-linked skill no longer needs a session restart — `/reload-skills`
re-scans, and a `SessionStart` hook can return `reloadSkills: true`. This dovetails
with the repo's existing `session-start-hook` knowledge.

**Recommendation:** add a note to the README / global instructions that after
`setup.sh`, `/reload-skills` picks up new skills in the current session. Optionally
ship a tiny `SessionStart` hook that runs `setup.sh` and returns
`reloadSkills: true` so a fresh clone self-installs on first session.

### 5. `claude plugin init` changes the authoring shortcut

`claude plugin init <name>` now scaffolds into `.claude/skills`, not into a
marketplace layout. For this repo's plugins/marketplace structure that scaffold is
the *wrong shape* — it would need to be moved under `plugins/<name>/` and a
marketplace entry added by hand.

**Recommendation:** the global instructions already spell out the correct
`plugins/<name>/skills/<name>/` + marketplace-entry layout. Keep using that;
don't switch the authoring flow to `claude plugin init`. It may be worth a one-line
note in the instructions explicitly saying "don't use `claude plugin init` — it
targets `.claude/skills`, which isn't our layout."

### 6. The cross-agent thesis is unaffected (and slightly reinforced)

None of this touches the Agent Skills spec or how Codex/Gemini/Cursor consume
`SKILL.md`. The "author once, run everywhere" bet is intact. If anything, Claude
Code converging on a plain `.claude/skills` drop-zone of `SKILL.md`-bearing dirs
makes Claude Code's behavior *more* like the other agents — the marketplace is now
the value-add layer on top of a substrate that all the agents share.

## Suggested concrete next steps (in priority order)

1. **De-duplicate the Claude Code path:** remove `~/.claude/skills` from
   `setup.sh` (Option A above), so marketplace install and symlink don't collide.
2. **Add context hygiene:** set `defaultEnabled: false` on the niche/personal
   plugins.
3. **Wire up reloads:** document `/reload-skills` after `setup.sh`; optionally add
   a `SessionStart` hook that returns `reloadSkills: true`.
4. **Guardrail the authoring flow:** note in the global instructions that
   `claude plugin init` targets `.claude/skills` and shouldn't be used here.
5. **Leave the marketplace as canonical** — it's now also the thing that powers
   "suggested for this directory" discovery.

None of these are urgent; the repo isn't broken by the change. They're about
aligning with where Claude Code is heading: a shared `.claude/skills` substrate,
directory-aware discovery, and explicit context budgeting — with the marketplace
kept as the distribution and discovery layer on top.
