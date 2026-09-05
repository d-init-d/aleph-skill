"""Actor isolation acceptance tests (I09)."""

from __future__ import annotations

import unittest


class ActorIsolationAcceptanceTests(unittest.TestCase):
    """Actor isolation acceptance tests mapped to test-mapping-matrix.md."""

    def setUp(self) -> None:
        from test_component_integration import ComponentIntegrationAcceptanceTests
        self._delegate = ComponentIntegrationAcceptanceTests()
        self._delegate.setUp()

    def tearDown(self) -> None:
        self._delegate.tearDown()

    def test_i09_actor_temporal_packet_isolation(self) -> None:
        self._delegate.test_i09_actor_temporal_packet_isolation()


if __name__ == "__main__":
    unittest.main()
