from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import timestamp_scope_revalidation as timestamp_scope


MESSAGE_ID = "1501683564796973076"
TIMESTAMP = "2026-05-06T20:34:21.779Z"


class TimestampScopeRevalidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.segment = (
            self.root
            / "raw"
            / "channel_segments"
            / "channel_questions_2026-05-05_2026-05-11.json"
        )
        self.recovery = self.root / "raw" / "evidence" / "timestamp_recovery.json"
        self.segment.parent.mkdir(parents=True)
        self.recovery.parent.mkdir(parents=True)
        self.row = self.make_row()
        self.payload = {"messages": [self.row]}
        self.write_json(self.segment, self.payload)
        self.write_json(self.recovery, self.make_recovery(self.row))

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def make_row() -> dict:
        return {
            "message_id": MESSAGE_ID,
            "article_id": f"search-result-{MESSAGE_ID}",
            "article_aria_labelledby": f"message-content-{MESSAGE_ID}",
            "author": "",
            "author_id": None,
            "content_present": True,
            "content_scope_exact": True,
            "content_text": (
                "Domme\n"
                " pinned a message to this channel. See all pinned messages.\n"
                " — \n"
                "5/6/26, 3:34 PM\n"
                "Wednesday, May 6, 2026 at 3:34 PM"
            ),
            "collection_channel_kind": "text channel",
            "timestamp_scope_exact": True,
            "timestamp_utc": TIMESTAMP,
            "snowflake_timestamp_utc": TIMESTAMP,
            "timestamp_discrepancy_ms": 0,
            "result_index": 668,
            "page_number": 27,
            "result_set_size": 1096,
            "result_listitem_id": "search-results-17",
            "search_query": "in:questions after:2026-05-04 before:2026-05-12",
        }

    @staticmethod
    def make_recovery(row: dict) -> dict:
        return {
            "message_id": row["message_id"],
            "result_index": row["result_index"],
            "search_page": row["page_number"],
            "search_query": row["search_query"],
            "event_text": "Domme pinned a message to this channel. See all pinned messages.",
            "dom_evidence": {
                "article_id": row["article_id"],
                "article_data_list_item_id": f"NO_LIST___{row['message_id']}",
                "owning_listitem_id": row["result_listitem_id"],
                "owning_result_index": row["result_index"],
                "owning_result_set_size": row["result_set_size"],
                "row_owned_time_count": 1,
                "row_owned_time_datetime": TIMESTAMP,
                "row_owned_time_element_id": None,
                "discord_pin_icon_present": True,
            },
            "timestamp_reconciliation": {
                "timestamp_utc": TIMESTAMP,
                "snowflake_timestamp_utc": TIMESTAMP,
                "timestamp_discrepancy_ms": 0,
                "timestamp_scope_exact": True,
            },
        }

    def sidecar_payload(self) -> dict:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "discord_timestamp_scope_revalidation",
            "source_scope": "discord_only",
            "outside_sources_used": False,
            "source_artifact_path": self.segment.relative_to(self.root).as_posix(),
            "source_artifact_sha256": timestamp_scope.sha256_file(self.segment),
            "source_artifact_bytes": self.segment.stat().st_size,
            "revalidations": [
                {
                    "status": "passed",
                    "evidence_type": (
                        "discord_pinned_message_system_event_sole_row_owned_time"
                    ),
                    "message_id": MESSAGE_ID,
                    "result_index": 668,
                    "source_row_sha256": timestamp_scope.row_sha256(self.row),
                    "recovery_evidence": {
                        "path": self.recovery.relative_to(self.root).as_posix(),
                        "sha256": timestamp_scope.sha256_file(self.recovery),
                        "bytes": self.recovery.stat().st_size,
                    },
                    "effective_correction": {
                        "timestamp_scope_exact": False,
                        "row_owned_time_count": 1,
                        "row_owned_time_datetime": TIMESTAMP,
                        "row_owned_time_element_id": None,
                        "discord_system_event_exact": True,
                        "discord_system_event_type": "message_pinned",
                        "timestamp_exact_fallback_source": (
                            timestamp_scope.PIN_FALLBACK_SOURCE
                        ),
                    },
                }
            ],
        }

    def write_sidecar(self, payload: dict | None = None) -> Path:
        sidecar = timestamp_scope.timestamp_scope_revalidation_sidecar_path(
            self.segment
        )
        self.write_json(sidecar, payload or self.sidecar_payload())
        return sidecar

    def load_and_audit(self) -> tuple[object, dict]:
        payload = json.loads(self.segment.read_text(encoding="utf-8"))
        bundle = timestamp_scope.load_adjacent_timestamp_scope_revalidation(
            self.segment,
            payload,
            source_artifact_sha256=timestamp_scope.sha256_file(self.segment),
            artifact_root=self.root,
        )
        return bundle, timestamp_scope.audit_segment_timestamp_scopes(
            payload["messages"], bundle
        )

    def test_literal_true_without_timestamp_aria_is_rejected(self) -> None:
        bundle, audit = self.load_and_audit()
        self.assertFalse(bundle.provided)
        self.assertFalse(audit["passed"])
        self.assertEqual(1, audit["unresolved_count"])

    def test_valid_adjacent_sidecar_applies_exact_pinned_fallback(self) -> None:
        self.write_sidecar()
        bundle, audit = self.load_and_audit()
        self.assertTrue(audit["passed"], audit)
        self.assertTrue(audit["sidecar"]["content_hash_bound"])
        self.assertEqual(
            1,
            audit["mode_counts"][
                f"{timestamp_scope.PIN_FALLBACK_SOURCE}_sidecar_revalidated"
            ],
        )
        self.assertEqual(2, len(bundle.source_artifacts()))

    def test_source_sha_mutation_fails_closed(self) -> None:
        sidecar = self.sidecar_payload()
        sidecar["source_artifact_sha256"] = "0" * 64
        self.write_sidecar(sidecar)
        _bundle, audit = self.load_and_audit()
        self.assertFalse(audit["passed"])
        self.assertTrue(
            any("source_artifact_sha256_mismatch" in error for error in audit["sidecar_errors"]),
            audit,
        )

    def test_recovery_evidence_hash_mutation_fails_closed(self) -> None:
        sidecar = self.sidecar_payload()
        sidecar["revalidations"][0]["recovery_evidence"]["sha256"] = "f" * 64
        self.write_sidecar(sidecar)
        _bundle, audit = self.load_and_audit()
        self.assertFalse(audit["passed"])
        self.assertTrue(
            any("recovery_evidence_sha256_mismatch" in error for error in audit["sidecar_errors"]),
            audit,
        )

    def test_message_id_mutation_fails_closed(self) -> None:
        sidecar = self.sidecar_payload()
        sidecar["revalidations"][0]["message_id"] = "1501683564796973077"
        self.write_sidecar(sidecar)
        _bundle, audit = self.load_and_audit()
        self.assertFalse(audit["passed"])
        self.assertEqual(1, audit["unresolved_count"])
        self.assertTrue(
            any("source_message_row_count_not_one" in error for error in audit["sidecar_errors"]),
            audit,
        )

    def test_canonical_row_mutation_breaks_file_and_row_binding(self) -> None:
        self.write_sidecar()
        changed = copy.deepcopy(self.payload)
        changed["messages"][0]["result_index"] = 669
        self.write_json(self.segment, changed)
        _bundle, audit = self.load_and_audit()
        self.assertFalse(audit["passed"])
        self.assertTrue(
            any("source_artifact_sha256_mismatch" in error for error in audit["sidecar_errors"]),
            audit,
        )

    def test_release_summary_requires_registered_sidecar_hash(self) -> None:
        summary = {
            "schema_version": "1.0.0",
            "passed": True,
            "content_hash_bound": True,
            "unresolved_message_count": 0,
            "invalid_sidecar_count": 0,
            "unused_revalidation_record_count": 0,
            "external_revalidation_message_count": 1,
            "external_revalidation_used_record_count": 1,
            "sidecar_count": 1,
            "sidecars": [
                {
                    "valid": True,
                    "content_hash_bound": True,
                    "record_count": 1,
                    "used_record_count": 1,
                    "unused_record_count": 0,
                    "sidecar_sha256": "a" * 64,
                    "source_artifact_sha256": "b" * 64,
                    "sidecar_path": "raw/channel_segments/a.timestamp-scope-revalidation.json",
                    "segment_path": "raw/channel_segments/a.json",
                    "source_file_id": "sidecar",
                    "evidence_source_file_ids": ["evidence"],
                }
            ],
        }
        payload = {
            "timestamp_scope_integrity": summary,
            "release_gates": [
                {
                    "gate": "timestamp_scope_integrity",
                    "passed": True,
                    "detail": copy.deepcopy(summary),
                }
            ],
            "source_files": [
                {
                    "source_file_id": "segment",
                    "relative_path": "raw/channel_segments/a.json",
                    "kind": "channel_capture_segment",
                    "sha256": "b" * 64,
                },
                {
                    "source_file_id": "sidecar",
                    "relative_path": "raw/channel_segments/a.timestamp-scope-revalidation.json",
                    "kind": "timestamp_scope_revalidation_sidecar",
                    "sha256": "a" * 64,
                },
                {
                    "source_file_id": "evidence",
                    "relative_path": "raw/evidence.json",
                    "kind": "timestamp_scope_recovery_dom_evidence",
                    "sha256": "d" * 64,
                },
            ],
        }
        self.assertEqual(
            [], timestamp_scope.release_timestamp_scope_integrity_errors(payload)
        )
        payload["source_files"][1]["sha256"] = "c" * 64
        errors = timestamp_scope.release_timestamp_scope_integrity_errors(payload)
        self.assertIn("timestamp_scope_sidecar_1_registered_sidecar_source_mismatch", errors)


class CurrentArtifactTimestampScopeIntegrationTests(unittest.TestCase):
    def test_promoted_may_segment_is_accepted_only_via_hash_bound_sidecar(self) -> None:
        root = Path(__file__).resolve().parent
        segment = (
            root
            / "raw"
            / "channel_segments"
            / "channel_questions_1273692573898113076_2026-05-05_2026-05-11.json"
        )
        payload = json.loads(segment.read_text(encoding="utf-8"))
        bundle = timestamp_scope.load_adjacent_timestamp_scope_revalidation(
            segment,
            payload,
            source_artifact_sha256=timestamp_scope.sha256_file(segment),
            artifact_root=root,
        )
        audit = timestamp_scope.audit_segment_timestamp_scopes(
            payload["messages"], bundle
        )
        self.assertTrue(audit["passed"], audit)
        self.assertEqual(
            "ae274fced814246c8d60889f29c745c08660abc815a0039419ae3a05d5dc0559",
            audit["sidecar"]["source_artifact_sha256"],
        )
        self.assertEqual([MESSAGE_ID], audit["sidecar"]["message_ids"])


if __name__ == "__main__":
    unittest.main()
