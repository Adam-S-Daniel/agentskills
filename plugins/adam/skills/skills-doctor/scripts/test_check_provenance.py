#!/usr/bin/env python3
"""Tests for check_provenance.py.

Hermetic and deterministic: no network, no sleeps, and no dependence on the
wall clock — the one test that reasons about mtimes sets them with `os.utime`
rather than waiting for them.

Every test drives EXPLICIT paths under `tmp_path`. That is not a nicety: the
script's defaults are `~/.claude/skills` and `.`, so a test that let either fall
through would read, digest and report on the developer's real machine. `run`
below is the only way these tests invoke it, and it supplies all THREE of the
script's paths — the same discipline `_run_hook` keeps in
`scripts/test_generate_skills_lock.py`, which is a required argument and a
hand-built environment rather than a guard.

The third one was learned the hard way. `--project-dir` was added late and `run`
was left passing two, so every test read `<pytest's cwd>/.claude/skills` — and
two of them flipped from pass to fail when that directory existed. It passed
only because this repo happens not to have one.
"""

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Tuple

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import check_provenance as prov  # noqa: E402

REGISTRY_SLUG = "Adam-S-Daniel/agentskills"
REGISTRY_URL = "https://github.com/Adam-S-Daniel/agentskills.git"

# The same shape `TRICKY_SKILL` has in the generator's suite: nested directories,
# an empty file, CRLF, a UTF-8 filename, and a file with no trailing newline.
# Digest agreement on `{"SKILL.md": "hi"}` would prove very little.
TRICKY = {
    "SKILL.md": "---\nname: tricky\n---\ntricky body\n",
    "reference/nested/deep.md": "two directories down\n",
    "empty.txt": "",
    "crlf.md": "line one\r\nline two\r\n",
    "ünïcodé-名前.md": "a UTF-8 filename\n",
    "no-trailing-newline.txt": "no newline at end of file",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def run(skills_dir: Path, lock: Path, capsys, project_dir: Path = None
        ) -> Tuple[int, str]:
    """Run the doctor against an explicit store, lock and project.

    `project_dir` defaults to a SIBLING OF THE STORE — under `tmp_path`, and
    normally not existing — rather than to the script's own default of `.`, which
    is pytest's working directory and therefore the developer's own checkout.
    Derived here rather than asked of every caller so that no test can reach the
    real one by forgetting an argument.

    Use `flat` on the returned output for any assertion on a sentence: prose is
    wrapped at render time, so matching raw text pins where the line broke rather
    than what was claimed.
    """
    if project_dir is None:
        project_dir = skills_dir.parent / "no-project"
    assert tempfile.gettempdir() in str(project_dir) or "pytest" in str(project_dir), (
        f"project dir {project_dir} is outside the test's own tmp tree")
    code = prov.main(["--skills-dir", str(skills_dir), "--lock", str(lock),
                      "--project-dir", str(project_dir)])
    return code, capsys.readouterr().out


def flat(out: str) -> str:
    """`out` with every run of whitespace collapsed to one space."""
    return " ".join(out.split())


def make_skill(store: Path, name: str, body: str = "body\n") -> Path:
    skill = store / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n{body}", encoding="utf-8")
    return skill


def write_record(store: Path, *names: str, registry: str = REGISTRY_URL,
                 bundle: str = "adam", digest: str = None) -> Path:
    """Write the record in the hook's own shape, digesting what is on disk.

    `digest` overrides the measured value, which is how a test says "the bytes
    changed after the hook recorded them" without needing the hook.
    """
    installed = [{"name": name, "registry": registry, "bundle": bundle,
                  "digest": digest or prov.digest_skill_dir(store / name)}
                 for name in sorted(names)]
    path = store / prov.RECORD_NAME
    path.write_text(json.dumps({"version": 1, "installed": installed}, indent=2) + "\n",
                    encoding="utf-8")
    return path


def write_lock(path: Path, store: Path, *names: str, registry: str = REGISTRY_SLUG,
               bundle: str = "adam", digests: dict = None) -> Path:
    skills = {f"{bundle}/{name}": (digests or {}).get(
        name, prov.digest_skill_dir(store / name) or "0" * 64) for name in names}
    path.write_text(json.dumps({
        "registry": registry, "ref": "a" * 40, "bundles": [bundle], "skills": skills,
    }, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def durable_surface(monkeypatch):
    """Pin every test to a DURABLE surface unless it deliberately says otherwise.

    Third instance of this file's standing rule, and it arrived the same way the
    first two did. `main` reads the surface out of the REAL environment, and this
    suite runs on both kinds of machine — including the cloud sessions where
    `CLAUDE_CODE_REMOTE_SESSION_ID` is set. Measured before this fixture existed:
    `test_locked_skills_absent_from_a_store_the_hook_never_ran_on_are_notes`
    passed on a laptop and failed in a cloud session, on identical bytes, because
    the promotion below is CORRECT there. A test that flips with where pytest was
    invoked is the same defect as one that reads the developer's real
    `~/.claude/skills`, so the environment is supplied here rather than inherited.

    Durable is the default because it is the quiet reading: a test that wants a
    finding has to ask for the surface that raises it.
    """
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_REMOTE_SESSION_ID", raising=False)
    # The third arm, and it arrived with the same rule the docstring above
    # describes. `read_surface` now mirrors the hook's full test, and
    # `SKILLS_BOOTSTRAP_FORCE=1` is how this repo's own hook suite forces a run
    # — so a developer who exported it while debugging the hook would flip every
    # test in this file to an ephemeral surface, on identical bytes. Clearing it
    # is what keeps the fixture's promise once the surface test widened.
    monkeypatch.delenv("SKILLS_BOOTSTRAP_FORCE", raising=False)


@pytest.fixture
def ephemeral(monkeypatch):
    """A cloud session / CI runner / container: a remote session id is issued.

    Sets the entrypoint too, because a real ephemeral surface has one — but
    nothing under test reads its VALUE, deliberately (see `read_surface`).
    """
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "remote_mobile")
    monkeypatch.setenv("CLAUDE_CODE_REMOTE_SESSION_ID", "cse_deadbeef")


@pytest.fixture
def store(tmp_path):
    """A store holding two hook-installed skills, recorded and locked."""
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "beta")
    write_record(store, "alpha", "beta")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha", "beta")
    return store, lock


# ---------------------------------------------------------------------------
# the digest — a third copy of one algorithm
# ---------------------------------------------------------------------------

def _walk_up(relpath: str):
    """The ancestor holding `relpath`, or skip/fail depending on where we are.

    This file ships inside the skill, so it legitimately runs from an installed
    copy at `~/.claude/skills/skills-doctor/scripts` where the registry's own
    `scripts/` are absent — there, skipping is right. (That run leaves a
    `__pycache__` and a `.pytest_cache` in the installed directory. The doctor
    reports them as an `artefacts-and-locked` NOTE and stays at exit 0; it used
    to call the directory edited and hold the exit code at 1 forever.) Inside a registry checkout
    it is not: the two tests that use this are the only ones binding the digest
    and the record format to the real hook, and a skip would retire them silently
    at exactly the moment they broke. So a checkout-shaped path FAILS instead.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / relpath).is_file():
            sys.path.insert(0, str((parent / relpath).parent))
            return parent
    if "plugins" in here.parts:
        pytest.fail(f"inside a registry checkout but {relpath} did not resolve — "
                    f"the hook-binding tests would have skipped silently")
    pytest.skip("not running inside the registry checkout")


def _generator():
    """The registry's `generate_skills_lock` module."""
    _walk_up("scripts/generate_skills_lock.py")
    import generate_skills_lock

    return generate_skills_lock


def test_the_digest_matches_the_generators(tmp_path):
    """The binding that keeps the third copy a COPY.

    `digest_skill_dir` here, `digest_skill_dir` in the generator and `digest_dir`
    in the hook are one algorithm written three times, because neither the hook
    nor this file may import the original. An independently drifting copy would
    not fail loudly — it would report every hook-installed skill as `edited`,
    which reads as "the user changed it" and is a lie about their machine.
    """
    gsl = _generator()
    skill = tmp_path / "skill"
    for relpath, contents in TRICKY.items():
        target = skill / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8", newline="")

    mine = prov.digest_skill_dir(skill)
    assert mine is not None, "the fixture did not digest at all"
    assert mine == gsl.digest_skill_dir(skill), (
        "the doctor's digest has drifted from the generator's")


def test_the_digest_is_none_for_a_path_that_is_not_a_directory(tmp_path):
    """None means "not measured"; a digest string would mean "measured"."""
    plain = tmp_path / "file"
    plain.write_text("x", encoding="utf-8")
    assert prov.digest_skill_dir(plain) is None


def _uploader():
    """sync-skills' `sync_skills` module — the other end of the upload filter."""
    _walk_up("plugins/adam-local/skills/sync-skills/sync_skills.py")
    import sync_skills

    return sync_skills


def test_the_upload_filter_matches_the_uploaders(tmp_path):
    """The binding that keeps `UPLOAD_SKIP_*` a copy of the uploader's rule.

    The account copy of a skill is whatever `zip_skill` put in the ZIP, so the
    only correct definition of "what both channels carry" is the uploader's own
    `_include_in_zip`. This file cannot import it — it ships into a
    `~/.claude/skills` with no sync-skills in it — so the sets are re-declared,
    and a re-declaration that drifts does not fail loudly: it turns whichever
    artefact stopped being skipped into a `shadow-copies-differ` FINDING about a
    session where nothing is wrong.
    """
    up = _uploader()
    assert prov.UPLOAD_SKIP_DIRS == up._SKIP_DIRS
    assert prov.UPLOAD_SKIP_DIR_PREFIXES == up._SKIP_DIR_PREFIXES
    assert prov.UPLOAD_SKIP_EXTS == up._SKIP_EXTS


def test_the_shared_payload_selects_what_the_uploader_would_have_zipped(tmp_path):
    """Bound to the uploader end to end, not just to its constant names.

    Re-declaring the sets correctly and then applying them differently —
    matching a directory name against the file's suffix, say, or testing only
    the last path segment — passes the binding above and still digests a file
    the account copy never held.
    """
    up = _uploader()
    skill = tmp_path / "skill"
    for relpath, data in (("SKILL.md", b"---\nname: skill\n---\nbody\n"),
                          ("scripts/helper.py", b"x = 1\n"),
                          ("scripts/__pycache__/helper.cpython-311.pyc", b"\x00c"),
                          ("scripts/helper.pyo", b"\x00o"),
                          ("payload.b64", b"AAAA"),
                          (".pytest_cache/v/last", b"{}"),
                          ("pytest-cache-files-abc/tmp", b"scratch"),
                          ("node_modules/dep/index.js", b"x")):
        target = skill / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    mine = {relpath for relpath, _ in prov.uploaded_files(skill)}
    assert mine == set(up.skill_payload(skill)), (
        "the doctor's upload filter has drifted from what a real upload carries")


# ---------------------------------------------------------------------------
# the record's three states
# ---------------------------------------------------------------------------

def test_a_missing_record_reads_as_absent(tmp_path):
    assert prov.read_record(tmp_path / "nope.json").state == prov.ABSENT


@pytest.mark.parametrize("corrupt", [
    "", "{ not json at all", "[]", '{"installed": {"alpha": true}}',
], ids=["empty", "truncated", "not-an-object", "installed-not-a-list"])
def test_a_corrupt_record_reads_as_unreadable_not_absent(tmp_path, corrupt):
    """Exactly the shapes the hook's planner exits 3 on.

    Reading any of them as "absent" would tell the reader the hook has never run
    here — a different machine, and a different fix — when in fact it ran, could
    not read its own record, and pruned nothing.
    """
    path = tmp_path / prov.RECORD_NAME
    path.write_text(corrupt, encoding="utf-8")
    assert prov.read_record(path).state == prov.UNREADABLE


def test_absent_and_unreadable_records_report_differently(tmp_path, capsys):
    """The issue itself: a doctor that says the same thing in both states.

    They are two machines with two fixes — "the hook never ran here" versus "the
    hook ran and could not read its own record" — so the two reports must not be
    substitutable. Collapse them and the reader is told to fix the wrong thing.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    _, absent = run(store, lock, capsys)
    (store / prov.RECORD_NAME).write_text("{ not json", encoding="utf-8")
    _, unreadable = run(store, lock, capsys)

    assert "record absent" in flat(absent), absent
    assert "record unreadable" in flat(unreadable), unreadable
    # Both directions, because a one-way check passes on a report that prints the
    # absent explanation under an "unreadable" heading — the two descriptions must
    # not be substitutable, not merely differ somewhere.
    assert "the file is there" in flat(unreadable), unreadable
    assert "the file is there" not in flat(absent), absent
    assert "no hook run has ever reached" in flat(absent), absent
    assert "no hook run has ever reached" not in flat(unreadable), unreadable
    # And the unreadable state carries a consequence and an action; absent has
    # neither, because on a durable machine it is not a defect at all.
    assert "self-heals" in unreadable, unreadable
    assert "self-heals" not in absent, absent


def test_an_empty_record_is_not_the_same_as_an_absent_one(tmp_path, capsys):
    """The hook writes this file at the END of a run, so an empty one is proof a
    run finished and installed nothing — where an absent one is proof none did.
    """
    store = tmp_path / "skills"
    store.mkdir()
    (store / prov.RECORD_NAME).write_text(
        json.dumps({"version": 1, "installed": []}) + "\n", encoding="utf-8")
    lock = write_lock(tmp_path / "skills.lock", store)

    _, out = run(store, lock, capsys)
    assert "record present" in flat(out), out
    assert "present and empty" in flat(out), out


def test_a_malformed_entry_is_skipped_and_reported(tmp_path, capsys):
    """The hook skips an entry it cannot validate rather than failing the file.

    So does this — but silently skipping it would hide the consequence: the
    pruner can never act on that install, whatever the lock later says.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    (store / prov.RECORD_NAME).write_text(json.dumps({"version": 1, "installed": [
        {"name": "alpha", "registry": REGISTRY_URL, "bundle": "adam",
         "digest": prov.digest_skill_dir(store / "alpha")},
        {"name": "bad", "registry": REGISTRY_URL, "bundle": "adam", "digest": "nope"},
    ]}, indent=2) + "\n", encoding="utf-8")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    record = prov.read_record(store / prov.RECORD_NAME)
    assert record.skipped == 1, record
    assert set(record.entries) == {"alpha"}

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "record-entries-skipped" in out, out


@pytest.mark.parametrize("field, value", [
    ("name", "../escape"), ("bundle", "not/a/bundle"),
    ("digest", "A" * 64), ("registry", "has space"), ("name", 7),
], ids=["name-traversal", "bundle-slash", "digest-uppercase",
        "registry-control", "name-not-a-string"])
def test_entry_validation_matches_the_hooks(field, value):
    """The doctor must reject exactly what the pruner rejects.

    Accepting an entry the hook skips would report a skill as hook-owned and
    removable when the hook will in fact leave it in place forever.
    """
    entry = {"name": "alpha", "registry": REGISTRY_URL,
             "bundle": "adam", "digest": "a" * 64}
    entry[field] = value
    assert prov._entry(entry) is None, entry


# ---------------------------------------------------------------------------
# provenance as fact
# ---------------------------------------------------------------------------

def test_a_recorded_skill_is_attributed_to_its_registry_and_bundle(store, capsys):
    """"Personal copy" does not say which registry, which is what #78 asked for."""
    skills, lock = store
    code, out = run(skills, lock, capsys)

    assert code == 0, out
    assert "2 hook-installed" in flat(out), out
    for name in ("alpha", "beta"):
        row = next(line for line in out.splitlines() if line.strip().startswith(name))
        assert REGISTRY_URL in row, row
        assert "adam" in row, row
        assert "hook" in row, row


def test_a_skill_the_record_does_not_name_is_not_attributed(store, capsys):
    """The hand-placed case must not inherit the neighbours' attribution."""
    skills, lock = store
    make_skill(skills, "mine")
    code, out = run(skills, lock, capsys)

    assert code == 1, out
    assert "1 unattributed" in flat(out), out
    row = next(line for line in out.splitlines() if line.strip().startswith("mine"))
    assert "unattributed" in row, row
    assert REGISTRY_URL not in row, row


def test_untouched_and_edited_are_told_apart_by_the_recorded_digest(store, capsys):
    """The precise answer the mtime cluster could not give.

    An editor touching a hook-installed skill is exactly the case the heuristic
    got backwards — it fell out of the cluster and read as hand-placed. Here it
    stays attributed and is reported as edited, which is both facts at once.
    """
    skills, lock = store
    (skills / "alpha" / "SKILL.md").write_text("edited\n", encoding="utf-8")
    _, out = run(skills, lock, capsys)

    assert "edited since install" in flat(out), out
    assert "unchanged since install" in flat(out), out
    alpha = [line for line in out.splitlines() if "edited since install" in line]
    assert len(alpha) == 1, out


def test_a_directory_that_cannot_be_measured_is_not_called_edited(
        tmp_path, monkeypatch, capsys):
    """Unmeasurable and edited are different claims about the user's machine.

    Reporting "edited" for a directory nobody could read would be a guess dressed
    as a measurement — and it would accuse the user of changing something they
    did not touch. Driven by monkeypatching the read rather than by `chmod`,
    which is a no-op for root and would make this test pass without ever
    exercising the path.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    real_read = Path.read_bytes

    def unreadable(self):
        if self.name == "SKILL.md":
            raise OSError("simulated read failure")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    _, out = run(store, lock, capsys)

    assert "unmeasurable since install" in flat(out), out
    assert "edited since install" not in flat(out), out
    # And the FINDING must not smuggle the accusation back in. It shares a
    # consequence with `edited` — the hook treats both the same — which makes it
    # easy to share the cause sentence too, and that sentence would be a
    # measurement nobody took.
    assert "[unmeasurable-and-locked] alpha" in flat(out), out
    assert "could not be read" in flat(out), out
    assert "no longer the ones the hook verified" not in flat(out), out


# ---------------------------------------------------------------------------
# the slug-versus-URL trap
# ---------------------------------------------------------------------------

def test_a_lock_slug_matches_a_record_url(tmp_path, capsys):
    """`OWNER/REPO` in the lock is `https://github.com/OWNER/REPO.git` in the record.

    Comparing them with `==` makes every hook-installed skill look like it came
    from a registry its own lock does not declare, which routes every stale skill
    into "nothing will ever clean this up". The symptom is a doctor that is
    alarming and wrong on a perfectly healthy machine.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "beta")
    write_record(store, "alpha", "beta")
    # beta has left the lock; alpha keeps the lock non-empty.
    lock = write_lock(tmp_path / "skills.lock", store, "alpha", registry=REGISTRY_SLUG)

    code, out = run(store, lock, capsys)
    assert (REGISTRY_URL, "adam") in prov.read_lock(lock).claims
    assert "[stale] beta" in flat(out), out
    assert "stale-out-of-scope" not in out, out
    assert code == 0, out


