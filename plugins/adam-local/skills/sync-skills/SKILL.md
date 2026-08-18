---
name: sync-skills
description: >
  Sync local skill folders from git repos to Claude.ai (and other agent
  targets) via the upload-skill API. Trigger when the user says "sync
  skills", "push skills to Claude", "upload skill", or after editing
  SKILL.md files locally. Requires a claude.ai tab open in Chrome (uses
  browser session cookies via javascript_tool). Works on Adam's computer
  wherever the agentskills clones live, but the helper does not guess where:
  it scans its own checkout, and any other clone must be named with --repos
  or $AGENTSKILLS_REPOS.
compatibility: Requires Claude in Chrome (browser automation) with a logged-in claude.ai tab, plus Bash/Python 3 for the sync_skills.py helper; local interactive execution only — not usable in headless or cloud sessions
---

# sync-skills

Upload changed skill folders from local git repos to claude.ai using the
browser's authenticated session. No separate API key required - the
`javascript_tool` runs in the browser context which already holds the
session cookies.

## Locating the helper

Clone locations are machine-specific — `~/repos/agentskills` on Linux/WSL,
`D:\repos\adam-s-daniel\agentskills` on ZENDA (Windows) — so **don't
hardcode either one**. Resolve the skill folder once and reuse it:

```bash
# Run from anywhere inside the agentskills clone:
SKILL_DIR="$(git rev-parse --show-toplevel)/plugins/adam-local/skills/sync-skills"
```

Every command below uses `"$SKILL_DIR"`. If you're not inside the clone,
set it by hand to wherever this skill folder actually is on this machine.

`sync_skills.py` resolves the **repos to sync** in this order, and no
other: `--repos` → `$AGENTSKILLS_REPOS` (`:`/`;`-separated) → the checkout
it lives in, claimed only for that checkout's own repo name.

There are deliberately **no built-in clone locations**. A `~/repos/<name>`
guess used to come first, which meant any directory sitting at that
path — an empty folder, a half-finished clone, a junction — outranked the
checkout the script was demonstrably running from. On ZENDA that guess
resolved an empty `~/repos/agentskills`, enumerated zero skills from it,
and reported `no skills selected ... Pass --all` on a command line that
already said `--all`. A path that merely exists is not evidence that it is
the registry.

The cost is that **`agentskills-private` is no longer found implicitly** —
nothing derives its location from `__file__`. It has to be named:

```bash
# WSL / Linux
python3 "$SKILL_DIR/sync_skills.py" --prepare --all \
  --repos ~/repos/agentskills ~/repos/agentskills-private
```

```powershell
# Windows (PowerShell) — set it once for the session
$env:AGENTSKILLS_REPOS = "D:\repos\adam-s-daniel\agentskills;D:\repos\adam-s-daniel\agentskills-private"
```

It warns on stderr about any declared repo it could not resolve, saying
that repo's skills went **unexamined**, and exits non-zero if it resolved
none — "nothing to sync" and "I couldn't look" are never the same answer.

### Repo-state gate

Before `--prepare`, `--verify` or `--dry-run` does any work, each resolved
repo is checked: it must be on `main` (compared literally, so a repo whose
default branch is called something else gets surfaced rather than silently
mishandled) and level with `origin/main`. Answering the second honestly
needs a `git fetch`, so it runs one, bounded to 20 seconds; if the fetch
can't happen — no remote, offline, too slow — the run says the state
**could not be determined** for that repo and carries on, because asserting
"up to date" from stale remote-tracking refs is the same class of lie the
resolution defaults used to tell.

The gate exists because the upload is built from the **working tree** and
the upload API has no delete: syncing from an off-main or behind clone
publishes the wrong bytes irreversibly.

- Interactive terminal: it lists the problems and asks
  `Continue anyway? [y/N] `. The default is no — a bare Enter, EOF, or
  anything not starting with `y` aborts non-zero.
