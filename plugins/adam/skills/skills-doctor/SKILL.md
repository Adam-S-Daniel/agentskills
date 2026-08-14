---
name: skills-doctor
description: >
  Diagnose skill DELIVERY health for the current session: name the surface,
  diff the expected set in `skills.lock` against what actually loaded (the
  session's own skill listing, `~/.claude/skills/`, the account
  `synced/manifest.json`, `claude plugin list`), attribute every skill to the
  channel that delivered it, and flag silent shadowing, account-store
  staleness, dangling payload references, and always-on context cost. Reports
  only — it never installs, copies, deletes or repairs anything. Use when a
  skill you expected is missing or won't trigger, when a repo-owned skill looks
  overridden, when the session-start `skills:` verdict reads DEGRADED, when you
  need to know whether a skill arrived via the plugin bundle or a personal or
  account copy, or when the user says "why didn't that skill load", "which
  skills do I actually have", "is my registry stale", "audit my skills",
  "skills doctor".
compatibility: >-
  Claude Code only. Reads ~/.claude/skills, the account synced/ store and the
  `claude plugin` CLI, none of which exist in other agent harnesses. Needs the
  registry checked out locally to compare against; everything else is
  observation of the running session.
---

# Skills doctor

A session can only see the skills it was *given*. So the failure that matters —
a skill that should have loaded and didn't — is invisible by construction: it
leaves no entry in the listing, no file on disk, and no error anywhere. Asking
"what loaded?" can never find it.

The fix is to diff against a **declared expectation**. `skills.lock` is that
expectation: registry, pinned commit, bundles, and the exact skill set with a
sha256 each. Everything below compares observed reality to it.

**Report, never self-heal.** Do not install, copy, delete, re-sync, or "just
fix" anything, even when the repair is one obvious command. The whole value of
this skill is a trustworthy account of what the session actually got; an agent
that silently repairs delivery destroys the evidence and hides a bug that will
recur on the next surface. Name the defect, name the knob that fixes it, stop.

## 1. Name the surface first

Expectations differ per surface, so establish which one this is before judging
anything:

```bash
echo "entry=$CLAUDE_CODE_ENTRYPOINT remote=$CLAUDE_CODE_REMOTE_SESSION_ID env=$CLAUDE_CODE_REMOTE_ENVIRONMENT_TYPE"
```

| Reading | Surface | What SHOULD be true |
|---|---|---|
| `entry=remote`, non-empty remote session id | ephemeral (cloud session, CI, container) | the bootstrap hook installed the locked bundle into `~/.claude/skills/`; **no** marketplace plugins (cloud gets none from repo-declared settings) |
| both empty | durable machine | the marketplace install is authoritative; `~/.claude/skills/` should hold **no** hook-installed bundle skills — finding them there means double delivery |

A durable machine with a full set of bundle skills in `~/.claude/skills/` is a
finding, not a pass.

## 2. Read the expectation

```bash
cat skills.lock     # registry, pinned ref, bundles, expected skills + sha256 each
```

If there is no `skills.lock`, stop and report that: with no declared
expectation the rest of this skill can only describe, never verdict.

## 3. Collect the actual

Four independent signals. Gather all four — each one is blind to something the
others see.

```bash
ls -1 ~/.claude/skills/                    # personal store: hook-installed or hand-placed
ls -1 ~/.claude/skills/synced/             # account store (claude.ai uploads)
cat ~/.claude/skills/synced/manifest.json  # per skill: skillId, source, updatedAt
claude plugin list --json                  # installed bundles + the commit SHA each resolved to
```

The fifth signal is **the session's own skill listing** — the names offered to
the Skill tool in this context. It is the only signal that says what the model
can actually *trigger*, and it is the authority when it disagrees with disk.
Read it out of context; do not reconstruct it from the filesystem.

## 4. Attribute every skill to its channel

A name's shape already tells you where it came from:

