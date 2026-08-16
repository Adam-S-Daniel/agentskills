"""Tests for sync_skills.py — run with pytest from the skill root."""

import base64
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

# Allow importing the sibling module regardless of working directory.
sys.path.insert(0, str(Path(__file__).parent.parent))
import sync_skills  # noqa: E402
from sync_skills import (  # noqa: E402
    _extract_skill_names,
    _skill_dir,
    get_all_skills,
    get_changed_skills,
    load_state,
    mark_synced,
    prepare,
    skill_hash,
    verify,
    zip_skill,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def skill_dir(tmp_path):
    """Minimal valid skill directory."""
    s = tmp_path / "my-skill"
    s.mkdir()
    (s / "SKILL.md").write_text("---\nname: my-skill\ndescription: test\n---\n# My Skill\n")
    (s / "helper.py").write_text("# helper\nprint('hi')\n")
    return s


@pytest.fixture()
def repo_with_skills(tmp_path):
    """A fake repo tree with two skills."""
    repo = tmp_path / "repo"
    for name in ("skill-a", "skill-b"):
        p = repo / "skills" / name
        p.mkdir(parents=True)
        (p / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
        (p / "extra.txt").write_text("extra")
    return repo


# ---------------------------------------------------------------------------
# zip_skill
# ---------------------------------------------------------------------------

class TestZipSkill:
    def test_returns_valid_zip(self, skill_dir):
        data = zip_skill(skill_dir)
        assert zipfile.is_zipfile(io.BytesIO(data))

    def test_skill_md_at_root(self, skill_dir):
        data = zip_skill(skill_dir)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert "SKILL.md" in zf.namelist()

    def test_all_files_present(self, skill_dir):
        data = zip_skill(skill_dir)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
        assert "SKILL.md" in names
        assert "helper.py" in names

    def test_no_absolute_or_parent_paths(self, skill_dir):
        data = zip_skill(skill_dir)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                assert not name.startswith("/")
                assert ".." not in name

    def test_base64_roundtrip(self, skill_dir):
        data = zip_skill(skill_dir)
        encoded = base64.b64encode(data).decode()
        decoded = base64.b64decode(encoded)
        assert zipfile.is_zipfile(io.BytesIO(decoded))

    def test_content_preserved(self, skill_dir):
        original = (skill_dir / "SKILL.md").read_bytes()
        data = zip_skill(skill_dir)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            extracted = zf.read("SKILL.md")
        assert extracted == original

    def test_subdirectory_preserved(self, tmp_path):
        s = tmp_path / "nested-skill"
        s.mkdir()
        (s / "SKILL.md").write_text("test")
        sub = s / "assets"
        sub.mkdir()
        (sub / "icon.png").write_bytes(b"\x89PNG\r\n")
        data = zip_skill(s)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert "assets/icon.png" in zf.namelist()

    def test_excludes_pytest_cache_files_dir(self, tmp_path):
        """pytest creates pytest-cache-files-<random> dirs; these must be skipped."""
        s = tmp_path / "my-skill"
        s.mkdir()
        (s / "SKILL.md").write_text("test")
        junk = s / "pytest-cache-files-abc123xyz" / "v" / "cache"
        junk.mkdir(parents=True)
        (junk / "nodeids").write_text("cached")
        data = zip_skill(s)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
        assert not any("pytest-cache-files-" in n for n in names), (
            f"pytest-cache-files-* should be excluded; got {names}"
        )

    def test_excludes_dot_pytest_cache(self, tmp_path):
        """.pytest_cache is already excluded — regression test."""
        s = tmp_path / "my-skill"
        s.mkdir()
        (s / "SKILL.md").write_text("test")
        cache = s / ".pytest_cache" / "v"
        cache.mkdir(parents=True)
        (cache / "lastfailed").write_text("{}")
        data = zip_skill(s)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
        assert not any(n.startswith(".pytest_cache") for n in names)


# ---------------------------------------------------------------------------
# skill_hash
# ---------------------------------------------------------------------------

class TestSkillHash:
    def test_deterministic(self, skill_dir):
        assert skill_hash(skill_dir) == skill_hash(skill_dir)

    def test_hex_format_and_length(self, skill_dir):
        h = skill_hash(skill_dir)
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_changes_with_content(self, skill_dir):
        h1 = skill_hash(skill_dir)
        (skill_dir / "SKILL.md").write_text("completely different")
        h2 = skill_hash(skill_dir)
        assert h1 != h2

    def test_changes_with_new_file(self, skill_dir):
        h1 = skill_hash(skill_dir)
        (skill_dir / "new_file.txt").write_text("extra")
        h2 = skill_hash(skill_dir)
        assert h1 != h2


# ---------------------------------------------------------------------------
# _extract_skill_names
# ---------------------------------------------------------------------------

class TestExtractSkillNames:
    def test_single_skill(self, repo_with_skills):
        diff = "skills/skill-a/SKILL.md\nskills/skill-a/extra.txt"
        names = _extract_skill_names(diff, repo_with_skills)
        assert names == ["skill-a"]

    def test_deduplication(self, repo_with_skills):
        diff = "skills/skill-a/SKILL.md\nskills/skill-a/extra.txt\nskills/skill-a/other.py"
        names = _extract_skill_names(diff, repo_with_skills)
        assert names.count("skill-a") == 1

    def test_multiple_skills(self, repo_with_skills):
        diff = "skills/skill-a/SKILL.md\nskills/skill-b/SKILL.md"
        names = _extract_skill_names(diff, repo_with_skills)
        assert set(names) == {"skill-a", "skill-b"}

    def test_ignores_non_skills_paths(self, repo_with_skills):
        diff = "README.md\n.github/workflows/ci.yml\nsrc/main.py"
        assert _extract_skill_names(diff, repo_with_skills) == []

    def test_excludes_dir_without_skill_md(self, tmp_path):
        repo = tmp_path / "repo"
        p = repo / "skills" / "incomplete"
        p.mkdir(parents=True)
        (p / "helper.py").write_text("x")  # no SKILL.md
        diff = "skills/incomplete/helper.py"
        assert _extract_skill_names(diff, repo) == []

    def test_empty_diff(self, repo_with_skills):
        assert _extract_skill_names("", repo_with_skills) == []


# ---------------------------------------------------------------------------
# get_all_skills
# ---------------------------------------------------------------------------

class TestGetAllSkills:
    def test_finds_all_skills(self, repo_with_skills):
        names = get_all_skills(repo_with_skills)
        assert set(names) == {"skill-a", "skill-b"}

    def test_sorted(self, repo_with_skills):
        names = get_all_skills(repo_with_skills)
        assert names == sorted(names)

    def test_excludes_dir_without_skill_md(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "skills" / "no-md").mkdir(parents=True)
        real_dir = repo / "skills" / "real"
        real_dir.mkdir(parents=True)
        (real_dir / "SKILL.md").write_text("ok")
        names = get_all_skills(repo)
        assert "no-md" not in names
        assert "real" in names

    def test_empty_skills_dir(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "skills").mkdir(parents=True)
        assert get_all_skills(repo) == []

    def test_missing_skills_dir(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        assert get_all_skills(repo) == []


# ---------------------------------------------------------------------------
# prepare (integration-style, no real git needed)
# ---------------------------------------------------------------------------

class TestPrepare:
    def test_explicit_skill_name(self, repo_with_skills, monkeypatch, tmp_path):
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: None)

        result = prepare([repo_with_skills], skill_names=["skill-a"])
        assert len(result["skills"]) == 1
        assert result["skills"][0]["name"] == "skill-a"

    def test_zip_b64_is_valid(self, repo_with_skills, monkeypatch, tmp_path):
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: None)

        result = prepare([repo_with_skills], skill_names=["skill-a"])
        zb64 = result["skills"][0]["zip_b64"]
        raw = base64.b64decode(zb64)
        assert zipfile.is_zipfile(io.BytesIO(raw))

    def test_is_update_false_for_new_skill(self, repo_with_skills, monkeypatch, tmp_path):
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", tmp_path / "account")
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: None)

        result = prepare([repo_with_skills], skill_names=["skill-a"])
        assert result["skills"][0]["is_update"] is False

    def test_is_update_true_when_already_on_the_account(
        self, repo_with_skills, monkeypatch, tmp_path
    ):
        """D6: the account mirror is the authority, not the local state file.

        On a fresh machine ~/.sync-skills-state.json doesn't exist, so a
        skill that IS on the account reported is_update=False, uploaded with
        overwrite=false, and 409'd.
        """
        account_dir = tmp_path / "account"
        (account_dir / "skill-a").mkdir(parents=True)
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "no-state.json")
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account_dir)
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: None)

        result = prepare([repo_with_skills], skill_names=["skill-a"])
        assert result["skills"][0]["is_update"] is True

    def test_is_update_true_after_mark_synced(self, repo_with_skills, monkeypatch, tmp_path):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("sync_skills.STATE_FILE", state_file)
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: None)

        mark_synced("skill-a", "deadbeef12345678")
        result = prepare([repo_with_skills], skill_names=["skill-a"])
        assert result["skills"][0]["is_update"] is True

    def test_nonexistent_repo_is_reported_not_silently_skipped(
        self, tmp_path, monkeypatch, capsys
    ):
        """D2: a missing repo path must be named, not silently dropped.

        Skipping it quietly made a typo'd --repos indistinguishable from a
        clean tree: both yielded an empty skill list and exit 0.
        """
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: None)

        missing = tmp_path / "does-not-exist"
        result = prepare([missing], skill_names=["anything"])

        assert result["skills"] == []
        err = capsys.readouterr().err
        assert "does-not-exist" in err
        assert "WARNING" in err

    def test_org_id_hint_included(self, repo_with_skills, monkeypatch, tmp_path):
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: "test-org-id")

        result = prepare([repo_with_skills], skill_names=["skill-a"])
        assert result["org_id_hint"] == "test-org-id"


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def write_manifest(account_dir, names=(), age_seconds=0):
    """Write an account-mirror manifest.json, aged ``age_seconds`` into the past."""
    account_dir.mkdir(parents=True, exist_ok=True)
    now_ms = datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000
    (account_dir / "manifest.json").write_text(
        json.dumps(
            {
                "lastUpdated": now_ms - age_seconds * 1000,
                "skills": [
                    {"name": n, "updatedAt": "2026-08-16T00:00:00.000000Z"}
                    for n in names
                ],
            }
        ),
        encoding="utf-8",
    )


