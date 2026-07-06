<!-- Bridges Claude Code (which reads CLAUDE.md, not AGENTS.md) to the managed guidance. -->
@AGENTS.md

## Project memory (portable, in-repo)

This repo's Claude Code auto memory lives in `.claude/memory/` (git-tracked), via
`autoMemoryDirectory` in `.claude/settings.json` — so memory travels to every
machine and into hosted-session clones. In environments where that setting isn't
active (Claude Code on the web, other harnesses), read `.claude/memory/MEMORY.md`
for accumulated project knowledge when you need it.

**This is a public repo: never write secrets, tokens, or PII into memory files.**
