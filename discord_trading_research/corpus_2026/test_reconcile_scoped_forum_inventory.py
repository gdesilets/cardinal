from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import reconcile_scoped_forum_inventory as reconcile


class ScopedForumInventoryReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.baseline = self.root / "raw" / "forum_thread_inventory.json"
        self.evidence = (
            self.root
            / "raw"
            / "quarantine_collection_errors"
            / "collector_b_premium_journals_fresh_staging_20260721"
            / "premium_journals_2026-01-02_authenticated_group_navigation_evidence_page_2.json"
        )
        self.partial = self.evidence.parent / (
            "collector_b_fresh_channel_premium_journals_1283941772577472643_"
            "2026-01-02_2026-01-02.partial.json"
        )
        self.baseline.parent.mkdir(parents=True)
        self.evidence.parent.mkdir(parents=True)
        shutil.copyfile(reconcile.DEFAULT_BASELINE, self.baseline)
        shutil.copyfile(reconcile.DEFAULT_EVIDENCE, self.evidence)
        source_evidence = json.loads(self.evidence.read_text(encoding="utf-8"))
        original_partial = reconcile.SCRIPT_DIR / source_evidence["source_partial_path"]
        shutil.copyfile(original_partial, self.partial)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def read(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, path: Path, value: dict[str, object]) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def build(self) -> dict[str, object]:
        return reconcile.build_reconciliation(
            self.baseline,
            self.evidence,
            corpus_root=self.root,
            expected_baseline_sha256=self.sha(self.baseline),
            expected_evidence_sha256=self.sha(self.evidence),
        )

    def assert_invalid(self, expected: str) -> None:
        with self.assertRaises(reconcile.ReconciliationValidationError) as raised:
            self.build()
        self.assertTrue(
            any(expected in issue for issue in raised.exception.issues),
            raised.exception.issues,
        )

    def test_builds_158_id_lower_bound_without_changing_sources(self) -> None:
        before = {
            path: self.sha(path) for path in (self.baseline, self.evidence, self.partial)
        }
        result = self.build()
        after = {
            path: self.sha(path) for path in (self.baseline, self.evidence, self.partial)
        }

        self.assertEqual(before, after)
        self.assertEqual(result["status"], "unresolved_census")
        self.assertFalse(result["inventory_complete"])
        self.assertFalse(result["enumeration_complete"])
        self.assertFalse(result["closure_proven"])
        self.assertEqual(result["counts"]["baseline_exact_thread_ids"], 156)
        self.assertEqual(result["counts"]["exact_additional_thread_ids"], 2)
        self.assertEqual(result["counts"]["exact_known_union_thread_ids"], 158)
        self.assertEqual(
            result["added_thread_ids"],
            ["1448404594731516058", "1456316273788063925"],
        )
        self.assertEqual(len(result["exact_known_union_thread_ids"]), 158)
        self.assertEqual(len(set(result["exact_known_union_thread_ids"])), 158)
        self.assertFalse(result["additive_evidence_source"]["proves_census_closure"])

    def test_source_sha_bindings_fail_closed(self) -> None:
        baseline_expected = self.sha(self.baseline)
        evidence_expected = self.sha(self.evidence)

        self.baseline.write_bytes(self.baseline.read_bytes() + b" ")
        with self.assertRaises(reconcile.ReconciliationValidationError) as raised:
            reconcile.build_reconciliation(
                self.baseline,
                self.evidence,
                corpus_root=self.root,
                expected_baseline_sha256=baseline_expected,
                expected_evidence_sha256=evidence_expected,
            )
        self.assertIn("baseline_source_sha256_mismatch", raised.exception.issues)

        shutil.copyfile(reconcile.DEFAULT_BASELINE, self.baseline)
        self.evidence.write_bytes(self.evidence.read_bytes() + b" ")
        with self.assertRaises(reconcile.ReconciliationValidationError) as raised:
            reconcile.build_reconciliation(
                self.baseline,
                self.evidence,
                corpus_root=self.root,
                expected_baseline_sha256=self.sha(self.baseline),
                expected_evidence_sha256=evidence_expected,
            )
        self.assertIn("evidence_source_sha256_mismatch", raised.exception.issues)

        shutil.copyfile(reconcile.DEFAULT_EVIDENCE, self.evidence)
        self.partial.write_bytes(self.partial.read_bytes() + b" ")
        self.assert_invalid("source_partial_sha256_mismatch")

    def test_exact_guild_parent_and_destination_are_required(self) -> None:
        cases = (
            ("guild_id", "999999999999999999", "evidence_wrong_guild"),
            (
                "parent_forum_channel_id",
                "999999999999999999",
                "evidence_wrong_parent",
            ),
        )
        for field, value, expected in cases:
            with self.subTest(field=field):
                payload = self.read(reconcile.DEFAULT_EVIDENCE)
                payload[field] = value
                self.write(self.evidence, payload)
                self.assert_invalid(expected)

        payload = self.read(reconcile.DEFAULT_EVIDENCE)
        payload["groups"][0]["click_destination_url"] = (
            "https://discord.com/channels/999999999999999999/1399473525630173255"
        )
        self.write(self.evidence, payload)
        self.assert_invalid("destination_wrong_guild")

        payload = self.read(reconcile.DEFAULT_EVIDENCE)
        payload["groups"][0]["click_destination_url"] = (
            f"https://discord.com/channels/{reconcile.GUILD_ID}/1456316273788063925"
        )
        self.write(self.evidence, payload)
        self.assert_invalid("destination_thread_mismatch")

    def test_repeated_groups_deduplicate_by_id_and_titles_never_conflate_ids(self) -> None:
        result = self.build()
        observations = {
            row["thread_id"]: row for row in result["navigation_observations"]
        }
        self.assertEqual(len(observations), 4)
        self.assertEqual(
            len(observations["1456316273788063925"]["exact_group_evidence"]), 3
        )

        evidence = self.read(self.evidence)
        partial = self.read(self.partial)
        shared_label = evidence["groups"][1]["group_label"]
        shared_header = evidence["groups"][1]["group_header_text"]
        evidence["groups"][2]["group_label"] = shared_label
        evidence["groups"][2]["group_header_text"] = shared_header
        for row in partial["messages"]:
            if row.get("result_index") == 39:
                row["group_label"] = shared_label
                row["group_header_text"] = shared_header
        self.write(self.partial, partial)
        evidence["source_partial_sha256"] = self.sha(self.partial)
        self.write(self.evidence, evidence)

        result = self.build()
        observations = {
            row["thread_id"]: row for row in result["navigation_observations"]
        }
        self.assertEqual(len(observations), 4)
        self.assertIn("1412565591944073238", observations)
        self.assertIn("1456316273788063925", observations)
        self.assertEqual(
            observations["1412565591944073238"]["observed_display_titles"],
            observations["1456316273788063925"]["observed_display_titles"],
        )

    def test_duplicate_group_membership_is_rejected(self) -> None:
        payload = self.read(self.evidence)
        duplicate = copy.deepcopy(payload["groups"][0])
        duplicate["group_ordinal"] = 7
        payload["groups"].append(duplicate)
        payload["search_group_count"] = 7
        self.write(self.evidence, payload)
        self.assert_invalid("evidence_duplicate_result_index")

    def test_membership_mismatch_with_bound_partial_is_rejected(self) -> None:
        payload = self.read(self.evidence)
        payload["groups"][0]["message_ids"][0] = "1456785922698907777"
        self.write(self.evidence, payload)
        self.assert_invalid("evidence_membership_not_in_bound_partial")


if __name__ == "__main__":
    unittest.main()
