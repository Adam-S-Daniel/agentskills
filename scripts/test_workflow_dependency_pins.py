#!/usr/bin/env python3
"""Every `pip install` in this repo's workflows installs requirements-dev.txt.

Issue #121. The packages this repo tests itself with used to be pinned inline
in the `pip install` lines of .github/workflows/. Nothing required any two of
those lines to agree, and nothing outside Actions could read the set at all.
requirements-dev.txt is now the one place a version of any of them is written;
these tests are what makes that claim hold rather than describe the day it was
made.

The rule they enforce is deliberately shaped so it needs no list of its own:

  * every `pip install` a workflow runs must install `-r <requirements file>`,
    and that file must be the declared one;
  * it may name no package of its own, pinned or not — a name on the command
    line is a second place a version can live, or a dependency nobody
    declared;
  * it may pass only options that cannot change which version pip resolves,
    and the table of those is a whitelist, so an option this file has never
    heard of fails rather than being skipped;
  * so a package added to requirements-dev.txt is covered the moment it is
    added, and nothing here has to be widened to notice it.

That cover runs one way only. A name the file declares that nothing in the
repo needs is not flagged by anything here; deciding a package is no longer
used takes reading the scripts, which these tests do not do.

FAIL-CLOSED IS THE POINT. A command these tests cannot place, or a pip install
that the YAML walk never reached, is a FAILURE naming the file, the job and the
step it sits in — not a silent pass. The whole failure mode being guarded is a
pin drifting back into a workflow unnoticed, and a checker that shrugs at a
shape it does not recognise would be the exact hole it was written to close.

A PIP INSTALL IS RECOGNISED BY TOKENS, NEVER BY A PREFIX. An earlier version of
this file matched a regex anchored to the start of a command, so it needed the
command to *begin* with `pip` or `python -m pip`. Any token in front of that
hid the install completely — `sudo python3 -m pip install`, `env FOO=1 pip
install`, `uv pip install`, `$PY -m pip install` — and `python3 -m pip -q
install` hid it by putting a global option between `pip` and `install`. A
recogniser that decides by matching a prefix loses to a longer prefix, every
time. So each `run:` body is tokenised as shell and split into commands, and a
command counts as a pip install whenever a token naming pip (`pip`, `pip3`,
`pip3.11`, a path ending in one) is followed by the subcommand `install` —
however many tokens sit in front of it and whatever flags sit between.

Parsed with `yaml`, not grepped: a `run:` body is a folded or literal scalar
whose indentation and continuations a line scan does not see, and this repo's
workflows are mostly comments — several of which now contain the words `pip
install` inside prose about pip installs, so a line scan would report those and
a commented-out install as real commands. Tokenising drops a `#` comment and
keeps a quoted `"pip install ..."` as one token, so neither is mistaken for a
command. The stray-scalar test below is the second pair of eyes on the walk: it
re-reads the SAME parsed document as a tree of scalars, with no notion of what
a job or a step is, and fails when a pip install turns up in a scalar the
`jobs -> steps -> run` walk never visited.

Scope is the YAML this repo runs its own CI from: .github/workflows/*.yml and
the composite actions at .github/actions/**/action.yml. A composite action's
`runs.steps[].run` is shell this repo's jobs execute exactly as a workflow step
is, so leaving it out would have left a place a pin could drift back into with
nothing looking. Skills SHIP workflow YAML as assets
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
REQUIREMENTS = REPO_ROOT / "requirements-dev.txt"

# Characters shlex hands back as their own tokens rather than folding into a
# word. The shell's own command separators, plus `\n` — which shlex counts as
# whitespace by default, and which has to stop a command here because a `run:`
# body is many commands one per line.
PUNCTUATION = "();<>|&\n"
# `{` and `}` are not shell operators, but `{ cmd; }` puts one against a
# command with no space, and neither ever starts a command.
BRACES = {"{", "}"}

# Cheap pre-filter. Tokenising is only attempted on text that could hold a pip
# install at all, which keeps shlex away from the many scalars in these files
# that are English prose and would raise on an apostrophe.
PIP_HINT = re.compile(r"pip|install", re.IGNORECASE)

# A token that names the pip program: `pip`, `pip3`, `pip3.11`, and the same
# reached by path. Deliberately a full match — `pipefail` and `pip_ok=no` both
# appear in this repo's workflows and neither is pip.
PIP_PROGRAM = re.compile(r"(?:[^\s]*/)?pip[0-9]*(?:\.[0-9]+)*")

# pip subcommands that install nothing, so a pin cannot drift in through one.
# A subcommand outside this set and not `install` is not waved through: it is
# reported as unplaceable, because the safe reading of a pip invocation this
# file does not understand is that it might install something.
PIP_SUBCOMMANDS_THAT_INSTALL_NOTHING = frozenset({
    "cache", "check", "completion", "config", "debug", "download", "freeze",
    "hash", "help", "index", "inspect", "list", "lock", "search", "show",
    "uninstall", "wheel",
})

# `NAME=value` in front of a command is an environment assignment, not the
# program being run.
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# A program name this file can read at test time. Anything holding a `$`, a
# backtick or a glob resolves at job time to something unknown, so a command
# running `install` under one of those cannot be placed.
RESOLVABLE_PROGRAM = re.compile(r"[A-Za-z0-9_@.+:/=-]+")

# THE OPTION TABLE IS A WHITELIST, and the entry test for it is one question:
# can this option change WHICH VERSION pip ends up installing, or where it
# comes from? If it can, it does not belong here. An earlier version of this
# table listed options so their values would not be mistaken for package
# names, which meant `-c constraints.txt` was consumed and waved through —
# and a constraints file beats the requirements file, so `pyyaml==6.0.1` in
# constraints.txt overrides `pyyaml==6.0.3` in requirements-dev.txt. Measured
# on this branch at 2330e9c: `pip install -r requirements-dev.txt -c
# constraints.txt` and `--index-url https://evil.example.com/simple` both
# passed green. A whitelist fails an option it has never heard of, so a future
# pip option that redirects a pin has to be looked at by a person before it
# can be used here.
#
# Options with no value, none of which change what pip resolves — they change
# how loud it is, where it puts the result, or whether it reads config.
VERSION_NEUTRAL_FLAGS = frozenset({
    "-q", "-qq", "-qqq", "--quiet",
    "-v", "-vv", "-vvv", "--verbose",
    "--no-color", "--no-input", "--no-cache-dir", "--isolated",
    "--disable-pip-version-check", "--no-python-version-warning",
    "--no-warn-script-location", "--no-warn-conflicts",
    "--require-virtualenv", "--user", "--break-system-packages",
    "--force-reinstall",
})
# Options that consume the following token, and whose value is a path, a
# number or a display setting rather than anything pip resolves against.
VERSION_NEUTRAL_VALUE_OPTIONS = frozenset({
    "--progress-bar", "--cache-dir", "--log", "--retries", "--timeout",
})
# `-r` is the point of the whole file, so it is read rather than skipped: the
# file it names is checked against the declared one.
REQUIREMENT_OPTIONS = frozenset({"-r", "--requirement"})


def canonical(name: str) -> str:
    """PEP 503 normalisation, so `PyYAML`, `pyyaml` and `py_yaml` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


