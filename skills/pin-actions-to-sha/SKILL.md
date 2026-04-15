---
name: pin-actions-to-sha
description: >
  Audit and fix GitHub Actions workflow files to ensure every `uses` reference
  is pinned to a full-length commit SHA (40 hex characters) with a version
  comment that includes the release date. Enforces a 7-day cooling-off period
  before adopting new releases. Trigger when: creating or modifying GitHub
  Actions workflows, running security audits, reviewing pull requests that
  touch workflow files, or when asked to pin, audit, or harden actions.
  Trigger on mentions of "pin actions", "SHA pinning", "actions security",
  "supply chain", or "harden workflows".
compatibility:
  tools:
    - GitHub CLI (gh)
    - Git CLI
  environment: any
---

# Pin Actions to SHA

Ensure every `uses` reference in GitHub Actions workflow files is pinned to a
full-length (40-character) commit SHA, with a comment showing the version tag
and its release date.

## 1. Required format

```yaml
uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11 # v4.1.1 (2023-10-17)
```

Rules:
- The ref after `@` must be a **40-character hex commit SHA**.
- Immediately after the SHA, a **comment** of the form `# vX.Y.Z (YYYY-MM-DD)`
  must appear, where the date is the release/publish date of that version.
- The version used must have been **released at least 7 days ago** (cooling-off
  period). If the latest release is less than 7 days old, use the previous
  eligible release instead.

## 2. What needs fixing

Scan every `.yml` and `.yaml` file under `.github/workflows/`.

| Pattern | Action |
|---------|--------|
| `uses: owner/repo@v4` (tag) | **Fix** -- look up SHA |
| `uses: owner/repo@main` (branch) | **Fix** -- look up SHA; warn user about branch ref |
| `uses: owner/repo@abc1234` (short SHA) | **Fix** -- resolve to full 40-char SHA |
| `uses: owner/repo@<40-char>` without comment | **Fix** -- add version comment |
| `uses: owner/repo@<40-char> # v1.2.3 (2023-01-01)` | **Skip** -- already correct |
| `uses: ./local/path` | **Skip** -- local composite action |
| `uses: docker://image:tag` | **Skip** -- Docker reference |

## 3. How to look up the correct SHA and version

### 3a. Identify the action's owner and repo

Parse the `uses` value to extract `{owner}/{repo}`. Handle special cases:
- **Subdirectory actions**: `actions/setup-node/subdir@v4` -- the repo is
  `actions/setup-node`; the `/subdir` is a path within it.
- **Reusable workflows**: `my-org/repo/.github/workflows/ci.yml@main` -- the
  repo is `my-org/repo`.

### 3b. Find the latest eligible version (with cooling-off)

**Using GitHub Releases (preferred):**

```bash
gh api "repos/{owner}/{repo}/releases" \
  --jq '.[] | "\(.tag_name)\t\(.published_at)"' | head -20
```

This lists versions with their publish dates. Pick the most recent release
whose publish date is **7 or more days before today**.

**Fallback -- using tags (when there are no Releases):**

```bash
# List tags
gh api "repos/{owner}/{repo}/tags" --jq '.[].name' | head -20

# Get the date for a specific tag (from the underlying commit)
gh api "repos/{owner}/{repo}/git/ref/tags/{tag}" --jq '.object.sha' \
  | xargs -I{} gh api "repos/{owner}/{repo}/git/commits/{}" --jq '.committer.date'
```

For the cooling-off check, compare the release/commit date to today:

```bash
release_date="2024-10-24"
days_ago=$(( ( $(date -u +%s) - $(date -u -d "$release_date" +%s) ) / 86400 ))
if [ "$days_ago" -ge 7 ]; then
  echo "OK: $days_ago days old"
else
  echo "TOO NEW: only $days_ago days old -- use previous version"
fi
```

### 3c. Get the full commit SHA for the chosen tag

```bash
gh api "repos/{owner}/{repo}/git/ref/tags/{tag}" --jq '.object.type, .object.sha'
```

**Critical:** If `.object.type` is `"tag"` (annotated tag), you must
**dereference** it to get the underlying commit SHA:

```bash
# Step 1: get the tag object
tag_sha=$(gh api "repos/{owner}/{repo}/git/ref/tags/{tag}" --jq '.object.sha')

# Step 2: if annotated, dereference to commit
commit_sha=$(gh api "repos/{owner}/{repo}/git/tags/$tag_sha" --jq '.object.sha')
```

