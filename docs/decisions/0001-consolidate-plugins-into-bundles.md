# 0001. Consolidate single-skill plugins into three bundles

- **Status:** Accepted
- **Date:** 2026-07-16
- **Deciders:** Adam Daniel

## Context

The marketplace carried 16 plugins, each wrapping exactly one skill (except
`fastmail-identities`, which held two). Every new skill meant a new plugin: a
new `plugin.json`, a new marketplace entry, a new install command on every
machine, and one more row of `/plugin` noise. Installing "everything useful"
took 16 `/plugin install` invocations, and there was no grouping that told a
consumer which skills are safe in a headless cloud session versus bound to one
machine's filesystem, browser, or WSL/Windows split.

Two constraints shaped the redesign:

- **Skill directory basenames must not change.** They key `setup.sh`'s
  per-agent symlinks (`~/.agents/skills/<skill>` etc.) and the claude.ai
  skill uploads done by `sync-skills`.
- **Existing installs must migrate.** Users may hold any historical set of the
  16 plugin names and update the marketplace at any time.

## Decision

Group all 17 skills into three bundle plugins, membership decided by where a
skill can run:

- `adam` — usable in a headless cloud session of an arbitrary repo
  (default-enabled).
- `adam-local` — machine-bound / local-resource skills (opt-in).
- `fastmail` — the Fastmail email domain (opt-in).

The 15 retired plugin names map to their bundle via the marketplace `renames`
object (`{"<old>": "<new>"}`), which Claude Code resolves on marketplace
update. That map is **append-only forever** — an entry may never be dropped or
repointed away from a resolvable chain, because a user can update from any old
version.

## Consequences

- One `/plugin install adam@agentskills` gets every cloud-safe skill; new
  skills in an installed bundle arrive on marketplace update with no extra
  install step.
- **Lost per-skill enable granularity.** Enabling `adam-local` is
  all-or-nothing: you cannot install `rename-pdfs` without also getting
  `wj-next-break`. If a skill ever needs independent enablement, it must move
  to its own plugin (via a new `renames` entry).
- **The `renames` map is a permanent, growing artifact.** Every future plugin
  rename or removal adds an entry; none may ever be deleted.
- **One-time reinstall touch per machine — re-run `bash setup.sh` immediately
  after pulling.** Two things break until then: (1) the global sync-skills
  pre-push git hook still points at the old absolute path, so **every
  `git push` from any repo on the machine fails** until setup.sh re-registers
  it; (2) old per-agent symlinks dangle at the old paths — setup.sh detects
  links under `plugins/` whose target vanished and relinks them (foreign
  links are never touched). Claude Code installs migrate via `renames` on
  `/plugin marketplace update agentskills`.
- **Merged plugins keep the survivor's enabled state (first-wins).** A user
  with both `fastmail` and `fastmail-identities` installed keeps `fastmail`'s
  prior enabled/disabled state after migration, so previously-enabled
  identities skills can end up silently disabled — and a version-pinned
  `fastmail` cache lacks the two migrated-in skills until
  `claude plugin update fastmail@agentskills` (plus
  `claude plugin enable fastmail@agentskills` if it was disabled).
- Invocations change from `/<skill>:<skill>` to `/<bundle>:<skill>`
  (e.g. `/adam:pin-actions-to-sha`).

## Alternatives considered

**Keep one plugin per skill.** Rejected: the per-plugin overhead grows
linearly, installs stay O(skills), and the cloud-safe/machine-bound
distinction stays invisible at install time.

**A single `adam` mega-bundle.** Rejected: it forces machine-bound skills
(which need WSL paths, local browsers, PowerShell) into every cloud session
that installs the bundle, where they can only misfire; `defaultEnabled` is
per-plugin, so the split must be at plugin granularity.

**Fleet-wide auto-install via repo-committed settings.** Rejected for now —
disproven by experiment; see "Experiment evidence" below. Local machines get
the marketplace via `setup.sh`/manual add instead, and fleet settings sync is
deferred.

## Experiment evidence

E1, run on `scratch-claude-001` on 2026-07-16, tested whether a consumer repo
can deliver this marketplace's plugins to hosted sessions:

- **(a)** Repo-declared `extraKnownMarketplaces` + `enabledPlugins` in
  committed `.claude/settings.json` do **not** install in claude.ai /
  Claude Code cloud sessions: the probe found an empty
  `installed_plugins.json`, no `known_marketplaces.json`, and the skill not
  loaded. Matches anthropics/claude-code#32606 and #13096.
- **(b)** A `SessionStart` hook shelling `claude plugin marketplace add` +
  `claude plugin install` runs **without a trust prompt** and installs
  successfully (scope: user) — but it executes **after** the session's skill
  registry is built, so the skill never loads in that session. Cloud
  containers are ephemeral, so the install doesn't survive to a next session
  either: the mechanism is unusable there today.

Consequence: bundles optimize the paths that do work — manual
`/plugin install` (one command instead of sixteen) and `setup.sh` symlinks —
rather than assuming settings-driven fleet delivery.

## References

- Restructure commits on `claude/bundle-restructure` (pure-rename commit,
  metadata commit, setup.sh relink commit, this docs commit).
- [`STRATEGY.md`](../../STRATEGY.md) — registry rules and the graduation path.
- [Issue #18](https://github.com/Adam-S-Daniel/agentskills/issues/18) — the
  consolidation plan this restructure extends.
- anthropics/claude-code#32606, anthropics/claude-code#13096 — cloud sessions
  ignoring repo-declared marketplaces/plugins.
