from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class ProtocolDocumentationV201Tests(unittest.TestCase):
    def test_commands_resolve_from_an_absolute_skill_root(self) -> None:
        paths = (
            "SKILL.md",
            "AGENTS.md",
            "README.md",
            "README.vi.md",
            "references/evaluation-forward-tests.md",
            "adapters/codex.md",
            "adapters/claude-code.md",
            "adapters/opencode.md",
        )
        for path in paths:
            with self.subTest(path=path):
                text = read(path)
                self.assertIn("ALEPH_SKILL_ROOT", text)
                self.assertIsNone(
                    re.search(r"\bpython\s+[\"']?scripts[\\/]", text),
                    f"{path} must not invoke helpers relative to the process working directory",
                )

        skill = read("SKILL.md")
        self.assertIn("Never assume that the current working directory is the skill directory.", skill)
        self.assertIn("$ALEPH_SKILL_ROOT/scripts/preflight.py", skill)
        self.assertIn("$env:ALEPH_SKILL_ROOT\\scripts\\preflight.py", skill)

    def test_uncalibrated_likelihood_language_cannot_regress_to_probability(self) -> None:
        paths = (
            "SKILL.md",
            "AGENTS.md",
            "references/temporal-modes.md",
            "references/evaluation-forward-tests.md",
            "examples/forward-test-prompts.md",
        )
        combined = "\n".join(read(path) for path in paths).lower()
        for forbidden in (
            "probability-normalized",
            "probability-update triggers",
            "keep the timeline probabilistic",
            "future monitoring and probability updates",
            "branch probabilities are conditional estimates",
        ):
            self.assertNotIn(forbidden, combined)

        self.assertIn("normalized `relative_weight`", combined)
        self.assertIn("ranking, not probability", combined)
        self.assertIn("only after", combined)
        self.assertIn("calibration and validation gates", combined)
        self.assertIn("observation-update triggers", read("references/temporal-modes.md"))

    def test_d_research_fallback_is_executable_but_assurance_limited(self) -> None:
        integration = read("references/d-research-integration.md")
        adaptive = read("references/adaptive-research-workflow.md")
        evaluation = read("references/evaluation-forward-tests.md")

        for token in (
            "Limited host-native fallback",
            "evidence-map.csv",
            "host-native tools",
            "research import receipt",
            "cannot support `verified` or `calibrated` assurance",
        ):
            self.assertIn(token, integration)
        self.assertIn("does not emit a D Research CSV", integration)
        self.assertIn("The same seal applies to host-native fallback research.", integration)
        self.assertIn("limited host-native workflow", adaptive)
        self.assertIn("required D Research capability blocked", evaluation)
        self.assertIn("gateway capability blocker", evaluation)

    def test_host_limits_create_resumable_unsaturated_handoffs(self) -> None:
        adaptive = read("references/adaptive-research-workflow.md")
        workflow = read("references/simulation-workflow.md")
        combined = adaptive + "\n" + workflow

        for token in (
            "no source-count or elapsed-time ceiling",
            "resumable checkpoint",
            "saturation_reached: false",
            "host_limit:",
            "unresolved critical gap",
            "execution.research_control.next_wave_queue",
            "execution.research_quality: limited",
            "no final assurance tier",
        ):
            self.assertIn(token, combined)
        self.assertIn("A later execution resumes", adaptive)
        self.assertIn("that handoff is not completion", workflow)

    def test_complexity_pointer_and_external_profile_scope_are_explicit(self) -> None:
        skill = read("SKILL.md")
        self.assertIn(
            "all seven complexity dimensions in `references/adaptive-research-workflow.md`",
            skill,
        )
        self.assertNotIn(
            "all seven complexity dimensions in `references/simulation-workflow.md`",
            skill,
        )
        self.assertIn("declarative adapter contracts, not turnkey orchestration", skill)
        self.assertIn("a host or wrapper must implement", skill)

    def test_roleplay_seal_remains_strict_in_every_research_mode(self) -> None:
        skill = read("SKILL.md")
        integration = read("references/d-research-integration.md")
        human = read("references/human-node-protocol.md")

        self.assertIn("distinct offline roleplay execution", skill)
        self.assertIn("neither its tools nor its evidence are exposed", integration)
        self.assertIn("Reject any roleplay output containing browsing/tool use", human)
        self.assertIn("may assign `calibrated_probability` only after", human)


if __name__ == "__main__":
    unittest.main()
