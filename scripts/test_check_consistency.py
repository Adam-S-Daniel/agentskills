#!/usr/bin/env python3
"""Tests for scripts/check_consistency.py.

Hermetic and deterministic: the machinery tests build throwaway plugin trees
under pytest's `tmp_path` and pass them in explicitly, so nothing here reads or
writes the real repo except where it means to. No network, no sleeps, no
wall-clock dependence.

Two kinds of test live here, deliberately:
  * MACHINERY tests (most of the file) use synthetic marketplaces and plugin
    dirs, so re-wording a shipped description can never break a test of the
    checking logic;
  * SHIPPED-ARTIFACT tests (the last section) read the real repo on purpose —
    they pin the contract itself: that the marketplace this repo publishes is
    internally consistent, and that EVERY federated entry has the shape the
    federation decision settled on. Those are written as rules over all
    federated entries rather than assertions about the name "cms-platform",
    so the second federated bundle inherits them instead of shipping
    unchecked.

Run: python3 -m pytest scripts/test_check_consistency.py -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_consistency as cc  # noqa: E402


# =================================================================================
# Fixture builders
# =================================================================================


def local_entry(name="demo", source=None, **extra):
    entry = {"name": name, "source": source if source is not None else f"./plugins/{name}"}
    entry.update(extra)
    return entry


def federated_entry(name="remote-bundle", repo="Owner/Repo", **extra):
    entry = {"name": name, "source": {"source": "github", "repo": repo}}
    entry.update(extra)
    return entry


def marketplace(*entries, **extra):
    data = {"name": "test-marketplace", "plugins": list(entries)}
    data.update(extra)
    return data


def write_local_plugin(plugins_dir: Path, name: str, *, manifest=...) -> Path:
    """Create plugins_dir/<name>/.claude-plugin/plugin.json.

    Pass manifest=None to create the directory with no Claude manifest, or a
    string to write raw (possibly malformed) bytes instead of JSON.
    """
    plugin_dir = plugins_dir / name
    (plugin_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    if manifest is ...:
        manifest = {"name": name, "version": "1.0.0"}
    if manifest is None:
        return plugin_dir
    path = plugin_dir / ".claude-plugin" / "plugin.json"
    if isinstance(manifest, str):
        path.write_text(manifest, encoding="utf-8")
    else:
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return plugin_dir


@pytest.fixture
def plugins_dir(tmp_path):
    d = tmp_path / "plugins"
    d.mkdir()
    return d


def errors_for(market, plugins_dir):
    found = []
    cc.check_marketplace_entries(market, found, plugins_dir=plugins_dir)
    return found


# =================================================================================
# classify_source — the one question `claude plugin validate` cannot answer
# =================================================================================


def test_a_relative_path_source_is_local():
    assert cc.classify_source(local_entry("adam")) == ("local", "./plugins/adam")


def test_a_github_source_object_is_federated():
    assert cc.classify_source(federated_entry(repo="Owner/Repo")) == ("federated", "Owner/Repo")


def test_a_missing_source_is_invalid():
    kind, detail = cc.classify_source({"name": "demo"})
    assert kind == "invalid"
    assert "no 'source'" in detail


@pytest.mark.parametrize("source", [None, 42, ["./plugins/demo"]])
def test_a_source_of_the_wrong_type_is_invalid(source):
    kind, detail = cc.classify_source({"name": "demo", "source": source})
    assert kind == "invalid"
    assert "type" in detail


@pytest.mark.parametrize("host", ["gitlab", "url", "git", "", None])
def test_only_github_is_federated_from(host):
    kind, detail = cc.classify_source({"name": "demo", "source": {"source": host, "repo": "o/r"}})
    assert kind == "invalid"
    assert "source.source" in detail


@pytest.mark.parametrize(
    "repo",
    [
        "https://github.com/Owner/Repo",  # a URL, not OWNER/REPO
        "Owner/Repo/skills",  # a subpath — federation uses the repo ROOT
        "Owner",  # no repo half
        "Owner/",  # empty repo half
        "/Repo",  # empty owner half
        "./plugins/demo",  # a local path smuggled into the object form
        "",
        None,
        42,
    ],
)
def test_a_malformed_repo_is_invalid(repo):
    kind, detail = cc.classify_source({"name": "demo", "source": {"source": "github", "repo": repo}})
    assert kind == "invalid"
    assert "source.repo" in detail


@pytest.mark.parametrize("repo", ["Adam-S-Daniel/cms-platform", "o/r", "a.b/c_d-e", "A1/B2"])
def test_well_formed_repos_are_accepted(repo):
    assert cc.classify_source({"source": {"source": "github", "repo": repo}}) == (
        "federated",
        repo,
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"ref": "v1.2.3"},
        {"commit": "0" * 40},
        {"version": "1.2.3"},
        {"branch": "main"},
        {"sha": "0" * 40},
        {"path": "skills"},
        {"ref": "v1.2.3", "commit": "0" * 40, "version": "1.2.3"},
    ],
)
def test_an_extra_key_on_a_federated_source_is_invalid(extra):
    # `claude plugin validate . --strict` passes every one of these, and the
    # CLI's own plugin-source schema declares NONE of commit/version/branch/path
    # for a github plugin source — they are silently stripped. `ref`/`sha` it
    # does declare, but this repo pins a federated bundle in skills.lock, where
    # the pin is an immutable commit with a per-skill sha256 it can verify.
    # Either way the key reads as a pin that nothing here can stand behind, so
    # classification must REJECT it rather than shrug it off as harmless.
    entry = {"name": "demo", "source": {"source": "github", "repo": "o/r", **extra}}
    kind, detail = cc.classify_source(entry)
    assert kind == "invalid"
    for key in extra:
        assert repr(key) in detail


def test_the_extra_key_rejection_names_the_pinning_rule():
    entry = {"name": "demo", "source": {"source": "github", "repo": "o/r", "ref": "v1"}}
    assert "skills.lock" in cc.classify_source(entry)[1]


def test_a_federated_source_carrying_exactly_the_known_keys_is_accepted():
    entry = {"name": "demo", "source": {"source": "github", "repo": "o/r"}}
    assert cc.classify_source(entry) == ("federated", "o/r")


def test_an_extra_key_on_a_federated_entry_fails_the_marketplace_check(plugins_dir):
    # Not just classification — the error has to reach the exit code, since
    # every checker exiting 0 on such an entry is the hole being closed.
    entry = {
        "name": "remote",
        "source": {"source": "github", "repo": "Owner/Repo", "ref": "v1.2.3"},
    }
    errors = errors_for(marketplace(entry), plugins_dir)
    assert len(errors) == 1
    assert "remote" in errors[0] and "'ref'" in errors[0]


# =================================================================================
# Local entries
# =================================================================================


def test_a_well_formed_local_entry_has_no_errors(plugins_dir):
    write_local_plugin(plugins_dir, "demo")
    assert errors_for(marketplace(local_entry("demo")), plugins_dir) == []


def test_a_local_entry_with_no_plugin_json_is_reported(plugins_dir):
    assert any(
        "does not exist" in e for e in errors_for(marketplace(local_entry("demo")), plugins_dir)
    )


def test_a_local_plugin_json_naming_a_different_plugin_is_reported(plugins_dir):
    write_local_plugin(plugins_dir, "demo", manifest={"name": "not-demo"})
    errors = errors_for(marketplace(local_entry("demo")), plugins_dir)
    assert any("expected 'demo'" in e for e in errors)


def test_an_unparseable_local_plugin_json_is_reported(plugins_dir):
    write_local_plugin(plugins_dir, "demo", manifest="{ oops")
    assert any("not valid JSON" in e for e in errors_for(marketplace(local_entry("demo")), plugins_dir))


def test_a_local_source_pointing_somewhere_else_is_reported(plugins_dir):
    # The name<->directory identity is load-bearing: `claude plugin validate`
    # would read the declared path while this script reads plugins/<name>, so
    # the two would check different files and both report success.
    write_local_plugin(plugins_dir, "demo")
    errors = errors_for(marketplace(local_entry("demo", source="./plugins/elsewhere")), plugins_dir)
    assert any("./plugins/demo" in e for e in errors)


# =================================================================================
# Federated entries — what is assertable offline
# =================================================================================


def test_a_federated_entry_needs_no_local_directory(plugins_dir):
    # The regression this whole branch exists for: the old code demanded a
    # local plugins/<name>/.claude-plugin/plugin.json for EVERY entry, so a
    # federated one could not be published at all.
    assert errors_for(marketplace(federated_entry("remote")), plugins_dir) == []


def test_a_federated_entry_shadowed_by_a_local_directory_is_reported(plugins_dir):
    (plugins_dir / "remote").mkdir()
    errors = errors_for(marketplace(federated_entry("remote", repo="Owner/Repo")), plugins_dir)
    assert len(errors) == 1
    assert "remote" in errors[0] and "Owner/Repo" in errors[0]


def test_a_shadowing_local_directory_is_caught_even_when_it_is_a_full_bundle(plugins_dir):
    # With a plugin.json present the reverse scan stays quiet (the name IS in
    # the marketplace), so the collision check is the only thing standing
    # between this and two resolvers disagreeing about which plugin ships.
    write_local_plugin(plugins_dir, "remote")
    assert any("also exists" in e for e in errors_for(marketplace(federated_entry("remote")), plugins_dir))


def test_a_malformed_federated_source_is_reported(plugins_dir):
    entry = {"name": "remote", "source": {"source": "github", "repo": "Owner/Repo/skills"}}
    errors = errors_for(marketplace(entry), plugins_dir)
    assert len(errors) == 1
    assert "remote" in errors[0] and "OWNER/REPO" in errors[0]


def test_an_unclassifiable_entry_fails_rather_than_being_skipped(plugins_dir):
    # The failure mode being removed: an entry nobody has an opinion about.
    entry = {"name": "mystery", "source": {"source": "gitlab", "repo": "o/r"}}
    assert errors_for(marketplace(entry), plugins_dir) != []


def test_federated_and_local_entries_coexist(plugins_dir):
    write_local_plugin(plugins_dir, "demo")
    market = marketplace(local_entry("demo"), federated_entry("remote"))
    assert errors_for(market, plugins_dir) == []


# =================================================================================
# Entry identity, and the reverse scan
# =================================================================================


@pytest.mark.parametrize("name", [None, "", 42])
def test_an_entry_without_a_usable_name_is_reported(plugins_dir, name):
    errors = errors_for(marketplace({"name": name, "source": "./plugins/x"}), plugins_dir)
    assert any("plugins[0]" in e for e in errors)


def test_a_duplicated_plugin_name_is_reported(plugins_dir):
    write_local_plugin(plugins_dir, "demo")
    market = marketplace(local_entry("demo"), local_entry("demo"))
    assert any("more than once" in e for e in errors_for(market, plugins_dir))


def test_a_local_bundle_missing_from_the_marketplace_is_still_an_error(plugins_dir):
    write_local_plugin(plugins_dir, "orphan")
    errors = errors_for(marketplace(), plugins_dir)
    assert any("orphan" in e and "not listed" in e for e in errors)


def test_a_federated_name_does_not_satisfy_the_reverse_scan_for_a_local_dir(plugins_dir):
    # A directory called plugins/other must still be reported even though the
    # marketplace has entries — matching by NAME, not by "the marketplace is
    # non-empty".
    write_local_plugin(plugins_dir, "other")
    errors = errors_for(marketplace(federated_entry("remote")), plugins_dir)
    assert any("other" in e and "not listed" in e for e in errors)


def test_a_missing_plugins_dir_is_tolerated(tmp_path):
    assert errors_for(marketplace(federated_entry("remote")), tmp_path / "absent") == []


# =================================================================================
# renames — why the federated bundle deliberately has no entry there
# =================================================================================


def test_a_renames_key_naming_a_current_plugin_is_reported():
    # `renames` maps a RETIRED plugin name to a current one. A newly published
    # name is not a rename of anything, and giving it a key would make the
    # resolver treat the live name as historical.
    errors = []
    cc.check_renames(
        marketplace(federated_entry("remote"), **{"renames": {"remote": "remote"}}), errors
    )
    assert any("collides with a current plugin name" in e for e in errors)


# =================================================================================
# Curated entries (ADR 0012) — source "./", "strict": false, a `skills` list
# =================================================================================


def curated_entry(name="personal", skills=("./plugins/alpha/skills/one",), **extra):
    entry = {
        "name": name, "source": "./", "strict": False, "version": "1.0.0",
        "defaultEnabled": False, "skills": list(skills),
    }
    entry.update(extra)
    return entry


def write_skill(plugins_dir: Path, bundle: str, skill: str) -> None:
    skill_dir = plugins_dir / bundle / "skills" / skill
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("---\nname: %s\n---\n" % skill, encoding="utf-8")


@pytest.fixture
def curated_tree(plugins_dir):
    write_local_plugin(plugins_dir, "alpha")
    write_skill(plugins_dir, "alpha", "one")
    write_skill(plugins_dir, "alpha", "two")
    return plugins_dir


def test_the_marketplace_root_source_is_curated():
    assert cc.classify_source(curated_entry()) == ("curated", "./")


def test_a_well_formed_curated_entry_has_no_errors(curated_tree):
    market = marketplace(local_entry("alpha"), curated_entry())
    assert errors_for(market, curated_tree) == []


@pytest.mark.parametrize("field, value, message", [
    ("strict", True, '"strict": false'),
    ("defaultEnabled", True, '"defaultEnabled": false'),
    ("version", "", 'no "version"'),
    ("skills", [], 'no "skills" list'),
])
def test_a_curated_entry_missing_a_required_setting_is_reported(curated_tree, field, value, message):
    market = marketplace(local_entry("alpha"), curated_entry(**{field: value}))
    assert any(message in e for e in errors_for(market, curated_tree))


def test_a_curated_entry_without_strict_at_all_is_reported(curated_tree):
    entry = curated_entry()
    del entry["strict"]
    assert any('"strict": false' in e for e in errors_for(marketplace(local_entry("alpha"), entry), curated_tree))


@pytest.mark.parametrize("path", [
    "./",
    "./plugins",
    "./plugins/alpha",
    "./plugins/alpha/skills",
    "./plugins/alpha/skills/one/",
    "./plugins/../skills/one",
    "./plugins/alpha/skills/..",
    "plugins/alpha/skills/one",
    "./elsewhere/alpha/skills/one",
])
def test_a_curated_path_of_the_wrong_shape_is_reported(curated_tree, path):
    market = marketplace(local_entry("alpha"), curated_entry(skills=[path]))
    assert any("must be exactly" in e for e in errors_for(market, curated_tree))


def test_a_curated_path_with_no_skill_md_is_reported(curated_tree):
    market = marketplace(local_entry("alpha"), curated_entry(skills=["./plugins/alpha/skills/missing"]))
    assert any("has no SKILL.md" in e for e in errors_for(market, curated_tree))


def test_a_curated_path_listed_twice_is_reported(curated_tree):
    path = "./plugins/alpha/skills/one"
    market = marketplace(local_entry("alpha"), curated_entry(skills=[path, path]))
    assert any("more than once" in e for e in errors_for(market, curated_tree))


def test_a_curated_entry_shadowed_by_a_plugin_directory_is_reported(curated_tree):
    (curated_tree / "personal").mkdir()
    market = marketplace(local_entry("alpha"), curated_entry())
    assert any("cannot be both" in e for e in errors_for(market, curated_tree))


@pytest.mark.parametrize("key", [
    "hooks", "mcpServers", "lspServers", "agents", "commands", "workflows",
    "outputStyles", "monitors", "experimental", "settings", "userConfig",
    "channels", "dependencies", "headersHelper", "metadata", "not-a-real-key",
])
def test_a_curated_entry_carrying_a_non_allowlisted_key_is_reported(curated_tree, key):
    market = marketplace(local_entry("alpha"), curated_entry(**{key: {}}))
    errors = errors_for(market, curated_tree)
    assert any("may carry only" in e and repr(key) in e for e in errors), errors


def test_every_allowlisted_key_is_accepted(curated_tree):
    display = {key: "x" for key in cc.CURATED_DISPLAY_KEYS}
    market = marketplace(local_entry("alpha"), curated_entry(**display))
    assert errors_for(market, curated_tree) == []


@pytest.mark.parametrize("rel", cc.CURATED_ROOT_COMPONENTS)
def test_a_root_component_beside_a_curated_entry_is_reported(tmp_path, rel):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if "." in Path(rel).name:
        path.write_text("{}", encoding="utf-8")
    else:
        path.mkdir()
    found = []
    cc.check_curated_root_components(marketplace(curated_entry()), found, repo_root=tmp_path)
    assert len(found) == 1 and found[0].startswith(f"{rel} exists at the repository root")


def test_root_components_are_fine_without_a_curated_entry(tmp_path):
    (tmp_path / "hooks").mkdir()
    found = []
    cc.check_curated_root_components(marketplace(local_entry("alpha")), found, repo_root=tmp_path)
    assert found == []


def test_the_shipped_root_carries_no_default_component():
    found = []
    cc.check_curated_root_components(cc.load_marketplace(), found)
    assert found == []


def test_a_missing_declaration_reader_is_a_named_error(tmp_path):
    with pytest.raises(cc.AccountDeclarationReaderMissing, match="load_account_declaration"):
        cc._load_account_declaration(cc.ACCOUNT_SKILLS_PATH, reader_path=tmp_path / "absent.py")


def test_a_reader_without_the_function_is_a_named_error(tmp_path):
    reader = tmp_path / "sync_skills.py"
    reader.write_text("X = 1\n", encoding="utf-8")
    with pytest.raises(cc.AccountDeclarationReaderMissing, match="AttributeError"):
        cc._load_account_declaration(cc.ACCOUNT_SKILLS_PATH, reader_path=reader)


def test_check_account_plugin_reports_a_missing_reader_instead_of_crashing(curated_tree, monkeypatch):
    def missing(path):
        raise cc.AccountDeclarationReaderMissing("cannot load load_account_declaration()")
    monkeypatch.setattr(cc, "_load_account_declaration", missing)
    market = marketplace(account_entry(["./plugins/alpha/skills/one"]))
    found = []
    cc.check_account_plugin(market, found, plugins_dir=curated_tree)
    assert found == ["cannot load load_account_declaration()"]


def account_errors(market, plugins_dir, declared):
    found = []
    cc.check_account_plugin(market, found, plugins_dir=plugins_dir, declared=declared)
    return found


def account_entry(skills):
    return curated_entry(name=cc.ACCOUNT_PLUGIN, skills=skills)


def test_the_account_plugin_matching_its_declaration_passes(curated_tree):
    market = marketplace(account_entry(["./plugins/alpha/skills/one", "./plugins/alpha/skills/two"]))
    assert account_errors(market, curated_tree, {"one", "two"}) == []


def test_a_declared_skill_missing_from_the_account_plugin_is_reported(curated_tree):
    market = marketplace(account_entry(["./plugins/alpha/skills/one"]))
    errors = account_errors(market, curated_tree, {"one", "two"})
    assert errors == [
        f"marketplace.json entry '{cc.ACCOUNT_PLUGIN}' is missing "
        "'./plugins/alpha/skills/two', which account-skills.txt declares"
    ]


def test_an_undeclared_skill_in_the_account_plugin_is_reported(curated_tree):
    market = marketplace(account_entry(["./plugins/alpha/skills/one", "./plugins/alpha/skills/two"]))
    errors = account_errors(market, curated_tree, {"one"})
    assert any("does not declare" in e and "skills/two" in e for e in errors)


def test_a_declared_skill_with_no_directory_is_reported(curated_tree):
    market = marketplace(account_entry(["./plugins/alpha/skills/one"]))
    errors = account_errors(market, curated_tree, {"one", "ghost"})
    assert any("'ghost'" in e and "expected exactly 1" in e for e in errors)


def test_a_missing_account_plugin_is_reported(curated_tree):
    assert any("no 'adam-personal' entry" in e for e in account_errors(marketplace(), curated_tree, {"one"}))


def test_an_account_plugin_that_is_not_curated_is_reported(curated_tree):
    market = marketplace(local_entry(cc.ACCOUNT_PLUGIN))
    assert any("must have source './'" in e for e in account_errors(market, curated_tree, {"one"}))


# =================================================================================
# Shipped artifacts — these read the real repo on purpose
# =================================================================================


def test_shipped_repo_passes(capsys, monkeypatch):
    # The exact entry point CI runs, so the assertion cannot drift from the
    # set of checks main() actually performs.
    monkeypatch.setattr(sys, "argv", ["check_consistency.py"])
    cc.main()
    assert "OK:" in capsys.readouterr().out


def test_every_shipped_entry_classifies():
    for entry in cc.load_marketplace()["plugins"]:
        kind, detail = cc.classify_source(entry)
        assert kind in ("local", "federated", "curated"), "%s: %s" % (entry.get("name"), detail)


def _declared_account_skills():
    """account-skills.txt, parsed here independently of the checker: one name
    per line, `#` starts a comment, blanks ignored (the file's own header)."""
    names = set()
    for line in cc.ACCOUNT_SKILLS_PATH.read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            names.add(name)
    return names


def test_the_account_plugin_serves_exactly_the_declared_account_skills():
    # ADR 0012: account-skills.txt is the one declaration; the marketplace
    # entry must list exactly its skills, each resolved to the bundle it lives
    # in, and nothing else.
    raw = json.loads(cc.MARKETPLACE_PATH.read_text(encoding="utf-8"))
    entries = [e for e in raw["plugins"] if e["name"] == "adam-personal"]
    assert len(entries) == 1
    entry = entries[0]
    declared = _declared_account_skills()
    assert declared, "account-skills.txt declares nothing — this test would be vacuous"

    expected = set()
    for name in declared:
        matches = sorted(cc.PLUGINS_DIR.glob(f"*/skills/{name}/SKILL.md"))
        assert len(matches) == 1, name
        expected.add(f"./plugins/{matches[0].parent.parent.parent.name}/skills/{name}")

    assert len(entry["skills"]) == len(set(entry["skills"])), "duplicate skills path"
    assert set(entry["skills"]) == expected
    for path in entry["skills"]:
        assert (cc.REPO_ROOT / path / "SKILL.md").is_file(), path


def test_the_account_plugin_is_a_curated_opt_in_entry():
    raw = json.loads(cc.MARKETPLACE_PATH.read_text(encoding="utf-8"))
    entry = next(e for e in raw["plugins"] if e["name"] == "adam-personal")
    assert entry["source"] == "./"
    assert entry["strict"] is False
    assert entry["defaultEnabled"] is False
    assert cc.classify_source(entry) == ("curated", "./")
    assert "adam-personal" not in raw.get("renames", {})
    assert not (cc.PLUGINS_DIR / "adam-personal").exists()


def test_the_checker_reads_account_skills_txt_as_sync_skills_does():
    # The checker loads sync_skills.py's own reader; this pins that the two
    # parses of the shipped file agree.
    assert cc._load_account_declaration(cc.ACCOUNT_SKILLS_PATH) == _declared_account_skills()


def _federated_shaped_entries():
    """Every entry that is TRYING to be federated, however badly.

    Deliberately not `classify_source(...)[0] == "federated"`: the rules below
    are what make an entry classify, so selecting by classification would make
    each one vacuously true the moment it was broken — an entry with a bogus
    `ref` classifies "invalid" and would simply drop out of the loop.
    """
    return [
        entry
        for entry in cc.load_marketplace()["plugins"]
        if isinstance(entry.get("source"), dict) and entry["source"].get("source") == "github"
    ]


def test_this_repo_publishes_at_least_one_federated_bundle():
    # Guards every rule below from passing vacuously on an empty list.
    assert _federated_shaped_entries()


def test_no_federated_entry_carries_a_key_this_repo_cannot_stand_behind():
    # RULE-LEVEL on purpose. The predecessor asserted this against the literal
    # name "cms-platform", so the second federated bundle — the one nobody has
    # reviewed yet — would have inherited no rule at all.
    for entry in _federated_shaped_entries():
        assert set(entry["source"]) == set(cc.FEDERATED_SOURCE_FIELDS), (
            "%s: a federated source carries exactly %s. `path` is not a key of a "
            "github plugin source at all (the repo ROOT is the plugin root), and a "
            "ref/commit/version/sha here would read as a pin that nothing in this "
            "repo verifies — skills.lock is where a federated bundle gets pinned."
            % (entry.get("name"), ", ".join(cc.FEDERATED_SOURCE_FIELDS))
        )


def test_every_federated_entry_is_opt_in():
    # Also rule-level, and for the same reason. A federated bundle's contents
    # are reviewed in another repo and are not in this repo's skills.lock, and
    # every enabled skill costs always-on context in every session — so nothing
    # federated may arrive switched on.
    for entry in _federated_shaped_entries():
        assert entry.get("defaultEnabled") is False, entry.get("name")


def test_the_cms_platform_bundle_is_federated_from_its_own_repo():
    # Name-specific on purpose, and the one thing that SHOULD be: which repo
    # this particular bundle federates from is a per-artifact fact, not a rule.
    entries = {e["name"]: e for e in cc.load_marketplace()["plugins"]}
    assert cc.classify_source(entries["cms-platform"]) == (
        "federated",
        "Adam-S-Daniel/cms-platform",
    )


def test_the_federated_bundle_is_not_a_renames_key():
    # It is a NEW name, not a retired one; a key here would be resolved as a
    # historical alias and would also trip check_renames' collision rule.
    marketplace_json = cc.load_marketplace()
    assert "cms-platform" not in marketplace_json.get("renames", {})


def test_no_shipped_federated_bundle_is_shadowed_by_a_local_directory():
    for entry in cc.load_marketplace()["plugins"]:
        if cc.classify_source(entry)[0] == "federated":
            assert not (cc.PLUGINS_DIR / entry["name"]).exists()
