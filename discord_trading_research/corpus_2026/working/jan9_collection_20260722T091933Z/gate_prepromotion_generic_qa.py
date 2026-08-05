from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "prepromotion_revalidated_generic_qa.json"
TARGET = ROOT / "prepromotion_revalidated_generic_qa_gate.json"
ALLOWED_ISOLATED_INFRASTRUCTURE_FAILURES = {
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
    "attachment_capture_status_present",
    "sqlite_database_supplied",
}


payload = json.loads(SOURCE.read_text(encoding="utf-8"))
checks = {
    str(check.get("name")): check
    for check in payload.get("checks") or []
    if isinstance(check, dict)
}
failed = {name for name, check in checks.items() if check.get("passed") is not True}
unexpected = sorted(failed - ALLOWED_ISOLATED_INFRASTRUCTURE_FAILURES)
missing_expected = sorted(ALLOWED_ISOLATED_INFRASTRUCTURE_FAILURES - failed)
counts = payload.get("counts") or {}
count_gate = {
    "source_files_discovered": counts.get("source_files_discovered") == 1,
    "source_files_valid": counts.get("source_files_valid") == 1,
    "complete_source_files": counts.get("complete_source_files") == 1,
    "partial_source_files": counts.get("partial_source_files") == 0,
    "diagnostic_message_occurrences": counts.get("diagnostic_message_occurrences")
    == 194,
    "complete_source_unique_message_ids": counts.get(
        "complete_source_unique_message_ids"
    )
    == 194,
}
artifact_gate = not unexpected and not missing_expected and all(count_gate.values())
result = {
    "status": "PASS" if artifact_gate else "FAIL",
    "interpretation": (
        "All artifact-specific generic QA checks passed; the only failures are "
        "expected full-release infrastructure omissions in a one-file isolated audit."
    ),
    "generic_report_status": payload.get("status"),
    "generic_report_overall_assessment": payload.get("overall_assessment"),
    "allowed_isolated_infrastructure_failures": sorted(
        ALLOWED_ISOLATED_INFRASTRUCTURE_FAILURES
    ),
    "observed_failed_checks": sorted(failed),
    "unexpected_failed_checks": unexpected,
    "missing_expected_isolated_failures": missing_expected,
    "count_gate": count_gate,
    "artifact_specific_gate_passed": artifact_gate,
}
TARGET.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, indent=2))
raise SystemExit(0 if artifact_gate else 1)
