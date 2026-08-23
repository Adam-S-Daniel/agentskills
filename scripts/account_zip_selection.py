#!/usr/bin/env python3
"""Decide which account-store skills `account-skill-zips` builds a ZIP for.

WHY THIS IS A MODULE AND NOT A `run:` HEREDOC
It used to be a `python3 - <<'PY'` block inside the workflow, which meant the
only way to learn what it selects was to merge a change and read a run's
summary. The selection now answers to a published cross-repo condition (see
`stale` below), which is exactly the kind of rule that has to be exercised
against fixtures rather than against production. Everything here is PURE - no
network, no clock, no git, no environment beyond argparse defaults - so
`scripts/test_account_zip_selection.py` can drive every branch hermetically.

THE TWO SOURCES OF "THIS SKILL NEEDS RE-UPLOADING", AND WHY BOTH ARE READ
* The LOCAL RECORDING (`account-state.json`, via `sync_skills.py
  --account-drift`) knows what the registry looks like RIGHT NOW, but its
  picture of the account store is only as recent as the last person who ran
  `--record-account-state` from a machine with a mirror.
* The PUBLISHED TIER-3 AUDIT (skills-evals' `propagation/account/latest.json`)
  measured the account store itself, from a signed-in surface a runner does not
  have - but it measured it against the registry commit named in its
  `registry_ref`, which is older than HEAD the moment anything lands.

Neither dominates the other, so `stale` takes the UNION. See `select()`.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# The ONE status under which the published audit contributes names, and the ONE
# place in this repo that spells it. It is the same string skills-evals keys the
# drift tracking issue off - it is what opens or updates "Account skill store
# drifted from registry (automated Tier-3 audit)", and `fresh` is what closes
# it. Writing the predicate down once per repo is what keeps the two from
# drifting apart: this workflow builds ZIPs under precisely the condition that
# files the issue, so the report of the problem and the fix for it appear
# together.
AUDIT_DRIFT_STATUS = "reported-failure"

# The six statuses skills-evals' `account_store.freshness_verdict` can return,
# plus the one this repo adds. `unavailable` is NOT a skills-evals status: it is
# what the caller reports when it could not compute a verdict at all (the clone
# failed, the harness moved, pyyaml was not installed). It exists so "we did not
# ask" can never be mistaken for "we asked and it passed" - a degraded run that
# looks identical to a clean one is the failure this whole file is defending
# against.
KNOWN_STATUSES = frozenset({
    "not-yet-bootstrapped", "missing", "unreadable", "stale",
    AUDIT_DRIFT_STATUS, "fresh",
})
UNAVAILABLE = "unavailable"


def normalise_status(value: str | None) -> str:
    """Any string skills-evals does not define reads as `unavailable`.

    Deliberately permissive on INPUT and strict on MEANING: a future
    skills-evals status this repo has never heard of must degrade to "could not
    use the audit", never to a silent pass that suppresses a real upload.

    THE `.strip()` STAYS NOW THAT THE WORKFLOW ALSO TRIMS, and not as a
    leftover. account-skill-zips.yml normalises its own capture before it
    dispatches on it, so this call no longer carries that step - but
    `--audit-status` is a public entry point with other callers, and dropping
    the strip here would buy agreement with a NEW false negative: `fresh\r`
    would silently degrade to `unavailable` everywhere, and the annotation
    that reported it would still name the wrong cause. Two places trim
    because two places have to answer the question; they agree, which is the
    property test_the_step_and_the_module_agree_on_what_a_verdict_is holds.
    """
    text = (value or "").strip()
    return text if text in KNOWN_STATUSES else UNAVAILABLE


def load_json(path: Path):
    """The parsed JSON, or None. A corrupt artifact is an absent artifact."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def finding_skills(summary) -> list:
    """Every skill named in the artifact's `findings`, for REPORTING only.

    EVERY SHAPE HERE IS CHECKED, NOT ASSUMED, AND THE VALUE TYPE IS THE ONE
    THAT BITES. `latest.json` comes off another repo's `eval-results` branch,
    which docs/decisions/0006 names as untrusted input, and this module's whole
    job is to make a bad artifact DEGRADE rather than take the workflow out.
    Guarding only the finding's shape (`isinstance(f, dict)`) and then trusting
    `f["skill"]` to be a string does the opposite: a `{"skill": 5}` beside a
    string makes `sorted()` compare an int with a str and raise, an unhashable
    value (a list, a dict) blows up the set comprehension before that, and a
    `findings` that is not a list at all is not iterable. Any of those raises
    under the pick step's `set -euo pipefail`, which reds the `pick` job, which
    means the `zip` job never runs and NO ZIPs are produced - a worse outcome
    than the drift the artifact was reporting. So: a list of dicts, and a
    non-empty string name, or the finding is simply not there.
    """
    if not isinstance(summary, dict):
        return []
    findings = summary.get("findings")
    if not isinstance(findings, list):
        return []
    return sorted({f["skill"] for f in findings
                   if isinstance(f, dict)
                   and isinstance(f.get("skill"), str)
                   and f["skill"].strip()})