- Not a terminal (an agent driving it through Bash): it does **not** wait
  for an answer nobody will give. It exits non-zero, lists the problems,
  and names `--yes`.
- `--yes` bypasses the gate, and says on stderr that it did. A silent
  override is how a gate rots into decoration.

## Setup (one-time)

Register the pre-push reminder hook (requires git 2.54+):

```bash
bash "$SKILL_DIR/setup.sh"
```

This registers a global config-based `pre-push` hook so every push from
any agentskills repo reminds you to run `sync-skills` if skill files
are being pushed. (Note: git has no native `post-push` event, so the
reminder fires just before the push - early enough to catch you before
you switch contexts.)

---

## Quick-start checklist

1. Ensure a claude.ai tab is open in Chrome (any page will do).
2. Run `sync_skills.py --prepare --zip-dir DIR` (via Bash) to write the
   ZIPs to disk and get the JSON payload. If it stops on the repo-state
   gate, fix the clone (`git checkout main`, `git pull`) rather than
   reaching for `--yes` — you are about to publish that tree to an API
   with no delete.
3. Hand those ZIP files to the page with `file_upload`, then POST them
   with `javascript_tool` (§3). Do **not** paste base64 into the script.
4. Mark each successfully uploaded skill with `--mark-synced`.
5. Refresh the account-copy mirror (§7) and run `--verify`; a sync isn't
   done until it reports `OK` for every skill you just uploaded. The
   refresh is mandatory — `--verify` against a stale mirror proves nothing.
6. Report results to the user.

---

## 1. Get the change list

Run the helper script to find changed skills and build their ZIPs. Pass
`--zip-dir` so the ZIPs are written as real files — that is what the
upload path in §3 consumes:

```bash
python3 "$SKILL_DIR/sync_skills.py" --prepare --zip-dir "$TMPDIR/skillzips"
```

Use `--all` to force-sync every skill regardless of git diff:

```bash
python3 "$SKILL_DIR/sync_skills.py" --prepare --all --zip-dir "$TMPDIR/skillzips"
```

The output is a JSON object:

```json
{
  "skills": [
    {
      "name": "fastmail",
      "zip_path": "/tmp/skillzips/fastmail.zip",
      "zip_bytes": 34552,
      "zip_sha256": "e647b204980d5794...",
      "is_update": true,
      "repo": "agentskills",
      "hash": "a1b2c3d4e5f6a7b8"
    }
  ],
  "org_id_hint": "12345678-abcd-..."
}
```

Without `--zip-dir` each entry carries `zip_b64` (base64 of the ZIP)
instead of `zip_path`. That shape still works, but prefer `--zip-dir`:
see §3 for why.

If `skills` is empty, read the `message` the payload carries with it — it
lists every repo that was resolved and how many skills each one held. An
empty payload from a plain `--prepare` means nothing changed since the last
sync; an empty one from `--prepare --all` means the resolved tree held no
skills, which is a resolution problem and not something to report as
"nothing to do". Either way, tell the user which it was, and stop.

---

## 2. Get the org_id

**Use `org_id_hint` from the payload. It is the answer — don't go looking
for another one.** It is read from `~/.claude.json`
(`oauthAccount.organizationUuid`): the org this machine's Claude Code CLI
is authenticated against. That is the right org by construction, because
the same CLI writes the account mirror that `--verify` reads in §7. If you
upload somewhere else, `--verify` can never see it.

To read it directly:

```bash
python3 -c "import json,pathlib;print(json.load(open(pathlib.Path.home()/'.claude.json'))['oauthAccount']['organizationUuid'])"
```

The UUID is not hardcoded in this skill on purpose — it is an account
identifier and this repo is public.

### Don't pick one from `/api/organizations`

That endpoint lists **every** org the account belongs to, with nothing
marking which one owns the skill store:

