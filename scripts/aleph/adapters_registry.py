"""Deterministic, host-specific adapter registry and drift checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import PACKAGE_VERSION

NATIVE_TARGETS = [
    "codex",
    "claude-code",
    "opencode",
    "gemini-cli",
    "github-copilot-cli",
    "agents",
    "cursor",
    "vscode-copilot",
    "windsurf",
    "cline",
    "roo-code",
    "jetbrains",
]
GENERATED_TARGETS = ["continue"]
EXTERNAL_TARGETS = ["grok-build", "aider", "generic-cli"]
ALL_TARGETS = NATIVE_TARGETS + GENERATED_TARGETS + EXTERNAL_TARGETS
PORTABLE_CORE_PATH = ".aleph/core/aleph-skill"
RETIRED_GENERATED_FILES = (
    "adapters/generated/cursor.md",
    "adapters/generated/vscode-copilot.md",
    "adapters/generated/windsurf.md",
    "adapters/generated/cline.md",
    "adapters/generated/roo-code.md",
    "adapters/generated/jetbrains.md",
)

TARGET_SPECS: dict[str, dict[str, Any]] = {
    "codex": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.agents/skills/aleph-skill", "project_path": ".agents/skills/aleph-skill"},
    "claude-code": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.claude/skills/aleph-skill", "project_path": ".claude/skills/aleph-skill"},
    "opencode": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.config/opencode/skills/aleph-skill", "project_path": ".opencode/skills/aleph-skill"},
    "gemini-cli": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.gemini/skills/aleph-skill", "project_path": ".gemini/skills/aleph-skill"},
    "github-copilot-cli": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.copilot/skills/aleph-skill", "project_path": ".github/skills/aleph-skill"},
    "agents": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.agents/skills/aleph-skill", "project_path": ".agents/skills/aleph-skill"},
    "cursor": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.cursor/skills/aleph-skill", "project_path": ".cursor/skills/aleph-skill"},
    "vscode-copilot": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.copilot/skills/aleph-skill", "project_path": ".github/skills/aleph-skill"},
    "windsurf": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.codeium/windsurf/skills/aleph-skill", "project_path": ".windsurf/skills/aleph-skill"},
    "cline": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.cline/skills/aleph-skill", "project_path": ".cline/skills/aleph-skill"},
    "roo-code": {"kind": "native", "install_kind": "skill_directory", "user_path": "~/.roo/skills/aleph-skill", "project_path": ".roo/skills/aleph-skill"},
    "jetbrains": {"kind": "native", "install_kind": "skill_directory", "project_path": ".agents/skills/aleph-skill"},
    "continue": {"kind": "generated", "install_kind": "instruction_file", "project_path": ".continue/rules/aleph.md", "source_path": "adapters/generated/continue.md", "format": "continue-rule"},
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
                "d_research_discovery": "bundled-component-gateway",
                "self_elevate_forbidden": True,
                "capability_tier_cap": "unknown-until-probed",
                "receipt_assurance": "cryptographic-only-when-hmac-verified",
            }
        )
        adapters[name] = spec
    return {"schema_version": "2.0.0", "package_version": PACKAGE_VERSION, "adapters": adapters}


def _core_contract(target: str) -> str:
    return f"""## Aleph 2.1 execution contract