def audit_drifted(summary, status: str) -> list:
    """The names the audit is allowed to CONTRIBUTE to the selection.

    Empty for every status but `reported-failure`, INCLUDING `fresh`. A pass
    describes the tree at the audit's own `registry_ref`; it says nothing about
    a commit that landed afterwards, so it is evidence of absence only for that
    older tree and is never allowed to remove a name the local recording found.
    """
    return finding_skills(summary) if status == AUDIT_DRIFT_STATUS else []


def select(report: dict, selection: str, summary, status: str) -> dict:
    """What to zip, and everything the summary needs to explain the choice.

    Keys: `names` (feeds the matrix), `why`, `unknown` (asked for but not
    declared), `undeclared` (audit-drifted but not declared), `unzippable`
    (audit-drifted and declared, but gone from the registry), `contributed`
    (came from the audit rather than the recording), `rows` (the report indexed
    by name).

    EVERY NAME THE AUDIT CARRIES LEAVES HERE IN EXACTLY ONE LIST, and every one
    of those lists is rendered. A name the audit reported and this module then
    dropped, with nothing in the summary saying so, is the silent divergence
    the two-sided design exists to prevent: the drift issue in skills-evals
    names a skill, this run builds no artifact for it, and neither surface
    states the disagreement.
    """
    rows = {r["name"]: r for r in report.get("skills") or []}
    sel = (selection or "").strip()
    unknown: list = []
    undeclared: list = []
    unzippable: list = []
    contributed: list = []

    if sel == "all":
        names = [r["name"] for r in report["skills"]
                 if r["status"] != "missing-from-registry"]
        why = "every declared skill (`all` requested)"
    elif sel in ("", "stale"):
        # THE UNION, and deliberately no gate that zeroes it out on a passing
        # audit. Suppressing the local recording whenever the audit says `fresh`
        # would be wrong in the exact case this workflow now runs in most often:
        # the audit passed against `registry_ref`, then a skill changed, and the
        # local recording is the ONLY thing that knows about the newer commit. A
        # push-triggered run is that case by construction - the push IS the
        # commit the audit could not have seen. So a passing audit contributes
        # nothing and removes nothing; `needs.pick.outputs.count != '0'` is what
        # skips the zip job when there is genuinely nothing to build.
        drifted = audit_drifted(summary, status)
        # An audit-drifted name that is not declared is REPORTED, never zipped.
        # Declaring a skill is close to a one-way door - the upload path has
        # no delete (docs/decisions/0002) - so a workflow that reacted to a
        # name it found in an artifact would walk through that door on its own.
        undeclared = [n for n in drifted if n not in rows]
        # DECLARED IS NOT ENOUGH - the row's status has to be zippable too, the
        # same guard the `all` branch applies fifteen lines above and for the
        # same reason: a `missing-from-registry` row has no directory, so the
        # matrix leg would fail on a path that is not there rather than tell
        # anyone anything. The union is where that gets easy to miss, because
        # the name arrives from the OTHER source. Retire a skill from the
        # registry while `account-skills.txt` still lists it and the audit -
        # which measured `registry_ref`, older than HEAD by construction - is
        # still naming a directory that existed in the tree it read and does
        # not exist here.
        #
        # IT IS CARRIED OUT AS ITS OWN LIST RATHER THAN FILTERED AWAY, because
        # the drop is a disagreement with the published audit and has to be
        # stated as one. The bottom-of-summary "Declared but absent from the
        # registry" line is NOT that statement: it fires off the local report
        # whether or not the audit ever named the skill, so it reports one of
        # the two facts and never connects them. render() has a 🚨 for this
        # beside the `undeclared` one.
        unzippable = [n for n in drifted if n in rows
                      and rows[n]["status"] == "missing-from-registry"]
        contributed = [n for n in drifted if n in rows
                       and n not in set(unzippable)]
        names = sorted(set(report["needs_upload"]) | set(contributed))
        why = "changed since the account store was last recorded"
        if contributed:
            why += ", or reported drifted by the published account audit"
    else:
        asked = [n.strip() for n in sel.split(",") if n.strip()]
        names = [n for n in asked if n in rows]
        why = "named on the dispatch"
        unknown = [n for n in asked if n not in rows]

    return {"names": names, "why": why, "unknown": unknown,
            "undeclared": undeclared, "unzippable": unzippable, "rows": rows,
            "contributed": set(contributed)}


