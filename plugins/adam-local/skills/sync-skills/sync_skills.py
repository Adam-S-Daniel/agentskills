#!/usr/bin/env python3
"""sync_skills.py — prepare skill ZIPs for the sync-skills Claude skill.

Usage:
  python sync_skills.py [--prepare] [--all] [--skill NAME] [--dry-run]
                        [--verify] [--mark-synced NAME:HASH] [--repos PATH ...]
                        [--account-list PATH]
"""

import argparse
import base64
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Set

STATE_FILE = Path.home() / ".sync-skills-state.json"

# The registry repos this script syncs from, in the order they are reported.
REPO_NAMES = ("agentskills", "agentskills-private")

# Where a clone might live. ~/repos is the historical layout; ZENDA (Adam's
# Windows box) keeps clones at D:\repos\<github-owner>\<repo> per the fleet
# AGENTS.md, so hardcoding ~/repos made every default invocation resolve
# nothing on the very machine this skill exists to run on. $AGENTSKILLS_REPOS
# (os.pathsep-separated) overrides the search for any other layout.
WINDOWS_REPO_ROOT = Path("D:/repos/adam-s-daniel")

# Local mirror of the claude.ai skill registry, refreshed by running
# ``CLAUDE_CODE_SYNC_SKILLS=1 claude -p ...`` — what --verify checks against.
ACCOUNT_SKILLS_DIR = Path.home() / ".claude" / "skills" / "synced"

# How stale the account mirror may be before --verify refuses to trust it.
#
# Why 6 hours: in the documented flow (SKILL.md §7) the refresh runs seconds
# before --verify, so any mirror belonging to the current sync is minutes old.
# The failure this guards against is the opposite extreme — the refresh was
# skipped or silently failed, so --verify compares against a snapshot taken
# before the uploads and cheerfully reports OK for an upload that never
# landed. That stale mirror is realistically hours-to-days old (a leftover
# from a previous session or a previous day). 6h is deliberately generous so
# a long interactive session never false-fails, while still catching every
# cross-session snapshot. Tightening it further trades a real safety margin
# for no extra detection.
MIRROR_MAX_AGE_SECONDS = 6 * 60 * 60

# Which skills are SUPPOSED to be on the claude.ai account store (ADR 0002).
# The file's own header carries the rule, the one-way-door warning, and the
# rulings behind each entry — read it before changing membership.
ACCOUNT_SKILLS_FILE = Path(__file__).resolve().parent / "account-skills.txt"


# ---------------------------------------------------------------------------
# Declared account-store membership (ADR 0002)
# ---------------------------------------------------------------------------