- Target host: `{target}`. Detect available tools and subagents at runtime; never claim capabilities from this file alone.
- Verified core: `<ABSOLUTE_PROJECT_ROOT>/{PORTABLE_CORE_PATH}`. Resolve `<ABSOLUTE_PROJECT_ROOT>` from the absolute path of this loaded rule at `<ABSOLUTE_PROJECT_ROOT>/.continue/rules/aleph.md`, never from the process working directory. Export or assign the resulting absolute core path as `ALEPH_SKILL_ROOT` and refuse to run if its `SKILL.md` is missing.
- Initialize a workspace outside the installed skill directory and use only schema `2.0.0` artifacts.
- Separate fact, inference, assumption, simulation, and counterfactual statements.
- D Research is bundled inside Aleph as `aleph-component://d-research`. Use absolute `ALEPH_SKILL_ROOT` with `scripts/preflight.py` and `scripts/research_gateway.py`; do not install a second D Research skill and do not set `D_RESEARCH_SKILL` as a default override. When the bundle cannot provide a needed capability, use the core's limited host-native fallback and cap assurance at `limited`.
- Before research, read `components/d-research/SKILL.md` and `references/bundled-research-routing.md` under `ALEPH_SKILL_ROOT`. Capability order: browser → host browser → fetch → search → structured blocker. Never fabricate ledgers when capabilities are missing.
- For every material actor, complete research first, freeze a temporal knowledge packet, then use a distinct offline roleplay execution. Roleplay never receives research root, HMAC key, raw ledger, browser, or network tools.
- Roleplay proposes decision-graph actions only. It never browses, adds evidence, invents private motives, or emits probability/confidence.
- Use `relative_weight` unless a declared calibration policy and hindcast gate authorize probability.
- Finalize only after strict validation, replay, integrity receipts, and assurance gates pass.

Replace `<ABSOLUTE_PROJECT_ROOT>` with the resolved absolute project path before running a helper. On POSIX, use `export ALEPH_SKILL_ROOT="<ABSOLUTE_PROJECT_ROOT>/{PORTABLE_CORE_PATH}"`; on PowerShell, use `$env:ALEPH_SKILL_ROOT = "<ABSOLUTE_PROJECT_ROOT>/{PORTABLE_CORE_PATH}"`. Then run `python "$ALEPH_SKILL_ROOT/scripts/preflight.py" --json` on POSIX or `python "$env:ALEPH_SKILL_ROOT/scripts/preflight.py" --json` on PowerShell. Never invoke the core through a process-relative path. Pass workspace paths explicitly. Do not copy secrets into artifacts or command prompts.
"""


def generate_instruction_adapter(target: str, skill_root: Path) -> str:
    del skill_root  # Generation is intentionally independent of SKILL.md excerpts.
    contract = _core_contract(target)
    if target == "continue":
        return (
            "---\n"
            "name: Aleph 2.0 causal simulation\n"
            "description: Evidence-grounded timeline simulation guardrails\n"
            "alwaysApply: false\n"
            "---\n\n"
            "# Aleph for Continue\n\n"
            + contract
        )
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
    removed = []
    for relative in RETIRED_GENERATED_FILES:
        path = skill_root / relative
        if path.is_file() or path.is_symlink():
            path.unlink()
            removed.append(relative)
    return {
        "written": written,
        "removed": removed,
        "registry": "adapters/registry.json",
    }


def _semantic_adapter_issues(relative: str, content: str) -> list[str]:
    problems: list[str] = []
    if relative.startswith("adapters/generated/"):
        if "self_elevate" in content.lower() or "capability_tier_cap" in content.lower():
            # Those keys belong in the registry, not host instruction prose.
            problems.append("vendor instruction leaks registry-only capability fields")
        for phrase in (
            "Roleplay proposes decision-graph actions only",
            "never claim capabilities",
            "relative_weight",
            "ALEPH_SKILL_ROOT",
            "<ABSOLUTE_PROJECT_ROOT>/.continue/rules/aleph.md",
        ):
            if phrase not in content:
                problems.append(f"missing semantic guardrail: {phrase}")
        if "python .aleph/" in content.replace("\\", "/"):
            problems.append("generated adapter invokes the core relative to the process working directory")
        for line in content.splitlines():
            key, separator, raw_value = line.partition(":")
            if not separator:
                continue
            value = raw_value.strip().strip("[]").replace("'", "").replace('"', "")
            if key.strip().casefold() == "applyto" and value in {"**", "**/*"}:
                problems.append("generated adapter applies to every file")
            if key.strip().casefold().replace("_", "") == "alwaysapply" and value.casefold() == "true":
                problems.append("generated adapter is always active")
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
    for relative in RETIRED_GENERATED_FILES:
        path = skill_root / relative
        if path.exists() or path.is_symlink():
            issues.append({"target": relative, "error": "retired-generated-adapter"})
    checked = len(expected_generated_files(skill_root)) + len(RETIRED_GENERATED_FILES)
    return {"ok": not issues, "checked": checked, "issues": issues}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
