# The blind-spot pass

The point of this pass is **not** to answer the questions you already have. It
is to generate the questions you did not know to ask, before a wrong assumption
gets baked into a design.

Run it after you can describe the task in a paragraph, and before you write a
plan.

## The core prompt

> Before we plan this, do a blind-spot pass. Don't answer my questions — find
> the ones I haven't asked. What assumptions am I making that could be wrong?
> What would you need to know to do this well that I haven't told you? Where
> would you have to guess?

Then, separately:

> What's the most likely way this goes wrong that neither of us has mentioned?

## Interrogation checklist

Work down the list. Anything you cannot answer concretely is an unknown; decide
whether to resolve it now or record it as an accepted risk.

**Scope and boundaries**
- What is explicitly *out* of scope? (If nothing is, the scope isn't defined.)
- What existing behaviour must not change?
- Who or what else consumes this? What breaks downstream?

**Correctness**
- What does "done" look like, as something observable? A test, an exit code, a
  screenshot — not an adjective.
- What are the edge cases: empty, one, many, huge, concurrent, malformed,
  hostile?
- What happens on partial failure? Is the operation retried, resumed, or lost?

**Constraints you may not know exist**
- What in this repo already solves part of this? (Duplicating an existing helper
  is the most common avoidable mistake.)
- What conventions does this codebase enforce that a newcomer would violate?
- Are there invariants that are load-bearing but not obvious — ordering,
  idempotence, a file that must stay byte-identical?
- What's the deployment/rollback story if this is wrong?

**Unknown knowns (taste made explicit)**
- What would make you reject this even if it worked?
- Is there existing code you consider the "right shape" to mirror?
- Which trade-off do you actually want: fewer moving parts, or fewer edge cases?
- Where on the spectrum from "quick and reversible" to "durable and rigid"?

**Evidence**
- What reference can be supplied *in code* instead of prose — a failing test, a
  mockup, an implementation to port, a rubric?
- How will this be verified, by a command that exits 0 or 1?

## Competing directions

Ask for two or three sketches with different risk profiles rather than one
proposal, and require each to state what it assumes:

> Give me three ways to do this. For each: what it assumes, what it makes easy,
> what it makes hard later, and how I'd know within a day if it was the wrong
> pick.

The comparison surfaces assumptions that a single proposal keeps invisible,
because a lone design never has to defend its premises against an alternative.

## Prototype the risky part first

When a disagreement is about how something will *feel* or *behave*, stop arguing
and build the smallest thing that answers it — the risky 20 lines, a throwaway
script, a static mockup. A prototype converts an unknown into a fact faster than
any amount of discussion, and it costs less than a wrong week.
