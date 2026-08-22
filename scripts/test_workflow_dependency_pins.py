#!/usr/bin/env python3
"""Every `pip install` in this repo's workflows installs requirements-dev.txt.

Issue #121. The four Python packages this repo tests itself with used to be
pinned inline in five `pip install` lines across .github/workflows/, and the
five named different subsets of them. Nothing required any two to agree, and
nothing outside Actions could read the set at all. requirements-dev.txt is now
the one place a version of any of them is written; these tests are what makes
that claim hold rather than describe the day it was made.

The rule they enforce is deliberately shaped so it needs no list of its own:

  * every `pip install` a workflow runs must install `-r <requirements file>`,
    and that file must be the declared one;
  * it may name no package of its own, pinned or not — a name on the command
    line is a second place a version can live, or a fifth dependency nobody
    declared;
  * so a package added to requirements-dev.txt is covered the moment it is
    added, and nothing here has to be widened to notice it.

FAIL-CLOSED IS THE POINT. A `pip install` these tests cannot parse, or one
that the YAML walk never reached, is a FAILURE naming the file and line — not
a silent pass. The whole failure mode being guarded is a pin drifting back
into a workflow unnoticed, and a checker that shrugs at a shape it does not
recognise would be the exact hole it was written to close.

Parsed with `yaml`, not grepped: a `run:` body is a folded or literal scalar
whose indentation and continuations a line scan does not see, and this repo's
workflows are mostly comments — several of which now contain the words `pip
install` inside prose about pip installs, so a line scan would report those
and a commented-out install as real commands. The last test below is the
second pair of eyes on the walk: it re-reads the SAME parsed document as a
tree of scalars, with no notion of what a job or a step is, and fails when a
pip install turns up in a scalar the `jobs -> steps -> run` walk never
visited.

Scope is .github/workflows/ only. Skills SHIP workflow YAML as assets
(plugins/adam/skills/github-actions-repo-settings/assets/workflows/) that runs
in other people's repositories; their dependencies are not ours to pin, and
sweeping them in here would fail this repo's CI over a template.

Run: python3 -m pytest scripts/test_workflow_dependency_pins.py -q
"""

import re
import shlex
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
REQUIREMENTS = REPO_ROOT / "requirements-dev.txt"

# `python3 -m pip install`, `python -m pip install`, `pip install`,
# `pip3 install` — at the start of a command, which after continuations are
# joined means after a newline, a `;`, a `&&`, a `||`, a `|` or an opening
# paren. Anchoring it that way is what keeps `pip download` and the phrase
# "pip install" inside a comment out of the match.
PIP_INSTALL = re.compile(
    r"(?:^|(?<=[\n;&|(]))\s*"
    r"(?:(?:python|python3)(?:\s+-\S+)*\s+-m\s+)?"
    r"pip3?\s+install\b"
)
# Where one of those commands ends. Sufficient for a `run:` body and not for
# shell in general — a `;` or `&&` inside a quoted pip argument would end the
# command early. That errs toward a parse the tests reject rather than one
# they wave through, which is the direction this file is willing to be wrong
# in.
COMMAND_END = re.compile(r"\n|;|&&|\|\||\|")

# pip options that consume the following token. Not the whole of pip's CLI —
# only enough that a value never gets mistaken for a package name. An option
# missing from here whose value happens not to start with `-` is read as a
# package operand and FAILS the test, which is the safe direction: the fix is
# to add the option here, and the alternative would be to skip unknown tokens
# and let a real package name through with them.
VALUE_OPTIONS = {
    "-r", "--requirement", "-c", "--constraint", "-t", "--target",
    "-e", "--editable", "-i", "--index-url", "--extra-index-url",
    "-f", "--find-links", "--python-version", "--platform", "--abi",
    "--implementation", "--prefix", "--root", "--src", "--upgrade-strategy",
    "--no-binary", "--only-binary", "--progress-bar", "--cache-dir", "--log",
    "--proxy", "--retries", "--timeout", "--exists-action", "--trusted-host",
    "--client-cert", "--cert", "--report", "--config-settings", "-C",
}
REQUIREMENT_OPTIONS = {"-r", "--requirement"}


