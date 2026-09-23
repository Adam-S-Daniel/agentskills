"""Regression tests for Sync-ClaudeSettings.ps1, driven through pwsh.

Every test runs the real script against two fixture settings.json files in
tmp_path, never against a real home. The findings these pin are in
https://github.com/Adam-S-Daniel/agentskills/issues/170.

Environment knobs (both optional):

- SYNC_CC_SETTINGS_SCRIPT: run a different copy of the script, e.g. an
  origin/main export, to prove these tests fail against the unfixed code.
- SYNC_CC_PWSH: the pwsh executable. Defaults to `pwsh` on PATH; the module
  skips when there is none. Naming a Windows `pwsh.exe` from inside WSL works:
  paths are then handed over in Windows form via `wslpath -w`, which is the
  same \\\\wsl.localhost\\... shape the script meets in real use.

The "newer" side is fixed with os.utime, so no test depends on the clock;
backup names carry a timestamp and are matched by glob.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT = Path(
    os.environ.get("SYNC_CC_SETTINGS_SCRIPT")
    or HERE.parent / "scripts" / "Sync-ClaudeSettings.ps1"
)
PWSH = os.environ.get("SYNC_CC_PWSH") or shutil.which("pwsh")
if not PWSH:
    pytest.skip("pwsh (PowerShell 7+) is not on PATH", allow_module_level=True)

# A Windows pwsh.exe driven from Linux (WSL interop) needs Windows paths.
_WINDOWS_PWSH_FROM_LINUX = sys.platform.startswith("linux") and PWSH.lower().endswith(".exe")

BOM = b"\xef\xbb\xbf"
OLDER = 1_600_000_000
NEWER = 1_700_000_000

PER_FILE_KEYS = {
    "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "bash ~/h.sh"}]}]},
    "statusLine": {"type": "command", "command": "~/.claude/statusline.sh"},
    "subagentStatusLine": {"type": "command", "command": "~/sub.sh"},
    "fileSuggestion": {"type": "command", "command": "~/files.sh"},
    "apiKeyHelper": "/bin/key.sh",
    "awsAuthRefresh": "aws sso login",
    "awsCredentialExport": "/bin/aws.sh",
    "gcpAuthRefresh": "gcloud auth application-default login",
    "otelHeadersHelper": "/bin/otel.sh",
    "processWrapper": "/usr/local/bin/wrap",
    "autoMemoryDirectory": "~/mem",
    "claudeMdExcludes": ["/home/example/vendor/CLAUDE.md"],
    "sandbox": {"enabled": True},
    "defaultShell": "bash",
    "syncClaudeAiSkills": False,
    "syncClaudeAiPlugins": False,
}


def _host_path(p: Path) -> str:
    if not _WINDOWS_PWSH_FROM_LINUX:
        return str(p)
    return subprocess.run(
        ["wslpath", "-w", str(p)], check=True, capture_output=True, text=True
    ).stdout.strip()


def _write(path: Path, data: dict, *, newline: str = "\n", bom: bool = False) -> None:
    text = json.dumps(data, indent=2).replace("\n", newline) + newline
    path.write_bytes((BOM if bom else b"") + text.encode("utf-8"))


def _read(path: Path):
    raw = path.read_bytes()
    if raw.startswith(BOM):
        raw = raw[len(BOM):]
    return json.loads(raw.decode("utf-8"))


def _pair(tmp_path: Path, win: dict, wsl: dict, **fmt) -> tuple[Path, Path]:
    """Two fixture files; Windows is the newer one."""
    wdir = tmp_path / "win"
    ldir = tmp_path / "wsl"
    wdir.mkdir()
    ldir.mkdir()
    w = wdir / "settings.json"
    l = ldir / "settings.json"
    _write(w, win, newline=fmt.get("win_newline", "\n"), bom=fmt.get("win_bom", False))
    _write(l, wsl, newline=fmt.get("wsl_newline", "\n"), bom=fmt.get("wsl_bom", False))
    os.utime(w, (NEWER, NEWER))
    os.utime(l, (OLDER, OLDER))
    return w, l


def _run(w: Path, l: Path, *args: str, answers: str = "") -> subprocess.CompletedProcess:
    # Answers go to Read-Host on stdin; without -AssumeYes a prompt that
    # finds no answer loops, so the timeout turns a hang into a failure.
    proc = subprocess.run(
        [
            PWSH, "-NoProfile", "-File", _host_path(SCRIPT),
            "-WindowsSettingsPath", _host_path(w),
            "-WslSettingsPath", _host_path(l),
            *args,
        ],
        input=answers.encode("utf-8"),
        capture_output=True,
        timeout=180,
    )
    out = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    assert proc.returncode == 0, out
    proc.output = out  # type: ignore[attr-defined]
    return proc


def test_identical_inputs_come_back_json_equal_with_arrays_intact(tmp_path):
    # Arrays on every code path: unlisted top-level, the permissions unions,
    # a permissions per-file key, a per-file top-level key and spinnerVerbs.
    settings = {
        "availableModels": ["opus"],
        "someEmptyList": [],
        "twoItems": ["a", "b"],
        "claudeMdExcludes": [],
        "permissions": {
            "allow": ["Bash(x)"],
            "ask": [],
            "deny": ["Bash(y)"],
            "additionalDirectories": [],
        },
        "spinnerVerbs": {"mode": "append", "verbs": ["Pondering"]},
        "env": {"A": "1"},
    }
    w, l = _pair(tmp_path, settings, settings)
    _run(w, l, "-AssumeYes")
    assert _read(w) == settings
    assert _read(l) == settings


def test_a_conflicting_one_element_array_stays_an_array(tmp_path):
    w, l = _pair(tmp_path, {"availableModels": ["opus"]}, {"availableModels": ["a", "b"]})
    _run(w, l, "-AssumeYes")
    assert _read(w)["availableModels"] == ["opus"]
    assert _read(l)["availableModels"] == ["opus"]


def test_a_one_sided_empty_array_is_copied_as_an_empty_array(tmp_path):
    w, l = _pair(tmp_path, {"someEmptyList": [], "permissions": {"ask": ["Bash(a)"]}}, {})
    _run(w, l, "-AssumeYes")
    for side in (w, l):
        data = _read(side)
        assert data["someEmptyList"] == []
        assert data["permissions"]["ask"] == ["Bash(a)"]


def test_skip_keeps_each_files_own_value(tmp_path):
    w, l = _pair(
        tmp_path,
        {"theme": "dark", "env": {"A": "win"}, "permissions": {"defaultMode": "plan"}},
        {"theme": "light", "env": {"A": "wsl"}, "permissions": {"defaultMode": "auto"}},
    )
    _run(w, l, answers="s\n" * 10)
    win, wsl = _read(w), _read(l)
    assert win["theme"] == "dark" and wsl["theme"] == "light"
    assert win["env"]["A"] == "win" and wsl["env"]["A"] == "wsl"
    assert win["permissions"]["defaultMode"] == "plan"
    assert wsl["permissions"]["defaultMode"] == "auto"


def test_os_bound_keys_stay_per_file_and_are_never_copied_across(tmp_path):
    wsl_settings = dict(PER_FILE_KEYS)
    wsl_settings["permissions"] = {"additionalDirectories": ["/home/example/src"]}
    w, l = _pair(tmp_path, {"theme": "dark"}, wsl_settings)
    _run(w, l, "-AssumeYes")
    win, wsl = _read(w), _read(l)
    leaked = sorted(k for k in PER_FILE_KEYS if k in win)
    assert leaked == [], f"copied into the Windows file: {leaked}"
    assert "additionalDirectories" not in win.get("permissions", {})
    for k, v in PER_FILE_KEYS.items():
        assert wsl[k] == v, k
    assert wsl["permissions"]["additionalDirectories"] == ["/home/example/src"]


def test_per_file_keys_keep_different_values_on_each_side(tmp_path):
    w, l = _pair(
        tmp_path,
        {"hooks": {"Stop": []}, "syncClaudeAiSkills": True},
        {"hooks": {"SessionStart": []}, "syncClaudeAiSkills": False},
    )
    _run(w, l, "-AssumeYes")
    assert _read(w) == {"hooks": {"Stop": []}, "syncClaudeAiSkills": True}
    assert _read(l) == {"hooks": {"SessionStart": []}, "syncClaudeAiSkills": False}


def test_permissions_ask_is_unioned_like_allow_and_deny(tmp_path):
    w, l = _pair(
        tmp_path,
        {"permissions": {"ask": ["Bash(a)"], "allow": ["Read"]}},
        {"permissions": {"ask": ["Bash(b)"], "deny": ["Write"]}},
    )
    _run(w, l, "-AssumeYes")
    expected = {"allow": ["Read"], "ask": ["Bash(a)", "Bash(b)"], "deny": ["Write"]}
    assert _read(w)["permissions"] == expected
    assert _read(l)["permissions"] == expected


def test_dry_run_prints_no_env_value_and_writes_nothing(tmp_path):
    w, l = _pair(
        tmp_path,
        {"env": {"API_TOKEN": "sentinel-win-7f3a91"}, "theme": "dark"},
        {"env": {"API_TOKEN": "sentinel-wsl-c02be4", "OTHER": "sentinel-other-5d11"}},
    )
    before = (w.read_bytes(), l.read_bytes())
    out = _run(w, l, "-DryRun", "-AssumeYes").output
    assert "sentinel-" not in out
    assert "API_TOKEN" in out and "OTHER" in out
    assert (w.read_bytes(), l.read_bytes()) == before
    assert list(tmp_path.glob("*/*.bak")) == []


@pytest.mark.parametrize(
    "win_fmt,wsl_fmt",
    [(("\r\n", True), ("\n", False)), (("\n", False), ("\r\n", True))],
    ids=["win-crlf-bom", "win-lf-nobom"],
)
def test_each_file_keeps_its_own_newline_style_and_bom(tmp_path, win_fmt, wsl_fmt):
    w, l = _pair(
        tmp_path,
        {"theme": "dark", "permissions": {"allow": ["a"]}},
        {"model": "opus"},
        win_newline=win_fmt[0], win_bom=win_fmt[1],
        wsl_newline=wsl_fmt[0], wsl_bom=wsl_fmt[1],
    )
    _run(w, l, "-AssumeYes")
    for path, (newline, bom) in ((w, win_fmt), (l, wsl_fmt)):
        raw = path.read_bytes()
        assert raw.startswith(BOM) == bom, path
        body = raw[len(BOM):] if bom else raw
        assert body.endswith(newline.encode()), path
        if newline == "\r\n":
            assert body.count(b"\n") == body.count(b"\r\n"), path
        else:
            assert b"\r" not in body, path
    # One timestamped backup per file, next to it.
    assert len(list(w.parent.glob("*-ET-settings.json.bak"))) == 1
    assert len(list(l.parent.glob("*-ET-settings.json.bak"))) == 1
