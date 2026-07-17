#!/usr/bin/env python3
"""check_consistency.py — cross-check marketplace.json against the filesystem.

Verifies, deriving everything from `.claude-plugin/marketplace.json` and the
`plugins/` filesystem layout (nothing about plugin/skill names or counts is
hardcoded):

  - every marketplace.json plugin entry has a matching
    plugins/<name>/.claude-plugin/plugin.json whose "name" matches, and
    every plugins/*/.claude-plugin/plugin.json has a matching marketplace
    entry (both directions);
  - every plugins/*/skills/*/ directory contains a SKILL.md;
  - if marketplace.json has a "renames" map ({old-name: new-name-or-null},
    append-only forever — users may update from any old version), every
    chain of values terminates at an existing plugin entry or at null
    (= plugin removed), with no cycles, no self-mappings, and no key that
    shadows a current plugin name;
  - skill directory basenames are unique across the whole repo, since they
    key setup.sh's per-agent symlinks and claude.ai skill uploads;
  - optionally, that no skill basename collides with one in another repo
    with the same plugins/*/skills/* layout (--private-registry PATH).

Usage:
  python3 scripts/check_consistency.py [--private-registry PATH]
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PLUGINS_DIR = REPO_ROOT / "plugins"


def _skill_basenames(plugins_dir: Path) -> Dict[str, List[str]]:
    """Map skill directory basename -> ['<plugin>/<skill>', ...] locations."""
    locations: Dict[str, List[str]] = {}
    if not plugins_dir.is_dir():
        return locations
    for skill_md in sorted(plugins_dir.glob("*/skills/*/SKILL.md")):
        skill_dir = skill_md.parent
        plugin_name = skill_dir.parent.parent.name
        locations.setdefault(skill_dir.name, []).append(f"{plugin_name}/{skill_dir.name}")
    return locations


def check_marketplace_plugin_json(marketplace: dict, errors: List[str]) -> None:
    marketplace_names = set()
    for entry in marketplace.get("plugins", []):
        name = entry.get("name")
        marketplace_names.add(name)
        plugin_json_path = PLUGINS_DIR / name / ".claude-plugin" / "plugin.json"
        if not plugin_json_path.is_file():
            errors.append(
                f"marketplace.json lists '{name}' but "
                f"{plugin_json_path.relative_to(REPO_ROOT)} does not exist"
            )
            continue
        try:
            plugin_json = json.loads(plugin_json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{plugin_json_path.relative_to(REPO_ROOT)} is not valid JSON: {exc}")
            continue
        if plugin_json.get("name") != name:
            errors.append(
                f"{plugin_json_path.relative_to(REPO_ROOT)} has name "
                f"'{plugin_json.get('name')}', expected '{name}'"
            )

    if not PLUGINS_DIR.is_dir():
        return
    for plugin_json_path in sorted(PLUGINS_DIR.glob("*/.claude-plugin/plugin.json")):
        dir_name = plugin_json_path.parent.parent.name
        if dir_name not in marketplace_names:
            errors.append(
                f"plugins/{dir_name} has a plugin.json but is not listed in marketplace.json"
            )


def check_skill_md_present(errors: List[str]) -> None:
    if not PLUGINS_DIR.is_dir():
        return
    for skill_dir in sorted(PLUGINS_DIR.glob("*/skills/*")):
        if skill_dir.is_dir() and not (skill_dir / "SKILL.md").is_file():
            errors.append(f"{skill_dir.relative_to(REPO_ROOT)} has no SKILL.md")


def check_renames(marketplace: dict, errors: List[str]) -> None:
    """Validate the marketplace "renames" map: {old-name: new-name-or-null}.

    Claude Code (verified against 2.1.211) resolves an installed old plugin
    name by looking it up as a key and following the chain of values until it
    reaches a name that is not itself a key; that terminal name must be a
    current plugins[].name. A null value means "removed". The map is
    append-only forever — users may update from any historical version.
    """
    renames = marketplace.get("renames")
    if renames is None:
        return
    if not isinstance(renames, dict):
        errors.append(
            "marketplace.json 'renames' must be a JSON object mapping "
            "old plugin name -> new plugin name (or null for removed)"
        )
        return
    plugin_names = {entry.get("name") for entry in marketplace.get("plugins", [])}
    for old, new in renames.items():
        if old in plugin_names:
            errors.append(f"renames key '{old}' collides with a current plugin name")
        if new == old:
            errors.append(f"renames entry '{old}' maps to itself")
    for old in renames:
        # Follow the value chain; bounded by the visited set, so cycle-safe.
        seen = {old}
        target = renames[old]
        while target is not None and target in renames:
            if target in seen:
                errors.append(f"renames chain starting at '{old}' contains a cycle")
                break
            seen.add(target)
            target = renames[target]
        else:
            if target is not None and target not in plugin_names:
                errors.append(
                    f"renames chain from '{old}' ends at '{target}', "
                    "which is not a marketplace.json plugin"
                )


def check_unique_skill_basenames(errors: List[str]) -> None:
    for basename, locations in sorted(_skill_basenames(PLUGINS_DIR).items()):
        if len(locations) > 1:
            errors.append(f"skill basename '{basename}' is used in multiple places: {', '.join(locations)}")


def check_private_registry(private_registry: Path, errors: List[str]) -> None:
    if not private_registry.exists():
        print(
            f"SKIP: --private-registry {private_registry} does not exist; "
            "skipping cross-repo skill-basename check"
        )
        return
    local = _skill_basenames(PLUGINS_DIR)
    private = _skill_basenames(private_registry / "plugins")
    for basename in sorted(set(local) & set(private)):
        errors.append(
            f"skill basename '{basename}' collides with the private registry: "
            f"{', '.join(local[basename])} vs {', '.join(private[basename])}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--private-registry", metavar="PATH",
        help="Path to a sibling repo with the same plugins/*/skills/* layout "
             "to check skill-basename collisions against; skipped with a "
             "note if the path doesn't exist",
    )
    args = parser.parse_args()

    if not MARKETPLACE_PATH.is_file():
        sys.exit(f"ERROR: {MARKETPLACE_PATH} not found")
    marketplace = json.loads(MARKETPLACE_PATH.read_text(encoding="utf-8"))

    errors: List[str] = []
    check_marketplace_plugin_json(marketplace, errors)
    check_skill_md_present(errors)
    check_renames(marketplace, errors)
    check_unique_skill_basenames(errors)
    if args.private_registry:
        check_private_registry(Path(args.private_registry), errors)

    if errors:
        print(f"FAILED: {len(errors)} consistency issue(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print("OK: consistency checks passed.")


if __name__ == "__main__":
    main()
