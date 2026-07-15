"""Regression tests for security scanner and assurance binding gates."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.installer import _contains_secret_content  # noqa: E402


class SecretScannerRegressionTests(unittest.TestCase):
    def test_real_secret_after_benign_match_is_not_exempted(self) -> None:
        payload = (
            b'secret = "PLACEHOLDER_DO_NOT_LEAK_123456"\n'
            b'api_key = "REALPRODUCTIONSECRET123456789"\n'
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.txt"
            path.write_bytes(payload)
            self.assertTrue(_contains_secret_content(path))

    def test_benign_fixture_token_alone_is_allowed(self) -> None:
        payload = b'secret = "PLACEHOLDER_DO_NOT_LEAK_123456"\n'
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.txt"
            path.write_bytes(payload)
            self.assertFalse(_contains_secret_content(path))

    def test_benign_word_inside_real_value_is_not_exempted(self) -> None:
        payload = b'api_key = "REALFAKEPRODUCTIONSECRET123456789"\n'
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.txt"
            path.write_bytes(payload)
            self.assertTrue(_contains_secret_content(path))


if __name__ == "__main__":
    unittest.main()
