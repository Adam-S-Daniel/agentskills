#!/usr/bin/env python3
"""Tests for scripts/check_skills.py.

Hermetic and deterministic: every fixture is a throwaway registry tree built under
pytest's `tmp_path`, and the tool is exercised through its public functions
(`check_skills.run` / `check_skills.main`) rather than a subprocess. No network, no
sleeps, no wall-clock dependence.

The test config deliberately declares its OWN field list, limits and pattern rather than
reading `skills_registries.yml`: the shipped values are provisional, and nothing here may
break when they are re-tuned.

Run: python3 -m pytest scripts/test_check_skills.py -q
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_skills  # noqa: E402


# =================================================================================
# Fixture builders
# =================================================================================

GOOD_FRONTMATTER = "---\nname: {name}\ndescription: A well formed skill.\n---\n"


def write_skill(
    registry_root: Path,
    rel_skill_dir: str,
    *,
    frontmatter: str = None,
    name: str = None,
    body: str = "",
    newline: str = "\n",
) -> Path:
    """Create <registry_root>/<rel_skill_dir>/SKILL.md and return the skill directory."""
    skill_dir = registry_root / rel_skill_dir
    skill_dir.mkdir(parents=True, exist_ok=True)
    if frontmatter is None:
        frontmatter = GOOD_FRONTMATTER.format(name=name or skill_dir.name)
    text = frontmatter + body
    (skill_dir / "SKILL.md").write_bytes(text.replace("\n", newline).encode("utf-8"))
    return skill_dir


def registry_entry(name: str, path: str, layout: str = "skills/*/SKILL.md", **extra) -> dict:
    entry = {"name": name, "path": path, "layout": layout}
    entry.update(extra)
    return entry


def write_config(path: Path, registries, **extra) -> Path:
    data = {
        "required_fields": ["name", "description"],
        "name_pattern": "^[a-z0-9]+(-[a-z0-9]+)*$",
        "max_lengths": {"name": 64, "description": 200},
        "known_fields": ["name", "description", "compatibility"],
        "payload_dirs": ["scripts", "references", "assets", "templates", "hooks",
                         "tests", "examples"],
        "registries": list(registries),
    }
    data.update(extra)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def run_tool(tmp_path: Path, registries, *, waivers=None, repo_root: Path = None, **extra):
    config = write_config(tmp_path / "config.yml", registries, **extra)
    waivers_path = tmp_path / "waivers.yml"
    waivers_path.write_text(yaml.safe_dump({"waivers": list(waivers or [])}), encoding="utf-8")
    return check_skills.run(config, waivers_path, {}, repo_root=repo_root or tmp_path)


def klasses(report) -> list:
    return sorted(finding.klass for finding in report.errors)


def messages(report, klass: str) -> list:
    return [f.message for f in report.errors if f.klass == klass]


@pytest.fixture
def local_registry(tmp_path):
    """A single registry declared as `path: .`, resolved against the tmp repo root."""
    return [registry_entry("alpha", ".")]


# =================================================================================
# Clean baseline
# =================================================================================

def test_good_tree_produces_no_findings_and_exits_zero(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []
    assert report.waived == []
    assert report.exit_code == 0
    assert report.skills_scanned == 1
    assert "OK: 1 skills across 1 registries — 0 findings, 0 waived." in \
        check_skills.render_text(report, False)


# =================================================================================
# Per-file frontmatter checks — each fires on a bad fixture, not on a good one
# =================================================================================

def test_frontmatter_missing(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(tmp_path, "skills/no-frontmatter", frontmatter="# Just a heading\n")
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_FRONTMATTER_MISSING]
    assert [f.path for f in fired] == ["skills/no-frontmatter/SKILL.md"]


def test_frontmatter_missing_when_block_is_unterminated(tmp_path, local_registry):
    write_skill(tmp_path, "skills/unterminated", frontmatter="---\nname: unterminated\n")
    report = run_tool(tmp_path, local_registry)
    assert check_skills.K_FRONTMATTER_MISSING in klasses(report)


def test_yaml_parse_reports_first_line_and_position(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(
        tmp_path, "skills/bad-yaml",
        frontmatter='---\nname: bad-yaml\ndescription: a "quoted: thing" that: breaks\n---\n',
    )
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_YAML_PARSE]
    assert [f.path for f in fired] == ["skills/bad-yaml/SKILL.md"]
    message = fired[0].message
    assert "\n" not in message                      # first line only
    assert "frontmatter line" in message and "column" in message
    assert "SKILL.md line" in message


def test_frontmatter_not_map(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(tmp_path, "skills/listy", frontmatter="---\n- name: listy\n---\n")
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_FRONTMATTER_NOT_MAP]
    assert [f.path for f in fired] == ["skills/listy/SKILL.md"]
    assert "list" in fired[0].message


def test_missing_field_fires_for_absent_and_whitespace_only(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(tmp_path, "skills/no-desc", frontmatter="---\nname: no-desc\n---\n")
    write_skill(tmp_path, "skills/blank-desc",
                frontmatter='---\nname: blank-desc\ndescription: "   "\n---\n')
    report = run_tool(tmp_path, local_registry)
    fired = {f.path for f in report.errors if f.klass == check_skills.K_MISSING_FIELD}
    assert fired == {"skills/no-desc/SKILL.md", "skills/blank-desc/SKILL.md"}


def test_name_dir_mismatch(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(tmp_path, "skills/on-disk", name="in-frontmatter")
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_NAME_DIR_MISMATCH]
    assert [f.path for f in fired] == ["skills/on-disk/SKILL.md"]
    assert "in-frontmatter" in fired[0].message and "on-disk" in fired[0].message


def test_name_pattern(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(tmp_path, "skills/Bad_Name")
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_NAME_PATTERN]
    assert [f.path for f in fired] == ["skills/Bad_Name/SKILL.md"]


def test_length_limit_states_actual_length_and_limit(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    long_description = "x" * 250
    write_skill(tmp_path, "skills/verbose",
                frontmatter=f"---\nname: verbose\ndescription: {long_description}\n---\n")
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_LENGTH_LIMIT]
    assert [f.path for f in fired] == ["skills/verbose/SKILL.md"]
    assert "250" in fired[0].message and "200" in fired[0].message


def test_length_limit_is_config_driven_not_hardcoded(tmp_path, local_registry):
    write_skill(tmp_path, "skills/verbose",
                frontmatter=f"---\nname: verbose\ndescription: {'x' * 250}\n---\n")
    relaxed = run_tool(tmp_path, local_registry, max_lengths={"description": 400})
    assert check_skills.K_LENGTH_LIMIT not in klasses(relaxed)
    strict = run_tool(tmp_path, local_registry, max_lengths={"description": 10})
    assert check_skills.K_LENGTH_LIMIT in klasses(strict)


def test_non_spec_field(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    write_skill(
        tmp_path, "skills/extra",
        frontmatter="---\nname: extra\ndescription: Has an extra key.\nbogus: 1\n---\n",
    )
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_NON_SPEC_FIELD]
    assert [f.path for f in fired] == ["skills/extra/SKILL.md"]
    assert "bogus" in fired[0].message


# =================================================================================
# dangling-payload-ref
# =================================================================================

FENCED_BODY = "\nRun it:\n\n```bash\npython scripts/x.py --schedule regular\n```\n"


def test_dangling_payload_ref_fires_for_absent_fenced_script(tmp_path, local_registry):
    write_skill(tmp_path, "skills/needy", body=FENCED_BODY)
    report = run_tool(tmp_path, local_registry)
    fired = [f for f in report.errors if f.klass == check_skills.K_DANGLING_PAYLOAD_REF]
    assert len(fired) == 1
    assert "scripts/x.py" in fired[0].message


def test_dangling_payload_ref_silent_when_the_file_exists(tmp_path, local_registry):
    skill_dir = write_skill(tmp_path, "skills/needy", body=FENCED_BODY)
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "x.py").write_text("print('hi')\n", encoding="utf-8")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []


def test_dangling_payload_ref_strips_a_leading_dot_slash(tmp_path, local_registry):
    write_skill(tmp_path, "skills/dotslash", body="\n```bash\npython ./scripts/z.py\n```\n")
    report = run_tool(tmp_path, local_registry)
    found = messages(report, check_skills.K_DANGLING_PAYLOAD_REF)
    assert len(found) == 1
    assert "'scripts/z.py'" in found[0]


def test_dangling_payload_ref_strips_trailing_punctuation(tmp_path, local_registry):
    write_skill(tmp_path, "skills/punct", body=(
        "\n```bash\n"
        "python scripts/w.py.\n"
        "bash scripts/v.sh,\n"
        "cat scripts/u.txt;\n"
        "```\n"
    ))
    report = run_tool(tmp_path, local_registry)
    found = sorted(messages(report, check_skills.K_DANGLING_PAYLOAD_REF))
    assert len(found) == 3
    assert any("'scripts/w.py'" in m for m in found)
    assert any("'scripts/v.sh'" in m for m in found)
    assert any("'scripts/u.txt'" in m for m in found)


def test_dangling_payload_ref_dedupes_within_one_skill(tmp_path, local_registry):
    write_skill(tmp_path, "skills/repeat", body=(
        "\n```bash\npython scripts/x.py\npython scripts/x.py\npython ./scripts/x.py\n```\n"))
    report = run_tool(tmp_path, local_registry)
    assert len(messages(report, check_skills.K_DANGLING_PAYLOAD_REF)) == 1


# ---------------------------------------------------------------------------------
# Precision guards: the classes that deliberately do NOT gate. Each is a real
# false-positive shape observed across the three live registries.
# ---------------------------------------------------------------------------------

def test_fenced_reference_to_an_existing_directory_is_not_a_finding(tmp_path, local_registry):
    skill_dir = write_skill(tmp_path, "skills/dirref", body="\n```bash\nls assets/img\n```\n")
    (skill_dir / "assets" / "img").mkdir(parents=True)
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []


def test_bare_directory_with_a_trailing_slash_does_not_gate(tmp_path, local_registry):
    write_skill(tmp_path, "skills/baredir", body="\n```bash\nls scripts/\n```\n")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []
    assert _dismissed(report)["scripts/"] == "trailing-slash"


def test_angle_bracket_placeholder_does_not_gate(tmp_path, local_registry):
    write_skill(tmp_path, "skills/placeholder", body="\n```bash\nbash scripts/<name>.sh\n```\n")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []
    assert _dismissed(report)["scripts/<name>.sh"] == "placeholder"


def test_prose_backtick_reference_does_not_gate(tmp_path, local_registry):
    write_skill(tmp_path, "skills/prosey",
                body="\nThe sibling skill ships `scripts/absent.py`, which lives elsewhere.\n")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []
    assert _dismissed(report)["scripts/absent.py"] == check_skills.PROSE_ONLY_RULE


def test_prose_markdown_link_target_does_not_gate(tmp_path, local_registry):
    write_skill(tmp_path, "skills/linky", body="\nSee [the helper](scripts/y.py) for detail.\n")
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []
    assert _dismissed(report)["scripts/y.py"] == check_skills.PROSE_ONLY_RULE


def test_a_reference_in_both_prose_and_a_fenced_block_still_gates(tmp_path, local_registry):
    write_skill(tmp_path, "skills/both", body=(
        "\nThe entry point is `scripts/x.py`.\n\n```bash\npython scripts/x.py\n```\n"))
    report = run_tool(tmp_path, local_registry)
    assert len(messages(report, check_skills.K_DANGLING_PAYLOAD_REF)) == 1


def test_an_indented_fence_inside_a_list_item_still_gates(tmp_path, local_registry):
    # Fences nested in a list item are indented; treating only column-0 fences as fenced
    # would silently drop this whole class.
    write_skill(tmp_path, "skills/nested", body=(
        "\n- **The tool** — run it like so:\n\n  ```bash\n  node scripts/tool.js --fix\n  ```\n"))
    report = run_tool(tmp_path, local_registry)
    found = messages(report, check_skills.K_DANGLING_PAYLOAD_REF)
    assert len(found) == 1 and "'scripts/tool.js'" in found[0]


NON_CANDIDATES = {
    "https://example.com/scripts/x.py": "url-scheme",
    "/etc/passwd": "absolute-path",
    "~/.claude/skills/foo": "home-relative",
    "../other/scripts/x.py": "parent-traversal",
    "scripts/*.py": "glob-metacharacter",
    "${VAR}/scripts/x.py": "placeholder",
    "scripts/<name>.sh": "placeholder",
    ".github/workflows/ci.yml": "not-payload-dir",
    "scripts": "no-slash",
    "scripts/": "trailing-slash",
}


def _dismissed(report) -> dict:
    """Map every dismissed candidate value -> the rule that dismissed it."""
    out = {}
    for _skill, candidates in report.dismissed:
        for candidate in candidates:
            out[candidate.value] = candidate.dismissed_by
    return out


def test_dangling_payload_ref_dismisses_non_candidates_with_the_expected_rule(
    tmp_path, local_registry
):
    # Inside a fenced block, so it is the STRUCTURAL rules under test, not prose-only.
    body = "\n```bash\n" + "\n".join(NON_CANDIDATES) + "\n```\n"
    write_skill(tmp_path, "skills/tricky", body=body)
    report = run_tool(tmp_path, local_registry)
    assert report.errors == []

    dismissed = _dismissed(report)
    for raw, expected_rule in NON_CANDIDATES.items():
        value = check_skills.normalise_candidate(raw)
        assert dismissed.get(value) == expected_rule, (raw, value, dismissed.get(value))


def test_dismissed_candidates_are_listed_for_audit(tmp_path, local_registry):
    write_skill(tmp_path, "skills/tricky",
                body="\nSee `scripts/*.py` for the glob and `scripts/absent.py` for the ref.\n")
    report = run_tool(tmp_path, local_registry)
    text = check_skills.render_text(report, True)
    assert "DISMISSED PAYLOAD CANDIDATES" in text
    assert "glob-metacharacter" in text and "scripts/*.py" in text
    # The recall gap the precision trade gives up is named, not silently dropped.
    assert check_skills.PROSE_ONLY_RULE in text and "scripts/absent.py" in text
    # ...and stays out of the way unless asked for.
    assert "DISMISSED PAYLOAD CANDIDATES" not in check_skills.render_text(report, False)


def test_code_block_tokenisation_keeps_only_the_path_token(tmp_path):
    fenced, prose = check_skills.split_code_regions(
        "before `scripts/inline.py`\n"
        "```bash\npython scripts/next_break.py --schedule regular\n```\nafter\n"
    )
    assert fenced == ["python", "scripts/next_break.py", "--schedule", "regular"]
    assert prose == ["scripts/inline.py"]
    candidates = check_skills.extract_candidates(
        "```bash\npython scripts/next_break.py --schedule regular\n```\n", ["scripts"]
    )
    qualifying = [c.value for c in candidates if c.dismissed_by is None]
    assert qualifying == ["scripts/next_break.py"]


def test_commonmark_block_forms_the_regex_scanner_could_not_see(tmp_path):
    """~~~ fences, info strings, and 4-space indented code blocks are all code."""
    fenced, _prose = check_skills.split_code_regions(
        "~~~python\nrun scripts/tilde.py\n~~~\n\n    run scripts/indented.py\n"
    )
    assert "scripts/tilde.py" in fenced
    assert "scripts/indented.py" in fenced


def test_link_nested_inside_emphasis_is_still_found(tmp_path):
    _fenced, prose = check_skills.split_code_regions("*See [it](scripts/nested.py) here.*\n")
    assert prose == ["scripts/nested.py"]


def test_inline_walk_recurses_into_nested_children():
    """The commonmark preset emits a FLAT inline stream, so no document exercises the
    recursive descent — but `children` can nest (plugins, future presets), and a
    non-recursive walk would silently drop those. Driven with synthetic tokens because
    that is the only way to reach the branch."""
    from markdown_it.token import Token

    span = Token("code_inline", "code", 0)
    span.content = "scripts/deep.py"
    link = Token("link_open", "a", 1)
    link.attrSet("href", "scripts/deep-link.py")
    inner = Token("inline", "", 0)
    inner.children = [span, link]
    root = Token("inline", "", 0)
    root.children = [inner]

    collected = []
    check_skills._walk_inline(root, collected)
    assert collected == ["scripts/deep.py", "scripts/deep-link.py"]


# =================================================================================
# Cross-registry basename census
# =================================================================================

def test_crlf_only_difference_classifies_as_mirror(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    write_skill(left, "skills/shared", body="\nSame content.\n")
    write_skill(right, "skills/shared", body="\nSame content.\n", newline="\r\n")
    report = run_tool(tmp_path, [registry_entry("left", "left"), registry_entry("right", "right")])

    groups = {group.basename: group for group in report.duplicate_groups}
    assert groups["shared"].verdict == "mirror"
    sizes = {loc.size for loc in groups["shared"].locations}
    assert len(sizes) == 2, "fixture must actually differ in raw bytes"
    assert len({loc.sha256 for loc in groups["shared"].locations}) == 2
    assert len({loc.sha256_nocr for loc in groups["shared"].locations}) == 1
    assert "mirror" in messages(report, check_skills.K_UNDECLARED_DUPLICATE)[0]


def test_genuinely_different_copies_classify_as_fork(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    write_skill(left, "skills/shared", body="\nOne body.\n")
    write_skill(right, "skills/shared", body="\nA different body.\n")
    report = run_tool(tmp_path, [registry_entry("left", "left"), registry_entry("right", "right")])
    assert report.duplicate_groups[0].verdict == "fork"
    assert "fork" in messages(report, check_skills.K_UNDECLARED_DUPLICATE)[0]


def test_duplicates_within_one_registry_are_not_a_cross_registry_finding(tmp_path):
    solo = tmp_path / "solo"
    write_skill(solo, "a/skills/shared")
    write_skill(solo, "b/skills/shared")
    report = run_tool(tmp_path, [registry_entry("solo", "solo", layout="*/skills/*/SKILL.md")])
    assert report.duplicate_groups == []
    assert check_skills.K_UNDECLARED_DUPLICATE not in klasses(report)


def test_undeclared_duplicate_is_keyed_by_basename_for_waivers(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    write_skill(left, "skills/shared")
    write_skill(right, "skills/shared")
    waiver = {"klass": check_skills.K_UNDECLARED_DUPLICATE, "registry": "left",
              "path": "shared", "reason": "mirrored on purpose",
              "issue": "https://example.com/issues/1"}
    report = run_tool(
        tmp_path,
        [registry_entry("left", "left"), registry_entry("right", "right")],
        waivers=[waiver],
    )
    assert report.errors == []
    assert len(report.waived) == 1
    assert report.exit_code == 0


# =================================================================================
# Registry resolution
# =================================================================================

def test_existing_but_empty_registry_scans_zero_skills_and_is_not_an_error(tmp_path):
    empty = tmp_path / "empty"
    (empty / "plugins" / "adam-private" / "skills").mkdir(parents=True)
    (empty / "plugins" / "adam-private" / "skills" / ".gitkeep").write_text("", encoding="utf-8")
    report = run_tool(
        tmp_path,
        [registry_entry("private", "empty", layout="plugins/*/skills/*/SKILL.md")],
    )
    assert report.errors == []
    assert report.exit_code == 0
    assert report.registries[0].status == "scanned"
    assert report.registries[0].skills_scanned == 0
    # Not silently passing: the empty registry is still named and counted in the report.
    text = check_skills.render_text(report, False)
    assert "private" in text and "0 skills" in text
    assert "OK: 0 skills across 1 registries" in text


def test_missing_required_registry_is_an_error(tmp_path):
    report = run_tool(tmp_path, [registry_entry("gone", "nowhere")])
    assert klasses(report) == [check_skills.K_REGISTRY_UNRESOLVED]
    assert report.unresolved_required == 1
    assert report.exit_code == 1
    assert report.registries[0].status == "unresolved"


def test_optional_registry_without_a_reason_is_still_an_error(tmp_path):
    report = run_tool(tmp_path, [registry_entry("gone", "nowhere", optional=True)])
    assert klasses(report) == [check_skills.K_REGISTRY_UNRESOLVED]
    assert report.exit_code == 1


def test_missing_optional_registry_with_a_reason_is_skipped(tmp_path):
    report = run_tool(
        tmp_path,
        [registry_entry("gone", "nowhere", optional=True, reason="cloned only in CI")],
    )
    assert report.errors == []
    assert report.exit_code == 0
    assert report.registries[0].status == "skipped"
    assert report.skips == ["SKIPPED: gone — cloned only in CI"]
    text = check_skills.render_text(report, False)
    assert "SKIPPED: gone — cloned only in CI" in text
    assert "SKIPPED (1)" in text


def test_registry_override_wins_over_the_configured_path(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    write_skill(elsewhere, "skills/moved")
    config = write_config(tmp_path / "config.yml", [registry_entry("alpha", "nowhere")])
    waivers = tmp_path / "waivers.yml"
    waivers.write_text(yaml.safe_dump({"waivers": []}), encoding="utf-8")
    report = check_skills.run(config, waivers, {"alpha": "elsewhere"}, repo_root=tmp_path)
    assert report.errors == []
    assert report.registries[0].overridden is True
    assert report.registries[0].skills_scanned == 1


def test_unknown_registry_override_is_rejected(tmp_path):
    config = write_config(tmp_path / "config.yml", [registry_entry("alpha", ".")])
    waivers = tmp_path / "waivers.yml"
    waivers.write_text(yaml.safe_dump({"waivers": []}), encoding="utf-8")
    with pytest.raises(SystemExit):
        check_skills.run(config, waivers, {"typo": "."}, repo_root=tmp_path)


# =================================================================================
# Waivers
# =================================================================================

def _non_spec_waiver(**extra):
    waiver = {
        "klass": check_skills.K_NON_SPEC_FIELD,
        "registry": "alpha",
        "path": "skills/extra/SKILL.md",
        "reason": "tolerated until the field is spec'd",
        "issue": "https://example.com/issues/55",
    }
    waiver.update(extra)
    return waiver


def _write_extra_field_skill(tmp_path):
    write_skill(
        tmp_path, "skills/extra",
        frontmatter="---\nname: extra\ndescription: Has an extra key.\nbogus: 1\n---\n",
    )


def test_waiver_suppresses_its_finding_and_reprints_it(tmp_path, local_registry):
    _write_extra_field_skill(tmp_path)
    report = run_tool(tmp_path, local_registry, waivers=[_non_spec_waiver()])
    assert report.errors == []
    assert len(report.waived) == 1
    assert report.waived[0][0].klass == check_skills.K_NON_SPEC_FIELD
    assert report.exit_code == 0
    text = check_skills.render_text(report, False)
    assert "WAIVED (1)" in text
    assert "https://example.com/issues/55" in text
    assert "bogus" in text, "a waived finding is re-printed, never hidden"


def test_waiver_match_substring_must_appear_in_the_message(tmp_path, local_registry):
    _write_extra_field_skill(tmp_path)
    hit = run_tool(tmp_path, local_registry, waivers=[_non_spec_waiver(match="bogus")])
    assert hit.errors == [] and len(hit.waived) == 1

    miss = run_tool(tmp_path, local_registry, waivers=[_non_spec_waiver(match="something-else")])
    assert sorted(klasses(miss)) == [check_skills.K_NON_SPEC_FIELD, check_skills.K_STALE_WAIVER]
    assert miss.exit_code == 1


def test_waiver_matching_nothing_is_a_stale_waiver_and_fails_the_run(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    report = run_tool(tmp_path, local_registry, waivers=[_non_spec_waiver()])
    assert klasses(report) == [check_skills.K_STALE_WAIVER]
    assert report.stale_waiver_count == 1
    assert report.exit_code == 1
    assert "skills/extra/SKILL.md" in report.errors[0].message


def test_waiver_is_scoped_to_its_registry_and_path(tmp_path, local_registry):
    _write_extra_field_skill(tmp_path)
    wrong_registry = run_tool(tmp_path, local_registry,
                              waivers=[_non_spec_waiver(registry="beta")])
    assert check_skills.K_NON_SPEC_FIELD in klasses(wrong_registry)
    assert check_skills.K_STALE_WAIVER in klasses(wrong_registry)

    wrong_path = run_tool(tmp_path, local_registry,
                          waivers=[_non_spec_waiver(path="skills/other/SKILL.md")])
    assert check_skills.K_NON_SPEC_FIELD in klasses(wrong_path)
    assert check_skills.K_STALE_WAIVER in klasses(wrong_path)


def test_malformed_waiver_is_reported_not_ignored(tmp_path, local_registry):
    write_skill(tmp_path, "skills/good-skill")
    broken = _non_spec_waiver()
    del broken["issue"]
    report = run_tool(tmp_path, local_registry, waivers=[broken])
    assert klasses(report) == [check_skills.K_WAIVER_INVALID]
    assert "issue" in report.errors[0].message
    assert report.exit_code == 1


def test_absent_waivers_file_means_nothing_is_waived(tmp_path, local_registry):
    _write_extra_field_skill(tmp_path)
    config = write_config(tmp_path / "config.yml", local_registry)
    report = check_skills.run(config, tmp_path / "no-such-waivers.yml", {}, repo_root=tmp_path)
    assert report.waivers_present is False
    assert check_skills.K_NON_SPEC_FIELD in klasses(report)


# =================================================================================
# Exit codes and output modes (through main())
# =================================================================================

def _absolute_registry_config(tmp_path, registry_root: Path) -> Path:
    return write_config(tmp_path / "config.yml",
                        [registry_entry("alpha", str(registry_root))])


def _empty_waivers(tmp_path) -> Path:
    path = tmp_path / "waivers.yml"
    path.write_text(yaml.safe_dump({"waivers": []}), encoding="utf-8")
    return path


def test_main_exits_zero_on_a_clean_tree(tmp_path, capsys):
    registry_root = tmp_path / "reg"
    write_skill(registry_root, "skills/good-skill")
    code = check_skills.main([
        "--config", str(_absolute_registry_config(tmp_path, registry_root)),
        "--waivers", str(_empty_waivers(tmp_path)),
    ])
    assert code == 0
    assert "OK: 1 skills across 1 registries — 0 findings, 0 waived." in capsys.readouterr().out


def test_main_exits_one_on_a_single_finding(tmp_path, capsys):
    registry_root = tmp_path / "reg"
    write_skill(registry_root, "skills/on-disk", name="in-frontmatter")
    code = check_skills.main([
        "--config", str(_absolute_registry_config(tmp_path, registry_root)),
        "--waivers", str(_empty_waivers(tmp_path)),
    ])
    assert code == 1
    out = capsys.readouterr().out
    assert "ERRORS (1)" in out
    assert check_skills.K_NAME_DIR_MISMATCH in out


def test_main_json_mode_emits_exactly_one_json_object(tmp_path, capsys):
    registry_root = tmp_path / "reg"
    write_skill(registry_root, "skills/on-disk", name="in-frontmatter")
    code = check_skills.main([
        "--config", str(_absolute_registry_config(tmp_path, registry_root)),
        "--waivers", str(_empty_waivers(tmp_path)),
        "--json",
    ])
    out = capsys.readouterr().out
    payload = json.loads(out)  # raises if anything else was printed
    assert code == payload["exit_code"] == 1
    assert payload["skills_scanned"] == 1
    assert payload["registries_scanned"] == 1
    assert [f["klass"] for f in payload["findings"]] == [check_skills.K_NAME_DIR_MISMATCH]
    assert "dismissed_candidates" not in payload


def test_main_json_mode_includes_dismissed_candidates_when_asked(tmp_path, capsys):
    registry_root = tmp_path / "reg"
    write_skill(registry_root, "skills/tricky", body="\nSee `scripts/*.py`.\n")
    check_skills.main([
        "--config", str(_absolute_registry_config(tmp_path, registry_root)),
        "--waivers", str(_empty_waivers(tmp_path)),
        "--json", "--list-findings",
    ])
    payload = json.loads(capsys.readouterr().out)
    rules = [c["dismissed_by"]
             for entry in payload["dismissed_candidates"] for c in entry["candidates"]]
    assert "glob-metacharacter" in rules


# =================================================================================
# The shipped config itself must stay loadable and self-consistent
# =================================================================================

def test_shipped_config_and_waivers_are_loadable(tmp_path):
    config = check_skills.load_yaml_mapping(check_skills.DEFAULT_CONFIG, "config")
    assert isinstance(config.get("registries"), list) and config["registries"]
    for entry in config["registries"]:
        assert entry.get("name") and entry.get("layout")
        if not entry.get("optional"):
            continue
        assert str(entry.get("reason") or "").strip(), \
            f"optional registry {entry['name']} needs a non-empty reason"
    waivers, findings, present = check_skills.load_waivers(check_skills.DEFAULT_WAIVERS)
    assert present is True
    assert findings == []
    assert isinstance(waivers, list)
