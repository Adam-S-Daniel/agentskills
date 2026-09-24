# 0012. Serve the account's skills as one repo-synced plugin

- **Status:** Proposed
- **Date:** 2026-09-23 (shape revised 2026-09-24)
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
plugin channel on 2026-09-23 and 2026-09-24 with throwaway probes. What each
one showed matters here, because they are not interchangeable:

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
- **Which plugin shapes claude.ai accepts** (E6 §3.7, measured by the owner
  on 2026-09-24 in the same throwaway repo):
  - an entry whose source folder has **no `plugin.json`** — `"source": "./"`
    or `"source": "./plugins"`, with `strict: false` and a curated `skills`
    list — **does not appear on claude.ai at all**;
  - an entry pointing at a **bundle folder** with `strict: false` and a
    `skills` list naming one of its two skills appears, but claude.ai
    **ignores the list** and shows both;
  - a plugin with **its own folder and `plugin.json`**, whose
    `skills/<name>` is a **git symlink** (mode 120000) to
    `../../<bundle>/skills/<name>`, appears with **only** that skill, and the
    skill **runs**: its token came back in claude.ai chat, in local Desktop
    Cowork on Windows (after a full Desktop restart) and on iOS, each with the
    control passing. In a Claude Code terminal on the Windows laptop the
    synced copy's `skills/<name>/SKILL.md` is a **real directory holding the
    real file** — claude.ai resolves the symlink server-side.
  - The Desktop app listed a newly added plugin only after a **full restart**
    (tray included), even after its marketplace reported the new commit.
- **Deletion was measured only for the uploaded probe**: deleting it on
  claude.ai removed it from iOS (E6 §3.5). Removing a repo-synced plugin was
  not measured. The terminal's synced manifest dropped `e6-probe` after its
  marketplace was removed, while the plugin's directory stayed on disk
  (E6 §3.7).
- **Updates arrived only by hand** for the repo-synced probe. No automatic sync
  delivered a push within 3½ hours. What worked: push, press "Check for
  updates" on the marketplace at claude.ai (it reported "Updated to <sha>"),
  then fully restart the Desktop app (E6 §5 step 5). Whether "Check for
  updates" honours the plugin's `version` is unknown: the probe's version was
  bumped with its content, and the terminal's synced manifest records
  claude.ai's own counter (`"0001"`, `"0041"`), not the semver.
- **This repo is accepted as a personal marketplace**, and adding a
  marketplace enables none of its plugins (E6 §3.5–§3.6).

None of the three bundles matches the account: enabling them would add 12
skills the account deliberately does not carry (E6 §3.2).

## Decision

Serve the account's skills as one plugin, `adam-personal`, in its own folder
`plugins/adam-personal/`: a `.claude-plugin/plugin.json` (plus the Agent
Plugins `plugin.json` every bundle carries) and a `skills/` directory whose
entries are **git symlinks** (mode 120000) to `../../<bundle>/skills/<name>`,
one per name in `account-skills.txt`. The marketplace entry is
`"source": "./plugins/adam-personal"`, `"defaultEnabled": false`, with no
`strict` and no `skills` list: `plugin.json` and the links define the plugin.
This is the one shape claude.ai was measured to list with exactly the intended
skills and to run (E6 §3.7). No skill moves; the `renames` map is untouched.

`account-skills.txt` stays the single declaration of what the account carries;
CI holds the links equal to it. Durable machines turn the plugin off in their
terminals (`"adam-personal@synced": false`, converged by `setup.sh`). The owner
adds this repo as a personal marketplace and enables only `adam-personal`, on
claude.ai and in the Desktop app.

### How this squares with ADR 0008

[ADR 0008](0008-refuse-symlinks-in-a-skill-directory.md) refuses a symlink
**inside** a skill directory, because the lock's digest must commit to every
byte a skill ships. The links here are a different thing: each is a plugin's
whole **skill entry**, pointing at a real skill directory that itself contains
no symlink. The plugin is never locked — the lock generator and the bootstrap
hook both refuse to lock `adam-personal` by name, and the generator also
refuses any symlinked skill directory in a locked bundle — so no digest is ever
taken through a link. claude.ai resolves the links server-side when it builds
the plugin from GitHub. ADR 0008's text does not forbid this shape, so it
carries no status change; this section is the carve-out.

## Rollout

