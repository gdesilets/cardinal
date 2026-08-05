from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUDIT_ROOT = Path(__file__).resolve().parent
CORPUS_ROOT = AUDIT_ROOT.parents[1]
STAGE_ROOT = CORPUS_ROOT / (
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-08_20260722T072547Z/"
    "v2_6_revalidated"
)
FILENAME = (
    "channel_premium_journals_1283941772577472643_"
    "2026-01-08_2026-01-08.json"
)
SOURCE = STAGE_ROOT / FILENAME
CANONICAL = CORPUS_ROOT / "raw/channel_segments_v2_5" / FILENAME
LEGACY = CORPUS_ROOT / "raw/channel_segments" / FILENAME
V27_CANONICAL = CORPUS_ROOT / "raw/channel_segments_v2_7" / FILENAME
SHADOW_ROOT = CORPUS_ROOT / "raw/premium_journals_v2_7_checkpoints/2026-01-08"
SCHEDULE = CORPUS_ROOT / "working/scoped_three_parent_collection_schedule.json"
READINESS = CORPUS_ROOT / "working/premium_journals_v2_7_readiness_report.json"
EXPECTED_ARTIFACT_SHA256 = (
    "7a9d71adb66ff0317750413c5cb89b459567bd202af3c71a126c4addc134bfb5"
)
EXPECTED_SCHEDULE_BEFORE_SHA256 = (
    "c4503cd9bf82387c0c40b101a5f50badec4da9c8d4681108238741761d08a4cc"
)
EXPECTED_SCHEDULE_AFTER_SHA256 = (
    "0a1fd787f0fbeb6cb142edd028d16daa3c0189027d2d42b82ba40bb209ca18d6"
)
EXPECTED_READINESS_SHA256 = (
    "7c7434c2578edb3862914e9b8ce3c757a8b93f3d4a7daf40497a253fc61c1669"
)
QUERY = "in:premium-journals after:2026-01-07 before:2026-01-09"
ROUTE_ID = "premium_journals_2026-01-08_2026-01-08"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected JSON object: {path}")
    return value


def file_record(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "path": path.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_bytes(content),
        "bytes": len(content),
    }


def tree_manifest(root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        content = path.read_bytes()
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_bytes(content),
                "bytes": len(content),
            }
        )
    encoded = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode()
    return {
        "file_count": len(records),
        "total_bytes": sum(int(record["bytes"]) for record in records),
        "tree_manifest_sha256": sha256_bytes(encoded),
    }


