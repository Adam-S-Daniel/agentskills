---
name: skills-doctor
description: >
  Diagnose skill DELIVERY health for the current session: name the surface,
  diff the expected set in `skills.lock` against what actually loaded (the
  session's own skill listing, `~/.claude/skills/`, the account
  `synced/manifest.json`, `claude plugin list`), attribute every skill to the
  registry and bundle it came from by reading the bootstrap hook's own install
  record rather than guessing, and flag silent shadowing, account-store
  staleness, dangling payload references, and always-on context cost. Reports
  only — it never installs, copies, deletes or repairs anything. Use when a
  skill you expected is missing or won't trigger, when a repo-owned skill looks
  overridden, when the session-start `skills:` verdict reads DEGRADED, when you
  need to know where a skill came from or whether the hook installed it, or
  when the user says "why didn't that skill load", "which skills do I actually
  have", "is my registry stale", "audit my skills", "skills doctor".
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
echo "entry=$CLAUDE_CODE_ENTRYPOINT remote=$CLAUDE_CODE_REMOTE_SESSION_ID force=$SKILLS_BOOTSTRAP_FORCE env=$CLAUDE_CODE_REMOTE_ENVIRONMENT_TYPE"
```

| Reading | Surface | What SHOULD be true |
|---|---|---|
| a non-empty remote session id, OR `entry=remote` exactly, OR `SKILLS_BOOTSTRAP_FORCE` set | ephemeral (cloud session, CI, container) | the bootstrap hook installed the locked bundle into `~/.claude/skills/`; **no** marketplace plugins (cloud gets none from repo-declared settings) |
| all three empty | durable machine | the marketplace install is authoritative; `~/.claude/skills/` should hold **no** hook-installed bundle skills — finding them there means double delivery |
| some other entrypoint, no session id, not forced | unsure | not a shade of either row. Judge it as durable — the quiet reading — and settle it against the session's own `skills:` verdict rather than the entrypoint's name |

A durable machine with a full set of bundle skills in `~/.claude/skills/` is a
finding, not a pass.

**Match the hook's test exactly — no wider, and no narrower.**
`skills-bootstrap.sh` installs on any of those three readings, so a diagnostic
that recognises fewer of them disagrees with the hook silently: it answers
`unsure`, which is the quiet reading, on a surface the hook has just installed
onto. That is how #85's headline defect survived its own fix.

**But do not widen to the entrypoint's SHAPE.** Every ephemeral entrypoint
measured so far begins with `remote`, which makes a prefix match look like the
same rule; it is not. The binary's own display classifier groups
`remote_cowork` with `local-agent`, so "no durable entrypoint starts with
`remote`" is unproven, and assuming it would call a durable Cowork machine
ephemeral and report its correctly-empty store as a delivery failure. The exact
string `remote` is settled and matched; `remote_cowork` and the other `remote_*`
spellings stay `unsure`.

## 2. Read the expectation

```bash
cat skills.lock     # registry, pinned ref, bundles, expected skills + sha256 each
```

If there is no `skills.lock`, stop and report that: with no declared
expectation the rest of this skill can only describe, never verdict.

## 3. Collect the actual

Five independent signals. Gather all five — each one is blind to something the
others see.

```bash
ls -1 ~/.claude/skills/                    # personal store: hook-installed or hand-placed
cat ~/.claude/skills/.skills-bootstrap-installed.json   # the hook's own account of what IT installed
ls -1 ~/.claude/skills/synced/             # account store (claude.ai uploads)
cat ~/.claude/skills/synced/manifest.json  # per skill: skillId, source, updatedAt
claude plugin list --json                  # installed bundles + the commit SHA each resolved to
```

The sixth signal is **the session's own skill listing** — the names offered to
the Skill tool in this context. It is the only signal that says what the model
can actually *trigger*, and it is the authority when it disagrees with disk.
Read it out of context; do not reconstruct it from the filesystem.

## 4. Attribute every skill to its source

A name's shape narrows it to a channel:

| Listed as | Channel | Confirm with |
|---|---|---|
| `adam:foo` (namespaced) | plugin / marketplace bundle | `claude plugin list --json` |
| `foo` (bare) | personal `~/.claude/skills/foo/` — hook-installed or hand-placed | the install record, below |
| `foo` (bare) | account store | `foo` has a record in `synced/manifest.json` |

The last two collide on purpose: both present as a bare name, so the manifest
is what separates them. A skill in `synced/manifest.json` came from the
account; one on disk in `~/.claude/skills/` but absent from the manifest came
from the hook or a hand copy.

**One name can be in both, and there the manifest confirms the collision
rather than resolving it.** Names reach a cloud session from the hook and the
account store at once — measured on this registry's own sessions
(agentskills#122). The listing shows each such name once, and nothing in it,
on disk, or in any log says which copy the model read.
`check_provenance.py` reports every one: a `shadowed-by-the-account-store`
NOTE where the two copies match once CRLF is folded to LF, and a
`shadow-copies-differ` FINDING where they do not. Both verdicts are over the
files an UPLOAD carries — the account copy is the ZIP `zip_skill` built, so
what the upload filter drops is excluded from both sides (`__pycache__`,
`.pytest_cache`, `.pyc`, `.b64` and `node_modules` among them), and a `diff -r`
of the two directories can disagree with a NOTE that calls their instructions
identical. Treat a match as a
measurement of the moment and not a guarantee — the two copies update on
different clocks, and only the divergent case is a defect. Which channel wins
when they disagree is an open question, not something the report answers.

**Which of those two it is, the hook already answered.** It writes
`~/.claude/skills/.skills-bootstrap-installed.json` — one entry per skill it
installed, with the registry it was fetched from, the bundle, and the digest
the bytes had when it verified them. Run the reader rather than reconstructing
it:

```bash
python3 -I scripts/check_provenance.py --lock skills.lock
```

**That path is relative to this skill's own directory, not to the repo you are
standing in** — `~/.claude/skills/skills-doctor/scripts/` when the hook
delivered it, `plugins/adam/skills/skills-doctor/scripts/` in a registry
checkout. Resolve it before running: the registry has a top-level `scripts/` of
its own, so the bare form run from the repo root reports `No such file or
directory` and exits 2, which is also argparse's code for a bad flag.

`-I` because this recomputes sha256 over directories to decide whether a skill
is still the bytes that were installed, and without it `sys.path[0]` is **the
script's own directory** — which for a delivered skill is content a fetched
registry supplied. A `hashlib.py` dropped in beside it would be what answers.
(The hook carries `-I` for the neighbouring reason: it runs `python3 -I -`, so
`sys.path[0]` would be the *project* directory instead.)

Exit 1 means *there are findings*, not that the tool failed; 0 means none.
`--skills-dir` and `--project-dir` point it at a store and a project other than
this machine's.

**`--lock` is optional, and leaving it off is usually right.** Omitted, it takes
the project dir's own `skills.lock`; when the project dir has none it resolves
every `*/skills.lock` one level below and reports per lock. That second case is
the multi-repo session, and it is the one the old bare default got wrong: it
resolved to nothing at the parent and reported the absence of a lock as though
it were the absence of a problem — 0 findings, exit 0, over nine undelivered
skills. Naming a lock explicitly is still honoured exactly, and never widened
into a scan. Several locks judging one store names **no winner** among them;
every finding says which lock declared it, and identical findings from several
locks are folded into one that names them all.

**It reads the surface, and the surface changes the verdict.** A locked skill
missing from the personal store is the correct state on a durable machine and a
delivery failure on an ephemeral one, so the same three facts are a NOTE on one
and a FINDING on the other. The test is the bootstrap hook's own three arms,
copied: a remote session id, OR `CLAUDE_CODE_ENTRYPOINT` exactly `remote`, OR
`SKILLS_BOOTSTRAP_FORCE`. Reading fewer of them than the hook does is
disagreement, not caution. What is NOT copied is any prefix match on the
entrypoint's shape — unproven against `remote_cowork`, held deliberately — so
any other entrypoint with no session id reports as `unsure` and is judged as
durable, which is the quiet reading.

The same reading gates every sentence about what the NEXT run does. Those three
arms are tested by the hook BEFORE it reads the lock, and failing all three it
prints `skills: skipped` and exits — so on a durable or unsure machine there is
no install, no refusal and no `skills:` verdict to read `DEGRADED`. Findings
that promise any of those carry the caveat themselves rather than leaving the
reader to reconcile them against the SURFACE block further up the report.

**`hook-not-wired` is the finding to look for in a multi-repo session.** A lock
plus a SessionStart hook wired only in a *child* of the project dir means no
hook is consulted at all, so nothing is delivered and nothing says so — there is
no `skills:` verdict because the script that prints one never runs. The two
scopes are read the way the binary reads them, and they are not the same shape:
the project scope is a **chain** — `settings.local.json` then `settings.json`,
both consulted — so a fix in either silences the finding; the user scope is a
**selection** of one name by Cowork mode — `cowork_settings.json` when
`CLAUDE_CODE_USE_COWORK_PLUGINS` is set, `settings.json` otherwise — and the
name not selected is never consulted. Treating that selection as a chain is the
mistake that points the wrong way: it reports the user scope as wired off a file
the machine never opens, and so suppresses the finding on a machine whose hook
genuinely cannot fire.

Three links are unreadable from a process and none is a file this can open: a
managed/policy settings file, a `--settings` path given on the command line, and
the `coworkPlugins` config flag (the other arm of the user-scope selection). The
tool **prints that sentence on the finding itself** — it is not documentation
you have to have read, because the person running the script is not necessarily
the person reading this. If the finding fires on a session you believe is wired
through one of those, that is the gap. See `docs/decisions/0005` in the
registry.

Produce one row per expected skill, and report `registry` and `bundle` on it,
not just the channel — "personal copy" does not say whether it came from the
fleet registry, a federated one, or somebody's hand, and "loaded" without a
source is not a diagnosis. A row the record names is **fact**: it is the
writer's own account of what it wrote, not an inference from the filesystem.

**These readings are different machines, not shades of one thing.** Do not
collapse them into "no record":

| State | What it proves | What to do |
|---|---|---|
| present | the hook installed exactly these, from these sources | nothing; read the rows as fact |
| present, `installed: []` | a run *completed* and installed nothing | anything on disk is not the hook's |
| absent | no run has ever reached the point of writing it | expected on a durable machine; on an ephemeral one read the session-start `skills:` verdict for why delivery never happened |
| unreadable | the hook cannot read it either: it pruned nothing that run, and rewrites it from scratch at the next session start | start one clean session — it self-heals in one run, but everything from before the corruption is forgotten, so anything that left the lock in that window is now left alone forever. Still unreadable after that? The rewrite happens LAST, after the lock read, the git probe and the fetch, so that run never got there: read its `skills:` verdict |

The lock has states of its own, and one of them is not a shade of "no lock":
a file that parses as JSON but that the hook's reader **refuses** — no
`bundles`, a bundle no source claims, a registry that is not `OWNER/REPO` or a
URL. It installs nothing at all from such a lock, so every skill judgement made
against one names a cause that never happened. `check_provenance.py` reports it
as `lock-rejected` and withholds the rest.

Only when the record cannot answer — absent, or unreadable — does the old
heuristic apply: directories the hook wrote in one run share an mtime, because
`cp -R` stamps the copy with the copy time. It is inference and the reader must
be told so. It degrades in both
directions — a hand copy made in the same minute as an install clusters with
the install, and a hook-installed skill an editor has touched since falls out
of the cluster — and neither failure announces itself. That is the whole reason
the record exists; never prefer the cluster to it.

## 5. The five things that fail silently

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

### Skills nothing will ever remove

The hook removes a skill that has left the lock only when it can prove all four
of: the record says it installed it, the bytes are still exactly what it
installed, the lock still declares that (registry, bundle), and the lock no
longer names it. Everything else it leaves alone — correctly, because
`~/.claude/skills` is the user's own directory and deleting work to satisfy a
lock they may not control is the worse failure.

The consequence is that directories live there permanently with nothing saying
so — a skill the record does not name at all, one whose bytes no longer match
what was installed, one whose (registry, bundle) the lock has stopped declaring.
The causes differ and so does what to do about each, which is why
`check_provenance.py` names the cause rather than just the count. Read its
FINDINGS section; the hook being right not to act is exactly why a human has to
see them.

Two of these are easy to state backwards. What preserves an edited skill is the
**digest mismatch**, not its having left the lock — restore the original bytes
and the next run removes it, so moving it out of the store is the only way to
keep it. And a skill whose bundle the lock no longer claims is not merely
unremoved: it is out of scope, so the hook does not mention it in any verdict
either, and nothing will ever say it is there.

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

Then: the surface and what it implies; a table of expected skill → source →
status, where source is the `registry # bundle` the record names and not merely
the channel; each finding with the file path and the knob that fixes it. Say
which record state the attribution rests on, because it is what separates a
report that is fact from one that is inference. **Where there is no readable
record, write `<n> unattributable`, never `0 unattributed`** — the zero is
arithmetically true and reads as "everything is accounted for", which is the
exact inversion. Close with the context-cost
figure. No remediation is performed — recommend, do not do.

