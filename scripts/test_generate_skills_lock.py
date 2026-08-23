#!/usr/bin/env python3
"""Tests for generate_skills_lock.py and .claude/hooks/skills-bootstrap.sh.

Hermetic and deterministic: no network (the hook is driven against a `file://`
git repo built in tmp_path), no sleeps, no wall-clock dependence.

Every invocation of the hook gets `HOME` pointed at a tmp directory. That is
not a nicety — the hook's whole job is to write into `$HOME/.claude/skills`,
and a test suite that pollutes the developer's real home directory is worse
than no test suite. `_run_hook` is the only way these tests launch it, and it
refuses to run without an explicit tmp home.
"""

import json
import os
import re
import select
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
GENERATOR = SCRIPTS_DIR / "generate_skills_lock.py"
HOOK = REPO_ROOT / ".claude" / "hooks" / "skills-bootstrap.sh"

sys.path.insert(0, str(SCRIPTS_DIR))
import generate_skills_lock as gsl  # noqa: E402


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------

def _write(path: Path, text: str) -> None:
    # newline="": python's text mode rewrites "\n" as os.linesep, so on Windows
    # every fixture was written with different bytes than on Linux — and
    # TRICKY_SKILL's deliberate "line one\r\nline two\r\n" landed on disk as
    # "line one\r\r\nline two\r\r\n", which is not the CRLF content the digest
    # tests exist to pin. A fixture whose bytes depend on the platform cannot
    # bind two hash implementations to the same answer.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


# Fixture repos must round-trip bytes verbatim. Without this they inherit the
# developer's own `core.autocrlf`, which on Windows rewrites line endings on
# the way into the blob and back out again — so what the hook fetches is not
# what the fixture wrote, and every digest assertion is measuring git's
# translation rather than the two implementations under test.
_GIT_VERBATIM = ("-c", "core.autocrlf=false", "-c", "core.eol=lf")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *_GIT_VERBATIM, "-C", str(repo), *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def make_registry(root: Path, skills: dict, layout: str = gsl.DEFAULT_LAYOUT) -> str:
    """Build a committed git repo laid out like a registry.

    `skills` maps '<bundle>/<name>' -> {relative path: contents}. Returns the
    commit SHA. `layout` is resolved by the generator's own `layout_dir`, not
    re-spelled here, so a fixture cannot disagree with the code under test
    about where a federated source keeps its skills.
    """
    root.mkdir(parents=True, exist_ok=True)
    for key, files in skills.items():
        bundle, name = key.split("/", 1)
        for relpath, contents in files.items():
            _write(root / gsl.layout_dir(layout, bundle) / name / relpath, contents)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def run_generator(*args: str, cwd: Path = None,
                  script: Path = GENERATOR) -> subprocess.CompletedProcess:
    """Run the generator.

    `script` defaults to this repo's copy. A test that reproduces the CI
    invocation has to run a COPY sitting in the checkout it is testing, because
    the generator derives its own `REPO_ROOT` (and therefore the default
    `--repo` and the default lock path) from `__file__` — pointing this repo's
    copy at another tree with `cwd` alone would silently read this repo instead.
    """
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )


def _registry_shipping_the_generator(root: Path, skills: dict,
                                     layout: str = gsl.DEFAULT_LAYOUT) -> str:
    """A fixture registry that also carries `scripts/generate_skills_lock.py`.

    This repo ships the generator, so a registry standing in for it must too —
    it is what a consumer with no `scripts/` of its own has historically been
    verified against. Returns the commit SHA.
    """
    make_registry(root, skills, layout=layout)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "generate_skills_lock.py").write_bytes(GENERATOR.read_bytes())
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "ship the generator")
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


SKILL_A = {"SKILL.md": "---\nname: alpha\n---\nalpha body\n", "notes.md": "a\n"}
SKILL_B = {"SKILL.md": "---\nname: beta\n---\nbeta body\n"}
SKILL_C = {"SKILL.md": "---\nname: gamma\n---\ngamma body\n"}
# A skill directory built to make two independently written digest
# implementations disagree if they differ at all: a nested directory, an EMPTY
# file, CRLF line endings, a non-ASCII filename, and a file with no trailing
# newline. Every one of those is somewhere a re-implementation quietly
# normalises, skips, or mis-encodes.
TRICKY_SKILL = {
    "SKILL.md": "---\nname: tricky\n---\ntricky body\n",
    "reference/nested/deep.md": "two directories down\n",
    "empty.txt": "",
    "crlf.md": "line one\r\nline two\r\n",
    "ünïcodé-名前.md": "a UTF-8 filename\n",
    "no-trailing-newline.txt": "no newline at end of file",
}


@pytest.fixture
def registry(tmp_path):
    """A two-skill fixture registry plus its SHA."""
    root = tmp_path / "registry"
    sha = make_registry(root, {"adam/alpha": SKILL_A, "adam/beta": SKILL_B})
    return root, sha


# --------------------------------------------------------------------------
# digest
# --------------------------------------------------------------------------

def test_digest_is_stable_across_runs(tmp_path):
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    _write(skill / "scripts" / "run.sh", "echo hi\n")
    assert gsl.digest_skill_dir(skill) == gsl.digest_skill_dir(skill)


def test_digest_changes_when_a_payload_file_changes(tmp_path):
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    before = gsl.digest_skill_dir(skill)
    _write(skill / "SKILL.md", "body changed\n")
    assert gsl.digest_skill_dir(skill) != before


def test_digest_changes_on_a_one_byte_edit_in_a_nested_file(tmp_path):
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    _write(skill / "reference" / "deep.md", "x\n")
    before = gsl.digest_skill_dir(skill)
    _write(skill / "reference" / "deep.md", "y\n")
    assert gsl.digest_skill_dir(skill) != before


def test_digest_changes_when_a_file_is_added(tmp_path):
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    before = gsl.digest_skill_dir(skill)
    _write(skill / "extra.md", "extra\n")
    assert gsl.digest_skill_dir(skill) != before


def test_digest_changes_when_a_file_is_removed(tmp_path):
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    _write(skill / "extra.md", "extra\n")
    before = gsl.digest_skill_dir(skill)
    (skill / "extra.md").unlink()
    assert gsl.digest_skill_dir(skill) != before


def test_digest_changes_when_a_file_is_renamed(tmp_path):
    """Paths are in the manifest, so a pure rename is not invisible."""
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    _write(skill / "one.md", "same bytes\n")
    before = gsl.digest_skill_dir(skill)
    (skill / "one.md").rename(skill / "two.md")
    assert gsl.digest_skill_dir(skill) != before


def test_digest_is_independent_of_filesystem_iteration_order(tmp_path):
    """Same contents, files created in opposite orders -> same digest."""
    names = ["SKILL.md", "a.md", "m.md", "z.md", "nested/deep.md"]
    contents = {name: f"contents of {name}\n" for name in names}

    forward = tmp_path / "forward"
    for name in names:
        _write(forward / name, contents[name])

    backward = tmp_path / "backward"
    for name in reversed(names):
        _write(backward / name, contents[name])

    assert gsl.digest_skill_dir(forward) == gsl.digest_skill_dir(backward)


def test_digest_cli_matches_the_library(tmp_path):
    """The hook calls --digest; it must agree with the lock generator."""
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    proc = run_generator("--digest", str(skill))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == gsl.digest_skill_dir(skill)


def test_the_digest_itself_still_counts_a_build_artefact(tmp_path):
    """A fix that taught the hasher to skip artefacts would desync the hook.

    `digest_skill_dir` with no `skip` must keep hashing everything under a
    directory, `__pycache__` included — the hook's inline `digest_dir` hashes
    `~/.claude/skills`, where nothing is gitignored, so any exclusion has to
    live on the caller's side (`--check-current`'s `skip=`), never inside the
    hash function the two copies share.
    """
    skill = tmp_path / "skill"
    _write(skill / "SKILL.md", "body\n")
    before = gsl.digest_skill_dir(skill)
    _write(skill / "__pycache__" / "x.pyc", "not really bytecode\n")
    assert gsl.digest_skill_dir(skill) != before


# --------------------------------------------------------------------------
# lock generation
# --------------------------------------------------------------------------

def test_generates_a_lock_for_every_skill_in_the_bundle(registry, tmp_path):
    root, sha = registry
    out = tmp_path / "skills.lock"
    proc = run_generator("--repo", str(root), "--registry", "owner/repo",
                         "--bundles", "adam", "-o", str(out))
    assert proc.returncode == 0, proc.stderr
    lock = json.loads(out.read_text(encoding="utf-8"))
    assert lock["registry"] == "owner/repo"
    assert lock["ref"] == sha
    assert lock["generated_from"] == sha
    assert lock["bundles"] == ["adam"]
    assert sorted(lock["skills"]) == ["adam/alpha", "adam/beta"]
    assert lock["skills"]["adam/alpha"] == gsl.LOCK_DIGEST_PREFIX + gsl.digest_skill_dir(
        root / "plugins" / "adam" / "skills" / "alpha"
    )


def test_every_emitted_digest_is_labelled_sha256(registry, tmp_path):
    """Every lock value is `sha256:<64 hex>` -- the shape gitleaks cannot fire on.

    RED before the generator labelled them: bare 64-hex values trip gitleaks'
    default `generic-api-key` rule whenever the skill basename contains one of
    its ten keyword substrings (`access api auth key credential creds passwd
    password secret token`), which is why every adopter's first lock commit used
    to go red and get a hand-written `.gitleaks.toml` after the fact.

    Asserted over EVERY value, not a sample: the rule fires per line, so one
    unlabelled digest is one red scan. And asserted as a shape rather than as
    "no leaks", because a bare lock can pass a real scan BY LUCK -- roughly
    0.18% of digests happen to contain a hex-spellable stopword like `dead` --
    so a green scanner is not evidence the next content change stays green.
    See agentskills#87.
    """
    root, sha = registry
    out = tmp_path / "skills.lock"
    proc = run_generator("--repo", str(root), "--registry", "owner/repo",
                         "--bundles", "adam", "-o", str(out))
    assert proc.returncode == 0, proc.stderr
    skills = json.loads(out.read_text(encoding="utf-8"))["skills"]
    assert skills, "a lock with no skills would pass this vacuously"
    for key, digest in skills.items():
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest), (key, digest)

    # The label is a serialisation detail and must not have disturbed the pin
    # itself: strip it and the value is still exactly what digest_skill_dir
    # produces. This is what makes the change safe to apply to committed locks.
    for key, digest in skills.items():
        bundle, name = key.split("/", 1)
        assert digest == gsl.LOCK_DIGEST_PREFIX + gsl.digest_skill_dir(
            root / "plugins" / bundle / "skills" / name)


def test_an_empty_bundle_yields_an_empty_skills_map(tmp_path):
    root = tmp_path / "registry"
    make_registry(root, {"adam/alpha": SKILL_A})
    out = tmp_path / "skills.lock"
    proc = run_generator("--repo", str(root), "--bundles", "fastmail", "-o", str(out))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["skills"] == {}


def test_content_comes_from_the_pinned_ref_not_the_working_tree(registry, tmp_path):
    """The lock describes the commit it pins; an uncommitted edit must not leak in.

    This is the property that lets the hook's integrity check mean something: a
    digest built from a mutable working tree would describe bytes that were
    never published at `ref`, and every hook run would report a mismatch that
    is really just generator staleness.
    """
    root, sha = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    pinned = json.loads(out.read_text(encoding="utf-8"))["skills"]["adam/alpha"]

    _write(root / "plugins" / "adam" / "skills" / "alpha" / "SKILL.md", "TAMPERED\n")
    assert run_generator("--repo", str(root), "--ref", sha, "-o", str(out)).returncode == 0
    assert json.loads(out.read_text(encoding="utf-8"))["skills"]["adam/alpha"] == pinned


def test_the_lock_is_not_poisoned_by_build_artefacts_in_the_working_tree(registry, tmp_path):
    """The correction of record for issue #81, as a test rather than a claim.

    Commit `0684a6e` and PR #79's body both said regenerating with build
    artefacts present would pin their digests, "after which no clean checkout
    can satisfy it". It cannot: the generator reads content out of
    `git archive <ref>`, which has never heard of an untracked file. The lock
    a poisoned-looking tree writes is the lock a pristine one writes.
    """
    root, sha = registry
    clean = tmp_path / "clean.lock"
    assert run_generator("--repo", str(root), "--ref", sha, "-o", str(clean)).returncode == 0

    _write(root / "plugins" / "adam" / "skills" / "alpha" / "__pycache__" / "x.pyc", "junk\n")
    dirty = tmp_path / "dirty.lock"
    assert run_generator("--repo", str(root), "--ref", sha, "-o", str(dirty)).returncode == 0

    assert dirty.read_bytes() == clean.read_bytes()
    assert run_generator("--repo", str(root), "--check", "-o", str(dirty)).returncode == 0


def test_unresolvable_ref_reports_an_error_rather_than_a_traceback(registry, tmp_path):
    root, _ = registry
    proc = run_generator("--repo", str(root), "--ref", "no-such-ref",
                         "-o", str(tmp_path / "skills.lock"))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "no-such-ref" in proc.stderr


# --------------------------------------------------------------------------
# federated sources
#
# `registry`/`ref`/`bundles` stay the PRIMARY source; a `sources` array adds
# bundles that live in another repo (cms-platform's, kept at `skills/` rather
# than this repo's `plugins/<bundle>/skills`). The first two tests here are the
# back-compat wall: a lock with no extra sources must serialize exactly as it
# did before any of this existed, because every consumer has one committed.
# --------------------------------------------------------------------------

def test_a_single_source_lock_omits_the_sources_key(registry, tmp_path):
    """Not `"sources": []` — an empty array would churn every existing lock."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    assert '"sources"' not in out.read_text(encoding="utf-8")


def test_this_repos_committed_lock_regenerates_byte_identically(tmp_path):
    """The back-compat proof, on the real artifact rather than a fixture.

    Byte-for-byte, not key-by-key: a re-serialization that merely round-trips
    the same VALUES could still reorder or reformat, and every consumer's
    committed lock would show a diff for a change that added nothing to it.
    """
    lock_text = (REPO_ROOT / "skills.lock").read_text(encoding="utf-8")
    lock = json.loads(lock_text)
    have = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "-e", lock["ref"] + "^{commit}"],
        capture_output=True,
    )
    if have.returncode != 0:
        pytest.skip(f"pinned ref {lock['ref']} is not present in this checkout")

    out = tmp_path / "skills.lock"
    proc = run_generator("--repo", str(REPO_ROOT), "--registry", lock["registry"],
                         "--ref", lock["ref"], "--bundles", ",".join(lock["bundles"]),
                         "-o", str(out))
    assert proc.returncode == 0, proc.stderr
    assert out.read_text(encoding="utf-8") == lock_text


@pytest.fixture
def federated(tmp_path):
    """A primary registry plus a sibling one laid out like cms-platform.

    The sibling is deliberately named `cms-platform` and left beside the
    primary, so the default `../<repo-name>` checkout lookup is what finds it.
    """
    primary = tmp_path / "registry"
    primary_sha = make_registry(primary, {"adam/alpha": SKILL_A})
    extra = tmp_path / "cms-platform"
    extra_sha = make_registry(extra, {"cms-platform/deploy": SKILL_B}, layout="skills")
    return primary, primary_sha, extra, extra_sha


@pytest.fixture
def federated_two(tmp_path):
    """A primary registry plus TWO sibling registries.

    One source cannot tell "advanced the source I named" from "advanced every
    source", and it cannot tell "scoped the question" from "asked it and got
    lucky". Both properties need a source that is deliberately left alone.

    The three BUNDLE names and the three skill BASENAMES are all distinct, and
    that is load-bearing rather than tidy: a shared bundle is a hard error in
    `plan_sources` and a shared basename is a hard error in
    `_reject_basename_collisions`, so either collision would fail the run
    before it could demonstrate anything about scoping or about merging.

    Both siblings are named so the default `../<repo-name>` checkout lookup
    finds them, the same way the single-source `federated` fixture is.
    """
    primary = tmp_path / "registry"
    primary_sha = make_registry(primary, {"adam/alpha": SKILL_A})
    extra = tmp_path / "cms-platform"
    extra_sha = make_registry(extra, {"cms-platform/deploy": SKILL_B}, layout="skills")
    other = tmp_path / "other-platform"
    other_sha = make_registry(other, {"other/publish": SKILL_C}, layout="skills")
    return primary, primary_sha, extra, extra_sha, other, other_sha


def _federated_two_lock(out: Path, federated_two,
                        *extra_args: str) -> subprocess.CompletedProcess:
    primary, primary_sha, extra, extra_sha, other, other_sha = federated_two
    return run_generator(
        "--repo", str(primary), "--registry", primary.resolve().as_uri(),
        "--ref", primary_sha, "--bundles", "adam",
        "--source", f"cms-platform={extra.resolve().as_uri()}@{extra_sha}:skills",
        "--source", f"other={other.resolve().as_uri()}@{other_sha}:skills",
        "-o", str(out), *extra_args,
    )


def _federated_lock(out: Path, federated, *extra_args: str) -> subprocess.CompletedProcess:
    primary, primary_sha, extra, extra_sha = federated
    return run_generator(
        "--repo", str(primary), "--registry", primary.resolve().as_uri(),
        "--ref", primary_sha, "--bundles", "adam",
        "--source", f"cms-platform={extra.resolve().as_uri()}@{extra_sha}:skills",
        "-o", str(out), *extra_args,
    )


def test_a_second_source_is_recorded_and_digested_from_its_own_layout(federated, tmp_path):
    primary, primary_sha, extra, extra_sha = federated
    out = tmp_path / "skills.lock"
    proc = _federated_lock(out, federated)
    assert proc.returncode == 0, proc.stderr

    lock = json.loads(out.read_text(encoding="utf-8"))
    assert lock["registry"] == primary.resolve().as_uri()
    assert lock["ref"] == primary_sha
    assert lock["sources"] == [{
        "registry": extra.resolve().as_uri(),
        "ref": extra_sha,
        "bundles": ["cms-platform"],
        "layout": "skills",
    }]
    assert sorted(lock["skills"]) == ["adam/alpha", "cms-platform/deploy"]
    assert lock["skills"]["cms-platform/deploy"] == gsl.LOCK_DIGEST_PREFIX + gsl.digest_skill_dir(
        extra / "skills" / "deploy"
    )


def test_source_repo_overrides_the_sibling_checkout_lookup(tmp_path):
    """A registry name that is not the directory name — so only the flag can find it."""
    primary = tmp_path / "registry"
    primary_sha = make_registry(primary, {"adam/alpha": SKILL_A})
    extra = tmp_path / "checked-out-somewhere-else"
    extra_sha = make_registry(extra, {"cms-platform/deploy": SKILL_B}, layout="skills")
    out = tmp_path / "skills.lock"

    args = ["--repo", str(primary), "--registry", "owner/primary",
            "--ref", primary_sha, "--bundles", "adam",
            "--source", f"cms-platform=Adam-S-Daniel/cms-platform@{extra_sha}:skills",
            "-o", str(out)]
    missing = run_generator(*args)
    assert missing.returncode != 0
    assert "--source-repo" in missing.stderr

    found = run_generator(*args, "--source-repo", f"cms-platform={extra}")
    assert found.returncode == 0, found.stderr
    lock = json.loads(out.read_text(encoding="utf-8"))
    assert lock["skills"]["cms-platform/deploy"] == gsl.LOCK_DIGEST_PREFIX + gsl.digest_skill_dir(
        extra / "skills" / "deploy"
    )


def test_a_source_ref_is_recorded_resolved_to_a_commit_sha(tmp_path):
    """A branch name in a source would be the one unpinned half of the lock."""
    primary = tmp_path / "registry"
    primary_sha = make_registry(primary, {"adam/alpha": SKILL_A})
    extra = tmp_path / "cms-platform"
    extra_sha = make_registry(extra, {"cms-platform/deploy": SKILL_B}, layout="skills")
    out = tmp_path / "skills.lock"

    proc = run_generator(
        "--repo", str(primary), "--registry", "owner/primary", "--ref", primary_sha,
        "--bundles", "adam",
        "--source", f"cms-platform={extra.resolve().as_uri()}@main:skills",
        "-o", str(out))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["sources"][0]["ref"] == extra_sha


def test_a_basename_shared_by_two_bundles_is_a_generator_error(tmp_path):
    """The flat install dir makes this an overwrite, so it must never be written."""
    primary = tmp_path / "registry"
    primary_sha = make_registry(primary, {"adam/deploy": SKILL_A})
    extra = tmp_path / "cms-platform"
    extra_sha = make_registry(extra, {"cms-platform/deploy": SKILL_B}, layout="skills")
    out = tmp_path / "skills.lock"

    proc = run_generator(
        "--repo", str(primary), "--registry", "owner/primary", "--ref", primary_sha,
        "--bundles", "adam",
        "--source", f"cms-platform={extra.resolve().as_uri()}@{extra_sha}:skills",
        "-o", str(out))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "adam/deploy" in proc.stderr
    assert "cms-platform/deploy" in proc.stderr
    assert not out.exists()


def test_the_same_bundle_claimed_by_two_sources_is_an_error(tmp_path):
    primary = tmp_path / "registry"
    primary_sha = make_registry(primary, {"adam/alpha": SKILL_A})
    extra = tmp_path / "cms-platform"
    extra_sha = make_registry(extra, {"adam/beta": SKILL_B}, layout="skills")

    proc = run_generator(
        "--repo", str(primary), "--registry", "owner/primary", "--ref", primary_sha,
        "--bundles", "adam",
        "--source", f"adam={extra.resolve().as_uri()}@{extra_sha}:skills",
        "-o", str(tmp_path / "skills.lock"))
    assert _rejected(proc)
    assert "claimed by both" in proc.stderr


def _rejected(proc: subprocess.CompletedProcess) -> bool:
    """True when the generator DELIBERATELY refused, not merely exited non-zero.

    A GeneratorError prints `ERROR: ...`; argparse prints a usage block. The
    distinction matters for every test of a `--source` value: a generator that
    did not know the flag at all would reject the whole invocation, and the
    test would pass while asserting nothing about the validation it names.
    """
    return proc.returncode != 0 and proc.stderr.startswith("ERROR:")


@pytest.mark.parametrize("layout", ["/etc", "../../etc", "..", "skills/../../etc", "sk{oops}"])
def test_the_generator_rejects_a_layout_that_escapes_the_fetched_tree(tmp_path, layout):
    primary = tmp_path / "registry"
    primary_sha = make_registry(primary, {"adam/alpha": SKILL_A})
    proc = run_generator(
        "--repo", str(primary), "--registry", "owner/primary", "--ref", primary_sha,
        "--bundles", "adam",
        "--source", f"cms-platform=owner/other@{primary_sha}:{layout}",
        "-o", str(tmp_path / "skills.lock"))
    assert _rejected(proc), proc.stderr
    assert "layout" in proc.stderr


@pytest.mark.parametrize("hostile", ["ext::sh -c whoami", "ext::whoami"])
def test_the_generator_rejects_a_source_registry_git_would_execute(tmp_path, hostile):
    """`ext::sh -c ...` is a remote HELPER: git runs it. Never write one.

    Two independent guards refuse it: the realistic payload carries whitespace,
    caught by the control-char guard; a whitespace-free helper shape
    (`ext::whoami`) is caught by the URL/OWNER-REPO shape check. Either way the
    lock is not written.
    """
    primary = tmp_path / "registry"
    primary_sha = make_registry(primary, {"adam/alpha": SKILL_A})
    out = tmp_path / "skills.lock"
    proc = run_generator(
        "--repo", str(primary), "--registry", "owner/primary", "--ref", primary_sha,
        "--bundles", "adam",
        "--source", f"cms-platform={hostile}@{primary_sha}:skills",
        "-o", str(out))
    assert _rejected(proc), proc.stderr
    assert ".registry" in proc.stderr
    assert not out.exists()


def test_a_source_key_nobody_reads_is_rejected_rather_than_ignored(federated, tmp_path):
    """A `commit:` added believing it pins would read as a pin and do nothing."""
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    lock = json.loads(out.read_text(encoding="utf-8"))
    lock["sources"][0]["commit"] = "0" * 40
    out.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    proc = run_generator("--repo", str(federated[0]), "--check", "-o", str(out))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "commit" in proc.stderr


def test_check_inherits_the_locks_sources(federated, tmp_path):
    """Inherited for the same reason `ref` is: verifying half a lock is not verifying."""
    primary, _, extra, _ = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    green = run_generator("--repo", str(primary), "--check", "-o", str(out))
    assert green.returncode == 0, green.stdout + green.stderr

    lock = json.loads(out.read_text(encoding="utf-8"))
    lock["skills"]["cms-platform/deploy"] = "0" * 64
    out.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    red = run_generator("--repo", str(primary), "--check", "-o", str(out))
    assert red.returncode == 1, red.stdout + red.stderr
    assert "cms-platform/deploy" in red.stdout
    # The re-pin hint has to carry the federated half too, or following it
    # silently drops the second source.
    assert "--source" in red.stdout
    assert extra.resolve().as_uri() in red.stdout


def test_check_current_reports_a_change_in_a_federated_source(federated, tmp_path):
    primary, primary_sha, extra, extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    _write(extra / "skills" / "deploy" / "SKILL.md", "---\nname: deploy\n---\nedited\n")

    proc = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "cms-platform/deploy" in proc.stdout
    assert "changed" in proc.stdout
    # Named with ITS ref, not the primary's, so the message says what to re-pin.
    assert extra_sha in proc.stdout
    # ...and the HEADLINE says so, not only a detail line under it. While the
    # headline named the primary's ref, a reader (and a fleet bumper) was told
    # to re-pin the half that had not moved.
    headline = next(line for line in proc.stdout.splitlines() if line.startswith("FAILED:"))
    assert extra.resolve().as_uri() in headline
    assert extra_sha in headline
    assert primary_sha not in headline
    assert "--repin-source" in proc.stdout


def test_check_current_ignores_a_build_artefact_in_a_federated_source(federated, tmp_path):
    """Each source has its own repo and its own ignore rules, so each is asked.

    An ignore set computed once — from the primary — would pass every
    single-source test and then red-fail the first consumer whose federated
    registry someone had run a test suite in.
    """
    primary, _, extra, _ = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    _write(extra / ".gitignore", "__pycache__/\n")
    _git(extra, "add", "-A")
    _git(extra, "commit", "-q", "-m", "ignore build artefacts")
    _write(extra / "skills" / "deploy" / "__pycache__" / "x.pyc", "junk\n")

    proc = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_check_current_failure_attributes_a_federated_drift_to_its_own_registry(
        federated, tmp_path):
    """A drift in another registry is reported as that registry's, entirely.

    The primary's sha is absent from everything that ATTRIBUTES the drift — the
    headline and the difference lines — because the primary did not move and
    nothing about it is the answer to why this run is red. It appears in the
    remediation alone, as `--ref`, where its job is the opposite one: holding
    the pin that did not move while the source's advances. See
    `test_the_federated_remediation_holds_the_primary_pin_when_only_a_source_drifted`.
    """
    primary, primary_sha, extra, extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    _write(extra / "skills" / "deploy" / "SKILL.md", "---\nname: deploy\n---\nedited\n")

    proc = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    headlines = [line for line in lines if line.startswith("FAILED:")]
    assert len(headlines) == 1, proc.stdout
    assert headlines[0].startswith(f"FAILED: {extra.resolve().as_uri()}'s bundles have moved")
    assert extra_sha in headlines[0]
    assert primary_sha not in headlines[0]
    remediation = lines[lines.index(headlines[0]) + 1]
    assert remediation.strip() == (
        "python3 scripts/generate_skills_lock.py --repin "
        f"--ref {primary_sha} --repin-source '{extra.resolve().as_uri()}@'")
    difference_lines = [line for line in lines if line.strip().startswith("- ")]
    assert difference_lines and all(primary_sha not in line for line in difference_lines)


def _source_drifted(extra: Path, message: str = "the source really moved") -> str:
    """Commit a real content change in a federated source. Returns its new sha."""
    _write(extra / "skills" / "deploy" / "SKILL.md", "---\nname: deploy\n---\nedited\n")
    _git(extra, "add", "-A")
    _git(extra, "commit", "-q", "-m", message)
    return _head(extra)


def test_the_federated_remediation_holds_the_primary_pin_when_only_a_source_drifted(
        federated, tmp_path):
    """The printed command, RUN VERBATIM, must do what the verdict said.

    agentskills #108 fixed this exact class for `--check-format`: `--repin`
    deliberately does not inherit `ref`, so a remediation printed without one
    falls through to `resolve_ref(repo, "HEAD")` and advances the pin the
    verdict just said had not moved. The fleet bumper quotes these lines into a
    PR body as the command that produced the diff beneath it, so a source-only
    repair that also bumps the primary is unreviewed content arriving under a
    verdict that never mentioned it.

    The primary's clone is deliberately AHEAD of the pin here, which is the
    ordinary state of a checkout, and the difference between a latent bug and a
    live one.
    """
    primary, primary_sha, extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    assert _move_head(primary) != primary_sha
    advanced = _source_drifted(extra)

    verdict = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert verdict.returncode == 1, verdict.stdout + verdict.stderr
    lines = verdict.stdout.splitlines()
    assert lines[0].startswith(f"FAILED: {extra.resolve().as_uri()}'s bundles"), verdict.stdout
    remediation = lines[1].strip()
    assert remediation == (
        "python3 scripts/generate_skills_lock.py --repin "
        f"--ref {primary_sha} --repin-source '{extra.resolve().as_uri()}@'")

    # Parsed with shlex rather than re-spelled, so what runs is the string a
    # reader would paste. --repo/-o only say where this fixture lives.
    applied = run_generator(*shlex.split(remediation)[2:],
                            "--repo", str(primary), "-o", str(out))
    assert applied.returncode == 0, applied.stdout + applied.stderr
    lock = json.loads(out.read_text(encoding="utf-8"))
    assert lock["ref"] == primary_sha, "the source-only repair advanced the primary pin"
    assert lock["sources"][0]["ref"] == advanced


def test_the_federated_remediation_drops_the_anchor_when_the_primary_drifted_too(
        federated, tmp_path):
    """Holding the pin is right for a source-only repair and wrong here.

    When the primary drifted as well its own block says to advance it, so an
    anchored federated line would tell the reader to hold the very pin the
    block above told them to move. One bare `--repin --repin-source` advances
    both, which is what both verdicts together are asking for.
    """
    primary, _primary_sha, extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    _write(primary / "plugins" / "adam" / "skills" / "alpha" / "SKILL.md",
           "---\nname: alpha\n---\nedited\n")
    _source_drifted(extra)

    proc = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    headlines = [index for index, line in enumerate(lines) if line.startswith("FAILED:")]
    assert len(headlines) == 2, proc.stdout
    assert lines[headlines[1] + 1].strip() == (
        "python3 scripts/generate_skills_lock.py --repin "
        f"--repin-source '{extra.resolve().as_uri()}@'")


def test_check_current_names_both_when_primary_and_source_both_drift(
        federated, tmp_path):
    """Two drifts are two verdicts, and the primary's comes first.

    Order is the cross-repo contract's second fact: the fleet bumper slices
    from the FIRST `^FAILED:` into a PR body, with a path substitution that
    names the primary lock alone.
    """
    primary, primary_sha, extra, extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    _write(primary / "plugins" / "adam" / "skills" / "alpha" / "SKILL.md",
           "---\nname: alpha\n---\nedited\n")
    _write(extra / "skills" / "deploy" / "SKILL.md", "---\nname: deploy\n---\nedited\n")

    proc = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    headlines = [line for line in proc.stdout.splitlines() if line.startswith("FAILED:")]
    assert len(headlines) == 2, proc.stdout
    assert headlines[0] == (
        f"FAILED: the bundle has moved on since {primary_sha}, which {out} still pins — "
        "nothing added or changed since then reaches an ephemeral surface. Re-pin it "
        "(after committing the content) with:")
    assert headlines[1].startswith(f"FAILED: {extra.resolve().as_uri()}'s bundles")
    assert "adam/alpha" in proc.stdout and "cms-platform/deploy" in proc.stdout


def test_every_failed_line_is_followed_by_its_own_remediation_command(
        federated_two, tmp_path):
    """Adjacency in the stream the loop prints, which is where it is true.

    A headline-then-note-then-command shape would break this the moment a
    second block existed, and a reader scanning for "what do I type" would find
    the wrong line under the wrong registry.

    This is deliberately NOT a claim about the fleet bumper's 20-line cap. The
    two are different measurements and the comment at the report loop used to
    conflate them; `test_the_bumper_cap_can_cut_a_later_headline_from_its_command`
    is the one that slices.
    """
    primary, _, extra, _extra_sha, other, _other_sha = federated_two
    out = tmp_path / "skills.lock"
    assert _federated_two_lock(out, federated_two).returncode == 0

    _write(primary / "plugins" / "adam" / "skills" / "alpha" / "SKILL.md",
           "---\nname: alpha\n---\nedited\n")
    _write(extra / "skills" / "deploy" / "SKILL.md", "---\nname: deploy\n---\nedited\n")
    _write(other / "skills" / "publish" / "SKILL.md", "---\nname: publish\n---\nedited\n")

    proc = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    headlines = [index for index, line in enumerate(lines) if line.startswith("FAILED:")]
    assert len(headlines) == 3, proc.stdout
    for index in headlines:
        assert lines[index + 1].strip().startswith(
            "python3 scripts/generate_skills_lock.py --repin"), lines[index:index + 2]


# The fleet bumper's PR-body slice, reproduced rather than described:
# `sed -n '/^FAILED:/,$p' | head -20` in bump-consumer-locks.sh. Asserting
# against the raw stdout measures something no reviewer reads.
_BUMPER_CAP = 20


def _bumper_slice(stdout: str, cap: int = _BUMPER_CAP) -> list:
    lines = stdout.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("FAILED:"))
    return lines[start:start + cap]


def _drift_the_primary_by(primary: Path, count: int) -> None:
    """`count` new skills in the working tree — one `added:` difference each."""
    for index in range(count):
        _write(primary / "plugins" / "adam" / "skills" / f"extra{index:02d}" / "SKILL.md",
               f"---\nname: extra{index:02d}\n---\nbody\n")


# 5 fixed lines in the primary's block (headline, remediation, three note
# lines) + this many differences puts the FIRST federated headline on the last
# line the cap keeps. Named rather than inlined because if the block's fixed
# size ever changes both tests below go red together, which is the signal that
# the arithmetic in the report loop's comment needs redoing.
_DIFFERENCES_THAT_FILL_THE_CAP = _BUMPER_CAP - 5 - 1


def test_the_bumper_cap_always_keeps_the_primary_headline_with_its_command(
        federated, tmp_path):
    """The one truncation property this report really does guarantee.

    Fact (2) — the primary's block comes first — is what gives it: its headline
    is line 1 of the slice and its remediation line 2, whatever else drifted.
    That block is the one the bumper's path substitution names, and it is the
    only pair any cap above two lines cannot split.
    """
    primary, _primary_sha, extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    _drift_the_primary_by(primary, _DIFFERENCES_THAT_FILL_THE_CAP)
    _write(extra / "skills" / "deploy" / "SKILL.md", "---\nname: deploy\n---\nedited\n")

    proc = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    sliced = _bumper_slice(proc.stdout)
    assert sliced[0].startswith("FAILED: the bundle has moved on since ")
    assert sliced[1].strip() == "python3 scripts/generate_skills_lock.py --repin"


def test_the_bumper_cap_can_cut_a_later_headline_from_its_command(
        federated, tmp_path):
    """The counterexample to the absolute the report loop used to assert.

    It claimed a truncation "can never separate a headline from the command
    that fixes it". With the primary's block filling the cap, the first
    federated headline is the LAST line kept and its remediation is the first
    line dropped — so the PR body a reviewer reads ends on a failure naming a
    registry, with no command under it.

    Pinned as a measurement, not as a wish: this test going red means the
    report's shape changed, and the comment that now states the cap is unsafe
    for a later block has to be re-derived rather than left standing.
    """
    primary, _primary_sha, extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    _drift_the_primary_by(primary, _DIFFERENCES_THAT_FILL_THE_CAP)
    _write(extra / "skills" / "deploy" / "SKILL.md", "---\nname: deploy\n---\nedited\n")

    proc = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    sliced = _bumper_slice(proc.stdout)

    assert len(sliced) == _BUMPER_CAP
    assert sliced[-1].startswith(f"FAILED: {extra.resolve().as_uri()}'s bundles have moved")
    # Its remediation exists — adjacency holds in the stream — and is exactly
    # the line the cap drops.
    first_cut = lines[lines.index(sliced[-1]) + 1]
    assert first_cut.strip().startswith("python3 scripts/generate_skills_lock.py --repin")


def test_check_current_verdict_still_starts_with_FAILED_at_column_zero(
        federated, tmp_path):
    """The contract's first fact, asserted for a federated block too.

    _agent-guidance's bump-consumer-locks.sh branches on `grep -q '^FAILED:'`
    and routes anything else to a path that reports without rewriting. A
    verdict indented by two spaces is not a softer message there — it is a
    consumer lock that silently stops being maintained.
    """
    primary, _primary_sha, extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    _write(extra / "skills" / "deploy" / "SKILL.md", "---\nname: deploy\n---\nedited\n")
    federated_drift = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert federated_drift.returncode == 1
    assert federated_drift.stdout.startswith("FAILED: ")

    _write(extra / "skills" / "deploy" / "SKILL.md", SKILL_B["SKILL.md"])
    _write(primary / "plugins" / "adam" / "skills" / "alpha" / "SKILL.md",
           "---\nname: alpha\n---\nedited\n")
    primary_drift = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert primary_drift.returncode == 1
    assert primary_drift.stdout.startswith("FAILED: ")


# --------------------------------------------------------------------------
# --check-current --only: drift attribution by QUESTION, not by answer
#
# A caller that wants to know whether the FEDERATED half of a lock has moved
# cannot read that off a full-lock verdict: one combined `FAILED:` is printed
# whether the primary drifted, a source drifted, or both. Measured before this
# flag existed, on a lock with two sources both sitting exactly at their pins
# and only the primary edited: exit 1, one `FAILED:` line, zero federated
# differences. A gate keyed on that verdict advances every federated pin on
# every ordinary night. `--only` makes the scope a property of the question.
# --------------------------------------------------------------------------

def test_check_current_only_scopes_the_question_to_one_federated_source(
        federated_two, tmp_path):
    """The regression this flag exists for: a PRIMARY-only drift is not federated drift.

    Unscoped this lock is red, and it is red for a reason that has nothing to
    do with either source. Asked about a source specifically, each one answers
    green, because each one really is sitting at its pin.
    """
    primary, _, _extra, _extra_sha, _other, _other_sha = federated_two
    out = tmp_path / "skills.lock"
    assert _federated_two_lock(out, federated_two).returncode == 0
    sources = json.loads(out.read_text(encoding="utf-8"))["sources"]

    _write(primary / "plugins" / "adam" / "skills" / "alpha" / "SKILL.md",
           "---\nname: alpha\n---\nalpha body, edited\n")

    combined = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert combined.returncode == 1, combined.stdout + combined.stderr

    for source in sources:
        scoped = run_generator("--repo", str(primary), "--check-current",
                               "--only", source["registry"], "-o", str(out))
        assert scoped.returncode == 0, (source["registry"], scoped.stdout, scoped.stderr)
        assert "FAILED:" not in scoped.stdout


def test_check_current_only_on_the_primary_ignores_a_drifted_source(
        federated_two, tmp_path):
    """The mirror. Scoped to the primary, another registry's drift is not this
    question's answer."""
    primary, _, extra, _extra_sha, _other, _other_sha = federated_two
    out = tmp_path / "skills.lock"
    assert _federated_two_lock(out, federated_two).returncode == 0

    _write(extra / "skills" / "deploy" / "SKILL.md", "---\nname: deploy\n---\nedited\n")

    combined = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert combined.returncode == 1, combined.stdout + combined.stderr

    scoped = run_generator("--repo", str(primary), "--check-current",
                           "--only", primary.resolve().as_uri(), "-o", str(out))
    assert scoped.returncode == 0, scoped.stdout + scoped.stderr


