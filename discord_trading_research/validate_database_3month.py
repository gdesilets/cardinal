from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from validate_database import EXPECTED_SCHEMA_VERSION, REQUIRED_TABLES


DEFAULT_DATABASE = Path(__file__).with_name("discord_trading_research_3month.sqlite")
DEFAULT_REPORT = Path(__file__).with_name("validation_report_3month.json")
LEGACY_DATABASE = Path(__file__).with_name("discord_trading_research.sqlite")
EXPECTED_RAW_COLLECTIONS = {
    "primary_messages",
    "server_rejection_phrase_messages",
    "questions_rb_messages",
    "questions_nq_es_messages",
    "broad_rb_shorthand_partial_messages",
    "contextual_qa_messages",
    "instrument_comparison_messages",
}
EXPECTED_COLLECTIONS = EXPECTED_RAW_COLLECTIONS | {"browser_context_followup_messages"}
EXPECTED_EXTENSION_OBJECTS = {
    "merged_message_provenance",
    "v_merged_message_provenance",
    "browser_context_followup_artifacts",
    "browser_followup_contexts",
    "browser_followup_context_messages",
    "v_browser_context_followups",
}
EXPECTED_BROWSER_CONTEXTS = {
    "higher_probability_confluences": ("1495760891348779110", "answered"),
    "es_applicability": ("1496515500832718898", "unresolved"),
    "close_vs_wick_validity": ("1496953286350340196", "unresolved"),
    "nested_rejection_blocks": ("1499025665553338438", "partially_answered"),
    "timeframe_preferences": ("1500203147996434782", "community_answer_only"),
    "timeframe_and_trading_window": ("1506012118317662338", "partially_answered"),
    "cross_market_mitigation": ("1511011249780162691", "unresolved"),
    "liquidity_sweep_probability": ("1522108259962454047", "community_answer_only"),
}
EXPECTED_BROWSER_MESSAGE_IDS = {
    "1495733759528534106",
    "1495733936712847481",
    "1495733972473217114",
    "1495760891348779110",
    "1495765871891710012",
    "1495766310955778108",
    "1495766377678766281",
    "1495766415897264150",
    "1495766459966554123",
    "1495767803356123169",
    "1495768735410032710",
    "1495770154641002516",
    "1495770180188377188",
    "1496515500832718898",
    "1496515526632149096",
    "1496953286350340196",
    "1499025665553338438",
    "1499025711065989260",
    "1500203147996434782",
    "1500203286173581333",
    "1500203408685142208",
    "1500203539115409460",
    "1500203550800613446",
    "1500203570379755600",
    "1500203596388761825",
    "1500203630232342671",
    "1500203750504272024",
    "1500203807060004924",
    "1506012118317662338",
    "1506012133672882186",
    "1511011249780162691",
    "1522108259962454047",
    "1522108347522748476",
    "1522109024399659028",
    "1522109182248222730",
}
EXPECTED_SUPPLEMENTAL_FILES = {
    "instrument_rb_es_2026-04-20_2026-07-06.json",
    "instrument_rb_nq_2026-04-20_2026-07-06.json",
    "questions_nq_es_2026-04-20_2026-07-06.json",
    "questions_rb_2026-04-20_2026-05-03.json",
    "questions_rb_2026-05-04_2026-05-17.json",
    "questions_rb_2026-05-18_2026-05-31.json",
    "questions_rb_2026-06-01_2026-06-14.json",
    "questions_rb_2026-06-15_2026-06-28.json",
    "questions_rb_2026-06-29_2026-07-06.json",
    "rbphrase_2026-04-20_2026-05-03.json",
    "rbphrase_2026-05-04_2026-05-17.json",
    "rbphrase_2026-05-18_2026-05-31.json",
    "rbphrase_2026-06-01_2026-06-14.json",
    "rbphrase_2026-06-15_2026-06-28.json",
    "rbphrase_2026-06-29_2026-07-06.json",
}
EXPECTED_SUPPLEMENTAL_OCCURRENCES_BY_PREFIX = {
    "instrument_rb_es": 45,
    "instrument_rb_nq": 32,
    "questions_nq_es": 21,
    "questions_rb": 927,
    "rbphrase": 1376,
}
EXPECTED_SUPPLEMENTAL_OCCURRENCES_BY_COLLECTION = {
    "instrument_comparison_messages": 98,
    "questions_rb_messages": 927,
    "server_rejection_phrase_messages": 1376,
}
SUPPLEMENTAL_PREFIX_COLLECTIONS = {
    "rbphrase_": "server_rejection_phrase_messages",
    "questions_rb_": "questions_rb_messages",
    "questions_nq_es_": "instrument_comparison_messages",
    "instrument_rb_": "instrument_comparison_messages",
}
EXPECTED_PRIMARY_FILES: set[str] = set()
_primary_cursor = dt.date(2026, 4, 20)
_primary_end = dt.date(2026, 7, 6)
while _primary_cursor <= _primary_end:
    _segment_end = min(_primary_cursor + dt.timedelta(days=1), _primary_end)
    EXPECTED_PRIMARY_FILES.add(
        f"primary_{_primary_cursor.isoformat()}_{_segment_end.isoformat()}.json"
    )
    _primary_cursor = _segment_end + dt.timedelta(days=1)
LEGACY_DOCUMENT_NAMES = {
    "rb_analysis",
    "trade_analysis",
    "model_analysis",
    "curated_analysis",
    "research_summary_markdown",
    "llm_readme_markdown",
}


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def parse_utc(value: str) -> dt.datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = dt.datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(dt.timezone.utc)


def recursive_key_values(value: Any, target_keys: set[str]) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in target_keys:
                found.append(child)
            if isinstance(child, (dict, list)):
                found.extend(recursive_key_values(child, target_keys))
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)):
                found.extend(recursive_key_values(child, target_keys))
    return found


