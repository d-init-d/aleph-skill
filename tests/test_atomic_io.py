from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.io import load_json_secure, write_json_atomic  # noqa: E402
from aleph.validator import validate_numerical_artifacts  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "schema-2.0-valid"


class AtomicIOAcceptanceTests(unittest.TestCase):
    def test_t16_mid_write_atomic_rollback(self) -> None:
        """T16: Process crashes or power fails midway through writing model, run ledger, or trace.

        Atomic write mechanism ensures partial files are marked or rolled back; no corrupt half-written state passes validator.
        """
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURE, workspace)
            manifest_path = workspace / "simulation-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            target_file = workspace / "simulation-model.json"
            original_bytes = target_file.read_bytes()

            # 1. Simulate mid-write crash during atomic write
            # When os.fsync or os.replace fails midway, target_file must retain original intact bytes
            class SimulatedCrashError(RuntimeError):
                pass

            def failing_fsync(_fileno: int) -> None:
                raise SimulatedCrashError("Power failure / mid-write crash before atomic commit")

            with patch("os.fsync", side_effect=failing_fsync):
                with self.assertRaises(SimulatedCrashError):
                    write_json_atomic(target_file, {"corrupted_in_progress": True})

            # Target file is completely unmodified and intact
            self.assertEqual(target_file.read_bytes(), original_bytes)
            # Validation continues to pass on uncorrupted file
            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "pass")

            # 2. Simulate new file mid-write crash
            new_target = workspace / "new-trace-output.json"
            with patch("os.fsync", side_effect=failing_fsync):
                with self.assertRaises(SimulatedCrashError):
                    write_json_atomic(new_target, {"partial_trace": [1, 2, 3]})

            # New target file does not exist in corrupt partial state
            self.assertFalse(new_target.exists())

            # 3. Verify validator and secure loader explicitly reject any truncated/corrupted half-written files
            corrupt_file = workspace / "corrupt-half-written.json"
            corrupt_file.write_text('{"schema_version": "2.0.0", "run_id": "run:partial', encoding="utf-8")

            _, load_issues = load_json_secure(corrupt_file)
            self.assertTrue(len(load_issues) > 0)
            self.assertEqual(load_issues[0].code, "INVALID_ARTIFACT")

            # Point manifest to the corrupt file; validator must reject it and fail
            corrupt_manifest = copy.deepcopy(manifest)
            corrupt_manifest["artifact_paths"]["run_ledger"] = "corrupt-half-written.json"
            val_result = validate_numerical_artifacts(workspace, corrupt_manifest)
            self.assertEqual(val_result.status, "fail")
            self.assertTrue(any(i.code in {"INVALID_ARTIFACT", "INVALID_JSON"} for i in val_result.issues))


if __name__ == "__main__":
    unittest.main()
