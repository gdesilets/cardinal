from __future__ import annotations

import hashlib
import json
import pathlib


AUDIT_DIR = pathlib.Path(__file__).resolve().parent
CORPUS_ROOT = AUDIT_DIR.parents[1]
ISOLATED_ROOT = CORPUS_ROOT.parents[1] / "jan7_prepromotion_isolated_20260722T070934Z"
SOURCE_ROOT = CORPUS_ROOT / (
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-07_20260722T055743Z/"
    "v2_6_revalidated"
)
FILENAME = (
    "channel_premium_journals_1283941772577472643_"
    "2026-01-07_2026-01-07.json"
)
SOURCE_PATH = SOURCE_ROOT / FILENAME
TARGET = CORPUS_ROOT / "raw/channel_segments_v2_5" / FILENAME
LEGACY = CORPUS_ROOT / "raw/channel_segments" / FILENAME
MIRROR = ISOLATED_ROOT / "raw/channel_segments_v2_5" / FILENAME
EXPECTED_SHA256 = "19486bee534ac150e76d70cc2f070ba07735c77ded7846e9ad090c026a81cb72"
EXPECTED_BYTES = 2_919_929
EXPECTED_TREE_SHA256 = "9a1c9ecb843e216cb2b8e11b5fb9cb610601e0f46392e7866e15980447104423"


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


def tree_manifest(root: pathlib.Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "file_count": len(rows),
        "total_bytes": sum(int(row["bytes"]) for row in rows),
        "tree_manifest_sha256": hashlib.sha256(encoded).hexdigest(),
    }


independent = read_json(AUDIT_DIR / "independent_audit.json")
specialized = read_json(AUDIT_DIR / "specialized_audit.json")
manifest = read_json(AUDIT_DIR / "isolated_coverage_manifest.json")
generic = read_json(AUDIT_DIR / "generic_qa_validation.json")
regressions = read_json(AUDIT_DIR / "regression_summary.json")
errors: list[str] = []


def require(condition: bool, error: str) -> None:
    if not condition:
        errors.append(error)


require(independent.get("status") == "PASS", "independent_audit_failed")
require(
    all(value == 0 for value in (independent.get("error_counts") or {}).values()),
    "independent_error_count_nonzero",
)
immutable = independent.get("immutability") or {}
require(immutable.get("source_tree_unchanged") is True, "independent_source_tree_changed")
require(not any(immutable.get(key) for key in (
    "canonical_exists_before",
    "canonical_exists_after",
    "legacy_exists_before",
    "legacy_exists_after",
    "partial_exists_before",
    "partial_exists_after",
    "legacy_partial_exists_before",
    "legacy_partial_exists_after",
)), "independent_target_or_partial_present")

require(specialized.get("status") == "PASS", "specialized_audit_failed")
require(specialized.get("terminal_valid") is True, "specialized_terminal_invalid")
require(specialized.get("unresolved_count") == 0, "specialized_unresolved_nonzero")
require(specialized.get("conflict_count") == 0, "specialized_conflict_nonzero")
accepted = specialized.get("accepted_artifact") or {}
for key, expected in {
    "sha256": EXPECTED_SHA256,
    "bytes": EXPECTED_BYTES,
    "collector_version": "2.6",
    "reported_total": 390,
    "captured_rows": 390,
    "reported_pages": 16,
    "completion_terminal_state": "stable_bottom",
    "forum_group_count": 181,
    "forum_navigation_unresolved_count": 0,
    "thread_channel_id_conflict_count": 0,
    "forbidden_selected_thread_source_count": 0,
    "full_qa_passed": True,
}.items():
    require(accepted.get(key) == expected, f"specialized_{key}_mismatch")
for section in (
    "forum_membership_integrity",
    "forum_navigation_artifact_integrity",
    "timestamp_scope_integrity",
    "reply_provenance_integrity",
    "attachment_provenance_integrity",
):
    require((specialized.get(section) or {}).get("passed") is True, f"{section}_failed")
