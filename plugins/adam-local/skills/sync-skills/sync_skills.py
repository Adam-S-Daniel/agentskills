#!/usr/bin/env python3
"""sync_skills.py — prepare skill ZIPs for the sync-skills Claude skill.

Usage:
  python sync_skills.py [--prepare] [--all] [--skill NAME] [--dry-run]
                        [--verify] [--mark-synced NAME:HASH] [--repos PATH ...]
                        [--account-list PATH] [--yes]
                        [--report-issue [--report-repo OWNER/NAME]]

Repos are located by --repos, then $AGENTSKILLS_REPOS, then the checkout this
file lives in (for its own name only). There are no built-in clone paths.
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
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

STATE_FILE = Path.home() / ".sync-skills-state.json"

# The registry repos a run is EXPECTED to cover, in the order they are
# reported. This list resolves NOTHING - resolution is --repos, then
# $AGENTSKILLS_REPOS, then this script's own checkout, and nothing else.
# Its only remaining job is to notice that one of these names was never
# resolved and say so, because a repo that went unexamined must never read
# like a repo that was checked and found clean. Adding a name here buys the
# warning, not the lookup: a new registry still has to be named to be synced.
EXPECTED_REPO_NAMES = ("agentskills", "agentskills-private")

# There are deliberately NO built-in clone locations. Guessing one (~/repos,
# D:\repos\<owner>) let anything sitting at the guessed path — an empty
# folder, a stale partial clone, a junction — outrank the checkout this
# script is demonstrably running from. That is not hypothetical: a Windows
# `--verify --all` resolved an empty ~/repos/agentskills, enumerated zero
# skills from it, and then reported that --all had not been passed. A repo is
# either NAMED (--repos, $AGENTSKILLS_REPOS) or is the checkout this file
# lives in; a path that merely exists is not evidence of anything.
#
# The cost is deliberate: agentskills-private can no longer be found
# implicitly. resolve_repos() says so on stderr rather than going quiet,
# because a repo that went unexamined and a repo with nothing to sync must
# never produce the same output.

# What to tell an operator who now has to name a path. Windows first — that
# is the machine the old defaults were failing on.
REPO_HINT = (
    "Name it: --repos D:\\repos\\adam-s-daniel\\agentskills  (PowerShell: "
    "$env:AGENTSKILLS_REPOS = 'D:\\repos\\adam-s-daniel\\agentskills'), or on "
    "Linux/WSL --repos /path/to/agentskills  (AGENTSKILLS_REPOS=/path/to/"
    "agentskills). Separate several paths with the OS path separator."
)

# The branch a clone must be on before this script will sync from it, and how
# long to wait on the fetch that answers "is it up to date?".
MAIN_BRANCH = "main"
FETCH_TIMEOUT_SECONDS = 20

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

def _git(
    args: List[str], cwd: Path, timeout: Optional[float] = None
) -> Optional[str]:
    """Run git; return stripped stdout, or None on non-zero exit or timeout.

    ``timeout`` is for the one call that touches the network (``fetch``): a
    hung remote must not hang the whole sync, and a timeout has to be
    reported as "could not determine", never folded into a verdict.
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
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
    """Where repo ``name`` might live, most-preferred first.

    Exactly one candidate is ever possible: the checkout this script lives
    in, and only for its OWN name. That is derived from ``__file__``, so it
    is a fact about this run rather than a guess about the machine —
    otherwise agentskills-private would silently resolve to the agentskills
    clone. Everything else must be named; see the note on built-in defaults
    at the top of this file for what guessing cost.
    """
    self_repo = _self_repo()
    if self_repo is not None and self_repo.name == name:
        return [self_repo]
    return []


