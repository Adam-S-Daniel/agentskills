---
name: ci-job-performance
description: Speed up a slow GitHub Actions job or test step using measurements, not guesses. Trigger when CI is slow; when asked to speed up a workflow, job, or step; to cache a tool or browser download in CI; to parallelize or shard tests; to add more workers; when asked why a step takes a minute (or any specific duration); or when evaluating nektos/act as a local CI inner loop. Covers reading job/step timings and log timestamps to tell CPU-bound from waiting, the actions/cache economics for Playwright browser installs, Playwright worker-count tuning, sharding tradeoffs against the fixed per-job cost, keeping a required check green across shards, and running act locally (Docker-in-WSL, binary and image pinning).
---

# CI Job Performance

Every number below is a measurement, not a rule of thumb — CI timing is noisy
and workload-specific, so re-measure before applying a fix here to a
different repo. Figures cited without another source were measured
2026-09-24 across `cms-platform`, `adamdaniel.ai` and `jodidaniel.com`.

## 1. Measure first

Don't guess which step is slow or why. Get real numbers before touching a
workflow.

**Step durations**, across a whole run:

```bash
gh run view <run-id> --json jobs --jq \
  '.jobs[] | .steps[] | "\(.name): \((.completedAt | fromdate) - (.startedAt | fromdate))s"'
```

