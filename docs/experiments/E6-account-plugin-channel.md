# E6 — Can plugins on the claude.ai account replace the skill ZIP uploads?

**Status: open — the upload test passed; three small checks remain** (§4).

**Short answer: yes, it can replace the ZIP uploads.** On 2026-09-23 Adam
uploaded a throwaway personal plugin, `e6-probe`. Its skill returned its unique
token in the iOS app, the Claude in Chrome side panel, claude.ai chat,
claude.ai Cowork and the Desktop Chat tab (§3.5). Those include both surfaces
the ZIP uploads exist for
([ADR 0002](../decisions/0002-limit-account-store-to-repo-independent-skills.md)).
Plugins can also be deleted, which the ZIP uploads cannot. It did **not**
appear in Desktop Cowork (the local Cowork tab), and that result counts only
once a control is checked there. Personal uploads reach Claude Code only
through the `My Uploads` marketplace, not the regular sync (§3.5).
`sync-skills` stays until §4's checks are done.

Tracked in [#160](https://github.com/Adam-S-Daniel/agentskills/issues/160).
[#158](https://github.com/Adam-S-Daniel/agentskills/issues/158) waits on the
`syncClaudeAiPlugins` recommendation in §6. Nothing was uploaded, enabled or
added on the account to produce this document.

---

## 1. The two channels side by side

| | ZIP uploads (`sync-skills`, today) | Account plugins |
|---|---|---|
| chat (web), Desktop Chat tab, Cowork | yes | yes — documented |
| Claude Code terminal and cloud sessions | yes | yes — documented and measured |
| **Claude in Chrome** | yes | **yes**, including a personal upload (measured 2026-09-23) |
| **Mobile app (iOS)** | yes | **yes**, including a personal upload (measured 2026-09-23) |
| Desktop Cowork (local) | not checked | **personal upload not found**; control not yet checked |
| Can be deleted | only by hand in the UI; the uploader has no delete | yes, at four layers (§2.3) |
| Needs an uploader | yes — a browser session, one skill at a time | no, if a personal marketplace can sync from this repo (§5 step 4) |
| Pinned to a commit | no — whatever was last uploaded | no — follows the latest version (§2.4) |
| Scoped to a repo | no | no (§3.3) |

## 2. What the documentation says

### 2.1 Personal accounts can have plugins

"Plugins are available to all paid plans (Pro, Max, Team, Enterprise)"
([Use plugins in Claude](https://support.claude.com/en/articles/13837440-use-plugins-in-claude)).
The org article #160 cites
([Manage plugins for your organization](https://support.claude.com/en/articles/13837433))
covers a different thing, the Team/Enterprise library. A personal account has
two ways in, both under **Customize → Plugins → Personal plugins → "+"**:

- **upload a plugin file** — the same manual step as a ZIP upload, but deletable;
- **add a marketplace from a GitHub repository or git URL** — this repo already
  is a marketplace, so that route would need no uploader at all.

### 2.2 Where a plugin shows up

The support article names "chat on the web, the Chat tab in Claude Desktop, and
Claude Cowork", and says "the skills bundled in a plugin work across all three".
Hooks and sub-agents run only in Cowork. That does not matter here, because this
repo's bundles contain only skills. Claude Code adds its own sessions: "In Cowork
and cloud sessions, Claude Code downloads them into the session's own
environment", and terminal sessions sync them from v2.1.273
([synced plugins](https://code.claude.com/docs/en/plugins-reference#synced-plugins)).
**Neither page mentions Chrome or mobile.** That silence does not mean they are
unsupported, which is why §5 checks them directly.

### 2.3 Removal works, at four layers

| Layer | How | Source |
|---|---|---|
| The marketplace | Customize → Plugins → Remove | [Use plugins in Claude](https://support.claude.com/en/articles/13837440-use-plugins-in-claude) |
| The plugin, everywhere | "Deleting a plugin removes it for everyone you shared it with" | same |
| Every Claude Code session | turn it off on claude.ai; "Claude Code removes it at the next sync" | [synced plugins](https://code.claude.com/docs/en/plugins-reference#synced-plugins) |
| One machine | `syncClaudeAiPlugins: false` in **user** settings moves synced plugins to `~/.claude/plugins/.trash/` | [`syncClaudeAiPlugins`](https://code.claude.com/docs/en/settings-reference#syncclaudeaiplugins) |

`.trash` is a local undo on one machine. It does not delete anything from the
account.

This is the strongest argument for the channel. With a marketplace synced from
this repo, deleting a skill directory in git would delete the skill everywhere.
Today it leaves an orphan on the account, which is what
[PR #62](https://github.com/Adam-S-Daniel/agentskills/pull/62) had to clean up.

"Required by your org" plugins, which cannot be turned off, need a Team or
Enterprise owner. This account has none, so every plugin on it stays disableable.

### 2.4 No pinning

When a plugin entry omits `version`, "Claude Code uses the source's resolved
commit SHA, so users get an update whenever that commit changes"
([Version resolution](https://code.claude.com/docs/en/plugin-marketplaces#version-resolution-and-release-channels)).
Org-synced marketplaces read the default branch
([GitLab sync](https://code.claude.com/docs/en/plugin-marketplaces#sync-a-gitlab-hosted-marketplace)).
Whether a *personal* marketplace does the same is **inferred, not documented**.
Either way the channel tracks `main`. That is better than the ZIP copies, which
drift until someone re-uploads
([E5](E5-account-store-vs-hook-precedence.md) measured one 127 lines behind),
but it is not a pin. Setting `version` is the only documented pin;
[ADR 0009](../decisions/0009-bump-bundle-versions-on-every-release.md) already
bumps it on every release.

### 2.5 Name clashes: plugins give way, skills double up

If a synced plugin has the same name as a plugin installed another way, "Claude
Code loads that plugin and reports the synced copy as not loaded"
([synced plugins](https://code.claude.com/docs/en/plugins-reference#synced-plugins)).
Synced *skills* do the opposite: both copies stay loaded
([skills](https://code.claude.com/docs/en/skills#when-a-synced-skill-name-matches-another-command)).
That doubling is the problem in #158. So a bundle enabled on the account would
cost nothing extra in a repo that already installs the same bundle.

## 3. What was measured

Every row below is measured, read-only. Where a row interprets rather than
measures, it says so.

| Date | Where | Finding |
|---|---|---|
| 2026-09-18 | cloud session, CLI 2.1.276 | Plugins bucket empty; `claude plugin list --json` → `[]`. This is a real zero: the skills bucket beside it held 21 skills, and a local test plugin did show up in the same command. |
| 2026-09-20 | laptop, WSL, CLI 2.1.278 | **Six plugins syncing from the account**: `pdf-viewer`, `productivity`, `design`, `finance`, `engineering`, `data`. All six are Anthropic's, from the default `knowledge-work-plugins` marketplace. |
| 2026-09-22 | laptop, Windows 2.1.278 and WSL 2.1.280 | The same six. Five had been updated on the account on 2026-09-21 without their own version numbers changing. |
| 2026-09-22 | laptop, Windows | `claude plugin marketplace list` shows a claude.ai-hosted **`My Uploads`** marketplace ("not added"). **Adam checked the claude.ai UI the same day and it shows no "My Uploads" or anything similar.** So the CLI lists something the UI does not show. What it is remains unknown. |
| 2026-09-22 | laptop | Four uploaded skills now carry a `backingPluginId` in the synced-skills manifest: `wj-next-break`, `pdf-ocr-audit`, `writing-adrs`, `sync-cc-settings-between-wsl-and-windows`. None of them was re-uploaded. **Inferred:** claude.ai may be moving uploads onto plugins behind the scenes. The UI shows nothing of it. |
| 2026-09-22 | laptop, Windows | `~/.claude/plugins/.trash/` holds 8 old versions of the six plugins, and nothing was turned off. So updates write to `.trash` too, and "something is in `.trash`" is not proof of a removal. |

### 3.1 The six Anthropic plugins cost context in every session

From `claude plugin details <name>@synced`, 2026-09-22:

| Plugin | Skills | MCP servers | Always-on tokens |
|---|---|---|---|
| `pdf-viewer` | 5 | 1 | ~226 |
| `productivity` | 4 | 9 | ~352 |
| `design` | 7 | 9 | ~617 |
| `finance` | 8 | 6 | ~752 |
| `engineering` | 10 | 10 | ~871 |
| `data` | 10 | 8 | ~1,126 |
| **total** | **44** | **43** | **~3,944** |

That cost lands in every Claude Code terminal session, in every repo. MCP tool
schemas are not counted, so the real cost is higher. This is ADR 0002's
objection — account content cannot be scoped to a repo — arriving through the
plugin channel.

### 3.2 None of this repo's bundles matches what is on the account

| Bundle | Always-on | Skills already on the account | Skills it would add |
|---|---|---|---|
| `adam` | ~1,895 | 3 | 6 CI/GitHub skills (~1,230 tokens) |
| `adam-local` | ~2,536 | 6 | 4 |
| `fastmail` | ~728 | 1 | 2 |

Enabling the three bundles would add 12 skills the account deliberately does not
carry, about 2,970 extra tokens on every surface. `adam`'s six are exactly what
ADR 0002 rejected putting on the account. Using this channel properly means a
separate personal bundle. Creating one moves skills between bundles, which
touches the append-only `renames` map and needs an adversarial review round
(AGENTS.md).

### 3.3 Still nothing repo-scoped

The per-plugin record has seven fields, plus `generation` on newer entries. The
per-skill record has five, plus `backingPluginId` on four. The only `scope` field
anywhere says `account`. So [ADR 0002](../decisions/0002-limit-account-store-to-repo-independent-skills.md)'s
finding, that account content cannot be scoped to a repo, holds for plugins too.

### 3.4 Chrome and iOS, checked by Adam (2026-09-23)

| Surface | Account plugin `design:design-critique` | Control: uploaded skill `rename-pdfs` | Verdict |
|---|---|---|---|
| Claude in Chrome side panel | offered in the prompt box's `/` list | offered | counts: Chrome receives account plugins |
| iOS app | works | works | counts: iOS receives account plugins |

Both plugin results are from Anthropic's default `knowledge-work-plugins`
marketplace. They show that the surfaces load account plugins, but not yet
that a plugin Adam uploads himself arrives the same way.

### 3.5 The upload test: a personal plugin (2026-09-23)

`e6-probe`: one plugin, one skill, which replies with exactly
`E6-PROBE-GRMFYBS7`. Uploaded by Adam through Customize → Plugins → Personal
plugins. The skill uploader rejects a plugin zip ("A skill cannot contain a
plugin manifest"), so the two doors really are separate. The zip has to
contain exactly one top-level folder.

| Surface | Token returned |
|---|---|
| iOS app | **yes** |
| Claude in Chrome side panel | **yes** |
| claude.ai chat | **yes** |
| claude.ai Cowork | **yes** |
| Claude Desktop, Chat tab | **yes** |
| Claude Desktop, Cowork tab (local) | **no** — counts only once a control (`design:design-critique` or `rename-pdfs`) is checked there in the same sitting |
| Claude Code terminal (laptop) | not in the regular sync: at 18:16 UTC `claude plugin list` still showed only the six Anthropic plugins |

**Where personal uploads live.** Since 2026-09-21 the command line has listed a
claude.ai-hosted `My Uploads` marketplace (`claudeai-my-uploads`, "not added"),
which the web UI does not show (§3). This test explains it: personal uploads
sit in that marketplace, and the regular sync does not bring them to Claude
Code. A machine gets them by adding it with
`claude plugin marketplace add --claudeai claudeai-my-uploads` — **inferred**,
not yet run. For Claude Code this matters little, because repos already install
this registry's bundles through the marketplace, pinned (ADR 0010).

## 4. What is still unknown

1. **Desktop Cowork:** is the missing token real? Check `design:design-critique`
   and `rename-pdfs` there. If neither appears, the check failed and records
   nothing. If a control appears but `e6-probe` doesn't, local Cowork doesn't
   receive personal plugins.
2. **Removal:** delete `e6-probe` on claude.ai and confirm it disappears from
   at least one surface. This is the property that justifies switching.
3. **Repo-synced route:** does a personal marketplace accept this public repo,
   and does it follow `main`? This decides whether the uploader can go
   entirely, or whether uploads just change from ZIP files to plugin files.
4. Does `syncClaudeAiPlugins: false` move plugins to `.trash`? This is
   documented but not yet witnessed; it isn't needed for the decision.
5. What the four `backingPluginId`s on uploaded skills are. Nothing depends on
   them.

## 5. How to finish it

**Rule for every check** (from [E4](E4-federated-bundle-delivery.md)): a
"not found" counts only if, on the same surface in the same sitting, a known
control *is* found. Otherwise the result is void.

1. **Claude in Chrome** — **done 2026-09-23: found** (§3.4).
2. **Mobile app** — **done 2026-09-23 on iOS: found** (§3.4).
3. **Upload test** — **done 2026-09-23: token on five surfaces**, not in
   Desktop Cowork (§3.5). Still to do: the Desktop Cowork control, then delete
   `e6-probe` and confirm it disappears (§4 items 1–2).
4. **Next:** add `Adam-S-Daniel/agentskills` as a personal marketplace
   (Customize → Plugins → "+" → Add marketplace → Add from a repository).
   Record whether a public repo is accepted, then how long a pushed change
   takes to arrive. Remove the marketplace afterwards; this repo's bundles
   should not stay on the account (§3.2).

| Result | Meaning | Action |
|---|---|---|
| No plugin on Chrome or mobile (control found) | Account plugins reach 3 of the 5 surfaces | **Keep `sync-skills`** for Chrome and mobile. Account plugins stay optional, and the experiment ends. |
| Test plugin found on all five | Account plugins can do everything the ZIPs do, and can delete | **Retire the uploader** in favour of a personal bundle. New ADR amending ADR 0002's "one-way door" consequence. |
| Found on one of Chrome/mobile only | Partial | Keep `sync-skills` for the other surface; re-decide on how many skills that leaves. |
| Step 4 refuses a public repo | The no-uploader route is closed | Only plugin-file uploads remain: the ZIP workflow, but with delete. Small gain. |
| Neither the plugin nor the control found | The check failed | Record nothing; re-run. |

## 6. Recommendations

1. **Keep `sync-skills`, `--verify` and the
   [ADR 0006](../decisions/0006-drive-the-account-store-drift-loop-from-one-published-artifact.md)
   drift loop unchanged** until §4 items 1–3 are answered. Then retire them
   in a new ADR that amends ADR 0002's "one-way door" consequence.
2. **Plan the switch to a dedicated personal plugin.** It would hold exactly
   the skills the account carries today (§3.2), be uploaded as one plugin file
   or synced from the repo (§4 item 3), and be deletable. Creating it moves
   skills between bundles, which touches the append-only `renames` map, so it
   needs the adversarial review round in AGENTS.md before merge.
3. **Do not enable this repo's bundles on the account** (§3.2).
4. **For #158: leave `syncClaudeAiPlugins` unset, but not because it is free.**
   [ADR 0010](../decisions/0010-let-pinned-channels-own-the-terminal.md)
   settled #158 for *skills*: laptops that `setup.sh` manages set
   `syncClaudeAiSkills: false`. It left plugins to this experiment because "the
   bucket … is empty today", which stopped being true on 2026-09-20. Setting
   `syncClaudeAiPlugins` to `false` would also block any personal plugin this
   experiment might adopt. The cost is the six Anthropic plugins (§3.1). The
   better lever is turning off, on claude.ai, the ones not used. That applies
   to every surface and is reversible.
5. **Leave `My Uploads` un-added in Claude Code.** Terminals already get this
   registry's skills pinned through the marketplace (ADR 0010). Adding it would
   load personal uploads into every repo session, which is ADR 0002's
   objection.

## 7. Reproducing (read-only)

```bash
claude --version
claude plugin list --json
claude plugin marketplace list
claude plugin details <name>@synced
ls ~/.claude/plugins/synced/*/ ~/.claude/skills/synced/*/ ~/.claude/plugins/.trash
cat ~/.claude/plugins/synced/*/manifest.json ~/.claude/plugins/synced/*/.marketplaces.json
```

Do not run `claude plugin marketplace add` on a machine that `setup.sh` manages:
it writes user-level `extraKnownMarketplaces`, which `setup.sh` owns. The
2026-09-18 test plugin was added and removed in a throwaway cloud container only.
