# 0011. Remove `wj-next-break` from the registry and the account store

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Adam Daniel

## Context

ADR 0002 rejected deleting `wj-next-break` from this registry: the skill also
existed, unversioned, on the claude.ai account store, which has no delete API.
Removing the git copy while the account copy survived would only unversion
it — recreating the exact orphan condition PR #62 had just cleared. So the
skill stayed in git, visibly broken (it names two payload scripts,
`scripts/next_break.py` and `scripts/test_next_break.py`, that have never
existed), waived twice in `scripts/skills_waivers.yml` and tracked by #63.

The owner now directs full removal: "I want to remove wj-next-break
altogether" (2026-09-22).

## Decision

Remove `wj-next-break` from **both** places at once: the registry copy, in
this change, and the account copy, by hand, in claude.ai → Customize → Skills.
Doing both together is what ADR 0002's rejection was missing — that decision
only ever considered deleting the registry copy alone. Deleting both leaves no
orphan for either arm to carry.

## Consequences

- The marketplace `renames` map entry `"wj-next-break": "adam-local"` is
  **kept**. It is append-only (ADR 0001) and still correctly routes anyone
  installing the retired standalone plugin name into the `adam-local` bundle;
  nothing about deleting the skill's content invalidates that routing.
- The two `dangling-payload-ref` waivers in `scripts/skills_waivers.yml` are
  retired — there is no longer a `SKILL.md` for them to match, and a waiver
  matching nothing is itself a build error. #63, which tracked the broken
  payload references, no longer has a subject and should be closed.
- `sync_skills.py --account-drift` and `--verify` both key their checks off
  skills the registry still declares (`account-skills.txt`) and still holds a
  directory for (`_skill_dir`). With the skill removed from both, neither
  check has any way to see a lingering account copy, so nothing in this
  registry or its CI would notice if the UI deletion had not happened. It
  did: the owner deleted the account copy on 2026-09-23, and the laptop's
  synced account manifest (`~/.claude/skills/synced/<bucket>/manifest.json`,
  rewritten 2026-09-23 02:45 UTC) dropped from 21 skills to 20, with no
  `wj-next-break` entry or directory. `account-state.json`'s `wj-next-break`
  entry is removed too. Nothing reads it once the skill is undeclared, but
  that file records what the account holds, and the account no longer holds
  this.
- `setup.sh` only removes a stale per-agent skill link (`remove_stale_repo_link`)
  as a side effect of relinking a skill it still wants to link elsewhere; it
  never runs for a skill removed outright. A machine that previously ran
  `setup.sh` may still carry a dangling `~/.claude/skills/wj-next-break`
  symlink (or Windows junction) and needs it removed by hand.

## Alternatives considered

None beyond what ADR 0002 already weighed — the situation is unchanged except
for the owner's direction to also delete the account copy, which is the piece
ADR 0002 didn't have.

## References

- [ADR 0002](0002-limit-account-store-to-repo-independent-skills.md) — the
  original rejection this decision partially supersedes.
- [ADR 0001](0001-consolidate-plugins-into-bundles.md) — the append-only
  `renames` map.
- [Issue #63](https://github.com/Adam-S-Daniel/agentskills/issues/63) — the
  waived payload references this change retires.
