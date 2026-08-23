# Merge order for the two halves of #58 — salvaged measurement

The generator half (agentskills `ag58-generator`) and the bumper half
(`_agent-guidance` `claude/…-r31h75`) cannot land atomically. The bumper's DEGRADED mode was
designed for _agent-guidance-first (new bumper, old generator). The other direction had never
been tested. An agent measured it and was killed mid-run by the container reap; its harness and
outputs survive under `scratchpad/mo/`. A confirmation workflow is re-running it.

## Direction A — agentskills first: NEW generator under the OLD bumper

| scenario | old generator | new generator | verdict |
|---|---|---|---|
| realistic fleet | — | **0 merged, 3 proposed, 3 skipped, 0 FAILED** | clean |
| a bundle that lost every skill (`S`) | 2 failed | 2 failed | same outcome, better message |
| emptied registry tree (`E`) | 1 failed, **traceback** | 1 failed, clear message | improvement |
| branch-name refs (`C`) | **2 PRs created** | **2 FAILED** | **the one behaviour change** |
| digest-shape only (`F`) | — | PR created | clean |

## Direction B — _agent-guidance first: NEW bumper under the OLD generator

Degrades with a warning, `0 failed`. Both-new: `4 proposed, 0 failed`.

## Is the one behaviour change reachable?

Only by a lock pinning its primary or a source at a **branch name**. Both real federated consumer
locks were checked by hand and are byte-identical and clean:

- `Adam-S-Daniel/adamdaniel.ai` and `jodidaniel/jodidaniel.com` — primary `ref` and the single
  `Adam-S-Daniel/cms-platform` source `ref` are both 40-hex lowercase shas; one source; no
  self-federation; no duplicate registry; every digest carries the `sha256:` prefix.

The remaining eight `skills_bootstrap.repos` are being checked by the confirmation workflow.

**Provisional recommendation: agentskills first.** The generator's new refusals are unreachable
for the consumers that exist, and the bumper's degraded mode then never has to fire at all.

## A live sighting of what the generator half fixes

`jodidaniel/scratch-claude-002#16` (opened 08-20, merged 08-23) carried this in its body, quoted
from the generator's own `--check-format` remediation:

    python3 scripts/generate_skills_lock.py --repin --ref 94cdcc81… --repo  -o

`--repo` and `-o` are EMPTY, because the fleet bumper's pre-clone `--check-format` call passes
neither. A published PR told a reader for three days to run a command with blank flag values.
That is the `_addressing` defect ag58 round 7 was sent to close — verify it explicitly, because
that round's mandate was written around a missing `--source-repo`, not around empty `--repo`/`-o`.