| Listed as | Channel | Confirm with |
|---|---|---|
| `adam:foo` (namespaced) | plugin / marketplace bundle | `claude plugin list --json` |
| `foo` (bare) | personal `~/.claude/skills/foo/` — hook-installed or hand-placed | directory present, mtime clusters with the hook's other copies |
| `foo` (bare) | account store | `foo` has a record in `synced/manifest.json` |

The last two collide on purpose: both present as a bare name, so the manifest
is what separates them. A skill in `synced/manifest.json` came from the
account; one on disk in `~/.claude/skills/` but absent from the manifest came
from the hook or a hand copy. Produce one row per expected skill with the
channel named — "loaded" without a channel is not a diagnosis.

## 5. The four things that fail silently

### Shadowing

Personal `~/.claude/skills/` **outranks** project `.claude/skills/` (measured).
A repo-owned skill can be overridden by a same-named personal copy with no
signal in the listing, on disk, or in any log — the model simply reads the
wrong instructions. Compare the two directories by basename:

```bash
comm -12 <(ls -1 ~/.claude/skills) <(ls -1 .claude/skills)
```

Every name printed is a shadowed repo-owned skill. Report each one with both
paths and say which copy is winning (the personal one, always).

### Staleness of the account store

The account store carries **no content hash and no version** — `updatedAt` is
the only drift signal there is. Compare it against the registry's last commit
touching that skill:

```bash
git -C <registry> log -1 --format=%cI -- plugins/adam/skills/<skill>
```

A registry commit newer than `updatedAt` means the account copy is behind.

**Normalise line endings before comparing content.** Account copies are CRLF,
the registry is LF. A naive hash or `diff` reports *every* skill as drifted,
which is a check nobody will keep:

```bash
diff <(tr -d '\r' < ~/.claude/skills/synced/<skill>/SKILL.md) \
     <(tr -d '\r' < <registry>/plugins/adam/skills/<skill>/SKILL.md)
```

### Missing payloads

A `SKILL.md` that tells the agent to run a file which is not there is a skill
that fails at the moment of use, having looked healthy until then. Check that
every `scripts/`, `references/`, `assets/` or `templates/` path named in a
skill's runnable blocks exists inside that skill's own directory. The
mechanised version of this is `scripts/check_skills.py` in the registry — run
it rather than eyeballing when the registry is checked out:

```bash
python3 <registry>/scripts/check_skills.py
```

### Context cost

Every loaded skill's description is always-on context. `claude plugin details`
reports it per bundle:

```bash
claude plugin details adam
```

Measured: the `adam` bundle is ~1,479 tok always-on for 8 skills (~185
tok/skill). This is not a tidiness point. At the default listing budget the
descriptions of the least-used skills are **silently dropped**, so a skill can
be loaded, present on disk, and still untriggerable — indistinguishable from
never having been delivered. Report the bundle's total and the skill count, and
flag it when a session is carrying skills it has no use for.

## 6. Report shape

One verdict line, then the evidence:

```
skills-doctor: <n>/<n> expected present, <n> shadowed, <n> stale, <n> unattributed — OK | DEGRADED
```

Then: the surface and what it implies; a table of expected skill → channel →
status; each finding with the file path and the knob that fixes it. Close with
the context-cost figure. No remediation is performed — recommend, do not do.

## Traps that will mislead you

- **`~/.claude/skills/synced/` cannot be seeded or simulated.** It is
  manifest-gated: writing a directory there does nothing at all. You can only
  observe it, so never "test" a hypothesis about the account channel by
  creating files in it — the negative result is meaningless.
- **Skills load from every workspace root, not just cwd.** In a multi-repo
  session, one repo's committed skills are advertised while you are working in
  another. Enumerate all workspace roots before concluding a skill "came from
  nowhere".
- **Absence from the listing is not absence from disk.** Deduplication and the
  listing budget both drop entries. Check disk *and* listing; a mismatch
  between them is itself a finding.
- **A green session-start verdict only covers the hook's own channel.** It says
  what the bootstrap installed and verified. It knows nothing about the account
  store, the marketplace, or shadowing, so it is a starting point and never the
  whole answer.
