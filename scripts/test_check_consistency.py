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
        assert kind in ("local", "federated"), "%s: %s" % (entry.get("name"), detail)


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
