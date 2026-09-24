#!/usr/bin/env python3
"""check_consistency.py — cross-check marketplace.json against the filesystem.

Verifies, deriving everything from `.claude-plugin/marketplace.json` and the
`plugins/` filesystem layout (nothing about plugin/skill names or counts is
hardcoded):

  - every marketplace.json plugin entry has a non-empty, unique name and a
    `source` this repo knows how to reason about — see classify_source();
  - every LOCAL entry has a matching
    plugins/<name>/.claude-plugin/plugin.json whose "name" matches, and
    every plugins/*/.claude-plugin/plugin.json has a matching marketplace
    entry (both directions);
  - every FEDERATED entry (a plugin root in another repo) is well-formed and
    is not shadowed by a local plugins/<name>/ directory of the same name;
  - every CURATED entry (source "./", the marketplace root, with
    "strict": false — the entry is the plugin's whole definition) lists
    existing plugins/<bundle>/skills/<skill> directories, is opt-in, carries
    a version, carries no key outside CURATED_ENTRY_KEYS, and is not shadowed
    by a plugins/<name>/ directory — and while one exists, no default plugin
    component (CURATED_ROOT_COMPONENTS) sits at the repository root;
  - the account plugin (ACCOUNT_PLUGIN, ADR 0012) lists exactly the skills
    account-skills.txt declares — that file stays the one declaration;
  - every plugins/*/skills/*/ directory contains a SKILL.md;
  - if marketplace.json has a "renames" map ({old-name: new-name-or-null},
    append-only forever — users may update from any old version), every
    value is a string or null, and every chain of values terminates at an
    existing plugin entry or at null (= plugin removed) within the
    resolver's 16-hop depth limit, with no cycles, no self-mappings, and
    no key that shadows a current plugin name;
  - skill directory basenames are unique across the whole repo, since they
    key setup.sh's per-agent symlinks and claude.ai skill uploads;
  - optionally, that no skill basename collides with one in another repo
    with the same plugins/*/skills/* layout (--private-registry PATH).

Usage:
  python3 scripts/check_consistency.py [--private-registry PATH]
"""

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PLUGINS_DIR = REPO_ROOT / "plugins"

# The `source` of a CURATED entry: the marketplace root itself. With
# "strict": false the entry is the plugin's entire definition and its `skills`
# list is the complete set it loads (code.claude.com/docs/en/plugin-marketplaces,
# "list specific subdirectories instead so each entry loads only its own
# skills"). No other root spelling is accepted, so there is one shape to reason
# about rather than several that mean the same thing.
CURATED_SOURCE = "./"

# The one curated entry this repo ships (ADR 0012): the skills the owner's
# claude.ai account carries, served in place from their bundles. Its skill list
# is DERIVED — it must equal the names in ACCOUNT_SKILLS_PATH, which stays the
# single declaration of account membership that sync_skills.py --verify reads.
ACCOUNT_PLUGIN = "adam-personal"

# The display-only keys of a marketplace entry: what users SEE in listings, not
# what the plugin loads (code.claude.com/docs/en/plugin-marketplaces, "Both the
# entry and the plugin's own plugin.json can set the display fields
# displayName, description, author, homepage, repository, license, and
# keywords", plus the marketplace's own `category` and `tags`). A change to
# one of these alone does not change what the plugin delivers, so
# check_plugin_versions.py does not demand a bump for it.
CURATED_DISPLAY_KEYS = frozenset({
    "displayName", "description", "author", "homepage", "repository",
    "license", "keywords", "category", "tags",
})

# Every key a curated entry may carry. CLOSED on purpose: a strict:false entry
# is the plugin's whole definition, so `hooks`, `mcpServers`, `lspServers`,
# `agents`, `commands`, `workflows`, `outputStyles`, `experimental`,
# `userConfig`, `channels`, `dependencies`, `settings` ... added here would
# become part of the account plugin — code that runs on every surface the
# account reaches — with no check of this repo's looking at it. An allowlist,
# not a denylist, so a component field the docs add next year is refused too.
CURATED_ENTRY_KEYS = CURATED_DISPLAY_KEYS | {
    "name", "source", "strict", "version", "defaultEnabled", "skills",
}

