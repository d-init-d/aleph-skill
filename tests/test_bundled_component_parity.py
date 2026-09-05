"""Bundled component parity acceptance tests (I01, I02, I03)."""

from __future__ import annotations

import unittest


class BundledComponentParityAcceptanceTests(unittest.TestCase):
    """Parity acceptance tests mapped to test-mapping-matrix.md."""

    def setUp(self) -> None:
        from test_component_integration import ComponentIntegrationAcceptanceTests
        self._delegate = ComponentIntegrationAcceptanceTests()
        self._delegate.setUp()

    def tearDown(self) -> None:
        self._delegate.tearDown()

    def test_i01_bundled_invocation_candidate_sha(self) -> None:
        self._delegate.test_i01_bundled_invocation_candidate_sha()

    def test_i02_canonical_parity_across_repos(self) -> None:
        self._delegate.test_i02_canonical_parity_across_repos()

    def test_i03_component_tamper_detection(self) -> None:
        self._delegate.test_i03_component_tamper_detection()


if __name__ == "__main__":
    unittest.main()