# A bare distribution name (PEP 508), and a version with nothing trailing it.
# Both halves are checked: `pytest-cov[toml]==7.0.0` used to be read as the
# name `pytest-cov[toml]` pinned to `7.0.0` and passed, even though an extra
# pulls in requirements this file does not name.
REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
REQUIREMENT_VERSION = re.compile(r"[0-9][0-9A-Za-z.+!-]*")


def parse_requirements(text: str, source: str = "requirements-dev.txt") -> dict:
    """{canonical name: version}, asserting the shape of every line it reads.

    Takes the text rather than reading the file so the shapes it rejects can
    be tested without writing into requirements-dev.txt.
    """
    declared = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        assert "==" in line, (
            f"{source}:{lineno}: `{line}` is not an exact pin. "
            f"AGENTS.md requires `name==version`; a range or a bare name lets "
            f"CI resolve to whatever is newest at job time."
        )
        name, _, version = line.partition("==")
        name, version = name.strip(), version.strip()
        assert REQUIREMENT_NAME.fullmatch(name), (
            f"{source}:{lineno}: `{name}` is not a bare distribution name. An "
            f"extra (`pytest-cov[toml]`) installs requirements this file does "
            f"not name, so the set CI gets stops being the set written here."
        )
        key = canonical(name)
        assert key not in declared, (
            f"{source}:{lineno}: `{name}` is pinned twice, "
            f"so the file no longer states one version for it."
        )
        assert version, f"{source}:{lineno}: empty version."
        assert REQUIREMENT_VERSION.fullmatch(version), (
            f"{source}:{lineno}: `{name}` is pinned to `{version}`, which is "
            f"not a plain version. An environment marker or anything else "
            f"trailing the version makes what CI installs depend on the "
            f"runner rather than on this line."
        )
        declared[key] = version
    assert declared, f"{source} declares nothing."
    return declared


