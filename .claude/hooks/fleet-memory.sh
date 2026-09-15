#!/usr/bin/env bash
# fleet-memory.sh — deliver the fleet's managed guidance ONCE per session.
#
# WHY THIS EXISTS
# ---------------
# The managed guidance used to be inlined into every repo's AGENTS.md, which
# Claude Code imports from that repo's CLAUDE.md. A session with N repos
# attached therefore loaded N identical copies. Measured 2026-08-29 on a hosted
# multi-repo session: 37 memory files, 332.3k tokens — a third of a 1M window —
# almost all of it ONE ~52 kB managed block repeated 19 times.
#
# User-level memory (~/.claude/CLAUDE.md) is read ONCE per session no matter how
# many repos are attached. That is where the fleet-wide block belongs; each repo
# keeps only what is genuinely its own, plus a small stub.
#
# THE THREE MEASUREMENTS THIS DESIGN RESTS ON
# -------------------------------------------
# Local CLI 2.1.251, tool-free single-turn probes, baseline context 34.5k:
#
#   1. An `@import` inlines ONLY when the target resolves INSIDE the project
#      tree. A nested in-tree import works (53,441 tokens, canary retrieved);
#      `@~/.claude/...` and out-of-tree absolute paths silently load NOTHING
#      (34,4xx, canary absent) — no error, no warning. So "one shared file that
#      every repo imports" is NOT available, which is why the content is
#      delivered to USER memory instead of imported from a shared path.
#   2. A SessionStart hook runs BEFORE memory is assembled. With no
#      ~/.claude/CLAUDE.md at launch, this hook wrote one and the SAME session
#      read it (53,439 tokens, canary retrieved). That is what makes the first
#      session in a fresh container correct rather than one session late.
#   3. The hosted harness does the same. Verified in a cloud session on
#      _agent-guidance@claude/agents-md-redundancy-uvw9am: the canary was in
#      context at session start, attributed to
#      "Contents of /root/.claude/CLAUDE.md (user's private global instructions
#      for all projects)". Local-only evidence would not have settled this,
#      because the hosted harness assembles memory itself.
#
# WHAT IT WRITES
# --------------
# A MARKED BLOCK, never the whole file, to TWO surfaces — one per agent CLI
# that reads a global instructions file:
#
#   ~/.claude/CLAUDE.md   (or $CLAUDE_CONFIG_DIR/CLAUDE.md) — Claude Code's
#                         user memory. Always.
#   ~/.codex/AGENTS.md    (or $CODEX_HOME/AGENTS.md) — Codex's global USER
#                         instructions. Normal hook mode writes it only when
#                         that directory already exists; explicit Codex Cloud
#                         mode creates the home because setup is the lifecycle.
#
# The Codex destination is the GLOBAL one on purpose. Codex reads the AGENTS.md
# chain from the project root down to cwd under a running byte budget,
# `project_doc_max_bytes`, default 32768 — and a file that does not fit is CUT
# at that byte with only a `tracing::warn!("project doc exceeds remaining
# budget; truncating")` in `codex-rs/core/src/agents_md.rs` that no ordinary
# session ever sees (codex-cli 0.154.0, measured 2026-09-14 on cms-platform:
# `codex debug prompt-input` rendered a 55,788-byte AGENTS.md (a
# feature-branch checkout; `main`'s copy was 71,794 bytes) as exactly
# 32,768 bytes, ending mid-heading). Putting ~50 kB of fleet guidance into a
# repo's own AGENTS.md would therefore silently evict that repo's own
# additions. The global file is loaded by a different path —
# `codex-rs/codex-home/src/instructions/mod.rs`, as USER instructions — and is
# NOT counted against that budget, which is why the guidance goes there while
# the repo stub stays the floor inside the repo. See
# docs/decisions/0012-codex-gets-the-guidance-as-user-instructions.md.
#
# A developer's own ~/.claude/CLAUDE.md or ~/.codex/AGENTS.md is theirs;
# everything outside the markers is preserved byte-for-byte in both.
#
# CODEX CLOUD MODE
# ----------------
# `--codex-cloud` is called directly from environment setup and maintenance.
# It exits before the legacy hook path below, targets Codex only, selects a
# nonempty AGENTS.override.md when present, persists its verdict inside the
# managed block, and returns nonzero on a delivery failure. It uses Python's
# byte-oriented and atomic file operations so content outside an existing
# block is unchanged and a failed replacement never truncates the destination.
#
# STDIN AND STDOUT
# ----------------
# With no arguments Claude Code runs this as a SessionStart hook; so does Codex,
# through a user-level entry registered once per machine by
# `scripts/register-codex-hook.sh`. Codex passes the hook event as one JSON
# object on stdin — this script never reads stdin, which is part of what makes
# one file correct for both harnesses — and adds a command hook's plain-text
# stdout to the session as developer context. So the single verdict line below
# is what a Codex session sees, exactly as a Claude session does.
#
# In its normal hook mode it always exits 0. A guidance delivery that breaks
# the session is worse than
# one that degrades, and the repo stub is the floor: every repo still carries
# the load-bearing rules inline, so a DEGRADED session is diminished, not blind.
# The verdict line below is what keeps that degradation VISIBLE rather than
# silent — the failure mode this hook most needs to avoid is working quietly
# until the day it doesn't. A failure at ONE destination never stops the other:
# the run finishes every destination it can and then reports DEGRADED naming
# the one that failed.
set -uo pipefail

