# Claude Code adapter

Claude Code supports Agent Skills and can load this portable core without Claude-only frontmatter.

## Recommended locations

- User skill: `~/.claude/skills/aleph-timeline-simulator`
- Project skill: `.claude/skills/aleph-timeline-simulator`

## Portability rule

Do not add Claude-only fields to the core `SKILL.md`, including:

- `allowed-tools`
- `disallowed-tools`
- `context: fork`
- dynamic `!command` injection
- Claude-specific hooks

If a Claude-only extension is needed later, keep it in a separate distribution branch or adapter note so the core remains usable by Codex and OpenCode.

## Install command

Dry-run first:

```powershell
python scripts\install_adapters.py --target claude-code --scope user --dry-run
```

Copy after review:

```powershell
python scripts\install_adapters.py --target claude-code --scope user --copy
```