def load_account_declaration(path: Optional[Path] = None) -> Optional[Set[str]]:
    """Return the declared account-store membership, or None if unreadable.

    Format: one skill name per line, ``#`` starts a comment, blanks ignored.
    Plain text rather than YAML or JSON deliberately: this script runs from a
    laptop with nothing but the stdlib, and one-name-per-line keeps a
    membership change to a single-line diff — which is what you want when
    reviewing an addition that is close to irreversible.

    Returns None, not an empty set, when the file cannot be read. An empty
    set would silently reclassify every skill as "not supposed to be on the
    account" and turn the gate into a no-op; None lets the caller say so.
    """
    path = path or ACCOUNT_SKILLS_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    names: Set[str] = set()
    for line in text.splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            names.add(name)
    return names


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state() -> Dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: Dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(args: List[str], cwd: Path) -> Optional[str]:
    """Run git; return stripped stdout or None on non-zero exit."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception:
        return None


def _self_repo() -> Optional[Path]:
    """The repo checkout this script lives in, if it looks like one.

    Layout: ``<repo>/plugins/<plugin>/skills/sync-skills/sync_skills.py``.
    """
    root = Path(__file__).resolve().parents[4]
    if (root / "plugins").is_dir() or (root / "skills").is_dir():
        return root
    return None


def _repo_candidates(name: str) -> List[Path]:
    """Where repo ``name`` might live, most-preferred first."""
    candidates = [Path.home() / "repos" / name, WINDOWS_REPO_ROOT / name]
    self_repo = _self_repo()
    # Only claim the checkout we live in for its OWN name — otherwise
    # agentskills-private would silently resolve to the agentskills clone.
    if self_repo is not None and self_repo.name == name:
        candidates.append(self_repo)
    return candidates


def resolve_repos(explicit: Optional[List[str]] = None) -> List[Path]:
    """Resolve which repos to scan, warning on stderr about what was missed.

    Order: ``--repos`` → ``$AGENTSKILLS_REPOS`` (os.pathsep-separated) →
    ``~/repos/<name>`` → ``D:/repos/adam-s-daniel/<name>`` → the checkout
    this script lives in.

    Returns the paths that exist. An empty return means nothing resolved and
    the caller must not treat the run as a successful no-op.
    """
    requested: Optional[List[Path]] = None
    source = ""
    if explicit:
        requested = [Path(p).expanduser() for p in explicit]
        source = "--repos"
    else:
        env = os.environ.get("AGENTSKILLS_REPOS")
        if env:
            requested = [
                Path(p).expanduser() for p in env.split(os.pathsep) if p.strip()
            ]
            source = "$AGENTSKILLS_REPOS"

    if requested is not None:
        # An explicitly named path that isn't there is always worth saying.
        return _existing_repos(requested, source=source)

    resolved: List[Path] = []
    for name in REPO_NAMES:
        candidates = _repo_candidates(name)
        hit = next((c for c in candidates if c.is_dir()), None)
        if hit is not None:
            resolved.append(hit)
        else:
            tried = ", ".join(str(c) for c in candidates)
            print(
                f"WARNING: no clone of {name} found (tried: {tried}). "
                f"Set AGENTSKILLS_REPOS or pass --repos to point at it.",
                file=sys.stderr,
            )

    if not resolved:
        self_repo = _self_repo()
        if self_repo is not None:
            print(
                f"WARNING: falling back to the checkout this script lives in: "
                f"{self_repo}",
                file=sys.stderr,
            )
            resolved = [self_repo]
    return resolved


def _existing_repos(repos: List[Path], source: str = "") -> List[Path]:
    """Yield the repo paths that exist, warning on stderr about those that don't.

    Silently skipping an unreadable repo makes a typo'd ``--repos`` and a
    genuinely clean tree indistinguishable: both produce "nothing to do" and
    exit 0. Naming the missing path is the difference between those two.
    """
    label = f"{source}: " if source else ""
    present: List[Path] = []
    for repo in repos:
        if repo.is_dir():
            present.append(repo)
        else:
            print(
                f"WARNING: {label}repo path does not exist: {repo}",
                file=sys.stderr,
            )
    return present


def _skill_dir(repo_path: Path, name: str) -> Optional[Path]:
    """Locate skill ``name`` on disk, supporting both repo layouts.

    Legacy layout:  ``<repo>/skills/<name>/SKILL.md``
    Plugin layout:  ``<repo>/plugins/<plugin>/skills/<name>/SKILL.md``

    Returns the skill directory, or None if not found.
    """
    legacy = repo_path / "skills" / name
    if (legacy / "SKILL.md").exists():
        return legacy
    plugins_dir = repo_path / "plugins"
    if plugins_dir.is_dir():
        for plugin in sorted(plugins_dir.iterdir()):
            cand = plugin / "skills" / name
            if (cand / "SKILL.md").exists():
                return cand
    return None


def _extract_skill_names(diff_output: str, repo_path: Path) -> List[str]:
    """Parse git diff --name-only output into unique skill folder names.

    Recognises both layouts: ``skills/<name>/...`` (legacy) and
    ``plugins/<plugin>/skills/<name>/...`` (plugin marketplace).
    """
    seen: set = set()
    result: List[str] = []
    for line in diff_output.splitlines():
        parts = Path(line.strip()).parts
        name: Optional[str] = None
        if len(parts) >= 2 and parts[0] == "skills":
            name = parts[1]
        elif len(parts) >= 4 and parts[0] == "plugins" and parts[2] == "skills":
            name = parts[3]
        if not name or name in seen:
            continue
        if _skill_dir(repo_path, name) is not None:
            seen.add(name)
            result.append(name)
    return result


def get_changed_skills(repo_path: Path) -> List[str]:
    """Return skill names changed since last push; falls back to all skills."""
    diff = _git(["diff", "--name-only", "HEAD@{push}", "HEAD"], cwd=repo_path)
    if diff is None:
        diff = _git(["diff", "--name-only", "origin/HEAD", "HEAD"], cwd=repo_path)
    if not diff:
        return []
    return _extract_skill_names(diff, repo_path)


def get_all_skills(repo_path: Path) -> List[str]:
    """Return all skill names present in the repo (either layout)."""
    names: set = set()
    skills_dir = repo_path / "skills"
    if skills_dir.is_dir():
        for d in skills_dir.iterdir():
            if d.is_dir() and (d / "SKILL.md").exists():
                names.add(d.name)
    plugins_dir = repo_path / "plugins"
    if plugins_dir.is_dir():
        for plugin in plugins_dir.iterdir():
            sk = plugin / "skills"
            if sk.is_dir():
                for d in sk.iterdir():
                    if d.is_dir() and (d / "SKILL.md").exists():
                        names.add(d.name)
    return sorted(names)


# ---------------------------------------------------------------------------
# ZIP and hash
# ---------------------------------------------------------------------------

def skill_hash(skill_path: Path) -> str:
    """16-hex-char SHA-256 fingerprint of all files in a skill folder."""
    h = hashlib.sha256()
    for f in sorted(skill_path.rglob("*")):
        if f.is_file() and _include_in_zip(f, skill_path):
            h.update(str(f.relative_to(skill_path)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


_SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache", ".git", ".venv", "node_modules"})
# Prefixes — matches pytest-cache-files-<random>/ dirs pytest drops alongside the skill.
_SKIP_DIR_PREFIXES = ("pytest-cache-files-",)
_SKIP_EXTS = frozenset({".pyc", ".pyo", ".b64"})


def _include_in_zip(path: Path, skill_root: Path) -> bool:
    """Return True if this file should be included in the skill ZIP."""
    rel = path.relative_to(skill_root)
    for part in rel.parts:
        if part in _SKIP_DIRS:
            return False
        if any(part.startswith(p) for p in _SKIP_DIR_PREFIXES):
            return False
    if path.suffix in _SKIP_EXTS:
        return False
    return True


def zip_skill(skill_path: Path) -> bytes:
    """Return in-memory ZIP bytes; paths are relative (SKILL.md at root).

    Uses ZIP_STORED (no compression) for maximum server compatibility.
    Path separators are normalised to forward-slashes as required by the
    ZIP specification.  Build artefacts (``__pycache__``, ``*.pyc``, etc.)
    are excluded.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for f in sorted(skill_path.rglob("*")):
            if f.is_file() and _include_in_zip(f, skill_path):
                # Ensure forward slashes regardless of OS
                arcname = f.relative_to(skill_path).as_posix()
                zf.write(str(f), arcname)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Org-id hint from Chrome cookies
