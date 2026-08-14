#!/usr/bin/env python3
"""check_skills.py — conformance census over every skills registry on this machine.

Scans each registry declared in `scripts/skills_registries.yml` — this repo plus any
sibling repo that ships skills under its own layout — and reports every SKILL.md that
violates the shared contract: malformed or non-conforming frontmatter, a body whose code
blocks run a payload file that does not exist, or a skill basename that appears in more
than one registry.

Everything is derived from the config file and the filesystem: no skill name, registry
name, count, field name, or limit is hardcoded here. Changing the contract means editing
`skills_registries.yml`, not this script.

Finding classes
--------------
  frontmatter-missing   no leading `---`-delimited frontmatter block
  yaml-parse            the frontmatter block is not parseable YAML
  frontmatter-not-map   the frontmatter parses, but not to a mapping
  missing-field         a `required_fields:` key is absent or empty/whitespace
  name-dir-mismatch     frontmatter `name` != the skill directory basename
  name-pattern          `name` does not match `name_pattern:`
  length-limit          a field exceeds its `max_lengths:` entry
  non-spec-field        a frontmatter key outside `known_fields:`
  dangling-payload-ref  a code block runs `<payload_dir>/…` that is not on disk
                        (prose-only mentions deliberately do not gate — see PROSE_ONLY_RULE)
  undeclared-duplicate  a skill basename exists in more than one registry, unwaived
  registry-unresolved   a required registry's path does not exist
  stale-waiver          a declared waiver matched nothing this run
  waiver-invalid        a waiver entry is missing a required field / malformed

Usage
-----
  python3 scripts/check_skills.py
  python3 scripts/check_skills.py --registry agentskills=/path/to/clone --json
  python3 scripts/check_skills.py --list-findings      # also audit dismissed candidates

Exit status
-----------
  0  no non-waived findings, no stale waivers, every required registry resolved
  1  otherwise
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml
from markdown_it import MarkdownIt

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_CONFIG = SCRIPT_DIR / "skills_registries.yml"
DEFAULT_WAIVERS = SCRIPT_DIR / "skills_waivers.yml"

# --- finding classes -------------------------------------------------------------
K_FRONTMATTER_MISSING = "frontmatter-missing"
K_YAML_PARSE = "yaml-parse"
K_FRONTMATTER_NOT_MAP = "frontmatter-not-map"
K_MISSING_FIELD = "missing-field"
K_NAME_DIR_MISMATCH = "name-dir-mismatch"
K_NAME_PATTERN = "name-pattern"
K_LENGTH_LIMIT = "length-limit"
K_NON_SPEC_FIELD = "non-spec-field"
K_DANGLING_PAYLOAD_REF = "dangling-payload-ref"
K_UNDECLARED_DUPLICATE = "undeclared-duplicate"
K_REGISTRY_UNRESOLVED = "registry-unresolved"
K_STALE_WAIVER = "stale-waiver"
K_WAIVER_INVALID = "waiver-invalid"

SEVERITY_ERROR = "error"

# Only used when the config omits `payload_dirs:` entirely.
DEFAULT_PAYLOAD_DIRS = ("scripts", "references", "assets", "templates",
                        "hooks", "tests", "examples")

WAIVER_REQUIRED_FIELDS = ("klass", "registry", "path", "reason", "issue")


# =================================================================================
# Records
# =================================================================================

@dataclass(frozen=True)
class Finding:
    """One conformance violation. `path` is registry-relative (or, for
    undeclared-duplicate, the offending skill basename) so waivers can name it."""

    registry: str
    path: str
    klass: str
    message: str
    severity: str = SEVERITY_ERROR

    def as_dict(self) -> Dict[str, str]:
        return {
            "registry": self.registry,
            "path": self.path,
            "klass": self.klass,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass
class Registry:
    name: str
    layout: str
    configured_path: str
    path: Path
    optional: bool
    reason: str
    overridden: bool
    exists: bool
    order: int
    skills_scanned: int = 0

    @property
    def status(self) -> str:
        if self.exists:
            return "scanned"
        return "skipped" if (self.optional and self.reason) else "unresolved"


@dataclass
class Skill:
    registry: str
    skill_dir: Path
    skill_md: Path
    rel_path: str
    basename: str


@dataclass
class Candidate:
    """A path-shaped token lifted out of a SKILL.md body, where it came from ("fenced" or
    "prose"), and the rule (if any) that disqualified it from gating."""

    raw: str
    value: str
    origin: str
    dismissed_by: Optional[str]


@dataclass
class DuplicateLocation:
    registry: str
    path: str
    size: int
    sha256: str
    sha256_nocr: str


@dataclass
class DuplicateGroup:
    basename: str
    verdict: str
    locations: List[DuplicateLocation] = field(default_factory=list)


# =================================================================================
# Config + waivers
# =================================================================================

def load_yaml_mapping(path: Path, label: str) -> Dict[str, Any]:
    """Load a YAML file that must contain a mapping. Usage errors exit immediately —
    they are not census findings."""
    if not path.is_file():
        sys.exit(f"ERROR: {label} file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        sys.exit(f"ERROR: {label} file {path} is not valid YAML: {_yaml_error_summary(exc)}")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        sys.exit(f"ERROR: {label} file {path} must contain a YAML mapping")
    return data


def load_waivers(path: Path) -> Tuple[List[Dict[str, Any]], List[Finding], bool]:
    """Return (valid waivers, findings for malformed entries, file_present).

    A missing waivers file simply means "nothing is waived" — the tool still runs, and
    every finding is reported. A *malformed* entry is a finding, never a silent skip.
    """
    if not path.is_file():
        return [], [], False
    raw = load_yaml_mapping(path, "waivers")
    entries = raw.get("waivers")
    findings: List[Finding] = []
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        sys.exit(f"ERROR: waivers file {path} must map 'waivers:' to a list")

    waivers: List[Dict[str, Any]] = []
    for index, entry in enumerate(entries):
        label = f"waivers[{index}]"
        if not isinstance(entry, dict):
            findings.append(Finding("(waivers)", path.name, K_WAIVER_INVALID,
                                    f"{label} is not a mapping"))
            continue
        missing = [key for key in WAIVER_REQUIRED_FIELDS
                   if not str(entry.get(key) or "").strip()]
        if missing:
            findings.append(Finding(
                str(entry.get("registry") or "(waivers)"), path.name, K_WAIVER_INVALID,
                f"{label} is missing required non-empty field(s): {', '.join(missing)}"))
            continue
        waivers.append({
            "klass": str(entry["klass"]).strip(),
            "registry": str(entry["registry"]).strip(),
            "path": str(entry["path"]).strip(),
            "match": str(entry["match"]) if entry.get("match") is not None else None,
            "reason": str(entry["reason"]).strip(),
            "issue": str(entry["issue"]).strip(),
        })
    return waivers, findings, True


def waiver_label(waiver: Dict[str, Any]) -> str:
    parts = [f"klass={waiver['klass']}", f"registry={waiver['registry']}", f"path={waiver['path']}"]
    if waiver.get("match") is not None:
        parts.append(f"match={waiver['match']!r}")
    parts.append(f"issue={waiver['issue']}")
    return ", ".join(parts)


def waiver_matches(waiver: Dict[str, Any], finding: Finding) -> bool:
    if waiver["klass"] != finding.klass:
        return False
    if waiver["registry"] != finding.registry:
        return False
    if waiver["path"] != finding.path:
        return False
    if waiver.get("match") is not None and waiver["match"] not in finding.message:
        return False
    return True


def apply_waivers(
    findings: Sequence[Finding], waivers: Sequence[Dict[str, Any]]
) -> Tuple[List[Finding], List[Tuple[Finding, Dict[str, Any]]], List[Finding]]:
    """Split findings into (errors, waived, stale-waiver findings).

    Every waiver that matches a finding is marked used — including redundant overlapping
    waivers — so a correct-but-shadowed waiver is not mislabelled stale.
    """
    used = [False] * len(waivers)
    errors: List[Finding] = []
    waived: List[Tuple[Finding, Dict[str, Any]]] = []
    for finding in findings:
        matched: Optional[Dict[str, Any]] = None
        for index, waiver in enumerate(waivers):
            if waiver_matches(waiver, finding):
                used[index] = True
                if matched is None:
                    matched = waiver
        if matched is None:
            errors.append(finding)
        else:
            waived.append((finding, matched))

    stale = [
        Finding(waiver["registry"], waiver["path"], K_STALE_WAIVER,
                f"waiver matched no finding this run — delete it or fix its fields "
                f"({waiver_label(waiver)})")
        for waiver, was_used in zip(waivers, used) if not was_used
    ]
    return errors, waived, stale


# =================================================================================
# Registry resolution
# =================================================================================

def resolve_registries(
    config: Dict[str, Any],
    overrides: Dict[str, str],
    repo_root: Path = REPO_ROOT,
) -> Tuple[List[Registry], List[Finding], List[str]]:
    """Resolve every declared registry to a local path.

    Returns (registries, findings, skip_messages). A `--registry NAME=PATH` override wins
    over the config's `path:`; a relative configured path is resolved against `repo_root`
    (so `path: .` means the repo this script lives in).
    """
    root = Path(repo_root).expanduser().resolve()
    entries = config.get("registries")
    if not isinstance(entries, list) or not entries:
        sys.exit("ERROR: config must declare a non-empty 'registries:' list")

    unknown = sorted(set(overrides) - {str(e.get("name")) for e in entries if isinstance(e, dict)})
    if unknown:
        sys.exit(
            "ERROR: --registry names not declared in the config: " + ", ".join(unknown)
        )

    registries: List[Registry] = []
    findings: List[Finding] = []
    skips: List[str] = []
    for order, entry in enumerate(entries):
        if not isinstance(entry, dict):
            sys.exit(f"ERROR: registries[{order}] is not a mapping")
        name = str(entry.get("name") or "").strip()
        layout = str(entry.get("layout") or "").strip()
        if not name or not layout:
            sys.exit(f"ERROR: registries[{order}] needs a non-empty 'name:' and 'layout:'")
        configured = str(entry.get("path") or ".")
        optional = bool(entry.get("optional", False))
        reason = str(entry.get("reason") or "").strip()

        overridden = name in overrides
        raw_path = overrides[name] if overridden else configured
        candidate = Path(raw_path).expanduser()
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()

        registry = Registry(
            name=name,
            layout=layout,
            configured_path=configured,
            path=resolved,
            optional=optional,
            reason=reason,
            overridden=overridden,
            exists=resolved.is_dir(),
            order=order,
        )
        registries.append(registry)

        if registry.exists:
            continue
        if optional and reason:
            skips.append(f"SKIPPED: {name} — {reason}")
        else:
            detail = "declared optional but with no 'reason:'" if optional else "required"
            findings.append(Finding(
                name, configured, K_REGISTRY_UNRESOLVED,
                f"registry path does not exist: {resolved} ({detail}; declare "
                f"'optional: true' with a non-empty 'reason:' to skip it)"))
    return registries, findings, skips


def find_skills(registry: Registry) -> List[Skill]:
    """Every SKILL.md under the registry's layout glob. An existing-but-empty registry
    yields an empty list — that is a valid state, not an error."""
    if not registry.exists:
        return []
    skills: List[Skill] = []
    for skill_md in sorted(registry.path.glob(registry.layout)):
        if not skill_md.is_file():
            continue
        skill_dir = skill_md.parent
        skills.append(Skill(
            registry=registry.name,
            skill_dir=skill_dir,
            skill_md=skill_md,
            rel_path=skill_md.relative_to(registry.path).as_posix(),
            basename=skill_dir.name,
        ))
    return skills


# =================================================================================
# Frontmatter
# =================================================================================

def split_frontmatter(text: str) -> Tuple[Optional[str], str]:
    """Split a SKILL.md into (frontmatter YAML source, body).

    Returns (None, whole text) when there is no `---`-delimited leading block. Scanning
    the whole file as the body in that case is deliberate: a file that is missing its
    frontmatter is already flagged, and its payload references should still be checked
    rather than silently skipped.
    """
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[1:index]), "".join(lines[index + 1:])
    return None, text


def _yaml_error_summary(exc: yaml.YAMLError) -> str:
    text = str(exc).strip()
    first_line = text.splitlines()[0].strip() if text else exc.__class__.__name__
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return first_line
    # `mark` is relative to the frontmatter block, which starts on file line 2.
    return (f"{first_line} (frontmatter line {mark.line + 1}, column {mark.column + 1}; "
            f"SKILL.md line {mark.line + 2})")


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _value_length(value: Any) -> int:
    try:
        return len(value)
    except TypeError:
        return len(str(value))


# =================================================================================
# dangling-payload-ref
# =================================================================================

# The body is real CommonMark, so it gets a real CommonMark parse. A regex scanner
# cannot see `~~~` fences, fences indented inside a list item, info strings, or
# indented code blocks — and being wrong about which spans are code is exactly what
# decides whether a candidate gates.
_MARKDOWN = MarkdownIt("commonmark")

# Trailing characters stripped off a candidate before the rules are applied. `/` is
# deliberately NOT stripped — a trailing slash is the signal that the author meant a bare
# directory, and the trailing-slash rule needs to see it. Nor are `*?[]<>${}`, so a glob
# or a placeholder is dismissed *as such* rather than being truncated into something that
# looks like a real path.
_TRAILING_PUNCT = "),.:;\"'"

# The one non-structural dismissal. This check deliberately trades RECALL for PRECISION:
# only a token inside a fenced code block can gate the build, because prose mentions a
# payload path for many reasons that are not "this skill ships this file" — it names
# another skill's script, a path in a different repo, or an illustrative example. Those
# produced a ~4-in-5 false-positive rate, and a lint that noisy gets muted. The candidates
# given up by that trade are not lost: each one lands in the `--list-findings` dismissal
# log under this reason, which is where the recall gap stays inspectable.
PROSE_ONLY_RULE = "prose-only (not in a fenced block)"


def _walk_inline(token, prose: List[str]) -> None:
    """Collect backtick spans and link targets out of an inline token's children.

    `code_inline` / `link_open` are never top-level tokens — they live in the `.children`
    of an `inline` token, and children can nest (a link inside emphasis, say), so the walk
    recurses.
    """
    for child in token.children or ():
        if child.type == "code_inline":
            prose.extend(child.content.split())
        elif child.type == "link_open":
            href = child.attrGet("href")
            if href:
                prose.append(href)
        if child.children:
            _walk_inline(child, prose)


def split_code_regions(body: str) -> Tuple[List[str], List[str]]:
    """Return (whitespace tokens of every code BLOCK, prose code-span/link targets).

    Both CommonMark block-code forms count as "code the reader is meant to run": `fence`
    (``` or ~~~, at any indent, including inside a list item) and `code_block` (the
    four-space indented form). Everything else in the document is prose.
    """
    fenced: List[str] = []
    prose: List[str] = []
    for token in _MARKDOWN.parse(body):
        if token.type in ("fence", "code_block"):
            fenced.extend(token.content.split())
        elif token.type == "inline":
            _walk_inline(token, prose)
    return fenced, prose


def normalise_candidate(raw: str) -> str:
    value = raw.strip().rstrip(_TRAILING_PUNCT)
    while value.startswith("./"):
        value = value[2:]
    return value


def dismissal_rule(
    value: str, payload_dirs: Sequence[str], *, in_fenced_block: bool = True
) -> Optional[str]:
    """The first rule that disqualifies `value` as a gating payload reference, or None.

    The structural rules run first and the prose-only gate runs LAST, on purpose: that way
    the `prose-only` bucket in the dismissal log holds exactly the candidates that would
    otherwise have been findings — i.e. the recall gap itself — instead of being flooded
    by every bare word in the document. Within the structural rules the order is chosen so
    the most specific, most informative reason wins.
    """
    if not value:
        return "empty"
    if "://" in value:
        return "url-scheme"
    if value.startswith("/"):
        return "absolute-path"
    if value.startswith("~"):
        return "home-relative"
    if value.startswith("#"):
        return "anchor"
    segments = value.split("/")
    if any(segment == ".." for segment in segments):
        return "parent-traversal"
    if any(char in value for char in "*?[]"):
        return "glob-metacharacter"
    if any(char in value for char in "<>${}"):
        return "placeholder"
    if "/" not in value:
        return "no-slash"
    if segments[0] not in payload_dirs:
        return "not-payload-dir"
    if value.endswith("/"):
        return "trailing-slash"          # a bare directory mention, not a file reference
    if value.startswith("."):
        return "dot-prefixed"
    if not in_fenced_block:
        return PROSE_ONLY_RULE
    return None


def extract_candidates(body: str, payload_dirs: Sequence[str]) -> List[Candidate]:
    """Every path-shaped token in the body, deduped, each tagged with origin and rule.

    Code blocks are tokenised on whitespace, so a command line like
    `python scripts/next_break.py --schedule regular` yields the path token rather than
    the whole command. A value seen in ANY code block counts as fenced even when it also
    appears in prose — appearing in a runnable command is the evidence that makes it gate.
    """
    fenced_raws, prose_raws = split_code_regions(body)

    fenced_values = {normalise_candidate(raw) for raw in fenced_raws if raw.strip()}

    seen: set = set()
    candidates: List[Candidate] = []
    for raw in fenced_raws + prose_raws:
        if not raw.strip():
            continue
        value = normalise_candidate(raw)
        if value in seen:
            continue
        seen.add(value)
        in_fenced = value in fenced_values
        candidates.append(Candidate(
            raw=raw,
            value=value,
            origin="fenced" if in_fenced else "prose",
            dismissed_by=dismissal_rule(value, payload_dirs, in_fenced_block=in_fenced),
        ))
    return candidates


def check_payload_refs(
    skill: Skill, body: str, payload_dirs: Sequence[str]
) -> Tuple[List[Finding], List[Candidate]]:
    """Findings for gating payload references that resolve to nothing on disk.

    Only a candidate that survives every rule in `dismissal_rule` gates — in practice a
    path token inside a fenced code block, which is the shape of "run this file that ships
    with the skill". A candidate that resolves to a directory is NOT dangling, so existence
    (not file-ness) is the test.
    """
    findings: List[Finding] = []
    candidates = extract_candidates(body, payload_dirs)
    for candidate in candidates:
        if candidate.dismissed_by is not None:
            continue
        if (skill.skill_dir / candidate.value).exists():
            continue
        findings.append(Finding(
            skill.registry, skill.rel_path, K_DANGLING_PAYLOAD_REF,
            f"body references '{candidate.value}' but {skill.basename}/{candidate.value} "
            f"does not exist"))
    return findings, candidates


# =================================================================================
# Per-skill checks
# =================================================================================

def check_skill(skill: Skill, config: Dict[str, Any]) -> Tuple[List[Finding], List[Candidate]]:
    """Run every per-file check against one SKILL.md."""
    required_fields = config.get("required_fields")
    name_pattern = config.get("name_pattern")
    max_lengths = config.get("max_lengths")
    known_fields = config.get("known_fields")
    payload_dirs = config.get("payload_dirs")
    if payload_dirs is None:
        payload_dirs = list(DEFAULT_PAYLOAD_DIRS)

    findings: List[Finding] = []
    text = skill.skill_md.read_bytes().decode("utf-8", errors="replace")
    frontmatter_src, body = split_frontmatter(text)

    payload_findings, candidates = check_payload_refs(skill, body, payload_dirs)
    findings.extend(payload_findings)

    if frontmatter_src is None:
        findings.append(Finding(
            skill.registry, skill.rel_path, K_FRONTMATTER_MISSING,
            "no '---'-delimited YAML frontmatter block at the top of the file"))
        return findings, candidates

    try:
        parsed = yaml.safe_load(frontmatter_src)
    except yaml.YAMLError as exc:
        findings.append(Finding(
            skill.registry, skill.rel_path, K_YAML_PARSE,
            f"frontmatter is not valid YAML: {_yaml_error_summary(exc)}"))
        return findings, candidates

    if not isinstance(parsed, dict):
        findings.append(Finding(
            skill.registry, skill.rel_path, K_FRONTMATTER_NOT_MAP,
            f"frontmatter parsed as {type(parsed).__name__}, expected a mapping"))
        return findings, candidates

    if isinstance(required_fields, (list, tuple)):
        for name in required_fields:
            if name not in parsed or _is_empty(parsed[name]):
                findings.append(Finding(
                    skill.registry, skill.rel_path, K_MISSING_FIELD,
                    f"required frontmatter field '{name}' is absent or empty"))

    name_value = parsed.get("name")
    if not _is_empty(name_value):
        name_text = name_value if isinstance(name_value, str) else str(name_value)
        if name_text.strip() != skill.basename:
            findings.append(Finding(
                skill.registry, skill.rel_path, K_NAME_DIR_MISMATCH,
                f"frontmatter name '{name_text.strip()}' does not match the skill "
                f"directory basename '{skill.basename}'"))
        if isinstance(name_pattern, str) and name_pattern:
            if re.search(name_pattern, name_text.strip()) is None:
                findings.append(Finding(
                    skill.registry, skill.rel_path, K_NAME_PATTERN,
                    f"frontmatter name '{name_text.strip()}' does not match the configured "
                    f"name_pattern {name_pattern!r}"))

    if isinstance(max_lengths, dict):
        for name, limit in max_lengths.items():
            if name not in parsed or parsed[name] is None:
                continue
            if not isinstance(limit, int):
                continue
            length = _value_length(parsed[name])
            if length > limit:
                findings.append(Finding(
                    skill.registry, skill.rel_path, K_LENGTH_LIMIT,
                    f"frontmatter field '{name}' has length {length}, which exceeds the "
                    f"configured limit of {limit}"))

    if isinstance(known_fields, (list, tuple)):
        allowed = {str(item) for item in known_fields}
        for key in sorted(str(k) for k in parsed):
            if key not in allowed:
                findings.append(Finding(
                    skill.registry, skill.rel_path, K_NON_SPEC_FIELD,
                    f"frontmatter key '{key}' is not in the configured known_fields"))

    return findings, candidates


# =================================================================================
# Cross-registry basename census
# =================================================================================

def build_duplicate_groups(skills: Sequence[Skill]) -> List[DuplicateGroup]:
    """Group skill basenames that appear in more than one registry.

    A group is a `mirror` when every copy's CR-stripped SKILL.md hash matches — the CRLF
    trap: a checkout with Windows line endings is byte-different but content-identical,
    and hashing the raw bytes would report every such copy as drifted.
    """
    by_basename: Dict[str, List[Skill]] = {}
    for skill in skills:
        by_basename.setdefault(skill.basename, []).append(skill)

    groups: List[DuplicateGroup] = []
    for basename in sorted(by_basename):
        members = by_basename[basename]
        if len({member.registry for member in members}) < 2:
            continue
        locations: List[DuplicateLocation] = []
        for member in members:
            raw = member.skill_md.read_bytes()
            locations.append(DuplicateLocation(
                registry=member.registry,
                path=member.rel_path,
                size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                sha256_nocr=hashlib.sha256(raw.replace(b"\r", b"")).hexdigest(),
            ))
        verdict = "mirror" if len({loc.sha256_nocr for loc in locations}) == 1 else "fork"
        groups.append(DuplicateGroup(basename=basename, verdict=verdict, locations=locations))
    return groups


def duplicate_findings(groups: Sequence[DuplicateGroup]) -> List[Finding]:
    """One finding per duplicate group, attached to the first registry holding it."""
    findings: List[Finding] = []
    for group in groups:
        rendered = "; ".join(
            f"{loc.registry}:{loc.path} ({loc.size} B, nocr sha256 {loc.sha256_nocr[:12]})"
            for loc in group.locations
        )
        findings.append(Finding(
            group.locations[0].registry, group.basename, K_UNDECLARED_DUPLICATE,
            f"skill basename '{group.basename}' exists in "
            f"{len({loc.registry for loc in group.locations})} registries — verdict: "
            f"{group.verdict} — {rendered}"))
    return findings


# =================================================================================
# Run
# =================================================================================

@dataclass
class Report:
    config_path: Path
    waivers_path: Path
    waivers_present: bool
    waiver_count: int
    registries: List[Registry]
    skips: List[str]
    errors: List[Finding]
    waived: List[Tuple[Finding, Dict[str, Any]]]
    duplicate_groups: List[DuplicateGroup]
    dismissed: List[Tuple[Skill, List[Candidate]]]
    unresolved_required: int
    stale_waiver_count: int

    @property
    def scanned_registries(self) -> List[Registry]:
        return [registry for registry in self.registries if registry.exists]

    @property
    def skills_scanned(self) -> int:
        return sum(registry.skills_scanned for registry in self.registries)

    @property
    def exit_code(self) -> int:
        if self.errors or self.stale_waiver_count or self.unresolved_required:
            return 1
        return 0


def run(
    config_path: Path,
    waivers_path: Path,
    overrides: Dict[str, str],
    repo_root: Path = REPO_ROOT,
) -> Report:
    config = load_yaml_mapping(config_path, "config")
    pattern = config.get("name_pattern")
    if isinstance(pattern, str) and pattern:
        try:
            re.compile(pattern)
        except re.error as exc:
            sys.exit(f"ERROR: config name_pattern is not a valid regex: {exc}")
    waivers, waiver_findings, waivers_present = load_waivers(waivers_path)
    registries, findings, skips = resolve_registries(config, overrides, repo_root)
    findings = list(findings) + waiver_findings
    unresolved_required = sum(1 for registry in registries if registry.status == "unresolved")

    all_skills: List[Skill] = []
    dismissed: List[Tuple[Skill, List[Candidate]]] = []
    for registry in registries:
        skills = find_skills(registry)
        registry.skills_scanned = len(skills)
        all_skills.extend(skills)
        for skill in skills:
            skill_findings, candidates = check_skill(skill, config)
            findings.extend(skill_findings)
            dismissed.append((skill, [c for c in candidates if c.dismissed_by is not None]))

    duplicate_groups = build_duplicate_groups(all_skills)
    findings.extend(duplicate_findings(duplicate_groups))

    order = {registry.name: registry.order for registry in registries}
    findings.sort(key=lambda f: (order.get(f.registry, len(order)), f.path, f.klass, f.message))

    errors, waived, stale = apply_waivers(findings, waivers)
    errors.extend(stale)

    return Report(
        config_path=config_path,
        waivers_path=waivers_path,
        waivers_present=waivers_present,
        waiver_count=len(waivers),
        registries=registries,
        skips=skips,
        errors=errors,
        waived=waived,
        duplicate_groups=duplicate_groups,
        dismissed=dismissed,
        unresolved_required=unresolved_required,
        stale_waiver_count=len(stale),
    )


# =================================================================================
# Output
# =================================================================================

def _counts_by_registry(report: Report) -> Tuple[Dict[str, int], Dict[str, int]]:
    errors: Dict[str, int] = {}
    waived: Dict[str, int] = {}
    for finding in report.errors:
        errors[finding.registry] = errors.get(finding.registry, 0) + 1
    for finding, _ in report.waived:
        waived[finding.registry] = waived.get(finding.registry, 0) + 1
    return errors, waived


def render_text(report: Report, list_findings: bool) -> str:
    error_counts, waived_counts = _counts_by_registry(report)
    lines: List[str] = []
    lines.append(f"config:  {report.config_path}")
    waivers_note = "" if report.waivers_present else " (absent — nothing waived)"
    lines.append(f"waivers: {report.waivers_path} — "
                 f"{report.waiver_count} waiver(s){waivers_note}")
    lines.append("")
    for message in report.skips:
        lines.append(message)
    if report.skips:
        lines.append("")

    lines.append("REGISTRIES")
    for registry in report.registries:
        lines.append(
            f"  {registry.name:<22} {registry.status:<10} {str(registry.path):<48} "
            f"{registry.skills_scanned:>4} skills  "
            f"{error_counts.get(registry.name, 0):>4} findings  "
            f"{waived_counts.get(registry.name, 0):>4} waived"
        )
    lines.append("")

    lines.append(f"ERRORS ({len(report.errors)})")
    for finding in report.errors:
        lines.append(f"  [{finding.klass}] {finding.registry} :: {finding.path}")
        lines.append(f"      {finding.message}")
    lines.append("")

    lines.append(f"WAIVED ({len(report.waived)})")
    for finding, waiver in report.waived:
        lines.append(f"  [{finding.klass}] {finding.registry} :: {finding.path}")
        lines.append(f"      {finding.message}")
        lines.append(f"      waived: {waiver['reason']} ({waiver['issue']})")
    lines.append("")

    if report.skips:
        lines.append(f"SKIPPED ({len(report.skips)})")
        for message in report.skips:
            lines.append(f"  {message[len('SKIPPED: '):]}")
        lines.append("")

    if list_findings:
        lines.append("DISMISSED PAYLOAD CANDIDATES")
        any_dismissed = False
        for skill, candidates in report.dismissed:
            if not candidates:
                continue
            any_dismissed = True
            lines.append(f"  {skill.registry} :: {skill.rel_path}")
            for candidate in candidates:
                lines.append(f"      {candidate.dismissed_by:<20} {candidate.value!r}")
        if not any_dismissed:
            lines.append("  (none)")
        lines.append("")

    if report.exit_code == 0:
        lines.append(
            f"OK: {report.skills_scanned} skills across {len(report.scanned_registries)} "
            f"registries — 0 findings, {len(report.waived)} waived."
        )
    else:
        lines.append(
            f"FAILED: {report.skills_scanned} skills across "
            f"{len(report.scanned_registries)} registries — {len(report.errors)} finding(s), "
            f"{len(report.waived)} waived, {report.stale_waiver_count} stale waiver(s), "
            f"{report.unresolved_required} unresolved required registry(ies)."
        )
    return "\n".join(lines)


def render_json(report: Report, list_findings: bool) -> str:
    error_counts, waived_counts = _counts_by_registry(report)
    payload: Dict[str, Any] = {
        "config": str(report.config_path),
        "waivers": str(report.waivers_path),
        "waivers_present": report.waivers_present,
        "waivers_declared": report.waiver_count,
        "registries": [
            {
                "name": registry.name,
                "status": registry.status,
                "configured_path": registry.configured_path,
                "path": str(registry.path),
                "layout": registry.layout,
                "optional": registry.optional,
                "reason": registry.reason,
                "overridden": registry.overridden,
                "skills_scanned": registry.skills_scanned,
                "findings": error_counts.get(registry.name, 0),
                "waived": waived_counts.get(registry.name, 0),
            }
            for registry in report.registries
        ],
        "skills_scanned": report.skills_scanned,
        "registries_scanned": len(report.scanned_registries),
        "skipped": [
            {"registry": registry.name, "reason": registry.reason}
            for registry in report.registries if registry.status == "skipped"
        ],
        "unresolved_required": report.unresolved_required,
        "stale_waivers": report.stale_waiver_count,
        "findings": [finding.as_dict() for finding in report.errors],
        "waived": [
            dict(finding.as_dict(), waiver=waiver) for finding, waiver in report.waived
        ],
        "duplicate_groups": [
            {
                "basename": group.basename,
                "verdict": group.verdict,
                "locations": [
                    {
                        "registry": loc.registry,
                        "path": loc.path,
                        "size": loc.size,
                        "sha256": loc.sha256,
                        "sha256_nocr": loc.sha256_nocr,
                    }
                    for loc in group.locations
                ],
            }
            for group in report.duplicate_groups
        ],
        "exit_code": report.exit_code,
    }
    if list_findings:
        payload["dismissed_candidates"] = [
            {
                "registry": skill.registry,
                "path": skill.rel_path,
                "candidates": [
                    {"raw": c.raw, "value": c.value, "origin": c.origin,
                     "dismissed_by": c.dismissed_by}
                    for c in candidates
                ],
            }
            for skill, candidates in report.dismissed if candidates
        ]
    return json.dumps(payload, indent=2)


# =================================================================================
# CLI
# =================================================================================

def parse_overrides(values: Iterable[str]) -> Dict[str, str]:
    overrides: Dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            sys.exit(f"ERROR: --registry expects NAME=PATH, got {value!r}")
        name, _, path = value.partition("=")
        name, path = name.strip(), path.strip()
        if not name or not path:
            sys.exit(f"ERROR: --registry expects a non-empty NAME and PATH, got {value!r}")
        overrides[name] = path
    return overrides


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Conformance census over every declared skills registry.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), metavar="PATH",
                        help="registry config YAML (default: scripts/skills_registries.yml)")
    parser.add_argument("--waivers", default=str(DEFAULT_WAIVERS), metavar="PATH",
                        help="declared exemptions YAML (default: scripts/skills_waivers.yml)")
    parser.add_argument("--registry", action="append", default=[], metavar="NAME=PATH",
                        help="override a declared registry's path (repeatable); resolved the "
                             "same way as a configured path — relative to the repo root "
                             "unless absolute")
    parser.add_argument("--json", action="store_true",
                        help="emit a single JSON object and nothing else")
    parser.add_argument("--list-findings", action="store_true",
                        help="also list the payload candidates that were extracted and "
                             "dismissed, with the rule that dismissed each one")
    args = parser.parse_args(argv)

    report = run(
        config_path=Path(args.config).expanduser(),
        waivers_path=Path(args.waivers).expanduser(),
        overrides=parse_overrides(args.registry),
    )
    if args.json:
        print(render_json(report, args.list_findings))
    else:
        print(render_text(report, args.list_findings))
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