def resolve_repos(explicit: Optional[List[str]] = None) -> List[Path]:
    """Resolve which repos to scan, warning on stderr about what was missed.

    Order, and nothing else: ``--repos`` → ``$AGENTSKILLS_REPOS``
    (os.pathsep-separated) → the checkout this script lives in, claimed only
    for its own repo name. There is no ``~/repos`` or ``D:/repos`` guess any
    more — see the note on built-in defaults at the top of this file.

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
    for name in EXPECTED_REPO_NAMES:
        candidates = _repo_candidates(name)
        hit = next((c for c in candidates if c.is_dir()), None)
        if hit is not None:
            resolved.append(hit)
        else:
            # Not fatal on its own — this run may not need that repo — but it
            # does mean part of the registry went unexamined, and that must
            # never read the same as "checked it, nothing to do".
            print(
                f"WARNING: no clone of {name} is known to this run, so NONE of "
                f"its skills were examined. This is not the same as finding it "
                f"clean. {REPO_HINT}",
                file=sys.stderr,
            )
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


def describe_resolved_repos(repos: List[Path]) -> str:
    """One line per resolved repo: its path, and how many skills it holds.

    Every "nothing happened" message ends with this. The Windows report that
    prompted it said only "no skills selected"; what the operator actually
    needed was which directory the tool had decided to call the registry, and
    that it had found zero skills there.
    """
    if not repos:
        return f"  (none — no repo was resolved)\n{REPO_HINT}"
    return "\n".join(
        f"  {repo}  ({len(get_all_skills(repo))} skill(s) found)"
        for repo in repos
    )


# ---------------------------------------------------------------------------
# Repo-state gate
# ---------------------------------------------------------------------------

def _stdin_is_tty() -> bool:
    """True when there is a human to ask. Never raises."""
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except Exception:
        return False


def repo_state(
    repo: Path, timeout: float = FETCH_TIMEOUT_SECONDS
) -> Dict[str, List[str]]:
    """Describe one repo's branch/upstream state for the pre-flight gate.

    Returns ``{"problems": [...], "unknowns": [...]}``. The split is the
    whole point: a *problem* is something established to be wrong (off
    ``main``, ahead of or behind ``origin/main``) and blocks; an *unknown* is
    a question this run could not answer (no remote, offline, fetch timed
    out) and is reported without being turned into a verdict either way.
    Asserting "up to date" from stale remote-tracking refs would be the same
    class of lie as the resolution defaults this gate ships alongside.

    A path that is not a git checkout at all is an unknown, not a problem:
    it has no branch to be wrong and no upstream to be behind, and blocking
    there would break every legitimate ``--repos /some/exported/tree`` run.
    """
    problems: List[str] = []
    unknowns: List[str] = []

    if _git(["rev-parse", "--is-inside-work-tree"], cwd=repo) != "true":
        unknowns.append(
            f"{repo}: not a git checkout — branch and up-to-date state unknown"
        )
        return {"problems": problems, "unknowns": unknowns}

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    if branch is None:
        unknowns.append(f"{repo}: could not determine the current branch")
    elif branch != MAIN_BRANCH:
        # Compared literally, so a repo whose default branch is called
        # something else is surfaced rather than silently mishandled.
        problems.append(
            f"{repo}: on branch '{branch}', not '{MAIN_BRANCH}'"
        )

    # Answer "is it up to date?" from a fresh fetch or not at all.
    fetched = _git(
        [
            "fetch",
            "--quiet",
            "origin",
            f"+refs/heads/{MAIN_BRANCH}:refs/remotes/origin/{MAIN_BRANCH}",
        ],
        cwd=repo,
        timeout=timeout,
    )
    if fetched is None:
        unknowns.append(
            f"{repo}: could not fetch origin/{MAIN_BRANCH} (no such remote, "
            f"offline, or slower than {timeout:.0f}s) — whether it is up to "
            f"date could not be determined"
        )
        return {"problems": problems, "unknowns": unknowns}

    counts = _git(
        ["rev-list", "--left-right", "--count", f"origin/{MAIN_BRANCH}...HEAD"],
        cwd=repo,
    )
    parts = counts.split() if counts else []
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        unknowns.append(
            f"{repo}: could not compare HEAD against origin/{MAIN_BRANCH}"
        )
    else:
        behind, ahead = int(parts[0]), int(parts[1])
        if behind or ahead:
            problems.append(
                f"{repo}: not up to date with origin/{MAIN_BRANCH} "
                f"({behind} behind, {ahead} ahead)"
            )
    return {"problems": problems, "unknowns": unknowns}


def check_repo_state(
    repos: List[Path],
    assume_yes: bool = False,
    timeout: float = FETCH_TIMEOUT_SECONDS,
) -> bool:
    """Pre-flight gate. True to proceed; never False without saying why.

    What gets uploaded is built from the working tree, so an off-main or
    behind clone silently publishes the wrong bytes to an API that has no
    delete. Ask first.
    """
    problems: List[str] = []
    unknowns: List[str] = []
    for repo in repos:
        state = repo_state(repo, timeout=timeout)
        problems.extend(state["problems"])
        unknowns.extend(state["unknowns"])

    for line in unknowns:
        print(f"NOTE: repo state could not be determined — {line}", file=sys.stderr)

    if not problems:
        return True

    header = "repo state is not what a sync should be run from:\n" + "\n".join(
        f"  - {problem}" for problem in problems
    )

    if assume_yes:
        # A silent override is how a gate rots into decoration.
        print(
            f"WARNING: {header}\n--yes bypassed the checks above. What gets "
            f"uploaded will be built from these trees exactly as they stand, "
            f"not from origin/{MAIN_BRANCH}.",
            file=sys.stderr,
        )
        return True

    def refuse_nobody_to_ask() -> None:
        print(
            f"ERROR: {header}\nStdin is not a terminal, so there is nobody to "
            f"ask — refusing rather than hanging on a prompt nobody will see. "
            f"Bring the tree(s) to {MAIN_BRANCH} and up to date, or re-run "
            f"with --yes to sync from them as they are.",
            file=sys.stderr,
        )

    if not _stdin_is_tty():
        refuse_nobody_to_ask()
        return False

    print(f"WARNING: {header}", file=sys.stderr)
    try:
        answer = input("Continue anyway? [y/N] ")
    except EOFError:
        # isatty() claimed a terminal and the very first read hit end of
        # input, so there was nobody there after all. Windows reports the
        # NUL device as a character device, which makes isatty() true under
        # `stdin=DEVNULL` — the way an agent drives this script, and the
        # exact case the non-TTY branch above exists for. Same situation,
        # so it gets the same answer instead of the vaguer "not confirmed",
        # which named neither the cause nor --yes as the way past it.
        refuse_nobody_to_ask()
        return False
    if not answer.strip().lower().startswith("y"):
        print(
            "ERROR: aborted — repo state was not confirmed.", file=sys.stderr
        )
        return False
    return True


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

def org_id_from_cli_config() -> Optional[str]:
    """Read the org UUID the Claude Code CLI itself is authenticated against.

    This is the authoritative answer to "which org do I upload to", and it
    is worth reading before the cookie store because it is *specific*: the
    account can belong to several orgs, ``/api/organizations`` lists all of
    them with no marker for which one owns the skill store, and picking the
    wrong one 404s every request. The mirror under
    ``~/.claude/skills/synced`` is produced by this CLI, so the org named
    here is by construction the org whose store ``--verify`` reads.

    Deliberately resolved at runtime rather than hardcoded in the skill: the
    UUID is an account identifier and this repo is public.
    """
    try:
        with open(Path.home() / ".claude.json", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    org = (data.get("oauthAccount") or {}).get("organizationUuid")
    if isinstance(org, str) and re.fullmatch(r"[0-9a-f-]{36}", org):
        return org
    return None


def get_org_id_hint() -> Optional[str]:
    """Best-effort org UUID: CLI config first, then Chrome's cookie store.

    The cookie scrape was the only source for a long time and it routinely
    returns None -- the org UUID only appears in a cookie *path*, which most
    claude.ai cookies do not carry -- which left the agent to guess between
    orgs. ``org_id_from_cli_config`` is tried first because it is exact.
    """
    from_cli = org_id_from_cli_config()
    if from_cli:
        return from_cli
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

def is_update(name: str, state: Optional[Dict] = None) -> bool:
    """Does the account already hold ``name``, so the upload must overwrite?

    The account mirror is the authority. ``~/.sync-skills-state.json`` only
    records what THIS machine uploaded, so on a fresh machine every skill
    looked new, went up with ``overwrite=false`` and 409'd against the copy
    already on the account; the state file is the fallback for the window
    between an upload and the next mirror refresh.

    ``--dry-run`` and ``--prepare`` MUST answer this the same way, which is
    why it lives in one place. They used to disagree: the preview tagged
    UPDATE/NEW from the state file alone, so on a fresh machine it previewed
    NEW for skills the real run then correctly uploaded as updates.
    """
    if state is None:
        state = load_state()
    return (ACCOUNT_SKILLS_DIR / name).is_dir() or name in state


def prepare(
    repos: List[Path],
    skill_names: Optional[List[str]] = None,
    declared: Optional[Set[str]] = None,
    zip_dir: Optional[Path] = None,
) -> Dict:
    """Build the JSON payload the agent POSTs to claude.ai.

    With ``zip_dir`` set, each ZIP is written there as a real file and the
    entry carries ``zip_path``/``zip_bytes``/``zip_sha256`` **instead of**
    ``zip_b64``. That is the mode the documented upload path uses (SKILL.md
    §3): the browser reads the file directly, so the payload never has to
    travel through the agent's context as base64. Dropping ``zip_b64`` is
    the point, not an oversight -- a single skill can be 200KB+ of base64,
    and emitting both would keep the cost this mode exists to avoid.

    Warns on stderr about any skill in the payload that is not declared for
    the account store. The payload itself is unfiltered — the operator, not
    this script, decides what to POST — but uploading is close to a one-way
    door (no delete API), so the warning has to arrive BEFORE the upload.
    ``--verify`` catching it afterwards is too late to undo.
    """
    state = load_state()
    skills_out: List[Dict] = []

    # One resolution pass, handed down: main resolves (resolve_repos) and
    # gates (check_repo_state) the SAME list iterated here. Re-filtering
    # internally meant the gated list and the used list were computed twice
    # and could in principle differ - a repo gated then dropped, or the
    # reverse. Callers pass repos that already exist.
    for repo in repos:
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
            entry = {
                "name": name,
                "is_update": is_update(name, state),
                "repo": repo.name,
                "hash": h,
            }
            if zip_dir is not None:
                zip_dir.mkdir(parents=True, exist_ok=True)
                zip_path = zip_dir / f"{name}.zip"
                zip_path.write_bytes(zip_bytes)
                entry["zip_path"] = str(zip_path)
                entry["zip_bytes"] = len(zip_bytes)
                entry["zip_sha256"] = hashlib.sha256(zip_bytes).hexdigest()
            else:
                entry["zip_b64"] = base64.b64encode(zip_bytes).decode()
            skills_out.append(entry)

    # is_update() above sets each upload's overwrite flag, and it reads the
    # account mirror. --verify refuses a mirror older than MIRROR_MAX_AGE
    # rather than silently trust it; prepare trusted one of any age, so a
    # week-old mirror could drive that flag with nothing said out loud.
    if skills_out:
        stale = check_mirror_freshness(account_manifest())
        if stale:
            print(
                f"WARNING: the overwrite flag in this payload was decided "
                f"against an account mirror that cannot be trusted - {stale}."
                f" A skill already on the account can look new here and 409 "
                f"on upload; retry that one with overwrite=true.",
                file=sys.stderr,
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
    selection: Optional[str] = None,
    verified_out: Optional[Set[str]] = None,
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

    ``selection`` records HOW the caller chose ("skill" / "all" /
    "changed"), which is the only way to say something useful when nothing
    gets checked. An empty ``skill_names`` list is ambiguous on its own —
    under ``--all`` it means the resolved repos held no skills (a resolution
    problem), and from a direct caller it means nothing was selected (a flag
    problem) — and reporting the second when the first happened is precisely
    the misdiagnosis this parameter exists to end. It defaults to the older
    inference so existing callers keep their behaviour.

    ``verified_out``, if given, is filled with the declared names whose
    account copy this run actually COMPARED. The bool return cannot carry
    that: True means "nothing I looked at failed", and a run that looked at
    no account copy at all returns True while saying so on stderr. That
    distinction is invisible to a caller holding only the bool, and
    ``account_upload_gap`` needs it — "no declared skill is absent" plus a
    green verify still is not "the account is correct" if no declared skill
    was ever opened. An out-parameter rather than a changed return type
    because the bool IS the exit code and every caller and test reads it.

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
    if selection is None:
        selection = "skill" if named else "changed"
    checked = 0
    verified = 0
    undeclared_absent: List[str] = []

    # One resolution pass, handed down: main resolves (resolve_repos) and
    # gates (check_repo_state) the SAME list iterated here. Re-filtering
    # internally meant the gated list and the used list were computed twice
    # and could in principle differ - a repo gated then dropped, or the
    # reverse. Callers pass repos that already exist.
    for repo in repos:
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
            if verified_out is not None:
                verified_out.add(name)
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
        where = describe_resolved_repos(repos)
        if named:
            print(
                f"ERROR: no skill named {', '.join(skill_names)} found in any "
                f"resolved repo — nothing was verified (check the spelling). "
                f"Resolved repos:\n{where}",
                file=sys.stderr,
            )
        elif selection == "all":
            # The Windows failure: --all WAS passed, and the repo it got
            # pointed at simply held nothing. Blaming the flag sent three
            # round-trips looking for a flag that was already there.
            print(
                f"ERROR: skills were selected (--all), but no resolved repo "
                f"contains any, so nothing was verified. Nothing is wrong with "
                f"the flags — this is repo resolution. Resolved repos:\n{where}"
                f"\nIf that is not the tree you meant: {REPO_HINT}",
                file=sys.stderr,
            )
        else:
            print(
                f"ERROR: no skills selected, so nothing was verified. A silent "
                f"pass here would look identical to a successful sync. Pass "
                f"--all or --skill NAME to say what should have been uploaded. "
                f"Resolved repos:\n{where}",
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
# Recorded account state — the only half of the account arm CI can see
# ---------------------------------------------------------------------------
#
# WHY A RECORDED STATE EXISTS AT ALL
# `--verify` answers "does the account copy match the registry?" by reading
# ~/.claude/skills/synced, which exists ONLY in a session signed in to the
# account (ADR 0002: "check_skills.py reads filesystem registries and cannot
# see claude.ai"). A GitHub runner has no such directory, so on CI that
# question is unanswerable, not merely unanswered.
#
# What a runner CAN answer is the next question down: "has the registry moved
# since anyone last LOOKED at the account?" That needs the observation to be
# committed, which is what account-state.json is — a digest per declared
# skill, taken from the mirror by `--record-account-state` on a machine that
# has one, and read by `--account-drift` anywhere.
#
# THE HONEST LIMIT, STATED SO NOBODY READS MORE INTO IT
# A `stale` verdict means "the registry changed after the last recording",
# which is evidence of a needed upload, not proof of one. An `in-sync`
# verdict means "nothing changed since the recording" — it says nothing
# about an upload made after it, and nothing about the account at all if the
# recording is old. Re-record after uploading, or the same skill keeps being
# offered. `recorded_at` is in the report for exactly that reason: an old
# stamp devalues every verdict computed from it.
#
# WHY THE DIGEST AND NOT A TIMESTAMP
# E5 measured the timestamp heuristic and it does not work: comparing the
# account's `updatedAt` against `git log` flagged 3 of 10 skills and 2 were
# false positives (`pdf-ocr-audit`, `wj-next-break` — PR #62 moved them into
# git without changing a byte). Content is the only signal that discriminates,
# so this digests the same CRLF-normalised member set `--verify` compares.

def _registry_root() -> Path:
    """Nearest ancestor that is a plugin marketplace, else this skill's dir.

    The recording lives at the REGISTRY ROOT, beside skills.lock, and not in
    this skill's own directory - both are recorded state about a delivery
    channel, and a skill directory is the one place this file cannot go.
    `zip_skill()` uploads a skill's whole directory, so a recording kept
    inside sync-skills would ship inside sync-skills: every `--record-account-
    state` would change the very skill it had just recorded, making it stale
    the instant it was recorded and re-stale after every upload. Measured, not
    theorised - the first cut of this file did exactly that.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".claude-plugin" / "marketplace.json").is_file():
            return parent
    return here.parent


