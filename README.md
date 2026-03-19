# agentskills

These (hopefully) follow https://agentskills.io/specification

## Global Instructions

I put the following in Claude desktop app -> Settings -> Cowork -> Global instructions 🤞:

> When it seems likely to be beneficial, create/update skills. Follow https://agentskills.io/specification and validate. In addition to putting them in your native place, push them to `main` in https://github.com/Adam-S-Daniel/agentskills under a `skills/` folder. Then clone (as necessary), fetch and pull in WSL and Windows under `~/repos` and `%USERPROFILE%\repos`, respectively. Finally, ensure in Windows and WSL that the following subfolders of `~/` and `%USERPROFILE%\` respectively exist as symlinks to `~/repos/agentskills` and `%USERPROFILE%\repos\agentskills` respectively:
> - .agents/skills/
> - .agent/skills/
> - .claude/skills/
> - .gemini/skills/
> - .gemini/antigravity/skills/
> - .cursor/skills/
