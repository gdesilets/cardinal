#!/usr/bin/env python3
"""Independent QA validator for the Jan 1-Jul 20 Discord corpus.

The validator is intentionally separate from the collector, merger, and database
builder.  It can inspect an incomplete capture safely, but a release passes only
when inventory, date coverage, source reconciliation, and the optional SQLite
database all satisfy the declared whole-server contract.
"""

from __future__ import annotations

import argparse
import copy
import collections
import datetime as dt
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urlparse

PACKAGE_DIR = Path(__file__).resolve().parent.parent
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

import preservation_hashes
import relevance_release_policy
import discord_attachment_archiver
import premium_journals_provenance_contract
import reply_provenance_contract
import timestamp_scope_revalidation


SCHEMA_VERSION = "1.0.0"
DEFAULT_GUILD_ID = "1167376964680691732"
DISCORD_EPOCH_MS = 1420070400000
DISCORD_ID_RE = re.compile(r"^\d{15,22}$")
QUERY_AFTER_RE = re.compile(r"(?:^|\s)after:(\d{4}-\d{2}-\d{2})(?:\s|$)", re.I)
QUERY_BEFORE_RE = re.compile(r"(?:^|\s)before:(\d{4}-\d{2}-\d{2})(?:\s|$)", re.I)
QUERY_CHANNEL_RE = re.compile(r"(?:^|\s)in:(.+?)(?=\s+(?:after|before|from|has|mentions):|$)", re.I)
ALLOWED_SCOPES = {"guild-wide", "channel-scoped", "thread-scoped"}
ALLOWED_REPLY_STATES = {
    "resolved",
    "outside_window",
    "context_stub",
    "deleted",
    "inaccessible",
    "unavailable",
    "unresolved_reference",
    "not_applicable",
}
MESSAGE_CONTAINER_FIELDS = (
    "channel_id",
    "thread_channel_id",
    "container_id",
    "exact_channel_id",
)
DISCORD_SOURCE_HOSTS = {
    "discord.com",
    "www.discord.com",
    "cdn.discordapp.com",
    "media.discordapp.net",
}
COMPLETION_EVIDENCE_SIDECAR_SUFFIX = ".completion-evidence.json"
TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX = (
    timestamp_scope_revalidation.TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX
)
REQUIRED_STABLE_EMPTY_OBSERVATIONS = 3
REQUIRED_STABLE_BOTTOM_OBSERVATIONS = 2


class ValidationError(RuntimeError):
    """Raised when the validator itself cannot safely continue."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso_utc(value: Any) -> dt.datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("missing timestamp")
    parsed = dt.datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no UTC offset")
    return parsed.astimezone(dt.timezone.utc)


def parse_date(value: Any) -> dt.date:
    return dt.date.fromisoformat(str(value))


def first_weekday(year: int, month: int, weekday: int) -> dt.date:
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(weekday - first.weekday()) % 7)


def central_transitions_utc(year: int) -> tuple[dt.datetime, dt.datetime]:
    """US Central transitions under the post-2007 daylight-saving rules."""
    second_sunday_march = first_weekday(year, 3, 6) + dt.timedelta(days=7)
    first_sunday_november = first_weekday(year, 11, 6)
    start = dt.datetime.combine(second_sunday_march, dt.time(8), tzinfo=dt.timezone.utc)
    end = dt.datetime.combine(first_sunday_november, dt.time(7), tzinfo=dt.timezone.utc)
    return start, end


def central_offset(instant_utc: dt.datetime) -> dt.timedelta:
    start, end = central_transitions_utc(instant_utc.year)
    return dt.timedelta(hours=-5 if start <= instant_utc < end else -6)


def utc_to_central(instant_utc: dt.datetime) -> dt.datetime:
    instant_utc = instant_utc.astimezone(dt.timezone.utc)
    return (instant_utc + central_offset(instant_utc)).replace(tzinfo=None)


def central_midnight_utc(day: dt.date) -> dt.datetime:
    """Convert midnight America/Chicago to UTC without a tzdata dependency."""
    dst_start_day = first_weekday(day.year, 3, 6) + dt.timedelta(days=7)
    dst_end_day = first_weekday(day.year, 11, 6)
    # Midnight on spring-transition day is still CST; midnight on fall-transition
    # day is still CDT.
    daylight = dst_start_day < day <= dst_end_day
    offset_hours = 5 if daylight else 6
    return dt.datetime.combine(day, dt.time(), tzinfo=dt.timezone.utc) + dt.timedelta(hours=offset_hours)


def snowflake_time(message_id: Any) -> dt.datetime:
    text = str(message_id or "")
    if not DISCORD_ID_RE.fullmatch(text):
        raise ValueError("not a Discord snowflake")
    milliseconds = (int(text) >> 22) + DISCORD_EPOCH_MS
    return dt.datetime.fromtimestamp(milliseconds / 1000, tz=dt.timezone.utc)


def exact_stage_system_event_timestamp_fallback(row: dict[str, Any], message_id: str) -> bool:
    """Accept the timestamp-less DOM shape used only by exact Discord Stage events.

    This is deliberately narrower than a generic snowflake fallback: the row must be
    an authorless Stage system event, own the exact content label, match a supported
    Discord event phrase, and carry a captured timestamp identical to the message-ID
    snowflake down to the millisecond.
    """

    if not DISCORD_ID_RE.fullmatch(message_id):
        return False
    if str(row.get("collection_channel_kind") or "") != "stage channel":
        return False
    if str(row.get("author") or "").strip() or str(row.get("author_id") or "").strip():
        return False
    if row.get("content_scope_exact") is not True or row.get("timestamp_scope_exact") is not False:
        return False
    labelled_by = str(row.get("article_aria_labelledby") or "").strip()
    if labelled_by not in {
        f"message-content-{message_id}",
        f"message-content-{message_id} message-accessories-{message_id}",
    }:
        return False
    lines = [line.strip() for line in str(row.get("content_text") or "").splitlines() if line.strip()]
    if len(lines) < 3 or not lines[0]:
        return False
    duplicated_stage_speaker_label = bool(
        len(lines) >= 4 and lines[0] and lines[0] == lines[1]
    )
    event_line = lines[2] if duplicated_stage_speaker_label else lines[1]
    stage_event = re.fullmatch(
        r"(?:(?:started|ended)\s+.+|is now a speaker\.)",
        event_line,
        flags=re.IGNORECASE,
    )
    poll_results_present = (
        any(re.match(r"The results?\b", line, flags=re.IGNORECASE) for line in lines)
        and any(re.fullmatch(r"\d+(?:\.\d+)?%", line) for line in lines)
    ) or any(
        re.fullmatch(r"Winning answer • \d+(?:\.\d+)?%", line, flags=re.IGNORECASE)
        for line in lines
    )
    poll_closed = (
        re.fullmatch(r".+['’]s poll .+ has closed\.", lines[0], flags=re.IGNORECASE)
        and poll_results_present
    )
    if not stage_event and not poll_closed:
        return False
    if row.get("timestamp_discrepancy_ms") != 0:
        return False
    try:
        captured = parse_iso_utc(row.get("timestamp_utc"))
        declared_snowflake = parse_iso_utc(row.get("snowflake_timestamp_utc"))
        encoded = snowflake_time(message_id)
    except ValueError:
        return False
    return captured == declared_snowflake == encoded


def exact_pinned_message_system_event_timestamp_fallback(
    row: dict[str, Any], message_id: str
) -> bool:
    """Accept only Discord's exact authorless pinned-message event DOM shape.

    Discord omits the message-specific timestamp ID for this one event type.  The
    fallback remains fail-closed unless the article and content identities are exact,
    the complete five-line event grammar matches, the article owns one un-ID'd time
    descendant outside reply context, and that datetime equals the snowflake exactly.
    """

    if not DISCORD_ID_RE.fullmatch(message_id):
        return False
    if str(row.get("collection_channel_kind") or "") != "text channel":
        return False
    if str(row.get("author") or "").strip() or str(row.get("author_id") or "").strip():
        return False
    if str(row.get("article_id") or "") != f"search-result-{message_id}":
        return False
    if row.get("content_scope_exact") is not True or row.get("timestamp_scope_exact") is not False:
        return False
    if str(row.get("article_aria_labelledby") or "").strip() != f"message-content-{message_id}":
        return False
    lines = [line.strip() for line in str(row.get("content_text") or "").splitlines() if line.strip()]
    if not (
        len(lines) == 5
        and 1 <= len(lines[0]) <= 80
        and lines[1] == "pinned a message to this channel. See all pinned messages."
        and lines[2] == "\u2014"
        and re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2}, \d{1,2}:\d{2} (?:AM|PM)", lines[3])
        and re.fullmatch(
            r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), "
            r"(?:January|February|March|April|May|June|July|August|September|October|November|December) "
            r"\d{1,2}, \d{4} at \d{1,2}:\d{2} (?:AM|PM)",
            lines[4],
        )
    ):
        return False
    if type(row.get("row_owned_time_count")) is not int or row["row_owned_time_count"] != 1:
        return False
    if str(row.get("row_owned_time_element_id") or "").strip():
        return False
    if row.get("timestamp_discrepancy_ms") != 0:
        return False
    try:
        captured = parse_iso_utc(row.get("timestamp_utc"))
        owned = parse_iso_utc(row.get("row_owned_time_datetime"))
        declared_snowflake = parse_iso_utc(row.get("snowflake_timestamp_utc"))
        encoded = snowflake_time(message_id)
    except ValueError:
        return False
    return captured == owned == declared_snowflake == encoded


def exact_discord_system_event_timestamp_fallback(
    row: dict[str, Any], message_id: str
) -> bool:
    return timestamp_scope_revalidation.exact_discord_system_event_timestamp_fallback(
        row, message_id
    )


# Public compatibility aliases keep existing callers/tests on the one shared
# semantic implementation used by the corpus builder and release validators.
exact_stage_system_event_timestamp_fallback = (
    timestamp_scope_revalidation.exact_stage_system_event_timestamp_fallback
)
exact_pinned_message_system_event_timestamp_fallback = (
    timestamp_scope_revalidation.exact_pinned_message_system_event_timestamp_fallback
)


def documented_reply_target_unavailability(message: dict[str, Any]) -> str | None:
    """Classify exact Discord reply-preview states that expose no target snowflake."""

    expected = reply_provenance_contract.classify_documented_no_id_status(message)
    if expected is None:
        return None
    if reply_provenance_contract.documented_no_id_contract_errors(message):
        return None
    return expected


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text).strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def stable_read_json(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValidationError(f"Source changed while being read: {path}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Invalid UTF-8 JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValidationError(f"Top-level JSON value is not an object: {path}")
    return payload, {
        "path": str(path.resolve()),
        "filename": path.name,
        "size_bytes": after.st_size,
        "mtime_ns": after.st_mtime_ns,
        "sha256": sha256_bytes(raw),
    }


def completion_evidence_sidecar_path(segment_path: Path) -> Path:
    return segment_path.with_name(
        f"{segment_path.stem}{COMPLETION_EVIDENCE_SIDECAR_SUFFIX}"
    )


def valid_completion_evidence_timestamp(value: Any) -> bool:
    try:
        parse_iso_utc(value)
    except (TypeError, ValueError):
        return False
    return str(value or "").endswith("Z")


def validate_completion_evidence(
    evidence: Any,
    *,
    query: str,
    reported_total: int,
    reported_pages: int,
) -> list[str]:
    """Independently enforce the collector's terminal-state proof contract."""

    errors: list[str] = []
    if not isinstance(evidence, dict):
        return ["completion_evidence_missing"]
    if evidence.get("schema_version") != "1.0.0":
        errors.append("completion_evidence_schema_invalid")
    if evidence.get("query") != query:
        errors.append("completion_evidence_query_mismatch")
    if evidence.get("reported_total") != reported_total:
        errors.append("completion_evidence_total_mismatch")
    if evidence.get("reported_pages") != reported_pages:
        errors.append("completion_evidence_pages_mismatch")

    submission = evidence.get("search_submission")
    if not isinstance(submission, dict):
        errors.append("search_submission_evidence_missing")
    else:
        if submission.get("query") != query:
            errors.append("search_submission_query_mismatch")
        if not valid_completion_evidence_timestamp(
            submission.get("submitted_at_utc") or submission.get("observed_at_utc")
        ):
            errors.append("search_submission_timestamp_invalid")

    if reported_total == 0:
        if evidence.get("terminal_state") != "stable_empty":
            errors.append("terminal_state_not_stable_empty")
        if (
            not isinstance(submission, dict)
            or submission.get("mode") != "fresh"
            or submission.get("submission_count") != 1
        ):
            errors.append("stable_empty_requires_one_fresh_submission")
        stable = evidence.get("stable_empty")
        observations = stable.get("observations") if isinstance(stable, dict) else None
        if (
            not isinstance(stable, dict)
            or stable.get("required_observations")
            != REQUIRED_STABLE_EMPTY_OBSERVATIONS
        ):
            errors.append("stable_empty_required_count_invalid")
        if (
            not isinstance(observations, list)
            or len(observations) != REQUIRED_STABLE_EMPTY_OBSERVATIONS
        ):
            errors.append("stable_empty_observation_count_invalid")
            observations = observations if isinstance(observations, list) else []
        for index, observation in enumerate(observations, start=1):
            if not isinstance(observation, dict):
                errors.append("stable_empty_observation_not_object")
                continue
            if observation.get("sequence") != index:
                errors.append("stable_empty_sequence_invalid")
            if observation.get("state") != "empty_candidate":
                errors.append("stable_empty_state_invalid")
            if observation.get("visible_result_count") != 0:
                errors.append("stable_empty_visible_count_nonzero")
            if not valid_completion_evidence_timestamp(
                observation.get("observed_at_utc")
            ):
                errors.append("stable_empty_timestamp_invalid")
            if "no results" not in str(observation.get("panel_text") or "").casefold():
                errors.append("stable_empty_panel_text_invalid")
    elif reported_total > 0:
        if evidence.get("terminal_state") != "stable_bottom":
            errors.append("terminal_state_not_stable_bottom")
        stable = evidence.get("stable_bottom")
        observations = stable.get("observations") if isinstance(stable, dict) else None
        if (
            not isinstance(stable, dict)
            or stable.get("required_observations")
            != REQUIRED_STABLE_BOTTOM_OBSERVATIONS
        ):
            errors.append("stable_bottom_required_count_invalid")
        if (
            not isinstance(observations, list)
            or len(observations) != REQUIRED_STABLE_BOTTOM_OBSERVATIONS
        ):
            errors.append("stable_bottom_observation_count_invalid")
            observations = observations if isinstance(observations, list) else []
        expected_first = (reported_pages - 1) * 25 + 1
        expected_visible = reported_total - expected_first + 1
        for index, observation in enumerate(observations, start=1):
            if not isinstance(observation, dict):
                errors.append("stable_bottom_observation_not_object")
                continue
            if observation.get("sequence") != index:
                errors.append("stable_bottom_sequence_invalid")
            if not valid_completion_evidence_timestamp(
                observation.get("observed_at_utc")
            ):
                errors.append("stable_bottom_timestamp_invalid")
            if observation.get("query") != query:
                errors.append("stable_bottom_query_mismatch")
            if observation.get("current_page") != reported_pages:
                errors.append("stable_bottom_page_mismatch")
            if observation.get("first_result_index") != expected_first:
                errors.append("stable_bottom_first_index_mismatch")
            if observation.get("last_result_index") != reported_total:
                errors.append("stable_bottom_last_index_mismatch")
            if observation.get("visible_result_count") != expected_visible:
                errors.append("stable_bottom_visible_count_mismatch")
            if observation.get("result_set_size") != reported_total:
                errors.append("stable_bottom_total_mismatch")
            if observation.get("has_enabled_next") is not False:
                errors.append("stable_bottom_next_disabled_not_proven")
    return sorted(set(errors))


def resolve_completion_evidence(
    path: Path,
    payload: dict[str, Any],
    source_sha256: str,
) -> tuple[Any, str, list[str]]:
    """Resolve inline proof or a SHA-bound sidecar without trusting either."""

    inline = payload.get("completion_evidence")
    sidecar_path = completion_evidence_sidecar_path(path)
    sidecar_exists = sidecar_path.is_file()
    errors: list[str] = []
    if isinstance(inline, dict):
        if sidecar_exists:
            errors.append("inline_and_sidecar_completion_evidence_ambiguous")
        return inline, "inline", errors
    if not sidecar_exists:
        return None, "missing", errors
    try:
        sidecar, _sidecar_source = stable_read_json(sidecar_path)
    except (OSError, ValidationError) as exc:
        return None, "sidecar_invalid", [str(exc)]
    if sidecar.get("artifact_type") != "discord_segment_completion_evidence_sidecar":
        errors.append("completion_evidence_sidecar_artifact_type_invalid")
    if sidecar.get("schema_version") != "1.0.0":
        errors.append("completion_evidence_sidecar_schema_invalid")
    if str(sidecar.get("source_artifact_sha256") or "").casefold() != str(
        source_sha256 or ""
    ).casefold():
        errors.append("completion_evidence_sidecar_source_hash_mismatch")
    if sidecar.get("source_artifact_path") != path.name:
        errors.append("completion_evidence_sidecar_source_path_mismatch")
    if sidecar.get("guild_id") != payload.get("guild_id"):
        errors.append("completion_evidence_sidecar_guild_mismatch")
    if sidecar.get("segment") != payload.get("segment"):
        errors.append("completion_evidence_sidecar_segment_mismatch")
    if sidecar.get("reported_total") != payload.get("reported_total"):
        errors.append("completion_evidence_sidecar_total_mismatch")
    if sidecar.get("reported_pages") != payload.get("reported_pages"):
        errors.append("completion_evidence_sidecar_pages_mismatch")
    sidecar_container = sidecar.get("requested_container")
    if not isinstance(sidecar_container, dict) or sidecar_container != payload.get(
        "requested_container"
    ):
        errors.append("completion_evidence_sidecar_container_mismatch")
    return sidecar.get("completion_evidence"), "sidecar", errors


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def limited(values: Iterable[Any], maximum: int = 20) -> list[Any]:
    output: list[Any] = []
    for value in values:
        if len(output) >= maximum:
            break
        output.append(value)
    return output


