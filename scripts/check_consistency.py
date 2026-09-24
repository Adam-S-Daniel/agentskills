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
  - the account plugin (ACCOUNT_PLUGIN, ADR 0012) is a plugin of SKILL
    LINKS: plugins/adam-personal/skills/<name> is a git symlink to
    ../../<bundle>/skills/<name> for exactly the names account-skills.txt
    declares (that file stays the one declaration), every link resolves to a
    real skill directory, the folder holds nothing but its manifests and
    skills/, and its marketplace entry is opt-in and adds no components;
  - every plugins/*/skills/*/ directory contains a SKILL.md;
  - if marketplace.json has a "renames" map ({old-name: new-name-or-null},
    append-only forever — users may update from any old version), every
    value is a string or null, and every chain of values terminates at an
    existing plugin entry or at null (= plugin removed) within the
    resolver's 16-hop depth limit, with no cycles, no self-mappings, and
    no key that shadows a current plugin name;
  - skill directory basenames are unique across the whole repo, since they
    key setup.sh's per-agent symlinks and claude.ai skill uploads — a
    symlinked skill entry is a second NAME for a skill, not a second skill,
    and is not counted (see is_linked_skill_entry);
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
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PLUGINS_DIR = REPO_ROOT / "plugins"

# The account plugin (ADR 0012): the skills the owner's claude.ai account
# carries, as a real plugin folder whose skills/<name> entries are git symlinks
# (mode 120000) to ../../<bundle>/skills/<name>. claude.ai builds the plugin from
# GitHub and serves the linked skills (measured 2026-09-24, E6 §3.7; that it
# resolves the links server-side is inferred from a terminal's synced copy); its
# skill set is DERIVED — it must equal the names in ACCOUNT_SKILLS_PATH, which
# stays the single declaration of account membership that sync_skills.py
# --verify reads.
ACCOUNT_PLUGIN = "adam-personal"

# The display-only keys of a marketplace entry: what users SEE in listings, not
# what the plugin loads (code.claude.com/docs/en/plugin-marketplaces, "Both the
# entry and the plugin's own plugin.json can set the display fields
# displayName, description, author, homepage, repository, license, and
# keywords", plus the marketplace's own `category` and `tags`).
DISPLAY_KEYS = frozenset({
    "displayName", "description", "author", "homepage", "repository",
    "license", "keywords", "category", "tags",
})

# Every key the account plugin's marketplace ENTRY may carry. Narrowed from the
# curated-entry allowlist it replaces rather than dropped: with the default
# strict mode, plugin.json is the authority but an entry can still ADD skills,
# hooks, MCP servers and the rest on top of it — code that would run on every
# surface the account reaches. `version` is refused too: plugin.json carries
# it, and the docs warn that a plugin.json version silently masks an entry's.
ACCOUNT_ENTRY_KEYS = DISPLAY_KEYS | {"name", "source", "defaultEnabled"}

# What plugins/adam-personal/ may contain at its top level. Anything else would
# join the plugin (hooks/, agents/, .mcp.json, bin/, settings.json, a
# package.json that triggers an install in every cached copy ...), so the folder
# is closed. plugin.json is the Agent Plugins manifest every sibling bundle
# carries, kept so check_agent_plugins.py treats it like one.
ACCOUNT_PLUGIN_TOP_LEVEL = frozenset({".claude-plugin", "plugin.json", "skills"})

# Every key the account plugin's two MANIFESTS may carry: metadata only, the
# set every sibling bundle uses (plus homepage/license, still metadata), and
# `$schema` on the Agent Plugins root manifest, which the spec requires there.
ACCOUNT_MANIFEST_KEYS = frozenset({
    "name", "version", "description", "author", "homepage", "repository",
    "license", "keywords",
})
ACCOUNT_ROOT_MANIFEST_KEYS = ACCOUNT_MANIFEST_KEYS | {"$schema"}
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


