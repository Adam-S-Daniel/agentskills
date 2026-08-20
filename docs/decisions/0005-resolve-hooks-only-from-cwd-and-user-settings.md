# 0005. Resolve hooks only from the cwd and user settings chains

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Adam Daniel

## Context

`skills-bootstrap` is a SessionStart hook, and a hook only runs if something
reads the settings file that declares it. Which files get read looked like an
implementation detail until a multi-repo session made it the whole problem.

The measured shape, on a Claude Code on the web session with the project
directory set to the parent of eleven repos: every repo carried a correct
`skills.lock`, a correct `.claude/hooks/skills-bootstrap.sh`, and a correct
`.claude/settings.json` wiring the hook. Nothing was installed. No `skills:`
verdict was printed by anything. `~/.claude/skills/` held one directory and no
install record. From inside any single repo there was nothing to find — every
file it owns is present and right — which is why this took an investigation
(#84) rather than a glance.

Two prior beliefs had to go. The first was that `--add-dir` widens the settings
chain the way it widens skill and command discovery; it does not. The second
was that the `.claude/settings.json` files under those repos were being
*consulted*, because startup `stat`s them and logs no complaint — enumeration
leaves the same trace as resolution, so the absence of a "missing file" line
proves only that the file exists.

## Decision

Record the settings/memory asymmetry as **settled**, not as something for the
next session to re-derive:

> **Hooks resolve from `cwd`, never from `--add-dir`.** Claude Code's settings
> chain is managed/policy → `--settings` (CLI) → `<cwd>/.claude/settings.local.json`
> → `<cwd>/.claude/settings.json` → `$HOME/.claude/settings.json`. `--add-dir` is
> a tool-access grant: additional directories contribute CLAUDE.md (gated by
> `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD`), skills, commands and agents,
> but never hooks or settings. Their `.claude/settings.json` files *are* stat'd
> during startup — enumeration is not resolution, and the absence of a "missing
> file" log line proves only that the file exists. Consequence: in a
> multi-repo-parent session, **no** repo's SessionStart hook can fire, whatever
> its command string. This needs a `cwd`-level or user-level settings file, which
> only the environment can place. (#84, measured 2026-08-16.)

Two consequences are adopted with it:

- **A repo may not be blamed for this, and may not try to fix it from inside.**
  No command string, no hook path, no `matcher` value changes the outcome. The
  fix is a settings file at a level the chain reads, and placing one is the
  environment's job.
- **The diagnostic names it.** `check_provenance.py` reports `hook-not-wired`
  when a lock is present, a SessionStart hook is wired only in a *child* of the
  project directory, and neither the project level nor the user level wires one.
  A failure with no log line and no verdict needs something that goes looking
  for it, or it is found by investigation every time.

## Consequences

Positive: the state is now nameable in one word, from a tool, in the session
where it is happening. The previous cost was an investigation per occurrence,
and the previous *output* was `0 findings, exit 0` — a diagnostic actively
arguing that nothing was wrong.

Negative, and worth being plain about: this ADR records a constraint we do not
control. It is a description of Claude Code's behaviour as measured on
2026-08-16, not a contract. If the settings chain ever widens to include
`--add-dir` roots, the `hook-not-wired` finding becomes a false positive and
this ADR needs superseding rather than editing. The finding's own wording is
deliberately about *what was observed* — settings files below the project dir,
none at a level the chain reads — so it degrades to a puzzled reader rather
than to a confident wrong answer.

Also negative: the honest fix (a user-scope settings file) is **not** safe to
adopt yet, for a reason that has nothing to do with hooks. See the second open
question below.

## Open questions

Neither of these is answered here. They need a human decision, and inventing
one in a diagnostic — the tempting place, because that is where the ambiguity
shows up — would bury the choice under an implementation.

### Which lock wins in a multi-repo session?

Three repos carry locks; two pin the *same* `(Adam-S-Daniel/agentskills, adam)`
bundle at *different* refs (`agentskills@216e519c`, and both consumers at
`b0518b8f`). A union is not well-defined, and the hook's own docs record the
residual: two such repos each reap the other's installs. Latent today only
because the name sets are identical (9 = 9, empty symmetric difference). It
activates the moment they diverge — and the `workflow-path-audit` bundle move
already recorded in AGENTS.md shows they can.

What was done in the meantime: `check_provenance.py` judges the one store once
per lock and **names no winner**. Every finding says which lock declared it,
and identical findings from several locks fold into one that names them all.
That is a reporting posture, available only because the doctor never installs
or removes anything. The hook has no such option — it has to choose — so the
question stays open for it.

### Should the `$SELF_ROOT/skills.lock` fallback exist?

Measured: `CLAUDE_PROJECT_DIR=/home/user/rss-inator` — no lock, not allowlisted
— yields `skills: 9/9 … OK`. Any absolute-path invocation from outside a
lock-bearing project dir silently converts the fleet's stated *"deliberate
per-repo decision and not a fleet default"* into a fleet default. The
`DEGRADED — no skills.lock` path it was assumed to fail into is **unreachable**
under that invocation. This is also why a user-scope hook — the obvious #84 fix
— is unsafe until the fallback gains an explicit-lock mode.

The ordering that follows: a user-scope settings file placed today would make
every session on the machine reach the fallback, so the fallback's behaviour
has to be settled *first*. That is a sequencing constraint on the fix this ADR
otherwise recommends, and it is the reason the fix is not simply applied here.

## Correctness footnotes

Two measured details that do not change the decision but will mislead whoever
implements against it.

**The user-scope settings filename is surface-dependent, and it is a SELECTION
rather than a chain.** The binary selects `cowork_settings.json` under
`coworkPlugins` / `CLAUDE_CODE_USE_COWORK_PLUGINS` and `settings.json`
otherwise; it reads the selected one and does not fall back to the other. So
anything hardcoding `settings.json` is wrong on a Cowork surface — but so is the
obvious repair of reading BOTH, which is the mistake that reads like caution and
is not. `check_provenance.py` accordingly picks ONE name
(`user_settings_name()`), and reads it.

The two errors are not symmetric, which is why the repair has to be the right
one rather than merely a wider one. Hardcoding `settings.json` reports
`hook-not-wired` at a Cowork machine where the fix has already been applied —
loud, and the reader who is being told they are broken goes and checks. Reading
both names does the opposite: on an ordinary machine a wired
`cowork_settings.json` reports the user scope as wired while the only file that
machine opens wires nothing, so the finding is SUPPRESSED on a machine whose
hook genuinely cannot fire, and a check that has gone quiet is indistinguishable
from a machine that is healthy. Anything else that learns to read the user scope
inherits the selection, not the pair.

The other arm, `coworkPlugins`, is config and not an environment variable, so no
process can read it. `check_provenance.py` therefore reads only
`CLAUDE_CODE_USE_COWORK_PLUGINS` and names the gap on the `hook-not-wired`
finding itself, alongside the managed/policy file and the `--settings` path —
the two links of the chain above that are likewise not files it can open.

**The shadow guard is inert, not merely mispointed, in a multi-repo shape.** The
hook's `$PROJECT_DIR/.claude/skills/<name>/SKILL.md` check — and
`check_provenance.py`'s matching `repo_owned` lookup — resolve against a
`/home/user/.claude` that does not exist, so neither can fire for any of the
repos below it. Harmless today only because the one repo-owned project skill
(`embeddable-tool-pages`) is not among the locked names.

Deliberately **not** fixed here. Both halves of the fix need the first open
question answered: the hook would have to decide which repo's skills shadow, and
the doctor's `repo_owned` lookup exists to *suppress* a finding, so widening it
to child repos would hide delivery failures on a guess. Widening the doctor in
the same change that raises new findings would also make the net effect
unreadable. It is recorded in the skill's own "Traps that will mislead you", and
the interim answer is the manual `comm` comparison against each workspace root.

## Alternatives considered

**Fold this into ADR 0001.** The issue that raised it named `docs/decisions/0001`
— but that is `_agent-guidance`'s numbering, where 0001 is
`0001-skills-bootstrap-delivery-is-opt-in.md`. This repo's 0001 is *Consolidate
single-skill plugins into three bundles*, a different subject, and this folder's
README makes ADRs append-only: "once accepted, a decision is superseded by a new
ADR, never edited to say something different." Editing 0001 would have broken
that rule to land in the wrong file. New ADR, next number.

**Fix it in the repos instead.** Every candidate — a different command string, an
absolute hook path, a `settings.local.json`, a `matcher` change — fails for the
same reason: the file is never read. There is nothing to fix at that level, which
is precisely the fact worth recording.

**Leave it undocumented and let the diagnostic carry it.** Rejected because the
diagnostic can only report the *state*; it cannot say why `--add-dir` does not
help, which is the belief that has to be corrected before anyone stops trying to
fix it from inside a repo.

## References

- [#84](https://github.com/Adam-S-Daniel/agentskills/issues/84) — the
  investigation; measurements dated 2026-08-16.
- [#85](https://github.com/Adam-S-Daniel/agentskills/issues/85) — this record's
  §2, the two open questions (§3) and the two footnotes (§4), plus the
  `skills-doctor` changes that name the state.
- [#86](https://github.com/Adam-S-Daniel/agentskills/issues/86) — the install
  loop no longer deletes what it did not install, which is what lifted the
  ordering constraint on the user-scope direction.
- `plugins/adam/skills/skills-doctor/scripts/check_provenance.py` —
  `hook_wiring`, `user_settings_name`, `hook_findings`, `read_surface`,
  `discover_locks`.

## Note on the quotation

The block quote above is reproduced from #85 §2 verbatim, with one repair: the
issue's rendered text reads `/.claude/settings.local.json` and
`/.claude/settings.json`, having lost an angle-bracket placeholder to Markdown
rendering (the same stripping that left `$PROJECT_DIR/.claude/skills//SKILL.md`
in §4). Restored as `<cwd>` here, because the rendered form reads as an absolute
path at the filesystem root and would be actively wrong.
