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
  data into the real `drift-report.md`. That file is **gitignored and untracked**
  on `main` (ADR 0001 moved the published copy to the `drift-report-latest`
  results branch), so `git checkout -- drift-report.md` fails with *pathspec did
  not match any file(s) known to git*. Clean it with
  `git clean -fX -- drift-report.md` before committing.
- Hosted cloud sessions (2026-08-21): there is no `gh` CLI and the
  `GITHUB_TOKEN`/`GH_TOKEN` env vars are short placeholders, but a bare
  `curl https://api.github.com/...` is authenticated by the agent proxy and
  scoped to the session's authorized repos — agentskills and skills-evals
  answered 200, repo-settings/cms-platform/anthropics 403. So cross-repo claims
  can be checked from such a session with plain curl; a 403 there means "not in
  this session's repo set", not "does not exist".