def declared_requirements() -> dict:
    """{canonical name: version} read out of requirements-dev.txt.

    Nothing in this file hardcodes what that set contains. A package added to
    the requirements file is part of the set these tests enforce as soon as it
    lands there.
    """
    return parse_requirements(REQUIREMENTS.read_text(encoding="utf-8"))


def governed_files(root=REPO_ROOT):
    """Every YAML file under .github/ whose `run:` bodies this repo executes.

    `root` is a parameter so the walk can be pointed at a fixture tree; the
    tests that check what it reaches would otherwise have to write into
    .github/ to say anything.
    """
    base = Path(root) / ".github"
    return sorted(
        set(base.glob("workflows/*.yml"))
        | set(base.glob("workflows/*.yaml"))
        | set(base.glob("actions/**/action.yml"))
        | set(base.glob("actions/**/action.yaml"))
    )


def join_continuations(body: str) -> str:
    """Fold `\\`-continued shell lines into one, so a command split across
    lines is one command to the scanner. account-skill-zips.yml's install is
    written that way."""
    return re.sub(r"\\\n[ \t]*", " ", body)


def shell_tokens(body: str):
    """The shell tokens of one `run:` body, separators included.

    Quoting, escaping and `#` comments are shlex's job, which is the point:
    `echo "pip install x"` comes back as two tokens and the second is not
    `pip`, and a comment is gone before anything looks at it. Raises
    ValueError on text shlex cannot tokenise, e.g. an unbalanced quote.
    """
    lexer = shlex.shlex(join_continuations(body), posix=True,
                        punctuation_chars=PUNCTUATION)
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"  # `\n` is a separator here, not whitespace
    lexer.commenters = "#"
    return list(lexer)


def is_separator(token: str) -> bool:
    return token in BRACES or all(char in PUNCTUATION for char in token)


def shell_commands(tokens):
    """Split a token stream into one list of tokens per command."""
    command = []
    for token in tokens:
        if is_separator(token):
            if command:
                yield command
            command = []
        else:
            command.append(token)
    if command:
        yield command


def command_program(tokens):
    """The program a command runs, past any `NAME=value` prefix."""
    for token in tokens:
        if ASSIGNMENT.match(token) or token.startswith("-"):
            continue
        return token
    return None


def classify_command(tokens):
    """('install', args) | ('other', None) | ('unplaceable', reason).

    `args` is the tokens after the `install` subcommand. Whatever sits in
    front of pip is ignored on purpose: a wrapper is how the anchored
    recogniser this replaced was defeated.
    """
    for index, token in enumerate(tokens):
        if not PIP_PROGRAM.fullmatch(token):
            continue
        rest = tokens[index + 1:]
        after_options = 0
        while (after_options < len(rest)
               and rest[after_options].startswith("-")):
            after_options += 1
        subcommand = rest[after_options] if after_options < len(rest) else None
        if subcommand == "install":
            return "install", rest[after_options + 1:]
        if subcommand in PIP_SUBCOMMANDS_THAT_INSTALL_NOTHING:
            return "other", None
        return "unplaceable", (
            f"`{token}` is pip, but the token after its flags is "
            f"{subcommand!r}, which is not a pip subcommand this file knows. "
            f"A pip command it cannot place might be an install, so it fails "
            f"rather than passing in silence. Note that a pip global option "
            f"taking a SEPARATE value (`pip --log f install ...`) lands here "
            f"too: only flags are skipped when looking for the subcommand."
        )
    if "install" in tokens:
        program = command_program(tokens)
        if program is None or not RESOLVABLE_PROGRAM.fullmatch(program):
            return "unplaceable", (
                f"runs `install` under {program!r}, which does not resolve to "
                f"a program name until job time, so this file cannot tell "
                f"whether it is pip."
            )
    return "other", None