```javascript
// Returns an array of orgs; the upload-skill endpoint keys on UUID (o.uuid),
// NOT the integer primary key (o.id).
const resp = await fetch('https://claude.ai/api/organizations', {credentials: 'include'});
(await resp.json()).map(o => ({uuid: o.uuid, name: o.name}));
```

On this account it returns two, and the personal-looking name is the
correct one — a coin flip you should not be taking. Guessing wrong 404s
every request. There is also **no read API to fall back on**: `GET
/skills`, `GET /skills/{id}` and `GET /skills/list` all 404 under both
orgs, so you cannot probe for the right one. Use `org_id_hint`, and only
fall back to this list if it is null.

---

## 3. Upload each skill

**Hand the browser the ZIP *files*; never carry them as base64.** A single
skill can be 200KB+ of base64 (`sync-skills` itself is ~218KB), and pasting
that into a `javascript_tool` call spends it all as context and risks
truncating the script. The file-input route below moves zero payload bytes
through the conversation.

Open a claude.ai tab first — the POST needs that origin's session cookies.

**Step 3a — inject a file input** into the claude.ai page:

```javascript
let el = document.getElementById('agentZipInput');
if (!el) {
  el = document.createElement('input');
  el.type = 'file';
  el.id = 'agentZipInput';
  el.setAttribute('aria-label', 'agent zip input');
  el.style.cssText = 'position:fixed;top:8px;left:8px;z-index:2147483647;width:320px;height:36px;background:#fff;';
  document.body.appendChild(el);
}
JSON.stringify({injected: true, files: el.files.length})
```

**Step 3b — locate it** with `find` (query: `file input with aria-label
"agent zip input"`) to get a `ref_N`.

**Step 3c — load the ZIPs into it** with the `file_upload` tool, passing
the `zip_path` values from §1 (it accepts several at once; the combined
limit is 10MB).

**Step 3d — POST them.** This reads the files straight from the input, so
`ORG_ID` is the only substitution:

```javascript
const orgId = "ORG_ID";
const el = document.getElementById('agentZipInput');
// name -> expected byte size, from the payload's zip_bytes. The guard is
// the only check that the file the browser picked up is the file the
// helper built; without it a stale or truncated pick uploads silently.
const expected = EXPECTED_SIZES;   // e.g. {"sync-skills.zip": 163439}
const results = [];
for (const f of Array.from(el.files)) {
  if (expected[f.name] !== f.size) {
    results.push({file: f.name, skipped: `size ${f.size} != expected ${expected[f.name]}`});
    continue;
  }
  // overwrite=true when the payload's is_update is true for this skill.
  const url = `https://claude.ai/api/organizations/${orgId}/skills/upload-skill?overwrite=true`;
  const form = new FormData();
  form.append('file', f, f.name);
  const resp = await fetch(url, {method: 'POST', body: form, credentials: 'include'});
  results.push({
    skill: f.name.replace(/\.zip$/, ''),
    status: resp.status,
    body: (await resp.text()).slice(0, 500),
  });
}
JSON.stringify({results}, null, 1)
```

Remove the injected input when you're done:
`document.getElementById('agentZipInput')?.remove()`.

**`overwrite=true` replaces the skill; it does not create a duplicate.**
The response carries a *new* `skillId` each time, which looks alarming but
is just how the server versions an overwrite — the account still holds one
copy. Confirmed by refreshing the mirror after an overwrite and finding no
duplicate names.

### Things that look like they should work and don't

- **Serving the ZIPs over `http://127.0.0.1` and fetching them from the
  page.** claude.ai's CSP `connect-src` doesn't include localhost, so the
  fetch hangs until aborted and the local server never logs a request.
- **Running the upload from the localhost page instead.** Then the POST is
  cross-origin and never carries claude.ai's session cookies.

### If you genuinely have no ZIP on disk

The base64 form still works — substitute `ZIP_B64` from a `--prepare` run
made *without* `--zip-dir`:

```javascript
const binary = atob("ZIP_B64");
const bytes = new Uint8Array(binary.length);
for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
const form = new FormData();
form.append('file', new Blob([bytes], {type: 'application/zip'}), "SKILL_NAME.zip");
const resp = await fetch(
  `https://claude.ai/api/organizations/ORG_ID/skills/upload-skill?overwrite=OVERWRITE`,
  {method: 'POST', body: form, credentials: 'include'});
JSON.stringify({status: resp.status, body: (await resp.text()).slice(0, 400)});
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
python3 "$SKILL_DIR/sync_skills.py" \
  --mark-synced "SKILL_NAME:HASH"
```

Substitute the `name` and `hash` fields from the JSON payload.

---

## 5. Dry run / troubleshooting

To preview what would be synced without uploading:

```bash
python3 "$SKILL_DIR/sync_skills.py" --dry-run
```

The preview answers `UPDATE` vs `NEW` from the same place the real run does:
the account mirror first, then `~/.sync-skills-state.json`. It used to read
the state file alone, so on a machine that had never uploaded anything it
previewed `NEW` for skills the run then correctly sent as updates. It also
lists a skill only for the repo that actually holds it — a requested name
was previously printed once per resolved repo, which reads as a second copy
and invites an upload from the wrong tree.

To target a single skill:

```bash
python3 "$SKILL_DIR/sync_skills.py" --skill fastmail
```

Only this script's own checkout is found without being told. Every other
clone — `agentskills-private`, a scratch checkout, a non-standard drive —
has to be named:

```bash
python3 "$SKILL_DIR/sync_skills.py" --prepare --all \
  --repos /path/to/agentskills /path/to/agentskills-private