ACCOUNT_STATE_FILE = _registry_root() / "account-state.json"


def payload_digest(payload: Dict[str, bytes]) -> str:
    """SHA-256 over a CRLF-normalised member set.

    The unit of comparison is the same one ``--verify`` uses — the members
    ``zip_skill()`` would upload, CRLF-normalised — so the two arms cannot
    disagree about what counts as drift.

    Each member contributes ``relpath\0len\0bytes``. The length delimiter is
    what stops a rename from colliding with a content change: without it,
    moving a byte from the end of a path into the start of its body leaves
    the concatenation identical.
    """
    h = hashlib.sha256()
    for rel in sorted(payload):
        body = normalise(payload[rel])
        h.update(f"{rel}\0{len(body)}\0".encode("utf-8"))
        h.update(body)
    # Labelled `sha256:<hex>` exactly as skills.lock labels its digests: it
    # says which algorithm produced the value, and it keeps a bare 64-hex
    # string from sitting next to a key in a committed, scanned artifact.
    # See AGENTS.md, "A name you choose becomes data a scanner reads".
    return f"sha256:{h.hexdigest()}"


def load_account_state(path: Optional[Path] = None) -> Optional[Dict]:
    """Parse the recorded account state, or None if missing/unreadable.

    None is "I have no record", which `--account-drift` reports as every
    declared skill being `unrecorded`. It is never treated as "in sync":
    an absent record is the least informed state, not the healthiest.
    """
    path = path or ACCOUNT_STATE_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def build_account_state(
    declared: Optional[Set[str]] = None,
    now: Optional[datetime.datetime] = None,
) -> Dict:
    """Record what the local account mirror holds, for every declared skill.

    Digests the ACCOUNT copy (not the registry copy): the recording is an
    observation of the store, so a later registry change is what shows up as
    drift. A declared skill the mirror does not hold is recorded with a null
    digest rather than omitted — "observed absent" and "never looked at" are
    different states and the report distinguishes them.
    """
    if declared is None:
        declared = load_account_declaration() or set()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    manifest = account_manifest() or {}
    updated_at = manifest_updated_at(manifest)
    skills: Dict[str, Dict] = {}
    for name in sorted(declared):
        payload = account_skill_payload(name)
        skills[name] = {
            # `observed` is the strong basis: this ran against a real mirror and
            # hashed what the account actually held. `--assert-uploaded` writes
            # `asserted` instead, and a full recording overwrites it here -
            # an observation always supersedes an assertion, never the reverse.
            "basis": "observed",
            "digest": payload_digest(payload) if payload is not None else None,
            "updatedAt": updated_at.get(name),
            "files": len(payload) if payload is not None else 0,
        }
    return {
        "_comment": (
            "Digest of each declared skill AS THE CLAUDE.AI ACCOUNT STORE HELD IT "
            "when this was recorded. Written by `sync_skills.py "
            "--record-account-state` from a session that has "
            "~/.claude/skills/synced; read by `--account-drift`, which CI can run "
            "and which cannot see the account itself. Re-record after uploading."
        ),
        "recorded_at": now.replace(microsecond=0).isoformat(),
        "skills": skills,
    }


