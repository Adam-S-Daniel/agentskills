"""What `account-skill-zips` decides to build a ZIP for.

This is the half of that workflow that used to be a `run:` heredoc, where the
only way to observe it was to merge a change and read a production run's
summary. The selection now answers to a CROSS-REPO condition - skills-evals'
published Tier-3 audit - so the interesting cases are ones that cannot be
produced on demand in CI at all: a `stale` verdict, an unreadable artifact, a
drifted name nobody declared. Every one of them is a fixture here.

Hermetic by construction: the module under test takes no clock, no network and
no git, so nothing below needs a sleep, a freeze or a fake HTTP layer.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import account_zip_selection as sel  # noqa: E402

DECLARED = ["adam-writing-style", "finding-unknowns", "sync-skills"]


def report(needs_upload=(), asserted=(), missing=(), statuses=None):
    """A `sync_skills.py --account-drift` report over DECLARED."""
    statuses = statuses or {}
    rows = [{"name": n,
             "status": statuses.get(
                 n, "stale" if n in needs_upload else "in-sync"),
             "recorded_basis": "observed"}
            for n in DECLARED]
    rows += [{"name": n, "status": "missing-from-registry"} for n in missing]
    return {
        "recorded_at": "2026-08-21T01:17:22+00:00",
        "needs_upload": list(needs_upload),
        "in_sync": [n for n in DECLARED if n not in needs_upload],
        "in_sync_asserted": list(asserted),
        "missing_from_registry": list(missing),
        "skills": rows,
    }


def artifact(status="fail", drifted=("sync-skills",), checked=None):
    """A published `propagation/account/latest.json`.

    Short `registry_ref` values on purpose: a 40-hex string next to a word is
    the shape gitleaks' generic-api-key rule looks for, and fixtures are
    committed and scanned. See AGENTS.md, "A name you choose becomes data".
    """
    return {
        "schema": 1,
        "probe": "propagation/account",
        "status": status,
        "generated_at": "2026-08-21T05:03:52Z",
        "registry_ref": "df11631",
        "checked": sorted(checked if checked is not None else DECLARED),
        "skipped": ["docx", "learn"],
        "findings": [{"skill": n, "kind": "content-drift", "detail": "differs"}
                     for n in drifted],
    }


def run(tmp_path, rep, *, selection="stale", latest=None, status="",
        event="schedule", run_id="12345", write_latest=True):
    """Drive main() the way the workflow does. Returns (outputs, summary)."""
    drift = tmp_path / "drift.json"
    drift.write_text(json.dumps(rep), encoding="utf-8")
    latest_path = tmp_path / "latest.json"
    if latest is not None and write_latest:
        latest_path.write_text(json.dumps(latest), encoding="utf-8")
    elif isinstance(latest, str):
        latest_path.write_text(latest, encoding="utf-8")
    out = tmp_path / "gh_output"
    summary = tmp_path / "summary.md"
    rc = sel.main([
        "--drift-report", str(drift),
        "--selection", selection,
        "--audit-latest", str(latest_path),
        "--audit-status", status,
        "--event", event,
        "--run-id", run_id,
        "--github-output", str(out),
        "--summary", str(summary),
    ])
    assert rc == 0
    parsed = dict(
        line.split("=", 1)
        for line in out.read_text(encoding="utf-8").splitlines() if line)
    return parsed, summary.read_text(encoding="utf-8")


class TestSelectionModes:
    def test_all_takes_every_declared_skill_but_not_the_absent_ones(self, tmp_path):
        """`all` is a human saying "rebuild everything I declared".

        A `missing-from-registry` row has no directory to zip, so including it
        would fail the matrix leg rather than tell anyone anything.
        """
        out, _ = run(tmp_path, report(missing=["gone"]), selection="all")
        assert json.loads(out["names"]) == DECLARED
        assert out["count"] == "3"

    def test_an_explicit_list_takes_only_the_names_that_are_declared(self, tmp_path):
        out, text = run(tmp_path, report(),
                        selection="sync-skills, not-a-skill")
        assert json.loads(out["names"]) == ["sync-skills"]
        assert "not declared in `account-skills.txt`, skipped" in text.lower()
        assert "not-a-skill" in text

    def test_an_explicit_list_ignores_the_audit_entirely(self, tmp_path):
        """A named dispatch is an instruction, not a question."""
        out, _ = run(tmp_path, report(), selection="finding-unknowns",
                     latest=artifact(drifted=["sync-skills"]),
                     status="reported-failure")
        assert json.loads(out["names"]) == ["finding-unknowns"]

    def test_an_empty_selection_means_stale(self, tmp_path):
        """The workflow passes `${{ inputs.selection || 'stale' }}`, which is
        the empty string on a push, a schedule, and a dispatch that cleared the
        box. All three must behave as `stale`, not as an unknown name list.
        """
        out, _ = run(tmp_path, report(needs_upload=["sync-skills"]),
                     selection="")
        assert json.loads(out["names"]) == ["sync-skills"]


class TestTheUnionWithThePublishedAudit:
    def test_reported_failure_contributes_its_drifted_names(self, tmp_path):
        """The case the local recording structurally cannot see: the account
        store drifted while the registry did not move, so `--account-drift`
        reports everything in sync and only the audit knows better.
        """
        out, _ = run(tmp_path, report(), latest=artifact(),
                     status="reported-failure")
        assert json.loads(out["names"]) == ["sync-skills"]

    def test_it_is_a_union_and_not_a_replacement(self, tmp_path):
        out, _ = run(tmp_path, report(needs_upload=["finding-unknowns"]),
                     latest=artifact(drifted=["sync-skills"]),
                     status="reported-failure")
        assert json.loads(out["names"]) == ["finding-unknowns", "sync-skills"]

    def test_the_union_does_not_duplicate_a_name_both_sources_name(self, tmp_path):
        out, _ = run(tmp_path, report(needs_upload=["sync-skills"]),
                     latest=artifact(drifted=["sync-skills"]),
                     status="reported-failure")
        assert json.loads(out["names"]) == ["sync-skills"]
        assert out["count"] == "1"

    @pytest.mark.parametrize("status", [
        "fresh", "stale", "missing", "unreadable", "not-yet-bootstrapped",
        "unavailable", "some-future-status-this-repo-never-heard-of", "",
    ])
    def test_no_other_status_contributes_a_single_name(self, tmp_path, status):
        """`reported-failure` is the ONE authoritative status, because it is
        the one skills-evals opens the tracking issue on. Anything else - a
        liveness fault, a pass, or a string this repo does not recognise - must
        leave the selection to the local recording alone.
        """
        out, _ = run(tmp_path, report(), latest=artifact(), status=status)
        assert json.loads(out["names"]) == []
        assert out["count"] == "0"

    def test_a_passing_audit_never_removes_a_locally_stale_name(self, tmp_path):
        """The reason there is no "audit passed, build nothing" gate.

        A `fresh` verdict describes the registry at the audit's own
        `registry_ref`. A push-triggered run is BY CONSTRUCTION about a commit
        newer than that, so zeroing the set here would silently drop the only
        source that knows about it.
        """
        out, text = run(tmp_path, report(needs_upload=["sync-skills"]),
                        latest=artifact(status="pass", drifted=[]),
                        status="fresh")
        assert json.loads(out["names"]) == ["sync-skills"]
        assert "registry_ref" in text

    def test_a_drifted_name_that_is_not_declared_is_reported_not_zipped(self, tmp_path):
        """Declaring a skill for the account store is close to a one-way door -
        the upload path has no delete (docs/decisions/0002) - so this workflow
        must never declare one because it found the name in an artifact.
        """
        out, text = run(tmp_path, report(),
                        latest=artifact(drifted=["sync-skills", "docx"],
                                        checked=DECLARED + ["docx"]),
                        status="reported-failure")
        assert json.loads(out["names"]) == ["sync-skills"]
        assert "docx" in text
        assert "one-way door" in text
        assert "docs/decisions/0002" in text

    def test_a_contributed_name_says_where_it_came_from(self, tmp_path):
        """It is `in-sync` locally, which on its own reads like a bug."""
        _, text = run(tmp_path, report(), latest=artifact(),
                      status="reported-failure")
        assert "reported drifted by the published audit" in text


class TestADegradedAuditIsNeverMistakenForAPass:
    def test_a_missing_artifact_degrades_loudly(self, tmp_path):
        out, text = run(tmp_path, report(needs_upload=["sync-skills"]),
                        latest=None, status="missing")
        assert json.loads(out["names"]) == ["sync-skills"]
        assert "Degraded" in text
        assert "`missing`" in text

    def test_a_corrupt_artifact_degrades_loudly(self, tmp_path):
        """Truncated or half-written JSON is an ABSENT artifact, not an empty
        finding list - the second reading would turn a broken publish into a
        clean bill of health.
        """
        out, text = run(tmp_path, report(), latest="{not json",
                        status="reported-failure", write_latest=False)
        assert json.loads(out["names"]) == []
        assert "Degraded" in text
        assert "`unavailable`" in text

    def test_a_verdict_whose_artifact_cannot_be_read_is_downgraded(self, tmp_path):
        """The caller said `fresh`, but this process cannot see the file the
        verdict was computed from. Trust the disk, and say so.
        """
        _, text = run(tmp_path, report(), latest=None, status="fresh")
        assert "Degraded" in text
        assert "`unavailable`" in text

    def test_a_fresh_audit_is_not_called_degraded(self, tmp_path):
        _, text = run(tmp_path, report(), latest=artifact(status="pass",
                                                          drifted=[]),
                      status="fresh")
        assert "Degraded" not in text

    def test_the_audit_block_names_what_it_measured(self, tmp_path):
        _, text = run(tmp_path, report(), latest=artifact(),
                      status="reported-failure")
        assert "2026-08-21T05:03:52Z" in text
        assert "df11631" in text
        assert "Checked 3 skill(s)" in text
        assert "found drifted: sync-skills" in text

    @pytest.mark.parametrize("given, expected", [
        ("fresh", "fresh"),
        ("  reported-failure  ", "reported-failure"),
        ("", "unavailable"),
        (None, "unavailable"),
        ("PASS", "unavailable"),
    ])
    def test_any_unrecognised_status_reads_as_unavailable(self, given, expected):
        assert sel.normalise_status(given) == expected


class TestTheSummaryStillCarriesWhatItCarried:
    def test_nothing_to_upload_reads_as_nothing_to_upload(self, tmp_path):
        out, text = run(tmp_path, report(), latest=artifact(status="pass",
                                                            drifted=[]),
                        status="fresh")
        assert out["count"] == "0"
        assert "Nothing to upload" in text

    def test_an_asserted_in_sync_is_named_even_though_it_is_not_offered(self, tmp_path):
        """A claim and a measurement must not look the same. Recorded by
        `--assert-uploaded` means nobody checked.
        """
        _, text = run(tmp_path, report(asserted=["fastmail"]))
        assert "Not offered, but never verified" in text
        assert "fastmail" in text

    def test_a_declared_skill_absent_from_the_registry_is_flagged(self, tmp_path):
        _, text = run(tmp_path, report(missing=["retired-skill"]))
        assert "Declared but absent from the registry" in text
        assert "retired-skill" in text

    def test_both_upload_routes_and_the_run_id_survive(self, tmp_path):
        """Same invariant scripts/test_account_workflows.py asserts against the
        module - restated here because this is where the rendering lives.
        """
        _, text = run(tmp_path, report(needs_upload=["sync-skills"]),
                      run_id="98765")
        assert "Record an account upload" in text
        assert "--record-account-state" in text
        assert "98765" in text

    def test_the_event_that_started_the_run_is_named(self, tmp_path):
        _, text = run(tmp_path, report(), event="push")
        assert "Started by: **push**" in text

    def test_no_filesystem_path_of_a_skill_reaches_the_summary(self, tmp_path):
        """`--account-drift` rows carry an absolute `path` from whichever
        machine ran it. This repo is public and so are its logs and run
        summaries; a home directory is never printed. See AGENTS.md, "Data
        exposure in CI and public repos".
        """
        rep = report(needs_upload=["sync-skills"])
        for row in rep["skills"]:
            row["path"] = "/home/somebody/repos/agentskills/" + row["name"]
        _, text = run(tmp_path, rep)
        assert "/home/somebody" not in text


class TestTheOutputContract:
    def test_names_is_json_the_matrix_can_consume(self, tmp_path):
        """`fromJSON(needs.pick.outputs.names)` is what expands the zip
        matrix, so the value has to be a JSON array on ONE line.
        """
        out, _ = run(tmp_path, report(needs_upload=DECLARED))
        assert out["names"] == json.dumps(DECLARED)
        assert "\n" not in out["names"]

    def test_count_is_the_string_the_if_condition_compares_against(self, tmp_path):
        """The zip job is gated on `needs.pick.outputs.count != '0'`."""
        out, _ = run(tmp_path, report())
        assert out["count"] == "0"

    def test_the_outputs_are_appended_not_rewritten(self, tmp_path):
        """$GITHUB_OUTPUT is shared by every step in the job; truncating it
        would drop an earlier step's outputs.
        """
        rep = tmp_path / "drift.json"
        rep.write_text(json.dumps(report()), encoding="utf-8")
        out = tmp_path / "gh_output"
        out.write_text("status=fresh\n", encoding="utf-8")
        summary = tmp_path / "summary.md"
        sel.main(["--drift-report", str(rep), "--github-output", str(out),
                  "--summary", str(summary)])
        assert out.read_text(encoding="utf-8").startswith("status=fresh\n")
        assert "count=0" in out.read_text(encoding="utf-8")
