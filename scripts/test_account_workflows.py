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
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
ZIPS = WORKFLOWS / "account-skill-zips.yml"
RECORD = WORKFLOWS / "record-account-upload.yml"
# The ZIP selection lives here rather than in a `run:` heredoc; see the note on
# test_the_summary_offers_the_route_a_phone_can_take.
SELECTION = Path(__file__).resolve().parent / "account_zip_selection.py"

sys.path.insert(0, str(SELECTION.parent))

# Imported rather than restated: the status vocabulary is the thing under test
# in test_the_case_block_names_every_status_the_module_knows, and a copy of it
# here would be a third place for a rename to miss.
import account_zip_selection as sel  # noqa: E402


# The prose a `run:` block actually emits, with its comments dropped: the
# `echo "..."` payloads joined. Asserting on the raw body would let a claim
# that only survives in a comment count as if it reached the reader.
ECHOED = re.compile(r'^\s*echo\s+"(.*)"\s*$', re.M)


def echoed(body):
    return " ".join(m.group(1) for m in ECHOED.finditer(body))


def _uncomment(line):
    """One line of shell with a trailing `#` comment removed.

    Quote-aware, because the alternative is a lint that mangles the step's own
    `::warning::` text. Bash starts a comment at a `#` that begins a WORD -
    line start or after whitespace - and only when it is not quoted, so
    `${v#x}`, `$#` and `a#b` are untouched, and a `#` inside `'...'` or
    `"..."` is data. A backslash escapes the next character everywhere except
    inside single quotes.

    WHAT IT DELIBERATELY DOES NOT MODEL, stated so nobody reads more into it:
    quoting is tracked per LINE, and a heredoc body is read as shell rather
    than as its own language. A string left open at a newline, or a `#` inside
    the Python heredoc, is therefore approximated. Both are the same
    approximation the whole-line version already made, and both fail toward
    dropping text rather than inventing it - which is the safe direction for
    every assertion built on this: text that is not here cannot satisfy a
    claim, and that is the point of the helper.
    """
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and quote != "'":
            i += 2
            continue
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
        i += 1
    return line


def code(body):
    """A `run:` block reduced to what the SHELL runs - comments removed.

    The counterpart to `echoed()` and there for the same reason. The audit
    step's comments quote its own shell at length - the paragraph above the
    quiet arm names `AUDIT_DRIFT_STATUS` in prose - so `"AUDIT_DRIFT_STATUS" in
    body` was true whether or not any COMMAND read it. Measured: replacing the
    read with a workflow-local `python3 -c 'print("reported" + "-failure")'`
    left the full verifier at 945 passed, with the predicate both repos key on
    now written a second time inside the workflow, which is the one thing the
    design forbids.

    TRAILING COMMENTS COUNT, and dropping only WHOLE-LINE ones left that hole
    half open. `drift=$(python3 -c '...')  # was read from AUDIT_DRIFT_STATUS`
    is a hardcode with the name surviving in a comment, and it kept the
    assertion green at the full verifier's 953 passed - the same finding, in
    the same place, closed for the shape it was first reported in and not for
    the shape beside it. A claim that only survives in a comment must not
    count as if it reached the reader, and where that comment sits on the line
    makes no difference to who reads it.

    Lines that hold nothing but a comment drop out entirely, as before.
    """
    return "\n".join(
        stripped for stripped in map(_uncomment, body.splitlines())
        if stripped.strip()
    )


def _script(tmp_path, text, name="step.sh"):
    """The step body as a FILE, which is how the runner delivers it.

    NOT `bash -c "<body>"`. Two reasons, and the second one cost a red
    pytest-windows run.

    Fidelity first: GitHub runs a `run:` block as `/usr/bin/bash -e {0}`, where
    `{0}` is a script file it wrote to disk. A `-c` string is a different
    execution mode - `$0` means something else, and the body arrives through
    the command line rather than a file - so a harness built on `-c` was never
    running the step the way production does.

    And on Windows it does not survive the trip. Python's `list2cmdline` quotes
    an argument by MSVC CRT rules, escaping each embedded `"` as `\"`; Git
    Bash's `bash.exe` then re-parses that raw Windows command line by MSYS2
    rules, which are not the same rules. This step body carries dozens of
    double quotes inside its `::warning::` strings, and the two conventions
    disagreed often enough to hand bash a mangled script: 18 tests failed on
    pytest-windows with `syntax error near unexpected token 'newline'` pointing
    at a `case` arm that Linux `bash -n` parses cleanly. A file has no command
    line to mangle.

    Forward slashes for the same reason the GITHUB_OUTPUT path uses them: Git
    Bash reads a Windows path's backslashes as escapes.
    """
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="\n")
    return str(path).replace("\\", "/")


def require_bash():
    """bash, or a hard FAILURE - deliberately not a skip.

    Everything in this file that proves a shell guard actually guards runs the
    real block in a real bash. A `pytest.skip` when bash is missing turns that
    into 22 skipped tests and an exit code of 0, and CI reads the exit code:
    the load-bearing half of this suite would go untested while the job stayed
    green. Measured before this became a failure - stubbing `shutil.which` to
    hide bash gave "17 passed, 22 skipped" and EXIT=0.

    That is the same silent-green shape these tests exist to close, and this
    repo has already paid for it once: ci.yml's `fetch-depth: 0` carries a
    comment about the dogfood test skipping rather than failing, and the fix
    there was to remove the condition that triggered the skip rather than to
    tolerate it.

    Both CI jobs have bash - ubuntu natively, and pytest-windows runs under
    `defaults: {run: {shell: bash}}`, which is Git Bash. A machine without it
    cannot verify this repo, and should say so out loud.
    """
    bash = shutil.which("bash")
    if not bash:
        pytest.fail(
            "bash is not on PATH, so every executed-guard test in this file "
            "would be skipped and the suite would still exit 0. Install bash "
            "(Git Bash on Windows) and re-run; do not turn this back into a "
            "skip."
        )
    return bash


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


