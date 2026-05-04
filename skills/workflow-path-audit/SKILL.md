---
name: workflow-path-audit
description: Audit GitHub Actions workflows for salient-path conditionals — every workflow that triggers on pull_request or push must filter on the files and directories its steps actually depend on, and skip with success when nothing salient changed. Use when adding a new workflow, modifying an existing one's steps, renaming/moving files a workflow depends on, or when a CI bill spike points at workflows running on irrelevant changes.
---

# Workflow path-filtering audit

Every workflow that runs on `pull_request` or `push` should fire only when something it actually depends on changed. When nothing salient changed, the workflow should either skip entirely (workflow-level `paths`/`paths-ignore`) or run a single fast job that emits a green check immediately (always-run + early-skip pattern, used for required checks).

This skill walks a repo's `.github/workflows/` and tightens path filters where they're missing or wrong, then keeps the docs in sync.

## When to invoke

- Adding a new workflow file under `.github/workflows/`.
- Adding a new step to an existing workflow that touches a new part of the codebase (a new spec, a new build artefact, a new script).
- Renaming or moving a file that a workflow consumes (e.g. moving `e2e/foo.spec.js` → `tests/foo.spec.js` while a workflow's `paths:` still points at the old path).
- A CI minutes / billing review showing workflows triggering on irrelevant changes.
- Promoting an existing job to a required status check — required checks block merge when missing, so path-filtered required checks need the always-run + early-skip pattern instead of workflow-level filtering.

## Procedure

### 1. Inventory the workflows

```bash
ls .github/workflows/
```

For each `.yml` / `.yaml` file, read it end-to-end and note:

- **Trigger** (`on:` block): `pull_request`, `push`, `schedule`, `workflow_dispatch`, event types like `issue_comment`, etc.
- **Filter present?** Look for `on.<event>.paths`, `on.<event>.paths-ignore`, or `on.<event>.branches` filters.
- **Required-check status?** Check the repo's branch-protection ruleset (`gh api repos/<owner>/<repo>/rulesets` → look at the `main` ruleset's `required_status_checks` rule) — the job names in there are required checks and need always-fire semantics.

Workflows triggered ONLY by `schedule` or by event types like `issue_comment`/`issues` don't need path filters — paths only meaningfully apply to `pull_request` and `push` events.

### 2. Determine each workflow's salient paths

Read the workflow's steps from top to bottom. For each step that runs a script, builds something, deploys something, or executes tests, ask: "what files in the repo does this consume? What files are produced by it that downstream steps consume?" Aggregate them into a salient-paths list.

Common categories (adapt to the repo):

- **Site source** the workflow builds: `_layouts/`, `_includes/`, `_posts/`, `_config.yml`, `assets/`, `pages/`
- **Test code** the workflow runs: `e2e/`, `tests/`, `_plugins_test/`
- **Scripts** the workflow invokes: `scripts/<the specific script>.sh`
- **Configs** the workflow reads: `playwright.config.js`, `Gemfile`, `package.json`, framework configs
- **Dep manifests** that change behaviour: `package*.json`, `Gemfile*`, `requirements*.txt`, `go.sum`
- **The workflow file itself** — always salient (workflow self-validation)

Things almost never salient (good `paths-ignore` candidates):

- `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/`, `LICENSE`, `.editorconfig`, `.gitignore`
- Other workflow files (a sibling workflow's edit shouldn't trigger this one)
- `screenshots/`, `recordings/`, `test-results/` (build artefacts)
- Skills / agent metadata directories (`.agents/`, `.claude/skills/`) unless the workflow specifically tests them

### 3. Pick the right pattern

**Workflow-level `paths:` (positive list)** — when the salient list is short and self-contained. Example: `visual-regression.yml` only cares about Jekyll site source plus a few pipeline scripts.

**Workflow-level `paths-ignore:` (negative list)** — when the workflow cares about *almost everything* and only a small set of paths are clearly irrelevant. Example: `deploy-production.yml` deploys whatever Jekyll builds, so the negative list is shorter than enumerating every site path.

