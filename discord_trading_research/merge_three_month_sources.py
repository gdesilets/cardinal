from __future__ import annotations

"""Merge completed Discord search segments into a non-destructive 3-month export.

The existing 14-day export is treated as an immutable input.  Completed segment
files are merged by Discord message ID.  Every source/query is retained in each
message's ``_merge_provenance`` object, and conflicting non-empty field values
are retained in ``field_variants`` instead of being discarded.
"""

import argparse
import copy
import datetime as dt
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "1.0.0"
DEFAULT_START = "2026-04-20"
DEFAULT_END = "2026-07-20"
DEFAULT_SEGMENT_DAYS = 2

COLLECTION_KEYS = (
    "primary_messages",
    "server_rejection_phrase_messages",
    "questions_rb_messages",
    "questions_nq_es_messages",
    "broad_rb_shorthand_partial_messages",
    "contextual_qa_messages",
    "instrument_comparison_messages",
)
SUPPLEMENTAL_PRIORITY = COLLECTION_KEYS[1:]
SUPPLEMENTAL_PREFIX_MAP = {
    "rbphrase": "server_rejection_phrase_messages",
    "questions_rb": "questions_rb_messages",
    "questions_nq_es": "instrument_comparison_messages",
    "instrument_rb_nq": "instrument_comparison_messages",
    "instrument_rb_es": "instrument_comparison_messages",
}
PROVENANCE_VARIANT_FIELDS = {
    "search_query",
    "result_index",
    "page_number",
    "displayed_time",
}
EMPTY_VALUES = (None, "", [], {})
SEGMENT_NAME_RE = re.compile(
    r"^(?P<prefix>.+?)_(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})"
    r"(?P<partial>\.partial)?\.json$"
)


