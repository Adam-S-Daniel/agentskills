#!/usr/bin/env python3
"""Tests for scripts/check_plugin_versions.py.

Hermetic and deterministic: every scenario builds a throwaway git repo under
pytest's `tmp_path` (the same approach scripts/test_generate_skills_lock.py
uses for its fixture registries, including the repo-local `user.name`/
`user.email` config so these pass with no global git identity available). No
network, no sleeps, no wall-clock dependence.

The load-bearing test is test_skill_edited_without_bump_fails: a fixture that
edits a SKILL.md under a bundle and leaves both manifests untouched must exit
NON-ZERO. AGENTS.md is explicit about why this one matters more than the
others: a gate never observed failing is a green light wired to nothing, and
this repo has already been bitten by exactly that (`python3 test_foo.py`
exiting 0 having run zero assertions).

Every scenario runs the script as a real subprocess via run_gate() — not by
importing and calling its functions — specifically so the load-bearing test
(and every other pass/fail test alongside it) proves the actual CLI exit
code CI depends on, not a Python return value one indirection away from it.

Run: python3 -m pytest scripts/test_check_plugin_versions.py -q
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
SCRIPT = SCRIPTS_DIR / "check_plugin_versions.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import check_plugin_versions as cpv  # noqa: E402


# ---------------------------------------------------------------------------
# fixture builders — same shape as test_generate_skills_lock.py's, adapted to
# a plugin bundle's own layout (plugin.json + .claude-plugin/plugin.json +
# skills/<name>/SKILL.md).
# ---------------------------------------------------------------------------

# Same reasoning as test_generate_skills_lock.py's identical constant: without
# this, a fixture repo inherits the developer's own `core.autocrlf`, which on
# Windows can rewrite line endings on the way into a blob and back out again —
# so what gets diffed is not byte-identical to what the fixture wrote.
_GIT_VERBATIM = ("-c", "core.autocrlf=false", "-c", "core.eol=lf")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *_GIT_VERBATIM, "-C", str(repo), *args],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _git_output(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *_GIT_VERBATIM, "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _write(path: Path, payload) -> None:
    # newline="": python's text mode would otherwise rewrite "\n" as
    # os.linesep, so a fixture written on Windows would not byte-match the
    # same fixture written on Linux — see test_generate_skills_lock.py's
    # identical note on its own _write().
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8", newline="")
    else:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="")


def _manifest_pair(name: str, version: str) -> tuple:
    root = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": name,
        "version": version,
        "description": "A fixture bundle.",
        "author": {"name": "Test"},
    }
    claude = {
        "name": name,
        "version": version,
        "description": "A fixture bundle.",
        "author": {"name": "Test"},
    }
    return root, claude


def write_bundle(repo_root: Path, name: str, version: str, skill_body: str = "body\n") -> None:
    """Write plugins/<name>/{plugin.json, .claude-plugin/plugin.json, skills/x/SKILL.md}."""
    root, claude = _manifest_pair(name, version)
    _write(repo_root / "plugins" / name / "plugin.json", root)
    _write(repo_root / "plugins" / name / ".claude-plugin" / "plugin.json", claude)
    _write(repo_root / "plugins" / name / "skills" / "x" / "SKILL.md", skill_body)


def set_version(repo_root: Path, name: str, which: str, version: str) -> None:
    """Rewrite just the `version` field of one of a bundle's two manifests.

    `which` is "root" (plugin.json) or "claude" (.claude-plugin/plugin.json),
    so a test can bump only one half — exactly the "only one manifest bumped"
    scenario check_agent_plugins.py's CROSS_CHECKED_FIELDS exists to catch
    from the other side, and this script must catch independently too.
    """
    rel = "plugin.json" if which == "root" else ".claude-plugin/plugin.json"
    path = repo_root / "plugins" / name / rel
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = version
    _write(path, data)


def init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")


def commit_all(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git_output(root, "rev-parse", "HEAD")


def base_fixture(tmp_path: Path, bundles=("alpha",)) -> tuple:
    """A repo with each named bundle at version 1.0.0, committed as `base`.

    Returns (repo_root, base_sha). Callers then mutate the working tree
    (uncommitted — the common case this script is meant to gate, an
    in-progress change — or commit a second `head` commit, exercised by
    test_change_committed_to_head_is_still_detected) before running the gate.
    """
    repo = tmp_path / "repo"
    init_repo(repo)
    for name in bundles:
        write_bundle(repo, name, "1.0.0")
    base = commit_all(repo, "base")
    return repo, base


def run_gate(base: str, repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--base", base, "--repo", str(repo)],
        capture_output=True, text=True,
    )


# ---------------------------------------------------------------------------
# parse_semver — pure function, no subprocess needed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("1.0.0", (1, 0, 0)),
    ("1.1.0", (1, 1, 0)),
    ("10.20.30", (10, 20, 30)),
    ("0.0.1", (0, 0, 1)),
])
def test_parse_semver_accepts_x_y_z(raw, expected):
    assert cpv.parse_semver(raw) == expected


@pytest.mark.parametrize("raw", [
    "1.0", "1.0.0.0", "1.0.0-rc1", "v1.0.0", None, 1.0, "", "latest",
])
def test_parse_semver_rejects_everything_else(raw):
    assert cpv.parse_semver(raw) is None


def test_parse_semver_tolerates_incidental_whitespace():
    # .strip() is deliberate: a JSON string value with incidental leading/
    # trailing whitespace is still unambiguously "1.0.0", and rejecting it
    # would be pedantry this script has no reason to enforce.
    assert cpv.parse_semver(" 1.0.0 ") == (1, 0, 0)


def test_semver_tuples_order_numerically_not_lexicographically():
    # A pure string comparison would put "10.0.0" before "9.0.0" — the
    # whole reason this script parses into an int tuple rather than
    # comparing the raw strings.
    assert cpv.parse_semver("10.0.0") > cpv.parse_semver("9.0.0")


# ---------------------------------------------------------------------------
# behavioural scenarios, run through the real CLI
# ---------------------------------------------------------------------------

def test_content_changed_and_version_bumped_passes(tmp_path):
    repo, base = base_fixture(tmp_path)
    (repo / "plugins" / "alpha" / "skills" / "x" / "SKILL.md").write_text(
        "changed body\n", encoding="utf-8"
    )
    set_version(repo, "alpha", "root", "1.1.0")
    set_version(repo, "alpha", "claude", "1.1.0")

    result = run_gate(base, repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "alpha" in result.stdout


def test_content_unchanged_no_bump_passes(tmp_path):
    repo, base = base_fixture(tmp_path)
    # Nothing touched at all — the bundle is untouched, so no bump is owed.

    result = run_gate(base, repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "unchanged" in result.stdout


def test_skill_edited_without_bump_fails(tmp_path):
    """THE LOAD-BEARING TEST.

    A SKILL.md under a bundle changes; both manifests are left exactly as
    they were at base. This must exit non-zero — if it does not, the gate
    this whole change exists to add is a light wired to nothing (AGENTS.md).
    """
    repo, base = base_fixture(tmp_path)
    (repo / "plugins" / "alpha" / "skills" / "x" / "SKILL.md").write_text(
        "changed body, no version bump\n", encoding="utf-8"
    )

    result = run_gate(base, repo)

    assert result.returncode != 0, (
        "the gate passed a bundle whose content changed with no version "
        f"bump — this must never happen.\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "alpha" in result.stdout
    assert "version" in result.stdout.lower()


def test_version_lowered_on_changed_content_fails(tmp_path):
    repo, base = base_fixture(tmp_path)
    (repo / "plugins" / "alpha" / "skills" / "x" / "SKILL.md").write_text(
        "changed body\n", encoding="utf-8"
    )
    set_version(repo, "alpha", "root", "0.9.0")
    set_version(repo, "alpha", "claude", "0.9.0")

    result = run_gate(base, repo)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "did not increase" in result.stdout


def test_only_one_manifest_bumped_fails(tmp_path):
    repo, base = base_fixture(tmp_path)
    (repo / "plugins" / "alpha" / "skills" / "x" / "SKILL.md").write_text(
        "changed body\n", encoding="utf-8"
    )
    set_version(repo, "alpha", "root", "1.1.0")
    # .claude-plugin/plugin.json deliberately left at 1.0.0.

    result = run_gate(base, repo)

    assert result.returncode != 0, result.stdout + result.stderr
    assert ".claude-plugin" in result.stdout


def test_newly_added_bundle_passes(tmp_path):
    repo, base = base_fixture(tmp_path, bundles=("alpha",))
    write_bundle(repo, "beta", "1.0.0")
    commit_all(repo, "add beta")

    result = run_gate(base, repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "beta" in result.stdout
    assert "newly added" in result.stdout


def test_change_committed_to_head_is_still_detected(tmp_path):
    """The gate must work whether the change sits in the working tree
    (uncommitted — the other tests here) or is already committed on top of
    `base` (the shape a finished PR head has in CI). Same failure scenario as
    the load-bearing test, but committed rather than left dirty."""
    repo, base = base_fixture(tmp_path)
    (repo / "plugins" / "alpha" / "skills" / "x" / "SKILL.md").write_text(
        "changed body, no version bump, and committed\n", encoding="utf-8"
    )
    commit_all(repo, "edit skill, forget the bump")

    result = run_gate(base, repo)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "alpha" in result.stdout


def test_two_bundles_only_the_changed_one_is_required_to_bump(tmp_path):
    """content changed + bumped for one bundle, untouched for the other —
    the untouched bundle must not be flagged."""
    repo, base = base_fixture(tmp_path, bundles=("alpha", "beta"))
    (repo / "plugins" / "beta" / "skills" / "x" / "SKILL.md").write_text(
        "beta changed\n", encoding="utf-8"
    )
    set_version(repo, "beta", "root", "1.1.0")
    set_version(repo, "beta", "claude", "1.1.0")
    # alpha is untouched.

    result = run_gate(base, repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "alpha: unchanged" in result.stdout
    assert "beta" in result.stdout


def test_uncommitted_new_untracked_file_counts_as_changed(tmp_path):
    """A file written but never `git add`ed must still count as a content
    change — git diff alone cannot see it (see _changed_paths' docstring),
    so this pins the untracked-file union that closes that gap."""
    repo, base = base_fixture(tmp_path)
    (repo / "plugins" / "alpha" / "skills" / "x" / "notes.md").write_text(
        "a brand new, never-added file\n", encoding="utf-8"
    )
    # No `git add` at all, and no version bump.

    result = run_gate(base, repo)

    assert result.returncode != 0, (
        "an untracked new file under a bundle must count as a content "
        f"change.\nstdout:\n{result.stdout}"
    )


def test_bad_base_ref_fails_clearly_instead_of_reporting_everything_new(tmp_path):
    """A typo'd --base must not silently read as 'nothing existed at base',
    which would make every bundle look brand new and the gate report success
    on exactly the PR it exists to catch."""
    repo, base = base_fixture(tmp_path)

    result = run_gate("not-a-real-ref", repo)

    assert result.returncode != 0
    assert "not-a-real-ref" in (result.stdout + result.stderr)


def test_no_bundles_found_fails(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("nothing here\n", encoding="utf-8")
    base = commit_all(repo, "base")

    result = run_gate(base, repo)

    assert result.returncode != 0
