#!/usr/bin/env python3
"""Read-only Discord segment reconciler and adaptive collection scheduler.

The orchestrator never opens a browser and never writes under ``raw/``.  It
reads ``working/relevance_jobs.json`` plus collector artifacts, validates their
coverage, and writes two disposable working products:

* ``working/collection_progress_manifest.json``
* ``working/collection_next_batch.json``

Throttle and count observations are explicit operator inputs stored in
``working/collection_orchestrator_state.json``.  The raw Discord JSON files are
always treated as immutable evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import build_corpus as corpus_contract


HERE = Path(__file__).resolve().parent
DEFAULT_JOBS = HERE / "working" / "relevance_jobs.json"
DEFAULT_STATE = HERE / "working" / "collection_orchestrator_state.json"
DEFAULT_MANIFEST = HERE / "working" / "collection_progress_manifest.json"
DEFAULT_NEXT = HERE / "working" / "collection_next_batch.json"
DEFAULT_MAX_MESSAGES = 1000
DEFAULT_MAX_PAGES = 40
PAGE_SIZE = 25
DATE_OPERATOR_RE = re.compile(r"(?:^|\s)(?:after|before):\d{4}-\d{2}-\d{2}(?=\s|$)", re.I)


class OrchestratorError(RuntimeError):
    """Raised for invalid scheduler inputs or inconsistent state."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise OrchestratorError(f"Expected a JSON object: {path}")
    return payload


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.next-{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def inspect_collector_contract(path: Path) -> dict[str, Any]:
    required = ("countSearch", "collectDateRange", "collectSegmentResilient", "makeSegments")
    if not path.exists():
        return {"status": "missing", "required_exports": list(required), "missing_exports": list(required)}
    source = path.read_text(encoding="utf-8")
    present = [
        name
        for name in required
        if re.search(rf"\bexport\s+(?:async\s+)?function\s+{re.escape(name)}\b", source)
    ]
    missing = sorted(set(required) - set(present))
    return {
        "status": "passed" if not missing else "failed",
        "required_exports": list(required),
        "present_exports": present,
        "missing_exports": missing,
    }


def iso_date(value: str) -> date:
    return date.fromisoformat(value)


def inclusive_days(start: date, end: date) -> int:
    if end < start:
        raise OrchestratorError(f"End date {end} precedes start date {start}")
    return (end - start).days + 1


def dated_query(query_prefix: str, start: date, end: date) -> str:
    return (
        f"{query_prefix} after:{start - timedelta(days=1)} "
        f"before:{end + timedelta(days=1)}"
    ).strip()


def query_core(query: str) -> str:
    return " ".join(DATE_OPERATOR_RE.sub(" ", str(query or "")).casefold().split())


def normalized_query(query: str) -> str:
    return " ".join(str(query or "").casefold().split())


def make_segments(job: dict[str, Any]) -> list[dict[str, Any]]:
    args = job["args"]
    start = iso_date(args["startIso"])
    end = iso_date(args["endIso"])
    span = int(args["spanDays"])
    if span < 1:
        raise OrchestratorError(f"Invalid spanDays for {job.get('job_id')}: {span}")
    prefix = args["collectorOptions"]["prefix"]
    output_directory = args["outputDirectory"]
    result: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        segment_end = min(end, cursor + timedelta(days=span - 1))
        result.append(
            {
                "start": cursor.isoformat(),
                "end": segment_end.isoformat(),
                "query": dated_query(args["queryPrefix"], cursor, segment_end),
                "query_core": query_core(args["queryPrefix"]),
                "expected_relative_path": str(
                    Path(output_directory)
                    / f"{prefix}_{cursor.isoformat()}_{segment_end.isoformat()}.json"
                ).replace("\\", "/"),
            }
        )
        cursor = segment_end + timedelta(days=1)
    return result


def interval_overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    return a_start <= b_end and b_start <= a_end


def merge_intervals(intervals: Iterable[tuple[date, date]]) -> list[tuple[date, date]]:
    ordered = sorted(intervals)
    merged: list[list[date]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def interval_is_covered(start: date, end: date, intervals: Iterable[tuple[date, date]]) -> bool:
    merged = merge_intervals(
        (max(start, item_start), min(end, item_end))
        for item_start, item_end in intervals
        if interval_overlap(start, end, item_start, item_end)
    )
    return bool(merged) and merged[0][0] <= start and merged[-1][1] >= end and all(
        right_start <= left_end + timedelta(days=1)
        for (_, left_end), (right_start, _) in zip(merged, merged[1:])
    )


def interval_gaps(start: date, end: date, intervals: Iterable[tuple[date, date]]) -> list[tuple[date, date]]:
    clipped = merge_intervals(
        (max(start, item_start), min(end, item_end))
        for item_start, item_end in intervals
        if interval_overlap(start, end, item_start, item_end)
    )
    result: list[tuple[date, date]] = []
    cursor = start
    for item_start, item_end in clipped:
        if cursor < item_start:
            result.append((cursor, item_start - timedelta(days=1)))
        cursor = max(cursor, item_end + timedelta(days=1))
    if cursor <= end:
        result.append((cursor, end))
    return result


def month_slices(start: date, end: date) -> list[tuple[date, date]]:
    result: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        month_end = min(end, next_month - timedelta(days=1))
        result.append((cursor, month_end))
        cursor = month_end + timedelta(days=1)
    return result


def split_interval(start: date, end: date) -> list[tuple[date, date]]:
    if start == end:
        return [(start, end)]
    days = inclusive_days(start, end)
    left_end = start + timedelta(days=(days // 2) - 1)
    return [(start, left_end), (left_end + timedelta(days=1), end)]


def within_thresholds(
    reported_total: int,
    reported_pages: int,
    *,
    max_messages: int,
    max_pages: int,
) -> bool:
    return 0 <= reported_total <= max_messages and 0 <= reported_pages <= max_pages


@dataclass(frozen=True)
class Artifact:
    path: Path
    relative_path: str
    state: str
    errors: tuple[str, ...]
    channel_id: str | None
    channel_name: str | None
    start: date | None
    end: date | None
    query: str
    query_core: str
    reported_total: int
    reported_pages: int
    pages_captured: int
    captured_rows: int
    unique_message_ids: int
    captured_at_utc: str | None
    size_bytes: int
    message_ids: tuple[str, ...]

    def serializable(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "file_state": self.state,
            "validation_errors": list(self.errors),
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "segment_start": self.start.isoformat() if self.start else None,
            "segment_end": self.end.isoformat() if self.end else None,
            "query": self.query,
            "query_core": self.query_core,
            "reported_messages": self.reported_total,
            "reported_pages": self.reported_pages,
            "captured_pages": self.pages_captured,
            "captured_messages": self.captured_rows,
            "unique_message_ids": self.unique_message_ids,
            "captured_at_utc": self.captured_at_utc,
            "size_bytes": self.size_bytes,
        }


def _integer(payload: dict[str, Any], key: str, default: int = -1) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def inspect_artifact(path: Path, root: Path) -> Artifact:
    errors: list[str] = []
    try:
        payload = load_json(path)
    except Exception as exc:
        return Artifact(
            path=path,
            relative_path=str(path.relative_to(root)).replace("\\", "/"),
            state="invalid",
            errors=(f"json_unreadable:{exc}",),
            channel_id=None,
            channel_name=None,
            start=None,
            end=None,
            query="",
            query_core="",
            reported_total=-1,
            reported_pages=-1,
            pages_captured=-1,
            captured_rows=-1,
            unique_message_ids=-1,
            captured_at_utc=None,
            size_bytes=path.stat().st_size,
            message_ids=(),
        )

    segment = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
    requested = (
        payload.get("requested_container")
        if isinstance(payload.get("requested_container"), dict)
        else {}
    )
    try:
        start = iso_date(str(segment.get("start")))
        end = iso_date(str(segment.get("end")))
        if end < start:
            errors.append("segment_end_before_start")
    except Exception:
        start = end = None
        errors.append("invalid_segment_dates")
    query = str(segment.get("query") or "").strip()
    if not query:
        errors.append("missing_segment_query")
    elif start is not None and end is not None:
        expected_after = f"after:{start - timedelta(days=1)}".casefold()
        expected_before = f"before:{end + timedelta(days=1)}".casefold()
        query_tokens = set(normalized_query(query).split())
        if expected_after not in query_tokens or expected_before not in query_tokens:
            errors.append("query_date_bounds_mismatch")
    channel_id = str(requested.get("channel_id") or "") or None
    channel_name = str(requested.get("channel_name") or "") or None
    if not channel_id:
        errors.append("missing_requested_channel_id")

    messages = payload.get("messages")
    if not isinstance(messages, list):
        messages = []
        errors.append("messages_not_array")
    reported_total = _integer(payload, "reported_total")
    reported_pages = _integer(payload, "reported_pages")
    pages_captured = _integer(payload, "pages_captured")
    captured_rows = _integer(payload, "captured_rows")
    unique_message_ids = _integer(payload, "unique_message_ids")
    complete_flag = payload.get("complete") is True
    ids = [str(row.get("message_id") or "") for row in messages if isinstance(row, dict)]
    observed_unique = len({message_id for message_id in ids if message_id})

    if reported_total < 0:
        errors.append("invalid_reported_total")
    if reported_pages < 0:
        errors.append("invalid_reported_pages")
    if pages_captured < 0:
        errors.append("invalid_pages_captured")
    if captured_rows != len(messages):
        errors.append("captured_rows_mismatch")
    if unique_message_ids != observed_unique:
        errors.append("unique_message_ids_mismatch")
    if _integer(payload, "container_mismatch_count", 0) != 0:
        errors.append("container_mismatch_nonzero")
    if payload.get("gap_indices") not in ([], None):
        errors.append("gap_indices_nonempty")
    expected_pages = math.ceil(reported_total / PAGE_SIZE) if reported_total > 0 else 0
    if reported_total >= 0 and reported_pages >= 0 and reported_pages != expected_pages:
        errors.append("reported_pages_inconsistent")
    if reported_total >= 0 and len(messages) > reported_total:
        errors.append("captured_messages_exceed_reported_total")
    if reported_pages >= 0 and pages_captured > reported_pages:
        errors.append("captured_pages_exceed_reported_pages")
    if complete_flag:
        if pages_captured != reported_pages:
            errors.append("complete_pages_not_fully_captured")
        if len(messages) != reported_total:
            errors.append("complete_message_count_mismatch")
        if observed_unique != reported_total:
            errors.append("complete_unique_count_mismatch")
        evidence, _source, _sidecar_path, binding_errors = (
            corpus_contract.resolve_completion_evidence(path, payload)
        )
        errors.extend(binding_errors)
        errors.extend(
            corpus_contract.validate_completion_evidence(
                evidence,
                query=query,
                reported_total=reported_total,
                reported_pages=reported_pages,
            )
        )
        if evidence is None:
            errors.append("completion_evidence_missing_recapture_or_sidecar_required")
    state = "invalid" if errors else ("complete" if complete_flag else "partial")
    return Artifact(
        path=path,
        relative_path=str(path.relative_to(root)).replace("\\", "/"),
        state=state,
        errors=tuple(errors),
        channel_id=channel_id,
        channel_name=channel_name,
        start=start,
        end=end,
        query=query,
        query_core=query_core(query),
        reported_total=max(reported_total, 0),
        reported_pages=max(reported_pages, 0),
        pages_captured=max(pages_captured, 0),
        captured_rows=max(captured_rows, len(messages), 0),
        unique_message_ids=max(unique_message_ids, observed_unique, 0),
        captured_at_utc=str(payload.get("captured_at_utc") or "") or None,
        size_bytes=path.stat().st_size,
        message_ids=tuple(message_id for message_id in ids if message_id),
    )


def discover_artifacts(root: Path, jobs: Sequence[dict[str, Any]]) -> tuple[list[Artifact], list[str]]:
    directories = {
        (root / str(job["args"]["outputDirectory"])).resolve()
        for job in jobs
    }
    directories.update(
        {
            (root / "raw" / "channel_segments").resolve(),
            (root / "raw" / "relevance_segments").resolve(),
            (root / "raw" / "audit_segments").resolve(),
        }
    )
    paths: set[Path] = set()
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.rglob("*.json"):
            if ".next-" not in path.name and not path.name.endswith(
                corpus_contract.COMPLETION_EVIDENCE_SIDECAR_SUFFIX
            ) and not path.name.endswith(
                corpus_contract.TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX
            ):
                paths.add(path.resolve())
    return (
        [inspect_artifact(path, root.resolve()) for path in sorted(paths)],
        [str(path.relative_to(root.resolve())).replace("\\", "/") for path in sorted(directories)],
    )


def initial_state() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "artifact_type": "discord_collection_orchestrator_state",
        "updated_at_utc": None,
        "throttle_events": [],
        "count_observations": [],
    }


def read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return initial_state()
    state = load_json(path)
    if state.get("schema_version") != "1.0.0":
        raise OrchestratorError(f"Unsupported state schema: {state.get('schema_version')}")
    state.setdefault("throttle_events", [])
    state.setdefault("count_observations", [])
    return state


def job_channel_id(job: dict[str, Any]) -> str:
    return str(job["args"]["collectorOptions"]["channelId"])


def job_channel_name(job: dict[str, Any]) -> str:
    return str(job["args"]["collectorOptions"]["channelName"])


def unfiltered_cores(jobs: Sequence[dict[str, Any]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for job in jobs:
        if job.get("job_kind") in {
            "full_capture_or_empty_verification",
            "residual_audit_census_day",
        }:
            result[job_channel_id(job)].add(query_core(job["args"]["queryPrefix"]))
    return result


def query_covers(
    artifact: Artifact,
    *,
    channel_id: str,
    target_core: str,
    unfiltered_by_channel: dict[str, set[str]],
) -> bool:
    if artifact.channel_id != channel_id:
        return False
    return artifact.query_core == target_core or artifact.query_core in unfiltered_by_channel.get(
        channel_id, set()
    )


def _artifact_exact(artifact: Artifact, segment: dict[str, Any]) -> bool:
    return (
        artifact.state == "complete"
        and artifact.start == iso_date(segment["start"])
        and artifact.end == iso_date(segment["end"])
        and normalized_query(artifact.query) == normalized_query(segment["query"])
    )


def reconcile_job(
    job: dict[str, Any],
    artifacts: Sequence[Artifact],
    *,
    unfiltered_by_channel: dict[str, set[str]],
    max_messages: int,
    max_pages: int,
) -> dict[str, Any]:
    channel_id = job_channel_id(job)
    target_core = query_core(job["args"]["queryPrefix"])
    segments = make_segments(job)
    channel_artifacts = [item for item in artifacts if item.channel_id == channel_id]
    segment_rows: list[dict[str, Any]] = []
    evidence_paths: set[str] = set()

    for segment in segments:
        start = iso_date(segment["start"])
        end = iso_date(segment["end"])
        exact = [item for item in channel_artifacts if _artifact_exact(item, segment)]
        if exact:
            status = "complete"
            evidence = exact
            reason = "exact_query_and_date_segment_complete"
        else:
            eligible_complete = [
                item
                for item in channel_artifacts
                if item.state == "complete"
                and item.start is not None
                and item.end is not None
                and query_covers(
                    item,
                    channel_id=channel_id,
                    target_core=target_core,
                    unfiltered_by_channel=unfiltered_by_channel,
                )
                and interval_overlap(start, end, item.start, item.end)
                and within_thresholds(
                    item.reported_total,
                    item.reported_pages,
                    max_messages=max_messages,
                    max_pages=max_pages,
                )
            ]
            if interval_is_covered(
                start,
                end,
                [(item.start, item.end) for item in eligible_complete if item.start and item.end],
            ):
                status = "superseded"
                evidence = eligible_complete
                reason = "gap_free_safe_complete_superset_coverage"
            else:
                overlapping = [
                    item
                    for item in channel_artifacts
                    if item.state in {"complete", "partial"}
                    and item.start is not None
                    and item.end is not None
                    and query_covers(
                        item,
                        channel_id=channel_id,
                        target_core=target_core,
                        unfiltered_by_channel=unfiltered_by_channel,
                    )
                    and interval_overlap(start, end, item.start, item.end)
                ]
                if overlapping:
                    status = "partial"
                    evidence = overlapping
                    reason = "some_compatible_coverage_or_checkpoint_present"
                else:
                    status = "pending"
                    evidence = []
                    reason = "no_compatible_artifact_coverage"
        evidence_paths.update(item.relative_path for item in evidence)
        segment_rows.append(
            {
                **segment,
                "status": status,
                "status_reason": reason,
                "evidence_artifacts": [item.relative_path for item in evidence],
            }
        )

    statuses = Counter(row["status"] for row in segment_rows)
    if statuses["complete"] == len(segment_rows):
        status = "complete"
    elif statuses["complete"] + statuses["superseded"] == len(segment_rows):
        status = "superseded"
    elif statuses["partial"] or statuses["complete"] or statuses["superseded"]:
        status = "partial"
    else:
        status = "pending"
    evidence = [item for item in artifacts if item.relative_path in evidence_paths]
    return {
        "job_id": job["job_id"],
        "job_kind": job["job_kind"],
        "channel_id": channel_id,
        "channel_name": job_channel_name(job),
        "query_prefix": job["args"]["queryPrefix"],
        "query_core": target_core,
        "window": {
            "start": job["args"]["startIso"],
            "end": job["args"]["endIso"],
        },
        "status": status,
        "segment_status_counts": {key: statuses[key] for key in ("complete", "partial", "pending", "superseded")},
        "evidence_occurrence_totals": {
            "artifact_count": len(evidence),
            "reported_messages": sum(item.reported_total for item in evidence),
            "captured_messages": sum(item.captured_rows for item in evidence),
            "reported_pages": sum(item.reported_pages for item in evidence),
            "captured_pages": sum(item.pages_captured for item in evidence),
        },
        "segments": segment_rows,
    }


def observation_key(channel_id: str, core: str, start: date, end: date) -> tuple[str, str, date, date]:
    return (channel_id, core, start, end)


def build_observations(
    state: dict[str, Any], artifacts: Sequence[Artifact]
) -> dict[tuple[str, str, date, date], dict[str, Any]]:
    result: dict[tuple[str, str, date, date], dict[str, Any]] = {}
    for item in artifacts:
        if item.state not in {"complete", "partial"} or not item.channel_id or not item.start or not item.end:
            continue
        result[observation_key(item.channel_id, item.query_core, item.start, item.end)] = {
            "source": "segment_artifact",
            "source_path": item.relative_path,
            "channel_id": item.channel_id,
            "query": item.query,
            "query_core": item.query_core,
            "start": item.start.isoformat(),
            "end": item.end.isoformat(),
            "reported_total": item.reported_total,
            "reported_pages": item.reported_pages,
            "observed_at_utc": item.captured_at_utc,
            "artifact_state": item.state,
        }
    for item in state.get("count_observations", []):
        try:
            key = observation_key(
                str(item["channel_id"]),
                query_core(str(item["query"])),
                iso_date(str(item["start"])),
                iso_date(str(item["end"])),
            )
        except Exception:
            continue
        previous = result.get(key)
        if previous is None or str(item.get("observed_at_utc") or "") >= str(previous.get("observed_at_utc") or ""):
            result[key] = item
    return result


def active_cooldowns(
    state: dict[str, Any], now: datetime
) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    for event in state.get("throttle_events", []):
        try:
            until = parse_utc(str(event["cooldown_until_utc"]))
        except Exception:
            continue
        if until > now:
            active.append({**event, "remaining_seconds": math.ceil((until - now).total_seconds())})
    return sorted(active, key=lambda row: row["cooldown_until_utc"])


def cooldown_applies(event: dict[str, Any], job: dict[str, Any]) -> bool:
    scope = event.get("scope", "global")
    if scope == "global":
        return True
    if scope == "channel":
        return str(event.get("scope_key")) == job_channel_id(job)
    if scope == "job":
        return str(event.get("scope_key")) == str(job["job_id"])
    return False


def artifact_coverage_intervals(
    job: dict[str, Any],
    artifacts: Sequence[Artifact],
    *,
    unfiltered_by_channel: dict[str, set[str]],
    max_messages: int,
    max_pages: int,
) -> list[tuple[date, date]]:
    channel_id = job_channel_id(job)
    core = query_core(job["args"]["queryPrefix"])
    planned_ranges = {
        (iso_date(segment["start"]), iso_date(segment["end"]))
        for segment in make_segments(job)
    }
    intervals: list[tuple[date, date]] = []
    for item in artifacts:
        if item.state != "complete" or not item.start or not item.end:
            continue
        if not query_covers(
            item,
            channel_id=channel_id,
            target_core=core,
            unfiltered_by_channel=unfiltered_by_channel,
        ):
            continue
        exact_planned = item.query_core == core and (item.start, item.end) in planned_ranges
        if exact_planned or within_thresholds(
            item.reported_total,
            item.reported_pages,
            max_messages=max_messages,
            max_pages=max_pages,
        ):
            intervals.append((item.start, item.end))
    return merge_intervals(intervals)


def matching_partial(
    job: dict[str, Any],
    artifacts: Sequence[Artifact],
    gaps: Sequence[tuple[date, date]],
    *,
    max_messages: int,
    max_pages: int,
) -> Artifact | None:
    channel_id = job_channel_id(job)
    core = query_core(job["args"]["queryPrefix"])
    candidates = [
        item
        for item in artifacts
        if item.state == "partial"
        and item.channel_id == channel_id
        and item.query_core == core
        and item.start is not None
        and item.end is not None
        and any(interval_overlap(item.start, item.end, start, end) for start, end in gaps)
        and within_thresholds(
            item.reported_total,
            item.reported_pages,
            max_messages=max_messages,
            max_pages=max_pages,
        )
    ]
    return max(candidates, key=lambda item: (item.captured_rows, item.captured_at_utc or ""), default=None)


def collector_action(
    job: dict[str, Any],
    start: date,
    end: date,
    *,
    strategy: str,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = job["args"]
    call_args = {
        "startIso": start.isoformat(),
        "endIso": end.isoformat(),
        "outputDirectory": args["outputDirectory"],
        "queryPrefix": args["queryPrefix"],
        "spanDays": inclusive_days(start, end),
        "collectorOptions": args["collectorOptions"],
        "schedulerOptions": args["schedulerOptions"],
    }
    prefix = args["collectorOptions"]["prefix"]
    return {
        "action": "collect_segment",
        "job_id": job["job_id"],
        "job_kind": job["job_kind"],
        "channel_id": job_channel_id(job),
        "channel_name": job_channel_name(job),
        "segment": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "query": dated_query(args["queryPrefix"], start, end),
            "query_core": query_core(args["queryPrefix"]),
        },
        "strategy": strategy,
        "collector_export": "collectDateRange",
        "collector_call_args": call_args,
        "expected_output": str(
            Path(args["outputDirectory"])
            / f"{prefix}_{start.isoformat()}_{end.isoformat()}.json"
        ).replace("\\", "/"),
        "count_observation": observation,
    }


def count_action(job: dict[str, Any], start: date, end: date, *, strategy: str) -> dict[str, Any]:
    query = dated_query(job["args"]["queryPrefix"], start, end)
    return {
        "action": "count_probe",
        "job_id": job["job_id"],
        "job_kind": job["job_kind"],
        "channel_id": job_channel_id(job),
        "channel_name": job_channel_name(job),
        "segment": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "query": query,
            "query_core": query_core(query),
        },
        "strategy": strategy,
        "collector_export": "countSearch",
        "collector_call_args": {"query": query},
        "record_result_command": (
            f'python "{Path(__file__).resolve()}" record-count --job-id {job["job_id"]} '
            f"--start {start.isoformat()} --end {end.isoformat()} "
            "--reported-total <N> --reported-pages <N>"
        ),
    }


def adaptive_window_action(
    job: dict[str, Any],
    start: date,
    end: date,
    observations: dict[tuple[str, str, date, date], dict[str, Any]],
    *,
    max_messages: int,
    max_pages: int,
    level: str = "full_uncovered_window",
) -> dict[str, Any]:
    channel_id = job_channel_id(job)
    core = query_core(job["args"]["queryPrefix"])
    observation = observations.get(observation_key(channel_id, core, start, end))
    if observation is None:
        return count_action(job, start, end, strategy=f"probe_{level}")
    total = int(observation.get("reported_total", -1))
    pages = int(observation.get("reported_pages", math.ceil(max(total, 0) / PAGE_SIZE)))
    if within_thresholds(total, pages, max_messages=max_messages, max_pages=max_pages):
        return collector_action(
            job,
            start,
            end,
            strategy=f"capture_{level}_within_threshold",
            observation=observation,
        )
    if start == end:
        return {
            "action": "blocked_oversize_day",
            "job_id": job["job_id"],
            "job_kind": job["job_kind"],
            "channel_id": channel_id,
            "channel_name": job_channel_name(job),
            "segment": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "query": dated_query(job["args"]["queryPrefix"], start, end),
                "query_core": core,
            },
            "strategy": "manual_review_required_single_day_exceeds_threshold",
            "count_observation": observation,
        }
    children = month_slices(start, end) if inclusive_days(start, end) > 31 else split_interval(start, end)
    child_level = "monthly_slice" if inclusive_days(start, end) > 31 else "bisected_slice"
    return adaptive_window_action(
        job,
        children[0][0],
        children[0][1],
        observations,
        max_messages=max_messages,
        max_pages=max_pages,
        level=child_level,
    )


def next_action_for_job(
    job: dict[str, Any],
    job_progress: dict[str, Any],
    artifacts: Sequence[Artifact],
    observations: dict[tuple[str, str, date, date], dict[str, Any]],
    *,
    unfiltered_by_channel: dict[str, set[str]],
    max_messages: int,
    max_pages: int,
) -> dict[str, Any] | None:
    if job_progress["status"] in {"complete", "superseded"}:
        return None
    start = iso_date(job["args"]["startIso"])
    end = iso_date(job["args"]["endIso"])
    coverage = artifact_coverage_intervals(
        job,
        artifacts,
        unfiltered_by_channel=unfiltered_by_channel,
        max_messages=max_messages,
        max_pages=max_pages,
    )
    gaps = interval_gaps(start, end, coverage)
    if not gaps:
        return None
    partial = matching_partial(
        job,
        artifacts,
        gaps,
        max_messages=max_messages,
        max_pages=max_pages,
    )
    if partial is not None and partial.start and partial.end:
        observation = observations.get(
            observation_key(job_channel_id(job), partial.query_core, partial.start, partial.end)
        )
        return collector_action(
            job,
            partial.start,
            partial.end,
            strategy="resume_safe_partial_checkpoint",
            observation=observation,
        )

    if job.get("job_kind") == "targeted_search":
        return adaptive_window_action(
            job,
            gaps[0][0],
            gaps[0][1],
            observations,
            max_messages=max_messages,
            max_pages=max_pages,
        )

    first_uncovered = next(
        row for row in job_progress["segments"] if row["status"] not in {"complete", "superseded"}
    )
    segment_start = iso_date(first_uncovered["start"])
    segment_end = iso_date(first_uncovered["end"])
    return collector_action(
        job,
        segment_start,
        segment_end,
        strategy="capture_planned_segment",
    )


def action_priority(action: dict[str, Any]) -> tuple[int, str, str]:
    strategy = str(action.get("strategy") or "")
    if strategy == "resume_safe_partial_checkpoint":
        rank = 0
    elif action.get("action") == "count_probe":
        rank = 1
    elif "within_threshold" in strategy:
        rank = 2
    elif action.get("action") == "collect_segment":
        rank = 3
    else:
        rank = 9
    return (rank, str(action.get("channel_id")), str(action.get("job_id")))


def choose_batch(
    candidates: Sequence[dict[str, Any]], batch_size: int
) -> list[dict[str, Any]]:
    if batch_size < 1:
        raise OrchestratorError("batch_size must be positive")
    ordered = sorted(candidates, key=action_priority)
    selected: list[dict[str, Any]] = []
    used_channels: set[str] = set()
    for action in ordered:
        channel_id = str(action.get("channel_id"))
        if channel_id in used_channels:
            continue
        selected.append(action)
        used_channels.add(channel_id)
        if len(selected) >= batch_size:
            return selected
    for action in ordered:
        if action not in selected:
            selected.append(action)
            if len(selected) >= batch_size:
                break
    return selected


def build_outputs(
    root: Path,
    jobs_payload: dict[str, Any],
    state: dict[str, Any],
    *,
    jobs_path: Path,
    now: datetime,
    max_messages: int,
    max_pages: int,
    batch_size: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    jobs = jobs_payload.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise OrchestratorError("jobs payload has no jobs")
    artifacts, inspected_directories = discover_artifacts(root, jobs)
    unfiltered_by_channel = unfiltered_cores(jobs)
    job_progress = [
        reconcile_job(
            job,
            artifacts,
            unfiltered_by_channel=unfiltered_by_channel,
            max_messages=max_messages,
            max_pages=max_pages,
        )
        for job in jobs
    ]
    progress_by_id = {row["job_id"]: row for row in job_progress}
    observations = build_observations(state, artifacts)
    cooldowns = active_cooldowns(state, now)
    candidates: list[dict[str, Any]] = []
    blocked_candidates: list[dict[str, Any]] = []
    cooling_jobs: list[dict[str, Any]] = []
    for job in jobs:
        action = next_action_for_job(
            job,
            progress_by_id[job["job_id"]],
            artifacts,
            observations,
            unfiltered_by_channel=unfiltered_by_channel,
            max_messages=max_messages,
            max_pages=max_pages,
        )
        if action is None:
            continue
        applicable = [event for event in cooldowns if cooldown_applies(event, job)]
        if applicable:
            cooling_jobs.append(
                {
                    "job_id": job["job_id"],
                    "channel_id": job_channel_id(job),
                    "cooldown_until_utc": max(event["cooldown_until_utc"] for event in applicable),
                }
            )
        elif action["action"].startswith("blocked_"):
            blocked_candidates.append(action)
        else:
            candidates.append(action)

    batch = choose_batch(candidates, batch_size) if candidates else []
    if batch:
        next_status = "ready"
    elif cooling_jobs:
        next_status = "cooldown"
    elif blocked_candidates:
        next_status = "blocked_oversize"
    else:
        next_status = "complete"

    artifact_states = Counter(item.state for item in artifacts)
    job_states = Counter(item["status"] for item in job_progress)
    segment_states = Counter(
        segment["status"] for item in job_progress for segment in item["segments"]
    )
    # Each file is parsed once, so a concurrently replaced checkpoint cannot
    # produce metadata from one version and unique-ID counts from another.
    unique_ids = {
        message_id
        for item in artifacts
        if item.state in {"complete", "partial"}
        for message_id in item.message_ids
    }

    collector_path = Path(str(jobs_payload.get("collector_module") or ""))
    if not collector_path.is_absolute():
        collector_path = (root / collector_path).resolve()
    collector_contract = inspect_collector_contract(collector_path)
    if collector_contract["status"] != "passed":
        raise OrchestratorError(
            "Collector module contract failed: " + ",".join(collector_contract["missing_exports"])
        )
    source_files = {
        "jobs": {
            "path": str(jobs_path.resolve()),
            "sha256": sha256_file(jobs_path),
        },
        "collector": {
            "path": str(collector_path),
            "sha256": sha256_file(collector_path) if collector_path.exists() else None,
            "contract": collector_contract,
        },
    }
    manifest = {
        "schema_version": "1.0.0",
        "artifact_type": "discord_collection_progress_manifest",
        "generated_at_utc": format_utc(now),
        "source_policy": {
            "scope": "local_discord_artifacts_only",
            "browser_calls_made": 0,
            "raw_files_modified": 0,
        },
        "root": str(root.resolve()),
        "source_files": source_files,
        "inspected_directories": inspected_directories,
        "safety_thresholds": {
            "max_reported_messages_for_adaptive_superset": max_messages,
            "max_reported_pages_for_adaptive_superset": max_pages,
            "page_size": PAGE_SIZE,
        },
        "cooldown": {
            "active": bool(cooldowns),
            "active_events": cooldowns,
            "cooling_job_count": len(cooling_jobs),
        },
        "summary": {
            "jobs": {
                "total": len(job_progress),
                **{key: job_states[key] for key in ("complete", "partial", "pending", "superseded")},
            },
            "planned_segments": {
                "total": sum(segment_states.values()),
                **{key: segment_states[key] for key in ("complete", "partial", "pending", "superseded")},
            },
            "artifacts": {
                "total": len(artifacts),
                "complete": artifact_states["complete"],
                "partial": artifact_states["partial"],
                "invalid": artifact_states["invalid"],
                "reported_message_occurrences_complete": sum(
                    item.reported_total for item in artifacts if item.state == "complete"
                ),
                "captured_message_occurrences": sum(
                    item.captured_rows for item in artifacts if item.state in {"complete", "partial"}
                ),
                "unique_message_ids_across_artifacts": len(unique_ids),
                "reported_pages_complete": sum(
                    item.reported_pages for item in artifacts if item.state == "complete"
                ),
                "captured_pages": sum(
                    item.pages_captured for item in artifacts if item.state in {"complete", "partial"}
                ),
            },
            "count_observations": len(state.get("count_observations", [])),
        },
        "artifacts": [item.serializable() for item in artifacts],
        "jobs": job_progress,
    }
    next_batch = {
        "schema_version": "1.0.0",
        "artifact_type": "discord_collection_next_batch",
        "generated_at_utc": format_utc(now),
        "status": next_status,
        "safety_thresholds": manifest["safety_thresholds"],
        "active_cooldowns": cooldowns,
        "cooling_jobs": cooling_jobs,
        "candidate_action_count": len(candidates),
        "actions": batch,
        "blocked_actions": blocked_candidates,
        "execution_note": (
            "This file is advisory. The orchestrator did not call Browser. Execute only the "
            "listed collector export, then rescan or record the exact count/throttle result."
        ),
    }
    return manifest, next_batch


def load_jobs(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if payload.get("schema_version") != "1.0.0":
        raise OrchestratorError(f"Unsupported jobs schema: {payload.get('schema_version')}")
    return payload


def find_job(jobs_payload: dict[str, Any], job_id: str) -> dict[str, Any]:
    matches = [job for job in jobs_payload.get("jobs", []) if job.get("job_id") == job_id]
    if len(matches) != 1:
        raise OrchestratorError(f"Expected exactly one job {job_id!r}; found {len(matches)}")
    return matches[0]


def record_count(
    state: dict[str, Any],
    job: dict[str, Any],
    *,
    start: date,
    end: date,
    total: int,
    pages: int | None,
    observed_at: datetime,
) -> dict[str, Any]:
    job_start = iso_date(job["args"]["startIso"])
    job_end = iso_date(job["args"]["endIso"])
    if start < job_start or end > job_end or end < start:
        raise OrchestratorError("Count observation dates must be inside the job window")
    if total < 0:
        raise OrchestratorError("reported_total must be non-negative")
    expected_pages = math.ceil(total / PAGE_SIZE) if total else 0
    if pages is None:
        pages = expected_pages
    if pages != expected_pages:
        raise OrchestratorError(
            f"reported_pages must equal ceil(total/{PAGE_SIZE}) ({expected_pages})"
        )
    query = dated_query(job["args"]["queryPrefix"], start, end)
    entry = {
        "observation_id": hashlib.sha256(
            f"{job['job_id']}|{start}|{end}|{query}|{format_utc(observed_at)}".encode("utf-8")
        ).hexdigest()[:20],
        "source": "operator_recorded_countSearch",
        "job_id": job["job_id"],
        "channel_id": job_channel_id(job),
        "channel_name": job_channel_name(job),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "query": query,
        "query_core": query_core(query),
        "reported_total": total,
        "reported_pages": pages,
        "observed_at_utc": format_utc(observed_at),
    }
    retained = [
        row
        for row in state.get("count_observations", [])
        if not (
            row.get("job_id") == job["job_id"]
            and row.get("start") == start.isoformat()
            and row.get("end") == end.isoformat()
            and query_core(str(row.get("query") or "")) == query_core(query)
        )
    ]
    state["count_observations"] = retained + [entry]
    state["updated_at_utc"] = format_utc(observed_at)
    return entry


def record_throttle(
    state: dict[str, Any],
    *,
    scope: str,
    scope_key: str | None,
    occurred_at: datetime,
    cooldown_seconds: int,
    reason: str,
    job_id: str | None,
) -> dict[str, Any]:
    if scope not in {"global", "channel", "job"}:
        raise OrchestratorError("Throttle scope must be global, channel, or job")
    if scope != "global" and not scope_key:
        raise OrchestratorError("channel/job throttle scopes require a scope key")
    if cooldown_seconds < 1:
        raise OrchestratorError("cooldown_seconds must be positive")
    until = occurred_at + timedelta(seconds=cooldown_seconds)
    entry = {
        "event_id": hashlib.sha256(
            f"{scope}|{scope_key}|{format_utc(occurred_at)}|{reason}".encode("utf-8")
        ).hexdigest()[:20],
        "scope": scope,
        "scope_key": scope_key if scope != "global" else None,
        "job_id": job_id,
        "occurred_at_utc": format_utc(occurred_at),
        "cooldown_seconds": cooldown_seconds,
        "cooldown_until_utc": format_utc(until),
        "reason": reason,
    }
    state.setdefault("throttle_events", []).append(entry)
    state["updated_at_utc"] = format_utc(occurred_at)
    return entry


def add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=HERE)
    parser.add_argument("--jobs", type=Path, default=DEFAULT_JOBS)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser("scan", help="Reconcile local artifacts and emit progress/next JSON")
    add_common_paths(scan)
    scan.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    scan.add_argument("--next", dest="next_path", type=Path, default=DEFAULT_NEXT)
    scan.add_argument("--max-messages", type=int, default=DEFAULT_MAX_MESSAGES)
    scan.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    scan.add_argument("--batch-size", type=int, default=1)
    scan.add_argument("--now", help="UTC ISO timestamp override for reproducible scans")
    scan.add_argument("--stdout", action="store_true", help="Print both products instead of writing them")

    count = subparsers.add_parser("record-count", help="Record an exact countSearch observation")
    add_common_paths(count)
    count.add_argument("--job-id", required=True)
    count.add_argument("--start", required=True)
    count.add_argument("--end", required=True)
    count.add_argument("--reported-total", type=int, required=True)
    count.add_argument("--reported-pages", type=int)
    count.add_argument("--observed-at", help="UTC ISO timestamp; defaults to now")

    throttle = subparsers.add_parser("record-throttle", help="Record a Discord throttle cooldown")
    add_common_paths(throttle)
    throttle.add_argument("--scope", choices=("global", "channel", "job"), default="global")
    throttle.add_argument("--scope-key")
    throttle.add_argument("--job-id")
    throttle.add_argument("--cooldown-seconds", type=int, default=300)
    throttle.add_argument("--reason", default="discord_search_throttle")
    throttle.add_argument("--occurred-at", help="UTC ISO timestamp; defaults to now")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):
        argv.insert(0, "scan")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        jobs_path = args.jobs.resolve()
        state_path = args.state.resolve()
        jobs_payload = load_jobs(jobs_path)
        state = read_state(state_path)
        if args.command == "record-count":
            job = find_job(jobs_payload, args.job_id)
            observed_at = parse_utc(args.observed_at) if args.observed_at else utc_now()
            entry = record_count(
                state,
                job,
                start=iso_date(args.start),
                end=iso_date(args.end),
                total=args.reported_total,
                pages=args.reported_pages,
                observed_at=observed_at,
            )
            atomic_write_json(state_path, state)
            print(json.dumps(entry, ensure_ascii=False, indent=2))
            return 0
        if args.command == "record-throttle":
            scope_key = args.scope_key
            if args.job_id:
                job = find_job(jobs_payload, args.job_id)
                if args.scope == "job" and not scope_key:
                    scope_key = job["job_id"]
                if args.scope == "channel" and not scope_key:
                    scope_key = job_channel_id(job)
            occurred_at = parse_utc(args.occurred_at) if args.occurred_at else utc_now()
            entry = record_throttle(
                state,
                scope=args.scope,
                scope_key=scope_key,
                occurred_at=occurred_at,
                cooldown_seconds=args.cooldown_seconds,
                reason=args.reason,
                job_id=args.job_id,
            )
            atomic_write_json(state_path, state)
            print(json.dumps(entry, ensure_ascii=False, indent=2))
            return 0

        now = parse_utc(args.now) if args.now else utc_now()
        if args.max_messages < 0 or args.max_pages < 0:
            raise OrchestratorError("Safety thresholds must be non-negative")
        manifest, next_batch = build_outputs(
            root,
            jobs_payload,
            state,
            jobs_path=jobs_path,
            now=now,
            max_messages=args.max_messages,
            max_pages=args.max_pages,
            batch_size=args.batch_size,
        )
        if args.stdout:
            print(json.dumps({"manifest": manifest, "next_batch": next_batch}, ensure_ascii=False, indent=2))
        else:
            atomic_write_json(args.manifest.resolve(), manifest)
            atomic_write_json(args.next_path.resolve(), next_batch)
            print(
                json.dumps(
                    {
                        "status": next_batch["status"],
                        "manifest": str(args.manifest.resolve()),
                        "next_batch": str(args.next_path.resolve()),
                        "summary": manifest["summary"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, OrchestratorError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
