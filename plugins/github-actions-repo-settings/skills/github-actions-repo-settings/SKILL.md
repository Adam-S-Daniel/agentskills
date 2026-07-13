---
name: github-actions-repo-settings
description: >
  Configure and enforce GitHub repository security settings as code: require
  actions to be pinned to full-length commit SHAs, require approval for all
  outside collaborators' fork pull-request workflow runs, and protect the
  default branch via a repository ruleset. Includes a generate/diff/apply engine
  (introspect current state -> emit YAML; detect drift; apply desired state) and
  a central fan-out workflow to enforce a baseline across many repos. Trigger
  when: setting up a new repo, running a security audit, onboarding a repo to org
  standards, enforcing settings across a fleet, or when asked to configure or
  harden Actions security settings. Trigger on mentions of "actions settings",
  "repo security settings", "repo settings as code", "settings drift", "fork
  approval", "outside collaborators", "actions policy", "branch protection",
  "ruleset", or "harden repo".
compatibility:
  tools:
    - GitHub CLI (gh)
    - Python 3 with PyYAML (for the settings-as-code engine)
  environment: any
---

# GitHub Actions Repo Settings

Configure and enforce GitHub repository security settings. Two ways to use this:

1. **Settings-as-code (recommended)** -- describe desired state in a YAML file
   and let `scripts/repo_settings.py` introspect, diff, and apply it, for a
   single repo or a whole fleet. See section 1.
2. **Manual API recipes** -- one-off `gh api` calls for each setting, plus a UI
   fallback. See sections 3-6.

## Settings enforced

| # | Setting | Purpose | API |
|---|---------|---------|-----|
| 1 | **Require actions pinned to a full-length SHA** | Prevents mutable tag refs; mitigates supply-chain attacks | `repos/{repo}/actions/permissions` |
| 2 | **Require approval for all outside collaborators** | Manual approval before fork PRs from non-collaborators run workflows | `.../actions/permissions/fork-pr-contributor-approval` |
| 3 | **Default-branch protection (ruleset)** | Require PRs, block force-push/deletion on the default branch | `repos/{repo}/rulesets` |

Setting 3 uses a **repository ruleset** rather than classic branch protection,
so the fleet speaks the same primitive as repos managed by other ruleset-based
systems (e.g. cms-platform).

## Key API facts (verified against the live API)

- **Fork-PR approval enum values are the short forms** returned by the GET:
  `first_time_contributors` and **`all_external_contributors`** (= the UI's
  "all outside collaborators"). Older docs showing
  `require_approval_for_all_outside_collaborators` are **wrong**.
- **Private repos** cannot use fork-PR approval (API `422`) and cannot use
  rulesets/branch protection without GitHub Pro (API `403`). Only SHA pinning
  applies to a private repo on a free plan.
- **`Adam-S-Daniel` and `jodidaniel` are user accounts, not orgs** -- there are
  no org-level settings to configure; everything is repo-level.
- Writing any of these needs **repo-admin** (fine-grained PAT with
  "Administration: read and write", classic PAT with `repo`, or a GitHub App).
  The default Actions `GITHUB_TOKEN` **cannot** change repo settings.

---

## 1. Settings-as-code engine

`scripts/repo_settings.py` drives everything through `gh` (so it uses your local
`gh auth`, or `GH_TOKEN` in CI). Install the one dependency once:

```bash
pip install pyyaml
```

### Generate -- introspect current state into a config

```bash
python scripts/repo_settings.py generate --repo Adam-S-Daniel/agentskills > repo-settings.yml
```

Emits a YAML document (single-repo shape) describing the repo's current SHA
pinning, fork-PR approval, and default-branch ruleset. Edit it to describe the
**desired** state, then diff/apply.

### Diff -- drift report (no changes)

```bash
python scripts/repo_settings.py diff --config repo-settings.yml
# exit 0 = no drift, 1 = drift, 2 = error
```

Prints, per setting, `ok` / `DRIFT` (with `from:` -> `to:`) / `skip` (with the
reason, e.g. private-repo downgrade) / `ERROR`.

### Apply -- converge to desired state

