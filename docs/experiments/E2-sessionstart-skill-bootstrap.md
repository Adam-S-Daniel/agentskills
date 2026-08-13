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

See `E2-CLOUD-RESULT.md` (written by a fresh Claude Code on the web session spawned
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
