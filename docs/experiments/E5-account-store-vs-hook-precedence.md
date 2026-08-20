# E5 — Account store vs SessionStart hook: drift census and precedence

**Verdict: the HOOK copy wins.** A skill delivered by both the claude.ai account
store and the `skills-bootstrap` SessionStart hook loads from
`~/.claude/skills/<name>/`, not from `~/.claude/skills/synced/<name>/`, and the
name is listed once rather than twice.

- Surface: Claude Code on the web, cloud sessions, CLI `2.1.237`, 2026-08-20
- Probes: `session_01VbH94u4RMiCZjSTiFxf1HV` (A), `session_01Ad3sfXBfFqdHU7gvpKoFUh` (B)
- Both on `Adam-S-Daniel/agentskills@claude/account-plugins-distribution-iybmw9`

Closes the question [E2](E2-cloud-session-result.md) left open ("which copy won
is **inconclusive** from the model's view alone").

## 1. Why a probe was needed at all — and why nothing had to be faked

The three names that collide under every current lock — `adam-writing-style`,
`finding-unknowns`, `writing-adrs` — are byte-identical to their registry
copies, so they carry no discriminator: whichever copy loads, the text is the
same. Marking them would have meant editing skill descriptions purely to run a
test.

That was unnecessary, because one skill has genuinely drifted. `sync-skills`'
account copy predates `2b97892` and is missing the whole `--report-issue`
section: **610 lines and 0 occurrences** of `--report-issue`, against the
registry's **737 lines and 6**. Making that collision reachable took a
config-only change — adding `adam-local` to this repo's `skills.lock`, pinned at
main's existing commit. No skill content was modified for the experiment.

## 2. Drift census (measured from a live session, not inferred)

The account store holds 19 skills: 10 `custom` (ours), 9 Anthropic-shipped. All
10 of ours are byte-identical to their registry copies after CRLF
normalisation, **except one**.

| Skill | Bundle | Account vs registry | `updatedAt` | last registry commit |
|---|---|---|---|---|
| `sync-skills` | `adam-local` | **DIFFERS — account behind** | 2026-08-18 | 2026-08-20 |
| `pdf-ocr-audit` | `adam-local` | same | 2026-04-05 | 2026-08-13 |
| `wj-next-break` | `adam-local` | same | 2026-05-11 | 2026-08-10 |
| other 7 | adam / adam-local / fastmail | same | — | — |

**The timestamp heuristic is not a drift signal on its own.** `pdf-ocr-audit`
and `wj-next-break` both carry an `updatedAt` months older than their last
registry commit and are nevertheless byte-identical — those commits moved the
skill into git (PR #62) without changing its bytes. Comparing `updatedAt`
against `git log` flags three skills here and two of them are false positives.
Only the content comparison is load-bearing; treat the timestamp as a
pre-filter, never a verdict.

## 3. The collision surface is keyed to the BUNDLE, not the repo

| Bundle | Names also in the account store | Shipped by a lock today |
|---|---|---|
| `adam` | 3 — `adam-writing-style`, `finding-unknowns`, `writing-adrs` | yes — all 10 lock-carrying repos |
| `adam-local` | 6 — `ocr-pdfs`, `pdf-ocr-audit`, `rename-pdfs`, `sync-cc-settings-between-wsl-and-windows`, `sync-skills`, `wj-next-break` | **no** |
| `fastmail` | 1 — `fastmail` | **no** |
| `cms-platform` | 0 | yes — 2 repos |

Every lock collides on the same 3 names. adamdaniel.ai and jodidaniel.com lock
22 skills each and add zero further collisions; `rss-inator` and `skills-evals`
carry no lock and collide on nothing.

**The one skill that has drifted sits in the one bundle nothing ships.** Not a
coincidence: `adam-local` is excluded from every lock because it is
machine-bound, so no digest ever re-verifies it, while the account store is the
only channel carrying it. The bundle with no lock coverage is the bundle that
rots.

## 4. The hook does not fire in a multi-repo session (#84, confirmed live)

The parent session had 12 repos attached. It carried no
`~/.claude/skills/.skills-bootstrap-installed.json` and none of the `adam`
bundle's skills, while the account store's 19 loaded normally. In a multi-repo
session the collision count is zero — and the account store is the *only*
channel.

## 5. The measurement

Both probes, independently, on a single-repo cloud session:

| Field | A | B |
|---|---|---|
| hook ran | YES | YES |
| `sync-skills` entries in the listing | 1 | 1 |
| account-only skills present (`docx`, `pdf`, `learn`, `theme-factory`) | YES | YES |
| hook-only skills present (`debug-github-workflows`, …) | YES | YES |
| **copy returned by the Skill tool** | **HOOK** | **HOOK** |

Both controls hold: the account store *and* the hook both loaded, so this is a
real collision and not one channel being absent.

B's evidence is the stronger form — **the Skill tool reported its base
directory**, `/root/.claude/skills/sync-skills`, so provenance is named rather
than inferred from content. A's is the content form: a 737-line body with 6
occurrences of `--report-issue`, matching the registry copy exactly (the account
copy has 610 lines and 0). A and B disagree on the line count (737 vs 723) —
a rendering/frontmatter-boundary difference, not a difference in which file was
read; both saw `--report-issue`, which the account copy does not contain at all.

## 6. Known precedence, and what is still unmeasured

- personal `~/.claude/skills/` **>** project `.claude/skills/` (E2 / C3)
- personal `~/.claude/skills/` **>** account `synced/` (this experiment)
- project `.claude/skills/` vs account `synced/` — **still unmeasured.**
  Nothing here settles it, and it is the case a repo-owned skill colliding with
  an account upload would hit in a multi-repo session where the hook never runs.

## 7. Consequences

- **A stale account copy is harmless wherever the hook runs.** Single-repo cloud
  sessions, CI runners: the lock-verified copy shadows it. The pinned,
  digest-checked channel is the one that wins, which is the right way round.
- **It is not harmless anywhere else, and "anywhere else" is most surfaces**:
  chat, Cowork, Claude in Chrome, mobile — and, per §4, any multi-repo Claude
  Code session. There the account copy is the only copy, so today
  `sync-skills` runs there without `--report-issue` existing.
- **The exposure is therefore the exact inverse of the delivery.** The channel
  that drifts serves the surfaces with no lock coverage, and the channel that
  cannot drift serves the surfaces that would have caught it. A green
  `skills: n/n — OK` in a hook session says nothing about what chat is running.
- **Fix for the live gap:** re-upload `sync-skills` from the laptop and confirm
  with `sync_skills.py --verify`. Nothing in CI can do it or assert it happened
  ([ADR 0002](../decisions/0002-limit-account-store-to-repo-independent-skills.md)).
- **`adam-local` is not in a lock and should not be** — it is machine-bound. The
  point is not to ship it, but that its account copies are unverified by
  construction, so a periodic content diff from a machine that has both is the
  only control available.

## 8. Reproducing

The probe lock (`adam` + `adam-local`, pinned at main's existing commit) was
never merged. It lives on the throwaway branch
[`claude/e5-probe-adam-local`](https://github.com/Adam-S-Daniel/agentskills/tree/claude/e5-probe-adam-local)
(commit `7e63423`), which is what the two probe sessions actually ran against
— `main` never carried `adam-local` in its lock, and should not.

To re-run: regenerate with `--bundles adam,adam-local`, push to a temporary
branch, open a single-repo cloud session against it, and ask the session to
invoke a skill whose two copies differ and report the Skill tool's base
directory. Keep it off any branch bound for `main`: the lock is a deliberate
declaration of what a repo installs, not a knob to borrow for a test.
