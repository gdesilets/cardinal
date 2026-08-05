from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

import package_final_release as release
import discord_attachment_archiver as attachment_archiver
import build_scoped_release_evidence as scoped_evidence
import qa.validate_scoped_release as scoped_qa


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def relevance_policy() -> dict:
    return {"enabled": False}


def executed_command_integrity() -> dict:
    anchor_id = (
        release.reply_provenance_contract.EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID
    )
    return {
        "schema_version": "1.0.0",
        "passed": True,
        "audited_segment_count": 1,
        "audited_message_count": 1,
        "expected_segment_count": 1,
        "expected_segment_present": True,
        "legacy_anchor_message_id": anchor_id,
        "legacy_anchor_count": 1,
        "candidate_count": 1,
        "accepted_exact_context_count": 1,
        "failure_count": 0,
        "candidate_message_ids": [anchor_id],
        "failures": [],
    }


def exact_wordle_raw_row() -> dict:
    contract = release.reply_provenance_contract
    message_id = contract.EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID
    context = "LukeLarps\nused\nPlay"
    return {
        "message_id": message_id,
        "article_id": f"search-result-{message_id}",
        "article_aria_labelledby": (
            f"message-username-{message_id} uid_3 "
            f"message-content-{message_id} "
            f"message-accessories-{message_id} uid_4 "
            f"message-timestamp-{message_id}"
        ),
        "author": "Wordle",
        "author_id": contract.EXECUTED_COMMAND_AUTHOR_ID,
        "author_id_source": "owner_scoped_avatar_cdn_path",
        "author_id_conflict": False,
        "author_verified_app_exact": True,
        "content_scope_exact": True,
        "content_text": "LukeLarps was playing",
        "reply_context": context,
        "reply_to_content": context,
        "reply_context_present": True,
        "reply_context_scope_exact": False,
        "reply_context_dom_class": "executedCommand_c19a55",
        "reply_context_dom_tag": "DIV",
        "reply_context_aria_hidden": True,
        "reply_context_article_binding_exact": True,
        "reply_context_owner_message_id": message_id,
        "reply_context_executed_command_exact": True,
        "reply_target_owner_scoped": False,
        "reply_target_scope_exact": False,
        "reply_target_content_text": "",
        "reply_to_message_id": None,
        "reply_to_channel_id": None,
        "reply_to_permalink": None,
        "reply_to_message_id_source": None,
        "reply_to_message_id_candidates": [],
        "reply_target_id_candidates": [],
        "reply_target_content_id": None,
        "reply_target_aria_labelledby": None,
        "reply_target_aria_describedby": None,
        "reply_target_data_list_item_id": None,
        "reply_to_message_id_conflict": False,
        "reply_to_channel_id_conflict": False,
        "reply_target_resolution_status": contract.EXECUTED_COMMAND_STATUS,
        "reply_target_unavailability_documented": True,
        "reply_context_non_reply_exact": True,
        "reply_context_non_reply_type": contract.EXECUTED_COMMAND_NON_REPLY_TYPE,
    }


