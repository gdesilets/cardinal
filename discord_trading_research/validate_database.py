from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


EXPECTED_SCHEMA_VERSION = "1.1.0"

REQUIRED_TABLES = {
    "meta",
    "research_runs",
    "collection_coverage",
    "exclusion_stats",
    "channels",
    "messages",
    "message_sources",
    "attachments",
    "message_tags",
    "message_instruments",
    "confluences",
    "message_confluences",
    "outcome_mentions",
    "qa_pairs",
    "rejection_block_findings",
    "rejection_block_finding_evidence",
    "trades",
    "trade_evidence",
    "trade_confluences",
    "outcome_profiles",
    "outcome_profile_confluences",
    "probability_tiers",
    "trading_models",
    "model_rules",
    "model_evidence",
    "research_questions",
    "contradictions",
    "analysis_documents",
    "data_dictionary",
    "messages_fts",
    "v_rejection_block_evidence",
    "v_answered_qa",
    "v_trade_feature_matrix",
    "v_win_loss_confluence_comparison",
    "v_model_cards",
    "v_llm_research_answers",
}


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "database",
        nargs="?",
        type=Path,
        default=Path(__file__).with_name("discord_trading_research.sqlite"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(__file__).with_name("validation_report.json"),
    )
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []

    def record(name: str, passed: bool, observed: Any, expected: Any, severity: str = "error") -> None:
        checks.append(
            {
                "name": name,
                "passed": bool(passed),
                "severity": severity,
                "observed": observed,
                "expected": expected,
            }
        )

    if not args.database.is_file():
        record("database_exists", False, str(args.database), "existing SQLite database")
        report = {
            "database": str(args.database.resolve()),
            "status": "failed",
            "checks": checks,
            "counts": {},
            "hard_failure_count": 1,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    with sqlite3.connect(args.database) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        integrity = scalar(conn, "PRAGMA integrity_check")
        record("sqlite_integrity", integrity == "ok", integrity, "ok")

        foreign_key_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
        record("foreign_keys", not foreign_key_issues, len(foreign_key_issues), 0)

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        missing_tables = sorted(REQUIRED_TABLES - tables)
        record("required_tables", not missing_tables, missing_tables, [])

        schema_version = scalar(conn, "SELECT value FROM meta WHERE key='schema_version'")
        record("schema_version", schema_version == EXPECTED_SCHEMA_VERSION, schema_version, EXPECTED_SCHEMA_VERSION)

        source_scope = scalar(conn, "SELECT source_scope FROM research_runs ORDER BY run_id DESC LIMIT 1")
        record("discord_only_scope", source_scope == "discord_only", source_scope, "discord_only")

        run_status = scalar(conn, "SELECT status FROM research_runs ORDER BY run_id DESC LIMIT 1")
        record(
            "overall_status_reflects_supplemental_gap",
            run_status == "partial",
            run_status,
            "partial (primary complete; broad RB shorthand supplemental search partial)",
        )

        primary_count = scalar(
            conn,
            """
            SELECT messages_seen FROM collection_coverage
            WHERE collection_name='primary_messages'
            ORDER BY coverage_id DESC LIMIT 1
            """,
        )
        record("primary_search_count", primary_count == 1514, primary_count, 1514)

        primary_complete = scalar(
            conn,
            """
            SELECT scan_complete FROM collection_coverage
            WHERE collection_name='primary_messages'
            ORDER BY coverage_id DESC LIMIT 1
            """,
        )
        record("primary_search_complete", primary_complete == 1, primary_complete, 1)

        incomplete_collections = [
            row[0]
            for row in conn.execute(
                "SELECT collection_name FROM collection_coverage WHERE scan_complete=0 ORDER BY collection_name"
            )
        ]
        record(
            "declared_collection_gaps",
            incomplete_collections == ["broad_rb_shorthand_partial_messages"],
            incomplete_collections,
            ["broad_rb_shorthand_partial_messages"],
        )

        coverage_min, coverage_max = conn.execute(
            """
            SELECT earliest_message_utc, latest_message_utc
            FROM collection_coverage
            WHERE collection_name='primary_messages'
            ORDER BY coverage_id DESC LIMIT 1
            """
        ).fetchone()
        coverage_dates_ok = bool(
            coverage_min
            and coverage_max
            and coverage_min >= "2026-07-06"
            and coverage_max < "2026-07-21"
        )
        record(
            "primary_coverage_timestamp_window",
            coverage_dates_ok,
            {"earliest": coverage_min, "latest": coverage_max},
            "2026-07-06 <= timestamp < 2026-07-21",
        )

        primary_min, primary_max = conn.execute(
            """
            SELECT MIN(m.created_at_utc), MAX(m.created_at_utc)
            FROM messages m
            JOIN message_sources s ON s.message_id=m.message_id
            WHERE s.collection_name='primary_messages'
            """
        ).fetchone()
        primary_dates_ok = bool(primary_min and primary_max and primary_min >= "2026-07-06" and primary_max < "2026-07-21")
        record(
            "stored_primary_subset_timestamp_window",
            primary_dates_ok,
            {"earliest": primary_min, "latest": primary_max},
            "2026-07-06 <= timestamp < 2026-07-21",
        )

        message_count = scalar(conn, "SELECT COUNT(*) FROM messages")
        fts_count = scalar(conn, "SELECT COUNT(*) FROM messages_fts")
        record("fts_row_parity", message_count == fts_count, fts_count, message_count)
        fts_id_mismatches = scalar(
            conn,
            """
            SELECT
              (SELECT COUNT(*) FROM (SELECT message_id FROM messages EXCEPT SELECT message_id FROM messages_fts))
              +
              (SELECT COUNT(*) FROM (SELECT message_id FROM messages_fts EXCEPT SELECT message_id FROM messages))
            """,
        )
        record("fts_id_parity", fts_id_mismatches == 0, fts_id_mismatches, 0)

        window_start, window_end = conn.execute(
            "SELECT window_start_utc, window_end_utc FROM research_runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        timestamps_outside_window = scalar(
            conn,
            "SELECT COUNT(*) FROM messages WHERE created_at_utc < ? OR created_at_utc >= ?",
            (window_start, window_end),
        )
        record("stored_messages_within_run_window", timestamps_outside_window == 0, timestamps_outside_window, 0)

        duplicate_ids = scalar(
            conn,
            "SELECT COUNT(*) FROM (SELECT message_id FROM messages GROUP BY message_id HAVING COUNT(*) > 1)",
        )
        record("unique_message_ids", duplicate_ids == 0, duplicate_ids, 0)

        duplicate_source_keys = scalar(
            conn,
            """
            SELECT COUNT(*) FROM (
              SELECT message_id, collection_name, query_text
              FROM message_sources
              GROUP BY message_id, collection_name, query_text
              HAVING COUNT(*) > 1
            )
            """,
        )
        record("unique_message_source_keys", duplicate_source_keys == 0, duplicate_source_keys, 0)

        finding_count = scalar(conn, "SELECT COUNT(*) FROM rejection_block_findings")
        findings_without_evidence = scalar(
            conn,
            """
            SELECT COUNT(*) FROM rejection_block_findings f
            WHERE NOT EXISTS (
              SELECT 1 FROM rejection_block_finding_evidence e WHERE e.finding_id=f.finding_id
            )
            """,
        )
        record("rb_findings_present", bool(finding_count), finding_count, "> 0")
        record("rb_findings_have_evidence", findings_without_evidence == 0, findings_without_evidence, 0)

        model_count = scalar(conn, "SELECT COUNT(*) FROM trading_models")
        record("model_count", 1 <= model_count <= 5, model_count, "1 to 5")
        models_without_rules = scalar(
            conn,
            """
            SELECT COUNT(*) FROM trading_models m
            WHERE NOT EXISTS (SELECT 1 FROM model_rules r WHERE r.model_id=m.model_id)
            """,
        )
        models_without_evidence = scalar(
            conn,
            """
            SELECT COUNT(*) FROM trading_models m
            WHERE NOT EXISTS (SELECT 1 FROM model_evidence e WHERE e.model_id=m.model_id)
            """,
        )
        record("models_have_rules", models_without_rules == 0, models_without_rules, 0)
        record("models_have_evidence", models_without_evidence == 0, models_without_evidence, 0)

        answered_without_summary = scalar(
            conn,
            """
            SELECT COUNT(*) FROM qa_pairs
            WHERE status IN ('answered','partial')
              AND (answer_summary IS NULL OR TRIM(answer_summary)='')
            """,
        )
        record("answered_qa_has_summary", answered_without_summary == 0, answered_without_summary, 0)

        duplicate_linked_qa = scalar(
            conn,
            """
            SELECT COUNT(*) FROM (
              SELECT question_message_id, COALESCE(answer_message_id,'') AS answer_key
              FROM qa_pairs
              WHERE question_message_id IS NOT NULL
              GROUP BY question_message_id, COALESCE(answer_message_id,'')
              HAVING COUNT(*) > 1
            )
            """,
        )
        record("unique_linked_qa_pairs", duplicate_linked_qa == 0, duplicate_linked_qa, 0)

        research_count = scalar(conn, "SELECT COUNT(*) FROM research_questions")
        record("research_answers_present", research_count >= 5, research_count, ">= 5")

        research_json_errors: list[int] = []
        research_orphan_ids: list[dict[str, Any]] = []
        stored_message_ids = {row[0] for row in conn.execute("SELECT message_id FROM messages")}
        for question_id, evidence_json in conn.execute(
            "SELECT research_question_id, evidence_message_ids_json FROM research_questions"
        ):
            try:
                evidence_ids = json.loads(evidence_json)
                if not isinstance(evidence_ids, list):
                    raise ValueError("evidence IDs must be a JSON list")
            except (TypeError, ValueError, json.JSONDecodeError):
                research_json_errors.append(question_id)
                continue
            missing_ids = sorted({str(item) for item in evidence_ids} - stored_message_ids)
            if missing_ids:
                research_orphan_ids.append({"research_question_id": question_id, "missing_ids": missing_ids})
        record("research_evidence_json", not research_json_errors, research_json_errors, [])
        record("research_evidence_ids_resolve", not research_orphan_ids, research_orphan_ids, [])

        known_trades_without_evidence = scalar(
            conn,
            """
            SELECT COUNT(*) FROM trades t
            WHERE outcome NOT IN ('unknown','open','cancelled_no_trade')
              AND NOT EXISTS (SELECT 1 FROM trade_evidence e WHERE e.trade_id=t.trade_id)
            """,
        )
        record("resolved_trades_have_evidence", known_trades_without_evidence == 0, known_trades_without_evidence, 0)

        all_trades_without_evidence = scalar(
            conn,
            """
            SELECT COUNT(*) FROM trades t
            WHERE NOT EXISTS (SELECT 1 FROM trade_evidence e WHERE e.trade_id=t.trade_id)
            """,
        )
        record("all_trade_records_have_evidence", all_trades_without_evidence == 0, all_trades_without_evidence, 0)

        profile_outcomes = {row[0] for row in conn.execute("SELECT outcome FROM outcome_profiles")}
        record(
            "win_and_loss_profiles_present",
            {"win", "loss"}.issubset(profile_outcomes),
            sorted(profile_outcomes),
            "profiles include win and loss",
        )

        probability_tier_count = scalar(conn, "SELECT COUNT(*) FROM probability_tiers")
        record("probability_tiers_present", probability_tier_count >= 2, probability_tier_count, ">= 2")

        probability_count_mismatches = scalar(
            conn,
            """
            SELECT COUNT(*) FROM probability_tiers
            WHERE resolved_count IS NOT NULL
              AND wins IS NOT NULL AND losses IS NOT NULL AND breakevens IS NOT NULL
              AND resolved_count <> wins + losses + breakevens
            """,
        )
        record(
            "probability_tier_count_arithmetic",
            probability_count_mismatches == 0,
            probability_count_mismatches,
            0,
            severity="warning",
        )

        embedded_invalid_json: list[str] = []
        for name, content in conn.execute("SELECT document_name, content_json FROM analysis_documents"):
            try:
                json.loads(content)
            except (TypeError, json.JSONDecodeError):
                embedded_invalid_json.append(name)
        record("embedded_analysis_json", not embedded_invalid_json, embedded_invalid_json, [])
        embedded_documents = {
            row[0] for row in conn.execute("SELECT document_name FROM analysis_documents")
        }
        record(
            "curated_analysis_embedded",
            "curated_analysis" in embedded_documents,
            sorted(embedded_documents),
            "curated_analysis present",
        )

        counts = {
            table: scalar(conn, f"SELECT COUNT(*) FROM {table}")
            for table in (
                "messages",
                "attachments",
                "qa_pairs",
                "rejection_block_findings",
                "rejection_block_finding_evidence",
                "trades",
                "trade_evidence",
                "trade_confluences",
                "outcome_profiles",
                "probability_tiers",
                "trading_models",
                "model_rules",
                "model_evidence",
                "research_questions",
                "contradictions",
            )
        }

    hard_failures = [item for item in checks if not item["passed"] and item["severity"] == "error"]
    report = {
        "database": str(args.database.resolve()),
        "status": "passed" if not hard_failures else "failed",
        "checks": checks,
        "counts": counts,
        "hard_failure_count": len(hard_failures),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not hard_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
