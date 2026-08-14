# Experiment E3 — what the conformance gate is blind to

**Question.** Does the repo's existing CI gate — `claude plugin validate` — detect
malformed skill frontmatter, and if not, what is it blind to?

Context: issue #55 found unparseable SKILL.md frontmatter that had already
propagated into a consumer repo, past a CI job whose entire purpose is validating
this marketplace. Either the gate ran and missed it, or the gate cannot see it.

Run 2026-08-14, Claude Code `2.1.223`, inside a Claude Code on the web container.

## Result 1 — `--strict` is unarmed, and only for a trivial reason

```
$ claude plugin validate .            → exit 0   (3 warnings)
$ claude plugin validate . --strict   → exit 1
  > plugins[0] plugin.json → version: No version specified. …
  > plugins[1] plugin.json → version: No version specified. …
  > plugins[2] plugin.json → version: No version specified. …
  × Validation failed (--strict treats warnings as errors)
```

One warning per bundle, all the same warning. After adding `"version": "1.0.0"` to
all three manifests: `claude plugin validate <copy> --strict` → **exit 0**. So
`--strict` was one three-line edit away from being switchable on the whole time,
and CI has been running the lenient mode by default rather than by decision.

## Result 2 — the gate is structurally blind to SKILL.md defects

A throwaway copy of the whole marketplace was mutated so that
`plugins/adam/skills/writing-adrs/SKILL.md` carried **both** defect classes at
once: (a) unparseable YAML frontmatter — an unquoted `: ` inside `description` —
and (b) a frontmatter `name` of `TOTALLY-DIFFERENT-NAME`, not matching its
directory.

```
$ claude plugin validate vtest --strict
Validating marketplace manifest: …/vtest/.claude-plugin/marketplace.json
√ Validation passed
exit=0

$ python3 -c "yaml.safe_load(frontmatter)"
PyYAML: ERROR -> mapping values are not allowed here
```

Green, with `--strict`, on a skill whose frontmatter no YAML parser will load.
`claude plugin validate` reads the marketplace and plugin **manifests**; it never
opens a `SKILL.md`. No amount of `--strict` would have caught the frontmatter that
issue #55 watched propagate into a consumer — this is a blind spot by
construction, not a missing flag.

## Result 3 — the payload-reference check needs precision, established empirically

A naive rule — any backticked or linked relative path whose first segment is a
payload dir — produced **5 false positives out of 8 findings** in this repo alone:
prose listing another skill's files, prose describing another repo's script, an
illustrative `tests/foo.spec.js` in a sentence about renaming, a `<placeholder>`
tokenised into `scripts/<the`, and bare directory mentions ending in `/`.

Restricting the gating rule to references **inside fenced code blocks**, and
excluding candidates that end in `/` or contain `< > * ? [ ] $ { }`, yields across
all three registries:

| Registry | Gating findings | Which |
|---|---|---|
| agentskills | 2 | `wj-next-break` → `scripts/next_break.py`, `scripts/test_next_break.py` |
| cms-platform | 1 | `admin-config-render` → `scripts/render-decap-config.rb` |
| adamdaniel.ai | 1 | `admin-config-render` → `scripts/render-decap-config.rb` |

Zero false positives.

The `admin-config-render` pair is the interesting row. The referenced script exists
at **cms-platform's repo root** but not inside the skill directory — so the
reference resolves only from the repo root, by accident of where the skill happens
to live. The identical reference then rides the vendored mirror into adamdaniel.ai,
where the file exists nowhere at all. That is issue #55's third defect class —
repo-relative payload paths that dangle the moment a skill is copied — caught in
the act, mid-propagation.

## Coverage, in one table

| Defect class | Seen by `claude plugin validate --strict`? |
|---|---|
| plugin manifest missing `version` | **yes** — exit 1 (Result 1) |
| SKILL.md frontmatter unparseable | no — exit 0 (Result 2) |
| SKILL.md `name` ≠ directory name | no — exit 0 (Result 2) |
| payload path that dangles once copied | no — the gate never opens a SKILL.md |

## What this changes

**`--strict` is necessary but nowhere near sufficient.** Arming it is worth doing —
it costs three lines and closes the manifest lane — but Result 2 shows the gate
cannot see the defect class issue #55 was actually about, and no future flag will
change that: it is a manifest validator, and the defects live in files it does not
read. A separate tool that opens every `SKILL.md` is the only thing that can cover
this, which is what `scripts/check_skills.py` is for.

**A payload-reference check is only worth gating on if it is precise.** Result 3's
naive rule was wrong five times in eight; a check with that hit rate gets muted, waived
into irrelevance, or routed around within a release or two — at which point it is
worse than no check, because it still reads as coverage. Precision was not a
polish pass here, it was the difference between a gate and a nuisance. The same
failure mode is the predictable one for a CRLF-naive hash comparison of vendored
mirrors: it would fire on every Windows-touched copy, be correct about nothing
anyone cares about, and be switched off.
