# 0002. Limit the claude.ai account store to personal, repo-independent skills

- **Status:** Accepted
- **Date:** 2026-08-14
- **Deciders:** Adam Daniel

## Context

The claude.ai account skill store is the only channel that reaches chat, Cowork,
Claude in Chrome and mobile — surfaces where no marketplace plugin and no
`SessionStart` hook runs. It is also the channel with the fewest controls: no
scoping, no delete, and until recently no way to see what it held.

**Account skills cannot be scoped to a repo, and the reason is structural rather
than a settings gap.** Two independent measurements (C8 in
[`docs/experiments/E2-sessionstart-skill-bootstrap.md`](../experiments/E2-sessionstart-skill-bootstrap.md)):

- every per-skill record in `~/.claude/skills/synced/manifest.json` carries
  exactly five fields — `description`, `name`, `skillId`, `source`, `updatedAt`
  — and nothing scope-shaped. Whatever claude.ai may express server-side, Claude
  Code has no channel to receive a scope on;
- the store does not vary by project. Syncing from two different repos produced
  byte-identical stores, because the sync is keyed to `$HOME`, not to the
  working directory.

The word "project" does two jobs here, which is what kept the question feeling
open: a claude.ai **Project** is a chat container, a Claude Code **project** is a
repo directory. Even per-Project attachment in the chat product — a different
axis, and not something we have verified exists — could not scope a Claude Code
session running in a git repo.

This is a **stronger** reason than name precedence (C3: personal
`~/.claude/skills/` shadows project `.claude/skills/`), which is the argument
usually reached for first. Precedence could be engineered around by renaming.
This cannot.

**The channel silently accumulated orphans.** `ocr-pdfs` and `pdf-ocr-audit`
existed *only* on claude.ai from April until PR #62, with zero git history;
`pdf-ocr-audit/scripts/check_ocr.py` was ~5 KB of working Python one deletion
away from being unrecoverable. Nothing surfaced them, because nothing compared
the account store to the registry. The uploader made it worse: `sync-skills`
§6's single-file fallback truncated multi-file skills to just `SKILL.md` and
returned no error, so a lossy upload looked like a successful one. PR #61 fixed
the fallback and added `--verify`.

**Nothing removes a skill from the account.** The API `sync-skills` drives
uploads only; deletions are a manual action in the claude.ai UI. Dropping a
skill from git therefore does not drop it from the account — it only makes the
live copy unversioned again, which is precisely the orphan condition above.

One thing the channel does *not* get wrong, recorded because it was an open
question from cms-platform#249: `CLAUDE_CODE_SYNC_SKILLS` **does** prune upstream
deletions. After three skills were deleted on claude.ai, one refresh removed all
three locally and dropped the manifest from 11 custom skills to 8. Stale copies
do not accumulate.

All of the above is observed behaviour on Claude Code 2.1.231 as of 2026-08-13,
not documented by Anthropic. It can change; re-measure before relying on it in a
new design.

## Decision

Treat the account store as a **personal, account-wide, desktop-surface channel**.
It carries only skills useful in *every* context the account touches — chat,
Cowork, mobile — that do not depend on a particular repo, and everything on it is
version-controlled in this registry first. Repo-scoped and platform-scoped skills
are never pushed there.

