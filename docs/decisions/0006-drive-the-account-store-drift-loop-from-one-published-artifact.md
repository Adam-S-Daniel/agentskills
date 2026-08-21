# 0006. Drive the account-store drift loop from one published artifact, read by two repos on their own schedules

- **Status:** Accepted
- **Date:** 2026-08-21
- **Deciders:** Adam Daniel

## Context

The claude.ai account skill store is the only channel that reaches chat,
Cowork, Claude in Chrome and mobile ([ADR 0002](0002-limit-account-store-to-repo-independent-skills.md)),
and it is the one delivery arm no CI job can look at. That sentence has been in
this repo for months as a flat assertion; it is worth being exact about *why*,
because the wrong reading of it sends the next person hunting for a credential.

**It is a surface constraint, not a permissions one.** Nothing about auditing
the account store spends a credential or calls an API: the audit reads files at
`~/.claude/skills/synced/`, and a GitHub runner does not have them. Measured
2026-08-21 from a hosted cloud session, which turned out to be exactly such a
surface: `~/.claude/skills/synced/` held 19 skill directories plus
`manifest.json`, and running skills-evals'
`harness/run_account_audit.py --registry <this checkout>` there reproduced the
published verdict exactly — `FAIL sync-skills [content-drift]`, 10 checked, 9
not owned, 1 finding. Same harness, same registry commit, same account. The
only variable was whether the process was standing somewhere those files exist.
No token would have changed the answer on a runner, because there is nothing
there to authenticate *to*.

So the only machine-readable statement of that store's state anywhere in CI's
reach is the one skills-evals' Tier-3 Routine publishes:
`propagation/account/latest.json` on that repo's `eval-results` branch. Until
this change, nothing here read it. `account-skill-zips` compared today's
registry against `account-state.json` — a recording that is only as recent as
whoever last ran `--record-account-state` from a machine with the mirror — and
it only ran when a push happened to touch a skill. Both halves of that are
wrong in the same direction: the account can drift with no commit here at all,
and the workflow that hands a phone the fix could only learn about it by
accident.

**Every event-based coupling to that publish is ruled out by measurement, not
by taste.**

- *There is no cross-repo credential.* Grepping `secrets.` across both repos'
  `.github/` trees on 2026-08-21 returns exactly one name, `secrets.GITHUB_TOKEN`,
  which is scoped to the repository whose workflow uses it. What that measures
  is that no workflow in either repo *references* a cross-repo credential — an
  unused repo Actions secret cannot be ruled out from here, because reading the
  secrets and variables endpoints is blocked to this session (403), so treat
  "there is none" as **not verified** and "nothing uses one" as measured.
- *There is no workflow run to chain from.* The Tier-3 result is pushed by a
  claude.ai Routine's fired session, not by a job, so `workflow_run` has no
  publisher to trigger on. skills-evals' own census of all 52 commits on
  `origin/eval-results` (2026-08-20, recorded in `evals/propagation/ROUTINE.md`)
  finds 20 publishes: 8 from that Routine and 12 from `eval.yml`'s badge step.
- *And a push listener on `eval-results` would never fire.* Every one of those
  20 publishes carries a CI-skip token — the publish message is fixed by
  ROUTINE.md step 4 and repeated verbatim in the live Routine prompt — and that
  token is GitHub's documented instruction not to create a workflow run for the
  push. It is witnessed rather than cited: skills-evals' commit `e4c291b`
  carried the token in a commit-message *body*, as a quotation, and created zero
  workflow runs where each earlier push to that branch had created three.
  skills-evals locks this with `PublishMessageAndPushTriggerTests`, which fails
  its build if such a listener is ever added while that message stands.

What survives all three is a `schedule` that reads the published artifact. That
is not a workaround for a coupling we failed to build; it is the only mechanism
the constraints leave standing.

## Decision

**One artifact, read by two repositories, each on its own schedule, and judged
by one implementation of the rule.**