```bash
python scripts/repo_settings.py apply --config repo-settings.yml            # apply
python scripts/repo_settings.py apply --config repo-settings.yml --dry-run  # preview
```

Applies only the settings that drift; prints `CHANGED` for each. The managed
ruleset is idempotent **by name**: apply creates it if absent, updates it in
place if present, and never touches other rulesets. Re-running a converged
config is a clean no-op.

### Config schema

See [assets/repo-settings.schema.md](assets/repo-settings.schema.md) for the
full schema. Two shapes:

- **Single-repo** -- top-level `actions:` / `ruleset:` blocks (+ optional
  `repo:`), for one repo.
- **Fleet** -- a `repos:` list with a shared `defaults:` baseline, per-repo
  `overrides:` (deep-merged), and `manage: false` to exclude a repo.

The engine **auto-downgrades**: on a private repo it skips fork-PR approval and
the ruleset (with a logged reason), so one `defaults:` baseline targets public
and private repos alike.

---

## 2. Enforcing a baseline across a fleet (central fan-out)

For managing many repos from one place, use the fleet config shape plus the
fan-out workflow. Worked example: [assets/fleet-config.example.yml](assets/fleet-config.example.yml)
(the live Adam-S-Daniel + jodidaniel fleet).

### Local one-shot

```bash
python scripts/repo_settings.py diff  --config fleet.yml   # audit the whole fleet
python scripts/repo_settings.py apply --config fleet.yml   # enforce it
```

### Ongoing enforcement in CI

Copy into the **central repo** you choose to own fleet settings (a `.github`
repo or a dedicated `repo-settings` repo):

```
scripts/repo_settings.py                        # from this skill
repo-settings/fleet.yml                          # your fleet config
.github/workflows/repo-settings.yml              # from assets/workflows/repo-settings-fanout.yml
```

Store a repo-admin PAT/App token as the `REPO_ADMIN_TOKEN` secret. The workflow
([assets/workflows/repo-settings-fanout.yml](assets/workflows/repo-settings-fanout.yml)):

- **pull_request** touching the config/script -> drift report only, fails the
  check if there is drift (so review shows what would change);
- **push to main** / **weekly schedule** / **manual dispatch** -> apply.

### How the fleet was classified

Non-standard settings are sometimes deliberate, so the fleet was classified with
an adversarial, per-repo workflow-safety audit before applying the ruleset. The
one failure mode that matters for the PR-required ruleset: a workflow step where
`github-actions[bot]` / `GITHUB_TOKEN` (a non-admin) pushes/force-pushes/deletes
on the repo's **own default branch** -- that push is blocked and the job fails.
Rules of thumb used:

- **public, no workflow pushes to the default branch** -> full baseline;
- **private** -> SHA pinning only (fork approval + ruleset unavailable);
- **scratch/experimental** -> Actions hardening only, no ruleset;
- **fork, or owned by another settings system** -> excluded (`manage: false`);
- **a workflow pushes to its own default branch** -> hold the ruleset until the
  workflow is converted to open a PR (e.g. `peter-evans/create-pull-request`).
  (In this fleet, `_agent-guidance`'s nightly `drift-report.yml` triggered this
  hold.)

---

## 3. CMS platform (cms-platform) and its consumers