## Traps that will mislead you

- **`~/.claude/skills/synced/` cannot be seeded or simulated.** It is
  manifest-gated: writing a directory there does nothing at all. You can only
  observe it, so never "test" a hypothesis about the account channel by
  creating files in it — the negative result is meaningless.
- **Skills load from every workspace root, not just cwd.** In a multi-repo
  session, one repo's committed skills are advertised while you are working in
  another. Enumerate all workspace roots before concluding a skill "came from
  nowhere".
- **The shadow guard is INERT in a multi-repo shape, not merely mispointed.**
  Both the hook and `check_provenance.py` look for repo-owned skills at
  `$PROJECT_DIR/.claude/skills/<name>/SKILL.md`. When the project dir is the
  parent of several repos, that directory does not exist at all — so the guard
  can never fire for ANY of them, and `delivered-by-the-project` can never be
  the reason a locked skill is absent. Harmless today only because the one
  repo-owned project skill on this fleet (`embeddable-tool-pages`) is not among
  the locked names; it stops being harmless the moment a repo ships a skill a
  lock also declares. Do the shadowing comparison by hand against each
  workspace root, per the `comm` recipe above, rather than trusting either
  tool's silence. Fixing it needs an answer to "which repo's skills win", which
  is the open question in `docs/decisions/0005`.
