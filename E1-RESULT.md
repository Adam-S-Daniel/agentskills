# ADR 0001 — Experiment E1 result (hosted/cloud session probe)

Question: does a hosted Claude Code (web/cloud) session install and load the
plugin declared by the repo's committed `.claude/settings.json`
(`extraKnownMarketplaces` + `enabledPlugins: {"adam@agentskills": true}`)?

Session context: Claude Code on the web, repo cloned fresh into
`/home/user/agentskills`, branch `claude/adamdaniel-skills-vendoring-szuluq`,
probe run 2026-08-13.

---

## 1. `cat .claude/settings.json`

```
{
  "autoMemoryDirectory": "~/repos/agentskills/.claude/memory",
  "extraKnownMarketplaces": {
    "agentskills": {
      "source": { "source": "github", "repo": "Adam-S-Daniel/agentskills" },
      "autoUpdate": true
    }
  },
  "enabledPlugins": { "adam@agentskills": true }
}
```

The declaration **is** present in the clone. Whatever happens next is not a
missing-config problem.

## 2. `ls -la ~/.claude/plugins/` and contents

```
$ ls -la ~/.claude/plugins/ 2>&1
total 12
drwxr-xr-x 2 root root 4096 Aug 13 13:28 .
drwxr-xr-x 9 root root 4096 Aug 13 13:30 ..
-rw-r--r-- 1 root root   35 Aug 13 13:28 installed_plugins.json
```

```
$ find ~/.claude/plugins -maxdepth 3 | head -50
/root/.claude/plugins
/root/.claude/plugins/installed_plugins.json
```

```
$ cat ~/.claude/plugins/installed_plugins.json
{
  "version": 2,
  "plugins": {}
}
```

```
$ cat ~/.claude/plugins/known_marketplaces.json
cat: /root/.claude/plugins/known_marketplaces.json: No such file or directory
```

No marketplace was ever fetched: there is no `marketplaces/` or `repos/` cache
directory, no `known_marketplaces.json`, and the installed-plugins map is empty.

## 3. Plugin/marketplace keys in `/root/.claude.json`

```
$ python3 -c "import json;d=json.load(open('/root/.claude.json'));print(sorted([k for k in d if 'lugin' in k or 'arketplace' in k]))"
[]
```

Not a single plugin- or marketplace-related key exists in the global config.

## 4. THE KEY QUESTION — my own available-skills list

Reported from the skills list actually present in this session's system prompt,
not from repo contents on disk.

- `finding-unknowns` — **NO** (absent)
- `writing-adrs` — **NO** (absent)
- `debug-github-workflows` — **NO** (absent)
- `review-bash-ci-reliability` — **NO** (absent)
- `pin-actions-to-sha` — **YES** (present)
- `adam-writing-style` — **YES** (present)

The four `adam`-bundle-only skills are all absent. The two that are also in the
claude.ai account-uploaded set are present — and, per check 6, they are present
on disk under `~/.claude/skills/synced/`, i.e. via the account-upload path, not
the plugin path.

For completeness, the full set of Adam-authored skills visible to this session
is exactly the `~/.claude/skills/synced/` listing from check 6 (plus Anthropic's
own built-ins such as `code-review`, `docx`, `pptx`, `xlsx`, `pdf`,
`skill-creator`, `artifact-design`, `init`, `security-review`, etc., and the
repo-local `session-start-hook`). Nothing beyond the synced set appeared.

## 5. Namespacing — are any skills `adam:<skill>` / `/adam:<skill>`?

**No.** Zero skills in the list carry a plugin namespace prefix. Every entry
appears as a bare skill name, e.g.:

```
- pin-actions-to-sha: Audit and fix GitHub Actions workflow files ...
- adam-writing-style: Write in Adam Daniel's voice ...
- workflow-path-audit: Audit GitHub Actions workflows for salient-path conditionals ...
```

There is no `adam:pin-actions-to-sha`, no `/adam:` form, and no plugin-qualified
name of any kind. Plugin-provided skills would be namespaced; these are not.

## 6. `~/.claude/skills/`

```
$ ls -la ~/.claude/skills/ 2>&1
total 16
drwxr-xr-x  4 root root 4096 Aug 13 13:30 .
drwxr-xr-x  9 root root 4096 Aug 13 13:30 ..
drwxr-xr-x  2 root root 4096 Aug 13 13:28 session-start-hook
drwxr-xr-x 22 root root 4096 Aug 13 13:30 synced
```

```
$ ls -1 ~/.claude/skills/synced/ 2>&1
adam-writing-style
doc-coauthoring
docx
fastmail
github-actions-repo-settings
learn
manifest.json
ocr-pdfs
pdf
pdf-ocr-audit
pin-actions-to-sha
pptx
rename-pdfs
skill-creator
sync-cc-settings-between-wsl-and-windows
sync-skills
theme-factory
web-artifacts-builder
wj-next-break
workflow-path-audit
xlsx
```

Every Adam skill this session has came from `synced/` — the claude.ai
account-upload channel. `finding-unknowns`, `writing-adrs`,
`debug-github-workflows`, and `review-bash-ci-reliability` are not there, which
is exactly why they are missing from the skills list.

## 7. `claude plugin` CLI

```
$ claude plugin list 2>&1 | head -30
No plugins installed. Use `claude plugin install` to install a plugin.
```

```
$ claude plugin marketplace list 2>&1 | head -30
No marketplaces configured
```

The CLI's own view agrees with the filesystem: nothing installed, no marketplace
registered — despite `extraKnownMarketplaces` being present in the repo settings
the session cloned.

---

## Six yes/no answers

| Skill | In `adam` bundle only? | Present in my skills list? |
| --- | --- | --- |
| `finding-unknowns` | yes (proves plugin path) | **NO** |
| `writing-adrs` | yes (proves plugin path) | **NO** |
| `debug-github-workflows` | yes (proves plugin path) | **NO** |
| `review-bash-ci-reliability` | yes (proves plugin path) | **NO** |
| `pin-actions-to-sha` | no (also account-uploaded) | **YES** |
| `adam-writing-style` | no (also account-uploaded) | **YES** |

All four plugin-only discriminators: absent. Both non-discriminating skills:
present, and accounted for by the account-upload path (`~/.claude/skills/synced/`).

---

VERDICT: E1(a) STILL FAILS — repo-declared plugins do not install in cloud

The decisive evidence is that all four `adam`-bundle-only skills
(`finding-unknowns`, `writing-adrs`, `debug-github-workflows`,
`review-bash-ci-reliability`) are absent from this session's skills list, while
`~/.claude/plugins/installed_plugins.json` reads `{"version": 2, "plugins": {}}`
and `claude plugin marketplace list` reports "No marketplaces configured" —
so the repo's `enabledPlugins` declaration was never acted on.
