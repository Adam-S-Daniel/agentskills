# Purpose — launch-wsl-claude-session

Maintenance context only; never loaded at inference.

## What it is for

Opening a detached, interactive Claude Code session inside WSL from either
side of the boundary (Windows PowerShell or WSL bash), optionally seeded with
an initial prompt, in a real Windows Terminal ConPTY. It is how one session
hands a task to a fresh one on the same machine.

## Why it is `adam-local`

It drives `wt.exe` and `wsl.exe` on the local machine; nothing about it works
in a hosted session.

## The failure shapes it was hardened against

- **A malformed TTY.** A bare `wsl.exe` spawn gets no proper ConPTY and an
  initial-prompt session exits immediately; hence `wt.exe`.
- **A missing PATH.** `wsl.exe` does not run the login shell's rc files, so
  the agent's subprocesses could not find `pwsh`, `bun`, etc.; hence the
  captured login PATH injected with `env`, not a wrapping interactive shell.
- **A prompt split by `;` (2026-09-24).** `wt.exe` re-parses its command line
  and treats an unescaped `;` as a new-tab separator even inside a quoted
  argument. Prompts containing `;` opened the real tab with the prompt
  truncated, plus a stray tab trying to execute the remainder
  (`0x80070002`). Both launchers now escape `;` as `\;`, and the `.ps1` quotes
  each argument, because `Start-Process -ArgumentList <array>` does not.