def test_remote_url_leaves_an_explicit_url_alone(tmp_path):
    assert prov.remote_url("file:///tmp/registry") == "file:///tmp/registry"
    assert prov.remote_url("OWNER/REPO") == "https://github.com/OWNER/REPO.git"
    assert prov.remote_url("not a registry") is None
    assert prov.remote_url(None) is None


# ---------------------------------------------------------------------------
# the findings
# ---------------------------------------------------------------------------

def test_a_skill_in_neither_the_lock_nor_the_record_is_a_finding(store, capsys):
    """#78's headline case: the hook is right to leave it, so a human must see it.

    It is invisible everywhere else — no verdict mentions it, no lock names it,
    and it will sit there through every future session.

    The finding's text is scoped to THE LOCK THAT RAISED IT rather than to the
    hook in general, and that is a correction rather than a hedge. In a
    multi-repo session `classify` runs once per lock, so a name that lock A
    declares and lock B does not raises B's `untracked` — and the old wording,
    "the hook will never update it and never remove it", is then false on its
    face: lock A's judgement of the same directory is `hand-placed-over-locked`,
    which says the next session start REPLACES it. Two findings, one directory,
    flatly contradicting each other. Only one lock was ever observed to be in
    play when the sentence was written, so it now says only what that one lock
    can support.
    """
    skills, lock = store
    make_skill(skills, "hand-copied")
    code, out = run(skills, lock, capsys)

    assert code == 1, out
    assert "[untracked] hand-copied" in flat(out), out
    assert "the lock this was judged against" in flat(out), out
    # It must not make the unqualified claim again. A single lock is still only
    # a single lock's reading.
    assert "The hook will never update it" not in flat(out), out


def test_untracked_does_not_contradict_another_locks_verdict_on_the_same_name(
        tmp_path, capsys, monkeypatch):
    """Divergent multi-repo locks make an absolute claim about "the hook" false.

    `classify` runs once per lock and names no winner — deliberately, since
    picking one would answer ADR 0005's first open question by being convenient.
    The cost is that two locks can reach opposite conclusions about one
    directory, and until now the text did not admit it: lock A names `shared`,
    so A reports `hand-placed-over-locked` — "will not survive the next session
    start, which replaces the directory". Lock B does not name it, so B reported
    `untracked` — "the hook will never update it and never remove it". Both in
    one findings list, about one directory, in direct contradiction.

    Latent while every lock in the fleet declared the same names (9 = 9, empty
    symmetric difference) and live the moment they diverge, which ADR 0005 notes
    they can. The fix is in the text, not the judgement: `untracked` now says
    what the lock that raised it can support, and the LOCK line says which lock
    that was.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "shared")
    # A record that is PRESENT and does not name it — the state that makes the
    # directory attributable-to-nobody rather than merely unknown, which is what
    # separates the two contradicting findings from a single quiet one.
    write_record(store)

    project = tmp_path / "repos"
    # A names `shared`; B does not.
    _repo_with_lock(project, "repo-a", "shared")
    _repo_with_lock(project, "repo-b", "other")

    code, out = run_autolock(store, project, capsys)
    assert code == 1, out
    flattened = flat(out)
    # Both readings are still reported — suppressing either would be the merge
    # this deliberately does not do.
    assert "[untracked] shared" in flattened, out
    assert "[hand-placed-over-locked] shared" in flattened, out
    # ...and the untracked one no longer asserts what the other one disproves.
    assert "The hook will never update it" not in flattened, out
    assert "another lock may name it" in flattened, out


def test_the_untracked_finding_still_fires_without_a_record(tmp_path, capsys):
    """Same directory, same permanence, weaker evidence.

    Withholding it here would mute the doctor precisely where it knows least,
    which is the failure mode the whole issue is about.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "hand-copied")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[untracked] hand-copied" in flat(out), out
    assert "no readable record" in flat(out), out


def test_a_hand_placed_copy_of_a_locked_skill_is_a_finding(tmp_path, capsys):
    """Not "untracked": the next bootstrap overwrites this one in place."""
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "beta")
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha", "beta")

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[hand-placed-over-locked] beta" in flat(out), out


def test_editing_a_locked_skill_warns_that_the_edit_is_overwritten(store, capsys):
    skills, lock = store
    (skills / "alpha" / "SKILL.md").write_text("mine now\n", encoding="utf-8")
    code, out = run(skills, lock, capsys)

    assert code == 1, out
    assert "[edited-and-locked] alpha" in flat(out), out


def test_an_edited_skill_that_left_the_lock_is_kept_by_the_mismatch(tmp_path, capsys):
    """What preserves it is the digest MISMATCH, not having left the lock.

    The hook compares against the ORIGINAL install digest, so reverting an
    experimental edit byte-for-byte hands the directory straight back to the
    pruner. Calling it safe "forever" is how somebody's work gets deleted one run
    after they tidy up — so the claim is asserted here AND demonstrated, by
    restoring the bytes and watching the same skill become removable.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    original = make_skill(store, "beta") / "SKILL.md"
    kept = original.read_text(encoding="utf-8")
    write_record(store, "alpha", "beta")
    original.write_text("mine now\n", encoding="utf-8")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[edited-and-stale] beta" in flat(out), out
    assert "what preserves it is the MISMATCH" in flat(out), out
    assert "restore the original bytes and the next run removes it" in flat(out), out

    original.write_text(kept, encoding="utf-8")
    code, restored = run(store, lock, capsys)
    assert "[stale] beta" in flat(restored), restored
    assert "edited-and-stale" not in restored, restored
    assert code == 0, restored


def test_an_edited_skill_out_of_claim_scope_reports_scope_not_ownership(
        tmp_path, capsys):
    """Out of scope the planner never consults the digest at all.

    It short-circuits to `keep`, so the verdict degrade that `edited-and-stale`
    promises never happens — and a reader sent looking for a signal the hook does
    not emit concludes the hook's own verdict is lying. Scope has to be decided
    before integrity, which is an ordering this test is the only thing pinning.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "orphan")
    write_record(store, "alpha", "orphan")
    record = json.loads((store / prov.RECORD_NAME).read_text(encoding="utf-8"))
    for entry in record["installed"]:
        if entry["name"] == "orphan":
            entry["bundle"] = "retired"
    (store / prov.RECORD_NAME).write_text(json.dumps(record, indent=2) + "\n",
                                          encoding="utf-8")
    (store / "orphan" / "SKILL.md").write_text("edited too\n", encoding="utf-8")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[stale-out-of-scope] orphan" in flat(out), out
    assert "edited-and-stale" not in out, out


def test_a_stale_skill_the_lock_still_claims_is_only_a_note(tmp_path, capsys):
    """All four removal conditions hold, so the next run handles it by itself."""
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "beta")
    write_record(store, "alpha", "beta")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "[stale] beta" in flat(out), out
    assert "FINDINGS (0)" in flat(out), out


def test_a_stale_skill_whose_bundle_left_the_lock_is_a_finding(tmp_path, capsys):
    """Removal is scoped per (registry, bundle) so two repos do not reap each
    other's installs. Out of scope therefore means nothing ever cleans it up.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "orphan")
    write_record(store, "alpha")
    write_record(store, "alpha", "orphan")
    record = json.loads((store / prov.RECORD_NAME).read_text(encoding="utf-8"))
    for entry in record["installed"]:
        if entry["name"] == "orphan":
            entry["bundle"] = "retired"
    (store / prov.RECORD_NAME).write_text(json.dumps(record, indent=2) + "\n",
                                          encoding="utf-8")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[stale-out-of-scope] orphan" in flat(out), out


def test_a_locked_skill_that_is_not_on_disk_is_a_finding(store, capsys):
    """The delivery failure that leaves no trace anywhere else."""
    skills, lock = store
    for child in sorted((skills / "beta").iterdir()):
        child.unlink()
    (skills / "beta").rmdir()

    code, out = run(skills, lock, capsys)
    assert code == 1, out
    assert "[missing] beta" in flat(out), out
    assert "1 not in the store" in flat(out), out
    # And it must not overclaim: this script cannot see the session's listing, so
    # it reports the absence and says where the answer actually lives.
    assert "never sees it" not in flat(out), out
    assert "session's own skill listing" in flat(out), out


def test_locked_skills_absent_from_a_store_the_hook_never_ran_on_are_notes(
        tmp_path, capsys):
    """On a durable machine the personal store is SUPPOSED to hold none of them.

    §1 of the skill says so: the marketplace install is authoritative there and
    hook-installed bundle skills in `~/.claude/skills` would be double delivery.
    Measured on the author's machine before this: nine locked skills, nine
    `[missing]` findings, exit 1 — three lines under a report that had just said
    "absent … Right on a durable machine". A findings list that is wrong on the
    ordinary case is one the reader learns to scroll past.
    """
    store = tmp_path / "skills"
    store.mkdir()
    lock = write_lock(tmp_path / "skills.lock", store)
    data = json.loads(lock.read_text(encoding="utf-8"))
    data["skills"] = {"adam/alpha": "a" * 64, "adam/beta": "b" * 64}
    lock.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "FINDINGS (0)" in flat(out), out
    assert "[not-in-the-store] alpha" in flat(out), out
    assert "[missing]" not in out, out
    # The headline count still says what is absent — it must not follow the
    # finding-versus-note judgement.
    assert "2 not in the store" in flat(out), out
    assert "surface durable" in flat(out), out


def test_locked_skills_absent_on_an_ephemeral_surface_are_findings(
        tmp_path, capsys, ephemeral):
    """The same three facts, the opposite verdict — #85 §1's promotion.

    Sibling of the test above, and the pair is the point: identical bytes on
    disk, identical lock, identical (absent) record, and the only difference is
    the machine. On a durable one the marketplace is authoritative and an empty
    personal store is right. On an ephemeral one the hook is the ONLY channel
    that delivers a locked bundle, so the same empty store is the delivery
    failure itself.

    This is the defect the issue reports verbatim: measured in a cloud session
    with nine locked skills undelivered, the doctor filed all nine as NOTES and
    exited 0 — answering "yes, healthy" in precisely the session where the
    answer was no.
    """
    store = tmp_path / "skills"
    store.mkdir()
    lock = write_lock(tmp_path / "skills.lock", store)
    data = json.loads(lock.read_text(encoding="utf-8"))
    data["skills"] = {"adam/alpha": "a" * 64, "adam/beta": "b" * 64}
    lock.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "FINDINGS (2)" in flat(out), out
    assert "[not-in-the-store] alpha" in flat(out), out
    assert "[not-in-the-store] beta" in flat(out), out
    # Matched on a sentence only the PROMOTED FINDING carries. "delivery
    # failure" alone would pass on the SURFACE block's own prose, which prints
    # on every ephemeral run whether or not anything was promoted — an
    # assertion that cannot fail is not one.
    assert "no hook run has ever finished here" in flat(out), out
    assert "surface ephemeral" in flat(out), out
    # NOTES must not carry them as well — one defect, reported once.
    assert "NOTES" not in out, out
    # And the headline count is unchanged by the promotion.
    assert "2 not in the store" in flat(out), out


@pytest.mark.parametrize("arm", [
    {"CLAUDE_CODE_REMOTE_SESSION_ID": "cse_deadbeef"},
    {"CLAUDE_CODE_ENTRYPOINT": "remote"},
    {"SKILLS_BOOTSTRAP_FORCE": "1"},
])
def test_every_arm_the_hook_installs_on_promotes_an_undelivered_skill(
        tmp_path, capsys, monkeypatch, arm):
    """A doctor narrower than the hook it diagnoses is silently wrong.

    `.claude/hooks/skills-bootstrap.sh` installs when a remote session id is
    set, OR `CLAUDE_CODE_ENTRYPOINT` is exactly `remote`, OR
    `SKILLS_BOOTSTRAP_FORCE` is set. Keying on the session id alone left the
    other two arms reading `unsure` and `durable` — the QUIET answers — so on a
    surface the hook installs onto, the doctor withheld every promotion and
    exited 0 over undelivered locked skills. That is #85's headline defect
    surviving on a surface this repo's own hook installs on, which is why each
    arm is asserted end to end rather than only on the reader.

    One arm per case deliberately: a single environment setting all three would
    still pass with two of them broken.
    """
    for name, value in arm.items():
        monkeypatch.setenv(name, value)

    store = tmp_path / "skills"
    store.mkdir()
    lock = write_lock(tmp_path / "skills.lock", store)
    data = json.loads(lock.read_text(encoding="utf-8"))
    data["skills"] = {"adam/alpha": "a" * 64, "adam/beta": "b" * 64}
    lock.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "surface ephemeral" in flat(out), out
    assert "FINDINGS (2)" in flat(out), out
    assert "[not-in-the-store] alpha" in flat(out), out
    assert "no hook run has ever finished here" in flat(out), out


def test_the_surface_reading_names_the_arm_that_decided_it(tmp_path, capsys,
                                                           monkeypatch):
    """`SKILLS_BOOTSTRAP_FORCE` alone reads ephemeral with the other two unset.

    The hook prints the values it judged from precisely so a MISCLASSIFIED
    surface is legible to whoever reads the transcript. A report that named only
    the entrypoint and the session id would, on this arm, print two unset
    readings above the word `ephemeral` and look like a contradiction — which is
    the same illegibility, one level down.
    """
    monkeypatch.setenv("SKILLS_BOOTSTRAP_FORCE", "1")
    store = tmp_path / "skills"
    store.mkdir()
    lock = write_lock(tmp_path / "skills.lock", store)

    _, out = run(store, lock, capsys)
    assert "surface ephemeral" in flat(out), out
    assert "SKILLS_BOOTSTRAP_FORCE=set" in flat(out), out
    assert "CLAUDE_CODE_ENTRYPOINT=(unset)" in flat(out), out
    assert "CLAUDE_CODE_REMOTE_SESSION_ID=(unset)" in flat(out), out


def test_the_widened_surface_still_leaves_a_durable_machine_alone(tmp_path,
                                                                  capsys):
    """The negative control for the three arms, and it is the load-bearing half.

    Widening the surface test is only correct if it widened where the hook
    installs and NOWHERE else. Without this, "the promotion fires now" would be
    indistinguishable from "the promotion fires always" — the second being a
    regression that turns every durable machine's correctly-empty store into a
    findings list. Identical bytes to the ephemeral cases above; only the
    environment differs, and `durable_surface` clears all three arms.
    """
    store = tmp_path / "skills"
    store.mkdir()
    lock = write_lock(tmp_path / "skills.lock", store)
    data = json.loads(lock.read_text(encoding="utf-8"))
    data["skills"] = {"adam/alpha": "a" * 64, "adam/beta": "b" * 64}
    lock.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "surface durable" in flat(out), out
    assert "FINDINGS (0)" in flat(out), out
    assert "[not-in-the-store] alpha" in flat(out), out


def test_the_promotion_needs_all_three_of_its_preconditions(tmp_path, capsys,
                                                            ephemeral):
    """Ephemeral AND record-absent AND a lock — drop any one and it stays quiet.

    Written against the promotion rather than around it. The interesting half is
    the record: an UNREADABLE record is not an absent one, and it already raises
    `record-unreadable` as a finding that names this same delivery gap once.
    Promoting there too would report one defect once per locked name, which is
    how a findings list stops being read.
    """
    store = tmp_path / "skills"
    store.mkdir()
    lock = write_lock(tmp_path / "skills.lock", store)
    data = json.loads(lock.read_text(encoding="utf-8"))
    data["skills"] = {"adam/alpha": "a" * 64}
    lock.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # All three hold: promoted.
    code, out = run(store, lock, capsys)
    assert code == 1 and "FINDINGS (1)" in flat(out), out

    # Record present but empty — a run COMPLETED and installed nothing, so the
    # locked name gets the `missing` finding that state already had, not this one.
    (store / prov.RECORD_NAME).write_text(
        json.dumps({"version": 1, "installed": []}), encoding="utf-8")
    _, present = run(store, lock, capsys)
    assert "[missing] alpha" in flat(present), present
    assert "no hook run has ever finished here" not in flat(present), present

    # Record unreadable — one finding for the record, and the locked name stays
    # a note rather than restating the same gap.
    (store / prov.RECORD_NAME).write_text("{ not json", encoding="utf-8")
    _, unreadable = run(store, lock, capsys)
    assert "[record-unreadable]" in unreadable, unreadable
    assert "[not-in-the-store] alpha" in flat(unreadable), unreadable
    assert "no hook run has ever finished here" not in flat(unreadable), unreadable

    # No lock at all — nothing was declared, so nothing fell short of it.
    (store / prov.RECORD_NAME).unlink()
    code, no_lock = run(store, tmp_path / "absent.lock", capsys)
    assert code == 0, no_lock
    assert "FINDINGS (0)" in flat(no_lock), no_lock


def test_an_unsure_surface_is_named_rather_than_rounded(tmp_path, capsys,
                                                        monkeypatch):
    """An entrypoint with no remote session id is a third state, not a shade.

    Judged as durable, because the conservative direction is to keep a note a
    note. Reported as `unsure`, because telling the reader "durable machine" on
    a reading nobody has classified is the same fabrication this script exists
    to stop making about the record.
    """
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "remote_cowork")
    store = tmp_path / "skills"
    store.mkdir()
    lock = write_lock(tmp_path / "skills.lock", store)
    data = json.loads(lock.read_text(encoding="utf-8"))
    data["skills"] = {"adam/alpha": "a" * 64}
    lock.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "surface unsure" in flat(out), out
    assert "[not-in-the-store] alpha" in flat(out), out
    assert "FINDINGS (0)" in flat(out), out


def test_the_surface_is_read_from_the_session_id_not_the_entrypoints_shape(
        monkeypatch):
    """`remote_cowork` may or may not be ephemeral, and this must not guess.

    A prefix match on `remote` is the fix that looks right: every ephemeral
    entrypoint measured so far starts with it. It is held deliberately (#85 §5)
    — the binary's own display classifier groups `remote_cowork` with
    `local-agent`, so "no durable entrypoint starts with remote" is unproven,
    and a doctor that assumed it would call a durable Cowork machine ephemeral
    and report its correctly-empty store as a delivery failure.

    Locked as a unit test on the reader rather than through a report, so the
    rule cannot be re-derived by someone reading only the rendered output.
    """
    assert prov.read_surface({"CLAUDE_CODE_ENTRYPOINT": "remote_cowork"})[0] \
        == prov.UNSURE
    assert prov.read_surface({"CLAUDE_CODE_ENTRYPOINT": "remote_mobile",
                              "CLAUDE_CODE_REMOTE_SESSION_ID": "cse_x"})[0] \
        == prov.EPHEMERAL
    # A durable CLI session sets an entrypoint and no session id, so the two
    # empties are the only durable reading this can assert.
    assert prov.read_surface({})[0] == prov.DURABLE
    # An empty string is not a session id. `os.environ` hands back exactly that
    # for `FOO=` and truthiness is what separates them.
    assert prov.read_surface({"CLAUDE_CODE_REMOTE_SESSION_ID": ""})[0] \
        == prov.DURABLE


def test_the_surface_test_is_the_hooks_own_three_arms():
    """Copied from `skills-bootstrap.sh`, and copied EXACTLY — no wider, no narrower.

    The sibling test above holds the widening that is NOT settled (the six
    `remote_*` spellings, where `remote_cowork` may well be durable). This one
    holds the three arms that ARE settled, because they are the hook's own: it
    installs on a remote session id, on the exact entrypoint `remote`, or on
    `SKILLS_BOOTSTRAP_FORCE`. Agreeing with the hook is not the same act as
    guessing past it, and only the second is on hold.

    Asserted on the reader as well as end to end, so the rule survives someone
    reading only the rendered output.
    """
    assert prov.read_surface({"CLAUDE_CODE_REMOTE_SESSION_ID": "cse_x"})[0] \
        == prov.EPHEMERAL
    assert prov.read_surface({"CLAUDE_CODE_ENTRYPOINT": "remote"})[0] \
        == prov.EPHEMERAL
    assert prov.read_surface({"SKILLS_BOOTSTRAP_FORCE": "1"})[0] == prov.EPHEMERAL
    # PRESENCE, not value. The hook tests `[ -z ... ]`, so `0` forces the
    # install too — reading the value here would disagree with it in the one
    # direction nobody would think to check.
    assert prov.read_surface({"SKILLS_BOOTSTRAP_FORCE": "0"})[0] == prov.EPHEMERAL
    # An empty string IS unset to `-z`, and so must be unset here.
    assert prov.read_surface({"SKILLS_BOOTSTRAP_FORCE": ""})[0] == prov.DURABLE
    # The EXACT value only: `remoteish` starts with `remote` and is not it, so a
    # prefix match — the widening on hold — fails this line.
    assert prov.read_surface({"CLAUDE_CODE_ENTRYPOINT": "remoteish"})[0] \
        == prov.UNSURE


def test_a_locked_skill_the_account_store_delivers_is_not_called_missing(
        tmp_path, capsys):
    """Measured on the author's own machine: this fired for real.

    `adam-writing-style` is in the lock, absent from the personal store, present
    in `synced/`, and in the session's listing — and the doctor said the session
    "simply never sees it". The two channels collide on a bare name by design,
    which is exactly why the doctor has to check the other one before concluding.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store / prov.ACCOUNT_DIR, "beta")
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")
    lock_data = json.loads(lock.read_text(encoding="utf-8"))
    lock_data["skills"]["adam/beta"] = "0" * 64
    lock.write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")

    code, out = run(store, lock, capsys)
    assert "[delivered-by-the-account-store] beta" in flat(out), out
    assert "[missing] beta" not in flat(out), out
    assert code == 0, out