def corpus_manifest() -> dict:
    scope_sha = release.sha256_file(release.DEFAULT_AUTHORIZED_SCOPE)
    timestamp_integrity = {
        "schema_version": "1.0.0",
        "passed": True,
        "content_hash_bound": True,
        "unresolved_message_count": 0,
        "invalid_sidecar_count": 0,
        "unused_revalidation_record_count": 0,
        "external_revalidation_message_count": 0,
        "external_revalidation_used_record_count": 0,
        "sidecar_count": 0,
        "sidecars": [],
    }
    executed_integrity = executed_command_integrity()
    path_policy = {
        "gate": "premium_journals_authoritative_v2_5_source_integrity",
        "passed": True,
        "standard_authoritative_directory": "raw/channel_segments",
        "premium_authoritative_directory": "raw/channel_segments_v2_5",
        "premium_legacy_preservation_directory": "raw/channel_segments",
        "premium_legacy_directory_policy": "preservation_only_not_authoritative",
        "premium_collector_version_required": "2.6",
        "required_roots_supplied_exactly_once": True,
        "legacy_premium_authoritative_occurrence_count": 0,
        "premium_collector_version_mismatch_count": 0,
        "premium_collector_version_mismatch_paths": [],
        "premium_provenance_missing_segment_count": 0,
        "premium_provenance_missing_segments": [],
        "invalid_premium_authoritative_file_count": 0,
        "invalid_premium_authoritative_paths": [],
        "accepted_premium_bound_source_file_count": 201,
        "accepted_premium_segment_count": 201,
        "accepted_premium_daily_date_count": 201,
        "duplicate_premium_daily_dates": [],
        "legacy_premium_preservation_file_count": 1,
        "accepted_premium_source_file_set_sha256": "a" * 64,
        "accepted_premium_message_id_set_sha256": "b" * 64,
    }
    reconciliation = {
        "provided": True,
        "inventory_complete": False,
        "enumeration_complete": False,
        "closure_proven": False,
        "source_sha256": "e" * 64,
        "added_thread_ids": [],
        "bound_inputs": [
            {
                "role": role,
                "relative_path": f"raw/{role}.json",
                "sha256": character * 64,
            }
            for role, character in (
                ("baseline", "1"),
                ("additive_evidence_source", "2"),
                ("additive_evidence_bound_partial", "3"),
            )
        ],
        "message_scope_closure": {
            "gate": "premium_journals_message_data_scope_closure",
            "passed": True,
            "closure_proven": True,
            "status": "complete",
            "required_parent_container_id": "1283941772577472643",
            "required_calendar_day_count": 201,
            "complete_calendar_day_count": 201,
            "parent_segment_count": 201,
            "required_exact_daily_parent_segment_count": 201,
            "invalid_daily_partition_segment_count": 0,
            "duplicate_daily_date_count": 0,
            "missing_date_ranges": [],
        },
    }
    inventory_rows = [
        {
            "container_id": channel_id,
            "name": release.AUTHORIZED_PARENT_IDENTITIES[channel_id][0],
            "kind": release.AUTHORIZED_PARENT_IDENTITIES[channel_id][1],
            "parent_container_id": None,
            "inventory_layer": "top_level_container",
        }
        for channel_id in sorted(release.AUTHORIZED_PARENT_IDS)
    ]
    return {
        "schema_version": "2.1.0",
        "artifact_type": "discord_serverwide_coverage_manifest",
        "status": "complete",
        "generated_at_utc": "2026-07-21T06:00:00Z",
        "data_cutoff_utc": "2026-07-21T05:30:00Z",
        "scope": {
            "guild_id": release.EXPECTED_GUILD_ID,
            "start_date_inclusive": release.EXPECTED_START_DATE,
            "end_date_inclusive": release.EXPECTED_END_DATE,
            "timezone": release.EXPECTED_TIMEZONE,
            "utc_start_inclusive": release.EXPECTED_START_UTC,
            "utc_end_exclusive": release.EXPECTED_END_UTC,
            "local_calendar_days": release.EXPECTED_LOCAL_DAYS,
        },
        "inventory": {
            "provided": True,
            "validated_complete": True,
            "guild_id": release.EXPECTED_GUILD_ID,
            "validation_errors": [],
            "containers": inventory_rows,
            "scope_derivation": {
                "child_inventory_reconciliation": reconciliation,
            },
        },
        "authorized_collection_scope": {
            "enabled": True,
            "schema_version": "1.0.0",
            "scope_status": "user_narrowed",
            "source_sha256": scope_sha,
            "source_size_bytes": 123,
            "guild_id": release.EXPECTED_GUILD_ID,
            "source_scope": "discord_only",
            "outside_sources_used": False,
            "allowed_top_level_containers": [
                {
                    "channel_id": channel_id,
                    "name": release.AUTHORIZED_PARENT_IDENTITIES[channel_id][0],
                    "logical_name": (
                        "questions"
                        if channel_id == "1273692573898113076"
                        else None
                    ),
                    "kind": release.AUTHORIZED_PARENT_IDENTITIES[channel_id][1],
                    "include_exact_child_threads": True,
                }
                for channel_id in sorted(release.AUTHORIZED_PARENT_IDS)
            ],
            "excluded": {
                "ambiguous_fail_closed_file_count": 0,
                "file_set_sha256": "C" * 64,
                "message_id_sets_sha256": "D" * 64,
            },
            "release_gate": {
                "gate": "authorized_collection_scope_enforced",
                "passed": True,
            },
            "canonical_path_policy": path_policy,
            "child_inventory_reconciliation": reconciliation,
        },
        "coverage": {"gaps": [], "file_failures": []},
        "timestamp_scope_integrity": timestamp_integrity,
        "executed_command_reply_provenance_integrity": executed_integrity,
        "source_files": [
            {
                "source_file_id": "source:1",
                "exists": True,
                "sha256": "A" * 64,
                "size_bytes": 123,
                "relative_path": "raw/channel_segments/a.json",
            }
        ],
        "quarantine": {
            "unresolved_valid_message_ids": [],
            "invalid_message_id_occurrence_count": 0,
            "invalid_migration_sidecar_record_count": 0,
            "unmatched_migration_sidecar_record_count": 0,
        },
        "release_ready": True,
        "release_gates": [
            {"gate": "exact_target_window", "passed": True},
            {"gate": "authorized_collection_scope_enforced", "passed": True},
            dict(path_policy),
            {
                **reconciliation["message_scope_closure"],
            },
            {
                "gate": "timestamp_scope_integrity",
                "passed": True,
                "detail": timestamp_integrity,
            },
            {
                "gate": "executed_command_reply_provenance_integrity",
                "passed": True,
                "detail": executed_integrity,
            },
        ],
        "relevance_policy": {"enabled": False},
        "source_scope": "discord_only",
        "outside_sources_used": 0,
    }


def qa_report(
    authoritative_sha256: str = "F" * 64,
    release_evidence_path: Path | None = None,
) -> dict:
    return {
        "schema_version": "1.0.0",
        "artifact_type": "independent_discord_corpus_validation",
        "status": "passed",
        "overall_assessment": "Ready to share",
        "scope": {
            "guild_id": release.EXPECTED_GUILD_ID,
            "source_scope": "discord_only",
            "outside_sources_used": False,
            "window_calendar_timezone": release.EXPECTED_TIMEZONE,
            "window_start_local_date": release.EXPECTED_START_DATE,
            "window_end_local_date_inclusive": release.EXPECTED_END_DATE,
            "window_start_utc": release.EXPECTED_START_UTC,
            "window_end_exclusive_utc": release.EXPECTED_END_UTC,
            "local_calendar_days": release.EXPECTED_LOCAL_DAYS,
            "data_cutoff_utc": "2026-07-21T05:30:00Z",
            "final_day_complete": True,
            "premium_authoritative_directory": "raw/channel_segments_v2_5",
            "premium_collector_version_required": "2.6",
            "premium_daily_segment_count": 201,
            "premium_inventory_census_complete": False,
        },
        "checks": [
            {
                "name": "all_final_release_checks",
                "severity": "critical",
                "dimension": "release",
                "passed": True,
            },
            {
                "name": "collection_drift_final_audit_passed",
                "severity": "critical",
                "dimension": "provenance",
                "passed": True,
            },
        ],
        "failure_counts": {"critical": 0, "high": 0, "medium_or_low": 0},
        "relevance_policy": relevance_policy(),
        "inputs": {
            "post_final_release_evidence": str(
                (release_evidence_path or Path("post_final_release_evidence.json")).resolve()
            ),
            "collection_drift_audit": str(Path("collection_drift_final.json").resolve()),
        },
        "collection_drift_audit": {
            "status": "passed",
            "passed": True,
            "path": str(Path("collection_drift_final.json").resolve()),
            "sha256": "D" * 64,
            "mode": "final",
            "overall_status": "PASS",
            "release_gate_passed": True,
            "summary": {
                "structural_failure_count": 0,
                "unresolved_count": 0,
                "effective_final_failure_count": 0,
                "orphan_quarantined_partial_count": 0,
            },
            "errors": [],
        },
        "database_validation": {"status": "inspected", "sha256": authoritative_sha256},
        "preservation": {
            "before": {"status": "passed"},
            "after": {"status": "passed"},
        },
        "source_hash_verification": {
            "before": {"status": "passed"},
            "after": {"status": "passed"},
        },
    }