`cms-platform` and the sites that consume it (`adamdaniel.ai`,
`jodidaniel.com`) manage their **own** settings-as-code from the platform: a
`repo-settings.yml` manifest + `scripts/audit-repo-settings.js`, propagated to
consumers when sites are scaffolded/re-synced, using **rulesets** (landing via
cms-platform PR #168, `feat/109-repo-settings-as-code`).

**These repos are excluded from the fan-out** (`manage: false`). Reason: the
fan-out and the platform would otherwise be two independent sources of truth for
branch protection, and GitHub enforces the **union** of all rulesets/protections
-- so a second system layering its own ruleset would create drift the platform's
audit is blind to. Branch protection for these three repos is owned by the
platform.

**Known gap to close in the platform:** PR #168 manages repo flags +
branch-protection rulesets but **not** the two Actions-permissions settings this
skill enforces (`sha_pinning_required`, fork-PR approval). To make the platform
the single source of truth, add an `actions_permissions` block to its
`repo-settings.yml` and the matching GET/PUT (`actions/permissions` and
`.../fork-pr-contributor-approval`, guarding the fork endpoint against 422 on
private repos) to `audit-repo-settings.js`, plus fixtures/lints. Do **not** let
the fan-out manage these repos to cover the gap.

**Divergence to be aware of:** the platform's `main` ruleset uses
`bypass_actors: []` (nobody, not even the owner, direct-pushes to main -- safe
there because every change lands via PR + auto-merge). The fan-out default uses
`admin_bypass: true` (owner can still direct-push). Both use
`required_approving_review_count: 0`, which is **required** -- the platform's bot
auto-merge chain deadlocks if any approval is required.

---

## 4. Manual recipe -- Setting 1 (SHA pinning)

### Check
```bash
gh api "repos/{owner}/{repo}/actions/permissions" --jq '.sha_pinning_required'
```

### Enable (repo)
```bash
gh api "repos/{owner}/{repo}/actions/permissions" \
  --method PUT \
  --field enabled=true \
  --field allowed_actions=all \
  --field sha_pinning_required=true
```

`enabled` and `allowed_actions` are **required** in the PUT body -- read them
first and preserve them to avoid unintended changes:

```bash
gh api "repos/{owner}/{repo}/actions/permissions" \
  --jq '{enabled, allowed_actions}'
```

## 5. Manual recipe -- Setting 2 (fork-PR approval)

### Check
```bash
gh api "repos/{owner}/{repo}/actions/permissions/fork-pr-contributor-approval" \
  --jq '.approval_policy'
```

### Enable "all outside collaborators" (repo)
```bash
gh api "repos/{owner}/{repo}/actions/permissions/fork-pr-contributor-approval" \
  --method PUT \
  --input - <<< '{"approval_policy":"all_external_contributors"}'
```

Returns `422` on a private repo (not applicable). Valid values:
`first_time_contributors`, `all_external_contributors`.

## 6. Manual recipe -- Setting 3 (default-branch ruleset)

### Check
```bash
gh api "repos/{owner}/{repo}/rulesets" --jq '.[]|{id,name,target,enforcement}'
gh api "repos/{owner}/{repo}/rulesets/{id}"   # full rule detail
```

### Create a default-branch protection ruleset
```bash
gh api "repos/{owner}/{repo}/rulesets" --method POST --input - <<'JSON'
{
  "name": "default branch protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      } }
  ],
  "bypass_actors": [
    { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" }
  ]
}
JSON
```

Returns `403` "Upgrade to GitHub Pro" on a private repo (not available).
`actor_id: 5` is the Admin repository role (owner keeps direct-push); use an
empty `bypass_actors: []` for no bypass.

## 7. Bulk verification

```bash
python scripts/repo_settings.py diff --config fleet.yml   # preferred
```

Or manually:

```bash
repos=$(gh repo list {owner} --limit 1000 --json nameWithOwner --jq '.[].nameWithOwner')
for repo in $repos; do
  sha=$(gh api "repos/$repo/actions/permissions" --jq '.sha_pinning_required' 2>/dev/null)
  fork=$(gh api "repos/$repo/actions/permissions/fork-pr-contributor-approval" --jq '.approval_policy' 2>/dev/null)
  rs=$(gh api "repos/$repo/rulesets" --jq '[.[].name]|join(",")' 2>/dev/null)
  echo "$repo: sha=$sha fork=$fork rulesets=[$rs]"
done
```

## 8. Fallback -- GitHub UI

If API endpoints change: **Settings > Actions > General** for settings 1-2;
**Settings > Rules > Rulesets** for setting 3.

## 9. Permissions required

- **Repository settings**: `repo` scope (PAT) or repository admin access.
- **GitHub App**: `administration` (write) for rulesets, `actions` (write) for
  Actions permissions.

## 10. Related

- After enabling SHA pinning, existing workflows with unpinned actions will
  fail -- audit and fix them with the **`pin-actions-to-sha`** skill.
- **`workflow-path-audit`** -- ensure workflows only run on salient path changes.
