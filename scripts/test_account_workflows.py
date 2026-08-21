"""Invariants of the two account-store workflows.

Parsed with the `yaml` library, never scanned as text: a regex reads clean on
structure it cannot see, and every invariant here is structural (which
permissions a job holds, which triggers publish a context, what a `run:` block
does). Same rule the fleet AGENTS.md states for workflow lints.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
ZIPS = WORKFLOWS / "account-skill-zips.yml"
RECORD = WORKFLOWS / "record-account-upload.yml"


def load(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # PyYAML resolves a bare `on:` key to the boolean True (YAML 1.1).
    data["on"] = data.pop(True, data.get("on"))
    return data


def runs(workflow):
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if step.get("run"):
                yield step["run"]


class TestRecordAccountUpload:
    def test_it_is_dispatch_only(self):
        """It records an event that happened in a browser.

        Nothing about an upload is inferable from a push, so any other trigger
        would be recording a claim nobody made.
        """
        assert list(load(RECORD)["on"]) == ["workflow_dispatch"]

    def test_it_cannot_open_a_pull_request(self):
        """The load-bearing design decision, locked so it cannot be "fixed".

        `pytest-windows` is a REQUIRED check on main (repo-settings' fleet.yml
        overrides agentskills to require it), and GitHub raises no workflow
        events for actions taken by GITHUB_TOKEN. A PR opened by this job would
        therefore never run CI, never publish that context, and could never
        merge - an inert PR that looks like a working feature. It pushes a
        branch and a human opens the PR, which runs the checks under their
        identity.
        """
        wf = load(RECORD)
        assert "pull-requests" not in wf.get("permissions", {})
        for body in runs(wf):
            assert "gh pr create" not in body
            assert "/pulls" not in body

    def test_it_never_writes_to_the_default_branch(self):
        """main is PR-only by ruleset; a push there is rejected (GH013).

        Asserted on the push TARGET rather than on the token, because the
        failure this prevents is designing a bot that expects to write there.
        """
        for body in runs(load(RECORD)):
            for push in re.findall(r"git push[^\n]*", body):
                assert "main" not in push, push

    def test_it_reads_dispatch_inputs_from_the_environment(self):
        """An `${{ inputs.* }}` expansion inside `run:` is echoed to the log
        and is attacker-controlled shell. Inputs reach the script as env vars.
        """
        wf = load(RECORD)
        for job in wf["jobs"].values():
            for step in job.get("steps", []):
                if step.get("run"):
                    assert "inputs." not in step["run"], step.get("name")

    def test_every_gh_api_call_discards_output_on_failure(self):
        """`gh api --jq` prints the raw error body to STDOUT and exits 1 on an
        HTTP error - the filter never runs - so `x=$(cmd) || true` captures
        that garbage as the answer. This broke sync.sh once; see AGENTS.md.
        """
        for body in runs(load(RECORD)):
            for line in body.splitlines():
                if "gh api" in line and "$(" in line:
                    tail = body[body.index(line):].split("\n", 2)
                    joined = " ".join(tail[:2])
                    assert re.search(r"\|\|\s*\w+=\"\"", joined), line


class TestAccountSkillZips:
    def test_the_artifact_is_stored_not_deflated(self):
        """GitHub serves an artifact AS a zip, so the artifact IS the upload.

        `zip_skill()` uses ZIP_STORED "for maximum server compatibility";
        compression-level 0 is what keeps the served file the same shape.
        """
        wf = load(ZIPS)
        uploads = [
            s for j in wf["jobs"].values() for s in j.get("steps", [])
            if "upload-artifact" in (s.get("uses") or "")
        ]
        assert uploads, "no upload-artifact step"
        for step in uploads:
            assert step["with"]["compression-level"] == 0

    def test_no_required_context_publisher_carries_a_concurrency_group(self):
        """Neither workflow may group runs.

        GitHub picks non-deterministically between a cancelled run and a
        successful one for the same context+sha, and a cancelled required check
        hard-blocks the merge with no override. Neither of these publishes a
        required context today - this asserts they stay that way rather than
        acquiring a group later and inheriting the hazard.
        """
        for path in (ZIPS, RECORD):
            wf = load(path)
            assert "concurrency" not in wf, path.name
            for job in wf["jobs"].values():
                assert "concurrency" not in job, path.name

    def test_the_zip_payload_is_built_by_the_real_uploader(self):
        """Re-walking the skill directory here would be a second
        implementation of `_include_in_zip`, free to drift from the one an
        actual upload uses. The payload comes from `--prepare --zip-dir`.
        """
        assert any("--zip-dir" in body for body in runs(load(ZIPS)))


class TestSkillInputIsValidatedBeforeUse:
    """The shipped guard, executed - not string-matched.

    A workflow lint that greps for the guard passes on a guard that does not
    work. These run the exact `case` block out of the YAML.
    """

    def _guard(self):
        body = next(
            s["run"] for s in load(RECORD)["jobs"]["record"]["steps"]
            if s.get("name") == "Resolve the run that built the artifact"
        )
        start = body.index('case "$SKILL"')
        end = body.index("esac", start) + len("esac")
        return body[start:end]

    def _bash(self, script, value):
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("bash not on PATH")
        # Delivered through the ENVIRONMENT, which is how the workflow supplies
        # it (`env: SKILL: ${{ inputs.skill }}`) - and the only way that
        # survives Windows.
        #
        # This was argv, and pytest-windows caught it: Git Bash reconstructs
        # its own argv from the Windows command line and truncates an argument
        # at a newline, so the shell saw a bare `sync-skills`, the guard
        # correctly admitted it, and the refusal test failed. Five of six evil
        # inputs still refused, which is what made it look like a guard bug
        # rather than a harness one. An environment block is NUL-delimited and
        # carries a newline intact.
        return subprocess.run(
            [bash, "-c", script], env={**os.environ, "SKILL": value},
            capture_output=True, text=True,
        )

    def _run(self, snippet, value):
        return self._bash(snippet, value).returncode

    @pytest.mark.parametrize("name", [
        "sync-skills", "wj-next-break", "pdf-ocr-audit",
        "sync-cc-settings-between-wsl-and-windows",
    ])
    def test_it_admits_every_real_declared_name(self, name):
        assert self._run(self._guard(), name) == 0, name

    @pytest.mark.parametrize("evil, why", [
        ("sync-skills\nfoo=bar", "newline injects a line into $GITHUB_ENV"),
        ("sync-skills bar", "space splits a branch name"),
        ("../../etc/passwd", "traversal"),
        ("sync-skills;id", "command separator"),
        ("-rf", "leading dash reads as a grep/git option"),
        ("", "empty"),
    ])
    def test_it_refuses_anything_that_is_not_a_bare_skill_name(self, evil, why):
        # Delivery first: a refusal test passes vacuously if the harness never
        # handed the shell the dangerous value. See _bash.
        got = self._bash('printf %s "$SKILL" | wc -c', evil).stdout.strip()
        assert int(got) == len(evil.encode()), (
            f"harness mangled the input before the guard saw it: sent "
            f"{len(evil.encode())} bytes, shell received {got}. The refusal "
            f"below would have passed for the wrong reason."
        )
        assert self._run(self._guard(), evil) != 0, why

    def test_the_grep_check_alone_would_have_let_the_newline_through(self):
        """The negative control, and the reason the guard above exists.

        `grep -F` treats a newline-separated pattern as ALTERNATIVES, so the
        artifact-name check matches on the first line and passes the whole
        value - including the tail that reaches $GITHUB_ENV. Without this
        test, a later reader deletes the `case` guard as redundant with a
        check that reads like validation and is not.
        """
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("bash not on PATH")
        script = (
            'printf \'%s\\n\' "sync-skills" "other" '
            '| grep -qxF -- "$1"'
        )
        assert subprocess.run(
            [bash, "-c", script, "_", "sync-skills\nfoo=bar"],
            capture_output=True,
        ).returncode == 0, (
            "grep -F unexpectedly rejected the multiline pattern; if this "
            "starts failing the hazard is gone and this test can go with it"
        )
