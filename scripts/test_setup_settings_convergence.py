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
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SETUP = REPO / "setup.sh"

# The heredoc's own delimiters. Named here so the one test that cares about
# the extraction can say what it is pinning.
OPEN, CLOSE = "\"$PYTHON_BIN\" - <<'PYEOF'\n", "\nPYEOF\n"


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


def test_invalid_json_is_left_untouched(tmp_path):
    """Pre-existing behaviour, pinned because the new keys must not become a
    reason to overwrite a file we could not read. A settings file that fails to
    parse is more likely mid-edit than corrupt."""
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"model": "claude-opus-5",,}', encoding="utf-8")
    assert run_convergence(tmp_path) == '{"model": "claude-opus-5",,}'


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
