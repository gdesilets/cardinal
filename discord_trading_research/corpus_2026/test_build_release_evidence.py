from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import build_release_evidence as release


GUILD_ID = "1167376964680691732"
FULL_CHANNEL_ID = "1329615478716502097"
TARGET_CHANNEL_ID = "1359593949110472777"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snowflake_at(value: dt.datetime, increment: int = 0) -> str:
    millis = int(value.timestamp() * 1000)
    return str(((millis - 1420070400000) << 22) + increment)


def raw_payload(
    *,
    channel_id: str,
    channel_name: str,
    start: str,
    end: str,
    query: str,
    messages: list[dict[str, object]],
    captured_at: str = "2026-01-02T07:00:00Z",
) -> dict[str, object]:
    total = len(messages)
    pages = 1 if total else 0
    for index, message in enumerate(messages, start=1):
        message.setdefault("result_index", index)
        message.setdefault("page_number", 1)
        message.setdefault("collection_channel_id", channel_id)
        message.setdefault("collection_channel_name", channel_name)
    submission = {
        "mode": "fresh",
        "query": query,
        "submission_count": 1,
        "submitted_at_utc": captured_at,
    }
    if total:
        first_index = (pages - 1) * 25 + 1
        observations = [
            {
                "sequence": sequence,
                "observed_at_utc": captured_at,
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
            "search_submission": submission,
            "stable_bottom": {
                "required_observations": 2,
                "observations": observations,
            },
        }
    else:
        observations = [
            {
                "sequence": sequence,
                "state": "empty_candidate",
                "visible_result_count": 0,
                "panel_text": "No Results",
                "observed_at_utc": captured_at,
            }
            for sequence in (1, 2, 3)
        ]
        completion_evidence = {
            "schema_version": "1.0.0",
            "query": query,
            "reported_total": 0,
            "reported_pages": 0,
            "terminal_state": "stable_empty",
            "search_submission": submission,
            "stable_empty": {
                "required_observations": 3,
                "observations": observations,
            },
        }
    return {
        "collector_version": "test",
        "guild_id": GUILD_ID,
        "captured_at_utc": captured_at,
        "requested_container": {"channel_id": channel_id, "channel_name": channel_name},
        "segment": {"start": start, "end": end, "query": query},
        "reported_total": total,
        "reported_pages": pages,
        "pages_captured": pages,
        "captured_rows": total,
        "unique_message_ids": total,
        "gap_indices": [],
        "container_mismatch_count": 0,
        "complete": True,
        "completion_evidence": completion_evidence,
        "messages": messages,
    }


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "working").mkdir()
        (self.root / "raw" / "channel_segments").mkdir(parents=True)
        (self.root / "raw" / "relevance_segments").mkdir(parents=True)
        (self.root / "raw" / "relevance_audit_segments").mkdir(parents=True)
        self.cutoff = dt.datetime(2026, 1, 2, 6, tzinfo=dt.timezone.utc)
        self.audit_time = dt.datetime(2026, 1, 2, 8, tzinfo=dt.timezone.utc)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def inspect_row(self, path: Path) -> dict[str, object]:
        return release.orchestrator.inspect_artifact(path, self.root).serializable()

    def test_count_reconciliation_requires_real_fresh_observation_and_hashed_segment(self) -> None:
        relative = "raw/channel_segments/channel_live_1329615478716502097_2026-01-01_2026-01-01.json"
        path = self.root / relative
        query = "in:Live after:2025-12-31 before:2026-01-02"
        message_id = snowflake_at(dt.datetime(2026, 1, 1, 15, tzinfo=dt.timezone.utc))
        write_json(
            path,
            raw_payload(
                channel_id=FULL_CHANNEL_ID,
                channel_name="Live",
                start="2026-01-01",
                end="2026-01-01",
                query=query,
                messages=[{"message_id": message_id, "content_text": "Discord only"}],
            ),
        )
        raw_sha = release.sha256_file(path)
        progress = {
            "artifacts": [self.inspect_row(path)],
            "jobs": [
                {
                    "job_id": "full-live",
                    "job_kind": "full_capture_or_empty_verification",
                    "channel_id": FULL_CHANNEL_ID,
                    "channel_name": "Live",
                    "query_prefix": "in:Live",
                    "query_core": "in:live",
                    "window": {"start": "2026-01-01", "end": "2026-01-01"},
                    "status": "complete",
                    "segments": [
                        {
                            "status": "complete",
                            "evidence_artifacts": [relative],
                        }
                    ],
                }
            ],
        }
        plan = {
            "channel_policies": [
                {"channel_id": FULL_CHANNEL_ID, "name": "Live", "policy": "full_capture"}
            ]
        }
        corpus = {
            "segments": [
                {
                    "segment_id": "segment-live-1",
                    "source_file_relative_path": f"{self.root.name}/{relative}",
                    "source_file_sha256": raw_sha,
                    "query_container_id": FULL_CHANNEL_ID,
                    "query": query,
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-01",
                    "computed_complete": True,
                    "reported_total": 1,
                }
            ]
        }
        raw_by_relative, _, errors = release.load_raw_artifacts(self.root, progress)
        self.assertEqual(errors, [])
        counts_path = self.root / "working" / "counts.json"
        observation = {
            "observation_id": "count-live-1",
            "source": "operator_recorded_countSearch",
            "channel_id": FULL_CHANNEL_ID,
            "start": "2026-01-01",
            "end": "2026-01-01",
            "query": query,
            "reported_total": 1,
            "reported_pages": 1,
            "observed_at_utc": "2026-01-02T06:01:00Z",
        }
        write_json(
            counts_path,
            {"artifact_type": "discord_count_observations", "count_observations": [observation]},
        )
        observations, _ = release.load_evidence_rows(
            [counts_path], "count_observations", "count_observation_artifact", self.root
        )
        result = release.build_count_reconciliation(
            plan=plan,
            progress=progress,
            raw_by_relative=raw_by_relative,
            corpus_manifest=corpus,
            observations=observations,
            cutoff=self.cutoff,
        )[0]
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["segment_ids"], ["segment-live-1"])
        self.assertTrue(any(raw_sha in ref for ref in result["evidence_refs"]))

        observation["observed_at_utc"] = "2026-01-02T05:59:59Z"
        write_json(
            counts_path,
            {"artifact_type": "discord_count_observations", "count_observations": [observation]},
        )
        observations, _ = release.load_evidence_rows(
            [counts_path], "count_observations", "count_observation_artifact", self.root
        )
        result = release.build_count_reconciliation(
            plan=plan,
            progress=progress,
            raw_by_relative=raw_by_relative,
            corpus_manifest=corpus,
            observations=observations,
            cutoff=self.cutoff,
        )[0]
        self.assertEqual(result["status"], "pending")
        self.assertIn("fresh_full_window_count_observation_missing", result["pending_reasons"])

    def test_residual_packets_are_deterministic_and_never_auto_reviewed(self) -> None:
        first = snowflake_at(dt.datetime(2026, 1, 1, 14, tzinfo=dt.timezone.utc))
        second = snowflake_at(dt.datetime(2026, 1, 1, 14, 1, tzinfo=dt.timezone.utc))
        target_relative = "raw/relevance_segments/relevance_chat_query_2026-01-01_2026-01-01.json"
        audit_relative = "raw/relevance_audit_segments/audit_chat_2026-01-01_2026-01-01.json"
        write_json(
            self.root / target_relative,
            raw_payload(
                channel_id=TARGET_CHANNEL_ID,
                channel_name="chat",
                start="2026-01-01",
                end="2026-01-01",
                query="in:chat RB after:2025-12-31 before:2026-01-02",
                messages=[{"message_id": first, "content_text": "RB"}],
            ),
        )
        write_json(
            self.root / audit_relative,
            raw_payload(
                channel_id=TARGET_CHANNEL_ID,
                channel_name="chat",
                start="2026-01-01",
                end="2026-01-01",
                query="in:chat after:2025-12-31 before:2026-01-02",
                messages=[
                    {"message_id": first, "content_text": "RB"},
                    {"message_id": second, "content_text": "unmatched but relevant?"},
                ],
            ),
        )
        progress = {
            "artifacts": [self.inspect_row(self.root / target_relative), self.inspect_row(self.root / audit_relative)],
            "jobs": [
                {
                    "job_id": "target-chat-rb",
                    "job_kind": "targeted_search",
                    "channel_id": TARGET_CHANNEL_ID,
                    "channel_name": "chat",
                    "status": "complete",
                    "segments": [{"status": "complete", "evidence_artifacts": [target_relative]}],
                },
                {
                    "job_id": "audit-chat-2026-01-01",
                    "job_kind": "residual_audit_census_day",
                    "channel_id": TARGET_CHANNEL_ID,
                    "channel_name": "chat",
                    "window": {"start": "2026-01-01", "end": "2026-01-01"},
                    "status": "complete",
                    "segments": [{"status": "complete", "evidence_artifacts": [audit_relative]}],
                },
            ],
        }
        raw_by_relative, _, errors = release.load_raw_artifacts(self.root, progress)
        self.assertEqual(errors, [])
        packets_a = release.build_residual_packets(progress, raw_by_relative, self.cutoff)
        packets_b = release.build_residual_packets(progress, raw_by_relative, self.cutoff)
        self.assertEqual(packets_a, packets_b)
        packet = packets_a["packets"][0]
        self.assertEqual(packet["residual_message_ids"], [second])

        pending = release.build_residual_reviews(packets_a, [], self.cutoff)[0]
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["unreviewed_residual_rows"], 1)

        reviews_path = self.root / "working" / "reviews.json"
        review = {
            "job_id": packet["job_id"],
            "packet_id": packet["packet_id"],
            "status": "complete",
            "reviewed_at_utc": "2026-01-02T07:00:00Z",
            "reviewer": {"type": "human", "id": "reviewer-1", "method": "row_by_row"},
            "classifications": [
                {"message_id": second, "decision": "not_relevant", "rationale": "social only"}
            ],
            "new_terms": [],
        }
        write_json(
            reviews_path,
            {"artifact_type": "discord_residual_review_results", "reviews": [review]},
        )
        review_rows, _ = release.load_evidence_rows(
            [reviews_path], "reviews", "residual_review_results", self.root
        )
        passed = release.build_residual_reviews(packets_a, review_rows, self.cutoff)[0]
        self.assertEqual(passed["status"], "complete")
        self.assertEqual(passed["unreviewed_residual_rows"], 0)

        review["new_terms"] = [{"term": "new term", "discord_source_message_ids": [second]}]
        write_json(
            reviews_path,
            {"artifact_type": "discord_residual_review_results", "reviews": [review]},
        )
        review_rows, _ = release.load_evidence_rows(
            [reviews_path], "reviews", "residual_review_results", self.root
        )
        blocked = release.build_residual_reviews(packets_a, review_rows, self.cutoff)[0]
        self.assertEqual(blocked["status"], "pending")
        self.assertIn(
            "new_terms_require_rerun_regenerated_packet_and_repeat_review",
            blocked["pending_reasons"],
        )

    def test_zero_targeted_channels_emit_no_required_residual_reviews(self) -> None:
        progress = {"jobs": [], "artifacts": []}
        plan = {
            "channel_policies": [
                {
                    "channel_id": TARGET_CHANNEL_ID,
                    "policy": "full_capture",
                }
            ],
            "query_families": [],
            "job_expansion": {"residual_audit": {"audit_dates": []}},
        }
        packets = release.build_residual_packets(
            progress, {}, self.cutoff, plan
        )
        self.assertEqual(packets["packet_count"], 0)
        self.assertFalse(packets["review_required"])
        self.assertEqual(release.build_residual_reviews(packets, [], self.cutoff), [])

    def make_database(self) -> tuple[Path, Path, Path, str, str]:
        corpus_data_path = self.root / "working" / "corpus.json"
        write_json(corpus_data_path, {"artifact_type": "discord_corpus", "messages": []})
        corpus_sha = release.sha256_file(corpus_data_path)
        corpus_manifest_path = self.root / "working" / "corpus_manifest.json"
        write_json(
            corpus_manifest_path,
            {
                "release": {"data_cutoff_utc": "2026-01-02T06:30:00Z"},
                "counts": {"unique_messages": 2, "source_occurrences": 1},
                "coverage": {"segments": []},
            },
        )
        qid = snowflake_at(dt.datetime(2026, 1, 1, 16, tzinfo=dt.timezone.utc))
        aid = snowflake_at(dt.datetime(2026, 1, 1, 16, 1, tzinfo=dt.timezone.utc))
        db_path = self.root / "working" / "analysis.sqlite"
        con = sqlite3.connect(db_path)
        con.executescript(
            """
            CREATE TABLE collection_runs(built_at_utc TEXT,outside_sources_used INTEGER,source_scope TEXT);
            CREATE TABLE source_artifacts(sha256 TEXT);
            CREATE TABLE messages(message_id TEXT PRIMARY KEY);
            CREATE TABLE message_source_occurrences(
              message_id TEXT,quarantined INTEGER,trust_state TEXT,raw_json TEXT
            );
            CREATE TABLE analysis_runs(analysis_run_id INTEGER);
            CREATE TABLE questions(
              question_id TEXT,primary_message_id TEXT,resolution_status TEXT
            );
            CREATE TABLE answers(answer_id TEXT,resolution_status TEXT,answer_claim_id TEXT);
            CREATE TABLE answer_messages(
              answer_id TEXT,message_id TEXT,sequence_order INTEGER,message_role TEXT
            );
            CREATE TABLE question_answer_links(
              question_id TEXT,answer_id TEXT,direct_reply INTEGER,link_type TEXT
            );
            CREATE TABLE claims(
              claim_id TEXT,facet TEXT,claim_text TEXT,normalized_value_json TEXT,
              claim_kind TEXT,epistemic_status TEXT,resolution_status TEXT,limitations TEXT
            );
            CREATE TABLE claim_evidence(claim_id TEXT,evidence_id TEXT);
            CREATE TABLE evidence_items(
              evidence_id TEXT,message_id TEXT,attachment_id TEXT,
              eligible_for_accepted_claims INTEGER,source_scope TEXT,outside_sources_used INTEGER
            );
            CREATE TABLE attachments(
              attachment_id TEXT,message_id TEXT,relation_type TEXT,
              ownership_status TEXT,ownership_evidence_json TEXT,
              owned_for_capture INTEGER,eligible_for_attachment_evidence INTEGER,
              media_kind TEXT,filename TEXT,
              local_package_path TEXT,content_sha256 TEXT,byte_size INTEGER,
              capture_status TEXT,capture_terminal INTEGER,extraction_status TEXT,
              extraction_artifacts_json TEXT,capture_attempt_count INTEGER,
              archive_manifest_source_file_id TEXT,capture_failure_code TEXT,
              capture_failure_detail TEXT,chart_claim_eligible INTEGER
            );
            CREATE TABLE attachment_extractions(
              attachment_id TEXT,status TEXT,local_package_path TEXT,
              content_sha256 TEXT,byte_size INTEGER,artifact_verified INTEGER,
              locator_json TEXT
            );
            CREATE TABLE relevance_annotations(message_id TEXT,label TEXT);
            CREATE TABLE setup_performance_rollups(
              rollup_id TEXT,claim_id TEXT,eligible_count INTEGER,wins INTEGER,losses INTEGER,
              breakevens INTEGER,unknowns INTEGER,observed_win_rate REAL,not_causal INTEGER,
              limitations TEXT
            );
            """
        )
        con.execute(
            "INSERT INTO collection_runs VALUES('2026-01-02T07:00:00Z',0,'discord_only')"
        )
        con.execute("INSERT INTO source_artifacts VALUES(?)", (corpus_sha,))
        con.executemany("INSERT INTO messages VALUES(?)", [(qid,), (aid,)])
        exact_payload = {
            "payload": {
                "reply_to_message_id": qid,
                "reply_to_message_id_source": "owned_reply_context_descendant_content_id",
                "reply_target_scope_exact": True,
                "reply_target_content_id": f"message-content-{qid}",
                "reply_to_channel_id": TARGET_CHANNEL_ID,
                "reply_to_permalink": f"https://discord.com/channels/{GUILD_ID}/{TARGET_CHANNEL_ID}/{qid}",
                "attachments": [],
                "media_assets": [],
            }
        }
        con.execute(
            "INSERT INTO message_source_occurrences VALUES(?,0,'trusted_canonical',?)",
            (aid, json.dumps(exact_payload)),
        )
        con.execute("INSERT INTO analysis_runs VALUES(1)")
        con.execute("INSERT INTO questions VALUES('q1',?,'answered')", (qid,))
        con.execute("INSERT INTO answers VALUES('a1','answered','answer-claim')")
        con.execute("INSERT INTO answer_messages VALUES('a1',?,1,'direct_reply')", (aid,))
        con.execute("INSERT INTO question_answer_links VALUES('q1','a1',1,'discord_reply_to')")
        con.execute(
            "INSERT INTO claims VALUES('prob1','performance','Observed win rate 50%',?,"
            "'observed_association','observed_association','accepted',?)",
            (json.dumps({"observed_win_rate": 0.5}), "Descriptive selected-corpus share; not a calibrated probability or causal estimate."),
        )
        con.execute("INSERT INTO evidence_items VALUES('ev1',?,NULL,1,'discord_only',0)", (qid,))
        con.execute("INSERT INTO claim_evidence VALUES('prob1','ev1')")
        con.commit()
        con.close()
        return db_path, corpus_data_path, corpus_manifest_path, qid, aid

    def open_context(self, db: Path, corpus_data: Path, manifest: Path) -> release.DatabaseContext:
        corpus_artifact = release.hash_artifact(corpus_data, "corpus_data", self.root)
        manifest_artifact = release.hash_artifact(manifest, "corpus_manifest", self.root)
        value = release.read_json_object(manifest, "corpus manifest")
        context = release.open_database_context(
            database_path=db,
            corpus_data=corpus_artifact,
            corpus_manifest_artifact=manifest_artifact,
            corpus_manifest=value,
            root=self.root,
            cutoff=self.cutoff,
            audit_time=self.audit_time,
        )
        assert context is not None
        return context

    def test_reply_audit_accepts_only_owned_scoped_descendant_id(self) -> None:
        db, corpus_data, manifest, _, aid = self.make_database()
        context = self.open_context(db, corpus_data, manifest)
        try:
            result = release.audit_reply_resolution(context, guild_id=GUILD_ID, audit_time=self.audit_time)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["direct_answer_linkage_errors"], 0)
        finally:
            context.connection.close()

        con = sqlite3.connect(db)
        raw = json.loads(con.execute(
            "SELECT raw_json FROM message_source_occurrences WHERE message_id=?", (aid,)
        ).fetchone()[0])
        raw["payload"]["reply_to_message_id_source"] = "reply_preview_link"
        con.execute(
            "UPDATE message_source_occurrences SET raw_json=? WHERE message_id=?",
            (json.dumps(raw), aid),
        )
        con.commit()
        con.close()
        context = self.open_context(db, corpus_data, manifest)
        try:
            result = release.audit_reply_resolution(context, guild_id=GUILD_ID, audit_time=self.audit_time)
            self.assertEqual(result["status"], "pending")
            self.assertGreater(result["direct_answer_linkage_errors"], 0)
        finally:
            context.connection.close()

    def test_attachment_chart_label_and_claim_calibration_are_fail_closed(self) -> None:
        db, corpus_data, manifest, qid, _ = self.make_database()
        attachment_id = snowflake_at(dt.datetime(2026, 1, 1, 16, tzinfo=dt.timezone.utc), 2)
        con = sqlite3.connect(db)
        con.execute(
            """
            INSERT INTO attachments(
              attachment_id,message_id,relation_type,ownership_status,
              ownership_evidence_json,owned_for_capture,
              eligible_for_attachment_evidence,media_kind,filename,
              local_package_path,content_sha256,byte_size,capture_status,
              capture_terminal,extraction_status,extraction_artifacts_json,
              capture_attempt_count,archive_manifest_source_file_id
            ) VALUES(?,?,'owned','owned_exact',?,1,1,'image','chart.png',?, ?,123,
                     'downloaded',1,'not_attempted','[]',1,'manifest')
            """,
            (
                attachment_id,
                qid,
                json.dumps(
                    {
                        "exact": True,
                        "owner_message_id": qid,
                        "owner_channel_id": TARGET_CHANNEL_ID,
                        "source_channel_id": TARGET_CHANNEL_ID,
                    }
                ),
                f"attachments/channel/{qid}/{attachment_id}_chart.png",
                "a" * 64,
            ),
        )
        con.commit()
        con.close()
        context = self.open_context(db, corpus_data, manifest)
        try:
            attachment_result = release.audit_attachments_and_charts(context, audit_time=self.audit_time)
            claim_result = release.audit_claim_calibration(context, audit_time=self.audit_time)
            self.assertEqual(attachment_result["status"], "pending")
            self.assertEqual(attachment_result["unlabeled_chart_dependent_count"], 1)
            self.assertEqual(claim_result["status"], "passed")
        finally:
            context.connection.close()

        con = sqlite3.connect(db)
        con.execute(
            "UPDATE claims SET normalized_value_json=? WHERE claim_id='prob1'",
            (json.dumps({"observed_win_rate": 0.5, "chart_dependent": True}),),
        )
        con.commit()
        con.close()
        context = self.open_context(db, corpus_data, manifest)
        try:
            attachment_result = release.audit_attachments_and_charts(context, audit_time=self.audit_time)
            self.assertEqual(attachment_result["status"], "pending")
            self.assertEqual(
                attachment_result["chart_claim_without_local_extraction_count"], 1
            )
        finally:
            context.connection.close()

        con = sqlite3.connect(db)
        failed_extraction = {
            "status": "failed",
            "local_package_path": None,
            "content_sha256": None,
            "byte_size": None,
            "local_artifact_verified": False,
        }
        con.execute(
            "UPDATE attachments SET extraction_status='failed',extraction_artifacts_json=? "
            "WHERE attachment_id=?",
            (json.dumps([failed_extraction]), attachment_id),
        )
        con.execute(
            "INSERT INTO attachment_extractions VALUES(?,?,?,?,?,?,?)",
            (
                attachment_id,
                "failed",
                None,
                None,
                None,
                0,
                json.dumps(failed_extraction),
            ),
        )
        con.commit()
        con.close()
        context = self.open_context(db, corpus_data, manifest)
        try:
            attachment_result = release.audit_attachments_and_charts(
                context, audit_time=self.audit_time
            )
            self.assertEqual(attachment_result["status"], "pending")
            self.assertEqual(
                attachment_result["chart_claim_without_local_extraction_count"], 1
            )
        finally:
            context.connection.close()

        con = sqlite3.connect(db)
        con.execute("DELETE FROM attachment_extractions")
        extraction = {
            "status": "complete",
            "local_package_path": f"attachments/extractions/{attachment_id}/ocr.txt",
            "content_sha256": "b" * 64,
            "byte_size": 12,
            "local_artifact_verified": True,
        }
        con.execute(
            "UPDATE attachments SET extraction_status='complete',extraction_artifacts_json=? "
            "WHERE attachment_id=?",
            (json.dumps([extraction]), attachment_id),
        )
        con.execute(
            "INSERT INTO attachment_extractions VALUES(?,?,?,?,?,?,?)",
            (
                attachment_id,
                "complete",
                extraction["local_package_path"],
                extraction["content_sha256"],
                extraction["byte_size"],
                1,
                json.dumps(extraction),
            ),
        )
        con.commit()
        con.close()
        context = self.open_context(db, corpus_data, manifest)
        try:
            attachment_result = release.audit_attachments_and_charts(
                context, audit_time=self.audit_time
            )
            self.assertEqual(attachment_result["status"], "passed")
        finally:
            context.connection.close()

        con = sqlite3.connect(db)
        con.execute(
            "UPDATE attachments SET capture_status='failed',local_package_path=NULL,"
            "content_sha256=NULL,byte_size=NULL WHERE attachment_id=?",
            (attachment_id,),
        )
        con.commit()
        con.close()
        context = self.open_context(db, corpus_data, manifest)
        try:
            attachment_result = release.audit_attachments_and_charts(
                context, audit_time=self.audit_time
            )
            self.assertEqual(attachment_result["status"], "pending")
            self.assertGreater(attachment_result["attachment_archive_issue_count"], 0)
            self.assertIn(
                "terminal_failed_attachment_blocks_literal_release",
                json.dumps(attachment_result["attachment_archive_issue_examples"]),
            )
        finally:
            context.connection.close()

        con = sqlite3.connect(db)
        con.execute("UPDATE claims SET limitations='' WHERE claim_id='prob1'")
        con.commit()
        con.close()
        context = self.open_context(db, corpus_data, manifest)
        try:
            claim_result = release.audit_claim_calibration(context, audit_time=self.audit_time)
            self.assertEqual(claim_result["status"], "pending")
            self.assertEqual(claim_result["uncalibrated_success_probability_count"], 1)
        finally:
            context.connection.close()

    def test_documented_external_embed_is_allowed_only_as_metadata(self) -> None:
        db, corpus_data, manifest, _qid, aid = self.make_database()
        attachment_id = "1364178305632174100"
        evidence = {
            "schema_version": "1.0.0",
            "exact": True,
            "basis": "discord_cdn_source_channel_differs_from_exact_message_container",
            "owner_message_id": aid,
            "owner_channel_id": TARGET_CHANNEL_ID,
            "source_channel_id": "1278211283656773643",
            "dom_relation": "embed_descendant",
        }
        con = sqlite3.connect(db)
        raw = json.loads(
            con.execute(
                "SELECT raw_json FROM message_source_occurrences WHERE message_id=?",
                (aid,),
            ).fetchone()[0]
        )
        raw["payload"]["attachments"] = [
            {
                "attachment_id": attachment_id,
                "relation_type": "embedded_external",
                "ownership_status": "non_owned_exact",
                "ownership_evidence": evidence,
                "dom_relation": "embed_descendant",
                "thread_channel_id": "1278211283656773643",
                "filename": "schizophrenicistalking.gif",
            }
        ]
        con.execute(
            "UPDATE message_source_occurrences SET raw_json=? WHERE message_id=?",
            (json.dumps(raw), aid),
        )
        con.execute(
            """
            INSERT INTO attachments(
              attachment_id,message_id,relation_type,ownership_status,
              ownership_evidence_json,owned_for_capture,
              eligible_for_attachment_evidence,media_kind,filename,
              local_package_path,content_sha256,byte_size,capture_status,
              capture_terminal,extraction_status,extraction_artifacts_json,
              capture_attempt_count,archive_manifest_source_file_id
            ) VALUES(?,?, 'embedded_external','non_owned_exact',?,0,0,
                     'image','schizophrenicistalking.gif',NULL,NULL,NULL,
                     'metadata_only',0,'not_attempted','[]',0,NULL)
            """,
            (attachment_id, aid, json.dumps(evidence)),
        )
        con.commit()
        con.close()
        context = self.open_context(db, corpus_data, manifest)
        try:
            result = release.audit_attachments_and_charts(
                context, audit_time=self.audit_time
            )
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["documented_non_owned_attachment_count"], 1)
            self.assertEqual(result["attachment_archive_issue_count"], 0)
            self.assertEqual(result["attachment_owner_count"], 0)
        finally:
            context.connection.close()

    def test_atomic_write_refuses_overwrite_and_preserves_existing_bytes(self) -> None:
        output = self.root / "working" / "release.json"
        release.atomic_write_json(output, {"first": True}, overwrite=False)
        original = output.read_bytes()
        with self.assertRaises(FileExistsError):
            release.atomic_write_json(output, {"second": True}, overwrite=False)
        self.assertEqual(output.read_bytes(), original)
        release.atomic_write_json(output, {"second": True}, overwrite=True)
        self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["second"])


if __name__ == "__main__":
    unittest.main()
