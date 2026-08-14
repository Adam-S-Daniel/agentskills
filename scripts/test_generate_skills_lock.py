#!/usr/bin/env python3
"""Tests for generate_skills_lock.py and .claude/hooks/skills-bootstrap.sh.

Hermetic and deterministic: no network (the hook is driven against a `file://`
git repo built in tmp_path), no sleeps, no wall-clock dependence.

Every invocation of the hook gets `HOME` pointed at a tmp directory. That is
not a nicety — the hook's whole job is to write into `$HOME/.claude/skills`,
and a test suite that pollutes the developer's real home directory is worse
than no test suite. `_run_hook` is the only way these tests launch it, and it
refuses to run without an explicit tmp home.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
GENERATOR = SCRIPTS_DIR / "generate_skills_lock.py"
HOOK = REPO_ROOT / ".claude" / "hooks" / "skills-bootstrap.sh"

sys.path.insert(0, str(SCRIPTS_DIR))
import generate_skills_lock as gsl  # noqa: E402


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def make_registry(root: Path, skills: dict) -> str:
    """Build a committed git repo laid out like the registry.

    `skills` maps '<bundle>/<name>' -> {relative path: contents}. Returns the
    commit SHA.
    """
    root.mkdir(parents=True, exist_ok=True)
    for key, files in skills.items():
        bundle, name = key.split("/", 1)
        for relpath, contents in files.items():
            _write(root / "plugins" / bundle / "skills" / name / relpath, contents)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def run_generator(*args: str, cwd: Path = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GENERATOR), *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )


SKILL_A = {"SKILL.md": "---\nname: alpha\n---\nalpha body\n", "notes.md": "a\n"}
SKILL_B = {"SKILL.md": "---\nname: beta\n---\nbeta body\n"}


@pytest.fixture
def registry(tmp_path):
    """A two-skill fixture registry plus its SHA."""
    root = tmp_path / "registry"
    sha = make_registry(root, {"adam/alpha": SKILL_A, "adam/beta": SKILL_B})
    return root, sha


# --------------------------------------------------------------------------
# digest
# --------------------------------------------------------------------------

def test_digest_is_stable_across_runs(tmp_path):
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    _write(skill / "scripts" / "run.sh", "echo hi\n")
    assert gsl.digest_skill_dir(skill) == gsl.digest_skill_dir(skill)


def test_digest_changes_when_a_payload_file_changes(tmp_path):
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    before = gsl.digest_skill_dir(skill)
    _write(skill / "SKILL.md", "body changed\n")
    assert gsl.digest_skill_dir(skill) != before


def test_digest_changes_on_a_one_byte_edit_in_a_nested_file(tmp_path):
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    _write(skill / "reference" / "deep.md", "x\n")
    before = gsl.digest_skill_dir(skill)
    _write(skill / "reference" / "deep.md", "y\n")
    assert gsl.digest_skill_dir(skill) != before


def test_digest_changes_when_a_file_is_added(tmp_path):
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    before = gsl.digest_skill_dir(skill)
    _write(skill / "extra.md", "extra\n")
    assert gsl.digest_skill_dir(skill) != before


def test_digest_changes_when_a_file_is_removed(tmp_path):
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    _write(skill / "extra.md", "extra\n")
    before = gsl.digest_skill_dir(skill)
    (skill / "extra.md").unlink()
    assert gsl.digest_skill_dir(skill) != before


def test_digest_changes_when_a_file_is_renamed(tmp_path):
    """Paths are in the manifest, so a pure rename is not invisible."""
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    _write(skill / "one.md", "same bytes\n")
    before = gsl.digest_skill_dir(skill)
    (skill / "one.md").rename(skill / "two.md")
    assert gsl.digest_skill_dir(skill) != before


def test_digest_is_independent_of_filesystem_iteration_order(tmp_path):
    """Same contents, files created in opposite orders -> same digest."""
    names = ["SKILL.md", "a.md", "m.md", "z.md", "nested/deep.md"]
    contents = {name: f"contents of {name}\n" for name in names}

    forward = tmp_path / "forward"
    for name in names:
        _write(forward / name, contents[name])

    backward = tmp_path / "backward"
    for name in reversed(names):
        _write(backward / name, contents[name])

    assert gsl.digest_skill_dir(forward) == gsl.digest_skill_dir(backward)


def test_digest_cli_matches_the_library(tmp_path):
    """The hook calls --digest; it must agree with the lock generator."""
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    proc = run_generator("--digest", str(skill))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == gsl.digest_skill_dir(skill)


# --------------------------------------------------------------------------
# lock generation
# --------------------------------------------------------------------------

def test_generates_a_lock_for_every_skill_in_the_bundle(registry, tmp_path):
    root, sha = registry
    out = tmp_path / "skills.lock"
    proc = run_generator("--repo", str(root), "--registry", "owner/repo",
                         "--bundles", "adam", "-o", str(out))
    assert proc.returncode == 0, proc.stderr
    lock = json.loads(out.read_text(encoding="utf-8"))
    assert lock["registry"] == "owner/repo"
    assert lock["ref"] == sha
    assert lock["generated_from"] == sha
    assert lock["bundles"] == ["adam"]
    assert sorted(lock["skills"]) == ["adam/alpha", "adam/beta"]
    assert lock["skills"]["adam/alpha"] == gsl.digest_skill_dir(
        root / "plugins" / "adam" / "skills" / "alpha"
    )


def test_an_empty_bundle_yields_an_empty_skills_map(tmp_path):
    root = tmp_path / "registry"
    make_registry(root, {"adam/alpha": SKILL_A})
    out = tmp_path / "skills.lock"
    proc = run_generator("--repo", str(root), "--bundles", "fastmail", "-o", str(out))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["skills"] == {}


def test_content_comes_from_the_pinned_ref_not_the_working_tree(registry, tmp_path):
    """The lock describes the commit it pins; an uncommitted edit must not leak in.

    This is the property that lets the hook's integrity check mean something: a
    digest built from a mutable working tree would describe bytes that were
    never published at `ref`, and every hook run would report a mismatch that
    is really just generator staleness.
    """
    root, sha = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    pinned = json.loads(out.read_text(encoding="utf-8"))["skills"]["adam/alpha"]

    _write(root / "plugins" / "adam" / "skills" / "alpha" / "SKILL.md", "TAMPERED\n")
    assert run_generator("--repo", str(root), "--ref", sha, "-o", str(out)).returncode == 0
    assert json.loads(out.read_text(encoding="utf-8"))["skills"]["adam/alpha"] == pinned


def test_unresolvable_ref_reports_an_error_rather_than_a_traceback(registry, tmp_path):
    root, _ = registry
    proc = run_generator("--repo", str(root), "--ref", "no-such-ref",
                         "-o", str(tmp_path / "skills.lock"))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "no-such-ref" in proc.stderr


# --------------------------------------------------------------------------
# --check
# --------------------------------------------------------------------------

def test_check_exits_zero_on_a_current_lock(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    proc = run_generator("--repo", str(root), "--check", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_check_exits_one_on_a_hand_edited_digest(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    lock = json.loads(out.read_text(encoding="utf-8"))
    lock["skills"]["adam/alpha"] = "0" * 64
    out.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    proc = run_generator("--repo", str(root), "--check", "-o", str(out))
    assert proc.returncode == 1
    assert "adam/alpha" in proc.stdout


def test_check_exits_one_when_a_skill_was_added_at_the_pinned_ref(registry, tmp_path):
    """Stale lock: the ref moved on and the lock still lists the old skill set."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0

    _write(root / "plugins" / "adam" / "skills" / "gamma" / "SKILL.md", "gamma\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "add gamma")
    new_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             check=True, capture_output=True, text=True).stdout.strip()

    proc = run_generator("--repo", str(root), "--check", "--ref", new_sha, "-o", str(out))
    assert proc.returncode == 1
    assert "adam/gamma" in proc.stdout