@dataclass
class CheckRecorder:
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        name: str,
        passed: bool | None,
        severity: str,
        observed: Any,
        expected: Any,
        *,
        dimension: str,
        examples: list[Any] | None = None,
        note: str | None = None,
    ) -> None:
        row: dict[str, Any] = {
            "name": name,
            "dimension": dimension,
            "severity": severity,
            "passed": passed,
            "observed": observed,
            "expected": expected,
        }
        if examples:
            row["examples"] = examples[:20]
        if note:
            row["note"] = note
        self.checks.append(row)

    def failure_count(self, severities: set[str] | None = None) -> int:
        allowed = severities or {"critical", "high", "medium", "low"}
        return sum(row["passed"] is False and row["severity"] in allowed for row in self.checks)


@dataclass
class SegmentArtifact:
    path: Path
    payload: dict[str, Any]
    source_record: dict[str, Any]
    scope: str
    scope_key: str
    channel_name: str | None
    channel_id: str | None
    start: dt.date
    end: dt.date
    complete: bool
    messages: list[dict[str, Any]]
    captured_at_utc: str | None
    input_role: str = "channel_capture"
    timestamp_scope_integrity: dict[str, Any] = field(default_factory=dict)
    timestamp_scope_source_records: list[dict[str, Any]] = field(
        default_factory=list
    )
    premium_forum_provenance_source_records: list[dict[str, Any]] = field(
        default_factory=list
    )
    executed_command_reply_provenance_integrity: dict[str, Any] = field(
        default_factory=dict
    )


ISSUE_META: dict[str, tuple[str, str, str]] = {
    "invalid_source": ("critical", "valid UTF-8 JSON segment files", "shape"),
    "invalid_segment_schema": ("critical", "required segment metadata and typed counts", "shape"),
    "guild_mismatch": ("critical", "the configured Discord guild only", "validity"),
    "invalid_scope": ("critical", f"one of {sorted(ALLOWED_SCOPES)}", "validity"),
    "missing_capture_timestamp": ("high", "capture timestamp on every source artifact", "timeliness"),
    "filename_completion_mismatch": ("high", "partial suffix agrees with complete=false", "consistency"),
    "invalid_query_boundary": ("critical", "after=start-1 day and before=end+1 day", "coverage"),
    "invalid_totals_pages_indices": ("critical", "reported totals, pages, rows, IDs, and indices reconcile", "integrity"),
    "invalid_zero_result_segment": ("critical", "zero-result completed segment has all zero counters", "coverage"),
    "invalid_completion_evidence": (
        "critical",
        "numeric collector versions marked complete carry valid inline or SHA-bound terminal-state proof",
        "coverage",
    ),
    "invalid_message_identity": ("critical", "valid unique message IDs within each artifact", "uniqueness"),
    "selector_identity_mismatch": ("critical", "article labels/selectors refer to the current message ID", "extraction"),
    "timestamp_scope_not_exact": ("high", "message-specific DOM timestamp selector or snowflake-qualified fallback", "extraction"),
    "invalid_timestamp_scope_revalidation": (
        "critical",
        "adjacent sidecar binds exact final segment/message bytes and preserved DOM evidence",
        "provenance",
    ),
    "invalid_executed_command_reply_provenance": (
        "critical",
        "exact message-bound Discord application-command DOM evidence with no target candidate",
        "provenance",
    ),
    "container_scope_mismatch": ("critical", "all results belong to the explicitly requested container scope", "coverage"),
    "missing_exact_container_id": ("high", "exact channel/thread ID for every captured message", "coverage"),
    "invalid_premium_forum_provenance": (
        "critical",
        "byte-bound Premium page plans/checkpoints and exact row-owned child IDs",
        "provenance",
    ),
    "invalid_message_timestamp": ("critical", "parseable timezone-aware Discord timestamp", "validity"),
    "snowflake_timestamp_mismatch": ("critical", "DOM and message-ID timestamps differ by at most one second", "consistency"),
    "segment_local_date_mismatch": ("critical", "timestamp falls within the segment's Central-local dates", "coverage"),
    "requested_window_mismatch": ("critical", "timestamp falls within the requested Central-local window", "coverage"),
    "message_after_cutoff": ("critical", "timestamp does not exceed the declared data cutoff", "timeliness"),
    "invalid_discord_source_url": ("high", "permalink/attachment evidence URLs are Discord-hosted", "provenance"),
    "source_changed_after_read": ("critical", "source artifacts remain stable throughout validation", "provenance"),
}


def add_issue(issues: dict[str, list[dict[str, Any]]], code: str, **detail: Any) -> None:
    issues.setdefault(code, []).append(detail)


def discover_json_files(inputs: list[Path]) -> list[Path]:
    found: set[Path] = set()
    for supplied in inputs:
        path = supplied.resolve()
        if path.is_file():
            if (
                path.suffix.lower() == ".json"
                and not path.name.endswith(".completion-evidence.json")
                and not path.name.endswith(
                    TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX
                )
            ):
                found.add(path)
        elif path.is_dir():
            found.update(
                candidate.resolve()
                for candidate in path.rglob("*.json")
                if candidate.is_file()
                and not candidate.name.endswith(".completion-evidence.json")
                and not candidate.name.endswith(
                    TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX
                )
            )
        else:
            raise ValidationError(f"Segment input does not exist: {path}")
    return sorted(found, key=lambda item: str(item).casefold())


def discover_segment_files_by_role(
    groups: dict[str, list[Path]],
) -> dict[Path, str]:
    result: dict[Path, str] = {}
    for role, inputs in groups.items():
        for path in discover_json_files(inputs):
            previous = result.get(path)
            if previous and previous != role:
                raise ValidationError(
                    f"Segment file {path} was selected as both {previous} and {role}"
                )
            result[path] = role
    return dict(sorted(result.items(), key=lambda item: str(item[0]).casefold()))


def progress_manifest_segment_inputs(
    progress_path: Path,
) -> dict[str, list[Path]]:
    payload, _source = stable_read_json(progress_path.resolve())
    declared_root = Path(str(payload.get("root") or ""))
    if not declared_root.is_absolute():
        declared_root = progress_path.resolve().parent.parent / declared_root
    declared_root = declared_root.resolve()
    result: dict[str, set[Path]] = {
        "channel_capture": set(),
        "relevance_query": set(),
        "residual_audit": set(),
    }
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return {key: [] for key in result}
    for row in artifacts:
        if not isinstance(row, dict):
            continue
        relative = Path(str(row.get("relative_path") or ""))
        if relative.is_absolute() or not str(relative):
            continue
        candidate = (declared_root / relative).resolve()
        try:
            candidate.relative_to(declared_root)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        lowered = {part.casefold() for part in relative.parts}
        if "relevance_segments" in lowered:
            role = "relevance_query"
        elif "relevance_audit_segments" in lowered or "audit_segments" in lowered:
            role = "residual_audit"
        elif {"channel_segments", "channel_segments_v2_5"} & lowered:
            role = "channel_capture"
        else:
            continue
        result[role].add(candidate)
    return {
        key: sorted(values, key=lambda path: str(path).casefold())
        for key, values in result.items()
    }


