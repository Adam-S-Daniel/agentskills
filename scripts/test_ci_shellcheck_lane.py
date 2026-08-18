"""Guards on the shellcheck CI lane (issue #93, item 5).

The lane's own comment block has twice had to record coverage it did not
actually have. This turns the two properties that were missing into
assertions instead of prose:

  * files are selected by SHEBANG as well as by name — git chooses the name
    `pre-push` for sync-skills' hook and gives it no extension, so a
    `-name '*.sh'` glob could never match the most frequently executed bash
    in the repo (setup.sh registers it as a GLOBAL git hook, so it runs on
    every push in every repo on the machine); and

  * the severity floor admits warnings — the defect that exposed the gap was
    SC2034 (assigned but never used), which shellcheck rates a WARNING, so a
    lane pinned at `-S error` would have gone on passing over it even once
    the file was finally in scope. Coverage and severity had to move
    together or the fix would have been cosmetic.

These are assertions about ci.yml's text, which is weaker than running the
linter. The last test therefore runs the real thing whenever a shellcheck
binary is on PATH, and skips when it is not — the repo's own shellcheck job
is where that always executes.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CI_WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
PRE_PUSH_HOOK = (
    REPO / "plugins" / "adam-local" / "skills" / "sync-skills" / "hooks" / "pre-push"
)


def lane_body() -> str:
    """The `Run shellcheck` step's ``run:`` block, dedented.

    Parsed off the raw text rather than via a YAML loader so this file has no
    third-party dependency: it runs in the same pytest job as the scripts/
    suites, and a guard that needs an extra pin is a guard that gets dropped.
    """
    lines = CI_WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(
        (i for i, ln in enumerate(lines) if ln.strip() == "- name: Run shellcheck"),
        None,
    )
    assert start is not None, "no 'Run shellcheck' step in ci.yml"
    run_at = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")

    first = lines[run_at + 1]
    indent = len(first) - len(first.lstrip())
    body = []
    for line in lines[run_at + 1:]:
        if line.strip() and (len(line) - len(line.lstrip())) < indent:
            break
        body.append(line[indent:] if line.strip() else "")
    return "\n".join(body)


def lane_commands() -> str:
    """The lane with comment lines stripped.

    The rationale comments quote the very flags under test (`-S error` is
    named there as the thing NOT to go back to), so asserting against the
    whole block would let a comment satisfy a test about behaviour.
    """
    return "\n".join(
        line
        for line in lane_body().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def test_the_lane_exists_and_runs_shellcheck():
    assert "shellcheck" in lane_commands()


def test_severity_floor_admits_warnings():
    """SC2034 is a warning. `-S error` cannot see the defect that started this."""
    commands = lane_commands()
    assert "-S error" not in commands
    assert "-S warning" in commands


def test_selection_reads_the_shebang_not_just_the_extension():
    """A file git insists on calling `pre-push` has no extension to match."""
    commands = lane_commands()
    assert "head -n 1" in commands


def test_selection_still_covers_the_whole_tree():
    """The lane's standing rule: target a TREE, never a hand-listed set."""
    commands = lane_commands()
    assert "find ." in commands
    assert "*.sh" in commands


def test_an_empty_file_list_still_fails_the_step():
    """A green step that checked nothing is the bug this lane keeps re-fixing.

    `xargs` must not be given --no-run-if-empty: with no input shellcheck
    exits non-zero ("No files specified") instead of passing vacuously.
    """
    commands = lane_commands()
    xargs_lines = [ln for ln in commands.splitlines() if "xargs" in ln]
    assert xargs_lines, "the lane no longer pipes the file list into xargs"
    for line in xargs_lines:
        # Checked on the xargs invocation only: `-r` is also how the file
        # loop reads a line (`IFS= read -r f`), which is unrelated.
        tokens = line[line.index("xargs"):].split()
        assert "-r" not in tokens
        assert "--no-run-if-empty" not in tokens


def test_the_pre_push_hook_has_no_extension_to_match_on():
    """Locks the premise. If this ever gains `.sh`, the shebang rule is moot."""
    assert PRE_PUSH_HOOK.is_file()
    assert PRE_PUSH_HOOK.suffix == ""


def test_the_hook_starts_with_a_shell_shebang():
    """...and is therefore selectable by the rule above."""
    first_line = PRE_PUSH_HOOK.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#!")
    assert first_line.rstrip().split("/")[-1].split()[-1] in {
        "sh", "bash", "dash", "ksh",
    }


@pytest.mark.skipif(
    shutil.which("shellcheck") is None, reason="shellcheck is not installed here"
)
def test_the_pre_push_hook_is_clean_at_warning_severity():
    proc = subprocess.run(
        ["shellcheck", "-S", "warning", str(PRE_PUSH_HOOK)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
