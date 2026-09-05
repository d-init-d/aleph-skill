"""Gateway fallbacks acceptance tests (I08)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
SCRIPTS = ROOT / "scripts"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class GatewayFallbacksAcceptanceTests(unittest.TestCase):
    """Gateway fallbacks acceptance tests mapped to test-mapping-matrix.md."""

    def setUp(self) -> None:
        from test_component_integration import ComponentIntegrationAcceptanceTests
        self._delegate = ComponentIntegrationAcceptanceTests()
        self._delegate.setUp()

    def tearDown(self) -> None:
        self._delegate.tearDown()

    def test_i08_missing_tools_structured_blocker(self) -> None:
        self._delegate.test_i08_missing_tools_structured_blocker()

    def test_vux03_draft_workflow_without_semantic_backend(self) -> None:
        """VUX03: Draft workflow functions without external semantic backend; assurance reflects degraded status without unnecessary approval loops."""
        import tempfile
        from pathlib import Path
        from unittest import mock

        import research_gateway

        # 1. Without external backend, preflight degrades cleanly without crashing
        with mock.patch("research_gateway._filter_research_env", return_value={}):
            preflight = research_gateway.build_preflight(
                skill_root=ROOT,
                capability_assertions={"fetch": False, "search": False},
            )
            self.assertEqual(preflight["selected_route"], "structured-blocker")
            self.assertTrue(len(preflight["blockers"]) > 0)

            # 2. Invoking network route without backend yields structured delegation/blocker, not unhandled crash
            with tempfile.TemporaryDirectory() as temp_dir:
                res = research_gateway.run_command(
                    "research:api-fetch",
                    skill_root=ROOT,
                    extra_args=["--url", "https://example.invalid"],
                    workspace=Path(temp_dir),
                )
                self.assertEqual(res.get("status"), "delegated")
                self.assertEqual(res.get("error_code"), "CAPABILITY_NETWORK_UNASSERTED")
                self.assertFalse(res.get("ok"))


if __name__ == "__main__":
    unittest.main()

