"""Component provenance acceptance tests (I13, I14)."""

from __future__ import annotations

import unittest


class ComponentProvenanceAcceptanceTests(unittest.TestCase):
    """Component provenance acceptance tests mapped to test-mapping-matrix.md."""

    def setUp(self) -> None:
        from test_component_integration import ComponentIntegrationAcceptanceTests
        self._delegate = ComponentIntegrationAcceptanceTests()
        self._delegate.setUp()

    def tearDown(self) -> None:
        self._delegate.tearDown()

    def test_i13_local_candidate_provenance(self) -> None:
        self._delegate.test_i13_local_candidate_provenance()

    def test_i14_production_release_verification(self) -> None:
        self._delegate.test_i14_production_release_verification()


if __name__ == "__main__":
    unittest.main()