def requested_container(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("requested_container")
    return value if isinstance(value, dict) else {}


def extract_requested_container_id(payload: dict[str, Any]) -> str | None:
    for source in (payload, requested_container(payload)):
        for key in MESSAGE_CONTAINER_FIELDS:
            value = str(source.get(key) or "").strip()
            if DISCORD_ID_RE.fullmatch(value):
                return value
    scope = payload.get("collection_scope")
    if isinstance(scope, dict):
        for key in MESSAGE_CONTAINER_FIELDS:
            value = str(scope.get(key) or "").strip()
            if DISCORD_ID_RE.fullmatch(value):
                return value
    return None


def extract_exact_container_id(message: dict[str, Any], payload: dict[str, Any]) -> str | None:
    for key in MESSAGE_CONTAINER_FIELDS:
        value = str(message.get(key) or "").strip()
        if DISCORD_ID_RE.fullmatch(value):
            return value
    requested = requested_container(payload)
    requested_id = extract_requested_container_id(payload)
    requested_name = normalize_text(requested.get("channel_name") or requested.get("name"))
    displayed_container = normalize_text(message.get("thread_title") or message.get("channel_name"))
    # A channel-scoped search proves the parent selection. It proves the message
    # container too only when the displayed result is the selected channel, not
    # a child forum/thread whose title differs from its parent.
    if requested_id and requested_name and displayed_container == requested_name:
        return requested_id
    return None


def infer_scope(payload: dict[str, Any]) -> tuple[str, str, str | None, str | None]:
    raw_scope = payload.get("collection_scope")
    scope = str(raw_scope.get("kind") if isinstance(raw_scope, dict) else raw_scope or "").strip().lower()
    if scope in {"guild", "guild_wide", "server-wide", "server_wide"}:
        scope = "guild-wide"
    elif scope in {"channel", "channel_scoped"}:
        scope = "channel-scoped"
    elif scope in {"thread", "thread_scoped"}:
        scope = "thread-scoped"
    segment = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
    query = str(segment.get("query") or "")
    channel_match = QUERY_CHANNEL_RE.search(query)
    channel_name = channel_match.group(1).strip() if channel_match else None
    channel_id = extract_requested_container_id(payload)
    requested = requested_container(payload)
    if not channel_name:
        channel_name = str(requested.get("channel_name") or requested.get("name") or "").strip() or None
    if scope == "guild-wide":
        return scope, f"guild:{payload.get('guild_id') or 'unknown'}", None, None
    if channel_id:
        return scope, f"container:{channel_id}", channel_name, channel_id
    return scope, f"container-name:{channel_name or 'unknown'}", channel_name, None


def validate_one_segment(
    path: Path,
    *,
    guild_id: str,
    window_start: dt.date,
    window_end: dt.date,
    cutoff_utc: dt.datetime,
    issues: dict[str, list[dict[str, Any]]],
    input_role: str = "channel_capture",
) -> SegmentArtifact | None:
    try:
        payload, source_record = stable_read_json(path)
    except (OSError, ValidationError) as exc:
        add_issue(issues, "invalid_source", file=str(path), error=str(exc))
        return None
    source_record["input_role"] = input_role

    required = {
        "segment",
        "reported_total",
        "reported_pages",
        "pages_captured",
        "captured_rows",
        "unique_message_ids",
        "gap_indices",
        "complete",
        "messages",
    }
    if not required <= set(payload) or not isinstance(payload.get("segment"), dict) or not isinstance(
        payload.get("messages"), list
    ):
        add_issue(issues, "invalid_segment_schema", file=str(path), missing=sorted(required - set(payload)))
        return None
    try:
        start = parse_date(payload["segment"]["start"])
        end = parse_date(payload["segment"]["end"])
        total = int(payload["reported_total"])
        reported_pages = int(payload["reported_pages"])
        pages_captured = int(payload["pages_captured"])
        captured_rows = int(payload["captured_rows"])
        declared_unique = int(payload["unique_message_ids"])
        complete = payload["complete"] is True
        if start > end or min(total, reported_pages, pages_captured, captured_rows, declared_unique) < 0:
            raise ValueError("invalid segment order or negative count")
    except (KeyError, TypeError, ValueError) as exc:
        add_issue(issues, "invalid_segment_schema", file=str(path), error=str(exc))
        return None

    scope, scope_key, channel_name, channel_id = infer_scope(payload)
    if str(payload.get("guild_id") or "") != guild_id:
        add_issue(issues, "guild_mismatch", file=path.name, observed=payload.get("guild_id"))
    if scope not in ALLOWED_SCOPES:
        add_issue(issues, "invalid_scope", file=path.name, observed=scope)
    try:
        container_mismatch_count = int(payload.get("container_mismatch_count") or 0)
    except (TypeError, ValueError):
        container_mismatch_count = -1
    container_mismatch_ids = payload.get("container_mismatch_message_ids") or []
    if container_mismatch_count != 0 or container_mismatch_ids:
        add_issue(
            issues,
            "container_scope_mismatch",
            file=path.name,
            mismatch_count=container_mismatch_count,
            message_ids=limited(container_mismatch_ids if isinstance(container_mismatch_ids, list) else []),
        )
    if not payload.get("captured_at_utc") and not payload.get("generated_at_utc"):
        add_issue(issues, "missing_capture_timestamp", file=path.name)
    captured_at = str(payload.get("captured_at_utc") or payload.get("generated_at_utc") or "").strip() or None
    if path.name.endswith(".partial.json") == complete:
        add_issue(issues, "filename_completion_mismatch", file=path.name, complete=complete)

    query = str(payload["segment"].get("query") or "")
    collector_version = str(payload.get("collector_version") or "").strip()
    if complete and re.fullmatch(r"\d+\.\d+", collector_version):
        evidence, evidence_source, evidence_errors = resolve_completion_evidence(
            path, payload, str(source_record.get("sha256") or "")
        )
        evidence_errors.extend(
            validate_completion_evidence(
                evidence,
                query=query,
                reported_total=total,
                reported_pages=reported_pages,
            )
        )
        if evidence_source == "missing":
            evidence_errors.append(
                "completion_evidence_missing_recapture_or_sidecar_required"
            )
        if evidence_errors:
            add_issue(
                issues,
                "invalid_completion_evidence",
                file=path.name,
                source=evidence_source,
                errors=sorted(set(evidence_errors)),
            )
    after = QUERY_AFTER_RE.search(query)
    before = QUERY_BEFORE_RE.search(query)
    expected_after = start - dt.timedelta(days=1)
    expected_before = end + dt.timedelta(days=1)
    try:
        query_valid = bool(
            after
            and before
            and parse_date(after.group(1)) == expected_after
            and parse_date(before.group(1)) == expected_before
        )
    except ValueError:
        query_valid = False
    if not query_valid:
        add_issue(
            issues,
            "invalid_query_boundary",
            file=path.name,
            query=query,
            expected_after=expected_after.isoformat(),
            expected_before=expected_before.isoformat(),
        )

    messages = [row for row in payload["messages"] if isinstance(row, dict)]
    malformed_rows = len(payload["messages"]) - len(messages)
    ids = [str(row.get("message_id") or "") for row in messages]
    indices: list[int] = []
    invalid_indices = 0
    for row in messages:
        try:
            indices.append(int(row.get("result_index")))
        except (TypeError, ValueError):
            invalid_indices += 1
    expected_pages = math.ceil(total / 25) if total else 0
    count_errors: dict[str, Any] = {}
    if reported_pages != expected_pages:
        count_errors["reported_pages"] = {"observed": reported_pages, "expected": expected_pages}
    if captured_rows != len(messages):
        count_errors["captured_rows"] = {"observed": captured_rows, "expected": len(messages)}
    if declared_unique != len(set(ids)):
        count_errors["unique_message_ids"] = {"observed": declared_unique, "expected": len(set(ids))}
    if malformed_rows:
        count_errors["malformed_message_rows"] = malformed_rows
    if invalid_indices:
        count_errors["invalid_result_indices"] = invalid_indices
    if pages_captured > reported_pages:
        count_errors["pages_captured_gt_reported"] = {"observed": pages_captured, "reported": reported_pages}
    if complete:
        expected_indices = set(range(1, total + 1))
        if total != len(messages) or total != len(set(ids)) or set(indices) != expected_indices or pages_captured != reported_pages:
            count_errors["complete_reconciliation"] = {
                "reported_total": total,
                "message_rows": len(messages),
                "unique_ids": len(set(ids)),
                "unique_indices": len(set(indices)),
                "pages_captured": pages_captured,
                "reported_pages": reported_pages,
            }
    elif indices and set(indices) != set(range(1, max(indices) + 1)):
        count_errors["partial_internal_index_gaps"] = limited(sorted(set(range(1, max(indices) + 1)) - set(indices)))
    if any(index < 1 or index > max(total, 1) for index in indices):
        count_errors["indices_out_of_range"] = limited(index for index in indices if index < 1 or index > max(total, 1))
    for row, index in zip(messages, indices):
        try:
            page = int(row.get("page_number"))
        except (TypeError, ValueError):
            page = -1
        if page != ((index - 1) // 25 + 1):
            count_errors.setdefault("page_index_mismatches", []).append(
                {"message_id": row.get("message_id"), "index": index, "page": page}
            )
            if len(count_errors["page_index_mismatches"]) >= 20:
                break
    if count_errors:
        add_issue(issues, "invalid_totals_pages_indices", file=path.name, errors=count_errors)

    if total == 0 and complete:
        if any((reported_pages, pages_captured, captured_rows, declared_unique, len(messages))) or payload.get("gap_indices"):
            add_issue(
                issues,
                "invalid_zero_result_segment",
                file=path.name,
                counters={
                    "reported_pages": reported_pages,
                    "pages_captured": pages_captured,
                    "captured_rows": captured_rows,
                    "unique_message_ids": declared_unique,
                    "message_rows": len(messages),
                    "gap_indices": payload.get("gap_indices"),
                },
            )

    duplicate_ids = [message_id for message_id, count in collections.Counter(ids).items() if count > 1]
    invalid_ids = [message_id for message_id in ids if not DISCORD_ID_RE.fullmatch(message_id)]
    if invalid_ids or duplicate_ids:
        add_issue(
            issues,
            "invalid_message_identity",
            file=path.name,
            invalid_ids=limited(invalid_ids),
            duplicate_ids=limited(duplicate_ids),
        )

    try:
        path.resolve().relative_to(PACKAGE_DIR.resolve())
        timestamp_artifact_root = PACKAGE_DIR
    except ValueError:
        timestamp_artifact_root = (
            path.parents[2]
            if path.parent.name in {"channel_segments", "channel_segments_v2_5"}
            and path.parent.parent.name == "raw"
            else path.parent
        )
    premium_parent_requested = (
        extract_requested_container_id(payload)
        == premium_journals_provenance_contract.PREMIUM_ID
    )
    premium_row_container_ids: dict[str, str] = {}
    premium_forum_provenance_source_records: list[dict[str, Any]] = []
    if premium_parent_requested:
        try:
            premium_audit = (
                premium_journals_provenance_contract
                .validate_premium_row_container_bindings(
                    path,
                    artifact_root=timestamp_artifact_root,
                    source_artifact_sha256=str(source_record.get("sha256") or ""),
                )
            )
            premium_row_container_ids = dict(
                premium_audit.get("row_child_container_ids") or {}
            )
            navigation_binding_errors: list[str] = []
            for bound in premium_audit["accepted_artifact"]["source_files"]:
                role = str(bound.get("role") or "")
                if role not in {
                    "forum_navigation_page_plan",
                    "forum_group_navigation_checkpoint",
                }:
                    continue
                bound_path = (
                    timestamp_artifact_root / str(bound.get("path") or "")
                ).resolve()
                try:
                    _bound_payload, bound_record = stable_read_json(bound_path)
                except (OSError, ValidationError) as exc:
                    navigation_binding_errors.append(
                        f"{role}:{bound.get('path')}:unreadable:{exc}"
                    )
                    continue
                if (
                    str(bound_record.get("sha256") or "").lower()
                    != str(bound.get("sha256") or "").lower()
                    or bound_record.get("size_bytes") != bound.get("bytes")
                ):
                    navigation_binding_errors.append(
                        f"{role}:{bound.get('path')}:byte_binding_mismatch"
                    )
                    continue
                bound_record["kind"] = role
                bound_record["input_role"] = "premium_forum_provenance"
                premium_forum_provenance_source_records.append(bound_record)
            if navigation_binding_errors:
                premium_row_container_ids = {}
                add_issue(
                    issues,
                    "invalid_premium_forum_provenance",
                    file=path.name,
                    errors=limited(navigation_binding_errors),
                )
        except premium_journals_provenance_contract.PremiumJournalsContractError as exc:
            add_issue(
                issues,
                "invalid_premium_forum_provenance",
                file=path.name,
                error=str(exc),
            )
    timestamp_revalidation = (
        timestamp_scope_revalidation.load_adjacent_timestamp_scope_revalidation(
            path,
            payload,
            source_artifact_sha256=str(source_record.get("sha256") or ""),
            artifact_root=timestamp_artifact_root,
        )
    )
    timestamp_scope_integrity = (
        timestamp_scope_revalidation.audit_segment_timestamp_scopes(
            messages, timestamp_revalidation
        )
    )
    for detail in timestamp_scope_integrity.get("unresolved") or []:
        add_issue(
            issues,
            "timestamp_scope_not_exact",
            file=path.name,
            **detail,
        )
    if (
        timestamp_scope_integrity.get("sidecar_error_count")
        or timestamp_scope_integrity.get("unused_revalidation_record_count")
    ):
        add_issue(
            issues,
            "invalid_timestamp_scope_revalidation",
            file=path.name,
            sidecar_errors=timestamp_scope_integrity.get("sidecar_errors"),
            unused_message_ids=timestamp_scope_integrity.get(
                "unused_revalidation_message_ids"
            ),
        )
    timestamp_scope_source_records: list[dict[str, Any]] = []
    for artifact in timestamp_revalidation.source_artifacts():
        artifact_path = Path(artifact["path"])
        try:
            _artifact_payload, artifact_record = stable_read_json(artifact_path)
        except (OSError, ValidationError) as exc:
            add_issue(
                issues,
                "invalid_timestamp_scope_revalidation",
                file=path.name,
                error=f"timestamp_scope_evidence_unreadable:{exc}",
            )
            continue
        artifact_record["kind"] = str(
            artifact.get("kind") or "timestamp_scope_evidence"
        )
        artifact_record["input_role"] = "timestamp_scope_evidence"
        timestamp_scope_source_records.append(artifact_record)

    expected_executed_command_ids: list[str] = []
    if channel_id == "1273692573898113076":
        if start == dt.date(2026, 6, 30) and end == dt.date(2026, 7, 6):
            expected_executed_command_ids = [
                reply_provenance_contract.EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID
            ]
    executed_command_reply_provenance_integrity = (
        reply_provenance_contract.audit_executed_command_contexts(
            messages,
            expected_message_ids=expected_executed_command_ids,
        )
    )
    if not executed_command_reply_provenance_integrity["passed"]:
        add_issue(
            issues,
            "invalid_executed_command_reply_provenance",
            file=path.name,
            audit=executed_command_reply_provenance_integrity,
        )

    for row in messages:
        message_id = str(row.get("message_id") or "")
        article_id = str(row.get("article_id") or "")
        labelled_by = str(row.get("article_aria_labelledby") or "")
        selector_problem = False
        if article_id and article_id != f"search-result-{message_id}":
            selector_problem = True
        if row.get("content_present") is True and labelled_by and f"message-content-{message_id}" not in labelled_by:
            selector_problem = True
        if row.get("content_scope_exact") is False:
            selector_problem = True
        if selector_problem:
            add_issue(
                issues,
                "selector_identity_mismatch",
                file=path.name,
                message_id=message_id,
                article_id=article_id,
                article_aria_labelledby=labelled_by,
            )

        exact_container = (
            premium_row_container_ids.get(message_id)
            if premium_parent_requested
            else extract_exact_container_id(row, payload)
        )
        if not exact_container:
            add_issue(issues, "missing_exact_container_id", file=path.name, message_id=message_id)

        try:
            timestamp = parse_iso_utc(row.get("timestamp_utc"))
        except ValueError as exc:
            add_issue(issues, "invalid_message_timestamp", file=path.name, message_id=message_id, error=str(exc))
            continue
        try:
            encoded = snowflake_time(message_id)
            delta_seconds = abs((timestamp - encoded).total_seconds())
            if delta_seconds > 1:
                add_issue(
                    issues,
                    "snowflake_timestamp_mismatch",
                    file=path.name,
                    message_id=message_id,
                    timestamp_utc=row.get("timestamp_utc"),
                    snowflake_timestamp_utc=encoded.isoformat().replace("+00:00", "Z"),
                    delta_seconds=round(delta_seconds, 3),
                )
        except ValueError:
            pass
        local_day = utc_to_central(timestamp).date()
        if not start <= local_day <= end:
            add_issue(
                issues,
                "segment_local_date_mismatch",
                file=path.name,
                message_id=message_id,
                timestamp_utc=row.get("timestamp_utc"),
                central_date=local_day.isoformat(),
                segment=[start.isoformat(), end.isoformat()],
            )
        if not window_start <= local_day <= window_end:
            add_issue(
                issues,
                "requested_window_mismatch",
                file=path.name,
                message_id=message_id,
                central_date=local_day.isoformat(),
            )
        if timestamp > cutoff_utc:
            add_issue(
                issues,
                "message_after_cutoff",
                file=path.name,
                message_id=message_id,
                timestamp_utc=row.get("timestamp_utc"),
                cutoff_utc=cutoff_utc.isoformat().replace("+00:00", "Z"),
            )

        permalink = str(row.get("permalink") or row.get("inferred_permalink") or "").strip()
        if permalink:
            parsed = urlparse(permalink)
            if parsed.hostname not in DISCORD_SOURCE_HOSTS or "undefined" in permalink:
                add_issue(
                    issues,
                    "invalid_discord_source_url",
                    file=path.name,
                    message_id=message_id,
                    field="permalink",
                    url=permalink,
                )
        for attachment in row.get("attachments") or []:
            if not isinstance(attachment, dict):
                continue
            url = str(attachment.get("url") or "").strip()
            if url and urlparse(url).hostname not in DISCORD_SOURCE_HOSTS:
                add_issue(
                    issues,
                    "invalid_discord_source_url",
                    file=path.name,
                    message_id=message_id,
                    field="attachment",
                    url=url,
                )

    return SegmentArtifact(
        path=path,
        payload=payload,
        source_record=source_record,
        scope=scope,
        scope_key=scope_key,
        channel_name=channel_name,
        channel_id=channel_id,
        start=start,
        end=end,
        complete=complete,
        messages=messages,
        captured_at_utc=captured_at,
        input_role=input_role,
        timestamp_scope_integrity=timestamp_scope_integrity,
        timestamp_scope_source_records=timestamp_scope_source_records,
        premium_forum_provenance_source_records=(
            premium_forum_provenance_source_records
        ),
        executed_command_reply_provenance_integrity=(
            executed_command_reply_provenance_integrity
        ),
    )


def iter_days(start: dt.date, end: dt.date) -> Iterator[dt.date]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += dt.timedelta(days=1)


def load_inventory(path: Path | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if path is None:
        return None, []
    payload, _ = stable_read_json(path.resolve())
    rows: list[dict[str, Any]] = []
    for key in ("channel_inventory", "channels", "threads", "containers", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    if not rows and isinstance(payload.get("inventory"), list):
        rows.extend(item for item in payload["inventory"] if isinstance(item, dict))
    return payload, rows


def inventory_identity(row: dict[str, Any]) -> str:
    return str(row.get("channel_id") or row.get("thread_id") or row.get("container_id") or row.get("id") or "").strip()


def inventory_name(row: dict[str, Any]) -> str:
    return str(row.get("name") or row.get("channel_name") or row.get("thread_title") or "").strip()


def inventory_required(row: dict[str, Any]) -> bool:
    if row.get("accessible") is False or row.get("is_accessible") is False:
        return False
    if row.get("message_capable") is False:
        return False
    kind = str(row.get("kind") or row.get("type") or "").casefold()
    return not any(label in kind for label in ("category", "voice", "stage", "directory"))


def validate_inventory_contract(
    recorder: CheckRecorder,
    payload: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    guild_id: str,
    window_start: dt.date,
    window_end: dt.date,
) -> dict[str, Any]:
    if payload is None:
        return {"status": "not_supplied", "rows": 0}
    source_scope = str(payload.get("source_scope") or "").replace(" ", "_").casefold()
    outside_sources_used = payload.get("outside_sources_used")
    recorder.add(
        "inventory_discord_only_scope",
        str(payload.get("guild_id") or "") == guild_id
        and source_scope == "discord_only"
        and outside_sources_used is False,
        "critical",
        {
            "guild_id": payload.get("guild_id"),
            "source_scope": payload.get("source_scope"),
            "outside_sources_used": outside_sources_used,
        },
        {"guild_id": guild_id, "source_scope": "discord_only", "outside_sources_used": False},
        dimension="provenance",
    )
    window = payload.get("requested_local_window") if isinstance(payload.get("requested_local_window"), dict) else {}
    expected_start = central_midnight_utc(window_start).isoformat()
    expected_end = central_midnight_utc(window_end + dt.timedelta(days=1)).isoformat()
    observed_start = str(window.get("start_inclusive") or "")
    observed_end = str(window.get("end_exclusive") or "")
    try:
        boundary_valid = (
            str(window.get("timezone") or "") == "America/Chicago"
            and parse_iso_utc(observed_start) == parse_iso_utc(expected_start)
            and parse_iso_utc(observed_end) == parse_iso_utc(expected_end)
        )
    except ValueError:
        boundary_valid = False
    recorder.add(
        "inventory_window_contract",
        boundary_valid,
        "critical",
        window,
        {
            "timezone": "America/Chicago",
            "start_utc": expected_start,
            "end_exclusive_utc": expected_end,
        },
        dimension="coverage",
    )
    captured_at = payload.get("captured_at_utc") or payload.get("capture_as_of_utc")
    try:
        parsed_capture_timestamp = parse_iso_utc(captured_at)
        capture_timestamp_valid = parsed_capture_timestamp is not None
    except ValueError:
        parsed_capture_timestamp = None
        capture_timestamp_valid = False
    recorder.add(
        "inventory_capture_timestamp",
        capture_timestamp_valid,
        "high",
        captured_at,
        "timezone-aware capture timestamp",
        dimension="timeliness",
    )

    declared_complete = payload.get("inventory_complete") is True or str(payload.get("status") or "").casefold() in {
        "complete",
        "completed",
    }
    completeness = (
        payload.get("completeness")
        if isinstance(payload.get("completeness"), dict)
        else {}
    )
    forum_scope = (
        payload.get("accessible_scope", {}).get("forum_threads", {})
        if isinstance(payload.get("accessible_scope"), dict)
        and isinstance(payload.get("accessible_scope", {}).get("forum_threads"), dict)
        else {}
    )
    ordinary_scope = (
        payload.get("accessible_scope", {}).get("ordinary_threads", {})
        if isinstance(payload.get("accessible_scope"), dict)
        and isinstance(payload.get("accessible_scope", {}).get("ordinary_threads"), dict)
        else {}
    )
    post_cutoff_scope = (
        payload.get("accessible_scope", {}).get(
            "post_cutoff_navigation_resnapshot", {}
        )
        if isinstance(payload.get("accessible_scope"), dict)
        and isinstance(
            payload.get("accessible_scope", {}).get(
                "post_cutoff_navigation_resnapshot"
            ),
            dict,
        )
        else {}
    )
    top_level_ids = {
        inventory_identity(row)
        for row in rows
        if row.get("inventory_layer") == "top_level_container"
        and inventory_identity(row)
    }
    requires_all_parent_thread_contract = len(top_level_ids) == 38
    ordinary_evidence = (
        ordinary_scope.get("completion_evidence")
        if isinstance(ordinary_scope.get("completion_evidence"), dict)
        else {}
    )
    audited_parent_ids = {
        str(value)
        for value in ordinary_evidence.get("audited_parent_ids", [])
        if value
    }
    ordinary_complete = bool(
        ordinary_scope.get("declared_complete") is True
        and ordinary_scope.get("validated_complete") is True
        and ordinary_scope.get("status") == "complete"
        and int(ordinary_scope.get("expected_parent_audit_count") or -1) == 38
        and int(ordinary_scope.get("audited_parent_count") or -1) == 38
        and ordinary_evidence.get("authenticated") is True
        and ordinary_evidence.get("parent_audits_complete") is True
        and audited_parent_ids == top_level_ids
        and int(ordinary_evidence.get("unresolved_observed_occurrence_count") or 0)
        == 0
    )
    post_cutoff_evidence = (
        post_cutoff_scope.get("completion_evidence")
        if isinstance(post_cutoff_scope.get("completion_evidence"), dict)
        else {}
    )
    try:
        post_cutoff_completed = parse_iso_utc(
            post_cutoff_evidence.get("capture_completed_at_utc")
        )
        required_end = parse_iso_utc(expected_end)
    except ValueError:
        post_cutoff_completed = None
        required_end = None
    post_cutoff_complete = bool(
        post_cutoff_scope.get("declared_complete") is True
        and post_cutoff_scope.get("status") == "complete"
        and post_cutoff_evidence.get("authenticated") is True
        and post_cutoff_evidence.get("navigation_pass_complete") is True
        and post_cutoff_evidence.get("terminal_state_observed") is True
        and isinstance(post_cutoff_evidence.get("source_refs"), list)
        and bool(post_cutoff_evidence.get("source_refs"))
        and post_cutoff_completed is not None
        and required_end is not None
        and post_cutoff_completed >= required_end
        and parsed_capture_timestamp is not None
        and parsed_capture_timestamp >= required_end
    )
    active_threads_complete = bool(
        payload.get("active_threads_complete") is True
        or completeness.get("active_forum_thread_enumeration_complete") is True
    )
    archived_threads_complete = bool(
        payload.get("archived_threads_complete") is True
        or completeness.get("discoverable_archived_forum_thread_enumeration_complete")
        is True
    )
    # A complete merged inventory with no forum parent does not need synthetic
    # active/archive booleans.  When a forum exists, require the independently
    # validated nested pass flags emitted by merge_forum_thread_inventory.py.
    forum_declared_complete = forum_scope.get("declared_complete") is True
    if not forum_scope:
        active_threads_complete = archived_threads_complete = True
    recorder.add(
        "inventory_declares_channel_and_thread_completion",
        declared_complete
        and active_threads_complete
        and archived_threads_complete
        and (not forum_scope or forum_declared_complete),
        "critical",
        {
            "inventory_complete": declared_complete,
            "active_threads_complete": payload.get("active_threads_complete"),
            "archived_threads_complete": payload.get("archived_threads_complete"),
            "nested_active_threads_complete": completeness.get(
                "active_forum_thread_enumeration_complete"
            ),
            "nested_archived_threads_complete": completeness.get(
                "discoverable_archived_forum_thread_enumeration_complete"
            ),
            "forum_declared_complete": forum_scope.get("declared_complete"),
            "known_limitations": payload.get("known_limitations") or [],
        },
        {
            "inventory_complete": True,
            "active_threads_complete": True,
            "archived_threads_complete": True,
        },
        dimension="coverage",
    )
    recorder.add(
        "inventory_post_cutoff_authenticated_navigation_complete",
        (not requires_all_parent_thread_contract) or post_cutoff_complete,
        "critical",
        {
            "required_for_38_top_level_inventory": requires_all_parent_thread_contract,
            "scope": post_cutoff_scope,
            "captured_at_utc": captured_at,
        },
        {
            "declared_complete": True,
            "authenticated": True,
            "navigation_pass_complete": True,
            "terminal_state_observed": True,
            "capture_completed_at_or_after_utc": expected_end,
            "source_refs": "nonempty",
        },
        dimension="coverage",
    )
    recorder.add(
        "inventory_all_parent_ordinary_thread_audit_complete",
        (not requires_all_parent_thread_contract) or ordinary_complete,
        "critical",
        {
            "required_for_38_top_level_inventory": requires_all_parent_thread_contract,
            "expected_parent_ids": sorted(top_level_ids),
            "scope": ordinary_scope,
        },
        {
            "declared_and_validated_complete": True,
            "audited_parent_count": 38,
            "audited_parent_ids": "exactly the 38 top-level IDs",
            "unresolved_observed_occurrence_count": 0,
        },
        dimension="coverage",
    )

    identities = [inventory_identity(row) for row in rows if inventory_identity(row)]
    duplicate_ids = sorted(identity for identity, count in collections.Counter(identities).items() if count > 1)
    recorder.add(
        "inventory_container_ids_unique",
        not duplicate_ids,
        "critical",
        len(duplicate_ids),
        0,
        dimension="uniqueness",
        examples=duplicate_ids,
    )
    invalid_counts: list[dict[str, Any]] = []
    for row in rows:
        if not inventory_required(row):
            continue
        status = str(row.get("count_status") or "").casefold()
        total = row.get("full_window_reported_total")
        if status and status not in {
            "ok",
            "complete",
            "verified_empty",
            "complete_parent_forum_enumeration",
        }:
            invalid_counts.append({"container_id": inventory_identity(row), "status": status, "error": row.get("count_error")})
        if total is not None:
            try:
                if int(total) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                invalid_counts.append({"container_id": inventory_identity(row), "reported_total": total})
    recorder.add(
        "inventory_reported_counts_valid",
        not invalid_counts,
        "high",
        len(invalid_counts),
        0,
        dimension="validity",
        examples=invalid_counts,
    )
    return {
        "status": (
            "complete"
            if declared_complete
            and active_threads_complete
            and archived_threads_complete
            and (not forum_scope or forum_declared_complete)
            else "partial"
        ),
        "rows": len(rows),
        "duplicate_ids": len(duplicate_ids),
        "invalid_reported_counts": len(invalid_counts),
    }


def coverage_validation(
    recorder: CheckRecorder,
    artifacts: list[SegmentArtifact],
    inventory_rows: list[dict[str, Any]],
    window_start: dt.date,
    window_end: dt.date,
    *,
    guild_wide_required: bool = True,
    policy_release_ready: bool | None = None,
) -> dict[str, Any]:
    expected_days = set(iter_days(window_start, window_end))
    complete_by_scope: dict[str, set[dt.date]] = collections.defaultdict(set)
    complete_segments_by_scope: dict[str, list[SegmentArtifact]] = collections.defaultdict(list)
    zero_complete = 0
    for artifact in artifacts:
        if artifact.complete:
            complete_by_scope[artifact.scope_key].update(iter_days(artifact.start, artifact.end))
            complete_segments_by_scope[artifact.scope_key].append(artifact)
            if int(artifact.payload.get("reported_total") or 0) == 0:
                zero_complete += 1

    guild_keys = [key for key in complete_by_scope if key.startswith("guild:")]
    guild_covered = set().union(*(complete_by_scope[key] for key in guild_keys)) if guild_keys else set()
    guild_missing = sorted(expected_days - guild_covered)
    recorder.add(
        "guild_wide_date_coverage"
        if guild_wide_required
        else "guild_wide_date_coverage_conditional_diagnostic",
        not guild_missing if guild_wide_required else True,
        "critical" if guild_wide_required else "low",
        {
            "covered_days": len(expected_days & guild_covered),
            "missing_days": len(guild_missing),
            "complete_segment_count": sum(len(complete_segments_by_scope[key]) for key in guild_keys),
        },
        {"covered_days": len(expected_days), "missing_days": 0},
        dimension="coverage",
        examples=[day.isoformat() for day in guild_missing[:20]],
        note=(
            "Channel-complete inventory coverage may also satisfy release, but guild-wide coverage is independently reported."
            if guild_wide_required
            else "The relevance plan makes stable guild-wide search conditional; missing guild-wide dates are diagnostic and do not replace or block policy-scoped capture."
        ),
    )

    recorder.add(
        "channel_thread_inventory_present",
        bool(inventory_rows),
        "critical",
        len(inventory_rows),
        "> 0 exact inventory rows",
        dimension="coverage",
    )
    invalid_inventory_ids = [
        {"name": inventory_name(row), "id": inventory_identity(row)}
        for row in inventory_rows
        if inventory_required(row) and not DISCORD_ID_RE.fullmatch(inventory_identity(row))
    ]
    recorder.add(
        "inventory_exact_ids",
        bool(inventory_rows) and not invalid_inventory_ids,
        "critical",
        len(invalid_inventory_ids),
        0,
        dimension="coverage",
        examples=invalid_inventory_ids,
    )

    artifacts_by_id: dict[str, list[SegmentArtifact]] = collections.defaultdict(list)
    artifacts_by_name: dict[str, list[SegmentArtifact]] = collections.defaultdict(list)
    for artifact in artifacts:
        if artifact.channel_id:
            artifacts_by_id[artifact.channel_id].append(artifact)
        if artifact.channel_name:
            artifacts_by_name[normalize_text(artifact.channel_name)].append(artifact)

    missing_units: list[dict[str, Any]] = []
    inaccessible_count = 0
    verified_empty_count = 0
    required_count = 0
    for row in inventory_rows:
        if not inventory_required(row):
            inaccessible_count += 1
            continue
        required_count += 1
        identity = inventory_identity(row)
        coverage_identity = str(
            row.get("coverage_container_id")
            or row.get("covered_by_container_id")
            or row.get("search_container_id")
            or row.get("parent_channel_id")
            or identity
        ).strip()
        name = inventory_name(row)
        status = str(row.get("coverage_status") or row.get("status") or "").casefold()
        if status in {"verified_empty", "empty"}:
            verified_empty_count += 1
            continue
        candidates = artifacts_by_id.get(coverage_identity, [])
        if not candidates and name:
            # Name matching is permitted for diagnosis but cannot establish exact
            # identity unless the name is unique in the inventory.
            name_key = normalize_text(name)
            same_name = [item for item in inventory_rows if normalize_text(inventory_name(item)) == name_key]
            if len(same_name) == 1:
                candidates = artifacts_by_name.get(name_key, [])
        covered: set[dt.date] = set()
        for artifact in candidates:
            if artifact.complete:
                covered.update(iter_days(artifact.start, artifact.end))
        missing = sorted(expected_days - covered)
        if missing:
            missing_units.append(
                {
                    "container_id": identity,
                    "coverage_container_id": coverage_identity,
                    "name": name,
                    "covered_days": len(expected_days & covered),
                    "missing_days": len(missing),
                    "first_missing": missing[0].isoformat() if missing else None,
                }
            )

    channel_complete = bool(inventory_rows) and not invalid_inventory_ids and not missing_units
    recorder.add(
        "inventory_unit_date_coverage",
        channel_complete,
        "critical",
        {
            "required_units": required_count,
            "complete_or_verified_empty": required_count - len(missing_units),
            "missing_units": len(missing_units),
            "verified_empty_units": verified_empty_count,
            "inaccessible_or_non_message_units": inaccessible_count,
        },
        "Every accessible message container covers all 201 local dates or is verified empty.",
        dimension="coverage",
        examples=missing_units,
    )

    whole_server_passed = channel_complete and (
        policy_release_ready is not False
    )
    recorder.add(
        "whole_server_coverage_gate",
        whole_server_passed,
        "critical",
        {
            "inventory_complete": channel_complete,
            "guild_wide_days_complete": not guild_missing,
            "policy_release_ready": policy_release_ready,
        },
        {"inventory_complete": True, "policy_release_ready": True},
        dimension="coverage",
        note="Guild-wide search is a useful reconciliation path but does not replace exact per-container inventory coverage.",
    )
    return {
        "expected_local_dates": len(expected_days),
        "guild_wide_covered_dates": len(expected_days & guild_covered),
        "guild_wide_missing_dates": [day.isoformat() for day in guild_missing],
        "inventory_rows": len(inventory_rows),
        "required_inventory_units": required_count,
        "missing_inventory_units": missing_units,
        "valid_zero_result_complete_segments": zero_complete,
        "policy_release_ready": policy_release_ready,
    }


def canonical_occurrences(artifacts: list[SegmentArtifact], complete_only: bool) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for artifact in artifacts:
        if complete_only and not artifact.complete:
            continue
        for message in artifact.messages:
            output.append(
                {
                    "message": message,
                    "path": str(artifact.path),
                    "sha256": artifact.source_record["sha256"],
                    "captured_at_utc": artifact.captured_at_utc,
                    "complete_source": artifact.complete,
                }
            )
    return output


def validate_duplicates_and_edits(recorder: CheckRecorder, occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for occurrence in occurrences:
        grouped[str(occurrence["message"].get("message_id") or "")].append(occurrence)
    duplicate_groups = {message_id: rows for message_id, rows in grouped.items() if len(rows) > 1}
    content_conflicts: list[dict[str, Any]] = []
    timestamp_conflicts: list[dict[str, Any]] = []
    author_conflicts: list[dict[str, Any]] = []
    ordered_edit_variants = 0
    unresolved_edit_variants = 0
    edited_without_version_time: list[dict[str, Any]] = []
    for message_id, rows in grouped.items():
        contents = {str(row["message"].get("content_text") or "") for row in rows}
        timestamps = {str(row["message"].get("timestamp_utc") or "") for row in rows}
        authors = {str(row["message"].get("author") or "") for row in rows if row["message"].get("author")}
        if len(timestamps) > 1:
            timestamp_conflicts.append({"message_id": message_id, "values": sorted(timestamps)[:5]})
        if len(authors) > 1:
            author_conflicts.append({"message_id": message_id, "values": sorted(authors)[:5]})
        if len(contents) > 1:
            capture_times = {row.get("captured_at_utc") for row in rows if row.get("captured_at_utc")}
            edited = any(row["message"].get("edited") for row in rows)
            if edited and len(capture_times) == len(rows):
                ordered_edit_variants += 1
            else:
                unresolved_edit_variants += 1
                content_conflicts.append(
                    {
                        "message_id": message_id,
                        "variant_count": len(contents),
                        "edited_flag_seen": edited,
                        "capture_times_present": len(capture_times),
                        "occurrences": len(rows),
                    }
                )
    for message_id, rows in grouped.items():
        if any(row["message"].get("edited") for row in rows):
            has_edited_at = any(row["message"].get("edited_at_utc") for row in rows)
            has_capture_time = all(row.get("captured_at_utc") for row in rows)
            if not has_edited_at and not has_capture_time:
                edited_without_version_time.append({"message_id": message_id, "occurrences": len(rows)})

    recorder.add(
        "duplicate_occurrences_preserved",
        True,
        "info",
        {
            "occurrences": len(occurrences),
            "unique_message_ids": len(grouped),
            "duplicated_message_ids": len(duplicate_groups),
            "duplicate_occurrences": len(occurrences) - len(grouped),
        },
        "All occurrences remain available to the validator.",
        dimension="uniqueness",
    )
    recorder.add(
        "timestamp_variants_resolved",
        not timestamp_conflicts,
        "critical",
        len(timestamp_conflicts),
        0,
        dimension="consistency",
        examples=timestamp_conflicts,
    )
    recorder.add(
        "content_edit_variants_resolved",
        not content_conflicts,
        "critical",
        {
            "ordered_edit_variants": ordered_edit_variants,
            "unresolved_content_variants": unresolved_edit_variants,
        },
        {"unresolved_content_variants": 0},
        dimension="consistency",
        examples=content_conflicts,
        note="A differing message body is an edit only when capture chronology or edited_at establishes version order.",
    )
    recorder.add(
        "author_variants_reviewed",
        not author_conflicts,
        "high",
        len(author_conflicts),
        0,
        dimension="consistency",
        examples=author_conflicts,
    )
    recorder.add(
        "edited_messages_have_version_timing",
        not edited_without_version_time,
        "high",
        len(edited_without_version_time),
        0,
        dimension="timeliness",
        examples=edited_without_version_time,
    )
    return {
        "message_occurrences": len(occurrences),
        "unique_message_ids": len(grouped),
        "duplicated_message_ids": len(duplicate_groups),
        "timestamp_conflicts": len(timestamp_conflicts),
        "unresolved_content_variants": unresolved_edit_variants,
    }


def validate_replies(recorder: CheckRecorder, occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    by_id: dict[str, dict[str, Any]] = {}
    for occurrence in occurrences:
        message = occurrence["message"]
        by_id.setdefault(str(message.get("message_id") or ""), message)
    reply_context_without_id: list[dict[str, Any]] = []
    documented_context_without_id: list[dict[str, Any]] = []
    undocumented_context_without_id: list[dict[str, Any]] = []
    unresolved_targets: list[dict[str, Any]] = []
    invalid_order: list[dict[str, Any]] = []
    self_replies: list[str] = []
    graph: dict[str, str] = {}
    exact_scope_failures: list[dict[str, Any]] = []
    resolution_contract_failures: list[dict[str, Any]] = []
    reply_count = 0
    for message_id, message in by_id.items():
        target = str(message.get("reply_to_message_id") or "").strip()
        has_context = bool(str(message.get("reply_context") or "").strip()) or message.get("reply_context_present") is True
        if has_context and not target:
            expected_status = (
                reply_provenance_contract.classify_documented_no_id_status(
                    message
                )
            )
            contract_errors = (
                reply_provenance_contract.documented_no_id_contract_errors(
                    message
                )
                if expected_status
                else reply_provenance_contract.resolution_status_boolean_errors(
                    message
                )
            )
            if contract_errors:
                resolution_contract_failures.append(
                    {
                        "message_id": message_id,
                        "declared_status": message.get(
                            "reply_target_resolution_status"
                        ),
                        "documented": message.get(
                            "reply_target_unavailability_documented"
                        ),
                        "expected_status": expected_status,
                        "reasons": contract_errors,
                    }
                )
            status = documented_reply_target_unavailability(message)
            item = {"message_id": message_id, "status": status}
            reply_context_without_id.append(item)
            if status:
                documented_context_without_id.append(item)
            else:
                undocumented_context_without_id.append(item)
        if not target:
            if not has_context:
                contract_errors = (
                    reply_provenance_contract.resolution_status_boolean_errors(
                        message
                    )
                )
                if contract_errors:
                    resolution_contract_failures.append(
                        {
                            "message_id": message_id,
                            "declared_status": message.get(
                                "reply_target_resolution_status"
                            ),
                            "documented": message.get(
                                "reply_target_unavailability_documented"
                            ),
                            "expected_status": "not_applicable",
                            "reasons": contract_errors,
                        }
                    )
            continue
        reply_count += 1
        source = str(message.get("reply_to_message_id_source") or "")
        exact_reasons = (
            reply_provenance_contract.exact_reply_target_contract_errors(
                message, guild_id=DEFAULT_GUILD_ID
            )
        )
        exact_status_reasons = [
            reason
            for reason in exact_reasons
            if reason
            in {
                "reply_target_resolution_status_not_exact_target_id",
                "resolved_target_claims_unavailability_documented",
            }
        ]
        if exact_status_reasons:
            resolution_contract_failures.append(
                {
                    "message_id": message_id,
                    "declared_status": message.get(
                        "reply_target_resolution_status"
                    ),
                    "documented": message.get(
                        "reply_target_unavailability_documented"
                    ),
                    "expected_status": "exact_target_id",
                    "reasons": exact_status_reasons,
                }
            )
        if exact_reasons:
            exact_scope_failures.append(
                {
                    "message_id": message_id,
                    "reply_to_message_id": target,
                    "source": source or None,
                    "reasons": exact_reasons,
                }
            )
        graph[message_id] = target
        if target == message_id:
            self_replies.append(message_id)
        try:
            if snowflake_time(target) > snowflake_time(message_id):
                invalid_order.append({"message_id": message_id, "reply_to_message_id": target})
        except ValueError:
            invalid_order.append({"message_id": message_id, "reply_to_message_id": target, "reason": "invalid ID"})
        if target not in by_id:
            state = str(message.get("reply_target_state") or "").casefold()
            if state not in ALLOWED_REPLY_STATES - {"resolved", "not_applicable"}:
                unresolved_targets.append({"message_id": message_id, "reply_to_message_id": target, "state": state or None})

    cycles: list[list[str]] = []
    visited: set[str] = set()
    for start in graph:
        if start in visited:
            continue
        chain: list[str] = []
        positions: dict[str, int] = {}
        cursor = start
        while cursor in graph and cursor not in visited:
            if cursor in positions:
                cycles.append(chain[positions[cursor] :] + [cursor])
                break
            positions[cursor] = len(chain)
            chain.append(cursor)
            cursor = graph[cursor]
        visited.update(chain)

    recorder.add(
        "reply_context_has_explicit_target",
        not undocumented_context_without_id,
        "high",
        len(undocumented_context_without_id),
        0,
        dimension="integrity",
        examples=undocumented_context_without_id,
        note=(
            "Adjacent or visible reply text must not be treated as an answered Q&A without an explicit target ID. "
            "Discord's exact unloaded, attachment, sticker, voice-message, and Dyno no-ID states remain "
            "documented, unresolved context and are never linked as answered Q&A."
        ),
    )
    recorder.add(
        "reply_target_unavailability_documented",
        not undocumented_context_without_id,
        "high",
        {
            "documented_without_target": len(documented_context_without_id),
            "undocumented_without_target": len(undocumented_context_without_id),
        },
        {"undocumented_without_target": 0},
        dimension="integrity",
        examples=undocumented_context_without_id,
    )
    recorder.add(
        "reply_resolution_status_boolean_consistent",
        not resolution_contract_failures,
        "critical",
        len(resolution_contract_failures),
        0,
        dimension="provenance",
        examples=resolution_contract_failures,
        note=(
            "A documented no-ID status is accepted only when the exact Discord "
            "widget evidence matches the enumerated status and the documentation "
            "boolean is true. Unknown contexts remain unresolved with false."
        ),
    )
    recorder.add(
        "reply_targets_resolved_or_documented",
        not unresolved_targets,
        "critical",
        len(unresolved_targets),
        0,
        dimension="integrity",
        examples=unresolved_targets,
    )
    recorder.add(
        "reply_targets_have_owned_exact_scope",
        not exact_scope_failures,
        "critical",
        len(exact_scope_failures),
        0,
        dimension="provenance",
        examples=exact_scope_failures,
        note=(
            "Accepted targets come from allowlisted row-owned reply-context content, "
            "ARIA, data-list/data-message, or permalink evidence with exact scope and "
            "an exact Discord target permalink. "
            "Preview-only links or text are context, never resolved reply evidence."
        ),
    )
    recorder.add(
        "reply_temporal_order",
        not invalid_order and not self_replies and not cycles,
        "critical",
        {"invalid_order": len(invalid_order), "self_replies": len(self_replies), "cycles": len(cycles)},
        {"invalid_order": 0, "self_replies": 0, "cycles": 0},
        dimension="integrity",
        examples=limited(invalid_order + [{"self_reply": item} for item in self_replies] + [{"cycle": item} for item in cycles]),
    )
    return {
        "messages_with_reply_id": reply_count,
        "reply_context_without_id": len(reply_context_without_id),
        "documented_reply_context_without_id": len(documented_context_without_id),
        "undocumented_reply_context_without_id": len(undocumented_context_without_id),
        "unresolved_reply_targets": len(unresolved_targets),
        "reply_target_scope_failures": len(exact_scope_failures),
        "reply_resolution_contract_failures": len(resolution_contract_failures),
    }


def validate_attachments(
    recorder: CheckRecorder,
    occurrences: list[dict[str, Any]],
    archived_attachment_ids: set[str] | None = None,
) -> tuple[dict[str, Any], set[str]]:
    archived_attachment_ids = archived_attachment_ids or set()
    attachment_owners: dict[str, set[str]] = collections.defaultdict(set)
    invalid_ids: list[dict[str, Any]] = []
    invalid_urls: list[dict[str, Any]] = []
    invalid_ownership_evidence: list[dict[str, Any]] = []
    suspicious_timing: list[dict[str, Any]] = []
    occurrences_count = 0
    non_owned_count = 0
    status_missing = 0
    for occurrence in occurrences:
        message = occurrence["message"]
        message_id = str(message.get("message_id") or "")
        for attachment in message.get("attachments") or []:
            if not isinstance(attachment, dict):
                invalid_ids.append({"message_id": message_id, "reason": "attachment is not an object"})
                continue
            relation = str(attachment.get("relation_type") or attachment.get("ownership") or "").casefold()
            ownership_status = str(attachment.get("ownership_status") or "").casefold()
            ownership_evidence = attachment.get("ownership_evidence")
            ownership_exact = (
                isinstance(ownership_evidence, dict)
                and ownership_evidence.get("schema_version") == "1.0.0"
                and ownership_evidence.get("exact") is True
                and str(ownership_evidence.get("owner_message_id") or "") == message_id
            )
            is_owned = relation == "owned" and ownership_status == "owned_exact" and ownership_exact
            is_non_owned = (
                relation in {"embedded_external", "copied_media", "non_owned"}
                and ownership_status == "non_owned_exact"
                and ownership_exact
            )
            attachment_id = str(attachment.get("attachment_id") or "").strip()
            occurrences_count += 1
            if not DISCORD_ID_RE.fullmatch(attachment_id):
                invalid_ids.append({"message_id": message_id, "attachment_id": attachment_id})
                continue
            if is_owned:
                attachment_owners[attachment_id].add(message_id)
            elif is_non_owned:
                non_owned_count += 1
            else:
                invalid_ownership_evidence.append(
                    {
                        "message_id": message_id,
                        "attachment_id": attachment_id,
                        "relation_type": relation or None,
                        "ownership_status": ownership_status or None,
                    }
                )
            url = str(attachment.get("url") or "").strip()
            if not url or urlparse(url).hostname not in DISCORD_SOURCE_HOSTS:
                invalid_urls.append({"message_id": message_id, "attachment_id": attachment_id, "url": url})
            if is_owned and (
                attachment_id not in archived_attachment_ids
                and not any(
                    key in attachment
                    for key in ("capture_status", "download_status", "extraction_status")
                )
            ):
                status_missing += 1
            if is_owned:
                try:
                    delta = abs((snowflake_time(message_id) - snowflake_time(attachment_id)).total_seconds())
                    if delta > 300:
                        suspicious_timing.append(
                            {"message_id": message_id, "attachment_id": attachment_id, "delta_seconds": round(delta, 3)}
                        )
                except ValueError:
                    pass
    multiple_owners = [
        {"attachment_id": attachment_id, "message_ids": sorted(owners)}
        for attachment_id, owners in attachment_owners.items()
        if len(owners) > 1
    ]
    recorder.add(
        "attachment_ids_and_urls_valid",
        not invalid_ids and not invalid_urls,
        "critical",
        {"invalid_ids": len(invalid_ids), "invalid_urls": len(invalid_urls)},
        {"invalid_ids": 0, "invalid_urls": 0},
        dimension="validity",
        examples=limited(invalid_ids + invalid_urls),
    )
    recorder.add(
        "attachment_has_single_owner",
        not multiple_owners,
        "critical",
        len(multiple_owners),
        0,
        dimension="integrity",
        examples=multiple_owners,
        note="Reply-preview attachment references must be stored separately from message-owned attachments.",
    )
    recorder.add(
        "attachment_ownership_evidence_exact",
        not invalid_ownership_evidence,
        "critical",
        len(invalid_ownership_evidence),
        0,
        dimension="provenance",
        examples=invalid_ownership_evidence,
        note="Every Discord CDN row must be explicitly classified as owned-exact or non-owned-exact.",
    )
    recorder.add(
        "attachment_ownership_timing",
        not suspicious_timing,
        "high",
        len(suspicious_timing),
        0,
        dimension="consistency",
        examples=suspicious_timing,
    )
    recorder.add(
        "attachment_capture_status_present",
        status_missing == 0,
        "high",
        status_missing,
        0,
        dimension="completeness",
    )
    return (
        {
            "attachment_occurrences": occurrences_count,
            "unique_owned_attachments": len(attachment_owners),
            "multiple_owner_attachments": len(multiple_owners),
            "non_owned_attachment_occurrences": non_owned_count,
            "invalid_ownership_evidence": len(invalid_ownership_evidence),
            "missing_capture_or_extraction_status": status_missing,
        },
        set(attachment_owners),
    )


def validate_attachment_archive(
    recorder: CheckRecorder,
    manifest_path: Path | None,
    archive_root: Path | None,
    expected_attachment_ids: set[str],
    auxiliary_source_records: list[dict[str, Any]],
) -> tuple[dict[str, Any], set[str]]:
    if manifest_path is None:
        passed = not expected_attachment_ids
        summary = {
            "provided": False,
            "status": "not_required" if passed else "missing",
            "expected_attachment_count": len(expected_attachment_ids),
            "manifest_attachment_count": 0,
            "entry_set_parity": passed,
            "terminal_coverage_complete": passed,
            "literal_release_complete": passed,
            "byte_complete": passed,
            "errors": [] if passed else ["attachment_manifest_missing"],
        }
        recorder.add(
            "attachment_archive_terminal_coverage",
            passed,
            "critical",
            summary,
            {"terminal_coverage_complete": True, "entry_set_parity": True},
            dimension="completeness",
            note=(
                "Every discovered Discord-owned attachment requires downloaded bytes with "
                "SHA-256 when available, or a substantiated terminal unavailable record. "
                "Terminal failed records remain degraded and block literal release."
            ),
        )
        return summary, set()

    resolved_manifest = manifest_path.resolve()
    summary: dict[str, Any]
    manifest_ids: set[str] = set()
    try:
        manifest, source_record = stable_read_json(resolved_manifest)
        source_record["kind"] = "discord_attachment_archive_manifest"
        auxiliary_source_records.append(source_record)
        discord_attachment_archiver.validate_manifest_structure(
            manifest, require_terminal=True
        )
        manifest_ids = {
            str(row.get("attachment_id") or "") for row in manifest["entries"]
        }
        missing = sorted(expected_attachment_ids - manifest_ids)
        extra = sorted(manifest_ids - expected_attachment_ids)
        downloaded = [
            row for row in manifest["entries"] if row.get("capture_status") == "downloaded"
        ]
        if downloaded and archive_root is None:
            raise ValidationError(
                "--attachment-archive-root is required for downloaded attachment verification"
            )
        verification = discord_attachment_archiver.verify_archive(
            manifest,
            (archive_root or resolved_manifest.parent).resolve(),
            require_terminal=True,
        )
        policy = manifest.get("policy") if isinstance(manifest.get("policy"), dict) else {}
        parity = not missing and not extra
        gate = manifest.get("release_gate") or {}
        terminal = bool(
            gate.get("terminal_coverage_complete") is True
            and verification.get("terminal_coverage_complete") is True
        )
        literal_release_complete = bool(
            manifest.get("status") == "complete"
            and gate.get("passed") is True
            and gate.get("literal_release_complete") is True
            and verification.get("literal_release_complete") is True
            and verification.get("status") == "passed"
        )
        boundary = bool(
            manifest.get("source_scope") == "discord_only"
            and manifest.get("outside_sources_used") in {0, False}
            and policy.get("external_links_fetched") is False
            and policy.get("credentials_or_browser_storage_inspected") is False
        )
        summary = {
            "provided": True,
            "path": str(resolved_manifest),
            "sha256": source_record["sha256"],
            "status": manifest.get("status"),
            "expected_attachment_count": len(expected_attachment_ids),
            "manifest_attachment_count": len(manifest_ids),
            "entry_set_parity": parity,
            "missing_attachment_ids": missing,
            "extra_attachment_ids": extra,
            "terminal_coverage_complete": terminal,
            "literal_release_complete": literal_release_complete,
            "byte_complete": bool(gate.get("byte_complete")),
            "discord_only_boundary": boundary,
            "counts": manifest.get("counts"),
            "verification": verification,
            "errors": [],
        }
    except (
        OSError,
        ValidationError,
        discord_attachment_archiver.AttachmentArchiveError,
    ) as exc:
        summary = {
            "provided": True,
            "path": str(resolved_manifest),
            "status": "invalid",
            "expected_attachment_count": len(expected_attachment_ids),
            "manifest_attachment_count": len(manifest_ids),
            "entry_set_parity": False,
            "terminal_coverage_complete": False,
            "literal_release_complete": False,
            "byte_complete": False,
            "discord_only_boundary": False,
            "errors": [str(exc)],
        }
    recorder.add(
        "attachment_archive_terminal_coverage",
        summary.get("terminal_coverage_complete") is True
        and summary.get("entry_set_parity") is True,
        "critical",
        summary,
        {"terminal_coverage_complete": True, "entry_set_parity": True},
        dimension="completeness",
        examples=limited(
            [{"missing_attachment_id": value} for value in summary.get("missing_attachment_ids", [])]
            + [{"extra_attachment_id": value} for value in summary.get("extra_attachment_ids", [])]
            + [{"error": value} for value in summary.get("errors", [])]
        ),
    )
    recorder.add(
        "attachment_archive_literal_release_complete",
        summary.get("literal_release_complete") is True
        and summary.get("entry_set_parity") is True,
        "critical",
        summary,
        {"literal_release_complete": True, "entry_set_parity": True},
        dimension="completeness",
        note=(
            "A terminal failed attachment is retained for audit but cannot satisfy the "
            "literal-release or final-package gate."
        ),
        examples=limited(
            [{"error": value} for value in summary.get("errors", [])]
        ),
    )
    recorder.add(
        "attachment_archive_discord_only_boundary",
        summary.get("discord_only_boundary") is True,
        "critical",
        summary.get("discord_only_boundary"),
        True,
        dimension="provenance",
    )
    recorder.add(
        "attachment_archive_byte_integrity",
        (summary.get("verification") or {}).get("status") == "passed",
        "critical",
        summary.get("verification"),
        {"status": "passed", "problem_count": 0},
        dimension="integrity",
        note=(
            "Every downloaded attachment and every complete/partial extraction artifact must "
            "match its local byte size and SHA-256."
        ),
    )
    return summary, manifest_ids


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sqlite_tables(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        str(name): str(sql or "")
        for name, sql in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")
    }


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({quote_identifier(table)})")}


def excerpt_matches(excerpt: str, *source_values: Any) -> bool:
    source = "\n".join(str(value or "") for value in source_values)
    if excerpt in source:
        return True
    normalized_excerpt = normalize_text(excerpt)
    normalized_source = normalize_text(source)
    if normalized_excerpt and normalized_excerpt in normalized_source:
        return True
    if normalized_excerpt.endswith(("…", "...")):
        prefix = normalized_excerpt.rstrip(".… ")
        return bool(prefix) and prefix in normalized_source
    return False


def classify_database_inventory_extensions(
    connection: sqlite3.Connection,
    extra_container_ids: set[str],
    expected_container_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate proven observed forum threads from unexplained DB extras.

    The frozen external inventory is the authoritative top-level set.  The
    merger may extend it with exact forum-thread IDs observed in authenticated
    parent-forum searches, but those extensions must retain both a forum parent
    from the frozen set and a trusted, non-migration channel-segment occurrence.
    """

    allowed: list[dict[str, Any]] = []
    unexplained: list[dict[str, Any]] = []
    inventory_columns = table_columns(connection, "channel_inventory")
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    message_columns = table_columns(connection, "messages") if "messages" in tables else set()
    occurrence_columns = (
        table_columns(connection, "message_source_occurrences")
        if "message_source_occurrences" in tables
        else set()
    )
    can_trace_occurrence = (
        {"message_id", "channel_id"} <= message_columns
        and {
            "message_id",
            "occurrence_id",
            "source_kind",
            "migration_source",
            "quarantined",
            "trusted_canonical",
        }
        <= occurrence_columns
    )

    projection = ["channel_id"]
    for optional in (
        "parent_channel_id",
        "kind",
        "exact_id_known",
        "inventory_basis",
        "source_json",
    ):
        if optional in inventory_columns:
            projection.append(optional)
    sql = (
        "SELECT "
        + ",".join(quote_identifier(column) for column in projection)
        + " FROM channel_inventory WHERE channel_id=?"
    )
    for channel_id in sorted(extra_container_ids):
        row = connection.execute(sql, (channel_id,)).fetchone()
        values = dict(zip(projection, row or ()))
        reasons: list[str] = []
        parent_id = str(values.get("parent_channel_id") or "").strip()
        kind = str(values.get("kind") or "").casefold()
        if not DISCORD_ID_RE.fullmatch(channel_id):
            reasons.append("extra_container_id_is_not_an_exact_discord_snowflake")
        if "exact_id_known" not in inventory_columns or values.get("exact_id_known") != 1:
            reasons.append("exact_id_known_is_not_1")
        if "forum" not in kind or "thread" not in kind:
            reasons.append("kind_is_not_forum_thread")
        if parent_id not in expected_container_ids:
            reasons.append("parent_is_not_in_frozen_external_inventory")
        parent_kind = ""
        if parent_id:
            parent_row = connection.execute(
                "SELECT kind FROM channel_inventory WHERE channel_id=?", (parent_id,)
            ).fetchone()
            parent_kind = str(parent_row[0] or "").casefold() if parent_row else ""
        if "forum" not in parent_kind or "thread" in parent_kind:
            reasons.append("parent_is_not_a_top_level_forum_container")

        trusted_occurrence_count = 0
        if can_trace_occurrence:
            trusted_occurrence_count = int(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT o.occurrence_id)
                    FROM messages m
                    JOIN message_source_occurrences o ON o.message_id=m.message_id
                    WHERE m.channel_id=?
                      AND o.source_kind='channel_segment'
                      AND o.migration_source=0
                      AND o.quarantined=0
                      AND o.trusted_canonical=1
                    """,
                    (channel_id,),
                ).fetchone()[0]
            )
        if trusted_occurrence_count == 0:
            reasons.append("no_trusted_nonmigration_channel_segment_occurrence")

        detail = {
            "container_id": channel_id,
            "parent_container_id": parent_id or None,
            "kind": values.get("kind"),
            "inventory_basis": values.get("inventory_basis"),
            "trusted_occurrence_count": trusted_occurrence_count,
        }
        if reasons:
            detail["reasons"] = reasons
            unexplained.append(detail)
        else:
            detail["classification"] = "provenance_backed_observed_forum_thread"
            allowed.append(detail)
    return allowed, unexplained


def validate_sqlite(
    recorder: CheckRecorder,
    database: Path | None,
    expected_message_ids: set[str],
    expected_attachment_ids: set[str],
    expected_container_ids: set[str],
    window_start: dt.date,
    window_end: dt.date,
) -> dict[str, Any]:
    if database is None:
        recorder.add(
            "sqlite_database_supplied",
            False,
            "critical",
            None,
            "path to the release SQLite database",
            dimension="integrity",
        )
        return {"status": "not_run"}
    database = database.resolve()
    if not database.is_file():
        recorder.add(
            "sqlite_database_supplied",
            False,
            "critical",
            str(database),
            "existing SQLite database",
            dimension="integrity",
        )
        return {"status": "not_found", "path": str(database)}

    sidecars = [Path(str(database) + suffix) for suffix in ("-wal", "-shm") if Path(str(database) + suffix).exists()]
    recorder.add(
        "sqlite_release_has_no_sidecars",
        not sidecars,
        "high",
        [str(path) for path in sidecars],
        [],
        dimension="integrity",
    )
    uri = "file:" + database.as_posix() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    summary: dict[str, Any] = {"status": "inspected", "path": str(database), "sha256": preservation_hashes.sha256_file(database)}
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        recorder.add("sqlite_integrity", integrity == "ok", "critical", integrity, "ok", dimension="integrity")
        recorder.add(
            "sqlite_foreign_keys",
            not foreign_keys,
            "critical",
            len(foreign_keys),
            0,
            dimension="integrity",
            examples=[list(row) for row in foreign_keys[:20]],
        )
        tables = sqlite_tables(connection)
        summary["table_count"] = len(tables)
        message_table = "messages_all" if "messages_all" in tables else "messages" if "messages" in tables else None
        recorder.add(
            "all_message_table_present",
            message_table is not None,
            "critical",
            message_table,
            "messages_all or messages",
            dimension="shape",
        )
        if not message_table:
            return summary
        columns = table_columns(connection, message_table)
        if "message_id" not in columns:
            recorder.add(
                "all_message_table_has_message_id",
                False,
                "critical",
                sorted(columns),
                "message_id column",
                dimension="shape",
            )
            return summary
        quoted_messages = quote_identifier(message_table)
        stored_ids = {str(row[0]) for row in connection.execute(f"SELECT message_id FROM {quoted_messages}")}
        duplicate_ids = connection.execute(
            f"SELECT COUNT(*) FROM (SELECT message_id FROM {quoted_messages} GROUP BY message_id HAVING COUNT(*)>1)"
        ).fetchone()[0]
        summary["message_table"] = message_table
        summary["message_rows"] = len(stored_ids)
        recorder.add(
            "sqlite_unique_message_ids",
            duplicate_ids == 0,
            "critical",
            duplicate_ids,
            0,
            dimension="uniqueness",
        )
        missing = sorted(expected_message_ids - stored_ids)
        extra = sorted(stored_ids - expected_message_ids)
        recorder.add(
            "all_message_source_parity",
            not missing and not extra,
            "critical",
            {
                "database_expected_source_unique_messages": len(expected_message_ids),
                "database_unique_messages": len(stored_ids),
                "missing": len(missing),
                "extra": len(extra),
            },
            {"missing": 0, "extra": 0},
            dimension="integrity",
            examples=limited([{"missing_message_id": item} for item in missing] + [{"extra_message_id": item} for item in extra]),
        )

        db_timestamp_mismatches: list[dict[str, Any]] = []
        timestamp_column = "created_at_utc" if "created_at_utc" in columns else "timestamp_utc" if "timestamp_utc" in columns else None
        if timestamp_column:
            for message_id, timestamp_text in connection.execute(
                f"SELECT message_id,{quote_identifier(timestamp_column)} FROM {quoted_messages}"
            ):
                try:
                    timestamp = parse_iso_utc(timestamp_text)
                    encoded = snowflake_time(message_id)
                    local_day = utc_to_central(timestamp).date()
                    if abs((timestamp - encoded).total_seconds()) > 1 or not window_start <= local_day <= window_end:
                        db_timestamp_mismatches.append(
                            {
                                "message_id": str(message_id),
                                "timestamp_utc": timestamp_text,
                                "snowflake_timestamp_utc": encoded.isoformat().replace("+00:00", "Z"),
                                "central_date": local_day.isoformat(),
                            }
                        )
                except (TypeError, ValueError):
                    db_timestamp_mismatches.append({"message_id": str(message_id), "timestamp_utc": timestamp_text})
        recorder.add(
            "sqlite_message_timestamp_integrity",
            timestamp_column is not None and not db_timestamp_mismatches,
            "critical",
            {"timestamp_column": timestamp_column, "mismatches": len(db_timestamp_mismatches)},
            {"timestamp_column": "present", "mismatches": 0},
            dimension="consistency",
            examples=db_timestamp_mismatches,
        )

        fts_tables = [name for name, sql in tables.items() if "using fts5" in sql.casefold()]
        message_fts_tables: list[str] = []
        message_fts_failures: list[dict[str, Any]] = []
        auxiliary_fts_checks: list[dict[str, Any]] = []
        auxiliary_fts_failures: list[dict[str, Any]] = []
        for table in fts_tables:
            fts_columns = table_columns(connection, table)
            count = int(connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(table)}").fetchone()[0])
            if "message_id" in fts_columns:
                message_fts_tables.append(table)
                fts_ids = {
                    str(row[0])
                    for row in connection.execute(
                        f"SELECT message_id FROM {quote_identifier(table)}"
                    )
                }
                if fts_ids != stored_ids or count != len(stored_ids):
                    message_fts_failures.append(
                        {
                            "table": table,
                            "rows": count,
                            "missing": len(stored_ids - fts_ids),
                            "extra": len(fts_ids - stored_ids),
                            "duplicate_rows": max(0, count - len(fts_ids)),
                        }
                    )
                continue
            source_table: str | None = None
            identity_column: str | None = None
            if "extraction_id" in fts_columns and "attachment_extractions" in tables:
                source_table, identity_column = "attachment_extractions", "extraction_id"
            elif "claim_id" in fts_columns and "claims" in tables:
                source_table, identity_column = "claims", "claim_id"
            if source_table and identity_column:
                source_ids = {
                    str(row[0])
                    for row in connection.execute(
                        f"SELECT {quote_identifier(identity_column)} FROM {quote_identifier(source_table)}"
                    )
                }
                fts_ids = {
                    str(row[0])
                    for row in connection.execute(
                        f"SELECT {quote_identifier(identity_column)} FROM {quote_identifier(table)}"
                    )
                }
                detail = {
                    "table": table,
                    "source_table": source_table,
                    "identity_column": identity_column,
                    "rows": count,
                    "source_rows": len(source_ids),
                    "missing": len(source_ids - fts_ids),
                    "extra": len(fts_ids - source_ids),
                    "duplicate_rows": max(0, count - len(fts_ids)),
                }
                auxiliary_fts_checks.append(detail)
                if fts_ids != source_ids or count != len(source_ids):
                    auxiliary_fts_failures.append(detail)
            else:
                auxiliary_fts_failures.append(
                    {
                        "table": table,
                        "rows": count,
                        "reason": "No recognized source identity column for independent parity.",
                    }
                )
        recorder.add(
            "fts_all_message_parity",
            bool(message_fts_tables) and not message_fts_failures,
            "critical",
            {"fts_tables": message_fts_tables, "failures": message_fts_failures},
            "At least one all-message FTS table with exact message-ID parity.",
            dimension="integrity",
        )
        recorder.add(
            "auxiliary_fts_source_parity",
            not auxiliary_fts_failures,
            "high",
            {"checks": auxiliary_fts_checks, "failures": auxiliary_fts_failures},
            "Each non-message FTS index exactly matches its own source entity table.",
            dimension="integrity",
        )

        evidence_tables = [name for name in tables if "evidence" in name.casefold() and "message_id" in table_columns(connection, name)]
        evidence_orphans: list[dict[str, Any]] = []
        excerpt_mismatches: list[dict[str, Any]] = []
        source_columns = [name for name in ("content_text", "visible_text", "reply_to_content") if name in columns]
        source_projection = ",".join("m." + quote_identifier(name) for name in source_columns)
        for table in evidence_tables:
            e_columns = table_columns(connection, table)
            orphan_count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {quote_identifier(table)} e LEFT JOIN {quoted_messages} m "
                    "ON m.message_id=e.message_id "
                    "WHERE e.message_id IS NOT NULL AND m.message_id IS NULL"
                ).fetchone()[0]
            )
            if orphan_count:
                evidence_orphans.append({"table": table, "orphan_message_ids": orphan_count})
            excerpt_column = (
                "exact_excerpt"
                if "exact_excerpt" in e_columns
                else "excerpt"
                if "excerpt" in e_columns
                else None
            )
            if excerpt_column and source_columns:
                quoted_excerpt = quote_identifier(excerpt_column)
                query = (
                    f"SELECT e.message_id,e.{quoted_excerpt},{source_projection} "
                    f"FROM {quote_identifier(table)} e "
                    f"JOIN {quoted_messages} m ON m.message_id=e.message_id "
                    f"WHERE e.{quoted_excerpt} IS NOT NULL AND TRIM(e.{quoted_excerpt})<>''"
                )
                for row in connection.execute(query):
                    if not excerpt_matches(str(row[1]), *row[2:]):
                        excerpt_mismatches.append(
                            {
                                "table": table,
                                "column": excerpt_column,
                                "message_id": str(row[0]),
                                "excerpt": str(row[1])[:240],
                            }
                        )
        recorder.add(
            "evidence_message_links_resolve",
            not evidence_orphans,
            "critical",
            evidence_orphans,
            [],
            dimension="evidence",
        )

        json_evidence_errors: list[dict[str, Any]] = []
        if "research_questions" in tables and "evidence_message_ids_json" in table_columns(
            connection, "research_questions"
        ):
            rq_columns = table_columns(connection, "research_questions")
            identifier = (
                "research_question_id"
                if "research_question_id" in rq_columns
                else "question_id"
                if "question_id" in rq_columns
                else None
            )
            if identifier:
                for question_id, encoded in connection.execute(
                    f"SELECT {quote_identifier(identifier)},evidence_message_ids_json FROM research_questions"
                ):
                    try:
                        evidence_ids = json.loads(str(encoded))
                        if not isinstance(evidence_ids, list) or not evidence_ids:
                            raise ValueError("evidence IDs must be a non-empty JSON array")
                        missing_ids = sorted({str(item) for item in evidence_ids} - stored_ids)
                        if missing_ids:
                            raise ValueError("unresolved IDs: " + ",".join(missing_ids[:10]))
                    except (json.JSONDecodeError, TypeError, ValueError) as exc:
                        json_evidence_errors.append({"question_id": str(question_id), "error": str(exc)})
        recorder.add(
            "json_evidence_lists_resolve",
            not json_evidence_errors,
            "critical",
            len(json_evidence_errors),
            0,
            dimension="evidence",
            examples=json_evidence_errors,
        )
        recorder.add(
            "evidence_excerpts_trace_to_source",
            not excerpt_mismatches,
            "high",
            len(excerpt_mismatches),
            0,
            dimension="evidence",
            examples=excerpt_mismatches,
            note="Exact, normalized, and explicitly ellipsis-truncated excerpts are accepted.",
        )

        entity_mappings = (
            ("rejection_block_findings", "finding_id", "rejection_block_finding_evidence", "finding_id"),
            ("trades", "trade_id", "trade_evidence", "trade_id"),
            ("trading_models", "model_id", "model_evidence", "model_id"),
            ("claims", "claim_id", "claim_evidence", "claim_id"),
        )
        entities_without_evidence: list[dict[str, Any]] = []
        for entity_table, entity_id, bridge, bridge_id in entity_mappings:
            if entity_table in tables and bridge in tables:
                missing_count = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {quote_identifier(entity_table)} e WHERE NOT EXISTS "
                        f"(SELECT 1 FROM {quote_identifier(bridge)} b WHERE b.{quote_identifier(bridge_id)}="
                        f"e.{quote_identifier(entity_id)})"
                    ).fetchone()[0]
                )
                if missing_count:
                    entities_without_evidence.append({"table": entity_table, "count": missing_count})
        recorder.add(
            "derived_entities_have_evidence",
            not entities_without_evidence,
            "critical",
            entities_without_evidence,
            [],
            dimension="evidence",
        )

        if "channel_inventory" in tables and "channel_id" in table_columns(connection, "channel_inventory"):
            inventory_columns = table_columns(connection, "channel_inventory")
            stored_container_ids = {
                str(row[0]) for row in connection.execute("SELECT channel_id FROM channel_inventory")
            }
            missing_container_ids = sorted(expected_container_ids - stored_container_ids)
            expected_not_exact: list[str] = []
            if "exact_id_known" in inventory_columns:
                expected_not_exact = sorted(
                    channel_id
                    for channel_id in expected_container_ids & stored_container_ids
                    if connection.execute(
                        "SELECT exact_id_known FROM channel_inventory WHERE channel_id=?",
                        (channel_id,),
                    ).fetchone()[0]
                    != 1
                )
            extra_container_ids = stored_container_ids - expected_container_ids
            allowed_forum_threads, unexplained_extras = classify_database_inventory_extensions(
                connection,
                extra_container_ids,
                expected_container_ids,
            )
            recorder.add(
                "sqlite_inventory_source_parity",
                not missing_container_ids
                and not expected_not_exact
                and not unexplained_extras,
                "critical",
                {
                    "frozen_external": len(expected_container_ids),
                    "database": len(stored_container_ids),
                    "missing_frozen_external": len(missing_container_ids),
                    "frozen_external_not_exact": len(expected_not_exact),
                    "provenance_backed_observed_forum_threads": len(allowed_forum_threads),
                    "unexplained_extra_containers": len(unexplained_extras),
                },
                {
                    "missing_frozen_external": 0,
                    "frozen_external_not_exact": 0,
                    "unexplained_extra_containers": 0,
                    "provenance_backed_observed_forum_threads": "allowed",
                },
                dimension="coverage",
                examples=limited(
                    [{"missing_container_id": item} for item in missing_container_ids]
                    + [{"frozen_external_not_exact": item} for item in expected_not_exact]
                    + unexplained_extras
                ),
                note=(
                    "Every frozen external container must be present and exact. Additional rows are "
                    "allowed only when they are exact forum threads parented to a frozen top-level "
                    "forum and backed by a trusted non-migration channel-segment occurrence."
                ),
            )
        else:
            recorder.add(
                "sqlite_inventory_source_parity",
                not expected_container_ids,
                "critical",
                {"source": len(expected_container_ids), "database_table": False},
                {"source": 0, "database_table": "or channel_inventory table present"},
                dimension="coverage",
            )

        coverage_issues: list[dict[str, Any]] = []
        window_start_utc = central_midnight_utc(window_start)
        window_end_utc = central_midnight_utc(window_end + dt.timedelta(days=1))
        if {"channel_inventory", "collection_units"} <= set(tables):
            inventory_columns = table_columns(connection, "channel_inventory")
            inventory_projection = ["channel_id"]
            for optional in ("kind", "is_accessible"):
                if optional in inventory_columns:
                    inventory_projection.append(optional)
            for row in connection.execute(
                "SELECT " + ",".join(quote_identifier(item) for item in inventory_projection)
                + " FROM channel_inventory"
            ):
                values = dict(zip(inventory_projection, row))
                channel_id = str(values["channel_id"])
                kind = str(values.get("kind") or "").casefold()
                if any(label in kind for label in ("category", "voice", "stage", "directory")):
                    continue
                if values.get("is_accessible") == 0:
                    continue
                complete_boundary = False
                unit_rows = connection.execute(
                    "SELECT unit_id,status,window_start_utc,window_end_utc "
                    "FROM collection_units WHERE channel_id=?",
                    (channel_id,),
                ).fetchall()
                for unit_id, status, start_text, end_text in unit_rows:
                    if str(status).casefold() != "complete":
                        continue
                    try:
                        if parse_iso_utc(start_text) <= window_start_utc and parse_iso_utc(end_text) >= window_end_utc:
                            complete_boundary = True
                            break
                    except ValueError:
                        coverage_issues.append(
                            {
                                "channel_id": channel_id,
                                "unit_id": str(unit_id),
                                "reason": "invalid coverage timestamp",
                            }
                        )
                if not complete_boundary:
                    coverage_issues.append(
                        {
                            "channel_id": channel_id,
                            "reason": "no complete unit spans the full requested Central-local window",
                            "unit_count": len(unit_rows),
                        }
                    )
            if "v_collection_gaps" in {
                str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='view'")
            }:
                gap_count = int(connection.execute("SELECT COUNT(*) FROM v_collection_gaps").fetchone()[0])
                if gap_count:
                    coverage_issues.append({"reason": "v_collection_gaps is non-empty", "rows": gap_count})
        else:
            coverage_issues.append({"reason": "channel_inventory and/or collection_units table missing"})
        recorder.add(
            "sqlite_whole_server_coverage_gate",
            not coverage_issues,
            "critical",
            {"issues": len(coverage_issues)},
            {"issues": 0},
            dimension="coverage",
            examples=coverage_issues,
        )

        db_reply_issues: list[dict[str, Any]] = []
        if "reply_to_message_id" in columns:
            state_column = "reply_target_state" if "reply_target_state" in columns else None
            projection = ",m.reply_target_state" if state_column else ""
            for row in connection.execute(
                f"SELECT m.message_id,m.reply_to_message_id{projection} FROM {quoted_messages} m "
                "LEFT JOIN "
                f"{quoted_messages} p ON p.message_id=m.reply_to_message_id "
                "WHERE m.reply_to_message_id IS NOT NULL AND TRIM(m.reply_to_message_id)<>'' AND p.message_id IS NULL"
            ):
                state = str(row[2] or "").casefold() if state_column else ""
                if state not in ALLOWED_REPLY_STATES - {"resolved", "not_applicable"}:
                    db_reply_issues.append(
                        {
                            "message_id": str(row[0]),
                            "reply_to_message_id": str(row[1]),
                            "reply_target_state": state or None,
                        }
                    )
        recorder.add(
            "sqlite_reply_targets_resolve_or_have_state",
            "reply_to_message_id" in columns and not db_reply_issues,
            "critical",
            {"reply_column_present": "reply_to_message_id" in columns, "unresolved": len(db_reply_issues)},
            {"reply_column_present": True, "unresolved": 0},
            dimension="integrity",
            examples=db_reply_issues,
        )

        invalid_scope_rows: list[dict[str, Any]] = []
        nonzero_outside_sources: list[dict[str, Any]] = []
        scope_assertion_locations: list[str] = []
        outside_source_assertion_locations: list[str] = []
        for table in tables:
            cols = table_columns(connection, table)
            if "source_scope" in cols:
                scope_assertion_locations.append(f"{table}.source_scope")
                values = list(
                    connection.execute(
                        f"SELECT {quote_identifier('source_scope')},COUNT(*) FROM {quote_identifier(table)} "
                        f"GROUP BY {quote_identifier('source_scope')}"
                    )
                )
                for value, count in values:
                    if str(value or "").replace(" ", "_").casefold() != "discord_only":
                        invalid_scope_rows.append({"table": table, "value": value, "count": count})
            if "outside_sources_used" in cols:
                outside_source_assertion_locations.append(f"{table}.outside_sources_used")
                count = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {quote_identifier(table)} WHERE COALESCE(outside_sources_used,0)<>0"
                    ).fetchone()[0]
                )
                if count:
                    nonzero_outside_sources.append({"table": table, "count": count})
        if "meta" in tables and {"key", "value"} <= table_columns(connection, "meta"):
            meta = {str(key): str(value) for key, value in connection.execute("SELECT key,value FROM meta")}
            scope_value = meta.get("source_scope") or meta.get("source_scope_policy")
            if scope_value is not None:
                scope_assertion_locations.append("meta.source_scope")
            if scope_value and scope_value.replace(" ", "_").casefold() != "discord_only":
                invalid_scope_rows.append({"table": "meta", "value": scope_value, "count": 1})
            outside_value = meta.get("outside_sources_used")
            if outside_value is not None:
                outside_source_assertion_locations.append("meta.outside_sources_used")
            if outside_value and outside_value.casefold() not in {"0", "false", "no"}:
                nonzero_outside_sources.append({"table": "meta", "value": outside_value})
        recorder.add(
            "discord_only_database_assertions",
            bool(scope_assertion_locations)
            and bool(outside_source_assertion_locations)
            and not invalid_scope_rows
            and not nonzero_outside_sources,
            "critical",
            {
                "scope_assertions": scope_assertion_locations,
                "outside_source_assertions": outside_source_assertion_locations,
                "invalid_scope": invalid_scope_rows,
                "outside_sources": nonzero_outside_sources,
            },
            {
                "scope_assertions": ">=1 explicit discord_only assertion",
                "outside_source_assertions": ">=1 explicit zero/false assertion",
                "invalid_scope": [],
                "outside_sources": [],
            },
            dimension="provenance",
        )

        if "attachments" in tables and "attachment_id" in table_columns(connection, "attachments"):
            attachment_columns = table_columns(connection, "attachments")
            stored_attachment_ids = {
                str(row[0]) for row in connection.execute("SELECT attachment_id FROM attachments")
            }
            missing_attachments = sorted(expected_attachment_ids - stored_attachment_ids)
            extra_attachments = sorted(stored_attachment_ids - expected_attachment_ids)
            recorder.add(
                "sqlite_attachment_source_parity",
                not missing_attachments and not extra_attachments,
                "critical",
                {
                    "source": len(expected_attachment_ids),
                    "database": len(stored_attachment_ids),
                    "missing": len(missing_attachments),
                    "extra": len(extra_attachments),
                },
                {"missing": 0, "extra": 0},
                dimension="integrity",
                examples=limited(
                    [{"missing_attachment_id": item} for item in missing_attachments]
                    + [{"extra_attachment_id": item} for item in extra_attachments]
                ),
            )
            status_column = (
                "capture_status"
                if "capture_status" in attachment_columns
                else "download_status"
                if "download_status" in attachment_columns
                else "extraction_status"
                if "extraction_status" in attachment_columns
                else None
            )
            missing_status = (
                int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM attachments WHERE {quote_identifier(status_column)} IS NULL "
                        f"OR TRIM({quote_identifier(status_column)})=''"
                    ).fetchone()[0]
                )
                if status_column
                else len(stored_attachment_ids)
            )
            recorder.add(
                "sqlite_attachment_status_accounting",
                status_column is not None and missing_status == 0,
                "high",
                {"status_column": status_column, "missing_status": missing_status},
                {"status_column": "present", "missing_status": 0},
                dimension="completeness",
            )
        else:
            recorder.add(
                "sqlite_attachment_source_parity",
                not expected_attachment_ids,
                "critical",
                {"source": len(expected_attachment_ids), "database_table": False},
                {"source": 0, "database_table": "or attachments table present"},
                dimension="integrity",
            )
    finally:
        connection.close()
    return summary


