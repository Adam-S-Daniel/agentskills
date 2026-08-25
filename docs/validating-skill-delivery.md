# Validating skill delivery across surfaces

A checklist for proving that skills actually reach a session, on each surface
and each session shape. Written to be run by **either** a person or an agent,
because the two see different things and neither sees everything — which is the
single most expensive fact in this whole area and the reason this file exists.

## Read this first: who can observe what

| signal | agent sees it | operator sees it |
|---|---|---|
| the `skills:` verdict, as `systemMessage` | yes | **yes, from the hook that carries it** |
| the `skills:` verdict, as `additionalContext` | yes | no |
| the session's skill roster | yes | only by asking the agent, or via the UI's skill list |
| `~/.claude/skills/` on disk | yes | yes (a terminal, or by asking) |
| `~/.claude/skills/.skills-bootstrap-installed.json` | yes | yes |

**The verdict became operator-visible on 2026-08-25** and reaches a given
session only once that session's pinned hook carries the change. Until then, a
person validating a consumer repo has to use the filesystem checks, not the
line. If you are unsure which hook a session ran, that is itself the first thing
to establish — step 0 below.

## Step 0 — establish what you are actually running

Do this before any permutation, because every result below is uninterpretable
without it.

```bash
echo "entrypoint : ${CLAUDE_CODE_ENTRYPOINT:-<unset>}"
echo "remote sid : ${CLAUDE_CODE_REMOTE_SESSION_ID:+set}${CLAUDE_CODE_REMOTE_SESSION_ID:-<unset>}"
echo "cwd        : $PWD"
echo "home       : $HOME"
claude --version
```

Then decide the surface from the table in
[`multi-repo-delivery.md`](multi-repo-delivery.md) — do **not** infer it from
the name. `ssh-remote` is durable; `claude-in-teams` is hosted.

And establish which hook copy would run, since the alphabetically-first attached
repo supplies it for the whole session:

```bash
for h in "$PWD"/.claude/hooks/skills-bootstrap.sh "$PWD"/*/.claude/hooks/skills-bootstrap.sh; do
  [ -f "$h" ] && { echo "WOULD RUN: $h"; sha256sum "$h"; break; }
done
```

A copy older than the guard widening reports `skipped — durable session` on six
of the seven `remote*` spellings and installs nothing. That failure looks
exactly like a correct decision, so compare the digest against
`_agent-guidance`'s `repos.yml` `skills_bootstrap.sha256` before concluding
anything about the wiring.

## The four permutations

Surface × session shape. Each cell states what SHOULD happen and how to tell.

| | **single-repo** | **multi-repo** |
|---|---|---|
| **laptop** (durable) | marketplace plugins load; hook self-skips | marketplace plugins load; hook self-skips |
| **claude.ai** (hosted) | repo's own `.claude/settings.json` fires the hook | needs the environment setup script |

The laptop row is the same in both columns **by design** — marketplace plugins
install at user level, so `cwd` is irrelevant to them. If those two cells ever
differ, something is wrong with the plugin install, not with session shape.

### P1 — laptop, single-repo

**Expect:** skills available as `/adam:<skill>`; the hook installs nothing.

```bash
claude plugin list                  # the adam bundle should be listed
ls ~/.claude/skills/                # NOT where marketplace skills live
```

Ask the agent: *"list your available skills and the exact slash command for
each."* Bundle-namespaced (`/adam:finding-unknowns`) is the pass.

**Fail signatures.** Skills missing entirely → the marketplace was never
installed; run `setup.sh`. Skills present but **bare-named** (`/finding-unknowns`)
→ they came from `~/.claude/skills/`, not the marketplace, so something
installed them as directories; check
`~/.claude/skills/.skills-bootstrap-installed.json`.

### P2 — laptop, multi-repo

**Expect:** identical to P1. Open a session with `cwd` above two repos and
re-run P1's checks.

**Fail signature.** Anything different from P1 means the plugin install is not
actually user-level — the one result here that would invalidate the claim that a
laptop needs no wiring.

### P3 — claude.ai, single-repo

**Expect:** the hook fires from the repo's committed `.claude/settings.json`;
skills land in `~/.claude/skills/` and are invoked **bare**.

