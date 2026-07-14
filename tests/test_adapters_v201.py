from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.adapters_registry import (  # noqa: E402
    EXTERNAL_TARGETS,
    GENERATED_TARGETS,
    NATIVE_TARGETS,
    PORTABLE_CORE_PATH,
    RETIRED_GENERATED_FILES,
    TARGET_SPECS,
    _semantic_adapter_issues,
    check_adapter_drift,
    expected_generated_files,
    generate_instruction_adapter,
    registry,
    write_generated_adapters,
)
from install_adapters import destination  # noqa: E402

EXPECTED_TARGETS: dict[str, tuple[str, str | None, str]] = {
    "codex": ("native", "~/.agents/skills/aleph-skill", ".agents/skills/aleph-skill"),
    "claude-code": (
        "native",
        "~/.claude/skills/aleph-skill",
        ".claude/skills/aleph-skill",
    ),
    "opencode": (
        "native",
        "~/.config/opencode/skills/aleph-skill",
        ".opencode/skills/aleph-skill",
    ),
    "gemini-cli": (
        "native",
        "~/.gemini/skills/aleph-skill",
        ".gemini/skills/aleph-skill",
    ),
    "github-copilot-cli": (
        "native",
        "~/.copilot/skills/aleph-skill",
        ".github/skills/aleph-skill",
    ),
    "agents": (
        "native",
        "~/.agents/skills/aleph-skill",
        ".agents/skills/aleph-skill",
    ),
    "cursor": (
        "native",
        "~/.cursor/skills/aleph-skill",
        ".cursor/skills/aleph-skill",
    ),
    "vscode-copilot": (
        "native",
        "~/.copilot/skills/aleph-skill",
        ".github/skills/aleph-skill",
    ),
    "windsurf": (
        "native",
        "~/.codeium/windsurf/skills/aleph-skill",
        ".windsurf/skills/aleph-skill",
    ),
    "cline": ("native", "~/.cline/skills/aleph-skill", ".cline/skills/aleph-skill"),
    "roo-code": ("native", "~/.roo/skills/aleph-skill", ".roo/skills/aleph-skill"),
    "jetbrains": ("native", None, ".agents/skills/aleph-skill"),
    "continue": ("generated", None, ".continue/rules/aleph.md"),
    "grok-build": ("external", None, ".aleph/profiles/grok-build.json"),
    "aider": ("external", None, ".aleph/profiles/aider.json"),
    "generic-cli": ("external", None, ".aleph/profiles/generic-cli.json"),
}


