# 0003. Retire the SHA-pinning skill and carry its rule in managed guidance

- **Status:** Accepted
- **Date:** 2026-08-17
- **Deciders:** Adam Daniel

> This ADR is the **one file in the account permitted to name the retired
> skill**. Everything else — this file's own name, the index row that links it,
> every other repo — refers to it as "the SHA-pinning skill". That is why the
> title and filename read the way they do; keep it that way if you amend this.

## Context

`pin-actions-to-sha` was a skill in the `adam` bundle: audit every `uses:` in a
workflow, pin it to a full 40-character commit SHA, annotate it with
`# vX.Y.Z (YYYY-MM-DD)`, and hold a 7-day cooling-off before adopting a new
release. It carried both halves of the subject — the *rule* (what a correct pin
looks like) and the *procedure* (the `gh api` calls that get you from a tag to
the commit SHA behind it).

Only one of those halves was ever safe to package as a skill.

A skill reaches the model in two stages: it must be **installed** in the
session, and then its `description` must **match** what the agent is doing.
Both stages fail open, and both fail silently:

- **Installation.** Cloud and other ephemeral sessions get no plugins from
  repo-declared settings — the E1 finding recorded in ADR 0001, matching
  anthropics/claude-code#32606 and #13096. The `skills.lock` +
  `skills-bootstrap` route closes that gap, but adoption is opt-in per repo and
  most repos have not adopted. So in a fresh cloud clone — the session most
  likely to be authoring a workflow from scratch — the skill was simply absent.
- **Trigger match.** Even fully installed, a skill is surfaced on description
  match. An agent editing `.github/workflows/deploy.yml` to change a job's
  timeout is not "auditing actions" and would never pull the skill in, yet it
  can still paste `uses: some/action@v4` on the way past.

Neither failure announces itself. The agent does not know the skill exists, so
nothing reports that the rule went unapplied — the workflow just lands with a
movable tag in it.

That is an acceptable outcome for a procedure and an unacceptable one for this
rule. `sha_pinning_required: true` is set on every repo in the account (by
`repo-settings`' `fleet.yml`, and by `cms-platform`'s `repo-settings.yml` for
the three sites it manages), so an unpinned `uses:` is a hard failure rather
than a style nit — and the reason the setting exists is that a tag is a movable
pointer, so pinning to one hands whoever can retag the upstream repo a shell on
the runner holding that job's token. A supply-chain rule whose application is
conditional on a trigger match is not a rule.

## Decision

Retire the `pin-actions-to-sha` skill. The SHA-pinning **rule** moves into
`_agent-guidance`'s managed `AGENTS.md` block, as the section
"## Pinning GitHub Actions", which propagates into every repo in the account and
is therefore in every agent's initial context unconditionally. The
step-by-step lookup **procedure** is dropped rather than rehomed.

The generalisation this encodes:

> **Always-on guidance carries rules; a skill carries procedures.** If
> forgetting the thing is an incident, it cannot live somewhere that only
> loads sometimes. If the thing is a multi-step recipe you would look up when
> you need it, a skill is the right carrier and the occasional miss is cheap.

## What was kept and what was dropped

**Kept** — moved verbatim in substance into `agents-md/base.md`:

- The full 40-character commit SHA requirement, applied to workflows, composite
  actions and reusable-workflow references alike; never a tag, never a branch,
  never an abbreviated SHA.
- The trailing `# vX.Y.Z (YYYY-MM-DD)` comment as *part of the pin*, plus the
  note that Dependabot rewrites the SHA and version but not the date.
- The 7-day cooling-off before adopting a release, and what to do when the
  newest release is younger than that.
- Annotated-tag dereferencing — the failure where
  `repos/<owner>/<repo>/git/ref/tags/<tag>` returns `.object.type == "tag"` and
  pinning that SHA fails at runtime — including the one-line
  `git ls-remote <url> 'refs/tags/<tag>^{}'` form.
- The carve-out that `./local/path` and `docker://` refs have nothing to pin.

**Dropped** — the ~250-line walkthrough: the per-step `gh api` invocations for
listing tags, reading a release date, fetching a tag object and dereferencing
it, the worked before/after transformation table, and the batch audit loop.
This is recoverable procedure. An agent that knows the rule and has `gh` can
re-derive it, and the single non-obvious step (the annotated-tag dereference)
was promoted into the rule rather than left in the part being deleted.

## Consequences

