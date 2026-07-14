from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aleph.finalize import finalize_workspace  # noqa: E402
from aleph.io import canonical_hash, load_json_secure, sha256_file, write_json_atomic  # noqa: E402
from aleph.quality import evaluate  # noqa: E402
from aleph.validator import validate_workspace  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "schema-2.0-valid"


def make_strict_workspace(destination: Path) -> Path:
    shutil.copytree(FIXTURE, destination, dirs_exist_ok=True)
    trace = json.loads((destination / "propagation-trace.jsonl").read_text(encoding="utf-8").strip())
    trace["sample_refs"] = ["run:0"]
    trace.pop("hash_chain", None)
    trace["hash_chain"] = canonical_hash({"previous_hash": None, "row": trace})
    (destination / "propagation-trace.jsonl").write_text(json.dumps(trace) + "\n", encoding="utf-8")
    ledger = json.loads((destination / "branch-ledger.json").read_text(encoding="utf-8"))
    for branch in ledger["branches"]:
        branch["derivation"] = "analyst_authored"
        branch["representative_run"] = None
        branch["trace_hash"] = sha256_file(destination / "propagation-trace.jsonl")
    write_json_atomic(destination / "branch-ledger.json", ledger)

    for script, extra in (("run_simulation.py", ["--ticks", "182"]), ("replay_simulation.py", [])):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), "--workspace", str(destination), *extra],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr + completed.stdout)
    return destination


class ValidatorIntegrityV2Tests(unittest.TestCase):
    def test_non_finite_json_is_rejected_on_read_and_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text('{"value": NaN}', encoding="utf-8")
            data, issues = load_json_secure(path)
            self.assertIsNone(data)
            self.assertTrue(issues)
            with self.assertRaises(ValueError):
                write_json_atomic(path, {"value": float("inf")})

    def test_malformed_nested_values_return_structured_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_strict_workspace(Path(tmp) / "workspace")
            manifest = json.loads((workspace / "simulation-manifest.json").read_text(encoding="utf-8"))
            manifest["execution"] = ["not", "an", "object"]
            write_json_atomic(workspace / "simulation-manifest.json", manifest)
            result = validate_workspace(workspace, mode="final", require_report=True)
            self.assertEqual(result["status"], "fail")
            self.assertIn("TYPE", result["error_codes"])
            self.assertTrue(all("code" in item and "severity" in item for item in result["issues"]))

    def test_trace_requires_step_formula_samples_and_hash_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_strict_workspace(Path(tmp) / "workspace")
            row = json.loads((workspace / "propagation-trace.jsonl").read_text(encoding="utf-8"))
            row["step"] = 999
            row["formula_version"] = "999.0"
            row.pop("sample_refs")
            row["hash_chain"] = "0" * 64
            (workspace / "propagation-trace.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            result = validate_workspace(workspace, mode="final", require_report=True)
            self.assertTrue({"TRACE_STEP", "SCHEMA", "REPLAY_MISMATCH"} <= set(result["error_codes"]))

    def test_report_terms_in_prose_do_not_replace_headings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_strict_workspace(Path(tmp) / "workspace")
            terms = ", ".join(
                [
                    "executive summary",
                    "methodology and scope",
                    "baseline and change point",
                    "evidence and source quality",
                    "causal architecture and propagation",
                    "scenario branches",
                    "human decision tracks",
                    "sensitivity, contradictions, and limitations",
                    "validation and audit",
                    "source appendix",
                    "warnings and next steps",
                    "future monitoring and likelihood updates",
                ]
            )
            (workspace / "REPORT.md").write_text(f"# Report\n\nThis prose mentions {terms}.\n", encoding="utf-8")
            result = validate_workspace(workspace, mode="final", require_report=True)
            self.assertIn("REPORT_SECTION", result["error_codes"])

    def test_finalize_is_immediately_valid_and_tamper_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_strict_workspace(Path(tmp) / "workspace")
            finalized = finalize_workspace(workspace)
            self.assertTrue(finalized["ok"], msg=json.dumps(finalized, indent=2))
            immediate = validate_workspace(
                workspace,
                mode="final",
                require_report=True,
                require_receipts=True,
            )
            self.assertEqual(immediate["status"], "pass", msg=immediate["errors"])
            nodes = json.loads((workspace / "nodes.json").read_text(encoding="utf-8"))
            nodes[0]["name"] = "tampered"
            write_json_atomic(workspace / "nodes.json", nodes)
            stale = validate_workspace(workspace, mode="final", require_report=True, require_receipts=True)
            self.assertIn("STALE_ARTIFACT", stale["error_codes"])

    def test_deleting_manifest_receipt_references_does_not_bypass_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_strict_workspace(Path(tmp) / "workspace")
            self.assertTrue(finalize_workspace(workspace)["ok"])
            manifest = json.loads((workspace / "simulation-manifest.json").read_text(encoding="utf-8"))
            manifest.pop("artifact_index")
            manifest.pop("validation_receipt")
            manifest.pop("quality_receipt")
            manifest.pop("finalization")
            write_json_atomic(workspace / "simulation-manifest.json", manifest)
            result = validate_workspace(workspace, mode="final", require_report=True)
            self.assertEqual(result["status"], "fail")
            self.assertIn("STALE_ARTIFACT", result["error_codes"])

    def test_quality_uses_actual_receipts_and_roleplay_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = make_strict_workspace(Path(tmp) / "workspace")
            before = evaluate(workspace)
            self.assertFalse(before["quality_gates"]["receipt_verified"])
            self.assertEqual(before["roleplay_tier"], "C")
            self.assertTrue(finalize_workspace(workspace)["ok"])
            after = evaluate(workspace)
            self.assertTrue(after["quality_gates"]["receipt_verified"])
            self.assertNotEqual(after["assurance_status"], "failed")


if __name__ == "__main__":
    unittest.main()
