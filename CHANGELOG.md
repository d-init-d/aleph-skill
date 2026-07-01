# Changelog

## v1.0.0 - 2026-07-01

Initial production release.

### Added

- Portable skill core with `SKILL.md` frontmatter limited to `name` and `description`.
- Runtime guidance for Codex, Claude Code, OpenCode, and generic `.agents` skill directories.
- D Research integration guide for evidence ledgers, source discovery, contradiction checks, and public-role actor research.
- Mandatory Human Research / Human Roleplay split for material human decision nodes.
- Seven-phase timeline simulation workflow: define, research, construct, link, propagate, branch, validate.
- Node, edge, human-node, propagation, branch, safety, and reporting reference guides.
- JSON/CSV/JSONL templates for simulation manifests, nodes, causal edges, actor dossiers, evidence maps, branch ledgers, propagation traces, and validation reports.
- Stdlib-first helper scripts for package validation, workspace initialization, artifact validation, butterfly scoring, report rendering, preflight checks, and adapter installation.
- Local release-gate self-test workflow documented in README.

### Verification

- `npm run self-test`
- `python scripts/validate_skill_package.py .`
- `python scripts/validate_simulation_artifacts.py --examples`
- end-to-end workspace lifecycle smoke test.