- **Absence from the listing is not absence from disk.** Deduplication and the
  listing budget both drop entries. Check disk *and* listing; a mismatch
  between them is itself a finding.
- **A green session-start verdict only covers the hook's own channel.** It says
  what the bootstrap installed and verified. It knows nothing about the account
  store, the marketplace, or shadowing, so it is a starting point and never the
  whole answer.
- **The record's `registry` is a resolved git remote URL; the lock's is a
  slug.** A lock saying `Adam-S-Daniel/agentskills` produces record entries
  saying `https://github.com/Adam-S-Daniel/agentskills.git`, and the verdict
  prints the slug again. Compare them with `==` and every skill reads as coming
  from a registry its own lock does not declare.
- **`AGENTSKILLS_BUNDLE` narrows a run and nothing on disk records that it
  did.** A run narrowed to one bundle claims authority over that bundle only
  and deliberately leaves every other bundle's skills alone — so a skill that
  has left the lock can sit there indefinitely while any reading of the record
  and the lock says the next run will remove it. The hook names it in that
  run's verdict; no later inspection can recover it.
- **The record's digest is the one taken at install and is never refreshed.**
  That is what makes "edited since install" detectable at all: re-recording the
  edited digest would make the next run's comparison succeed and delete the
  user's work. So a digest mismatch against the record means *edited*, while a
  mismatch against the **lock** means the copy predates the current lock — two
  different facts from the same number. One mismatch against the record is
  neither: when every file an UPLOAD would carry still matches, the difference
  is a build artefact (`__pycache__`, `.pytest_cache`) and the doctor reports
  `artefacts-and-locked`, naming the extra files it actually found.
- **A directory the hook will not overwrite is not a directory the hook is
  about to delete.** `may_replace` in `.claude/hooks/skills-bootstrap.sh`
  overwrites in three cases only — nothing is there, what is there already
  digests to the digest the lock names, or the record names it and the bytes
  still digest to what was recorded — and REFUSES everything else. A refusal
  copies nothing and removes nothing: the skill is dropped from that run, the
  bytes stay, and the name is listed after `DEGRADED … shadowed`. So the risk
  an `edited-and-locked`, `artefacts-and-locked`, `hand-placed-over-locked` or
  `unattributable-over-locked` finding describes is a skill that stops being
  delivered, never local work about to be overwritten — and a build artefact
  left by running a skill's own suite in place stalls that skill's updates
  until it is deleted, which is why it is a finding rather than a note.
- **The middle clause is why some of those are only a note.** "What is there
  already digests to the digest the lock names" asks nothing about who put the
  directory there, so a hand-placed copy of exactly the bytes the bundle ships
  is overwritten like any other and delivery is unaffected. The doctor measures
  that clause and reports `bytes-are-the-locked-ones` instead of a refusal —
  the state is not a defect, it is a state that becomes one the next time the
  lock's digest for that name moves.
