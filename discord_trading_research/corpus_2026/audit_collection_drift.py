#!/usr/bin/env python3
"""Fail-closed audit for Discord search-total drift and replacement artifacts.

This program is deliberately read-only with respect to ``raw/``.  It validates
machine-readable total-drift notes and their quarantined checkpoints, then
proves that each drift chain ends in one unambiguous, complete canonical
replacement.  Its only write is an atomic JSON report below ``working/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
DEFAULT_START = date(2026, 1, 1)
DEFAULT_END = date(2026, 7, 20)
DISCORD_EPOCH_MS = 1_420_070_400_000
SNOWFLAKE_RE = re.compile(r"^[0-9]{17,20}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_TEXT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$"
)
QUERY_RE = re.compile(
    r"^in:(?:\"[^\"]+\"|\S+) after:(\d{4}-\d{2}-\d{2}) "
    r"before:(\d{4}-\d{2}-\d{2})$"
)
PREMIUM_PARENT_ID = "1283941772577472643"
STANDARD_CANONICAL_PREFIX = PurePosixPath("raw/channel_segments")
PREMIUM_CANONICAL_PREFIX = PurePosixPath("raw/channel_segments_v2_5")

NOTE_REQUIRED = {
    "event_type",
    "guild_id",
    "channel_id",
    "channel_name",
    "segment_start",
    "segment_end",
    "query",
    "old_reported_total",
    "new_reported_total",
    "old_total_observed_at_utc",
    "new_total_observed_at_utc",
    "source_checkpoint_original_path",
    "source_checkpoint_quarantine_path",
    "source_checkpoint_sha256",
    "source_checkpoint_rows",
    "source_checkpoint_pages",
    "source_checkpoint_unique_message_ids",
    "source_checkpoint_gap_count",
    "restart_artifact_path",
    "restart_partial_path",
    "action",
    "outside_sources_used",
}
NOTE_OPTIONAL = {
    "missing_result_index_before_recount",
    "diagnostics",
}
NOTE_ALLOWED = NOTE_REQUIRED | NOTE_OPTIONAL
NOTE_DIAGNOSTIC_KEYS = {
    "missing_result_index_before_recount",
    "missing_result_index_before_rerender",
    "transient_zero_recount_observed",
}
NOTE_DIAGNOSTIC_INDEX_KEYS = {
    "missing_result_index_before_recount",
    "missing_result_index_before_rerender",
}

RESOLUTION_REQUIRED = {
    "event_type",
    "schema_version",
    "guild_id",
    "channel_id",
    "channel_name",
    "segment_start",
    "segment_end",
    "query",
    "resolved_at_utc",
    "invalid_artifact_path",
    "invalid_artifact_sha256",
    "invalid_artifact_metrics",
    "defects",
    "canonical_replacement_path",
    "canonical_replacement_sha256",
    "canonical_replacement_metrics",
    "resolution_action",
    "outside_sources_used",
}
RESOLUTION_METRIC_KEYS = {
    "collector_version",
    "reported_total",
    "reported_pages",
    "pages_captured",
    "captured_rows",
    "unique_message_ids",
    "gap_indices",
    "container_mismatch_count",
    "complete",
}


class DriftAuditError(RuntimeError):
    """Raised for unsafe output choices or unusable command arguments."""


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("top-level JSON value is not an object")
    return value


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not UTC_TEXT_RE.fullmatch(value):
        raise ValueError("expected an ISO-8601 UTC timestamp ending in Z")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    return parsed.astimezone(timezone.utc)


def _snowflake_datetime(value: str) -> datetime:
    if not SNOWFLAKE_RE.fullmatch(value):
        raise ValueError("not a Discord snowflake")
    milliseconds = (int(value) >> 22) + DISCORD_EPOCH_MS
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc)


def _first_sunday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7)


def _central_date(value: datetime) -> date:
    """Return America/Chicago date without requiring a host tzdata package.

    The corpus is in 2026, when the post-2007 US daylight-saving rule applies:
    DST begins at 08:00 UTC on March's second Sunday and ends at 07:00 UTC on
    November's first Sunday.
    """

    value = value.astimezone(timezone.utc)
    year = value.year
    march_second_sunday = _first_sunday(year, 3) + timedelta(days=7)
    november_first_sunday = _first_sunday(year, 11)
    dst_start = datetime.combine(march_second_sunday, datetime.min.time(), timezone.utc) + timedelta(hours=8)
    dst_end = datetime.combine(november_first_sunday, datetime.min.time(), timezone.utc) + timedelta(hours=7)
    offset = -5 if dst_start <= value < dst_end else -6
    return (value + timedelta(hours=offset)).date()


def _is_int(value: Any) -> bool:
    return type(value) is int


def _relative_path(root: Path, value: Any, prefix: PurePosixPath) -> tuple[Path | None, str | None]:
    if not isinstance(value, str) or not value or "\\" in value:
        return None, "path must be a non-empty root-relative POSIX path"
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return None, "path must not be absolute or contain traversal components"
    if tuple(pure.parts[: len(prefix.parts)]) != prefix.parts:
        return None, f"path must be below {prefix.as_posix()}/"
    resolved_root = root.resolve()
    resolved = (root / Path(*pure.parts)).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return None, "path resolves outside the corpus root"
    return resolved, None


def _canonical_prefix(channel_id: Any) -> PurePosixPath:
    return (
        PREMIUM_CANONICAL_PREFIX
        if str(channel_id or "") == PREMIUM_PARENT_ID
        else STANDARD_CANONICAL_PREFIX
    )


def _issue(
    code: str,
    message: str,
    *,
    path: str | None = None,
    note_path: str | None = None,
    kind: str = "failure",
    severity: str = "critical",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "code": code,
        "kind": kind,
        "severity": severity,
        "message": message,
    }
    if note_path is not None:
        row["note_path"] = note_path
    if path is not None:
        row["path"] = path
    if evidence:
        row["evidence"] = evidence
    return row


def _rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _validate_note(
    payload: dict[str, Any],
    note_relative: str,
    window_start: date,
    window_end: date,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    missing = sorted(NOTE_REQUIRED - set(payload))
    unexpected = sorted(set(payload) - NOTE_ALLOWED)
    if missing or unexpected:
        issues.append(
            _issue(
                "drift_note_schema_mismatch",
                "Drift note keys do not match the exact supported schema.",
                note_path=note_relative,
                evidence={"missing_keys": missing, "unexpected_keys": unexpected},
            )
        )

    def fail(code: str, message: str, **evidence: Any) -> None:
        issues.append(_issue(code, message, note_path=note_relative, evidence=evidence or None))

    if payload.get("event_type") != "discord_search_total_drift":
        fail("invalid_event_type", "event_type must be discord_search_total_drift.")
    for field in ("guild_id", "channel_id"):
        value = payload.get(field)
        if not isinstance(value, str) or not SNOWFLAKE_RE.fullmatch(value):
            fail("invalid_note_snowflake", f"{field} is not an exact Discord snowflake.", field=field, value=value)
    if not isinstance(payload.get("channel_name"), str) or not payload.get("channel_name", "").strip():
        fail("invalid_channel_name", "channel_name must be a non-empty string.")

    parsed_start: date | None = None
    parsed_end: date | None = None
    try:
        parsed_start = date.fromisoformat(payload.get("segment_start"))
        parsed_end = date.fromisoformat(payload.get("segment_end"))
    except (TypeError, ValueError):
        fail("invalid_segment_window", "segment_start and segment_end must be exact ISO dates.")
    if parsed_start is not None and parsed_end is not None:
        if parsed_end < parsed_start or parsed_start < window_start or parsed_end > window_end:
            fail(
                "segment_outside_audit_window",
                "Drift-note segment is reversed or outside the configured corpus window.",
                segment_start=parsed_start.isoformat(),
                segment_end=parsed_end.isoformat(),
                audit_start=window_start.isoformat(),
                audit_end=window_end.isoformat(),
            )
        query = payload.get("query")
        match = QUERY_RE.fullmatch(query) if isinstance(query, str) else None
        expected_after = (parsed_start - timedelta(days=1)).isoformat()
        expected_before = (parsed_end + timedelta(days=1)).isoformat()
        if not match or match.group(1) != expected_after or match.group(2) != expected_before:
            fail(
                "query_window_mismatch",
                "Discord query must contain exactly the one-day-exclusive boundaries for the segment.",
                query=query,
                expected_after=expected_after,
                expected_before=expected_before,
            )

    for field in (
        "old_reported_total",
        "new_reported_total",
        "source_checkpoint_rows",
        "source_checkpoint_pages",
        "source_checkpoint_unique_message_ids",
        "source_checkpoint_gap_count",
    ):
        value = payload.get(field)
        if not _is_int(value) or value < 0:
            fail("invalid_note_integer", f"{field} must be a non-negative integer.", field=field, value=value)
    old_total = payload.get("old_reported_total")
    new_total = payload.get("new_reported_total")
    if _is_int(old_total) and _is_int(new_total) and old_total == new_total:
        fail("unchanged_reported_total", "A total-drift note must have different old and new totals.", total=old_total)

    old_time: datetime | None = None
    new_time: datetime | None = None
    try:
        old_time = _parse_utc(payload.get("old_total_observed_at_utc"))
    except (TypeError, ValueError) as exc:
        fail("invalid_observation_timestamp", str(exc), field="old_total_observed_at_utc")
    try:
        new_time = _parse_utc(payload.get("new_total_observed_at_utc"))
    except (TypeError, ValueError) as exc:
        fail("invalid_observation_timestamp", str(exc), field="new_total_observed_at_utc")
    if old_time is not None and new_time is not None and not old_time < new_time:
        fail(
            "observation_order_invalid",
            "old_total_observed_at_utc must precede new_total_observed_at_utc.",
            old=payload.get("old_total_observed_at_utc"),
            new=payload.get("new_total_observed_at_utc"),
        )

    sha = payload.get("source_checkpoint_sha256")
    if not isinstance(sha, str) or not SHA256_RE.fullmatch(sha):
        fail("invalid_checkpoint_sha256", "source_checkpoint_sha256 must be 64 lowercase hexadecimal characters.")
    if payload.get("action") != "quarantined_stale_checkpoint_and_restart_from_page_1":
        fail("invalid_drift_action", "action does not describe the required quarantine-and-page-1 restart.")
    if payload.get("outside_sources_used") is not False:
        fail("outside_source_boundary_violation", "outside_sources_used must be exactly false.")

    if "missing_result_index_before_recount" in payload:
        index = payload.get("missing_result_index_before_recount")
        if not _is_int(index) or index < 1 or (_is_int(old_total) and index > old_total):
            fail(
                "invalid_missing_result_index",
                "missing_result_index_before_recount must be a positive index within the old total.",
                field="missing_result_index_before_recount",
                value=index,
            )
    if "diagnostics" in payload:
        diagnostics = payload.get("diagnostics")
        if not isinstance(diagnostics, dict) or not diagnostics:
            fail("invalid_drift_diagnostics", "diagnostics must be a non-empty object when present.")
        else:
            unknown = sorted(set(diagnostics) - NOTE_DIAGNOSTIC_KEYS)
            if unknown:
                fail(
                    "invalid_drift_diagnostics_schema",
                    "diagnostics contains unsupported keys.",
                    unexpected_keys=unknown,
                    allowed_keys=sorted(NOTE_DIAGNOSTIC_KEYS),
                )
            for field, index in diagnostics.items():
                if field in NOTE_DIAGNOSTIC_INDEX_KEYS and (
                    not _is_int(index) or index < 1 or (_is_int(old_total) and index > old_total)
                ):
                    fail(
                        "invalid_missing_result_index",
                        f"diagnostics.{field} must be a positive index within the old total.",
                        field=f"diagnostics.{field}",
                        value=index,
                    )
                if field == "transient_zero_recount_observed" and not isinstance(index, bool):
                    fail(
                        "invalid_transient_zero_diagnostic",
                        "diagnostics.transient_zero_recount_observed must be a boolean.",
                        field=f"diagnostics.{field}",
                        value=index,
                    )
            if (
                "missing_result_index_before_recount" in diagnostics
                and "missing_result_index_before_recount" in payload
            ):
                fail(
                    "duplicate_drift_diagnostic",
                    "missing_result_index_before_recount cannot appear both top-level and in diagnostics.",
                )

    # Safe path syntax, required directory prefixes, and root containment are
    # checked by the caller against the actual corpus root.
    original = payload.get("source_checkpoint_original_path")
    restart_partial = payload.get("restart_partial_path")
    if isinstance(original, str) and isinstance(restart_partial, str) and original != restart_partial:
        fail(
            "restart_partial_path_mismatch",
            "restart_partial_path must reuse the quarantined checkpoint's original canonical path.",
            original=original,
            restart_partial=restart_partial,
        )
    if isinstance(payload.get("source_checkpoint_quarantine_path"), str) and not payload[
        "source_checkpoint_quarantine_path"
    ].endswith(".partial.json"):
        fail("invalid_quarantine_checkpoint_suffix", "Quarantined source checkpoint must end in .partial.json.")
    if isinstance(payload.get("restart_partial_path"), str) and not payload["restart_partial_path"].endswith(
        ".partial.json"
    ):
        fail("invalid_restart_partial_suffix", "restart_partial_path must end in .partial.json.")
    artifact_path = payload.get("restart_artifact_path")
    if isinstance(artifact_path, str) and (not artifact_path.endswith(".json") or artifact_path.endswith(".partial.json")):
        fail("invalid_restart_artifact_suffix", "restart_artifact_path must name a non-partial .json artifact.")
    return issues


def _validate_resolution_note(
    root: Path,
    payload: dict[str, Any],
    note_relative: str,
    window_start: date,
    window_end: date,
) -> list[dict[str, Any]]:
    """Validate the exact schema of a non-drift collection-error resolution."""

    issues: list[dict[str, Any]] = []

    def fail(code: str, message: str, **evidence: Any) -> None:
        issues.append(_issue(code, message, note_path=note_relative, evidence=evidence or None))

    missing = sorted(RESOLUTION_REQUIRED - set(payload))
    unexpected = sorted(set(payload) - RESOLUTION_REQUIRED)
    if missing or unexpected:
        fail(
            "collection_error_resolution_schema_mismatch",
            "Collection-error resolution keys do not match the exact supported schema.",
            missing_keys=missing,
            unexpected_keys=unexpected,
        )
    if payload.get("event_type") != "collection_error_resolution":
        fail("invalid_resolution_event_type", "event_type must be collection_error_resolution.")
    if payload.get("schema_version") != "1.0.0":
        fail("invalid_resolution_schema_version", "schema_version must be 1.0.0.")
    for field in ("guild_id", "channel_id"):
        value = payload.get(field)
        if not isinstance(value, str) or not SNOWFLAKE_RE.fullmatch(value):
            fail("invalid_resolution_snowflake", f"{field} is not an exact Discord snowflake.", field=field, value=value)
    if not isinstance(payload.get("channel_name"), str) or not payload.get("channel_name", "").strip():
        fail("invalid_resolution_channel_name", "channel_name must be a non-empty string.")

    parsed_start: date | None = None
    parsed_end: date | None = None
    try:
        parsed_start = date.fromisoformat(payload.get("segment_start"))
        parsed_end = date.fromisoformat(payload.get("segment_end"))
    except (TypeError, ValueError):
        fail("invalid_resolution_window", "segment_start and segment_end must be exact ISO dates.")
    if parsed_start is not None and parsed_end is not None:
        if parsed_end < parsed_start or parsed_start < window_start or parsed_end > window_end:
            fail(
                "resolution_outside_audit_window",
                "Resolution segment is reversed or outside the configured corpus window.",
                segment_start=parsed_start.isoformat(),
                segment_end=parsed_end.isoformat(),
                audit_start=window_start.isoformat(),
                audit_end=window_end.isoformat(),
            )
        query = payload.get("query")
        match = QUERY_RE.fullmatch(query) if isinstance(query, str) else None
        expected_after = (parsed_start - timedelta(days=1)).isoformat()
        expected_before = (parsed_end + timedelta(days=1)).isoformat()
        if not match or match.group(1) != expected_after or match.group(2) != expected_before:
            fail(
                "resolution_query_window_mismatch",
                "Discord query must contain exactly the one-day-exclusive boundaries for the segment.",
                query=query,
                expected_after=expected_after,
                expected_before=expected_before,
            )
    try:
        _parse_utc(payload.get("resolved_at_utc"))
    except (TypeError, ValueError) as exc:
        fail("invalid_resolution_timestamp", str(exc))

    for field in ("invalid_artifact_sha256", "canonical_replacement_sha256"):
        value = payload.get(field)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            fail("invalid_resolution_sha256", f"{field} must be 64 lowercase hexadecimal characters.", field=field)
    for field, prefix, partial_expected in (
        ("invalid_artifact_path", PurePosixPath("raw/quarantine_collection_errors"), True),
        ("canonical_replacement_path", _canonical_prefix(payload.get("channel_id")), False),
    ):
        _path, error = _relative_path(root, payload.get(field), prefix)
        if error:
            fail("invalid_resolution_path", error, field=field, value=payload.get(field))
        value = payload.get(field)
        if isinstance(value, str):
            suffix_ok = value.endswith(".partial.json") if partial_expected else (
                value.endswith(".json") and not value.endswith(".partial.json")
            )
            if not suffix_ok:
                fail(
                    "invalid_resolution_path_suffix",
                    f"{field} has the wrong complete/partial suffix.",
                    field=field,
                    value=value,
                )

    for field, expected_complete in (
        ("invalid_artifact_metrics", False),
        ("canonical_replacement_metrics", True),
    ):
        metrics = payload.get(field)
        if not isinstance(metrics, dict):
            fail("resolution_metrics_not_object", f"{field} must be an object.", field=field)
            continue
        missing_metrics = sorted(RESOLUTION_METRIC_KEYS - set(metrics))
        unexpected_metrics = sorted(set(metrics) - RESOLUTION_METRIC_KEYS)
        if missing_metrics or unexpected_metrics:
            fail(
                "resolution_metrics_schema_mismatch",
                f"{field} keys do not match the exact metrics schema.",
                field=field,
                missing_keys=missing_metrics,
                unexpected_keys=unexpected_metrics,
            )
        if not isinstance(metrics.get("collector_version"), str) or not metrics.get("collector_version", "").strip():
            fail("resolution_metrics_collector_invalid", f"{field}.collector_version must be non-empty.", field=field)
        for metric in (
            "reported_total",
            "reported_pages",
            "pages_captured",
            "captured_rows",
            "unique_message_ids",
            "container_mismatch_count",
        ):
            value = metrics.get(metric)
            if not _is_int(value) or value < 0:
                fail(
                    "resolution_metric_integer_invalid",
                    f"{field}.{metric} must be a non-negative integer.",
                    field=field,
                    metric=metric,
                    value=value,
                )
        gaps = metrics.get("gap_indices")
        if (
            not isinstance(gaps, list)
            or any(not _is_int(item) or item < 1 for item in gaps)
            or gaps != sorted(set(gaps))
        ):
            fail(
                "resolution_metric_gaps_invalid",
                f"{field}.gap_indices must be a sorted unique list of positive integers.",
                field=field,
            )
        if metrics.get("complete") is not expected_complete:
            fail(
                "resolution_metric_complete_invalid",
                f"{field}.complete must be exactly {str(expected_complete).lower()}.",
                field=field,
            )

    defects = payload.get("defects")
    if not isinstance(defects, list) or not defects:
        fail("resolution_defects_missing", "defects must be a non-empty list.")
    elif any(not isinstance(item, dict) or not isinstance(item.get("code"), str) for item in defects):
        fail("resolution_defect_schema_invalid", "Every defect must be an object with a string code.")
    else:
        encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in defects]
        if len(encoded) != len(set(encoded)):
            fail("resolution_defects_duplicated", "defects contains duplicate entries.")
    if payload.get("resolution_action") != "quarantined_malformed_checkpoint_and_verified_complete_canonical_replacement":
        fail("invalid_resolution_action", "resolution_action does not match the supported remediation.")
    if payload.get("outside_sources_used") is not False:
        fail("resolution_outside_source_boundary_violation", "outside_sources_used must be exactly false.")
    return issues


def _artifact_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload.get(key) for key in sorted(RESOLUTION_METRIC_KEYS)}


def _detect_source_defects(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Return reproducible malformed-checkpoint defects and unsupported errors."""

    defects: list[dict[str, Any]] = []
    unsupported: list[str] = []
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return [], ["messages_not_list"]

    message_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    indices: list[int] = []
    page_numbers: list[int] = []
    result_sizes: dict[int, int] = defaultdict(int)
    for row_number, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            unsupported.append(f"message_not_object:{row_number}")
            continue
        message_id = message.get("message_id")
        if not isinstance(message_id, str) or not SNOWFLAKE_RE.fullmatch(message_id):
            unsupported.append(f"invalid_message_id:{row_number}")
        else:
            message_rows[message_id].append(message)
        result_index = message.get("result_index")
        if not _is_int(result_index) or result_index < 1:
            unsupported.append(f"invalid_result_index:{row_number}")
        else:
            indices.append(result_index)
        page_number = message.get("page_number")
        if not _is_int(page_number) or page_number < 1:
            unsupported.append(f"invalid_page_number:{row_number}")
        else:
            page_numbers.append(page_number)
            if _is_int(result_index) and page_number != math.ceil(result_index / 25):
                unsupported.append(f"page_result_index_disagreement:{row_number}")
        result_size = message.get("result_set_size")
        if not _is_int(result_size) or result_size < 0:
            unsupported.append(f"invalid_result_set_size:{row_number}")
        else:
            result_sizes[result_size] += 1

    for message_id, rows in sorted(message_rows.items(), key=lambda item: int(item[0])):
        if len(rows) > 1:
            defects.append(
                {
                    "code": "duplicate_message_id",
                    "message_id": message_id,
                    "occurrences": len(rows),
                    "result_indices": sorted(
                        row["result_index"] for row in rows if _is_int(row.get("result_index"))
                    ),
                }
            )
    index_counts: dict[int, int] = defaultdict(int)
    for index in indices:
        index_counts[index] += 1
    for index, count in sorted(index_counts.items()):
        if count > 1:
            defects.append({"code": "duplicate_result_index", "result_index": index, "occurrences": count})
    computed_gaps = _computed_gap_indices(indices)
    for index in computed_gaps:
        defects.append({"code": "missing_result_index", "result_index": index})
    declared_gaps = payload.get("gap_indices")
    if declared_gaps != computed_gaps:
        defects.append(
            {
                "code": "declared_gap_indices_mismatch",
                "declared": declared_gaps,
                "computed": computed_gaps,
            }
        )
    if len(result_sizes) > 1:
        defects.append(
            {
                "code": "mixed_result_set_sizes",
                "counts": [
                    {"reported_total": total, "rows": rows}
                    for total, rows in sorted(result_sizes.items())
                ],
            }
        )

    computed_unique = len(message_rows)
    if payload.get("captured_rows") != len(messages):
        defects.append(
            {
                "code": "captured_rows_mismatch",
                "declared": payload.get("captured_rows"),
                "computed": len(messages),
            }
        )
    if payload.get("unique_message_ids") != computed_unique:
        defects.append(
            {
                "code": "unique_message_ids_mismatch",
                "declared": payload.get("unique_message_ids"),
                "computed": computed_unique,
            }
        )
    observed_pages = sorted(set(page_numbers))
    expected_observed_pages = list(range(1, payload.get("pages_captured", -1) + 1)) if _is_int(payload.get("pages_captured")) else []
    if observed_pages != expected_observed_pages:
        defects.append(
            {
                "code": "pages_captured_mismatch",
                "declared": payload.get("pages_captured"),
                "observed_pages": observed_pages,
            }
        )
    reported_total = payload.get("reported_total")
    expected_reported_pages = math.ceil(reported_total / 25) if _is_int(reported_total) and reported_total else 0
    if payload.get("reported_pages") != expected_reported_pages:
        defects.append(
            {
                "code": "reported_pages_mismatch",
                "declared": payload.get("reported_pages"),
                "computed": expected_reported_pages,
            }
        )
    if payload.get("container_mismatch_count") not in (0, None):
        defects.append(
            {
                "code": "container_mismatch",
                "count": payload.get("container_mismatch_count"),
                "message_ids": payload.get("container_mismatch_message_ids"),
            }
        )
    defects.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return defects, sorted(set(unsupported))


