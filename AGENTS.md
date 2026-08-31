<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->
<!-- Source: _agent-guidance -->
<!-- Sections: none -->
<!-- Mode: stub -->

# AGENTS.md

> **Managed by [`_agent-guidance`].**
> Edit only below the `## Repo-specific additions` header.
> Everything above it will be overwritten on the next sync.

## Fleet guidance is delivered once per session — not by this file

The account's full guidance — incidents, fleet policy, machine layout, the
traps that cost real outages — is installed into **user memory**
(`~/.claude/CLAUDE.md`) by the `fleet-memory` SessionStart hook, so it is
loaded **once per session** no matter how many repos are attached. It used to
be inlined here in every repo, which meant a session with 19 repos open
carried 19 identical copies: 332.3k tokens of a 1M window, measured
2026-08-29.

**Check the session-start verdict before you rely on it.** The hook prints one
line:

- `fleet-guidance: installed (v<id>, <n> bytes)` or `fleet-guidance: current` —
  the full guidance is in context. Use it.
- `fleet-guidance: DEGRADED — <reason>` — it is **not** in context. You have
  only what is below. Read `agents-md/base.md` in the `_agent-guidance`
  checkout (or on GitHub) before non-trivial work, and say in your reply that
  you were running degraded.
- `fleet-guidance: skipped (FLEET_GUIDANCE_SKIP set)` — also not in context,
  but by the machine owner's deliberate choice, not a fault. User memory is
  GLOBAL on a durable machine, so the guidance would otherwise load in every
  unrelated project on that box; `FLEET_GUIDANCE_SKIP` opts out and removes any
  block an earlier session installed. Read `agents-md/base.md` the same way you
  would when degraded — just don't report it as a problem or try to "fix" it.

No verdict at all means the hook never ran — treat that as DEGRADED.

## The floor: rules that hold even when the guidance did not load

These are the ones with teeth. They are restated here, deliberately, because a
session that lost the guidance must not also lose these.

- **Branch protection is real.** Fleet repos are PR-only on their default
  branch; a direct push is rejected (GH013), even from the repo's own
  workflows. Never design a bot that pushes to a protected default branch.
- **Every `uses:` is pinned to a full 40-character commit SHA, with no
  trailing version comment.** The one carve-out is a ref into this account's
  own `cms-platform`, which stays on its release tag.
- **Never commit secrets or `.env` files, and never print personal data to a
  CI log** — logs, artifacts and git history on a public repo are public.
- **A successful `git push` does not mean your commit exists.** A refused
  pre-commit hook still lets the push report success. Verify with
  `git merge-base --is-ancestor <sha> origin/<branch>` — it is the only check
  that names both the commit and the ref.
- **"The watch finished" is not "CI passed."** Read the parsed conclusions;
  never infer pass/fail from a watch command's exit code.
- **A GitHub 404 means "not authorized", not "not there."** Never report a
  repo, PR or branch as gone on a 404 alone.
- **The fleet spans TWO owners** — `Adam-S-Daniel` and `jodidaniel`. A query
  scoped to one returns a plausible, complete-shaped, wrong answer.
- **Anything you name gets its link** — what you hand over, what you are
  waiting on, and what you cite as already done.
- **Merge with a merge commit** (`gh pr merge --merge`); do not amend
  published commits or force-push shared branches.

<!-- END MANAGED SECTION -->
## Repo-specific additions

<!-- Add your repo-specific agent guidance below this line -->

### Architecture Decision Records

ADRs live in [`docs/decisions/`](docs/decisions/README.md) (index +
[`0001-consolidate-plugins-into-bundles.md`](docs/decisions/0001-consolidate-plugins-into-bundles.md)).
Skills are grouped into three bundle plugins — `plugins/adam/` (cloud-safe),
`plugins/adam-local/` (machine-bound), `plugins/fastmail/` — each holding many
`skills/<skill>/` dirs; skill directory basenames must stay unique and never
change (they key `setup.sh` symlinks and claude.ai uploads), and the
marketplace `renames` map is append-only.

### Operational gotchas

