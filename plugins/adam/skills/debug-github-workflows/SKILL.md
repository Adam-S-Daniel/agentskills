---
name: debug-github-workflows
description: Debugging GitHub Actions workflow failures. Use when workflows are failing, showing unexpected results, or when you need to read workflow run logs and diagnose CI/CD issues.
---

# Debug GitHub Actions Workflows

Guide for diagnosing and fixing GitHub Actions workflow failures.

## Prerequisites: Installing gh CLI

The `gh` CLI is often not pre-installed in remote environments. Install it directly:

```bash
curl -sL https://github.com/cli/cli/releases/download/v2.67.0/gh_2.67.0_linux_amd64.tar.gz | tar xz -C /tmp
export PATH="/tmp/gh_2.67.0_linux_amd64/bin:$PATH"
```

If `gh` is unavailable or unauthenticated, use the GitHub API via `WebFetch`:

```
WebFetch: https://api.github.com/repos/{owner}/{repo}/actions/runs
WebFetch: https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs
WebFetch: https://api.github.com/repos/{owner}/{repo}/check-runs/{job_id}/annotations
```

## Workflow: Diagnosing Failures

### 1. List All Workflows and Runs

- Check `.github/workflows/` for workflow files — but also check OTHER BRANCHES, not just the current one. A workflow may exist only on a feature branch.
- Use `WebFetch` on `https://github.com/{owner}/{repo}/actions` to see all workflow runs and their statuses.
- Use the API `https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs` to get detailed job/step information including step-level conclusions.

### 2. Read Actual Logs — Don't Trust Status Badges

**CRITICAL**: A workflow showing "success" does NOT mean it actually succeeded. Common false-success patterns:

- **Process substitution silently swallows errors**: `mapfile -t ARR < <(command_that_fails)` will NOT trigger `set -e`. The array will simply be empty and the script continues.
- **Scripts that handle empty results gracefully**: If a script says "no items found, exiting" with `exit 0`, the workflow shows success even though it did nothing useful.
- **Commands with `|| true` or `2>/dev/null`**: Error output is suppressed and non-zero exits are masked.

Always look at:
1. Step durations — suspiciously fast steps (0-1 seconds) often indicate silent failures
2. Annotations via the API (contains error messages)
3. The actual script logic to understand how errors propagate

### 3. Check for Annotations

```
WebFetch: https://api.github.com/repos/{owner}/{repo}/check-runs/{job_id}/annotations
```

Annotations contain the actual error messages (e.g., "Process completed with exit code 128").

### 4. Run Tests Locally

Always try to reproduce failures locally before making changes — find and run the
repo's own test entrypoint (e.g. `test/run-tests.sh`, `make test`, or the command
the failing workflow step invokes).

## Common Failure Patterns

### Process Substitution Does Not Propagate Errors

**Problem**: `set -euo pipefail` does NOT catch failures inside `<(...)` process substitutions.

```bash
# BAD: gh failure is silently swallowed, REPOS becomes empty array
mapfile -t REPOS < <(gh repo list "$ORG" --json nameWithOwner --jq '.[].nameWithOwner')

# GOOD: command substitution propagates errors under set -e
repo_list_raw=$(gh repo list "$ORG" --json nameWithOwner --jq '.[].nameWithOwner')
mapfile -t REPOS <<< "$repo_list_raw"
```

### Git Operations Fail in CI (Exit Code 128)

**Problem**: `git commit` requires `user.name` and `user.email`. Local machines have these in `~/.gitconfig`, but CI runners often don't.

**Fix**: Set git identity before any git operations in test scripts:
```bash
if ! git config --global user.name &>/dev/null; then
    git config --global user.name "test-runner"
fi
if ! git config --global user.email &>/dev/null; then
    git config --global user.email "test@localhost"
fi
```

### Workflow Files on Wrong Branch

Workflows may exist only on a feature branch, not on `main`. Always check:
```bash
git fetch origin <branch>
git show origin/<branch>:.github/workflows/
```

### GitHub Token / Auth Issues

If `gh` commands fail silently in CI:
- Verify the secret name matches: `${{ secrets.SECRET_NAME }}`
- Check `permissions:` in the workflow YAML
- Ensure the token has correct scopes (repo, read:org, etc.)