class AdapterRegistryV201Tests(unittest.TestCase):
    def test_target_classification_and_paths_are_exact(self) -> None:
        native = {name for name, (kind, _, _) in EXPECTED_TARGETS.items() if kind == "native"}
        generated = {
            name for name, (kind, _, _) in EXPECTED_TARGETS.items() if kind == "generated"
        }
        external = {
            name for name, (kind, _, _) in EXPECTED_TARGETS.items() if kind == "external"
        }
        self.assertEqual(set(NATIVE_TARGETS), native)
        self.assertEqual(set(GENERATED_TARGETS), generated)
        self.assertEqual(set(EXTERNAL_TARGETS), external)
        self.assertEqual(set(TARGET_SPECS), set(EXPECTED_TARGETS))

        published = registry()["adapters"]
        for target, (kind, user_path, project_path) in EXPECTED_TARGETS.items():
            with self.subTest(target=target):
                spec = TARGET_SPECS[target]
                self.assertEqual(spec["kind"], kind)
                self.assertEqual(spec["project_path"], project_path)
                self.assertEqual(published[target]["project_path"], project_path)
                if user_path is None:
                    self.assertNotIn("user_path", spec)
                    self.assertNotIn("user_path", published[target])
                else:
                    self.assertEqual(spec["user_path"], user_path)
                    self.assertEqual(published[target]["user_path"], user_path)

                if kind == "native":
                    self.assertEqual(spec["install_kind"], "skill_directory")
                    self.assertNotIn("source_path", spec)
                    self.assertNotIn("core_path", published[target])
                elif kind == "generated":
                    self.assertEqual(spec["install_kind"], "instruction_file")
                    self.assertEqual(published[target]["core_path"], PORTABLE_CORE_PATH)
                else:
                    self.assertEqual(spec["install_kind"], "external_profile")
                    self.assertEqual(published[target]["core_path"], PORTABLE_CORE_PATH)

    def test_scope_resolution_matches_declared_support(self) -> None:
        project = Path("C:/portable-project")
        home = Path("C:/Users/adapter-test")
        with mock.patch("install_adapters.Path.home", return_value=home):
            for target, (_, user_path, project_path) in EXPECTED_TARGETS.items():
                with self.subTest(target=target, scope="project"):
                    self.assertEqual(
                        destination(target, "project", project),
                        project / project_path,
                    )
                with self.subTest(target=target, scope="user"):
                    if user_path is None:
                        with self.assertRaisesRegex(ValueError, "has no user default"):
                            destination(target, "user", project)
                    else:
                        self.assertEqual(
                            destination(target, "user", project),
                            home / user_path.removeprefix("~/"),
                        )

    def test_continue_is_the_only_generated_non_always_on_fallback(self) -> None:
        self.assertEqual(GENERATED_TARGETS, ["continue"])
        content = generate_instruction_adapter("continue", ROOT)
        self.assertIn("alwaysApply: false", content)
        self.assertNotIn("applyTo:", content)
        self.assertIn(
            "use the core's limited host-native fallback and cap assurance at `limited`",
            content,
        )
        self.assertIn("<ABSOLUTE_PROJECT_ROOT>/.continue/rules/aleph.md", content)
        self.assertIn('export ALEPH_SKILL_ROOT="<ABSOLUTE_PROJECT_ROOT>/', content)
        self.assertIn('$env:ALEPH_SKILL_ROOT = "<ABSOLUTE_PROJECT_ROOT>/', content)
        self.assertIn('python "$ALEPH_SKILL_ROOT/scripts/preflight.py" --json', content)
        self.assertIn('python "$env:ALEPH_SKILL_ROOT/scripts/preflight.py" --json', content)
        self.assertNotIn("python .aleph/", content)
        self.assertEqual(_semantic_adapter_issues("adapters/generated/continue.md", content), [])

        for promoted in (
            "cursor",
            "vscode-copilot",
            "windsurf",
            "cline",
            "roo-code",
            "jetbrains",
        ):
            with self.subTest(promoted=promoted):
                with self.assertRaises(ValueError):
                    generate_instruction_adapter(promoted, ROOT)

    def test_semantic_check_rejects_broad_generated_rules(self) -> None:
        contract = generate_instruction_adapter("continue", ROOT)
        broad_apply_to = contract.replace("alwaysApply: false", "applyTo: ['**']")
        always_active = contract.replace("alwaysApply: false", "alwaysApply: true")
        cwd_relative = contract.replace(
            'python "$ALEPH_SKILL_ROOT/scripts/preflight.py" --json',
            "python .aleph/core/aleph-skill/scripts/preflight.py --json",
        )
        self.assertIn(
            "generated adapter applies to every file",
            _semantic_adapter_issues("adapters/generated/bad.md", broad_apply_to),
        )
        self.assertIn(
            "generated adapter is always active",
            _semantic_adapter_issues("adapters/generated/bad.md", always_active),
        )
        self.assertIn(
            "generated adapter invokes the core relative to the process working directory",
            _semantic_adapter_issues("adapters/generated/bad.md", cwd_relative),
        )

    def test_generation_removes_only_known_retired_wrappers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in RETIRED_GENERATED_FILES:
                stale = root / relative
                stale.parent.mkdir(parents=True, exist_ok=True)
                stale.write_text("stale\n", encoding="utf-8")
            unrelated = root / "adapters" / "generated" / "unmanaged-note.txt"
            unrelated.write_text("keep\n", encoding="utf-8")

            result = write_generated_adapters(root)

            self.assertEqual(set(result["removed"]), set(RETIRED_GENERATED_FILES))
            self.assertTrue(unrelated.is_file())
            for relative in RETIRED_GENERATED_FILES:
                self.assertFalse((root / relative).exists())
            self.assertTrue(check_adapter_drift(root)["ok"])

    def test_checked_in_registry_and_generated_files_have_no_drift(self) -> None:
        expected = expected_generated_files(ROOT)
        self.assertEqual(
            {path for path in expected if path.startswith("adapters/generated/")},
            {"adapters/generated/continue.md"},
        )
        result = check_adapter_drift(ROOT)
        self.assertTrue(result["ok"], result)


if __name__ == "__main__":
    unittest.main()
