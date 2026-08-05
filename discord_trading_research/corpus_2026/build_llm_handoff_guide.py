"""Render the final portable LLM handoff guide from its frozen template.

The renderer is deliberately narrow: it validates the final release scope and
cross-artifact database hashes, replaces the template's exact placeholder
contract, and publishes a new guide without overwriting any file.  It never
modifies the template, a database, or another release artifact.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import timestamp_scope_revalidation
import reply_provenance_contract


EXPECTED_GUILD_ID = "1167376964680691732"
EXPECTED_TIMEZONE = "America/Chicago"
EXPECTED_START_DATE = "2026-01-01"
EXPECTED_END_DATE = "2026-07-20"
EXPECTED_START_UTC = "2026-01-01T06:00:00Z"
EXPECTED_END_UTC = "2026-07-21T05:00:00Z"
PREMIUM_PARENT_ID = "1283941772577472643"
PREMIUM_AUTHORITATIVE_DIRECTORY = "raw/channel_segments_v2_5"
PREMIUM_LEGACY_DIRECTORY = "raw/channel_segments"
PREMIUM_COLLECTOR_VERSION = "2.6"
PREMIUM_REQUIRED_DAILY_SEGMENTS = 201

EXPECTED_FILENAMES = {
    "merged_corpus": "raw_corpus_release.json",
    "coverage_manifest": "coverage_manifest_release.json",
    "pristine_database": "cardinal_pristine.sqlite",
    "full_database": "cardinal_analyzed.sqlite",
    "compact_database": "cardinal_llm.sqlite",
    "analysis_report": "analysis_report.json",
    "qa_report": "independent_qa_report.json",
    "compact_report": "llm_companion_report.json",
    "output": "LLM_HANDOFF_GUIDE.md",
}

PACKAGE_PATHS = {
    "coverage_manifest": "manifests/corpus_coverage_manifest.json",
    "full_database": "databases/authoritative_cardinal.sqlite",
    "compact_database": "databases/compact_llm.sqlite",
    "qa_report": "qa/independent_qa_report.json",
}

NOT_PACKAGED = {
    "merged_corpus": (
        "NOT_PACKAGED (message and provenance data are retained in "
        "databases/authoritative_cardinal.sqlite)"
    ),
    "pristine_database": (
        "NOT_PACKAGED (use databases/authoritative_cardinal.sqlite as the "
        "analyzed authority)"
    ),
    "analysis_report": (
        "NOT_PACKAGED (use qa/independent_qa_report.json for release validation)"
    ),
    "compact_report": (
        "NOT_PACKAGED (see llm_manifest in databases/compact_llm.sqlite)"
    ),
}

PLACEHOLDER_COUNTS = Counter(
    {
        "{{ANALYSIS_REPORT_PATH}}": 2,
        "{{COMPACT_DATABASE_PATH}}": 2,
        "{{COMPACT_DATABASE_SHA256}}": 1,
        "{{COMPACT_REPORT_PATH}}": 2,
        "{{COVERAGE_MANIFEST_JSON_PATH}}": 2,
        "{{FULL_DATABASE_PATH}}": 3,
        "{{FULL_DATABASE_SHA256}}": 1,
        "{{MERGED_CORPUS_JSON_PATH}}": 2,
        "{{PRISTINE_DATABASE_PATH}}": 3,
        "{{QA_REPORT_PATH}}": 1,
        "{{RELEASE_STATUS}}": 1,
    }
)
PLACEHOLDER_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


class HandoffGuideError(ValueError):
    """Raised when the final handoff contract is not satisfied."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_regular(path: Path, *, label: str, suffixes: set[str]) -> Path:
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_file():
        raise HandoffGuideError(f"{label} must be an existing non-symlink regular file: {path}")
    if resolved.suffix.casefold() not in suffixes:
        raise HandoffGuideError(f"{label} has an unexpected suffix: {resolved.name}")
    if resolved.stat().st_size <= 0:
        raise HandoffGuideError(f"{label} is empty: {resolved}")
    return resolved


def require_expected_name(path: Path, role: str) -> None:
    expected = EXPECTED_FILENAMES[role]
    if path.name != expected:
        raise HandoffGuideError(
            f"{role} must use the canonical final filename {expected!r}, got {path.name!r}"
        )