```bash
cat ~/.claude/skills/.skills-bootstrap-installed.json   # exists only if the hook ran
ls ~/.claude/skills/
```

Then diff against the lock — the check that actually proves delivery:

```bash
python3 - <<'PY'
import json, os
lock = json.load(open("skills.lock"))
want = sorted(k.split("/")[-1] for k in lock["skills"])
d = os.path.expanduser("~/.claude/skills")
have = sorted(x for x in os.listdir(d)
              if os.path.isdir(os.path.join(d, x)) and not x.startswith("."))
print("lock  :", want)
print("onDisk:", have)
print("MISSING:", [x for x in want if x not in have] or "none")
PY
```

Ask the agent for its `skills:` verdict. `n/n … — OK` is the pass.

**Fail signatures.** No verdict and no install record → the hook never ran.
`skipped — durable session` in a session you know is hosted → either a stale
hook copy (step 0) or an entrypoint the guard does not classify as remote.
`DEGRADED` → the verdict names the knob; act on that rather than guessing.

### P4 — claude.ai, multi-repo

The one that needs the environment setup script. **Expect** P3's outcome, plus
the union across every attached repo that carries a lock.

```bash
ls -la "$PWD/.claude/settings.json"   # written by the setup script; absent => not wired
```

Run P3's lock-diff **once per attached repo**, from each repo's directory. The
verdict should name every contributing lock, e.g.
`skills: 22/22 from … across 2 locks (adamdaniel.ai, agentskills) — OK`.

**Fail signatures.** `$PWD/.claude/settings.json` absent → the setup script did
not run, or wrote elsewhere; it hardcodes the project dir precisely because
`CLAUDE_PROJECT_DIR` is unset and `$PWD` is wrong at setup time. Verdict names
one lock when two repos carry one → the hook that ran predates union discovery
(step 0 again). Present on the first session of an environment and absent on the
second → the filesystem snapshot did not preserve the settings file, and the
wiring belongs in a `SessionStart` hook rather than a setup script. **That last
one is an open question, not a known behaviour — if you hit it, record it.**

## Offline permutations an agent can run without new sessions

These need no session at all and are the fastest way to catch a regression.
Every one is a real measurement, not a code reading.

```bash
# 1. Surface classification, every legal entrypoint, no session id.
#    Expect: the 10 remote spellings install, the 16 durable ones skip.
for ep in remote remote_mobile remote_cowork ssh-remote cli claude-in-teams; do
  d=$(mktemp -d)
  out=$(env -i PATH="$PATH" HOME="$d" CLAUDE_PROJECT_DIR="$PWD" \
        CLAUDE_CODE_ENTRYPOINT="$ep" bash .claude/hooks/skills-bootstrap.sh </dev/null 2>&1 | head -1)
  n=$(ls "$d/.claude/skills/" 2>/dev/null | wc -l)
  case "$out" in *"durable session"*) v=SKIP ;; *) v="INSTALL($n)" ;; esac
  printf '%-18s %s\n' "$ep" "$v"; rm -rf "$d"
done

# 2. The verdict reaches both readers, and identically.
d=$(mktemp -d)
env -i PATH="$PATH" HOME="$d" CLAUDE_PROJECT_DIR="$PWD" \
  CLAUDE_CODE_ENTRYPOINT=remote_mobile bash .claude/hooks/skills-bootstrap.sh </dev/null \
  | python3 -c 'import json,sys; p=json.load(sys.stdin); \
      print(p["systemMessage"]); \
      print("IDENTICAL" if p["systemMessage"]==p["hookSpecificOutput"]["additionalContext"] else "DIFFER")'
rm -rf "$d"
```

**Always use a throwaway `HOME`.** The hook writes to `~/.claude/skills/` and
prunes what its own record says it installed; pointing it at a real profile
during a test is how a validation run becomes an incident.

## What to record when something fails

Enough for the next person to skip the re-derivation:

- the four lines from step 0, verbatim;
- the digest of the hook copy that would run, and the pin it was compared to;
- the verdict text, or an explicit "no verdict, and no install record either";
- which permutation, and what you expected instead.

Durable findings belong in this file or in
[`multi-repo-delivery.md`](multi-repo-delivery.md) — not in a chat log, and not
in agent memory, which the next session silently does not read.