def canonical(name: str) -> str:
    """PEP 503 normalisation, so `PyYAML`, `pyyaml` and `py_yaml` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_requirements() -> dict:
    """{canonical name: version} read out of requirements-dev.txt.

    Nothing in this file hardcodes what that set contains. A package added to
    the requirements file is part of the set these tests enforce as soon as it
    lands there.
    """
    declared = {}
    for lineno, raw in enumerate(
            REQUIREMENTS.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        assert "==" in line, (
            f"requirements-dev.txt:{lineno}: `{line}` is not an exact pin. "
            f"AGENTS.md requires `name==version`; a range or a bare name lets "
            f"CI resolve to whatever is newest at job time."
        )
        name, _, version = line.partition("==")
        key = canonical(name.strip())
        assert key not in declared, (
            f"requirements-dev.txt:{lineno}: `{name.strip()}` is pinned twice, "
            f"so the file no longer states one version for it."
        )
        assert version.strip(), f"requirements-dev.txt:{lineno}: empty version."
        declared[key] = version.strip()
    assert declared, "requirements-dev.txt declares nothing."
    return declared


def workflow_files():
    return sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))


def join_continuations(body: str) -> str:
    """Fold `\\`-continued shell lines into one, so a command split across
    lines is one command to the scanner. account-skill-zips.yml's install is
    written that way."""
    return re.sub(r"\\\n[ \t]*", " ", body)


def pip_install_commands(body: str):
    """Yield every pip install command in one shell body, as raw text."""
    joined = join_continuations(body)
    for match in PIP_INSTALL.finditer(joined):
        tail = joined[match.start():]
        # Skip the leading whitespace the pattern absorbed before measuring
        # the end of the command.
        offset = len(tail) - len(tail.lstrip())
        end = COMMAND_END.search(tail, match.end() - match.start())
        yield tail[offset:end.start() if end else len(tail)].strip()


def parsed_run_steps():
    """(workflow, job id, step index, step name, run body) for every step."""
    for path in workflow_files():
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in (doc.get("jobs") or {}).items():
            for index, step in enumerate(job.get("steps") or []):
                run = step.get("run")
                if isinstance(run, str):
                    yield path, job_id, index, step.get("name"), run


def installs():
    """Every pip install command reached through the parsed YAML walk."""
    for path, job_id, index, name, body in parsed_run_steps():
        for command in pip_install_commands(body):
            where = (
                f"{path.relative_to(REPO_ROOT).as_posix()} job `{job_id}` "
                f"step {index}" + (f" ({name})" if name else "")
            )
            yield where, command


def install_operands(command: str):
    """(requirement files, package operands) for one pip install command."""
    tokens = shlex.split(command, comments=True)
    tokens = tokens[tokens.index("install") + 1:]
    requirement_files, packages = [], []
    iterator = iter(range(len(tokens)))
    skip_next = False
    for i in iterator:
        if skip_next:
            skip_next = False
            continue
        token = tokens[i]
        if token in REQUIREMENT_OPTIONS:
            if i + 1 < len(tokens):
                requirement_files.append(tokens[i + 1])
            skip_next = True
        elif token.startswith("--requirement="):
            requirement_files.append(token.split("=", 1)[1])
        elif token.startswith("-r") and len(token) > 2:
            requirement_files.append(token[2:])
        elif token in VALUE_OPTIONS:
            skip_next = True
        elif token.startswith("-"):
            continue
        else:
            packages.append(token)
    return requirement_files, packages


ALL_INSTALLS = list(installs())


def test_the_workflows_install_python_dependencies_at_all():
    """Guards the vacuous pass. Every assertion below is over the installs
    found; delete them all and the rest of this file goes quiet."""
    assert ALL_INSTALLS, (
        "no `pip install` was found in any workflow under .github/workflows/. "
        "Either the jobs stopped installing their Python dependencies, or the "
        "scanner in this file no longer recognises how they do it — and the "
        "rest of these tests pass either way, which is why this one exists."
    )


@pytest.mark.parametrize("where, command", ALL_INSTALLS,
                         ids=[w for w, _ in ALL_INSTALLS])
def test_a_workflow_install_reads_the_declared_requirements_file(where, command):
    files, _ = install_operands(command)
    assert files, (
        f"{where} runs `{command}` without `-r`. Every workflow install has "
        f"to read requirements-dev.txt so the version it gets is the declared "
        f"one; installing anything by name here is a second place a pin can "
        f"live and drift."
    )
    expected = REQUIREMENTS.relative_to(REPO_ROOT).as_posix()
    for path in files:
        resolved = (REPO_ROOT / path).resolve()
        assert resolved == REQUIREMENTS, (
            f"{where} installs `-r {path}`, which is not the declared "
            f"dependency set at {expected}. A second requirements file is a "
            f"second place a version is written down."
        )


@pytest.mark.parametrize("where, command", ALL_INSTALLS,
                         ids=[w for w, _ in ALL_INSTALLS])
def test_a_workflow_install_names_no_package_of_its_own(where, command):
    """The drift check proper, and the one that reads the declared set.

    A package named on a workflow's command line is measured against
    requirements-dev.txt rather than against a list kept here, so this fails
    the same way whether the workflow disagrees with the file, duplicates it,
    or installs something the file has never heard of.
    """
    _, packages = install_operands(command)
    if not packages:
        return
    declared = declared_requirements()
    detail = []
    for spec in packages:
        name, sep, version = spec.partition("==")
        key = canonical(name.strip())
        if key not in declared:
            detail.append(
                f"`{spec}` is not in requirements-dev.txt at all — a "
                f"dependency this workflow needs that the declared set does "
                f"not mention")
        elif not sep:
            detail.append(
                f"`{spec}` is unpinned here while requirements-dev.txt pins "
                f"it to {declared[key]}, so this job can resolve to a "
                f"different version than the rest of CI")
        elif version.strip() != declared[key]:
            detail.append(
                f"`{spec}` DISAGREES with requirements-dev.txt, which pins "
                f"{name.strip()}=={declared[key]}")
        else:
            detail.append(
                f"`{spec}` restates the pin in requirements-dev.txt — it "
                f"agrees today, and it is the copy that drifts tomorrow")
    raise AssertionError(
        f"{where} names packages on its own command line:\n  " + command
        + "\n" + "\n".join("  - " + d for d in detail)
        + "\nInstall them by adding them to requirements-dev.txt and using "
          "`-r requirements-dev.txt` alone."
    )


def scalars(node, path=""):
    """Every string scalar in a parsed document, with its key path.

    Knows nothing about jobs or steps on purpose — that is what makes it an
    independent reading of the same file.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            yield from scalars(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from scalars(value, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path or ".", node


def step_run_paths(doc):
    return {
        f".jobs.{job_id}.steps[{index}].run"
        for job_id, job in (doc.get("jobs") or {}).items()
        for index, step in enumerate(job.get("steps") or [])
        if isinstance(step.get("run"), str)
    }


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_no_pip_install_hides_where_the_parsed_walk_cannot_see_it(path):
    """The checks above read `jobs -> steps -> run`. A pip install anywhere
    else in the document — a `defaults:` block, an `env:` default, a `with:`
    argument to an action, a job key a future Actions feature adds — would be
    governed by nothing and pass in silence. This walks the whole tree instead
    and fails on any pip install outside a step body, naming the key path."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    known = step_run_paths(doc)
    stray = sorted(
        key for key, text in scalars(doc)
        if key not in known and any(pip_install_commands(text))
    )
    assert not stray, (
        f"{path.relative_to(REPO_ROOT).as_posix()} runs `pip install` from "
        f"{len(stray)} place(s) the step walk in this file never reaches, so "
        f"none of the checks above apply to them: {stray}. Either move the "
        f"install into a step's `run:`, or teach installs() to reach it — do "
        f"not leave it ungoverned."
    )


def test_every_declared_requirement_is_pinned_exactly():
    """declared_requirements() asserts the shape of every line as it reads
    them; this is the test that makes those assertions run even on a day when
    no workflow names a package and the drift test returns early."""
    declared = declared_requirements()
    for name, version in declared.items():
        assert re.fullmatch(r"[0-9][0-9A-Za-z.+!-]*", version), (
            f"requirements-dev.txt pins {name} to `{version}`, which is not a "
            f"plain version. An environment marker, extra or URL here would "
            f"make what CI installs depend on the runner."
        )
