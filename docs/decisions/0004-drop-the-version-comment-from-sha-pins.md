# 0004. Drop the version comment from SHA-pinned actions

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Adam Daniel

> Supersedes, **in part only**, [ADR 0003](0003-retire-the-sha-pinning-skill.md):
> exactly one bullet of its "what was kept" list — the trailing
> `# vX.Y.Z (YYYY-MM-DD)` comment. Everything else 0003 kept still stands: the
> full 40-character SHA, the 7-day cooling-off, annotated-tag dereferencing, and
> the `./local` / `docker://` carve-out.

## Context

Every `uses:` in this account is pinned to a full 40-character commit SHA, and
until now the pin was defined as carrying a trailing version comment:

```yaml
uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11 # v4.1.1 (2023-10-17)
```

The argument for the comment was that forty hex characters say nothing on their
own: the version says what the pin is and the date says how stale it is. That
argument assumes the comment stays true. It does not, and the mechanism that
was supposed to keep it true does not work.

**Dependabot's rewriting of the comment is inconsistent and cannot be relied
on.** Measured across the fleet on 2026-08-20:

- In `GHA-bench#52` it rewrote a bare `# v5` to `# v7.0.0` while leaving `# v4`
  stale on the line directly above it — same file, same PR.
- In `skills-evals#38`, `#39` and `#40` it moved the SHAs and left every
  `# vX.Y.Z (YYYY-MM-DD)` comment untouched.

The result in `skills-evals` is the failure in its clearest form: one action,
`actions/checkout`, actually at **v7.0.1**, was labelled `# v4.3.1` in one file
and `# v6.0.0` in two others — three different answers in one repo, none of them
correct.

A comment that drifts is not merely useless. **A wrong label is worse than no
label, because it is read and believed.** An agent or a reviewer deciding
whether a pin is current reads the comment, not the SHA — that is the entire
reason the comment was there — so a stale one actively misinforms the reader it
exists to serve, and it does so silently, with nothing that goes red.

The SHA itself never drifts. It is the truth, and it is verifiable on demand:

```bash
git ls-remote https://github.com/actions/checkout | grep <sha>
```

or, more cheaply in practice, the title of the Dependabot PR that moved it.

## Decision

Drop the trailing version comment. A SHA pin is the `@<sha>` and nothing after
it:

```yaml
uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11
```

Strip a trailing comment only when the ref is a full 40-hex SHA **and** the
comment is purely a version, optionally with a date. Comments that carry other
meaning — `# zizmor: ignore`, a security note, an explanatory sentence — are
unaffected, and so is the deliberate own-account carve-out that keeps
`Adam-S-Daniel/cms-platform/.github/workflows/*.yml@v0.1.8x` reusable-workflow
references on a *tag*.

## Consequences

- **The pin can no longer lie.** The only thing left on the line is the one
  token that is always correct.
- **Reading a version now costs a lookup.** This is the real price. It is paid
  by whoever actually needs the version, at the moment they need it, and it
  returns a right answer — where the comment was free to read and returned a
  wrong one.
- **Staleness stops being invisible.** There is no longer a field that looks
  maintained while rotting; Dependabot's inconsistency has nothing left to be
  inconsistent about.
- **Dependabot PRs get quieter and more honest** — the diff is the SHA, which is
  the whole of what changed.
- **The fleet converges by sync, not by hand.** The rule lives in
  `_agent-guidance`'s `agents-md/base.md`, so every repo's `AGENTS.md` picks it
  up on the next sync. Existing pins in other repos keep their comments until
  each repo is swept; a stale comment left behind is the status quo, not a
  regression.
- **No behaviour changes.** The comment was never read by GitHub Actions. Every
  workflow resolves the same commit before and after.

## Alternatives considered

**Keep the comment and fix Dependabot.** The rewriting is upstream behaviour we
do not control, and the measurements above show it is not merely lagging but
*inconsistent* — it rewrote one comment and skipped another in the same file.
There is nothing here to configure.

**Keep the comment and add a CI lint that verifies it against the real tag.**
This is the only option that would make the comment trustworthy. It costs a
network call per pin per run, needs a token, goes red for reasons unrelated to
the change under review, and — decisively — it would be enforcing the accuracy
of a field nothing actually requires. The cheaper way to make a field stop being
wrong is to delete it.

**Keep the version, drop the date.** This was the shape most of the damage
already took: `skills-evals` carried bare-ish version labels that were wrong by
three major versions. The date was never the part that lied hardest; the version
was.

**Sweep the whole fleet in one change.** Rejected as scope. Each repo's pins are
that repo's to sweep, and `agents-md/base.md` is what makes the rule arrive
everywhere. This ADR records the reversal and fixes this registry — including
the two shipped skills that were teaching the old format to every agent that
read them.

## How to verify

No in-scope pin comment survives:

```bash
grep -rPI '@[0-9a-f]{40}[ \t]+#[ \t]*v?[0-9]' --exclude-dir=.git .
```

The one expected hit is the managed block of `AGENTS.md`, which is generated by
the `_agent-guidance` sync and is not edited here — it clears on the next sync.

## References

- [ADR 0003](0003-retire-the-sha-pinning-skill.md) — the decision this amends;
  its "what was kept" list is where the retired convention was recorded.
- `GHA-bench#52` — Dependabot rewrote `# v5` to `# v7.0.0` and left `# v4` stale
  in the same file.
- `skills-evals#38`, `#39`, `#40` — SHAs moved, comments untouched; one action
  at v7.0.1 labelled `# v4.3.1` and `# v6.0.0` in the same repo.
- `_agent-guidance` `agents-md/base.md`, "Pinning GitHub Actions" — the
  fleet-wide carrier for this rule.