require(specialized.get("source_file_count") == 198, "specialized_source_file_count_mismatch")
require(specialized.get("message_id_count") == 390, "specialized_message_count_mismatch")
require(specialized.get("row_child_binding_count") == 390, "specialized_binding_count_mismatch")
require(specialized.get("owned_attachment_id_count") == 75, "specialized_attachment_count_mismatch")

counts = manifest.get("counts") or {}
for key, expected in {
    "channel_segments": 1,
    "source_occurrences": 390,
    "unique_messages": 390,
    "duplicate_occurrences_over_unique_messages": 0,
    "messages_with_field_variants": 0,
    "valid_unique_message_ids": 390,
    "invalid_message_id_occurrences": 0,
    "messages_with_quarantined_occurrences": 0,
    "fully_quarantined_messages": 0,
}.items():
    require(counts.get(key) == expected, f"isolated_manifest_{key}_mismatch")
require(manifest.get("field_conflicts") == [], "isolated_manifest_field_conflicts")
coverage = (manifest.get("coverage") or {}).get("summary") or {}
require(coverage.get("segment_count") == 1, "isolated_segment_count_mismatch")
require(coverage.get("complete_segment_count") == 1, "isolated_complete_count_mismatch")
require(coverage.get("partial_or_failed_segment_count") == 0, "isolated_partial_segment_present")
scope = manifest.get("authorized_collection_scope") or {}
policy = scope.get("canonical_path_policy") or {}
for key, expected in {
    "passed": True,
    "required_roots_supplied_exactly_once": True,
    "legacy_premium_authoritative_occurrence_count": 0,
    "accepted_premium_segment_count": 1,
    "accepted_premium_daily_date_count": 1,
    "duplicate_premium_daily_dates": [],
    "premium_collector_version_mismatch_count": 0,
    "premium_provenance_missing_segment_count": 0,
    "invalid_premium_authoritative_file_count": 0,
    "accepted_premium_bound_source_file_count": 198,
}.items():
    require(policy.get(key) == expected, f"isolated_policy_{key}_mismatch")
included = scope.get("included") or {}
excluded = scope.get("excluded") or {}
require(included.get("segment_file_count") == 1, "isolated_included_file_count_mismatch")
require(included.get("occurrence_count") == 390, "isolated_included_occurrence_mismatch")
require(included.get("unique_valid_message_id_count") == 390, "isolated_included_unique_mismatch")
require(excluded.get("file_count") == 0, "isolated_scope_excluded_files")
require(excluded.get("ambiguous_fail_closed_file_count") == 0, "isolated_scope_ambiguous_files")
message_scope = (scope.get("child_inventory_reconciliation") or {}).get("message_scope_closure") or {}
for key, expected in {
    "complete_calendar_day_count": 1,
    "parent_segment_count": 1,
    "invalid_daily_partition_segment_count": 0,
    "duplicate_daily_date_count": 0,
    "incomplete_segment_count": 0,
    "terminal_evidence_invalid_segment_count": 0,
    "captured_parent_forum_occurrence_count": 390,
    "unresolved_row_binding_count": 0,
    "row_binding_conflict_count": 0,
    "observed_message_bearing_child_count": 46,
    "observed_child_outside_derived_union_count": 0,
}.items():
    require(message_scope.get(key) == expected, f"isolated_message_scope_{key}_mismatch")
require((manifest.get("timestamp_scope_integrity") or {}).get("passed") is True, "isolated_timestamp_failed")
require((manifest.get("timestamp_scope_integrity") or {}).get("unresolved_message_count") == 0, "isolated_timestamp_unresolved")
require((manifest.get("executed_command_reply_provenance_integrity") or {}).get("passed") is True, "isolated_reply_failed")

generic_counts = generic.get("counts") or {}
for key, expected in {
    "source_files_discovered": 1,
    "source_files_valid": 1,
    "complete_source_files": 1,
    "partial_source_files": 0,
    "diagnostic_message_occurrences": 390,
    "complete_source_unique_message_ids": 390,
}.items():
    require(generic_counts.get(key) == expected, f"generic_{key}_mismatch")
