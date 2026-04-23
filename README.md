# agentskills

These (hopefully) follow https://agentskills.io/specification

## Setup

After cloning, run `setup.sh` once in **each** environment you use (Windows
Git Bash *and* WSL — they have separate `$HOME`s):

```bash
bash setup.sh
```

This creates symlinks (directory junctions on Windows — no admin required)
from the standard agent-tool skill dirs into this repo's `skills/` folder:

- `~/.agents/skills/`
- `~/.agent/skills/`
- `~/.claude/skills/`
- `~/.gemini/skills/`
- `~/.gemini/antigravity/skills/`
- `~/.cursor/skills/`

It also registers the `sync-skills` pre-push reminder hook (requires git
2.54+). Re-running is safe — existing links are detected and left alone.

## Global Instructions

I put the following in Claude desktop app -> Settings -> Cowork -> Global instructions 🤞:

> When it seems likely to be beneficial, create/update skills. Follow https://agentskills.io/specification and validate. In addition to putting them in your native place, push them to `main` in https://github.com/Adam-S-Daniel/agentskills under a `skills/` folder. Then clone (as necessary), fetch and pull in WSL and Windows under `~/repos` and `%USERPROFILE%\repos`, respectively. Finally, run `bash setup.sh` in both WSL and Windows Git Bash to ensure the skill directories are symlinked into the standard locations (`.claude/skills/`, `.gemini/skills/`, `.cursor/skills/`, etc.).
