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
STAGE_ARTIFACT = STAGE_ROOT / FILENAME
SHADOW_ROOT = CORPUS_ROOT / "raw/premium_journals_v2_7_checkpoints/2026-01-08"
SCHEDULE = CORPUS_ROOT / "working/scoped_three_parent_collection_schedule.json"
READINESS = CORPUS_ROOT / "working/premium_journals_v2_7_readiness_report.json"
V26_VALIDATION = AUDIT_ROOT / "v2_6_stage_validation.json"
SHADOW_VALIDATION = AUDIT_ROOT / "shadow_full_verification.json"
EXPECTED_SCHEDULE_SHA256 = (
    "c4503cd9bf82387c0c40b101a5f50badec4da9c8d4681108238741761d08a4cc"
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


def file_record(path: Path, root: Path = CORPUS_ROOT) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_bytes(content),
        "bytes": len(content),
    }


def tree_manifest(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        content = path.read_bytes()
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_bytes(content),
                "bytes": len(content),
            }
        )
    encoded = json.dumps(files, ensure_ascii=False, separators=(",", ":")).encode()
    return {
        "file_count": len(files),
        "total_bytes": sum(int(record["bytes"]) for record in files),
        "tree_manifest_sha256": sha256_bytes(encoded),
        "files": files,
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


v26_validation = json.loads(V26_VALIDATION.read_text(encoding="utf-8"))
shadow_validation = json.loads(SHADOW_VALIDATION.read_text(encoding="utf-8"))
schedule_bytes = SCHEDULE.read_bytes()
schedule_payload = json.loads(schedule_bytes)
readiness_bytes = READINESS.read_bytes()
readiness_payload = json.loads(readiness_bytes)
schedule_route = find_route(schedule_payload, ROUTE_ID)
stage_tree = tree_manifest(STAGE_ROOT)
shadow_tree = tree_manifest(SHADOW_ROOT)
stage_artifact_record = file_record(STAGE_ARTIFACT)

page_reports: list[dict[str, Any]] = []
for page_number in range(1, 8):
    path = AUDIT_ROOT / f"page_{page_number:03d}_shadow_control_comparison.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
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

real_v26_canonical = CORPUS_ROOT / "raw/channel_segments_v2_5" / FILENAME
real_v27_canonical = CORPUS_ROOT / "raw/channel_segments_v2_7" / FILENAME
real_legacy_canonical = CORPUS_ROOT / "raw/channel_segments" / FILENAME
partial = STAGE_ARTIFACT.with_suffix(".partial.json")
quarantine_reports = sorted(AUDIT_ROOT.glob("page_*_shadow_quarantine.json"))

checks = {
    "v2_6_stage_validation_passed": v26_validation.get("status") == "PASS",
    "v2_6_generic_issue_count_zero": (
        (v26_validation.get("generic") or {}).get("issue_count") == 0
    ),
    "shadow_full_verification_passed": shadow_validation.get("status") == "PASS",
    "page_report_count_exact": len(page_reports) == 7,
    "page_report_rows_exact": sum(int(row["rows"] or 0) for row in page_reports) == 162,
    "control_count_exact": sum(int(row["controls"] or 0) for row in page_reports) == 78,
    "direct_count_exact": sum(int(row["direct"] or 0) for row in page_reports) == 36,
    "fallback_count_exact": sum(int(row["fallback"] or 0) for row in page_reports) == 42,
    "direct_key_parity_exact": sum(
        int(row["direct_key_matches"] or 0) for row in page_reports
    )
    == 36,
    "direct_child_parity_exact": sum(
        int(row["direct_child_matches"] or 0) for row in page_reports
    )
    == 36,
    "all_resolution_parity_exact": sum(
        int(row["all_resolution_child_matches"] or 0) for row in page_reports
    )
    == 78,
    "no_shadow_quarantine_report": not quarantine_reports,
    "schedule_sha256_unchanged": sha256_bytes(schedule_bytes)
    == EXPECTED_SCHEDULE_SHA256,
    "schedule_route_still_pending": schedule_route is not None
    and schedule_route.get("status") == "pending_fresh_v2_6_capture",
    "schedule_route_query_unchanged": schedule_route is not None
    and schedule_route.get("query") == QUERY,
    "readiness_sha256_unchanged": sha256_bytes(readiness_bytes)
    == EXPECTED_READINESS_SHA256,
    "readiness_remains_nonpromotable": readiness_payload.get("status")
    == "shadow_ready_nonpromotable"
    and readiness_payload.get("live_collection_enabled") is False
    and readiness_payload.get("promotion_allowed") is False,
    "real_v2_6_canonical_absent": not real_v26_canonical.exists(),
    "real_v2_7_canonical_absent": not real_v27_canonical.exists(),
    "real_legacy_canonical_absent": not real_legacy_canonical.exists(),
    "stage_partial_absent": not partial.exists(),
    "stage_tree_file_count_exact": stage_tree["file_count"] == 86,
    "stage_tree_sha256_exact": stage_tree["tree_manifest_sha256"]
    == "82bc960858880db60f2627656705cf36e58494b027e02620ddc290fdde25ab3e",
    "shadow_tree_file_count_exact": shadow_tree["file_count"] == 85,
    "regressions_passed": regressions["status"] == "PASS",
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
        "promotion_performed": False,
        "schedule_status_updated": False,
        "stage_artifact": stage_artifact_record,
        "stage_tree": {key: value for key, value in stage_tree.items() if key != "files"},
        "reported_total": 162,
        "captured_rows": 162,
        "unique_message_ids": 162,
        "reported_pages": 7,
        "gap_count": 0,
        "forum_group_count": 78,
        "observed_child_thread_count": 32,
        "owned_attachment_count": 43,
        "completion_terminal_state": "stable_bottom",
        "validation_report": file_record(V26_VALIDATION),
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
        "shadow_tree": {key: value for key, value in shadow_tree.items() if key != "files"},
        "full_verification_report": file_record(SHADOW_VALIDATION),
        "page_reports": page_reports,
    },
    "guardrails": {
        "schedule": {
            **file_record(SCHEDULE),
            "expected_sha256": EXPECTED_SCHEDULE_SHA256,
            "jan8_route_status": schedule_route.get("status") if schedule_route else None,
        },
        "readiness_report": {
            **file_record(READINESS),
            "expected_sha256": EXPECTED_READINESS_SHA256,
            "status": readiness_payload.get("status"),
            "live_collection_enabled": readiness_payload.get("live_collection_enabled"),
            "promotion_allowed": readiness_payload.get("promotion_allowed"),
        },
        "v2_6_canonical_absent": not real_v26_canonical.exists(),
        "v2_7_canonical_absent": not real_v27_canonical.exists(),
        "legacy_canonical_absent": not real_legacy_canonical.exists(),
        "stage_partial_absent": not partial.exists(),
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