def test_check_inherits_the_locks_ref_so_a_moved_head_is_not_a_failure(registry, tmp_path):
    """--check asks 'is this lock faithful to the ref it pins', not 'is it HEAD'.

    Committing anything at all moves HEAD, so a --check that silently re-pinned
    to HEAD could never be green on a committed lock -- and in CI, HEAD is a
    merge commit that is never the pinned SHA.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    pinned = json.loads(out.read_text(encoding="utf-8"))["ref"]

    _write(root / "unrelated.txt", "moves HEAD\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "move HEAD")

    proc = run_generator("--repo", str(root), "--check", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["ref"] == pinned


# --------------------------------------------------------------------------
# --check-current
#
# --check asks "is the lock faithful to the ref it pins"; --check-current asks
# "is that ref still the bundle". A lock pinned before a skill was added passes
# the first and fails the second, and the skill reaches no ephemeral surface --
# which is the silent no-op these tests exist to keep caught.
# --------------------------------------------------------------------------

def _lock_for(root: Path, out: Path) -> None:
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0


def test_check_current_exits_zero_when_the_tree_matches_the_pinned_ref(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_check_current_flags_a_skill_added_to_the_working_tree(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    _write(root / "plugins" / "adam" / "skills" / "gamma" / "SKILL.md", "gamma\n")

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "adam/gamma" in proc.stdout
    assert "added" in proc.stdout


def test_check_current_flags_a_skill_removed_from_the_working_tree(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    shutil.rmtree(root / "plugins" / "adam" / "skills" / "beta")

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "adam/beta" in proc.stdout
    assert "removed" in proc.stdout


def test_check_current_flags_a_skill_whose_content_changed(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    _write(root / "plugins" / "adam" / "skills" / "alpha" / "SKILL.md",
           "---\nname: alpha\n---\nalpha body, edited\n")

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "adam/alpha" in proc.stdout
    assert "changed" in proc.stdout
    # The untouched skill must not be dragged in with it.
    assert "adam/beta" not in proc.stdout


def test_check_current_failure_names_the_re_pin_command(registry, tmp_path):
    """A red check that does not say how to go green is a check people route around."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    _write(root / "plugins" / "adam" / "skills" / "gamma" / "SKILL.md", "gamma\n")

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode == 1
    assert "scripts/generate_skills_lock.py" in proc.stdout


