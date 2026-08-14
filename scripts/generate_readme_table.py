#!/usr/bin/env python3
"""generate_readme_table.py — regenerate the plugin/skill table in README.md.

Reads .claude-plugin/marketplace.json and every plugins/*/skills/*/SKILL.md
frontmatter, then rewrites the GitHub-markdown table between the
`<!-- BEGIN GENERATED PLUGIN TABLE -->` / `<!-- END GENERATED PLUGIN TABLE -->`
markers in README.md — one row per skill (Plugin, Invocation, Description).
Everything is derived from marketplace.json + the filesystem; nothing about
plugin names, skill names, or counts is hardcoded.

A FEDERATED bundle (a plugin root in another repo) has no local skills to
enumerate and this script does no network I/O, so it renders as a single row
that NAMES its source repo. Two things that row is not: a fabricated skill
list, and nothing at all. It used to be the latter — a federated entry
contributed zero rows, the rendered table came out byte-identical, `--check`
passed green, and the README never mentioned the bundle. collect_plugin_rows()
now fails instead of letting any published plugin render as silence.

Usage:
  python3 scripts/generate_readme_table.py            # write README.md
  python3 scripts/generate_readme_table.py --check    # exit 1 if out of date
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

# Same directory, so running this as a script puts it on sys.path — but be
# explicit, because pytest and `python -m` do not both agree on that.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# One reader for "what does the marketplace publish", one answer for "is this
# entry local or federated". A second copy of that classification here is
# exactly how the README and the consistency gate would come to disagree about
# which bundles exist.
from check_consistency import classify_source, load_marketplace  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO_ROOT / "plugins"
README_PATH = REPO_ROOT / "README.md"

BEGIN_MARKER = "<!-- BEGIN GENERATED PLUGIN TABLE -->"
END_MARKER = "<!-- END GENERATED PLUGIN TABLE -->"


# ---------------------------------------------------------------------------
# Frontmatter parsing (PyYAML)
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> Dict[str, Any]:
    """Parse a SKILL.md's leading '---' YAML frontmatter into a dict.

    Only the delimiters are located here — finding the leading `---` … `---`
    block is line handling, not format parsing. Everything inside the block
    goes to PyYAML's `yaml.safe_load`, so the whole YAML grammar is supported
    (quoted scalars, folded/literal block scalars, nested mappings, lists)
    rather than the subset a hand-rolled parser happened to cover.

    Returns {} for a file with no frontmatter, an unterminated block, or
    content that does not parse as a mapping.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}

    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        # Swallowed on purpose: this script renders the README table, it does not
        # validate. scripts/check_skills.py owns frontmatter validation and fails
        # the build loudly on a malformed file.
        return {}
    if not isinstance(data, dict):
        return {}

    # PyYAML keeps the trailing newline of a folded/literal block scalar
    # (`>`, `>-`, `|`, `|-`) where the previous parser stripped it — strip so
    # the values callers see are unchanged.
    return {k: v.strip() if isinstance(v, str) else v for k, v in data.items()}


# ---------------------------------------------------------------------------
# Table construction
# ---------------------------------------------------------------------------

