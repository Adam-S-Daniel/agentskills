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


# Decode subprocess output as UTF-8 explicitly, and never die on a stray byte.
#
# `text=True` on its own decodes using the LOCALE encoding, which on Windows
# is cp1252. That is not hypothetical here. sync-skills' own setup.sh
# registers hooks/pre-push as a GLOBAL git hook, so every `git push` these
# fixtures make — including into a throwaway bare repo under tmp — prints
# that hook's UTF-8 box-drawing banner, and cp1252 cannot decode it. The
# whole repo-state-gate class errored out before its first assertion, on
# exactly the class of machine this skill exists to run on and the one whose
# failure prompted the resolution work in #92.
#
# errors="replace" as well as an explicit encoding: a test helper must fail
# on the assertion it was written for, never on decoding the evidence.
TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}


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
        """D2: a typo'd --repos must never look like a clean tree.

        prepare() no longer re-filters its input: resolution happens once, in
        resolve_repos(), and is handed down (issue #93 item 4). The naming
        guarantee is asserted where it now lives -
        TestResolveRepos.test_explicit_missing_repo_warns_and_is_dropped for
        the warning, and
        TestIssue93Residuals.test_missing_repos_path_is_named_and_the_run_fails
        for the end-to-end CLI behaviour. What is pinned here is that
        prepare() stays empty rather than inventing skills for a path that
        is not there.
        """
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


# The declaration this skill actually ships, by its real path. Needed
# explicitly because tests redirect ACCOUNT_SKILLS_FILE to stay hermetic.
REAL_DECLARATION = Path(__file__).parent.parent / "account-skills.txt"


def write_declaration(path, names):
    """Write an account-store membership list in the real on-disk format."""
    path.write_text(
        "# test declaration\n" + "".join(f"{n}\n" for n in names),
        encoding="utf-8",
    )
    return path


class TestVerify:
    @pytest.fixture(autouse=True)
    def _declare_fixture_skills(self, tmp_path, monkeypatch):
        """Declare the fixture skills for the account store.

        These tests are about the CONTENT comparison, not about membership,
        so they run against a declaration that admits every skill they use.
        Membership semantics have their own class below.
        """
        monkeypatch.setattr(
            "sync_skills.ACCOUNT_SKILLS_FILE",
            write_declaration(
                tmp_path / "declared.txt", ["skill-a", "skill-b", "sync-skills"]
            ),
        )

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
            # Normalise before converting. On Windows the fixture's own
            # SKILL.md is already CRLF, so a bare \n -> \r\n replace
            # produced \r\r\n: the test manufactured the drift it exists
            # to prove is not drift, and failed on its own fixture.
            transform=lambda b: b.replace(b"\r\n", b"\n").replace(
                b"\n", b"\r\n"
            ),
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
# Declared account-store membership (ADR 0002)
#
# The gate's first cut made "selected but absent from the account" a failure
# unconditionally. That is right for --skill and for the git-changed default,
# and WRONG for --all: --all enumerates the whole registry, and most of the
# registry is correctly absent from the account store. Thirteen expected
# failures buried four real ones, which is the exact unreadability the gate
# exists to remove. These tests pin the four-way verdict on the declaration.
# ---------------------------------------------------------------------------

class TestLoadAccountDeclaration:
    def test_parses_names_ignoring_comments_and_blanks(self, tmp_path):
        f = tmp_path / "list.txt"
        f.write_text(
            "# header comment\n"
            "\n"
            "alpha\n"
            "  beta  \n"
            "gamma  # trailing note\n"
            "\n",
            encoding="utf-8",
        )
        assert sync_skills.load_account_declaration(f) == {"alpha", "beta", "gamma"}

    def test_missing_file_is_none_not_empty_set(self, tmp_path):
        """None and set() must not be conflated.

        An empty set reads as "nothing belongs on the account", which would
        reclassify every skill as undeclared and silently neuter the gate.
        """
        assert sync_skills.load_account_declaration(tmp_path / "nope.txt") is None

    def test_shipped_declaration_is_the_ruled_set(self):
        """Lock the declared membership so a change has to be deliberate.

        Adding a name here is close to a one-way door — the upload API has no
        delete — so membership moves through this assertion, not by accident.
        """
        declared = sync_skills.load_account_declaration()
        assert declared == {
            "adam-writing-style",
            "fastmail",
            "finding-unknowns",
            "ocr-pdfs",
            "pdf-ocr-audit",
            "rename-pdfs",
            "sync-cc-settings-between-wsl-and-windows",
            "sync-skills",
            "wj-next-break",
            "writing-adrs",
        }


