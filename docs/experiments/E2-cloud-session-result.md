# E2 — Cloud-session probe: SessionStart skill bootstrap

**Verdict: POSITIVE.** The `SessionStart` hook (`.claude/hooks/skills-bootstrap.sh`)
ran on the Claude Code on the web boot path, installed the `adam` bundle into
`~/.claude/skills/`, the skills were loaded into the model's skill listing for
the first turn, and the hook's `additionalContext` string reached the model.

- Surface: Claude Code on the web / remote cloud session, CLI `2.1.231`
- Session: `cse_01H13PoHmG9AUGDo33EhxYAc`
- Branch: `claude/skills-centralization-propagation-5mxlmp`
- Probe run: 2026-08-13 (hook fired 14:33:11 UTC)

---

## 1. THE KEY QUESTION — skills present in the model's own context

Read off the skill listing supplied to the Skill tool in this session's context
(not from disk):

| Skill | Status |
|---|---|
| `finding-unknowns` | **present** |
| `writing-adrs` | **present** |
| `debug-github-workflows` | **present** |
| `review-bash-ci-reliability` | **present** |

All four present. None of these exist in the claude.ai account-upload set (see
`~/.claude/skills/synced/` listing in §2 — they are absent there), so the
`SessionStart` hook is the only possible source.

### Every other non-obvious-built-in skill in the listing

**Also from the `adam` bundle (hook-installed), name-collides with an account upload:**
`adam-writing-style`, `github-actions-repo-settings`, `pin-actions-to-sha`,
`workflow-path-audit` — these appear both at `~/.claude/skills/<name>/` (hook)
and in `~/.claude/skills/synced/<name>/` (account upload). Each appears exactly
once in the model's listing, so the two sources are deduplicated rather than
double-loaded. Which copy won was **inconclusive** from the model's view alone;
[E5](E5-account-store-vs-hook-precedence.md) settled it by measurement — the
**hook copy wins**, and the Skill tool names `~/.claude/skills/<name>/` as its
base directory.

**From the account-upload / `synced/` set:**
`doc-coauthoring`, `docx`, `fastmail`, `learn`, `ocr-pdfs`, `pdf`,
`pdf-ocr-audit`, `pptx`, `rename-pdfs`, `skill-creator`,
`sync-cc-settings-between-wsl-and-windows`, `sync-skills`, `theme-factory`,
`web-artifacts-builder`, `wj-next-break`, `xlsx`.

**Present but from neither the hook nor `synced/`:**
`session-start-hook` — see the discrepancy note in §2.

**Judged Anthropic built-ins (listed for completeness, not counted above):**
`dataviz`, `artifact-design`, `artifact-diagramming`, `artifact-capabilities`,
`update-config`, `keybindings-help`, `code-review`, `simplify`,
`fewer-permission-prompts`, `loop`, `claude-api`, `run`, `init`,
`security-review`.

---

## 2. Did the hook run? — raw command output

### `ls -la ~/.claude/skills/`

```
total 48
drwxr-xr-x 12 root root 4096 Aug 13 14:33 .
drwxr-xr-x  8 root root 4096 Aug 13 14:33 ..
drwxr-xr-x  2 root root 4096 Aug 13 14:33 adam-writing-style
drwxr-xr-x  2 root root 4096 Aug 13 14:33 debug-github-workflows
drwxr-xr-x  3 root root 4096 Aug 13 14:33 finding-unknowns
drwxr-xr-x  4 root root 4096 Aug 13 14:33 github-actions-repo-settings
drwxr-xr-x  2 root root 4096 Aug 13 14:33 pin-actions-to-sha
drwxr-xr-x  2 root root 4096 Aug 13 14:33 review-bash-ci-reliability
drwxr-xr-x  2 root root 4096 Aug 13 14:33 session-start-hook
drwxr-xr-x 22 root root 4096 Aug 13 14:33 synced
drwxr-xr-x  2 root root 4096 Aug 13 14:33 workflow-path-audit
drwxr-xr-x  3 root root 4096 Aug 13 14:33 writing-adrs
```

### `cat /tmp/skills-bootstrap.log` (`$TMPDIR` is unset, so `/tmp` is the path the hook used)

```
installed=8 ref=main sha=65893a1 bundle=adam
```

No clone-failure output preceded that line, i.e. the `git clone` of the
registry succeeded.

### `ls -1 ~/.claude/skills/synced/ | head -30`