def scan_shell_body(body: str):
    """(pip install commands, unplaceable commands) for one shell body.

    Each entry is (command text, ...) where the text is the command's tokens
    re-joined by shlex — a faithful, runnable rendering of what was parsed
    rather than a slice of the original, so a failure message shows what the
    checker actually saw.
    """
    if not PIP_HINT.search(body):
        return [], []
    try:
        tokens = shell_tokens(body)
    except ValueError as exc:
        return [], [(body.strip(), f"could not be tokenised as shell: {exc}")]
    found, unplaceable = [], []
    for command in shell_commands(tokens):
        kind, payload = classify_command(command)
        text = shlex.join(command)
        if kind == "install":
            found.append((text, payload))
        elif kind == "unplaceable":
            unplaceable.append((text, payload))
    return found, unplaceable


def step_containers(doc):
    """(label, key path prefix, steps) for each place a document holds steps.

    A workflow keeps them under `jobs.<id>.steps`; a composite action keeps
    them under `runs.steps`. Both are shell this repo runs.
    """
    for job_id, job in (doc.get("jobs") or {}).items():
        if isinstance(job, dict):
            yield f"job `{job_id}`", f".jobs.{job_id}.steps", job.get("steps")
    runs = doc.get("runs")
    if isinstance(runs, dict):
        yield "composite action", ".runs.steps", runs.get("steps")


def parsed_run_steps(root=REPO_ROOT):
    """(path, container label, step index, step name, run body) per step."""
    for path in governed_files(root):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            continue
        for label, _, steps in step_containers(doc):
            for index, step in enumerate(steps or []):
                if not isinstance(step, dict):
                    continue
                run = step.get("run")
                if isinstance(run, str):
                    yield path, label, index, step.get("name"), run


def scan_workflows(root=REPO_ROOT):
    """(installs, unplaceable) over every governed step body under .github/."""
    found, unplaceable = [], []
    for path, label, index, name, body in parsed_run_steps(root):
        where = (
            f"{path.relative_to(root).as_posix()} {label} "
            f"step {index}" + (f" ({name})" if name else "")
        )
        commands, strays = scan_shell_body(body)
        found.extend((where, text, args) for text, args in commands)
        unplaceable.extend((where, text, why) for text, why in strays)
    return found, unplaceable


def install_operands(args):
    """(requirement files, package operands, rejected options) for one install.

    A rejected option is one that is not on the whitelist above, whether pip
    has it today or grows it tomorrow. Parsing does not try to guess whether
    such an option takes a value, so the token after it may land in the
    package list — which is why the package check defers to the option check
    on a command that has any.
    """
    requirement_files, packages, rejected = [], [], []
    skip_next = False
    for i, token in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        name, equals, value = token.partition("=")
        if token.startswith("-r") and not token.startswith("--") and len(token) > 2:
            requirement_files.append(token[2:])
        elif name in REQUIREMENT_OPTIONS:
            if equals:
                requirement_files.append(value)
            elif i + 1 < len(args):
                requirement_files.append(args[i + 1])
                skip_next = True
            else:
                rejected.append(f"{token} (with no file after it)")
        elif name in VERSION_NEUTRAL_VALUE_OPTIONS:
            skip_next = not equals
        elif token in VERSION_NEUTRAL_FLAGS:
            continue
        elif token.startswith("-"):
            rejected.append(token)
        else:
            packages.append(token)
    return requirement_files, packages, rejected


ALL_INSTALLS, UNPLACEABLE = scan_workflows()
INSTALL_CASES = [(where, text, args) for where, text, args in ALL_INSTALLS]
INSTALL_IDS = [where for where, _, _ in ALL_INSTALLS]


def test_the_workflows_install_python_dependencies_at_all():
    """Guards the vacuous pass for the checks that are parametrized over the
    installs found: with none found, pytest generates no cases for them and
    they cannot fail."""
    assert ALL_INSTALLS, (
        "no `pip install` was found in any workflow under .github/workflows/. "
        "Either the jobs stopped installing their Python dependencies, or the "
        "scanner in this file no longer recognises how they do it — and the "
        "checks parametrized over the installs pass either way, which is why "
        "this one exists."
    )


