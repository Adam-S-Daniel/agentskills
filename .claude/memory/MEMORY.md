# Project memory index

Auto-memory index for the `agentskills` repo. Claude Code maintains this file;
topic files live alongside it in this directory.

**Public repo — no secrets, credentials, or PII in any memory file.** Memory
changes ship in commits like any other file: review diffs before pushing.

Durable, still-true lessons (standing traps that apply on every machine and
every surface) live in `AGENTS.md`'s "Repo-specific additions" section, not
here — that's what every agent reads regardless of harness. What remains in
this directory is genuinely volatile: open TODOs, per-machine convergence
status, and notes narrow enough that they aren't worth a permanent home yet.

<!-- Claude: add one line per topic file below, e.g. `- [Title](topic.md) — hook` -->
- [Ecosystem state](ecosystem-state.md) — issue #18 pending manual step, per-machine setup.sh convergence status, agentskills-private bundle status, fleet AGENTS.md sync history, skills-evals harness coverage, account-store drift loop's pending skills-evals half
- [Gotchas](gotchas.md) — memory-path munging ambiguity, claude-memory-map chromium test breakage, one-time plugin-install touch after a rename migration, _agent-guidance test mock-data trap, GitHub reach from a hosted cloud session
