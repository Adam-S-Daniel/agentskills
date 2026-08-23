"""Invariants of the two account-store workflows.

Parsed with the `yaml` library, never scanned as text: a regex reads clean on
structure it cannot see, and every invariant here is structural (which
permissions a job holds, which triggers publish a context, what a `run:` block
does). Same rule the fleet AGENTS.md states for workflow lints.
"""

import itertools
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


# `#` OPENS A COMMENT WHEREVER IT BEGINS A WORD, and a word begins after
# whitespace OR after an unquoted metacharacter. `: ;;# note` is a comment to
# bash - verified, `case x in x) : ;;# note` runs the arm and prints nothing -
# and it was not one to either scanner here, which is the same false red these
# scanners exist to remove. `;`, `&`, `|` and `(` are in; `<` and `>` are not,
# because `echo hi >#x` is a syntax error either way and `bash -n` catches it.
#
# `)` IS NOT IN THIS SET BECAUSE IT DOES NOT ALWAYS DELIMIT, and unlike the
# characters above it cannot be decided from the character alone. It gets
# `_WordParens` below, which decides each `)` from the scan's own state.
_COMMENT_OPENS_AFTER = " \t\n;&|("


# THE `)` SHAPES `_WordParens` TELLS APART, each beside the script that runs
# it. The class named these in prose and claimed every one was executed in a
# real bash; two of them - a function definition's parameter list and `>( )` -
# had no script, and nothing in this file could notice. So the enumeration
# lives here instead of in the prose, and
# test_every_opener_this_file_models_is_executed_by_the_table holds that each
# name below is in `_COMMENT_OPENER_SCRIPTS` and that the table's own answer
# is the one recorded here.
#
# Second field: does this `)` END A WORD - so that a `#` straight after it
# opens a comment?
_PAREN_SHAPES = {
    "a `case` pattern terminator": ("after-a-case-pattern-paren", True),
    "a subshell or group close": ("after-a-subshell-close", True),
    "an arithmetic `(( ))` close": ("after-an-arithmetic-command-close", True),
    "a function definition's parameter list":
        ("after-a-function-definitions-parameter-list", True),
    "a command substitution `$( )`":
        ("after-a-command-substitution-close", False),
    "an input process substitution `<( )`":
        ("after-a-process-substitution-close", False),
    "an output process substitution `>( )`":
        ("after-an-output-process-substitution-close", False),
}

# Where an unquoted WORD ends. Everything else - including `=`, `{`, `}`, `[`
# and `$` - is a word character here, so `case=1` and `escape` cannot be read
# as the keyword `case` or `esac`.
_WORD_BREAK = " \t\n;&|()<>"

# Words after which the NEXT word is still in command position, which is
# where `case` and `esac` are keywords rather than ordinary words.
_KEEPS_COMMAND_POSITION = frozenset({
    "!", "{", "}", "then", "do", "else", "elif", "time",
})


class _WordParens:
    """Which `)` end a WORD and which end a COMMAND, tracked as a scan walks.

    A `#` opens a comment only where a word begins, so whether the `)` before
    it delimits is what decides whether the rest of the line is code or prose
    - and `)` does both jobs in bash. As an OPERATOR it ends a word and the
    `#` after it opens a comment; as the close of a SUBSTITUTION it sits
    inside a word, so the `#` after it is ordinary text and the line goes on
    being code. `_PAREN_SHAPES` above enumerates both groups and names the
    script that runs each one.

    BOTH DIRECTIONS COST SOMETHING AND THEY ARE NOT THE SAME COST. Treating
    every `)` as an operator invents a comment where bash sees data, which
    deletes code from the scan. Treating none of them as one is the false red
    this scanner was fixed for and then only half fixed: the audit step's
    `case` is built out of pattern-terminating `)`, so a `)#` there reads as
    an arm whose pattern the scanner cannot find, or as a block with no
    catch-all, on shell that runs exactly as it always did.

    Neither direction is asserted in this comment.
    test_bash_opens_a_comment_only_after_a_paren_that_ends_a_word runs each
    script in a real bash, and the test beside it requires this class to have
    reached the same answer.

    THE STACK HOLDS ONE FLAG PER OPEN PAREN - True when that paren opened a
    word - so `$((1+2))#y`, whose inner `(` is a group and whose outer one is
    a substitution, comes out as a word.

    A `case` PATTERN TERMINATOR IS NOT A CLOSE AT ALL, and it is the one `)`
    that must not touch the stack. Its `(` is optional and the shipped step
    omits it, so popping for it consumes some OTHER construct's flag: inside
    a command substitution it ate the `$(`, and the substitution's real `)`
    then read as an operator, so `x=$(case y in y) echo hi ;; esac)#` opened a
    comment in both scanners and in no bash. That is the expensive direction
    named above - code deleted from the scan - so the class tracks open `case`
    statements rather than inferring one from an empty stack.

    WHAT IT DOES NOT MODEL. `case` and `esac` are recognised as WORDS in
    command position, the same latitude `_CASE_TOKEN` already takes, so a
    literal unquoted `case` used as a command name would open a context that
    only a matching `in` can arm. `esac` reached through a variable, or a
    `case` word built by expansion, is outside the model - as it is for every
    other scanner in this file.
    """

    __slots__ = ("_stack", "_last_close_in_word", "_cases", "_word",
                 "_word_cmd", "_cmd")

    def __init__(self):
        self._stack = []
        self._last_close_in_word = False
        # One entry per open `case`: [paren depth it opened at, state]. State
        # is "in" while the `case WORD in` head is being read, "pattern" while
        # a pattern is, "body" inside an arm.
        self._cases = []
        self._word = ""
        self._word_cmd = True
        self._cmd = True

    def _end_word(self):
        word, self._word = self._word, ""
        if not word:
            return
        if word == "case" and self._word_cmd:
            self._cases.append([len(self._stack), "in"])
        elif self._cases and self._cases[-1][0] == len(self._stack):
            if word == "in" and self._cases[-1][1] == "in":
                self._cases[-1][1] = "pattern"
            elif word == "esac" and self._word_cmd:
                self._cases.pop()
        self._cmd = word in _KEEPS_COMMAND_POSITION

    def _awaiting_a_pattern(self):
        return bool(self._cases) and self._cases[-1] == [
            len(self._stack), "pattern"]

    def _pattern_ended(self):
        self._cases[-1][1] = "body"
        self._last_close_in_word = False
        self._cmd = True

    def end_of_line(self):
        """The caller reached a newline that this scan was never fed.

        `code()` hands `_uncomment` one line at a time with the newlines
        already taken off, so without this a word at the end of one line
        would go on being read into the first word of the next.
        """
        self._end_word()
        self._cmd = True

    def saw(self, s, i, in_a_word):
        """Feed the character at `s[i]`.

        `in_a_word` when the character is quoted or backslash-escaped, and so
        is data rather than punctuation.
        """
        ch = s[i]
        if in_a_word:
            # Quoting takes a keyword's keyword-ness away - `"case"` in
            # command position is a command name - so a quoted character ends
            # whatever word was being read and starts nothing.
            self._word = ""
            self._cmd = False
            if ch == ")":
                self._last_close_in_word = True
            return
        if ch not in _WORD_BREAK:
            if not self._word:
                self._word_cmd = self._cmd
            self._word += ch
            return
        self._end_word()
        if ch == "(":
            self._stack.append(i > 0 and s[i - 1] in "$<>")
            self._cmd = True
        elif ch == ")":
            if self._awaiting_a_pattern():
                self._pattern_ended()
                return
            if self._stack:
                self._last_close_in_word = self._stack.pop()
                # A `case` left unclosed inside the paren just popped cannot
                # go on matching depths outside it.
                while self._cases and self._cases[-1][0] > len(self._stack):
                    self._cases.pop()
                if self._awaiting_a_pattern():
                    # The optional `(` form: `(pattern)`. The `(` was pushed,
                    # this `)` closed it, and the arm body starts here.
                    self._pattern_ended()
                    return
            else:
                self._last_close_in_word = False
            self._cmd = False
        elif ch in ";&|\n":
            if (ch == ";" and s[i + 1:i + 2] in (";", "&")
                    and self._cases
                    and self._cases[-1][0] == len(self._stack)):
                # `;;`, `;&` and `;;&` all end an arm; the next word is a
                # pattern again.
                self._cases[-1][1] = "pattern"
            self._cmd = True

    def opens_comment(self, s, i):
        """Would a `#` at `s[i]` begin a word, and so open a comment?"""
        if i == 0:
            return True
        prev = s[i - 1]
        if prev == ")":
            return not self._last_close_in_word
        return prev in _COMMENT_OPENS_AFTER


