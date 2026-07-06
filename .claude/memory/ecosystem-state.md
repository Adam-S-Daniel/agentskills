# Ecosystem state (as of 2026-07-06)

- Issue #18 (consolidation) is essentially done: all phases complete except the
  skills-evals quality badge (needs real eval runs) and three manual items
  (archive 4 scratch repos; close 5 stale sync PRs on excluded repos; recreate
  the `ORG_JODIDANIEL_READWRITE_CONTENTS_PRS` PAT secret on `_agent-guidance`).
- Every in-scope Adam-S-Daniel repo now carries a managed `AGENTS.md` plus a
  `CLAUDE.md` containing `@AGENTS.md` (Claude Code does not read AGENTS.md
  natively — the bridge is created by `_agent-guidance/scripts/sync.sh`).
- Exceptions: `GHA-bench` and `adamdaniel.ai` have hand-written CLAUDE.md files
  WITHOUT the `@AGENTS.md` import (sync warns but never edits existing files).
- `_agent-guidance` sync excludes repos listed in its `repos.yml` (civic-*,
  scratch repos). CI sync fails fast until the PAT secret is recreated; run
  `./scripts/sync.sh` locally in the meantime (needs yq + gh).
- skills-evals harness: `python3 harness/run_eval.py evals/<skill> --arm both`
  runs real A/B evals (headless `claude -p`); `--arm objective-only` needs no
  API. Tests: `python3 test/run_tests.py` (hermetic, fake-claude).
