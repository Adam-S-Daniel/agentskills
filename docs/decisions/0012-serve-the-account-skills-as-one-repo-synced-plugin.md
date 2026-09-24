# 0012. Serve the account's skills as one repo-synced plugin

- **Status:** Proposed
- **Date:** 2026-09-23
- **Deciders:** Adam Daniel

## Context

The claude.ai account carries the skills `account-skills.txt` declares
([ADR 0002](0002-limit-account-store-to-repo-independent-skills.md)). They get
there as ZIP uploads, one skill at a time, through `sync-skills` and a signed-in
browser. The upload path has no delete, so ADR 0002 calls uploading "close to a
one-way door". The drift loop that notices a stale upload
([ADR 0006](0006-drive-the-account-store-drift-loop-from-one-published-artifact.md))
exists because nothing else can see the account.

Experiment E6
([`docs/experiments/E6-account-plugin-channel.md`](../experiments/E6-account-plugin-channel.md),
[#160](https://github.com/Adam-S-Daniel/agentskills/issues/160)) tested the
plugin channel on 2026-09-23 with two throwaway probes. What each one showed
matters here, because they are not interchangeable:

- **The repo-synced probe** (a plugin in the public repo
  `Adam-S-Daniel/e6-probe-marketplace`, added as a personal marketplace) was
  checked in **local Desktop Cowork**, **claude.ai chat**, one more surface E6
  does not name, and the laptop's **Claude Code terminal** (E6 §3.6). Local
  Cowork got it only after it was enabled a second time in the Desktop app's
  own plugin settings: the marketplace list is shared, enabling is per app.
  The terminal downloaded it into `~/.claude/plugins/synced/` too, which the
  uploaded probe never reached (E6 §3.5–§3.6).
- **The uploaded probe** (a plugin zip uploaded on claude.ai) reached iOS,
  Claude in Chrome, claude.ai chat and Cowork, and the Desktop Chat tab, but
  **not** local Cowork: personal uploads live in the hidden `My Uploads`
  marketplace, which the Desktop app doesn't list (E6 §3.5). Local Cowork is
  where the PDF skills are used. iOS and Chrome were also shown to load
  Anthropic's own marketplace plugins (E6 §3.4).
- **Deletion was measured only for the uploaded probe**: deleting it on
  claude.ai removed it from iOS (E6 §3.5). Removing a repo-synced plugin was
  not measured, and the laptop's synced manifest still listed `e6-probe` after
  the marketplace was removed on claude.ai (E6 §3.6).
- **Updates arrived only by hand** for the repo-synced probe. No automatic sync
  delivered a push within 3½ hours. What worked: push, press "Check for
  updates" on the marketplace at claude.ai (it reported "Updated to <sha>"),
  then fully restart the Desktop app (E6 §5 step 5). Whether "Check for
  updates" honours the entry's `version` is unknown: the probe's version was
  bumped with its content, and the terminal's synced manifest records
  claude.ai's own counter (`"0001"`, `"0041"`), not the semver.
- **This repo is accepted as a personal marketplace**, and adding a
  marketplace enables none of its plugins (E6 §3.5–§3.6).

None of the three bundles matches the account: enabling them would add 12
skills the account deliberately does not carry (E6 §3.2).

## Decision

Serve the account's skills as one plugin, `adam-personal`, defined entirely by
its entry in `.claude-plugin/marketplace.json`: `"source": "./"`,
`"strict": false`, and a `skills` list pointing at the existing skill
directories in place. That is the pattern Claude Code's documentation gives for
several entries sharing one tree
([plugin marketplaces](https://code.claude.com/docs/en/plugin-marketplaces):
"list specific subdirectories instead so each entry loads only its own
skills"; with a marketplace-root source "the listed paths are the complete set
for that entry"). **It is Claude Code's documentation only.** How claude.ai's
server-side import and the Desktop app treat a `./` + `strict: false` +
`skills` entry is unmeasured, which is why phase 2 starts with a shape probe.

`account-skills.txt` stays the single declaration of what the account carries;
CI holds the entry equal to it. Durable machines turn the plugin off in their
terminals (`"adam-personal@synced": false`, converged by `setup.sh`). The owner
adds this repo as a personal marketplace and enables only `adam-personal`, on
claude.ai and in the Desktop app.

## Rollout

1. **Phase 1 — this change.** The entry; the checks that hold it to
   `account-skills.txt`, to a closed set of keys, to an empty repository root
   and to the version rule; `setup.sh`'s terminal opt-out; this ADR. Nothing
   changes on the account.
2. **Phase 2 — the owner, in this order.** Every "not offered" below counts
   only if a known control is offered on the same surface in the same sitting
   (E6 §5's rule).
   0. **Shape probe first (prerequisite).** Before anything touches the owner's
      account, the exact `adam-personal` shape (a `./` source, `strict: false`,
      a `skills` list naming some directories of a tree that holds others) is
      probed in a throwaway repo. Its result must show that **only the listed
      skills** load on claude.ai, in local Cowork and in a terminal. If
      anything unlisted loads, stop: this decision's premise is wrong.
   1. Run `bash setup.sh` on each durable machine, so
      `"adam-personal@synced": false` is in place **before** the plugin is
      enabled anywhere.
   2. Add `Adam-S-Daniel/agentskills` at
      [claude.ai/customize/plugins](https://claude.ai/customize/plugins);
      enable only `adam-personal` there and in the Desktop app's plugin
      settings.
   3. On each surface (claude.ai chat and Cowork, local Cowork, Desktop Chat,
      Chrome, iOS), with a control: a listed skill is offered as
      `adam-personal:<name>` (for example `adam-personal:rename-pdfs`); an
      **unlisted** skill (for example `adam-personal:debug-github-workflows`)
      is **not** offered; and none of `adam`, `adam-local`, `fastmail` or
      `cms-platform` is enabled anywhere. The uploaded ZIP copies still exist
      in this phase, so each account skill appears twice; that is expected.
   4. A terminal on a durable machine, after a fresh launch: `claude plugin
      list --json` shows `adam-personal@synced` as disabled.
   5. **Removal.** Disable `adam-personal` on claude.ai and confirm it
      disappears from one surface and from the terminal's synced bucket
      (`~/.claude/plugins/synced/`); then re-enable it. This measurement, not
      E6's deletion of the uploaded probe, is what phase 3 rests on.
3. **Phase 3 — the owner, then a follow-up change.** Delete the uploaded ZIP
   skills on claude.ai. A follow-up change then retires `sync-skills`' upload
   path and replaces ADR 0006's drift loop with comparing the commit claude.ai
   reports ("Updated to <sha>") against `main`. That change must keep
   `sync_skills.py`'s `load_account_declaration()`, or move the parser and
   repoint `check_consistency.py`, which reads `account-skills.txt` with it
   (it fails with a named error, not a crash, if the reader is gone). This ADR
   then moves to Accepted, and ADR 0002's "Uploading is close to a one-way
   door" consequence is marked superseded — on the strength of phase 2
   step 5.

## Consequences

- **Every release is three manual steps.** Bump the entry's `version`, press
  "Check for updates" on claude.ai, and restart the Desktop app. E6 saw no
  automatic update in 3½ hours. The bump is required conservatively:
  `claude plugin update` gates on `version` (ADR 0009), but whether claude.ai's
  "Check for updates" does is unknown (its synced manifest records its own
  counter). `check_plugin_versions.py` requires the bump whenever a listed
  skill's directory, the list, or any non-display key of the entry changes
  against the PR base. A skill that also lives in a bundle needs that bundle's
  bump too, so one skill edit can mean two bumps.
- **Terminals would receive it — measured — so durable machines opt out.** A
  repo-synced plugin reached the laptop's terminal in E6 (§3.6), and Claude
  Code dedupes a synced plugin only against an installed plugin *of the same
  name*; `adam-personal` matches no bundle. Enabled, it would load 8 of its 9
  skills a second time beside `adam@agentskills` and `adam-local@agentskills`,
  and bring back the account copy of `sync-skills` that ADR 0010 took off
  terminals. `setup.sh` therefore converges `"adam-personal@synced": false` in
  user `enabledPlugins`, the documented per-plugin off switch
  ([synced plugins](https://code.claude.com/docs/en/plugins-reference#synced-plugins)).
- **Cloud sessions cannot opt out at user level.** They sync the account's
  plugins into the session and have no user settings `setup.sh` can reach. The
  only lever is a repo's committed `.claude/settings.json` with
  `"adam-personal@synced": false`, one repo at a time. After phase 3 a cloud
  session carries the same skills it carries today as uploaded account skills,
  so it is at parity; during phase 2 it carries both copies.
- **The plugin cache holds the whole repository.** The source is the root, so
  each installed version copies all of it (about 3.3 MB of tracked files at
  `3b4cfff`), not just nine skill directories. Inside that snapshot
  `sync_skills.py`'s `_registry_root()` resolves to the cached copy, so an
  `account-state.json` read or write from the cached `sync-skills` lands in the
  cache, not the checkout. Acceptable, because the uploader is being retired
  (phase 3); nothing is changed for it.
- **The repo root must stay free of plugin components.** The plugin root is
  the repository root. The docs give `skills` a marketplace-root exception but
  do not say `strict: false` stops default discovery of `hooks/`, `.mcp.json`,
  `agents/`, `bin/`, `settings.json` and the rest, so `check_consistency.py`
  fails if any default component location exists at the root. None does today.
- **The entry's keys are closed.** A `strict: false` entry is the plugin's
  whole definition, so `check_consistency.py` refuses any key outside display
  fields, `name`, `source`, `strict`, `version`, `defaultEnabled` and `skills`.
  Adding a hook or an MCP server to the account plugin needs this check
  changed on purpose.
- **Duplicates during the transition.** Between phases 2 and 3 each account
  skill appears twice, once uploaded and once as `adam-personal:<name>`, and
  its description is paid twice on every surface the account reaches.
- **The three bundles become available on the account.** Adding the
  marketplace lists `adam`, `adam-local`, `fastmail` and `cms-platform` too.
  They must stay un-enabled: enabling them brings the 12-skill overshoot and
  the repo-scoped skills ADR 0002 keeps off the account (E6 §3.2).
- **The name is effectively permanent once enabled.** Renaming
  `adam-personal` would need an entry in the append-only `renames` map
  (ADR 0001), and every app that enabled it would follow that map or lose it.
- **One more place to keep in step.** Adding a skill to the account is now a
  line in `account-skills.txt`, a path in the entry and a version bump.
  `check_consistency.py` fails until the first two agree.

## Alternatives considered

- **Move the skills into a new `plugins/adam-personal/skills/` bundle.** Skill
  moves between bundles touch the append-only `renames` map, and it would pull
  `adam-writing-style`, `finding-unknowns` and `writing-adrs` out of the `adam`
  bundle that ten repos install through `skills.lock`. Rejected: the curated
  entry serves the same directories with no move.
- **Upload a plugin file on claude.ai.** It lands in `My Uploads`, which
  doesn't reach local Cowork (E6 §3.5). Rejected: it misses the surface the PDF
  skills are for.
- **Enable the existing bundles on the account.** 12 extra skills, about 2,970
  always-on tokens on every surface, including the CI skills ADR 0002 rejected
  (E6 §3.2). Rejected.
- **A separate personal repo as the marketplace.** A second registry holding
  copies of skills this one owns, kept in sync by hand or by another workflow.
  Rejected: it recreates the unversioned-copy problem ADR 0002 exists to
  prevent.
- **Symlinked skill directories under a new plugin root.** ADR 0008 refuses
  symlinks in a skill directory, and the digests and zips here would not follow
  them. Rejected.

## How to verify

- `scripts/check_consistency.py` (CI job `consistency`) fails unless
  `adam-personal` has `"source": "./"`, `"strict": false`,
  `"defaultEnabled": false`, a `version` and no key outside the allowlist;
  every listed path is `./plugins/<bundle>/skills/<skill>` with a `SKILL.md`;
  the list equals the names in `account-skills.txt` resolved to their bundles;
  and no default plugin component exists at the repository root. Tests in
  `scripts/test_check_consistency.py`, including
  `test_the_account_plugin_serves_exactly_the_declared_account_skills` (which
  parses both files independently of the checker),
  `test_a_curated_entry_carrying_a_non_allowlisted_key_is_reported` and
  `test_a_root_component_beside_a_curated_entry_is_reported`.
- `scripts/check_plugin_versions.py` fails a PR that changes a listed skill,
  the list, or a non-display key without raising the entry's `version`
  (`test_curated_skill_edited_without_entry_bump_fails`,
  `test_curated_skills_list_changed_without_bump_fails`,
  `test_curated_entry_definition_changed_without_bump_fails` in
  `scripts/test_check_plugin_versions.py`).
- `setup.sh` converges `"adam-personal@synced": false`
  (`test_the_account_plugin_is_off_in_terminals` in
  `scripts/test_setup_settings_convergence.py`, which parses the JSON).
- `claude plugin validate . --strict` (CI job `plugin-validate`) accepts the
  entry.

## References

- [E6](../experiments/E6-account-plugin-channel.md) and
  [#160](https://github.com/Adam-S-Daniel/agentskills/issues/160) — the
  measurements this rests on
- [ADR 0001](0001-consolidate-plugins-into-bundles.md) — bundles and the
  append-only `renames` map
- [ADR 0002](0002-limit-account-store-to-repo-independent-skills.md) — what the
  account carries, and the one-way-door consequence phase 3 supersedes
- [ADR 0006](0006-drive-the-account-store-drift-loop-from-one-published-artifact.md)
  — the drift loop phase 3 replaces
- [ADR 0008](0008-refuse-symlinks-in-a-skill-directory.md) — no symlinks
- [ADR 0009](0009-bump-bundle-versions-on-every-release.md) — version bumps
- [ADR 0010](0010-let-pinned-channels-own-the-terminal.md) — terminals take
  skills from the pinned bundles
- Claude Code docs: [plugin marketplaces, strict mode and `skills`
  paths](https://code.claude.com/docs/en/plugin-marketplaces),
  [synced plugins](https://code.claude.com/docs/en/plugins-reference#synced-plugins),
  [file locations](https://code.claude.com/docs/en/plugins-reference#file-locations-reference),
  [version management](https://code.claude.com/docs/en/plugins-reference#version-management)
