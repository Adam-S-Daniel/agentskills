# agentskills

Adam Daniel's reusable agent skills, packaged as **Claude Code plugins** and as
cross-agent skills that follow the
[Agent Skills specification](https://agentskills.io/specification).

Skills are grouped into three **bundle plugins** under `plugins/<bundle>/skills/<skill>/`,
and the repo root is a Claude Code **plugin marketplace**
(`.claude-plugin/marketplace.json`). The marketplace also publishes one
**federated bundle** — `cms-platform`, whose plugin root is that repo itself, so
it keeps its skills, its cadence and its review path there while this stays the
one marketplace to add. The exact same `SKILL.md` files are consumed
unchanged by Codex, Cursor, VS Code, and any other agent that reads the Agent Skills
format — so a skill is authored once and installs everywhere.

This repo is the **canonical upstream registry** for reusable skills. For where
skills live across repos, the public/private rule, and how a skill graduates into
this registry, see [`STRATEGY.md`](STRATEGY.md).

## Install — Claude Code

Add the marketplace once, then install whichever bundles you want:

```bash
/plugin marketplace add Adam-S-Daniel/agentskills
/plugin install adam@agentskills
# opt-in bundles:
/plugin install adam-local@agentskills
/plugin install fastmail@agentskills
/plugin install cms-platform@agentskills   # federated — fetched from Adam-S-Daniel/cms-platform
# …or browse and pick interactively:
/plugin
```

Skills are namespaced by bundle — invoke them as `/<bundle>:<skill>`, e.g.
`/adam:finding-unknowns`. Update later with `/plugin marketplace update agentskills`;
that refreshes the catalog, and the three local bundles' contents with it. The
federated bundle's contents come from the other repo instead — this marketplace
carries its address, not its skills.

### Bundles

Membership follows where a skill can run: skills usable in a **headless cloud
session of an arbitrary repo** go in `adam` (installed by default);
**machine-bound / local-resource** skills (WSL/Windows homes, local files, a
signed-in browser) go in `adam-local` (opt-in); the **Fastmail domain** is
`fastmail` (opt-in).

`cms-platform` is the exception to that layout: it is **federated**, not
vendored — its entry names `Adam-S-Daniel/cms-platform` as the plugin root, so
its skills are never copied into this repo and never drift from it. It is
opt-in because it is platform-scoped (useful in cms-platform's own consumer
sites, noise everywhere else) and every enabled skill costs always-on context.

**Publishing and delivering are independent axes, on purpose.** The marketplace
says a bundle *exists* and *where it lives*; a consumer's
[`skills.lock`](skills.lock) says which bundles *that repo* installs and pins.
So `cms-platform` is published here and delivered by nothing in this repo's own
lock — which carries only the cloud-safe `adam` bundle — and that is the
intended state rather than an omission: the platform bundle is opt-in per
consumer, and which bundles another repo installs is not this repo's business.
Nothing cross-checks the two files against each other, and nothing should; a
gate coupling them would be enforcing a rule that isn't true. Written down here
because a deliberate gap nobody recorded is indistinguishable from an oversight.

**A federated `source` carries exactly `source` and `repo`** — never a
`ref`, `commit`, `version` or `path` key — and `scripts/check_consistency.py`
fails the build on one rather than leaving a reader to spot it. Checked against
the CLI's own plugin-source schema rather than assumed: a `github` plugin source
declares `repo` plus optional `ref` and `sha`, and nothing else. `path` belongs
to the separate *marketplace* source schema and `version` to the `npm`/`pip`
variants, so on a github source those two — and `commit`, and `branch` — are
undeclared and silently discarded. `ref`/`sha` may well be honoured, but this
repo refuses them too, as policy: pinning a federated bundle to a revision is
`skills.lock`'s job, where the pin is an immutable commit plus a sha256 per
skill that this repo can actually verify. A key that reads as a pin while
nothing here stands behind it is worse than no key at all.

If you installed the old per-skill plugins (`workflow-path-audit`,
`rename-pdfs`, …), they migrate to their bundle automatically on
`/plugin marketplace update agentskills` via the marketplace `renames` map.
That map is **append-only forever** — JSON has no comments, so it's said here:
never delete or repoint an entry, because users may update from any old
version and every historical name must keep resolving.

**Migrating an existing machine:** re-run `bash setup.sh` immediately after
pulling this restructure — the global sync-skills pre-push hook still points
at the old path and **blocks every `git push` from any repo** until
re-registered (setup.sh also relinks the now-dangling per-agent skill links).
If you had both `fastmail` and `fastmail-identities` installed, the merged
`fastmail` keeps its previous enabled/disabled state and a version-pinned
cache lacks the two migrated-in skills — run
`claude plugin update fastmail@agentskills` (and
`claude plugin enable fastmail@agentskills` if it ended up disabled).

Available skills:

<!-- BEGIN GENERATED PLUGIN TABLE -->
| Plugin | Invocation | Description |
| --- | --- | --- |
| `adam` | `/adam:adam-writing-style` | Write in Adam Daniel's voice — professional but warm, direct, em-dash-friendly, free of corporate buzzwords. |
| `adam` | `/adam:debug-github-workflows` | Debugging GitHub Actions workflow failures. |
| `adam` | `/adam:disarm-inherited-reach` | Sever a scratch tree's inherited push path to the real repository the moment the tree exists, before anything runs in it. |
| `adam` | `/adam:finding-unknowns` | Surface and resolve the ambiguities in a task before, during, and after implementation — the blind-spot pass, the self-interview, reference-driven specs, implementation notes, and a post-hoc explainer or quiz. |
| `adam` | `/adam:github-actions-repo-settings` | Configure and enforce GitHub repository security settings as code: require actions to be pinned to full-length commit SHAs, require approval for all outside collaborators' fork pull-request workflow runs, and protect the default branch via a repository ruleset. |
| `adam` | `/adam:review-bash-ci-reliability` | Review bash scripts for CI/CD reliability issues. |
| `adam` | `/adam:skills-doctor` | Diagnose skill DELIVERY health for the current session: name the surface, diff the expected set in `skills.lock` against what actually loaded (the session's own skill listing, `~/.claude/skills/`, the account `synced/manifest.json`, `claude plugin list`), attribute every skill to the registry and bundle it came from by reading the bootstrap hook's own install record rather than guessing, and flag silent shadowing, account-store staleness, dangling payload references, and always-on context cost. |
| `adam` | `/adam:workflow-path-audit` | Audit GitHub Actions workflows for salient-path conditionals — every workflow that triggers on pull_request or push must filter on the files and directories its steps actually depend on, and skip with success when nothing salient changed. |
| `adam` | `/adam:writing-adrs` | Write a lightweight Nygard-style Architecture Decision Record under `docs/decisions/` when a non-obvious decision needs context that won't fit in a code comment and would rot if left only in a PR description. |
| `adam-local` | `/adam-local:compare-pdfpairs` | Compare pairs of PDFs (name.pdf + name<suffix>.pdf in the same folder) to determine whether they would produce identical printouts and whether their embedded text differs — e.g. to safely delete redundant "-signed" or "-needsocr" duplicates. |
| `adam-local` | `/adam-local:launch-wsl-claude-session` | Launch a detached, interactive Claude Code session inside WSL from a Windows Claude Code session — in a specific repo/folder, optionally remote-controllable and optionally seeded with an initial prompt. |
| `adam-local` | `/adam-local:migrate-claude-memory` | Inventory, clean up, and migrate Claude Code auto-memory stores found under ~/.claude/projects/<munged-path>/memory/ on this machine. |
| `adam-local` | `/adam-local:ocr-pdfs` | Batch-OCR scanned PDFs flagged as needing OCR, then visually review results with a WPF side-by-side comparison tool. |
| `adam-local` | `/adam-local:pdf-ocr-audit` | Audit PDF files to determine whether OCR (optical character recognition) is needed to make them fully text-searchable. |
| `adam-local` | `/adam-local:rename-pdfs` | Rename already-searchable PDFs in a specified folder to descriptive, date-prefixed names, proposing each name from the PDF's own content and prompting for per-file confirmation or edit before applying. |
| `adam-local` | `/adam-local:sync-cc-settings-between-wsl-and-windows` | Sync Claude Code settings.json between a Windows home and a WSL home. |
| `adam-local` | `/adam-local:sync-skills` | Sync local skill folders from git repos to Claude.ai (and other agent targets) via the upload-skill API. |
| `adam-local` | `/adam-local:windows-elevation-from-wsl` | Handle "Access is denied" from powershell.exe or pwsh.exe run inside WSL — Register-ScheduledTask / Set-ScheduledTask on a RunLevel=HighestAvailable task, a service change (Set-Service, Stop-Service, New-Service), an LSA rights grant such as "Log on as a batch job" (SeBatchLogonRight, secedit, ntrights), an HKLM registry write, or any other change to Windows state from a WSL session. |
| `adam-local` | `/adam-local:wj-next-break` | Answer questions about the current or next class period, break, passing period, lunch, or bell at Walter Johnson High School (WJ / WJHS, Bethesda MD). |
| `cms-platform` | `/cms-platform:<skill>` — skills live in [Adam-S-Daniel/cms-platform](https://github.com/Adam-S-Daniel/cms-platform) | The cms-platform site machinery's own skills, federated from that repo rather than mirrored here: Decap /admin config rendering, AWS bootstrap and PR preview environments, Playwright e2e, CI watcher loops, stuck-PR triage, and the platform release/consumer-bump flow. |
| `fastmail` | `/fastmail:add-from-address` | Add one or more email addresses to a Fastmail account as selectable "From" (sending) identities by triggering the add-from-address GitHub Actions workflow in the Adam-S-Daniel/fastmail-actions repo (which does the JMAP work with the FASTMAIL_API_TOKEN repo secret). |
| `fastmail` | `/fastmail:add-received-from-addresses` | Discover which of a Fastmail account's own alias addresses are worth being able to send from, and add them as "From" identities, by triggering the add-received-from-addresses GitHub Actions workflow in the Adam-S-Daniel/fastmail-actions repo (which does the JMAP work with the FASTMAIL_API_TOKEN repo secret). |
| `fastmail` | `/fastmail:fastmail` | Automate Fastmail email workflows via a local browser session. |
<!-- END GENERATED PLUGIN TABLE -->

## Install — Codex, Cursor, and local use

These tools discover skills from per-agent directories rather than a marketplace.
Run `setup.sh` once **in each environment** (Windows Git Bash *and* WSL — they have
separate `$HOME`s):

```bash
bash setup.sh
```

It links every skill under `plugins/*/skills/*` into the standard skill homes:

- `~/.agents/skills/` — Codex (and the generic agents dir)
- `~/.agent/skills/`
- `~/.cursor/skills/`

**Claude Code is deliberately not in that list** — it's served by the marketplace
above. Linking the same skills into `~/.claude/skills` too would double-load them
(once as a namespaced plugin, once as a personal skill), so `setup.sh` now removes
any such links it created in earlier versions. Background and rationale:
[`docs/2026-06-05-skill-discovery-and-centralized-strategy.md`](docs/2026-06-05-skill-discovery-and-centralized-strategy.md).

> **Gemini / Antigravity was retired as a target (2026-08-14).** That is an owner
> **scope decision, not** a finding that those paths were dead — Gemini/Antigravity is
> four separately-versioned products, three of which read skills from three
> *different* directories, and the Antigravity IDE genuinely does read
> `~/.gemini/antigravity/skills`. So this removes a link that was doing real work.
> Because un-listing a home leaves the old links behind — still feeding an
> unmanaged copy of the skill set, and dangling as soon as a skill is renamed —
> `setup.sh` also **sweeps** `~/.gemini/skills/` and
> `~/.gemini/antigravity/skills/`: it removes only links that resolve into this
> repo's `plugins/` tree, then removes each directory only if that left it empty.
> Your own files and links there are untouched. Re-run `bash setup.sh` on any
> machine set up before this change.

On Windows it uses directory junctions (`mklink /J`) — no admin required. The script
is idempotent and migrates the old whole-directory links left by earlier versions.

After running `setup.sh`, you don't need to restart an open Claude Code session —
run `/reload-skills` to re-scan the skill directories in place.

> Codex reads `~/.agents/skills`; that link is what makes these skills available in
> Codex. See the [Codex skills docs](https://developers.openai.com/codex/skills).

### Agent Plugins v1 — the root `plugin.json`

Each bundle ships **two** manifests, on purpose:

| File | Read by |
| --- | --- |
| `plugins/<bundle>/plugin.json` | [Agent Plugins 1.0.0](https://agent-plugins.org) clients — Codex, VS Code, Cursor, GitHub Copilot |
| `plugins/<bundle>/.claude-plugin/plugin.json` | Claude Code |

Claude Code is **not** an Agent Plugins conformant client and is absent from the
spec's client roster, so it keeps its own manifest; the two coexist rather than
one replacing the other. Neither declares the skills — the spec discovers them
by convention at `<plugin-root>/skills/`, which this repo's layout already
satisfies. Because both are shipped, both can drift, so
`scripts/check_agent_plugins.py` validates the root manifests against the
schema **vendored** at `schemas/agent-plugins-1.0.0-plugin.schema.json` (the
spec repo publishes no tags or releases, so there is nothing to pin a fetch to)
and cross-checks `name` + `version` between each pair. It runs in CI.

The schema is closed, and requires only `$schema` and `name`. The
marketplace-only keys `category` and `defaultEnabled` are **invalid** in a root
manifest, and there is no `skills` key at all.

Measured minimum client versions:

- **Codex ≥ 0.147.0** — established by source-diffing release tags
  `rust-v0.146.0` vs `rust-v0.147.0`. It accepts **only** the exact canonical
  `$schema` string; anything else is rejected as "unsupported Agent Plugins
  schema".
- **VS Code ≥ 1.131.0** — established by tag-bisecting
  `src/vs/platform/agentPlugins/common/agentPluginParser.ts`. Never announced
  in the release notes.
- **Cursor** — reads it (verified in the newest CLI build); no changelog names
  it and no minimum is established.
- **GitHub Copilot** — has read a root `plugin.json` as its own long-standing
  format; declaring `$schema` is what opts into Agent Plugins v1 semantics
  (GA 2026-08-12). No minimum established; measured working on 1.0.79 and 1.0.80.

**Codex needs no extra marketplace file.** Its marketplace search path includes
`.claude-plugin/marketplace.json` alongside `.agents/plugins/marketplace.json`,
so the file this repo already has is the one it reads — verified live
(`codex plugin add adam@agentskills` installed every skill).

<!-- Do NOT add .agents/plugins/marketplace.json. Codex 0.147.0's
     MARKETPLACE_MANIFEST_RELATIVE_PATHS is [".agents/plugins/marketplace.json",
     ".agents/plugins/api_marketplace.json", ".claude-plugin/marketplace.json",
     ".cursor-plugin/marketplace.json"] — this repo's existing
     .claude-plugin/marketplace.json is already read. A second marketplace file
     would create a second source of truth for zero gain. -->


## Hosted agents — Claude Code on the web, claude.ai

Hosted sessions start with **no user plugins and no marketplace adds** — but
`~/.claude` is not empty: the claude.ai account store is already present at
`~/.claude/skills/synced/` and loads from turn one (see "The claude.ai account
store" below). The repo clone can additionally *write* into `~/.claude`; that
write is the delivery channel for ephemeral surfaces. What works where:

- **Claude Code on the web / cloud sessions**: files committed to the repo being
  worked on — `CLAUDE.md`, `AGENTS.md`, `.claude/settings.json`, `.claude/skills/`,
  `.claude/memory/` — are all picked up. Repo-declared `extraKnownMarketplaces` +
  `enabledPlugins` are honored for *local* teammate sessions, but as of 2026-07
  they do **not** install anything in cloud sessions (verified by experiment —
  see [ADR 0001](docs/decisions/0001-consolidate-plugins-into-bundles.md),
  "Experiment evidence"; matches anthropics/claude-code#32606).
- **The bootstrap hook** — [`.claude/hooks/skills-bootstrap.sh`](.claude/hooks/skills-bootstrap.sh),
  armed as a `SessionStart` hook in `.claude/settings.json`. It fetches the
  registry and copies the bundle's skill directories into `~/.claude/skills`, so
  the skills are live for **turn one** of a hosted session and are inherited by
  any subagent that session spawns. A committed hook, not a vendored mirror, is
  therefore all a consumer repo needs (experiment
  [E2](docs/experiments/E2-sessionstart-skill-bootstrap.md), incl. why
  `claude plugin install` is *not* a substitute).
  What it installs is pinned and integrity-checked by
  [`skills.lock`](skills.lock) — registry, an immutable commit SHA, and a sha256
  per skill — because fetching instruction text at session start is a
  supply-chain surface; re-pin it with
  `python3 scripts/generate_skills_lock.py --repin`, which inherits the lock's
  registry, bundles and federated `sources` instead of taking them off the
  command line (a bare re-run drops every source the command line does not
  repeat, and exits 0). The hook is a no-op on a durable
  machine (the marketplace install is authoritative there), it skips any skill
  the project already owns in `.claude/skills/` (personal skills shadow project
  ones), and it always exits 0 — a failure downgrades to a one-line
  `skills: DEGRADED — …` notice naming the knob to fix.
  It also **removes** a skill that later leaves the lock, so a withdrawn or
  renamed one stops loading instead of living on under a clean verdict — but
  only one it installed itself and nobody has edited since, tracked in
  `~/.claude/skills/.skills-bootstrap-installed.json` and scoped to the
  registries and bundles the locks still declare. A hand-placed skill, the
  skills of a repo that is **not in this session**, and the account-sync
  `synced/` store are never touched; an edited one is kept and named in the
  verdict rather than deleted.
  In a session opened on several repos it reads **every** repo's lock and
  installs the union, so a repo in the same session is no longer "another
  repo" — its skills are this run's too. Two locks naming one skill directory
  at the same digest collapse to one install; at different digests neither
  installs and the verdict names the locks that disagree. See
  [ADR 0007](docs/decisions/0007-install-the-union-of-every-discovered-lock.md),
  and [`docs/multi-repo-delivery.md`](docs/multi-repo-delivery.md) for the
  wiring such a session needs before any of it runs.
- **The claude.ai account store** — `~/.claude/skills/synced/`, populated by
  uploading skills as ZIPs via Settings → Capabilities. This is the *only*
  channel that reaches claude.ai chat, Cowork, Claude in Chrome, and mobile —
  and it loads in Claude Code on the web / cloud sessions too, alongside
  whatever the repo delivers. Where both channels carry the same skill NAME the
  hook's copy wins and the name is listed once — measured in
  [E5](docs/experiments/E5-account-store-vs-hook-precedence.md), which is also
  why a stale account copy is shadowed in a hook session and still live in chat,
  Cowork, mobile and any multi-repo session. It can't be repo-scoped (see
  [ADR 0002](docs/decisions/0002-limit-account-store-to-repo-independent-skills.md)),
  so it's reserved for skills that should be live everywhere, not per-repo
  ones. The [`sync-skills`](plugins/adam-local/skills/sync-skills) skill (in
  the `adam-local` bundle) automates pushing this registry's skills there.
  Nothing in CI can see that store — a *surface* limit, not a permissions one:
  it is files under `~/.claude/skills/synced/`, which a runner simply does not
  have — so what a runner compares against is
  [`account-state.json`](account-state.json) — a digest per declared skill,
  recorded from a session that *does* have the mirror
  (`sync_skills.py --record-account-state`). The
  [Account skill ZIPs](.github/workflows/account-skill-zips.yml) workflow reads
  it, and daily also reads the account audit
  [skills-evals](https://github.com/Adam-S-Daniel/skills-evals) publishes to its
  `eval-results` branch — the one thing that does look at the store — building
  one artifact per skill *either* source calls drifted, each downloading as a
  `<name>.zip` that uploads to claude.ai as-is: the path for uploading from a
  phone. The union is deliberate (each source knows something the other cannot),
  and intersecting the audit's names with the declared list is the guard on
  reading an unprotected branch — see
  [ADR 0006](docs/decisions/0006-drive-the-account-store-drift-loop-from-one-published-artifact.md).
  A `stale` verdict is evidence an upload is needed, never proof one
  happened. Close the loop afterwards either by re-recording from a machine
  with the mirror, or — with no mirror, from the phone — by dispatching
  [Record an account upload](.github/workflows/record-account-upload.yml),
  which writes the weaker `basis: asserted` and pushes a branch to merge. An
  observation always overwrites an assertion. See `sync-skills` SKILL.md §9.
- **Memory**: hosted sessions see a repo's git-tracked `.claude/memory/` (see the
  Memory section in [`STRATEGY.md`](STRATEGY.md) and the
  [portable-memory guide](https://github.com/Adam-S-Daniel/claude-memory-map/blob/main/docs/portable-memory.md);
  migrate existing machine-local stores with the `migrate-claude-memory` plugin).

## Repo layout

```
.claude-plugin/marketplace.json       # catalog: 3 local bundles + 1 federated + renames map
plugins/
  <bundle>/                           # adam | adam-local | fastmail — LOCAL bundles only;
                                      # cms-platform is federated, so no directory here
    plugin.json                       # Agent Plugins 1.0.0 manifest
    .claude-plugin/plugin.json        # Claude Code bundle manifest
    skills/<skill>/SKILL.md           # one dir per skill (+ scripts/, tests/, hooks/)
schemas/                              # vendored Agent Plugins schema (pinned by sha256)
docs/decisions/                       # ADRs (see 0001 for the bundle restructure)
setup.sh                              # link skills into per-agent dirs (non-Claude-Code)
```

Validate the marketplace and any plugin with `claude plugin validate <path>`.

## Global Instructions

I put the following in Claude desktop app -> Settings -> Cowork -> Global instructions 🤞:

> When it seems likely to be beneficial, create/update skills. Follow
> https://agentskills.io/specification and validate with `claude plugin validate`.
> Skills live in bundle plugins: add a new skill as
> `plugins/<bundle>/skills/<skill>/SKILL.md` in the right bundle — `adam` for
> cloud-safe general-purpose skills, `adam-local` for machine-bound ones,
> `fastmail` for Fastmail — no new plugin.json or marketplace entry needed.
> Do **not** use `claude plugin init` — it
> scaffolds into `.claude/skills`, which is not this repo's marketplace layout.
> Never rename skill directories, and never delete or repoint entries in the
> marketplace `renames` map (append-only). Push to `main` in
> https://github.com/Adam-S-Daniel/agentskills. Then fetch and pull in WSL and Windows
> under `~/repos` and `%USERPROFILE%\repos`, and run `bash setup.sh` in both WSL and
> Windows Git Bash so the skills are linked into the standard locations
> (`.agents/skills/`, `.agent/skills/`, `.cursor/skills/`) — Claude Code itself
> uses the marketplace, not `.claude/skills`. Run `/reload-skills` to pick up changes
> without restarting the session.
