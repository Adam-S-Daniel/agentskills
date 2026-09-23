# E6 — Can plugins on the claude.ai account replace the skill ZIP uploads?

**Status: open — one test left, for local Cowork** (§4).

**Short answer: almost, but not for local Cowork yet.** On 2026-09-23 Adam
uploaded a throwaway personal plugin, `e6-probe`. Its skill returned its unique
token in the iOS app, the Claude in Chrome side panel, claude.ai chat,
claude.ai Cowork and the Desktop Chat tab (§3.5). Those include both surfaces
the ZIP uploads exist for
([ADR 0002](../decisions/0002-limit-account-store-to-repo-independent-skills.md)).
Deleting it on claude.ai removed it (checked on iOS). The account also accepted
this repo as a personal marketplace.

The gap is **Desktop Cowork, the local Cowork tab**. It does not get a
personally uploaded plugin. It does get Anthropic's marketplace plugins and
uploaded skills (§3.5). Local Cowork is where the PDF skills would actually be
used, since they work on local files. So `sync-skills` stays until a plugin
delivered through a *marketplace synced from a repo* has been tested there
(§5 step 4). That route is what Anthropic's plugins use, and it is the one that
already reaches local Cowork.

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
| **Desktop Cowork (local)** | yes (`rename-pdfs`, measured 2026-09-23) | **no** for a personal upload; **yes** for Anthropic's marketplace plugins (measured 2026-09-23); a repo-synced marketplace is untested |
| Can be deleted | only by hand in the UI; the uploader has no delete | yes, at four layers (§2.3) |
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
| Claude Desktop, Cowork tab (local) | **no**. This counts: in the same sitting both controls, `design:design-critique` and `rename-pdfs`, were offered there |
| Claude Code terminal (laptop) | not in the regular sync: at 18:16 UTC `claude plugin list` still showed only the six Anthropic plugins |

**Where personal uploads live.** Since 2026-09-21 the command line has listed a
claude.ai-hosted `My Uploads` marketplace (`claudeai-my-uploads`, "not added"),
which the web UI does not show (§3). This test explains it: personal uploads
sit in that marketplace, and the regular sync does not bring them to Claude
Code. A machine gets them by adding it with
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
That is also how Claude Code behaves (above). The support article says the
Cowork tab "sources its skills, plugins, and connectors from this Customize
configuration", but it doesn't say which marketplaces.

## 4. What is still unknown

1. **Does a plugin from a repo-synced personal marketplace reach local
   Cowork?** This now decides the switch (§5 step 4). If yes, one channel
   covers every surface. If no, local Cowork keeps needing uploaded skills.
2. Does a repo-synced marketplace follow `main`, and how fast does a push
   arrive?
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
4. **Next — a repo-synced probe in local Cowork.** Put a probe plugin in a
   *separate, throwaway* public repo that is a one-plugin marketplace, not in
   this repo: adding this repo would bring all three bundles onto the account
   (§3.2). Add that repo at
   [claude.ai/customize/plugins](https://claude.ai/customize/plugins) → "+" →
   Add marketplace → Add from a repository, and enable the probe if it isn't
   enabled already. Check its token in local Cowork, with `rename-pdfs` as the
   control, and on one other surface. Then push a change to the token and time
   how long it takes to arrive. Remove the marketplace and delete the repo
   afterwards.

| Result | Meaning | Action |
|---|---|---|
| Step 4 result | Meaning | Action |
|---|---|---|
| Repo-synced probe found in local Cowork (control found) | One channel covers every surface, with delete and no uploader | **Retire the uploader** in favour of a dedicated personal plugin synced from the repo. New ADR amending ADR 0002's "one-way door" consequence. |
| Not found in local Cowork (control found) | Local Cowork only gets uploaded skills | **Keep `sync-skills`** for the skills used in local Cowork (the PDF trio). Everything else can move to the plugin channel, or everything can stay on skills; decide on how many skills each side would hold. |
| Neither the probe nor the control found | The check failed | Record nothing; re-run. |

## 6. Recommendations

1. **Keep `sync-skills`, `--verify` and the
   [ADR 0006](../decisions/0006-drive-the-account-store-drift-loop-from-one-published-artifact.md)
   drift loop unchanged** until §5 step 4 is done. Local Cowork gets personal
   content only as uploaded skills today, and that is where the PDF skills run.
2. **Run §5 step 4 next.** If a repo-synced plugin reaches local Cowork, plan
   the switch to a dedicated personal plugin holding exactly the skills the
   account carries today (§3.2), synced from the repo and deletable. Creating
   it moves skills between bundles, which touches the append-only `renames`
   map, so it needs the adversarial review round in AGENTS.md before merge.
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
