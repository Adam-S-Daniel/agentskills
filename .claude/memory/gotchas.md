# Gotchas learned the hard way

- Memory-path munging replaces both `/` AND `.` with `-`, so decoded paths are
  ambiguous (e.g. `adamdaniel.ai` vs `adamdaniel/ai`) — the
  `migrate-claude-memory` inventory marks such stores as decode-guesses.
- claude-memory-map: the `@sparticuz/chromium` serverless test path is broken in
  v131 (`.default` removed); CI uses `npm run setup:browser` instead.
- Expect a one-time `claude plugin install adam@agentskills` touch per
  machine (plugin-cache-miss after the rename migration); sync-skills flags
  all 17 skills changed once (hash-dedup makes the re-upload a no-op).
- In _agent-guidance, `test/run-tests.sh` (test_drift_report) writes mock
  data into the real `drift-report.md` — `git checkout -- drift-report.md`
  before committing.