CODEX_CLOUD=0
case "$#:${1-}" in
    0:) ;;
    1:--codex-cloud) CODEX_CLOUD=1 ;;
    *)
        echo "fleet-guidance: DEGRADED — unknown argument; expected no arguments or --codex-cloud"
        exit 2
        ;;
esac

BEGIN_MARK='<!-- BEGIN FLEET GUIDANCE (managed by _agent-guidance) — DO NOT EDIT -->'
END_MARK='<!-- END FLEET GUIDANCE -->'

# Payload ships beside this hook, deliberately OUTSIDE any memory-file path
# (only CLAUDE.md / AGENTS.md are auto-loaded), so it costs zero always-on
# context in the repo that carries it.
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || HOOK_DIR=""
PAYLOAD="${FLEET_GUIDANCE_PAYLOAD:-$HOOK_DIR/fleet-guidance.md}"
CODEX_DEST_DIR="${CODEX_HOME:-$HOME/.codex}"

# One spelling policy serves both execution modes. Character classes keep the
# comparison case-insensitive without adding an external command to the
# shell-only legacy path.
case "${FLEET_GUIDANCE_SKIP:-}" in
    ""|0|[Ff][Aa][Ll][Ss][Ee]|[Nn][Oo]|[Oo][Ff][Ff]) FLEET_GUIDANCE_SKIP_ENABLED=0 ;;
    *) FLEET_GUIDANCE_SKIP_ENABLED=1 ;;
esac

if [ "$CODEX_CLOUD" -eq 1 ]; then
    cloud_result="$(python3 - \
        "$CODEX_DEST_DIR" "$PAYLOAD" "$BEGIN_MARK" "$END_MARK" \
        "$FLEET_GUIDANCE_SKIP_ENABLED" 2>/dev/null <<'PY'
import hashlib
import os
import pathlib
import stat
import sys
import tempfile


class Refusal(Exception):
    pass


def refuse(reason):
    raise Refusal(reason)


def marker_span(raw, begin, end):
    begins = []
    ends = []
    offset = 0
    for line in raw.splitlines(keepends=True):
        bare = line.rstrip(b"\r\n")
        if bare == begin:
            begins.append(offset)
        if bare == end:
            ends.append(offset + len(line))
        offset += len(line)
    if raw.count(begin) != len(begins) or raw.count(end) != len(ends):
        refuse("effective global instructions have malformed fleet-guidance markers")
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        refuse("effective global instructions have malformed fleet-guidance markers")
    return begins[0], ends[0]


