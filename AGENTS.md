<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->
<!-- Source: _agent-guidance -->
<!-- Sections: none -->

# AGENTS.md

> **Managed by [`_agent-guidance`].**
> Edit only below the `## Repo-specific additions` header.
> Everything above it will be overwritten on the next sync.

## General guidelines

- Read existing code before modifying it. Understand the patterns already in use.
- Keep changes minimal and focused — fix what was asked, nothing more.
- Do not add speculative features, premature abstractions, or unused helpers.
- Prefer editing existing files over creating new ones.
- Never commit secrets, credentials, or .env files.

## Code quality

- Follow the idioms and style already established in this repo.
- Write code that is clear enough to not need comments; add comments only when intent is non-obvious.
- Avoid introducing new dependencies unless strictly necessary.
- Every public interface change should include corresponding test updates.

## Security

- Validate all external input (user input, API responses, file contents).
- Never construct SQL, shell commands, or HTML by string concatenation with untrusted data.
- Use parameterized queries, shell arrays, and context-aware escaping respectively.
- Do not disable TLS verification, authentication, or CSRF protection.

## Testing

- Run the existing test suite before considering a task complete.
- New behavior requires new tests; bug fixes require regression tests.
- Tests should be deterministic — no sleeping, no network calls, no reliance on wall-clock time.

## Subagent delegation (model routing)

- Don't write code in the main loop: run the implementation in a subagent on an
  appropriately lower-power model (e.g. the Agent tool's `model` override in
  Claude Code; skip if the harness has no subagent support).
- Route by mechanicalness: smallest model (haiku-class) for exactly-specified
  edits — pin bumps, renames, config/doc tweaks; mid-tier (sonnet-class) for
  normal implementation from a clear spec.
- The main loop keeps root-cause investigation, architectural decisions,
  writing the spec, and review of the subagent's diff before commit.
- Escalate the model rather than ship a wrong diff when the task is genuinely
  subtle (cross-repo invariants, race conditions).
- Give the subagent a precise spec — files, exact changes, house style, the
  test command to run. Subagent output is gated by the same test/CI proof as
  any other change.

## Git practices

- Write concise commit messages that explain *why*, not just *what*.
- One logical change per commit.
- Do not amend published commits or force-push shared branches.

<!-- END MANAGED SECTION -->
## Repo-specific additions

<!-- Add your repo-specific agent guidance below this line -->
