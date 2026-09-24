# AGENTS.md

> **Managed by [`_agent-guidance`].**
> Edit only below the `## Repo-specific additions` header.
> Everything above it will be overwritten on the next sync.

Only what is **specific to this account and learned the hard way**; depth
lives in each repo's `docs/` and the skills registry. The fleet-memory hook
delivers this once per session to `~/.claude/CLAUDE.md` (Claude Code) and
`~/.codex/AGENTS.md` (Codex). Keep it under 24 KiB: Codex truncates project
instructions silently at 32 KiB (`project_doc_max_bytes`), and full mode
inlines this file with sections and a repo's additions.

## Working in these repos

- Fix what was asked: no speculative features, premature abstractions or
  unused helpers. Prefer editing an existing file over creating one.
- Every public interface change updates its tests. Run the existing suite
  before calling a task complete and say what you ran. New behaviour gets a
  test; a bug fix gets a regression test.
- Tests are deterministic — no sleeps, no network, no wall-clock time.
- **A lint or check that reasons about code SHAPE** (which functions or
  `test()` blocks exist, what sits inside what, a call's arguments) **parses
  a real AST, never a regex or line scan** — regex cannot see a variable
  value like `` page.goto(`…#/collections/${col}`) ``, the jodidaniel
  host-loop gap in `cms-platform`. Use `acorn`/`acorn-walk` for JS, the
  `yaml` package for YAML; regex only for a lexical token.

## Anything you name gets its link

Any noun the reader might want to open gets its URL in the same sentence:
**what you hand over** (jodidaniel.com#176, 2026-08-27: three turns of
"waiting on a human approval", no location), **what YOU are waiting on**
(every time you name it), **what you cite as DONE** (2026-08-29: "documented
in the issue, the changelog" — no links).

- **Link the surface that decides, not its parent.** A required environment
  review lives on its job page, `.../actions/runs/<run_id>/job/<job_id>`, not
  the PR. Resolve the run `waiting` on the CURRENT head.
  With two surfaces (the regression gate: Actions and `/admin/reviews/`), give
  both and say which you verified.
- **A link is not a description** — one clause of identification travels with
  it. A bare `repo#123` autolinks only inside that repo; elsewhere, full URL.
- **No URL? Say so** — "No link — local task `abc123`, output at `/tmp/…`".
- **Stop naming it once it stops blocking** — cancel the check-in on a merged
  PR or finished run, and say so.

## Finding your unknowns

Ambiguities surface *during* implementation. Before: name what you don't
know, preferring a **code** reference to prose. During: surface departures
from the plan and edge cases. After: explain what changed and why it is
correct. Durable findings go in the **repo**, not agent memory: a fleet rule in
`_agent-guidance`'s `agents-md/base.md`, a repo fact below
`## Repo-specific additions`, a procedure in the skills registry. A note in
`~/.claude/projects/*/memory/` is a POINTER: `metadata.home` names that copy as
`<owner>/<repo>:<path>`, the memory-home Stop hook blocks a session that wrote
one without it, and a `type: user` note about the person is exempt. The
**`finding-unknowns`** skill is the full workflow: use it on unfamiliar code, a
new domain or subjective criteria.

## Workstation layout

Repo locations are host-specific (on Windows, check `$env:COMPUTERNAME`).

- **`ZENDA`** (Windows): clones live under
  `D:\repos\<github-owner-or-org>\<repo>` (e.g.
  `D:\repos\adam-s-daniel\wsl-automation`), never `C:\Users\<user>\...`.
- **Any Windows host with WSL**: PowerShell run from WSL inherits the launching
  session's elevation, and an agent's is not elevated — see the
  `windows-elevation-from-wsl` skill (`adam-local`).

## Sessions get cut off

**`ZENDA` drops sessions mid-task, frequently** — any run can end between
tool calls.

- **Commit and push as you go**, on a branch; a conversation, a dirty tree and
  a worktree do not survive the laptop.
- **Persist the expensive part** (root cause, baseline test result, the option
  ruled out) in the commit message, PR body or an ADR.
- **Say where things stand before a long step** (full suite, CI watch, wide
  refactor).
- **Report a resume pointer:** branch, PR number, worktree path, next command.

## Security

- Validate anything crossing a trust boundary — user input, API responses,
  file contents.
- Never build SQL, shell commands or HTML by concatenating untrusted data;
  use parameterized queries, shell arrays, context-aware escaping.
- Never commit secrets, credentials or `.env` files.
- Never disable TLS verification, authentication or CSRF protection.

## Data exposure in CI and public repos

CI logs, summaries, artifacts, run pages and git history are **public** on a
public repo (a workflow once logged email addresses).

- **Never print personal or sensitive data to a log** — emails, contacts,
  names, IDs, mailbox counts, tokens. Deliver results out-of-band (email the
  account itself); log a non-identifying status line.
- **Don't interpolate `${{ inputs.* }}` / `${{ github.event.* }}` into a
  `run:` block** — the rendered command is echoed. Read inputs from
  `$GITHUB_EVENT_PATH`; `::add-mask::` sensitive values first.
- **Sensitive config goes in secrets**, not inputs or `vars`.
- **Sanitize error output** — never dump an API/HTTP body; log a status code
  plus machine error type, data-bearing call inside the try/catch.
- **Least privilege:** `permissions:` at the minimum (usually
  `contents: read`); require approval for outside-collaborator fork PRs.
- **Fixtures use reserved `example.com` / `example.net` domains only.**
- **Sanitize before the first commit.** Committed sensitive data means
  rewriting history, deleting every ref pointing at it (branches, tags,
  **PRs**) and force-pushing — the one sanctioned force-push (objects stay
  fetchable by SHA until GC).
- **Commit with the `…@users.noreply.github.com` identity** on public repos.

## Network allowlists live in `_agent-guidance/docs/reference/`

Two egress allowlists as **reference copies**, each with a `.CHANGELOG.md`
sidecar; nothing loads them. Add a changelog entry when you change one.

- `network-allowlist-claude-environments.txt` — **in force** for the Claude
  Code cloud environment `My Whitelist`; the dialog at claude.ai/code is
  authoritative.
- `network-allowlist-github-runners.txt` — **proposed** for CI; unenforceable
  on a standard runner (roadmap #821 closed, not planned).

Traps: `*.example.com` does **not** match the apex `example.com`, and the
"also include default list of common package managers" checkbox silently
adds ~200 domains.

## Automation vs branch protection

Fleet repos are PR-only on the default branch via ruleset, managed as code in
`repo-settings` (ADR 0001).

- Never design a bot that pushes to a protected default branch — rejected
  (GH013), even from the repo's own workflows.
- Generated data goes on a dedicated unprotected results branch
  (skills-evals' `eval-results`), treated as untrusted.
- A bot that must write to a default branch needs a ruleset bypass actor
  declared in repo-settings' `fleet.yml`, never a hand-granted UI bypass; the
  AGENTS.md sync App is the example.
- PR + auto-merge is not a sanctioned bot-write path for fleet repos; the
  cms-platform-managed repos use it by design.
- **A required status check gets no `concurrency` group** when its job can
  fire twice on one head sha (label events, `opened` + `synchronize`): a
  cancelled run can win the context and block the merge for good
  (`405 Required status check "<ctx>" is cancelled`).
  `cancel-in-progress: false` does not help (only the latest pending run
  survives), a shared group cancels older siblings, so make triggers pairwise
  disjoint; `push`/`synchronize`-only jobs may keep `cancel-in-progress:
  true`. Lock it with a test that **parses** the YAML.

## Two GitHub connectors, and which one you are holding

`get_me` cannot tell the two GitHub MCP servers apart; establish which you
hold before writing.

- **`mcp__github__*` — session-provisioned**, not in `ListConnectors`. The
  **only** one with Actions tools (`actions_*`), job logs (`get_job_logs`),
  auto-merge and review-thread resolution. Reach: the attached repos.
- **`mcp__github-mcp__*` — the claude.ai org connector `github-mcp`**, listed
  by `ListConnectors`. A **strict subset**: same reads,
  same PR/issue/merge/push/delete writes; no Actions, job logs, auto-merge
  or review threads. Reach: a GitHub App allowlist INDEPENDENT of the
  attached repos. **Probe for it by connector NAME, never a remembered
  prefix** (`mcp__b26ebb34-…__*` until 2026-08-28). It CAN check a PR —
  `pull_request_read` with `method: "get_check_runs"` and `"get_status"`;
  read BOTH (#83: `get_status` `pending` while every check run was green) —
  but cannot dispatch or read a workflow RUN, and a merge
  under it is synchronous (no `enable_pr_auto_merge`).
- **Fewer tools is not less dangerous.** Both merge, push and delete, and the
  subset one's reach cannot be inferred from the session's repo list
  (2026-08-19: `github-mcp` 404s on private `repo-settings`).

Prefer `mcp__github__`; use `github-mcp` only when the other cannot see a
repo, say so, and name the connector in every verification.

## A GitHub 404 means "not authorized", not "not there"

GitHub answers **404, not 403**, to a caller not allowed to know a private repo
exists, so every 404 is ambiguous: gone, or invisible to that credential.

- **Probe the repo, not the object.** If `GET /repos/<owner>/<repo>/pulls`
  404s too, the repo is invisible — a scope gap; if only the object 404s, it
  is gone.
- **Try the other connector first** (2026-08-19: a mid-session MCP reconnect
  brought up a server blind to a private repo the other had just read;
  `add_repo` "already attached" is session scope, not the connector's
  installation).
- **Git is a separate credential path:** `git ls-remote origin '<ref>'` asks
  "does this branch exist", `git merge-base --is-ancestor <sha> origin/main`
  "was it merged"; neither touches the API.
- Never report a repo, PR or branch as gone on a 404 alone; say which
  credential could not see it and what you checked with.

## The fleet spans TWO owners, and a scoped search will not say so

`Adam-S-Daniel` and `jodidaniel`, both in `SYNC_OWNERS` (_agent-guidance's
`sync.yml` and sibling workflows): enumerate it, never hardcode one. A
query scoped to one owner returns a **plausible, complete-shaped, wrong**
result (2026-08-25: a `user:Adam-S-Daniel` code search "proved"
jodidaniel.com had no `skills.lock`).

- **Prefer the fleet's registries** (`repos.yml`, `fleet.yml`,
  `cron_coverage.fleet`) **to a search index**; a zero result is weak evidence.
- **To ask whether repo X has file Y, ask the repo** (`git ls-remote`, the
  contents API), not the index.
- **Your session's reach is not the fleet's shape** — hosted sessions refuse
  cross-owner attachment; "I cannot see it" and "it does not exist" stay
  separate sentences.
- **The DENOMINATOR is the part that lies.** Enumerating from local checkouts
  errs both ways (2026-08-29: three DELETED repos counted and a live consumer
  missed — `ALL PROPAGATED` over 17 of 18, false GREEN; issue #37). Take the
  denominator from a registry or the remote, never the disk, and say which.

## "The watch finished" is not "CI passed"

Never read pass/fail off a watch command's exit code: in `cmd | tail` `$?` is
`tail`'s, a backgrounded watch reports that pipeline code, and `tail -N`
hides FAILURE lines (once: e2e and lint FAILURE read green).

- Capture the real code with `${PIPESTATUS[0]}`, or don't pipe the watch.
- After **any** CI watch, query the conclusions and report the parsed result:

  ```bash
  gh pr view <n> --repo <owner>/<repo> --json statusCheckRollup --jq \
    '.statusCheckRollup[] | (.conclusion // .state) as $c
     | select($c != null and $c != "SUCCESS" and $c != "NEUTRAL")
     | "\(.name // .context): \($c)"'
  ```

  A check run carries `.conclusion`, a legacy status `.state`; filter on one
  and the other reads clean. "Watch done" means "now verify".
- **A pipe into `grep -q` is a race.** `grep -q` exits at the first match, so
  past the 64 KiB pipe buffer the writer dies 141 and `pipefail` reads a
  present marker as absent (issue #81; 20 trials per size: 48 kB 0/20,
  95 kB 18/20). Feed data as an argument or here-string —
  `grep -qxF -- "$m" <<<"$s"` or `[[ $'\n'"$s"$'\n' == *$'\n'"$m"$'\n'* ]]`.
  A size-dependent bug needs trials, not a probe; say how many.
  `$( ... || true )` is safe by accident — say so where you find it.
- **`gh api ... --jq` on an HTTP error prints the raw error body to stdout**,
  so `|| true` captures it (it broke `sync.sh`'s `default_sections`).
  Discard explicitly:
  `out=$(gh api ... --jq '.foo') || out=""`.

## A successful `git push` does not mean your commit exists

A pre-commit hook that refuses the commit does not stop the push: `git push`
pushes the branch at the base commit and prints an ordinary success. The
fleet's `secrets-scan` guard (cms-platform's `dev-hooks-sync.yml`) FAILS
CLOSED without `gitleaks` on `PATH`, which a fresh container lacks
(2026-08-25, adamdaniel.ai).

- **Verify the commit, not the push:** `git log --oneline -1` shows your
  message and `git status --short` is clean, or `git rev-parse HEAD` differs
  from `git rev-parse origin/<base>`.
- **`&&`-chain commit into push**; a newline or `;` lets a refused commit
  become a pushed branch.
- **Install the tool; do not reach for the bypass.** `SKIP_SECRETS_SCAN=1` is
  for emergencies; the binary is one `curl` away.
- **A push can carry the WRONG ref while both checks pass.**
  `git push -u origin <name>` pushes the LOCAL branch of that name, not
  necessarily the one you are on (2026-08-29: the commit landed on `main` and
  the push updated a stale local branch). The only check naming commit AND
  remote branch:
  `git merge-base --is-ancestor <sha> origin/<branch>`. Run it after every push.

## Dependency updates

Dependabot `cooldown`: `default-days: 7` always, `semver-major-days: 30` where
supported, never `github-actions` (#133). Version updates only (an advisory
bypasses it); unset `cooldown` isn't "no wait" (GitHub's implicit minimum: 3
days); `semver-minor-days` / `semver-patch-days` stay undefined, falling back
to `default-days`. A package added or bumped **by hand** has no automation
watching it: check `npm view <pkg> time --json`, take the newest release past
7 days, pin it exact (no caret).

## A name you choose becomes data a scanner reads

gitleaks' `generic-api-key` rule fires on a **keyword** next to a
high-entropy value:

```
access  auth  api  credential  creds  key  passwd  password  secret  token
```

Any generated file serialising such a `name: value` beside a hash looks like
a leak: **`cms-platform-secrets`** in `skills.lock` turned both consumer sites
red on every push (adamdaniel.ai: eight blocked publishes) while the author's
repo stayed green — the PR lane scans `base..head`, the push lane full
history, and the name outlives the rename until history is rewritten.

- **Check a name against that list before committing to it** whenever it
  lands in a generated artifact; name the purpose
  (`consumer-repo-provisioning`), not the sensitive noun.
- **Fix it at the source, not with an allowlist.** A `.gitleaksignore`
  fingerprint is `<commit>:<file>:<rule>:<line>` — repo-unique, unpropagable.
- **Do not lean on a scanner's internals.** `sha256:<hex>` dodges the rule
  only because `:` is outside its capture class; call it self-documentation.
- **Suppress by value, never by path.** A `paths` entry skips the file before
  any rule runs (cms-platform#260: 29KB unscanned).

## Pinning GitHub Actions

**Every `uses:` is pinned to a full 40-character commit SHA** — workflows,
composite actions and reusable-workflow refs alike; never a tag, branch or
abbreviated SHA.

```yaml
uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11
```

- **A pin carries NO trailing version comment.** Nothing maintains it, so it
  lies (2026-08-20: Dependabot left stale comments beside moved SHAs in
  GHA-bench#52 and skills-evals #38/#39/#40). Resolve a version with
  `git ls-remote <url> | grep <sha>` or the Dependabot PR title.
- **Wait 7 days after a release before adopting it**; else pin the previous.
- **Dereference annotated tags.** `gh api .../git/ref/tags/<tag>` with
  `.object.type == "tag"` gives the tag object's SHA, which fails at runtime;
  use `git ls-remote <url> 'refs/tags/<tag>^{}'`.
- **The one carve-out: a ref into this account's own `cms-platform` stays on
  its release tag, in both shapes** — reusable workflow
  `Adam-S-Daniel/cms-platform/.github/workflows/<x>.yml@v0.1.88` and composite
  action `Adam-S-Daniel/cms-platform/.github/actions/<x>@v0.1.88` —
  because `check-platform-pin-consistency.js` asserts each equals
  `platform.lock`'s `platform_ref`. Nothing third-party is ever a tag.
- `./local/path` and `docker://` refs have nothing to pin.

`sha_pinning_required: true` (`repo-settings`' `fleet.yml`; `cms-platform`'s
`repo-settings.yml`) governs **actions** only, so a tag in a *third-party*
reusable-workflow ref is for review to catch.

## Subagent delegation (model routing)

- Don't write code in the main loop: implement in a subagent on a lower-power
  model — haiku-class for exactly-specified edits, sonnet-class for
  implementation from a clear spec; escalate rather than ship a wrong diff on
  anything subtle. The main loop keeps root cause, architecture, the spec
  and diff review. Explore/Plan agents and SDK
  harnesses with `settingSources: []` never see this file — restate
  load-bearing constraints in the prompt.
- Delegated work is done when a **verifier exits 0**: name the exact
  command, run LAST (after a trailing `echo $?` the tool's exit code is the
  echo's), and require its code back. "Cannot run it" is BLOCKED; a count
  off-spec, stop-and-report; a self-contradicting report ("2 failed",
  "exit 0"), re-run it. **Prove the verifier can fail first:**
  `python3 test_foo.py` with no `__main__` block exits 0 having asserted
  nothing (2026-08-22). Name the RUNNER (`python3 -m pytest <paths> -q`),
  never the file, and require the test COUNT beside the exit code.
- **A working subagent OWNS the tree — do not commit or push under it.** A
  push mid-flight plus its `git commit --amend` diverges a published branch
  (2026-08-22; recover by reset and fresh commit, never force-push),
  and its `git checkout -- <file>` discards your edits. Wait for a clean
  `git status` plus a recorded result, and decline a stop hook's "commit and
  push" nudge.
- **A subagent that has REPORTED can still be holding the tree**: its
  BACKGROUND CHILDREN are not reaped (2026-08-29: an orphan test loop turned
  a 999/0 tree into 985/14). Look for descendants before trusting a long run
  (`ps -eo pid,etimes,cmd | grep '[y]our-command'`); `md5sum` the inputs
  before and after (differ → not evidence); prefer a per-run output path;
  kill orphans, and say so.
- **A subagent that goes quiet is not working — check activity, not the
  clock.** Its transcript's mtime is the signal. Fix the staleness threshold
  in advance and write the fallback into the check-in (2026-08-22: a dead
  agent looked in-flight for an hour).
- **A live-test prompt states the credential boundary** — which
  `HOME`/profile, what it may read, and that it must not copy real
  credentials to make the test pass (a reviewer once did, unasked). Supply a
  throwaway credential or run unauthenticated; else it's the operator's call.
- **A scratch tree can still reach production.** `cp -a` copies
  `.git/config`, so a copy inherits `origin` (one pushed 14 commits to a
  default branch); but `git remote remove origin` inside a
  `git worktree` strips the PARENT's remote. Before disarming anything, run
  **`/adam:disarm-inherited-reach`**.

## Skills ecosystem

- The registry is `Adam-S-Daniel/agentskills`: bundles `adam`
  (general-purpose, cloud-safe; default-on), `adam-local` (machine-bound)
  and `fastmail`, each with `skills/<skill>/`; invoke as `/adam:<skill>`;
  its `setup.sh` sets up a machine.
- **A `git push` failing in EVERY repo** means `setup.sh`'s GLOBAL
  sync-skills pre-push hook is stale: re-run `bash setup.sh` in the registry.
- Cloud/ephemeral sessions get **no** plugins from repo-declared settings
  (agentskills' ADR 0001): the repo's `skills.lock` and `skills-bootstrap`
  SessionStart hook install the bundles, digest-verified; read the
  `skills:` verdict at session start.
- **Adoption is opt-in and double-keyed:** an entry in `_agent-guidance`'s
  `repos.yml` AND a `skills.lock` the repo committed itself (the sync never
  writes one). Bundles cost always-on context, so a repo may be deliberately
  out — check for `skills.lock`, don't guess.
- **Terminals (CLI 2.1.273+) load the claude.ai account store too**, as
  `anthropic-skills:<name>`; `setup.sh` opts a machine out
  (`syncClaudeAiSkills: false`), cloud sessions can't (agentskills' ADR 0010).
- New reusable skills graduate **into** the registry (sensitive ones into
  `agentskills-private`); a long skill splits across files.

## Two setup gaps you may close, and must not nag about

Two setup gaps no repo can commit, both silent when missing. **Detect first,
and say nothing when the check passes.** Run it once per session, not as a
greeting and not only for skills work.

**Cloud (claude.ai) — PROMPT, never act.** Resolve `$project` first:
`$CLAUDE_PROJECT_DIR` when set (**unset** here — `remote_mobile`,
2026-08-25), else the nearest ancestor of the cwd holding more than one repo
checkout; an empty value probes `/` and silently suppresses the prompt.
Prompt only when **all four** hold:

1. hosted — entrypoint `remote*`/`claude_in_slack`/`claude-in-slack`/
   `claude-in-teams`, **or** `$CLAUDE_CODE_REMOTE_SESSION_ID` is set;
2. **multi-repo** — `$project` has no `skills.lock` but a child does;
3. `$project/.claude/settings.json` does not already register a
   `skills-bootstrap` SessionStart hook;
4. some child ships `.claude/hooks/skills-bootstrap.sh`.

Then say it once, name the snippet's home (`docs/multi-repo-delivery.md` in
agentskills — do not paraphrase it), and drop it.

**Durable machine — ACT, then one line.** `claude plugin marketplace list
--json` returns `[]` when nothing is configured: **absent** → `claude plugin
marketplace add Adam-S-Daniel/agentskills` and install the bundles wanted;
**marketplace behind** → `claude plugin marketplace update agentskills`;
**install behind** → below; **both current** → silence.
The clone (`~/.claude/plugins/marketplaces/<name>/` — find it, never assume
the path) auto-updates while the installed bundle
(`~/.claude/plugins/cache/<marketplace>/<bundle>/<version>/`) never moves, so
check the INSTALL (`ZENDA`, 2026-08-31: **381 commits** behind):
`~/.claude/plugins/installed_plugins.json` carries a `gitCommitSha` per
entry; `git -C <clone> merge-base --is-ancestor <that sha> HEAD` succeeding
means behind, `git -C <clone> rev-list --count <sha>..HEAD` says by how far.
**`claude plugin update` may not fix it, and will say it did** (it gates on
the `version` string alone; agentskills' ADR 0009) — uninstall and reinstall
instead. An update changes what loads **next** session; a marketplace
refresh does not move a federated bundle. Neither check belongs in a repo's
`AGENTS.md`.

## Git practices

- Concise commit messages that explain *why*; one logical change per commit.
- Do not amend published commits or force-push shared branches.
- **Merge with a merge commit — `gh pr merge --merge`.** Squash and rebase
  are off (`--squash` fails; do not offer it) except the three
  cms-platform-managed repos (`cms-platform`, `adamdaniel.ai`,
  `jodidaniel.com`), kept for Decap publishing. Squash strands commits a
  lockfile pins by sha (2026-08-15: `cannot resolve ref`).
- **A closing keyword before `#N` or an issue/PR URL, any repo, closes it,
  PRs too**, from a PR body or any commit message; backticks don't help,
  and linked issues won't show it (cms-platform#283 via `78617e1`,
  _agent-guidance#136 by cms-platform#434). To close: `Closes #N`, not
  `For #N`; else omit it. After merging, check what should stay open is.
