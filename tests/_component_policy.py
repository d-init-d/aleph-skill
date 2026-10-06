"""Assertions for explicit local-candidate and official-release provenance."""
from aleph.component_registry import candidate_snapshot_digest


def assert_component_provenance(test, entry):
    mode = entry.get("provenance", {}).get("mode", "upstream_release")
    if mode == "local_candidate":
        test.assertEqual(entry["source_archive_format"], "local-profile-snapshot")
        test.assertIs(entry["provenance"]["upstream_attested"], False)
        test.assertEqual(entry["provenance"]["snapshot_sha256"], candidate_snapshot_digest(entry))
        for field in ("source_tag", "upstream_commit", "upstream_tag_object", "upstream_tree"):
            test.assertEqual(entry[field], "")
    else:
        test.assertEqual(mode, "upstream_release")
        test.assertTrue(entry["source_tag"])
        test.assertEqual(entry["source_archive_format"], "git-archive-tar")
        for field in ("upstream_commit", "upstream_tag_object", "upstream_tree"):
            test.assertRegex(entry[field], r"^[0-9a-f]{40}$")
