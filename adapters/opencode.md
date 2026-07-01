# OpenCode adapter

OpenCode discovers Agent Skills through several project and global paths.

## Supported locations

- Project OpenCode: `.opencode/skills/aleph-timeline-simulator`
- Global OpenCode: `~/.config/opencode/skills/aleph-timeline-simulator`
- Project Claude-compatible: `.claude/skills/aleph-timeline-simulator`
- Global Claude-compatible: `~/.claude/skills/aleph-timeline-simulator`
- Project Agent-compatible: `.agents/skills/aleph-timeline-simulator`
- Global Agent-compatible: `~/.agents/skills/aleph-timeline-simulator`

## Optional permission example

OpenCode can hide, ask, or allow skills through `opencode.json`. Do not ship this as a default; let the project owner decide.

```json
{
  "permission": {
    "skill": {
      "aleph-timeline-simulator": "allow"
    }
  }
}
```

## Install command

Dry-run first:

```powershell
python scripts\install_adapters.py --target opencode --scope user --dry-run
```

Copy after review:

```powershell
python scripts\install_adapters.py --target opencode --scope user --copy
```
