from __future__ import annotations

import datetime as dt
import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import build_corpus as corpus_builder
import discord_attachment_archiver as attachment_archiver


GUILD_ID = corpus_builder.DEFAULT_GUILD_ID
CHANNEL_ID = "1273692573898113076"
FORUM_ID = "1283941772577472643"
THREAD_ID = "1456316273788063925"


class CorpusBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.segments = self.root / "raw" / "channel_segments"
        self.segments.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_json(self, path: Path, value: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def inventory(self, *, complete: bool = True) -> Path:
        return self.write_json(
            self.root / "channel_inventory.json",
            {
                "guild_id": GUILD_ID,
                "inventory_complete": complete,
                "captured_at_utc": "2026-07-21T05:05:00Z",
                "containers": [
                    {
                        "channel_id": CHANNEL_ID,
                        "name": "questions",
                        "kind": "text",
                        "message_bearing": True,
                        "accessible": True,
                        "searchable": True,
                    }
                ],
            },
        )

    def forum_inventory(self, *, overall_complete: bool = False) -> Path:
        return self.write_json(
            self.root / "forum_inventory.json",
            {
                "guild_id": GUILD_ID,
                "inventory_complete": overall_complete,
                "status": "complete" if overall_complete else "partial",
                "captured_at_utc": "2026-07-21T05:05:00Z",
                "scope_definition": "Authenticated-account visible/searchable scope.",
                "accessible_scope": {
                    "top_level_containers": {
                        "declared_complete": True,
                        "expected_count": 1,
                        "evidence": {"method": "navigation snapshot"},
                    },
                    "forum_threads": {
                        "declared_complete": False,
                        "status": "partial_observed_ids_only",
                        "completion_evidence": None,
                    },
                },
                "containers": [
                    {
                        "channel_id": FORUM_ID,
                        "name": "premium-journals",
                        "kind": "forum channel",
                        "message_bearing": True,
                        "accessible": True,
                        "searchable": True,
                        "count_status": "ok",
                    }
                ],
            },
        )

    def write_segment(
        self,
        name: str,
        *,
        start: str,
        end: str,
        messages: list[dict[str, object]],
        complete: bool = True,
        reported_total: int | None = None,
        channel_id: str = CHANNEL_ID,
        channel_name: str = "questions",
        channel_kind: str = "text channel",
        collector_version: str = "test",
        completion_evidence: dict[str, object] | None = None,
        include_completion_evidence: bool = True,
    ) -> Path:
        total = len(messages) if reported_total is None else reported_total
        pages = 0 if total == 0 else (total + 24) // 25
        query = f"in:{channel_name} after:{start} before:{end}"
        if complete and include_completion_evidence and completion_evidence is None:
            if total == 0:
                observations = [
                    {
                        "sequence": sequence,
                        "observed_at_utc": f"2026-07-21T05:05:0{sequence}Z",
                        "state": "empty_candidate",
                        "visible_result_count": 0,
                        "panel_text": "No Results",
                    }
                    for sequence in (1, 2, 3)
                ]
                completion_evidence = {
                    "schema_version": "1.0.0",
                    "query": query,
                    "reported_total": 0,
                    "reported_pages": 0,
                    "terminal_state": "stable_empty",
                    "search_submission": {
                        "mode": "fresh",
                        "query": query,
                        "submission_count": 1,
                        "submitted_at_utc": "2026-07-21T05:05:00Z",
                    },
                    "stable_empty": {
                        "required_observations": 3,
                        "observations": observations,
                    },
                    "stable_bottom": None,
                }
            else:
                first_index = (pages - 1) * 25 + 1
                observations = [
                    {
                        "sequence": sequence,
                        "observed_at_utc": f"2026-07-21T05:05:0{sequence}Z",
                        "query": query,
                        "visible_result_count": total - first_index + 1,
                        "first_result_index": first_index,
                        "last_result_index": total,
                        "current_page": pages,
                        "result_set_size": total,
                        "has_enabled_next": False,
                    }
                    for sequence in (1, 2)
                ]
                completion_evidence = {
                    "schema_version": "1.0.0",
                    "query": query,
                    "reported_total": total,
                    "reported_pages": pages,
                    "terminal_state": "stable_bottom",
                    "search_submission": {
                        "mode": "fresh",
                        "query": query,
                        "submission_count": 1,
                        "submitted_at_utc": "2026-07-21T05:05:00Z",
                    },
                    "stable_empty": None,
                    "stable_bottom": {
                        "required_observations": 2,
                        "observations": observations,
                    },
                }
        return self.write_json(
            self.segments / name,
            {
                "collector_version": collector_version,
                "guild_id": GUILD_ID,
                "collection_started_at_utc": "2026-07-21T05:04:00Z",
                "captured_at_utc": "2026-07-21T05:05:00Z",
                "requested_container": {
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "channel_kind": channel_kind,
                    "channel_id_source": "navigation_inventory",
                },
                "segment": {
                    "start": start,
                    "end": end,
                    "query": query,
                    "timezone": "America/Chicago",
                },
                "reported_total": total,
                "reported_pages": pages,
                "pages_captured": pages,
                "captured_rows": len(messages),
                "unique_message_ids": len({row.get("message_id") for row in messages}),
                "gap_indices": [],
                **(
                    {"completion_evidence": completion_evidence}
                    if include_completion_evidence and completion_evidence is not None
                    else {}
                ),
                "complete": complete,
                "messages": messages,
            },
        )

    def message(
        self,
        local_value: dt.datetime,
        *,
        content: str = "rejection block example",
        captured_utc: str | None = None,
        result_index: int = 1,
        increment: int = 1,
    ) -> dict[str, object]:
        local = local_value.replace(tzinfo=corpus_builder.resolve_timezone("America/Chicago"))
        utc = local.astimezone(dt.timezone.utc)
        message_id = corpus_builder.snowflake_id_for_datetime(utc, increment)
        return {
            "message_id": message_id,
            "guild_id": GUILD_ID,
            "channel_id": CHANNEL_ID,
            "author": "tester",
            "timestamp_utc": captured_utc or corpus_builder.iso_z(utc),
            "snowflake_timestamp_utc": corpus_builder.iso_z(utc),
            "timestamp_discrepancy_ms": 0,
            "article_id": f"search-result-{message_id}",
            "article_aria_labelledby": (
                f"message-content-{message_id} message-timestamp-{message_id}"
            ),
            "content_present": True,
            "content_scope_exact": True,
            "timestamp_scope_exact": True,
            "content_text": content,
            "result_index": result_index,
            "page_number": 1,
            "attachments": [],
        }

    def build(self, **overrides: object) -> tuple[dict, dict]:
        args = {
            "segment_dirs": [self.segments],
            "provenance_root": self.root,
            "data_cutoff_utc": dt.datetime(2026, 7, 21, 5, 5, tzinfo=dt.timezone.utc),
        }
        args.update(overrides)
        return corpus_builder.build_corpus(**args)

    def test_default_scope_uses_central_local_boundaries_and_dst(self) -> None:
        scope = corpus_builder.make_scope(
            GUILD_ID,
            "2026-01-01",
            "2026-07-20",
            "America/Chicago",
        )
        self.assertEqual(scope.local_day_count, 201)
        self.assertEqual(corpus_builder.iso_z(scope.utc_start), "2026-01-01T06:00:00.000Z")
        self.assertEqual(
            corpus_builder.iso_z(scope.utc_end_exclusive), "2026-07-21T05:00:00.000Z"
        )

    def test_numeric_collector_requires_durable_completion_evidence(self) -> None:
        path = self.write_segment(
            "channel_questions_2026-01-06_2026-01-06.json",
            start="2026-01-06",
            end="2026-01-06",
            messages=[],
            collector_version="2.4",
            include_completion_evidence=False,
        )
        payload = corpus_builder.load_json_object(path, "test segment")
        scope = corpus_builder.make_scope(
            GUILD_ID, "2026-01-01", "2026-07-20", "America/Chicago"
        )
        normalized, _ = corpus_builder.validate_segment_payload(path, payload, scope)
        self.assertIn(
            "completion_evidence_missing_recapture_or_sidecar_required",
            normalized["validation_errors"],
        )
        self.assertFalse(normalized["computed_complete"])
        payload["collector_version"] = "legacy"
        self.write_json(path, payload)
        normalized, _ = corpus_builder.validate_segment_payload(path, payload, scope)
        self.assertIn(
            "completion_evidence_missing_recapture_or_sidecar_required",
            normalized["validation_errors"],
        )
        self.assertFalse(normalized["computed_complete"])

    def test_fresh_stable_empty_sidecar_binds_exact_source_hash(self) -> None:
        path = self.write_segment(
            "channel_questions_2026-01-07_2026-01-07.json",
            start="2026-01-07",
            end="2026-01-07",
            messages=[],
            collector_version="2.4",
            include_completion_evidence=False,
        )
        payload = corpus_builder.load_json_object(path, "test segment")
        query = payload["segment"]["query"]
        observations = [
            {
                "sequence": sequence,
                "observed_at_utc": f"2026-07-21T05:10:0{sequence}Z",
                "state": "empty_candidate",
                "visible_result_count": 0,
                "panel_text": "No Results",
            }
            for sequence in (1, 2, 3)
        ]
        evidence = {
            "schema_version": "1.0.0",
            "query": query,
            "reported_total": 0,
            "reported_pages": 0,
            "terminal_state": "stable_empty",
            "search_submission": {
                "mode": "fresh",
                "query": query,
                "submission_count": 1,
                "submitted_at_utc": "2026-07-21T05:10:00Z",
            },
            "search_observations": observations,
            "stable_empty": {"required_observations": 3, "observations": observations},
            "stable_bottom": None,
        }
        sidecar_path = corpus_builder.completion_evidence_sidecar_path(path)
        self.write_json(
            sidecar_path,
            {
                "artifact_type": "discord_segment_completion_evidence_sidecar",
                "schema_version": "1.0.0",
                "created_at_utc": "2026-07-21T05:10:04Z",
                "source_artifact_path": path.name,
                "source_artifact_sha256": corpus_builder.sha256_file(path),
                "guild_id": payload["guild_id"],
                "requested_container": payload["requested_container"],
                "segment": payload["segment"],
                "reported_total": 0,
                "reported_pages": 0,
                "completion_evidence": evidence,
            },
        )
        scope = corpus_builder.make_scope(
            GUILD_ID, "2026-01-01", "2026-07-20", "America/Chicago"
        )
        normalized, _ = corpus_builder.validate_segment_payload(path, payload, scope)
        self.assertTrue(normalized["computed_complete"])
        self.assertEqual(normalized["completion_evidence_source"], "sidecar")
        self.assertTrue(normalized["completion_evidence_valid"])

        sidecar = corpus_builder.load_json_object(sidecar_path, "test sidecar")
        sidecar.pop("requested_container")
        self.write_json(sidecar_path, sidecar)
        normalized, _ = corpus_builder.validate_segment_payload(path, payload, scope)
        self.assertFalse(normalized["computed_complete"])
        self.assertIn(
            "completion_evidence_sidecar_container_mismatch",
            normalized["validation_errors"],
        )

        sidecar["requested_container"] = payload["requested_container"]
        sidecar["source_artifact_path"] = "wrong-source.json"
        self.write_json(sidecar_path, sidecar)
        wrong_path, _ = corpus_builder.validate_segment_payload(path, payload, scope)
        self.assertIn(
            "completion_evidence_sidecar_source_path_mismatch",
            wrong_path["validation_errors"],
        )

        payload["captured_at_utc"] = "2026-07-21T05:11:00Z"
        self.write_json(path, payload)
        tampered, _ = corpus_builder.validate_segment_payload(path, payload, scope)
        self.assertIn(
            "completion_evidence_sidecar_source_hash_mismatch",
            tampered["validation_errors"],
        )

    def test_stable_bottom_requires_explicit_disabled_next(self) -> None:
        query = "in:questions after:2026-01-07 before:2026-01-08"
        observations = [
            {
                "sequence": sequence,
                "observed_at_utc": f"2026-07-21T05:10:0{sequence}Z",
                "query": query,
                "visible_result_count": 5,
                "first_result_index": 26,
                "last_result_index": 30,
                "current_page": 2,
                "result_set_size": 30,
            }
            for sequence in (1, 2)
        ]
        errors = corpus_builder.validate_completion_evidence(
            {
                "schema_version": "1.0.0",
                "query": query,
                "reported_total": 30,
                "reported_pages": 2,
                "terminal_state": "stable_bottom",
                "search_submission": {
                    "mode": "fresh",
                    "query": query,
                    "submission_count": 1,
                    "submitted_at_utc": "2026-07-21T05:10:00Z",
                },
                "stable_empty": None,
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

    def test_exact_forum_thread_id_precedes_generic_parent_channel_id(self) -> None:
        message_container, parent, issues = corpus_builder.resolve_row_container(
            {
                "channel_id": FORUM_ID,
                "collection_channel_id": FORUM_ID,
                "collection_channel_kind": "forum channel",
                "inferred_thread_channel_id": THREAD_ID,
                "thread_channel_id_source": "forum_group_header_data_list_item_id",
                "thread_channel_id_exact": True,
                "group_header_parent_forum_channel_id": FORUM_ID,
            },
            FORUM_ID,
        )
        self.assertEqual(message_container, THREAD_ID)
        self.assertEqual(parent, FORUM_ID)
        self.assertEqual(issues, [])

    def test_exact_forum_navigation_source_precedes_generic_parent_channel_id(self) -> None:
        message_id = "1456772646124650737"
        query = "in:premium-journals after:2026-01-01 before:2026-01-03"
        message_ids = [message_id, "1456772414640881705"]
        fingerprint = json.dumps(
            {
                "query": query,
                "page_number": 1,
                "group_message_ids": sorted(message_ids),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        evidence_key = (
            "forum-group-navigation:"
            + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
        )
        row = {
                "message_id": message_id,
                "channel_id": FORUM_ID,
                "collection_channel_id": FORUM_ID,
                "collection_channel_kind": "forum channel",
                "inferred_thread_channel_id": THREAD_ID,
                "thread_channel_id_source": "forum_group_header_navigation_exact",
                "thread_channel_id_exact": True,
                "exact_permalink_status": "thread_id_from_forum_group_header_navigation",
                "search_query": query,
                "page_number": 1,
                "forum_group_message_ids": message_ids,
                "forum_group_membership_exact": True,
                "forum_group_membership_key": evidence_key,
                "forum_group_navigation_evidence_key": evidence_key,
                "forum_group_navigation_evidence": {
                    "schema_version": "1.0.0",
                    "evidence_type": "forum_group_header_navigation_exact",
                    "evidence_key": evidence_key,
                    "guild_id": GUILD_ID,
                    "parent_forum_channel_id": FORUM_ID,
                    "query": query,
                    "page_number": 1,
                    "group_message_ids": sorted(message_ids),
                    "navigation_trigger": "unique_direct_child_role_button_click",
                    "header_match_count": 1,
                    "header_button_match_count": 1,
                    "destination_url": (
                        f"https://discord.com/channels/{GUILD_ID}/{THREAD_ID}"
                    ),
                    "destination_guild_id": GUILD_ID,
                    "thread_channel_id": THREAD_ID,
                    "destination_verified": True,
                    "return_state_verified": True,
                    "observed_at_utc": "2026-07-21T16:30:00.000Z",
                    "authenticated": True,
                    "source_scope": "discord_only",
                    "outside_sources_used": False,
                },
            }
        message_container, parent, issues = corpus_builder.resolve_row_container(row, FORUM_ID)
        self.assertEqual(message_container, THREAD_ID)
        self.assertEqual(parent, FORUM_ID)
        self.assertEqual(issues, [])

        tampered = json.loads(json.dumps(row))
        tampered["forum_group_navigation_evidence"]["destination_url"] = (
            f"https://discord.com/channels/999999999999999999/{THREAD_ID}"
        )
        message_container, parent, issues = corpus_builder.resolve_row_container(
            tampered, FORUM_ID
        )
        self.assertEqual(message_container, FORUM_ID)
        self.assertIsNone(parent)
        self.assertIn(
            "exact_forum_thread_id_unresolved_inherited_from_collection", issues
        )

    def test_nonforum_collection_id_precedes_unverified_attachment_channel(self) -> None:
        stage_id = "1329615478716502097"
        attachment_channel_id = "961384152656142397"
        message_container, parent, issues = corpus_builder.resolve_row_container(
            {
                "collection_channel_id": stage_id,
                "collection_channel_kind": "stage channel",
                "channel_id": attachment_channel_id,
                "inferred_thread_channel_id": attachment_channel_id,
                "thread_channel_id_source": "attachment_cdn_path_unverified",
                "thread_channel_id_exact": False,
                "exact_permalink": (
                    f"https://discord.com/channels/{GUILD_ID}/"
                    f"{attachment_channel_id}/1461017285824352268"
                ),
                "exact_permalink_status": "thread_id_from_unverified_attachment",
            },
            stage_id,
        )
        self.assertEqual(message_container, stage_id)
        self.assertIsNone(parent)
        self.assertEqual(issues, [])

    def test_forum_attachment_channel_cannot_become_exact_thread(self) -> None:
        attachment_channel_id = "961384152656142397"
        message_container, parent, issues = corpus_builder.resolve_row_container(
            {
                "collection_channel_id": FORUM_ID,
                "collection_channel_kind": "forum channel",
                "channel_id": attachment_channel_id,
                "inferred_thread_channel_id": attachment_channel_id,
                "thread_channel_id_source": "attachment_cdn_path_unverified",
                "thread_channel_id_exact": False,
                "exact_permalink": (
                    f"https://discord.com/channels/{GUILD_ID}/"
                    f"{attachment_channel_id}/1461017285824352268"
                ),
                "exact_permalink_status": "thread_id_from_unverified_attachment",
            },
            FORUM_ID,
        )
        self.assertEqual(message_container, FORUM_ID)
        self.assertIsNone(parent)
        self.assertIn(
            "exact_forum_thread_id_unresolved_inherited_from_collection", issues
        )

    def test_complete_release_accepts_verified_empty_full_window(self) -> None:
        self.write_segment(
            "channel_questions_2026-01-01_2026-07-20.json",
            start="2026-01-01",
            end="2026-07-20",
            messages=[],
        )
        corpus, manifest = self.build(
            inventory_path=self.inventory(),
            release_requested=True,
        )
        self.assertEqual(corpus["release"]["status"], "complete")
        self.assertTrue(manifest["release_ready"])
        self.assertEqual(manifest["coverage"]["summary"]["verified_empty_segment_count"], 1)
        self.assertEqual(manifest["coverage"]["containers"][0]["complete_day_count"], 201)
        self.assertEqual(manifest["coverage"]["gaps"], [])
        self.assertTrue(all(row["sha256"] for row in manifest["source_files"]))
        self.assertTrue(
            all(not Path(row["relative_path"]).is_absolute() for row in manifest["source_files"])
        )

    def test_working_build_never_labels_itself_complete(self) -> None:
        self.write_segment(
            "channel_questions_2026-01-01_2026-07-20.json",
            start="2026-01-01",
            end="2026-07-20",
            messages=[],
        )
        corpus, manifest = self.build(inventory_path=self.inventory(), release_requested=False)
        self.assertTrue(manifest["release_ready"])
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(corpus["artifact_type"], corpus_builder.ARTIFACT_TYPE_WORKING)

    def test_owned_attachment_requires_terminal_manifest_for_release(self) -> None:
        row = self.message(dt.datetime(2026, 1, 1, 10, 0))
        attachment_id = corpus_builder.snowflake_id_for_datetime(
            dt.datetime(2026, 1, 1, 16, 0, 1, tzinfo=dt.timezone.utc), 2
        )
        row["attachments"] = [
            {
                "attachment_id": attachment_id,
                "relation_type": "owned",
                "ownership_status": "owned_exact",
                "ownership_evidence": {
                    "schema_version": "1.0.0",
                    "exact": True,
                    "basis": "test_exact_message_accessories",
                    "owner_message_id": row["message_id"],
                    "owner_channel_id": CHANNEL_ID,
                    "source_channel_id": CHANNEL_ID,
                },
                "filename": "chart.png",
                "url": (
                    f"https://cdn.discordapp.com/attachments/{CHANNEL_ID}/"
                    f"{attachment_id}/chart.png"
                ),
            }
        ]
        self.write_segment(
            "channel_questions_2026-01-01_2026-07-20.json",
            start="2026-01-01",
            end="2026-07-20",
            messages=[row],
        )
        working, missing_manifest = self.build(
            inventory_path=self.inventory(), release_requested=False
        )
        gate = {
            item["gate"]: item for item in missing_manifest["release_gates"]
        }["discord_attachment_terminal_coverage"]
        self.assertFalse(gate["passed"])
        self.assertFalse(missing_manifest["release_ready"])

        corpus_path = self.write_json(self.root / "working_corpus.json", working)
        manifest_path = self.root / "attachment_manifest.json"
        archive_root = self.root / "archive"
        attachment_manifest = attachment_archiver.create_or_resume_manifest(
            corpus_path, manifest_path
        )
        entry = attachment_manifest["entries"][0]
        body = b"chart image bytes"
        attachment_archiver.ingest_browser_response(
            attachment_manifest,
            {
                "contract": "discord_attachment_browser_response_v1",
                "request_id": entry["request_id"],
                "message_id": entry["message_id"],
                "attachment_id": entry["attachment_id"],
                "final_url": entry["discord_url"],
                "status": "downloaded",
                "terminal": True,
                "attempted_at_utc": "2026-07-21T05:06:00Z",
                "http_status": 200,
                "mime_type": "image/png",
                "body_base64": base64.b64encode(body).decode("ascii"),
                "byte_size": len(body),
                "sha256": attachment_archiver.sha256_bytes(body),
                "outside_sources_used": 0,
                "credentials_or_browser_storage_inspected": False,
            },
            archive_root,
        )
        staging = self.root / "attachment_extraction_staging"
        staging.mkdir()
        (staging / "ocr.txt").write_text(
            "verified local extraction", encoding="utf-8"
        )
        attachment_archiver.record_extraction(
            attachment_manifest,
            {
                "attachment_id": entry["attachment_id"],
                "status": "complete",
                "method": "local_ocr_v1",
                "created_at_utc": "2026-07-21T05:07:00Z",
                "staged_file": "ocr.txt",
                "filename": "ocr.txt",
            },
            archive_root,
            staging_root=staging,
        )
        attachment_archiver.write_json_atomic(manifest_path, attachment_manifest)

        corpus, complete_manifest = self.build(
            inventory_path=self.inventory(),
            attachment_manifest_path=manifest_path,
            attachment_archive_root=archive_root,
            release_requested=True,
        )
        self.assertTrue(complete_manifest["release_ready"])
        self.assertEqual(complete_manifest["status"], "complete")
        archive_summary = corpus["attachment_archive"]
        self.assertTrue(archive_summary["release_gate"]["passed"])
        stored = corpus["messages"][0]["attachments"][0]
        self.assertEqual(stored["capture_status"], "downloaded")
        self.assertEqual(stored["content_sha256"], attachment_archiver.sha256_bytes(body))
        self.assertEqual(stored["byte_size"], len(body))
        self.assertTrue(stored["local_package_path"].startswith("attachments/"))
        self.assertFalse(stored["chart_claim_eligible"])
        self.assertTrue(stored["extraction_artifacts"][0]["local_artifact_verified"])

    def test_non_owned_embed_cannot_retain_unverified_local_artifacts(self) -> None:
        message_id = corpus_builder.snowflake_id_for_datetime(
            dt.datetime(2026, 2, 5, 16, tzinfo=dt.timezone.utc), 574
        )
        messages = [
            {
                "message_id": message_id,
                "attachments": [
                    {
                        "attachment_id": "1364178305632174100",
                        "filename": "schizophrenicistalking.gif",
                        "url": (
                            "https://cdn.discordapp.com/attachments/1278211283656773643/"
                            "1364178305632174100/schizophrenicistalking.gif"
                        ),
                        "thread_channel_id": "1278211283656773643",
                        "relation_type": "embedded_external",
                        "ownership_status": "non_owned_exact",
                        "ownership_evidence": {
                            "schema_version": "1.0.0",
                            "exact": True,
                            "basis": "discord_cdn_source_channel_differs_from_exact_message_container",
                            "owner_message_id": message_id,
                            "owner_channel_id": CHANNEL_ID,
                            "source_channel_id": "1278211283656773643",
                            "dom_relation": "embed_descendant",
                        },
                        "local_package_path": "attachments/outside.gif",
                        "content_sha256": "a" * 64,
                        "capture_status": "downloaded",
                        "capture_terminal": True,
                        "capture_attempt_count": 1,
                        "capture_attempts": [{"status": "downloaded"}],
                        "extraction_status": "complete",
                        "extraction_artifacts": [{"extracted_text": "outside"}],
                    }
                ],
            }
        ]
        summary = corpus_builder.apply_attachment_archive_manifest(
            messages=messages,
            manifest_path=None,
            archive_root=None,
            provenance_root=self.root,
            source_registry={},
            authorized_message_ids={message_id},
        )
        self.assertEqual(summary["expected_owned_attachment_count"], 0)
        stored = messages[0]["attachments"][0]
        self.assertEqual(stored["capture_status"], "metadata_only")
        self.assertFalse(stored["capture_terminal"])
        self.assertEqual(stored["capture_attempt_count"], 0)
        self.assertEqual(stored["capture_attempts"], [])
        self.assertIsNone(stored["local_package_path"])
        self.assertIsNone(stored["content_sha256"])
        self.assertEqual(stored["extraction_status"], "not_attempted")
        self.assertEqual(stored["extraction_artifacts"], [])

    def test_terminal_failed_attachment_blocks_literal_release(self) -> None:
        row = self.message(dt.datetime(2026, 1, 1, 10, 0))
        attachment_id = corpus_builder.snowflake_id_for_datetime(
            dt.datetime(2026, 1, 1, 16, 0, 1, tzinfo=dt.timezone.utc), 3
        )
        row["attachments"] = [
            {
                "attachment_id": attachment_id,
                "relation_type": "owned",
                "ownership_status": "owned_exact",
                "ownership_evidence": {
                    "schema_version": "1.0.0",
                    "exact": True,
                    "basis": "test_exact_message_accessories",
                    "owner_message_id": row["message_id"],
                    "owner_channel_id": CHANNEL_ID,
                    "source_channel_id": CHANNEL_ID,
                },
                "filename": "chart.png",
                "url": (
                    f"https://cdn.discordapp.com/attachments/{CHANNEL_ID}/"
                    f"{attachment_id}/chart.png"
                ),
            }
        ]
        self.write_segment(
            "channel_questions_2026-01-01_2026-07-20.json",
            start="2026-01-01",
            end="2026-07-20",
            messages=[row],
        )
        working, _ = self.build(
            inventory_path=self.inventory(), release_requested=False
        )
        corpus_path = self.write_json(self.root / "failed_working_corpus.json", working)
        manifest_path = self.root / "failed_attachment_manifest.json"
        archive_root = self.root / "failed_archive"
        attachment_manifest = attachment_archiver.create_or_resume_manifest(
            corpus_path, manifest_path
        )
        entry = attachment_manifest["entries"][0]
        for attempt_number in range(1, 4):
            attachment_archiver.ingest_browser_response(
                attachment_manifest,
                {
                    "contract": "discord_attachment_browser_response_v1",
                    "request_id": entry["request_id"],
                    "message_id": entry["message_id"],
                    "attachment_id": entry["attachment_id"],
                    "final_url": entry["discord_url"],
                    "status": "failed",
                    "terminal": attempt_number == 3,
                    "attempted_at_utc": f"2026-07-21T05:0{attempt_number}:00Z",
                    "http_status": 503,
                    "error_code": "discord_request_failed",
                    "error_detail": (
                        f"Discord attachment request attempt {attempt_number} returned HTTP 503"
                    ),
                    "outside_sources_used": 0,
                    "credentials_or_browser_storage_inspected": False,
                },
                archive_root,
            )
        attachment_archiver.write_json_atomic(manifest_path, attachment_manifest)
        corpus, release_manifest = self.build(
            inventory_path=self.inventory(),
            attachment_manifest_path=manifest_path,
            attachment_archive_root=archive_root,
            release_requested=True,
        )
        self.assertFalse(release_manifest["release_ready"])
        self.assertEqual(release_manifest["status"], "partial")
        gate = {
            item["gate"]: item for item in release_manifest["release_gates"]
        }["discord_attachment_terminal_coverage"]
        self.assertTrue(gate["passed"])
        literal_gate = {
            item["gate"]: item for item in release_manifest["release_gates"]
        }["discord_attachment_literal_release_complete"]
        self.assertFalse(literal_gate["passed"])
        self.assertEqual(corpus["attachment_archive"]["status"], "degraded")
        self.assertTrue(
            corpus["attachment_archive"]["release_gate"]["terminal_coverage_complete"]
        )
        self.assertFalse(
            corpus["attachment_archive"]["release_gate"]["literal_release_complete"]
        )

    def test_untrusted_unresolved_attachment_metadata_is_retained_but_not_fetched(self) -> None:
        row = self.message(dt.datetime(2026, 1, 1, 10, 0))
        attachment_id = corpus_builder.snowflake_id_for_datetime(
            dt.datetime(2026, 1, 1, 16, 0, 1, tzinfo=dt.timezone.utc), 9
        )
        attachment = {
            "attachment_id": attachment_id,
            "filename": "legacy-chart.png",
            "url": (
                f"https://cdn.discordapp.com/attachments/{CHANNEL_ID}/"
                f"{attachment_id}/legacy-chart.png"
            ),
        }
        untrusted = {
            **row,
            "eligible_for_accepted_evidence": False,
            "evidence_trust_state": "quarantined_only",
            "attachments": [attachment],
        }
        discovered, excluded = corpus_builder.discover_attachment_candidates_with_trust(
            [untrusted]
        )
        self.assertEqual(discovered, [])
        self.assertEqual(len(excluded), 1)
        self.assertEqual(
            untrusted["attachments"][0]["capture_status"],
            "metadata_only_untrusted_ownership_unresolved",
        )
        self.assertFalse(untrusted["attachments"][0]["archive_required"])

        trusted = {
            **row,
            "eligible_for_accepted_evidence": True,
            "evidence_trust_state": "trusted_canonical_recapture",
            "attachments": [{**attachment}],
        }
        with self.assertRaisesRegex(corpus_builder.CorpusError, "ownership is unresolved"):
            corpus_builder.discover_attachment_candidates_with_trust([trusted])

    def test_policy_gate_ignores_only_targeted_diagnostic_partial_status(self) -> None:
        scope = corpus_builder.make_scope(
            GUILD_ID, "2026-01-01", "2026-07-20", "America/Chicago"
        )
        policy = {
            "enabled": True,
            "plan_valid": True,
            "plan_validation": {"status": "passed"},
            "release_ready": True,
            "diagnostic_partial_targeted_full_capture_count": 1,
            "classified_segments": [
                {
                    "segment_id": "diagnostic-partial",
                    "policy_role": "diagnostic_targeted_full_capture",
                    "computed_complete": False,
                }
            ],
            "hard_gates": [
                {"gate_id": gate_id, "passed": True}
                for gate_id in (
                    "full_capture_segment_coverage",
                    "targeted_query_matrix",
                    "residual_audit",
                )
            ],
        }
        gates = corpus_builder.make_release_gates(
            scope=scope,
            data_cutoff=dt.datetime(2026, 7, 21, 5, 1, tzinfo=dt.timezone.utc),
            inventory={
                "provided": True,
                "validated_complete": True,
                "guild_id": GUILD_ID,
                "validation_errors": [],
                "completeness": {},
            },
            coverage={
                "containers": [],
                "segments": [{"segment_id": "diagnostic-partial", "computed_complete": False}],
                "file_failures": [],
                "gaps": [],
            },
            quarantine={
                "unresolved_valid_message_ids": [],
                "invalid_message_id_occurrence_count": 0,
                "invalid_migration_sidecar_record_count": 0,
                "unmatched_migration_sidecar_record_count": 0,
            },
            source_files=[
                {
                    "source_file_id": "source",
                    "relative_path": "raw/channel_segments/diagnostic.partial.json",
                    "exists": True,
                    "sha256": "A" * 64,
                    "size_bytes": 1,
                }
            ],
            legacy={"provided": False},
            messages=[],
            relevance_policy=policy,
        )
        by_name = {row["gate"]: row for row in gates}
        self.assertTrue(
            by_name["all_policy_required_segment_files_strictly_complete"]["passed"]
        )
        self.assertNotIn("all_channel_segment_files_strictly_complete", by_name)

    def test_partial_inputs_have_explicit_date_gaps(self) -> None:
        self.write_segment(
            "channel_questions_2026-01-01_2026-01-01.json",
            start="2026-01-01",
            end="2026-01-01",
            messages=[],
        )
        _corpus, manifest = self.build(inventory_path=self.inventory())
        row = manifest["coverage"]["containers"][0]
        self.assertEqual(row["complete_day_count"], 1)
        self.assertEqual(row["missing_day_count"], 200)
        self.assertEqual(
            row["missing_date_ranges"],
            [{"start_date": "2026-01-02", "end_date": "2026-07-20", "day_count": 200}],
        )
        failed = {item["gate"] for item in manifest["release_gates"] if not item["passed"]}
        self.assertIn("every_accessible_message_container_has_full_date_coverage", failed)

    def test_partial_capture_rows_are_searchable_but_not_trusted_canonical(self) -> None:
        row = self.message(dt.datetime(2026, 1, 1, 10, 0))
        self.write_segment(
            "channel_questions_2026-01-01_2026-01-01.partial.json",
            start="2026-01-01",
            end="2026-01-01",
            messages=[row],
            complete=False,
            include_completion_evidence=False,
        )
        corpus, _manifest = self.build(
            start_date="2026-01-01",
            end_date_inclusive="2026-01-01",
        )
        self.assertEqual(len(corpus["messages"]), 1)
        self.assertEqual(
            corpus["messages"][0]["evidence_trust_state"],
            "untrusted_noncanonical_only",
        )
        self.assertFalse(corpus["messages"][0]["eligible_for_accepted_evidence"])
        self.assertFalse(corpus["occurrences"][0]["complete_source"])

    def test_snowflake_timestamp_mismatch_is_quarantined(self) -> None:
        row = self.message(
            dt.datetime(2026, 1, 1, 10, 0),
            captured_utc="2026-01-03T00:00:00Z",
        )
        self.write_segment(
            "channel_questions_2026-01-01_2026-01-01.json",
            start="2026-01-01",
            end="2026-01-01",
            messages=[row],
        )
        corpus, manifest = self.build(inventory_path=self.inventory())
        occurrence = corpus["occurrences"][0]
        self.assertTrue(occurrence["quarantined"])
        self.assertIn(
            "captured_timestamp_snowflake_mismatch_gt_1000ms",
            occurrence["quarantine_reasons"],
        )
        message = corpus["messages"][0]
        self.assertEqual(message["timestamp_utc"], occurrence["snowflake_timestamp_utc"])
        self.assertTrue(message["quarantined"])
        self.assertEqual(len(manifest["quarantine"]["unresolved_valid_message_ids"]), 1)

    def test_all_occurrences_and_field_variants_are_retained(self) -> None:
        first = self.message(dt.datetime(2026, 1, 1, 10, 0), content="version one")
        second = dict(first)
        second["content_text"] = "version two"
        self.write_segment(
            "capture_a_2026-01-01_2026-01-01.json",
            start="2026-01-01",
            end="2026-01-01",
            messages=[first],
        )
        self.write_segment(
            "capture_b_2026-01-01_2026-01-01.json",
            start="2026-01-01",
            end="2026-01-01",
            messages=[second],
        )
        corpus, _manifest = self.build(
            start_date="2026-01-01",
            end_date_inclusive="2026-01-01",
        )
        self.assertEqual(len(corpus["occurrences"]), 2)
        self.assertEqual(len(corpus["messages"]), 1)
        variants = corpus["messages"][0]["_field_variants"]["content_text"]
        self.assertEqual({item["value"] for item in variants}, {"version one", "version two"})
        self.assertTrue(all(item["occurrence_ids"] for item in variants))

    def test_migration_quarantine_is_retained_and_ineligible(self) -> None:
        row = self.message(
            dt.datetime(2026, 1, 1, 10, 0),
            content="Legacy rejection block text retained for lookup.",
        )
        row.update(
            {
                "migration_quarantined": True,
                "migration_quarantine_reasons": [
                    "exact_permalink_unavailable",
                    "reply_preview_content_contamination_suspected",
                ],
                "_migration_occurrence": {"occurrence_id": "legacy_occ:test-inline"},
            }
        )
        self.write_segment(
            "migration_2026-01-01_2026-01-01.json",
            start="2026-01-01",
            end="2026-01-01",
            messages=[row],
        )

        corpus, manifest = self.build(
            start_date="2026-01-01",
            end_date_inclusive="2026-01-01",
        )
        occurrence = corpus["occurrences"][0]
        message = corpus["messages"][0]
        self.assertTrue(occurrence["migration_source"])
        self.assertTrue(occurrence["migration_quarantined"])
        self.assertTrue(occurrence["quarantined"])
        self.assertIn(
            "reply_preview_content_contamination_suspected",
            occurrence["quarantine_reasons"],
        )
        self.assertEqual(
            message["content_text"],
            "Legacy rejection block text retained for lookup.",
        )
        self.assertEqual(message["evidence_trust_state"], "quarantined_only")
        self.assertFalse(message["eligible_for_accepted_evidence"])
        self.assertEqual(
            manifest["quarantine"]["messages_ineligible_for_accepted_evidence"], 1
        )
        self.assertEqual(
            manifest["quarantine"]["migration_quarantined_occurrence_count"], 1
        )

    def test_migration_quarantine_sidecar_is_auto_discovered(self) -> None:
        row = self.message(
            dt.datetime(2026, 1, 1, 10, 0),
            content="Sidecar-only migrated rejection block text.",
        )
        row["_migration_occurrence"] = {"occurrence_id": "legacy_occ:test-sidecar"}
        self.write_segment(
            "migration_2026-01-01_2026-01-01.json",
            start="2026-01-01",
            end="2026-01-01",
            messages=[row],
        )
        sidecar = self.segments.parent / corpus_builder.MIGRATION_QUARANTINE_SIDECAR_NAME
        sidecar.write_text(
            json.dumps(
                {
                    "occurrence_id": "legacy_occ:test-sidecar",
                    "message_id": row["message_id"],
                    "reasons": ["exact_thread_identity_unavailable"],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        corpus, manifest = self.build(
            start_date="2026-01-01",
            end_date_inclusive="2026-01-01",
        )
        occurrence = corpus["occurrences"][0]
        self.assertEqual(occurrence["migration_quarantine_sources"], ["sidecar"])
        self.assertIn(
            "exact_thread_identity_unavailable", occurrence["quarantine_reasons"]
        )
        self.assertFalse(corpus["messages"][0]["eligible_for_accepted_evidence"])
        sidecar_summary = manifest["migration_quarantine_sidecars"]
        self.assertEqual(sidecar_summary["matched_occurrence_count"], 1)
        self.assertEqual(sidecar_summary["unmatched_occurrence_count"], 0)
        self.assertEqual(sidecar_summary["invalid_record_count"], 0)

    def test_independent_canonical_recapture_unlocks_only_canonical_message(self) -> None:
        canonical = self.message(
            dt.datetime(2026, 1, 1, 10, 0),
            content="Trusted canonical rejection block text.",
        )
        migrated = dict(canonical)
        migrated.update(
            {
                "content_text": (
                    "Legacy contaminated rejection block text plus unrelated reply preview."
                ),
                "migration_quarantined": True,
                "migration_quarantine_reasons": [
                    "reply_preview_content_contamination_suspected"
                ],
                "_migration_occurrence": {"occurrence_id": "legacy_occ:test-recapture"},
            }
        )
        self.write_segment(
            "a_migration_2026-01-01_2026-01-01.json",
            start="2026-01-01",
            end="2026-01-01",
            messages=[migrated],
        )
        self.write_segment(
            "b_canonical_2026-01-01_2026-01-01.json",
            start="2026-01-01",
            end="2026-01-01",
            messages=[canonical],
        )

        corpus, manifest = self.build(
            start_date="2026-01-01",
            end_date_inclusive="2026-01-01",
        )
        message = corpus["messages"][0]
        occurrences = corpus["occurrences"]
        self.assertEqual(message["content_text"], canonical["content_text"])
        self.assertEqual(
            message["evidence_trust_state"], "trusted_canonical_recapture"
        )
        self.assertTrue(message["eligible_for_accepted_evidence"])
        self.assertTrue(message["has_quarantined_occurrences"])
        self.assertEqual(message["trusted_canonical_occurrence_count"], 1)
        self.assertEqual(message["quarantined_occurrence_count"], 1)
        self.assertEqual(sum(row["quarantined"] for row in occurrences), 1)
        self.assertEqual(
            sum(
                row["migration_source"] and row["quarantined"]
                for row in occurrences
            ),
            1,
        )
        self.assertEqual(manifest["quarantine"]["unresolved_valid_message_ids"], [])

    def test_historical_disappeared_snapshot_is_searchable_but_never_analytical(self) -> None:
        legacy_row = self.message(
            dt.datetime(2026, 1, 1, 10, 0),
            content="Historical rejection block lesson no longer in current search.",
        )
        canonical_name = "channel_questions_2026-01-01_2026-01-01.json"
        initial = self.write_segment(
            canonical_name,
            start="2026-01-01",
            end="2026-01-01",
            messages=[legacy_row],
            collector_version="2.0",
        )
        legacy_payload = json.loads(initial.read_text(encoding="utf-8"))
        quarantine_dir = self.root / "raw" / "quarantine_collection_errors"
        legacy_path = self.write_json(
            quarantine_dir / "channel_questions.legacy-v2.0.json",
            legacy_payload,
        )
        current = self.write_segment(
            canonical_name,
            start="2026-01-01",
            end="2026-01-01",
            messages=[],
            collector_version="2.5",
        )
        query = legacy_payload["segment"]["query"]
        self.write_json(
            quarantine_dir
            / "channel_questions_2026-01-01_2026-01-01.v2.5-replacement-note.json",
            {
                "event_type": "discord_collector_version_replacement",
                "guild_id": GUILD_ID,
                "channel_id": CHANNEL_ID,
                "channel_name": "questions",
                "segment_start": "2026-01-01",
                "segment_end": "2026-01-01",
                "query": query,
                "legacy_final_quarantine_path": legacy_path.relative_to(
                    self.root
                ).as_posix(),
                "legacy_final_sha256": corpus_builder.sha256_file(legacy_path),
                "replacement_final_path": current.relative_to(self.root).as_posix(),
                "replacement_final_sha256": corpus_builder.sha256_file(current),
                "message_id_reconciliation": {
                    "shared_ids": 0,
                    "added_ids": [],
                    "missing_ids": [legacy_row["message_id"]],
                    "causal_claim": (
                        "No deletion, edit, or other cause is claimed from search-set "
                        "differences alone."
                    ),
                },
            },
        )

        corpus, manifest = self.build(
            start_date="2026-01-01",
            end_date_inclusive="2026-01-01",
            historical_reconciliation_dirs=[quarantine_dir],
        )
        self.assertEqual(len(corpus["messages"]), 1)
        message = corpus["messages"][0]
        self.assertEqual(message["message_id"], legacy_row["message_id"])
        self.assertEqual(message["evidence_trust_state"], "quarantined_only")
        self.assertFalse(message["eligible_for_accepted_evidence"])
        occurrence = corpus["occurrences"][0]
        self.assertEqual(occurrence["source_kind"], "historical_reconciled_segment")
        self.assertTrue(occurrence["historical_disappeared_certified"])
        self.assertIn(
            "historical_disappeared_from_latest_fresh_exact_search",
            occurrence["quarantine_reasons"],
        )
        self.assertEqual(manifest["quarantine"]["unresolved_valid_message_ids"], [])
        self.assertEqual(
            manifest["quarantine"][
                "certified_historically_unavailable_message_ids"
            ],
            [legacy_row["message_id"]],
        )
        self.assertEqual(
            manifest["historical_reconciliation"]["invalid_note_count"], 0
        )
        gates = {row["gate"]: row for row in manifest["release_gates"]}
        self.assertTrue(gates["historical_reconciliation_notes_valid"]["passed"])

    def test_historical_reconciliation_hash_mismatch_fails_closed(self) -> None:
        legacy_row = self.message(dt.datetime(2026, 1, 1, 10, 0))
        canonical_name = "channel_questions_2026-01-01_2026-01-01.json"
        initial = self.write_segment(
            canonical_name,
            start="2026-01-01",
            end="2026-01-01",
            messages=[legacy_row],
        )
        legacy_payload = json.loads(initial.read_text(encoding="utf-8"))
        quarantine_dir = self.root / "raw" / "quarantine_collection_errors"
        legacy_path = self.write_json(
            quarantine_dir / "channel_questions.legacy-v2.0.json",
            legacy_payload,
        )
        current = self.write_segment(
            canonical_name,
            start="2026-01-01",
            end="2026-01-01",
            messages=[],
            collector_version="2.5",
        )
        self.write_json(
            quarantine_dir
            / "channel_questions_2026-01-01_2026-01-01.v2.5-replacement-note.json",
            {
                "event_type": "discord_collector_version_replacement",
                "guild_id": GUILD_ID,
                "channel_id": CHANNEL_ID,
                "segment_start": "2026-01-01",
                "segment_end": "2026-01-01",
                "query": legacy_payload["segment"]["query"],
                "legacy_final_quarantine_path": legacy_path.relative_to(
                    self.root
                ).as_posix(),
                "legacy_final_sha256": "0" * 64,
                "replacement_final_path": current.relative_to(self.root).as_posix(),
                "replacement_final_sha256": corpus_builder.sha256_file(current),
                "message_id_reconciliation": {
                    "missing_ids": [legacy_row["message_id"]],
                    "added_ids": [],
                },
            },
        )

        corpus, manifest = self.build(
            start_date="2026-01-01",
            end_date_inclusive="2026-01-01",
            historical_reconciliation_dirs=[quarantine_dir],
        )
        self.assertEqual(corpus["messages"], [])
        self.assertEqual(
            manifest["historical_reconciliation"]["invalid_note_count"], 1
        )
        gates = {row["gate"]: row for row in manifest["release_gates"]}
        self.assertFalse(gates["historical_reconciliation_notes_valid"]["passed"])

    def test_historical_reconciliation_current_hash_mismatch_fails_closed(self) -> None:
        legacy_row = self.message(dt.datetime(2026, 1, 1, 10, 0))
        canonical_name = "channel_questions_2026-01-01_2026-01-01.json"
        initial = self.write_segment(
            canonical_name,
            start="2026-01-01",
            end="2026-01-01",
            messages=[legacy_row],
        )
        legacy_payload = json.loads(initial.read_text(encoding="utf-8"))
        quarantine_dir = self.root / "raw" / "quarantine_collection_errors"
        legacy_path = self.write_json(
            quarantine_dir / "channel_questions.legacy-v2.0.json",
            legacy_payload,
        )
        current = self.write_segment(
            canonical_name,
            start="2026-01-01",
            end="2026-01-01",
            messages=[],
            collector_version="2.5",
        )
        self.write_json(
            quarantine_dir
            / "channel_questions_2026-01-01_2026-01-01.v2.5-replacement-note.json",
            {
                "event_type": "discord_collector_version_replacement",
                "guild_id": GUILD_ID,
                "channel_id": CHANNEL_ID,
                "segment_start": "2026-01-01",
                "segment_end": "2026-01-01",
                "query": legacy_payload["segment"]["query"],
                "legacy_final_quarantine_path": legacy_path.relative_to(
                    self.root
                ).as_posix(),
                "legacy_final_sha256": corpus_builder.sha256_file(legacy_path),
                "replacement_final_path": current.relative_to(self.root).as_posix(),
                "replacement_final_sha256": "f" * 64,
                "message_id_reconciliation": {
                    "missing_ids": [legacy_row["message_id"]],
                    "added_ids": [],
                },
            },
        )

        corpus, manifest = self.build(
            start_date="2026-01-01",
            end_date_inclusive="2026-01-01",
            historical_reconciliation_dirs=[quarantine_dir],
        )
        self.assertEqual(corpus["messages"], [])
        self.assertEqual(
            manifest["historical_reconciliation"]["invalid_note_count"], 1
        )
        invalid_note = manifest["historical_reconciliation"]["invalid_records"][0]
        self.assertIn("reconciled_current_sha256_mismatch", invalid_note["errors"])
        gates = {row["gate"]: row for row in manifest["release_gates"]}
        self.assertFalse(gates["historical_reconciliation_notes_valid"]["passed"])

    def test_legacy_provenance_expands_occurrences_and_preserves_declared_variants(self) -> None:
        row = self.message(dt.datetime(2026, 5, 1, 10, 0), content="canonical text")
        row["_merge_provenance"] = {
            "occurrence_count": 2,
            "sources": [
                {
                    "source_file": "missing/source_a.json",
                    "collection": "primary_messages",
                    "query": "in:premium-journals after:2026-04-30 before:2026-05-02",
                    "result_index": 1,
                    "page_number": 1,
                    "complete_source": True,
                    "segment_start": "2026-05-01",
                    "segment_end": "2026-05-01",
                },
                {
                    "source_file": "missing/source_b.json",
                    "collection": "server_rejection_phrase_messages",
                    "query": "rejection block after:2026-04-30 before:2026-05-02",
                    "result_index": 7,
                    "page_number": 1,
                    "complete_source": True,
                    "segment_start": "2026-05-01",
                    "segment_end": "2026-05-01",
                },
            ],
            "field_variants": {"content_text": ["canonical text", "alternate capture"]},
        }
        legacy = self.write_json(
            self.root / "raw_discord_export_3month.json",
            {"metadata": {"guild_id": GUILD_ID}, "primary_messages": [row]},
        )
        corpus, manifest = self.build(legacy_raw_path=legacy)
        self.assertEqual(corpus["legacy_provenance"]["reconstructed_occurrences"], 2)
        self.assertEqual(len(corpus["occurrences"]), 2)
        self.assertEqual(len(corpus["messages"]), 1)
        variants = corpus["messages"][0]["_field_variants"]["content_text"]
        self.assertEqual(
            {item["value"] for item in variants}, {"canonical text", "alternate capture"}
        )
        self.assertEqual(manifest["legacy_provenance"]["coverage_contribution"], "none")

    def test_observed_forum_thread_ids_extend_inventory_with_exact_provenance(self) -> None:
        observed = self.message(dt.datetime(2026, 1, 2, 10, 0), increment=11)
        observed.pop("channel_id")
        observed.update(
            {
                "collection_channel_id": FORUM_ID,
                "collection_channel_kind": "forum channel",
                "inferred_thread_channel_id": THREAD_ID,
                "thread_channel_id_source": "forum_group_header_data_list_item_id",
                "thread_channel_id_exact": True,
                "thread_title": "All My Suffering And Learning",
                "parent_channel": "premium-journals",
            }
        )
        unresolved = self.message(dt.datetime(2026, 1, 2, 10, 1), increment=12)
        unresolved.pop("channel_id")
        unresolved.update(
            {
                "collection_channel_id": FORUM_ID,
                "collection_channel_kind": "forum channel",
                "thread_title": "Thread without row-owned ID evidence",
                "parent_channel": "premium-journals",
                "result_index": 2,
            }
        )
        self.write_segment(
            "channel_premium_journals_2026-01-02_2026-01-02.json",
            start="2026-01-02",
            end="2026-01-02",
            messages=[observed, unresolved],
            channel_id=FORUM_ID,
            channel_name="premium-journals",
            channel_kind="forum channel",
        )

        corpus, manifest = self.build(inventory_path=self.forum_inventory())
        inventory = manifest["inventory"]
        self.assertEqual(inventory["top_level_container_count"], 1)
        self.assertEqual(inventory["observed_forum_thread_count"], 1)
        self.assertEqual(inventory["container_count"], 2)
        thread = next(
            row for row in inventory["containers"] if row["container_id"] == THREAD_ID
        )
        self.assertEqual(thread["inventory_layer"], "observed_forum_thread")
        self.assertEqual(thread["parent_container_id"], FORUM_ID)
        self.assertEqual(thread["coverage_container_id"], FORUM_ID)
        self.assertTrue(thread["accessible"])
        self.assertIn(
            "forum_group_header_thread_channel_id",
            thread["identity_provenance"]["method"],
        )
        self.assertEqual(thread["identity_provenance"]["observation_count"], 1)
        self.assertEqual(len(thread["identity_provenance"]["source_occurrence_ids"]), 1)
        forum_scope = inventory["accessible_scope"]["forum_threads"]
        self.assertEqual(forum_scope["observed_exact_ids"], [THREAD_ID])
        self.assertEqual(forum_scope["unresolved_observed_occurrence_count"], 1)
        self.assertFalse(forum_scope["validated_complete"])
        self.assertIn(
            "forum_thread_inventory_completeness_not_proven",
            inventory["validation_errors"],
        )
        observed_occurrence = next(
            row for row in corpus["occurrences"] if row["message_id"] == observed["message_id"]
        )
        self.assertFalse(observed_occurrence["quarantined"])
        self.assertEqual(observed_occurrence["message_container_id"], THREAD_ID)
        self.assertEqual(observed_occurrence["parent_container_id"], FORUM_ID)

    def test_forum_inventory_cannot_be_complete_without_archive_evidence(self) -> None:
        self.write_segment(
            "channel_premium_journals_2026-01-01_2026-07-20.json",
            start="2026-01-01",
            end="2026-07-20",
            messages=[],
            channel_id=FORUM_ID,
            channel_name="premium-journals",
            channel_kind="forum channel",
        )
        _corpus, manifest = self.build(
            inventory_path=self.forum_inventory(overall_complete=True),
            release_requested=True,
        )
        self.assertFalse(manifest["inventory"]["validated_complete"])
        failed = {row["gate"] for row in manifest["release_gates"] if not row["passed"]}
        self.assertIn("channel_inventory_declared_complete_and_valid", failed)
        self.assertFalse(manifest["release_ready"])

    def test_shipped_inventory_has_38_exact_top_level_ids_and_stays_partial(self) -> None:
        path = corpus_builder.SCRIPT_DIR / "full_server_channel_inventory.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        channels = payload["channels"]
        ids = [str(row.get("channel_id") or "") for row in channels]
        self.assertEqual(len(channels), 38)
        self.assertEqual(len(set(ids)), 38)
        self.assertTrue(all(corpus_builder.MESSAGE_ID_RE.fullmatch(value) for value in ids))
        self.assertFalse(payload["inventory_complete"])
        self.assertTrue(
            payload["accessible_scope"]["top_level_containers"]["declared_complete"]
        )
        self.assertFalse(payload["accessible_scope"]["forum_threads"]["declared_complete"])

    def test_release_cli_refuses_to_write_when_gates_fail(self) -> None:
        self.write_segment(
            "channel_questions_2026-01-01_2026-01-01.json",
            start="2026-01-01",
            end="2026-01-01",
            messages=[],
        )
        with tempfile.TemporaryDirectory(dir=corpus_builder.SCRIPT_DIR) as output_dir:
            output = Path(output_dir) / "release.json"
            manifest = Path(output_dir) / "manifest.json"
            code = corpus_builder.main(
                [
                    "--segment-dir",
                    str(self.segments),
                    "--inventory",
                    str(self.inventory()),
                    "--provenance-root",
                    str(self.root),
                    "--data-cutoff-utc",
                    "2026-07-21T05:05:00Z",
                    "--release",
                    "--output",
                    str(output),
                    "--manifest",
                    str(manifest),
                ]
            )
            self.assertEqual(code, 2)
            self.assertFalse(output.exists())
            self.assertFalse(manifest.exists())


if __name__ == "__main__":
    unittest.main()
