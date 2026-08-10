---
name: finding-unknowns
description: Surface and resolve the ambiguities in a task before, during, and after implementation — the blind-spot pass, the self-interview, reference-driven specs, implementation notes, and a post-hoc explainer or quiz. Use at the start of any non-trivial build, when working in an unfamiliar codebase or a new domain, when acceptance criteria are subjective ("make it feel polished", "clean this up"), when a plan keeps needing rework mid-implementation, or when the user says "help me find my unknowns", "what am I missing", "poke holes in this", "what haven't I thought about". Also use after a change lands, to produce the explainer or comprehension check that proves the work is understood.
---

# Finding your unknowns

On a non-trivial task, output quality is bounded less by the model's ability to
write code than by how well the task's ambiguities got resolved. The ceiling is
set by clarification, not by effort.

The mistake is treating that as a phase. Ambiguity is not front-loaded and it
does not end when a plan is approved — the sharpest unknowns surface *while*
building, when a real constraint contradicts an assumption nobody wrote down.
So this is a loop, run before, during, and after implementation.

## Three kinds of unknown

Naming which kind you're facing tells you which tool to reach for:

| Kind | What it looks like | What resolves it |
|---|---|---|
| **Known unknowns** | "I don't know which auth flow this API wants." | Go look. Read the code, the docs, the wire format. |
| **Unknown unknowns** | The constraint you didn't know existed until it broke the build. | The **blind-spot pass** — ask for what you *didn't* ask about. |
| **Unknown knowns** | You have a strong opinion about "good" here but never said it out loud. | The **self-interview** — force the taste into words, or a rubric. |

Unknown knowns are the quiet one. When someone rejects finished work as "not
quite right" but can't say why, that's an unknown known that was never
articulated — and no amount of implementation effort would have hit it.

## When to invoke

- Starting anything non-trivial in an **unfamiliar codebase** or a **new domain**.
- Acceptance criteria are **subjective** — "polished", "clean", "readable",
  "feels fast".
- A plan has already needed **mid-flight rework** once. That's evidence the
  unknowns weren't drained.
- The user asks "what am I missing", "poke holes in this", "what would you ask
  me", "help me find my unknowns".
- After a change lands and someone else has to buy into it or maintain it.

Do NOT invoke for: a one-line fix, a task with an exact spec and a failing test
that defines done, or mechanical edits (renames, pin bumps, config tweaks). The
interrogation costs more than the task.

## Before implementation

1. **Blind-spot pass.** Ask explicitly for the questions you did not think to
   ask — not answers to your existing questions. Prompts and the interrogation
   checklist: `references/blind-spot-pass.md`.
2. **Brainstorm more than one direction.** A single proposal hides its own
   assumptions; two or three competing sketches expose them by contrast. Cheap
   prototypes beat argument — build the risky 20 lines and look.
3. **Self-interview.** Answer the ambiguity questions out loud before building.
   If an answer is "whatever seems right", that is an unknown known — pin it
   down or write a rubric for it.
4. **Anchor the spec in code, not prose.** This is the highest-leverage move
   available: a reference implementation to mirror, a failing test, an HTML
   mockup, a rubric, the actual function being ported. A mockup produces better
   results than a paragraph describing the mockup, because prose silently drops
   the details the code is forced to make explicit.
5. **Then** write the plan — with the resolved answers in it, not the questions.

## During implementation

Unknowns discovered mid-build are the most valuable ones, and the easiest to
lose. Capture them as they happen:

- Keep **implementation notes**: every place the build departed from the plan,
  and why. Template: `references/implementation-notes.md`.
- Note **edge cases** as they surface, even ones handled in passing. An edge
  case handled silently is indistinguishable from one that was missed.
- When a discovery invalidates the plan, say so and re-decide. Do not quietly
  absorb a contradiction — that is how a plan becomes fiction.

## After implementation

- **Explainer / pitch.** Write up what changed and why it is the right shape,
  aimed at whoever has to approve or maintain it. If it can't be explained
  crisply, that is usually a design smell, not a writing problem.
- **Comprehension check.** Have the change quizzed back at you — what breaks if
  this line is removed, why this branch exists, what the failure mode is. It
  reliably finds the parts that were pattern-matched rather than reasoned
  through. Format: `references/implementation-notes.md`.
- **Feed the unknowns back.** A gotcha that cost real time belongs in the repo's
  guidance file or a skill, not only in the PR thread.

## How to ask

The framing matters more than the wording. These pull in genuinely different
material:

- "What are the unknown unknowns here?" — the blind-spot pass.
- "Interview me about this before we start. Ask the questions whose answers
  would change the design."
- "Give me three approaches with different risk profiles, and say what each one
  assumes."
- "What did I not ask about that you'd want to know?"
- "Quiz me on this change."

Weak framings to avoid: "any questions?" (invites "no"), "does this look right?"
(invites agreement), "is this a good plan?" (invites a verdict, not a gap list).
