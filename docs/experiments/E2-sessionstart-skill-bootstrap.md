# Experiment E2 — SessionStart-hook skill bootstrap

**Question.** Can a repo-committed `SessionStart` hook install the canonical skill
registry into a session at boot, so that a consumer repo needs to vendor **no**
skill files at all?

Context: `Adam-S-Daniel/cms-platform#249` concluded "no replacement channel exists
for repo-scoped skills" and therefore recommended keeping adamdaniel.ai's vendored
`.claude/skills/` mirror. Experiment E1 (recorded in that issue) established that
repo-declared *plugins* do not install in cloud sessions. E2 tests a different
channel that E1 did not consider.

Run 2026-08-13, Claude Code `2.1.231`, inside a Claude Code on the web container.

## Discriminators

The same four used by E1: `finding-unknowns`, `writing-adrs`,
`debug-github-workflows`, `review-bash-ci-reliability`. They are in the `adam`
bundle but **not** in the claude.ai account uploads, so their presence cannot be
explained by the account-sync channel, and E1 proved the plugin channel is dead in
cloud. If they appear, the hook is the only possible source.

## Part 1 — hook-installed skills load in the same session (local headless)

Four workspaces, each with a `SessionStart` hook that writes one uniquely-named
canary skill, then a `claude -p` probe asking which `probe-*` skills are visible.
`~/.claude/skills/probe-*` was removed before every run.

| Variant | Hook installs into | Hook stdout | Model saw the canary? |
|---|---|---|---|
| t1 | `~/.claude/skills/` | `{"reloadSkills": true}` | **yes** |
| t2 | `~/.claude/skills/` | `{"hookSpecificOutput":{…,"reloadSkills":true}}` | **yes** |
| t3 | `~/.claude/skills/` | *(nothing)* | **yes** |
| t4 | project `.claude/skills/` | both shapes | **yes** |

**t3 is the important row.** With no `reloadSkills` at all the skill still loaded,
so on 2.1.231 skill discovery happens *after* `SessionStart` hooks run. Emitting
`reloadSkills` is belt-and-braces, not the mechanism.

Two harness traps cost a false negative each, and any future re-run must avoid both:

- `--disallowedTools …,Skill` suppresses the skills list entirely — every variant
  reported `NONE`. Leave `Skill` allowed.
- `--setting-sources project` excludes user-level skills, so even a skill
  pre-installed in `~/.claude/skills/` before launch reported `NONE`. Use
  `--setting-sources user,project`. A positive control (skill present at boot, no
  hook) is mandatory; without it both traps read as a real negative result.

## Part 2 — the realistic prototype

`.claude/hooks/skills-bootstrap.sh` clones the registry shallow, copies
`plugins/<bundle>/skills/*` into `~/.claude/skills/`, and emits `reloadSkills`.
It is surface-guarded (no-op unless the session is ephemeral, so it can never
clobber a developer's marketplace install) and fails soft (a missing registry
yields a notice, never a crash).

```
$ echo '{"hook_event_name":"SessionStart","source":"startup"}' | bash .claude/hooks/skills-bootstrap.sh
{"reloadSkills":true,"hookSpecificOutput":{"hookEventName":"SessionStart","reloadSkills":true,
 "additionalContext":"skills-bootstrap: installed 8 skills from Adam-S-Daniel/agentskills@main (65893a1), bundle=adam, into ~/.claude/skills."}}
```

End-to-end, starting from a `~/.claude/skills/` holding only `session-start-hook`
and `synced/`:

```
$ claude -p 'Of these four skill names, list ONLY the ones that appear in the Skills
  available to you: finding-unknowns, writing-adrs, debug-github-workflows,
  review-bash-ci-reliability. If none appear, reply exactly NONE.' \
  --setting-sources user,project --model claude-haiku-4-5
finding-unknowns, writing-adrs, debug-github-workflows, review-bash-ci-reliability
```

