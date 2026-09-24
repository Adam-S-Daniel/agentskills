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
[#160](https://github.com/Adam-S-Daniel/agentskills/issues/160)) measured the
plugin channel on 2026-09-23:

- **A plugin from a repo-synced personal marketplace reaches every surface the
  uploads serve**: iOS, Claude in Chrome, claude.ai chat and Cowork, the Desktop
  Chat tab, and local Desktop Cowork (E6 §3.4–§3.6).
- **An uploaded plugin does not reach local Cowork.** Personal uploads live in
  the hidden `My Uploads` marketplace, which the Desktop app doesn't list
  (E6 §3.5). Local Cowork is where the PDF skills are used.
- **It can be deleted.** Deleting the probe on claude.ai removed it from iOS
  (E6 §3.5).
- **It updates only by hand.** No automatic sync delivered a push within 3½
  hours. What worked: push, press "Check for updates" on the marketplace at
  claude.ai (it reported "Updated to <sha>"), then fully restart the Desktop
  app (E6 §5 step 5).
- **Enabling is per app.** The marketplace list is shared, but a plugin has to
  be enabled once on claude.ai and once in the Desktop app (E6 §3.6).
- **This repo is accepted as a personal marketplace**, and adding a
  marketplace enables none of its plugins (E6 §3.5–§3.6).

None of the three bundles matches the account: enabling them would add 12
skills the account deliberately does not carry (E6 §3.2).

## Decision

Serve the account's skills as one plugin, `adam-personal`, defined entirely by
its entry in `.claude-plugin/marketplace.json`: `"source": "./"`,
`"strict": false`, and a `skills` list pointing at the existing skill
directories in place. That is the documented pattern for several entries
sharing one tree ([plugin marketplaces](https://code.claude.com/docs/en/plugin-marketplaces):
"list specific subdirectories instead so each entry loads only its own
skills"; with a marketplace-root source "the listed paths are the complete set
for that entry"). `account-skills.txt` stays the single declaration of what the
account carries; CI holds the entry equal to it.

The owner adds this repo as a personal marketplace and enables only
`adam-personal`, on claude.ai and in the Desktop app.

## Rollout

1. **Phase 1 — this change.** The entry, the checks that hold it to
   `account-skills.txt` and to the version rule, and this ADR. Nothing changes
   on the account.
2. **Phase 2 — the owner.** Add `Adam-S-Daniel/agentskills` at
   [claude.ai/customize/plugins](https://claude.ai/customize/plugins); enable
   only `adam-personal` there and in the Desktop app's plugin settings. Check
   that each surface invokes a skill through it, for example
   `adam-personal:rename-pdfs` in local Cowork, with a known control found in
   the same sitting (E6 §5's rule). The uploaded ZIP copies still exist during
   this phase, so both copies show up; that is expected. Also check whether a
   Claude Code terminal receives the plugin as `adam-personal@synced`
   (`claude plugin list --json`). If it does, the follow-up is adding
   `"adam-personal@synced": false` to `setup.sh`'s convergence, so terminals
   keep getting these skills from the pinned bundles (ADR 0010). That is not
   part of this change.
3. **Phase 3 — the owner, then a follow-up change.** Delete the uploaded ZIP
   skills on claude.ai. A follow-up change then retires `sync-skills`' upload
   path and replaces ADR 0006's drift loop with comparing the commit claude.ai
   reports ("Updated to <sha>") against `main`. This ADR moves to Accepted, and
   ADR 0002's "Uploading is close to a one-way door" consequence is marked
   superseded.

## Consequences

- **Every release is three manual steps.** Bump the entry's `version`
  (ADR 0009; `check_plugin_versions.py` now requires it whenever a listed
  skill's directory, or the list itself, changes against the PR base), press
  "Check for updates" on claude.ai, and restart the Desktop app. E6 saw no
  automatic update in 3½ hours. A skill that also lives in a bundle needs that
  bundle's bump too, so one skill edit can mean two bumps.
- **The plugin cache holds the whole repository.** The source is the root, so
  each installed version copies all of it (about 3.3 MB of tracked files at
  `3b4cfff`), not just nine skill directories.
- **Duplicates during the transition.** Between phases 2 and 3 each account
  skill appears twice, once uploaded and once as `adam-personal:<name>`. Its
  description is paid twice in every session on the account until phase 3.
- **The three bundles become available on the account.** Adding the
  marketplace lists `adam`, `adam-local`, `fastmail` and `cms-platform` too.
  They must stay un-enabled: enabling them brings the 12-skill overshoot and
  the repo-scoped skills ADR 0002 keeps off the account (E6 §3.2).
  `"defaultEnabled": false` on every entry except `adam` keeps them off in
  Claude Code; on claude.ai nothing is enabled unless someone enables it.
- **The name is effectively permanent once enabled.** Renaming
  `adam-personal` would need an entry in the append-only `renames` map
  (ADR 0001), and every app that enabled it would follow that map or lose it.
- **One more place to keep in step.** Adding a skill to the account is now a
  line in `account-skills.txt` plus a path in the entry plus a version bump.
  `check_consistency.py` fails until the first two agree.
- **Terminals may receive it.** If the plugin syncs to Claude Code as
  `adam-personal@synced`, terminals would load these skills a second time
  beside the pinned bundles. Phase 2 checks this.

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
  `"defaultEnabled": false` and a `version`, every listed path is
  `./plugins/<bundle>/skills/<skill>` with a `SKILL.md`, and the list equals
  the names in `account-skills.txt` resolved to their bundles. Its tests are in
  `scripts/test_check_consistency.py`, including
  `test_the_account_plugin_serves_exactly_the_declared_account_skills`, which
  parses both files independently of the checker.
- `scripts/check_plugin_versions.py` fails a PR that changes a listed skill, or
  the list, without raising the entry's `version`
  (`test_curated_skill_edited_without_entry_bump_fails`,
  `test_curated_skills_list_changed_without_bump_fails` in
  `scripts/test_check_plugin_versions.py`).
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
  [version management](https://code.claude.com/docs/en/plugins-reference#version-management)