def render(report: dict, chosen: dict, summary, status: str, *,
           run_id: str, event: str, latest_path) -> str:
    """The step summary, read ON A PHONE. Wording here is load-bearing."""
    out: list = []

    def say(text: str = "") -> None:
        out.append(text + "\n")

    names = chosen["names"]
    rows = chosen["rows"]
    if chosen["unknown"]:
        say(f"> Not declared in `account-skills.txt`, skipped: "
            f"{', '.join(chosen['unknown'])}\n")
    say("## Account skill ZIPs\n")
    say(f"Recording last taken: **{report['recorded_at'] or 'never'}**\n")
    # Named because it changes how the rest reads: a `schedule` run is the one
    # that fires on the published audit alone, with no commit behind it.
    if event:
        say(f"Started by: **{event}**\n")

    # The published audit is reported BEFORE the decision, so a reader can see
    # what the decision was made from rather than having to infer it.
    say("### The published account audit\n")
    if status == UNAVAILABLE or not isinstance(summary, dict):
        say(f"- Verdict: `{status}` — no usable result at "
            f"`{latest_path}`.")
    else:
        drifted = finding_skills(summary)
        say(f"- Verdict: `{status}`")
        say(f"- Published **{summary.get('generated_at', 'unknown')}** for "
            f"registry ref `{summary.get('registry_ref', 'unknown')}`")
        say(f"- Checked {len(summary.get('checked') or [])} skill(s); "
            f"found drifted: {', '.join(drifted) if drifted else 'none'}")
    say()
    if status not in (AUDIT_DRIFT_STATUS, "fresh"):
        # DEGRADED, and said so in words. The dangerous version of this run is
        # the one that looks exactly like a healthy one while the cross-repo
        # half of the evidence was never read.
        say(f"> ⚠️ **Degraded: the published audit could not be used "
            f"(`{status}`).** The local recording alone drove this run, so a "
            f"skill that drifted on the account store WITHOUT the registry "
            f"moving would not be offered here. Check the Tier-3 Routine and "
            f"the `eval-results` branch of `Adam-S-Daniel/skills-evals`.\n")
    elif status == "fresh":
        say("> The audit passed, which contributes no names and removes none: "
            "it describes the registry at its own `registry_ref`, and the "
            "recording below is the only thing that knows about anything "
            "committed since.\n")
    if chosen["undeclared"]:
        say(f"> 🚨 **Reported drifted by the audit but NOT declared in "
            f"`account-skills.txt`: {', '.join(chosen['undeclared'])}.** Not "
            f"zipped, deliberately — declaring a skill for the account "
            f"store is close to a one-way door (the upload path has no delete, "
            f"docs/decisions/0002), so this workflow will not declare one on "
            f"its own. Add it by hand if it belongs there.\n")
    if chosen["unzippable"]:
        # THE OTHER HALF OF THE SAME OBLIGATION. `undeclared` has always said
        # "the audit named it and this run did not build it"; the
        # registry-absent drop was made just as deliberately and said nothing,
        # which leaves a reader holding a drift issue that names a skill and a
        # run page with no artifact for it, and no sentence anywhere admitting
        # the two disagree. The "Declared but absent from the registry" line at
        # the bottom is NOT that sentence: it fires off the local report
        # whether or not the audit ever named the skill, so it states one of
        # the facts and never connects them. Naming the remedy matters as much
        # as naming the skill - neither repo can repair this on its own,
        # because both ends of the contradiction are committed files a human
        # owns.
        say(f"> 🚨 **Reported drifted by the audit, but absent from the "
            f"registry at this commit: {', '.join(chosen['unzippable'])}.** "
            f"Not zipped — there is no `plugins/*/skills/<name>/` here to "
            f"build a ZIP from, so the build would fail on a path that is not "
            f"there rather than tell you anything. The audit measured its own "
            f"`registry_ref`, which is older than HEAD, so it read a tree "
            f"where that directory still existed. The drift issue in "
            f"`Adam-S-Daniel/skills-evals` will go on naming these while no "
            f"artifact appears for them — close the gap at the declaration: "
            f"drop the name from `account-skills.txt` if the skill is "
            f"retired, or restore the skill if it is not.\n")

    if not names:
        say("Nothing to upload - every declared skill matches the "
            "recording.\n")
    else:
        say(f"{len(names)} skill(s) {chosen['why']}:\n")
        for n in names:
            st = rows[n]["status"]
            # A name the audit contributed is normally `in-sync` locally, which
            # on its own reads like a bug. Say where it came from.
            tag = " — reported drifted by the published audit" \
                if n in chosen["contributed"] else ""
            say(f"- **{n}** — `{st}`{tag}")
        say("\n### From your phone\n")
        say("1. Scroll to **Artifacts** at the bottom of this run page.")
        say("2. Tap a skill's artifact — it downloads as `<name>.zip`.")
        say("3. In Safari open claude.ai → Settings → Capabilities → "
            "upload that `.zip` as-is. No unzipping.")
        say("4. It replaces the account copy of the same name.\n")
        # Two ways to close the loop, PHONE FIRST - this summary is read on a
        # phone, and the mirror route is the one a phone cannot take. Naming
        # only the mirror told the reader to do the single thing their device
        # cannot.
        say("\n### Then close the loop, or these keep being offered\n")
        # Workflow name kept contiguous on one line so a grep for it finds
        # this pointer.
        say("**From the phone** — dispatch **Record an account upload**"
            f" with the skill name and this run ID "
            f"(`{run_id}`). It records "
            "the upload and pushes a branch for you to open as a PR. "
            "It writes `basis: asserted` — your word that the upload "
            "landed, which nothing can verify.\n")
        say("**From a machine with the account mirror** — the stronger "
            "one, because it measures instead of asserting:\n")
        say("```")
        say("CLAUDE_CODE_SYNC_SKILLS=1 claude -p 'ok'")
        say("python3 plugins/adam-local/skills/sync-skills/"
            "sync_skills.py --record-account-state")
        say("```")
        say("\nEither way, commit the result. An observation "
            "overwrites an assertion, never the reverse.\n")
    # An `in-sync` that rests on --assert-uploaded was never checked against
    # the account. Not offering it is right; saying nothing about it is what
    # made a claim indistinguishable from a measurement.
    if report.get("in_sync_asserted"):
        say(f"\n**Not offered, but never verified:** "
            f"{', '.join(report['in_sync_asserted'])} — recorded by "
            f"`--assert-uploaded`, i.e. on the operator's word. Run "
            f"`--record-account-state` from a session with a mirror to "
            f"replace the claim with a measurement.\n")
    if report["missing_from_registry"]:
        say(f"\n⚠️ Declared but absent from the registry: "
            f"{', '.join(report['missing_from_registry'])}\n")
    return "".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--drift-report", required=True, type=Path,
                        help="JSON written by `sync_skills.py --account-drift`")
    parser.add_argument("--selection", default="stale",
                        help="stale (default) | all | comma-separated names")
    parser.add_argument("--audit-latest", type=Path,
                        help="skills-evals propagation/account/latest.json; "
                             "a path that does not exist is tolerated")
    parser.add_argument("--audit-status", default="",
                        help="freshness_verdict status, or `unavailable`")
    parser.add_argument("--event", default="", help="GitHub event name")
    # Defaulted from the environment as well as accepted as a flag, so the run
    # ID reaches the summary even when a caller forgets to pass it - having to
    # go and find it is the friction the dispatch pointer exists to remove.
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    parser.add_argument("--github-output", type=Path,
                        default=os.environ.get("GITHUB_OUTPUT") or None)
    parser.add_argument("--summary", type=Path,
                        default=os.environ.get("GITHUB_STEP_SUMMARY") or None)
    args = parser.parse_args(argv)

    report = json.loads(args.drift_report.read_text(encoding="utf-8"))
    status = normalise_status(args.audit_status)
    summary = load_json(args.audit_latest) if args.audit_latest else None
    if summary is None and status in (AUDIT_DRIFT_STATUS, "fresh"):
        # The caller computed a verdict FROM an artifact this process cannot
        # read - different file, truncated write, a clone that vanished. Trust
        # what is on disk here rather than the label, and degrade loudly.
        status = UNAVAILABLE

    chosen = select(report, args.selection, summary, status)
    text = render(report, chosen, summary, status, run_id=args.run_id,
                  event=args.event,
                  latest_path=args.audit_latest or "(no path given)")

    lines = (f"names={json.dumps(chosen['names'])}\n"
             f"count={len(chosen['names'])}\n")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(lines)
    else:
        print(lines, end="")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
