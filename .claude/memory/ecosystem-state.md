# Ecosystem state (as of 2026-07-16)

- **Bundle restructure SHIPPED** (#41): 17 skills in 3 bundles — `adam` (7,
  default-on), `adam-local` (7), `fastmail` (3). Invocation `/adam:<skill>`.
  Skill dir basenames are stable keys (setup.sh links, claude.ai uploads);
  marketplace `renames` map is append-only forever. ADR: docs/decisions/0001.
- **Issue #18 is fully closed out**: the last checkbox (eval quality signal)
  shipped via skills-evals eval.yml (weekly Mon 07:00 UTC + dispatch) and the
  README badge (#43). PENDING MANUAL STEP: create a spend-capped key
  (dedicated Console workspace, ~$10/mo limit) and
  `gh secret set ANTHROPIC_API_KEY -R Adam-S-Daniel/skills-evals`, then one
  workflow_dispatch. Until then the badge shows "no data". Arms pinned to
  claude-sonnet-5 (ceiling-effect avoidance), judge claude-opus-4-8.
- **E1 cloud-install experiment (2026-07-16): NO-GO.** Repo-declared
  extraKnownMarketplaces/enabledPlugins do NOT install in claude.ai/code
  cloud sessions (two live probes; matches anthropics/claude-code#32606 and
  #13096). A SessionStart hook installs successfully without a trust prompt
  but AFTER the skill registry is built — skills never load in ephemeral
  cloud containers. Fleet settings sync therefore DEFERRED; evidence in ADR
  0001. Re-test when Anthropic fixes install ordering.
- **Local convergence**: setup.sh now also deep-merges marketplace
  registration (agentskills + agentskills-private, autoUpdate:true — field
  verified in the 2.1.211 binary) and enabledPlugins["adam@agentskills"]
  into ~/.claude/settings.json, idempotently. WSL machine converged and
  verified (adam installed, /plugin works). WINDOWS PENDING: run
  `bash setup.sh` in Git Bash (also fixes the stale global pre-push hook).
- **agentskills-private**: `adam-private` bundle scaffolded (private#5),
  marketplace entry live, no skills yet.
- **Fleet**: AGENTS.md "## Skills ecosystem" section synced to all 17 repos
  across both accounts (2026-07-16, _agent-guidance#23); the original
  fleet-settings-block plan and its drift column were dropped per E1.
- skills-evals harness is dual-layout (glob plugins/*/skills/<skill>) and
  fully hermetic (47 tests, verified under unshare -rn); real evals are the
  only network path.
