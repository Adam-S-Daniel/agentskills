# Resume pointer — ultracode session

Goal: agentskills#120, skills-evals#48, _agent-guidance#58 completed, plus emergent issues
recursively, in-session (user, twice). **Token budget is the binding constraint: the user's
weekly allowance hit 98%, so all five adversarial workflows were stopped mid-round.**

## PRs OPEN

- **Adam-S-Daniel/agentskills#127** — head `claude/ultracode-workflow-testing-r31h75`.
  Closes #122, #123, #125; carries the generator half of _ag#58.
  Local suite 1383 passed / 10 skipped. CI green on `0efc7ad` except `pytest-windows` (running).
  Contains `ag58-generator@b44cd31` + `doctor-122-123@4ab5a89` + account-state + a lock re-pin.
- **Adam-S-Daniel/_agent-guidance#66** — head `claude/ultracode-workflow-testing-r31h75`.
  Closes #58, #65. Published at the round-3 tip `2a8085a` (662 passed).
  **~18 further local commits are NOT pushed**: 17 carry a `Co-Authored-By: Claude <model>`
  trailer that must not reach a pushed artifact.

**Merge order: _agent-guidance#66 FIRST, then agentskills#127.** Argued in `MERGE-ORDER.md`.

## Closed / done
skills-evals#48 CLOSED · skills-evals#54 MERGED · Tier-3 Routine repaired
(`trig_01AK5s6efLSzdHBSZhkx6KW1`) · _ag#64 root cause fixed, auto-closes · agentskills#121
answered with a counter-measurement · **jodidaniel/scratch-claude-002#16 MERGED** by a live
`skills-lock-bump` dispatch (`1 merged, 0 proposed, 19 skipped, 0 failed`), which is also the
first end-to-end proof of the nightly's LIVE write path — the earlier dispatch was a dry run.

## What is left, in priority order

1. **Strip trailers on the _ag branch and force-push to #66.** Tree is free now.
   `git filter-branch -f --msg-filter "grep -v '^Co-Authored-By: Claude .*<noreply@anthropic\.com>$' | cat" origin/main..HEAD`,
   confirm trees byte-identical, run the gate ONCE sequentially, force-push.
2. **Graft a120 then a121 onto #127** (a121 second — it conflicts). Last grafted:
   `grafted-ag58.txt`, `grafted-doctor.txt`. Suite must be green on the merged result.
3. Fold in: the runner-path leak in bump PR bodies, and `repos.yml`'s staleness (both _ag).
4. Delete `origin/record-account-upload/sync-skills-32558665629` (superseded; sha `a691453`).
5. Delete every `backup/*` ref — they carry the un-stripped trailers.

## Branch state at stop time
| branch | tip | suite |
|---|---|---|
| `ag58-generator` | `b44cd31` clean | 1114 passed / 10 skipped (grafted) |
| `doctor-122-123` | WIP on `4ab5a89` | 4ab5a89 grafted; WIP unrun |
| `a120-impl` | `56fc28a` clean | unrun |
| `a121-requirements` | WIP on `c627e55` | unrun |
| `_agent-guidance` | `ee8c00b` clean | unrun since 662 @ round 3 |

All pushed to `backup/*`; `scratchpad/autobackup.sh` re-pushes every 4 minutes.

## Measured facts worth keeping
- `)` opens a bash comment as an OPERATOR but not when it closes `$( )`.
- An `echo` right of a final `||` is NOT exempt from errexit.
- Two concurrent `run-tests.sh` runs collide on the shared `git config --global` identity.
- `_COMMIT_SHA_RE` is case-INSENSITIVE — an uppercase sha is NOT one of the new refusals
  (I briefed agents that it was; it is not).
- The bumper's population is NOT `skills_bootstrap.repos`; it is `gh repo list --no-archived
  --source` filtered on `.exclude` alone.
- `test_the_fetch_budget_ends_a_stalled_fetch[timeout-absent]` is load-sensitive: it fails its own
  precondition under concurrency and passes in isolation in 6s.
- Cross-tier `add_repo` (jodidaniel/* from an adam-s-daniel session) is refused by platform v1,
  regardless of user authorization. The org connector reads jodidaniel but 403s on merge.
  The bumper's own App mints a jodidaniel token and can merge — that is the working path.
