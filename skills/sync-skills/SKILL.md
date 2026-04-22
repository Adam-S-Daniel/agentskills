---
name: sync-skills
description: >
  Sync local skill folders from git repos to Claude.ai (and other agent
  targets) via the upload-skill API. Trigger when the user says "sync
  skills", "push skills to Claude", "upload skill", or after editing
  SKILL.md files locally. Requires a claude.ai tab open in Chrome (uses
  browser session cookies via javascript_tool). Works on Adam's computer
  where the agentskills repos live under ~/repos/ or %USERPROFILE%\repos\.
compatibility:
  tools:
    - Claude in Chrome (browser automation)
    - Bash / Python 3 (sync_skills.py helper)
  environment: local-browser-only
---

# sync-skills

Upload changed skill folders from local git repos to claude.ai using the
browser's authenticated session. No separate API key required — the
`javascript_tool` runs in the browser context which already holds the
session cookies.

## Setup (one-time)

Register the post-push reminder hook (requires git 2.54+):

```bash
bash ~/repos/agentskills/skills/sync-skills/setup.sh
```

On Windows (Git Bash / WSL):
```bash
bash "$USERPROFILE/repos/agentskills/skills/sync-skills/setup.sh"
```

This registers a global config-based `pre-push` hook so every push from
any agentskills repo reminds you to run `sync-skills` if skill files
are being pushed. (Note: git has no native `post-push` event, so the
reminder fires just before the push — early enough to catch you before
you switch contexts.)

---

## Quick-start checklist

1. Ensure a claude.ai tab is open in Chrome (any page will do).
2. Run `sync_skills.py --prepare` (via Bash) to get the JSON payload.
3. For each skill in the payload, call `javascript_tool` to POST the ZIP.
4. Mark each successfully uploaded skill with `--mark-synced`.
5. Report results to the user.

---

## 1. Get the change list

Run the helper script to find changed skills and build base64-encoded ZIPs:

```bash
python3 ~/repos/agentskills/skills/sync-skills/sync_skills.py --prepare
```

On Windows the path is `%USERPROFILE%\repos\agentskills\skills\sync-skills\sync_skills.py`.
Use `--all` to force-sync every skill regardless of git diff:

```bash
python3 ~/repos/agentskills/skills/sync-skills/sync_skills.py --prepare --all
```

The output is a JSON object:

```json
{
  "skills": [
    {
      "name": "fastmail",
      "zip_b64": "<base64-encoded ZIP>",
      "is_update": true,
      "repo": "agentskills",
      "hash": "a1b2c3d4e5f6a7b8"
    }
  ],
  "org_id_hint": "12345678-abcd-..."
}
```

If `skills` is empty, nothing has changed since the last sync. Inform the
user and stop.

---

## 2. Get the org_id

If `org_id_hint` is non-null, use it directly.

Otherwise, retrieve it via `javascript_tool`:

```javascript
// Returns an array of orgs; use the first active one
const resp = await fetch('https://claude.ai/api/organizations', {credentials: 'include'});
const orgs = await resp.json();
return orgs.map(o => ({id: o.id, name: o.name}));
```

Pick the correct `org_id` from the list (usually only one). Confirm with
the user if there are multiple.

---

## 3. Upload each skill

For each entry in `skills`, call `javascript_tool` with the following
template. Substitute `ORG_ID`, `SKILL_NAME`, `OVERWRITE`, and `ZIP_B64`.

- `OVERWRITE` = `"true"` when `is_update` is `true`, `"false"` otherwise.

```javascript
(async () => {
  const orgId   = "ORG_ID";
  const name    = "SKILL_NAME";
  const overwrite = OVERWRITE;   // true or false (boolean, not string)
  const zipB64  = "ZIP_B64";    // full base64 string from the JSON

  const url = `https://claude.ai/api/organizations/${orgId}/skills/upload-skill?overwrite=${overwrite}`;

  // Decode base64 → Uint8Array → Blob
  const binary  = atob(zipB64);
  const bytes   = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const blob    = new Blob([bytes], {type: 'application/zip'});

  const form    = new FormData();
  form.append('file', blob, `${name}.zip`);

  const resp    = await fetch(url, {method: 'POST', body: form, credentials: 'include'});
  const text    = await resp.text();
  return {status: resp.status, ok: resp.ok, body: text.slice(0, 400)};
})();
```

**Expected success:** `status: 200` (or 201). If you get `409 Conflict`
on a new upload, the skill already exists — retry with `overwrite=true`.
If you get `404`, double-check the org_id. For any other error, surface the
response body to the user.

---

## 4. Mark as synced

After each successful upload, record it in the state file so future runs
know to use `overwrite=true`:

```bash
python3 ~/repos/agentskills/skills/sync-skills/sync_skills.py \
  --mark-synced "SKILL_NAME:HASH"
```

Substitute the `name` and `hash` fields from the JSON payload.

---

## 5. Dry run / troubleshooting

To preview what would be synced without uploading:

```bash
python3 ~/repos/agentskills/skills/sync-skills/sync_skills.py --dry-run
```

To target a single skill:

```bash
python3 ~/repos/agentskills/skills/sync-skills/sync_skills.py --skill fastmail
```

To include skills from both repos:

```bash
python3 ~/repos/agentskills/skills/sync-skills/sync_skills.py --prepare --all \
  --repos ~/repos/agentskills ~/repos/agentskills-private
```

---

## 6. Fallback: upload a raw .md file

The endpoint also accepts a bare `.md` file (no ZIP required). If
`javascript_tool` has trouble with ZIPs, upload just the `SKILL.md`:

```javascript
(async () => {
  const orgId = "ORG_ID";
  const overwrite = OVERWRITE;
  const content = `SKILL_MD_CONTENT`;  // full text of SKILL.md

  const url = `https://claude.ai/api/organizations/${orgId}/skills/upload-skill?overwrite=${overwrite}`;
  const blob = new Blob([content], {type: 'text/markdown'});
  const form = new FormData();
  form.append('file', blob, 'SKILL.md');

  const resp = await fetch(url, {method: 'POST', body: form, credentials: 'include'});
  return {status: resp.status, ok: resp.ok};
})();
```

---

## 7. Reporting

After all uploads, summarise:

```
Synced 3 skills:
  ✓ fastmail          (updated)  agentskills
  ✓ pin-actions-to-sha (new)     agentskills
  ✗ some-skill        FAILED — status 403
```

If any skill failed, explain the error and suggest remedies (re-authenticate
on claude.ai, check org_id, try overwrite flag).
