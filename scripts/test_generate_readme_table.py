#!/usr/bin/env python3
"""Tests for scripts/generate_readme_table.py.

Hermetic and deterministic: the machinery tests build throwaway skill trees
under pytest's `tmp_path` and pass a synthetic marketplace dict in, so nothing
here reads the real repo except where it means to. No network, no sleeps, no
wall-clock dependence.

The SHIPPED-ARTIFACT section at the bottom reads README.md on purpose and
parses its generated table with a real markdown parser rather than matching
substrings — the property under test is "every published bundle is a row of
that table", which is a statement about the rendered artifact, not about the
source that produced it.

Run: python3 -m pytest scripts/test_generate_readme_table.py -q
"""

import sys
from pathlib import Path

import pytest
from markdown_it import MarkdownIt

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_consistency as cc  # noqa: E402
import generate_readme_table as grt  # noqa: E402


# =================================================================================
# Fixture builders
# =================================================================================

SKILL_MD = "---\nname: {name}\ndescription: {description}\n---\n\nBody.\n"


def write_skill(plugins_dir: Path, plugin: str, skill: str, description="Does a thing.") -> Path:
    skill_dir = plugins_dir / plugin / "skills" / skill
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        SKILL_MD.format(name=skill, description=description), encoding="utf-8"
    )
    return skill_dir


def local_entry(name="demo", description="A local bundle."):
    return {"name": name, "source": f"./plugins/{name}", "description": description}


def federated_entry(name="remote", repo="Owner/Repo", description="A remote bundle."):
    return {
        "name": name,
        "source": {"source": "github", "repo": repo},
        "description": description,
    }


def marketplace(*entries):
    return {"name": "test-marketplace", "plugins": list(entries)}


@pytest.fixture
def plugins_dir(tmp_path):
    d = tmp_path / "plugins"
    d.mkdir()
    return d


def collect(market, plugins_dir):
    return grt.collect_plugin_rows(market, plugins_dir=plugins_dir)


# =================================================================================
# Table cell parsing — used to assert ON the rendered rows, not on the inputs
# =================================================================================

_MD = MarkdownIt("commonmark").enable("table")


def table_cells(table_markdown: str):
    """[[cell, …], …] for a GFM table's BODY rows, parsed with markdown-it.

    Reading the rendered table back through a real parser (rather than
    splitting on '|') is what makes "the README mentions every bundle" an
    assertion about the artifact a reader sees.
    """
    rows, row, in_body = [], None, False
    for token in _MD.parse(table_markdown):
        if token.type == "tbody_open":
            in_body = True
        elif token.type == "tbody_close":
            in_body = False
        elif in_body and token.type == "tr_open":
            row = []
        elif in_body and token.type == "tr_close":
            rows.append(row)
        elif in_body and token.type == "inline" and row is not None:
            row.append(token.content)
    return rows


def test_table_cells_reads_back_what_build_table_wrote():
    # Guards the guard: if this helper mis-parsed, every assertion built on it
    # would be vacuous in exactly the way this file exists to prevent.
    table = grt.build_table(["| `a` | `/a:x` | One. |", "| `b` | `/b:y` | Two. |"])
    assert table_cells(table) == [["`a`", "`/a:x`", "One."], ["`b`", "`/b:y`", "Two."]]


# =================================================================================
# Local bundles
# =================================================================================


def test_a_local_bundle_renders_one_row_per_skill(plugins_dir):
    write_skill(plugins_dir, "demo", "alpha", "Alpha does alpha. And more.")
    write_skill(plugins_dir, "demo", "beta", "Beta does beta.")
    by_plugin, problems = collect(marketplace(local_entry("demo")), plugins_dir)
    assert problems == []
    assert [name for name, _ in by_plugin] == ["demo"]
    assert table_cells(grt.build_table(by_plugin[0][1])) == [
        ["`demo`", "`/demo:alpha`", "Alpha does alpha."],
        ["`demo`", "`/demo:beta`", "Beta does beta."],
    ]


def test_a_skill_without_a_description_falls_back_to_the_bundle_description(plugins_dir):
    skill_dir = write_skill(plugins_dir, "demo", "alpha")
    (skill_dir / "SKILL.md").write_text("---\nname: alpha\n---\n", encoding="utf-8")
    by_plugin, problems = collect(marketplace(local_entry("demo", "Fallback text.")), plugins_dir)
    assert problems == []
    assert table_cells(grt.build_table(by_plugin[0][1]))[0][2] == "Fallback text."


# =================================================================================
# Federated bundles — named, never enumerated, never silent
# =================================================================================