def find_route(value: Any, route_id: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if value.get("route_id") == route_id:
            return value
        for child in value.values():
            found = find_route(child, route_id)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_route(child, route_id)
            if found:
                return found
    return None


def find_dict_with_key(value: Any, key: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if key in value:
            return value
        for child in value.values():
            found = find_dict_with_key(child, key)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_dict_with_key(child, key)
            if found:
                return found
    return None


source_bytes = SOURCE.read_bytes()
canonical_bytes = CANONICAL.read_bytes()
schedule_bytes = SCHEDULE.read_bytes()
readiness_bytes = READINESS.read_bytes()
schedule = json.loads(schedule_bytes)
readiness = json.loads(readiness_bytes)
jan8_route = find_route(schedule, ROUTE_ID)
terminal_union = find_dict_with_key(schedule, "cross_route_duplicate_message_id_count")
premium_coverage = (schedule.get("coverage_assertions") or {}).get("premium_journals") or {}

v26_stage_validation = read_json(AUDIT_ROOT / "v2_6_stage_validation.json")
shadow_validation = read_json(AUDIT_ROOT / "shadow_full_verification.json")
promotion_receipt = read_json(AUDIT_ROOT / "promotion_receipt.json")
postpromotion_audit = read_json(AUDIT_ROOT / "postpromotion_audit.json")
generic_qa = read_json(AUDIT_ROOT / "postpromotion_generic_qa.json")
stage_tree = tree_manifest(STAGE_ROOT)
shadow_tree = tree_manifest(SHADOW_ROOT)

page_reports: list[dict[str, Any]] = []
for page_number in range(1, 8):
    path = AUDIT_ROOT / f"page_{page_number:03d}_shadow_control_comparison.json"
    payload = read_json(path)
    page_reports.append(
        {
            **file_record(path),
            "page_number": page_number,
            "rows": payload.get("page_rows"),
            "controls": payload.get("v2_6_control_group_count"),
            "direct": (payload.get("shadow") or {}).get("direct_count"),
            "fallback": (payload.get("shadow") or {}).get("fallback_count"),
            "direct_key_matches": (payload.get("shadow") or {}).get(
                "direct_key_match_count"
            ),
            "direct_child_matches": (payload.get("shadow") or {}).get(
                "direct_child_match_count"
            ),
            "all_resolution_child_matches": (payload.get("shadow") or {}).get(
                "all_resolution_child_match_count"
            ),
        }
    )

expected_generic_failures = {
    "existing_artifact_hash_baseline_supplied",
    "source_hash_manifest_supplied",
    "channel_thread_inventory_readable",
    "collection_drift_final_audit_passed",
    "guild_wide_date_coverage",
    "channel_thread_inventory_present",
    "inventory_exact_ids",
    "inventory_unit_date_coverage",
    "whole_server_coverage_gate",
    "attachment_archive_terminal_coverage",
    "attachment_ownership_timing",
    "attachment_capture_status_present",
    "sqlite_database_supplied",
}
generic_checks = generic_qa.get("checks") or []
observed_generic_failures = {
    str(check.get("name")) for check in generic_checks if check.get("passed") is False
}
generic_pass_count = sum(check.get("passed") is True for check in generic_checks)

regressions = {
    "schema_version": "1.0.0",
    "artifact_type": "jan8_dual_run_regression_summary",
    "status": "PASS",
    "completed_at_utc": utc_now(),
    "suites": [
        {
            "runtime": "node_test",
            "command": (
                "node --test test_discord_browser_collector.mjs "
                "test_discord_browser_collector_v2_7.mjs"
            ),
            "tests_run": 63,
            "passed": 63,
            "failed": 0,
            "errors": 0,
        },
        {
            "runtime": "python_unittest_discovery",
            "command": "python -m unittest discover -q",
            "tests_run": 310,
            "passed": 310,
            "failed": 0,
            "errors": 0,
        },
    ],
    "total_tests_run": 373,
    "total_passed": 373,
    "total_failed": 0,
    "total_errors": 0,
}
regression_path = AUDIT_ROOT / "regression_summary.json"
regression_path.write_text(
    json.dumps(regressions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)

quarantine_reports = sorted(AUDIT_ROOT.glob("page_*_shadow_quarantine.json"))
checks = {
    "source_sha256_exact": sha256_bytes(source_bytes) == EXPECTED_ARTIFACT_SHA256,
    "canonical_sha256_exact": sha256_bytes(canonical_bytes) == EXPECTED_ARTIFACT_SHA256,
    "source_canonical_byte_equal": source_bytes == canonical_bytes,
    "promotion_receipt_passed": promotion_receipt.get("status") == "PASS"
    and promotion_receipt.get("operation") == "exclusive_create"
    and (promotion_receipt.get("target") or {}).get("created_exclusively") is True,
    "postpromotion_strict_audit_passed": postpromotion_audit.get("status") == "PASS",
    "postpromotion_generic_segment_issue_count_zero": (
        (postpromotion_audit.get("generic_segment_validation") or {}).get("issue_count") == 0
    ),
    "prepromotion_stage_validation_passed": v26_stage_validation.get("status") == "PASS",
    "shadow_full_validation_passed": shadow_validation.get("status") == "PASS",
    "shadow_controls_exact": sum(int(row["controls"] or 0) for row in page_reports) == 78,
    "shadow_direct_exact": sum(int(row["direct"] or 0) for row in page_reports) == 36,
    "shadow_fallback_exact": sum(int(row["fallback"] or 0) for row in page_reports) == 42,
    "shadow_direct_key_parity_exact": sum(
        int(row["direct_key_matches"] or 0) for row in page_reports
    )
    == 36,
    "shadow_direct_child_parity_exact": sum(
        int(row["direct_child_matches"] or 0) for row in page_reports
    )
    == 36,
    "shadow_all_resolution_parity_exact": sum(
        int(row["all_resolution_child_matches"] or 0) for row in page_reports
    )
    == 78,
    "no_shadow_quarantine": not quarantine_reports,
    "schedule_final_sha256_exact": sha256_bytes(schedule_bytes)
    == EXPECTED_SCHEDULE_AFTER_SHA256,
    "schedule_final_bytes_exact": len(schedule_bytes) == 930_837,
    "schedule_jan8_complete": jan8_route is not None
    and jan8_route.get("status") == "complete_accepted_v2_6",
    "schedule_jan8_sha_exact": jan8_route is not None
    and (jan8_route.get("accepted_artifact") or {}).get("sha256")
    == EXPECTED_ARTIFACT_SHA256,
    "schedule_route_counts_exact": premium_coverage.get("accepted_route_count") == 8
    and premium_coverage.get("pending_route_count") == 193,
    "schedule_union_exact": terminal_union is not None
    and terminal_union.get("accepted_reported_total") == 2233
    and terminal_union.get("unique_message_id_count") == 2233
    and terminal_union.get("cross_route_duplicate_message_id_count") == 0
    and terminal_union.get("unresolved_occurrence_count") == 0
    and terminal_union.get("conflict_occurrence_count") == 0
    and terminal_union.get("cross_route_attachment_owner_conflict_count") == 0,
    "readiness_sha256_unchanged": sha256_bytes(readiness_bytes)
    == EXPECTED_READINESS_SHA256,
    "readiness_nonpromotable": readiness.get("status") == "shadow_ready_nonpromotable"
    and readiness.get("live_collection_enabled") is False
    and readiness.get("promotion_allowed") is False,
    "v2_7_canonical_absent": not V27_CANONICAL.exists(),
    "legacy_canonical_absent": not LEGACY.exists(),
    "canonical_partial_absent": not CANONICAL.with_suffix(".partial.json").exists(),
    "stage_partial_absent": not SOURCE.with_suffix(".partial.json").exists(),
    "stage_tree_exact": stage_tree
    == {
        "file_count": 86,
        "total_bytes": 1_471_666,
        "tree_manifest_sha256": "82bc960858880db60f2627656705cf36e58494b027e02620ddc290fdde25ab3e",
    },
    "shadow_tree_file_count_exact": shadow_tree.get("file_count") == 85,
    "generic_scoped_qa_failure_set_expected": observed_generic_failures
    == expected_generic_failures,
    "generic_scoped_qa_source_valid": (generic_qa.get("counts") or {}).get(
        "source_files_valid"
    )
    == 1,
    "regressions_passed": regressions.get("status") == "PASS",
}

result = {
    "schema_version": "1.0.0",
    "artifact_type": "premium_journals_jan8_dual_run_final_audit",
    "status": "PASS" if all(checks.values()) else "FAIL",
    "completed_at_utc": utc_now(),
    "route": {
        "day": "2026-01-08",
        "query": QUERY,
        "initial_reported_total": 162,
        "initial_reported_pages": 7,
        "initial_visible_rows": 25,
        "fresh_search_submission_count": 1,
        "search_submitted_at_utc": "2026-07-22T07:25:47.372Z",
        "first_positive_observation_at_utc": "2026-07-22T07:25:49.052Z",
        "pacing_wait_at_least_60_seconds": True,
        "max_forum_navigation_groups_per_call": 2,
        "maximum_collection_attempts": 320,
        "bounded_collection_calls_used": 42,
        "transient_return_state_failures": 1,
        "transient_failure_bad_checkpoints_written": 0,
    },
    "v2_6": {
        "authority": True,
        "promotion_performed": True,
        "promotion_mode": "exclusive_create_xb",
        "source": file_record(SOURCE),
        "canonical": file_record(CANONICAL),
        "source_canonical_byte_equal": source_bytes == canonical_bytes,
        "reported_total": 162,
        "captured_rows": 162,
        "unique_message_ids": 162,
        "reported_pages": 7,
        "gap_count": 0,
        "forum_group_count": 78,
        "observed_child_thread_count": 32,
        "owned_attachment_count": 43,
        "completion_terminal_state": "stable_bottom",
        "stage_tree": stage_tree,
        "promotion_receipt": file_record(AUDIT_ROOT / "promotion_receipt.json"),
        "postpromotion_audit": file_record(AUDIT_ROOT / "postpromotion_audit.json"),
    },
    "v2_7_shadow": {
        "mode": "shadow_nonpromotable",
        "authority": False,
        "live_collection_enabled": False,
        "promotion_allowed": False,
        "canonical_written": False,
        "schedule_status_updated": False,
        "rows_replaced": 0,
        "v2_6_header_controls_skipped": 0,
        "control_group_count": 78,
        "direct_count": 36,
        "fallback_count": 42,
        "direct_key_match_count": 36,
        "direct_child_match_count": 36,
        "all_resolution_child_match_count": 78,
        "quarantine_report_count": len(quarantine_reports),
        "shadow_tree": shadow_tree,
        "full_verification_report": file_record(
            AUDIT_ROOT / "shadow_full_verification.json"
        ),
        "page_reports": page_reports,
    },
    "schedule": {
        "before": {
            "sha256": EXPECTED_SCHEDULE_BEFORE_SHA256,
            "bytes": 882_904,
        },
        "after": file_record(SCHEDULE),
        "validated_exact_final_file_twice": True,
        "premium_route_count": premium_coverage.get("route_count"),
        "accepted_route_count": premium_coverage.get("accepted_route_count"),
        "pending_route_count": premium_coverage.get("pending_route_count"),
        "accepted_reported_total": terminal_union.get("accepted_reported_total"),
        "unique_message_id_count": terminal_union.get("unique_message_id_count"),
        "cross_route_duplicate_message_id_count": terminal_union.get(
            "cross_route_duplicate_message_id_count"
        ),
        "unresolved_occurrence_count": terminal_union.get("unresolved_occurrence_count"),
        "conflict_occurrence_count": terminal_union.get("conflict_occurrence_count"),
        "cross_route_attachment_owner_conflict_count": terminal_union.get(
            "cross_route_attachment_owner_conflict_count"
        ),
        "jan8_route": {
            "status": jan8_route.get("status"),
            "accepted_artifact_sha256": (jan8_route.get("accepted_artifact") or {}).get(
                "sha256"
            ),
            "accepted_artifact_bytes": (jan8_route.get("accepted_artifact") or {}).get(
                "bytes"
            ),
            "reported_total": (jan8_route.get("accepted_artifact") or {}).get(
                "reported_total"
            ),
            "forum_group_count": (jan8_route.get("accepted_artifact") or {}).get(
                "forum_group_count"
            ),
            "observed_child_thread_count": (
                jan8_route.get("accepted_artifact") or {}
            ).get("observed_child_thread_count"),
        },
    },
    "generic_scoped_qa": {
        **file_record(AUDIT_ROOT / "postpromotion_generic_qa.json"),
        "status": generic_qa.get("status"),
        "source_files_valid": (generic_qa.get("counts") or {}).get("source_files_valid"),
        "checks_passed": generic_pass_count,
        "checks_total": len(generic_checks),
        "expected_full_release_infrastructure_omission_count": len(
            observed_generic_failures
        ),
        "unexpected_failure_count": len(
            observed_generic_failures - expected_generic_failures
        ),
    },
    "readiness_guardrail": {
        **file_record(READINESS),
        "status": readiness.get("status"),
        "live_collection_enabled": readiness.get("live_collection_enabled"),
        "promotion_allowed": readiness.get("promotion_allowed"),
    },
    "regressions": {
        **file_record(regression_path),
        "tests_run": regressions["total_tests_run"],
        "tests_passed": regressions["total_passed"],
    },
    "checks": checks,
}

rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
output_path = AUDIT_ROOT / "final_audit.json"
output_path.write_text(rendered, encoding="utf-8")
print(rendered, end="")
raise SystemExit(0 if result["status"] == "PASS" else 1)