# Default component locations at a plugin root
# (code.claude.com/docs/en/plugins-reference, "File locations reference", plus
# the root SKILL.md single-skill layout). A curated entry's plugin root IS the
# repository root. The docs give `skills` a marketplace-root exception; they do
# not say strict:false suppresses default discovery of anything else. So none
# of these may exist at the repo root while a curated entry does, or it would
# join the account plugin silently.
CURATED_ROOT_COMPONENTS = (
    ".claude-plugin/plugin.json",
    "SKILL.md",
    "skills",
    "commands",
    "agents",
    "workflows",
    "output-styles",
    "themes",
    "hooks",
    "monitors",
    "bin",
    ".mcp.json",
    ".lsp.json",
    "settings.json",
)
SYNC_SKILLS_DIR = PLUGINS_DIR / "adam-local" / "skills" / "sync-skills"
ACCOUNT_SKILLS_PATH = SYNC_SKILLS_DIR / "account-skills.txt"

# A federated entry's repo, spelled the way GitHub spells it: OWNER/REPO, each
# half starting with an alphanumeric. Anchored on purpose so the near-misses
# that would otherwise read as "close enough" are rejected outright:
# "https://github.com/o/r" (a URL), "o/r/skills" (a subpath), "o/" and "/r".
GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")

# Every key a federated `source` object may carry. Anything else is an ERROR
# rather than ignored — the same rule, and the same reasoning, as
# generate_skills_lock.py's normalize_source(): a key that reads as a pin while
# nothing consumes it is worse than no key, because the one place a reader
# looks to find out whether a remote bundle is pinned would then answer
# falsely.
#
# Why exactly these two, checked against the CLI's own plugin-source schema
# (Claude Code 2.1.231; CI pins 2.1.223) rather than assumed:
#   * the `github` variant of a marketplace PLUGIN source declares `repo`, plus
#     optional `ref` and `sha` — and nothing else;
#   * `path` is NOT part of it. It belongs to the separate MARKETPLACE source
#     schema (where marketplace.json sits inside a repo), and `version` belongs
#     to the `npm`/`pip` plugin variants. On a github plugin source, `path`,
#     `version`, `commit` and `branch` are not declared at all — zod strips
#     them silently, so they are pure decoration;
#   * `ref`/`sha` ARE declared, so they may well be honoured — but this repo
#     still refuses them, as POLICY rather than as a claim about the CLI:
#     pinning a federated bundle to a revision is skills.lock's job, where the
#     pin is an immutable commit with a sha256 per skill that this repo can
#     verify. A marketplace `ref` is verified by nothing here.
FEDERATED_SOURCE_FIELDS = ("source", "repo")


def load_marketplace(path: Path = MARKETPLACE_PATH) -> dict:
    """Parse marketplace.json.

    Shared with the other scripts that must reason about the same entries
    (generate_readme_table.py, check_agent_plugins.py) so "what does the
    marketplace publish" has exactly one reader and one answer.
    """
    return json.loads(path.read_text(encoding="utf-8"))