def regular_readable(path, label):
    try:
        info = path.lstat()
    except OSError:
        refuse(f"could not inspect {label}")
    if not stat.S_ISREG(info.st_mode) or not os.access(path, os.R_OK):
        refuse(f"{label} is not a readable regular file")
    return info


try:
    codex_home = pathlib.Path(sys.argv[1])
    payload_path = pathlib.Path(sys.argv[2])
    begin = sys.argv[3].encode()
    end = sys.argv[4].encode()
    skip = sys.argv[5] == "1"

    payload = b""
    if not skip:
        try:
            payload = payload_path.read_bytes()
        except OSError:
            refuse("fleet-guidance payload is missing or unreadable")
        if not payload:
            refuse("fleet-guidance payload is empty")
        if begin in payload or end in payload or any(
            line.startswith(b"fleet-guidance:") for line in payload.splitlines()
        ):
            refuse("fleet-guidance payload contains a reserved marker or verdict")

    try:
        codex_home.mkdir(parents=True, exist_ok=True)
    except OSError:
        refuse("could not create Codex home")
    if not codex_home.is_dir():
        refuse("Codex home is not a directory")

    override = codex_home / "AGENTS.override.md"
    agents = codex_home / "AGENTS.md"
    if os.path.lexists(override):
        override_info = regular_readable(override, "AGENTS.override.md")
        target = override if override_info.st_size > 0 else agents
    else:
        target = agents
    label = "~/.codex/AGENTS.override.md" if target == override else "~/.codex/AGENTS.md"

    original = b""
    original_mode = None
    if os.path.lexists(target):
        target_info = regular_readable(target, "effective global instructions")
        original_mode = stat.S_IMODE(target_info.st_mode)
        try:
            original = target.read_bytes()
        except OSError:
            refuse("could not read effective global instructions")

    span = marker_span(original, begin, end)
    if skip:
        body = (
            b"fleet-guidance: skipped (FLEET_GUIDANCE_SKIP set) "
            + "— Codex Cloud setup and maintenance".encode()
            + b"\n"
        )
        verdict = f"fleet-guidance: skipped (FLEET_GUIDANCE_SKIP set) — persisted in {label}"
    else:
        version = hashlib.sha256(payload).hexdigest()[:8]
        payload_body = payload if payload.endswith(b"\n") else payload + b"\n"
        persisted = (
            f"fleet-guidance: installed (v{version}, {len(payload)} bytes) "
            "— Codex Cloud setup and maintenance"
        ).encode()
        body = (
            f"<!-- fleet-guidance-version: {version} -->\n".encode()
            + persisted
            + b"\n"
            + payload_body
        )
    block = begin + b"\n" + body + end + b"\n"

    if span is None:
        separator = b"" if not original or original.endswith(b"\n") else b"\n"
        candidate = original + separator + block
    else:
        candidate = original[:span[0]] + block + original[span[1]:]

    changed = candidate != original
    if changed:
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=".fleet-guidance-", dir=target.parent)
            if original_mode is not None:
                os.fchmod(fd, original_mode)
            with os.fdopen(fd, "wb") as stream:
                stream.write(candidate)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
        except OSError:
            refuse("could not write effective global instructions")
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    if skip:
        print(verdict)
    elif changed:
        print(f"fleet-guidance: installed (v{version}, {len(payload)} bytes) -> {label}")
    else:
        print(f"fleet-guidance: current (v{version}, {len(payload)} bytes) — {label}")
except Refusal as exc:
    print(f"fleet-guidance: DEGRADED — {exc}")
    raise SystemExit(1)
except Exception:
    print("fleet-guidance: DEGRADED — unexpected Cloud setup failure")
    raise SystemExit(1)
