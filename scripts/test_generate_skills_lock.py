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
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Tuple

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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
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
    assert lock["skills"]["adam/alpha"] == gsl.digest_skill_dir(
        root / "plugins" / "adam" / "skills" / "alpha"
    )


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
    assert lock["skills"]["cms-platform/deploy"] == gsl.digest_skill_dir(
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
    assert lock["skills"]["cms-platform/deploy"] == gsl.digest_skill_dir(
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
    primary, _, extra, extra_sha = federated
    out = tmp_path / "skills.lock"
    assert _federated_lock(out, federated).returncode == 0

    _write(extra / "skills" / "deploy" / "SKILL.md", "---\nname: deploy\n---\nedited\n")

    proc = run_generator("--repo", str(primary), "--check-current", "-o", str(out))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "cms-platform/deploy" in proc.stdout
    assert "changed" in proc.stdout
    # Named with ITS ref, not the primary's, so the message says what to re-pin.
    assert extra_sha in proc.stdout


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


def _hook_reader_accepts(lock: dict, tmp_path: Path) -> bool:
    """True iff the hook's lock reader accepts `lock` (exit 0)."""
    lock_path = tmp_path / "probe.lock"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    out = tmp_path / "reader-out"
    out.mkdir(exist_ok=True)
    proc = subprocess.run(
        [sys.executable, "-c", _extract_hook_lock_reader()],
        env={"LOCK_PATH": str(lock_path), "OUT_DIR": str(out),
             "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        capture_output=True, text=True,
    )
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
    ("skill", "pin-actions-to-sha", True),
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
    """A red check that does not say how to go green is a check people route around."""
    root, _ = registry
    out = tmp_path / "skills.lock"
    _lock_for(root, out)

    _write(root / "plugins" / "adam" / "skills" / "gamma" / "SKILL.md", "gamma\n")

    proc = run_generator("--repo", str(root), "--check-current", "-o", str(out))
    assert proc.returncode == 1
    assert "scripts/generate_skills_lock.py" in proc.stdout


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
# the bootstrap hook (bash, driven through subprocess)
# --------------------------------------------------------------------------

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
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(home / "tmp"),
        # Fixture repos are committed by `git commit` in make_registry; the
        # hook itself only reads, but keep git non-interactive regardless.
        "GIT_TERMINAL_PROMPT": "0",
    }
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(script)],
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
    assert str(project / "skills.lock") in verdict


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
    assert _looked_in(verdict) == [str((repo / "skills.lock").resolve())]


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
        str((project / "skills.lock").resolve()),
        str((repo / "skills.lock").resolve()),
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
    link.symlink_to(repo, target_is_directory=True)

    proc = _run_hook(tmp_path / "home", link,
                     {"SKILLS_BOOTSTRAP_FORCE": "1"}, script=script)
    assert proc.returncode == 0
    assert _looked_in(_verdict(proc)) == [str((repo / "skills.lock").resolve())]


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
    assert gsl.digest_skill_dir(installed) == locked


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
    """
    extra = federated[2]
    project = tmp_path / "project"
    lock_path = _federated_project(project, federated)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["sources"][0]["ref"] = "0" * 40  # cms-platform (owns deploy) unreachable
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    home = tmp_path / "home"
    _write(
        home / ".claude" / "skills" / "deploy" / "SKILL.md",
        "---\nname: deploy\n---\nATTACKER-CONTROLLED STALE BODY\n",
    )

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

    The hook launches python five times — the verdict encoder, the lock reader,
    the digest, and the two halves of the install record (the prune planner that
    READS it and the writer that rewrites it) — and every one of them puts the
    project directory on sys.path without `-I`. The planner is the one this
    alarm earns its keep on most: a `json.py` in the project directory would
    otherwise decide which of the user's directories the hook then `rm -rf`s.

    Matching is lexical and deliberately narrow: an invocation is `python3`
    followed by a flag, which is the shape all five have (`-c`, `-`) and which
    no prose mention or `command -v python3` probe takes. Comment lines are
    dropped first so the header's own `python3 -I` reference is not counted as a
    call site.
    """
    code = "\n".join(
        line for line in HOOK.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    assert re.findall(r"\bpython3\s+(-\S+)", code) == ["-I"] * 5


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
# --------------------------------------------------------------------------

_STALE = "---\nname: {name}\n---\nATTACKER-CONTROLLED STALE BODY\n"


def _seed_stale(home: Path, name: str) -> Path:
    """Plant an unverified copy where the hook installs, and return its dir."""
    _write(home / ".claude" / "skills" / name / "SKILL.md", _STALE.format(name=name))
    return home / ".claude" / "skills" / name


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
    stale = _seed_stale(home, "alpha")

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
    stale = _seed_stale(home, "alpha")

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
    stale = _seed_stale(home, "ghost")

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
    stale = _seed_stale(home, "alpha")

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
        readable socket is drained until recv() returns b"" (EOF, the client is
        gone). Nothing here waits out `within` on the happy path: the FIN
        arrives as soon as the last holder of the socket dies.
        """
        deadline = time.monotonic() + within
        pending = list(self.held)
        while pending and time.monotonic() < deadline:
            readable, _, _ = select.select(pending, [], [], 0.1)
            for connection in readable:
                if connection.recv(65536) == b"":
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
    assert f"could not read the install record {record_path}" in verdict, verdict
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
    assert f"could not write the install record {blocked}" in verdict, verdict
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