# ---------------------------------------------------------------------------

def get_org_id_hint() -> Optional[str]:
    """Try to read an org UUID from Chrome's sqlite cookie store."""
    localappdata = os.environ.get("LOCALAPPDATA", "")
    cookie_paths = [
        Path(localappdata) / "Google/Chrome/User Data/Default/Network/Cookies",
        Path(localappdata) / "Google/Chrome/User Data/Default/Cookies",
        Path.home() / "Library/Application Support/Google/Chrome/Default/Cookies",
        Path.home() / ".config/google-chrome/Default/Cookies",
    ]
    for cookie_path in cookie_paths:
        if not cookie_path.exists():
            continue
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            shutil.copy2(str(cookie_path), tmp_path)
            conn = sqlite3.connect(tmp_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT path FROM cookies "
                "WHERE host_key LIKE '%claude.ai%' "
                "ORDER BY last_access_utc DESC LIMIT 100"
            )
            for (path,) in cur.fetchall():
                m = re.search(r"/organizations/([0-9a-f-]{36})", path or "")
                if m:
                    conn.close()
                    return m.group(1)
            conn.close()
        except Exception:
            pass
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    return None


# ---------------------------------------------------------------------------
# Core prepare
# ---------------------------------------------------------------------------

def prepare(
    repos: List[Path],
    skill_names: Optional[List[str]] = None,
    declared: Optional[Set[str]] = None,
) -> Dict:
    """Build the JSON payload the agent POSTs to claude.ai.

    Warns on stderr about any skill in the payload that is not declared for
    the account store. The payload itself is unfiltered — the operator, not
    this script, decides what to POST — but uploading is close to a one-way
    door (no delete API), so the warning has to arrive BEFORE the upload.
    ``--verify`` catching it afterwards is too late to undo.
    """
    state = load_state()
    skills_out: List[Dict] = []

    for repo in _existing_repos(repos):
        if skill_names is not None:
            names = [n for n in skill_names if _skill_dir(repo, n) is not None]
        else:
            names = get_changed_skills(repo)

        for name in names:
            skill_path = _skill_dir(repo, name)
            if skill_path is None:
                continue
            h = skill_hash(skill_path)
            zip_bytes = zip_skill(skill_path)
            skills_out.append(
                {
                    "name": name,
                    "zip_b64": base64.b64encode(zip_bytes).decode(),
                    # The account mirror is the authority on whether a skill
                    # already exists; ~/.sync-skills-state.json only records
                    # what THIS machine uploaded. On a fresh machine the state
                    # file is absent, so every skill looked new, went up with
                    # overwrite=false, and 409'd against the copy already
                    # there. Fall back to the state file for the window
                    # between an upload and the next mirror refresh.
                    "is_update": (ACCOUNT_SKILLS_DIR / name).is_dir()
                    or name in state,
                    "repo": repo.name,
                    "hash": h,
                }
            )

    if declared is None:
        declared = load_account_declaration()
    if declared is not None:
        undeclared = sorted(
            s["name"] for s in skills_out if s["name"] not in declared
        )
        if undeclared:
            print(
                f"WARNING: not declared for the account store (ADR 0002) but "
                f"present in this payload: {', '.join(undeclared)}. Uploading "
                f"is close to a one-way door — the API has no delete. Declare "
                f"them in {ACCOUNT_SKILLS_FILE.name} or drop them from this run.",
                file=sys.stderr,
            )

    return {"skills": skills_out, "org_id_hint": get_org_id_hint()}