def assert_uploaded(
    name: str,
    repos: List[Path],
    ref: Optional[str] = None,
    run_id: Optional[str] = None,
    state_path: Optional[Path] = None,
    declared: Optional[Set[str]] = None,
    now: Optional[datetime.datetime] = None,
) -> Tuple[bool, str]:
    """Record ``name`` as uploaded, on the operator's word rather than a mirror.

    Returns ``(changed, message)``. Raises ValueError on anything it refuses.

    THIS WRITES A CLAIM, NOT A MEASUREMENT, and the file says so: the entry is
    stamped ``basis: asserted`` with the ref and run it came from, so a reader
    can tell it apart from a `--record-account-state` row that actually opened
    the account store. The distinction is the whole point - an assertion that
    the upload landed is exactly as good as the operator's certainty, and if a
    truncated upload gets asserted, nothing downstream can ever contradict it
    (CI has no mirror; that is why this arm exists at all).

    The digest is taken from ``repos`` - which the caller points at the tree the
    UPLOADED artifact was built from, not necessarily the current one. Recording
    the current tree's digest for an upload made from an older one is the
    specific error this signature exists to prevent.

    ``recorded_at`` at the top of the file is deliberately NOT bumped: it stamps
    when the STORE was last observed, and asserting does not observe it. Bumping
    it would make a stale file look freshly verified, which inverts its meaning.
    """
    if declared is None:
        declared = load_account_declaration() or set()
    if name not in declared:
        raise ValueError(
            f"{name!r} is not declared in {ACCOUNT_SKILLS_FILE.name}, so it is "
            f"not supposed to be on the account store at all (ADR 0002). "
            f"Declare it there first, deliberately - adding a name is close to "
            f"a one-way door."
        )
    path = None
    for repo in repos:
        path = _skill_dir(repo, name)
        if path is not None:
            break
    if path is None:
        raise ValueError(
            f"{name!r} is declared but no tree in {[str(r) for r in repos]} "
            f"holds it, so there is nothing to digest."
        )
    digest = payload_digest(skill_payload(path))

    state_path = state_path or ACCOUNT_STATE_FILE
    state = load_account_state(state_path)
    if state is None:
        raise ValueError(
            f"no recording at {state_path} to amend. Seed one with "
            f"--record-account-state from a session that has a mirror: "
            f"asserting into an absent file would produce a state whose every "
            f"other skill silently reads as never-recorded."
        )
    entry = dict((state.get("skills") or {}).get(name) or {})
    if entry.get("digest") == digest:
        return False, (
            f"{name} already records {digest} - nothing to change. Either the "
            f"upload was recorded already, or the tree you uploaded from has "
            f"not moved since."
        )
    now = now or datetime.datetime.now(datetime.timezone.utc)
    previous = entry.get("digest")
    entry.update({
        "basis": "asserted",
        "digest": digest,
        # An assertion cannot know the account's own stamp - only the mirror
        # carries that - and keeping the old one would attribute this content
        # to the previous upload's timestamp.
        "updatedAt": None,
        "files": len(skill_payload(path)),
        "asserted_at": now.replace(microsecond=0).isoformat(),
        "asserted_from": {"ref": ref, "run_id": run_id},
    })
    state.setdefault("skills", {})[name] = entry
    state_path.write_text(
        json.dumps(state, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return True, (
        f"{name}: recorded {digest} as uploaded (was {previous or 'unrecorded'}), "
        f"basis=asserted, ref={ref or 'unspecified'}, run={run_id or 'unspecified'}"
    )


def account_drift(
    repos: List[Path],
    declared: Optional[Set[str]] = None,
    state: Optional[Dict] = None,
) -> List[Dict]:
    """Compare each declared skill's registry payload against the recording.

    Per-skill status:

    ``stale``                 registry digest differs from the recorded one
    ``in-sync``               digests match
    ``unrecorded``            declared, but the recording does not cover it
    ``never-uploaded``        recorded as absent from the account store
    ``missing-from-registry`` declared, but no repo holds the skill

    Both ``stale`` and ``unrecorded`` and ``never-uploaded`` are "offer this
    one"; only ``in-sync`` is "leave it alone". ``missing-from-registry`` is a
    declaration bug and is reported rather than skipped.
    """
    if declared is None:
        declared = load_account_declaration() or set()
    recorded = (state or {}).get("skills") or {}
    rows: List[Dict] = []
    for name in sorted(declared):
        path = None
        for repo in repos:
            path = _skill_dir(repo, name)
            if path is not None:
                break
        row: Dict = {"name": name, "path": str(path) if path else None}
        if path is None:
            row.update(status="missing-from-registry", registry_digest=None,
                       recorded_digest=None)
            rows.append(row)
            continue
        reg = payload_digest(skill_payload(path))
        row["registry_digest"] = reg
        if name not in recorded:
            row.update(status="unrecorded", recorded_digest=None)
        else:
            rec = (recorded[name] or {}).get("digest")
            row["recorded_digest"] = rec
            row["recorded_updatedAt"] = (recorded[name] or {}).get("updatedAt")
            # WHAT the recording rests on travels with the verdict, always.
            # An `in-sync` from `--assert-uploaded` is the operator's word;
            # one from `--record-account-state` was measured against the
            # account mirror. Dropping this here made the two byte-identical
            # in every report while SKILL.md called the distinction the point
            # - a caveat that lives only in a docstring is not a caveat.
            row["recorded_basis"] = (recorded[name] or {}).get("basis")
            if rec is None:
                row["status"] = "never-uploaded"
            elif rec == reg:
                row["status"] = "in-sync"
            else:
                row["status"] = "stale"
        rows.append(row)
    return rows


NEEDS_UPLOAD = ("stale", "unrecorded", "never-uploaded")


def account_drift_report(rows: List[Dict], state: Optional[Dict]) -> Dict:
    """Shape ``account_drift`` rows for a machine consumer (the workflow)."""
    return {
        "recorded_at": (state or {}).get("recorded_at"),
        "needs_upload": [r["name"] for r in rows if r["status"] in NEEDS_UPLOAD],
        "in_sync": [r["name"] for r in rows if r["status"] == "in-sync"],
        # A caller that only reads `in_sync` gets the safe reading; one that
        # cares whether anybody actually looked reads this. Both are present
        # so neither has to know to ask.
        "in_sync_asserted": [
            r["name"] for r in rows
            if r["status"] == "in-sync" and r.get("recorded_basis") == "asserted"
        ],
        "missing_from_registry": [
            r["name"] for r in rows if r["status"] == "missing-from-registry"
        ],
        "skills": rows,
    }


# ---------------------------------------------------------------------------
# Account-store upload tracking issue (--report-issue)
#
# THE GAP THIS FILLS. Membership in the account store is already CI-locked:
# tests/test_sync_skills.py::test_shipped_declaration_is_the_ruled_set reads
# the REAL shipped account-skills.txt and pins its exact name set, and it runs
# in the REQUIRED pytest / pytest-windows jobs. So adding a name is a
# deliberate two-file edit that a human reviews. What NOTHING checks is the
# other half: whether the claude.ai account store has actually RECEIVED the
# skill that name declares. It cannot — the account store is reachable only
# from a laptop with a logged-in browser session, never from CI — so the
# window between "the declaration merged" and "a laptop uploaded it" is
# invisible. Every job stays green throughout.
#
# THAT GREENNESS IS DELIBERATE AND MUST SURVIVE THIS FEATURE. The standing
# ruling is that a pending upload must be TRACKED, never BLOCKED: the pre-push
# hook prints a reminder and always exits 0, nothing advertises the state as
# broken, and this mode changes none of that. It only makes the window
# visible, by opening / updating / closing ONE tracking issue.
#
# WHY AN ISSUE AND NOT AN EXIT CODE. The upload cannot happen in CI, so a red
# check would be a permanently-red check nobody can clear from the machine
# that sees it — the fastest possible route to a check everyone learns to
# ignore. An issue is the alert channel that matches who can act (the laptop
# owner, who watches their own repo) and it self-clears the moment the upload
# lands.
#
# WHAT IT IS MODELLED ON. cms-platform's scripts/audit-scheduled-runs.js —
# specifically four of its properties, each load-bearing here:
#   1. a HIDDEN HTML MARKER identifies THE tracking issue among open issues
#      (never a title search — a title can be edited, and a near-miss match
#      opens a duplicate against an API that will happily hold both);
#   2. the /issues listing includes PULL REQUESTS, so they are filtered out;
#   3. COMMENT ONLY ON CHANGE, deduped through a hidden block, because a
#      daily identical comment is exactly the noise the alert exists to cut
#      through;
#   4. NEVER ACT ON AN UNKNOWN ANSWER. That is #258 verbatim: that audit once
#      CLOSED a live tracking issue because a probe it could not run came back
#      empty and empty read as clean. Here the probe that can come back
#      unreadable is the local account mirror, and an unreadable mirror means
#      "I could not tell", never "nothing is missing".
# Deliberately NOT taken from skills-evals' propagation.yml `report` job: it
# dedupes by full-text TITLE SEARCH with an exact-title re-check and has no
# marker and no close branch, so its issue can only accumulate.
#
# SCOPE — ABSENCE IS WHAT IT REPORTS; DRIFT IS WHAT IT REFUSES TO OVERRULE.
# The finding set is "declared for the account store, and the account store
# does not hold it". A skill that IS on the account with stale contents
# (verify's DRIFT / MISMATCH) is deliberately NOT reported here: those two
# verdicts mean the upload path MISBEHAVED and need a human reading which
# paths differ, which is a different bug with a different remedy (SKILL.md §7,
# "Read the two FAIL wordings as different bugs"). Widening this to drift
# would put a second, coarser channel on top of a verdict that already names
# the failing files.
#
# But "not reported" must never become "reported as fine", and it did once.
# Absence alone drove the verdict, so a run where every declared name was
# PRESENT-but-wrong (DRIFT, MISMATCH, or an empty account directory) computed
# as "clean" and the reporter posted an affirmative all-clear and CLOSED the
# tracking issue — on a run whose own --verify had just exited 1. That is
# property #4 above failing in its most expensive direction, from inside the
# module that quotes it. Hence ``verify_ok``: the CLEAN verdict — the only one
# that can close an issue or post an all-clear — additionally requires that
# --verify PASSED on this run. No absentees and a red verify is not clean; it
# is UNKNOWN, and UNKNOWN writes nothing in either direction.
#
# SCOPE OF THE WRITE. The gap is computed GLOBALLY (the whole declaration
# against the whole mirror) while --verify can be narrowed to one skill. A
# narrowed verify driving a repo-wide close is a write broader than what the
# operator asked for, so --report-issue requires --all; see main().
# ---------------------------------------------------------------------------

# Hidden marker that identifies THE tracking issue among this repo's open
# issues — stable forever; never change it or the next run opens a duplicate
# alongside the issue it can no longer see.
ACCOUNT_UPLOAD_ISSUE_MARKER = "<!-- account-store-upload-pending -->"
ACCOUNT_UPLOAD_ISSUE_TITLE = (
    "Skills declared for the account store are not uploaded yet"
)
# The dedupe channel: the pending set AS OF the most recent write, recorded in
# the issue body and in every comment. Deliberately the LATEST block rather
# than a union of all of them (which is what audit-scheduled-runs.js wants for
# run ids): a run id is an event that stays reported forever, whereas this set
# both GROWS (a name declared) and SHRINKS (an upload lands), and a union could
# only ever grow — it would mistake "two of the three landed" for "no change".
ACCOUNT_UPLOAD_PENDING_RE = re.compile(r"<!--\s*pending-uploads:([^\n]*?)-->")

# What a declared skill name may contain before it is allowed anywhere near a
# GitHub write. The declaration is a plain text file, so nothing upstream
# constrains its contents, and the dedupe channel above is an HTML comment
# delimited by ``-->``: a name containing ``-->`` closes the block early, so
# ``hidden_pending_block(["alpha --> beta", "gamma"])`` writes
# ``<!-- pending-uploads: alpha --> beta gamma -->`` and reads back as
# ``{"alpha"}``. The set then never compares equal to the real one, so the
# tool comments on EVERY run — precisely the daily-identical-comment noise
# that dedupe property #3 exists to prevent, plus arbitrary text injected into
# an issue body.
#
# Rejecting rather than escaping, deliberately: this character set is exactly
# what a skill DIRECTORY name can be (it is a path component that ships in a
# ZIP and becomes a folder on the account), so any name outside it is a broken
# declaration to fix at source, not a string to make safe. A rejected name
# makes the whole verdict UNKNOWN — never "clean", never "pending" — so a
# malformed declaration can drive no write at all.
# The leading lookahead is not decoration. The character class alone admits
# ".", ".." and "..." — and ".." is not a name a directory can have, it is the
# PARENT. `account_skill_payload("..")` then resolves to ACCOUNT_SKILLS_DIR/..
# and rglobs the whole tree above the mirror, returning a non-None payload, so
# ".." counted as PRESENT and a declaration containing it computed as CLEAN:
# the guard produced the one verdict it exists to withhold. Rejecting every
# all-dots name is the fix; "..a" and ".hidden" stay legal because they are
# legal directory names.
SKILL_NAME_RE = re.compile(r"(?!\.+$)[A-Za-z0-9._-]+")

# gh is a network client; a hung call must not hang a sync that has already
# done its real work. The whole reporting layer is best-effort, so a timeout
# is just one more "could not tell".
GH_TIMEOUT_SECONDS = 30


def declaration_differs_from_committed(
    path: Optional[Path] = None,
) -> Optional[str]:
    """A reason string if the declaration on disk is not the committed one.

    The tracking issue is a shared, durable artifact, and what makes the
    declaration trustworthy enough to write one off is that its membership is
    CI-locked (``test_shipped_declaration_is_the_ruled_set`` pins the exact
    name set in the required jobs). An UNCOMMITTED edit is outside that lock
    entirely, and it breaks the write in both directions: a locally-added
    name makes the issue announce a backlog item the committed declaration
    does not contain, and a locally-removed one makes the remaining names
    verify clean and CLOSE the issue on a declaration nobody agreed to.
    Neither needs an unusual flag — the documented invocation reaches both.

    Returns None when the file matches HEAD, and ALSO when the question
    cannot be answered — not running from a checkout, the path outside it,
    git absent or the file untracked. That is deliberately not the module's
    "never act on an unknown" rule, and the difference is worth stating: that
    rule governs the INPUTS to the verdict (the declaration, the mirror),
    each of which is read and can come back unreadable. This is a PROVENANCE
    check on an input that was already read successfully, and it is a second
    line of defence behind the CI lock rather than the thing creating the
    guarantee. Failing to establish provenance therefore falls back to the
    behaviour that shipped before this check existed; failing to READ the
    declaration is still UNKNOWN, above.
    """
    path = (path or ACCOUNT_SKILLS_FILE).resolve()
    repo = _self_repo()
    if repo is None:
        return None
    try:
        rel = path.relative_to(repo).as_posix()
    except ValueError:
        return None
    # Tracked-ness first: an untracked file has no committed version for the
    # working copy to differ FROM, so `status` reporting it as untracked must
    # not read as "locally modified".
    if _git(["ls-files", "--error-unmatch", "--", rel], repo) is None:
        return None
    # Staged counts as uncommitted too: the point is what `main` holds, not
    # what the index holds.
    dirty = _git(["status", "--porcelain", "--", rel], repo)
    if not dirty:
        return None
    return (
        f"the account-store membership declaration at {path} has uncommitted "
        f"local changes ({dirty.strip().splitlines()[0]}), so it is not the "
        f"CI-locked list the tracking issue speaks for. Writing off it would "
        f"either announce a backlog item nobody has agreed to or close the "
        f"issue against a declaration that does not exist on main. Commit or "
        f"revert the file, then re-run"
    )


class UploadGap(NamedTuple):
    """The account store's upload backlog, or the reason it is unknown.

    ``state`` is one of:

    ``"pending"``  ``missing`` names declared skills the account lacks.
    ``"clean"``    the account holds every declared skill.
    ``"unknown"``  the question could not be answered; ``reason`` says why.
                   NOT a synonym for ``"clean"`` — see the module note above.
    """

    state: str
    missing: List[str]
    reason: str


def account_present_names(names: Set[str]) -> Set[str]:
    """Of ``names``, the ones the account store holds — on ``verify()``'s test.

    Present means exactly what ``verify()`` means by it: ``account_skill_payload``
    returns something other than None, i.e. there is a directory for the skill
    under ``ACCOUNT_SKILLS_DIR``. Nothing else — and specifically NOT "and the
    account's own manifest.json indexes it too".

    An earlier version required both and called itself "in lockstep with
    verify()". It was not, in EITHER direction, and only the flattering
    direction was written down:

    * a payload on disk that ``manifest.json`` does not index read as ABSENT
      here while ``verify()`` read it as present — so the tracking issue named
      a skill that is in fact uploaded, and kept naming it. Removing the
      manifest condition is what fixes that, and is why this now takes a name
      set rather than a manifest;
    * an EMPTY account directory reads as PRESENT to both (``{}`` is not
      None), while ``verify()`` calls it MISMATCH and exits 1. That one is not
      fixable here at all: "the account holds a copy" and "the copy is right"
      are different questions and this function only answers the first.

    That second divergence is handled where it belongs — ``account_upload_gap``
    refuses to call anything CLEAN on a run whose ``--verify`` did not pass.
    """
    return {name for name in names if account_skill_payload(name) is not None}


def account_upload_gap(
    declared: Optional[Set[str]],
    manifest: Optional[Dict] = None,
    now: Optional[datetime.datetime] = None,
    *,
    verify_ok: Optional[bool],
    verified_names: Optional[Set[str]],
) -> UploadGap:
    """Which declared skills the account store has not received yet.

    ``declared`` and ``manifest`` are pre-read by the caller so the answer is
    computed from the same declaration and the same mirror snapshot
    ``--verify`` just used. ``verify_ok`` is that run's verdict, and it is
    keyword-only and REQUIRED on purpose: this verdict cannot be computed
    without it, so no future caller can reach the CLEAN arm by forgetting a
    parameter.

    ``verified_names`` is the set of declared skills whose account copy this
    run actually compared (``verify``'s ``verified_out``). Keyword-only and
    REQUIRED for the same reason ``verify_ok`` is.

    SEVEN conditions make the answer UNKNOWN rather than clean. The first
    five are the same mistake in different clothes — concluding "nothing is
    missing" from a source that could not be read, or was never consulted:

    * the declaration is unreadable (``declared is None``) — with no list of
      what belongs on the account, every name reads as undeclared and the
      backlog computes as empty;
    * the declaration is EMPTY. "No declared name is absent" is then
      vacuously true and every later gate passes vacuously with it: an empty
      list makes ``verify`` compare nothing and return True, so the CLEAN arm
      was reachable with no evidence whatsoever behind it. Measured: an
      empty ``--account-list`` file closed a live tracking issue with an
      affirmative all-clear, in a run whose own output said "no account copy
      was verified";
    * the declaration on disk is not the committed one
      (``declaration_differs_from_committed``) — see that function;
    * a declared name is not a legal skill name (``SKILL_NAME_RE``) — the
      declaration is corrupt, and one such name silently truncates the dedupe
      block every write depends on (see the note on ``SKILL_NAME_RE``);
    * the mirror is absent, unstamped or stale (``check_mirror_freshness``) —
      a pre-upload snapshot answers "present" for uploads that never landed,
      and a stale mirror answers "absent" for uploads that did.

    The last two are about evidence rather than readability. The first of
    them is the one that cost an issue-close on a red run:

    * no declared name is ABSENT, but ``verify_ok`` is not True. Absence is
      the only thing this function can measure, and "present" is a weaker
      claim than "correct": DRIFT, MISMATCH and an empty account directory
      are all present-and-wrong, and all three exit 1. Calling that clean let
      the reporter post "the account store now holds every skill declared"
      and close the tracking issue on a run that had just failed. It is
      UNKNOWN instead — the account may well be complete, but this run did
      not establish it, and #258's ruling is that an unestablished answer
      earns no write.

    * a declared name's account copy was never COMPARED this run
      (``declared - verified_names``). ``verify`` returns True for a run that
      opened no account copy at all — it selects skills from the resolved
      repos, and a declared skill those repos do not carry is simply never
      reached — so a green verdict can rest on zero comparisons. The account
      copy may hold anything; nothing looked. Same rule as the branch above,
      one level down: "present" is weaker than "correct", and "never opened"
      is weaker still.

    Note the ORDER: ``missing`` is returned as PENDING before the
    ``verify_ok`` gate is consulted. A red verify caused BY the missing
    upload is exactly the state the tracking issue exists to announce, so
    gating pending on a green verify would silence the alert in the one case
    it is for.

    The returned ``missing`` list is always a subset of ``declared``. That
    makes it a subset of the COMMITTED declaration only because two other
    things hold: ``--report-issue`` refuses ``--account-list`` (see
    ``main()``), so ``declared`` always comes from ``ACCOUNT_SKILLS_FILE``,
    and the provenance branch above rejects a locally-edited copy of it.
    Remove either and this sentence stops being true — it was not, before
    both existed.
    """
    if declared is None:
        return UploadGap(
            "unknown",
            [],
            f"the account-store membership declaration at "
            f"{ACCOUNT_SKILLS_FILE} is missing or unreadable, so there is no "
            f"list of what the account is supposed to hold",
        )
    if not declared:
        return UploadGap(
            "unknown",
            [],
            "the account-store membership declaration is EMPTY, so 'no "
            "declared skill is missing' is true of nothing and --verify had "
            "nothing to compare. An empty list cannot establish that the "
            "account store is complete; it establishes that this run asked "
            "no question",
        )
    malformed = sorted(n for n in declared if not SKILL_NAME_RE.fullmatch(n))
    if malformed:
        return UploadGap(
            "unknown",
            [],
            f"the account-store membership declaration contains "
            f"{len(malformed)} entr(y/ies) that are not legal skill names "
            f"(allowed: letters, digits, '.', '_', '-'): "
            f"{', '.join(repr(n) for n in malformed)}. A name outside that "
            f"set can truncate the hidden dedupe block and inject text into "
            f"an issue body, so no issue is written until the declaration is "
            f"fixed",
        )
    uncommitted = declaration_differs_from_committed()
    if uncommitted:
        return UploadGap("unknown", [], uncommitted)
    stale = check_mirror_freshness(manifest, now)
    if stale:
        return UploadGap("unknown", [], stale)
    missing = sorted(declared - account_present_names(declared))
    if missing:
        return UploadGap(
            "pending",
            missing,
            f"{len(missing)} declared skill(s) not on the account store",
        )
    if not verify_ok:
        return UploadGap(
            "unknown",
            [],
            "no declared skill is ABSENT from the account store, but --verify "
            "did not pass on this run — so at least one account copy is "
            "present-and-wrong (DRIFT, MISMATCH, an empty directory, or a "
            "skill on the account with no membership ruling). 'Nothing is "
            "missing' is not 'the account is correct', and only the second "
            "licenses closing the tracking issue. Read --verify's own output "
            "above for the failing skill",
        )
    unverified = sorted(declared - (verified_names or set()))
    if unverified:
        return UploadGap(
            "unknown",
            [],
            f"{len(unverified)} declared skill(s) are present on the account "
            f"store but their contents were never compared on this run "
            f"({', '.join(unverified)}) — the resolved repos do not carry "
            f"them, so --verify passed without opening them. A green verdict "
            f"over zero comparisons is not evidence the account is correct. "
            f"Point --repos at the tree that holds them and re-run",
        )
    return UploadGap("clean", [], "every declared skill is on the account")


# ── issue text ──────────────────────────────────────────────────────────────

def hidden_pending_block(missing: List[str]) -> str:
    """The dedupe channel for one write: the pending set, sorted."""
    return f"<!-- pending-uploads: {' '.join(sorted(missing))} -->"


def extract_reported_pending(texts: List[Optional[str]]) -> Optional[Set[str]]:
    """The LAST pending set recorded across issue body + comments, in order.

    Returns None when no block was found at all — an issue body written by
    hand, or one predating this channel. None is distinct from ``set()`` for
    the usual reason: an empty set would read as "the last write reported
    nothing pending", which would suppress the very first comment. None
    routes to "changed", so the next run states the current set once and
    every run after that dedupes against it.
    """
    found: Optional[Set[str]] = None
    for text in texts or []:
        if not isinstance(text, str):
            continue
        for match in ACCOUNT_UPLOAD_PENDING_RE.finditer(text):
            found = {n for n in match.group(1).split() if n}
    return found


def _render_missing(missing: List[str]) -> str:
    return "\n".join(f"- `{name}`" for name in sorted(missing))


def _remedy() -> str:
    """The concrete laptop recipe, in the order that actually works.

    Written for someone who did not build this: the refresh comes FIRST
    because every step after it reads the mirror, and skipping it is the
    documented way to get a confident wrong answer (SKILL.md §7).
    """
    script = "plugins/adam-local/skills/sync-skills/sync_skills.py"
    return (
        "## How to clear this\n"
        "\n"
        "The upload needs a laptop with a logged-in claude.ai tab open in "
        "Chrome — it cannot be done from CI, which is why this is an issue "
        "and not a failing check. From a clean `main` checkout:\n"
        "\n"
        "1. **Refresh the local account mirror first.** Everything below "
        "reads it, and a stale mirror compares your uploads against a "
        "pre-upload snapshot:\n"
        "\n"
        "   ```bash\n"
        "   CLAUDE_CODE_SYNC_SKILLS=1 claude -p 'ok'\n"
        "   ```\n"
        "\n"
        "2. **Build the ZIP and upload it** for each name above, following "
        "`sync-skills` SKILL.md §1 and §3 (the browser reads the ZIP off "
        "disk; never paste base64):\n"
        "\n"
        "   ```bash\n"
        f"   python3 {script} --prepare --skill NAME --zip-dir \"$TMPDIR/skillzips\"\n"
        "   ```\n"
        "\n"
        "3. **Re-run the gate.** It closes this issue itself once the "
        "account holds every declared skill:\n"
        "\n"
        "   ```bash\n"
        "   CLAUDE_CODE_SYNC_SKILLS=1 claude -p 'ok'\n"
        f"   python3 {script} --verify --all --report-issue\n"
        "   ```\n"
        "\n"
        "Do **not** clear this by deleting the name from `account-skills.txt` "
        "unless the membership ruling itself changed: adding a name there is "
        "close to a one-way door, because the upload API has no delete "
        "(ADR 0002).\n"
    )


def build_upload_issue_body(missing: List[str]) -> str:
    """The tracking issue body. Names come only from the caller's list."""
    return (
        f"{ACCOUNT_UPLOAD_ISSUE_MARKER}\n"
        "\n"
        f"`account-skills.txt` declares {len(missing)} skill(s) that the "
        "claude.ai account store does not hold yet. Nothing else reports "
        "this: the declaration's membership is CI-locked, but the account "
        "store itself is reachable only from a laptop, so every check here "
        "stays green while the upload is outstanding. That is by design — "
        "a pending upload is tracked, never blocked.\n"
        "\n"
        "**Pending upload:**\n"
        "\n"
        f"{_render_missing(missing)}\n"
        "\n"
        f"{_remedy()}"
        "\n"
        f"{hidden_pending_block(missing)}\n"
    )


def build_upload_comment(missing: List[str]) -> str:
    """Posted only when the pending set CHANGED since the last write."""
    return (
        "The set of skills awaiting upload to the account store has "
        f"changed. Now pending ({len(missing)}):\n"
        "\n"
        f"{_render_missing(missing)}\n"
        "\n"
        f"{hidden_pending_block(missing)}\n"
    )


def build_upload_close_comment() -> str:
    """Posted only on a run where NOTHING is absent AND ``--verify`` passed.

    Both halves are load-bearing in the wording. The claim used to be posted
    on absence alone, so it could and did go out on a run whose ``--verify``
    had exited 1 on DRIFT — and the "a later run reopens a fresh issue"
    promise was false for exactly that case, because drift is not something
    this reporter tracks and reopening is not something it would ever do.
    """
    return (
        "The claude.ai account store now holds every skill declared in "
        "`account-skills.txt`, and the `--verify` run that established that "
        "passed — closing.\n"
        "\n"
        "A later run opens a fresh tracking issue if another declared skill "
        "goes un-uploaded. It will NOT reopen for content drift: a skill "
        "that is on the account with stale contents is `--verify`'s DRIFT / "
        "MISMATCH verdict, which names the differing files and exits 1, and "
        "this issue deliberately does not duplicate it."
    )


# ── gh-backed plumbing (best effort: it may never raise, and never blocks) ───

def _gh_api(
    endpoint: str,
    method: Optional[str] = None,
    fields: Optional[List[str]] = None,
) -> Optional[str]:
    """One ``gh api`` call. Returns stdout, or None on ANY failure.

    Never raises and never propagates a message that could carry a
    credential: only gh's FIRST stderr line is echoed, and the environment
    (which holds GH_TOKEN) is inherited untouched rather than logged.

    A missing gh, a logged-out gh, a rate limit and a network hiccup all
    land here as None. That is the whole safety story for this mode: a
    tracker that can break a developer's push is worse than no tracker.
    """
    args = ["gh", "api", endpoint]
    if method:
        args += ["-X", method]
    for field in fields or []:
        args += ["-f", field]
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GH_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # FileNotFoundError, TimeoutExpired, OSError…
        print(
            f"NOTE: --report-issue could not run gh ({type(exc).__name__}: "
            f"{exc}); the tracking issue was left untouched.",
            file=sys.stderr,
        )
        return None
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        print(
            f"NOTE: --report-issue: `gh api {endpoint}` failed (exit "
            f"{result.returncode}: {detail[0] if detail else 'no output'}); "
            f"the tracking issue was left untouched.",
            file=sys.stderr,
        )
        return None
    return result.stdout


def _gh_json(
    endpoint: str,
    method: Optional[str] = None,
    fields: Optional[List[str]] = None,
) -> Optional[object]:
    """``_gh_api`` plus a real JSON parse. None on failure, including bad JSON."""
    raw = _gh_api(endpoint, method=method, fields=fields)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        print(
            f"NOTE: --report-issue: `gh api {endpoint}` returned "
            f"unparseable JSON ({exc}); the tracking issue was left untouched.",
            file=sys.stderr,
        )
        return None


def github_slug_from_remote(url: Optional[str]) -> Optional[str]:
    """``owner/name`` from a git remote URL, or None if it is not a GitHub one.

    Handles the three forms a clone here can carry — ``git@github.com:o/r.git``,
    ``ssh://git@github.com/o/r.git`` and ``https://github.com/o/r.git``.
    Parsed rather than hardcoded so a fork, a rename or a second remote host
    reports against the repo it is actually working in.
    """
    if not url:
        return None
    text = url.strip()
    if text.endswith(".git"):
        text = text[: -len(".git")]
    match = re.search(r"github\.com[/:]+([^/\s]+)/([^/\s]+)/*$", text)
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}"


# What may be interpolated into an issue endpoint. The old test rejected only
# "/" and whitespace, which let "owner/name?state=all", "o/n#frag" and "../x"
# through into f"repos/{target}/issues" — a string that is built by hand and
# never URL-quoted. None of those could retarget the OWNER (that needs a "/"
# in the first component, which both versions forbid) and "?"/"#" truncate the
# path back to a non-write endpoint, so this is hardening rather than a live
# hole — but "a write must go where you said or nowhere" is a property the
# validator should enforce, not one the endpoint's shape happens to preserve.
# GitHub's own rules: owner is alphanumeric-or-hyphen, not hyphen-terminated;
# repo adds "." and "_" and cannot be "." or "..".
GITHUB_SLUG_RE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/(?!\.+$)[A-Za-z0-9._-]+"
)


