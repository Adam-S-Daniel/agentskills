---
name: windows-elevation-from-wsl
description: >-
  Handle "Access is denied" from powershell.exe or pwsh.exe run inside WSL on
  ZENDA — Register-ScheduledTask / Set-ScheduledTask on a
  RunLevel=HighestAvailable task, a service change (Set-Service, Stop-Service,
  New-Service), an LSA rights grant such as "Log on as a batch job"
  (SeBatchLogonRight, secedit, ntrights), an HKLM registry write, or any other
  change to Windows state from a WSL session. Use it BEFORE attempting such a
  write from WSL and the moment one is denied. A WSL-launched PowerShell holds a
  filtered, non-elevated token: reads succeed (Get-ScheduledTask, Get-Service,
  the registry) so the surface looks available, writes that need elevation
  fail, and no flag, retry, Start-Process -Verb RunAs, schtasks, sudo, SYSTEM
  principal or downgraded RunLevel fixes it. Compose the command from WSL,
  export what it will overwrite (Export-ScheduledTask), then hand the operator
  the exact line for an elevated Windows prompt and say it needs elevation.
license: MIT
compatibility: >-
  A WSL session on a Windows host (ZENDA) that drives Windows through
  powershell.exe / pwsh.exe via interop. Not applicable on Claude.ai web,
  hosted sandboxes, macOS or plain Linux, and not needed in a Claude Code
  session that already runs elevated on the Windows side.
---

# Windows elevation from WSL

You are in WSL on `ZENDA`, you ran `powershell.exe` or `pwsh.exe` to change
something on the Windows side, and it answered **`Access is denied`** — or you
are about to run such a write and want to skip that step. This skill is the
whole procedure. It is short because the fix is not on the WSL side.

## What is actually happening

A process launched from WSL through Windows interop inherits a **filtered,
non-elevated token**, even when the Windows account is a local administrator.
Nothing that process can do raises a UAC prompt: there is no console session
for the prompt to appear in, so the elevation request fails outright rather
than waiting for a click.

The failure is **asymmetric**, which is what makes it easy to misread:

| From WSL | Result |
| --- | --- |
| `Get-ScheduledTask`, `Get-ScheduledTaskInfo`, `Export-ScheduledTask` | works |
| `Get-Service`, `Get-ItemProperty HKLM:\...`, `whoami`, `Get-Date` | works |
| `Register-ScheduledTask` / `Set-ScheduledTask` / `Unregister-ScheduledTask` on a `RunLevel=HighestAvailable` task | `Access is denied` |
| `Set-Service` / `Stop-Service` / `Start-Service` / `New-Service` | `Access is denied` |
| An LSA rights grant — `secedit`, `ntrights`, `LsaAddAccountRights` | `Access is denied` |
| `Set-ItemProperty` / `New-ItemProperty` under `HKLM:` | `Access is denied` |
| A script carrying `#requires -RunAsAdministrator` | refuses before its first line runs |

Reads succeed, so the surface looks fully available right up to the first
write. Confirm the diagnosis in one read if you want to:

```powershell
powershell.exe -NoProfile -Command "whoami /groups | Select-String Administrators"
```

`BUILTIN\Administrators ... Group used for deny only` is the filtered token.
So is `False` from
`([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole('Administrator')`.

## What does not fix it

Every one of these has been tried; none works, and several make things worse.

- **A flag.** There is no `-Elevated`, `-RunAsAdministrator` or
  `-ExecutionPolicy` value that changes the token.
- **A retry.** The second attempt is denied like the first. One denial is
  discovery; a second is chasing it.
- **`Start-Process ... -Verb RunAs`.** Fails with *"The requested operation
  requires elevation"* — it needs a UAC prompt the WSL-launched process cannot
  show.
- **`schtasks`, `runas`, `sudo`, `gsudo`.** Same token, same denial, or the
  tool is absent.
- **A downgraded principal.** Switching the task to `NT AUTHORITY\SYSTEM` or
  to `-RunLevel Limited` may let the write through and is the dangerous
  outcome: WSL distros are registered per Windows user, so a SYSTEM task sees
  none of them and backs up nothing, while registering cleanly and running on
  schedule. Do not change the principal to dodge the denial.
- **Editing the script until the write disappears.** The write is the point of
  the script.

## The procedure

1. **Investigate from WSL with reads.** `Get-ScheduledTask -TaskPath '\<path>\'`,
   `Get-ScheduledTaskInfo`, `Get-Service`, registry reads. Establish exactly
   what needs to change.
2. **Export what the write will overwrite**, so there is something to restore:

   ```powershell
   powershell.exe -NoProfile -Command "Export-ScheduledTask -TaskPath '\WslAutomation\' -TaskName 'WSL-Backup'" > wsl-backup.before.xml
   ```

   Keep the XML beside the change (in the working tree, not committed, unless
   the repo has a convention for it). A service or registry change gets the
   equivalent: `Get-Service | Select-Object Name,StartType,Status` or a
   `reg export`.
3. **Make the change in the repo** — the script, the parameter default, the
   config — and commit it as usual. That part needs no elevation.
4. **Compose the exact command** the operator will run, with **Windows paths**
   (`D:\repos\...`, not `/mnt/d/...`), an explicit `-ExecutionPolicy Bypass`
   if the script needs it, and every parameter filled in. Prefer the repo's
   own installer script over a hand-assembled cmdlet when one exists — that is
   the line the README already tells a human to run.
5. **Hand it over, and say it needs elevation.** Do not bury this in a list of
   things the operator "may want to do". The shape:

   > This needs an **elevated** Windows prompt (PowerShell → *Run as
   > administrator*); it cannot be applied from WSL. Run:
   >
   > ```powershell
   > cd D:\repos\adam-s-daniel\wsl-automation
   > .\scripts\register-tasks.ps1 -BackupDir 'D:\Backups\wsl'
   > ```
   >
   > The previous task definition is exported to `wsl-backup.before.xml` if it
   > needs restoring (`Register-ScheduledTask -Xml (Get-Content -Raw ...)`).

6. **Verify with reads afterwards**, once the operator says it ran:
   `Get-ScheduledTask ... | Select-Object -ExpandProperty Triggers`, or the
   equivalent read for a service or registry value. Do not report the change
   as applied until a read shows it.

## What to say when asked why

One sentence is enough: *PowerShell launched from WSL runs with a filtered
token and cannot prompt for elevation, so anything that needs administrator
rights has to be run from an elevated Windows prompt; everything read-only
works from here.*

## Where the rule lives

The fleet guidance's "Workstation layout" carries a one-clause pointer at this
skill for `ZENDA`; this file is the procedure. The repo where it was learned
(`wsl-automation`) keeps only what is specific to its scheduled tasks in its
own `AGENTS.md`.