All four. Registry clone cost: **1.19 s** shallow.

## Part 3 — the real cloud-session boot path

See [`E2-cloud-session-result.md`](E2-cloud-session-result.md) (written by a fresh Claude Code on the web session spawned
against this branch). Part 1 and 2 ran the same binary but through `claude -p`
inside an already-booted container; a spawned session exercises the actual boot
path, including whatever hook-trust behaviour applies there.

## What this changes

`#249`'s second pillar — *no replacement channel exists for repo-scoped skills* —
is **false**. The consequences are argued in the issue; the short version is that a
consumer can carry one committed hook instead of a vendored mirror, which dissolves
the existence-keyed `#83` gate, the `rsync --delete` carve-out, the `.repo-local`
single point of failure, and the recurring sync PRs at once.

Limits this experiment does **not** clear: it says nothing about claude.ai chat,
Cowork, or any surface that does not run Claude Code hooks; and fetching
instruction text at session start is a supply-chain surface that wants an immutable
pinned ref plus integrity checking, not `main`.

---

# Companion measurements (same container, 2026-08-13, CLI 2.1.231)

Five further results taken while validating E2. Each is reproducible with the
harness shape in Part 1 (`--setting-sources user,project`, `Skill` left allowed,
positive control first).

## C1 — `reloadSkills` re-scans skill DIRECTORIES, not installed plugins

A hook that runs `claude plugin marketplace add` + `claude plugin install` succeeds
(the install log confirms it) but the plugin's skills are **absent** from that same
session's listing, even with `reloadSkills` emitted in both shapes:

```
--- hook log:  √ Successfully installed plugin: adam@agentskills (scope: user)
--- model saw: NONE
```

Consequence for design: on a **one-shot** surface (cloud session, CI runner) the
bootstrap must copy skills into a *skill directory*. `claude plugin install` only
pays off on a **durable** machine, where the next session picks it up.

## C2 — plugin install itself works fine at runtime in a cloud session

This narrows E1's finding considerably. What is broken in cloud is the
**declarative boot-time** install of repo-declared `extraKnownMarketplaces` /
`enabledPlugins` — *not* the plugin machinery:

```
$ claude plugin marketplace add Adam-S-Daniel/agentskills   → √ Successfully added
$ claude plugin install adam@agentskills                    → √ Successfully installed (scope: user)
$ claude -p '…list matching skills…'   (fresh, empty, unrelated workspace)
adam:finding-unknowns
adam:writing-adrs
adam:debug-github-workflows
adam:review-bash-ci-reliability
```

Note the `adam:` namespace, and that `installed_plugins.json` pins
`gitCommitSha: 65893a10ad16…` — plugin delivery is namespaced and version-pinned;
copying loose skill dirs is neither.

## C3 — precedence: personal `~/.claude/skills/` SHADOWS project `.claude/skills/`

One skill name, three homes, distinct description markers, asked which description
the model sees:

| Present in | Model saw |
|---|---|
| `synced/` only | **ABSENT** |
| `synced/` + personal | `PERSONALWINS` |
| `synced/` + personal + project | `PERSONALWINS` |
| project only *(control)* | `PROJECTWINS` |
| project + personal *(re-add)* | `PERSONALWINS` |

Two consequences:

1. **A fleet skill installed into `~/.claude/skills/` silently overrides a
   same-named skill the repo owns.** Any bootstrap that writes there needs either
   namespacing or a lint forbidding name collisions with repo-owned skills.
2. **Writing a directory into `~/.claude/skills/synced/` does nothing.** That store
   is manifest-gated, so the account channel cannot be simulated locally — it can
   only be observed from a session actually signed in to the account.

## C4 — a marketplace can publish a plugin that lives in another repo

```json
{ "name": "adam-remote",
  "source": { "source": "github", "repo": "Adam-S-Daniel/agentskills", "path": "plugins/adam" } }
```