def _uncomment(line, parens=None):
    """One line of shell with a trailing `#` comment removed.

    Quote-aware, because the alternative is a lint that mangles the step's own
    `::warning::` text. Bash starts a comment at a `#` that begins a WORD -
    see `_COMMENT_OPENS_AFTER` and `_WordParens` for where a word begins - and
    only when it is not quoted, so `${v#x}`, `$#` and `a#b` are untouched, and
    a `#` inside `'...'` or `"..."` is data. A backslash escapes the next
    character everywhere except inside single quotes.

    PAREN STATE IS THE CALLER'S, and that is what `parens` is for. A `$( )`
    may span lines, so its closing `)` sits on a line whose own text holds no
    `(` at all - and a fresh `_WordParens` per line reads that `)` as an
    operator and cuts at the `#` after it. Measured in bash: `x=$(echo hi` /
    `)#y` sets `x` to `hi#y`, so the `#` is data. `_shell_scan` walks a whole
    body with ONE instance and got that right while this helper, called a
    line at a time, deleted the `#y`; two scanners disagreeing about the same
    bytes is the defect round 2 opened and this parameter is what closes it.
    `code()` passes one instance for the whole body. A caller with a single
    line and no wider context passes none and gets a fresh one.

    WHAT IT DELIBERATELY DOES NOT MODEL, stated so nobody reads more into it:
    QUOTING is tracked per LINE - not paren state, which is now the caller's -
    and a heredoc body is read as shell rather than as its own language. A
    string left open at a newline, or a `#` inside the Python heredoc, is
    therefore approximated. Both are the same approximation the whole-line
    version already made, and both fail toward dropping text rather than
    inventing it - which is the safe direction for every assertion built on
    this: text that is not here cannot satisfy a claim, and that is the point
    of the helper.
    """
    quote = None
    if parens is None:
        parens = _WordParens()
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and quote != "'":
            if i + 1 < len(line):
                parens.saw(line, i + 1, True)
            i += 2
            continue
        if quote:
            parens.saw(line, i, True)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and parens.opens_comment(line, i):
            parens.end_of_line()
            return line[:i].rstrip()
        else:
            parens.saw(line, i, False)
        i += 1
    parens.end_of_line()
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

    ONE `_WordParens` FOR THE WHOLE BODY, for the reason `_uncomment`'s own
    `parens` paragraph gives: the audit step's verdict capture is a `$( )`
    spanning twenty-odd lines, and a per-line instance reads its closing `)`
    as an operator.
    """
    parens = _WordParens()
    return "\n".join(
        stripped for stripped in (
            _uncomment(line, parens) for line in body.splitlines())
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


_CASE_TOKEN = re.compile(r'(?<![\w.-])(?:case|esac)(?![\w.-])|;;&|;;|;&')
# THE QUOTES ON THE WORD ARE OPTIONAL, because bash cannot see them. `case`
# expands its word but does NOT field-split or pathname-expand the result -
# verified: `v="a b"; case $v in "a b")` matches, and `v="*"; case $v in "*")`
# matches the literal star - so `case $verdict in` and `case "$verdict" in`
# are the same command. Requiring the quoted form made an editor's reformat
# red most of the tests reading each workflow, which is the false red this
# whole scanner exists to stop. (Contrast a case PATTERN, where the quotes are
# load-bearing and `_unquote` keeps them on anything glob-like.)
_CASE_VERDICT = re.compile(r'(?<![\w.-])case\s+"?\$verdict"?\s+in(?![\w.-])')
_CASE_SKILL = re.compile(r'(?<![\w.-])case\s+"?\$SKILL"?(?![\w.-])')
_CASE_FROM_RUN = re.compile(r'(?<![\w.-])case\s+"?\$FROM_RUN"?(?![\w.-])')


def _shell_scan(body):
    """(text, mask) for a `run:` body. Both are the SAME LENGTH as `body`.

    `text` is `body` with COMMENT characters blanked. `mask` is `text` with
    QUOTED characters blanked as well. Same length in, same length out, so an
    index found in either indexes all three - which is what lets `_tail` and
    `_guard` go on returning slices of the raw body.

    A BACKSLASH ESCAPE IS BLANKED IN THE MASK WITH THE CHARACTER IT ESCAPES,
    AND A LINE CONTINUATION IS NOT. `\\` before a newline is an operator - it
    joins two lines into one command - so it stays in the mask, and it is the
    only backslash the mask ever carries. `\\\\` before a newline is an escaped
    backslash, an ordinary word character that joins nothing, and both halves
    of it are blanked. The distinction is the mask's alone: the TEXT of the
    two shapes ends in a backslash either way, so a caller that reads the text
    to find a continuation reads `echo \\\\` as continued and glues the next
    command onto it. `_logical_lines` is that caller and
    test_an_escaped_backslash_does_not_continue_a_line runs the pair.

    COMMENTS OPEN WHERE BASH OPENS THEM, which is `_COMMENT_OPENS_AFTER` plus
    the stateful `)` rule in `_WordParens`, and not merely after whitespace.
    `: ;;# note` is a comment; reading it as code made the `;;` inside the
    prose end an arm and the next arm start mid-sentence, so the block reded
    with "an arm has no pattern this test can read" over a missing space.
    `_ARMS_OK` shapes and the decoys in
    test_the_tail_slice_is_not_relocated_by_a_comment hold it, and
    test_this_files_scanners_agree_with_bash_about_where_a_comment_opens
    holds the opener rule itself against a real bash.

    ONE WALK, ONE QUOTE MODEL, and that is the point rather than an
    efficiency. `_uncomment` tracks quotes per LINE; anything that tracks them
    across lines and consumes `_uncomment`'s output disagrees with it, and the
    disagreement is not benign. Measured: wrap a `::warning::` onto two lines,
    valid bash, and put ` #120` on the continuation before its closing quote.
    The per-line stripper cuts at the `#`, taking the closing quote and the
    `;;` with it; a whole-text masker then sees a string left open and reads
    the rest of the block - including the real `esac` - as data. The shipped
    helper passes that shape. A composed one does not, and it blames the
    `case` block for damage done in an arm above it.

    The two remedies that look right and were measured wrong: resetting quote
    state at each newline reds every wrapped string, including ones with no
    `#` at all, because the closing quote then masks the `;;` after it; and
    masking first and stripping comments off the mask reds an apostrophe
    inside a comment - `# skills-evals' verdict vocabulary ...`, which this
    repo's prose writes constantly - because the apostrophe opens a quote that
    never closes. Testing `#` BEFORE entering quote state, in the same pass, is
    what handles both. ALL THREE REJECTED MODELS ARE KEPT RUNNABLE - see
    `_model_per_line_quotes`, `_model_strip_then_mask`, `_model_mask_then_strip`
    - and test_each_rejected_quote_model_is_caught_by_its_own_fixture runs
    every one of them against every fixture in `_MODEL_MATRIX`, both
    directions, each time this suite does. A rejection nobody can re-run is
    the same unasserted claim #120 is about. Which fixture catches which
    model is that matrix and not this paragraph, for a reason the comment
    above the wrapped shapes in `_ARMS_OK` records: prose here said each of
    those three caught a model the others did not, and two of them did not.

    `shlex` is the obvious alternative and is wrong twice over. It REMOVES
    quotes, and `"$drift"` versus `$drift` is exactly what
    test_the_case_block_names_every_status_the_module_knows exists to assert -
    an unquoted expansion is re-read as a pattern, so a glob in the constant
    would silence every verdict this step annotates. And it has no notion of a
    `case` pattern: `*[!A-Za-z0-9]*` and `''|*)` are not words to it.

    WHAT IT DELIBERATELY DOES NOT MODEL, so nobody reads more into it: a
    heredoc body is read as shell rather than as its own language, so quote
    state carries through the Python heredoc in the audit step. A `#` comment
    holding an apostrophe is still handled, because the `#` is seen before any
    quote is entered. A line that leaves a shell quote OPEN is not handled at
    all: from there the mask is out of step with the body until some later
    quote character resynchronises it, and where that lands is what decides
    the outcome.

    THAT OUTCOME IS NOT RELIABLY LOUD, and this docstring claimed the opposite
    until #120's own review caught it. Splicing such a line at every position
    of the real step body: many positions resynchronise before the `case` and
    the scan is UNAFFECTED - it passes, silently and correctly; many raise
    with the sentence the caller passed; and a few land on a red whose message
    is about something else, up to and including the cross-repo drift
    accusation that names two files which agree.
    test_an_unbalanced_quote_inside_a_heredoc_is_not_reliably_loud runs that
    sweep and holds both of the first two directions, so this paragraph cannot
    quietly become a promise again.

    EXTGLOB IS OUTSIDE THE MODEL IN THE QUIET DIRECTION OUTRIGHT. `@(stale
    |missing)` is not refused; it is split at the `|` into the literal tokens
    `@(stale` and `missing`, and the caller then reds with that same drift
    accusation about a rename nobody made. An `esac` reached through a
    variable and a `case` whose word is built by expansion are outside the
    model too, and nothing here promises how either fails.

    Two things bound the damage, and neither is a promise of a loud failure.
    A shape has to be valid bash to ship, and `bash -n` rejects `@(...)` in a
    file that has not run `shopt -s extglob` first. And every assertion built
    on this compares a whole SET, so a lost or mangled arm reds rather than
    passing quietly - loud, but with a diagnosis that may point at the wrong
    file.
    """
    text = list(body)
    mask = list(body)
    quote = None
    parens = _WordParens()
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch == "\\" and quote != "'":
            if i + 1 < n and body[i + 1] == "\n":
                mask[i] = "\\"
            else:
                mask[i] = " "
                if i + 1 < n:
                    mask[i + 1] = " "
                    parens.saw(body, i + 1, True)
            i += 2
            continue
        if quote:
            if ch != "\n":
                mask[i] = " "
            parens.saw(body, i, True)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            mask[i] = " "
            quote = ch
            i += 1
            continue
        if ch == "#" and parens.opens_comment(body, i):
            j = body.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                text[k] = " "
                mask[k] = " "
            i = j
            continue
        parens.saw(body, i, False)
        i += 1
    return "".join(text), "".join(mask)


# THE THREE QUOTE MODELS `_shell_scan` REJECTS, KEPT RUNNABLE. Its docstring
# says each of them is wrong and names the `_ARMS_OK` shape that proves it -
# a claim a reader cannot check by reading, and the one shape of claim #120 is
# about. So they live here and a test runs them.
#
# Each is the smallest faithful version of the idea rather than a straw man:
# same signature, same return contract, same comment-opening rule - every
# scanner in this file asks `_WordParens` and `_COMMENT_OPENS_AFTER` the same
# question, and the same backslash rule - `_shell_scan`'s mask keeps a line
# continuation and blanks an escape, and so do these - differing only in where
# quote state is tracked. A model that also differed in where a comment opens,
# or in what the mask says about a backslash, would be discriminated by the
# wrong half.


def _model_per_line_quotes(body):
    """Quote state RESET at every newline. Wrong because a `::warning::` may be
    wrapped over two lines: the closing quote then masks the `;;` after it."""
    text, mask = list(body), list(body)
    parens = _WordParens()
    quote, i, n = None, 0, len(body)
    while i < n:
        ch = body[i]
        if ch == "\n":
            quote = None
            parens.saw(body, i, False)
            i += 1
            continue
        if ch == "\\" and quote != "'":
            if i + 1 < n and body[i + 1] == "\n":
                mask[i] = "\\"
            else:
                mask[i] = " "
                if i + 1 < n:
                    mask[i + 1] = " "
                    parens.saw(body, i + 1, True)
            i += 2
            continue
        if quote:
            mask[i] = " "
            parens.saw(body, i, True)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            mask[i] = " "
            quote = ch
            i += 1
            continue
        if ch == "#" and parens.opens_comment(body, i):
            j = body.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                text[k] = mask[k] = " "
            i = j
            continue
        parens.saw(body, i, False)
        i += 1
    return "".join(text), "".join(mask)


def _model_strip_then_mask(body):
    """Comments stripped PER LINE first, then quotes masked over the whole
    text. Wrong because the per-line stripper cuts a wrapped string at a `#`
    on its continuation, taking the closing quote with it."""
    parens = _WordParens()
    text = "\n".join(
        _uncomment(l, parens).ljust(len(l)) for l in body.split("\n"))
    mask = list(text)
    quote, i, n = None, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and quote != "'":
            if i + 1 < n and text[i + 1] == "\n":
                mask[i] = "\\"
            else:
                mask[i] = " "
                if i + 1 < n:
                    mask[i + 1] = " "
            i += 2
            continue
        if quote:
            if ch != "\n":
                mask[i] = " "
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            mask[i] = " "
            quote = ch
            i += 1
            continue
        i += 1
    return text, "".join(mask)


def _model_mask_then_strip(body):
    """Quotes masked over the whole text FIRST, comments taken off the mask
    afterwards. Wrong because an apostrophe inside a comment opens a quote
    that never closes."""
    mask = list(body)
    quote, i, n = None, 0, len(body)
    while i < n:
        ch = body[i]
        if ch == "\\" and quote != "'":
            if i + 1 < n and body[i + 1] == "\n":
                mask[i] = "\\"
            else:
                mask[i] = " "
                if i + 1 < n:
                    mask[i + 1] = " "
            i += 2
            continue
        if quote:
            if ch != "\n":
                mask[i] = " "
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            mask[i] = " "
            quote = ch
            i += 1
            continue
        i += 1
    text, i = list(body), 0
    parens = _WordParens()
    masked = "".join(mask)
    while i < n:
        if mask[i] == "#" and parens.opens_comment(masked, i):
            j = body.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                text[k] = mask[k] = " "
            i = j
            continue
        parens.saw(masked, i, False)
        i += 1
    return "".join(text), "".join(mask)


# A HEREDOC OPENER IS `<<` OR `<<-`, AND `<<<` IS NOT ONE. A here-string has
# no body - it feeds one word to stdin - so reading its word as a delimiter
# blanks every line from there to the end of the step. That is not a false
# red; it is a silent green, because every rule downstream iterates commands
# and a blanked line produces none. `if grep -q fresh <<<yes; then` above the
# `case` was enough to hide a bare `mkdir` from the guard below without reding
# anything. So the `<<` may not touch a third `<` on either side, and
# test_a_here_string_does_not_blank_the_rest_of_the_step runs that exact
# shape.
_HEREDOC_OPENER = re.compile(
    r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def _strip_heredocs(body):
    """`body` with every heredoc BODY blanked, line for line.

    A heredoc is another language sitting inside the shell - the audit step's
    is Python - and `_shell_scan` says so in its own limitations: it reads one
    as shell and lets quote state carry through it. Anything classifying the
    step's COMMANDS has to drop it first, or `sys.path.insert(0, "...")`
    arrives as an unguarded command that can fail.

    Blanked rather than deleted so a line number still means what it says, and
    so quote state cannot leak out of the heredoc into the shell around it.
    The delimiter line goes too: it is the heredoc's punctuation, not a
    command. The opener is read off `_uncomment`'s output so a `<<` inside a
    comment cannot start one - a line at a time, with no shared
    `_WordParens`, because the loop below never feeds it the heredoc BODIES
    and a paren state assembled from only some of a body's lines is worse
    than none.

    AN OPENER WHOSE DELIMITER NEVER ARRIVES RAISES. `_HEREDOC_OPENER` closes
    the one misreading of an OPENER that has been demonstrated - a here-string
    - and the assertion closes the consequence of reading an opener where
    there is none, because a word that is not a delimiter is a word no later
    line equals: the tail is blanked to the end of the body and every rule
    built on this classifies nothing. An arithmetic `$(( x<<n ))` is the next
    candidate and is NOT excluded by the regex: `n` reads as a delimiter. It
    reds here instead, loudly, naming the line. A `run:` body with a genuinely
    unterminated heredoc reds here too, which is correct - bash warns about
    that one as well.

    THE ASSERTION DOES NOT COVER THE OPPOSITE MISREADING, AND THIS DOCSTRING
    CLAIMED IT DID. Ending a heredoc EARLY blanks less rather than more, so no
    delimiter goes missing and nothing raises; the lines after the false
    terminator come back as live shell, and in this step they fold into the
    `verdict=$(python3 - <<'PY' ... ) || verdict=""` command, whose final `||`
    marks the whole blob guarded. That direction is closed by MATCHING BASH'S
    OWN TERMINATOR RULE instead: a terminator is the delimiter alone on a
    line, at column 0, and only `<<-` allows anything before it - TABS, never
    spaces. `  EOF` indented under a plain `<<EOF` is DATA and the heredoc
    runs past it, which is what
    test_an_indented_terminator_does_not_end_a_heredoc runs in a real bash
    and then requires of this helper.
    """
    out, delim, dash, opened_at = [], None, "", None
    for number, line in enumerate(body.split("\n"), 1):
        if delim is None:
            out.append(line)
            opener = _HEREDOC_OPENER.search(_uncomment(line))
            if opener:
                dash, delim = opener.group(1), opener.group(3)
                opened_at = number
            continue
        out.append(" " * len(line))
        if (line.lstrip("\t") if dash else line) == delim:
            delim = None
    assert delim is None, (
        f"line {opened_at} of this `run:` body reads as a heredoc opening on "
        f"`{delim}`, and no later line is that delimiter. Either the heredoc "
        f"is unterminated, or this is not an opener at all and the rest of "
        f"the body has just been blanked - which would leave every rule built "
        f"on this classifying nothing and passing."
    )
    return "\n".join(out)


def _logical_lines(body):
    """A `run:` body as COMMANDS: (first line, last line, text, mask) tuples.

    Heredocs blanked, comments blanked by `_shell_scan`, backslash
    continuations joined, and a command that spans lines inside an unclosed
    `$( )` joined with it - which is the only reason the audit step's heredoc
    capture and its `|| verdict=""` end up in one entry rather than two.

    A CONTINUATION IS FOUND IN THE MASK, NOT IN THE TEXT, and that is the
    difference between joining two lines and swallowing one. The text of
    `echo \\\\` ends in a backslash exactly like the text of `echo foo \\`, so
    reading the text joined the NEXT command onto an `echo` and handed the
    pair to the carve-out as one command called `echo` - the unguarded half
    disappeared from the classification entirely. The mask carries a
    backslash only where one continues a line, which is what `_shell_scan`'s
    own backslash paragraph exists to provide.

    TEXT AND MASK STAY THE SAME LENGTH, so an index found in the mask - an
    operator that is not inside a `::warning::` - indexes the text. That is
    why nothing here is `.strip()`ed: the mask blanks quoted characters, so
    stripping the two independently slides them out of step.

    A `)` that closes nothing takes the depth negative - a `case` pattern
    terminator does exactly that - so the depth is reset per command rather
    than carried.
    """
    text, mask = _shell_scan(_strip_heredocs(body))
    commands, buf_t, buf_m, start, depth = [], "", "", None, 0
    for number, (line_t, line_m) in enumerate(
            zip(text.split("\n"), mask.split("\n")), 1):
        if start is None:
            if not line_t.strip():
                continue
            start = number
        buf_t, buf_m = buf_t + line_t, buf_m + line_m
        depth += line_m.count("(") - line_m.count(")")
        if buf_m.rstrip().endswith("\\"):
            cut = buf_m.rindex("\\")
            buf_t, buf_m = buf_t[:cut], buf_m[:cut]
            continue
        if depth > 0:
            buf_t, buf_m = buf_t + "\n", buf_m + "\n"
            continue
        commands.append((start, number, buf_t, buf_m))
        buf_t, buf_m, start, depth = "", "", None, 0
    assert start is None, (
        f"a command starting at line {start} of this `run:` body never "
        f"closed its `$(`, so the rest of the body was read as part of it"
    )
    return commands


def _top_level(mask, tokens):
    """Indices in `mask` where one of `tokens` sits outside `( )`."""
    found, depth, i = [], 0, 0
    while i < len(mask):
        ch = mask[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            for token in tokens:
                if mask.startswith(token, i):
                    found.append((i, token))
                    i += len(token)
                    break
            else:
                i += 1
                continue
            continue
        i += 1
    return found


def _code_index(text, mask, needle, what):
    """Index of the first `needle` that is CODE - not inside a string or a
    comment. `what` is the sentence to fail with if there is none."""
    i = -1
    while True:
        i = text.find(needle, i + 1)
        assert i != -1, what
        if mask[i] == needle[0]:
            return i


def _case_block(text, mask, opener_re, what):
    """(opener, arm spans, closer) for ONE `case` block, read off the mask.

    `esac` closes THIS block only at depth 0, so a nested `case` in an arm
    body no longer ends it early - which is a false red today whenever the
    nested block precedes the catch-all, and a pass by luck when it does not.
    `esac` as a WORD, so `esacapade` in a `::warning::` does not either. All
    three arm terminators, because bash has three: `;;` ends an arm, `;&`
    falls through to the next arm's body, `;;&` goes on testing patterns.

    The opener is matched in `text` and CONFIRMED against `mask`, because the
    mask blanks the `"` around `$verdict` and so cannot be matched directly.
    """
    opener = next((m for m in opener_re.finditer(text)
                   if mask[m.start()] == "c"), None)
    assert opener, what
    depth, start, spans, closer = 0, opener.end(), [], None
    for m in _CASE_TOKEN.finditer(mask, opener.end()):
        token = m.group(0)
        if token == "case":
            depth += 1
        elif token == "esac":
            if depth == 0:
                closer = m
                break
            depth -= 1
        elif depth == 0:
            spans.append((start, m.start()))
            start = m.end()
    assert closer is not None, (
        "a `case` this test reads has no closing `esac` outside a string and "
        f"outside a nested `case`, so its arms would be counted as absent: "
        f"{what}"
    )
    spans.append((start, closer.start()))
    return opener, spans, closer


# The three characters that make a `case` pattern a PATTERN. Quoting any of
# them takes that away, which is why `_unquote` refuses to touch a token that
# holds one.
_GLOB_META = "*?["


def _pattern_payload(token):
    """(the literal TEXT of one `case` pattern, whether it holds a bare `*`).

    A `*` outside quotes is the wildcard; every other character, and every
    character inside quotes, is text the subject has to match. So a token
    whose text is empty and which holds a bare `*` matches EVERY string, and a
    token whose text is empty without one matches only the empty string.
    Neither is a status name, and `_unquote` cannot tell either of them from
    one: it only strips a symmetric pair of quotes around a whole token, so
    `""*`, `*''` and the `''` in `''|*` all come back looking like literals
    the module has never heard of, and the caller reds with the cross-repo
    drift accusation about a `case` that is behaving perfectly.

    QUOTING AN EMPTY STRING TAKES NOTHING AWAY, which is what separates these
    from `"*"`. Quoting the STAR takes the glob away and leaves a match on a
    literal asterisk - a dead catch-all, and the silent-green hole `_ARMS_RED`
    holds in both quotings. `"*"` has text `*`, so it is not a catch-all here
    either; the text being EMPTY is the whole test.

    A backslash escapes the next character into the text, so `\\*` is a match
    on a literal asterisk rather than a catch-all. An unbalanced quote returns
    no text at all and the caller falls back to comparing the token as
    written: guessing at half a pattern is how a scanner starts accepting
    shapes bash does not.
    """
    text, star, i, n = [], False, 0, len(token)
    while i < n:
        ch = token[i]
        if ch == "\\":
            text.append(token[i + 1:i + 2])
            i += 2
            continue
        if ch in "'\"":
            close = token.find(ch, i + 1)
            if close == -1:
                return None, False
            text.append(token[i + 1:close])
            i = close + 1
            continue
        if ch == "*":
            star = True
        else:
            text.append(ch)
        i += 1
    return "".join(text), star


def _unquote(token):
    """One `case` pattern with a symmetric pair of surrounding quotes removed -
    but only where the quotes are invisible to bash.

    `"fresh"`, `'fresh'` and `fresh` are one pattern, so the set comparison in
    the caller must not tell them apart. `"*"` and `*` are NOT one pattern:
    quoting turns the catch-all into a match on a literal asterisk. Measured:
    `case wat in "*") echo hit ;; esac` prints nothing and exits 0, while the
    unquoted form prints `hit`.

    So the quotes come off only when the token holds no `*`, `?` or `[`, and
    stay on otherwise - where staying on is what makes the token compare
    unequal to `*` and to every status name, so the caller reds instead of
    accepting a `case` whose unknown-verdict arm matches nothing. Stripping
    them unconditionally is the silent-green hole
    test_the_case_block_names_every_status_the_module_knows exists to close,
    and `_ARMS_RED` holds both quotings of it.

    Only ever applied to a LITERAL - see the caller for why an expansion keeps
    its quotes.
    """
    if (len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'"
            and not any(ch in _GLOB_META for ch in token[1:-1])):
        return token[1:-1]
    return token


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


def _audit_body():
    return next(
        s["run"] for s in load(ZIPS)["jobs"]["pick"]["steps"]
        if s.get("id") == "audit"
    )


def _splice(block):
    """The REAL audit body with its `case` block swapped for `block`.

    In memory, and there are no fixture FILES on purpose. A file would be a
    second copy of the shipped `case` with nothing keeping it in step with the
    first, and a stale fixture is a test asserting a shape the workflow no
    longer has - green, and about nothing. Built here, every block is the real
    body with one thing changed.

    DRIFT BETWEEN `_ARMS_BASE` AND THE STEP FAILS IN TWO DIFFERENT PLACES, and
    it is worth knowing which one you are looking at. Drift that moves an
    anchor - a reworded `::warning::`, a renamed status, a moved terminator -
    trips `_arm` at IMPORT and pytest reports a collection error; `_arm` says
    which anchor and which file. Drift that moves no anchor is caught by
    `_ARMS_OK["control"]`, which runs the full status assertion over
    `_ARMS_BASE` itself and reds with the vocabulary message. Neither of them
    passes quietly, and only the second one carries the drift message - which
    this paragraph used to promise for both.

    The rest of the body is kept because the test under test reads it - the
    `drift=$(` line it pins the constant read to sits above the `case`, and
    `code(body)` has to see real surroundings or the assertion would be
    passing against a fixture rather than against the step.

    THE BLOCK IS FOUND WITH THE SAME HELPER THE SCANNER USES, and that is the
    whole reason this is not two `lines.index` calls. Anchoring on
    `case "$verdict" in` and `esac` as lines of their own re-imposes exactly
    the line shape `_case_statuses` was taught to stop requiring - so applying
    a reformat this file CERTIFIES as invisible, `opener-shares-its-line`
    among them, to the real workflow made every fixture in `_ARMS_OK` and
    `_ARMS_RED` die inside this function with a bare `ValueError` naming a
    string. The scanner under test passed; the harness that feeds it did not,
    at a blast radius larger than the false red the scanner was fixed for. A
    harness may not be pickier than the thing it is testing.
    """
    body = _audit_body()
    text, mask = _shell_scan(body)
    opener, _, closer = _case_block(
        text, mask, _CASE_VERDICT,
        "the audit step's `case \"$verdict\" in` is no longer a command this "
        "fixture builder can find, so it cannot swap the block out for one",
    )
    return body[:opener.start()] + block + body[closer.end():]


# The shipped `case`, reduced to its arms: the fixtures below are this
# block with ONE thing about its formatting changed. Shorter warning text than
# the real step's, because what is under test is the shape.
_ARMS_BASE = '''case "$verdict" in
  stale|missing|unreadable)
    echo "::warning::the published account audit reads '$verdict' - liveness." ;;
  not-yet-bootstrapped)
    if [ "$results_ok" = yes ]; then
      echo "::warning::the published account audit reads '$verdict', but the branch cloned cleanly."
    fi ;;
  fresh|unavailable|"$drift")
    : ;;
  *)
    echo "::warning::the published account audit reads '$verdict', which is not a status this repo knows." ;;
esac'''

_LIVENESS = (
    """    echo "::warning::the published account audit reads '$verdict'"""
    """ - liveness." ;;"""
)
_CATCHALL = (
    """    echo "::warning::the published account audit reads '$verdict',"""
    """ which is not a status this repo knows." ;;"""
)
_QUIET = '  fresh|unavailable|"$drift")'