def _one_line(text: str) -> str:
    """Collapse to one line and escape pipes for a markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|")


def iter_skill_dirs(plugin_dir: Path) -> List[Path]:
    """Every plugins/<plugin>/skills/<skill>/ dir that has a SKILL.md."""
    skills_dir = plugin_dir / "skills"
    if not skills_dir.is_dir():
        return []
    return sorted(p.parent for p in skills_dir.glob("*/SKILL.md"))


# Abbreviations whose internal '.' must not be mistaken for a sentence
# terminator when scanning for the first real sentence boundary.
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "cf.")


def _first_sentence(text: str) -> str:
    """Collapse whitespace, then truncate to the first sentence: up to and
    including the first '.', '!', or '?' that is followed by whitespace or
    end-of-string, skipping terminators that are actually the trailing '.'
    of a known abbreviation (see _ABBREVIATIONS) so those aren't mistaken
    for sentence ends. Falls back to the whole (collapsed) text if no real
    sentence terminator is found."""
    collapsed = " ".join(text.split())
    for m in re.finditer(r"[.!?](?=\s|$)", collapsed):
        prefix = collapsed[: m.end()]
        if any(prefix.lower().endswith(abbr) for abbr in _ABBREVIATIONS):
            continue
        return prefix
    return collapsed


def _row(plugin_name: str, invocation: str, description: str) -> str:
    return f"| `{plugin_name}` | {invocation} | {_one_line(description)} |"


def local_rows(plugin: dict, plugins_dir: Path) -> List[str]:
    """One row per plugins/<plugin>/skills/<skill>/ that has a SKILL.md."""
    plugin_name = plugin["name"]
    fallback_desc = plugin.get("description", "")
    rows = []
    for skill_dir in iter_skill_dirs(plugins_dir / plugin_name):
        frontmatter = parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
        description = _first_sentence(frontmatter.get("description") or fallback_desc)
        rows.append(_row(plugin_name, f"`/{plugin_name}:{skill_dir.name}`", description))
    return rows


def federated_row(plugin: dict, repo: str) -> str:
    """The single row a federated bundle contributes.

    Its skills live in another repo and this script does no network I/O, so
    the row names the source repo instead of enumerating them — a table that
    invented a skill list would be worse than one that omits it, and a fetch
    here would make the README build depend on the network. The invocation
    cell keeps the `/<bundle>:<skill>` shape so a reader still learns how these
    are called, with `<skill>` left an explicit placeholder.
    """
    plugin_name = plugin["name"]
    invocation = f"`/{plugin_name}:<skill>` — skills live in [{repo}](https://github.com/{repo})"
    return _row(plugin_name, invocation, _first_sentence(plugin.get("description", "")))


def collect_plugin_rows(
    marketplace: Optional[dict] = None, plugins_dir: Path = PLUGINS_DIR
) -> Tuple[List[Tuple[str, List[str]]], List[str]]:
    """[(plugin name, its rows)] in marketplace order, plus a list of problems.

    Grouped per plugin, and with the problems handed back rather than raised,
    so the one property that is easy to lose can be asserted structurally:
    EVERY marketplace plugin contributes at least one row. A plugin that
    renders as nothing is a plugin the README does not mention, and no amount
    of `--check`ing a table against itself notices that.
    """
    if marketplace is None:
        marketplace = load_marketplace()
    by_plugin: List[Tuple[str, List[str]]] = []
    problems: List[str] = []
    for plugin in marketplace.get("plugins", []):
        plugin_name = plugin.get("name")
        kind, detail = classify_source(plugin)
        if kind == "local":
            rows = local_rows(plugin, plugins_dir)
        elif kind == "federated":
            rows = [federated_row(plugin, detail)]
        else:
            problems.append(
                f"marketplace.json entry '{plugin_name}' {detail} — it cannot be "
                "rendered, so the README would silently omit it"
            )
            continue
        if not rows:
            problems.append(
                f"marketplace.json publishes plugin '{plugin_name}' but it renders "
                "no table rows, so README.md would not mention it at all"
            )
        by_plugin.append((plugin_name, rows))
    return by_plugin, problems


def build_rows() -> List[str]:
    by_plugin, problems = collect_plugin_rows()
    if problems:
        sys.exit("ERROR: " + "\n       ".join(problems))
    return [row for _, rows in by_plugin for row in rows]


def build_table(rows: List[str]) -> str:
    header = ["| Plugin | Invocation | Description |", "| --- | --- | --- |"]
    return "\n".join(header + rows)


# ---------------------------------------------------------------------------
# README rewriting
# ---------------------------------------------------------------------------

def render(check: bool) -> int:
    readme = README_PATH.read_text(encoding="utf-8")
    if BEGIN_MARKER not in readme or END_MARKER not in readme:
        sys.exit(
            f"ERROR: README.md is missing the {BEGIN_MARKER} / {END_MARKER} markers"
        )
    before, rest = readme.split(BEGIN_MARKER, 1)
    _, after = rest.split(END_MARKER, 1)
    rows = build_rows()
    new_readme = f"{before}{BEGIN_MARKER}\n{build_table(rows)}\n{END_MARKER}{after}"

    if check:
        if new_readme != readme:
            print(
                "README.md plugin table is out of date. "
                "Run: python3 scripts/generate_readme_table.py",
                file=sys.stderr,
            )
            return 1
        print("README.md plugin table is up to date.")
        return 0

    if new_readme != readme:
        README_PATH.write_text(new_readme, encoding="utf-8")
        print(f"README.md updated ({len(rows)} rows).")
    else:
        print("README.md already up to date.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="Exit 1 if README.md's table is out of date instead of writing it",
    )
    args = parser.parse_args()
    sys.exit(render(args.check))


if __name__ == "__main__":
    main()
