#!/usr/bin/env python3
"""Tests for the required-status-check invariants of .github/workflows/ci.yml.

`pytest-windows` is a REQUIRED status check on this repo's default branch. The
declaration lives in another repo — repo-settings' `fleet.yml`, under
`Adam-S-Daniel/agentskills` — which is exactly why these tests exist here: this
repo owns the workflow that has to keep satisfying it, and has nothing else
that would notice when it stops.

Every assertion below guards the same failure, which is severe and silent:
GitHub holds a required context that is never reported as *pending forever*, so
every open PR becomes unmergeable at once, with no failing check to point at.

Parsed as YAML rather than grepped on purpose. A line scan reads clean on a
`concurrency:` nested inside a job it never looked into, and cannot tell a job
id from the same word in one of ci.yml's comments — which is most of the file.

Run: python3 -m pytest scripts/test_ci_workflow.py -q
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# The contexts repo-settings' fleet.yml makes required for this repo. Kept in
# step by hand because the two repos cannot read each other — and that is the
# job of the first test below: it is what fails when this list and the workflow
# drift apart, instead of a PR queue silently wedging.
REQUIRED_CONTEXTS = ["pytest-windows"]


def load_ci() -> dict:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def ci_triggers(doc: dict) -> dict:
    # YAML 1.1 (what pyyaml implements) resolves a bare `on` to the boolean
    # True, so the trigger block is doc[True] and not doc["on"]. Accept either
    # rather than depending on which spec version the parser follows.
    return doc[True] if True in doc else doc["on"]


@pytest.mark.parametrize("context", REQUIRED_CONTEXTS)
def test_every_required_context_is_still_a_job_here(context):
    assert context in load_ci()["jobs"], (
        f"{context} is a required status check (repo-settings fleet.yml) but is "
        f"no longer a job in ci.yml. Nothing will ever report that context, and "
        f"every open PR is blocked until fleet.yml is updated to match."
    )


@pytest.mark.parametrize("context", REQUIRED_CONTEXTS)
def test_a_required_job_publishes_its_context_under_its_job_id(context):
    """GitHub names a check after the job id only while the job has neither a
    `name:` nor a `strategy.matrix`. Either one renames the context — a matrix
    to `pytest-windows (windows-latest)` — which is the whole reason the
    Windows lane is a separate job rather than a matrix leg of `pytest`."""
    job = load_ci()["jobs"][context]
    assert "name" not in job, (
        f"{context} gained a `name:`, which renames the published check context "
        f"away from the required `{context}`."
    )
    assert "matrix" not in (job.get("strategy") or {}), (
        f"{context} gained a `strategy.matrix`, which renames the published "
        f"check context away from the required `{context}`."
    )


def test_ci_reports_on_every_pull_request():
    """A `paths:`/`branches:` filter on `on: pull_request` would leave the
    required context unreported on any PR that misses the filter — pending
    forever, not skipped."""
    pull_request = ci_triggers(load_ci())["pull_request"]
    narrowing = {"paths", "paths-ignore", "branches", "branches-ignore"}
    assert not (narrowing & set(pull_request or {})), (
        "ci.yml's `on: pull_request` was narrowed. A PR that misses the filter "
        "never reports the required contexts and can never be merged."
    )


@pytest.mark.parametrize("context", REQUIRED_CONTEXTS)
def test_no_concurrency_group_governs_a_required_job(context):
    """A job publishing a required context gets no `concurrency` block, at
    either level. GitHub picks non-deterministically between a cancelled and a
    successful run for the same context + sha, and when cancelled wins the
    merge API returns `405 Required status check "<ctx>" is cancelled` — which
    nothing overrides, not auto-merge and not an explicit merge call.

    `cancel-in-progress: false` does not make this safe: GitHub still keeps
    only the in-progress run plus the latest pending one per group and cancels
    the other pending duplicates."""
    doc = load_ci()
    assert "concurrency" not in doc, (
        "ci.yml gained a workflow-level `concurrency:` block, which governs "
        f"every job in it including the required `{context}`."
    )
    assert "concurrency" not in doc["jobs"][context], (
        f"the required job `{context}` gained a `concurrency:` block."
    )