Under that rule two open questions are settled (#63): the PDF trio — `ocr-pdfs`,
`pdf-ocr-audit`, `rename-pdfs` — is kept in full, and `wj-next-break` is descoped
without being deleted.

## Consequences

- **The PDF trio is the archetype of what the channel is for**: desktop document
  work, repo-independent, reached from chat and Cowork and not only from a
  terminal. It is also **one decision rather than three**. `rename-pdfs`
  references the other two in four places — its `description` ("Use after running
  `ocr-pdfs`"), the "natural follow-up to `ocr-pdfs`" line, the `*-needsocr.pdf`
  backup rule, and the branch that acts on a `pdf-ocr-audit` verdict — so
  dropping two while keeping it would leave it documenting a workflow that no
  longer exists. All three are now version-controlled (#62), `--verify`-covered
  (#61) and in the conformance census (#55).

- **A known-broken skill stays live on the account, and that is the accepted
  cost.** Owner directive, 2026-08-14: "Forget the WJ bell schedule skill" — no
  payload research, no rewrite. `wj-next-break` tells the agent to run
  `scripts/next_break.py` and `scripts/test_next_break.py`, neither of which has
  ever existed in its directory, and it stays that way. Anyone who activates it
  from chat or mobile gets an agent reaching for a script that isn't there. It is
  not deleted from git because deleting it there would not delete the account
  copy — it would only unversion it, re-creating the exact condition PR #62 just
  cleared. Its two code-block payload references are declared in
  `scripts/skills_waivers.yml` against #63 (the third, `references/bell_schedules.json`,
  is prose-only and does not gate), so the census prints them under WAIVED on
  every run instead of hiding them.

- **Repo-scoped skills stay on channels that can scope them**, so the account
  store is not a fallback when a plugin install or the bootstrap hook is
  inconvenient. The cost of getting that wrong is measurable, not theoretical:
  `claude plugin details adam` reports ~1,479 always-on tokens for 8 skills
  (~185/skill), and account-synced skills feed the same per-session skill
  listing. A platform bundle on the account would be paid in every unrelated
  session while being outranked by the marketplace bundle in the repos it was
  written for — and #54 records the further risk that at the default context
  budget the least-used descriptions are silently dropped, so noise on the
  account can make a genuinely useful skill untriggerable.

- **Uploading is close to a one-way door.** With no delete in the upload path,
  "push it and see" commits to keeping the skill in git indefinitely; a manual
  UI deletion is possible but nothing in CI can assert it happened.

- **The account arm stays the least observable one.** `check_skills.py` reads
  filesystem registries and cannot see claude.ai, so coverage there depends on a
  human running `--verify` from the laptop. This ADR's rule is enforced by
  habit on that arm, not by a gate.

## Alternatives considered

**Push the platform / `adam` bundle to the account store.** Rejected: account
skills cannot be repo-scoped, so every CI and workflow skill would load in every
unrelated repo as pure context cost, while being outranked by the marketplace
bundle exactly where it is useful.

**Drop the PDF trio entirely.** Rejected: it is real desktop capability on the
one surface this channel exists to serve, and dropping it would also require
stripping four `ocr-pdfs` / `pdf-ocr-audit` references out of `rename-pdfs`.

**Drop `ocr-pdfs` + `pdf-ocr-audit` and keep `rename-pdfs`.** Rejected
explicitly — #63 names this as the one option that should not be taken. The
rescue has already happened, so it saves nothing, and it leaves `rename-pdfs`
describing a two-step workflow whose first step is gone.

**Delete `wj-next-break` from the registry.** Rejected: the account copy would
survive the deletion and become unversioned, so the "cleanup" would recreate the
orphan class this ADR exists to prevent.

## How to verify

- **Registry arm:** `python3 scripts/check_skills.py` — the cross-registry
  census. It fails on a dangling payload reference or an undeclared duplicate,
  and a waiver matching nothing is itself an error, so the two `wj-next-break`
  waivers cannot rot: repairing the skill makes them stale and CI then forces
  their deletion.
- **Account arm:** refresh the local mirror with
  `CLAUDE_CODE_SYNC_SKILLS=1 claude -p 'ok'`, then run
  `plugins/adam-local/skills/sync-skills/sync_skills.py --verify` (PR #61). It
  compares each account copy's file set against what the uploader would send and
  exits non-zero on any `MISMATCH` — the check that would have caught the
  truncation. It needs Adam's laptop; CI cannot reach the account store.

## References

- [Issue #63](https://github.com/Adam-S-Daniel/agentskills/issues/63) — the two
  rulings recorded here.
- [Issue #54](https://github.com/Adam-S-Daniel/agentskills/issues/54) §6 (what we
  deliberately will not do) and §9 (record C8 in an ADR).
- [PR #61](https://github.com/Adam-S-Daniel/agentskills/pull/61) — fallback
  truncation fix plus `--verify`.
- [PR #62](https://github.com/Adam-S-Daniel/agentskills/pull/62) — the two
  orphaned PDF skills brought under version control.
- [Issue #55](https://github.com/Adam-S-Daniel/agentskills/issues/55) — the
  conformance + cross-registry census.
- [`docs/experiments/E2-sessionstart-skill-bootstrap.md`](../experiments/E2-sessionstart-skill-bootstrap.md)
  — C3 (precedence), C6 (token cost), C8 (structural non-scopability).
- [`scripts/skills_waivers.yml`](../../scripts/skills_waivers.yml) — the declared
  `wj-next-break` exemptions.
- [ADR 0001](0001-consolidate-plugins-into-bundles.md) — the bundle split. #54 §9
  asked for C8 "in ADR 0001"; ADRs here are append-only, so it lands as a new one.
- [cms-platform#249](https://github.com/Adam-S-Daniel/cms-platform/issues/249) —
  where the `CLAUDE_CODE_SYNC_SKILLS` pruning question came from.