duplicates = generic.get("duplicates_and_edits") or {}
for key in ("duplicated_message_ids", "timestamp_conflicts", "unresolved_content_variants"):
    require(duplicates.get(key) == 0, f"generic_{key}_nonzero")
reply = generic.get("replies") or {}
for key in (
    "undocumented_reply_context_without_id",
    "unresolved_reply_targets",
    "reply_target_scope_failures",
    "reply_resolution_contract_failures",
):
    require(reply.get(key) == 0, f"generic_{key}_nonzero")
attachments = generic.get("attachments") or {}
for key in ("multiple_owner_attachments", "non_owned_attachment_occurrences", "invalid_ownership_evidence"):
    require(attachments.get(key) == 0, f"generic_{key}_nonzero")
timestamp = generic.get("timestamp_scope_integrity") or {}
require(timestamp.get("passed") is True, "generic_timestamp_failed")
for key in ("unresolved_message_count", "invalid_sidecar_count", "unused_revalidation_record_count"):
    require(timestamp.get(key) == 0, f"generic_{key}_nonzero")
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
    require(checks.get(name) is True, f"generic_artifact_check_failed:{name}")
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
require(observed_generic_failures == expected_generic_failures, "generic_unexpected_failure_set")

require(regressions.get("status") == "PASS", "regressions_failed")
require(regressions.get("v2_7_involved") is False, "v2_7_was_involved")
require(regressions.get("total_tests_run") == 177, "regression_count_mismatch")
require(regressions.get("total_passed") == 177, "regression_pass_count_mismatch")
require(regressions.get("total_failed") == 0, "regression_failure_nonzero")
require(regressions.get("total_errors") == 0, "regression_error_nonzero")

source_tree = tree_manifest(SOURCE_ROOT)
require(source_tree == {
    "file_count": 198,
    "total_bytes": 3_480_038,
    "tree_manifest_sha256": EXPECTED_TREE_SHA256,
}, "final_source_tree_manifest_mismatch")
for label, path in (("source", SOURCE_PATH), ("mirror", MIRROR)):
    require(path.is_file(), f"{label}_artifact_missing")
    if path.is_file():
        require(path.stat().st_size == EXPECTED_BYTES, f"{label}_artifact_bytes_mismatch")
        require(sha256_file(path) == EXPECTED_SHA256, f"{label}_artifact_sha256_mismatch")
for label, path in (
    ("canonical", TARGET),
    ("canonical_partial", TARGET.with_suffix(".partial.json")),
    ("legacy", LEGACY),
    ("legacy_partial", LEGACY.with_suffix(".partial.json")),
):
    require(not path.exists(), f"{label}_preexists")

input_paths = [
    AUDIT_DIR / "independent_audit.json",
    AUDIT_DIR / "specialized_audit.json",
    AUDIT_DIR / "isolated_raw_corpus.json",
    AUDIT_DIR / "isolated_coverage_manifest.json",
    AUDIT_DIR / "generic_qa_validation.json",
    AUDIT_DIR / "regression_summary.json",
]
result = {
    "status": "PASS" if not errors else "FAIL",
    "promotion_authorized": not errors,
    "expected_stage_sha256": EXPECTED_SHA256,
    "stage_tree": source_tree,
    "v2_7_involved": False,
    "artifact_level_gate_count": len(artifact_checks),
    "generic_expected_non_artifact_failure_count": len(expected_generic_failures),
    "regression_tests": {"passed": 177, "run": 177},
    "target_absence": {
        "canonical": not TARGET.exists(),
        "canonical_partial": not TARGET.with_suffix(".partial.json").exists(),
        "legacy": not LEGACY.exists(),
        "legacy_partial": not LEGACY.with_suffix(".partial.json").exists(),
    },
    "input_artifacts": [
        {
            "path": path.relative_to(CORPUS_ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in input_paths
    ],
    "errors": errors,
}
rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
(AUDIT_DIR / "prepromotion_gate.json").write_text(rendered, encoding="utf-8")
print(rendered, end="")
raise SystemExit(0 if not errors else 1)