def classify_source(entry: dict) -> Tuple[str, str]:
    """Classify one marketplace entry's `source` — what decides what is
    checkable OFFLINE. Returns (kind, detail):

      ("local",     "<path>")       A path inside this repo. The plugin root
                                    is on disk, so its manifest can be read
                                    and cross-checked.
      ("federated", "OWNER/REPO")   A plugin root that lives in ANOTHER repo,
                                    which keeps its skills, its cadence and
                                    its review path there. Measured against
                                    Claude Code 2.1.223: `claude plugin
                                    validate . --strict` accepts such an entry
                                    WITHOUT resolving or fetching it, and a
                                    source object accepts extra keys in
                                    silence — ref/commit/version/branch/path
                                    all "validate", including the ones its own
                                    schema does not define. So validation
                                    asserts nothing whatsoever about a
                                    federated entry — every guarantee has to
                                    be made right here, and that includes the
                                    key set: a source carrying anything
                                    outside FEDERATED_SOURCE_FIELDS is
                                    invalid, not federated. See that constant
                                    for why the set is exactly {source, repo}.
      ("curated",   "./")           The marketplace root (CURATED_SOURCE). Not
                                    a bundle: there is no plugins/<name>/ and
                                    no manifest; the entry's `skills` list,
                                    under "strict": false, is the plugin.
                                    Kept apart from "local" so no caller treats
                                    the repo root as a bundle directory — see
                                    _check_curated_entry().
      ("invalid",   "<reason>")     Anything else, phrased as a sentence
                                    fragment to follow the entry's name. An
                                    entry nobody can classify must FAIL rather
                                    than fall past a checked branch: a silent
                                    skip is the failure mode this pass exists
                                    to remove.

    Deliberately NOT a schema check of the whole entry — Claude Code owns that,
    and `claude plugin validate` runs alongside this script in CI. This answers
    the one question that script cannot: local or remote.
    """
    if "source" not in entry:
        return ("invalid", "has no 'source'")
    source = entry["source"]
    if source == CURATED_SOURCE:
        return ("curated", source)
    if isinstance(source, str):
        return ("local", source)
    if not isinstance(source, dict):
        return (
            "invalid",
            f"has a 'source' of type {type(source).__name__}; expected a "
            'local "./path" string or a {"source": "github", "repo": '
            '"OWNER/REPO"} object',
        )
    kind = source.get("source")
    if kind != "github":
        return (
            "invalid",
            f"has source.source {kind!r}; this repo federates only from GitHub "
            '("source": "github")',
        )
    # Shape before value: an entry may carry a perfectly well-formed `repo` and
    # still be lying about being pinned, which is the more dangerous of the two.
    unknown = sorted(set(source) - set(FEDERATED_SOURCE_FIELDS))
    if unknown:
        return (
            "invalid",
            f"has unknown key(s) {', '.join(repr(key) for key in unknown)} on its "
            f"'source'; a federated source carries exactly "
            f"{', '.join(FEDERATED_SOURCE_FIELDS)} — a key like 'ref'/'commit'/"
            "'version' here reads as a pin without being one, and pinning a "
            "federated bundle to a revision is skills.lock's job",
        )
    repo = source.get("repo")
    if not isinstance(repo, str) or not GITHUB_REPO_RE.match(repo):
        return (
            "invalid",
            f"has source.repo {repo!r}; a federated entry needs "
            '"repo": "OWNER/REPO"',
        )
    return ("federated", repo)


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


