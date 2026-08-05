#!/usr/bin/env python3
"""Build a read-only schema-migration snapshot from canonical and quarantined segments."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import build_corpus as corpus_contract


HERE = Path(__file__).resolve().parent
CANONICAL_DIR = HERE / "raw" / "channel_segments"
QUARANTINE_DIR = HERE / "raw" / "quarantine_collection_errors"
SCHEDULE_PATH = HERE / "working" / "two_tab_collection_schedule.json"
OUTPUT_PATH = HERE / "working" / "schema_migration_progress_manifest.json"
SUMMARY_PATH = HERE / "working" / "SCHEMA_MIGRATION_PROGRESS_SUMMARY.md"
BASELINE_DEFINITION_PATH = HERE / "working" / "schema_migration_baseline_242.json"
GUILD_ID = "1167376964680691732"
WINDOW_START = "2026-01-01"
WINDOW_END = "2026-07-20"

CLASS_ACCEPTED_V25 = "accepted_v2_5_complete"
CLASS_ACCEPTED_ZERO_SIDECAR = "accepted_v2_5_verified_empty_with_valid_sidecar"
CLASS_POSITIVE_RECAPTURE = "needs_positive_fresh_recapture"
CLASS_ZERO_SIDECAR = "needs_zero_sidecar_revalidation"
CLASS_PARTIAL_RESTART = "pre_v2_5_partial_fresh_restart"
CLASS_QUARANTINE = "historical_disappeared_quarantine"
BASELINE_BUCKET_ZERO = "zero_sidecar_candidate"
BASELINE_BUCKET_RECAPTURE = "fresh_recapture"
BASELINE_BUCKETS = {BASELINE_BUCKET_ZERO, BASELINE_BUCKET_RECAPTURE}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def rel(path: Path) -> str:
    return path.resolve().relative_to(HERE.resolve()).as_posix()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def baseline_membership_sha256(membership: dict[str, str]) -> str:
    rows = [f"{path}|{membership[path]}" for path in sorted(membership)]
    return sha256_bytes("\n".join(rows).encode("utf-8"))


def _valid_canonical_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("raw/channel_segments/") or "\\" in value:
        return False
    parsed = PurePosixPath(value)
    return ".." not in parsed.parts and parsed.suffix == ".json"


def load_baseline_membership(path: Path = BASELINE_DEFINITION_PATH) -> tuple[dict[str, Any], dict[str, str], str]:
    """Load and strictly validate the immutable 242-segment migration membership.

    The definition expands a contiguous daily Live series plus 41 explicit non-Live
    identities.  Current directory contents never create baseline members.  A
    malformed definition raises instead of silently changing the denominator.
    """

    payload = load_object(path)
    errors: list[str] = []
    if payload.get("artifact_type") != "discord_schema_migration_baseline_definition":
        errors.append("artifact_type_invalid")
    if payload.get("schema_version") != "1.0.0":
        errors.append("schema_version_invalid")

    membership: dict[str, str] = {}

    def add_member(member_path: Any, bucket: Any, source: str) -> None:
        if not _valid_canonical_relative_path(member_path):
            errors.append(f"{source}_path_invalid")
            return
        if bucket not in BASELINE_BUCKETS:
            errors.append(f"{source}_bucket_invalid")
            return
        if member_path in membership:
            errors.append(f"duplicate_membership_path:{member_path}")
            return
        membership[member_path] = bucket

    live = payload.get("live_daily_series")
    if not isinstance(live, dict):
        errors.append("live_daily_series_missing_or_invalid")
    else:
        channel_id = str(live.get("channel_id") or "")
        template = live.get("canonical_path_template")
        start_text = live.get("start_date_inclusive")
        end_text = live.get("end_date_inclusive")
        fresh_dates_raw = live.get("fresh_recapture_dates")
        if not channel_id.isdigit():
            errors.append("live_channel_id_invalid")
        if not isinstance(template, str) or any(token not in template for token in ("{channel_id}", "{date}")):
            errors.append("live_canonical_path_template_invalid")
        if not isinstance(fresh_dates_raw, list) or any(not isinstance(item, str) for item in fresh_dates_raw):
            errors.append("live_fresh_recapture_dates_invalid")
            fresh_dates: set[str] = set()
        else:
            fresh_dates = set(fresh_dates_raw)
            if len(fresh_dates) != len(fresh_dates_raw):
                errors.append("live_fresh_recapture_dates_duplicate")
        try:
            start_date = date.fromisoformat(str(start_text))
            end_date = date.fromisoformat(str(end_text))
            if end_date < start_date:
                raise ValueError("end before start")
        except (TypeError, ValueError):
            errors.append("live_date_range_invalid")
            start_date = end_date = None
        if start_date is not None and isinstance(template, str):
            allowed_dates: set[str] = set()
            current = start_date
            while current <= end_date:
                current_text = current.isoformat()
                allowed_dates.add(current_text)
                bucket = BASELINE_BUCKET_RECAPTURE if current_text in fresh_dates else BASELINE_BUCKET_ZERO
                try:
                    member_path = template.format(channel_id=channel_id, date=current_text)
                except (KeyError, ValueError):
                    errors.append("live_canonical_path_template_format_error")
                    break
                add_member(member_path, bucket, "live")
                current += timedelta(days=1)
            if fresh_dates - allowed_dates:
                errors.append("live_fresh_recapture_date_outside_range")

    non_live = payload.get("non_live_segments")
    if not isinstance(non_live, list):
        errors.append("non_live_segments_missing_or_invalid")
    else:
        for index, item in enumerate(non_live):
            if not isinstance(item, dict):
                errors.append(f"non_live_segment_{index}_invalid")
                continue
            add_member(item.get("path"), item.get("bucket"), f"non_live_segment_{index}")

    expected = payload.get("expected_counts")
    counts = Counter(membership.values())
    if not isinstance(expected, dict):
        errors.append("expected_counts_missing_or_invalid")
    else:
        if expected.get("canonical_segments") != len(membership):
            errors.append("expected_canonical_segment_count_mismatch")
        if expected.get("zero_sidecar_candidates") != counts[BASELINE_BUCKET_ZERO]:
            errors.append("expected_zero_sidecar_candidate_count_mismatch")
        if expected.get("fresh_recaptures") != counts[BASELINE_BUCKET_RECAPTURE]:
            errors.append("expected_fresh_recapture_count_mismatch")
    computed_hash = baseline_membership_sha256(membership)
    if payload.get("membership_sha256") != computed_hash:
        errors.append("membership_sha256_mismatch")
    if errors:
        raise RuntimeError(f"Invalid immutable schema-migration baseline {path}: {', '.join(sorted(set(errors)))}")
    return payload, membership, computed_hash


def segment_key(payload: dict[str, Any]) -> tuple[str, str, str]:
    requested = payload.get("requested_container") if isinstance(payload.get("requested_container"), dict) else {}
    segment = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
    return (
        str(requested.get("channel_id") or ""),
        str(segment.get("start") or ""),
        str(segment.get("end") or ""),
    )


def sidecar_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}{corpus_contract.COMPLETION_EVIDENCE_SIDECAR_SUFFIX}")


def validate_sidecar(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    candidate = sidecar_path(path)
    if not candidate.is_file():
        return {
            "path": rel(candidate),
            "exists": False,
            "valid": None,
            "sha256": None,
            "validation_errors": [],
        }
    errors: list[str] = []
    try:
        sidecar = load_object(candidate)
    except Exception as exc:
        return {
            "path": rel(candidate),
            "exists": True,
            "valid": False,
            "sha256": sha256_file(candidate),
            "validation_errors": [f"unreadable:{exc}"],
        }
    if sidecar.get("artifact_type") != "discord_segment_completion_evidence_sidecar":
        errors.append("artifact_type_invalid")
    if sidecar.get("schema_version") != "1.0.0":
        errors.append("sidecar_schema_invalid")
    source_text = str(sidecar.get("source_artifact_path") or "")
    if not source_text:
        errors.append("source_artifact_path_missing")
    else:
        source = Path(source_text)
        resolved_source = source.resolve() if source.is_absolute() else (candidate.parent / source).resolve()
        if resolved_source != path.resolve():
            errors.append("source_artifact_path_mismatch")
    if sidecar.get("source_artifact_sha256") != sha256_file(path):
        errors.append("source_artifact_sha256_mismatch")
    if sidecar.get("guild_id") != payload.get("guild_id"):
        errors.append("guild_id_mismatch")
    if not isinstance(sidecar.get("requested_container"), dict):
        errors.append("requested_container_missing_or_invalid")
    elif sidecar.get("requested_container") != payload.get("requested_container"):
        errors.append("requested_container_mismatch")
    if sidecar.get("segment") != payload.get("segment"):
        errors.append("segment_mismatch")
    if sidecar.get("reported_total") != payload.get("reported_total"):
        errors.append("reported_total_mismatch")
    if sidecar.get("reported_pages") != payload.get("reported_pages"):
        errors.append("reported_pages_mismatch")
    segment = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
    errors.extend(
        corpus_contract.validate_completion_evidence(
            sidecar.get("completion_evidence"),
            query=str(segment.get("query") or ""),
            reported_total=payload.get("reported_total"),
            reported_pages=payload.get("reported_pages"),
        )
    )
    return {
        "path": rel(candidate),
        "exists": True,
        "valid": not errors,
        "sha256": sha256_file(candidate),
        "artifact_type": sidecar.get("artifact_type"),
        "schema_version": sidecar.get("schema_version"),
        "created_at_utc": sidecar.get("created_at_utc"),
        "source_artifact_path": sidecar.get("source_artifact_path"),
        "source_artifact_sha256": sidecar.get("source_artifact_sha256"),
        "completion_evidence_schema_version": (
            sidecar.get("completion_evidence", {}).get("schema_version")
            if isinstance(sidecar.get("completion_evidence"), dict)
            else None
        ),
        "validation_errors": sorted(set(errors)),
    }


def basic_structural_errors(path: Path, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    rows = payload.get("messages")
    rows = rows if isinstance(rows, list) else []
    total = payload.get("reported_total")
    pages = payload.get("reported_pages")
    ids = [str(row.get("message_id") or "") for row in rows if isinstance(row, dict)]
    if payload.get("complete") is not True or path.name.endswith(".partial.json"):
        errors.append("not_declared_complete")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        errors.append("reported_total_invalid")
    else:
        if len(rows) != total:
            errors.append("message_count_mismatch")
        if len(set(ids)) != total:
            errors.append("message_id_uniqueness_mismatch")
        expected_pages = math.ceil(total / 25) if total else 0
        if pages != expected_pages:
            errors.append("reported_pages_mismatch")
        if payload.get("pages_captured") != expected_pages:
            errors.append("pages_captured_mismatch")
    if payload.get("gap_indices") not in ([], None):
        errors.append("gap_indices_nonempty")
    if int(payload.get("container_mismatch_count") or 0) != 0:
        errors.append("container_mismatch_nonzero")
    requested = payload.get("requested_container")
    if not isinstance(requested, dict) or not requested.get("channel_id"):
        errors.append("requested_container_missing")
    segment = payload.get("segment")
    if not isinstance(segment, dict) or not segment.get("query") or not segment.get("start") or not segment.get("end"):
        errors.append("segment_identity_missing")
    if payload.get("guild_id") != GUILD_ID:
        errors.append("guild_id_mismatch")
    return sorted(set(errors))


def inline_evidence_errors(payload: dict[str, Any]) -> list[str]:
    segment = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
    return corpus_contract.validate_completion_evidence(
        payload.get("completion_evidence"),
        query=str(segment.get("query") or ""),
        reported_total=payload.get("reported_total"),
        reported_pages=payload.get("reported_pages"),
    )


def classify_canonical(path: Path, payload: dict[str, Any], sidecar: dict[str, Any]) -> tuple[str, str]:
    total = payload.get("reported_total")
    version = str(payload.get("collector_version") or "")
    structural = basic_structural_errors(path, payload)
    inline_errors = inline_evidence_errors(payload)
    if version == "2.5" and not structural and not inline_errors:
        return CLASS_ACCEPTED_V25, "Collector 2.5 artifact is structurally complete with valid durable inline completion evidence."
    if total == 0 and not structural and sidecar.get("valid") is True:
        return (
            CLASS_ACCEPTED_ZERO_SIDECAR,
            "Pre-2.5 verified-empty artifact is accepted under the 2.5 contract through a strictly bound valid sidecar.",
        )
    if payload.get("complete") is not True or path.name.endswith(".partial.json"):
        return CLASS_PARTIAL_RESTART, "Pre-2.5 partial checkpoint requires fail-closed fresh restart or validated patched resumption."
    if total == 0:
        return CLASS_ZERO_SIDECAR, "Pre-2.5 zero-result artifact still needs authenticated empty revalidation and a valid bound sidecar."
    return CLASS_POSITIVE_RECAPTURE, "Positive pre-2.5 artifact remains in the prior fresh-recapture population."


def record_for(path: Path, payload: dict[str, Any], classification: str, reason: str) -> dict[str, Any]:
    rows = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    requested = payload.get("requested_container") if isinstance(payload.get("requested_container"), dict) else {}
    segment = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
    sidecar = validate_sidecar(path, payload)
    inline_errors = inline_evidence_errors(payload)
    return {
        "classification": classification,
        "reason": reason,
        "canonical_path": rel(path),
        "filename": path.name,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "guild_id": payload.get("guild_id"),
        "container": {
            "channel_id": requested.get("channel_id"),
            "channel_name": requested.get("channel_name"),
            "channel_kind": requested.get("channel_kind"),
            "category_name": requested.get("category_name"),
            "channel_id_source": requested.get("channel_id_source"),
        },
        "segment": {
            "query": segment.get("query"),
            "start": segment.get("start"),
            "end": segment.get("end"),
            "timezone": segment.get("timezone") or payload.get("timezone"),
        },
        "counts": {
            "reported_total": payload.get("reported_total"),
            "reported_pages": payload.get("reported_pages"),
            "captured_rows_declared": payload.get("captured_rows"),
            "messages_actual": len(rows),
            "unique_message_ids_declared": payload.get("unique_message_ids"),
            "unique_message_ids_actual": len(
                {str(row.get("message_id") or "") for row in rows if isinstance(row, dict)}
            ),
            "pages_captured": payload.get("pages_captured"),
        },
        "versions": {
            "collector_version": payload.get("collector_version"),
            "artifact_schema_version": payload.get("schema_version"),
            "inline_completion_evidence_schema_version": (
                payload.get("completion_evidence", {}).get("schema_version")
                if isinstance(payload.get("completion_evidence"), dict)
                else None
            ),
            "sidecar_schema_version": sidecar.get("schema_version"),
        },
        "declared_complete": payload.get("complete") is True,
        "structural_validation_errors": basic_structural_errors(path, payload),
        "inline_completion_evidence": {
            "present": isinstance(payload.get("completion_evidence"), dict),
            "valid": isinstance(payload.get("completion_evidence"), dict) and not inline_errors,
            "validation_errors": inline_errors,
        },
        "sidecar": sidecar,
    }


def quarantine_records() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[tuple[str, str, str], list[dict[str, Any]]]]:
    segments: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(QUARANTINE_DIR.glob("*.json")):
        if path.name.endswith(
            (
                corpus_contract.COMPLETION_EVIDENCE_SIDECAR_SUFFIX,
                corpus_contract.TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX,
            )
        ):
            continue
        payload = load_object(path)
        is_segment = (
            isinstance(payload.get("segment"), dict)
            and isinstance(payload.get("messages"), list)
            and payload.get("collector_version") is not None
        )
        if not is_segment:
            notes.append(
                {
                    "path": rel(path),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                    "artifact_type": payload.get("artifact_type"),
                    "schema_version": payload.get("schema_version"),
                }
            )
            continue
        sidecar = validate_sidecar(path, payload)
        requested = payload.get("requested_container") if isinstance(payload.get("requested_container"), dict) else {}
        segment = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
        rows = payload.get("messages")
        row = {
            "classification": CLASS_QUARANTINE,
            "reason": (
                "Superseded, rejected, invalid, or stale historical capture is absent from the canonical directory and preserved for audit."
            ),
            "quarantine_path": rel(path),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
            "guild_id": payload.get("guild_id"),
            "container": {
                "channel_id": requested.get("channel_id"),
                "channel_name": requested.get("channel_name"),
                "channel_kind": requested.get("channel_kind"),
            },
            "segment": {
                "query": segment.get("query"),
                "start": segment.get("start"),
                "end": segment.get("end"),
            },
            "counts": {
                "reported_total": payload.get("reported_total"),
                "reported_pages": payload.get("reported_pages"),
                "messages_actual": len(rows),
            },
            "versions": {
                "collector_version": payload.get("collector_version"),
                "artifact_schema_version": payload.get("schema_version"),
                "sidecar_schema_version": sidecar.get("schema_version"),
            },
            "declared_complete": payload.get("complete") is True,
            "sidecar": sidecar,
        }
        segments.append(row)
        by_key[segment_key(payload)].append(row)
    return segments, notes, by_key


def schedule_routes(schedule: dict[str, Any]) -> dict[str, Any]:
    groups = schedule.get("action_groups") if isinstance(schedule.get("action_groups"), dict) else {}
    recapture: dict[str, str] = {}

    live = groups.get("A_live_legacy_nonempty_recaptures", {})
    template = str(live.get("expected_canonical_path_template") or "")
    for date_value in live.get("dates", []):
        recapture[template.format(date=date_value)] = "A_live_legacy_nonempty_recaptures"

    questions = groups.get("A_questions_old_schema_recaptures", {})
    template = str(questions.get("expected_canonical_path_template") or "")
    for date_value in questions.get("dates", []):
        recapture[template.format(date=date_value)] = "A_questions_old_schema_recaptures"

    chat = groups.get("A_chat_old_schema_recapture", {})
    if chat.get("expected_canonical_path"):
        recapture[str(chat["expected_canonical_path"])] = "A_chat_old_schema_recapture"

    for action in groups.get("B_old_schema_nonempty_recaptures", {}).get("actions", []):
        recapture[str(action.get("expected_canonical_path"))] = "B_old_schema_nonempty_recaptures"

    for group_name in ("A_partial_resumptions", "B_partial_resumptions"):
        for action in groups.get(group_name, {}).get("actions", []):
            recapture[str(action.get("partial_path"))] = group_name

    empty_channels: dict[str, str] = {}
    for group_name in ("A_empty_full_window_reverification", "B_empty_full_window_reverification"):
        for channel_id in groups.get(group_name, {}).get("channel_ids", []):
            empty_channels[str(channel_id)] = group_name

    live_refresh = groups.get("A_live_completion_evidence_refresh", {})
    excluded_live_dates = {str(item) for item in live_refresh.get("exclude_fully_recaptured_dates", [])}
    return {
        "recapture": recapture,
        "empty_channels": empty_channels,
        "live_refresh_group": "A_live_completion_evidence_refresh",
        "live_refresh_channel_id": str(live_refresh.get("channel_id") or ""),
        "live_refresh_excluded_dates": excluded_live_dates,
    }


def bind_schedule(record: dict[str, Any], routes: dict[str, Any]) -> dict[str, Any]:
    classification = record["classification"]
    path = record["canonical_path"]
    channel_id = str(record["container"].get("channel_id") or "")
    date_value = str(record["segment"].get("start") or "")
    if classification in {CLASS_POSITIVE_RECAPTURE, CLASS_PARTIAL_RESTART}:
        if path in routes["recapture"]:
            return {
                "required_action": "fresh_recapture_or_fail_closed_restart",
                "covered": True,
                "route": routes["recapture"][path],
                "issue": None,
            }
        if (
            classification == CLASS_POSITIVE_RECAPTURE
            and channel_id == routes["live_refresh_channel_id"]
            and date_value not in routes["live_refresh_excluded_dates"]
        ):
            return {
                "required_action": "fresh_recapture",
                "covered": False,
                "route": routes["live_refresh_group"],
                "issue": "positive_pre_v2_5_segment_is_misrouted_to_completion_evidence_refresh",
            }
        return {
            "required_action": "fresh_recapture_or_fail_closed_restart",
            "covered": False,
            "route": None,
            "issue": "required_recapture_has_no_schedule_route",
        }
    if classification == CLASS_ZERO_SIDECAR:
        if channel_id == routes["live_refresh_channel_id"] and date_value not in routes["live_refresh_excluded_dates"]:
            return {
                "required_action": "zero_sidecar_revalidation",
                "covered": True,
                "route": routes["live_refresh_group"],
                "issue": None,
            }
        if channel_id in routes["empty_channels"]:
            return {
                "required_action": "zero_sidecar_revalidation",
                "covered": True,
                "route": routes["empty_channels"][channel_id],
                "issue": None,
            }
        return {
            "required_action": "zero_sidecar_revalidation",
            "covered": False,
            "route": None,
            "issue": "zero_sidecar_candidate_has_no_schedule_route",
        }
    return {"required_action": None, "covered": True, "route": None, "issue": None}


def directory_fingerprint() -> str:
    rows: list[str] = []
    for directory in (CANONICAL_DIR, QUARANTINE_DIR):
        for path in sorted(directory.glob("*.json")):
            rows.append(f"{rel(path)}|{path.stat().st_size}|{sha256_file(path)}")
    return sha256_bytes("\n".join(rows).encode("utf-8"))


def build() -> tuple[dict[str, Any], str]:
    started_fingerprint = directory_fingerprint()
    baseline_definition_sha256 = sha256_file(BASELINE_DEFINITION_PATH)
    baseline_definition, baseline_membership, baseline_membership_hash = load_baseline_membership()
    schedule = load_object(SCHEDULE_PATH)
    quarantine, quarantine_notes, historical_by_key = quarantine_records()
    scope = corpus_contract.make_scope(GUILD_ID, WINDOW_START, WINDOW_END, "America/Chicago")
    routes = schedule_routes(schedule)
    records: list[dict[str, Any]] = []
    post_baseline_records: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    baseline_bucket_lineage_mismatches: list[dict[str, Any]] = []
    for path in sorted(CANONICAL_DIR.glob("*.json")):
        if path.name.endswith(
            (
                corpus_contract.COMPLETION_EVIDENCE_SIDECAR_SUFFIX,
                corpus_contract.TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX,
            )
        ):
            continue
        payload = load_object(path)
        sidecar = validate_sidecar(path, payload)
        classification, reason = classify_canonical(path, payload, sidecar)
        record = record_for(path, payload, classification, reason)
        normalized, _rows = corpus_contract.validate_segment_payload(path, payload, scope)
        record["corpus_contract"] = {
            "computed_complete": normalized["computed_complete"],
            "status": normalized["status"],
            "validation_errors": normalized["validation_errors"],
            "validation_warnings": normalized["validation_warnings"],
        }
        canonical_path = record["canonical_path"]
        predecessors = historical_by_key.get(segment_key(payload), [])
        baseline_evidence = next(
            (item for item in predecessors if ".legacy-v2." in item["quarantine_path"]),
            predecessors[0] if predecessors else None,
        )
        if baseline_evidence is not None:
            baseline_total = baseline_evidence["counts"].get("reported_total")
            baseline_complete = baseline_evidence.get("declared_complete") is True
            reconstructed_bucket = BASELINE_BUCKET_ZERO if baseline_complete and baseline_total == 0 else BASELINE_BUCKET_RECAPTURE
            baseline_path = baseline_evidence["quarantine_path"]
        else:
            reconstructed_bucket = (
                BASELINE_BUCKET_ZERO
                if payload.get("complete") is True and payload.get("reported_total") == 0
                else BASELINE_BUCKET_RECAPTURE
            )
            baseline_path = record["canonical_path"]

        if canonical_path in baseline_membership:
            frozen_bucket = baseline_membership[canonical_path]
            record["migration_membership"] = "prior_242_baseline"
            record["prior_242_baseline"] = {
                "bucket": frozen_bucket,
                "membership_source": rel(BASELINE_DEFINITION_PATH),
                "lineage_reconstructed_bucket": reconstructed_bucket,
                "lineage_bucket_matches_frozen_definition": reconstructed_bucket == frozen_bucket,
                "lineage_evidence_path": baseline_path,
                "promoted_canonical_replacement": baseline_evidence is not None,
            }
            if reconstructed_bucket != frozen_bucket:
                baseline_bucket_lineage_mismatches.append(
                    {
                        "canonical_path": canonical_path,
                        "frozen_bucket": frozen_bucket,
                        "lineage_reconstructed_bucket": reconstructed_bucket,
                        "lineage_evidence_path": baseline_path,
                    }
                )
            record["schedule"] = bind_schedule(record, routes)
            records.append(record)
        else:
            record["migration_membership"] = "post_baseline_new_segment"
            record["prior_242_baseline"] = None
            record["schedule"] = {
                "required_action": None,
                "covered": True,
                "route": None,
                "issue": None,
                "note": "Outside the frozen 242-segment schema-migration denominator.",
            }
            post_baseline_records.append(record)
        all_records.append(record)

    classification_counts = Counter(item["classification"] for item in records)
    baseline_counts = Counter(baseline_membership.values())
    observed_baseline_counts = Counter(item["prior_242_baseline"]["bucket"] for item in records)
    current_canonical_paths = {item["canonical_path"] for item in all_records}
    missing_baseline_paths = sorted(set(baseline_membership) - current_canonical_paths)
    post_baseline_records.sort(key=lambda item: item["canonical_path"])
    post_baseline_paths = [item["canonical_path"] for item in post_baseline_records]
    post_baseline_path_list_hash = sha256_bytes("\n".join(post_baseline_paths).encode("utf-8"))
    post_baseline_inventory_rows = [
        f"{item['canonical_path']}|{item['size_bytes']}|{item['sha256']}" for item in post_baseline_records
    ]
    post_baseline_inventory_hash = sha256_bytes("\n".join(post_baseline_inventory_rows).encode("utf-8"))
    post_baseline_classification_counts = Counter(item["classification"] for item in post_baseline_records)
    omissions = [
        {
            "canonical_path": item["canonical_path"],
            "channel_id": item["container"]["channel_id"],
            "channel_name": item["container"]["channel_name"],
            "start": item["segment"]["start"],
            "end": item["segment"]["end"],
            "classification": item["classification"],
            "reported_total": item["counts"]["reported_total"],
            "current_route": item["schedule"]["route"],
            "issue": item["schedule"]["issue"],
        }
        for item in records
        if not item["schedule"]["covered"]
    ]
    pending_classes = {CLASS_POSITIVE_RECAPTURE, CLASS_ZERO_SIDECAR, CLASS_PARTIAL_RESTART}
    pending = [item for item in records if item["classification"] in pending_classes]
    resolved = [item for item in records if item["classification"] not in pending_classes]
    promoted = [item for item in records if item["prior_242_baseline"]["promoted_canonical_replacement"]]
    end_fingerprint = directory_fingerprint()
    if end_fingerprint != started_fingerprint:
        raise RuntimeError("Raw source set changed while the migration snapshot was being built; rerun for an atomic current view.")
    if sha256_file(BASELINE_DEFINITION_PATH) != baseline_definition_sha256:
        raise RuntimeError("Immutable migration baseline changed while the snapshot was being built; rerun after auditing it.")

    baseline_integrity_passed = (
        len(baseline_membership) == 242
        and baseline_counts[BASELINE_BUCKET_ZERO] == 169
        and baseline_counts[BASELINE_BUCKET_RECAPTURE] == 73
        and not missing_baseline_paths
        and not baseline_bucket_lineage_mismatches
        and len(records) == 242
    )

    manifest = {
        "schema_version": "1.1.0",
        "artifact_type": "discord_schema_migration_progress_manifest",
        "generated_at_utc": utc_now(),
        "status": "action_required" if omissions or pending or not baseline_integrity_passed else "complete",
        "source_policy": {
            "scope": "local_discord_raw_only",
            "online_sources_used": 0,
            "browser_calls_made": 0,
            "canonical_raw_files_modified": 0,
            "snapshot_fingerprint_sha256": started_fingerprint,
            "immutable_baseline_definition_path": rel(BASELINE_DEFINITION_PATH),
            "immutable_baseline_definition_sha256": baseline_definition_sha256,
            "immutable_baseline_membership_sha256": baseline_membership_hash,
        },
        "scope": {
            "guild_id": GUILD_ID,
            "start_date_inclusive": WINDOW_START,
            "end_date_inclusive": WINDOW_END,
            "canonical_directory": rel(CANONICAL_DIR),
            "quarantine_directory": rel(QUARANTINE_DIR),
            "grain": (
                "canonical_segments contains only the frozen 242 migration members; post-baseline newly collected "
                "canonical artifacts and quarantined historical artifacts are listed separately"
            ),
        },
        "classification_definitions": {
            CLASS_ACCEPTED_V25: "Canonical Collector 2.5 complete artifact with valid inline 1.0.0 completion evidence.",
            CLASS_ACCEPTED_ZERO_SIDECAR: "Canonical pre-2.5 zero-result complete artifact accepted through a strictly source-bound valid 1.0.0 sidecar.",
            CLASS_POSITIVE_RECAPTURE: "Canonical positive pre-2.5 complete artifact requiring fresh recapture under the prior migration decision.",
            CLASS_ZERO_SIDECAR: "Canonical zero-result pre-2.5 complete artifact awaiting authenticated empty sidecar revalidation.",
            CLASS_PARTIAL_RESTART: "Canonical pre-2.5 partial artifact requiring fail-closed fresh restart or patched validated resumption.",
            CLASS_QUARANTINE: "Historical data artifact removed from canonical placement and preserved in quarantine.",
        },
        "prior_242_reconciliation": {
            "baseline_definition_path": rel(BASELINE_DEFINITION_PATH),
            "baseline_definition_sha256": baseline_definition_sha256,
            "baseline_membership_sha256": baseline_membership_hash,
            "prior_expected": baseline_definition["expected_counts"],
            "observed_baseline_reconstruction": {
                "canonical_segments": len(records),
                "zero_sidecar_candidates": observed_baseline_counts[BASELINE_BUCKET_ZERO],
                "fresh_recaptures": observed_baseline_counts[BASELINE_BUCKET_RECAPTURE],
                "missing_baseline_segment_count": len(missing_baseline_paths),
                "missing_baseline_paths": missing_baseline_paths,
                "lineage_bucket_mismatch_count": len(baseline_bucket_lineage_mismatches),
                "lineage_bucket_mismatches": baseline_bucket_lineage_mismatches,
            },
            "baseline_reconciled": baseline_integrity_passed,
            "progress_since_baseline": {
                "fresh_recaptures_promoted_to_accepted_v2_5": classification_counts[CLASS_ACCEPTED_V25],
                "zero_candidates_revalidated_with_valid_sidecars": classification_counts[CLASS_ACCEPTED_ZERO_SIDECAR],
                "total_resolved": len(resolved),
                "historical_predecessor_links_observed": len(promoted),
                "historical_predecessor_note": (
                    "Historical predecessor links include earlier 2.0-to-2.4 repairs and are not all post-baseline promotions."
                ),
            },
            "current": {
                "resolved": len(resolved),
                "remaining": len(pending),
                "classification_counts": dict(sorted(classification_counts.items())),
            },
        },
        "current_canonical_inventory": {
            "total_segment_count": len(all_records),
            "baseline_migration_segment_count": len(records),
            "post_baseline_new_segment_count": len(post_baseline_records),
            "partition_is_exhaustive_and_exclusive": (
                len(all_records) == len(records) + len(post_baseline_records)
                and not ({item["canonical_path"] for item in records} & set(post_baseline_paths))
            ),
        },
        "post_baseline_new_segments": {
            "count": len(post_baseline_records),
            "canonical_paths": post_baseline_paths,
            "canonical_path_list_sha256": post_baseline_path_list_hash,
            "artifact_inventory_sha256": post_baseline_inventory_hash,
            "classification_counts": dict(sorted(post_baseline_classification_counts.items())),
            "records": post_baseline_records,
        },
        "schedule_reconciliation": {
            "schedule_path": rel(SCHEDULE_PATH),
            "schedule_sha256": sha256_file(SCHEDULE_PATH),
            "schedule_generated_at_utc": schedule.get("generated_at_utc"),
            "pending_segment_count": len(pending),
            "covered_pending_segment_count": len(pending) - len(omissions),
            "omission_count": len(omissions),
            "schedule_covers_all_pending_migrations": not omissions,
            "omissions": omissions,
        },
        "quarantine_summary": {
            "historical_data_artifact_count": len(quarantine),
            "note_artifact_count": len(quarantine_notes),
            "historical_classification": CLASS_QUARANTINE,
        },
        "validation": {
            "checks": {
                "immutable_baseline_definition_count_is_242": len(baseline_membership) == 242,
                "all_242_baseline_members_present": len(records) == 242 and not missing_baseline_paths,
                "baseline_paths_unique": len({item["canonical_path"] for item in records}) == len(records),
                "baseline_classification_is_exhaustive_and_exclusive": sum(classification_counts.values()) == len(records),
                "prior_169_plus_73_reconciled": (
                    baseline_counts[BASELINE_BUCKET_ZERO] == 169
                    and baseline_counts[BASELINE_BUCKET_RECAPTURE] == 73
                ),
                "frozen_buckets_match_lineage_reconstruction": not baseline_bucket_lineage_mismatches,
                "current_canonical_inventory_partitioned_without_overlap": (
                    len(all_records) == len(records) + len(post_baseline_records)
                    and not ({item["canonical_path"] for item in records} & set(post_baseline_paths))
                ),
                "accepted_sidecars_all_strictly_valid": all(
                    item["sidecar"]["valid"] is True
                    for item in records
                    if item["classification"] == CLASS_ACCEPTED_ZERO_SIDECAR
                ),
                "accepted_v2_5_all_pass_corpus_contract": all(
                    item["corpus_contract"]["computed_complete"] is True
                    for item in records
                    if item["classification"] == CLASS_ACCEPTED_V25
                ),
                "all_pending_segments_have_correct_schedule_route": not omissions,
                "raw_snapshot_stable_during_build": True,
                "immutable_baseline_stable_during_build": True,
            },
            "manifest_integrity_passed": baseline_integrity_passed,
            "schedule_validation_passed": not omissions,
        },
        "canonical_segments": records,
        "historical_quarantine_artifacts": quarantine,
        "quarantine_note_artifacts": quarantine_notes,
    }

    omitted_dates = [str(item["start"]) for item in omissions]
    counts = manifest["prior_242_reconciliation"]["current"]["classification_counts"]
    if omissions:
        schedule_note = (
            "The omitted positive Live dates would otherwise fall under completion-evidence "
            "refresh, but positive pre-v2.5 artifacts require fresh recapture."
        )
    else:
        schedule_note = (
            "Every remaining positive pre-v2.5 Live artifact is routed to fresh recapture; "
            "none is misrouted to completion-evidence refresh."
        )
    markdown = f"""# Schema migration progress