If `.object.type` is `"commit"` (lightweight tag), the SHA from step 1 is
already the commit SHA.

**Verification:** The final SHA must be exactly 40 hex characters. Confirm:

```bash
echo "$commit_sha" | grep -qE '^[0-9a-f]{40}$' && echo "Valid" || echo "INVALID"
```

**Alternative method -- `git ls-remote`:**

```bash
# Lightweight tags return the commit SHA directly
git ls-remote --tags https://github.com/{owner}/{repo}.git "refs/tags/{tag}"

# For annotated tags, the ^{} dereferenced form gives the commit SHA
git ls-remote --tags https://github.com/{owner}/{repo}.git "refs/tags/{tag}^{}"
```

Use the `^{}` (dereferenced) line if present; otherwise use the plain line.

### 3d. Construct the replacement line

```
uses: {owner}/{repo}@{commit_sha} # {tag} ({release_date})
```

- Preserve the original indentation exactly.
- `{release_date}` is formatted as `YYYY-MM-DD`.
- For subdirectory actions: `uses: {owner}/{repo}/{subdir}@{sha} # {tag} ({date})`
- For reusable workflows: `uses: {owner}/{repo}/.github/workflows/{file}@{sha} # {tag} ({date})`

## 4. Full audit process

1. **Find workflow files:**
   ```bash
   ls .github/workflows/*.yml .github/workflows/*.yaml 2>/dev/null
   ```

2. **Scan for `uses:` lines** in each file. For each line, classify it per the
   table in section 2.

3. **For each line that needs fixing:**
   - Extract `{owner}/{repo}` (section 3a)
   - Find the latest eligible version (section 3b)
   - Get the full commit SHA (section 3c)
   - Build the replacement line (section 3d)

4. **Apply the fix** by replacing the old line. Preserve all surrounding YAML
   structure and indentation.

5. **After all fixes**, validate the YAML still parses cleanly:
   ```bash
   python3 -c "import yaml, sys; yaml.safe_load(open(sys.argv[1]))" .github/workflows/file.yml
   ```
   or use `yq` / any available YAML linter.

6. **Summarize** the changes: list each file and each action that was updated,
   with old and new references.

## 5. Edge cases

- **Annotated vs. lightweight tags**: Always check `.object.type` from the
  `git/ref/tags/` endpoint. Annotated tags require an extra dereference step.
  Skipping this produces a tag-object SHA, not a commit SHA, and the workflow
  will fail at runtime.

- **Tags with and without `v` prefix**: Some actions use `v4.1.1`, others use
  `4.1.1`. Preserve the exact tag format used by the action's maintainers.

- **Actions without GitHub Releases**: Fall back to tags and use the tag's
  underlying commit date for the cooling-off check and the date comment.

- **Org-internal / private actions**: The `gh` CLI works if authenticated with
  appropriate permissions. The same process applies.

- **Branch-only references** (e.g., `@main` with no tags): Pin to the HEAD
  commit of the branch, but **warn the user** that this action has no tagged
  releases and should be reviewed for trustworthiness. Use today's date minus
  7 days as the reference point; if the commit is less than 7 days old, use a
  commit from at least 7 days ago.

- **Multiple actions from the same repo**: Look up the SHA once per
  repo+version pair and reuse it across all `uses:` lines referencing it.

## 6. Example transformations

| Before | After |
|--------|-------|
| `uses: actions/checkout@v4` | `uses: actions/checkout@<sha> # v4.2.2 (2024-10-24)` |
| `uses: actions/setup-node@v4.1.0` | `uses: actions/setup-node@<sha> # v4.1.0 (2024-10-03)` |
| `uses: org/repo/.github/workflows/ci.yml@main` | `uses: org/repo/.github/workflows/ci.yml@<sha> # v2.0.0 (2024-09-15)` |
| `uses: actions/cache@v3` | `uses: actions/cache@<sha> # v3.4.0 (2024-08-12)` |

(Replace `<sha>` with the actual 40-character commit SHA in each case.)

## 7. Related

After pinning workflow files, ensure the repository is configured to **require**
SHA-pinned actions going forward. See the `github-actions-repo-settings` skill.
