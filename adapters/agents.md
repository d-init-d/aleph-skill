# Generic Agent Skills adapter

Use this adapter for runtimes that implement the Agent Skills directory convention without a platform-specific namespace.

## Recommended locations

- User skill: `~/.agents/skills/aleph-skill`
- Project skill: `.agents/skills/aleph-skill`

## Compatibility contract

The portable core depends only on:

- a directory named `aleph-skill`,
- a top-level `SKILL.md`,
- YAML frontmatter with `name` and `description`,
- Markdown instructions,
- optional `references/`, `scripts/`, `templates/`, `adapters/`, and `examples/`.

Runtimes may ignore `agents/openai.yaml`; it is Codex UI metadata only.
