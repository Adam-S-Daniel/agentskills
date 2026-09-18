# skill-impact.md — the registry's skill-change audit trail

Every change to a skill hosted in this registry (the `adam`, `adam-local` and
`fastmail` bundles) gets an entry here — creations, edits, renames, removals,
and **rejected proposals**. The rejected ones are the reason the file exists:
git history records what landed, but nothing records what was tried and turned
down, so the next session re-derives and re-proposes it. An approach already
ruled out is the expensive thing to lose. (Convention defined in skills-evals'
`DESIGN.md`, "Scaling to the registry"; the underlying evidence — a proposal
audit trail is what stops failed abstractions being re-proposed — is
WikiSkill, arXiv:2608.27454.)

What this file is NOT for: harness, hook, CI, lock or docs changes — only
skill content. Entries append in the same PR as the change, newest first.

## Entry format

```
## YYYY-MM-DD — <bundle>/<skill> — <create|edit|rename|remove|rejected>
- Motivation: one line — the incident, pattern, or issue that prompted it
- Change: one line — what changed (PR #NNN)
- Eval: the skill's eval result (exit code + counts), or "none — no eval
  exists yet", or "exempt (DESIGN.md non-coverage table)"
- Outcome: merged YYYY-MM-DD, or rejected YYYY-MM-DD — one line why. The
  full proposal survives in the closed PR; link it rather than pasting it.
```

Rules:

- **A rejected proposal is the highest-value entry.** Record it even when it
  feels like noise — especially then.
- **Append-only.** A wrong entry gets a correcting entry, not an edit.
- **This repo is public and scanned.** Nothing sensitive in an entry, ever;
  a sensitive rejection is recorded by PR link alone.

Entries before 2026-08-25 predate this file and live only in git history —
no backfill is planned; the file adds the fields git does not capture.
# skill-impact.md — the registry's skill-change audit trail

Every change to a skill hosted in this registry (the `adam`, `adam-local` and
`fastmail` bundles) gets an entry here — creations, edits, renames, removals,
and **rejected proposals**. The rejected ones are the reason the file exists:
git history records what landed, but nothing records what was tried and turned
down, so the next session re-derives and re-proposes it. An approach already
ruled out is the expensive thing to lose. (Convention defined in skills-evals'
`DESIGN.md`, "Scaling to the registry"; the underlying evidence — a proposal
audit trail is what stops failed abstractions being re-proposed — is
WikiSkill, arXiv:2608.27454.)

What this file is NOT for: harness, hook, CI, lock or docs changes — only
skill content. Entries append in the same PR as the change, newest first.

## Entry format

```
## YYYY-MM-DD — <bundle>/<skill> — <create|edit|rename|remove|rejected>
- Motivation: one line — the incident, pattern, or issue that prompted it
- Change: one line — what changed (PR #NNN)
- Eval: the skill's eval result (exit code + counts), or "none — no eval
  exists yet", or "exempt (DESIGN.md non-coverage table)"
- Outcome: merged YYYY-MM-DD, or rejected YYYY-MM-DD — one line why. The
  full proposal survives in the closed PR; link it rather than pasting it.
```

Rules:

- **A rejected proposal is the highest-value entry.** Record it even when it
  feels like noise — especially then.
- **Append-only.** A wrong entry gets a correcting entry, not an edit.
- **This repo is public and scanned.** Nothing sensitive in an entry, ever;
  a sensitive rejection is recorded by PR link alone.

Entries before 2026-08-25 predate this file and live only in git history —
no backfill is planned; the file adds the fields git does not capture.

---

## 2026-09-18 — adam/skills-doctor — edit

