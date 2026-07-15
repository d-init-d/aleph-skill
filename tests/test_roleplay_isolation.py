"""Roleplay isolation: no research root, HMAC, browser, or network tools."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from research_gateway import MODE_ROLEPLAY, assert_roleplay_isolation, roleplay_env, run_command


class RoleplayIsolationTests(unittest.TestCase):
    def test_env_and_component_path_not_leaked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = roleplay_env(
                packet_dir=Path(temporary),
                base={
                    "PATH": os.environ.get("PATH", ""),
                    "D_RESEARCH_ROOT": "D:/components/d-research",
                    "D_RESEARCH_LEDGER_KEY": "hmac-secret-value",
                    "D_RESEARCH_SKILL": "D:/external",
                    "PLAYWRIGHT_BROWSERS_PATH": "D:/browsers",
                    "BROWSER_CHANNEL": "chrome",
                    "HTTP_PROXY": "http://proxy",
                    "TEMP": temporary,
                },
            )
            self.assertEqual(assert_roleplay_isolation(env), [])
            for banned in (
                "D_RESEARCH_ROOT",
                "D_RESEARCH_LEDGER_KEY",
                "D_RESEARCH_SKILL",
                "PLAYWRIGHT_BROWSERS_PATH",
                "BROWSER_CHANNEL",
            ):
                self.assertNotIn(banned, env)

    def test_gateway_denied_in_roleplay(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for command in (
            "research:preflight",
            "research:run",
            "research:import",
            "research:self-test",
        ):
            result = run_command(command, skill_root=root, mode=MODE_ROLEPLAY)
            self.assertEqual(result["status"], "refused", command)
            self.assertEqual(result["error_code"], "ROLEPLAY_NETWORK", command)


if __name__ == "__main__":
    unittest.main()
