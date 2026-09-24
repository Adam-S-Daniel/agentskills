# Purpose — ci-job-performance

Maintenance context only; never loaded at inference.

## What it is for

CI performance work keeps getting redone from intuition — "cache the
browsers", "add more workers", "shard the tests" — each time re-measured from
scratch, and the intuitive fix is wrong often enough to be worth writing down.
This skill packages the measured answers so the next session starts from data
instead of guessing.

## The evidence it packages

- **Caching Playwright browsers nets out differently per browser.**
  `apt install-deps` dominates the install cost and can't be cached at all;
  `actions/cache` restoring `~/.cache/ms-playwright` costs ~5-6s, which beats
  chromium's ~10s download, roughly breaks even on webkit, and is slower than
  firefox's ~4s download
  ([cms-platform#461](https://github.com/Adam-S-Daniel/cms-platform/pull/461)).
- **A CPU-bound suite has a worker ceiling.** A 2112-test lint step moved only
  49s → 45-47s going from 2 to 4 workers, and 150% (6) was no better; the
  actual lever was reducing CPU work per file, since 5 of 148 files held ~45
  of ~72 CPU-s
  ([cms-platform#462](https://github.com/Adam-S-Daniel/cms-platform/issues/462)).
- **Sharding pays a fixed per-job cost that swallows small suites' wins**,
  and Playwright's `--shard` splits by test count, not duration, so shards
  land unbalanced (16s/28s/23s observed on one 3-way split).
- **`act` measures correctness, not speed** — container timings don't predict
  runner timings — and needs the same 7-day cooling-off on its binary and
  runner images that this account already applies to Actions pins
  ([fastmail-actions#27](https://github.com/Adam-S-Daniel/fastmail-actions/pull/27),
  [GHA-bench#83](https://github.com/Adam-S-Daniel/GHA-bench/pull/83)).

## Why `adam`

Cloud-safe: everything here is `gh`/API measurement and workflow-YAML
reasoning, plus one local-machine section (Docker-in-WSL for `act`) that
degrades gracefully to lint-only fallbacks when Docker isn't available —
nothing requires a signed-in browser or a machine-bound credential.

## Eval

No eval exists yet. This is a Class A/B candidate (procedural + measurement
literacy) per `skills-evals`' `DESIGN.md`; the first fixture would need a
synthetic slow-job log and workflow file rather than a real repo, since the
underlying evidence is measurement-heavy and repo-specific.

## Name

Checked against gitleaks' `generic-api-key` keyword list (`access auth api
credential creds key passwd password secret token`) before committing — it
contains none of them.
