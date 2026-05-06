"""Tests for sync_skills.py — run with pytest from the skill root."""

import base64
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

# Allow importing the sibling module regardless of working directory.
sys.path.insert(0, str(Path(__file__).parent.parent))
from sync_skills import (  # noqa: E402
    _extract_skill_names,
    get_all_skills,
    get_changed_skills,
    load_state,
    mark_synced,
    prepare,
    skill_hash,
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
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: None)

        result = prepare([repo_with_skills], skill_names=["skill-a"])
        assert result["skills"][0]["is_update"] is False

    def test_is_update_true_after_mark_synced(self, repo_with_skills, monkeypatch, tmp_path):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("sync_skills.STATE_FILE", state_file)
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: None)

        mark_synced("skill-a", "deadbeef12345678")
        result = prepare([repo_with_skills], skill_names=["skill-a"])
        assert result["skills"][0]["is_update"] is True

    def test_skips_nonexistent_repo(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: None)

        missing = tmp_path / "does-not-exist"
        result = prepare([missing], skill_names=["anything"])
        assert result["skills"] == []

    def test_org_id_hint_included(self, repo_with_skills, monkeypatch, tmp_path):
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: "test-org-id")

        result = prepare([repo_with_skills], skill_names=["skill-a"])
        assert result["org_id_hint"] == "test-org-id"


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