def test_a_locked_skill_the_project_ships_is_not_called_missing(tmp_path, capsys):
    """The hook removes its own copy here ON PURPOSE, so repo-owned wins.

    Reporting the result of that as a delivery failure inverts it: the session
    does see the skill, and it sees the copy the project intended.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    project = tmp_path / "project"
    make_skill(project / ".claude" / "skills", "beta")
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")
    lock_data = json.loads(lock.read_text(encoding="utf-8"))
    lock_data["skills"]["adam/beta"] = "0" * 64
    lock.write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")

    code, out = run(store, lock, capsys, project_dir=project)
    assert "[delivered-by-the-project] beta" in flat(out), out
    assert "[missing] beta" not in flat(out), out
    assert code == 0, out


def test_a_missing_skill_absent_from_a_healthy_record_names_both_causes(
        tmp_path, capsys):
    """A readable record that omits a locked name has TWO readings, not one.

    The record carries no ref and no timestamp on purpose, so nothing in it can
    be dated against the current lock — and this fixture is the counter-example
    to the single-cause version: it adds a skill to the lock AFTER the record was
    written, which is this repo's own workflow (the lock is regenerated in a
    commit of its own). Nothing failed to install; the last run never saw it.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")
    lock_data = json.loads(lock.read_text(encoding="utf-8"))
    lock_data["skills"]["adam/never-arrived"] = "0" * 64
    lock.write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[missing] never-arrived" in flat(out), out
    assert "the last hook run never saw it" in flat(out), out
    assert "could not install it" in flat(out), out
    assert "undated by design" in flat(out), out


# ---------------------------------------------------------------------------
# what may not be judged
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("corrupt", [None, "{ not json", "[]"],
                         ids=["absent", "truncated", "not-an-object"])
def test_no_readable_lock_means_nothing_is_called_stale_or_missing(
        tmp_path, capsys, corrupt):
    """With no declared expectation there is nothing to fall short of.

    Judging against an empty set reports the entire store as stale, which is the
    loudest possible way to be wrong. An unreadable lock has to reach the same
    conclusion as a missing one — it is equally silent about what was expected.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "beta")
    write_record(store, "alpha", "beta")
    lock = tmp_path / "skills.lock"
    if corrupt is not None:
        lock.write_text(corrupt, encoding="utf-8")

    code, out = run(store, lock, capsys)
    # Nothing about the SKILLS may be judged. A lock that is there and unusable
    # is itself reported — it is being relied on and delivers nothing — but an
    # absent one is simply a machine this script cannot verdict on.
    assert "[stale" not in out, out
    assert "[untracked]" not in out, out
    assert "[missing]" not in out, out
    # And the rows must not claim a lock omitted them.
    assert "not in lock" not in flat(out), out
    if corrupt is None:
        assert "lock absent" in flat(out), out
        assert "FINDINGS (0)" in flat(out), out
        assert code == 0, out
    else:
        assert "lock unreadable" in flat(out), out
        assert "[lock-unreadable]" in out, out
        assert code == 1, out


@pytest.mark.parametrize("mutate, because", [
    (lambda lock: lock.pop("bundles"), "non-empty list of bundle names"),
    (lambda lock: lock.update(bundles=[]), "non-empty list of bundle names"),
    (lambda lock: lock.update(skills={"nobody/foo": "a" * 64}), "no source claims"),
    (lambda lock: lock.update(registry="not a registry"), "not OWNER/REPO"),
], ids=["no-bundles", "empty-bundles", "unclaimed-bundle", "bad-registry"])
def test_a_lock_the_hook_refuses_is_reported_rather_than_judged_against(
        tmp_path, capsys, mutate, because):
    """Parsing as JSON is not the same as being a lock the hook will use.

    Judging against one of these produces findings whose stated cause never
    happened — measured: a lock with `bundles` deleted yielded
    `[stale-out-of-scope] alpha … removal is scoped to what the lock claims`,
    while the hook refused the file outright and installed and removed nothing.
    The real defect, that this machine gets ZERO delivery, went unreported.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")
    data = json.loads(lock.read_text(encoding="utf-8"))
    mutate(data)
    lock.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    parsed = prov.read_lock(lock)
    assert parsed.state == prov.REJECTED, parsed
    assert because in (parsed.reason or ""), parsed.reason

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[lock-rejected]" in out, out
    assert "installs nothing from it at all" in flat(out), out
    # And nothing may be JUDGED against a lock that never applies. Matched on the
    # finding marker, not the bare word — the LOCK section's own explanation says
    # "nothing can be called stale or missing", and asserting on that sentence
    # would make the test pass or fail on prose rather than on a verdict.
    assert "[stale" not in out, out
    assert "[untracked]" not in out, out
    assert "[missing]" not in out, out


def test_the_verdict_does_not_report_zero_of_everything_when_unattributable(
        tmp_path, capsys):
    """"3 on disk, 0 hook-installed, 0 unattributed" reads as a contradiction.

    At best it stalls the reader; at worst it reads as "the store is empty" and
    the rest of the report goes unread.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "beta")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha", "beta")

    _, out = run(store, lock, capsys)
    verdict = out.splitlines()[0]
    assert "2 unattributable (no readable record)" in verdict, verdict
    assert "0 hook-installed" not in verdict, verdict


def test_a_store_that_does_not_exist_is_not_reported_as_an_empty_one(
        tmp_path, capsys):
    """A green all-clear about a path nobody read is the worst output here.

    With a lock it was worse than empty: every locked skill became "declared by
    the lock and not in the personal store" — an assertion about a disk that was
    never opened. Same class of error as conflating an absent record with an
    unreadable one, in the one place the script had not applied its own rule.
    """
    lock = write_lock(tmp_path / "skills.lock", tmp_path / "nowhere")
    data = json.loads(lock.read_text(encoding="utf-8"))
    data["skills"] = {"adam/alpha": "a" * 64}
    lock.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    state, names = prov.scan(tmp_path / "nowhere")
    assert (state, names) == (prov.ABSENT, []), (state, names)

    _, out = run(tmp_path / "nowhere", lock, capsys)
    assert "the directory does not exist" in flat(out), out
    assert "[missing] alpha" not in flat(out), out


def test_a_store_that_cannot_be_read_is_a_finding(tmp_path, monkeypatch, capsys):
    """Unreadable is not empty, and an empty FINDINGS list must not imply clean.

    Driven by monkeypatching `iterdir` rather than by `chmod`, which is a no-op
    for root and would let this pass without exercising the path at all.

    The record is written deliberately: without it nothing is attributable, the
    absent-store branch answers first, and the `[missing]` assertion below is
    true whether or not the guard exists — measured, that is exactly how this
    test survived a mutation sweep on its first draft.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    real_iterdir = Path.iterdir

    def denied(self):
        if self == store:
            raise PermissionError("simulated")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", denied)
    assert prov.scan(store) == (prov.UNREADABLE, [])

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[store-unreadable]" in out, out
    assert "could not be read" in flat(out), out
    # And it must not report the skills it never saw as absent from the store.
    assert "[missing] alpha" not in flat(out), out


def test_the_account_store_is_not_reported_as_a_skill(store, capsys):
    """`synced/` is the claude.ai channel's own directory, manifest-gated.

    Scanning it in would report the account store as an untracked skill on every
    machine that has one — a finding that is always wrong and always present.
    """
    skills, lock = store
    make_skill(skills / prov.ACCOUNT_DIR, "account-only")
    code, out = run(skills, lock, capsys)

    assert code == 0, out
    assert "account-only" not in out, out
    assert "2 on disk" in flat(out), out


def test_the_record_itself_is_not_reported_as_a_skill(store, capsys):
    """It lives in the directory it describes, as does its staging file."""
    skills, lock = store
    (skills / ".skills-bootstrap-installed.abc123.tmp").mkdir()
    code, out = run(skills, lock, capsys)

    assert code == 0, out
    assert prov.RECORD_NAME not in out.split("SKILLS")[-1], out
    assert "2 on disk" in flat(out), out


# ---------------------------------------------------------------------------
# the fallback
# ---------------------------------------------------------------------------

def test_the_mtime_fallback_clusters_only_when_there_is_no_record(store, capsys):
    """It is the fallback, not a second opinion.

    Printing a cluster next to a record that already answered would invite the
    reader to weigh a heuristic against the fact that replaced it.
    """
    skills, lock = store
    _, with_record = run(skills, lock, capsys)
    assert "INFERENCE" not in with_record, with_record

    (skills / prov.RECORD_NAME).unlink()
    _, without = run(skills, lock, capsys)
    assert "INFERENCE" in without, without
    assert "is not evidence of one" in flat(without), without

    # It applies in the UNREADABLE state too — and there its header must not say
    # "no record" twenty lines under a RECORD block insisting the file is there.
    (skills / prov.RECORD_NAME).write_text("{ not json", encoding="utf-8")
    _, corrupt = run(skills, lock, capsys)
    assert "INFERENCE" in corrupt, corrupt
    assert "the record cannot answer" in flat(corrupt), corrupt
    assert "INFERENCE — no record" not in flat(corrupt), corrupt


def test_the_clusters_split_on_the_gap_not_the_clock(tmp_path):
    """Deterministic by construction: mtimes are set, never waited for."""
    import os

    store = tmp_path / "skills"
    store.mkdir()
    for name in ("one", "two", "far"):
        make_skill(store, name)
    base = 1_000_000.0
    for name, when in (("one", base), ("two", base + 5), ("far", base + 5_000)):
        for path in sorted((store / name).rglob("*")):
            os.utime(path, (when, when))

    stamped = [(name, prov.newest_mtime(store / name))
               for name in ("one", "two", "far")]
    clusters = prov.cluster(stamped)
    assert [[name for name, _ in group] for group in clusters] == [["one", "two"], ["far"]]


# ---------------------------------------------------------------------------
# exit codes
# ---------------------------------------------------------------------------

def test_exit_is_zero_on_a_clean_store_and_one_on_a_finding(store, capsys):
    """1 means "there are findings", which is a doctor's ordinary output.

    Asserted together so that a change making everything a finding — or nothing
    one — cannot pass by moving both.
    """
    skills, lock = store
    clean, _ = run(skills, lock, capsys)
    make_skill(skills, "hand-copied")
    dirty, _ = run(skills, lock, capsys)

    assert clean == 0
    assert dirty == 1


# ---------------------------------------------------------------------------
# against the hook itself
# ---------------------------------------------------------------------------

def _suite():
    """The registry's hook-driving test helpers."""
    _walk_up("scripts/test_generate_skills_lock.py")
    import test_generate_skills_lock

    return test_generate_skills_lock


