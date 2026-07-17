---
name: writing-adrs
description: Write a lightweight Nygard-style Architecture Decision Record under `docs/decisions/` when a non-obvious decision needs context that won't fit in a code comment and would rot if left only in a PR description. Trigger when the user asks "should I document this", "add an ADR", "why did we do X" referring to a past choice with no comment trail, or when you find yourself drafting a multi-paragraph PR description justifying a one-line change. Also covers the bootstrap case (creating `docs/decisions/README.md` + the first ADR from scratch) if the folder doesn't exist yet.
---

# Writing Architecture Decision Records

A `docs/decisions/` folder captures **why** non-obvious decisions were made — context that's not in the code, that `git blame` won't surface, and that a contributor a year from now would re-derive (badly) without it.

This skill tells you when to write one, how to format it, and how to keep the index honest. It's repo-agnostic: it operates on whatever repository you're working in, and it carries its own template so it works even in a repo that has no ADR folder yet.

## When to invoke this skill

Trigger any of these:

- **The user explicitly asks for one.** "Should this be an ADR?", "add an ADR for X", "document this decision so we don't undo it", "write up why we did Y this way".
- **You're drafting a PR description that's growing into an explanation of *why* a small change isn't crazy.** That's the signal: the explanation belongs in an ADR, the PR description should link to it. Recognize this pattern when your PR body has paragraphs starting with "you might wonder why we didn't just…" or "this looks like a downgrade because…" or "the reason we picked X over Y is…".
- **The user asks "why did we…" about a past decision and there's no satisfying answer in code comments, AGENTS.md, or commit history.** Offer to write the ADR retroactively so the next person doesn't have to ask.
- **A code-review comment surfaces a non-obvious decision** — propose extracting the reasoning into an ADR and linking to it from the code comment.

Do NOT invoke for:

- One-character cosmetic preferences.
- Decisions already covered by an existing ADR (link to that one instead, or update its `Status` to `Superseded by NNNN` and write the replacement).
- Decisions that are fully obvious from the code or already documented in AGENTS.md / a CLAUDE.md / a focused doc under `docs/`. Adding an ADR for those is bureaucratic clutter.
- Decisions where the "why" fits comfortably in a 2-3 line code comment at the call site AND the call site is stable. Local context beats remote context.

## Procedure

### 0. Read the existing convention

If `docs/decisions/README.md` exists in the target repo, read it first — it has the canonical format, naming convention, and index for that repo. Follow it. If it doesn't exist, skip to "Bootstrap" below.

### 1. Confirm the decision is ADR-worthy

Ask yourself (or the user, if unclear) one question: **"In six months, when someone proposes reverting this change, what's the shortest answer that'll save them an afternoon of investigation?"** If the answer is a sentence, an inline comment is fine. If it's three paragraphs covering the alternatives you ruled out, write the ADR.

The most reliable ADR-worthiness test: would a reasonable contributor scan the diff and think "this looks wrong, let me undo it"? If yes — ADR.

### 2. Pick the next number

Read `docs/decisions/README.md`'s index and pick `MAX(existing) + 1`. Zero-pad to 4 digits (`0001`, `0002`, …, `0011`, …). Files sort correctly past 9999 if and when we get there; we won't.

### 3. Pick a title

Imperative verb + object. Examples that work: "Store timestamps as UTC epoch millis, not local ISO strings", "Pin GitHub Actions to commit SHAs not version tags", "Block direct pushes to `main` via ruleset, not branch protection".

Examples that don't work: "Timestamp config" (no verb), "Decided to switch storage format" (past tense + vague), "Architecture for time handling" (noun pile).

### 4. Use the template

