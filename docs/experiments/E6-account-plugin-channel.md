# E6 — Can a plugin enabled on the claude.ai account replace the ZIP uploads?

**Verdict, from documentation: yes for three of the five surfaces the ZIP channel
serves, and it is the only channel that can DELETE.** Anthropic's support article
says a plugin is usable "in chat on the web, the Chat tab in Claude Desktop, and
Claude Cowork", and that "the skills bundled in a plugin work across all three"
([Use plugins in Claude](https://support.claude.com/en/articles/13837440-use-plugins-in-claude)).
Claude Code adds terminal and cloud sessions on top
([synced plugins](https://code.claude.com/docs/en/plugins-reference#synced-plugins)).
**Claude in Chrome and the mobile apps are named by no page we could find**, and
they are two of the five surfaces [ADR 0002](../decisions/0002-limit-account-store-to-repo-independent-skills.md)
exists to serve. That gap is the whole remaining question, and it needs the UI
step in §3.

- Surface: Claude Code on the web, cloud session `session_015qdErtu4Sm8Bf3MJjdtvgs`, CLI `2.1.276`, 2026-09-18
- **Addendum, 2026-09-20 (§2.8)** — re-measured from a laptop terminal on CLI `2.1.278`. Six plugins are now syncing from the account, so §2.1's empty-bucket baseline is superseded. Read §2.8 before acting on §3.
- Account `d11d9c2e-1772-4767-9197-f59d6fe0ab5a`, bucket `29094e6a-…_d11d9c2e-…`
- Nothing was enabled, installed, uploaded or added on the account to produce this document. Everything in §2 is either a container-local probe (§2.6) or a read of state that was already there.
- [#158](https://github.com/Adam-S-Daniel/agentskills/issues/158) waits on this answer for its `syncClaudeAiPlugins` line; §5 is written to be the input to that decision.

---

## 1. What the documentation says

### 1.1 An individual account can have plugins — the plan floor is Pro, not Team

The question in [#160](https://github.com/Adam-S-Daniel/agentskills/issues/160)
assumed this might be a Team/Enterprise-only channel, because the page the issue
cites is about organizations. It is not.

| Claim | Source | Quote |
|---|---|---|
| Plans | [Use plugins in Claude](https://support.claude.com/en/articles/13837440-use-plugins-in-claude) | "Plugins are available to all paid plans (Pro, Max, Team, Enterprise)." |
| Personal upload exists | same | "You can also upload a custom plugin file if you built one yourself." |
| Personal marketplace exists | same | "In the **Personal plugins** section, click the '+' button, then select 'Add marketplace.' … **Add from a repository:** Sync a marketplace from a GitHub repository or git URL." |
| Org library is the *other* thing | [Manage plugins for your organization](https://support.claude.com/en/articles/13837433) | "Plugin marketplaces let Team and Enterprise plan owners distribute curated plugins to everyone in their organization." |

Claude Code's own docs corroborate the personal half twice, in passing rather
than in a section of its own: `claude plugin marketplace list` prints marketplaces
claude.ai offers "such as your organization's plugin library **and your own
claude.ai uploads**"
([Add from claude.ai](https://code.claude.com/docs/en/discover-plugins#add-from-claude-ai)),
and a managed allowlist entry matching `claude.ai` "doesn't admit a member's
**personal claude.ai uploads**"
([How restrictions work](https://code.claude.com/docs/en/plugin-marketplaces#how-restrictions-work)).
An admin allowlist that has to say "not the personal ones" is evidence the
personal ones exist as a distinct class.

So there are **two** ways to get a bundle onto this account, and they are not the
same door:

- **Upload a plugin file** — the direct analogue of today's ZIP upload, one
  artifact, manual.
- **Add a personal marketplace from a GitHub repository or git URL** — which is
  what makes this interesting, because `Adam-S-Daniel/agentskills` already *is* a
  marketplace. No new packaging, no uploader, no browser POST.

### 1.2 Which surfaces a plugin reaches

| Surface | Reached? | Evidence |
|---|---|---|
| chat, web | **yes** | "You can install and use plugins in chat on the web…" ([Use plugins in Claude](https://support.claude.com/en/articles/13837440-use-plugins-in-claude)) |
| Claude Desktop, Chat tab | **yes** | same sentence |
| Cowork | **yes** | same sentence; and "The [Cowork] tab in the Desktop app sources its skills, plugins, and connectors from this Customize configuration, which syncs through your claude.ai account" ([Extend Claude Code](https://code.claude.com/docs/en/desktop#extend-claude-code)) |
| Claude Code — cloud sessions | **yes** | "In Cowork and cloud sessions, Claude Code downloads them into the session's own environment when the session starts" ([synced plugins](https://code.claude.com/docs/en/plugins-reference#synced-plugins)) |
| Claude Code — terminal | **yes, v2.1.273+** | same page; changelog 2.1.275, "Added syncing of the skills and plugins enabled on your claude.ai account to terminal sessions signed in with it" |
| **Claude in Chrome** | **not stated anywhere** | the word "Chrome" does not appear in either support article; it does not appear in the synced-plugins section |
| **Mobile apps** | **not stated anywhere** | ditto for "mobile"; the surface list is "chat on the web, the Chat tab in Claude Desktop, and Claude Cowork" and stops there |

Two caveats that keep this from being read as a clean five-for-five:

- **Not everything in a plugin crosses.** "Hooks and sub-agents run only in
  Cowork, so they appear grayed out in chat." Only the *skills* are promised on
  all three. That happens to be harmless here — all three bundles ship skills and
  nothing else (§2.5) — but it means "the plugin loads" and "the plugin works" are
  different claims on the chat surface.
- **Absence of a sentence is not absence of a feature.** The support article is
  written for the knowledge-work plugin catalog, not as a compatibility matrix.
  Chrome and mobile being unnamed is the reason for §3, not a finding.

### 1.3 Removal — the thing the ZIP channel cannot do

[ADR 0002](../decisions/0002-limit-account-store-to-repo-independent-skills.md)
calls the account store "close to a one-way door" because "the API `sync-skills`
drives uploads only; deletions are a manual action in the claude.ai UI", so
dropping a skill from git only unversions the live copy. The plugin channel
documents a removal path at every layer:

| Layer | What removes it | Source |
|---|---|---|
| The marketplace | "To remove a marketplace, including the default Knowledge Work marketplace: … select 'Remove.'" | [Use plugins in Claude](https://support.claude.com/en/articles/13837440-use-plugins-in-claude) |
| The plugin, for everyone shared with | "Deleting a plugin removes it for everyone you shared it with" | same |
| Every Claude Code session | "To remove one, turn the plugin off for your claude.ai account, and Claude Code removes it at the next sync." | [synced plugins](https://code.claude.com/docs/en/plugins-reference#synced-plugins) |
| One machine | `syncClaudeAiPlugins: false` → "it moves the plugins it already synced to `~/.claude/plugins/.trash/` and no longer loads them" | [`syncClaudeAiPlugins`](https://code.claude.com/docs/en/settings-reference#syncclaudeaiplugins) |

**`.trash` is a machine-local undo, not an account-level delete, and the two must
not be confused.** `.trash` is where a *machine* puts content it has stopped
loading — on an opt-out, or (for the skills half) after a sign-out or an org
turning Skills off (changelog 2.1.271, 2.1.273). The files stay recoverable "until
the [retention sweep](https://code.claude.com/docs/en/claude-directory#cleaned-up-automatically)
deletes them". Nothing about `.trash` touches the account. The account-level
delete is the UI action in row 2, and it is the one that answers ADR 0002.

The asymmetry with a source-synced marketplace is worth stating plainly, because
it is the strongest single argument in this document: **with a repo-synced
personal marketplace, `git rm` becomes a delete.** Today, removing a skill from
this registry leaves an unversioned orphan on the account — the exact condition
PR #62 had to clean up. If the account's copy is a marketplace sync of this repo,
removing the directory removes it from the sync's next read, and Claude Code
"removes [it] at the next sync" everywhere it landed.

### 1.4 `required by your org` — inapplicable to a personal account, and that is the point

"You can't turn off a plugin that your organization marks as required on
claude.ai. Claude Code loads it even if you disabled it earlier, and
`claude plugin disable` refuses with `Plugin "<name>@synced" is required by your
organization and can't be disabled here.`" In `claude plugin list` these are
marked `required by your org`
([synced plugins](https://code.claude.com/docs/en/plugins-reference#synced-plugins)).

Marking a plugin required is an Owner action in **Organization settings >
Plugins**, which the support article scopes to "Owners and Primary Owners of Team
and Enterprise plans". A personal Pro/Max account has no such settings page, so
nothing on this account can be marked required and no `@synced` plugin here can
become undisableable. That is a safety property of adopting the channel on *this*
account specifically, and it is exactly the property that would stop holding if
the account ever joined a Team plan. Written down so the next person does not
have to re-derive why the escape hatch exists.

The escape hatches that *do* apply, in increasing blast radius, and the reason
this experiment is cheap to run:

1. `claude plugin disable <name>@synced` — writes `"<name>@synced": false` into user-level `enabledPlugins`.
2. `"<name>@synced": false` in a project's committed `.claude/settings.json` — "to keep it out of one project in every environment".
3. `syncClaudeAiPlugins: false` in user, local or managed settings — "A repository can't turn it off for you", and "Claude Code honors only `false`: `true` is the same as unset".
4. Turn the plugin off on claude.ai — removed everywhere at the next sync.

### 1.5 Pinning: it follows a branch, and nothing here can pin it

The measured answer is that there is no documented pin, and two independent
mechanisms both resolve to "latest on a branch":

- **The marketplace sync side.** For the GitLab path the docs are explicit:
  "Organization sync reads the project's **default branch**. If you turn on
  **Sync automatically**, only pushes to the default branch start a sync"
  ([Sync a GitLab-hosted marketplace](https://code.claude.com/docs/en/plugin-marketplaces#sync-a-gitlab-hosted-marketplace)).
  No page states the equivalent for a *personal* marketplace added from a GitHub
  repository. Treat "personal sync follows the default branch too" as **inferred**
  by analogy, and put it in the UI protocol (§3, step 6).
- **The version-resolution side.** "For git-based sources, if you omit `version`,
  Claude Code uses the source's resolved commit SHA, so users get an update
  whenever that commit changes"; and "Setting `version` pins the plugin for every
  source type" ([Version resolution and release channels](https://code.claude.com/docs/en/plugin-marketplaces#version-resolution-and-release-channels)).
  This repo's marketplace entries carry no `version` key, and each bundle's
  `plugin.json` does (`adam` 1.1.0, `adam-local` 1.2.0, `fastmail` 1.1.0), which
  is why [ADR 0009](../decisions/0009-bump-bundle-versions-on-every-release.md)
  exists at all: a bundle whose `version` string does not move does not propagate.

Neither mechanism is `skills.lock`. **The account plugin channel is exactly as
unpinned as the ZIP channel it would replace** — the difference is that its
unpinned state tracks `main` automatically rather than drifting to whenever a
human last ran the uploader, which is the failure E5 measured (`sync-skills`'
account copy 127 lines and six `--report-issue` occurrences behind the registry).
Auto-tracking `main` is better than silent drift, but it is not a pin, and
[E4 F3](E4-federated-bundle-delivery.md) is the standing warning against writing
one that does nothing: `ref`, `commit`, `version` and `branch` all *validate* on a
marketplace source, and validation passing says only that a key is tolerated.

### 1.6 Two loading rules that matter more than they look

**Collisions are exclusive for plugins and additive for skills.** For plugins:
"When an enabled plugin from any other source matches a synced plugin's name,
Claude Code loads that plugin and reports the synced copy as **not loaded**"
([synced plugins](https://code.claude.com/docs/en/plugins-reference#synced-plugins)).
For account *skills* the opposite: the synced copy stays loaded and merely loses
the short name — "`/<name>` runs the other command, and the synced skill runs only
as `/anthropic-skills:<name>`"
([When a synced skill name matches another command](https://code.claude.com/docs/en/skills#when-a-synced-skill-name-matches-another-command)),
which is the doubling [#158](https://github.com/Adam-S-Daniel/agentskills/issues/158)
is about.

This inverts the context argument. A bundle enabled on the account costs **zero
extra always-on tokens** in any repo that already installs the same bundle from
the marketplace, because the synced copy is dropped rather than shadowed. The
same bundle delivered as account *skills* is paid twice. §2.5 puts numbers on
both halves.

**A `defaultEnabled: false` marketplace entry installs disabled** — measured in
§2.6. `adam-local` and `fastmail` carry that flag, so a personal marketplace
synced from this repo would not silently switch them on.

---

## 2. Measured here, without the UI

Everything in this section was run in the session named at the top. Nothing is
inferred. Commands are given verbatim so a later session can re-run them.

### 2.1 The channel exists on this account and is empty

```console
$ claude --version
2.1.276 (Claude Code)

$ claude plugin list
No plugins installed. Use `claude plugin install` to install a plugin.

$ claude plugin list --json
[]

$ claude plugin marketplace list
No marketplaces configured
```

`~/.claude/plugins/` holds exactly one entry, `synced/`, and nothing else — no
`cache/`, no `installed_plugins.json`, no `known_marketplaces.json`, no `.trash/`:

```console
$ find ~/.claude/plugins/synced ~/.claude/skills/synced -maxdepth 1
/root/.claude/plugins/synced
/root/.claude/plugins/synced/.bucket-29094e6a-…_d11d9c2e-…
/root/.claude/plugins/synced/29094e6a-…_d11d9c2e-…        ← empty directory
/root/.claude/skills/synced
/root/.claude/skills/synced/.bucket-29094e6a-…_d11d9c2e-…
/root/.claude/skills/synced/29094e6a-…_d11d9c2e-…         ← 21 skills + manifest.json
```

### 2.2 The empty bucket is a real negative, and the skills bucket beside it is the control

This is the [E4](E4-federated-bundle-delivery.md) rule applied to a measurement
made in a single session, and it is why §2.1 is a finding rather than a shrug.
"The plugins bucket is empty" is worth nothing on its own — an empty directory is
equally consistent with *nothing is enabled* and with *the sync never ran*. Three
controls separate them:

1. **The bucket was created.** `.bucket-<org>_<account>` exists beside it, and so
   does the bucket directory itself. Something ran and produced zero, rather than
   nothing running.
2. **The sibling channel, same bucket id, same session, is populated.** The
   account-sync transport demonstrably works here: 21 skills landed under the
   identical `<org>_<account>` key. A session where the account handshake had
   failed would have an empty skills bucket too.
3. **The readback command is not structurally blind.** §2.6 makes
   `claude plugin list --json` print a populated array in this same container.

With all three holding, `[]` means *nothing is enabled on the account*. Measured.

### 2.3 One reading that is NOT evidence, stated so it is not cited later

`claude plugin marketplace list` printed no `From claude.ai:` section — before the
probe, and again after a marketplace existed. It is tempting to read that as
"claude.ai offers this account zero marketplaces". **It is not admissible.** The
docs scope that section to terminal sessions: "In **terminal sessions** where
plugins sync from your claude.ai account, claude.ai can also list marketplaces for
you"
([Add from claude.ai](https://code.claude.com/docs/en/discover-plugins#add-from-claude-ai)).
This is a cloud session. Its silence is the expected output of a code path that
does not run here, and it is indistinguishable from the account having nothing to
offer. The same trap as E2's two false negatives. Re-read it from a laptop
terminal on 2.1.273+; §3 step 6 does.

### 2.4 ADR 0002's structural finding still holds in the bucketed layout

C8 in [E2](E2-sessionstart-skill-bootstrap.md) — the finding
[ADR 0002](../decisions/0002-limit-account-store-to-repo-independent-skills.md)
rests on — was measured before the `<org>_<account>` bucket existed. Re-measured
today against the bucketed manifest, it is unchanged:

```console
$ python3 -c "import json;m=json.load(open('…/manifest.json'));print(len(m['skills']));print(sorted({k for s in m['skills'] for k in s}))"
21
['description', 'name', 'skillId', 'source', 'updatedAt']
```

All 21 records carry those five fields and no others — no sixth field on any
record, nothing scope-shaped. Claude Code still has no channel on which to receive
a scope, so ADR 0002's "account skills cannot be scoped to a repo, and the reason
is structural" stands, and it will apply to account plugins for the same reason
the moment one exists. `syncClaudeAiPlugins` is a *machine* switch, and the
project-scope escape hatch is a per-name opt-**out** in a committed
`.claude/settings.json`, not a scope.

Composition, by `source`, for the record:

| `source` | count | names |
|---|---|---|
| `custom` | 10 | `adam-writing-style`, `fastmail`, `finding-unknowns`, `ocr-pdfs`, `pdf-ocr-audit`, `rename-pdfs`, `sync-cc-settings-between-wsl-and-windows`, `sync-skills`, `wj-next-break`, `writing-adrs` |
| `anthropic-example` | 7 | `doc-coauthoring`, `docs`, `import-memory`, `learn`, `skill-creator`, `theme-factory`, `web-artifacts-builder` |
| `anthropic` | 4 | `docx`, `pdf`, `pptx`, `xlsx` |

`CLAUDE_CODE_ACCOUNT_UUID` is `d11d9c2e-1772-4767-9197-f59d6fe0ab5a`, which is the
second half of the bucket name. The first half, `29094e6a-…`, is an organization
id that exists even though this is a personal account — so the bucket layout does
not tell you whether an account has an org, and a script must not infer plan from
it.

### 2.5 No existing bundle matches the account's contents, and the overshoot is the ADR 0002 objection

The obvious implementation — "enable the `adam` bundle" — does not reproduce
today's account. Computed from the live manifest against the three bundle
directories:

| Bundle | Skills | Already on the account | Would ADD to the account |
|---|---|---|---|
| `adam` | 9 | 3 — `adam-writing-style`, `finding-unknowns`, `writing-adrs` | **6** — `debug-github-workflows`, `disarm-inherited-reach`, `github-actions-repo-settings`, `review-bash-ci-reliability`, `skills-doctor`, `workflow-path-audit` |
| `adam-local` | 10 | 6 — `ocr-pdfs`, `pdf-ocr-audit`, `rename-pdfs`, `sync-cc-settings-between-wsl-and-windows`, `sync-skills`, `wj-next-break` | 4 — `compare-pdfpairs`, `launch-wsl-claude-session`, `migrate-claude-memory`, `windows-elevation-from-wsl` |
| `fastmail` | 3 | 1 — `fastmail` | 2 — `add-from-address`, `add-received-from-addresses` |

The union of all three covers the account's `custom` set exactly — 10 of 10 — and
adds 12 skills that are deliberately not there. `adam`'s six are **precisely** the
alternative ADR 0002 records as rejected: "Push the platform / `adam` bundle to the
account store. Rejected: account skills cannot be repo-scoped, so every CI and
workflow skill would load in every unrelated repo as pure context cost."

Measured cost, from `claude plugin details` against a container-local install of
this repo's own marketplace (§2.6):

| Set | Always-on tokens |
|---|---|
| `adam` | ~1,895 |
| `adam-local` | ~2,536 |
| `fastmail` | ~728 |
| all three | ~5,159 |
| the 10 skills the account holds today | ~2,200 |
| **the 12-skill overshoot** | **~2,970** |

Three notes on that table. First, ADR 0002 quoted "~1,479 always-on tokens for 8
skills (~185/skill)" for `adam`; it is now ~1,895 for 9 (~211/skill), so the
per-skill figure has grown ~14% and a design that budgets from the old number is
already wrong. Second, the overshoot lands on **every** surface — chat, Cowork,
mobile — where #54's warning applies that at the default budget the least-used
descriptions are silently dropped, so this is not only a cost but a
discoverability risk to the skills that are supposed to be there. Third, per §1.6
it costs nothing extra in a Claude Code session that installs the same bundle from
the marketplace, because the synced copy is dropped rather than doubled — the
opposite of the skills channel.

The clean fix is a fourth, dedicated personal bundle. **That is not a free move
and this experiment does not take it**: skill directory basenames key `setup.sh`
symlinks and the claude.ai uploads, moving a skill between bundles writes the
marketplace `renames` map, and that map is append-only forever and earns an
adversarial round (AGENTS.md, "One-way doors get an adversarial round").

### 2.6 The container-local probe — the negative control, and where the numbers came from

Run in a throwaway cloud container, never on the account. Two marketplaces were
added with local sources and then removed; nothing was uploaded, enabled or added
on claude.ai, and nothing under `~/.claude/{skills,plugins}/synced/` was written.

**Probe A — prove the readback is not blind.** A two-file throwaway marketplace in
the scratchpad, one plugin, one no-op skill:

```console
$ claude plugin validate /tmp/…/probe/mkt --strict
√ Validation passed
$ claude plugin marketplace add /tmp/…/probe/mkt
√ Successfully added marketplace: e6-local-probe (declared in user settings)
$ claude plugin install e6-probe@e6-local-probe --scope local
√ Successfully installed plugin: e6-probe@e6-local-probe (scope: local)
$ claude plugin list --json
[ { "id": "e6-probe@e6-local-probe", "version": "0.0.1", "scope": "local",
    "enabled": true, "installPath": "/root/.claude/plugins/cache/…", … } ]
```

So `[]` in §2.1 is a real zero. This is the control §2.2 needs, and it is the form
of control E4 §"the two findings that generalise" insists on: before reporting
that a thing is absent, show the harness reporting a thing that is present.

It also fixes the shape of the readback the UI protocol will use. A synced plugin
is documented to appear "under a `Synced from claude.ai` heading" with `synced` as
its source and an id of `<name>@synced`; what §3 will actually diff is this JSON
against `[]`.

**Probe B — where the token numbers came from.** The repo checkout itself, added
as a directory marketplace:

```console
$ claude plugin marketplace add /home/user/agentskills
√ Successfully added marketplace: agentskills (declared in user settings)
$ claude plugin install adam@agentskills --scope local     # and adam-local, fastmail
$ claude plugin list
  > adam-local@agentskills   Version: 1.2.0  Scope: local  Status: × disabled
  > adam@agentskills         Version: 1.1.0  Scope: local  Status: √ enabled
  > fastmail@agentskills     Version: 1.1.0  Scope: local  Status: × disabled
```

**`defaultEnabled: false` is honored on install** — `adam-local` and `fastmail`
landed disabled without being asked to. Worth knowing before enabling a
repo-synced personal marketplace: two of its three local bundles arrive off.

**Teardown, verified.** `claude plugin marketplace remove <name>` for both (which
uninstalls their plugins), then `rm` of the four paths the adds created —
`~/.claude/settings.json` (birth timestamp `06:45:00`, i.e. created by the first
`marketplace add`; it did not exist before and nothing was clobbered),
`~/.claude/plugins/cache`, `installed_plugins.json`, `known_marketplaces.json` and
`marketplaces/`. Final state matches §2.1 exactly, and `git status --porcelain` in
the checkout is empty — a directory marketplace loads in place and wrote nothing
into the repo.

### 2.7 What could not be measured from here

- Whether claude.ai offers this account any marketplace (§2.3 — wrong session type). **Answered 2026-09-20 — see §2.8.**
- The shape of a synced plugin's on-disk record, and whether a `plugins/synced/manifest.json` analogue exists. The bucket is empty, so there is nothing to read. **Answered 2026-09-20 — see §2.8.**
- Whether `syncClaudeAiPlugins: false` really populates `~/.claude/plugins/.trash/`. With an empty bucket there is nothing to move, and setting it would have disturbed the 21 skills this session is running on. **Still open**, and §2.8 narrows why: the directory still does not exist, because nothing has been turned off yet.
- Everything in §1.2's last two rows. No amount of shell in a cloud session can see Claude in Chrome or a phone. **Still open — this is the experiment**, though §2.8 makes it much cheaper to run.

### 2.8 Re-measured two days later on the durable machine — the zero baseline is already gone

> Taken 2026-09-20 from a laptop terminal on `ZENDA` (WSL), CLI **2.1.278**, same
> account, same bucket id. §2.1–§2.7 stand exactly as recorded — they were true on
> 2026-09-18 from a cloud session. This is a second surface two days later, and it
> moves three of them. Kept as an addendum rather than folded into §2.1, because
> "the bucket was empty and two days later it was not" is the more useful fact.

**Six plugins are now enabled on the account and synced into a terminal session.**

| Plugin | `plugin list` version | `manifest.json` version | `marketplaceName` | `installationPreference` |
|---|---|---|---|---|
| `pdf-viewer` | 0.2.0 | 0037 | `knowledge-work-plugins` | `available` |
| `productivity` | 1.3.1 | 0039 | `knowledge-work-plugins` | `available` |
| `design` | 1.2.0 | 0038 | `knowledge-work-plugins` | `available` |
| `finance` | 1.3.0 | 0038 | `knowledge-work-plugins` | `available` |
| `engineering` | 1.2.0 | 0038 | `knowledge-work-plugins` | `available` |
| `data` | 1.1.0 | 0038 | `knowledge-work-plugins` | `available` |

Every one carries `marketplaceName: knowledge-work-plugins` — the Knowledge Work
marketplace that [Use plugins in Claude](https://support.claude.com/en/articles/13837440-use-plugins-in-claude)
says is "added by default". **Nothing this session did produced them**, and that
default-marketplace provenance is consistent with their having arrived on their
own — but whether they were enabled by hand on claude.ai between the two
measurements is not something a shell can see. So the measured claim is *the
channel is live on this account*, not *the channel turned itself on*. Their
skills load namespaced as
`<plugin>:<skill>` — `design:design-critique`, `engineering:code-review`,
`pdf-viewer:view-pdf` — and `adam@agentskills` sits beside them as the one
non-synced install.

**`plugins/synced/manifest.json` exists, and ADR 0002's C8 extends to plugins.**
§2.7 listed its shape as unreadable. It reads now, and it is the plugin analogue
of the five-field skill record §2.4 re-measured. Each per-plugin entry carries
seven fields — `pluginId`, `name`, `description`, `version`, `updatedAt`,
`marketplaceName`, `installationPreference` — and a `<name>.meta.json` sidecar
beside each directory carries three: `server_plugin_id`, `marketplace_name`,
`installation_preference`. **Nothing repo-scope-shaped in either.** So ADR 0002's
structural objection — Claude Code has no channel on which to receive a scope —
holds for the plugin channel too, measured rather than argued by analogy. §1.4 is
confirmed from the CLI side at the same time: the installation-preference
vocabulary does reach the client, and all six read `available`.

**§2.3's open question is answered, and the answer is a separation.**
`claude plugin marketplace list` on this laptop terminal — the right session type,
on 2.1.278, well past the 2.1.273 floor — lists four GitHub marketplaces and **no
`From claude.ai:` section at all**, while six plugins sync from claude.ai in that
same session. Plugin sync and the claude.ai marketplace listing are therefore
independent mechanisms: plugins arrive without any marketplace being offered to
the CLI. §2.3 was right to refuse the cloud reading and right that it needed a
terminal — and the terminal says the two do not travel together. A session that
inferred "no account plugins" from a missing `From claude.ai:` heading would have
been wrong in exactly the way §2.3 warned about, on the other side of the same
coin.

**A plugin that ships MCP servers understates its own cost.** `design@synced`
reports `Always-on: ~617 tok` for 7 skills (~88/skill) and lists 9 MCP servers
with the note `tool schemas resolved at runtime; not counted`. §2.5's arithmetic
is unaffected — all three of this repo's bundles ship skills and nothing else —
but that number cannot be extended to a bundle that ever gains an MCP server, and
a budget built from `plugin details` alone would miss the larger half.

**Two version namespaces, and they disagree by construction.**
`claude plugin list --json` reports `design@synced` at `1.2.0`, the plugin's own
`plugin.json` value; `manifest.json` records `"version": "0038"`, the account
store's serial. Both are correct and they are not comparable. A drift check that
diffs one against the other compares different things and reports drift forever;
[ADR 0009](../decisions/0009-bump-bundle-versions-on-every-release.md)'s bump
discipline governs the first field only.

**`~/.claude/plugins/.trash/` still does not exist**, while
`~/.claude/skills/.trash/` does. Consistent with §1.3 — trash is written on a
removal or opt-out event, and no plugin has been turned off on this account yet —
so §1.3's `.trash` row stays documented-and-not-witnessed.

**What this does to §3, which is the part worth acting on.** The protocol was
written against a zero baseline, so every reading needed an uploaded probe first.
For the one question that actually matters, it no longer does:

- **The control arm is now free, and needs no upload.** Six account plugins are
  already enabled, so on any surface the question *does this surface receive
  account plugins at all?* can be asked directly: is `design:design-critique`,
  `engineering:code-review` or `pdf-viewer:view-pdf` offered there? Nothing to
  add, nothing to tear down, nothing one-way.
- **Chrome and mobile can therefore be answered first, in minutes.** If those
  skills are absent on a surface, that surface does not receive account plugins
  and no personal upload will change it — a strong negative, obtained for free.
- **A present result still needs the probe.** These six are Anthropic's own,
  arriving through a default marketplace. That a surface renders *them* does not
  prove it would render a **personal** upload or a repo-synced personal
  marketplace, which is what this experiment is actually about. A positive on the
  free control promotes the question rather than closing it, and steps 2–5 run as
  written.

That asymmetry is the whole value of the change: the cheap check can only produce
the answer that ends the experiment, never the one that flatters it.

---

## 3. The UI protocol

Runnable by Adam in under 30 minutes. It is safe to run *because* of §1.3: unlike
the ZIP channel, every step here is reversible from the same UI that made it.

**Materials.** A throwaway probe plugin — not one of this repo's bundles, and not
any name in §2.4's table. One skill, `e6-probe`, whose description is distinctive
("Reports the E6 probe token") and whose body says: *reply with the token
`E6-2026-09-18` and nothing else.* E5 §1 is the reason for the token: the three
names that already collide are byte-identical to their registry copies, so they
carry no discriminator and cannot tell you which copy answered. A probe with no
token measures nothing.

### The negative-control rule, non-negotiable

Per [E4](E4-federated-bundle-delivery.md): **a probe that finds nothing proves
nothing until the same probe, on the same surface, in the same sitting, finds
something it should.** Concretely, on each surface run both arms before writing
down a verdict:

- **Positive arm** — the probe plugin's `e6-probe`.
- **Control arm, cheapest first** — a skill from one of the account *plugins*
  already syncing (§2.8): `design:design-critique`, `engineering:code-review`,
  `pdf-viewer:view-pdf`. This controls the **plugin** channel directly, which the
  original control could not, and it needs no upload. Fall back to an account
  *skill* — `rename-pdfs`, on the account since April, `source: custom` — when you
  need to separate "no plugins on this surface" from "no account content here at
  all".

| Positive | Control | Verdict |
|---|---|---|
| found | found | **admissible** — the surface gets account plugins |
| not found | found | **admissible** — the surface does not get account plugins |
| found | not found | re-check which account is signed in; the run is suspect |
| not found | not found | **VOID.** The harness is not working on this surface. Do not record a negative. |

Row 4 is the row that matters. Without it, "Chrome does not get account plugins"
and "I was signed into the wrong account in Chrome" produce the same note, which
is what cost E2 two false negatives.

### Steps

1. **Baseline, before enabling anything.** Open a cloud session and run §2.1's
   four commands. **Do not expect `[]`** — §2.8 measured six account plugins
   syncing on 2026-09-20, so the zero baseline this protocol was first written
   against is gone. Record what the bucket holds, confirm it against §2.8's
   table, and treat that set as the free control arm below.

2. **Enable the probe.** claude.ai → **Customize** → **Plugins** → **Personal
   plugins** → **+** → upload the probe plugin file. Do not add the repo as a
   marketplace yet; step 6 does that, and mixing them makes step 5 ambiguous.

3. **Run both arms on each surface.** Type `/`, look for `e6-probe` in the list,
   then invoke it and check the token comes back. A skill that appears in the menu
   but does not fire is a different (and interesting) outcome from one that is
   absent — record which.

   | Surface | How to read it back |
   |---|---|
   | chat, web | `/` menu; invoke; token |
   | Claude Desktop, Chat tab | same |
   | Cowork | **Cowork** tab → **Customize** first (per the support article), then `/` menu; invoke; token |
   | **Claude in Chrome** | `/` menu if present; otherwise ask in words for the probe skill and check the token |
   | **mobile app** | same as Chrome |

   The last two rows are the reason this document exists. Everything above them is
   already answered by §1.2.

4. **Read it back from Claude Code.** A fresh cloud session, then a laptop terminal
   on ≥2.1.273:

   ```bash
   claude plugin list --json          # expect an entry id "e6-probe@synced", source synced
   claude plugin list                 # expect a "Synced from claude.ai" heading
   find ~/.claude/plugins/synced -maxdepth 3
   ```
   plus `/skills` and `/context` in-session, and record whether the probe is
   grouped separately (the skills channel groups under `claude.ai sync`) and what
   `/context` attributes to it. If `claude plugin list --json` is still `[]` while
   the UI shows the plugin enabled, that is the single most important negative in
   the whole experiment — record the CLI version, whether `/login` has been re-run
   since 2.1.273 ("a sign-in from an earlier version of Claude Code picks up plugin
   access the next time Claude Code renews that sign-in… or right away if you run
   `/login` again"), and stop.

5. **Test removal — the ADR 0002 question.** Turn the probe off on claude.ai.
   Re-run step 3 on one UI surface and step 4 in a *new* cloud session. Expect the
   plugin gone from the bucket. Then set `syncClaudeAiPlugins: false` on the
   laptop, restart, and check `~/.claude/plugins/.trash/` — that distinguishes the
   machine-local undo from the account delete (§1.3), and it is the one claim in
   §1.3 nothing here has witnessed.

6. **Test the repo-synced marketplace, and the pin question.** **Customize** →
   **Plugins** → **+** → **Add marketplace** → **Add from a repository** →
   `Adam-S-Daniel/agentskills`. Then, in a terminal on ≥2.1.273:
   `claude plugin marketplace list` — expect a `From claude.ai:` section (§2.3 is
   why this must be a terminal, not a cloud session). Record: whether a **public**
   repo is accepted at all; whether the three local bundles arrive disabled per
   §2.6; whether `adam-local`'s and `fastmail`'s `defaultEnabled: false` survives
   the round trip. Then push a trivial commit to `main` that changes a skill
   description **and** bumps that bundle's `plugin.json` version, and time how long
   until a new session sees it. That is the branch-vs-pin answer §1.5 could not
   get from documentation.

### Decision table

| Outcome | What it means | Action |
|---|---|---|
| **A.** Probe found on chat + Cowork + Desktop **and** Chrome **and** mobile | The channel is a strict superset of the ZIP channel, with a delete | **Retire the uploader.** `sync-skills`' upload path and `--verify` go; ADR 0006's drift loop collapses to "does the sync run". Write ADR 0010 amending ADR 0002's "close to a one-way door" consequence — the door opens. Keep ADR 0002's *contents* rule; §2.4 shows scope is still structurally impossible. |
| **B.** Found on chat + Cowork + Desktop, **not** on Chrome or mobile | Covers 3 of 5 surfaces | **Adopt as the primary channel, keep the ZIP for the remainder.** Two channels, and the registry must record which skill is served by which. Worse than today on complexity unless the Chrome/mobile set is small. Do not retire `--verify`. |
| **C.** Found only in Claude Code sessions | A fourth Claude Code channel | **Not adopting**, recorded. ADR 0002's objection applies unchanged and the channel adds no surface the marketplace bundle does not already serve, pinned. Close #160 with the measurement. |
| **D.** Step 6 shows the sync tracks `main` with no pin | Expected (§1.5) | Does not change A–C. It changes the ADR: the account channel is auto-tracking and unpinned, so ADR 0009's version-bump discipline becomes load-bearing for a surface with no lock. |
| **E.** Step 6 refuses a public repo | Personal marketplaces inherit the org rule ("must be private or internal") | The repo-synced route is closed; only the per-plugin **upload** remains, which is the ZIP's ergonomics with a delete. Re-score A–C on that basis — the "no uploader" benefit evaporates. |
| **F.** Row 4 of the control table on any surface | Harness fault | **Record nothing for that surface.** Re-run it. |

---

## 4. Consequences, independent of the UI step

- **The one-way door in ADR 0002 is a property of the *transport*, not of the
  account.** The store has no delete because the uploader is a browser POST with
  no DELETE. A channel on the same account, reaching the same surfaces, documents
  removal at four layers (§1.3). That reframes ADR 0002's most-cited consequence
  as a fact about `sync-skills`, which is worth knowing whichever way §3 lands.

- **The collision rule cuts the opposite way from the skills channel**, so
  [#158](https://github.com/Adam-S-Daniel/agentskills/issues/158)'s central
  complaint — the same bundle paid for twice in a converged terminal — does not
  arise for plugins. Whatever #158 decides about `syncClaudeAiSkills`, the
  argument does not carry across to `syncClaudeAiPlugins`, and its "leave
  `syncClaudeAiPlugins` alone until E6" line can be resolved to **leave it unset**:
  with an empty bucket it does nothing today, and if a bundle is ever enabled the
  synced copy is dropped wherever the marketplace copy is installed.

- **The channel is still invisible to CI**, for exactly the reason
  [ADR 0006](../decisions/0006-drive-the-account-store-drift-loop-from-one-published-artifact.md)
  gives: it is a surface constraint, not a credential one. `~/.claude/plugins/synced/`
  does not exist on a GitHub runner. Any drift loop for this channel would be the
  same shape — a session that has the files, publishing an artifact CI can read.

- **`--verify`'s failure mode disappears, but only on the marketplace route.**
  PR #61's lossy single-file fallback was a property of ZIP packaging. A git sync
  has no such fallback. If step 6 lands on outcome E, the upload route keeps a
  packaging step and this benefit does not apply.

---

## 5. Recommendation — **pending the UI step in §3**

**Provisionally outcome B, and do not retire anything yet.**

On the documentation alone, this channel is better than the ZIP uploads on three
axes and worse on none, for the surfaces it is documented to reach: it deletes
(§1.3), it needs no uploader if the repo-synced route works (§1.1), and it costs
nothing extra where a marketplace copy is already installed (§1.6). The two things
that would make it a *replacement* rather than an addition are both unanswered:
whether Claude in Chrome and the mobile apps see it (§1.2), and whether a personal
marketplace accepts a public repo (§3 step 6, outcome E).

Concretely, pending §3:

1. **Do not enable `adam` on the account.** It ships six CI/platform skills to
   chat for ~1,230 always-on tokens (§2.5) and is verbatim the alternative ADR
   0002 rejected. If this channel is adopted it wants a dedicated personal bundle,
   which means the `renames` map, which means an adversarial round.
2. **Leave `syncClaudeAiPlugins` unset** in `setup.sh`'s convergence block — the
   answer #158 is waiting for. Unset is the safe default: the bucket is empty, the
   collision rule is exclusive, and the per-plugin `"<name>@synced": false` opt-out
   exists if one ever needs turning off.
3. **Keep `sync-skills`, `--verify` and ADR 0006's loop running unchanged** until
   §3 returns. None of them can be retired on a documentation reading, and E5 is
   the standing evidence that this channel family rots when nothing checks it.
4. **Cheapest first, and §2.8 has changed the order.** The six already-synced
   account plugins make the Chrome and mobile question answerable with no upload
   at all: check whether `design:design-critique` or `pdf-viewer:view-pdf` is
   offered on those two surfaces before doing anything else. An absent result ends
   the experiment for that surface; a present one still needs the probe, because
   those six are Anthropic's through a default marketplace rather than a personal
   upload. Then §3 step 6 — outcome E kills the interesting version of the
   proposal in one click and costs two minutes.

ADR 0002 does not change today. What changes when §3 returns is one consequence —
"Uploading is close to a one-way door" — and only for a channel that did not exist
when it was written.

---

## 6. Reproducing

Read-only, from any cloud session signed in to the account:

```bash
claude --version
claude plugin list --json
claude plugin marketplace list
find ~/.claude/plugins/synced ~/.claude/skills/synced -maxdepth 2
cat ~/.claude/skills/synced/*/manifest.json
env | grep CLAUDE_CODE_ACCOUNT_UUID
```

The container-local probes in §2.6 are throwaway and must be torn down in the same
session (`claude plugin marketplace remove <name>`, then remove
`~/.claude/settings.json` if the add created it, plus `~/.claude/plugins/cache`,
`installed_plugins.json`, `known_marketplaces.json` and `marketplaces/`). Do not
run them on a durable machine: `marketplace add` writes user-level
`extraKnownMarketplaces`, and on a converged laptop that collides with what
`setup.sh` owns.

## References

- [ADR 0002](../decisions/0002-limit-account-store-to-repo-independent-skills.md), [ADR 0006](../decisions/0006-drive-the-account-store-drift-loop-from-one-published-artifact.md), [ADR 0009](../decisions/0009-bump-bundle-versions-on-every-release.md)
- [E2](E2-sessionstart-skill-bootstrap.md) (C3, C6, C8), [E4](E4-federated-bundle-delivery.md) (F3, the negative-control rule), [E5](E5-account-store-vs-hook-precedence.md) (the drift census this channel would replace)
- [#160](https://github.com/Adam-S-Daniel/agentskills/issues/160) — this experiment. [#158](https://github.com/Adam-S-Daniel/agentskills/issues/158) — the `syncClaudeAiPlugins` decision waiting on §5. [PR #61](https://github.com/Adam-S-Daniel/agentskills/pull/61) — the lossy-upload fix. [PR #62](https://github.com/Adam-S-Daniel/agentskills/pull/62) — the orphans. [#54](https://github.com/Adam-S-Daniel/agentskills/issues/54) — the dropped-description risk.
- Claude Code docs: [synced plugins](https://code.claude.com/docs/en/plugins-reference#synced-plugins), [`syncClaudeAiPlugins`](https://code.claude.com/docs/en/settings-reference#syncclaudeaiplugins), [Add from claude.ai](https://code.claude.com/docs/en/discover-plugins#add-from-claude-ai), [Distribute through organization settings](https://code.claude.com/docs/en/plugin-marketplaces#distribute-through-organization-settings), [Sync a GitLab-hosted marketplace](https://code.claude.com/docs/en/plugin-marketplaces#sync-a-gitlab-hosted-marketplace), [Version resolution and release channels](https://code.claude.com/docs/en/plugin-marketplaces#version-resolution-and-release-channels), [How restrictions work](https://code.claude.com/docs/en/plugin-marketplaces#how-restrictions-work), [Extend Claude Code](https://code.claude.com/docs/en/desktop#extend-claude-code), [Skills synced from claude.ai](https://code.claude.com/docs/en/skills#how-synced-skills-behave), [When a synced skill name matches another command](https://code.claude.com/docs/en/skills#when-a-synced-skill-name-matches-another-command)
- Support articles: [Use plugins in Claude](https://support.claude.com/en/articles/13837440-use-plugins-in-claude) — the surface list and the personal-plugin path, and the page that actually answers §1.1; [Manage plugins for your organization](https://support.claude.com/en/articles/13837433) — the Team/Enterprise org library, which is a different door.
- [CHANGELOG](https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md): 2.1.228 (synced-skill hardening), 2.1.239 (`name@synced`, never overrides a same-named install), 2.1.246 (`enabled_via: admin-install` telemetry), 2.1.261 (managed force-enable vs synced), 2.1.269 (synced MCP servers on resume), 2.1.271 and 2.1.273 (`.trash` on sign-out and on Skills-off), 2.1.273 ("sign-in with a Claude account to also request access to your claude.ai plugins"), 2.1.275 (terminal sync, and the `syncClaudeAiSkills` / `syncClaudeAiPlugins` opt-outs).
