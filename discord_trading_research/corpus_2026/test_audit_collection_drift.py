#!/usr/bin/env python3
"""Focused tests for the Discord collection-drift release gate."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from audit_collection_drift import (
    DISCORD_EPOCH_MS,
    DriftAuditError,
    audit_collection_drift,
    write_report_atomic,
)


GUILD_ID = "1167376964680691732"
CHANNEL_ID = "1329615478716502097"
PREMIUM_CHANNEL_ID = "1283941772577472643"
START = "2026-03-09"
END = "2026-03-09"
QUERY = "in:Live after:2026-03-08 before:2026-03-10"


def snowflake(at: datetime, increment: int = 0) -> str:
    milliseconds = int(at.timestamp() * 1000)
    return str(((milliseconds - DISCORD_EPOCH_MS) << 22) | increment)


def utc_text(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def artifact(*, total: int, captured: int, complete: bool) -> dict:
    base = datetime(2026, 3, 9, 18, 0, tzinfo=timezone.utc)
    messages = []
    for index in range(1, captured + 1):
        at = base + timedelta(milliseconds=index)
        messages.append(
            {
                "message_id": snowflake(at, index),
                "result_index": index,
                "page_number": (index - 1) // 25 + 1,
                "result_set_size": total,
                "snowflake_timestamp_utc": utc_text(at),
            }
        )
    pages = (total + 24) // 25 if total else 0
    captured_pages = (captured + 24) // 25 if captured else 0
    return {
        "collector_version": "2.4",
        "guild_id": GUILD_ID,
        "collection_scope": "channel-scoped",
        "captured_at_utc": "2026-07-21T00:00:00.000Z",
        "requested_container": {"channel_id": CHANNEL_ID, "channel_name": "Live"},
        "segment": {"start": START, "end": END, "query": QUERY},
        "reported_total": total,
        "reported_pages": pages,
        "pages_captured": captured_pages,
        "captured_rows": captured,
        "unique_message_ids": captured,
        "gap_indices": [],
        "container_mismatch_count": 0,
        "container_mismatch_message_ids": [],
        "complete": complete,
        "messages": messages,
    }


class DriftAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "raw" / "channel_segments").mkdir(parents=True)
        (self.root / "raw" / "quarantine_collection_errors").mkdir(parents=True)
        (self.root / "working").mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_json(self, relative: str, value: dict) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        return path

    def base_note(self, *, suffix: str = "") -> tuple[dict, Path]:
        quarantine_rel = (
            "raw/quarantine_collection_errors/"
            f"channel_live_{CHANNEL_ID}_{START}_{END}.stale{suffix}.partial.json"
        )
        checkpoint_path = self.write_json(quarantine_rel, artifact(total=4, captured=2, complete=False))
        checkpoint_sha = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
        canonical_base = f"raw/channel_segments/channel_live_{CHANNEL_ID}_{START}_{END}"
        note = {
            "event_type": "discord_search_total_drift",
            "guild_id": GUILD_ID,
            "channel_id": CHANNEL_ID,
            "channel_name": "Live",
            "segment_start": START,
            "segment_end": END,
            "query": QUERY,
            "old_reported_total": 4,
            "new_reported_total": 3,
            "old_total_observed_at_utc": "2026-07-21T00:00:00.000Z",
            "new_total_observed_at_utc": "2026-07-21T00:05:00.000Z",
            "source_checkpoint_original_path": canonical_base + ".partial.json",
            "source_checkpoint_quarantine_path": quarantine_rel,
            "source_checkpoint_sha256": checkpoint_sha,
            "source_checkpoint_rows": 2,
            "source_checkpoint_pages": 1,
            "source_checkpoint_unique_message_ids": 2,
            "source_checkpoint_gap_count": 0,
            "restart_artifact_path": canonical_base + ".json",
            "restart_partial_path": canonical_base + ".partial.json",
            "action": "quarantined_stale_checkpoint_and_restart_from_page_1",
            "outside_sources_used": False,
        }
        return note, checkpoint_path

    def install_note(self, note: dict, name: str = "live.total-drift-note.json") -> Path:
        return self.write_json(f"raw/quarantine_collection_errors/{name}", note)

    def install_complete(self, note: dict, total: int | None = None) -> Path:
        total = note["new_reported_total"] if total is None else total
        return self.write_json(note["restart_artifact_path"], artifact(total=total, captured=total, complete=True))

    @staticmethod
    def premium_artifact(*, total: int, captured: int, complete: bool) -> dict:
        value = artifact(total=total, captured=captured, complete=complete)
        value["collector_version"] = "2.6"
        value["requested_container"] = {
            "channel_id": PREMIUM_CHANNEL_ID,
            "channel_name": "premium-journals",
        }
        value["segment"]["query"] = (
            "in:premium-journals after:2026-03-08 before:2026-03-10"
        )
        return value

    def premium_note(self, *, authoritative_root: str) -> dict:
        note, checkpoint = self.base_note()
        query = "in:premium-journals after:2026-03-08 before:2026-03-10"
        checkpoint.write_text(
            json.dumps(
                self.premium_artifact(total=4, captured=2, complete=False),
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        canonical_base = (
            f"{authoritative_root}/channel_premium-journals_"
            f"{PREMIUM_CHANNEL_ID}_{START}_{END}"
        )
        note.update(
            {
                "channel_id": PREMIUM_CHANNEL_ID,
                "channel_name": "premium-journals",
                "query": query,
                "source_checkpoint_original_path": canonical_base + ".partial.json",
                "source_checkpoint_sha256": hashlib.sha256(
                    checkpoint.read_bytes()
                ).hexdigest(),
                "restart_artifact_path": canonical_base + ".json",
                "restart_partial_path": canonical_base + ".partial.json",
            }
        )
        return note

    def malformed_source(self) -> dict:
        value = artifact(total=4, captured=3, complete=False)
        value["messages"][1]["result_index"] = 3
        value["messages"][2]["result_index"] = 4
        value["messages"][1]["message_id"] = value["messages"][2]["message_id"]
        value["messages"][1]["snowflake_timestamp_utc"] = value["messages"][2]["snowflake_timestamp_utc"]
        value["messages"][1]["result_set_size"] = 3
        value["unique_message_ids"] = 2
        value["gap_indices"] = []
        return value

    @staticmethod
    def metrics(value: dict) -> dict:
        return {
            "collector_version": value["collector_version"],
            "reported_total": value["reported_total"],
            "reported_pages": value["reported_pages"],
            "pages_captured": value["pages_captured"],
            "captured_rows": value["captured_rows"],
            "unique_message_ids": value["unique_message_ids"],
            "gap_indices": value["gap_indices"],
            "container_mismatch_count": value["container_mismatch_count"],
            "complete": value["complete"],
        }

    def install_error_resolution(self) -> tuple[dict, Path, Path]:
        invalid = self.malformed_source()
        invalid_rel = (
            "raw/quarantine_collection_errors/"
            f"channel_live_{CHANNEL_ID}_{START}_{END}.invalid-gap.partial.json"
        )
        invalid_path = self.write_json(invalid_rel, invalid)
        canonical = artifact(total=3, captured=3, complete=True)
        canonical_rel = f"raw/channel_segments/channel_live_{CHANNEL_ID}_{START}_{END}.json"
        canonical_path = self.write_json(canonical_rel, canonical)
        duplicate_id = invalid["messages"][2]["message_id"]
        resolution = {
            "event_type": "collection_error_resolution",
            "schema_version": "1.0.0",
            "guild_id": GUILD_ID,
            "channel_id": CHANNEL_ID,
            "channel_name": "Live",
            "segment_start": START,
            "segment_end": END,
            "query": QUERY,
            "resolved_at_utc": "2026-07-21T00:10:00.000Z",
            "invalid_artifact_path": invalid_rel,
            "invalid_artifact_sha256": hashlib.sha256(invalid_path.read_bytes()).hexdigest(),
            "invalid_artifact_metrics": self.metrics(invalid),
            "defects": [
                {"code": "declared_gap_indices_mismatch", "declared": [], "computed": [2]},
                {
                    "code": "duplicate_message_id",
                    "message_id": duplicate_id,
                    "occurrences": 2,
                    "result_indices": [3, 4],
                },
                {"code": "missing_result_index", "result_index": 2},
                {
                    "code": "mixed_result_set_sizes",
                    "counts": [
                        {"reported_total": 3, "rows": 1},
                        {"reported_total": 4, "rows": 2},
                    ],
                },
            ],
            "canonical_replacement_path": canonical_rel,
            "canonical_replacement_sha256": hashlib.sha256(canonical_path.read_bytes()).hexdigest(),
            "canonical_replacement_metrics": self.metrics(canonical),
            "resolution_action": "quarantined_malformed_checkpoint_and_verified_complete_canonical_replacement",
            "outside_sources_used": False,
        }
        self.write_json(
            "raw/quarantine_collection_errors/live.collection-error-resolution.json",
            resolution,
        )
        return resolution, invalid_path, canonical_path

    def issue_codes(self, report: dict) -> set[str]:
        return {row["code"] for row in report["failures"] + report["unresolved"]}

    def test_fully_resolved_drift_passes_final_mode(self) -> None:
        note, _checkpoint = self.base_note()
        self.install_note(note)
        self.install_complete(note)
        report = audit_collection_drift(self.root, mode="final")
        self.assertEqual(report["overall_status"], "PASS")
        self.assertTrue(report["release_gate_passed"])
        self.assertEqual(report["summary"]["resolved_segment_groups"], 1)
        self.assertEqual(report["summary"]["unresolved_count"], 0)
        self.assertEqual(report["notes"][0]["resolution"], "direct")

    def test_premium_drift_uses_dedicated_authoritative_root(self) -> None:
        (self.root / "raw" / "channel_segments_v2_5").mkdir(parents=True)
        note = self.premium_note(authoritative_root="raw/channel_segments_v2_5")
        self.install_note(note, "premium.total-drift-note.json")
        self.write_json(
            note["restart_artifact_path"],
            self.premium_artifact(total=3, captured=3, complete=True),
        )
        report = audit_collection_drift(self.root, mode="final")
        self.assertEqual(report["overall_status"], "PASS")
        self.assertTrue(report["release_gate_passed"])

    def test_premium_drift_rejects_legacy_canonical_root(self) -> None:
        note = self.premium_note(authoritative_root="raw/channel_segments")
        self.install_note(note, "premium-legacy.total-drift-note.json")
        self.write_json(
            note["restart_artifact_path"],
            self.premium_artifact(total=3, captured=3, complete=True),
        )
        report = audit_collection_drift(self.root, mode="final")
        self.assertEqual(report["overall_status"], "FAIL")
        self.assertIn("invalid_note_path", self.issue_codes(report))

    def test_typed_rerender_gap_diagnostic_is_supported(self) -> None:
        note, _checkpoint = self.base_note()
        note["diagnostics"] = {
            "missing_result_index_before_rerender": 3,
            "transient_zero_recount_observed": True,
        }
        self.install_note(note)
        self.install_complete(note)
        report = audit_collection_drift(self.root, mode="final")
        self.assertEqual(report["overall_status"], "PASS")

    def test_drift_diagnostics_reject_unknown_keys_and_bad_types(self) -> None:
        mutations = (
            {"unreviewed_note": 3},
            {"missing_result_index_before_rerender": "3"},
            {"transient_zero_recount_observed": 1},
        )
        for diagnostics in mutations:
            with self.subTest(diagnostics=diagnostics):
                with tempfile.TemporaryDirectory() as temporary:
                    original_root = self.root
                    self.root = Path(temporary)
                    (self.root / "raw" / "channel_segments").mkdir(parents=True)
                    (self.root / "raw" / "quarantine_collection_errors").mkdir(parents=True)
                    (self.root / "working").mkdir()
                    note, _checkpoint = self.base_note()
                    note["diagnostics"] = diagnostics
                    self.install_note(note)
                    report = audit_collection_drift(self.root, mode="collection")
                    self.assertEqual(report["overall_status"], "FAIL")
                    self.root = original_root

    def test_missing_replacement_is_pending_during_collection_and_fails_final(self) -> None:
        note, _checkpoint = self.base_note()
        self.install_note(note)
        collection = audit_collection_drift(self.root, mode="collection")
        final = audit_collection_drift(self.root, mode="final")
        self.assertEqual(collection["overall_status"], "PENDING")
        self.assertEqual(final["overall_status"], "FAIL")
        self.assertIn("drift_note_lacks_final_resolution", self.issue_codes(final))

    def test_replacement_partial_is_explicitly_pending(self) -> None:
        note, _checkpoint = self.base_note()
        self.install_note(note)
        self.write_json(note["restart_partial_path"], artifact(total=3, captured=1, complete=False))
        report = audit_collection_drift(self.root, mode="collection")
        self.assertEqual(report["overall_status"], "PENDING")
        self.assertIn("replacement_still_partial", self.issue_codes(report))

    def test_exact_note_schema_snowflakes_window_totals_and_order_are_enforced(self) -> None:
        mutations = {
            "unexpected schema key": lambda row: row.update({"comment": "not in schema"}),
            "rerender diagnostic must be nested": lambda row: row.update({"missing_result_index_before_rerender": 3}),
            "invalid snowflake": lambda row: row.update({"channel_id": "123"}),
            "wrong query window": lambda row: row.update({"query": "in:Live after:2026-03-09 before:2026-03-10"}),
            "unchanged total": lambda row: row.update({"new_reported_total": 4}),
            "reversed observations": lambda row: row.update({"new_total_observed_at_utc": "2026-07-20T23:59:00Z"}),
        }
        expected_codes = {
            "unexpected schema key": "drift_note_schema_mismatch",
            "rerender diagnostic must be nested": "drift_note_schema_mismatch",
            "invalid snowflake": "invalid_note_snowflake",
            "wrong query window": "query_window_mismatch",
            "unchanged total": "unchanged_reported_total",
            "reversed observations": "observation_order_invalid",
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                # Use a new isolated root for every mutation.
                with tempfile.TemporaryDirectory() as temporary:
                    original_root = self.root
                    self.root = Path(temporary)
                    (self.root / "raw" / "channel_segments").mkdir(parents=True)
                    (self.root / "raw" / "quarantine_collection_errors").mkdir(parents=True)
                    (self.root / "working").mkdir()
                    note, _checkpoint = self.base_note()
                    mutate(note)
                    self.install_note(note)
                    report = audit_collection_drift(self.root, mode="collection")
                    self.assertEqual(report["overall_status"], "FAIL")
                    self.assertIn(expected_codes[label], self.issue_codes(report))
                    self.root = original_root

    def test_checkpoint_hash_mismatch_is_a_hard_failure(self) -> None:
        note, checkpoint = self.base_note()
        self.install_note(note)
        checkpoint.write_text(checkpoint.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        report = audit_collection_drift(self.root, mode="collection")
        self.assertEqual(report["overall_status"], "FAIL")
        self.assertIn("source_checkpoint_hash_mismatch", self.issue_codes(report))
        self.assertEqual(report["summary"]["resolved_segment_groups"], 0)
        self.assertEqual(report["notes"][0]["status"], "invalid")

    def test_checkpoint_note_row_page_unique_and_gap_metrics_are_reconciled(self) -> None:
        note, _checkpoint = self.base_note()
        note["source_checkpoint_rows"] = 3
        note["source_checkpoint_pages"] = 2
        note["source_checkpoint_unique_message_ids"] = 1
        note["source_checkpoint_gap_count"] = 1
        self.install_note(note)
        report = audit_collection_drift(self.root, mode="collection")
        mismatches = [row for row in report["failures"] if row["code"] == "checkpoint_note_metric_mismatch"]
        self.assertEqual(len(mismatches), 4)

    def test_canonical_requires_continuous_unique_indices_and_zero_mismatches(self) -> None:
        note, _checkpoint = self.base_note()
        self.install_note(note)
        broken = artifact(total=3, captured=3, complete=True)
        broken["messages"][1]["result_index"] = 3
        broken["messages"][1]["message_id"] = broken["messages"][0]["message_id"]
        broken["messages"][1]["snowflake_timestamp_utc"] = broken["messages"][0]["snowflake_timestamp_utc"]
        broken["gap_indices"] = [2]
        broken["unique_message_ids"] = 2
        broken["container_mismatch_count"] = 1
        broken["container_mismatch_message_ids"] = [broken["messages"][0]["message_id"]]
        self.write_json(note["restart_artifact_path"], broken)
        report = audit_collection_drift(self.root, mode="final")
        codes = self.issue_codes(report)
        self.assertEqual(report["overall_status"], "FAIL")
        self.assertIn("artifact_duplicate_message_ids", codes)
        self.assertIn("canonical_result_indices_not_continuous", codes)
        self.assertIn("artifact_container_mismatch", codes)

    def test_message_snowflake_must_fall_inside_central_segment_window(self) -> None:
        note, _checkpoint = self.base_note()
        self.install_note(note)
        broken = artifact(total=3, captured=3, complete=True)
        outside = datetime(2026, 3, 11, 18, 0, tzinfo=timezone.utc)
        broken["messages"][0]["message_id"] = snowflake(outside, 1)
        broken["messages"][0]["snowflake_timestamp_utc"] = utc_text(outside)
        self.write_json(note["restart_artifact_path"], broken)
        report = audit_collection_drift(self.root, mode="final")
        self.assertEqual(report["overall_status"], "FAIL")
        self.assertIn("artifact_message_outside_segment_window", self.issue_codes(report))

    def test_complete_plus_partial_or_byte_identical_stale_checkpoint_is_ambiguous(self) -> None:
        note, checkpoint = self.base_note()
        self.install_note(note)
        self.install_complete(note)
        canonical_partial = self.root / note["restart_partial_path"]
        canonical_partial.parent.mkdir(parents=True, exist_ok=True)
        canonical_partial.write_bytes(checkpoint.read_bytes())
        report = audit_collection_drift(self.root, mode="final")
        codes = self.issue_codes(report)
        self.assertIn("stale_checkpoint_present_in_canonical", codes)
        self.assertIn("canonical_segment_ambiguity", codes)

    def test_orphan_quarantined_partial_is_pending_then_final_failure(self) -> None:
        self.write_json("raw/quarantine_collection_errors/orphan.invalid.partial.json", artifact(total=2, captured=1, complete=False))
        collection = audit_collection_drift(self.root, mode="collection")
        final = audit_collection_drift(self.root, mode="final")
        self.assertEqual(collection["overall_status"], "PENDING")
        self.assertEqual(final["overall_status"], "FAIL")
        self.assertIn("orphan_quarantined_partial", self.issue_codes(final))

    def test_valid_collection_error_resolution_clears_orphan_without_becoming_drift(self) -> None:
        _resolution, _invalid, _canonical = self.install_error_resolution()
        report = audit_collection_drift(self.root, mode="final")
        self.assertEqual(report["overall_status"], "PASS")
        self.assertEqual(report["summary"]["drift_note_files"], 0)
        self.assertEqual(report["summary"]["drift_segment_groups"], 0)
        self.assertEqual(report["summary"]["collection_error_resolution_files"], 1)
        self.assertEqual(report["summary"]["valid_non_drift_collection_error_resolutions"], 1)
        self.assertEqual(report["summary"]["orphan_quarantined_partial_count"], 0)
        self.assertEqual(report["notes"], [])
        self.assertEqual(
            report["collection_error_resolutions"][0]["status"],
            "resolved_non_drift_collection_error",
        )

    def test_missing_or_bad_defect_evidence_fails_and_leaves_orphan(self) -> None:
        mutations = {
            "missing defect": lambda row: row["defects"].pop(),
            "bad detail": lambda row: row["defects"][1].update({"occurrences": 3}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temporary:
                    original_root = self.root
                    self.root = Path(temporary)
                    (self.root / "raw" / "channel_segments").mkdir(parents=True)
                    (self.root / "raw" / "quarantine_collection_errors").mkdir(parents=True)
                    (self.root / "working").mkdir()
                    resolution, _invalid, _canonical = self.install_error_resolution()
                    mutate(resolution)
                    self.write_json(
                        "raw/quarantine_collection_errors/live.collection-error-resolution.json",
                        resolution,
                    )
                    report = audit_collection_drift(self.root, mode="final")
                    self.assertEqual(report["overall_status"], "FAIL")
                    self.assertIn("resolution_defect_evidence_mismatch", self.issue_codes(report))
                    self.assertEqual(report["summary"]["valid_non_drift_collection_error_resolutions"], 0)
                    self.assertEqual(report["summary"]["orphan_quarantined_partial_count"], 1)
                    self.root = original_root

    def test_resolution_source_and_canonical_hashes_are_both_mandatory(self) -> None:
        resolution, invalid_path, canonical_path = self.install_error_resolution()
        invalid_path.write_text(invalid_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        canonical_path.write_text(canonical_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        report = audit_collection_drift(self.root, mode="final")
        codes = self.issue_codes(report)
        self.assertIn("resolution_invalid_artifact_hash_mismatch", codes)
        self.assertIn("resolution_canonical_hash_mismatch", codes)
        self.assertEqual(report["summary"]["orphan_quarantined_partial_count"], 1)

    def test_chained_drift_resolves_at_latest_total_without_hiding_history(self) -> None:
        first, _checkpoint = self.base_note(suffix="-one")
        self.install_note(first, "one.total-drift-note.json")
        second_checkpoint_rel = (
            "raw/quarantine_collection_errors/"
            f"channel_live_{CHANNEL_ID}_{START}_{END}.stale-two.partial.json"
        )
        second_checkpoint = artifact(total=3, captured=1, complete=False)
        second_checkpoint["captured_at_utc"] = "2026-07-21T00:06:00.000Z"
        second_path = self.write_json(second_checkpoint_rel, second_checkpoint)
        second = deepcopy(first)
        second.update(
            {
                "old_reported_total": 3,
                "new_reported_total": 2,
                "old_total_observed_at_utc": "2026-07-21T00:06:00.000Z",
                "new_total_observed_at_utc": "2026-07-21T00:10:00.000Z",
                "source_checkpoint_quarantine_path": second_checkpoint_rel,
                "source_checkpoint_sha256": hashlib.sha256(second_path.read_bytes()).hexdigest(),
                "source_checkpoint_rows": 1,
                "source_checkpoint_pages": 1,
                "source_checkpoint_unique_message_ids": 1,
            }
        )
        self.install_note(second, "two.total-drift-note.json")
        self.install_complete(second)
        report = audit_collection_drift(self.root, mode="final")
        self.assertEqual(report["overall_status"], "PASS")
        resolutions = {row["note_path"]: row["resolution"] for row in report["notes"]}
        self.assertEqual(resolutions["raw/quarantine_collection_errors/one.total-drift-note.json"], "superseded_by_valid_later_drift")
        self.assertEqual(resolutions["raw/quarantine_collection_errors/two.total-drift-note.json"], "direct")

    def test_report_write_is_atomic_working_only_and_refuses_implicit_overwrite(self) -> None:
        report = audit_collection_drift(self.root, mode="collection")
        output = Path("working/audit.json")
        write_report_atomic(self.root, output, report)
        before = (self.root / output).read_bytes()
        with self.assertRaises(DriftAuditError):
            write_report_atomic(self.root, output, report)
        self.assertEqual((self.root / output).read_bytes(), before)
        write_report_atomic(self.root, output, report, overwrite=True)
        with self.assertRaises(DriftAuditError):
            write_report_atomic(self.root, Path("raw/forbidden.json"), report)
        self.assertFalse(list((self.root / "working").glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
