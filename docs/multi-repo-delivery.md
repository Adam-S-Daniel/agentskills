# Skill delivery in a multi-repo session

A Claude Code on the web session created with several repositories clones them
side by side and sets the project directory to their **parent**. Getting the
fleet's pinned skills into that session takes two independent things, and it is
worth being blunt about which of them a repo can and cannot ship:

| half | what it is | who ships it |
|---|---|---|
| **the union** | the hook installs every discovered repo's locked skills, not one repo's | this repo — see [ADR 0007](decisions/0007-install-the-union-of-every-discovered-lock.md) |
| **the wiring** | something has to make a `SessionStart` hook fire at all | **the environment** — it cannot be committed by a repo |

Without the second, the first never runs. That is not a gap in the union; it is
[ADR 0005](decisions/0005-resolve-hooks-only-from-cwd-and-user-settings.md)'s
finding, and it is why every claim elsewhere in these docs of the form "the hook
installs nothing in a multi-repo session" is still true until the wiring below is
placed.

## First: which surface are you on

**This whole document is about the CLOUD. A laptop needs none of it**, and the
two surfaces differ in what delivers skills, where they land, and what you type
to invoke one. Getting that wrong is the single easiest way to misread every
section below.

| | laptop (durable machine) | Claude Code on the web (cloud/ephemeral) |
|---|---|---|
| what delivers skills | the **marketplace plugin install** — `setup.sh`, `/plugin install adam@agentskills` | the **`skills-bootstrap` hook**, from `skills.lock` |
| where they land | `~/.claude/plugins/` | `~/.claude/skills/<name>/` |
| scope | **user-level, so cwd-independent** | per session, rebuilt each time |
| multi-repo session | already works — nothing to do | needs the wiring below |
| invoke a skill as | `/adam:skills-doctor` — bundle-namespaced | `/skills-doctor` — **bare, no prefix** |

**The invocation difference is not cosmetic and is easy to get backwards.**
Plugin skills are namespaced `<bundle>:<skill>`; a skill installed as a
directory under `~/.claude/skills/` has no bundle to be namespaced by, so it is
just its own name. Measured, not assumed: a probe skill planted at
`~/.claude/skills/zz-invocation-probe/` and a hook-installed
`~/.claude/skills/finding-unknowns/` were both reported by a fresh CLI session
as `/zz-invocation-probe` and `/finding-unknowns`. So `/adam:skills-doctor`
**does not exist in a cloud session**, and typing it there gets
`Unknown command`.

**Why the laptop needs nothing, stated so nobody adds wiring it does not want.**
Marketplace plugins install at user level, so they are already loaded whatever
`cwd` is — the multi-repo shape that breaks cloud delivery is not a problem
there at all. And the hook itself declines to act: on a non-remote entrypoint it
emits `skills: skipped — durable session (…), marketplace install is
authoritative` and installs nothing. A laptop that has never run `setup.sh` gets
no skills, and the fix for that is the marketplace, never the setup script
below — a laptop has no environment setup script in the first place, because
that field belongs to a **cloud environment**.

## Why the wiring cannot live in a repo

Claude Code resolves hooks from the settings chain rooted at `cwd` and at
`$HOME`:

```
managed/policy → --settings → <cwd>/.claude/settings.local.json
              → <cwd>/.claude/settings.json → $HOME/.claude/settings.json
```

`--add-dir` is a tool-access grant. An additional directory contributes skills
(with live reload), commands and agents — and `CLAUDE.md` only when
`CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD` is set — but **never hooks, and
never the rest of settings**. So in a session whose project dir is the parent of
eleven repos, all eleven `.claude/settings.json` files are read and none is
applied. No repo's hook fires, whatever its command string says.

Measured, not inferred — re-confirmed on Claude Code 2.1.241 with controls: a
byte-identical settings file fires its hooks as `<cwd>` and fires nothing as an
`--add-dir` root, across three event types, while `--add-dir` was demonstrably
honoured in the same runs. `skills-doctor` reports the state as `hook-not-wired`.