def read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HandoffGuideError(f"{label} is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise HandoffGuideError(f"{label} JSON root must be an object")
    return value


def is_zero_outside_sources(value: Any) -> bool:
    return value is False or (type(value) is int and value == 0)


def parse_utc(value: Any, *, field: str) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise HandoffGuideError(f"Missing UTC timestamp {field}")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise HandoffGuideError(f"Invalid UTC timestamp {field}: {value!r}") from exc
    if parsed.tzinfo is None:
        raise HandoffGuideError(f"UTC timestamp {field} is timezone-naive: {value!r}")
    return parsed.astimezone(dt.timezone.utc)


def require_utc(value: Any, expected: str, *, field: str) -> None:
    if parse_utc(value, field=field) != parse_utc(expected, field=f"expected {field}"):
        raise HandoffGuideError(f"{field} does not match the canonical release window")


def require_scope(scope: Any, *, label: str, qa_shape: bool = False) -> None:
    if not isinstance(scope, dict):
        raise HandoffGuideError(f"{label} scope is missing")
    if str(scope.get("guild_id") or "") != EXPECTED_GUILD_ID:
        raise HandoffGuideError(f"{label} guild_id does not match the release")
    timezone_key = "window_calendar_timezone" if qa_shape else "timezone"
    if str(scope.get(timezone_key) or "") != EXPECTED_TIMEZONE:
        raise HandoffGuideError(f"{label} timezone does not match the release")
    start_key = "window_start_local_date" if qa_shape else "start_date_inclusive"
    end_key = "window_end_local_date_inclusive" if qa_shape else "end_date_inclusive"
    if str(scope.get(start_key) or "") != EXPECTED_START_DATE:
        raise HandoffGuideError(f"{label} local start date does not match the release")
    if str(scope.get(end_key) or "") != EXPECTED_END_DATE:
        raise HandoffGuideError(f"{label} local end date does not match the release")
    start_utc_key = "window_start_utc" if qa_shape else "utc_start_inclusive"
    end_utc_key = "window_end_exclusive_utc" if qa_shape else "utc_end_exclusive"
    require_utc(scope.get(start_utc_key), EXPECTED_START_UTC, field=f"{label}.{start_utc_key}")
    require_utc(scope.get(end_utc_key), EXPECTED_END_UTC, field=f"{label}.{end_utc_key}")
    days = scope.get("local_calendar_days")
    if days is not None and days != 201:
        raise HandoffGuideError(f"{label} local_calendar_days must be 201")


def require_discord_only(payload: Mapping[str, Any], *, label: str) -> None:
    if str(payload.get("source_scope") or "") != "discord_only":
        raise HandoffGuideError(f"{label} is not explicitly Discord-only")
    if not is_zero_outside_sources(payload.get("outside_sources_used")):
        raise HandoffGuideError(f"{label} reports outside-source use")


def validate_premium_release_contract(
    payload: dict[str, Any], *, label: str
) -> dict[str, Any]:
    authorized = payload.get("authorized_collection_scope")
    if not isinstance(authorized, dict) or authorized.get("enabled") is not True:
        raise HandoffGuideError(f"{label} authorized collection scope is missing")
    path_policy = authorized.get("canonical_path_policy")
    path_policy = path_policy if isinstance(path_policy, dict) else {}
    required_path_values = {
        "gate": "premium_journals_authoritative_v2_5_source_integrity",
        "passed": True,
        "standard_authoritative_directory": PREMIUM_LEGACY_DIRECTORY,
        "premium_authoritative_directory": PREMIUM_AUTHORITATIVE_DIRECTORY,
        "premium_legacy_preservation_directory": PREMIUM_LEGACY_DIRECTORY,
        "premium_legacy_directory_policy": "preservation_only_not_authoritative",
        "premium_collector_version_required": PREMIUM_COLLECTOR_VERSION,
        "required_roots_supplied_exactly_once": True,
        "legacy_premium_authoritative_occurrence_count": 0,
        "premium_collector_version_mismatch_count": 0,
        "premium_collector_version_mismatch_paths": [],
        "premium_provenance_missing_segment_count": 0,
        "premium_provenance_missing_segments": [],
        "invalid_premium_authoritative_file_count": 0,
        "invalid_premium_authoritative_paths": [],
        "accepted_premium_segment_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
        "accepted_premium_daily_date_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
        "duplicate_premium_daily_dates": [],
    }
    for key, expected in required_path_values.items():
        if path_policy.get(key) != expected:
            raise HandoffGuideError(
                f"{label} Premium authoritative source-integrity field {key} mismatch"
            )
    if (
        type(path_policy.get("accepted_premium_bound_source_file_count")) is not int
        or path_policy.get("accepted_premium_bound_source_file_count")
        < PREMIUM_REQUIRED_DAILY_SEGMENTS
    ):
        raise HandoffGuideError(
            f"{label} Premium immutable provenance source-file coverage is incomplete"
        )
    for key in (
        "accepted_premium_source_file_set_sha256",
        "accepted_premium_message_id_set_sha256",
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", str(path_policy.get(key) or "")):
            raise HandoffGuideError(
                f"{label} Premium authoritative source-integrity field {key} is missing"
            )
    matching_gates = [
        row
        for row in payload.get("release_gates") or []
        if isinstance(row, dict)
        and row.get("gate")
        == "premium_journals_authoritative_v2_5_source_integrity"
    ]
    if len(matching_gates) != 1 or matching_gates[0] != path_policy:
        raise HandoffGuideError(
            f"{label} Premium authoritative source-integrity gate is unbound"
        )
    reconciliation = authorized.get("child_inventory_reconciliation")
    reconciliation = reconciliation if isinstance(reconciliation, dict) else {}
    if reconciliation.get("provided") is not True or any(
        reconciliation.get(key) is not False
        for key in ("inventory_complete", "enumeration_complete", "closure_proven")
    ):
        raise HandoffGuideError(
            f"{label} Premium lower-bound inventory must remain explicitly non-closed"
        )
    closure = reconciliation.get("message_scope_closure")
    closure = closure if isinstance(closure, dict) else {}
    required_closure_values = {
        "gate": "premium_journals_message_data_scope_closure",
        "passed": True,
        "closure_proven": True,
        "status": "complete",
        "required_parent_container_id": PREMIUM_PARENT_ID,
        "required_calendar_day_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
        "complete_calendar_day_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
        "parent_segment_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
        "required_exact_daily_parent_segment_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
        "invalid_daily_partition_segment_count": 0,
        "duplicate_daily_date_count": 0,
    }
    for key, expected in required_closure_values.items():
        if closure.get(key) != expected:
            raise HandoffGuideError(
                f"{label} Premium message-scope closure field {key} mismatch"
            )
    if closure.get("missing_date_ranges") != []:
        raise HandoffGuideError(f"{label} Premium message-scope closure has missing dates")
    release_closure_gates = [
        row
        for row in payload.get("release_gates") or []
        if isinstance(row, dict)
        and row.get("gate") == "premium_journals_message_data_scope_closure"
    ]
    if len(release_closure_gates) != 1 or release_closure_gates[0] != closure:
        raise HandoffGuideError(
            f"{label} Premium message-scope closure gate is unbound"
        )
    return {"path_policy": path_policy, "closure": closure}


def validate_corpus(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("artifact_type") != "discord_serverwide_corpus_release":
        raise HandoffGuideError("Merged corpus is not the final release artifact type")
    require_scope(payload.get("scope"), label="merged corpus")
    require_discord_only(payload, label="merged corpus")
    release = payload.get("release")
    if not isinstance(release, dict):
        raise HandoffGuideError("Merged corpus release envelope is missing")
    if not (
        release.get("status") == "complete"
        and release.get("release_requested") is True
        and release.get("release_ready") is True
    ):
        raise HandoffGuideError("Merged corpus is not a complete release")
    return validate_premium_release_contract(payload, label="merged corpus")


def validate_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("artifact_type") != "discord_serverwide_coverage_manifest":
        raise HandoffGuideError("Coverage manifest has the wrong artifact type")
    if payload.get("status") != "complete" or payload.get("release_ready") is not True:
        raise HandoffGuideError("Coverage manifest is not release-ready and complete")
    require_scope(payload.get("scope"), label="coverage manifest")
    require_discord_only(payload, label="coverage manifest")
    timestamp_errors = (
        timestamp_scope_revalidation.release_timestamp_scope_integrity_errors(
            payload
        )
    )
    if timestamp_errors:
        raise HandoffGuideError(
            "Coverage manifest timestamp-scope integrity failed: "
            + ", ".join(timestamp_errors)
        )
    executed_command_errors = (
        reply_provenance_contract.release_executed_command_integrity_errors(
            payload
        )
    )
    if executed_command_errors:
        raise HandoffGuideError(
            "Coverage manifest executed-command reply provenance failed: "
            + ", ".join(executed_command_errors)
        )
    return validate_premium_release_contract(payload, label="coverage manifest")


def validate_analysis_report(
    payload: dict[str, Any], *, full_hash: str, pristine_hash: str, full_name: str, pristine_name: str
) -> None:
    if payload.get("status") != "passed":
        raise HandoffGuideError("Analysis report status is not passed")
    require_discord_only(payload, label="analysis report")
    if payload.get("database_sha256") != full_hash:
        raise HandoffGuideError("Analysis report is not bound to the final analyzed database hash")
    if Path(str(payload.get("database") or "")).name != full_name:
        raise HandoffGuideError("Analysis report names a different analyzed database")
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict) or coverage.get("analysis_completeness") != "complete":
        raise HandoffGuideError("Analysis report does not certify complete analysis coverage")
    require_utc(coverage.get("window_start_utc"), EXPECTED_START_UTC, field="analysis coverage start")
    require_utc(coverage.get("window_end_utc"), EXPECTED_END_UTC, field="analysis coverage end")
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise HandoffGuideError("Analysis report provenance is missing")
    if provenance.get("input_database_sha256") != pristine_hash:
        raise HandoffGuideError("Analysis report is not bound to the pristine database hash")
    if Path(str(provenance.get("input_database") or "")).name != pristine_name:
        raise HandoffGuideError("Analysis report names a different pristine database")


def validate_qa_report(payload: dict[str, Any], *, full_hash: str, full_name: str) -> None:
    if payload.get("artifact_type") != "independent_discord_corpus_validation":
        raise HandoffGuideError("QA report has the wrong artifact type")
    if payload.get("status") != "passed" or payload.get("overall_assessment") != "Ready to share":
        raise HandoffGuideError("QA report is not passed / Ready to share")
    scope = payload.get("scope")
    require_scope(scope, label="QA report", qa_shape=True)
    if not isinstance(scope, dict):
        raise HandoffGuideError("QA report scope is missing")
    require_discord_only(scope, label="QA report")
    if not (
        scope.get("premium_authoritative_directory")
        == PREMIUM_AUTHORITATIVE_DIRECTORY
        and scope.get("premium_collector_version_required")
        == PREMIUM_COLLECTOR_VERSION
        and scope.get("premium_daily_segment_count")
        == PREMIUM_REQUIRED_DAILY_SEGMENTS
        and scope.get("premium_inventory_census_complete") is False
    ):
        raise HandoffGuideError(
            "QA report does not bind the Premium authoritative source contract"
        )
    failures = payload.get("failure_counts")
    if not isinstance(failures, dict) or any(value != 0 for value in failures.values()):
        raise HandoffGuideError("QA report has nonzero or missing failure counts")
    checks = payload.get("checks")
    passed_check_names = {
        str(row.get("name") or "")
        for row in checks
        if isinstance(checks, list)
        and isinstance(row, dict)
        and row.get("passed") is True
    } if isinstance(checks, list) else set()
    if "collection_drift_final_audit_passed" not in passed_check_names:
        raise HandoffGuideError("QA report lacks the passed final collection-drift gate")
    database_validation = payload.get("database_validation")
    if not isinstance(database_validation, dict) or database_validation.get("sha256") != full_hash:
        raise HandoffGuideError("QA report is not bound to the analyzed database hash")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict) or Path(str(inputs.get("database") or "")).name != full_name:
        raise HandoffGuideError("QA report names a different analyzed database")
    drift_input_path = str(inputs.get("collection_drift_audit") or "").strip()
    if not drift_input_path:
        raise HandoffGuideError("QA report does not name its final collection-drift audit")
    drift = payload.get("collection_drift_audit")
    drift_summary = drift.get("summary") if isinstance(drift, dict) else None
    if not (
        isinstance(drift, dict)
        and drift.get("status") == "passed"
        and drift.get("passed") is True
        and drift.get("mode") == "final"
        and drift.get("overall_status") == "PASS"
        and drift.get("release_gate_passed") is True
        and SHA256_RE.fullmatch(str(drift.get("sha256") or ""))
        and isinstance(drift_summary, dict)
        and all(
            type(drift_summary.get(field)) is int
            and drift_summary.get(field) == 0
            for field in (
                "structural_failure_count",
                "unresolved_count",
                "effective_final_failure_count",
                "orphan_quarantined_partial_count",
            )
        )
        and not (drift.get("errors") or [])
    ):
        raise HandoffGuideError("QA report does not certify zero unresolved collection drift")
    drift_summary_path = str(drift.get("path") or "").strip()
    if not drift_summary_path or Path(drift_summary_path).resolve() != Path(
        drift_input_path
    ).resolve():
        raise HandoffGuideError(
            "QA collection-drift summary is not bound to its declared input path"
        )


def validate_compact_report(
    payload: dict[str, Any], *, full_hash: str, compact_hash: str, full_name: str, compact_name: str
) -> None:
    if payload.get("status") != "passed":
        raise HandoffGuideError("Compact-build report status is not passed")
    require_discord_only(payload, label="compact-build report")
    if payload.get("source_database_sha256") != full_hash:
        raise HandoffGuideError("Compact-build report is not bound to the analyzed database hash")
    if payload.get("database_sha256") != compact_hash:
        raise HandoffGuideError("Compact-build report is not bound to the compact database hash")
    if payload.get("source_database_unchanged") is not True:
        raise HandoffGuideError("Compact-build report does not certify source stability")
    if Path(str(payload.get("source_database") or "")).name != full_name:
        raise HandoffGuideError("Compact-build report names a different analyzed database")
    if Path(str(payload.get("database") or "")).name != compact_name:
        raise HandoffGuideError("Compact-build report names a different compact database")


def validate_template(text: str) -> None:
    actual = Counter(PLACEHOLDER_RE.findall(text))
    if actual != PLACEHOLDER_COUNTS:
        missing_or_wrong = {
            token: {"expected": count, "actual": actual.get(token, 0)}
            for token, count in PLACEHOLDER_COUNTS.items()
            if actual.get(token, 0) != count
        }
        unknown = {token: count for token, count in actual.items() if token not in PLACEHOLDER_COUNTS}
        raise HandoffGuideError(
            "Template placeholder contract mismatch: "
            + json.dumps({"missing_or_wrong": missing_or_wrong, "unknown": unknown}, sort_keys=True)
        )


def render_text(template: str, replacements: Mapping[str, str], *, bindings: str) -> str:
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    unresolved = PLACEHOLDER_RE.findall(rendered)
    if unresolved or "{{" in rendered or "}}" in rendered:
        raise HandoffGuideError(f"Rendered guide contains unresolved template syntax: {unresolved}")
    return rendered.rstrip() + "\n\n" + bindings.rstrip() + "\n"


def atomic_write_no_overwrite(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise HandoffGuideError(f"Output already exists; refusing overwrite: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise HandoffGuideError(f"Output appeared during publish; refusing overwrite: {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def render_handoff_guide(
    *,
    template: Path,
    output: Path,
    merged_corpus: Path,
    coverage_manifest: Path,
    pristine_database: Path,
    full_database: Path,
    compact_database: Path,
    analysis_report: Path,
    qa_report: Path,
    compact_report: Path,
    release_status: str = "complete",
) -> dict[str, Any]:
    if release_status != "complete":
        raise HandoffGuideError("Only release_status=complete may be rendered")

    paths = {
        "template": require_regular(template, label="Template", suffixes={".md"}),
        "merged_corpus": require_regular(merged_corpus, label="Merged corpus", suffixes={".json"}),
        "coverage_manifest": require_regular(
            coverage_manifest, label="Coverage manifest", suffixes={".json"}
        ),
        "pristine_database": require_regular(
            pristine_database, label="Pristine database", suffixes={".sqlite", ".db"}
        ),
        "full_database": require_regular(
            full_database, label="Analyzed database", suffixes={".sqlite", ".db"}
        ),
        "compact_database": require_regular(
            compact_database, label="Compact database", suffixes={".sqlite", ".db"}
        ),
        "analysis_report": require_regular(
            analysis_report, label="Analysis report", suffixes={".json"}
        ),
        "qa_report": require_regular(qa_report, label="QA report", suffixes={".json"}),
        "compact_report": require_regular(
            compact_report, label="Compact report", suffixes={".json"}
        ),
    }
    output_path = output.resolve()
    require_expected_name(output_path, "output")
    for role in EXPECTED_FILENAMES:
        if role != "output":
            require_expected_name(paths[role], role)
    if output_path in set(paths.values()):
        raise HandoffGuideError("Output path must differ from every input")
    if len(set(paths.values())) != len(paths):
        raise HandoffGuideError("Every input role must refer to a distinct file")
    if output_path.exists() or output_path.is_symlink():
        raise HandoffGuideError(f"Output already exists; refusing overwrite: {output_path}")

    hashes_before = {role: sha256_file(path) for role, path in paths.items()}
    template_text = paths["template"].read_text(encoding="utf-8")
    validate_template(template_text)

    corpus_payload = read_json(paths["merged_corpus"], label="Merged corpus")
    manifest_payload = read_json(paths["coverage_manifest"], label="Coverage manifest")
    analysis_payload = read_json(paths["analysis_report"], label="Analysis report")
    qa_payload = read_json(paths["qa_report"], label="QA report")
    compact_payload = read_json(paths["compact_report"], label="Compact report")
    corpus_premium = validate_corpus(corpus_payload)
    manifest_premium = validate_manifest(manifest_payload)
    if corpus_premium != manifest_premium:
        raise HandoffGuideError(
            "Merged corpus and coverage manifest Premium source contracts differ"
        )
    validate_analysis_report(
        analysis_payload,
        full_hash=hashes_before["full_database"],
        pristine_hash=hashes_before["pristine_database"],
        full_name=paths["full_database"].name,
        pristine_name=paths["pristine_database"].name,
    )
    validate_qa_report(
        qa_payload,
        full_hash=hashes_before["full_database"],
        full_name=paths["full_database"].name,
    )
    validate_compact_report(
        compact_payload,
        full_hash=hashes_before["full_database"],
        compact_hash=hashes_before["compact_database"],
        full_name=paths["full_database"].name,
        compact_name=paths["compact_database"].name,
    )

    replacements = {
        "{{MERGED_CORPUS_JSON_PATH}}": NOT_PACKAGED["merged_corpus"],
        "{{COVERAGE_MANIFEST_JSON_PATH}}": PACKAGE_PATHS["coverage_manifest"],
        "{{PRISTINE_DATABASE_PATH}}": NOT_PACKAGED["pristine_database"],
        "{{FULL_DATABASE_PATH}}": PACKAGE_PATHS["full_database"],
        "{{COMPACT_DATABASE_PATH}}": PACKAGE_PATHS["compact_database"],
        "{{ANALYSIS_REPORT_PATH}}": NOT_PACKAGED["analysis_report"],
        "{{QA_REPORT_PATH}}": PACKAGE_PATHS["qa_report"],
        "{{COMPACT_REPORT_PATH}}": NOT_PACKAGED["compact_report"],
        "{{FULL_DATABASE_SHA256}}": hashes_before["full_database"],
        "{{COMPACT_DATABASE_SHA256}}": hashes_before["compact_database"],
        "{{RELEASE_STATUS}}": release_status,
    }
    bindings = f"""## Deterministic release binding

This section was generated only after the exact final scope and cross-artifact
hash links passed. Paths below are relative to the release package root.

- Guild: `{EXPECTED_GUILD_ID}`
- Local-date scope: `{EXPECTED_START_DATE}` through `{EXPECTED_END_DATE}` in `{EXPECTED_TIMEZONE}`
- Exact UTC interval: `[{EXPECTED_START_UTC}, {EXPECTED_END_UTC})`
- Premium authoritative canonical directory: `{PREMIUM_AUTHORITATIVE_DIRECTORY}`
- Premium collector contract: v{PREMIUM_COLLECTOR_VERSION}; `{PREMIUM_REQUIRED_DAILY_SEGMENTS}` exact daily segments accepted
- Legacy Premium directory: `{PREMIUM_LEGACY_DIRECTORY}` - preservation-only, never authoritative
- Premium child inventory census complete: `false` (message-data closure is distinct from inventory census closure)
- Packaged authoritative database: `{PACKAGE_PATHS['full_database']}` — SHA-256 `{hashes_before['full_database']}`
- Packaged compact database: `{PACKAGE_PATHS['compact_database']}` — SHA-256 `{hashes_before['compact_database']}`
- Packaged coverage manifest: `{PACKAGE_PATHS['coverage_manifest']}` — source SHA-256 `{hashes_before['coverage_manifest']}`
- Packaged independent QA: `{PACKAGE_PATHS['qa_report']}` — source SHA-256 `{hashes_before['qa_report']}`
- Build-only merged corpus: `{paths['merged_corpus'].name}` — SHA-256 `{hashes_before['merged_corpus']}` — not packaged
- Build-only pristine database: `{paths['pristine_database'].name}` — SHA-256 `{hashes_before['pristine_database']}` — not packaged
- Build-only analysis report: `{paths['analysis_report'].name}` — SHA-256 `{hashes_before['analysis_report']}` — not packaged
- Build-only compact-build report: `{paths['compact_report'].name}` — SHA-256 `{hashes_before['compact_report']}` — not packaged
"""
    rendered = render_text(template_text, replacements, bindings=bindings)

    hashes_after = {role: sha256_file(path) for role, path in paths.items()}
    changed = [role for role in paths if hashes_after[role] != hashes_before[role]]
    if changed:
        raise HandoffGuideError("Input changed during rendering: " + ", ".join(changed))
    atomic_write_no_overwrite(output_path, rendered)
    return {
        "status": "passed",
        "release_status": release_status,
        "output": str(output_path),
        "output_sha256": sha256_file(output_path),
        "full_database_sha256": hashes_before["full_database"],
        "compact_database_sha256": hashes_before["compact_database"],
        "template_sha256": hashes_before["template"],
        "inputs_unchanged": True,
        "scope": {
            "guild_id": EXPECTED_GUILD_ID,
            "timezone": EXPECTED_TIMEZONE,
            "start_date_inclusive": EXPECTED_START_DATE,
            "end_date_inclusive": EXPECTED_END_DATE,
            "utc_start_inclusive": EXPECTED_START_UTC,
            "utc_end_exclusive": EXPECTED_END_UTC,
            "premium_authoritative_directory": PREMIUM_AUTHORITATIVE_DIRECTORY,
            "premium_collector_version_required": PREMIUM_COLLECTOR_VERSION,
            "premium_daily_segment_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
            "premium_inventory_census_complete": False,
        },
        "package_paths": PACKAGE_PATHS,
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument(
        "--template",
        type=Path,
        default=Path(__file__).resolve().parent / "LLM_HANDOFF_GUIDE_TEMPLATE.md",
    )
    value.add_argument("--output", required=True, type=Path)
    value.add_argument("--merged-corpus", required=True, type=Path)
    value.add_argument("--coverage-manifest", required=True, type=Path)
    value.add_argument("--pristine-database", required=True, type=Path)
    value.add_argument("--full-database", required=True, type=Path)
    value.add_argument("--compact-database", required=True, type=Path)
    value.add_argument("--analysis-report", required=True, type=Path)
    value.add_argument("--qa-report", required=True, type=Path)
    value.add_argument("--compact-report", required=True, type=Path)
    value.add_argument("--release-status", choices=("complete",), default="complete")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = render_handoff_guide(
            template=args.template,
            output=args.output,
            merged_corpus=args.merged_corpus,
            coverage_manifest=args.coverage_manifest,
            pristine_database=args.pristine_database,
            full_database=args.full_database,
            compact_database=args.compact_database,
            analysis_report=args.analysis_report,
            qa_report=args.qa_report,
            compact_report=args.compact_report,
            release_status=args.release_status,
        )
    except (OSError, UnicodeError, HandoffGuideError) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2) + "\n", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
