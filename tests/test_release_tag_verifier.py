from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts" / "verify_release_tag.py"


class ReleaseTagVerifierTests(unittest.TestCase):
    def _git(self, repository: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout.strip()

    def _repository(self, parent: Path) -> tuple[Path, Path]:
        remote = parent / "remote.git"
        work = parent / "work"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        subprocess.run(["git", "init", str(work)], check=True, capture_output=True)
        self._git(work, "config", "user.name", "Aleph Release Test")
        self._git(work, "config", "user.email", "release-test@example.invalid")
        tracked = work / "tracked.txt"
        tracked.write_text("release\n", encoding="utf-8")
        self._git(work, "add", "tracked.txt")
        self._git(work, "commit", "-m", "release source")
        self._git(work, "branch", "-M", "main")
        self._git(work, "remote", "add", "origin", str(remote))
        self._git(work, "push", "-u", "origin", "main")
        return work, remote

    def _advance_main(self, work: Path) -> str:
        tracked = work / "tracked.txt"
        tracked.write_text(tracked.read_text(encoding="utf-8") + "advance\n", encoding="utf-8")
        self._git(work, "add", "tracked.txt")
        self._git(work, "commit", "-m", "advance main")
        self._git(work, "push", "origin", "main")
        return self._git(work, "rev-parse", "HEAD")

    def _run(self, work: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(VERIFIER),
                "--repository",
                str(work),
                "--tag",
                "v2.0.1",
                *args,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _failure_code(self, result: subprocess.CompletedProcess[str]) -> str:
        self.assertNotEqual(result.returncode, 0, result.stdout)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["status"], "fail")
        return str(payload["code"])

    def test_annotated_tag_survives_local_checkout_peeling_and_main_advance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            work, _remote = self._repository(parent)
            release_commit = self._git(work, "rev-parse", "HEAD")
            self._git(work, "tag", "-a", "v2.0.1", "-m", "release")
            self._git(work, "push", "origin", "refs/tags/v2.0.1")
            self._advance_main(work)
            self._git(work, "switch", "--detach", release_commit)
            self._git(work, "update-ref", "refs/tags/v2.0.1", release_commit)
            state = parent / "state.json"

            initial = self._run(work, "--state-out", str(state))
            self.assertEqual(initial.returncode, 0, initial.stderr)
            payload = json.loads(initial.stdout)
            self.assertEqual(payload["tag_commit"], release_commit)
            self.assertEqual(self._git(work, "cat-file", "-t", "refs/tags/v2.0.1"), "commit")
            self.assertEqual(
                self._git(work, "cat-file", "-t", "refs/aleph-release-tags/v2.0.1"),
                "tag",
            )

            final = self._run(work, "--expected-state", str(state))
            self.assertEqual(final.returncode, 0, final.stderr)

    def test_lightweight_tag_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work, _remote = self._repository(Path(temporary))
            self._git(work, "tag", "v2.0.1")
            self._git(work, "push", "origin", "refs/tags/v2.0.1")
            self.assertEqual(self._failure_code(self._run(work)), "TAG_NOT_ANNOTATED")

    def test_checked_out_commit_must_match_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work, _remote = self._repository(Path(temporary))
            self._git(work, "tag", "-a", "v2.0.1", "-m", "release")
            self._git(work, "push", "origin", "refs/tags/v2.0.1")
            self._advance_main(work)
            self.assertEqual(self._failure_code(self._run(work)), "HEAD_MISMATCH")

    def test_remote_tag_move_is_refused_by_second_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            work, _remote = self._repository(parent)
            release_commit = self._git(work, "rev-parse", "HEAD")
            self._git(work, "tag", "-a", "v2.0.1", "-m", "release")
            self._git(work, "push", "origin", "refs/tags/v2.0.1")
            self._advance_main(work)
            self._git(work, "switch", "--detach", release_commit)
            state = parent / "state.json"
            initial = self._run(work, "--state-out", str(state))
            self.assertEqual(initial.returncode, 0, initial.stderr)

            self._git(work, "switch", "main")
            self._git(work, "tag", "-f", "-a", "v2.0.1", "-m", "moved")
            self._git(work, "push", "--force", "origin", "refs/tags/v2.0.1")
            self._git(work, "switch", "--detach", release_commit)
            self.assertEqual(
                self._failure_code(self._run(work, "--expected-state", str(state))),
                "TAG_MOVED",
            )

    def test_side_branch_tag_is_not_on_main(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work, _remote = self._repository(Path(temporary))
            self._git(work, "switch", "-c", "side")
            side_file = work / "side.txt"
            side_file.write_text("side\n", encoding="utf-8")
            self._git(work, "add", "side.txt")
            self._git(work, "commit", "-m", "side commit")
            self._git(work, "tag", "-a", "v2.0.1", "-m", "side release")
            self._git(work, "push", "origin", "refs/tags/v2.0.1")
            self.assertEqual(self._failure_code(self._run(work)), "NOT_ON_MAIN")

    def test_invalid_tag_format_fails_before_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work, _remote = self._repository(Path(temporary))
            result = subprocess.run(
                [
                    sys.executable,
                    str(VERIFIER),
                    "--repository",
                    str(work),
                    "--tag",
                    "v2.0",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(self._failure_code(result), "TAG_FORMAT")


if __name__ == "__main__":
    unittest.main()
