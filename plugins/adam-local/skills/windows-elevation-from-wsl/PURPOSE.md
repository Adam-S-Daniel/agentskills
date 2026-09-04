# Purpose — windows-elevation-from-wsl

Maintenance context only; never loaded at inference.

## The incident it packages

`wsl-automation`'s `AGENTS.md` recorded, in its repo-specific section, that an
agent working in WSL on `ZENDA` drives Windows through `powershell.exe` /
`pwsh.exe` and that this process holds a filtered, non-elevated token: reads
(`Get-ScheduledTask`, `Get-Service`, the registry) succeed, so the surface looks
fully available, while writes that need elevation (`Register-ScheduledTask` /
`Set-ScheduledTask` on a `RunLevel=HighestAvailable` task, service changes, an
LSA rights grant such as "Log on as a batch job") fail with "Access is denied".
The lesson was that no flag, retry or downgraded principal fixes it, and that
the right move is to compose the command from WSL, export what it will
overwrite, and hand the operator the exact line for an elevated Windows prompt.

## Why a skill, and why `adam-local`

The guidance-centralization routine proposed promoting the bullet into the
fleet-wide `base.md`
([_agent-guidance#112](https://github.com/Adam-S-Daniel/_agent-guidance/pull/112),
tightened in [#113](https://github.com/Adam-S-Daniel/_agent-guidance/pull/113)).
[_agent-guidance#114](https://github.com/Adam-S-Daniel/_agent-guidance/issues/114)
assessed it against that repo's ADR 0002 three-part test for `base.md` —
unconditional rule, failure mode is non-recognition, enforced somewhere in
code — and it clears none: it applies only on ZENDA when changing Windows
state; the failure mode is a loud `Access is denied`, which a symptom-keyed
description matches on; and nothing enforces it. ADR 0002's reach objection
("a skill only applies in a session that loaded it") cuts the other way here:
the ~18 cloud-only fleet repos cannot run `powershell.exe`, so `base.md` would
ship the text to every session that can never use it, while ZENDA sessions are
durable and install `adam-local` from the marketplace. `base.md` keeps a
one-clause pointer; this skill carries the procedure.

`adam-local` because it is machine-bound (a WSL session on a Windows host);
`adam` is the cloud-safe bundle.

## Eval

`skills-evals/evals/windows-elevation-from-wsl/` — Class B, hermetic: a fake
`powershell.exe` on the arm's `PATH` answers reads, denies writes, refuses the
dodges this skill forbids, and logs every invocation; the checks read the log,
the edited script and the final reply.

## Name

Checked against gitleaks' `generic-api-key` keyword list (`access auth api
credential creds key passwd password secret token`) before committing — it
contains none of them, so its `skills.lock` line cannot false-positive.
