from __future__ import annotations

import collections
import hashlib
import json
import pathlib
import sys


AUDIT_DIR = pathlib.Path(__file__).resolve().parent
CORPUS_ROOT = AUDIT_DIR.parents[1]
SCHEDULE = CORPUS_ROOT / "working/scoped_three_parent_collection_schedule.json"
SOURCE = CORPUS_ROOT / (
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-07_20260722T055743Z/"
    "v2_6_revalidated/"
    "channel_premium_journals_1283941772577472643_2026-01-07_2026-01-07.json"
)
TARGET = CORPUS_ROOT / (
    "raw/channel_segments_v2_5/"
    "channel_premium_journals_1283941772577472643_2026-01-07_2026-01-07.json"
)
LEGACY = CORPUS_ROOT / (
    "raw/channel_segments/"
    "channel_premium_journals_1283941772577472643_2026-01-07_2026-01-07.json"
)
EXPECTED_SHA256 = "19486bee534ac150e76d70cc2f070ba07735c77ded7846e9ad090c026a81cb72"
EXPECTED_BYTES = 2_919_929
SCHEDULE_BEFORE = {
    "sha256": "b7df54b5a70cd41ea0436f8d128333233bc30593c1b77111d067916df514f4d4",
    "bytes": 780_136,
    "generated_at_utc": "2026-07-22T07:07:25.582444Z",
}

sys.path.insert(0, str(CORPUS_ROOT))
import validate_scoped_three_parent_schedule as schedule_validator  # noqa: E402


