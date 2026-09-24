# E6 — Can plugins on the claude.ai account replace the skill ZIP uploads?

**Status: answered (2026-09-23).** Next step: a switch proposal with its own
ADR (§6).

**Short answer: very likely yes, through a marketplace synced from a repo.**
Two probes, and it matters which one measured what:

- **An uploaded plugin** (§3.5) reached iOS, the Claude in Chrome side panel,
  claude.ai chat and Cowork, and the Desktop Chat tab, and deleting it removed
  it (checked on iOS). It did **not** reach local Cowork, the Desktop app's
  Cowork tab, where the PDF skills are used.
- **A plugin from a repo-synced marketplace** (§3.6) reached local Cowork,
  claude.ai chat, one more surface and a Claude Code terminal. It updates by
  hand: push, press "Check for updates" on claude.ai, restart the Desktop app
  (§5 step 5). Chrome, iOS and deleting were **not** re-measured with it.

Chrome and iOS demonstrably load account plugins (§3.4–§3.5), so the
repo-synced route very likely reaches them too, but that is inferred. The
switch this points to is [ADR 0012](../decisions/0012-serve-the-account-skills-as-one-repo-synced-plugin.md);
its phase 2 measures the remaining surfaces and removal before anything is
retired.

Tracked in [#160](https://github.com/Adam-S-Daniel/agentskills/issues/160).
[#158](https://github.com/Adam-S-Daniel/agentskills/issues/158) waits on the
`syncClaudeAiPlugins` recommendation in §6. What was done on the account to
produce this document was throwaway probes only: an uploaded test plugin,
since deleted, and a separate probe marketplace added, enabled and later
removed (see §3.5–§3.6). None of this repo's plugins was enabled on the
account.

---

## 1. The two channels side by side

| | ZIP uploads (`sync-skills`, today) | Account plugins |
|---|---|---|
| chat (web), Desktop Chat tab, Cowork | yes | yes — documented |
| Claude Code terminal and cloud sessions | yes | yes — documented; measured for a repo-synced plugin in a terminal (§3.6), not for an uploaded one (§3.5) |
| **Claude in Chrome** | yes | **yes**, including a personal upload (measured 2026-09-23) |
| **Mobile app (iOS)** | yes | **yes**, including a personal upload (measured 2026-09-23) |
| **Desktop Cowork (local)** | yes (`rename-pdfs`, measured 2026-09-23) | **no** for an uploaded plugin; **yes** for a plugin from a repo-synced marketplace, once enabled in the Desktop app too (measured 2026-09-23, §3.6) |
| Can be deleted | only by hand in the UI; the uploader has no delete | yes, at four layers — documented (§2.3); measured only for an uploaded plugin (§3.5) |
| Needs an uploader | yes — a browser session, one skill at a time | no — a public repo is accepted as a personal marketplace (measured 2026-09-23) |
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
separate personal plugin. [ADR 0012](../decisions/0012-serve-the-account-skills-as-one-repo-synced-plugin.md)
builds it as a curated marketplace entry that serves the existing skill
directories in place, so no skill moves between bundles and the append-only
`renames` map is untouched.

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
| Claude Desktop, Cowork tab (local) | **no**. This counts: in the same sitting both controls, `design:design-critique` and `rename-pdfs`, were offered there |
| Claude Code terminal (laptop) | not in the regular sync: at 18:16 UTC `claude plugin list` still showed only the six Anthropic plugins. This row is the **uploaded** probe; the repo-synced one did reach the terminal (§3.6) |

**Where personal uploads live.** Since 2026-09-21 the command line has listed a
claude.ai-hosted `My Uploads` marketplace (`claudeai-my-uploads`, "not added"),
which the web UI does not show (§3). This test explains it: personal
*uploads* sit in that marketplace, and the regular sync does not bring them to
Claude Code. A plugin from a repo-synced marketplace is different: it does
sync to a terminal (§3.6). A machine gets them by adding it with
`claude plugin marketplace add --claudeai claudeai-my-uploads` — **inferred**,
not yet run. For Claude Code this matters little, because repos already install
this registry's bundles through the marketplace, pinned (ADR 0010).

**Removal works.** Adam deleted `e6-probe` on claude.ai, and a new chat in the
iOS app no longer offered it (2026-09-23).

**A public repo is accepted as a personal marketplace.** Adam added
`Adam-S-Daniel/agentskills` at
[claude.ai/customize/plugins](https://claude.ai/customize/plugins) and it was
accepted; he then removed it. No plugin from it was tested on any surface, so
this settles only that the route exists.

**Reading the local Cowork result.** Local Cowork offered an Anthropic plugin
(from a marketplace) and an uploaded skill, but not the uploaded plugin. The
likeliest explanation, **inferred**, is that local Cowork loads plugins from
marketplaces and skills from the skill store, while a personally uploaded
plugin lives in the `My Uploads` marketplace, which local Cowork doesn't read.
That is also how a Claude Code terminal treats an *uploaded* plugin (above). The support article says the
Cowork tab "sources its skills, plugins, and connectors from this Customize
configuration", but it doesn't say which marketplaces.

### 3.6 A repo-synced marketplace reaches local Cowork (2026-09-23)

A throwaway public repo,
[`Adam-S-Daniel/e6-probe-marketplace`](https://github.com/Adam-S-Daniel/e6-probe-marketplace),
holds a one-plugin marketplace. Its skill replied with `E6-REPO-8LIV5EH3`.
Adam added the repo at
[claude.ai/customize/plugins](https://claude.ai/customize/plugins) and added
`e6-probe` from it there. Every check succeeded, with one wrinkle:

- **The marketplace syncs to the Desktop app; enabling a plugin does not.**
  After `e6-probe` was added on claude.ai, it was still missing from local
  Cowork. In the Desktop app's own plugin settings, `e6-probe-marketplace`
  was already listed, and `e6-probe` had to be added and enabled there. After
  that the token came back in local Cowork. So which plugins are enabled is
  tracked separately on claude.ai and in the Desktop app, but the marketplace
  list is shared.
- The control (`rename-pdfs`) and a second surface also passed in the same
  sitting.
- **It reached the laptop's Claude Code terminal too.** Unlike the uploaded
  probe (§3.5), the repo-synced `e6-probe` was downloaded by the terminal's
  plugin sync: `~/.claude/plugins/synced/<bucket>/manifest.json` lists
  `e6-probe` with `marketplaceName: e6-probe-marketplace`, and its files were
  written at 2026-09-23 19:35:56 UTC. That manifest carries claude.ai's own
  revision counter (`"0001"`, `"0041"`), not the entry's semver. It still
  listed `e6-probe` after Adam removed the marketplace on claude.ai, so
  removal of a repo-synced plugin from a terminal is **not** measured.

Only local Cowork, claude.ai chat, one more surface and the terminal were
checked for the repo-synced probe. iOS, Chrome, claude.ai Cowork and the
Desktop Chat tab were measured with the uploaded probe (§3.5) or Anthropic's
plugins (§3.4), not with this one.

This is why the uploaded plugin in §3.5 never reached local Cowork: it lives
in the `My Uploads` marketplace, which the Desktop app doesn't list, so there
was nothing to enable there.

**It changes §3.2's worry about adding this repo.** Adding a marketplace
makes its plugins *available*, not enabled, and each app enables them
separately. So this repo could be the personal marketplace. Only a dedicated
personal plugin would be enabled; the three existing bundles would sit there
unused.

## 4. What is still unknown

1. Would a push arrive without a `version` bump? The test bumped it, and
   version pinning is documented for Claude Code (§2.4).
2. Would the Claude GitHub App, if it covered the repo, make claude.ai's sync
   automatic?
3. Does `syncClaudeAiPlugins: false` move plugins to `.trash`? This is
   documented but not yet witnessed; it isn't needed for the decision.
4. What the four `backingPluginId`s on uploaded skills are. Nothing depends on
   them.

## 5. How to finish it

**Rule for every check** (from [E4](E4-federated-bundle-delivery.md)): a
"not found" counts only if, on the same surface in the same sitting, a known
control *is* found. Otherwise the result is void.

1. **Claude in Chrome** — **done 2026-09-23: found** (§3.4).
2. **Mobile app** — **done 2026-09-23 on iOS: found** (§3.4).
3. **Upload test** — **done 2026-09-23**: token on five surfaces, not in local
   Cowork (control found there), deletion confirmed (§3.5).
4. **Repo-synced probe in local Cowork** — **done 2026-09-23: found**, after
   enabling it in the Desktop app too (§3.6). It used a separate throwaway
   repo, so that this repo's three bundles never reached the account.
5. **Update speed** — **done 2026-09-23**: updates only by hand (readings
   below). At 2026-09-23 19:41:55 UTC the probe's
   token was changed to `E6-PUSH-9P3RFBY2` and its version bumped from 0.0.1
   to 0.0.2 (commits `2e8b50f`, `404f7dd`). Record when each surface first
   returns the new token, and whether local Cowork kept `e6-probe` enabled.
   Then remove the marketplace on claude.ai and in the Desktop app, and
   delete the repo.

   **Readings so far** (Adam; "Sync marketplace" on, which is the default):

   | Time (UTC) | Since push | Surface | Token |
   |---|---|---|---|
   | 19:42 | 1 min | local Cowork | old (`E6-REPO-8LIV5EH3`) |
   | 19:46 | 4 min | claude.ai chat | old |
   | 23:09 | 3 h 27 min | claude.ai chat | old |
   | 23:10 | 3 h 28 min | local Cowork | old |

   | 23:49 | 4 h 7 min | claude.ai chat | **new** (`E6-PUSH-9P3RFBY2`), after "Check for updates" on claude.ai |
   | 23:51 | 4 h 9 min | local Cowork | old; the Desktop app's "Check for updates" failed ("Couldn't check for updates. Try again.") |
   | 23:58 | 4 h 16 min | local Cowork | **new**, after fully quitting the Desktop app (tray included) and reopening it; `e6-probe` stayed enabled |

   The push is on the repo's default branch (checked through the GitHub API),
   and the repo has no webhooks. **Automatic sync did not deliver a push within
   3½ hours.** On claude.ai the **"Check for updates"** button on the
   marketplace works: it reported "Updated to 404f7dd", the exact commit
   pushed, and the next chat returned the new token. So claude.ai's copy
   follows a commit and needs a manual check to move. The Desktop app keeps
   its own copy, and its check failed. **A full restart of the Desktop app
   picked up the update**, and the plugin stayed enabled.

   **The update routine, as measured:** push to `main` → press "Check for
   updates" on the marketplace at claude.ai → fully restart the Desktop app.
   Nothing needs re-enabling.

   A Claude/Anthropic GitHub App is installed on the account (Adam,
   2026-09-23). Whether it covers this repo wasn't checked, and it didn't make
   the sync automatic.

| Step 4 result | Meaning | Action |
|---|---|---|
| Repo-synced probe found in local Cowork (control found) | One channel covers every surface, with delete and no uploader | **Retire the uploader** in favour of a dedicated personal plugin synced from the repo. New ADR amending ADR 0002's "one-way door" consequence. |
| Not found in local Cowork (control found) | Local Cowork only gets uploaded skills | **Keep `sync-skills`** for the skills used in local Cowork (the PDF trio). Everything else can move to the plugin channel, or everything can stay on skills; decide on how many skills each side would hold. |
| Neither the probe nor the control found | The check failed | Record nothing; re-run. |

## 6. Recommendations

1. **Keep `sync-skills`, `--verify` and the
   [ADR 0006](../decisions/0006-drive-the-account-store-drift-loop-from-one-published-artifact.md)
   drift loop unchanged** until the switch below has landed and been checked
   on every surface.
2. **Switch to a dedicated personal plugin in a repo-synced marketplace.** It
   would hold exactly the skills the account carries today (§3.2), sync from
   the repo, and be deletable. Enable it once on claude.ai and once in the
   Desktop app (§3.6). Every release then means: bump its `version` (ADR
   0009), merge, press "Check for updates" on claude.ai, and restart the
   Desktop app. The `sync-skills` drift check could be replaced by comparing
   the commit claude.ai reports against `main`. This repo can be the marketplace, because
   adding a marketplace enables nothing by itself. [ADR 0012](../decisions/0012-serve-the-account-skills-as-one-repo-synced-plugin.md)
   is that switch: a curated marketplace entry serving the existing skill
   directories in place, so no skill moves and the `renames` map is untouched.
   It goes through the adversarial review round in AGENTS.md before merge, and
   amends ADR 0002's "one-way door" consequence only after its phase 2 has
   measured removal.
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