def verify_sources_stable(artifacts: list[SegmentArtifact]) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    for artifact in artifacts:
        path = artifact.path
        if not path.is_file():
            changed.append({"path": str(path), "reason": "missing after validation"})
            continue
        actual = preservation_hashes.sha256_file(path)
        if actual != artifact.source_record["sha256"]:
            changed.append(
                {
                    "path": str(path),
                    "before_sha256": artifact.source_record["sha256"],
                    "after_sha256": actual,
                }
            )
    return changed


def verify_source_hash_manifest(path: Path, source_paths: list[Path]) -> dict[str, Any]:
    baseline = preservation_hashes.load_manifest(path.resolve())
    result = preservation_hashes.verify_manifest(path.resolve())
    protected_root = Path(str(baseline.get("protected_root") or "")).resolve()
    expected_paths = {
        (protected_root / Path(str(row.get("relative_path") or ""))).resolve()
        for row in baseline.get("files") or []
        if isinstance(row, dict) and row.get("relative_path")
    }
    actual_paths = {item.resolve() for item in source_paths}
    result["source_set_match"] = expected_paths == actual_paths
    result["manifest_source_count"] = len(expected_paths)
    result["discovered_source_count"] = len(actual_paths)
    result["unmanifested_sources"] = [str(item) for item in sorted(actual_paths - expected_paths, key=str)[:20]]
    result["manifested_but_not_selected"] = [
        str(item) for item in sorted(expected_paths - actual_paths, key=str)[:20]
    ]
    if not result["source_set_match"]:
        result["status"] = "failed"
    return result


