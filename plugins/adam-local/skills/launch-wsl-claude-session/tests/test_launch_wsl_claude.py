"""Regression tests for the wt.exe ';' escaping bug in launch-wsl-claude.sh
and launch-wsl-claude.ps1.

wt.exe re-parses its OWN command line and treats an unescaped ';' as a
subcommand separator (new-tab) EVEN when it arrives inside an argument that
was already a single, correctly quoted argv element — quoting alone does not
protect it. Windows Terminal's documented escape is a literal backslash
before the semicolon (`\\;`). Without it, a prompt like
"Work issue #5; it has evidence" opened the real tab with the prompt
truncated at the ';', plus a stray tab trying to run the remainder as a
command (error 0x80070002).

Both scripts are run for real — never reimplemented in Python — against
hermetic stubs, so a regression in the actual escaping/quoting logic fails
these tests:

- launch-wsl-claude.sh: real `bash`, with stub `claude` and `wt.exe`
  executables placed on PATH and `LAUNCH_WSL_CLAUDE_DRY_RUN=1`. The real
  launch backgrounds wt.exe with `&` and `disown`s it (so remote-controlled
  sessions survive the launching shell exiting), which makes the resulting
  process nondeterministic to wait on from a test — `disown` also detaches
  it from this shell's job table, so even `wait "$pid"` no longer blocks on
  it (confirmed by hand: a disowned 1s sleep job returns from `wait`
  immediately). The dry-run mode sidesteps all of that: it prints the
  final, already-escaped wt.exe argv, one per line, and exits before ever
  launching anything.
- launch-wsl-claude.ps1: real `pwsh`, with `wsl.exe`/`wt.exe` PowerShell
  *functions* defined ahead of it in the same session. Functions take
  precedence over external commands of the same bare name (verified by
  hand: a `function wsl.exe { ... }` shadowed the real
  C:\\Windows\\System32\\wsl.exe on a machine that has it installed), so
  this needs no real WSL distro or Windows Terminal — and the script's
  `-PrintArgs` switch prints the final command line instead of calling
  Start-Process.

Both scripts always run against their own real source — never a
reimplementation of the escaping — so a regression in either script fails
here.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
SH_SCRIPT = Path(
    os.environ.get("LAUNCH_WSL_CLAUDE_SH")
    or SKILL_DIR / "scripts" / "launch-wsl-claude.sh"
)
PS1_SCRIPT = Path(
    os.environ.get("LAUNCH_WSL_CLAUDE_PS1")
    or SKILL_DIR / "scripts" / "launch-wsl-claude.ps1"
)

BASH = shutil.which("bash")
PWSH = os.environ.get("LAUNCH_WSL_CLAUDE_PWSH") or shutil.which("pwsh")

# Decode subprocess output explicitly and never die on a stray byte — see
# sync-skills' tests for why `text=True` alone (locale-decoded, cp1252 on
# Windows) is not safe here.
TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}


# ---------------------------------------------------------------------------
# launch-wsl-claude.sh
# ---------------------------------------------------------------------------

def _make_executable_stub(path: Path) -> None:
    path.write_text("#!/usr/bin/env bash\necho stub\n")
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture()
def sh_argv(tmp_path):
    """Returns a function that runs the real .sh script and returns the
    dry-run argv it would have handed to wt.exe, one element per line.
    """
    if not BASH:
        pytest.skip("bash is not on PATH")

    bin_dir = tmp_path / "bin"
    home_dir = tmp_path / "home"
    bin_dir.mkdir()
    home_dir.mkdir()
    _make_executable_stub(bin_dir / "claude")
    _make_executable_stub(bin_dir / "wt.exe")
    # `bash -lic` (used by the script to capture the login PATH) is an
    # interactive login shell: without these it either prints a "no
    # ~/.bash_profile" warning or (some bash builds) auto-creates one —
    # neither is hermetic.
    (home_dir / ".bash_profile").write_text("")
    (home_dir / ".bashrc").write_text("")

    env = dict(
        HOME=str(home_dir),
        PATH=f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        LAUNCH_WSL_CLAUDE_DRY_RUN="1",
    )

    def run(*args: str) -> list[str]:
        try:
            proc = subprocess.run(
                [BASH, str(SH_SCRIPT), *args],
                env=env,
                capture_output=True,
                timeout=30,
                **TEXT,
            )
        except OSError as exc:
            pytest.skip(f"could not run bash hermetically: {exc}")
        if proc.returncode != 0:
            if sys.platform.startswith("win"):
                # launch-wsl-claude.sh assumes a real Linux/WSL userland
                # (e.g. /proc/sys/kernel/random/uuid for the session-id
                # branch, which a Windows bash like Git Bash does not have).
                # Every case below supplies --prompt precisely to avoid that
                # branch; a failure here on Windows means something else
                # about this host's bash isn't hermetic enough to trust —
                # skip rather than report a false regression.
                pytest.skip(
                    "launch-wsl-claude.sh did not run hermetically on this "
                    f"Windows bash (exit {proc.returncode}):\n{proc.stdout}{proc.stderr}"
                )
            raise AssertionError(proc.stdout + proc.stderr)
        return proc.stdout.splitlines()

    return run


def test_sh_prompt_with_semicolon_and_spaces_is_one_escaped_arg(sh_argv):
    argv = sh_argv(
        "--dir", "/home/x/repo",
        "--prompt", "Work issue #5; it has evidence and more",
    )
    # The prompt is the last argv element handed to wt.exe, and it must
    # arrive as ONE element (dry-run prints one per line) with every ';'
    # escaped to '\;'.
    assert argv[-1] == r"Work issue #5\; it has evidence and more"


def test_sh_prompt_without_semicolon_is_unchanged(sh_argv):
    argv = sh_argv(
        "--dir", "/home/x/repo",
        "--prompt", "no semicolons in this one",
    )
    assert argv[-1] == "no semicolons in this one"


def test_sh_remote_control_name_with_semicolon_is_escaped(sh_argv):
    argv = sh_argv(
        "--dir", "/home/x/repo",
        "--prompt", "stand by",
        "--remote-control-name", "my;rc",
    )
    idx = argv.index("--remote-control")
    assert argv[idx + 1] == r"my\;rc"


def test_sh_dir_is_passed_through_as_a_single_arg(sh_argv):
    argv = sh_argv("--dir", "/home/x/repo", "--prompt", "hi")
    assert "--cd" in argv
    assert argv[argv.index("--cd") + 1] == "/home/x/repo"


# ---------------------------------------------------------------------------
# launch-wsl-claude.ps1
# ---------------------------------------------------------------------------

# A thin wrapper that shadows wsl.exe/wt.exe with PowerShell functions (which
# take precedence over external commands of the same name) before invoking
# the real script with -PrintArgs, so this needs no real WSL distro or
# Windows Terminal install. It forwards every bound parameter through to the
# real script untouched — the wrapper itself never sees or reconstructs the
# argument text, so it cannot introduce or hide an escaping bug.
_WRAPPER_PS1 = textwrap.dedent(
    """\
    [CmdletBinding()]
    param(
      [string] $Dir,
      [string] $Prompt,
      [string] $Distro = 'Ubuntu',
      [string] $RemoteControlName,
      [switch] $NoWindowsTerminal,
      [switch] $PrintArgs
    )

    function wsl.exe {
      $joined = $args -join ' '
      if ($joined -match 'printf') {
        Write-Output $env:LAUNCH_WSL_CLAUDE_TEST_LOGIN_PATH
      } else {
        Write-Output $env:LAUNCH_WSL_CLAUDE_TEST_CLAUDE_PATH
      }
    }
    function wt.exe {
      Write-Output "WT_STUB_SHOULD_NOT_BE_CALLED: $args"
    }

    $splat = @{}
    foreach ($k in $PSBoundParameters.Keys) { $splat[$k] = $PSBoundParameters[$k] }
    & $env:LAUNCH_WSL_CLAUDE_REAL_SCRIPT @splat
    """
)


@pytest.fixture()
def ps1_argv(tmp_path):
    """Returns a function that runs the real .ps1 script (via the stub
    wrapper above) with -PrintArgs and returns the final command line wt.exe
    (or, with -NoWindowsTerminal, wsl.exe) would have received.
    """
    if not PWSH:
        pytest.skip("pwsh (PowerShell 7+) is not on PATH")

    wrapper = tmp_path / "wrapper.ps1"
    wrapper.write_text(_WRAPPER_PS1)

    env = dict(os.environ)
    env.update(
        LAUNCH_WSL_CLAUDE_REAL_SCRIPT=str(PS1_SCRIPT),
        LAUNCH_WSL_CLAUDE_TEST_LOGIN_PATH="/usr/bin:/fake/login/bin",
        LAUNCH_WSL_CLAUDE_TEST_CLAUDE_PATH="/fake/claude/bin/claude",
    )

    def run(*args: str) -> str:
        proc = subprocess.run(
            [PWSH, "-NoProfile", "-File", str(wrapper), *args],
            env=env,
            capture_output=True,
            timeout=60,
            **TEXT,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        # The stub never runs the real wt.exe/wsl.exe, so the last
        # non-empty line is always -PrintArgs' one-line command string.
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        assert lines, proc.stdout + proc.stderr
        return lines[-1]

    return run


def test_ps1_prompt_with_semicolon_and_spaces_is_one_escaped_arg(ps1_argv):
    cmdline = ps1_argv(
        "-Dir", "/home/x/repo",
        "-Prompt", "Work issue #5; it has evidence and more",
        "-PrintArgs",
    )
    assert cmdline.endswith(r'"Work issue #5\; it has evidence and more"')
    assert "WT_STUB_SHOULD_NOT_BE_CALLED" not in cmdline


def test_ps1_prompt_without_semicolon_is_unchanged_content(ps1_argv):
    cmdline = ps1_argv(
        "-Dir", "/home/x/repo",
        "-Prompt", "no semicolons in this one",
        "-PrintArgs",
    )
    # Still quoted as one argument (it contains spaces), but no backslash
    # was introduced anywhere in it.
    assert cmdline.endswith('"no semicolons in this one"')
    assert "\\" not in cmdline


def test_ps1_remote_control_name_with_semicolon_is_escaped(ps1_argv):
    cmdline = ps1_argv(
        "-Dir", "/home/x/repo",
        "-RemoteControlName", "my;rc",
        "-PrintArgs",
    )
    assert "--remote-control my\\;rc" in cmdline


def test_ps1_no_windows_terminal_fallback_does_not_escape_semicolons(ps1_argv):
    # No wt.exe tokenizer involved on this path, so ';' must survive as a
    # literal character rather than picking up a backslash nothing will
    # ever strip back out.
    cmdline = ps1_argv(
        "-Dir", "/home/x/repo",
        "-Prompt", "a;b",
        "-NoWindowsTerminal",
        "-PrintArgs",
    )
    assert cmdline.endswith("a;b")
    assert r"a\;b" not in cmdline