# ---------------------------------------------------------------------------
# Verify (account-copy drift check)
# ---------------------------------------------------------------------------

def normalise(data: bytes) -> bytes:
    """CRLF → LF. The account store's line endings are not a content change.

    Some account copies came back CRLF and some LF (it varies by upload
    batch, not by uploader version), so a raw byte compare would flag line
    endings as drift. This matches the normalisation the independent
    account-audit oracle applies, so both agree on what counts as drift.
    """
    return data.replace(b"\r\n", b"\n")


def skill_payload(skill_path: Path) -> Dict[str, bytes]:
    """Return ``{relpath: bytes}`` exactly as ``zip_skill()`` would upload it.

    Reads the members back out of the actual ZIP bytes rather than
    re-walking the directory, so the exclusion rules in ``_include_in_zip``
    can never drift out of sync with what a real upload would contain.
    """
    with zipfile.ZipFile(io.BytesIO(zip_skill(skill_path))) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def account_skill_payload(name: str) -> Optional[Dict[str, bytes]]:
    """Return ``{relpath: bytes}`` under the local account-copy mirror.

    Reads ``ACCOUNT_SKILLS_DIR / name``. Returns None if that directory
    doesn't exist (skill was never uploaded, or the mirror hasn't been
    refreshed since).
    """
    account_dir = ACCOUNT_SKILLS_DIR / name
    if not account_dir.is_dir():
        return None
    return {
        f.relative_to(account_dir).as_posix(): f.read_bytes()
        for f in account_dir.rglob("*")
        if f.is_file()
    }


def account_manifest() -> Optional[Dict]:
    """Parse the account mirror's manifest.json, or None if unreadable."""
    path = ACCOUNT_SKILLS_DIR / "manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def manifest_updated_at(manifest: Dict) -> Dict[str, str]:
    """Map skill name -> the account's own ``updatedAt`` stamp for it."""
    out: Dict[str, str] = {}
    for entry in manifest.get("skills", []) or []:
        name = entry.get("name")
        if name:
            out[name] = entry.get("updatedAt") or "unknown"
    return out