class MergeError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # include the filename in all parse failures
        raise MergeError(f"Could not parse JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MergeError(f"Expected a JSON object in {path}")
    return value


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def nonempty(value: Any) -> bool:
    return value not in EMPTY_VALUES


def iso_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise MergeError(f"Invalid ISO date {value!r}") from exc


def expected_segments(start: str, end: str, span_days: int) -> list[dict[str, str]]:
    first = iso_date(start)
    last = iso_date(end)
    if first > last:
        raise MergeError("start date must not be after end date")
    if span_days < 1:
        raise MergeError("segment days must be positive")
    rows: list[dict[str, str]] = []
    cursor = first
    while cursor <= last:
        segment_end = min(cursor + dt.timedelta(days=span_days - 1), last)
        after = cursor - dt.timedelta(days=1)
        before = segment_end + dt.timedelta(days=1)
        rows.append(
            {
                "start": cursor.isoformat(),
                "end": segment_end.isoformat(),
                "expected_filename": f"primary_{cursor.isoformat()}_{segment_end.isoformat()}.json",
                "expected_query": f"in:premium-journals after:{after.isoformat()} before:{before.isoformat()}",
            }
        )
        cursor = segment_end + dt.timedelta(days=1)
    return rows


def infer_segment(path: Path, payload: dict[str, Any] | None) -> tuple[str | None, str | None]:
    segment = (payload or {}).get("segment")
    if isinstance(segment, dict):
        start = segment.get("start")
        end = segment.get("end")
        if isinstance(start, str) and isinstance(end, str):
            return start, end
    match = SEGMENT_NAME_RE.match(path.name)
    if match:
        return match.group("start"), match.group("end")
    return None, None


def timestamp_bounds(messages: Iterable[dict[str, Any]]) -> tuple[str | None, str | None]:
    values = sorted(
        str(row.get("timestamp_utc"))
        for row in messages
        if isinstance(row, dict) and row.get("timestamp_utc")
    )
    return (values[0], values[-1]) if values else (None, None)


def infer_complete_baseline_range(baseline: dict[str, Any]) -> dict[str, Any]:
    """Infer the inclusive calendar range covered by the complete baseline query.

    The original export records Discord's exclusive ``after`` and ``before``
    date boundaries in ``requested_window_*``.  Therefore a 2026-07-06 through
    2026-07-21 query covers message dates July 7 through July 20 inclusively.
    Actual primary-message bounds are retained as an independent cross-check.
    """

    metadata = baseline.get("metadata") if isinstance(baseline.get("metadata"), dict) else {}
    primary_rows = baseline.get("primary_messages") if isinstance(baseline.get("primary_messages"), list) else []
    actual_min, actual_max = timestamp_bounds(row for row in primary_rows if isinstance(row, dict))
    result: dict[str, Any] = {
        "query_complete": metadata.get("primary_search_complete") is True,
        "inclusive_start_date": None,
        "inclusive_end_date": None,
        "basis": None,
        "actual_min_timestamp_utc": actual_min,
        "actual_max_timestamp_utc": actual_max,
    }
    if not result["query_complete"]:
        result["basis"] = "baseline primary search is not declared complete"
        return result

    start_boundary = metadata.get("requested_window_start_date")
    end_boundary = metadata.get("requested_window_end_date")
    try:
        covered_start = iso_date(str(start_boundary)) + dt.timedelta(days=1)
        covered_end = iso_date(str(end_boundary)) - dt.timedelta(days=1)
    except MergeError:
        if not actual_min or not actual_max:
            result["basis"] = "complete baseline lacks usable date boundaries"
            return result
        covered_start = iso_date(actual_min[:10])
        covered_end = iso_date(actual_max[:10])
        result["basis"] = "actual primary message timestamp bounds"
    else:
        result["basis"] = "exclusive baseline after/before query boundaries converted to inclusive dates"

    if covered_start > covered_end:
        result["basis"] = "invalid baseline boundary order"
        return result
    result["inclusive_start_date"] = covered_start.isoformat()
    result["inclusive_end_date"] = covered_end.isoformat()
    return result


def message_score(row: dict[str, Any]) -> tuple[int, int, int]:
    nonempty_fields = sum(1 for key, value in row.items() if key != "_merge_provenance" and nonempty(value))
    text_size = sum(len(str(row.get(key) or "")) for key in ("content_text", "visible_text", "reply_context"))
    attachment_count = len(row.get("attachments") or []) if isinstance(row.get("attachments"), list) else 0
    return nonempty_fields, text_size, attachment_count


def unique_values(values: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    output: list[Any] = []
    for value in values:
        marker = compact(value)
        if marker not in seen:
            seen.add(marker)
            output.append(copy.deepcopy(value))
    return output


def merge_lists(values: Iterable[Any]) -> list[Any]:
    flattened: list[Any] = []
    for value in values:
        if isinstance(value, list):
            flattened.extend(value)
    return unique_values(flattened)


def merge_occurrences(message_id: str, occurrences: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    records = [item["record"] for item in occurrences]
    preferred = max(records, key=message_score)
    merged = copy.deepcopy(preferred)
    merged["message_id"] = message_id

    all_keys = sorted({key for record in records for key in record if key != "_merge_provenance"})
    variants: dict[str, list[Any]] = {}
    semantic_conflicts: dict[str, list[Any]] = {}
    provenance_variants: dict[str, list[Any]] = {}

    for key in all_keys:
        values = [record.get(key) for record in records if key in record and nonempty(record.get(key))]
        distinct = unique_values(values)
        if not distinct:
            merged.setdefault(key, None)
            continue
        if all(isinstance(value, list) for value in values):
            merged[key] = merge_lists(values)
        elif not nonempty(merged.get(key)):
            merged[key] = copy.deepcopy(distinct[0])
        if len(distinct) > 1:
            variants[key] = distinct
            if key in PROVENANCE_VARIANT_FIELDS:
                provenance_variants[key] = distinct
            else:
                semantic_conflicts[key] = distinct

    sources = unique_values(item["source"] for item in occurrences)
    queries = unique_values(
        source.get("query") for source in sources if isinstance(source, dict) and source.get("query")
    )
    collections = unique_values(
        source.get("collection") for source in sources if isinstance(source, dict) and source.get("collection")
    )
    merged["source_queries"] = queries
    merged["_merge_provenance"] = {
        "occurrence_count": len(occurrences),
        "sources": sources,
        "source_queries": queries,
        "source_collections": collections,
        "field_variants": variants,
    }

    detail = {
        "message_id": message_id,
        "occurrence_count": len(occurrences),
        "sources": sources,
        "semantic_conflicts": semantic_conflicts,
        "provenance_variants": provenance_variants,
    }
    return merged, detail


def source_descriptor(
    *, source_file: Path, collection: str, row: dict[str, Any], segment: dict[str, Any] | None, complete: bool
) -> dict[str, Any]:
    descriptor: dict[str, Any] = {
        "source_file": str(source_file.resolve()),
        "collection": collection,
        "query": row.get("search_query") or (segment or {}).get("query"),
        "result_index": row.get("result_index"),
        "page_number": row.get("page_number"),
        "complete_source": complete,
    }
    if segment:
        descriptor["segment_start"] = segment.get("start")
        descriptor["segment_end"] = segment.get("end")
    return descriptor


def inspect_segment_directory(segment_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid_files: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    if not segment_dir.exists():
        return valid_files, inventory

    for path in sorted(segment_dir.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        entry: dict[str, Any] = {
            "path": str(path.resolve()),
            "filename": path.name,
            "size_bytes": path.stat().st_size,
        }
        if path.suffix.lower() != ".json":
            entry["status"] = "ignored_non_json"
            inventory.append(entry)
            continue
        try:
            payload = load_json(path)
        except MergeError as exc:
            start, end = infer_segment(path, None)
            entry.update(
                {
                    "status": "invalid_json",
                    "error": str(exc),
                    "segment_start": start,
                    "segment_end": end,
                }
            )
            inventory.append(entry)
            continue
        start, end = infer_segment(path, payload)
        messages = payload.get("messages")
        is_partial_name = path.name.endswith(".partial.json")
        complete = payload.get("complete") is True and not is_partial_name
        rows = messages if isinstance(messages, list) else []
        ids = [str(row.get("message_id") or "") for row in rows if isinstance(row, dict) and row.get("message_id")]
        minimum, maximum = timestamp_bounds(row for row in rows if isinstance(row, dict))
        entry.update(
            {
                "status": "complete" if complete else "partial",
                "segment_start": start,
                "segment_end": end,
                "query": (payload.get("segment") or {}).get("query") if isinstance(payload.get("segment"), dict) else None,
                "reported_total": payload.get("reported_total"),
                "reported_pages": payload.get("reported_pages"),
                "pages_captured": payload.get("pages_captured"),
                "captured_rows_declared": payload.get("captured_rows"),
                "captured_rows_computed": len(rows),
                "unique_message_ids_declared": payload.get("unique_message_ids"),
                "unique_message_ids_computed": len(set(ids)),
                "duplicates_within_file": len(ids) - len(set(ids)),
                "gap_indices": payload.get("gap_indices") or [],
                "min_timestamp_utc": minimum,
                "max_timestamp_utc": maximum,
            }
        )
        if not isinstance(messages, list):
            entry["status"] = "invalid_schema"
            entry["error"] = "Top-level messages is not an array"
        elif complete:
            valid_files.append({"path": path, "payload": payload, "inventory": entry})
        inventory.append(entry)
    return valid_files, inventory


def supplemental_prefix(filename: str) -> str | None:
    for prefix in sorted(SUPPLEMENTAL_PREFIX_MAP, key=len, reverse=True):
        if filename.startswith(prefix + "_"):
            return prefix
    return None


def validate_completed_search_payload(payload: dict[str, Any]) -> list[str]:
    """Return strict validation errors for a purported completed search file."""

    errors: list[str] = []
    messages = payload.get("messages")
    if payload.get("complete") is not True:
        errors.append("complete is not true")
    if not isinstance(messages, list):
        return errors + ["messages is not an array"]
    if not isinstance(payload.get("segment"), dict):
        errors.append("segment metadata is missing")
    rows = [row for row in messages if isinstance(row, dict)]
    if len(rows) != len(messages):
        errors.append("messages contains non-object rows")
    ids = [str(row.get("message_id") or "") for row in rows]
    if any(not message_id for message_id in ids):
        errors.append("one or more rows lack message_id")
    if len(set(ids)) != len(ids):
        errors.append("duplicate message_id values within file")
    declared_rows = payload.get("captured_rows")
    if declared_rows is not None and int(declared_rows) != len(messages):
        errors.append(f"captured_rows={declared_rows} but messages={len(messages)}")
    declared_unique = payload.get("unique_message_ids")
    if declared_unique is not None and int(declared_unique) != len(set(ids)):
        errors.append(f"unique_message_ids={declared_unique} but computed={len(set(ids))}")
    reported_total = payload.get("reported_total")
    if reported_total is not None and int(reported_total) != len(messages):
        errors.append(f"reported_total={reported_total} but messages={len(messages)}")
    gaps = payload.get("gap_indices") or []
    if gaps:
        errors.append(f"gap_indices is non-empty ({len(gaps)} gaps)")
    reported_pages = payload.get("reported_pages")
    captured_pages = payload.get("pages_captured")
    if reported_pages is not None and captured_pages is not None and int(reported_pages) != int(captured_pages):
        errors.append(f"pages_captured={captured_pages} but reported_pages={reported_pages}")
    return errors


def inspect_supplemental_directory(
    supplemental_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    validated: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    if not supplemental_dir.exists():
        return validated, inventory

    for path in sorted(supplemental_dir.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        prefix = supplemental_prefix(path.name)
        entry: dict[str, Any] = {
            "path": str(path.resolve()),
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "prefix": prefix,
            "mapped_collection": SUPPLEMENTAL_PREFIX_MAP.get(prefix or ""),
        }
        if path.suffix.lower() != ".json":
            entry["status"] = "ignored_non_json"
            inventory.append(entry)
            continue
        if prefix is None:
            entry["status"] = "ignored_unrecognized_prefix"
            inventory.append(entry)
            continue
        try:
            payload = load_json(path)
        except MergeError as exc:
            start, end = infer_segment(path, None)
            entry.update(
                {
                    "status": "invalid_json",
                    "validation_errors": [str(exc)],
                    "segment_start": start,
                    "segment_end": end,
                }
            )
            inventory.append(entry)
            continue

        segment = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
        start, end = infer_segment(path, payload)
        messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
        ids = [str(row.get("message_id") or "") for row in messages if isinstance(row, dict)]
        minimum, maximum = timestamp_bounds(row for row in messages if isinstance(row, dict))
        validation_errors = validate_completed_search_payload(payload)
        is_partial = path.name.endswith(".partial.json") or payload.get("complete") is not True
        status = "partial" if is_partial else "validated_complete" if not validation_errors else "failed_validation"
        entry.update(
            {
                "status": status,
                "segment_start": start,
                "segment_end": end,
                "query": segment.get("query"),
                "reported_total": payload.get("reported_total"),
                "reported_pages": payload.get("reported_pages"),
                "pages_captured": payload.get("pages_captured"),
                "captured_rows_declared": payload.get("captured_rows"),
                "captured_rows_computed": len(messages),
                "unique_message_ids_declared": payload.get("unique_message_ids"),
                "unique_message_ids_computed": len(set(ids)),
                "duplicates_within_file": len(ids) - len(set(ids)),
                "gap_indices": payload.get("gap_indices") or [],
                "min_timestamp_utc": minimum,
                "max_timestamp_utc": maximum,
                "validation_errors": validation_errors,
            }
        )
        if status == "validated_complete":
            validated.append({"path": path, "payload": payload, "inventory": entry, "prefix": prefix})
        inventory.append(entry)
    return validated, inventory


def compress_missing_dates(dates: list[dt.date]) -> list[dict[str, str]]:
    if not dates:
        return []
    ordered = sorted(set(dates))
    output: list[dict[str, str]] = []
    start = previous = ordered[0]
    for value in ordered[1:]:
        if value != previous + dt.timedelta(days=1):
            output.append({"start": start.isoformat(), "end": previous.isoformat()})
            start = value
        previous = value
    output.append({"start": start.isoformat(), "end": previous.isoformat()})
    return output


def supplemental_coverage(
    validated: list[dict[str, Any]], start_date: str, end_date: str
) -> dict[str, Any]:
    required_start = iso_date(start_date)
    required_end = iso_date(end_date)
    required_dates = {
        required_start + dt.timedelta(days=offset)
        for offset in range((required_end - required_start).days + 1)
    }
    prefix_rows: list[dict[str, Any]] = []
    prefix_status: dict[str, bool] = {}
    for prefix, mapped_collection in SUPPLEMENTAL_PREFIX_MAP.items():
        files = [item for item in validated if item["prefix"] == prefix]
        covered: set[dt.date] = set()
        for item in files:
            start_text = item["inventory"].get("segment_start")
            end_text = item["inventory"].get("segment_end")
            if not start_text or not end_text:
                continue
            start = max(iso_date(str(start_text)), required_start)
            end = min(iso_date(str(end_text)), required_end)
            if start <= end:
                covered.update(start + dt.timedelta(days=offset) for offset in range((end - start).days + 1))
        missing = sorted(required_dates - covered)
        complete = not missing
        prefix_status[prefix] = complete
        prefix_rows.append(
            {
                "prefix": prefix,
                "mapped_collection": mapped_collection,
                "required_start_date": start_date,
                "required_end_date": end_date,
                "validated_file_count": len(files),
                "validated_files": [str(item["path"].resolve()) for item in files],
                "reported_result_count": sum(int(item["inventory"].get("reported_total") or 0) for item in files),
                "captured_row_count": sum(int(item["inventory"].get("captured_rows_computed") or 0) for item in files),
                "date_coverage_complete": complete,
                "missing_date_ranges": compress_missing_dates(missing),
            }
        )

    collection_rows: list[dict[str, Any]] = []
    for collection in sorted(set(SUPPLEMENTAL_PREFIX_MAP.values())):
        prefixes = [prefix for prefix, mapped in SUPPLEMENTAL_PREFIX_MAP.items() if mapped == collection]
        collection_rows.append(
            {
                "collection_name": collection,
                "prefixes": prefixes,
                "older_window_complete": all(prefix_status.get(prefix, False) for prefix in prefixes),
                "validated_file_count": sum(1 for item in validated if item["prefix"] in prefixes),
                "captured_row_count": sum(
                    int(item["inventory"].get("captured_rows_computed") or 0)
                    for item in validated
                    if item["prefix"] in prefixes
                ),
            }
        )
    return {
        "required_start_date": start_date,
        "required_end_date": end_date,
        "prefix_coverage": prefix_rows,
        "collection_coverage": collection_rows,
        "all_prefixes_complete": all(prefix_status.values()),
    }


def baseline_supplemental_completeness(metadata: dict[str, Any]) -> dict[str, bool]:
    result = {
        "server_rejection_phrase_messages": False,
        "questions_rb_messages": False,
        "questions_nq_es_messages": False,
    }
    searches = metadata.get("supplemental_searches") if isinstance(metadata, dict) else []
    for item in searches if isinstance(searches, list) else []:
        if not isinstance(item, dict) or item.get("complete") is not True:
            continue
        query = str(item.get("query") or "").lower()
        if "rejection block" in query and "questions" not in query:
            result["server_rejection_phrase_messages"] = True
        if "questions" in query and re.search(r"\brb\b", query):
            result["questions_rb_messages"] = True
        if "questions" in query and "nq" in query and "es" in query:
            result["questions_nq_es_messages"] = True
    return result


def reconcile_expected(
    expected: list[dict[str, str]], inventory: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_range: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in inventory:
        start, end = item.get("segment_start"), item.get("segment_end")
        if start and end:
            by_range[(start, end)].append(item)
    coverage: list[dict[str, Any]] = []
    for wanted in expected:
        matches = by_range.get((wanted["start"], wanted["end"]), [])
        complete = [item for item in matches if item.get("status") == "complete"]
        partial = [item for item in matches if item.get("status") == "partial"]
        invalid = [item for item in matches if item.get("status") in {"invalid_json", "invalid_schema"}]
        status = "complete" if complete else "partial" if partial else "invalid" if invalid else "missing"
        coverage.append(
            {
                **wanted,
                "status": status,
                "complete_files": [item["path"] for item in complete],
                "partial_files": [item["path"] for item in partial],
                "invalid_files": [item["path"] for item in invalid],
                "reported_result_count": sum(int(item.get("reported_total") or 0) for item in complete),
                "captured_row_count": sum(int(item.get("captured_rows_computed") or 0) for item in complete),
                "unique_message_count": sum(int(item.get("unique_message_ids_computed") or 0) for item in complete),
            }
        )
    return coverage


def build_merge(
    baseline_path: Path,
    segment_dir: Path,
    supplemental_dir: Path,
    start_date: str,
    end_date: str,
    segment_days: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    baseline = load_json(baseline_path)
    completed_files, inventory = inspect_segment_directory(segment_dir)
    validated_supplemental_files, supplemental_inventory = inspect_supplemental_directory(supplemental_dir)
    requested_start = iso_date(start_date)
    requested_end = iso_date(end_date)
    if requested_start > requested_end:
        raise MergeError("start date must not be after end date")

    baseline_range = infer_complete_baseline_range(baseline)
    baseline_start_text = baseline_range.get("inclusive_start_date")
    baseline_end_text = baseline_range.get("inclusive_end_date")
    baseline_covers_requested_tail = False
    segment_collection_end = requested_end
    if baseline_start_text and baseline_end_text:
        baseline_start = iso_date(str(baseline_start_text))
        baseline_end = iso_date(str(baseline_end_text))
        baseline_covers_requested_tail = baseline_start <= requested_end <= baseline_end
        if baseline_covers_requested_tail:
            segment_collection_end = min(requested_end, baseline_start - dt.timedelta(days=1))

    expected = (
        expected_segments(start_date, segment_collection_end.isoformat(), segment_days)
        if segment_collection_end >= requested_start
        else []
    )
    coverage = reconcile_expected(expected, inventory)
    supplemental_window_end = segment_collection_end if segment_collection_end >= requested_start else requested_start
    supplemental_coverage_report = supplemental_coverage(
        validated_supplemental_files, start_date, supplemental_window_end.isoformat()
    )

    occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)
    baseline_occurrences = 0
    baseline_collection_counts: dict[str, int] = {}
    for collection in COLLECTION_KEYS:
        rows = baseline.get(collection) or []
        if not isinstance(rows, list):
            raise MergeError(f"Baseline field {collection!r} must be an array")
        baseline_collection_counts[collection] = len(rows)
        for row in rows:
            if not isinstance(row, dict) or not row.get("message_id"):
                continue
            message_id = str(row["message_id"])
            occurrences[message_id].append(
                {
                    "record": row,
                    "source": source_descriptor(
                        source_file=baseline_path,
                        collection=collection,
                        row=row,
                        segment=None,
                        complete=True,
                    ),
                }
            )
            baseline_occurrences += 1

    segment_occurrences = 0
    segment_source_metadata: list[dict[str, Any]] = []
    for item in completed_files:
        path, payload = item["path"], item["payload"]
        segment = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
        rows = payload.get("messages") or []
        segment_source_metadata.append({key: value for key, value in payload.items() if key != "messages"})
        for row in rows:
            if not isinstance(row, dict) or not row.get("message_id"):
                continue
            message_id = str(row["message_id"])
            occurrences[message_id].append(
                {
                    "record": row,
                    "source": source_descriptor(
                        source_file=path,
                        collection="primary_messages",
                        row=row,
                        segment=segment,
                        complete=True,
                    ),
                }
            )
            segment_occurrences += 1

    supplemental_occurrences = 0
    supplemental_source_metadata: list[dict[str, Any]] = []
    supplemental_occurrences_by_prefix: Counter[str] = Counter()
    supplemental_occurrences_by_collection: Counter[str] = Counter()
    for item in validated_supplemental_files:
        path, payload, prefix = item["path"], item["payload"], item["prefix"]
        collection = SUPPLEMENTAL_PREFIX_MAP[prefix]
        segment = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
        rows = payload.get("messages") or []
        supplemental_source_metadata.append(
            {
                "source_file": str(path.resolve()),
                "prefix": prefix,
                "mapped_collection": collection,
                **{key: value for key, value in payload.items() if key != "messages"},
            }
        )
        for row in rows:
            if not isinstance(row, dict) or not row.get("message_id"):
                continue
            message_id = str(row["message_id"])
            occurrences[message_id].append(
                {
                    "record": row,
                    "source": source_descriptor(
                        source_file=path,
                        collection=collection,
                        row=row,
                        segment=segment,
                        complete=True,
                    ),
                }
            )
            supplemental_occurrences += 1
            supplemental_occurrences_by_prefix[prefix] += 1
            supplemental_occurrences_by_collection[collection] += 1

    merged_by_id: dict[str, dict[str, Any]] = {}
    duplicate_details: list[dict[str, Any]] = []
    semantic_conflict_fields: Counter[str] = Counter()
    provenance_variant_fields: Counter[str] = Counter()
    for message_id, items in occurrences.items():
        merged, detail = merge_occurrences(message_id, items)
        merged_by_id[message_id] = merged
        if len(items) > 1:
            duplicate_details.append(detail)
        semantic_conflict_fields.update(detail["semantic_conflicts"].keys())
        provenance_variant_fields.update(detail["provenance_variants"].keys())

    output_collections: dict[str, list[dict[str, Any]]] = {key: [] for key in COLLECTION_KEYS}
    for message_id, row in merged_by_id.items():
        memberships = row["_merge_provenance"]["source_collections"]
        if "primary_messages" in memberships:
            destination = "primary_messages"
        else:
            destination = next((key for key in SUPPLEMENTAL_PRIORITY if key in memberships), SUPPLEMENTAL_PRIORITY[0])
        output_collections[destination].append(row)
    for rows in output_collections.values():
        rows.sort(key=lambda row: (str(row.get("timestamp_utc") or ""), str(row.get("message_id") or "")))

    all_messages = list(merged_by_id.values())
    primary_min, primary_max = timestamp_bounds(output_collections["primary_messages"])
    all_min, all_max = timestamp_bounds(all_messages)
    complete_ranges = [item for item in coverage if item["status"] == "complete"]
    missing_ranges = [item for item in coverage if item["status"] == "missing"]
    partial_ranges = [item for item in coverage if item["status"] == "partial"]
    invalid_ranges = [item for item in coverage if item["status"] == "invalid"]
    all_expected_complete = len(complete_ranges) == len(expected)

    generated_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    manifest_name = "three_month_coverage_manifest.json"
    output_name = "raw_discord_export_3month.json"
    baseline_metadata = copy.deepcopy(baseline.get("metadata") or {})
    baseline_supplemental_complete = baseline_supplemental_completeness(baseline_metadata)
    supplemental_collection_status = {
        item["collection_name"]: item
        for item in supplemental_coverage_report["collection_coverage"]
    }
    total_occurrences = baseline_occurrences + segment_occurrences + supplemental_occurrences
    collection_coverage: list[dict[str, Any]] = []
    for collection in COLLECTION_KEYS:
        rows = baseline.get(collection) or []
        queries = unique_values(
            row.get("search_query") for row in rows if isinstance(row, dict) and row.get("search_query")
        )
        queries.extend(
            query
            for query in unique_values(
                (item["payload"].get("segment") or {}).get("query")
                for item in validated_supplemental_files
                if SUPPLEMENTAL_PREFIX_MAP[item["prefix"]] == collection
                and isinstance(item["payload"].get("segment"), dict)
            )
            if query not in queries
        )
        older_complete = bool(
            supplemental_collection_status.get(collection, {}).get("older_window_complete")
        )
        baseline_tail_complete = bool(baseline_supplemental_complete.get(collection))
        if collection == "primary_messages":
            queries.extend(
                query
                for query in unique_values(
                    (item["payload"].get("segment") or {}).get("query")
                    for item in completed_files
                    if isinstance(item["payload"].get("segment"), dict)
                )
                if query not in queries
            )
            scan_complete = all_expected_complete
            gaps = [
                f"{item['start']} through {item['end']}: {item['status']}"
                for item in coverage
                if item["status"] != "complete"
            ]
        elif collection in {"server_rejection_phrase_messages", "questions_rb_messages"}:
            scan_complete = older_complete and baseline_tail_complete and baseline_covers_requested_tail
            gaps = []
            if not older_complete:
                gaps.append("One or more older-window supplemental prefixes have date gaps.")
            if not baseline_tail_complete:
                gaps.append("The baseline tail search is not declared complete.")
        elif collection == "instrument_comparison_messages":
            scan_complete = False
            gaps = [] if older_complete else ["One or more older-window instrument prefixes have date gaps."]
            gaps.append(
                "The older-window instrument searches are complete, but equivalent instrument_rb_nq and "
                "instrument_rb_es searches were not captured for the baseline July 7-20 tail."
            )
        elif collection == "questions_nq_es_messages":
            scan_complete = False
            gaps = [
                "This collection intentionally preserves the baseline July 7-20 search separately; "
                "the older questions_nq_es search is stored in instrument_comparison_messages."
            ]
        else:
            scan_complete = False
            gaps = ["Supplemental collection was not expanded beyond the baseline export."]
        collection_coverage.append(
            {
                "collection_name": collection,
                "queries": queries,
                "scan_complete": scan_complete,
                "declared_messages_seen": len(output_collections[collection]),
                "baseline_messages_seen": len(rows),
                "supplemental_source_occurrences": int(supplemental_occurrences_by_collection.get(collection, 0)),
                "older_window_complete": older_complete,
                "baseline_tail_complete": baseline_tail_complete,
                "gap_notes": gaps,
            }
        )

    # Preserve the original metadata values verbatim.  The expanded window and
    # its independent completeness state live under metadata.merge so callers
    # cannot mistake baseline completeness for three-month completeness.
    metadata = copy.deepcopy(baseline_metadata)
    metadata.update(
        {
            "merge": {
                "schema_version": SCHEMA_VERSION,
                "generated_at_utc": generated_at,
                "requested_window_start_date": start_date,
                "requested_window_end_date": end_date,
                "baseline_input": str(baseline_path.resolve()),
                "segment_directory": str(segment_dir.resolve()),
                "supplemental_directory": str(supplemental_dir.resolve()),
                "segment_days": segment_days,
                "segment_collection_start_date": start_date if expected else None,
                "segment_collection_end_date": segment_collection_end.isoformat() if expected else None,
                "baseline_tail_coverage": baseline_range,
                "baseline_covers_requested_tail": baseline_covers_requested_tail,
                "expected_segments": len(expected),
                "completed_segments": len(complete_ranges),
                "partial_segments": len(partial_ranges),
                "missing_segments": len(missing_ranges),
                "invalid_segments": len(invalid_ranges),
                "all_expected_segments_complete": all_expected_complete,
                "completed_segment_files_ingested": [str(item["path"].resolve()) for item in completed_files],
                "supplemental_validated_files_ingested": [
                    str(item["path"].resolve()) for item in validated_supplemental_files
                ],
                "supplemental_validated_file_count": len(validated_supplemental_files),
                "supplemental_message_occurrences": supplemental_occurrences,
                "supplemental_occurrences_by_prefix": dict(supplemental_occurrences_by_prefix),
                "supplemental_occurrences_by_collection": dict(supplemental_occurrences_by_collection),
                "supplemental_coverage": supplemental_coverage_report,
                "message_occurrences": total_occurrences,
                "unique_messages": len(merged_by_id),
                "duplicate_occurrences_collapsed": total_occurrences - len(merged_by_id),
                "messages_with_semantic_conflicts": sum(
                    1 for item in duplicate_details if item.get("semantic_conflicts")
                ),
                "primary_messages_output": len(output_collections["primary_messages"]),
                "collection_coverage": collection_coverage,
                "coverage_manifest": manifest_name,
                "preservation_note": (
                    "Messages are globally unique by message_id across output collections. "
                    "All input collections, files, queries, and conflicting field values are retained "
                    "under each message's _merge_provenance object."
                ),
            },
            "source_metadata": {
                "baseline": copy.deepcopy(baseline_metadata),
                "completed_segments": segment_source_metadata,
                "completed_supplemental": supplemental_source_metadata,
            },
        }
    )

    merged_export: dict[str, Any] = {"metadata": metadata, **output_collections}
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "discord_three_month_merge_coverage",
        "generated_at_utc": generated_at,
        "configured_window": {
            "start_date": start_date,
            "end_date": end_date,
            "segment_days": segment_days,
            "expected_segment_count": len(expected),
            "segment_collection_start_date": start_date if expected else None,
            "segment_collection_end_date": segment_collection_end.isoformat() if expected else None,
            "baseline_tail_coverage": baseline_range,
            "baseline_covers_requested_tail": baseline_covers_requested_tail,
        },
        "intended_outputs": {"merged_export": output_name, "coverage_manifest": manifest_name},
        "baseline": {
            "path": str(baseline_path.resolve()),
            "collection_counts": baseline_collection_counts,
            "message_occurrences": baseline_occurrences,
            "unique_message_ids": len(
                {
                    str(row.get("message_id"))
                    for key in COLLECTION_KEYS
                    for row in (baseline.get(key) or [])
                    if isinstance(row, dict) and row.get("message_id")
                }
            ),
            "metadata": copy.deepcopy(baseline.get("metadata") or {}),
        },
        "segment_file_inventory": inventory,
        "supplemental_file_inventory": supplemental_inventory,
        "supplemental_coverage": supplemental_coverage_report,
        "date_segment_coverage": coverage,
        "missing_segment_files": [item for item in coverage if item["status"] == "missing"],
        "partial_segment_files": [item for item in coverage if item["status"] == "partial"],
        "invalid_segment_files": [item for item in coverage if item["status"] == "invalid"],
        "coverage_summary": {
            "all_expected_segments_complete": all_expected_complete,
            "completed_segments": len(complete_ranges),
            "partial_segments": len(partial_ranges),
            "missing_segments": len(missing_ranges),
            "invalid_segments": len(invalid_ranges),
            "completed_files_ingested": len(completed_files),
            "segment_reported_results": sum(int(item["inventory"].get("reported_total") or 0) for item in completed_files),
            "segment_captured_rows": segment_occurrences,
            "supplemental_validated_files": len(validated_supplemental_files),
            "supplemental_message_occurrences": supplemental_occurrences,
            "supplemental_occurrences_by_prefix": dict(supplemental_occurrences_by_prefix),
            "supplemental_occurrences_by_collection": dict(supplemental_occurrences_by_collection),
            "supplemental_all_prefixes_complete": supplemental_coverage_report["all_prefixes_complete"],
            "message_occurrences_all_inputs": total_occurrences,
            "unique_messages_output": len(merged_by_id),
            "duplicate_occurrences_collapsed": total_occurrences - len(merged_by_id),
            "duplicate_message_ids": len(duplicate_details),
            "messages_with_semantic_conflicts": sum(
                1 for item in duplicate_details if item.get("semantic_conflicts")
            ),
            "semantic_conflict_field_counts": dict(semantic_conflict_fields),
            "provenance_variant_field_counts": dict(provenance_variant_fields),
            "output_collection_counts": {key: len(rows) for key, rows in output_collections.items()},
            "min_timestamp_utc_all_messages": all_min,
            "max_timestamp_utc_all_messages": all_max,
            "min_timestamp_utc_primary_messages": primary_min,
            "max_timestamp_utc_primary_messages": primary_max,
        },
        "duplicate_and_conflict_details": sorted(
            duplicate_details, key=lambda item: (-int(item["occurrence_count"]), item["message_id"])
        ),
        "validation": {
            "output_message_ids_globally_unique": sum(len(rows) for rows in output_collections.values())
            == len(merged_by_id),
            "every_output_message_has_provenance": all("_merge_provenance" in row for row in all_messages),
            "every_output_message_has_message_id": all(bool(row.get("message_id")) for row in all_messages),
            "completed_files_only_ingested": all(item["payload"].get("complete") is True for item in completed_files),
            "supplemental_files_strictly_validated_before_ingest": all(
                not validate_completed_search_payload(item["payload"])
                for item in validated_supplemental_files
            ),
        },
        "limitations": [
            "Partial checkpoint files are inventoried but never ingested.",
            "A semantic conflict means two non-empty serialized values differed; it does not imply either value is wrong.",
            "Search result counts can overlap across queries, so unique message counts are the reliable merged denominator.",
            "Discord post timestamps describe message time, not necessarily the setup time discussed in the message.",
        ],
    }
    return merged_export, manifest


def write_exclusive(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise MergeError(f"Refusing to overwrite existing artifact: {path}") from exc


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=base_dir / "raw_discord_export.json")
    parser.add_argument("--segments", type=Path, default=base_dir / "three_month_segments")
    parser.add_argument(
        "--supplemental-dir",
        type=Path,
        default=base_dir / "three_month_supplemental",
        help="Directory containing completed rbphrase/questions/instrument supplemental searches.",
    )
    parser.add_argument("--output", type=Path, default=base_dir / "raw_discord_export_3month.json")
    parser.add_argument("--manifest", type=Path, default=base_dir / "three_month_coverage_manifest.json")
    parser.add_argument("--start-date", default=DEFAULT_START)
    parser.add_argument("--end-date", default=DEFAULT_END)
    parser.add_argument("--segment-days", type=int, default=DEFAULT_SEGMENT_DAYS)
    parser.add_argument("--allow-incomplete", action="store_true", help="Write explicitly partial outputs when expected segments are missing.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print coverage without writing artifacts.")
    args = parser.parse_args()

    try:
        if not args.baseline.is_file():
            raise MergeError(f"Baseline export not found: {args.baseline}")
        if not args.dry_run:
            for path in (args.output, args.manifest):
                if path.exists():
                    raise MergeError(f"Refusing to overwrite existing artifact: {path}")
        merged, manifest = build_merge(
            args.baseline.resolve(),
            args.segments.resolve(),
            args.supplemental_dir.resolve(),
            args.start_date,
            args.end_date,
            args.segment_days,
        )
        summary = manifest["coverage_summary"]
        if not summary["all_expected_segments_complete"] and not (args.allow_incomplete or args.dry_run):
            raise MergeError(
                "Coverage is incomplete; no artifacts were written. Complete the missing/partial segments, "
                "or pass --allow-incomplete to create explicitly partial outputs."
            )
        if not args.dry_run:
            # Preflight both names before either exclusive write.
            if args.output.exists() or args.manifest.exists():
                raise MergeError("An output appeared during preflight; refusing to overwrite it.")
            write_exclusive(args.output, merged)
            write_exclusive(args.manifest, manifest)
        print(
            json.dumps(
                {
                    "dry_run": args.dry_run,
                    "output_written": not args.dry_run,
                    "output": str(args.output.resolve()),
                    "manifest": str(args.manifest.resolve()),
                    **summary,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except MergeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