def write_exclusive(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise ValidationError(f"Refusing to overwrite validation output: {path}") from exc


def policy_inputs_from_artifacts(
    artifacts: list[SegmentArtifact],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    segments: list[dict[str, Any]] = []
    occurrences: list[dict[str, Any]] = []
    canonical: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        segment = artifact.payload.get("segment")
        segment = segment if isinstance(segment, dict) else {}
        segment_id = sha256_bytes(
            compact(
                {
                    "source_sha256": artifact.source_record.get("sha256"),
                    "channel_id": artifact.channel_id,
                    "query": segment.get("query"),
                    "start": artifact.start.isoformat(),
                    "end": artifact.end.isoformat(),
                }
            ).encode("utf-8")
        )
        segments.append(
            {
                "segment_id": segment_id,
                "source_file_id": artifact.source_record.get("sha256"),
                "source_file_relative_path": str(artifact.path),
                "input_role": artifact.input_role,
                "query_container_id": artifact.channel_id,
                "query_container_name": artifact.channel_name,
                "query": segment.get("query"),
                "start_date": artifact.start.isoformat(),
                "end_date": artifact.end.isoformat(),
                "computed_complete": artifact.complete,
                "reported_total": int(artifact.payload.get("reported_total") or 0),
                "captured_rows_computed": len(artifact.messages),
            }
        )
        for message in artifact.messages:
            message_id = str(message.get("message_id") or "")
            thread_id = str(
                message.get("inferred_thread_channel_id")
                or message.get("thread_channel_id")
                or message.get("message_channel_id")
                or message.get("channel_id")
                or message.get("collection_channel_id")
                or artifact.channel_id
                or ""
            )
            parent_id = str(
                message.get("parent_channel_id")
                or message.get("group_header_parent_forum_channel_id")
                or message.get("forum_channel_id")
                or message.get("thread_parent_id")
                or ""
            )
            if (
                not parent_id
                and artifact.channel_id
                and thread_id
                and thread_id != artifact.channel_id
                and str(message.get("collection_channel_id") or artifact.channel_id)
                == artifact.channel_id
            ):
                parent_id = artifact.channel_id
            occurrences.append(
                {
                    "message_id": message_id,
                    "segment_id": segment_id,
                    "query_container_id": artifact.channel_id,
                    "message_container_id": thread_id,
                    "parent_container_id": parent_id or None,
                    "payload": message,
                }
            )
            if DISCORD_ID_RE.fullmatch(message_id):
                canonical.setdefault(message_id, {"message_id": message_id})
    return segments, occurrences, list(canonical.values())


def verify_auxiliary_sources_stable(
    source_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    for source in source_records:
        path = Path(str(source.get("path") or ""))
        if not path.is_file():
            changed.append({"path": str(path), "reason": "missing after validation"})
            continue
        actual = preservation_hashes.sha256_file(path)
        if actual != source.get("sha256"):
            changed.append(
                {
                    "path": str(path),
                    "before_sha256": source.get("sha256"),
                    "after_sha256": actual,
                }
            )
    return changed


def validate_collection_drift_audit(
    payload: dict[str, Any],
    *,
    path: Path,
    sha256: str,
    window_start: dt.date,
    window_end: dt.date,
    required_end_exclusive_utc: dt.datetime,
) -> dict[str, Any]:
    """Validate the separate final drift audit without trusting its exit code."""

    errors: list[str] = []
    if payload.get("audit_type") != "discord_collection_total_drift":
        errors.append("unexpected_audit_type")
    if payload.get("mode") != "final":
        errors.append("audit_mode_not_final")
    if payload.get("overall_status") != "PASS":
        errors.append("overall_status_not_pass")
    if payload.get("release_gate_passed") is not True:
        errors.append("release_gate_not_passed")

    audit_window = payload.get("audit_window")
    if not isinstance(audit_window, dict):
        errors.append("audit_window_missing")
        audit_window = {}
    if str(audit_window.get("start") or "") != window_start.isoformat():
        errors.append("audit_window_start_mismatch")
    if str(audit_window.get("end") or "") != window_end.isoformat():
        errors.append("audit_window_end_mismatch")
    if str(audit_window.get("timezone") or "") != "America/Chicago":
        errors.append("audit_window_timezone_mismatch")

    try:
        generated = parse_iso_utc(payload.get("generated_at_utc"))
    except (TypeError, ValueError):
        generated = None
        errors.append("generated_at_utc_invalid")
    if generated is not None and generated < required_end_exclusive_utc:
        errors.append("audit_generated_before_required_cutoff")

    boundary = payload.get("evidence_boundary")
    if not isinstance(boundary, dict):
        errors.append("evidence_boundary_missing")
        boundary = {}
    if boundary.get("outside_sources_permitted") is not False:
        errors.append("outside_sources_not_prohibited")
    if boundary.get("links_or_attachments_fetched") is not False:
        errors.append("link_or_attachment_fetch_not_zero")
    if "discord" not in str(boundary.get("source") or "").casefold():
        errors.append("discord_source_boundary_missing")

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary_missing")
        summary = {}
    zero_fields = (
        "structural_failure_count",
        "unresolved_count",
        "effective_final_failure_count",
        "orphan_quarantined_partial_count",
    )
    for field in zero_fields:
        if type(summary.get(field)) is not int or summary.get(field) != 0:
            errors.append(f"summary_{field}_not_zero")

    for field in ("failures", "unresolved", "orphan_quarantined_partials"):
        value = payload.get(field)
        if not isinstance(value, list) or value:
            errors.append(f"{field}_not_empty")

    exit_contract = payload.get("exit_code_contract")
    if not isinstance(exit_contract, dict) or exit_contract != {
        "PASS": 0,
        "FAIL": 1,
        "PENDING": 2,
    }:
        errors.append("exit_code_contract_mismatch")

    return {
        "status": "passed" if not errors else "failed",
        "passed": not errors,
        "path": str(path.resolve()),
        "sha256": sha256,
        "overall_status": payload.get("overall_status"),
        "mode": payload.get("mode"),
        "release_gate_passed": payload.get("release_gate_passed") is True,
        "summary": summary,
        "errors": errors,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    recorder = CheckRecorder()
    issues: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    window_start = parse_date(args.window_start)
    window_end = parse_date(args.window_end)
    if window_start > window_end:
        raise ValidationError("--window-start must not be after --window-end")
    cutoff = parse_iso_utc(args.data_cutoff_utc) if args.data_cutoff_utc else dt.datetime.now(dt.timezone.utc)
    requested_end_exclusive_utc = central_midnight_utc(window_end + dt.timedelta(days=1))

    preservation_before: dict[str, Any] | None = None
    if args.preservation_manifest:
        preservation_before = preservation_hashes.verify_manifest(args.preservation_manifest.resolve())
        recorder.add(
            "existing_artifacts_unchanged_before_validation",
            preservation_before["status"] == "passed",
            "critical",
            {
                "status": preservation_before["status"],
                "missing": len(preservation_before["missing"]),
                "changed": len(preservation_before["changed"]),
            },
            {"status": "passed", "missing": 0, "changed": 0},
            dimension="preservation",
            examples=limited(preservation_before["missing"] + preservation_before["changed"]),
        )
    else:
        recorder.add(
            "existing_artifact_hash_baseline_supplied",
            False,
            "critical",
            None,
            "--preservation-manifest",
            dimension="preservation",
        )

    source_roles = discover_segment_files_by_role(
        {
            "channel_capture": [
                path.resolve() for path in getattr(args, "segments", [])
            ],
            "relevance_query": [
                path.resolve()
                for path in (getattr(args, "relevance_segments", None) or [])
            ],
            "residual_audit": [
                path.resolve()
                for path in (getattr(args, "audit_segments", None) or [])
            ],
        }
    )
    source_paths = list(source_roles)
    recorder.add(
        "segment_sources_present",
        bool(source_paths),
        "critical",
        len(source_paths),
        "> 0",
        dimension="completeness",
    )
    source_hash_before: dict[str, Any] | None = None
    if args.source_hash_manifest:
        source_hash_before = verify_source_hash_manifest(args.source_hash_manifest.resolve(), source_paths)
        recorder.add(
            "source_hash_manifest_matches_before_validation",
            source_hash_before["status"] == "passed",
            "critical",
            {
                "status": source_hash_before["status"],
                "manifest_sources": source_hash_before["manifest_source_count"],
                "discovered_sources": source_hash_before["discovered_source_count"],
                "source_set_match": source_hash_before["source_set_match"],
            },
            {"status": "passed", "source_set_match": True},
            dimension="provenance",
            examples=limited(
                source_hash_before.get("changed", [])
                + source_hash_before.get("missing", [])
                + source_hash_before.get("unmanifested_sources", [])
                + source_hash_before.get("manifested_but_not_selected", [])
            ),
        )
    else:
        recorder.add(
            "source_hash_manifest_supplied",
            False,
            "critical",
            None,
            "write-once source hash manifest covering the exact selected segment set",
            dimension="provenance",
        )
    artifacts: list[SegmentArtifact] = []
    timestamp_scope_auxiliary_source_records: list[dict[str, Any]] = []
    premium_forum_auxiliary_source_records: list[dict[str, Any]] = []
    for path in source_paths:
        artifact = validate_one_segment(
            path,
            guild_id=args.guild_id,
            window_start=window_start,
            window_end=window_end,
            cutoff_utc=cutoff,
            issues=issues,
            input_role=source_roles[path],
        )
        if artifact:
            artifacts.append(artifact)
            timestamp_scope_auxiliary_source_records.extend(
                artifact.timestamp_scope_source_records
            )
            premium_forum_auxiliary_source_records.extend(
                artifact.premium_forum_provenance_source_records
            )

    timestamp_mode_counts: collections.Counter[str] = collections.Counter()
    timestamp_unresolved_count = 0
    timestamp_invalid_sidecar_count = 0
    timestamp_unused_record_count = 0
    timestamp_sidecars: list[dict[str, Any]] = []
    for artifact in artifacts:
        audit = artifact.timestamp_scope_integrity
        for mode, count in (audit.get("mode_counts") or {}).items():
            timestamp_mode_counts[str(mode)] += int(count or 0)
        timestamp_unresolved_count += int(audit.get("unresolved_count") or 0)
        timestamp_invalid_sidecar_count += int(
            audit.get("sidecar_error_count") or 0
        )
        timestamp_unused_record_count += int(
            audit.get("unused_revalidation_record_count") or 0
        )
        sidecar = audit.get("sidecar")
        if isinstance(sidecar, dict) and sidecar.get("provided") is True:
            timestamp_sidecars.append(copy.deepcopy(sidecar))
    timestamp_scope_integrity_summary = {
        "schema_version": "1.0.0",
        "passed": bool(
            timestamp_unresolved_count == 0
            and timestamp_invalid_sidecar_count == 0
            and timestamp_unused_record_count == 0
            and all(
                row.get("valid") is True
                and row.get("content_hash_bound") is True
                for row in timestamp_sidecars
            )
        ),
        "content_hash_bound": bool(
            all(
                row.get("valid") is True
                and row.get("content_hash_bound") is True
                for row in timestamp_sidecars
            )
        ),
        "mode_counts": dict(sorted(timestamp_mode_counts.items())),
        "unresolved_message_count": timestamp_unresolved_count,
        "invalid_sidecar_count": timestamp_invalid_sidecar_count,
        "unused_revalidation_record_count": timestamp_unused_record_count,
        "sidecar_count": len(timestamp_sidecars),
        "sidecars": timestamp_sidecars,
    }

    for code, (severity, expected, dimension) in ISSUE_META.items():
        details = issues.get(code, [])
        recorder.add(
            code,
            not details,
            severity,
            len(details),
            expected,
            dimension=dimension,
            examples=limited(details),
        )
    recorder.add(
        "timestamp_scope_integrity_content_hash_bound",
        timestamp_scope_integrity_summary["passed"],
        "critical",
        timestamp_scope_integrity_summary,
        {
            "passed": True,
            "content_hash_bound": True,
            "unresolved_message_count": 0,
            "invalid_sidecar_count": 0,
            "unused_revalidation_record_count": 0,
        },
        dimension="provenance",
    )

    complete_files = [artifact for artifact in artifacts if artifact.complete]
    partial_files = [artifact for artifact in artifacts if not artifact.complete]
    inventory_payload: dict[str, Any] | None = None
    inventory_rows: list[dict[str, Any]] = []
    try:
        inventory_payload, inventory_rows = load_inventory(args.inventory.resolve() if args.inventory else None)
    except (OSError, ValidationError) as exc:
        recorder.add(
            "channel_thread_inventory_readable",
            False,
            "critical",
            str(exc),
            "valid UTF-8 JSON inventory",
            dimension="coverage",
        )
    else:
        recorder.add(
            "channel_thread_inventory_readable",
            inventory_payload is not None,
            "critical",
            str(args.inventory.resolve()) if args.inventory else None,
            "inventory artifact",
            dimension="coverage",
        )

    inventory_summary = validate_inventory_contract(
        recorder,
        inventory_payload,
        inventory_rows,
        args.guild_id,
        window_start,
        window_end,
    )

    relevance_plan_path = getattr(args, "relevance_plan", None)
    progress_path = getattr(args, "orchestrator_progress_manifest", None)
    relevance_policy_summary: dict[str, Any] | None = None
    auxiliary_source_records: list[dict[str, Any]] = list(
        timestamp_scope_auxiliary_source_records
    )
    auxiliary_source_records.extend(premium_forum_auxiliary_source_records)
    drift_audit_summary: dict[str, Any] = {
        "status": "not_supplied",
        "passed": False,
        "errors": ["final_collection_drift_audit_not_supplied"],
    }
    drift_audit_path = getattr(args, "drift_audit", None)
    if drift_audit_path:
        resolved_drift_audit = drift_audit_path.resolve()
        try:
            drift_payload, drift_source_record = stable_read_json(
                resolved_drift_audit
            )
            drift_source_record["kind"] = "final_collection_drift_audit"
            auxiliary_source_records.append(drift_source_record)
            drift_audit_summary = validate_collection_drift_audit(
                drift_payload,
                path=resolved_drift_audit,
                sha256=drift_source_record["sha256"],
                window_start=window_start,
                window_end=window_end,
                required_end_exclusive_utc=requested_end_exclusive_utc,
            )
        except (OSError, ValidationError) as exc:
            drift_audit_summary = {
                "status": "failed",
                "passed": False,
                "path": str(resolved_drift_audit),
                "errors": [f"unreadable_or_unstable_drift_audit: {exc}"],
            }
    recorder.add(
        "collection_drift_final_audit_passed",
        drift_audit_summary.get("passed") is True,
        "critical",
        drift_audit_summary,
        {
            "status": "passed",
            "mode": "final",
            "overall_status": "PASS",
            "structural_failure_count": 0,
            "unresolved_count": 0,
            "orphan_quarantined_partial_count": 0,
        },
        dimension="provenance",
        examples=limited(drift_audit_summary.get("errors") or []),
        note=(
            "A final release requires the independent collection-drift audit to "
            "resolve every total-drift/replacement chain with zero unresolved or "
            "orphan quarantined partials."
        ),
    )
    diagnostic_policy_occurrences: list[dict[str, Any]] = []
    policy_inventory_rows = inventory_rows
    if relevance_plan_path:
        resolved_plan = relevance_plan_path.resolve()
        _plan_payload_for_hash, plan_source_record = stable_read_json(resolved_plan)
        auxiliary_source_records.append(plan_source_record)
        plan_bundle = relevance_release_policy.load_validated_plan(
            resolved_plan,
            args.inventory.resolve() if args.inventory else None,
        )
        for source in plan_bundle.get("plan", {}).get("vocabulary_sources", []):
            if not isinstance(source, dict):
                continue
            relative = str(source.get("path_relative_to_plan") or "")
            if not relative:
                continue
            source_path = (resolved_plan.parent / relative).resolve()
            if source_path.is_file():
                stat = source_path.stat()
                auxiliary_source_records.append(
                    {
                        "path": str(source_path),
                        "filename": source_path.name,
                        "size_bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "sha256": preservation_hashes.sha256_file(source_path),
                        "kind": "discord_vocabulary_source",
                    }
                )
        progress_payload: dict[str, Any] | None = None
        if progress_path:
            progress_payload, progress_source_record = stable_read_json(
                progress_path.resolve()
            )
            auxiliary_source_records.append(progress_source_record)
        policy_segments, policy_occurrences, policy_messages = policy_inputs_from_artifacts(
            artifacts
        )
        relevance_policy_summary = relevance_release_policy.evaluate_relevance_policy(
            plan_bundle=plan_bundle,
            segments=policy_segments,
            inventory=inventory_payload or {},
            progress=progress_payload,
            data_cutoff_utc=cutoff,
            required_end_exclusive_utc=requested_end_exclusive_utc,
            occurrences=policy_occurrences,
            messages=policy_messages,
        )
        diagnostic_segment_ids = {
            str(row.get("segment_id") or "")
            for row in relevance_policy_summary.get("classified_segments", [])
            if row.get("policy_role") == "diagnostic_targeted_full_capture"
            and not row.get("computed_complete")
        }
        diagnostic_policy_occurrences = [
            row
            for row in policy_occurrences
            if str(row.get("segment_id") or "") in diagnostic_segment_ids
        ]
        if args.database:
            for gate in relevance_policy_summary.get("hard_gates", []):
                if gate.get("gate_id") != "claim_calibration":
                    continue
                detail = gate.get("detail") if isinstance(gate.get("detail"), dict) else {}
                if detail.get("basis") == "raw_corpus_emits_no_normalized_trading_claims":
                    gate["passed"] = False
                    gate["detail"] = {
                        "reason": "database_claim_calibration_evidence_required",
                        "required": (
                            "release_evidence.claim_calibration with provenance refs and zero "
                            "unsupported/uncalibrated probability claims"
                        ),
                    }
            relevance_policy_summary["release_ready"] = all(
                gate.get("passed") is True
                for gate in relevance_policy_summary.get("hard_gates", [])
            )
            if not relevance_policy_summary["release_ready"]:
                for channel in relevance_policy_summary.get("channel_coverage", []):
                    if channel.get("policy") == relevance_release_policy.TARGETED_POLICY:
                        channel["completion_label"] = "topic-partial_targeted"
                        channel["policy_gate_passed"] = False
                        channel["message_complete"] = False
        recorder.add(
            "relevance_collection_plan_validated",
            relevance_policy_summary.get("plan_valid") is True,
            "critical",
            relevance_policy_summary.get("plan_validation"),
            {"status": "passed"},
            dimension="methodology",
        )
        for gate in relevance_policy_summary.get("hard_gates", []):
            recorder.add(
                f"relevance_plan_hard_gate__{gate.get('gate_id')}",
                gate.get("passed") is True,
                "critical",
                gate.get("detail"),
                {"passed": True},
                dimension="coverage",
                note="A hard plan gate is fail-closed; unsupported or missing evidence cannot pass.",
            )
        required_partial = relevance_release_policy.policy_required_partial_segments(
            relevance_policy_summary
        )
        diagnostic_partial = [
            row
            for row in relevance_policy_summary.get("classified_segments", [])
            if row.get("policy_role") == "diagnostic_targeted_full_capture"
            and not row.get("computed_complete")
        ]
        recorder.add(
            "all_policy_required_segment_files_complete",
            bool(artifacts) and not required_partial,
            "critical",
            {
                "complete_files": len(complete_files),
                "partial_files": len(partial_files),
                "required_partial_files": len(required_partial),
                "diagnostic_targeted_partial_files": len(diagnostic_partial),
            },
            {"required_partial_files": 0},
            dimension="coverage",
            examples=[
                {
                    "segment_id": row.get("segment_id"),
                    "policy_role": row.get("policy_role"),
                    "channel_id": row.get("query_container_id"),
                }
                for row in required_partial[:20]
            ],
            note=(
                "Partial full-channel attempts in newsfeed/chat/levels are diagnostic: "
                "they remain ingested but never certify message completeness and do not block "
                "the policy gate by partial status alone."
            ),
        )
        targeted_ids = {
            str(row.get("channel_id"))
            for row in plan_bundle.get("plan", {}).get("channel_policies", [])
            if isinstance(row, dict)
            and row.get("policy")
            == relevance_release_policy.TARGETED_POLICY
        }
        policy_inventory_rows = [
            row for row in inventory_rows if inventory_identity(row) not in targeted_ids
        ]
    else:
        recorder.add(
            "all_segment_files_complete",
            bool(artifacts) and not partial_files,
            "critical",
            {"complete": len(complete_files), "partial": len(partial_files)},
            {"partial": 0},
            dimension="coverage",
            examples=[
                {
                    "file": str(item.path),
                    "pages_captured": item.payload.get("pages_captured"),
                    "reported_pages": item.payload.get("reported_pages"),
                }
                for item in partial_files[:20]
            ],
        )

    coverage_summary = coverage_validation(
        recorder,
        artifacts,
        policy_inventory_rows,
        window_start,
        window_end,
        guild_wide_required=relevance_policy_summary is None,
        policy_release_ready=(
            relevance_policy_summary.get("release_ready")
            if relevance_policy_summary is not None
            else None
        ),
    )

    full_day_elapsed = cutoff >= requested_end_exclusive_utc
    recorder.add(
        "requested_window_elapsed_at_cutoff",
        full_day_elapsed,
        "critical",
        {
            "data_cutoff_utc": cutoff.isoformat().replace("+00:00", "Z"),
            "required_end_exclusive_utc": requested_end_exclusive_utc.isoformat().replace("+00:00", "Z"),
            "final_day_status": "complete_day_possible" if full_day_elapsed else "partial_live_day",
        },
        {"final_day_status": "complete_day_possible"},
        dimension="timeliness",
        note="July 20 cannot be certified as a full Central-local day before July 21 05:00Z.",
    )

    diagnostic_occurrences = canonical_occurrences(artifacts, complete_only=False)
    complete_occurrences = canonical_occurrences(artifacts, complete_only=True)
    duplicate_summary = validate_duplicates_and_edits(recorder, diagnostic_occurrences)
    reply_summary = validate_replies(recorder, diagnostic_occurrences)
    attachment_occurrences = [*complete_occurrences]
    attachment_occurrences.extend(
        {"message": row.get("payload") or {}}
        for row in diagnostic_policy_occurrences
    )
    expected_attachment_ids = {
        str(attachment.get("attachment_id") or "")
        for occurrence in attachment_occurrences
        for attachment in (occurrence.get("message") or {}).get("attachments", []) or []
        if isinstance(attachment, dict)
        and str(
            attachment.get("relation_type")
            or attachment.get("ownership")
            or "owned"
        ).casefold()
        in {"owned", "attachment", "message_attachment"}
        and DISCORD_ID_RE.fullmatch(str(attachment.get("attachment_id") or ""))
    }
    attachment_archive_summary, archived_attachment_ids = validate_attachment_archive(
        recorder,
        getattr(args, "attachment_manifest", None),
        getattr(args, "attachment_archive_root", None),
        expected_attachment_ids,
        auxiliary_source_records,
    )
    attachment_summary, complete_attachment_ids = validate_attachments(
        recorder, attachment_occurrences, archived_attachment_ids
    )
    attachment_summary["archive"] = attachment_archive_summary
    complete_message_ids = {
        str(occurrence["message"].get("message_id") or "")
        for occurrence in complete_occurrences
        if DISCORD_ID_RE.fullmatch(str(occurrence["message"].get("message_id") or ""))
    }
    diagnostic_partial_message_ids = {
        str(row.get("message_id") or "")
        for row in diagnostic_policy_occurrences
        if DISCORD_ID_RE.fullmatch(str(row.get("message_id") or ""))
    }
    database_expected_message_ids = complete_message_ids | diagnostic_partial_message_ids

    database_summary = validate_sqlite(
        recorder,
        args.database.resolve() if args.database else None,
        database_expected_message_ids,
        complete_attachment_ids,
        {
            inventory_identity(row)
            for row in inventory_rows
            if DISCORD_ID_RE.fullmatch(inventory_identity(row))
        },
        window_start,
        window_end,
    )

    changed_sources = verify_sources_stable(artifacts)
    recorder.add(
        "source_artifacts_stable_during_validation",
        not changed_sources,
        "critical",
        len(changed_sources),
        0,
        dimension="provenance",
        examples=changed_sources,
    )
    changed_auxiliary_sources = verify_auxiliary_sources_stable(
        auxiliary_source_records
    )
    recorder.add(
        "relevance_policy_artifacts_stable_during_validation",
        not changed_auxiliary_sources,
        "critical",
        len(changed_auxiliary_sources),
        0,
        dimension="provenance",
        examples=changed_auxiliary_sources,
        note=(
            "Includes the relevance plan, Discord-derived vocabulary sources, and "
            "the orchestrator progress/review manifest and final collection-drift "
            "audit when supplied."
        ),
    )
    source_hash_after: dict[str, Any] | None = None
    if args.source_hash_manifest:
        source_hash_after = verify_source_hash_manifest(args.source_hash_manifest.resolve(), source_paths)
        recorder.add(
            "source_hash_manifest_matches_after_validation",
            source_hash_after["status"] == "passed",
            "critical",
            {
                "status": source_hash_after["status"],
                "manifest_sources": source_hash_after["manifest_source_count"],
                "discovered_sources": source_hash_after["discovered_source_count"],
                "source_set_match": source_hash_after["source_set_match"],
            },
            {"status": "passed", "source_set_match": True},
            dimension="provenance",
            examples=limited(
                source_hash_after.get("changed", [])
                + source_hash_after.get("missing", [])
                + source_hash_after.get("unmanifested_sources", [])
                + source_hash_after.get("manifested_but_not_selected", [])
            ),
        )

    preservation_after: dict[str, Any] | None = None
    if args.preservation_manifest:
        preservation_after = preservation_hashes.verify_manifest(args.preservation_manifest.resolve())
        recorder.add(
            "existing_artifacts_unchanged_after_validation",
            preservation_after["status"] == "passed",
            "critical",
            {
                "status": preservation_after["status"],
                "missing": len(preservation_after["missing"]),
                "changed": len(preservation_after["changed"]),
            },
            {"status": "passed", "missing": 0, "changed": 0},
            dimension="preservation",
            examples=limited(preservation_after["missing"] + preservation_after["changed"]),
        )

    critical_failures = recorder.failure_count({"critical"})
    high_failures = recorder.failure_count({"high"})
    lower_failures = recorder.failure_count({"medium", "low"})
    if critical_failures:
        status = "needs_revision"
        assessment = "Not releasable"
    elif high_failures or lower_failures:
        status = "passed_with_limitations"
        assessment = "Share with caveats"
    else:
        status = "passed"
        assessment = "Ready to share"

    report = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "independent_discord_corpus_validation",
        "generated_at_utc": utc_now(),
        "status": status,
        "overall_assessment": assessment,
        "scope": {
            "guild_id": args.guild_id,
            "source_scope": "discord_only",
            "outside_sources_used": False,
            "window_calendar_timezone": "America/Chicago",
            "window_start_local_date": window_start.isoformat(),
            "window_end_local_date_inclusive": window_end.isoformat(),
            "window_start_utc": central_midnight_utc(window_start).isoformat().replace("+00:00", "Z"),
            "window_end_exclusive_utc": requested_end_exclusive_utc.isoformat().replace("+00:00", "Z"),
            "local_calendar_days": (window_end - window_start).days + 1,
            "data_cutoff_utc": cutoff.isoformat().replace("+00:00", "Z"),
            "final_day_complete": full_day_elapsed,
        },
        "inputs": {
            "segment_inputs": [str(path.resolve()) for path in args.segments],
            "relevance_segment_inputs": [
                str(path.resolve())
                for path in (getattr(args, "relevance_segments", None) or [])
            ],
            "audit_segment_inputs": [
                str(path.resolve())
                for path in (getattr(args, "audit_segments", None) or [])
            ],
            "inventory": str(args.inventory.resolve()) if args.inventory else None,
            "relevance_plan": (
                str(relevance_plan_path.resolve()) if relevance_plan_path else None
            ),
            "orchestrator_progress_manifest": (
                str(progress_path.resolve()) if progress_path else None
            ),
            "collection_drift_audit": (
                str(drift_audit_path.resolve()) if drift_audit_path else None
            ),
            "attachment_manifest": (
                str(args.attachment_manifest.resolve())
                if getattr(args, "attachment_manifest", None)
                else None
            ),
            "attachment_archive_root": (
                str(args.attachment_archive_root.resolve())
                if getattr(args, "attachment_archive_root", None)
                else None
            ),
            "database": str(args.database.resolve()) if args.database else None,
            "preservation_manifest": str(args.preservation_manifest.resolve()) if args.preservation_manifest else None,
            "source_hash_manifest": str(args.source_hash_manifest.resolve()) if args.source_hash_manifest else None,
        },
        "source_artifacts": [artifact.source_record for artifact in artifacts],
        "auxiliary_source_artifacts": auxiliary_source_records,
        "counts": {
            "source_files_discovered": len(source_paths),
            "source_files_valid": len(artifacts),
            "complete_source_files": len(complete_files),
            "partial_source_files": len(partial_files),
            "diagnostic_message_occurrences": len(diagnostic_occurrences),
            "complete_source_message_occurrences": len(complete_occurrences),
            "complete_source_unique_message_ids": len(complete_message_ids),
            "diagnostic_partial_targeted_unique_message_ids": len(
                diagnostic_partial_message_ids
            ),
            "database_expected_unique_message_ids": len(database_expected_message_ids),
        },
        "coverage": coverage_summary,
        "relevance_policy": (
            {
                key: value
                for key, value in relevance_policy_summary.items()
                if key != "classified_segments"
            }
            if relevance_policy_summary is not None
            else {"enabled": False}
        ),
        "inventory_validation": inventory_summary,
        "duplicates_and_edits": duplicate_summary,
        "replies": reply_summary,
        "attachments": attachment_summary,
        "collection_drift_audit": drift_audit_summary,
        "timestamp_scope_integrity": timestamp_scope_integrity_summary,
        "database_validation": database_summary,
        "preservation": {
            "before": preservation_before,
            "after": preservation_after,
        },
        "source_hash_verification": {
            "before": source_hash_before,
            "after": source_hash_after,
        },
        "checks": recorder.checks,
        "failure_counts": {
            "critical": critical_failures,
            "high": high_failures,
            "medium_or_low": lower_failures,
        },
        "limitations": [
            "Completeness is limited to containers visible to the authenticated Discord account at inventory time.",
            "Messages deleted before collection and inaccessible private channels cannot be independently recovered.",
            "An edited flag without edited_at or ordered capture timestamps cannot reconstruct edit history.",
            "Discord post time is not assumed to equal the market setup time discussed in a message.",
            "External links appearing inside Discord messages are retained as text but are not external evidence.",
            "Terminal unavailable Discord attachments remain explicit media gaps; terminal failed rows are degraded and block literal release.",
            "Chart-dependent claims remain unresolved unless a complete/partial extraction with a verified local artifact and exact provenance exists.",
            "All 16 nonempty channels require message-complete capture; supplemental targeted artifacts never satisfy or weaken that requirement.",
        ],
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_segments = Path(__file__).resolve().parents[1] / "raw" / "channel_segments"
    default_premium_segments = (
        Path(__file__).resolve().parents[1] / "raw" / "channel_segments_v2_5"
    )
    default_relevance_segments = (
        Path(__file__).resolve().parents[1] / "raw" / "relevance_segments"
    )
    default_audit_segments = (
        Path(__file__).resolve().parents[1] / "raw" / "relevance_audit_segments"
    )
    parser.add_argument(
        "--segments",
        type=Path,
        action="append",
        help=(
            "Segment file/directory; repeatable. Defaults to the canonical "
            f"capture paths: {default_segments} and {default_premium_segments}"
        ),
    )
    parser.add_argument(
        "--relevance-segments",
        type=Path,
        action="append",
        help="Targeted relevance-query segment file/directory; repeatable.",
    )
    parser.add_argument(
        "--audit-segments",
        type=Path,
        action="append",
        help="Residual census segment file/directory; repeatable.",
    )
    parser.add_argument("--inventory", type=Path, help="Exact channel/thread inventory JSON.")
    parser.add_argument(
        "--relevance-plan",
        type=Path,
        help="Validated relevance_collection_plan.json enabling policy-aware release QA.",
    )
    parser.add_argument(
        "--orchestrator-progress-manifest",
        type=Path,
        help=(
            "Read-only orchestrator progress manifest, optionally augmented with "
            "provenance-backed release_evidence for count, review, reply, and attachment gates."
        ),
    )
    parser.add_argument(
        "--drift-audit",
        type=Path,
        help=(
            "Write-once audit_collection_drift.py --mode final report. A final QA "
            "pass requires PASS with zero structural, unresolved, and orphan counts."
        ),
    )
    parser.add_argument(
        "--attachment-manifest",
        type=Path,
        help=(
            "Terminal Discord attachment archive manifest. Required when the selected "
            "corpus contains owned attachments."
        ),
    )
    parser.add_argument(
        "--attachment-archive-root",
        type=Path,
        help="Root containing package-relative downloaded attachment/extraction files.",
    )
    parser.add_argument("--database", type=Path, help="Release SQLite database to validate read-only.")
    parser.add_argument("--preservation-manifest", type=Path, help="Immutable existing-artifact baseline to check before and after.")
    parser.add_argument(
        "--source-hash-manifest",
        type=Path,
        help="Write-once hash manifest covering exactly the selected canonical segment files.",
    )
    parser.add_argument("--output", type=Path, required=True, help="Write-once JSON validation report.")
    parser.add_argument("--guild-id", default=DEFAULT_GUILD_ID)
    parser.add_argument("--window-start", default="2026-01-01")
    parser.add_argument("--window-end", default="2026-07-20")
    parser.add_argument("--data-cutoff-utc", help="Exact collection cutoff; defaults to validator start time.")
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="Return success after writing a needs-revision smoke report. Never changes check results.",
    )
    args = parser.parse_args()
    if not args.segments:
        args.segments = [default_segments]
        if default_premium_segments.is_dir():
            args.segments.append(default_premium_segments)
    if args.relevance_plan and not args.relevance_segments and default_relevance_segments.is_dir():
        args.relevance_segments = [default_relevance_segments]
    if args.relevance_plan and not args.audit_segments and default_audit_segments.is_dir():
        args.audit_segments = [default_audit_segments]
    if args.orchestrator_progress_manifest:
        auto_inputs = progress_manifest_segment_inputs(
            args.orchestrator_progress_manifest
        )
        args.segments = [*args.segments, *auto_inputs["channel_capture"]]
        args.relevance_segments = [
            *(args.relevance_segments or []),
            *auto_inputs["relevance_query"],
        ]
        args.audit_segments = [
            *(args.audit_segments or []),
            *auto_inputs["residual_audit"],
        ]
    try:
        report = build_report(args)
        write_exclusive(args.output.resolve(), report)
        summary = {
            "status": report["status"],
            "overall_assessment": report["overall_assessment"],
            "output": str(args.output.resolve()),
            "failure_counts": report["failure_counts"],
            "counts": report["counts"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "passed" or args.allow_failures else 1
    except (
        OSError,
        ValueError,
        ValidationError,
        relevance_release_policy.RelevancePolicyError,
        sqlite3.Error,
    ) as exc:
        print(json.dumps({"status": "validator_error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
