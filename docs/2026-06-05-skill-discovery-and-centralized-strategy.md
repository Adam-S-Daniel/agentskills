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

## Decisions taken (2026-06-05)

All of the recommendations above were adopted and implemented in the same change
that added this document. Status:

1. **De-duplicated the Claude Code path — DONE.** `~/.claude/skills` was removed
   from `setup.sh`'s `HOMES` list (Option A), so the marketplace is the single
   Claude Code channel. `setup.sh` also gained a `dedup_claude_code_dir` migration
   that removes links *earlier* versions of the script created in
   `~/.claude/skills` — but only links that point back into this repo (plus a
   legacy whole-directory link). Personal skills the user keeps there are left
   untouched. The migration is idempotent and was verified on a simulated legacy
   link.
2. **Context hygiene — DONE.** `wj-next-break`, `fastmail`, and
   `sync-cc-settings-between-wsl-and-windows` are now `"defaultEnabled": false` in
   `.claude-plugin/marketplace.json`. They install but stay dormant until invoked.
   The broadly-useful plugins remain enabled by default.
3. **Wire up reloads — DONE as documentation; SessionStart hook intentionally
   omitted.** The README and the global instructions now tell you to run
   `/reload-skills` after `setup.sh` instead of restarting. The *optional*
   `SessionStart` hook returning `reloadSkills: true` was **not** added, and that
   is a deliberate consequence of decision #1: now that Claude Code is served by
   the marketplace and `setup.sh` no longer links into `~/.claude/skills`, a
   `reloadSkills` hook would have nothing new to surface to Claude Code — it would
   be a no-op. (`reloadSkills` only affects Claude Code's own skill scan, and the
   only scanned dir we used to touch was `~/.claude/skills`.) Shipping a no-op hook
   that runs in every contributor's session — and that would either do nothing or,
   if it ran `setup.sh`, mutate their `$HOME` and install a git hook without
   consent — is worse than not shipping it. If a future change reintroduces a
   `~/.claude/skills` (or project `.claude/skills`) drop-zone, revisit this.
4. **Guardrail the authoring flow — DONE.** The global instructions in the README
   now say explicitly not to use `claude plugin init` (it scaffolds into
   `.claude/skills`, not this repo's `plugins/<name>/` marketplace layout) and to
   mark niche plugins `defaultEnabled: false`.
5. **Marketplace stays canonical — DONE (decision recorded).** No code change
   beyond the above; the `setup.sh` header and README now state the rule plainly.
   The marketplace is what powers the new "suggested for this directory" discovery,
   so keeping it is an asset, not legacy weight.

The net effect: one clear Claude Code channel (the marketplace), a leaner default
context footprint, no-restart reloads, and a documented authoring guardrail —
aligned with where Claude Code is heading (a shared skills substrate,
directory-aware discovery, explicit context budgeting) while keeping the
marketplace as the distribution and discovery layer on top.