def test_no_workflow_command_that_might_be_a_pip_install_is_left_unplaced():
    """The fail-closed half of the recogniser.

    A command holding a pip token whose subcommand this file cannot name, or
    running `install` under a program that only resolves at job time, is
    reported here rather than skipped. Skipping it is how a drifted pin gets
    through: the checks below only see what the recogniser hands them.
    """
    assert not UNPLACEABLE, "\n".join(
        f"{where} runs a command this file cannot place:\n  {text}\n  {why}"
        for where, text, why in UNPLACEABLE
    )


@pytest.mark.parametrize("where, command, args", INSTALL_CASES, ids=INSTALL_IDS)
def test_a_workflow_install_reads_the_declared_requirements_file(
        where, command, args):
    files, _, _ = install_operands(args)
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


@pytest.mark.parametrize("where, command, args", INSTALL_CASES, ids=INSTALL_IDS)
def test_a_workflow_install_uses_no_option_that_can_change_the_version(
        where, command, args):
    """`-r requirements-dev.txt` only states the pins; several pip options
    override them. A constraints file wins over a requirements file, and an
    index option moves where every pin resolves from, so an install carrying
    one of those reads the declared set and installs something else."""
    _, _, rejected = install_operands(args)
    assert not rejected, (
        f"{where} runs `{command}`, which passes {rejected} — not on the "
        f"whitelist of options that cannot change which version pip installs. "
        f"A constraints file overrides the requirements pin and an index "
        f"option moves where it resolves from, so an option here can undo "
        f"requirements-dev.txt without changing a line of it. If the option "
        f"genuinely cannot change the resolved version, add it to "
        f"VERSION_NEUTRAL_FLAGS or VERSION_NEUTRAL_VALUE_OPTIONS and say why."
    )


@pytest.mark.parametrize("where, command, args", INSTALL_CASES, ids=INSTALL_IDS)
def test_a_workflow_install_names_no_package_of_its_own(where, command, args):
    """The drift check proper, and the one that reads the declared set.

    A package named on a workflow's command line is measured against
    requirements-dev.txt rather than against a list kept here, so this fails
    the same way whether the workflow disagrees with the file, duplicates it,
    or installs something the file has never heard of.
    """
    _, packages, rejected = install_operands(args)
    if not packages or rejected:
        # An option the whitelist rejects may swallow the token after it, so a
        # bare token here can be that value rather than a package. The options
        # check is the one that reports such a command.
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
        f"{prefix}[{index}].run"
        for _, prefix, steps in step_containers(doc)
        for index, step in enumerate(steps or [])
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    }


@pytest.mark.parametrize("path", governed_files(),
                         ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
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
        if key not in known and any(scan_shell_body(text))
    )
    assert not stray, (
        f"{path.relative_to(REPO_ROOT).as_posix()} runs `pip install` from "
        f"{len(stray)} place(s) the step walk in this file never reaches, so "
        f"none of the checks above apply to them: {stray}. Either move the "
        f"install into a step's `run:`, or teach installs() to reach it — do "
        f"not leave it ungoverned."
    )


def test_every_declared_requirement_is_pinned_exactly():
    """parse_requirements() asserts the shape of every line as it reads them;
    this is the test that makes those assertions run even on a day when no
    workflow names a package and the drift test returns early."""
    assert declared_requirements()


REJECTED_REQUIREMENT_LINES = [
    ("pytest-cov[toml]==7.0.0", "bare distribution name"),
    ('requests==2.32.3 ; python_version < "3.13"', "plain version"),
    ("requests>=2.32.3", "exact pin"),
    ("requests", "exact pin"),
    ("requests @ https://example.com/requests.whl", "exact pin"),
    ("-r other-requirements.txt", "exact pin"),
    ("requests==", "empty version"),
    ("pyyaml==6.0.3\nPyYAML==6.0.1", "pinned twice"),
    ("# nothing but a comment", "declares nothing"),
]


@pytest.mark.parametrize("text, expected", REJECTED_REQUIREMENT_LINES)
def test_a_requirement_line_that_is_not_one_exact_pin_is_rejected(text, expected):
    """The extras case is the one that used to pass: `pytest-cov[toml]` was
    read as the name and `7.0.0` satisfied the version check, so a line
    pulling in unnamed requirements was green."""
    with pytest.raises(AssertionError) as raised:
        parse_requirements(text, source="fixture")
    assert expected in str(raised.value)


