"""Gateway fallbacks acceptance tests (I08)."""

from __future__ import annotations

import unittest


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


if __name__ == "__main__":
    unittest.main()
