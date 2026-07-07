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
