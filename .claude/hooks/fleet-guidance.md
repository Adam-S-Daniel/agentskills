# AGENTS.md

> **Managed by [`_agent-guidance`].**
> Edit only below the `## Repo-specific additions` header.
> Everything above it will be overwritten on the next sync.

This block is deliberately short. It carries the things that are **specific to
this account and learned the hard way** — incidents, fleet policy, machine
layout. It does not restate general engineering practice, and it does not
describe anything you can learn by reading the repo. Depth lives in each repo's
`docs/` and in the skills registry; follow the pointers when the work touches
that area.

## Working in these repos

- Fix what was asked. No speculative features, premature abstractions, or
  unused helpers.
- Prefer editing an existing file over creating a new one.
- Every public interface change updates the corresponding tests.
- Run the existing test suite before calling a task complete, and say plainly
  what you ran. New behaviour gets a test; a bug fix gets a regression test.
- Tests must be deterministic — no sleeps, no network, no reliance on
  wall-clock time.

## Anything you name gets its link

Any noun the reader might want to open gets its URL in the same sentence — the
one who wrote the sentence is the one holding the id. It covers three shapes,
and the rule keeps being applied to the first while the other two slip:

- **What you hand over**: an approval, a PR to merge, a red run, a setting to
  flip, a dashboard to read. (2026-08-27: a session reported jodidaniel.com#176
  as waiting on a human approval across three turns, each time naming no
  location. The reply was "What human approval? Link me.")
- **What YOU are waiting on**: "waiting on CI", "the sync is running", "once the
  verifier finishes". Link it every time you name it, not once when you start it
  and never again. A status update whose nouns cannot be clicked has handed over
  a feeling of progress and no way to check it.
- **What you cite as already DONE**: "tracked in the issue", "the changelog
  records it", "see the ADR". Evidence is the noun that most needs a link — its
  whole job is to let someone confirm you did what you say. (2026-08-29: a
  session closed a remediation with "documented in the issue, the release
  changelog" — three nouns, no links, no statement of what the item WAS. The
  reply was "What is the item/blocker? Link to it please.")

Four rules about the link itself:

- **Link the surface that decides, not its parent.** A required environment
  review lives on its own job page, `.../actions/runs/<run_id>/job/<job_id>`;
  the PR only shows it pending. Resolve the run `waiting` on the CURRENT head —
  one carried from an earlier head governs nothing. Where there are two surfaces
  (the regression gate is approvable from Actions *and* from `/admin/reviews/`),
  give both and say which you verified.
- **A link is not a description.** "#329's blocker" still costs the reader a tab,
  which relocates the cost rather than paying it; one clause of identification
  travels with the link. A bare `repo#123` autolinks only inside that repo's own
  threads — in chat, another repo's issue, an email or a doc it is dead text, so
  cross-repo references get the full URL.
- **No URL? Say so, and give what it does have.** A local task, a subagent, a
  file on a machine only you can see: "No link — local task `abc123`, output at
  `/tmp/…`" satisfies the rule. "I'm waiting on the verifier" does not, and is
  worse than silence — it reads as something the operator could go and look at.
- **Stop naming it once it stops blocking.** A check-in on a PR that has merged,
  a poll for a run that finished — cancel it and say so. Naming a blocker that no
  longer blocks is the same defect pointed at the past.

## Finding your unknowns

Output quality on a non-trivial task is bounded by how well the ambiguities got
resolved — and most of them surface *during* implementation, not before it. So
treat unknown-hunting as part of the work, not a phase that ends at the plan:

- Before building: name what you don't know. Prefer a reference in **code** — an
  existing implementation to mirror, a failing test, a rubric, an HTML mockup —
  over a prose description of the same thing.
- While building: keep a running note of decisions that departed from the plan
  and edge cases you hit. Surface them; don't silently absorb them.
- After building: be able to explain what changed and why it is correct.
- Durable findings go in the **repo**, not in agent memory — an environment
  quirk, non-obvious wiring, where a source of truth actually lives, a
  sequencing constraint. Repo files version with the code and every person and
  every harness that opens the repo sees them; agent memory is per-agent and is
  silently missed by the next session. A fleet-wide rule goes in
  `_agent-guidance`'s `agents-md/base.md`, a repo fact below the
  `## Repo-specific additions` marker, a reusable procedure into the skills
  registry. A memory note is a supplement, never the only copy.

The full workflow (blind-spot pass, self-interview, implementation notes,
post-hoc explainer) is the **`finding-unknowns`** skill in the registry. Reach
for it on unfamiliar code, a new domain, or anything with subjective acceptance
criteria.

## Workstation layout

Repo locations are host-specific — match the convention of the machine you're on
(on Windows, check `$env:COMPUTERNAME`).

- **`ZENDA`** (Windows): local clones live under `D:\repos\<github-owner-or-org>\<repo>`
  (for example `D:\repos\adam-s-daniel\wsl-automation`). Clone new repos there, and
  assume existing repos live there rather than under the user profile
  (`C:\Users\<user>\...`).

## Sessions get cut off

**`ZENDA` drops sessions mid-task, frequently.** Assume any run can end between
one tool call and the next, and keep the work recoverable throughout rather
than only at the end.

- **Commit and push as you go**, on a branch. A pushed branch survives the
  laptop; the conversation, a dirty tree and a worktree do not — a worktree can
  be deleted with the session that made it. Small commits *are* the checkpoint.
- **Persist the expensive part**, which is the investigation and not the diff:
  the root cause, the baseline test result, the option already ruled out. A
  fresh session can regenerate a patch quickly; it cannot cheaply re-derive why
  the obvious fix was wrong. Put it in the commit message, the PR body, or an
  ADR — all of which outlive the context window. Chat does not.
- **Say where things stand before a long step** — a full test suite, a CI
  watch, a wide refactor — so a resumed session starts from a statement of what
  is done and what is next, not a reconstruction of it.
- **Report a resume pointer, not just an outcome:** branch, PR number, worktree
  path, and the next command to run.

## Security

