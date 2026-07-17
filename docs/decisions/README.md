# Architecture Decision Records

This folder captures **why** non-obvious decisions were made in this repo —
context that isn't in the code, that `git blame` won't surface, and that a
contributor a year from now would otherwise re-derive (badly).

Each ADR is one Markdown file, `NNNN-kebab-title.md`, numbered sequentially and
zero-padded to four digits. ADRs are **append-only**: once accepted, a decision
is superseded by a new ADR, never edited to say something different. The audit
trail is the point.

## When to write one

Write an ADR when, in six months, someone proposing to revert the change would
need three paragraphs — covering the alternatives you ruled out — to be talked
out of it. If a sentence in a code comment would do, write the comment instead.

The reliable test: would a reasonable contributor scan the diff and think "this
looks wrong, let me undo it"? If yes, write the ADR so they don't.

Do **not** write one for cosmetic preferences, decisions already covered by an
existing ADR (supersede it instead), or things fully obvious from the code or
already documented in `AGENTS.md` / `STRATEGY.md` / a focused `docs/` page.

## Naming and numbering

- `NNNN-kebab-title.md`, e.g. `0007-utc-epoch-millis.md`.
- Next number is `MAX(existing) + 1`, zero-padded to four digits.
- Title is an imperative verb + object: "Store timestamps as UTC epoch millis,
  not local ISO strings" — not "Timestamp config" (no verb) and not "Decided to
  switch storage format" (past tense, vague).
- If two open PRs claim the same number, whichever merges second renumbers in
  its own PR. Never renumber an already-merged ADR.

## Status values

`Proposed` → `Accepted` → `Superseded by NNNN` (or `Deprecated`). A superseded
ADR keeps its content and gains a `Superseded by NNNN` status line; the
replacement ADR links back to it.

## Template

Copy everything between the rules into `NNNN-kebab-title.md` and fill it in.

---

```markdown
# NNNN. Imperative title matching the index row

- **Status:** Proposed
- **Date:** YYYY-MM-DD
- **Deciders:** <names or roles, optional>

## Context

What did we observe? What constraints applied? What forced the decision? Write
this without referencing the decision itself — it should read as a description
of the problem space that any reader would recognize.

## Decision

The shortest unambiguous statement of what we did, in one or two sentences.

## Consequences

Both positive and negative — be honest. A future reader trusts an ADR that
names its trade-offs more than one that reads like a press release.

## Alternatives considered

For each alternative, one short paragraph: what it was, and why we rejected it.
If you didn't evaluate any, say so explicitly:
"Alternatives considered: none; the decision was forced by <external constraint (link)>."

## References

PRs, issues, commits, external docs — artefacts that won't go stale.
```

---

Optional sections for high-impact decisions:

- **Why this doesn't break X** — when the change looks scarier than it is, walk
  the layers (storage, rendering, callers, tests) and explain why each is fine.
- **How to verify** — when the decision can be pinned by a test or invariant,
  name the test and link to it.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-consolidate-plugins-into-bundles.md) | Consolidate single-skill plugins into three bundles | Accepted |
