# repo-settings config schema

`repo_settings.py` reads a YAML document describing the **desired** state of a
repo's Actions-security settings and default-branch protection. Branch
protection is expressed as a **GitHub repository ruleset** (the modern
replacement for classic branch protection, and the same primitive the
cms-platform-managed repos use). Two document shapes are accepted.

## Single-repo shape

Lives in a repo as `.github/repo-settings.yml`, or anywhere when passing
`--repo`.

```yaml
# Optional. If omitted, pass --repo owner/name on the command line.
repo: Adam-S-Daniel/agentskills

actions:
  # Require every `uses:` to be pinned to a full-length commit SHA.
  sha_pinning_required: true

  # Fork pull-request approval policy. Accepted values:
  #   first_time_contributors        (approval only for first-time contributors)
  #   all_external_contributors      (approval for ALL outside collaborators)
  # Omit the key, or set it to null, to leave the policy unmanaged.
  # NOTE: not applicable to PRIVATE repos -- the API returns 422; the engine
  # detects private repos and skips this automatically.
  fork_pr_approval: all_external_contributors

ruleset:
  # Omit the whole block, or set enabled: false, to leave protection unmanaged.
  # PUBLIC repos only -- private repos need GitHub Pro (API 403); the engine
  # detects private repos and skips this automatically.
  enabled: true

  # The managed ruleset is idempotent BY NAME: apply creates it if absent,
  # updates it in place if present, and never touches other rulesets.
  name: "default branch protection"

  # active = enforced; evaluate = dry-run/reporting; disabled = off.
  enforcement: active

  # Which branches the ruleset targets. Default: the repo's default branch
  # (via the ~DEFAULT_BRANCH special ref, so it follows a renamed default).
  # To target explicit branches instead:
  #   target_branches: [main, release]

  # null / omit -> the "require a pull request before merging" rule is left off.
  # a mapping   -> the rule is enabled.
  require_pull_request:
    required_approving_review_count: 0   # 0 = PR required, self-merge allowed
    dismiss_stale_reviews: false

  block_force_pushes: true               # adds the non_fast_forward rule
  block_deletions: true                  # adds the deletion rule

  # Check contexts that must pass before merge. Empty/omit = rule not added
  # (recommended unless you have stable, always-present check names).
  required_status_checks: []

  # true  = the repo Admin role bypasses the ruleset ("always") -- the human
  #         owner can still push directly; only non-admins (incl. the Actions
  #         bot) are forced through PRs.
  # false = nobody bypasses (stricter; matches the cms-platform `main` ruleset).
  admin_bypass: true
```

## Fleet / fan-out shape

One document describing many repos. `defaults:` is the baseline every repo
inherits; each entry may add `overrides:` (deep-merged over the defaults) or set
`manage: false` to exclude the repo (reported as excluded, not touched).

```yaml
defaults:
  actions:
    sha_pinning_required: true
    fork_pr_approval: all_external_contributors
  ruleset:
    enabled: true
    name: "default branch protection"
    enforcement: active
    require_pull_request:
      required_approving_review_count: 0
    block_force_pushes: true
    block_deletions: true
    admin_bypass: true

repos:
  # Inherits everything from defaults.
  - name: Adam-S-Daniel/agentskills

  # Private repo: the engine auto-skips fork_pr_approval and the ruleset.
  # Setting ruleset.enabled: false is optional but self-documenting.
  - name: Adam-S-Daniel/rss-inator
    overrides:
      ruleset: { enabled: false }

  # Scratch/experimental repo: keep the harmless Actions hardening, skip the
  # workflow-changing ruleset.
  - name: Adam-S-Daniel/scratch-jules-001
    overrides:
      ruleset: { enabled: false }

  # Excluded entirely (e.g. a fork, or a repo owned by another settings system).
  - name: Adam-S-Daniel/OctopusDeploy-Api
    manage: false
```

## Downgrade behavior (why one baseline works everywhere)

The engine introspects each repo and skips settings the repo cannot accept,
logging a `skip` with the reason:

| Condition | Skipped setting | API signal |
|-----------|-----------------|------------|
| Private repo | `actions.fork_pr_approval` | 422 "not allowed for private repositories" |
| Private repo without GitHub Pro | `ruleset` | 403 "Upgrade to GitHub Pro" |

## Fork-PR approval values (verified against the live API)

The correct enum values are the short forms returned by
`GET repos/{repo}/actions/permissions/fork-pr-contributor-approval`:

| GitHub UI label | API value |
|-----------------|-----------|
| Require approval for first-time contributors | `first_time_contributors` |
| **Require approval for all outside collaborators** | `all_external_contributors` |