Snapshot: `{manifest['generated_at_utc']}`  
Raw snapshot SHA-256: `{started_fingerprint}`  
Immutable baseline membership SHA-256: `{baseline_membership_hash}`

## Current result

| Measure | Count |
|---|---:|
| Current canonical segments (all collection work) | {len(all_records)} |
| Frozen migration baseline segments | {len(records)} |
| Post-baseline newly collected segments | {len(post_baseline_records)} |
| Prior zero-sidecar population | {baseline_counts['zero_sidecar_candidate']} |
| Prior fresh-recapture population | {baseline_counts['fresh_recapture']} |
| Accepted Collector 2.5 replacements | {counts.get(CLASS_ACCEPTED_V25, 0)} |
| Accepted valid zero sidecars | {counts.get(CLASS_ACCEPTED_ZERO_SIDECAR, 0)} |
| Positive fresh recaptures remaining | {counts.get(CLASS_POSITIVE_RECAPTURE, 0)} |
| Zero sidecar revalidations remaining | {counts.get(CLASS_ZERO_SIDECAR, 0)} |
| Pre-2.5 partial restarts remaining | {counts.get(CLASS_PARTIAL_RESTART, 0)} |
| Total resolved | {len(resolved)} |
| Total remaining | {len(pending)} |

The prior `242 = 169 + 73` population reconciles exactly: **{manifest['prior_242_reconciliation']['baseline_reconciled']}**.