- Motivation: Claude Code 2.1.273+ buckets the claude.ai account store at
  `synced/<organizationUuid>_<accountUuid>/`, so `--account-drift` read the
  flat path, found nothing, and printed "holds no skills — nothing to
  compare … 0 drifted" at exit 0 over 21 skills on disk — a false clean, and
  the shadow comparison (#122) went silent the same way
  ([#157](https://github.com/Adam-S-Daniel/agentskills/issues/157)).
- Change: `resolve_account_store()` finds the bucket and refuses rather than
  guessing on a multi-account machine; `DriftReport.blocked` keeps "could not
  run" (exit 2) distinct from "0 drifted"; SKILL.md's account-store recipe and
  a new trap bullet. Branch `claude/account-mirror-bucket-layout` — the PR
  could not be opened from the session that did the work (`POST /pulls`
  returned 403 `Resource not accessible by integration`), so no number exists
  to cite yet; append a correcting entry with it once one does.
- Eval: first fixture added — skills-evals
  `evals/skills-doctor/bucketed-account-store`, branch
  `claude/skills-doctor-first-fixture`. Objective-only both ways:
  `--arm objective-only` on the pristine seed exits 1, 1/5 checks passed (the
  restraint check, correctly); `--arm objective-only --workspace <reference
  answer>` exits 0, 5/5. The A/B arms were NOT run — this session had no model
  access for them, so no with_skill/without_skill delta is claimed.
- Outcome: open as of 2026-09-18 — branch pushed and verified on the remote,
  PR pending the permission above.

## 2026-09-18 — adam-local/sync-skills — edit

- Motivation: same bucket move
  ([#157](https://github.com/Adam-S-Daniel/agentskills/issues/157)).
  `--verify` exited 1 naming a manifest path no current CLI has, and told the
  operator to refresh a mirror that was already there; `--record-account-state`
  read the same constant, so the account half of ADR 0006's drift loop could
  not be re-recorded from any current CLI.
- Change: `resolve_account_mirror()` finds the bucket, keeps reading a flat
  mirror when there is none, and refuses — naming the candidates — when a
  machine has several and neither `oauthAccount` nor
  `$CLAUDE_CODE_ACCOUNT_UUID` resolves one; `--record-account-state` now writes
  nothing on a refusal, because a recording taken against an unread store
  claims every declared skill was never uploaded. SKILL.md gains a "where the
  mirror is" paragraph. Branch `claude/account-mirror-bucket-layout`, PR
  pending for the reason in the entry above.
- Eval: exempt (DESIGN.md non-coverage table) — listed under "defer:
  machine-bound (WSL/WPF/browser surfaces); faking the surface costs more than
  the churn justifies today".
- Outcome: open as of 2026-09-18 — branch pushed and verified on the remote.


## 2026-09-04 — adam-local/windows-elevation-from-wsl — create

- Motivation: wsl-automation's repo-specific "PowerShell invoked from WSL is
  never elevated" lesson; _agent-guidance#112/#113 proposed promoting it to
  base.md, and _agent-guidance#114 assessed it against ADR 0002 as a skill
  (conditional on one host, loud failure mode, nothing enforces it).
- Change: new skill, adam-local 1.1.0 -> 1.2.0
  ([#152](https://github.com/Adam-S-Daniel/agentskills/pull/152))
- Eval: skills-evals `evals/windows-elevation-from-wsl`
  ([skills-evals#59](https://github.com/Adam-S-Daniel/skills-evals/pull/59)),
  3 trials per arm on claude-sonnet-5, run exit 0 each time — with_skill
  7/7 objective checks in all three (judge 9.4 / 9.6 / 9.4); without_skill
  6/7 in all three, every miss `exported-before-handoff` (judge 10.0 under
  the first rubric, 7.2 / 7.4 after it was capped on the export). The
  delta is the export-before-overwrite step; the baseline already stops at
  one denial and hands over an elevated-prompt line.
- Outcome: opened 2026-09-04 as #152; merge is a human step (skill
  graduation), so the merge date is not recorded here

---

## 2026-08-29 — adam/disarm-inherited-reach — create

- Motivation: a fleet incident during the guidance-centralization work
  surfaced a procedure worth packaging (per PR #144: "the procedure a fleet
  incident turned out to need").
- Change: new skill
  ([#144](https://github.com/Adam-S-Daniel/agentskills/pull/144))
- Eval: none — no eval exists yet
- Outcome: merged 2026-08-29

## 2026-08-25 — adam/skills-doctor — edit

- Motivation: account-store drift was judged by timestamps, which reports
  false drift; content is the fact of the matter.
- Change: account drift became a content check, not a timestamp one
  ([#142](https://github.com/Adam-S-Daniel/agentskills/pull/142))
- Eval: none — no eval exists yet
- Outcome: merged 2026-08-25

## 2026-08-25 — adam/skills-doctor — edit

- Motivation: the hosted-session OR rule was stated outside the surface
  table, where readers had already stopped reading.
- Change: the OR moved into the surface table
  ([#141](https://github.com/Adam-S-Daniel/agentskills/pull/141))
- Eval: none — no eval exists yet
- Outcome: merged 2026-08-25


- Motivation: a fleet incident during the guidance-centralization work
  surfaced a procedure worth packaging (per PR #144: "the procedure a fleet
  incident turned out to need").
- Change: new skill
  ([#144](https://github.com/Adam-S-Daniel/agentskills/pull/144))
- Eval: none — no eval exists yet
- Outcome: merged 2026-08-29

## 2026-08-25 — adam/skills-doctor — edit

- Motivation: account-store drift was judged by timestamps, which reports
  false drift; content is the fact of the matter.
- Change: account drift became a content check, not a timestamp one
  ([#142](https://github.com/Adam-S-Daniel/agentskills/pull/142))
- Eval: none — no eval exists yet
- Outcome: merged 2026-08-25

## 2026-08-25 — adam/skills-doctor — edit

- Motivation: the hosted-session OR rule was stated outside the surface
  table, where readers had already stopped reading.
- Change: the OR moved into the surface table
  ([#141](https://github.com/Adam-S-Daniel/agentskills/pull/141))
- Eval: none — no eval exists yet
- Outcome: merged 2026-08-25
