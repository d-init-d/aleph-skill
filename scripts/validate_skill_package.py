from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from _lib import SKILL_NAME, is_skill_name, load_json, parse_frontmatter, read_text


REQUIRED_FILES = [
    ".gitattributes",
    ".gitignore",
    "SKILL.md",
    "AGENTS.md",
    "README.md",
    "README.vi.md",
    "CHANGELOG.md",
    "LICENSE",
    "agents/openai.yaml",
    "package.json",
    "pyproject.toml",
    "references/simulation-workflow.md",
    "references/aleph-core-integration.md",
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
    "templates/evidence-map.csv",
    "templates/branch-ledger.json",
    "templates/propagation-trace.jsonl",
    "templates/validation-report.json",
    "templates/subagent-roleplay-prompt.md",
    "adapters/codex.md",
    "adapters/claude-code.md",
    "adapters/opencode.md",
    "adapters/agents.md",
    "scripts/preflight.py",
    "scripts/init_simulation_workspace.py",
    "scripts/validate_skill_package.py",
    "scripts/validate_simulation_artifacts.py",
    "scripts/aleph_bridge.py",
    "scripts/score_butterfly.py",
    "scripts/render_simulation_report.py",
    "scripts/install_adapters.py",
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

    return {"status": "pass" if not errors else "fail", "errors": errors, "warnings": warnings}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the Aleph Timeline Simulator skill package.")
    parser.add_argument("root", nargs="?", default=".", help="Skill root directory.")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    result = validate(root)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
