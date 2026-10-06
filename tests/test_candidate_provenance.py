"""Local candidates cannot impersonate official upstream releases."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from aleph.component_registry import resolve_component, verify_component_lock  # noqa: E402
from lock_bundled_component import verify_upstream_snapshot  # noqa: E402


class CandidateProvenanceTests(unittest.TestCase):
    def test_candidate_binding_and_upstream_refusal(self):
        resolution = resolve_component("aleph-component://d-research", skill_root=ROOT)
        self.assertEqual(resolution.provenance_mode, "local_candidate")
        binding = resolution.binding()
        self.assertIs(binding["upstream_attested"], False)
        self.assertRegex(binding["candidate_snapshot_sha256"], r"^sha256:[0-9a-f]{64}$")
        process = subprocess.run([sys.executable, str(ROOT / "scripts/lock_bundled_component.py"), "--require-upstream"], capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("LOCAL_CANDIDATE_NOT_UPSTREAM", process.stdout)

    def test_forged_upstream_or_changed_source_binding_is_refused(self):
        for mutation in ("upstream_claim", "source_digest", "remove_mode"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                shutil.copytree(ROOT / "components", root / "components")
                lock = json.loads((ROOT / "component-lock.json").read_text())
                entry = lock["components"]["d-research"]
                if mutation == "upstream_claim":
                    entry["upstream_commit"] = "1" * 40
                elif mutation == "source_digest":
                    entry["source_artifacts"]["full_profile"]["sha256"] = "sha256:" + "0" * 64
                else:
                    del entry["provenance"]
                (root / "component-lock.json").write_text(json.dumps(lock))
                verification = verify_component_lock(skill_root=root)
                self.assertFalse(verification.ok)
                self.assertEqual(verification.error_code, "COMPONENT_LOCK_INVALID")

    def test_official_policy_still_rejects_wrong_tag_object(self):
        entry = json.loads((ROOT / "component-lock.json").read_text())["components"]["d-research"]
        entry = copy.deepcopy(entry)
        del entry["provenance"]
        entry.update(source_tag="v3.5.0", upstream_tag_object="1" * 40, upstream_commit="2" * 40)
        with tempfile.TemporaryDirectory() as raw, patch("lock_bundled_component.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=b'0000000000000000000000000000000000000000\n', stderr=b'')):
            with self.assertRaisesRegex(ValueError, "tag object"):
                verify_upstream_snapshot(ROOT, Path(raw), {"components": {"d-research": entry}}, component_id="d-research")


if __name__ == "__main__":
    unittest.main()
