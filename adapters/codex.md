# Codex adapter

Codex can consume the portable `SKILL.md` core directly.

## Recommended locations

- User skill: `~/.codex/skills/aleph-skill`
- Development workspace: keep this repository at `D:\Downloads\aleth-skill\aleph-skill` and copy or symlink after validation.

## Install command

Dry-run first:

```powershell
python scripts\install_adapters.py --target codex --scope user --dry-run
```

Copy after review:

```powershell
python scripts\install_adapters.py --target codex --scope user --copy
```

## Validation

Use the Codex skill validator:

```powershell
python C:\Users\dmn05\.codex\skills\.system\skill-creator\scripts\quick_validate.py D:\Downloads\aleth-skill\aleph-skill
```

Keep `agents/openai.yaml`; it provides Codex UI metadata and does not affect other platforms.