def test_check_is_blind_to_what_check_current_catches(registry, tmp_path):
    """The whole reason --check-current is a SEPARATE flag, in one test.

    An added-but-unpinned skill leaves the lock a perfectly faithful
    description of the commit it pins -- so --check is green -- while that
    skill is delivered to nobody.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    _write(root / "plugins" / "adam" / "skills" / "gamma" / "SKILL.md", "gamma\n")

    faithful = run_generator("--repo", str(root), "--check", "-o", str(out))
    current = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert faithful.returncode == 0, faithful.stdout + faithful.stderr
    assert current.returncode == 1, current.stdout + current.stderr


def test_check_and_check_current_run_together_and_both_report(registry, tmp_path):
    """Passing both runs both; the exit code is the worse of the two."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    _write(root / "plugins" / "adam" / "skills" / "gamma" / "SKILL.md", "gamma\n")

    proc = run_generator("--repo", str(root), "--check", "--check-current", "-o", str(out))
    assert proc.returncode == 1
    assert "OK:" in proc.stdout        # --check's verdict
    assert "FAILED:" in proc.stdout    # --check-current's


def test_check_current_on_a_missing_lock_errors_cleanly(tmp_path):
    proc = run_generator("--check-current", "-o", str(tmp_path / "absent.lock"))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr


def test_check_current_on_an_unreachable_pinned_ref_errors_cleanly(registry, tmp_path):
    """The CI shape: a shallow checkout that does not contain the pinned commit."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)
    lock = json.loads(out.read_text(encoding="utf-8"))
    lock["ref"] = "0" * 40
    out.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "0" * 40 in proc.stderr


def test_check_on_a_missing_lock_errors_cleanly(tmp_path):
    proc = run_generator("--check", "-o", str(tmp_path / "absent.lock"))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr


def test_unwritable_output_errors_cleanly(registry, tmp_path):
    root, _ = registry
    proc = run_generator("--repo", str(root), "-o", str(tmp_path / "nope" / "skills.lock"))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr


# --------------------------------------------------------------------------
# the bootstrap hook (bash, driven through subprocess)
# --------------------------------------------------------------------------

def _run_hook(home: Path, project_dir: Path = None, extra_env: dict = None,
              script: Path = HOOK) -> subprocess.CompletedProcess:
    """Run the hook with HOME forced into a tmp dir.

    The tmp HOME is mandatory and constructed here rather than by the caller's
    environment, so no test can write into the developer's real ~/.claude.
    """
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(home / "tmp"),
        # Fixture repos are committed by `git commit` in make_registry; the
        # hook itself only reads, but keep git non-interactive regardless.
        "GIT_TERMINAL_PROMPT": "0",
    }
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(script)],
        input='{"hook_event_name":"SessionStart","source":"startup"}',
        env=env, capture_output=True, text=True,
    )


def _verdict(proc: subprocess.CompletedProcess) -> str:
    payload = json.loads(proc.stdout)
    return payload["hookSpecificOutput"]["additionalContext"]


def _looked_in(verdict: str) -> list:
    """The lock locations a 'no skills.lock found' verdict says it searched.

    Parsed out of the rendered sentence rather than substring-counted, so a
    duplicate is caught as a repeated list ENTRY -- counting occurrences would
    miscount whenever one candidate path is a prefix of the other.
    """
    prefix, suffix = "looked in ", " (generate it"
    start = verdict.index(prefix) + len(prefix)
    return verdict[start:verdict.index(suffix, start)].split(", ")


def _hook_copy(root: Path) -> Path:
    """Install a copy of the hook so that its SELF_ROOT resolves to `root`."""
    script = root / ".claude" / "hooks" / "skills-bootstrap.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(HOOK.read_bytes())
    return script


def make_project(project_dir: Path, registry_root: Path, sha: str) -> Path:
    """A project dir carrying a skills.lock that pins the fixture registry."""
    project_dir.mkdir(parents=True, exist_ok=True)
    lock = project_dir / "skills.lock"
    proc = run_generator("--repo", str(registry_root),
                         "--registry", registry_root.resolve().as_uri(),
                         "--ref", sha, "--bundles", "adam", "-o", str(lock))
    assert proc.returncode == 0, proc.stderr
    return lock


def test_hook_is_a_no_op_without_the_ephemeral_marker(tmp_path, registry):
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)

    proc = _run_hook(tmp_path / "home", project)
    assert proc.returncode == 0
    assert _verdict(proc).startswith("skills: skipped")
    assert not (tmp_path / "home" / ".claude" / "skills").exists()


def test_hook_installs_and_verifies_the_locked_skills(tmp_path, registry):
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)  # must be valid JSON
    assert payload["reloadSkills"] is True
    assert payload["hookSpecificOutput"]["reloadSkills"] is True

    verdict = payload["hookSpecificOutput"]["additionalContext"]
    assert verdict.startswith("skills: 2/2 ")
    assert verdict.endswith("OK")
    assert sha[:7] in verdict

    for name in ("alpha", "beta"):
        assert (home / ".claude" / "skills" / name / "SKILL.md").is_file()
    # Copied whole, not just SKILL.md.
    assert (home / ".claude" / "skills" / "alpha" / "notes.md").is_file()


def test_hook_skips_a_skill_the_project_already_owns(tmp_path, registry):
    """Personal ~/.claude/skills shadows project .claude/skills (E2 / C3)."""
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)
    _write(project / ".claude" / "skills" / "alpha" / "SKILL.md", "repo-owned alpha\n")
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0
    verdict = _verdict(proc)
    assert verdict.startswith("skills: 1/2 ")
    assert "DEGRADED" in verdict
    assert "alpha" in verdict

    assert not (home / ".claude" / "skills" / "alpha").exists()
    assert (home / ".claude" / "skills" / "beta" / "SKILL.md").is_file()


def test_hook_reports_a_digest_mismatch(tmp_path, registry):
    """A lock digest that does not match the fetched bytes must be named."""
    root, sha = registry
    project = tmp_path / "project"
    lock_path = make_project(project, root, sha)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["skills"]["adam/alpha"] = "0" * 64
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0
    verdict = _verdict(proc)
    assert verdict.startswith("skills: 1/2 ")
    assert "1 digest mismatch (alpha)" in verdict


def test_hook_fails_soft_when_the_lock_is_missing(tmp_path):
    """Exit 0 with a verdict naming the file, never a non-zero that blocks boot."""
    # Run a copy of the hook from a tmp tree so its own repo's skills.lock is
    # not found by the fallback lookup.
    script = tmp_path / "hooks" / "skills-bootstrap.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(HOOK.read_bytes())
    project = tmp_path / "project"
    project.mkdir()

    proc = _run_hook(tmp_path / "home", project,
                     {"SKILLS_BOOTSTRAP_FORCE": "1"}, script=script)
    assert proc.returncode == 0
    verdict = _verdict(proc)
    assert "DEGRADED" in verdict
    assert "skills.lock" in verdict
    assert str(project / "skills.lock") in verdict


def test_missing_lock_verdict_names_one_location_once(tmp_path):
    """The two candidates collapse when they name the same file.

    `$CLAUDE_PROJECT_DIR/skills.lock` and the hook-relative `../../skills.lock`
    are the same path whenever the session's project dir IS the repo shipping
    the hook -- the common case -- and the verdict used to list it twice, which
    reads like two separate lookups failed.
    """
    repo = tmp_path / "repo"
    script = _hook_copy(repo)

    proc = _run_hook(tmp_path / "home", repo,
                     {"SKILLS_BOOTSTRAP_FORCE": "1"}, script=script)
    assert proc.returncode == 0
    verdict = _verdict(proc)
    assert "DEGRADED" in verdict
    assert _looked_in(verdict) == [str((repo / "skills.lock").resolve())]


def test_missing_lock_verdict_names_each_distinct_location_once(tmp_path):
    """De-duplicating must not collapse candidates that really are different.

    Both are still named, in priority order -- project dir first.
    """
    repo = tmp_path / "repo"
    script = _hook_copy(repo)
    project = tmp_path / "project"
    project.mkdir()

    proc = _run_hook(tmp_path / "home", project,
                     {"SKILLS_BOOTSTRAP_FORCE": "1"}, script=script)
    assert proc.returncode == 0
    assert _looked_in(_verdict(proc)) == [
        str((project / "skills.lock").resolve()),
        str((repo / "skills.lock").resolve()),
    ]


def test_missing_lock_verdict_de_duplicates_a_symlinked_project_dir(tmp_path):
    """The comparison is between RESOLVED paths, not the strings as spelled.

    A project dir reached through a symlink is the same directory as the hook's
    own repo. Comparing the raw strings would not see that, so this is the test
    that keeps the de-duplication from being "simplified" back into one.
    """
    repo = tmp_path / "repo"
    script = _hook_copy(repo)
    link = tmp_path / "link"
    link.symlink_to(repo, target_is_directory=True)

    proc = _run_hook(tmp_path / "home", link,
                     {"SKILLS_BOOTSTRAP_FORCE": "1"}, script=script)
    assert proc.returncode == 0
    assert _looked_in(_verdict(proc)) == [str((repo / "skills.lock").resolve())]


def test_hook_verifies_using_the_registrys_own_generator(tmp_path):
    """The consumer shape: a repo carrying the hook and lock but no scripts/.

    Candidates 1 and 2 (beside the hook, in the project) both miss, so the
    digest implementation has to come from the fetched registry — pinned to the
    same immutable ref as the skills it is verifying.
    """
    root = tmp_path / "registry"
    make_registry(root, {"adam/alpha": SKILL_A})
    # The registry ships the generator, as this repo does.
    (root / "scripts").mkdir()
    (root / "scripts" / "generate_skills_lock.py").write_bytes(GENERATOR.read_bytes())
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "ship the generator")
    sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True).stdout.strip()

    project = tmp_path / "consumer"
    make_project(project, root, sha)
    assert not (project / "scripts").exists()

    script = tmp_path / "elsewhere" / "hooks" / "skills-bootstrap.sh"
    script.parent.mkdir(parents=True)
    script.write_bytes(HOOK.read_bytes())

    proc = _run_hook(tmp_path / "home", project,
                     {"SKILLS_BOOTSTRAP_FORCE": "1"}, script=script)
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert verdict.startswith("skills: 1/1 "), verdict
    assert verdict.endswith("OK"), verdict


def test_hook_bundle_override_narrows_what_is_installed(tmp_path):
    """AGENTSKILLS_BUNDLE filters the lock; it never widens beyond it."""
    root = tmp_path / "registry"
    sha = make_registry(root, {"adam/alpha": SKILL_A, "fastmail/beta": SKILL_B})
    project = tmp_path / "project"
    project.mkdir()
    proc = run_generator("--repo", str(root), "--registry", root.resolve().as_uri(),
                         "--ref", sha, "--bundles", "adam,fastmail",
                         "-o", str(project / "skills.lock"))
    assert proc.returncode == 0, proc.stderr
    home = tmp_path / "home"

    proc = _run_hook(home, project, {
        "SKILLS_BOOTSTRAP_FORCE": "1",
        "AGENTSKILLS_BUNDLE": "adam",
    })
    assert proc.returncode == 0
    assert _verdict(proc).startswith("skills: 1/1 ")
    assert (home / ".claude" / "skills" / "alpha").is_dir()
    assert not (home / ".claude" / "skills" / "beta").exists()


def test_hook_fails_soft_when_the_ref_cannot_be_fetched(tmp_path, registry):
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)

    proc = _run_hook(tmp_path / "home", project, {
        "SKILLS_BOOTSTRAP_FORCE": "1",
        "AGENTSKILLS_REF": "0" * 40,
    })
    assert proc.returncode == 0
    verdict = _verdict(proc)
    assert "DEGRADED" in verdict
    assert "could not fetch" in verdict


def test_hook_rejects_a_registry_that_is_not_a_repo_or_allowed_url(tmp_path, registry):
    """The registry becomes a git remote URL — it is a trust boundary."""
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)

    proc = _run_hook(tmp_path / "home", project, {
        "SKILLS_BOOTSTRAP_FORCE": "1",
        "AGENTSKILLS_REPO": "ext::sh -c whoami",
    })
    assert proc.returncode == 0
    assert "DEGRADED" in _verdict(proc)
    assert not (tmp_path / "home" / ".claude" / "skills").exists()


def test_this_repos_own_lock_is_installable_end_to_end(tmp_path):
    """Dogfood: the committed skills.lock, against a local clone of this repo.

    Uses a file:// clone of the real repo rather than the network, so the test
    stays hermetic while still exercising the SHA-pinned fetch path (a commit
    SHA cannot be reached by `git clone --branch`, so this is the code path
    that would silently be untested otherwise).
    """
    lock = json.loads((REPO_ROOT / "skills.lock").read_text(encoding="utf-8"))
    have = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "-e", lock["ref"] + "^{commit}"],
        capture_output=True,
    )
    if have.returncode != 0:
        pytest.skip(f"pinned ref {lock['ref']} is not present in this checkout")

    project = tmp_path / "project"
    project.mkdir()
    lock["registry"] = REPO_ROOT.as_uri()
    (project / "skills.lock").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    expected = len(lock["skills"])
    assert verdict.startswith(f"skills: {expected}/{expected} "), verdict
    assert verdict.endswith("OK"), verdict
