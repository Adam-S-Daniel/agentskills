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
    `scripts/` are absent — there, skipping is right. Inside a registry checkout
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
    rows, findings, notes = prov.classify(
        skills, names, record, prov.read_lock(lock))
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
    #84's defect at a machine where it has already been fixed. Both names are
    checked, and this is the half that a `settings.json`-only reader fails.
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
    (home / ".claude" / "cowork_settings.json").write_text(
        (wired / ".claude" / "settings.json").read_text(encoding="utf-8"),
        encoding="utf-8")
    assert not (home / ".claude" / "settings.json").exists()

    _, out = run_autolock(store, project, capsys)
    assert "[hook-not-wired]" not in out, out


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