class TestTheCommentStripperTheseAssertionsRestOn:
    """`code()` is load-bearing, so it is tested rather than trusted.

    Every assertion of the form "this claim reached the SHELL and not just a
    comment" is only as good as this helper. It dropped whole-line comments
    alone, so the claim could be met by a comment after all - just one sitting
    at the end of a code line instead of on its own - and the full verifier
    stayed at 953 passed while the predicate both repos key on was hardcoded a
    second time inside the workflow.

    The two directions are tested together on purpose. A stripper that is too
    eager is the failure that gets a correct guard deleted: it would cut the
    step's own `::warning::` text at any `#` a message happened to contain,
    and accuse an author of a divergence they did not introduce.
    """

    def test_it_drops_a_trailing_comment(self):
        """The exact mutation the whole-line version let through."""
        body = (
            'drift=$(python3 -c \'print("reported" + "-failure")\') '
            '|| drift=""  # was read from AUDIT_DRIFT_STATUS\n'
        )
        assert "AUDIT_DRIFT_STATUS" not in code(body), (
            "a name that survives only in a trailing comment still counts as "
            "if the shell read it, so the hardcode this helper exists to "
            "catch passes"
        )
        assert "drift=$(" in code(body), "the command itself was thrown away"

    def test_it_keeps_a_hash_inside_a_quoted_string(self):
        """The negative control, and the reason this is not a regex.

        A `#` inside quotes is data - a run-page message, a URL fragment, an
        issue number. Cutting there would silently shorten the text these
        tests assert on and red a workflow that is perfectly correct.
        """
        body = (
            'echo "::warning::see issue #118 for why"\n'
            "echo 'a # inside single quotes is data too'\n"
            'echo "trailing is still a comment"  # but this one is not data\n'
        )
        kept = code(body)
        assert "#118" in kept, "a quoted `#` was read as the start of a comment"
        assert "a # inside single quotes is data too" in kept
        assert "but this one is not data" not in kept, (
            "the trailing comment survived on a line that also holds quotes"
        )

    def test_it_leaves_shell_syntax_that_merely_contains_a_hash(self):
        """`#` only opens a comment at the start of a word.

        `${v#x}` is prefix removal and `$#` is the argument count; neither is
        a comment, and a stripper that cut at them would delete real commands
        from the text every claim here is checked against.
        """
        body = 'name=${dest#../}\nif [ $# -gt 0 ]; then :; fi\n'
        kept = code(body)
        assert "${dest#../}" in kept
        assert "$# -gt 0" in kept

    def test_it_drops_a_whole_line_comment_as_it_always_did(self):
        body = "  # a whole line of prose\nreal_command\n"
        assert code(body).strip() == "real_command"


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

    def _summary(self):
        return echoed(next(
            s["run"] for s in load(RECORD)["jobs"]["record"]["steps"]
            if s.get("name") == "Summarise"
        ))

    def test_it_never_tells_the_reader_to_close_the_drift_issue(self):
        """The tail of this summary has now been wrong two ways, both of which
        cost the reader the same thing: they act on it.

        First it contradicted itself inside one paragraph - it opened "Nothing
        further from you", described the loop closing itself, and then said the
        last step is done by hand. Both cannot be true and the reader acts on
        the first, so they stop and the drift issue stays open reporting a
        problem that was already repaired.

        Then, having named the manual step, it named it WRONGLY: closing the
        drift issue is not the operator's job at all. skills-evals'
        `account-store-drift.yml` opens, edits and closes it, and the issue
        body's own step 4 says to leave it alone. An operator who closes it
        from their phone gets a DUPLICATE - the Tier-3 audit re-measures once a
        day, so the next drift run still reads `fail`, looks up `--state open`,
        finds nothing, and files a fresh issue.

        Both defects live in the same sentence, so one test holds the ground:
        this summary describes what the machinery does and never hands the
        reader the issue to close.
        """
        text = self._summary()
        assert "Nothing further from you" not in text
        assert "loop closes itself" not in text
        assert "Leave the drift issue alone" in text, (
            "the summary no longer tells the reader who owns the drift issue"
        )
        # "still manual", "by hand", "until ... lands" - the shapes that put a
        # close on the operator. `to close by hand` is the sanctioned use: the
        # sentence saying there is nothing here for them to close.
        assert "still manual" not in text
        assert text.count("by hand") == text.count("to close by hand")

    def test_it_pins_no_issue_or_pull_request_number(self):
        """A number written here cannot be kept true by anything.

        The drift design opens a FRESH issue per episode - the lookup is
        `--state open`, so a closed one is never found again - which makes a
        hardcoded issue number wrong from the second episode onward. A PR
        number is worse: this text shipped alongside the very PR it called
        "open and unmerged", so it was false the day it landed. Nothing in this
        repo watches either number, and a summary read on a phone is believed.

        Point at the workflow that owns the lifecycle instead. If a reference
        ever does come back, it carries the full `owner/repo#n` - a bare `#48`
        in an agentskills summary reads, and links, as an agentskills issue.
        """
        text = self._summary()
        refs = re.findall(r"(\S*)#(\d+)", text)
        assert not refs, (
            f"the summary pins {', '.join(p + '#' + n for p, n in refs)}, "
            f"which nothing updates when the next episode opens a new issue"
        )
        for prefix, number in refs:
            assert "Adam-S-Daniel/skills-evals" in prefix, (
                f"#{number} is written as {prefix}#{number}, which does not "
                f"resolve to the repo it lives in"
            )
        # The pointer that replaced them: a workflow name, which is stable
        # across every episode.
        assert "account-store-drift.yml" in text

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

    def test_the_summary_offers_the_route_a_phone_can_take(self):
        """The summary is read ON a phone, so it must not name only the route
        a phone cannot take.

        It used to say just "re-run --record-account-state from a session that
        has ~/.claude/skills/synced" - correct, and useless to the reader
        standing there holding the artifact they just uploaded. Both routes are
        named now; this keeps the dispatchable one from being edited back out.

        IT READS THE MODULE NOW, NOT THE `run:` BODY, and that is a deliberate
        follow of the code rather than a weakening. The summary moved into
        scripts/account_zip_selection.py when the selection grew a second
        source, because a heredoc cannot be unit-tested. Asserting on the
        workflow text alone would now pass on an empty module, so this asserts
        on the module AND that the pick step still invokes it - the strings
        have to sit on the path that actually renders. The rendering itself is
        covered in scripts/test_account_zip_selection.py.
        """
        pick = next(
            s for s in load(ZIPS)["jobs"]["pick"]["steps"]
            if s.get("name") == "Decide which skills need a ZIP"
        )["run"]
        assert "scripts/account_zip_selection.py" in pick, (
            "the pick step no longer calls the module this test reads"
        )
        text = SELECTION.read_text(encoding="utf-8")
        assert "Record an account upload" in text, "phone route not offered"
        assert "--record-account-state" in text, "mirror route not offered"
        # The run ID is filled in for the reader - having to go and find it is
        # the friction this whole path exists to remove.
        assert "GITHUB_RUN_ID" in text

    def test_it_runs_daily_on_one_offset_cron(self):
        """The condition it reacts to can become true with NO commit here.

        skills-evals' Tier-3 Routine publishes on its own schedule, so without
        a cron this workflow could only ever learn about account drift by
        accident, on the next unrelated push. Exactly one cron - a second entry
        would double every day's runs for no new information - and a non-zero
        minute, because GitHub queues the whole platform's cron on the hour and
        delays it. Offset from ci.yml's `17 6 * * *` so the two do not contend.
        """
        triggers = load(ZIPS)["on"]
        assert "schedule" in triggers, "no schedule trigger"
        crons = [entry["cron"] for entry in triggers["schedule"]]
        assert len(crons) == 1, crons
        minute = crons[0].split()[0]
        assert minute not in ("0", "00", "*"), crons[0]

    def test_the_recording_that_decides_staleness_is_a_salient_path(self):
        """The bug this locks out, found live.

        `account-state.json` sits at the repo ROOT deliberately - a copy inside
        a skill directory would be uploaded as part of that skill by
        `zip_skill()` - so it matches neither `plugins/*/skills/**` nor the
        workflow's own path. A merged `record-account-upload` PR, whose diff is
        exactly and only that file, therefore did not re-run this workflow at
        all, and the header comment claimed the filter covered "the recording
        that decides staleness" while it did not.
        """
        paths = load(ZIPS)["on"]["push"]["paths"]
        assert "account-state.json" in paths, paths
        # The selection module decides what gets built; a change to it that
        # nothing re-ran would be the same silent no-op one level up.
        assert "scripts/account_zip_selection.py" in paths, paths

    def test_the_verdict_is_imported_from_skills_evals_not_re_derived(self):
        """One predicate, two repos, and only one implementation of it.

        `freshness_verdict` is what opens, updates and closes the tracking
        issue "Account skill store drifted from registry (automated Tier-3
        audit)". A copy of that rule here would be free to disagree with the
        issue - ZIPs for a condition nobody filed, or silence while an issue
        sat open - and nothing would report the divergence. So the pick job
        clones skills-evals and calls ITS function, with the age limit read
        from ITS fixture rather than pasted into a constant.
        """
        audit = next(
            s for s in load(ZIPS)["jobs"]["pick"]["steps"]
            if s.get("id") == "audit"
        )["run"]
        assert "freshness_verdict" in audit, "the verdict is not imported"
        assert "--branch eval-results" in audit, "the artifact branch is not read"
        assert "Adam-S-Daniel/skills-evals" in audit
        assert "account/latest.json" in audit
        assert "fixture.yaml" in audit, "max_age_days was hard-coded"
        # Re-deriving staleness would need a comparison against the age limit
        # right here. Importing means this step does arithmetic on nothing.
        assert "timedelta" not in audit

    def test_the_cross_repo_condition_is_named_in_exactly_one_place(self):
        """`reported-failure` is the string both repos key on.

        It has to be written down on this side or the coupling is invisible to
        a reader, and it has to be DECIDED in one place or the next edit
        updates one copy of it. So: exactly one string literal, in the module,
        as a named constant - and in the workflow the name may appear only in a
        comment explaining the coupling, never in a line that acts on it.
        """
        module = SELECTION.read_text(encoding="utf-8")
        assert "reported-failure" in module
        assert module.count('"reported-failure"') == 1, (
            "the predicate is written out more than once in the module"
        )
        for line in ZIPS.read_text(encoding="utf-8").splitlines():
            if "reported-failure" in line:
                assert line.strip().startswith("#"), (
                    f"the workflow acts on the predicate itself: {line!r}"
                )

    def test_no_run_body_interpolates_a_workflow_expression(self):
        """A `${{ }}` expansion inside `run:` is rendered into the command and
        echoed to a PUBLIC log, and is attacker-controlled shell. Values reach
        a script through `env:` only.

        `github.server_url` / `github.repository` / `github.run_id` are the one
        tolerated exception in these repos (ci.yml builds a run URL that way),
        so they are allowed here rather than silently forcing a rewrite of a
        file this change does not touch.
        """
        allowed = re.compile(
            r"\$\{\{\s*github\.(server_url|repository|run_id)\s*\}\}")
        for path in (ZIPS, RECORD):
            for body in runs(load(path)):
                assert "${{" not in allowed.sub("", body), path.name

    def test_the_zip_payload_is_built_by_the_real_uploader(self):
        """Re-walking the skill directory here would be a second
        implementation of `_include_in_zip`, free to drift from the one an
        actual upload uses. The payload comes from `--prepare --zip-dir`.
        """
        assert any("--zip-dir" in body for body in runs(load(ZIPS)))