def _arm(old, new, count=1):
    """One `_ARMS_BASE` substitution, with its anchor asserted first.

    THIS RUNS AT IMPORT, so its failure is the whole module's failure and
    pytest reports it as a collection error rather than as a test. That is the
    right severity - every fixture below is built from `_ARMS_BASE`, so an
    anchor that has moved means none of them are the step any more - but it
    puts the burden on the message: a collection error naming a bare string is
    the failure mode `_splice`'s docstring condemns, and this assert used to
    be exactly that.
    """
    found = _ARMS_BASE.count(old)
    assert found == count, (
        f"a fixture anchors on a piece of `_ARMS_BASE` that appears {found} "
        f"time(s) rather than {count}. `_ARMS_BASE` is the audit step's "
        f"`case` reduced to its arms, so this is what a wording or vocabulary "
        f"change in .github/workflows/account-skill-zips.yml looks like from "
        f"here: nothing below is the shipped block any more, and the whole "
        f"file fails to import rather than one test failing. Re-sync "
        f"`_ARMS_BASE` with the step, then re-point the anchor. The anchor "
        f"was: {old!r}"
    )
    return _ARMS_BASE.replace(old, new, count)


# EVERY ONE OF THESE IS VALID BASH, and the test below proves that with
# `bash -n` before it asserts anything - so a typo in a fixture reds as a typo
# rather than masquerading as a helper bug.
#
# AND EVERY ONE OF THEM MAKES THE STEP ANSWER AS THE CONTROL DOES, except the
# ones `_ARMS_THAT_CHANGE_WHAT_THE_STEP_DOES` names. That used to be written
# here as "says exactly what the shipped block says" with nothing behind it
# but `bash -n`, and it was false for the two terminator fixtures: `;;&` on
# the quiet arm goes on to match `*)`, so `fresh` - the happy path the whole
# block exists to keep silent - annotated. A fixture free to change behaviour
# is a fixture that can certify a real behaviour change as invisible, which is
# the one thing this set must not do.
#
# WHAT "ANSWER" MEANS IS THE DECISION, NOT THE TEXT: how many annotations the
# tail raises and what it writes to $GITHUB_OUTPUT. Several shapes here edit
# the warning wording on purpose, and `_ARMS_BASE` is already shorter than the
# shipped step, so comparing text would assert the opposite of what the set is
# for. test_a_reformat_leaves_the_step_answering_as_it_did runs the comparison
# over every status crossed with both values of `results_ok`.
#
# MANY OF THEM RED on a scanner that reads the block as TEXT rather than as
# code, and that is the whole case for this set existing. No count here:
# nothing in this repo can run the old scanner, so a number would be
# unasserted prose about a thing that no longer exists - see #120. The false
# red is the expensive direction: it accuses an author of moving skills-evals'
# vocabulary when all they did was wrap a line, and the message it accuses
# them with points at the `case` block rather than at the reformat.
#
# THE SET SHRINKING IS HOW THE FALSE REDS GOT HERE, so its length is asserted
# by test_the_regression_set_did_not_shrink. That is the one count in this
# change that a test holds - see #120 for why an unasserted one is worse than
# none.
_ARMS_OK = [
    ("control", _ARMS_BASE),
    ("leading-paren", _arm(_QUIET, '  (fresh|unavailable|"$drift")')),
    ("opener-shares-its-line",
     _arm('case "$verdict" in\n  stale|missing|unreadable)',
          'case "$verdict" in stale|missing|unreadable)')),
    ("quoted-literals", _arm(_QUIET, '''  "fresh"|'unavailable'|"$drift")''')),
    # The word, not a pattern. `case` expands its word and then neither splits
    # nor globs the result, so the quotes here are invisible to bash - unlike
    # the quotes on the `"$drift"` PATTERN in the quiet arm, which are the
    # difference between a literal and a glob and are asserted for.
    ("unquoted-case-word",
     _arm('case "$verdict" in', 'case $verdict in')),
    ("comment-containing-a-terminator",
     _arm('  not-yet-bootstrapped)',
          '  # the arm below used to end in ;; on its own line\n'
          '  not-yet-bootstrapped)')),
    ("comment-containing-esac",
     _arm('  not-yet-bootstrapped)',
          '  # nothing between here and the esac below reads this\n'
          '  not-yet-bootstrapped)')),
    ("esac-quoted-in-the-first-arm",
     _arm(_LIVENESS, _LIVENESS.replace(
         'liveness."', 'liveness; the esac below is unaffected."'))),
    # Passes on the shipped helper too - but only BY POSITION, because its
    # `esac` happens to be the last one. Held so the position dependence is
    # covered from both sides.
    ("esac-quoted-in-the-catch-all-arm",
     _arm(_CATCHALL, _CATCHALL.replace(
         'repo knows."', 'repo knows; teach the case before its esac."'))),
    ("terminator-inside-a-quoted-body",
     _arm(_LIVENESS, _LIVENESS.replace(
         'liveness."', 'liveness ;; and that is data."'))),
    ("continue-testing-terminator",
     _arm(_QUIET + '\n    : ;;', _QUIET + '\n    : ;;&')),
    ("fallthrough-terminator",
     _arm(_QUIET + '\n    : ;;', _QUIET + '\n    : ;&')),
    ("nested-case-one-line-mid-block",
     _arm('    fi ;;',
          '    fi\n    case "$results_ok" in yes) : ;; esac ;;')),
    # THE ANTI-REGRESSION CASE. A closer found by anchoring - "the `esac` on a
    # line of its own" - reds this one, which is why nesting is COUNTED.
    ("nested-case-one-line-last-arm",
     _arm(_CATCHALL,
          '    case "$results_ok" in *) echo "::warning::unknown" ;; esac ;;')),
    ("nested-case-multiline",
     _arm('    fi ;;', '    fi\n    case "$results_ok" in\n'
          '      yes) : ;;\n      *) : ;;\n    esac ;;')),
    ("esac-as-a-substring",
     _arm(_LIVENESS, _LIVENESS.replace(
         'liveness."', 'liveness - an esacapade of renames."'))),
    ("trailing-comment-containing-a-terminator",
     _arm('    : ;;', '    : ;;  # was ;; before the fallthrough experiment')),
    # THE SAME COMMENT WITH THE SPACE TAKEN OUT, and the space was load-bearing
    # until it was not supposed to be. Bash opens a comment wherever `#` begins
    # a WORD, so `;;#` is a comment and `;; #` is the same comment - the pair
    # is here so the two cannot drift apart again.
    ("trailing-comment-with-no-space-after-the-terminator",
     _arm('    : ;;', '    : ;;# was ;; before the fallthrough experiment')),
    ("comment-opening-straight-after-a-single-semicolon",
     _arm('    : ;;', '    : ;# the esac below is unaffected\n    ;;')),
    ("comment-opening-straight-after-an-open-paren",
     _arm('    : ;;', '    (# a subshell whose esac and ;; are prose\n    :)\n    ;;')),
    # THE `)` SHAPES, in both directions. A `)` that terminates a `case`
    # pattern or closes a subshell ends a word, so the `#` after it is prose;
    # a `)` that closes `$( )` is inside a word, so the `#` after it is text
    # and the `;;` beyond it is still an arm terminator. Reading that last
    # kind as a comment loses the `;;` and merges the quiet arm into the
    # catch-all, which is why it sits here rather than in `_ARMS_RED`.
    # `_WordParens` is what tells them apart, and
    # TestWhereAHashOpensAComment runs each shape in a real bash.
    ("comment-opening-straight-after-a-case-pattern-paren",
     _arm(_QUIET, _QUIET + '# quiet on purpose; the ;; and esac here are prose')),
    ("comment-opening-straight-after-a-subshell-close",
     _arm('    : ;;',
          '    (:)# a subshell whose esac and ;; are prose\n    ;;')),
    ("hash-after-a-command-substitution-close-is-not-a-comment",
     _arm('    : ;;', '    : $(echo quiet)#not-a-comment ;;')),
    ("command-substitution-in-an-arm-body",
     _arm('    : ;;',
          '    echo "seen at $(date -u +%Y 2>/dev/null || echo ?)" ;;')),
    ("blank-lines-between-arms",
     _arm('  not-yet-bootstrapped)', '\n\n  not-yet-bootstrapped)')),
    ("no-terminator-on-the-last-arm",
     _arm(_CATCHALL + '\nesac', _CATCHALL[:-3].rstrip() + '\nesac')),
    # THE CATCH-ALL IN SPELLINGS `_unquote` CANNOT REACH. Quoting an EMPTY
    # string takes nothing away, so each of these matches what `*)` matches;
    # quoting the STAR does take the glob away, which is why both quotings of
    # that sit in `_ARMS_RED` instead. That the three below really are
    # catch-alls is executed rather than argued:
    # test_a_reformat_leaves_the_step_answering_as_it_did runs each against
    # the control for every verdict, in a real bash.
    ("catch-all-with-an-empty-alternative", _arm('  *)', "  ''|*)")),
    ("catch-all-with-a-leading-empty-string", _arm('  *)', '  ""*)')),
    ("catch-all-with-a-trailing-empty-string", _arm('  *)', "  *'')")),
    # THE THREE WRAPPED SHAPES, AND WHAT EACH IS WORTH. This comment claimed
    # each was here for a model the others do not catch; the table it carried
    # said otherwise, and the table was right. Only ONE of the three reaches
    # a second model. For model discrimination the other two
    # are near-duplicates of the first, and saying so is the point - a reason
    # that is not true is what makes a set look deletable to the next person
    # tidying it.
    #
    # THE MATRIX IS `_MODEL_MATRIX`, ASSERTED IN BOTH DIRECTIONS, so the
    # "alone" above is now a red rather than a sentence: every rejected model
    # is run against every fixture here and has to red on exactly the ones
    # recorded there.
    #
    # What keeps the second and third is not a model. Each is a different
    # wrapped-string SHAPE the real scanner has to go on reading correctly - a
    # continuation carrying no `#` at all, and a quote that does not close
    # until two lines down - and every shape in this set is run against the
    # control by test_a_reformat_leaves_the_step_answering_as_it_did.
    # `_ARMS_OK`'s floor is what makes removing one red.
    #
    # They wrap the FIRST arm rather than the catch-all deliberately: a
    # terminator lost in the LAST arm costs nothing, because the closing
    # `esac` ends that span anyway, so the same fixture at the bottom of the
    # block discriminates nothing.
    ("wrapped-warning-hash-before-the-closing-quote",
     _arm(_LIVENESS, '''    echo "::warning::the published account audit reads '$verdict' - the Tier-3
Routine has not published a usable result recently. See #120 for the history." ;;''')),
    ("wrapped-warning-with-no-hash",
     _arm(_LIVENESS, '''    echo "::warning::the published account audit reads '$verdict' - the Tier-3
Routine has not published a usable result recently. Check that Routine." ;;''')),
    ("wrapped-warning-hash-with-the-quote-closing-later",
     _arm(_LIVENESS, '''    echo "::warning::the published account audit reads '$verdict' - the Tier-3
Routine has not published a usable result recently. See #120 for the history.
Check that Routine and the eval-results branch." ;;''')),
    # Reds masking-first-then-stripping-comments-off-the-mask, which is the
    # other remedy that looks right: the apostrophe opens a quote that never
    # closes. This repo's prose writes that construction constantly.
    ("apostrophe-inside-a-comment",
     _arm('  not-yet-bootstrapped)',
          "  # skills-evals' verdict vocabulary is what this arm tracks\n"
          "  not-yet-bootstrapped)")),
]

# REFORMATS OF THE WHOLE STEP, not of a fixture, and they exist for `_splice`
# rather than for the scanner. `_splice` is what builds every block above out
# of the real body, so a shape IT cannot read takes out the entire set at once
# and does it with a message about a missing string rather than about the
# workflow. Each of these is `bash -n` clean and says exactly what the shipped
# step says; the first is `opener-shares-its-line` applied to the real file
# instead of to a fixture.
def _indent_the_case_block(body):
    head, _, rest = body.partition('case "$verdict" in\n')
    block, _, tail = rest.partition("\nesac\n")
    lines = ['case "$verdict" in'] + block.splitlines() + ["esac"]
    return (head + "if true; then\n"
            + "\n".join("  " + l for l in lines)
            + "\nfi\n" + tail)


_BODY_REFORMATS = [
    ("opener-shares-its-line",
     lambda b: b.replace('case "$verdict" in\n', 'case "$verdict" in ', 1)),
    ("trailing-comment-on-the-opener",
     lambda b: b.replace(
         'case "$verdict" in\n',
         'case "$verdict" in  # dispatch on what skills-evals said\n', 1)),
    ("the-whole-block-indented", _indent_the_case_block),
]


# The other direction, and the reason the set above is not just a licence to
# accept anything: these are real divergences and they must still red.
_ARMS_RED = [
    ("a rogue arm the module has never heard of",
     _arm('  *)', '  quota-exceeded)\n    : ;;\n  *)')),
    ("the drift arm through an UNQUOTED expansion",
     _arm(_QUIET, '  fresh|unavailable|$drift)')),
    ("no catch-all arm at all", _arm('  *)\n' + _CATCHALL + '\n', '')),
    # THE CATCH-ALL THAT IS NOT ONE, in both quotings. Quoting a `*` is not a
    # reformat: bash matches a literal asterisk and every unknown verdict
    # falls through the block in silence. It belongs HERE and not in
    # `_ARMS_OK` for that reason - a scanner that strips quotes off any
    # literal reads this as a catch-all and goes green on a workflow whose
    # unknown-verdict arm is dead.
    ("the catch-all arm through a DOUBLE-QUOTED star",
     _arm('  *)', '  "*")')),
    ("the catch-all arm through a SINGLE-QUOTED star",
     _arm('  *)', "  '*')")),
    ("an arm the module knows but the case does not",
     _arm('  stale|missing|unreadable)', '  stale|missing)')),
]



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


# WHERE A `#` OPENS A COMMENT, AS BASH ANSWERS IT RATHER THAN AS THIS FILE
# REMEMBERS IT. Each entry is a whole script, valid on its own, written so the
# answer shows up in stdout: `echo LOUD` is reachable only through text a
# comment would have swallowed, and DONE prints either way. The third field is
# what bash does, and the tests below check that against a real bash before
# holding this file's scanners to it.
_COMMENT_OPENER_SCRIPTS = [
    ("at-the-very-first-character", "#; echo LOUD\necho DONE\n", True),
    ("after-a-newline", ":\n#; echo LOUD\necho DONE\n", True),
    ("after-a-space", ": #; echo LOUD\necho DONE\n", True),
    ("after-a-tab", ":\t#; echo LOUD\necho DONE\n", True),
    ("after-a-semicolon", ": ;#; echo LOUD\necho DONE\n", True),
    ("after-an-ampersand", ": &#; echo LOUD\nwait\necho DONE\n", True),
    ("after-a-pipe", "true |#; echo LOUD\ncat /dev/null\necho DONE\n", True),
    ("after-an-open-paren", "(#; echo LOUD\n:)\necho DONE\n", True),
    # THE `)` SHAPES, and they are why this table is executed rather than
    # argued. The ones that END A COMMAND open a comment; the ones that close a
    # SUBSTITUTION are inside a word and do not. Reasoning from either group
    # alone gives the wrong rule for the other, which is how `)` ended up
    # excluded from `_COMMENT_OPENS_AFTER` on the strength of a `$( )` example.
    ("after-a-case-pattern-paren",
     "case x in\n  x)#; echo LOUD\n    echo DONE ;;\nesac\n", True),
    ("after-a-subshell-close", "(:)#; echo LOUD\necho DONE\n", True),
    ("after-an-arithmetic-command-close",
     "(( 1 ))#; echo LOUD\necho DONE\n", True),
    ("after-a-command-substitution-close",
     "x=$(echo hi)#; echo LOUD\necho DONE\n", False),
    # THE SAME SUBSTITUTION SPREAD OVER TWO LINES, which is the shape the
    # audit step actually ships - its verdict capture is a `$(` on one line
    # and a `)` twenty-odd lines later. The closing line holds no `(` of its
    # own, so a scanner that starts each line with an empty paren stack reads
    # that `)` as an operator and cuts the line at the `#`. Nothing in this
    # table could express it while the test below sliced ONE line out of the
    # script; it runs `code()` over the whole script now, for that reason.
    ("after-a-multi-line-command-substitution-close",
     "x=$(echo hi\n)#; echo LOUD\necho DONE\n", False),
    ("after-an-arithmetic-expansion-close",
     "x=$((1+2))#; echo LOUD\necho DONE\n", False),
    ("after-a-subshell-nested-in-a-substitution",
     "x=$( (echo hi) )#; echo LOUD\necho DONE\n", False),
    ("after-a-process-substitution-close",
     ": <(echo ps)#; echo LOUD\necho DONE\n", False),
    ("after-an-output-process-substitution-close",
     ": >(cat /dev/null)#; echo LOUD\necho DONE\n", False),
    ("after-a-function-definitions-parameter-list",
     "foo()#; echo LOUD\n{ echo in-foo; }\necho DONE\n", True),
    # A `case` PATTERN INSIDE ANOTHER CONSTRUCT, which is where the two roles
    # of `)` collide. The pattern's `)` closes nothing - its `(` is optional
    # and omitted here - so a scanner that pops for it spends the enclosing
    # construct's stack entry, and the enclosing `)` then reads as an
    # operator. All three of these are one character apart in what the `#`
    # follows and two of them answer the opposite way.
    ("after-a-substitution-that-closes-a-case",
     "x=$(case y in y) echo hi ;; esac)#; echo LOUD\necho DONE\n", False),
    ("after-a-multi-line-substitution-that-closes-a-case",
     "x=$(\ncase y in\n  y) echo hi ;;\nesac\n)#; echo LOUD\necho DONE\n",
     False),
    ("after-a-subshell-that-closes-a-case",
     "(case y in y) echo hi ;; esac)#; echo LOUD\necho DONE\n", True),
    ("after-a-substitution-closing-a-case-inside-a-loop",
     "x=$(for i in 1; do case $i in 1) echo hi;; esac; done)#; echo LOUD\n"
     "echo DONE\n", False),
]