@pytest.mark.parametrize("mutate", [
    lambda lock: lock.pop("bundles"),
    lambda lock: lock.update(bundles=[]),
    lambda lock: lock.update(registry="not a registry"),
], ids=["no-bundles", "empty-bundles", "bad-registry"])
def test_a_lock_this_rejects_is_one_the_hook_rejects_too(tmp_path, mutate):
    """The binding for the SECOND copy of the hook's lock validation in this file.

    `read_lock` re-implements the subset of the hook's reader that decides what
    this script may conclude, and a hand-written copy with nothing holding it to
    the original drifts silently — the exact failure `digest_skill_dir`'s binding
    test exists to prevent. So: every lock this calls REJECTED must be one the
    real hook also refuses, asserted against the hook's own verdict rather than
    against a second opinion written here.
    """
    suite = _suite()
    root = tmp_path / "registry"
    sha = suite.make_registry(root, {"adam/alpha": suite.SKILL_A})
    project = tmp_path / "project"
    lock_path = suite.make_project(project, root, sha)
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    mutate(data)
    lock_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    assert prov.read_lock(lock_path).state == prov.REJECTED

    proc = suite._run_hook(tmp_path / "home", project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = suite._verdict(proc)
    assert "DEGRADED" in verdict, verdict
    assert "could not read" in verdict, verdict
    # And nothing was installed, which is the consequence the finding states.
    assert not (tmp_path / "home" / ".claude" / "skills" / "alpha").exists(), verdict


def test_the_doctor_reads_the_record_the_hook_actually_writes(tmp_path):
    """The format-drift guard, and the only test that is not writing its own record.

    Everything above builds the record from this file's own idea of the hook's
    shape, so all of it would keep passing if the hook changed that shape — and
    the doctor would report a fully healthy machine as entirely unattributed.
    This drives the real hook against a `file://` fixture registry and a scratch
    HOME, then attributes what it actually installed.
    """
    suite = _suite()
    root = tmp_path / "registry"
    sha = suite.make_registry(root, {"adam/alpha": suite.SKILL_A,
                                     "adam/beta": suite.SKILL_B})
    project = tmp_path / "project"
    lock = suite.make_project(project, root, sha)
    home = tmp_path / "home"
    proc = suite._run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr

    skills = home / ".claude" / "skills"
    assert (skills / prov.RECORD_NAME).is_file(), "the hook wrote no record to read"

    record = prov.read_record(skills / prov.RECORD_NAME)
    assert record.state == prov.PRESENT, record
    assert sorted(record.entries) == ["alpha", "beta"], record
    for entry in record.entries.values():
        assert entry.bundle == "adam", entry
        assert entry.registry == root.resolve().as_uri(), entry

    store_state, names = prov.scan(skills)
    assert store_state == prov.PRESENT
    parsed = prov.read_lock(lock)
    rows, findings, notes = prov.classify(
        skills, names, record, parsed,
        prov.assign_origins(skills, names, record, parsed.names))
    assert [(row.name, row.origin, row.integrity) for row in rows] == [
        ("alpha", prov.HOOK, prov.UNCHANGED), ("beta", prov.HOOK, prov.UNCHANGED)]
    assert findings == [], findings
    assert notes == [], notes


# ---------------------------------------------------------------------------
# the parts a coarse mutation sweep leaves free
#
# Each of the following survived a finer sweep than the author's first one:
# a boundary, a constant, an `is_dir` guard, a rationale stated in a docstring
# and bound by nothing. They are cheap to write and they are exactly the shape
# of thing a "0 survivors" run gives false confidence about.
# ---------------------------------------------------------------------------

def test_newest_mtime_is_the_newest_file_not_the_oldest(tmp_path):
    """The docstring's whole reason for not using the directory's own mtime.

    `cp -R` stamps every file with the copy time, but a later edit moves only the
    file it touched — so the newest file is what says when the directory was last
    written. `min` and "the directory's mtime" both look right on a fixture whose
    files all share one timestamp, which is what the clustering test builds.
    """
    import os

    skill = make_skill(tmp_path, "alpha")
    (skill / "later.md").write_text("edited afterwards\n", encoding="utf-8")
    os.utime(skill / "SKILL.md", (1_000_000, 1_000_000))
    os.utime(skill / "later.md", (2_000_000, 2_000_000))

    assert prov.newest_mtime(skill) == 2_000_000


def test_newest_mtime_falls_back_to_the_directory_for_an_empty_one(tmp_path):
    """An empty directory has no file to date it by, and None would drop it out
    of the fallback entirely — silently shrinking the only evidence that section
    has."""
    import os

    empty = tmp_path / "empty"
    empty.mkdir()
    os.utime(empty, (1_500_000, 1_500_000))
    assert prov.newest_mtime(empty) == 1_500_000


def test_clusters_chain_from_the_previous_member_not_the_first(tmp_path):
    """One `cp -R` loop writes in sequence, so a slow run is a chain of small
    gaps rather than one wide one. Measuring from the cluster's FIRST member
    splits a single install into several, and every extra cluster reads as
    another provenance.
    """
    stamped = [("one", 0.0), ("two", 40.0), ("three", 80.0), ("far", 200.0)]
    assert [[n for n, _ in group] for group in prov.cluster(stamped)] == [
        ["one", "two", "three"], ["far"]]


def test_a_gap_exactly_at_the_window_still_clusters(tmp_path):
    """The boundary is inclusive. Stated because `<` and `<=` are equally
    plausible readings of a constant named GAP, and nothing else pins it."""
    assert len(prov.cluster([("a", 0.0), ("b", prov.MTIME_CLUSTER_GAP)])) == 1
    assert len(prov.cluster([("a", 0.0), ("b", prov.MTIME_CLUSTER_GAP + 1)])) == 2


def test_a_plain_file_in_the_store_is_not_a_skill(tmp_path, capsys):
    """A skill is a directory. A stray file — a README, a leftover archive —
    reported as an untracked skill is a finding about something that could never
    have been delivered."""
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    (store / "notes.txt").write_text("mine\n", encoding="utf-8")
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    assert prov.scan(store) == (prov.PRESENT, ["alpha"])
    code, out = run(store, lock, capsys)
    assert "notes.txt" not in out, out
    assert code == 0, out


def test_an_empty_directory_does_not_count_as_account_delivery(tmp_path, capsys):
    """The SKILL.md test in `skill_names` is what makes "the account store has a
    copy" mean it. Without it an empty or incidental directory downgrades a real
    `[missing]` finding to a note, and the doctor stops reporting a delivery
    failure on the strength of a directory with nothing in it.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    (store / prov.ACCOUNT_DIR / "beta").mkdir(parents=True)
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")
    data = json.loads(lock.read_text(encoding="utf-8"))
    data["skills"]["adam/beta"] = "0" * 64
    lock.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    code, out = run(store, lock, capsys)
    assert "[missing] beta" in out, out
    assert "delivered-by-the-account-store" not in out, out
    assert code == 1, out


def test_a_locked_skill_the_record_names_is_not_told_it_failed_to_install(
        tmp_path, capsys):
    """The two-cause sentence belongs only to a skill the record does NOT name.

    Appended unconditionally it is a statement about the user's machine that is
    simply false — the record names it, so nothing suggests the last run failed
    on it — and no assertion elsewhere looks at the suffix.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "beta")
    write_record(store, "alpha", "beta")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha", "beta")
    for child in sorted((store / "beta").iterdir()):
        child.unlink()
    (store / "beta").rmdir()

    code, out = run(store, lock, capsys)
    assert "[missing] beta" in out, out
    assert "the last hook run never saw it" not in flat(out), out
    assert "could not install it" not in flat(out), out
    assert code == 1, out


def test_a_locked_skill_with_no_record_produces_no_finding_of_its_own(
        tmp_path, capsys):
    """Present, expected, and unattributable is not a defect — it is every skill
    on a machine whose record was deleted. A finding here would fire once per
    skill and drown the ones that matter."""
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert "FINDINGS (0)" in out, out
    assert code == 0, out


# ---------------------------------------------------------------------------
# federated sources — the reason the lock format has them
# ---------------------------------------------------------------------------

def test_a_federated_lock_claims_every_source_it_declares(tmp_path):
    """A consumer federating two registries is the case the `sources` array
    exists for. Read only the primary and every skill from the other registry
    falls out of claim scope — flipping a benign `[stale]` note into
    "nothing here will ever clean it up" on a healthy machine.
    """
    lock = tmp_path / "skills.lock"
    lock.write_text(json.dumps({
        "registry": REGISTRY_SLUG, "ref": "a" * 40, "bundles": ["adam"],
        "sources": [{"registry": "Adam-S-Daniel/cms-platform", "ref": "b" * 40,
                     "bundles": ["cms-platform"], "layout": "skills"}],
        "skills": {"adam/alpha": "a" * 64, "cms-platform/beta": "b" * 64},
    }, indent=2) + "\n", encoding="utf-8")

    parsed = prov.read_lock(lock)
    assert parsed.state == prov.PRESENT, parsed
    assert parsed.claims == {
        (REGISTRY_URL, "adam"),
        ("https://github.com/Adam-S-Daniel/cms-platform.git", "cms-platform"),
    }, parsed.claims
    assert parsed.names == {"alpha", "beta"}, parsed.names


def test_a_bundle_two_sources_claim_is_rejected(tmp_path):
    """Routing is TOTAL in the hook: a bundle has one registry and one layout.

    Only reachable through `sources` — a primary listing the same bundle twice
    resolves to the same position and is not a collision with itself.
    """
    lock = tmp_path / "skills.lock"
    lock.write_text(json.dumps({
        "registry": REGISTRY_SLUG, "ref": "a" * 40, "bundles": ["adam"],
        "sources": [{"registry": "Adam-S-Daniel/cms-platform", "ref": "b" * 40,
                     "bundles": ["adam"], "layout": "skills"}],
        "skills": {"adam/alpha": "a" * 64},
    }, indent=2) + "\n", encoding="utf-8")

    parsed = prov.read_lock(lock)
    assert parsed.state == prov.REJECTED, parsed
    assert "claimed by two sources" in (parsed.reason or ""), parsed.reason


def test_a_primary_listing_one_bundle_twice_is_not_a_collision(tmp_path):
    """The other half of the same rule, and the reason it is keyed by POSITION
    rather than by name alone."""
    lock = tmp_path / "skills.lock"
    lock.write_text(json.dumps({
        "registry": REGISTRY_SLUG, "ref": "a" * 40, "bundles": ["adam", "adam"],
        "skills": {"adam/alpha": "a" * 64},
    }, indent=2) + "\n", encoding="utf-8")

    assert prov.read_lock(lock).state == prov.PRESENT


def test_a_lock_whose_skills_are_not_a_mapping_reads_as_empty_not_a_crash(tmp_path):
    """A hand-edited lock is the shape this has to survive; a traceback here
    would replace the whole report."""
    lock = tmp_path / "skills.lock"
    lock.write_text(json.dumps({
        "registry": REGISTRY_SLUG, "ref": "a" * 40, "bundles": ["adam"],
        "skills": ["adam/alpha"],
    }, indent=2) + "\n", encoding="utf-8")

    parsed = prov.read_lock(lock)
    assert parsed.state == prov.PRESENT, parsed
    assert parsed.names == set(), parsed


def test_a_record_entry_with_a_non_string_registry_is_skipped(tmp_path):
    """Every field is type-checked, not just the one a test happened to probe."""
    assert prov._entry({"name": "alpha", "registry": 7,
                        "bundle": "adam", "digest": "a" * 64}) is None


def test_an_environmental_open_failure_does_not_invent_a_corrupt_record(
        tmp_path, monkeypatch):
    """Out of file descriptors, `open` fails before the path is resolved.

    Classified on the exception alone, a record that does not exist came back as
    UNREADABLE — the report then asserted "the file is there" about a file that
    is not, and prescribed a clean session for a machine the hook has never run
    on. That is precisely the absent-versus-unreadable conflation this script
    was written to end, arriving through the back door.
    """
    def exhausted(*args, **kwargs):
        raise OSError(24, "Too many open files")

    monkeypatch.setattr("builtins.open", exhausted)
    assert prov.read_record(tmp_path / "nope.json").state == prov.ABSENT

    present = tmp_path / prov.RECORD_NAME
    monkeypatch.undo()
    present.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("builtins.open", exhausted)
    assert prov.read_record(present).state == prov.UNREADABLE


# ---------------------------------------------------------------------------
# lock auto-discovery — the multi-repo session the default could not see
# ---------------------------------------------------------------------------

def run_autolock(skills_dir: Path, project_dir: Path, capsys) -> Tuple[int, str]:
    """`run`, but with `--lock` OMITTED so discovery is what resolves it.

    Still explicit about the other two paths, for the reason the module
    docstring gives. `--lock` is the one under test here, and it is exactly the
    argument whose default used to resolve to nothing and report that as health.
    """
    assert tempfile.gettempdir() in str(project_dir) or "pytest" in str(project_dir), (
        f"project dir {project_dir} is outside the test's own tmp tree")
    code = prov.main(["--skills-dir", str(skills_dir),
                      "--project-dir", str(project_dir)])
    return code, capsys.readouterr().out


def _repo_with_lock(parent: Path, name: str, *skills: str, hook: bool = False) -> Path:
    """A child repo carrying a `skills.lock`, and optionally a wired hook."""
    repo = parent / name
    repo.mkdir(parents=True, exist_ok=True)
    lock = write_lock(repo / prov.LOCK_NAME, repo)
    data = json.loads(lock.read_text(encoding="utf-8"))
    data["skills"] = {f"adam/{skill}": "a" * 64 for skill in skills}
    lock.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if hook:
        claude = repo / ".claude"
        claude.mkdir(exist_ok=True)
        (claude / "settings.json").write_text(json.dumps({
            "hooks": {"SessionStart": [{
                "matcher": "startup|resume",
                "hooks": [{"type": "command",
                           "command": 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/'
                                      'skills-bootstrap.sh"',
                           "timeout": 90}],
            }]},
        }, indent=2) + "\n", encoding="utf-8")
    return repo


def test_the_default_lock_finds_the_repos_one_level_down(tmp_path, capsys,
                                                         ephemeral):
    """#85 §1's ordinary repro: `cd <parent> && check_provenance.py`.

    Measured verbatim before this landed, in a cloud session with seven repos
    under the project dir, three of them carrying a lock AND a bootstrap hook,
    none of their skills installed and no install record:

        LOCK     — absent
        FINDINGS (0)
        EXIT=0

    The bare default `skills.lock` resolved to nothing at the parent, and the
    absence of a lock was reported as though it were the absence of a problem.
    A diagnostic that answers "healthy" in exactly the session where the answer
    is no is worse than no diagnostic, because it ends the investigation.
    """
    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    project.mkdir()
    _repo_with_lock(project, "alpha-repo", "alpha")
    _repo_with_lock(project, "beta-repo", "beta")

    code, out = run_autolock(store, project, capsys)
    assert code == 1, out
    assert "2 locks were discovered" in flat(out), out
    assert str(project / "alpha-repo" / prov.LOCK_NAME) in out, out
    assert str(project / "beta-repo" / prov.LOCK_NAME) in out, out
    # Both undelivered skills are findings, each attributed to its own lock —
    # the report names no winner among the locks.
    assert "[not-in-the-store] alpha" in flat(out), out
    assert "[not-in-the-store] beta" in flat(out), out
    assert f"declared by {project / 'alpha-repo' / prov.LOCK_NAME}" in flat(out), out
    assert "2 not in the store" in flat(out), out


def test_a_project_dirs_own_lock_wins_over_scanning_its_children(tmp_path,
                                                                 capsys):
    """One repo is the ordinary case and must not become a scan.

    A repo that has its own lock has said what it expects; sweeping its
    subdirectories as well would judge its store against locks belonging to
    vendored checkouts it merely contains.
    """
    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repo"
    project.mkdir()
    write_lock(project / prov.LOCK_NAME, store)
    _repo_with_lock(project, "vendored", "should-not-be-read")

    code, out = run_autolock(store, project, capsys)
    assert code == 0, out
    assert "locks were discovered" not in flat(out), out
    assert "should-not-be-read" not in out, out
    assert str(project / prov.LOCK_NAME) in out, out


def test_an_explicit_lock_is_never_widened_into_a_scan(tmp_path, capsys):
    """Naming a file is a statement about which expectation is meant."""
    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    project.mkdir()
    named = _repo_with_lock(project, "named", "alpha")
    _repo_with_lock(project, "other", "beta")

    code, out = run(store, named / prov.LOCK_NAME, capsys, project_dir=project)
    assert "beta" not in out, out
    assert "locks were discovered" not in flat(out), out
    assert code == 0, out  # durable surface: the absent skill is a note


def test_discovery_finding_nothing_still_reports_an_absent_lock(tmp_path, capsys):
    """A machine with no lock is one this cannot verdict on, not a broken one.

    The whole fix is about not exiting 0 over a lock that resolved to nothing —
    which makes it tempting to call "no lock anywhere" a finding too. It is not:
    an absent lock is an absent EXPECTATION, and manufacturing a defect out of
    one would re-break the durable machines this is quiet on today.
    """
    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "empty"
    project.mkdir()
    (project / "not-a-repo").mkdir()

    code, out = run_autolock(store, project, capsys)
    assert code == 0, out
    assert "FINDINGS (0)" in flat(out), out
    assert f"LOCK {project / prov.LOCK_NAME}" in flat(out), out
    assert "absent — nothing can be called stale or missing" in flat(out), out


def test_discovery_looks_one_level_down_and_no_further(tmp_path, capsys):
    """A lock four levels down belongs to no session that was started here."""
    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    (project / "outer" / "inner").mkdir(parents=True)
    _repo_with_lock(project / "outer", "inner", "too-deep")

    assert prov.discover_locks(None, project) == [project / prov.LOCK_NAME]
    _, out = run_autolock(store, project, capsys)
    assert "too-deep" not in out, out


def test_one_unreadable_store_is_reported_once_not_once_per_lock(
        tmp_path, monkeypatch, capsys):
    """Store-wide facts are store-wide, however many locks judge that store.

    Raised inside the per-lock pass, one unreadable directory would be reported
    as N defects — the headline count inflating with the number of repos in the
    session rather than with what is wrong.
    """
    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    project.mkdir()
    _repo_with_lock(project, "alpha-repo", "alpha")
    _repo_with_lock(project, "beta-repo", "beta")

    real_iterdir = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda self: (
        (_ for _ in ()).throw(PermissionError("simulated"))
        if self == store else real_iterdir(self)))

    _, out = run_autolock(store, project, capsys)
    assert out.count("[store-unreadable]") == 1, out


# ---------------------------------------------------------------------------
# hook-not-wired — #84's signature
# ---------------------------------------------------------------------------