def mirror_skill(account_dir, skill_src, name, transform=None):
    """Copy a skill folder into the fake account mirror, byte for byte."""
    dst = account_dir / name
    for f in sorted(skill_src.rglob("*")):
        if not f.is_file():
            continue
        target = dst / f.relative_to(skill_src)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = f.read_bytes()
        target.write_bytes(transform(data) if transform else data)


class TestVerify:
    def test_reports_ok_when_content_matches(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        account_dir = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account_dir)
        mirror_skill(account_dir, repo_with_skills / "skills" / "skill-a", "skill-a")
        write_manifest(account_dir, ["skill-a"])

        ok = verify([repo_with_skills], skill_names=["skill-a"])

        assert ok is True
        out = capsys.readouterr().out
        assert "OK" in out
        assert "skill-a" in out

    def test_detects_content_drift_with_identical_file_set(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """The D1 defect: every path present, contents stale.

        A path-only comparison reported OK here, which is exactly the state
        a re-upload that never landed leaves behind. The gate must catch it.
        """
        account_dir = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account_dir)
        mirror_skill(account_dir, repo_with_skills / "skills" / "skill-a", "skill-a")
        # Same file set, different bytes.
        (account_dir / "skill-a" / "SKILL.md").write_text("---\nname: skill-a\n---\nSTALE\n")
        write_manifest(account_dir, ["skill-a"])

        ok = verify([repo_with_skills], skill_names=["skill-a"])

        assert ok is False
        out = capsys.readouterr().out
        assert "DRIFT" in out
        assert "SKILL.md" in out

    def test_crlf_only_difference_is_not_drift(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """Line endings are not content.

        Account copies come back CRLF for some upload batches and LF for
        others. Normalising both sides at COMPARE time is what makes the
        content check usable; normalising at UPLOAD time (rewriting the
        bytes of every skill) was considered and rejected — it would change
        what lands on the account for all skills to chase a legacy artefact.
        """
        account_dir = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account_dir)
        mirror_skill(
            account_dir,
            repo_with_skills / "skills" / "skill-a",
            "skill-a",
            transform=lambda b: b.replace(b"\n", b"\r\n"),
        )
        write_manifest(account_dir, ["skill-a"])

        ok = verify([repo_with_skills], skill_names=["skill-a"])

        assert ok is True
        assert "OK" in capsys.readouterr().out

    def test_reports_mismatch_and_fails_on_missing_payload(self, monkeypatch, tmp_path, capsys):
        """The exact live bug: SKILL.md present on the account copy, scripts/* absent."""
        repo = tmp_path / "repo"
        skill = repo / "skills" / "sync-skills"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: sync-skills\n---\n")
        scripts = skill / "scripts"
        scripts.mkdir()
        (scripts / "helper.py").write_text("print('hi')\n")

        account_dir = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account_dir)
        skill_account = account_dir / "sync-skills"
        skill_account.mkdir(parents=True)
        (skill_account / "SKILL.md").write_text("---\nname: sync-skills\n---\n")
        # scripts/helper.py deliberately absent on the account copy.
        write_manifest(account_dir, ["sync-skills"])

        ok = verify([repo], skill_names=["sync-skills"])

        assert ok is False
        out = capsys.readouterr().out
        assert "MISMATCH" in out
        assert "scripts/helper.py" in out

    def test_absent_account_copy_is_a_failure(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """D3(a): a selected skill that never landed must fail the gate.

        Reporting SKIP and exiting 0 passed the gate for precisely the
        upload that silently did not happen.
        """
        account_dir = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account_dir)
        write_manifest(account_dir, [])

        ok = verify([repo_with_skills], skill_names=["skill-a"])

        assert ok is False
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "skill-a" in out

    def test_empty_selection_is_an_error(self, repo_with_skills, monkeypatch, tmp_path, capsys):
        """D3(b): verifying nothing must not look like verifying successfully."""
        account_dir = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account_dir)
        write_manifest(account_dir, [])

        ok = verify([repo_with_skills], skill_names=[])

        assert ok is False
        assert "no skills selected" in capsys.readouterr().err

    def test_unknown_skill_name_is_an_error(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """D3(c): a typo'd --skill resolved nowhere and still exited 0."""
        account_dir = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account_dir)
        write_manifest(account_dir, [])

        ok = verify([repo_with_skills], skill_names=["skill-typo"])

        assert ok is False
        err = capsys.readouterr().err
        assert "skill-typo" in err

    def test_stale_mirror_is_an_error(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """D7: a mirror older than the threshold predates the uploads."""
        account_dir = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account_dir)
        mirror_skill(account_dir, repo_with_skills / "skills" / "skill-a", "skill-a")
        write_manifest(
            account_dir, ["skill-a"], age_seconds=sync_skills.MIRROR_MAX_AGE_SECONDS + 60
        )

        ok = verify([repo_with_skills], skill_names=["skill-a"])

        assert ok is False
        assert "stale" in capsys.readouterr().err

    def test_missing_manifest_is_an_error(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """D7: no manifest means the mirror's freshness cannot be established."""
        account_dir = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account_dir)
        mirror_skill(account_dir, repo_with_skills / "skills" / "skill-a", "skill-a")
        # No manifest.json written.

        ok = verify([repo_with_skills], skill_names=["skill-a"])

        assert ok is False
        assert "manifest" in capsys.readouterr().err

    def test_prints_account_updated_at_beside_verdict(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """D7: a verdict must be traceable to the upload it describes."""
        account_dir = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account_dir)
        mirror_skill(account_dir, repo_with_skills / "skills" / "skill-a", "skill-a")
        write_manifest(account_dir, ["skill-a"])

        verify([repo_with_skills], skill_names=["skill-a"])

        assert "2026-08-16T00:00:00.000000Z" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# resolve_repos — machine-portable repo discovery (D2)
