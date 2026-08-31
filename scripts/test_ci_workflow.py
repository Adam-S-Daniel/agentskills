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


# --- The docs-only early-skip gate -----------------------------------------
#
# Because `pytest-windows` is required, the docs-only skip lives INSIDE the two
# pytest jobs (an always-run `salient` step gating the heavy steps) rather than
# as a trigger filter — test_ci_reports_on_every_pull_request above is what
# forbids the trigger form. That trade is safe only while three things hold,
# and each gets its own test: the two jobs carry the SAME gate (a list edited
# in one job only is a silent divergence), the gate pins the MEASURED inert
# list (every path no suite reads; plugins/**/*.md stays salient because
# SKILL.md/PURPOSE.md feed the digests and lock checks), and the gate actually
# behaves — the body is executed here against fixture repos in both
# directions, because a green gate that was never shown able to say "run" is
# a light wired to nothing.

INERT_DOCS_REGEX = (
    r"^(docs/.*\.md|README\.md|AGENTS\.md|CLAUDE\.md|STRATEGY\.md"
    r"|\.claude/memory/.*\.md)$"
)

PYTEST_JOBS = ["pytest", "pytest-windows"]


def _salience_step(job_id: str) -> dict:
    steps = [s for s in load_ci()["jobs"][job_id].get("steps", [])
             if s.get("id") == "salient"]
    assert len(steps) == 1, (
        f"job `{job_id}` must carry exactly one step with id `salient`; "
        f"found {len(steps)}. Without it the heavy steps' `if:` guards "
        f"evaluate against a missing step and the job runs nothing."
    )
    return steps[0]


def test_the_pytest_jobs_share_one_salience_gate():
    """The two suites must skip and run on the same verdict. An inert list
    widened in one job only would let the required Windows lane skip a change
    the ubuntu lane still tests — or the reverse, which reports the required
    context green on a change nothing Windows-shaped ever saw."""
    ubuntu, windows = (_salience_step(j) for j in PYTEST_JOBS)
    assert ubuntu["run"] == windows["run"], (
        "the `salient` step bodies in `pytest` and `pytest-windows` differ — "
        "edit them together, byte for byte."
    )
    assert ubuntu.get("env") == windows.get("env"), (
        "the `salient` step env blocks in `pytest` and `pytest-windows` "
        "differ — edit them together."
    )


def test_the_salience_gate_pins_the_measured_inert_list():
    """The inert list is an allowlist of paths measured to be read by no test
    this workflow runs. This pin makes widening it a two-place edit — here and
    in ci.yml — so it happens deliberately or fails the suite."""
    body = _salience_step("pytest")["run"]
    assert INERT_DOCS_REGEX in body, (
        "the salience gate's inert regex no longer matches the measured list "
        "this test pins. If the change is deliberate, re-measure (which tests "
        "read the paths being added?) and update both places."
    )
    assert '"$EVENT_NAME" != "pull_request"' in body, (
        "the salience gate lost its fail-open guard: every non-pull_request "
        "event (push, workflow_dispatch) must run the full suite."
    )


@pytest.mark.parametrize("job_id", PYTEST_JOBS)
def test_the_heavy_steps_are_gated_on_the_salience_verdict(job_id):
    """A detect step nothing consults is dead code that looks like a control.
    The pip install and the pytest run in both jobs must consult it."""
    gate = "steps.salient.outputs.run == 'true'"
    gated = [s for s in load_ci()["jobs"][job_id]["steps"]
             if s.get("if") == gate]
    runs = " ".join(s.get("run") or "" for s in gated)
    assert "-m pytest" in runs and "pip install" in runs, (
        f"job `{job_id}` must gate both its pip install and its pytest run "
        f"on `{gate}`; the gated steps found were: "
        f"{[s.get('name', '<unnamed>') for s in gated]}"
    )


def _run_salience_body(tmp_path, changed_files, event_name):
    """Execute the real gate body from ci.yml in a throwaway two-commit repo
    and return the GITHUB_OUTPUT it wrote."""
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on PATH to execute the gate body")

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, text=True)

    git("init", "-q")
    # Repo-local identity, the same pattern the lock tests use, so this
    # passes with no global git identity available.
    git("config", "user.name", "fixture")
    git("config", "user.email", "fixture@example.com")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    git("add", "seed.txt")
    git("commit", "-q", "-m", "base")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True
                          ).stdout.strip()
    for name in changed_files:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("changed\n", encoding="utf-8")
        git("add", name)
    git("commit", "-q", "-m", "head")
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True
                          ).stdout.strip()

    out_file = tmp_path / "github_output"
    out_file.write_text("", encoding="utf-8")
    import os
    env = {**os.environ,
           "BASE_SHA": base, "HEAD_SHA": head, "EVENT_NAME": event_name,
           # as_posix() so the redirection target parses the same under Git
           # Bash on Windows as under bash on Linux.
           "GITHUB_OUTPUT": out_file.as_posix()}
    result = subprocess.run([bash, "-c", _salience_step("pytest")["run"]],
                            cwd=str(repo), env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, (
        f"the gate body itself failed (exit {result.returncode}):\n"
        f"{result.stdout}\n{result.stderr}"
    )
    return out_file.read_text(encoding="utf-8")


def test_the_gate_skips_a_docs_only_change(tmp_path):
    out = _run_salience_body(
        tmp_path,
        ["docs/guide.md", "README.md", "AGENTS.md", ".claude/memory/MEMORY.md"],
        "pull_request")
    assert "run=false" in out and "run=true" not in out


def test_the_gate_runs_when_any_salient_file_rides_along(tmp_path):
    """The mixed changeset is the common case, not the corner — a docs edit
    riding with a code edit must run everything."""
    out = _run_salience_body(
        tmp_path, ["docs/guide.md", "scripts/thing.py"], "pull_request")
    assert "run=true" in out and "run=false" not in out


def test_the_gate_treats_skill_markdown_as_salient(tmp_path):
    """plugins/**/*.md is content: SKILL.md and PURPOSE.md move digests and
    the lock checks. The inert list must never grow to cover it."""
    out = _run_salience_body(
        tmp_path, ["plugins/adam/skills/x/SKILL.md"], "pull_request")
    assert "run=true" in out and "run=false" not in out


def test_the_gate_fails_open_off_pull_request_events(tmp_path):
    out = _run_salience_body(tmp_path, ["docs/guide.md"], "workflow_dispatch")
    assert "run=true" in out and "run=false" not in out