def read_json(path: pathlib.Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON root is not an object: {path}")
    return value


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


errors: list[str] = []


def require(condition: bool, error: str) -> None:
    if not condition:
        errors.append(error)


promotion = read_json(AUDIT_DIR / "promotion_receipt.json")
post = read_json(AUDIT_DIR / "postpromotion_audit.json")
generic = read_json(AUDIT_DIR / "postpromotion_generic_qa.json")
regressions = read_json(AUDIT_DIR / "regression_summary.json")
schedule = read_json(SCHEDULE)

require(promotion.get("status") == "PASS", "promotion_receipt_failed")
require(promotion.get("operation") == "exclusive_create", "promotion_not_exclusive")
require(promotion.get("write_mode") == "xb", "promotion_write_mode_not_exclusive")
require(promotion.get("v2_7_involved") is False, "promotion_v2_7_involved")
require(post.get("status") == "PASS", "postpromotion_contract_failed")
require(post.get("source_equals_canonical") is True, "source_canonical_bytes_differ")
require(post.get("errors") == [], "postpromotion_errors_nonempty")
require(regressions.get("status") == "PASS", "regression_summary_failed")
require(regressions.get("total_passed") == 177, "regression_pass_count_mismatch")
require(regressions.get("total_tests_run") == 177, "regression_run_count_mismatch")
require(regressions.get("v2_7_involved") is False, "regression_v2_7_involved")

for label, path in (("source", SOURCE), ("canonical", TARGET)):
    require(path.is_file(), f"{label}_missing")
    if path.is_file():
        require(path.stat().st_size == EXPECTED_BYTES, f"{label}_bytes_mismatch")
        require(sha256_file(path) == EXPECTED_SHA256, f"{label}_sha256_mismatch")
require(not TARGET.with_suffix(".partial.json").exists(), "canonical_partial_exists")
require(not LEGACY.exists(), "legacy_exists")
require(not LEGACY.with_suffix(".partial.json").exists(), "legacy_partial_exists")

checks = {str(row.get("name")): row.get("passed") for row in generic.get("checks") or []}
artifact_checks = {
    "invalid_scope",
    "invalid_totals_pages_indices",
    "invalid_message_identity",
    "timestamp_scope_not_exact",
    "invalid_timestamp_scope_revalidation",
    "invalid_executed_command_reply_provenance",
    "container_scope_mismatch",
    "missing_exact_container_id",
    "invalid_premium_forum_provenance",
    "invalid_message_timestamp",
    "snowflake_timestamp_mismatch",
    "timestamp_scope_integrity_content_hash_bound",
    "duplicate_occurrences_preserved",
    "timestamp_variants_resolved",
    "reply_context_has_explicit_target",
    "reply_target_unavailability_documented",
    "reply_resolution_status_boolean_consistent",
    "reply_targets_resolved_or_documented",
    "reply_targets_have_owned_exact_scope",
    "reply_temporal_order",
    "attachment_ownership_evidence_exact",
    "attachment_ownership_timing",
}
for name in artifact_checks:
    require(checks.get(name) is True, f"post_generic_artifact_check_failed:{name}")
expected_generic_failures = {
    "existing_artifact_hash_baseline_supplied",
    "source_hash_manifest_supplied",
    "inventory_window_contract",
    "inventory_reported_counts_valid",
    "collection_drift_final_audit_passed",
    "guild_wide_date_coverage",
    "inventory_unit_date_coverage",
    "whole_server_coverage_gate",
    "attachment_archive_terminal_coverage",
    "attachment_capture_status_present",
    "sqlite_database_supplied",
}
observed_generic_failures = {name for name, passed in checks.items() if passed is not True}
require(observed_generic_failures == expected_generic_failures, "post_generic_unexpected_failure_set")
generic_counts = generic.get("counts") or {}
require(generic_counts.get("source_files_valid") == 1, "post_generic_source_invalid")
require(generic_counts.get("complete_source_unique_message_ids") == 390, "post_generic_unique_count_mismatch")
duplicates = generic.get("duplicates_and_edits") or {}
require(duplicates.get("duplicated_message_ids") == 0, "post_generic_duplicate_ids")
require(duplicates.get("timestamp_conflicts") == 0, "post_generic_timestamp_conflicts")
require(duplicates.get("unresolved_content_variants") == 0, "post_generic_content_variants")

schedule_errors = schedule_validator.validate_schedule(CORPUS_ROOT, SCHEDULE)
require(schedule_errors == [], "schedule_validation_failed")
require(schedule.get("status") == "active_scoped_schedule", "schedule_status_mismatch")
require("v2_7" not in SCHEDULE.read_text(encoding="utf-8"), "schedule_contains_v2_7")
policy = schedule.get("premium_journals_acceptance_policy") or {}
require(policy.get("collector_version_required") == "2.6", "schedule_not_v2_6")
routes = (schedule.get("routes") or {}).get("premium_journals") or []
status_counts = collections.Counter(str(row.get("status") or "") for row in routes)
require(len(routes) == 201, "premium_schedule_route_count_mismatch")
require(status_counts == {"complete_accepted_v2_6": 7, "pending_fresh_v2_6_capture": 194}, "premium_schedule_status_counts_mismatch")
jan7 = [row for row in routes if row.get("start") == "2026-01-07" and row.get("end") == "2026-01-07"]
require(len(jan7) == 1, "jan7_schedule_route_count_mismatch")
jan7_route = jan7[0] if len(jan7) == 1 else {}
accepted = jan7_route.get("accepted_artifact") or {}
for key, expected in {
    "status": "complete_accepted_v2_6",
    "query": "in:premium-journals after:2026-01-06 before:2026-01-08",
}.items():
    require(jan7_route.get(key) == expected, f"jan7_schedule_{key}_mismatch")
for key, expected in {
    "sha256": EXPECTED_SHA256,
    "bytes": EXPECTED_BYTES,
    "collector_version": "2.6",
    "reported_total": 390,
    "captured_rows": 390,
    "reported_pages": 16,
    "completion_terminal_state": "stable_bottom",
    "message_id_set_sha256": "ad9f828f1ce92cdb471ba5189239cf2498f8ccf5ae390674cb51c2c6893b2f56",
    "observed_child_thread_count": 46,
    "observed_child_thread_id_set_sha256": "33da777fa3cce1481ac47dfa49259223171b1f7e4dbe12f93fdffc1656521199",
    "forum_group_count": 181,
    "forum_navigation_unresolved_count": 0,
    "thread_channel_id_conflict_count": 0,
    "forbidden_selected_thread_source_count": 0,
    "full_qa_passed": True,
}.items():
    require(accepted.get(key) == expected, f"jan7_accepted_{key}_mismatch")
terminal = (schedule.get("premium_thread_census") or {}).get("full_window_union_terminal_evidence") or {}
for key, expected in {
    "accepted_daily_route_count": 7,
    "pending_daily_route_count": 194,
    "accepted_reported_total": 2071,
    "unique_message_id_count": 2071,
    "cross_route_duplicate_message_id_count": 0,
    "unresolved_occurrence_count": 0,
    "conflict_occurrence_count": 0,
    "cross_route_attachment_owner_conflict_count": 0,
}.items():
    require(terminal.get(key) == expected, f"schedule_terminal_{key}_mismatch")

schedule_after = {
    "sha256": sha256_file(SCHEDULE),
    "bytes": SCHEDULE.stat().st_size,
    "generated_at_utc": schedule.get("generated_at_utc"),
    "validator_error_count": len(schedule_errors),
    "valid": not schedule_errors,
}
report_paths = [
    AUDIT_DIR / "prepromotion_gate.json",
    AUDIT_DIR / "promotion_receipt.json",
    AUDIT_DIR / "postpromotion_audit.json",
    AUDIT_DIR / "postpromotion_generic_qa.json",
    AUDIT_DIR / "regression_summary.json",
    SCHEDULE,
]
result = {
    "status": "PASS" if not errors else "FAIL",
    "promotion_complete": not errors,
    "v2_7_involved": False,
    "canonical": {
        "path": TARGET.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_file(TARGET),
        "bytes": TARGET.stat().st_size,
    },
    "source": {
        "path": SOURCE.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_file(SOURCE),
        "bytes": SOURCE.stat().st_size,
        "equals_canonical": SOURCE.read_bytes() == TARGET.read_bytes(),
    },
    "stage_tree": post.get("stage_tree_after"),
    "legacy_absent": not LEGACY.exists(),
    "legacy_partial_absent": not LEGACY.with_suffix(".partial.json").exists(),
    "canonical_partial_absent": not TARGET.with_suffix(".partial.json").exists(),
    "prepromotion": {
        "artifact_level_generic_checks_passed": len(artifact_checks),
        "regressions_passed": regressions.get("total_passed"),
        "regressions_run": regressions.get("total_tests_run"),
    },
    "postpromotion": {
        "specialized_contract_passed": post.get("status") == "PASS",
        "generic_artifact_checks_passed": len(artifact_checks),
        "generic_expected_non_artifact_failure_count": len(expected_generic_failures),
    },
    "schedule": {
        "before": SCHEDULE_BEFORE,
        "after": schedule_after,
        "premium_route_count": len(routes),
        "premium_route_status_counts": dict(sorted(status_counts.items())),
        "accepted_reported_total": terminal.get("accepted_reported_total"),
        "unique_message_id_count": terminal.get("unique_message_id_count"),
        "cross_route_duplicate_message_id_count": terminal.get("cross_route_duplicate_message_id_count"),
        "unresolved_occurrence_count": terminal.get("unresolved_occurrence_count"),
        "conflict_occurrence_count": terminal.get("conflict_occurrence_count"),
        "cross_route_attachment_owner_conflict_count": terminal.get("cross_route_attachment_owner_conflict_count"),
        "jan7_route": {
            "route_id": jan7_route.get("route_id"),
            "status": jan7_route.get("status"),
            "accepted_artifact_sha256": accepted.get("sha256"),
            "accepted_artifact_bytes": accepted.get("bytes"),
            "reported_total": accepted.get("reported_total"),
            "captured_rows": accepted.get("captured_rows"),
            "reported_pages": accepted.get("reported_pages"),
            "forum_group_count": accepted.get("forum_group_count"),
            "observed_child_thread_count": accepted.get("observed_child_thread_count"),
        },
    },
    "audit_artifacts": [
        {
            "path": path.relative_to(CORPUS_ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in report_paths
    ],
    "errors": errors,
}
rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
(AUDIT_DIR / "final_audit.json").write_text(rendered, encoding="utf-8")
print(rendered, end="")
raise SystemExit(0 if not errors else 1)
