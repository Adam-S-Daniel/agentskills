# Resume pointer — ultracode session, agentskills / _agent-guidance / skills-evals

Goal (user): "The issues in the screenshot are completed, as are any emergent issues,
recursively." — agentskills#120, skills-evals#48, _agent-guidance#58. Emergent issues are
completed in THIS session, not deferred (user, explicitly, twice).

## Done and closed
- **skills-evals#48** CLOSED. Tier-3 audit run in a session holding `~/.claude/skills/synced/`,
  `pass` / 10 checked / 0 findings, published to `eval-results`, `account-store-drift.yml`
  closed the issue at 15:04:29Z. `propagation.yml`'s gate passes again.
- **skills-evals#54** MERGED (ROUTINE.md: third binding loss + the `unarchive_session` repair;
  the prompt-correction record).
- **Tier-3 Routine repaired** — `trig_01AK5s6efLSzdHBSZhkx6KW1`, cron `0 5 * * *`, bound to
  sourced session `session_01GJAYVjFux2xte99afwoFJB`, prompt carries both ROUTINE.md corrections.
- **_agent-guidance#64** (red nightly) GREEN, verified by dispatch 32585595150.
- **agentskills#121** answered with a counter-measurement.

## Branches (all pushed to `backup/<name>` on Adam-S-Daniel/agentskills)
| branch | closes | state |
|---|---|---|
| `a120-impl` | #120, #124, #126 | round 5 landed, 1224 passed |
| `ag58-generator` | #58 (generator half), #125 | round 7 in flight; r6 = 1110 passed / 10 skipped |
| `doctor-122-123` | #122, #123 | round 5 in flight |
| `_agent-guidance` `claude/ultracode-workflow-testing-r31h75` | #58 (bumper half), _ag#65 | round 3 in flight; pushed at `c7e5569` |

## Remaining plan
1. Let the in-flight adversarial rounds settle. Stopping line: **no blocking defect and no
   user-visible should-fix**; surviving nits go into the PR body as known limitations.
2. `graft.sh <repo> <base> <branch>` cherry-picks each branch onto
   `claude/ultracode-workflow-testing-r31h75` **stripping the `Co-Authored-By: Claude <model>`
   trailer** — a model identifier must not reach a pushed artifact. The three agentskills
   branches touch disjoint files, so the graft is conflict-free.
3. Delete the `backup/*` refs once grafted: they carry the un-stripped trailers.
4. Remove the subagent worktrees, run the full suite once on the merged result.
5. Open PRs **agentskills first, then _agent-guidance** — merge order is load-bearing, because
   the bumper's degraded mode exists to survive the window where the generator is behind it.
6. Close #120, #122, #123, #124, #125, #126, #58, _ag#65.

## Measured facts worth keeping
- `)` opens a bash comment as an OPERATOR (case-pattern terminator, subshell close) but NOT when
  it closes `$( )`: `x=$(echo hi)#notcomment` -> `[hi#notcomment]`.
- An `echo` on the RIGHT of a final `||` is NOT exempt from errexit.
- Two concurrent `run-tests.sh` runs collide on the shared `git config --global` identity and
  produce a log with two result blocks. Run it sequentially; read the LAST block.
- `create_session` has been unavailable on two independent days (status pages green).
- `update_trigger` refuses to edit the prompt of a Routine bound to another session; replacing
  the binding needs `create_trigger` + `delete_trigger`.
- Pre-existing, filed: `--check`'s remediation omits `--registry`/`--bundles`, silently
  re-pointing a lock at `DEFAULT_REGISTRY`.

## Cannot complete (credential boundary, not deferral)
- `jodidaniel/scratch-claude-002#16` — the session connector refuses cross-tier `add_repo`; the
  org connector reads the repo but 403s on merge. It only warns; it blocks nothing.
- Root-causing `repo-settings#25`'s sweep read failure needs the agents-md-sync App's token.