Standard practice applies without being restated here. These are the ones with
teeth in this account:

- Validate anything that crosses a trust boundary — user input, API responses,
  file contents.
- Never build SQL, shell commands, or HTML by string-concatenating untrusted
  data. Use parameterized queries, shell arrays, and context-aware escaping.
- Never commit secrets, credentials, or `.env` files.
- Never disable TLS verification, authentication, or CSRF protection.

## Data exposure in CI and public repos

Treat CI run logs, job summaries, artifacts, workflow run pages, and git history
as **public** on a public repo. (Real incident: a workflow printed the owner's
email addresses and their correspondents' into a public Actions log.)

- **Never print personal or sensitive data to a log** — no emails, contacts,
  names, IDs, mailbox sizes/counts, tokens, or anything "useful to an attacker or
  scammer." Deliver sensitive results out-of-band (e.g. email the account itself,
  write to a private store) and log only a non-identifying status line.
- **Don't interpolate `${{ inputs.* }}` / `${{ github.event.* }}` into a `run:`
  block** — the rendered command is echoed to the log. Read inputs from
  `$GITHUB_EVENT_PATH` inside the script and `::add-mask::` sensitive values
  before use. `::add-mask::` only scrubs the log *stream*, not other surfaces.
- **Put sensitive config in secrets, not plaintext inputs or `vars`.** Only
  secret *values* are masked in logs.
- **Sanitize error output** — never dump an API/HTTP response body on failure (it
  can quote personal data); reduce it to a status code + machine error type, and
  keep the data-bearing serialization/call inside the try/catch.
- **Least privilege:** set `permissions:` to the minimum (usually
  `contents: read`) and require approval for outside-collaborator fork PRs.
- **Test fixtures use reserved `example.com` / `example.net` domains only** —
  never a real address; fixtures get committed and logged.

### git history & metadata
- **Sanitize before the first commit.** Fixing the current file does not remove
  data from history. If sensitive data was committed, rewrite history to drop the
  commits, delete every ref that points at them (branches, tags, **PRs**), and
  force-push. GitHub garbage-collects unreachable objects on its own schedule
  (days to weeks) — until then they remain reachable *by SHA* — and you can ask
  GitHub Support to expedite for a public repo. (This is the deliberate exception
  to "don't force-push"; it is a security remediation.)
- **Commit with the GitHub `…@users.noreply.github.com` identity** on public
  repos so a real email is not baked into commit author/committer metadata.

## Network allowlists live in `_agent-guidance/docs/reference/`

Two egress allowlists are kept there as **reference copies**, each with a
sidecar changelog carrying the per-domain justification an allowlist line has
no room for. Neither is loaded by anything — read them when you are changing
one, and add a changelog entry when you do.

- `network-allowlist-claude-environments.txt` — the domain list in force in the
  Claude Code cloud environment named `My Whitelist`. **In force**; the
  authoritative copy is the environment dialog at claude.ai/code, and this file
  tracks it.
- `network-allowlist-github-runners.txt` — a **proposed** list for CI. Not
  enforced anywhere and not enforceable on a standard runner: GitHub-hosted
  runners have unrestricted egress, and roadmap #821 for native outbound
  control is closed as not planned.

Each `.txt` has a `.CHANGELOG.md` beside it whose header states what an entry
must carry. Two traps both files exist to record: a `*.example.com` line does
**not** match the apex `example.com`, and the Claude environment's "also
include default list of common package managers" checkbox silently adds ~200
more domains, so a line that looks missing may be covered by it.

## Automation vs branch protection

Fleet repos enforce PR-only default branches via ruleset, managed as code in
`repo-settings` (see its ADR 0001). Design automation accordingly:

- Never design a bot that pushes to a protected default branch ad hoc — the
  push is rejected (GH013), even from the repo's own workflows.