PY
)"
    cloud_status=$?
    if [ -z "$cloud_result" ]; then
        cloud_result="fleet-guidance: DEGRADED — Python 3 is unavailable for Codex Cloud setup"
        cloud_status=1
    fi
    printf '%s\n' "$cloud_result"
    exit "$cloud_status"
fi

# ── Destinations ───────────────────────────────────────────────────────────
#
# The LABELS are the canonical `~/…` spellings rather than the resolved paths:
# they are what the verdict names, and a test overriding CLAUDE_CONFIG_DIR or
# CODEX_HOME should not change the sentence an operator reads. Every FAILURE
# message names the resolved path instead, because those have to be actionable.
CLAUDE_DEST_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CLAUDE_DEST="$CLAUDE_DEST_DIR/CLAUDE.md"
CODEX_DEST="$CODEX_DEST_DIR/AGENTS.md"
# shellcheck disable=SC2088  # the tilde is LITERAL here on purpose: these two
# are prose shown to a human, never paths anything opens. The paths are the
# four lines above.
CLAUDE_LABEL="~/.claude/CLAUDE.md"
# shellcheck disable=SC2088
CODEX_LABEL="~/.codex/AGENTS.md"

# Claude Code is always in play — its config dir is created if absent, as it
# always was. Codex is in play only when its home ALREADY EXISTS: creating
# ~/.codex on a machine that has never run Codex would manufacture config for a
# tool that is not installed, and the empty directory would then read as
# "Codex is set up here" to everything that probes for it.
DEST_PATHS=("$CLAUDE_DEST")
DEST_LABELS=("$CLAUDE_LABEL")
if [ -d "$CODEX_DEST_DIR" ]; then
    DEST_PATHS+=("$CODEX_DEST")
    DEST_LABELS+=("$CODEX_LABEL")
fi

# Joined once, for the verdict. Only destinations that were in play appear, so
# a machine without Codex reads exactly the line it read before this hook
# learned about Codex at all.
dest_list() {
    local out="" l
    for l in "${DEST_LABELS[@]}"; do out="${out:+$out, }$l"; done
    printf '%s' "$out"
}

FAILURES=""
record_failure() { FAILURES="${FAILURES:+$FAILURES; }$1"; }

TMP_FILES=()
# shellcheck disable=SC2317  # reached through the EXIT trap below, not by a call
cleanup_tmp() {
    [ "${#TMP_FILES[@]}" -eq 0 ] && return 0
    local t
    for t in "${TMP_FILES[@]}"; do rm -f "$t" "$t.raw"; done
    return 0
}
trap cleanup_tmp EXIT

new_tmp() {
    local t
    t="$(mktemp 2>/dev/null)" || return 1
    TMP_FILES+=("$t")
    printf '%s' "$t"
}

# A fault that leaves NOTHING deliverable anywhere — no payload, no hook
# directory to find one in. Nothing per-destination belongs here: those are
# recorded and reported together at the end, so one broken surface cannot cost
# the others their delivery.
degraded() {
    echo "fleet-guidance: DEGRADED — $1. Repo stub only; read the fleet guidance in _agent-guidance/agents-md/base.md before non-trivial work."
    exit 0
}

