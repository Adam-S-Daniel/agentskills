# Ecosystem state (as of 2026-07-07)

- Issue #18 (consolidation) is essentially done. The only open checkbox is the
  skills-evals quality badge, which needs real eval runs first
  (`python3 harness/run_eval.py evals/pin-actions-to-sha --arm both`).
- The scratch repos are NOT archived — user decision 2026-07-07: they stay
  active and participate in AGENTS.md sync like any consumer.
- Sync is multi-owner: `SYNC_OWNERS="Adam-S-Daniel jodidaniel"` (workflows set
  it; scripts default to single-owner when unset). Full fleet = 16 repos across
  both accounts; all converged (AGENTS.md + CLAUDE.md bridge) as of 2026-07-07.
  Only exclusions: the three civic-* repos (repos.yml in _agent-guidance).
- Claude Code does not read AGENTS.md natively — the sync engine creates a
  CLAUDE.md containing `@AGENTS.md` when absent. Repos with hand-written
  CLAUDE.md lacking the import (sync WARNs, never edits): GHA-bench,
  adamdaniel.ai, jodidaniel/scratch-claude-002.
- CI sync needs secret `AGENTS_SYNC_READWRITE_TOKEN` on _agent-guidance
  (classic PAT with repo scope for both-account coverage; fine-grained PATs are
  single-owner). Optional: `AGENTS_SYNC_READONLY_TOKEN` for drift-report
  private-repo coverage (falls back to github.token). Until the write secret
  exists, run sync locally: `GH_TOKEN=$(gh auth token) SYNC_OWNERS="Adam-S-Daniel
  jodidaniel" ./scripts/sync.sh` (needs yq).
- skills-evals harness: `--arm both` = real A/B via headless `claude -p`;
  `--arm objective-only` needs no API. Tests: `python3 test/run_tests.py`.