Copy the template from `docs/decisions/README.md` (or, if you're bootstrapping, from [`references/decisions-README-template.md`](references/decisions-README-template.md) in this skill) and fill in the sections in this order:

1. **Context** first. What did we observe? What constraints applied? What forced the decision? Write this without referencing the decision — it should read as "here's the problem space."
2. **Decision** second, in one or two sentences. The shortest unambiguous statement of what we did.
3. **Consequences** third. Both positive AND negative — be honest. Future-you will trust an ADR more if it acknowledges trade-offs than if it reads like a press release.
4. **Alternatives considered** fourth. This is the section that earns its keep when a future contributor proposes one of them. For each alternative, one short paragraph: what it was, why we rejected it. If the rejection is "we didn't think of it at the time," say that — write the alternative anyway so we don't re-evaluate it without learning something.
5. **References** last. PRs, issues, commits, external docs. Anchor the ADR to artefacts that won't go stale.

Optional sections worth considering for high-impact decisions:

- **"Why this doesn't break X"** when the decision looks scarier than it is. Walk through the layers (storage, rendering, callers, tests) and explain why each one is fine.
- **"How to verify"** when the decision can be locked in with a test or an invariant — describe the test and link to it.

### 5. Update the index

The README's index table at the bottom needs a new row. Add it in the same commit as the ADR — never in a follow-up. Format:

```markdown
| [NNNN](NNNN-kebab-title.md) | Title (matching the H1) | Status |
```

### 6. Link to the ADR from the relevant code

Wherever the decision shows up in the codebase, add a short comment pointing at the ADR. Two or three lines max — the ADR has the prose, the code comment is the signpost. Pattern:

```js
// See docs/decisions/0007-utc-epoch-millis.md for why timestamps are
// stored as epoch millis, not ISO strings.
```

If the decision shows up in multiple files, only the call site needs a comment; the rest can rely on grep. Don't sprinkle the same comment everywhere.

### 7. Update the PR description

In the PR that introduces (or implements) the decision, link to the ADR in the body. The PR description should be short — "implements `docs/decisions/NNNN-…`" plus a one-line summary — and the ADR carries the prose. This keeps reviewers' attention on the diff, not on re-reading the rationale.

## Bootstrap: the folder doesn't exist yet

If `docs/decisions/` doesn't exist, set it up in the same change as the first ADR. The README and the first ADR land together — never bootstrap an empty folder.

1. Create `docs/decisions/README.md` from [`references/decisions-README-template.md`](references/decisions-README-template.md) in this skill — it carries the format spec, the when-to-write rules, the ADR template, and an empty index. Adjust wording to match the target repo's house style if it has one.
2. Write the first ADR (`0001-…`).
3. Add the index row for the first ADR.
4. Add a pointer paragraph in `AGENTS.md` (or `README.md` if there's no AGENTS.md) under a new `### Architecture Decision Records` heading, immediately above or below the project docs / README index. The pointer is one or two sentences and a link.
5. Commit everything as a single `docs(decisions): start ADR folder; first ADR is …` commit. Same PR as the change the ADR is justifying — don't ship the ADR scaffolding in isolation, it'll feel like overhead.

## Common mistakes to avoid

- **Writing the ADR *after* the PR merges.** ADRs are most useful when reviewers can read them during review — they signal "I've thought about the alternatives, here they are." Write the ADR as part of the same PR.
- **Editing accepted ADRs to change the decision.** Don't. Write a new ADR that supersedes the old one and update the old one's `Status` line. The audit trail is the point.
- **Including transient state.** No "as of today the build is broken" or "currently we're using X but will move to Y next quarter." ADRs decay if they contain time-bound information that's not pinned to a specific PR or commit.
- **Skipping "Alternatives considered."** This is the most-skipped section and the most valuable. A future contributor proposing alternative A wants to know if A was already evaluated. If you didn't evaluate any alternatives, write "Alternatives considered: none; the decision was forced by an external constraint (link)" — make it explicit.
- **Linking to the ADR from too many places.** One comment at the call site is enough. The ADR is searchable; the codebase doesn't need ADR breadcrumbs on every adjacent line.
- **Numbering disputes.** If two PRs both want `NNNN` and one merges first, the second rebases and renumbers in their own PR. No retroactive renumbering of already-merged ADRs.

## What's NOT an ADR

To keep `docs/decisions/` from drifting into a general-purpose docs dump:

- **Tutorials and how-tos** belong in `docs/<topic>.md` (e.g. a content guide, a testing guide).
- **Project-wide conventions** that aren't the result of a *decision* belong in `AGENTS.md` (branch hygiene, how to read PR diffs, etc.).
- **Skill instructions for agents** (when to use a pattern, how to apply it) belong in a skill's own `SKILL.md` — like this file.
- **Test plans and runbooks** belong in a testing doc or a script's own header comment.

The ADR test: "If someone proposes reverting this, the ADR is what they read." Anything that doesn't fit that purpose probably belongs elsewhere.