## Post-baseline collection

The {len(post_baseline_records)} newly collected canonical segments are listed separately under `post_baseline_new_segments`. Their path-list SHA-256 is `{post_baseline_path_list_hash}` and their path/size/artifact-SHA inventory hash is `{post_baseline_inventory_hash}`. They do not change migration classifications or the 242 denominator.

## Schedule validation

The current two-tab schedule covers {len(pending) - len(omissions)} of {len(pending)} remaining migration segments with the required action type. It omits or misroutes **{len(omissions)}** positive pre-2.5 Live recaptures. {schedule_note}

Omitted Live dates: {', '.join(omitted_dates) if omitted_dates else 'none'}.

All remaining zero-result candidates and all pre-2.5 partial checkpoints have an explicit schedule route.

## Quarantine

The manifest separately indexes {len(quarantine)} historical quarantined data artifacts and {len(quarantine_notes)} quarantine note artifacts. These do not inflate the 242 canonical-segment denominator.
"""
    return manifest, markdown


def main() -> int:
    manifest, markdown = build()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SUMMARY_PATH.write_text(markdown, encoding="utf-8")
    print(json.dumps({
        "manifest": rel(OUTPUT_PATH),
        "summary": rel(SUMMARY_PATH),
        "status": manifest["status"],
        "classification_counts": manifest["prior_242_reconciliation"]["current"]["classification_counts"],
        "baseline_migration_segments": manifest["current_canonical_inventory"]["baseline_migration_segment_count"],
        "post_baseline_new_segments": manifest["post_baseline_new_segments"]["count"],
        "post_baseline_path_list_sha256": manifest["post_baseline_new_segments"]["canonical_path_list_sha256"],
        "schedule_omissions": manifest["schedule_reconciliation"]["omission_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