def test_a_hook_wired_only_in_a_child_of_the_project_dir_is_a_finding(
        tmp_path, capsys, ephemeral, monkeypatch):
    """#84 exactly: every file present and correct, and nothing ever runs it.

    Claude Code resolves hooks from the settings chain at `cwd` and at `$HOME`.
    `--add-dir` contributes skills, commands, agents and CLAUDE.md — never hooks
    and never settings. So a session opened on the PARENT of several repos
    consults none of their `.claude/settings.json` files, and every SessionStart
    hook they declare is inert, whatever its command string says.

    Invisible from inside any one repo, which is why it earns a finding rather
    than a comment: the lock is right, the hook script is right, the settings
    file is right, and no `skills:` verdict is ever printed to say otherwise —
    because the script that prints one never runs.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    project.mkdir()
    _repo_with_lock(project, "alpha-repo", "alpha", hook=True)
    _repo_with_lock(project, "beta-repo", "beta", hook=True)

    code, out = run_autolock(store, project, capsys)
    assert code == 1, out
    assert "[hook-not-wired]" in out, out
    assert "2 settings file(s) below this directory" in flat(out), out
    assert "never from an --add-dir grant" in flat(out), out
    assert str(project / "alpha-repo" / ".claude" / "settings.json") in out, out
    # A finding, not a note, and it must say what was lost.
    assert "no bundle is installed here at all" in flat(out), out
    assert out.index("FINDINGS") < out.index("[hook-not-wired]"), out


def test_the_same_wiring_is_only_a_note_on_a_durable_machine(tmp_path, capsys,
                                                             monkeypatch):
    """The hook makes ITSELF a no-op there, so nothing was lost by not firing.

    Caught by a negative control rather than by design: forcing the live repro
    to a durable surface still produced `[hook-not-wired]` as a finding, on a
    machine where the marketplace install is authoritative and a hook that never
    runs costs nothing. That is this change's own thesis inverted — a finding
    that is harmless on the ordinary case is one the reader learns to scroll
    past — so the same evidence takes the same surface gate as the promotion.

    Still reported, because it is the answer to "why was there no `skills:`
    verdict?", which is a real question on any surface.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    project.mkdir()
    _repo_with_lock(project, "alpha-repo", "alpha", hook=True)

    code, out = run_autolock(store, project, capsys)
    assert code == 0, out
    assert "FINDINGS (0)" in flat(out), out
    assert "[hook-not-wired]" in out, out
    assert "Not a defect on this surface" in flat(out), out
    assert out.index("NOTES") < out.index("[hook-not-wired]"), out


@pytest.mark.parametrize("where", ["project", "user"])
def test_a_hook_the_chain_actually_reads_is_not_a_finding(tmp_path, capsys,
                                                          ephemeral, monkeypatch,
                                                          where):
    """Wired at either level the chain consults, and the finding must not fire.

    Both halves matter. The project level is the single-repo session this must
    stay quiet on; the user level is the candidate fix for #84, and a doctor
    that kept reporting the defect after it was fixed would be the same false
    verdict pointing the other way.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    project.mkdir()
    _repo_with_lock(project, "alpha-repo", "alpha", hook=True)

    wired = _repo_with_lock(tmp_path / "staging", "wired", hook=True)
    settings = (wired / ".claude" / "settings.json").read_text(encoding="utf-8")
    if where == "project":
        (project / ".claude").mkdir()
        (project / ".claude" / "settings.json").write_text(settings, encoding="utf-8")
    else:
        (home / ".claude" / "settings.json").write_text(settings, encoding="utf-8")

    _, out = run_autolock(store, project, capsys)
    assert "[hook-not-wired]" not in out, out


def test_the_user_scope_settings_filename_is_surface_dependent(tmp_path, capsys,
                                                               ephemeral,
                                                               monkeypatch):
    """`cowork_settings.json` is the user-scope file on a Cowork surface.

    The binary selects it under `coworkPlugins` / `CLAUDE_CODE_USE_COWORK_PLUGINS`,
    so anything hardcoding `settings.json` reads a file that is not there and
    concludes "no user-scope hook" about a machine that has one — reporting
    #84's defect at a machine where it has already been fixed.

    The env arm is set here because the selection is what decides which name is
    read: this is a Cowork surface, and on a Cowork surface `cowork_settings.json`
    is the file. The companion test below is the same tree with the arm UNSET,
    where the answer must flip.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CLAUDE_CODE_USE_COWORK_PLUGINS", "1")

    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    project.mkdir()
    _repo_with_lock(project, "alpha-repo", "alpha", hook=True)

    wired = _repo_with_lock(tmp_path / "staging", "wired", hook=True)
    (home / ".claude" / "cowork_settings.json").write_text(
        (wired / ".claude" / "settings.json").read_text(encoding="utf-8"),
        encoding="utf-8")
    assert not (home / ".claude" / "settings.json").exists()

    _, out = run_autolock(store, project, capsys)
    assert "[hook-not-wired]" not in out, out


def test_the_user_scope_selects_one_file_and_does_not_merge_the_pair(
        tmp_path, capsys, ephemeral, monkeypatch):
    """A wired `cowork_settings.json` must NOT silence the finding off-Cowork.

    The user scope is a SELECTION, not a chain: the binary reads
    `cowork_settings.json` under `coworkPlugins` /
    `CLAUDE_CODE_USE_COWORK_PLUGINS` and `settings.json` otherwise, and never
    falls back from one to the other. Asking `any(...)` over the pair therefore
    answers a question nobody is in: on an ordinary machine it reports the user
    scope as wired because `cowork_settings.json` does, while the only file that
    machine opens — `settings.json`, present here and wiring nothing — does not.

    That direction is the dangerous one. The sibling defects in this area fire
    the finding at somebody who already fixed it, which is loud and self-
    correcting; this one SUPPRESSES the finding on a machine whose hook
    genuinely cannot fire, and a check that goes quiet is indistinguishable from
    a machine that is healthy.

    `settings.json` deliberately EXISTS and is empty of hooks. The bug needs a
    first file that is present and does not wire — a rule of "first that exists
    answers" and a rule of "either may answer" agree on every other tree.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("CLAUDE_CODE_USE_COWORK_PLUGINS", raising=False)

    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    project.mkdir()
    _repo_with_lock(project, "alpha-repo", "alpha", hook=True)

    wired = _repo_with_lock(tmp_path / "staging", "wired", hook=True)
    settings = (wired / ".claude" / "settings.json").read_text(encoding="utf-8")
    (home / ".claude" / "cowork_settings.json").write_text(settings,
                                                           encoding="utf-8")
    (home / ".claude" / "settings.json").write_text("{}", encoding="utf-8")

    code, out = run_autolock(store, project, capsys)
    assert "[hook-not-wired]" in out, out
    assert code == 1, out

    # NEGATIVE CONTROL, same bytes on disk, one environment variable apart: on a
    # Cowork surface `cowork_settings.json` IS the file the binary opens, so the
    # finding must go quiet again. Without this the assertion above is satisfied
    # by any change that simply stops reading `cowork_settings.json` at all —
    # which would re-break the Cowork machine this pair exists to keep working.
    monkeypatch.setenv("CLAUDE_CODE_USE_COWORK_PLUGINS", "1")
    _, out = run_autolock(store, project, capsys)
    assert "[hook-not-wired]" not in out, out


@pytest.mark.parametrize("value, name", [
    ("1", prov.USER_SETTINGS_COWORK),
    ("true", prov.USER_SETTINGS_COWORK),
    ("", prov.USER_SETTINGS_DEFAULT),
    ("   ", prov.USER_SETTINGS_DEFAULT),
], ids=["set", "set-word", "empty", "blank"])
def test_the_user_scope_name_is_chosen_by_the_cowork_arm(value, name):
    """One name out, chosen by the arm a process can read.

    A non-empty value is on. Unlike `read_surface`'s presence-not-value rule for
    `SKILLS_BOOTSTRAP_FORCE` — which was read off this repo's own hook source —
    there is no source here to copy, so this is a convention and is documented
    as one on `user_settings_name`.
    """
    assert prov.user_settings_name(
        {"CLAUDE_CODE_USE_COWORK_PLUGINS": value}) == name
    assert prov.user_settings_name({}) == prov.USER_SETTINGS_DEFAULT


def test_the_finding_names_the_links_it_could_not_read(tmp_path, capsys,
                                                       ephemeral, monkeypatch):
    """The blind spot is printed where the reader is, not only in SKILL.md.

    Three links of the resolution chain are not files a process can open: a
    managed/policy settings file, a `--settings` path on the command line, and
    the `coworkPlugins` config flag that picks the user-scope name. A finding
    that lists only the files it DID read reads as exhaustive, so a session
    wired through any of the three gets exit 1 and a verdict with no way to tell
    a real defect from this check's blind spot — the same "fires at the person
    who already fixed it" failure as the settings-chain narrowings, one link
    further out.

    Asserted on the rendered OUTPUT rather than on the docstring, because the
    docstring is exactly what claimed this while the finding did not carry it.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    project.mkdir()
    _repo_with_lock(project, "alpha-repo", "alpha", hook=True)

    _, out = run_autolock(store, project, capsys)
    assert "[hook-not-wired]" in out, out
    flattened = flat(out)
    assert "a managed/policy settings file" in flattened, out
    assert "a --settings path given on the command line" in flattened, out
    assert "`coworkPlugins` config flag" in flattened, out

    # The durable-surface NOTE carries the same sentence. It is the same blind
    # spot, and a reader who only ever sees the note would otherwise never be
    # told about it.
    monkeypatch.delenv("CLAUDE_CODE_REMOTE_SESSION_ID")
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT")
    code, out = run_autolock(store, project, capsys)
    assert code == 0, out
    assert "[hook-not-wired]" in out, out
    assert "a managed/policy settings file" in flat(out), out


def test_a_child_wiring_only_settings_local_json_is_still_counted(
        tmp_path, capsys, ephemeral, monkeypatch):
    """The child scan reads the same project chain a child would read as cwd.

    `settings.local.json` sits AHEAD of `settings.json` in that chain and is the
    gitignored file someone reaches for in a repo they would rather not commit
    to — so enumerating only `settings.json` below the project dir undercounts
    exactly the repos most likely to have been fixed by hand, and with one such
    repo alone it withholds the finding entirely.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    project.mkdir()
    repo = _repo_with_lock(project, "alpha-repo", "alpha", hook=True)
    settings = repo / ".claude" / "settings.json"
    local = repo / ".claude" / "settings.local.json"
    local.write_text(settings.read_text(encoding="utf-8"), encoding="utf-8")
    settings.unlink()

    code, out = run_autolock(store, project, capsys)
    assert code == 1, out
    assert "[hook-not-wired]" in out, out
    assert str(local) in out, out
    # One path per repo, not one per name: a child carrying BOTH files is one
    # child, and a count that double-reports it misdescribes the machine.
    settings.write_text(local.read_text(encoding="utf-8"), encoding="utf-8")
    _, out = run_autolock(store, project, capsys)
    assert "1 settings file(s) below this directory" in flat(out), out

    # NEGATIVE CONTROL: with neither name wiring, there is no child to report
    # and the finding must not fire at all.
    local.unlink()
    settings.write_text("{}", encoding="utf-8")
    code, out = run_autolock(store, project, capsys)
    assert "[hook-not-wired]" not in out, out
    assert code == 1, out  # still red: the locked skills are undelivered


def test_an_ephemeral_verdict_carried_by_force_alone_says_so(tmp_path, capsys,
                                                             monkeypatch):
    """Export the variable on a laptop and the doctor reads that laptop as cloud.

    Deliberate: `read_surface` copies the hook's third arm, and the hook installs
    whenever `SKILLS_BOOTSTRAP_FORCE` is set. The cost is that a durable machine
    can be talked into the ephemeral reading by hand, and then every locked
    skill the marketplace install owns is reported as an undelivered one.

    Narrowing the arm would disagree with the hook silently, which is the
    failure the surface gate exists to stop — so the verdict names the input it
    rests on instead, and the reader can check it in one command.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("CLAUDE_CODE_REMOTE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    monkeypatch.setenv("SKILLS_BOOTSTRAP_FORCE", "1")

    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    project.mkdir()
    _repo_with_lock(project, "alpha-repo", "alpha", hook=True)

    code, out = run_autolock(store, project, capsys)
    assert code == 1, out
    assert "SURFACE  ephemeral" in out, out
    assert "rests on SKILLS_BOOTSTRAP_FORCE ALONE" in flat(out), out
    assert "Unset it and re-run" in flat(out), out

    # NEGATIVE CONTROL: an ephemeral reading carried by a real session id must
    # NOT carry the caveat — it is not a hand-exported variable, and a caveat on
    # every cloud session is one the reader learns to skip.
    monkeypatch.setenv("CLAUDE_CODE_REMOTE_SESSION_ID", "cse_deadbeef")
    _, out = run_autolock(store, project, capsys)
    assert "SURFACE  ephemeral" in out, out
    assert "rests on SKILLS_BOOTSTRAP_FORCE ALONE" not in flat(out), out


def test_the_project_scope_reads_settings_local_json_too(tmp_path, capsys,
                                                         ephemeral, monkeypatch):
    """The gitignored machine-local file sits AHEAD of `settings.json` in the chain.

    ADR 0005 records the order as managed/policy -> `--settings` ->
    `<cwd>/.claude/settings.local.json` -> `<cwd>/.claude/settings.json` ->
    `$HOME/.claude/settings.json`. Reading only the second of those fired
    `hook-not-wired` at a project that wires the hook in the first — telling the
    one person who had already applied the fix that "nothing here or at the user
    scope does", and exiting 1 at them. Wrong exactly for the reader it exists
    to serve, and the same defect class as the `cowork_settings.json` name one
    scope up.

    `settings.local.json` is the LIKELIER of the two to carry this fix: it is
    the file you reach for in a repo you would rather not commit the change to.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    project.mkdir()
    _repo_with_lock(project, "alpha-repo", "alpha", hook=True)

    wired = _repo_with_lock(tmp_path / "staging", "wired", hook=True)
    settings = (wired / ".claude" / "settings.json").read_text(encoding="utf-8")
    (project / ".claude").mkdir()
    local = project / ".claude" / "settings.local.json"
    local.write_text(settings, encoding="utf-8")
    assert not (project / ".claude" / "settings.json").exists()

    _, out = run_autolock(store, project, capsys)
    assert "[hook-not-wired]" not in out, out

    # NEGATIVE CONTROL, in the same test and on the same bytes: remove the one
    # file and the finding must come back. Without it, "the finding stopped
    # firing" is indistinguishable from "the finding stopped working" — and a
    # gate that cannot fire is worth less than no gate, because it reads as one.
    local.unlink()
    _, out = run_autolock(store, project, capsys)
    assert "[hook-not-wired]" in out, out


def test_a_child_hook_with_no_lock_anywhere_is_not_a_finding(tmp_path, capsys,
                                                             ephemeral,
                                                             monkeypatch):
    """The finding is about delivery failing, and nothing was declared.

    A repo wiring a hook but shipping no lock has nothing to deliver, so saying
    its hook never fires would be true and useless — and it would fire on every
    multi-repo session on the fleet, most of which have no lock at all.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    repo = project / "hook-only"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.json").write_text(json.dumps({
        "hooks": {"SessionStart": [{"matcher": "startup", "hooks": [
            {"type": "command", "command": "true"}]}]},
    }), encoding="utf-8")

    code, out = run_autolock(store, project, capsys)
    assert "[hook-not-wired]" not in out, out
    assert code == 0, out


@pytest.mark.parametrize("settings, wired", [
    ({"hooks": {"SessionStart": [{"hooks": [{"command": "run.sh"}]}]}}, True),
    ({"hooks": {"SessionStart": []}}, False),
    ({"hooks": {"SessionStart": [{"hooks": []}]}}, False),
    ({"hooks": {"SessionStart": [{"hooks": [{"command": "   "}]}]}}, False),
    ({"hooks": {"SessionStart": [{"hooks": [{"type": "command"}]}]}}, False),
    ({"hooks": {"PreToolUse": [{"hooks": [{"command": "run.sh"}]}]}}, False),
    ({"hooks": {"SessionStart": "SessionStart"}}, False),
    ({"hooks": []}, False),
    ({"note": "SessionStart is wired in the other file"}, False),
    ([], False),
], ids=["wired", "no-matchers", "no-entries", "blank-command", "no-command",
        "other-event", "not-a-list", "hooks-not-an-object", "only-mentioned",
        "not-an-object"])
