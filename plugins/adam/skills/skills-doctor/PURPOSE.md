# Purpose — skills-doctor

Maintenance context only; never loaded at inference.

## What it is for

Four delivery channels put skills in front of a session — the marketplace
bundles, the bootstrap hook's `skills.lock` install, a repo's own
`.claude/skills/`, and the claude.ai account store — and none of them tells the
session which one it read. `skills-doctor` answers "what is actually loaded
here, and where did each one come from" by reading the hook's own install
record rather than inferring, and it **reports, never repairs**.

## The incidents it packages

- **#122 — the silent shadow.** Three locked skills in this repo's own cloud
  sessions arrived from BOTH the hook and the account store under one bare
  name. The listing shows the name once and nothing says which copy the model
  read. The doctor called such a session clean. Matching copies are now a NOTE
  and divergent ones a FINDING — the split matters, because reddening the
  ordinary case is how a diagnostic gets skipped.
- **#84 — every file correct, nothing ever runs.** The lock and the hook were
  both right; what was missing was a settings file at a level the hook chain
  actually reads (ADR 0005, ADR 0007).
- **False drift from timestamps.** Account drift was judged by `updatedAt`
  against `git log`, so a repo-wide path move re-flagged every skill it
  touched: `pdf-ocr-audit` and `wj-next-break` both read STALE while being
  byte-identical once CRLF was folded. Content is the verdict now and the
  timestamp is not consulted at all.
- **#157 — a false clean over 21 skills.** Claude Code 2.1.273+ buckets the
  account store at `synced/<organizationUuid>_<accountUuid>/`. Reading the flat
  path found nothing, and this script's answer to nothing was "this is what an
  account with no uploads looks like … 0 drifted", exit 0 — while the bucket
  one level down held 21 skills. The shadow comparison went silent the same
  way. `resolve_account_store` finds the bucket, names it in the report header,
  and REFUSES with exit 2 when a machine has several and nothing says which is
  this session's.

## Why it is `adam`

Cloud-safe: it reads the session it is standing in and needs no browser, no
credentials and no machine-bound surface. That is also its limit — nothing in
CI can run it, because CI never stands on a surface where the account channel
exists at all (ADR 0002, E5).

## The rule every finding is written to

A verdict that could not be measured must never be printed as a clean one.
`store_findings` raises when the personal store is unreadable, `account_drift`
distinguishes "0 drifted" from "could not resolve the store", and
`DriftReport.blocked` is a separate field rather than a magic zero for exactly
that reason.

## Eval status

No eval existed when #157 was fixed. `DESIGN.md` names `skills-doctor` as a
Class B (diagnosis/triage) candidate with no fixture yet; the first fixture
is `evals/skills-doctor/` in skills-evals.
