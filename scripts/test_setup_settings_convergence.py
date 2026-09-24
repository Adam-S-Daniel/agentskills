#!/usr/bin/env python3
"""Tests for setup.sh's ~/.claude/settings.json convergence block.

Hermetic: every test runs the convergence against a throwaway `HOME` under
pytest's `tmp_path`, so nothing here reads or writes the developer's real
settings — which is the one file on the machine where a bug would be both
invisible and global.

WHY THIS PARSES JSON AND NEVER GREPS THE FILE
ADR 0010's "How to verify" says so, and the reason is the bug class this
block can actually have. `deep_merge` writes a nested structure; a regex over
the rendered text passes on `"syncClaudeAiSkills": false` appearing ANYWHERE,
including inside a marketplace entry, inside a string, or twice. The question
is what `json.loads` gets, so that is what is asked.

WHY IT RUNS THE REAL SCRIPT RATHER THAN A COPY OF THE HEREDOC
A copy is a second implementation, and a test of a second implementation
asserts nothing about the one that ships. `setup.sh` does a great deal besides
this block (symlink farms across five agent homes, a global git hook), none of
which belongs in a settings test and some of which would touch the real
machine — so the block is extracted FROM THE SHIPPED FILE at run time and run
on its own. `test_the_extracted_block_is_the_shipped_one` is what keeps that
extraction honest: if the heredoc's markers ever move, it fails rather than
silently testing nothing.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SETUP = REPO / "setup.sh"

# The heredoc's own delimiters. Named here so the one test that cares about
# the extraction can say what it is pinning.
OPEN, CLOSE = "\"${PYTHON_CMD[@]}\" - <<'PYEOF'\n", "\nPYEOF\n"

# The shell section around it: interpreter selection, the heredoc, and the
# exit-code check. Run as shipped (see run_section) so "setup.sh fails instead
# of printing Setup complete." is asserted on the real text.
SECTION_OPEN, SECTION_CLOSE = "# >>> settings-convergence\n", "# <<< settings-convergence\n"


def convergence_block() -> str:
    """The Python the shipped setup.sh feeds to `$PYTHON_BIN`."""
    text = SETUP.read_text(encoding="utf-8")
    start = text.index(OPEN) + len(OPEN)
    return text[start:text.index(CLOSE, start)]


def run_convergence(home: Path, existing=None) -> str:
    """Run the shipped block against `home`, and return the file's RAW text.

    `existing` seeds ~/.claude/settings.json first — the state every real run
    but the first one starts from.

    Raw rather than parsed, because one case below is specifically about a
    file that does not parse, and a helper that parsed unconditionally would
    fail that test inside itself rather than letting it assert.
    """
    settings = home / ".claude" / "settings.json"
    if existing is not None:
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(
            existing if isinstance(existing, str)
            else json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-c", convergence_block()],
                          env={"HOME": str(home), "USERPROFILE": str(home),
                               "PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return settings.read_text(encoding="utf-8") if settings.exists() else ""


def converge(home: Path, existing=None) -> dict:
    """`run_convergence`, parsed. What every test but the invalid-JSON one wants."""
    raw = run_convergence(home, existing)
    return json.loads(raw) if raw else {}


def test_the_extracted_block_is_the_shipped_one():
    """The extraction is load-bearing, so it is asserted rather than assumed.

    A moved heredoc marker would make every test below run an empty string and
    pass, which is the shape of a suite that has quietly stopped testing.
    """
    block = convergence_block()
    assert "TARGET_ENABLED_PLUGINS" in block
    assert "def deep_merge" in block
    assert SETUP.read_text(encoding="utf-8").count(OPEN) == 1


def test_a_fresh_machine_gets_both_marketplaces_and_the_bundles(tmp_path):
    settings = converge(tmp_path)
    assert set(settings["extraKnownMarketplaces"]) == {
        "agentskills", "agentskills-private"}
    assert settings["enabledPlugins"]["adam@agentskills"] is True


def test_the_machine_bound_bundle_is_enabled_from_the_marketplace(tmp_path):
    """ADR 0010: `adam-local` comes from the marketplace, pinned, where it has
    been drifting on the account. Enabling it is half the decision — the other
    half is the opt-out below, and neither is safe alone: opting out without
    enabling would take sync-skills off the laptop entirely."""
    settings = converge(tmp_path)
    assert settings["enabledPlugins"]["adam-local@agentskills"] is True


def test_the_account_plugin_is_off_in_terminals(tmp_path):
    """ADR 0012: `adam-personal` is for claude.ai, the Desktop app, Chrome and
    mobile. A terminal signed in with the account would sync it as
    `adam-personal@synced` and load its skills a second time beside the pinned
    bundles, so the durable machine turns it off by name.

    `False`, the JSON boolean — `True` would be the opposite decision and a
    missing key would leave the synced copy on."""
    settings = converge(tmp_path)
    assert settings["enabledPlugins"]["adam-personal@synced"] is False


def test_an_operator_who_enabled_the_account_plugin_is_overridden(tmp_path):
    settings = converge(tmp_path, {"enabledPlugins": {"adam-personal@synced": True}})
    assert settings["enabledPlugins"]["adam-personal@synced"] is False


def test_the_account_skill_sync_is_turned_off(tmp_path):
    """ADR 0010: pinned channels own the terminal.

    `False`, the JSON boolean, not the string "false" and not 0 — the CLI
    honours only `false`, so a truthy stand-in is an opt-out that silently
    does not happen.
    """
    settings = converge(tmp_path)
    assert settings["syncClaudeAiSkills"] is False


def test_the_plugin_sync_is_left_alone(tmp_path):
    """ADR 0010 deferred this to E6 (#160). E6 found the account's plugins
    syncing (Anthropic's six since 2026-09-20), and ADR 0012 turns off only
    the one this repo owns, `adam-personal@synced`, by name. Writing
    `syncClaudeAiPlugins` either way would switch every account plugin at
    once, so the key stays absent."""
    assert "syncClaudeAiPlugins" not in converge(tmp_path)


def test_converging_twice_changes_nothing(tmp_path):
    """Idempotence, on the file rather than on the printed verdict: `setup.sh`
    is run on every machine after every bundle restructure, so a block that
    rewrote the file each time would churn a file other tools also write."""
    first = converge(tmp_path)
    path = tmp_path / ".claude" / "settings.json"
    before = path.read_text(encoding="utf-8")
    second = converge(tmp_path)
    assert second == first
    assert path.read_text(encoding="utf-8") == before


def test_an_operators_own_settings_survive(tmp_path):
    """The deep merge overwrites only the leaves it names. A settings file is
    not ours: it carries the operator's model, theme, permissions and hooks,
    and a convergence that flattened them would be a data loss no verdict here
    would mention."""
    settings = converge(tmp_path, {
        "model": "claude-opus-5",
        "permissions": {"allow": ["Bash(git status)"]},
        "extraKnownMarketplaces": {
            "someone-elses": {"source": {"source": "github",
                                         "repo": "other/marketplace"}}},
        "enabledPlugins": {"theirs@someone-elses": True},
    })
    assert settings["model"] == "claude-opus-5"
    assert settings["permissions"] == {"allow": ["Bash(git status)"]}
    assert settings["extraKnownMarketplaces"]["someone-elses"]["source"]["repo"] \
        == "other/marketplace"
    assert settings["enabledPlugins"]["theirs@someone-elses"] is True
    # and ours are there too
    assert settings["enabledPlugins"]["adam-local@agentskills"] is True
    assert settings["syncClaudeAiSkills"] is False


def test_an_operator_who_turned_sync_back_on_is_overridden(tmp_path):
    """Convergence means convergence. `setup.sh` is the machine's statement of
    what a durable machine looks like, and a key it declines to re-assert is a
    key it does not own. Recorded as a test because the alternative reading —
    "don't touch what the operator changed" — is defensible and is NOT what
    ADR 0010 decided; anyone who wants the other behaviour is changing the
    decision, not fixing a bug."""
    settings = converge(tmp_path, {"syncClaudeAiSkills": True})
    assert settings["syncClaudeAiSkills"] is False


UNREADABLE = {
    "invalid-json": ('{"model": "claude-opus-5",,}', "invalid JSON"),
    "non-object-top-level": ('["model", "claude-opus-5"]\n', "does not contain a JSON object"),
}


@pytest.mark.parametrize("case", sorted(UNREADABLE))
def test_an_unreadable_file_is_left_untouched_and_fails(tmp_path, case):
    """A settings file we cannot read is left byte-for-byte alone — it is more
    likely mid-edit than corrupt — AND the block exits non-zero, because a file
    left alone is a file not converged. This used to be a WARNING and exit 0,
    and setup.sh then reported success."""
    raw, message = UNREADABLE[case]
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw.encode("utf-8"))
    proc = subprocess.run([sys.executable, "-c", convergence_block()],
                          env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
                               "PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    assert proc.returncode != 0
    assert "settings: ERROR" in proc.stderr and message in proc.stderr
    assert path.read_bytes() == raw.encode("utf-8")


def test_the_write_is_one_atomic_replace():
    """The update must never leave a moment with no settings.json. Asserted on
    the parsed Python of the block (not a text scan): os.replace is called and
    neither os.remove nor os.rename is. A timing test for a window this small
    would be flaky by construction, so the call itself is what is pinned."""
    import ast
    calls = set()
    for node in ast.walk(ast.parse(convergence_block())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
            calls.add(node.func.attr)
    assert "replace" in calls
    assert not calls & {"remove", "rename", "unlink"}


def run_block(home: Path, existing) -> subprocess.CompletedProcess:
    """`run_convergence` without the success assertion, for the failure cases."""
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return subprocess.run([sys.executable, "-c", convergence_block()],
                          env={"HOME": str(home), "USERPROFILE": str(home),
                               "PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


@pytest.mark.parametrize("container", ["enabledPlugins", "extraKnownMarketplaces"])
@pytest.mark.parametrize("value", [["adam@agentskills"], "on", None, 1])
def test_a_non_object_container_fails_clearly_and_writes_nothing(tmp_path, container, value):
    existing = {"model": "claude-opus-5", container: value}
    path = tmp_path / ".claude" / "settings.json"
    proc = run_block(tmp_path, existing)
    before = json.dumps(existing, indent=2) + "\n"
    assert proc.returncode != 0
    assert "AttributeError" not in proc.stderr
    assert f"'{container}' is {type(value).__name__}, not a JSON object" in proc.stderr
    assert path.read_text(encoding="utf-8") == before


# --- the shell section: interpreter choice and exit-code propagation --------

def _posix_bash():
    """A POSIX bash, never Windows' System32 WSL launcher — the same resolver
    test_generate_skills_lock.py uses, for the same reason."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_generate_skills_lock import BASH
    # Off Windows that helper returns the bare name, which subprocess would
    # look up on the child's PATH — and run_section's PATH holds only stubs.
    return shutil.which(BASH) if BASH and os.name != "nt" else BASH


def section() -> str:
    text = SETUP.read_text(encoding="utf-8")
    start = text.index(SECTION_OPEN)
    end = text.index(SECTION_CLOSE, start) + len(SECTION_CLOSE)
    return text[start:end]


def tail() -> str:
    """Everything setup.sh runs after the section — where "Setup complete." is."""
    text = SETUP.read_text(encoding="utf-8")
    return text[text.index(SECTION_CLOSE) + len(SECTION_CLOSE):]


def write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)


STORE_STUB = 'echo "Python was not found; run without arguments to install from the Microsoft Store" >&2\nexit 49'


def run_section(home: Path, bin_dir: Path) -> subprocess.CompletedProcess:
    """The shipped section followed by the shipped tail, with PATH holding
    ONLY bin_dir — so the stubs placed there are the only interpreters found,
    on a Linux runner with a real python3 in /usr/bin as much as on Windows."""
    bash = _posix_bash()
    if bash is None:
        pytest.skip("no POSIX bash on this machine")
    # A file, not `bash -c <text>`: on Windows the argument crosses
    # CreateProcess quoting, which mangles a script this full of double quotes.
    script = home / "section.sh"
    # `set -u` first: the section runs under setup.sh's own options (its line
    # `set -u`), so an unset variable fails here exactly as it would there.
    script.write_text("set -u\n" + section() + tail(), encoding="utf-8", newline="\n")
    return subprocess.run([bash, script.as_posix()],
                          env={"HOME": str(home), "USERPROFILE": str(home),
                               "PATH": str(bin_dir)},
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def test_setup_sets_the_options_run_section_assumes():
    assert re.search(r"^set -u$", SETUP.read_text(encoding="utf-8"), re.M)


def real_python_stub(bin_dir: Path, name: str, guard: str = "") -> None:
    """A stub that runs the test's own Python, optionally only when `guard`
    (a shell condition) holds — otherwise it behaves like the Store stub."""
    run = 'exec "%s" "$@"' % Path(sys.executable).as_posix()
    if guard:
        run = "if %s; then shift; %s; fi\n%s" % (guard, run, STORE_STUB)
    write_stub(bin_dir, name, run)


@pytest.mark.parametrize("case", sorted(UNREADABLE))
def test_an_unreadable_file_fails_setup(tmp_path, case):
    raw, _ = UNREADABLE[case]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_python_stub(bin_dir, "python3")
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw.encode("utf-8"))
    proc = run_section(tmp_path, bin_dir)
    assert proc.returncode != 0
    assert "Setup complete." not in proc.stdout
    assert path.read_bytes() == raw.encode("utf-8")


def test_python_2_is_skipped_for_a_later_candidate(tmp_path):
    """A `python3` that answers the version probe with 1 (what Python 2 does
    with `sys.exit(sys.version_info < (3, 3))`) is passed over."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir, "python3", 'echo "python3 was run" >&2\nexit 1')
    real_python_stub(bin_dir, "python")
    proc = run_section(tmp_path, bin_dir)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "settings: probing python3" in proc.stdout
    assert "settings: probing python..." in proc.stdout
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["enabledPlugins"]["adam-personal@synced"] is False


def test_py_dash_3_is_chosen_and_passed_as_two_words(tmp_path):
    """python3 and python are Store stubs; `py` works ONLY when its first
    argument is exactly `-3`. So success proves `py -3` was split into two
    words for both the probe and the heredoc call — a single word "py -3" or a
    dropped flag would hit the stub branch and exit 49."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir, "python3", STORE_STUB)
    write_stub(bin_dir, "python", STORE_STUB)
    real_python_stub(bin_dir, "py", guard='[ "$1" = "-3" ]')
    proc = run_section(tmp_path, bin_dir)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "settings: probing py -3" in proc.stdout
    assert "Setup complete." in proc.stdout
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["enabledPlugins"]["adam-personal@synced"] is False


def test_the_section_markers_are_the_shipped_ones():
    text = SETUP.read_text(encoding="utf-8")
    assert text.count(SECTION_OPEN) == 1 and text.count(SECTION_CLOSE) == 1
    assert OPEN in section()
    assert "Setup complete." in tail() and "Setup complete." not in section()


def test_a_store_stub_interpreter_fails_setup_instead_of_completing(tmp_path):
    """Measured 2026-09-24 on a Windows home: python3 resolved to the
    WindowsApps stub, exited 49, and setup.sh still printed "Setup complete."
    with nothing written."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("python3", "python", "py"):
        write_stub(bin_dir, name, STORE_STUB)
    proc = run_section(tmp_path, bin_dir)
    assert proc.returncode != 0
    assert "Setup complete." not in proc.stdout
    assert "Microsoft Store stub" in proc.stderr
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_a_working_interpreter_after_a_stub_is_chosen(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir, "python3", STORE_STUB)
    write_stub(bin_dir, "python", 'exec "%s" "$@"' % Path(sys.executable).as_posix())
    proc = run_section(tmp_path, bin_dir)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "Setup complete." in proc.stdout
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["enabledPlugins"]["adam-personal@synced"] is False


def test_a_failing_convergence_fails_setup(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir, "python3", 'exec "%s" "$@"' % Path(sys.executable).as_posix())
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"enabledPlugins": []}\n', encoding="utf-8")
    proc = run_section(tmp_path, bin_dir)
    assert proc.returncode != 0
    assert "Setup complete." not in proc.stdout
    assert "convergence failed" in proc.stderr
    assert path.read_text(encoding="utf-8") == '{"enabledPlugins": []}\n'


def test_the_adr_that_decided_this_is_on_disk_and_accepted():
    """The keys above are a decision, not a preference, and the decision is the
    ADR. A test that only checked the JSON would let the ADR be deleted or
    flipped to Rejected while the code kept converging its opposite."""
    adr = (REPO / "docs" / "decisions"
           / "0010-let-pinned-channels-own-the-terminal.md").read_text(encoding="utf-8")
    assert re.search(r"^- \*\*Status:\*\* Accepted\b", adr, re.M)
    assert "syncClaudeAiSkills" in adr and "adam-local@agentskills" in adr
    index = (REPO / "docs" / "decisions" / "README.md").read_text(encoding="utf-8")
    assert "0010-let-pinned-channels-own-the-terminal.md" in index