```

`--skill` and `--all` are mutually exclusive — asking for both used to
silently sync only the single named skill.

When a run checks nothing, the error says which of three things happened,
and always lists the repos it resolved with a skill count for each:

- **nothing was selected** — no `--all`, no `--skill`, and the git-diff
  path found no changes. Pass one of them.
- **something was selected but no resolved repo contains any of it** — the
  flags are fine; this is repo resolution. The counts tell you which tree
  it decided to call the registry.
- **`--skill NAME` matched nothing** — check the spelling against the
  counts.

Reading the second as the first is what cost three round-trips on the
Windows report: `--all` was on the command line and the message asked for
`--all`.

If the run stops before any of that with a branch or `origin/main`
complaint, that is the repo-state gate — see "Locating the helper" above,
and `--yes` to override it.

---

## 6. Fallback: single-file skills only (no local repo)

**Precondition — read before using this path.** This fallback uploads
**only `SKILL.md`.** If the skill folder has a `scripts/`, `references/`,
or `assets/` directory — or any file besides `SKILL.md` — this path
silently truncates the upload: the server-side overwrite replaces the
whole skill with just the one file, and there is no error to catch it.
If the repo is available locally, section 1/3 (`sync_skills.py
--prepare`) is **mandatory** instead — it zips the whole folder, not
just one file. This exact mistake already cost three skills their
payloads: `sync-skills`, `sync-cc-settings-between-wsl-and-windows`, and
`github-actions-repo-settings` were each reduced to a bare `SKILL.md` on
claude.ai after being pushed through this fallback. Only reach for this
path when the repo genuinely isn't available locally **and** you've
confirmed — by counting the files in the skill folder — that `SKILL.md`
really is the only one.

Earlier versions of this skill noted that the upload endpoint accepted a
bare `.md` file. **That is no longer the case** — the server now rejects
`text/markdown` uploads with `skill_upload_invalid_file_type` (only
`.zip` or `.skill` extensions are accepted, and `.skill` is parsed as a
ZIP container, not a single-file format).

**Why base64: `SKILL_MD_B64` is base64, never raw Markdown.** The
substituted value is the base64 encoding of the SKILL.md **bytes**. An
earlier version of this snippet carried the payload in a JS template
literal (`` const skillMd = `SKILL_MD_CONTENT`; ``), which cannot work: a
SKILL.md body contains backticks (fenced code blocks, inline code) and
`${...}` sequences, so the substituted script is a **syntax error** — it
dies at parse time, before the `expectedFileCount` guard below can run, so
the guard never fires. Measured against every single-file skill in the
registry that this section legitimately applies to: 8 of 8 failed
`node --check`. Base64 has no character that can terminate the string
early, so it survives any SKILL.md.

Produce it with `base64 -w0 SKILL.md` (Linux/WSL),
`base64 -i SKILL.md` (macOS), or
`[Convert]::ToBase64String([IO.File]::ReadAllBytes("SKILL.md"))`
(PowerShell).

When you don't have `sync_skills.py --prepare` available locally (e.g.
the local repo doesn't exist on this machine, or you only have the raw
`SKILL.md` content in hand) and you've confirmed the skill has no other
files, build a minimal STORE-mode ZIP in the browser and upload that:

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
  // SKILL_MD_B64 is the base64 of the SKILL.md bytes — NEVER the raw text.
  // See "Why base64" below; pasting Markdown here does not parse.
  const skillMd = new TextDecoder().decode(
    Uint8Array.from(atob("SKILL_MD_B64"), c => c.charCodeAt(0)));
  const expectedFileCount = N;             // the total number of files in the skill folder — count them before filling this in

  // ----- hard guard: this fallback can only ever upload one file -----
  // If the skill folder has more than SKILL.md, this path silently
  // truncates the upload and wipes those files from the account copy.
  // See the precondition above and sections 1/3 for anything with
  // scripts/, references/, or assets/.
  if (expectedFileCount !== 1) {
    throw new Error(
      `sync-skills fallback refused: skill has ${expectedFileCount} files, ` +
      `not 1 — this path only uploads SKILL.md and would silently drop the ` +
      `rest. Use section 1/3 (sync_skills.py --prepare) instead.`
    );
  }

  const zipBytes = makeZip(`${skillName}/SKILL.md`, skillMd);
  const url = `https://claude.ai/api/organizations/${orgId}/skills/upload-skill?overwrite=${overwrite}`;
  const form = new FormData();
  form.append('file', new Blob([zipBytes], {type:'application/zip'}), `${skillName}.zip`);
  const resp = await fetch(url, { method:'POST', body: form, credentials:'include' });
  return { status: resp.status, ok: resp.ok, body: (await resp.text()).slice(0, 800) };
})();
```

`sync_skills.py --prepare` (sections 1/3) uploads with `SKILL.md` at the
ZIP root, and the server keys the skill name correctly regardless —
evidenced by `rename-pdfs`, which was uploaded that way and landed on
claude.ai under the right name with its full payload intact (`SKILL.md`,
`scripts/extract_pdf_context.py`, `scripts/test_extract_pdf_context.py`,
all present and correct). So a root-relative ZIP layout is fine. The
`<skill-name>/SKILL.md` prefix form this fallback builds above also
works. Neither form is required over the other.

## 7. Verify

A sync is **not complete** until this passes — for every upload path,
not just section 6's fallback. The known failure mode (`SKILL.md`
uploaded, everything else silently dropped) returns no HTTP error, so
the only way to catch it is to check what actually landed on the
account afterward.

First, refresh the local mirror of the account store. This is what
populates `~/.claude/skills/synced/`, and it's stale until you run it:

```bash
CLAUDE_CODE_SYNC_SKILLS=1 claude -p 'ok'
```

Then run:

```bash
python3 "$SKILL_DIR/sync_skills.py" --verify
```

**The refresh is not optional.** `--verify` reads that mirror, so skipping
it compares your uploads against a *pre-upload* snapshot and reports OK for
things that never landed. `--verify` now refuses to run against a mirror
older than 6 hours rather than silently trusting it, and prints each
skill's account `updatedAt` beside its verdict so you can see which upload
a verdict actually describes.

### What "should be on the account" means

Verdicts are read against **`account-skills.txt`** — the declared list of
skills that belong on the claude.ai account store, sitting beside
`sync_skills.py`. Read its header (and
[ADR 0002](../../../../docs/decisions/0002-limit-account-store-to-repo-independent-skills.md))
before changing it: adding a line is close to a one-way door, because the
upload API has no delete.

That declaration is what makes "absent from the account" readable at all.
It means two opposite things — a **missing upload** for a skill that
belongs there, and the **correct resting state** for one that doesn't —
and most of this registry is deliberately in the second category. Without
the list, `--verify --all` flagged every repo-scoped skill as a failure and
buried the four real ones under thirteen expected ones.

It prints one line per skill checked and exits non-zero unless every one
passed:

| Verdict | Declared? | On the account? | Meaning |
| --- | --- | --- | --- |
| `OK` | yes | yes | Every file present and byte-identical (CRLF normalised). |
| `DRIFT` | yes | yes | Same files, **different contents** — names the differing paths. |
| `MISMATCH` | yes | yes | File set differs — lists missing/extra paths. |
| `FAIL` … *declared … but NOT on it* | yes | no | The **upload never happened**. Nothing is wrong with the local skill — it just hasn't been pushed. Push it (sections 1/3) and re-verify. |
| `FAIL` … *ON the account but NOT declared* | no | yes | It was **uploaded without a membership ruling**. Either declare it in `account-skills.txt` or delete it by hand in the claude.ai UI — there is no delete API. |
| one summary line | no | no | Correct and expected; collapsed to a single `(N not declared …)` line so it never drowns the verdicts. |

**Read the two `FAIL` wordings as different bugs.** *Declared but not on
it* is a **push you still owe**; `DRIFT`/`MISMATCH` is a **push that landed
wrong**. Re-uploading fixes the first; the second means the upload path
itself misbehaved (section 6 truncation is the usual cause) and needs
investigating before you re-push.

It checks the same skill set you just synced (git-changed by default; pass
`--all` or `--skill NAME` if that's what you used to sync). Selecting
nothing, or naming a skill that exists in no repo, is an **error** — a
silent exit 0 there is indistinguishable from a clean run, which is how a
broken account passed this gate for months. So is `--skill NAME` for an
**undeclared** skill: asking to verify something that isn't supposed to be
on the account is an operator mistake, not a pass.

`--account-list PATH` points the gate at a different membership list — use
it to dry-run a proposed membership change before committing one. A path
that isn't readable is an error rather than a silent fall-back to the
shipped list.

`--prepare` warns on stderr if the payload contains an undeclared skill. It
does **not** filter the payload — you decide what to POST — but the warning
has to arrive before the upload, because `--verify` catching it afterwards
cannot undo it.

`--prepare` also warns when the `is_update` flag it just computed — the one
that becomes `overwrite=` on the POST — was decided against an account mirror
it cannot trust: missing, unstamped, or older than the same six hours
`--verify` refuses. The payload is still built; the doubt is simply stated
before the upload rather than discovered as a `409` during it.

If `--verify` reports anything but `OK`, treat the sync as failed:
re-upload that skill through section 1/3 (never section 6 — that's what
causes `MISMATCH`), then re-run `--verify` before reporting success.

Content is compared with CRLF normalised on **both** sides, because the
account store's line endings vary by upload batch and are not a content
change. Do **not** "fix" that by normalising newlines in `zip_skill()` —
that would rewrite the bytes of every upload to chase a legacy artefact of
one 2026-05-11 batch, and the compare-time normalisation already handles it.

## 8. Reporting

After all uploads, summarise:

```
Synced 3 skills:
  [OK]  fastmail           (updated)  agentskills
  [OK]  skills-doctor      (new)      agentskills
  [FAIL] some-skill        status 403
```

If any skill failed, explain the error and suggest remedies (re-authenticate
on claude.ai, check org_id, try overwrite flag).