def mirror_age_seconds(
    manifest: Dict, now: Optional[datetime.datetime] = None
) -> Optional[float]:
    """Seconds since the mirror was last refreshed, or None if unknown.

    ``lastUpdated`` is epoch milliseconds.
    """
    raw = manifest.get("lastUpdated")
    if not isinstance(raw, (int, float)):
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.timestamp() - (raw / 1000.0)


def check_mirror_freshness(
    manifest: Optional[Dict], now: Optional[datetime.datetime] = None
) -> Optional[str]:
    """Return an error message if the account mirror can't be trusted.

    Returns None when the mirror is present and recent enough. ``--verify``
    is only meaningful against a mirror refreshed *after* the uploads it is
    checking; without this, skipping the refresh step turns --verify into a
    comparison against a pre-upload snapshot that reports OK for uploads
    that never landed.
    """
    refresh = (
        "refresh it with:  CLAUDE_CODE_SYNC_SKILLS=1 claude -p 'ok'"
    )
    if manifest is None:
        return (
            f"account mirror manifest not found or unreadable at "
            f"{ACCOUNT_SKILLS_DIR / 'manifest.json'} — {refresh}"
        )
    age = mirror_age_seconds(manifest, now)
    if age is None:
        return (
            f"account mirror manifest has no usable 'lastUpdated' stamp, so "
            f"its freshness cannot be established — {refresh}"
        )
    if age > MIRROR_MAX_AGE_SECONDS:
        return (
            f"account mirror is stale: last refreshed {age / 3600:.1f}h ago "
            f"(limit {MIRROR_MAX_AGE_SECONDS / 3600:.0f}h). Verifying against "
            f"it would compare uploads to a pre-upload snapshot — {refresh}"
        )
    return None


