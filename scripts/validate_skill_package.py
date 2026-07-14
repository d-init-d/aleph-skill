from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from _lib import SKILL_NAME, is_skill_name, load_json, parse_frontmatter, read_text
from aleph.adapters_registry import check_adapter_drift
from aleph.installer import (
    MANIFEST_NAME,
    build_distribution_manifest,
    scan_secret_like_files,
    verify_distribution_manifest,
)
from aleph.io import write_json_atomic

REQUIRED_FILES = [
    "SKILL.md",
    "AGENTS.md",
    "README.md",
    "README.vi.md",
    "CHANGELOG.md",
    "LICENSE",
    "agents/openai.yaml",
    "package.json",
    "pyproject.toml",
    "distribution-manifest.json",
    "references/simulation-workflow.md",
    "references/adaptive-research-workflow.md",
    "references/temporal-modes.md",
    "references/artifact-contract.md",
    "references/d-research-integration.md",
    "references/node-builder.md",
    "references/causal-edge-protocol.md",
    "references/propagation-engine.md",
    "references/human-node-protocol.md",
    "references/branch-management.md",
    "references/safety-and-privacy.md",
    "references/reporting-contract.md",
    "references/evaluation-forward-tests.md",
    "templates/simulation-manifest.json",
    "templates/timeline-node.json",
    "templates/causal-edge.json",
    "templates/actor-dossier.json",
    "templates/human-track-ledger.jsonl",
    "templates/evidence-map.csv",
    "templates/branch-ledger.json",
    "templates/propagation-trace.jsonl",
    "templates/validation-report.json",
    "templates/subagent-research-prompt.md",
    "templates/subagent-roleplay-prompt.md",
    "schemas/run-ledger.schema.json",
    "adapters/codex.md",
    "adapters/claude-code.md",
    "adapters/opencode.md",
    "adapters/agents.md",
    "adapters/registry.json",
    "adapters/external/grok-build.json",
    "adapters/external/aider.json",
    "adapters/external/generic-cli.json",
    "scripts/preflight.py",
    "scripts/init_simulation_workspace.py",
    "scripts/validate_skill_package.py",
    "scripts/validate_simulation_artifacts.py",
    "scripts/evaluate_simulation_quality.py",
    "scripts/score_butterfly.py",
    "scripts/render_simulation_report.py",
    "scripts/install_adapters.py",
    "scripts/migrate_workspace.py",
    "scripts/run_simulation.py",
    "scripts/aleph/__init__.py",
    "scripts/aleph/validator.py",
    "scripts/aleph/paths.py",
    "scripts/aleph/engine.py",
    "packs/economics/pack-manifest.json",
    "packs/geopolitics/pack-manifest.json",
]

FORBIDDEN_PUBLIC_REFERENCES = [
    "d-init-d/" + "Aleph",
    "aleph_" + "bridge.py",
    "aleph-" + "core-integration.md",
    "Aleph " + "core bridge",
    "private " + "Aleph",
]


def find_markdown_refs(text: str) -> set[str]:
    refs: set[str] = set()
    for match in re.finditer(r"`((?:references|scripts|templates|adapters|examples)/[^`]+)`", text):
        refs.add(match.group(1))
    for match in re.finditer(r"\]\(((?:references|scripts|templates|adapters|examples)/[^)]+)\)", text):
        refs.add(match.group(1))
    return refs


def validate(root: Path) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []
    if root.name != SKILL_NAME:
        warnings.append(
            f"folder name is {root.name}; install the skill as {SKILL_NAME} for Agent Skills discovery"
        )

    for rel in REQUIRED_FILES:
        if not (root / rel).exists():
            errors.append(f"missing required file: {rel}")

    skill_path = root / "SKILL.md"
    if skill_path.exists():
        skill_text = read_text(skill_path)
        try:
            frontmatter, body = parse_frontmatter(skill_text)
        except ValueError as exc:
            errors.append(str(exc))
            frontmatter, body = {}, ""
        name = frontmatter.get("name", "")
        description = frontmatter.get("description", "")
        if name != SKILL_NAME:
            errors.append(f"frontmatter name must be {SKILL_NAME}, got {name!r}")
        if not is_skill_name(name):
            errors.append("frontmatter name does not match Agent Skills naming rules")
        if not description:
            errors.append("frontmatter description is required")
        if len(description) > 1024:
            errors.append("frontmatter description exceeds 1024 characters")
        unexpected = set(frontmatter) - {"name", "description"}
        if unexpected:
            errors.append(f"portable core frontmatter has unsupported keys: {sorted(unexpected)}")
        scaffold_markers = ("T" + "ODO", "[T" + "ODO")
        if any(marker in skill_text for marker in scaffold_markers):
            errors.append("SKILL.md contains scaffold markers")
        line_count = len(skill_text.splitlines())
        if line_count > 500:
            warnings.append(f"SKILL.md has {line_count} lines; target is under 500")
        for ref in find_markdown_refs(body):
            if not (root / ref).exists():
                errors.append(f"SKILL.md references missing file: {ref}")

    for rel in ["package.json", "templates/simulation-manifest.json", "templates/branch-ledger.json"]:
        path = root / rel
        if path.exists():
            try:
                load_json(path)
            except json.JSONDecodeError as exc:
                errors.append(f"{rel} is invalid JSON: {exc}")

    adapter_result = check_adapter_drift(root)
    for adapter_issue in adapter_result.get("issues", []):
        errors.append(f"adapter drift: {adapter_issue}")

    distribution = verify_distribution_manifest(root, require=True)
    if not distribution.get("ok"):
        for manifest_issue in distribution.get("issues", []):
            errors.append(f"distribution manifest: {manifest_issue}")

    for finding in scan_secret_like_files(root):
        errors.append(f"secret-like package content refused: {finding['path']} ({finding['reason']})")

    text_file_suffixes = {".md", ".py", ".json", ".toml", ".yaml", ".yml", ".csv"}
    for path in root.rglob("*"):
        if ".git" in path.parts or path.is_dir() or path.suffix.lower() not in text_file_suffixes:
            continue
        if path.name == "validate_skill_package.py":
            continue
        text = read_text(path)
        rel = path.relative_to(root).as_posix()
        for forbidden in FORBIDDEN_PUBLIC_REFERENCES:
            if forbidden in text:
                errors.append(f"{rel} contains removed/private-base reference: {forbidden}")

    return {"status": "pass" if not errors else "fail", "errors": errors, "warnings": warnings}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the Aleph Skill package.")
    parser.add_argument("root", nargs="?", default=".", help="Skill root directory.")
    parser.add_argument(
        "--generate-manifest",
        action="store_true",
        help="Release-maintainer operation: regenerate the deterministic distribution manifest before validation.",
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if args.generate_manifest:
        write_json_atomic(root / MANIFEST_NAME, build_distribution_manifest(root))
    result = validate(root)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
