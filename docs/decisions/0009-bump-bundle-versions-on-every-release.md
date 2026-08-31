# 0009. Bump bundle versions on every release so `plugin update` can fire

- **Status:** Proposed
- **Date:** 2026-08-31
- **Deciders:** Adam Daniel

## Context

Measured on `ZENDA` on 2026-08-31, by a `skills-doctor` run that was looking
for something else.

`claude plugin update adam@agentskills` reports
`✔ adam is already at the latest version (1.0.0).` and does nothing. It is
correct about the version and wrong about everything a reader takes from that
sentence: the installed bundle was **381 commits behind `main`**.

`update` gates on the declared **version string** and on nothing else:

- Every bundle declares `"version": "1.0.0"`. `adam` has said `1.0.0` since
  commit `b833735` first introduced the field; the three earlier commits that
  touched that file carried no `version` key at all. `1.0.0` is the only
  version this bundle has ever had, and the repo has **zero** git tags.
- Installed `1.0.0` equals marketplace `1.0.0`, so the updater short-circuits
  before it looks at anything else.

The CLI is not missing the information — it records the source commit and then
declines to consult it. `~/.claude/plugins/installed_plugins.json` carries
`"gitCommitSha": "88526d12e3d6b35e4db6248497334b3a85e50b68"`, which is
`Prune skills that left the lock, without deleting what we did not install`
(#77, 2026-08-14). `git merge-base --is-ancestor` confirms it a clean ancestor
of `main`, 381 commits back.

What those commits changed under `plugins/adam/skills` — +10,516 / −295 lines
across 9 files:

| File | Δ |
|---|---|
| `disarm-inherited-reach/SKILL.md` | +221 (added) |
| the SHA-pinning skill's `SKILL.md` | −248 (removed) |
| `skills-doctor/SKILL.md` | ±417 |
| `skills-doctor/scripts/check_provenance.py` | +3,493 (added) |
| `skills-doctor/scripts/test_check_provenance.py` | +6,326 (added) |
| `github-actions-repo-settings/SKILL.md` + assets | ±20 |
| `workflow-path-audit/SKILL.md` | ±86 |

Three consequences make this more than one stale laptop.

**A skill named in managed guidance was unreachable.** `AGENTS.md` instructs
sessions to run `/adam:disarm-inherited-reach` before disarming a scratch
tree's inherited reach. It is on `main` and in two fleet `skills.lock` files.
It was absent from the install, so a session obeying that instruction got
nothing — and the guidance offers no fallback, having been written on the
assumption that delivery works.

**A retired skill kept loading.** ADR 0003 retired the SHA-pinning skill
deliberately: a supply-chain rule must not be conditional on a description
match, so the rule moved into managed `AGENTS.md` and the skill was dropped.
The stale install still advertised it — ~190 tok of always-on context spent on
a skill the registry had already ruled must not be one. A retirement that does
not reach installed machines has not happened, and installed machines are the
only place it means anything.

**The freshness check in managed guidance is blind to this by construction.**
`AGENTS.md`'s "Durable machine — ACT, then one line" prescribes comparing the
marketplace **clone's** `HEAD` against `origin/main`. But
`known_marketplaces.json` sets `"autoUpdate": true` on this marketplace and
stamps `lastUpdated` on its own. The clone therefore refreshes itself every
session, the prescribed check reads GREEN permanently, and the installed bundle
rots untouched beside it. The check does not merely measure the wrong object —
auto-update *guarantees* the measured object is always correct, so that
indicator can never go red however far the install drifts.

That last one is a shape this account has already paid for twice: `$?`
belonging to `tail` rather than to `gh pr checks`, and `python3 test_foo.py`
exiting 0 having run zero assertions. An indicator wired to something adjacent
to what it claims to report, reading green.

Three constraints bound any fix:

- **Skill directory basenames must not change** (ADR 0001) — they key
  `setup.sh`'s per-agent symlinks and the `sync-skills` account uploads.
- **Each bundle ships the version twice.** The root `plugin.json` read by Agent
  Plugins v1 clients and `.claude-plugin/plugin.json` read by Claude Code both
  carry it — six manifests across three bundles, all at `1.0.0`. A bump must
  move both halves of a pair; `check_agent_plugins.py` already cross-checks
  `version` between them and fails on disagreement.
- **The version is the only field `update` compares**, so whatever moves it has
  to move on every content change — not on a maintainer's assessment of whether
  a change was worth a release.

## Decision

Bump `version` in **both** of a bundle's manifests on every change to that
bundle's content, and enforce it in CI: a pull request that touches files under
`plugins/<bundle>/` must also raise that bundle's version above its value at
the PR base, or the check fails.

## Consequences

Positive:

- `claude plugin update` becomes able to fire at all. Today it cannot, for any
  consumer of this registry, ever — which is a delivery channel this repo
  believed it had.
- Skill additions and retirements reach machines that already hold the bundle,
  currently the one population they never reach.
- `claude plugin tag` becomes usable: it creates a `{name}--v{version}` tag and
  validates `plugin.json` against the enclosing marketplace entry. With a
  frozen version there is nothing to tag, which is why zero tags exist.

Negative:

- Every content PR now carries a version bump, and the version line becomes a
  merge-conflict surface when two PRs touch one bundle. Whichever merges second
  re-bumps — the convention ADR numbering already uses.
- The gate asserts only that the version **moved**, never that it moved
  *correctly*. A patch bump on a breaking change passes.
- It does nothing for installs that are already stale: they compare against the
  version they already hold, so each needs one manual reinstall. This decision
  prevents the next drift; it cannot undo the current one.
- The bump is one more thing to forget, which is precisely why it is a CI gate
  and not a line in `AGENTS.md`.

## Alternatives considered

**Have `update` compare the recorded `gitCommitSha`.** The data already sits in
`installed_plugins.json`, so this addresses the actual cause. Rejected because
it is not ours: that is Claude Code CLI behaviour, and a registry must not
depend on a change it neither controls nor can schedule.

**Tell operators to periodically uninstall and reinstall.** Rejected. It is
unobservable — nothing reports whether anyone did — and it scales with the
number of machines rather than the number of releases. It is the same "remember
to check" shape the freshness check above already failed at, and failing
quietly is what let this reach 381 commits before anyone looked.

**Bump only on changes judged significant.** Rejected. Significance is assessed
by the author, who is the party least able to see a consumer's staleness. This
account has the asymmetry on record: the repo that chose the
`generic-api-key`-tripping skill name was not the repo that went red, and its
own lock stayed green, so the author had no signal at all.

**Derive the version automatically from the commit (date- or count-stamped).**
Attractive, and still open as a follow-up. Rejected for now as a larger change
than the gate needs to be, and because it would churn every bundle's version on
docs-only commits that do not touch bundle content.

## How to verify

The gate is `scripts/check_plugin_versions.py`, wired into `ci.yml`'s
`consistency` job and covered by `scripts/test_check_plugin_versions.py`.

It must **parse** both manifests rather than grepping the version line, for the
reason `AGENTS.md` gives about the workflow-YAML invariant: a line scan reads
clean on text it cannot see.

The test that matters is the one proving the gate can fail — a fixture PR that
edits a `SKILL.md` under a bundle and leaves the manifests untouched must exit
non-zero. A gate never observed failing is a green light wired to nothing, and
this repo has already been bitten by exactly that (`python3 test_foo.py`
exiting 0 over zero assertions).

## References

- [ADR 0001](0001-consolidate-plugins-into-bundles.md) — bundle layout, and the
  basename constraint this decision must not violate.
- [ADR 0003](0003-retire-the-sha-pinning-skill.md) — the retirement that never
  took effect on the stale install.
- Installed source commit: `88526d12e3d6b35e4db6248497334b3a85e50b68` (#77).
- `main` at time of measurement: `f2b86d204f2c89d8867df5ba85b8831337fdfba8`.
- Follow-up in a different repo: `_agent-guidance`'s `agents-md/base.md`
  prescribes the clone-vs-remote freshness check this ADR shows is permanently
  green. It should compare `installed_plugins.json`'s `gitCommitSha` against the
  marketplace clone's `HEAD` instead.