def verify(
    repos: List[Path],
    skill_names: Optional[List[str]] = None,
    now: Optional[datetime.datetime] = None,
    declared: Optional[Set[str]] = None,
    named: Optional[bool] = None,
) -> bool:
    """Compare each skill's expected upload against its account copy.

    Mirrors ``prepare()``'s skill selection: an explicit ``skill_names``
    list is used as-is, otherwise each repo's changed skills (via
    ``get_changed_skills``) are checked.

    Every verdict is read against the DECLARED account-store membership
    (``account-skills.txt``, ADR 0002) rather than inferred, because
    "selected but absent from the account" has two opposite meanings and
    nothing else could tell them apart. Absent is a missing upload for a
    declared skill and the correct resting state for an undeclared one — so
    without the declaration, ``--verify --all`` buried four real failures
    under thirteen expected ones.

    Per selected skill:

    ==================  ==========  =====================================
    declared?           on account  verdict
    ==================  ==========  =====================================
    yes                 yes, same   OK
    yes                 yes, differ DRIFT / MISMATCH
    yes                 no          FAIL — the upload never happened
    no                  yes         FAIL — uploaded without a ruling
    no                  no          not a failure; summarised in one line
    ==================  ==========  =====================================

    Naming an undeclared skill explicitly via ``--skill`` is an operator
    error, not a pass: it asks to verify something that is not supposed to
    be on the account at all.

    Compares CONTENT, not just the file set: for every path, the bytes
    ``zip_skill()`` would upload are compared against the account copy's
    bytes, both CRLF-normalised. A path-only comparison passes on an
    account whose files are all present but whose contents are stale, which
    is exactly the state a failed re-upload leaves behind. Each verdict is
    annotated with the account's own ``updatedAt`` stamp so it can be traced
    to a specific upload.

    Returns True only if the declaration was readable, the mirror was fresh,
    at least one skill was selected, and nothing failed.
    """
    all_ok = True

    if declared is None:
        declared = load_account_declaration()
    if declared is None:
        print(
            f"ERROR: the account-store membership declaration is missing or "
            f"unreadable at {ACCOUNT_SKILLS_FILE}. Without it --verify cannot "
            f"tell a missing upload from a skill that was never meant to be on "
            f"the account, so it refuses to guess.",
            file=sys.stderr,
        )
        return False

    manifest = account_manifest()
    stale = check_mirror_freshness(manifest, now)
    if stale:
        print(f"ERROR: {stale}", file=sys.stderr)
        all_ok = False
    updated_at = manifest_updated_at(manifest) if manifest else {}

    # Did the operator NAME these skills (--skill), or did the tool enumerate
    # them (--all / git-changed)? Only the first makes an undeclared skill an
    # operator error; enumerating one is just the registry being bigger than
    # the account store, which is the normal case.
    if named is None:
        named = skill_names is not None and len(skill_names) > 0
    checked = 0
    verified = 0
    undeclared_absent: List[str] = []

    for repo in _existing_repos(repos):
        if skill_names is not None:
            names = [n for n in skill_names if _skill_dir(repo, n) is not None]
        else:
            names = get_changed_skills(repo)

        for name in names:
            skill_path = _skill_dir(repo, name)
            if skill_path is None:
                continue

            checked += 1
            stamp = updated_at.get(name, "unknown")
            actual = account_skill_payload(name)

            if name not in declared:
                if actual is not None:
                    # The case #59 was still listing as a live defect:
                    # github-actions-repo-settings was uploaded on 2026-08-13
                    # with no membership ruling, and nothing surfaced it until
                    # a human happened to trim the store by hand.
                    all_ok = False
                    print(
                        f"  FAIL      {name}  ({repo.name})  ON the account but "
                        f"NOT declared in {ACCOUNT_SKILLS_FILE.name} — uploaded "
                        f"without a membership ruling (ADR 0002). Either declare "
                        f"it or delete it in the claude.ai UI; there is no delete "
                        f"API.  [account updatedAt={stamp}]"
                    )
                elif named:
                    all_ok = False
                    print(
                        f"ERROR: {name} is not declared in "
                        f"{ACCOUNT_SKILLS_FILE.name}, so it is not supposed to be "
                        f"on the account store at all (ADR 0002) and there is "
                        f"nothing to verify for it. Declare it there first if "
                        f"that is wrong.",
                        file=sys.stderr,
                    )
                else:
                    undeclared_absent.append(name)
                continue

            verified += 1
            expected = skill_payload(skill_path)

            if actual is None:
                all_ok = False
                print(
                    f"  FAIL      {name}  ({repo.name})  declared for the account "
                    f"store but NOT on it — the upload never happened (no copy in "
                    f"{ACCOUNT_SKILLS_DIR})"
                )
                continue

            missing = sorted(set(expected) - set(actual))
            extra = sorted(set(actual) - set(expected))
            if missing or extra:
                all_ok = False
                detail = []
                if missing:
                    detail.append(f"missing: {', '.join(missing)}")
                if extra:
                    detail.append(f"extra: {', '.join(extra)}")
                print(
                    f"  MISMATCH  {name}  ({repo.name})  "
                    f"{'  '.join(detail)}  [account updatedAt={stamp}]"
                )
                continue

            differing = sorted(
                path for path in sorted(expected)
                if normalise(expected[path]) != normalise(actual[path])
            )
            if differing:
                all_ok = False
                print(
                    f"  DRIFT     {name}  ({repo.name})  content differs: "
                    f"{', '.join(differing)}  [account updatedAt={stamp}]"
                )
                continue

            print(
                f"  OK        {name}  ({repo.name})  "
                f"[account updatedAt={stamp}]"
            )

    # One quiet line, not one per skill: under --all these are the majority
    # of the registry and are correctly absent, so they must stay legible
    # without drowning the verdicts above them.
    if undeclared_absent:
        print(
            f"  ({len(undeclared_absent)} not declared for the account store "
            f"and correctly absent from it: "
            f"{', '.join(sorted(undeclared_absent))})"
        )

    if checked == 0:
        if named:
            print(
                f"ERROR: no skill named {', '.join(skill_names)} found in any "
                f"resolved repo — nothing was verified (check the spelling)",
                file=sys.stderr,
            )
        else:
            print(
                "ERROR: no skills selected, so nothing was verified. A silent "
                "pass here would look identical to a successful sync. Pass "
                "--all or --skill NAME to say what should have been uploaded.",
                file=sys.stderr,
            )
        return False

    # Selection resolved, but none of it was account business. Exit 0 is the
    # honest answer — there was nothing to upload — but say so, because a
    # bare exit 0 reads identically to "every account copy checked out".
    if verified == 0 and all_ok:
        print(
            f"NOTE: none of the {checked} selected skill(s) is declared for the "
            f"account store, so no account copy was verified.",
            file=sys.stderr,
        )

    return all_ok


