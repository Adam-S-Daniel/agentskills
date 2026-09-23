# E6 — Can plugins on the claude.ai account replace the skill ZIP uploads?

**Status: open — one upload test left.** On 2026-09-23 Adam confirmed that
Claude in Chrome and the iOS app both offer account plugins (§3). What remains
is whether a plugin *he* uploads reaches them too, not just Anthropic's (§5
step 3).

**Short answer: a likely replacement, pending one test.** A plugin enabled on
the claude.ai account reaches chat on the web, the Desktop Chat tab, Cowork and
every Claude Code session, and unlike the ZIP uploads it can be deleted. Claude
in Chrome and the iOS app, the two surfaces the ZIP uploads exist for
([ADR 0002](../decisions/0002-limit-account-store-to-repo-independent-skills.md)),
also offer Anthropic's plugins. If a personal test plugin reaches them too, the
plugin channel covers everything the ZIP uploads do. `sync-skills` stays until
that test is done.

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
| **Claude in Chrome** | yes | **yes** for Anthropic's plugins (measured 2026-09-23); a personal plugin is untested |
| **Mobile app (iOS)** | yes | **yes** for Anthropic's plugins (measured 2026-09-23); a personal plugin is untested |
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

## 4. What is still unknown

1. **Does a personal plugin reach Chrome and iOS too?** This is the deciding
   question now (§5 step 3).
2. Does a personal marketplace accept a public repo, and does it follow `main`?
3. Does `syncClaudeAiPlugins: false` move plugins to `.trash`? This is documented
   but not yet witnessed.
4. What the CLI's `My Uploads` listing and the four `backingPluginId`s are.
   Neither is visible in the UI.

## 5. How to finish it

**Rule for every check** (from [E4](E4-federated-bundle-delivery.md)): a
"not found" counts only if, on the same surface in the same sitting, a known
control *is* found. Otherwise the result is void.

1. **Claude in Chrome** — **done 2026-09-23: found** (§3.4).
2. **Mobile app** — **done 2026-09-23 on iOS: found** (§3.4).
3. **Next:** upload a throwaway test plugin whose one skill replies with a
   unique token, and repeat the check on all five surfaces. The six Anthropic plugins arrive through a default marketplace, so
   they don't prove a *personal* plugin would. Then turn the test plugin off and
   confirm it disappears everywhere.
4. **Only if step 3 passes:** add `Adam-S-Daniel/agentskills` as a personal
   marketplace. Record whether a public repo is accepted, then how long a pushed
   change takes to arrive.

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
   drift loop unchanged** until §5 step 3 is done.
2. **Run the upload test next (§5 step 3).** Chrome and iOS both passed the
   free check, so this one test now decides whether the plugin channel can
   replace the uploader. Upload one throwaway plugin, check all five surfaces,
   then delete it. Deletion is documented, so nothing is left behind.
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
5. **Ignore `My Uploads` and `backingPluginId` for now.** Neither is visible in
   the UI and nothing depends on them. Re-read them if the UI changes.

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