def test_hook_wiring_is_decided_by_a_parser_not_a_line_scan(tmp_path, settings,
                                                            wired):
    """The question is structural, so the answer comes from the JSON parser.

    `"SessionStart"` appears in a settings file that merely mentions it, that
    declares it with no runnable entry, or that wires some other event — and a
    grep cannot tell any of those from a hook that will actually fire. The
    `only-mentioned` case is the one a line scan gets exactly backwards.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    assert prov.wires_session_start(path) is wired


def test_unreadable_or_absent_settings_are_not_wired(tmp_path):
    """Neither is a hook, and neither may crash the doctor."""
    assert prov.wires_session_start(tmp_path / "nope.json") is False
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert prov.wires_session_start(broken) is False
    assert prov.wires_session_start(tmp_path) is False


def test_a_path_in_a_finding_survives_wrapping_intact(tmp_path):
    """The path is the actionable half; a chopped one cannot be copied or grepped.

    textwrap breaks any word wider than the column by default, and every path
    this reports is one word. Measured: a store under a long tmp prefix came out
    split across two lines mid-directory, which reads as a typo and defeats the
    one thing a reader does with a finding's path.
    """
    path = "/a-very/long/prefix/" + "x" * 90 + "/skills.lock"
    wrapped = prov._para(f"the lock at {path} could not be read.", "      ")
    assert any(path in line for line in wrapped), wrapped


def test_one_defect_several_locks_declare_is_reported_once(tmp_path, capsys,
                                                           ephemeral):
    """Per-lock attribution must not become per-lock repetition.

    The fleet's locks largely declare the same bundle, so in a multi-repo
    session one undelivered skill is raised once per lock that names it.
    Measured on the session that produced #85's repro: eleven locks turned
    twenty-four distinct defects into ninety-five findings. A count that grows
    with the number of repos open, rather than with anything wrong, is how a
    findings list stops being read — which is the same failure this whole
    change exists to fix, arrived at from the other side.
    """
    store = tmp_path / "skills"
    store.mkdir()
    project = tmp_path / "repos"
    project.mkdir()
    for name in ("one", "two", "three", "four"):
        _repo_with_lock(project, name, "shared")

    code, out = run_autolock(store, project, capsys)
    assert code == 1, out
    assert out.count("[not-in-the-store] shared") == 1, out
    assert "FINDINGS (1)" in flat(out), out
    # Folded, not dropped: the finding still names who declared it, and the
    # header stays one line however many locks that is.
    assert f"declared by {project / 'four' / prov.LOCK_NAME}" in flat(out) or \
        "and 1 more" in flat(out), out
    assert "and 1 more" in flat(out), out


def test_two_locks_disagreeing_about_one_skill_stay_two_findings(tmp_path,
                                                                 capsys):
    """Identity is (kind, subject, DETAIL) — a shared name is not a shared fact.

    One lock naming a directory and another not naming it are two different
    statements about it. Folding on (kind, subject) alone would silently drop
    whichever the reader needed.
    """
    a = prov.Finding("untracked", "alpha", "detail A", "one.lock")
    b = prov.Finding("untracked", "alpha", "detail B", "two.lock")
    same = prov.Finding("untracked", "alpha", "detail A", "three.lock")

    merged = prov.dedupe([a, b, same])
    assert len(merged) == 2, merged
    assert merged[0].lock == "one.lock, three.lock", merged[0]
    assert merged[1].lock == "two.lock", merged[1]


# ---------------------------------------------------------------------------
# a directory no name-keyed channel here produced (#123)
# ---------------------------------------------------------------------------

def seeded_skill(store: Path, directory: str, declared: str) -> Path:
    """A directory whose SKILL.md declares a name that is not its basename.

    The shape #123 reports: a lone SKILL.md, no payload directories, and a
    frontmatter `name:` belonging to something else. Built from bytes rather
    than copied from the real
    `~/.claude/skills`, which would make the suite report on whichever surface
    it happens to run on.
    """
    skill = store / directory
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {declared}\ndescription: seeded by the harness\n---\nbody\n",
        encoding="utf-8")
    return skill


def test_a_seeded_directory_is_a_note_and_does_not_hold_the_exit_code_at_one(
        tmp_path, capsys, ephemeral):
    """#123's whole point: exit 1 must not be the resting state of a healthy session.

    The reported session is reproduced exactly — one hook-installed locked
    skill, plus `session-start-hook/` which the harness placed and whose
    frontmatter says `startup-hook-skill`. Before this, that second directory
    was an `untracked` FINDING and the session exited 1 with nothing wrong with
    it, on a surface where that is the permanent resting state.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    write_record(store, "alpha")
    seeded_skill(store, "session-start-hook", "startup-hook-skill")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "FINDINGS (0)" in out, out
    assert "[untracked] session-start-hook" not in out, out
    assert "[foreign] session-start-hook" in out, out
    # The note has to SAY the thing, not merely exist: the measurement that
    # carried the reclassification is the declared name.
    assert "startup-hook-skill" in flat(out), out


def test_the_seeded_row_is_counted_so_the_headline_still_adds_up(
        tmp_path, capsys, ephemeral):
    """"2 on disk, 1 hook-installed, 0 unattributed" loses a directory silently.

    Same arithmetic defect `_tally` already exists to prevent one column over.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    write_record(store, "alpha")
    seeded_skill(store, "session-start-hook", "startup-hook-skill")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    _, out = run(store, lock, capsys)
    verdict = out.splitlines()[0]
    assert "2 on disk" in verdict, verdict
    assert "1 hook-installed" in verdict, verdict
    assert "0 unattributed" in verdict, verdict
    assert "1 foreign" in verdict, verdict
    assert "session-start-hook           foreign" in out, out


def test_a_name_disagreement_the_lock_declares_is_still_a_finding(
        tmp_path, capsys, ephemeral):
    """The lock naming it outranks the label, because the hook is about to act.

    A locked directory is replaced at the next session start whatever its
    frontmatter says, and that consequence is the user's to know about. If the
    recogniser could suppress this, one mis-typed `name:` would silence the
    warning that local work is about to be overwritten.
    """
    store = tmp_path / "skills"
    store.mkdir()
    seeded_skill(store, "alpha", "something-else")
    write_record(store)                       # a run that recorded no install
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[hand-placed-over-locked] alpha" in out, out
    assert "alpha                        unattributed" in out, out


def test_a_name_disagreement_with_no_readable_record_is_still_a_finding(
        tmp_path, capsys, ephemeral):
    """Reclassifying needs a record that is SILENT about the directory.

    Withholding `untracked` says the delivery channels here can be ruled out,
    and the only thing that supports that is a record which names installs and
    does not name this one. With no record there is no silence to read: the
    no-record `untracked` text says even "the hook installed it" cannot be ruled
    out, and a note over the top of it asserting the opposite is the report
    contradicting itself about one directory.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    seeded_skill(store, "helper", "some-other-name")
    # No record at all — the state `read_record` calls ABSENT.
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[untracked] helper" in out, out
    assert "[foreign] helper" not in out, out


def test_the_foreign_note_does_not_claim_no_bundle_ever_delivered_it(
        tmp_path, capsys, ephemeral):
    """The note may report what the record says, not what it cannot know.

    A record is not a history. A hook that fails to READ one rewrites it from
    scratch and forgets every install before that run — a cause the `untracked`
    finding's own list already names — so a present record's silence is evidence
    and not proof. A registry whose CI does not run the name-dir-mismatch check
    can also ship the frontmatter this recognises. Both leave "no bundle here
    delivered it" a claim the tool cannot support, in a sentence a reader has no
    way to check.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    write_record(store, "alpha")
    seeded_skill(store, "session-start-hook", "startup-hook-skill")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "[foreign] session-start-hook" in out, out
    assert "So no bundle here delivered it" not in flat(out), out
    assert "nothing this script can see delivered THIS directory" in flat(out), out
    # The two things the reclassification does NOT establish, named in the note
    # rather than left for the reader to notice.
    assert "forgets every install before that run" in flat(out), out
    assert "does not run the name-dir-mismatch check" in flat(out), out


def test_a_name_the_record_names_in_a_skipped_entry_is_not_foreign(
        tmp_path, capsys, ephemeral):
    """The record file is what a reader opens, not `Record.entries`.

    An entry the hook's shape check rejects is dropped from `entries` and stays
    in the file. Gating only on `entries` therefore printed "the install record
    does not name it" about a directory the record names in plain JSON — beside
    a `record-entries-skipped` FINDING in the same report, pointing at the very
    file that contradicts it.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    seeded_skill(store, "helper", "some-other-name")
    (store / prov.RECORD_NAME).write_text(json.dumps({"version": 1, "installed": [
        {"name": "alpha", "registry": REGISTRY_URL, "bundle": "adam",
         "digest": prov.digest_skill_dir(store / "alpha")},
        {"name": "helper", "registry": REGISTRY_URL, "bundle": "adam",
         "digest": "not-a-digest"},
    ]}, indent=2) + "\n", encoding="utf-8")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    record = prov.read_record(store / prov.RECORD_NAME)
    assert record.skipped == 1 and record.skipped_names == {"helper"}, record

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[record-entries-skipped]" in out, out
    assert "[foreign] helper" not in out, out
    assert "[untracked] helper" in out, out
    assert "helper                       unattributed" in out, out


def test_an_unnamed_skipped_entry_contributes_no_name(tmp_path):
    """The negative control: `skipped_names` is names, not a rejection count.

    An entry with no usable `name` is still skipped and still counted, and it
    must not put anything into the set — a set that filled up with placeholder
    entries would disqualify the foreign label for directories nothing names.
    """
    path = tmp_path / prov.RECORD_NAME
    path.write_text(json.dumps({"version": 1, "installed": [
        {"registry": REGISTRY_URL, "bundle": "adam", "digest": "f" * 64},
        {"name": 7, "registry": REGISTRY_URL, "bundle": "adam", "digest": "f" * 64},
        "not-a-dict",
    ]}) + "\n", encoding="utf-8")
    record = prov.read_record(path)
    assert record.skipped == 3, record
    assert record.skipped_names == set(), record


def test_a_foreign_directory_the_account_store_also_names_is_not_a_shadow(
        tmp_path, capsys, ephemeral):
    """A basename collision is not one skill arriving twice.

    The shadow comparison exists for a skill the registry installs and an
    earlier upload also delivers, and its whole remedy is "the account copy is
    behind, re-upload it". A directory whose frontmatter declares another name
    is not that skill's second copy: no upload could ever reconcile the pair, so
    the FINDING it produced could never go green — permanent red beside a note
    saying nothing here delivered the directory and there is nothing to fix.
    One report, two sentences, opposite advice.

    Reachable today rather than hypothetical: the hosted harness seeds
    `session-start-hook/` into every session here, and Anthropic example skills
    do reach the account store.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    write_record(store, "alpha")
    seeded_skill(store, "session-start-hook", "startup-hook-skill")
    account_copy(store, "session-start-hook")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "FINDINGS (0)" in out, out
    assert "shadow-copies-differ" not in out, out
    assert "[shadowed-by-the-account-store] session-start-hook" not in out, out
    assert "[foreign] session-start-hook" in out, out
    # Excluding it from the comparison is not licence to deny the collision:
    # the account manifest DOES name the basename, the report prints
    # the account store's own path in the same run, and a reader can check it
    # with one `ls`.
    assert f"The account store does hold a {prov.ACCOUNT_DIR}/session-start-hook/" \
        in flat(out), out
    assert "Nor does the account manifest" not in flat(out), out


def test_a_kind_no_observation_registers_cannot_reach_a_reader():
    """The table is enforced, not decorative.

    `OBSERVATION_ORIGINS` is the axis the matrix test enumerates. If a kind
    could be raised without appearing in it, the matrix would be complete over a
    table that no longer describes the code — which is the pairwise wiring this
    replaced, one level up.
    """
    with pytest.raises(KeyError):
        prov._observed("invented-kind", prov.HOOK, "alpha", "detail")
    with pytest.raises(ValueError):
        prov._observed("foreign", prov.HOOK, "alpha", "detail")
    with pytest.raises(ValueError):
        prov._observed("shadow-copies-differ", prov.FOREIGN, "alpha", "detail")
    # And the positive control, so the three above are not passing because
    # `_observed` refuses everything.
    assert prov._observed("foreign", prov.FOREIGN, "alpha", "d").kind == "foreign"


def test_a_recorded_name_disagreement_gets_no_foreign_note(tmp_path, capsys,
                                                           ephemeral):
    """A record ENTRY is the hook saying it installed exactly this directory.

    The same contradiction `reported_foreign`'s other gates exist for, in the
    state neither of them reaches: a skill the hook installed and the lock has
    since dropped, whose frontmatter disagrees with its basename — which a
    federated registry that does not run the name-dir-mismatch lint can ship.
    The row reads `hook`, with the registry and bundle that delivered it, and
    the note beside it said the install record does not name it.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    seeded_skill(store, "helper", "some-other-name")
    write_record(store, "alpha", "helper")    # the hook installed BOTH
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    _, out = run(store, lock, capsys)
    assert "helper                       hook" in out, out
    assert "[foreign] helper" not in out, out


def test_a_locked_name_disagreement_gets_no_foreign_note(tmp_path, capsys,
                                                         ephemeral):
    """One directory, two sentences that cannot both be true.

    `hand-placed-over-locked` says it will not survive the next session start;
    the `foreign` note says the hook will never remove it. The row already gave
    the lock priority, but the note was emitted store-wide with no lock gate, so
    a locked directory collected both.
    """
    store = tmp_path / "skills"
    store.mkdir()
    seeded_skill(store, "alpha", "something-else")
    write_record(store)                       # a run that recorded no install
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[hand-placed-over-locked] alpha" in out, out
    assert "[foreign] alpha" not in out, out
    assert "will not survive the next session start" in flat(out), out
    assert "the hook will not remove it" not in flat(out), out


def test_a_name_one_lock_declares_is_not_foreign_under_another(tmp_path, capsys):
    """The gate is store-wide because the claim is: "no bundle HERE delivered it".

    With the gate applied per lock, the lock that does not name the directory
    still reclassifies it, and the same run prints `hand-placed-over-locked`
    from the lock that does. A name any readable lock declares is one some
    bundle here delivers, so the premise fails under every lock at once.
    """
    store = tmp_path / "skills"
    store.mkdir()
    seeded_skill(store, "helper", "some-other-name")
    write_record(store)                       # present, and names no install
    project = tmp_path / "repos"
    _repo_with_lock(project, "one", "helper")
    _repo_with_lock(project, "two", "elsewhere")

    code, out = run_autolock(store, project, capsys)
    assert code == 1, out
    assert "[hand-placed-over-locked] helper" in out, out
    assert "[untracked] helper" in out, out
    assert "[foreign] helper" not in out, out


def test_an_ordinary_untracked_directory_is_still_a_finding(
        tmp_path, capsys, ephemeral):
    """The negative control: the recogniser must not blanket-mute `untracked`.

    A directory whose SKILL.md agrees with its own basename is exactly the case
    the finding was written for, and it has to survive a change whose whole
    purpose is to stop that finding firing on something else.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "mine")                 # name == basename
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[untracked] mine" in out, out
    assert "[foreign] mine" not in out, out


def test_the_untracked_finding_names_the_surface_as_a_fourth_cause(
        tmp_path, capsys, ephemeral):
    """#123 option 4, which the issue asks for regardless of the rest.

    "Three ways to land here" sent the reader hunting for a decision they never
    made, because none of the three was "something other than you put it there".
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "mine")
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    _, out = run(store, lock, capsys)
    flattened = flat(out)
    assert "Four ways to land here" in flattened, out
    assert "Three ways to land here" not in flattened, out
    assert "the SURFACE seeded it" in flattened, out


@pytest.mark.parametrize("frontmatter", [
    "",                                        # no frontmatter at all
    "---\ndescription: no name here\n---\n",   # frontmatter, no name
    "---\nname: alpha\n---\n",                 # name agrees with the basename
    "---\nname: |\n  alpha\n---\n",            # a block scalar it cannot read
    "---\nname: other # why\n---\n",           # a possible trailing comment
    "---\nname:\n---\n",                       # an empty value
    "---\nfields:\n  name: other\n---\n",      # nested, not the skill's own name
])
def test_every_shape_it_cannot_read_confidently_leaves_the_finding_alone(
        tmp_path, capsys, ephemeral, frontmatter):
    """None must mean "not measured", and not measured must change nothing.

    Downgrading a real finding to a note is the expensive direction: the reader
    never sees it again. So every ambiguous shape has to fall back to the
    behaviour this change did not touch.
    """
    store = tmp_path / "skills"
    store.mkdir()
    (store / "alpha").mkdir()
    (store / "alpha" / "SKILL.md").write_text(frontmatter + "body\n",
                                              encoding="utf-8")
    write_record(store)
    lock = write_lock(tmp_path / "skills.lock", store)

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[untracked] alpha" in out, out
    assert "[foreign] alpha" not in out, out


@pytest.mark.parametrize("line,expected", [
    ("name: startup-hook-skill", "startup-hook-skill"),
    ('name: "startup-hook-skill"', "startup-hook-skill"),
    ("name: 'startup-hook-skill'", "startup-hook-skill"),
    ("name:\tstartup-hook-skill", "startup-hook-skill"),
])
def test_the_plain_scalar_shapes_it_does_read(tmp_path, line, expected):
    """Quoting is a YAML detail, not a different name."""
    skill = tmp_path / "session-start-hook"
    skill.mkdir()
    (skill / "SKILL.md").write_text(f"---\n{line}\n---\nbody\n", encoding="utf-8")
    assert prov.declared_name(skill) == expected


def test_a_directory_with_no_skill_md_declares_nothing(tmp_path):
    """No SKILL.md is not a disagreement — it is the absence of a reading."""
    skill = tmp_path / "empty"
    skill.mkdir()
    assert prov.declared_name(skill) is None


def test_no_registry_skill_could_be_read_as_foreign():
    """The recogniser's premise, asserted against the registry rather than claimed.

    `foreign` means "no name-keyed channel here produced this", and that only
    holds while no skill this registry ships has a frontmatter `name:` differing
    from its directory basename. `scripts/check_skills.py` refuses one (kind
    `name-dir-mismatch`, unwaived, run on every CI push) — but that lint could be
    waived or dropped without anything here noticing, and the day it is, this
    tool starts labelling a real bundle skill as foreign and withholding a real
    finding about it. So the premise is measured here, on the checkout, and goes
    red at the moment it stops being true.
    """
    root = _walk_up("scripts/check_skills.py")
    skills = sorted((root / "plugins").glob("*/skills/*/SKILL.md"))
    assert skills, "no skills found — this test would pass by measuring nothing"
    mismatched = {
        path.parent.name: prov.declared_name(path.parent)
        for path in skills
        if prov.declared_name(path.parent) not in (None, path.parent.name)}
    assert not mismatched, (
        f"these registry skills would be reported as `foreign` by "
        f"check_provenance.py, which would withhold real findings about them: "
        f"{mismatched}")


