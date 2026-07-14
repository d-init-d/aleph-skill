# Codex adapter

Codex can consume the portable `SKILL.md` core directly.

## Recommended locations

- User skill: `~/.codex/skills/aleph-skill`
- Development workspace: keep the repository anywhere outside the installed target and copy or symlink only after validation.

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

Use Aleph's portable package validator:

```powershell
python scripts\validate_skill_package.py .
```

Keep `agents/openai.yaml`; it provides Codex UI metadata and does not affect other platforms.