def _git_out(cwd: Path, *args: str) -> Optional[bytes]:
    """stdout of `git -C cwd <args>`, or None when git fails or is absent."""
    try:
        proc = subprocess.run(["git", "-C", str(cwd), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _tracked_as_symlink(path: Path) -> bool:
    """True when git's index records `path` as a symlink (mode 120000)."""
    out = _git_out(path.parent, "ls-files", "-s", "--", path.name)
    return bool(out) and out.startswith(b"120000 ")


def is_linked_skill_entry(path: Path) -> bool:
    """True when a plugins/<plugin>/skills/<name> entry is a LINK, not a skill.

    Two spellings of the same git object (mode 120000): a real symlink where
    the checkout materialises symlinks (Linux, macOS, and — measured on PR
    #177's runs — the GitHub Windows runners), and a small regular FILE holding
    the link target where it has core.symlinks=false (the Git for Windows
    default, so the owner's Windows clone). A regular file counts only where it
    can BE a link: inside the account plugin's skills/, or recorded by git as
    mode 120000 — so a stray README.md under a real bundle's skills/ is not
    mistaken for one. Every consumer that enumerates skills asks this before
    counting, linking, zipping or locking an entry, so the one skill behind a
    link is never seen twice.
    """
    if path.is_symlink():
        return True
    if not path.is_file():
        return False
    return path.parent.parent.name == ACCOUNT_PLUGIN or _tracked_as_symlink(path)


def read_skill_link(path: Path) -> Optional[str]:
    """The target a linked skill entry names, '/'-separated; None if `path` is
    a real directory (a copy, not a link) or does not exist."""
    if path.is_symlink():
        return os.readlink(path).replace("\\", "/")
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return None


def linked_skill_entries(plugin_dir: Path) -> Dict[str, Optional[str]]:
    """{name: link target, or None for a real directory} for every skill entry
    in plugin_dir/skills/; a regular file that is not a link (is_linked_skill_entry)
    is no skill entry at all and is left out. The one filesystem reader of
    "what does a plugin of links hold", shared with generate_readme_table.py and
    check_plugin_versions.py."""
    skills_dir = plugin_dir / "skills"
    if not skills_dir.is_dir():
        return {}
    entries: Dict[str, Optional[str]] = {}
    for entry in sorted(skills_dir.iterdir()):
        if is_linked_skill_entry(entry):
            entries[entry.name] = read_skill_link(entry)
        elif entry.is_dir():
            entries[entry.name] = None
    return entries


def committed_link_entries(plugin_dir: Path) -> Optional[Dict[str, Tuple[str, Optional[str]]]]:
    """{name: (mode, raw target or None)} for plugin_dir/skills/* as git's INDEX
    records them, or None when plugin_dir is not inside a git work tree.

    The committed truth, whatever this checkout wrote to disk. It is what CI's
    test reads, so the checker reads it too: on a core.symlinks=false clone a
    link that was `git add`ed as a text file is mode 100644 — a regular file
    claude.ai would serve as-is — and only the index can say so. A tracked
    skill DIRECTORY (files below skills/<name>/) is reported as mode "tree".
    """
    if not plugin_dir.is_dir():
        return None
    inside = _git_out(plugin_dir, "rev-parse", "--is-inside-work-tree")
    listing = None
    if inside and inside.strip() == b"true":
        listing = _git_out(plugin_dir, "ls-files", "-s", "-z", "--", "skills")
    if listing is None:
        # Outside git the filesystem answer is the valid one. But a `.git` up
        # the tree with git failing (not installed, not on PATH, a broken repo)
        # means the index check was SKIPPED here while CI runs it — say so
        # rather than pass quietly on a weaker check.
        if any((parent / ".git").exists() for parent in (plugin_dir, *plugin_dir.parents)):
            print(
                f"WARNING: {_rel(plugin_dir)} is inside a git work tree but git could "
                "not be run, so the account plugin's links were checked on the "
                "filesystem only, not their committed modes (ADR 0012) — CI may "
                "disagree with this result",
                file=sys.stderr,
            )
        return None
    entries: Dict[str, Tuple[str, Optional[str]]] = {}
    for record in listing.decode("utf-8").split("\0"):
        if not record:
            continue
        meta, path = record.split("\t", 1)
        mode, blob, _stage = meta.split()
        parts = path.split("/")
        if len(parts) < 2:
            continue
        if len(parts) > 2:
            entries[parts[1]] = ("tree", None)
            continue
        raw = _git_out(plugin_dir, "cat-file", "blob", blob)
        entries[parts[1]] = (mode, raw.decode("utf-8") if raw is not None else None)
    return entries


def _skill_basenames(plugins_dir: Path) -> Dict[str, List[str]]:
    """Map skill directory basename -> ['<plugin>/<skill>', ...] locations.

    A linked entry (is_linked_skill_entry) is skipped: on a symlink-capable
    checkout the glob follows it into the target, and counting that would
    report every account skill as a duplicate of itself.
    """
    locations: Dict[str, List[str]] = {}
    if not plugins_dir.is_dir():
        return locations
    for skill_md in sorted(plugins_dir.glob("*/skills/*/SKILL.md")):
        skill_dir = skill_md.parent
        if is_linked_skill_entry(skill_dir):
            continue
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


def skill_home(name: str, plugins_dir: Path, errors: List[str]) -> Optional[str]:
    """The bundle that holds skill `name` as a real directory with SKILL.md, or
    None (with an error) when there is not exactly one.

    A home is a real BUNDLE: a plugins/<bundle> directory that is not itself a
    symlink and carries its Claude Code manifest. Linked skill entries are
    skipped, so the account plugin's own link never counts — and neither does
    a two-hop path like a committed `plugins/evil -> elsewhere` holding
    skills/<name>/SKILL.md, which would otherwise give ../../evil/skills/<name>
    a "home" outside every bundle this repo validates.
    """
    homes = []
    for skill_md in sorted(plugins_dir.glob(f"*/skills/{name}/SKILL.md")):
        bundle_dir = skill_md.parent.parent.parent
        if is_linked_skill_entry(skill_md.parent) or bundle_dir.is_symlink():
            continue
        if not (bundle_dir / ".claude-plugin" / "plugin.json").is_file():
            continue
        homes.append(bundle_dir.name)
    if len(homes) != 1:
        errors.append(
            f"account-skills.txt declares '{name}', which is a real skill directory "
            f"in {len(homes)} bundles ({', '.join(homes) or 'none'}); expected exactly 1"
        )
        return None
    return homes[0]


def check_account_plugin(
    marketplace: dict,
    errors: List[str],
    plugins_dir: Path = PLUGINS_DIR,
    declared: Optional[Set[str]] = None,
) -> None:
    """ACCOUNT_PLUGIN is a folder of skill links equal to account-skills.txt.

    The declaration file stays the single source (ADR 0012); the folder is a
    copy that CI holds equal to it, so adding a skill to the account is still
    one reviewed line there — plus one link and a version bump. Checked:

      * the marketplace entry is local (./plugins/adam-personal), opt-in, and
        carries only ACCOUNT_ENTRY_KEYS — no skills/hooks/... added on top;
      * the folder holds only ACCOUNT_PLUGIN_TOP_LEVEL, and .claude-plugin/
        only plugin.json;
      * skills/ has exactly one entry per declared name, each a LINK (a
        symlink, or on a core.symlinks=false checkout a file holding the
        target) — a real directory there would be an unversioned second copy;
      * each link's target is exactly ../../<bundle>/skills/<name>, where
        <bundle> is the one bundle holding that skill as a real directory with
        SKILL.md — so it resolves, and never to another link.

    `declared` is injectable for tests; by default it is read from the file.
    """
    entries = [e for e in marketplace.get("plugins", []) if e.get("name") == ACCOUNT_PLUGIN]
    if not entries:
        errors.append(f"marketplace.json has no '{ACCOUNT_PLUGIN}' entry (ADR 0012)")
        return
    entry = entries[0]
    expected_source = f"./{plugins_dir.name}/{ACCOUNT_PLUGIN}"
    if entry.get("source") != expected_source:
        errors.append(
            f"marketplace.json entry '{ACCOUNT_PLUGIN}' must have source "
            f"'{expected_source}' (ADR 0012): claude.ai does not list an entry "
            "whose source folder has no plugin.json (E6 §3.7)"
        )
    unknown = sorted(set(entry) - ACCOUNT_ENTRY_KEYS)
    if unknown:
        errors.append(
            f"marketplace.json entry '{ACCOUNT_PLUGIN}' carries "
            f"{', '.join(repr(key) for key in unknown)}; it may carry only "
            f"{', '.join(sorted(ACCOUNT_ENTRY_KEYS))} — plugin.json and the skill "
            "links define the plugin, and anything added on the entry (skills, "
            "hooks, MCP servers, a version that plugin.json would mask) bypasses them"
        )
    if entry.get("defaultEnabled") is not False:
        errors.append(
            f"marketplace.json entry '{ACCOUNT_PLUGIN}' must be \"defaultEnabled\": "
            "false; it must arrive switched off"
        )

    plugin_dir = plugins_dir / ACCOUNT_PLUGIN
    if not plugin_dir.is_dir():
        errors.append(f"{_rel(plugin_dir)} does not exist (ADR 0012)")
        return
    for child in sorted(plugin_dir.iterdir()):
        if child.name not in ACCOUNT_PLUGIN_TOP_LEVEL:
            errors.append(
                f"{_rel(child)} is not allowed in {ACCOUNT_PLUGIN}/: the folder holds "
                f"only {', '.join(sorted(ACCOUNT_PLUGIN_TOP_LEVEL))}, since anything "
                "else there would load as part of the account plugin"
            )
    manifest_dir = plugin_dir / ".claude-plugin"
    if manifest_dir.is_dir():
        for child in sorted(manifest_dir.iterdir()):
            if child.name != "plugin.json":
                errors.append(f"{_rel(child)} is not allowed; .claude-plugin/ holds only plugin.json")
    # The manifests are closed too: a Claude Code plugin.json may declare hooks,
    # MCP/LSP servers, commands, agents, output styles or extra skills paths
    # INLINE, which the closed-folder rule above never sees.
    for manifest, allowed in ((manifest_dir / "plugin.json", ACCOUNT_MANIFEST_KEYS),
                              (plugin_dir / "plugin.json", ACCOUNT_ROOT_MANIFEST_KEYS)):
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            errors.append(f"{_rel(manifest)} is not valid JSON: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{_rel(manifest)} is not a JSON object")
            continue
        extra = sorted(set(data) - allowed)
        if extra:
            errors.append(
                f"{_rel(manifest)} carries {', '.join(repr(key) for key in extra)}; the "
                f"account plugin's manifest may carry only {', '.join(sorted(allowed))} — "
                "components declared there would load on every surface the account reaches"
            )

    if declared is None:
        try:
            declared = _load_account_declaration(ACCOUNT_SKILLS_PATH)
        except AccountDeclarationReaderMissing as exc:
            errors.append(str(exc))
            return
    if declared is None:
        errors.append(f"{_rel(ACCOUNT_SKILLS_PATH)} is missing or unreadable")
        return

    committed = committed_link_entries(plugin_dir)
    if committed is None:
        # Outside a git work tree only the filesystem can answer.
        links = linked_skill_entries(plugin_dir)
    else:
        links = {}
        for name, (mode, target) in committed.items():
            entry_path = plugin_dir / "skills" / name
            if mode == "tree":
                links[name] = None
            elif mode != "120000":
                errors.append(
                    f"{_rel(entry_path)} is committed as mode {mode}, not as a symlink "
                    "(120000): claude.ai would serve that file, not the skill. On a "
                    "core.symlinks=false checkout add it with `git update-index --add "
                    "--cacheinfo 120000,...` (ADR 0012)"
                )
            elif target is None or "\\" in target:
                errors.append(
                    f"{_rel(entry_path)} links to {target!r}; a link target uses '/' only"
                )
            else:
                links[name] = target
    for name in sorted(set(links) - declared):
        errors.append(
            f"{_rel(plugin_dir / 'skills' / name)} is not declared in account-skills.txt"
        )
    for name in sorted(declared - set(links)):
        errors.append(
            f"account-skills.txt declares '{name}' but {_rel(plugin_dir / 'skills')} "
            "has no link for it"
        )
    for name in sorted(declared & set(links)):
        target = links[name]
        entry_path = plugin_dir / "skills" / name
        if target is None:
            errors.append(
                f"{_rel(entry_path)} is a real directory; it must be a git symlink to "
                "the bundle's skill (a copy would drift from it)"
            )
            continue
        bundle = skill_home(name, plugins_dir, errors)
        if bundle is None:
            continue
        expected_target = f"../../{bundle}/skills/{name}"
        if target != expected_target:
            errors.append(
                f"{_rel(entry_path)} links to {target!r}; expected {expected_target!r}"
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