**Inside one step**, pull the raw log and diff timestamps between marker
lines — never pipe the log into `grep -q` (the 64 KiB pipe-buffer race in
`AGENTS.md`'s "watch finished" section applies here too):

```bash
gh api repos/<owner>/<repo>/actions/jobs/<job-id>/logs > job.log
```

Then grep for markers the tool itself prints — Playwright's `Downloading …` /
`… downloaded to …` bracket a browser install, `Running N tests using K
workers` marks the start of the run — and subtract their timestamps. A
per-second histogram of completion-line timestamps finds stalls that an
average would hide.

**CPU-bound vs waiting**: run the same step locally under
`/usr/bin/time -f "%U %S %e"`. When `%U + %S` (user + sys CPU seconds) is much
larger than `%e` (wall clock) times the worker count, the step is CPU-bound —
more workers or a faster machine helps. When it's close, the step is waiting
on I/O (network, disk, another process) and workers won't fix it.

**Per-file CPU profile**: run each spec file alone at 1 worker and subtract
Playwright's fixed startup cost (~1 CPU-s) to get that file's real share.

**Report ranges with sample counts, not just an average.** Two or three CI
runs of the identical variant spread ±5-10s on their own — a "5s win" from a
single before/after pair is inside that noise. Take at least 3 samples per
variant before claiming a delta.

## 2. Caching browser downloads: the economics

A Playwright browser install is two separate costs: `apt install-deps` (OS
libraries, **not cacheable by `actions/cache`** — 17-26s, 60-70% of the total
install time) plus the browser binary download itself (chromium ~10s, firefox
~4s, webkit ~6s).

`actions/cache` restoring `~/.cache/ms-playwright` on a hit costs ~5-6s. Net
effect:

- chromium: saves ~4-5s per job (download avoided, restore cheaper than it)
- webkit: nets out to ~0
- firefox: **makes the job slower** — the 4s download is cheaper than the 5-6s
  restore

So caching is worth it only when the job has no `apt install-deps` step (a
pre-built image, or a step that already ran it) and runs often enough for the
restore cost to amortize. Key the cache on the lockfile that pins
`@playwright/test`, since that's what fixes the exact browser build:

```yaml
key: playwright-browsers-${{ hashFiles('<test-dir>/package-lock.json') }}
```

A miss falls back to the normal install — always keep that fallback step
un-conditioned on the cache hit.

## 3. Workers: CPU-bound suites don't get faster from more of them

Playwright's worker default is 50% of available cores — 2 workers on the
4-vCPU `ubuntu-latest` public-repo runner; private repos get only 2 vCPU
total, so the default is lower there too.

A pure-Node suite that spawns child processes (node CLIs, `gitleaks`) is
CPU-bound, and adding workers has a ceiling: going 2 → 4 workers moved a
2112-test lint step only 49s → 45-47s, and 150% (6 workers) was no better.
When a step is CPU-bound, the lever that actually moves it is **less CPU
work**, not more workers — spawn a subprocess once and assert many things
against its output, or call a library in-process instead of shelling out to
its CLI per test. Example: 5 of 148 files accounted for ~45 of ~72 total CPU
seconds in one such suite
([cms-platform#462](https://github.com/Adam-S-Daniel/cms-platform/issues/462)).

## 4. Sharding across jobs

Each shard re-pays the job's fixed cost — checkout, `setup-node`, `npm ci`,
cache restore — roughly 15-20s before a single test runs. GitHub can also
stagger runner allocation across a matrix (one observed case: ~36s of extra
wait), which can make a sharded run finish *slower* than an unsharded one.

Playwright's `--shard=i/N` splits by test **count**, not measured duration, so
shards come out unbalanced in wall-clock terms — one measured 3-way split ran
16s / 28s / 23s. On a ~42s total suite, 3 shards averaged no better than a
single job at 4 workers.

Shard only when the unsharded test time is several times the fixed per-job
cost — sharding a fast suite adds overhead without buying anything back.

## 5. Keeping a required check green across shards

If the workflow's required status check name must survive sharding:

- Put the required context on a **gate job** that depends on every shard
  (`needs: [shards]`, `if: always()`), and have it fail unless every needed
  shard's result is `success`.
- Give the gate job **no `concurrency` group and no `timeout-minutes`** — see
  `AGENTS.md`'s note on a required check with no concurrency group when its
  job can fire twice on one head SHA.
- Run shards with `fail-fast: false`, so one red shard doesn't hide the
  others' results from the gate.
- Bind `${{ matrix.* }}` values to a job-level `env:` var and read `"$VAR"`
  inside `run:` — never interpolate an expression directly into a run body;
  workflow-injection lints flag it, and it's the same class of risk as
  `${{ inputs.* }}` / `${{ github.event.* }}` in a `run:` block.
- **Prove the gate can actually fail** before trusting it: push a temporary
  commit that fails one shard on purpose, confirm the required context reports
  `FAILURE` (not `cancelled` or stuck `pending`), then revert with a **new**
  commit — never amend or force-push the proof away.

## 6. nektos/act as an inner loop

`act` is for **correctness** — does the workflow's logic run at all, do steps
wire together right — not for timing. Container step durations under act do
not predict runner durations; don't use it to validate a performance change.

It needs a working Docker. On WSL, a Docker Desktop WSL integration that
isn't enabled shows up as `/usr/bin/docker` symlinked to an unmounted
`/mnt/wsl/docker-desktop/...` path, and act fails with `failed to connect to
the docker API at unix:///var/run/docker.sock`. Fix: install Docker Desktop,
then Settings → Resources → WSL integration → enable the distro, and confirm
with `docker version` showing a Server section.

`actions/upload-artifact` fails under act with `Unable to get the
ACTIONS_RUNTIME_TOKEN env variable` unless you pass
`--artifact-server-path <dir>` — that's expected behavior, not a regression
act introduced.

Without a working Docker, fall back to: `actionlint`, the repo's own AST-based
workflow lints, a local `--list --shard=i/N` partition check (proves the shard
split without running anything), and a local dry-run of the gate job's
pass/fail logic against synthetic shard results.

## 7. Cooling-off applies to local tooling too

The same 7-day adoption delay this account applies to GitHub Actions pins
(see `AGENTS.md`) applies to `act` and its runner images:

- Pin the `act` binary itself to the newest release that is at least 7 days
  old, verified against that release's own `checksums.txt`.
- Pin runner images by `tag@digest`, using a **dated** tag at least 7 days
  old. `catthehacker` publishes dated tags — `act-latest-20260815`,
  `pwsh-latest-20260815` — on Docker Hub; `ghcr.io` carries the same digests
  but currently has no dated `pwsh` tags. Resolve a digest from
  `https://hub.docker.com/v2/repositories/catthehacker/ubuntu/tags/<tag>`.
- **Never use a `-latest` tag.** These images' build scripts fetch "latest"
  upstream releases (e.g. PowerShell via its `releases/latest` API) at *build*
  time, so a floating tag silently adopts a brand-new upstream release the day
  it lands. What matters is the release's age at the moment **you** adopt it,
  not the image's build date.
- Inside a Dockerfile that layers on these images, pin apt package versions
  (e.g. `powershell=7.6.6-1.deb`) and PowerShell modules
  (`Install-Module -RequiredVersion ...`), and make sure the apt repo you add
  matches the base image's actual Ubuntu release — a 22.04 apt repo on a
  24.04 base can "work" by accident and break later. A floating
  `-MinimumVersion 5.0` on a module install silently jumped to a new major
  version (Pester 6.x) once these constraints weren't in place.

## Sources

Evidence for the figures above:
[cms-platform#461](https://github.com/Adam-S-Daniel/cms-platform/pull/461),
[cms-platform#462](https://github.com/Adam-S-Daniel/cms-platform/issues/462),
[fastmail-actions#27](https://github.com/Adam-S-Daniel/fastmail-actions/pull/27),
[GHA-bench#83](https://github.com/Adam-S-Daniel/GHA-bench/pull/83).
