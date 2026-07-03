---
name: review-bash-ci-reliability
description: Review bash scripts for CI/CD reliability issues. Use when writing or reviewing shell scripts that run in GitHub Actions or other CI environments to catch silent failure patterns, missing error propagation, and environment assumptions.
---

# Review Bash Scripts for CI Reliability

Audit shell scripts for common patterns that cause silent failures in CI environments.

## Checklist

When reviewing or writing bash scripts that run in CI, check for these issues:

### 1. Error Propagation in Process Substitution

`set -euo pipefail` does NOT catch failures inside process substitution `<(...)`.

```bash
# DANGEROUS: if `some_command` fails, the error is silently swallowed
mapfile -t ITEMS < <(some_command | sort)

# SAFE: command substitution properly propagates errors under set -e
items_raw=$(some_command)
mapfile -t ITEMS < <(echo "$items_raw" | sort)
```

Search for: `< <(` patterns in scripts with `set -e`.

### 2. Git Identity in CI

CI runners (GitHub Actions, etc.) often lack global git `user.name` / `user.email`.
Any script that calls `git commit` outside the checkout directory will fail with exit code 128.

```bash
# Add at the top of test scripts that create git repos:
if ! git config --global user.name &>/dev/null; then
    git config --global user.name "ci-runner"
fi
if ! git config --global user.email &>/dev/null; then
    git config --global user.email "ci@localhost"
fi
```

### 3. Silent Success on Empty Results

Scripts that handle "no items found" by exiting 0 will show green checkmarks in CI even when the underlying command failed. Always distinguish between "genuinely found nothing" and "the discovery command itself failed."

```bash
# DANGEROUS: gh failure => empty array => "nothing to do" => exit 0
mapfile -t REPOS < <(gh repo list ...)
if [[ ${#REPOS[@]} -eq 0 ]]; then
    echo "No repos found."; exit 0
fi

# SAFE: fail explicitly if gh itself fails
repo_list=$(gh repo list ...) || { echo "ERROR: gh repo list failed"; exit 1; }
mapfile -t REPOS <<< "$repo_list"
```

### 4. Commands with Suppressed Errors

Review uses of `|| true`, `2>/dev/null`, and `|| :` to ensure they are intentional and not hiding real failures. These patterns are fine for optional/fallback logic but dangerous when applied to critical commands.

### 5. Commit Signing in CI

If the git config has `commit.gpgsign = true` globally, CI environments need either:
- `git config commit.gpgsign false` in test repos
- Or proper signing key setup

### 6. Missing Dependencies

CI scripts may assume tools are installed. Verify that all required tools (yq, jq, gh, etc.) are either pre-installed on the runner or explicitly installed in the workflow.

## How to Use This Skill

1. Find all shell scripts: `find . -name '*.sh' -type f`
2. For each script with `set -e`, grep for `< <(` patterns
3. For each script that uses `git commit`, check for user config
4. For each script that exits 0 on "empty results," verify the discovery command errors are caught
5. Run scripts locally to validate they pass before pushing
