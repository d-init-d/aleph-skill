"""Deterministic, host-specific adapter registry and drift checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import PACKAGE_VERSION

NATIVE_TARGETS = ["codex", "claude-code", "opencode", "gemini-cli", "github-copilot-cli", "agents"]
GENERATED_TARGETS = ["cursor", "vscode-copilot", "windsurf", "cline", "roo-code", "continue", "jetbrains"]
EXTERNAL_TARGETS = ["grok-build", "aider", "generic-cli"]
ALL_TARGETS = NATIVE_TARGETS + GENERATED_TARGETS + EXTERNAL_TARGETS
PORTABLE_CORE_PATH = ".aleph/core/aleph-skill"

TARGET_SPECS: dict[str, dict[str, Any]] = {
    "codex": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.codex/skills/aleph-skill", "project_path": ".codex/skills/aleph-skill"},
    "claude-code": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.claude/skills/aleph-skill", "project_path": ".claude/skills/aleph-skill"},
    "opencode": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.config/opencode/skills/aleph-skill", "project_path": ".opencode/skills/aleph-skill"},
    "gemini-cli": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.gemini/skills/aleph-skill", "project_path": ".gemini/skills/aleph-skill"},
    "github-copilot-cli": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.copilot/skills/aleph-skill", "project_path": ".copilot/skills/aleph-skill"},
    "agents": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.agents/skills/aleph-skill", "project_path": ".agents/skills/aleph-skill"},
    "cursor": {"kind": "generated", "install_kind": "instruction_file", "project_path": ".cursor/rules/aleph.mdc", "source_path": "adapters/generated/cursor.md", "format": "cursor-mdc"},
    "vscode-copilot": {"kind": "generated", "install_kind": "instruction_file", "project_path": ".github/instructions/aleph.instructions.md", "source_path": "adapters/generated/vscode-copilot.md", "format": "copilot-instructions"},
    "windsurf": {"kind": "generated", "install_kind": "instruction_file", "project_path": ".windsurf/rules/aleph.md", "source_path": "adapters/generated/windsurf.md", "format": "windsurf-rule"},
    "cline": {"kind": "generated", "install_kind": "instruction_file", "project_path": ".clinerules/aleph.md", "source_path": "adapters/generated/cline.md", "format": "cline-rule"},
    "roo-code": {"kind": "generated", "install_kind": "instruction_file", "project_path": ".roo/rules/aleph.md", "source_path": "adapters/generated/roo-code.md", "format": "roo-rule"},
    "continue": {"kind": "generated", "install_kind": "instruction_file", "project_path": ".continue/rules/aleph.md", "source_path": "adapters/generated/continue.md", "format": "continue-rule"},
    "jetbrains": {"kind": "generated", "install_kind": "instruction_file", "project_path": ".idea/ai-assistant/aleph.md", "source_path": "adapters/generated/jetbrains.md", "format": "jetbrains-ai-rule"},
    "grok-build": {"kind": "external", "install_kind": "external_profile", "project_path": ".aleph/profiles/grok-build.json", "source_path": "adapters/external/grok-build.json", "executable": "grok"},
    "aider": {"kind": "external", "install_kind": "external_profile", "project_path": ".aleph/profiles/aider.json", "source_path": "adapters/external/aider.json", "executable": "aider"},
    "generic-cli": {"kind": "external", "install_kind": "external_profile", "project_path": ".aleph/profiles/generic-cli.json", "source_path": "adapters/external/generic-cli.json", "executable": None},
}


def registry() -> dict[str, Any]:
    adapters: dict[str, Any] = {}
    for name in ALL_TARGETS:
        spec = dict(TARGET_SPECS[name])
        if spec.get("install_kind") in {"instruction_file", "external_profile"}:
            spec["core_path"] = PORTABLE_CORE_PATH
        spec.update(
            {
                "name": name,
                "capability_detection": "runtime-probe-required",
                "d_research_discovery": "aleph-preflight",
                "self_elevate_forbidden": True,
                "capability_tier_cap": "unknown-until-probed",
                "receipt_assurance": "cryptographic-only-when-hmac-verified",
            }
        )
        adapters[name] = spec
    return {"schema_version": "2.0.0", "package_version": PACKAGE_VERSION, "adapters": adapters}


def _core_contract(target: str) -> str:
    return f"""## Aleph 2.0 execution contract

- Target host: `{target}`. Detect available tools and subagents at runtime; never claim capabilities from this file alone.
- Verified core: `{PORTABLE_CORE_PATH}`. Resolve every bundled reference and script from this directory, never from the process working directory.
- Initialize a workspace outside the installed skill directory and use only schema `2.0.0` artifacts.
- Separate fact, inference, assumption, simulation, and counterfactual statements.
- Use D Research 3.x when its identity and version pass `{PORTABLE_CORE_PATH}/scripts/preflight.py`; otherwise declare limited research.
- For every material actor, complete research first, freeze a temporal knowledge packet, then use a distinct offline roleplay execution.
- Roleplay proposes decision-graph actions only. It never browses, adds evidence, invents private motives, or emits probability/confidence.
- Use `relative_weight` unless a declared calibration policy and hindcast gate authorize probability.
- Finalize only after strict validation, replay, integrity receipts, and assurance gates pass.

