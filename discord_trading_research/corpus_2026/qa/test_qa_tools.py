from __future__ import annotations

import datetime as dt
import base64
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


QA_DIR = Path(__file__).resolve().parent
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))
PACKAGE_DIR = QA_DIR.parent
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

import build_cardinal_database_v2
import discord_attachment_archiver
import premium_journals_provenance_contract
import preservation_hashes
import validate_corpus


def snowflake_for(instant: dt.datetime) -> str:
    milliseconds = int(instant.timestamp() * 1000)
    return str((milliseconds - validate_corpus.DISCORD_EPOCH_MS) << 22)


class QAToolTests(unittest.TestCase):
    def _validate_premium_segment(
        self, path: Path
    ) -> tuple[validate_corpus.SegmentArtifact | None, dict[str, list[dict]]]:
        issues: dict[str, list[dict]] = {}
        artifact = validate_corpus.validate_one_segment(
            path,
            guild_id=validate_corpus.DEFAULT_GUILD_ID,
            window_start=dt.date(2026, 1, 1),
            window_end=dt.date(2026, 7, 20),
            cutoff_utc=dt.datetime(2026, 7, 21, tzinfo=dt.timezone.utc),
            issues=issues,
        )
        return artifact, issues

    def _clone_real_premium_day(self, target_root: Path, day: str = "01") -> Path:
        relative = Path(
            "raw/channel_segments_v2_5/"
            f"channel_premium_journals_1283941772577472643_"
            f"2026-01-{day}_2026-01-{day}.json"
        )
        source_path = PACKAGE_DIR / relative
        accepted = (
            premium_journals_provenance_contract
            .validate_premium_row_container_bindings(
                source_path,
                artifact_root=PACKAGE_DIR,
            )["accepted_artifact"]
        )
        for bound in accepted["source_files"]:
            source = PACKAGE_DIR / str(bound["path"])
            destination = target_root / str(bound["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        return target_root / relative

    def test_real_premium_rows_use_only_byte_bound_forum_container_ids(self) -> None:
        for day, expected_navigation_source_count in (
            ("01", 59),
            ("02", 177),
            ("03", 50),
        ):
            with self.subTest(day=day):
                path = PACKAGE_DIR / (
                    "raw/channel_segments_v2_5/"
                    f"channel_premium_journals_1283941772577472643_"
                    f"2026-01-{day}_2026-01-{day}.json"
                )
                artifact, issues = self._validate_premium_segment(path)
                self.assertIsNotNone(artifact)
                self.assertEqual(issues, {})
                self.assertEqual(
                    len(artifact.premium_forum_provenance_source_records),
                    expected_navigation_source_count,
                )

    def test_premium_generic_qa_rejects_tampered_unbound_or_inferred_only_rows(
        self,
    ) -> None:
        for mutation in ("tampered_checkpoint", "missing_checkpoint", "inferred_only"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = self._clone_real_premium_day(root)
                accepted = (
                    premium_journals_provenance_contract
                    .validate_premium_row_container_bindings(
                        path,
                        artifact_root=root,
                    )["accepted_artifact"]
                )
                checkpoint = next(
                    root / str(row["path"])
                    for row in accepted["source_files"]
                    if row["role"] == "forum_group_navigation_checkpoint"
                )
                if mutation == "tampered_checkpoint":
                    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
                    payload["tampered"] = True
                    checkpoint.write_text(json.dumps(payload), encoding="utf-8")
                elif mutation == "missing_checkpoint":
                    checkpoint.unlink()
                else:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    payload["messages"][0]["thread_channel_id_source"] = (
                        "legacy_inferred_container_id"
                    )
                    path.write_text(json.dumps(payload), encoding="utf-8")

                artifact, issues = self._validate_premium_segment(path)
                self.assertIsNotNone(artifact)
                self.assertIn("invalid_premium_forum_provenance", issues)
                self.assertEqual(len(issues["missing_exact_container_id"]), 198)
                self.assertEqual(
                    artifact.premium_forum_provenance_source_records,
                    [],
                )

    def test_terminal_attachment_archive_is_rehashed_and_reconciled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            message_id = "1457078514107941056"
            attachment_id = "1457078513864802415"
            channel_id = "1359593949110472777"
            corpus_path = root / "corpus.json"
            manifest_path = root / "manifest.json"
            archive_root = root / "archive"
            corpus_path.write_text(
                json.dumps(
                    {
                        "artifact_type": "discord_serverwide_corpus_working",
                        "scope": {"guild_id": validate_corpus.DEFAULT_GUILD_ID},
                        "messages": [
                            {
                                "message_id": message_id,
                                "attachments": [
                                    {
                                        "attachment_id": attachment_id,
                                        "relation_type": "owned",
                                        "ownership_status": "owned_exact",
                                        "ownership_evidence": {
                                            "schema_version": "1.0.0",
                                            "exact": True,
                                            "basis": "test_exact_message_accessories",
                                            "owner_message_id": message_id,
                                            "owner_channel_id": channel_id,
                                            "source_channel_id": channel_id,
                                        },
                                        "filename": "chart.png",
                                        "url": (
                                            f"https://cdn.discordapp.com/attachments/{channel_id}/"
                                            f"{attachment_id}/chart.png"
                                        ),
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            manifest = discord_attachment_archiver.create_or_resume_manifest(
                corpus_path, manifest_path
            )
            entry = manifest["entries"][0]
            body = b"durable chart bytes"
            discord_attachment_archiver.ingest_browser_response(
                manifest,
                {
                    "contract": "discord_attachment_browser_response_v1",
                    "request_id": entry["request_id"],
                    "message_id": message_id,
                    "attachment_id": attachment_id,
                    "final_url": entry["discord_url"],
                    "status": "downloaded",
                    "http_status": 200,
                    "attempted_at_utc": "2026-07-21T05:05:00Z",
                    "body_base64": base64.b64encode(body).decode("ascii"),
                    "outside_sources_used": 0,
                    "credentials_or_browser_storage_inspected": False,
                },
                archive_root,
            )
            staging = root / "staging"
            staging.mkdir()
            (staging / "ocr.txt").write_text(
                "verified local extraction", encoding="utf-8"
            )
            extraction = discord_attachment_archiver.record_extraction(
                manifest,
                {
                    "attachment_id": attachment_id,
                    "status": "complete",
                    "method": "local_ocr_v1",
                    "created_at_utc": "2026-07-21T05:06:00Z",
                    "staged_file": "ocr.txt",
                    "filename": "ocr.txt",
                },
                archive_root,
                staging_root=staging,
            )
            discord_attachment_archiver.write_json_atomic(manifest_path, manifest)
            recorder = validate_corpus.CheckRecorder()
            auxiliary: list[dict] = []
            summary, ids = validate_corpus.validate_attachment_archive(
                recorder,
                manifest_path,
                archive_root,
                {attachment_id},
                auxiliary,
            )
            self.assertTrue(summary["terminal_coverage_complete"])
            self.assertTrue(summary["entry_set_parity"])
            self.assertEqual(ids, {attachment_id})
            self.assertTrue(all(row["passed"] for row in recorder.checks))
            self.assertEqual(auxiliary[0]["kind"], "discord_attachment_archive_manifest")
            self.assertEqual(
                summary["verification"]["verified_extraction_artifact_count"], 1
            )

            extraction_path = discord_attachment_archiver.resolve_under(
                archive_root,
                extraction["local_package_path"],
                label="QA extraction artifact",
            )
            extraction_path.write_text("tampered", encoding="utf-8")
            tampered_recorder = validate_corpus.CheckRecorder()
            tampered_summary, _ = validate_corpus.validate_attachment_archive(
                tampered_recorder,
                manifest_path,
                archive_root,
                {attachment_id},
                [],
            )
            self.assertEqual(tampered_summary["verification"]["status"], "failed")
            integrity = {
                row["name"]: row for row in tampered_recorder.checks
            }["attachment_archive_byte_integrity"]
            self.assertFalse(integrity["passed"])

    def build_inventory_extension_database(self, root: Path) -> tuple[Path, str, str, str]:
        parent_id = "1283941772577472643"
        thread_id = "1480000000000000801"
        message_id = "1480000000000000802"
        corpus = root / "inventory-extension.json"
        database = root / "inventory-extension.sqlite"
        corpus.write_text(
            json.dumps(
                {
                    "schema_version": "2.1.0",
                    "artifact_type": "discord_serverwide_working_corpus",
                    "scope": {
                        "guild_id": validate_corpus.DEFAULT_GUILD_ID,
                        "utc_start_inclusive": "2026-01-01T06:00:00Z",
                        "utc_end_exclusive": "2026-01-02T06:00:00Z",
                    },
                    "inventory": {
                        "containers": [
                            {
                                "container_id": parent_id,
                                "name": "premium-journals",
                                "kind": "forum channel",
                                "inventory_layer": "top_level_container",
                                "accessible": True,
                            },
                            {
                                "container_id": thread_id,
                                "parent_container_id": parent_id,
                                "name": "journal-one",
                                "kind": "forum thread",
                                "inventory_layer": "observed_forum_thread",
                                "accessible": True,
                            },
                        ]
                    },
                    "coverage": {
                        "containers": [
                            {
                                "container_id": parent_id,
                                "name": "premium-journals",
                                "kind": "forum channel",
                                "status": "complete",
                            },
                            {
                                "container_id": thread_id,
                                "name": "journal-one",
                                "kind": "forum thread",
                                "status": "complete",
                            },
                        ]
                    },
                    "messages": [
                        {
                            "message_id": message_id,
                            "channel_id": thread_id,
                            "parent_channel_id": parent_id,
                            "channel_name": "journal-one",
                            "author": "Member",
                            "timestamp_utc": "2026-01-01T14:00:00Z",
                            "content_text": "provenance-backed observed forum thread",
                            "evidence_trust_state": "trusted_source",
                            "eligible_for_accepted_evidence": True,
                        }
                    ],
                    "occurrences": [
                        {
                            "occurrence_id": "occurrence:inventory-extension",
                            "message_id": message_id,
                            "source_kind": "channel_segment",
                            "migration_source": False,
                            "quarantined": False,
                            "trusted_canonical": True,
                            "source_file": "raw/channel_segments/forum.json",
                            "collection_name": "channel_segment",
                            "query_text": "in:premium-journals",
                            "result_index": 0,
                            "page_number": 1,
                            "payload": {"message_id": message_id},
                            "provenance": {"complete_source": True},
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        report = build_cardinal_database_v2.build_database(
            [corpus], database, window_start="2026-01-01", window_end="2026-01-01"
        )
        self.assertEqual(report["status"], "passed")
        return database, parent_id, thread_id, message_id

    def test_central_window_boundaries_cross_dst(self) -> None:
        self.assertEqual(
            validate_corpus.central_midnight_utc(dt.date(2026, 1, 1)),
            dt.datetime(2026, 1, 1, 6, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(
            validate_corpus.central_midnight_utc(dt.date(2026, 7, 21)),
            dt.datetime(2026, 7, 21, 5, tzinfo=dt.timezone.utc),
        )

    def test_merged_inventory_nested_forum_completion_contract_is_accepted(self) -> None:
        recorder = validate_corpus.CheckRecorder()
        payload = {
            "guild_id": validate_corpus.DEFAULT_GUILD_ID,
            "source_scope": "discord_only",
            "outside_sources_used": False,
            "inventory_complete": True,
            "status": "complete",
            "captured_at_utc": "2026-07-21T05:01:00Z",
            "requested_local_window": {
                "timezone": "America/Chicago",
                "start_inclusive": "2026-01-01T06:00:00Z",
                "end_exclusive": "2026-07-21T05:00:00Z",
            },
            "accessible_scope": {
                "forum_threads": {
                    "declared_complete": True,
                    "status": "complete",
                }
            },
            "completeness": {
                "active_forum_thread_enumeration_complete": True,
                "discoverable_archived_forum_thread_enumeration_complete": True,
            },
        }
        rows = [
            {
                "container_id": "1283941772577472643",
                "name": "premium-journals",
                "kind": "forum channel",
                "count_status": "ok",
                "accessible": True,
            },
            {
                "container_id": "1480000000000000801",
                "name": "journal-one",
                "kind": "forum thread",
                "count_status": "complete_parent_forum_enumeration",
                "accessible": True,
            },
        ]
        summary = validate_corpus.validate_inventory_contract(
            recorder,
            payload,
            rows,
            validate_corpus.DEFAULT_GUILD_ID,
            dt.date(2026, 1, 1),
            dt.date(2026, 7, 20),
        )

        self.assertEqual(summary["status"], "complete")
        checks = {row["name"]: row for row in recorder.checks}
        self.assertTrue(
            checks["inventory_declares_channel_and_thread_completion"]["passed"]
        )
        self.assertTrue(checks["inventory_reported_counts_valid"]["passed"])

    def test_valid_zero_result_segment_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "empty_2026-01-01_2026-01-01.json"
            payload = {
                "collector_version": "test",
                "captured_at_utc": "2026-07-21T05:01:00Z",
                "guild_id": validate_corpus.DEFAULT_GUILD_ID,
                "collection_scope": "guild-wide",
                "requested_container": {
                    "kind": "guild-wide",
                    "guild_id": validate_corpus.DEFAULT_GUILD_ID,
                },
                "segment": {
                    "start": "2026-01-01",
                    "end": "2026-01-01",
                    "query": "after:2025-12-31 before:2026-01-02",
                },
                "reported_total": 0,
                "reported_pages": 0,
                "pages_captured": 0,
                "captured_rows": 0,
                "unique_message_ids": 0,
                "gap_indices": [],
                "complete": True,
                "messages": [],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            issues: dict[str, list[dict[str, object]]] = {}
            artifact = validate_corpus.validate_one_segment(
                path,
                guild_id=validate_corpus.DEFAULT_GUILD_ID,
                window_start=dt.date(2026, 1, 1),
                window_end=dt.date(2026, 7, 20),
                cutoff_utc=dt.datetime(2026, 7, 21, 5, 1, tzinfo=dt.timezone.utc),
                issues=issues,
            )
            self.assertIsNotNone(artifact)
            self.assertNotIn("invalid_zero_result_segment", issues)
            self.assertNotIn("invalid_totals_pages_indices", issues)

    def test_numeric_complete_requires_valid_sha_bound_terminal_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "empty_2026-01-01_2026-01-01.json"
            query = "after:2025-12-31 before:2026-01-02"
            payload = {
                "collector_version": "2.4",
                "captured_at_utc": "2026-07-21T05:01:00Z",
                "guild_id": validate_corpus.DEFAULT_GUILD_ID,
                "collection_scope": "guild-wide",
                "requested_container": {
                    "kind": "guild-wide",
                    "guild_id": validate_corpus.DEFAULT_GUILD_ID,
                },
                "segment": {
                    "start": "2026-01-01",
                    "end": "2026-01-01",
                    "query": query,
                },
                "reported_total": 0,
                "reported_pages": 0,
                "pages_captured": 0,
                "captured_rows": 0,
                "unique_message_ids": 0,
                "gap_indices": [],
                "complete": True,
                "messages": [],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")

            issues: dict[str, list[dict[str, object]]] = {}
            validate_corpus.validate_one_segment(
                path,
                guild_id=validate_corpus.DEFAULT_GUILD_ID,
                window_start=dt.date(2026, 1, 1),
                window_end=dt.date(2026, 7, 20),
                cutoff_utc=dt.datetime(2026, 7, 21, 5, 1, tzinfo=dt.timezone.utc),
                issues=issues,
            )
            self.assertIn("invalid_completion_evidence", issues)

            observations = [
                {
                    "sequence": index,
                    "state": "empty_candidate",
                    "visible_result_count": 0,
                    "panel_text": "No Results",
                    "observed_at_utc": f"2026-07-21T05:01:0{index}Z",
                }
                for index in range(1, 4)
            ]
            evidence = {
                "schema_version": "1.0.0",
                "query": query,
                "reported_total": 0,
                "reported_pages": 0,
                "terminal_state": "stable_empty",
                "search_submission": {
                    "mode": "fresh",
                    "submission_count": 1,
                    "query": query,
                    "submitted_at_utc": "2026-07-21T05:01:00Z",
                },
                "stable_empty": {
                    "required_observations": 3,
                    "observations": observations,
                },
            }
            sidecar = {
                "artifact_type": "discord_segment_completion_evidence_sidecar",
                "schema_version": "1.0.0",
                "source_artifact_path": path.name,
                "source_artifact_sha256": validate_corpus.sha256_bytes(path.read_bytes()),
                "guild_id": payload["guild_id"],
                "requested_container": payload["requested_container"],
                "segment": payload["segment"],
                "reported_total": 0,
                "reported_pages": 0,
                "completion_evidence": evidence,
            }
            validate_corpus.completion_evidence_sidecar_path(path).write_text(
                json.dumps(sidecar), encoding="utf-8"
            )
            issues = {}
            validate_corpus.validate_one_segment(
                path,
                guild_id=validate_corpus.DEFAULT_GUILD_ID,
                window_start=dt.date(2026, 1, 1),
                window_end=dt.date(2026, 7, 20),
                cutoff_utc=dt.datetime(2026, 7, 21, 5, 1, tzinfo=dt.timezone.utc),
                issues=issues,
            )
            self.assertNotIn("invalid_completion_evidence", issues)

            sidecar_path = validate_corpus.completion_evidence_sidecar_path(path)
            sidecar["source_artifact_path"] = "wrong-source.json"
            sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
            issues = {}
            validate_corpus.validate_one_segment(
                path,
                guild_id=validate_corpus.DEFAULT_GUILD_ID,
                window_start=dt.date(2026, 1, 1),
                window_end=dt.date(2026, 7, 20),
                cutoff_utc=dt.datetime(2026, 7, 21, 5, 1, tzinfo=dt.timezone.utc),
                issues=issues,
            )
            self.assertIn("invalid_completion_evidence", issues)
            self.assertIn(
                "completion_evidence_sidecar_source_path_mismatch",
                issues["invalid_completion_evidence"][0]["errors"],
            )

    def test_stable_bottom_requires_explicit_disabled_next_observation(self) -> None:
        query = "in:test after:2025-12-31 before:2026-01-02"
        observations = [
            {
                "sequence": index,
                "observed_at_utc": f"2026-07-21T05:01:0{index}Z",
                "query": query,
                "visible_result_count": 5,
                "first_result_index": 26,
                "last_result_index": 30,
                "current_page": 2,
                "result_set_size": 30,
            }
            for index in range(1, 3)
        ]
        errors = validate_corpus.validate_completion_evidence(
            {
                "schema_version": "1.0.0",
                "query": query,
                "reported_total": 30,
                "reported_pages": 2,
                "terminal_state": "stable_bottom",
                "search_submission": {
                    "mode": "fresh",
                    "submission_count": 1,
                    "query": query,
                    "submitted_at_utc": "2026-07-21T05:01:00Z",
                },
                "stable_bottom": {
                    "required_observations": 2,
                    "observations": observations,
                },
            },
            query=query,
            reported_total=30,
            reported_pages=2,
        )
        self.assertIn("stable_bottom_next_disabled_not_proven", errors)

    def test_snowflake_timestamp_mismatch_is_detected(self) -> None:
        instant = dt.datetime(2026, 1, 1, 12, tzinfo=dt.timezone.utc)
        message_id = snowflake_for(instant)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "one_2026-01-01_2026-01-01.json"
            payload = {
                "collector_version": "test",
                "captured_at_utc": "2026-07-21T05:01:00Z",
                "guild_id": validate_corpus.DEFAULT_GUILD_ID,
                "collection_scope": {"kind": "channel-scoped", "channel_id": "1283941772577472643"},
                "segment": {
                    "start": "2026-01-01",
                    "end": "2026-01-01",
                    "query": "in:test after:2025-12-31 before:2026-01-02",
                },
                "reported_total": 1,
                "reported_pages": 1,
                "pages_captured": 1,
                "captured_rows": 1,
                "unique_message_ids": 1,
                "gap_indices": [],
                "complete": True,
                "messages": [
                    {
                        "message_id": message_id,
                        "channel_id": "1283941772577472643",
                        "result_index": 1,
                        "page_number": 1,
                        "timestamp_utc": "2026-01-01T12:05:00Z",
                        "article_id": f"search-result-{message_id}",
                        "article_aria_labelledby": f"message-content-{message_id} message-timestamp-{message_id}",
                        "content_present": True,
                        "content_text": "test",
                        "attachments": [],
                    }
                ],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            issues: dict[str, list[dict[str, object]]] = {}
            validate_corpus.validate_one_segment(
                path,
                guild_id=validate_corpus.DEFAULT_GUILD_ID,
                window_start=dt.date(2026, 1, 1),
                window_end=dt.date(2026, 7, 20),
                cutoff_utc=dt.datetime(2026, 7, 21, 5, 1, tzinfo=dt.timezone.utc),
                issues=issues,
            )
            self.assertEqual(len(issues["snowflake_timestamp_mismatch"]), 1)

    def test_exact_stage_system_event_uses_narrow_snowflake_timestamp_fallback(self) -> None:
        instant = dt.datetime(2026, 1, 14, 16, 33, 35, 323000, tzinfo=dt.timezone.utc)
        message_id = snowflake_for(instant)
        timestamp = instant.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        base = {
            "message_id": message_id,
            "channel_id": "1329615478716502097",
            "collection_channel_kind": "stage channel",
            "result_index": 1,
            "page_number": 1,
            "author": "",
            "author_id": None,
            "timestamp_utc": timestamp,
            "snowflake_timestamp_utc": timestamp,
            "timestamp_discrepancy_ms": 0,
            "timestamp_scope_exact": False,
            "article_id": f"search-result-{message_id}",
            "article_aria_labelledby": f"message-content-{message_id}",
            "content_present": True,
            "content_scope_exact": True,
            "content_text": "Powell\nis now a speaker.\n—\n1/14/26, 10:33 AM",
            "attachments": [],
        }
        self.assertTrue(validate_corpus.exact_stage_system_event_timestamp_fallback(base, message_id))
        for mutation in (
            {"collection_channel_kind": "text channel"},
            {"author": "Powell"},
            {"timestamp_discrepancy_ms": 1},
            {"timestamp_discrepancy_ms": None},
            {"content_text": "Powell\nordinary message\n1/14/26, 10:33 AM"},
            {"article_aria_labelledby": f"message-content-{message_id} other"},
        ):
            self.assertFalse(
                validate_corpus.exact_stage_system_event_timestamp_fallback(
                    {**base, **mutation}, message_id
                )
            )

    def test_exact_poll_close_system_event_uses_narrow_snowflake_timestamp_fallback(self) -> None:
        instant = dt.datetime(2026, 1, 26, 22, 16, 45, 754000, tzinfo=dt.timezone.utc)
        message_id = snowflake_for(instant)
        timestamp = instant.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        base = {
            "message_id": message_id,
            "collection_channel_kind": "stage channel",
            "author": "",
            "author_id": None,
            "timestamp_utc": timestamp,
            "snowflake_timestamp_utc": timestamp,
            "timestamp_discrepancy_ms": 0,
            "timestamp_scope_exact": False,
            "content_scope_exact": True,
            "article_aria_labelledby": (
                f"message-content-{message_id} message-accessories-{message_id}"
            ),
            "content_text": (
                "yarin's poll what boy should play? has closed.\n—\n1/26/26, 4:16 PM\n"
                "Monday, January 26, 2026 at 4:16 PM\nThe results were tied\n50%"
            ),
        }
        self.assertTrue(validate_corpus.exact_stage_system_event_timestamp_fallback(base, message_id))
        winning_answer = {
            **base,
            "content_text": (
                "kp's poll Does Erik Hit TP? has closed.\n—\n3/9/26, 7:28 PM\n"
                "Monday, March 9, 2026 at 7:28 PM\nyes\nWinning answer • 63%"
            ),
        }
        self.assertTrue(
            validate_corpus.exact_stage_system_event_timestamp_fallback(
                winning_answer, message_id
            )
        )
        self.assertFalse(
            validate_corpus.exact_stage_system_event_timestamp_fallback(
                {**base, "content_text": "yarin's poll what boy should play? has closed.\n50%"},
                message_id,
            )
        )

        duplicate_id = "1473403911636258939"
        duplicate_time = validate_corpus.snowflake_time(duplicate_id).isoformat().replace(
            "+00:00", "Z"
        )
        duplicate = {
            "message_id": duplicate_id,
            "collection_channel_kind": "stage channel",
            "author": "",
            "author_id": None,
            "timestamp_utc": duplicate_time,
            "snowflake_timestamp_utc": duplicate_time,
            "timestamp_discrepancy_ms": 0,
            "timestamp_scope_exact": False,
            "content_scope_exact": True,
            "article_aria_labelledby": f"message-content-{duplicate_id}",
            "content_text": "tig\ntig\n ended NY Session\n—\n2/17/26, 8:00 AM",
        }
        self.assertTrue(
            validate_corpus.exact_stage_system_event_timestamp_fallback(
                duplicate, duplicate_id
            )
        )
        self.assertFalse(
            validate_corpus.exact_stage_system_event_timestamp_fallback(
                {
                    **duplicate,
                    "content_text": "tig\nother\n ended NY Session\n—\n2/17/26, 8:00 AM",
                },
                duplicate_id,
            )
        )

    def test_exact_pinned_message_event_uses_only_the_sole_row_owned_time(self) -> None:
        message_id = "1501683564796973076"
        timestamp = "2026-05-06T20:34:21.779Z"
        base = {
            "message_id": message_id,
            "channel_id": "1273692573898113076",
            "collection_channel_kind": "text channel",
            "author": "",
            "author_id": None,
            "article_id": f"search-result-{message_id}",
            "article_aria_labelledby": f"message-content-{message_id}",
            "content_present": True,
            "content_scope_exact": True,
            "timestamp_scope_exact": False,
            "timestamp_utc": timestamp,
            "snowflake_timestamp_utc": timestamp,
            "timestamp_discrepancy_ms": 0,
            "row_owned_time_count": 1,
            "row_owned_time_datetime": timestamp,
            "row_owned_time_element_id": None,
            "discord_system_event_exact": True,
            "discord_system_event_type": "message_pinned",
            "timestamp_exact_fallback_source": (
                "discord_snowflake_exact_pinned_message_system_event"
            ),
            "content_text": (
                "Domme\npinned a message to this channel. See all pinned messages.\n"
                "\u2014\n5/6/26, 3:34 PM\nWednesday, May 6, 2026 at 3:34 PM"
            ),
            "result_index": 1,
            "page_number": 1,
            "attachments": [],
        }
        self.assertTrue(
            validate_corpus.exact_pinned_message_system_event_timestamp_fallback(
                base, message_id
            )
        )
        fail_closed_mutations = (
            {"collection_channel_kind": "stage channel"},
            {"article_id": f"search-result-{int(message_id) + 1}"},
            {
                "article_aria_labelledby": (
                    f"message-content-{message_id} message-timestamp-{message_id}"
                )
            },
            {"author": "Domme"},
            {"row_owned_time_count": 2},
            {"row_owned_time_datetime": "2026-05-06T20:34:22.779Z"},
            {"row_owned_time_element_id": f"message-timestamp-{message_id}"},
            {"discord_system_event_exact": False},
            {"discord_system_event_type": None},
            {"timestamp_exact_fallback_source": None},
            {"timestamp_discrepancy_ms": 1},
            {"timestamp_discrepancy_ms": None},
            {"content_text": "Domme\nordinary message"},
        )
        for mutation in fail_closed_mutations:
            self.assertFalse(
                validate_corpus.exact_pinned_message_system_event_timestamp_fallback(
                    {**base, **mutation}, message_id
                )
            )

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "questions_2026-05-06_2026-05-06.json"
            payload = {
                "collector_version": "test",
                "captured_at_utc": "2026-07-21T05:01:00Z",
                "guild_id": validate_corpus.DEFAULT_GUILD_ID,
                "collection_scope": {
                    "kind": "channel-scoped",
                    "channel_id": "1273692573898113076",
                },
                "segment": {
                    "start": "2026-05-06",
                    "end": "2026-05-06",
                    "query": "in:questions after:2026-05-05 before:2026-05-07",
                },
                "reported_total": 1,
                "reported_pages": 1,
                "pages_captured": 1,
                "captured_rows": 1,
                "unique_message_ids": 1,
                "gap_indices": [],
                "complete": True,
                "messages": [base],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            issues: dict[str, list[dict[str, object]]] = {}
            validate_corpus.validate_one_segment(
                path,
                guild_id=validate_corpus.DEFAULT_GUILD_ID,
                window_start=dt.date(2026, 1, 1),
                window_end=dt.date(2026, 7, 20),
                cutoff_utc=dt.datetime(2026, 7, 21, 5, 1, tzinfo=dt.timezone.utc),
                issues=issues,
            )
            self.assertNotIn("timestamp_scope_not_exact", issues)

            for mutation in fail_closed_mutations:
                payload["messages"] = [{**base, **mutation}]
                path.write_text(json.dumps(payload), encoding="utf-8")
                issues = {}
                validate_corpus.validate_one_segment(
                    path,
                    guild_id=validate_corpus.DEFAULT_GUILD_ID,
                    window_start=dt.date(2026, 1, 1),
                    window_end=dt.date(2026, 7, 20),
                    cutoff_utc=dt.datetime(
                        2026, 7, 21, 5, 1, tzinfo=dt.timezone.utc
                    ),
                    issues=issues,
                )
                self.assertEqual(len(issues["timestamp_scope_not_exact"]), 1)

    def test_documented_reply_unavailability_never_creates_a_target(self) -> None:
        base = {
            "reply_context_present": True,
            "reply_to_message_id": None,
            "reply_to_message_id_source": None,
            "reply_to_channel_id": None,
            "reply_to_message_id_candidates": [],
            "reply_target_content_id": None,
            "reply_target_aria_labelledby": None,
            "reply_target_data_list_item_id": None,
            "reply_to_permalink": None,
            "reply_target_scope_exact": False,
            "reply_to_message_id_conflict": False,
            "reply_to_channel_id_conflict": False,
            "reply_context_non_reply_exact": False,
            "reply_context_non_reply_type": None,
        }
        self.assertEqual(
            validate_corpus.documented_reply_target_unavailability(
                {
                    **base,
                    "reply_context": "Message could not be loaded",
                    "reply_target_resolution_status": "discord_message_not_loaded",
                    "reply_target_unavailability_documented": True,
                }
            ),
            "discord_message_not_loaded",
        )
        self.assertEqual(
            validate_corpus.documented_reply_target_unavailability(
                {
                    **base,
                    "reply_context": "@vale\nClick to see attachment",
                    "reply_target_resolution_status": (
                        "discord_attachment_preview_without_exact_target_id"
                    ),
                    "reply_target_unavailability_documented": True,
                }
            ),
            "discord_attachment_preview_without_exact_target_id",
        )
        self.assertEqual(
            validate_corpus.documented_reply_target_unavailability(
                {
                    **base,
                    "reply_context": "@! nq john\nClick to see sticker",
                    "reply_target_resolution_status": (
                        "discord_sticker_preview_without_exact_target_id"
                    ),
                    "reply_target_unavailability_documented": True,
                }
            ),
            "discord_sticker_preview_without_exact_target_id",
        )
        self.assertEqual(
            validate_corpus.documented_reply_target_unavailability(
                {
                    **base,
                    "reply_context": "Click to see voice message",
                    "reply_target_resolution_status": (
                        "discord_voice_message_preview_without_exact_target_id"
                    ),
                    "reply_target_unavailability_documented": True,
                }
            ),
            "discord_voice_message_preview_without_exact_target_id",
        )
        self.assertIsNone(
            validate_corpus.documented_reply_target_unavailability(
                {
                    **base,
                    "reply_context": "ordinary unresolved preview",
                    "reply_target_resolution_status": (
                        "unresolved_without_exact_target_id"
                    ),
                    "reply_target_unavailability_documented": False,
                }
            )
        )
        self.assertIsNone(
            validate_corpus.documented_reply_target_unavailability(
                {
                    **base,
                    "reply_context": "Click to see attachment",
                    "reply_target_aria_labelledby": "ambiguous-reference",
                    "reply_target_resolution_status": (
                        "discord_attachment_preview_without_exact_target_id"
                    ),
                    "reply_target_unavailability_documented": True,
                }
            )
        )
        for mismatch in (
            {
                "reply_context": "Click to see voice message",
                "reply_target_resolution_status": (
                    "discord_attachment_preview_without_exact_target_id"
                ),
                "reply_target_unavailability_documented": True,
            },
            {
                "reply_context": "Click to see voice message",
                "reply_target_resolution_status": (
                    "discord_voice_message_preview_without_exact_target_id"
                ),
                "reply_target_unavailability_documented": False,
            },
            {
                "reply_context": "@target\nClick to see voice message",
                "reply_target_resolution_status": (
                    "discord_voice_message_preview_without_exact_target_id"
                ),
                "reply_target_unavailability_documented": True,
            },
        ):
            with self.subTest(mismatch=mismatch):
                self.assertIsNone(
                    validate_corpus.documented_reply_target_unavailability(
                        {**base, **mismatch}
                    )
                )
        dyno = {
            **base,
            "author_id": "155149108183695360",
            "content_scope_exact": True,
            "content_text": "",
            "reply_context": "boy\n used \nmute",
            "reply_target_resolution_status": (
                "discord_dyno_command_context_without_reply_target"
            ),
            "reply_target_unavailability_documented": True,
            "reply_context_non_reply_exact": True,
            "reply_context_non_reply_type": "discord_dyno_command_invocation",
        }
        self.assertEqual(
            validate_corpus.documented_reply_target_unavailability(dyno),
            "discord_dyno_command_context_without_reply_target",
        )
        self.assertIsNone(
            validate_corpus.documented_reply_target_unavailability(
                {**dyno, "content_text": "mute"}
            )
        )
        executed_command = {
            **base,
            "message_id": "1523613360099295304",
            "article_id": "search-result-1523613360099295304",
            "article_aria_labelledby": (
                "message-username-1523613360099295304 uid_3 "
                "message-content-1523613360099295304 "
                "message-accessories-1523613360099295304 uid_4 "
                "message-timestamp-1523613360099295304"
            ),
            "author": "Wordle",
            "author_id": "1211781489931452447",
            "author_id_source": "owner_scoped_avatar_cdn_path",
            "author_id_conflict": False,
            "content_scope_exact": True,
            "content_text": "LukeLarps was playing",
            "reply_context": "LukeLarps\n used \nPlay",
            "reply_to_content": "LukeLarps\n used \nPlay",
            "reply_context_scope_exact": False,
            "reply_context_dom_class": (
                "repliedMessage_c19a55 messageSpine_c19a55 "
                "executedCommand_c19a55"
            ),
            "reply_context_dom_tag": "DIV",
            "reply_context_aria_hidden": True,
            "reply_context_article_binding_exact": True,
            "reply_context_owner_message_id": "1523613360099295304",
            "reply_context_executed_command_exact": True,
            "author_verified_app_exact": True,
            "reply_target_owner_scoped": False,
            "reply_target_content_text": "",
            "reply_target_id_candidates": [],
            "reply_target_resolution_status": (
                "discord_executed_command_context_without_reply_target"
            ),
            "reply_target_unavailability_documented": True,
            "reply_context_non_reply_exact": True,
            "reply_context_non_reply_type": (
                "discord_application_command_invocation"
            ),
        }
        self.assertEqual(
            validate_corpus.documented_reply_target_unavailability(
                executed_command
            ),
            "discord_executed_command_context_without_reply_target",
        )
        second = {
            **executed_command,
            "message_id": "1523977453436010537",
            "article_id": "search-result-1523977453436010537",
            "article_aria_labelledby": (
                "message-username-1523977453436010537 uid_3 "
                "message-content-1523977453436010537 "
                "message-accessories-1523977453436010537 uid_4 "
                "message-timestamp-1523977453436010537"
            ),
            "reply_context": "TenshiKira\n used \nPlay",
            "reply_to_content": "TenshiKira\n used \nPlay",
            "reply_context_owner_message_id": "1523977453436010537",
        }
        self.assertEqual(
            validate_corpus.documented_reply_target_unavailability(second),
            "discord_executed_command_context_without_reply_target",
        )
        for mutation in (
            {"reply_context_executed_command_exact": False},
            {"reply_context_aria_hidden": False},
            {"author_verified_app_exact": False},
            {"reply_context_dom_class": "repliedMessage_c19a55"},
            {"reply_context_dom_class": "executedCommand_lookalike"},
            {"reply_context_article_binding_exact": False},
            {"reply_context_owner_message_id": "1523613360099295305"},
            {"reply_context_dom_tag": "SPAN"},
            {"article_id": "search-result-1523613360099295305"},
            {"author": "Wordle lookalike"},
            {"author_id": "1211781489931452448"},
            {"reply_target_aria_describedby": "ambiguous-reference"},
            {"reply_target_id_candidates": [{}]},
            {
                "reply_context": "LukeLarps\nused\nOther",
                "reply_to_content": "LukeLarps\nused\nOther",
            },
        ):
            self.assertIsNone(
                validate_corpus.documented_reply_target_unavailability(
                    {**executed_command, **mutation}
                )
            )

    def test_reply_qa_accepts_only_owner_scoped_alternate_exact_sources(self) -> None:
        channel_id = "1329615478716502097"
        target_id = "1456316273788063000"
        rows = []
        for owner_id, source, raw_value in (
            (
                "1456316273788063999",
                "owned_reply_descendant_aria_reference",
                f"message-username-{target_id} message-content-{target_id}",
            ),
            (
                "1456316273788064999",
                "owned_reply_descendant_data_list_item_id",
                f"chat-messages___{target_id}",
            ),
        ):
            rows.append(
                {
                    "message": {
                        "message_id": owner_id,
                        "reply_context_present": True,
                        "reply_context": "@target\nquoted text",
                        "reply_to_message_id": target_id,
                        "reply_to_message_id_source": source,
                        "reply_to_message_id_candidates": [
                            {
                                "message_id": target_id,
                                "channel_id": None,
                                "source": source,
                                "raw_value": raw_value,
                                "owner_scoped": True,
                            }
                        ],
                        "reply_to_channel_id": channel_id,
                        "reply_to_permalink": (
                            "https://discord.com/channels/"
                            f"{validate_corpus.DEFAULT_GUILD_ID}/{channel_id}/{target_id}"
                        ),
                        "reply_target_scope_exact": True,
                        "reply_to_message_id_conflict": False,
                        "reply_to_channel_id_conflict": False,
                        "reply_target_resolution_status": "exact_target_id",
                        "reply_target_unavailability_documented": False,
                        "reply_target_state": "outside_window",
                    }
                }
            )
        recorder = validate_corpus.CheckRecorder()
        summary = validate_corpus.validate_replies(recorder, rows)
        checks = {row["name"]: row for row in recorder.checks}
        self.assertTrue(checks["reply_targets_have_owned_exact_scope"]["passed"])
        self.assertTrue(
            checks["reply_resolution_status_boolean_consistent"]["passed"]
        )
        self.assertEqual(summary["reply_target_scope_failures"], 0)

        for mutation in (
            {"candidate_owner_scoped": False},
            {"reply_target_resolution_status": "not_applicable"},
            {"reply_target_unavailability_documented": True},
        ):
            bad = json.loads(json.dumps(rows[0]))
            message = bad["message"]
            if "candidate_owner_scoped" in mutation:
                message["reply_to_message_id_candidates"][0]["owner_scoped"] = (
                    mutation["candidate_owner_scoped"]
                )
            else:
                message.update(mutation)
            recorder = validate_corpus.CheckRecorder()
            validate_corpus.validate_replies(recorder, [bad])
            checks = {row["name"]: row for row in recorder.checks}
            self.assertFalse(
                checks["reply_targets_have_owned_exact_scope"]["passed"], mutation
            )
            if "candidate_owner_scoped" not in mutation:
                self.assertFalse(
                    checks["reply_resolution_status_boolean_consistent"][
                        "passed"
                    ],
                    mutation,
                )

    def test_reply_qa_rejects_documented_status_boolean_mismatches(self) -> None:
        base = {
            "message_id": "1459199677718200543",
            "reply_context_present": True,
            "reply_context": "Click to see voice message",
            "reply_to_message_id": None,
            "reply_to_message_id_source": None,
            "reply_to_channel_id": None,
            "reply_to_permalink": None,
            "reply_to_message_id_candidates": [],
            "reply_target_content_id": None,
            "reply_target_aria_labelledby": None,
            "reply_target_data_list_item_id": None,
            "reply_target_scope_exact": False,
            "reply_to_message_id_conflict": False,
            "reply_to_channel_id_conflict": False,
            "reply_context_non_reply_exact": False,
            "reply_context_non_reply_type": None,
        }
        valid = {
            **base,
            "reply_target_resolution_status": (
                "discord_voice_message_preview_without_exact_target_id"
            ),
            "reply_target_unavailability_documented": True,
        }
        observed_voice_ids = (
            "1459199677718200543",
            "1459199648798609624",
        )
        recorder = validate_corpus.CheckRecorder()
        validate_corpus.validate_replies(
            recorder,
            [
                {"message": {**valid, "message_id": message_id}}
                for message_id in observed_voice_ids
            ],
        )
        checks = {row["name"]: row for row in recorder.checks}
        self.assertTrue(
            checks["reply_resolution_status_boolean_consistent"]["passed"]
        )
        self.assertTrue(checks["reply_target_unavailability_documented"]["passed"])

        for mutation in (
            {"reply_target_unavailability_documented": False},
            {"reply_target_resolution_status": "discord_message_not_loaded"},
            {"reply_context": "@target\nClick to see voice message"},
        ):
            recorder = validate_corpus.CheckRecorder()
            validate_corpus.validate_replies(
                recorder, [{"message": {**valid, **mutation}}]
            )
            checks = {row["name"]: row for row in recorder.checks}
            self.assertFalse(
                checks["reply_resolution_status_boolean_consistent"]["passed"],
                mutation,
            )
            self.assertFalse(
                checks["reply_target_unavailability_documented"]["passed"],
                mutation,
            )

    def test_non_owned_attachment_is_not_timed_or_required_in_archive(self) -> None:
        message_id = snowflake_for(dt.datetime(2026, 1, 14, 12, tzinfo=dt.timezone.utc))
        old_attachment_id = snowflake_for(dt.datetime(2022, 4, 1, 12, tzinfo=dt.timezone.utc))
        recorder = validate_corpus.CheckRecorder()
        summary, owned_ids = validate_corpus.validate_attachments(
            recorder,
            [
                {
                    "message": {
                        "message_id": message_id,
                        "attachments": [
                            {
                                "attachment_id": old_attachment_id,
                                "url": (
                                    "https://media.discordapp.net/attachments/961384152656142397/"
                                    f"{old_attachment_id}/nice.gif"
                                ),
                                "relation_type": "embedded_external",
                                "ownership_status": "non_owned_exact",
                                "ownership_evidence": {
                                    "schema_version": "1.0.0",
                                    "exact": True,
                                    "owner_message_id": message_id,
                                    "owner_channel_id": "1329615478716502097",
                                    "source_channel_id": "961384152656142397",
                                    "basis": "discord_cdn_source_channel_differs_from_exact_message_container",
                                },
                            }
                        ],
                    }
                }
            ],
        )
        checks = {row["name"]: row for row in recorder.checks}
        self.assertTrue(checks["attachment_ownership_timing"]["passed"])
        self.assertTrue(checks["attachment_capture_status_present"]["passed"])
        self.assertTrue(checks["attachment_ownership_evidence_exact"]["passed"])
        self.assertEqual(summary["non_owned_attachment_occurrences"], 1)
        self.assertEqual(summary["unique_owned_attachments"], 0)
        self.assertEqual(owned_ids, set())

    def test_preservation_manifest_detects_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "artifact.json"
            artifact.write_text("{}\n", encoding="utf-8")
            manifest = preservation_hashes.build_manifest(root, [artifact])
            manifest_path = root / "baseline.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(preservation_hashes.verify_manifest(manifest_path)["status"], "passed")
            artifact.write_text('{"changed":true}\n', encoding="utf-8")
            result = preservation_hashes.verify_manifest(manifest_path)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(len(result["changed"]), 1)

    def test_v2_sqlite_fts_and_exact_excerpt_validation(self) -> None:
        instant = dt.datetime(2026, 1, 1, 12, tzinfo=dt.timezone.utc)
        message_id = snowflake_for(instant)
        channel_id = "1283941772577472643"
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "v2.sqlite"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE messages(
                      message_id TEXT PRIMARY KEY, created_at_utc TEXT,
                      content_text TEXT, visible_text TEXT, reply_to_content TEXT,
                      reply_to_message_id TEXT, reply_target_state TEXT
                    );
                    CREATE VIRTUAL TABLE messages_fts USING fts5(
                      message_id UNINDEXED, content_text
                    );
                    CREATE TABLE attachment_extractions(extraction_id TEXT PRIMARY KEY);
                    CREATE VIRTUAL TABLE attachment_extractions_fts USING fts5(
                      extraction_id UNINDEXED, extracted_text
                    );
                    CREATE TABLE claims(claim_id TEXT PRIMARY KEY);
                    CREATE TABLE claim_evidence(claim_id TEXT, evidence_id TEXT);
                    CREATE VIRTUAL TABLE claims_fts USING fts5(
                      claim_id UNINDEXED, claim_text
                    );
                    CREATE TABLE evidence_items(
                      evidence_id TEXT PRIMARY KEY, message_id TEXT,
                      attachment_id TEXT, exact_excerpt TEXT
                    );
                    CREATE TABLE attachments(
                      attachment_id TEXT PRIMARY KEY, capture_status TEXT
                    );
                    CREATE TABLE channel_inventory(
                      channel_id TEXT PRIMARY KEY, kind TEXT, is_accessible INTEGER
                    );
                    CREATE TABLE collection_units(
                      unit_id TEXT PRIMARY KEY, channel_id TEXT, status TEXT,
                      window_start_utc TEXT, window_end_utc TEXT
                    );
                    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
                    """
                )
                connection.execute(
                    "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                    (
                        message_id,
                        "2026-01-01T12:00:00Z",
                        "Exact Discord excerpt.",
                        "Exact Discord excerpt.",
                        None,
                        None,
                        "not_applicable",
                    ),
                )
                connection.execute(
                    "INSERT INTO messages_fts VALUES(?,?)",
                    (message_id, "Exact Discord excerpt."),
                )
                connection.execute(
                    "INSERT INTO evidence_items VALUES('e1',?,NULL,'Exact Discord excerpt.')",
                    (message_id,),
                )
                # Attachment-backed evidence legitimately has no message_id and
                # must not be classified as an orphan message link.
                connection.execute(
                    "INSERT INTO evidence_items VALUES('e2',NULL,'a1','')"
                )
                connection.execute("INSERT INTO attachments VALUES('a1','metadata_only')")
                connection.execute(
                    "INSERT INTO channel_inventory VALUES(?,'text',1)", (channel_id,)
                )
                connection.execute(
                    "INSERT INTO collection_units VALUES('u1',?,'complete',"
                    "'2026-01-01T06:00:00Z','2026-01-02T06:00:00Z')",
                    (channel_id,),
                )
                connection.executemany(
                    "INSERT INTO meta VALUES(?,?)",
                    (("source_scope", "discord_only"), ("outside_sources_used", "0")),
                )
                connection.commit()
            connection.close()

            recorder = validate_corpus.CheckRecorder()
            validate_corpus.validate_sqlite(
                recorder,
                database,
                {message_id},
                {"a1"},
                {channel_id},
                dt.date(2026, 1, 1),
                dt.date(2026, 1, 1),
            )
            checks = {row["name"]: row for row in recorder.checks}
            self.assertTrue(checks["fts_all_message_parity"]["passed"])
            self.assertTrue(checks["auxiliary_fts_source_parity"]["passed"])
            self.assertTrue(checks["evidence_message_links_resolve"]["passed"])
            self.assertTrue(checks["evidence_excerpts_trace_to_source"]["passed"])
            self.assertTrue(checks["sqlite_whole_server_coverage_gate"]["passed"])

    def test_sqlite_inventory_allows_provenance_backed_observed_forum_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database, parent_id, _thread_id, message_id = self.build_inventory_extension_database(
                Path(temporary)
            )
            recorder = validate_corpus.CheckRecorder()
            validate_corpus.validate_sqlite(
                recorder,
                database,
                {message_id},
                set(),
                {parent_id},
                dt.date(2026, 1, 1),
                dt.date(2026, 1, 1),
            )
            check = next(
                row
                for row in recorder.checks
                if row["name"] == "sqlite_inventory_source_parity"
            )
            self.assertTrue(check["passed"])
            self.assertEqual(check["observed"]["missing_frozen_external"], 0)
            self.assertEqual(
                check["observed"]["provenance_backed_observed_forum_threads"], 1
            )
            self.assertEqual(check["observed"]["unexplained_extra_containers"], 0)

    def test_sqlite_inventory_rejects_unexplained_extra_container(self) -> None:
        unexplained_id = "1480000000000000899"
        with tempfile.TemporaryDirectory() as temporary:
            database, parent_id, _thread_id, message_id = self.build_inventory_extension_database(
                Path(temporary)
            )
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    INSERT INTO channel_inventory(
                      channel_id,guild_id,parent_channel_id,name,kind,exact_id_known,
                      is_accessible,inventory_basis,source_json
                    ) VALUES(?,?,?,?,?,1,1,'synthetic_unexplained','{}')
                    """,
                    (
                        unexplained_id,
                        validate_corpus.DEFAULT_GUILD_ID,
                        None,
                        "unexplained",
                        "text channel",
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            recorder = validate_corpus.CheckRecorder()
            validate_corpus.validate_sqlite(
                recorder,
                database,
                {message_id},
                set(),
                {parent_id},
                dt.date(2026, 1, 1),
                dt.date(2026, 1, 1),
            )
            check = next(
                row
                for row in recorder.checks
                if row["name"] == "sqlite_inventory_source_parity"
            )
            self.assertFalse(check["passed"])
            self.assertEqual(check["observed"]["unexplained_extra_containers"], 1)
            example = next(
                row for row in check["examples"] if row.get("container_id") == unexplained_id
            )
            self.assertIn("kind_is_not_forum_thread", example["reasons"])
            self.assertIn("parent_is_not_in_frozen_external_inventory", example["reasons"])
            self.assertIn(
                "no_trusted_nonmigration_channel_segment_occurrence", example["reasons"]
            )

    def test_final_collection_drift_audit_is_fail_closed(self) -> None:
        payload = {
            "audit_type": "discord_collection_total_drift",
            "generated_at_utc": "2026-07-21T05:01:00Z",
            "mode": "final",
            "audit_window": {
                "start": "2026-01-01",
                "end": "2026-07-20",
                "timezone": "America/Chicago",
            },
            "evidence_boundary": {
                "source": "Discord collector artifacts and local provenance notes only",
                "outside_sources_permitted": False,
                "links_or_attachments_fetched": False,
            },
            "overall_status": "PASS",
            "release_gate_passed": True,
            "summary": {
                "structural_failure_count": 0,
                "unresolved_count": 0,
                "effective_final_failure_count": 0,
                "orphan_quarantined_partial_count": 0,
            },
            "failures": [],
            "unresolved": [],
            "orphan_quarantined_partials": [],
            "exit_code_contract": {"PASS": 0, "FAIL": 1, "PENDING": 2},
        }
        result = validate_corpus.validate_collection_drift_audit(
            payload,
            path=Path("working/collection_drift_final.json"),
            sha256="A" * 64,
            window_start=dt.date(2026, 1, 1),
            window_end=dt.date(2026, 7, 20),
            required_end_exclusive_utc=dt.datetime(
                2026, 7, 21, 5, tzinfo=dt.timezone.utc
            ),
        )
        self.assertTrue(result["passed"])

        payload["overall_status"] = "FAIL"
        payload["summary"]["unresolved_count"] = 1
        payload["unresolved"] = [{"note": "unresolved drift"}]
        result = validate_corpus.validate_collection_drift_audit(
            payload,
            path=Path("working/collection_drift_final.json"),
            sha256="A" * 64,
            window_start=dt.date(2026, 1, 1),
            window_end=dt.date(2026, 7, 20),
            required_end_exclusive_utc=dt.datetime(
                2026, 7, 21, 5, tzinfo=dt.timezone.utc
            ),
        )
        self.assertFalse(result["passed"])
        self.assertIn("overall_status_not_pass", result["errors"])
        self.assertIn("summary_unresolved_count_not_zero", result["errors"])


if __name__ == "__main__":
    unittest.main()
