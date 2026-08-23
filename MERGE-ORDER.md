# Merge order for the two halves of #58 — DECIDED

**Order: `_agent-guidance` (the bumper) FIRST, then `agentskills` (the generator).**

This reverses my provisional call. I had recommended agentskills-first on the strength of a
reachability measurement: the new generator's refusals are unreachable for the locks that exist,
so nothing breaks. That is true and it is the wrong test. The verifier's argument is better:

- **Bumper-first is insured by code written for it.** Its DEGRADED mode was built for exactly
  this window. Measured on the realistic fleet: new bumper x old generator gives
  `0 merged, 3 proposed, 3 skipped, 0 failed`, exit 0 — the same proposals as old x old — and it
  announces the degradation loudly, with two up-front `::warning::` lines naming the two missing
  flags plus a per-repo warning on each federated consumer. Both-new then gives `4 proposed,
  0 failed`, the extra proposal being the federated advance the scoped flags unlock. Every
  intermediate state is one somebody wrote and tested a path for.
- **Generator-first is safe only by accident of today's data.** Its safety rests entirely on no
  live lock pinning at a branch name — a property of DATA, not of code. The generator's own
  docstring calls that shape "rare and not unreachable" and names a hand-edited lock as the route
  in. One hand-edit or one botched conflict resolution during the window and the nightly reds
  fleet-wide.

Generator-first fails in the GOOD way if it is ever forced — scenario C gives `BUMP_EXIT=1,
0 proposed, 2 failed` with the generator's diagnostic in the log, so it is loud red rather than a
silently-wrong PR. It is recoverable; it is simply uninsured.

**The cheap proof after the bumper lands:** let one nightly complete. The two `::warning::` lines
should appear, the summary should read `0 failed`, and the proposal count should match the
previous night.

## Reachability sweep — now COMPLETE

Eleven locks, all clean; seven repos with no lock, all confirmed absent rather than unverified.

| lock blob | repos | shape |
|---|---|---|
| `b1fa5623` | `_agent-guidance`, `agentskills-private`, `claude-memory-map`, `cms-platform`, `fastmail-actions`, `GHA-bench`, `wsl-automation`, `repo-settings` | `adam` only, no sources, all `sha256:`-prefixed |
| `040602df` | `adamdaniel.ai`, `jodidaniel.com` (byte-identical) | federated: one `cms-platform` source at a 40-hex sha, layout `skills` |
| `ab097175` | `scratch-claude-002` | older pin, BARE hex digests — the malformed shape. Old and new generator behave identically on it (`2 proposed, 0 failed` each). Repaired since, by the live bump that merged its PR #16. |

No lock: `skills-evals`, `scratch-claude-001`, `scratch-jules-001`, `squarespacetemp`, and — the
three the verifier could not see, which I closed by attaching them to the session — `4A`, `jc`,
`rss-inator`. All three read at repo level and have no `skills.lock`.

## Corrections to things I had believed

- **The bumper's population is not `skills_bootstrap.repos`.** It discovers with
  `gh repo list <owner> --no-archived --source` and filters on `.exclude` ALONE; it never reads
  that list. My scoping premise under-counted by roughly half, which is exactly why the three
  dormant private repos mattered.
- **"An uppercase 40-hex sha" is NOT one of the new refusals.** I put it in the briefing;
  `_COMMIT_SHA_RE` is deliberately case-INSENSITIVE and an uppercase pin passes both blockers.
  Measured, not read.
- **The salvaged numbers were measured on stale snapshots** — `mo/snap` predates
  `_reject_line_breaks`. The confirming agent re-archived from live refs (`mo2/`) and re-measured;
  the conclusions held, but do not reuse `mo/snap`.
- **`repos.yml` is substantially stale.** It says only `adamdaniel.ai` and `jodidaniel.com` carry
  a committed lock and the other eight have an open companion PR. Eleven carry one today; all
  eight companion PRs have landed. That file is what the next person scopes a blast radius from.

## A defect that arrives with the generator in EITHER order

`_addressing` now prints full addressing into its remediation line, and the bumper quotes that
verdict verbatim into every bump PR body. So each PR body now carries the bump runner's own
absolute path:

    python3 scripts/generate_skills_lock.py --repin --repo /home/runner/work/_agent-guidance/_agent-guidance/registries/agentskills -o skills.lock

Not a data-exposure incident — a hosted-runner workspace path is generic and carries no PII — but
it hands every consumer's reviewer a copy-pasteable command that cannot run on their machine, on
every bump PR, every night. It also falsifies a claim the bumper makes a few lines away: *"a PR
body carries no path from the machine that ran the bump — the quoted remediation line uses the
same device for `--repo`."* The placeholder device covers only lines the BUMPER composes, not the
generator verdict it quotes.

Fix on the bumper side: sanitise the quoted verdict with the placeholder device it already has,
and correct the now-false comment. Note the shape — round 7 fixed "the printed line is unrunnable
because it has no addressing" and produced "the printed line is unrunnable because it has the
WRONG machine's addressing." Same class, opposite swing.
