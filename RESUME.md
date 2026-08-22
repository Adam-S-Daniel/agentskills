# Resume pointer — ultracode session, 2026-08-22

Snapshot of an in-flight session, pushed so the work survives container reaping.
Everything here is process artifact, NOT repo content. Do not merge this branch.

## What is already landed and needs nothing

- **skills-evals#48** — CLOSED. Account store repaired by hand; audit re-run here read
  `pass` (10 checked, 0 findings); published to `eval-results`; reactor closed it.
- **skills-evals#54** — MERGED. ROUTINE.md: the third Tier-3 binding loss, and the
  `unarchive_session` repair that works when `create_session` is unavailable.
- **Tier-3 Routine** — repaired and verified by effect twice. Trigger is now
  `trig_01AK5s6efLSzdHBSZhkx6KW1`, cron `0 5 * * *`, bound to the sourced session
  `session_01GJAYVjFux2xte99afwoFJB`, prompt corrected (no `issue: unavailable`
  clause, no agentskills#59 pointer).
- **_agent-guidance#64** (red nightly) — GREEN, verified by dispatch 32585595150.
  Merged repo-settings#25 and agentskills-private#10 to clear it.
- **agentskills** `claude/ultracode-workflow-testing-r31h75` (PUSHED, 2 commits):
  account-state.json recorded as `observed`; `.claude/worktrees/` gitignored.

## Branches pushed as backups — these carry the unmerged engineering

| remote ref | commits | suite | subject |
|---|---|---|---|
| `backup/a120-impl` | 17 | 1100 passed | agentskills#120, four deferred findings |
| `backup/ag58-generator` | 19 | 1007 passed | #58 generator half: `--only`, `--repin-source` |
| `backup/doctor-122-123` | 2 | in progress | agentskills#122 + #123 |
| `_agent-guidance` `claude/ultracode-workflow-testing-r31h75` | 2 | 533 passed | #58 bumper half + sweep stderr fix + ADR 0009 |

They are NOT the final state — four fix rounds were running when this was written.
Re-read each branch tip before trusting a count above.

## The verdict files here, and why they are the expensive part

`*-verdicts.json`, `*-reconciled.json`, `critique-*.json` are the output of repeated
adversarial rounds. Each defect carries a REPRODUCTION, usually a harness path under
this scratchpad. A fresh session can regenerate a patch cheaply; it cannot cheaply
re-derive why a given fix was wrong. Read these before re-fixing anything.

## Standing facts that cost time to establish

- **Every fix round so far has introduced new defects.** ag58 rounds 2, 3 and 4 each
  did; a120 rounds 1 and 2 each did. Matches ADR 0002's record for skills-bootstrap.sh
  (four rounds, two of which found defects the prior fix introduced). A green suite has
  been true at every one of those points and never meant done.
- **`)` and bash comments** — measured here, settling a disagreement between two agents:
  `)` opens a comment as an OPERATOR (case-pattern terminator, subshell close) but NOT
  when it closes `$( )`, because there it is inside a word. The rule is stateful.
      bash -c 'x=$(echo hi)#notcomment; echo "[$x]"'   -> [hi#notcomment]   not a comment
      bash -c 'case x in x)# c
        echo REACHED ;; esac'                          -> REACHED           is a comment
- **errexit and `||`** — an `echo` on the RIGHT of the final `||` is NOT exempt from
  errexit. The exemption covers the command BEFORE the final `&&`/`||`.
- **`create_session` is unavailable** on this surface — failed 3x today including a bare
  title-only call, with status.claude.com green. Second independent day after 08-21's
  ten attempts. Use `unarchive_session` instead; an archived session keeps its
  `session_context`, so a sourced publisher is woken rather than rebuilt.
- **`update_trigger` cannot re-point `persistent_session_id`** and refuses to edit the
  prompt of a Routine bound to another session — even one you created minutes earlier.
  Re-pointing is always create-then-delete.
- **#122 and #123 both reproduce in a second cloud session** — which settles #123's
  stated blocking question. `session-start-hook` is harness-seeded: lone SKILL.md, mode
  0600 root, frontmatter `name: startup-hook-skill` mismatching the basename, mtime
  BEFORE the hook's install cluster, absent from the 20-entry manifest.
- **Two concurrent `run-tests.sh` runs collide** on the shared `git config --global`
  identity and produce a log with two result blocks. Run it sequentially.
- **agentskills' `scripts/test_*.py` cannot fail when run directly** — no `__main__`
  block. The only real gate is
  `python3 -m pytest scripts/ plugins/*/skills/*/tests/ plugins/*/skills/*/scripts/ -q`.

## Open, and needing a credential this session lacks

- `jodidaniel/scratch-claude-002#16` — session connector refuses cross-tier adds
  (jodidaniel vs adam-s-daniel); org connector reads it but 403s on merge. Only warns.
- Root cause of the `repo-settings#25` sweep read failure — needs the agents-md-sync
  App's own token. `gh pr list` succeeded on the same repo in the same run while
  `gh pr view --json ...,statusCheckRollup,files` failed, which points at a field-level
  permission rather than repo access.

## Still to do

1. Finish the four fix rounds; keep rounds going while they keep finding things.
2. Graft `a120-impl`, `ag58-generator`, `doctor-122-123` onto
   `claude/ultracode-workflow-testing-r31h75` STRIPPING the `Co-Authored-By: Claude
   <model>` trailers (policy forbids a model identifier in any pushed artifact).
   Script: `scratchpad/graft.sh <repo> <base> <branch>`.
3. Amend `8750045`'s message to cite #124 by number — it claims a follow-up was "filed
   separately" and #124 is that filing.
4. `git worktree remove` the strays (`wt-base`, and the scratchpad worktrees).
5. Open PRs: agentskills FIRST, then _agent-guidance. The bumper soft-probes the
   generator's flags and must never hard-fail without them, but PR 2 landing first
   would advertise capability the generator lacks.
6. Close #120, #122, #123, #124, #125, #126, _agent-guidance#58, #65 with their PRs.
