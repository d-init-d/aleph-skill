# Codex adapter

Codex can consume the portable `SKILL.md` core directly.

## Recommended locations

- User skill: `~/.agents/skills/aleph-skill`
- Project skill: `.agents/skills/aleph-skill` at the working directory or any parent through the repository root

Resolve the cloned or installed source directory to an absolute `ALEPH_SKILL_ROOT`. Do not run its helpers relative to the process working directory.

## Install command

Dry-run first:

```powershell
python "$env:ALEPH_SKILL_ROOT\scripts\install_adapters.py" --target codex --scope user --dry-run
```

Copy after review:

```powershell
python "$env:ALEPH_SKILL_ROOT\scripts\install_adapters.py" --target codex --scope user --copy
```

## Validation

Use Aleph's portable package validator:

```powershell
python "$env:ALEPH_SKILL_ROOT\scripts\validate_skill_package.py" "$env:ALEPH_SKILL_ROOT"
```

Keep `agents/openai.yaml`; it provides Codex UI metadata and does not affect other platforms.
