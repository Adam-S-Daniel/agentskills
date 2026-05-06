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
browser's authenticated session. No separate API key required - the
`javascript_tool` runs in the browser context which already holds the
session cookies.

## Setup (one-time)

Register the pre-push reminder hook (requires git 2.54+):

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
reminder fires just before the push - early enough to catch you before
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
// Returns an array of orgs; the upload-skill endpoint keys on UUID (o.uuid),
// NOT the integer primary key (o.id). Always pick o.uuid.
(async () => {
  const resp = await fetch('https://claude.ai/api/organizations', {credentials: 'include'});
  const orgs = await resp.json();
  return orgs.map(o => ({uuid: o.uuid, name: o.name}));
})()
```

Pick the correct `uuid` from the list (usually only one) - that's the value
to substitute for `ORG_ID` in step 3. Confirm with the user if there are
multiple orgs.

---

## 3. Upload each skill

For each entry in `skills`, call `javascript_tool` with the following
template. Substitute `ORG_ID`, `SKILL_NAME`, `OVERWRITE`, and `ZIP_B64`.

- `OVERWRITE` = `true` when `is_update` is `true`, `false` otherwise.

```javascript
(async () => {
  const orgId   = "ORG_ID";
  const name    = "SKILL_NAME";
  const overwrite = OVERWRITE;   // true or false (boolean, not string)
  const zipB64  = "ZIP_B64";     // full base64 string from the JSON

  const url = `https://claude.ai/api/organizations/${orgId}/skills/upload-skill?overwrite=${overwrite}`;

  // Decode base64 -> Uint8Array -> Blob
  const binary = atob(zipB64);
  const bytes  = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const blob   = new Blob([bytes], {type: 'application/zip'});

  const form   = new FormData();
  form.append('file', blob, `${name}.zip`);

  const resp   = await fetch(url, {method: 'POST', body: form, credentials: 'include'});
  const text   = await resp.text();
  return {status: resp.status, ok: resp.ok, body: text.slice(0, 400)};
})();
```

**Expected success:** HTTP 200 with a `skill` field in the response.
Note: the server may return HTTP 200 with a `validation_errors` array
instead - check the response body for `validation_errors` before
treating the upload as successful. Common validation errors include
`skill_upload_invalid_encoding` (SKILL.md is not valid UTF-8).

If you get `409 Conflict` on a new upload, the skill already exists -
retry with `overwrite=true`. If you get `404`, double-check the org_id.
For any other error, surface the response body to the user.

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

## 6. Fallback: build the ZIP in-browser

Earlier versions of this skill noted that the upload endpoint accepted a
bare `.md` file. **That is no longer the case** — the server now rejects
`text/markdown` uploads with `skill_upload_invalid_file_type` (only
`.zip` or `.skill` extensions are accepted, and `.skill` is parsed as a
ZIP container, not a single-file format).

When you don't have `sync_skills.py --prepare` available locally (e.g.
the local repo doesn't exist on this machine, or you only have the raw
`SKILL.md` content in hand), build a minimal STORE-mode ZIP in the
browser and upload that:

```javascript
(async () => {
  // ----- minimal CRC32 + STORE-mode ZIP builder -----
  const tbl = (() => { const t=new Uint32Array(256); for(let i=0;i<256;i++){let c=i;for(let j=0;j<8;j++)c=(c&1)?(0xEDB88320^(c>>>1)):(c>>>1);t[i]=c>>>0;} return t; })();
  const crc32 = b => { let c=0xFFFFFFFF; for(let i=0;i<b.length;i++) c=tbl[(c^b[i])&0xFF]^(c>>>8); return (c^0xFFFFFFFF)>>>0; };
  function makeZip(name, content) {
    const enc = new TextEncoder(), nB = enc.encode(name), dB = enc.encode(content);
    const crc = crc32(dB), sz = dB.length, nl = nB.length;
    const lfh = new DataView(new ArrayBuffer(30));
    lfh.setUint32(0,0x04034b50,true); lfh.setUint16(4,10,true); lfh.setUint16(12,0x0021,true);
    lfh.setUint32(14,crc,true); lfh.setUint32(18,sz,true); lfh.setUint32(22,sz,true);
    lfh.setUint16(26,nl,true);
    const cdfh = new DataView(new ArrayBuffer(46));
    cdfh.setUint32(0,0x02014b50,true); cdfh.setUint16(4,10,true); cdfh.setUint16(6,10,true);
    cdfh.setUint16(14,0x0021,true); cdfh.setUint32(16,crc,true);
    cdfh.setUint32(20,sz,true); cdfh.setUint32(24,sz,true); cdfh.setUint16(28,nl,true);
    const eocd = new DataView(new ArrayBuffer(22));
    eocd.setUint32(0,0x06054b50,true); eocd.setUint16(8,1,true); eocd.setUint16(10,1,true);
    eocd.setUint32(12,46+nl,true); eocd.setUint32(16,30+nl+sz,true);
    const out = new Uint8Array(30+nl+sz+46+nl+22); let p = 0;
    out.set(new Uint8Array(lfh.buffer), p); p+=30; out.set(nB,p); p+=nl;
    out.set(dB,p); p+=sz; out.set(new Uint8Array(cdfh.buffer), p); p+=46;
    out.set(nB,p); p+=nl; out.set(new Uint8Array(eocd.buffer), p);
    return out;
  }

  // ----- inputs (substitute) -----
  const orgId = "ORG_ID";
  const overwrite = OVERWRITE;             // true | false
  const skillName = "SKILL_NAME";          // e.g. "adam-writing-style"
  const skillMd = `SKILL_MD_CONTENT`;       // full SKILL.md text

  const zipBytes = makeZip(`${skillName}/SKILL.md`, skillMd);
  const url = `https://claude.ai/api/organizations/${orgId}/skills/upload-skill?overwrite=${overwrite}`;
  const form = new FormData();
  form.append('file', new Blob([zipBytes], {type:'application/zip'}), `${skillName}.zip`);
  const resp = await fetch(url, { method:'POST', body: form, credentials:'include' });
  return { status: resp.status, ok: resp.ok, body: (await resp.text()).slice(0, 800) };
})();
```

The path inside the ZIP **must** be `<skill-name>/SKILL.md` — the server
keys the skill name on the directory prefix. Don't put the file at the
ZIP root.

This fallback is only for SKILL.md-only skills (no `references/`,
`scripts/`, `assets/`). For multi-file skills, use the
`sync_skills.py --prepare` path in section 1.

## 7. Reporting

After all uploads, summarise:

```
Synced 3 skills:
  [OK]  fastmail           (updated)  agentskills
  [OK]  pin-actions-to-sha (new)      agentskills
  [FAIL] some-skill        status 403
```

If any skill failed, explain the error and suggest remedies (re-authenticate
on claude.ai, check org_id, try overwrite flag).