class TestTheAuditStepAnnouncesEveryDegradedVerdict:
    """The audit step's own tail, EXECUTED - not string-matched.

    THE FAILURE THIS LOCKS OUT IS A GREEN RUN THAT ASKED NOTHING. Every other
    failure in that step (both clones, the pyyaml install) raises a
    `::warning::`; TWO paths failed OPEN, and each has its own test below.

    The first is the empty-capture fallback, taken when the cross-repo IMPORT
    breaks: skills-evals moves `account_store.py`, renames
    `freshness_verdict`, or renames the `account_audit_max_age_days` fixture
    key. Both clones succeed, neither existing warning fires, the heredoc
    raises, and with a clean local recording `count=0` skips the zip job. The
    run ends GREEN with zero annotations, and scheduled-run-health.yml - which
    scans for FAILED runs - never reports it either.

    The second is quieter still, because nothing raises at all: a verdict that
    is not empty and is not a status this repo has a name for. It passes the
    empty-capture guard, and until the `case` grew a `*)` arm it matched
    nothing, said nothing, and was folded to `unavailable` one step later. A
    skills-evals RENAME of the drift status is exactly that shape - the whole
    cross-repo half of the evidence goes unused and the run page shows no sign
    of it.

    The liveness verdicts are here for the same reason one level out: `stale`,
    `missing` and `unreadable` all mean the Tier-3 Routine stopped publishing a
    usable result, which without an annotation is visible only inside a step
    summary nobody opens on a green run.

    Run rather than grepped because a lint that greps for `::warning::` passes
    on a warning that sits in the wrong branch.
    """

    def _tail(self):
        """Everything the audit step does after the heredoc returns.

        Anchored on the heredoc's own `) || verdict=""` rather than on a line
        number, so the slice follows the code if it moves.
        """
        body = next(
            s["run"] for s in load(ZIPS)["jobs"]["pick"]["steps"]
            if s.get("id") == "audit"
        )
        marker = ') || verdict=""'
        assert marker in body, (
            "the empty-capture fallback changed shape; this test no longer "
            "knows where the step's verdict handling starts"
        )
        return body[body.index(marker) + len(marker):]

    def _run(self, tmp_path, verdict, *, harness_ok="yes", results_ok="yes",
             pip_ok="yes"):
        bash = require_bash()
        # THE THREE FLAGS THE STEP HEAD SETS AND THE TAIL READS, delivered
        # here by their real names. "yes" on all three is the healthy head -
        # both clones and the install green - which is the state every
        # verdict-driven test below assumes.
        #
        # They are the state of each PATH, not a tally of annotations already
        # raised, and that is the whole point: the empty-capture guard has to
        # know WHICH earlier thing failed, because a clone failure explains an
        # empty capture and a pip failure does not. A single `annotated` flag
        # stood here once and could not tell them apart, so a transient pip
        # hiccup silenced the cross-repo import annotation.
        #
        # Hardcoding the NAMES is safe in the direction that matters. Rename a
        # flag in the workflow and the tail dereferences an unset variable
        # under `set -u`, which exits non-zero and reds the assertion below
        # with the shell's own message. It cannot quietly test a stale shape.
        assert harness_ok in ("yes", "no"), harness_ok
        # `results_ok` is also what tells `not-yet-bootstrapped` apart from a
        # branch that is not published yet.
        assert results_ok in ("yes", "no"), results_ok
        assert pip_ok in ("yes", "no"), pip_ok
        out = tmp_path / "gh_output"
        # Forward slashes: Git Bash reads the backslashes of a Windows path as
        # escapes inside the step's own `>> "$GITHUB_OUTPUT"` redirect, and
        # pytest-windows runs this file too.
        env = {**os.environ, "GITHUB_OUTPUT": str(out).replace("\\", "/")}
        # The verdict arrives as an ARGUMENT rather than baked into the script,
        # so the empty case is delivered as an actual empty string.
        #
        # cwd IS THE REPO ROOT BECAUSE THAT IS WHERE THE RUNNER STANDS. The
        # tail reads the drift predicate out of scripts/account_zip_selection.py
        # with a relative PYTHONPATH, exactly as the next step in that job runs
        # `python3 scripts/account_zip_selection.py`; both resolve against
        # $GITHUB_WORKSPACE. Inheriting pytest's cwd instead would make the
        # quiet arm depend on where the suite happened to be started from.
        #
        # `-e`, BECAUSE THAT IS THE SHELL THE STEP ACTUALLY GETS. The workflow
        # declares no `shell:` and no `defaults:`, so GitHub runs every `run:`
        # body as `/usr/bin/bash -e {0}`, and the step's own `set -uo pipefail`
        # cannot take that back - `set -o` only ENABLES options. Spawning this
        # tail without `-e` made the harness differ from production in exactly
        # the flag that decides whether an unguarded failure aborts the step,
        # so a command that fails soft here would abort there and this suite
        # would report the opposite of what the runner does.
        # `verdict` is $1 of the SCRIPT, so it is passed straight after the
        # path - there is no `-c` placeholder $0 to absorb an argument.
        script = _script(
            tmp_path,
            f"set -uo pipefail\nharness_ok={harness_ok}\n"
            f"results_ok={results_ok}\npip_ok={pip_ok}\nverdict=$1\n"
            + self._tail(),
            name="tail.sh",
        )
        proc = subprocess.run(
            [bash, "-e", script, verdict],
            capture_output=True, text=True, env=env, cwd=str(REPO),
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout, out.read_text(encoding="utf-8")

    # `git` and `python3 -m pip` as SHELL FUNCTIONS, prepended to the step
    # body, so that `_run_step` below can execute the head of the step without
    # a network and without a PATH stub.
    #
    # Functions rather than executables on PATH because a function is the one
    # form that behaves identically on both CI jobs: no shebang, no execute
    # bit, no Windows path translation, and bash resolves a function ahead of
    # PATH so the real `git` cannot win by accident. A test that reached the
    # network here would clone Adam-S-Daniel/skills-evals on every run of the
    # suite - non-deterministic by AGENTS.md's rule, and slow.
    #
    # The `git` double answers on the DESTINATION argument, which is the only
    # thing the step varies between its two clones, and it creates the tree on
    # success so the step sees the same directory layout it would get from a
    # real clone with a moved payload. It never fabricates the skills-evals
    # harness itself: what is under test here is the step's REACTION to a
    # clone, not skills-evals' own code, and a fake `freshness_verdict` would
    # be exactly the second implementation this workflow exists to avoid.
    #
    # The `python3` double intercepts `-m pip` alone and hands everything else
    # to the real interpreter, so the heredoc and the drift read are genuine.
    # `${1-}` / `${2-}`, not `$1` / `$2`: the step calls `python3 -` with a
    # single argument and the body runs under `set -u`.
    #
    # `$FAIL_PIP` is the install's exit status rather than a yes/no, so the
    # double reproduces the real failure shape - a non-zero `python3 -m pip`
    # that the step catches in a `||` list - instead of a stub that merely
    # reports one. It is what crosses a pip failure with every clone state
    # below; that combination was executed by no test, which is how a pip
    # hiccup silently deleted the import annotation.
    STUBS = """
    git() {
      dest=""
      for arg in "$@"; do dest="$arg"; done
      case "$dest" in
        *skills-evals) if [ "$FAIL_HARNESS" = yes ]; then return 128; fi ;;
        *eval-results) if [ "$FAIL_RESULTS" = yes ]; then return 128; fi ;;
      esac
      mkdir -p "$dest"
    }
    python3() {
      if [ "${1-}" = -m ] && [ "${2-}" = pip ]; then return "$FAIL_PIP"; fi
      command python3 "$@"
    }
    """

    def _run_step(self, tmp_path, *, unreachable=(), placeholder_blocked=False,
                  pip_broken=False):
        """The WHOLE audit step - clones, install, heredoc and tail - executed.

        `_tail()` starts at the heredoc's `) || verdict=""`, so everything
        above that point is run by nothing else in this suite. That head is
        where the step's most likely fault lives: skills-evals unreachable, on
        a GitHub outage, a rate limit, or the repo renamed or made private. It
        needs no cross-repo rename to happen, and how many annotations it
        produces cannot be seen from the tail.

        The sandbox is `tmp_path/workspace`, so the step's `../` clones land in
        `tmp_path` rather than beside the real checkout, and it carries a copy
        of account_zip_selection.py at the path the drift read expects.
        """
        bash = require_bash()
        workspace = tmp_path / "workspace"
        (workspace / "scripts").mkdir(parents=True)
        shutil.copy(SELECTION, workspace / "scripts" / SELECTION.name)
        if placeholder_blocked:
            # `mkdir -p` refuses a path that already exists and is not a
            # directory, on every platform and without needing a permission
            # the test would have to be root to arrange.
            (tmp_path / "eval-results").write_text("a file\n", encoding="utf-8")
        out = tmp_path / "gh_output"
        env = {
            **os.environ,
            "GITHUB_OUTPUT": str(out).replace("\\", "/"),
            "FAIL_HARNESS": "yes" if "harness" in unreachable else "no",
            "FAIL_RESULTS": "yes" if "results" in unreachable else "no",
            "FAIL_PIP": "1" if pip_broken else "0",
        }
        script = _script(tmp_path, self.STUBS + self._body())
        proc = subprocess.run(
            [bash, "-e", script],
            capture_output=True, text=True, env=env, cwd=str(workspace),
        )
        assert proc.returncode == 0, (
            "the audit step ABORTED. Under the runner's `bash -e` an unguarded "
            f"failure ends the step before it reports anything:\n{proc.stderr}"
        )
        assert "checked out" in proc.stdout or "::warning::" in proc.stdout, (
            "the step produced neither a clone nor a warning, so the doubles "
            f"above did not stand in for the real ones:\n{proc.stdout}"
        )
        return proc.stdout, out.read_text(encoding="utf-8")

    @pytest.mark.parametrize("unreachable, names, why", [
        (("harness", "results"), "could not reach Adam-S-Daniel/skills-evals",
         "one unreachable repository is ONE fault - the two clones pull from "
         "the same one, so whatever takes out the first takes out the second"),
        (("harness",), "could not clone the skills-evals harness",
         "the harness clone alone"),
        (("results",), "eval-results branch not present yet",
         "the published-artifact clone alone"),
        ((), "the import inside this step",
         "both clones green and the heredoc still raised, which is the "
         "cross-repo import breaking and the one this annotation may blame"),
    ])
    def test_a_fault_in_the_step_head_raises_exactly_one_annotation(
            self, tmp_path, unreachable, names, why):
        """EXACTLY one, and it must be about the fault that actually happened.

        This is the step's most likely failure and the only one that needs no
        cross-repo rename: skills-evals unreachable, on an outage, a rate limit
        or the repo renamed or made private. It produced THREE annotations -
        one per clone, then the empty-capture fallback - and the third told the
        reader "the clones can succeed while the import inside this step
        breaks", sending them to hunt a moved module in the other repo while
        the real cause sat two lines above, already annotated.

        A wrong label is worse than no label because it is read and believed,
        and the count matters for the same reason the class docstring gives:
        a reader who learns the annotations are duplicated stops counting them,
        and the silent one goes unnoticed.

        ONE fault is what these rows arrange - the pyyaml install is green in
        every one of them. The rule is one annotation per DISTINCT fault, not
        one per run, and the sibling test below crosses each of these rows
        with a pip failure to hold the other half of it.
        """
        log, out = self._run_step(tmp_path, unreachable=unreachable)
        annotations = [line for line in log.splitlines() if "::warning::" in line]
        assert len(annotations) == 1, f"{why}\n{log}"
        assert names in annotations[0], (
            f"the annotation does not name the fault that happened: "
            f"{annotations[0]}"
        )
        if unreachable:
            assert "import inside this step" not in annotations[0], (
                "the annotation blames the cross-repo import on a run where a "
                "clone failed, so it asserts a path succeeded that never ran"
            )
        # The verdict still reaches the next step, whichever fault it was.
        assert "status=unavailable" in out

    @pytest.mark.parametrize("unreachable, names, why", [
        (("harness", "results"), "could not reach Adam-S-Daniel/skills-evals",
         "the unreachable repository and the failed install are two faults"),
        (("harness",), "could not clone the skills-evals harness",
         "the harness clone and the failed install are two faults"),
        (("results",), "eval-results branch not present yet",
         "the missing branch and the failed install are two faults"),
        ((), "the import inside this step",
         "THE REGRESSION THIS ROW EXISTS FOR: both clones green, pip down, "
         "and the cross-repo import broken - the import must still be named"),
    ])
    def test_a_pip_failure_is_a_second_fault_and_deletes_no_annotation(
            self, tmp_path, unreachable, names, why):
        """The rows above, crossed with a failing `python3 -m pip`.

        THE RULE IS ONE ANNOTATION PER DISTINCT FAULT, NOT ONE PER RUN, and
        this cross is where the difference bites. A tally of "has anything
        annotated yet" made the pyyaml guard suppress the empty-capture guard,
        so a run where skills-evals had renamed `account_store.py` AND pip
        hiccupped carried exactly one annotation - naming pip. The pip line
        hedges its own relevance ("unless it is already present"), so the
        reader was sent after a transient hiccup while the durable cross-repo
        rename this whole step exists to surface was annotated nowhere.

        The last row is that scenario. It is the one combination the
        one-fault test above cannot reach: it parametrizes clone states only,
        and pip was green in all four, so nothing executed this at all.

        A clone failure is genuinely different and stays consolidated - the
        heredoc reads out of the tree that did not land, so an empty capture
        there is that fault's consequence. Those rows therefore still hold at
        two annotations, not three: the clone fault, and the install.
        """
        log, _ = self._run_step(
            tmp_path, unreachable=unreachable, pip_broken=True)
        annotations = [l for l in log.splitlines() if "::warning::" in l]
        assert len(annotations) == 2, (
            f"{why}, so the run page must carry both:\n{log}"
        )
        install = [l for l in annotations if "pyyaml install failed" in l]
        assert len(install) == 1, f"the failed install went unreported:\n{log}"
        other = [l for l in annotations if l not in install]
        assert names in other[0], (
            f"the second annotation does not name the other fault ({names}); "
            f"a pip failure must not stand in for it:\n{other[0]}"
        )
        if not unreachable:
            # The text has to stop claiming what is no longer true. Saying the
            # install succeeded, on the run where it did not, is the same
            # wrong-label failure one level down.
            assert "the pyyaml install succeeded" not in other[0], (
                "the import annotation asserts the pyyaml install succeeded "
                f"on a run where it failed: {other[0]}"
            )

    def test_an_empty_verdict_does_not_re_annotate_a_reported_fault(
            self, tmp_path):
        """The tail half of the same invariant.

        `unavailable` set by the empty-capture guard is not a fault of its own
        when a CLONE failed above - the heredoc reads out of both trees, so a
        tree that is not on disk is why the capture came back empty. The log
        still records it; the Actions list does not get a second entry for it.

        Driven by the flag the head actually sets, not by a synthetic
        "something annotated" bit: which earlier path failed is the whole
        distinction the guard makes, and the sibling test below covers the
        earlier path that does NOT explain an empty capture.
        """
        log, out = self._run(tmp_path, "", harness_ok="no")
        assert "::warning::" not in log, log
        assert "no verdict" in log, (
            "the empty capture went unrecorded entirely; quiet on the "
            "annotations list is not quiet in the log"
        )
        assert out.strip() == "status=unavailable"

    def test_an_empty_verdict_after_a_failed_install_still_annotates(
            self, tmp_path):
        """The tail half of the pip cross, at the guard itself.

        The step head decides WHETHER to warn from three flags; this drives
        the tail directly with the one state that used to be silent. Reverting
        the guard to a single "has anything annotated" tally reds here and in
        the parametrized head test above, from opposite directions.

        The text is asserted as well as the count, because the annotation has
        to stay TRUE: on this run the install did not succeed, and a line
        saying it did would be the wrong label the guard exists to avoid.
        """
        log, out = self._run(tmp_path, "", pip_ok="no")
        assert log.count("::warning::") == 1, (
            "an empty capture after a failed pyyaml install raises no "
            f"annotation, so a cross-repo rename would reach nobody:\n{log}"
        )
        annotation = next(l for l in log.splitlines() if "::warning::" in l)
        assert "the import inside this step" in annotation, (
            "the annotation does not send the reader at the import, which is "
            f"what an empty capture with both clones green means: {annotation}"
        )
        assert "the pyyaml install succeeded" not in annotation, (
            "the annotation claims the pyyaml install succeeded on a run "
            f"where it failed: {annotation}"
        )
        assert out.strip() == "status=unavailable"

    def test_a_placeholder_it_cannot_create_does_not_abort_the_step(
            self, tmp_path):
        """The missing-eval-results branch is the one the step's own comment
        promises is "reported and never fatal", and it held the one unguarded
        command in the step.

        `mkdir -p ../eval-results` fails when that path exists as a file or its
        parent is not writable. Under `bash -e` that ended the step on the spot
        - before the empty-capture guard, before the `case`, before the
        `status=` write - so the branch documented as never fatal was the one
        that killed the job, and the workflow reported nothing at all rather
        than reporting DEGRADED.
        """
        log, out = self._run_step(
            tmp_path, unreachable=("results",), placeholder_blocked=True)
        assert "status=unavailable" in out, (
            "the step never reached its output write, so the next step reads "
            "no status at all"
        )
        assert "eval-results branch not present yet" in log

    @pytest.mark.parametrize("verdict, recorded, why", [
        ("", "unavailable",
         "the heredoc raised - a broken cross-repo import looks exactly like "
         "this, with both clones green"),
        ("stale", "stale", "the Tier-3 Routine has stopped publishing"),
        ("missing", "missing", "nothing has been published to read"),
        ("unreadable", "unreadable", "what was published cannot be parsed"),
    ])
    def test_a_verdict_the_run_could_not_use_raises_an_annotation(
            self, tmp_path, verdict, recorded, why):
        log, out = self._run(tmp_path, verdict)
        # EXACTLY one, not at least one. Each of these is a single fault, and
        # an arm that annotated a fault a previous arm had already reported
        # would teach a reader that the annotations are duplicated - after
        # which they stop counting them, which is how the silent one below
        # goes unnoticed. `unavailable` is the case that makes this concrete:
        # it is set by the empty-capture branch, which warns as it sets it.
        assert log.count("::warning::") == 1, why
        # The verdict still reaches the selection module unchanged - the
        # annotation is additional, not a substitute.
        assert f"status={recorded}" in out

    @pytest.mark.parametrize("verdict, why", [
        ("account-drifted", "the shape a rename of the drift status takes"),
        ("throttled", "a verdict skills-evals could add tomorrow"),
    ])
    def test_a_status_this_repo_has_never_heard_of_raises_an_annotation(
            self, tmp_path, verdict, why):
        """THE SILENT-GREEN HOLE, and the one fault that annotated nowhere.

        A non-empty verdict this repo has no name for passes the
        `if [ -z "$verdict" ]` guard untouched, matched no arm of the `case`,
        and was then normalised to `unavailable` inside
        account_zip_selection.py - so the audit contributed no skill names, the
        Degraded notice reached the STEP SUMMARY alone, and the run finished
        green with zero annotations while the entire cross-repo half of the
        evidence went unused. Both clones succeeded; the import succeeded; only
        the vocabulary had moved.

        WHAT THIS TEST CANNOT COVER, STATED SO IT IS NOT READ AS MORE. It
        cannot catch the rename itself. skills-evals is not a dependency of
        this repo, CI here has no network, and nothing local can observe that
        `reported-failure` became something else - which is exactly why the
        `*)` arm exists at RUNTIME: the annotation is the mechanism for the
        cross-repo case, and this test only proves the annotation fires.
        """
        log, out = self._run(tmp_path, verdict)
        assert log.count("::warning::") == 1, why
        # ON THE ANNOTATION LINE, because "somewhere in stdout" is a guarantee
        # this step gives for free. This read `verdict in log`, and the step
        # echoes `published account audit reads: $verdict` a few lines above
        # the `case` on EVERY run - so the assertion was true before the `*)`
        # arm ran and stayed true however that arm was worded. Measured:
        # rewriting the annotation to say "a status that is not one this repo
        # knows", naming nothing, left this file at 39 passed while the
        # assertion whose sole job is to require the name said nothing.
        #
        # Which status it was is the actionable half. An Actions annotation
        # reading "skills-evals' verdict vocabulary has moved" and not saying
        # to WHAT sends the reader to diff two repos; naming the word turns it
        # into one edit to account_zip_selection.py.
        annotation = next(l for l in log.splitlines() if "::warning::" in l)
        assert verdict in annotation, (
            "the annotation does not name the status it could not use, so the "
            f"run page says a vocabulary moved without saying to what: "
            f"{annotation}"
        )
        # Unchanged on the way out, as with every other verdict: the selection
        # module is what decides an unknown string means "unusable", and it
        # cannot do that on a string this step rewrote.
        assert f"status={verdict}" in out

    @pytest.mark.parametrize("verdict", [
        "fresh",
        # The drift verdict - the condition this whole workflow exists to react
        # to - reaches the case like any other and MUST stay quiet. It is also
        # the one status this file cannot spell (the module owns it, see
        # test_the_cross_repo_condition_is_named_in_exactly_one_place), so the
        # quiet arm matches it through a variable the step reads back out of
        # the module. That indirection is what this parameter exercises: it
        # goes red if the read breaks and the happy path starts warning.
        sel.AUDIT_DRIFT_STATUS,
        # Delivered directly rather than through the empty capture that
        # normally produces it, which is the point: the branch that sets it
        # already annotated, so a second annotation here would be one fault
        # reported twice.
        sel.UNAVAILABLE,
    ])
    def test_a_healthy_verdict_stays_quiet(self, tmp_path, verdict):
        """The negative control. A warning on every run is a warning nobody
        reads, so an annotation here would cost the ones above their meaning -
        and would also pass the test above for the wrong reason.
        """
        log, out = self._run(tmp_path, verdict)
        assert "::warning::" not in log, verdict
        assert f"status={verdict}" in out

    def _body(self):
        return next(
            s["run"] for s in load(ZIPS)["jobs"]["pick"]["steps"]
            if s.get("id") == "audit"
        )

    def _case_statuses(self):
        """The arm patterns of the audit step's `case`, split on `;;`.

        Returns (the step body, literal statuses, variable patterns).

        SPLIT ON `;;` RATHER THAN ON NEWLINES, WHICH IS THE WHOLE POINT OF
        THIS HELPER. An arm is `PATTERN) BODY ;;` and bash does not care where
        the newlines fall inside it: `quota-exceeded) : ;;` written on ONE line
        is the same arm as the two-line form. This used to look for a pattern
        by scanning for a line ENDING in `)`, which sees neither half of that
        arm - the pattern shares its line with the body, so no line ends in
        `)` - and skipped it in silence. Splicing exactly that line above the
        catch-all left the full verifier at 945 passed while the `case` quietly
        swallowed a status account_zip_selection.py has never heard of, which
        is verbatim the divergence the test below exists to prevent. The
        one-line form is not hypothetical either: setup.sh:69, :70 and :270 all
        use it, and so does record-account-upload.yml.
        The asymmetry ran the wrong way as well - reformatting the CORRECT
        `*)` arm onto one line made the same scan report that the catch-all was
        MISSING, so it accused on a harmless reformat and stayed quiet on a
        harmful one. AGENTS.md's rule for workflow invariants is the parsed
        one for this reason: a scan "reads clean on text it cannot see".

        Anything inside the block that is neither a comment nor an arm with a
        pattern is an ERROR rather than a skip, so a shape this does not
        understand cannot pass for an empty one.
        """
        body = self._body()
        # Anchored on a LINE OF CODE, not on the first occurrence of the text.
        # This step's comments quote its own shell heavily (the paragraph above
        # the `case` names `$verdict` twice), and `body.index` would follow the
        # first comment that ever quotes the opener verbatim - relocating the
        # slice to a region with no arms in it, where every assertion below
        # passes on an empty set.
        opener = re.search(r'^[^\S\n]*case "\$verdict" in[^\S\n]*$', body, re.M)
        assert opener, (
            "the audit step's `case` opener is no longer a line of its own; "
            "this test no longer knows which block it is reading"
        )
        block = body[opener.end():body.index("esac", opener.end())]
        literals, variables = set(), set()
        # All three arm terminators, because bash has three: `;;` ends an arm,
        # `;&` falls through to the next arm's body and `;;&` goes on testing
        # patterns. Splitting on `;;` alone would swallow the arm after a `;&`
        # exactly the way the line scan swallowed a one-line one.
        for chunk in re.split(r";;&|;;|;&", block):
            code = "\n".join(
                line for line in chunk.splitlines()
                if not line.strip().startswith("#")
            ).strip()
            if not code:
                continue
            pattern, closer, _ = code.partition(")")
            assert closer, (
                "an arm of the audit step's `case` has no pattern this test "
                f"can read, so its statuses were counted as absent:\n{code}"
            )
            for token in pattern.split("|"):
                token = token.strip()
                (variables if "$" in token else literals).add(token)
        return body, literals, variables

    def test_the_case_block_names_every_status_the_module_knows(self):
        """The two vocabularies inside THIS repo cannot drift apart silently.

        The `case` decides which verdicts are worth an annotation;
        account_zip_selection.py decides which ones mean anything at all. They
        are separate files in separate languages, and a status added to one is
        invisible to the other - a new skills-evals verdict taught to the
        module alone would land in the `*)` arm and be reported as unrecognised
        when it is not, and one taught to the workflow alone would be quiet
        here and normalised to `unavailable` one step later.

        The drift predicate is the deliberate hole in the enumeration: this
        workflow may not spell it (see
        test_the_cross_repo_condition_is_named_in_exactly_one_place), so the
        quiet arm matches it through a variable the step reads back out of the
        module's own constant. Asserted as a variable rather than trusted:
        `"$drift"` matching nothing at all would be indistinguishable from a
        working arm on every verdict except that one.

        This is a same-repo agreement and nothing more. A rename INSIDE
        skills-evals moves a string no test here can see - agentskills' CI has
        no network and does not depend on that repo - which is why the runtime
        `*)` annotation, not this test, is what covers that case.
        """
        body, literals, variables = self._case_statuses()
        assert "*" in literals, (
            "no catch-all arm: a status this repo has never heard of matches "
            "nothing, says nothing, and the run goes green having used the "
            "local recording alone"
        )
        literals.discard("*")
        assert literals | {sel.AUDIT_DRIFT_STATUS} == (
            sel.KNOWN_STATUSES | {sel.UNAVAILABLE}), (
            "the `case` and account_zip_selection.py disagree about which "
            "statuses exist; every one the module knows needs an arm, or it "
            "is annotated as unrecognised when it is not"
        )
        # QUOTED, and that is behaviour rather than house style. A `case`
        # pattern undergoes parameter expansion, and an UNQUOTED expansion is
        # then read as a pattern - so if the module's constant ever held a glob
        # metacharacter the quiet arm would stop matching one status and start
        # matching everything, swallowing every verdict this step exists to
        # annotate. Measured, with the two forms side by side:
        #
        #   d="*"; case anything in fresh|"$d") quiet ;; *) loud ;; esac -> loud
        #   d="*"; case anything in fresh|$d)   quiet ;; *) loud ;; esac -> quiet
        #
        # The set is asserted whole for the other half of the same guarantee:
        # `"$drift"` matching NOTHING at all - a read that silently returned
        # empty - looks exactly like a working arm on every verdict except the
        # drift one, which is the single verdict this workflow exists for.
        assert variables == {'"$drift"'}, (
            "the quiet arm no longer matches the drift verdict through exactly "
            "one quoted expansion of the module's constant"
        )
        # `code(body)`, never `body`: see that helper. The claim here is about
        # what the step RUNS, and this file's own docstring says asserting on
        # the raw body "would let a claim that only survives in a comment count
        # as if it reached the reader".
        step = code(body)
        assert "drift=$(" in step, (
            "the quiet arm's variable is no longer read from the module's "
            "constant, so it can hold anything - including nothing"
        )
        # ON THE `drift=$(` LINE, not merely somewhere in the step. The step
        # is one long body and the name could reach it from anywhere - an
        # `echo` that mentions the constant, an unrelated command - while the
        # read itself was quietly replaced by a literal. Pinning the claim to
        # the command that has to make it leaves the assertion nowhere else to
        # be satisfied from.
        read = [l for l in step.splitlines() if l.strip().startswith("drift=$(")]
        assert len(read) == 1, (
            f"expected exactly one `drift=$(` command to check, found "
            f"{len(read)}: {read}"
        )
        assert "AUDIT_DRIFT_STATUS" in read[0], (
            "the drift verdict is no longer read from the module's constant - "
            f"it is spelled a second time inside the workflow: {read[0]}"
        )

    def test_a_published_tree_that_moved_is_not_read_as_a_fresh_install(
            self, tmp_path):
        """THE FOURTH FAULT IN THE FAMILY, and the one that annotated nowhere.

        The empty-capture guard covers skills-evals moving `account_store.py`,
        renaming `freshness_verdict`, or renaming the
        `account_audit_max_age_days` fixture key - all three raise, so the
        capture is empty and the guard warns. Moving the published ARTIFACT
        PATH raises nothing: both clones succeed, the import succeeds, the
        heredoc simply finds no `propagation/account/latest.json` and no
        `propagation/.bootstrapped` - they live in the same directory, so one
        rename takes both - and `freshness_verdict` answers
        `not-yet-bootstrapped`. That is a word this repo knows, so the empty
        guard cannot see it and the `*)` arm cannot either. It sat in the quiet
        arm: green run, zero annotations, the cross-repo half of the evidence
        unused, and the Degraded notice reaching only the step summary.

        The distinguishing fact is the clone, which is why this drives
        `results_ok` rather than moving the status to the loud arm - see the
        negative control below.
        """
        log, out = self._run(
            tmp_path, "not-yet-bootstrapped", results_ok="yes")
        assert log.count("::warning::") == 1, log
        annotation = next(line for line in log.splitlines() if "::warning::" in line)
        assert "not-yet-bootstrapped" in annotation, annotation
        assert "cloned cleanly" in annotation, (
            "the annotation does not say what makes this a fault rather than "
            f"a beginning - that the branch was there and the tree was not: "
            f"{annotation}"
        )
        assert out.strip() == "status=not-yet-bootstrapped"

    def test_a_branch_that_is_not_published_yet_stays_quiet(self, tmp_path):
        """The negative control for the test above, and the reason it is gated
        on the clone rather than on the status.

        `not-yet-bootstrapped` is the honest answer before the first audit run
        ever publishes, and a workflow that annotates every run of a fresh
        install is one whose annotations get ignored - which would cost every
        other annotation in this step its meaning. When the eval-results branch
        is genuinely absent the clone above says so and has already annotated
        it; a second entry here would be one fault reported twice.

        This case used to be a parameter of test_a_healthy_verdict_stays_quiet,
        which asserted quiet for the status unconditionally. It is asserted
        here for the state where quiet is CORRECT, and the test above asserts
        loud for the state where it is not.
        """
        log, out = self._run(tmp_path, "not-yet-bootstrapped", results_ok="no")
        assert "::warning::" not in log, log
        assert out.strip() == "status=not-yet-bootstrapped"

    def test_nothing_in_this_workflow_overrides_the_runner_s_shell(self):
        """The premise the harness above rests on, asserted instead of assumed.

        `_run` spawns the step's tail under `bash -e` BECAUSE that is what the
        runner gives it: with no `shell:` and no `defaults:` anywhere in the
        file, GitHub runs every `run:` body as `/usr/bin/bash -e {0}`, and the
        step's own `set -uo pipefail` cannot take errexit away because `set -o`
        only ENABLES options. That premise was stated twice in comments - once
        in the workflow, once in `_run` - and enforced nowhere.

        A `defaults: {run: {shell: bash --noprofile --norc {0}}}` is a normal,
        reasonable-looking thing to add to a workflow, and it drops errexit in
        production while this harness keeps it. Every execution test above
        would stay green on a step that no longer behaves the way they run it,
        which is the harness lying in the safer-looking direction: a command
        that fails soft here would abort there.

        Parsed, per AGENTS.md - the same shape as scripts/test_ci_workflow.py
        holding ci.yml's no-`concurrency` invariant.
        """
        wf = load(ZIPS)
        assert "defaults" not in wf, (
            "a workflow-level `defaults:` overrides the runner's "
            "`/usr/bin/bash -e {0}`, so the shell the tests execute is no "
            "longer the shell the step gets"
        )
        for job_id, job in wf["jobs"].items():
            assert "defaults" not in job, (
                f"job `{job_id}` declares `defaults:`; see above"
            )
            for step in job.get("steps", []):
                assert "shell" not in step, (
                    f"step `{step.get('name') or step.get('id')}` in job "
                    f"`{job_id}` declares `shell:`; see above"
                )

    def test_the_verdict_still_reaches_the_step_output(self, tmp_path):
        """Whatever else the step says, `status=` is the only thing the next
        step consumes. An annotation that swallowed it would be a worse bug
        than the silence it replaced.
        """
        _, out = self._run(tmp_path, "")
        assert out.strip() == "status=unavailable"


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
        bash = require_bash()
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
        bash = require_bash()
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