**Always-run + early-skip pattern** — required when the workflow's named check must be a required status check, because GitHub blocks the merge on *missing* checks (not just failing ones). Pattern:

```yaml
on:
  pull_request:
    branches: [main]
  workflow_dispatch:

jobs:
  the-job:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<sha>  # v<x>.<y>.<z> (<release-date>)
        with:
          fetch-depth: 0  # need history to diff base..head

      - name: Detect salient changes
        id: salient
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
          HEAD_SHA: ${{ github.event.pull_request.head.sha }}
          EVENT_NAME: ${{ github.event_name }}
        run: |
          set -euo pipefail
          if [ "$EVENT_NAME" = "workflow_dispatch" ]; then
            echo "run=true" >> "$GITHUB_OUTPUT"
            exit 0
          fi
          changed=$(git diff --name-only "$BASE_SHA" "$HEAD_SHA")
          echo "Changed files:"; echo "$changed" | sed 's/^/  /'
          if printf '%s\n' "$changed" | grep -qE '^(<salient regex here>)'; then
            echo "run=true" >> "$GITHUB_OUTPUT"
          else
            echo "run=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Skip — not salient
        if: steps.salient.outputs.run != 'true'
        run: echo "::notice::No salient paths changed; reporting success without running."

      # All real work below this guarded by:
      - name: <real step>
        if: steps.salient.outputs.run == 'true'
        run: ...
```

The job always runs so the named check is always present and reports success when nothing salient changed. Manual `workflow_dispatch` always forces a real run.

### 4. Apply the changes

Edit each workflow's `on:` block (or job steps for the always-run pattern) and add/correct the path filter.

For workflow-level filters, prefer one filter style per workflow — if the workflow already uses `paths-ignore`, extend that list rather than mixing in a `paths:` block.

When listing patterns, group related items and sort within groups. A future reader skimming the list should be able to tell "what are docs", "what are tests", "what are sibling workflows".

### 5. Update the docs

Most repos have an AGENTS.md or CONTRIBUTING.md that documents workflows. Update:

- Any quick-reference table of "what each workflow does + what triggers it" — make sure the salient paths column reflects the new filter.
- Any per-workflow section with a "Trigger:" line — confirm it matches the YAML.

If the repo has neither, add a "Workflow path-filtering rule" section to AGENTS.md (or whatever the equivalent agent-onboarding doc is) that names the rule and lists the salient paths per workflow.

### 6. Verify

```bash
# Each workflow's `on:` block should have either path filtering OR a clear
# documented reason it doesn't (event-driven, schedule-only, intentionally
# always-run for required-check semantics).
grep -nE '^on:|paths:|paths-ignore:|schedule:|workflow_dispatch:|issue_comment:|pull_request_review:|issues:' .github/workflows/*.yml
```

For workflows using the always-run + early-skip pattern, sanity-check by running with no salient changes (workflow_dispatch from main, or open a draft PR that only edits a doc): the workflow should complete in seconds with a green check and a `::notice::` line stating why it skipped.

For workflows using `paths-ignore`, open a draft PR that only edits a path in the ignore list: the workflow should not appear on the PR's checks page at all (assuming no required-check trap — see below).

## Required-check + path-filter trap

GitHub blocks the merge when a required status check is *missing* — including when path filtering prevented the workflow from triggering. If a check is required:

- DON'T use workflow-level `paths` / `paths-ignore` — use the always-run + early-skip pattern instead.
- DO list the named check (job ID, or `<job_id> (<matrix value>)` for matrix jobs) in `.github/rulesets/main.json` and apply via `gh api -X PUT`.

When promoting an existing path-filtered workflow to required, refactor it to always-run + early-skip in the same change.

## Output

When this audit runs successfully, three things are in sync:

1. Each workflow's `on:` block has the right filter (or a documented reason it's always-run).
2. AGENTS.md (or equivalent) has an up-to-date salient-paths reference.
3. The branch-protection ruleset's `required_status_checks` list only contains checks that always fire, OR that use the always-run + early-skip pattern.