Run scripts with the host's Python 3.10+ executable, for example `python {PORTABLE_CORE_PATH}/scripts/preflight.py --json`. Pass workspace paths explicitly. Do not copy secrets into artifacts or command prompts.
"""


def generate_instruction_adapter(target: str, skill_root: Path) -> str:
    del skill_root  # Generation is intentionally independent of SKILL.md excerpts.
    contract = _core_contract(target)
    if target == "cursor":
        return "---\ndescription: Aleph 2.0 causal simulation protocol\nglobs: []\nalwaysApply: false\n---\n\n# Aleph for Cursor\n\n" + contract
    if target == "vscode-copilot":
        return "---\napplyTo: '**'\ndescription: 'Aleph 2.0 causal simulation protocol'\n---\n\n# Aleph for GitHub Copilot in VS Code\n\n" + contract
    if target == "windsurf":
        return "# Aleph for Windsurf\n\nUse this rule only for causal simulation and counterfactual analysis tasks.\n\n" + contract
    if target == "cline":
        return "# Aleph for Cline\n\nBefore tool use, read the installed Aleph `SKILL.md` and only the references needed for the current phase.\n\n" + contract
    if target == "roo-code":
        return "# Aleph for Roo Code\n\nKeep research, roleplay, adjudication, and validation as separate auditable modes.\n\n" + contract
    if target == "continue":
        return "---\nname: Aleph 2.0 causal simulation\ndescription: Evidence-grounded timeline simulation guardrails\n---\n\n# Aleph for Continue\n\n" + contract
    if target == "jetbrains":
        return "# Aleph for JetBrains AI Assistant\n\nLoad the portable Aleph skill instructions before producing or modifying simulation artifacts.\n\n" + contract
    raise ValueError(f"not a generated adapter target: {target}")


def generate_external_profile(target: str) -> dict[str, Any]:
    if target not in EXTERNAL_TARGETS:
        raise ValueError(f"not an external adapter target: {target}")
    executable = TARGET_SPECS[target].get("executable")
    return {
        "schema_version": "2.0.0",
        "profile_id": target,
        "package_version": PACKAGE_VERSION,
        "executable": executable,
        "shell": False,
        "prompt_transport": "stdin",
        "working_directory": "workspace",
        "core_path": PORTABLE_CORE_PATH,
        "preflight": ["python", f"{PORTABLE_CORE_PATH}/scripts/preflight.py", "--json"],
        "version_probe": [executable, "--version"] if executable else None,
        "network_policy": {"research": "host-declared", "roleplay": "deny"},
        "tool_policy": {"research": "host-declared", "roleplay": "deny"},
        "required_receipt_fields": [
            "execution_id", "runtime_id", "adapter_id", "started_at", "completed_at",
            "inputs", "outputs", "capability_snapshot_hash", "receipt_hash", "hmac",
        ],
        "notes": "Do not interpolate prompts into a shell command. The caller must probe capabilities and hash all inputs/outputs.",
    }


def expected_generated_files(skill_root: Path) -> dict[str, str]:
    expected = {
        f"adapters/generated/{target}.md": generate_instruction_adapter(target, skill_root)
        for target in GENERATED_TARGETS
    }
    expected.update(
        {
            f"adapters/external/{target}.json": json.dumps(generate_external_profile(target), indent=2, ensure_ascii=False) + "\n"
            for target in EXTERNAL_TARGETS
        }
    )
    expected["adapters/registry.json"] = json.dumps(registry(), indent=2, ensure_ascii=False) + "\n"
    return expected


def write_generated_adapters(skill_root: Path) -> dict[str, Any]:
    written = []
    for relative, content in expected_generated_files(skill_root).items():
        path = skill_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append({"path": relative, "sha256": _sha(content)})
    return {"written": written, "registry": "adapters/registry.json"}


def _semantic_adapter_issues(relative: str, content: str) -> list[str]:
    problems: list[str] = []
    if relative.startswith("adapters/generated/"):
        if "self_elevate" in content.lower() or "capability_tier_cap" in content.lower():
            # Those keys belong in the registry, not host instruction prose.
            problems.append("vendor instruction leaks registry-only capability fields")
        for phrase in ("Roleplay proposes decision-graph actions only", "never claim capabilities", "relative_weight"):
            if phrase not in content:
                problems.append(f"missing semantic guardrail: {phrase}")
    return problems


def check_adapter_drift(skill_root: Path) -> dict[str, Any]:
    """Compare expected adapters without mutating the repository."""
    issues: list[dict[str, Any]] = []
    for relative, expected in expected_generated_files(skill_root).items():
        path = skill_root / relative
        if not path.is_file():
            issues.append({"target": relative, "error": "missing"})
            continue
        try:
            actual = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            issues.append({"target": relative, "error": str(exc)})
            continue
        if actual != expected:
            issues.append({"target": relative, "error": "drift", "expected_sha": _sha(expected), "actual_sha": _sha(actual)})
        for semantic in _semantic_adapter_issues(relative, actual):
            issues.append({"target": relative, "error": "semantic", "message": semantic})
    return {"ok": not issues, "checked": len(expected_generated_files(skill_root)), "issues": issues}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
