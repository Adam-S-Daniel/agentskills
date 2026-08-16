Transcript handoff from session session_01XFxq5SpueH5mD4HtRhwqSP ("Skills delivery v2 loose ends"), requested by the session finishing #54/#59.

## 1. Rejected alternatives

**Tier-3 transport: I never tested whether a trigger created *from* a session holding explicit `sources` passes them to `create_new_session_on_fire` sessions.** I considered it and discarded it as untestable from where I sat — the authorized session would have had to call `create_trigger` itself, and I never confirmed it had `mcp__*` tools. I took `persistent_session_id` instead because it was available and provable in the moment. **If anyone wants the cold boot and the push/email notifications back, that untested experiment is the first thing to try**; it is cheap and would restore both. The persistent binding was the available fix, not the best one.

**Publishing the Tier-3 result before the transport was fixed.** I nearly did, and stopped. `freshness_verdict` gates `bootstrapped` only on the `summary is None` branch, so *any* published `latest.json` reds the gate immediately when the audit reports drift — the marker is irrelevant once a result exists. Publishing while the Routine still could not push would have left CI asserting "the Routine has stopped firing" while it was firing perfectly. Order matters: prove the transport, then arm the gate.

**Disabling the broken Routine rather than fixing it.** Rejected because at that moment its push/email notification was the *only* reporting layer that worked; disabling it would have silenced the one channel still delivering.

**Fixing the skills-evals `--registry` bug in the workflow instead of the harness.** Passing an absolute path from `propagation.yml` would have gone green just as fast. Rejected: a relative path is a legitimate thing for a caller to pass, and patching the caller leaves the landmine armed for the next one.

## 2. Dead ends and false starts

**The `fire_trigger` diagnostic, and the misleading signal it produced.** When the first Tier-3 run published nothing, I fired the Routine again with extra instructions appended via `fire_trigger`'s `text` parameter, telling it to push a marker branch first and report `$HOME` internals. No branch appeared. **I read that absence as evidence that pushing was broken. It was not** — the session had refused the whole appended block as untrusted injected content, which was the correct call. Pushing *was* also broken, so I reached the right conclusion for the wrong reason and would not have known the difference. `fire_trigger`'s `text` is not a steering channel for a Routine; `update_trigger` is.

**I committed subagent work twice while the agent was still running.** Both times the agent's tree went clean underneath it; one reported a phantom "external force-push" and spent real effort investigating. No force-push occurred — I verified the old branch tip was an ancestor of the pushed one. Wait for the completion notification, not for the transcript to go quiet.

**Environment, not repo:** `jsonschema`, `markdown-it-py` and `pytest` disappeared from this container mid-session. `check_agent_plugins.py` and the whole pytest job failed on `ModuleNotFoundError` and read as a repo regression for a few minutes. Reinstalling restored both. Nothing in the repo caused it.

**shellcheck:** I ran it repo-wide without `-S error` and got two `SC2088` warnings in `memory-migrate.sh`, and briefly thought CI was inconsistent with local. CI uses `-S error` and those two warnings are documented as tolerated. Match CI's invocation before concluding anything.

## 3. Unverified hunches

- **Whether a trigger created from a session with explicit `sources` passes them to fired sessions** (see §1). Settle it by creating one from the authorized session and firing it: if the fired session can push, the persistent binding can be retired and the cold boot restored.
- **Whether the account store in a trigger-fired container is live or a build-time snapshot.** The fired audit returned counts identical to an interactive session, which is consistent with either. Settle it by changing one account skill and firing: if the count does not move, Tier 3 is auditing a stale copy and its freshness claim is weaker than it reads.
- **Persistent-session context growth.** The Tier-3 Routine now fires daily into one long-lived session. Over months that context only grows. Never measured; no idea where it degrades.
- **~20 accumulated `send_later` one-shot triggers** were in `list_triggers` when I looked, from PR check-ins. I do not know whether fired one-shots are ever reaped. Settle by listing triggers after a few days.

## 4. Noticed but not acted on