def _defect_set(value: list[dict[str, Any]]) -> list[str]:
    return sorted(json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value)


def _validate_resolution_source_artifact(
    root: Path,
    path: Path,
    note: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    """Validate immutable/source identity while allowing enumerated defects."""

    relative = _rel(root, path)
    note_relative = note["_note_relative"]
    issues: list[dict[str, Any]] = []

    def fail(code: str, message: str, **evidence: Any) -> None:
        issues.append(_issue(code, message, path=relative, note_path=note_relative, evidence=evidence or None))

    try:
        payload = _load_object(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        fail("resolution_invalid_artifact_json", f"Could not load invalid-artifact JSON: {exc}")
        return issues, None, []
    if payload.get("guild_id") != note.get("guild_id"):
        fail("resolution_invalid_artifact_guild_mismatch", "Invalid artifact guild_id does not match the resolution note.")
    requested = payload.get("requested_container")
    if not isinstance(requested, dict) or requested.get("channel_id") != note.get("channel_id"):
        fail("resolution_invalid_artifact_channel_mismatch", "Invalid artifact channel_id does not match the resolution note.")
    segment = payload.get("segment")
    expected_segment = {
        "start": note.get("segment_start"),
        "end": note.get("segment_end"),
        "query": note.get("query"),
    }
    if not isinstance(segment, dict) or any(segment.get(key) != value for key, value in expected_segment.items()):
        fail("resolution_invalid_artifact_segment_mismatch", "Invalid artifact segment/query does not match the resolution note.")
    observed_metrics = _artifact_metrics(payload)
    if observed_metrics != note.get("invalid_artifact_metrics"):
        fail(
            "resolution_invalid_artifact_metrics_mismatch",
            "invalid_artifact_metrics does not exactly bind the quarantined file.",
            declared=note.get("invalid_artifact_metrics"),
            observed=observed_metrics,
        )

    defects, unsupported = _detect_source_defects(payload)
    if unsupported:
        fail(
            "resolution_source_has_unsupported_defects",
            "Invalid artifact contains defects that this exact schema cannot safely certify.",
            defects=unsupported,
        )
    if not defects:
        fail("resolution_source_not_malformed", "Invalid artifact has no independently reproducible defect.")
    declared_defects = note.get("defects")
    if isinstance(declared_defects, list) and all(isinstance(item, dict) for item in declared_defects):
        if _defect_set(declared_defects) != _defect_set(defects):
            fail(
                "resolution_defect_evidence_mismatch",
                "Declared defects do not exactly match independently recomputed defects.",
                declared=declared_defects,
                computed=defects,
            )

    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    segment_start = date.fromisoformat(note["segment_start"])
    segment_end = date.fromisoformat(note["segment_end"])
    outside_ids: list[str] = []
    timestamp_mismatch_ids: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_id = message.get("message_id")
        if not isinstance(message_id, str) or not SNOWFLAKE_RE.fullmatch(message_id):
            continue
        stamp = _snowflake_datetime(message_id)
        if not segment_start <= _central_date(stamp) <= segment_end:
            outside_ids.append(message_id)
        declared_stamp = message.get("snowflake_timestamp_utc")
        if declared_stamp is not None:
            try:
                if abs((_parse_utc(declared_stamp) - stamp).total_seconds()) > 0.001:
                    timestamp_mismatch_ids.append(message_id)
            except ValueError:
                timestamp_mismatch_ids.append(message_id)
    if outside_ids:
        fail(
            "resolution_invalid_artifact_outside_window",
            "Invalid artifact contains message snowflakes outside its Central segment window.",
            count=len(outside_ids),
            sample=outside_ids[:10],
        )
    if timestamp_mismatch_ids:
        fail(
            "resolution_invalid_artifact_snowflake_timestamp_mismatch",
            "Invalid artifact has declared snowflake timestamps that disagree with message IDs.",
            count=len(timestamp_mismatch_ids),
            sample=timestamp_mismatch_ids[:10],
        )
    return issues, payload, defects


def _computed_gap_indices(indices: Iterable[int]) -> list[int]:
    values = sorted(set(indices))
    if not values:
        return []
    present = set(values)
    return [number for number in range(1, values[-1] + 1) if number not in present]


def _validate_artifact(
    root: Path,
    path: Path,
    note: dict[str, Any],
    *,
    expected_total: int,
    role: str,
    require_complete: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    relative = _rel(root, path)
    note_relative = str(note["_note_relative"])
    issues: list[dict[str, Any]] = []

    def fail(code: str, message: str, **evidence: Any) -> None:
        issues.append(
            _issue(code, message, path=relative, note_path=note_relative, evidence=evidence or None)
        )

    try:
        payload = _load_object(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        fail("artifact_json_invalid", f"Could not load {role} JSON: {exc}")
        return issues, {"path": relative, "valid": False}

    if payload.get("guild_id") != note.get("guild_id"):
        fail("artifact_guild_mismatch", f"{role} guild_id does not match the drift note.")
    requested = payload.get("requested_container")
    if not isinstance(requested, dict) or requested.get("channel_id") != note.get("channel_id"):
        fail("artifact_channel_mismatch", f"{role} requested channel_id does not match the drift note.")
    segment = payload.get("segment")
    expected_segment = {
        "start": note.get("segment_start"),
        "end": note.get("segment_end"),
        "query": note.get("query"),
    }
    if not isinstance(segment, dict) or any(segment.get(key) != value for key, value in expected_segment.items()):
        fail("artifact_segment_mismatch", f"{role} segment/query does not exactly match the drift note.")
    if payload.get("reported_total") != expected_total:
        fail(
            "artifact_total_mismatch",
            f"{role} reported_total does not match the applicable drift total.",
            expected=expected_total,
            actual=payload.get("reported_total"),
        )
    expected_pages = math.ceil(expected_total / 25) if expected_total else 0
    if payload.get("reported_pages") != expected_pages:
        fail(
            "artifact_reported_pages_mismatch",
            f"{role} reported_pages is inconsistent with reported_total.",
            expected=expected_pages,
            actual=payload.get("reported_pages"),
        )

    messages = payload.get("messages")
    if not isinstance(messages, list):
        fail("artifact_messages_invalid", f"{role} messages must be a list.")
        messages = []
    captured_rows = payload.get("captured_rows")
    if captured_rows != len(messages):
        fail(
            "artifact_row_count_mismatch",
            f"{role} captured_rows does not match len(messages).",
            declared=captured_rows,
            computed=len(messages),
        )

    message_ids: list[str] = []
    result_indices: list[int] = []
    page_numbers: list[int] = []
    bad_result_set_size: list[str] = []
    bad_window_ids: list[str] = []
    bad_snowflake_timestamp_ids: list[str] = []
    segment_start = date.fromisoformat(str(note["segment_start"]))
    segment_end = date.fromisoformat(str(note["segment_end"]))
    for number, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            fail("artifact_message_not_object", f"{role} message row {number} is not an object.")
            continue
        message_id = message.get("message_id")
        if not isinstance(message_id, str) or not SNOWFLAKE_RE.fullmatch(message_id):
            fail(
                "artifact_message_id_invalid",
                f"{role} has a non-snowflake message_id.",
                row=number,
                message_id=message_id,
            )
        else:
            message_ids.append(message_id)
            stamp = _snowflake_datetime(message_id)
            if not segment_start <= _central_date(stamp) <= segment_end:
                bad_window_ids.append(message_id)
            declared_stamp = message.get("snowflake_timestamp_utc")
            if declared_stamp is not None:
                try:
                    if abs((_parse_utc(declared_stamp) - stamp).total_seconds()) > 0.001:
                        bad_snowflake_timestamp_ids.append(message_id)
                except ValueError:
                    bad_snowflake_timestamp_ids.append(message_id)
        result_index = message.get("result_index")
        if _is_int(result_index) and result_index > 0:
            result_indices.append(result_index)
        else:
            fail("artifact_result_index_invalid", f"{role} row has an invalid result_index.", row=number)
        page_number = message.get("page_number")
        if _is_int(page_number) and page_number > 0:
            page_numbers.append(page_number)
            if _is_int(result_index) and page_number != math.ceil(result_index / 25):
                fail(
                    "artifact_page_index_mismatch",
                    f"{role} page_number does not agree with result_index.",
                    row=number,
                    result_index=result_index,
                    page_number=page_number,
                )
        else:
            fail("artifact_page_number_invalid", f"{role} row has an invalid page_number.", row=number)
        if message.get("result_set_size") != expected_total:
            bad_result_set_size.append(str(message_id or number))

    if bad_window_ids:
        fail(
            "artifact_message_outside_segment_window",
            f"{role} contains snowflakes outside the segment in America/Chicago.",
            count=len(bad_window_ids),
            sample=bad_window_ids[:10],
        )
    if bad_snowflake_timestamp_ids:
        fail(
            "artifact_snowflake_timestamp_mismatch",
            f"{role} has declared snowflake timestamps that disagree with message IDs.",
            count=len(bad_snowflake_timestamp_ids),
            sample=bad_snowflake_timestamp_ids[:10],
        )
    if bad_result_set_size:
        fail(
            "artifact_result_set_size_mismatch",
            f"{role} message rows do not all carry the applicable reported total.",
            count=len(bad_result_set_size),
            sample=bad_result_set_size[:10],
        )

    unique_count = len(set(message_ids))
    if payload.get("unique_message_ids") != unique_count:
        fail(
            "artifact_unique_count_mismatch",
            f"{role} unique_message_ids does not match computed uniqueness.",
            declared=payload.get("unique_message_ids"),
            computed=unique_count,
        )
    if len(message_ids) != unique_count:
        fail(
            "artifact_duplicate_message_ids",
            f"{role} contains duplicate message IDs.",
            rows=len(message_ids),
            unique=unique_count,
        )

    declared_gaps = payload.get("gap_indices")
    computed_gaps = _computed_gap_indices(result_indices)
    if len(result_indices) != len(set(result_indices)):
        fail(
            "artifact_duplicate_result_indices",
            f"{role} contains duplicate result indices.",
            rows=len(result_indices),
            unique=len(set(result_indices)),
        )
    if declared_gaps != computed_gaps:
        fail(
            "artifact_gap_indices_mismatch",
            f"{role} gap_indices does not match computed result-index gaps.",
            declared=declared_gaps,
            computed=computed_gaps[:100],
        )
    pages_captured = payload.get("pages_captured")
    computed_pages = sorted(set(page_numbers))
    if not _is_int(pages_captured) or pages_captured < 0 or computed_pages != list(range(1, pages_captured + 1)):
        fail(
            "artifact_pages_captured_mismatch",
            f"{role} pages_captured does not match continuous observed page numbers.",
            declared=pages_captured,
            computed=computed_pages,
        )
    mismatch_ids = payload.get("container_mismatch_message_ids")
    if payload.get("container_mismatch_count") != 0 or mismatch_ids not in (None, []):
        fail(
            "artifact_container_mismatch",
            f"{role} has non-zero channel/container mismatches.",
            count=payload.get("container_mismatch_count"),
            message_ids=mismatch_ids,
        )

    if require_complete:
        if payload.get("complete") is not True:
            fail("canonical_replacement_not_complete", "Canonical replacement is not marked complete.")
        if len(messages) != expected_total:
            fail(
                "canonical_replacement_row_total_mismatch",
                "Canonical replacement row count does not equal the new total.",
                expected=expected_total,
                actual=len(messages),
            )
        if result_indices != list(range(1, expected_total + 1)):
            fail(
                "canonical_result_indices_not_continuous",
                "Canonical replacement result indices are not exactly 1..new_total in row order.",
                expected_count=expected_total,
                actual_count=len(result_indices),
                computed_gaps=computed_gaps[:100],
            )
        if pages_captured != expected_pages:
            fail(
                "canonical_page_total_mismatch",
                "Canonical replacement did not capture all reported pages.",
                expected=expected_pages,
                actual=pages_captured,
            )
    else:
        if payload.get("complete") is not False:
            fail("source_checkpoint_not_partial", "Quarantined source checkpoint must be marked complete=false.")
        field_pairs = {
            "source_checkpoint_rows": len(messages),
            "source_checkpoint_pages": payload.get("pages_captured"),
            "source_checkpoint_unique_message_ids": unique_count,
            "source_checkpoint_gap_count": len(computed_gaps),
        }
        for note_field, computed in field_pairs.items():
            if note.get(note_field) != computed:
                fail(
                    "checkpoint_note_metric_mismatch",
                    f"Drift-note {note_field} does not match the quarantined checkpoint.",
                    field=note_field,
                    note_value=note.get(note_field),
                    checkpoint_value=computed,
                )
        try:
            captured_at = _parse_utc(payload.get("captured_at_utc"))
            observed_at = _parse_utc(note.get("old_total_observed_at_utc"))
            if captured_at != observed_at:
                fail(
                    "checkpoint_observation_time_mismatch",
                    "Checkpoint captured_at_utc must equal old_total_observed_at_utc.",
                    captured_at=payload.get("captured_at_utc"),
                    observed_at=note.get("old_total_observed_at_utc"),
                )
        except ValueError as exc:
            fail("checkpoint_timestamp_invalid", str(exc))

    facts = {
        "path": relative,
        "valid": not issues,
        "reported_total": payload.get("reported_total"),
        "reported_pages": payload.get("reported_pages"),
        "captured_rows": len(messages),
        "unique_message_ids_computed": unique_count,
        "pages_captured": payload.get("pages_captured"),
        "gap_count_computed": len(computed_gaps),
        "container_mismatch_count": payload.get("container_mismatch_count"),
        "complete": payload.get("complete"),
        "sha256": _sha256(path),
    }
    return issues, facts


def _candidate_files(root: Path, note: dict[str, Any]) -> tuple[list[Path], list[Path]]:
    channel_root = root / Path(*_canonical_prefix(note.get("channel_id")).parts)
    if not channel_root.exists():
        return [], []
    token = f"_{note['channel_id']}_{note['segment_start']}_{note['segment_end']}"
    complete: list[Path] = []
    partial: list[Path] = []
    for path in channel_root.rglob("*.json"):
        if path.name.endswith(
            (
                ".completion-evidence.json",
                ".timestamp-scope-revalidation.json",
            )
        ):
            continue
        if token not in path.name:
            continue
        if path.name.endswith(".partial.json"):
            partial.append(path.resolve())
        else:
            complete.append(path.resolve())
    return sorted(set(complete)), sorted(set(partial))


def audit_collection_drift(
    root: Path,
    *,
    mode: str = "collection",
    window_start: date = DEFAULT_START,
    window_end: date = DEFAULT_END,
) -> dict[str, Any]:
    """Audit drift notes and return a serializable report without writing files."""

    root = root.resolve()
    if mode not in {"collection", "final"}:
        raise DriftAuditError("mode must be collection or final")
    if window_end < window_start:
        raise DriftAuditError("window end precedes window start")
    quarantine = root / "raw" / "quarantine_collection_errors"
    notes_paths = sorted(quarantine.rglob("*.total-drift-note.json")) if quarantine.exists() else []
    resolution_paths = sorted(quarantine.rglob("*.collection-error-resolution.json")) if quarantine.exists() else []
    failures: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    note_rows: list[dict[str, Any]] = []
    resolution_rows: list[dict[str, Any]] = []
    valid_notes: list[dict[str, Any]] = []
    referenced_quarantine_paths: set[Path] = set()
    resolution_reference_owners: dict[Path, str] = {}

    for note_path in notes_paths:
        relative = _rel(root, note_path)
        try:
            payload = _load_object(note_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            failures.append(
                _issue(
                    "drift_note_json_invalid",
                    f"Could not load drift-note JSON: {exc}",
                    note_path=relative,
                    path=relative,
                )
            )
            note_rows.append({"note_path": relative, "status": "invalid_json"})
            continue
        payload = dict(payload)
        payload["_note_relative"] = relative
        schema_payload = {key: value for key, value in payload.items() if key != "_note_relative"}
        note_issues = _validate_note(schema_payload, relative, window_start, window_end)
        # Validate real-root containment for all note paths.
        canonical_prefix = _canonical_prefix(payload.get("channel_id"))
        for field, prefix in {
            "source_checkpoint_original_path": canonical_prefix,
            "source_checkpoint_quarantine_path": PurePosixPath("raw/quarantine_collection_errors"),
            "restart_artifact_path": canonical_prefix,
            "restart_partial_path": canonical_prefix,
        }.items():
            _resolved, error = _relative_path(root, payload.get(field), prefix)
            if error:
                note_issues.append(
                    _issue(
                        "invalid_note_path",
                        error,
                        note_path=relative,
                        evidence={"field": field, "value": payload.get(field)},
                    )
                )
        if note_issues:
            failures.extend(note_issues)
            note_rows.append({"note_path": relative, "status": "invalid", "issue_count": len(note_issues)})
            continue

        checkpoint_path, _error = _relative_path(
            root,
            payload["source_checkpoint_quarantine_path"],
            PurePosixPath("raw/quarantine_collection_errors"),
        )
        assert checkpoint_path is not None
        referenced_quarantine_paths.add(checkpoint_path.resolve())
        if not checkpoint_path.exists():
            failures.append(
                _issue(
                    "quarantined_checkpoint_missing",
                    "Drift note references a quarantined checkpoint that does not exist.",
                    note_path=relative,
                    path=payload["source_checkpoint_quarantine_path"],
                )
            )
            note_rows.append({"note_path": relative, "status": "invalid", "resolution": "checkpoint_missing"})
            continue
        actual_sha = _sha256(checkpoint_path)
        if actual_sha != payload["source_checkpoint_sha256"]:
            failures.append(
                _issue(
                    "source_checkpoint_hash_mismatch",
                    "Quarantined checkpoint SHA-256 does not match the drift note.",
                    note_path=relative,
                    path=_rel(root, checkpoint_path),
                    evidence={"expected": payload["source_checkpoint_sha256"], "actual": actual_sha},
                )
            )
        checkpoint_issues, checkpoint_facts = _validate_artifact(
            root,
            checkpoint_path,
            payload,
            expected_total=payload["old_reported_total"],
            role="quarantined checkpoint",
            require_complete=False,
        )
        failures.extend(checkpoint_issues)
        payload["_checkpoint_facts"] = checkpoint_facts
        payload["_checkpoint_valid"] = (
            not checkpoint_issues and actual_sha == payload["source_checkpoint_sha256"]
        )
        valid_notes.append(payload)
        note_rows.append(
            {
                "note_path": relative,
                "status": "checkpoint_valid" if payload["_checkpoint_valid"] else "invalid",
                "old_reported_total": payload["old_reported_total"],
                "new_reported_total": payload["new_reported_total"],
                "checkpoint": checkpoint_facts,
                "resolution": "not_yet_evaluated",
            }
        )

    row_by_path = {row["note_path"]: row for row in note_rows}
    groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for note in valid_notes:
        groups[
            (
                note["guild_id"],
                note["channel_id"],
                note["segment_start"],
                note["segment_end"],
                note["query"],
            )
        ].append(note)

    resolved_groups = 0
    for key, chain in sorted(groups.items()):
        chain.sort(key=lambda item: _parse_utc(item["old_total_observed_at_utc"]))
        chain_valid = all(note["_checkpoint_valid"] for note in chain)
        for previous, current in zip(chain, chain[1:]):
            previous_new_time = _parse_utc(previous["new_total_observed_at_utc"])
            current_old_time = _parse_utc(current["old_total_observed_at_utc"])
            if current["old_reported_total"] != previous["new_reported_total"] or current_old_time < previous_new_time:
                chain_valid = False
                failures.append(
                    _issue(
                        "drift_chain_disconnected",
                        "Successive drift notes do not form a chronological old-total/new-total chain.",
                        note_path=current["_note_relative"],
                        evidence={
                            "previous_note": previous["_note_relative"],
                            "previous_new_total": previous["new_reported_total"],
                            "current_old_total": current["old_reported_total"],
                        },
                    )
                )
        restart_paths = {note["restart_artifact_path"] for note in chain}
        if len(restart_paths) != 1:
            chain_valid = False
            failures.append(
                _issue(
                    "drift_chain_restart_path_changed",
                    "Notes for one segment do not identify one canonical replacement path.",
                    note_path=chain[-1]["_note_relative"],
                    evidence={"restart_paths": sorted(restart_paths)},
                )
            )

        latest = chain[-1]
        expected_artifact, _error = _relative_path(
            root,
            latest["restart_artifact_path"],
            _canonical_prefix(latest.get("channel_id")),
        )
        assert expected_artifact is not None
        complete_candidates, partial_candidates = _candidate_files(root, latest)
        if expected_artifact.exists() and expected_artifact.resolve() not in complete_candidates:
            complete_candidates.append(expected_artifact.resolve())
            complete_candidates.sort()

        stale_hashes = {note["source_checkpoint_sha256"] for note in chain}
        for candidate in partial_candidates:
            try:
                candidate_sha = _sha256(candidate)
            except OSError:
                continue
            if candidate_sha in stale_hashes:
                failures.append(
                    _issue(
                        "stale_checkpoint_present_in_canonical",
                        "A quarantined stale checkpoint still exists byte-for-byte in canonical storage.",
                        note_path=latest["_note_relative"],
                        path=_rel(root, candidate),
                        evidence={"sha256": candidate_sha},
                    )
                )
                chain_valid = False

        if len(complete_candidates) > 1 or (complete_candidates and partial_candidates) or len(partial_candidates) > 1:
            failures.append(
                _issue(
                    "canonical_segment_ambiguity",
                    "The drifted segment has multiple current complete/partial canonical candidates.",
                    note_path=latest["_note_relative"],
                    evidence={
                        "complete_candidates": [_rel(root, path) for path in complete_candidates],
                        "partial_candidates": [_rel(root, path) for path in partial_candidates],
                    },
                )
            )
            chain_valid = False

        canonical_valid = False
        canonical_facts: dict[str, Any] | None = None
        if len(complete_candidates) == 1 and complete_candidates[0] == expected_artifact.resolve():
            canonical_issues, canonical_facts = _validate_artifact(
                root,
                expected_artifact,
                latest,
                expected_total=latest["new_reported_total"],
                role="canonical replacement",
                require_complete=True,
            )
            failures.extend(canonical_issues)
            canonical_valid = not canonical_issues
        elif complete_candidates and expected_artifact.resolve() not in complete_candidates:
            failures.append(
                _issue(
                    "canonical_replacement_path_mismatch",
                    "A complete segment candidate exists, but not at restart_artifact_path.",
                    note_path=latest["_note_relative"],
                    evidence={"expected": _rel(root, expected_artifact)},
                )
            )
        elif not complete_candidates:
            if partial_candidates:
                unresolved.append(
                    _issue(
                        "replacement_still_partial",
                        "The latest drift replacement remains partial and has no complete canonical artifact.",
                        note_path=latest["_note_relative"],
                        path=_rel(root, partial_candidates[0]),
                        kind="unresolved",
                        severity="high",
                        evidence={"target_total": latest["new_reported_total"]},
                    )
                )
            else:
                unresolved.append(
                    _issue(
                        "drift_note_lacks_final_resolution",
                        "No partial or complete canonical replacement exists for the latest drift note.",
                        note_path=latest["_note_relative"],
                        path=latest["restart_artifact_path"],
                        kind="unresolved",
                        severity="high",
                        evidence={"target_total": latest["new_reported_total"]},
                    )
                )

        group_resolved = chain_valid and canonical_valid
        if group_resolved:
            resolved_groups += 1
        for index, note in enumerate(chain):
            row = row_by_path[note["_note_relative"]]
            row["canonical_replacement"] = canonical_facts
            if group_resolved and row.get("status") != "invalid":
                row["status"] = "resolved"
                row["resolution"] = "direct" if index == len(chain) - 1 else "superseded_by_valid_later_drift"
                row["final_reported_total"] = latest["new_reported_total"]
            elif row.get("status") != "invalid":
                row["status"] = "unresolved"
                row["resolution"] = "pending_or_failed"

    valid_resolution_count = 0
    for resolution_path in resolution_paths:
        relative = _rel(root, resolution_path)
        try:
            payload = _load_object(resolution_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            failures.append(
                _issue(
                    "collection_error_resolution_json_invalid",
                    f"Could not load collection-error resolution JSON: {exc}",
                    note_path=relative,
                    path=relative,
                )
            )
            resolution_rows.append({"note_path": relative, "status": "invalid_json"})
            continue
        local_issues = _validate_resolution_note(root, payload, relative, window_start, window_end)
        if local_issues:
            failures.extend(local_issues)
            resolution_rows.append(
                {"note_path": relative, "status": "invalid", "issue_count": len(local_issues)}
            )
            continue
        payload = dict(payload)
        payload["_note_relative"] = relative
        invalid_path, _error = _relative_path(
            root,
            payload["invalid_artifact_path"],
            PurePosixPath("raw/quarantine_collection_errors"),
        )
        canonical_path, _error = _relative_path(
            root,
            payload["canonical_replacement_path"],
            _canonical_prefix(payload.get("channel_id")),
        )
        assert invalid_path is not None and canonical_path is not None
        source_payload: dict[str, Any] | None = None
        computed_defects: list[dict[str, Any]] = []
        canonical_facts: dict[str, Any] | None = None
        if not invalid_path.exists():
            local_issues.append(
                _issue(
                    "resolution_invalid_artifact_missing",
                    "Collection-error resolution references a missing quarantined artifact.",
                    note_path=relative,
                    path=payload["invalid_artifact_path"],
                )
            )
        else:
            actual_invalid_sha = _sha256(invalid_path)
            if actual_invalid_sha != payload["invalid_artifact_sha256"]:
                local_issues.append(
                    _issue(
                        "resolution_invalid_artifact_hash_mismatch",
                        "Invalid-artifact SHA-256 does not match the collection-error resolution.",
                        note_path=relative,
                        path=_rel(root, invalid_path),
                        evidence={"expected": payload["invalid_artifact_sha256"], "actual": actual_invalid_sha},
                    )
                )
            source_issues, source_payload, computed_defects = _validate_resolution_source_artifact(
                root, invalid_path, payload
            )
            local_issues.extend(source_issues)

        complete_candidates, partial_candidates = _candidate_files(root, payload)
        if canonical_path.exists() and canonical_path.resolve() not in complete_candidates:
            complete_candidates.append(canonical_path.resolve())
            complete_candidates.sort()
        if (
            len(complete_candidates) != 1
            or complete_candidates[0] != canonical_path.resolve()
            or partial_candidates
        ):
            local_issues.append(
                _issue(
                    "resolution_canonical_segment_ambiguity",
                    "Collection-error resolution does not point to one sole complete canonical segment.",
                    note_path=relative,
                    evidence={
                        "expected": _rel(root, canonical_path),
                        "complete_candidates": [_rel(root, path) for path in complete_candidates],
                        "partial_candidates": [_rel(root, path) for path in partial_candidates],
                    },
                )
            )
        if not canonical_path.exists():
            local_issues.append(
                _issue(
                    "resolution_canonical_replacement_missing",
                    "Collection-error resolution references a missing canonical replacement.",
                    note_path=relative,
                    path=payload["canonical_replacement_path"],
                )
            )
        else:
            actual_canonical_sha = _sha256(canonical_path)
            if actual_canonical_sha != payload["canonical_replacement_sha256"]:
                local_issues.append(
                    _issue(
                        "resolution_canonical_hash_mismatch",
                        "Canonical replacement SHA-256 does not match the collection-error resolution.",
                        note_path=relative,
                        path=_rel(root, canonical_path),
                        evidence={"expected": payload["canonical_replacement_sha256"], "actual": actual_canonical_sha},
                    )
                )
            try:
                canonical_payload = _load_object(canonical_path)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                local_issues.append(
                    _issue(
                        "resolution_canonical_json_invalid",
                        f"Could not load canonical replacement JSON: {exc}",
                        note_path=relative,
                        path=_rel(root, canonical_path),
                    )
                )
                canonical_payload = None
            if canonical_payload is not None:
                observed_canonical_metrics = _artifact_metrics(canonical_payload)
                if observed_canonical_metrics != payload["canonical_replacement_metrics"]:
                    local_issues.append(
                        _issue(
                            "resolution_canonical_metrics_mismatch",
                            "canonical_replacement_metrics does not exactly bind the canonical file.",
                            note_path=relative,
                            path=_rel(root, canonical_path),
                            evidence={
                                "declared": payload["canonical_replacement_metrics"],
                                "observed": observed_canonical_metrics,
                            },
                        )
                    )
                canonical_issues, canonical_facts = _validate_artifact(
                    root,
                    canonical_path,
                    payload,
                    expected_total=payload["canonical_replacement_metrics"]["reported_total"],
                    role="collection-error canonical replacement",
                    require_complete=True,
                )
                local_issues.extend(canonical_issues)
                if source_payload is not None:
                    try:
                        source_time = _parse_utc(source_payload.get("captured_at_utc"))
                        canonical_time = _parse_utc(canonical_payload.get("captured_at_utc"))
                        resolved_time = _parse_utc(payload["resolved_at_utc"])
                        if not source_time <= canonical_time <= resolved_time:
                            local_issues.append(
                                _issue(
                                    "resolution_chronology_invalid",
                                    "Expected invalid capture <= canonical capture <= resolved_at_utc.",
                                    note_path=relative,
                                    evidence={
                                        "invalid_captured_at_utc": source_payload.get("captured_at_utc"),
                                        "canonical_captured_at_utc": canonical_payload.get("captured_at_utc"),
                                        "resolved_at_utc": payload["resolved_at_utc"],
                                    },
                                )
                            )
                    except ValueError as exc:
                        local_issues.append(
                            _issue(
                                "resolution_artifact_timestamp_invalid",
                                str(exc),
                                note_path=relative,
                            )
                        )

        invalid_resolved = invalid_path.resolve()
        if invalid_resolved in referenced_quarantine_paths:
            local_issues.append(
                _issue(
                    "cross_event_reference_ambiguity",
                    "One quarantine artifact cannot be both a total-drift checkpoint and a malformed-checkpoint resolution source.",
                    note_path=relative,
                    path=_rel(root, invalid_path),
                )
            )
        if invalid_resolved in resolution_reference_owners:
            local_issues.append(
                _issue(
                    "duplicate_collection_error_resolution",
                    "Multiple collection-error resolution notes reference the same invalid artifact.",
                    note_path=relative,
                    path=_rel(root, invalid_path),
                    evidence={"first_resolution_note": resolution_reference_owners[invalid_resolved]},
                )
            )

        if local_issues:
            failures.extend(local_issues)
            resolution_rows.append(
                {
                    "note_path": relative,
                    "status": "invalid",
                    "issue_count": len(local_issues),
                    "invalid_artifact_path": payload["invalid_artifact_path"],
                    "canonical_replacement": canonical_facts,
                    "computed_defects": computed_defects,
                }
            )
        else:
            referenced_quarantine_paths.add(invalid_resolved)
            resolution_reference_owners[invalid_resolved] = relative
            valid_resolution_count += 1
            resolution_rows.append(
                {
                    "note_path": relative,
                    "status": "resolved_non_drift_collection_error",
                    "invalid_artifact_path": payload["invalid_artifact_path"],
                    "invalid_artifact_sha256": payload["invalid_artifact_sha256"],
                    "canonical_replacement": canonical_facts,
                    "defects": computed_defects,
                }
            )

    orphan_paths: list[str] = []
    if quarantine.exists():
        for partial in sorted(quarantine.rglob("*.partial.json")):
            if partial.resolve() not in referenced_quarantine_paths:
                relative = _rel(root, partial)
                orphan_paths.append(relative)
                unresolved.append(
                    _issue(
                        "orphan_quarantined_partial",
                        "Quarantined partial is not referenced by any valid total-drift note.",
                        path=relative,
                        kind="unresolved",
                        severity="high",
                    )
                )

    failure_count = len(failures)
    unresolved_count = len(unresolved)
    if failure_count:
        overall = "FAIL"
    elif unresolved_count:
        overall = "PENDING" if mode == "collection" else "FAIL"
    else:
        overall = "PASS"
    effective_final_failures = failure_count + (unresolved_count if mode == "final" else 0)
    report = {
        "schema_version": "1.0.0",
        "audit_type": "discord_collection_total_drift",
        "generated_at_utc": _now_utc(),
        "corpus_root": str(root),
        "mode": mode,
        "audit_window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "timezone": "America/Chicago",
        },
        "evidence_boundary": {
            "source": "Discord collector artifacts and local provenance notes only",
            "outside_sources_permitted": False,
            "links_or_attachments_fetched": False,
        },
        "overall_status": overall,
        "release_gate_passed": overall == "PASS",
        "summary": {
            "drift_note_files": len(notes_paths),
            "valid_notes_reaching_checkpoint_validation": len(valid_notes),
            "drift_segment_groups": len(groups),
            "resolved_segment_groups": resolved_groups,
            "collection_error_resolution_files": len(resolution_paths),
            "valid_non_drift_collection_error_resolutions": valid_resolution_count,
            "structural_failure_count": failure_count,
            "unresolved_count": unresolved_count,
            "effective_final_failure_count": effective_final_failures,
            "orphan_quarantined_partial_count": len(orphan_paths),
        },
        "notes": sorted(note_rows, key=lambda row: row["note_path"]),
        "collection_error_resolutions": sorted(resolution_rows, key=lambda row: row["note_path"]),
        "failures": failures,
        "unresolved": unresolved,
        "orphan_quarantined_partials": orphan_paths,
        "exit_code_contract": {"PASS": 0, "FAIL": 1, "PENDING": 2},
    }
    return report


def write_report_atomic(
    root: Path,
    output: Path,
    report: dict[str, Any],
    *,
    overwrite: bool = False,
) -> None:
    """Atomically write one report below working/, refusing implicit overwrite."""

    root = root.resolve()
    working = (root / "working").resolve()
    output = output if output.is_absolute() else root / output
    output = output.resolve()
    try:
        output.relative_to(working)
    except ValueError as exc:
        raise DriftAuditError("report output must be below the corpus working/ directory") from exc
    if output.exists() and not overwrite:
        raise DriftAuditError(f"refusing to overwrite existing report without --overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=HERE, help="corpus root")
    parser.add_argument(
        "--mode",
        choices=("collection", "final"),
        default="collection",
        help="collection permits PENDING; final requires zero unresolved items",
    )
    parser.add_argument("--window-start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--window-end", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("working/collection_drift_audit.json"),
        help="root-relative or absolute output below working/",
    )
    parser.add_argument("--overwrite", action="store_true", help="explicitly replace an existing report")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = audit_collection_drift(
            args.root,
            mode=args.mode,
            window_start=args.window_start,
            window_end=args.window_end,
        )
        write_report_atomic(args.root, args.output, report, overwrite=args.overwrite)
    except (DriftAuditError, OSError) as exc:
        print(f"collection drift audit error: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "overall_status": report["overall_status"],
                "release_gate_passed": report["release_gate_passed"],
                "summary": report["summary"],
                "report": str((args.output if args.output.is_absolute() else args.root / args.output).resolve()),
            },
            indent=2,
        )
    )
    return report["exit_code_contract"][report["overall_status"]]


if __name__ == "__main__":
    raise SystemExit(main())