def resolve_report_repo(
    explicit: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """``(owner/name, None)`` or ``(None, why_not)`` — where the issue lives.

    ``--report-repo`` wins; otherwise the ``origin`` remote of the checkout
    this script lives in. There is no fallback constant on purpose: the same
    reason ``resolve_repos`` refuses to guess clone locations — a guessed
    destination for a WRITE is how an issue lands in somebody else's repo.

    Two failures, and they are NOT the same message. A bare None for both
    told an operator who had just passed ``--report-repo 'bad slug here'`` to
    "pass --report-repo OWNER/NAME" — advice to do the thing they had done,
    pointing at the wrong cause. The SAFETY property is unchanged and is the
    reason this returns early: a malformed explicit value never falls through
    to ``origin``, because silently retargeting a write to a different repo
    than the one the operator named is worse than not writing at all.
    """
    if explicit:
        if GITHUB_SLUG_RE.fullmatch(explicit):
            return explicit, None
        return None, (
            f"--report-repo was given {explicit!r}, which is not an "
            f"OWNER/NAME slug (exactly one '/', no spaces). Nothing was "
            f"written, and this did NOT fall back to the origin remote: a "
            f"write must go where you said or nowhere"
        )
    self_repo = _self_repo()
    if self_repo is None:
        return None, (
            "this script is not running from inside a repo checkout, so "
            "there is no origin remote to file against; pass "
            "--report-repo OWNER/NAME"
        )
    slug = github_slug_from_remote(_git(["remote", "get-url", "origin"], self_repo))
    if slug is not None and not GITHUB_SLUG_RE.fullmatch(slug):
        # Same validator on both arms. A remote URL is operator-controlled
        # data too, and the property being defended ("a write goes where you
        # said or nowhere") does not care which arm produced the target.
        return None, (
            f"the `origin` remote on {self_repo} parses to {slug!r}, which is "
            f"not a usable OWNER/NAME slug; pass --report-repo OWNER/NAME"
        )
    if slug is None:
        return None, (
            f"no GitHub `origin` remote on {self_repo}, so there is nothing "
            f"to derive the target repo from; pass --report-repo OWNER/NAME"
        )
    return slug, None


# How many 100-item pages either listing will walk before giving up. Ten is
# 1000 items, far past anything these repos carry; the cap exists so a
# pathological or looping API cannot spin here forever, not as a real limit.
GH_MAX_PAGES = 10


def find_upload_tracking_issue(repo: str):
    """``(issue_or_None, lookup_ok)`` — the open issue carrying the marker.

    The two failure shapes must not be conflated: "there is no such issue"
    licenses opening one, while "the lookup failed" does not — treating the
    second as the first is how a duplicate gets opened next to an issue the
    run simply could not see, against an API that will hold both happily.
    Hence the explicit ok flag rather than a bare None.

    PAGINATED, for exactly that reason. This used to issue ONE request for
    ``state=open&per_page=100`` and read a short first page as the whole
    truth. GitHub's ``/issues`` listing returns PULL REQUESTS as well as
    issues and defaults to created-descending, so an AGEING tracking issue —
    which is precisely the one still open and still needing an update — drops
    off page 1 as soon as 100 newer open items exist. The next run then finds
    nothing and opens a SECOND tracking issue beside the first, falsifying
    the one property this module's header claims for the marker.

    Running out of pages is a lookup FAILURE, not an absence: after
    ``GH_MAX_PAGES`` full pages there may be more, and "I stopped looking"
    must never license opening a duplicate.

    The ``/issues`` listing includes PULL REQUESTS; they are filtered out.
    """
    for page in range(1, GH_MAX_PAGES + 1):
        endpoint = (
            f"repos/{repo}/issues?state=open&per_page=100&page={page}"
        )
        data = _gh_json(endpoint)
        if not isinstance(data, list):
            # Distinct from the None case, which _gh_api has already
            # explained: here gh exited 0 and returned well-formed JSON that
            # simply is not a list (``{"message": "Not Found"}`` is the one
            # that actually happens). Nothing downstream would have said a
            # word about it, and a silent give-up reads exactly like "no
            # issue is open".
            if data is not None:
                print(
                    f"NOTE: --report-issue: `gh api {endpoint}` returned "
                    f"{type(data).__name__}, not a list of issues, so the "
                    f"tracking issue could not be looked up; it was left "
                    f"untouched.",
                    file=sys.stderr,
                )
            return None, False
        for item in data:
            if not isinstance(item, dict) or item.get("pull_request"):
                continue
            body = item.get("body")
            if isinstance(body, str) and ACCOUNT_UPLOAD_ISSUE_MARKER in body:
                return item, True
        if len(data) < 100:
            return None, True
    print(
        f"NOTE: --report-issue: {repo} has more than "
        f"{GH_MAX_PAGES * 100} open issues/PRs and the marker was not found "
        f"in them, so whether a tracking issue exists is unknown; it was "
        f"left untouched.",
        file=sys.stderr,
    )
    return None, False


def list_issue_comments(repo: str, number: int):
    """``(comment_bodies, ok)`` in chronological order (the API's default).

    Same two-shape contract as ``find_upload_tracking_issue``: exhausting
    ``GH_MAX_PAGES`` is a failure, not "that was all of them", because the
    caller uses this to decide whether the pending set CHANGED and a
    truncated history answers that question wrongly.
    """
    bodies: List[Optional[str]] = []
    for page in range(1, GH_MAX_PAGES + 1):
        endpoint = (
            f"repos/{repo}/issues/{number}/comments?per_page=100&page={page}"
        )
        data = _gh_json(endpoint)
        if not isinstance(data, list):
            if data is not None:
                print(
                    f"NOTE: --report-issue: `gh api {endpoint}` returned "
                    f"{type(data).__name__}, not a list of comments.",
                    file=sys.stderr,
                )
            return [], False
        bodies.extend(c.get("body") for c in data if isinstance(c, dict))
        if len(data) < 100:
            return bodies, True
    print(
        f"NOTE: --report-issue: issue #{number} on {repo} has more than "
        f"{GH_MAX_PAGES * 100} comments; its history could not be read in "
        f"full.",
        file=sys.stderr,
    )
    return bodies, False


def report_account_upload_gap(
    gap: UploadGap,
    repo: Optional[str] = None,
) -> None:
    """Open / comment on / close the single tracking issue. Never raises.

    Returns None in every case: this runs AFTER ``--verify`` has reached its
    verdict and must not be able to influence it. Every branch that cannot
    establish something states it on stderr and never leaves the issue in a
    state it has not explained.

    That is deliberately weaker than "leaves the issue alone", which is what
    this used to claim and is not true of one branch: on the clean path the
    closing COMMENT is posted before the close PATCH, so a PATCH that fails
    leaves an issue that is open while carrying a comment saying it is
    resolved. Saying so on stderr is the right behaviour — closing silently
    or pretending the comment was not written would both be worse — but a
    docstring narrower than its code is how a reader ends up trusting an
    invariant that does not hold. Repeated clean runs against a persistently
    failing PATCH will append that comment each time: the comment-only-on-
    change dedupe covers ``build_upload_comment``, not this one.
    """
    if gap.state == "unknown":
        # The #258 shape: an unreadable probe is not a clean answer. Opening
        # would invent a backlog; closing would retire a live one.
        print(
            f"NOTE: --report-issue made NO change to the tracking issue — the "
            f"account-store state is UNKNOWN, not clean: {gap.reason}",
            file=sys.stderr,
        )
        return

    target, why_not = resolve_report_repo(repo)
    if target is None:
        print(
            f"NOTE: --report-issue could not work out which repo to file "
            f"against: {why_not}. The tracking issue was left untouched.",
            file=sys.stderr,
        )
        return

    issue, ok = find_upload_tracking_issue(target)
    if not ok:
        # find_upload_tracking_issue (or _gh_api beneath it) has printed the
        # specific reason. This line is what guarantees the run says SOMETHING
        # rather than exiting a give-up branch in silence.
        print(
            f"NOTE: --report-issue made NO change on {target}: whether a "
            f"tracking issue is open could not be established.",
            file=sys.stderr,
        )
        return

    if gap.state == "clean":
        if issue is None:
            print(
                f"--report-issue: account store is complete and no tracking "
                f"issue is open on {target}. Nothing to do.",
                file=sys.stderr,
            )
            return
        number = issue.get("number")
        posted = _gh_api(
            f"repos/{target}/issues/{number}/comments",
            fields=[f"body={build_upload_close_comment()}"],
        )
        if posted is None:
            # Say WHY it stayed open rather than closing an issue whose
            # closing comment never landed — a silently-closed issue with no
            # explanation is the thing a reader has to reconstruct later.
            print(
                f"NOTE: --report-issue left issue #{number} OPEN: the closing "
                f"comment could not be posted.",
                file=sys.stderr,
            )
            return
        if _gh_api(
            f"repos/{target}/issues/{number}",
            method="PATCH",
            fields=["state=closed", "state_reason=completed"],
        ) is None:
            print(
                f"NOTE: --report-issue left issue #{number} OPEN: the closing "
                f"comment posted but the close itself did not. The issue now "
                f"carries a comment saying it is resolved while still being "
                f"open — close it by hand or re-run.",
                file=sys.stderr,
            )
            return
        print(f"--report-issue: closed #{number} on {target} — account store complete.")
        return

    # gap.state == "pending"
    if issue is None:
        created = _gh_json(
            f"repos/{target}/issues",
            fields=[
                f"title={ACCOUNT_UPLOAD_ISSUE_TITLE}",
                f"body={build_upload_issue_body(gap.missing)}",
            ],
        )
        if isinstance(created, dict) and created.get("number"):
            print(
                f"--report-issue: opened #{created['number']} on {target} — "
                f"{len(gap.missing)} skill(s) awaiting upload."
            )
        else:
            # Either the POST failed (_gh_api said so) or it "succeeded" with
            # a body that is not an issue. Either way the backlog is real and
            # now goes unannounced, which is the one thing this mode exists
            # to prevent — so it says so here rather than returning quietly.
            print(
                f"NOTE: --report-issue could NOT open a tracking issue on "
                f"{target}; {len(gap.missing)} skill(s) are still awaiting "
                f"upload and nothing is tracking them: "
                f"{', '.join(gap.missing)}",
                file=sys.stderr,
            )
        return

    number = issue.get("number")
    comments, ok = list_issue_comments(target, number)
    if not ok:
        # Cannot establish whether the set changed. The alert is already
        # delivered (the issue is open), so silence beats a comment that may
        # be the same one for the third day running.
        print(
            f"NOTE: --report-issue left issue #{number} unchanged: its "
            f"comments could not be read, so whether the pending set changed "
            f"is unknown.",
            file=sys.stderr,
        )
        return
    previous = extract_reported_pending([issue.get("body"), *comments])
    if previous == set(gap.missing):
        print(
            f"--report-issue: #{number} on {target} already reports this exact "
            f"pending set ({len(gap.missing)} skill(s)). No comment posted."
        )
        return
    if _gh_api(
        f"repos/{target}/issues/{number}/comments",
        fields=[f"body={build_upload_comment(gap.missing)}"],
    ) is not None:
        print(
            f"--report-issue: commented on #{number} on {target} — pending set "
            f"changed to {len(gap.missing)} skill(s)."
        )
    else:
        print(
            f"NOTE: --report-issue: issue #{number} on {target} stays open but "
            f"its pending set is now out of date — the update comment could "
            f"not be posted.",
            file=sys.stderr,
        )


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
        help=(
            "Repo paths to scan. There are no built-in clone locations: this "
            "or $AGENTSKILLS_REPOS is how a repo other than this script's own "
            "checkout gets found at all"
        ),
    )
    parser.add_argument(
        "--yes", action="store_true",
        help=(
            f"Sync from a repo that is off {MAIN_BRANCH} or not up to date "
            f"with origin/{MAIN_BRANCH} instead of stopping to ask"
        ),
    )
    parser.add_argument(
        "--zip-dir", metavar="DIR",
        help=(
            "Write each skill's ZIP into DIR as a real file and emit "
            "zip_path instead of zip_b64. This is what the documented "
            "upload path (SKILL.md section 3) uses: the browser reads the "
            "file directly, so a 200KB base64 blob never has to travel "
            "through the agent's context."
        ),
    )
    parser.add_argument(
        "--account-list", metavar="PATH",
        help=(
            "Declared account-store membership list to read "
            f"(default: {ACCOUNT_SKILLS_FILE.name} beside this script). "
            f"Rejected with --report-issue: that write speaks for the "
            f"committed file. Use it "
            "to dry-run a proposed membership change before committing one."
        ),
    )
    parser.add_argument(
        "--record-account-state", action="store_true",
        help=(
            "Record what the local account mirror (~/.claude/skills/synced) "
            "holds for every declared skill, into account-state.json. Needs a "
            "session signed in to the account; this is the observation CI "
            "cannot make for itself. Re-run after uploading."
        ),
    )
    parser.add_argument(
        "--account-drift", action="store_true",
        help=(
            "Compare each declared skill's registry payload against "
            "account-state.json and print a JSON report to stdout. Runs "
            "anywhere, including CI: it reads the committed recording, never "
            "the account. Exits 0 whether or not anything drifted - drift is "
            "the finding, not an error."
        ),
    )
    parser.add_argument(
        "--account-state", metavar="PATH",
        help=(
            f"Recorded-account-state file to read or write "
            f"(default: {ACCOUNT_STATE_FILE.name} beside this script)."
        ),
    )
    parser.add_argument(
        "--assert-uploaded", metavar="NAME",
        help=(
            "Record NAME as uploaded to the account store on your word, "
            "without a mirror to check it against. The entry is stamped "
            "basis=asserted so it stays distinguishable from a "
            "--record-account-state row. Digests the tree --repos points at, "
            "which must be the tree the uploaded artifact was built from."
        ),
    )
    parser.add_argument(
        "--asserted-ref", metavar="SHA",
        help="Commit the asserted upload was built from; recorded as provenance.",
    )
    parser.add_argument(
        "--asserted-run", metavar="ID",
        help="Workflow run that built the uploaded artifact; recorded as provenance.",
    )
    parser.add_argument(
        "--report-issue", action="store_true",
        help=(
            "With --verify --all: open, update or close ONE GitHub tracking "
            "issue for skills declared in account-skills.txt that the account "
            "store has not received yet. Requires --all because the backlog is "
            "computed across the WHOLE declaration, so a narrowed verify would "
            "drive a repo-wide write. Opt-in, best-effort and non-blocking - it "
            "never changes --verify's exit code and never fails the run. Needs "
            "a refreshed mirror, so run the CLAUDE_CODE_SYNC_SKILLS refresh "
            "first (SKILL.md section 7)"
        ),
    )
    parser.add_argument(
        "--report-repo", metavar="OWNER/NAME",
        help=(
            "Which repo --report-issue files against (default: the origin "
            "remote of this script's own checkout). There is no built-in "
            "fallback: a guessed destination for a WRITE lands an issue in "
            "somebody else's repo"
        ),
    )
    args = parser.parse_args()

    # --report-issue reports on a verdict, so it needs one. Silently ignoring
    # it on a --prepare run would read as "reported, nothing pending".
    if args.report_issue and not args.verify:
        parser.error("--report-issue requires --verify")
    # And the verdict it reports on has to cover the same ground the write
    # does. The backlog is computed GLOBALLY — the entire declaration against
    # the entire mirror — while --verify can be narrowed to one skill or to
    # git-changed skills. `--verify --skill sync-skills --report-issue` was
    # therefore accepted and went on to CLOSE the repo's tracking issue on the
    # strength of having checked one skill: a write far broader than the
    # question asked. Refuse rather than silently widen or silently narrow;
    # narrowing is not on offer because a per-skill backlog is not a thing
    # this issue can represent.
    if args.report_issue and not args.all:
        parser.error(
            "--report-issue requires --all. The tracking issue covers the "
            "WHOLE account-store declaration, so it can only be opened, "
            "updated or closed on a run that verified all of it; a narrowed "
            "--verify (--skill NAME, or the default git-changed selection) "
            "would make a repo-wide write off a subset. Run: "
            "--verify --all --report-issue"
        )
    # And it has to be the COMMITTED declaration the write speaks for.
    # --account-list does not narrow the question the way --skill does; it
    # REPLACES the entire basis of the answer with a file the operator names,
    # which is strictly the larger substitution — and its own --help calls it
    # a way to "dry-run a proposed membership change before committing one".
    # Measured: `--verify --all --report-issue --account-list <empty file>`
    # closed the repo's live tracking issue and posted "the account store now
    # holds every skill declared in `account-skills.txt`" — a file that run
    # never opened, which at the time declared ten skills of which nine were
    # absent. Pointed at a list of invented names it opened an issue naming
    # them, over the words "account-skills.txt declares 2 skill(s)".
    # Refused for the same reason --skill is: the write is answerable only
    # for the declaration the repo actually carries. The dry-run use case is
    # unaffected — run --account-list WITHOUT --report-issue.
    if args.report_issue and args.account_list:
        parser.error(
            "--report-issue cannot be combined with --account-list. The "
            "tracking issue speaks for the committed "
            f"{ACCOUNT_SKILLS_FILE.name}, so it can only be opened, updated "
            "or closed on a run that read that file; --account-list swaps "
            "the whole declaration out and the issue text would describe a "
            "file the run never opened. Dry-run a proposed membership change "
            "with --account-list alone, and drop it to report."
        )
    if args.report_repo and not args.report_issue:
        parser.error("--report-repo only means anything with --report-issue")

    # --mark-synced touches only the state file, so it needs no repo.
    if args.mark_synced:
        parts = args.mark_synced.split(":", 1)
        if len(parts) != 2:
            sys.exit("ERROR: --mark-synced expects NAME:HASH")
        mark_synced(parts[0], parts[1])
        print(f"Marked {parts[0]} as synced (hash={parts[1]})")
        return

    # Reads the account mirror and the declaration; no repo is involved, so
    # this returns before repo resolution exactly as --mark-synced does.
    if args.record_account_state:
        state_path = (
            Path(args.account_state).expanduser()
            if args.account_state else ACCOUNT_STATE_FILE
        )
        declared_now = load_account_declaration(
            Path(args.account_list).expanduser() if args.account_list else None
        )
        if declared_now is None:
            sys.exit(
                "ERROR: the account-store membership declaration is missing or "
                "unreadable, so there is nothing to record against."
            )
        if not ACCOUNT_SKILLS_DIR.is_dir():
            sys.exit(
                f"ERROR: no account mirror at {ACCOUNT_SKILLS_DIR}. Only a "
                f"session signed in to the claude.ai account has one - "
                f"refresh it with `CLAUDE_CODE_SYNC_SKILLS=1 claude -p 'ok'` "
                f"and re-run. Recording an absent mirror would write a file "
                f"claiming every skill was never uploaded."
            )
        state = build_account_state(declared_now)
        state_path.write_text(
            json.dumps(state, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        held = sum(1 for v in state["skills"].values() if v["digest"])
        print(
            f"Recorded {held}/{len(state['skills'])} declared skills as held by "
            f"the account store -> {state_path}"
        )
        return

    repos = resolve_repos(args.repos)
    if not repos:
        sys.exit(
            f"ERROR: no repo could be resolved, so nothing was inspected. "
            f"This is not the same as having nothing to sync. {REPO_HINT}"
        )

    # Same placement rationale as --account-drift below: it reads the trees to
    # digest one skill and writes only the recording, never an upload, so a
    # detached-HEAD runner must not be turned away by check_repo_state.
    if args.assert_uploaded:
        try:
            changed, message = assert_uploaded(
                args.assert_uploaded,
                repos,
                ref=args.asserted_ref,
                run_id=args.asserted_run,
                state_path=(
                    Path(args.account_state).expanduser()
                    if args.account_state else None
                ),
                declared=load_account_declaration(
                    Path(args.account_list).expanduser()
                    if args.account_list else None
                ),
            )
        except ValueError as exc:
            sys.exit(f"ERROR: {exc}")
        print(message)
        if changed:
            print(
                "This is a CLAIM that the upload landed whole. Nothing can "
                "check it - re-run --record-account-state from a session with "
                "a mirror to replace it with a measurement."
            )
        return

    # Reads the trees but writes nothing and uploads nothing, so it runs
    # before check_repo_state: a CI runner sits on a detached HEAD, and
    # failing there would make the report unavailable exactly where it is the
    # only report obtainable.
    if args.account_drift:
        declared_now = load_account_declaration(
            Path(args.account_list).expanduser() if args.account_list else None
        )
        if declared_now is None:
            sys.exit(
                "ERROR: the account-store membership declaration is missing or "
                "unreadable, so there is nothing to compare."
            )
        state_path = (
            Path(args.account_state).expanduser()
            if args.account_state else ACCOUNT_STATE_FILE
        )
        state = load_account_state(state_path)
        rows = account_drift(repos, declared_now, state)
        report = account_drift_report(rows, state)
        print(json.dumps(report, indent=2))
        if state is None:
            print(
                f"WARNING: no recording at {state_path}, so every declared "
                f"skill reports as `unrecorded`. That is 'nobody has looked', "
                f"not 'everything drifted'.",
                file=sys.stderr,
            )
        for row in rows:
            # The suffix is not decoration: `in-sync` alone is what made an
            # unverified claim read exactly like a measurement.
            label = row["status"]
            if row.get("recorded_basis") == "asserted":
                label += " (asserted)"
            print(f"{label:22} {row['name']}", file=sys.stderr)
        return

    # Everything below builds an upload out of these working trees, so their
    # state is a precondition, not a detail. --mark-synced returned above and
    # is exempt: it touches only the state file.
    if not check_repo_state(repos, assume_yes=args.yes):
        sys.exit(1)

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
                # Preview only what this repo actually holds. A requested
                # name used to be printed once per resolved repo, inventing
                # a row for repos that do not carry the skill at all - which
                # reads as a second copy and invites an upload from the
                # wrong tree.
                if _skill_dir(repo, name) is None:
                    continue
                # Same answer as prepare(), from the same function: tagging
                # from the state file alone previewed NEW on a fresh machine
                # for skills the real run uploaded as updates.
                tag = "UPDATE" if is_update(name, state) else "NEW   "
                print(f"  {tag}  {name}  ({repo.name})")
                any_found = True
        if not any_found:
            print(
                f"Nothing would be synced. Resolved repos:\n"
                f"{describe_resolved_repos(repos)}\n"
                f"Use --all to sync everything; if the tree(s) above are not "
                f"the ones you meant: {REPO_HINT}"
            )
        return

    # --verify
    if args.verify:
        # named=True only for --skill: --all enumerates the registry, and most
        # of the registry is legitimately not on the account.
        # Filled by verify() with the declared skills it actually compared;
        # the reporter needs it to tell a green verdict from a green verdict
        # over zero comparisons. Collected unconditionally so the two calls
        # cannot drift apart depending on a flag.
        verified_names: Set[str] = set()
        ok = verify(
            repos, skill_names, declared=declared, named=bool(args.skill),
            selection="skill" if args.skill else ("all" if args.all else "changed"),
            verified_out=verified_names,
        )
        # Strictly AFTER the verdict, and strictly unable to affect it. The
        # reporter is best-effort by construction (every gh call returns None
        # rather than raising), but the belt-and-braces except is deliberate:
        # this mode exists to make a quiet state visible, and a tracker that
        # can turn a developer's clean sync into a traceback has made things
        # worse than the silence it replaced.
        if args.report_issue:
            try:
                report_account_upload_gap(
                    # `verify_ok=ok` is the whole F1 fix at the call site:
                    # the reporter may only post an all-clear and close on a
                    # run whose --verify actually passed.
                    account_upload_gap(
                        declared, account_manifest(), verify_ok=ok,
                        verified_names=verified_names,
                    ),
                    repo=args.report_repo,
                )
            except Exception as exc:  # noqa: BLE001 - see above
                print(
                    f"NOTE: --report-issue failed ({type(exc).__name__}: "
                    f"{exc}); --verify's own result is unaffected.",
                    file=sys.stderr,
                )
        sys.exit(0 if ok else 1)

    # Default: --prepare / JSON output
    zip_dir = Path(args.zip_dir).expanduser() if args.zip_dir else None
    result = prepare(repos, skill_names, declared=declared, zip_dir=zip_dir)
    if not result["skills"]:
        result["message"] = (
            "No skills to sync. Resolved repos: "
            + "; ".join(
                f"{repo} ({len(get_all_skills(repo))} skill(s) found)"
                for repo in repos
            )
            + (
                ". --all was passed, so this is repo resolution, not the flags."
                if args.all
                else ". Use --all to sync everything."
            )
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