class TestWhereAHashOpensAComment:
    """The opener rule itself, run against bash instead of remembered.

    `_COMMENT_OPENS_AFTER` and `_WordParens` are a model of one bash rule, and
    a model that lives only in the comment above it is the shape #120 is
    about. This one has already been wrong in the safe-looking direction more
    than once: `;`, `&`, `|` and `(` were missing outright, and then `)` was
    left out of the fix on the strength of a `$( )` example that says nothing
    about a `case` pattern. Each time the cost was a red on shell that runs
    exactly as it always did, with a message accusing another repo of a
    rename.

    So bash answers first, in a subprocess, and the scanners are held to that
    answer rather than to a paragraph.
    """

    @pytest.mark.parametrize(
        "name, script, opens", _COMMENT_OPENER_SCRIPTS,
        ids=[n for n, _, _ in _COMMENT_OPENER_SCRIPTS])
    def test_bash_opens_a_comment_only_after_a_paren_that_ends_a_word(
            self, tmp_path, name, script, opens):
        """What bash does with each script, observed on the machine running
        this - so the table cannot go on claiming a rule bash has stopped
        following, and cannot be corrected to match a scanner bug."""
        bash = require_bash()
        path = _script(tmp_path, script, name="opener.sh")
        syntax = subprocess.run([bash, "-n", path], capture_output=True,
                                text=True)
        assert syntax.returncode == 0, (
            f"the `{name}` script is not valid bash, so what bash prints for "
            f"it is about the script and not about the comment rule:"
            f"\n{syntax.stderr}"
        )
        proc = subprocess.run([bash, path], capture_output=True, text=True)
        assert "DONE" in proc.stdout, (
            f"the `{name}` script did not reach its end, so its stdout is not "
            f"evidence about the `#`:\n{proc.stdout}\n{proc.stderr}"
        )
        assert ("LOUD" not in proc.stdout) == opens, (
            f"bash disagrees with this table about `{name}`: the table says "
            f"the `#` {'opens' if opens else 'does not open'} a comment there, "
            f"and bash printed {proc.stdout!r}"
        )

    @pytest.mark.parametrize(
        "name, script, opens", _COMMENT_OPENER_SCRIPTS,
        ids=[n for n, _, _ in _COMMENT_OPENER_SCRIPTS])
    def test_this_files_scanners_agree_with_bash_about_where_a_comment_opens(
            self, name, script, opens):
        """`_shell_scan` AND `code()`, against the same table.

        Both, because every `case` assertion reads one and `code()` is what
        the workflow-text assertions read: a rule that drifted between them
        would leave one helper reading prose as code while the other read
        code as prose, and the two failures look nothing alike.

        `code()` rather than `_uncomment` on the line carrying LOUD, and the
        difference is not cosmetic. Slicing one line out asks the per-line
        helper a question with no body around it, so no entry in the table
        could ever express a construct that spans lines - and a `$( )` closing
        on a later line is exactly the construct the two scanners drifted on.
        """
        text, mask = _shell_scan(script)
        assert len(text) == len(script) and len(mask) == len(script), (
            "`_shell_scan` must return strings the same length as its input; "
            "every index the callers hand around depends on it"
        )
        assert ("LOUD" not in text) == opens, (
            f"`_shell_scan` read `{name}` the other way from bash: with a "
            f"comment {'opening' if opens else 'not opening'} there, LOUD "
            f"should be {'blanked' if opens else 'kept'} and it is not"
        )
        assert ("LOUD" not in code(script)) == opens, (
            f"`code()` read `{name}` the other way from bash, so it and the "
            f"`case` scanners no longer agree about this shape"
        )

    def test_every_opener_this_file_models_is_executed_by_the_table(self):
        """The coverage claim, asserted instead of stated.

        An opener modelled with no script here is one decided by argument,
        which is how `)` came to be half-right for a whole round.
        """
        covered = {script[script.index("#") - 1]
                   for _, script, _ in _COMMENT_OPENER_SCRIPTS
                   if script.index("#") > 0}
        missing = sorted(set(_COMMENT_OPENS_AFTER) - covered)
        assert not missing, (
            f"`_COMMENT_OPENS_AFTER` names these openers and no script above "
            f"runs one: {missing!r}"
        )
        assert any(s.startswith("#") for _, s, _ in _COMMENT_OPENER_SCRIPTS), (
            "`_WordParens.opens_comment` answers True at index 0 without "
            "consulting any character, and nothing above exercises it"
        )
        answers = {opens for _, script, opens in _COMMENT_OPENER_SCRIPTS
                   if script[script.index("#") - 1:script.index("#")] == ")"}
        assert answers == {True, False}, (
            "the `)` rule is stateful, so a table that runs only one of its "
            "two answers holds half of it - which is the half this file "
            "shipped before #120's second review"
        )
        table = {name: opens for name, _, opens in _COMMENT_OPENER_SCRIPTS}
        for shape, (fixture, ends_a_word) in _PAREN_SHAPES.items():
            assert fixture in table, (
                f"`_PAREN_SHAPES` says {shape} is run by `{fixture}`, and no "
                f"script above is called that. A `)` shape this file models "
                f"with no script is one decided by argument - which is how "
                f"the class docstring came to name shapes that nothing ran."
            )
            assert table[fixture] == ends_a_word, (
                f"`_PAREN_SHAPES` and `{fixture}` disagree about whether {shape} "
                f"ends a word, so one of the two is describing a different "
                f"script"
            )


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