The committed wiring cannot simply be re-pointed either. This repo's
`.claude/settings.json` runs
`bash "$CLAUDE_PROJECT_DIR/.claude/hooks/skills-bootstrap.sh"`, and in the
multi-repo shape `$CLAUDE_PROJECT_DIR` is the parent — which has no
`.claude/hooks/` at all.

## The wiring (cloud only)

Put this in the **cloud environment's setup script** — the *Setup script* field
in the environment settings dialog at claude.ai/code. It writes a settings file
at the project level, a link of the chain that is definitely read.

There is no laptop counterpart to this section and no laptop version of the
script. A durable machine has no environment setup script, and would have
nothing for one to fix: see *First: which surface are you on* above.

```bash
#!/bin/bash
# Deliver the fleet's pinned skills in a multi-repo session.
#
# Hooks resolve from <cwd>/.claude and $HOME/.claude only, never from the repos
# below cwd, so this cannot be committed to any of them (ADR 0005).
#
# THE PROJECT DIR IS HARDCODED, and the two obvious ways to derive it are both
# wrong HERE. A setup script runs as root before Claude Code launches, and
# measured in a hosted session: `CLAUDE_PROJECT_DIR` is UNSET — Claude Code sets
# it when it invokes a hook, which this is not — and `HOME` is `/root`, not the
# project dir's parent. So `${CLAUDE_PROJECT_DIR:-$PWD}` falls through to a
# `$PWD` that is not the project dir, and writes the file somewhere nothing
# reads. The near miss is the bad one: `/root/.claude/settings.json` is exactly
# the user-scope file the four notes below decline to write.
#
# Getting it right the first time matters more than usual, because the setup
# script runs ONCE. Anthropic snapshots the filesystem afterwards and later
# sessions skip the script, so a wrong path is baked into the snapshot and
# persists until the script changes or the cache expires (~7 days).
#
# Safe in a single-repo session too: there `cwd` IS the repo, so this file sits
# above it, off the settings chain and inert, and the repo's own committed
# `.claude/settings.json` fires as it does today.
project=/home/user

# NOT `set -eu`. A setup script that exits non-zero makes the SESSION FAIL TO
# START, and skills not loading is by far the lesser failure — hence `|| true`
# here and the unconditional `exit 0` at the end.
mkdir -p "$project/.claude" || true
cat > "$project/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'for h in \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/skills-bootstrap.sh \"$CLAUDE_PROJECT_DIR\"/*/.claude/hooks/skills-bootstrap.sh; do [ -f \"$h\" ] && exec bash \"$h\"; done'",
            "timeout": 90
          }
        ]
      }
    ]
  }
}
JSON
echo "skills wiring: wrote $project/.claude/settings.json"
exit 0
```

`$CLAUDE_PROJECT_DIR` **inside** the hook command is correct and must stay: that
one is expanded at hook time, by Claude Code, which does set it. Only the outer
`project=` cannot rely on it.

Four things about it are deliberate:

- **It resolves the hook at session start, not at setup time.** The project
  dir's own copy is tried first, so a single-repo session behaves exactly as it
  does today; then each child repo's. If no attached repo ships the hook the
  command exits 0 and nothing happens — there is nothing to deliver.
- **`timeout: 90`.** The hook's own budget for fetching every source is 60
  seconds, so a shorter harness timeout kills it mid-fetch and loses the
  fail-soft verdict it exists to print.
- **Project scope, not `$HOME/.claude/settings.json`.** The hosted container
  ships its own `~/.claude/launcher-settings.json` carrying harness hooks. How a
  user-scope `settings.json` interacts with it is undocumented, and getting it
  wrong could displace the harness's own `SessionStart` hook. The project-level
  file has no such interaction, and it also satisfies `skills-doctor`'s
  `hook-not-wired` finding, which is the correct signal.
- **It is safe in a repo that never adopted delivery.** Since ADR 0007 the
  `$SELF_ROOT` fallback applies only when `CLAUDE_PROJECT_DIR` is unset, so a
  session whose project dir and children carry no lock installs nothing rather
  than silently inheriting the bundle of whichever repo happened to ship the
  hook.

