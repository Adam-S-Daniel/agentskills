#!/usr/bin/env python3
"""Tests for the repo-root conftest.py: plugins/adam-personal is never collected.

ADR 0012's account plugin holds git symlinks to other bundles' skill
directories; the suite's globbed command would otherwise run every linked
skill's tests twice.

Run: python3 -m pytest scripts/test_repo_conftest.py -q
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _conftest():
    spec = importlib.util.spec_from_file_location("_repo_conftest", REPO / "conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("rel, ignored", [
    ("plugins/adam-personal/skills/sync-skills/tests/test_sync_skills.py", True),
    ("plugins/adam-personal/skills/rename-pdfs/scripts", True),
    ("plugins/adam-local/skills/sync-skills/tests/test_sync_skills.py", False),
    ("scripts/test_check_consistency.py", False),
])
def test_the_hook_ignores_only_the_linked_plugin(rel, ignored):
    assert _conftest().is_linked_plugin_path(REPO / rel) is ignored


def test_the_suite_collects_nothing_through_the_links():
    """The command CI runs, pointed at a linked skill's tests: nothing may be
    collected. Only meaningful where the checkout materialised the links as
    real symlinks; a core.symlinks=false checkout has no such directory."""
    linked = REPO / "plugins" / "adam-personal" / "skills" / "sync-skills" / "tests"
    if not linked.is_dir():
        pytest.skip("this checkout holds the links as text files, not directories")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "plugins/adam-personal/skills/sync-skills/tests/",
         "plugins/adam-local/skills/sync-skills/tests/"],
        cwd=REPO, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    collected = [line for line in proc.stdout.splitlines() if "::" in line]
    assert collected, "the real directory's tests were not collected either"
    assert not [line for line in collected if "adam-personal" in line], collected