def _rel(path: Path) -> str:
    """Path relative to the repo root when it is under it, for readable errors.

    Falls back to the absolute path so a tmp_path-rooted fixture (or a
    --private-registry sibling) still produces a message instead of a
    ValueError from Path.relative_to.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _check_local_entry(name: str, source: str, errors: List[str], plugins_dir: Path) -> None:
    """A local entry's plugin root is on disk: read it and cross-check it."""
    # The reverse scan below — and setup.sh, and the whole plugins/<name>
    # convention — assume the directory basename IS the plugin name. An entry
    # that pointed anywhere else would break that identity silently: `claude
    # plugin validate` resolves the declared path while this script resolves
    # the conventional one, so the two would check different files and agree
    # they were both fine. plugins_dir.name rather than a literal "plugins" so
    # renaming the directory constant cannot leave this string behind.
    expected_source = f"./{plugins_dir.name}/{name}"
    if source != expected_source:
        errors.append(
            f"marketplace.json entry '{name}' has source '{source}'; a local "
            f"entry must be '{expected_source}' so the marketplace name and the "
            "plugin directory basename stay the same thing"
        )

    plugin_json_path = plugins_dir / name / ".claude-plugin" / "plugin.json"
    if not plugin_json_path.is_file():
        errors.append(
            f"marketplace.json lists '{name}' but {_rel(plugin_json_path)} does not exist"
        )
        return
    try:
        plugin_json = json.loads(plugin_json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{_rel(plugin_json_path)} is not valid JSON: {exc}")
        return
    if plugin_json.get("name") != name:
        errors.append(
            f"{_rel(plugin_json_path)} has name "
            f"'{plugin_json.get('name')}', expected '{name}'"
        )


def _check_federated_entry(name: str, repo: str, errors: List[str], plugins_dir: Path) -> None:
    """A federated entry's plugin root is in another repo — assert what is
    still checkable here, offline.

    Skipping the entry outright would trade a false error for a silent hole:
    `claude plugin validate` never resolves a github source, so if this script
    also looks away, NOTHING in CI has an opinion about the entry. Two things
    are checkable without the network: that the source is well-formed (already
    done by classify_source, which is why reaching here means it is), and this:

      no local plugins/<name>/ may exist under the same name. A name that is
      both a directory here and a remote source is ambiguous — one resolver
      would pick the local tree and another the remote one, and the two would
      silently disagree about which plugin actually ships under that name.

    Everything else about the remote tree — its manifests, its SKILL.md files —
    is that repo's own CI's job. check_agent_plugins.py names that split out
    loud rather than leaving it implied.
    """
    local_dir = plugins_dir / name
    if local_dir.exists():
        errors.append(
            f"marketplace.json entry '{name}' is federated from {repo} but "
            f"{_rel(local_dir)} also exists; a name cannot be both a local "
            "plugin root and a remote one — delete one of them"
        )


def curated_skill_paths(entry: dict) -> List[str]:
    """The `skills` list of a curated entry, as given; [] when absent or not a
    list of strings. Validation is _check_curated_entry's job — this is the one
    reader the other scripts (README table, version gate) use to ask "which
    directories make up this plugin"."""
    skills = entry.get("skills")
    if not isinstance(skills, list):
        return []
    return [path for path in skills if isinstance(path, str)]


def _curated_skill_dir(path: str, plugins_dir: Path) -> Optional[Path]:
    """Resolve one curated `skills` path to its skill directory, or None when
    it is not exactly ./<plugins>/<bundle>/skills/<skill>.

    The shape is pinned, not merely "somewhere under the root": a skill served
    from anywhere else would be outside every bundle, so setup.sh, the
    basename-uniqueness rule and skills.lock would all be blind to it. Split on
    "/" (the marketplace's own separator on every OS) rather than handed to
    Path, so "..", "." and empty segments are rejected instead of normalised
    away.
    """
    parts = path.split("/")
    if len(parts) != 5 or parts[0] != "." or parts[1] != plugins_dir.name or parts[3] != "skills":
        return None
    bundle, skill = parts[2], parts[4]
    if bundle in ("", ".", "..") or skill in ("", ".", ".."):
        return None
    return plugins_dir / bundle / "skills" / skill


def _check_curated_entry(entry: dict, errors: List[str], plugins_dir: Path) -> None:
    """A curated entry has no plugin root of its own: its `skills` list is the
    plugin. Check that list against the filesystem, and the three settings that
    make it a curated plugin rather than an accident.

      * "strict": false — without it Claude Code expects a plugin.json at the
        source (the repo root, which has none) to define the components;
      * "defaultEnabled": false — the same opt-in rule every federated entry
        follows: installing it must never switch skills on in a repo session
        (repos install the bundles, ADR 0010);
      * a "version" — ADR 0009: `plugin update` gates on it, and
        check_plugin_versions.py enforces the bump against it. There is no
        plugin.json to carry it, so the entry does.
    """
    name = entry["name"]
    unknown = sorted(set(entry) - CURATED_ENTRY_KEYS)
    if unknown:
        errors.append(
            f"marketplace.json entry '{name}' is curated but carries "
            f"{', '.join(repr(key) for key in unknown)}; a curated entry may carry "
            f"only {', '.join(sorted(CURATED_ENTRY_KEYS))} — anything else (hooks, "
            "MCP/LSP servers, agents, commands ...) would join the plugin unchecked"
        )
    if entry.get("strict") is not False:
        errors.append(
            f"marketplace.json entry '{name}' has source '{CURATED_SOURCE}' but not "
            '"strict": false; a marketplace-root entry must be its own whole '
            "definition, since the root carries no plugin.json"
        )
    if entry.get("defaultEnabled") is not False:
        errors.append(
            f"marketplace.json entry '{name}' is curated from the marketplace root "
            'but not "defaultEnabled": false; it must arrive switched off'
        )
    if not isinstance(entry.get("version"), str) or not entry["version"]:
        errors.append(
            f"marketplace.json entry '{name}' is curated but has no \"version\"; "
            "with no plugin.json the entry must carry it (ADR 0009)"
        )
    skills = entry.get("skills")
    if not isinstance(skills, list) or not skills:
        errors.append(
            f"marketplace.json entry '{name}' is curated but has no \"skills\" list; "
            "with a marketplace-root source an empty list falls back to scanning "
            "the whole root"
        )
        return
    seen = set()
    for path in skills:
        if not isinstance(path, str):
            errors.append(f"marketplace.json entry '{name}' has a non-string skills path {path!r}")
            continue
        if path in seen:
            errors.append(f"marketplace.json entry '{name}' lists skills path '{path}' more than once")
        seen.add(path)
        skill_dir = _curated_skill_dir(path, plugins_dir)
        if skill_dir is None:
            errors.append(
                f"marketplace.json entry '{name}' lists skills path '{path}'; a curated "
                f"path must be exactly ./{plugins_dir.name}/<bundle>/skills/<skill>"
            )
        elif not (skill_dir / "SKILL.md").is_file():
            errors.append(
                f"marketplace.json entry '{name}' lists skills path '{path}', which has "
                "no SKILL.md"
            )
    # Same ambiguity as a shadowed federated entry: a plugins/<name>/ beside a
    # curated <name> would be a second, different definition of one plugin.
    local_dir = plugins_dir / name
    if local_dir.exists():
        errors.append(
            f"marketplace.json entry '{name}' is curated from the marketplace root but "
            f"{_rel(local_dir)} also exists; a name cannot be both"
        )


class AccountDeclarationReaderMissing(Exception):
    """sync_skills.py, or its load_account_declaration(), could not be loaded."""


def _load_account_declaration(
    path: Path, reader_path: Optional[Path] = None
) -> Optional[Set[str]]:
    """Parse account-skills.txt with sync_skills.py's own reader.

    Loaded by path, not re-implemented: that reader is what `--verify` uses, so
    a second parser here could accept a line the gate reads differently, and
    the plugin and the declaration it is checked against would silently mean
    different things. sync_skills.py is stdlib-only with no import-time side
    effects beyond computing paths.

    Raises AccountDeclarationReaderMissing, never a bare AttributeError or
    FileNotFoundError, when the reader is gone — ADR 0012's phase 3 retires
    sync-skills' upload path, and whoever does that must see this check name
    what it depends on rather than crash.
    """
    reader_path = reader_path or SYNC_SKILLS_DIR / "sync_skills.py"
    try:
        spec = importlib.util.spec_from_file_location(
            "_sync_skills_account_declaration", reader_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"no loader for {reader_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        reader = module.load_account_declaration
    except (OSError, ImportError, SyntaxError, AttributeError) as exc:
        raise AccountDeclarationReaderMissing(
            f"cannot load load_account_declaration() from {_rel(reader_path)} "
            f"({type(exc).__name__}: {exc}); check_consistency.py reads "
            "account-skills.txt with it (ADR 0012) — keep it, or move the parser "
            "and point check_consistency.py's _load_account_declaration at its new home"
        ) from exc
    return reader(path)


def expected_account_skill_paths(
    declared: Set[str], plugins_dir: Path, errors: List[str]
) -> Set[str]:
    """Map each declared skill name to ./<plugins>/<bundle>/skills/<name>.

    Found by searching every bundle rather than by a name->bundle table, so a
    skill can never be listed under a bundle it does not live in. Basenames are
    unique across the repo (check_unique_skill_basenames), so a name resolves
    to at most one directory; zero is an error.
    """
    expected: Set[str] = set()
    for name in sorted(declared):
        matches = sorted(plugins_dir.glob(f"*/skills/{name}/SKILL.md"))
        if len(matches) != 1:
            errors.append(
                f"account-skills.txt declares '{name}', which resolves to "
                f"{len(matches)} plugins/*/skills/{name}/ directories; expected exactly 1"
            )
            continue
        bundle = matches[0].parent.parent.parent.name
        expected.add(f"./{plugins_dir.name}/{bundle}/skills/{name}")
    return expected


def check_account_plugin(
    marketplace: dict,
    errors: List[str],
    plugins_dir: Path = PLUGINS_DIR,
    declared: Optional[Set[str]] = None,
) -> None:
    """ACCOUNT_PLUGIN lists exactly the skills account-skills.txt declares.

    The declaration file stays the single source (ADR 0012); the marketplace
    entry is a copy that CI holds equal to it, so adding a skill to the account
    is still one reviewed line there — plus this entry and a version bump.
    `declared` is injectable for tests; by default it is read from the file.
    """
    entries = [e for e in marketplace.get("plugins", []) if e.get("name") == ACCOUNT_PLUGIN]
    if not entries:
        errors.append(f"marketplace.json has no '{ACCOUNT_PLUGIN}' entry (ADR 0012)")
        return
    entry = entries[0]
    if classify_source(entry)[0] != "curated":
        errors.append(
            f"marketplace.json entry '{ACCOUNT_PLUGIN}' must have source "
            f"'{CURATED_SOURCE}' (ADR 0012)"
        )
        return
    if declared is None:
        try:
            declared = _load_account_declaration(ACCOUNT_SKILLS_PATH)
        except AccountDeclarationReaderMissing as exc:
            errors.append(str(exc))
            return
    if declared is None:
        errors.append(f"{_rel(ACCOUNT_SKILLS_PATH)} is missing or unreadable")
        return
    expected = expected_account_skill_paths(declared, plugins_dir, errors)
    listed = set(curated_skill_paths(entry))
    for path in sorted(expected - listed):
        errors.append(
            f"marketplace.json entry '{ACCOUNT_PLUGIN}' is missing '{path}', which "
            "account-skills.txt declares"
        )
    for path in sorted(listed - expected):
        errors.append(
            f"marketplace.json entry '{ACCOUNT_PLUGIN}' lists '{path}', which "
            "account-skills.txt does not declare"
        )


def check_curated_root_components(
    marketplace: dict, errors: List[str], repo_root: Path = REPO_ROOT
) -> None:
    """No default plugin component may sit at the repo root while a curated
    ("./") entry exists — see CURATED_ROOT_COMPONENTS."""
    curated = [
        entry.get("name") for entry in marketplace.get("plugins", [])
        if isinstance(entry, dict) and classify_source(entry)[0] == "curated"
    ]
    if not curated:
        return
    for rel in CURATED_ROOT_COMPONENTS:
        # lexists: a dangling symlink at one of these names is still something
        # a loader might follow.
        if os.path.lexists(repo_root / rel):
            errors.append(
                f"{rel} exists at the repository root, which is the plugin root of "
                f"curated entr{'y' if len(curated) == 1 else 'ies'} "
                f"{', '.join(repr(n) for n in curated)}; Claude Code may load it as "
                "part of that plugin (ADR 0012) — move it or drop the curated entry"
            )


def check_marketplace_entries(
    marketplace: dict, errors: List[str], plugins_dir: Path = PLUGINS_DIR
) -> None:
    """Check every marketplace entry, then scan back from the filesystem."""
    marketplace_names = set()
    for index, entry in enumerate(marketplace.get("plugins", [])):
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"marketplace.json plugins[{index}] has no usable 'name': {name!r}")
            continue
        if name in marketplace_names:
            errors.append(f"marketplace.json lists plugin name '{name}' more than once")
        marketplace_names.add(name)

        kind, detail = classify_source(entry)
        if kind == "local":
            _check_local_entry(name, detail, errors, plugins_dir)
        elif kind == "federated":
            _check_federated_entry(name, detail, errors, plugins_dir)
        elif kind == "curated":
            _check_curated_entry(entry, errors, plugins_dir)
        else:
            errors.append(f"marketplace.json entry '{name}' {detail}")

    if not plugins_dir.is_dir():
        return
    for plugin_json_path in sorted(plugins_dir.glob("*/.claude-plugin/plugin.json")):
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
    name by looking it up as a key and following the chain of values — at
    most 16 hops — until it reaches a name that is not itself a key; that
    terminal name must be a current plugins[].name. A null value means
    "removed". The map is append-only forever — users may update from any
    historical version.
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
    bad_keys = set()  # entries already reported; skip their chain walk
    for old, new in renames.items():
        if not (new is None or isinstance(new, str)):
            errors.append(
                f"renames entry '{old}' has non-string value {new!r}; "
                "values must be a plugin name string or null (= removed)"
            )
            bad_keys.add(old)
            continue
        if old in plugin_names:
            errors.append(f"renames key '{old}' collides with a current plugin name")
        if new == old:
            errors.append(f"renames entry '{old}' maps to itself")
            bad_keys.add(old)  # a self-map is also a cycle; one error is enough
    max_hops = 16  # the 2.1.211 resolver gives up after 16 lookups
    for old in renames:
        if old in bad_keys:
            continue
        # Follow the value chain; bounded by the visited set and the hop cap.
        seen = {old}
        target = renames[old]
        hops = 1
        while isinstance(target, str) and target in renames:
            if target in seen:
                errors.append(f"renames chain starting at '{old}' contains a cycle")
                break
            if hops >= max_hops:
                errors.append(
                    f"renames chain starting at '{old}' exceeds the resolver "
                    f"depth limit ({max_hops})"
                )
                break
            seen.add(target)
            target = renames[target]
            hops += 1
        else:
            if target is None:
                continue  # removed — a valid terminal
            if not isinstance(target, str):
                continue  # mid-chain bad value, already reported for its own key
            if target not in plugin_names:
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
    marketplace = load_marketplace()

    errors: List[str] = []
    check_marketplace_entries(marketplace, errors)
    check_account_plugin(marketplace, errors)
    check_curated_root_components(marketplace, errors)
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
