#!/usr/bin/env python3
"""Tests for setup.sh's skill collection (the `# >>> skill-collection` section).

ADR 0012 adds plugins/adam-personal/skills/<name> as git symlinks to the
bundles' own skill directories. On a symlink-capable checkout setup.sh's glob
`plugins/*/skills/*/SKILL.md` follows them, and every account skill would be
linked into the per-agent homes twice under one basename. The section runs as
shipped, extracted from setup.sh, against a throwaway plugins tree — never the
real homes.

Run: python3 -m pytest scripts/test_setup_skill_collection.py -q
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SETUP = REPO / "setup.sh"
OPEN, CLOSE = "# >>> skill-collection\n", "# <<< skill-collection\n"


def section() -> str:
    text = SETUP.read_text(encoding="utf-8")
    start = text.index(OPEN)
    return text[start:text.index(CLOSE, start) + len(CLOSE)]


def _posix_bash():
    """A POSIX bash, never Windows' System32 WSL launcher (the same resolver
    test_generate_skills_lock.py uses)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_generate_skills_lock import BASH
    return shutil.which(BASH) if BASH and os.name != "nt" else BASH


def collect(tmp_path: Path, plugins_dir: Path) -> list:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("no POSIX bash on this machine")
    script = tmp_path / "collect.sh"
    script.write_text(
        "set -u\nPLUGINS_DIR=\"$1\"\n" + section()
        + 'for d in "${SKILL_DIRS[@]}"; do printf "%s\\n" "${d#"$PLUGINS_DIR"/}"; done\n',
        encoding="utf-8", newline="\n")
    proc = subprocess.run([bash, script.as_posix(), plugins_dir.as_posix()],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.splitlines()


def write_skill(plugins_dir: Path, bundle: str, name: str) -> None:
    skill = plugins_dir / bundle / "skills" / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: %s\n---\n" % name, encoding="utf-8")


def test_the_section_markers_are_the_shipped_ones():
    text = SETUP.read_text(encoding="utf-8")
    assert text.count(OPEN) == 1 and text.count(CLOSE) == 1
    assert "SKILL_DIRS+=" in section()


def test_a_symlinked_skill_directory_is_collected_once(tmp_path):
    plugins_dir = tmp_path / "plugins"
    write_skill(plugins_dir, "alpha", "one")
    link = plugins_dir / "personal" / "skills" / "one"
    link.parent.mkdir(parents=True)
    try:
        os.symlink("../../alpha/skills/one", link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create symlinks")
    assert collect(tmp_path, plugins_dir) == ["alpha/skills/one"]


def test_a_link_checked_out_as_a_text_file_is_not_collected(tmp_path):
    # core.symlinks=false (the Windows default) writes the link as a file.
    plugins_dir = tmp_path / "plugins"
    write_skill(plugins_dir, "alpha", "one")
    link = plugins_dir / "personal" / "skills" / "one"
    link.parent.mkdir(parents=True)
    link.write_text("../../alpha/skills/one", encoding="utf-8")
    assert collect(tmp_path, plugins_dir) == ["alpha/skills/one"]