**Which copy of the hook runs, and why a stale one can deliver NOTHING.** The
first match wins — the glob is alphabetical, so the alphabetically-first attached
repo supplies the hook for the whole session. The fleet's copies are synced and
sha-pinned by `_agent-guidance`, so they are normally byte-identical; a repo
sitting on an older pin supplies an older copy.

The obvious cost of an older copy is missing union discovery, so only that repo's
lock is read. **That is not the expensive one.** Before the entrypoint guard was
widened (ADR 0007's PR), the hook tested `CLAUDE_CODE_ENTRYPOINT != "remote"` —
an exact match against ONE spelling, while seven legal values begin with
`remote`. On any of the other six it took the early return and reported
`skills: skipped — durable session`, a sentence that reads like a correct
decision while installing nothing.

Measured across both hook copies at three spellings, same lock, same project dir,
only the entrypoint varying:

| entrypoint | pre-widening copy | post-widening copy |
|---|---|---|
| `remote` | 22/22 — OK | 22/22 — OK |
| `remote_mobile` | **0 — "skipped, durable session"** | 22/22 — OK |
| `remote_cowork` | **0 — "skipped, durable session"** | 22/22 — OK |

`remote_mobile` is not hypothetical: it is what `CLAUDE_CODE_ENTRYPOINT` actually
held in the hosted session these notes were measured in. So the wiring above can
be placed correctly, fire correctly, resolve a hook correctly, and still deliver
nothing — because the copy it resolved is older than the guard fix. **Check the
hook copy the glob will actually pick, not just that the wiring is in place**,
and treat `skipped — durable session` in a session you know is remote as that
diagnosis rather than as a durable machine.

## What crosses into a multi-repo session, and what does not

Hooks do not. **Skills do** — and that asymmetry is worth knowing before anyone
reaches for a workaround, because it is the one thing a repo can still deliver
into this shape on its own.

Measured in a hosted two-repo session on Claude Code **2.1.245**, project dir
`/home/user` with `adamdaniel.ai` and `agentskills` beneath it: the project skill
committed at `adamdaniel.ai/.claude/skills/embeddable-tool-pages/` was live in
that session's skill roster, while neither repo's `SessionStart` hook fired at
all and no `skills:` verdict was printed. Established by elimination rather than
by inference — the only other skill sources present were `~/.claude/skills/`
(the claude.ai account-sync channel's `synced/`, plus one built-in), and that
skill is in neither, so it reached the session from a child of `cwd`.

So a repo that needs one or two specific skills in a multi-repo session **can**
commit them under `.claude/skills/` and skip the hook entirely. That is a real
escape hatch and a bad default. A vendored copy is not digest-verified, does not
track the registry, and is precisely the drift ADR 0001 declined to take on
fleet-wide; adamdaniel.ai's own guidance says "do NOT re-vendor" for exactly
this reason. Reach for it for a **site-owned** skill — which is what
`embeddable-tool-pages` is — never for a bundle. The wiring above is what
delivers a bundle.

One further measurement from the same session, because it bears on why the
snippet writes project scope rather than user scope: the hosted container ships
**no `~/.claude/settings.json` at all**. The harness's own hooks live in
`~/.claude/launcher-settings.json` — a different filename, and so a different
entry in the settings chain rather than the same file being overwritten. That
does not make a user-scope `settings.json` safe, and the bullet above stands as
written: whether two chain entries merge their `SessionStart` arrays or one
wins is still unmeasured here. It narrows the risk to that question alone, and
rules out the cruder reading — that the two would collide by filename.

## Checking it worked

**Who sees the verdict depends on which hook the session is running**, and the
answer changed on 2026-08-25.

The hook emits its line as `hookSpecificOutput.additionalContext`, which Claude
Code's hooks reference defines for `SessionStart` as *"text to inject into
Claude's context"*. The field that renders to a **person** is the top-level
`systemMessage`, and for most of this hook's life it set only the first. So "no
`skills:` line" was a thing the agent could report and a human simply could not
observe — reading its absence as "the hook never ran" was sound only when the
agent was the one looking. That is not academic: the absence was once put to an
operator as something they should have noticed across months of sessions, when
they were never in a position to notice it.

The hook now emits **both**, bound to one scrubbed value so the operator and the
agent cannot be told different things. Printed at every start, deliberately: it
is the ABSENCE of the line that diagnoses a hook which never fired, so a verdict
shown only when `DEGRADED` would go silent in exactly the case worth noticing.

**A consumer does not get this the moment it merges here.** Sessions run the
hook their `skills_bootstrap` pin names, so the verdict stays invisible to the
operator until that pin moves and the sync delivers. Until then, treat the three
checks below as the way to see it.

The verdict, when there is one:

```
skills: 23/23 from Adam-S-Daniel/agentskills@eb25bd6 across 3 locks (agentskills, cms-platform, adamdaniel.ai) — OK
```

With several locks it names how many contributed and which repos they came from.
With exactly one lock the line is unchanged from what it has always been.
`DEGRADED` names the knob to fix.

**So check it one of these ways instead**, in rough order of directness:

- **Ask the agent.** "What does your `skills:` verdict say?" It is in the
  context window; it can quote it verbatim, `DEGRADED` reason and all.
- **Look at the filesystem** — a cloud check, and the most decisive one, because
  it inspects the install rather than a report about it. `ls ~/.claude/skills/`:
  the hook writes each locked skill as a directory there. Two things in that
  listing are NOT the hook's doing and are the easy false positive —
  `synced/` is the claude.ai account-sync channel, and a skill can also arrive
  from a repo's own `.claude/skills/`. The unambiguous signal is the hook's own
  install record, `~/.claude/skills/.skills-bootstrap-installed.json`: it exists
  only if the hook ran, and it names what the hook itself put there.
  On a **laptop** this bullet does not apply — look in `~/.claude/plugins/`
  instead, or run `claude plugin list`, because the marketplace is what
  installed them and `~/.claude/skills/` is not where they live.
- **Run the skills-doctor skill** for the long form, once a bundle carrying it
  has actually loaded — **`/skills-doctor` in a cloud session**, where the hook
  installs it as a bare directory, and `/adam:skills-doctor` on a laptop, where
  the marketplace namespaces it by bundle. Typing the laptop spelling in the
  cloud gets `Unknown command`, which reads like the skill is missing when it is
  present under its other name. Note also the ordering trap: the diagnostic that
  explains why no skills loaded is itself a skill
  (`plugins/adam/skills/skills-doctor`), so it is unavailable in exactly the
  case you most want it.

**Check the SECOND session too, not just the first.** The setup script runs only
when no cached environment exists; afterwards the filesystem is snapshotted and
later sessions skip it. Anthropic's documentation says a snapshot keeps what the
script wrote to disk, which should carry `$project/.claude/settings.json`
forward — but the repos beneath it are re-cloned per session, and whether that
leaves this file untouched is NOT established here. Skills present on the first
session and absent on the second is that failure, and the remedy is to move the
wiring into something that runs every session rather than once: a `SessionStart`
hook in a settings file the session does read. Recorded as an open question
rather than a caveat, because one session cannot answer it.

**The trade that was weighed before adding `systemMessage`:** it prints a line
at every session start, in every repo carrying the bundle, forever. That is real
noise. It wins anyway, on two counts — the line is one sentence, and the failure
it replaces is unbounded (an operator who cannot tell a working install from a
dead one has no way to stop being wrong about it). Showing it only on `DEGRADED`
was the obvious compromise and is the wrong one: a hook that never fires prints
no `DEGRADED` either, so the compromise goes quiet in the only case that
actually needed a signal.

## What a repo still has to do

Delivery stays opt-in and double-keyed: a repo contributes only if it has
committed its own `skills.lock`. A repo without one contributes nothing.

Two locks that name the same skill directory at the **same** digest collapse to
one install, which is the ordinary case when several repos pin one registry. Two
that name it at **different** digests install neither and say so, naming the
locks that disagree — the fix is to re-pin one of them, not to pick a winner.
See ADR 0007 for why.
