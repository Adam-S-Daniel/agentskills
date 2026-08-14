# Experiment E4 — federating a bundle from another repo, and what the hook survives

**Question.** Can one marketplace publish a bundle that lives in a *different* repo, so
cms-platform stops rsyncing its skills into consumers — and is the resulting
multi-source bootstrap hook safe enough to run, unprompted, at every session start?

Context: `#54` §4 chose federation over the vendored mirror on the strength of C4,
which showed a marketplace entry can point at another repo's *subdirectory*. This
records what was measured while actually building it. Run 2026-08-14, Claude Code
`2.1.231`.

## Part 1 — the packaging questions, settled by running them

### F1 — a plugin root can be a repo root, so cms-platform needs no restructuring

C4 federated `plugins/adam` — a subdirectory laid out as a plugin. cms-platform's
skills live at `skills/<name>/` at its **repo root**, and a prior analysis flagged
"whether that shape installs" as the single unverified assumption under its
recommendation to restructure.

It installs. A fixture shaped exactly like cms-platform's root — `.claude-plugin/plugin.json`
beside `skills/`, plus unrelated sibling dirs (`theme/`, `e2e/`, `scripts/`) —
published through a marketplace and installed:

```
√ Successfully installed plugin: cms-platform@fedlocal (scope: user)

$ claude plugin details cms-platform
  Component inventory
    Skills (1)  aws-bootstrap
```

The whole tree lands in the cache (`~/.claude/plugins/cache/<mkt>/<plugin>/<version>/`),
including the sibling dirs — 5.5 MB for cms-platform. Worth knowing, not worth
restructuring a repo over.

**So the restructure was avoidable**, and avoiding it dodged a landmine: the census
resolves that registry by a `layout:` glob, and a moved `skills/` with an unmoved
glob would have scanned **zero** skills while still resolving — coverage of 15 skills
vanishing with no finding, because `skills_scanned` has no floor to fail against.

### F2 — `claude plugin validate --strict` accepts a federated entry without resolving it

```
plugins[1] = {"name":"cms-platform","source":{"source":"github","repo":"Adam-S-Daniel/cms-platform"}}
→ √ Validation passed          (warnings were about plugins[0], the LOCAL entry)
```

No warning, no error, no fetch. The entry is not resolved at all, so validation
asserts **nothing** about it — not that the repo exists, not that it is a plugin
root. Whatever guarantee we want about a federated bundle, our own checks must
provide; and `check_agent_plugins.py` must say out loud that the remote is validated
elsewhere rather than skipping it silently.

This is E3's lesson again, one layer out: a green check that never opens the thing
it appears to be checking.

### F3 — a marketplace `source` silently accepts keys that may do nothing

`ref`, `commit`, `version` and `branch` were each added to a federated `source`
object. All four validate:

```
source key "ref":"v0.1.82"      → √ Validation passed
source key "commit":"abc123"    → √ Validation passed
source key "version":"v0.1.82"  → √ Validation passed
source key "branch":"main"      → √ Validation passed
```

Validation passing tells us the key is *tolerated*, not that it is *honoured* — and
there is no way to tell those apart from here. So none was added: a key that reads
as a pin while nothing consumes it is worse than no key, because it is exactly what
a reader checks when asking "is this pinned?". Pinning stays the lock's job, which
is also how the `adam` bundle has always worked. `check_consistency.py` now rejects
any key outside the honoured set, so the next person cannot add one by copy-paste.

### F4 — a sparse, blobless fetch of the federated repo is SLOWER than a plain shallow one

The hook fetches every source at session start, so the cost is paid on every
ephemeral session. The obvious optimisation is to fetch only `skills/`:

| method | wall clock | on disk |
|---|---|---|
| `fetch --depth 1` (whole repo) | **1.53 s** | 7.2 MB |
| `--filter=blob:none` + sparse-checkout of `skills/` | **2.22 s** | 600 KB |

12× smaller and **45% slower** — the extra round trip to backfill the filtered blobs
costs more than the bytes saved. So the hook does the simple thing. Recorded because
the optimisation is tempting on its face and someone will propose it again.

## Part 2 — the hook, attacked four times

The multi-source change was reviewed by independent adversarial agents in four
rounds. Two rounds found defects **introduced by the previous round's fix**, which is
the part worth remembering.

