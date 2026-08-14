#!/usr/bin/env python3
"""Tests for scripts/check_agent_plugins.py.

Hermetic and deterministic: the machinery tests build throwaway bundle trees
under pytest's `tmp_path` and exercise the tool's public functions directly.
No network, no sleeps, no wall-clock dependence — the vendored schema is read
from disk, never fetched.

Two kinds of test live here, deliberately:
  * MACHINERY tests (most of the file) use synthetic bundles, so re-wording a
    shipped description can never break a test of the checking logic;
  * SHIPPED-ARTIFACT tests (the last section) read the real repo on purpose —
    they pin the contract itself: the vendored schema's identity, that the
    three bundles this repo actually ships are valid and mutually consistent,
    and that its federated marketplace entry is named in the output rather
    than skipped in silence.

Run: python3 -m pytest scripts/test_check_agent_plugins.py -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_agent_plugins as cap  # noqa: E402

# The other half of the pair. Imported so one test can assert the two scripts
# reach the SAME verdict on one tree, rather than each being checked alone and
# their disagreement going unnoticed (which is exactly what happened).
import check_consistency as cc  # noqa: E402


# =================================================================================
# Fixture builders
# =================================================================================

SCHEMA_URI = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"


def good_root(**overrides):
    """A minimal-but-realistic conformant root manifest."""
    manifest = {
        "$schema": SCHEMA_URI,
        "name": "demo",
        "version": "1.0.0",
        "description": "A demo bundle.",
        "author": {"name": "Demo Author"},
        "repository": "https://example.com/demo",
        "keywords": ["demo"],
    }
    manifest.update(overrides)
    return manifest


def good_claude(**overrides):
    """The Claude Code counterpart, agreeing with good_root() by default."""
    manifest = {
        "name": "demo",
        "version": "1.0.0",
        "description": "A demo bundle.",
        "author": {"name": "Demo Author"},
    }
    manifest.update(overrides)
    return manifest


def write_bundle(plugins_dir: Path, bundle_name: str, *, root=..., claude=...) -> Path:
    """Create plugins_dir/<bundle_name>/ with either/both manifest.

    Pass root=None or claude=None to omit that manifest; pass a string to
    write raw (possibly malformed) bytes instead of JSON.
    """
    bundle = plugins_dir / bundle_name
    bundle.mkdir(parents=True, exist_ok=True)

    if root is ...:
        root = good_root(name=bundle_name)
    if claude is ...:
        claude = good_claude(name=bundle_name)

    if root is not None:
        _write(bundle / "plugin.json", root)
    if claude is not None:
        (bundle / ".claude-plugin").mkdir(exist_ok=True)
        _write(bundle / ".claude-plugin" / "plugin.json", claude)
    return bundle


def _write(path: Path, payload):
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def schema():
    """The real vendored schema — the thing manifests are validated against."""
    return json.loads(cap.SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def plugins_dir(tmp_path):
    d = tmp_path / "plugins"
    d.mkdir()
    return d


def problems_for(bundle, schema):
    found = []
    cap.check_bundle(bundle, schema, found)
    return found


def write_marketplace(tmp_path: Path, *entries) -> Path:
    path = tmp_path / "marketplace.json"
    payload = {"name": "test-marketplace", "plugins": list(entries)}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def local_entry(name="demo"):
    return {"name": name, "source": "./plugins/%s" % name}


def federated_entry(name="remote", repo="Owner/Repo"):
    return {"name": name, "source": {"source": "github", "repo": repo}}


def coverage(bundles, marketplace_path):
    """(notices, problems) from check_marketplace_coverage."""
    problems = []
    notices = cap.check_marketplace_coverage(bundles, problems, marketplace_path=marketplace_path)
    return notices, problems


# =================================================================================
# Vendored-schema integrity
# =================================================================================


def test_integrity_accepts_the_shipped_schema():
    problems = []
    schema = cap.check_schema_integrity(problems)
    assert problems == []
    assert schema is not None
    assert schema["$id"] == SCHEMA_URI


def test_integrity_rejects_tampered_bytes(tmp_path):
    # Semantically harmless edit (a widened `name` maxLength) — the point is
    # that ANY change fails, not just a dangerous-looking one.
    tampered = json.loads(cap.SCHEMA_PATH.read_text(encoding="utf-8"))
    tampered["properties"]["name"]["maxLength"] = 9999
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")

    problems = []
    assert cap.check_schema_integrity(problems, schema_path=path) is None
    assert len(problems) == 1
    assert "sha256 mismatch" in problems[0]


def test_integrity_rejects_missing_schema(tmp_path):
    problems = []
    assert cap.check_schema_integrity(problems, schema_path=tmp_path / "absent.json") is None
    assert any("missing" in p for p in problems)


def test_integrity_rejects_unparseable_schema(tmp_path):
    path = tmp_path / "schema.json"
    payload = b"{not json"
    path.write_bytes(payload)
    import hashlib

    problems = []
    # Hash matches, so only the JSON parse can fail — proving the parse guard
    # is real and not shadowed by the hash check.
    result = cap.check_schema_integrity(
        problems, schema_path=path, expected=hashlib.sha256(payload).hexdigest()
    )
    assert result is None
    assert any("not valid JSON" in p for p in problems)


# =================================================================================
# Schema validation of the root manifest
# =================================================================================


def test_conformant_bundle_has_no_problems(plugins_dir, schema):
    bundle = write_bundle(plugins_dir, "demo")
    assert problems_for(bundle, schema) == []


@pytest.mark.parametrize(
    "extra_key, value",
    [
        ("category", "productivity"),  # marketplace-only concept
        ("defaultEnabled", False),  # marketplace-only concept
        ("skills", ["a", "b"]),  # no such field: skills are found by convention
        ("source", "./plugins/demo"),
    ],
)
def test_closed_schema_rejects_extra_top_level_keys(plugins_dir, schema, extra_key, value):
    bundle = write_bundle(plugins_dir, "demo", root=good_root(name="demo", **{extra_key: value}))
    problems = problems_for(bundle, schema)
    assert problems, "expected %r to be rejected by the closed schema" % extra_key
    assert any(extra_key in p for p in problems)


def test_author_is_closed_too(plugins_dir, schema):
    root = good_root(name="demo", author={"name": "Demo Author", "github": "demo"})
    bundle = write_bundle(plugins_dir, "demo", root=root)
    problems = problems_for(bundle, schema)
    assert any("github" in p for p in problems)


@pytest.mark.parametrize(
    "schema_value",
    [
        "https://agent-plugins.org/schemas/1.0.1/plugin.schema.json",
        "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json/",
        "http://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "",
    ],
)
def test_schema_uri_is_a_const(plugins_dir, schema, schema_value):
    # Codex rejects anything but the exact string as "unsupported Agent
    # Plugins schema", so a near-miss must fail here rather than at install.
    bundle = write_bundle(plugins_dir, "demo", root=good_root(name="demo", **{"$schema": schema_value}))
    assert problems_for(bundle, schema)


@pytest.mark.parametrize("missing", ["$schema", "name"])
def test_required_fields(plugins_dir, schema, missing):
    root = good_root(name="demo")
    del root[missing]
    bundle = write_bundle(plugins_dir, "demo", root=root)
    problems = problems_for(bundle, schema)
    assert any(missing in p for p in problems)


@pytest.mark.parametrize(
    "name",
    [
        "Demo",  # uppercase
        "-demo",  # leading separator
        "demo-",  # trailing separator
        "de--mo",  # doubled hyphen
        "de..mo",  # doubled dot
        "demo_bundle",  # underscore
        "",  # empty
        "x" * 65,  # over maxLength
    ],
)
def test_invalid_names_rejected(plugins_dir, schema, name):
    bundle = write_bundle(
        plugins_dir, "demo", root=good_root(name=name), claude=good_claude(name=name)
    )
    problems = problems_for(bundle, schema)
    assert problems, "expected name %r to be rejected" % name


@pytest.mark.parametrize("name", ["adam", "adam-local", "fastmail", "a", "a.b-c", "x" * 64])
def test_valid_names_accepted(plugins_dir, schema, name):
    bundle = write_bundle(
        plugins_dir, "demo", root=good_root(name=name), claude=good_claude(name=name)
    )
    assert problems_for(bundle, schema) == []


def test_optional_fields_may_be_omitted(plugins_dir, schema):
    bundle = write_bundle(
        plugins_dir,
        "demo",
        root={"$schema": SCHEMA_URI, "name": "demo"},
        claude={"name": "demo"},
    )
    assert problems_for(bundle, schema) == []


def test_unparseable_root_manifest_is_reported(plugins_dir, schema):
    bundle = write_bundle(plugins_dir, "demo", root="{ oops")
    problems = problems_for(bundle, schema)
    assert any("not valid JSON" in p for p in problems)


# =================================================================================
# Cross-manifest consistency
# =================================================================================


def test_name_disagreement_is_reported(plugins_dir, schema):
    bundle = write_bundle(
        plugins_dir, "demo", root=good_root(name="demo"), claude=good_claude(name="demo-old")
    )
    problems = problems_for(bundle, schema)
    assert any("'name'" in p for p in problems)


def test_version_disagreement_is_reported(plugins_dir, schema):
    bundle = write_bundle(
        plugins_dir, "demo", root=good_root(name="demo", version="1.1.0"),
        claude=good_claude(name="demo", version="1.0.0"),
    )
    problems = problems_for(bundle, schema)
    assert any("'version'" in p for p in problems)
    assert any("1.1.0" in p and "1.0.0" in p for p in problems)


def test_version_present_in_only_one_manifest_is_reported(plugins_dir, schema):
    claude = good_claude(name="demo")
    del claude["version"]
    bundle = write_bundle(plugins_dir, "demo", root=good_root(name="demo"), claude=claude)
    assert any("'version'" in p for p in problems_for(bundle, schema))


def test_description_may_differ_without_complaint(plugins_dir, schema):
    # Only name/version are cross-checked; prose is free to differ.
    bundle = write_bundle(
        plugins_dir,
        "demo",
        root=good_root(name="demo", description="One wording."),
        claude=good_claude(name="demo", description="Another wording."),
    )
    assert problems_for(bundle, schema) == []


def test_bundle_missing_its_root_manifest_is_reported(plugins_dir, schema):
    bundle = write_bundle(plugins_dir, "demo", root=None)
    problems = problems_for(bundle, schema)
    assert len(problems) == 1
    assert "no root plugin.json" in problems[0]


def test_bundle_missing_its_claude_manifest_is_reported(plugins_dir, schema):
    bundle = write_bundle(plugins_dir, "demo", claude=None)
    problems = problems_for(bundle, schema)
    assert any(".claude-plugin" in p for p in problems)


def test_unparseable_claude_manifest_is_reported(plugins_dir, schema):
    bundle = write_bundle(plugins_dir, "demo", claude="{ oops")
    assert any("not valid JSON" in p for p in problems_for(bundle, schema))


# =================================================================================
# Bundle discovery
# =================================================================================


def test_discovery_is_filesystem_derived_and_sorted(plugins_dir):
    write_bundle(plugins_dir, "zeta")
    write_bundle(plugins_dir, "alpha")
    assert [b.name for b in cap.discover_bundles(plugins_dir)] == ["alpha", "zeta"]


def test_discovery_finds_a_bundle_carrying_only_the_claude_manifest(plugins_dir):
    # This is the case that must NOT be skipped silently — it is exactly how a
    # new bundle would ship without a conformant manifest.
    write_bundle(plugins_dir, "half-built", root=None)
    assert [b.name for b in cap.discover_bundles(plugins_dir)] == ["half-built"]


def test_discovery_ignores_directories_with_neither_manifest(plugins_dir):
    (plugins_dir / "not-a-bundle").mkdir()
    (plugins_dir / "not-a-bundle" / "README.md").write_text("hi", encoding="utf-8")
    (plugins_dir / "stray.txt").write_text("hi", encoding="utf-8")
    assert cap.discover_bundles(plugins_dir) == []


def test_discovery_on_missing_plugins_dir(tmp_path):
    assert cap.discover_bundles(tmp_path / "absent") == []


# =================================================================================
# Marketplace coverage — the federated entry discover_bundles() cannot see
# =================================================================================


def test_a_locally_discovered_bundle_needs_no_notice(plugins_dir, tmp_path):
    bundle = write_bundle(plugins_dir, "demo")
    notices, problems = coverage([bundle], write_marketplace(tmp_path, local_entry("demo")))
    assert (notices, problems) == ([], [])


def test_a_federated_entry_is_named_rather_than_skipped(plugins_dir, tmp_path):
    # The gap being made visible: nothing here validates that repo's manifests,
    # and before this the script simply printed OK as though something had.
    path = write_marketplace(tmp_path, federated_entry("remote", "Owner/Repo"))
    notices, problems = coverage([], path)
    assert problems == []
    assert len(notices) == 1
    assert "remote" in notices[0] and "Owner/Repo" in notices[0]


def test_a_federated_entry_shadowed_by_a_discovered_bundle_is_a_problem(plugins_dir, tmp_path):
    # The two-scripts-disagree case. Before this, the same run printed
    # "federated … validated by that repo's own CI, not here" AND counted the
    # shadowing bundle among the manifests it had just validated HERE, exit 0 —
    # while check_consistency.py rejected the identical tree.
    bundle = write_bundle(plugins_dir, "remote")
    notices, problems = coverage([bundle], write_marketplace(tmp_path, federated_entry("remote")))
    assert notices == []
    assert len(problems) == 1
    assert "remote" in problems[0] and "Owner/Repo" in problems[0]


def test_the_shadowing_verdict_matches_check_consistency(plugins_dir, tmp_path):
    # Pins the agreement itself, not just each script's own behaviour: one tree,
    # two scripts, both must object.
    write_bundle(plugins_dir, "remote")
    entry = federated_entry("remote")
    _, problems = coverage(cap.discover_bundles(plugins_dir), write_marketplace(tmp_path, entry))
    consistency_errors = []
    cc.check_marketplace_entries({"plugins": [entry]}, consistency_errors, plugins_dir=plugins_dir)
    assert problems and consistency_errors


def test_a_local_entry_with_no_discovered_bundle_fails(plugins_dir, tmp_path):
    # Published by the marketplace, validated by nothing — the case a silent
    # skip would hide just as thoroughly as it hid the federated one.
    _, problems = coverage([], write_marketplace(tmp_path, local_entry("ghost")))
    assert len(problems) == 1
    assert "ghost" in problems[0]


def test_an_entry_that_is_neither_local_nor_federated_fails(tmp_path):
    entry = {"name": "mystery", "source": {"source": "gitlab", "repo": "o/r"}}
    _, problems = coverage([], write_marketplace(tmp_path, entry))
    assert len(problems) == 1
    assert "mystery" in problems[0]


def test_a_missing_marketplace_fails(tmp_path):
    _, problems = coverage([], tmp_path / "absent.json")
    assert any("missing" in p for p in problems)


def test_an_unparseable_marketplace_fails(tmp_path):
    path = tmp_path / "marketplace.json"
    path.write_text("{ oops", encoding="utf-8")
    _, problems = coverage([], path)
    assert any("not valid JSON" in p for p in problems)


def test_coverage_reports_every_kind_at_once(plugins_dir, tmp_path):
    bundle = write_bundle(plugins_dir, "demo")
    path = write_marketplace(
        tmp_path, local_entry("demo"), federated_entry("remote"), local_entry("ghost")
    )
    notices, problems = coverage([bundle], path)
    assert len(notices) == 1 and len(problems) == 1


# =================================================================================
# Reporting
# =================================================================================


def test_schema_violation_does_not_suppress_the_cross_manifest_check(plugins_dir, schema):
    # An invalid manifest must not short-circuit the pair check, or fixing the
    # schema error would just reveal a second round of failures.
    root = good_root(name="demo", category="productivity", defaultEnabled=True)
    bundle = write_bundle(
        plugins_dir, "demo", root=root, claude=good_claude(name="demo", version="9.9.9")
    )
    problems = problems_for(bundle, schema)
    assert any("Additional properties" in p for p in problems)
    assert any("'version'" in p for p in problems)


def test_all_offending_extra_keys_are_named(plugins_dir, schema):
    # jsonschema coalesces additionalProperties into ONE message; assert it
    # still names every offending key, so a fix list is complete on first read.
    root = good_root(name="demo", category="productivity", defaultEnabled=True)
    bundle = write_bundle(plugins_dir, "demo", root=root)
    problems = problems_for(bundle, schema)
    assert len(problems) == 1
    assert "category" in problems[0] and "defaultEnabled" in problems[0]


def test_problems_from_several_bundles_accumulate(plugins_dir, schema):
    write_bundle(plugins_dir, "one", root=good_root(name="one", category="x"))
    write_bundle(plugins_dir, "two", root=None)
    problems = []
    for bundle in cap.discover_bundles(plugins_dir):
        cap.check_bundle(bundle, schema, problems)
    assert len(problems) >= 2


# =================================================================================
# Shipped artifacts — these read the real repo on purpose
# =================================================================================


def test_shipped_schema_matches_recorded_provenance():
    import hashlib

    raw = cap.SCHEMA_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == cap.SCHEMA_SHA256
    assert len(raw) == 1805


def test_shipped_repo_passes(capsys):
    assert cap.main() == 0
    assert "OK:" in capsys.readouterr().out


def test_shipped_run_names_every_federated_entry_it_did_not_validate(capsys):
    # A skip is acceptable here only because it is NAMED. If the notice ever
    # stops printing, the run goes back to claiming coverage it does not have.
    assert cap.main() == 0
    out = capsys.readouterr().out
    federated = [
        (entry["name"], cap.classify_source(entry)[1])
        for entry in json.loads(cap.MARKETPLACE_PATH.read_text(encoding="utf-8"))["plugins"]
        if cap.classify_source(entry)[0] == "federated"
    ]
    assert federated, "expected this repo to publish at least one federated bundle"
    for name, repo in federated:
        assert "NOTE: %s: federated from %s" % (name, repo) in out


def test_every_shipped_bundle_has_both_manifests():
    bundles = cap.discover_bundles()
    assert {b.name for b in bundles} == {"adam", "adam-local", "fastmail"}
    for bundle in bundles:
        assert (bundle / "plugin.json").is_file()
        assert (bundle / ".claude-plugin" / "plugin.json").is_file()


def test_shipped_manifests_carry_the_exact_schema_const():
    for bundle in cap.discover_bundles():
        manifest = json.loads((bundle / "plugin.json").read_text(encoding="utf-8"))
        assert manifest["$schema"] == SCHEMA_URI


def test_shipped_manifests_omit_marketplace_only_keys():
    # These live in .claude-plugin/marketplace.json and would make the root
    # manifest invalid under the closed schema.
    for bundle in cap.discover_bundles():
        manifest = json.loads((bundle / "plugin.json").read_text(encoding="utf-8"))
        assert "category" not in manifest
        assert "defaultEnabled" not in manifest
        assert "skills" not in manifest


def test_shipped_manifests_assert_no_license():
    # The repo ships no LICENSE file, so declaring one would be a false claim.
    assert not (cap.REPO_ROOT / "LICENSE").exists()
    for bundle in cap.discover_bundles():
        manifest = json.loads((bundle / "plugin.json").read_text(encoding="utf-8"))
        assert "license" not in manifest
