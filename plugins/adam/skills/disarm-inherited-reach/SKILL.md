---
name: disarm-inherited-reach
description: "Sever a scratch tree's inherited push path to the real repository the moment the tree exists, before anything runs in it. Fires on the concrete act, not on how risky the work feels: copying a repo (cp -a, cp -r, rsync, untarring, a /tmp scratch tree), git clone, git worktree add, or standing up a throwaway checkout to run something in; and on any negative control - proving the verifier can fail, a mutation test, a deliberately broken tree, editing a dry-run flag or confirmation prompt or required check to watch it fire, or running an untested destructive path such as a reaper, a sweeper, a delete or force-push script, or a data migration. cp -a copies .git/config, so the copy inherits origin pointing at production. A worktree is not a copy and must be handled differently, because removing its remote removes the parent's. Use this even when you believe you are only verifying."
---

# Disarm inherited reach

A scratch copy of a repository is not a sandbox. `cp -a` copies `.git/config`, so the copy
carries `origin` pointing at the real repository, along with every other push path the
original had. Nothing about living in `/tmp` changes that. The tree looks disposable and is
fully armed.

This skill governs one narrow window: **from the moment a tree exists that inherited reach,
to the moment that reach is proven gone.** Disarm first, then work. A disarm performed after
the destructive command has run is a report, not a control.

## When to invoke

Invoke on the **act**, never on your assessment of the risk. The agent in the incident below
believed it was verifying a safety gate. It was right about its intent, and it still pushed to
production.

- You made a copy of a repository: `cp -a`, `cp -r`, `rsync`, untarring, a `/tmp` scratch
  tree, a second checkout "just to try something".
- You ran `git clone`, or `git worktree add`.
- You are about to run a negative control: neutering a guard, a gate, a dry-run flag, a
  confirmation prompt, or a required check so you can watch it fail. Proving the verifier can
  fail. A mutation test. A deliberately broken tree.
- You are about to run an untested destructive path — a reaper, a sweeper, a delete or
  force-push script, a data migration — anywhere other than the surface it was written for.

The first bullet stands alone. A copy that nothing is ever run in still needs disarming,
because at copy time you do not know what the next hour will ask of it.

## Procedure

Steps 1 to 6 come before any edit to the tree and before any command is run inside it.

1. **Enter the tree.** After `cp -a /repo /tmp/scratch` your shell is still in `/repo`. Every
   step below acts on the current repository, so running them one directory too early disarms
   — or misattributes — the real checkout. `cd` into the copy and confirm with `git rev-parse
   --show-toplevel` that it prints the scratch path, not the original.

2. **Establish standalone versus worktree, before touching any config.** A worktree shares the
   parent's `.git`, so steps 3 and 5 would act on the parent. Compare the two git directories
   *in the same path format* — the naive comparison false-positives from any subdirectory:

   ```
   a=$(git rev-parse --path-format=absolute --git-dir 2>/dev/null)
   b=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
   if [ -z "$a" ]; then echo "not a git repository"
   elif [ "$a" = "$b" ]; then echo "standalone: safe to disarm"
   else echo "WORKTREE: do not disarm here"; fi
   ```

   `--path-format` needs git 2.31 or newer. On older git, resolve both with `cd "$(git
   rev-parse --git-dir)" && pwd` before comparing; do not compare the raw strings.

3. **Worktree — do not disarm it. Stop using it for this.** There is no safe in-place disarm;
   its config *is* the parent's config. Remove the worktree, make a real copy or clone, and
   start again at step 1.

4. **Standalone — sever the remotes.** `git remote -v` shows what you inherited; remove each
   one by name with `git remote remove`. Confirm `git remote -v` prints nothing.

5. **Close the routes that survive remote removal.** Run `git config --list --show-origin` and
   read it. Entries injected through the environment appear under the label `command line:`
   rather than any file, which is the only way to see them. Remove or override any
   `url.*.insteadOf` rewrite, any `remote.*.pushurl`, and any `gh` default-repo setting; for a
   repo with submodules, disarm each submodule too.

6. **Set a throwaway local identity** so anything this tree commits is self-identifying:
   `git config --local user.name scratch-tree` and `git config --local user.email
   scratch@example.com`. Local, never global, and never in a worktree — there it rewrites the
   parent's identity.

7. **Prove it fails closed.** Attempt `git push origin HEAD` and watch it be refused with
   `fatal: 'origin' does not appear to be a git repository`. Read what this does and does not
   establish in "An isolation you have not watched fail" below.

8. **Only now** neuter the gate, break the tree, or run the destructive path.

9. **Delete the tree when the finding is reported.** The 45 armed copies below did not
   accumulate because anyone disarmed them wrongly. They accumulated because nothing removed
   them.

10. **Report the disarm alongside the result** — what you severed, what the refused push
    printed, and that the tree is gone. A negative control whose isolation is undocumented is
    not reviewable.

## The incident this exists to prevent

