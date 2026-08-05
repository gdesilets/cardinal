from __future__ import annotations

"""Build an append-preserving, server-wide Discord corpus and coverage manifest.

This builder is deliberately isolated from the validated 14-day and three-month
artifacts in the parent directory.  It accepts completed or partial channel
search segment JSON files, retains every captured occurrence, merges globally
unique Discord messages without discarding field conflicts, audits the message
timestamp encoded in every Discord snowflake, and writes an explicit coverage
manifest.

Working builds are always labelled ``partial``.  ``--release`` is the only mode
that may label a corpus complete, and it refuses to write release artifacts
unless every strict completeness gate passes.
"""

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import relevance_release_policy
import discord_attachment_archiver
import authorized_collection_scope
import premium_journals_provenance_contract
import reply_provenance_contract
import timestamp_scope_revalidation


SCHEMA_VERSION = "2.4.0"
ARTIFACT_TYPE_WORKING = "discord_serverwide_corpus_working"
ARTIFACT_TYPE_RELEASE = "discord_serverwide_corpus_release"
DEFAULT_GUILD_ID = "1167376964680691732"
DEFAULT_START_DATE = "2026-01-01"
DEFAULT_END_DATE_INCLUSIVE = "2026-07-20"
DEFAULT_TIMEZONE = "America/Chicago"
DISCORD_EPOCH_MS = 1_420_070_400_000
TIMESTAMP_MISMATCH_THRESHOLD_MS = 1_000
MESSAGE_ID_RE = re.compile(r"\d{15,22}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
SEGMENT_FILENAME_RE = re.compile(
    r"(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})(?:\.partial)?\.json$"
)

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SEGMENT_DIR = SCRIPT_DIR / "raw" / "channel_segments"
DEFAULT_PREMIUM_SEGMENT_DIR = SCRIPT_DIR / "raw" / "channel_segments_v2_5"
DEFAULT_RELEVANCE_SEGMENT_DIR = SCRIPT_DIR / "raw" / "relevance_segments"
DEFAULT_AUDIT_SEGMENT_DIR = SCRIPT_DIR / "raw" / "relevance_audit_segments"
DEFAULT_HISTORICAL_RECONCILIATION_DIR = (
    SCRIPT_DIR / "raw" / "quarantine_collection_errors"
)
DEFAULT_RELEVANCE_PLAN = SCRIPT_DIR / "relevance_collection_plan.json"
DEFAULT_AUTHORIZED_SCOPE = SCRIPT_DIR / "authorized_collection_scope.json"
DEFAULT_SCOPED_CHILD_INVENTORY_RECONCILIATION = (
    SCRIPT_DIR / "working" / "premium_journals_scoped_inventory_reconciliation.json"
)
DEFAULT_ORCHESTRATOR_PROGRESS = SCRIPT_DIR / "working" / "collection_progress_manifest.json"
DEFAULT_WORKING_CORPUS = SCRIPT_DIR / "raw_corpus_working.json"
DEFAULT_WORKING_MANIFEST = SCRIPT_DIR / "coverage_manifest_working.json"
DEFAULT_RELEASE_CORPUS = SCRIPT_DIR / "raw_corpus_release.json"
DEFAULT_RELEASE_MANIFEST = SCRIPT_DIR / "coverage_manifest_release.json"
MIGRATION_QUARANTINE_SIDECAR_NAME = "legacy_premium_journals_v2_quarantine.jsonl"
COMPLETION_EVIDENCE_SIDECAR_SUFFIX = ".completion-evidence.json"
TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX = (
    timestamp_scope_revalidation.TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX
)
HISTORICAL_RECONCILIATION_NOTE_SUFFIXES = (
    ".v2.5-replacement-note.json",
    ".v2.5-reconciliation-note.json",
)
REQUIRED_STABLE_EMPTY_OBSERVATIONS = 3
REQUIRED_STABLE_BOTTOM_OBSERVATIONS = 2

PROTECTED_PARENT_NAMES = {
    "raw_discord_export.json",
    "raw_discord_export_3month.json",
    "discord_trading_research.sqlite",
    "discord_trading_research_3month.sqlite",
    "three_month_coverage_manifest.json",
    "validation_report.json",
    "validation_report_3month.json",
}

EMPTY_VALUES = (None, "", [], {})
LIST_UNION_FIELDS = {
    "attachments",
    "links",
    "image_alt",
    "emoji_alt",
    "mentions",
    "embeds",
    "reactions",
    "forum_tags",
}


class CorpusError(RuntimeError):
    """A corpus input or release-safety failure."""


@dataclass(frozen=True)
class Scope:
    guild_id: str
    start_date: dt.date
    end_date_inclusive: dt.date
    timezone_name: str
    timezone: dt.tzinfo
    local_start: dt.datetime
    local_end_exclusive: dt.datetime
    utc_start: dt.datetime
    utc_end_exclusive: dt.datetime

    @property
    def local_day_count(self) -> int:
        return (self.end_date_inclusive - self.start_date).days + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "guild_id": self.guild_id,
            "start_date_inclusive": self.start_date.isoformat(),
            "end_date_inclusive": self.end_date_inclusive.isoformat(),
            "timezone": self.timezone_name,
            "local_start_inclusive": self.local_start.isoformat(),
            "local_end_exclusive": self.local_end_exclusive.isoformat(),
            "utc_start_inclusive": iso_z(self.utc_start),
            "utc_end_exclusive": iso_z(self.utc_end_exclusive),
            "local_calendar_days": self.local_day_count,
            "date_filter_semantics": (
                "Discord after:/before: date tokens are treated as calendar dates in "
                f"{self.timezone_name}; canonical message creation time is derived from the Discord snowflake."
            ),
        }


def iso_z(value: dt.datetime, *, milliseconds: bool = True) -> str:
    value = value.astimezone(dt.timezone.utc)
    timespec = "milliseconds" if milliseconds else "seconds"
    return value.isoformat(timespec=timespec).replace("+00:00", "Z")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_date(value: Any, label: str) -> dt.date:
    text = str(value or "").strip()
    if not DATE_RE.fullmatch(text):
        raise CorpusError(f"{label} must be YYYY-MM-DD, got {value!r}")
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise CorpusError(f"Invalid {label} {value!r}") from exc


def parse_timestamp(value: Any) -> dt.datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is empty")
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    parsed = dt.datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(dt.timezone.utc)


def first_sunday_on_or_after(value: dt.datetime) -> dt.datetime:
    days_to_go = 6 - value.weekday()
    if days_to_go:
        value += dt.timedelta(days=days_to_go)
    return value