1. **Phase 1 — PR #175, merged.** The first shape (a curated `./` entry), its
   checks, `setup.sh`'s terminal opt-out and this ADR. Nothing changed on the
   account.
2. **Phase 1b — this change.** The symlink-folder shape replaces the curated
   entry, with the checks that hold it to `account-skills.txt` and to the
   version rule, and every consumer taught to skip the links. Nothing changes
   on the account.
3. **Phase 2 — the owner, in this order.** Every "not offered" below counts
   only if a known control is offered on the same surface in the same sitting
   (E6 §5's rule).
   0. **Shape probe — done 2026-09-24.** The symlink-folder shape was probed in
      the throwaway repo: only the linked skill loaded, and it ran in
      claude.ai chat, local Desktop Cowork and iOS; the terminal's synced copy
      was a resolved real directory (E6 §3.7). The curated `./` shape this
      phase first called for failed the same probe — it never appeared.
   1. Run `bash setup.sh` in **each home** (Windows Git Bash and WSL) on each
      durable machine, so `"adam-personal@synced": false` is in place
      **before** the plugin is enabled anywhere. The pass condition is reading
      each home's `~/.claude/settings.json` and seeing
      `enabledPlugins["adam-personal@synced"] == false`, **not** setup.sh's
      output: before PR #175 setup.sh could print "Setup complete." on a
      Windows home having written nothing, because it picked the Microsoft
      Store `python3` stub by name and never checked the exit code (measured
      2026-09-24; that home's settings.json had none of ADR 0010's keys). It
      now chooses an interpreter by running it and fails when convergence
      does. **Prerequisite: each home needs a working Python 3 on PATH** for
      setup.sh's settings step. This laptop's Windows home has none (measured:
      `python3` and `python` there are the App Installer alias, exit 49), so
      setup.sh will stop there with an error. Remedy: install Python 3 from
      python.org or with `winget install Python.Python.3.12`, then re-run
      `bash setup.sh` in a **new** terminal (the install updates PATH only for
      shells started after it).
   2. Add `Adam-S-Daniel/agentskills` at
      [claude.ai/customize/plugins](https://claude.ai/customize/plugins);
      enable only `adam-personal` there and in the Desktop app's plugin
      settings. The Desktop app lists a newly added plugin only after a
      **full restart** (tray included), even once the marketplace reports the
      new commit (E6 §3.7).
   3. On each surface (claude.ai chat and Cowork, local Cowork, Desktop Chat,
      Chrome, iOS), with a control: a listed skill is offered as
      `adam-personal:<name>` (for example `adam-personal:rename-pdfs`); an
      **unlisted** skill (for example `adam-personal:debug-github-workflows`)
      is **not** offered; and none of `adam`, `adam-local`, `fastmail` or
      `cms-platform` is enabled anywhere. The uploaded ZIP copies still exist
      in this phase, so each account skill appears twice; that is expected.
   4. A terminal on a durable machine, after a fresh launch: `claude plugin
      list --json` shows `adam-personal@synced` as disabled.
   5. **Removal.** Disable `adam-personal` on claude.ai. Then, within 24
      hours: fully restart the Desktop app; launch a terminal, wait a few
      minutes, and launch one again — the docs say terminal sync removes
      plugins "in the background" after start, so a single launch can race
      it — and only then check that it is no longer offered on a surface and
      is gone from the terminal's synced bucket
      (`~/.claude/plugins/synced/`). **If it is still
      offered anywhere, or still in `~/.claude/plugins/synced/`, phase 3's
      premise fails and ADR 0002's one-way-door consequence stays.** Then
      re-enable it on **both** claude.ai and the Desktop app. This
      measurement, not E6's deletion of the uploaded probe, is what phase 3
      rests on. The bound is not arbitrary caution: on the Windows home of
      this laptop the synced bucket still listed `e6-probe` at 2026-09-24
      01:15 UTC, after its marketplace had been removed on claude.ai (how long
      the terminal sync takes to drop a plugin is unknown).
4. **Phase 3 — the owner, then a follow-up change.** Delete the uploaded ZIP
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

- **Every release is three manual steps.** Bump `adam-personal`'s version in
  both manifests, press "Check for updates" on claude.ai, and restart the
  Desktop app. E6 saw no automatic update in 3½ hours. The bump is required
  conservatively: `claude plugin update` gates on `version` (ADR 0009), but
  whether claude.ai's "Check for updates" does is unknown (its synced manifest
  records its own counter). `check_plugin_versions.py` requires the bump when
  the plugin's own files change (a link added, removed or retargeted) **or**
  when any linked skill's content changes: git never follows a symlink, so the
  plugin's own diff stays empty when a target changes, and the gate reads the
  targets' changes separately. A linked skill edit therefore needs two bumps —
  its bundle's and `adam-personal`'s. **That gate is advisory today:** it fails
  the `consistency` job, but `main`'s only required status check is
  `pytest-windows` (ruleset 18877850), so a missed bump is not merge-blocking
  unless `consistency` becomes a required check in repo-settings' `fleet.yml`.
- **Windows checkouts see the links as text files.** With `core.symlinks=false`
  (the Windows default) each `skills/<name>` is a small file holding its
  target. That is harmless for the account: claude.ai builds the plugin from
  GitHub, where they are symlinks, and the Desktop app installs from
  claude.ai's build — measured working in local Cowork on Windows (E6 §3.7).
  Every check here reads both spellings as the same link.
- **Every skill enumerator has to skip the links.** On a symlink-capable
  checkout `plugins/*/skills/*/SKILL.md` follows them, so `setup.sh`, the
  consistency basename rule, `check_skills.py`'s census and `sync_skills.py`
  skip symlinked skill entries; the README renders `adam-personal` as one row
  naming its skills; and a repo-root `conftest.py` keeps pytest from
  collecting the linked skills' tests a second time through the suite's
  `plugins/*/skills/*/tests/` glob (measured on this PR's first CI runs, where
  Windows runners materialise the links too). skills-doctor's `registry_copy` was left as it is: for
  `fastmail` alone it may name the link path instead of `plugins/fastmail`,
  reaching the same bytes, and changing it would move the `adam` bundle's
  locked digest.
- **Terminals would receive it — measured — so durable machines opt out.** A
  repo-synced plugin reached the laptop's terminal in E6 (§3.6), and Claude
  Code dedupes a synced plugin only against an installed plugin *of the same
  name*; `adam-personal` matches no bundle. Enabled, it would load 8 of its 9
  skills a second time beside `adam@agentskills` and `adam-local@agentskills`,
  and bring back the account copy of `sync-skills` that ADR 0010 took off
  terminals. `setup.sh` therefore converges `"adam-personal@synced": false` in
  user `enabledPlugins`, the documented per-plugin off switch
  ([synced plugins](https://code.claude.com/docs/en/plugins-reference#synced-plugins)).
  It writes `false` on every run, so a manual
  `claude plugin enable adam-personal@synced` lasts only until setup.sh next
  runs. These user settings also govern the Desktop app's Code tab, so the
  plugin is off there too; the Desktop app's Chat and Cowork tabs are enabled
  in its own plugin settings.
- **Cloud sessions cannot opt out at user level.** They sync the account's
  plugins into the session and have no user settings `setup.sh` can reach. The
  only lever is a repo's committed `.claude/settings.json` with
  `"adam-personal@synced": false`, one repo at a time. After phase 3 a cloud
  session carries the same skills it carries today as uploaded account skills,
  so it is at parity; during phase 2 it carries both copies.
- **The plugin folder is closed.** `check_consistency.py` fails if
  `plugins/adam-personal/` holds anything but `.claude-plugin/plugin.json`,
  `plugin.json` and `skills/`, since anything else there (`hooks/`, `.mcp.json`,
  `bin/`, `settings.json`, a `package.json` that triggers an install in every
  cached copy) would load as part of the account plugin. Its marketplace entry
  may carry only display fields, `name`, `source` and `defaultEnabled`: with
  the default strict mode an entry can still add skills, hooks or MCP servers
  on top of `plugin.json`, and a `version` there would be masked by
  `plugin.json`'s.
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
  line in `account-skills.txt`, a link in `plugins/adam-personal/skills/` and a
  version bump. `check_consistency.py` fails until the first two agree.

## Alternatives considered

- **A curated `./` entry with `strict: false` and a `skills` list** (this
  ADR's first shape, merged in PR #175). Rejected: claude.ai does not list an
  entry whose source folder has no `plugin.json` at all (E6 §3.7, measured
  2026-09-24).
- **A curated `skills` list on an existing bundle's entry.** Rejected:
  claude.ai lists it but ignores the list and serves every skill in the bundle
  (E6 §3.7, measured).
- **Move the skills into a new `plugins/adam-personal/skills/` bundle as real
  directories.** Skill moves between bundles touch the append-only `renames`
  map, and it would pull `adam-writing-style`, `finding-unknowns` and
  `writing-adrs` out of the `adam` bundle that ten repos install through
  `skills.lock`. Rejected: the links serve the same directories with no move.
- **Copy the skill directories into the plugin.** A second, unversioned copy of
  each skill that drifts from its bundle. Rejected.
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

## How to verify

- `scripts/check_consistency.py` (CI job `consistency`) fails unless the
  `adam-personal` entry sources `./plugins/adam-personal`, is opt-in and
  carries only allowed keys; the folder holds only its manifests and
  `skills/`; `skills/` has exactly one link per name in `account-skills.txt`;
  and each link targets `../../<bundle>/skills/<name>` for the one bundle that
  holds that skill as a real directory. Tests in
  `scripts/test_check_consistency.py`, run in both link spellings, including
  `test_the_account_plugin_links_exactly_the_declared_account_skills` (which
  reads modes and targets from git, independently of the checker),
  `test_a_link_to_the_wrong_target_is_reported` and
  `test_anything_else_in_the_account_plugin_folder_is_reported`.
- `scripts/check_plugin_versions.py` fails a PR that changes a linked skill
  without raising `adam-personal`'s version
  (`test_a_linked_skill_edited_without_the_linking_plugins_bump_fails`), and
  the plugin's own diff stays empty when only a target changes
  (`test_the_linking_plugins_own_diff_stays_empty_when_a_target_changes`).
  This fails the `consistency` job; it is **not merge-blocking** unless
  `consistency` becomes a required check (today only `pytest-windows` is
  required, ruleset 18877850). The tests themselves run in `pytest-windows`.
- `adam-personal` cannot be locked: `test_the_generator_refuses_to_lock_the_account_plugin`
  and `test_the_hook_reader_refuses_a_lock_naming_the_account_plugin` in
  `scripts/test_generate_skills_lock.py`.
- The consumers skip the links: `scripts/test_setup_skill_collection.py`,
  `test_a_symlinked_skill_directory_is_not_scanned_twice`
  (`scripts/test_check_skills.py`), `test_a_symlinked_skill_entry_is_not_the_skill`
  (sync-skills' tests) and `test_a_plugin_of_skill_links_renders_one_row_naming_them`
  (`scripts/test_generate_readme_table.py`).
- `setup.sh` converges `"adam-personal@synced": false`
  (`test_the_account_plugin_is_off_in_terminals` in
  `scripts/test_setup_settings_convergence.py`, which parses the JSON), and
  fails rather than printing "Setup complete." when no interpreter works or
  convergence fails (`test_a_store_stub_interpreter_fails_setup_instead_of_completing`,
  `test_a_failing_convergence_fails_setup`).
- `claude plugin validate . --strict` (CI job `plugin-validate`) accepts the
  marketplace and the plugin.

## References

- [E6](../experiments/E6-account-plugin-channel.md) and
  [#160](https://github.com/Adam-S-Daniel/agentskills/issues/160) — the
  measurements this rests on
- [PR #175](https://github.com/Adam-S-Daniel/agentskills/pull/175) — the first
  shape, superseded by this revision
- [ADR 0001](0001-consolidate-plugins-into-bundles.md) — bundles and the
  append-only `renames` map
- [ADR 0002](0002-limit-account-store-to-repo-independent-skills.md) — what the
  account carries, and the one-way-door consequence phase 3 supersedes
- [ADR 0006](0006-drive-the-account-store-drift-loop-from-one-published-artifact.md)
  — the drift loop phase 3 replaces
- [ADR 0008](0008-refuse-symlinks-in-a-skill-directory.md) — no symlinks
  inside a skill directory; see "How this squares with ADR 0008"
- [ADR 0009](0009-bump-bundle-versions-on-every-release.md) — version bumps
- [ADR 0010](0010-let-pinned-channels-own-the-terminal.md) — terminals take
  skills from the pinned bundles
- Claude Code docs: [plugin marketplaces](https://code.claude.com/docs/en/plugin-marketplaces),
  [synced plugins](https://code.claude.com/docs/en/plugins-reference#synced-plugins),
  [version management](https://code.claude.com/docs/en/plugins-reference#version-management)