`eval-results:propagation/account/latest.json` is the single statement of the
account store's condition. skills-evals reads it in `propagation.yml`'s
freshness gate; agentskills' `account-skill-zips` now reads the same file on a
daily cron (`23 6 * * *`, offset from `ci.yml`'s `17 6 * * *`) and on the pushes
that were already salient. Both sides evaluate it with skills-evals' own
`propagation.account_store.freshness_verdict`: the `pick` job clones the
harness anonymously and calls that function, with `max_age_days` read from
skills-evals' fixture rather than copied into a constant here.

On a `reported-failure` verdict the names it carries are **unioned** with the
local recording's `needs_upload`, then **intersected** with the committed
`account-skills.txt` declaration. Every other verdict — including `fresh` —
contributes nothing and removes nothing.

## Consequences

**This repo's CI now has a cross-repo dependency whose verdict can change with
no commit here.** Said plainly because it is the property a reviewer should
notice: a red `account-skill-zips` run can be caused entirely by something
happening in another repository, or in a claude.ai session. That is not new in
kind — `ci.yml`'s `skills-conformance` job clones two other repos' default
branches and audits them, which is exactly why it carries a schedule of its
own. Both jobs have the same shape, and both carry a cron for the same reason:
a condition that becomes true with no local event needs something local that
asks.

**The coupling is by condition, not by event, so the two sides can be up to a
day apart.** The Routine publishes at about 05:04 UTC (issue #48's last update,
2026-08-21T05:04:53Z, against a result generated at 05:03:52Z), and this cron
runs at 06:23 — but GitHub queues cron across the whole platform and delays it
by an amount nothing here controls (this fleet's own note on
`scheduled-run-health.yml` records 4-5h measured on daily crons), so no
schedule can be reliably "just after" a publish and aligning the two is not
something this side can arrange. That is latency, not incorrectness, and the
artifact's own `generated_at` is what makes a late read safe: `freshness_verdict` judges the age, so an early run sees a
yesterday's-but-still-fresh result and says so, while a Routine that has stopped
firing reads as `stale` — never as a pass. A design that trusted the clock
instead would have to guess at exactly the moment it matters most.

**The union is deliberately a superset, and a passing audit removes nothing.**
The recording knows today's registry against an old account; the audit knows
the account against an older `registry_ref`. Neither dominates, and the case
where the difference bites is the common one: a push-triggered run is by
construction the run whose commit the audit could not have seen. A gate that
zeroed the offer list on `fresh` would be wrong precisely there. Measured
2026-08-21 the two agreed exactly — both named `sync-skills` and nothing else —
which is the evidence that reading both costs nothing when they agree, not
evidence that one of them is redundant.

**`eval-results` is untrusted input, and the allowlist is what neutralises
it.** It is an unprotected results branch by design (AGENTS.md, "Automation vs
branch protection": generated data belongs on a dedicated unprotected branch,
and consumers treat its content as untrusted), published by a session's push
rather than by a reviewed workflow. So an audit-named skill is zipped only if
`account-skills.txt` already declares it; an undeclared name is reported loudly
in the run summary and built for nobody. One line, two jobs: it also keeps the
workflow from walking through the one-way door in ADR 0002, since the upload
path has no delete and declaring a skill is close to irreversible. Adding a
declaration stays a human's commit.

**A degraded run must never read as a clean one.** If the clone or the import
fails the verdict is `unavailable` — not a skills-evals status, deliberately —
the run continues on the local recording alone, and the summary says DEGRADED
in words, naming what could not be reached. The failure mode being defended
against is not a crash; it is a run that looks exactly like a healthy one while
half the evidence was never read.

**The ZIP workflow now has a `schedule`, which brings it inside
`scheduled-run-health.yml`'s watch.** That daily audit scans this repo's
`event=schedule` runs for failure, startup failure and timeout, and lands
findings on a single `ci`-labelled tracking issue. Positive: a scheduled run
that fails here can no longer be silent, which for a workflow nobody watches is
the difference between a broken offer list and a *reported* broken offer list.
Negative, and accepted: those failures now land on the same shared issue as
every other scheduled workflow, so the signal is one more comment on a thread
rather than a dedicated alert.

The gap that leaves is worth naming, because it is the one a reader will
assume is covered. A failed clone of skills-evals does **not** fail this
workflow — it degrades the verdict to `unavailable` and the run goes on
building ZIPs from the local recording, deliberately, because a hard failure
would take out the one workflow that hands a phone the fix. So the health audit
never sees it. The DEGRADED line exists only in the run summary, and nothing
notifies on a summary; a cross-repo half that quietly stopped being readable
would be visible only to someone opening the run.

**What this ADR does not close.** The drift issue's own lifecycle lives in the
other repo and is not finished. As of 2026-08-21, skills-evals issue #48
("Account skill store drifted from registry (automated Tier-3 audit)") is open
and maintained **by hand** — `ROUTINE.md` step 5 asks the fired session to keep
it in step and, per skills-evals#52, it never has — and the workflow that would
close it on the first `pass`, `.github/workflows/account-store-drift.yml`, is
open PR skills-evals#52, not merged. Verified through the GitHub API on
2026-08-21: skills-evals' default branch (`9737f0e`) carries five workflows and
that is not one of them. So "the loop closes itself" describes the agentskills
half today; the skills-evals half arrives when #52 lands. Anything here that
reads as though the close is already automatic is describing the intended end
state, and this paragraph is the correction.

## Alternatives considered

**`repository_dispatch` from skills-evals into agentskills.** The textbook
answer, and it fails twice over. It needs a credential that can write to
another repository, and the measurement above finds no workflow in either repo
referencing one; and there is no workflow run at publish time to send it from,
because the publisher is a session's `git push`. Creating a cross-repo
credential to solve a problem that is not a credential problem would also widen
the blast radius of the least-observable arm in the registry.

**`workflow_run` chained off the publish.** Rejected on the same second
ground, more directly: `workflow_run` triggers on a *workflow* completing, and
the Tier-3 audit is not a workflow. There is nothing to chain to.

**A `push` listener on `eval-results`.** The most tempting one, because it
reads as the direct coupling everybody wants — and it is the worst shape a CI
dependency can take: it would fire on none of the Routine's publishes and be
neither red, nor slow, nor logged. The mandated publish message carries a
CI-skip token, which suppresses the push event entirely, and skills-evals
enforces that pairing with a test that fails the build if the listener is added
while the message stands. Removing the token is not a typo fix either: it is
what stops a results-branch publish feeding CI back into itself.

**Re-deriving the freshness rule here.** Trivially easy — the artifact is JSON,
and an age comparison is three lines. Rejected because a second implementation
of a policy is free to drift from the first, and nothing would report the
divergence. `freshness_verdict` is the predicate the drift tracking issue is
keyed to — the verdict skills-evals' own freshness gate acts on today, and the
one that will open, edit and close issue #48 once #52 lands — so a local copy
of the rule would let this
workflow build ZIPs for a condition nobody filed, or sit silent while an issue
stayed open, and the only symptom would be two repos quietly disagreeing about
whether the account store is broken. Cloning a public repo to call one function
is a real cost — a network dependency in a job that had none — and it buys the
guarantee that the report of the problem and the fix for it always appear
together.

**Leaving the workflow push-only and letting a human dispatch it.** This is
the status quo, and it is what produced the shape being fixed: the account
condition changes without commits here, so a push-only trigger learns about it
only when some unrelated change happens to touch a skill. A human who must
remember to dispatch is a schedule with worse reliability and no log.

## How to verify

```bash
python3 -m pytest scripts/test_account_workflows.py \
                  scripts/test_account_zip_selection.py -q
```

The decisions above are asserted rather than described:

- `test_it_runs_daily_on_one_offset_cron` — exactly one cron, non-zero minute.
- `test_the_verdict_is_imported_from_skills_evals_not_re_derived` — the `pick`
  step imports `freshness_verdict`, reads the age limit from skills-evals'
  fixture, and does no date arithmetic of its own.
- `test_the_cross_repo_condition_is_named_in_exactly_one_place` — the
  `reported-failure` predicate is a named constant in
  `scripts/account_zip_selection.py` and appears in the workflow only inside a
  comment.
- `test_a_passing_audit_never_removes_a_locally_stale_name` and
  `test_it_is_a_union_and_not_a_replacement` — the union, from both directions.
- `test_a_drifted_name_that_is_not_declared_is_reported_not_zipped` — the
  allowlist intersection.
- `TestADegradedAuditIsNeverMistakenForAPass` — the whole class.
- `test_the_recording_that_decides_staleness_is_a_salient_path` — the push
  filter bug fixed alongside this: `account-state.json` lives at the repo root
  deliberately (a copy inside a skill directory would be uploaded as part of
  that skill), matched neither glob, and a merged `record-account-upload` PR
  therefore never re-ran the workflow whose offer list it corrects.

## References

- [PR #118](https://github.com/Adam-S-Daniel/agentskills/pull/118) / commit
  `f65df0d` — the implementation this records.
- [`.github/workflows/account-skill-zips.yml`](../../.github/workflows/account-skill-zips.yml)
  and [`scripts/account_zip_selection.py`](../../scripts/account_zip_selection.py).
- [ADR 0002](0002-limit-account-store-to-repo-independent-skills.md) — why the
  account store is a one-way door, and why CI cannot see it.
- `sync-skills` SKILL.md §9 — the phone flow, the recording, and what a verdict
  does and does not mean.
- skills-evals `harness/propagation/account_store.py` (`freshness_verdict`),
  `evals/propagation/ROUTINE.md` (steps 4 and 5, the publish message, and the
  two blockers on a push listener), and `.github/workflows/propagation.yml`
  (the freshness gate reading the same artifact).
- [skills-evals#48](https://github.com/Adam-S-Daniel/skills-evals/issues/48) —
  the open drift tracking issue; [skills-evals#52](https://github.com/Adam-S-Daniel/skills-evals/pull/52)
  — the unmerged lifecycle workflow.
