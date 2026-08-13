# Agent Plugins — compatible clients (captured 2026-08-13)

Snapshot of <https://agent-plugins.org/compatible-clients>. Captured by hand because
the table is rendered client-side: it is absent from the page source, `/llms.txt`
and `/sitemap.md`, so no agent can fetch it. This file exists so work on
`agentskills#57` has the roster available offline.

Source page framing: *"Agent Plugins clients can adopt portable component types
incrementally. The clients below document the components and MCP transports they
support."*

| Client | Agent Skills | MCP transports | Source code linked |
|---|---|---|---|
| Hermes Agent | yes | stdio, Streamable HTTP | yes |
| OpenClaw | yes | stdio, Streamable HTTP, legacy SSE | yes |
| GitHub Copilot | yes | stdio, Streamable HTTP, legacy SSE | no |
| Grok Bot | yes | stdio, Streamable HTTP, legacy SSE | no |
| ChatGPT & Codex | yes | stdio, Streamable HTTP | yes |
| NanoClaw | yes | stdio, Streamable HTTP | yes |
| Cursor | yes | stdio, Streamable HTTP, legacy SSE | no |
| Kiro | yes | stdio, Streamable HTTP, legacy SSE | no |
| VS Code | yes | stdio, Streamable HTTP, legacy SSE | yes |

Every listed client carries Agent Skills, and each has its own "Setup instructions"
link — consistent with the spec defining no installation mechanism of its own.

## What this settles

- **Claude Code is absent.** Confirms from the authoritative source what
  `claude plugin validate` already showed empirically: a pure Agent Plugins v1
  package is not loadable by Claude Code, so it keeps needing `.claude-plugin/`.
- **Gemini / Antigravity is absent**, which is independent support for dropping
  those two `setup.sh` link targets.
- **Codex is a conformant client**, listed jointly as "ChatGPT & Codex". The
  question left open by CLI probing — *does Codex consume the portable root
  manifest?* — is answered yes. What Codex lacks is a **headless install path**
  (`codex plugin` exposes only `marketplace {add,upgrade,remove}`; install is
  TUI-only as of `codex-cli 0.129.0`). Format support and install ergonomics are
  separate problems.
- **Cursor is conformant too**, so `~/.cursor/skills` is a candidate for retirement
  on the same terms as Codex.

## Correction to earlier notes

Secondary sources (vendor launch posts, GitHub's changelog) described a launch set
of six — ChatGPT, Codex, Cursor, GitHub Copilot, Kiro, VS Code. The authoritative
roster is **nine entries**, and ChatGPT and Codex are a **single** entry, not two.
The four the blogs omit are Hermes Agent, OpenClaw, Grok Bot and NanoClaw. Prefer
this page over launch coverage.