- **The rule is now unconditional.** It reaches every session in every repo
  regardless of surface, plugin state, or whether anything triggered — which
  was the entire point.
- **Every session pays for it in always-on context**, whether or not it will
  ever open a workflow file. Roughly thirty lines, charged in all of the
  account's repos, on every turn, forever. This is a real and permanent cost
  and it is the price of the previous bullet; it is justified only because the
  rule is short, universal, and enforced by a repo setting. The same trade
  would not justify moving a long or niche skill into base.md.
- **`github-actions-repo-settings` lost a sibling.** Its section 10 used to
  hand off to this skill for the remediation half of enabling
  `sha_pinning_required`; it now points at `AGENTS.md → "Pinning GitHub
  Actions"`. That handoff is a doc pointer rather than an invocation, so the
  one-command remediation path is gone even though the information is not.
- **skills-evals loses its reference A/B subject.** The with-vs-without eval
  harness was built around this skill and its weekly result was the badge at
  the top of this repo's README; both go away. The harness keeps working, but
  its established baseline — the one skill with a run history to compare
  against — no longer exists, so the next skill evaluated starts cold.
- **The detailed procedure is gone from the registry**, not archived elsewhere.
  Recovering it means re-deriving from the `gh` and `git` docs. Accepted
  knowingly: see "kept and dropped" above.
- **The marketplace `renames` entry stays.** ADR 0001 makes that map
  append-only forever, and its values are *bundle* names: `pin-actions-to-sha`
  is a retired **plugin** name whose target, the `adam` bundle, still exists
  and is still what a stale install must migrate onto. Deleting the entry would
  strand any user updating from a pre-consolidation marketplace — the exact
  harm the append-only rule exists to prevent — and would do so to save a
  string in a machine-read map that no agent loads into context. The skill is
  gone; the historical plugin name keeps resolving.

## Alternatives considered

**Keep the skill and also state the rule in base.md.** Rejected: two copies of
one security rule drift, and they would drift in the worst direction — the
skill's copy was the more detailed one, so the always-present copy would decay
into the less trustworthy of the two. It also pays both costs (always-on
context *and* bundle weight) to buy one guarantee.

**Keep the skill and fix delivery instead — push `skills.lock` adoption to
every repo.** Rejected: it closes the installation half and leaves the
trigger-match half wide open. A skill that is present but never surfaced is
indistinguishable, at the point of failure, from one that was never installed.
It also makes every repo carry the whole `adam` bundle to secure one rule.

**Keep the skill unchanged and accept the gap.** Rejected on the enforcement
asymmetry: `sha_pinning_required` turns a missed pin into a failed run, and the
threat model behind it is upstream repo compromise. The cost of the rule being
occasionally absent is not symmetric with the cost of carrying it always.

**Rehome the `gh api` walkthrough into `github-actions-repo-settings`.**
Rejected: that skill is about repository *settings* as code — introspect, diff,
apply. Bolting a workflow-file rewriting procedure onto it would blur a
coherent skill's scope to avoid deleting text, and the procedure's one
load-bearing step was promoted into the rule anyway.

## How to verify

- `_agent-guidance/agents-md/base.md` contains the "## Pinning GitHub Actions"
  section, with the 40-character-SHA rule and the 7-day cooling-off.
- The managed block that `build-agents-md.sh` renders — every section, not just
  the default set — carries that rule and does not name the retired skill, so
  the next sync clears the stale mentions still sitting in consumer `AGENTS.md`
  files rather than reintroducing them.
- No repo tree contains a `plugins/*/skills/pin-actions-to-sha/` directory, and
  `skills.lock` no longer keys `adam/pin-actions-to-sha`.

## References

- [ADR 0001](0001-consolidate-plugins-into-bundles.md) — the bundle layout, the
  E1 cloud-session finding, and the append-only `renames` rule this decision
  defers to.
- `_agent-guidance/agents-md/base.md`, section "Pinning GitHub Actions" — where
  the rule now lives.
- [`docs/experiments/E2-cloud-session-result.md`](../experiments/E2-cloud-session-result.md)
  — the probe that established the `skills.lock` + SessionStart delivery route,
  and its limits.
- [`STRATEGY.md`](../../STRATEGY.md) — the registry's placement rules and
  graduation path.
- anthropics/claude-code#32606, anthropics/claude-code#13096 — cloud sessions
  ignoring repo-declared marketplaces/plugins.