# ---------------------------------------------------------------------------
# one bare name delivered by both channels (#122)
# ---------------------------------------------------------------------------

def account_copy(store: Path, name: str, body: str = "line one\nline two\n",
                 crlf: bool = True) -> Path:
    """A copy of `name` in the account store, CRLF by default as measured.

    Written from bytes under `tmp_path` rather than read from a real
    `~/.claude/skills/synced/` — which SKILL.md notes "cannot be seeded or
    simulated" on a machine, and which would make this suite report on whichever
    account happened to be logged in.
    """
    skill = store / prov.ACCOUNT_DIR / name
    skill.mkdir(parents=True, exist_ok=True)
    text = f"---\nname: {name}\n---\n{body}"
    (skill / "SKILL.md").write_bytes(
        text.encode("utf-8").replace(b"\n", b"\r\n") if crlf
        else text.encode("utf-8"))
    return skill


def shadowed_store(tmp_path, *, crlf: bool = True) -> Tuple[Path, Path]:
    """#122's session: three locked skills, all three shadowed by the account store."""
    store = tmp_path / "skills"
    store.mkdir()
    shared = ("adam-writing-style", "finding-unknowns", "writing-adrs")
    for name in shared:
        skill = store / name
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\n---\nline one\nline two\n",
            encoding="utf-8", newline="")
        account_copy(store, name, crlf=crlf)
    write_record(store, *shared)
    return store, write_lock(tmp_path / "skills.lock", store, *shared)


def test_the_shadowed_session_is_reported_without_being_called_broken(
        tmp_path, capsys, ephemeral):
    """#122's headline: the doctor called this session clean while three of its
    locked skills had a copy it never mentioned.

    Every one of the three has to be NAMED, and the exit code has to stay 0 —
    the collision is the correct and expected state of every cloud session this
    registry delivers into, so reddening it would train the reader to skip the
    findings section.
    """
    store, lock = shadowed_store(tmp_path)

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "FINDINGS (0)" in out, out
    for name in ("adam-writing-style", "finding-unknowns", "writing-adrs"):
        assert f"[shadowed-by-the-account-store] {name}" in out, out


def test_the_benign_note_says_it_is_benign_only_for_now(tmp_path, capsys,
                                                        ephemeral):
    """The argument is the point of the note, not the name list.

    A note that said only "two copies exist" would read as trivia. #122's case
    is that the copies agree by measurement rather than by design, and that the
    two arms update on different clocks — which is what makes the benign state
    temporary.
    """
    store, lock = shadowed_store(tmp_path)

    _, out = run(store, lock, capsys)
    flattened = flat(out)
    assert "byte-identical instructions once CRLF line endings are folded to LF" \
        in flattened, out
    assert "a property of this moment and not of the design" in flattened, out
    assert "update on different clocks" in flattened, out
    # It must not quietly settle the precedence question, which is #122 item 1.
    assert "naming a winner between the two channels is a policy question" \
        in flattened, out


def test_two_copies_that_really_differ_are_a_finding(tmp_path, capsys,
                                                     ephemeral):
    """The case worth alarm: one bare name, two different sets of instructions.

    This is what "edit a skill, regenerate the lock, forget to re-upload" leaves
    behind, and it is invisible to CI — the collision only exists on a surface
    CI never stands on.
    """
    store, lock = shadowed_store(tmp_path)
    account_copy(store, "writing-adrs", body="line one\nAN OLDER LINE\n")

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[shadow-copies-differ] writing-adrs" in out, out
    assert "do NOT carry the same instructions" in flat(out), out
    # The other two are untouched by one skill having drifted.
    assert "[shadowed-by-the-account-store] finding-unknowns" in out, out
    assert "[shadow-copies-differ] finding-unknowns" not in out, out


def test_a_line_ending_difference_alone_is_never_the_finding(tmp_path, capsys,
                                                             ephemeral):
    """The whole reason the comparison normalises.

    Account copies are CRLF and the registry is LF, so an exact-digest
    comparison marks every account copy as drifted — a signal that fires on all
    of them and therefore says nothing about any of them. `shadowed_store`
    already writes CRLF; this pins that the LF-vs-CRLF pair alone stays a note.
    """
    store, lock = shadowed_store(tmp_path)

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "shadow-copies-differ" not in out, out


def test_identical_bytes_are_described_as_identical_bytes(tmp_path, capsys,
                                                          ephemeral):
    """Two copies that need no normalising must not be described as if they did.

    "identical once line endings are folded" asserts a CRLF difference that is
    not there, and a reader who checks would find the sentence wrong.
    """
    store, lock = shadowed_store(tmp_path, crlf=False)

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "The two copies carry byte-identical instructions, so which one wins" \
        in flat(out), out
    assert "folded to LF" not in flat(out), out


def test_a_name_only_one_channel_delivers_is_not_a_shadow(tmp_path, capsys,
                                                          ephemeral):
    """The negative control: an account-only skill is delivery, not collision.

    It already has a note of its own (`delivered-by-the-account-store`), and
    reporting it as a shadow too would be one fact twice under two names.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    write_record(store, "alpha")
    account_copy(store, "beta")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "shadowed-by-the-account-store" not in out, out
    assert "shadow-copies-differ" not in out, out


def test_a_copy_that_cannot_be_read_is_not_reported_as_agreeing(
        tmp_path, capsys, ephemeral, monkeypatch):
    """None means "not measured", and the note has to say that rather than pick.

    Reporting an unmeasurable pair as identical would be a guess dressed as a
    measurement — `digest_skill_dir`'s own rule, which `digest_shared_payload`
    keeps. Reporting it as differing would
    invent a defect. The digest is stubbed rather than the directory made
    unreadable, because this suite runs as root in some environments and a
    chmod-based test would silently stop testing anything there.
    """
    store, lock = shadowed_store(tmp_path)
    real = prov.digest_shared_payload
    monkeypatch.setattr(
        prov, "digest_shared_payload",
        lambda path, fold=False: None if prov.ACCOUNT_DIR in Path(path).parts
        else real(path, fold))

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "could NOT be compared" in flat(out), out
    assert "unmeasured rather than confirmed" in flat(out), out
    assert "shadow-copies-differ" not in out, out


def test_an_unmeasurable_normalised_digest_does_not_become_a_finding(
        tmp_path, capsys, ephemeral, monkeypatch):
    """The second unmeasurable branch: exact digests differ, normalising fails.

    Falling through to the finding here would accuse the two copies of holding
    different instructions on the strength of a comparison that never ran.
    """
    store, lock = shadowed_store(tmp_path)
    real = prov.digest_shared_payload
    monkeypatch.setattr(prov, "digest_shared_payload",
                        lambda path, fold=False: None if fold else real(path))

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "could not be re-compared with line endings normalised" in flat(out), out
    assert "shadow-copies-differ" not in out, out


def test_a_shadow_is_attributed_to_no_lock_however_many_there_are(
        tmp_path, capsys, ephemeral):
    """Store-wide, for the reason `store_findings` is — asserted on attribution.

    The collision is a property of the two stores and of NO lock. The tempting
    assertion is that it appears once, but that one cannot fail: `dedupe` keys on
    (kind, subject, detail) and folds identical notes however many times they are
    raised, so a version of this reporting moved inside the per-lock loop would
    still print one line and the test would stay green over the regression.

    What per-lock reporting DOES change is the header, which `_whose` decorates
    with "declared by <lock>" as soon as a note carries one and there is more
    than one lock to choose between — claiming a lock declared something no lock
    has an opinion about. That is checkable, so it is what this asserts.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "shared")
    write_record(store, "shared")
    # `make_skill`'s body verbatim: the point here is the COUNT of one note, so
    # the pair has to land on the note branch rather than the finding branch.
    account_copy(store, "shared", body="body\n")
    project = tmp_path / "repos"
    for child in ("one", "two", "three"):
        _repo_with_lock(project, child, "shared")
    # One lock declares a skill nobody delivered, purely so this run contains a
    # finding that IS lock-attributed — see the control assertion below.
    _repo_with_lock(project, "four", "shared", "undelivered")

    _, out = run_autolock(store, project, capsys)
    # `_whose`'s decoration only, which is "— declared by <path>" on a finding's
    # own HEADER line. A bare "declared by" would also match the phrase "declared
    # by the lock and not in the personal store" inside `not-in-the-store`'s
    # wrapped prose, which would make the control below pass over a `_whose` that
    # had stopped working entirely.
    headers = [line for line in out.splitlines() if line.lstrip().startswith("[")]
    mine = [line for line in headers
            if "[shadowed-by-the-account-store] shared" in line]
    assert len(mine) == 1, out
    assert "— declared by" not in mine[0], mine[0]
    # The control: another finding in this same run IS lock-attributed, so the
    # assertion above means the shadow note was excluded from attribution rather
    # than attribution having quietly stopped happening at all.
    assert any("— declared by" in line for line in headers), out


def _artefacts(skill: Path) -> None:
    """Every shape the uploader drops, planted beside a skill's real files."""
    for relpath, data in (("__pycache__/helper.cpython-311.pyc", b"\x00compiled"),
                          ("scripts/helper.pyo", b"\x00optimised"),
                          ("payload.b64", b"QUFB"),
                          (".pytest_cache/v/cache/lastfailed", b"{}"),
                          ("pytest-cache-files-xyz/tmp", b"scratch"),
                          (".venv/lib/thing", b"x"),
                          ("node_modules/dep/index.js", b"x")):
        target = skill / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def test_a_build_artefact_is_not_a_second_set_of_instructions(tmp_path, capsys,
                                                              ephemeral):
    """The healthy session this reporting exists to leave alone.

    The account copy is not a copy of the directory — it is the ZIP `zip_skill`
    uploaded, and `_include_in_zip` never put a `__pycache__` in it. Digesting
    the personal directory whole therefore reads an ordinary build artefact as a
    divergent set of instructions and exits 1 on a session where the SKILL.md
    bytes on both sides are identical. That is the exact failure this reporting
    forbids in its own rationale: reddening the ordinary case is how findings
    come to be skipped. The checkout this suite runs from already carries
    `__pycache__` directories under `plugins/`, and the installed copy at
    `~/.claude/skills/skills-doctor/scripts` collects its own.
    """
    store, lock = shadowed_store(tmp_path)
    _artefacts(store / "writing-adrs")
    write_record(store, "adam-writing-style", "finding-unknowns", "writing-adrs")

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "shadow-copies-differ" not in out, out
    assert "[shadowed-by-the-account-store] writing-adrs" in out, out


def test_the_shadow_note_claims_no_more_than_it_compared(tmp_path, capsys,
                                                        ephemeral):
    """The note prints two absolute paths; a reader can diff them.

    Narrowing the comparison to the uploader's filtered set left the sentence
    "The two copies are byte-identical" beside those paths, which is false the
    moment a `__pycache__` sits in one of them — the ordinary state of any skill
    whose scripts have been imported. Same defect class as the CRLF direction
    one sentence over: a reader who checks the bytes finds the claim wrong.
    """
    store, lock = shadowed_store(tmp_path, crlf=False)
    _artefacts(store / "writing-adrs")
    write_record(store, "adam-writing-style", "finding-unknowns", "writing-adrs")

    mine, theirs = store / "writing-adrs", store / prov.ACCOUNT_DIR / "writing-adrs"
    # The premise: the directories really do differ, and only the uploaded set
    # agrees. Without this the assertions below would pass on a pair that is
    # identical either way and prove nothing.
    assert prov.digest_skill_dir(mine) != prov.digest_skill_dir(theirs)
    assert prov.digest_shared_payload(mine) == prov.digest_shared_payload(theirs)

    code, out = run(store, lock, capsys)
    assert code == 0, out
    flattened = flat(out)
    assert "The two copies are byte-identical" not in flattened, out
    assert "carry byte-identical instructions" in flattened, out
    # And the report defines the word rather than leaving it to be guessed.
    assert "means the files an upload carries" in flattened, out
    assert "A diff -r of the two paths above can differ over those" in flattened, out


def test_a_file_the_uploader_would_have_carried_is_still_a_divergence(
        tmp_path, capsys, ephemeral):
    """The negative control on the filter: it must skip artefacts, not content.

    A filter written too wide — comparing only SKILL.md, skipping every dotted
    directory, or skipping everything below the top level — would turn
    `shadow-copies-differ` off altogether while every test above stayed green,
    because they all assert the quiet outcome.

    Asserted twice, at the top level and one directory down, because the two
    over-wide shapes fail differently: a comparison narrowed to SKILL.md misses
    both, and one that stops at the top level misses only the second. A single
    file at one depth leaves whichever half it does not stand on unguarded.
    """
    for depth, relpath in (("top level", "reference.md"),
                           ("nested", "reference/deeper/guidance.md")):
        here = tmp_path / depth.replace(" ", "-")
        here.mkdir()
        store, lock = shadowed_store(here)
        extra = store / "writing-adrs" / relpath
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text("guidance\n", encoding="utf-8")
        write_record(store, "adam-writing-style", "finding-unknowns",
                     "writing-adrs")

        code, out = run(store, lock, capsys)
        assert code == 1, (depth, out)
        assert "[shadow-copies-differ] writing-adrs" in out, (depth, out)


def test_a_directory_with_no_skill_md_collides_with_nothing(tmp_path, capsys,
                                                            ephemeral):
    """One channel delivering `beta` is one `beta`, whatever else is on disk.

    A leftover directory holding no SKILL.md is not a skill: the session's
    listing never sees it, so it shadows nothing. Reporting it as a two-channel
    collision also guarantees the divergence finding, since an empty directory
    is unavoidably not the account skill — the doctor inventing both the
    collision and the defect, at exit 1, over a directory that delivers nothing.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    write_record(store, "alpha")
    (store / "beta").mkdir()                  # a leftover, not a skill
    account_copy(store, "beta")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    _, out = run(store, lock, capsys)
    assert "shadow-copies-differ" not in out, out
    assert "shadowed-by-the-account-store" not in out, out


def test_the_same_directory_with_a_skill_md_is_a_shadow(tmp_path, capsys,
                                                        ephemeral):
    """The control for the test above: the SKILL.md is what makes it a skill.

    Without this, deleting the collision reporting entirely would pass there.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "beta", body="line one\nline two\n")
    write_record(store, "alpha", "beta")
    account_copy(store, "beta")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha", "beta")

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "[shadowed-by-the-account-store] beta" in out, out