class TestEveryFailableCommandInTheAuditStepIsGuarded:
    """The audit step's tolerance rule, parsed - #124.

    The rule was prose only. The paragraph above the step says no `echo`
    carries a redirect except the `$GITHUB_OUTPUT` write, and that every
    command that can plausibly fail sits in an `if` condition or a `||` list -
    the two constructs errexit is defined not to act on. Nothing checked
    either, so the next unguarded failable command was caught only if somebody
    happened to write a test for that command. `58cfab6` is what that costs:
    an unguarded `mkdir -p ../eval-results` aborted the step under `bash -e`,
    in the one branch a comment promised was never fatal.

    THE ERREXIT SUBTLETY A TEST WRITTEN FROM THE OLD COMMENT WOULD GET WRONG:
    a command on the RIGHT of the final `||` is NOT exempt. Errexit's
    exemption covers the command BEFORE the final `&&`/`||`; the one after it
    aborts the step exactly like a bare one. Executed here rather than
    restated - test_a_command_after_the_final_or_aborts_the_step_too runs both
    shapes in a real bash - because the whole classification below turns on
    it.

    Parsed rather than scanned: the body comes out of the workflow through
    `yaml`, its heredoc is blanked because it is Python, its comments are
    blanked by `_shell_scan`, and its operators are found in the mask so a
    `||` inside a `::warning::` is not one. AGENTS.md's rule for workflow
    lints, applied to the shell inside the workflow as well as to the YAML
    around it.
    """

    # THE CARVE-OUT LIST. Getting it wrong is worse than having no test - too
    # broad and it certifies the shipped file while holding nothing, too
    # narrow and it reds on a step that is correct. Each entry is a command
    # that cannot plausibly fail, or one this step leaves unguarded on purpose
    # with the reason written down:
    #
    #   `set -uo pipefail` - a builtin with valid options.
    #   a bare assignment - `harness_ok=yes`, `verdict="${verdict#...}"`.
    #     PARAMETER EXPANSION ONLY: an assignment whose right-hand side is a
    #     `$( )` takes that command's status and can abort the step, so it is
    #     deliberately NOT carved out. The step's own `verdict=$(python3 - ...)`
    #     and `drift=$(...)` are guarded by their `|| ...=""`, which is what
    #     this asymmetry exists to require.
    #     AND A SIMPLE COMMAND, for the same reason the `echo` below is:
    #     `_ASSIGNMENT` is anchored only at the start, so it matches the whole
    #     of `harness_ok=yes | mkdir ...` and of `harness_ok=yes & mkdir ...`.
    #     A pipeline takes the status of what it ends with and an `&` list
    #     takes the status of what follows the `&` - neither is an
    #     assignment - and both abort under `bash -e`, which
    #     test_the_assignment_carve_out_admits_only_a_simple_assignment runs.
    #   `:` - the no-op the quiet `case` arm is made of.
    #   an `echo` to stdout - an `echo` to a runner's stdout essentially never
    #     fails. THE COMMAND WORD HAS TO BE `echo` EXACTLY AND THE COMMAND
    #     HAS TO BE A SIMPLE ONE: `_headed_by` refuses `echofail --now`,
    #     whose name merely starts with those letters, and it refuses a
    #     PIPELINE headed by `echo`. `echo hi | grep -q zzz` under `bash -e`
    #     exits 1 and never reaches the next line - it aborts the step
    #     exactly like a bare `mkdir`, and a prefix test called it safe. An
    #     `echo` that REDIRECTS is a different command too and gets no
    #     carve-out here; the redirect rule below is what covers it.
    #   the `$GITHUB_OUTPUT` write - the one deliberately unguarded failable
    #     command in the step. There is no degraded path when the runner
    #     cannot write its own output file: aborting is the intended answer,
    #     and test_the_output_write_is_left_unguarded_on_purpose holds that it
    #     keeps no guard. A simple `echo` again, for the same two reasons: a
    #     pipeline ending in that redirect is not the write this describes.
    #   `{` and `}` - a brace group's punctuation, not a command.
    _STRUCTURE = re.compile(
        r"^(?:else|fi|done|do|then|esac|\{|\}|case\s+\S+\s+in)$")
    _ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
    _OUTPUT_WRITE = re.compile(r'^echo\s+.*>>\s*"\$GITHUB_OUTPUT"$')
    _COMPOUND_HEAD = re.compile(r"^(?:if|elif|while|until)\b")
    _CASE_OPEN = re.compile(r"^case\s+\S+\s+in$")
    _ARM_TERMINATORS = (";;&", ";;", ";&")

    # THE FOUR ANSWERS, AND THERE IS NO FIFTH. Every command this classifier
    # extracts leaves it carrying one of these names, and a construct it
    # cannot name raises instead of picking one. Three of the four EXEMPT a
    # command from the rule below, which is why none of them may be reached
    # by a heuristic: an exemption handed out by a rule of thumb is an
    # unguarded command the guard has stopped being able to see, and that is
    # a silent green rather than a red.
    _BUCKETS = ("guarded", "carved-out", "punctuation", "bare")

    # CONSTRUCTS THIS CLASSIFIER DOES NOT MODEL, RECOGNISED SO THEY RAISE.
    # A function definition is the one that matters: its body does not run
    # where it is written, so every answer above is the wrong answer for it -
    # `bare` reds with a diagnosis that is false, and any exemption hides a
    # body nothing here has read. Naming it is the only honest outcome.
    _UNMODELLED = (
        (re.compile(r"^(?:function\s|[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\))"),
         "a function definition, whose body does not run where it is written"),
    )

    @staticmethod
    def _one_command(mask):
        """True when `mask` holds no `|`, `&` or `;` - one command, not a list.

        Errexit's exemption is about a command's position in a list, so a
        carve-out that admits a whole pipeline or `&` list on the strength of
        its first word admits whatever that list ends with.
        """
        return not any(ch in mask for ch in "|&;")

    def _headed_by(self, command, name):
        """True when `command` is a SIMPLE command whose name is `name`.

        Both halves are what `command.startswith(name)` got wrong. The name
        has to be the whole first word, and the command has to be one command
        - see `_one_command`.
        """
        _, mask = _shell_scan(command)
        return self._one_command(mask) and command.split()[:1] == [name]

    def _carved_out(self, command):
        if command.startswith("{"):
            command = command[1:].strip()
        if command in ("", "}"):
            return True
        if self._STRUCTURE.match(command):
            return True
        if command in ("set -uo pipefail", ":"):
            return True
        _, mask = _shell_scan(command)
        redirects = any(ch in mask for ch in "<>")
        if (self._ASSIGNMENT.match(command) and self._one_command(mask)
                and "$(" not in mask and "`" not in mask and not redirects):
            return True
        if not self._headed_by(command, "echo"):
            return False
        if not redirects:
            return True
        return bool(self._OUTPUT_WRITE.match(command))

    @staticmethod
    def _pattern_end(mask):
        """Index just past the `)` closing a `case` pattern list, or None.

        A pattern list may be written `(a|b)` as well as `a|b)`, so a leading
        `(` is consumed rather than counted, and any other `(` before the
        pattern's own `)` is a construct this cannot read - which returns
        None and reds at the caller rather than guessing where the pattern
        stopped.
        """
        i, depth = 0, 0
        while i < len(mask) and mask[i].isspace():
            i += 1
        if i < len(mask) and mask[i] == "(":
            i += 1
        while i < len(mask):
            if mask[i] == "(":
                depth += 1
            elif mask[i] == ")":
                if depth == 0:
                    return i + 1
                depth -= 1
            i += 1
        return None

    def _classify_region(self, start, text, mask, classified):
        """One command region - a line, or an arm body after its pattern.

        Splits at the final `&&`/`||` and then at every top-level `;`, and
        gives each piece one of `_BUCKETS`. A `case` opener or an `esac`
        reaching here is a `case` written in a shape `_classified`'s state
        machine never saw, so it raises rather than being counted as
        punctuation by a regex that cannot tell which block it belongs to.
        """
        head = text.strip()
        if self._COMPOUND_HEAD.match(head):
            assert head.endswith("then") or head.endswith("do"), (
                f"line {start} of the audit step opens a compound command "
                f"and puts something after its `then`/`do` on the same "
                f"line; this classifier would read the whole line as a "
                f"condition and exempt it: {head!r}"
            )
            classified.append((start, head, "guarded"))
            return
        if self._STRUCTURE.match(head):
            classified.append((start, head, "punctuation"))
            return
        guards = _top_level(mask, ("||", "&&"))
        if guards:
            at, token = guards[-1]
            classified.append((start, text[:at].strip(), "guarded"))
            text, mask = text[at + len(token):], mask[at + len(token):]
        previous = 0
        for at, _token in _top_level(mask, (";",)) + [(len(mask), ";")]:
            segment = text[previous:at].strip()
            previous = at + 1
            if not segment:
                continue
            for shape, what in self._UNMODELLED:
                assert not shape.match(segment), (
                    f"line {start} of the audit step is {what}. This "
                    f"classifier has no answer for it, and every answer it "
                    f"does have would be wrong: {segment!r}"
                )
            assert not self._CASE_OPEN.match(segment) and segment != "esac", (
                f"line {start} of the audit step writes a `case` construct "
                f"inside another command, which the classifier's `case` "
                f"state machine never sees - so its arms would be read as "
                f"ordinary commands and its patterns as failures: "
                f"{segment!r}"
            )
            classified.append(
                (start, segment,
                 "carved-out" if self._carved_out(segment) else "bare"))

    def _classified(self):
        """Every command in the step as (first line, text, bucket).

        `bucket` is one of `_BUCKETS`, and getting there is POSITIVE
        throughout: a command is exempt because a recogniser said what it is,
        never because no recogniser objected. `guarded` is where errexit is
        defined not to act - an `if`/`elif`/`while`/`until` condition and the
        left of the final `&&`/`||`. `punctuation` is the shell's own
        skeleton, including a `case` pattern list. `carved-out` is the list
        above. Everything else is `bare`, and `bare` is what reds.

        THE `case` BLOCK IS PARSED, NOT SNIFFED, and that is this method's
        whole shape. The heuristic it replaces called a line punctuation
        whenever its masked text ended in `)` with more `)` than `(` - which
        is true of a one-line arm as well as of a pattern, so
        `zzz) drift=$(mkdir -p /proc/nope/c) ;;` was punctuation: guarded,
        read by no rule, and exempting exactly the assignment-from-`$( )`
        the carve-out list says is deliberately NOT carved out. Here a `case`
        opener pushes a state, a pattern list is consumed by `_pattern_end`
        and the REST OF THE LINE is classified as the arm body it is, an arm
        terminator returns the block to awaiting a pattern, and `esac` pops.
        Anything the machine cannot place - a pattern it cannot find the end
        of, an arm terminator or an `esac` outside a `case`, a `case` left
        open at the end of the step - raises, naming the text. An
        unrecognised construct must not become an exemption.
        """
        classified, cases = [], []
        for start, _, text, mask in _logical_lines(_audit_body()):
            ended_arm = None
            for terminator in self._ARM_TERMINATORS:
                if text.rstrip().endswith(terminator):
                    cut = len(text.rstrip()) - len(terminator)
                    text, mask, ended_arm = text[:cut], mask[:cut], terminator
                    break
            assert ended_arm is None or cases, (
                f"line {start} of the audit step ends an arm with "
                f"`{ended_arm}` and no `case` block is open, so the "
                f"classifier does not know what it terminates"
            )
            head = text.strip()
            if head == "esac":
                assert cases, (
                    f"line {start} of the audit step closes a `case` block "
                    f"that was never opened"
                )
                cases.pop()
                classified.append((start, head, "punctuation"))
                continue
            if cases and cases[-1]:
                end = self._pattern_end(mask)
                assert end is not None, (
                    f"line {start} of the audit step stands where a `case` "
                    f"pattern list must, and no `)` closes one there. The "
                    f"classifier cannot tell where the pattern stops and the "
                    f"arm body starts, so it can say nothing about either: "
                    f"{head!r}"
                )
                classified.append((start, text[:end].strip(), "punctuation"))
                text, mask = text[end:], mask[end:]
                cases[-1] = False
                head = text.strip()
            if self._CASE_OPEN.match(head):
                cases.append(True)
                classified.append((start, head, "punctuation"))
            elif head:
                self._classify_region(start, text, mask, classified)
            elif ended_arm is not None:
                classified.append((start, ended_arm, "punctuation"))
            if ended_arm is not None:
                cases[-1] = True
        assert not cases, (
            "the audit step opens a `case` block it never closes, so every "
            "line after it was classified as an arm of a block with no end"
        )
        return classified

    def test_the_classifier_reads_every_line_of_the_step(self):
        """A classifier that sees nothing certifies everything.

        Both rules below iterate what `_classified` returns, so a splitter
        that silently dropped the second half of the body would pass them
        while asserting nothing about it. This is the negative control: every
        line of the step that carries code has to be inside a command, and
        every command has to reach the classifier.
        """
        body = _audit_body()
        spans = _logical_lines(body)
        covered = set()
        for start, end, _text, _mask in spans:
            covered.update(range(start, end + 1))
        text, _ = _shell_scan(_strip_heredocs(body))
        for number, line in enumerate(text.split("\n"), 1):
            assert not line.strip() or number in covered, (
                f"line {number} of the audit step carries code and is in no "
                f"command, so nothing below classifies it: {line.strip()!r}"
            )
        assert {start for start, _, _, _ in spans} == {
            start for start, _, _ in self._classified()}, (
            "a command came out of `_logical_lines` and produced no "
            "classified segment, so the rules below never see it"
        )

    def _with_lines_before_the_case(self, extra):
        """The real body with `extra` inserted above `case "$verdict" in`.

        Above the `case` on purpose: it is the position that maximises what a
        mis-blanked tail would swallow, and it is where 58cfab6's `mkdir`
        actually sat.
        """
        real = _audit_body()
        lines = real.split("\n")
        at = [i for i, l in enumerate(lines) if 'case "$verdict" in' in l]
        assert len(at) == 1, (
            f"this control inserts above the audit step's "
            f"`case \"$verdict\" in`, and the body has {len(at)} of them"
        )
        pad = " " * (len(lines[at[0]]) - len(lines[at[0]].lstrip()))
        return "\n".join(
            lines[:at[0]] + [pad + l for l in extra] + lines[at[0]:])

    _HERE_STRING_LINES = [
        "if grep -q fresh <<<yes; then",
        "  echo matched",
        "fi",
    ]

    def test_a_here_string_does_not_blank_the_rest_of_the_step(
            self, monkeypatch, tmp_path):
        """The negative control for `_strip_heredocs`, run both ways.

        A guard that stops seeing the step does not red - it passes, having
        asserted nothing, which is #120's whole subject wearing this file's
        own clothes. `_strip_heredocs` read `<<<yes` as a heredoc opening on
        `yes`, found no line equal to `yes`, and blanked everything after it;
        the step's `case`, its `$GITHUB_OUTPUT` write and a bare `mkdir`
        spliced beside them all vanished from `_classified()`, and nothing in
        this file went red.

        `test_the_classifier_reads_every_line_of_the_step` cannot catch that
        and never could: its check is `not line.strip() or number in covered`
        against `_shell_scan(_strip_heredocs(body))`, and an over-blanked line
        IS blank. So the control has to be a command the guard must still
        find, not a count of lines the classifier admits it saw.

        Both directions, because only the pair says the fix is the right one.
        With the here-string alone the step is still clean - no false red -
        and the classifier still reaches the write on the last line. With a
        bare `mkdir` beside it the guard reds and names that `mkdir`.
        """
        bash = require_bash()
        clean = self._with_lines_before_the_case(self._HERE_STRING_LINES)
        armed = self._with_lines_before_the_case(
            self._HERE_STRING_LINES + ["mkdir -p ../scratch"])
        for name, body in (("clean", clean), ("armed", armed)):
            syntax = subprocess.run(
                [bash, "-n", _script(tmp_path, body, name=f"{name}.sh")],
                capture_output=True, text=True)
            assert syntax.returncode == 0, (
                f"the `{name}` control body is not valid bash, so what the "
                f"guard says about it is about the fixture:\n{syntax.stderr}"
            )

        monkeypatch.setitem(globals(), "_audit_body", lambda: clean)
        self.test_the_classifier_reads_every_line_of_the_step()
        self.test_every_command_that_can_fail_sits_in_an_if_or_an_or_list()
        assert any(self._OUTPUT_WRITE.match(command)
                   for _, command, _ in self._classified()), (
            "a here-string above the `case` cost the classifier the step's "
            "`$GITHUB_OUTPUT` write, so everything below it was blanked and "
            "the rules certified an empty set"
        )

        monkeypatch.setitem(globals(), "_audit_body", lambda: armed)
        with pytest.raises(AssertionError) as caught:
            self.test_every_command_that_can_fail_sits_in_an_if_or_an_or_list()
        assert "mkdir -p ../scratch" in str(caught.value), (
            f"the guard reded on the armed control, but not about the bare "
            f"`mkdir` this control adds: {caught.value}"
        )

    # TWO LINES THAT END IN A BACKSLASH, AND ONLY ONE OF THEM CONTINUES.
    # Both columns are what bash prints for the script beside them, measured
    # rather than reasoned: `echo one \\` is a command that prints a
    # backslash and ENDS, and `echo one \` is half of a command that runs on
    # into the next line.
    _BACKSLASH_PAIR = (
        ("an-escaped-backslash", "echo one \\\\\necho two\n",
         ["one \\", "two"], 2),
        ("a-line-continuation", "echo one \\\necho two\n",
         ["one echo two"], 1),
    )

    def test_an_escaped_backslash_does_not_continue_a_line(self, tmp_path):
        """The join that made an unguarded command disappear.

        `_logical_lines` used to ask the TEXT whether a line continues, and
        the text of both shapes above ends in a backslash. So `echo one \\\\`
        swallowed the line under it, the pair arrived at `_carved_out` as one
        command whose first word is `echo`, and the swallowed half was
        exempted without ever being classified - a silent green, not a red.
        Splicing `echo \\\\` and a bare `mkdir` into the real step left the
        whole module passing.

        Bash answers first, in a subprocess, because the difference between
        the two shapes is a bash rule and not a preference of this file.
        """
        bash = require_bash()
        for name, body, printed, commands in self._BACKSLASH_PAIR:
            proc = subprocess.run(
                [bash, _script(tmp_path, body, name="join.sh")],
                capture_output=True, text=True)
            assert proc.returncode == 0, (
                f"the `{name}` script did not run, so what bash printed for "
                f"it says nothing about the rule:\n{proc.stderr}"
            )
            assert proc.stdout.splitlines() == printed, (
                f"bash disagrees with this table about `{name}`: it printed "
                f"{proc.stdout!r}, and the table expects {printed!r}"
            )
            found = _logical_lines(body)
            assert len(found) == commands, (
                f"`_logical_lines` read `{name}` as {len(found)} command(s) "
                f"and bash ran it as {commands}: {found!r}. A command joined "
                f"onto another one is a command the guard below never "
                f"classifies."
            )

    # BASH'S TERMINATOR RULE, AND THE TWO WAYS THIS FILE USED TO BREAK IT.
    # A heredoc ends at the delimiter ALONE ON A LINE AT COLUMN 0; `<<-`
    # allows TABS before it and nothing else. `line.strip() == delim` ignored
    # both halves, so an indented `EOF` that bash hands over as DATA ended the
    # heredoc here and the lines after it came back as live shell.
    _HEREDOC_TERMINATORS = (
        ("an-indented-terminator-under-a-plain-heredoc",
         "cat <<EOF\nline1\n  EOF\nmkdir -p /proc/nope/child\nEOF\n"
         "echo AFTER\n",
         ["line1", "  EOF", "mkdir -p /proc/nope/child", "AFTER"],
         ["cat <<EOF", "echo AFTER"]),
        ("a-tab-indented-terminator-under-a-dash-heredoc",
         "cat <<-EOF\n\tline1\n\tEOF\necho AFTER\n",
         ["line1", "AFTER"],
         ["cat <<-EOF", "echo AFTER"]),
        ("a-space-indented-terminator-under-a-dash-heredoc",
         "cat <<-EOF\nline1\n  EOF\nmkdir -p /proc/nope/child\nEOF\n"
         "echo AFTER\n",
         ["line1", "  EOF", "mkdir -p /proc/nope/child", "AFTER"],
         ["cat <<-EOF", "echo AFTER"]),
    )

    def test_an_indented_terminator_does_not_end_a_heredoc(self, tmp_path):
        """The misreading `assert delim is None` cannot see.

        Ending a heredoc EARLY blanks less rather than more: no delimiter
        goes missing, so the assertion never fires, and the heredoc's own
        data comes back as live shell. In this step that is silent rather
        than loud - the un-blanked lines fold into the
        `verdict=$(python3 - <<'PY' ... ) || verdict=""` command, whose final
        `||` marks the whole blob guarded - so a command spliced into the
        Python heredoc under an indented `PY` was exempted and nothing reded.

        The fix is not another assertion; it is matching bash. Each row runs
        in a real bash first, and what bash treats as DATA this helper has to
        blank.
        """
        bash = require_bash()
        for name, body, printed, live in self._HEREDOC_TERMINATORS:
            proc = subprocess.run(
                [bash, _script(tmp_path, body, name="heredoc.sh")],
                capture_output=True, text=True)
            assert proc.returncode == 0, (
                f"the `{name}` script did not run, so what bash printed for "
                f"it says nothing about the rule:\n{proc.stderr}"
            )
            assert proc.stdout.splitlines() == printed, (
                f"bash disagrees with this table about `{name}`: it printed "
                f"{proc.stdout!r}, and the table expects {printed!r}"
            )
            kept = [l for l in _strip_heredocs(body).split("\n") if l.strip()]
            assert kept == live, (
                f"`_strip_heredocs` read `{name}` the other way from bash. "
                f"It left {kept!r} as shell, and bash ran only {live!r} - "
                f"everything else was heredoc data."
            )

    def test_no_echo_in_this_step_redirects_except_the_output_write(self):
        """Redirection is what makes an `echo` failable.

        An `echo` to a runner's stdout essentially never fails, which is why
        the step leaves every one of them unguarded. An `echo` to a FILE or a
        closed descriptor is an ordinary failable command, and one added in a
        bare position would abort the step in whichever branch it sits in -
        found only on the day that branch runs.
        """
        for line, command, _bucket in self._classified():
            if command.split()[:1] != ["echo"]:
                continue
            _, mask = _shell_scan(command)
            if not any(ch in mask for ch in "<>"):
                continue
            assert self._OUTPUT_WRITE.match(command), (
                f"line {line} of the audit step has an `echo` that redirects "
                f"and is not the `$GITHUB_OUTPUT` write, so it can fail and "
                f"abort the step: {command!r}. Put it in an `if` condition or "
                f"a `||` list, or explain it in the carve-out list above."
            )

    _NOT_A_SIMPLE_ECHO = (
        "echo hi | grep -q zzz",
        "echofail --now",
    )

    def test_the_echo_carve_out_admits_only_a_simple_echo(
            self, monkeypatch, tmp_path):
        """The shapes `command.startswith("echo")` let through.

        A carve-out too broad certifies the shipped file while holding
        nothing, which the list above says in its own first sentence. A
        prefix match is exactly that: it admitted an entire PIPELINE headed
        by `echo`, whose exit status is the LAST command's, and it admitted
        any command whose name merely begins with those four letters.

        Each is run under `bash -e` first, so what makes it dangerous is
        measured here rather than argued - both abort before the next line,
        which is what a bare `mkdir` does and what the whole rule exists to
        stop. Then each is spliced into the step and the guard has to name
        it.
        """
        bash = require_bash()
        # Every body built BEFORE anything is monkeypatched, or the second
        # one would be built from a step that already carries the first.
        armed_bodies = {c: self._with_lines_before_the_case([c])
                        for c in self._NOT_A_SIMPLE_ECHO}
        for command, armed in armed_bodies.items():
            script = f"set -uo pipefail\n{command}\necho reached\n"
            proc = subprocess.run(
                [bash, "-e", _script(tmp_path, script, name="carve.sh")],
                capture_output=True, text=True)
            assert proc.returncode != 0 and "reached" not in proc.stdout, (
                f"`{command}` no longer aborts under `bash -e`, so this case "
                f"proves nothing about the carve-out: {proc.returncode}, "
                f"{proc.stdout!r}"
            )
            syntax = subprocess.run(
                [bash, "-n", _script(tmp_path, armed, name="carve-body.sh")],
                capture_output=True, text=True)
            assert syntax.returncode == 0, (
                f"the body carrying `{command}` is not valid bash, so what "
                f"the guard says about it is about the fixture:"
                f"\n{syntax.stderr}"
            )
            monkeypatch.setitem(globals(), "_audit_body", lambda b=armed: b)
            with pytest.raises(AssertionError) as caught:
                self.\
                    test_every_command_that_can_fail_sits_in_an_if_or_an_or_list()
            assert command in str(caught.value), (
                f"the guard reded on a step carrying `{command}`, but about "
                f"something else: {caught.value}"
            )

    _NOT_A_SIMPLE_ASSIGNMENT = (
        "harness_ok=yes | mkdir -p /proc/nope/child",
        "harness_ok=yes & mkdir -p /proc/nope/child",
    )

    def test_the_assignment_carve_out_admits_only_a_simple_assignment(
            self, monkeypatch, tmp_path):
        """`_headed_by`'s lesson, applied to the branch beside it.

        `_ASSIGNMENT` is anchored at the start and says nothing about what
        follows, so `harness_ok=yes | mkdir ...` matched it whole and the
        carve-out admitted the pipeline on the strength of its first word -
        the same defect the `echo` branch had, in the same method, left
        standing when that one was fixed. A pipeline takes the status of what
        it ends with and an `&` list takes the status of what follows the
        `&`, so neither is the assignment the carve-out list describes.

        Run under `bash -e` first, so what makes each dangerous is measured
        rather than argued, then spliced into the step so the guard has to
        name it.
        """
        bash = require_bash()
        # Every body built BEFORE anything is monkeypatched, or the second
        # one would be built from a step that already carries the first.
        armed_bodies = {c: self._with_lines_before_the_case([c])
                        for c in self._NOT_A_SIMPLE_ASSIGNMENT}
        for command, armed in armed_bodies.items():
            script = f"set -uo pipefail\n{command}\necho reached\n"
            proc = subprocess.run(
                [bash, "-e", _script(tmp_path, script, name="assign.sh")],
                capture_output=True, text=True)
            assert proc.returncode != 0 and "reached" not in proc.stdout, (
                f"`{command}` no longer aborts under `bash -e`, so this case "
                f"proves nothing about the carve-out: {proc.returncode}, "
                f"{proc.stdout!r}"
            )
            syntax = subprocess.run(
                [bash, "-n", _script(tmp_path, armed, name="assign-body.sh")],
                capture_output=True, text=True)
            assert syntax.returncode == 0, (
                f"the body carrying `{command}` is not valid bash, so what "
                f"the guard says about it is about the fixture:"
                f"\n{syntax.stderr}"
            )
            monkeypatch.setitem(globals(), "_audit_body", lambda b=armed: b)
            with pytest.raises(AssertionError) as caught:
                self.\
                    test_every_command_that_can_fail_sits_in_an_if_or_an_or_list()
            assert command in str(caught.value), (
                f"the guard reded on a step carrying `{command}`, but about "
                f"something else: {caught.value}"
            )

    def test_every_command_gets_one_of_the_four_answers(self):
        """The vocabulary is closed, and closed is the whole point.

        Three of `_BUCKETS` exempt a command from the rule below. A fourth
        answer invented in passing - or a bucket name misspelled at one
        `append` - would exempt whatever carries it, because the rule tests
        for `bare` and everything that is not `bare` walks free.
        """
        for line, command, bucket in self._classified():
            assert bucket in self._BUCKETS, (
                f"line {line} of the audit step was classified `{bucket}`, "
                f"which is not one of {self._BUCKETS}: {command!r}"
            )

    # A MENU OF CONSTRUCTS THIS CLASSIFIER DOES NOT MODEL, each with the text
    # its failure has to name. Three of these were live silent exemptions -
    # the escaped backslash, the assignment-headed pipeline and the one-line
    # `case` arm - and the rest are the neighbouring shapes a reader would
    # reasonably wonder about. The requirement is the same for all of them
    # and it is not "handled": each has to be classified as the failable
    # unguarded command it is, or to RAISE naming itself. What none of them
    # may do is pass.
    _UNMODELLED_CONSTRUCTS = (
        ("an-escaped-backslash-before-a-newline",
         ["echo \\\\", "mkdir -p ../scratch"], "mkdir -p ../scratch"),
        ("an-assignment-headed-pipeline",
         ["harness_ok=yes | mkdir -p ../scratch"], "mkdir -p ../scratch"),
        ("a-one-line-case-arm",
         ["case zzz in", "  zzz) drift=$(mkdir -p ../scratch) ;;", "esac"],
         "mkdir -p ../scratch"),
        ("a-fallthrough-case-arm",
         ["case zzz in", "  zzz) mkdir -p ../scratch ;;&", "  *) : ;;",
          "esac"], "mkdir -p ../scratch"),
        ("an-arithmetic-command", ["(( 0 ))"], "(( 0 ))"),
        ("a-double-bracket-with-a-quoted-or",
         ['[[ "x" == "a||b" ]]'], '[[ "x" == "a||b" ]]'),
        ("a-background-list",
         ["harness_ok=yes & mkdir -p ../scratch"], "mkdir -p ../scratch"),
        ("a-function-definition",
         ["zzz() { mkdir -p ../scratch; }"], "a function definition"),
    )

    def test_a_construct_this_classifier_cannot_place_is_never_a_pass(
            self, monkeypatch, tmp_path):
        """The negative control for the classification itself.

        A guard whose parser mis-reads a construct does not red - it hands
        out an exemption, which is #120's subject wearing this file's
        clothes. All three blocking faults of this round had that shape: a
        line joined onto the one above it, a pipeline admitted as its
        leading assignment, and a whole `case` arm called punctuation
        because its masked text ended in `)`. Each left the shipped step
        green with an unguarded command inside it.

        So every row is spliced into the real step and the guard has to
        FAIL - either by naming the command as unguarded, or by refusing to
        classify the construct at all. The two are one assertion on purpose:
        which of them is right is a judgement about the construct, and the
        thing that must never happen is neither.
        """
        bash = require_bash()
        # Every body built BEFORE anything is monkeypatched, or the second
        # one would be built from a step that already carries the first.
        armed = {name: (self._with_lines_before_the_case(lines), names)
                 for name, lines, names in self._UNMODELLED_CONSTRUCTS}
        for name, (body, names) in armed.items():
            syntax = subprocess.run(
                [bash, "-n", _script(tmp_path, body, name="menu.sh")],
                capture_output=True, text=True)
            assert syntax.returncode == 0, (
                f"the body carrying `{name}` is not valid bash, so what the "
                f"guard says about it is about the fixture:\n{syntax.stderr}"
            )
            monkeypatch.setitem(globals(), "_audit_body", lambda b=body: b)
            with pytest.raises(AssertionError) as caught:
                self.\
                    test_every_command_that_can_fail_sits_in_an_if_or_an_or_list()
            assert names in str(caught.value), (
                f"the guard reded on a step carrying `{name}`, but not about "
                f"{names!r}: {caught.value}"
            )

    def test_every_command_that_can_fail_sits_in_an_if_or_an_or_list(self):
        """The rule the tolerance paragraph states and nothing held.

        `if` and `||` are the two constructs errexit does not act on, and the
        carve-out list above is what says which bare commands are safe anyway.
        A command that is neither reds here rather than at 06:23 UTC.
        """
        for line, command, bucket in self._classified():
            assert bucket != "bare", (
                f"line {line} of the audit step runs a command that can fail "
                f"and is neither in an `if` condition nor in a `||` list: "
                f"{command!r}. Under `bash -e` that aborts the whole step - "
                f"the fault 58cfab6 fixed. Guard it, or add it to the "
                f"carve-out list above with the reason it cannot fail."
            )

    def test_a_command_after_the_final_or_aborts_the_step_too(self, tmp_path):
        """The errexit rule this classification turns on, executed.

        An earlier version of the tolerance comment had it the other way and
        called two `echo`s protected because a `||` sat to their left. Errexit
        exempts the command BEFORE the final `&&`/`||`; the one after it is an
        ordinary command. A test written from the old reading would exempt
        exactly the wrong half, so the rule is run rather than cited.
        """
        bash = require_bash()
        results = {}
        for name, script in {
            "after-a-||": 'false || echo hi >&-\necho reached\n',
            "bare": 'echo hi >&-\necho reached\n',
            "before-a-||": 'echo hi >&- || true\necho reached\n',
        }.items():
            proc = subprocess.run(
                [bash, "-e", _script(tmp_path, script, name="errexit.sh")],
                capture_output=True, text=True)
            results[name] = (proc.returncode, "reached" in proc.stdout)
        assert results["after-a-||"] == results["bare"], (
            f"a failing command after the final `||` no longer behaves like a "
            f"bare one, so the classifier above exempts the wrong half: "
            f"{results}"
        )
        assert results["bare"][0] != 0 and not results["bare"][1], results
        assert results["before-a-||"] == (0, True), (
            f"a failing command BEFORE the final `||` aborted the script, so "
            f"the exemption this classifier grants is not real: {results}"
        )


