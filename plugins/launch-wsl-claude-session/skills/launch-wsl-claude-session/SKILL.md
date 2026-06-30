---
name: launch-wsl-claude-session
description: >-
  Launch a detached, interactive Claude Code session inside WSL from a Windows
  Claude Code session — in a specific repo/folder, optionally remote-controllable
  and optionally seeded with an initial prompt. Use this WHENEVER the user wants
  to open / launch / spawn / start / fire off a separate (or background, detached,
  standalone, "and ignore it") Claude session in WSL, in a given directory,
  especially so it shows up in their remote Claude sessions list, even if they
  don't name every detail. It handles the Windows→WSL launch quirks that silently
  break naive attempts: ConPTY via Windows Terminal, PowerShell path passing,
  session-id vs initial-prompt openers, prompt quoting, and the workspace-trust gate.
compatibility: >-
  Requires a Windows PC with WSL and Windows Terminal. Works whether Claude runs on the
  Windows host (via PowerShell) or inside the WSL distro (via bash + Windows interop).
  Not applicable on Claude.ai web, the mobile app, headless/remote sandboxes, macOS, or
  plain Linux without Windows underneath.
---

# Launch a detached Claude session in WSL

## Environment requirement (read first)

This skill only applies on a **Windows PC that has WSL** — and it works the same
whether the Claude you're using right now is running **on the Windows host** or
**inside that WSL distro**. Either way the result is identical: a new interactive
Claude session in the chosen WSL directory.

It does **not** apply, and you should not use it, when there's no local Windows+WSL to
drive: Claude.ai web, the mobile app, a remote/headless sandbox, a Mac, or a plain
Linux box without Windows underneath. In those environments, stop and tell the user the
skill needs a Windows machine with WSL.

**Pick the launcher for your host:**
- **Claude running on Windows** (PowerShell available; platform is `win32`) → use
  `scripts\launch-wsl-claude.ps1`.
- **Claude running inside WSL / Linux** (bash; `/proc/version` mentions `microsoft`) →
  use `scripts/launch-wsl-claude.sh`.

Both scripts produce the same window via the same underlying command
(`wt.exe wsl.exe --cd <dir> -- <claude> ...`); they differ only in how the host shell
spawns it.

## What this does

Opens a new terminal window running an **interactive** `claude` session inside WSL,
rooted at a directory you choose, and leaves it running for the user to drive (or to
control remotely from the Claude mobile/web app). The session is independent — it
shares no context with the current one.

Two ways to open the session:

- **Default — `--session-id <fresh-uuid>`.** Opens a brand-new session directly. This
  bypasses the "agents view" landing screen (which otherwise does *not* start a chat
  session) and gives the user an empty session to type into.
- **Initial-prompt mode — a positional prompt.** If the user supplies an initial
  prompt (e.g. "stand by for instructions"), seed the session with it instead. Do
  **not** pass `-p`/`--print` — that makes Claude run the prompt once and exit. Without
  it, the prompt becomes the first message and the session stays interactive.

## The fastest path: use the bundled script

**On a Windows host** (PowerShell):

```powershell
# Default (session-id opener):
& "<skill-dir>\scripts\launch-wsl-claude.ps1" -Dir /home/passp/repos/GHA-bench

# With an initial prompt (stays interactive):
& "<skill-dir>\scripts\launch-wsl-claude.ps1" -Dir /home/passp/repos/GHA-bench -Prompt "Stand by for instructions."
```

Parameters: `-Dir` (required, the **WSL** path), `-Prompt` (optional initial prompt),
`-Distro` (default `Ubuntu`), `-RemoteControlName` (optional, adds `--remote-control <name>`),
`-NoWindowsTerminal` (switch; avoid — see the ConPTY gotcha).

**Inside WSL / Linux** (bash) — same behavior, flag-style args:

```bash
# Default (session-id opener):
bash "<skill-dir>/scripts/launch-wsl-claude.sh" --dir /home/passp/repos/GHA-bench

# With an initial prompt (stays interactive):
bash "<skill-dir>/scripts/launch-wsl-claude.sh" --dir /home/passp/repos/GHA-bench --prompt "Stand by for instructions."
```

Args: `--dir` (required), `--prompt` (optional), `--distro` (default `Ubuntu`),
`--remote-control-name` (optional).

Both scripts resolve the `claude` binary path, generate the session UUID, pass an
initial prompt as a single argument (so the quoting is always correct), and launch a
Windows Terminal window — so you don't have to reconstruct any of it by hand.

## Prerequisites (check these — they cause silent failures)