def test_a_federated_bundle_renders_exactly_one_row_naming_its_repo(plugins_dir):
    market = marketplace(federated_entry("remote", "Owner/Repo", "A remote bundle. Extra."))
    by_plugin, problems = collect(market, plugins_dir)
    assert problems == []
    assert [name for name, _ in by_plugin] == ["remote"]
    (cells,) = table_cells(grt.build_table(by_plugin[0][1]))
    assert cells[0] == "`remote`"
    assert "Owner/Repo" in cells[1] and "https://github.com/Owner/Repo" in cells[1]
    assert cells[2] == "A remote bundle."


def test_a_federated_bundle_does_not_invent_a_skill_list(plugins_dir):
    # There is no offline way to know the remote skill names, so the row must
    # carry a visible placeholder rather than anything that reads as real.
    market = marketplace(federated_entry("remote"))
    by_plugin, _ = collect(market, plugins_dir)
    (cells,) = table_cells(grt.build_table(by_plugin[0][1]))
    assert "<skill>" in cells[1]


def test_a_federated_bundle_ignores_a_same_named_local_directory(plugins_dir):
    # check_consistency rejects that collision; the renderer must not quietly
    # paper over it by enumerating the local tree under the federated name.
    write_skill(plugins_dir, "remote", "local-leak")
    by_plugin, _ = collect(marketplace(federated_entry("remote")), plugins_dir)
    assert len(by_plugin[0][1]) == 1
    assert "local-leak" not in by_plugin[0][1][0]


# =================================================================================
# The omission gate — a published bundle may never render as nothing
# =================================================================================


def test_a_bundle_that_renders_no_rows_is_a_problem(plugins_dir):
    # The exact old behaviour of a federated entry: zero rows, a byte-identical
    # table, and a green --check over a README that never mentions the bundle.
    by_plugin, problems = collect(marketplace(local_entry("empty")), plugins_dir)
    assert len(problems) == 1
    assert "empty" in problems[0]
    assert [name for name, _ in by_plugin] == ["empty"]


def test_an_unclassifiable_entry_is_a_problem_not_a_skip(plugins_dir):
    entry = {"name": "mystery", "source": {"source": "gitlab", "repo": "o/r"}}
    _, problems = collect(marketplace(entry), plugins_dir)
    assert len(problems) == 1
    assert "mystery" in problems[0]


def test_problems_from_several_bundles_accumulate(plugins_dir):
    market = marketplace(local_entry("empty-one"), local_entry("empty-two"))
    _, problems = collect(market, plugins_dir)
    assert len(problems) == 2


def test_build_rows_exits_nonzero_when_a_bundle_renders_nothing(monkeypatch, plugins_dir):
    monkeypatch.setattr(grt, "PLUGINS_DIR", plugins_dir)
    monkeypatch.setattr(grt, "load_marketplace", lambda: marketplace(local_entry("empty")))
    with pytest.raises(SystemExit) as excinfo:
        grt.build_rows()
    assert "empty" in str(excinfo.value)


def test_rows_are_emitted_in_marketplace_order(plugins_dir):
    write_skill(plugins_dir, "zeta", "z-skill")
    write_skill(plugins_dir, "alpha", "a-skill")
    market = marketplace(local_entry("zeta"), federated_entry("mid"), local_entry("alpha"))
    by_plugin, problems = collect(market, plugins_dir)
    assert problems == []
    assert [name for name, _ in by_plugin] == ["zeta", "mid", "alpha"]


# =================================================================================
# Shipped artifacts — these read the real repo on purpose
# =================================================================================


def test_shipped_readme_table_is_up_to_date(capsys):
    assert grt.render(check=True) == 0
    capsys.readouterr()


def test_shipped_marketplace_renders_without_problems():
    _, problems = grt.collect_plugin_rows()
    assert problems == []


def test_every_published_bundle_is_a_row_of_the_rendered_readme_table():
    # The property the old code lost silently. Asserted against README.md as
    # committed, parsed as markdown — not against the row builder's own output,
    # which would only prove the builder agrees with itself.
    readme = grt.README_PATH.read_text(encoding="utf-8")
    table = readme.split(grt.BEGIN_MARKER, 1)[1].split(grt.END_MARKER, 1)[0]
    rendered = {cells[0].strip("`") for cells in table_cells(table)}
    published = {entry["name"] for entry in cc.load_marketplace()["plugins"]}
    assert published <= rendered
    assert rendered <= published  # no row naming a bundle nobody publishes


def test_the_shipped_federated_bundle_names_its_source_repo_in_the_readme():
    readme = grt.README_PATH.read_text(encoding="utf-8")
    table = readme.split(grt.BEGIN_MARKER, 1)[1].split(grt.END_MARKER, 1)[0]
    by_name = {cells[0].strip("`"): cells for cells in table_cells(table)}
    for entry in cc.load_marketplace()["plugins"]:
        kind, repo = cc.classify_source(entry)
        if kind == "federated":
            assert repo in by_name[entry["name"]][1]
