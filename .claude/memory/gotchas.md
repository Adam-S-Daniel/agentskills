# Gotchas learned the hard way

- `gh api ... --jq '.filter'` on an HTTP error prints the RAW error JSON body to
  stdout (the jq filter is not applied) and exits 1. `cmd || true` therefore
  captures garbage — discard output on failure instead
  (`out=$(cmd) || out=""`). This silently broke sync.sh's default_sections.
- This repo disallows squash merges — use `gh pr merge --merge`.
- Skill install for evals: copy the NESTED `plugins/<name>/skills/<name>/` into
  `.claude/skills/<name>/`; copying the outer plugin dir buries SKILL.md and it
  silently never loads.
- Memory-path munging replaces both `/` AND `.` with `-`, so decoded paths are
  ambiguous (e.g. `adamdaniel.ai` vs `adamdaniel/ai`) — the
  `migrate-claude-memory` inventory marks such stores as decode-guesses.
- `autoMemoryDirectory` accepts only absolute or `~/` paths (no repo-relative);
  the in-repo pattern works because repos live at `~/repos/<name>` everywhere.
- claude-memory-map: the `@sparticuz/chromium` serverless test path is broken in
  v131 (`.default` removed); CI uses `npm run setup:browser` instead.
- sync.sh cannot recover a stale remote `agents-md-sync/update` branch (push is
  non-fast-forward and it deliberately never force-pushes). Recovery: open a PR
  from the stale branch and merge it (frees the name), then re-run sync. Do NOT
  add --force to sync.sh — it could discard reviewer commits on open PRs.
- The marketplace `renames` map is a one-way door: an object {old: new|null}
  (null = removed), chains followed to depth 16 by the 2.1.211 resolver,
  APPEND-ONLY forever. Many-to-one is fine; enable-state merges are
  first-wins (a disabled surviving plugin silently disables migrated-in
  skills — the fastmail/fastmail-identities caveat, ADR 0001).
- After pulling the bundle restructure on any machine, re-run `bash setup.sh`
  IMMEDIATELY: the global sync-skills pre-push hook still points at the old
  plugin path and fails EVERY `git push` from EVERY repo until re-registered.
- Expect a one-time `claude plugin install adam@agentskills` touch per
  machine (plugin-cache-miss after the rename migration); sync-skills flags
  all 17 skills changed once (hash-dedup makes the re-upload a no-op).
- gh CLI weirdness 2026-07-16: GitHub's /user REST endpoint 503'd for hours
  ("Unicorn!" HTML) making `gh auth status` claim the token was invalid while
  GraphQL and git push worked fine. Verify with `gh pr list` before
  re-authing. Also: raw curl to api.github.com without a User-Agent gets
  rejected at the edge.
- In _agent-guidance, `test/run-tests.sh` (test_drift_report) writes mock
  data into the real `drift-report.md` — `git checkout -- drift-report.md`
  before committing.