| Round | Found | Status |
|---|---|---|
| 1 | TSV framing injection → arbitrary git remote; `<bundle>/..` path escape → overwrote `~/.claude/settings.json` with a `SessionStart` hook | both proven RCE, both fixed |
| 2 | digest-mismatch content left installed; skip paths left stale content live; **CI had never shellchecked the hook** | fixed — and the round's own fix introduced a new RCE |
| 3 | the round-2 fix let a *federated* source supply and execute the digest implementation, and bypass sha256 under a clean `1/1 OK` | class removed, not patched |
| 4 | `python3` ran with the project dir on `sys.path`, so a planted `hashlib.py` forged the digest | fixed with `-I`; verdict **SHIPPABLE** |

### The two findings that generalise

**A fix can be worse than the bug it fixes.** Round 2 made the digest verifier fall
back to "the first successfully-fetched source" so an unreachable primary would not
zero the run. That let a federated source — a different owner, possibly shipping zero
locked skills — supply and run `generate_skills_lock.py`; reading the lock through the
inherited `CLAUDE_PROJECT_DIR`, it echoed back each expected sha256 so tampered content
installed under `skills: 1/1 … — OK`. The trigger is a primary fetch failure, which is
inducible by network position.

The repair was not a better fallback. It was **deleting the capability**: the digest is
now computed inline in the hook, and nothing fetched is executed. That reverses an
explicit earlier decision — "a second, independently written copy of a hash algorithm is
exactly the class of bug that produces an expected number nobody can explain" — which was
sound reasoning, so the second copy is bound by a test hashing one fixture with both
implementations. Verified identical across 18 hard cases (nested dirs, empty file, CRLF
bytes, UTF-8 filenames, no trailing newline, NUL in contents, 40-deep paths, five symlink
shapes), including matching *error* behaviour on a surrogate-escape filename.

**"Nothing fetched is executed" is empty without `-I`.** Round 3 removed the fetched-code
path; round 4 showed all three `python3` invocations still ran with `sys.path[0] = ''`,
i.e. the session's project directory. A malicious project shipping `skills.lock` *and* a
`hashlib.py` whose `sha256().hexdigest()` returns `'0'*64` produced `skills: 1/1 … — OK`
with attacker content installed. A `re.py` shadow bypassed lock validation entirely.

`-I` (isolated mode; Python ≥ 3.4) closes it. The measurement that makes this trustworthy
is the **negative control**: the same hook with `-I` stripped from one call site *does*
install `EVIL BODY` under a clean verdict. Without that control, "the exploit stopped
working" is indistinguishable from "the harness stopped working" — the same trap that cost
E2 two false negatives.

### Mutation testing found what test count hid

The suite grew 297 → 371 tests across the hardening rounds. A 35-mutant sweep on a clean
baseline still found **9 survivors**, 5 behaviourally observable — including four of the
six `purge_locked_destinations` call sites, where deleting the purge reproduces the exact
incident the hook's own comment says it fixes, with the whole suite green.

A fixer's own sweep had reported 19 mutants and 1 survivor. The gap between those two
numbers is the point: **test count is not coverage**, and a sweep is only as honest as its
mutant list. This is the same shape as the 157 tests that once executed nowhere.

### One residual, stated rather than papered over

A federated source that *explicitly* claims a bundle the primary does not list still routes
those skills to itself. That is structurally identical to the federation the file exists to
support, so no routing rule separates them. It needs write access to `skills.lock` — which
already permits rewriting `registry` outright for the same effect — and the verdict names
the registry each skill came from either way. Going further would mean rejecting a claim
whose directory also exists in the primary, which would hard-fail an honest bundle
migration.

Two further defects were **deferred, not fixed**: orphaned skills persisting when a skill
leaves the lock (#71), and `BASH_ENV` breaking the always-exit-0 contract (#72). Both
pre-date this change and neither is amplified by it. #72 is really a documentation defect —
the header states an *absolute* guarantee that is not absolute.

## What this changes

The mirror's replacement is measured, not assumed: one registry can publish a bundle that
lives in another repo, a consumer's `skills.lock` can pin and verify sources from more than
one owner, and the hook that installs them survives four rounds of directed attack.

The limit worth carrying forward is that **a lock is a trust boundary, and the hook is the
side that consumes locks authored elsewhere** — so it must be the stricter of the two
implementations, not the more permissive one. Three separate defects here were the hook
accepting something the generator refuses.