def release_evidence(authoritative_sha256: str, manifest_sha256: str) -> dict:
    passed = {"status": "passed", "evidence_refs": ["sha256:" + "B" * 64]}
    return {
        "artifact_type": "discord_collection_progress_manifest",
        "source_policy": {"browser_calls_made": 0, "raw_files_modified": 0},
        "release_evidence": {
            "schema_version": "1.0.0",
            "artifact_type": "discord_release_evidence",
            "status": "complete",
            "required_cutoff_utc": release.EXPECTED_END_UTC,
            "generated_at_utc": "2026-07-21T05:30:00Z",
            "outside_sources_used": 0,
            "generator": {
                "local_only": True,
                "browser_calls_made": 0,
                "network_calls_made": 0,
                "raw_files_modified": 0,
            },
            "source_artifacts": [
                {
                    "kind": "cardinal_sqlite_database",
                    "path": "final/authoritative.sqlite",
                    "sha256": authoritative_sha256,
                    "size_bytes": 100,
                },
                {
                    "kind": "corpus_manifest",
                    "path": "final/corpus_coverage_manifest.json",
                    "sha256": manifest_sha256,
                    "size_bytes": 100,
                },
            ],
            "authorized_collection_scope": {
                "status": "passed",
                "source_sha256": release.sha256_file(
                    release.DEFAULT_AUTHORIZED_SCOPE
                ),
                "authorized_parent_ids": sorted(release.AUTHORIZED_PARENT_IDS),
                "premium_message_scope_closure_passed": True,
                "premium_authoritative_source_integrity_passed": True,
                "premium_authoritative_directory": "raw/channel_segments_v2_5",
                "premium_legacy_directory_policy": "preservation_only_not_authoritative",
                "premium_collector_version_required": "2.6",
                "premium_accepted_daily_segment_count": 201,
                "premium_inventory_census_complete": False,
            },
            "scoped_collection_reconciliation": [dict(passed)],
            "residual_reviews": [],
            "reply_resolution": dict(passed),
            "attachments_and_chart_dependence": dict(passed),
            "claim_calibration": dict(passed),
            "pending_items": [],
        },
        "release_review_packets": {
            "artifact_type": "discord_residual_review_packets",
            "review_required": False,
            "packet_count": 0,
            "packets": [],
        },
    }


def analysis_documents() -> dict[str, dict]:
    return {
        "discord_analysis_coverage": {
            "analysis_completeness": "complete",
            "collection_run_status": "complete",
            "gap_count": 0,
            "source_scope": "discord_only",
            "outside_sources_used": 0,
        },
        "discord_analysis_methodology": {
            "source_scope": "discord_only",
            "outside_sources_used": 0,
        },
        "discord_rejection_block_research": {},
        "discord_trade_profiles": {},
        "discord_model_cards": {"models": []},
    }


