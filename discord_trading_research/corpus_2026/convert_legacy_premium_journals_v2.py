#!/usr/bin/env python3
"""Stage validated legacy premium-journals searches in the canonical-v2 shape.

This is a provenance migration, not a recapture and not an analysis pass.  It
reads only the already-created Discord artifacts, preserves every source row
and its complete original payload, derives the canonical creation timestamp
from the Discord message snowflake, and records uncertainty without repairing
or inferring missing thread/permalink evidence.

The default destination is deliberately outside ``raw/channel_segments``.
The migration refuses an existing destination so a previous staging run can
never be silently overwritten.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse


DISCORD_EPOCH_MS = 1_420_070_400_000
GUILD_ID = "1167376964680691732"
CHANNEL_ID = "1283941772577472643"
CHANNEL_NAME = "premium-journals"
CHANNEL_KIND = "text channel"
CATEGORY_NAME = "PREMIUM"
UTC = dt.timezone.utc
# The entire migration scope (2026-04-20 through 2026-07-20) is inside US
# Central daylight time.  A fixed offset keeps this local-only utility runnable
# on Windows Python installations that do not ship the optional IANA tzdata
# package, without changing any date boundary in the scoped source material.
CENTRAL = dt.timezone(dt.timedelta(hours=-5), name="CDT")
MIGRATOR_VERSION = "legacy-premium-journals-canonical-v2/1.0.0"

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_THREE_MONTH_DIR = PROJECT_DIR / "three_month_segments"
DEFAULT_BASELINE_PATH = PROJECT_DIR / "raw_discord_export.json"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "staging" / "legacy_premium_journals_v2"
PROTECTED_RAW_SEGMENT_DIR = SCRIPT_DIR / "raw" / "channel_segments"

DEFAULT_WINDOW_START = dt.date(2026, 4, 20)
DEFAULT_SEGMENT_END = dt.date(2026, 7, 6)
DEFAULT_TAIL_START = dt.date(2026, 7, 7)
DEFAULT_WINDOW_END = dt.date(2026, 7, 20)

MESSAGE_ID_RE = re.compile(r"^[0-9]{17,20}$")
SEGMENT_FILE_RE = re.compile(
    r"^primary_(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})\.json$"
)
RENDERED_TIMESTAMP_RE = re.compile(
    r"(?P<stamp>(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"\d{1,2},\s+\d{4}\s+at\s+\d{1,2}:\d{2}\s+(?:AM|PM))",
    re.IGNORECASE,
)


class MigrationError(RuntimeError):
    """Raised when an input or staging safety invariant is violated."""


@dataclasses.dataclass(frozen=True)
class MigrationWindow:
    start: dt.date = DEFAULT_WINDOW_START
    segment_end: dt.date = DEFAULT_SEGMENT_END
    tail_start: dt.date = DEFAULT_TAIL_START
    end: dt.date = DEFAULT_WINDOW_END

    def validate(self) -> None:
        if not (self.start <= self.segment_end < self.tail_start <= self.end):
            raise MigrationError(f"invalid migration window: {self!r}")
        if self.segment_end + dt.timedelta(days=1) != self.tail_start:
            raise MigrationError("legacy segment coverage and baseline tail must be contiguous")


@dataclasses.dataclass
class SourceSegment:
    path: Path
    source_kind: str
    source_collection: str
    start: dt.date
    end: dt.date
    query: str
    complete: bool
    reported_total: int
    reported_pages: int
    pages_captured: int
    captured_rows: int
    unique_message_ids: int
    gap_indices: list[int]
    messages: list[dict[str, Any]]
    captured_at_utc: str | None = None


def parse_date(value: Any, label: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise MigrationError(f"{label} is not an ISO date: {value!r}") from exc


def parse_timestamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso_z(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    value = value.astimezone(UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def snowflake_datetime(message_id: Any) -> dt.datetime | None:
    text = str(message_id or "")
    if not MESSAGE_ID_RE.fullmatch(text):
        return None
    try:
        milliseconds = DISCORD_EPOCH_MS + (int(text) >> 22)
        return dt.datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def snowflake_id_for_datetime(value: dt.datetime, increment: int = 0) -> str:
    """Test/helper inverse for a Discord snowflake creation timestamp."""

    utc = value.astimezone(UTC)
    milliseconds = math.floor(utc.timestamp() * 1000)
    if milliseconds < DISCORD_EPOCH_MS:
        raise ValueError("Discord snowflakes cannot predate the Discord epoch")
    return str(((milliseconds - DISCORD_EPOCH_MS) << 22) | (increment & ((1 << 22) - 1)))


def normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).casefold()


def stable_sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_source_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_DIR.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"cannot read JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MigrationError(f"expected a JSON object in {path}")
    return value


def int_value(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MigrationError(f"{label} is not an integer: {value!r}") from exc
    if parsed < 0:
        raise MigrationError(f"{label} cannot be negative: {parsed}")
    return parsed


def inclusive_dates(start: dt.date, end: dt.date) -> set[dt.date]:
    if end < start:
        return set()
    return {
        start + dt.timedelta(days=offset)
        for offset in range((end - start).days + 1)
    }


def validate_source_segment(source: SourceSegment) -> None:
    errors: list[str] = []
    if not source.complete:
        errors.append("source_not_declared_complete")
    if source.captured_rows != len(source.messages):
        errors.append(
            f"captured_rows={source.captured_rows} but messages={len(source.messages)}"
        )
    ids = [str(row.get("message_id") or "") for row in source.messages]
    if source.unique_message_ids != len(set(ids)):
        errors.append(
            f"unique_message_ids={source.unique_message_ids} but observed={len(set(ids))}"
        )
    if source.reported_total != source.captured_rows:
        errors.append(
            f"reported_total={source.reported_total} but captured_rows={source.captured_rows}"
        )
    if source.gap_indices:
        errors.append(f"source declares gap indices: {source.gap_indices[:20]!r}")
    result_indices = [
        int(row["result_index"])
        for row in source.messages
        if row.get("result_index") is not None
    ]
    if len(result_indices) != source.captured_rows:
        errors.append("one or more source rows lack result_index")
    elif set(result_indices) != set(range(1, source.reported_total + 1)):
        errors.append("result_index values are not the exact reported 1..N set")
    page_numbers = {
        int(row["page_number"])
        for row in source.messages
        if row.get("page_number") is not None
    }
    if len(page_numbers) != source.pages_captured:
        errors.append(
            f"pages_captured={source.pages_captured} but observed pages={len(page_numbers)}"
        )
    invalid_ids = [message_id for message_id in ids if snowflake_datetime(message_id) is None]
    if invalid_ids:
        errors.append(f"invalid Discord snowflakes: {invalid_ids[:10]!r}")
    if errors:
        raise MigrationError(f"invalid validated source {source.path}: " + "; ".join(errors))


def load_three_month_segments(directory: Path, window: MigrationWindow) -> list[SourceSegment]:
    sources: list[SourceSegment] = []
    for path in sorted(directory.glob("primary_*.json")):
        match = SEGMENT_FILE_RE.fullmatch(path.name)
        if not match:
            continue
        payload = load_json_object(path)
        segment = payload.get("segment")
        if not isinstance(segment, dict):
            raise MigrationError(f"missing segment object in {path}")
        start = parse_date(segment.get("start"), f"{path.name} segment.start")
        end = parse_date(segment.get("end"), f"{path.name} segment.end")
        if start.isoformat() != match.group("start") or end.isoformat() != match.group("end"):
            raise MigrationError(f"filename/segment date mismatch in {path}")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not all(isinstance(row, dict) for row in messages):
            raise MigrationError(f"messages is not an object array in {path}")
        source = SourceSegment(
            path=path,
            source_kind="validated_three_month_primary_segment",
            source_collection="primary_messages",
            start=start,
            end=end,
            query=str(segment.get("query") or ""),
            complete=payload.get("complete") is True,
            reported_total=int_value(payload.get("reported_total"), "reported_total"),
            reported_pages=int_value(payload.get("reported_pages"), "reported_pages"),
            pages_captured=int_value(payload.get("pages_captured"), "pages_captured"),
            captured_rows=int_value(payload.get("captured_rows"), "captured_rows"),
            unique_message_ids=int_value(
                payload.get("unique_message_ids"), "unique_message_ids"
            ),
            gap_indices=[int(value) for value in (payload.get("gap_indices") or [])],
            messages=messages,
            captured_at_utc=(
                str(payload.get("captured_at_utc"))
                if payload.get("captured_at_utc")
                else None
            ),
        )
        validate_source_segment(source)
        sources.append(source)

    if not sources:
        raise MigrationError(f"no validated primary segment files found in {directory}")
    seen: set[dt.date] = set()
    overlaps: set[dt.date] = set()
    for source in sources:
        dates = inclusive_dates(source.start, source.end)
        overlaps.update(seen & dates)
        seen.update(dates)
    expected = inclusive_dates(window.start, window.segment_end)
    if seen != expected or overlaps:
        missing = sorted(expected - seen)
        extra = sorted(seen - expected)
        raise MigrationError(
            "three-month segment date coverage mismatch: "
            f"missing={missing!r}; extra={extra!r}; overlaps={sorted(overlaps)!r}"
        )
    return sources


def load_baseline_tail(path: Path, window: MigrationWindow) -> SourceSegment:
    payload = load_json_object(path)
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise MigrationError(f"baseline metadata is missing in {path}")
    messages = payload.get("primary_messages")
    if not isinstance(messages, list) or not all(isinstance(row, dict) for row in messages):
        raise MigrationError(f"baseline primary_messages is not an object array in {path}")
    if metadata.get("primary_search_complete") is not True:
        raise MigrationError("baseline primary search is not declared complete")
    query_values = {str(row.get("search_query") or "") for row in messages}
    if len(query_values) != 1:
        raise MigrationError(f"baseline primary rows have multiple queries: {sorted(query_values)!r}")
    query = next(iter(query_values), "")
    reported_total = int_value(metadata.get("primary_result_count"), "primary_result_count")
    page_numbers = {
        int(row["page_number"])
        for row in messages
        if row.get("page_number") is not None
    }
    source = SourceSegment(
        path=path,
        source_kind="validated_baseline_tail_primary_collection",
        source_collection="primary_messages",
        start=window.tail_start,
        end=window.end,
        query=query,
        complete=True,
        reported_total=reported_total,
        reported_pages=max(page_numbers, default=0),
        pages_captured=len(page_numbers),
        captured_rows=len(messages),
        unique_message_ids=len({str(row.get("message_id") or "") for row in messages}),
        gap_indices=[],
        messages=messages,
        captured_at_utc=(
            str(metadata.get("collected_at_utc"))
            if metadata.get("collected_at_utc")
            else None
        ),
    )
    validate_source_segment(source)
    if str(metadata.get("primary_channel_id") or "") != CHANNEL_ID:
        raise MigrationError("baseline primary channel ID does not match premium-journals")
    if str(metadata.get("primary_channel_name") or "") != CHANNEL_NAME:
        raise MigrationError("baseline primary channel name does not match premium-journals")
    return source


def rendered_timestamps(value: Any) -> list[dt.datetime]:
    if not isinstance(value, str):
        return []
    parsed: list[dt.datetime] = []
    for match in RENDERED_TIMESTAMP_RE.finditer(value):
        stamp = match.group("stamp")
        try:
            local = dt.datetime.strptime(stamp, "%A, %B %d, %Y at %I:%M %p")
        except ValueError:
            continue
        parsed.append(local.replace(tzinfo=CENTRAL).astimezone(UTC))
    return parsed


def contamination_audit(
    message: dict[str, Any], canonical_timestamp: dt.datetime | None
) -> tuple[dict[str, Any], list[str]]:
    captured = parse_timestamp(message.get("timestamp_utc"))
    discrepancy_ms: int | None = None
    if captured is not None and canonical_timestamp is not None:
        discrepancy_ms = round(abs((captured - canonical_timestamp).total_seconds() * 1000))

    content = str(message.get("content_text") or "")
    content_normalized = normalize_text(content)
    reply_blobs = [
        str(message.get(key) or "")
        for key in ("reply_context", "reply_to_content")
        if str(message.get(key) or "").strip()
    ]
    reply_duplicate = False
    if len(content_normalized) >= 12:
        for blob in reply_blobs:
            normalized = normalize_text(blob)
            if content_normalized in normalized or (
                len(normalized) >= 12 and normalized in content_normalized
            ):
                reply_duplicate = True
                break

    content_stamps = rendered_timestamps(content)
    reply_stamps = [stamp for blob in reply_blobs for stamp in rendered_timestamps(blob)]
    captured_matches_reply_stamp = bool(
        captured is not None
        and any(abs((captured - stamp).total_seconds()) < 60 for stamp in reply_stamps)
    )

    reasons: list[str] = []
    if captured is None:
        reasons.append("legacy_captured_timestamp_unparseable")
    if discrepancy_ms is not None and discrepancy_ms > 1000:
        reasons.append("legacy_captured_timestamp_snowflake_mismatch_gt_1000ms")
    if reply_duplicate:
        reasons.append("reply_preview_content_contamination_suspected")
    if captured_matches_reply_stamp or (
        reply_duplicate and discrepancy_ms is not None and discrepancy_ms > 1000
    ):
        reasons.append("reply_preview_timestamp_contamination_suspected")
    if content_stamps:
        reasons.append("rendered_timestamp_embedded_in_content")

    audit = {
        "legacy_captured_timestamp_utc": (
            str(message.get("timestamp_utc")) if message.get("timestamp_utc") is not None else None
        ),
        "canonical_snowflake_timestamp_utc": iso_z(canonical_timestamp),
        "timestamp_discrepancy_ms": discrepancy_ms,
        "content_duplicates_reply_preview": reply_duplicate,
        "rendered_timestamp_count_in_content": len(content_stamps),
        "rendered_timestamp_count_in_reply_preview": len(reply_stamps),
        "legacy_captured_timestamp_matches_reply_preview_minute": (
            captured_matches_reply_stamp
        ),
    }
    return audit, sorted(set(reasons))


def valid_snowflake_id(value: Any) -> str | None:
    text = str(value or "")
    return text if MESSAGE_ID_RE.fullmatch(text) else None


def parse_discord_permalink(value: Any, message_id: str) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.hostname not in {
        "discord.com",
        "canary.discord.com",
        "ptb.discord.com",
    }:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 4 or parts[0] != "channels":
        return None
    guild_id, channel_id, permalink_message_id = parts[1:]
    if (
        guild_id != GUILD_ID
        or not valid_snowflake_id(channel_id)
        or permalink_message_id != message_id
    ):
        return None
    return {
        "url": value.strip(),
        "guild_id": guild_id,
        "thread_channel_id": channel_id,
        "message_id": permalink_message_id,
    }


def locator_audit(message: dict[str, Any], message_id: str) -> tuple[dict[str, Any], list[str]]:
    explicit_thread_id = valid_snowflake_id(
        message.get("thread_channel_id") or message.get("exact_thread_channel_id")
    )
    explicit_permalink_value = message.get("permalink") or message.get("message_url")
    exact_permalink = parse_discord_permalink(explicit_permalink_value, message_id)
    if exact_permalink and explicit_thread_id and (
        exact_permalink["thread_channel_id"] != explicit_thread_id
    ):
        exact_permalink = None

    inferred_thread_id = valid_snowflake_id(message.get("inferred_thread_channel_id"))
    inferred_permalink_value = message.get("inferred_permalink")
    inferred_permalink = parse_discord_permalink(inferred_permalink_value, message_id)
    attachment_thread_ids = sorted(
        {
            thread_id
            for attachment in (message.get("attachments") or [])
            if isinstance(attachment, dict)
            for thread_id in [valid_snowflake_id(attachment.get("thread_channel_id"))]
            if thread_id
        }
    )

    reasons: list[str] = []
    if explicit_thread_id is None:
        reasons.append("exact_thread_id_unavailable")
    if exact_permalink is None:
        reasons.append("exact_permalink_unavailable")
    if inferred_permalink_value and inferred_permalink is None:
        reasons.append("inferred_permalink_invalid")
    if message.get("inferred_thread_channel_id") and inferred_thread_id is None:
        reasons.append("inferred_thread_id_invalid")

    thread_confidence = "exact" if explicit_thread_id else (
        "inferred" if inferred_thread_id or attachment_thread_ids else "unavailable"
    )
    permalink_confidence = "exact" if exact_permalink else (
        "inferred" if inferred_permalink else "invalid" if inferred_permalink_value else "unavailable"
    )
    audit = {
        "exact_thread_channel_id": explicit_thread_id,
        "exact_permalink": exact_permalink["url"] if exact_permalink else None,
        "inferred_thread_channel_id_preserved": inferred_thread_id,
        "inferred_permalink_preserved": (
            str(inferred_permalink_value) if inferred_permalink_value is not None else None
        ),
        "attachment_thread_channel_ids": attachment_thread_ids,
        "thread_locator_confidence": thread_confidence,
        "permalink_confidence": permalink_confidence,
        "inferred_values_promoted_to_exact": False,
    }
    return audit, sorted(set(reasons))


def occurrence_id(
    source_relative_path: str, collection: str, row_index: int, message_id: str
) -> str:
    seed = f"{source_relative_path}\0{collection}\0{row_index}\0{message_id}".encode("utf-8")
    return "legacy_occ_" + hashlib.sha256(seed).hexdigest()[:32]


def convert_message(
    original: dict[str, Any],
    *,
    source: SourceSegment,
    source_relative_path: str,
    source_sha256: str,
    row_index: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    original_copy = copy.deepcopy(original)
    message_id = str(original.get("message_id") or "")
    canonical_timestamp = snowflake_datetime(message_id)
    contamination, contamination_reasons = contamination_audit(
        original, canonical_timestamp
    )
    locator, locator_reasons = locator_audit(original, message_id)
    reasons = set(contamination_reasons) | set(locator_reasons)

    if canonical_timestamp is None:
        reasons.add("invalid_discord_message_snowflake")
    else:
        local_date = canonical_timestamp.astimezone(CENTRAL).date()
        if not (source.start <= local_date <= source.end):
            reasons.add("snowflake_timestamp_outside_declared_source_segment")
    if str(original.get("search_query") or "") != source.query:
        reasons.add("row_search_query_mismatch")
    if str(original.get("parent_channel") or "") != CHANNEL_NAME:
        reasons.add("row_parent_channel_not_exact_premium_journals")

    occurrence = occurrence_id(
        source_relative_path, source.source_collection, row_index, message_id
    )
    converted = copy.deepcopy(original)
    converted["legacy_captured_timestamp_utc"] = original.get("timestamp_utc")
    converted["snowflake_timestamp_utc"] = iso_z(canonical_timestamp)
    converted["timestamp_utc"] = (
        iso_z(canonical_timestamp) or original.get("timestamp_utc")
    )
    converted["timestamp_discrepancy_ms"] = contamination["timestamp_discrepancy_ms"]
    converted["timestamp_scope_exact"] = canonical_timestamp is not None
    converted["legacy_timestamp_scope_exact"] = not bool(
        {
            "legacy_captured_timestamp_unparseable",
            "legacy_captured_timestamp_snowflake_mismatch_gt_1000ms",
            "reply_preview_timestamp_contamination_suspected",
        }
        & reasons
    )
    converted["content_present"] = bool(
        str(original.get("content_text") or "").strip()
        or original.get("attachments")
        or original.get("image_alt")
        or str(original.get("visible_text") or "").strip()
    )
    converted["content_scope_exact"] = not bool(
        {
            "reply_preview_content_contamination_suspected",
            "rendered_timestamp_embedded_in_content",
        }
        & reasons
    )
    converted["reply_context_present"] = bool(
        str(original.get("reply_context") or "").strip()
        or str(original.get("reply_to_content") or "").strip()
    )
    converted.setdefault("article_aria_labelledby", None)
    converted.setdefault("article_id", None)
    converted.setdefault("group_header_text", None)
    converted.setdefault("media_assets", [])
    converted.setdefault("reactions", [])
    converted.setdefault("referenced_user_ids", [])
    converted.setdefault("result_listitem_id", None)
    converted["result_set_size"] = source.reported_total
    converted["collection_channel_id"] = CHANNEL_ID
    converted["collection_channel_name"] = CHANNEL_NAME
    converted["collection_channel_kind"] = CHANNEL_KIND
    converted["collection_category_name"] = CATEGORY_NAME
    converted["collection_channel_id_source"] = "navigation_inventory"
    converted["source_scope"] = "discord_only"
    converted["permalink_confidence"] = locator["permalink_confidence"]
    converted["thread_locator_confidence"] = locator["thread_locator_confidence"]
    converted["locator_audit"] = locator
    converted["legacy_contamination_audit"] = contamination
    converted["migration_quarantined"] = bool(reasons)
    converted["migration_quarantine_reasons"] = sorted(reasons)
    converted["_migration_occurrence"] = {
        "occurrence_id": occurrence,
        "source_file_relative_path": source_relative_path,
        "source_file_sha256": source_sha256,
        "source_kind": source.source_kind,
        "source_collection": source.source_collection,
        "source_row_index_zero_based": row_index,
        "source_row_ordinal_one_based": row_index + 1,
        "source_query": source.query,
        "source_segment_start": source.start.isoformat(),
        "source_segment_end": source.end.isoformat(),
        "source_page_number": original.get("page_number"),
        "source_result_index": original.get("result_index"),
        "source_declared_complete": source.complete,
        "original_payload_sha256": stable_sha256_json(original_copy),
        "original_payload_preserved_inline": True,
    }
    converted["legacy_original_payload"] = original_copy

    quarantine = None
    if reasons:
        quarantine = {
            "quarantine_id": "q_" + occurrence.removeprefix("legacy_occ_"),
            "occurrence_id": occurrence,
            "message_id": message_id or None,
            "source_file_relative_path": source_relative_path,
            "source_collection": source.source_collection,
            "source_row_index_zero_based": row_index,
            "source_segment_start": source.start.isoformat(),
            "source_segment_end": source.end.isoformat(),
            "canonical_snowflake_timestamp_utc": iso_z(canonical_timestamp),
            "legacy_captured_timestamp_utc": original.get("timestamp_utc"),
            "reasons": sorted(reasons),
            "status": "quarantined",
            "resolution": "requires exact Discord recapture or locator evidence",
        }
    return converted, quarantine


def requested_container() -> dict[str, str]:
    return {
        "channel_id": CHANNEL_ID,
        "channel_name": CHANNEL_NAME,
        "channel_kind": CHANNEL_KIND,
        "category_name": CATEGORY_NAME,
        "channel_id_source": "navigation_inventory",
    }


def output_filename(source: SourceSegment) -> str:
    return (
        f"channel_premium_journals_{CHANNEL_ID}_"
        f"{source.start.isoformat()}_{source.end.isoformat()}.json"
    )


def build_segment_payload(
    source: SourceSegment,
    *,
    generated_at_utc: str,
    source_relative_path: str,
    source_sha256: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    converted_messages: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []
    for row_index, original in enumerate(source.messages):
        converted, record = convert_message(
            original,
            source=source,
            source_relative_path=source_relative_path,
            source_sha256=source_sha256,
            row_index=row_index,
        )
        converted_messages.append(converted)
        if record is not None:
            quarantine.append(record)

    ids = [str(row.get("message_id") or "") for row in converted_messages]
    payload = {
        "collector_version": MIGRATOR_VERSION,
        "schema_version": "canonical-v2-staging-1.0.0",
        "guild_id": GUILD_ID,
        "collection_scope": "channel-scoped",
        "source_scope": "discord_only",
        "collection_started_at_utc": None,
        "captured_at_utc": source.captured_at_utc,
        "migrated_at_utc": generated_at_utc,
        "resumed_from_partial_rows": 0,
        "requested_container": requested_container(),
        "segment": {
            "start": source.start.isoformat(),
            "end": source.end.isoformat(),
            "query": source.query,
        },
        "reported_total": source.reported_total,
        "reported_pages": source.reported_pages,
        "pages_captured": source.pages_captured,
        "captured_rows": len(converted_messages),
        "unique_message_ids": len(set(ids)),
        "gap_indices": list(source.gap_indices),
        "container_mismatch_count": 0,
        "container_mismatch_message_ids": [],
        "complete": source.complete,
        "migration": {
            "mode": "provenance_preserving_legacy_staging",
            "source_file_relative_path": source_relative_path,
            "source_file_sha256": source_sha256,
            "source_file_bytes": source.path.stat().st_size,
            "source_kind": source.source_kind,
            "source_collection": source.source_collection,
            "outside_sources_used": 0,
            "canonical_timestamp_basis": "Discord message snowflake only",
            "original_payloads_preserved_inline": True,
            "inferred_thread_or_permalink_promoted_to_exact": False,
            "quarantined_occurrence_count": len(quarantine),
        },
        "messages": converted_messages,
    }
    return payload, quarantine


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(
                    json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n"
                )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def directory_fingerprint(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return {
        file.relative_to(path).as_posix(): {
            "bytes": file.stat().st_size,
            "sha256": sha256_file(file),
        }
        for file in sorted(path.rglob("*"))
        if file.is_file()
    }


def ensure_safe_output(output_dir: Path, protected_raw_dir: Path) -> None:
    output = output_dir.resolve()
    protected = protected_raw_dir.resolve()
    if output == protected or protected in output.parents or output in protected.parents:
        raise MigrationError(
            f"staging output {output} must be separate from protected raw directory {protected}"
        )
    if output.exists():
        raise MigrationError(
            f"staging output already exists and will not be overwritten: {output}"
        )


def utc_now_iso() -> str:
    return iso_z(dt.datetime.now(tz=UTC)) or ""


def run_migration(
    *,
    three_month_dir: Path,
    baseline_path: Path,
    output_dir: Path,
    protected_raw_dir: Path = PROTECTED_RAW_SEGMENT_DIR,
    window: MigrationWindow = MigrationWindow(),
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    window.validate()
    ensure_safe_output(output_dir, protected_raw_dir)
    protected_before = directory_fingerprint(protected_raw_dir)
    generated_at = generated_at_utc or utc_now_iso()

    sources = load_three_month_segments(three_month_dir, window)
    sources.append(load_baseline_tail(baseline_path, window))
    sources.sort(key=lambda item: (item.start, item.end, item.path.name))

    covered: set[dt.date] = set()
    overlaps: set[dt.date] = set()
    for source in sources:
        dates = inclusive_dates(source.start, source.end)
        overlaps.update(covered & dates)
        covered.update(dates)
    expected = inclusive_dates(window.start, window.end)
    if covered != expected or overlaps:
        raise MigrationError(
            "combined legacy and baseline coverage is not exact and non-overlapping: "
            f"missing={sorted(expected-covered)!r}; extra={sorted(covered-expected)!r}; "
            f"overlaps={sorted(overlaps)!r}"
        )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix=output_dir.name + ".tmp.", dir=output_dir.parent)
    )
    segment_root = temporary_root / "segments"
    segment_root.mkdir(parents=True)
    source_manifest: list[dict[str, Any]] = []
    quarantine_rows: list[dict[str, Any]] = []
    all_occurrence_ids: list[str] = []
    all_message_ids: list[str] = []
    input_occurrence_count = 0

    try:
        for source in sources:
            source_relative = relative_source_path(source.path)
            source_hash_before = sha256_file(source.path)
            payload, quarantines = build_segment_payload(
                source,
                generated_at_utc=generated_at,
                source_relative_path=source_relative,
                source_sha256=source_hash_before,
            )
            output_path = segment_root / output_filename(source)
            atomic_write_json(output_path, payload)
            source_hash_after = sha256_file(source.path)
            if source_hash_before != source_hash_after:
                raise MigrationError(f"source file changed during migration: {source.path}")

            occurrence_ids = [
                str(row["_migration_occurrence"]["occurrence_id"])
                for row in payload["messages"]
            ]
            all_occurrence_ids.extend(occurrence_ids)
            all_message_ids.extend(str(row.get("message_id") or "") for row in payload["messages"])
            input_occurrence_count += len(source.messages)
            quarantine_rows.extend(quarantines)
            source_manifest.append(
                {
                    "source_file_relative_path": source_relative,
                    "source_file_sha256": source_hash_before,
                    "source_file_bytes": source.path.stat().st_size,
                    "source_kind": source.source_kind,
                    "source_collection": source.source_collection,
                    "segment_start": source.start.isoformat(),
                    "segment_end": source.end.isoformat(),
                    "query": source.query,
                    "declared_complete": source.complete,
                    "reported_total": source.reported_total,
                    "captured_rows": source.captured_rows,
                    "unique_message_ids": source.unique_message_ids,
                    "output_file_relative_path": output_path.relative_to(temporary_root).as_posix(),
                    "output_file_sha256": sha256_file(output_path),
                    "output_file_bytes": output_path.stat().st_size,
                    "output_occurrence_count": len(payload["messages"]),
                    "quarantined_occurrence_count": len(quarantines),
                    "source_unchanged_after_conversion": True,
                }
            )

        if len(all_occurrence_ids) != input_occurrence_count:
            raise MigrationError("input/output occurrence preservation count mismatch")
        if len(set(all_occurrence_ids)) != len(all_occurrence_ids):
            raise MigrationError("migration occurrence IDs are not globally unique")

        quarantine_path = temporary_root / "legacy_premium_journals_v2_quarantine.jsonl"
        atomic_write_jsonl(quarantine_path, quarantine_rows)
        reason_counts = Counter(
            reason for row in quarantine_rows for reason in row.get("reasons", [])
        )
        contamination_count = sum(
            any(
                reason.startswith("reply_preview_")
                or reason == "rendered_timestamp_embedded_in_content"
                for reason in row.get("reasons", [])
            )
            for row in quarantine_rows
        )
        exact_locator_missing_count = sum(
            "exact_thread_id_unavailable" in row.get("reasons", [])
            or "exact_permalink_unavailable" in row.get("reasons", [])
            for row in quarantine_rows
        )
        manifest = {
            "schema_version": "1.0.0",
            "artifact_type": "legacy_premium_journals_canonical_v2_staging_manifest",
            "generated_at_utc": generated_at,
            "source_scope": "discord_only",
            "outside_sources_used": 0,
            "requested_container": requested_container(),
            "scope": {
                "timezone": "America/Chicago",
                "inclusive_start_date": window.start.isoformat(),
                "inclusive_end_date": window.end.isoformat(),
                "validated_three_month_segment_end": window.segment_end.isoformat(),
                "validated_baseline_tail_start": window.tail_start.isoformat(),
            },
            "staging": {
                "directory": output_dir.resolve().as_posix(),
                "segment_directory": (output_dir / "segments").resolve().as_posix(),
                "protected_raw_channel_segments": protected_raw_dir.resolve().as_posix(),
                "protected_raw_unchanged": None,
                "overwrite_policy": "refuse_existing_destination",
            },
            "migration_policy": {
                "canonical_timestamp_basis": "Discord message snowflake only",
                "legacy_captured_timestamp_preserved": True,
                "original_payload_preserved_inline": True,
                "original_source_file_hashed": True,
                "every_input_row_emitted_once": True,
                "inferred_thread_or_permalink_promoted_to_exact": False,
                "reply_preview_contamination_silently_corrected": False,
                "relevance_filter_applied": False,
                "outside_knowledge_inferences": 0,
            },
            "coverage": {
                "status": "validated_legacy_coverage_staged_with_message_quarantines",
                "segment_count": len(sources),
                "calendar_day_count": len(covered),
                "missing_dates": [],
                "overlapping_dates": [],
                "all_sources_declared_complete": all(source.complete for source in sources),
            },
            "preservation": {
                "source_file_count": len(sources),
                "input_occurrence_count": input_occurrence_count,
                "output_occurrence_count": len(all_occurrence_ids),
                "unique_occurrence_id_count": len(set(all_occurrence_ids)),
                "unique_message_id_count": len(set(all_message_ids)),
                "duplicate_message_occurrences": len(all_message_ids) - len(set(all_message_ids)),
                "missing_input_occurrences": 0,
                "extra_output_occurrences": 0,
                "all_original_payloads_preserved_inline": True,
            },
            "quarantine": {
                "record_file_relative_path": quarantine_path.relative_to(temporary_root).as_posix(),
                "record_file_sha256": sha256_file(quarantine_path),
                "occurrence_count": len(quarantine_rows),
                "reply_or_rendered_timestamp_contamination_count": contamination_count,
                "exact_thread_or_permalink_missing_count": exact_locator_missing_count,
                "reason_counts": dict(sorted(reason_counts.items())),
                "rows_excluded_from_staging": 0,
            },
            "sources": source_manifest,
            "validation": {
                "all_source_counts_revalidated": True,
                "all_source_gap_lists_empty": True,
                "all_source_result_indices_complete": True,
                "all_canonical_timestamps_recomputed_from_snowflakes": True,
                "all_occurrences_preserved": True,
                "all_quarantined_rows_retained": True,
                "canonical_raw_channel_segments_mutated": None,
            },
        }
        manifest_path = temporary_root / "legacy_premium_journals_v2_manifest.json"
        atomic_write_json(manifest_path, manifest)

        protected_after = directory_fingerprint(protected_raw_dir)
        protected_unchanged = protected_before == protected_after
        if not protected_unchanged:
            raise MigrationError("protected raw/channel_segments changed during migration")
        manifest["staging"]["protected_raw_unchanged"] = True
        manifest["validation"]["canonical_raw_channel_segments_mutated"] = False
        atomic_write_json(manifest_path, manifest)

        temporary_root.replace(output_dir)
        return manifest
    except Exception:
        if temporary_root.exists():
            shutil.rmtree(temporary_root)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--three-month-dir", type=Path, default=DEFAULT_THREE_MONTH_DIR)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--protected-raw-dir", type=Path, default=PROTECTED_RAW_SEGMENT_DIR
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = run_migration(
        three_month_dir=args.three_month_dir,
        baseline_path=args.baseline,
        output_dir=args.output_dir,
        protected_raw_dir=args.protected_raw_dir,
    )
    print(
        json.dumps(
            {
                "output_directory": manifest["staging"]["directory"],
                "segment_count": manifest["coverage"]["segment_count"],
                "input_occurrence_count": manifest["preservation"]["input_occurrence_count"],
                "output_occurrence_count": manifest["preservation"]["output_occurrence_count"],
                "quarantined_occurrence_count": manifest["quarantine"]["occurrence_count"],
                "protected_raw_unchanged": manifest["staging"]["protected_raw_unchanged"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