# Write $2 with $1's managed block stripped out and everything else verbatim.
# Returns non-zero with the reason in STRIP_ERR; the caller decides what that
# costs — one destination, never the run.
#
# NEVER OVERWRITE A FILE WHOSE CURRENT CONTENTS COULD NOT BE READ. On a durable
# machine ~/.claude/CLAUDE.md is the developer's own global memory — and
# ~/.codex/AGENTS.md their own global Codex instructions — and this hook is a
# guest in both. An earlier draft fell back to an EMPTY strip result when the
# read failed, then appended the block to that emptiness and copied it over the
# top — so a file that was writable but not readable (mode 0200, a mount quirk,
# an ACL) was silently REPLACED by the fleet block alone, and the verdict still
# said `installed`. Measured: a personal CLAUDE.md destroyed, with a success
# line. Every unreadable-or-unparseable shape degrades instead.
strip_managed_block() {
    local dest="$1" out="$2"
    STRIP_ERR=""
    if [ ! -e "$dest" ]; then : > "$out"; return 0; fi
    if [ ! -f "$dest" ]; then
        STRIP_ERR="$dest exists but is not a regular file — refusing to replace it"
        return 1
    fi
    if [ ! -r "$dest" ]; then
        STRIP_ERR="$dest exists but is not readable — refusing to overwrite content I cannot preserve"
        return 1
    fi

    if ! BEGIN_MARK="$BEGIN_MARK" END_MARK="$END_MARK" awk '
        BEGIN { b = ENVIRON["BEGIN_MARK"]; e = ENVIRON["END_MARK"]; skip = 0 }
        index($0, b) == 1 { skip = 1; next }
        index($0, e) == 1 { skip = 0; next }
        !skip { print }
    ' "$dest" > "$out.raw" 2>/dev/null; then
        rm -f "$out.raw"
        STRIP_ERR="could not read or parse $dest — refusing to overwrite content I cannot preserve"
        return 1
    fi

    # Drop trailing blank lines so repeated runs cannot grow the file, and so a
    # file whose ONLY content was the block collapses to empty rather than
    # accumulating a blank line per run.
    local kept; kept="$(cat "$out.raw" 2>/dev/null)"
    if [ -n "$kept" ]; then printf '%s\n' "$kept" > "$out"; else : > "$out"; fi
    rm -f "$out.raw"
    return 0
}

# ── Opt-out ────────────────────────────────────────────────────────────────
#
# FLEET_GUIDANCE_SKIP exists because user memory is GLOBAL on a durable
# machine. ~/.claude/CLAUDE.md is read in EVERY Claude session on that box, and
# ~/.codex/AGENTS.md in every Codex session in every project on it, so once a
# fleet repo has been opened once, the guidance rides along into unrelated
# projects too. That is a fair trade for some machines and not others, and it
# is the developer's call, not this hook's. ONE flag governs BOTH surfaces,
# because the reason to opt out is the same reason on each.
#
# It REMOVES an already-installed block rather than merely declining to write
# one. Skipping the write alone would leave a block installed by an earlier
# session sitting in the file and still loading — an opt-out that does not opt
# you out, which is worse than none because it looks like it worked.
#
# `0`, `false`, `no` and `off` are honoured as OFF, case-insensitively. Treating
# any non-empty value as ON would make `FLEET_GUIDANCE_SKIP=0` mean "skip", and
# a flag whose disabled spelling enables it is a trap worth two lines of code
# to avoid.
case "$FLEET_GUIDANCE_SKIP_ENABLED" in
    0) ;;
    1)
        removed=""      # destinations a block was actually removed from
        present=0       # destinations that exist at all
        for i in "${!DEST_PATHS[@]}"; do
            dest="${DEST_PATHS[$i]}"; label="${DEST_LABELS[$i]}"
            [ -e "$dest" ] || continue
            present=$((present + 1))
            tmp="$(new_tmp)" || { record_failure "mktemp failed"; continue; }
            if ! strip_managed_block "$dest" "$tmp"; then
                record_failure "$STRIP_ERR"
                continue
            fi
            if cmp -s "$tmp" "$dest" 2>/dev/null; then
                continue                      # nothing of ours in there
            elif cp "$tmp" "$dest" 2>/dev/null; then
                removed="${removed:+$removed, }$label"
            else
                record_failure "FLEET_GUIDANCE_SKIP is set but $dest could not be rewritten to remove the managed block"
            fi
        done

        if [ -n "$FAILURES" ]; then
            echo "fleet-guidance: DEGRADED — $FAILURES. FLEET_GUIDANCE_SKIP is set, so the managed block may still be loading there."
        elif [ -n "$removed" ]; then
            echo "fleet-guidance: skipped (FLEET_GUIDANCE_SKIP set) — removed the managed block from $removed; your own content is untouched"
        elif [ "$present" -gt 0 ]; then
            echo "fleet-guidance: skipped (FLEET_GUIDANCE_SKIP set) — no managed block present"
        else
            echo "fleet-guidance: skipped (FLEET_GUIDANCE_SKIP set)"
        fi
        exit 0
        ;;
