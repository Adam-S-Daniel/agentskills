#!/usr/bin/env python3
"""sync_skills.py — prepare skill ZIPs for the sync-skills Claude skill.

Usage:
  python sync_skills.py [--prepare] [--all] [--skill NAME] [--dry-run]
                        [--verify] [--mark-synced NAME:HASH] [--repos PATH ...]
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
from typing import Dict, List, Optional

STATE_FILE = Path.home() / ".sync-skills-state.json"

DEFAULT_REPOS: List[Path] = [
    Path.home() / "repos" / "agentskills",
    Path.home() / "repos" / "agentskills-private",
]

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


def _existing_repos(repos: List[Path]) -> List[Path]:
    """Yield the repo paths that exist, warning on stderr about those that don't.

    Silently skipping an unreadable repo makes a typo'd ``--repos`` and a
    genuinely clean tree indistinguishable: both produce "nothing to do" and
    exit 0. Naming the missing path is the difference between those two.
    """
    present: List[Path] = []
    for repo in repos:
        if repo.is_dir():
            present.append(repo)
        else:
            print(f"WARNING: repo path does not exist: {repo}", file=sys.stderr)
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
) -> Dict:
    """Build the JSON payload the agent POSTs to claude.ai."""
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
                    "is_update": name in state,
                    "repo": repo.name,
                    "hash": h,
                }
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
) -> bool:
    """Compare each skill's expected upload against its account copy.

    Mirrors ``prepare()``'s skill selection: an explicit ``skill_names``
    list is used as-is, otherwise each repo's changed skills (via
    ``get_changed_skills``) are checked.

    Compares CONTENT, not just the file set: for every path, the bytes
    ``zip_skill()`` would upload are compared against the account copy's
    bytes, both CRLF-normalised. A path-only comparison passes on an
    account whose files are all present but whose contents are stale, which
    is exactly the state a failed re-upload leaves behind.

    Prints one line per skill — OK, MISMATCH (file set differs), or DRIFT
    (same files, different contents) — each annotated with the account's own
    ``updatedAt`` stamp so a verdict can be traced to a specific upload.

    Returns True only if the mirror was fresh, at least one skill was
    checked, and every checked skill matched. A skill that was selected but
    never landed on the account is a FAILURE, not a skip: it is precisely
    the upload that silently did not happen.
    """
    all_ok = True

    manifest = account_manifest()
    stale = check_mirror_freshness(manifest, now)
    if stale:
        print(f"ERROR: {stale}", file=sys.stderr)
        all_ok = False
    updated_at = manifest_updated_at(manifest) if manifest else {}

    explicit = skill_names is not None and len(skill_names) > 0
    checked = 0

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
            expected = skill_payload(skill_path)
            actual = account_skill_payload(name)

            if actual is None:
                all_ok = False
                print(
                    f"  FAIL      {name}  ({repo.name})  never landed on the "
                    f"account — no copy in {ACCOUNT_SKILLS_DIR}"
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

    if checked == 0:
        if explicit:
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
    parser.add_argument(
        "--all", action="store_true",
        help="Include all skills, not just git-changed ones",
    )
    parser.add_argument(
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
    args = parser.parse_args()

    repos = [Path(r) for r in args.repos] if args.repos else DEFAULT_REPOS

    # --mark-synced
    if args.mark_synced:
        parts = args.mark_synced.split(":", 1)
        if len(parts) != 2:
            sys.exit("ERROR: --mark-synced expects NAME:HASH")
        mark_synced(parts[0], parts[1])
        print(f"Marked {parts[0]} as synced (hash={parts[1]})")
        return

    # Resolve skill_names
    if args.skill:
        skill_names: Optional[List[str]] = [args.skill]
    elif args.all:
        skill_names = []
        for repo in repos:
            if repo.is_dir():
                skill_names.extend(get_all_skills(repo))
    else:
        skill_names = None  # auto-detect via git diff

    # --dry-run
    if args.dry_run:
        state = load_state()
        any_found = False
        for repo in repos:
            if not repo.is_dir():
                continue
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
        ok = verify(repos, skill_names)
        sys.exit(0 if ok else 1)

    # Default: --prepare / JSON output
    result = prepare(repos, skill_names)
    if not result["skills"]:
        result["message"] = (
            "No changed skills found. Use --all to sync everything."
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
