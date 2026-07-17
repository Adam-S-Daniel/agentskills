---
name: migrate-claude-memory
description: >
  Inventory, clean up, and migrate Claude Code auto-memory stores found under
  ~/.claude/projects/<munged-path>/memory/ on this machine. Use this skill to
  list every memory store with its decoded project path, file count, size, and
  freshness; to identify ORPHANED stores whose original workspace no longer
  exists (so a human can review and delete them); and to migrate a chosen
  store into a repo's git-tracked .claude/memory/ directory so the memory
  travels with the repo across machines and is visible to hosted/cloud Claude
  sessions. Trigger on requests like "clean up claude memory", "migrate claude
  memory", "inventory memory stores", "orphaned memory", "sync memory across
  machines", "make memory portable", or any mention of
  `~/.claude/projects` or `autoMemoryDirectory`. LOCAL-ONLY: this skill reads
  and writes files under this machine's `~/.claude` directory and CANNOT run
  in a hosted/cloud Claude session that has no local `~/.claude` on disk — do
  not invoke it there.
compatibility: Requires bash, GNU coreutils (find, stat, du) and read/write access to ~/.claude/projects on the local machine; local execution only — memory stores are machine-local and this skill cannot run in a hosted/cloud session without that directory present.
---

# Migrate Claude Memory

## Background

Claude Code's auto-memory feature stores per-project memory files under
`~/.claude/projects/<munged-absolute-path>/memory/`, where `<munged-absolute-path>`
is the project's absolute filesystem path with `/` replaced by `-`
(e.g. `/home/passp/repos/foo` becomes `-home-passp-repos-foo`).

These stores are keyed **per machine**: the same project checked out at a WSL
path and a Windows path munges to two different directory names, so memory
built up on one machine is invisible on the other. Stores also become
**orphaned** when the workspace directory they refer to is later deleted or
moved — the memory files are still on disk, but nothing points to them
anymore. And because it's all under this machine's `~/.claude`, auto-memory is
**invisible to hosted/cloud Claude sessions**, which don't share your local
filesystem.

This plugin does three things:

1. **Inventory** every memory store (`memory-inventory.sh`) — read-only.
2. **Help a human clean up orphans** — the script only *points at* candidates;
   it never deletes anything. You review the `ORPHANED` entries yourself and
   run `rm -rf` on the ones you're sure about.
3. **Migrate a chosen store into the portable in-repo pattern**
   (`memory-migrate.sh`) — copy its files into `<repo>/.claude/memory/`
   (git-tracked) so the memory travels with the repo via git, reaching other
   machines and hosted/cloud sessions too.

## `autoMemoryDirectory`

Claude Code reads `autoMemoryDirectory` from settings.json to decide where to
read/write a project's memory instead of the default per-machine
`~/.claude/projects/...` location:

- Accepts an **absolute path** or a `~/`-relative path — NOT a bare relative
  path like `.claude/memory`.
- When set in a project's own `<repo>/.claude/settings.json`, it is
  **workspace-trust gated**: it's only honored once that folder has been
  trusted in Claude Code.
- Because auto-memory is otherwise entirely machine-local, putting it in-repo
  and committing it to git is the only channel that carries it to other
  machines and to hosted/cloud sessions.

This skill never edits `settings.json` for you — it prints the exact JSON
snippet to add, and you (or another edit) apply it.

## Workflow 1: Inventory

```
bash scripts/memory-inventory.sh          # human-readable
bash scripts/memory-inventory.sh --json   # machine-readable JSON array
```

Both run a fail-fast preflight first:

```bash
[ -d ~/.claude/projects ] || { echo "..." >&2; exit 1; }
```

For each store, it reports the munged name, a best-guess decoded original
path (or a `ORPHANED`-labeled guess if no matching directory exists on disk),
file count, human-readable size, and newest file mtime, then a summary line
(`N stores, M orphaned`). **`memory-inventory.sh` never deletes or modifies
anything** — it is strictly read-only.

## Workflow 2: Clean up orphans

Look at the `ORPHANED` entries from the inventory. For each one you're
confident about (i.e. you recognize the guessed path and know that workspace
is really gone), delete the memory directory yourself:

```
rm -rf ~/.claude/projects/<munged-name>
```

This skill never runs `rm` for you — cleanup is a manual, human-reviewed step.

## Workflow 3: Migrate to in-repo portable memory

```
bash scripts/memory-migrate.sh [--force] <store-dir> <repo-dir>
```

`--force`, if given, must appear **before** the two positional arguments.
This copies every file from `<store-dir>` into `<repo-dir>/.claude/memory/`
(creating it if needed, preserving mtimes), refusing to overwrite existing
files unless `--force` is passed. It never deletes the source store and never
touches `<repo-dir>/.claude/settings.json` — it only prints the JSON snippet
for you to add, e.g.:

```
Add this to <repo>/.claude/settings.json:
{"autoMemoryDirectory": "~/repos/myrepo/.claude/memory"}
```

**If the repo is public**, review the copied files for secrets, PII,
credentials, or internal-only details before committing — once memory is
migrated in-repo, it becomes as visible as the rest of the repo.

## Known limitation: dotted directory names

Claude Code's real munging also replaces literal `.` in path components with
`-` (verified: a repo literally named `adamdaniel.ai` produces a memory-store
folder ending `...-adamdaniel-ai` — indistinguishable from a repo actually
named `adamdaniel-ai`). The decoder in `memory-inventory.sh` only tries the
`-`-was-`/` vs. `-`-is-a-literal-hyphen split; it does **not** also try
substituting `.` for `-`. This means directories whose real name contains a
literal dot will be reported `ORPHANED` even though the workspace still
exists on disk. This is an accepted best-guess limitation, not a bug — verify
any `ORPHANED` result against the actual filesystem before deleting anything.