def test_the_pins_a_requirements_file_may_hold_still_parse():
    assert parse_requirements(
        "# a comment\n\npyyaml==6.0.3  # trailing comment\n"
        "markdown-it-py==4.2.0\nPyYAML_x==1.0.0rc1\n"
    ) == {"pyyaml": "6.0.3", "markdown-it-py": "4.2.0", "pyyaml-x": "1.0.0rc1"}


# --- the recogniser's own tests -------------------------------------------
#
# The checks above are only as good as what the scanner hands them, and every
# case below is one an anchored, prefix-matching recogniser got wrong. They are
# unit tests over synthetic shell rather than over this repo's workflows, so
# they keep holding on a day when no workflow happens to be written that way.

WRAPPED_PIP_INSTALLS = [
    "sudo python3 -m pip install pyyaml==6.0.1",
    "env FOO=1 pip install pyyaml==6.0.1",
    "uv pip install pyyaml==6.0.1",
    "$PY -m pip install pyyaml==6.0.1",
    "python3 -m pip -q install pyyaml==6.0.1",
    "pip3.11 install pyyaml==6.0.1",
    "/usr/bin/pip install pyyaml==6.0.1",
    "FOO=1 BAR=2 pip3 install pyyaml==6.0.1",
    "xvfb-run --auto-servernum python -m pip install pyyaml==6.0.1",
    "set -e\ntrue && sudo -H pip install pyyaml==6.0.1",
]


@pytest.mark.parametrize("body", WRAPPED_PIP_INSTALLS)
def test_the_recogniser_sees_an_install_however_it_is_wrapped(body):
    """Each of these hid a drifted pin from the anchored recogniser this
    replaced, at exit 0 and with no test even generated for it."""
    found, unplaceable = scan_shell_body(body)
    assert not unplaceable, unplaceable
    assert len(found) == 1, found
    _, packages, _ = install_operands(found[0][1])
    assert packages == ["pyyaml==6.0.1"], found


NOT_PIP_INSTALLS = [
    "npm install left-pad",
    "apt-get install -y python3",
    "go install ./cmd/thing",
    'echo "pip install pyyaml==6.0.1"',
    "# pip install pyyaml==6.0.1\ntrue",
    "pip download pyyaml==6.0.1",
    "pip list --outdated",
    "set -uo pipefail\npip_ok=yes",
]


@pytest.mark.parametrize("body", NOT_PIP_INSTALLS)
def test_the_recogniser_reports_nothing_for_what_installs_no_package(body):
    """The other half: a recogniser that fails closed is only usable if the
    ordinary shell in these workflows does not trip it. A quoted string and a
    `#` comment are one token and no token respectively, which is why
    tokenising beats scanning lines for the words."""
    assert scan_shell_body(body) == ([], [])


UNPLACEABLE_COMMANDS = [
    "$PY -m $MODULE install pyyaml==6.0.1",
    "${PIP} install pyyaml==6.0.1",
    "pip $SUBCOMMAND pyyaml==6.0.1",
    "pip --log build.log install pyyaml==6.0.1",
]


@pytest.mark.parametrize("body", UNPLACEABLE_COMMANDS)
def test_a_command_that_might_be_an_install_is_reported_not_skipped(body):
    """Fail-closed. None of these can be read at test time as installing or
    not installing, so the scanner refuses to decide and
    test_no_workflow_command_that_might_be_a_pip_install_is_left_unplaced
    turns that refusal into a failure."""
    found, unplaceable = scan_shell_body(body)
    assert found == []
    assert len(unplaceable) == 1, unplaceable


def test_an_unquotable_shell_body_fails_rather_than_parsing_as_nothing():
    """shlex raises on an unbalanced quote. Returning no commands there would
    turn any body it cannot read into a silent pass."""
    found, unplaceable = scan_shell_body("pip install 'pyyaml==6.0.1")
    assert found == []
    assert len(unplaceable) == 1 and "tokenise" in unplaceable[0][1]


def test_a_continued_install_is_one_command():
    """account-skill-zips.yml writes its install across lines with a trailing
    backslash; the tokens either side of the break belong to one command."""
    found, unplaceable = scan_shell_body(
        "python3 -m pip install --quiet \\\n  -r requirements-dev.txt")
    assert not unplaceable
    assert [install_operands(args) for _, args in found] == [
        (["requirements-dev.txt"], [], [])]