class AmericaChicagoFallback(dt.tzinfo):
    """Dependency-free America/Chicago rules for modern US dates.

    Windows Python installations do not always ship the IANA time-zone
    database.  The requested corpus is in 2026, so the post-2007 US rule
    (second Sunday in March through first Sunday in November) is sufficient and
    independently testable.  ``ZoneInfo`` remains preferred whenever present.
    """

    standard_offset = -dt.timedelta(hours=6)
    daylight_offset = -dt.timedelta(hours=5)
    dst_delta = dt.timedelta(hours=1)

    @staticmethod
    def transition_bounds(year: int) -> tuple[dt.datetime, dt.datetime]:
        start = first_sunday_on_or_after(dt.datetime(year, 3, 8, 2))
        end = first_sunday_on_or_after(dt.datetime(year, 11, 1, 2))
        return start, end

    def tzname(self, value: dt.datetime | None) -> str:
        return "CDT" if value is not None and self.dst(value) else "CST"

    def utcoffset(self, value: dt.datetime | None) -> dt.timedelta:
        return self.standard_offset + self.dst(value)

    def dst(self, value: dt.datetime | None) -> dt.timedelta:
        if value is None:
            return dt.timedelta(0)
        start, end = self.transition_bounds(value.year)
        naive = value.replace(tzinfo=None)
        # Treat the spring gap as daylight time and the repeated fall hour
        # according to PEP 495's fold flag.
        if start <= naive < start + self.dst_delta:
            return dt.timedelta(0) if value.fold else self.dst_delta
        if end - self.dst_delta <= naive < end:
            return self.dst_delta if value.fold else dt.timedelta(0)
        if start + self.dst_delta <= naive < end - self.dst_delta:
            return self.dst_delta
        return dt.timedelta(0)

    def fromutc(self, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is not self:
            raise ValueError("fromutc requires a datetime carrying this timezone")
        start, end = self.transition_bounds(value.year)
        start_utc_standard = start - self.standard_offset
        end_utc_daylight = end - self.daylight_offset
        utc_naive = value.replace(tzinfo=None)
        if utc_naive < start_utc_standard or utc_naive >= end_utc_daylight:
            return (utc_naive + self.standard_offset).replace(tzinfo=self, fold=0)
        return (utc_naive + self.daylight_offset).replace(tzinfo=self, fold=0)


def resolve_timezone(timezone_name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(timezone_name)
    except Exception as exc:
        if timezone_name == "America/Chicago":
            return AmericaChicagoFallback()
        raise CorpusError(f"Unknown timezone {timezone_name!r}: {exc}") from exc


def make_scope(
    guild_id: str,
    start_date: str,
    end_date_inclusive: str,
    timezone_name: str,
) -> Scope:
    start = parse_date(start_date, "start date")
    end = parse_date(end_date_inclusive, "inclusive end date")
    if start > end:
        raise CorpusError("start date must not be after end date")
    zone = resolve_timezone(timezone_name)
    local_start = dt.datetime.combine(start, dt.time.min, tzinfo=zone)
    local_end = dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, tzinfo=zone)
    return Scope(
        guild_id=str(guild_id),
        start_date=start,
        end_date_inclusive=end,
        timezone_name=timezone_name,
        timezone=zone,
        local_start=local_start,
        local_end_exclusive=local_end,
        utc_start=local_start.astimezone(dt.timezone.utc),
        utc_end_exclusive=local_end.astimezone(dt.timezone.utc),
    )


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_path_token(path: Path, provenance_root: Path) -> tuple[str, str]:
    """Return a portable path and a resolution status without leaking absolutes."""

    resolved = path.resolve()
    root = provenance_root.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        token = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
        return f"external/{token}_{resolved.name}", "outside_provenance_root"
    return relative.as_posix(), "within_provenance_root"


def snowflake_datetime(message_id: str) -> dt.datetime:
    if not MESSAGE_ID_RE.fullmatch(message_id):
        raise ValueError("not a Discord snowflake-shaped message ID")
    value = int(message_id)
    milliseconds = (value >> 22) + DISCORD_EPOCH_MS
    return dt.datetime.fromtimestamp(milliseconds / 1000, tz=dt.timezone.utc)


def snowflake_id_for_datetime(value: dt.datetime, increment: int = 0) -> str:
    """Test/helper utility: construct a Discord-shaped snowflake for a UTC time."""

    utc = value.astimezone(dt.timezone.utc)
    milliseconds = int(utc.timestamp() * 1000)
    if milliseconds < DISCORD_EPOCH_MS:
        raise ValueError("timestamp predates the Discord epoch")
    return str(((milliseconds - DISCORD_EPOCH_MS) << 22) | (increment & ((1 << 22) - 1)))


def dates_between(start: dt.date, end: dt.date) -> list[dt.date]:
    if start > end:
        return []
    return [start + dt.timedelta(days=index) for index in range((end - start).days + 1)]


def compress_date_ranges(values: Iterable[dt.date]) -> list[dict[str, Any]]:
    ordered = sorted(set(values))
    if not ordered:
        return []
    output: list[dict[str, Any]] = []
    range_start = ordered[0]
    previous = ordered[0]
    for value in ordered[1:]:
        if value != previous + dt.timedelta(days=1):
            output.append(
                {
                    "start_date": range_start.isoformat(),
                    "end_date": previous.isoformat(),
                    "day_count": (previous - range_start).days + 1,
                }
            )
            range_start = value
        previous = value
    output.append(
        {
            "start_date": range_start.isoformat(),
            "end_date": previous.isoformat(),
            "day_count": (previous - range_start).days + 1,
        }
    )
    return output


def first_nonempty(mapping: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        value = mapping.get(name)
        if value not in EMPTY_VALUES:
            return value
    return None


def exact_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if MESSAGE_ID_RE.fullmatch(text) else None


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CorpusError(f"Could not read {label} {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise CorpusError(f"{label} {path.name} must contain a top-level object")
    return value


def completion_evidence_sidecar_path(segment_path: Path) -> Path:
    return segment_path.with_name(f"{segment_path.stem}{COMPLETION_EVIDENCE_SIDECAR_SUFFIX}")


def valid_evidence_timestamp(value: Any) -> bool:
    try:
        parse_timestamp(value)
    except Exception:
        return False
    return str(value or "").endswith("Z")


def validate_completion_evidence(
    evidence: Any,
    *,
    query: str,
    reported_total: int | None,
    reported_pages: int | None,
) -> list[str]:
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
        if not valid_evidence_timestamp(
            submission.get("submitted_at_utc") or submission.get("observed_at_utc")
        ):
            errors.append("search_submission_timestamp_invalid")
    if reported_total == 0:
        if evidence.get("terminal_state") != "stable_empty":
            errors.append("terminal_state_not_stable_empty")
        if not isinstance(submission, dict) or submission.get("mode") != "fresh" or submission.get(
            "submission_count"
        ) != 1:
            errors.append("stable_empty_requires_one_fresh_submission")
        stable = evidence.get("stable_empty")
        observations = stable.get("observations") if isinstance(stable, dict) else None
        if not isinstance(stable, dict) or stable.get("required_observations") != REQUIRED_STABLE_EMPTY_OBSERVATIONS:
            errors.append("stable_empty_required_count_invalid")
        if not isinstance(observations, list) or len(observations) != REQUIRED_STABLE_EMPTY_OBSERVATIONS:
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
            if not valid_evidence_timestamp(observation.get("observed_at_utc")):
                errors.append("stable_empty_timestamp_invalid")
            if "no results" not in str(observation.get("panel_text") or "").casefold():
                errors.append("stable_empty_panel_text_invalid")
    elif isinstance(reported_total, int) and reported_total > 0:
        if evidence.get("terminal_state") != "stable_bottom":
            errors.append("terminal_state_not_stable_bottom")
        stable = evidence.get("stable_bottom")
        observations = stable.get("observations") if isinstance(stable, dict) else None
        if not isinstance(stable, dict) or stable.get("required_observations") != REQUIRED_STABLE_BOTTOM_OBSERVATIONS:
            errors.append("stable_bottom_required_count_invalid")
        if not isinstance(observations, list) or len(observations) != REQUIRED_STABLE_BOTTOM_OBSERVATIONS:
            errors.append("stable_bottom_observation_count_invalid")
            observations = observations if isinstance(observations, list) else []
        expected_first = ((reported_pages or 1) - 1) * 25 + 1
        expected_visible = reported_total - expected_first + 1
        for index, observation in enumerate(observations, start=1):
            if not isinstance(observation, dict):
                errors.append("stable_bottom_observation_not_object")
                continue
            if observation.get("sequence") != index:
                errors.append("stable_bottom_sequence_invalid")
            if not valid_evidence_timestamp(observation.get("observed_at_utc")):
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
    path: Path, payload: dict[str, Any]
) -> tuple[Any, str, Path | None, list[str]]:
    inline = payload.get("completion_evidence")
    sidecar_path = completion_evidence_sidecar_path(path)
    sidecar_exists = sidecar_path.is_file()
    errors: list[str] = []
    if isinstance(inline, dict):
        if sidecar_exists:
            errors.append("inline_and_sidecar_completion_evidence_ambiguous")
        return inline, "inline", None, errors
    if not sidecar_exists:
        return None, "missing", None, errors
    try:
        sidecar = load_json_object(sidecar_path, "completion evidence sidecar")
    except CorpusError as exc:
        return None, "sidecar_invalid", sidecar_path, [str(exc)]
    if sidecar.get("artifact_type") != "discord_segment_completion_evidence_sidecar":
        errors.append("completion_evidence_sidecar_artifact_type_invalid")
    if sidecar.get("schema_version") != "1.0.0":
        errors.append("completion_evidence_sidecar_schema_invalid")
    if sidecar.get("source_artifact_sha256") != sha256_file(path):
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
    return sidecar.get("completion_evidence"), "sidecar", sidecar_path, errors


def register_source_file(
    registry: dict[str, dict[str, Any]],
    path: Path,
    provenance_root: Path,
    *,
    kind: str,
    exists_override: bool | None = None,
) -> dict[str, Any]:
    exists = path.is_file() if exists_override is None else exists_override
    relative_path, path_status = source_path_token(path, provenance_root)
    size = path.stat().st_size if exists else None
    digest = sha256_file(path) if exists else None
    fingerprint = compact_json(
        {"relative_path": relative_path, "size_bytes": size, "sha256": digest, "kind": kind}
    )
    file_id = sha256_bytes(fingerprint.encode("utf-8"))
    record = registry.setdefault(
        file_id,
        {
            "source_file_id": file_id,
            "relative_path": relative_path,
            "path_resolution": path_status,
            "kind": kind,
            "exists": bool(exists),
            "size_bytes": size,
            "sha256": digest,
        },
    )
    return record


def discover_attachment_candidates_with_trust(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Discover exact-owned media while retaining untrusted ownership gaps as metadata.

    Trusted/analysis-eligible rows remain fail-closed. A row explicitly classified
    as quarantined or noncanonical may retain older attachment metadata whose DOM
    ownership relation cannot be reconstructed; that metadata is disclosed and is
    never fetched. Exact-owned attachments on such rows are still archived.
    """

    discovered: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    owner_by_attachment_id: dict[str, str] = {}
    ineligible_states = {
        "quarantined_only",
        "untrusted_noncanonical_only",
        "conflicting",
    }
    for message in messages:
        message_id = str(message.get("message_id") or "")
        attachments = message.get("attachments") or []
        if not isinstance(attachments, list):
            raise CorpusError(f"Message {message_id} attachments is not an array")
        explicitly_ineligible = bool(
            message.get("eligible_for_accepted_evidence") is False
            and str(message.get("evidence_trust_state") or "") in ineligible_states
        )
        for attachment_index, attachment in enumerate(attachments):
            if not isinstance(attachment, dict):
                if not explicitly_ineligible:
                    raise CorpusError(
                        f"Message {message_id} has a non-object attachment"
                    )
                excluded.append(
                    {
                        "message_id": message_id,
                        "attachment_index": attachment_index,
                        "attachment_id": None,
                        "evidence_trust_state": message.get("evidence_trust_state"),
                        "reason": "analysis_ineligible_nonobject_attachment_metadata",
                    }
                )
                continue
            probe = {
                "artifact_type": ARTIFACT_TYPE_WORKING,
                "messages": [{**message, "attachments": [attachment]}],
            }
            try:
                rows = discord_attachment_archiver.discover_entries(probe)
            except discord_attachment_archiver.AttachmentArchiveError as exc:
                if not explicitly_ineligible:
                    raise CorpusError(f"Attachment discovery failed: {exc}") from exc
                attachment_id = str(
                    attachment.get("attachment_id") or attachment.get("id") or ""
                ) or None
                exclusion_reason = (
                    "analysis_ineligible_attachment_ownership_unresolved"
                )
                attachment.update(
                    {
                        "archive_required": False,
                        "archive_exclusion_reason": exclusion_reason,
                        "capture_status": "metadata_only_untrusted_ownership_unresolved",
                        "chart_claim_eligible": False,
                    }
                )
                excluded.append(
                    {
                        "message_id": message_id,
                        "attachment_index": attachment_index,
                        "attachment_id": attachment_id,
                        "evidence_trust_state": message.get("evidence_trust_state"),
                        "reason": exclusion_reason,
                        "detail": str(exc),
                    }
                )
                continue
            for row in rows:
                attachment_id = str(row["attachment_id"])
                previous_owner = owner_by_attachment_id.get(attachment_id)
                if previous_owner and previous_owner != message_id:
                    raise CorpusError(
                        f"Attachment {attachment_id} is owned by multiple messages"
                    )
                owner_by_attachment_id[attachment_id] = message_id
                discovered.append(row)
    return (
        sorted(discovered, key=lambda row: (row["message_id"], row["attachment_id"])),
        excluded,
    )


def clear_unverified_attachment_capture_state(
    messages: list[dict[str, Any]],
) -> None:
    """Remove every byte/extraction claim not supplied by the verified archive.

    Raw Discord rows may legitimately retain visible metadata for copied or embedded
    media.  They must never be able to smuggle a local path, digest, capture attempt,
    or extraction into a corpus merely because no owned-attachment manifest row was
    available to overwrite the raw fields.
    """

    for message in messages:
        attachments = message.get("attachments")
        if not isinstance(attachments, list):
            continue
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            attachment.update(
                {
                    "local_package_path": None,
                    "capture_status": "metadata_only",
                    "download_status": "metadata_only",
                    "capture_terminal": False,
                    "capture_attempt_count": 0,
                    "capture_attempts": [],
                    "capture_failure_code": None,
                    "capture_failure_detail": None,
                    "content_sha256": None,
                    "extraction_status": "not_attempted",
                    "extraction_artifacts": [],
                    "chart_claim_eligible": False,
                    "archive_manifest_source_file_id": None,
                }
            )


def apply_attachment_archive_manifest(
    *,
    messages: list[dict[str, Any]],
    manifest_path: Path | None,
    archive_root: Path | None,
    provenance_root: Path,
    source_registry: dict[str, dict[str, Any]],
    authorized_message_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Annotate owned attachments with a verified durable-archive disposition.

    A working corpus may omit the archive while collection is still underway.  A
    release corpus with any owned attachments cannot: ``make_release_gates`` uses
    this summary as a strict terminal-coverage gate.  External message links are
    never considered attachment candidates.
    """

    discovered, excluded_untrusted_ownership = (
        discover_attachment_candidates_with_trust(messages)
    )
    expected = {
        (str(row["message_id"]), str(row["attachment_id"])): row
        for row in discovered
    }
    # The verified manifest is the sole authority for local bytes and extraction
    # artifacts.  Start from a metadata-only state, then overlay exact owned rows.
    clear_unverified_attachment_capture_state(messages)
    if manifest_path is None:
        return {
            "provided": False,
            "manifest_source_file_id": None,
            "expected_owned_attachment_count": len(expected),
            "excluded_untrusted_ownership_metadata_count": len(
                excluded_untrusted_ownership
            ),
            "excluded_untrusted_ownership_metadata": excluded_untrusted_ownership,
            "manifest_attachment_count": 0,
            "entry_set_parity": len(expected) == 0,
            "status": "not_required" if not expected else "missing",
            "counts": {
                "total": len(expected),
                "pending": len(expected),
                "downloaded": 0,
                "unavailable": 0,
                "failed": 0,
                "terminal": 0,
            },
            "verification": {
                "status": "passed" if not expected else "pending",
                "problem_count": 0 if not expected else len(expected),
                "problems": [] if not expected else [{"reason": "attachment_manifest_missing"}],
            },
            "release_gate": {
                "gate": "discord_attachment_terminal_coverage",
                "passed": len(expected) == 0,
                "terminal_coverage_complete": len(expected) == 0,
                "literal_release_complete": len(expected) == 0,
                "byte_complete": len(expected) == 0,
                "all_available_bytes_required": True,
                "terminal_unavailable_allowed": True,
                "terminal_failed_release_allowed": False,
            },
            "entries": [],
            "authorized_scope_filtering": {
                "enabled": authorized_message_ids is not None,
                "excluded_owned_entry_count": 0,
                "excluded_non_owned_entry_count": 0,
                "source_manifest_bytes_mutated": False,
            },
            "policy": discord_attachment_archiver.manifest_policy(),
        }

    resolved_manifest = manifest_path.resolve()
    source_file = register_source_file(
        source_registry,
        resolved_manifest,
        provenance_root,
        kind="discord_attachment_archive_manifest",
    )
    try:
        archive_manifest = discord_attachment_archiver.load_json_object(
            resolved_manifest, label="attachment archive manifest"
        )
        discord_attachment_archiver.validate_manifest_structure(
            archive_manifest, require_terminal=False
        )
    except discord_attachment_archiver.AttachmentArchiveError as exc:
        raise CorpusError(f"Attachment archive manifest is invalid: {exc}") from exc
    attachment_scope_filtering: dict[str, Any] | None = None
    if authorized_message_ids is not None:
        all_entries = list(archive_manifest.get("entries") or [])
        all_non_owned = list(archive_manifest.get("non_owned_attachments") or [])
        excluded_entries = [
            row
            for row in all_entries
            if str(row.get("message_id") or "") not in authorized_message_ids
        ]
        excluded_non_owned = [
            row
            for row in all_non_owned
            if str(row.get("message_id") or "") not in authorized_message_ids
        ]
        archive_manifest = copy.deepcopy(archive_manifest)
        archive_manifest["entries"] = [
            row
            for row in all_entries
            if str(row.get("message_id") or "") in authorized_message_ids
        ]
        archive_manifest["non_owned_attachments"] = [
            row
            for row in all_non_owned
            if str(row.get("message_id") or "") in authorized_message_ids
        ]
        discord_attachment_archiver.refresh_manifest(archive_manifest)
        attachment_scope_filtering = {
            "enabled": True,
            "excluded_owned_entry_count": len(excluded_entries),
            "excluded_non_owned_entry_count": len(excluded_non_owned),
            "excluded_entry_key_set_sha256": sha256_bytes(
                compact_json(
                    sorted(
                        [
                            str(row.get("message_id") or ""),
                            str(row.get("attachment_id") or ""),
                        ]
                        for row in excluded_entries
                    )
                ).encode("utf-8")
            ),
            "source_manifest_bytes_mutated": False,
        }
    manifest_entries = {
        (str(row.get("message_id") or ""), str(row.get("attachment_id") or "")): row
        for row in archive_manifest["entries"]
    }
    missing = sorted(set(expected) - set(manifest_entries))
    extra = sorted(set(manifest_entries) - set(expected))
    parity = not missing and not extra
    downloaded = [
        row for row in archive_manifest["entries"] if row.get("capture_status") == "downloaded"
    ]
    if downloaded and archive_root is None:
        raise CorpusError(
            "--attachment-archive-root is required to verify downloaded attachment bytes"
        )
    try:
        verification = discord_attachment_archiver.verify_archive(
            archive_manifest,
            (archive_root or resolved_manifest.parent).resolve(),
            require_terminal=False,
        )
    except discord_attachment_archiver.AttachmentArchiveError as exc:
        raise CorpusError(f"Attachment archive verification failed: {exc}") from exc

    verified_extraction_ids = set(verification.get("verified_extraction_ids") or [])

    def annotated_extractions(archived: dict[str, Any]) -> list[dict[str, Any]]:
        annotated: list[dict[str, Any]] = []
        for artifact in archived.get("extraction_artifacts") or []:
            item = copy.deepcopy(artifact)
            item["local_artifact_verified"] = bool(
                item.get("status")
                in discord_attachment_archiver.SUCCESSFUL_EXTRACTION_STATES
                and item.get("extraction_id") in verified_extraction_ids
            )
            annotated.append(item)
        return annotated

    for message in messages:
        message_id = str(message.get("message_id") or "")
        attachments = message.get("attachments")
        if not isinstance(attachments, list):
            continue
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            attachment_id = str(attachment.get("attachment_id") or attachment.get("id") or "")
            archived = manifest_entries.get((message_id, attachment_id))
            if archived is None:
                continue
            attachment.update(
                {
                    "archive_request_id": archived.get("request_id"),
                    "local_package_path": archived.get("local_package_path"),
                    "capture_status": archived.get("capture_status"),
                    "download_status": archived.get("capture_status"),
                    "capture_terminal": bool(archived.get("terminal")),
                    "capture_attempt_count": archived.get("attempt_count"),
                    "capture_attempts": copy.deepcopy(archived.get("attempts") or []),
                    "capture_failure_code": archived.get("failure_code"),
                    "capture_failure_detail": archived.get("failure_detail"),
                    "content_sha256": archived.get("content_sha256"),
                    "byte_size": archived.get("byte_size"),
                    "mime_type": archived.get("mime_type") or attachment.get("mime_type"),
                    "extraction_status": archived.get("extraction_status"),
                    "extraction_artifacts": annotated_extractions(archived),
                    "chart_claim_eligible": False,
                    "archive_manifest_source_file_id": source_file["source_file_id"],
                }
            )

    terminal_gate = bool(
        parity
        and verification.get("status") == "passed"
        and archive_manifest.get("status") == "complete"
        and (archive_manifest.get("release_gate") or {}).get("passed") is True
    )
    return {
        "provided": True,
        "manifest_source_file_id": source_file["source_file_id"],
        "manifest_sha256": source_file["sha256"],
        "expected_owned_attachment_count": len(expected),
        "excluded_untrusted_ownership_metadata_count": len(
            excluded_untrusted_ownership
        ),
        "excluded_untrusted_ownership_metadata": excluded_untrusted_ownership,
        "manifest_attachment_count": len(manifest_entries),
        "entry_set_parity": parity,
        "missing_entries": [
            {"message_id": message_id, "attachment_id": attachment_id}
            for message_id, attachment_id in missing
        ],
        "extra_entries": [
            {"message_id": message_id, "attachment_id": attachment_id}
            for message_id, attachment_id in extra
        ],
        "status": archive_manifest.get("status"),
        "counts": copy.deepcopy(archive_manifest.get("counts") or {}),
        "verification": verification,
        "release_gate": {
            **copy.deepcopy(archive_manifest.get("release_gate") or {}),
            "gate": "discord_attachment_terminal_coverage",
            "passed": terminal_gate,
            "entry_set_parity": parity,
            "files_reverified": verification.get("status") == "passed",
        },
        "entries": [
            {
                **copy.deepcopy(row),
                "extraction_artifacts": annotated_extractions(row),
            }
            for row in archive_manifest["entries"]
        ],
        "authorized_scope_filtering": attachment_scope_filtering
        or {"enabled": False},
        "policy": copy.deepcopy(archive_manifest.get("policy") or {}),
    }


def infer_segment_dates(path: Path, segment: dict[str, Any]) -> tuple[str | None, str | None]:
    start = first_nonempty(segment, ("start", "start_date", "segment_start", "segment_start_date"))
    end = first_nonempty(segment, ("end", "end_date", "segment_end", "segment_end_date"))
    if start and end:
        return str(start), str(end)
    match = SEGMENT_FILENAME_RE.search(path.name)
    if match:
        return match.group("start"), match.group("end")
    return None, None


def infer_segment_container(segment: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[str | None, str]:
    declared = exact_id(
        first_nonempty(
            segment,
            ("container_id", "channel_id", "thread_id", "target_channel_id", "query_container_id"),
        )
    )
    if declared:
        return declared, "segment_metadata"
    candidates: set[str] = set()
    for row in rows:
        candidate = exact_id(
            first_nonempty(
                row,
                (
                    "query_container_id",
                    "channel_id",
                    "thread_id",
                    "collection_channel_id",
                    "inferred_thread_channel_id",
                ),
            )
        )
        if candidate:
            candidates.add(candidate)
    if len(candidates) == 1:
        return next(iter(candidates)), "single_message_container_inference"
    return None, "missing_or_ambiguous"


def normalize_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_segment_payload(
    path: Path,
    payload: dict[str, Any],
    scope: Scope,
    *,
    input_role: str = "channel_capture",
    artifact_root: Path | None = None,
    source_artifact_sha256: str | None = None,
    timestamp_revalidation: (
        timestamp_scope_revalidation.SegmentTimestampScopeRevalidation | None
    ) = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    errors: list[str] = []
    warnings: list[str] = []
    segment = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
    requested_container = (
        payload.get("requested_container")
        if isinstance(payload.get("requested_container"), dict)
        else {}
    )
    container_context = {**requested_container, **segment}
    raw_rows = payload.get("messages")
    if not isinstance(raw_rows, list):
        raw_rows = []
        errors.append("messages_not_array")
    rows = [row for row in raw_rows if isinstance(row, dict)]
    if len(rows) != len(raw_rows):
        errors.append("messages_contains_non_object_rows")

    start_text, end_text = infer_segment_dates(path, segment)
    try:
        start = parse_date(start_text, "segment start")
        end = parse_date(end_text, "segment end")
        if start > end:
            errors.append("segment_date_order_invalid")
    except CorpusError:
        start = None
        end = None
        errors.append("segment_dates_missing_or_invalid")

    container_id, container_id_source = infer_segment_container(container_context, rows)
    if not container_id:
        errors.append("exact_query_container_id_missing")
    payload_guild_id = exact_id(payload.get("guild_id"))
    if payload_guild_id and payload_guild_id != scope.guild_id:
        errors.append("segment_guild_id_mismatch")
    query = str(first_nonempty(segment, ("query", "search_query")) or payload.get("query") or "")
    if not query:
        errors.append("query_missing")

    message_ids = [str(row.get("message_id") or "").strip() for row in rows]
    valid_ids = [message_id for message_id in message_ids if MESSAGE_ID_RE.fullmatch(message_id)]
    if len(valid_ids) != len(message_ids):
        errors.append("one_or_more_message_ids_invalid")
    if len(set(message_ids)) != len(message_ids):
        errors.append("duplicate_message_ids_within_segment")

    reported_total = normalize_int(payload.get("reported_total"))
    reported_pages = normalize_int(payload.get("reported_pages"))
    pages_captured = normalize_int(payload.get("pages_captured"))
    captured_declared = normalize_int(payload.get("captured_rows"))
    unique_declared = normalize_int(payload.get("unique_message_ids"))
    declared_gaps = payload.get("gap_indices") if isinstance(payload.get("gap_indices"), list) else []
    declared_complete = payload.get("complete") is True and not path.name.endswith(".partial.json")
    collector_version = str(
        first_nonempty(segment, ("collector_version",)) or payload.get("collector_version") or ""
    ).strip()
    (
        completion_evidence,
        completion_evidence_source,
        completion_evidence_sidecar,
        completion_binding_errors,
    ) = resolve_completion_evidence(path, payload)
    requires_completion_evidence = declared_complete
    completion_validation_errors = validate_completion_evidence(
        completion_evidence,
        query=query,
        reported_total=reported_total,
        reported_pages=reported_pages,
    )
    if requires_completion_evidence:
        errors.extend(completion_binding_errors)
        errors.extend(completion_validation_errors)
        if completion_evidence_source == "missing":
            errors.append("completion_evidence_missing_recapture_or_sidecar_required")
    elif completion_binding_errors or (
        completion_evidence_source != "missing" and completion_validation_errors
    ):
        warnings.extend(completion_binding_errors)
        if completion_evidence_source != "missing":
            warnings.extend(completion_validation_errors)

    if reported_total is None:
        errors.append("reported_total_missing_or_invalid")
    elif reported_total != len(rows):
        errors.append("reported_total_mismatch")
    if captured_declared is not None and captured_declared != len(rows):
        errors.append("captured_rows_mismatch")
    if unique_declared is not None and unique_declared != len(set(message_ids)):
        errors.append("unique_message_ids_mismatch")
    if declared_gaps:
        errors.append("declared_gap_indices_nonempty")

    result_indices = [normalize_int(row.get("result_index")) for row in rows]
    computed_missing_indices: list[int] = []
    if reported_total is not None and reported_total > 0:
        if any(value is None or value < 1 for value in result_indices):
            errors.append("result_indices_missing_or_invalid")
        else:
            index_set = {int(value) for value in result_indices if value is not None}
            computed_missing_indices = [
                index for index in range(1, reported_total + 1) if index not in index_set
            ]
            if computed_missing_indices:
                errors.append("computed_result_index_gaps")
        expected_pages = math.ceil(reported_total / 25)
        if reported_pages is not None and reported_pages != expected_pages:
            warnings.append("reported_pages_differs_from_25_row_page_expectation")
        if reported_pages is None or pages_captured is None or reported_pages != pages_captured:
            errors.append("page_capture_mismatch")
    elif reported_total == 0:
        if rows:
            errors.append("zero_reported_total_has_rows")
        if reported_pages not in (None, 0) or pages_captured not in (None, 0):
            errors.append("verified_empty_page_counts_not_zero")

    row_timestamp_issues: list[dict[str, Any]] = []
    if start is not None and end is not None:
        for row_index, row in enumerate(rows, start=1):
            message_id = str(row.get("message_id") or "")
            try:
                snowflake = snowflake_datetime(message_id)
            except ValueError:
                continue
            local_date = snowflake.astimezone(scope.timezone).date()
            if not (start <= local_date <= end):
                row_timestamp_issues.append(
                    {
                        "row_index": row_index,
                        "message_id": message_id,
                        "snowflake_local_date": local_date.isoformat(),
                    }
                )
        if row_timestamp_issues:
            errors.append("snowflake_dates_outside_segment")
        if start < scope.start_date or end > scope.end_date_inclusive:
            errors.append("segment_extends_outside_requested_window")

    if timestamp_revalidation is None:
        inferred_artifact_root = artifact_root
        if inferred_artifact_root is None:
            inferred_artifact_root = (
                path.parents[2]
                if path.parent.name in {"channel_segments", "channel_segments_v2_5"}
                and path.parent.parent.name == "raw"
                else path.parent
            )
        timestamp_revalidation = (
            timestamp_scope_revalidation.load_adjacent_timestamp_scope_revalidation(
                path,
                payload,
                source_artifact_sha256=(
                    source_artifact_sha256 or sha256_file(path)
                ),
                artifact_root=inferred_artifact_root,
            )
        )
    timestamp_scope_integrity = (
        timestamp_scope_revalidation.audit_segment_timestamp_scopes(
            rows, timestamp_revalidation
        )
    )
    if not timestamp_scope_integrity["passed"]:
        errors.append("timestamp_scope_integrity_failed")
    expected_executed_command_ids: list[str] = []
    if container_id == "1273692573898113076":
        if start == dt.date(2026, 6, 30) and end == dt.date(2026, 7, 6):
            expected_executed_command_ids = [
                reply_provenance_contract.EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID
            ]
    executed_command_reply_provenance_integrity = (
        reply_provenance_contract.audit_executed_command_contexts(
            rows,
            expected_message_ids=expected_executed_command_ids,
        )
    )
    if not executed_command_reply_provenance_integrity["passed"]:
        errors.append("executed_command_reply_provenance_integrity_failed")

    if not declared_complete:
        errors.append("source_not_declared_complete")
    computed_complete = not errors
    status = (
        "verified_empty"
        if computed_complete and reported_total == 0
        else "complete"
        if computed_complete
        else "partial"
        if not declared_complete or path.name.endswith(".partial.json")
        else "failed_validation"
    )
    normalized = {
        "segment_id": "",  # populated after the source-file hash is known
        "source_file_id": "",
        "source_file_relative_path": "",
        "input_role": input_role,
        "query_container_id": container_id,
        "query_container_id_source": container_id_source,
        "query_container_name": str(
            first_nonempty(
                container_context,
                ("container_name", "channel_name", "thread_name", "name"),
            )
            or ""
        ),
        "query_container_kind": str(
            first_nonempty(
                container_context,
                ("container_kind", "channel_kind", "channel_type", "kind", "type"),
            )
            or ""
        ),
        "query": query,
        "start_date": start.isoformat() if start else start_text,
        "end_date": end.isoformat() if end else end_text,
        "timezone": str(segment.get("timezone") or payload.get("timezone") or scope.timezone_name),
        "capture_started_at_utc": first_nonempty(
            segment, ("capture_started_at_utc", "started_at_utc")
        )
        or payload.get("capture_started_at_utc")
        or payload.get("collection_started_at_utc"),
        "capture_completed_at_utc": first_nonempty(
            segment, ("capture_completed_at_utc", "completed_at_utc", "collected_at_utc")
        )
        or payload.get("capture_completed_at_utc")
        or payload.get("collected_at_utc")
        or payload.get("captured_at_utc"),
        "collector_version": collector_version or None,
        "completion_evidence_required": requires_completion_evidence,
        "completion_evidence_source": completion_evidence_source,
        "completion_evidence_sidecar_filename": (
            completion_evidence_sidecar.name if completion_evidence_sidecar else None
        ),
        "completion_evidence_valid": not bool(
            completion_binding_errors or completion_validation_errors
        ),
        "completion_evidence_validation_errors": sorted(
            set(completion_binding_errors + completion_validation_errors)
        ),
        "completion_evidence": copy.deepcopy(completion_evidence),
        "declared_complete": declared_complete,
        "computed_complete": computed_complete,
        "status": status,
        "reported_total": reported_total,
        "reported_pages": reported_pages,
        "pages_captured": pages_captured,
        "captured_rows_declared": captured_declared,
        "captured_rows_computed": len(rows),
        "unique_message_ids_declared": unique_declared,
        "unique_message_ids_computed": len(set(message_ids)),
        "invalid_message_id_count": len(message_ids) - len(valid_ids),
        "duplicate_message_id_count": len(message_ids) - len(set(message_ids)),
        "declared_gap_indices": declared_gaps,
        "computed_gap_indices": computed_missing_indices,
        "validation_errors": errors,
        "validation_warnings": warnings,
        "snowflake_date_issues": row_timestamp_issues,
        "timestamp_scope_integrity": timestamp_scope_integrity,
        "executed_command_reply_provenance_integrity": (
            executed_command_reply_provenance_integrity
        ),
    }
    return normalized, rows


def segment_id_for(source_file_id: str, record: dict[str, Any]) -> str:
    fingerprint = {
        "source_file_id": source_file_id,
        "query_container_id": record.get("query_container_id"),
        "query": record.get("query"),
        "start_date": record.get("start_date"),
        "end_date": record.get("end_date"),
    }
    return sha256_bytes(compact_json(fingerprint).encode("utf-8"))


EXACT_THREAD_ID_SOURCES = {
    "forum_group_header_data_list_item_id",
    "forum_group_header_navigation_exact",
    "owned_reply_permalink",
}
EXACT_THREAD_PERMALINK_STATUSES = {
    "thread_id_from_forum_group_header",
    "thread_id_from_forum_group_header_navigation",
    "thread_id_from_owned_reply_permalink",
}


def validated_forum_navigation_thread_id(row: dict[str, Any]) -> str | None:
    evidence = row.get("forum_group_navigation_evidence")
    if not isinstance(evidence, dict):
        return None
    message_id = exact_id(row.get("message_id"))
    raw_message_ids = row.get("forum_group_message_ids")
    if (
        row.get("forum_group_membership_exact") is not True
        or not isinstance(raw_message_ids, list)
        or not raw_message_ids
    ):
        return None
    message_ids = [exact_id(value) for value in raw_message_ids]
    if any(value is None for value in message_ids):
        return None
    normalized_message_ids = sorted(str(value) for value in message_ids if value)
    if len(set(normalized_message_ids)) != len(normalized_message_ids):
        return None
    if not message_id or message_id not in normalized_message_ids:
        return None
    query = str(row.get("search_query") or "").strip()
    try:
        page_number = int(row.get("page_number"))
    except (TypeError, ValueError):
        return None
    if not query or page_number < 1:
        return None
    fingerprint = json.dumps(
        {
            "query": query,
            "page_number": page_number,
            "group_message_ids": normalized_message_ids,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    evidence_key = f"forum-group-navigation:{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()}"
    if (
        row.get("forum_group_membership_key") != evidence_key
        or row.get("forum_group_navigation_evidence_key") != evidence_key
        or evidence.get("evidence_key") != evidence_key
        or evidence.get("schema_version") != "1.0.0"
        or evidence.get("evidence_type") != "forum_group_header_navigation_exact"
        or evidence.get("query") != query
        or evidence.get("page_number") != page_number
        or sorted(str(value) for value in evidence.get("group_message_ids") or [])
        != normalized_message_ids
        or evidence.get("navigation_trigger")
        != "unique_direct_child_role_button_click"
        or evidence.get("header_match_count") != 1
        or evidence.get("header_button_match_count") != 1
        or evidence.get("authenticated") is not True
        or evidence.get("source_scope") != "discord_only"
        or evidence.get("outside_sources_used") is not False
        or evidence.get("destination_verified") is not True
        or evidence.get("return_state_verified") is not True
        or not valid_evidence_timestamp(evidence.get("observed_at_utc"))
    ):
        return None
    destination = urlparse(str(evidence.get("destination_url") or ""))
    path_parts = [part for part in destination.path.split("/") if part]
    if (
        destination.scheme != "https"
        or destination.hostname not in {"discord.com", "www.discord.com"}
        or destination.params
        or destination.query
        or destination.fragment
        or len(path_parts) != 3
        or path_parts[0] != "channels"
    ):
        return None
    destination_guild_id = exact_id(path_parts[1])
    thread_id = exact_id(path_parts[2])
    parent_forum_id = exact_id(evidence.get("parent_forum_channel_id"))
    collection_channel_id = exact_id(row.get("collection_channel_id"))
    if (
        destination_guild_id != DEFAULT_GUILD_ID
        or evidence.get("guild_id") != DEFAULT_GUILD_ID
        or evidence.get("destination_guild_id") != DEFAULT_GUILD_ID
        or evidence.get("thread_channel_id") != thread_id
        or not thread_id
        or not parent_forum_id
        or parent_forum_id != collection_channel_id
        or thread_id == parent_forum_id
    ):
        return None
    return thread_id


def exact_row_thread_id(row: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return only row-owned forum-thread evidence, never CDN inference."""

    direct = exact_id(first_nonempty(row, ("message_channel_id", "thread_id")))
    if direct:
        return direct, "captured_row_exact_thread_id"
    source = str(row.get("thread_channel_id_source") or "").strip()
    permalink_status = str(row.get("exact_permalink_status") or "").strip()
    evidence = row.get("forum_group_navigation_evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    if (
        source
        == premium_journals_provenance_contract.OWNED_REPLY_ANCHOR_EVIDENCE_TYPE
        or evidence.get("evidence_type")
        == premium_journals_provenance_contract.OWNED_REPLY_ANCHOR_EVIDENCE_TYPE
    ):
        # This method is group- and page-scoped.  A single row/permalink is
        # never sufficient; authoritative Premium ingestion supplies the
        # whole-artifact byte-bound override only after the shared audit.
        return None, None
    if (
        source == "forum_group_header_navigation_exact"
        or permalink_status == "thread_id_from_forum_group_header_navigation"
    ):
        navigation_thread_id = validated_forum_navigation_thread_id(row)
        if navigation_thread_id:
            return navigation_thread_id, "forum_group_header_navigation_exact"
        return None, None
    candidate = exact_id(
        first_nonempty(row, ("thread_channel_id", "inferred_thread_channel_id"))
    )
    if candidate and (
        row.get("thread_channel_id_exact") is True
        or row.get("thread_channel_id_exact") == 1
        or source in EXACT_THREAD_ID_SOURCES
    ):
        return candidate, source or "captured_row_exact_thread_id"
    permalink = str(row.get("exact_permalink") or "").strip()
    if permalink_status in EXACT_THREAD_PERMALINK_STATUSES:
        match = re.search(r"/channels/\d{15,22}/(\d{15,22})/\d{15,22}(?:[/?#]|$)", permalink)
        if match:
            return match.group(1), permalink_status
    return None, None


def resolve_row_container(
    row: dict[str, Any],
    query_container_id: str | None,
    *,
    trusted_forum_thread_id: str | None = None,
) -> tuple[str | None, str | None, list[str]]:
    # ``collection_channel_id`` identifies the channel searched by the
    # collector.  For a forum search that is the parent forum, not necessarily
    # the container that owns the individual message.  Prefer an exact thread
    # ID recovered from the row before falling back to the searched channel.
    collection_container_id = exact_id(row.get("collection_channel_id")) or query_container_id
    collection_kind = str(row.get("collection_channel_kind") or "").casefold()
    is_forum_collection = "forum" in collection_kind
    exact_thread_id = exact_id(trusted_forum_thread_id)
    if not exact_thread_id:
        exact_thread_id, _thread_source = exact_row_thread_id(row)
    explicit_channel_id = exact_id(row.get("channel_id"))
    inferred_channel_id = exact_id(row.get("inferred_thread_channel_id"))
    if explicit_channel_id and explicit_channel_id == inferred_channel_id and not exact_thread_id:
        explicit_channel_id = None
    if is_forum_collection:
        message_container_id = exact_thread_id or collection_container_id
    elif collection_container_id:
        # Text, voice, stage, and announcement searches already have an exact
        # inventoried collection ID.  A pasted/embedded attachment URL cannot
        # move an individual result into another channel.
        message_container_id = collection_container_id
    else:
        message_container_id = exact_thread_id or explicit_channel_id
    parent_id = exact_id(
        first_nonempty(
            row,
            (
                "parent_channel_id",
                "group_header_parent_forum_channel_id",
                "parent_id",
                "forum_channel_id",
                "thread_parent_id",
            ),
        )
    )
    if (
        not parent_id
        and exact_thread_id
        and query_container_id
        and exact_thread_id != query_container_id
        and collection_container_id == query_container_id
    ):
        # The collector searched the parent forum and recovered the owning
        # thread ID from exact row-owned group-header or reply evidence.
        parent_id = query_container_id
    issues: list[str] = []
    if not message_container_id and query_container_id:
        message_container_id = query_container_id
        issues.append("message_container_inherited_from_query_segment")
    if not message_container_id:
        issues.append("exact_message_container_id_missing")
    if is_forum_collection and not exact_thread_id and collection_container_id:
        issues.append("exact_forum_thread_id_unresolved_inherited_from_collection")
    if (
        message_container_id
        and query_container_id
        and message_container_id != query_container_id
        and parent_id != query_container_id
    ):
        issues.append("message_container_does_not_match_query_container_or_parent")
    return message_container_id, parent_id, issues


def occurrence_id_for(
    *,
    source_file_id: str,
    source_kind: str,
    message_id: str,
    row_index: int,
    page_number: Any,
    result_index: Any,
    query: str,
    collection: str,
) -> str:
    fingerprint = {
        "source_file_id": source_file_id,
        "source_kind": source_kind,
        "message_id": message_id,
        "row_index": row_index,
        "page_number": normalize_int(page_number),
        "result_index": normalize_int(result_index),
        "query": query,
        "collection": collection,
    }
    return sha256_bytes(compact_json(fingerprint).encode("utf-8"))


def migration_occurrence_id(row: dict[str, Any]) -> str | None:
    migration = row.get("_migration_occurrence")
    if not isinstance(migration, dict):
        return None
    value = str(migration.get("occurrence_id") or "").strip()
    return value or None


def normalize_quarantine_reasons(value: Any) -> list[str]:
    candidates = value if isinstance(value, list) else [value]
    return sorted(
        {
            str(reason).strip()
            for reason in candidates
            if isinstance(reason, (str, int, float)) and str(reason).strip()
        }
    )


def discover_migration_quarantine_sidecars(
    segment_dirs: Sequence[Path], explicit_paths: Sequence[Path]
) -> list[Path]:
    candidates = {Path(path).resolve() for path in explicit_paths}
    for directory in segment_dirs:
        resolved = Path(directory).resolve()
        for candidate in (
            resolved / MIGRATION_QUARANTINE_SIDECAR_NAME,
            resolved.parent / MIGRATION_QUARANTINE_SIDECAR_NAME,
        ):
            if candidate.is_file():
                candidates.add(candidate.resolve())
    return sorted(candidates, key=lambda path: path.as_posix().casefold())


def ingest_migration_quarantine_sidecars(
    paths: Sequence[Path],
    provenance_root: Path,
    source_registry: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    invalid_records: list[dict[str, Any]] = []
    source_file_ids: list[str] = []
    record_count = 0
    for path in paths:
        source_file = register_source_file(
            source_registry,
            path,
            provenance_root,
            kind="migration_quarantine_sidecar",
        )
        source_file_ids.append(source_file["source_file_id"])
        try:
            if path.suffix.casefold() == ".jsonl":
                rows = []
                for line_number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), start=1
                ):
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as exc:
                        invalid_records.append(
                            {
                                "source_file_id": source_file["source_file_id"],
                                "line_number": line_number,
                                "error": f"invalid_json:{exc}",
                            }
                        )
                        continue
                    rows.append(value)
            else:
                payload = load_json_object(path, "migration quarantine sidecar")
                raw_rows = payload.get("records", payload.get("quarantine", []))
                rows = raw_rows if isinstance(raw_rows, list) else []
        except OSError as exc:
            invalid_records.append(
                {
                    "source_file_id": source_file["source_file_id"],
                    "line_number": None,
                    "error": f"read_failed:{exc}",
                }
            )
            continue

        for row_number, row in enumerate(rows, start=1):
            record_count += 1
            if not isinstance(row, dict):
                invalid_records.append(
                    {
                        "source_file_id": source_file["source_file_id"],
                        "line_number": row_number,
                        "error": "record_not_object",
                    }
                )
                continue
            occurrence_id = str(row.get("occurrence_id") or "").strip()
            reasons = normalize_quarantine_reasons(
                row.get("reasons", row.get("reason", row.get("quarantine_reason")))
            )
            if not occurrence_id:
                invalid_records.append(
                    {
                        "source_file_id": source_file["source_file_id"],
                        "line_number": row_number,
                        "error": "occurrence_id_missing",
                    }
                )
                continue
            if not reasons:
                reasons = ["migration_declared_quarantined_without_reason"]
            entry = index.setdefault(
                occurrence_id,
                {
                    "occurrence_id": occurrence_id,
                    "message_ids": set(),
                    "reasons": set(),
                    "source_file_ids": set(),
                    "records": [],
                },
            )
            message_id = str(row.get("message_id") or "").strip()
            if message_id:
                entry["message_ids"].add(message_id)
            entry["reasons"].update(reasons)
            entry["source_file_ids"].add(source_file["source_file_id"])
            entry["records"].append(copy.deepcopy(row))

    serializable_records = [
        {
            "occurrence_id": occurrence_id,
            "message_ids": sorted(entry["message_ids"]),
            "reasons": sorted(entry["reasons"]),
            "source_file_ids": sorted(entry["source_file_ids"]),
        }
        for occurrence_id, entry in sorted(index.items())
    ]
    return index, {
        "provided": bool(paths),
        "source_file_ids": sorted(source_file_ids),
        "source_file_count": len(source_file_ids),
        "record_count": record_count,
        "indexed_occurrence_count": len(index),
        "invalid_record_count": len(invalid_records),
        "invalid_records": invalid_records,
        "records": serializable_records,
        "auto_discovery_filename": MIGRATION_QUARANTINE_SIDECAR_NAME,
    }


def make_occurrence(
    row: dict[str, Any],
    *,
    row_index: int,
    source_file: dict[str, Any],
    source_kind: str,
    segment: dict[str, Any] | None,
    scope: Scope,
    collection: str,
    query_override: str | None = None,
    page_override: Any = None,
    result_override: Any = None,
    complete_source: bool | None = None,
    legacy_declared_variants: dict[str, Any] | None = None,
    migration_quarantine_index: dict[str, dict[str, Any]] | None = None,
    trusted_forum_thread_id: str | None = None,
) -> dict[str, Any]:
    query = str(query_override if query_override is not None else (segment or {}).get("query") or "")
    page_number = page_override if page_override is not None else row.get("page_number")
    result_index = result_override if result_override is not None else row.get("result_index")
    message_id = str(row.get("message_id") or "").strip()
    reasons: list[str] = []
    warnings: list[str] = []
    migration_id = migration_occurrence_id(row)
    per_message_migration_reasons = normalize_quarantine_reasons(
        row.get("migration_quarantine_reasons")
    )
    migration_source = bool(
        migration_id
        or row.get("migration_quarantined") is not None
        or per_message_migration_reasons
    )
    migration_quarantine_sources: list[str] = []
    migration_sidecar_file_ids: list[str] = []
    migration_reasons: set[str] = set()
    if row.get("migration_quarantined") is True or per_message_migration_reasons:
        migration_quarantine_sources.append("per_message")
        migration_reasons.update(per_message_migration_reasons)
        if not per_message_migration_reasons:
            migration_reasons.add("migration_declared_quarantined_without_reason")
    sidecar = (
        (migration_quarantine_index or {}).get(migration_id)
        if migration_id
        else None
    )
    if sidecar:
        migration_quarantine_sources.append("sidecar")
        migration_reasons.update(sidecar.get("reasons") or [])
        migration_sidecar_file_ids = sorted(sidecar.get("source_file_ids") or [])
        sidecar_message_ids = set(sidecar.get("message_ids") or [])
        if sidecar_message_ids and message_id and message_id not in sidecar_message_ids:
            migration_reasons.add("migration_quarantine_sidecar_message_id_mismatch")
    reasons.extend(sorted(migration_reasons))

    snowflake: dt.datetime | None = None
    if MESSAGE_ID_RE.fullmatch(message_id):
        try:
            snowflake = snowflake_datetime(message_id)
        except Exception:
            reasons.append("discord_snowflake_derivation_failed")
    else:
        reasons.append("invalid_or_missing_message_id")

    captured_text = first_nonempty(row, ("timestamp_utc", "created_at_utc"))
    captured: dt.datetime | None = None
    if captured_text:
        try:
            captured = parse_timestamp(captured_text)
        except Exception:
            reasons.append("captured_timestamp_invalid")
    else:
        reasons.append("captured_timestamp_missing")

    delta_ms: int | None = None
    if snowflake is not None and captured is not None:
        delta_ms = round(abs((captured - snowflake).total_seconds()) * 1000)
        if delta_ms > TIMESTAMP_MISMATCH_THRESHOLD_MS:
            reasons.append("captured_timestamp_snowflake_mismatch_gt_1000ms")
        elif delta_ms > 0:
            warnings.append("captured_timestamp_differs_within_1000ms")

    query_container_id = exact_id((segment or {}).get("query_container_id"))
    message_container_id, parent_container_id, container_issues = resolve_row_container(
        row,
        query_container_id,
        trusted_forum_thread_id=trusted_forum_thread_id,
    )
    for issue in container_issues:
        if issue == "message_container_inherited_from_query_segment":
            warnings.append(issue)
        else:
            reasons.append(issue)

    row_guild_id = exact_id(row.get("guild_id"))
    if row_guild_id and row_guild_id != scope.guild_id:
        reasons.append("guild_id_mismatch")

    local_date: dt.date | None = None
    if snowflake is not None:
        local_date = snowflake.astimezone(scope.timezone).date()
        if not (scope.start_date <= local_date <= scope.end_date_inclusive):
            reasons.append("snowflake_timestamp_outside_requested_local_window")

    occurrence_id = occurrence_id_for(
        source_file_id=source_file["source_file_id"],
        source_kind=source_kind,
        message_id=message_id,
        row_index=row_index,
        page_number=page_number,
        result_index=result_index,
        query=query,
        collection=collection,
    )
    return {
        "occurrence_id": occurrence_id,
        "message_id": message_id,
        "source_kind": source_kind,
        "source_file_id": source_file["source_file_id"],
        "source_file_relative_path": source_file["relative_path"],
        "source_file_size_bytes": source_file.get("size_bytes"),
        "source_file_sha256": source_file.get("sha256"),
        "source_collection": collection,
        "source_query": query,
        "segment_id": (segment or {}).get("segment_id"),
        "segment_start_date": (segment or {}).get("start_date"),
        "segment_end_date": (segment or {}).get("end_date"),
        "query_container_id": query_container_id,
        "message_container_id": message_container_id,
        "message_container_id_source": (
            "premium_whole_artifact_byte_bound_row_mapping"
            if exact_id(trusted_forum_thread_id)
            else None
        ),
        "parent_container_id": parent_container_id,
        "page_number": normalize_int(page_number),
        "result_index": normalize_int(result_index),
        "row_index": row_index,
        "complete_source": complete_source,
        "capture_completed_at_utc": (segment or {}).get("capture_completed_at_utc"),
        "snowflake_timestamp_utc": iso_z(snowflake) if snowflake else None,
        "snowflake_local_date": local_date.isoformat() if local_date else None,
        "captured_timestamp_utc": iso_z(captured) if captured else str(captured_text or "") or None,
        "timestamp_delta_ms": delta_ms,
        "quarantined": bool(reasons),
        "quarantine_reasons": reasons,
        "migration_source": migration_source,
        "migration_occurrence_id": migration_id,
        "migration_quarantined": bool(migration_reasons),
        "migration_quarantine_reasons": sorted(migration_reasons),
        "migration_quarantine_sources": sorted(set(migration_quarantine_sources)),
        "migration_quarantine_sidecar_source_file_ids": migration_sidecar_file_ids,
        "warnings": warnings,
        "legacy_declared_field_variants": copy.deepcopy(legacy_declared_variants or {}),
        "payload": copy.deepcopy(row),
    }


def discover_segment_files(segment_dirs: Sequence[Path]) -> list[Path]:
    paths: dict[Path, None] = {}
    for directory in segment_dirs:
        if not directory.exists():
            continue
        for path in directory.rglob("*.json"):
            if (
                path.is_file()
                and not path.name.endswith(COMPLETION_EVIDENCE_SIDECAR_SUFFIX)
                and not path.name.endswith(
                    TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX
                )
            ):
                paths[path.resolve()] = None
    return sorted(paths, key=lambda item: item.as_posix().lower())


def ingest_segment_files(
    segment_dirs: Sequence[Path],
    scope: Scope,
    provenance_root: Path,
    source_registry: dict[str, dict[str, Any]],
    migration_quarantine_index: dict[str, dict[str, Any]] | None = None,
    *,
    input_role: str = "channel_capture",
    authorized_scope: authorized_collection_scope.AuthorizedScope | None = None,
    proven_children: dict[str, dict[str, Any]] | None = None,
    authorized_parent_name_aliases: dict[str, frozenset[str]] | None = None,
    scope_excluded_files: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    segment_records: list[dict[str, Any]] = []
    occurrences: list[dict[str, Any]] = []
    file_failures: list[dict[str, Any]] = []
    for path in discover_segment_files(segment_dirs):
        sidecar_path = completion_evidence_sidecar_path(path)
        timestamp_sidecar_path = (
            timestamp_scope_revalidation.timestamp_scope_revalidation_sidecar_path(
                path
            )
        )
        payload: dict[str, Any] | None = None
        premium_acceptance_audit: dict[str, Any] | None = None
        if authorized_scope is not None:
            try:
                payload = load_json_object(path, "channel segment")
                classification = authorized_collection_scope.classify_segment_payload(
                    payload,
                    authorized_scope,
                    proven_children or {},
                    parent_name_aliases=authorized_parent_name_aliases,
                )
                classification = authorized_collection_scope.apply_canonical_path_policy(
                    path, classification, authorized_scope
                )
                if (
                    classification.get("included") is True
                    and str(classification.get("requested_container_id") or "")
                    == authorized_collection_scope.PREMIUM_PARENT_ID
                ):
                    segment_payload = payload.get("segment")
                    segment_payload = (
                        segment_payload if isinstance(segment_payload, dict) else {}
                    )
                    route = {
                        "start": segment_payload.get("start"),
                        "end": segment_payload.get("end"),
                        "query": segment_payload.get("query"),
                        "expected_canonical_path": path.resolve()
                        .relative_to(provenance_root.resolve())
                        .as_posix(),
                    }
                    try:
                        premium_acceptance_audit = (
                            premium_journals_provenance_contract
                            .audit_premium_canonical(
                                path,
                                route,
                                artifact_root=provenance_root,
                            )
                        )
                    except (
                        premium_journals_provenance_contract
                        .PremiumJournalsContractError
                    ) as exc:
                        classification = {
                            **classification,
                            "included": False,
                            "classification": "ambiguous_fail_closed",
                            "reason": (
                                "premium_authoritative_contract_invalid:"
                                f"{type(exc).__name__}:{exc}"
                            ),
                        }
            except CorpusError as exc:
                classification = {
                    "included": False,
                    "classification": "ambiguous_fail_closed",
                    "reason": f"segment_json_unreadable:{exc}",
                    "requested_container_id": None,
                    "requested_container_id_source": None,
                    "parent_container_id": None,
                }
            if not classification.get("included"):
                if scope_excluded_files is not None:
                    scope_excluded_files.append(
                        authorized_collection_scope.audit_file_record(
                            path,
                            provenance_root=provenance_root,
                            classification=classification,
                            payload=payload,
                            artifact_role="segment",
                        )
                    )
                    if sidecar_path.is_file():
                        scope_excluded_files.append(
                            authorized_collection_scope.audit_file_record(
                                sidecar_path,
                                provenance_root=provenance_root,
                                classification=classification,
                                payload=None,
                                artifact_role="completion_evidence_sidecar",
                            )
                        )
                    if timestamp_sidecar_path.is_file():
                        scope_excluded_files.append(
                            authorized_collection_scope.audit_file_record(
                                timestamp_sidecar_path,
                                provenance_root=provenance_root,
                                classification=classification,
                                payload=None,
                                artifact_role="timestamp_scope_revalidation_sidecar",
                            )
                        )
                continue
        source_file = register_source_file(
            source_registry, path, provenance_root, kind=f"{input_role}_segment"
        )
        sidecar_source_file = (
            register_source_file(
                source_registry,
                sidecar_path,
                provenance_root,
                kind="segment_completion_evidence_sidecar",
            )
            if sidecar_path.is_file()
            else None
        )
        timestamp_sidecar_source_file = (
            register_source_file(
                source_registry,
                timestamp_sidecar_path,
                provenance_root,
                kind="timestamp_scope_revalidation_sidecar",
            )
            if timestamp_sidecar_path.is_file()
            else None
        )
        try:
            if payload is None:
                payload = load_json_object(path, "channel segment")
        except CorpusError as exc:
            file_failures.append(
                {
                    "source_file_id": source_file["source_file_id"],
                    "source_file_relative_path": source_file["relative_path"],
                    "input_role": input_role,
                    "status": "invalid_json",
                    "errors": [str(exc)],
                }
            )
            continue
        assert payload is not None
        try:
            path.resolve().relative_to(SCRIPT_DIR.resolve())
            timestamp_artifact_root = SCRIPT_DIR
        except ValueError:
            timestamp_artifact_root = provenance_root
        timestamp_revalidation = (
            timestamp_scope_revalidation.load_adjacent_timestamp_scope_revalidation(
                path,
                payload,
                source_artifact_sha256=str(source_file.get("sha256") or ""),
                artifact_root=timestamp_artifact_root,
            )
        )
        timestamp_evidence_source_files = []
        for evidence_artifact in timestamp_revalidation.source_artifacts():
            evidence_path = Path(evidence_artifact["path"])
            if evidence_path.resolve() == timestamp_sidecar_path.resolve():
                continue
            timestamp_evidence_source_files.append(
                register_source_file(
                    source_registry,
                    evidence_path,
                    provenance_root,
                    kind=str(evidence_artifact.get("kind") or "timestamp_scope_evidence"),
                )
            )
        premium_provenance_source_files = []
        if premium_acceptance_audit is not None:
            for bound in premium_acceptance_audit["accepted_artifact"][
                "source_files"
            ]:
                bound_path = provenance_root / str(bound.get("path") or "")
                if bound_path.resolve() in {
                    path.resolve(),
                    timestamp_sidecar_path.resolve(),
                } or any(
                    bound_path.resolve() == Path(source["path"]).resolve()
                    for source in timestamp_revalidation.source_artifacts()
                ):
                    continue
                registered = register_source_file(
                    source_registry,
                    bound_path,
                    provenance_root,
                    kind=str(bound.get("role") or "premium_forum_provenance"),
                )
                if (
                    registered.get("sha256") != bound.get("sha256")
                    or registered.get("size_bytes") != bound.get("bytes")
                ):
                    raise CorpusError(
                        "Premium forum provenance bytes changed after strict acceptance: "
                        + str(bound.get("path") or "")
                    )
                premium_provenance_source_files.append(registered)
        segment, rows = validate_segment_payload(
            path,
            payload,
            scope,
            input_role=input_role,
            artifact_root=timestamp_artifact_root,
            source_artifact_sha256=str(source_file.get("sha256") or ""),
            timestamp_revalidation=timestamp_revalidation,
        )
        segment["source_file_id"] = source_file["source_file_id"]
        segment["source_file_relative_path"] = source_file["relative_path"]
        segment["source_file_size_bytes"] = source_file.get("size_bytes")
        segment["source_file_sha256"] = source_file.get("sha256")
        segment["completion_evidence_source_file_id"] = (
            sidecar_source_file.get("source_file_id") if sidecar_source_file else None
        )
        segment["timestamp_scope_revalidation_source_file_id"] = (
            timestamp_sidecar_source_file.get("source_file_id")
            if timestamp_sidecar_source_file
            else None
        )
        segment["timestamp_scope_evidence_source_file_ids"] = [
            row["source_file_id"] for row in timestamp_evidence_source_files
        ]
        segment["premium_journals_provenance_integrity"] = (
            copy.deepcopy(premium_acceptance_audit["accepted_artifact"])
            if premium_acceptance_audit is not None
            else None
        )
        segment["premium_forum_provenance_source_file_ids"] = [
            row["source_file_id"] for row in premium_provenance_source_files
        ]
        segment["segment_id"] = segment_id_for(source_file["source_file_id"], segment)
        segment_records.append(segment)
        for row_index, row in enumerate(rows, start=1):
            trusted_premium_thread_id = (
                str(
                    (
                        premium_acceptance_audit.get("row_child_container_ids")
                        if premium_acceptance_audit is not None
                        else {}
                    ).get(str(row.get("message_id") or ""))
                    or ""
                )
                or None
            )
            occurrences.append(
                make_occurrence(
                    row,
                    row_index=row_index,
                    source_file=source_file,
                    source_kind="channel_segment",
                    segment=segment,
                    scope=scope,
                    collection={
                        "channel_capture": "serverwide_channel_segment",
                        "relevance_query": "targeted_relevance_query_segment",
                        "residual_audit": "residual_audit_census_segment",
                    }.get(input_role, f"{input_role}_segment"),
                    complete_source=bool(segment["computed_complete"]),
                    migration_quarantine_index=migration_quarantine_index,
                    trusted_forum_thread_id=trusted_premium_thread_id,
                )
            )
    return segment_records, occurrences, file_failures


def resolve_provenance_bound_path(
    value: Any, provenance_root: Path, *, declaring_path: Path | None = None
) -> Path:
    """Resolve a note-declared artifact path without allowing it to escape the root."""

    text = str(value or "").strip()
    if not text:
        raise CorpusError("reconciliation artifact path missing")
    candidate = Path(text)
    if not candidate.is_absolute():
        candidates = [provenance_root / candidate]
        if declaring_path is not None:
            for ancestor in declaring_path.resolve().parents:
                try:
                    ancestor.relative_to(provenance_root.resolve())
                except ValueError:
                    break
                candidates.append(ancestor / candidate)
        candidate = next(
            (item for item in candidates if item.is_file()), candidates[0]
        )
    resolved = candidate.resolve()
    try:
        resolved.relative_to(provenance_root.resolve())
    except ValueError as exc:
        raise CorpusError("reconciliation artifact path escapes provenance root") from exc
    return resolved


def discover_historical_reconciliation_notes(
    directories: Sequence[Path],
) -> list[Path]:
    paths: set[Path] = set()
    for directory in directories:
        resolved = Path(directory).resolve()
        if not resolved.is_dir():
            continue
        for path in resolved.rglob("*.json"):
            if path.is_file() and path.name.endswith(
                HISTORICAL_RECONCILIATION_NOTE_SUFFIXES
            ):
                paths.add(path.resolve())
    return sorted(paths, key=lambda path: path.as_posix().casefold())


def reconciliation_artifact_bindings(note: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    legacy = note.get("legacy_artifact")
    replacement = note.get("replacement_artifact")
    legacy = legacy if isinstance(legacy, dict) else {}
    replacement = replacement if isinstance(replacement, dict) else {}
    return (
        note.get("legacy_final_quarantine_path")
        or note.get("prior_final_quarantine_path")
        or legacy.get("preserved_path"),
        note.get("legacy_final_sha256")
        or note.get("prior_final_sha256")
        or legacy.get("sha256"),
        note.get("replacement_final_path") or replacement.get("canonical_path"),
        note.get("replacement_final_sha256") or replacement.get("sha256"),
    )


def ingest_historical_reconciliations(
    directories: Sequence[Path],
    scope: Scope,
    provenance_root: Path,
    source_registry: dict[str, dict[str, Any]],
    *,
    authorized_scope: authorized_collection_scope.AuthorizedScope | None = None,
    proven_children: dict[str, dict[str, Any]] | None = None,
    scope_excluded_files: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Retain byte-bound pre-provenance snapshots without promoting them to evidence.

    A reconciliation note may prove that an older message-bearing snapshot was
    followed by a complete, exact-scope v2.5 recapture whose current result set no
    longer contains one or more of those message IDs.  Those historical rows remain
    searchable as quarantined occurrences.  The scope recapture certifies only the
    historical-unavailability classification; it never makes the old row eligible
    for accepted analysis and it makes no deletion/edit causal claim.
    """

    note_paths = discover_historical_reconciliation_notes(directories)
    occurrences: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    invalid_records: list[dict[str, Any]] = []
    duplicate_bindings: list[dict[str, Any]] = []
    seen_bindings: set[tuple[Path, Path]] = set()

    for note_path in note_paths:
        note_source = register_source_file(
            source_registry,
            note_path,
            provenance_root,
            kind="historical_reconciliation_note",
        )
        errors: list[str] = []
        try:
            note = load_json_object(note_path, "historical reconciliation note")
        except CorpusError as exc:
            invalid_records.append(
                {
                    "note_source_file_id": note_source["source_file_id"],
                    "errors": [str(exc)],
                }
            )
            continue

        if authorized_scope is not None and not authorized_collection_scope.is_authorized_container_id(
            note.get("channel_id"), authorized_scope, proven_children or {}
        ):
            note_channel_id = exact_id(note.get("channel_id"))
            classification = {
                "included": False,
                "classification": (
                    "outside_authorized_scope"
                    if note_channel_id
                    else "ambiguous_fail_closed"
                ),
                "reason": (
                    "historical_reconciliation_container_not_authorized"
                    if note_channel_id
                    else "historical_reconciliation_exact_container_missing"
                ),
                "requested_container_id": note_channel_id,
                "requested_container_id_source": "reconciliation_note_exact_channel_id",
                "parent_container_id": None,
            }
            source_registry.pop(note_source["source_file_id"], None)
            if scope_excluded_files is not None:
                scope_excluded_files.append(
                    authorized_collection_scope.audit_file_record(
                        note_path,
                        provenance_root=provenance_root,
                        classification=classification,
                        payload=None,
                        artifact_role="historical_reconciliation_note",
                    )
                )
                legacy_value, _legacy_sha, current_value, _current_sha = (
                    reconciliation_artifact_bindings(note)
                )
                for artifact_role, artifact_value in (
                    ("historical_reconciled_segment", legacy_value),
                    ("historical_reconciliation_current_segment", current_value),
                ):
                    try:
                        artifact_path = resolve_provenance_bound_path(
                            artifact_value,
                            provenance_root,
                            declaring_path=note_path,
                        )
                    except CorpusError:
                        continue
                    if not artifact_path.is_file():
                        continue
                    try:
                        artifact_payload = load_json_object(
                            artifact_path, artifact_role
                        )
                    except CorpusError:
                        artifact_payload = None
                    scope_excluded_files.append(
                        authorized_collection_scope.audit_file_record(
                            artifact_path,
                            provenance_root=provenance_root,
                            classification=classification,
                            payload=artifact_payload,
                            artifact_role=artifact_role,
                        )
                    )
            continue

        if str(note.get("event_type") or "") not in {
            "discord_collector_version_replacement",
            "discord_collector_count_reconciliation_and_version_replacement",
        }:
            errors.append("reconciliation_event_type_invalid")
        if exact_id(note.get("guild_id")) != scope.guild_id:
            errors.append("reconciliation_guild_mismatch")

        legacy_path_value, declared_legacy_sha, current_path_value, declared_current_sha = (
            reconciliation_artifact_bindings(note)
        )
        try:
            legacy_path = resolve_provenance_bound_path(
                legacy_path_value,
                provenance_root,
                declaring_path=note_path,
            )
            current_path = resolve_provenance_bound_path(
                current_path_value,
                provenance_root,
                declaring_path=note_path,
            )
        except CorpusError as exc:
            errors.append(str(exc))
            legacy_path = note_path
            current_path = note_path

        binding = (legacy_path, current_path)
        if binding in seen_bindings:
            duplicate_bindings.append(
                {
                    "note_source_file_id": note_source["source_file_id"],
                    "legacy_relative_path": source_path_token(
                        legacy_path, provenance_root
                    )[0],
                    "current_relative_path": source_path_token(
                        current_path, provenance_root
                    )[0],
                }
            )
            continue
        seen_bindings.add(binding)

        if not legacy_path.is_file():
            errors.append("reconciled_legacy_artifact_missing")
        if not current_path.is_file():
            errors.append("reconciled_current_artifact_missing")
        actual_legacy_sha = sha256_file(legacy_path) if legacy_path.is_file() else None
        actual_current_sha = sha256_file(current_path) if current_path.is_file() else None
        if not re.fullmatch(r"[0-9a-f]{64}", str(declared_legacy_sha or "")):
            errors.append("reconciled_legacy_sha256_missing_or_invalid")
        elif actual_legacy_sha != str(declared_legacy_sha):
            errors.append("reconciled_legacy_sha256_mismatch")
        if not re.fullmatch(r"[0-9a-f]{64}", str(declared_current_sha or "")):
            errors.append("reconciled_current_sha256_missing_or_invalid")
        elif actual_current_sha != str(declared_current_sha):
            errors.append("reconciled_current_sha256_mismatch")

        legacy_source = (
            register_source_file(
                source_registry,
                legacy_path,
                provenance_root,
                kind="historical_reconciled_segment",
            )
            if legacy_path.is_file()
            else None
        )
        current_source = (
            register_source_file(
                source_registry,
                current_path,
                provenance_root,
                kind="historical_reconciliation_current_segment",
            )
            if current_path.is_file()
            else None
        )

        legacy_payload: dict[str, Any] = {}
        current_payload: dict[str, Any] = {}
        if legacy_path.is_file():
            try:
                legacy_payload = load_json_object(
                    legacy_path, "historical reconciled segment"
                )
            except CorpusError as exc:
                errors.append(str(exc))
        if current_path.is_file():
            try:
                current_payload = load_json_object(
                    current_path, "historical reconciliation current segment"
                )
            except CorpusError as exc:
                errors.append(str(exc))

        legacy_segment: dict[str, Any] = {}
        legacy_rows: list[dict[str, Any]] = []
        current_segment: dict[str, Any] = {}
        current_rows: list[dict[str, Any]] = []
        if legacy_payload:
            legacy_segment, legacy_rows = validate_segment_payload(
                legacy_path,
                legacy_payload,
                scope,
                input_role="historical_reconciled_snapshot",
            )
        if current_payload:
            current_segment, current_rows = validate_segment_payload(
                current_path,
                current_payload,
                scope,
                input_role="historical_reconciliation_current",
            )
            if not current_segment.get("computed_complete"):
                errors.append("reconciliation_current_segment_not_strictly_complete")

        note_channel_id = exact_id(note.get("channel_id"))
        if not note_channel_id:
            errors.append("reconciliation_channel_id_missing")
        elif current_segment and note_channel_id != current_segment.get(
            "query_container_id"
        ):
            errors.append("reconciliation_current_container_mismatch")
        elif legacy_segment and note_channel_id != legacy_segment.get(
            "query_container_id"
        ):
            errors.append("reconciliation_legacy_container_mismatch")
        note_query = str(note.get("query") or "")
        if not note_query:
            errors.append("reconciliation_query_missing")
        elif current_segment and note_query != current_segment.get("query"):
            errors.append("reconciliation_current_query_mismatch")
        elif legacy_segment and note_query != legacy_segment.get("query"):
            errors.append("reconciliation_legacy_query_mismatch")
        note_start = str(note.get("segment_start") or "")
        note_end = str(note.get("segment_end") or "")
        if (
            not DATE_RE.fullmatch(note_start)
            or not DATE_RE.fullmatch(note_end)
            or (current_segment and note_start != current_segment.get("start_date"))
            or (current_segment and note_end != current_segment.get("end_date"))
            or (legacy_segment and note_start != legacy_segment.get("start_date"))
            or (legacy_segment and note_end != legacy_segment.get("end_date"))
        ):
            errors.append("reconciliation_current_date_scope_mismatch")

        legacy_ids = {
            str(row.get("message_id") or "")
            for row in legacy_rows
            if MESSAGE_ID_RE.fullmatch(str(row.get("message_id") or ""))
        }
        current_ids = {
            str(row.get("message_id") or "")
            for row in current_rows
            if MESSAGE_ID_RE.fullmatch(str(row.get("message_id") or ""))
        }
        missing_ids = legacy_ids - current_ids
        added_ids = current_ids - legacy_ids
        shared_ids = legacy_ids & current_ids

        declared_reconciliation = note.get("message_id_reconciliation")
        declared_reconciliation = (
            declared_reconciliation
            if isinstance(declared_reconciliation, dict)
            else {}
        )
        declared_missing = {
            str(value)
            for value in (declared_reconciliation.get("missing_ids") or [])
        }
        declared_added = {
            str(value)
            for value in (declared_reconciliation.get("added_ids") or [])
        }

        if errors:
            invalid_records.append(
                {
                    "note_source_file_id": note_source["source_file_id"],
                    "legacy_source_file_id": (
                        legacy_source.get("source_file_id") if legacy_source else None
                    ),
                    "current_source_file_id": (
                        current_source.get("source_file_id") if current_source else None
                    ),
                    "errors": sorted(set(errors)),
                }
            )
            continue

        assert legacy_source is not None
        assert current_source is not None
        legacy_segment["source_file_id"] = legacy_source["source_file_id"]
        legacy_segment["source_file_relative_path"] = legacy_source["relative_path"]
        legacy_segment["source_file_size_bytes"] = legacy_source.get("size_bytes")
        legacy_segment["source_file_sha256"] = legacy_source.get("sha256")
        legacy_segment["segment_id"] = segment_id_for(
            legacy_source["source_file_id"], legacy_segment
        )

        for row_index, row in enumerate(legacy_rows, start=1):
            row_copy = copy.deepcopy(row)
            message_id = str(row_copy.get("message_id") or "")
            reasons = ["historical_pre_provenance_snapshot"]
            if message_id in missing_ids:
                reasons.append("historical_disappeared_from_latest_fresh_exact_search")
            row_copy["migration_quarantined"] = True
            row_copy["migration_quarantine_reasons"] = reasons
            row_copy["_migration_occurrence"] = {
                "occurrence_id": sha256_bytes(
                    compact_json(
                        {
                            "kind": "historical_reconciled_snapshot",
                            "source_file_id": legacy_source["source_file_id"],
                            "row_index": row_index,
                            "message_id": message_id,
                        }
                    ).encode("utf-8")
                )
            }
            occurrence = make_occurrence(
                row_copy,
                row_index=row_index,
                source_file=legacy_source,
                source_kind="historical_reconciled_segment",
                segment=legacy_segment,
                scope=scope,
                collection="historical_reconciled_snapshot",
                complete_source=False,
            )
            occurrence["historical_disappeared_certified"] = bool(
                message_id in missing_ids
            )
            occurrence["historical_reconciliation_note_source_file_id"] = note_source[
                "source_file_id"
            ]
            occurrence["historical_reconciliation_current_source_file_id"] = (
                current_source["source_file_id"]
            )
            occurrences.append(occurrence)

        actual_current_sha = current_source.get("sha256")
        records.append(
            {
                "note_source_file_id": note_source["source_file_id"],
                "legacy_source_file_id": legacy_source["source_file_id"],
                "current_source_file_id": current_source["source_file_id"],
                "channel_id": note_channel_id,
                "query": note_query,
                "segment_start": note_start,
                "segment_end": note_end,
                "legacy_message_count": len(legacy_rows),
                "legacy_unique_valid_message_ids": len(legacy_ids),
                "current_unique_valid_message_ids": len(current_ids),
                "shared_message_ids": sorted(shared_ids),
                "historically_unavailable_message_ids": sorted(missing_ids),
                "current_added_message_ids": sorted(added_ids),
                "declared_missing_ids_at_note_time": sorted(declared_missing),
                "declared_added_ids_at_note_time": sorted(declared_added),
                "set_drift_since_note": {
                    "missing_ids_added_since_note": sorted(missing_ids - declared_missing),
                    "missing_ids_returned_since_note": sorted(declared_missing - missing_ids),
                    "added_ids_since_note": sorted(added_ids - declared_added),
                    "added_ids_absent_since_note": sorted(declared_added - added_ids),
                },
                "declared_current_sha256": str(declared_current_sha or "") or None,
                "actual_current_sha256": actual_current_sha,
                "current_hash_matches_declared": bool(
                    declared_current_sha
                    and str(declared_current_sha) == actual_current_sha
                ),
                "causal_claim": "No deletion, edit, or other cause is inferred from search-set differences alone.",
            }
        )

    certified_ids = sorted(
        {
            str(item.get("message_id"))
            for item in occurrences
            if item.get("historical_disappeared_certified")
            and MESSAGE_ID_RE.fullmatch(str(item.get("message_id") or ""))
        }
    )
    return occurrences, {
        "provided": bool(note_paths),
        "note_count": len(note_paths),
        "valid_note_count": len(records),
        "invalid_note_count": len(invalid_records),
        "duplicate_binding_count": len(duplicate_bindings),
        "historical_occurrence_count": len(occurrences),
        "certified_historically_unavailable_message_count": len(certified_ids),
        "certified_historically_unavailable_message_ids": certified_ids,
        "records": records,
        "invalid_records": invalid_records,
        "duplicate_bindings": duplicate_bindings,
        "policy": (
            "Byte-bound legacy rows remain searchable but quarantined and analysis-ineligible. "
            "A complete current exact-scope recapture may certify only that a prior ID is absent "
            "from the current search result set; no deletion/edit cause is claimed."
        ),
    }


def resolve_legacy_descriptor_path(
    descriptor_path: Any,
    legacy_path: Path,
    provenance_root: Path,
) -> Path:
    text = str(descriptor_path or "").strip()
    if not text:
        return legacy_path
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = provenance_root / candidate
    return candidate


def ingest_legacy_raw(
    legacy_path: Path | None,
    scope: Scope,
    provenance_root: Path,
    source_registry: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if legacy_path is None:
        return [], {
            "provided": False,
            "source_file_id": None,
            "unique_message_ids": 0,
            "reconstructed_occurrences": 0,
            "collections": [],
            "note": "No legacy three-month raw artifact was supplied.",
        }
    legacy_path = legacy_path.resolve()
    legacy_source = register_source_file(
        source_registry, legacy_path, provenance_root, kind="legacy_merged_raw"
    )
    payload = load_json_object(legacy_path, "legacy raw corpus")
    occurrences: list[dict[str, Any]] = []
    unique_ids: set[str] = set()
    collections: list[str] = []
    reconstructed = 0
    for collection, rows in payload.items():
        if collection == "metadata" or not isinstance(rows, list):
            continue
        message_rows = [row for row in rows if isinstance(row, dict) and row.get("message_id")]
        if not message_rows:
            continue
        collections.append(collection)
        for row_index, row in enumerate(message_rows, start=1):
            message_id = str(row.get("message_id") or "")
            unique_ids.add(message_id)
            merge_provenance = row.get("_merge_provenance")
            sources = (
                merge_provenance.get("sources")
                if isinstance(merge_provenance, dict)
                and isinstance(merge_provenance.get("sources"), list)
                else []
            )
            declared_variants = (
                merge_provenance.get("field_variants")
                if isinstance(merge_provenance, dict)
                and isinstance(merge_provenance.get("field_variants"), dict)
                else {}
            )
            if not sources:
                sources = [
                    {
                        "source_file": str(legacy_path),
                        "collection": collection,
                        "query": row.get("search_query"),
                        "result_index": row.get("result_index"),
                        "page_number": row.get("page_number"),
                        "complete_source": None,
                    }
                ]
            for source_number, descriptor in enumerate(sources, start=1):
                descriptor = descriptor if isinstance(descriptor, dict) else {}
                referenced_path = resolve_legacy_descriptor_path(
                    descriptor.get("source_file"), legacy_path, provenance_root
                )
                if referenced_path.is_file():
                    referenced_source = register_source_file(
                        source_registry,
                        referenced_path,
                        provenance_root,
                        kind="legacy_referenced_source",
                    )
                else:
                    # Keep the missing descriptor as a first-class, portable
                    # provenance record. Its absent hash is an intentional release
                    # gate failure; the legacy merged artifact remains separately
                    # byte-verifiable but does not impersonate the missing source.
                    referenced_source = register_source_file(
                        source_registry,
                        referenced_path,
                        provenance_root,
                        kind="legacy_referenced_source_missing",
                        exists_override=False,
                    )
                segment_stub = {
                    "segment_id": None,
                    "query_container_id": exact_id(
                        first_nonempty(row, ("channel_id", "inferred_thread_channel_id"))
                    ),
                    "query": str(descriptor.get("query") or row.get("search_query") or ""),
                    "start_date": descriptor.get("segment_start"),
                    "end_date": descriptor.get("segment_end"),
                    "capture_completed_at_utc": None,
                }
                occurrence = make_occurrence(
                    row,
                    row_index=row_index * 1000 + source_number,
                    source_file=referenced_source,
                    source_kind="legacy_reconstructed_occurrence",
                    segment=segment_stub,
                    scope=scope,
                    collection=str(descriptor.get("collection") or collection),
                    query_override=str(descriptor.get("query") or row.get("search_query") or ""),
                    page_override=descriptor.get("page_number"),
                    result_override=descriptor.get("result_index"),
                    complete_source=(
                        bool(descriptor.get("complete_source"))
                        if descriptor.get("complete_source") is not None
                        else None
                    ),
                    legacy_declared_variants=declared_variants,
                )
                occurrence["legacy_occurrence_reconstructed"] = True
                occurrences.append(occurrence)
                reconstructed += 1
    return occurrences, {
        "provided": True,
        "source_file_id": legacy_source["source_file_id"],
        "source_file_relative_path": legacy_source["relative_path"],
        "source_file_size_bytes": legacy_source["size_bytes"],
        "source_file_sha256": legacy_source["sha256"],
        "unique_message_ids": len(unique_ids),
        "reconstructed_occurrences": reconstructed,
        "collections": sorted(collections),
        "coverage_contribution": "none",
        "note": (
            "Legacy occurrences preserve prior provenance and message IDs but do not certify "
            "server-wide channel/date coverage. Occurrence-specific field values unavailable "
            "from the merged legacy artifact remain reconstructed and are labelled as such."
        ),
    }


def nonempty(value: Any) -> bool:
    return value not in EMPTY_VALUES


def union_list_values(values: Iterable[Any]) -> list[Any]:
    output: list[Any] = []
    markers: set[str] = set()
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            marker = compact_json(item)
            if marker not in markers:
                markers.add(marker)
                output.append(copy.deepcopy(item))
    return output


def occurrence_sort_key(occurrence: dict[str, Any]) -> tuple[Any, ...]:
    kind_rank = 0 if occurrence.get("source_kind") == "channel_segment" else 1
    quarantine_rank = 1 if occurrence.get("quarantined") else 0
    return (
        quarantine_rank,
        kind_rank,
        str(occurrence.get("source_file_relative_path") or ""),
        int(occurrence.get("page_number") or 0),
        int(occurrence.get("result_index") or 0),
        int(occurrence.get("row_index") or 0),
        str(occurrence.get("occurrence_id") or ""),
    )


def is_trusted_canonical_occurrence(occurrence: dict[str, Any]) -> bool:
    """Return whether an occurrence is an independent canonical recapture.

    A migrated legacy row remains non-canonical even when it happens not to
    carry a quarantine reason.  Only a separate, non-quarantined channel
    strictly complete segment occurrence can unlock that message for accepted
    analytical use. Partial or failed captures remain searchable but cannot do so.
    """

    return bool(
        occurrence.get("source_kind") == "channel_segment"
        and occurrence.get("complete_source") is True
        and not occurrence.get("quarantined")
        and not occurrence.get("migration_source")
    )


def merge_message_occurrences(
    message_id: str,
    occurrences: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(occurrences, key=occurrence_sort_key)
    non_quarantined = [item for item in ordered if not item.get("quarantined")]
    trusted_canonical = [item for item in ordered if is_trusted_canonical_occurrence(item)]
    canonical_pool = trusted_canonical or non_quarantined or ordered
    selected = canonical_pool[0]
    selected_payload = copy.deepcopy(selected.get("payload") or {})
    selected_payload.pop("_merge_provenance", None)
    selected_payload.pop("_corpus_provenance", None)
    selected_payload.pop("_field_variants", None)
    selected_payload["message_id"] = message_id

    field_occurrences: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for occurrence in ordered:
        payload = occurrence.get("payload") if isinstance(occurrence.get("payload"), dict) else {}
        for key, value in payload.items():
            if key in {"_merge_provenance", "_corpus_provenance", "_field_variants"} or not nonempty(value):
                continue
            marker = compact_json(value)
            entry = field_occurrences[key].setdefault(
                marker,
                {
                    "value": copy.deepcopy(value),
                    "occurrence_ids": [],
                    "source": "captured_occurrence",
                },
            )
            entry["occurrence_ids"].append(occurrence["occurrence_id"])
        legacy_variants = occurrence.get("legacy_declared_field_variants")
        if isinstance(legacy_variants, dict):
            for key, values in legacy_variants.items():
                candidate_values = values if isinstance(values, list) else [values]
                for value in candidate_values:
                    if not nonempty(value):
                        continue
                    marker = compact_json(value)
                    field_occurrences[key].setdefault(
                        marker,
                        {
                            "value": copy.deepcopy(value),
                            "occurrence_ids": [],
                            "source": "legacy_declared_variant",
                        },
                    )

    # For multi-valued Discord payload fields, union trusted occurrences so a
    # later capture cannot silently erase an attachment, embed, or reaction.
    trusted_payloads = [item.get("payload") or {} for item in canonical_pool]
    for field in LIST_UNION_FIELDS:
        union = union_list_values(payload.get(field) for payload in trusted_payloads)
        if union:
            selected_payload[field] = union

    variants = {
        key: list(marker_map.values())
        for key, marker_map in sorted(field_occurrences.items())
        if len(marker_map) > 1
    }
    snowflake = snowflake_datetime(message_id)
    captured_timestamp_values = sorted(
        {
            str(item.get("captured_timestamp_utc"))
            for item in ordered
            if item.get("captured_timestamp_utc")
        }
    )
    mismatched_occurrence_ids = [
        item["occurrence_id"]
        for item in ordered
        if "captured_timestamp_snowflake_mismatch_gt_1000ms"
        in (item.get("quarantine_reasons") or [])
    ]
    container_ids = sorted(
        {
            str(item.get("message_container_id"))
            for item in ordered
            if item.get("message_container_id")
        }
    )
    source_files = sorted(
        {
            str(item.get("source_file_id"))
            for item in ordered
            if item.get("source_file_id")
        }
    )
    queries = sorted(
        {str(item.get("source_query")) for item in ordered if item.get("source_query")}
    )
    collections = sorted(
        {
            str(item.get("source_collection"))
            for item in ordered
            if item.get("source_collection")
        }
    )
    segment_ids = sorted(
        {str(item.get("segment_id")) for item in ordered if item.get("segment_id")}
    )
    selected_payload["timestamp_utc_captured"] = selected.get("captured_timestamp_utc")
    selected_payload["timestamp_utc"] = iso_z(snowflake)
    selected_payload["canonical_created_at_utc"] = iso_z(snowflake)
    selected_payload["canonical_created_at_source"] = "discord_snowflake"
    if selected.get("message_container_id"):
        selected_payload["channel_id"] = selected["message_container_id"]
    selected_payload["_field_variants"] = variants
    selected_payload["_timestamp_audit"] = {
        "snowflake_timestamp_utc": iso_z(snowflake),
        "captured_timestamp_variants": captured_timestamp_values,
        "max_absolute_delta_ms": max(
            (int(item["timestamp_delta_ms"]) for item in ordered if item.get("timestamp_delta_ms") is not None),
            default=None,
        ),
        "mismatched_occurrence_ids": mismatched_occurrence_ids,
    }
    selected_payload["_corpus_provenance"] = {
        "canonical_occurrence_id": selected["occurrence_id"],
        "canonical_selection_rule": (
            "first strictly complete, independently recaptured, non-migration, non-quarantined channel-segment "
            "occurrence in deterministic source/page/result order; non-canonical or quarantined "
            "occurrence only when no trusted canonical recapture exists"
        ),
        "occurrence_count": len(ordered),
        "trusted_occurrence_count": len(non_quarantined),
        "trusted_canonical_occurrence_count": len(trusted_canonical),
        "trusted_canonical_occurrence_ids": [
            item["occurrence_id"] for item in trusted_canonical
        ],
        "migration_occurrence_count": sum(bool(item.get("migration_source")) for item in ordered),
        "migration_quarantined_occurrence_ids": [
            item["occurrence_id"]
            for item in ordered
            if item.get("migration_quarantined")
        ],
        "quarantined_occurrence_count": len(ordered) - len(non_quarantined),
        "occurrence_ids": [item["occurrence_id"] for item in ordered],
        "source_file_ids": source_files,
        "source_collections": collections,
        "source_queries": queries,
        "segment_ids": segment_ids,
        "message_container_ids": container_ids,
    }
    selected_payload["has_quarantined_occurrences"] = any(
        item.get("quarantined") for item in ordered
    )
    selected_payload["quarantined"] = not bool(non_quarantined)
    selected_payload["quarantine_reasons"] = sorted(
        {
            reason
            for item in ordered
            for reason in (item.get("quarantine_reasons") or [])
        }
    )
    if len(container_ids) > 1:
        selected_payload["quarantined"] = True
        selected_payload["quarantine_reasons"] = sorted(
            set(selected_payload["quarantine_reasons"]) | {"conflicting_exact_message_container_ids"}
        )
    if len(container_ids) > 1:
        trust_state = "conflicting"
        eligible_for_accepted_evidence = False
    elif trusted_canonical:
        trust_state = "trusted_canonical_recapture"
        eligible_for_accepted_evidence = True
    elif not non_quarantined:
        trust_state = "quarantined_only"
        eligible_for_accepted_evidence = False
    else:
        trust_state = "untrusted_noncanonical_only"
        eligible_for_accepted_evidence = False
    selected_payload["evidence_trust_state"] = trust_state
    selected_payload["eligible_for_accepted_evidence"] = eligible_for_accepted_evidence
    selected_payload["trusted_canonical_occurrence_count"] = len(trusted_canonical)
    selected_payload["quarantined_occurrence_count"] = len(ordered) - len(non_quarantined)
    selected_payload["_corpus_provenance"]["evidence_trust_state"] = trust_state
    selected_payload["_corpus_provenance"][
        "eligible_for_accepted_evidence"
    ] = eligible_for_accepted_evidence
    return selected_payload


def merge_unique_messages(
    occurrences: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    valid_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    invalid_occurrences: list[dict[str, Any]] = []
    for occurrence in occurrences:
        message_id = str(occurrence.get("message_id") or "")
        if MESSAGE_ID_RE.fullmatch(message_id):
            valid_groups[message_id].append(occurrence)
        else:
            invalid_occurrences.append(occurrence)
    messages = [
        merge_message_occurrences(message_id, rows)
        for message_id, rows in sorted(
            valid_groups.items(), key=lambda item: (snowflake_datetime(item[0]), item[0])
        )
    ]
    conflicts = [
        {
            "message_id": row["message_id"],
            "variant_fields": sorted(row.get("_field_variants", {})),
            "variant_field_count": len(row.get("_field_variants", {})),
        }
        for row in messages
        if row.get("_field_variants")
    ]
    stats = {
        "valid_unique_message_ids": len(messages),
        "invalid_message_id_occurrences": len(invalid_occurrences),
        "messages_with_field_variants": len(conflicts),
        "messages_with_quarantined_occurrences": sum(
            bool(row.get("has_quarantined_occurrences")) for row in messages
        ),
        "fully_quarantined_messages": sum(bool(row.get("quarantined")) for row in messages),
    }
    return messages, conflicts, stats


def normalize_inventory(
    inventory_path: Path | None,
    scope: Scope,
    provenance_root: Path,
    source_registry: dict[str, dict[str, Any]],
    observed_occurrences: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    if inventory_path is None:
        return {
            "provided": False,
            "source_file_id": None,
            "declared_complete": False,
            "validated_complete": False,
            "guild_id": None,
            "containers": [],
            "accessible_scope": {
                "status": "unknown_inventory_missing",
                "top_level_containers": {
                    "declared_complete": False,
                    "validated_complete": False,
                    "represented_count": 0,
                },
                "forum_threads": {
                    "declared_complete": False,
                    "validated_complete": False,
                    "observed_exact_id_count": 0,
                    "unresolved_observed_occurrence_count": 0,
                },
                "ordinary_threads": {
                    "declared_complete": False,
                    "validated_complete": False,
                    "expected_parent_audit_count": 0,
                    "audited_parent_count": 0,
                    "observed_exact_id_count": 0,
                    "unresolved_observed_occurrence_count": 0,
                },
                "post_cutoff_navigation_resnapshot": {
                    "declared_complete": False,
                    "validated_complete": False,
                },
            },
            "completeness": {
                "overall_declared_complete": False,
                "overall_validated_complete": False,
                "limitation": "No exact channel/thread inventory was supplied.",
            },
            "provenance": None,
            "validation_errors": ["channel_inventory_missing"],
        }
    inventory_path = inventory_path.resolve()
    source_file = register_source_file(
        source_registry, inventory_path, provenance_root, kind="channel_inventory"
    )
    payload = load_json_object(inventory_path, "channel inventory")
    raw_containers = payload.get("containers")
    if not isinstance(raw_containers, list):
        raw_containers = payload.get("channels")
    errors: list[str] = []
    if not isinstance(raw_containers, list):
        raw_containers = []
        errors.append("inventory_containers_not_array")

    payload_accessible_scope = (
        payload.get("accessible_scope")
        if isinstance(payload.get("accessible_scope"), dict)
        else {}
    )
    payload_completeness = (
        payload.get("completeness")
        if isinstance(payload.get("completeness"), dict)
        else {}
    )
    top_level_scope = (
        payload_accessible_scope.get("top_level_containers")
        if isinstance(payload_accessible_scope.get("top_level_containers"), dict)
        else {}
    )
    forum_scope = (
        payload_accessible_scope.get("forum_threads")
        if isinstance(payload_accessible_scope.get("forum_threads"), dict)
        else {}
    )
    ordinary_scope_present = isinstance(
        payload_accessible_scope.get("ordinary_threads"), dict
    )
    ordinary_scope = (
        payload_accessible_scope.get("ordinary_threads")
        if ordinary_scope_present
        else {}
    )
    resnapshot_scope_present = isinstance(
        payload_accessible_scope.get("post_cutoff_navigation_resnapshot"), dict
    )
    resnapshot_scope = (
        payload_accessible_scope.get("post_cutoff_navigation_resnapshot")
        if resnapshot_scope_present
        else {}
    )
    declared_complete = bool(
        payload.get("inventory_complete") is True
        or payload.get("complete") is True
        or str(payload.get("status") or "").lower() == "complete"
    )
    if not declared_complete:
        errors.append("inventory_not_declared_complete")
    inventory_guild_id = str(payload.get("guild_id") or scope.guild_id)
    if inventory_guild_id != scope.guild_id:
        errors.append("inventory_guild_id_mismatch")

    known_message_kind_tokens = {
        "text",
        "thread",
        "forum",
        "announcement",
        "news",
        "media",
        "voice",
        "stage",
    }
    containers: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, row in enumerate(raw_containers, start=1):
        if not isinstance(row, dict):
            errors.append(f"inventory_container_{index}_not_object")
            continue
        container_id = exact_id(
            first_nonempty(row, ("container_id", "channel_id", "thread_id", "id"))
        )
        if not container_id:
            errors.append(f"inventory_container_{index}_missing_exact_id")
            continue
        if container_id in seen_ids:
            errors.append(f"inventory_duplicate_container_id:{container_id}")
            continue
        seen_ids.add(container_id)
        kind = str(first_nonempty(row, ("kind", "channel_type", "type")) or "unknown")
        kind_lower = kind.lower().replace("_", " ")
        message_bearing = (
            bool(row.get("message_bearing"))
            if row.get("message_bearing") is not None
            else any(token in kind_lower for token in known_message_kind_tokens)
            and "category" not in kind_lower
        )
        count_status = str(row.get("count_status") or "").lower()
        accessible = (
            bool(row.get("accessible"))
            if row.get("accessible") is not None
            else count_status not in {"inaccessible", "forbidden", "permission_denied"}
        )
        searchable = (
            bool(row.get("searchable"))
            if row.get("searchable") is not None
            else count_status in {"", "ok", "complete"}
        )
        coverage_container_id = exact_id(
            first_nonempty(
                row,
                (
                    "coverage_container_id",
                    "covered_by_container_id",
                    "search_container_id",
                ),
            )
        ) or container_id
        coverage_start_date = row.get("coverage_start_date")
        if not coverage_start_date and row.get("channel_created_at_utc"):
            try:
                created = parse_timestamp(row.get("channel_created_at_utc"))
                coverage_start_date = max(
                    scope.start_date, created.astimezone(scope.timezone).date()
                ).isoformat()
            except Exception:
                coverage_start_date = None
        parent_container_id = exact_id(
            first_nonempty(row, ("parent_container_id", "parent_channel_id", "parent_id"))
        )
        inventory_layer = str(row.get("inventory_layer") or "").strip()
        if not inventory_layer:
            inventory_layer = (
                "declared_thread"
                if parent_container_id and "thread" in kind_lower
                else "top_level_container"
            )
        identity_input = (
            row.get("identity_provenance")
            if isinstance(row.get("identity_provenance"), dict)
            else {}
        )
        evidence_message_id = exact_id(
            first_nonempty(row, ("identity_evidence_message_id", "evidence_message_id"))
        )
        identity_method = str(
            identity_input.get("method")
            or row.get("channel_id_source")
            or "inventory_file_exact_id"
        )
        access_status = str(row.get("accessible_scope_status") or "").strip()
        if not access_status:
            if accessible and searchable:
                access_status = "accessible_and_searchable_as_of_inventory_capture"
            elif accessible:
                access_status = "accessible_not_searchable"
            else:
                access_status = "not_accessible_to_authenticated_account"
        containers.append(
            {
                "container_id": container_id,
                "name": str(first_nonempty(row, ("name", "channel_name", "thread_name")) or ""),
                "kind": kind,
                "parent_container_id": parent_container_id,
                "category_id": exact_id(row.get("category_id")),
                "category_name": row.get("category_name"),
                "inventory_layer": inventory_layer,
                "message_bearing": message_bearing,
                "accessible": accessible,
                "searchable": searchable,
                "accessible_scope_status": access_status,
                "archived": row.get("archived"),
                "locked": row.get("locked"),
                "coverage_container_id": coverage_container_id,
                "coverage_start_date": str(coverage_start_date or scope.start_date),
                "coverage_end_date": str(row.get("coverage_end_date") or scope.end_date_inclusive),
                "full_window_query": row.get("full_window_query"),
                "full_window_reported_total": normalize_int(
                    row.get("full_window_reported_total")
                ),
                "count_status": row.get("count_status"),
                "channel_created_at_utc": row.get("channel_created_at_utc"),
                "notes": row.get("notes"),
                "identity_provenance": {
                    "method": identity_method,
                    "source_file_ids": [source_file["source_file_id"]],
                    "source_occurrence_ids": [],
                    "evidence_message_ids": [evidence_message_id]
                    if evidence_message_id
                    else [],
                    "observed_at_utc": payload.get("captured_at_utc")
                    or payload.get("capture_as_of_utc"),
                },
                "accessible_scope_evidence": {
                    "source_file_id": source_file["source_file_id"],
                    "count_status": row.get("count_status"),
                    "full_window_query": row.get("full_window_query"),
                    "full_window_reported_total": normalize_int(
                        row.get("full_window_reported_total")
                    ),
                },
            }
        )

    explicit_by_id = {row["container_id"]: row for row in containers}
    forum_parent_ids = {
        row["container_id"]
        for row in containers
        if "forum" in str(row.get("kind") or "").lower()
        and row.get("inventory_layer") == "top_level_container"
    }
    thread_evidence: dict[str, dict[str, Any]] = {}
    unresolved_forum_occurrence_ids: list[str] = []
    for occurrence in observed_occurrences:
        if occurrence.get("source_kind") != "channel_segment":
            continue
        parent_id = exact_id(occurrence.get("query_container_id"))
        if parent_id not in forum_parent_ids:
            continue
        row = occurrence.get("payload")
        if not isinstance(row, dict):
            unresolved_forum_occurrence_ids.append(str(occurrence.get("occurrence_id") or ""))
            continue
        thread_id, thread_id_source = exact_row_thread_id(row)
        if not thread_id or thread_id == parent_id:
            unresolved_forum_occurrence_ids.append(str(occurrence.get("occurrence_id") or ""))
            continue
        if thread_id in explicit_by_id and thread_id not in thread_evidence:
            thread_evidence[thread_id] = {
                "parent_id": parent_id,
                "titles": Counter(),
                "source_file_ids": set(),
                "occurrence_ids": [],
                "message_ids": set(),
                "local_dates": set(),
                "methods": set(),
            }
        evidence = thread_evidence.setdefault(
            thread_id,
            {
                "parent_id": parent_id,
                "titles": Counter(),
                "source_file_ids": set(),
                "occurrence_ids": [],
                "message_ids": set(),
                "local_dates": set(),
                "methods": set(),
            },
        )
        title = str(first_nonempty(row, ("thread_title", "thread_name")) or "").strip()
        if title:
            evidence["titles"][title] += 1
        source_file_id = str(occurrence.get("source_file_id") or "")
        if source_file_id:
            evidence["source_file_ids"].add(source_file_id)
        occurrence_id = str(occurrence.get("occurrence_id") or "")
        if occurrence_id:
            evidence["occurrence_ids"].append(occurrence_id)
        message_id = exact_id(occurrence.get("message_id"))
        if message_id:
            evidence["message_ids"].add(message_id)
        local_date = str(occurrence.get("snowflake_local_date") or "")
        if DATE_RE.fullmatch(local_date):
            evidence["local_dates"].add(local_date)
        method_by_source = {
            "forum_group_header_data_list_item_id": "forum_group_header_thread_channel_id",
            "owned_reply_permalink": "owned_reply_permalink_thread_channel_id",
            "thread_id_from_forum_group_header": "forum_group_header_thread_channel_id",
            "thread_id_from_owned_reply_permalink": "owned_reply_permalink_thread_channel_id",
        }
        evidence["methods"].add(
            method_by_source.get(str(thread_id_source), "captured_row_exact_thread_id")
        )

    for thread_id in sorted(thread_evidence):
        evidence = thread_evidence[thread_id]
        parent_id = str(evidence["parent_id"])
        parent = explicit_by_id[parent_id]
        titles = sorted(
            evidence["titles"].items(), key=lambda item: (-item[1], item[0].casefold())
        )
        title = titles[0][0] if titles else ""
        identity_provenance = {
            "method": "+".join(sorted(evidence["methods"])),
            "source_file_ids": sorted(evidence["source_file_ids"]),
            "source_occurrence_ids": sorted(set(evidence["occurrence_ids"])),
            "evidence_message_ids": sorted(evidence["message_ids"]),
            "observation_count": len(evidence["occurrence_ids"]),
            "first_observed_local_date": min(evidence["local_dates"])
            if evidence["local_dates"]
            else None,
            "last_observed_local_date": max(evidence["local_dates"])
            if evidence["local_dates"]
            else None,
            "thread_title_variants": [
                {"value": value, "observation_count": count} for value, count in titles
            ],
        }
        if thread_id in explicit_by_id:
            explicit = explicit_by_id[thread_id]
            explicit["identity_provenance"]["source_file_ids"] = sorted(
                set(explicit["identity_provenance"]["source_file_ids"])
                | set(identity_provenance["source_file_ids"])
            )
            explicit["identity_provenance"]["source_occurrence_ids"] = identity_provenance[
                "source_occurrence_ids"
            ]
            explicit["identity_provenance"]["evidence_message_ids"] = sorted(
                set(explicit["identity_provenance"]["evidence_message_ids"])
                | set(identity_provenance["evidence_message_ids"])
            )
            explicit["identity_provenance"]["captured_row_evidence"] = identity_provenance
            continue
        thread = {
            "container_id": thread_id,
            "name": title,
            "kind": "forum thread",
            "parent_container_id": parent_id,
            "category_id": parent.get("category_id"),
            "category_name": parent.get("category_name"),
            "inventory_layer": "observed_forum_thread",
            "message_bearing": True,
            "accessible": True,
            "searchable": True,
            "accessible_scope_status": "observed_accessible_in_authenticated_parent_forum_search",
            "archived": None,
            "locked": None,
            "coverage_container_id": parent_id,
            "coverage_start_date": parent.get("coverage_start_date") or scope.start_date.isoformat(),
            "coverage_end_date": parent.get("coverage_end_date")
            or scope.end_date_inclusive.isoformat(),
            "full_window_query": parent.get("full_window_query"),
            "full_window_reported_total": None,
            "count_status": "observed_via_parent_forum_search",
            "channel_created_at_utc": None,
            "notes": (
                "Exact thread ID observed in a captured premium-journals row. "
                "This proves identity/access for the observation, not complete active/archived thread enumeration."
            ),
            "identity_provenance": identity_provenance,
            "accessible_scope_evidence": {
                "parent_forum_container_id": parent_id,
                "parent_coverage_container_id": parent_id,
                "archive_enumeration_complete": False,
            },
        }
        containers.append(thread)
        explicit_by_id[thread_id] = thread
        seen_ids.add(thread_id)

    top_level_rows = [
        row for row in containers if row.get("inventory_layer") == "top_level_container"
    ]
    observed_thread_rows = [
        row for row in containers if row.get("inventory_layer") == "observed_forum_thread"
    ]
    ordinary_thread_rows = [
        row
        for row in containers
        if row.get("inventory_layer")
        in {"observed_ordinary_thread", "declared_ordinary_thread"}
    ]
    expected_top_level_count = normalize_int(
        first_nonempty(
            top_level_scope,
            ("expected_count", "declared_count", "container_count"),
        )
    )
    top_level_declared_complete = bool(
        top_level_scope.get("declared_complete") is True
        or payload_completeness.get("top_level_exact_container_inventory_complete") is True
        or (declared_complete and not forum_parent_ids)
    )
    top_level_validated_complete = bool(
        top_level_declared_complete
        and (
            expected_top_level_count is None
            or expected_top_level_count == len(top_level_rows)
        )
    )
    if top_level_declared_complete and expected_top_level_count is not None:
        if expected_top_level_count != len(top_level_rows):
            errors.append(
                "top_level_inventory_count_mismatch:"
                f"declared={expected_top_level_count},represented={len(top_level_rows)}"
            )

    forum_declared_complete = bool(
        forum_scope.get("declared_complete") is True
        or payload_completeness.get("forum_thread_enumeration_complete") is True
    )
    forum_completion_evidence = forum_scope.get("completion_evidence")
    if not forum_completion_evidence:
        forum_completion_evidence = forum_scope.get("verification_method")
    forum_validated_complete = bool(
        not forum_parent_ids
        or (forum_declared_complete and forum_completion_evidence)
    )
    if forum_parent_ids and not forum_validated_complete:
        errors.append("forum_thread_inventory_completeness_not_proven")
    ordinary_declared_complete = bool(
        ordinary_scope.get("declared_complete") is True
        or payload_completeness.get("ordinary_thread_enumeration_complete") is True
    )
    ordinary_completion_evidence = ordinary_scope.get("completion_evidence")
    ordinary_validated_complete = bool(
        not ordinary_scope_present
        or (ordinary_declared_complete and ordinary_completion_evidence)
    )
    if ordinary_scope_present and not ordinary_validated_complete:
        errors.append("ordinary_thread_inventory_completeness_not_proven")
    resnapshot_declared_complete = bool(
        resnapshot_scope.get("declared_complete") is True
        or payload_completeness.get(
            "post_cutoff_authenticated_navigation_resnapshot_complete"
        )
        is True
    )
    resnapshot_completion_evidence = resnapshot_scope.get("completion_evidence")
    resnapshot_validated_complete = bool(
        not resnapshot_scope_present
        or (resnapshot_declared_complete and resnapshot_completion_evidence)
    )
    if resnapshot_scope_present and not resnapshot_validated_complete:
        errors.append("post_cutoff_navigation_resnapshot_completeness_not_proven")
    validated_complete = bool(
        declared_complete
        and top_level_validated_complete
        and forum_validated_complete
        and ordinary_validated_complete
        and resnapshot_validated_complete
        and not errors
    )

    payload_provenance = (
        payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    )
    return {
        "provided": True,
        "source_file_id": source_file["source_file_id"],
        "source_file_relative_path": source_file["relative_path"],
        "source_file_size_bytes": source_file["size_bytes"],
        "source_file_sha256": source_file["sha256"],
        "declared_complete": declared_complete,
        "validated_complete": validated_complete,
        "guild_id": inventory_guild_id,
        "captured_at_utc": payload.get("captured_at_utc") or payload.get("capture_as_of_utc"),
        "containers": containers,
        "container_count": len(containers),
        "top_level_container_count": len(top_level_rows),
        "observed_forum_thread_count": len(observed_thread_rows),
        "ordinary_thread_count": len(ordinary_thread_rows),
        "message_bearing_accessible_searchable_count": sum(
            row["message_bearing"] and row["accessible"] and row["searchable"]
            for row in containers
        ),
        "accessible_scope": {
            "status": "complete" if validated_complete else "partial",
            "definition": payload.get("scope_definition"),
            "authenticated_account_only": True,
            "source_scope": payload.get("source_scope") or "discord_only",
            "top_level_containers": {
                "declared_complete": top_level_declared_complete,
                "validated_complete": top_level_validated_complete,
                "expected_count": expected_top_level_count,
                "represented_count": len(top_level_rows),
                "status": "complete" if top_level_validated_complete else "partial",
                "evidence": top_level_scope.get("evidence"),
            },
            "forum_threads": {
                "parent_forum_count": len(forum_parent_ids),
                "parent_forum_container_ids": sorted(forum_parent_ids),
                "declared_complete": forum_declared_complete,
                "validated_complete": forum_validated_complete,
                "status": "complete"
                if forum_validated_complete
                else "partial_observed_ids_only",
                "observed_exact_id_count": len(observed_thread_rows),
                "observed_exact_ids": sorted(
                    row["container_id"] for row in observed_thread_rows
                ),
                "unresolved_observed_occurrence_count": len(
                    set(filter(None, unresolved_forum_occurrence_ids))
                ),
                "unresolved_observed_occurrence_ids": sorted(
                    set(filter(None, unresolved_forum_occurrence_ids))
                ),
                "discovery_method": forum_scope.get("discovery_method")
                or "captured forum search rows with exact row-owned thread evidence",
                "remaining_limitation": (
                    None
                    if forum_validated_complete
                    else "Active and archived forum thread enumeration is not proven complete; rows without exact thread evidence remain attributed to the parent forum."
                ),
            },
            "ordinary_threads": {
                "declared_complete": ordinary_declared_complete,
                "validated_complete": ordinary_validated_complete,
                "status": "complete" if ordinary_validated_complete else "partial",
                "expected_parent_audit_count": normalize_int(
                    ordinary_scope.get("expected_parent_audit_count")
                ),
                "audited_parent_count": normalize_int(
                    ordinary_scope.get("audited_parent_count")
                ),
                "audited_parent_ids": ordinary_scope.get("audited_parent_ids") or [],
                "observed_exact_id_count": len(ordinary_thread_rows),
                "exact_thread_count": normalize_int(
                    ordinary_scope.get("exact_thread_count")
                ),
                "unresolved_observed_occurrence_count": normalize_int(
                    ordinary_scope.get("unresolved_observed_occurrence_count")
                ),
                "completion_evidence": ordinary_completion_evidence,
                "remaining_limitation": ordinary_scope.get("remaining_limitation"),
            },
            "post_cutoff_navigation_resnapshot": {
                "declared_complete": resnapshot_declared_complete,
                "validated_complete": resnapshot_validated_complete,
                "status": "complete" if resnapshot_validated_complete else "partial",
                "required_capture_at_or_after_utc": resnapshot_scope.get(
                    "required_capture_at_or_after_utc"
                ),
                "completion_evidence": resnapshot_completion_evidence,
            },
        },
        "completeness": {
            "overall_declared_complete": declared_complete,
            "overall_validated_complete": validated_complete,
            "top_level_exact_container_inventory_complete": top_level_validated_complete,
            "forum_thread_enumeration_complete": forum_validated_complete,
            "ordinary_thread_enumeration_complete": ordinary_validated_complete,
            "post_cutoff_authenticated_navigation_resnapshot_complete": resnapshot_validated_complete,
            "rule": (
                "Overall inventory completeness requires an exact top-level inventory and, "
                "when a forum exists, independently evidenced active/archived forum enumeration, "
                "a complete ordinary-thread parent audit, and a post-cutoff authenticated navigation "
                "resnapshot. Observed thread IDs alone never prove archive completeness."
            ),
        },
        "provenance": {
            "source_file_id": source_file["source_file_id"],
            "source_file_relative_path": source_file["relative_path"],
            "source_file_size_bytes": source_file["size_bytes"],
            "source_file_sha256": source_file["sha256"],
            "inventory_method": payload.get("inventory_method"),
            "capture_as_of_utc": payload.get("captured_at_utc")
            or payload.get("capture_as_of_utc"),
            "declared_provenance": copy.deepcopy(payload_provenance),
        },
        "validation_errors": errors,
    }


def build_coverage(
    scope: Scope,
    inventory: dict[str, Any],
    segments: list[dict[str, Any]],
    file_failures: list[dict[str, Any]],
) -> dict[str, Any]:
    complete_dates_by_container: dict[str, set[dt.date]] = defaultdict(set)
    incomplete_by_container: dict[str, list[str]] = defaultdict(list)
    segment_containers: set[str] = set()
    unbound_segments: list[str] = []
    for segment in segments:
        container_id = segment.get("query_container_id")
        if not container_id:
            unbound_segments.append(segment["segment_id"])
            continue
        container_id = str(container_id)
        segment_containers.add(container_id)
        if segment.get("computed_complete"):
            try:
                start = parse_date(segment.get("start_date"), "coverage segment start")
                end = parse_date(segment.get("end_date"), "coverage segment end")
            except CorpusError:
                incomplete_by_container[container_id].append(segment["segment_id"])
                continue
            for value in dates_between(max(start, scope.start_date), min(end, scope.end_date_inclusive)):
                complete_dates_by_container[container_id].add(value)
        else:
            incomplete_by_container[container_id].append(segment["segment_id"])

    expected_inventory_rows = [
        row
        for row in inventory.get("containers", [])
        if row.get("message_bearing") and row.get("accessible") and row.get("searchable")
    ]
    coverage_rows: list[dict[str, Any]] = []
    expected_coverage_ids: set[str] = set()
    all_expected_dates: set[dt.date] = set(dates_between(scope.start_date, scope.end_date_inclusive))
    for row in expected_inventory_rows:
        coverage_id = str(row.get("coverage_container_id") or row["container_id"])
        expected_coverage_ids.add(coverage_id)
        try:
            expected_start = max(
                scope.start_date,
                parse_date(row.get("coverage_start_date"), "inventory coverage start"),
            )
            expected_end = min(
                scope.end_date_inclusive,
                parse_date(row.get("coverage_end_date"), "inventory coverage end"),
            )
        except CorpusError:
            expected_start = scope.start_date
            expected_end = scope.end_date_inclusive
        expected_dates = set(dates_between(expected_start, expected_end))
        complete_dates = complete_dates_by_container.get(coverage_id, set()) & expected_dates
        missing_dates = expected_dates - complete_dates
        coverage_rows.append(
            {
                "container_id": row["container_id"],
                "coverage_container_id": coverage_id,
                "name": row.get("name"),
                "kind": row.get("kind"),
                "expected_start_date": expected_start.isoformat(),
                "expected_end_date": expected_end.isoformat(),
                "expected_day_count": len(expected_dates),
                "complete_day_count": len(complete_dates),
                "missing_day_count": len(missing_dates),
                "missing_date_ranges": compress_date_ranges(missing_dates),
                "incomplete_segment_ids": sorted(incomplete_by_container.get(coverage_id, [])),
                "status": "complete" if not missing_dates else "gap",
            }
        )

    global_gaps: list[dict[str, Any]] = []
    if not inventory.get("provided"):
        global_gaps.append(
            {
                "gap_type": "channel_inventory_missing",
                "detail": "No exact server channel/thread inventory was supplied; whole-server coverage cannot be certified.",
            }
        )
    for error in inventory.get("validation_errors", []):
        global_gaps.append({"gap_type": "inventory_validation_error", "detail": error})
    if file_failures:
        global_gaps.append(
            {
                "gap_type": "unreadable_or_invalid_segment_files",
                "count": len(file_failures),
                "source_file_ids": [row["source_file_id"] for row in file_failures],
            }
        )
    if unbound_segments:
        global_gaps.append(
            {
                "gap_type": "segments_without_exact_query_container_id",
                "count": len(unbound_segments),
                "segment_ids": unbound_segments,
            }
        )
    unlisted = sorted(segment_containers - expected_coverage_ids) if inventory.get("declared_complete") else []
    if unlisted:
        global_gaps.append(
            {
                "gap_type": "segment_containers_missing_from_declared_complete_inventory",
                "container_ids": unlisted,
            }
        )
    if not segments:
        global_gaps.append(
            {
                "gap_type": "no_channel_segment_files",
                "detail": "The canonical raw/channel_segments input contains no parseable segment files.",
            }
        )
    covered_global_dates = set().union(*complete_dates_by_container.values()) if complete_dates_by_container else set()
    return {
        "segments": segments,
        "containers": coverage_rows,
        "gaps": global_gaps,
        "summary": {
            "segment_count": len(segments),
            "complete_segment_count": sum(row.get("status") == "complete" for row in segments),
            "verified_empty_segment_count": sum(
                row.get("status") == "verified_empty" for row in segments
            ),
            "partial_or_failed_segment_count": sum(
                row.get("status") not in {"complete", "verified_empty"} for row in segments
            )
            + len(file_failures),
            "inventory_expected_container_count": len(expected_inventory_rows),
            "complete_inventory_container_count": sum(
                row["status"] == "complete" for row in coverage_rows
            ),
            "containers_with_gaps": sum(row["status"] != "complete" for row in coverage_rows),
            "calendar_days_seen_in_any_complete_segment": len(covered_global_dates & all_expected_dates),
            "requested_calendar_days": scope.local_day_count,
        },
        "file_failures": file_failures,
    }


def quarantine_summary(
    occurrences: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    migration_sidecars: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quarantined = [item for item in occurrences if item.get("quarantined")]
    reason_counts = Counter(
        reason for item in quarantined for reason in (item.get("quarantine_reasons") or [])
    )
    items = [
        {
            "occurrence_id": item["occurrence_id"],
            "message_id": item.get("message_id"),
            "source_kind": item.get("source_kind"),
            "source_file_id": item.get("source_file_id"),
            "segment_id": item.get("segment_id"),
            "page_number": item.get("page_number"),
            "result_index": item.get("result_index"),
            "snowflake_timestamp_utc": item.get("snowflake_timestamp_utc"),
            "captured_timestamp_utc": item.get("captured_timestamp_utc"),
            "timestamp_delta_ms": item.get("timestamp_delta_ms"),
            "reasons": item.get("quarantine_reasons"),
            "migration_source": bool(item.get("migration_source")),
            "migration_occurrence_id": item.get("migration_occurrence_id"),
            "migration_quarantined": bool(item.get("migration_quarantined")),
            "migration_quarantine_sources": item.get("migration_quarantine_sources") or [],
            "historical_disappeared_certified": bool(
                item.get("historical_disappeared_certified")
            ),
            "historical_reconciliation_note_source_file_id": item.get(
                "historical_reconciliation_note_source_file_id"
            ),
        }
        for item in quarantined
    ]
    trusted_channel_ids = {
        str(item.get("message_id"))
        for item in occurrences
        if is_trusted_canonical_occurrence(item)
        and MESSAGE_ID_RE.fullmatch(str(item.get("message_id") or ""))
    }
    matched_migration_ids = {
        str(item.get("migration_occurrence_id"))
        for item in occurrences
        if item.get("migration_occurrence_id")
    }
    sidecar_records = (
        migration_sidecars.get("records", [])
        if isinstance(migration_sidecars, dict)
        else []
    )
    unmatched_sidecar_records = [
        copy.deepcopy(row)
        for row in sidecar_records
        if str(row.get("occurrence_id") or "") not in matched_migration_ids
    ]
    unmatched_valid_message_ids = {
        str(message_id)
        for row in unmatched_sidecar_records
        for message_id in (row.get("message_ids") or [])
        if MESSAGE_ID_RE.fullmatch(str(message_id or ""))
        and str(message_id) not in trusted_channel_ids
    }
    certified_historical_ids = {
        str(item.get("message_id"))
        for item in quarantined
        if item.get("historical_disappeared_certified")
        and MESSAGE_ID_RE.fullmatch(str(item.get("message_id") or ""))
    }
    unresolved_ids = sorted(
        {
            str(item.get("message_id"))
            for item in quarantined
            if MESSAGE_ID_RE.fullmatch(str(item.get("message_id") or ""))
            and str(item.get("message_id")) not in trusted_channel_ids
            and str(item.get("message_id")) not in certified_historical_ids
        }
        | (unmatched_valid_message_ids - certified_historical_ids)
    )
    invalid_id_count = sum(
        not MESSAGE_ID_RE.fullmatch(str(item.get("message_id") or "")) for item in quarantined
    )
    if isinstance(migration_sidecars, dict):
        migration_sidecars["matched_occurrence_count"] = len(
            matched_migration_ids
            & {str(row.get("occurrence_id") or "") for row in sidecar_records}
        )
        migration_sidecars["unmatched_occurrence_count"] = len(unmatched_sidecar_records)
        migration_sidecars["unmatched_records"] = unmatched_sidecar_records
    return {
        "occurrence_count": len(quarantined),
        "channel_segment_occurrence_count": sum(
            item.get("source_kind") == "channel_segment" for item in quarantined
        ),
        "legacy_occurrence_count": sum(
            item.get("source_kind") != "channel_segment" for item in quarantined
        ),
        "migration_quarantined_occurrence_count": sum(
            bool(item.get("migration_quarantined")) for item in quarantined
        ),
        "fully_quarantined_message_count": sum(bool(row.get("quarantined")) for row in messages),
        "messages_ineligible_for_accepted_evidence": sum(
            not bool(row.get("eligible_for_accepted_evidence")) for row in messages
        ),
        "reason_counts": dict(sorted(reason_counts.items())),
        "certified_historically_unavailable_message_ids": sorted(
            certified_historical_ids
        ),
        "certified_historically_unavailable_message_count": len(
            certified_historical_ids
        ),
        "historical_reconciled_occurrence_count": sum(
            item.get("source_kind") == "historical_reconciled_segment"
            for item in occurrences
        ),
        "unresolved_valid_message_ids": unresolved_ids,
        "invalid_message_id_occurrence_count": invalid_id_count,
        "unmatched_migration_sidecar_record_count": len(unmatched_sidecar_records),
        "unmatched_migration_sidecar_records": unmatched_sidecar_records,
        "invalid_migration_sidecar_record_count": int(
            (migration_sidecars or {}).get("invalid_record_count") or 0
        ),
        "occurrences": items,
    }


def summarize_timestamp_scope_integrity(
    segments: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    mode_counts: Counter[str] = Counter()
    unresolved: list[dict[str, Any]] = []
    sidecars: list[dict[str, Any]] = []
    sidecar_errors: list[dict[str, Any]] = []
    unused_records: list[dict[str, Any]] = []
    audited_message_count = 0
    for segment in segments:
        audit = segment.get("timestamp_scope_integrity")
        if not isinstance(audit, dict):
            sidecar_errors.append(
                {
                    "segment_id": segment.get("segment_id"),
                    "error": "timestamp_scope_integrity_missing",
                }
            )
            continue
        audited_message_count += int(audit.get("message_count") or 0)
        for mode, count in (audit.get("mode_counts") or {}).items():
            mode_counts[str(mode)] += int(count or 0)
        unresolved.extend(
            {
                "segment_id": segment.get("segment_id"),
                "source_file_id": segment.get("source_file_id"),
                **row,
            }
            for row in (audit.get("unresolved") or [])
            if isinstance(row, dict)
        )
        sidecar = audit.get("sidecar")
        if isinstance(sidecar, dict) and sidecar.get("provided") is True:
            sidecar_record = {
                **copy.deepcopy(sidecar),
                "source_file_id": segment.get(
                    "timestamp_scope_revalidation_source_file_id"
                ),
                "evidence_source_file_ids": copy.deepcopy(
                    segment.get("timestamp_scope_evidence_source_file_ids") or []
                ),
            }
            sidecars.append(sidecar_record)
        for error in audit.get("sidecar_errors") or []:
            sidecar_errors.append(
                {
                    "segment_id": segment.get("segment_id"),
                    "error": str(error),
                }
            )
        for message_id in audit.get("unused_revalidation_message_ids") or []:
            unused_records.append(
                {
                    "segment_id": segment.get("segment_id"),
                    "message_id": str(message_id),
                }
            )
    external_mode = (
        f"{timestamp_scope_revalidation.PIN_FALLBACK_SOURCE}_sidecar_revalidated"
    )
    external_message_count = int(mode_counts.get(external_mode) or 0)
    used_record_count = sum(
        int(row.get("used_record_count") or 0) for row in sidecars
    )
    sidecar_bindings_valid = all(
        row.get("valid") is True
        and row.get("content_hash_bound") is True
        and row.get("source_file_id")
        and row.get("sidecar_sha256")
        and row.get("evidence_source_file_ids")
        for row in sidecars
    )
    passed = bool(
        not unresolved
        and not sidecar_errors
        and not unused_records
        and external_message_count == used_record_count
        and sidecar_bindings_valid
    )
    return {
        "schema_version": "1.0.0",
        "passed": passed,
        "content_hash_bound": passed,
        "audited_segment_count": len(segments),
        "audited_message_count": audited_message_count,
        "mode_counts": dict(sorted(mode_counts.items())),
        "external_revalidation_message_count": external_message_count,
        "external_revalidation_used_record_count": used_record_count,
        "sidecar_count": len(sidecars),
        "invalid_sidecar_count": len(sidecar_errors),
        "unused_revalidation_record_count": len(unused_records),
        "unresolved_message_count": len(unresolved),
        "unresolved_messages": unresolved[:100],
        "sidecar_errors": sidecar_errors[:100],
        "unused_revalidation_records": unused_records[:100],
        "sidecars": sidecars,
        "policy": (
            "timestamp_scope_exact=true requires the exact message-timestamp-<id> "
            "ARIA token; otherwise only an exact Stage/poll/pinned fallback is "
            "accepted. Adjacent recovery sidecars must bind final segment SHA-256, "
            "message row SHA-256, and preserved DOM evidence SHA-256."
        ),
    }


def summarize_executed_command_reply_provenance_integrity(
    segments: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    audits: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    candidate_message_ids: list[str] = []
    expected_segment_count = 0
    candidate_count = 0
    accepted_exact_context_count = 0
    audited_message_count = 0
    for segment in segments:
        audit = segment.get("executed_command_reply_provenance_integrity")
        if not isinstance(audit, dict):
            failures.append(
                {
                    "segment_id": segment.get("segment_id"),
                    "reasons": ["executed_command_reply_provenance_audit_missing"],
                }
            )
            continue
        audits.append(audit)
        audited_message_count += int(audit.get("audited_message_count") or 0)
        candidate_count += int(audit.get("candidate_count") or 0)
        accepted_exact_context_count += int(
            audit.get("accepted_exact_context_count") or 0
        )
        expected_segment_count += int(
            audit.get("expected_message_present") is True
        )
        candidate_message_ids.extend(
            str(value)
            for value in audit.get("candidate_message_ids") or []
            if str(value)
        )
        failures.extend(
            {
                "segment_id": segment.get("segment_id"),
                "source_file_id": segment.get("source_file_id"),
                **row,
            }
            for row in audit.get("failures") or []
            if isinstance(row, dict)
        )
    legacy_anchor_id = (
        reply_provenance_contract.EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID
    )
    legacy_anchor_count = candidate_message_ids.count(legacy_anchor_id)
    candidate_ids_valid = bool(
        len(candidate_message_ids) == candidate_count
        and len(set(candidate_message_ids)) == len(candidate_message_ids)
        and all(
            reply_provenance_contract.DISCORD_ID_RE.fullmatch(message_id)
            for message_id in candidate_message_ids
        )
    )
    expected_segment_present = bool(
        expected_segment_count == 1 and legacy_anchor_count == 1
    )
    release_shape_valid = bool(
        expected_segment_present
        and candidate_count >= 1
        and accepted_exact_context_count == candidate_count
        and candidate_ids_valid
    )
    empty_non_release_shape_valid = bool(
        expected_segment_count == 0
        and candidate_count == 0
        and accepted_exact_context_count == 0
        and not candidate_message_ids
    )
    passed = bool(
        not failures
        and (release_shape_valid or empty_non_release_shape_valid)
    )
    return {
        "schema_version": "1.0.0",
        "passed": passed,
        "audited_segment_count": len(audits),
        "audited_message_count": audited_message_count,
        "expected_segment_count": expected_segment_count,
        "expected_segment_present": expected_segment_present,
        "legacy_anchor_message_id": legacy_anchor_id,
        "legacy_anchor_count": legacy_anchor_count,
        "candidate_count": candidate_count,
        "accepted_exact_context_count": accepted_exact_context_count,
        "failure_count": len(failures),
        "candidate_message_ids": candidate_message_ids,
        "failures": failures[:100],
        "policy": (
            "The June 30-July 6 Questions segment must preserve the original "
            "Wordle command row as a legacy anchor. Any number of additional "
            "Wordle command rows are accepted only through the exact Discord "
            "application-command DOM contract; message IDs are not allowlisted."
        ),
    }


def make_release_gates(
    *,
    scope: Scope,
    data_cutoff: dt.datetime,
    inventory: dict[str, Any],
    coverage: dict[str, Any],
    quarantine: dict[str, Any],
    source_files: list[dict[str, Any]],
    legacy: dict[str, Any],
    messages: list[dict[str, Any]],
    historical_reconciliation: dict[str, Any] | None = None,
    attachment_archive: dict[str, Any] | None = None,
    relevance_policy: dict[str, Any] | None = None,
    timestamp_scope_integrity: dict[str, Any] | None = None,
    executed_command_reply_provenance_integrity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    coverage_rows = coverage.get("containers", [])
    segment_rows = coverage.get("segments", [])
    legacy_ids_preserved = True
    if legacy.get("provided"):
        legacy_ids_preserved = int(legacy.get("unique_message_ids") or 0) <= len(messages)
    checks: list[tuple[str, bool, Any]] = [
        (
            "exact_target_window",
            scope.start_date.isoformat() == DEFAULT_START_DATE
            and scope.end_date_inclusive.isoformat() == DEFAULT_END_DATE_INCLUSIVE
            and scope.timezone_name == DEFAULT_TIMEZONE
            and scope.local_day_count == 201,
            scope.as_dict(),
        ),
        (
            "requested_window_has_ended_at_data_cutoff",
            data_cutoff >= scope.utc_end_exclusive,
            {
                "data_cutoff_utc": iso_z(data_cutoff),
                "required_through_utc": iso_z(scope.utc_end_exclusive),
            },
        ),
        ("channel_inventory_provided", bool(inventory.get("provided")), inventory.get("source_file_id")),
        (
            "channel_inventory_declared_complete_and_valid",
            bool(inventory.get("validated_complete"))
            and inventory.get("guild_id") == scope.guild_id,
            {
                "validation_errors": inventory.get("validation_errors"),
                "completeness": inventory.get("completeness"),
            },
        ),
        (
            "all_critical_quarantines_have_trusted_recaptures",
            not quarantine.get("unresolved_valid_message_ids")
            and int(quarantine.get("invalid_message_id_occurrence_count") or 0) == 0
            and int(quarantine.get("invalid_migration_sidecar_record_count") or 0) == 0
            and int(quarantine.get("unmatched_migration_sidecar_record_count") or 0) == 0,
            {
                "unresolved_valid_message_ids": quarantine.get("unresolved_valid_message_ids"),
                "invalid_message_id_occurrence_count": quarantine.get(
                    "invalid_message_id_occurrence_count"
                ),
                "invalid_migration_sidecar_record_count": quarantine.get(
                    "invalid_migration_sidecar_record_count"
                ),
                "unmatched_migration_sidecar_record_count": quarantine.get(
                    "unmatched_migration_sidecar_record_count"
                ),
            },
        ),
        (
            "all_source_files_are_hashed_and_portable",
            bool(source_files)
            and all(
                row.get("exists")
                and row.get("sha256")
                and row.get("size_bytes") is not None
                and not Path(str(row.get("relative_path") or "")).is_absolute()
                for row in source_files
            ),
            [row["source_file_id"] for row in source_files if not row.get("sha256")],
        ),
        (
            "timestamp_scope_integrity",
            bool(
                (timestamp_scope_integrity or {}).get("passed") is True
                and (timestamp_scope_integrity or {}).get("content_hash_bound")
                is True
                and int(
                    (timestamp_scope_integrity or {}).get(
                        "unresolved_message_count"
                    )
                    or 0
                )
                == 0
                and int(
                    (timestamp_scope_integrity or {}).get("invalid_sidecar_count")
                    or 0
                )
                == 0
                and int(
                    (timestamp_scope_integrity or {}).get(
                        "unused_revalidation_record_count"
                    )
                    or 0
                )
                == 0
            ),
            copy.deepcopy(timestamp_scope_integrity or {}),
        ),
        (
            "executed_command_reply_provenance_integrity",
            not reply_provenance_contract.release_executed_command_integrity_errors(
                {
                    "executed_command_reply_provenance_integrity": (
                        executed_command_reply_provenance_integrity or {}
                    ),
                    "release_gates": [
                        {
                            "gate": "executed_command_reply_provenance_integrity",
                            "passed": bool(
                                (executed_command_reply_provenance_integrity or {}).get(
                                    "passed"
                                )
                            ),
                            "detail": copy.deepcopy(
                                executed_command_reply_provenance_integrity or {}
                            ),
                        }
                    ],
                },
                allow_empty_non_release=True,
            ),
            copy.deepcopy(executed_command_reply_provenance_integrity or {}),
        ),
        (
            "historical_reconciliation_notes_valid",
            (
                int(
                    (historical_reconciliation or {}).get("invalid_note_count")
                    or 0
                )
                == 0
                # Duplicate notes for the same legacy/current byte binding are
                # ambiguous provenance and must be reconciled before release.
                and int(
                    (historical_reconciliation or {}).get(
                        "duplicate_binding_count"
                    )
                    or 0
                )
                == 0
            ),
            {
                "provided": bool((historical_reconciliation or {}).get("provided")),
                "valid_note_count": int(
                    (historical_reconciliation or {}).get("valid_note_count") or 0
                ),
                "invalid_note_count": int(
                    (historical_reconciliation or {}).get("invalid_note_count") or 0
                ),
                "duplicate_binding_count": int(
                    (historical_reconciliation or {}).get("duplicate_binding_count")
                    or 0
                ),
            },
        ),
        (
            "legacy_message_ids_preserved_when_supplied",
            legacy_ids_preserved,
            {
                "legacy_unique_message_ids": legacy.get("unique_message_ids"),
                "corpus_unique_message_ids": len(messages),
            },
        ),
        (
            "discord_attachment_terminal_coverage",
            bool(
                (attachment_archive or {})
                .get("release_gate", {})
                .get("terminal_coverage_complete")
            ),
            {
                "provided": bool((attachment_archive or {}).get("provided")),
                "expected_owned_attachment_count": (attachment_archive or {}).get(
                    "expected_owned_attachment_count"
                ),
                "manifest_attachment_count": (attachment_archive or {}).get(
                    "manifest_attachment_count"
                ),
                "entry_set_parity": (attachment_archive or {}).get("entry_set_parity"),
                "counts": (attachment_archive or {}).get("counts"),
                "verification": (attachment_archive or {}).get("verification"),
                "literal_release_rule": (attachment_archive or {}).get("policy", {}).get(
                    "literal_release_rule"
                ),
            },
        ),
        (
            "discord_attachment_literal_release_complete",
            bool(
                (attachment_archive or {}).get("release_gate", {}).get("passed")
                and (attachment_archive or {})
                .get("release_gate", {})
                .get("literal_release_complete")
            ),
            {
                "terminal_coverage_complete": (attachment_archive or {})
                .get("release_gate", {})
                .get("terminal_coverage_complete"),
                "literal_release_complete": (attachment_archive or {})
                .get("release_gate", {})
                .get("literal_release_complete"),
                "failed_count": (attachment_archive or {})
                .get("counts", {})
                .get("failed"),
            },
        ),
    ]
    if relevance_policy and relevance_policy.get("enabled"):
        required_partial = relevance_release_policy.policy_required_partial_segments(
            relevance_policy
        )
        policy_gate_by_id = {
            str(row.get("gate_id")): row
            for row in relevance_policy.get("hard_gates", [])
        }
        capture_gate_ids = {
            "full_capture_segment_coverage",
            "targeted_query_matrix",
            "residual_audit",
        }
        checks[4:4] = [
            (
                "relevance_collection_plan_validated",
                bool(relevance_policy.get("plan_valid")),
                relevance_policy.get("plan_validation"),
            ),
            (
                "policy_appropriate_capture_coverage",
                all(
                    policy_gate_by_id.get(gate_id, {}).get("passed")
                    for gate_id in capture_gate_ids
                ),
                {
                    gate_id: policy_gate_by_id.get(gate_id)
                    for gate_id in sorted(capture_gate_ids)
                },
            ),
            (
                "all_policy_required_segment_files_strictly_complete",
                bool(segment_rows)
                and not coverage.get("file_failures")
                and not required_partial,
                {
                    "incomplete_required_segment_ids": [
                        row.get("segment_id") for row in required_partial
                    ],
                    "diagnostic_partial_targeted_full_capture_count": relevance_policy.get(
                        "diagnostic_partial_targeted_full_capture_count"
                    ),
                    "file_failures": coverage.get("file_failures"),
                },
            ),
            (
                "all_relevance_plan_hard_gates_passed",
                bool(relevance_policy.get("release_ready")),
                relevance_policy.get("hard_gates"),
            ),
        ]
    else:
        checks[4:4] = [
            (
                "every_accessible_message_container_has_full_date_coverage",
                bool(coverage_rows)
                and all(row.get("status") == "complete" for row in coverage_rows),
                [
                    {
                        "container_id": row.get("container_id"),
                        "missing_date_ranges": row.get("missing_date_ranges"),
                    }
                    for row in coverage_rows
                    if row.get("status") != "complete"
                ],
            ),
            (
                "all_channel_segment_files_strictly_complete",
                bool(segment_rows)
                and not coverage.get("file_failures")
                and all(row.get("computed_complete") for row in segment_rows),
                {
                    "incomplete_segment_ids": [
                        row.get("segment_id")
                        for row in segment_rows
                        if not row.get("computed_complete")
                    ],
                    "file_failures": coverage.get("file_failures"),
                },
            ),
            (
                "no_global_coverage_gaps",
                not coverage.get("gaps"),
                coverage.get("gaps"),
            ),
        ]
    return [
        {"gate": name, "passed": bool(passed), "detail": detail}
        for name, passed, detail in checks
    ]


def sanitize_occurrence_for_output(occurrence: dict[str, Any]) -> dict[str, Any]:
    # All path values in normalized provenance are relative. The original row
    # payload can still contain a legacy `_merge_provenance` object with absolute
    # paths, so remove only that redundant object; its variants and source
    # descriptors have already been normalized into first-class records.
    output = copy.deepcopy(occurrence)
    payload = output.get("payload")
    if isinstance(payload, dict):
        payload.pop("_merge_provenance", None)
    return output


def progress_manifest_segment_directories(
    progress_path: Path,
    progress: dict[str, Any],
) -> dict[str, list[Path]]:
    """Resolve only provenance-listed raw segment parents within the declared root."""

    declared_root = Path(str(progress.get("root") or ""))
    if not declared_root.is_absolute():
        declared_root = progress_path.resolve().parent.parent / declared_root
    declared_root = declared_root.resolve()
    result: dict[str, set[Path]] = {
        "channel_capture": set(),
        "relevance_query": set(),
        "residual_audit": set(),
    }
    artifacts = progress.get("artifacts")
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
        result[role].add(candidate.parent)
    return {
        key: sorted(values, key=lambda path: path.as_posix().casefold())
        for key, values in result.items()
    }


def unique_segment_directory_groups(
    groups: dict[str, Sequence[Path]],
) -> dict[str, list[Path]]:
    normalized: dict[str, list[Path]] = {}
    owner: dict[Path, str] = {}
    for role, values in groups.items():
        resolved_values: list[Path] = []
        for value in values:
            resolved = Path(value).resolve()
            previous = owner.get(resolved)
            if previous and previous != role:
                raise CorpusError(
                    f"Segment directory {resolved} was assigned to both {previous} and {role}"
                )
            owner[resolved] = role
            if resolved not in resolved_values:
                resolved_values.append(resolved)
        normalized[role] = resolved_values
    return normalized


def build_corpus(
    *,
    segment_dirs: Sequence[Path],
    relevance_segment_dirs: Sequence[Path] = (),
    audit_segment_dirs: Sequence[Path] = (),
    inventory_path: Path | None = None,
    authorized_scope_path: Path | None = None,
    scoped_child_inventory_reconciliation_path: Path | None = None,
    relevance_plan_path: Path | None = None,
    orchestrator_progress_path: Path | None = None,
    attachment_manifest_path: Path | None = None,
    attachment_archive_root: Path | None = None,
    legacy_raw_path: Path | None = None,
    quarantine_sidecar_paths: Sequence[Path] = (),
    historical_reconciliation_dirs: Sequence[Path] = (),
    provenance_root: Path | None = None,
    guild_id: str = DEFAULT_GUILD_ID,
    start_date: str = DEFAULT_START_DATE,
    end_date_inclusive: str = DEFAULT_END_DATE_INCLUSIVE,
    timezone_name: str = DEFAULT_TIMEZONE,
    data_cutoff_utc: dt.datetime | None = None,
    release_requested: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    scope = make_scope(guild_id, start_date, end_date_inclusive, timezone_name)
    provenance_root = (provenance_root or SCRIPT_DIR.parents[1]).resolve()
    cutoff = (data_cutoff_utc or now_utc()).astimezone(dt.timezone.utc)
    generated = now_utc()
    source_registry: dict[str, dict[str, Any]] = {}
    authorized_scope_policy: authorized_collection_scope.AuthorizedScope | None = None
    proven_children: dict[str, dict[str, Any]] = {}
    authorized_parent_aliases: dict[str, frozenset[str]] = {}
    child_inventory_reconciliation: dict[str, Any] | None = None
    scope_excluded_files: list[dict[str, Any]] = []
    authorized_scope_source_file: dict[str, Any] | None = None
    canonical_path_policy_audit: dict[str, Any] | None = None
    if authorized_scope_path is not None:
        if legacy_raw_path is not None:
            raise CorpusError(
                "--legacy-raw is not accepted in an authorized scoped build; use exact "
                "channel segments so mixed-channel legacy rows cannot leak into the release."
            )
        if relevance_plan_path is not None:
            raise CorpusError(
                "The server-wide relevance plan is incompatible with the user-narrowed "
                "authorized scope; omit --relevance-plan for the scoped release."
            )
        if orchestrator_progress_path is not None:
            raise CorpusError(
                "The server-wide orchestrator progress manifest is incompatible with the "
                "user-narrowed scope; pass only the scoped segment directories."
            )
        authorized_scope_policy = authorized_collection_scope.load_validated_scope(
            authorized_scope_path,
            expected_guild_id=scope.guild_id,
            expected_timezone=scope.timezone_name,
            expected_start_date=scope.start_date.isoformat(),
            expected_end_date=scope.end_date_inclusive.isoformat(),
        )
        canonical_path_policy_audit = (
            authorized_collection_scope.validate_authoritative_segment_directories(
                segment_dirs, authorized_scope_policy
            )
        )
        if inventory_path is None:
            raise CorpusError(
                "An authorized scoped build requires the exact channel inventory"
            )
        if scoped_child_inventory_reconciliation_path is None:
            raise CorpusError(
                "An authorized scoped build requires the byte-bound Premium Journals "
                "child-inventory reconciliation"
            )
        proven_children, _inventory_payload = (
            authorized_collection_scope.load_proven_child_relationships(
                inventory_path.resolve() if inventory_path else None,
                authorized_scope_policy,
            )
        )
        authorized_parent_aliases = (
            authorized_collection_scope.authorized_parent_name_aliases(
                _inventory_payload, authorized_scope_policy
            )
        )
        proven_children, child_inventory_reconciliation = (
            authorized_collection_scope.load_scoped_child_inventory_reconciliation(
                scoped_child_inventory_reconciliation_path,
                authorized_scope_policy,
                proven_children,
            )
        )
        authorized_scope_source_file = register_source_file(
            source_registry,
            authorized_scope_policy.source_path,
            provenance_root,
            kind="authorized_collection_scope",
        )
        if scoped_child_inventory_reconciliation_path is not None:
            register_source_file(
                source_registry,
                scoped_child_inventory_reconciliation_path.resolve(),
                provenance_root,
                kind="scoped_child_inventory_reconciliation",
            )
    elif scoped_child_inventory_reconciliation_path is not None:
        raise CorpusError(
            "A scoped child-inventory reconciliation requires --authorized-scope"
        )
    progress_payload: dict[str, Any] | None = None
    progress_source_file: dict[str, Any] | None = None
    auto_groups = {
        "channel_capture": [],
        "relevance_query": [],
        "residual_audit": [],
    }
    if orchestrator_progress_path:
        resolved_progress = orchestrator_progress_path.resolve()
        progress_source_file = register_source_file(
            source_registry,
            resolved_progress,
            provenance_root,
            kind="orchestrator_progress_manifest",
        )
        progress_payload = relevance_release_policy.load_json_object(
            resolved_progress, "orchestrator progress manifest"
        )
        auto_groups = progress_manifest_segment_directories(
            resolved_progress, progress_payload
        )
    segment_groups = unique_segment_directory_groups(
        {
            "channel_capture": [*segment_dirs, *auto_groups["channel_capture"]],
            "relevance_query": [
                *relevance_segment_dirs,
                *auto_groups["relevance_query"],
            ],
            "residual_audit": [*audit_segment_dirs, *auto_groups["residual_audit"]],
        }
    )
    resolved_segment_dirs = [
        path for values in segment_groups.values() for path in values
    ]
    resolved_sidecars = discover_migration_quarantine_sidecars(
        resolved_segment_dirs, quarantine_sidecar_paths
    )
    migration_quarantine_index, migration_sidecars = ingest_migration_quarantine_sidecars(
        resolved_sidecars,
        provenance_root,
        source_registry,
    )

    segments: list[dict[str, Any]] = []
    channel_occurrences: list[dict[str, Any]] = []
    file_failures: list[dict[str, Any]] = []
    for input_role, directories in segment_groups.items():
        role_segments, role_occurrences, role_failures = ingest_segment_files(
            directories,
            scope,
            provenance_root,
            source_registry,
            migration_quarantine_index,
            input_role=input_role,
            authorized_scope=authorized_scope_policy,
            proven_children=proven_children,
            authorized_parent_name_aliases=authorized_parent_aliases,
            scope_excluded_files=scope_excluded_files,
        )
        segments.extend(role_segments)
        channel_occurrences.extend(role_occurrences)
        file_failures.extend(role_failures)
    legacy_occurrences, legacy = ingest_legacy_raw(
        legacy_raw_path.resolve() if legacy_raw_path else None,
        scope,
        provenance_root,
        source_registry,
    )
    historical_occurrences, historical_reconciliation = (
        ingest_historical_reconciliations(
            historical_reconciliation_dirs,
            scope,
            provenance_root,
            source_registry,
            authorized_scope=authorized_scope_policy,
            proven_children=proven_children,
            scope_excluded_files=scope_excluded_files,
        )
    )
    if (
        authorized_scope_policy is not None
        and child_inventory_reconciliation is not None
    ):
        child_inventory_reconciliation = copy.deepcopy(
            child_inventory_reconciliation
        )
        child_inventory_reconciliation["message_scope_closure"] = (
            authorized_collection_scope.evaluate_premium_journals_message_scope_closure(
                scope=authorized_scope_policy,
                segments=segments,
                occurrences=channel_occurrences,
                proven_children=proven_children,
            )
        )
        proven_children = (
            authorized_collection_scope.extend_proven_children_from_premium_occurrences(
                proven_children, channel_occurrences
            )
        )
    inventory = normalize_inventory(
        inventory_path.resolve() if inventory_path else None,
        scope,
        provenance_root,
        source_registry,
        channel_occurrences,
    )
    if authorized_scope_policy is not None:
        inventory = authorized_collection_scope.derive_scoped_inventory(
            inventory,
            authorized_scope_policy,
            proven_children,
            child_inventory_reconciliation,
        )
    occurrences = channel_occurrences + legacy_occurrences + historical_occurrences
    scope_excluded_migration_audit: dict[str, Any] | None = None
    if authorized_scope_policy is not None and migration_sidecars.get("provided"):
        included_migration_ids = {
            str(row.get("migration_occurrence_id"))
            for row in occurrences
            if str(row.get("migration_occurrence_id") or "")
        }
        all_sidecar_records = list(migration_sidecars.get("records") or [])
        scoped_sidecar_records = [
            row
            for row in all_sidecar_records
            if str(row.get("occurrence_id") or "") in included_migration_ids
        ]
        excluded_sidecar_records = [
            row
            for row in all_sidecar_records
            if str(row.get("occurrence_id") or "") not in included_migration_ids
        ]
        excluded_message_ids = sorted(
            {
                str(message_id)
                for row in excluded_sidecar_records
                for message_id in (row.get("message_ids") or [])
                if MESSAGE_ID_RE.fullmatch(str(message_id or ""))
            }
        )
        scope_excluded_migration_audit = {
            "excluded_record_count": len(excluded_sidecar_records),
            "excluded_unique_valid_message_id_count": len(excluded_message_ids),
            "excluded_record_set_sha256": sha256_bytes(
                compact_json(
                    sorted(
                        str(row.get("occurrence_id") or "")
                        for row in excluded_sidecar_records
                    )
                ).encode("utf-8")
            ),
            "excluded_message_id_set_sha256": sha256_bytes(
                compact_json(excluded_message_ids).encode("utf-8")
            ),
            "raw_sidecar_bytes_mutated": False,
        }
        migration_sidecars["records"] = scoped_sidecar_records
        migration_sidecars["record_count"] = len(scoped_sidecar_records)
        migration_sidecars["indexed_occurrence_count"] = len(scoped_sidecar_records)
        migration_sidecars["scope_filtering"] = copy.deepcopy(
            scope_excluded_migration_audit
        )
    messages, conflicts, merge_stats = merge_unique_messages(occurrences)
    coverage = build_coverage(scope, inventory, segments, file_failures)
    quarantine = quarantine_summary(occurrences, messages, migration_sidecars)
    attachment_archive = apply_attachment_archive_manifest(
        messages=messages,
        manifest_path=attachment_manifest_path,
        archive_root=attachment_archive_root,
        provenance_root=provenance_root,
        source_registry=source_registry,
        authorized_message_ids=(
            {
                str(row.get("message_id"))
                for row in messages
                if MESSAGE_ID_RE.fullmatch(str(row.get("message_id") or ""))
            }
            if authorized_scope_policy is not None
            else None
        ),
    )

    relevance_policy: dict[str, Any] | None = None
    if relevance_plan_path:
        resolved_plan = relevance_plan_path.resolve()
        register_source_file(
            source_registry,
            resolved_plan,
            provenance_root,
            kind="relevance_collection_plan",
        )
        plan_bundle = relevance_release_policy.load_validated_plan(
            resolved_plan,
            inventory_path.resolve() if inventory_path else None,
        )
        for vocabulary_source in plan_bundle.get("plan", {}).get(
            "vocabulary_sources", []
        ):
            if not isinstance(vocabulary_source, dict):
                continue
            relative = str(vocabulary_source.get("path_relative_to_plan") or "")
            if relative:
                register_source_file(
                    source_registry,
                    (resolved_plan.parent / relative).resolve(),
                    provenance_root,
                    kind="discord_vocabulary_source",
                )
        relevance_policy = relevance_release_policy.evaluate_relevance_policy(
            plan_bundle=plan_bundle,
            segments=segments,
            inventory=inventory,
            progress=progress_payload,
            data_cutoff_utc=cutoff,
            required_end_exclusive_utc=scope.utc_end_exclusive,
            occurrences=occurrences,
            messages=messages,
        )
        segments = relevance_policy["classified_segments"]
        coverage["segments"] = segments
    timestamp_scope_integrity = summarize_timestamp_scope_integrity(segments)
    executed_command_reply_provenance_integrity = (
        summarize_executed_command_reply_provenance_integrity(segments)
    )
    source_files = sorted(
        source_registry.values(), key=lambda row: (row["relative_path"], row["source_file_id"])
    )
    release_gates = make_release_gates(
        scope=scope,
        data_cutoff=cutoff,
        inventory=inventory,
        coverage=coverage,
        quarantine=quarantine,
        source_files=source_files,
        legacy=legacy,
        messages=messages,
        historical_reconciliation=historical_reconciliation,
        attachment_archive=attachment_archive,
        relevance_policy=relevance_policy,
        timestamp_scope_integrity=timestamp_scope_integrity,
        executed_command_reply_provenance_integrity=(
            executed_command_reply_provenance_integrity
        ),
    )
    authorized_scope_audit: dict[str, Any]
    if authorized_scope_policy is not None:
        authorized_scope_audit = authorized_collection_scope.summarize_selection_audit(
            scope=authorized_scope_policy,
            included_segments=segments,
            included_occurrences=occurrences,
            excluded_files=scope_excluded_files,
            proven_children=proven_children,
        )
        authorized_scope_audit["source_file_id"] = (
            authorized_scope_source_file.get("source_file_id")
            if authorized_scope_source_file
            else None
        )
        authorized_scope_audit["excluded_migration_sidecar_records"] = (
            scope_excluded_migration_audit
            or {
                "excluded_record_count": 0,
                "excluded_unique_valid_message_id_count": 0,
                "raw_sidecar_bytes_mutated": False,
            }
        )
        authorized_scope_audit["child_inventory_reconciliation"] = copy.deepcopy(
            child_inventory_reconciliation
        )
        premium_segments = [
            row
            for row in segments
            if str(row.get("query_container_id") or "")
            == authorized_collection_scope.PREMIUM_PARENT_ID
            and row.get("input_role") == "channel_capture"
        ]
        premium_occurrences = [
            row
            for row in occurrences
            if str(row.get("query_container_id") or "")
            == authorized_collection_scope.PREMIUM_PARENT_ID
            and row.get("source_kind") == "channel_segment"
        ]
        premium_dates = [
            str(row.get("start_date") or "")
            for row in premium_segments
            if row.get("start_date") == row.get("end_date")
        ]
        duplicate_dates = sorted(
            value
            for value, count in Counter(premium_dates).items()
            if value and count > 1
        )
        legacy_preserved_count = sum(
            1
            for row in scope_excluded_files
            if row.get("reason")
            == "premium_journals_legacy_directory_preservation_only"
            and row.get("artifact_role") == "segment"
        )
        invalid_premium_authoritative_paths = sorted(
            str(row.get("relative_path") or "")
            for row in scope_excluded_files
            if str(row.get("reason") or "").startswith(
                "premium_authoritative_contract_invalid:"
            )
            and row.get("artifact_role") == "segment"
        )
        premium_version_mismatch_paths = sorted(
            str(row.get("source_file_relative_path") or "")
            for row in premium_segments
            if str(row.get("collector_version") or "")
            != str(
                authorized_collection_scope.CANONICAL_PATH_POLICY[
                    "premium_journals"
                ]["collector_version_required"]
            )
        )
        premium_bound_source_files: dict[str, dict[str, Any]] = {}
        premium_provenance_missing_segments: list[str] = []
        for row in premium_segments:
            integrity = row.get("premium_journals_provenance_integrity")
            integrity = integrity if isinstance(integrity, dict) else {}
            bound_rows = integrity.get("source_files")
            if not isinstance(bound_rows, list) or not bound_rows:
                premium_provenance_missing_segments.append(
                    str(row.get("source_file_relative_path") or "")
                )
                continue
            for bound in bound_rows:
                if not isinstance(bound, dict):
                    continue
                relative = str(bound.get("path") or "")
                previous = premium_bound_source_files.get(relative)
                if previous is not None and previous != bound:
                    premium_provenance_missing_segments.append(
                        str(row.get("source_file_relative_path") or "")
                    )
                    continue
                premium_bound_source_files[relative] = bound
        premium_provenance_missing_segments = sorted(
            set(premium_provenance_missing_segments)
        )
        path_audit = copy.deepcopy(canonical_path_policy_audit or {})
        path_audit.update(
            {
                "passed": bool(
                    path_audit.get("passed") is True
                    and not duplicate_dates
                    and not premium_version_mismatch_paths
                    and not premium_provenance_missing_segments
                    and not invalid_premium_authoritative_paths
                    and path_audit.get(
                        "legacy_premium_authoritative_occurrence_count"
                    )
                    == 0
                ),
                "accepted_premium_segment_count": len(premium_segments),
                "accepted_premium_daily_date_count": len(set(premium_dates)),
                "duplicate_premium_daily_dates": duplicate_dates,
                "premium_collector_version_mismatch_count": len(
                    premium_version_mismatch_paths
                ),
                "premium_collector_version_mismatch_paths": (
                    premium_version_mismatch_paths
                ),
                "premium_provenance_missing_segment_count": len(
                    premium_provenance_missing_segments
                ),
                "premium_provenance_missing_segments": (
                    premium_provenance_missing_segments
                ),
                "invalid_premium_authoritative_file_count": len(
                    invalid_premium_authoritative_paths
                ),
                "invalid_premium_authoritative_paths": (
                    invalid_premium_authoritative_paths
                ),
                "accepted_premium_bound_source_file_count": len(
                    premium_bound_source_files
                ),
                "legacy_premium_preservation_file_count": legacy_preserved_count,
                "legacy_premium_authoritative_occurrence_count": 0,
                "accepted_premium_source_file_set_sha256": sha256_bytes(
                    compact_json(
                        sorted(
                            [
                                relative,
                                str(bound.get("sha256") or ""),
                                int(bound.get("bytes") or 0),
                            ]
                            for relative, bound in premium_bound_source_files.items()
                        )
                    ).encode("utf-8")
                ),
                "accepted_premium_message_id_set_sha256": sha256_bytes(
                    compact_json(
                        sorted(
                            {
                                str(row.get("message_id"))
                                for row in premium_occurrences
                                if MESSAGE_ID_RE.fullmatch(
                                    str(row.get("message_id") or "")
                                )
                            }
                        )
                    ).encode("utf-8")
                ),
            }
        )
        authorized_scope_audit["canonical_path_policy"] = path_audit
        release_gates.insert(0, copy.deepcopy(authorized_scope_audit["release_gate"]))
        release_gates.insert(1, copy.deepcopy(path_audit))
        if child_inventory_reconciliation is not None:
            release_gates.insert(
                2,
                copy.deepcopy(
                    child_inventory_reconciliation["message_scope_closure"]
                ),
            )
    else:
        authorized_scope_audit = {"enabled": False}
    release_ready = all(row["passed"] for row in release_gates)
    status = "complete" if release_requested and release_ready else "partial"
    artifact_type = ARTIFACT_TYPE_RELEASE if status == "complete" else ARTIFACT_TYPE_WORKING

    occurrence_counts = Counter(item.get("source_kind") or "unknown" for item in occurrences)
    corpus = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": artifact_type,
        "scope": scope.as_dict(),
        "release": {
            "status": status,
            "release_requested": release_requested,
            "release_ready": release_ready,
            "generated_at_utc": iso_z(generated),
            "data_cutoff_utc": iso_z(cutoff),
            "completeness_through_utc": (
                iso_z(scope.utc_end_exclusive) if status == "complete" else None
            ),
            "rule": (
                "Working builds are always partial. Complete status is allowed only in --release "
                "mode after every release gate passes."
            ),
        },
        "inventory": inventory,
        "authorized_collection_scope": authorized_scope_audit,
        "relevance_policy": (
            {
                key: value
                for key, value in relevance_policy.items()
                if key != "classified_segments"
            }
            if relevance_policy
            else {"enabled": False}
        ),
        "orchestrator_progress": (
            {
                "provided": True,
                "source_file_id": progress_source_file.get("source_file_id")
                if progress_source_file
                else None,
                "artifact_type": progress_payload.get("artifact_type")
                if progress_payload
                else None,
                "generated_at_utc": progress_payload.get("generated_at_utc")
                if progress_payload
                else None,
                "summary": copy.deepcopy(progress_payload.get("summary") or {})
                if progress_payload
                else {},
            }
            if progress_payload is not None
            else {"provided": False}
        ),
        "source_files": source_files,
        "segments": segments,
        "messages": messages,
        "attachment_archive": attachment_archive,
        "timestamp_scope_integrity": timestamp_scope_integrity,
        "executed_command_reply_provenance_integrity": (
            executed_command_reply_provenance_integrity
        ),
        "occurrences": [sanitize_occurrence_for_output(item) for item in occurrences],
        "quarantine": quarantine,
        "migration_quarantine_sidecars": migration_sidecars,
        "historical_reconciliation": historical_reconciliation,
        "legacy_provenance": legacy,
        "counts": {
            "source_files": len(source_files),
            "channel_segments": len(segments),
            "source_occurrences": len(occurrences),
            "source_occurrences_by_kind": dict(sorted(occurrence_counts.items())),
            "unique_messages": len(messages),
            "duplicate_occurrences_over_unique_messages": len(occurrences) - len(messages),
            "messages_with_field_variants": len(conflicts),
            **merge_stats,
        },
        "field_conflicts": conflicts,
        "release_gates": release_gates,
        "source_scope": "discord_only",
        "outside_sources_used": 0,
        "methodology": {
            "canonical_timestamp": "Discord message ID snowflake timestamp",
            "timestamp_mismatch_threshold_ms": TIMESTAMP_MISMATCH_THRESHOLD_MS,
            "message_deduplication_key": "Discord message_id",
            "occurrence_retention": "Every input row and reconstructed legacy source occurrence is retained.",
            "field_conflict_policy": (
                "All distinct non-empty values retain occurrence linkage in _field_variants; "
                "the canonical row is selected deterministically from trusted channel captures."
            ),
            "relevance_filtering": (
                "Policy-scoped evidence capture; no captured message is deleted. "
                "All 16 nonempty channels require message completeness and all 22 empty "
                "channels require independently verified zero captures; targeted artifacts are supplemental only."
                if relevance_policy
                else "none"
            ),
            "authorized_scope_filtering": (
                "Only exact authenticated requested-container segments for student-breakdowns, "
                "premium-journals, questions, or provenance-proven child threads are present; "
                "excluded raw bytes remain untouched and are represented only by hash/count audit metadata."
                if authorized_scope_policy
                else "disabled"
            ),
            "legacy_coverage_policy": "Legacy term-filtered data never certifies server-wide coverage.",
            "forum_inventory_policy": (
                "Exact forum thread IDs observed in captured rows are added as provenance-backed "
                "containers covered by the parent forum search. Observed IDs do not certify that "
                "all active and archived threads were enumerated."
            ),
            "attachment_archive_policy": (
                "Every discovered Discord-owned attachment must have exact message and attachment "
                "IDs. Release requires a verified terminal archive disposition: downloaded bytes "
                "with SHA-256 when available, or a documented terminal unavailable state. A "
                "terminal failed state is preserved as a degraded gap and blocks literal release. "
                "External links are never fetched, and chart claims remain unresolved without a "
                "verified complete/partial local extraction artifact."
            ),
            "migration_quarantine_policy": (
                "Migrated legacy occurrences and sidecar flags remain quarantined and ineligible "
                "for accepted analysis unless the same Discord message has an independent, "
                "strictly complete, non-migration, non-quarantined channel-segment recapture."
            ),
            "historical_reconciliation_policy": historical_reconciliation.get(
                "policy"
            ),
        },
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "discord_serverwide_coverage_manifest",
        "status": status,
        "generated_at_utc": iso_z(generated),
        "data_cutoff_utc": iso_z(cutoff),
        "scope": scope.as_dict(),
        "inventory": inventory,
        "authorized_collection_scope": authorized_scope_audit,
        "relevance_policy": (
            {
                key: value
                for key, value in relevance_policy.items()
                if key != "classified_segments"
            }
            if relevance_policy
            else {"enabled": False}
        ),
        "orchestrator_progress": corpus["orchestrator_progress"],
        "coverage": coverage,
        "source_files": source_files,
        "counts": corpus["counts"],
        "quarantine": quarantine,
        "migration_quarantine_sidecars": migration_sidecars,
        "historical_reconciliation": historical_reconciliation,
        "legacy_provenance": legacy,
        "field_conflicts": conflicts,
        "attachment_archive": attachment_archive,
        "timestamp_scope_integrity": timestamp_scope_integrity,
        "executed_command_reply_provenance_integrity": (
            executed_command_reply_provenance_integrity
        ),
        "release_ready": release_ready,
        "release_gates": release_gates,
        "limitations": [
            "Only channels and threads visible/searchable to the authenticated Discord account can be certified.",
            "Deleted messages and containers that disappeared before inventory cannot be proven complete.",
            "Premium-journals thread IDs are currently discovered only when a captured row exposes exact row-owned thread evidence; active and archived forum enumeration remains incomplete.",
            "Legacy reconstructed occurrences preserve prior provenance but not occurrence-specific field values.",
            "Migrated or sidecar-quarantined rows remain searchable but cannot support accepted analytical evidence without an independent trusted canonical recapture.",
            "Historically unavailable rows are retained from byte-bound earlier Discord captures, remain quarantined and analysis-ineligible, and carry no inferred deletion/edit cause.",
            "A successful segment search executed before the requested end cannot certify the unfinished final day.",
            "Terminal unavailable Discord attachments are preserved as explicit gaps, while terminal failed attachments block literal release and final packaging.",
            "Attachment filenames, presence, failed extraction attempts, and extraction rows without verified local artifacts cannot establish chart geometry or setup facts.",
            "Diagnostic partial full-channel attempts in targeted noisy channels are retained as evidence but never certify message completeness and do not replace the 94-query matrix or residual audit.",
        ],
    }
    return corpus, manifest


def ensure_safe_output(path: Path) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(SCRIPT_DIR)
    except ValueError as exc:
        raise CorpusError(
            f"Output must stay inside the isolated corpus package: {SCRIPT_DIR}"
        ) from exc
    if resolved.parent == SCRIPT_DIR.parent and resolved.name in PROTECTED_PARENT_NAMES:
        raise CorpusError(f"Refusing to overwrite protected parent artifact {resolved.name}")


def write_json_atomic(path: Path, payload: dict[str, Any], *, exclusive: bool) -> None:
    ensure_safe_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if exclusive:
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        except FileExistsError as exc:
            raise CorpusError(f"Refusing to overwrite release artifact {path}") from exc
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def parse_cli_timestamp(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return parse_timestamp(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--segment-dir",
        type=Path,
        action="append",
        help=(
            "Canonical directory containing channel segment JSON. May be repeated. "
            f"Defaults to {DEFAULT_SEGMENT_DIR}."
        ),
    )
    parser.add_argument(
        "--relevance-segment-dir",
        type=Path,
        action="append",
        help=(
            "Targeted-query segment directory; repeatable. With --relevance-plan, "
            f"defaults to {DEFAULT_RELEVANCE_SEGMENT_DIR} when that directory exists."
        ),
    )
    parser.add_argument(
        "--audit-segment-dir",
        type=Path,
        action="append",
        help=(
            "Residual census segment directory; repeatable. With --relevance-plan, "
            f"defaults to {DEFAULT_AUDIT_SEGMENT_DIR} when that directory exists."
        ),
    )
    parser.add_argument("--inventory", type=Path, help="Exact channel/thread inventory JSON.")
    parser.add_argument(
        "--authorized-scope",
        type=Path,
        default=DEFAULT_AUTHORIZED_SCOPE,
        help=(
            "Fail-closed user-authorized collection scope. Defaults to "
            f"{DEFAULT_AUTHORIZED_SCOPE.name} when present; only its exact parents and "
            "proven child threads enter corpus/database/analysis outputs."
        ),
    )
    parser.add_argument(
        "--scoped-child-inventory-reconciliation",
        type=Path,
        default=DEFAULT_SCOPED_CHILD_INVENTORY_RECONCILIATION,
        help=(
            "Additive exact child-ID reconciliation. Exact additions become selectable, "
            "but closure_proven=false keeps inventory/release completeness blocked."
        ),
    )
    parser.add_argument(
        "--relevance-plan",
        type=Path,
        help=(
            "Validated Discord-only relevance collection plan. Supplying it enables "
            "policy-aware message-complete/topic-complete release gates."
        ),
    )
    parser.add_argument(
        "--orchestrator-progress-manifest",
        type=Path,
        help=(
            "Read-only collection progress manifest. Its provenance-listed segment "
            "directories and release_evidence are reconciled against raw files; assertions "
            "never replace raw segment evidence."
        ),
    )
    parser.add_argument(
        "--attachment-manifest",
        type=Path,
        help=(
            "Manifest produced by discord_attachment_archiver.py. Required for release "
            "when the corpus contains any Discord-owned attachments."
        ),
    )
    parser.add_argument(
        "--attachment-archive-root",
        type=Path,
        help=(
            "Local root containing the manifest's package-relative attachment files. "
            "Required whenever the manifest contains downloaded bytes."
        ),
    )
    parser.add_argument(
        "--legacy-raw",
        type=Path,
        help="Optional legacy three-month merged raw JSON; preserved as provenance only.",
    )
    parser.add_argument(
        "--quarantine-sidecar",
        type=Path,
        action="append",
        default=[],
        help=(
            "Migration quarantine JSONL/JSON sidecar. May be repeated. The standard "
            f"{MIGRATION_QUARANTINE_SIDECAR_NAME} is also auto-discovered beside a segment directory."
        ),
    )
    parser.add_argument(
        "--historical-reconciliation-dir",
        type=Path,
        action="append",
        help=(
            "Directory containing byte-bound v2.5 replacement/reconciliation notes "
            "and their preserved legacy artifacts. May be repeated. Defaults to "
            f"{DEFAULT_HISTORICAL_RECONCILIATION_DIR} when that directory exists."
        ),
    )
    parser.add_argument("--provenance-root", type=Path, default=SCRIPT_DIR.parents[1])
    parser.add_argument("--guild-id", default=DEFAULT_GUILD_ID)
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE_INCLUSIVE)
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument(
        "--data-cutoff-utc",
        type=parse_cli_timestamp,
        help="Exact collection/data cutoff. Defaults to current UTC time.",
    )
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    segment_dirs = args.segment_dir or [
        DEFAULT_SEGMENT_DIR,
        DEFAULT_PREMIUM_SEGMENT_DIR,
    ]
    relevance_segment_dirs = args.relevance_segment_dir or (
        [DEFAULT_RELEVANCE_SEGMENT_DIR]
        if args.relevance_plan and DEFAULT_RELEVANCE_SEGMENT_DIR.is_dir()
        else []
    )
    audit_segment_dirs = args.audit_segment_dir or (
        [DEFAULT_AUDIT_SEGMENT_DIR]
        if args.relevance_plan and DEFAULT_AUDIT_SEGMENT_DIR.is_dir()
        else []
    )
    historical_reconciliation_dirs = args.historical_reconciliation_dir or (
        [DEFAULT_HISTORICAL_RECONCILIATION_DIR]
        if DEFAULT_HISTORICAL_RECONCILIATION_DIR.is_dir()
        and args.provenance_root.resolve() == SCRIPT_DIR.parents[1].resolve()
        else []
    )
    output = args.output or (DEFAULT_RELEASE_CORPUS if args.release else DEFAULT_WORKING_CORPUS)
    manifest_path = args.manifest or (
        DEFAULT_RELEASE_MANIFEST if args.release else DEFAULT_WORKING_MANIFEST
    )
    try:
        corpus, manifest = build_corpus(
            segment_dirs=segment_dirs,
            relevance_segment_dirs=relevance_segment_dirs,
            audit_segment_dirs=audit_segment_dirs,
            inventory_path=args.inventory,
            authorized_scope_path=args.authorized_scope,
            scoped_child_inventory_reconciliation_path=(
                args.scoped_child_inventory_reconciliation
                if args.authorized_scope
                else None
            ),
            relevance_plan_path=args.relevance_plan,
            orchestrator_progress_path=args.orchestrator_progress_manifest,
            attachment_manifest_path=args.attachment_manifest,
            attachment_archive_root=args.attachment_archive_root,
            legacy_raw_path=args.legacy_raw,
            quarantine_sidecar_paths=args.quarantine_sidecar,
            historical_reconciliation_dirs=historical_reconciliation_dirs,
            provenance_root=args.provenance_root,
            guild_id=args.guild_id,
            start_date=args.start_date,
            end_date_inclusive=args.end_date,
            timezone_name=args.timezone,
            data_cutoff_utc=args.data_cutoff_utc,
            release_requested=args.release,
        )
        summary = {
            "status": manifest["status"],
            "release_requested": args.release,
            "release_ready": manifest["release_ready"],
            "scope": manifest["scope"],
            "counts": manifest["counts"],
            "coverage_summary": manifest["coverage"]["summary"],
            "failed_release_gates": [
                row["gate"] for row in manifest["release_gates"] if not row["passed"]
            ],
            "output": None if args.dry_run else str(output.resolve()),
            "manifest": None if args.dry_run else str(manifest_path.resolve()),
        }
        if args.release and not manifest["release_ready"]:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print(
                "ERROR: release requested but one or more completeness gates failed; "
                "no release artifacts were written.",
                file=sys.stderr,
            )
            return 2
        if not args.dry_run:
            write_json_atomic(output, corpus, exclusive=args.release)
            write_json_atomic(manifest_path, manifest, exclusive=args.release)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (
        CorpusError,
        authorized_collection_scope.AuthorizedScopeError,
        relevance_release_policy.RelevancePolicyError,
        OSError,
        ValueError,
    ) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