Validates, adds, and installs (`√ Successfully installed plugin: adam-remote@fedtest`),
caching to `~/.claude/plugins/cache/fedtest/adam-remote/65893a10ad16`. So one
marketplace can federate bundles owned by several repos — a repo keeps its skills,
its release cadence and its review path, and still ships through the single registry.

## C5 — dual-format packaging, and the limit of it

`claude plugin validate` on a directory carrying **both** `.claude-plugin/plugin.json`
and an Agent Plugins v1 root `plugin.json`: passes, reading only the Claude manifest.
The same directory with **only** the root `plugin.json`:

```
× directory: No manifest found in directory.
  Expected .claude-plugin/marketplace.json or .claude-plugin/plugin.json
```

So the portable root manifest is free to add and Claude Code ignores it — but Claude
Code is **not** an Agent Plugins v1 client, and a pure-portable layout is unloadable
by it. Adopt the standard additively, exactly as its own migration guide prescribes.

## C6 — measured always-on token cost

`claude plugin details adam` reports the cost of carrying a bundle:

```
Projected token cost
  Always-on:   ~1,479 tok   added to every session      (8 skills, ~185 tok/skill)
```

At that rate adamdaniel.ai's 18 vendored skills cost roughly **3.3k tokens of every
session's context**. Material, and a reason to scope bundles per repo rather than
ship everything everywhere. `claude plugin details` also gives a first-party
per-skill breakdown, so this is measurable rather than estimated.

## C7 — subagents do NOT fire SessionStart hooks, and do not need to

The open risk carried into `#54`/`#56` was: if a subagent never fires the bootstrap
hook, delegated work is starved of the skills the delegation relies on — and fleet
policy routes implementation to subagents. Tested directly.

A workspace whose `SessionStart` hook logs every invocation (including whether the
stdin payload carries `agent_id` / `agent_type`) and installs one canary skill. The
top-level agent is asked to launch a **general-purpose** Task subagent and relay,
verbatim, which `zz-*` skills that subagent can see. `Read,Glob,Grep,Bash` are
disallowed for the whole run so the subagent cannot forage the answer off disk;
`Task` and `Skill` stay allowed.

| Arm | Hook | Subagent reported |
|---|---|---|
| negative control | disabled | `NONE` |
| test | enabled | `zz-subagent-canary` |

```
hook fire log (test arm, entire contents):
    FIRE event=SessionStart agent_id=None agent_type=None
```

**One fire, for the top-level session only** — no second invocation carrying
`agent_id`/`agent_type`. So the hook does not run per-subagent. But the subagent
still sees the skill, because subagent skill discovery reads the same
`~/.claude/skills/` the hook already populated at session start.

Conclusion: hook-delivered skills reach subagents. The blocker is retired. The
ordering it depends on is worth stating, though, because it is the real
constraint: **the hook must complete before the subagent is spawned.** That holds
for `SessionStart` by construction, and it is why an installer that runs later
(say, from a tool call mid-session) would not carry the same guarantee.

Not tested: whether `Explore`/`Plan`-type agents behave the same. They skip repo
guidance by design, so they should be assumed unreliable for skill-dependent work
regardless — which is already fleet policy.

## C8 — account-uploaded skills cannot be scoped to a repo, structurally

`#54` listed this as browser-only homework and as the potentially "durable reason"
account delivery can't replace repo skills. It is answerable from the client side,
and the answer is **no** — for two independent reasons, neither of which is a
settings gap that could be closed later.

**1. The synced manifest has nowhere to put scope.** Every per-skill record in
`~/.claude/skills/synced/manifest.json` carries exactly five fields:

```
per-skill keys (union across all skills): description, name, skillId, source, updatedAt
anything scope-shaped: NONE
```

Whatever claude.ai might express server-side, Claude Code has no channel to receive
it on. A client cannot honour a scope it is never told.

**2. The store does not vary by project.** Running
`CLAUDE_CODE_SYNC_SKILLS=1` from two different repos and diffing the resulting
store:

```
adamdaniel.ai  → 20 skills
agentskills    → 20 skills
diff           → IDENTICAL — store does not vary by project
```

The sync is keyed to `$HOME`, not to a working directory. There is one account
store per machine, and every session on that machine sees all of it.

**The word "project" is doing two jobs**, which is what makes this question feel
open longer than it is. A claude.ai **Project** is a chat container; a Claude Code
**project** is a repo directory. Even if per-Project skill attachment exists in the
chat product — worth checking for Cowork/chat use — it is a different axis and
cannot scope a Claude Code session in a git repo.

So the reason account delivery can't replace repo-scoped skills is **structural, not
precedential**. Name precedence (C3) is the reason usually reached for first; it is
also the weaker one, since it could be engineered around by renaming. This cannot.
That makes it the right thing to record in ADR 0001.

## C9 — Codex: install is scriptable from 0.146.x, but the root manifest is inert

Two questions about the non-Claude arm, both answered on the laptop.

### The tooling block is gone — upgrade Codex

`codex-cli 0.129.0` exposes only `codex plugin marketplace {add,upgrade,remove}` —
no install, no list, so plugin installation was TUI-only and unscriptable.
**0.146.1 adds `codex plugin {add,list,remove}`.** Both steps are now headless:

```
$ codex plugin marketplace add <root>       # root must contain .agents/plugins/marketplace.json
$ codex plugin add <plugin>@<marketplace>   # bare <plugin> errors: "requires --marketplace"
$ codex plugin list --json                  # machine-readable, incl. installed/enabled/version
```

State lands in `~/.codex/config.toml` as `[marketplaces.<name>]` and
`[plugins."<plugin>@<marketplace>"] enabled = true`, with the payload cached under
`~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/`. So the retirement of
`~/.agents/skills` for Codex is unblocked; it was a CLI-surface gap, not a design
one. (0.147.0 was skipped as 6 days old, inside the repo's 7-day cooling-off.)

### But Codex does not read the Agent Plugins root manifest

Two fixtures published through identical marketplaces — one plugin carrying **only**
a portable root `plugin.json`, one carrying **only** `.codex-plugin/plugin.json`.
Both install and enable, and **both plugins' skills materialise**. The discriminator
is the version column:

| Fixture | Manifest present | STATUS | VERSION | cache path |
|---|---|---|---|---|
| `demo-codexnative` | `.codex-plugin/plugin.json` | installed, enabled | `0.1.0` | `…/demo-codexnative/0.1.0` |
| `demo-portable` | root `plugin.json` only | installed, enabled | **`local`** | `…/demo-portable/local` |

The portable plugin's version is `local`, not the `0.1.0` its root manifest
declares. And Codex **synthesised** a native manifest for it:

```
$ cat …/probe-portable/demo-portable/local/.codex-plugin/plugin.json     # written by Codex
{ "description": "manifest-discovery probe", "name": "demo-portable" }
```

That description is the **marketplace entry's**, not the root manifest's
(`"PORTABLE-ROOT-MANIFEST-ONLY"`). The root `plugin.json` is copied through as an
inert file and never parsed.

### What this means for the design

**The portable core that actually works across clients today is the `skills/<name>/SKILL.md`
directory convention — not the manifest.** As of 2026-08-13, neither Claude Code
(measured, C5) nor Codex reads the Agent Plugins root `plugin.json`; each takes its
metadata from its own channel (`.claude-plugin/plugin.json`; the marketplace entry).

That does not argue against adding the root manifest — it is spec-prescribed, costs
one file, and is what makes the package conformant as clients catch up. It argues
against *expecting it to do work yet*, and against any plan that treats it as the
mechanism rather than the declaration. Note the compatible-clients page lists
"ChatGPT & Codex" with an Agent Skills check; that check is about loading `skills/`,
which is exactly what was observed — it is not a claim that the manifest is read.