1. **The target directory must be trusted.** If `hasTrustDialogAccepted` is `false`
   for that path in WSL `~/.claude.json`, the session opens but immediately **blocks on
   the workspace-trust dialog** waiting for input. A "launch and ignore" session then
   sits there invisibly and never registers for remote control. Either open it once
   interactively and click **Trust**, or (only with the user's explicit OK) set
   `hasTrustDialogAccepted: true` for that exact path in `~/.claude.json` first. Do not
   silently flip this — it's a security gate and the user's decision.
2. **Claude Code must be installed in the WSL distro** (the script checks via
   `command -v claude` and errors out if missing).

## Remote control

The whole point is usually that the session shows up in the user's **remote Claude
sessions list**. Remote control engages automatically at startup when
`remoteControlAtStartup: true` is set in the WSL `~/.claude/settings.json` (or
`~/.claude.json`). If it is, you don't need any flag — just launch. If it isn't, pass
`-RemoteControlName <name>` so the script adds `--remote-control <name>`.

Note the session only registers once it actually *reaches a live session* — i.e. past
the agents-view landing (handled by the openers above) and past the trust gate
(prerequisite #1). If "nothing shows up," it's almost always one of those two gates.

## Why the launch is done this way (gotchas, learned the hard way)

These are non-obvious and each one silently breaks the launch if ignored — that's why
the script encodes them:

- **On the Windows host, launch from PowerShell, never the Bash/Git-Bash tool.** Git
  Bash rewrites POSIX-looking arguments: `/home/passp/.local/bin/claude` becomes
  `C:/Program Files/Git/home/passp/.local/bin/claude`. The session then starts in the
  wrong place (or the binary isn't found). PowerShell `Start-Process` passes the paths
  through untouched. (This mangling is a Git-Bash/MSYS quirk — **real WSL bash does not
  do it**, which is why the WSL-side `.sh` launcher calls `wt.exe` from bash directly.)
- **Use Windows Terminal (`wt.exe`), not bare `wsl.exe`.** `wt` gives the session a
  proper ConPTY. A bare `wsl.exe` spawn gets a malformed TTY (`your 131072x1 screen
  size is bogus`), and in initial-prompt mode Claude treats that as non-interactive and
  **exits immediately**. With `wt`, the prompt session stays open.
- **Quote the entire initial prompt as ONE argument.** If the prompt is split across
  multiple shell arguments, Claude receives only the first word. The script takes
  `-Prompt` as a single string and passes it as a single element, so this is handled —
  but if you ever launch by hand, wrap the whole prompt in quotes:
  `... -- <claude> "Stand by for instructions."` not `... <claude> Stand by for instructions.`
- **Use the full path to the `claude` binary.** A non-login WSL shell may not have
  `~/.local/bin` on `PATH`. The script resolves the absolute path first.
- **Run the session under a login shell so its *runtime* PATH is complete.** Resolving
  the binary isn't enough: `wsl.exe -- <claude>` runs claude under WSL's reduced default
  PATH, so the *running* agent's own subprocesses can't find tools that only live on the
  login PATH — `pwsh` (`/snap/bin`), `bun` (`~/.bun/bin`), `dotnet`, `~/.npm-global/bin`.
  A long agent job (e.g. a benchmark) then silently breaks with `pwsh: command not
  found`. The scripts launch via `... -- bash -lic 'exec "$@"' bash <claude> <args>`:
  the login+interactive shell rebuilds the full PATH, then `exec` replaces it with claude
  (which inherits both the PATH and the ConPTY).
- **`--cd <wsl-path>` sets the working directory** for the session; pass a WSL path
  (`/home/...`), not a Windows path.

## Manual one-liners (fallback if the script isn't available)

```powershell
# Default — new session by id:
$sid = [guid]::NewGuid().ToString()
$claude = (wsl.exe -d Ubuntu -- bash -lc 'command -v claude').Trim()
Start-Process wt.exe -ArgumentList @('wsl.exe','-d','Ubuntu','--cd','/home/passp/repos/GHA-bench','--',$claude,'--session-id',$sid)

# Initial-prompt — note the prompt is a single quoted argument:
$claude = (wsl.exe -d Ubuntu -- bash -lc 'command -v claude').Trim()
Start-Process wt.exe -ArgumentList @('wsl.exe','-d','Ubuntu','--cd','/home/passp/repos/GHA-bench','--',$claude,'Stand by for instructions.')
```

Inside WSL / Linux (bash, via Windows interop):

```bash
# Default — new session by id:
wt.exe wsl.exe -d Ubuntu --cd /home/passp/repos/GHA-bench -- "$(command -v claude)" \
  --session-id "$(cat /proc/sys/kernel/random/uuid)" &

# Initial-prompt — the whole prompt is a single quoted argument:
wt.exe wsl.exe -d Ubuntu --cd /home/passp/repos/GHA-bench -- "$(command -v claude)" \
  "Stand by for instructions." &
```

After launching, the session is the user's to drive — don't try to interact with it
from here. If they asked you to "launch and ignore," confirm it's up and stop.