def test_the_note_says_which_copy_carries_the_crlf(tmp_path, capsys, ephemeral):
    """Folding reconciled the pair says they disagree, not which way round.

    The sentence names a direction, so it has to be read off the bytes. Asserted
    in both directions, because a hard-coded string satisfies either one alone.
    """
    store, lock = shadowed_store(tmp_path)      # personal LF, account CRLF
    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "the account copy carries CRLF and this one does not" in flat(out), out

    other = tmp_path / "reversed"
    other.mkdir()
    store = other / "skills"
    store.mkdir()
    skill = store / "alpha"
    skill.mkdir()
    (skill / "SKILL.md").write_bytes(b"---\r\nname: alpha\r\n---\r\nbody\r\n")
    write_record(store, "alpha")
    account_copy(store, "alpha", body="body\n", crlf=False)
    lock = write_lock(other / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "this copy carries CRLF and the account copy does not" in flat(out), out


def test_crlf_on_both_sides_is_described_as_being_on_both_sides(tmp_path, capsys,
                                                                ephemeral):
    """The reading no fixed sentence can cover: neither copy is "the CRLF one".

    Each copy spells a different file with CRLF, so folding reconciles them
    while both sides carry some.
    """
    store = tmp_path / "skills"
    store.mkdir()
    skill = store / "alpha"
    skill.mkdir()
    (skill / "SKILL.md").write_bytes(b"---\r\nname: alpha\r\n---\r\nbody\r\n")
    (skill / "reference.md").write_bytes(b"one\ntwo\n")
    write_record(store, "alpha")
    theirs = store / prov.ACCOUNT_DIR / "alpha"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_bytes(b"---\nname: alpha\n---\nbody\n")
    (theirs / "reference.md").write_bytes(b"one\r\ntwo\r\n")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "both carry CRLF, and they disagree about where" in flat(out), out


# ---------------------------------------------------------------------------
# a build artefact is not an edit (#123's class, one channel over)
# ---------------------------------------------------------------------------

def test_running_a_suite_inside_an_installed_skill_does_not_make_it_edited(
        tmp_path, capsys, ephemeral):
    """The documented workflow that turned the doctor permanently red.

    `_walk_up`'s docstring blesses running this suite from the installed copy at
    `~/.claude/skills/skills-doctor/scripts`. Doing so writes `__pycache__` and
    `.pytest_cache` beside the scripts, the whole-directory digest stops
    matching the record, and every later run reports `edited-and-locked` at exit
    1 with nothing wrong and nothing to restore. Reddening an ordinary session
    is the defect #123 exists to remove, whichever finding does it.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")
    _artefacts(store / "alpha")

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "FINDINGS (0)" in out, out
    assert "[edited-and-locked] alpha" not in out, out
    assert "[artefacts-and-locked] alpha" in out, out
    # The consequence is still real and still stated: the hook digests the whole
    # directory, so it replaces this one anyway.
    assert "replaced at the next session start" in flat(out), out


def test_an_edit_beside_a_build_artefact_is_still_edited(tmp_path, capsys,
                                                         ephemeral):
    """The negative control, and the one that matters most.

    A reading that answered "artefacts only" whenever any artefact was present
    would pass the test above and silently stop reporting real edits — the
    integrity column going quietly wrong is what the install record exists to
    prevent.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")
    _artefacts(store / "alpha")
    (store / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\n---\nedited body\n", encoding="utf-8")

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[edited-and-locked] alpha" in out, out
    assert "[artefacts-and-locked] alpha" not in out, out


def test_a_filtered_file_present_at_install_is_not_read_as_an_artefact(
        tmp_path, capsys, ephemeral):
    """The second negative control: equality is the claim, not the filter.

    `digest_shared_payload` drops `.b64` files, so a skill that SHIPPED one and
    has had it rewritten is a directory whose uploaded set is unchanged. It is
    still an edit — and the equality catches it, because the recorded
    whole-directory digest included that file and the payload digest excludes
    it, so the two cannot compare equal. Asserting it here is what stops the
    argument in `ARTEFACTS_ONLY`'s comment from being prose nobody checked.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    (store / "alpha" / "payload.b64").write_bytes(b"QUFB")
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")
    before = prov.digest_shared_payload(store / "alpha")
    (store / "alpha" / "payload.b64").write_bytes(b"QkJC")
    # The premise: the filter really does drop it, so the uploaded set is
    # unchanged by an edit that the whole-directory digest does see.
    assert prov.digest_shared_payload(store / "alpha") == before
    assert prov.digest_skill_dir(store / "alpha") != before

    code, out = run(store, lock, capsys)
    assert code == 1, out
    assert "[edited-and-locked] alpha" in out, out
    assert "artefacts-and-locked" not in out, out


def test_a_stale_skill_a_build_artefact_keeps_alive_says_so(tmp_path, capsys,
                                                            ephemeral):
    """Out of the lock and preserved by a cache directory — a note, and a why.

    The hook keeps what it cannot show it installed unchanged, so the artefact
    silently cancels the removal the plain `stale` note promises. Reporting it
    as plain `stale` would tell the reader the next bootstrap removes it, which
    is exactly what will not happen.
    """
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "beta")
    write_record(store, "alpha", "beta")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")
    _artefacts(store / "beta")

    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "[artefacts-and-stale] beta" in out, out
    assert "[stale] beta" not in out, out
    assert "the removal this would otherwise get does not happen" in flat(out), out


# ---------------------------------------------------------------------------
# the central property: no ordinary session may red
# ---------------------------------------------------------------------------

def _ordinary_nothing_declared(tmp_path):
    """A store with a skill in it and no lock: nothing to fall short of."""
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    return store, tmp_path / "skills.lock"


def _ordinary_hook_delivered_the_lock(tmp_path):
    """The healthy ephemeral session: the hook installed exactly what is locked."""
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "beta")
    write_record(store, "alpha", "beta")
    return store, write_lock(tmp_path / "skills.lock", store, "alpha", "beta")


def _ordinary_shadowed_by_the_account_store(tmp_path):
    """#122's session: every locked skill also arrives from the account store."""
    return shadowed_store(tmp_path)


def _ordinary_suite_run_from_the_installed_copy(tmp_path):
    """#123's class: a documented workflow left build artefacts behind."""
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    write_record(store, "alpha")
    lock = write_lock(tmp_path / "skills.lock", store, "alpha")
    _artefacts(store / "alpha")
    return store, lock


def _ordinary_harness_seeded_a_directory(tmp_path):
    """#123 itself: the hosted harness places a directory before the hook runs."""
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    write_record(store, "alpha")
    seeded_skill(store, "session-start-hook", "startup-hook-skill")
    return store, write_lock(tmp_path / "skills.lock", store, "alpha")


def _ordinary_seeded_name_the_account_store_also_holds(tmp_path):
    """The same, one upload later: the account store carries that basename too."""
    store, lock = _ordinary_harness_seeded_a_directory(tmp_path)
    account_copy(store, "session-start-hook")
    return store, lock


def _ordinary_skill_left_the_lock(tmp_path):
    """A skill dropped from the lock and untouched: the next bootstrap removes it."""
    store = tmp_path / "skills"
    store.mkdir()
    make_skill(store, "alpha")
    make_skill(store, "beta")
    write_record(store, "alpha", "beta")
    return store, write_lock(tmp_path / "skills.lock", store, "alpha")


ORDINARY_SESSIONS = (
    _ordinary_nothing_declared,
    _ordinary_hook_delivered_the_lock,
    _ordinary_shadowed_by_the_account_store,
    _ordinary_suite_run_from_the_installed_copy,
    _ordinary_harness_seeded_a_directory,
    _ordinary_seeded_name_the_account_store_also_holds,
    _ordinary_skill_left_the_lock,
)


@pytest.mark.parametrize("build", ORDINARY_SESSIONS,
                         ids=[build.__name__[len("_ordinary_"):]
                              for build in ORDINARY_SESSIONS])
def test_no_ordinary_session_reddens(tmp_path, capsys, ephemeral, build):
    """The property the module docstring names, on the harshest surface there is.

    Exit 1 means "a human has to decide about this". Every session below is the
    correct, expected resting state of a class of machines this registry
    delivers into — so each may print as many NOTES as it likes and must exit 0.
    An exit code that can never be green has stopped carrying information, and
    every round of review on this branch has found one more way to hold it at 1
    forever. Adding a shape here is cheaper than rediscovering that.

    Run on the EPHEMERAL surface deliberately: it is the one that promotes
    absences to findings, so a session that stays green here stays green
    anywhere.
    """
    store, lock = build(tmp_path)
    code, out = run(store, lock, capsys)
    assert code == 0, out
    assert "FINDINGS (0)" in out, out


def test_the_folded_digest_folds_crlf_and_the_unfolded_one_does_not(tmp_path):
    """The two digests must disagree on a CRLF pair, or normalising is a no-op.

    If these ever returned the same answer, every test above would still pass
    while the comparison had quietly stopped doing anything — the account copies
    would read as agreeing because nothing distinguished them, not because they
    match.
    """
    lf, crlf = tmp_path / "lf", tmp_path / "crlf"
    for path, data in ((lf, b"---\nname: x\n---\nbody\n"),
                       (crlf, b"---\r\nname: x\r\n---\r\nbody\r\n")):
        path.mkdir()
        (path / "SKILL.md").write_bytes(data)

    assert prov.digest_shared_payload(lf) != prov.digest_shared_payload(crlf)
    assert prov.digest_shared_payload(lf, fold=True) == \
        prov.digest_shared_payload(crlf, fold=True)


def test_the_folded_digest_still_separates_a_real_content_change(tmp_path):
    """Normalising must not be so eager that it hides an edit.

    A comparison that returned equal for everything would turn every divergent
    pair into a benign note — the exact failure this reporting exists to catch.
    """
    one, two = tmp_path / "one", tmp_path / "two"
    for path, data in ((one, b"---\r\nname: x\r\n---\r\nbody\r\n"),
                       (two, b"---\r\nname: x\r\n---\r\nDIFFERENT\r\n")):
        path.mkdir()
        (path / "SKILL.md").write_bytes(data)

    assert prov.digest_shared_payload(one, fold=True) is not None
    assert prov.digest_shared_payload(one, fold=True) != \
        prov.digest_shared_payload(two, fold=True)


def test_the_shared_payload_digest_is_none_for_a_path_that_is_not_a_directory(
        tmp_path):
    """Same contract as `digest_skill_dir`: None is "not measured"."""
    plain = tmp_path / "file"
    plain.write_text("x", encoding="utf-8")
    assert prov.digest_shared_payload(plain) is None
    assert prov.digest_shared_payload(plain, fold=True) is None


# ---------------------------------------------------------------------------
# the matrix: origin x observation x record x lock
#
# Earlier rounds on this branch fixed one pair at a time, and each round's
# fixes opened defects of the same shape: a `foreign` directory fed to the
# shadow comparison, a `_tally` branch made unreachable by a gate added
# elsewhere, guards in `classify` that could no longer fire while their
# docstrings said they were live. Patching pairs does not converge on that,
# because what is missing is the list.
#
# So this is the list. `CELLS` is built from `prov.ORIGINS` and
# `prov.OBSERVATION_ORIGINS`, so a fifth origin or a fifth observation grows it
# by itself and `test_the_matrix_is_complete` fails until `MATRIX` accounts for
# the new cells. Each reachable cell asserts the origin assigned, the exact
# kinds reported about the directory, whether each is a FINDING or a NOTE, and
# the exit code. Each unreachable one asserts that the ladder cannot produce
# that origin from those inputs at all — which is how a dead branch shows up
# here as a fact rather than as a comment claiming it is live.
# ---------------------------------------------------------------------------

TARGET = "target"
BODY = "line one\nline two\n"
OBSERVATIONS = tuple(sorted(prov.OBSERVATION_ORIGINS))


def matrix_store(tmp_path, origin, observation, record_present, lock_declares):
    """The smallest store in which `target` has `origin` and `observation` fires.

    Never refuses a combination. An unreachable cell is one this builds
    faithfully and the ladder still declines to label the way the cell asked
    for — asserting that is the point, and a builder that raised instead would
    hide it behind the fixture.
    """
    store = tmp_path / "skills"
    store.mkdir()
    declared = "somewhere-else" if origin == prov.FOREIGN else TARGET
    skill = store / TARGET
    skill.mkdir()
    (skill / "SKILL.md").write_text(f"---\nname: {declared}\n---\n{BODY}",
                                    encoding="utf-8")
    if observation == "shadow":
        # Byte-for-byte what the personal copy holds, so the benign NOTE is what
        # a shadow produces here and a divergence never masquerades as one.
        account_copy(store, TARGET, body=BODY, crlf=False)
    if record_present:
        write_record(store, *( [TARGET] if origin == prov.HOOK else [] ))
    if observation == "integrity":
        # After the record, so the recorded digest is the pre-edit one.
        (skill / "SKILL.md").write_text(
            f"---\nname: {declared}\n---\n{BODY}edited\n", encoding="utf-8")
    lock = tmp_path / "skills.lock"
    write_lock(lock, store, *([TARGET] if lock_declares else []))
    return store, lock


def matrix_origin(out: str) -> str:
    """The origin column of `target`'s row, read out of the report."""
    for line in out.splitlines():
        match = re.match(rf"  {TARGET}\s+(\S+)\s", line)
        if match:
            return match.group(1)
    raise AssertionError(f"no row for {TARGET} in:\n{out}")


def matrix_kinds(out: str):
    """(finding kinds, note kinds) reported about `target`.

    Parsed from the rendered report rather than from the functions' returns, so
    the cell measures what a reader is shown — dedupe, per-lock attribution and
    the finding/note split included.
    """
    findings, notes, section = set(), set(), None
    for line in out.splitlines():
        if line.startswith("FINDINGS ("):
            section = findings
        elif line.startswith("NOTES ("):
            section = notes
        elif line.startswith("INFERENCE"):
            section = None
        elif section is not None:
            match = re.match(r"  \[([a-z-]+)\] (\S+)", line)
            if match and match.group(2) == TARGET:
                section.add(match.group(1))
    return findings, notes


_H, _U, _F, _K = prov.HOOK, prov.UNATTRIBUTED, prov.FOREIGN, prov.UNKNOWN

# (origin, record present, lock declares, observation) ->
#     (finding kinds, note kinds, exit code)
# Every triple absent from the keys is unreachable and asserted to be.
MATRIX = {
    (_H, True, True, "lock-expectation"): (set(), set(), 0),
    (_H, True, True, "integrity"): ({"edited-and-locked"}, set(), 1),
    (_H, True, True, "shadow"): (set(), {"shadowed-by-the-account-store"}, 0),
    (_H, True, True, "foreign"): (set(), set(), 0),

    (_H, True, False, "lock-expectation"): (set(), {"stale"}, 0),
    (_H, True, False, "integrity"): ({"edited-and-stale"}, set(), 1),
    (_H, True, False, "shadow"): (set(),
                                  {"stale", "shadowed-by-the-account-store"}, 0),
    (_H, True, False, "foreign"): (set(), {"stale"}, 0),

    (_U, True, True, "lock-expectation"): ({"hand-placed-over-locked"}, set(), 1),
    (_U, True, True, "integrity"): ({"hand-placed-over-locked"}, set(), 1),
    (_U, True, True, "shadow"): ({"hand-placed-over-locked"},
                                 {"shadowed-by-the-account-store"}, 1),
    (_U, True, True, "foreign"): ({"hand-placed-over-locked"}, set(), 1),

    (_U, True, False, "lock-expectation"): ({"untracked"}, set(), 1),
    (_U, True, False, "integrity"): ({"untracked"}, set(), 1),
    (_U, True, False, "shadow"): ({"untracked"},
                                  {"shadowed-by-the-account-store"}, 1),
    (_U, True, False, "foreign"): ({"untracked"}, set(), 1),

    # The pair that made this table. A FOREIGN directory whose basename the
    # account store also holds emitted `shadow-copies-differ` at exit 1 beside
    # its own note saying there was nothing to fix, and no upload could have
    # cleared it. Its shadow cell is now the same as its other three.
    (_F, True, False, "lock-expectation"): (set(), {"foreign"}, 0),
    (_F, True, False, "integrity"): (set(), {"foreign"}, 0),
    (_F, True, False, "shadow"): (set(), {"foreign"}, 0),
    (_F, True, False, "foreign"): (set(), {"foreign"}, 0),

    (_K, False, True, "lock-expectation"): (set(), set(), 0),
    (_K, False, True, "integrity"): (set(), set(), 0),
    (_K, False, True, "shadow"): (set(), {"shadowed-by-the-account-store"}, 0),
    (_K, False, True, "foreign"): (set(), set(), 0),

    (_K, False, False, "lock-expectation"): ({"untracked"}, set(), 1),
    (_K, False, False, "integrity"): ({"untracked"}, set(), 1),
    (_K, False, False, "shadow"): ({"untracked"},
                                   {"shadowed-by-the-account-store"}, 1),
    (_K, False, False, "foreign"): ({"untracked"}, set(), 1),
}

REACHABLE = {(origin, record, lock) for origin, record, lock, _ in MATRIX}
CELLS = [(origin, record, lock, observation)
         for origin in prov.ORIGINS
         for record in (True, False)
         for lock in (True, False)
         for observation in OBSERVATIONS]


def test_the_matrix_is_complete():
    """No cell may be left out of the table, and none may be invented.

    This is the assertion that makes the axes derived rather than decorative: a
    new origin in `prov.ORIGINS` or a new observation in
    `prov.OBSERVATION_ORIGINS` grows `CELLS` here and fails until `MATRIX`
    accounts for it — either as a reachable cell with an expected outcome, or by
    being absent and therefore claimed unreachable.
    """
    assert set(MATRIX) <= set(CELLS)
    assert set(MATRIX) == {(origin, record, lock, observation)
                           for origin, record, lock in REACHABLE
                           for observation in OBSERVATIONS}
    # And every origin the code names is REACHED by some cell. Without this the
    # table absorbs a new origin silently: nothing assigns it, so all sixteen of
    # its cells land in the unreachable branch and pass by saying nothing.
    assert {origin for origin, _, _ in REACHABLE} == set(prov.ORIGINS)


def test_every_origin_an_observation_declares_is_exercised():
    """`OBSERVATION_ORIGINS` may not promise an origin no cell reaches.

    An entry nothing exercises is the same defect as a guard nothing asserts —
    it reads as covered, and the next reader who checks whether it matters
    deletes it or, worse, trusts it.
    """
    for observation, origins in prov.OBSERVATION_ORIGINS.items():
        kinds = set(prov.OBSERVATION_KINDS[observation])
        for origin in origins:
            exercised = any(
                (findings | notes) & kinds
                for (cell_origin, _, _, cell_observation), (findings, notes, _)
                in MATRIX.items()
                if cell_origin == origin and cell_observation == observation)
            assert exercised, (observation, origin)


@pytest.mark.parametrize(
    "cell", CELLS,
    ids=[f"{origin}-{'record' if record else 'norecord'}"
         f"-{'locked' if lock else 'unlocked'}-{observation}"
         for origin, record, lock, observation in CELLS])
def test_the_origin_observation_matrix(tmp_path, capsys, ephemeral, cell):
    """One cell of the cross-product, on the surface that raises the most.

    Ephemeral deliberately: it is the surface that promotes absences to
    findings, so an exit code recorded here is the worst case for that cell.
    """
    origin, record_present, lock_declares, observation = cell
    store, lock = matrix_store(tmp_path, origin, observation,
                               record_present, lock_declares)
    code, out = run(store, lock, capsys)
    assigned = matrix_origin(out)
    assert assigned in prov.ORIGINS, out

    if (origin, record_present, lock_declares) not in REACHABLE:
        assert assigned != origin, (
            f"the ladder was expected to be unable to label {TARGET} "
            f"{origin!r} with record_present={record_present} and "
            f"lock_declares={lock_declares}, and it did:\n{out}")
        return

    assert assigned == origin, out
    findings, notes = matrix_kinds(out)
    want_findings, want_notes, want_code = MATRIX[cell]
    assert findings == want_findings, out
    assert notes == want_notes, out
    assert code == want_code, out
    # Soundness, independent of the table above: nothing reported about this
    # directory may come from an observation that does not cover its origin.
    for kind in findings | notes:
        assert kind in prov.OBSERVATION_OF, (kind, out)
        assert assigned in prov.OBSERVATION_ORIGINS[prov.OBSERVATION_OF[kind]], (
            kind, assigned, out)
