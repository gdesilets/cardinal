from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_cardinal_database_v2 as builder  # noqa: E402


def synthetic_corpus() -> dict:
    first = {
        "message_id": "1480000000000000001",
        "channel_id": "1283941772577472643",
        "thread_title": "journal-one",
        "parent_channel": "premium-journals",
        "author": "Member One",
        "timestamp_utc": "2026-01-01T15:00:00Z",
        "content_text": "NQ rejection block example with a chart.",
        "visible_text": "Member One — NQ rejection block example with a chart.",
        "attachments": [
            {
                "attachment_id": "1480000000000000101",
                "relation_type": "owned",
                "ownership_status": "owned_exact",
                "ownership_evidence": {
                    "schema_version": "1.0.0",
                    "exact": True,
                    "basis": "exact_message_accessories_descendant_and_matching_cdn_channel",
                    "owner_message_id": "1480000000000000001",
                    "owner_channel_id": "1283941772577472643",
                    "source_channel_id": "1283941772577472643",
                    "dom_relation": "exact_message_accessories_descendant",
                },
                "filename": "chart.png",
                "thread_channel_id": "1283941772577472643",
                "url": "https://cdn.discordapp.com/attachments/1283941772577472643/1480000000000000101/chart.png",
            }
        ],
        "_merge_provenance": {
            "field_variants": {},
            "sources": [
                {
                    "source_file": "synthetic_segment.json",
                    "collection": "all_messages",
                    "query": "after:2025-12-31 before:2026-01-03",
                    "result_index": 1,
                    "page_number": 1,
                    "complete_source": True,
                }
            ],
        },
    }
    second = {
        "message_id": "1480000000000000002",
        "channel_id": "1283941772577472643",
        "thread_title": "journal-one",
        "parent_channel": "premium-journals",
        "author": "Member Two",
        "timestamp_utc": "2026-01-01T15:02:00Z",
        "content_text": "Thanks, see you tomorrow.",
        "reply_to_message_id": "1480000000000000001",
        "reply_to_content": "NQ rejection block example with a chart.",
        "attachments": [],
    }
    return {
        "metadata": {
            "guild_id": "1167376964680691732",
            "guild_name": "Synthetic Discord",
            "requested_window_start_date": "2026-01-01",
            "requested_window_end_date": "2026-01-02",
            "source_scope": "discord_only",
        },
        "raw_messages": [first, second],
        "questions_messages": [dict(first)],
        "coverage_units": [
            {
                "unit_id": "coverage:premium-journals",
                "channel_id": "1283941772577472643",
                "channel_name": "premium-journals",
                "channel_kind": "forum",
                "collection_name": "inventory_scan",
                "window_start_utc": "2026-01-01T00:00:00Z",
                "window_end_utc": "2026-01-03T00:00:00Z",
                "status": "complete",
                "unique_messages_seen": 2,
                "occurrences_seen": 3,
                "segments": [
                    {
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-01-03T00:00:00Z",
                        "status": "complete",
                        "returned_count": 2,
                    }
                ],
            }
        ],
    }