VERSION_CHANGING_OPTIONS = [
    "python3 -m pip install -r requirements-dev.txt -c constraints.txt",
    "python3 -m pip install -r requirements-dev.txt --constraint=constraints.txt",
    "python3 -m pip install -r requirements-dev.txt -i https://evil.example.com/simple",
    "python3 -m pip install -r requirements-dev.txt --index-url https://evil.example.com/simple",
    "python3 -m pip install -r requirements-dev.txt --extra-index-url https://evil.example.com/simple",
    "python3 -m pip install -r requirements-dev.txt -f ./wheels",
    "python3 -m pip install -r requirements-dev.txt --pre",
    "python3 -m pip install -r requirements-dev.txt --no-deps",
    "python3 -m pip install -r requirements-dev.txt --trusted-host evil.example.com",
    "python3 -m pip install -r requirements-dev.txt --an-option-pip-grows-later",
]


@pytest.mark.parametrize("body", VERSION_CHANGING_OPTIONS)
def test_an_option_that_can_override_the_pins_is_rejected(body):
    """Every one of these reads requirements-dev.txt and can still install a
    version it does not name; the last is the whitelist's whole point, since
    no table written today knows what pip will grow."""
    found, unplaceable = scan_shell_body(body)
    assert not unplaceable and len(found) == 1, (found, unplaceable)
    _, _, rejected = install_operands(found[0][1])
    assert rejected, body


VERSION_NEUTRAL_INSTALLS = [
    "python3 -m pip install --quiet -r requirements-dev.txt",
    "python3 -m pip install -r requirements-dev.txt --no-cache-dir",
    "python3 -m pip install --progress-bar off -r requirements-dev.txt",
    "python3 -m pip install --progress-bar=off -r requirements-dev.txt",
    "python3 -m pip install --requirement=requirements-dev.txt",
    "python3 -m pip install -rrequirements-dev.txt",
]


@pytest.mark.parametrize("body", VERSION_NEUTRAL_INSTALLS)
def test_the_whitelist_still_admits_an_install_that_changes_no_version(body):
    """The whitelist has to leave the shapes these workflows use, and the ones
    a reviewer would reasonably reach for, able to pass — a check nothing can
    satisfy gets deleted rather than obeyed."""
    found, unplaceable = scan_shell_body(body)
    assert not unplaceable and len(found) == 1, (found, unplaceable)
    files, packages, rejected = install_operands(found[0][1])
    assert (files, packages, rejected) == (["requirements-dev.txt"], [], [])


COMPOSITE_ACTION_WITH_A_DRIFTED_PIN = """\
name: Set up
runs:
  using: composite
  steps:
    - shell: bash
      run: python3 -m pip install pyyaml==6.0.1
"""


def test_a_composite_action_is_governed_like_a_workflow_step(tmp_path):
    """.github/actions/**/action.yml runs shell in this repo's own jobs, so a
    pin can drift back in there. The walk used to read `jobs -> steps -> run`
    in .github/workflows/ only, and a composite action has neither the
    directory nor the `jobs` key."""
    action = tmp_path / ".github" / "actions" / "setup" / "action.yml"
    action.parent.mkdir(parents=True)
    action.write_text(COMPOSITE_ACTION_WITH_A_DRIFTED_PIN, encoding="utf-8")

    assert governed_files(tmp_path) == [action]
    found, unplaceable = scan_workflows(tmp_path)
    assert not unplaceable
    assert [(where, command) for where, command, _ in found] == [
        (".github/actions/setup/action.yml composite action step 0",
         "python3 -m pip install pyyaml==6.0.1")
    ]
    assert install_operands(found[0][2]) == ([], ["pyyaml==6.0.1"], [])


def test_the_second_pair_of_eyes_reaches_a_composite_action_too(tmp_path):
    """The stray-scalar walk has to know where a composite action's steps
    live, or every one of its `run:` bodies reads as a place the step walk
    never visited and the file fails on its own governed shell."""
    doc = yaml.safe_load(COMPOSITE_ACTION_WITH_A_DRIFTED_PIN)
    assert step_run_paths(doc) == {".runs.steps[0].run"}
    stray = [key for key, text in scalars(doc)
             if key not in step_run_paths(doc) and any(scan_shell_body(text))]
    assert stray == []
