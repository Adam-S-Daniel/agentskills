#!/usr/bin/env python3
"""generate_readme_table.py — regenerate the plugin/skill table in README.md.

Reads .claude-plugin/marketplace.json and every plugins/*/skills/*/SKILL.md
frontmatter, then rewrites the GitHub-markdown table between the
`<!-- BEGIN GENERATED PLUGIN TABLE -->` / `<!-- END GENERATED PLUGIN TABLE -->`
markers in README.md — one row per skill (Plugin, Invocation, Description).
Everything is derived from marketplace.json + the filesystem; nothing about
plugin names, skill names, or counts is hardcoded.

Usage:
  python3 scripts/generate_readme_table.py            # write README.md
  python3 scripts/generate_readme_table.py --check    # exit 1 if out of date
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PLUGINS_DIR = REPO_ROOT / "plugins"
README_PATH = REPO_ROOT / "README.md"

BEGIN_MARKER = "<!-- BEGIN GENERATED PLUGIN TABLE -->"
END_MARKER = "<!-- END GENERATED PLUGIN TABLE -->"


# ---------------------------------------------------------------------------
# Minimal frontmatter parsing (stdlib only — no PyYAML dependency)
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> Dict[str, str]:
    """Parse a SKILL.md's leading '---' YAML frontmatter into a flat dict.

    Handles the subset of YAML this repo's SKILL.md files actually use:
    plain scalars, quoted scalars, and folded/literal block scalars
    (`>`, `>-`, `|`, `|-`). Nested mappings (e.g. a structured
    `compatibility:` block) are recognised and skipped, not parsed, since
    only `name` and `description` are needed here. Not a general YAML parser.
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
    body = lines[1:end]

    result: Dict[str, str] = {}
    i = 0
    while i < len(body):
        line = body[i]
        if not line.strip() or line[:1] in (" ", "\t"):
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, rest = m.group(1), m.group(2).strip()

        if rest in (">", ">-", "|", "|-"):
            fold = rest[0] == ">"
            block_lines: List[str] = []
            i += 1
            while i < len(body) and (body[i][:1] in (" ", "\t") or not body[i].strip()):
                block_lines.append(body[i])
                i += 1
            indents = [len(l) - len(l.lstrip(" ")) for l in block_lines if l.strip()]
            indent = min(indents) if indents else 0
            dedented = [l[indent:] if len(l) >= indent else l.lstrip() for l in block_lines]
            if fold:
                value = " ".join(l.strip() for l in dedented if l.strip())
            else:
                value = "\n".join(dedented).rstrip("\n")
            result[key] = value.strip()
            continue

        if rest == "":
            # Nested mapping (e.g. a structured `compatibility:` block) — not
            # needed here, skip its indented lines.
            i += 1
            while i < len(body) and (body[i][:1] in (" ", "\t")):
                i += 1
            continue

        if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in "\"'":
            rest = rest[1:-1]
        result[key] = rest
        i += 1

    return result


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


def build_rows() -> List[str]:
    marketplace = json.loads(MARKETPLACE_PATH.read_text(encoding="utf-8"))
    rows: List[str] = []
    for plugin in marketplace.get("plugins", []):
        plugin_name = plugin["name"]
        fallback_desc = plugin.get("description", "")
        for skill_dir in iter_skill_dirs(PLUGINS_DIR / plugin_name):
            frontmatter = parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
            skill_name = skill_dir.name
            description = _first_sentence(frontmatter.get("description") or fallback_desc)
            invocation = f"`/{plugin_name}:{skill_name}`"
            rows.append(f"| `{plugin_name}` | {invocation} | {_one_line(description)} |")
    return rows


def build_table() -> str:
    header = ["| Plugin | Invocation | Description |", "| --- | --- | --- |"]
    return "\n".join(header + build_rows())


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
    new_readme = f"{before}{BEGIN_MARKER}\n{build_table()}\n{END_MARKER}{after}"

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
        print(f"README.md updated ({len(build_rows())} skill rows).")
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