class CardinalDatabaseV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_path = self.root / "synthetic.json"
        self.output_path = self.root / "corpus.sqlite"
        self.input_path.write_text(
            json.dumps(synthetic_corpus(), ensure_ascii=False), encoding="utf-8"
        )
        self.report = builder.build_database([self.input_path], self.output_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.output_path)
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def test_partial_channel_occurrence_is_not_trusted_canonical(self) -> None:
        fields = builder.occurrence_trust_fields(
            {
                "source_kind": "channel_segment",
                "complete_source": False,
                "migration_source": False,
                "quarantined": False,
            }
        )
        self.assertEqual(fields[3], 0)
        self.assertEqual(fields[4], "untrusted_noncanonical")

        complete = builder.occurrence_trust_fields(
            {
                "source_kind": "channel_segment",
                "complete_source": True,
                "migration_source": False,
                "quarantined": False,
            }
        )
        self.assertEqual(complete[3], 1)
        self.assertEqual(complete[4], "trusted_canonical")

    def test_inferred_attachment_channel_snowflake_is_not_promoted_to_exact(self) -> None:
        inferred_channel_id = "961384152656142397"
        channel_id, exact, name, _title, _parent, basis = builder.extract_channel(
            {
                "thread_title": "Live",
                "inferred_thread_channel_id": inferred_channel_id,
                "attachments": [
                    {
                        "attachment_id": "964614171725561946",
                        "thread_channel_id": inferred_channel_id,
                    }
                ],
            },
            "1167376964680691732",
        )
        self.assertEqual(channel_id, inferred_channel_id)
        self.assertEqual(exact, 0)
        self.assertEqual(name, "Live")
        self.assertEqual(basis, "inferred_attachment_or_legacy_discord_id")

    def test_durable_attachment_manifest_fields_and_extractions_are_ingested(self) -> None:
        payload = synthetic_corpus()
        attachment = payload["raw_messages"][0]["attachments"][0]
        attachment.update(
            {
                "local_package_path": (
                    "attachments/1283941772577472643/1480000000000000001/"
                    "1480000000000000101_chart.png"
                ),
                "capture_status": "downloaded",
                "capture_terminal": True,
                "capture_attempt_count": 1,
                "capture_attempts": [
                    {
                        "attempt_number": 1,
                        "status": "downloaded",
                        "attempted_at_utc": "2026-07-21T05:05:00Z",
                    }
                ],
                "content_sha256": "a" * 64,
                "byte_size": 321,
                "mime_type": "image/png",
                "extraction_status": "complete",
                "archive_manifest_source_file_id": "manifest-source-id",
                "chart_claim_eligible": False,
                "extraction_artifacts": [
                    {
                        "extraction_id": "extract-1",
                        "method": "local_ocr_v1",
                        "status": "complete",
                        "created_at_utc": "2026-07-21T05:06:00Z",
                        "local_package_path": (
                            "attachments/extractions/1480000000000000101/extract-1.txt"
                        ),
                        "content_sha256": "b" * 64,
                        "byte_size": 12,
                        "mime_type": "text/plain",
                        "local_artifact_verified": True,
                        "extracted_text": "rejection block label",
                        "confidence": 0.73,
                    }
                ],
            }
        )
        payload["attachment_archive"] = {
            "manifest_sha256": "c" * 64,
            "counts": {"total": 1, "downloaded": 1, "unavailable": 0, "failed": 0},
            "release_gate": {
                "passed": True,
                "terminal_coverage_complete": True,
                "literal_release_complete": True,
                "byte_complete": True,
            },
        }
        durable_input = self.root / "durable.json"
        durable_output = self.root / "durable.sqlite"
        durable_input.write_text(json.dumps(payload), encoding="utf-8")
        builder.build_database([durable_input], durable_output)
        with closing(sqlite3.connect(durable_output)) as con:
            row = con.execute(
                """
                SELECT local_package_path,capture_status,capture_terminal,
                       capture_attempt_count,capture_attempts_json,content_sha256,
                       byte_size,mime_type,extraction_status,
                       archive_manifest_source_file_id,chart_claim_eligible
                FROM attachments WHERE attachment_id='1480000000000000101'
                """
            ).fetchone()
            self.assertEqual(row[1], "downloaded")
            self.assertEqual(row[2], 1)
            self.assertEqual(row[3], 1)
            self.assertEqual(json.loads(row[4])[0]["status"], "downloaded")
            self.assertEqual(row[5], "a" * 64)
            self.assertEqual(row[6], 321)
            self.assertEqual(row[7], "image/png")
            self.assertEqual(row[8], "complete")
            self.assertEqual(row[9], "manifest-source-id")
            self.assertEqual(row[10], 0)
            meta = dict(
                con.execute(
                    "SELECT key,value FROM meta WHERE key LIKE 'attachment_%'"
                )
            )
            self.assertEqual(meta["attachment_archive_manifest_sha256"], "c" * 64)
            self.assertEqual(meta["attachment_archive_terminal_coverage_complete"], "1")
            self.assertEqual(meta["attachment_archive_literal_release_complete"], "1")
            self.assertEqual(meta["attachment_archive_byte_complete"], "1")
            extraction = con.execute(
                """
                SELECT method,status,extracted_text,local_package_path,
                       content_sha256,byte_size,artifact_verified,confidence,locator_json
                FROM attachment_extractions WHERE extraction_id='extract-1'
                """
            ).fetchone()
            self.assertEqual(extraction[0], "local_ocr_v1")
            self.assertEqual(extraction[1], "complete")
            self.assertEqual(extraction[2], "rejection block label")
            self.assertEqual(
                extraction[3],
                "attachments/extractions/1480000000000000101/extract-1.txt",
            )
            self.assertEqual(extraction[4], "b" * 64)
            self.assertEqual(extraction[5], 12)
            self.assertEqual(extraction[6], 1)
            self.assertEqual(extraction[7], 0.73)
            self.assertEqual(
                json.loads(extraction[8])["local_package_path"],
                "attachments/extractions/1480000000000000101/extract-1.txt",
            )

    def test_external_questions_embed_is_metadata_only_and_never_evidence(self) -> None:
        payload = synthetic_corpus()
        message = payload["raw_messages"][0]
        message["channel_id"] = "1273692573898113076"
        attachment = message["attachments"][0]
        attachment.update(
            {
                "attachment_id": "1364178305632174100",
                "filename": "schizophrenicistalking.gif",
                "url": (
                    "https://cdn.discordapp.com/attachments/1278211283656773643/"
                    "1364178305632174100/schizophrenicistalking.gif"
                ),
                "thread_channel_id": "1278211283656773643",
                "dom_relation": "embed_descendant",
                "href_in_message_content": False,
                "relation_type": "embedded_external",
                "ownership_status": "non_owned_exact",
                "ownership_evidence": {
                    "schema_version": "1.0.0",
                    "exact": True,
                    "basis": "discord_cdn_source_channel_differs_from_exact_message_container",
                    "owner_message_id": "1480000000000000001",
                    "owner_channel_id": "1273692573898113076",
                    "source_channel_id": "1278211283656773643",
                    "dom_relation": "embed_descendant",
                },
                # Adversarial stale fields must be discarded at the DB boundary.
                "local_package_path": "attachments/outside.gif",
                "content_sha256": "f" * 64,
                "capture_status": "downloaded",
                "capture_terminal": True,
                "capture_attempt_count": 1,
                "capture_attempts": [
                    {"attempt_number": 1, "status": "downloaded"}
                ],
                "extraction_status": "complete",
                "extraction_artifacts": [
                    {
                        "extraction_id": "outside-extraction",
                        "method": "local_ocr_v1",
                        "status": "complete",
                        "local_package_path": "attachments/extractions/outside.txt",
                        "content_sha256": "e" * 64,
                        "byte_size": 1,
                        "local_artifact_verified": True,
                        "extracted_text": "outside bytes",
                    }
                ],
            }
        )
        payload["questions_messages"] = [dict(message)]
        path = self.root / "external-embed.json"
        output = self.root / "external-embed.sqlite"
        path.write_text(json.dumps(payload), encoding="utf-8")
        builder.build_database([path], output)
        with closing(sqlite3.connect(output)) as con:
            row = con.execute(
                """
                SELECT relation_type,ownership_status,source_channel_id,
                       owned_for_capture,eligible_for_attachment_evidence,
                       capture_status,capture_terminal,capture_attempt_count,
                       local_package_path,content_sha256,extraction_status,
                       extraction_artifacts_json,ownership_evidence_json
                FROM attachments WHERE attachment_id='1364178305632174100'
                """
            ).fetchone()
            self.assertEqual(row[:8], (
                "embedded_external",
                "non_owned_exact",
                "1278211283656773643",
                0,
                0,
                "metadata_only",
                0,
                0,
            ))
            self.assertIsNone(row[8])
            self.assertIsNone(row[9])
            self.assertEqual(row[10], "not_attempted")
            self.assertEqual(json.loads(row[11]), [])
            self.assertEqual(json.loads(row[12])["dom_relation"], "embed_descendant")
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM attachment_extractions").fetchone()[0],
                0,
            )
            con.execute(
                """
                INSERT INTO analysis_runs(
                  analysis_run_id,collection_run_id,schema_version,method,
                  created_at_utc,source_scope,outside_sources_used
                ) VALUES(9,1,'2.4.0','test','2026-01-03T00:00:00Z','discord_only',0)
                """
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "cannot be evidence"):
                con.execute(
                    """
                    INSERT INTO evidence_items(
                      evidence_id,analysis_run_id,attachment_id,source_type,
                      exact_excerpt,content_sha256,extraction_method,
                      extraction_confidence
                    ) VALUES('external-evidence',9,'1364178305632174100',
                             'attachment_metadata','gif','0','metadata',1.0)
                    """
                )

    def test_failed_or_unverified_extractions_never_enter_queryable_table(self) -> None:
        normalized = builder.normalize_verified_extraction(
            {
                "status": "complete",
                "local_package_path": (
                    "attachments/extractions/1480000000000000101/no-confidence.txt"
                ),
                "content_sha256": "e" * 64,
                "byte_size": 1,
                "local_artifact_verified": True,
            },
            attachment_id="1480000000000000101",
        )
        self.assertIsNotNone(normalized)
        self.assertIsNone(normalized["confidence"])
        with self.assertRaisesRegex(ValueError, "substantive detail"):
            builder.validate_capture_attempts(
                [
                    {
                        "attempt_number": 1,
                        "status": "failed",
                        "error_detail": "failed",
                    }
                ],
                attachment_id="1480000000000000101",
                capture_status="pending",
            )
        payload = synthetic_corpus()
        attachment = payload["raw_messages"][0]["attachments"][0]
        attachment.update(
            {
                "extraction_status": "failed",
                "extraction_artifacts": [
                    {
                        "extraction_id": "extract-failed",
                        "method": "local_ocr_v1",
                        "status": "failed",
                        "created_at_utc": "2026-07-21T05:06:00Z",
                        "local_package_path": None,
                        "content_sha256": None,
                        "byte_size": None,
                        "failure_code": "ocr_parse_error",
                        "failure_detail": "Local OCR produced no readable chart labels",
                    }
                ],
            }
        )
        failed_input = self.root / "failed-extraction.json"
        failed_output = self.root / "failed-extraction.sqlite"
        failed_input.write_text(json.dumps(payload), encoding="utf-8")
        builder.build_database([failed_input], failed_output)
        with closing(sqlite3.connect(failed_output)) as con:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM attachment_extractions").fetchone()[0],
                0,
            )
            retained = con.execute(
                "SELECT extraction_artifacts_json FROM attachments"
            ).fetchone()[0]
            self.assertEqual(json.loads(retained)[0]["status"], "failed")

        unverified = synthetic_corpus()
        unverified_attachment = unverified["raw_messages"][0]["attachments"][0]
        unverified_attachment.update(
            {
                "extraction_status": "complete",
                "extraction_artifacts": [
                    {
                        "extraction_id": "extract-unverified",
                        "method": "local_ocr_v1",
                        "status": "complete",
                        "created_at_utc": "2026-07-21T05:06:00Z",
                        "local_package_path": (
                            "attachments/extractions/1480000000000000101/unverified.txt"
                        ),
                        "content_sha256": "d" * 64,
                        "byte_size": 4,
                    }
                ],
            }
        )
        unverified_input = self.root / "unverified-extraction.json"
        unverified_input.write_text(json.dumps(unverified), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "verified local artifact"):
            builder.build_database(
                [unverified_input], self.root / "unverified-extraction.sqlite"
            )

    def test_raw_retention_fts_and_no_skill_population(self) -> None:
        self.assertEqual(self.report["status"], "passed")
        with closing(self.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 1)
            self.assertEqual(
                con.execute("SELECT extraction_status FROM attachments").fetchone()[0],
                "not_attempted",
            )
            # Trading words in raw text never populate analytical facts by themselves.
            for table in (
                "claims",
                "concept_terms",
                "setup_models",
                "setup_instances",
                "instruments",
                "trade_episodes",
                "questions",
            ):
                self.assertEqual(
                    con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0],
                    0,
                    table,
                )
            chatter = con.execute(
                """
                SELECT content_text,reply_target_state FROM messages
                WHERE message_id='1480000000000000002'
                """
            ).fetchone()
            self.assertEqual(chatter, ("Thanks, see you tomorrow.", "resolved"))
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'rejection'"
                ).fetchone()[0],
                2,
            )

    def test_discord_only_constraints(self) -> None:
        with closing(self.connect()) as con:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(
                    """
                    INSERT INTO analysis_runs(
                      analysis_run_id,collection_run_id,schema_version,method,
                      created_at_utc,source_scope,outside_sources_used
                    ) VALUES(99,1,'2.0.0','bad','2026-01-03T00:00:00Z','discord_only',1)
                    """
                )

    def test_input_with_outside_sources_is_rejected(self) -> None:
        bad_path = self.root / "outside.json"
        bad_output = self.root / "outside.sqlite"
        bad = synthetic_corpus()
        bad["metadata"]["outside_sources_used"] = True
        bad_path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outside_sources_used"):
            builder.build_database([bad_path], bad_output)
        self.assertFalse(bad_output.exists())

    def test_evidence_backed_setup_card(self) -> None:
        with closing(self.connect()) as con:
            con.execute(
                """
                INSERT INTO analysis_runs(
                  analysis_run_id,collection_run_id,schema_version,method,
                  created_at_utc,source_scope,outside_sources_used,limitations
                ) VALUES(1,1,'2.0.0','manual_discord_annotation',
                         '2026-01-03T00:00:00Z','discord_only',0,'Synthetic test only.')
                """
            )
            con.execute(
                """
                INSERT INTO analysis_entities(
                  entity_id,entity_type,created_analysis_run_id,lifecycle_status,
                  source_scope,outside_sources_used
                ) VALUES('setup:test','setup_instance',1,'active','discord_only',0)
                """
            )
            excerpt = "NQ rejection block example with a chart."
            con.execute(
                """
                INSERT INTO evidence_items(
                  evidence_id,analysis_run_id,message_id,source_type,exact_excerpt,
                  locator_json,content_sha256,extraction_method,extraction_confidence,
                  source_scope,outside_sources_used
                ) VALUES('evidence:test',1,'1480000000000000001','message_text',?,
                         '{}',?,'manual_exact_excerpt',1.0,'discord_only',0)
                """,
                (excerpt, hashlib.sha256(excerpt.encode("utf-8")).hexdigest().upper()),
            )
            con.execute(
                """
                INSERT INTO claims(
                  claim_id,subject_entity_id,facet,claim_text,normalized_value_json,
                  claim_kind,epistemic_status,resolution_status,analysis_run_id,
                  source_scope,outside_sources_used,created_at_utc
                ) VALUES('claim:test','setup:test','setup_identity',?,NULL,
                         'explicit_example','explicit_source','accepted',1,
                         'discord_only',0,'2026-01-03T00:00:00Z')
                """,
                (excerpt,),
            )
            con.execute(
                "INSERT INTO claim_evidence VALUES('claim:test','evidence:test','defines')"
            )
            author_id = con.execute(
                "SELECT author_id FROM messages WHERE message_id='1480000000000000001'"
            ).fetchone()[0]
            con.execute(
                """
                INSERT INTO setup_instances(
                  instance_id,occurrence_type,primary_message_id,primary_author_id,
                  direction,lifecycle_state,identity_resolution_status,
                  identity_claim_id,notes
                ) VALUES('setup:test','chart_example','1480000000000000001',?,
                         NULL,NULL,'explicit','claim:test','No missing fields were filled.')
                """,
                (author_id,),
            )
            row = con.execute(
                """
                SELECT instance_id,missing_executed_instrument,missing_direction,
                       missing_htf_narrative,source_scope,outside_sources_used,
                       evidence_row_count
                FROM v_cardinal_setup_cards
                WHERE instance_id='setup:test'
                """
            ).fetchone()
            self.assertEqual(row, ("setup:test", 1, 1, 1, "discord_only", 0, 1))
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM v_discord_only_audit").fetchone()[0],
                0,
            )

    def test_unbacked_direction_is_rejected(self) -> None:
        with closing(self.connect()) as con:
            con.execute(
                """
                INSERT INTO analysis_runs(
                  analysis_run_id,collection_run_id,schema_version,method,
                  created_at_utc,source_scope,outside_sources_used
                ) VALUES(1,1,'2.0.0','test','2026-01-03T00:00:00Z','discord_only',0)
                """
            )
            con.execute(
                """
                INSERT INTO analysis_entities(
                  entity_id,entity_type,created_analysis_run_id,lifecycle_status,
                  source_scope,outside_sources_used
                ) VALUES('setup:test','setup_instance',1,'active','discord_only',0)
                """
            )
            excerpt = "A setup was discussed."
            con.execute(
                """
                INSERT INTO evidence_items(
                  evidence_id,analysis_run_id,message_id,source_type,exact_excerpt,
                  locator_json,content_sha256,extraction_method,extraction_confidence,
                  source_scope,outside_sources_used
                ) VALUES('evidence:test',1,'1480000000000000001','message_text',?,
                         '{}',?,'manual_exact_excerpt',1.0,'discord_only',0)
                """,
                (excerpt, hashlib.sha256(excerpt.encode("utf-8")).hexdigest().upper()),
            )
            con.execute(
                """
                INSERT INTO claims(
                  claim_id,subject_entity_id,facet,claim_text,claim_kind,
                  epistemic_status,resolution_status,analysis_run_id,source_scope,
                  outside_sources_used,created_at_utc
                ) VALUES('claim:test','setup:test','setup_identity',?,
                         'explicit_example','explicit_source','accepted',1,
                         'discord_only',0,'2026-01-03T00:00:00Z')
                """,
                (excerpt,),
            )
            con.execute(
                "INSERT INTO claim_evidence VALUES('claim:test','evidence:test','defines')"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(
                    """
                    INSERT INTO setup_instances(
                      instance_id,occurrence_type,primary_message_id,direction,
                      identity_resolution_status,identity_claim_id
                    ) VALUES('setup:test','chart_example','1480000000000000001',
                             'long','explicit','claim:test')
                    """
                )

    def test_canonical_corpus_and_manifest_adapter(self) -> None:
        corpus_path = self.root / "raw_corpus_working.json"
        manifest_path = self.root / "coverage_manifest_working.json"
        output_path = self.root / "canonical.sqlite"
        corpus_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "artifact_type": "raw_corpus",
                    "scope": {
                        "guild_id": "1167376964680691732",
                        "window_start_utc": "2026-01-01T00:00:00Z",
                        "window_end_utc": "2026-01-03T00:00:00Z",
                    },
                    "messages": [
                        {
                            "message_id": "1480000000000000201",
                            "channel_id": "1273692573898113076",
                            "channel_name": "questions",
                            "author": "Question Member",
                            "timestamp_utc": "2026-01-02T14:00:00Z",
                            "content_text": "Does this level still apply?",
                            "_field_variants": {"channel_name": ["questions"]},
                            "_corpus_provenance": ["occurrence:one"],
                        }
                    ],
                    "occurrences": [
                        {
                            "occurrence_id": "occurrence:one",
                            "message_id": "1480000000000000201",
                            "source_file": "raw/channel_segments/questions.json",
                            "collection_name": "channel_segment",
                            "query_text": "in:questions",
                            "result_index": 0,
                            "page_number": 1,
                            "payload": {"message_id": "1480000000000000201"},
                            "provenance": {"complete_source": True},
                        }
                    ],
                    "segments": [
                        {
                            "segment_id": "segment:one",
                            "source_file": "raw/channel_segments/questions.json",
                            "channel_id": "1273692573898113076",
                            "start": "2026-01-01T00:00:00Z",
                            "end": "2026-01-03T00:00:00Z",
                            "status": "complete",
                            "message_count": 1,
                            "occurrence_count": 1,
                        }
                    ],
                    "quarantine": [
                        {
                            "quarantine_id": "quarantine:one",
                            "message_id": "1480000000000000201",
                            "occurrence_id": "occurrence:one",
                            "reason": "Synthetic audit marker",
                        }
                    ],
                    "legacy_provenance": {"source": "synthetic"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "artifact_type": "coverage_manifest",
                    "scope": {
                        "guild_id": "1167376964680691732",
                        "window_start_utc": "2026-01-01T00:00:00Z",
                        "window_end_utc": "2026-01-03T00:00:00Z",
                    },
                    "inventory": {
                        "channels": [
                            {
                                "channel_id": "1273692573898113076",
                                "name": "questions",
                                "kind": "text",
                                "is_accessible": 1,
                            }
                        ]
                    },
                    "coverage": {
                        "units": [
                            {
                                "unit_id": "coverage:questions",
                                "channel_id": "1273692573898113076",
                                "collection_name": "inventory_scan",
                                "window_start_utc": "2026-01-01T00:00:00Z",
                                "window_end_utc": "2026-01-03T00:00:00Z",
                                "status": "complete",
                                "messages_seen": 1,
                            }
                        ]
                    },
                    "status": "working",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        report = builder.build_database(
            [corpus_path, manifest_path], output_path
        )
        self.assertEqual(report["status"], "passed")
        with closing(sqlite3.connect(output_path)) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM message_source_occurrences").fetchone()[0],
                1,
            )
            self.assertEqual(con.execute("SELECT COUNT(*) FROM source_segments").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM quarantine_records").fetchone()[0], 1)
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM legacy_provenance_records").fetchone()[0],
                1,
            )
            self.assertEqual(
                con.execute("SELECT status FROM collection_runs WHERE run_id=1").fetchone()[0],
                "complete",
            )
            self.assertEqual(
                con.execute(
                    "SELECT inventory_basis FROM channel_inventory WHERE channel_id='1273692573898113076'"
                ).fetchone()[0],
                "explicit_merger_coverage",
            )

    def test_non_message_inventory_rows_do_not_block_complete_collection_run(self) -> None:
        corpus_path = self.root / "message-and-non-message-inventory.json"
        output_path = self.root / "message-and-non-message-inventory.sqlite"
        text_id = "1273692573898113076"
        voice_id = "1273692573898113077"
        message_id = "1480000000000000251"
        corpus_path.write_text(
            json.dumps(
                {
                    "schema_version": "2.1.0",
                    "source_scope": "discord_only",
                    "outside_sources_used": 0,
                    "scope": {
                        "guild_id": "1167376964680691732",
                        "utc_start_inclusive": "2026-01-01T06:00:00Z",
                        "utc_end_exclusive": "2026-01-02T06:00:00Z",
                    },
                    "inventory": {
                        "containers": [
                            {
                                "container_id": text_id,
                                "name": "questions",
                                "kind": "text channel",
                                "accessible": True,
                            },
                            {
                                "container_id": voice_id,
                                "name": "voice room",
                                "kind": "voice channel",
                                "message_bearing": False,
                                "accessible": True,
                            },
                        ]
                    },
                    "coverage": {
                        "containers": [
                            {
                                "container_id": text_id,
                                "name": "questions",
                                "kind": "text channel",
                                "window_start_utc": "2026-01-01T06:00:00Z",
                                "window_end_utc": "2026-01-02T06:00:00Z",
                                "status": "complete",
                            }
                        ]
                    },
                    "messages": [
                        {
                            "message_id": message_id,
                            "channel_id": text_id,
                            "channel_name": "questions",
                            "author": "Member",
                            "timestamp_utc": "2026-01-01T14:00:00Z",
                            "content_text": "message-bearing coverage fixture",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        report = builder.build_database([corpus_path], output_path)
        self.assertEqual(report["status"], "passed")
        with closing(sqlite3.connect(output_path)) as con:
            self.assertEqual(
                con.execute("SELECT status FROM collection_runs WHERE run_id=1").fetchone()[0],
                "complete",
            )
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM channel_inventory").fetchone()[0],
                2,
            )

    def test_current_merger_containers_shape_is_ingested_at_the_right_grain(self) -> None:
        corpus_path = self.root / "current_merger_shape.json"
        output_path = self.root / "current_merger_shape.sqlite"
        top_level_id = "1283941772577472643"
        observed_thread_id = "1480000000000000901"
        message_id = "1480000000000000902"
        reply_message_id = "1480000000000000903"
        exact_author_id = "1480000000000000904"
        corpus_path.write_text(
            json.dumps(
                {
                    "schema_version": "2.1.0",
                    "artifact_type": "discord_serverwide_working_corpus",
                    "scope": {
                        "guild_id": "1167376964680691732",
                        "utc_start_inclusive": "2026-01-01T06:00:00Z",
                        "utc_end_exclusive": "2026-01-03T06:00:00Z",
                    },
                    "inventory": {
                        "containers": [
                            {
                                "container_id": top_level_id,
                                "name": "premium-journals",
                                "kind": "forum channel",
                                "inventory_layer": "top_level_container",
                                "accessible": True,
                            },
                            {
                                "container_id": observed_thread_id,
                                "parent_container_id": top_level_id,
                                "name": "journal-one",
                                "kind": "forum thread",
                                "inventory_layer": "observed_forum_thread",
                                "accessible": True,
                            },
                        ]
                    },
                    "coverage": {
                        "segments": [
                            {
                                "segment_id": "coverage-segment:must-not-be-a-unit",
                                "query_container_id": top_level_id,
                                "status": "verified_empty",
                            }
                        ],
                        "containers": [
                            {
                                "container_id": top_level_id,
                                "name": "premium-journals",
                                "kind": "forum channel",
                                "status": "complete",
                                "missing_day_count": 0,
                            },
                            {
                                "container_id": observed_thread_id,
                                "name": "journal-one",
                                "kind": "forum thread",
                                "status": "gap",
                                "missing_day_count": 1,
                                "missing_date_ranges": [
                                    {"start_date": "2026-01-02", "end_date": "2026-01-02"}
                                ],
                            },
                        ],
                        "gaps": [
                            {"container_id": observed_thread_id, "status": "gap"}
                        ],
                    },
                    "segments": [
                        {
                            "segment_id": "segment:current-shape",
                            "source_file_relative_path": "inputs/segment.json",
                            "query_container_id": top_level_id,
                            "start_date": "2026-01-01",
                            "end_date": "2026-01-02",
                            "status": "complete",
                            "unique_message_ids_computed": 2,
                            "captured_rows_computed": 2,
                        }
                    ],
                    "messages": [
                        {
                            "message_id": message_id,
                            "channel_id": observed_thread_id,
                            "parent_channel_id": top_level_id,
                            "channel_name": "journal-one",
                            "author": "Member",
                            "author_id": exact_author_id,
                            "timestamp_utc": "2026-01-02T14:00:00Z",
                            "content_text": "current merger contract fixture",
                            "permalink": (
                                "https://discord.com/channels/1167376964680691732/"
                                f"{observed_thread_id}/{message_id}"
                            ),
                        },
                        {
                            "message_id": reply_message_id,
                            "channel_id": observed_thread_id,
                            "parent_channel_id": top_level_id,
                            "channel_name": "journal-one",
                            "author": "Reply Member",
                            "author_id": exact_author_id,
                            "timestamp_utc": "2026-01-02T14:01:00Z",
                            "content_text": "exact reply fixture",
                            "reply_to_message_id": message_id,
                            "reply_to_content": "current merger contract fixture",
                            "reply_target_state": "resolved",
                            "permalink": (
                                "https://discord.com/channels/1167376964680691732/"
                                f"{observed_thread_id}/{reply_message_id}"
                            ),
                        },
                    ],
                    "occurrences": [
                        {
                            "occurrence_id": "occurrence:current-shape",
                            "message_id": message_id,
                            "source_file": "inputs/segment.json",
                            "collection_name": "channel_segment",
                            "query_text": "in:premium-journals",
                            "result_index": 0,
                            "page_number": 1,
                            "payload": {"message_id": message_id},
                            "provenance": {"complete_source": True},
                        },
                        {
                            "occurrence_id": "occurrence:current-shape-reply",
                            "message_id": reply_message_id,
                            "source_file": "inputs/segment.json",
                            "collection_name": "channel_segment",
                            "query_text": "in:premium-journals",
                            "result_index": 1,
                            "page_number": 1,
                            "payload": {
                                "message_id": reply_message_id,
                                "author_id": exact_author_id,
                                "reply_to_message_id": message_id,
                            },
                            "provenance": {"complete_source": True},
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        report = builder.build_database([corpus_path], output_path)
        self.assertEqual(report["status"], "passed")
        with closing(sqlite3.connect(output_path)) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM channel_inventory").fetchone()[0], 2)
            # Two explicit container-grain units plus the normal observed-message
            # source unit.  Neither coverage.segments nor coverage.gaps may be
            # misread as an additional collection unit.
            self.assertEqual(con.execute("SELECT COUNT(*) FROM collection_units").fetchone()[0], 3)
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM collection_units WHERE unit_type='explicit_coverage'"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                con.execute(
                    "SELECT status FROM collection_units "
                    "WHERE channel_id=? AND unit_type='explicit_coverage'",
                    (observed_thread_id,),
                ).fetchone()[0],
                "partial",
            )
            self.assertEqual(
                con.execute(
                    "SELECT channel_id,message_count,occurrence_count FROM source_segments "
                    "WHERE segment_id='segment:current-shape'"
                ).fetchone(),
                (top_level_id, 2, 2),
            )
            self.assertEqual(
                con.execute(
                    "SELECT discord_user_id,user_id_exact,identity_resolution "
                    "FROM authors WHERE author_id=?",
                    (f"discord-user:{exact_author_id}",),
                ).fetchone(),
                (exact_author_id, 1, "exact_discord_user_id"),
            )
            self.assertEqual(
                con.execute(
                    "SELECT author_id,reply_to_message_id,reply_target_state,"
                    "permalink,permalink_confidence FROM messages WHERE message_id=?",
                    (reply_message_id,),
                ).fetchone(),
                (
                    f"discord-user:{exact_author_id}",
                    message_id,
                    "resolved",
                    (
                        "https://discord.com/channels/1167376964680691732/"
                        f"{observed_thread_id}/{reply_message_id}"
                    ),
                    "exact",
                ),
            )

    def test_current_occurrence_source_fields_preserve_distinct_provenance(self) -> None:
        message_id = "1480000000000000401"
        corpus_path = self.root / "current_occurrences.json"
        output_path = self.root / "current_occurrences.sqlite"
        corpus_path.write_text(
            json.dumps(
                {
                    "schema_version": "2.0.0",
                    "artifact_type": "discord_serverwide_corpus_working",
                    "scope": {
                        "guild_id": "1167376964680691732",
                        "utc_start_inclusive": "2026-06-01T00:00:00Z",
                        "utc_end_exclusive": "2026-06-15T00:00:00Z",
                    },
                    "messages": [
                        {
                            "message_id": message_id,
                            "channel_id": "1283941772577472643",
                            "channel_name": "questions",
                            "author": "Member",
                            "timestamp_utc": "2026-06-07T15:00:00Z",
                            "content_text": "Canonical occurrence alias regression fixture.",
                        }
                    ],
                    "occurrences": [
                        {
                            "occurrence_id": "occ:current-source-a",
                            "message_id": message_id,
                            "source_kind": "channel_segment",
                            "source_file_relative_path": "raw/questions_nq_es.json",
                            "source_file_sha256": "a" * 64,
                            "source_collection": "instrument_comparison_messages",
                            "source_query": "in:questions NQ ES",
                            "segment_start_date": "2026-06-01",
                            "segment_end_date": "2026-06-14",
                            "result_index": 6,
                            "page_number": 1,
                            "quarantined": True,
                            "quarantine_reasons": ["exact_message_container_id_missing"],
                            "payload": {"message_id": message_id},
                        },
                        {
                            "occurrence_id": "occ:current-source-b",
                            "message_id": message_id,
                            "source_kind": "channel_segment",
                            "source_file_relative_path": "raw/questions_rb.json",
                            "source_file_sha256": "b" * 64,
                            "source_collection": "questions_rb_messages",
                            "source_query": "in:questions RB",
                            "segment_start_date": "2026-06-01",
                            "segment_end_date": "2026-06-14",
                            "result_index": 6,
                            "page_number": 1,
                            "quarantined": True,
                            "quarantine_reasons": ["exact_message_container_id_missing"],
                            "payload": {"message_id": message_id},
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        report = builder.build_database([corpus_path], output_path)
        self.assertEqual(report["status"], "passed")
        with closing(sqlite3.connect(output_path)) as con:
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM message_source_occurrences WHERE message_id=?",
                    (message_id,),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                con.execute(
                    """
                    SELECT source_file,sha256,collection_name,query_text
                    FROM source_artifacts
                    WHERE collection_method='explicit_corpus_occurrence'
                    ORDER BY source_file
                    """
                ).fetchall(),
                [
                    (
                        "raw/questions_nq_es.json",
                        "a" * 64,
                        "instrument_comparison_messages",
                        "in:questions NQ ES",
                    ),
                    (
                        "raw/questions_rb.json",
                        "b" * 64,
                        "questions_rb_messages",
                        "in:questions RB",
                    ),
                ],
            )
            self.assertEqual(
                con.execute(
                    """
                    SELECT occurrence_id,collection_name,query_text,
                           segment_start_utc,segment_end_utc
                    FROM message_source_occurrences
                    WHERE message_id=?
                    ORDER BY occurrence_id
                    """,
                    (message_id,),
                ).fetchall(),
                [
                    (
                        "occ:current-source-a",
                        "instrument_comparison_messages",
                        "in:questions NQ ES",
                        "2026-06-01T00:00:00Z",
                        "2026-06-14T00:00:00Z",
                    ),
                    (
                        "occ:current-source-b",
                        "questions_rb_messages",
                        "in:questions RB",
                        "2026-06-01T00:00:00Z",
                        "2026-06-14T00:00:00Z",
                    ),
                ],
            )
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM quarantine_records WHERE message_id=?",
                    (message_id,),
                ).fetchone()[0],
                2,
            )

    def test_explicit_occurrence_collision_fails_closed(self) -> None:
        message_id = "1480000000000000402"
        corpus_path = self.root / "colliding_occurrences.json"
        output_path = self.root / "colliding_occurrences.sqlite"
        occurrence = {
            "message_id": message_id,
            "source_kind": "channel_segment",
            "source_file_relative_path": "raw/questions.json",
            "source_file_sha256": "c" * 64,
            "source_collection": "questions_rb_messages",
            "source_query": "in:questions RB",
            "result_index": 6,
            "page_number": 1,
            "quarantined": True,
            "quarantine_reasons": ["exact_message_container_id_missing"],
            "payload": {"message_id": message_id},
        }
        corpus_path.write_text(
            json.dumps(
                {
                    "metadata": {
                        "guild_id": "1167376964680691732",
                        "requested_window_start_date": "2026-06-01",
                        "requested_window_end_date": "2026-06-14",
                    },
                    "messages": [
                        {
                            "message_id": message_id,
                            "channel_id": "1283941772577472643",
                            "channel_name": "questions",
                            "author": "Member",
                            "timestamp_utc": "2026-06-07T15:00:00Z",
                            "content_text": "Intentional occurrence identity collision.",
                        }
                    ],
                    "occurrences": [
                        {**occurrence, "occurrence_id": "occ:collision-a"},
                        {**occurrence, "occurrence_id": "occ:collision-b"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "Explicit occurrence collision"):
            builder.build_database([corpus_path], output_path)
        self.assertFalse(output_path.exists())
        self.assertFalse(output_path.with_suffix(output_path.suffix + ".building").exists())

    def test_migration_quarantine_is_searchable_but_blocked_from_analysis(self) -> None:
        quarantined_id = "1480000000000000301"
        recaptured_id = "1480000000000000302"
        corpus_path = self.root / "trust_corpus.json"
        output_path = self.root / "trust.sqlite"
        corpus_path.write_text(
            json.dumps(
                {
                    "schema_version": "2.1.0",
                    "artifact_type": "discord_serverwide_working_corpus",
                    "scope": {
                        "guild_id": "1167376964680691732",
                        "window_start_utc": "2026-01-01T00:00:00Z",
                        "window_end_utc": "2026-01-03T00:00:00Z",
                    },
                    "messages": [
                        {
                            "message_id": quarantined_id,
                            "channel_id": "1283941772577472643",
                            "channel_name": "premium-journals",
                            "author": "Legacy Member",
                            "timestamp_utc": "2026-01-01T15:00:00Z",
                            "content_text": "quarantineonlytoken rejection block text",
                        },
                        {
                            "message_id": recaptured_id,
                            "channel_id": "1283941772577472643",
                            "channel_name": "premium-journals",
                            "author": "Recaptured Member",
                            "timestamp_utc": "2026-01-01T15:01:00Z",
                            "content_text": "trustedrecapturetoken canonical rejection block text",
                        },
                    ],
                    "occurrences": [
                        {
                            "occurrence_id": "occ:quarantined-only",
                            "message_id": quarantined_id,
                            "source_kind": "channel_segment",
                            "migration_source": True,
                            "migration_quarantined": True,
                            "quarantined": True,
                            "quarantine_reasons": ["exact_permalink_unavailable"],
                            "source_file": "staging/legacy_segment.json",
                            "collection_name": "channel_segment",
                            "payload": {
                                "message_id": quarantined_id,
                                "content_text": "quarantineonlytoken rejection block text",
                            },
                        },
                        {
                            "occurrence_id": "occ:recaptured-legacy",
                            "message_id": recaptured_id,
                            "source_kind": "channel_segment",
                            "migration_source": True,
                            "migration_quarantined": True,
                            "quarantined": True,
                            "quarantine_reasons": ["reply_preview_contamination_suspected"],
                            "source_file": "staging/legacy_segment.json",
                            "collection_name": "channel_segment",
                            "payload": {
                                "message_id": recaptured_id,
                                "content_text": "legacy contaminated variant",
                            },
                        },
                        {
                            "occurrence_id": "occ:trusted-recapture",
                            "message_id": recaptured_id,
                            "source_kind": "channel_segment",
                            "complete_source": True,
                            "migration_source": False,
                            "quarantined": False,
                            "source_file": "raw/channel_segment.json",
                            "collection_name": "channel_segment",
                            "payload": {
                                "message_id": recaptured_id,
                                "content_text": "trustedrecapturetoken canonical rejection block text",
                            },
                        },
                    ],
                    "quarantine": {
                        "occurrences": [
                            {
                                "occurrence_id": "occ:quarantined-only",
                                "message_id": quarantined_id,
                                "reasons": ["exact_permalink_unavailable"],
                            },
                            {
                                "occurrence_id": "occ:recaptured-legacy",
                                "message_id": recaptured_id,
                                "reasons": ["reply_preview_contamination_suspected"],
                            },
                        ]
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        report = builder.build_database([corpus_path], output_path)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["counts"]["analysis_ineligible_messages"], 1)

        with closing(sqlite3.connect(output_path)) as con:
            con.execute("PRAGMA foreign_keys=ON")
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'quarantineonlytoken'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM v_analysis_eligible_messages WHERE message_id=?",
                    (quarantined_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM v_analysis_eligible_messages WHERE message_id=?",
                    (recaptured_id,),
                ).fetchone()[0],
                1,
            )
            trust_row = con.execute(
                """
                SELECT evidence_trust_state,eligible_for_accepted_evidence,
                       occurrence_trust_state,occurrence_raw_json,canonical_raw_json
                FROM v_message_trust_lookup
                WHERE message_id=?
                """,
                (quarantined_id,),
            ).fetchone()
            self.assertEqual(trust_row[:3], ("quarantined_only", 0, "quarantined_migration"))
            self.assertIn("quarantineonlytoken", trust_row[3])
            self.assertIn("quarantineonlytoken", trust_row[4])
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM quarantine_records WHERE occurrence_id IN (?,?)",
                    ("occ:quarantined-only", "occ:recaptured-legacy"),
                ).fetchone()[0],
                2,
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "canonical recapture"):
                con.execute(
                    """
                    UPDATE messages
                    SET evidence_trust_state='trusted_source',
                        eligible_for_accepted_evidence=1
                    WHERE message_id=?
                    """,
                    (quarantined_id,),
                )

            con.execute(
                """
                INSERT INTO analysis_runs(
                  analysis_run_id,collection_run_id,schema_version,method,
                  created_at_utc,source_scope,outside_sources_used
                ) VALUES(1,1,'2.1.0','trust-regression',
                         '2026-01-03T00:00:00Z','discord_only',0)
                """
            )
            for entity_id in (
                "setup:quarantined",
                "trade:quarantined",
                "setup:recaptured",
            ):
                entity_type = "trade_episode" if entity_id.startswith("trade:") else "setup_instance"
                con.execute(
                    """
                    INSERT INTO analysis_entities(
                      entity_id,entity_type,created_analysis_run_id,lifecycle_status,
                      source_scope,outside_sources_used
                    ) VALUES(?,?,1,'active','discord_only',0)
                    """,
                    (entity_id, entity_type),
                )

            def insert_evidence(evidence_id: str, message_id: str, text: str) -> None:
                con.execute(
                    """
                    INSERT INTO evidence_items(
                      evidence_id,analysis_run_id,message_id,source_type,exact_excerpt,
                      locator_json,content_sha256,extraction_method,extraction_confidence,
                      source_scope,outside_sources_used
                    ) VALUES(?,1,?,'message_text',?,'{}',?,'exact_database_text',1.0,
                             'discord_only',0)
                    """,
                    (
                        evidence_id,
                        message_id,
                        text,
                        hashlib.sha256(text.encode("utf-8")).hexdigest().upper(),
                    ),
                )

            insert_evidence(
                "evidence:quarantined", quarantined_id, "quarantineonlytoken"
            )
            insert_evidence(
                "evidence:recaptured", recaptured_id, "trustedrecapturetoken"
            )
            self.assertEqual(
                con.execute(
                    "SELECT eligible_for_accepted_claims FROM evidence_items WHERE evidence_id='evidence:quarantined'"
                ).fetchone()[0],
                0,
            )

            con.execute(
                """
                INSERT INTO claims(
                  claim_id,subject_entity_id,facet,claim_text,claim_kind,
                  epistemic_status,resolution_status,analysis_run_id,source_scope,
                  outside_sources_used,created_at_utc
                ) VALUES('claim:quarantined','setup:quarantined','setup_identity',
                         'Untrusted legacy claim','explicit_example','explicit_source',
                         'unresolved',1,'discord_only',0,'2026-01-03T00:00:00Z')
                """
            )
            con.execute(
                "INSERT INTO claim_evidence VALUES('claim:quarantined','evidence:quarantined','defines')"
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "cannot become accepted"):
                con.execute(
                    "UPDATE claims SET resolution_status='accepted' WHERE claim_id='claim:quarantined'"
                )
            con.execute(
                """
                INSERT INTO setup_instances(
                  instance_id,occurrence_type,primary_message_id,
                  identity_resolution_status,identity_claim_id
                ) VALUES('setup:quarantined','chart_example',?,'unresolved','claim:quarantined')
                """,
                (quarantined_id,),
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "trusted canonical recapture"):
                con.execute(
                    "UPDATE setup_instances SET identity_resolution_status='explicit' WHERE instance_id='setup:quarantined'"
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "strict trade comparison"):
                con.execute(
                    """
                    INSERT INTO trade_episodes(
                      trade_id,instance_id,episode_kind,strict_comparison_eligible,
                      linkage_status,episode_claim_id
                    ) VALUES('trade:quarantined','setup:quarantined','example',1,
                             'linked','claim:quarantined')
                    """
                )

            con.execute(
                """
                INSERT INTO claims(
                  claim_id,subject_entity_id,facet,claim_text,claim_kind,
                  epistemic_status,resolution_status,analysis_run_id,source_scope,
                  outside_sources_used,created_at_utc
                ) VALUES('claim:recaptured','setup:recaptured','setup_identity',
                         'Trusted recaptured claim','explicit_example','explicit_source',
                         'accepted',1,'discord_only',0,'2026-01-03T00:00:00Z')
                """
            )
            con.execute(
                "INSERT INTO claim_evidence VALUES('claim:recaptured','evidence:recaptured','defines')"
            )
            con.execute(
                """
                INSERT INTO setup_instances(
                  instance_id,occurrence_type,primary_message_id,
                  identity_resolution_status,identity_claim_id
                ) VALUES('setup:recaptured','chart_example',?,'explicit','claim:recaptured')
                """,
                (recaptured_id,),
            )
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM setup_instances WHERE instance_id='setup:recaptured'"
                ).fetchone()[0],
                1,
            )


if __name__ == "__main__":
    unittest.main()
