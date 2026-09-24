# Purpose — debug-github-workflows

Maintenance context only; never loaded at inference.

## What it is for

A workflow run showing green in GitHub's UI is not proof that it did what it
was supposed to — process substitution swallows `set -e` failures, a script
that exits 0 on "nothing found" masks a broken discovery step, and a status
check's own listing can lie about what recently ran. This skill collects the
false-success and false-absence patterns worth checking before trusting a
run's reported outcome.

## The incidents it packages

- **Process substitution under `set -euo pipefail` does not propagate
  errors** — `mapfile -t ARR < <(cmd_that_fails)` leaves `ARR` empty and the
  script keeps going, which reads as "found nothing" rather than "the command
  failed".
- **Git identity is missing on CI runners** — `git commit` outside the
  checkout fails with exit 128 unless `user.name`/`user.email` are set first.
- **A workflow can exist only on a feature branch** — checking `main`'s
  `.github/workflows/` alone misses it.
- **2026-09 — a runs listing returned a stale snapshot.** The per-workflow
  `/actions/workflows/<id>/runs` endpoint intermittently answered with a
  ~3-week-old snapshot while the repo-wide `/actions/runs` listing, queried
  seconds later, already had the recent runs — not reproducible afterward
  (15/15 calls came back current). It produced false "no recent success"
  alerts
  ([jodidaniel.com#264](https://github.com/jodidaniel/jodidaniel.com/issues/264),
  fixed in
  [cms-platform#459](https://github.com/Adam-S-Daniel/cms-platform/pull/459)).
  Added as its own section rather than folded into an existing one, because
  the failure mode is the listing itself, not a script run inside a job.

## Why `adam`

Cloud-safe: every technique here is `gh`/API log-reading and reasoning about
workflow YAML, no machine-bound resource involved.

## Eval

No eval exists yet.

## Name

Unchanged by this edit; not re-checked against the gitleaks keyword list
since the basename was not touched.
