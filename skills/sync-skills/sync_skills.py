#!/usr/bin/env python3
"""sync_skills.py — prepare skill ZIPs for the sync-skills Claude skill.

Usage:
  python sync_skills.py [--prepare] [--all] [--skill NAME] [--dry-run]
                        [--mark-synced NAME:HASH] [--repos PATH ...]
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


def _extract_skill_names(diff_output: str, repo_path: Path) -> List[str]:
    """Parse git diff --name-only output into unique skill folder names."""
    seen: set = set()
    result: List[str] = []
    for line in diff_output.splitlines():
        parts = Path(line.strip()).parts
        if len(parts) >= 2 and parts[0] == "skills":
            name = parts[1]
            skill_path = repo_path / "skills" / name
            if (
                name not in seen
                and skill_path.is_dir()
                and (skill_path / "SKILL.md").exists()
            ):
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
    """Return all skill names present in the repo."""
    skills_dir = repo_path / "skills"
    if not skills_dir.is_dir():
        return []
    return sorted(
        d.name
        for d in skills_dir.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    )


# ---------------------------------------------------------------------------
# ZIP and hash
# ---------------------------------------------------------------------------

def skill_hash(skill_path: Path) -> str:
    """16-hex-char SHA-256 fingerprint of all files in a skill folder."""
    h = hashlib.sha256()
    for f in sorted(skill_path.rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(skill_path)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


_SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache", ".git", ".venv", "node_modules"})
_SKIP_EXTS = frozenset({".pyc", ".pyo", ".b64"})


def _include_in_zip(path: Path, skill_root: Path) -> bool:
    """Return True if this file should be included in the skill ZIP."""
    rel = path.relative_to(skill_root)
    for part in rel.parts:
        if part in _SKIP_DIRS:
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

    for repo in repos:
        if not repo.is_dir():
            continue

        if skill_names is not None:
            names = [
                n for n in skill_names
                if (repo / "skills" / n / "SKILL.md").exists()
            ]
        else:
            names = get_changed_skills(repo)

        for name in names:
            skill_path = repo / "skills" / name
            if not (skill_path / "SKILL.md").exists():
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

    # Default: --prepare / JSON output
    result = prepare(repos, skill_names)
    if not result["skills"]:
        result["message"] = (
            "No changed skills found. Use --all to sync everything."
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