def write_report(
    path: Path,
    database: Path,
    checks: list[dict[str, Any]],
    counts: dict[str, Any],
) -> int:
    errors = [item for item in checks if not item["passed"] and item["severity"] == "error"]
    warnings = [item for item in checks if not item["passed"] and item["severity"] == "warning"]
    status = "failed" if errors else "passed_with_warnings" if warnings else "passed"
    assessment = (
        "Needs revision" if errors else "Share with caveats" if warnings else "Ready to share"
    )
    report = {
        "database": str(database.resolve()),
        "validation_entry_point": str(Path(__file__).resolve()),
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": status,
        "overall_assessment": assessment,
        "checks": checks,
        "counts": counts,
        "hard_failure_count": len(errors),
        "warning_count": len(warnings),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the separate three-month Discord trading research database."
    )
    parser.add_argument("database", nargs="?", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    database = args.database.resolve()
    checks: list[dict[str, Any]] = []
    counts: dict[str, Any] = {}

    def record(
        name: str,
        passed: bool,
        observed: Any,
        expected: Any,
        severity: str = "error",
    ) -> None:
        checks.append(
            {
                "name": name,
                "passed": bool(passed),
                "severity": severity,
                "observed": observed,
                "expected": expected,
            }
        )

    if database == LEGACY_DATABASE.resolve():
        record(
            "separate_three_month_database",
            False,
            str(database),
            "a path other than the existing 14-day database",
        )
        return write_report(args.report, database, checks, counts)
    if not database.is_file():
        record("database_exists", False, str(database), "existing three-month SQLite database")
        return write_report(args.report, database, checks, counts)

    try:
        connection_uri = f"file:{database.as_posix()}?mode=ro&immutable=1"
        with sqlite3.connect(connection_uri, uri=True) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            integrity = scalar(conn, "PRAGMA integrity_check")
            record("sqlite_integrity", integrity == "ok", integrity, "ok")
            foreign_key_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
            record("foreign_keys", not foreign_key_issues, len(foreign_key_issues), 0)

            objects = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                )
            }
            missing_objects = sorted((REQUIRED_TABLES | EXPECTED_EXTENSION_OBJECTS) - objects)
            record("required_schema_objects", not missing_objects, missing_objects, [])
            if missing_objects:
                return write_report(args.report, database, checks, counts)

            meta = dict(conn.execute("SELECT key,value FROM meta"))
            record(
                "schema_version",
                meta.get("schema_version") == EXPECTED_SCHEMA_VERSION,
                meta.get("schema_version"),
                EXPECTED_SCHEMA_VERSION,
            )
            record(
                "three_month_extension_schema_version",
                meta.get("three_month_extension_schema_version") == "1.0.0",
                meta.get("three_month_extension_schema_version"),
                "1.0.0",
            )
            record(
                "three_month_corpus_label",
                meta.get("corpus_label") == "three_month",
                meta.get("corpus_label"),
                "three_month",
            )
            record(
                "discord_only_meta_scope",
                meta.get("source_scope") == "Discord only",
                meta.get("source_scope"),
                "Discord only",
            )
            raw_export = Path(meta.get("raw_export") or "")
            curated_export = Path(meta.get("curated_analysis") or "")
            record(
                "three_month_input_names",
                raw_export.name != "raw_discord_export.json"
                and curated_export.name != "curated_analysis.json",
                {"raw": raw_export.name, "curated": curated_export.name},
                "not the 14-day input filenames",
            )
            browser_input = Path(meta.get("browser_context_followups") or "")
            record(
                "browser_context_input_meta",
                browser_input.name == "browser_context_followups_3month.json"
                and meta.get("browser_context_count") == "8"
                and meta.get("browser_context_message_count") == "35"
                and "not a complete export"
                in str(meta.get("browser_context_completeness_boundary") or "").lower()
                and bool(str(meta.get("browser_context_authority_caution") or "").strip()),
                {
                    "source_file": browser_input.name,
                    "contexts": meta.get("browser_context_count"),
                    "messages": meta.get("browser_context_message_count"),
                    "completeness_boundary": meta.get(
                        "browser_context_completeness_boundary"
                    ),
                    "authority_caution_present": bool(
                        str(meta.get("browser_context_authority_caution") or "").strip()
                    ),
                },
                {
                    "source_file": "browser_context_followups_3month.json",
                    "contexts": "8",
                    "messages": "35",
                    "completeness_boundary": "explicitly not channel-wide",
                    "authority_caution_present": True,
                },
            )
            recorded_database = Path(meta.get("database_file") or "")
            record(
                "recorded_database_path",
                recorded_database.resolve() == database,
                str(recorded_database),
                str(database),
                severity="warning",
            )

            run_rows = conn.execute(
                """
                SELECT schema_version,guild_id,primary_channel_id,window_start_utc,
                       window_end_utc,source_scope,status,limitations
                FROM research_runs
                """
            ).fetchall()
            record("single_research_run", len(run_rows) == 1, len(run_rows), 1)
            if not run_rows:
                return write_report(args.report, database, checks, counts)
            run = run_rows[0]
            record("scoped_guild", run[1] == "1167376964680691732", run[1], "1167376964680691732")
            record(
                "scoped_primary_channel",
                run[2] == "1283941772577472643",
                run[2],
                "1283941772577472643",
            )
            record("discord_only_run_scope", run[5] == "discord_only", run[5], "discord_only")
            try:
                start = parse_utc(run[3])
                end = parse_utc(run[4])
                window_days = (end - start).total_seconds() / 86400
                window_ok = abs(window_days - 92.0) < 0.001
            except (TypeError, ValueError):
                start = end = None
                window_days = None
                window_ok = False
            record("three_month_window_size", window_ok, window_days, 92.0)
            record(
                "three_month_window_boundaries",
                run[3] == "2026-04-20T00:00:00Z" and run[4] == "2026-07-21T00:00:00Z",
                {"start": run[3], "exclusive_end": run[4]},
                {"start": "2026-04-20T00:00:00Z", "exclusive_end": "2026-07-21T00:00:00Z"},
            )
            try:
                meta_days = float(meta.get("requested_days", "nan"))
            except ValueError:
                meta_days = float("nan")
            record(
                "window_duration_meta_matches",
                window_days is not None and abs(meta_days - window_days) < 0.001,
                meta_days,
                window_days,
            )

            coverage = conn.execute(
                """
                SELECT collection_name,scan_complete,messages_seen,
                       earliest_message_utc,latest_message_utc,gap_notes
                FROM collection_coverage
                ORDER BY collection_name
                """
            ).fetchall()
            coverage_names = {row[0] for row in coverage}
            record(
                "expected_collection_coverage",
                coverage_names == EXPECTED_COLLECTIONS,
                sorted(coverage_names),
                sorted(EXPECTED_COLLECTIONS),
            )
            incomplete = sorted(row[0] for row in coverage if not row[1])
            expected_status = "partial" if incomplete else "complete"
            record("run_status_matches_coverage", run[6] == expected_status, run[6], expected_status)
            primary_coverage = next((row for row in coverage if row[0] == "primary_messages"), None)
            record("primary_coverage_present", primary_coverage is not None, bool(primary_coverage), True)
            if primary_coverage:
                record(
                    "primary_messages_seen",
                    primary_coverage[2] > 0,
                    primary_coverage[2],
                    "> 0",
                )
                record(
                    "primary_scan_declared_complete",
                    primary_coverage[1] == 1,
                    primary_coverage[1],
                    1,
                )
            required_complete_coverage = {
                "primary_messages",
                "server_rejection_phrase_messages",
                "questions_rb_messages",
            }
            missing_required_complete = sorted(
                required_complete_coverage - coverage_names
            )
            record(
                "required_complete_coverage_present",
                not missing_required_complete,
                missing_required_complete,
                [],
            )
            incomplete_required_complete = sorted(
                row[0]
                for row in coverage
                if row[0] in required_complete_coverage and not row[1]
            )
            record(
                "required_complete_coverage_complete",
                not incomplete_required_complete,
                incomplete_required_complete,
                [],
            )
            instrument_coverage = next(
                (row for row in coverage if row[0] == "instrument_comparison_messages"), None
            )
            record(
                "instrument_tail_gap_disclosed",
                instrument_coverage is not None
                and instrument_coverage[1] == 0
                and bool(str(instrument_coverage[5] or "").strip()),
                None
                if instrument_coverage is None
                else {
                    "scan_complete": bool(instrument_coverage[1]),
                    "gap_notes": instrument_coverage[5],
                },
                "older-window instrument evidence retained, with the uncaptured July 7-20 tail disclosed",
            )
            browser_coverage = next(
                (row for row in coverage if row[0] == "browser_context_followup_messages"),
                None,
            )
            browser_gap_note = str(browser_coverage[5] or "") if browser_coverage else ""
            record(
                "browser_context_targeted_coverage_disclosed",
                browser_coverage is not None
                and browser_coverage[1] == 0
                and browser_coverage[2] == 35
                and "eight selected permalink contexts" in browser_gap_note.lower()
                and "not a complete export" in browser_gap_note.lower(),
                None
                if browser_coverage is None
                else {
                    "scan_complete": bool(browser_coverage[1]),
                    "messages_seen": browser_coverage[2],
                    "gap_notes": browser_coverage[5],
                },
                {
                    "scan_complete": False,
                    "messages_seen": 35,
                    "scope": "complete for eight selected contexts, not channel-wide",
                },
            )
            record(
                "supplemental_coverage_gaps_disclosed",
                all(row[1] or bool(str(row[5] or "").strip()) for row in coverage),
                [row[0] for row in coverage if not row[1] and not str(row[5] or "").strip()],
                [],
            )

            message_count = scalar(conn, "SELECT COUNT(*) FROM messages")
            counts["messages"] = message_count
            record("messages_present", message_count > 0, message_count, "> 0")
            duplicate_ids = scalar(
                conn,
                "SELECT COUNT(*) FROM (SELECT message_id FROM messages GROUP BY message_id HAVING COUNT(*)>1)",
            )
            record("unique_message_ids", duplicate_ids == 0, duplicate_ids, 0)
            messages_without_sources = scalar(
                conn,
                """
                SELECT COUNT(*) FROM messages m
                WHERE NOT EXISTS (SELECT 1 FROM message_sources s WHERE s.message_id=m.message_id)
                """,
            )
            record("messages_have_source_provenance", messages_without_sources == 0, messages_without_sources, 0)
            if start and end:
                outside = scalar(
                    conn,
                    "SELECT COUNT(*) FROM messages WHERE created_at_utc < ? OR created_at_utc >= ?",
                    (run[3], run[4]),
                )
                record("messages_within_declared_window", outside == 0, outside, 0)

            fts_count = scalar(conn, "SELECT COUNT(*) FROM messages_fts")
            record("fts_row_parity", fts_count == message_count, fts_count, message_count)
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

            provenance_rows = conn.execute(
                """
                SELECT message_id,source_file,source_collection,source_query,
                       segment_start_date,segment_end_date,complete_source,source_json
                FROM merged_message_provenance
                """
            ).fetchall()
            counts["merged_message_provenance"] = len(provenance_rows)
            record(
                "merged_provenance_present",
                len(provenance_rows) >= message_count,
                len(provenance_rows),
                f">= {message_count}",
            )
            missing_merged_provenance = scalar(
                conn,
                """
                SELECT COUNT(*) FROM messages m
                WHERE NOT EXISTS (
                  SELECT 1 FROM merged_message_provenance p WHERE p.message_id=m.message_id
                )
                """,
            )
            record(
                "all_messages_have_merged_provenance",
                missing_merged_provenance == 0,
                missing_merged_provenance,
                0,
            )
            provenance_collections = {row[2] for row in provenance_rows}
            record(
                "provenance_collections_are_declared",
                provenance_collections <= EXPECTED_COLLECTIONS,
                sorted(provenance_collections - EXPECTED_COLLECTIONS),
                [],
            )
            message_source_collections = {
                row[0] for row in conn.execute("SELECT DISTINCT collection_name FROM message_sources")
            }
            record(
                "message_source_collections_are_declared",
                message_source_collections <= EXPECTED_COLLECTIONS,
                sorted(message_source_collections - EXPECTED_COLLECTIONS),
                [],
            )
            message_sources_without_provenance = scalar(
                conn,
                """
                SELECT COUNT(*) FROM (
                  SELECT DISTINCT message_id,collection_name,query_text FROM message_sources
                  EXCEPT
                  SELECT DISTINCT message_id,source_collection,source_query
                  FROM merged_message_provenance
                )
                """,
            )
            provenance_without_message_sources = scalar(
                conn,
                """
                SELECT COUNT(*) FROM (
                  SELECT DISTINCT message_id,source_collection,source_query
                  FROM merged_message_provenance
                  EXCEPT
                  SELECT DISTINCT message_id,collection_name,query_text FROM message_sources
                )
                """,
            )
            record(
                "message_source_query_parity",
                message_sources_without_provenance == 0
                and provenance_without_message_sources == 0,
                {
                    "message_sources_without_provenance": message_sources_without_provenance,
                    "provenance_without_message_sources": provenance_without_message_sources,
                },
                {
                    "message_sources_without_provenance": 0,
                    "provenance_without_message_sources": 0,
                },
            )

            invalid_source_json: list[str] = []
            inconsistent_source_json: list[str] = []
            source_file_names: set[str] = set()
            incomplete_named_sources: list[str] = []
            non_json_source_files: list[str] = []
            query_urls: list[str] = []
            source_file_urls: list[str] = []
            supplemental_mapping_issues: list[dict[str, str]] = []
            primary_mapping_issues: list[dict[str, str]] = []
            inherited_baseline_query_descriptors: set[tuple[str, str]] = set()
            for (
                message_id,
                source_file,
                source_collection,
                source_query,
                segment_start,
                segment_end,
                complete_source,
                source_json,
            ) in provenance_rows:
                source_file = str(source_file or "")
                source_query = str(source_query or "")
                if re.search(r"https?://", source_query, re.I):
                    query_urls.append(source_query)
                if re.match(r"https?://", source_file, re.I):
                    source_file_urls.append(source_file)
                filename = Path(source_file).name if source_file else ""
                if filename:
                    source_file_names.add(filename)
                    if Path(filename).suffix.lower() != ".json":
                        non_json_source_files.append(source_file)
                    if complete_source != 1:
                        incomplete_named_sources.append(source_file)
                    for prefix, expected_collection in SUPPLEMENTAL_PREFIX_COLLECTIONS.items():
                        if filename.startswith(prefix) and source_collection != expected_collection:
                            supplemental_mapping_issues.append(
                                {
                                    "file": filename,
                                    "observed": source_collection,
                                    "expected": expected_collection,
                                }
                            )
                            break
                    if filename.startswith("primary_") and source_collection != "primary_messages":
                        primary_mapping_issues.append(
                            {
                                "file": filename,
                                "observed": source_collection,
                                "expected": "primary_messages",
                            }
                        )
                    if (
                        filename == "raw_discord_export.json"
                        and source_collection != "primary_messages"
                        and "premium-journals" in source_query.lower()
                    ):
                        inherited_baseline_query_descriptors.add(
                            (message_id, source_collection)
                        )
                    if (
                        filename == "browser_context_followups_3month.json"
                        and source_collection != "browser_context_followup_messages"
                    ):
                        supplemental_mapping_issues.append(
                            {
                                "file": filename,
                                "observed": source_collection,
                                "expected": "browser_context_followup_messages",
                            }
                        )
                try:
                    parsed_source = json.loads(source_json)
                    if not isinstance(parsed_source, dict):
                        raise ValueError("source_json is not an object")
                except (TypeError, ValueError, json.JSONDecodeError):
                    invalid_source_json.append(message_id)
                    continue
                expected_fields = {
                    "source_file": source_file,
                    "collection": source_collection,
                    "query": source_query,
                    "segment_start": str(segment_start or ""),
                    "segment_end": str(segment_end or ""),
                }
                observed_fields = {
                    "source_file": str(parsed_source.get("source_file") or ""),
                    "collection": str(parsed_source.get("collection") or ""),
                    "query": str(parsed_source.get("query") or ""),
                    "segment_start": str(parsed_source.get("segment_start") or ""),
                    "segment_end": str(parsed_source.get("segment_end") or ""),
                }
                if observed_fields != expected_fields:
                    inconsistent_source_json.append(message_id)
            record("merged_provenance_source_json", not invalid_source_json, invalid_source_json[:20], [])
            record(
                "merged_provenance_fields_match_source_json",
                not inconsistent_source_json,
                inconsistent_source_json[:20],
                [],
            )
            record("source_queries_have_no_urls", not query_urls, query_urls[:20], [])
            record("source_files_are_local", not source_file_urls, source_file_urls[:20], [])
            record("source_files_are_json", not non_json_source_files, non_json_source_files[:20], [])
            record(
                "named_source_files_declared_complete",
                not incomplete_named_sources,
                incomplete_named_sources[:20],
                [],
            )
            record(
                "supplemental_file_collection_mapping",
                not supplemental_mapping_issues,
                supplemental_mapping_issues[:20],
                [],
            )
            record(
                "primary_file_collection_mapping",
                not primary_mapping_issues,
                primary_mapping_issues[:20],
                [],
            )
            known_source_files = EXPECTED_PRIMARY_FILES | EXPECTED_SUPPLEMENTAL_FILES | {
                "raw_discord_export.json",
                "browser_context_followups_3month.json",
            }
            unexpected_source_files = sorted(source_file_names - known_source_files)
            record(
                "provenance_source_file_inventory",
                not unexpected_source_files,
                unexpected_source_files,
                [],
            )
            counts["provenance_primary_files_represented"] = len(
                source_file_names & EXPECTED_PRIMARY_FILES
            )
            counts["provenance_supplemental_files_represented"] = len(
                source_file_names & EXPECTED_SUPPLEMENTAL_FILES
            )
            record(
                "all_primary_segment_files_represented_in_provenance",
                counts["provenance_primary_files_represented"] == 39,
                counts["provenance_primary_files_represented"],
                39,
            )
            record(
                "all_supplemental_files_represented_in_provenance",
                counts["provenance_supplemental_files_represented"] == 15,
                counts["provenance_supplemental_files_represented"],
                15,
            )
            try:
                declared_inherited_descriptor_count = int(
                    meta.get("inherited_baseline_query_descriptor_count", "0")
                )
            except ValueError:
                declared_inherited_descriptor_count = -1
            source_limitation_note = meta.get("source_metadata_limitation") or ""
            counts["included_inherited_baseline_query_descriptors"] = len(
                inherited_baseline_query_descriptors
            )
            record(
                "inherited_baseline_query_quirk_documented",
                (
                    not inherited_baseline_query_descriptors
                    and declared_inherited_descriptor_count == 0
                )
                or (
                    bool(inherited_baseline_query_descriptors)
                    and declared_inherited_descriptor_count
                    >= len(inherited_baseline_query_descriptors)
                    and "retained losslessly" in source_limitation_note.lower()
                    and source_limitation_note in str(run[7] or "")
                ),
                {
                    "included_descriptor_pairs": len(
                        inherited_baseline_query_descriptors
                    ),
                    "all_raw_descriptors_declared": declared_inherited_descriptor_count,
                    "metadata_note_present": bool(source_limitation_note),
                    "run_limitation_present": source_limitation_note in str(run[7] or "")
                    if source_limitation_note
                    else False,
                },
                "lossless inherited descriptor quirk disclosed in meta and research-run limitations",
            )

            browser_artifact_rows = conn.execute(
                """
                SELECT schema_version,source_file,guild_id,window_start_utc,window_end_utc,
                       captured_contexts,purpose,source_description,outside_sources_used,
                       selection,completeness_boundary,answer_linkage,authority_caution,
                       methodology_json,source_json
                FROM browser_context_followup_artifacts
                """
            ).fetchall()
            record(
                "single_browser_context_artifact",
                len(browser_artifact_rows) == 1,
                len(browser_artifact_rows),
                1,
            )
            browser_source_artifact: dict[str, Any] = {}
            if browser_artifact_rows:
                browser_artifact_row = browser_artifact_rows[0]
                record(
                    "browser_context_artifact_scope",
                    browser_artifact_row[0] == "1.0.0"
                    and Path(str(browser_artifact_row[1])).name
                    == "browser_context_followups_3month.json"
                    and browser_artifact_row[2] == "1167376964680691732"
                    and browser_artifact_row[3] == "2026-04-20T00:00:00Z"
                    and browser_artifact_row[4] == "2026-07-21T00:00:00Z"
                    and browser_artifact_row[5] == 8
                    and browser_artifact_row[8] == 0,
                    {
                        "schema_version": browser_artifact_row[0],
                        "source_file": Path(str(browser_artifact_row[1])).name,
                        "guild_id": browser_artifact_row[2],
                        "window_start": browser_artifact_row[3],
                        "window_end": browser_artifact_row[4],
                        "captured_contexts": browser_artifact_row[5],
                        "outside_sources_used": browser_artifact_row[8],
                    },
                    {
                        "schema_version": "1.0.0",
                        "source_file": "browser_context_followups_3month.json",
                        "guild_id": "1167376964680691732",
                        "window_start": "2026-04-20T00:00:00Z",
                        "window_end": "2026-07-21T00:00:00Z",
                        "captured_contexts": 8,
                        "outside_sources_used": 0,
                    },
                )
                completeness_boundary = str(browser_artifact_row[10] or "")
                authority_caution = str(browser_artifact_row[12] or "")
                record(
                    "browser_context_completeness_boundary",
                    "eight selected permalink contexts" in completeness_boundary.lower()
                    and "not a complete export" in completeness_boundary.lower(),
                    completeness_boundary,
                    "complete only for eight selected permalink contexts; not channel-wide",
                )
                record(
                    "browser_context_authority_boundary",
                    "domme replies" in authority_caution.lower()
                    and "ordinary member replies" in authority_caution.lower()
                    and bool(str(browser_artifact_row[11] or "").strip()),
                    {
                        "authority_caution": authority_caution,
                        "answer_linkage": browser_artifact_row[11],
                    },
                    "verbatim mentor/community authority caution and answer-linkage policy",
                )
                try:
                    methodology_json = json.loads(browser_artifact_row[13])
                    browser_source_artifact = json.loads(browser_artifact_row[14])
                    if not isinstance(methodology_json, dict) or not isinstance(
                        browser_source_artifact, dict
                    ):
                        raise ValueError
                except (TypeError, ValueError, json.JSONDecodeError):
                    methodology_json = {}
                    browser_source_artifact = {}
                record(
                    "browser_context_artifact_json",
                    bool(methodology_json) and bool(browser_source_artifact),
                    {
                        "methodology_object": bool(methodology_json),
                        "source_object": bool(browser_source_artifact),
                    },
                    {"methodology_object": True, "source_object": True},
                )
                record(
                    "browser_context_methodology_preserved",
                    methodology_json.get("outside_sources_used") is False
                    and methodology_json.get("source")
                    == "Authenticated Discord UI in the user's in-app browser"
                    and methodology_json.get("completeness_boundary")
                    == completeness_boundary
                    and methodology_json.get("authority_caution") == authority_caution,
                    methodology_json,
                    "verbatim audited Discord-only methodology",
                )

            browser_context_rows = conn.execute(
                """
                SELECT context_id,target_message_id,status,resolution
                FROM browser_followup_contexts
                ORDER BY context_id
                """
            ).fetchall()
            observed_browser_contexts = {
                str(row[0]): (str(row[1]), str(row[2])) for row in browser_context_rows
            }
            counts["browser_followup_contexts"] = len(browser_context_rows)
            record(
                "browser_contexts_8_of_8",
                observed_browser_contexts == EXPECTED_BROWSER_CONTEXTS
                and all(bool(str(row[3] or "").strip()) for row in browser_context_rows),
                observed_browser_contexts,
                EXPECTED_BROWSER_CONTEXTS,
            )
            observed_status_counts: dict[str, int] = {}
            for _context_id, (_target_id, status) in observed_browser_contexts.items():
                observed_status_counts[status] = observed_status_counts.get(status, 0) + 1
            record(
                "browser_context_status_distribution",
                observed_status_counts
                == {
                    "answered": 1,
                    "partially_answered": 2,
                    "community_answer_only": 2,
                    "unresolved": 3,
                },
                observed_status_counts,
                {
                    "answered": 1,
                    "partially_answered": 2,
                    "community_answer_only": 2,
                    "unresolved": 3,
                },
            )

            browser_message_rows = conn.execute(
                """
                SELECT cm.context_id,cm.message_id,cm.context_source_url,
                       cm.collection_method,cm.author_as_captured,cm.authority_class,
                       cm.is_target_message,m.channel_id,m.permalink
                FROM browser_followup_context_messages cm
                JOIN messages m ON m.message_id=cm.message_id
                ORDER BY cm.message_id
                """
            ).fetchall()
            browser_message_ids = {str(row[1]) for row in browser_message_rows}
            counts["browser_followup_context_messages"] = len(browser_message_rows)
            record(
                "browser_context_messages_35_of_35",
                len(browser_message_rows) == 35
                and browser_message_ids == EXPECTED_BROWSER_MESSAGE_IDS,
                {
                    "row_count": len(browser_message_rows),
                    "unique_ids": len(browser_message_ids),
                    "missing": sorted(EXPECTED_BROWSER_MESSAGE_IDS - browser_message_ids),
                    "unexpected": sorted(browser_message_ids - EXPECTED_BROWSER_MESSAGE_IDS),
                },
                {"row_count": 35, "unique_ids": 35, "missing": [], "unexpected": []},
            )
            invalid_browser_links: list[dict[str, str]] = []
            invalid_browser_methods: list[str] = []
            invalid_browser_authority: list[str] = []
            browser_authority_counts = {"domme": 0, "non_domme": 0}
            target_memberships: set[tuple[str, str]] = set()
            for (
                context_id,
                message_id,
                context_source_url,
                collection_method,
                author,
                authority_class,
                is_target,
                channel_id,
                permalink,
            ) in browser_message_rows:
                expected_context = EXPECTED_BROWSER_CONTEXTS.get(str(context_id))
                if collection_method != "direct_permalink_visible_context":
                    invalid_browser_methods.append(str(message_id))
                expected_authority = (
                    "domme" if str(author or "").strip().casefold() == "domme" else "non_domme"
                )
                if authority_class != expected_authority:
                    invalid_browser_authority.append(str(message_id))
                if authority_class in browser_authority_counts:
                    browser_authority_counts[str(authority_class)] += 1
                expected_context_url = (
                    f"https://discord.com/channels/1167376964680691732/{channel_id}/"
                    f"{expected_context[0]}"
                    if expected_context
                    else ""
                )
                expected_message_url = (
                    f"https://discord.com/channels/1167376964680691732/{channel_id}/{message_id}"
                )
                if (
                    not re.fullmatch(r"\d{15,22}", str(channel_id))
                    or str(context_source_url) != expected_context_url
                    or str(permalink) != expected_message_url
                ):
                    invalid_browser_links.append(
                        {
                            "message_id": str(message_id),
                            "context_source_url": str(context_source_url),
                            "permalink": str(permalink),
                        }
                    )
                if is_target:
                    target_memberships.add((str(context_id), str(message_id)))
            expected_target_memberships = {
                (context_id, target_and_status[0])
                for context_id, target_and_status in EXPECTED_BROWSER_CONTEXTS.items()
            }
            record(
                "browser_context_collection_method",
                not invalid_browser_methods,
                invalid_browser_methods,
                [],
            )
            record(
                "browser_context_authority_classification",
                not invalid_browser_authority
                and browser_authority_counts == {"domme": 4, "non_domme": 31},
                {
                    "invalid_ids": invalid_browser_authority,
                    "counts": browser_authority_counts,
                },
                {"invalid_ids": [], "counts": {"domme": 4, "non_domme": 31}},
            )
            record(
                "browser_context_channel_and_permalinks",
                not invalid_browser_links,
                invalid_browser_links[:20],
                [],
            )
            record(
                "browser_context_target_memberships",
                target_memberships == expected_target_memberships,
                sorted(target_memberships),
                sorted(expected_target_memberships),
            )
            browser_provenance_ids = {
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT message_id FROM merged_message_provenance
                    WHERE source_collection='browser_context_followup_messages'
                      AND source_file LIKE '%browser_context_followups_3month.json'
                    """
                )
            }
            browser_message_source_ids = {
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT message_id FROM message_sources
                    WHERE collection_name='browser_context_followup_messages'
                    """
                )
            }
            record(
                "browser_context_messages_have_distinct_provenance",
                browser_provenance_ids == EXPECTED_BROWSER_MESSAGE_IDS
                and browser_message_source_ids == EXPECTED_BROWSER_MESSAGE_IDS,
                {
                    "provenance_ids": len(browser_provenance_ids),
                    "message_source_ids": len(browser_message_source_ids),
                    "missing_provenance": sorted(
                        EXPECTED_BROWSER_MESSAGE_IDS - browser_provenance_ids
                    ),
                    "missing_message_sources": sorted(
                        EXPECTED_BROWSER_MESSAGE_IDS - browser_message_source_ids
                    ),
                },
                {
                    "provenance_ids": 35,
                    "message_source_ids": 35,
                    "missing_provenance": [],
                    "missing_message_sources": [],
                },
            )
            if browser_source_artifact:
                embedded_browser_ids = {
                    str(row.get("message_id") or "")
                    for row in browser_source_artifact.get("messages", [])
                    if isinstance(row, dict)
                }
                embedded_browser_contexts = {
                    str(row.get("context_id") or ""): (
                        str(row.get("target_message_id") or ""),
                        str(row.get("status") or ""),
                    )
                    for row in browser_source_artifact.get("contexts", [])
                    if isinstance(row, dict)
                }
                record(
                    "browser_context_source_artifact_reconciles",
                    embedded_browser_ids == EXPECTED_BROWSER_MESSAGE_IDS
                    and embedded_browser_contexts == EXPECTED_BROWSER_CONTEXTS,
                    {
                        "messages": len(embedded_browser_ids),
                        "contexts": embedded_browser_contexts,
                    },
                    {"messages": 35, "contexts": EXPECTED_BROWSER_CONTEXTS},
                )

            non_discord_permalinks = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT permalink FROM messages
                    WHERE permalink IS NOT NULL AND TRIM(permalink)<>''
                      AND permalink NOT LIKE 'https://discord.com/channels/%'
                    """
                )
            ]
            record(
                "message_permalinks_are_discord",
                not non_discord_permalinks,
                non_discord_permalinks[:20],
                [],
            )
            discord_attachment_url = re.compile(
                r"^https://(?:[a-z0-9-]+\.)*(?:discord\.com|discordapp\.(?:com|net))/",
                re.I,
            )
            non_discord_attachment_urls = [
                row[0]
                for row in conn.execute(
                    "SELECT discord_url FROM attachments WHERE discord_url IS NOT NULL AND TRIM(discord_url)<>''"
                )
                if not discord_attachment_url.match(str(row[0]))
            ]
            record(
                "attachment_urls_are_discord",
                not non_discord_attachment_urls,
                non_discord_attachment_urls[:20],
                [],
            )

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
            counts["rejection_block_findings"] = finding_count
            record("rb_findings_present", finding_count > 0, finding_count, "> 0")
            record("rb_findings_have_evidence", findings_without_evidence == 0, findings_without_evidence, 0)

            model_count = scalar(conn, "SELECT COUNT(*) FROM trading_models")
            counts["trading_models"] = model_count
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

            trade_count = scalar(conn, "SELECT COUNT(*) FROM trades")
            counts["trades"] = trade_count
            record("trades_present", trade_count > 0, trade_count, "> 0")
            trades_without_evidence = scalar(
                conn,
                """
                SELECT COUNT(*) FROM trades t
                WHERE NOT EXISTS (SELECT 1 FROM trade_evidence e WHERE e.trade_id=t.trade_id)
                """,
            )
            record("trades_have_evidence", trades_without_evidence == 0, trades_without_evidence, 0)

            profile_outcomes = {row[0] for row in conn.execute("SELECT outcome FROM outcome_profiles")}
            counts["outcome_profiles"] = len(profile_outcomes)
            record(
                "win_and_loss_profiles_present",
                {"win", "loss"}.issubset(profile_outcomes),
                sorted(profile_outcomes),
                "profiles include win and loss",
            )
            tier_count = scalar(conn, "SELECT COUNT(*) FROM probability_tiers")
            counts["probability_tiers"] = tier_count
            record("probability_tiers_present", tier_count >= 2, tier_count, ">= 2")

            research_count = scalar(conn, "SELECT COUNT(*) FROM research_questions")
            counts["research_questions"] = research_count
            record("research_answers_present", research_count >= 5, research_count, ">= 5")
            stored_ids = {row[0] for row in conn.execute("SELECT message_id FROM messages")}
            invalid_research_json: list[int] = []
            orphan_research_ids: list[dict[str, Any]] = []
            for question_id, evidence_json in conn.execute(
                "SELECT research_question_id,evidence_message_ids_json FROM research_questions"
            ):
                try:
                    evidence_ids = json.loads(evidence_json)
                    if not isinstance(evidence_ids, list):
                        raise ValueError
                except (TypeError, ValueError, json.JSONDecodeError):
                    invalid_research_json.append(question_id)
                    continue
                missing = sorted({str(item) for item in evidence_ids} - stored_ids)
                if missing:
                    orphan_research_ids.append(
                        {"research_question_id": question_id, "missing_ids": missing}
                    )
            record("research_evidence_json", not invalid_research_json, invalid_research_json, [])
            record("research_evidence_ids_resolve", not orphan_research_ids, orphan_research_ids, [])

            answered_without_summary = scalar(
                conn,
                """
                SELECT COUNT(*) FROM qa_pairs
                WHERE status IN ('answered','partial')
                  AND (answer_summary IS NULL OR TRIM(answer_summary)='')
                """,
            )
            record("answered_qa_has_summary", answered_without_summary == 0, answered_without_summary, 0)

            embedded = {
                row[0]: row[1]
                for row in conn.execute("SELECT document_name,content_json FROM analysis_documents")
            }
            invalid_documents: list[str] = []
            for name, content in embedded.items():
                try:
                    json.loads(content)
                except (TypeError, json.JSONDecodeError):
                    invalid_documents.append(name)
            record("embedded_analysis_json", not invalid_documents, invalid_documents, [])
            raw_merge_metadata: dict[str, Any] = {}
            raw_merge_content = embedded.get("raw_merge_metadata_3month")
            record(
                "raw_merge_metadata_embedded",
                raw_merge_content is not None,
                "present" if raw_merge_content is not None else "missing",
                "present",
            )
            if raw_merge_content is not None:
                try:
                    parsed_metadata = json.loads(raw_merge_content)
                    if not isinstance(parsed_metadata, dict):
                        raise ValueError("embedded raw merge metadata is not an object")
                    raw_merge_metadata = parsed_metadata
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    record(
                        "raw_merge_metadata_is_object",
                        False,
                        str(exc),
                        "valid JSON object",
                    )
                else:
                    record("raw_merge_metadata_is_object", True, "valid object", "valid object")

            if raw_merge_metadata:
                record(
                    "raw_metadata_scope",
                    str(raw_merge_metadata.get("source_scope") or "").replace(" ", "_").lower()
                    == "discord_only",
                    raw_merge_metadata.get("source_scope"),
                    "discord_only",
                )
                record(
                    "raw_metadata_guild",
                    str(raw_merge_metadata.get("guild_id") or "") == "1167376964680691732",
                    raw_merge_metadata.get("guild_id"),
                    "1167376964680691732",
                )
                record(
                    "raw_metadata_primary_channel",
                    str(raw_merge_metadata.get("primary_channel_id") or "")
                    == "1283941772577472643",
                    raw_merge_metadata.get("primary_channel_id"),
                    "1283941772577472643",
                )
                source_scope_values = recursive_key_values(raw_merge_metadata, {"source_scope"})
                non_discord_scopes = [
                    value
                    for value in source_scope_values
                    if str(value or "").replace(" ", "_").lower() != "discord_only"
                ]
                record(
                    "all_embedded_source_scopes_are_discord_only",
                    bool(source_scope_values) and not non_discord_scopes,
                    non_discord_scopes,
                    [],
                )
                metadata_query_urls = [
                    str(value)
                    for value in recursive_key_values(
                        raw_merge_metadata,
                        {"query", "query_text", "search_query", "source_query"},
                    )
                    if isinstance(value, str) and re.search(r"https?://", value, re.I)
                ]
                record(
                    "embedded_source_queries_have_no_urls",
                    not metadata_query_urls,
                    metadata_query_urls[:20],
                    [],
                )

                merge_metadata = raw_merge_metadata.get("merge")
                record(
                    "merge_metadata_present",
                    isinstance(merge_metadata, dict),
                    type(merge_metadata).__name__,
                    "dict",
                )
                if isinstance(merge_metadata, dict):
                    record(
                        "merge_window_dates",
                        merge_metadata.get("requested_window_start_date") == "2026-04-20"
                        and merge_metadata.get("requested_window_end_date") == "2026-07-20",
                        {
                            "start": merge_metadata.get("requested_window_start_date"),
                            "inclusive_end": merge_metadata.get("requested_window_end_date"),
                        },
                        {"start": "2026-04-20", "inclusive_end": "2026-07-20"},
                    )
                    record(
                        "baseline_tail_complete",
                        merge_metadata.get("baseline_covers_requested_tail") is True,
                        merge_metadata.get("baseline_covers_requested_tail"),
                        True,
                    )
                    segment_counts = {
                        "expected": merge_metadata.get("expected_segments"),
                        "completed": merge_metadata.get("completed_segments"),
                        "partial": merge_metadata.get("partial_segments"),
                        "missing": merge_metadata.get("missing_segments"),
                        "invalid": merge_metadata.get("invalid_segments"),
                    }
                    record(
                        "primary_segment_coverage_39_of_39",
                        segment_counts
                        == {
                            "expected": 39,
                            "completed": 39,
                            "partial": 0,
                            "missing": 0,
                            "invalid": 0,
                        }
                        and merge_metadata.get("all_expected_segments_complete") is True,
                        {
                            **segment_counts,
                            "all_complete": merge_metadata.get(
                                "all_expected_segments_complete"
                            ),
                        },
                        {
                            "expected": 39,
                            "completed": 39,
                            "partial": 0,
                            "missing": 0,
                            "invalid": 0,
                            "all_complete": True,
                        },
                    )
                    completed_primary_files = merge_metadata.get(
                        "completed_segment_files_ingested"
                    )
                    completed_primary_names = {
                        Path(str(item)).name for item in completed_primary_files
                    } if isinstance(completed_primary_files, list) else set()
                    record(
                        "primary_segment_file_inventory",
                        completed_primary_names == EXPECTED_PRIMARY_FILES
                        and isinstance(completed_primary_files, list)
                        and len(completed_primary_files) == 39,
                        {
                            "count": len(completed_primary_files)
                            if isinstance(completed_primary_files, list)
                            else None,
                            "missing": sorted(EXPECTED_PRIMARY_FILES - completed_primary_names),
                            "unexpected": sorted(completed_primary_names - EXPECTED_PRIMARY_FILES),
                        },
                        {"count": 39, "missing": [], "unexpected": []},
                    )

                    supplemental_files = merge_metadata.get(
                        "supplemental_validated_files_ingested"
                    )
                    supplemental_names = {
                        Path(str(item)).name for item in supplemental_files
                    } if isinstance(supplemental_files, list) else set()
                    record(
                        "supplemental_file_inventory_15",
                        merge_metadata.get("supplemental_validated_file_count") == 15
                        and isinstance(supplemental_files, list)
                        and len(supplemental_files) == 15
                        and supplemental_names == EXPECTED_SUPPLEMENTAL_FILES,
                        {
                            "declared_count": merge_metadata.get(
                                "supplemental_validated_file_count"
                            ),
                            "listed_count": len(supplemental_files)
                            if isinstance(supplemental_files, list)
                            else None,
                            "missing": sorted(EXPECTED_SUPPLEMENTAL_FILES - supplemental_names),
                            "unexpected": sorted(supplemental_names - EXPECTED_SUPPLEMENTAL_FILES),
                        },
                        {
                            "declared_count": 15,
                            "listed_count": 15,
                            "missing": [],
                            "unexpected": [],
                        },
                    )
                    record(
                        "supplemental_message_occurrences",
                        merge_metadata.get("supplemental_message_occurrences") == 2401,
                        merge_metadata.get("supplemental_message_occurrences"),
                        2401,
                    )
                    record(
                        "supplemental_occurrences_by_prefix",
                        merge_metadata.get("supplemental_occurrences_by_prefix")
                        == EXPECTED_SUPPLEMENTAL_OCCURRENCES_BY_PREFIX,
                        merge_metadata.get("supplemental_occurrences_by_prefix"),
                        EXPECTED_SUPPLEMENTAL_OCCURRENCES_BY_PREFIX,
                    )
                    record(
                        "supplemental_occurrences_by_collection",
                        merge_metadata.get("supplemental_occurrences_by_collection")
                        == EXPECTED_SUPPLEMENTAL_OCCURRENCES_BY_COLLECTION,
                        merge_metadata.get("supplemental_occurrences_by_collection"),
                        EXPECTED_SUPPLEMENTAL_OCCURRENCES_BY_COLLECTION,
                    )
                    supplemental_coverage = merge_metadata.get("supplemental_coverage")
                    record(
                        "supplemental_coverage_present",
                        isinstance(supplemental_coverage, dict),
                        type(supplemental_coverage).__name__,
                        "dict",
                    )
                    if isinstance(supplemental_coverage, dict):
                        prefix_coverage = supplemental_coverage.get("prefix_coverage")
                        prefix_rows = (
                            [item for item in prefix_coverage if isinstance(item, dict)]
                            if isinstance(prefix_coverage, list)
                            else []
                        )
                        expected_prefixes = set(EXPECTED_SUPPLEMENTAL_OCCURRENCES_BY_PREFIX)
                        observed_prefixes = {
                            str(item.get("prefix") or "") for item in prefix_rows
                        }
                        incomplete_prefixes = sorted(
                            str(item.get("prefix") or "")
                            for item in prefix_rows
                            if item.get("date_coverage_complete") is not True
                            or bool(item.get("missing_date_ranges"))
                        )
                        record(
                            "all_supplemental_prefixes_complete",
                            supplemental_coverage.get("all_prefixes_complete") is True
                            and observed_prefixes == expected_prefixes
                            and not incomplete_prefixes,
                            {
                                "all_prefixes_complete": supplemental_coverage.get(
                                    "all_prefixes_complete"
                                ),
                                "prefixes": sorted(observed_prefixes),
                                "incomplete": incomplete_prefixes,
                            },
                            {
                                "all_prefixes_complete": True,
                                "prefixes": sorted(expected_prefixes),
                                "incomplete": [],
                            },
                        )
                    merge_collection_coverage = merge_metadata.get("collection_coverage")
                    merge_collection_map = {
                        str(item.get("collection_name") or ""): item
                        for item in merge_collection_coverage
                        if isinstance(item, dict)
                    } if isinstance(merge_collection_coverage, list) else {}
                    merge_collection_names = {
                        name for name in merge_collection_map if name
                    }
                    record(
                        "merge_collection_coverage_inventory",
                        merge_collection_names == EXPECTED_RAW_COLLECTIONS,
                        sorted(merge_collection_names),
                        sorted(EXPECTED_RAW_COLLECTIONS),
                    )
                    database_coverage_map = {row[0]: row for row in coverage}
                    collection_coverage_mismatches: list[dict[str, Any]] = []
                    for collection_name in sorted(EXPECTED_RAW_COLLECTIONS):
                        declared_row = merge_collection_map.get(collection_name)
                        database_row = database_coverage_map.get(collection_name)
                        if declared_row is None or database_row is None:
                            continue
                        declared_count = declared_row.get("declared_messages_seen")
                        declared_complete = declared_row.get("scan_complete")
                        if (
                            declared_count != database_row[2]
                            or bool(declared_complete) != bool(database_row[1])
                        ):
                            collection_coverage_mismatches.append(
                                {
                                    "collection": collection_name,
                                    "metadata_count": declared_count,
                                    "database_count": database_row[2],
                                    "metadata_complete": declared_complete,
                                    "database_complete": bool(database_row[1]),
                                }
                            )
                    record(
                        "database_coverage_matches_merge_metadata",
                        not collection_coverage_mismatches,
                        collection_coverage_mismatches,
                        [],
                    )
                    supplemental_directory = str(
                        merge_metadata.get("supplemental_directory") or ""
                    )
                    record(
                        "supplemental_directory_is_local",
                        bool(supplemental_directory)
                        and not re.match(r"https?://", supplemental_directory, re.I),
                        supplemental_directory,
                        "non-empty local directory path",
                    )
            browser_document_content = embedded.get("browser_context_followups_3month")
            browser_document: dict[str, Any] = {}
            if browser_document_content is not None:
                try:
                    parsed_browser_document = json.loads(browser_document_content)
                    if not isinstance(parsed_browser_document, dict):
                        raise ValueError
                    browser_document = parsed_browser_document
                except (TypeError, ValueError, json.JSONDecodeError):
                    browser_document = {}
            record(
                "browser_context_artifact_embedded",
                bool(browser_document)
                and browser_document == browser_source_artifact
                and {
                    str(row.get("message_id") or "")
                    for row in browser_document.get("messages", [])
                    if isinstance(row, dict)
                }
                == EXPECTED_BROWSER_MESSAGE_IDS,
                {
                    "present": browser_document_content is not None,
                    "valid_object": bool(browser_document),
                    "matches_extension_table": bool(browser_document)
                    and browser_document == browser_source_artifact,
                    "message_count": len(browser_document.get("messages", []))
                    if browser_document
                    else 0,
                },
                {
                    "present": True,
                    "valid_object": True,
                    "matches_extension_table": True,
                    "message_count": 35,
                },
            )
            record(
                "three_month_curated_analysis_embedded",
                "curated_analysis_3month" in embedded,
                sorted(embedded),
                "curated_analysis_3month present",
            )
            legacy_embedded = sorted(LEGACY_DOCUMENT_NAMES & set(embedded))
            record("no_14_day_analysis_documents", not legacy_embedded, legacy_embedded, [])
            optional_missing = sorted(
                {"rb_analysis_3month", "trade_analysis_3month", "model_analysis_3month"}
                - set(embedded)
            )
            record(
                "three_month_supporting_analyses_embedded",
                not optional_missing,
                optional_missing,
                [],
                severity="warning",
            )

            for table in (
                "attachments",
                "qa_pairs",
                "trade_evidence",
                "trade_confluences",
                "model_rules",
                "model_evidence",
                "contradictions",
                "analysis_documents",
                "browser_context_followup_artifacts",
                "browser_followup_contexts",
                "browser_followup_context_messages",
            ):
                counts[table] = scalar(conn, f"SELECT COUNT(*) FROM {table}")
            counts["coverage"] = [
                {
                    "collection_name": row[0],
                    "scan_complete": bool(row[1]),
                    "messages_seen": row[2],
                    "earliest_message_utc": row[3],
                    "latest_message_utc": row[4],
                    "gap_notes": row[5],
                }
                for row in coverage
            ]
    except (sqlite3.Error, ValueError) as exc:
        record("validator_execution", False, str(exc), "validator completes without SQL/schema errors")

    return write_report(args.report, database, checks, counts)


if __name__ == "__main__":
    raise SystemExit(main())