class TestVerifyDeclaration:
    def _account(self, tmp_path, monkeypatch, mirrored=()):
        account_dir = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account_dir)
        write_manifest(account_dir, [n for n, _ in mirrored])
        return account_dir

    def test_undeclared_and_absent_is_not_a_failure(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """The --all noise case: correctly absent must not read as broken."""
        self._account(tmp_path, monkeypatch)

        ok = verify(
            [repo_with_skills],
            skill_names=["skill-a", "skill-b"],
            declared=set(),
            named=False,
        )

        assert ok is True
        out = capsys.readouterr().out
        assert "FAIL" not in out
        assert "skill-a" in out and "skill-b" in out

    def test_undeclared_absent_collapses_to_one_line(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """One summary line, not one per skill — the signal must stay legible."""
        self._account(tmp_path, monkeypatch)

        verify(
            [repo_with_skills],
            skill_names=["skill-a", "skill-b"],
            declared=set(),
            named=False,
        )

        lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
        assert len(lines) == 1
        assert "not declared" in lines[0]

    def test_undeclared_but_present_on_account_is_a_failure(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """github-actions-repo-settings was exactly this: uploaded, never ruled on.

        Nothing surfaced it until a human trimmed the store by hand, and #59's
        body was still listing it as live months later.
        """
        account_dir = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account_dir)
        mirror_skill(account_dir, repo_with_skills / "skills" / "skill-a", "skill-a")
        write_manifest(account_dir, ["skill-a"])

        ok = verify(
            [repo_with_skills],
            skill_names=["skill-a"],
            declared=set(),
            named=False,
        )

        assert ok is False
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "NOT declared" in out

    def test_declared_but_absent_says_so_distinctly(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """A missing upload must not read like content drift.

        finding-unknowns and writing-adrs are in this state right now: ruled
        in, not yet uploaded. The message has to name that, or the operator
        cannot tell it from a re-upload that landed wrong.
        """
        self._account(tmp_path, monkeypatch)

        ok = verify(
            [repo_with_skills],
            skill_names=["skill-a"],
            declared={"skill-a"},
            named=False,
        )

        assert ok is False
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "declared for the account store but NOT on it" in out
        assert "DRIFT" not in out

    def test_named_undeclared_skill_is_an_operator_error(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """--skill NAME for something that shouldn't be there must not pass."""
        self._account(tmp_path, monkeypatch)

        ok = verify(
            [repo_with_skills],
            skill_names=["skill-a"],
            declared={"skill-b"},
            named=True,
        )

        assert ok is False
        err = capsys.readouterr().err
        assert "skill-a" in err
        assert "not declared" in err

    def test_unreadable_declaration_fails_closed(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """No declaration means no verdict — never a permissive default."""
        self._account(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "sync_skills.ACCOUNT_SKILLS_FILE", tmp_path / "missing.txt"
        )

        ok = verify([repo_with_skills], skill_names=["skill-a"])

        assert ok is False
        assert "declaration is missing" in capsys.readouterr().err

    def test_nothing_declared_in_selection_says_nothing_was_verified(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """Exit 0 is honest here, but it must not look like a verified-clean run."""
        self._account(tmp_path, monkeypatch)

        ok = verify(
            [repo_with_skills],
            skill_names=["skill-a", "skill-b"],
            declared=set(),
            named=False,
        )

        assert ok is True
        assert "no account copy was verified" in capsys.readouterr().err


class TestPrepareDeclaration:
    def test_warns_before_uploading_an_undeclared_skill(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """The warning has to land BEFORE the POST.

        There is no delete API, so --verify catching an undeclared upload
        afterwards cannot undo it.
        """
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", tmp_path / "account")
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: None)

        result = prepare(
            [repo_with_skills], skill_names=["skill-a"], declared=set()
        )

        # The payload is NOT filtered — the operator decides what to POST.
        assert [s["name"] for s in result["skills"]] == ["skill-a"]
        err = capsys.readouterr().err
        assert "WARNING" in err and "skill-a" in err and "one-way door" in err

    def test_silent_for_a_declared_skill(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", tmp_path / "account")
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: None)

        prepare(
            [repo_with_skills], skill_names=["skill-a"], declared={"skill-a"}
        )

        assert "not declared" not in capsys.readouterr().err


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

    def test_home_repos_layout_is_not_consulted(self, tmp_path, monkeypatch):
        """~/repos/<name> is no longer a candidate, even when it exists.

        This is the reported defect, at the unit level: on Windows a
        directory at ~/repos/agentskills outranked the checkout the script
        was running from, yielded zero skills, and the run died claiming
        --all had not been passed.
        """
        monkeypatch.delenv("AGENTSKILLS_REPOS", raising=False)
        home = tmp_path / "home"
        decoy = home / "repos" / "agentskills"
        decoy.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr("sync_skills._self_repo", lambda: None)

        assert decoy not in sync_skills.resolve_repos(None)
        assert sync_skills.resolve_repos(None) == []

    def test_home_repos_decoy_loses_to_the_self_checkout(self, tmp_path, monkeypatch):
        """The decoy must not merely be dropped — the real tree must win.

        Dropping ~/repos and resolving nothing would still leave the
        operator stuck; the point is that the checkout the script lives in
        is what gets scanned.
        """
        monkeypatch.delenv("AGENTSKILLS_REPOS", raising=False)
        home = tmp_path / "home"
        decoy = home / "repos" / "agentskills"
        decoy.mkdir(parents=True)
        real = tmp_path / "checkout" / "agentskills"
        (real / "plugins").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr("sync_skills._self_repo", lambda: real)

        assert sync_skills.resolve_repos(None) == [real]

    def test_windows_default_root_is_not_consulted(self, tmp_path, monkeypatch):
        """The hardcoded D:\\repos\\adam-s-daniel root is gone entirely.

        It was a guess about one machine baked into every machine; the
        constant it lived in must not come back.
        """
        assert not hasattr(sync_skills, "WINDOWS_REPO_ROOT")
        monkeypatch.delenv("AGENTSKILLS_REPOS", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        win_root = tmp_path / "d-repos" / "adam-s-daniel"
        (win_root / "agentskills").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr("sync_skills._self_repo", lambda: None)

        assert win_root / "agentskills" not in sync_skills.resolve_repos(None)

    def test_self_repo_is_claimed_for_its_own_name_only(self, tmp_path, monkeypatch):
        """The self-checkout answers for its own name, and nothing else.

        Claiming it for every declared repo is how agentskills-private would
        silently resolve to the agentskills clone and verify against the
        wrong tree.
        """
        monkeypatch.delenv("AGENTSKILLS_REPOS", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        self_repo = tmp_path / "agentskills"
        (self_repo / "plugins").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr("sync_skills._self_repo", lambda: self_repo)

        assert sync_skills._repo_candidates("agentskills") == [self_repo]
        assert sync_skills._repo_candidates("agentskills-private") == []
        assert sync_skills.resolve_repos(None) == [self_repo]

    def test_unresolvable_repo_says_it_went_unexamined(self, tmp_path, monkeypatch, capsys):
        """agentskills-private can no longer be found implicitly — say so.

        The deliberate cost of deleting the guesses. It must never degrade
        into silence: "not looked at" and "looked at, nothing to do" are
        different answers.
        """
        monkeypatch.delenv("AGENTSKILLS_REPOS", raising=False)
        self_repo = tmp_path / "agentskills"
        (self_repo / "plugins").mkdir(parents=True)
        monkeypatch.setattr("sync_skills._self_repo", lambda: self_repo)

        sync_skills.resolve_repos(None)

        err = capsys.readouterr().err
        assert "agentskills-private" in err
        assert "NONE of its skills were examined" in err
        assert "AGENTSKILLS_REPOS" in err

    def test_returns_empty_when_nothing_resolves(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AGENTSKILLS_REPOS", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr("sync_skills._self_repo", lambda: None)

        assert sync_skills.resolve_repos(None) == []

    def test_explicit_repos_beat_the_env_var(self, tmp_path, monkeypatch):
        """--repos wins outright; the env var is not merged into it."""
        flagged = tmp_path / "flagged"
        env_repo = tmp_path / "from-env"
        flagged.mkdir()
        env_repo.mkdir()
        monkeypatch.setenv("AGENTSKILLS_REPOS", str(env_repo))

        assert sync_skills.resolve_repos([str(flagged)]) == [flagged]


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


def run_cli(*args, home, account_list=None):
    """Run sync_skills.py in a sandboxed HOME (which relocates the mirror).

    ``account_list`` points --account-list at a sandbox membership list, so
    these end-to-end runs exercise the real loader instead of the shipped
    declaration (which knows nothing about the fixture skills).
    """
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env.pop("AGENTSKILLS_REPOS", None)
    extra = ["--account-list", str(account_list)] if account_list else []
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, *extra],
        capture_output=True, env=env, **TEXT,
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
    return {
        "home": home,
        "account": account,
        "repo": repo,
        "skill": skill,
        "declared": write_declaration(tmp_path / "declared.txt", ["skill-a"]),
        "undeclared": write_declaration(tmp_path / "empty.txt", []),
    }


class TestCliExitCodes:
    def test_verify_passes_when_account_matches(self, sandbox):
        mirror_skill(sandbox["account"], sandbox["skill"], "skill-a")
        proc = run_cli("--verify", "--all", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"], account_list=sandbox["declared"])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "OK" in proc.stdout

    def test_verify_fails_on_content_drift(self, sandbox):
        mirror_skill(sandbox["account"], sandbox["skill"], "skill-a")
        (sandbox["account"] / "skill-a" / "SKILL.md").write_text("stale\n")
        proc = run_cli("--verify", "--all", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"], account_list=sandbox["declared"])
        assert proc.returncode != 0
        assert "DRIFT" in proc.stdout

    def test_verify_fails_when_skill_never_landed(self, sandbox):
        # Account mirror exists but holds no copy of skill-a.
        proc = run_cli("--verify", "--all", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"], account_list=sandbox["declared"])
        assert proc.returncode != 0
        assert "FAIL" in proc.stdout

    def test_verify_all_passes_over_an_undeclared_absent_skill(self, sandbox):
        """--all must not fail on the registry being bigger than the account.

        This is the regression the declaration exists for: before it, every
        undeclared skill under --all produced a FAIL, and thirteen of those
        hid the four that meant something.
        """
        proc = run_cli("--verify", "--all", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"], account_list=sandbox["undeclared"])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "FAIL" not in proc.stdout
        assert "not declared" in proc.stdout

    def test_verify_skill_rejects_an_undeclared_name(self, sandbox):
        """--skill for something that shouldn't be on the account is an error."""
        proc = run_cli("--verify", "--repos", str(sandbox["repo"]),
                       "--skill", "skill-a",
                       home=sandbox["home"], account_list=sandbox["undeclared"])
        assert proc.returncode != 0
        assert "not declared" in proc.stderr

    def test_verify_fails_on_unreadable_account_list(self, sandbox, tmp_path):
        """A typo'd --account-list must not fall back to the shipped one."""
        proc = run_cli("--verify", "--all", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"], account_list=tmp_path / "nope.txt")
        assert proc.returncode != 0
        assert "not readable" in proc.stderr

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
# "Nothing happened" messages must say WHICH nothing, and where it looked
#
# The reported defect: `--verify --all` on Windows printed "no skills
# selected ... Pass --all" while --all was on the command line. The message
# covered three unrelated situations and named none of the repos it had
# resolved, which cost three round-trips before anyone looked at resolution.
# ---------------------------------------------------------------------------

class TestEmptyRunDiagnostics:
    def test_all_with_an_empty_repo_blames_resolution_not_the_flags(
        self, tmp_path, monkeypatch, capsys
    ):
        """The exact Windows shape: --all was passed, the repo held nothing."""
        empty = tmp_path / "decoy-agentskills"
        empty.mkdir()
        account = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account)
        write_manifest(account, [])

        ok = verify([empty], skill_names=[], declared=set(), selection="all")

        err = capsys.readouterr().err
        assert ok is False
        assert "no resolved repo contains any" in err
        assert "Nothing is wrong with the flags" in err
        assert "no skills selected" not in err
        assert str(empty) in err
        assert "0 skill(s) found" in err

    def test_nothing_selected_keeps_its_own_message(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        """Case 1 stays distinct from case 2, and now names the repos too."""
        account = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account)
        write_manifest(account, [])

        ok = verify(
            [repo_with_skills], skill_names=[], declared=set(),
            selection="changed",
        )

        err = capsys.readouterr().err
        assert ok is False
        assert "no skills selected" in err
        assert "no resolved repo contains any" not in err
        assert str(repo_with_skills) in err
        assert "2 skill(s) found" in err

    def test_named_skill_not_found_names_the_repos(
        self, repo_with_skills, monkeypatch, tmp_path, capsys
    ):
        account = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account)
        write_manifest(account, [])

        ok = verify(
            [repo_with_skills], skill_names=["skill-typo"], declared=set(),
            selection="skill",
        )

        err = capsys.readouterr().err
        assert ok is False
        assert "skill-typo" in err
        assert str(repo_with_skills) in err

    def test_cli_all_over_an_empty_repo(self, sandbox, tmp_path):
        """End to end, with the real argv the user typed."""
        empty = tmp_path / "empty-repo"
        empty.mkdir()
        proc = run_cli("--verify", "--all", "--repos", str(empty),
                       home=sandbox["home"], account_list=sandbox["declared"])
        assert proc.returncode != 0
        assert "no resolved repo contains any" in proc.stderr
        assert str(empty) in proc.stderr
        assert "no skills selected" not in proc.stderr

    def test_describe_resolved_repos_counts_each_repo(
        self, repo_with_skills, tmp_path
    ):
        empty = tmp_path / "empty"
        empty.mkdir()
        text = sync_skills.describe_resolved_repos([repo_with_skills, empty])
        assert f"{repo_with_skills}  (2 skill(s) found)" in text
        assert f"{empty}  (0 skill(s) found)" in text

    def test_describe_resolved_repos_says_none_and_how_to_fix_it(self):
        text = sync_skills.describe_resolved_repos([])
        assert "no repo was resolved" in text
        assert "AGENTSKILLS_REPOS" in text


# ---------------------------------------------------------------------------
# Repo-state gate — off main / behind origin/main
#
# Uploads are built from the working tree, and the upload API has no delete,
# so syncing from a stale or off-main clone publishes the wrong bytes
# irreversibly. The remote is a local bare repo: the gate's fetch has to
# really run (not trusting stale refs is its entire point), and a path remote
# keeps that deterministic and offline.
# ---------------------------------------------------------------------------

GIT_ID = [
    "-c", "user.name=Test", "-c", "user.email=test@example.com",
    "-c", "commit.gpgsign=false",
]


def _git_run(args, cwd):
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, **TEXT,
    )
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


@pytest.fixture()
def git_clone(tmp_path):
    """A real clone on ``main``, up to date with a local bare origin."""
    origin = tmp_path / "origin.git"
    _git_run(["init", "--bare", "--initial-branch=main", str(origin)], tmp_path)

    work = tmp_path / "agentskills"
    _git_run(["init", "--initial-branch=main", str(work)], tmp_path)
    skill = work / "skills" / "skill-a"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: skill-a\n---\nbody\n")
    _git_run(["add", "-A"], work)
    _git_run([*GIT_ID, "commit", "-m", "seed"], work)
    _git_run(["remote", "add", "origin", str(origin)], work)
    _git_run(["push", "-u", "origin", "main"], work)
    return work


def run_cli_no_tty(*args, home):
    """Run the CLI with stdin closed, i.e. the way an agent drives it.

    Explicitly DEVNULL rather than inherited: a subprocess inherits the real
    fd 0, so a human running pytest from a terminal would otherwise hand the
    gate a TTY and hang the suite on a prompt.
    """
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env.pop("AGENTSKILLS_REPOS", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, env=env, stdin=subprocess.DEVNULL, **TEXT,
    )


class TestRepoStateGate:
    def test_clean_clone_passes(self, git_clone):
        state = sync_skills.repo_state(git_clone)
        assert state["problems"] == []
        assert state["unknowns"] == []
        assert sync_skills.check_repo_state([git_clone]) is True

    def test_off_main_is_a_problem(self, git_clone):
        _git_run(["checkout", "-b", "feature"], git_clone)
        problems = sync_skills.repo_state(git_clone)["problems"]
        assert any("on branch 'feature'" in p for p in problems)
        assert any("not 'main'" in p for p in problems)

    def test_behind_upstream_is_a_problem(self, git_clone):
        (git_clone / "skills" / "skill-a" / "SKILL.md").write_text("v2\n")
        _git_run(["add", "-A"], git_clone)
        _git_run([*GIT_ID, "commit", "-m", "second"], git_clone)
        _git_run(["push", "origin", "main"], git_clone)
        _git_run(["reset", "--hard", "HEAD~1"], git_clone)

        problems = sync_skills.repo_state(git_clone)["problems"]
        assert any("not up to date" in p for p in problems)
        assert any("1 behind, 0 ahead" in p for p in problems)

    def test_ahead_of_upstream_is_a_problem(self, git_clone):
        (git_clone / "skills" / "skill-a" / "SKILL.md").write_text("v2\n")
        _git_run(["add", "-A"], git_clone)
        _git_run([*GIT_ID, "commit", "-m", "unpushed"], git_clone)

        problems = sync_skills.repo_state(git_clone)["problems"]
        assert any("0 behind, 1 ahead" in p for p in problems)

    def test_the_fetch_is_what_detects_a_stale_clone(self, git_clone, tmp_path):
        """Someone else pushed; this clone's remote-tracking ref is stale.

        The only way to see it is to fetch, which is why the gate does. If
        the fetch were dropped in favour of the refs already on disk, this
        clone would report itself up to date while being a commit behind.
        """
        origin = tmp_path / "origin.git"
        other = tmp_path / "other"
        _git_run(["clone", str(origin), str(other)], tmp_path)
        (other / "elsewhere.txt").write_text("from another machine\n")
        _git_run(["add", "-A"], other)
        _git_run([*GIT_ID, "commit", "-m", "landed on main elsewhere"], other)
        _git_run(["push", "origin", "main"], other)

        # Stale refs alone still say "up to date" — that is the trap.
        stale = _git_run(
            ["rev-list", "--left-right", "--count", "origin/main...HEAD"],
            git_clone,
        )
        assert stale.split() == ["0", "0"]

        problems = sync_skills.repo_state(git_clone)["problems"]
        assert any("1 behind, 0 ahead" in p for p in problems)

    def test_git_returns_none_on_timeout(self, git_clone, monkeypatch):
        """A hung remote becomes "could not determine", not a hang or a pass."""
        def _boom(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="git", timeout=1)

        monkeypatch.setattr(subprocess, "run", _boom)
        assert sync_skills._git(["fetch"], cwd=git_clone, timeout=1) is None

    def test_unfetchable_remote_is_unknown_not_a_verdict(self, git_clone):
        """No remote to ask means "could not determine", never "fine"."""
        _git_run(["remote", "remove", "origin"], git_clone)
        state = sync_skills.repo_state(git_clone)
        assert state["problems"] == []
        assert any("could not fetch" in u for u in state["unknowns"])
        assert any("could not be determined" in u for u in state["unknowns"])

    def test_non_git_directory_is_unknown_not_a_problem(self, tmp_path):
        """A plain exported tree has no branch to be wrong — don't block it."""
        plain = tmp_path / "plain"
        plain.mkdir()
        state = sync_skills.repo_state(plain)
        assert state["problems"] == []
        assert any("not a git checkout" in u for u in state["unknowns"])

    def test_unknowns_are_reported_on_stderr(self, tmp_path, capsys):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert sync_skills.check_repo_state([plain]) is True
        assert "could not be determined" in capsys.readouterr().err

    def test_non_tty_aborts_instead_of_prompting(self, git_clone, monkeypatch, capsys):
        _git_run(["checkout", "-b", "feature"], git_clone)
        monkeypatch.setattr("sync_skills._stdin_is_tty", lambda: False)

        assert sync_skills.check_repo_state([git_clone]) is False
        err = capsys.readouterr().err
        assert "not a terminal" in err
        assert "--yes" in err

    def test_yes_bypasses_and_says_so(self, git_clone, monkeypatch, capsys):
        _git_run(["checkout", "-b", "feature"], git_clone)
        monkeypatch.setattr("sync_skills._stdin_is_tty", lambda: False)

        assert sync_skills.check_repo_state([git_clone], assume_yes=True) is True
        err = capsys.readouterr().err
        assert "--yes bypassed" in err
        assert "on branch 'feature'" in err

    def test_tty_answering_no_aborts(self, git_clone, monkeypatch, capsys):
        _git_run(["checkout", "-b", "feature"], git_clone)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")

        assert sync_skills.check_repo_state([git_clone]) is False
        assert "not confirmed" in capsys.readouterr().err

    def test_tty_bare_enter_aborts(self, git_clone, monkeypatch):
        """Default is no: Enter must not be a way to say yes."""
        _git_run(["checkout", "-b", "feature"], git_clone)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr("builtins.input", lambda prompt="": "")

        assert sync_skills.check_repo_state([git_clone]) is False

    def test_tty_eof_aborts(self, git_clone, monkeypatch):
        _git_run(["checkout", "-b", "feature"], git_clone)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

        def _eof(prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", _eof)
        assert sync_skills.check_repo_state([git_clone]) is False

    def test_tty_answering_yes_proceeds(self, git_clone, monkeypatch):
        _git_run(["checkout", "-b", "feature"], git_clone)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")

        assert sync_skills.check_repo_state([git_clone]) is True


class TestWindowsDecoyRegression:
    """The reported defect, end to end, with the argv the user actually typed.

    `--verify --all` on Windows died with "no skills selected ... Pass --all"
    because a directory at ~/repos/agentskills outranked the checkout the
    script was running from and enumerated nothing. Reproduced here by
    running a COPY of the script from a synthetic checkout, so the real
    __file__-derived _self_repo() is what has to win — a monkeypatched one
    would prove nothing about the path that failed.
    """

    @pytest.fixture()
    def planted(self, tmp_path):
        """A synthetic clone holding the script, plus a decoy ~/repos clone."""
        checkout = tmp_path / "real" / "agentskills"
        skill_dir = checkout / "plugins" / "adam-local" / "skills" / "sync-skills"
        skill_dir.mkdir(parents=True)
        shutil.copy2(SCRIPT, skill_dir / "sync_skills.py")
        shutil.copy2(SCRIPT.parent / "account-skills.txt", skill_dir)
        real_skill = checkout / "plugins" / "adam" / "skills" / "planted-skill"
        real_skill.mkdir(parents=True)
        (real_skill / "SKILL.md").write_text("---\nname: planted-skill\n---\n")

        # On main and up to date, so the repo-state gate is not what is
        # under test here.
        origin = tmp_path / "origin.git"
        _git_run(["init", "--bare", "--initial-branch=main", str(origin)], tmp_path)
        _git_run(["init", "--initial-branch=main", str(checkout)], tmp_path)
        _git_run(["add", "-A"], checkout)
        _git_run([*GIT_ID, "commit", "-m", "seed"], checkout)
        _git_run(["remote", "add", "origin", str(origin)], checkout)
        _git_run(["push", "-u", "origin", "main"], checkout)

        # The decoy: exists, is not a clone of anything, holds no skills.
        home = tmp_path / "home"
        decoy = home / "repos" / "agentskills"
        (decoy / "plugins").mkdir(parents=True)
        return {
            "home": home,
            "decoy": decoy,
            "checkout": checkout,
            "script": skill_dir / "sync_skills.py",
        }

    def _run(self, planted, *args):
        env = dict(os.environ)
        env["HOME"] = str(planted["home"])
        env["USERPROFILE"] = str(planted["home"])
        env.pop("AGENTSKILLS_REPOS", None)
        return subprocess.run(
            [sys.executable, str(planted["script"]), *args],
            capture_output=True, env=env, stdin=subprocess.DEVNULL, **TEXT,
        )

    def test_decoy_no_longer_outranks_the_self_checkout(self, planted):
        proc = self._run(planted, "--dry-run", "--all")

        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "planted-skill" in proc.stdout
        assert str(planted["decoy"]) not in proc.stdout
        assert str(planted["decoy"]) not in proc.stderr

    def test_all_is_never_reported_as_missing(self, planted):
        """The literal symptom: --all was passed and blamed for being absent."""
        proc = self._run(planted, "--dry-run", "--all")
        assert "no skills selected" not in proc.stderr
        assert "Pass --all" not in proc.stderr

    def test_private_repo_absence_is_stated_not_guessed(self, planted):
        """A decoy at ~/repos/agentskills-private must not fill the gap."""
        private_decoy = planted["home"] / "repos" / "agentskills-private"
        private_decoy.mkdir(parents=True)

        proc = self._run(planted, "--dry-run", "--all")

        assert str(private_decoy) not in proc.stdout
        assert "agentskills-private" in proc.stderr
        assert "NONE of its skills were examined" in proc.stderr


class TestRepoStateGateCli:
    def test_off_main_aborts_the_run(self, git_clone, tmp_path):
        _git_run(["checkout", "-b", "feature"], git_clone)
        home = tmp_path / "home"
        home.mkdir()
        proc = run_cli_no_tty(
            "--dry-run", "--all", "--repos", str(git_clone), home=home
        )
        assert proc.returncode != 0
        assert "on branch 'feature'" in proc.stderr
        assert "not a terminal" in proc.stderr

    def test_behind_upstream_aborts_the_run(self, git_clone, tmp_path):
        (git_clone / "skills" / "skill-a" / "SKILL.md").write_text("v2\n")
        _git_run(["add", "-A"], git_clone)
        _git_run([*GIT_ID, "commit", "-m", "second"], git_clone)
        _git_run(["push", "origin", "main"], git_clone)
        _git_run(["reset", "--hard", "HEAD~1"], git_clone)
        home = tmp_path / "home"
        home.mkdir()

        proc = run_cli_no_tty(
            "--dry-run", "--all", "--repos", str(git_clone), home=home
        )
        assert proc.returncode != 0
        assert "not up to date" in proc.stderr

    def test_yes_lets_the_run_through(self, git_clone, tmp_path):
        _git_run(["checkout", "-b", "feature"], git_clone)
        home = tmp_path / "home"
        home.mkdir()
        proc = run_cli_no_tty(
            "--dry-run", "--all", "--yes", "--repos", str(git_clone), home=home
        )
        assert proc.returncode == 0, proc.stderr
        assert "--yes bypassed" in proc.stderr
        assert "skill-a" in proc.stdout

    def test_clean_clone_runs_without_the_gate_complaining(self, git_clone, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        proc = run_cli_no_tty(
            "--dry-run", "--all", "--repos", str(git_clone), home=home
        )
        assert proc.returncode == 0, proc.stderr
        assert "skill-a" in proc.stdout

    def test_mark_synced_is_exempt_from_the_gate(self, git_clone, tmp_path):
        """It touches only the state file, so repo state is irrelevant to it."""
        _git_run(["checkout", "-b", "feature"], git_clone)
        home = tmp_path / "home"
        home.mkdir()
        proc = run_cli_no_tty(
            "--mark-synced", "skill-a:abc123", "--repos", str(git_clone),
            home=home,
        )
        assert proc.returncode == 0, proc.stderr
        assert "on branch" not in proc.stderr


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


# ---------------------------------------------------------------------------
# Issue #93: residual defects surfaced while fixing repo resolution (#92)
#
# One is_update shared by the preview and the run; one resolution pass handed
# down; and no trusting an account mirror of unknown age to set the overwrite
# flag. Each of these was small, real, and independently fixable.
# ---------------------------------------------------------------------------


class TestIssue93Residuals:
    def test_dry_run_tags_update_from_the_account_not_just_the_state_file(
        self, sandbox
    ):
        """#93.1: the preview disagreed with the run on a fresh machine.

        Nothing was uploaded from THIS machine, so the state file is empty,
        but the skill IS on the account and the real upload correctly
        overwrites. The preview tagged from the state file alone: NEW.
        """
        mirror_skill(sandbox["account"], sandbox["skill"], "skill-a")
        proc = run_cli("--dry-run", "--all", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"])
        assert proc.returncode == 0, proc.stderr
        assert "UPDATE" in proc.stdout
        assert "NEW" not in proc.stdout

    def test_dry_run_and_prepare_agree_on_is_update(self, sandbox):
        """The preview and the run must never answer this differently."""
        mirror_skill(sandbox["account"], sandbox["skill"], "skill-a")
        dry = run_cli("--dry-run", "--all", "--repos", str(sandbox["repo"]),
                      home=sandbox["home"])
        prep = run_cli("--prepare", "--all", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"])
        assert dry.returncode == 0 and prep.returncode == 0
        previewed_update = "UPDATE" in dry.stdout
        payload = json.loads(prep.stdout)
        assert payload["skills"][0]["is_update"] is previewed_update

    def test_dry_run_does_not_invent_a_row_for_a_repo_without_the_skill(
        self, sandbox, tmp_path
    ):
        """A requested name was printed once per RESOLVED repo.

        With two repos resolved and only one carrying the skill, the preview
        claimed a second copy - which reads as a name collision and invites
        an upload from the wrong tree.
        """
        other = tmp_path / "other-repo"
        unrelated = other / "skills" / "unrelated"
        unrelated.mkdir(parents=True)
        (unrelated / "SKILL.md").write_text(
            "---\nname: unrelated\n---\nbody\n"
        )

        proc = run_cli("--dry-run", "--skill", "skill-a",
                       "--repos", str(sandbox["repo"]), str(other),
                       home=sandbox["home"])
        assert proc.returncode == 0, proc.stderr
        rows = [ln for ln in proc.stdout.splitlines() if "skill-a" in ln]
        assert len(rows) == 1, proc.stdout

    def test_prepare_warns_when_the_mirror_is_too_old_to_trust(self, sandbox):
        """#93.2: prepare read the mirror at ANY age to set overwrite.

        --verify refuses a mirror past MIRROR_MAX_AGE rather than trust it;
        prepare had no freshness check at all, so a week-old mirror silently
        drove the flag. The payload is still built - the operator decides
        what to POST - but the doubt is stated before the upload, not after.
        """
        mirror_skill(sandbox["account"], sandbox["skill"], "skill-a")
        write_manifest(
            sandbox["account"], ["skill-a"],
            age_seconds=sync_skills.MIRROR_MAX_AGE_SECONDS + 60,
        )
        proc = run_cli("--prepare", "--all", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"])
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["skills"], "payload should still build"
        assert "overwrite flag" in proc.stderr
        assert "stale" in proc.stderr

    def test_prepare_says_nothing_about_a_fresh_mirror(self, sandbox):
        """The warning has to stay rare enough to still mean something."""
        mirror_skill(sandbox["account"], sandbox["skill"], "skill-a")
        write_manifest(sandbox["account"], ["skill-a"])
        proc = run_cli("--prepare", "--all", "--repos", str(sandbox["repo"]),
                       home=sandbox["home"])
        assert proc.returncode == 0, proc.stderr
        assert "overwrite flag" not in proc.stderr

    def test_expected_repo_names_warns_but_never_resolves(
        self, tmp_path, monkeypatch, capsys
    ):
        """#93.3: the constant is a warning list, not a lookup.

        Adding a name buys the "went unexamined" warning and nothing else -
        a new registry still has to be NAMED to be synced.
        """
        monkeypatch.setattr(
            sync_skills, "EXPECTED_REPO_NAMES",
            ("agentskills", "not-a-real-registry"),
        )
        monkeypatch.delenv("AGENTSKILLS_REPOS", raising=False)
        resolved = sync_skills.resolve_repos()
        err = capsys.readouterr().err
        assert "not-a-real-registry" in err
        assert all(p.name != "not-a-real-registry" for p in resolved)

    def test_missing_repos_path_is_named_and_the_run_fails(
        self, sandbox, tmp_path
    ):
        """#93.4: one resolution pass, and it still refuses to go quiet."""
        proc = run_cli("--prepare", "--all", "--repos", str(tmp_path / "nope"),
                       home=sandbox["home"])
        assert proc.returncode != 0
        assert "nope" in proc.stderr


# ---------------------------------------------------------------------------
# --zip-dir: ZIPs as real files, so the browser can read them directly
# ---------------------------------------------------------------------------

class TestPrepareZipDir:
    """The upload path in SKILL.md section 3 hands the browser a FILE.

    The point of this mode is that the ZIP never becomes base64 in the
    agent's context, so the assertion that matters most here is the
    NEGATIVE one: zip_b64 must be absent. A version that emitted both
    would pass a naive "zip_path is present" test while still paying the
    whole cost the mode exists to avoid.
    """

    def _prep(self, repo, monkeypatch, tmp_path, zip_dir):
        monkeypatch.setattr("sync_skills.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", tmp_path / "account")
        monkeypatch.setattr("sync_skills.get_org_id_hint", lambda: None)
        return prepare([repo], skill_names=["skill-a"], zip_dir=zip_dir)

    def test_writes_a_real_zip_file(self, repo_with_skills, monkeypatch, tmp_path):
        zd = tmp_path / "zips"
        result = self._prep(repo_with_skills, monkeypatch, tmp_path, zd)
        entry = result["skills"][0]
        written = Path(entry["zip_path"])
        assert written.exists()
        assert written == zd / "skill-a.zip"
        assert zipfile.is_zipfile(written)

    def test_omits_zip_b64(self, repo_with_skills, monkeypatch, tmp_path):
        result = self._prep(repo_with_skills, monkeypatch, tmp_path, tmp_path / "zips")
        assert "zip_b64" not in result["skills"][0]

    def test_reports_size_and_digest_of_what_it_wrote(
        self, repo_with_skills, monkeypatch, tmp_path
    ):
        """The digest is the only end-to-end check on the file the browser
        picks up: nothing else compares the bytes on disk to the bytes this
        script built."""
        import hashlib

        result = self._prep(repo_with_skills, monkeypatch, tmp_path, tmp_path / "zips")
        entry = result["skills"][0]
        raw = Path(entry["zip_path"]).read_bytes()
        assert entry["zip_bytes"] == len(raw)
        assert entry["zip_sha256"] == hashlib.sha256(raw).hexdigest()

    def test_creates_missing_directory(self, repo_with_skills, monkeypatch, tmp_path):
        zd = tmp_path / "a" / "b" / "c"
        result = self._prep(repo_with_skills, monkeypatch, tmp_path, zd)
        assert Path(result["skills"][0]["zip_path"]).exists()

    def test_default_still_emits_base64(self, repo_with_skills, monkeypatch, tmp_path):
        """Without --zip-dir the old payload shape is unchanged."""
        result = self._prep(repo_with_skills, monkeypatch, tmp_path, None)
        entry = result["skills"][0]
        assert "zip_path" not in entry
        assert zipfile.is_zipfile(io.BytesIO(base64.b64decode(entry["zip_b64"])))


# ---------------------------------------------------------------------------
# org id resolution
# ---------------------------------------------------------------------------

class TestOrgIdFromCliConfig:
    """Which org to upload to must be answerable without guessing.

    The account can belong to several orgs; /api/organizations lists them
    all and marks none of them as the one owning the skill store, and the
    wrong choice 404s. ~/.claude.json names the org the CLI is actually
    authenticated against -- the same CLI that writes the mirror --verify
    reads -- so it is the exact answer.
    """

    def _home(self, tmp_path, monkeypatch, payload):
        home = tmp_path / "home"
        home.mkdir()
        if payload is not None:
            (home / ".claude.json").write_text(json.dumps(payload))
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        return home

    def test_reads_organization_uuid(self, tmp_path, monkeypatch):
        uuid = "29094e6a-eeb7-4d76-982e-84e62238e605"
        self._home(tmp_path, monkeypatch, {"oauthAccount": {"organizationUuid": uuid}})
        assert sync_skills.org_id_from_cli_config() == uuid

    def test_missing_file_is_none_not_an_error(self, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch, None)
        assert sync_skills.org_id_from_cli_config() is None

    def test_malformed_json_is_none_not_an_error(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude.json").write_text("{not json")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        assert sync_skills.org_id_from_cli_config() is None

    def test_absent_oauth_account_is_none(self, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch, {"projects": {}})
        assert sync_skills.org_id_from_cli_config() is None

    def test_rejects_a_value_that_is_not_a_uuid(self, tmp_path, monkeypatch):
        """A non-UUID would be pasted straight into an API URL."""
        self._home(tmp_path, monkeypatch, {"oauthAccount": {"organizationUuid": "nope"}})
        assert sync_skills.org_id_from_cli_config() is None

    def test_hint_prefers_cli_config_over_cookie_scrape(self, monkeypatch):
        uuid = "11111111-2222-3333-4444-555555555555"
        monkeypatch.setattr("sync_skills.org_id_from_cli_config", lambda: uuid)
        assert sync_skills.get_org_id_hint() == uuid

    def test_hint_falls_back_when_cli_config_has_nothing(self, tmp_path, monkeypatch):
        """Falling through to the cookie scrape must still work; with no
        Chrome profile present that path simply yields None rather than
        raising."""
        monkeypatch.setattr("sync_skills.org_id_from_cli_config", lambda: None)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nonexistent"))
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "nohome"))
        assert sync_skills.get_org_id_hint() is None


# ---------------------------------------------------------------------------
# --report-issue: the account-store upload tracking issue
#
# The state this mode exists to make visible is INVISIBLE TO EVERY OTHER CHECK
# in this repo, and deliberately so. test_shipped_declaration_is_the_ruled_set
# above CI-locks WHICH names belong on the account store, but nothing can
# check whether the account store received them — that needs a laptop with a
# logged-in browser session. So the window between "the declaration merged"
# and "a laptop uploaded it" is a green CI run either way, and the policy is
# that it must be TRACKED, never BLOCKED.
#
# That non-blocking half is what most of these tests defend. The failure this
# feature could plausibly introduce is not a missed alert — it is a tracker
# that raises, or fails a push, or opens a duplicate issue every morning, or
# (worst, and the #258 shape cms-platform already shipped once) CLOSES a live
# tracking issue because a probe it could not run came back empty.
# ---------------------------------------------------------------------------

class FakeGh:
    """Stand-in for ``sync_skills._gh_api``: records calls, answers canned JSON.

    Monkeypatching at ``_gh_api`` rather than at ``subprocess`` covers
    ``_gh_json`` too (it is a thin parse on top) and keeps every test free of
    a real ``gh`` binary — which the CI runners here do not have and which
    would make these tests network-dependent if they did.
    """

    def __init__(self, repo="Adam-S-Daniel/agentskills", issues=None,
                 comments=None, fail=False):
        self.repo = repo
        self.issues = [] if issues is None else issues
        self.comments = comments or {}
        self.fail = fail
        self.calls = []

    def __call__(self, endpoint, method=None, fields=None):
        self.calls.append((endpoint, method, tuple(fields or ())))
        if self.fail:
            return None
        if "?" in endpoint:
            if "/issues?state=open" in endpoint:
                # Paginate for real. The lookup walks pages, and a stub that
                # serves the whole list on every page cannot tell a found
                # marker from a lookup that never got past page 1.
                page = int(endpoint.split("&page=")[1])
                start = (page - 1) * 100
                return json.dumps(self.issues[start:start + 100])
            if "/comments?" in endpoint:
                number = int(endpoint.split("/issues/")[1].split("/")[0])
                # `&page=`, not `page=`: `per_page=` matches first otherwise.
                page = int(endpoint.split("&page=")[1])
                return json.dumps(self.comments.get(number, []) if page == 1 else [])
            raise AssertionError(f"unexpected GET endpoint: {endpoint}")
        if method == "PATCH":
            return json.dumps({"number": 1, "state": "closed"})
        if endpoint.endswith("/comments"):
            return json.dumps({"id": 999})
        if endpoint.endswith("/issues"):
            return json.dumps({"number": 42, "html_url": "https://example.test/42"})
        raise AssertionError(f"unexpected write endpoint: {endpoint}")

    # -- assertions helpers ------------------------------------------------
    def created_bodies(self):
        return [
            f[len("body="):]
            for endpoint, method, fields in self.calls
            if endpoint.endswith("/issues") and method is None
            for f in fields if f.startswith("body=")
        ]

    def comment_bodies(self):
        return [
            f[len("body="):]
            for endpoint, method, fields in self.calls
            if endpoint.endswith("/comments") and method is None
            for f in fields if f.startswith("body=")
        ]

    def patches(self):
        return [c for c in self.calls if c[1] == "PATCH"]

    def writes(self):
        """Every call that MUTATES something on GitHub."""
        return [c for c in self.calls if "?" not in c[0]]


def gap_pending(*names):
    return sync_skills.UploadGap("pending", sorted(names), "test")


def open_issue(number=7, body=None, pending=()):
    """An open tracking issue carrying the marker (and optionally a set)."""
    if body is None:
        body = sync_skills.ACCOUNT_UPLOAD_ISSUE_MARKER + "\n\n" + (
            sync_skills.hidden_pending_block(list(pending)) if pending else ""
        )
    return {"number": number, "body": body}


class TestAccountUploadGap:
    """The verdict itself: pending / clean / unknown, computed from two inputs."""

    @pytest.fixture(autouse=True)
    def _hermetic_declaration_path(self, tmp_path, monkeypatch):
        """Point ACCOUNT_SKILLS_FILE away from the real one, for every test here.

        ``account_upload_gap`` consults the shipped declaration's git
        provenance (``declaration_differs_from_committed``), so a test that
        left this pointing at the real file would pass or fail on whether THIS
        working tree happens to have account-skills.txt staged or edited —
        exactly the environment dependence the suite is not allowed to have.
        A tmp path is untracked, which is the documented "cannot establish,
        so do not guess" branch.
        """
        monkeypatch.setattr(
            "sync_skills.ACCOUNT_SKILLS_FILE", tmp_path / "declaration.txt"
        )

    def _mirror(self, tmp_path, monkeypatch, present=(), age_seconds=0,
                manifest_names=None):
        account = tmp_path / "account"
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account)
        account.mkdir(parents=True, exist_ok=True)
        for name in present:
            (account / name).mkdir(parents=True, exist_ok=True)
            (account / name / "SKILL.md").write_text("body\n", encoding="utf-8")
        names = list(present) if manifest_names is None else list(manifest_names)
        write_manifest(account, names, age_seconds=age_seconds)
        return account

    def test_declared_but_absent_is_pending(self, tmp_path, monkeypatch):
        self._mirror(tmp_path, monkeypatch, present=["alpha"])
        gap = sync_skills.account_upload_gap(
            {"alpha", "beta"}, sync_skills.account_manifest(), verify_ok=False,
            verified_names={"alpha", "beta"},
        )
        assert gap.state == "pending"
        assert gap.missing == ["beta"]

    def test_pending_is_reported_even_though_verify_went_red(
        self, tmp_path, monkeypatch
    ):
        """The ordering that keeps the verify_ok gate from silencing the alert.

        A declared-but-absent skill IS a --verify failure, so if the gate ran
        before the absence check every real backlog would compute as UNKNOWN
        and the issue would never open in the one case it exists for.
        """
        self._mirror(tmp_path, monkeypatch, present=[])
        gap = sync_skills.account_upload_gap(
            {"alpha"}, sync_skills.account_manifest(), verify_ok=False,
            verified_names={"alpha"},
        )
        assert gap.state == "pending"

    def test_everything_declared_is_present_is_clean(self, tmp_path, monkeypatch):
        self._mirror(tmp_path, monkeypatch, present=["alpha", "beta"])
        gap = sync_skills.account_upload_gap(
            {"alpha", "beta"}, sync_skills.account_manifest(), verify_ok=True,
            verified_names={"alpha", "beta"},
        )
        assert gap.state == "clean"
        assert gap.missing == []

    def test_nothing_absent_but_verify_failed_is_unknown_not_clean(
        self, tmp_path, monkeypatch
    ):
        """F1. The account copies are all THERE; --verify still exited 1.

        DRIFT, MISMATCH and 'on the account without a membership ruling' are
        all present-and-wrong, and absence is the only thing this function can
        measure. Calling that clean is what let the reporter post an
        affirmative all-clear and close the tracking issue on a red run.
        """
        self._mirror(tmp_path, monkeypatch, present=["alpha", "beta"])
        gap = sync_skills.account_upload_gap(
            {"alpha", "beta"}, sync_skills.account_manifest(), verify_ok=False,
            verified_names={"alpha", "beta"},
        )
        assert gap.state == "unknown"
        assert gap.missing == []
        assert "--verify did not pass" in gap.reason

    def test_verify_ok_is_required_not_defaulted(self):
        """No caller can reach the CLEAN arm by forgetting a parameter."""
        with pytest.raises(TypeError):
            sync_skills.account_upload_gap({"alpha"}, {})

    def test_verified_names_is_required_not_defaulted(self):
        """Same rule for the evidence set: a default would restore the hole."""
        with pytest.raises(TypeError):
            sync_skills.account_upload_gap({"alpha"}, {}, verify_ok=True)

    def test_an_EMPTY_declaration_is_unknown_not_clean(self, tmp_path, monkeypatch):
        """The vacuous CLEAN. Nothing declared -> nothing absent -> "complete".

        Every later gate passes vacuously alongside it: verify() compares no
        account copy and returns True, so verify_ok=True is not evidence
        either. Measured before the fix: this exact state closed a live
        tracking issue and posted an affirmative all-clear.
        """
        self._mirror(tmp_path, monkeypatch, present=["alpha"])
        gap = sync_skills.account_upload_gap(
            set(), sync_skills.account_manifest(), verify_ok=True,
            verified_names=set(),
        )
        assert gap.state == "unknown"
        assert "EMPTY" in gap.reason

    def test_a_declared_skill_never_compared_is_unknown_not_clean(
        self, tmp_path, monkeypatch
    ):
        """Present on the account, but nothing opened it on this run.

        verify() selects skills from the resolved repos; a declared skill
        those repos do not carry is never reached, and verify still returns
        True. "Present" was already weaker than "correct"; "never opened" is
        weaker still, and only the strongest of the three licenses a close.
        """
        self._mirror(tmp_path, monkeypatch, present=["alpha", "beta"])
        gap = sync_skills.account_upload_gap(
            {"alpha", "beta"}, sync_skills.account_manifest(), verify_ok=True,
            verified_names={"alpha"},          # beta was never compared
        )
        assert gap.state == "unknown"
        assert "never compared" in gap.reason
        assert "beta" in gap.reason

    def test_verified_names_None_is_not_evidence(self, tmp_path, monkeypatch):
        """A caller that cannot say what it compared has not said "all of it"."""
        self._mirror(tmp_path, monkeypatch, present=["alpha"])
        gap = sync_skills.account_upload_gap(
            {"alpha"}, sync_skills.account_manifest(), verify_ok=True,
            verified_names=None,
        )
        assert gap.state == "unknown"

    def test_an_uncommitted_declaration_is_unknown_not_clean(
        self, tmp_path, monkeypatch
    ):
        """The route that needs no unusual flag at all.

        A locally-edited account-skills.txt is outside the CI lock the write
        speaks for. Removing a name locally makes the rest verify clean and
        CLOSES the issue; adding one makes the issue announce a backlog item
        that does not exist on main. Both directions, one guard.
        """
        self._mirror(tmp_path, monkeypatch, present=["alpha"])
        monkeypatch.setattr(
            "sync_skills.declaration_differs_from_committed",
            lambda path=None: "uncommitted local changes (test)",
        )
        gap = sync_skills.account_upload_gap(
            {"alpha"}, sync_skills.account_manifest(), verify_ok=True,
            verified_names={"alpha"},
        )
        assert gap.state == "unknown"
        assert "uncommitted" in gap.reason

    def test_an_uncommitted_declaration_blocks_the_PENDING_write_too(
        self, tmp_path, monkeypatch
    ):
        """Not just the close: the issue must not name a locally-added skill."""
        self._mirror(tmp_path, monkeypatch, present=[])
        monkeypatch.setattr(
            "sync_skills.declaration_differs_from_committed",
            lambda path=None: "uncommitted local changes (test)",
        )
        gap = sync_skills.account_upload_gap(
            {"locally-added"}, sync_skills.account_manifest(), verify_ok=False,
            verified_names=set(),
        )
        assert gap.state == "unknown"
        assert gap.missing == []

    def test_empty_account_directory_is_not_clean(self, tmp_path, monkeypatch):
        """F3(b). ``account_skill_payload`` returns {} — which is not None.

        So the directory reads as PRESENT to both this module and verify(),
        while verify() goes on to call it MISMATCH and exit 1. This function
        cannot tell the difference and must not try; the verify_ok gate is
        what stops it closing an issue on that run.
        """
        account = tmp_path / "account"
        (account / "alpha").mkdir(parents=True)
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account)
        write_manifest(account, ["alpha"])

        assert sync_skills.account_skill_payload("alpha") == {}
        gap = sync_skills.account_upload_gap(
            {"alpha"}, sync_skills.account_manifest(), verify_ok=False,
            verified_names={"alpha"},
        )
        assert gap.state == "unknown"

    def test_undeclared_skill_on_the_account_is_not_a_gap(self, tmp_path, monkeypatch):
        """The account holding extra skills is verify's business, not this one's."""
        self._mirror(tmp_path, monkeypatch, present=["alpha", "stowaway"])
        gap = sync_skills.account_upload_gap(
            {"alpha"}, sync_skills.account_manifest(), verify_ok=True,
            verified_names={"alpha"},
        )
        assert gap.state == "clean"

    def test_missing_is_always_a_subset_of_the_declaration(self, tmp_path, monkeypatch):
        """The guarantee that keeps the issue from becoming a second declaration."""
        self._mirror(tmp_path, monkeypatch, present=["stowaway"])
        declared = {"alpha", "beta"}
        gap = sync_skills.account_upload_gap(
            declared, sync_skills.account_manifest(), verify_ok=False,
            verified_names=declared,
        )
        assert set(gap.missing) <= declared
        assert "stowaway" not in gap.missing

    def test_stale_mirror_is_unknown_not_clean(self, tmp_path, monkeypatch):
        """A pre-upload snapshot answers 'present' for uploads that never landed.

        Asserted with ``verify_ok=True`` deliberately: a passing verify must
        not rescue a mirror whose freshness could not be established.
        """
        self._mirror(
            tmp_path, monkeypatch, present=["alpha", "beta"],
            age_seconds=sync_skills.MIRROR_MAX_AGE_SECONDS + 60,
        )
        gap = sync_skills.account_upload_gap(
            {"alpha", "beta"}, sync_skills.account_manifest(), verify_ok=True,
            verified_names={"alpha", "beta"},
        )
        assert gap.state == "unknown"
        assert "stale" in gap.reason

    def test_absent_mirror_is_unknown_not_pending(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", tmp_path / "nope")
        gap = sync_skills.account_upload_gap(
            {"alpha"}, sync_skills.account_manifest(), verify_ok=True,
            verified_names={"alpha"},
        )
        assert gap.state == "unknown"
        assert gap.missing == []

    def test_unreadable_declaration_is_unknown_not_clean(self, tmp_path, monkeypatch):
        """declared=None must not compute as an empty backlog."""
        self._mirror(tmp_path, monkeypatch, present=[])
        gap = sync_skills.account_upload_gap(
            None, sync_skills.account_manifest(), verify_ok=True,
            verified_names=set(),
        )
        assert gap.state == "unknown"
        assert "declaration" in gap.reason

    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param("alpha --> beta", id="closes-the-hidden-block"),
            pytest.param("alpha beta", id="space-splits-the-block"),
            pytest.param("../escape", id="path-traversal"),
            pytest.param("a`b", id="backtick"),
            pytest.param("..", id="bare-parent-directory"),
            pytest.param(".", id="bare-current-directory"),
            pytest.param("...", id="all-dots"),
        ],
    )
    def test_illegal_declared_name_is_unknown_not_clean(
        self, tmp_path, monkeypatch, bad
    ):
        """F6. A corrupt declaration must drive no write in any direction."""
        self._mirror(tmp_path, monkeypatch, present=["alpha"])
        gap = sync_skills.account_upload_gap(
            {"alpha", bad}, sync_skills.account_manifest(), verify_ok=True,
            verified_names={"alpha", bad},
        )
        assert gap.state == "unknown"
        assert "not legal skill names" in gap.reason
        assert repr(bad) in gap.reason

    def test_bare_parent_reads_files_from_OUTSIDE_the_mirror(
        self, tmp_path, monkeypatch
    ):
        """Why ".." had to be rejected, pinned at the mechanism.

        The old character class admitted it, and ".." is not a name a
        directory can have — it is the PARENT. account_skill_payload("..")
        resolved to ACCOUNT_SKILLS_DIR/.. and rglob'd the tree ABOVE the
        mirror, so it returned a non-None payload, counted as PRESENT, and a
        declaration containing it computed as CLEAN: the guard produced the
        one verdict it exists to withhold. Asserting the payload first so
        this pins the mechanism, not just the verdict.
        """
        account = tmp_path / "nest" / "account"
        account.mkdir(parents=True)
        (tmp_path / "nest" / "sibling.txt").write_text("outside the mirror\n")
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account)
        write_manifest(account, [])

        # The traversal itself still works - this is a path resolution, not
        # something the name guard can undo. What changed is that the name
        # can no longer reach it through the declaration.
        assert sync_skills.account_skill_payload("..") is not None
        assert not sync_skills.SKILL_NAME_RE.fullmatch("..")

        gap = sync_skills.account_upload_gap(
            {".."}, sync_skills.account_manifest(), verify_ok=True,
            verified_names={".."},
        )
        assert gap.state == "unknown"

    def test_dotted_names_that_are_real_directories_stay_legal(self):
        """The fix must reject only the pure-dot tokens, not every dot."""
        for legal in ("..a", ".hidden", "a.b", "sync-skills", "a_b-c.d"):
            assert sync_skills.SKILL_NAME_RE.fullmatch(legal), legal

    def test_ordinary_skill_names_are_all_legal(self):
        """The guard must not reject the real declaration it ships beside.

        Reads the shipped path EXPLICITLY: the hermetic fixture above
        redirects ACCOUNT_SKILLS_FILE, and this is the one test in the class
        whose entire point is the real file's real contents.
        """
        real = sync_skills.load_account_declaration(REAL_DECLARATION)
        assert real, "the shipped declaration should be readable"
        assert [n for n in real if not sync_skills.SKILL_NAME_RE.fullmatch(n)] == []

    def test_manifest_entry_without_a_local_copy_counts_as_missing(
        self, tmp_path, monkeypatch
    ):
        """Half-written mirror: index refreshed, directory not.

        verify() calls that skill's upload missing (account_skill_payload is
        None), and so does this — the two agree, which is the point.
        """
        self._mirror(
            tmp_path, monkeypatch, present=[], manifest_names=["alpha"]
        )
        gap = sync_skills.account_upload_gap(
            {"alpha"}, sync_skills.account_manifest(), verify_ok=False,
            verified_names={"alpha"},
        )
        assert gap.state == "pending"
        assert gap.missing == ["alpha"]

    def test_local_copy_absent_from_the_manifest_is_still_PRESENT(
        self, tmp_path, monkeypatch
    ):
        """F3(a), the other direction of the old 'lockstep' claim.

        The mirror holds a readable copy but the account's manifest.json does
        not index it. verify() reads that as present (it only ever calls
        ``account_skill_payload``); the old reporter read it as ABSENT and
        named an already-uploaded skill in the tracking issue, every run.
        """
        self._mirror(
            tmp_path, monkeypatch, present=["alpha"], manifest_names=[]
        )
        assert sync_skills.account_skill_payload("alpha") is not None
        assert sync_skills.account_present_names({"alpha"}) == {"alpha"}
        gap = sync_skills.account_upload_gap(
            {"alpha"}, sync_skills.account_manifest(), verify_ok=True,
            verified_names={"alpha"},
        )
        assert gap.state == "clean"
        assert gap.missing == []


class TestPendingBlockDedupe:
    def test_round_trips_the_set(self):
        block = sync_skills.hidden_pending_block(["beta", "alpha"])
        assert sync_skills.extract_reported_pending([block]) == {"alpha", "beta"}

    def test_takes_the_LAST_block_not_the_union(self):
        """The set shrinks as uploads land; a union could only ever grow."""
        texts = [
            sync_skills.hidden_pending_block(["alpha", "beta"]),
            sync_skills.hidden_pending_block(["beta"]),
        ]
        assert sync_skills.extract_reported_pending(texts) == {"beta"}

    def test_no_block_anywhere_is_none_not_empty_set(self):
        """None routes to 'changed'; set() would suppress the first comment."""
        assert sync_skills.extract_reported_pending(["hand-written issue"]) is None

    def test_ignores_non_string_bodies(self):
        assert sync_skills.extract_reported_pending([None, 17]) is None

    def test_a_name_containing_an_arrow_breaks_the_round_trip(self):
        """F6, end to end: why SKILL_NAME_RE exists at all.

        The dedupe channel is an HTML comment delimited by ``-->``. A name
        containing ``-->`` closes the block early, so the block written and
        the set read back out of it are not the same set — and since the
        comparison in report_account_upload_gap can then never be equal, the
        tool would comment on EVERY run, which is exactly the daily-identical
        -comment noise the dedupe exists to prevent.

        This is a CHARACTERISATION test of the un-escaped channel, not a spec
        for it: the fix is upstream refusal (see
        test_illegal_declared_name_is_unknown_not_clean), because these names
        are directory names and one outside [A-Za-z0-9._-] is a broken
        declaration rather than a string to make safe.
        """
        block = sync_skills.hidden_pending_block(["alpha --> beta", "gamma"])
        assert block == "<!-- pending-uploads: alpha --> beta gamma -->"
        read_back = sync_skills.extract_reported_pending([block])

        assert read_back == {"alpha"}
        assert read_back != {"alpha --> beta", "gamma"}
        # ...and the guard upstream is what keeps such a name out of here.
        assert not sync_skills.SKILL_NAME_RE.fullmatch("alpha --> beta")


class TestReportAccountUploadGap:
    """The issue lifecycle. Every gh call is faked; nothing touches a network."""

    REPO = "Adam-S-Daniel/agentskills"

    def _wire(self, monkeypatch, fake):
        monkeypatch.setattr("sync_skills._gh_api", fake)

    def test_pending_with_no_issue_opens_one(self, monkeypatch, capsys):
        fake = FakeGh()
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(gap_pending("alpha", "beta"), repo=self.REPO)

        bodies = fake.created_bodies()
        assert len(bodies) == 1
        assert sync_skills.ACCOUNT_UPLOAD_ISSUE_MARKER in bodies[0]
        assert "`alpha`" in bodies[0] and "`beta`" in bodies[0]
        assert "opened #42" in capsys.readouterr().out

    def test_issue_body_names_the_remedy_and_the_refresh_order(self, monkeypatch):
        """Actionable by someone who did not write it (and did not read SKILL.md)."""
        fake = FakeGh()
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(gap_pending("alpha"), repo=self.REPO)

        body = fake.created_bodies()[0]
        assert "CLAUDE_CODE_SYNC_SKILLS=1 claude -p 'ok'" in body
        assert "--prepare --skill NAME --zip-dir" in body
        # The refresh must come BEFORE the upload command, not merely appear.
        assert body.index("CLAUDE_CODE_SYNC_SKILLS") < body.index("--prepare --skill")

    def test_issue_never_names_a_skill_outside_the_declaration(self, monkeypatch):
        """The issue is derived from the declaration; it is not a second one."""
        fake = FakeGh()
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(gap_pending("alpha"), repo=self.REPO)

        assert "stowaway" not in fake.created_bodies()[0]

    def test_unchanged_set_does_not_re_comment(self, monkeypatch, capsys):
        """A daily identical comment is the noise the alert exists to cut through."""
        fake = FakeGh(issues=[open_issue(pending=["alpha", "beta"])])
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(gap_pending("alpha", "beta"), repo=self.REPO)

        assert fake.writes() == []
        assert "No comment posted" in capsys.readouterr().out

    def test_changed_set_comments(self, monkeypatch):
        fake = FakeGh(issues=[open_issue(pending=["alpha"])])
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(gap_pending("alpha", "beta"), repo=self.REPO)

        bodies = fake.comment_bodies()
        assert len(bodies) == 1
        assert "`beta`" in bodies[0]
        assert sync_skills.hidden_pending_block(["alpha", "beta"]) in bodies[0]
        assert fake.patches() == []

    def test_set_that_shrank_still_counts_as_changed(self, monkeypatch):
        """Two of three landed: real progress, and a union-based dedupe misses it."""
        fake = FakeGh(issues=[open_issue(pending=["alpha", "beta", "gamma"])])
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(gap_pending("gamma"), repo=self.REPO)

        assert len(fake.comment_bodies()) == 1

    def test_latest_comment_wins_over_the_issue_body(self, monkeypatch):
        fake = FakeGh(
            issues=[open_issue(number=7, pending=["alpha", "beta"])],
            comments={7: [{"body": sync_skills.hidden_pending_block(["beta"])}]},
        )
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(gap_pending("beta"), repo=self.REPO)

        assert fake.writes() == []

    def test_clean_closes_the_open_issue(self, monkeypatch, capsys):
        fake = FakeGh(issues=[open_issue(number=7, pending=["alpha"])])
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(
            sync_skills.UploadGap("clean", [], "done"), repo=self.REPO
        )

        assert len(fake.comment_bodies()) == 1
        patches = fake.patches()
        assert len(patches) == 1
        assert "state=closed" in patches[0][2]
        assert "closed #7" in capsys.readouterr().out

    def test_clean_comments_BEFORE_it_closes(self, monkeypatch):
        """A closed issue with no explanation is a state someone reconstructs later."""
        fake = FakeGh(issues=[open_issue(number=7)])
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(
            sync_skills.UploadGap("clean", [], "done"), repo=self.REPO
        )

        kinds = [m for _, m, _ in fake.writes()]
        assert kinds == [None, "PATCH"]

    def test_clean_with_no_issue_writes_nothing(self, monkeypatch):
        fake = FakeGh(issues=[])
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(
            sync_skills.UploadGap("clean", [], "done"), repo=self.REPO
        )

        assert fake.writes() == []

    def test_lookup_uses_the_marker_and_ignores_a_same_titled_issue(self, monkeypatch):
        """Never a title search: a title can be edited, a marker cannot drift."""
        fake = FakeGh(issues=[{"number": 3, "body": "no marker here"}])
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(gap_pending("alpha"), repo=self.REPO)

        assert len(fake.created_bodies()) == 1  # opened fresh, did not adopt #3

    def test_pull_requests_are_filtered_out_of_the_issue_listing(self, monkeypatch):
        """GET /issues returns PRs too; commenting on one is not tracking."""
        pr = {
            "number": 5,
            "body": sync_skills.ACCOUNT_UPLOAD_ISSUE_MARKER,
            "pull_request": {"url": "https://example.test/pull/5"},
        }
        fake = FakeGh(issues=[pr])
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(gap_pending("alpha"), repo=self.REPO)

        assert len(fake.created_bodies()) == 1
        assert not any("/issues/5/" in e for e, _, _ in fake.calls)

    # -- the UNKNOWN arm: no write, in any direction ------------------------

    @pytest.mark.parametrize(
        "issues",
        [
            pytest.param([], id="no-issue-open"),
            pytest.param([open_issue(number=7, pending=["alpha"])], id="issue-open"),
        ],
    )
    def test_unknown_touches_nothing(self, monkeypatch, capsys, issues):
        """#258 verbatim: an unreadable probe is not a clean answer.

        Not merely 'does not close' — it makes no gh call at all, so it can
        neither open a backlog it cannot see nor retire a live one.
        """
        fake = FakeGh(issues=issues)
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(
            sync_skills.UploadGap("unknown", [], "mirror is stale"), repo=self.REPO
        )

        assert fake.calls == []
        err = capsys.readouterr().err
        assert "UNKNOWN, not clean" in err
        assert "mirror is stale" in err

    # -- gh that cannot answer ---------------------------------------------

    def test_failing_gh_never_raises_and_writes_nothing(self, monkeypatch):
        """A lookup that failed is not 'there is no issue' — that opens a duplicate."""
        fake = FakeGh(issues=[open_issue()], fail=True)
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(gap_pending("alpha"), repo=self.REPO)

        assert fake.writes() == []

    def test_unreadable_comments_leave_the_issue_alone(self, monkeypatch, capsys):
        """Cannot tell whether the set changed; the alert is already delivered."""

        def gh(endpoint, method=None, fields=None):
            gh.calls.append((endpoint, method, tuple(fields or ())))
            if "/issues?state=open" in endpoint:
                return json.dumps([open_issue(number=7, pending=["alpha"])])
            if "/comments?" in endpoint:
                return None
            raise AssertionError(f"should not have been called: {endpoint}")

        gh.calls = []
        monkeypatch.setattr("sync_skills._gh_api", gh)

        sync_skills.report_account_upload_gap(gap_pending("beta"), repo=self.REPO)

        assert [c for c in gh.calls if "?" not in c[0]] == []
        assert "comments could not be read" in capsys.readouterr().err

    def test_failed_close_comment_leaves_the_issue_open(self, monkeypatch, capsys):
        def gh(endpoint, method=None, fields=None):
            gh.calls.append((endpoint, method, tuple(fields or ())))
            if "/issues?state=open" in endpoint:
                return json.dumps([open_issue(number=7)])
            return None  # the close comment fails to post

        gh.calls = []
        monkeypatch.setattr("sync_skills._gh_api", gh)

        sync_skills.report_account_upload_gap(
            sync_skills.UploadGap("clean", [], "done"), repo=self.REPO
        )

        assert [c for c in gh.calls if c[1] == "PATCH"] == []
        assert "left issue #7 OPEN" in capsys.readouterr().err

    def test_unresolvable_repo_writes_nothing(self, monkeypatch, capsys):
        fake = FakeGh()
        self._wire(monkeypatch, fake)
        monkeypatch.setattr("sync_skills._self_repo", lambda: None)

        sync_skills.report_account_upload_gap(gap_pending("alpha"), repo=None)

        assert fake.calls == []
        assert "--report-repo OWNER/NAME" in capsys.readouterr().err

    def test_malformed_report_repo_names_the_value_and_writes_nothing(
        self, monkeypatch, capsys
    ):
        """F5 at the reporter: the message points at the cause, not the flag."""
        fake = FakeGh()
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(
            gap_pending("alpha"), repo="bad slug here"
        )

        assert fake.calls == []
        err = capsys.readouterr().err
        assert "'bad slug here'" in err
        assert "not an OWNER/NAME slug" in err

    # -- F2: the marker lookup is paginated ---------------------------------

    def test_marker_issue_on_page_two_is_found_not_duplicated(
        self, monkeypatch, capsys
    ):
        """F2. GitHub returns PRs in /issues and sorts created-descending.

        So an AGEING tracking issue — exactly the one still open and still
        needing an update — falls off page 1 the moment 100 newer open items
        exist. A single un-paginated request then found nothing and opened a
        SECOND tracking issue beside the first.
        """
        filler = [{"number": 1000 + i, "body": "unrelated"} for i in range(100)]
        fake = FakeGh(issues=filler + [open_issue(number=7, pending=["alpha"])])
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(gap_pending("alpha", "beta"),
                                              repo=self.REPO)

        assert fake.created_bodies() == []  # no duplicate opened
        assert len(fake.comment_bodies()) == 1  # it UPDATED the real issue
        pages = [e for e, _, _ in fake.calls if "issues?state=open" in e]
        assert len(pages) == 2  # it actually walked past page 1

    def test_a_failed_page_is_not_read_as_no_issue_exists(self, monkeypatch):
        """A lookup that broke mid-walk must not license opening a duplicate."""
        filler = [{"number": 1000 + i, "body": "unrelated"} for i in range(100)]

        def gh(endpoint, method=None, fields=None):
            gh.calls.append((endpoint, method, tuple(fields or ())))
            if "issues?state=open" in endpoint:
                if endpoint.endswith("page=1"):
                    return json.dumps(filler)
                return None  # page 2 fails
            raise AssertionError(f"should not have been called: {endpoint}")

        gh.calls = []
        monkeypatch.setattr("sync_skills._gh_api", gh)

        sync_skills.report_account_upload_gap(gap_pending("alpha"), repo=self.REPO)

        assert [c for c in gh.calls if "?" not in c[0]] == []

    def test_running_out_of_pages_is_not_read_as_no_issue_exists(
        self, monkeypatch, capsys
    ):
        """'I stopped looking' is not 'it is not there'."""
        fake = FakeGh(issues=[
            {"number": i, "body": "unrelated"}
            for i in range(100 * sync_skills.GH_MAX_PAGES + 50)
        ])
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(gap_pending("alpha"), repo=self.REPO)

        assert fake.writes() == []
        assert "unknown" in capsys.readouterr().err.lower()

    # -- F4: every give-up branch says why ----------------------------------

    def test_gh_exiting_zero_with_a_json_object_notes_and_writes_nothing(
        self, monkeypatch, capsys
    ):
        """F4. `{"message":"Not Found"}` parses fine and is not a list.

        gh exits 0, _gh_api succeeds, _gh_json parses — and the old lookup
        returned (None, False) without a word, under a caller comment that
        claimed '_gh_api already said why on stderr'. It had not: measured
        zero report-issue lines on stderr.
        """
        monkeypatch.setattr(
            "sync_skills._gh_api",
            lambda endpoint, method=None, fields=None: '{"message":"Not Found"}',
        )

        sync_skills.report_account_upload_gap(gap_pending("alpha"), repo=self.REPO)

        err = capsys.readouterr().err
        notes = [line for line in err.splitlines() if "--report-issue" in line]
        assert notes, f"a give-up branch said nothing; stderr was {err!r}"
        assert any("not a list of issues" in line for line in notes)

    def test_a_failed_open_says_the_backlog_is_untracked(self, monkeypatch, capsys):
        """The one state this mode exists to prevent, going unannounced."""

        def gh(endpoint, method=None, fields=None):
            if "issues?state=open" in endpoint:
                return json.dumps([])
            return None  # the POST fails

        monkeypatch.setattr("sync_skills._gh_api", gh)

        sync_skills.report_account_upload_gap(gap_pending("alpha"), repo=self.REPO)

        err = capsys.readouterr().err
        assert "could NOT open a tracking issue" in err
        assert "alpha" in err

    # -- F1: a red verify may never produce a close or an all-clear ---------

    def test_unknown_never_emits_the_all_clear_text(self, monkeypatch, capsys):
        """Stated as text, not just as 'no PATCH'.

        The affirmative comment is half the damage: it tells a reader the
        account store is complete on a run that proved no such thing.
        """
        fake = FakeGh(issues=[open_issue(number=7, pending=["alpha"])])
        self._wire(monkeypatch, fake)

        sync_skills.report_account_upload_gap(
            sync_skills.UploadGap(
                "unknown", [], "--verify did not pass on this run"
            ),
            repo=self.REPO,
        )

        all_clear = "now holds every skill declared"
        assert all_clear in sync_skills.build_upload_close_comment()
        assert not any(
            all_clear in f for _, _, fields in fake.calls for f in fields
        )
        assert fake.calls == []
        assert fake.patches() == []


class TestGhWrapper:
    """_gh_api is the whole safety story: it may never raise, and never leak."""

    def test_missing_gh_binary_returns_none(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise FileNotFoundError("gh")

        monkeypatch.setattr(sync_skills.subprocess, "run", boom)
        assert sync_skills._gh_api("repos/o/r/issues") is None
        assert "could not run gh" in capsys.readouterr().err

    def test_timeout_returns_none(self, monkeypatch):
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="gh", timeout=1)

        monkeypatch.setattr(sync_skills.subprocess, "run", boom)
        assert sync_skills._gh_api("repos/o/r/issues") is None

    def test_non_zero_exit_echoes_only_the_first_stderr_line(self, monkeypatch, capsys):
        """gh's later stderr lines can quote the request; keep them out of the log."""

        class Result:
            returncode = 1
            stdout = ""
            stderr = "gh: HTTP 401\nAuthorization: Bearer SENTINEL-DO-NOT-ECHO\n"

        monkeypatch.setattr(sync_skills.subprocess, "run", lambda *a, **k: Result())
        assert sync_skills._gh_api("repos/o/r/issues") is None
        err = capsys.readouterr().err
        assert "HTTP 401" in err
        assert "SENTINEL-DO-NOT-ECHO" not in err

    def test_unparseable_json_returns_none(self, monkeypatch, capsys):
        monkeypatch.setattr("sync_skills._gh_api", lambda *a, **k: "not json")
        assert sync_skills._gh_json("repos/o/r/issues") is None
        assert "unparseable JSON" in capsys.readouterr().err

    def test_gh_is_invoked_as_an_argv_list_never_a_shell_string(self, monkeypatch):
        """No shell: a skill name reaching a shell string would be an injection."""
        seen = {}

        class Result:
            returncode = 0
            stdout = "{}"
            stderr = ""

        def fake_run(args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs
            return Result()

        monkeypatch.setattr(sync_skills.subprocess, "run", fake_run)
        sync_skills._gh_api("repos/o/r/issues", method="PATCH", fields=["state=closed"])

        assert seen["args"] == [
            "gh", "api", "repos/o/r/issues", "-X", "PATCH", "-f", "state=closed",
        ]
        assert not seen["kwargs"].get("shell", False)


class TestGithubSlug:
    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:Adam-S-Daniel/agentskills.git",
            "https://github.com/Adam-S-Daniel/agentskills.git",
            "https://github.com/Adam-S-Daniel/agentskills",
            "ssh://git@github.com/Adam-S-Daniel/agentskills.git",
        ],
    )
    def test_parses_every_remote_form_this_clone_can_carry(self, url):
        assert sync_skills.github_slug_from_remote(url) == "Adam-S-Daniel/agentskills"

    @pytest.mark.parametrize("url", [None, "", "https://gitlab.com/o/r.git", "garbage"])
    def test_non_github_remote_is_none(self, url):
        assert sync_skills.github_slug_from_remote(url) is None

    def test_explicit_repo_wins_and_is_shape_checked(self, monkeypatch):
        monkeypatch.setattr("sync_skills._self_repo", lambda: None)
        assert sync_skills.resolve_report_repo("owner/name") == ("owner/name", None)
        repo, why = sync_skills.resolve_report_repo("not-a-slug")
        assert repo is None
        assert why

    def test_a_malformed_explicit_value_is_not_diagnosed_as_a_missing_origin(self):
        """F5. The old message told the operator to pass the flag they passed.

        Both failures returned a bare None, so the caller could only print one
        sentence for two causes, and it named the wrong one whenever
        --report-repo was present and malformed.
        """
        repo, why = sync_skills.resolve_report_repo("bad slug here")
        assert repo is None
        assert "'bad slug here'" in why
        assert "OWNER/NAME slug" in why
        assert "origin" not in why.split("fall back")[0]

    def test_a_malformed_explicit_value_NEVER_falls_back_to_origin(self, monkeypatch):
        """The safety property the message fix must not cost.

        Retargeting a WRITE to a repo the operator did not name is worse than
        not writing: asserted by making origin resolve to a real, valid slug
        and proving it is not returned.
        """
        called = []

        def fake_git(args, cwd, timeout=None):
            called.append(args)
            return "git@github.com:someone-else/their-repo.git"

        monkeypatch.setattr("sync_skills._git", fake_git)
        monkeypatch.setattr("sync_skills._self_repo", lambda: Path("/tmp"))

        repo, why = sync_skills.resolve_report_repo("bad slug here")

        assert repo is None
        assert "someone-else" not in (why or "")
        assert called == []  # origin was never even consulted

    def test_no_origin_remote_says_origin_not_bad_flag(self, monkeypatch):
        monkeypatch.setattr("sync_skills._self_repo", lambda: Path("/tmp"))
        monkeypatch.setattr("sync_skills._git", lambda *a, **k: None)
        repo, why = sync_skills.resolve_report_repo(None)
        assert repo is None
        assert "origin" in why

    @pytest.mark.parametrize(
        "slug",
        [
            pytest.param("owner/name?state=all", id="query-string"),
            pytest.param("o/n#frag", id="fragment"),
            pytest.param("../x", id="dot-dot-owner"),
            pytest.param("-oops/x", id="hyphen-leading-owner"),
            pytest.param("o/..", id="dot-dot-repo"),
        ],
    )
    def test_url_significant_characters_are_not_a_usable_target(self, slug):
        """The slug is interpolated into f"repos/{target}/issues" un-quoted.

        None of these could retarget the OWNER (that needs a "/" in the first
        component, which is forbidden either way) and "?"/"#" truncate the
        path back to a non-write endpoint, so this is hardening rather than a
        live hole. It is here because "a write must go where you said or
        nowhere" should be a property the validator enforces, not one the
        endpoint's shape happens to preserve.
        """
        repo, why = sync_skills.resolve_report_repo(slug)
        assert repo is None
        assert why

    def test_real_slugs_still_resolve(self):
        for good in ("Adam-S-Daniel/agentskills", "o/r", "a/b.c_d-e"):
            assert sync_skills.resolve_report_repo(good)[0] == good

    def test_an_unusable_origin_slug_is_refused_too(self, monkeypatch):
        """Same validator on both arms; a remote URL is operator data too."""
        monkeypatch.setattr("sync_skills._self_repo", lambda: Path("/tmp"))
        monkeypatch.setattr("sync_skills._git", lambda *a, **k: "x")
        monkeypatch.setattr(
            "sync_skills.github_slug_from_remote", lambda url: "-bad/name"
        )
        repo, why = sync_skills.resolve_report_repo(None)
        assert repo is None
        assert "not a usable OWNER/NAME slug" in why


class TestDeclarationProvenance:
    """Is the declaration on disk the one the repo committed?

    Stubbed at ``_git`` like the slug tests above, not driven against a real
    ``git init``: the suite must stay runnable (and identical) on a box with
    no git, which is also how every other git-touching path here is tested.
    """

    def _wire(self, monkeypatch, tmp_path, answers):
        repo = tmp_path / "repo"
        (repo / "plugins").mkdir(parents=True)
        decl = repo / "account-skills.txt"
        decl.write_text("alpha\n", encoding="utf-8")
        monkeypatch.setattr("sync_skills._self_repo", lambda: repo)
        calls = []

        def fake_git(args, cwd, timeout=None):
            calls.append(list(args))
            for key, value in answers.items():
                if key in args:
                    return value
            return None

        monkeypatch.setattr("sync_skills._git", fake_git)
        return decl, calls

    def test_a_locally_modified_declaration_is_named(self, monkeypatch, tmp_path):
        decl, calls = self._wire(
            monkeypatch, tmp_path,
            {"ls-files": "account-skills.txt", "status": " M account-skills.txt"},
        )
        why = sync_skills.declaration_differs_from_committed(decl)
        assert why and "uncommitted" in why
        assert any("ls-files" in c for c in calls)

    def test_a_clean_declaration_passes(self, monkeypatch, tmp_path):
        decl, _ = self._wire(
            monkeypatch, tmp_path, {"ls-files": "account-skills.txt", "status": ""}
        )
        assert sync_skills.declaration_differs_from_committed(decl) is None

    def test_an_untracked_file_is_not_reported_as_modified(
        self, monkeypatch, tmp_path
    ):
        """No committed version to differ FROM - `status` would say `??`.

        This is the branch the whole test suite runs on: every fixture points
        ACCOUNT_SKILLS_FILE at a tmp file. Reading `??` as "locally modified"
        would make the guard fire on every test and on any operator running
        from a tarball.
        """
        decl, calls = self._wire(
            monkeypatch, tmp_path, {"status": "?? account-skills.txt"}
        )  # ls-files answers None -> untracked
        assert sync_skills.declaration_differs_from_committed(decl) is None
        assert not any("status" in c for c in calls), (
            "tracked-ness must be settled before status is consulted"
        )

    def test_a_path_outside_the_checkout_is_not_guessed_at(
        self, monkeypatch, tmp_path
    ):
        self._wire(monkeypatch, tmp_path, {"ls-files": "x", "status": " M x"})
        outside = tmp_path / "elsewhere.txt"
        outside.write_text("alpha\n", encoding="utf-8")
        assert sync_skills.declaration_differs_from_committed(outside) is None

    def test_not_a_checkout_at_all_is_not_guessed_at(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sync_skills._self_repo", lambda: None)
        decl = tmp_path / "account-skills.txt"
        decl.write_text("alpha\n", encoding="utf-8")
        assert sync_skills.declaration_differs_from_committed(decl) is None

    def test_the_real_git_path_runs_without_raising(self):
        """Smoke: the un-stubbed call, against the real file and real git.

        Deliberately asserts the TYPE, not the verdict. Asserting "clean"
        would make the suite fail whenever a contributor has account-skills.txt
        edited but not yet committed - which is a normal moment in the middle
        of the very membership change this file exists to gate, and is a fact
        about the working tree rather than about the code.
        """
        result = sync_skills.declaration_differs_from_committed()
        assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# --report-issue end to end through main()
#
# Driven in-process (argv + monkeypatch) rather than as a subprocess, because
# the load-bearing assertions are about what gh was and was NOT asked to do,
# and a subprocess cannot be asked that without a real gh on PATH.
# ---------------------------------------------------------------------------

def tmp_path_of(wired):
    """The tmp dir the ``wired`` fixture was built in."""
    return wired["repo"].parent


def run_main(argv, monkeypatch):
    """Run main() with argv, returning its exit code."""
    monkeypatch.setattr(sys, "argv", ["sync_skills.py", *argv])
    with pytest.raises(SystemExit) as excinfo:
        sync_skills.main()
    return excinfo.value.code


class TestReportIssueCli:
    @pytest.fixture()
    def wired(self, tmp_path, monkeypatch):
        """A repo with skill-a, an account mirror, and a declaration for both."""
        account = tmp_path / "account"
        account.mkdir(parents=True)
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", account)

        repo = tmp_path / "repo"
        skill = repo / "skills" / "skill-a"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: skill-a\n---\nbody\n")

        write_manifest(account, [])
        declared = write_declaration(tmp_path / "declared.txt", ["skill-a"])
        # ACCOUNT_SKILLS_FILE, not --account-list: --report-issue refuses that
        # flag (it swaps out the declaration the issue text claims to speak
        # for), so this seam is the only one that exercises the real write
        # path. The suite used to pass --account-list on every one of these
        # runs, which is why the missing guard was invisible from in here.
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_FILE", declared)
        return {
            "account": account, "repo": repo, "skill": skill, "declared": declared,
            "argv": ["--verify", "--all", "--repos", str(repo)],
        }

    def test_flag_is_opt_in_no_gh_call_without_it(self, wired, monkeypatch):
        """Absent the flag, the reporting layer must not run at all.

        Asserted on a RECORDER, not on a stub that raises: main() wraps the
        reporter in a catch-all (so a broken tracker can never fail a sync),
        and that catch-all would swallow an AssertionError raised from inside
        it — leaving this test green against a mode that ran unconditionally.
        """
        fake = FakeGh()
        monkeypatch.setattr("sync_skills._gh_api", fake)

        code = run_main(wired["argv"], monkeypatch)

        assert code == 1  # verify fails: skill-a is declared and not uploaded
        assert fake.calls == []

    def test_flag_requires_verify(self, monkeypatch, capsys):
        """Silently ignoring it would read as 'reported, nothing pending'."""
        code = run_main(["--report-issue"], monkeypatch)
        assert code == 2
        assert "--report-issue requires --verify" in capsys.readouterr().err

    def test_report_repo_alone_is_an_error(self, monkeypatch, capsys):
        code = run_main(["--verify", "--report-repo", "o/r"], monkeypatch)
        assert code == 2
        assert "only means anything with --report-issue" in capsys.readouterr().err

    def test_pending_opens_and_the_exit_code_is_still_verify_s(
        self, wired, monkeypatch
    ):
        fake = FakeGh()
        monkeypatch.setattr("sync_skills._gh_api", fake)

        code = run_main(wired["argv"] + ["--report-issue", "--report-repo", "o/r"],
                        monkeypatch)

        assert code == 1  # verify failed: skill-a is declared and not uploaded
        assert len(fake.created_bodies()) == 1
        assert "`skill-a`" in fake.created_bodies()[0]

    def test_clean_closes_and_verify_still_exits_zero(self, wired, monkeypatch):
        mirror_skill(wired["account"], wired["skill"], "skill-a")
        write_manifest(wired["account"], ["skill-a"])
        fake = FakeGh(issues=[open_issue(number=7, pending=["skill-a"])])
        monkeypatch.setattr("sync_skills._gh_api", fake)

        code = run_main(wired["argv"] + ["--report-issue", "--report-repo", "o/r"],
                        monkeypatch)

        assert code == 0
        assert len(fake.patches()) == 1

    def test_failing_gh_does_not_change_the_exit_code(self, wired, monkeypatch):
        """The point of the whole mode: a broken tracker breaks nothing else."""
        mirror_skill(wired["account"], wired["skill"], "skill-a")
        write_manifest(wired["account"], ["skill-a"])
        monkeypatch.setattr("sync_skills._gh_api", FakeGh(fail=True))

        code = run_main(wired["argv"] + ["--report-issue", "--report-repo", "o/r"],
                        monkeypatch)

        assert code == 0  # verify passed; the failed report must not flip it

    def test_a_raising_reporter_does_not_change_the_exit_code(
        self, wired, monkeypatch, capsys
    ):
        """Belt and braces: even an unexpected exception is contained."""
        mirror_skill(wired["account"], wired["skill"], "skill-a")
        write_manifest(wired["account"], ["skill-a"])

        def boom(*a, **k):
            raise RuntimeError("unexpected")

        monkeypatch.setattr("sync_skills.report_account_upload_gap", boom)

        code = run_main(wired["argv"] + ["--report-issue", "--report-repo", "o/r"],
                        monkeypatch)

        assert code == 0
        assert "--report-issue failed (RuntimeError" in capsys.readouterr().err

    def test_stale_mirror_reports_unknown_and_touches_nothing(
        self, wired, monkeypatch, capsys
    ):
        """The arm most likely to be silently wrong, driven end to end."""
        mirror_skill(wired["account"], wired["skill"], "skill-a")
        write_manifest(
            wired["account"], ["skill-a"],
            age_seconds=sync_skills.MIRROR_MAX_AGE_SECONDS + 60,
        )
        fake = FakeGh(issues=[open_issue(number=7, pending=["skill-a"])])
        monkeypatch.setattr("sync_skills._gh_api", fake)

        code = run_main(wired["argv"] + ["--report-issue", "--report-repo", "o/r"],
                        monkeypatch)

        assert code == 1  # verify already fails on a stale mirror
        assert fake.calls == []
        assert "UNKNOWN, not clean" in capsys.readouterr().err

    # -- F1 end to end: a red --verify may never close or post an all-clear --

    def _assert_no_write_and_no_all_clear(self, fake, err):
        # `err` is passed in, not re-read: capsys.readouterr() DRAINS the
        # buffer, so a helper that reads it a second time silently asserts
        # against an empty string and passes on anything.
        assert fake.calls == [], f"gh was called: {fake.calls}"
        assert fake.patches() == []
        assert "UNKNOWN, not clean" in err
        assert "--verify did not pass" in err
        assert "now holds every skill declared" not in err

    def test_drift_run_closes_nothing_and_claims_nothing(
        self, wired, monkeypatch, capsys
    ):
        """F1, the reproduced case: DRIFT on skill-a, --verify exits 1.

        Every declared name is PRESENT, so absence-only logic computed
        'clean', printed 'closed #7 ... account store complete.' and posted
        'The claude.ai account store now holds every skill declared ...' — on
        the same run whose own output read
        'DRIFT skill-a () content differs: SKILL.md'.
        """
        mirror_skill(wired["account"], wired["skill"], "skill-a")
        (wired["account"] / "skill-a" / "SKILL.md").write_text(
            "---\nname: skill-a\n---\nSTALE UPLOAD\n"
        )
        write_manifest(wired["account"], ["skill-a"])
        fake = FakeGh(issues=[open_issue(number=7, pending=["skill-a"])])
        monkeypatch.setattr("sync_skills._gh_api", fake)

        code = run_main(wired["argv"] + ["--report-issue", "--report-repo", "o/r"],
                        monkeypatch)

        assert code == 1
        captured = capsys.readouterr()
        assert "DRIFT" in captured.out
        self._assert_no_write_and_no_all_clear(fake, captured.err)

    def test_empty_account_directory_closes_nothing_and_claims_nothing(
        self, wired, monkeypatch, capsys
    ):
        """F3(b), the second close-while-red route, independent of drift.

        ``account_skill_payload`` returns ``{}`` for an empty directory, and
        ``{} is not None``, so the skill counted as PRESENT and the gap
        computed 'clean' — while verify reported MISMATCH and exited 1.
        """
        (wired["account"] / "skill-a").mkdir()
        write_manifest(wired["account"], ["skill-a"])
        fake = FakeGh(issues=[open_issue(number=7, pending=["skill-a"])])
        monkeypatch.setattr("sync_skills._gh_api", fake)

        code = run_main(wired["argv"] + ["--report-issue", "--report-repo", "o/r"],
                        monkeypatch)

        assert code == 1
        captured = capsys.readouterr()
        assert "MISMATCH" in captured.out
        self._assert_no_write_and_no_all_clear(fake, captured.err)

    # -- F7: the write may not be broader than the verify it reports on -----

    def test_narrowing_verify_to_one_skill_refuses_the_repo_wide_write(
        self, wired, monkeypatch, capsys
    ):
        """F7. `--verify --skill sync-skills --report-issue` CLOSED the issue.

        The gap is computed across the whole declaration, so a per-skill
        verify cannot license a repo-wide open/comment/close. Refused rather
        than silently widened (the write would exceed the request) or
        silently narrowed (a per-skill backlog is not a thing one shared
        tracking issue can represent).
        """
        fake = FakeGh(issues=[open_issue(number=7, pending=["skill-a"])])
        monkeypatch.setattr("sync_skills._gh_api", fake)

        code = run_main(
            ["--verify", "--skill", "skill-a", "--repos", str(wired["repo"]),
             "--report-issue", "--report-repo", "o/r"],
            monkeypatch,
        )

        assert code == 2
        assert fake.calls == []
        assert "--report-issue requires --all" in capsys.readouterr().err

    def test_default_changed_selection_also_refuses(self, wired, monkeypatch, capsys):
        """The git-changed default is a subset too, and reads like 'everything'."""
        fake = FakeGh()
        monkeypatch.setattr("sync_skills._gh_api", fake)

        code = run_main(
            ["--verify", "--repos", str(wired["repo"]),
             "--report-issue", "--report-repo", "o/r"],
            monkeypatch,
        )

        assert code == 2
        assert fake.calls == []
        assert "--report-issue requires --all" in capsys.readouterr().err

    def test_the_documented_invocation_is_the_one_that_is_allowed(
        self, wired, monkeypatch
    ):
        """--verify --all --report-issue: what SKILL.md and the issue body say."""
        fake = FakeGh()
        monkeypatch.setattr("sync_skills._gh_api", fake)

        code = run_main(wired["argv"] + ["--report-issue", "--report-repo", "o/r"],
                        monkeypatch)

        assert code == 1  # skill-a is declared and not uploaded
        assert len(fake.created_bodies()) == 1

    # -- the write must speak for the COMMITTED declaration ----------------

    def test_report_issue_refuses_account_list(self, wired, monkeypatch, capsys):
        """--account-list REPLACES the basis of the answer, and was ungated.

        --skill was refused for narrowing the verify; --account-list swaps
        the whole declaration out, which is the larger substitution, and its
        own --help advertises it as a way to dry-run a proposed membership
        change. Reproduced before the guard: `--verify --all --report-issue
        --account-list <file declaring only what the mirror happens to hold>`
        CLOSED the live tracking issue and posted "the account store now
        holds every skill declared in `account-skills.txt`" - naming a file
        that run never opened, and which really declared ten skills of which
        nine were absent.
        """
        other = write_declaration(tmp_path_of(wired) / "proposed.txt", ["skill-a"])
        fake = FakeGh(issues=[open_issue(number=7, pending=["skill-a"])])
        monkeypatch.setattr("sync_skills._gh_api", fake)

        code = run_main(
            wired["argv"] + ["--report-issue", "--report-repo", "o/r",
                             "--account-list", str(other)],
            monkeypatch,
        )

        assert code == 2
        assert fake.calls == []
        err = capsys.readouterr().err
        assert "cannot be combined with --account-list" in err

    def test_an_EMPTY_account_list_cannot_close_anything(
        self, wired, monkeypatch, capsys
    ):
        """The minimal repro: a zero-name list made every gate pass vacuously.

        Belt and braces on top of the flag refusal - the guard that stops it
        is the parser, and this asserts the OUTCOME (no write) rather than
        the mechanism, so moving the refusal elsewhere still has to keep it.
        """
        empty = write_declaration(tmp_path_of(wired) / "empty.txt", [])
        fake = FakeGh(issues=[open_issue(number=7, pending=["skill-a"])])
        monkeypatch.setattr("sync_skills._gh_api", fake)

        run_main(
            wired["argv"] + ["--report-issue", "--report-repo", "o/r",
                             "--account-list", str(empty)],
            monkeypatch,
        )

        assert fake.calls == []
        assert "now holds every skill declared" not in capsys.readouterr().out

    def test_account_list_alone_is_still_the_documented_dry_run(
        self, wired, monkeypatch, capsys
    ):
        """The refusal must not cost the use case --account-list exists for."""
        other = write_declaration(tmp_path_of(wired) / "proposed.txt", [])
        code = run_main(
            wired["argv"] + ["--account-list", str(other)], monkeypatch
        )
        assert code == 0            # nothing declared, so nothing to upload
        assert "cannot be combined" not in capsys.readouterr().err

    def test_an_empty_shipped_declaration_closes_nothing(
        self, wired, monkeypatch, capsys
    ):
        """Same vacuous CLEAN reached through the shipped file, no flags."""
        write_declaration(wired["declared"], [])
        fake = FakeGh(issues=[open_issue(number=7, pending=["skill-a"])])
        monkeypatch.setattr("sync_skills._gh_api", fake)

        run_main(wired["argv"] + ["--report-issue", "--report-repo", "o/r"],
                 monkeypatch)

        assert fake.patches() == []
        out, err = capsys.readouterr()
        assert "now holds every skill declared" not in out
        assert "EMPTY" in err

    def test_a_declared_skill_no_repo_carries_closes_nothing(
        self, wired, monkeypatch, capsys
    ):
        """verify() returns True having opened no account copy at all.

        skill-b is declared and sits on the account; no resolved repo carries
        it, so verify never reaches it and passes over zero comparisons. The
        account copy could hold anything.
        """
        write_declaration(wired["declared"], ["skill-a", "skill-b"])
        for name in ("skill-a", "skill-b"):
            (wired["account"] / name).mkdir(parents=True, exist_ok=True)
        # skill-a's account copy matches the repo, so absence is not the story.
        (wired["account"] / "skill-a" / "SKILL.md").write_text(
            (wired["skill"] / "SKILL.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (wired["account"] / "skill-b" / "SKILL.md").write_text("anything\n")
        write_manifest(wired["account"], ["skill-a", "skill-b"])
        fake = FakeGh(issues=[open_issue(number=7, pending=["skill-b"])])
        monkeypatch.setattr("sync_skills._gh_api", fake)

        code = run_main(wired["argv"] + ["--report-issue", "--report-repo", "o/r"],
                        monkeypatch)

        assert code == 0                      # verify itself passed
        assert fake.patches() == []           # and still closed nothing
        out, err = capsys.readouterr()
        assert "now holds every skill declared" not in out
        assert "never compared" in err

    def test_a_malformed_declaration_drives_no_write_end_to_end(
        self, wired, tmp_path, monkeypatch, capsys
    ):
        """F6 through main(): the corrupt name never reaches a gh call."""
        bad = write_declaration(tmp_path / "bad.txt", ["skill-a --> evil"])
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_FILE", bad)
        fake = FakeGh(issues=[open_issue(number=7, pending=["skill-a"])])
        monkeypatch.setattr("sync_skills._gh_api", fake)

        code = run_main(
            wired["argv"] + ["--report-issue", "--report-repo", "o/r"],
            monkeypatch,
        )

        assert fake.calls == []
        err = capsys.readouterr().err
        assert "not legal skill names" in err
        assert code in (0, 1)  # verify's own verdict, whatever it is


# ---------------------------------------------------------------------------
# The shipped SKILL.md must not overstate what this mode has been tested on
# ---------------------------------------------------------------------------

class TestReportIssueDocsAreHonest:
    """SKILL.md is the only place a reader learns what is UNVERIFIED here.

    Every test of --report-issue stubs gh — monkeypatched ``_gh_api`` in this
    file, a recording script on PATH by hand. That is the right call for a
    suite that must never write to a real repo, and it means gh's actual
    acceptance of these calls is untested. A doc that omits that reads as
    coverage the code does not have, so the omission is a defect and this
    pins it.
    """

    SKILL_MD = Path(__file__).parent.parent / "SKILL.md"

    def _text(self):
        return self.SKILL_MD.read_text(encoding="utf-8")

    def test_it_names_the_stub_gh_limitation(self):
        text = self._text()
        assert "not* been tested against the real GitHub API" in text, (
            "SKILL.md must carry the section that says which --report-issue "
            "behaviours have only ever run against a stub gh"
        )
        for unverified in ("state_reason", "POST when `-f` fields are present"):
            assert unverified in text, f"{unverified!r} not named as unverified"

    def test_the_three_state_table_says_a_red_verify_closes_nothing(self):
        """The table is the thing an operator reads instead of the code."""
        text = self._text()
        assert "nothing absent **and `--verify` passed**" in text
        assert "nothing absent but `--verify` **failed**" in text

    def test_it_documents_the_scope_guards_on_the_write(self):
        """Both refusals, and both must stay named where an operator reads.

        --all was already documented; --account-list was not, and its absence
        from the docs was the same absence as its absence from the parser.
        """
        text = self._text()
        assert "**`--all` is required, and `--account-list` is refused.**" in text
        assert "refused with `--report-issue`" in text

    def test_it_documents_the_three_vacuous_clean_routes(self):
        """Each closed a reproduced false close; each needs to stay written down."""
        text = self._text()
        for clause in ("empty declaration.", "never compared.",
                       "uncommitted declaration."):
            assert clause in text, f"{clause!r} not documented"



# ---------------------------------------------------------------------------
# Recorded account state (--record-account-state / --account-drift)
# ---------------------------------------------------------------------------


class TestAccountState:
    """The CI-visible half of the account arm.

    CI cannot read ~/.claude/skills/synced, so staleness on a runner is decided
    against a committed recording. These pin the properties that make that
    substitution honest rather than merely convenient.
    """

    @pytest.fixture()
    def registry(self, tmp_path):
        """A fake registry holding two declared skills."""
        repo = tmp_path / "reg"
        for name in ("alpha", "beta"):
            p = repo / "plugins" / "bundle" / "skills" / name
            p.mkdir(parents=True)
            (p / "SKILL.md").write_text(f"---\nname: {name}\n---\nbody\n")
        return repo

    @pytest.fixture()
    def mirror(self, tmp_path, monkeypatch):
        """A fake account mirror; holds `alpha` only."""
        acct = tmp_path / "account"
        (acct / "alpha").mkdir(parents=True)
        (acct / "alpha" / "SKILL.md").write_text("---\nname: alpha\n---\nbody\n")
        (acct / "manifest.json").write_text(json.dumps({
            "lastUpdated": 0,
            "skills": [{"name": "alpha", "updatedAt": "2026-01-01T00:00:00Z"}],
        }))
        monkeypatch.setattr("sync_skills.ACCOUNT_SKILLS_DIR", acct)
        return acct

    def test_digest_is_labelled_like_skills_lock(self):
        d = sync_skills.payload_digest({"SKILL.md": b"x"})
        assert d.startswith("sha256:")
        assert len(d) == len("sha256:") + 64

    def test_digest_ignores_line_endings(self):
        """CRLF is an upload-batch artefact, not a content change."""
        assert (sync_skills.payload_digest({"a": b"one\r\ntwo\r\n"})
                == sync_skills.payload_digest({"a": b"one\ntwo\n"}))

    def test_digest_separates_a_rename_from_a_content_change(self):
        """The length delimiter earns its place here.

        Without it, `{"ab": b"c"}` and `{"a": b"bc"}` concatenate identically
        and a rename would be indistinguishable from an edit.
        """
        assert (sync_skills.payload_digest({"ab": b"c"})
                != sync_skills.payload_digest({"a": b"bc"}))

    def test_absent_from_the_mirror_is_recorded_as_null_not_omitted(self, mirror):
        """"Observed absent" and "never looked at" must stay distinguishable."""
        state = sync_skills.build_account_state(declared={"alpha", "beta"})
        assert state["skills"]["alpha"]["digest"] is not None
        assert state["skills"]["beta"]["digest"] is None
        assert "beta" in state["skills"]

    def test_drift_reports_each_status(self, registry, mirror):
        state = sync_skills.build_account_state(declared={"alpha", "beta"})
        rows = {r["name"]: r for r in sync_skills.account_drift(
            [registry], declared={"alpha", "beta", "gamma"}, state=state)}
        assert rows["alpha"]["status"] == "in-sync"
        assert rows["beta"]["status"] == "never-uploaded"
        assert rows["gamma"]["status"] == "missing-from-registry"

        # Move the registry copy on: that, and only that, is `stale`.
        (registry / "plugins" / "bundle" / "skills" / "alpha"
         / "SKILL.md").write_text("---\nname: alpha\n---\nCHANGED\n")
        rows = {r["name"]: r for r in sync_skills.account_drift(
            [registry], declared={"alpha"}, state=state)}
        assert rows["alpha"]["status"] == "stale"
        assert rows["alpha"]["registry_digest"] != rows["alpha"]["recorded_digest"]

    def test_an_unrecorded_skill_is_offered_not_assumed_clean(
            self, registry, mirror):
        rows = list(sync_skills.account_drift(
            [registry], declared={"alpha"}, state={"skills": {}}))
        assert rows[0]["status"] == "unrecorded"
        assert "alpha" in sync_skills.account_drift_report(rows, None)["needs_upload"]

    def test_no_recording_reads_as_unknown_never_as_in_sync(
            self, registry, mirror, tmp_path):
        """An absent record is the least informed state, not the healthiest."""
        assert sync_skills.load_account_state(tmp_path / "nope.json") is None
        (tmp_path / "junk.json").write_text("{not json")
        assert sync_skills.load_account_state(tmp_path / "junk.json") is None
        rows = sync_skills.account_drift([registry], declared={"alpha"}, state=None)
        assert rows[0]["status"] == "unrecorded"

    def test_the_recording_never_lives_inside_a_skill_it_records(self):
        """Regression: recording inside sync-skills made it eternally stale.

        `zip_skill()` uploads a skill's whole directory, so a recording kept in
        this skill's own folder ships inside this skill - every
        `--record-account-state` would change the skill it had just recorded,
        so `sync-skills` could never once read as in-sync. Caught by simulating
        the workflow, not by review.
        """
        skill_root = Path(sync_skills.__file__).resolve().parent
        if not (skill_root.parents[3] / ".claude-plugin"
                / "marketplace.json").is_file():
            pytest.skip("not a registry checkout; ACCOUNT_STATE_FILE falls back")
        assert skill_root not in sync_skills.ACCOUNT_STATE_FILE.parents
        assert not any("account-state" in member
                       for member in sync_skills.skill_payload(skill_root))