- **GitHub disables cron after ~60 days without repo activity** — recorded in `eval.yml`'s own header. Both things I scheduled inherit that: `propagation.yml`'s daily trigger and the Tier-3 Routine. skills-evals gets regular commits today so it is not live, but nothing detects it if that stops.
- **skills-evals has no lint locking the "no `${{ }}` expression inside a `run:` body" rule**, which I relied on and verified only with an ad-hoc script. An agent offered to promote that checker into the test suite; I declined to avoid moving test counts mid-PR. It is a security property of a public repo's CI with nothing pinning it.
- **The #77 install loop can still clobber a directory named `synced`** if a lock ever names a skill called that. I guarded only the *removal* path, deliberately — changing install behaviour was outside that PR. Pre-existing and unlikely, but real.

## 5. My #78 plan — what of it is still unlanded?

**Nothing. #79 superseded it entirely, and went well past it.** My plan was to edit `SKILL.md` so `skills-doctor` reads the record. #79 wrote a 792-line `check_provenance.py` plus a 1269-line test file — a different and better shape than I had in mind.

Against the three parts specifically:

- **pytest baseline check — done.** My baseline was 400 passed; `main` is now **476 passed** (I ran it just now). The baseline was respected and raised, not bypassed.
- **The 5-part validation suite — superseded, and I can only speak to artifacts, not to their process.** I cannot see their session, so I cannot say whether they ran my five verifiers. What landed covers the same ground: the test file has substantial coverage of record-absent (~47 matches), record-unreadable/corrupt (~35), and the mtime fallback (~13), which were the three states my suite existed to separate. The lock checks are green on `main`.
- **Mutation testing — cannot verify.** Nothing in the commit tells me whether each assertion was watched failing. Weak counter-evidence: **#82 landed after #79, closing #80 and #81** — two follow-up defects found post-merge, one of them `--check-current` counting build artefacts. Those are adjacent to #79 rather than inside `check_provenance.py`, so I would not read them as a coverage failure, but they do mean #79 was not the last word.

One thing from my plan that #79 confirmed the hard way: the content → re-pin ordering. Its commit message says it landed "as content -> re-pin pairs, because the lock digests the skill's whole directory and cannot pin the commit that contains it." That was the trap I flagged, and it is real.

**Nothing here is worth redoing.**

## 6. Not in a commit message or issue comment

### a. The account-upload arm and the `sync-skills` uploader (#59)

Close to nothing, and I want to be plain about that rather than pad it. **I never ran `sync-skills`, never opened the uploader, and never touched the account store.** My only contact was `run_account_audit.py`, which is strictly read-only — there is no write call in `account_store.py`, and the audit's output paths default to `None` and must be passed explicitly.

Everything I actually learned about the account store is already in my #59 comment. I have no observation of the uploader's behaviour to add.

### b. The `skills-bootstrap` SessionStart hook

**One incident that is in no commit or issue: early in this session a subagent violated an explicit scratch-`HOME` instruction and ran the hook against the real `HOME`, installing the 9 `adam` skills into `/root/.claude/skills`.** It was harmless in substance — the install is digest-verified and the container is ephemeral — but it was an unsanctioned write to a live session's own skill directory, and it **changed this session's skill listing mid-run**. That matters for §6c below, and it matters as a warning: the hook is easy to point at a real `HOME` by accident, and when that happens the evidence you are standing on shifts underneath you.

### c. Does the hook fire when the project dir is a multi-repo parent?

**I never deliberately tested it, and I did not check `~/.claude/skills` at session start.** So I cannot confirm or contradict your measurement directly.

I do have **indirect evidence consistent with your finding, with a confound that weakens it** — take it as suggestive, not as a second data point:

- This session's cwd is `/home/user` with 14 repos side by side, exactly the shape you describe.
- The subagent incident in §6b is recorded in my own notes as having *changed* this session's skill listing when it installed the 9 `adam` skills. If the listing changed, those skills were not there before — which implies the hook had not populated `~/.claude/skills` at session start.
- Later `/context` output shows user-level skills as `session-start-hook` plus the 9 `adam` bundle skills, and separately the `claude.ai sync` ones. That composition is what you would expect from "`session-start-hook` + `synced/` at start, then a mid-session install of 9".

**The confound:** the subagent install is itself sufficient to explain the 9 being present, so this cannot distinguish "the hook never fired" from "the hook fired and the subagent re-installed on top". I am reconstructing the ordering from a summary of my own earlier turns, not from a direct observation I made at the time. **Treat your hand-run measurement as the authoritative one; mine is at best weak corroboration.**