def test_check_current_only_refuses_a_registry_the_lock_does_not_plan(
        federated, tmp_path):
    """And names what it DOES plan: the lock's own strings are the key, so an
    OWNER/REPO lock does not match an https:// URL for the same repository, and
    a caller has to be able to see that from the one line it gets."""
    primary, _, extra, _ = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    proc = run_generator("--repo", str(primary), "--check-current",
                         "--only", "owner/nowhere", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAILED:" not in proc.stdout    # not drift — the question could not be asked
    assert "ERROR:" in proc.stderr
    assert primary.resolve().as_uri() in proc.stderr
    assert extra.resolve().as_uri() in proc.stderr


def test_check_current_only_refuses_a_registry_that_is_both_primary_and_source(
        tmp_path):
    """Nothing else refuses this shape: plan_sources rejects a BUNDLE claimed
    twice and says nothing about one registry standing as both halves.

    The source here omits `layout`, so it inherits DEFAULT_LAYOUT — the
    primary's own. The refusal used to tell the reader their two entries
    "carry different bundles and different layouts" and then say "Fix the
    lock", which is a message asserting something false about the lock in front
    of them. Only the bundles are forced apart, by plan_sources' uniqueness
    check; the layouts here are identical and so are the refs.
    """
    primary = tmp_path / "registry"
    primary_sha = make_registry(primary, {"adam/alpha": SKILL_A, "extras/beta": SKILL_B})
    out = tmp_path / "skills.lock"
    assert run_generator(
        "--repo", str(primary), "--registry", primary.resolve().as_uri(),
        "--ref", primary_sha, "--bundles", "adam",
        "--source", f"extras={primary.resolve().as_uri()}@{primary_sha}",
        "--source-repo", f"extras={primary}", "-o", str(out)).returncode == 0

    proc = run_generator("--repo", str(primary), "--check-current",
                         "--only", primary.resolve().as_uri(),
                         "--source-repo", f"extras={primary}", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAILED:" not in proc.stdout
    assert "BOTH" in proc.stderr
    lock = json.loads(out.read_text(encoding="utf-8"))
    assert lock["sources"][0]["layout"] == gsl.DEFAULT_LAYOUT
    assert "different layouts" not in proc.stderr, proc.stderr


def test_check_current_only_without_check_current_is_an_argparse_error(
        federated, tmp_path):
    primary, _, _extra, _ = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    before = out.read_text(encoding="utf-8")

    proc = run_generator("--repo", str(primary), "--only", primary.resolve().as_uri(),
                         "-o", str(out))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "usage:" in proc.stderr
    assert "--only" in proc.stderr
    assert out.read_text(encoding="utf-8") == before


# `--only "$REG"` with an unset REG is the ordinary route in a shell caller,
# which is what the fleet bumper is — so the empty string is not a hypothetical
# input, it is the failure mode of the intended one.
_EMPTY_ONLY_SHAPES = [
    pytest.param([], id="a plain generate"),
    pytest.param(["--check"], id="--check"),
    pytest.param(["--check-format"], id="--check-format"),
]
assert _EMPTY_ONLY_SHAPES, "an empty parametrize list SKIPS at exit 0"


@pytest.mark.parametrize("other_flags", _EMPTY_ONLY_SHAPES)
def test_only_with_an_empty_value_is_refused_rather_than_silently_ignored(
        federated, tmp_path, other_flags):
    """An unset shell variable must not degrade into a different command.

    Both guards tested `args.only` for TRUTH, and the empty string is falsy, so
    `--only ''` slipped past them and the run continued unscoped. On a plain
    generate that is a data-loss path, not a cosmetic one: the run writes a
    lock from the command line alone, which DE-FEDERATES the lock and replaces
    its registry with DEFAULT_REGISTRY, at exit 0, with `--check` green
    afterwards. On `--check` it answers the unscoped question while the caller
    believes it was scoped.
    """
    primary, _, _extra, _ = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    before = out.read_text(encoding="utf-8")

    proc = run_generator("--repo", str(primary), *other_flags, "--only", "",
                         "-o", str(out))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "--only" in proc.stderr
    assert out.read_text(encoding="utf-8") == before
    assert "sources" in json.loads(out.read_text(encoding="utf-8"))


def test_check_current_only_alongside_check_or_check_format_is_an_argparse_error(
        federated, tmp_path):
    """`status` is already the worst verdict across the verify flags, so a run
    with one of them scoped and the others not has an exit code that answers no
    question anybody asked."""
    primary, _, _extra, _ = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    for other_flag in ("--check", "--check-format"):
        proc = run_generator("--repo", str(primary), "--check-current", other_flag,
                             "--only", primary.resolve().as_uri(), "-o", str(out))
        assert proc.returncode == 2, (other_flag, proc.stdout, proc.stderr)
        assert "usage:" in proc.stderr
        assert "--only" in proc.stderr


def test_check_current_only_does_not_need_an_unrelated_sources_checkout(
        federated_two, tmp_path):
    """This is what pins filtering BEFORE plan_sources rather than after it.

    plan_sources raises on the first `no checkout at ...` it meets, so a filter
    applied to its output would let one absent sibling clone decide a question
    asked about a different registry entirely.
    """
    primary, _, extra, _extra_sha, other, _other_sha = federated_two
    out = tmp_path / "skills.lock"
    assert _federated_two_lock(out, federated_two).returncode == 0

    shutil.move(str(other), str(tmp_path / "moved-away"))

    combined = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert combined.returncode != 0
    assert "no checkout at" in combined.stderr

    scoped = run_generator("--repo", str(primary), "--check-current",
                           "--only", extra.resolve().as_uri(), "-o", str(out))
    assert scoped.returncode == 0, scoped.stdout + scoped.stderr


def test_check_current_only_ok_line_names_the_scoped_sources_ref(
        federated, tmp_path):
    """The OK line names the ref this run actually read, not the primary's."""
    primary, primary_sha, extra, extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    scoped = run_generator("--repo", str(primary), "--check-current",
                           "--only", extra.resolve().as_uri(), "-o", str(out))
    assert scoped.returncode == 0, scoped.stdout + scoped.stderr
    assert scoped.stdout == f"OK: the working tree still matches {extra_sha} (1 skills).\n"

    unscoped = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert unscoped.returncode == 0, unscoped.stdout + unscoped.stderr
    assert unscoped.stdout == f"OK: the working tree still matches {primary_sha} (2 skills).\n"


# --------------------------------------------------------------------------
# the two validators must accept the same set
#
# The generator (python) and the hook (a python heredoc inside bash) each
# validate registry / ref / layout. FIX 6's bug was that they disagreed: the
# generator wrote a lock — `--ref 'HEAD~1'` — the hook then rejected wholesale.
# These two tests keep the copies in lockstep: one is a cheap byte-level drift
# alarm on the shared patterns, the other feeds values through BOTH and asserts
# the accept/reject verdict matches.
# --------------------------------------------------------------------------

def _extract_hook_lock_reader() -> str:
    """The hook's embedded lock-reader python, lifted out to run standalone.

    It does no network — it only validates the lock and writes the framing
    files — so running it in isolation is exactly the hook's acceptance check.
    """
    text = HOOK.read_text(encoding="utf-8")
    start = text.index("import json, os, re, sys")
    end = text.index("\nPY\n", start)
    return text[start:end] + "\n"


def _run_hook_reader(lock: dict, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run the hook's lock reader standalone against `lock`.

    Returned whole rather than reduced to a bool, because for this reader the
    exit CODE is not the whole verdict: the hook renders one fixed sentence for
    every non-zero exit, so a test that wants to know WHY the lock was refused
    has to read stderr.
    """
    lock_path = tmp_path / "probe.lock"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    out = tmp_path / "reader-out"
    out.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, "-c", _extract_hook_lock_reader()],
        env={"LOCK_PATH": str(lock_path), "OUT_DIR": str(out),
             "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        capture_output=True, text=True,
    )


def _hook_reader_accepts(lock: dict, tmp_path: Path) -> bool:
    """True iff the hook's lock reader accepts `lock` (exit 0).

    A traceback is a HARNESS failure, not a rejection, and is asserted away here
    rather than reported as False -- the same standard `_rejected` holds the
    generator to, applied to the hook's copy. It matters because the hook fails
    soft on ANY non-zero exit from this reader and emits one fixed verdict
    (`could not read $LOCK (invalid JSON or a bad field ...)`) whatever the
    cause: a deliberate `sys.exit` and an uncaught exception are the same exit
    code and the same verdict, and the $LOG that verdict points the operator at
    is the only surface where they differ. Reading the code alone would let every
    rejection test in this file pass against a reader that had lost its check and
    merely crashed instead.
    """
    proc = _run_hook_reader(lock, tmp_path)
    assert "Traceback" not in proc.stderr, proc.stderr
    return proc.returncode == 0


def test_hook_and_generator_share_the_validation_patterns():
    """The trust-boundary patterns must be byte-identical in both files.

    Two hand-written validators that must agree, with nothing asserting they do,
    is the drift bug FIX 6 names. Change one copy and this reddens.
    """
    hook = HOOK.read_text(encoding="utf-8")
    gen = GENERATOR.read_text(encoding="utf-8")
    for pattern in (
        r"[A-Za-z0-9][A-Za-z0-9._-]*",                                    # NAME
        r"[A-Za-z0-9._/+:@-]+",                                           # REF
        r"(?:https|file)://[A-Za-z0-9._~:/?#@%!$&()*+,;=\[\]-]+",         # URL
        r"[\s\x00-\x1f\x7f]",                                             # CONTROL
    ):
        assert pattern in hook, f"pattern missing from the hook: {pattern!r}"
        assert pattern in gen, f"pattern missing from the generator: {pattern!r}"


def test_hook_and_generator_share_the_source_cap():
    """MAX_SOURCES is one number, stated once per file, byte-identically.

    Same drift alarm as the patterns above, extended rather than re-invented,
    and for the same reason: the generator wrote a NINE-source lock at exit 0,
    `--check` blessed it, and the hook — which caps the list at 8 because every
    source is fetched before the session starts — then refused the WHOLE lock
    and installed none of its ten skills. A cap only one side knows is a cap
    that produces locks nobody can install.

    Spelled against `gsl.MAX_SOURCES` rather than a literal on both sides, so
    the hook's text is bound to the value the generator actually enforces.
    """
    literal = f"MAX_SOURCES = {gsl.MAX_SOURCES}"
    assert literal in HOOK.read_text(encoding="utf-8"), literal
    assert literal in GENERATOR.read_text(encoding="utf-8"), literal


def _lock_with_sources(count: int) -> dict:
    """A minimal, otherwise-valid lock carrying `count` federated sources."""
    return {
        "registry": "owner/primary",
        "ref": "0" * 40,
        "bundles": ["adam"],
        "sources": [
            {"registry": f"owner/extra{index}", "ref": "%040d" % index,
             "bundles": [f"b{index}"], "layout": "skills"}
            for index in range(1, count + 1)
        ],
        "skills": {},
    }


def test_the_hook_caps_the_number_of_sources(tmp_path):
    """At the cap, accepted; one over, the whole lock is refused."""
    assert _hook_reader_accepts(_lock_with_sources(gsl.MAX_SOURCES), tmp_path)
    assert not _hook_reader_accepts(_lock_with_sources(gsl.MAX_SOURCES + 1), tmp_path)


def test_the_generator_refuses_more_sources_than_the_hook_accepts(tmp_path):
    """Don't write a lock the hook rejects — the same rule as validate_ref.

    The boundary is asserted in both directions, because `>` vs `>=` is the
    whole content of a cap: one over must name the cap, and AT the cap the run
    must get far enough to fail for an unrelated reason (no checkout at the
    sibling path), which is only true if it was not refused.
    """
    primary = tmp_path / "registry"
    primary_sha = make_registry(primary, {"adam/alpha": SKILL_A})
    out = tmp_path / "skills.lock"

    def attempt(count: int) -> subprocess.CompletedProcess:
        args = ["--repo", str(primary), "--registry", "owner/primary", "--ref", primary_sha,
                "--bundles", "adam", "-o", str(out)]
        for index in range(1, count + 1):
            args += ["--source", f"b{index}=owner/extra{index}@{'%040d' % index}:skills"]
        return run_generator(*args)

    over = attempt(gsl.MAX_SOURCES + 1)
    assert _rejected(over), over.stderr
    assert f"at most {gsl.MAX_SOURCES}" in over.stderr, over.stderr
    assert not out.exists()

    at_cap = attempt(gsl.MAX_SOURCES)
    assert _rejected(at_cap), at_cap.stderr
    assert f"at most {gsl.MAX_SOURCES}" not in at_cap.stderr, at_cap.stderr
    assert "--source-repo" in at_cap.stderr, at_cap.stderr


# (field, value, expected-accept). Covers the good shapes and every rejection
# class FIX 1 / FIX 2 / FIX 6 turn on: control chars, non-URL/OWNER-REPO,
# leading-dot names, and refs the hook's charset forbids.
_VALIDATION_CASES = [
    ("registry", "owner/repo", True),
    ("registry", "Adam-S-Daniel/agentskills", True),
    ("registry", "https://github.com/o/r.git", True),
    ("registry", "file:///tmp/x/reg", True),
    ("registry", "ext::sh -c whoami", False),   # whitespace + helper shape
    ("registry", "ext::whoami", False),         # helper shape, no whitespace
    ("registry", "https://evil\ttab", False),   # control char
    ("registry", "file:///x\ny", False),        # control char
    ("registry", "..", False),                  # leading-dot name
    ("registry", ".hidden/x", False),           # leading-dot owner
    ("ref", "0" * 40, True),
    ("ref", "main", True),
    ("ref", "v1.2.3", True),
    ("ref", "HEAD~1", False),                   # '~' outside the ref charset
    ("ref", "a b", False),                      # whitespace
    ("ref", "x\ty", False),                     # control char
    ("layout", "skills", True),
    ("layout", "plugins/{bundle}/skills", True),
    ("layout", "../etc", False),                # '..' escape
    ("layout", "/etc", False),                  # absolute
    ("layout", "sk ill", False),                # whitespace
    ("layout", "a\tb", False),                  # control char
    # Skill DIRECTORY names. The hook rejects a lock key that is not
    # '<bundle>/<skill>' by the shared NAME pattern — and it rejects the WHOLE
    # lock, so a single stray directory in the bundle costs the session every
    # other skill too. A generator that happily digests such a directory into a
    # key is therefore writing a lock nobody can read.
    ("skill", "alpha", True),
    ("skill", "workflow-path-audit", True),     # internal hyphens are fine
    ("skill", "skills_doctor", True),           # '_' is fine, just not leading
    ("skill", "_template", False),              # leading '_' (a scaffold dir)
    ("skill", ".hidden", False),                # leading '.'
    ("skill", "-dash", False),                  # leading '-'
    ("skill", "sk ill", False),                 # embedded space
    ("skill", "skíll", False),                  # non-ASCII
]


def _generator_accepts_skill_dir(name: str, tmp_path: Path) -> bool:
    """True iff a bundle holding a skill directory called `name` yields a lock
    KEY for it.

    Probed through the generator's real behaviour — a fixture registry with a
    directory of that name — rather than by calling a named validator, because
    the property being pinned is about what reaches the lock, not about how the
    generator spells the check internally.

    False therefore covers both safe outcomes (refused outright, or never
    collected); the unsafe one, and the only one this returns True for, is a key
    landing in a lock the hook will then reject wholesale.
    """
    root = tmp_path / "skill-name-probe"
    make_registry(root, {f"adam/{name}": SKILL_A})
    out = tmp_path / "skill-name-probe.lock"
    proc = run_generator("--repo", str(root), "--registry", "owner/repo",
                         "--bundles", "adam", "-o", str(out))
    if proc.returncode != 0 or not out.exists():
        return False
    return f"adam/{name}" in json.loads(out.read_text(encoding="utf-8"))["skills"]


@pytest.mark.parametrize("field,value,accept", _VALIDATION_CASES)
def test_the_two_validators_accept_the_same_set(field, value, accept, tmp_path):
    """For each value, the generator and the hook reader must AGREE.

    Not merely 'both are strict somewhere' — the exact same accept/reject
    verdict, so a lock one side writes is a lock the other side reads.
    """
    if field == "skill":
        generator_accepts = _generator_accepts_skill_dir(value, tmp_path)
    else:
        generator_validator = {
            "registry": gsl.validate_registry,
            "ref": gsl.validate_ref,
            "layout": gsl.validate_layout,
        }[field]
        try:
            generator_validator(value, field)
            generator_accepts = True
        except gsl.GeneratorError:
            generator_accepts = False

    # Every probe lock carries `bundles`: routing is TOTAL, so the primary's
    # claim is required and a skills key whose bundle nobody claims is refused
    # on its own — which would mask the field this case is actually probing.
    if field == "layout":
        lock = {"registry": "owner/repo", "ref": "0" * 40, "bundles": ["adam"], "skills": {},
                "sources": [{"registry": "owner/other", "ref": "1" * 40,
                             "bundles": ["cms-platform"], "layout": value}]}
    elif field == "registry":
        lock = {"registry": value, "ref": "0" * 40, "bundles": ["adam"], "skills": {}}
    elif field == "skill":
        lock = {"registry": "owner/repo", "ref": "0" * 40, "bundles": ["adam"],
                "skills": {f"adam/{value}": "0" * 64}}
    else:  # ref
        lock = {"registry": "owner/repo", "ref": value, "bundles": ["adam"], "skills": {}}
    hook_accepts = _hook_reader_accepts(lock, tmp_path)

    assert generator_accepts == accept, f"generator disagreed on {field}={value!r}"
    assert hook_accepts == accept, f"hook disagreed on {field}={value!r}"


def test_a_lock_the_generator_writes_is_accepted_by_the_hook_reader(federated, tmp_path):
    """The critical direction, end to end: generator output -> hook reader.

    FIX 6's failure was a lock that generated clean at exit 0 but the hook
    rejected wholesale. Whatever the generator writes here, the hook must read.
    """
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    assert _hook_reader_accepts(
        json.loads(out.read_text(encoding="utf-8")), tmp_path
    )


def test_a_primary_ref_the_hook_rejects_is_not_written(registry, tmp_path):
    """FIX 6, primary side: `--ref 'HEAD~1'` must fail at authoring time.

    It resolves to a commit, so without the charset check the generator would
    happily record `"ref": "HEAD~1"` — a lock the hook then rejects wholesale,
    installing nothing and naming no field.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    proc = run_generator("--repo", str(root), "--ref", "HEAD~1", "-o", str(out))
    assert _rejected(proc), proc.stderr
    assert "ref" in proc.stderr
    assert not out.exists()


# --------------------------------------------------------------------------
# --check
# --------------------------------------------------------------------------

def test_check_exits_zero_on_a_current_lock(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    proc = run_generator("--repo", str(root), "--check", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_check_exits_one_on_a_hand_edited_digest(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    lock = json.loads(out.read_text(encoding="utf-8"))
    lock["skills"]["adam/alpha"] = "0" * 64
    out.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    proc = run_generator("--repo", str(root), "--check", "-o", str(out))
    assert proc.returncode == 1
    assert "adam/alpha" in proc.stdout


def test_check_exits_one_when_a_skill_was_added_at_the_pinned_ref(registry, tmp_path):
    """Stale lock: the ref moved on and the lock still lists the old skill set."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0

    _write(root / "plugins" / "adam" / "skills" / "gamma" / "SKILL.md", "gamma\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "add gamma")
    new_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             check=True, capture_output=True, text=True).stdout.strip()

    proc = run_generator("--repo", str(root), "--check", "--ref", new_sha, "-o", str(out))
    assert proc.returncode == 1
    assert "adam/gamma" in proc.stdout


def test_check_inherits_the_locks_ref_so_a_moved_head_is_not_a_failure(registry, tmp_path):
    """--check asks 'is this lock faithful to the ref it pins', not 'is it HEAD'.

    Committing anything at all moves HEAD, so a --check that silently re-pinned
    to HEAD could never be green on a committed lock -- and in CI, HEAD is a
    merge commit that is never the pinned SHA.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    pinned = json.loads(out.read_text(encoding="utf-8"))["ref"]

    _write(root / "unrelated.txt", "moves HEAD\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "move HEAD")

    proc = run_generator("--repo", str(root), "--check", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["ref"] == pinned


# --------------------------------------------------------------------------
# --check-current
#
# --check asks "is the lock faithful to the ref it pins"; --check-current asks
# "is that ref still the bundle". A lock pinned before a skill was added passes
# the first and fails the second, and the skill reaches no ephemeral surface --
# which is the silent no-op these tests exist to keep caught.
# --------------------------------------------------------------------------

def _lock_for(root: Path, out: Path) -> None:
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0


def test_check_current_exits_zero_when_the_tree_matches_the_pinned_ref(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_check_current_flags_a_skill_added_to_the_working_tree(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    _write(root / "plugins" / "adam" / "skills" / "gamma" / "SKILL.md", "gamma\n")

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "adam/gamma" in proc.stdout
    assert "added" in proc.stdout


def test_check_current_flags_a_skill_removed_from_the_working_tree(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    shutil.rmtree(root / "plugins" / "adam" / "skills" / "beta")

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "adam/beta" in proc.stdout
    assert "removed" in proc.stdout


def test_check_current_flags_a_skill_whose_content_changed(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    _write(root / "plugins" / "adam" / "skills" / "alpha" / "SKILL.md",
           "---\nname: alpha\n---\nalpha body, edited\n")

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "adam/alpha" in proc.stdout
    assert "changed" in proc.stdout
    # The untouched skill must not be dragged in with it.
    assert "adam/beta" not in proc.stdout


def test_check_current_failure_names_the_re_pin_command(registry, tmp_path):
    """A red check that does not say how to go green is a check people route around.

    It must name `--repin` specifically. The bare command it used to print is
    the ADR's de-federation trap: on a federated lock a plain generate takes
    `sources` from the command line alone, so following the remediation line
    literally drops every federated source and exits 0.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    _write(root / "plugins" / "adam" / "skills" / "gamma" / "SKILL.md", "gamma\n")

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode == 1
    assert "scripts/generate_skills_lock.py --repin" in proc.stdout
    # The substring above is satisfied by a `--repin --repin-source 'X@'` line
    # too, so without this the test is a green light wired to nothing: a bug
    # that printed the federated remediation for a PRIMARY drift would keep it
    # passing, and following that line advances the wrong pin.
    assert "--repin-source" not in proc.stdout


def test_check_current_failure_names_the_merge_cause(registry, tmp_path):
    """The local cause is obvious from the tree; the merge cause is not.

    On a freshly merged branch nothing the reader edited explains the red, and
    the old wording offered no reason to go and look at the base sha.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    _write(root / "plugins" / "adam" / "skills" / "gamma" / "SKILL.md", "gamma\n")

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode == 1
    assert "merged" in proc.stdout


def test_ignored_paths_reports_a_git_failure_rather_than_returning_nothing(tmp_path):
    """Failing open here would be a silent downgrade, not a safe default.

    An empty set reads exactly like "git ignores nothing", so a broken query
    would quietly restore the false positive this exists to remove — and the
    message would still be about the bundle having moved on.
    """
    with pytest.raises(gsl.GeneratorError) as failure:
        gsl.ignored_paths(tmp_path)
    assert "ls-files" in str(failure.value)


def _registry_ignoring_pycache(root: Path) -> str:
    """A fixture registry that gitignores build artefacts, as the real one does.

    Committed rather than left loose, so the ignore rule is part of the fixture
    and no test here depends on whatever `core.excludesFile` the machine has.
    """
    _write(root / ".gitignore", "__pycache__/\n")
    return make_registry(root, {"adam/alpha": SKILL_A, "adam/beta": SKILL_B})


def test_check_current_ignores_what_git_ignores(tmp_path):
    """Issue #81: a local test run left `__pycache__` behind and reddened this.

    `git status` showed nothing, so the check was reporting bytes that can
    never reach the pinned ref — it cleared with `rm -rf` and came straight
    back on the next run, which is how a check gets muted.
    """
    root = tmp_path / "registry"
    _registry_ignoring_pycache(root)
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    _write(root / "plugins" / "adam" / "skills" / "alpha" / "__pycache__" / "x.pyc", "junk\n")

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_check_current_still_flags_an_untracked_file_git_does_not_ignore(tmp_path):
    """Excluding more than git ignores would delete the reason this flag exists.

    The file-level half of the untracked-still-counts property that
    `test_check_current_flags_a_skill_added_to_the_working_tree` pins at the
    directory level: ask git which files are IGNORED, never merely which are
    untracked.
    """
    root = tmp_path / "registry"
    _registry_ignoring_pycache(root)
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    _write(root / "plugins" / "adam" / "skills" / "alpha" / "notes-draft.md", "draft\n")

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "adam/alpha" in proc.stdout
    assert "changed" in proc.stdout


def test_check_is_blind_to_what_check_current_catches(registry, tmp_path):
    """The whole reason --check-current is a SEPARATE flag, in one test.

    An added-but-unpinned skill leaves the lock a perfectly faithful
    description of the commit it pins -- so --check is green -- while that
    skill is delivered to nobody.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    _write(root / "plugins" / "adam" / "skills" / "gamma" / "SKILL.md", "gamma\n")

    faithful = run_generator("--repo", str(root), "--check", "-o", str(out))
    current = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert faithful.returncode == 0, faithful.stdout + faithful.stderr
    assert current.returncode == 1, current.stdout + current.stderr


def test_check_and_check_current_run_together_and_both_report(registry, tmp_path):
    """Passing both runs both; the exit code is the worse of the two."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    _write(root / "plugins" / "adam" / "skills" / "gamma" / "SKILL.md", "gamma\n")

    proc = run_generator("--repo", str(root), "--check", "--check-current", "-o", str(out))
    assert proc.returncode == 1
    assert "OK:" in proc.stdout        # --check's verdict
    assert "FAILED:" in proc.stdout    # --check-current's


def test_check_current_on_a_missing_lock_errors_cleanly(tmp_path):
    proc = run_generator("--check-current", "-o", str(tmp_path / "absent.lock"))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr


def test_check_current_on_an_unreachable_pinned_ref_errors_cleanly(registry, tmp_path):
    """The CI shape: a shallow checkout that does not contain the pinned commit."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)
    lock = json.loads(out.read_text(encoding="utf-8"))
    lock["ref"] = "0" * 40
    out.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "0" * 40 in proc.stderr


# --------------------------------------------------------------------------
# --check-format
#
# The third question, and the reason it is not a widening of either flag
# above. `--check-current` compares two freshly digested trees and never reads
# the lock's stored values at all; `--check` reads them only inside a
# whole-document comparison, which reports a wrong SHAPE in the same words as
# content drift. Eight consumer locks in this fleet are stored as bare 64-hex,
# all pinning 94cdcc81, and the fleet bumper's anti-churn gate is
# `--check-current` — which says OK, because the bundle content genuinely has
# not moved. Green gate, skipped re-pin, shape never healed. These tests pin
# that gap open so it cannot close by accident, and pin the new flag's promise
# that it reads the FILE and nothing else.
# --------------------------------------------------------------------------

def _unlabel(path: Path, only: Optional[set] = None) -> None:
    """Strip `sha256:` back off a lock's stored digests, in place.

    Deliberately self-proving: it asserts each value it rewrites was labelled
    to begin with, and that it rewrote at least one. A fixture helper that
    silently mutated nothing would leave every test below asserting that a
    correct lock is correct.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["skills"], "nothing to unlabel — the fixture wrote an empty lock"
    changed = 0
    for name, digest in document["skills"].items():
        if only is not None and name not in only:
            continue
        assert digest.startswith(gsl.LOCK_DIGEST_PREFIX), (name, digest)
        document["skills"][name] = digest[len(gsl.LOCK_DIGEST_PREFIX):]
        changed += 1
    assert changed, "unlabelled no digest at all"
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def test_check_format_exits_zero_on_a_lock_this_generator_wrote(registry, tmp_path):
    """The writer's output is the shape the checker accepts, by construction."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0

    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK:" in proc.stdout
    assert "(2 skills)" in proc.stdout    # named, so a silent empty pass is visible


def test_this_repos_committed_lock_passes_check_format():
    """Dogfood. This repo's own lock is the one every consumer copies the shape
    of, and it is the reference an adopter compares theirs against."""
    proc = run_generator("--check-format", "-o", str(REPO_ROOT / "skills.lock"))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_check_current_is_blind_to_what_check_format_catches(registry, tmp_path):
    """The stranded-lock defect, in one test — the reason this flag exists.

    A lock whose digests are bare hex still describes the bundle at the ref it
    pins perfectly: both sides of --check-current are freshly digested trees,
    so it answers OK. That is the fleet bumper's anti-churn gate, so the heal
    it would perform (--repin relabels) is never reached, and eight consumer
    locks have sat in this state for as long as the `adam` bundle has stood
    still. Only a question asked directly of the STORED values sees it.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _unlabel(out)

    current = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    shape = run_generator("--check-format", "-o", str(out))
    assert current.returncode == 0, current.stdout + current.stderr
    assert "OK:" in current.stdout
    assert shape.returncode == 1, shape.stdout + shape.stderr


def test_a_repin_is_what_heals_a_bare_lock(registry, tmp_path):
    """--check-format's remediation line names --repin; this is that claim, run.

    The writer was never the broken half — labelling happens at the document
    boundary on every write — so the repair needs no new code path, only a gate
    that can tell the bumper to take the one that exists.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _unlabel(out)
    assert run_generator("--check-format", "-o", str(out)).returncode == 1

    assert run_generator("--repo", str(root), "--repin", "-o", str(out)).returncode == 0
    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_check_format_fails_a_bare_lock_and_names_every_offender(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _unlabel(out)

    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAILED:" in proc.stdout
    assert "adam/alpha" in proc.stdout
    assert "adam/beta" in proc.stdout
    # The remediation has to name the SAFE command. A reader told to "regenerate"
    # would rerun a plain generate, which takes `sources` off the command line
    # alone and de-federates any consumer lock that has them.
    assert "--repin" in proc.stdout


def test_check_format_fails_a_mixed_lock_and_names_only_the_bare_ones(registry, tmp_path):
    """Partway-healed is a real state: a hand edit fixes the lines someone read.

    Reported per skill rather than as one verdict for the file, so the count in
    the summary is the number of digests actually wrong.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _unlabel(out, only={"adam/alpha"})

    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "1 of 2 digests" in proc.stdout
    assert "adam/alpha" in proc.stdout
    assert "adam/beta" not in proc.stdout


def _suggested_command(stdout: str) -> str:
    """The one line of a --check-format failure a reader is meant to RUN.

    Pulled out by its `--repin` rather than by line number: the surrounding
    prose is what changes, and a helper anchored on prose would keep passing
    against a report whose command line had gone missing entirely.
    """
    lines = [line.strip() for line in stdout.splitlines()
             if "generate_skills_lock.py --repin" in line]
    assert len(lines) == 1, stdout
    return lines[0]


def test_check_format_suggests_a_repin_pinned_to_the_locks_own_ref(registry, tmp_path):
    """The remediation must DO what the sentence one line above it promises.

    `--repin` deliberately does not inherit `ref`, so a suggested command
    without one falls through to `resolve_ref(repo, "HEAD")` and rebuilds every
    digest from whatever commit the clone is sitting on — advancing the pin and
    re-attesting the lock over a different tree, under a sentence promising the
    digests are recomputed "from the pinned ref". Measured on a copy of
    repo-settings' real lock before the fix: the pin moved off 94cdcc81 onto
    the clone's HEAD.

    Both legs matter. The first asserts the printed command, so a reader who
    runs it verbatim gets a pin-preserving RELABEL. The second runs the same
    command MINUS `--ref` and asserts it does not — without that control this
    would pass just as well against a fixture where the two answers coincide,
    which is exactly why the defect survived review: today, against a bundle
    that has not moved, they DO coincide.
    """
    root, pinned = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    before = json.loads(out.read_text(encoding="utf-8"))["skills"]
    _unlabel(out)

    # The bundle moves after the lock was pinned — the state the whole hazard
    # needs, and the one a stranded consumer lock is actually in.
    _write(root / gsl.layout_dir(gsl.DEFAULT_LAYOUT, "adam") / "alpha" / "notes.md",
           "a different note\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "move the bundle")
    assert _head(root) != pinned

    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"--ref {pinned}" in _suggested_command(proc.stdout), proc.stdout

    faithful = tmp_path / "faithful.lock"
    faithful.write_text(out.read_text(encoding="utf-8"), encoding="utf-8", newline="")
    assert run_generator("--repin", "--ref", pinned, "--repo", str(root),
                         "-o", str(faithful)).returncode == 0
    healed = json.loads(faithful.read_text(encoding="utf-8"))
    assert healed["ref"] == pinned, "a SHAPE repair must not advance the pin"
    assert healed["skills"] == before, "a SHAPE repair must relabel, not recompute"

    # The control: the command as it used to be printed. Same lock, same clone,
    # no --ref — and it does neither of the two things asserted above.
    drifting = tmp_path / "drifting.lock"
    drifting.write_text(out.read_text(encoding="utf-8"), encoding="utf-8", newline="")
    assert run_generator("--repin", "--repo", str(root),
                         "-o", str(drifting)).returncode == 0
    drifted = json.loads(drifting.read_text(encoding="utf-8"))
    assert drifted["ref"] != pinned
    assert drifted["skills"] != before


def test_check_format_will_not_echo_a_hand_edited_ref_into_the_command(registry,
                                                                      tmp_path):
    """That line is copy-pasteable, and the fleet bumper slices it into a PR body.

    The document arrives as found on disk, so `ref` is arbitrary text until
    something checks it. A shell metacharacter reaching a reader's terminal
    through a report ABOUT a malformed lock would be the report becoming the
    vulnerability. `_REF_RE` is the guard; the placeholder is the fallback, and
    the run still names the real problem.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _unlabel(out)
    hostile = "$(touch /tmp/pwned); rm -rf ~"
    _edit_lock(out, ref=hostile)

    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert hostile not in proc.stdout + proc.stderr
    command = _suggested_command(proc.stdout)
    assert "--ref <the commit this lock pins>" in command, command
    assert "lowercase hex" in proc.stdout    # still the verdict it came to give


# Both case lists below are NAMED and length-asserted rather than written
# inline. An empty parametrize list does not fail, it SKIPS — measured: pytest
# reports "got empty parameter set" and exits 0 — so a list that lost its cases
# would quietly stop guarding anything while the suite stayed green and only
# the total count moved. A module-level assert is a collection ERROR instead,
# the same self-proving discipline `_unlabel` uses on the fixture it edits.
# "-o" is the case a charset guard alone lets through: it is legal in
# `_REF_RE`, so before the dash check it was echoed straight into the command
# as `--ref -o --repo ...`, where the ref stops being a value and becomes an
# OPTION. That fails loudly (argparse exit 2) rather than silently, but a
# remediation line that cannot run is still a remediation line that does not
# do what the sentence above it promises.
_UNUSABLE_REFS = [None, 7, "", "  ", "a" * 40 + " --repo /elsewhere", "-o"]
assert len(_UNUSABLE_REFS) == 6


@pytest.mark.parametrize("ref_value", _UNUSABLE_REFS)
def test_check_format_falls_back_to_a_placeholder_ref_rather_than_omitting_it(
        registry, tmp_path, ref_value):
    """A lock with no usable `ref` is the state a bad merge resolution leaves.

    Omitting the flag would silently restore the defect this pair exists to
    close, so the fallback is a PLACEHOLDER the reader must fill in, plus a
    line saying why. `--repin` refuses such a lock anyway, and saying so here
    is cheaper than discovering it one failed command later.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _unlabel(out)
    _edit_lock(out, **({"ref": _DROP} if ref_value is None else {"ref": ref_value}))

    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "--ref <the commit this lock pins>" in _suggested_command(proc.stdout)
    assert "no usable 'ref'" in proc.stdout


# --------------------------------------------------------------------------
# the FAILED: / ERROR: prefix contract
#
# A caller keys a WRITE off the prefix. _agent-guidance's
# bump-consumer-locks.sh greps `^FAILED:` in this flag's output to set
# `repin_reason=format` and re-pin a consumer's lock, and routes everything
# else to a branch that reports and counts WITHOUT rewriting anything — under a
# comment reading "Only the flag's own FAILED: means 'these digests are
# malformed'". These pin that sentence true from this side, since the caller
# lives in another repo and cannot be tested from here. The exit code carries
# none of this: every condition below exits 1.
# --------------------------------------------------------------------------

def test_only_malformed_digests_are_reported_as_failed(registry, tmp_path):
    """The one verdict for which a re-pin is the right repair."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _unlabel(out)

    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert proc.stdout.startswith("FAILED:"), proc.stdout


_NOTHING_TO_CHECK = [
    ({}, "lists no skills at all"),           # nothing there
    ("not a map", "no usable 'skills' map"),  # nothing to read
    (["adam/alpha"], "no usable 'skills' map"),
    (7, "no usable 'skills' map"),
    (None, "no usable 'skills' map"),
]
assert len(_NOTHING_TO_CHECK) == 5


@pytest.mark.parametrize("skills_value, expected", _NOTHING_TO_CHECK)
def test_nothing_to_check_is_an_error_not_a_failed(registry, tmp_path,
                                                   skills_value, expected):
    """"There is nothing here whose shape could be wrong" is not "these digests
    are malformed", and a re-pin is the wrong repair for it — most sharply for
    an empty map, which a re-pin reproduces exactly, so keying the repair off
    it produced a nightly loop with no automated exit (see
    `report_digest_format`). Still exit 1: this is about which ANSWER the run
    gives, not about letting it pass."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _edit_lock(out, skills=skills_value)

    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert proc.stdout.startswith("ERROR:"), proc.stdout
    assert "FAILED:" not in proc.stdout, proc.stdout
    assert expected in proc.stdout
    assert "Traceback" not in proc.stderr


def test_a_lock_the_generator_cannot_read_at_all_never_says_failed(tmp_path):
    """The conditions the caller's comment ALREADY claimed were ERROR:, asserted
    rather than assumed — a missing file, a directory at -o, a top-level array
    and invalid JSON. Each is "the question could not be answered", and none of
    them is a licence to rewrite a consumer's lock."""
    missing = tmp_path / "absent.lock"
    directory = tmp_path / "a-directory.lock"
    directory.mkdir()
    array = tmp_path / "array.lock"
    array.write_text("[]\n", encoding="utf-8")
    garbage = tmp_path / "garbage.lock"
    garbage.write_text("{not json\n", encoding="utf-8")

    unreadable = (missing, directory, array, garbage)
    for lock in unreadable:
        proc = run_generator("--check-format", "-o", str(lock))
        assert proc.returncode != 0, (lock, proc.stdout, proc.stderr)
        combined = proc.stdout + proc.stderr
        assert "FAILED:" not in combined, (lock, combined)
        assert "ERROR:" in combined, (lock, combined)
        assert "Traceback" not in proc.stderr, (lock, proc.stderr)
    # A loop is only a guard while it has something to iterate. Stated, because
    # emptying the tuple above is the one edit that turns this whole test green
    # against a generator that says FAILED: to every one of them.
    assert len(unreadable) == 4


def test_check_format_ignores_repo_entirely(registry, tmp_path):
    """`--repo` is accepted here and never read — the promise the help text makes.

    Left legal rather than refused because the flag COMPOSES with --check,
    which does read it; an argparse error in one composition and a requirement
    in the other is a mode-dependence not worth a nit. Accepted-and-ignored is
    only a promise if something holds it, so: the verdict must be BYTE-
    IDENTICAL with no --repo, with a nonexistent one, and with a real clone of
    a DIFFERENT registry whose skills digest differently. The last leg is the
    one that matters — a flag that quietly consulted the clone would still
    agree with itself across the first two.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _unlabel(out)

    other = tmp_path / "other-registry"
    make_registry(other, {"adam/alpha": SKILL_B, "adam/beta": SKILL_A})
    absent = tmp_path / "no-such-clone"
    assert not absent.exists()

    baseline = run_generator("--check-format", "-o", str(out))
    assert baseline.returncode == 1, baseline.stdout + baseline.stderr
    clones = (str(absent), str(other), str(REPO_ROOT))
    for repo_arg in clones:
        other_run = run_generator("--check-format", "--repo", repo_arg, "-o", str(out))
        assert other_run.returncode == baseline.returncode, repo_arg
        assert other_run.stdout == baseline.stdout, repo_arg
    # Same reason as above: an empty tuple here agrees with everything.
    assert len(clones) == 3


def test_check_format_fails_on_an_empty_skills_map(tmp_path):
    """"No work" and "no errors" must not be the same answer.

    A generate over a bundle with no skills writes an empty map legitimately
    (test_an_empty_bundle_yields_an_empty_skills_map), so every value in it is
    trivially well-shaped. This flag gates a REPAIR sweep, and a repair gate
    that greens on an empty file is how a sweep reports success for having
    inspected nothing.
    """
    root = tmp_path / "registry"
    make_registry(root, {"adam/alpha": SKILL_A})
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "--bundles", "fastmail",
                         "-o", str(out)).returncode == 0
    assert json.loads(out.read_text(encoding="utf-8"))["skills"] == {}

    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "no skills" in proc.stdout
    # ERROR:, not FAILED: — the caller re-pins off FAILED:, and a re-pin over a
    # registry with no skills writes this same empty map straight back. See
    # test_nothing_to_check_is_an_error_not_a_failed.
    assert proc.stdout.startswith("ERROR:"), proc.stdout


@pytest.mark.parametrize("digest", [
    "a" * 63,                                  # bare, one short
    gsl.LOCK_DIGEST_PREFIX + "a" * 63,         # labelled, one short
    gsl.LOCK_DIGEST_PREFIX + "a" * 65,         # labelled, one long
    gsl.LOCK_DIGEST_PREFIX + "A" * 64,         # uppercase — hexdigest() is not
    gsl.LOCK_DIGEST_PREFIX + "g" * 64,         # 64 characters, not hex
    gsl.LOCK_DIGEST_PREFIX,                    # the label with nothing behind it
    gsl.LOCK_DIGEST_PREFIX * 2 + "a" * 64,     # labelled twice
    "sha512:" + "a" * 64,                      # a different algorithm's label
    " " + gsl.LOCK_DIGEST_PREFIX + "a" * 64,   # leading whitespace
    gsl.LOCK_DIGEST_PREFIX + "a" * 64 + "\n",  # trailing newline
])
def test_check_format_rejects_a_digest_of_the_wrong_length_or_case(
        registry, tmp_path, digest):
    """Length AND case AND alphabet, all of them exactly.

    The lock is a byte-for-byte attestation: every reader — the bootstrap hook
    included — compares a stored digest against one it computed itself, and
    `hexdigest()` emits 64 lowercase hex characters and nothing else. Anything
    that merely LOOKS like a digest was not written by this generator, so
    accepting it here would green a lock whose next integrity check reports
    tampering.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    document["skills"]["adam/alpha"] = digest
    out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "adam/alpha" in proc.stdout


@pytest.mark.parametrize("digest", [1, None, True, ["sha256:" + "a" * 64], {}])
def test_check_format_reports_a_type_confused_digest_rather_than_tracebacking(
        registry, tmp_path, digest):
    """The lock arrives as found ON DISK and may be any shape at all.

    A verdict that raises instead of printing is a verdict nobody reads — the
    same standard `_render_sources` is written to, and the reason the type name
    is reported rather than the value.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    document["skills"]["adam/alpha"] = digest
    out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    assert "adam/alpha" in proc.stdout
    assert type(digest).__name__ in proc.stdout


@pytest.mark.parametrize("skills_value", ["not a map", ["adam/alpha"], 7, None])
def test_check_format_reports_a_skills_map_that_is_not_a_map(registry, tmp_path,
                                                             skills_value):
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    document["skills"] = skills_value
    out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    assert "'skills'" in proc.stdout
    assert proc.stdout.startswith("ERROR:"), proc.stdout    # nothing to read != malformed


def test_check_format_never_echoes_the_offending_digest(registry, tmp_path):
    """The offending value is a bare 64-hex string — the exact token gitleaks'
    `generic-api-key` rule fires on beside a keyword-bearing name, which is the
    whole reason LOCK_DIGEST_PREFIX exists. Printing one into a CI log in order
    to complain about it would put it straight back into scanned text."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _unlabel(out)
    bare = json.loads(out.read_text(encoding="utf-8"))["skills"]["adam/alpha"]
    assert re.fullmatch(r"[0-9a-f]{64}", bare), bare

    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert bare not in proc.stdout + proc.stderr


def test_check_format_bounds_the_names_it_prints(tmp_path):
    """Every digest in a lock can be wrong at once — all eight stranded consumer
    locks were, and a 22-skill consumer would be 22. An unbounded list scrolls
    its own remediation line off the top of a CI log."""
    root = tmp_path / "registry"
    many = {f"adam/skill-{index:02d}": SKILL_B for index in range(14)}
    make_registry(root, many)
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _unlabel(out)

    proc = run_generator("--check-format", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "14 of 14 digests" in proc.stdout
    listed = [line for line in proc.stdout.splitlines()
              if line.startswith("  - adam/skill-")]
    assert len(listed) == gsl._FORMAT_REPORT_CAP, proc.stdout
    assert f"and {14 - gsl._FORMAT_REPORT_CAP} more" in proc.stdout


def test_check_format_reads_the_file_alone_and_needs_no_checkout(registry, tmp_path):
    """The calling convention, not an implementation detail.

    _agent-guidance's bump-consumer-locks.sh runs this per consumer lock BEFORE
    it has cloned the registry, so a single git call on this path would fail
    the sweep's very first use of it. `--repo` is pointed at a path that does
    not exist; the control is the last leg, where --check with the same bogus
    `--repo` DOES fail — without it this would pass just as well against a flag
    that quietly used the default repo.

    The lock with NO `ref` is the leg that actually holds the early return in
    place. A lock that HAS one never reaches `resolve_ref(repo, "HEAD")` at
    all, so this test was green against the fall-through too until it grew this
    case (measured). A missing `ref` is exactly what a botched merge-conflict
    resolution leaves behind — the state a repair sweep is most likely to meet
    — and the shape question has an answer regardless: it is asked of `skills`
    and of nothing else.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    absent = tmp_path / "no-such-clone"
    assert not absent.exists()

    shape = run_generator("--check-format", "--repo", str(absent), "-o", str(out))
    assert shape.returncode == 0, shape.stdout + shape.stderr

    refless = tmp_path / "refless.lock"
    refless.write_text(out.read_text(encoding="utf-8"), encoding="utf-8", newline="")
    _edit_lock(refless, ref=_DROP)
    assert "ref" not in json.loads(refless.read_text(encoding="utf-8"))
    shapeless = run_generator("--check-format", "--repo", str(absent), "-o", str(refless))
    assert shapeless.returncode == 0, shapeless.stdout + shapeless.stderr

    faithful = run_generator("--check", "--repo", str(absent), "-o", str(out))
    assert faithful.returncode != 0, faithful.stdout + faithful.stderr


def test_check_format_runs_alongside_check_and_the_exit_code_is_the_worst(
        registry, tmp_path):
    """Composes the way --check and --check-current always have.

    A bare lock is stale to --check as well (a wrong shape is a wrong byte), so
    this asserts the composition rather than an independence the two do not
    have: both verdicts are printed, and the run exits 1.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _unlabel(out)

    proc = run_generator("--repo", str(root), "--check-format", "--check", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "lowercase hex" in proc.stdout          # --check-format's verdict
    assert "is stale" in proc.stdout               # --check's


def test_check_format_alongside_check_current_reports_both_verdicts(registry, tmp_path):
    """The pairing a heal sweep would actually run: 'has the content moved' and
    'is the stored shape right' are independent, and a bare lock answers OK to
    the first and FAILED to the second in the same run."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _unlabel(out)

    proc = run_generator("--repo", str(root), "--check-format", "--check-current",
                         "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAILED:" in proc.stdout    # --check-format's
    assert "OK: the working tree still matches" in proc.stdout    # --check-current's


def test_check_format_on_a_missing_lock_errors_cleanly(tmp_path):
    proc = run_generator("--check-format", "-o", str(tmp_path / "absent.lock"))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr


# --------------------------------------------------------------------------
# the merge race (issue #80)
#
# A re-pin asserts "the tree at ref X is what this lock describes", and the
# assertion can be falsified by `main` moving rather than by either branch
# being wrong. These two pin which shapes merge silently and which do not.
# --------------------------------------------------------------------------

def _repin(root: Path, message: str) -> None:
    """Regenerate `root`'s committed lock against its own HEAD and commit that."""
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()
    assert run_generator("--repo", str(root), "--ref", head,
                         "-o", str(root / "skills.lock")).returncode == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def _edit_and_commit(root: Path, skill: str) -> None:
    _write(root / "plugins" / "adam" / "skills" / skill / "SKILL.md",
           f"---\nname: {skill}\n---\n{skill} body, edited\n")
    _git(root, "commit", "-q", "-am", f"edit {skill}")


def _base_repo_with_a_committed_lock(tmp_path: Path) -> Path:
    """A registry whose two edited skills sit FAR APART in the lock's skills map.

    Deliberately eight skills rather than two: `alpha` and `theta` are five
    lines apart once the map is serialized, so their digest lines are separate
    diff hunks and merge cleanly on their own. That leaves `ref` and
    `generated_from` as the only lines both re-pins rewrite — which is the
    mechanism `test_two_repinned_branches_conflict_in_the_lock` is about. With
    two adjacent skills the merge conflicts either way and the test would pass
    without binding anything.
    """
    root = tmp_path / "registry"
    names = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta")
    make_registry(root, {f"adam/{name}": {"SKILL.md": f"---\nname: {name}\n---\n{name} body\n"}
                         for name in names})
    _repin(root, "pin the base")
    return root


def _merge(root: Path, branch: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), "merge", "--no-edit", branch],
                          capture_output=True, text=True)


def test_a_merge_can_leave_the_lock_stale_with_no_conflict(tmp_path):
    """The one shape that merges silently: `main` between a content commit and its re-pin.

    The branch is green on both flags when it is cut and still green when it
    lands; what changed underneath it is `main`. Nothing conflicts, so nobody
    is prompted, and the first anyone hears is CI on `main`.
    """
    root = _base_repo_with_a_committed_lock(tmp_path)
    lock = root / "skills.lock"

    _git(root, "checkout", "-q", "-b", "feature")
    _edit_and_commit(root, "alpha")
    _repin(root, "re-pin to the alpha edit")
    assert run_generator("--repo", str(root), "--check", "-o", str(lock)).returncode == 0
    assert run_generator("--repo", str(root), "--check-current",
                         "-o", str(lock)).returncode == 0

    # `main` moves onto a DIFFERENT locked skill, and its own re-pin has not
    # landed yet — so `main` is already red here, before the merge.
    _git(root, "checkout", "-q", "main")
    _edit_and_commit(root, "theta")

    merged = _merge(root, "feature")
    assert merged.returncode == 0, merged.stdout + merged.stderr

    assert run_generator("--repo", str(root), "--check", "-o", str(lock)).returncode == 0
    proc = run_generator("--repo", str(root), "--check-current", "-o", str(lock))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "adam/theta" in proc.stdout


def test_two_repinned_branches_conflict_in_the_lock(tmp_path):
    """And the shape that does NOT merge silently, which is why this is not urgent.

    When both sides follow the documented order, both re-pins rewrite the same
    `ref` and `generated_from` lines, so `skills.lock` conflicts even though
    the two branches touched different skills — a human is forced to look.
    """
    root = _base_repo_with_a_committed_lock(tmp_path)

    _git(root, "checkout", "-q", "-b", "feature")
    _edit_and_commit(root, "alpha")
    _repin(root, "re-pin to the alpha edit")

    _git(root, "checkout", "-q", "main")
    _edit_and_commit(root, "theta")
    _repin(root, "re-pin to the theta edit")

    merged = _merge(root, "feature")
    assert merged.returncode != 0, merged.stdout + merged.stderr
    conflicted = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", "--diff-filter=U"],
        check=True, capture_output=True, text=True).stdout.split()
    assert "skills.lock" in conflicted
    # The skills themselves merge cleanly; it is the lock that catches it.
    assert not [path for path in conflicted if path.startswith("plugins/")]


def test_check_on_a_missing_lock_errors_cleanly(tmp_path):
    proc = run_generator("--check", "-o", str(tmp_path / "absent.lock"))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr


def test_unwritable_output_errors_cleanly(registry, tmp_path):
    root, _ = registry
    proc = run_generator("--repo", str(root), "-o", str(tmp_path / "nope" / "skills.lock"))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr


# --------------------------------------------------------------------------
# --repin
#
# A re-pin is the one WRITE that must not decide afresh what a lock means: it
# advances an existing lock's `ref` and inherits everything else off the lock
# itself. The trap it closes is named in _agent-guidance's ADR 0001 — a plain
# generate takes `sources` from the command line alone, so re-pinning a
# federated lock by rerunning the generate command drops whatever --source the
# command line does not repeat, writes a de-federated lock, and exits 0.
# --------------------------------------------------------------------------

def _head(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


def _move_head(root: Path, message: str = "move HEAD") -> str:
    """Commit an unrelated file so HEAD advances without any skill changing.

    The body carries the CURRENT head, so calling this twice with the same
    message still changes the file. Writing the message alone made the second
    call a no-op commit, which `_git`'s `check=True` turned into a bare
    CalledProcessError with both streams sent to DEVNULL — the least legible
    failure available, in a helper whose docstring advertises it as reusable.
    """
    _write(root / f"{message.replace(' ', '-')}.txt", f"{message} from {_head(root)}\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _head(root)


def _edit_lock(path: Path, **fields) -> None:
    """Hand-edit a lock's top-level fields, the way a bad merge resolution would.

    A field set to `_DROP` is deleted rather than assigned, which is the shape
    a conflict resolution actually leaves behind.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    for key, value in fields.items():
        if value is _DROP:
            document.pop(key, None)
        else:
            document[key] = value
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


_DROP = object()


def test_repin_on_a_federated_lock_preserves_every_source_verbatim(federated, tmp_path):
    """The ADR's named trap, made unrepresentable rather than merely avoidable.

    The re-pin repeats no --source at all, which is exactly the invocation that
    de-federates a lock through a plain generate. Every field of every source
    has to come back byte-identical, and the federated skills have to still be
    in the map — a `sources` array that survived while its skills did not
    would be a lock that promises a registry it no longer digests.

    The EXTRA registry's HEAD moves too, and that is what gives the
    byte-identical assertion teeth: with only the primary advancing, the extra
    source's pinned ref already equals its repo's HEAD, so "inherited" and
    "silently re-resolved to HEAD" produce the same bytes and the test cannot
    tell them apart. A re-pin that re-resolved a federated ref would pull
    unreviewed content from another registry into every consumer's bootstrap
    under the banner of a routine bump.
    """
    primary, primary_sha, extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    before = json.loads(out.read_text(encoding="utf-8"))

    _move_head(extra)
    advanced = _move_head(primary)
    proc = run_generator("--repo", str(primary), "--repin", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    after = json.loads(out.read_text(encoding="utf-8"))
    assert after["sources"] == before["sources"]
    assert "cms-platform/deploy" in after["skills"]
    assert after["skills"]["cms-platform/deploy"] == before["skills"]["cms-platform/deploy"]
    # ...and the pin actually moved, which is the half that makes it a re-pin.
    assert after["ref"] == advanced != primary_sha
    assert after["generated_from"] == advanced


def test_a_plain_generate_still_does_not_inherit_the_locks_sources(federated, tmp_path):
    """Asserted so nobody 'fixes' the trap by teaching generate to inherit.

    That would silently change what a fresh lock means: today its `sources` are
    exactly what the command line says, and a consumer setting one up reads the
    command to know what it federates. --repin is where inheritance belongs,
    because it is advancing something whose identity is already decided.
    """
    primary, _primary_sha, _extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    advanced = _move_head(primary)
    proc = run_generator("--repo", str(primary), "--registry", primary.resolve().as_uri(),
                         "--ref", advanced, "--bundles", "adam", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    de_federated = json.loads(out.read_text(encoding="utf-8"))
    assert "sources" not in de_federated
    assert "cms-platform/deploy" not in de_federated["skills"]


def test_repin_inherits_the_locks_registry_and_bundles(tmp_path):
    """Identity comes off the lock, so a re-pin needs no flag to stay itself.

    The bundle is deliberately not `adam`: inheriting DEFAULT_BUNDLES by
    accident would be indistinguishable from inheriting the lock's own.
    """
    root = tmp_path / "registry"
    make_registry(root, {"extras/alpha": SKILL_A})
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "--registry", "owner/elsewhere",
                         "--ref", _head(root), "--bundles", "extras",
                         "-o", str(out)).returncode == 0

    _move_head(root)
    proc = run_generator("--repo", str(root), "--repin", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    lock = json.loads(out.read_text(encoding="utf-8"))
    assert lock["registry"] == "owner/elsewhere"
    assert lock["bundles"] == ["extras"]
    assert "extras/alpha" in lock["skills"]


def test_repin_on_a_single_source_lock_still_omits_the_sources_key(registry, tmp_path):
    """Not `"sources": []` — a re-pin must not churn a lock into a new shape."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0

    _move_head(root)
    proc = run_generator("--repo", str(root), "--repin", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert '"sources"' not in out.read_text(encoding="utf-8")


def test_repin_pins_exactly_the_ref_it_is_given(registry, tmp_path):
    """--ref wins over HEAD, so a re-pin can land on a reviewed commit."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0

    # Both with the DEFAULT message, which is also the case that used to die
    # inside _move_head: identical content staged nothing and `git commit`
    # exited 1 through a check=True call with both streams at DEVNULL.
    target = _move_head(root)
    head = _move_head(root)
    assert head != target

    proc = run_generator("--repo", str(root), "--repin", "--ref", target, "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lock = json.loads(out.read_text(encoding="utf-8"))
    assert lock["ref"] == target
    assert lock["generated_from"] == target


def test_repin_reports_the_same_wrote_line_a_generate_does(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0

    advanced = _move_head(root)
    proc = run_generator("--repo", str(root), "--repin", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith(f"Wrote {out}:")
    assert advanced in proc.stdout


def test_repin_on_a_missing_lock_refuses_rather_than_creating_one(tmp_path):
    """A lock that does not exist has no identity to inherit.

    Creating one decides which registry and which bundles a consumer installs,
    which is a decision rather than a bump — so it stays a plain generate, and
    the refusal says so instead of quietly guessing the defaults.
    """
    root = tmp_path / "registry"
    make_registry(root, {"adam/alpha": SKILL_A})
    out = tmp_path / "absent.lock"

    proc = run_generator("--repo", str(root), "--repin", "-o", str(out))
    assert _rejected(proc), proc.stdout + proc.stderr
    assert str(out) in proc.stderr
    assert "--repin" in proc.stderr
    assert not out.exists()


@pytest.mark.parametrize("verify_flag", ["--check", "--check-current", "--check-format"])
def test_repin_with_a_verify_flag_is_an_argparse_error(registry, tmp_path, verify_flag):
    """One writes and one verifies: a run asking for both has no answer to give.

    Rejected by argparse rather than resolved by precedence — silently picking
    a side would report on a lock the caller did not mean, and the lock on disk
    is what everyone reads afterwards.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    before = out.read_text(encoding="utf-8")
    _move_head(root)

    proc = run_generator("--repo", str(root), "--repin", verify_flag, "-o", str(out))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "usage:" in proc.stderr
    assert not _rejected(proc)    # argparse refused it, not the generator
    assert out.read_text(encoding="utf-8") == before


def test_a_repinned_lock_passes_check_immediately(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0

    _move_head(root)
    assert run_generator("--repo", str(root), "--repin", "-o", str(out)).returncode == 0

    proc = run_generator("--repo", str(root), "--check", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_repinned_federated_lock_still_matches_the_source_stated_on_the_command_line(
        federated, tmp_path):
    """--check must be told the source INDEPENDENTLY, or it cannot see a drop.

    A bare `--check` inherits `registry` / `ref` / `bundles` / `sources` from
    the very lock it is checking, so after a re-pin that dropped or rewrote a
    source it rebuilds from the damaged lock, compares it with itself, and goes
    green. Passing the ORIGINAL --source spec on the command line is what makes
    this a guard rather than a smoke test: the expectation is reconstructed
    from outside the file under test.
    """
    primary, _primary_sha, extra, extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    _move_head(primary)
    assert run_generator("--repo", str(primary), "--repin", "-o", str(out)).returncode == 0

    repinned_ref = json.loads(out.read_text(encoding="utf-8"))["ref"]
    proc = run_generator(
        "--repo", str(primary), "--check",
        "--registry", primary.resolve().as_uri(), "--ref", repinned_ref,
        "--bundles", "adam",
        "--source", f"cms-platform={extra.resolve().as_uri()}@{extra_sha}:skills",
        "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("identity_flag,value", [
    ("--registry", "owner/elsewhere"),
    ("--bundles", "extras"),
    ("--source", "other=owner/other@" + "0" * 40),
])
def test_repin_refuses_a_flag_that_would_override_the_inherited_identity(
        registry, tmp_path, identity_flag, value):
    """The ADR trap, reached through the flag written to close it.

    `--source` did not add to the inherited array, it REPLACED it — so an
    operator advancing the pin while bumping one federated source lost every
    other registry, silently, at exit 0, with --check green afterwards. The
    same precedence applied to --registry and --bundles. Advancing a pin and
    deciding what a lock means are two decisions, so they are two commands, and
    argparse says so rather than a precedence rule resolving it quietly.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    before = out.read_text(encoding="utf-8")
    _move_head(root)

    proc = run_generator("--repo", str(root), "--repin", identity_flag, value,
                         "-o", str(out))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "usage:" in proc.stderr
    assert identity_flag in proc.stderr
    assert out.read_text(encoding="utf-8") == before


def test_repin_still_accepts_source_repo_which_is_not_the_locks_identity(federated, tmp_path):
    """Where a source's checkout lives is a property of the MACHINE, not the lock.

    Refusing it alongside --repin would leave a federated lock un-re-pinnable
    on any machine whose sibling clones are not at the default `../<repo>`,
    which is the case --source-repo exists for.
    """
    primary, _primary_sha, extra, _extra_sha = federated
    relocated = tmp_path / "elsewhere" / "cms-platform"
    relocated.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(extra), str(relocated))

    out = tmp_path / "skills.lock"
    assert run_generator(
        "--repo", str(primary), "--registry", primary.resolve().as_uri(),
        "--ref", _head(primary), "--bundles", "adam",
        "--source", f"cms-platform={relocated.resolve().as_uri()}@{_head(relocated)}:skills",
        "--source-repo", f"cms-platform={relocated}", "-o", str(out)).returncode == 0

    _move_head(primary)
    proc = run_generator("--repo", str(primary), "--repin",
                         "--source-repo", f"cms-platform={relocated}", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "cms-platform/deploy" in json.loads(out.read_text(encoding="utf-8"))["skills"]


# --------------------------------------------------------------------------
# --repin-source: the ONE way a federated pin advances
#
# Inheritance stays the default and stays the only thing that carries a
# source's ref forward — `test_repin_on_a_federated_lock_preserves_every_source_verbatim`
# is the standing proof and is deliberately untouched by any of this. What was
# missing was any way to advance ONE named source without reaching for
# `--source`, which REPLACES the inherited array and de-federates the lock. So
# this flag merges by registry key and can express nothing else.
# --------------------------------------------------------------------------

def _repin_source_lock(federated_two, out: Path) -> dict:
    assert _federated_two_lock(out, federated_two).returncode == 0
    return json.loads(out.read_text(encoding="utf-8"))


def _source_named(lock: dict, registry: str) -> dict:
    return next(source for source in lock["sources"] if source["registry"] == registry)


def test_repin_source_advances_only_the_named_source(federated_two, tmp_path):
    """The merge-not-replace property, and the whole reason for the flag.

    BOTH siblings move, so an implementation that re-resolved every source to
    HEAD — or that replaced the array with the one source named — produces a
    different answer from one that merged a single key. The un-named source is
    compared as a whole dict rather than on `ref` alone: bundles and layout are
    the lock's identity and a re-pin must not restate them either.
    """
    primary, _, extra, _extra_sha, other, _other_sha = federated_two
    out = tmp_path / "skills.lock"
    before = _repin_source_lock(federated_two, out)

    _move_head(extra)
    advanced = _move_head(other)

    proc = run_generator("--repo", str(primary), "--repin",
                         "--repin-source", f"{other.resolve().as_uri()}@", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    after = json.loads(out.read_text(encoding="utf-8"))
    assert [source["registry"] for source in after["sources"]] == \
           [source["registry"] for source in before["sources"]]
    assert _source_named(after, other.resolve().as_uri())["ref"] == advanced
    assert _source_named(after, extra.resolve().as_uri()) == \
           _source_named(before, extra.resolve().as_uri())


def test_repin_source_with_an_empty_ref_advances_to_that_sources_head(
        federated, tmp_path):
    primary, _, extra, extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    advanced = _move_head(extra)
    proc = run_generator("--repo", str(primary), "--repin",
                         "--repin-source", f"{extra.resolve().as_uri()}@", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    after = json.loads(out.read_text(encoding="utf-8"))
    assert after["sources"][0]["ref"] == advanced != extra_sha
    # A resolved sha, never the literal HEAD: a source has no `generated_from`
    # to record a resolution in, so an unresolved ref there is the one unpinned
    # half of a lock whose entire purpose is pinning.
    assert re.fullmatch(r"[0-9a-f]{40}", after["sources"][0]["ref"])


def test_repin_source_with_an_explicit_ref_pins_exactly_that(federated, tmp_path):
    primary, _, extra, extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    middle = _move_head(extra, "middle")
    _move_head(extra, "newest")

    proc = run_generator("--repo", str(primary), "--repin",
                         "--repin-source", f"{extra.resolve().as_uri()}@{middle}",
                         "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["sources"][0]["ref"] == middle


def test_repin_source_can_pin_a_source_backward(federated, tmp_path):
    """Nothing here enforces that a new pin is NEWER, deliberately.

    Putting a federated source back on a known-good commit after a bad bump is
    the same legitimate operation `--repin --ref` already is for the primary.
    """
    primary, _, extra, extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    _move_head(extra)
    forward = run_generator("--repo", str(primary), "--repin",
                            "--repin-source", f"{extra.resolve().as_uri()}@",
                            "-o", str(out))
    assert forward.returncode == 0, forward.stdout + forward.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["sources"][0]["ref"] != extra_sha

    back = run_generator("--repo", str(primary), "--repin",
                         "--repin-source", f"{extra.resolve().as_uri()}@{extra_sha}",
                         "-o", str(out))
    assert back.returncode == 0, back.stdout + back.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["sources"][0]["ref"] == extra_sha


def test_repin_source_redigests_the_advanced_sources_skills(federated, tmp_path):
    """A pin that moved without its digests moving is an attestation over bytes
    nobody recomputed — the exact thing the lock exists to make impossible."""
    primary, _, extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    before = json.loads(out.read_text(encoding="utf-8"))

    _write(extra / "skills" / "deploy" / "SKILL.md", "---\nname: deploy\n---\ndeploy v2\n")
    _git(extra, "add", "-A")
    _git(extra, "commit", "-q", "-m", "deploy v2")

    proc = run_generator("--repo", str(primary), "--repin",
                         "--repin-source", f"{extra.resolve().as_uri()}@", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    after = json.loads(out.read_text(encoding="utf-8"))
    assert after["skills"]["cms-platform/deploy"] != before["skills"]["cms-platform/deploy"]
    assert after["skills"]["cms-platform/deploy"] == gsl.LOCK_DIGEST_PREFIX + \
        gsl.digest_skill_dir(extra / "skills" / "deploy")
    assert after["skills"]["adam/alpha"] == before["skills"]["adam/alpha"]


def test_repin_source_leaves_the_primary_ref_alone_when_only_a_source_is_named(
        federated, tmp_path):
    """Advancing a source is not a primary content advance.

    The fleet bumper's federated-only night is exactly this invocation — the
    primary held at the ref it already pins, one source moved — so the two
    halves have to be independently settable in one run.
    """
    primary, primary_sha, extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    _move_head(primary)
    advanced = _move_head(extra)

    proc = run_generator("--repo", str(primary), "--repin", "--ref", primary_sha,
                         "--repin-source", f"{extra.resolve().as_uri()}@", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    after = json.loads(out.read_text(encoding="utf-8"))
    assert after["ref"] == primary_sha
    assert after["sources"][0]["ref"] == advanced


def test_repin_source_refuses_a_registry_the_lock_does_not_federate(
        federated, tmp_path):
    """ADDING a source changes what the lock means; that is a plain generate."""
    primary, _, extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    before = out.read_text(encoding="utf-8")

    proc = run_generator("--repo", str(primary), "--repin",
                         "--repin-source", "owner/never-heard-of-it@", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "not a source this lock federates" in proc.stderr
    assert extra.resolve().as_uri() in proc.stderr
    assert out.read_text(encoding="utf-8") == before


def test_repin_source_refuses_the_primary_registry(federated, tmp_path):
    primary, _, _extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    before = out.read_text(encoding="utf-8")

    proc = run_generator("--repo", str(primary), "--repin",
                         "--repin-source", f"{primary.resolve().as_uri()}@", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "PRIMARY registry" in proc.stderr
    assert out.read_text(encoding="utf-8") == before


def test_repin_source_refuses_the_same_registry_twice(federated, tmp_path):
    """Two pins for one source is not a last-one-wins precedence question."""
    primary, _, extra, extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    before = out.read_text(encoding="utf-8")
    newest = _move_head(extra)

    proc = run_generator("--repo", str(primary), "--repin",
                         "--repin-source", f"{extra.resolve().as_uri()}@{extra_sha}",
                         "--repin-source", f"{extra.resolve().as_uri()}@{newest}",
                         "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "twice" in proc.stderr
    assert out.read_text(encoding="utf-8") == before


def test_repin_source_without_repin_is_an_argparse_error(federated, tmp_path):
    primary, _, extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    before = out.read_text(encoding="utf-8")

    proc = run_generator("--repo", str(primary),
                         "--repin-source", f"{extra.resolve().as_uri()}@", "-o", str(out))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "usage:" in proc.stderr
    assert "--repin-source" in proc.stderr
    assert out.read_text(encoding="utf-8") == before


def test_repin_source_on_a_plain_generate_is_refused_before_anything_is_written(
        federated, tmp_path):
    """The counterexample the argparse guard exists for.

    A plain generate with `--source` populates `extras` from the command line,
    so a merge step guarded on `--repin-source` alone would rewrite a source's
    ref on a WRITE path that never inherited anything — at exit 0, under an
    ordinary `Wrote ...` line.
    """
    primary, primary_sha, extra, extra_sha = federated
    out = tmp_path / "never-written.lock"

    proc = run_generator(
        "--repo", str(primary), "--registry", primary.resolve().as_uri(),
        "--ref", primary_sha, "--bundles", "adam",
        "--source", f"cms-platform={extra.resolve().as_uri()}@{extra_sha}:skills",
        "--repin-source", f"{extra.resolve().as_uri()}@", "-o", str(out))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "usage:" in proc.stderr
    assert not out.exists()


def test_repin_source_is_not_in_the_refused_identity_flags(federated, tmp_path):
    """The flag written to close the de-federation trap must not be folded into it.

    `--registry` / `--bundles` / `--source` are refused alongside `--repin`
    because each REPLACES part of the inherited identity. `--repin-source`
    merges by key and replaces nothing, so tidying it into that list would make
    the only way to advance a federated pin an error alongside the only flag it
    means anything with.
    """
    primary, _, extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    _move_head(extra)

    proc = run_generator("--repo", str(primary), "--repin",
                         "--repin-source", f"{extra.resolve().as_uri()}@", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--repin inherits the lock's identity" not in proc.stderr


def test_repin_source_honours_source_repo_for_an_out_of_tree_clone(
        federated, tmp_path):
    """Where a source's checkout lives is a property of the MACHINE.

    Resolving an empty ref needs that lookup, which is why the merge runs after
    `--source-repo` has been parsed rather than beside the inherited array.
    """
    primary, primary_sha, extra, _extra_sha = federated
    relocated = tmp_path / "elsewhere" / "cms-platform"
    relocated.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(extra), str(relocated))

    out = tmp_path / "skills.lock"
    assert run_generator(
        "--repo", str(primary), "--registry", primary.resolve().as_uri(),
        "--ref", primary_sha, "--bundles", "adam",
        "--source", f"cms-platform={relocated.resolve().as_uri()}@{_head(relocated)}:skills",
        "--source-repo", f"cms-platform={relocated}", "-o", str(out)).returncode == 0

    advanced = _move_head(relocated)
    proc = run_generator("--repo", str(primary), "--repin",
                         "--repin-source", f"{relocated.resolve().as_uri()}@",
                         "--source-repo", f"cms-platform={relocated}", "-o", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["sources"][0]["ref"] == advanced


def _lock_federating_one_registry_twice(tmp_path) -> tuple:
    """A lock whose `sources` names ONE registry twice, with independent pins.

    Representable and `--check`-green: plan_sources' uniqueness check is keyed
    on BUNDLE, and these two entries claim different bundles, read different
    layouts and pin different commits. Two layouts are what keep the skill
    BASENAMES distinct as well, which `_reject_basename_collisions` requires.
    """
    primary = tmp_path / "registry"
    primary_sha = make_registry(primary, {"adam/alpha": SKILL_A})
    extra = tmp_path / "cms-platform"
    first = make_registry(extra, {"cms-platform/deploy": SKILL_B}, layout="skills")
    _write(extra / "other" / "publish" / "SKILL.md", SKILL_C["SKILL.md"])
    _git(extra, "add", "-A")
    _git(extra, "commit", "-q", "-m", "a second bundle, one commit later")
    second = _head(extra)
    assert first != second

    out = tmp_path / "skills.lock"
    uri = extra.resolve().as_uri()
    assert run_generator(
        "--repo", str(primary), "--registry", primary.resolve().as_uri(),
        "--ref", primary_sha, "--bundles", "adam",
        "--source", f"cms-platform={uri}@{first}:skills",
        "--source", f"other={uri}@{second}:{{bundle}}",
        "-o", str(out)).returncode == 0
    lock = json.loads(out.read_text(encoding="utf-8"))
    assert [source["ref"] for source in lock["sources"]] == [first, second]
    return primary, extra, uri, out


def test_repin_source_refuses_a_registry_the_lock_federates_twice(tmp_path):
    """One spec, one source — or no re-pin at all.

    Merging by registry key over a lock that federates that registry TWICE
    advances both entries, so a caller naming one moves a pin nobody asked
    about, at exit 0, with its digests rewritten to the new content. The module
    docstring says this flag advances ONE source; this refusal is what makes
    that sentence true rather than usually true. It is the same answer
    `_select_sources` already gives to the analogous ambiguity on the read-only
    path ("scoping to it has two answers, so it gets none").
    """
    primary, extra, uri, out = _lock_federating_one_registry_twice(tmp_path)
    before = out.read_text(encoding="utf-8")
    _move_head(extra)

    proc = run_generator("--repo", str(primary), "--repin",
                         "--repin-source", f"{uri}@", "-o", str(out))
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "twice" in proc.stderr, proc.stderr
    # Both bundles named, so the reader can see WHICH two entries collide.
    assert "cms-platform" in proc.stderr and "other" in proc.stderr, proc.stderr
    assert out.read_text(encoding="utf-8") == before


def test_repin_source_refuses_a_checkout_that_is_not_the_source_it_names(
        federated, tmp_path):
    """The identity probe the primary's --repin has, applied per source.

    A sibling directory of the right NAME is not proof of the right
    repository: a fork, or a same-named repo under another owner, sits at
    `../cms-platform` just as happily. Every source this flag does not name is
    still probed by accident downstream — plan_sources resolves its inherited
    pin in that clone and fails there — but the named source's pin is REPLACED
    before plan_sources sees it, so the wrong clone's HEAD is written under the
    right registry's name at exit 0. The commit the lock already pins is the
    probe, exactly as it is for the primary.
    """
    primary, _, extra, extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    before = out.read_text(encoding="utf-8")

    # A DIFFERENT repository at the same sibling path, so the default
    # `../<repo-name>` lookup finds it and the lock's registry string still
    # matches. Its HEAD resolves; the commit the lock pins does not exist.
    shutil.rmtree(extra)
    decoy_sha = make_registry(extra, {"cms-platform/deploy": SKILL_C}, layout="skills")
    assert decoy_sha != extra_sha

    proc = run_generator("--repo", str(primary), "--repin",
                         "--repin-source", f"{extra.resolve().as_uri()}@", "-o", str(out))
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert extra_sha in proc.stderr, proc.stderr
    assert "is not that registry" in proc.stderr, proc.stderr
    assert out.read_text(encoding="utf-8") == before
    assert decoy_sha not in out.read_text(encoding="utf-8")


def test_repin_source_with_a_missing_checkout_leaves_the_lock_untouched(
        federated, tmp_path):
    """A re-pin that cannot read the new content must write nothing at all.

    A half-applied merge would record a pin whose digests were never
    recomputed, which is the one failure mode the lock is a lock to prevent.
    """
    primary, _, extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    before = out.read_text(encoding="utf-8")

    shutil.move(str(extra), str(tmp_path / "moved-away"))

    proc = run_generator("--repo", str(primary), "--repin",
                         "--repin-source", f"{extra.resolve().as_uri()}@", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "no checkout at" in proc.stderr
    assert out.read_text(encoding="utf-8") == before


def test_repin_refuses_a_lock_whose_bundle_name_escapes_the_pinned_tree(registry, tmp_path):
    """The inherited bundle list reaches a filesystem path, and now a WRITE.

    `bundles` is substituted into `layout_dir` and into every skills key, and
    was only ever type-checked as a list. `../../../outside` globs its way out
    of the scratch `git archive` extraction entirely, so the lock records a
    digest for content that is in no commit of any registry — an attestation
    over bytes nobody published — and the hook then refuses the WHOLE lock over
    the traversal key, installing nothing. Before --repin that value could only
    be reached by --check, which never writes.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0

    outside = tmp_path / "outside" / "skills" / "pwned"
    _write(outside / "SKILL.md", "---\nname: pwned\n---\nnot in any commit\n")
    _edit_lock(out, bundles=["../../../outside"])
    before = out.read_text(encoding="utf-8")
    _move_head(root)

    proc = run_generator("--repo", str(root), "--repin", "-o", str(out))
    assert _rejected(proc), proc.stdout + proc.stderr
    assert "bundles" in proc.stderr
    assert out.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("bad", [123, None, True, {"a": 1}, ["nested"]])
def test_repin_reports_a_type_confused_bundle_rather_than_tracebacking(registry, tmp_path, bad):
    """GeneratorError is this file's convention: a message, never a traceback.

    Unvalidated elements reached `str.replace` (a TypeError inside
    `layout_dir`) or a dict lookup (`unhashable type`) instead.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _edit_lock(out, bundles=[bad])

    proc = run_generator("--repo", str(root), "--repin", "-o", str(out))
    assert _rejected(proc), proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("registry_value", [_DROP, "", None])
def test_repin_refuses_a_lock_with_no_usable_registry(registry, tmp_path, registry_value):
    """Defaulting here re-points a consumer at a DIFFERENT repository, at exit 0.

    A plain generate falls back to DEFAULT_REGISTRY because nothing was
    inherited. Under --repin the lock is the authority, so a missing field is a
    refusal — otherwise the lock silently starts naming this repo, `--check`
    goes green against the substitution, and the consumer fetches its
    instruction text from somewhere it never declared.
    """
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "--registry", "owner/consumer",
                         "--ref", _head(root), "--bundles", "adam",
                         "-o", str(out)).returncode == 0
    _edit_lock(out, registry=registry_value)
    before = out.read_text(encoding="utf-8")

    proc = run_generator("--repo", str(root), "--repin", "-o", str(out))
    assert _rejected(proc), proc.stdout + proc.stderr
    assert "registry" in proc.stderr
    assert out.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("bundles_value", [_DROP, [], "adam,extras"])
def test_repin_refuses_a_lock_with_no_usable_bundles(tmp_path, bundles_value):
    """Defaulting here silently stops delivering a whole bundle.

    `[]` and a comma STRING are both falsy-or-wrong-typed, so the generate
    path's fall-through would narrow a two-bundle lock to DEFAULT_BUNDLES and
    drop the other bundle's skills — and the operator usually arrives at
    --repin because the hook told them the lock was unreadable, which is
    exactly when nobody is looking for a narrowing.
    """
    root = tmp_path / "registry"
    make_registry(root, {"adam/alpha": SKILL_A, "extras/zeta": SKILL_B})
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "--registry", "owner/consumer",
                         "--ref", _head(root), "--bundles", "adam,extras",
                         "-o", str(out)).returncode == 0
    _edit_lock(out, bundles=bundles_value)
    before = out.read_text(encoding="utf-8")

    proc = run_generator("--repo", str(root), "--repin", "-o", str(out))
    assert _rejected(proc), proc.stdout + proc.stderr
    assert "bundles" in proc.stderr
    assert out.read_text(encoding="utf-8") == before


def test_repin_refuses_a_sources_value_that_is_not_a_list(federated, tmp_path):
    """A wrong-typed `sources` is a DELETION on the write path, not a fallback.

    `--check` reports it (the rebuilt document has no sources, so the
    comparison goes red and names the field) and the hook calls it fatal. The
    silent fallthrough only became destructive when --repin put it on a write
    path: the federated half would be written away under a normal `Wrote ...`
    line at exit 0, with --check green against the de-federated result.
    """
    primary, _primary_sha, _extra, _extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    _edit_lock(out, sources={"0": document["sources"][0]})
    before = out.read_text(encoding="utf-8")
    _move_head(primary)

    proc = run_generator("--repo", str(primary), "--repin", "-o", str(out))
    assert _rejected(proc), proc.stdout + proc.stderr
    assert "sources" in proc.stderr
    assert out.read_text(encoding="utf-8") == before


def test_repin_refuses_a_repo_that_is_not_the_registry_the_lock_names(tmp_path):
    """The lock names a registry; --repo names a clone. Nothing else ties them.

    Re-pinning a consumer's lock from the wrong checkout wrote one repo's
    commit under the other's name: exit 0, --check green (it re-derives from
    the same wrong clone), and the failure surfaced only at a consumer's
    session start, where the hook cannot fetch that sha from that registry and
    reports DEGRADED. The pin the lock already carries is the probe — a clone
    that IS this registry has that commit.
    """
    registry_root = tmp_path / "registry"
    make_registry(registry_root, {"adam/alpha": SKILL_A})
    other = tmp_path / "other-registry"
    make_registry(other, {"adam/beta": SKILL_B})

    out = tmp_path / "consumer.lock"
    assert run_generator("--repo", str(registry_root),
                         "--registry", registry_root.resolve().as_uri(),
                         "--ref", _head(registry_root), "--bundles", "adam",
                         "-o", str(out)).returncode == 0
    before = out.read_text(encoding="utf-8")

    proc = run_generator("--repo", str(other), "--repin", "-o", str(out))
    assert _rejected(proc), proc.stdout + proc.stderr
    assert str(other) in proc.stderr
    assert out.read_text(encoding="utf-8") == before


def test_repin_refuses_the_consumer_checkout_rather_than_emptying_its_lock(tmp_path):
    """The same guard, in the shape the flag's own use case produces.

    A consumer repo holds the lock but ships none of the bundles, so re-pinning
    with `--repo <consumer>` used to write the CONSUMER's HEAD under the
    REGISTRY's name, digest zero skills, print `Wrote ...: 0 skills` and exit
    0 — destroying a populated lock. Getting --repo wrong is the one mistake
    this workflow invites, since the two repositories differ by construction.
    """
    registry_root = tmp_path / "registry"
    make_registry(registry_root, {"adam/alpha": SKILL_A})
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _write(consumer / "README.md", "a consumer that ships no bundles\n")
    _git(consumer, "init", "-q", "-b", "main")
    _git(consumer, "config", "user.email", "test@example.com")
    _git(consumer, "config", "user.name", "Test")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-q", "-m", "consumer")

    out = consumer / "skills.lock"
    assert run_generator("--repo", str(registry_root), "--registry", "owner/registry",
                         "--ref", _head(registry_root), "--bundles", "adam",
                         "-o", str(out)).returncode == 0
    before = out.read_text(encoding="utf-8")

    proc = run_generator("--repo", str(consumer), "--repin", "-o", str(out))
    assert _rejected(proc), proc.stdout + proc.stderr
    assert out.read_text(encoding="utf-8") == before
    assert json.loads(before)["skills"]    # the lock it refused to empty was populated


def test_repin_refuses_a_lock_with_no_ref_to_advance(registry, tmp_path):
    root, _ = registry
    out = tmp_path / "skills.lock"
    assert run_generator("--repo", str(root), "-o", str(out)).returncode == 0
    _edit_lock(out, ref=_DROP)
    before = out.read_text(encoding="utf-8")

    proc = run_generator("--repo", str(root), "--repin", "-o", str(out))
    assert _rejected(proc), proc.stdout + proc.stderr
    assert "ref" in proc.stderr
    assert out.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("mode", ["--repin", "--check"])
def test_a_directory_at_the_output_path_errors_cleanly(registry, tmp_path, mode):
    """`-o <a directory>` is 'the lock is not usable', not an IsADirectoryError.

    A plain generate already reported this cleanly, because its write catches
    OSError; every mode that READS the lock tracebacked instead.
    """
    root, _ = registry
    a_directory = tmp_path / "not-a-lock"
    a_directory.mkdir()

    proc = run_generator("--repo", str(root), mode, "-o", str(a_directory))
    assert _rejected(proc), proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("mode", ["--repin", "--check"])
def test_a_lock_that_is_valid_json_but_not_an_object_errors_cleanly(registry, tmp_path, mode):
    root, _ = registry
    out = tmp_path / "skills.lock"
    out.write_text("[]\n", encoding="utf-8")

    proc = run_generator("--repo", str(root), mode, "-o", str(out))
    assert _rejected(proc), proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr


# --------------------------------------------------------------------------
# the bootstrap hook (bash, driven through subprocess)
# --------------------------------------------------------------------------

def _windows_dir() -> Path:
    """The Windows directory, as the place a WSL `bash.exe` is found under."""
    return Path(os.environ.get("SystemRoot", r"C:\Windows"))


def _is_wsl_launcher(candidate: Path) -> bool:
    """True for `C:\\Windows\\System32\\bash.exe` and friends.

    Anything named `bash` living under the Windows directory is the WSL
    launcher, not a POSIX shell. Judged by location rather than by running it,
    so this stays a pure predicate and does not depend on WSL's state.
    """
    try:
        candidate.resolve().relative_to(_windows_dir().resolve())
    except (ValueError, OSError):
        return False
    return True


def _find_posix_bash() -> Optional[str]:
    """Absolute path to a POSIX bash, or None if this machine has none.

    On Windows `subprocess` hands the bare name to `CreateProcess`, which
    searches the application directory, the current directory and then
    **System32** before it ever consults PATH — and `System32\\bash.exe` is the
    WSL launcher. It cannot execute a hook addressed by a `D:\\...` path, so
    every `["bash", script]` here failed having tested nothing.

    `shutil.which("bash")` is not the fix and is actively misleading: it
    searches PATH only, so it reports Git Bash on exactly the machines where
    `CreateProcess` reaches WSL. Resolving to an absolute path before invoking
    is what closes the gap; passing the bare name cannot.

    POSIX has nothing to disambiguate, so the bare name is kept there and this
    whole path is a no-op off Windows.
    """
    if os.name != "nt":
        return "bash"

    candidates = []
    found = shutil.which("bash")
    if found:
        candidates.append(Path(found))

    # Git for Windows ships the real bash at <git>/usr/bin/bash.exe. Derive the
    # install root from git itself (covers portable/scoop installs that are on
    # PATH but in no standard location), then fall back to the usual roots.
    git = shutil.which("git")
    if git:
        # .../cmd/git.exe, .../bin/git.exe and .../mingw64/bin/git.exe all sit
        # one or two levels below the install root.
        for up in (2, 3):
            root = Path(git).resolve().parents[up - 1]
            candidates.append(root / "usr" / "bin" / "bash.exe")
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)",
                "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidates.append(Path(base) / "Git" / "usr" / "bin" / "bash.exe")
            candidates.append(
                Path(base) / "Programs" / "Git" / "usr" / "bin" / "bash.exe")

    for candidate in candidates:
        if _is_wsl_launcher(candidate):
            continue
        if candidate.is_file():
            return str(candidate)
    return None


BASH = _find_posix_bash()


def _find_cygpath() -> Optional[str]:
    """Git Bash's path translator, which ships beside bash itself."""
    if os.name != "nt" or BASH is None:
        return None
    candidate = Path(BASH).with_name("cygpath.exe")
    return str(candidate) if candidate.is_file() else None


CYGPATH = _find_cygpath()


def _symlink_to_dir(link: Path, target: Path) -> None:
    """Point `link` at directory `target`, however this platform can.

    `os.symlink` needs Developer Mode or elevation on Windows (WinError 1314),
    which is not a reasonable thing for a test run to require. A directory
    JUNCTION needs neither, and is the same thing for everything asserted
    here: `Path.resolve()` follows it to the target, which is precisely the
    de-duplication being pinned.
    """
    if os.name != "nt":
        link.symlink_to(target, target_is_directory=True)
        return
    proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.skip("cannot create a directory junction here: "
                    f"{(proc.stderr or proc.stdout).strip()}")


def _hook_path(path: Path) -> str:
    """`path` spelled the way the hook's own shell spells it.

    The hook runs under Git Bash on Windows, whose MSYS layer has its own mount
    table: the Windows temp directory is `/tmp`, `C:\\` is `/c`. Every path the
    hook prints back is in that spelling, so an expectation built from
    `str(WindowsPath(...))` is comparing against something that was never going
    to look like one — the assertion failed on the rendering, not on the
    behaviour it was written to pin.

    Asking `cygpath` is asking the very shell the hook runs under, so this
    tracks the real mount table instead of hard-coding a guess at it. Off
    Windows there is nothing to translate.
    """
    if CYGPATH is None:
        return str(path)
    proc = subprocess.run([CYGPATH, "-u", str(path)],
                          capture_output=True, text=True, check=True)
    return proc.stdout.strip()


# Windows needs a baseline environment that POSIX does not, and the hook's
# deliberately scrubbed env was leaving it out. Strip LOCALAPPDATA and the
# `python3` shim that Python 3.14 installs can no longer see its own installed
# runtimes: it prints "Extracting: ..." onto STDOUT — corrupting the framed
# JSON the hook reads back, which surfaced as a bogus "framing error" verdict —
# and downloads a fresh interpreter into the current directory, which is how a
# suite that documents itself as hermetic ended up fetching 30MB over the
# network and writing it into the repo.
#
# None of these name the developer's home, which is what the tmp HOME exists to
# protect: `$HOME/.claude/skills` is expanded by bash, and every python3 the
# hook runs is `-I` with its paths handed over explicitly in the environment.
# USERPROFILE is deliberately NOT among them — it is the one that would let a
# stray `expanduser("~")` escape the tmp home on Windows.
_WINDOWS_BASE_ENV = (
    "SystemRoot", "SystemDrive", "windir", "COMSPEC", "PATHEXT",
    "LOCALAPPDATA", "APPDATA", "ProgramData", "ProgramFiles",
    "ProgramFiles(x86)", "ProgramW6432", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)


def _run_hook(home: Path, project_dir: Path = None, extra_env: dict = None,
              script: Path = HOOK, cwd: Path = None,
              timeout: float = None) -> subprocess.CompletedProcess:
    """Run the hook with HOME forced into a tmp dir.

    The tmp HOME is mandatory and constructed here rather than by the caller's
    environment, so no test can write into the developer's real ~/.claude.

    `cwd` is the session's working directory, which Claude Code sets to the
    project dir. It matters because python puts the process cwd on `sys.path`
    unless it is run isolated — see the `-I` tests below. `timeout` bounds the
    run for the tests that assert the hook cannot HANG; everywhere else it is
    None, so a hang there surfaces as the suite hanging rather than as a
    misattributed failure.
    """
    if BASH is None:
        pytest.skip("no POSIX bash on this machine (Windows System32 bash.exe "
                    "is the WSL launcher, which cannot run the hook)")
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(home / "tmp"),
        # Fixture repos are committed by `git commit` in make_registry; the
        # hook itself only reads, but keep git non-interactive regardless.
        "GIT_TERMINAL_PROMPT": "0",
    }
    for name in _WINDOWS_BASE_ENV if os.name == "nt" else ():
        if name in os.environ:
            env[name] = os.environ[name]
    if os.name == "nt":
        # Windows' own spellings of TMPDIR, kept pointing at the tmp home so
        # nothing spills into the real temp directory.
        env["TEMP"] = env["TMP"] = str(home / "tmp")
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    env.update(extra_env or {})
    return subprocess.run(
        [BASH, str(script)],
        input='{"hook_event_name":"SessionStart","source":"startup"}',
        env=env, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
        timeout=timeout,
    )


# Everything the hook shells out to, or is launched through, across the paths
# these tests drive. A farm built from this list minus one tool is how a
# platform MISSING that tool is reproduced.
_PATH_FARM_TOOLS = ("bash", "sh", "env", "git", "python3", "cat", "cp", "rm",
                    "mkdir", "mktemp", "dirname", "basename", "sleep")


def _path_farm(tmp_path: Path, omit: str) -> str:
    """A PATH holding everything the hook needs EXCEPT `omit`.

    There is no way to hide one binary from a PATH, and the hook decides by
    probing `command -v <tool>` — so the platform without it is reproduced by
    building a PATH that has only the rest. Skips rather than fails if this
    machine is missing one of them: the point is the tool-less branch, not the
    harness's own coreutils.

    `omit` need not be in the list (`timeout` is not — nothing else here uses
    it); the final assertion is what actually establishes the tool is hidden.
    """
    if os.name == "nt":
        # A PATH farm cannot be built on Windows AT ALL, and the reason is not
        # the linking — it is that the isolation the farm depends on is what
        # breaks the tools. These are MSYS binaries: each one loads
        # msys-2.0.dll, which Windows finds via the application directory and
        # then PATH. A farm holds neither, so every tool in it dies at load
        # time with 0xC0000135 (STATUS_DLL_NOT_FOUND) before it runs a single
        # instruction — measured. The hook then reports "could not create a
        # temp directory" because `mktemp` never started, which is a different
        # branch from the tool-less one under test, and the failure names the
        # wrong cause.
        #
        # Copying msys-2.0.dll in beside them would fix the load and defeat
        # the point: the farm's whole job is to be a directory containing
        # nothing but the chosen tools.
        #
        # This skips whether or not os.symlink is permitted here — it is
        # refused without Developer Mode (WinError 1314) on a workstation and
        # allowed on a GitHub runner, and the farm is useless either way. So
        # the tool-less branch goes unexercised on Windows, and says so; a
        # farm quietly holding the real PATH would have claimed it passed.
        pytest.skip("a PATH farm cannot be built on Windows: an MSYS tool "
                    "isolated from msys-2.0.dll fails to load (0xC0000135), "
                    "so hiding one tool hides them all")
    farm = tmp_path / f"bin-no-{omit}"
    farm.mkdir(exist_ok=True)
    for tool in _PATH_FARM_TOOLS:
        if tool == omit:
            continue
        found = shutil.which(tool)
        if found is None:
            pytest.skip(f"{tool} is not on PATH, so a PATH farm cannot be built")
        link = farm / tool
        if not link.exists():
            link.symlink_to(found)
    assert shutil.which(omit, path=str(farm)) is None
    return str(farm)


def _hook_with(tmp_path: Path, old: str, new: str, name: str) -> Path:
    """A scratch copy of the hook with one exact substring replaced.

    FAULT INJECTION, and used only where the condition under test cannot be
    produced from outside the hook: a python<->bash record count that disagrees
    (no lock can forge one — that is the point of the framing), or a wall-clock
    budget shortened so a liveness property can be asserted in seconds instead
    of a minute. The behaviour being asserted is still the real hook's.

    The anchor must appear exactly once, so renaming it in the hook fails these
    tests loudly instead of silently patching nothing and asserting on the
    unmodified file.
    """
    text = HOOK.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"anchor is not unique in the hook: {old!r}"
    script = tmp_path / name / ".claude" / "hooks" / "skills-bootstrap.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(text.replace(old, new), encoding="utf-8")
    return script


def _verdict(proc: subprocess.CompletedProcess) -> str:
    payload = json.loads(proc.stdout)
    return payload["hookSpecificOutput"]["additionalContext"]


def _looked_in(verdict: str) -> list:
    """The lock locations a 'no skills.lock found' verdict says it searched.

    Parsed out of the rendered sentence rather than substring-counted, so a
    duplicate is caught as a repeated list ENTRY -- counting occurrences would
    miscount whenever one candidate path is a prefix of the other.
    """
    prefix, suffix = "looked in ", " (generate it"
    start = verdict.index(prefix) + len(prefix)
    return verdict[start:verdict.index(suffix, start)].split(", ")


def _hook_copy(root: Path) -> Path:
    """Install a copy of the hook so that its SELF_ROOT resolves to `root`."""
    script = root / ".claude" / "hooks" / "skills-bootstrap.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(HOOK.read_bytes())
    return script


def _tree(root: Path) -> dict:
    """Every file under `root`, relative path -> bytes.

    Shape AND content, so "unchanged" means unchanged rather than the weaker
    "still has a SKILL.md somewhere under that name".
    """
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _federated_project(project_dir: Path, federated) -> Path:
    """A project dir whose lock pins BOTH fixture registries."""
    project_dir.mkdir(parents=True, exist_ok=True)
    lock = project_dir / "skills.lock"
    assert _federated_lock(lock, federated).returncode == 0
    return lock


def make_project(project_dir: Path, registry_root: Path, sha: str) -> Path:
    """A project dir carrying a skills.lock that pins the fixture registry."""
    project_dir.mkdir(parents=True, exist_ok=True)
    lock = project_dir / "skills.lock"
    proc = run_generator("--repo", str(registry_root),
                         "--registry", registry_root.resolve().as_uri(),
                         "--ref", sha, "--bundles", "adam", "-o", str(lock))
    assert proc.returncode == 0, proc.stderr
    return lock


def test_hook_is_a_no_op_without_the_ephemeral_marker(tmp_path, registry):
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)

    proc = _run_hook(tmp_path / "home", project)
    assert proc.returncode == 0
    assert _verdict(proc).startswith("skills: skipped")
    assert not (tmp_path / "home" / ".claude" / "skills").exists()


def test_hook_installs_and_verifies_the_locked_skills(tmp_path, registry):
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)  # must be valid JSON
    assert payload["reloadSkills"] is True
    assert payload["hookSpecificOutput"]["reloadSkills"] is True

    verdict = payload["hookSpecificOutput"]["additionalContext"]
    assert verdict.startswith("skills: 2/2 ")
    assert verdict.endswith("OK")
    assert sha[:7] in verdict

    for name in ("alpha", "beta"):
        assert (home / ".claude" / "skills" / name / "SKILL.md").is_file()
    # Copied whole, not just SKILL.md.
    assert (home / ".claude" / "skills" / "alpha" / "notes.md").is_file()

    # RUN IT AGAIN, same HOME — the most ordinary thing this hook does, and the
    # only thing that exercises the `rm -rf "$DEST/$name"` sitting immediately
    # before the install `cp -R`. `cp -R src dest` copies INTO dest when dest
    # already exists, so without that removal run 2 nests the skill inside
    # itself (`alpha/alpha/SKILL.md`), the digest of the nested tree does not
    # match, and the mismatch branch then deletes the skill run 1 had installed
    # correctly: the session ends at `0/2 — DEGRADED` with an EMPTY tree. Fail
    # closed, so not a hole — but a repeat run must simply be a no-op, and
    # nothing else in this suite runs the hook twice.
    installed = _tree(home / ".claude" / "skills")
    assert installed, "run 1 installed nothing, so run 2 asserts nothing"

    again = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert again.returncode == 0, again.stderr
    assert _verdict(again) == verdict, _verdict(again)
    # Byte-identical, so a nested copy or a re-copy that lost a file reddens.
    assert _tree(home / ".claude" / "skills") == installed


def test_hook_skips_a_skill_the_project_already_owns(tmp_path, registry):
    """Personal ~/.claude/skills shadows project .claude/skills (E2 / C3)."""
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)
    _write(project / ".claude" / "skills" / "alpha" / "SKILL.md", "repo-owned alpha\n")
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0
    verdict = _verdict(proc)
    assert verdict.startswith("skills: 1/2 ")
    assert "DEGRADED" in verdict
    assert "alpha" in verdict

    assert not (home / ".claude" / "skills" / "alpha").exists()
    assert (home / ".claude" / "skills" / "beta" / "SKILL.md").is_file()


def test_hook_reports_a_digest_mismatch(tmp_path, registry):
    """A lock digest that does not match the fetched bytes must be named."""
    root, sha = registry
    project = tmp_path / "project"
    lock_path = make_project(project, root, sha)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["skills"]["adam/alpha"] = "0" * 64
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0
    verdict = _verdict(proc)
    assert verdict.startswith("skills: 1/2 ")
    assert "1 digest mismatch (alpha)" in verdict


# --------------------------------------------------------------------------
# both lock digest shapes: "<64 hex>" and "sha256:<64 hex>"
#
# The prefixed shape exists because a committed lock of bare 64-hex values
# trips gitleaks' `generic-api-key` rule on any keyword-bearing skill basename
# (issue #87). The hook's tolerance has to LAND AND BE DELIVERED to every
# consumer before the generator starts emitting it, so these tests pin the
# reader against a shape nothing writes yet -- deliberately, and they are what
# stops the tolerance being deleted as dead code in the interval.
#
# `scripts/generate_skills_lock.py` still emits bare hex, so a prefixed lock is
# built here by rewriting one the generator wrote, never by hand-rolling a
# digest: the fixture's digests stay the ones the generator actually computes.
# --------------------------------------------------------------------------

def _reshape_lock_digests(lock_path: Path, *, prefix: bool) -> None:
    """Rewrite `lock_path` with every digest in the requested shape.

    Normalises whatever is already there BEFORE applying the shape, so a fixture
    states which shape it wants instead of inheriting the generator's. That
    inheritance is exactly what broke these when the generator began emitting
    the prefix: prepending unconditionally produced `sha256:sha256:<hex>`, which
    the reader rejects outright, and all three tests failed as DEGRADED — the
    fixtures were wrong, not the hook. A test that pins one side of a contract
    must not be written in terms of the other side's current behaviour.
    """
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["skills"] = {
        key: ("sha256:" if prefix else "") + digest.split("sha256:")[-1]
        for key, digest in lock["skills"].items()
    }
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


def test_both_digest_shapes_install_and_record_identically(tmp_path, registry):
    """A prefixed lock installs, and is indistinguishable downstream from bare.

    RED before the reader normalised: a `sha256:`-prefixed lock was refused
    wholesale and the session reported DEGRADED with nothing installed.

    Asserted as EQUALITY against the bare-hex run rather than as a second set of
    hand-written expectations, because "the prefix changes the value the reader
    hands to bash" is the whole failure mode -- the integrity check
    (`[ "$got" = "$want" ]`, against `digest_dir`'s always-bare hex) and the
    install record both read that one value. Two runs that agree on the tree AND
    on the record can only agree if the prefix was normalised away.
    """
    root, sha = registry
    bare_project = tmp_path / "bare"
    prefixed_project = tmp_path / "prefixed"
    bare_lock = make_project(bare_project, root, sha)
    _reshape_lock_digests(bare_lock, prefix=False)
    prefixed_lock = make_project(prefixed_project, root, sha)
    _reshape_lock_digests(prefixed_lock, prefix=True)
    # The fixture is only worth anything if the two locks really do differ, and
    # BOTH sides are asserted now that the generator emits one of the two shapes
    # natively -- otherwise "bare" silently becomes a second prefixed run.
    assert all(digest.startswith("sha256:") for digest in
               json.loads(prefixed_lock.read_text(encoding="utf-8"))["skills"].values())
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in
               json.loads(bare_lock.read_text(encoding="utf-8"))["skills"].values())

    bare_home = tmp_path / "bare-home"
    prefixed_home = tmp_path / "prefixed-home"
    bare = _run_hook(bare_home, bare_project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    prefixed = _run_hook(prefixed_home, prefixed_project,
                         {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert bare.returncode == 0, bare.stderr
    assert prefixed.returncode == 0, prefixed.stderr

    verdict = _verdict(prefixed)
    assert verdict.startswith("skills: 2/2 "), verdict
    assert verdict.endswith("OK"), verdict
    assert _verdict(bare) == verdict, _verdict(bare)

    # The skills actually landed -- a verdict is not delivery.
    for name in ("alpha", "beta"):
        assert (prefixed_home / ".claude" / "skills" / name / "SKILL.md").is_file()
    assert (prefixed_home / ".claude" / "skills" / "alpha" / "notes.md").is_file()
    assert _tree(prefixed_home / ".claude" / "skills") == _tree(
        bare_home / ".claude" / "skills")

    # Stated directly as well as by equality: the record is read back next run
    # by two separate `[0-9a-f]{64}` validators (the may_replace reader and the
    # orphan pruner). A record of prefixed values fails both, which is silent --
    # every installed skill reads as unrecognised.
    for entry in _record(prefixed_home)["installed"]:
        assert re.fullmatch(r"[0-9a-f]{64}", entry["digest"]), entry


def test_a_second_run_against_a_prefixed_lock_is_a_clean_no_op(tmp_path, registry):
    """The ordinary repeat run, driven from a prefixed lock.

    The `rm -rf "$DEST/$name"` before the install `cp -R` only ever runs on a
    second pass, and `cp -R src dest` copies INTO an existing dest -- so a
    repeat run is where a prefixed lock would surface a nested re-copy. Nothing
    else in this suite runs the hook twice against one.

    It is NOT the test that covers the install record: see
    `test_a_skill_leaving_a_prefixed_lock_is_still_reaped` below for why a
    repeat run stays green even with an unreadable record.
    """
    root, sha = registry
    project = tmp_path / "project"
    _reshape_lock_digests(make_project(project, root, sha), prefix=True)
    home = tmp_path / "home"

    first = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert first.returncode == 0, first.stderr
    assert _verdict(first).startswith("skills: 2/2 "), _verdict(first)
    installed = _tree(home / ".claude" / "skills")
    record = _record(home)
    assert [entry["name"] for entry in record["installed"]] == ["alpha", "beta"]

    second = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert second.returncode == 0, second.stderr
    assert _verdict(second) == _verdict(first), _verdict(second)
    assert _tree(home / ".claude" / "skills") == installed
    assert _record(home) == record


def test_a_skill_leaving_a_prefixed_lock_is_still_reaped(tmp_path, registry):
    """The record-with-teeth case, which a repeat run does NOT cover.

    A repeat run alone is a weak probe: `may_replace` has a second way to say
    yes -- the bytes on disk still digest to what the lock names -- so a record
    of unreadable values still yields an identical tree and an identical record,
    and the run looks clean. Measured against a hook deliberately mutated to
    write `sha256:`-prefixed values into the record: the no-op test above passed.

    The prune is where the record is the ONLY evidence. A skill that has left
    the lock is removed only if the record proves this hook installed it and
    nobody has edited it since; an entry failing the pruner's `[0-9a-f]{64}` is
    SKIPPED, so a prefixed record leaks every dropped skill forever under a
    verdict that reads OK. That is the silent half of the 910/1248 trap, and
    this is the assertion that fails on it.
    """
    root, sha = registry
    project = tmp_path / "project"
    lock_path = make_project(project, root, sha)
    _reshape_lock_digests(lock_path, prefix=True)
    full = json.loads(lock_path.read_text(encoding="utf-8"))
    home = tmp_path / "home"

    first = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert first.returncode == 0, first.stderr
    assert _verdict(first).startswith("skills: 2/2 "), _verdict(first)
    assert (home / ".claude" / "skills" / "alpha" / "SKILL.md").is_file()

    _relock(lock_path, full, {"beta"})
    second = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert second.returncode == 0, second.stderr
    verdict = _verdict(second)
    assert verdict.startswith("skills: 1/1 "), verdict
    # Named, not merely gone -- the same rule the bare-hex prune test holds to.
    assert "removed 1 skill no longer in the lock (alpha)" in verdict, verdict
    assert not (home / ".claude" / "skills" / "alpha").exists(), verdict
    assert (home / ".claude" / "skills" / "beta" / "SKILL.md").is_file()
    assert [entry["name"] for entry in _record(home)["installed"]] == ["beta"]


_GOOD_DIGESTS = ("ab" * 32, "sha256:" + "ab" * 32)
_BAD_DIGESTS = (
    "sha256:" + "ab" * 31 + "a",        # 63 hex -- one short of a digest
    "sha256:sha256:" + "ab" * 32,       # the prefix is not repeatable
    "SHA256:" + "ab" * 32,              # PINNED: case-sensitive, one spelling
    "not-a-digest",                     # bare, non-hex
    12345,                              # not a string at all
    # The one non-string that LOOKS like one: `str()` of a 64-digit JSON number
    # is 64 characters drawn entirely from `[0-9a-f]`, so a reader that coerced
    # instead of type-checking would ACCEPT it. See the end-to-end test below
    # for what accepting it costs.
    int("1" * 64),
)


def _lock_with_digest(digest) -> dict:
    """A minimal, otherwise-valid one-skill lock carrying `digest`."""
    return {"registry": "owner/primary", "ref": "0" * 40, "bundles": ["adam"],
            "skills": {"adam/alpha": digest}}


@pytest.mark.parametrize("digest", _GOOD_DIGESTS)
def test_the_lock_reader_takes_either_digest_shape(digest, tmp_path):
    """The positive control for the rejection test below.

    Without it, a reader that refused EVERYTHING would pass that test while
    delivering nothing -- "the bad shapes stopped being accepted" and "the
    harness stopped accepting anything" are the same observation otherwise.
    """
    assert _hook_reader_accepts(_lock_with_digest(digest), tmp_path)


@pytest.mark.parametrize("digest", _BAD_DIGESTS)
def test_the_lock_reader_still_refuses_a_malformed_digest(digest, tmp_path):
    """Tolerating one more shape must not turn the check into a substring match.

    `re.fullmatch` with the prefix OPTIONAL is what keeps these out; a `re.search`
    or a `lstrip("sha256:")` would take every one of them. The uppercase case is a
    decision, not an oversight: the generator emits exactly one spelling, and hex
    is lower-case only, so a case-insensitive reader would be tolerance for a
    shape nothing writes -- and a lock hand-edited that far is one to refuse.

    Refused BY NAME, not merely non-zero. The hook's verdict for every non-zero
    exit from this reader is one fixed sentence pointing at $LOG, so the message
    below is the operator's ONLY statement of which field is wrong -- and a
    reader that had lost the check and crashed instead would exit non-zero too,
    passing any test that read the code alone.
    """
    proc = _run_hook_reader(_lock_with_digest(digest), tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "has no sha256 digest" in proc.stderr, proc.stderr


def test_a_digest_typed_as_a_number_does_not_delete_an_installed_skill(
        tmp_path, registry):
    """The fail-closed half of the normalisation, driven end to end.

    Every lock the generator writes types a digest as a STRING, so this shape
    only arrives by hand-edit -- but it is the one malformed value whose `str()`
    is 64 characters of `[0-9a-f]`, so a tolerance that coerced rather than
    type-checked would take it. Taking it is not merely a wrong read: the install
    loop runs `rm -rf "$DEST/$name"` BEFORE it copies and verifies, so a lock
    that is refused wholesale today would instead DELETE a skill this hook had
    already installed and verified, leaving the session with less than it started
    with under an honest-looking mismatch verdict.

    End to end rather than through the extracted reader because the deletion is
    in bash, downstream of the exit code the rejection test above asserts: the
    reader's refusal is only worth anything if it lands on the one verdict that
    carries $LEFT_IN_PLACE and runs no purge.
    """
    root, sha = registry
    project = tmp_path / "project"
    lock_path = make_project(project, root, sha)
    home = tmp_path / "home"

    first = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert first.returncode == 0, first.stderr
    assert _verdict(first).startswith("skills: 2/2 "), _verdict(first)
    assert (home / ".claude" / "skills" / "alpha" / "SKILL.md").is_file()
    installed = _tree(home / ".claude" / "skills")

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["skills"]["adam/alpha"] = int("1" * 64)
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    # The fixture is only the case it names if the digest really is unquoted.
    assert '"adam/alpha": 1111' in lock_path.read_text(encoding="utf-8")

    second = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert second.returncode == 0, second.stderr
    verdict = _verdict(second)
    # The HARM first, so a regression reddens on the deletion rather than on the
    # wording of a verdict: with the type check gone, the install loop has
    # already `rm -rf`'d alpha by the time the digest fails to verify, and this
    # is the line that catches it.
    assert (home / ".claude" / "skills" / "alpha" / "SKILL.md").is_file(), verdict
    # Nothing touched AT ALL -- tree and record both, so "left in place" means
    # exactly that rather than the weaker "alpha happened to survive".
    assert _tree(home / ".claude" / "skills") == installed, verdict
    assert "DEGRADED" in verdict, verdict
    assert "could not read" in verdict, verdict
    # And the cause reaches the log the verdict sends the operator to, which is
    # the only place it is ever stated.
    assert "has no sha256 digest" in _bootstrap_log(home), verdict


def test_hook_fails_soft_when_the_lock_is_missing(tmp_path):
    """Exit 0 with a verdict naming the file, never a non-zero that blocks boot."""
    # Run a copy of the hook from a tmp tree so its own repo's skills.lock is
    # not found by the fallback lookup.
    script = tmp_path / "hooks" / "skills-bootstrap.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(HOOK.read_bytes())
    project = tmp_path / "project"
    project.mkdir()

    proc = _run_hook(tmp_path / "home", project,
                     {"SKILLS_BOOTSTRAP_FORCE": "1"}, script=script)
    assert proc.returncode == 0
    verdict = _verdict(proc)
    assert "DEGRADED" in verdict
    assert "skills.lock" in verdict
    assert _hook_path(project / "skills.lock") in verdict


def test_missing_lock_verdict_names_one_location_once(tmp_path):
    """The two candidates collapse when they name the same file.

    `$CLAUDE_PROJECT_DIR/skills.lock` and the hook-relative `../../skills.lock`
    are the same path whenever the session's project dir IS the repo shipping
    the hook -- the common case -- and the verdict used to list it twice, which
    reads like two separate lookups failed.
    """
    repo = tmp_path / "repo"
    script = _hook_copy(repo)

    proc = _run_hook(tmp_path / "home", repo,
                     {"SKILLS_BOOTSTRAP_FORCE": "1"}, script=script)
    assert proc.returncode == 0
    verdict = _verdict(proc)
    assert "DEGRADED" in verdict
    assert _looked_in(verdict) == [_hook_path((repo / "skills.lock").resolve())]


def test_missing_lock_verdict_names_each_distinct_location_once(tmp_path):
    """De-duplicating must not collapse candidates that really are different.

    Both are still named, in priority order -- project dir first.
    """
    repo = tmp_path / "repo"
    script = _hook_copy(repo)
    project = tmp_path / "project"
    project.mkdir()

    proc = _run_hook(tmp_path / "home", project,
                     {"SKILLS_BOOTSTRAP_FORCE": "1"}, script=script)
    assert proc.returncode == 0
    assert _looked_in(_verdict(proc)) == [
        _hook_path((project / "skills.lock").resolve()),
        _hook_path((repo / "skills.lock").resolve()),
    ]


def test_missing_lock_verdict_de_duplicates_a_symlinked_project_dir(tmp_path):
    """The comparison is between RESOLVED paths, not the strings as spelled.

    A project dir reached through a symlink is the same directory as the hook's
    own repo. Comparing the raw strings would not see that, so this is the test
    that keeps the de-duplication from being "simplified" back into one.
    """
    repo = tmp_path / "repo"
    script = _hook_copy(repo)
    link = tmp_path / "link"
    _symlink_to_dir(link, repo)

    proc = _run_hook(tmp_path / "home", link,
                     {"SKILLS_BOOTSTRAP_FORCE": "1"}, script=script)
    assert proc.returncode == 0
    assert _looked_in(_verdict(proc)) == [_hook_path((repo / "skills.lock").resolve())]


def test_hook_verifies_using_the_registrys_own_generator(tmp_path):
    """The consumer shape: a repo carrying the hook and lock but no scripts/.

    Candidates 1 and 2 (beside the hook, in the project) both miss, so the
    digest implementation has to come from the fetched registry — pinned to the
    same immutable ref as the skills it is verifying.
    """
    root = tmp_path / "registry"
    # The registry ships the generator, as this repo does.
    sha = _registry_shipping_the_generator(root, {"adam/alpha": SKILL_A})

    project = tmp_path / "consumer"
    make_project(project, root, sha)
    assert not (project / "scripts").exists()

    script = tmp_path / "elsewhere" / "hooks" / "skills-bootstrap.sh"
    script.parent.mkdir(parents=True)
    script.write_bytes(HOOK.read_bytes())

    proc = _run_hook(tmp_path / "home", project,
                     {"SKILLS_BOOTSTRAP_FORCE": "1"}, script=script)
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert verdict.startswith("skills: 1/1 "), verdict
    assert verdict.endswith("OK"), verdict


def test_a_federated_source_delivers_when_the_primary_is_unreachable(tmp_path):
    """One reachable source is enough — even when the UNREACHABLE one is primary.

    The documented consumer shape (hook copied to a root with no `scripts/`, a
    project dir with no `scripts/`) plus a primary pinned to a ref that cannot
    be fetched. Everything this session gets therefore has to come from the
    federated source: the skill, and whatever the hook needs to VERIFY it.

    Written as the PROPERTY — the federated source's skill installs and verifies
    — rather than as "which copy of the digest implementation got selected".
    Where that implementation lives is an internal detail; that a reachable
    federated source still delivers when the primary is down is the invariant,
    and it has to hold however the hook computes a digest.
    """
    primary = tmp_path / "registry"
    primary_sha = make_registry(primary, {"adam/alpha": SKILL_A})
    # Left at the sibling `../cms-platform`, which is what the generator's
    # default checkout lookup finds; it ships the generator as this repo does.
    extra = tmp_path / "cms-platform"
    extra_sha = _registry_shipping_the_generator(
        extra, {"cms-platform/deploy": SKILL_B}, layout="skills")

    project = tmp_path / "consumer"
    project.mkdir()
    lock_path = project / "skills.lock"
    proc = run_generator(
        "--repo", str(primary), "--registry", primary.resolve().as_uri(),
        "--ref", primary_sha, "--bundles", "adam",
        "--source", f"cms-platform={extra.resolve().as_uri()}@{extra_sha}:skills",
        "-o", str(lock_path))
    assert proc.returncode == 0, proc.stderr
    # Take the PRIMARY down: a well-formed ref that no fetch can resolve.
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["ref"] = "0" * 40
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    script = tmp_path / "elsewhere" / "hooks" / "skills-bootstrap.sh"
    script.parent.mkdir(parents=True)
    script.write_bytes(HOOK.read_bytes())
    # The consumer shape, asserted rather than assumed: neither local candidate
    # for anything the hook might need is present.
    assert not (project / "scripts").exists()
    assert not (script.parent.parent / "scripts").exists()
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"}, script=script)
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    # 1 of 2: `deploy` is in the NUMERATOR, which only a matching digest puts it
    # in — so this asserts verified, not merely copied.
    assert verdict.startswith("skills: 1/2 "), verdict
    assert "1 skill unavailable: alpha" in verdict, verdict
    assert (home / ".claude" / "skills" / "deploy" / "SKILL.md").is_file()
    assert not (home / ".claude" / "skills" / "alpha").exists()


def test_the_hooks_digest_agrees_with_the_generators_on_a_tricky_skill(tmp_path):
    """Hold the hook's digest and generate_skills_lock.py's to the same answer.

    A second, independently written copy of a hash algorithm is exactly the
    class of bug that produces an "expected" number nobody can explain. If the
    hook carries one at all, something has to bind the two on content chosen to
    expose a difference — that binding is what makes the second copy acceptable.

    The equality is asserted transitively over the SAME directory, which keeps
    it independent of how either side is implemented:

      * the locked digest is the GENERATOR's number, computed at lock time;
      * `gsl.digest_skill_dir(installed)` recomputes the generator's number over
        the bytes that actually landed in ~/.claude/skills, and its equality
        with the locked one proves the installed tree IS the digested content;
      * the hook counting the skill installed (1/1, "OK") is the HOOK's number
        over that same tree matching the locked one.

    So hook(dir) == locked == generator(dir). Do not reduce this to the verdict
    alone: without the middle step, a git-side rewrite of the content would
    leave both sides agreeing on bytes that are no longer the fixture's.
    """
    root = tmp_path / "registry"
    root.mkdir(parents=True)
    # `* -text`: no line-ending translation through git whatever core.autocrlf
    # says on the machine running this, so `crlf.md` is still CRLF in the
    # fetched tree. It sits outside every skill directory, so it changes no digest.
    _write(root / ".gitattributes", "* -text\n")
    sha = make_registry(root, {"adam/tricky": TRICKY_SKILL})
    project = tmp_path / "project"
    make_project(project, root, sha)
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert verdict.startswith("skills: 1/1 "), verdict
    assert verdict.endswith("OK"), verdict

    installed = home / ".claude" / "skills" / "tricky"
    # The fixture survived the round trip — otherwise the two implementations
    # would be agreeing on content that is no longer tricky.
    assert (installed / "reference" / "nested" / "deep.md").is_file()
    assert (installed / "empty.txt").read_bytes() == b""
    assert (installed / "crlf.md").read_bytes() == b"line one\r\nline two\r\n"
    assert (installed / "ünïcodé-名前.md").is_file()
    assert (installed / "no-trailing-newline.txt").read_bytes() == b"no newline at end of file"

    locked = json.loads(
        (project / "skills.lock").read_text(encoding="utf-8")
    )["skills"]["adam/tricky"]
    assert gsl.LOCK_DIGEST_PREFIX + gsl.digest_skill_dir(installed) == locked


def test_hook_bundle_override_narrows_what_is_installed(tmp_path):
    """AGENTSKILLS_BUNDLE filters the lock; it never widens beyond it."""
    root = tmp_path / "registry"
    sha = make_registry(root, {"adam/alpha": SKILL_A, "fastmail/beta": SKILL_B})
    project = tmp_path / "project"
    project.mkdir()
    proc = run_generator("--repo", str(root), "--registry", root.resolve().as_uri(),
                         "--ref", sha, "--bundles", "adam,fastmail",
                         "-o", str(project / "skills.lock"))
    assert proc.returncode == 0, proc.stderr
    home = tmp_path / "home"

    proc = _run_hook(home, project, {
        "SKILLS_BOOTSTRAP_FORCE": "1",
        "AGENTSKILLS_BUNDLE": "adam",
    })
    assert proc.returncode == 0
    assert _verdict(proc).startswith("skills: 1/1 ")
    assert (home / ".claude" / "skills" / "alpha").is_dir()
    assert not (home / ".claude" / "skills" / "beta").exists()


def test_hook_fails_soft_when_the_ref_cannot_be_fetched(tmp_path, registry):
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)

    proc = _run_hook(tmp_path / "home", project, {
        "SKILLS_BOOTSTRAP_FORCE": "1",
        "AGENTSKILLS_REF": "0" * 40,
    })
    assert proc.returncode == 0
    verdict = _verdict(proc)
    assert "DEGRADED" in verdict
    assert "could not fetch" in verdict


def test_hook_rejects_a_registry_that_is_not_a_repo_or_allowed_url(tmp_path, registry):
    """The registry becomes a git remote URL — it is a trust boundary."""
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)

    proc = _run_hook(tmp_path / "home", project, {
        "SKILLS_BOOTSTRAP_FORCE": "1",
        "AGENTSKILLS_REPO": "ext::sh -c whoami",
    })
    assert proc.returncode == 0
    assert "DEGRADED" in _verdict(proc)
    assert not (tmp_path / "home" / ".claude" / "skills").exists()


def test_hook_rejects_a_hostile_registry_in_a_federated_source(tmp_path, federated):
    """The same trust boundary as the test above, one level down.

    A federated source's registry becomes a git remote exactly like the primary
    one does, and `ext::sh -c ...` is a remote HELPER git executes. Validating
    only the primary is how the extra ones become the hole, so the attack is
    tested at both levels.
    """
    project = tmp_path / "project"
    lock_path = _federated_project(project, federated)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["sources"][0]["registry"] = "ext::sh -c whoami"
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    proc = _run_hook(tmp_path / "home", project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0
    assert "DEGRADED" in _verdict(proc)
    # Rejected before anything was fetched, so nothing was installed either.
    assert not (tmp_path / "home" / ".claude" / "skills").exists()


def test_hook_rejects_an_unknown_source_key(tmp_path, federated):
    """The hook consumes locks authored elsewhere, so it must reject an unknown
    source key too — the generator already does. A `commit:` added believing it
    pins something reads as a pin while nothing consumes it.
    """
    project = tmp_path / "project"
    lock_path = _federated_project(project, federated)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["sources"][0]["commit"] = "0" * 40
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    proc = _run_hook(tmp_path / "home", project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0
    assert "DEGRADED" in _verdict(proc)
    assert not (tmp_path / "home" / ".claude" / "skills").exists()


@pytest.mark.parametrize("layout", ["/etc", "../../etc", "skills/../../etc"])
def test_hook_rejects_a_layout_that_escapes_the_fetched_tree(tmp_path, federated, layout):
    """`layout` is the one source field that becomes a filesystem path."""
    project = tmp_path / "project"
    lock_path = _federated_project(project, federated)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["sources"][0]["layout"] = layout
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    proc = _run_hook(tmp_path / "home", project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0
    assert "DEGRADED" in _verdict(proc)
    assert not (tmp_path / "home" / ".claude" / "skills").exists()


def test_hook_installs_from_every_source_into_one_flat_dest(tmp_path, federated):
    """The whole point of federation, end to end: two repos, one session."""
    primary, primary_sha, extra, extra_sha = federated
    project = tmp_path / "project"
    _federated_project(project, federated)
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert verdict.startswith("skills: 2/2 "), verdict
    assert verdict.endswith("OK"), verdict
    # Each registry named at its OWN short ref -- one line, but a reader has to
    # be able to tell what was installed from where.
    assert f"{primary.resolve().as_uri()}@{primary_sha[:7]}" in verdict, verdict
    assert f"{extra.resolve().as_uri()}@{extra_sha[:7]}" in verdict, verdict
    # Flat destination, whatever layout each source keeps its skills in.
    assert (home / ".claude" / "skills" / "alpha" / "SKILL.md").is_file()
    assert (home / ".claude" / "skills" / "deploy" / "SKILL.md").is_file()


def test_hook_degrades_only_the_skills_of_an_unreachable_source(tmp_path, federated):
    """One unreachable repo must not zero out the session's other registries.

    The assertions pin the WHOLE per-source attribution clause, not two loose
    substrings: neutering the attribution (e.g. `if false; then` around the
    SRC_LOST bookkeeping) reroutes `deploy` into the generic 'not installed'
    bucket and the source into the 'no locked skill needed it' else-branch —
    both of which are asserted ABSENT here, so that mutation now reddens.
    """
    extra = federated[2]
    project = tmp_path / "project"
    lock_path = _federated_project(project, federated)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["sources"][0]["ref"] = "0" * 40
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert verdict.startswith("skills: 1/2 "), verdict
    assert "DEGRADED" in verdict
    # The full attribution clause — only the per-source branch emits this exact
    # "(N skill(s) unavailable: <names>)" shape.
    assert (
        f"could not fetch {extra.resolve().as_uri()}@0000000 "
        "(1 skill unavailable: deploy; see " in verdict
    ), verdict
    # The generic fallbacks the mutation would drop into must NOT appear.
    assert "not installed" not in verdict, verdict
    assert "no locked skill needed it" not in verdict, verdict
    assert (home / ".claude" / "skills" / "alpha" / "SKILL.md").is_file()
    assert not (home / ".claude" / "skills" / "deploy").exists()


def test_hook_removes_a_stale_copy_when_the_source_is_unreachable(tmp_path, federated):
    """A skill reported unavailable must be GONE, not a stale body left live.

    Seed $HOME/.claude/skills/deploy with attacker-controlled content, make the
    source that owns `deploy` unreachable, and assert the seeded directory does
    not survive — a verdict that says a skill is unavailable must mean it is not
    there. (Regression for the skip-path-above-the-rm bug.)

    Seeded through `_seed_installed`, so the leftover is one this hook OWNS —
    see that helper: an unrecorded directory is refused rather than removed now,
    and that is a different invariant with its own test.
    """
    extra = federated[2]
    project = tmp_path / "project"
    lock_path = _federated_project(project, federated)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["sources"][0]["ref"] = "0" * 40  # cms-platform (owns deploy) unreachable
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    home = tmp_path / "home"
    _seed_installed(home, "deploy")

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "deploy" in verdict and "unavailable" in verdict, verdict
    # The stale, unverified copy is gone.
    assert not (home / ".claude" / "skills" / "deploy").exists()
    # The reachable source's skill still installed.
    assert (home / ".claude" / "skills" / "alpha" / "SKILL.md").is_file()


# --------------------------------------------------------------------------
# routing: every bundle a skill row names is claimed by EXACTLY ONE source
#
# The hook resolves a row against `sources[claim[bundle]]`. While that lookup
# had a DEFAULT — `claim.get(bundle, 0)`, the primary — the map only had to be
# seeded wrongly for a row to be fetched from somewhere other than the registry
# a reader would name, and seeding it from the primary's `bundles` list meant a
# federated source could claim `adam` unopposed whenever that list was omitted,
# empty, or naming something else. All three installed an attacker's body under
# a clean `skills: 1/1 … OK`.
#
# The rule is now total: the primary claims exactly its top-level `bundles`
# (required and non-empty, the same shape each extra source's list already had
# to have), each extra claims exactly its own, and a bundle claimed by nobody —
# or by two — is a lock error. Refuse an ambiguous route rather than resolve it.
# --------------------------------------------------------------------------

def _reroute_project(tmp_path: Path, bundles) -> Path:
    """A lock whose FEDERATED source claims the primary's bundle.

    The attacking registry ships its own `adam/alpha` and the lock carries THAT
    digest: a reroute is only worth defending against when the rerouted bytes
    verify, which is what made the reproduction report `1/1 … OK` rather than a
    digest mismatch. `bundles` is the primary's claim, neutered here the three
    ways that evaded the collision check (absent when None).
    """
    primary = tmp_path / "registry"
    primary_sha = make_registry(primary, {"adam/alpha": SKILL_A})
    evil = tmp_path / "evil"
    evil_sha = make_registry(
        evil, {"adam/alpha": {"SKILL.md": "---\nname: alpha\n---\nEVIL BODY\n"}})

    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    lock = {
        "registry": primary.resolve().as_uri(),
        "ref": primary_sha,
        "sources": [{"registry": evil.resolve().as_uri(), "ref": evil_sha,
                     "bundles": ["adam"], "layout": gsl.DEFAULT_LAYOUT}],
        "skills": {"adam/alpha": gsl.digest_skill_dir(
            evil / "plugins" / "adam" / "skills" / "alpha")},
    }
    if bundles is not None:
        lock["bundles"] = bundles
    (project / "skills.lock").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return project


@pytest.mark.parametrize("bundles", [None, [], ["adam"]],
                         ids=["bundles-absent", "bundles-empty", "bundles-claims-adam"])
def test_a_federated_source_cannot_capture_a_bundle_from_the_primary(tmp_path, bundles):
    """Whatever the primary's list says, `adam` cannot be taken out from under it.

    Absent and empty are refused because the primary's claim is required —
    nothing is assumed for it — and the explicit `["adam"]` is refused as a
    collision. Reported as a lock the hook could not read, with NOTHING
    installed: the rerouted body must not reach ~/.claude/skills at all.
    """
    project = _reroute_project(tmp_path, bundles)
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "DEGRADED" in verdict, verdict
    assert "could not read" in verdict, verdict
    assert not (home / ".claude" / "skills").exists()


def test_a_bundle_no_source_claims_is_refused_rather_than_sent_to_the_primary(
        tmp_path, registry):
    """The default that is gone: `claim.get(bundle, 0)`.

    A lock whose `bundles` names something else entirely still resolved every
    `adam/*` row against source 0 and installed it, so the field that says where
    a bundle comes from could disagree with where it came from and nothing said
    so. There is no registry, ref or layout to resolve such a row against —
    refuse the lock and name the bundle.
    """
    root, sha = registry
    project = tmp_path / "project"
    lock_path = make_project(project, root, sha)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["bundles"] = ["adam-local"]          # claims a bundle no skill row names
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    assert "DEGRADED" in _verdict(proc), _verdict(proc)
    assert not (home / ".claude" / "skills").exists()


def test_narrowing_to_one_bundle_cannot_hide_an_unroutable_row(tmp_path, registry):
    """AGENTSKILLS_BUNDLE filters what is INSTALLED, not what is VALIDATED.

    The claim check runs before the filter on purpose: a session narrowed to
    `adam` must not accept a lock whose other rows have no source, or the same
    lock would be honest or malformed depending on an environment variable.
    """
    root, sha = registry
    project = tmp_path / "project"
    lock_path = make_project(project, root, sha)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["skills"]["fastmail/orphan"] = "0" * 64       # no source claims `fastmail`
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    home = tmp_path / "home"

    proc = _run_hook(home, project, {
        "SKILLS_BOOTSTRAP_FORCE": "1",
        "AGENTSKILLS_BUNDLE": "adam",
    })
    assert proc.returncode == 0, proc.stderr
    assert "DEGRADED" in _verdict(proc), _verdict(proc)
    assert not (home / ".claude" / "skills").exists()


# --------------------------------------------------------------------------
# the hook's python is isolated from the project directory (`python3 -I`)
#
# python puts the process's cwd on sys.path, and this hook's cwd is the
# session's PROJECT DIRECTORY — attacker-controlled in exactly the scenario the
# lock exists for. A `hashlib.py` sitting there was the sha256 the integrity
# check used; a `json.py` was the lock parser. Both defeat the header's central
# claim (pinned, verified, nothing fetched is executed) from inside the checks
# that are supposed to establish it.
# --------------------------------------------------------------------------

_FAKE_HASHLIB = '''\
"""A `hashlib` that reports every input as the all-zero digest."""


class _Hash:
    def __init__(self, *args, **kwargs):
        pass

    def update(self, *args, **kwargs):
        pass

    def hexdigest(self):
        return "0" * 64

    def digest(self):
        return b"\\0" * 32


def sha256(*args, **kwargs):
    return _Hash()


def new(*args, **kwargs):
    return _Hash()
'''


def test_a_hashlib_in_the_project_directory_is_not_the_hooks_hasher(tmp_path, registry):
    """The integrity check must not be supplied by the tree it is checking.

    The lock names the all-zero digest — what the planted `hashlib` "computes"
    for anything — so without isolation every skill verifies and the hook
    reports `OK`. With it, the real sha256 runs and the row is a mismatch, which
    is the observable that says the planted module was never imported.
    """
    root, sha = registry
    project = tmp_path / "project"
    lock_path = make_project(project, root, sha)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["skills"] = {"adam/alpha": "0" * 64}
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    _write(project / "hashlib.py", _FAKE_HASHLIB)
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"}, cwd=project)
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "1 digest mismatch (alpha)" in verdict, verdict
    assert not (home / ".claude" / "skills" / "alpha").exists()


def test_a_json_in_the_project_directory_is_not_the_hooks_lock_reader(tmp_path, registry):
    """Shadowing `json` replaces the lock ITSELF — parser, validation and all.

    The planted module ignores the file on disk and hands back a lock pinning
    another registry, so without isolation the hook installs from a repo the
    real lock never names. The two registries ship differently-named skills, so
    which one landed in ~/.claude/skills is the whole assertion.
    """
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)

    elsewhere = tmp_path / "elsewhere"
    elsewhere_sha = make_registry(elsewhere, {"adam/substituted": SKILL_B})
    substituted = {
        "registry": elsewhere.resolve().as_uri(),
        "ref": elsewhere_sha,
        "bundles": ["adam"],
        "skills": {"adam/substituted": gsl.digest_skill_dir(
            elsewhere / "plugins" / "adam" / "skills" / "substituted")},
    }
    # Only load/loads: `emit`'s encoder wants json.dumps, and its absence sends
    # that one call down the documented printf fallback, which still prints a
    # verdict. What is being probed here is the READER, not the encoder.
    _write(project / "json.py", (
        '"""A `json` whose load() ignores the file and returns another lock."""\n'
        f"_LOCK = {substituted!r}\n"
        "\n\n"
        "def load(handle, **kwargs):\n"
        "    return _LOCK\n"
        "\n\n"
        "def loads(text, **kwargs):\n"
        "    return _LOCK\n"
    ))
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"}, cwd=project)
    assert proc.returncode == 0, proc.stderr
    assert not (home / ".claude" / "skills" / "substituted").exists()
    for name in ("alpha", "beta"):
        assert (home / ".claude" / "skills" / name / "SKILL.md").is_file()


def test_every_python3_the_hook_launches_is_isolated():
    """A drift alarm on the flag itself, since only two call sites are probed.

    The hook launches python six times — the verdict encoder, the lock reader,
    the digest, and the three readers/writers of the install record (the
    attribution read that decides what the install loop may OVERWRITE, the prune
    planner that decides what it may REMOVE, and the writer that rewrites it) —
    and every one of them puts the project directory on sys.path without `-I`.
    The two that read the record are the ones this alarm earns its keep on most:
    a `json.py` in the project directory would otherwise decide which of the
    user's directories the hook then `rm -rf`s.

    Matching is lexical and deliberately narrow: an invocation is `python3`
    followed by a flag, which is the shape all six have (`-c`, `-`) and which
    no prose mention or `command -v python3` probe takes. Comment lines are
    dropped first so the header's own `python3 -I` reference is not counted as a
    call site.
    """
    code = "\n".join(
        line for line in HOOK.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    assert re.findall(r"\bpython3\s+(-\S+)", code) == ["-I"] * 6


# --------------------------------------------------------------------------
# ... and the SAME invariant on every OTHER path that skips a skill
#
# The hook has five of them — unreachable source (above), digest mismatch,
# duplicate destination name, absent SKILL.md, project collision — and each one
# removes `$DEST/$name` before it records the skip. That ordering is the whole
# reason a verdict can be trusted: "unavailable" has to MEAN not there, or a
# stale, unverified body stays live in ~/.claude/skills for the model to load on
# turn one while the verdict says the skill was skipped.
#
# Each test below seeds $HOME/.claude/skills/<name> with junk, drives its path,
# and asserts the directory is GONE — so deleting that path's `rm -rf` reddens
# exactly one test and names which skip leaked.
#
# The seed is RECORDED as this hook's own install (`_seed_installed`), and that
# is load-bearing rather than incidental: the install loop no longer touches a
# destination it cannot attribute to itself, so an UNRECORDED directory is
# refused outright and reported as `shadowed` — which is a different invariant,
# covered by `test_the_install_loop_refuses_to_overwrite_what_it_did_not_install`
# below. What these tests exist for is the other one, that the hook's OWN
# leftover must not survive a skip, so the fixture has to be a leftover the hook
# owns. `_seed_stale` stays unrecorded for the `purge_locked_destinations`
# tests, which deliberately still remove regardless of provenance.
# --------------------------------------------------------------------------

_STALE = "---\nname: {name}\n---\nATTACKER-CONTROLLED STALE BODY\n"


def _seed_stale(home: Path, name: str) -> Path:
    """Plant an unverified copy where the hook installs, and return its dir."""
    _write(home / ".claude" / "skills" / name / "SKILL.md", _STALE.format(name=name))
    return home / ".claude" / "skills" / name


def _seed_installed(home: Path, name: str) -> Path:
    """`_seed_stale`, plus the install record entry that makes it the HOOK's.

    The digest recorded is the seeded directory's own, which is what "this hook
    installed it and nobody has touched it since" means on the next run — so the
    install loop is allowed to replace it, and every `rm -rf` under test is
    reached. Seed WITHOUT the record and the loop refuses the skill instead,
    which is the point of the guard, not a way to drive these paths.
    """
    path = _seed_stale(home, name)
    record_path = home / ".claude" / "skills" / _RECORD
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        record = {"version": 1, "installed": []}
    record["installed"].append({
        "name": name,
        "registry": "https://example.test/registry",
        "bundle": "adam",
        "digest": gsl.digest_skill_dir(path),
    })
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def test_hook_removes_the_unverified_copy_on_a_digest_mismatch(tmp_path, registry):
    """Integrity FAILED must leave NOTHING behind, not merely say so.

    This path is the one where the leftover is worst: the bytes were already
    copied in before the digest was checked, so skipping the removal leaves
    content that FAILED verification sitting in ~/.claude/skills, under the exact
    name the model looks the skill up by.
    """
    root, sha = registry
    project = tmp_path / "project"
    lock_path = make_project(project, root, sha)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["skills"]["adam/alpha"] = "0" * 64
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    home = tmp_path / "home"
    stale = _seed_installed(home, "alpha")

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "1 digest mismatch (alpha)" in verdict, verdict
    assert not stale.exists()
    # The skill that DID verify is unaffected — the removal is per-skill.
    assert (home / ".claude" / "skills" / "beta" / "SKILL.md").is_file()


def test_hook_removes_a_stale_copy_when_two_lock_rows_collide(tmp_path):
    """Neither colliding row installs, so neither name may survive either."""
    project = _duplicate_basename_project(tmp_path)
    home = tmp_path / "home"
    stale = _seed_installed(home, "alpha")

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "share a destination name" in verdict, verdict
    assert not stale.exists()


def test_hook_removes_a_stale_copy_when_the_skill_is_absent_at_the_pinned_ref(
        tmp_path, registry):
    """A lock row naming a skill the fetched tree does not have.

    Hand-edited (the generator only writes rows it digested from a real
    directory), which is exactly the lock shape the hook has to survive: the row
    resolves to a path with no SKILL.md, so nothing can be installed or verified
    — and a stale directory of that name must not be left standing in its place.
    """
    root, sha = registry
    project = tmp_path / "project"
    lock_path = make_project(project, root, sha)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["skills"]["adam/ghost"] = "0" * 64
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    home = tmp_path / "home"
    stale = _seed_installed(home, "ghost")

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "not installed (ghost)" in verdict, verdict
    assert not stale.exists()
    assert (home / ".claude" / "skills" / "alpha" / "SKILL.md").is_file()


def test_hook_removes_a_stale_copy_that_shadows_a_project_owned_skill(tmp_path, registry):
    """The collision skip is the one where leaving the copy DEFEATS the guard.

    Personal ~/.claude/skills SHADOWS the project's .claude/skills (E2 / C3), so
    a stale personal `alpha` left in place goes on shadowing the repo-owned
    `alpha` — the precise condition the guard exists to end. Reporting
    "repo-owned wins" while the personal copy still wins is worse than not
    reporting at all.
    """
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)
    _write(project / ".claude" / "skills" / "alpha" / "SKILL.md", "repo-owned alpha\n")
    home = tmp_path / "home"
    stale = _seed_installed(home, "alpha")

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "collision skipped, repo-owned wins (alpha)" in verdict, verdict
    assert not stale.exists()
    assert (home / ".claude" / "skills" / "beta" / "SKILL.md").is_file()


def _duplicate_basename_project(tmp_path: Path) -> Path:
    """A project whose lock has two bundles shipping the same skill BASENAME.

    The generator refuses to WRITE one (see
    test_a_basename_shared_by_two_bundles_is_a_generator_error), so this
    hand-edits the second row in — which is the only way such a lock exists, and
    exactly the lock shape the hook has to survive.
    """
    root = tmp_path / "registry"
    sha = make_registry(root, {"adam/alpha": SKILL_A, "fastmail/alpha": SKILL_B})
    project = tmp_path / "project"
    project.mkdir()
    lock_path = project / "skills.lock"
    proc = run_generator("--repo", str(root), "--registry", root.resolve().as_uri(),
                         "--ref", sha, "--bundles", "adam", "-o", str(lock_path))
    assert proc.returncode == 0, proc.stderr
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["skills"]["fastmail/alpha"] = gsl.digest_skill_dir(
        root / "plugins" / "fastmail" / "skills" / "alpha"
    )
    # The hand-edit has to stay ROUTABLE: routing is total, so a `fastmail/*`
    # row whose bundle the lock never claims is refused for that reason alone,
    # and this fixture is about the flat-destination collision instead.
    lock["bundles"].append("fastmail")
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return project


def test_hook_reports_two_lock_rows_that_want_the_same_destination(tmp_path):
    """The generator refuses to write this; the hook still has to survive one.

    A hand-edited lock -- or one written before that rule existed -- can carry
    two bundles shipping the same skill directory name. The install dir is
    FLAT, so quietly letting the last row win would make one skill silently
    become the other's bytes, decided by sort order rather than by anyone.
    """
    project = _duplicate_basename_project(tmp_path)
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert verdict.startswith("skills: 0/2 "), verdict
    assert "share a destination name" in verdict
    assert "adam/alpha" in verdict and "fastmail/alpha" in verdict
    # Neither wins. Installing either one is the silent overwrite being caught.
    assert not (home / ".claude" / "skills" / "alpha").exists()


# --------------------------------------------------------------------------
# the python <-> bash framing contract
#
# python writes the records NUL-delimited and states, in `meta`, how many it
# wrote; bash counts what it actually read back and refuses if the two disagree,
# because a desynced stream means every skill's SOURCE INDEX is suspect — a row
# resolved against the wrong tree installs one registry's bytes under another's
# name. No lock can forge a disagreement (that is what the framing buys), so the
# only way to assert the cross-check is to INJECT one into a scratch copy of the
# writer and watch the reader catch it.
# --------------------------------------------------------------------------

_META_WRITE = 'handle.write("%d\\n%d\\n" % (len(sources), len(rows)))'


def test_a_source_count_that_disagrees_with_the_stream_is_refused(tmp_path, registry):
    """python claims two sources, writes one: refuse, don't resolve."""
    script = _hook_with(
        tmp_path, _META_WRITE,
        _META_WRITE.replace("len(sources)", "len(sources) + 1"), "desync-sources")
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)
    home = tmp_path / "home"
    stale = _seed_stale(home, "alpha")

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"}, script=script)
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "source framing mismatch: 2 declared, 1 read" in verdict, verdict
    # Refusing is only half of it: the purge is what makes the verdict true.
    assert not stale.exists()
    assert not (home / ".claude" / "skills" / "beta").exists()


def test_a_skill_count_that_disagrees_with_the_stream_is_refused(tmp_path, registry):
    """The row half of the same contract, checked after the install loop.

    Everything this run installed goes too: the framing is what says which rows
    were meant to exist at all, so none of it can be trusted once it desyncs.
    """
    script = _hook_with(
        tmp_path, _META_WRITE,
        _META_WRITE.replace("len(rows)", "len(rows) + 1"), "desync-skills")
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"}, script=script)
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "skill framing mismatch: 3 declared, 2 read" in verdict, verdict
    for name in ("alpha", "beta"):
        assert not (home / ".claude" / "skills" / name).exists()


# --------------------------------------------------------------------------
# ... and the same invariant on the BAIL-OUTS, which return BEFORE the install
# loop runs at all
#
# `purge_locked_destinations` is what covers those. Every per-skill removal
# tested above lives INSIDE the install loop; each path below returns before it
# is reached, which is the incident the function's own comment names, verbatim:
# a seeded `alpha/SKILL.md` surviving under "skills: DEGRADED — could not fetch
# ...", a verdict a reader takes to mean nothing was installed, while the model
# loads the stale body on turn one.
#
# The hook has six such call sites. Two — the source-count and skill-count
# framing mismatches — are covered by the two tests directly above. These are
# the rest that can be driven.
#
# The VERDICT half is asserted as tightly as the removal, in both directions:
# these bail-outs deliberately omit the $LEFT_IN_PLACE disclaimer BECAUSE the
# purge makes the clean-slate reading true, so its absence is part of what is
# being pinned. A verdict that gained it while the purge still ran, or lost the
# purge while still claiming a clean slate, is the same bug from either side.
#
# DELIBERATELY NOT COVERED: the `mkdir -p "$DEST"` failure path. Its purge
# cannot be observed by construction — a seeded `$DEST/<name>` existing means
# `$DEST` is an existing directory, and `mkdir -p` on an existing directory
# SUCCEEDS, so the branch and the seed cannot both be present. Reaching it needs
# `$DEST` unreachable through a permission mode that would equally defeat the
# `rm -rf` the test would then assert on — and that root ignores outright, which
# is most CI containers. That is a test that could not fail for the reason it
# names, so the gap is left stated rather than papered over.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case, expected", [
    ("all-sources-unreachable", "could not fetch"),
    ("git-absent", "git not found on PATH"),
    ("meta-unreadable", "framing error"),
])
def test_a_bail_out_purges_every_destination_the_lock_names(
        tmp_path, registry, case, expected):
    """A bail-out's verdict must MEAN the lock's skills are not installed.

    Both of the lock's destinations are pre-seeded with unverified bytes, one
    bail-out is driven, and the assertion is that neither survives it. Delete
    the `purge_locked_destinations` call from the path under test and exactly
    this case reddens, naming which bail-out leaked.
    """
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)
    home = tmp_path / "home"
    seeded = [_seed_stale(home, name) for name in ("alpha", "beta")]
    assert all(path.is_dir() for path in seeded), "nothing was seeded to leak"

    script = HOOK
    env = {"SKILLS_BOOTSTRAP_FORCE": "1"}
    if case == "all-sources-unreachable":
        # This lock names ONE source, so pinning it to a ref that repo does not
        # carry leaves `fetched` at 0 and fires the all-unreachable bail-out. A
        # bogus ref rather than a dead host keeps it hermetic and instant, and
        # the branch cannot tell the two apart — it counts successes.
        env["AGENTSKILLS_REF"] = "0" * 40
    elif case == "git-absent":
        # The prerequisite the hook deliberately checks BELOW the lock read
        # rather than beside python3/HOME, precisely so this failure path is one
        # the purge can run on. Siting it back up top is the regression.
        env["PATH"] = _path_farm(tmp_path, "git")
    else:
        # No lock can forge an unreadable `meta` — that is what the framing
        # buys — so the writer is faulted in a scratch copy, the same way the
        # two count-mismatch tests above do, and against the same anchor.
        script = _hook_with(tmp_path, _META_WRITE, 'handle.write("")', case)

    proc = _run_hook(home, project, env, script=script)
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert verdict.startswith("skills: DEGRADED"), verdict
    assert expected in verdict, verdict
    # Truthful in the other direction too: the disclaimer belongs only to the
    # bail-outs ABOVE the lock read, which genuinely cannot know these names.
    assert "LEFT IN PLACE" not in verdict, verdict
    # And the purge is what earns that. EVERY destination the lock names, not
    # merely the one that would have been installed first.
    for path in seeded:
        assert not path.exists(), verdict
    assert list((home / ".claude" / "skills").iterdir()) == [], verdict


# --------------------------------------------------------------------------
# the verdict encoder
# --------------------------------------------------------------------------

def test_the_verdict_is_written_by_the_encoder_not_the_printf_fallback(tmp_path, registry):
    """Every verdict carries an em-dash, and `ensure_ascii=True` is what makes
    encoding it incapable of failing: the payload leaves json.dumps as 7-bit
    `\\u2014`, so the explicit `.encode("ascii")` onto stdout.buffer cannot
    raise whatever the surface's encoding is.

    Asserted on the RAW bytes rather than the decoded verdict, because both
    branches produce the same STRING: only the encoder escapes the em-dash. A
    silent fall-through to the printf fallback — which no longer emits a fixed
    literal — is exactly what this pins, and the traceback such a fall-through
    leaves on stderr is the second assertion.
    """
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)

    proc = _run_hook(tmp_path / "home", project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0
    assert proc.stderr == "", proc.stderr
    assert _verdict(proc).endswith("— OK")
    assert "\\u2014" in proc.stdout, proc.stdout
    assert "—" not in proc.stdout, proc.stdout


# --------------------------------------------------------------------------
# the fetch budget is a real deadline on every platform
#
# `timeout(1)` is absent on stock macOS and in minimal containers — the very
# bash-3.2 platform this hook targets — and the budget used to be enforced
# ONLY through it, so there it did not apply at all: a remote that accepts TCP
# and then never speaks (a TLS/connect-phase tarpit) hung the hook indefinitely,
# blocking the session start the whole fail-soft design exists to protect.
# git's own GIT_HTTP_LOW_SPEED_* detector does not cover it — that measures
# transfer throughput and never fires before the first byte.
#
# Liveness cannot be asserted without time passing, so these two are the
# suite's only wall-clock tests: the budget is shortened in a scratch copy, and
# the subprocess cap is the assertion (it is ~7x the shortened budget, and the
# failure being guarded is UNBOUNDED, so the margin is not a flake surface).
# The tarpit is an in-process listener on 127.0.0.1:0 — no network, nothing
# outside the test.
# --------------------------------------------------------------------------

class _Tarpit:
    """The listener's side of the stall, and what it can still observe."""

    def __init__(self, port: int, held: list):
        self.port = port
        self.held = held

    def clients_all_gone(self, within: float) -> bool:
        """True once every accepted connection has been closed by its client.

        This is how an ORPHAN is seen from outside. `git fetch` does the network
        in a helper child, so killing git alone leaves that helper blocked in
        the handshake, holding this connection open — the stall the deadline was
        supposed to end simply outlives the process that reported it ended.
        Killing the process GROUP takes the helper too, and the socket closes.

        Each connection has the client's TLS ClientHello sitting in it, so a
        readable socket is drained until recv() reports the client is gone.
        Nothing here waits out `within` on the happy path: the socket resolves
        as soon as its last holder dies.

        "Gone" has two spellings. A graceful close gives b"" (EOF). A process
        that was KILLED — which is the whole point of this test — has its
        socket torn down abortively, and Windows surfaces that RST as
        ConnectionResetError rather than as EOF. Both mean the client is gone,
        which is the only thing being observed here; treating the reset as an
        error would fail the test on the very outcome it is asserting.
        """
        deadline = time.monotonic() + within
        pending = list(self.held)
        while pending and time.monotonic() < deadline:
            readable, _, _ = select.select(pending, [], [], 0.1)
            for connection in readable:
                try:
                    gone = connection.recv(65536) == b""
                except (ConnectionResetError, ConnectionAbortedError):
                    gone = True
                if gone:
                    pending.remove(connection)
        return not pending


@pytest.fixture
def tarpit():
    """A local port that completes the TCP handshake and then says nothing."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(16)
    held = []

    def accept_forever():
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            held.append(connection)   # accepted, never spoken to

    thread = threading.Thread(target=accept_forever, daemon=True)
    thread.start()
    yield _Tarpit(server.getsockname()[1], held)
    # shutdown() before close(): closing a listening socket does not wake a
    # thread already blocked in accept(), so a plain close would make teardown
    # sit out the join.
    try:
        server.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    server.close()
    for connection in held:
        connection.close()
    thread.join(timeout=5)


def _tarpit_project(tmp_path: Path, port: int) -> Path:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    lock = {"registry": f"https://127.0.0.1:{port}/x.git", "ref": "0" * 40,
            "bundles": ["adam"], "skills": {"adam/alpha": "0" * 64}}
    (project / "skills.lock").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return project


@pytest.mark.parametrize("timeout_on_path", [True, False],
                         ids=["timeout-present", "timeout-absent"])
def test_the_fetch_budget_ends_a_stalled_fetch(tmp_path, tarpit, timeout_on_path):
    """The hook returns a verdict; it does not hang. Both branches of run_git.

    And the stall is actually OVER when it says so: git's network helper is a
    child process, so a deadline that reaps only git leaves the helper holding
    the connection — the hook reports the fetch as failed while the thing it
    failed on runs on. The tarpit is what can see that, because the socket stays
    open exactly as long as some client process holds it.
    """
    script = _hook_with(tmp_path, "FETCH_BUDGET=60", "FETCH_BUDGET=3", "short-budget")
    project = _tarpit_project(tmp_path, tarpit.port)
    extra_env = {"SKILLS_BOOTSTRAP_FORCE": "1"}
    if not timeout_on_path:
        extra_env["PATH"] = _path_farm(tmp_path, "timeout")

    proc = _run_hook(tmp_path / "home", project, extra_env, script=script, timeout=45)
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "could not fetch" in verdict, verdict
    assert f"127.0.0.1:{tarpit.port}" in verdict, verdict
    # The stall was reached at all -- otherwise the rest asserts nothing.
    assert tarpit.held, "git never connected, so nothing was stalled"
    assert tarpit.clients_all_gone(within=20), (
        "a git helper outlived the fetch that spawned it -- the deadline killed "
        "git but not its process group"
    )


# --------------------------------------------------------------------------
# the CI shape, on a lock that HAS a federated source
#
# .github/workflows/ci.yml runs the two lock checks with no flags at all —
# `python3 scripts/generate_skills_lock.py --check` and `--check-current`, from
# the checkout root — so registry, ref, bundles, the whole `sources` array, and
# where each source's checkout lives are every one of them inherited or
# defaulted. This repo's own lock carries no `sources` today, which makes the
# federated half of that invocation UNEXERCISED rather than working: it would
# first be tried on the PR that adopts a second registry.
#
# These reproduce the CI layout instead of the harness's — a checkout with its
# own skills.lock and its own copy of the generator, invoked from its root —
# so the federated path is covered by the pytest job that already runs
# `scripts/` repo-wide, no network and no sibling clone required.
# --------------------------------------------------------------------------

def _ci_shaped_checkout(tmp_path: Path, source_root: Path) -> Tuple[Path, Path]:
    """A repo laid out like the CI checkout, with a FEDERATED skills.lock.

    Returns (checkout, its copy of the generator). The checkout carries the
    machinery a real one does — `scripts/generate_skills_lock.py`,
    `.claude/hooks/skills-bootstrap.sh`, `skills.lock` — so the generator
    resolves its own REPO_ROOT, default `--repo` and default lock path exactly
    as it does in CI.
    """
    checkout = tmp_path / "checkout"
    checkout.mkdir(parents=True)
    (checkout / "scripts").mkdir()
    (checkout / "scripts" / "generate_skills_lock.py").write_bytes(GENERATOR.read_bytes())
    _hook_copy(checkout)
    sha = make_registry(checkout, {"adam/alpha": SKILL_A})

    extra_sha = make_registry(source_root, {"cms-platform/deploy": SKILL_B}, layout="skills")
    lock = checkout / "skills.lock"
    proc = run_generator(
        "--repo", str(checkout), "--registry", checkout.resolve().as_uri(),
        "--ref", sha, "--bundles", "adam",
        "--source", f"cms-platform={source_root.resolve().as_uri()}@{extra_sha}:skills",
        "--source-repo", f"cms-platform={source_root}",
        "-o", str(lock))
    assert proc.returncode == 0, proc.stderr
    assert "sources" in json.loads(lock.read_text(encoding="utf-8"))
    return checkout, checkout / "scripts" / "generate_skills_lock.py"


def test_the_ci_lock_checks_pass_on_a_federated_lock(tmp_path):
    """Exactly what ci.yml runs, against a lock carrying a second registry.

    The source is left at the sibling `../cms-platform` — the convention
    `source_checkout` defaults to and `scripts/skills_registries.yml` already
    uses — so both checks resolve it with no `--source-repo`, which is the only
    way they can pass in CI, where no flags are passed at all.
    """
    checkout, generator = _ci_shaped_checkout(tmp_path, tmp_path / "cms-platform")

    faithful = run_generator("--check", cwd=checkout, script=generator)
    assert faithful.returncode == 0, faithful.stdout + faithful.stderr
    current = run_generator("--check-current", cwd=checkout, script=generator)
    assert current.returncode == 0, current.stdout + current.stderr

    # ...and the same lock installs end to end, both registries into one session.
    home = tmp_path / "home"
    proc = _run_hook(home, checkout, {"SKILLS_BOOTSTRAP_FORCE": "1"},
                     script=checkout / ".claude" / "hooks" / "skills-bootstrap.sh")
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert verdict.startswith("skills: 2/2 "), verdict
    assert verdict.endswith("OK"), verdict
    assert (home / ".claude" / "skills" / "alpha" / "SKILL.md").is_file()
    assert (home / ".claude" / "skills" / "deploy" / "SKILL.md").is_file()


def test_the_ci_lock_checks_say_how_to_fix_a_missing_source_checkout(tmp_path):
    """The failure CI gets when a federated source is not checked out beside it.

    Digests are read from git, never a working tree, so a federated lock needs
    that source's clone present locally — and a CI job that checks out only the
    workspace has none. That is a real precondition, not a bug; what makes it
    survivable is that the failure NAMES the missing path and the flag that
    overrides it, rather than surfacing as a digest mismatch or a traceback.
    """
    checkout, generator = _ci_shaped_checkout(tmp_path, tmp_path / "elsewhere" / "cms-platform")

    for flag in ("--check", "--check-current"):
        proc = run_generator(flag, cwd=checkout, script=generator)
        assert proc.returncode != 0, flag + " unexpectedly passed"
        assert "Traceback" not in proc.stderr, proc.stderr
        assert str(tmp_path / "cms-platform") in proc.stderr, proc.stderr
        assert "--source-repo" in proc.stderr, proc.stderr


def test_this_repos_own_lock_is_installable_end_to_end(tmp_path):
    """Dogfood: the committed skills.lock, against a local clone of this repo.

    Uses a file:// clone of the real repo rather than the network, so the test
    stays hermetic while still exercising the SHA-pinned fetch path (a commit
    SHA cannot be reached by `git clone --branch`, so this is the code path
    that would silently be untested otherwise).
    """
    lock = json.loads((REPO_ROOT / "skills.lock").read_text(encoding="utf-8"))
    have = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "-e", lock["ref"] + "^{commit}"],
        capture_output=True,
    )
    if have.returncode != 0:
        pytest.skip(f"pinned ref {lock['ref']} is not present in this checkout")

    project = tmp_path / "project"
    project.mkdir()
    lock["registry"] = REPO_ROOT.as_uri()
    (project / "skills.lock").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    home = tmp_path / "home"

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    expected = len(lock["skills"])
    assert verdict.startswith(f"skills: {expected}/{expected} "), verdict
    assert verdict.endswith("OK"), verdict


# --------------------------------------------------------------------------
# skills that LEAVE the lock (#71)
#
# `purge_locked_destinations` removes what the lock NAMES, on the bail-out paths
# only. Nothing removed what the lock STOPPED naming, and nothing ran on the
# success path at all — so a withdrawn skill stayed live in ~/.claude/skills
# forever while the verdict read `skills: N/N … — OK`, and a rename left both
# names loaded. Reproduced before any of this was written: run 1 with a
# two-skill lock, run 2 with a one-skill lock, dropped skill still there.
#
# The removal cannot be "delete what the lock does not name": ~/.claude/skills
# is the USER's directory, and the claude.ai account-sync channel writes into
# `synced/`. So it is driven by the hook's own install record, and every test
# below pins one of the four conditions a removal needs — or one of the two
# reasons a directory is left alone and SAID SO rather than silently kept.
#
# Each is written to fail if the rule it names is dropped: the ones that assert
# survival also assert that a real orphan was removed in the same run, so
# "nothing was deleted" cannot pass them by the prune simply not running.
# --------------------------------------------------------------------------

_RECORD = ".skills-bootstrap-installed.json"


def _record(home: Path) -> dict:
    """The hook's install record, parsed."""
    return json.loads(
        (home / ".claude" / "skills" / _RECORD).read_text(encoding="utf-8"))


def _relock(lock_path: Path, full: dict, keep) -> None:
    """Rewrite `lock_path` naming only the skills in `keep`.

    A skill LEAVES a lock exactly this way — the artifact is the same whether it
    was withdrawn upstream, renamed, or retired, and rewriting from a saved copy
    lets one test drop a row and add another back (a rename) without a second
    fixture commit.
    """
    lock = dict(full)
    lock["skills"] = {key: digest for key, digest in full["skills"].items()
                      if key.rsplit("/", 1)[-1] in keep}
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


def _install_then_relock(tmp_path: Path, registry, keep) -> Tuple[Path, Path]:
    """Install the two-skill lock, then narrow it to `keep`. Returns (home, lock)."""
    root, sha = registry
    project = tmp_path / "project"
    lock_path = make_project(project, root, sha)
    full = json.loads(lock_path.read_text(encoding="utf-8"))
    home = tmp_path / "home"
    first = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert first.returncode == 0, first.stderr
    assert _verdict(first).endswith("— OK"), _verdict(first)
    _relock(lock_path, full, keep)
    return home, project


def test_a_skill_dropped_from_the_lock_is_removed_and_the_verdict_says_so(
        tmp_path, registry):
    """The defect itself: `alpha` leaves the lock, and must not survive it."""
    home, project = _install_then_relock(tmp_path, registry, {"beta"})
    assert (home / ".claude" / "skills" / "alpha" / "SKILL.md").is_file()

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert verdict.startswith("skills: 1/1 "), verdict
    # Named, not merely gone. A silent removal is the same unreadable state as a
    # silent leak, from the other side.
    assert "removed 1 skill no longer in the lock (alpha)" in verdict, verdict
    assert not (home / ".claude" / "skills" / "alpha").exists(), verdict
    assert (home / ".claude" / "skills" / "beta" / "SKILL.md").is_file()
    assert [entry["name"] for entry in _record(home)["installed"]] == ["beta"]


def test_a_bundle_the_lock_has_emptied_still_reaps_its_skills(tmp_path, registry):
    """The scope is the lock's ROUTING, not the bundles its rows happen to name.

    Withdrawing every skill of a bundle at once leaves a lock that still
    declares the bundle and names none of its skills — so a scope derived from
    the rows would claim nothing, find no owner for any recorded install, and
    leak the entire bundle. Deriving it from `claim` is what makes the emptied
    case behave like every other withdrawal.
    """
    home, project = _install_then_relock(tmp_path, registry, set())

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert verdict.startswith("skills: 0/0 "), verdict
    assert "removed 2 skills no longer in the lock (alpha, beta)" in verdict, verdict
    assert [path.name for path in (home / ".claude" / "skills").iterdir()] == [_RECORD]
    assert _record(home)["installed"] == []


def test_a_skill_the_hook_never_installed_is_never_removed(tmp_path, registry):
    """The hard constraint. ~/.claude/skills is the user's own directory.

    A hand-made skill no lock has ever named is planted, and the run that
    removes a genuine orphan is driven right past it. The orphan assertion is
    what makes this a test of the SCOPE rather than of the prune being inert:
    `alpha` goes, `handmade` stays, in the same run.
    """
    home, project = _install_then_relock(tmp_path, registry, {"beta"})
    _write(home / ".claude" / "skills" / "handmade" / "SKILL.md",
           "---\nname: handmade\n---\nwritten by hand, never locked\n")
    mine = _tree(home / ".claude" / "skills" / "handmade")

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "removed 1 skill no longer in the lock (alpha)" in verdict, verdict
    assert _tree(home / ".claude" / "skills" / "handmade") == mine, verdict
    assert "handmade" not in verdict, verdict


def test_a_rename_leaves_only_the_new_name(tmp_path):
    """Old name out, new name in — the shape that used to load BOTH."""
    root = tmp_path / "registry"
    sha = make_registry(root, {"adam/oldname": SKILL_A, "adam/newname": SKILL_B})
    project = tmp_path / "project"
    lock_path = make_project(project, root, sha)
    full = json.loads(lock_path.read_text(encoding="utf-8"))
    home = tmp_path / "home"

    _relock(lock_path, full, {"oldname"})
    assert _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"}).returncode == 0
    assert (home / ".claude" / "skills" / "oldname" / "SKILL.md").is_file()

    _relock(lock_path, full, {"newname"})
    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "removed 1 skill no longer in the lock (oldname)" in verdict, verdict
    assert not (home / ".claude" / "skills" / "oldname").exists(), verdict
    assert (home / ".claude" / "skills" / "newname" / "SKILL.md").is_file()
    assert sorted(path.name for path in
                  (home / ".claude" / "skills").iterdir()) == [_RECORD, "newname"]


def test_a_hand_edited_skill_that_leaves_the_lock_survives_and_says_why(
        tmp_path, registry):
    """The digest already detects the edit; what it buys is ownership.

    Once a user has edited a file they have taken it over, and deleting their
    work to satisfy a lock they may not control is a worse failure than the leak
    — so it stays, and the verdict says which skill and why, distinctly from a
    clean removal.
    """
    home, project = _install_then_relock(tmp_path, registry, {"beta"})
    edited = home / ".claude" / "skills" / "alpha" / "SKILL.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\nmy own notes\n",
                      encoding="utf-8")
    mine = _tree(home / ".claude" / "skills" / "alpha")

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    # A skill that is live but not in the lock is the exact state this hook
    # exists to make knowable, so it degrades the verdict rather than riding
    # along under OK.
    assert "DEGRADED" in verdict, verdict
    assert ("1 skill no longer in the lock left in place, edited since install "
            "(alpha)") in verdict, verdict
    # The note itself, not the bare word — "could be removed this run"
    # appears in the unreadable-record clause and is not a removal.
    assert "; removed " not in verdict, verdict
    assert _tree(home / ".claude" / "skills" / "alpha") == mine, verdict

    # ...and it survives the run AFTER that one, which is where recording the
    # edited digest would show up: the comparison would then succeed and delete
    # the user's work one run late, with the notice already gone quiet.
    third = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert third.returncode == 0, third.stderr
    assert "edited since install (alpha)" in _verdict(third), _verdict(third)
    assert _tree(home / ".claude" / "skills" / "alpha") == mine, _verdict(third)


def test_the_install_loop_refuses_to_overwrite_what_it_did_not_install(
        tmp_path, registry):
    """The same ownership rule as the prune, applied on the INSTALL side.

    The install loop used to open by unconditionally `rm -rf`-ing its
    destination, before consulting anything — so a hand-placed
    ~/.claude/skills/<name> that happened to share a name with a locked skill
    was destroyed, and the verdict still read `skills: N/N … — OK`. Nothing
    said a file had been deleted, and nothing could: the removal ran before the
    only record of what this hook owns was ever read.

    That is #54's C3 shadowing hazard, closed on the side it was never closed
    on. Its blast radius today is bounded only by the accident that the hook
    does not fire in a multi-repo session (#84), and every fix for #84 removes
    exactly that bound.
    """
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)
    home = tmp_path / "home"
    # The user's own, under a name the lock also names, with NO install record —
    # nothing here is attributable to this hook.
    mine = home / ".claude" / "skills" / "alpha"
    _write(mine / "SKILL.md", "---\nname: alpha\n---\nMY OWN HAND-PLACED BODY\n")
    sentinel = _tree(mine)
    assert not (home / ".claude" / "skills" / _RECORD).exists()

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    # Byte-for-byte survival, not merely "a SKILL.md still exists there".
    assert _tree(mine) == sentinel, verdict
    # ...and the verdict SAYS so, rather than counting it quietly as installed.
    assert "shadowed — refusing to overwrite" in verdict, verdict
    assert "(alpha)" in verdict, verdict
    assert verdict.startswith("skills: 1/2 "), verdict
    assert "DEGRADED" in verdict, verdict
    # The rest of the lock is unaffected: the refusal is per-skill.
    assert (home / ".claude" / "skills" / "beta" / "SKILL.md").is_file(), verdict


def test_the_install_loop_refuses_to_overwrite_its_own_install_once_edited(
        tmp_path, registry):
    """The second half of the rule the prune already follows.

    A skill this hook DID install, which the user has since edited, is theirs
    now — the prune has always said so, and an install that silently reverted
    the edit would take the same work away through the other door.
    """
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)
    home = tmp_path / "home"
    assert _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"}).returncode == 0
    edited = home / ".claude" / "skills" / "alpha" / "SKILL.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\nmy own notes\n",
                      encoding="utf-8")
    mine = _tree(home / ".claude" / "skills" / "alpha")

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert _tree(home / ".claude" / "skills" / "alpha") == mine, verdict
    assert "shadowed — refusing to overwrite" in verdict, verdict
    assert "(alpha)" in verdict, verdict


def test_the_install_loop_still_replaces_its_own_untouched_install(tmp_path, registry):
    """Refusing to overwrite must not cost the hook its ability to UPDATE.

    An install this hook made and nobody has touched is the one thing it may
    still replace — otherwise a locked skill could never move to a new pinned
    ref, and the guard above would have traded a data-loss bug for a delivery
    that silently freezes at whatever landed first.
    """
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)
    home = tmp_path / "home"
    first = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert first.returncode == 0, first.stderr
    assert _verdict(first).endswith("— OK"), _verdict(first)

    # The same skill, new bytes, new commit, new lock — an ordinary upstream
    # update arriving over an install that is already there.
    _write(root / gsl.layout_dir(gsl.DEFAULT_LAYOUT, "adam") / "alpha" / "SKILL.md",
           SKILL_A["SKILL.md"] + "updated upstream\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "update alpha")
    updated = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert updated != sha
    make_project(project, root, updated)

    second = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert second.returncode == 0, second.stderr
    verdict = _verdict(second)
    assert verdict.endswith("— OK"), verdict
    assert "shadowed" not in verdict, verdict
    assert "updated upstream" in (
        home / ".claude" / "skills" / "alpha" / "SKILL.md"
    ).read_text(encoding="utf-8"), verdict


@pytest.mark.parametrize("only, note", [
    ("adam", "leaving 1 other-bundle skill alone (beta)"),
    ("nothingmatches", "leaving 2 other-bundle skills alone (alpha, beta)"),
])
def test_bundle_narrowing_prunes_nothing_from_other_bundles(tmp_path, only, note):
    """AGENTSKILLS_BUNDLE means "install a subset", not "this is now the truth".

    A run narrowed to one bundle has no opinion about the others, so it must not
    reap them — otherwise a debug flag silently deletes everything it was not
    pointed at, which is the third shape #71 reported (`AGENTSKILLS_BUNDLE=
    nothingmatches` left every seeded skill live under `0/0 … — OK`, saying
    nothing). What it must do instead is SAY what it left alone.
    """
    root = tmp_path / "registry"
    sha = make_registry(root, {"adam/alpha": SKILL_A, "fastmail/beta": SKILL_B})
    project = tmp_path / "project"
    project.mkdir()
    proc = run_generator("--repo", str(root), "--registry", root.resolve().as_uri(),
                         "--ref", sha, "--bundles", "adam,fastmail",
                         "-o", str(project / "skills.lock"))
    assert proc.returncode == 0, proc.stderr
    home = tmp_path / "home"
    assert _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"}).returncode == 0
    before = _tree(home / ".claude" / "skills")
    assert before, "nothing was installed, so nothing is at risk of being reaped"

    proc = _run_hook(home, project, {
        "SKILLS_BOOTSTRAP_FORCE": "1",
        "AGENTSKILLS_BUNDLE": only,
    })
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert note in verdict, verdict
    # The note itself, not the bare word — "could be removed this run"
    # appears in the unreadable-record clause and is not a removal.
    assert "; removed " not in verdict, verdict
    # Byte-identical: nothing removed, nothing rewritten, record included.
    assert _tree(home / ".claude" / "skills") == before, verdict


@pytest.mark.parametrize("corrupt", [
    "", "{ not json at all", "[]", '{"installed": {"alpha": true}}',
], ids=["empty", "truncated", "not-an-object", "installed-not-a-list"])
def test_an_unreadable_install_record_prunes_nothing(tmp_path, registry, corrupt):
    """A record it cannot read must mean "remove nothing", never "remove all".

    The record is the only thing standing between the prune and the user's own
    directory, so every way of failing to read one has to fail in the same
    direction — and say so, because a run that cannot tell whether stale skills
    are live is exactly the unreadable state this whole file argues against.
    """
    home, project = _install_then_relock(tmp_path, registry, {"beta"})
    record_path = home / ".claude" / "skills" / _RECORD
    assert record_path.is_file(), "the first run wrote no record to corrupt"
    record_path.write_text(corrupt, encoding="utf-8")

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "DEGRADED" in verdict, verdict
    assert f"could not read the install record {_hook_path(record_path)}" in verdict, verdict
    assert (home / ".claude" / "skills" / "alpha" / "SKILL.md").is_file(), verdict
    # The note itself, not the bare word — "could be removed this run"
    # appears in the unreadable-record clause and is not a removal.
    assert "; removed " not in verdict, verdict
    # The skill the lock DOES name still installed, and the record is rewritten
    # from this run — one bad file is a one-run blind spot, not a permanent one.
    assert verdict.startswith("skills: 1/1 "), verdict
    assert [entry["name"] for entry in _record(home)["installed"]] == ["beta"]


def test_the_account_sync_directory_is_never_removed(tmp_path, registry):
    """`~/.claude/skills/synced/` belongs to the claude.ai account channel.

    Defence in depth, and reached only through a record that claims the hook
    installed something called `synced` — which is precisely the case where
    obeying the record would delete a store that is not ours. So the record is
    hand-written to name it, with a digest that MATCHES the planted directory:
    every other condition for a removal is satisfied, and the name guard is the
    only thing left holding.
    """
    home, project = _install_then_relock(tmp_path, registry, {"beta"})
    synced = home / ".claude" / "skills" / "synced"
    _write(synced / "manifest.json", '{"skills": []}\n')
    _write(synced / "account-skill" / "SKILL.md",
           "---\nname: account-skill\n---\nsynced from claude.ai\n")
    account = _tree(synced)

    record_path = home / ".claude" / "skills" / _RECORD
    record = json.loads(record_path.read_text(encoding="utf-8"))
    template = record["installed"][0]
    record["installed"].append({
        "name": "synced",
        "registry": template["registry"],
        "bundle": template["bundle"],
        "digest": gsl.digest_skill_dir(synced),
    })
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "removed 1 skill no longer in the lock (alpha)" in verdict, verdict
    assert _tree(synced) == account, verdict
    assert "synced" not in verdict, verdict


# --------------------------------------------------------------------------
# `synced` is a RESERVED destination name, refused before anything deletes it
#
# The guard above is defence in depth for a hand-edited install RECORD. These
# two are the paths that needed no hand-editing at all: a skill DIRECTORY named
# `synced` in any locked bundle was enough, because the generator emitted that
# row without complaint. From the lock it reached both destructive consumers of
# `skills.nul` — the install loop (`rm -rf "$DEST/$name"` then `cp -R`) and
# `purge_locked_destinations` — and either one destroys ~/.claude/skills/synced,
# the claude.ai account-sync store, which has no delete or restore API behind it.
#
# So the lock READER refuses such a lock wholesale, upstream of both streams, and
# the GENERATOR refuses to write one — the half that matters because the reader's
# own verdict tells the operator to regenerate the lock with it.
# --------------------------------------------------------------------------

SYNCED_SKILL = {"SKILL.md": "---\nname: synced\n---\nan upstream skill named synced\n"}


def _account_store(home: Path) -> dict:
    """Plant a claude.ai account store under `home`, and return its tree."""
    synced = home / ".claude" / "skills" / "synced"
    _write(synced / "manifest.json", '{"skills": ["account-skill"]}\n')
    _write(synced / "account-skill" / "SKILL.md",
           "---\nname: account-skill\n---\nsynced from claude.ai\n")
    return _tree(synced)


def _bootstrap_log(home: Path) -> str:
    """Everything the hook wrote to its $LOG on this run.

    $LOG is `mktemp "$TMPDIR/skills-bootstrap.XXXXXX"`, kept deliberately OUTSIDE
    the run's scratch dir so it survives for the reader the verdicts send there;
    `_run_hook` points TMPDIR at the scratch HOME, so that is where it lands.
    """
    return "".join(path.read_text(encoding="utf-8", errors="replace")
                   for path in sorted((home / "tmp").glob("skills-bootstrap.*"))
                   if path.is_file())


def _project_naming_synced(project_dir: Path, root: Path, ref: str) -> Path:
    """A lock naming `adam/synced`, assembled WITHOUT the generator.

    Hand-built because the generator refuses to write one — that is the other
    half of this fix — but built from the fixture registry's own bytes, with the
    true digest of a real `synced/` skill directory. That is what makes it a
    reproduction rather than a straw man: routing, source index, `SKILL.md`
    present and digest-match are all satisfied, so the reserved-name check is the
    only thing standing between this lock and the account store.

    `ref` is a parameter because an UNREACHABLE source reaches the other
    destructive path (the purge) instead of the install loop.
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    skills_root = root / gsl.layout_dir(gsl.DEFAULT_LAYOUT, "adam")
    lock = {
        "registry": root.resolve().as_uri(),
        "ref": ref,
        "bundles": ["adam"],
        "skills": {f"adam/{name}": gsl.digest_skill_dir(skills_root / name)
                   for name in ("alpha", "synced")},
    }
    lock_path = project_dir / "skills.lock"
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return lock_path


def test_a_lock_naming_synced_is_refused_rather_than_installed(tmp_path):
    """The install loop: `rm -rf "$DEST/$name"`, then `cp -R` over the store.

    Unpatched, this exact run reported `skills: 2/2 ... — OK` — the copy
    SUCCEEDS and its digest verifies, so every counter agrees the session got
    what it asked for — while ~/.claude/skills/synced had been replaced by one
    upstream skill's files. 238 files across 18 skills, on the machine this was
    found on, with no API to put them back.

    The verdict is deliberately NOT asserted to name the key: the reader fails on
    the fixed "could not read $LOCK" literal, which carries $LEFT_IN_PLACE and is
    the one verdict that runs no purge. The offending key is in $LOG, where that
    literal sends the reader.
    """
    root = tmp_path / "registry"
    sha = make_registry(root, {"adam/alpha": SKILL_A, "adam/synced": SYNCED_SKILL})
    project = tmp_path / "project"
    _project_naming_synced(project, root, sha)
    home = tmp_path / "home"
    account = _account_store(home)

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})

    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert _tree(home / ".claude" / "skills" / "synced") == account, verdict
    assert "DEGRADED" in verdict, verdict
    assert "synced" in _bootstrap_log(home), verdict
    # Refused WHOLESALE, which is the deliberate trade: one bad upstream
    # directory name costs the consumer every skill in its lock, because the
    # cheaper answer — skip the row, like the `dup` status does — only works for
    # a name the install loop has already `rm -rf`'d, and for THIS name that
    # removal is the entire harm.
    assert not (home / ".claude" / "skills" / "alpha").exists(), verdict

    # And the same name is unwritable by the sanctioned tooling, which is what
    # keeps the verdict above honest when it says to regenerate the lock with it.
    proc = run_generator("--repo", str(root), "--registry", root.resolve().as_uri(),
                         "--ref", sha, "--bundles", "adam",
                         "-o", str(tmp_path / "regenerated.lock"))
    assert _rejected(proc), proc.stdout + proc.stderr
    assert "synced" in proc.stderr, proc.stderr
    assert not (tmp_path / "regenerated.lock").exists(), proc.stderr


def test_an_unreachable_source_does_not_purge_the_account_store(tmp_path):
    """`purge_locked_destinations`, the other consumer of `skills.nul`.

    Every source unreachable means nothing can be installed, and the purge is
    what makes "nothing to install" also mean "nothing is left installed": it
    removes every destination the LOCK NAMES. So a lock naming `synced` had it
    delete the account store on a run that installed nothing whatsoever — under
    "could not fetch ...", a verdict that never says which name it took.

    This is the path the install-loop test cannot reach: no fetch succeeds, so no
    `cp -R` is ever attempted and the removal is the only thing that happens.
    """
    root = tmp_path / "registry"
    make_registry(root, {"adam/alpha": SKILL_A, "adam/synced": SYNCED_SKILL})
    project = tmp_path / "project"
    # A ref nothing can resolve: the lock's single source is unreachable, so
    # `fetched` is 0 and the hook takes the purge-then-degrade path.
    _project_naming_synced(project, root, "0" * 40)
    home = tmp_path / "home"
    account = _account_store(home)

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})

    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert _tree(home / ".claude" / "skills" / "synced") == account, verdict
    assert "DEGRADED" in verdict, verdict
    assert "synced" in _bootstrap_log(home), verdict


def test_a_record_that_cannot_be_written_is_reported_rather_than_fatal(
        tmp_path, registry):
    """The write is the half that lets the NEXT run prune at all.

    Reached deterministically by leaving a DIRECTORY where the record file goes:
    `os.replace` onto one fails whatever the permissions say, which is what a
    read-only-$DEST test could not honestly claim — this suite runs as root in
    CI, and root ignores the mode (the same reason the `mkdir -p "$DEST"` purge
    path above is left uncovered). A record it cannot keep must degrade the
    verdict and name the path, never take the session down with it.
    """
    root, sha = registry
    project = tmp_path / "project"
    make_project(project, root, sha)
    home = tmp_path / "home"
    blocked = home / ".claude" / "skills" / _RECORD
    blocked.mkdir(parents=True)

    proc = _run_hook(home, project, {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert "DEGRADED" in verdict, verdict
    assert f"could not write the install record {_hook_path(blocked)}" in verdict, verdict
    # The session still got its skills: a record it cannot keep is a problem for
    # the NEXT run, not this one.
    assert verdict.startswith("skills: 2/2 "), verdict
    assert (home / ".claude" / "skills" / "alpha" / "SKILL.md").is_file()
    # And the staged temp file is cleaned up rather than left behind in $DEST.
    assert [path.name for path in (home / ".claude" / "skills").iterdir()
            if path.name.endswith(".tmp")] == [], verdict


def test_another_repos_lock_does_not_reap_this_ones_skills(tmp_path):
    """Two repos, one ~/.claude/skills — the reason the scope is per-source.

    Both locks claim the bundle `adam`; only the REGISTRY differs, so this
    isolates that half of the scope. Unscoped, repo two's run would find every
    name repo one installed missing from its own lock and reap the lot.

    (The residual the hook's comment states is the case this cannot separate:
    two locks naming the SAME registry AND bundle at different refs still
    contend, because neither is more authoritative than the other.)
    """
    one = tmp_path / "registry-one"
    one_sha = make_registry(one, {"adam/alpha": SKILL_A})
    two = tmp_path / "registry-two"
    two_sha = make_registry(two, {"adam/beta": SKILL_B})
    make_project(tmp_path / "project-one", one, one_sha)
    make_project(tmp_path / "project-two", two, two_sha)
    home = tmp_path / "home"

    assert _run_hook(home, tmp_path / "project-one",
                     {"SKILLS_BOOTSTRAP_FORCE": "1"}).returncode == 0
    proc = _run_hook(home, tmp_path / "project-two", {"SKILLS_BOOTSTRAP_FORCE": "1"})
    assert proc.returncode == 0, proc.stderr
    verdict = _verdict(proc)
    assert verdict.endswith("— OK"), verdict
    assert (home / ".claude" / "skills" / "alpha" / "SKILL.md").is_file(), verdict
    assert (home / ".claude" / "skills" / "beta" / "SKILL.md").is_file(), verdict
    # Both remembered, each attributed to the registry that installed it — which
    # is what lets either repo prune its own without touching the other's.
    assert {entry["name"]: entry["registry"]
            for entry in _record(home)["installed"]} == {
        "alpha": one.resolve().as_uri(),
        "beta": two.resolve().as_uri(),
    }
