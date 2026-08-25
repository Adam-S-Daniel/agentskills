# 0007. Install the union of every discovered lock in a multi-repo session

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Adam Daniel

## Context

[ADR 0005](0005-resolve-hooks-only-from-cwd-and-user-settings.md) settled that
in a session whose project directory is the PARENT of several repos, no repo's
`.claude/settings.json` is consulted, so no repo's SessionStart hook can fire.
It closed that question and deliberately left two open:

1. **Which lock wins in a multi-repo session?** "A union is not well-defined,"
   because two of the account's repos pin the same `(Adam-S-Daniel/agentskills,
   adam)` bundle at different refs. It ends: "The hook has no such option — it
   has to choose — so the question stays open for it."
2. **Should the `$SELF_ROOT/skills.lock` fallback exist?** Measured: an
   absolute-path invocation from a project dir with no lock yields
   `skills: 9/9 … OK` off the hook-shipping repo's own lock, silently
   converting a deliberate per-repo opt-in into a fleet default.

The two are sequenced, and that is why they are answered together. Fixing the
dispatch half of #84 needs a settings file at a level the chain reads, which
makes the hook run from a fixed absolute path in **every** session on the
machine — so question 2 has to be settled before that wiring is safe to place,
and question 1 has to be settled before the wiring is worth placing.

Separately, the hook read exactly ONE `skills.lock`. So even in the sessions
where it does fire, it could only ever deliver one repo's skills.

## Decision

**A session installs the union of every lock it discovers**, and the
`$SELF_ROOT` fallback applies only when `CLAUDE_PROJECT_DIR` is unset.

Discovery mirrors `skills-doctor`'s `discover_locks` — the project directory's
own `skills.lock` if it has one, otherwise every child repo's, one level only,
directories only, sorted — deliberately, so the hook and the diagnostic that
judges it cannot disagree about what a session contains. Children of the named
project directory, never siblings of anything: a sibling scan reaches whatever
happens to sit beside the project and lets a directory nobody attached to the
session decide what gets written into `$HOME`.

> **CORRECTION, 2026-08-25 — "child repo" is now enforced, not assumed.** As
> first implemented, discovery was `for child in "$PROJECT"/*/`, and an
> adversarial round showed that predicate does not mean what this paragraph
> says. `*/` matches symlinks-to-directories and `[ -d ]` follows them, so a
> child symlink reached a tree outside the project — the exact "sibling scan"
> the sentence above forbids — and a symlink pointing at `..` read a lock ABOVE
> the project directory. Both landed under a clean `skills: 1/1 … — OK`. Worse,
> ANY subdirectory carrying a lock contributed: `testdata/skills.lock`,
> `vendor/skills.lock`, and — dotglob being on to match the doctor —
> `.claude/skills.lock`. That falsified the promise the next section leans on,
> that "a lockless project is a project that did not opt in", which is in turn
> what this ADR cites as making a user-scope wiring safe.
>
> A contributing child must now be a real directory that is **not a symlink**, a
> **git repository root** (`.git`, file or directory, so worktrees count), and
> **not a duplicate by resolved lock path**. That single predicate closed six
> separate findings, which is why it replaced them rather than being patched
> around one at a time.
>
> One mechanical detail worth carrying forward, because it makes a guard that
> reads correctly do nothing: `*/` leaves a TRAILING SLASH, and `[ -L "dir/" ]`
> is FALSE even when `dir` is a symlink.

### Three destination cases, not one

`~/.claude/skills/` is FLAT: one name, one directory, one owner. **A destination
name installs iff every row naming it agrees on one normalised digest, and no
single lock contributed more than one row for it.**

- **Same name, same digest, different locks** — the ordinary fleet shape, since
  the locks largely declare the same `adam` bundle. Collapses to one install;
  identical digests mean identical bytes, so which source supplies them cannot
  matter. This is why the rule is a digest agreement and not a name count: the
  hook's existing duplicate guard counts destination NAMES over one lock's rows,
  and fed a union unchanged it would stamp every shared skill `dup`, `rm -rf` the
  destination and install nothing — `skills: 0/N` for the most common multi-repo
  session there is, reading as a lock authoring error rather than a hook defect.

- **Same name, different digests** — the state ADR 0005 measured as the fleet's
  actual one. **Neither installs**, and the verdict names the conflict and the
  locks that disagree. Picking a winner would serve bytes under a name whose own
  lock pins different bytes, breaking the integrity guarantee the lock exists
  for; ADR 0005 refused to invent a tiebreak precisely because neither lock is
  more authoritative. Failing closed is per NAME, so when two refs differ only in
  the skills that actually changed, every unchanged skill still unions cleanly —
  and it cannot regress anything, because today that session delivers nothing.

- **Two rows for one name inside a single lock** — unchanged: `dup`, regardless
  of digest. An authoring error the generator refuses to write and ADR 0001
  forbids.

### One bad lock degrades only its own skills

A lock that fails validation is recorded with its reason and skipped; the rest
install. This is the posture the hook already takes for an unreachable registry.

It is not polish. Discovery means the number of locks read is no longer under the
adopting repo's control, so without it any one of fourteen attached repos could
deny delivery to every other — a fragility this change would itself introduce.
The fail-closed refusals keep their teeth, scoped to the offending lock: one
naming `synced` contributes no rows and no claims, so that name never reaches
either destructive consumer.

### Sources are fetched once per `(url, ref)`

Fourteen repos pinning one registry are one clone. `MAX_SOURCES` stays the
PER-LOCK federation cap, byte-identical to the generator's copy; `MAX_FETCHES` is
a separate whole-run bound, because eleven locks could otherwise present
11 × (1 + 8) = 99 fetches under one 60-second `FETCH_BUDGET` inside a 90-second
hook timeout — an arithmetic the existing constants were never calibrated for.

## Consequences

**The union dissolves a documented residual, within a session.** The prune is
scoped per `(registry, bundle)`, and the hook's own comment records that "two
repos declaring the SAME (registry, bundle) at different pinned refs with
different skill sets still contend, each run removing what the other installed."
One run that sees every lock reaps nothing another lock still wants.

> **CORRECTION, 2026-08-25.** As first written this section said the union
> "dissolves that contention outright" within a session, and that the
> cross-session residual was "the same mechanism, the same bound and the same
> names as today's churn". An adversarial round falsified both, with four
> isolating controls. Inside ONE session a sibling declaring the identical
> `(registry, bundle)` supplied the claim a starved, rejected or capped lock had
> withheld, and the planner then reaped that lock's own skills — no second
> session needed, and reproducible with seventeen ordinary repos pinning one
> registry at seventeen refs, with no hostility at all. The hook asserted the
> opposite in two comments; those and this paragraph were wrong together.
>
> Fixed by extending the rule the file already applied to an incomplete child
> enumeration: **a run that cannot fully account for the session claims
> authority over nothing.** Rejected locks, cap-dropped locks and fetch-starved
> locks now empty `claims.nul` and prune nothing, saying so.
>
> That is NARROWER than the per-entry `lock` provenance field this section
> originally deferred, and it is the better fix — it reuses a principle the file
> already states rather than adding a record-schema change to its most
> destructive path. The provenance field is not owed.
>
> The cross-session residual itself is unchanged and still stands as described
> above.

**`purge_locked_destinations` now has a fleet-wide blast radius.** Six bail-outs
call it and it removes every destination the locks name, ungated by provenance —
deliberately, per its own comment, with tests asserting exactly that. With N
locks that is N repos' locked names. Per-lock degradation narrows it usefully (a
rejected lock contributes no names), but the trade its comment declined to
re-argue is now worth re-arguing. **Next question, not settled here.**

Two facts about that radius were established by measurement afterwards, and both
belong here rather than in a reader's imagination. A sibling lock **chooses the
names**, and can **induce the bail-out itself** by declaring enough unreachable
sources to exhaust the whole-run fetch cap. Against that: the radius is confined
to `$DEST`; names are charset-bounded with no traversal; the install record is
out of reach on its leading dot; `$DEST` in the sessions where this hook runs at
all is a throwaway container's; and `~/.claude/skills/synced` — the one target
whose loss is unrecoverable — is NOT reachable, which was attacked with three
hostile locks and held, with a negative control confirming the guard rather than
the harness was what stopped it. The author's chosen mitigation was to fail
closed on the unrecoverable target and state the rest, and that judgement
survives the round.

**The no-lock verdict stays `DEGRADED`.** Under a user-scope wiring it becomes
the ordinary state of every non-adopting repo on the machine, and a permanent
banner there is noise that trains people to stop reading the verdict. Softening
it is a separate argument about a separate audience, and it would weaken the one
signal that tells an ADOPTING repo its delivery is broken. `skills-doctor`
distinguishes the two. **Next question, not settled here.**

**A sibling lock can DENY delivery of a name**, by naming it with a digest that
disagrees with everyone else's. That follows directly from failing closed, and is
taken knowingly: the alternative silently serves bytes under a name whose own
lock pins different bytes. The conflict and the disagreeing locks are named in
the verdict, and the denial is per name rather than per session.

**A sibling repo's lock now contributes**, which is a real widening of the trust
surface — the hook reads a lock from every directory one level under the project
dir. It is accepted for a reason that has to be stated rather than assumed:
`--add-dir` ALREADY loads an attached directory's `.claude/skills/`, commands and
agents (documented, with live reload; settings and hooks are the documented
exceptions, which is what ADR 0005 rests on). A repo attached to a session can
therefore already put instruction text in front of the model. Installing its
pinned, digest-verified locked skills is not a new class of exposure, and it is
strictly more accountable, because every contributing lock and registry is named
in the verdict.

Note that E4's containment argument for its own stated residual — "it needs write
access to `skills.lock`", meaning THIS project's lock — no longer covers this
shape. Write access to ANY attached repo's lock is now enough to route skills
into the session: a strictly larger and less obviously trusted set of writers.

**No CI gate can verify a union.** `--check`, `--check-current` and
`--check-format` each take one `-o` and read one file, and a generator run in
repo A cannot know which repos will share a session with it — a cross-lock rule
there would be non-deterministic and would fail A's CI for B's content. The
union's correctness is enforceable only in the hook and only at session start.

**The doctor had to follow.** `skills-doctor` models the hook's decisions, and
its `stale` family judged each name against ONE lock — so under a union it
promised removals the hook will not perform. Its per-lock REPORTING posture
("names no winner") is still correct and is retained: the doctor never installs,
so it can report every lock's reading; the hook has to choose, and now does. But
that posture is no longer justified by the question being open, and its
docstrings said so.

**This does not, by itself, make the hook fire.** ADR 0005's finding is about
which settings files Claude Code reads, and lock discovery is downstream of it.
Every claim of the form "the hook installs nothing in a multi-repo session" —
E5 §4, §6 and §7, the README's account-store bullet, `skills-doctor`'s SKILL.md —
stays TRUE until a `cwd`- or user-level settings file is placed, which only the
environment can do. See [`docs/multi-repo-delivery.md`](../multi-repo-delivery.md)
for the wiring and why the committed
`bash "$CLAUDE_PROJECT_DIR/.claude/hooks/skills-bootstrap.sh"` cannot be reused
for it.

### One correction to ADR 0005, recorded here rather than there

ADR 0005 says the `--add-dir` roots' settings files "*are* stat'd during startup
— enumeration is not resolution". Re-measured on Claude Code 2.1.241 with
`strace`: they are not merely stat'd. `<added>/.claude/settings.json` is opened
`O_RDONLY` and read repeatedly, and `settings.local.json` is probed too. Claude
Code fully reads them and then declines to apply them.

The functional conclusion is unchanged and was re-confirmed with controls: a
byte-identical settings file fires its hooks as `<cwd>` and fires nothing as an
`--add-dir` root, across three event types, with an `env` key equally ignored,
while `--add-dir` was demonstrably honoured in the same runs. Only the sentence
about the syscalls was wrong, and ADRs are append-only, so it is corrected here.

## Alternatives considered

**Pick a winner by ref recency** — ask git which ref is newer, so the order comes
from the repository's own history rather than from us. Rejected on mechanics and
on principle: a `--depth 1` fetch carries no history for `merge-base`, committer
timestamps are not a trustworthy order, and inventing an order is exactly what
ADR 0005 declined to do.

**Pick the first lock in discovery order.** Rejected: sort order is not
authority. It would make which bytes a session gets depend on what the repos are
named.

**Refuse the whole run on any cross-lock conflict.** Rejected: one diverged pin
would cost every repo every skill. Per-name failure is bounded to the genuinely
ambiguous names and leaves the rest of the union intact.

**Merge the locks into one expectation before reading them.** Rejected: it
destroys per-lock routing — two locks each legitimately claiming `adam` at
different registries would trip the "bundle is claimed by two sources" refusal,
so a union of valid inputs would be invalid — and it throws away the attribution
the verdict needs to name which lock said what.

**Run the existing single-lock reader once per lock and concatenate its
streams.** Rejected: each row carries an index into a global source array, so
every row from the second lock onward misroutes, and the only tripwire is a range
check that reports the failure as "not installed" with the wrong cause. It would
also reproduce the prune's mutual-reaping residual N times inside one session,
which is strictly worse than today.

**Solve it in the environment instead** — a setup script that copies one repo's
skills into `~/.claude/skills`. Rejected: unpinned, unverified delivery is the
whole risk the lock exists to close.

**Delete the `$SELF_ROOT` fallback entirely.** Rejected: a hand run with no
`CLAUDE_PROJECT_DIR` could then find no lock at all, and that is the documented
way to run the hook manually — it is how #84 was diagnosed.

**Gate the fallback behind a new environment variable.** Rejected as a knob for a
condition already exactly expressed by "the session named no project directory".

## What the adversarial rounds did NOT reach

Two rounds, 26 verified findings, each graded by a skeptic instructed to refute
it. Six findings were fixed, two were REFUTED as stated limits re-reported, and
one — the digest not covering symlinks — predates this work and is
[#132](https://github.com/Adam-S-Daniel/agentskills/issues/132).

Recorded so the coverage is not read as wider than it was:

- **No Windows/Git Bash or macOS bash 3.2 run.** Everything was Linux. The MSYS
  mount table, the parallel-array paths and Win32 trailing-dot stripping are
  reasoned about, not measured — and three Windows regressions on this branch
  say that gap is not theoretical.
- **The case-fold itself was never measured.** No case-insensitive filesystem
  could be built in the container, so `$DEST/Synced` resolving to `$DEST/synced`
  on APFS and NTFS is asserted from platform semantics. The fix was made anyway;
  the exposure was measured on the purge path, which takes the name verbatim.
- **Four framing-error purge bail-outs were reasoned about, not induced.** No
  route was found from lock CONTENT to a python↔bash record-count desync, so
  whether a hostile lock can reach the post-install purge — the one that
  discards a run's own successful installs — is unestablished.
- **Concurrency was not probed at all**: two sessions running the hook against
  one `$HOME`. The record is staged and `os.replace`d, but the install loop and
  the prune are not serialised.
- **`$DEST` itself as a symlink** was not tested, nor a symlink swapped in
  between `may_replace` and the `rm`.
- One lens was lost to a StructuredOutput retry cap and re-run separately; had
  it not been re-run, four of the highest-severity findings would not exist.

## How to verify

- `test_two_sibling_repos_with_disjoint_skills_both_install`
- `test_two_sibling_repos_pinning_one_registry_share_a_single_fetch` — asserts the
  FETCH count, not only the install count: installing 2/2 after cloning one
  registry fourteen times is correct and unaffordable, and nothing else notices.
- `test_two_locks_naming_one_skill_at_different_digests_install_neither`
- `test_a_project_with_its_own_lock_ignores_its_child_repos_locks`
- `test_one_malformed_sibling_lock_degrades_only_its_own_skills`
- `test_a_sibling_lock_naming_synced_contributes_nothing_and_spares_the_store`
- `test_the_hooks_own_repo_is_the_fallback_only_when_no_project_dir_is_named`
- `test_a_narrower_session_leaves_the_other_repos_skills_alone`
- `test_a_discovery_scan_that_cannot_be_completed_installs_but_prunes_nothing`

## References

- [#84](https://github.com/Adam-S-Daniel/agentskills/issues/84) — the
  investigation; measurements dated 2026-08-16.
- ADR 0005 — the two open questions this answers, and the constraint it records.
- ADR 0001 — skill directory basenames must stay unique.
- [E4](../experiments/E4-federated-bundle-delivery.md) — "a lock is a trust
  boundary, and the hook is the side that consumes locks authored elsewhere", and
  the four adversarial rounds on this file.
