---
name: github-actions-repo-settings
description: >
  Configure GitHub repository and organization settings to enforce Actions
  security policies: require actions to be pinned to full-length commit SHAs
  and require approval for all outside collaborators' fork pull-request
  workflow runs. Trigger when: setting up a new repo, running a security
  audit, onboarding a repo to org standards, or when asked to configure or
  harden Actions security settings. Trigger on mentions of "actions settings",
  "repo security settings", "fork approval", "outside collaborators",
  "actions policy", or "harden repo".
compatibility:
  tools:
    - GitHub CLI (gh)
  environment: any
---

# GitHub Actions Repo Settings

Configure repository-level and organization-level GitHub Actions security
settings via the GitHub REST API.

## 1. Settings to enforce

| # | Setting | Purpose |
|---|---------|---------|
| 1 | **Require actions to be pinned to a full-length commit SHA** | Prevents mutable tag references; mitigates supply-chain attacks |
| 2 | **Require approval for all outside collaborators** | Requires manual approval before fork PRs from non-collaborators can run workflows |

## 2. Setting 1 -- Require SHA pinning

### Check current state

```bash
gh api "repos/{owner}/{repo}/actions/permissions" --jq '.sha_pinning_required'
```

Returns `true` or `false`. For an organization:

```bash
gh api "orgs/{org}/actions/permissions" --jq '.sha_pinning_required'
```

### Enable for a single repository

```bash
gh api "repos/{owner}/{repo}/actions/permissions" \
  --method PUT \
  --field enabled=true \
  --field allowed_actions=all \
  --field sha_pinning_required=true
```

**Important:** You must include the `enabled` and `allowed_actions` fields in
the PUT body -- they are required. Set `allowed_actions` to the repo's current
value (check with GET first) to avoid changing it unintentionally. Common
values: `"all"`, `"local_only"`, `"selected"`.

To read the current `allowed_actions` value before writing:

```bash
gh api "repos/{owner}/{repo}/actions/permissions" \
  --jq '{enabled: .enabled, allowed_actions: .allowed_actions}'
```

### Enable at the organization level

```bash
gh api "orgs/{org}/actions/permissions" \
  --method PUT \
  --field enabled_repositories=all \
  --field allowed_actions=all \
  --field sha_pinning_required=true
```

As with repo-level, preserve the current `enabled_repositories` and
`allowed_actions` values. Read them first:

```bash
gh api "orgs/{org}/actions/permissions" \
  --jq '{enabled_repositories: .enabled_repositories, allowed_actions: .allowed_actions}'
```

## 3. Setting 2 -- Require approval for all outside collaborators

### Check current state

```bash
# Repository level
gh api "repos/{owner}/{repo}/actions/permissions/fork-pr-contributor-approval"

# Organization level
gh api "orgs/{org}/actions/permissions/fork-pr-contributor-approval"
```

### Discover allowed values

If unsure of the exact enum values accepted by the API, read the current value
first with the GET call above. The response contains the current
`approval_policy` string. Known values (verify against the latest API docs):

| UI label | API value |
|----------|-----------|
| Require approval for first-time contributors who are new to GitHub | `require_approval_for_new_users` |
| Require approval for first-time contributors | `require_approval_for_first_time_contributors` |
| **Require approval for all outside collaborators** | `require_approval_for_all_outside_collaborators` |

### Enable for a single repository

```bash
gh api "repos/{owner}/{repo}/actions/permissions/fork-pr-contributor-approval" \
  --method PUT \
  --input - <<< '{"approval_policy":"require_approval_for_all_outside_collaborators"}'
```

### Enable at the organization level

```bash
gh api "orgs/{org}/actions/permissions/fork-pr-contributor-approval" \
  --method PUT \
  --input - <<< '{"approval_policy":"require_approval_for_all_outside_collaborators"}'
```

### If the API value is different

If the PUT call fails with a validation error, the error message typically
lists the allowed enum values. Read the error, pick the correct value, and
retry. You can also check the current value on a repo that already has the
setting enabled (via the GitHub UI) to discover the correct API string.

## 4. Apply across all repos in an organization

### Step-by-step

```bash
# 1. List all repos in the org
repos=$(gh repo list {org} --limit 1000 --json nameWithOwner --jq '.[].nameWithOwner')

# 2. For each repo, apply both settings
for repo in $repos; do
  echo "Configuring $repo ..."

  # Read current allowed_actions to preserve it
  current=$(gh api "repos/$repo/actions/permissions" \
    --jq '{enabled: .enabled, allowed_actions: .allowed_actions}' 2>/dev/null)

  enabled=$(echo "$current" | jq -r '.enabled // true')
  allowed=$(echo "$current" | jq -r '.allowed_actions // "all"')

  # Setting 1: SHA pinning
  gh api "repos/$repo/actions/permissions" \
    --method PUT \
    --field enabled="$enabled" \
    --field allowed_actions="$allowed" \
    --field sha_pinning_required=true \
    2>/dev/null && echo "  SHA pinning: OK" || echo "  SHA pinning: FAILED"

  # Setting 2: Fork PR approval
  gh api "repos/$repo/actions/permissions/fork-pr-contributor-approval" \
    --method PUT \
    --input - <<< '{"approval_policy":"require_approval_for_all_outside_collaborators"}' \
    2>/dev/null && echo "  Fork approval: OK" || echo "  Fork approval: FAILED"

done
```

### Rate limiting

If you hit rate limits, add a delay:

```bash
sleep 1  # between repos
```

Check remaining quota:

```bash
gh api rate_limit --jq '.resources.core.remaining'
```

## 5. Verification

After applying settings, verify they took effect:

```bash
# For a single repo
gh api "repos/{owner}/{repo}/actions/permissions" \
  --jq '{sha_pinning_required: .sha_pinning_required}'

gh api "repos/{owner}/{repo}/actions/permissions/fork-pr-contributor-approval" \
  --jq '.approval_policy'
```

Expected output:
```
{ "sha_pinning_required": true }
require_approval_for_all_outside_collaborators
```

### Bulk verification

```bash
repos=$(gh repo list {org} --limit 1000 --json nameWithOwner --jq '.[].nameWithOwner')

for repo in $repos; do
  sha_pin=$(gh api "repos/$repo/actions/permissions" --jq '.sha_pinning_required' 2>/dev/null)
  fork_policy=$(gh api "repos/$repo/actions/permissions/fork-pr-contributor-approval" --jq '.approval_policy' 2>/dev/null)
  echo "$repo: sha_pinning=$sha_pin fork_approval=$fork_policy"
done
```

## 6. Fallback -- GitHub UI

If API endpoints change or return errors, configure via the UI:

1. Go to the repo on github.com
2. **Settings** > **Actions** > **General**
3. Under **Actions permissions**, check **Require actions to be pinned to a
   full-length commit SHA**
4. Under **Fork pull request workflows** > **Approval for running fork pull
   request workflows from outside collaborators**, select **Require approval
   for all outside collaborators**
5. Click **Save**

## 7. Permissions required

- **Repository settings**: `repo` scope (PAT) or repository admin access
- **Organization settings**: `admin:org` scope (PAT) or organization owner/admin role
- **GitHub App**: `actions` permission with `write` access

## 8. Related

After enabling the SHA pinning requirement, existing workflows with unpinned
actions will fail. Audit and fix them using the `pin-actions-to-sha` skill.
