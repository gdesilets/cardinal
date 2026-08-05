from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import build_schema_migration_progress_manifest as migration


class ImmutableMigrationBaselineTests(unittest.TestCase):
    def test_frozen_definition_expands_exact_original_population(self) -> None:
        payload, membership, membership_hash = migration.load_baseline_membership()

        self.assertEqual(242, len(membership))
        self.assertEqual(
            Counter(
                {
                    migration.BASELINE_BUCKET_ZERO: 169,
                    migration.BASELINE_BUCKET_RECAPTURE: 73,
                }
            ),
            Counter(membership.values()),
        )
        self.assertEqual(payload["membership_sha256"], membership_hash)

    def test_post_baseline_bulk_segments_cannot_expand_denominator(self) -> None:
        _payload, membership, _membership_hash = migration.load_baseline_membership()
        new_vc_paths = {
            f"raw/channel_segments/channel_vc_1257820236833489019_2026-{start}_2026-{end}.json"
            for start, end in (
                ("01-01", "01-07"),
                ("01-08", "01-14"),
                ("01-15", "01-21"),
                ("01-22", "01-28"),
                ("01-29", "02-04"),
                ("02-05", "02-11"),
                ("02-12", "02-18"),
                ("02-19", "02-25"),
                ("02-26", "03-04"),
                ("03-05", "03-11"),
            )
        }

        self.assertTrue(new_vc_paths.isdisjoint(membership))
        current_paths = set(membership) | new_vc_paths
        self.assertEqual(242, len(current_paths & set(membership)))
        self.assertEqual(new_vc_paths, current_paths - set(membership))

    def test_definition_tamper_fails_closed(self) -> None:
        payload = json.loads(migration.BASELINE_DEFINITION_PATH.read_text(encoding="utf-8"))
        payload["live_daily_series"]["fresh_recapture_dates"].append("2026-01-05")
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "tampered.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Invalid immutable schema-migration baseline"):
                migration.load_baseline_membership(path)


class CurrentManifestPartitionRegressionTests(unittest.TestCase):
    def test_current_build_keeps_migration_counts_at_242(self) -> None:
        manifest, _summary = migration.build()
        counts = manifest["prior_242_reconciliation"]["current"]["classification_counts"]
        post_baseline = manifest["post_baseline_new_segments"]

        self.assertEqual(242, sum(counts.values()))
        self.assertEqual(242, len(manifest["canonical_segments"]))
        self.assertEqual(242, manifest["current_canonical_inventory"]["baseline_migration_segment_count"])
        self.assertEqual(post_baseline["count"], len(post_baseline["canonical_paths"]))
        self.assertTrue(manifest["current_canonical_inventory"]["partition_is_exhaustive_and_exclusive"])
        self.assertEqual(
            migration.sha256_bytes("\n".join(post_baseline["canonical_paths"]).encode("utf-8")),
            post_baseline["canonical_path_list_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