- Generated data (badges, run summaries, reports, dashboards) belongs on a
  dedicated unprotected results branch (e.g. skills-evals' `eval-results`);
  consumers read from that branch and treat its content as untrusted.
- The rare bot that genuinely must write to a default branch needs a ruleset
  bypass actor declared in repo-settings' `fleet.yml` — never a hand-granted
  UI bypass (the drift report flags those). The AGENTS.md sync App is the
  standing example.
- PR + auto-merge is not a sanctioned bot-write path for fleet repos; the
  cms-platform-managed repos (outside the fleet ruleset) use it by their own
  design.

### A required status check gets no `concurrency` group

A job that publishes a **required** status context and can fire more than once
on the same head sha — label events, an `opened` + `synchronize` burst, any
multi-event trigger — gets no `concurrency` block at all.

- GitHub picks **non-deterministically** between a cancelled run and a
  successful one for the same context + sha. When cancelled wins the PR is hard
  blocked: the merge API returns `405 Required status check "<ctx>" is
  cancelled`, and nothing overrides it — not native auto-merge, not an explicit
  merge call, not a nudge bot. The PR looks all-green and simply never lands.
- **`cancel-in-progress: false` is not "run them all."** GitHub keeps the
  in-progress run plus only the *latest* pending run in the group and cancels
  the other pending duplicates, so a same-sha burst still leaves cancelled runs
  behind. Flipping that flag is the fix that looks right and changes nothing.
- Same mechanic on any shared lane: when one push drives two workflows into one
  group, the older pending sibling is cancelled. Make the triggers pairwise
  disjoint — a shared group only serialises runs that already arrive apart.
- Jobs triggered only by `push` / `synchronize` — each a new sha — are safe to
  cancel and keep `cancel-in-progress: true`.
- Lock the invariant with a test that **parses** the workflow YAML (the `yaml`
  package — never a regex or line scan, which reads clean on text it cannot
  see), so the block cannot come back.

## Two GitHub connectors, and which one you are holding

A session here can see **two** GitHub MCP servers at once. They authenticate as
the same person, so `get_me` will not tell them apart, and the tool names do
not say which is which. Establish it before you reach for one:

- **`mcp__github__*` — session-provisioned.** It does NOT appear in
  `ListConnectors`; the remote environment supplies it and the session's own
  system prompt points at it. It is the **only** one with GitHub Actions tools
  (`actions_list`, `actions_get`, `actions_run_trigger`), workflow-run and
  job-log introspection (`get_check_run`, `get_job_logs`), auto-merge control,
  and review-thread resolution. Read that as the ACTIONS side specifically, not
  as "all CI reads" — the next bullet is where a pull request's own check runs
  live. Its reach is the session's attached repositories; `add_repo` widens it
  mid-session.
- **`mcp__github-mcp__*` — the claude.ai org connector `github-mcp`.** It lists
  in `ListConnectors` as `connected: true`. Its tool set is a **strict subset**
  of the above: same reads, same PR and issue writes, same `merge_pull_request`,
  `push_files` and `delete_file` — and no Actions, no job logs, no auto-merge,
  no review threads. Its reach comes from a GitHub App installation allowlist
  that is INDEPENDENT of the session's attached repos. **Probe for it by
  connector NAME, never by that prefix from memory.** This file named it
  `mcp__b26ebb34-…__*` until 2026-08-28, when a live session measured it as
  `mcp__github-mcp__*`; a run that searches its tools for the remembered
  literal matches nothing, concludes "no connector present", and stands down
  with a fully working one sitting right there. The prefix has moved once
  already — assume it can move again, and probe both forms by name.

Three consequences, and the first is why this section sits where it does:

- **The CI boundary runs through the middle of the org connector, not around
  it.** It CAN check a pull request: `pull_request_read` accepts
  `method: "get_check_runs"` (the head commit's check runs, with their
  conclusions) and `method: "get_status"` (the combined commit status).
  Measured 2026-08-28 against `_agent-guidance` #83 — four check runs came
  back, `success` and `skipped`. What it genuinely lacks is the **Actions**
  side: no `actions_list` / `actions_get` / `actions_run_trigger`, no
  `get_check_run`, no `get_job_logs` — so it cannot dispatch a workflow, read
  a workflow RUN, or pull a failed job's log — and with no
  `enable_pr_auto_merge` a merge under it is synchronous (check, then merge,
  in the same run) rather than armed and walked away from. Read BOTH methods,
  for the reason `"The watch finished" is not "CI passed"` gives below: #83
  answered `get_status` with `pending` over zero statuses at the same moment
  every check run on it was green, so either method read alone misreports.
  This bullet used to say the org connector "can merge a pull request but it
  cannot check one" — wrong, and wrong in the expensive direction, because it
  tells an org-connector-only session that it must not merge and so disables
  a capability the operator deliberately granted it.
- **Fewer tools is not less dangerous.** Both connectors merge, push and
  delete. The subset one is the connector whose reach you cannot infer from the
  session's repo list, so a write through it can land somewhere the session was
  never scoped to. Measured 2026-08-19: `github-mcp` 404s on the private
  `repo-settings` even though the account can push there, while both read a
  public non-attached repo fine.
- **A 404 means "not visible to THIS connector"** — never that a repo or file
  does not exist. Re-check on the other one before concluding anything; the
  next section is how to tell the two apart.

Prefer `mcp__github__` for everything. Reach for `github-mcp` only when the
other genuinely cannot see a repo, and say so out loud when you do. When you
report a verification, name the connector it came from.

## A GitHub 404 means "not authorized", not "not there"

GitHub answers **404 rather than 403** when a caller is not authorized to know a
private repo exists — it will not confirm the repo either way. So a 404 from any
GitHub API or MCP call is ambiguous by design: either the thing is gone, or the
credential simply lacks that repo. The body says "Not Found" in both cases,
which is why the wrong reading — telling someone their PR was deleted — is the
easy one to reach for.

- **Probe the repo, not the object.** If `GET /repos/<owner>/<repo>/pulls` 404s
  as well, the whole repo is invisible to that credential: a scope gap, not a
  missing PR. If the repo answers and only the object 404s, it is genuinely
  gone.
- **Try the other connector before concluding anything.** The two servers above
  do not share an installation, so one can be blind to a repo the other reads
  fine. (Real incident, 2026-08-19: a mid-session MCP reconnect brought up a
  second GitHub server whose credential could not see a private repo. Every call
  against it 404ed — including on a PR the *other* connector had read
  successfully minutes earlier — and the repo was neither deleted nor unshared.
  `add_repo` reported it already attached, which is about session scope and does
  not widen a connector's own installation.)
- **Git is a separate credential path** and often still works when the API
  token does not. `git ls-remote origin '<ref>'` answers "does this branch
  exist"; `git merge-base --is-ancestor <sha> origin/main` answers "was it
  merged". Neither touches the API, so both stay available to report real state
  while a connector is blind.
- Never report a repo, PR, or branch as gone on a 404 alone. Say which
  credential could not see it, and what you checked with.

## The fleet spans TWO owners, and a scoped search will not say so

`Adam-S-Daniel` and `jodidaniel`. Both are named in `SYNC_OWNERS` in
_agent-guidance's `sync.yml`, `drift-report.yml` and `skills-lock-bump.yml`,
and every fleet-wide script reads that variable rather than assuming one owner.
An ad-hoc query that does assume one is answering a narrower question than the
one you asked — and it answers it confidently.

That is what makes this worse than the 404 above. A 404 at least looks like a
problem. A search scoped to one owner returns a **plausible, complete-shaped
result set**: no error, no empty page, nothing to prompt a second look.

Measured 2026-08-25: `search_code filename:skills.lock user:Adam-S-Daniel`
returned five repos, and that became a report that jodidaniel.com "has NO
`skills.lock`, so it receives no bundles at all." It had one — a federated lock
of 22 skills, the bootstrap hook, and the `settings.json` wiring. The repo is
`jodidaniel/jodidaniel.com`, so the query could not have found it under any
circumstances. `repos.yml` also said `lock: committed` in plain English, one
`grep` away.

- **Enumerate owners; never hardcode one.** `SYNC_OWNERS` is the list, and it
  is two long today precisely so nobody has to remember that it is.
- **Prefer the fleet's own registries to a search index.** `repos.yml`,
  `fleet.yml` and `cron_coverage.fleet` are lists a human maintains and CI
  checks. A code-search result is a snapshot of an index that is not
  exhaustive even within one owner — a zero result is weak evidence in a way a
  missing entry in one of those files is not.
- **To ask whether repo X has file Y, ask the repo.** `git ls-remote`, a
  shallow clone, or the contents API answers about the repo; a search answers
  about the index.
- **Your session's reach is not the fleet's shape.** Tooling may be scoped to
  one owner — hosted sessions refuse cross-owner repo attachment — so "I cannot
  see it" and "it does not exist" have to stay separate sentences. Say which
  one you mean, and say what you checked with.
- **The DENOMINATOR is the part that lies.** Enumerating what to verify from
  whatever happens to be checked out locally answers a narrower question than
  the one you asked, and does it in both directions at once. Measured
  2026-08-29 while verifying an AGENTS.md sync: three local checkouts were
  repos DELETED from GitHub, counted as consumers and reported as missing the
  text (false RED), while a real consumer that session had never attached was
  absent from the set entirely — so the run printed `ALL PROPAGATED` over 17 of
  18 (false GREEN). The false green is the dangerous half: a clean verdict over
  an incomplete list, with nothing on screen to prompt a second look. It is the
  same defect `repos.yml`'s `cron_coverage` block exists to prevent for the
  cron audit (issue #37). Take the denominator from a registry or from the
  remote — never from the disk — and say which one you used.

## "The watch finished" is not "CI passed"

Never read CI pass/fail off a watch command's exit code, or off the fact that it
returned. Three failure modes stack: in `cmd | tail` the shell's `$?` is
`tail`'s — always 0 — masking the non-zero from `gh pr checks`; a backgrounded
watch reports that same pipeline code, so its "completed (exit code 0)"
notification says nothing about the build; and `tail -N` can show only the
passing and skipping lines while the FAILURE lines scrolled out of the window,
so eyeballing it looks green too. (Real incident: all three lined up on one PR —
e2e and lint were FAILURE while the session reported CI green and moved on.)

- Capture the real code with `${PIPESTATUS[0]}`, or don't pipe the watch at all.
- After **any** CI watch, query the conclusions explicitly and report the parsed
  result before acting on it:

  ```bash
  gh pr view <n> --repo <owner>/<repo> --json statusCheckRollup --jq \
    '.statusCheckRollup[] | (.conclusion // .state) as $c
     | select($c != null and $c != "SUCCESS" and $c != "NEUTRAL")
     | "\(.name // .context): \($c)"'
  ```

  A check run carries `.conclusion`, a legacy commit status carries `.state` —
  filter on only one and the other's failures read as clean.
- Treat "watch done" as "now verify", never as "passed". Don't launch a watch
  and go passive without a definite verify-the-rollup step on resume.

### A pipe into `grep -q` is a race, and one passing test proves nothing

`echo "$big" | grep -q` under `pipefail` is the same trap with a timer on it.
`grep -q` exits at the first match; once the payload passes the 64 KiB pipe
buffer the writer still has bytes to write, takes SIGPIPE, and `pipefail` turns
141 into a false negative — a marker that IS present reads as absent.

It defeated its own investigation for a week (issue #81), because the disproof
was one probe per size. Twenty trials per size against the real file: 48 kB
0/20, 56 kB 0/20, 64 kB 0/20, 72 kB 2/20, 95 kB 18/20. At 95 kB a single shot
passes about one time in ten, which is exactly what that issue recorded as
"passed at every size". In production it presented as the largest `AGENTS.md`
in the fleet, and only that one, reporting false drift.

- **Feed the data as an argument or a here-string, never through a pipe** into
  a command that exits early: `grep -qxF -- "$m" <<<"$s"`, or `grep -qxF -- "$m"
  file`, or pure bash `[[ $'\n'"$s"$'\n' == *$'\n'"$m"$'\n'* ]]`.
- **A size-dependent bug needs trials, not a probe.** If what you are clearing
  could be a race, one green run is not evidence — say how many trials you ran.
- The same shape is safe when the value is captured inside `$( ... || true )`,
  because the status is discarded. That is correct by accident, so say so where
  you find it rather than leaving the next reader to re-derive it.

### `gh api ... --jq` on an HTTP error prints the raw error body, not a filtered result

On a non-2xx response, `gh api` skips the `--jq` filter entirely and writes the
API's raw error JSON to **stdout** — not stderr — while still exiting non-zero.
A caller that does `out=$(gh api ... --jq '.foo') || true` to tolerate a
missing resource then captures that raw error body instead of an empty string:
the fallback swallows the exit code, not the payload it was meant to guard
against.

This has already been independently rediscovered twice, in two different
repos, which is the clearest signal a rule belongs here rather than in either
repo alone: it once silently broke `sync.sh`'s `default_sections` (recorded in
`agentskills`), and `_agent-guidance`'s own `drift-report.sh` documents the
same shape in a code comment written to tell a real 404 apart from an API
error.

- **Discard output on failure explicitly, never with a bare `|| true`:**
  `out=$(gh api ... --jq '.foo') || out=""`.
- The exit code is not enough on its own — `|| true` clears the *status* but
  leaves `$out` holding whatever `gh api` printed, error body included.

## A successful `git push` does not mean your commit exists

Same shape as the trap above — an exit code that belongs to a different command
than the one you meant to measure — but it bites at the other end of the cycle,
and it is worse because the artifact it leaves behind looks finished.

A pre-commit hook that refuses the commit does not stop the push. `git commit`
exits non-zero and writes nothing; the `git push` that follows then pushes the
branch at whatever HEAD still is — the base commit — and prints exactly what a
real push prints:

```
 * [new branch]      claude/my-branch -> claude/my-branch
branch 'claude/my-branch' set up to track 'origin/claude/my-branch'.
```

The branch is real, a PR can be opened on it, and the diff is empty. Nothing in
the push output is false; it just answers a question you did not ask.

**This is a hosted-session problem specifically.** The fleet's `secrets-scan`
pre-commit guard (delivered by cms-platform's `dev-hooks-sync.yml`) requires
`gitleaks` on `PATH` and FAILS CLOSED when it is missing — correctly, since a
security gate that skips when absent is not a gate. A fresh container has no
`gitleaks`, so every commit in one is refused until you install it. (Measured
2026-08-25 on adamdaniel.ai: the hook printed its install instructions, the
commit never happened, `git push -u` reported a new branch, and
`git log --oneline -1` was still the base merge commit.)

- **Verify the commit, not the push.** `git log --oneline -1` should show your
  message, and `git status --short` should be clean. Or compare directly:
  `git rev-parse HEAD` must differ from `git rev-parse origin/<base>`.
- **`&&`-chain commit into push** so a refused commit stops the chain. A
  newline or `;` between them does not — that is what turns a blocked commit
  into a pushed branch.
- **Install the tool; do not reach for the bypass.** `SKIP_SECRETS_SCAN=1`
  exists for an emergency, and a missing binary in a container you control is
  not one — a release binary is one `curl` away, and CI scans the PR either
  way, so bypassing locally only moves the finding later.
- **A push can also carry the WRONG ref, and the two checks above both pass.**
  `git push -u origin <name>` pushes the LOCAL BRANCH of that name, which need
  not be the branch you are on. If one already exists — a stale leftover from an
  earlier session — your commit stays where you made it and the push updates
  something else, successfully. (Measured 2026-08-29 in this repo: `checkout -b`
  failed on a dirty tree, its fallback `checkout` failed too, so HEAD never left
  `main`; the commit landed there, `push -u origin claude/…` pushed a stale
  local branch of that name, and printed the ordinary success. `git log
  --oneline -1` showed the commit and `git status --short` was clean — both true,
  both measuring the wrong thing.) The only check that settles it names the
  commit AND the remote branch:
  `git merge-base --is-ancestor <sha> origin/<branch>`. Run it after every push.

## Dependency updates

Dependabot runs with a **minimum package age** (`cooldown`) so an unattended
merge still gets a cooling-off period: `default-days: 7`, `semver-major-days: 30`.
Two things about that setting are easy to get wrong:

- It applies to **version** updates only. A security advisory bypasses cooldown
  entirely and opens immediately — the wait never delays a vulnerability fix.
- An unset `cooldown` is **not** "no wait": GitHub applies an implicit 3-day
  minimum age to version updates. Writing 7 is a raise from 3, not from zero.

`semver-minor-days` / `semver-patch-days` are deliberately left undefined —
they fall back to `default-days`, and spelling them out only invites drift.

The window is not only Dependabot's. A package you add or bump **by hand** mid-task
is the case with no automation watching it: check the publish date
(`npm view <pkg> time --json`), take the newest release that has already cleared
the 7 days rather than the freshest one, and pin it exact (no caret) so `npm ci`
cannot drift onto a version that has had no cooling-off at all.

## A name you choose becomes data a scanner reads

gitleaks' `generic-api-key` rule fires on a **keyword** next to a
high-entropy value. The keyword list is short and ordinary:

```
access  auth  api  credential  creds  key  passwd  password  secret  token
```

Nothing warns you that those words are reserved, because they are not — they
are only reserved *in the position a scanner looks at*. Name a skill, a config
key, a job output, an artifact or a fixture with one of them, and every
generated file that serialises `name: value` alongside a hash, id or digest
starts looking like a leak.

That is not hypothetical. A skill named **`cms-platform-secrets`** put the line
`"cms-platform/cms-platform-secrets": "<64-hex>"` into `skills.lock`, which is
generated, committed, and scanned. Both consumer sites went red on every push
to `main` — adamdaniel.ai for eight consecutive pushes, each one a blocked
editorial publish. An audit of all 34 skill names across both registries found
exactly one hit: that name. One word, one outage.

The shape that makes it hard to catch:

- **The repo that chooses the name is not the repo that breaks.** cms-platform
  named the skill; the two sites that install its bundle are what went red.
  cms-platform's own lock lists only `adam/*` skills, so it stayed green and
  the author had no signal at all.
- **A pull request cannot see it.** The PR lane scans `base..head`; the push,
  schedule and dispatch lanes scan full history. A finding that lives in an
  older commit is invisible to every PR and fires on every push.
- **History is immutable, so the name outlives the rename.** Fixing the
  generator or renaming the skill fixes the working tree and nothing else. The
  old line stays in every clone until history is rewritten.

So:

- **Check a name against that list before you commit to it**, whenever the name
  will land in a generated or serialised artifact. It costs one grep. Prefer a
  name that says what the thing is for over one that names the sensitive noun —
  `consumer-repo-provisioning` carries the same meaning as
  `cms-platform-secrets` and trips nothing.
- **Fix it at the source, not with an allowlist.** An allowlist entry is
  per-repo; a `.gitleaksignore` fingerprint is `<commit>:<file>:<rule>:<line>`
  and commit shas are repo-unique, so it cannot be propagated *at all* — copied
  to another repo it names a commit that does not exist there and silently
  suppresses nothing while looking like coverage. One rename immunises every
  consumer at once; N exclusions immunise N repos until the next one adopts.
- **Do not lean on a scanner's internals.** Labelling a digest `sha256:<hex>`
  currently dodges the rule because `:` falls outside its capture class — a
  welcome side effect, and a bad thing to depend on. Justify such a label as
  self-documentation (it says which algorithm produced the digest); if the
  upstream regex ever widens, every lock in the fleet goes red at once.
- **Suppress by value, never by path.** A `paths` entry does not filter
  findings, it skips the file before any rule runs, so a real credential pasted
  into it is never reported (cms-platform#260 — 29KB of a public repo left
  unscanned that way, suppressing nothing that the value regexes did not
  already cover).

## Pinning GitHub Actions

**Every `uses:` is pinned to a full 40-character commit SHA** — in workflows,
composite actions, and reusable-workflow references alike. The one carve-out,
named below, is a ref into this account's own `cms-platform`, and it covers both
of the shapes such a ref takes. Never a tag, never a branch, never an
abbreviated SHA. A tag is a movable pointer: pinning to one gives whoever can
retag the upstream repo a shell on the runner, holding that job's token.

```yaml
uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11
```

- **A pin carries NO trailing version comment.** `@<sha>` and nothing after it.
  The argument for one is intuitive and will be re-derived by the next person, so
  here is why it lost: forty hex characters do say nothing on their own, but the
  comment is not maintained by anything, and an unmaintained label does not stay
  silent — it starts lying. Dependabot's rewriting of it is **inconsistent**, not
  merely incomplete: measured 2026-08-20, it rewrote a bare `# v5` to `# v7.0.0`
  in GHA-bench#52 while leaving `# v4` stale on the line above **in the same
  file**, and it left every `# vX.Y.Z (YYYY-MM-DD)` comment untouched in
  skills-evals #38/#39/#40 while moving their SHAs. The result in one repo:
  `actions/checkout` at v7.0.1 labelled `# v4.3.1` in one file and `# v6.0.0` in
  two others. A wrong label is worse than no label, because it is read and
  believed — a reviewer trusts it instead of resolving the SHA, and the
  staleness the comment was supposed to advertise is exactly what it hides. The
  SHA is the truth. When you need the version, resolve it:
  `git ls-remote <url> | grep <sha>`, or read the Dependabot PR title.
- **Wait 7 days after a release before adopting it** — the cooling-off above,
  applied by hand. If the newest release is younger than that, pin the previous
  one.
- **Dereference annotated tags.** `gh api repos/<owner>/<repo>/git/ref/tags/<tag>`
  returning `.object.type == "tag"` gives you the tag object's SHA, not the
  commit's, and pinning that fails at runtime. Follow it with
  `git/tags/<that-sha>`, or ask git directly:
  `git ls-remote <url> 'refs/tags/<tag>^{}'`.
- **The one carve-out: a ref into `cms-platform` — a repo this account owns —
  stays on a tag, in either shape that ref takes.** Both of these are correct as
  written, and neither is a SHA-pinning violation to be "fixed" — a reusable
  **workflow**, `Adam-S-Daniel/cms-platform/.github/workflows/<x>.yml@v0.1.88`,
  and a **composite action** referenced from another repo,
  `Adam-S-Daniel/cms-platform/.github/actions/<x>@v0.1.88`. The tag is the
  platform's release identity: `platform-bump.yml` moves the `uses:@` refs, the
  theme gem, `platform.lock` and every `platform_ref:` input to one release in a
  single PR, and `check-platform-pin-consistency.js` asserts each of those refs
  equals `platform.lock`'s `platform_ref` — a SHA in either shape fails that lint
  and strands the bump. The composite shape used to be the exception to the
  exception, pinned by SHA plus a `# vX.Y.Z` comment; that comment was the only
  thing tying such a pin to `platform_ref`, and with the comment gone the tag is
  what ties it. It stops there — nothing third-party is ever a tag, in either
  shape.
- `./local/path` and `docker://` refs have nothing to pin. Leave them.

`sha_pinning_required: true` enforces the rule at the repo level — set by
`repo-settings`' `fleet.yml` for the fleet and `cms-platform`'s
`repo-settings.yml` for the three sites it manages. It governs **actions**, not
reusable-workflow refs: adamdaniel.ai and jodidaniel.com were already enforcing
it at the 2026-07-13 audit and still call 32 tag-pinned cms-platform reusables
apiece, and four repos on the `fleet.yml` default call one each. That is what
makes the carve-out workable — and what leaves a tag in a *third-party* reusable
ref for review, not the setting, to catch.

## Subagent delegation (model routing)

- Don't write code in the main loop: run the implementation in a subagent on an
  appropriately lower-power model (e.g. the Agent tool's `model` override in
  Claude Code; skip if the harness has no subagent support).
- Route by mechanicalness: smallest model (haiku-class) for exactly-specified
  edits — pin bumps, renames, config/doc tweaks; mid-tier (sonnet-class) for
  normal implementation from a clear spec. Escalate rather than ship a wrong
  diff when the task is genuinely subtle (cross-repo invariants, race
  conditions).
- The main loop keeps root-cause investigation, architectural decisions,
  writing the spec, and review of the subagent's diff before commit.
- Delegated work is done when a **verifier exits 0**, not when the report reads
  as finished. Name the exact command in the spec and require its exit code
  back. A subagent that cannot run it reports BLOCKED; a count that disagrees
  with the spec's stated expectation is a stop-and-report condition, never a
  rounding difference.
- **Prove the verifier can fail before you trust it.** A command exiting 0 is
  evidence only if a broken tree makes it exit non-zero; otherwise it is a green
  light wired to nothing. The trap with teeth: `python3 path/to/test_foo.py` on a
  pytest module with no `if __name__ == "__main__"` block imports the file,
  defines the test functions, and exits 0 having run **zero** assertions. It
  looks exactly like a pass. (Real incident, 2026-08-22: appending
  `def test_x(): assert False` to a 53-test file left `python3
  scripts/test_account_zip_selection.py` at exit 0, while `python3 -m pytest` on
  that same file returned exit 1 and "1 failed, 53 passed". Two delegation briefs
  had been citing the hollow command as the gate.) This is the `${PIPESTATUS[0]}`
  lesson above in a second costume — an exit code that belongs to something other
  than the thing you meant to measure. Name the RUNNER in the spec
  (`python3 -m pytest <paths> -q`), never the file, and require the test COUNT
  back beside the exit code: a count is the cheapest proof that anything ran at
  all.
- **A working subagent OWNS the tree — do not commit or push under it.** While a
  subagent is editing, the branch is its workspace, not yours. Push into it and
  the agent's own next `git commit --amend` — correct from where it stands, since
  it has no way to know the commit went public — rewrites a commit that is now
  published, and the branch diverges. Recovery is not a force-push: reset to the
  published tip, re-apply the delta as working-tree changes, and commit it fresh
  so history stays append-only. A working agent's `git checkout -- <file>` will
  also discard uncommitted edits it did not make, including yours. (Real
  incident, 2026-08-22: a checkpoint push landed mid-flight, the agent amended,
  and unwinding it cost a reset-and-reapply.) Wait for the agent to report, then
  push — a clean `git status` plus a recorded result is the signal, not elapsed
  time. Note that a "you have uncommitted changes, please commit and push" stop
  hook cannot see that a subagent holds the tree, so it will advise exactly this
  mistake; say why you are declining rather than complying by reflex.
- **A subagent that has REPORTED can still be holding the tree.** The rule
  above is about one that is still working; this is the half that surprises
  people. The Agent or Workflow call returns when the agent returns, and its
  BACKGROUND CHILDREN are not reaped with it — they keep running, in your
  checkout, writing your files. Measured 2026-08-29 in `_agent-guidance`,
  twice in one session, both after the workflow had printed its result: one
  orphan was looping the test suite, which writes its report into the repo
  root, so two runs clobbered the same artifact and a 999/0 tree reported
  985/14 — fourteen failures in code nobody had touched. The other was
  mutation-testing a source file: a guard I had restored and verified by
  checksum was gone one command later, and the run in between blamed my
  change for the orphan's mutation. The reviewing agent hit the same
  collision from the other side and could not name it — it reported "the old
  bytes" executing in freshly spawned processes while `md5sum` on that path
  across 1779 samples never once showed the old content.
  - **Look for descendants before trusting a long run**
    (`ps -eo pid,etimes,cmd | grep '[y]our-command'`). A result arriving is
    not evidence the processes behind it are gone.
  - **Fingerprint the inputs across the run** — `md5sum` the files under test
    before and after. If they differ, the run measured something you cannot
    name and is not evidence, however green. Two lines, and it is the only
    thing that separates a real regression from a race.
  - **A shared mutable path is what turns a race into a wrong verdict**, so
    prefer a per-run output path over a well-known one. The false RED is
    loud; its false-GREEN twin — a run whose script never executed,
    certifying the previous run's artifact — is silent.
  - **Kill orphans rather than working around them, and say that you did.** A
    result obtained after killing a competing writer is a different
    measurement from one obtained before it.
- **A subagent that goes quiet is not working — check activity, not the clock.**
  Its transcript file's mtime is the real signal; a run journal only writes on
  start and finish, so silence there is expected and proves nothing. Decide the
  staleness threshold in advance, and write the fallback INTO the check-in: what
  to verify by hand if the agent never reports. An indefinite wait on a dead
  agent is the quiet way a gate stops being a gate. (Real incident, 2026-08-22: a
  verification agent wrote for five minutes, died, and left a run looking
  in-flight for an hour.)
- Don't assume the subagent sees this file: general-purpose and custom
  subagents receive the full memory hierarchy (imports included), but
  Explore/Plan-type agents and SDK harnesses with `settingSources: []` skip
  repo guidance entirely. Restate load-bearing constraints (style, test
  command, invariants) in the delegation prompt, and don't hand
  guidance-sensitive work to agents that won't see it.
- **Any prompt that sends a subagent to live-test states the credential
  boundary** — which `HOME`/profile it may use, what it may read, and that it
  must not copy real credentials anywhere to make the test pass. (Real
  incident: a reviewer live-testing a plugin migration in a scratch `HOME`
  copied the account's real OAuth credentials into it. The test worked; nobody
  had asked, and nothing in the prompt forbade it.)
- Supply a throwaway credential, or scope the test to what runs
  unauthenticated. If it genuinely cannot run without a real one, that is the
  operator's call — not a gap for the subagent to close on its own initiative.
- **A tree you made to break something in can still reach production, and the
  obvious fix is worse than the problem in a worktree.** `cp -a` copies
  `.git/config`, so a scratch copy inherits `origin` — measured, 45 such copies
  in one container, 44 of which were never mutated, and the one that was pushed
  14 commits to a real default branch. But `git remote remove origin` inside a
  `git worktree` strips the PARENT checkout's remote, and `git config --local`
  there rewrites the parent's identity (both measured), and the Agent tool's
  `isolation: 'worktree'` means subagents land in one routinely. Before
  disarming anything, run **`/adam:disarm-inherited-reach`** — it carries the
  standalone-vs-worktree test, the reach paths a remote removal does not close,
  and why no in-code guard can substitute.

## Skills ecosystem

- The canonical skills registry is `github.com/Adam-S-Daniel/agentskills`,
  organized as three bundle plugins — `adam` (general-purpose, cloud-safe;
  default-on), `adam-local` (machine-bound), and `fastmail` — each holding
  `skills/<skill>/` directories.
- In Claude Code with the marketplace installed, invoke a skill as
  `/adam:<skill>` (e.g. `/adam:finding-unknowns`).
- Local machines get the marketplace plus per-agent symlinks via that repo's
  `setup.sh`.
- **A `git push` that suddenly fails in EVERY repo is one repo's problem.**
  `setup.sh` registers a GLOBAL sync-skills pre-push hook, so after a bundle
  restructure a stale one keeps pointing at the old plugin path and refuses
  every push from every repo until it is re-registered. The cause is recorded
  in `agentskills`' own guidance, which is the right place for it and the
  wrong place to find it: the symptom shows up in repos whose sessions never
  open that file. Re-run `bash setup.sh` from the registry checkout on the
  machine; nothing in the repo you were pushing from is broken.
- Cloud/ephemeral sessions still get **no** plugins from repo-declared
  settings — that Claude Code limitation (agentskills' `docs/decisions/0001`)
  is unchanged. What changed is that it now has a fix: a repo carrying its own
  `skills.lock` plus the `skills-bootstrap` SessionStart hook installs the
  bundles that lock names directly into those sessions, verified against a
  pinned commit and per-skill digests. Such a session opens with a `skills:`
  verdict naming what loaded, or why nothing did — read it instead of guessing.
- **Adoption is opt-in and double-keyed, and no longer rare.** Delivery needs
  an allowlist entry in `_agent-guidance`'s `repos.yml` AND a `skills.lock` the
  repo committed itself — the fleet sync never writes one, because the lock is
  where a repo declares which bundles it installs (some federate several
  registries). A repo holds both keys, or is mid-adoption holding one, or is
  deliberately out for a reason — a propagation experiment the bundle would
  contaminate, a dormant repo whose sessions never happen. Which of the three
  fits an unfamiliar repo is not guessable: look for `skills.lock`. Bundles
  cost always-on context in every session that carries them, which is why this
  stays a deliberate per-repo decision and not a fleet default.
- New reusable skills graduate **into** the registry (sensitive ones into
  `agentskills-private`) rather than living on in a consumer repo. A long skill
  splits across files rather than growing into one wall of text.

## Two setup gaps you may close, and must not nag about

Skill delivery needs one thing per surface that no repo can commit for itself.
Both are one-time, both are silent when missing, and both are easy to turn into
noise. So: **detect first, and say nothing at all when the check passes.** A
session that opens by telling the operator to paste a script they pasted last
week has spent their attention and taught them to skim the next one.

Run the check once per session and never again that session. Not as a greeting —
answer whatever you were actually asked first — but do **not** wait for the work
to touch skills either. The gap is invisible precisely in the sessions where
nobody is thinking about skills, so a skills-work trigger mostly guarantees the
operator never hears about it. The conditions below are the anti-nag mechanism,
not the timing: they fire only when the gap is real and unfixed, and condition 3
goes false forever the moment the operator closes it.

**Cloud (claude.ai) — PROMPT, never act.** The setup-script field lives in the
environment settings and is not reachable from inside a session, so the most you
can do is ask. Prompt only when **all four** hold; any one false means stay
quiet.

First resolve the project dir, because **`$CLAUDE_PROJECT_DIR` is unset on this
surface** (measured on `remote_mobile`, 2026-08-25): use it when set, otherwise
the nearest ancestor of the cwd that holds more than one repo checkout — in a
hosted multi-repo session that is the cwd's parent, `/home/user`. Substituting an
empty value silently turns conditions 2 and 3 into probes of `/`, which answers
"not multi-repo" and suppresses the prompt — the exact failure this paragraph
exists to prevent. Call the resolved value `$project` below.

1. the session is hosted — entrypoint is `remote*`/`claude_in_slack`/
   `claude-in-slack`/`claude-in-teams`, **or** `$CLAUDE_CODE_REMOTE_SESSION_ID`
   is set (that `OR` is the whole rule; see agentskills' `multi-repo-delivery.md`);
2. the shape is **multi-repo** — `$project` has no `skills.lock` of its own but
   at least one child directory does. A single-repo session needs nothing: its
   own committed `.claude/settings.json` fires the hook;
3. `$project/.claude/settings.json` does not already register a
   `skills-bootstrap` SessionStart hook — **this is the "already there" test, so
   run it before opening your mouth**;
4. some child actually ships `.claude/hooks/skills-bootstrap.sh`, so there is
   something for the wiring to find.

Then say it once, name the snippet's home (`docs/multi-repo-delivery.md` in
agentskills — do not paraphrase it from memory, the project dir is hardcoded
there for measured reasons), and drop it.

**Durable machine — ACT, then one line.** Here you can just fix it, and should.
`claude plugin marketplace list --json` returns `[]` when nothing is configured
(verified), so presence is unambiguous:

- **absent** → `claude plugin marketplace add Adam-S-Daniel/agentskills`, then
  install the bundles the machine wants (`adam` at minimum).
- **present but behind** → `claude plugin marketplace update agentskills`.
- **present and current** → do nothing and say nothing.

"Behind" is a git question, not a guess: the marketplace is a clone under
`~/.claude/plugins/`. **Find it rather than assuming a path** — this account has
already been bitten once by encoding a clone location as a constant — then
compare `git -C <clone> rev-parse HEAD` against
`git ls-remote https://github.com/Adam-S-Daniel/agentskills refs/heads/main`.
Equal means current. The `--json` output may also carry an updated-at or commit
field; read what your build actually prints rather than trusting a field name
from here.

Two things to say afterwards, because both surprise people: an update changes
what loads **next** session, not this one, and a marketplace refresh moves the
three local bundles' contents but not a federated bundle's — that one comes from
its own registry (agentskills' `README.md`).

Neither check belongs in a repo's own `AGENTS.md`, and neither is a reason to
edit a repo. They are session-environment gaps, and the fix lives outside the
tree in both cases.

## Git practices

- Write concise commit messages that explain *why*, not just *what*.
- One logical change per commit.
- Do not amend published commits or force-push shared branches.
- **Merge with a merge commit — `gh pr merge --merge`.** Squash and rebase are
  disabled on every fleet repo, so `--squash` fails rather than falling back;
  do not try it, and do not offer it as a choice. The exceptions are the three
  cms-platform-managed repos (`cms-platform`, `adamdaniel.ai`,
  `jodidaniel.com`), where squash stays enabled because the Decap publish chain
  arms SQUASH auto-merge on every editorial PR and squash is what collapses an
  editor's many per-save commits into one `publish: <title>` commit. Merge
  commits work there too, so `--merge` is the one form that works everywhere.

  Squash is off elsewhere because it is actively unsafe for a repo that pins
  commits by sha: it collapses a branch into a new commit and strands the
  originals on no branch, so a lockfile naming the pre-merge content commit
  (agentskills' `skills.lock`) ends up pinning something a fresh clone of the
  default branch does not contain. Measured on throwaway clones 2026-08-15 —
  `generate_skills_lock.py --check` then fails with `cannot resolve ref`.
  Settings are enforced as code: `repo-settings`' `fleet.yml` for the fleet,
  `cms-platform`'s `repo-settings.yml` for the three above.
