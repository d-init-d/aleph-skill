# OpenCode adapter

OpenCode discovers Agent Skills through several project and global paths.

## Supported locations

- Project OpenCode: `.opencode/skills/aleph-skill`
- Global OpenCode: `~/.config/opencode/skills/aleph-skill`
- Project Claude-compatible: `.claude/skills/aleph-skill`
- Global Claude-compatible: `~/.claude/skills/aleph-skill`
- Project Agent-compatible: `.agents/skills/aleph-skill`
- Global Agent-compatible: `~/.agents/skills/aleph-skill`

## Optional permission example

OpenCode can hide, ask, or allow skills through `opencode.json`. Do not ship this as a default; let the project owner decide.

```json
{
  "permission": {
    "skill": {
      "aleph-skill": "allow"
    }
  }
}
```

## Install command

Set `ALEPH_SKILL_ROOT` to the absolute source directory; the commands do not assume the process working directory.

Dry-run first:

```powershell
python "$env:ALEPH_SKILL_ROOT\scripts\install_adapters.py" --target opencode --scope user --dry-run
```

Copy after review:

```powershell
python "$env:ALEPH_SKILL_ROOT\scripts\install_adapters.py" --target opencode --scope user --copy
```