```
adam-writing-style
doc-coauthoring
docx
fastmail
github-actions-repo-settings
learn
manifest.json
ocr-pdfs
pdf
pdf-ocr-audit
pin-actions-to-sha
pptx
rename-pdfs
skill-creator
sync-cc-settings-between-wsl-and-windows
sync-skills
theme-factory
web-artifacts-builder
wj-next-break
workflow-path-audit
xlsx
```

(21 entries, not truncated by `head -30`.)

### Cross-check: `installed=8` vs 9 top-level directories

`~/.claude/skills/` holds 9 directories besides `synced/`, but the log says
`installed=8`. Resolved, not a miscount:

- `git ls-tree origin/main plugins/adam/skills/` returns exactly **8** skills:
  `adam-writing-style`, `debug-github-workflows`, `finding-unknowns`,
  `github-actions-repo-settings`, `pin-actions-to-sha`,
  `review-bash-ci-reliability`, `workflow-path-audit`, `writing-adrs`.
  `git rev-parse --short origin/main` = `65893a1`, matching the log's `sha=`.
- The 9th directory, `session-start-hook`, has mtime **14:33:04**, seven seconds
  *before* the eight hook-copied directories (all **14:33:11**), and its
  `SKILL.md` frontmatter declares `name: startup-hook-skill` — a different
  provenance. It is not in `origin/main`'s bundle and not in `synced/`; it was
  placed by the platform's own pre-session skill install, not by this hook.

So the hook's own footprint is exactly the 8 bundle skills, and all 8 are in the
model's listing.

---

## 3. Was the hook's `additionalContext` visible to the model?

**Yes.** It appeared at the top of this session's context, before the first user
turn, as a system line reading:

```
SessionStart hook additional context: skills-bootstrap: installed 8 skills from Adam-S-Daniel/agentskills@main (65893a1), bundle=adam, into ~/.claude/skills.
```

Conclusion: `SessionStart` `additionalContext` **does** reach the model on the
Claude Code on the web surface, and the `reloadSkills` signal took effect — the
newly copied skills were in the listing for turn one, not only on disk.

---

## 4. Surface fingerprint

```
$ claude --version
2.1.231 (Claude Code)

CLAUDE_CODE_ENTRYPOINT=remote
CLAUDE_CODE_REMOTE_ENVIRONMENT_TYPE=cloud_default
CLAUDE_CODE_REMOTE_SESSION_ID=cse_01H13PoHmG9AUGDo33EhxYAc
CLAUDECODE=1
CLAUDE_CODE_VERSION=2.1.42
```

Note `claude --version` (`2.1.231`) and `$CLAUDE_CODE_VERSION` (`2.1.42`)
disagree; the env var appears to be set by the remote environment rather than by
the CLI on `PATH`. Not investigated further.

Both arms of the hook's surface guard were satisfied
(`CLAUDE_CODE_REMOTE_SESSION_ID` non-empty **and** `CLAUDE_CODE_ENTRYPOINT=remote`),
so the ephemeral-session branch was taken as designed.

---

## 5. Plugins state

```
$ ls -la ~/.claude/plugins/
ls: cannot access '/root/.claude/plugins/': No such file or directory

$ cat ~/.claude/plugins/installed_plugins.json
cat: /root/.claude/plugins/installed_plugins.json: No such file or directory

$ claude plugin marketplace list
No marketplaces configured
```

`~/.claude/plugins/` does not exist and no marketplace is configured. This
confirms the known limitation recorded in `docs/decisions/0001` — cloud sessions
get **no** plugins from repo-declared settings — and it independently rules out
the marketplace as an alternative source for the four key skills.

---

## 6. Approval / trust prompts, blocking, or skipping

**No prompt of any kind.** Nothing asked the model to approve or trust the hook,
and there was no indication the hook was blocked, skipped, or deferred. The hook
executed before the first turn under whatever permission mode the session booted
with, and the only evidence of it in the model's context was the
`additionalContext` line quoted in §3. The positive disk and log evidence in §2
confirms it ran to completion rather than being silently suppressed.

---

## Summary

| Question | Result |
|---|---|
| Four bundle-only skills in the model's context | **All 4 present** |
| Hook executed | **Yes** — log `installed=8 ref=main sha=65893a1 bundle=adam` |
| Registry clone reachable from the cloud sandbox | **Yes** |
| `additionalContext` reached the model | **Yes**, verbatim |
| Skills live for turn one (`reloadSkills`) | **Yes** |
| Marketplace/plugins as alternate source | **Ruled out** — `~/.claude/plugins/` absent, no marketplaces |
| Approval prompt / block / skip | **None** |