esac

[ -n "$HOOK_DIR" ] || degraded "cannot resolve hook directory"
[ -r "$PAYLOAD" ]  || degraded "no readable payload at $PAYLOAD"
[ -s "$PAYLOAD" ]  || degraded "payload at $PAYLOAD is empty"

# Short content id, so the verdict names WHICH guidance landed. Any of these
# three digest tools may be absent; a missing one is cosmetic, never fatal.
version="$( { sha256sum "$PAYLOAD" 2>/dev/null || shasum -a 256 "$PAYLOAD" 2>/dev/null || openssl dgst -sha256 "$PAYLOAD" 2>/dev/null; } \
            | tr ' ' '\n' | grep -oE '^[0-9a-f]{64}$' | head -1 )"
version="${version:0:8}"
[ -n "$version" ] || version="unknown"

bytes="$(wc -c < "$PAYLOAD" 2>/dev/null | tr -d ' ')"

# Install the block at ONE destination. Sets INSTALL_STATE to `written` or
# `current`; on failure records the reason and returns non-zero, having
# changed nothing at that destination.
install_to() {
    local dest="$1" tmp dir
    INSTALL_STATE=""

    # The parent directory: created for Claude Code exactly as it always was,
    # and a no-op for Codex, whose directory had to exist for this destination
    # to be in play at all.
    dir="$(dirname "$dest")"
    if ! mkdir -p "$dir" 2>/dev/null; then
        record_failure "cannot create $dir"
        return 1
    fi

    tmp="$(new_tmp)" || { record_failure "mktemp failed"; return 1; }

    if ! strip_managed_block "$dest" "$tmp"; then
        record_failure "$STRIP_ERR"
        return 1
    fi
    [ -s "$tmp" ] && printf '\n' >> "$tmp"

    {
        printf '%s\n' "$BEGIN_MARK"
        printf '<!-- fleet-guidance-version: %s -->\n' "$version"
        cat "$PAYLOAD"
        printf '%s\n' "$END_MARK"
    } >> "$tmp" 2>/dev/null || {
        record_failure "could not assemble the guidance block for $dest"
        return 1
    }

    if cmp -s "$tmp" "$dest" 2>/dev/null; then
        INSTALL_STATE="current"
        return 0
    fi

    if cp "$tmp" "$dest" 2>/dev/null; then
        INSTALL_STATE="written"
        return 0
    fi

    record_failure "could not write $dest"
    return 1
}

wrote=0
for dest in "${DEST_PATHS[@]}"; do
    install_to "$dest" || continue
    [ "$INSTALL_STATE" = "written" ] && wrote=$((wrote + 1))
done

# ── Verdict ────────────────────────────────────────────────────────────────
#
# ONE line, with the same three prefixes the repo stub and the tests key on.
# Any destination that failed makes the whole run DEGRADED even when another
# succeeded: a session holding the guidance in Claude Code and not in Codex is
# running on the stub alone in one of its two harnesses, and it has to be able
# to see that. Mixed success reads `installed` — the stronger,
# an-action-was-taken word — and the list that follows is every destination
# that was in play, i.e. every surface now carrying this version of the block.
if [ -n "$FAILURES" ]; then
    echo "fleet-guidance: DEGRADED — $FAILURES. Repo stub only there; read the fleet guidance in _agent-guidance/agents-md/base.md before non-trivial work."
elif [ "$wrote" -gt 0 ]; then
    echo "fleet-guidance: installed (v$version, ${bytes} bytes) -> $(dest_list)"
else
    echo "fleet-guidance: current (v$version, ${bytes} bytes) — $(dest_list)"
fi
exit 0