def create_common_database(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE collection_runs(
          run_id INTEGER PRIMARY KEY,guild_id TEXT,window_start_utc TEXT,
          window_end_utc TEXT,source_scope TEXT,outside_sources_used INTEGER,
          status TEXT
        )
        """
    )
    con.execute(
        "INSERT INTO collection_runs VALUES(1,?,?,?,?,?,?)",
        (
            release.EXPECTED_GUILD_ID,
            release.EXPECTED_START_UTC,
            release.EXPECTED_END_UTC,
            "discord_only",
            0,
            "complete",
        ),
    )
    con.execute(
        """
        CREATE TABLE analysis_runs(
          analysis_run_id INTEGER PRIMARY KEY,source_scope TEXT,
          outside_sources_used INTEGER
        )
        """
    )
    con.execute("INSERT INTO analysis_runs VALUES(1,'discord_only',0)")
    con.execute(
        "CREATE TABLE analysis_documents(document_name TEXT PRIMARY KEY,content_json TEXT NOT NULL)"
    )
    con.executemany(
        "INSERT INTO analysis_documents VALUES(?,?)",
        [
            (name, json.dumps(value, sort_keys=True, separators=(",", ":")))
            for name, value in analysis_documents().items()
        ],
    )
    for table in release.CORE_SHARED_TABLES:
        if table == "analysis_documents":
            continue
        if table == "messages":
            con.execute(
                'CREATE TABLE "messages"(message_id TEXT PRIMARY KEY,channel_id TEXT,content_text TEXT,raw_json TEXT NOT NULL)'
            )
            con.execute(
                'INSERT INTO "messages" VALUES(?,?,?,?)',
                (
                    "1457078514107941056",
                    "1273692573898113076",
                    "fixture scoped message",
                    json.dumps(
                        {
                            "message_id": "1457078514107941056",
                            "content_text": "fixture scoped message",
                        },
                        sort_keys=True,
                    ),
                ),
            )
            wordle = exact_wordle_raw_row()
            con.execute(
                'INSERT INTO "messages" VALUES(?,?,?,?)',
                (
                    wordle["message_id"],
                    "1273692573898113076",
                    wordle["content_text"],
                    json.dumps(wordle, sort_keys=True),
                ),
            )
        elif table == "evidence_items":
            con.execute(
                'CREATE TABLE "evidence_items"(id TEXT PRIMARY KEY,attachment_id TEXT)'
            )
            con.execute(
                'INSERT INTO "evidence_items" VALUES(?,NULL)',
                ("evidence_items:1",),
            )
        elif table == "attachment_extractions":
            con.execute(
                'CREATE TABLE "attachment_extractions"(extraction_id TEXT PRIMARY KEY,attachment_id TEXT)'
            )
        else:
            con.execute(f'CREATE TABLE "{table}"(id TEXT PRIMARY KEY)')
            con.execute(f'INSERT INTO "{table}" VALUES(?)', (f"{table}:1",))
    con.execute(
        """
        CREATE TABLE attachments(
          attachment_id TEXT PRIMARY KEY,message_id TEXT,attachment_id_exact INTEGER,
          filename TEXT,discord_url TEXT,source_channel_id TEXT,relation_type TEXT,
          ownership_status TEXT,ownership_evidence_json TEXT,owned_for_capture INTEGER,
          eligible_for_attachment_evidence INTEGER,mime_type TEXT,media_kind TEXT,
          width INTEGER,height INTEGER,byte_size INTEGER,content_sha256 TEXT,
          local_package_path TEXT,capture_status TEXT,capture_terminal INTEGER,
          capture_attempt_count INTEGER,capture_attempts_json TEXT,
          capture_failure_code TEXT,capture_failure_detail TEXT,extraction_status TEXT,
          extraction_artifacts_json TEXT,archive_manifest_source_file_id TEXT,
          chart_claim_eligible INTEGER,notes TEXT
        )
        """
    )
    con.execute(
        """
        INSERT INTO attachments(
          attachment_id,message_id,attachment_id_exact,filename,discord_url,
          source_channel_id,relation_type,ownership_status,
          ownership_evidence_json,owned_for_capture,
          eligible_for_attachment_evidence,mime_type,media_kind,width,height,
          byte_size,content_sha256,local_package_path,capture_status,
          capture_terminal,capture_attempt_count,capture_attempts_json,
          capture_failure_code,capture_failure_detail,extraction_status,
          extraction_artifacts_json,archive_manifest_source_file_id,
          chart_claim_eligible,notes
        ) VALUES(
          '1364178305632174100','1457078514107941056',1,
          'schizophrenicistalking.gif',
          'https://cdn.discordapp.com/attachments/1278211283656773643/1364178305632174100/schizophrenicistalking.gif',
          '1278211283656773643','embedded_external','non_owned_exact',?,
          0,0,'image/gif','image',NULL,NULL,NULL,NULL,NULL,'metadata_only',
          0,0,'[]',NULL,NULL,'not_attempted','[]',NULL,0,'metadata only'
        )
        """,
        (
            json.dumps(
                {
                    "exact": True,
                    "owner_message_id": "1457078514107941056",
                    "owner_channel_id": "1273692573898113076",
                    "source_channel_id": "1278211283656773643",
                    "dom_relation": "embed_descendant",
                },
                sort_keys=True,
            ),
        ),
    )
    con.execute(
        """
        CREATE VIEW v_discord_only_audit AS
        SELECT run_id AS entity_id FROM collection_runs
        WHERE source_scope<>'discord_only' OR outside_sources_used<>0
        UNION ALL
        SELECT analysis_run_id FROM analysis_runs
        WHERE source_scope<>'discord_only' OR outside_sources_used<>0
        """
    )


def create_full_database(path: Path, manifest_sha256: str) -> None:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    con.executemany(
        "INSERT INTO meta VALUES(?,?)",
        [
            ("schema_version", "2.2.0"),
            ("source_scope", "discord_only"),
            ("outside_sources_used", "0"),
            ("authorized_collection_scope_enabled", "1"),
            (
                "authorized_collection_scope_sha256",
                release.sha256_file(release.DEFAULT_AUTHORIZED_SCOPE),
            ),
            (
                "authorized_parent_container_ids_json",
                json.dumps(sorted(release.AUTHORIZED_PARENT_IDS)),
            ),
        ],
    )
    create_common_database(con)
    con.execute(
        "CREATE TABLE source_artifacts(artifact_id TEXT PRIMARY KEY,sha256 TEXT,source_file TEXT)"
    )
    con.execute(
        "INSERT INTO source_artifacts VALUES('manifest',?,'manifests/corpus_coverage_manifest.json')",
        (manifest_sha256,),
    )
    con.execute("CREATE VIEW v_collection_gaps AS SELECT run_id FROM collection_runs WHERE 0")
    con.commit()
    con.close()


def create_compact_database(path: Path, full_sha256: str) -> None:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=DELETE")
    create_common_database(con)
    con.execute("CREATE TABLE source_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    con.executemany(
        "INSERT INTO source_meta VALUES(?,?)",
        [
            ("schema_version", "2.2.0"),
            ("source_scope", "discord_only"),
            ("outside_sources_used", "0"),
            ("authorized_collection_scope_enabled", "1"),
            (
                "authorized_collection_scope_sha256",
                release.sha256_file(release.DEFAULT_AUTHORIZED_SCOPE),
            ),
        ],
    )
    con.execute("CREATE TABLE llm_manifest(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    con.executemany(
        "INSERT INTO llm_manifest VALUES(?,?)",
        [
            ("source_database_sha256", full_sha256),
            ("source_database_is_authoritative", "1"),
            ("companion_role", "portable_query_snapshot"),
            ("source_scope", "discord_only"),
            ("outside_sources_used", "0"),
        ],
    )
    for table in (
        "query_rejection_blocks",
        "query_qa",
        "query_trade_episodes",
        "query_confluence_profiles",
        "query_models",
        "query_setup_cards",
        "query_collection_gaps",
    ):
        con.execute(f'CREATE TABLE "{table}"(id TEXT PRIMARY KEY)')
    con.commit()
    con.close()


def research_json(full_sha256: str) -> dict:
    return {
        "report_schema_version": "1.0.0",
        "title": "Discord-Only Rejection Block and Trading Model Research",
        "report_type": "technical_evidence_report",
        "claim_scope": "discord_only",
        "outside_sources_used": 0,
        "input_database": {"sha256": full_sha256, "source_scope": "discord_only"},
        "analysis_run": {"source_scope": "discord_only", "outside_sources_used": 0},
        "release_validation": {
            "status": "passed",
            "model_count_within_limit": True,
            "window_matches_requested_local_dates": True,
        },
        "scope_and_coverage": {
            "guild_id": release.EXPECTED_GUILD_ID,
            "window_start_utc": release.EXPECTED_START_UTC,
            "window_end_utc": release.EXPECTED_END_UTC,
            "collection_status": "complete",
            "analysis_completeness": "complete",
            "gap_count": 0,
            "source_scope": "discord_only",
            "outside_sources_used": 0,
        },
        "rejection_blocks": {},
        "trade_profiles": {},
        "model_cards": {"models_emitted": 4, "models": [{}, {}, {}, {}]},
        "question_and_answer_catalog": {},
        "analysis_methodology": {"source_scope": "discord_only", "outside_sources_used": 0},
        "evidence_catalog": {},
    }


class ReleaseFixture:
    def __init__(self, root: Path):
        self.root = root
        self.corpus_manifest = root / "corpus_coverage_manifest.json"
        self.release_evidence = root / "post_final_release_evidence.json"
        self.qa = root / "independent_qa.json"
        self.full = root / "authoritative.sqlite"
        self.compact = root / "compact.sqlite"
        self.report_json = root / "detailed_research.json"
        self.report_md = root / "detailed_research.md"
        self.guide = root / "LLM_HANDOFF_GUIDE.md"
        write_json(self.corpus_manifest, corpus_manifest())
        create_full_database(self.full, release.sha256_file(self.corpus_manifest))
        full_sha = release.sha256_file(self.full)
        write_json(
            self.release_evidence,
            release_evidence(full_sha, release.sha256_file(self.corpus_manifest)),
        )
        write_json(self.qa, qa_report(full_sha, self.release_evidence))
        create_compact_database(self.compact, full_sha)
        compact_sha = release.sha256_file(self.compact)
        write_json(self.report_json, research_json(full_sha))
        self.report_md.write_text(
            "\n".join(
                [
                    "# Discord-Only Rejection Block and Trading Model Research",
                    release.EXPECTED_START_UTC + " through " + release.EXPECTED_END_UTC,
                    "## Rejection block identification and invalidation",
                    "## Strict self-reported win and loss profiles",
                    "## Evidence-backed trading model cards",
                    "## Relevant Discord questions and captured answers",
                    "Source scope: `discord_only`; outside sources used: `0`.",
                    "The report does not browse the web or add outside trading facts.",
                    f"Report input SHA-256: `{full_sha}`.",
                    "",
                ]
            ),
            encoding="utf-8",
            newline="\n",
        )
        self.guide.write_text(
            "\n".join(
                [
                    "# Cardinal / LLM handoff guide — Discord-only trading research",
                    f"Window: {release.EXPECTED_START_DATE} through {release.EXPECTED_END_DATE}.",
                    "Full analyzed SQLite database is authoritative.",
                    "Compact LLM SQLite companion is the first query file.",
                    "Do not add web knowledge or any outside trading facts.",
                    "## Deterministic release binding",
                    f"Guild: `{release.EXPECTED_GUILD_ID}`",
                    f"UTC interval: `[{release.EXPECTED_START_UTC}, {release.EXPECTED_END_UTC})`",
                    "Authoritative: `databases/authoritative_cardinal.sqlite`",
                    "Compact: `databases/compact_llm.sqlite`",
                    "Manifest: `manifests/corpus_coverage_manifest.json`",
                    "QA: `qa/independent_qa_report.json`",
                    "Merged corpus: `NOT_PACKAGED`",
                    f"Full database SHA-256: `{full_sha}`",
                    f"Compact database SHA-256: `{compact_sha}`",
                    "",
                ]
            ),
            encoding="utf-8",
            newline="\n",
        )

    def args(self, output: Path) -> dict:
        return {
            "authoritative_db": self.full,
            "compact_db": self.compact,
            "corpus_manifest": self.corpus_manifest,
            "release_evidence": self.release_evidence,
            "qa_report": self.qa,
            "research_markdown": [self.report_md],
            "research_json": [self.report_json],
            "llm_handoff_guide": self.guide,
            "output_dir": output,
        }


class PackageFinalReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = ReleaseFixture(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def authorized_scope() -> scoped_evidence.authorized_collection_scope.AuthorizedScope:
        return scoped_evidence.authorized_collection_scope.load_validated_scope(
            release.DEFAULT_AUTHORIZED_SCOPE,
            expected_guild_id=release.EXPECTED_GUILD_ID,
            expected_timezone=release.EXPECTED_TIMEZONE,
            expected_start_date=release.EXPECTED_START_DATE,
            expected_end_date=release.EXPECTED_END_DATE,
        )

    def test_builds_deterministic_atomic_package_and_preserves_sources(self) -> None:
        source_paths = [
            self.fixture.full,
            self.fixture.compact,
            self.fixture.corpus_manifest,
            self.fixture.release_evidence,
            self.fixture.qa,
            self.fixture.report_md,
            self.fixture.report_json,
            self.fixture.guide,
        ]
        before = {path: release.sha256_file(path) for path in source_paths}
        first = self.root / "release_one"
        second = self.root / "release_two"
        result = release.package_release(**self.fixture.args(first))
        release.package_release(**self.fixture.args(second))

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["release_status"], "complete")
        self.assertTrue((first / "databases" / "authoritative_cardinal.sqlite").is_file())
        self.assertTrue((first / "databases" / "compact_llm.sqlite").is_file())
        manifest = json.loads((first / "RELEASE_MANIFEST.sha256.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["release_status"], "complete")
        self.assertEqual(manifest["source_scope"], "discord_only")
        self.assertEqual(manifest["outside_sources_used"], 0)
        self.assertTrue(manifest["validation"]["authoritative_sqlite"]["immutable_read_only_open"])
        self.assertTrue(manifest["validation"]["compact_sqlite"]["source_hash_linked"])
        self.assertNotIn("RELEASE_MANIFEST.sha256.json", {row["path"] for row in manifest["files"]})
        for row in manifest["files"]:
            packaged = first / row["path"]
            self.assertEqual(packaged.stat().st_size, row["size_bytes"])
            self.assertEqual(release.sha256_file(packaged), row["sha256"])

        files_one = {
            path.relative_to(first).as_posix(): path.read_bytes()
            for path in first.rglob("*")
            if path.is_file()
        }
        files_two = {
            path.relative_to(second).as_posix(): path.read_bytes()
            for path in second.rglob("*")
            if path.is_file()
        }
        self.assertEqual(files_one, files_two)
        self.assertEqual(before, {path: release.sha256_file(path) for path in source_paths})

    def test_authoritative_database_rederives_executed_command_rows(self) -> None:
        anchor_id = (
            release.reply_provenance_contract
            .EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID
        )
        con = sqlite3.connect(self.fixture.full)
        try:
            raw = json.loads(
                con.execute(
                    "SELECT raw_json FROM messages WHERE message_id=?",
                    (anchor_id,),
                ).fetchone()[0]
            )
            raw["reply_context_dom_class"] = "executedCommand_lookalike"
            con.execute(
                "UPDATE messages SET raw_json=? WHERE message_id=?",
                (json.dumps(raw, sort_keys=True), anchor_id),
            )
            con.commit()
        finally:
            con.close()
        manifest = corpus_manifest()
        with self.assertRaisesRegex(
            release.ReleasePackageError,
            "executed-command row audit failed",
        ):
            release.validate_full_database(
                self.fixture.full,
                manifest_sha256=release.sha256_file(
                    self.fixture.corpus_manifest
                ),
                manifest_payload=manifest,
            )

    def test_attachment_package_gate_rehashes_and_emits_exact_local_files(self) -> None:
        message_id = "1457078514107941056"
        attachment_id = "1457078513864802415"
        channel_id = "1359593949110472777"
        source_corpus = self.root / "attachment_source.json"
        attachment_manifest = self.root / "attachment_archive.json"
        archive_root = self.root / "attachment_archive_root"
        write_json(
            source_corpus,
            {
                "artifact_type": "discord_serverwide_corpus_working",
                "scope": {"guild_id": release.EXPECTED_GUILD_ID},
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
            },
        )
        archive_manifest = attachment_archiver.create_or_resume_manifest(
            source_corpus, attachment_manifest
        )
        entry = archive_manifest["entries"][0]
        body = b"package attachment bytes"
        attachment_archiver.ingest_browser_response(
            archive_manifest,
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
        staging = self.root / "attachment_extraction_staging"
        staging.mkdir()
        (staging / "ocr.txt").write_text(
            "verified extracted chart labels", encoding="utf-8"
        )
        extraction = attachment_archiver.record_extraction(
            archive_manifest,
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
        attachment_archiver.write_json_atomic(attachment_manifest, archive_manifest)
        manifest = corpus_manifest()
        manifest["attachment_archive"] = {
            "provided": True,
            "manifest_sha256": release.sha256_file(attachment_manifest),
            "expected_owned_attachment_count": 1,
            "manifest_attachment_count": 1,
            "entry_set_parity": True,
            "release_gate": {
                "passed": True,
                "literal_release_complete": True,
            },
        }
        validation, destinations = release.validate_attachment_archive_package(
            manifest, attachment_manifest, archive_root
        )
        self.assertTrue(validation["terminal_coverage_complete"])
        self.assertTrue(validation["byte_complete"])
        self.assertEqual(validation["packaged_media_file_count"], 2)
        self.assertIn(
            entry["local_package_path"],
            {relative for _role, _source, relative in destinations},
        )

        archived_path = attachment_archiver.resolve_under(
            archive_root, entry["local_package_path"], label="test archive"
        )
        archived_path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(release.ReleasePackageError, "byte verification failed"):
            release.validate_attachment_archive_package(
                manifest, attachment_manifest, archive_root
            )
        archived_path.write_bytes(body)
        extraction_path = attachment_archiver.resolve_under(
            archive_root,
            extraction["local_package_path"],
            label="test extraction archive",
        )
        extraction_path.write_text("tampered extraction", encoding="utf-8")
        with self.assertRaisesRegex(release.ReleasePackageError, "byte verification failed"):
            release.validate_attachment_archive_package(
                manifest, attachment_manifest, archive_root
            )

    def test_terminal_failed_attachment_is_degraded_and_cannot_be_packaged(self) -> None:
        message_id = "1457078514107941056"
        attachment_id = "1457078513864802415"
        channel_id = "1359593949110472777"
        source_corpus = self.root / "failed_attachment_source.json"
        attachment_manifest = self.root / "failed_attachment_archive.json"
        archive_root = self.root / "failed_attachment_root"
        write_json(
            source_corpus,
            {
                "artifact_type": "discord_serverwide_corpus_working",
                "scope": {"guild_id": release.EXPECTED_GUILD_ID},
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
            },
        )
        archive_manifest = attachment_archiver.create_or_resume_manifest(
            source_corpus, attachment_manifest
        )
        entry = archive_manifest["entries"][0]
        for attempt_number in range(1, 4):
            attachment_archiver.ingest_browser_response(
                archive_manifest,
                {
                    "contract": "discord_attachment_browser_response_v1",
                    "request_id": entry["request_id"],
                    "message_id": message_id,
                    "attachment_id": attachment_id,
                    "final_url": entry["discord_url"],
                    "status": "failed",
                    "terminal": attempt_number == 3,
                    "http_status": 503,
                    "error_code": "discord_request_failed",
                    "error_detail": (
                        f"Discord attachment request attempt {attempt_number} returned HTTP 503"
                    ),
                    "attempted_at_utc": f"2026-07-21T05:0{attempt_number}:00Z",
                    "outside_sources_used": 0,
                    "credentials_or_browser_storage_inspected": False,
                },
                archive_root,
            )
        attachment_archiver.write_json_atomic(
            attachment_manifest, archive_manifest
        )
        malicious_summary = corpus_manifest()
        malicious_summary["attachment_archive"] = {
            "provided": True,
            "manifest_sha256": release.sha256_file(attachment_manifest),
            "expected_owned_attachment_count": 1,
            "manifest_attachment_count": 1,
            "entry_set_parity": True,
            "release_gate": {
                "passed": True,
                "literal_release_complete": True,
            },
        }
        with self.assertRaisesRegex(release.ReleasePackageError, "degraded"):
            release.validate_attachment_archive_package(
                malicious_summary, attachment_manifest, archive_root
            )

    def test_refuses_existing_output_unless_verified_empty_option(self) -> None:
        output = self.root / "existing_release"
        output.mkdir()
        with self.assertRaisesRegex(release.ReleasePackageError, "Output already exists"):
            release.package_release(**self.fixture.args(output))
        args = self.fixture.args(output)
        args["allow_existing_empty_target"] = True
        result = release.package_release(**args)
        self.assertEqual(result["status"], "passed")

    def test_refuses_nonempty_existing_output_even_with_option(self) -> None:
        output = self.root / "existing_release"
        output.mkdir()
        (output / "keep.txt").write_text("do not overwrite", encoding="utf-8")
        args = self.fixture.args(output)
        args["allow_existing_empty_target"] = True
        with self.assertRaisesRegex(release.ReleasePackageError, "not empty"):
            release.package_release(**args)
        self.assertEqual((output / "keep.txt").read_text(encoding="utf-8"), "do not overwrite")

    def test_refuses_partial_manifest_and_never_creates_output(self) -> None:
        payload = corpus_manifest()
        payload["status"] = "partial"
        payload["release_ready"] = False
        write_json(self.fixture.corpus_manifest, payload)
        output = self.root / "release"
        with self.assertRaisesRegex(release.ReleasePackageError, "status must be complete"):
            release.package_release(**self.fixture.args(output))
        self.assertFalse(output.exists())

    def test_refuses_manifest_without_user_authorized_scope(self) -> None:
        payload = corpus_manifest()
        payload.pop("authorized_collection_scope")
        with self.assertRaisesRegex(
            release.ReleasePackageError, "authorized three-channel scope"
        ):
            release.validate_corpus_manifest(payload)

    def test_refuses_scoped_inventory_child_with_outside_parent(self) -> None:
        payload = corpus_manifest()
        payload["inventory"]["containers"].append(
            {
                "container_id": "1456316273788063925",
                "parent_container_id": "1329615478716502097",
                "inventory_layer": "observed_forum_thread",
            }
        )
        with self.assertRaisesRegex(release.ReleasePackageError, "unauthorized parent"):
            release.validate_corpus_manifest(payload)

    def test_refuses_premium_child_with_url_only_parentage_claim(self) -> None:
        payload = corpus_manifest()
        payload["inventory"]["containers"].append(
            {
                "container_id": "1456316273788063925",
                "parent_container_id": "1283941772577472643",
                "name": "outside child",
                "kind": "forum thread",
                "inventory_layer": "observed_forum_thread",
                "identity_provenance": {
                    "evidence": [
                        {
                            "method": "authenticated_discord_thread_url",
                            "thread_url": (
                                "https://discord.com/channels/"
                                f"{release.EXPECTED_GUILD_ID}/1456316273788063925"
                            ),
                            "authenticated": True,
                            "source_scope": "discord_only",
                            "outside_sources_used": False,
                        }
                    ]
                },
            }
        )
        with self.assertRaisesRegex(
            release.ReleasePackageError, "lacks exact parentage proof"
        ):
            release.validate_corpus_manifest(payload)

    def test_refuses_missing_premium_message_scope_closure(self) -> None:
        payload = corpus_manifest()
        payload["authorized_collection_scope"][
            "child_inventory_reconciliation"
        ]["message_scope_closure"]["passed"] = False
        with self.assertRaisesRegex(
            release.ReleasePackageError, "message-scope closure field passed"
        ):
            release.validate_corpus_manifest(payload)

    def test_refuses_premium_legacy_root_as_authoritative(self) -> None:
        payload = corpus_manifest()
        payload["authorized_collection_scope"]["canonical_path_policy"][
            "premium_authoritative_directory"
        ] = "raw/channel_segments"
        with self.assertRaisesRegex(
            release.ReleasePackageError,
            "authoritative source-integrity field premium_authoritative_directory",
        ):
            release.validate_corpus_manifest(payload)

    def test_refuses_incomplete_premium_daily_closure(self) -> None:
        payload = corpus_manifest()
        payload["authorized_collection_scope"]["child_inventory_reconciliation"][
            "message_scope_closure"
        ]["parent_segment_count"] = 200
        with self.assertRaisesRegex(
            release.ReleasePackageError,
            "message-scope closure field parent_segment_count",
        ):
            release.validate_corpus_manifest(payload)

    def test_refuses_false_premium_inventory_census_claim(self) -> None:
        payload = corpus_manifest()
        payload["authorized_collection_scope"]["child_inventory_reconciliation"][
            "inventory_complete"
        ] = True
        with self.assertRaisesRegex(
            release.ReleasePackageError, "lower-bound inventory"
        ):
            release.validate_corpus_manifest(payload)

    def test_scoped_evidence_and_independent_qa_enforce_same_premium_contract(self) -> None:
        scope = self.authorized_scope()
        valid = corpus_manifest()
        scoped_evidence.validate_manifest(valid, scope)
        self.assertEqual(scoped_qa.validate_manifest(valid, scope=scope), [])

        mutations = {
            "legacy_authoritative_root": (
                "canonical_path_policy",
                "premium_authoritative_directory",
                "raw/channel_segments",
            ),
            "wrong_collector_version": (
                "canonical_path_policy",
                "premium_collector_version_required",
                "2.5",
            ),
            "incomplete_daily_routes": (
                "message_scope_closure",
                "parent_segment_count",
                200,
            ),
            "false_inventory_census": (
                "reconciliation",
                "inventory_complete",
                True,
            ),
        }
        for label, (section, key, value) in mutations.items():
            with self.subTest(label=label):
                payload = corpus_manifest()
                authorized = payload["authorized_collection_scope"]
                if section == "canonical_path_policy":
                    authorized["canonical_path_policy"][key] = value
                elif section == "message_scope_closure":
                    authorized["child_inventory_reconciliation"][
                        "message_scope_closure"
                    ][key] = value
                else:
                    authorized["child_inventory_reconciliation"][key] = value
                with self.assertRaises(scoped_evidence.ScopedReleaseEvidenceError):
                    scoped_evidence.validate_manifest(payload, scope)
                self.assertTrue(scoped_qa.validate_manifest(payload, scope=scope))

    def test_refuses_scope_hash_that_is_well_formed_but_not_exact(self) -> None:
        payload = corpus_manifest()
        payload["authorized_collection_scope"]["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            release.ReleasePackageError, "does not match the exact"
        ):
            release.validate_corpus_manifest(payload)

    def test_refuses_failed_qa_gate(self) -> None:
        payload = qa_report(
            release.sha256_file(self.fixture.full), self.fixture.release_evidence
        )
        payload["checks"][0]["passed"] = False
        write_json(self.fixture.qa, payload)
        with self.assertRaisesRegex(release.ReleasePackageError, "failed/skipped"):
            release.package_release(**self.fixture.args(self.root / "release"))

    def test_refuses_qa_without_final_collection_drift_pass(self) -> None:
        payload = qa_report(
            release.sha256_file(self.fixture.full), self.fixture.release_evidence
        )
        payload["collection_drift_audit"]["summary"]["unresolved_count"] = 1
        write_json(self.fixture.qa, payload)
        with self.assertRaisesRegex(release.ReleasePackageError, "zero unresolved drift"):
            release.package_release(**self.fixture.args(self.root / "release"))

    def test_refuses_working_named_artifact(self) -> None:
        working = self.root / "working"
        working.mkdir()
        moved = working / "detailed_research.json"
        shutil.copyfile(self.fixture.report_json, moved)
        args = self.fixture.args(self.root / "release")
        args["research_json"] = [moved]
        with self.assertRaisesRegex(release.ReleasePackageError, "partial/smoke/working"):
            release.package_release(**args)

    def test_refuses_partial_named_release_destination(self) -> None:
        with self.assertRaisesRegex(release.ReleasePackageError, "partial/smoke/working"):
            release.package_release(**self.fixture.args(self.root / "partial_release"))

    def test_refuses_unresolved_handoff_template(self) -> None:
        text = self.fixture.guide.read_text(encoding="utf-8") + "Full: {{FULL_DATABASE_PATH}}\n"
        self.fixture.guide.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(release.ReleasePackageError, "template placeholders"):
            release.package_release(**self.fixture.args(self.root / "release"))

    def test_refuses_handoff_without_portable_package_paths(self) -> None:
        text = self.fixture.guide.read_text(encoding="utf-8")
        self.fixture.guide.write_text(
            text.replace(
                "databases/authoritative_cardinal.sqlite",
                "C:/local/build/final/cardinal_analyzed.sqlite",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(release.ReleasePackageError, "missing"):
            release.package_release(**self.fixture.args(self.root / "release"))

    def test_refuses_compact_database_with_wrong_authoritative_hash(self) -> None:
        con = sqlite3.connect(self.fixture.compact)
        con.execute(
            "UPDATE llm_manifest SET value=? WHERE key='source_database_sha256'",
            ("0" * 64,),
        )
        con.commit()
        con.close()
        with self.assertRaisesRegex(release.ReleasePackageError, "does not link"):
            release.package_release(**self.fixture.args(self.root / "release"))

    def test_refuses_compact_same_count_but_different_message_content(self) -> None:
        con = sqlite3.connect(self.fixture.compact)
        con.execute(
            "UPDATE messages SET content_text='COMPACT_CONTENT_PARITY_CANARY'"
        )
        con.commit()
        con.close()
        # Keep the source hash linkage current so the stronger row-parity gate,
        # rather than the ordinary database-hash gate, is what rejects it.
        full_sha = release.sha256_file(self.fixture.full)
        con = sqlite3.connect(self.fixture.compact)
        con.execute(
            "UPDATE llm_manifest SET value=? WHERE key='source_database_sha256'",
            (full_sha,),
        )
        con.commit()
        con.close()
        with self.assertRaisesRegex(
            release.ReleasePackageError, "identity/content differs"
        ):
            release.package_release(**self.fixture.args(self.root / "release"))

    def test_refuses_absolute_source_paths_in_authoritative_database(self) -> None:
        con = sqlite3.connect(self.fixture.full)
        con.execute(
            "UPDATE source_artifacts SET source_file='C:/private/build/input.json'"
        )
        con.commit()
        con.close()
        with self.assertRaisesRegex(
            release.ReleasePackageError, "nonportable absolute"
        ):
            release.validate_full_database(
                self.fixture.full,
                manifest_sha256=release.sha256_file(
                    self.fixture.corpus_manifest
                ),
                manifest_payload=corpus_manifest(),
            )

    def test_refuses_sqlite_sidecars(self) -> None:
        sidecar = Path(str(self.fixture.full) + "-wal")
        sidecar.write_bytes(b"")
        with self.assertRaisesRegex(release.ReleasePackageError, "sidecar files exist"):
            release.package_release(**self.fixture.args(self.root / "release"))

    def test_refuses_outside_source_provenance(self) -> None:
        payload = qa_report(
            release.sha256_file(self.fixture.full), self.fixture.release_evidence
        )
        payload["scope"]["outside_sources_used"] = True
        write_json(self.fixture.qa, payload)
        with self.assertRaisesRegex(release.ReleasePackageError, "outside_sources_used"):
            release.package_release(**self.fixture.args(self.root / "release"))

    def test_refuses_post_final_release_evidence_with_wrong_database_hash(self) -> None:
        payload = release_evidence(
            "0" * 64, release.sha256_file(self.fixture.corpus_manifest)
        )
        write_json(self.fixture.release_evidence, payload)
        with self.assertRaisesRegex(release.ReleasePackageError, "authoritative database hash"):
            release.package_release(**self.fixture.args(self.root / "release"))

    def test_zero_targeted_release_requires_explicit_zero_review_packets(self) -> None:
        payload = release_evidence(
            release.sha256_file(self.fixture.full),
            release.sha256_file(self.fixture.corpus_manifest),
        )
        del payload["release_review_packets"]
        write_json(self.fixture.release_evidence, payload)
        with self.assertRaisesRegex(
            release.ReleasePackageError, "canonical zero-targeted plan"
        ):
            release.package_release(**self.fixture.args(self.root / "release"))

    def test_zero_targeted_release_rejects_nonzero_review_packet_metadata(self) -> None:
        payload = release_evidence(
            release.sha256_file(self.fixture.full),
            release.sha256_file(self.fixture.corpus_manifest),
        )
        payload["release_review_packets"].update(
            {"review_required": True, "packet_count": 1}
        )
        write_json(self.fixture.release_evidence, payload)
        with self.assertRaisesRegex(
            release.ReleasePackageError, "review_required=false"
        ):
            release.package_release(**self.fixture.args(self.root / "release"))

    def test_empty_residual_reviews_reject_nonzero_targeted_plan(self) -> None:
        payload = release_evidence(
            release.sha256_file(self.fixture.full),
            release.sha256_file(self.fixture.corpus_manifest),
        )
        with self.assertRaisesRegex(
            release.ReleasePackageError, "canonical zero-targeted plan"
        ):
            release.validate_release_evidence(
                payload,
                authoritative_sha256=release.sha256_file(self.fixture.full),
                corpus_manifest_sha256=release.sha256_file(
                    self.fixture.corpus_manifest
                ),
                targeted_channel_count=1,
                authorized_scope_sha256=release.sha256_file(
                    release.DEFAULT_AUTHORIZED_SCOPE
                ),
            )

    def test_refuses_qa_not_bound_to_post_final_release_evidence(self) -> None:
        payload = qa_report(
            release.sha256_file(self.fixture.full), self.root / "different_evidence.json"
        )
        write_json(self.fixture.qa, payload)
        with self.assertRaisesRegex(release.ReleasePackageError, "does not identify"):
            release.package_release(**self.fixture.args(self.root / "release"))


    def test_package_boundary_allows_labeled_external_metadata_but_rejects_bytes(self) -> None:
        path = self.root / "external-attachment-boundary.sqlite"
        create_full_database(path, "a" * 64)
        con = sqlite3.connect(path)
        result = release.validate_attachment_ownership_boundary(
            con, label="fixture database"
        )
        self.assertEqual(result["non_owned_exact_count"], 1)
        con.execute(
            "UPDATE attachments SET local_package_path='attachments/outside.gif'"
        )
        with self.assertRaisesRegex(release.ReleasePackageError, "non-owned"):
            release.validate_attachment_ownership_boundary(
                con, label="fixture database"
            )
        con.close()


if __name__ == "__main__":
    unittest.main()