# ---------------------------------------------------------------------------

class TestResolveRepos:
    def test_explicit_repos_win(self, tmp_path):
        real = tmp_path / "somewhere" / "agentskills"
        real.mkdir(parents=True)
        assert sync_skills.resolve_repos([str(real)]) == [real]

    def test_explicit_missing_repo_warns_and_is_dropped(self, tmp_path, capsys):
        missing = tmp_path / "nope"
        assert sync_skills.resolve_repos([str(missing)]) == []
        assert "nope" in capsys.readouterr().err

    def test_env_var_used_when_no_explicit_repos(self, tmp_path, monkeypatch):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        monkeypatch.setenv("AGENTSKILLS_REPOS", os.pathsep.join([str(a), str(b)]))
        assert sync_skills.resolve_repos(None) == [a, b]

    def test_home_repos_layout_resolves(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AGENTSKILLS_REPOS", raising=False)
        home = tmp_path / "home"
        (home / "repos" / "agentskills").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr("sync_skills.WINDOWS_REPO_ROOT", tmp_path / "no-d-drive")

        assert home / "repos" / "agentskills" in sync_skills.resolve_repos(None)

    def test_windows_layout_resolves_when_home_repos_absent(self, tmp_path, monkeypatch):
        """ZENDA keeps clones at D:\\repos\\<owner>\\<repo>, not ~/repos."""
        monkeypatch.delenv("AGENTSKILLS_REPOS", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        win_root = tmp_path / "d-repos" / "adam-s-daniel"
        (win_root / "agentskills").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr("sync_skills.WINDOWS_REPO_ROOT", win_root)

        assert win_root / "agentskills" in sync_skills.resolve_repos(None)

    def test_self_repo_is_last_resort(self, tmp_path, monkeypatch):
        """With no clone anywhere else, fall back to the checkout we live in."""
        monkeypatch.delenv("AGENTSKILLS_REPOS", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        self_repo = tmp_path / "checkout"
        (self_repo / "plugins").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr("sync_skills.WINDOWS_REPO_ROOT", tmp_path / "no-d-drive")
        monkeypatch.setattr("sync_skills._self_repo", lambda: self_repo)

        assert sync_skills.resolve_repos(None) == [self_repo]

    def test_returns_empty_when_nothing_resolves(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AGENTSKILLS_REPOS", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr("sync_skills.WINDOWS_REPO_ROOT", tmp_path / "no-d-drive")
        monkeypatch.setattr("sync_skills._self_repo", lambda: None)

        assert sync_skills.resolve_repos(None) == []


# ---------------------------------------------------------------------------
# mark_synced / load_state
# ---------------------------------------------------------------------------

class TestMarkSynced:
    def test_creates_state_entry(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "state.json")
        mark_synced("fastmail", "abc123def456abcd")
        state = load_state()
        assert "fastmail" in state
        assert state["fastmail"]["last_synced_hash"] == "abc123def456abcd"

    def test_synced_at_is_iso8601(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "state.json")
        mark_synced("fastmail", "abc123")
        state = load_state()
        ts = state["fastmail"]["synced_at"]
        assert "T" in ts
        assert "+" in ts or ts.endswith("Z")

    def test_overwrite_existing_entry(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "state.json")
        mark_synced("fastmail", "first")
        mark_synced("fastmail", "second")
        state = load_state()
        assert state["fastmail"]["last_synced_hash"] == "second"

    def test_load_state_returns_empty_dict_when_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "no-such-file.json")
        assert load_state() == {}


# ---------------------------------------------------------------------------
# Plugin marketplace layout: plugins/<plugin>/skills/<skill>/SKILL.md
# ---------------------------------------------------------------------------

@pytest.fixture()
def repo_plugin_layout(tmp_path):
    """A fake repo using the plugins/<plugin>/skills/<skill> layout."""
    repo = tmp_path / "repo"
    for name in ("skill-a", "skill-b"):
        p = repo / "plugins" / name / "skills" / name
        p.mkdir(parents=True)
        (p / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
        (p / "extra.txt").write_text("extra")
    return repo


class TestPluginLayout:
    def test_skill_dir_legacy(self, repo_with_skills):
        d = _skill_dir(repo_with_skills, "skill-a")
        assert d is not None and d.name == "skill-a"
        assert (d / "SKILL.md").exists()

    def test_skill_dir_plugin(self, repo_plugin_layout):
        d = _skill_dir(repo_plugin_layout, "skill-a")
        assert d == repo_plugin_layout / "plugins" / "skill-a" / "skills" / "skill-a"

    def test_skill_dir_missing(self, repo_plugin_layout):
        assert _skill_dir(repo_plugin_layout, "nope") is None

    def test_get_all_skills_plugin_layout(self, repo_plugin_layout):
        assert set(get_all_skills(repo_plugin_layout)) == {"skill-a", "skill-b"}

    def test_extract_skill_names_plugin_layout(self, repo_plugin_layout):
        diff = (
            "plugins/skill-a/skills/skill-a/SKILL.md\n"
            "plugins/skill-b/skills/skill-b/SKILL.md"
        )
        names = _extract_skill_names(diff, repo_plugin_layout)
        assert set(names) == {"skill-a", "skill-b"}

    def test_extract_ignores_plugin_non_skill_paths(self, repo_plugin_layout):
        diff = (
            "plugins/skill-a/.claude-plugin/plugin.json\n"
            ".claude-plugin/marketplace.json"
        )
        assert _extract_skill_names(diff, repo_plugin_layout) == []

    def test_prepare_plugin_layout(self, repo_plugin_layout, monkeypatch, tmp_path):
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: None)
        result = prepare([repo_plugin_layout], skill_names=["skill-a"])
        assert len(result["skills"]) == 1
        assert result["skills"][0]["name"] == "skill-a"


# ---------------------------------------------------------------------------
# CLI exit codes — main() end to end
#
# Every vacuous-pass defect showed up as "exit 0", so the exit code is the
# thing under test here. None of these paths were exercised before: the suite
# called verify()/prepare() directly and never ran main() at all.
# ---------------------------------------------------------------------------

SCRIPT = Path(__file__).parent.parent / "sync_skills.py"


def run_cli(*args, home):
    """Run sync_skills.py in a sandboxed HOME (which relocates the mirror)."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env.pop("AGENTSKILLS_REPOS", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env,
    )


@pytest.fixture()
def sandbox(tmp_path):
    """A fake HOME with an account mirror, plus a repo holding one skill."""
    home = tmp_path / "home"
    account = home / ".claude" / "skills" / "synced"
    account.mkdir(parents=True)

    repo = tmp_path / "repo"
    skill = repo / "skills" / "skill-a"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: skill-a\n---\nbody\n")

    write_manifest(account, ["skill-a"])
    return {"home": home, "account": account, "repo": repo, "skill": skill}


class TestCliExitCodes:
    def test_verify_passes_when_account_matches(self, sandbox):
        mirror_skill(sandbox["account"], sandbox["skill"], "skill-a")
        proc = run_cli("--verify", "--all", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "OK" in proc.stdout

    def test_verify_fails_on_content_drift(self, sandbox):
        mirror_skill(sandbox["account"], sandbox["skill"], "skill-a")
        (sandbox["account"] / "skill-a" / "SKILL.md").write_text("stale\n")
        proc = run_cli("--verify", "--all", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"])
        assert proc.returncode != 0
        assert "DRIFT" in proc.stdout

    def test_verify_fails_when_skill_never_landed(self, sandbox):
        # Account mirror exists but holds no copy of skill-a.
        proc = run_cli("--verify", "--all", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"])
        assert proc.returncode != 0
        assert "FAIL" in proc.stdout

    def test_verify_fails_on_empty_selection(self, sandbox):
        """D3(b): bare --verify with nothing selected exited 0 silently."""
        proc = run_cli("--verify", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"])
        assert proc.returncode != 0
        assert "no skills selected" in proc.stderr

    def test_verify_fails_on_unknown_skill(self, sandbox):
        """D3(c): a typo'd --skill matched nothing and exited 0."""
        proc = run_cli("--verify", "--repos", str(sandbox["repo"]),
                       "--skill", "skill-typo", home=sandbox["home"])
        assert proc.returncode != 0
        assert "skill-typo" in proc.stderr

    def test_verify_fails_on_unresolvable_repo(self, sandbox, tmp_path):
        """D2: a wrong --repos reported 'nothing to do' and exited 0."""
        proc = run_cli("--verify", "--all", "--repos", str(tmp_path / "nope"),
                       home=sandbox["home"])
        assert proc.returncode != 0
        assert "nope" in proc.stderr

    def test_verify_fails_on_stale_mirror(self, sandbox):
        """D7: verifying against a pre-upload snapshot must not pass."""
        mirror_skill(sandbox["account"], sandbox["skill"], "skill-a")
        write_manifest(
            sandbox["account"], ["skill-a"],
            age_seconds=sync_skills.MIRROR_MAX_AGE_SECONDS + 60,
        )
        proc = run_cli("--verify", "--all", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"])
        assert proc.returncode != 0
        assert "stale" in proc.stderr

    def test_all_and_skill_are_mutually_exclusive(self, sandbox):
        """D9: --skill silently overrode --all."""
        proc = run_cli("--verify", "--all", "--skill", "skill-a",
                       "--repos", str(sandbox["repo"]), home=sandbox["home"])
        assert proc.returncode != 0
        assert "not allowed with" in proc.stderr

    def test_prepare_marks_is_update_from_account_mirror(self, sandbox):
        """D6: no state file, but the skill IS on the account -> overwrite."""
        mirror_skill(sandbox["account"], sandbox["skill"], "skill-a")
        proc = run_cli("--prepare", "--all", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"])
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["skills"][0]["is_update"] is True


# ---------------------------------------------------------------------------
# SKILL.md section 6 — pure-text guard-regression lint
# ---------------------------------------------------------------------------

class TestSkillMdSection6Guard:
    """Section 6 documents a hand-built single-file ZIP fallback that has
    already caused real payload loss (SKILL.md uploaded, everything else
    silently dropped) for three skills. These assertions keep the fix —
    the expectedFileCount hard guard, and the removal of the refuted
    ZIP-root claim — from being silently edited away later.
    """

    def test_guard_present_and_false_claim_removed(self):
        skill_md = Path(__file__).parent.parent / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")

        assert "expectedFileCount" in text, (
            "SKILL.md section 6 must keep the expectedFileCount hard guard"
        )
        assert "Don't put the file at the ZIP root" not in text, (
            "SKILL.md must not reintroduce the refuted ZIP-root-prefix claim"
        )

    def test_payload_placeholder_is_base64_not_a_template_literal(self):
        """The payload must not be pasted into a JS template literal.

        SKILL.md bodies contain backticks and ${...}; interpolating one into
        `...` makes the script a syntax error, which kills it at PARSE time —
        before the expectedFileCount guard can run.
        """
        # Scoped to the executable block: the prose deliberately quotes the
        # old template-literal form to explain why it was replaced.
        js = section6_js()
        assert "SKILL_MD_B64" in js
        assert "SKILL_MD_CONTENT" not in js


def section6_js():
    """The JavaScript block from SKILL.md section 6."""
    import re

    text = (Path(__file__).parent.parent / "SKILL.md").read_text(encoding="utf-8")
    section = text.split("## 6.")[1].split("## 7.")[0]
    blocks = re.findall(r"```javascript\n(.*?)```", section, re.S)
    assert len(blocks) == 1, f"expected 1 JS block in section 6, found {len(blocks)}"
    return blocks[0]


class TestSection6JavaScriptParses:
    """Run the substituted section-6 snippet through `node --check`.

    This is the lint that the previous template-literal form would have
    failed for every legitimate target of section 6.
    """

    def _substitute(self, skill_md_bytes):
        """Fill in the snippet the way an agent following SKILL.md would.

        Both payload placeholders are handled deliberately: the lint has to
        reproduce what actually gets pasted. Substituting only the base64
        placeholder would leave a raw-text form's `SKILL_MD_CONTENT` intact
        as a harmless literal, and the lint would pass on the very defect it
        exists to catch (verified: it did).
        """
        js = (
            section6_js()
            .replace('"ORG_ID"', '"11111111-2222-3333-4444-555555555555"')
            .replace("OVERWRITE", "true")
            .replace('"SKILL_NAME"', '"some-skill"')
            .replace("= N;", "= 1;")
        )
        if "SKILL_MD_B64" in js:
            js = js.replace("SKILL_MD_B64", base64.b64encode(skill_md_bytes).decode())
        # A raw-text placeholder gets the raw Markdown, because that is what
        # the instruction "full SKILL.md text" tells the agent to paste.
        return js.replace(
            "SKILL_MD_CONTENT", skill_md_bytes.decode("utf-8")
        )

    def _node_check(self, source, tmp_path):
        node = shutil.which("node")
        if node is None:
            pytest.skip(
                "node not on PATH — cannot syntax-check SKILL.md section 6's "
                "JavaScript. Install Node to run this lint; it is skipped, "
                "NOT passed."
            )
        script = tmp_path / "section6.js"
        script.write_text(source, encoding="utf-8")
        return subprocess.run(
            [node, "--check", str(script)], capture_output=True, text=True
        )

    def test_parses_with_a_hostile_skill_md(self, tmp_path):
        """Backticks, ${...} and quotes are all normal in a SKILL.md body."""
        hostile = (
            "---\nname: some-skill\n---\n"
            "# Heading\n\n"
            "Inline `code` and a fence:\n\n"
            "```bash\n"
            'echo "${HOME}/repos" && echo `date`\n'
            "```\n\n"
            "A backslash \\ and a lone ` backtick.\n"
        ).encode("utf-8")

        proc = self._node_check(self._substitute(hostile), tmp_path)
        assert proc.returncode == 0, (
            f"section 6's JavaScript does not parse:\n{proc.stderr}"
        )

    def test_parses_for_every_single_file_skill_in_this_repo(self, tmp_path):
        """The real targets: skills whose upload really is just SKILL.md."""
        # tests/ -> sync-skills -> skills -> adam-local -> plugins -> repo root
        repo = Path(__file__).resolve().parents[5]
        singles = []
        for name in get_all_skills(repo):
            path = _skill_dir(repo, name)
            with zipfile.ZipFile(io.BytesIO(zip_skill(path))) as zf:
                if zf.namelist() == ["SKILL.md"]:
                    singles.append(path / "SKILL.md")

        if not singles:
            pytest.skip("no single-file skills in this checkout to exercise")

        failures = []
        for skill_md in singles:
            proc = self._node_check(
                self._substitute(skill_md.read_bytes()), tmp_path
            )
            if proc.returncode != 0:
                failures.append(f"{skill_md.parent.name}: {proc.stderr.strip()}")

        assert not failures, "section 6 JS fails to parse for:\n" + "\n".join(failures)