@pytest.fixture(scope="session")
def control_answers(tmp_path_factory):
    """`_ARMS_BASE`'s own answers, computed once for the whole session.

    test_a_reformat_leaves_the_step_answering_as_it_did compares every fixture
    against this, and the control does not depend on the fixture - so
    recomputing it per parametrization spawns a bash per verdict per shape and
    doubles the cost of the slowest test in this file for no extra coverage.

    `pytest.MonkeyPatch.context()` rather than the `monkeypatch` fixture,
    which is function-scoped and cannot be requested from here.
    """
    with pytest.MonkeyPatch.context() as mp:
        return TestTheAuditStepAnnouncesEveryDegradedVerdict()._decisions(
            tmp_path_factory.mktemp("control"), mp, _ARMS_BASE)


class TestTheAuditStepAnnouncesEveryDegradedVerdict:
    """The audit step's own tail, EXECUTED - not string-matched.

    THE FAILURE THIS LOCKS OUT IS A GREEN RUN THAT ASKED NOTHING. Every other
    failure in that step (both clones, the pyyaml install) raises a
    `::warning::`; THREE paths failed OPEN, and each has its own test below.

    THREE, not two - the count is maintained here because a reader auditing
    whether every failed-open path has a test counts what this sentence says,
    finds that many enumerated, and stops. It said TWO after a third had been
    added with its own test, which is how the third one - the gated one, and
    the one whose premise most needed a second look - would have escaped it.
    An unmaintained label does not stay silent; it starts lying.

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

    The third is quieter again, because the verdict is one this repo HAS a
    name for: `not-yet-bootstrapped`, which is what `freshness_verdict`
    answers when skills-evals moves the published ARTIFACT PATH rather than a
    module, a function or a fixture key. Both clones succeed and the import
    succeeds, so neither guard above can see it, and the `case` sent it to the
    quiet arm. It is the one of the three whose annotation is CONDITIONAL -
    quiet is the right answer before anything has ever been published - so it
    carries two tests, one for each side of that gate.

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
        body = self._body()
        marker = ') || verdict=""'
        # THE SAME BOUNDARY RULE AS THE TWO SCANNERS BELOW, and this one
        # carries the most. Every verdict test in this class executes whatever
        # this returns, so a comment that ever quotes the marker verbatim
        # would relocate the slice and leave those tests passing against a
        # region that is not the step. Measured with a decoy comment spliced
        # above the real marker: `body.index` returns a slice starting inside
        # the comment, this returns the real tail unchanged. See
        # test_the_tail_slice_is_not_relocated_by_a_comment.
        text, mask = _shell_scan(body)
        return body[_code_index(
            text, mask, marker,
            "the empty-capture fallback changed shape; this test no longer "
            "knows where the step's verdict handling starts",
        ) + len(marker):]

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
    # The `git` double answers on the DESTINATION argument - the last word of
    # either command line, and the one part of it that says WHICH clone is
    # being asked for. The flags differ between the two as well
    # (`--filter=blob:none` against `--single-branch --branch eval-results`),
    # which is exactly why the double does not key on them: they are the half
    # most likely to be edited. It creates the tree on
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
        """The arm patterns of the audit step's `case`, read off a mask.

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

        WHAT THIS IS AND IS NOT: quote-aware, comment-free and
        nesting-counted, and NOT a bash parser. It knows that a `;;`, an
        `esac` or a `|` inside a string or a comment is data, and that an
        `esac` inside a nested `case` closes the nested one. It does not know
        heredocs, extglob, or an `esac` reached through a variable, and NONE
        of those is reliably refused - an extglob arm is misread as two
        literals and a heredoc desync is often invisible. `_shell_scan`'s
        docstring says what each actually does; do not read "outside the
        model" as "caught".
        """
        body = self._body()
        text, mask = _shell_scan(body)
        # THE OPENER NO LONGER HAS TO BE A LINE OF ITS OWN. That anchor
        # existed to stop `body.index` from following a COMMENT that quotes
        # the opener verbatim, and comments are gone by here; the mask covers
        # the other half, a quoted occurrence. What the anchor ALSO did was
        # reject `case "$verdict" in <first arm>)` on one line - the form
        # record-account-upload.yml ships at both of its own guards - with
        # "the opener is no longer a line of its own", which is a red on a
        # reformat that changes nothing bash can see.
        _, spans, _ = _case_block(
            text, mask, _CASE_VERDICT,
            "the audit step's `case \"$verdict\" in` is no longer a command "
            "this test can find; it no longer knows which block it is reading",
        )
        literals, variables = set(), set()
        for begin, end in spans:
            arm, armmask = text[begin:end], mask[begin:end]
            if not arm.strip():
                continue
            # The FIRST UNQUOTED `)`, off the mask: a `)` inside a quoted arm
            # body is not where the pattern ends.
            cut = armmask.find(")")
            assert cut != -1, (
                "an arm of the audit step's `case` has no pattern this test "
                f"can read, so its statuses were counted as absent:\n"
                f"{arm.strip()}"
            )
            pattern, patternmask = arm[:cut], armmask[:cut]
            bars = [k for k, ch in enumerate(patternmask) if ch == "|"]
            prev = 0
            for k in bars + [len(pattern)]:
                token = pattern[prev:k].strip()
                prev = k + 1
                # A POSIX leading `(` is punctuation, not part of the first
                # pattern: `(fresh|unavailable|"$drift")` names `fresh`.
                if token.startswith("("):
                    token = token[1:].strip()
                # QUOTES ARE STRIPPED FROM A LITERAL AND KEPT ON AN EXPANSION,
                # and the asymmetry is the point. `"fresh"` and `fresh` are the
                # same pattern to bash, so the set comparison must not tell
                # them apart; `"$drift"` and `$drift` are NOT the same pattern,
                # and the assertion below exists to require the quoted one.
                #
                # WRITE THIS AS AN EXPLICIT if/else. The one-liner
                # `(variables if "$" in token else literals).add(_unquote(token))`
                # reads as a faithful port and unquotes `"$drift"` to `$drift`,
                # which reds every shape including the control, with a message
                # about the quiet arm that points nowhere near the cause.
                if "$" in token:
                    variables.add(token)
                    continue
                # EVERY SPELLING OF THE CATCH-ALL COUNTS AS ONE, and a
                # pattern that matches only the empty string counts as
                # nothing - see `_pattern_payload`. Reading `''|*)` as a
                # literal named "" was a red on a block that matches exactly
                # what `*)` matches, and the message it reds with sends the
                # reader to diff this workflow against another repo.
                # `payload`, not `text`: `text` is the whole masked body
                # this loop is slicing arms out of.
                payload, star = _pattern_payload(token)
                if payload == "" and star:
                    literals.add("*")
                elif payload == "":
                    continue
                else:
                    literals.add(_unquote(token))
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

    # SPELLED-OUT AND DIGIT COUNTS ALIKE, because the one that came back was
    # spelled out ("Twelve other `echo` commands").
    _COUNT_WORD = (r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
                   r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
                   r"seventeen|eighteen|nineteen|twenty)")

    def test_an_unbalanced_quote_inside_a_heredoc_is_not_reliably_loud(self):
        """`_shell_scan`'s stated limitation, executed rather than promised.

        The docstring used to say a line leaving a shell quote open inside the
        Python heredoc "swallows the rest of the body" and that the helper
        "RAISES ... rather than returning a plausible slice of the wrong
        region". Neither half held: swept over the whole step body, most
        splice positions resynchronise before the `case` and pass silently,
        and at least one reds with the cross-repo drift accusation instead.
        Reading that paragraph, an author who put such a line in the heredoc
        would expect to be told; they would not be.

        SO BOTH DIRECTIONS ARE ASSERTED, and the point of asserting both is
        that neither may be written up as the only one. If someone teaches
        this scanner heredocs, the quiet half goes to zero and this test reds
        - at which point the paragraph is the thing to update, not the
        assertion.

        A SWEEP RATHER THAN ONE POSITION, because a position number in the
        step body is the most stale-prone thing this file could pin: any line
        added above the heredoc moves it.
        """
        body = _audit_body()
        lines = body.splitlines()
        quiet = loud = 0
        for pos in range(len(lines) + 1):
            spliced = "\n".join(
                lines[:pos] + ['x = "unterminated'] + lines[pos:]) + "\n"
            text, mask = _shell_scan(spliced)
            try:
                _case_block(text, mask, _CASE_VERDICT, "opener not found")
            except AssertionError:
                loud += 1
                continue
            quiet += 1
        assert loud, (
            "an unbalanced quote spliced anywhere in the step body now always "
            "leaves the `case` findable. That is a better scanner than the "
            "one `_shell_scan`'s docstring describes - update the docstring."
        )
        assert quiet, (
            "an unbalanced quote spliced anywhere in the step body now always "
            "raises. That is the behaviour `_shell_scan`'s docstring used to "
            "claim and did not have; if it is true now, say so there instead "
            "of hedging."
        )

    def test_an_extglob_arm_is_misread_rather_than_refused(self):
        """The other half of the same correction.

        The docstring claimed extglob "fails the same loud way". It does not:
        `@(stale|missing)` is split at the `|` like any other alternation, so
        the scan returns the tokens `@(stale` and `missing` and the caller
        reds about account_zip_selection.py having renamed a status - the
        expensive false red this whole file exists to remove, arrived at
        through a shape the docstring said was caught.

        Pinned so the prose cannot drift back. Teaching `_case_statuses`
        extglob would red this, and the fix then is to say so in
        `_shell_scan`.
        """
        body = (
            "shopt -s extglob\n"
            'case "$verdict" in\n'
            "  @(stale|missing)|unreadable) : ;;\n"
            '  fresh|unavailable|"$drift") : ;;\n'
            "  *) : ;;\n"
            "esac\n"
        )
        text, mask = _shell_scan(body)
        _, spans, _ = _case_block(text, mask, _CASE_VERDICT, "opener not found")
        tokens = set()
        for begin, end in spans:
            arm, armmask = text[begin:end], mask[begin:end]
            if not arm.strip():
                continue
            cut = armmask.find(")")
            assert cut != -1, arm
            tokens.update(tok.strip() for tok in arm[:cut].split("|"))
        assert "@(stale" in tokens, (
            f"an extglob arm is no longer split into literal tokens: {tokens}. "
            f"If `_case_statuses` learned extglob, `_shell_scan`'s docstring "
            f"still tells the reader it is misread - correct it there."
        )
        assert "stale" not in tokens, (
            f"the scan recovered `stale` from an extglob arm: {tokens}. Same "
            f"as above - the docstring is what needs correcting."
        )

    def test_no_comment_in_this_step_counts_its_own_echoes(self):
        """#120, guarded rather than described.

        The defect this workflow was fixed for was a comment saying how many
        `echo` commands the step had. It was accurate when it was written and
        nothing asserted it, so it read as checked and would have gone stale
        on the next edit - and an accurate unasserted count is the dangerous
        kind, because nobody has a reason to look at it.

        The paragraph that used to carry it now argues for a SHAPE instead.
        This is what stops the count coming back the next time someone wants
        the paragraph to sound more precise: a number qualifying `echo` in
        this file has to be asserted somewhere, and there is nowhere here for
        it to be asserted from.
        """
        text = ZIPS.read_text(encoding="utf-8")
        hits = re.findall(
            rf"(?i)\b{self._COUNT_WORD}\b[^.\n]{{0,40}}`?echo`?s?\b", text)
        assert not hits, (
            f"a count of this step's `echo` commands came back into a "
            f"comment: {hits}. Nothing in this repo can assert it, so it "
            f"reads as checked and goes stale on the next edit - see #120."
        )

    def test_the_output_write_is_left_unguarded_on_purpose(self):
        """The one command the tolerance comment exempts, held to the exemption.

        That comment names `echo "status=$verdict" >> "$GITHUB_OUTPUT"` as
        deliberately unguarded: there is no degraded path when the runner
        cannot write its own output file, because the next step would read an
        absent status and normalise it to `unavailable` - "we could not ask"
        reported as "we asked and it passed". Aborting is the honest answer.

        It also closes with a rule about what belongs in an `if` or a `||`,
        and an editor reading that rule alone would wrap this line and undo
        the decision. So the decision is asserted here rather than argued
        there: adding `|| true`, or moving the write into an `if` condition,
        reds this test with the reason.
        """
        step = code(_audit_body())
        writes = [l.strip() for l in step.splitlines()
                  if "$GITHUB_OUTPUT" in l]
        assert len(writes) == 1, (
            f"expected exactly one $GITHUB_OUTPUT write in the audit step, "
            f"found {len(writes)}: {writes}"
        )
        write = writes[0]
        assert write.startswith("echo "), (
            f"the audit step's output write is no longer a bare `echo`, so "
            f"this test no longer knows whether it is guarded: {write!r}"
        )
        # THE PROPERTY, NOT THE TEXT. Matching the line byte for byte would
        # red on a requoting bash cannot see, which is the false red the rest
        # of this file exists to remove. What must not appear is a GUARD.
        assert not re.search(r"\|\||&&", write), (
            f"the `status=` write acquired a guard: {write!r}. There is no "
            f"degraded path when the runner cannot write its own output file "
            f"- the next step would read an absent status as `unavailable`, "
            f"which is 'we could not ask' reported as 'we asked and it "
            f"passed'. Aborting is the intended answer; see the tolerance "
            f"comment above the step."
        )

    def _bash_n(self, tmp_path, body, name):
        """The fixture is REAL BASH, asserted before anything is read off it.

        Without this a typo in a fixture below looks exactly like a helper
        bug: the scanner reports "no closing `esac`" about a block that has no
        closing `esac` because the fixture forgot one, and the next reader
        goes looking for the defect in `_shell_scan`.
        """
        bash = require_bash()
        path = _script(tmp_path, body, name="fixture.sh")
        proc = subprocess.run([bash, "-n", path], capture_output=True,
                              text=True)
        assert proc.returncode == 0, (
            f"the `{name}` fixture is not valid bash, so whatever the "
            f"scanner says about it is about the fixture:\n{proc.stderr}"
        )

    # THE FIXTURES THAT DELIBERATELY CHANGE WHAT THE BLOCK DOES, and there
    # is no way to write them so they do not. `;;&` goes on testing patterns
    # after a match and `;&` runs the next arm's body, so an arm with any arm
    # after it cannot carry either terminator and still behave like `;;`. On
    # the LAST arm they would behave like `;;` and stop discriminating: a
    # scanner blind to `;&` reads a block whose only `;&` is the last
    # terminator perfectly well, and reds only when the arm below it gets
    # swallowed. They are held for what the SCANNER must do with them rather
    # than for what the shell does, and naming them here is what keeps the
    # certificate the rest of the set carries off them.
    _ARMS_THAT_CHANGE_WHAT_THE_STEP_DOES = {
        "continue-testing-terminator",
        "fallthrough-terminator",
    }

    # Every status the module knows, the one it uses for "could not ask", and
    # one it has never heard of - which is the arm most of these fixtures move
    # around.
    _DECISION_VERDICTS = (
        sorted(sel.KNOWN_STATUSES | {sel.UNAVAILABLE}) + ["brand-new"]
    )

    def _decisions(self, tmp_path, monkeypatch, block):
        """What the step ANSWERS, for every verdict crossed with `results_ok`.

        A dict rather than a diff, so a failure names the verdict that moved.
        Each run gets its own directory because the step APPENDS to
        $GITHUB_OUTPUT, and a shared one would compare a growing file.
        """
        body = _splice(block)
        monkeypatch.setattr(type(self), "_body", lambda self: body)
        answers = {}
        for verdict in self._DECISION_VERDICTS:
            for results_ok in ("yes", "no"):
                run_dir = tmp_path / f"{verdict}-{results_ok}".replace("/", "_")
                run_dir.mkdir(parents=True, exist_ok=True)
                log, written = self._run(run_dir, verdict,
                                         results_ok=results_ok)
                answers[(verdict, results_ok)] = (
                    log.count("::warning::"), written)
        return answers

    @pytest.mark.parametrize("name, block", _ARMS_OK,
                             ids=[n for n, _ in _ARMS_OK])
    def test_a_reformat_leaves_the_step_answering_as_it_did(
            self, tmp_path, monkeypatch, control_answers, name, block):
        """The certificate `_ARMS_OK` carries, EXECUTED rather than asserted
        in its banner.

        `bash -n` was the only gate this set had, and `bash -n` cannot see a
        behaviour change. So the banner's "says exactly what the shipped block
        says" was free to be false, and was: `;;&` on the quiet arm made
        `fresh` annotate, which is precisely the failure the shipped block is
        written to avoid. A fixture that changes what the step does, sitting in
        a set whose whole job is to certify that a scanner may ignore
        everything in it, is how a real behaviour change gets certified as
        invisible.

        BOTH DIRECTIONS, because a list of exceptions rots the same way a
        count does. A fixture named in `_ARMS_THAT_CHANGE_WHAT_THE_STEP_DOES`
        must actually diverge, or the exemption is covering nothing and the
        next reader trusts it.

        WHAT THIS DOES NOT HOLD, so nobody reads more into it: the warning
        TEXT, which several shapes edit on purpose, and any output that is not
        an annotation. Two blocks that swapped the bodies of two arms would
        agree here. What it holds is the pair a run page shows - how many
        annotations were raised and what status went downstream.
        """
        actual = self._decisions(tmp_path / "fixture", monkeypatch, block)
        control = control_answers
        if name in self._ARMS_THAT_CHANGE_WHAT_THE_STEP_DOES:
            assert actual != control, (
                f"`{name}` is exempted from this certificate as a fixture "
                f"that changes what the step does, and it no longer does. "
                f"Take it out of `_ARMS_THAT_CHANGE_WHAT_THE_STEP_DOES` - an "
                f"exemption covering nothing is read as covering something."
            )
            return
        moved = {k: (control[k], actual[k]) for k in control
                 if control[k] != actual[k]}
        assert not moved, (
            f"the `{name}` fixture is certified as a reformat this file "
            f"cannot see, and it changes what the step ANSWERS. (verdict, "
            f"results_ok) -> (control, fixture), as (annotations, "
            f"$GITHUB_OUTPUT): {moved}"
        )

    @pytest.mark.parametrize("name, block", _ARMS_OK,
                             ids=[n for n, _ in _ARMS_OK])
    def test_a_reformat_bash_cannot_see_does_not_red_this_test(
            self, tmp_path, monkeypatch, name, block):
        """A REFORMAT MUST NOT READ AS A CROSS-REPO VOCABULARY DRIFT.

        Every block here has the same arms, the same patterns and the same
        quoted expansion as the shipped one, and - bar the terminator shapes
        named in `_ARMS_THAT_CHANGE_WHAT_THE_STEP_DOES` - makes the step
        answer identically; test_a_reformat_leaves_the_step_answering_as_it_did
        is what holds that half. Many of them red on a scanner that reads the
        block as TEXT, and the message it reds with says the `case` and
        account_zip_selection.py disagree about which statuses exist - which
        sends the reader to diff two files over a wrapped line.

        The `bash -n` gate first, so a broken fixture cannot masquerade as a
        broken helper.
        """
        body = _splice(block)
        self._bash_n(tmp_path, body, name)
        monkeypatch.setattr(type(self), "_body", lambda self: body)
        self.test_the_case_block_names_every_status_the_module_knows()

    @pytest.mark.parametrize("name, block", _ARMS_RED,
                             ids=[n for n, _ in _ARMS_RED])
    def test_the_shapes_that_must_still_red_still_red(
            self, tmp_path, monkeypatch, name, block):
        """The other half, without which the set above is a licence to accept.

        A scanner that admits every reformat by admitting everything would
        pass every case above and hold nothing. The ones here are real
        divergences and must still fail.

        The UNQUOTED `$drift` case is the sharpest of them. The helper strips
        quotes from a LITERAL token, so this proves it does NOT strip them
        from an expansion - and the difference is behaviour, not style: an
        unquoted expansion is re-read as a pattern, so a glob in the module's
        constant would silence every verdict this step exists to annotate.
        """
        body = _splice(block)
        self._bash_n(tmp_path, body, name)
        monkeypatch.setattr(type(self), "_body", lambda self: body)
        with pytest.raises(AssertionError):
            self.test_the_case_block_names_every_status_the_module_knows()

    @pytest.mark.parametrize("name, reformat", _BODY_REFORMATS,
                             ids=[n for n, _ in _BODY_REFORMATS])
    def test_the_fixture_builder_reads_the_case_the_way_the_scanner_does(
            self, tmp_path, monkeypatch, name, reformat):
        """`_splice` may not be pickier about shape than the scanner it feeds.

        The set above certifies that reformatting the `case` block changes
        nothing this test can see. That certificate is worthless if applying
        the same reformat to the SHIPPED file breaks the harness instead of
        the scanner - the red just moves, and it moves somewhere with a worse
        message, because a `ValueError` naming a string says nothing about the
        workflow.

        So the reformats here are applied to the real step body and `_splice`
        has to go on locating the block. It found the block by `lines.index`
        once, which required the opener and the `esac` to be lines reading
        exactly that text; every case here defeats that and none of them
        defeats bash.
        """
        real = _audit_body()
        moved = reformat(real)
        assert moved != real, (
            f"the `{name}` reformat left the body unchanged, so this case "
            f"proves nothing about `_splice`"
        )
        self._bash_n(tmp_path, moved, name)
        monkeypatch.setitem(globals(), "_audit_body", lambda: moved)
        spliced = _splice(_ARMS_BASE)
        assert _ARMS_BASE in spliced, (
            f"`_splice` did not put the fixture block into a body reformatted "
            f"by `{name}`"
        )
        assert 'case "$verdict" in' not in spliced.replace(_ARMS_BASE, "", 1), (
            f"`_splice` left the reformatted original `case` behind as well "
            f"as the fixture block, so the spliced body holds two of them"
        )
        self._bash_n(tmp_path, spliced, name)

    def test_a_fixture_anchor_that_drifted_names_the_workflow(self):
        """`_arm`'s assert is the file's import, so its message is load-bearing.

        `_splice`'s docstring argues at length that a harness failing "with a
        bare `ValueError` naming a string" is unacceptable because it says
        nothing about the workflow - and `_arm`'s own anchor assert was that
        exact failure, reached by dropping a status from `_ARMS_BASE`. A collection error is the loudest failure this file has
        and it was the least informative one.
        """
        with pytest.raises(AssertionError) as caught:
            _arm("a line the shipped `case` block does not contain", "x")
        message = str(caught.value)
        assert "_ARMS_BASE" in message, message
        assert "account-skill-zips.yml" in message, (
            f"the anchor assert does not say which file drifted, so a "
            f"collection error sends the reader to the fixture rather than "
            f"to the step: {message}"
        )

    _REJECTED_MODELS = {
        "per-line-quotes": _model_per_line_quotes,
        "strip-then-mask": _model_strip_then_mask,
        "mask-then-strip": _model_mask_then_strip,
    }

    def test_every_rejected_model_reads_a_backslash_as_the_scanner_does(self):
        """The one axis these models may differ on is quote tracking.

        The comment above them says so, and the matrix below only means what
        it says while it is true: a model that also disagreed about what the
        mask says for a backslash would be caught - or missed - by the wrong
        half of a fixture. The mask distinguishes a line continuation from an
        escaped backslash, which is a rule none of these three is a variant
        of, so all four scanners have to give the same answer for it.
        """
        for name, body, _printed, _commands in \
                TestEveryFailableCommandInTheAuditStepIsGuarded._BACKSLASH_PAIR:
            expected = _shell_scan(body)[1]
            for model_name, model in self._REJECTED_MODELS.items():
                assert model(body)[1] == expected, (
                    f"`{model_name}` disagrees with `_shell_scan` about the "
                    f"mask for `{name}`: {model(body)[1]!r} against "
                    f"{expected!r}. These models exist to differ in ONE way, "
                    f"and this is not it."
                )

    # WHICH FIXTURE CATCHES WHICH REJECTED MODEL - THE WHOLE MATRIX, not only
    # the cells that hold. `_shell_scan`'s docstring names the pairings and
    # the `_ARMS_OK` comment above says which fixtures are near-duplicates of
    # one another; both are claims about the EMPTY cells as much as the full
    # ones, and a parametrization that only ran the full ones could not tell
    # a fixture that discriminates nothing from one that discriminates two.
    # So every model is run against every fixture below.
    _MODEL_MATRIX = {
        "wrapped-warning-hash-before-the-closing-quote":
            {"per-line-quotes", "strip-then-mask"},
        "wrapped-warning-with-no-hash": {"per-line-quotes"},
        "wrapped-warning-hash-with-the-quote-closing-later": {"per-line-quotes"},
        "apostrophe-inside-a-comment": {"mask-then-strip"},
    }
    # `itertools.product` rather than a comprehension: a comprehension in a
    # class body cannot see the class's own names past its outermost
    # iterable.
    _MODEL_PAIRINGS = list(itertools.product(_REJECTED_MODELS, _MODEL_MATRIX))

    @pytest.mark.parametrize(
        "model_name, fixture", _MODEL_PAIRINGS,
        ids=[f"{m}/{f}" for m, f in _MODEL_PAIRINGS])
    def test_each_rejected_quote_model_is_caught_by_its_own_fixture(
            self, monkeypatch, model_name, fixture):
        """The docstring's "measured wrong" list, measured here every run.

        `_shell_scan` rejects three quote models and says which `_ARMS_OK`
        shape catches each. Nobody reading that can check it - the rejected
        models exist only as prose in the paragraph rejecting them - which
        makes it exactly the claim #120 is about. So the models are kept
        runnable next to the real one and the pairing is asserted.

        BOTH DIRECTIONS, which is what the earlier version of this got wrong.
        Running only the cells that hold says nothing about the empty ones, so
        the comment above `_ARMS_OK` was free to claim each wrapped shape
        catches a model the others do not while two of them caught nothing
        extra. A cell here is a red either way round: a model that stops being
        caught, and a model that starts being caught, both mean a description
        somewhere has gone stale.

        A cell that stops holding is not a licence to delete the fixture: it
        means the docstring is now wrong about something.
        """
        blocks = dict(_ARMS_OK)
        body = _splice(blocks[fixture])
        monkeypatch.setattr(type(self), "_body", lambda self: body)
        # The real scanner first, so a fixture broken for some other reason
        # cannot pass for a discriminating one.
        self.test_the_case_block_names_every_status_the_module_knows()
        monkeypatch.setitem(globals(), "_shell_scan",
                            self._REJECTED_MODELS[model_name])
        try:
            self.test_the_case_block_names_every_status_the_module_knows()
        except AssertionError:
            caught = True
        else:
            caught = False
        assert caught == (model_name in self._MODEL_MATRIX[fixture]), (
            f"`{fixture}` "
            f"{'no longer reds' if not caught else 'now reds'} "
            f"`{model_name}`, and `_MODEL_MATRIX` says the opposite. Correct "
            f"the matrix and whichever description rests on it - "
            f"`_shell_scan`'s rejected-model paragraph, or the comment above "
            f"the wrapped shapes in `_ARMS_OK`."
        )

    def test_the_regression_set_did_not_shrink(self):
        """A FLOOR under the reformat set, not a count of it.

        #120 is about a comment that carried a number nothing asserted; it
        read as checked and went stale on the next edit. This number is the
        opposite shape - it is asserted here, so deleting a shape from the set
        reds rather than passing quietly, and dropping a shape is how a
        scanner starts false-reding on it again.

        `>=`, NOT `==`, and the difference is the test's own name. Equality
        reds when the set GROWS, so every contributor adding a shape has to
        edit a number to get back to green - and a number you must edit to
        make a red go away is one people learn to edit rather than read.
        Removing a shape is the thing being prevented; adding one is the thing
        being invited, and the assertion now says only that.
        """
        assert len(_ARMS_OK) >= 27, (
            "a shape came out of the reformat set. Removing one is how a "
            "scanner starts false-reding on it again; add shapes freely, but "
            "do not take one out to make a helper pass."
        )

    def test_a_crlf_checkout_does_not_read_as_a_vocabulary_drift(
            self, monkeypatch):
        """A line ending is not a rename, and must not be reported as one.

        NOT IN `_ARMS_OK`, and the reason is measurable rather than stylistic:
        `bash -n` REJECTS a CRLF body - `syntax error near unexpected token
        $'in\\r'` - so this shape cannot sit in a set whose gate is `bash -n`
        cleanliness. It is not a reformat anyone types either; it is what a
        checkout can hand a scanner on a machine configured for it, and
        pytest-windows runs this file.

        The alternative red is the wrong diagnosis, which is the cost being
        avoided: "the `case` and account_zip_selection.py disagree about which
        statuses exist" points at two files that agree perfectly.
        """
        body = _splice(_ARMS_BASE).replace("\n", "\r\n")
        monkeypatch.setattr(type(self), "_body", lambda self: body)
        self.test_the_case_block_names_every_status_the_module_knows()

    # THE DECOY IN EVERY SHAPE A COMMENT CAN OPEN IN. The own-line form is the
    # one a text search and a code search already agree is a comment; the rest
    # open the comment straight after a metacharacter, which is where both
    # scanners in this file used to disagree with bash and hand `_tail` a slice
    # that starts mid-sentence. `(:)` is the `)` shape, and it is here because
    # excluding `)` outright left this decoy live through a round that reported
    # the boundary rule closed. Which openers exist at all is asserted next to
    # them, in TestWhereAHashOpensAComment.
    @pytest.mark.parametrize(
        "decoy_prefix", ["", ": ;", "true &", "true |", "(", "(:)"],
        ids=["own-line", "after-;", "after-&", "after-|", "after-(",
             "after-)"])
    def test_the_tail_slice_is_not_relocated_by_a_comment(
            self, monkeypatch, decoy_prefix):
        """`_tail()` is the slice EVERY verdict test in this class executes.

        So a boundary found by text rather than by code is the worst-placed
        instance of that defect in this file: a comment that quotes the marker
        verbatim relocates the slice, and every test below goes on passing
        against a region that is not the step. Green, and wired to nothing.

        The naive slice is COMPUTED below and asserted to differ rather than
        described, so each decoy is proved to be a decoy on the machine
        running this instead of on the one that wrote the docstring.
        """
        body = _audit_body()
        lines = body.splitlines()
        i = next(k for k, l in enumerate(lines) if l.startswith("verdict=$("))
        decoy = (decoy_prefix
                 + '# the heredoc ends with ) || verdict="" and then dispatches')
        if decoy_prefix == "(":
            decoy += "\n:)"
        poisoned = "\n".join(lines[:i] + [decoy] + lines[i:]) + "\n"
        pristine = self._tail()
        assert "verdict=$(" not in pristine, (
            "the marker no longer sits after the heredoc, so this decoy is "
            "not testing what it says it is"
        )
        # The naive slice, shown relocated rather than described, so the decoy
        # is proved to BE a decoy on the machine running this.
        marker = ') || verdict=""'
        naive = poisoned[poisoned.index(marker) + len(marker):]
        assert naive != pristine, (
            "a text search for the marker was NOT relocated by this decoy, so "
            "the assertion below would prove nothing about the hardened one"
        )
        monkeypatch.setattr(type(self), "_body", lambda self: poisoned)
        assert self._tail() == pristine, (
            "a comment quoting the empty-capture marker moved the tail slice; "
            "every verdict test in this class would be executing the wrong "
            "region of the step"
        )

    @pytest.mark.parametrize("results_ok", ["yes", "no"])
    @pytest.mark.parametrize("pad", [" {}", "{} ", "{}\r", "\t{} "])
    @pytest.mark.parametrize(
        "verdict", sorted(sel.KNOWN_STATUSES | {sel.UNAVAILABLE}))
    def test_whitespace_around_a_verdict_changes_nothing(
            self, tmp_path, verdict, pad, results_ok):
        """A padded verdict and its bare twin must behave identically.

        PARAMETRIZED OVER `results_ok` BECAUSE THAT IS WHERE THEY DIVERGED
        MOST, and taking the default on both sides cannot see it. Measured
        without the fix: ` not-yet-bootstrapped` with results_ok=no raised one
        annotation where bare `not-yet-bootstrapped` raised none.

        WHAT THAT ANNOTATION WAS SAYING MATTERS, because "loud went quiet"
        reads like a regression and this is the opposite. The annotation was
        the catch-all's - "skills-evals' verdict vocabulary has moved under
        it" - which is FALSE about a padded status this repo knows perfectly
        well. The bare form's silence is the deliberate answer argued at
        length in that arm's own comment: a workflow that annotates every run
        of a fresh install is a workflow whose annotations get ignored. So
        what the fix removes is a wrong diagnosis, and what it leaves is the
        designed one.

        AND THE HARNESS'S WINDOW IS NARROWER THAN THE RUN'S, said rather than
        assumed: `_tail()` starts after the heredoc, so the clone failure that
        sets results_ok=no is annotated in the step HEAD that this harness
        never executes. A production run in that state is not silent; this
        slice of it is.
        """
        one, two = tmp_path / "bare", tmp_path / "padded"
        one.mkdir()
        two.mkdir()
        bare = self._run(one, verdict, results_ok=results_ok)
        padded = self._run(two, pad.format(verdict), results_ok=results_ok)
        assert bare[1] == padded[1], (
            f"{verdict!r} and {pad.format(verdict)!r} wrote different "
            f"$GITHUB_OUTPUT: {bare[1]!r} vs {padded[1]!r}"
        )
        assert bare[0].count("::warning::") == padded[0].count("::warning::"), (
            f"{verdict!r} and {pad.format(verdict)!r} raised a different "
            f"number of annotations:\n{bare[0]}\n---\n{padded[0]}"
        )

    @pytest.mark.parametrize("blank", [" ", "\t", "\r", " \t\r ", "  "])
    def test_a_whitespace_only_capture_is_an_empty_capture(
            self, tmp_path, blank):
        """`[ -z "$verdict" ]` cannot see a single space, and one is empty.

        Without the trim a whitespace-only capture sails past the diagnostic
        branch that was written for exactly this fault, lands in `*)`, and
        accuses skills-evals of a vocabulary rename that never happened -
        while writing `status= ` for the next step to read.

        Asserted on the BRANCH it reaches, not only on the status it writes:
        the wrong branch with the right status is still the wrong annotation
        on the run page, which is the thing this step exists to get right.
        """
        log, out = self._run(tmp_path, blank)
        assert "status=unavailable" in out.splitlines(), out
        assert "could not compute the published account audit verdict" in log, (
            "a whitespace-only capture did not reach the empty-capture "
            f"branch, so it was diagnosed as something it is not:\n{log}"
        )

    # WHITESPACE THAT HAPPENS TO BE A NEWLINE, which is where the trim and
    # the newline-reject meet. `fresh\n`
    # and `\nfresh` are the shape a heredoc emits when something prints a bare
    # newline at import time, or when `print()` is called with no argument -
    # padding, not a second value.
    @pytest.mark.parametrize("verdict", [
        "\nfresh", "\n\nfresh", " \nfresh", "\tfresh\n",
        "fresh\n", "fresh\n\n", "fresh \n",
    ])
    def test_a_capture_padded_with_a_newline_is_trimmed_not_condemned(
            self, tmp_path, verdict):
        """THE ORDER OF THE TRIM AND THE NEWLINE-REJECT, executed.

        The step trims leading and trailing whitespace and then rejects any
        capture that still holds a newline. Swap those two and the rejection
        sees the padding as a second line: every case here degrades to
        `unavailable` and raises an annotation about a cross-repo import that
        did not break. Nothing else in this file notices - measured, moving
        the reject above the two trim lines leaves the whole of `scripts/`
        green - so the ordering was argued in a comment and held by nothing,
        which is the shape #120 is about.

        `$( )` already discards a TRAILING blank line, so condemning a LEADING
        one would make the step's answer depend on which end the blank arrived
        at. That asymmetry is what these cases pin.

        THE POLICY BOUNDARY IS RIGHT HERE and not in the sibling test above:
        that one asserts a capture holding a second VALUE (`junk\nfresh`) is
        rejected, and this one asserts a capture holding only padding is not.
        Both directions of one rule, and neither is safe to infer from the
        other.
        """
        out, written = self._run(tmp_path, verdict)
        assert written == "status=fresh\n", (
            f"a capture padded with a newline was condemned rather than "
            f"trimmed: {verdict!r} wrote {written!r}. The newline-reject runs "
            f"before the trim."
        )
        assert "::warning::" not in out, (
            f"a healthy verdict padded with whitespace raised an annotation: "
            f"{verdict!r} -> {out!r}"
        )

    @pytest.mark.parametrize("verdict", [
        "fresh", " fresh", "fresh ", "fresh\r", "\tfresh",
        sel.AUDIT_DRIFT_STATUS, sel.AUDIT_DRIFT_STATUS + "\r",
        " " + sel.UNAVAILABLE, " ", "\t", "", "wat", " wat ",
        "junk\nfresh", sel.AUDIT_DRIFT_STATUS + "\njunk",
    ])
    def test_the_step_and_the_module_agree_on_what_a_verdict_is(
            self, tmp_path, verdict):
        """THE PROPERTY, rather than an enumeration of the shapes that hold it.

        The step dispatches on `$verdict` with `case` patterns;
        account_zip_selection.py decides what the same string MEANS. A `case`
        pattern does not strip and `normalise_status` does, so the two can
        disagree about a string neither of them is wrong about.

        THE FIRST ASSERTION IS THE ONE THAT CATCHES A REACHABLE FAULT, and it
        is first because the other two pass without it. `$( )` captures ALL
        stdout of the heredoc, so a print at import time - in `yaml`, or in
        skills-evals' own `propagation.account_store`, neither of which this
        repo can see - prepends a line and `echo "status=$verdict"` writes a
        two-line entry whose second line has no `=`. The runner parses that
        file line by line and REJECTS it: the step dies instead of degrading,
        which is the one outcome this whole block exists to avoid. The
        cases holding an embedded newline are what exercise it, and on the
        untrimmed step they wrote two lines while both of the assertions below
        still passed.

        The workflow documents this same fault a second time, further down,
        for the `drift` read - where a test already executes it. This capture
        had nothing.
        """
        _, out = self._run(tmp_path, verdict)
        lines = [l for l in out.splitlines() if l]
        assert len(lines) == 1, (
            f"the step wrote {len(lines)} lines to $GITHUB_OUTPUT for "
            f"{verdict!r}: {out!r}. The runner parses that file line by line "
            f"and rejects a line with no `=`, so a stray stdout print inside "
            f"the heredoc takes the step out rather than degrading it."
        )
        written = lines[0].partition("=")[2]
        assert written == written.strip(), (
            f"the step wrote un-normalised whitespace into its own output: "
            f"{written!r}"
        )
        if "\n" in verdict:
            # The policy EDIT above chose, asserted rather than assumed: a
            # contaminated capture is REJECTED, not salvaged by keeping a
            # line of it. Salvaging recovers a plausible answer and leaves the
            # run green with no annotation; rejecting hands it to the branch
            # whose text is already the right diagnosis.
            assert sel.normalise_status(written) == sel.UNAVAILABLE, (
                f"a multi-line capture was salvaged rather than degraded: "
                f"{written!r}"
            )
        else:
            assert sel.normalise_status(written) == sel.normalise_status(
                verdict), (
                f"the step and the module disagree about {verdict!r}: the "
                f"step wrote {written!r}, which the module reads as "
                f"{sel.normalise_status(written)!r}, while it reads the "
                f"capture itself as {sel.normalise_status(verdict)!r}"
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

    @pytest.mark.parametrize("path", [ZIPS, RECORD], ids=["zips", "record"])
    def test_nothing_in_this_workflow_overrides_the_runner_s_shell(self, path):
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

        BOTH WORKFLOWS THIS FILE EXECUTES, not just the one the harness
        above spawns. record-account-upload.yml declares no `shell:` and no
        `defaults:` either, so it runs under the same `/usr/bin/bash -e {0}`,
        and TestSkillInputIsValidatedBeforeUse lifts a `case` block out of it
        and runs it. Asserting this for one of the two files left the other
        free to grow a `defaults:` that nothing here would see.

        Parsed, per AGENTS.md - the same shape as scripts/test_ci_workflow.py
        holding ci.yml's no-`concurrency` invariant.
        """
        wf = load(path)
        assert "defaults" not in wf, (
            f"{path.name}: a workflow-level `defaults:` overrides the "
            "runner's `/usr/bin/bash -e {0}`, so the shell the tests execute "
            "is no longer the shell the step gets"
        )
        for job_id, job in wf["jobs"].items():
            assert "defaults" not in job, (
                f"{path.name}: job `{job_id}` declares `defaults:`; see above"
            )
            for step in job.get("steps", []):
                assert "shell" not in step, (
                    f"{path.name}: step "
                    f"`{step.get('name') or step.get('id')}` in job "
                    f"`{job_id}` declares `shell:`; see above"
                )

    def test_both_harnesses_hand_the_step_to_the_runner_s_shell(
            self, tmp_path, monkeypatch):
        """The OTHER half of the premise above, and the half nothing held.

        The test before this one locks the WORKFLOW: no `shell:`, no
        `defaults:`, so GitHub runs the step as `/usr/bin/bash -e {0}`. That
        says nothing about how THIS FILE spawns it. `_run` and `_run_step`
        each pass `-e` by hand, in two places, and `bash -e script` ->
        `bash script` is exactly the tidy-up that looks harmless: dropping it
        from both spawns changed no test result on the tree before this test
        existed, while the same edit disarms
        test_a_placeholder_it_cannot_create_does_not_abort_the_step, the only
        test that catches an unguarded `mkdir` in the step head.

        Asserted by RECORDING the argv, not by reading this file's own
        source: a source scan cannot see a spawn that moved behind a helper,
        it would go green on a helper that stopped passing the flag on, and
        it would false-red on the deliberate `bash -c` spawns further down,
        which run a `case` fragment rather than a `run:` body.

        WHAT THIS DOES NOT COVER, said rather than implied: a brand-new third
        sibling harness that this test never calls. `len(seen) == 2` catches
        an extra spawn inside `_run` or `_run_step` and nothing beyond them.
        """
        seen = []
        real = subprocess.run

        def record(argv, *args, **kwargs):
            seen.append(list(argv))
            return real(argv, *args, **kwargs)

        tail = tmp_path / "tail"
        tail.mkdir()
        step = tmp_path / "step"
        step.mkdir()
        monkeypatch.setattr(subprocess, "run", record)
        self._run(tail, "fresh")
        self._run_step(step)
        monkeypatch.undo()

        assert len(seen) == 2, (
            "expected exactly one spawn from each harness; a third execution "
            f"path is not covered by this assertion:\n{seen}"
        )
        for argv in seen:
            assert argv[:2] == [require_bash(), "-e"], (
                "a harness in this file spawned the step body as "
                f"{argv[:2]} rather than `bash -e`. The runner gives the step "
                "errexit and the step cannot take it back, so a harness "
                "without it reports the opposite of what production does on "
                "any unguarded failure."
            )

        # NEGATIVE CONTROL: that the flag asserted above still means errexit
        # on the machine running this. Without it the loop is a string
        # comparison wired to nothing.
        probe = real(
            [require_bash(), "-e",
             _script(tmp_path, "false\necho reached\n", name="probe.sh")],
            capture_output=True, text=True,
        )
        assert probe.returncode != 0 and "reached" not in probe.stdout, (
            "`bash -e` ran on past an unguarded failure, so the assertion "
            "above proves nothing about this shell"
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

    def _step_body(self):
        """The `run:` body holding both input guards, read through `yaml`."""
        return next(
            s["run"] for s in load(RECORD)["jobs"]["record"]["steps"]
            if s.get("name") == "Resolve the run that built the artifact"
        )

    def _guard(self):
        body = self._step_body()
        # THE SAME BOUNDARY RULE, because this slice is EXECUTED and a wrong
        # boundary here fails in the direction that hides itself. A truncated
        # slice is broken bash: it returns non-zero for EVERY input, so
        # test_it_admits_every_real_declared_name goes red - loud - while
        # every refusal below PASSES VACUOUSLY, proving the guard rejects
        # `sync-skills;id` with a snippet that also rejects `sync-skills`.
        # Anyone who "fixes" the red by trimming the admit list ships a suite
        # that asserts nothing about the injection guard. An EMPTY slice is the
        # opposite failure and the silent-green one: an empty snippet exits 0
        # and admits everything.
        #
        # A slice of `body`, not of the scan's `text`: what is returned here is
        # RUN, and today's snippet is preserved byte for byte.
        text, mask = _shell_scan(body)
        opener, _, closer = _case_block(
            text, mask, _CASE_SKILL,
            "the skill guard's `case \"$SKILL\"` is no longer a command this "
            "test can find; an empty snippet exits 0 and ADMITS everything",
        )
        return body[opener.start():closer.end()]

    def _bash(self, script, value, name="SKILL"):
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
        #
        # NO `-e`, AND THAT IS ESTABLISHED RATHER THAN ASSUMED. The runner
        # hands a `run:` BODY to `/usr/bin/bash -e {0}`, and
        # test_both_harnesses_hand_the_step_to_the_runner_s_shell locks the
        # audit-step harnesses to that. This is not a `run:` body: it is a
        # `case` fragment lifted out of one, so the same argument does not
        # carry over by itself. It is errexit-NEUTRAL instead -
        # test_the_extracted_fragments_behave_the_same_under_errexit runs
        # every input this class uses through both fragments with and without
        # `-e` and requires the same status, stdout and stderr - which is why
        # the flag is absent rather than merely missing. A fragment that grows
        # a command errexit can act on reds there.
        return subprocess.run(
            [bash, "-c", script], env={**os.environ, name: value},
            capture_output=True, text=True,
        )

    def _run(self, snippet, value, name="SKILL"):
        return self._bash(snippet, value, name).returncode

    def _from_run_guard(self):
        """The SIBLING guard, sliced by the same machinery.

        `case "$FROM_RUN" in ''|*[!0-9]*)` validates the other value this step
        reads from `inputs`, and it is the one that goes straight into an API
        path. Nothing in `scripts/` executed it while its neighbour was run
        against a list of real evil inputs; see #126.
        The boundary rule and the vacuous-slice hazard are the SKILL guard's,
        unchanged - an empty slice exits 0 and admits everything, so
        test_the_extracted_from_run_guard_admits_and_refuses pairs an admit
        with a refusal in one body.

        Its opener and its first pattern share a line, which is the shape
        `_case_block` was taught to stop requiring.
        """
        body = self._step_body()
        text, mask = _shell_scan(body)
        opener, _, closer = _case_block(
            text, mask, _CASE_FROM_RUN,
            "the run-ID guard's `case \"$FROM_RUN\"` is no longer a command "
            "this test can find; an empty snippet exits 0 and ADMITS "
            "everything",
        )
        return body[opener.start():closer.end()]

    def test_the_extracted_guard_is_the_one_that_reads_SKILL(self):
        """The slice is the SKILL guard, and it both admits and refuses.

        NOT a `bash -n` control, which is the obvious shape and a strict
        subset of a test that already exists: a truncated slice is broken
        bash, so it returns non-zero for every input and reds every
        test_it_admits_every_real_declared_name case already. `bash -n`
        failing therefore tells you nothing new, and `bash -n` PASSING tells
        you nothing at all - `case "$SKILL" in *) exit 1 ;; esac` is valid
        bash that refuses everything and sails through it.

        Pairing one admit with one refusal in ONE body is what cannot be
        defeated by trimming the admit list, which is the failure mode this
        guards: the vacuous slice reds loudly in the admit tests and passes
        VACUOUSLY in every refusal below, so a reader who "fixes" the red
        by deleting admit cases ships a suite asserting nothing about
        injection.
        """
        snippet = self._guard()
        assert len(_CASE_SKILL.findall(snippet)) == 1, (
            f"the extracted snippet is not one SKILL guard:\n{snippet}"
        )
        assert snippet.rstrip().endswith("esac"), (
            f"the extracted snippet does not close its `case`:\n{snippet}"
        )
        assert self._run(snippet, "sync-skills") == 0, (
            "the extracted snippet refuses a real declared name, so every "
            "refusal test below is passing vacuously"
        )
        assert self._run(snippet, "sync-skills;id") != 0, (
            "the extracted snippet admits a command separator, so it is not "
            "the guard"
        )

    def test_the_guard_is_found_with_or_without_quotes_on_its_case_word(self):
        """A reformat bash cannot see, on the OTHER workflow's guard.

        `case` expands its word and then neither field-splits nor
        pathname-expands the result, so `case $SKILL in` and `case "$SKILL" in`
        are one command. The slice finder required the quoted form, so
        dropping the quotes reded this whole class with "no longer a command
        this test can find" - an honest message about a change bash does not
        see, which is the false red this file's scanners exist to remove.

        BOTH FORMS ARE RUN rather than compared as text: the equivalence being
        claimed is a runtime one, so the proof is that the unquoted slice still
        admits a real name and still refuses a command separator.
        """
        body = self._step_body()
        unquoted = body.replace('case "$SKILL"', "case $SKILL")
        assert unquoted != body, (
            "the guard no longer spells its word `case \"$SKILL\"`, so this "
            "test is comparing one form against itself"
        )
        for label, variant in (("quoted", body), ("unquoted", unquoted)):
            text, mask = _shell_scan(variant)
            opener, _, closer = _case_block(
                text, mask, _CASE_SKILL,
                f"the {label} form of the SKILL guard was not found",
            )
            snippet = variant[opener.start():closer.end()]
            assert self._run(snippet, "sync-skills") == 0, (
                f"the {label} slice refuses a real declared name"
            )
            assert self._run(snippet, "sync-skills;id") != 0, (
                f"the {label} slice admits a command separator"
            )

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

    # THE RUN IDS THIS STEP HAS TO ADMIT. `from_run` reaches
    # `gh api /repos/.../actions/runs/$FROM_RUN/artifacts`, so the guard is the
    # only thing between a dispatch input and an API path.
    _REAL_RUN_IDS = ["32414713598", "1", "0", "007"]

    def test_the_extracted_from_run_guard_admits_and_refuses(self):
        """The vacuous-slice control, on the sibling guard - #126.

        Same hazard as its neighbour and the same shape of answer: an empty
        or truncated slice passes every refusal below for the wrong reason, so
        one admit and one refusal are paired in a single body. `bash -n` would
        not do it - `case "$FROM_RUN" in *) exit 1 ;; esac` is valid bash that
        refuses everything.
        """
        snippet = self._from_run_guard()
        assert len(_CASE_FROM_RUN.findall(snippet)) == 1, (
            f"the extracted snippet is not one FROM_RUN guard:\n{snippet}"
        )
        assert snippet.rstrip().endswith("esac"), (
            f"the extracted snippet does not close its `case`:\n{snippet}"
        )
        assert self._run(snippet, "32414713598", "FROM_RUN") == 0, (
            "the extracted snippet refuses a real run ID, so every refusal "
            "test below is passing vacuously"
        )
        assert self._run(snippet, "1;id", "FROM_RUN") != 0, (
            "the extracted snippet admits a command separator, so it is not "
            "the guard"
        )

    @pytest.mark.parametrize("run_id", _REAL_RUN_IDS)
    def test_the_run_id_guard_admits_a_run_id(self, run_id):
        assert self._run(self._from_run_guard(), run_id, "FROM_RUN") == 0, run_id

    @pytest.mark.parametrize("evil, why", [
        ("", "empty - the dispatch input was left blank"),
        ("1;id", "command separator"),
        ("1 2", "space splits it into two argv words"),
        ("1\nfoo=bar", "newline injects a line into $GITHUB_ENV"),
        ("../../../orgs/x/actions", "traversal out of the runs path"),
        ("-1", "leading dash reads as an option"),
        ("1e5", "scientific notation is not a run ID"),
        ("0x10", "hex is not a run ID"),
        (" 1", "leading space"),
        ("1 ", "trailing space"),
        ("١٢", "Arabic-Indic digits are digits and are not [0-9]"),
        ("$(id)", "command substitution, unexpanded by the guard"),
    ])
    def test_the_run_id_guard_refuses_anything_that_is_not_digits(
            self, evil, why):
        # Delivery first, for the reason `_bash` gives: a refusal test passes
        # vacuously if the harness never handed the shell the dangerous value.
        got = self._bash('printf %s "$FROM_RUN" | wc -c', evil,
                         "FROM_RUN").stdout.strip()
        assert int(got) == len(evil.encode()), (
            f"harness mangled the input before the guard saw it: sent "
            f"{len(evil.encode())} bytes, shell received {got}. The refusal "
            f"below would have passed for the wrong reason."
        )
        assert self._run(self._from_run_guard(), evil, "FROM_RUN") != 0, why

    def test_the_extracted_fragments_behave_the_same_under_errexit(self):
        """Why `_bash` spawns without `-e`, measured rather than assumed - #126.

        The runner gives a `run:` body `/usr/bin/bash -e {0}` and
        record-account-upload.yml declares no `shell:` and no `defaults:`, so
        its body gets that shell like every other. What `_bash` runs is not
        that body: it is a `case` fragment lifted out of it, delivered through
        the environment. So the fidelity argument #120 settled for the audit
        step does not carry over on its own, and the question is whether the
        fragment behaves differently under errexit.

        It does not, and this is where that is established: every input this
        class uses, through both fragments, with and without `-e`, compared on
        exit status, stdout and stderr. There is nothing for errexit to act
        on - a `case` with no matching arm returns 0, `echo` succeeds, and
        `exit 1` exits the same way under either shell.

        So the flag is absent because it changes nothing, not because nobody
        looked. A fragment that grows a command which can fail - a `grep`, a
        `[` outside a condition - reds here, and the answer then is to make
        the harness match the runner rather than to update this docstring.
        """
        bash = require_bash()
        fragments = {"SKILL": self._guard(), "FROM_RUN": self._from_run_guard()}
        values = {
            "SKILL": ["sync-skills", "sync-skills;id", "-rf", "",
                      "sync-skills\nfoo=bar"],
            "FROM_RUN": self._REAL_RUN_IDS + ["", "1;id", "1\nfoo=bar", "-1"],
        }
        for name, fragment in fragments.items():
            for value in values[name]:
                answers = []
                for flags in (["-c"], ["-ec"]):
                    proc = subprocess.run(
                        [bash, *flags, fragment],
                        env={**os.environ, name: value},
                        capture_output=True, text=True)
                    answers.append(
                        (proc.returncode, proc.stdout, proc.stderr))
                assert answers[0] == answers[1], (
                    f"the {name} fragment is NOT errexit-neutral on "
                    f"{value!r}: without `-e` it answered {answers[0]}, with "
                    f"`-e` {answers[1]}. `_bash` spawns without the flag the "
                    f"runner uses, which is now a fidelity gap rather than a "
                    f"measured no-op - give it the runner's shell."
                )

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
