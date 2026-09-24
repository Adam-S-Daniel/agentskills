"""Repo-wide pytest configuration.

One rule, ADR 0012: nothing under plugins/adam-personal/ is collected.

That plugin's skills/<name> entries are git symlinks to the bundles' own skill
directories. The suite's command (AGENTS.md, both CI pytest jobs) passes the
GLOBS `plugins/*/skills/*/tests/` and `plugins/*/skills/*/scripts/`, which a
shell expands through the links on any checkout that materialises them (Linux,
and Windows runners with symlinks enabled), so every linked skill's tests ran
a second time under a second path — measured on PR #177's first runs. The
links are second NAMES for skills the suite already reaches through their real
directories, so they are skipped here rather than in each command line.

`absolute()`, not `resolve()`: resolving would follow the link to the real
directory and ignore THAT instead.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_LINKED_PLUGIN = ("plugins", "adam-personal")


def is_linked_plugin_path(path) -> bool:
    try:
        rel = Path(str(path)).absolute().relative_to(_ROOT)
    except ValueError:
        return False
    return rel.parts[:2] == _LINKED_PLUGIN


def pytest_ignore_collect(collection_path, config):
    if is_linked_plugin_path(collection_path):
        return True
    return None