# ---------------------------------------------------------------------------
# State mutation
# ---------------------------------------------------------------------------

def mark_synced(name: str, hash_val: str) -> None:
    state = load_state()
    state[name] = {
        "last_synced_hash": hash_val,
        "synced_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    save_state(state)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare or inspect skill ZIPs for upload to claude.ai"
    )
    parser.add_argument(
        "--prepare", action="store_true", default=True,
        help="Output JSON payload (default behaviour)",
    )
    # --skill used to silently win over --all, so `--all --skill x` synced
    # only x while reading as "sync everything".
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--all", action="store_true",
        help="Include all skills, not just git-changed ones",
    )
    selection.add_argument(
        "--skill", metavar="NAME",
        help="Target a single skill by name",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List what would be synced without building ZIPs",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Compare account copies (~/.claude/skills/synced) against expected ZIP contents",
    )
    parser.add_argument(
        "--mark-synced", metavar="NAME:HASH",
        help="Record a skill as successfully synced (e.g. fastmail:a1b2c3d4)",
    )
    parser.add_argument(
        "--repos", nargs="+", metavar="PATH",
        help="Repo paths to scan (overrides built-in defaults)",
    )
    parser.add_argument(
        "--account-list", metavar="PATH",
        help=(
            "Declared account-store membership list to read "
            f"(default: {ACCOUNT_SKILLS_FILE.name} beside this script). Use it "
            "to dry-run a proposed membership change before committing one."
        ),
    )
    args = parser.parse_args()

    # --mark-synced touches only the state file, so it needs no repo.
    if args.mark_synced:
        parts = args.mark_synced.split(":", 1)
        if len(parts) != 2:
            sys.exit("ERROR: --mark-synced expects NAME:HASH")
        mark_synced(parts[0], parts[1])
        print(f"Marked {parts[0]} as synced (hash={parts[1]})")
        return

    repos = resolve_repos(args.repos)
    if not repos:
        sys.exit(
            "ERROR: no repo could be resolved, so nothing was inspected. "
            "This is not the same as having nothing to sync."
        )

    # An explicitly named list that isn't readable is a typo, not a licence
    # to fall back to the built-in one.
    declared = load_account_declaration(
        Path(args.account_list).expanduser() if args.account_list else None
    )
    if args.account_list and declared is None:
        sys.exit(
            f"ERROR: --account-list path is not readable: {args.account_list}"
        )

    # Resolve skill_names
    if args.skill:
        skill_names: Optional[List[str]] = [args.skill]
    elif args.all:
        skill_names = []
        for repo in repos:
            skill_names.extend(get_all_skills(repo))
    else:
        skill_names = None  # auto-detect via git diff

    # --dry-run
    if args.dry_run:
        state = load_state()
        any_found = False
        for repo in repos:
            names = (
                skill_names
                if skill_names is not None
                else get_changed_skills(repo)
            )
            for name in names:
                tag = "UPDATE" if name in state else "NEW   "
                print(f"  {tag}  {name}  ({repo.name})")
                any_found = True
        if not any_found:
            print("No changed skills found. Use --all to sync everything.")
        return

    # --verify
    if args.verify:
        # named=True only for --skill: --all enumerates the registry, and most
        # of the registry is legitimately not on the account.
        ok = verify(
            repos, skill_names, declared=declared, named=bool(args.skill)
        )
        sys.exit(0 if ok else 1)

    # Default: --prepare / JSON output
    result = prepare(repos, skill_names, declared=declared)
    if not result["skills"]:
        result["message"] = (
            "No changed skills found. Use --all to sync everything."
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