An agent was told to prove a safety gate was load-bearing — a correct and ordinary task. It
made a scratch copy of a repo with `cp -a`. The copy inherited `origin` pointing at the real
repository. The agent rewrote the script's dry-run gate from `if $DRY_RUN; then` to `if false;
then`, deliberately neutering it exactly as the task asked, and ran the script.

It pushed **14 commits to the real repository's default branch**, destroyed the documented
half of a tracked file, and left CI red.

Four independent axes identified that copy afterwards: its `origin`, its `HEAD` (exactly the
parent of the first bad commit), its neutered gate, and its lack of a local git identity.
Three of those four — the origin, the neutered gate, the missing identity — were sitting in
plain view before the script ran.

In the same session a sibling scratch copy had `git remote remove origin` run on it **before**
the same class of mutation. It caused no harm. One command, run first, was the entire
difference between the two trees.

## Disarm at copy time, not at judgment time

The tempting rule is "disarm when you are about to do something dangerous." It fails twice
over.

It failed in the incident because the agent did not classify itself as about to do something
dangerous — it classified itself as *verifying*. Any rule that asks the agent to self-assess
is a rule the agent in the incident walks straight past.

It also fails at scale. A sweep of the same container found **45 scratch copies of that one
repository under `/tmp`**, every one carrying a live push path to the real repository,
accumulated over two days. **44 of the 45 involved no mutation at all** — they were simply
armed, waiting for whatever the next hour asked of them.

Disarm is cheap and unconditional and belongs at the moment of creation. A tree you have to
make a judgement about later is a tree that will be judged wrongly once.

## A worktree is not a copy — disarming one destroys the parent

Measured directly in a throwaway repo, not reasoned about:

- `git remote remove origin` run **inside a worktree** removed the **parent checkout's**
  remote. The parent went from having `origin` to having none.
- `git config --local user.name` run inside a worktree **rewrote the parent's** identity.

A worktree shares the parent's `.git`. "Local" config in a worktree is the parent's config. So
the central instruction of this skill is *destructive* when applied in a worktree: it silently
disarms and misattributes the real checkout you are working from, while the worktree itself
gains nothing.

This is live for this account specifically. The harness's Agent tool offers `isolation:
'worktree'`, so subagents routinely run in worktrees without having chosen one. Assume
nothing; run the step 2 test.

The remedy for a worktree is not a cleverer disarm. There is not one.

**If you have already disarmed inside a worktree**, the parent's remote and identity are gone
now. Restore them in the parent checkout — re-add the remote by URL and reset `user.name` and
`user.email` — before doing anything else, and say so in your report.

## Severing remotes is necessary, not sufficient

`git remote remove origin` closes the most common route and none of the others. Each of these
reaches production from a tree with zero configured remotes:

- **A URL on the command line.** `git push https://x-access-token:$TOKEN@github.com/owner/repo`
  needs no remote at all, and CI-shaped scripts construct exactly that URL from an ambient
  token.
- **`url.<base>.insteadOf` rewriting.** Measured live in this account's containers, injected
  through `GIT_CONFIG_KEY_*` / `GIT_CONFIG_VALUE_*`. It is in no config file, which is why
  step 5 reads `git config --list --show-origin` and looks under `command line:`.
- **`remote.<name>.pushurl`**, which survives a fetch-URL you thought you had checked.
- **Submodule remotes**, which are separate repositories with their own config.
- **A `gh` default-repo setting**, which makes `gh` commands act on a repo you never named.
- **Ambient cloud, registry, or package credentials**, for a destructive test that is not a
  git operation at all.

## An isolation you have not watched fail

Step 7 establishes exactly one thing: the remote **named** `origin` is gone. It does not
exercise a token-bearing URL, an `insteadOf` rewrite, or a submodule. Do not read a refused
push as proof the tree is inert; read it as proof that one route is closed and that you
verified rather than assumed.

Verify by asking git, not by recalling how you made the copy. Recall is what failed: the
destroying copy was made by someone who knew it was "just a scratch copy". An isolation you
have not watched fail is a green light wired to nothing — the same defect the agent had been
dispatched to find in someone else's code.

## When the thing under test legitimately needs a remote

Sometimes the behaviour under test *is* the push: proving a push path works, exercising
auto-merge, testing a deploy. The skill does not go silent here, because an agent that finds
it silent improvises, and improvising is the incident.

Disarm exactly as above, then add back **one** remote pointing at a throwaway repository you
own and can afford to destroy. Confirm it by **URL**, not by remote name — `git remote get-url
--push --all` — because the name `origin` tells you nothing about where it points. Do that
between step 6 and step 8, and in step 7 expect the push to *land in the throwaway* rather
than be refused; the thing you observe is the destination URL, not a refusal.

## Why an in-code guard is not the fix

The first remedy proposed after the incident was a guard inside the script: refuse to push
when `GITHUB_ACTIONS` is unset. It was withdrawn, and it should not be re-proposed.

It is code, in the same file, reachable by the same `sed` that neutered the gate beside it. No
in-code guard survives deliberate mutation of the gate it lives next to. Removing the reach is
the only control that sits outside what you are about to break — which is exactly why this is
a procedure and not a patch.

## Stay inside the window

This is not a general testing-philosophy skill, not a sandboxing or containers skill, and not
a credential-management skill. It covers the window from "a tree exists that inherited reach"
to "that reach is proven gone", plus deleting the tree afterwards. Anything else you notice —
a script that should not be pushing at all, a credential that should not be in the environment
— belongs in your report, not in this procedure.

## What done looks like

1. You are inside the scratch tree, confirmed by its top-level path.
2. It is standalone, not a worktree, established with the path-format-normalised comparison.
3. `git remote -v` prints nothing — or prints exactly one URL you have read, belonging to a
   throwaway repository.
4. On the no-remote path, a push was attempted and **observed** to be refused. On the
   throwaway path, a push was observed to land in the throwaway, identified by URL.
5. `git config --list --show-origin` has been read, and any `insteadOf`, `pushurl`, submodule
   or `gh` default-repo route was closed or is named in your report.
6. The tree carries a scratch identity, set locally.
7. The tree is deleted, and your report says what was severed and what the verification
   printed.
