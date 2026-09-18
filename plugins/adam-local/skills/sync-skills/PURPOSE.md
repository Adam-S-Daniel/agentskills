# Purpose — sync-skills

Maintenance context only; never loaded at inference.

## What it is for

`sync-skills` is the one path by which a skill in this registry reaches the
claude.ai account store — the channel that serves chat, Cowork, Claude in
Chrome and mobile, none of which can see a git checkout. ADR 0002 sets its
membership rule (personal, repo-independent skills only) and records that the
upload path has **no delete**, which is why `account-skills.txt` is a committed
declaration rather than a flag and why every verdict this tool prints has to
distinguish "checked and clean" from "could not check".

## Why it is `adam-local`

The upload needs a browser session on Adam's machine. ADR 0001's bundle split
puts machine-bound skills in `adam-local`; `adam` is the cloud-safe bundle.
The `--verify`, `--record-account-state` and `--account-drift` halves are not
machine-bound in the same way, which is the thread issue #157 pulled on.

## The failure shapes it was hardened against

Each of these is in the code as a comment beside the guard it produced; this
is the index.

- **A false clean.** An unreadable declaration, an empty one, a skill that was
  never compared — every one of them makes "nothing is missing" vacuously true.
  `account_upload_gap` takes `verify_ok` and `verified_names` as REQUIRED
  keyword arguments so no future caller can reach the clean arm by forgetting
  one.
- **A guessed repo path.** A Windows `--verify --all` resolved an empty
  `~/repos/agentskills`, enumerated zero skills and reported that `--all` had
  not been passed. There are now no built-in clone locations at all: a repo is
  named or is this file's own checkout.
- **A stale mirror read as a fresh one.** `--verify` against a pre-upload
  snapshot reports OK for an upload that never landed, so the mirror's
  `lastUpdated` is checked and a mirror older than 6h is refused.
- **`..` as a skill name.** It resolved to the mirror's PARENT, rglobbed the
  tree above it, returned a payload and so counted as PRESENT — the guard
  produced the one verdict it exists to withhold.
- **A moved mirror read as an empty account** — issue #157, the change this
  file arrived with. Claude Code 2.1.273+ buckets the mirror at
  `synced/<organizationUuid>_<accountUuid>/`; the flat path still resolved, to
  nothing, so `--verify` errored with a path no current CLI has and
  `--record-account-state` would have written a recording claiming every
  declared skill was never uploaded. `resolve_account_mirror` refuses rather
  than guessing when a machine has more than one bucket, for the same reason
  everything above exists: a confident wrong answer costs more here than an
  error does.

- **Refresh advice that could not work on either branch** — issue #158 and
  ADR 0010. `CLAUDE_CODE_SYNC_SKILLS=1 claude -p 'ok'` was the documented way
  to refresh the mirror. From CLI 2.1.273 a syncing terminal refreshes itself,
  making the line a no-op dressed as a prerequisite; and on a machine
  `setup.sh` has converged, ADR 0010 sets `syncClaudeAiSkills: false`, so
  there is no mirror for it to refresh at all. Advice that works on neither
  branch is worse than none: it sends the reader to re-run a command and
  conclude the tool is broken. The verify and record halves now run from a
  cloud session — which always has the mirror and cannot opt out — and the
  upload half still needs the laptop, because it needs a browser.

## Eval status

Deferred by decision, not by omission: skills-evals' `DESIGN.md` deliberate
non-coverage table lists `sync-skills` under "machine-bound (WSL/WPF/browser
surfaces); faking the surface costs more than the churn justifies today".