- Eval skill installs need the nested path: copy `plugins/<name>/skills/<name>/`
  into `.claude/skills/<name>/`. Copying the outer plugin directory buries
  `SKILL.md` and the skill silently never loads.
- `autoMemoryDirectory` accepts only absolute or `~/` paths (no repo-relative
  form). Don't assume the in-repo pattern resolves identically on every
  machine just because repos "live at `~/repos/<name>` everywhere" — see
  Workstation layout above for the counterexample. That exact assumption once
  broke sync-skills: it guessed `~/repos/<name>` ahead of the checkout it was
  actually running from, a decoy outranked the real clone, and `--all`
  enumerated nothing. Check the resolved path on the machine in front of you;
  never encode a repo location as a constant.
- `sync.sh` never force-pushes a stale remote `agents-md-sync/update` branch
  (the push is non-fast-forward and it deliberately won't override). Recover
  by opening a PR from the stale branch and merging it to free the name, then
  re-running sync — don't add `--force` to `sync.sh`, it could discard
  reviewer commits on an open PR.
- The marketplace `renames` map is a one-way door: an object
  `{old: new|null}` (`null` = removed), chains followed to depth 16 by Claude
  Code's plugin resolver (verified at 2.1.211), append-only forever.
  Many-to-one is fine; enable-state merges are first-wins, so a disabled
  surviving plugin silently disables skills migrated into it (the
  fastmail/fastmail-identities caveat — see ADR 0001).
- After any bundle restructure, re-run `bash setup.sh` on every machine right
  away. A stale global sync-skills pre-push hook keeps pointing at the old
  plugin path and fails every `git push` from every repo until re-registered.
- **`python3 scripts/test_<x>.py` cannot fail, so never verify with it.**
  Running a test file directly executes only what is at module scope: unless
  the file ends in an `if __name__ == "__main__"` block that invokes a runner,
  Python imports the module, defines its classes and exits 0 having asserted
  nothing — and it looks exactly like a suite that passed. All 8 files under
  `scripts/` lack such a block, so all 8 are that trap. Exactly one test file
  in the repo does not:
  `plugins/adam-local/skills/rename-pdfs/scripts/test_extract_pdf_context.py`
  ends with `unittest.main()`, and running THAT one directly really does run
  its tests — which is why the habit to build is naming the runner rather than
  auditing each file for a footer. Measured: appending
  `def test_this_must_fail(): assert False` to
  `scripts/test_account_zip_selection.py` left `python3
  scripts/test_account_zip_selection.py` at exit 0, while `python3 -m pytest`
  on that same file exited 1 with "1 failed, 53 passed". This is a live
  instance of *"The watch finished" is not "CI passed"* above — an exit code
  that reports the harness rather than the tests, the `cmd | tail` /
  `${PIPESTATUS[0]}` trap wearing different clothes. The one command that
  actually runs this repo's suite is the one CI runs:
  `python3 -m pytest scripts/ plugins/*/skills/*/tests/ plugins/*/skills/*/scripts/ -q`,
  and read its result from `$?` directly rather than through a pipe.
- **A hosted session starts with NONE of the dev dependencies**, so that command
  and `scripts/check_skills.py` both fail before they check anything. CI installs
  `requirements-dev.txt` per job; nothing installs it here, and no SessionStart
  hook does either (`.claude/settings.json` wires only `skills-bootstrap.sh`).
  Install it first — and expect the plain form to fail on these images:

  ```bash
  python3 -m pip install --ignore-installed PyYAML -r requirements-dev.txt
  ```

  `--ignore-installed PyYAML` is not optional cargo. The distro ships PyYAML
  without installer metadata, so pip refuses with `Cannot uninstall PyYAML 6.0.1,
  RECORD file not found. Hint: The package was installed by debian.` and installs
  **nothing else either** — one unrelated package aborts the whole file, which
  reads as "the repo's dependencies are broken" rather than "one of them is
  undeletable". Measured on `remote_mobile`, 2026-08-25.

  **And the flag really does leave the pinned version importable — measured,
  because the obvious worry is real and the answer is not obvious.**
  `--ignore-installed` does not remove Debian's copy; it installs alongside it,
  so both are on disk and which one wins is a `sys.path` ORDER question rather
  than an install question. It wins: pip lands the pinned wheel in
  `/usr/local/lib/python3.11/dist-packages`, Debian's lives in
  `/usr/lib/python3/dist-packages`, and the former precedes the latter, so
  `import yaml` gives **6.0.3** — `requirements-dev.txt`'s pin — with the full
  suite green (1943 passed, 11 skipped). Verified 2026-08-30 on a hosted
  session that started with `yaml` at Debian's 6.0.1 and `pytest`,
  `jsonschema` and `markdown_it` all absent.

  That distinction is the whole reason to write this down. A hook that
  installed only the three genuinely-MISSING modules would exit 0 and look
  correct while leaving the session testing against unpinned `pyyaml` 6.0.1 —
  the shape that appears to work is the shape that silently reintroduces the
  bug. So the acceptance test is never "the install exited 0"; it is
  `python3 -c "import yaml; print(yaml.__version__)"` reporting the pinned
  version, then the canonical gate read from `$?` unpiped.

  A **venv** is the other measured-good answer and costs a `PATH`/interpreter
  decision every later step in the session has to honour;
  `--break-system-packages` was not tried and the name is the warning.

- **A long background run is not a baseline if you edit while it runs.** The
  suite takes ~5.5 minutes, and several meta-tests READ THE SOURCE FROM DISK at
  run time rather than from the collected module — `test_every_test_this_repo_cites_by_name_exists`
  is one. Edit during the run and it judges the new tree against the old run,
  so a failure it reports may be from a file the run never started with.
  (Measured 2026-08-25: a "pre-existing" red turned out to be a dangling test
  citation written 90 seconds into the run.) Take the baseline before editing,
  or re-run it after — never read one that overlapped the edits.

### One-way doors get an adversarial round

- The irreversible surfaces in this repo are the marketplace `renames` map
  (append-only forever), skill directory basenames (they key `setup.sh`
  symlinks and claude.ai uploads), and an upload to the claude.ai account
  store — which has no delete in the upload path (ADR 0002). A change that
  touches one of them gets an **independent adversarial round before merge**:
  a separately prompted agent whose job is to break the change, not to
  approve it, run against the diff and — where the change is one-way — against
  a live migration in a scratch environment.
- **One clean-looking fix does not end the gate.** Of the four rounds that
  hardened `.claude/hooks/skills-bootstrap.sh`, two found defects *introduced
  by the previous round's fix*, one of them a fresh RCE. Keep going while
  rounds keep finding things.
- Depth, including the negative-control rule that keeps "the exploit stopped
  working" from being mistaken for "the harness stopped working", is in
  [`docs/experiments/E4-federated-bundle-delivery.md`](docs/experiments/E4-federated-bundle-delivery.md).

### Skill changes get recorded, and evals gate them

The method (instrument classes, fixture mining, harness rules) is skills-evals'
`DESIGN.md`, "Scaling to the registry"; these are the two hooks that live here:

- **Every skill-content change appends an entry to
  [`docs/skill-impact.md`](docs/skill-impact.md)** — creations, edits,
  renames, removals, and **rejected proposals** — in the same PR as the
  change (for a rejection, a follow-up commit linking the closed PR). Git
  records what landed; that file records motivation, eval result, and what
  was tried and turned down, which is the part the next session cannot
  re-derive.
- **Graduation gate:** a skill graduating into this registry ships with at
  least one eval fixture in skills-evals (`evals/<skill>/`), and the
  graduation PR's definition of done includes a green `with_skill` arm.
- **Touch gate:** a PR that edits an existing SKILL.md either runs that
  skill's eval (report exit code and counts) or adds its first fixture.
  Skills in DESIGN.md's deliberate-non-coverage table are exempt — the
  table is the record of why.
- **A new or touched skill adds a `PURPOSE.md` beside `SKILL.md`** mapping
  it to the incidents/patterns that motivated it — maintenance context only,
  never loaded at inference. Do **not** mass-backfill `PURPOSE.md` across
  untouched skills: every added file moves that skill's digest, which stales
  `skills.lock` in every consumer until re-pinned. On-touch only.
