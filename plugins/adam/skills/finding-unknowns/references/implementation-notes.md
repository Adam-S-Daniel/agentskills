# Implementation notes, explainers, and comprehension checks

Three artifacts for the *during* and *after* halves of the loop. None of them
should be produced by default — write one when the work is non-trivial enough
that someone will have to trust it later.

## 1. Implementation notes (during)

The unknowns discovered mid-build are the highest-value ones and the easiest to
lose, because by the time the work is finished they feel obvious. Keep a running
note as you go, not a reconstruction afterwards.

Capture only deltas — do not restate the plan:

```markdown
## Implementation notes

### Departures from the plan
- **<what changed>** — planned X, did Y. Why: <the constraint that forced it>.
  Consequence: <what this means for anyone reading the code later>.

### Edge cases found while building
- <case> → handled at <file:line> / deliberately not handled because <reason>.

### Still unresolved
- <question> — currently assuming <assumption>. Breaks if <condition>.
```

The "still unresolved" block is the part that earns its keep: it is the
difference between a known accepted risk and a silent landmine. Anything left
there at the end goes in the PR description.

## 2. Explainer / pitch (after)

For a change that needs buy-in — a reviewer, a teammate, your future self.
Lead with the problem, not the diff.

```markdown
# <Change>, in one line

**Problem.** <What was broken or missing, and how it showed up in practice.>

**Approach.** <The shape of the fix, in two or three sentences.>

**Why this and not the obvious alternative.** <The alternative, and the specific
reason it loses. This is the section that stops the decision being re-litigated.>

**What this does not do.** <Explicit non-goals and accepted limitations.>

**How to tell it works.** <The command, test, or observable behaviour.>
```

If the "why not the alternative" section is hard to write, that is usually a
sign the design is not settled yet — not that the writing is hard. Treat it as
a signal to go back a step.

For a decision that will invite "let's just change it back" a year from now,
promote this into an ADR under `docs/decisions/` instead — see the
`writing-adrs` skill.

## 3. Comprehension check (after)

A quiz is the cheapest way to find the parts of a change that were
pattern-matched rather than reasoned through. Ask for it against the diff:

> Quiz me on this change. Ask questions I can only answer if I actually
> understand it — what breaks if a line is removed, why a branch exists, what
> the failure mode is under concurrency. Don't ask me to recite the diff.

Good questions target load-bearing decisions:

- "What happens if `<X>` is called twice concurrently?"
- "Why is this check before the write rather than after?"
- "Which line makes this idempotent? What happens without it?"
- "This tolerates a missing file. What made that necessary?"
- "What would a reviewer most likely object to, and what's the answer?"

A question you cannot answer is not a failed quiz — it is an unknown that
survived all the way to the end, which is exactly what the loop exists to
catch. Fix the code or write the answer down; do not just move on.

## Feeding unknowns back

A gotcha that cost real time should not live only in a PR thread. Route it:

- A constraint that applies to **every** task in the repo → the repo's guidance
  file (`AGENTS.md`), stated as briefly as it can be stated.
- A **procedure** that recurs → a skill in the registry.
- A **decision** that will be questioned later → an ADR.
- Anything else → a comment at the line that would otherwise mislead someone.
