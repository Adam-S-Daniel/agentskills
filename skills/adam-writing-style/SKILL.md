---
name: adam-writing-style
description: Write in Adam Daniel's voice — professional but warm, direct, em-dash-friendly, free of corporate buzzwords. Trigger whenever the user asks Claude to write something that will go out under Adam's name or be lifted into his materials: emails, replies, bios, proposal blurbs, cover letters, LinkedIn posts, performance self-appraisals, comments on other people's drafts, "ghostwrite this for me", or any "in my voice / sound like me" request. Also trigger when polishing or rewriting Adam's own draft. Do NOT trigger for generic third-party content the user is helping someone else produce (e.g. "draft a press release for the company") unless they ask for Adam's voice specifically.
---

# Writing in Adam Daniel's voice

This skill captures the patterns Adam uses when he's writing something that
matters — emails to schools and clinicians, work correspondence, performance
self-appraisals, proposal bios, advice to friends. Adopt these patterns when
generating any content that will be attributed to Adam or pasted into his
materials.

## The register, in one paragraph

Adam writes like someone who genuinely respects the reader's time, tells the
truth about what he knows and doesn't know, and treats specifics as a kind
of courtesy. He's warm without being effusive, factual without being curt,
and willing to be a little self-deprecating to take pressure off the
recipient. He almost never reaches for marketing language. He tends to tuck
asides between em dashes, hedge politely on assertions ("I'm guessing", "I
think", "as far as I can tell"), and back claims with concrete sources or
numbers when they're available.

## Core moves

### 1. Greet warmly, by name

- `Hi Ms. [LastName]!`
- `Hi [FirstName],`
- `Hey all —` for small group threads

Skip it for tight back-and-forth replies inside an active thread. Avoid
"Dear" for normal correspondence; reserve for very formal letters.

### 2. Open with a hedge or apology when the contact is unsolicited

> "I'm guessing you're the right person for this, but if not my apologies,
> and feel free to forward."

> "Sorry, just realized I never replied to this."

> "Sorry, one last note/question:"

These openings disarm the reader and acknowledge that you're imposing on
their attention. Use them when initiating contact, following up, or sending
a third email in a row.

### 3. Be specific. Cite, link, quote, and quantify.

- Quote dollar amounts to the cent.
- Paste IDs, payment confirmations, ticket numbers verbatim.
- Hyperlink the page you're talking about, even if the recipient probably
  knows it.
- When making a claim about policy or rules, link the source.

Specificity is doing the recipient's work for them.

### 4. Em dashes for asides

Adam uses em dashes (`—`) generously, both for parenthetical asides and to
break a long sentence at the right joint:

> "the URL of the other link — used on three other pages of the site —
> includes a year that's now out of date"

> "...building the standardized deployment scaffolding, secrets-remediation
> pipeline, and CI/CD foundations its development teams now rely on..."

Real em dashes, not double-hyphen `--`. Always `space — space`.

### 5. Manage the recipient's urgency

After raising an issue, tell them how urgent it is so they can prioritize:

> "Not urgent, just a heads-up."

> "No need to reply."

> "Just an update here; no need to reply: ..."

### 6. Sign off plainly

Standard closes, in rough order of formality:

- `Thank you,` / `Thanks,` / `Thanks so much for your help!`
- `Adam Daniel` (full name to people who don't know him well)
- `Adam` (to people who do)
- Phone number, when extending a request that may need a callback (Adam
  has it memorized; this skill deliberately does not record it)

Skip "Best regards," "Warmest," "Sincerely," etc.

### 7. Light humor in sparing doses

Occasional emoji is fine when warranted — a single 😃 inside a parenthetical,
not at the end of a sentence. Self-deprecating humor about himself works.
Wisecracks about the recipient or third parties don't.

> "...making some users (me anyway 😃) feel uncertain that it is still
> accurate."

### 8. Proposal / bio register: third person, factual, no puff

When writing a proposal bio or a "selected skills" blurb, drop the email
softeners. Keep:

- Concrete employer-and-dates structure ("at Novigen Sciences and its
  successor Exponent (1998–2005)").
- Specifics over adjectives ("normalized large, heterogeneous data sets
  drawn from federal agencies and private industry into unified schemas
  suitable for statistical analysis and risk modeling" — not "deep
  expertise in data integration").
- Em dashes for asides about scope or scale.
- Plain certifications listing at the end.

### 9. Performance-eval register: first person, narrative, generous

When writing self-appraisals or year-end summaries, Adam writes in
paragraphs and bulleted accomplishments, takes credit alongside
collaborators (e.g. "Combined with build fixes made concurrently by a
coworker, ..."), and explicitly names the next year's goals at the end.

## Vocabulary preferences

### Use freely

- "I'm guessing", "I think", "as far as I can tell" (epistemic honesty)
- "FYI", "heads-up", "wanted to flag"
- "given that", "in light of", "since"
- "kicked off", "stood up", "spun up" (for getting things started)
- "rolled out", "promulgated" (for spreading practices)
- "reached" (a milestone), "landed" (a change)

### Avoid (almost always)

- "leverage" as a verb, "synergy", "robust", "best-in-class", "world-class"
- "deep dive", "deep expertise in", "thought leader"
- "circle back", "touch base", "ping me"
- Excessive exclamation marks. One per email max, usually in the greeting.
- Em dash overuse to the point of dash-soup. If a sentence has two em-dash
  asides, split it.
- Hedging stacked on hedging ("I think it might possibly be the case
  that...") — pick one hedge per claim.

### Adam's specific preferred spellings / forms

- `404s` (verbed, lowercase)
- "Section 508" (capital S, no abbreviation)
- "FISMA / NIST 800-53" (with the slash)
- "CI/CD", "IaC", "PaaS", "GHEC" — capitalize standard tech acronyms
- Oxford comma: yes
- Smart quotes inside prose are fine; in code blocks, straight quotes

## Anti-patterns to flag and rewrite

If Adam (or a draft for Adam) reads like any of the below, rework it:

- Bullet lists with one-word bullets and no sentences around them. Adam
  writes prose unless the structure genuinely demands bullets.
- Resume-headline language ("results-driven", "passionate", "proven track
  record") — strip it.
- Long paragraphs of self-praise without specifics. Replace with one
  concrete example.
- Anything that reads like a chatbot wrote it: parallel intros, repeated
  "Furthermore,"/"Moreover,"/"Additionally," chain, three-bullet lists with
  a sentence above each.
- Closing with "Let me know if you have any questions!" without context.
  Either spell out what kind of follow-up makes sense or skip the line.

## Privacy / public-output rule

This skill lives in a public repository. Do not commit examples that
contain other people's names (recipients, family members, coworkers),
real institutional URLs tied to Adam, exact dollar amounts from real
transactions, phone numbers, account numbers, or anything similarly
identifying. Use placeholders like `[FirstName]`, `[School]`,
`$X,XXX.XX`, `https://example.edu/...` in calibration examples. The
patterns illustrate just as well without the personal detail attached.

When generating content for Adam in a private session, real names and
specifics are obviously fine — the rule is about what gets persisted
into shared/public artifacts.

## Output checklist

Before delivering writing in Adam's voice, verify:

1. Greeting is warm and uses the recipient's name where known.
2. First paragraph respects the recipient's time and frames the urgency.
3. Specifics (numbers, links, IDs, dates) are present where they'd help.
4. No corporate buzzwords from the avoid-list survived.
5. Em dashes are real `—`, used at least once, not in every sentence.
6. Sign-off is plain (`Thanks,` / `Thank you,` / name).
7. In emails, the message ends with the action you want or an explicit
   "no action needed."

## Calibration examples

### Email — unsolicited, low-stakes flag

> Hi Ms. [LastName]!
>
> I'm guessing you're the right person for this, but if not my apologies,
> and feel free to forward.
>
> On the Bell Schedules page (https://example.org/...), the "Click here
> to be taken to the updated Bell schedule" link currently 404s. The URL
> of the other bell schedule link — used on the Main Office,
> Administration, and Start of School pages — includes "2022-2023",
> making some users (me anyway 😃) feel uncertain that it is still
> accurate.
>
> Not urgent, just a heads-up.
>
> Thank you,
> Adam Daniel

### Email — short follow-up to a transactional thread

> Hi [FirstName],
>
> Please find attached proof of the electronic payment in the amount of
> $X,XXX.XX.
>
> Thank you,
> Adam Daniel

### Email — sourced question

> Sorry, one last note/question: Based on the payment plan ledger the
> Friends and Family dashboard shows me, it appears the hold was placed
> on the registration when the April payment was less than 14 days past
> due. Do you know why that happened given that
> https://example.edu/student-accounts/late-fees-holds/ indicates such
> holds are placed when payment is 60 days overdue?
>
> Thanks so much for your help!
>
> Adam Daniel

### Proposal-bio paragraph

> He started his career at Novigen Sciences and its successor Exponent
> (1998–2005), writing data-management and probabilistic-modeling tools
> for federal regulators and large industrial clients. Working alongside
> statisticians and scientific staff, he designed stochastic models,
> built licensed commercial desktop applications and Excel-based
> analytic tools, and routinely normalized large, heterogeneous data
> sets — drawn from federal agencies and private industry — into
> unified schemas suitable for statistical analysis and risk modeling.

### Performance-eval bullet

> Built standardized cloud deployment repository, improving consistency
> and reliability across multiple application teams.

## When in doubt

Read the recipient's previous message back and write toward them, not
toward an imagined formal standard. Adam's voice mirrors the reader: more
formal with new contacts and authority figures, more conversational with
people he's been corresponding with for years, plain English in both.
