#!/usr/bin/env python3
"""Build a deterministic, fail-closed Discord research release directory.

This is intentionally a *last-mile* packager, not a builder.  It accepts only
explicit final artifacts, validates their release state and cross-links, opens
both SQLite databases read-only, and atomically publishes a new directory.
Source artifacts are hashed before and after packaging and are never changed.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence

import discord_attachment_archiver
import reply_provenance_contract
import timestamp_scope_revalidation


SCHEMA_VERSION = "1.0.0"
ARTIFACT_TYPE = "cardinal_discord_research_release_package"
EXPECTED_GUILD_ID = "1167376964680691732"
EXPECTED_START_DATE = "2026-01-01"
EXPECTED_END_DATE = "2026-07-20"
EXPECTED_TIMEZONE = "America/Chicago"
EXPECTED_START_UTC = "2026-01-01T06:00:00Z"
EXPECTED_END_UTC = "2026-07-21T05:00:00Z"
EXPECTED_LOCAL_DAYS = 201
PREMIUM_PARENT_ID = "1283941772577472643"
PREMIUM_AUTHORITATIVE_DIRECTORY = "raw/channel_segments_v2_5"
PREMIUM_LEGACY_DIRECTORY = "raw/channel_segments"
PREMIUM_COLLECTOR_VERSION = "2.6"
PREMIUM_REQUIRED_DAILY_SEGMENTS = 201
AUTHORIZED_PARENT_IDS = {
    "1370578463223975986",
    "1283941772577472643",
    "1273692573898113076",
}
AUTHORIZED_PARENT_IDENTITIES = {
    "1370578463223975986": ("student-breakdowns", "text channel"),
    "1283941772577472643": ("premium-journals", "forum channel"),
    "1273692573898113076": ("\u2753\u2502questions", "text channel"),
}
DEFAULT_AUTHORIZED_SCOPE = Path(__file__).resolve().parent / "authorized_collection_scope.json"

FORBIDDEN_FILE_TOKENS = {
    "partial",
    "smoke",
    "working",
    "draft",
    "provisional",
    "staging",
    "temporary",
    "temp",
    "tmp",
    "template",
    "incomplete",
    "needs_revision",
}
FORBIDDEN_DIRECTORY_NAMES = {
    "partial",
    "smoke",
    "working",
    "staging",
}

REQUIRED_ANALYSIS_DOCUMENTS = {
    "discord_analysis_coverage",
    "discord_analysis_methodology",
    "discord_rejection_block_research",
    "discord_trade_profiles",
    "discord_model_cards",
}

CORE_SHARED_TABLES = (
    "messages",
    "message_source_occurrences",
    "analysis_documents",
    "evidence_items",
    "claims",
    "questions",
    "answers",
    "trade_episodes",
    "setup_models",
    "attachment_extractions",
)

ATTACHMENT_RELEASE_COLUMNS = (
    "attachment_id",
    "message_id",
    "attachment_id_exact",
    "filename",
    "discord_url",
    "source_channel_id",
    "relation_type",
    "ownership_status",
    "ownership_evidence_json",
    "owned_for_capture",
    "eligible_for_attachment_evidence",
    "mime_type",
    "media_kind",
    "width",
    "height",
    "byte_size",
    "content_sha256",
    "local_package_path",
    "capture_status",
    "capture_terminal",
    "capture_attempt_count",
    "capture_attempts_json",
    "capture_failure_code",
    "capture_failure_detail",
    "extraction_status",
    "extraction_artifacts_json",
    "archive_manifest_source_file_id",
    "chart_claim_eligible",
    "notes",
)


class ReleasePackageError(RuntimeError):
    """Raised when an input or destination cannot be safely released."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def pretty_json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def parse_utc(value: Any, *, label: str) -> dt.datetime:
    text = str(value or "").strip()
    if not text:
        raise ReleasePackageError(f"{label} is missing")
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ReleasePackageError(f"{label} is not an ISO-8601 timestamp: {text!r}") from exc
    if parsed.tzinfo is None:
        raise ReleasePackageError(f"{label} has no timezone: {text!r}")
    return parsed.astimezone(dt.timezone.utc)


def utc_equal(value: Any, expected: str, *, label: str) -> None:
    if parse_utc(value, label=label) != parse_utc(expected, label="expected timestamp"):
        raise ReleasePackageError(f"{label} must equal {expected}, got {value!r}")


def outside_zero(value: Any) -> bool:
    return value is False or value == 0 or str(value).strip() == "0"


def require_bool_true(value: Any, label: str) -> None:
    if value is not True:
        raise ReleasePackageError(f"{label} must be true")


def require_empty(value: Any, label: str) -> None:
    if value not in (None, [], {}, "", 0):
        raise ReleasePackageError(f"{label} must be empty/zero, got {value!r}")


def reject_nonfinal_path(path: Path, *, label: str) -> None:
    tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", path.name.casefold())
        if token
    }
    bad_tokens = sorted(tokens & FORBIDDEN_FILE_TOKENS)
    bad_directories = sorted(
        {
            part.casefold()
            for part in path.parts[:-1]
            if part.casefold() in FORBIDDEN_DIRECTORY_NAMES
        }
    )
    if bad_tokens or bad_directories:
        detail = ", ".join(bad_tokens + bad_directories)
        raise ReleasePackageError(
            f"{label} looks like a partial/smoke/working artifact ({detail}): {path}"
        )


def require_regular_file(
    path: Path,
    *,
    label: str,
    suffixes: set[str],
    reject_working_path: bool = True,
) -> Path:
    supplied = path.absolute()
    if supplied.is_symlink():
        raise ReleasePackageError(f"{label} must not be a symbolic link: {supplied}")
    resolved = path.resolve()
    if not resolved.is_file():
        raise ReleasePackageError(f"{label} must be a regular file: {resolved}")
    if resolved.suffix.casefold() not in suffixes:
        raise ReleasePackageError(
            f"{label} has an unexpected extension {resolved.suffix!r}: {resolved}"
        )
    if reject_working_path:
        reject_nonfinal_path(resolved, label=label)
    return resolved


def stable_read_json(path: Path, *, label: str) -> dict[str, Any]:
    before = sha256_file(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleasePackageError(f"{label} is not valid UTF-8 JSON: {path}: {exc}") from exc
    after = sha256_file(path)
    if before != after:
        raise ReleasePackageError(f"{label} changed while it was read: {path}")
    if not isinstance(value, dict):
        raise ReleasePackageError(f"{label} must contain a JSON object: {path}")
    return value


def validate_structured_discord_only(value: Any, *, label: str) -> None:
    errors: list[str] = []

    def walk(item: Any, location: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                child_location = f"{location}.{key}"
                key_folded = str(key).casefold()
                if key_folded in {"source_scope", "claim_scope"}:
                    if str(child).casefold() != "discord_only":
                        errors.append(f"{child_location}={child!r}")
                elif key_folded == "outside_sources_used":
                    if not outside_zero(child):
                        errors.append(f"{child_location}={child!r}")
                walk(child, child_location)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                walk(child, f"{location}[{index}]")

    walk(value, label)
    if errors:
        raise ReleasePackageError(
            f"{label} violates Discord-only provenance: " + "; ".join(errors[:20])
        )


def validate_manifest_scope(scope: Any, *, label: str) -> None:
    if not isinstance(scope, dict):
        raise ReleasePackageError(f"{label} must be an object")
    expected_text = {
        "guild_id": EXPECTED_GUILD_ID,
        "start_date_inclusive": EXPECTED_START_DATE,
        "end_date_inclusive": EXPECTED_END_DATE,
        "timezone": EXPECTED_TIMEZONE,
    }
    for key, expected in expected_text.items():
        if str(scope.get(key) or "") != expected:
            raise ReleasePackageError(
                f"{label}.{key} must equal {expected!r}, got {scope.get(key)!r}"
            )
    utc_equal(scope.get("utc_start_inclusive"), EXPECTED_START_UTC, label=f"{label}.utc_start_inclusive")
    utc_equal(scope.get("utc_end_exclusive"), EXPECTED_END_UTC, label=f"{label}.utc_end_exclusive")
    if int(scope.get("local_calendar_days") or 0) != EXPECTED_LOCAL_DAYS:
        raise ReleasePackageError(
            f"{label}.local_calendar_days must equal {EXPECTED_LOCAL_DAYS}"
        )


def validate_qa_scope(scope: Any) -> None:
    if not isinstance(scope, dict):
        raise ReleasePackageError("QA scope must be an object")
    expected_text = {
        "guild_id": EXPECTED_GUILD_ID,
        "source_scope": "discord_only",
        "window_calendar_timezone": EXPECTED_TIMEZONE,
        "window_start_local_date": EXPECTED_START_DATE,
        "window_end_local_date_inclusive": EXPECTED_END_DATE,
    }
    for key, expected in expected_text.items():
        if str(scope.get(key) or "") != expected:
            raise ReleasePackageError(
                f"QA scope.{key} must equal {expected!r}, got {scope.get(key)!r}"
            )
    if not outside_zero(scope.get("outside_sources_used")):
        raise ReleasePackageError("QA scope.outside_sources_used must be false/0")
    utc_equal(scope.get("window_start_utc"), EXPECTED_START_UTC, label="QA scope.window_start_utc")
    utc_equal(
        scope.get("window_end_exclusive_utc"),
        EXPECTED_END_UTC,
        label="QA scope.window_end_exclusive_utc",
    )
    if int(scope.get("local_calendar_days") or 0) != EXPECTED_LOCAL_DAYS:
        raise ReleasePackageError(
            f"QA scope.local_calendar_days must equal {EXPECTED_LOCAL_DAYS}"
        )
    if not (
        scope.get("premium_authoritative_directory")
        == PREMIUM_AUTHORITATIVE_DIRECTORY
        and scope.get("premium_collector_version_required")
        == PREMIUM_COLLECTOR_VERSION
        and scope.get("premium_daily_segment_count")
        == PREMIUM_REQUIRED_DAILY_SEGMENTS
        and scope.get("premium_inventory_census_complete") is False
    ):
        raise ReleasePackageError(
            "QA scope does not bind the Premium authoritative source contract"
        )
    require_bool_true(scope.get("final_day_complete"), "QA scope.final_day_complete")
    cutoff = parse_utc(scope.get("data_cutoff_utc"), label="QA scope.data_cutoff_utc")
    if cutoff < parse_utc(EXPECTED_END_UTC, label="required release cutoff"):
        raise ReleasePackageError("QA data cutoff precedes the full Jul 20 Central day")


def validate_gate_rows(rows: Any, *, label: str, id_key: str) -> None:
    if not isinstance(rows, list) or not rows:
        raise ReleasePackageError(f"{label} must be a nonempty gate list")
    failed: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            failed.append(f"row[{index}]:malformed")
            continue
        if row.get("passed") is not True:
            failed.append(str(row.get(id_key) or f"row[{index}]"))
        if "supported" in row and row.get("supported") is not True:
            failed.append(str(row.get(id_key) or f"row[{index}]") + ":unsupported")
    if failed:
        raise ReleasePackageError(f"{label} contains failed/pending gates: {', '.join(failed[:30])}")


def validate_relevance_policy(policy: Any, *, label: str) -> None:
    if not isinstance(policy, dict) or policy.get("enabled") is not False:
        raise ReleasePackageError(
            f"{label} must be disabled for the user-authorized three-channel release"
        )
    forbidden = {
        "policy_counts",
        "job_coverage",
        "hard_gates",
        "classified_segments",
    }
    present = sorted(forbidden & set(policy))
    if present:
        raise ReleasePackageError(
            f"{label} carries obsolete server-wide fields: {', '.join(present)}"
        )


def validate_corpus_manifest(payload: dict[str, Any]) -> None:
    if payload.get("artifact_type") != "discord_serverwide_coverage_manifest":
        raise ReleasePackageError("Corpus manifest has the wrong artifact_type")
    if payload.get("status") != "complete":
        raise ReleasePackageError("Corpus manifest status must be complete")
    require_bool_true(payload.get("release_ready"), "Corpus manifest release_ready")
    validate_manifest_scope(payload.get("scope"), label="Corpus manifest scope")
    cutoff = parse_utc(payload.get("data_cutoff_utc"), label="Corpus manifest data_cutoff_utc")
    if cutoff < parse_utc(EXPECTED_END_UTC, label="required release cutoff"):
        raise ReleasePackageError("Corpus manifest data cutoff precedes the full Jul 20 Central day")
    if payload.get("source_scope") not in (None, "discord_only"):
        raise ReleasePackageError("Corpus manifest source_scope must be discord_only")
    if "outside_sources_used" in payload and not outside_zero(payload.get("outside_sources_used")):
        raise ReleasePackageError("Corpus manifest outside_sources_used must be false/0")
    validate_gate_rows(payload.get("release_gates"), label="Corpus release_gates", id_key="gate")
    timestamp_errors = (
        timestamp_scope_revalidation.release_timestamp_scope_integrity_errors(
            payload
        )
    )
    if timestamp_errors:
        raise ReleasePackageError(
            "Corpus timestamp-scope integrity failed: "
            + ", ".join(timestamp_errors)
        )
    executed_command_errors = (
        reply_provenance_contract.release_executed_command_integrity_errors(
            payload
        )
    )
    if executed_command_errors:
        raise ReleasePackageError(
            "Corpus executed-command reply provenance failed: "
            + ", ".join(executed_command_errors)
        )

    authorized = payload.get("authorized_collection_scope")
    if not isinstance(authorized, dict) or authorized.get("enabled") is not True:
        raise ReleasePackageError(
            "Corpus manifest must carry the user-authorized three-channel scope"
        )
    if authorized.get("schema_version") != "1.0.0" or authorized.get(
        "scope_status"
    ) != "user_narrowed":
        raise ReleasePackageError("Corpus authorized collection scope is invalid")
    if authorized.get("guild_id") != EXPECTED_GUILD_ID:
        raise ReleasePackageError("Corpus authorized scope guild mismatch")
    if authorized.get("source_scope") != "discord_only" or not outside_zero(
        authorized.get("outside_sources_used")
    ):
        raise ReleasePackageError("Corpus authorized scope is not Discord-only")
    scope_sha = str(authorized.get("source_sha256") or "").casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", scope_sha):
        raise ReleasePackageError("Corpus authorized scope SHA-256 is missing")
    if not DEFAULT_AUTHORIZED_SCOPE.is_file() or sha256_file(
        DEFAULT_AUTHORIZED_SCOPE
    ) != scope_sha:
        raise ReleasePackageError(
            "Corpus authorized scope SHA-256 does not match the exact packaged pipeline scope"
        )
    allowed_rows = authorized.get("allowed_top_level_containers")
    if not isinstance(allowed_rows, list):
        raise ReleasePackageError("Corpus authorized parent list is missing")
    allowed_ids = {
        str(row.get("channel_id") or "")
        for row in allowed_rows
        if isinstance(row, dict)
    }
    if allowed_ids != AUTHORIZED_PARENT_IDS or len(allowed_rows) != 3:
        raise ReleasePackageError("Corpus authorized parent set is not the exact three-channel scope")
    for row in allowed_rows:
        if not isinstance(row, dict):
            raise ReleasePackageError("Corpus authorized parent identity row is malformed")
        channel_id = str(row.get("channel_id") or "")
        expected_name, expected_kind = AUTHORIZED_PARENT_IDENTITIES[channel_id]
        if row.get("name") != expected_name or row.get("kind") != expected_kind:
            raise ReleasePackageError(
                f"Corpus authorized parent identity mismatch for {channel_id}"
            )
        if row.get("include_exact_child_threads") is not True:
            raise ReleasePackageError(
                f"Corpus child-thread policy mismatch for {channel_id}"
            )
        if channel_id == "1273692573898113076" and row.get(
            "logical_name"
        ) != "questions":
            raise ReleasePackageError(
                "Questions logical name is missing; it must never replace the exact Discord name"
            )
    scope_gate = authorized.get("release_gate")
    if not isinstance(scope_gate, dict) or scope_gate.get("passed") is not True:
        raise ReleasePackageError("Corpus authorized-scope release gate did not pass")
    excluded = authorized.get("excluded")
    if not isinstance(excluded, dict) or int(
        excluded.get("ambiguous_fail_closed_file_count") or 0
    ) != 0:
        raise ReleasePackageError("Corpus has ambiguous fail-closed scope exclusions")
    for key in ("file_set_sha256", "message_id_sets_sha256"):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(excluded.get(key) or "")):
            raise ReleasePackageError(f"Corpus scope exclusion audit lacks {key}")
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
            raise ReleasePackageError(
                f"Corpus Premium authoritative source-integrity field {key} mismatch"
            )
    if (
        type(path_policy.get("accepted_premium_bound_source_file_count")) is not int
        or path_policy.get("accepted_premium_bound_source_file_count")
        < PREMIUM_REQUIRED_DAILY_SEGMENTS
    ):
        raise ReleasePackageError(
            "Corpus Premium immutable provenance source-file coverage is incomplete"
        )
    for key in (
        "accepted_premium_source_file_set_sha256",
        "accepted_premium_message_id_set_sha256",
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", str(path_policy.get(key) or "")):
            raise ReleasePackageError(
                f"Corpus Premium authoritative source-integrity field {key} is missing"
            )
    release_path_gates = [
        row
        for row in payload.get("release_gates") or []
        if isinstance(row, dict)
        and row.get("gate")
        == "premium_journals_authoritative_v2_5_source_integrity"
    ]
    if len(release_path_gates) != 1 or release_path_gates[0] != path_policy:
        raise ReleasePackageError(
            "Corpus Premium authoritative source-integrity gate is missing, duplicated, or unbound"
        )
    relevance = payload.get("relevance_policy")
    if not isinstance(relevance, dict) or relevance.get("enabled") is not False:
        raise ReleasePackageError(
            "Scoped corpus must disable the obsolete server-wide relevance policy"
        )

    reconciliation = authorized.get("child_inventory_reconciliation")
    if not isinstance(reconciliation, dict) or reconciliation.get("provided") is not True:
        raise ReleasePackageError("Corpus Premium child reconciliation is missing")
    if not re.fullmatch(
        r"[0-9a-f]{64}", str(reconciliation.get("source_sha256") or "")
    ):
        raise ReleasePackageError("Corpus Premium reconciliation SHA-256 is missing")
    if any(
        reconciliation.get(key) is not False
        for key in ("inventory_complete", "enumeration_complete", "closure_proven")
    ):
        raise ReleasePackageError(
            "Corpus Premium lower-bound inventory must remain explicitly non-closed"
        )
    bound_inputs = reconciliation.get("bound_inputs")
    bound_inputs = bound_inputs if isinstance(bound_inputs, list) else []
    roles = {
        str(row.get("role") or "")
        for row in bound_inputs
        if isinstance(row, dict)
        and re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256") or ""))
        and str(row.get("relative_path") or "")
    }
    if roles != {
        "baseline",
        "additive_evidence_source",
        "additive_evidence_bound_partial",
    }:
        raise ReleasePackageError("Corpus Premium reconciliation bindings are incomplete")
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
            raise ReleasePackageError(
                f"Corpus Premium message-scope closure field {key} mismatch"
            )
    if closure.get("missing_date_ranges") != []:
        raise ReleasePackageError(
            "Corpus Premium message-scope closure still has missing dates"
        )
    release_closure_gates = [
        row
        for row in payload.get("release_gates") or []
        if isinstance(row, dict)
        and row.get("gate") == "premium_journals_message_data_scope_closure"
    ]
    if len(release_closure_gates) != 1 or release_closure_gates[0] != closure:
        raise ReleasePackageError(
            "Corpus Premium message-scope closure gate is missing, duplicated, or unbound"
        )

    inventory = payload.get("inventory")
    if not isinstance(inventory, dict):
        raise ReleasePackageError("Corpus manifest inventory is missing")
    require_bool_true(inventory.get("provided"), "Corpus inventory.provided")
    require_bool_true(inventory.get("validated_complete"), "Corpus inventory.validated_complete")
    if str(inventory.get("guild_id") or "") != EXPECTED_GUILD_ID:
        raise ReleasePackageError("Corpus inventory guild_id does not match the requested guild")
    require_empty(inventory.get("validation_errors"), "Corpus inventory.validation_errors")
    inventory_rows = inventory.get("containers")
    if not isinstance(inventory_rows, list):
        raise ReleasePackageError("Scoped corpus inventory containers are missing")
    derivation = inventory.get("scope_derivation")
    derivation = derivation if isinstance(derivation, dict) else {}
    if canonical_json_bytes(derivation.get("child_inventory_reconciliation")) != canonical_json_bytes(
        reconciliation
    ):
        raise ReleasePackageError(
            "Corpus inventory/reconciliation summary binding does not match"
        )
    added_ids = {
        str(value)
        for value in reconciliation.get("added_thread_ids") or []
        if re.fullmatch(r"\d{15,22}", str(value or ""))
    }
    top_level_ids: set[str] = set()
    for index, row in enumerate(inventory_rows):
        if not isinstance(row, dict):
            raise ReleasePackageError(f"Scoped inventory row {index} is not an object")
        container_id = str(row.get("container_id") or "")
        parent_id = str(row.get("parent_container_id") or "")
        if parent_id:
            if parent_id not in AUTHORIZED_PARENT_IDS:
                raise ReleasePackageError(
                    f"Scoped inventory child {container_id} has an unauthorized parent"
                )
            identity = row.get("identity_provenance")
            identity = identity if isinstance(identity, dict) else {}
            binding = identity.get("verified_parent_child_binding")
            binding = binding if isinstance(binding, dict) else {}
            binding_payload = {
                "guild_id": EXPECTED_GUILD_ID,
                "parent_container_id": parent_id,
                "child_container_id": container_id,
                "forum_card_data_list_item_id": (
                    f"forum-channel-list-{parent_id}___{container_id}"
                ),
            }
            baseline_proven = bool(
                binding.get("guild_id") == EXPECTED_GUILD_ID
                and binding.get("parent_container_id") == parent_id
                and binding.get("child_container_id") == container_id
                and binding.get("forum_card_data_list_item_id")
                == binding_payload["forum_card_data_list_item_id"]
                and binding.get("verification_method")
                == "forum_card_data_list_item_id"
                and binding.get("binding_sha256")
                == hashlib.sha256(
                    json.dumps(
                        binding_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            )
            reconciled_proven = bool(
                row.get("inventory_layer") == "reconciled_exact_forum_thread"
                and container_id in added_ids
                and identity.get("method")
                == "forum_group_header_navigation_exact"
                and identity.get("reconciliation_source_sha256")
                == reconciliation.get("source_sha256")
            )
            if not baseline_proven and not reconciled_proven:
                raise ReleasePackageError(
                    f"Scoped inventory child {container_id} lacks exact parentage proof"
                )
        else:
            top_level_ids.add(container_id)
            expected = AUTHORIZED_PARENT_IDENTITIES.get(container_id)
            if not expected or row.get("name") != expected[0] or row.get(
                "kind"
            ) != expected[1]:
                raise ReleasePackageError(
                    f"Scoped inventory parent identity mismatch for {container_id}"
                )
    if top_level_ids != AUTHORIZED_PARENT_IDS:
        raise ReleasePackageError("Scoped inventory does not contain exactly the three parents")

    coverage = payload.get("coverage")
    if not isinstance(coverage, dict):
        raise ReleasePackageError("Corpus manifest coverage is missing")
    require_empty(coverage.get("gaps"), "Corpus coverage.gaps")
    require_empty(coverage.get("file_failures"), "Corpus coverage.file_failures")

    quarantine = payload.get("quarantine")
    if not isinstance(quarantine, dict):
        raise ReleasePackageError("Corpus manifest quarantine summary is missing")
    require_empty(
        quarantine.get("unresolved_valid_message_ids"),
        "Corpus quarantine.unresolved_valid_message_ids",
    )
    for key in (
        "invalid_message_id_occurrence_count",
        "invalid_migration_sidecar_record_count",
        "unmatched_migration_sidecar_record_count",
    ):
        require_empty(quarantine.get(key), f"Corpus quarantine.{key}")

    sources = payload.get("source_files")
    if not isinstance(sources, list) or not sources:
        raise ReleasePackageError("Corpus manifest source_files must be nonempty")
    malformed = [
        index
        for index, row in enumerate(sources)
        if not isinstance(row, dict)
        or row.get("exists") is not True
        or not re.fullmatch(r"[0-9a-fA-F]{64}", str(row.get("sha256") or ""))
        or row.get("size_bytes") is None
    ]
    if malformed:
        raise ReleasePackageError(
            "Corpus manifest has unhashed/nonportable source file rows: "
            + ", ".join(map(str, malformed[:20]))
        )
    validate_structured_discord_only(payload, label="Corpus manifest")


def validate_attachment_archive_package(
    corpus_manifest: dict[str, Any],
    attachment_manifest_path: Path | None,
    attachment_archive_root: Path | None,
) -> tuple[dict[str, Any], list[tuple[str, Path, str]]]:
    summary = (
        corpus_manifest.get("attachment_archive")
        if isinstance(corpus_manifest.get("attachment_archive"), dict)
        else {}
    )
    expected_count = int(summary.get("expected_owned_attachment_count") or 0)
    provided = summary.get("provided") is True
    if not provided and expected_count == 0:
        if attachment_manifest_path is not None or attachment_archive_root is not None:
            raise ReleasePackageError(
                "Attachment package inputs were supplied but the final corpus manifest has no archive"
            )
        return {
            "required": False,
            "terminal_coverage_complete": True,
            "literal_release_complete": True,
            "byte_complete": True,
            "attachment_count": 0,
            "packaged_media_file_count": 0,
        }, []
    if expected_count < 1 or not provided:
        raise ReleasePackageError(
            "Final corpus attachment archive summary is internally inconsistent"
        )
    if attachment_manifest_path is None or attachment_archive_root is None:
        raise ReleasePackageError(
            "--attachment-manifest and --attachment-archive-root are required for this release"
        )
    manifest_path = require_regular_file(
        attachment_manifest_path,
        label="Discord attachment archive manifest",
        suffixes={".json"},
        reject_working_path=False,
    )
    manifest = stable_read_json(manifest_path, label="Discord attachment archive manifest")
    try:
        discord_attachment_archiver.validate_manifest_structure(
            manifest, require_terminal=True
        )
        verification = discord_attachment_archiver.verify_archive(
            manifest, attachment_archive_root.resolve(), require_terminal=True
        )
    except discord_attachment_archiver.AttachmentArchiveError as exc:
        raise ReleasePackageError(f"Discord attachment archive is invalid: {exc}") from exc
    if verification.get("status") != "passed":
        raise ReleasePackageError(
            "Discord attachment archive byte verification failed: "
            + json.dumps(verification.get("problems") or [], sort_keys=True)
        )
    if verification.get("literal_release_complete") is not True or int(
        (manifest.get("counts") or {}).get("failed") or 0
    ):
        raise ReleasePackageError(
            "Discord attachment archive is degraded: terminal failed rows block "
            "literal release and final packaging"
        )
    manifest_sha = sha256_file(manifest_path)
    if summary.get("manifest_sha256") != manifest_sha:
        raise ReleasePackageError(
            "Attachment manifest hash does not match the final corpus manifest"
        )
    entries = manifest.get("entries") or []
    if len(entries) != expected_count or summary.get("entry_set_parity") is not True:
        raise ReleasePackageError("Attachment manifest/corpus entry-set parity is not exact")
    if not (
        (summary.get("release_gate") or {}).get("passed") is True
        and (summary.get("release_gate") or {}).get("literal_release_complete") is True
    ):
        raise ReleasePackageError(
            "Corpus attachment literal-release gate did not pass"
        )

    destinations: list[tuple[str, Path, str]] = [
        (
            "discord_attachment_archive_manifest",
            manifest_path,
            "manifests/discord_attachment_archive_manifest.json",
        )
    ]
    seen_relative: set[str] = set()
    for entry in entries:
        candidate_records: list[tuple[str, dict[str, Any]]] = []
        if entry.get("capture_status") == "downloaded":
            candidate_records.append(("discord_attachment_bytes", entry))
        for extraction in entry.get("extraction_artifacts") or []:
            if isinstance(extraction, dict) and extraction.get("local_package_path"):
                candidate_records.append(("discord_attachment_extraction", extraction))
        for role, record in candidate_records:
            relative = str(record.get("local_package_path") or "").replace("\\", "/")
            if not relative.startswith("attachments/"):
                raise ReleasePackageError(
                    f"Attachment package path must remain under attachments/: {relative!r}"
                )
            if relative.casefold() in seen_relative:
                raise ReleasePackageError(f"Duplicate attachment package path: {relative}")
            seen_relative.add(relative.casefold())
            try:
                source = discord_attachment_archiver.resolve_under(
                    attachment_archive_root, relative, label="attachment package path"
                )
            except discord_attachment_archiver.AttachmentArchiveError as exc:
                raise ReleasePackageError(str(exc)) from exc
            source = require_regular_file(
                source,
                label="Attachment archive file",
                suffixes={source.suffix.casefold()},
                reject_working_path=False,
            )
            expected_sha = str(record.get("content_sha256") or "")
            expected_size = record.get("byte_size")
            if sha256_file(source) != expected_sha or source.stat().st_size != expected_size:
                raise ReleasePackageError(f"Attachment source file hash/size mismatch: {relative}")
            destinations.append((role, source, relative))
    return {
        "required": True,
        "manifest_sha256": manifest_sha,
        "terminal_coverage_complete": True,
        "literal_release_complete": True,
        "byte_complete": bool((manifest.get("release_gate") or {}).get("byte_complete")),
        "attachment_count": len(entries),
        "downloaded_count": int((manifest.get("counts") or {}).get("downloaded") or 0),
        "unavailable_count": int((manifest.get("counts") or {}).get("unavailable") or 0),
        "failed_count": int((manifest.get("counts") or {}).get("failed") or 0),
        "packaged_media_file_count": len(destinations) - 1,
    }, destinations


def validate_release_evidence(
    payload: dict[str, Any],
    *,
    authoritative_sha256: str,
    corpus_manifest_sha256: str,
    targeted_channel_count: int,
    authorized_scope_sha256: str,
) -> dict[str, Any]:
    if payload.get("artifact_type") != "discord_collection_progress_manifest":
        raise ReleasePackageError(
            "Post-final release evidence must be an augmented Discord collection progress manifest"
        )
    evidence = payload.get("release_evidence")
    if not isinstance(evidence, dict):
        raise ReleasePackageError("Post-final release_evidence object is missing")
    if evidence.get("artifact_type") != "discord_release_evidence":
        raise ReleasePackageError("Post-final release_evidence has the wrong artifact_type")
    if evidence.get("status") != "complete":
        raise ReleasePackageError("Post-final release_evidence status must be complete")
    utc_equal(
        evidence.get("required_cutoff_utc"),
        EXPECTED_END_UTC,
        label="Post-final release_evidence.required_cutoff_utc",
    )
    generated = parse_utc(
        evidence.get("generated_at_utc"),
        label="Post-final release_evidence.generated_at_utc",
    )
    if generated < parse_utc(EXPECTED_END_UTC, label="required release cutoff"):
        raise ReleasePackageError("Post-final release_evidence was generated before the full window ended")
    if not outside_zero(evidence.get("outside_sources_used")):
        raise ReleasePackageError("Post-final release_evidence outside_sources_used must be zero")
    require_empty(evidence.get("pending_items"), "Post-final release_evidence.pending_items")

    generator = evidence.get("generator")
    if not isinstance(generator, dict) or generator.get("local_only") is not True:
        raise ReleasePackageError("Post-final release_evidence generator must be local_only")
    for field in ("browser_calls_made", "network_calls_made", "raw_files_modified"):
        if not outside_zero(generator.get(field)):
            raise ReleasePackageError(
                f"Post-final release_evidence generator.{field} must be zero"
            )

    source_rows = evidence.get("source_artifacts")
    if not isinstance(source_rows, list) or not source_rows:
        raise ReleasePackageError("Post-final release_evidence source_artifacts is missing")
    malformed = [
        index
        for index, row in enumerate(source_rows)
        if not isinstance(row, dict)
        or not str(row.get("kind") or "").strip()
        or not str(row.get("path") or "").strip()
        or not re.fullmatch(r"[0-9a-fA-F]{64}", str(row.get("sha256") or ""))
        or not isinstance(row.get("size_bytes"), int)
        or int(row.get("size_bytes")) < 0
    ]
    if malformed:
        raise ReleasePackageError(
            "Post-final release_evidence has malformed source_artifacts: "
            + ", ".join(map(str, malformed[:20]))
        )
    hashes_by_kind: dict[str, set[str]] = {}
    for row in source_rows:
        hashes_by_kind.setdefault(str(row["kind"]), set()).add(
            str(row["sha256"]).casefold()
        )
    if authoritative_sha256.casefold() not in hashes_by_kind.get(
        "cardinal_sqlite_database", set()
    ):
        raise ReleasePackageError(
            "Post-final release_evidence does not link the supplied authoritative database hash"
        )
    if corpus_manifest_sha256.casefold() not in hashes_by_kind.get(
        "corpus_manifest", set()
    ):
        raise ReleasePackageError(
            "Post-final release_evidence does not link the supplied final corpus manifest hash"
        )

    scope_binding = evidence.get("authorized_collection_scope")
    if not (
        isinstance(scope_binding, dict)
        and scope_binding.get("status") == "passed"
        and str(scope_binding.get("source_sha256") or "").casefold()
        == authorized_scope_sha256.casefold()
        and set(scope_binding.get("authorized_parent_ids") or [])
        == AUTHORIZED_PARENT_IDS
        and scope_binding.get("premium_message_scope_closure_passed") is True
        and scope_binding.get("premium_authoritative_source_integrity_passed")
        is True
        and scope_binding.get("premium_authoritative_directory")
        == PREMIUM_AUTHORITATIVE_DIRECTORY
        and scope_binding.get("premium_legacy_directory_policy")
        == "preservation_only_not_authoritative"
        and scope_binding.get("premium_collector_version_required")
        == PREMIUM_COLLECTOR_VERSION
        and scope_binding.get("premium_accepted_daily_segment_count")
        == PREMIUM_REQUIRED_DAILY_SEGMENTS
        and scope_binding.get("premium_inventory_census_complete") is False
    ):
        raise ReleasePackageError(
            "Post-final release_evidence does not bind the exact authorized scope and Premium closure"
        )

    managed = (
        "scoped_collection_reconciliation",
        "reply_resolution",
        "attachments_and_chart_dependence",
        "claim_calibration",
    )
    for field in managed:
        value = evidence.get(field)
        rows = value if isinstance(value, list) else [value]
        if not rows or any(
            not isinstance(row, dict)
            or str(row.get("status") or "").casefold()
            not in {"passed", "complete"}
            for row in rows
        ):
            raise ReleasePackageError(
                f"Post-final release_evidence.{field} is not fully passed"
            )

    residual_reviews = evidence.get("residual_reviews")
    review_packets = payload.get("release_review_packets")
    zero_targeted_no_review = bool(
        targeted_channel_count == 0
        and residual_reviews == []
        and isinstance(review_packets, dict)
        and review_packets.get("artifact_type") == "discord_residual_review_packets"
        and review_packets.get("review_required") is False
        and type(review_packets.get("packet_count")) is int
        and review_packets.get("packet_count") == 0
        and review_packets.get("packets") == []
    )
    if not zero_targeted_no_review:
        raise ReleasePackageError(
            "Post-final release_evidence.residual_reviews may be empty only for the "
            "canonical zero-targeted plan with explicit review_required=false, "
            "packet_count=0, and no review packets"
        )
    validate_structured_discord_only(evidence, label="Post-final release_evidence")
    return {
        "status": "passed",
        "required_cutoff_utc": EXPECTED_END_UTC,
        "authoritative_database_hash_linked": True,
        "corpus_manifest_hash_linked": True,
        "source_artifact_count": len(source_rows),
    }


def validate_qa_report(
    payload: dict[str, Any],
    *,
    authoritative_sha256: str,
    release_evidence_path: Path,
) -> None:
    if payload.get("artifact_type") != "independent_discord_corpus_validation":
        raise ReleasePackageError("QA report has the wrong artifact_type")
    if payload.get("status") != "passed" or payload.get("overall_assessment") != "Ready to share":
        raise ReleasePackageError("QA report must be passed and Ready to share")
    validate_qa_scope(payload.get("scope"))
    failures = payload.get("failure_counts")
    if not isinstance(failures, dict) or any(int(value or 0) != 0 for value in failures.values()):
        raise ReleasePackageError("QA report has nonzero failure counts")
    checks = payload.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ReleasePackageError("QA report has no independent checks")
    failed = [
        str(row.get("name") or index)
        for index, row in enumerate(checks)
        if not isinstance(row, dict) or row.get("passed") is not True
    ]
    if failed:
        raise ReleasePackageError("QA report contains failed/skipped checks: " + ", ".join(failed[:30]))
    check_names = {
        str(row.get("name") or "")
        for row in checks
        if isinstance(row, dict) and row.get("passed") is True
    }
    if "collection_drift_final_audit_passed" not in check_names:
        raise ReleasePackageError(
            "QA report does not contain the passed final collection-drift audit gate"
        )
    validate_relevance_policy(payload.get("relevance_policy"), label="QA relevance_policy")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise ReleasePackageError("QA inputs are missing")
    qa_evidence_path = Path(str(inputs.get("post_final_release_evidence") or ""))
    if not str(qa_evidence_path) or qa_evidence_path.resolve() != release_evidence_path.resolve():
        raise ReleasePackageError(
            "QA report does not identify the supplied scoped post-final release-evidence artifact"
        )
    qa_drift_path_text = str(inputs.get("collection_drift_audit") or "").strip()
    if not qa_drift_path_text:
        raise ReleasePackageError("QA report does not identify the final collection-drift audit")

    drift = payload.get("collection_drift_audit")
    if not isinstance(drift, dict):
        raise ReleasePackageError("QA final collection-drift audit summary is missing")
    drift_summary = drift.get("summary")
    if not (
        drift.get("status") == "passed"
        and drift.get("passed") is True
        and drift.get("mode") == "final"
        and drift.get("overall_status") == "PASS"
        and drift.get("release_gate_passed") is True
        and re.fullmatch(r"[0-9a-fA-F]{64}", str(drift.get("sha256") or ""))
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
        raise ReleasePackageError(
            "QA final collection-drift audit is not PASS with zero unresolved drift"
        )
    drift_path_text = str(drift.get("path") or "").strip()
    if not drift_path_text or Path(drift_path_text).resolve() != Path(
        qa_drift_path_text
    ).resolve():
        raise ReleasePackageError(
            "QA final collection-drift summary is not bound to its declared input path"
        )

    database_validation = payload.get("database_validation")
    if not isinstance(database_validation, dict) or database_validation.get("status") not in {
        "passed",
        "inspected",
    }:
        raise ReleasePackageError("QA database_validation must be completed")
    if str(database_validation.get("sha256") or "").casefold() != authoritative_sha256.casefold():
        raise ReleasePackageError(
            "QA database_validation does not link the supplied authoritative database hash"
        )
    for group_name in ("preservation", "source_hash_verification"):
        group = payload.get(group_name)
        if not isinstance(group, dict):
            raise ReleasePackageError(f"QA {group_name} is missing")
        for phase in ("before", "after"):
            row = group.get(phase)
            if not isinstance(row, dict) or row.get("status") != "passed":
                raise ReleasePackageError(f"QA {group_name}.{phase} must be passed")
    validate_structured_discord_only(payload, label="QA report")


def sqlite_sidecar_paths(path: Path) -> list[Path]:
    return [Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal")]


@contextlib.contextmanager
def readonly_sqlite(path: Path):
    sidecars = [sidecar for sidecar in sqlite_sidecar_paths(path) if sidecar.exists()]
    if sidecars:
        raise ReleasePackageError(
            "Final SQLite must be self-contained; sidecar files exist: "
            + ", ".join(str(item) for item in sidecars)
        )
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.DatabaseError as exc:
        raise ReleasePackageError(f"Cannot open SQLite read-only: {path}: {exc}") from exc
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        if int(con.execute("PRAGMA query_only").fetchone()[0]) != 1:
            raise ReleasePackageError(f"SQLite query_only could not be enabled: {path}")
        try:
            con.execute("CREATE TABLE __release_write_probe__(value INTEGER)")
        except sqlite3.DatabaseError:
            pass
        else:
            raise ReleasePackageError(f"SQLite write probe unexpectedly succeeded: {path}")
        yield con
    finally:
        con.close()


def sqlite_objects(con: sqlite3.Connection) -> dict[str, str]:
    return {
        str(row[0]): str(row[1])
        for row in con.execute(
            "SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
    }


def validate_sqlite_basics(con: sqlite3.Connection, *, label: str) -> dict[str, Any]:
    integrity_rows = [str(row[0]) for row in con.execute("PRAGMA integrity_check")]
    if integrity_rows != ["ok"]:
        raise ReleasePackageError(f"{label} integrity_check failed: {integrity_rows[:10]}")
    foreign_keys = list(con.execute("PRAGMA foreign_key_check"))
    if foreign_keys:
        raise ReleasePackageError(f"{label} has {len(foreign_keys)} foreign-key violation(s)")
    header = con.execute("PRAGMA schema_version").fetchone()
    if header is None:
        raise ReleasePackageError(f"{label} has no readable SQLite schema")
    return {
        "integrity_check": "ok",
        "foreign_key_violations": 0,
        "application_id": int(con.execute("PRAGMA application_id").fetchone()[0]),
        "user_version": int(con.execute("PRAGMA user_version").fetchone()[0]),
        "query_only": True,
        "immutable_read_only_open": True,
        "self_contained_no_sidecars": True,
    }


def key_value_table(con: sqlite3.Connection, table: str) -> dict[str, str]:
    return {str(row[0]): str(row[1]) for row in con.execute(f'SELECT key,value FROM "{table}"')}


def ensure_sqlite_scope_and_provenance(con: sqlite3.Connection, *, label: str) -> None:
    rows = list(con.execute("SELECT * FROM collection_runs ORDER BY run_id"))
    if len(rows) != 1:
        raise ReleasePackageError(f"{label} must contain exactly one collection run")
    row = rows[0]
    required_columns = {
        "guild_id",
        "window_start_utc",
        "window_end_utc",
        "source_scope",
        "outside_sources_used",
        "status",
    }
    if not required_columns <= set(row.keys()):
        raise ReleasePackageError(f"{label} collection_runs is missing release columns")
    if str(row["guild_id"]) != EXPECTED_GUILD_ID:
        raise ReleasePackageError(f"{label} guild_id is outside the requested Discord")
    utc_equal(row["window_start_utc"], EXPECTED_START_UTC, label=f"{label} window_start_utc")
    utc_equal(row["window_end_utc"], EXPECTED_END_UTC, label=f"{label} window_end_utc")
    if str(row["status"]) != "complete":
        raise ReleasePackageError(f"{label} collection run is not complete")
    if str(row["source_scope"]) != "discord_only" or not outside_zero(row["outside_sources_used"]):
        raise ReleasePackageError(f"{label} collection run is not Discord-only")

    analysis_rows = list(con.execute("SELECT * FROM analysis_runs ORDER BY analysis_run_id"))
    if len(analysis_rows) != 1:
        raise ReleasePackageError(f"{label} must contain exactly one analysis run")
    analysis = analysis_rows[0]
    if str(analysis["source_scope"]) != "discord_only" or not outside_zero(analysis["outside_sources_used"]):
        raise ReleasePackageError(f"{label} analysis run is not Discord-only")

    audit_count = int(con.execute("SELECT COUNT(*) FROM v_discord_only_audit").fetchone()[0])
    if audit_count:
        raise ReleasePackageError(f"{label} Discord-only audit has {audit_count} issue(s)")


def validate_analysis_documents(con: sqlite3.Connection, *, label: str) -> None:
    rows = list(con.execute("SELECT document_name,content_json FROM analysis_documents"))
    names = {str(row[0]) for row in rows}
    missing = sorted(REQUIRED_ANALYSIS_DOCUMENTS - names)
    if missing:
        raise ReleasePackageError(f"{label} lacks required analysis documents: {', '.join(missing)}")
    documents: dict[str, Any] = {}
    for row in rows:
        try:
            documents[str(row[0])] = json.loads(str(row[1]))
        except json.JSONDecodeError as exc:
            raise ReleasePackageError(f"{label} has invalid analysis document JSON: {row[0]}") from exc
    coverage = documents["discord_analysis_coverage"]
    if not isinstance(coverage, dict):
        raise ReleasePackageError(f"{label} analysis coverage document is malformed")
    if coverage.get("analysis_completeness") != "complete":
        raise ReleasePackageError(f"{label} analysis coverage is not complete")
    if coverage.get("collection_run_status") != "complete":
        raise ReleasePackageError(f"{label} analysis coverage does not link a complete run")
    if int(coverage.get("gap_count") or 0) != 0:
        raise ReleasePackageError(f"{label} analysis coverage records gaps")
    validate_structured_discord_only(documents, label=f"{label} analysis documents")


def manifest_authorized_container_ids(manifest: dict[str, Any]) -> set[str]:
    inventory = manifest.get("inventory")
    rows = inventory.get("containers") if isinstance(inventory, dict) else []
    return {
        str(row.get("container_id") or "")
        for row in rows or []
        if isinstance(row, dict) and str(row.get("container_id") or "")
    }


def attachment_release_digest(con: sqlite3.Connection) -> str:
    columns = {str(row[1]) for row in con.execute("PRAGMA table_info(attachments)")}
    missing = sorted(set(ATTACHMENT_RELEASE_COLUMNS) - columns)
    if missing:
        raise ReleasePackageError(
            "attachments table lacks release ownership columns: " + ", ".join(missing)
        )
    selected = ",".join(f'"{name}"' for name in ATTACHMENT_RELEASE_COLUMNS)
    digest = hashlib.sha256()
    for row in con.execute(
        f'SELECT {selected} FROM attachments ORDER BY attachment_id'
    ):
        digest.update(
            json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        digest.update(b"\n")
    return digest.hexdigest()


def validate_attachment_ownership_boundary(
    con: sqlite3.Connection, *, label: str
) -> dict[str, Any]:
    digest = attachment_release_digest(con)
    invalid = int(
        con.execute(
            """
            SELECT COUNT(*) FROM attachments a
            WHERE json_valid(a.ownership_evidence_json)=0
               OR json_type(a.ownership_evidence_json)<>'object'
               OR a.ownership_status NOT IN ('owned_exact','non_owned_exact')
               OR (a.ownership_status='owned_exact' AND (
                    a.relation_type NOT IN ('owned','attachment','message_attachment')
                    OR a.owned_for_capture<>1
                    OR a.eligible_for_attachment_evidence<>1
                    OR json_extract(a.ownership_evidence_json,'$.exact')<>1
                    OR json_extract(a.ownership_evidence_json,'$.owner_message_id')<>a.message_id
                    OR json_extract(a.ownership_evidence_json,'$.owner_channel_id')<>a.source_channel_id
                  ))
               OR (a.ownership_status='non_owned_exact' AND (
                    a.relation_type NOT IN ('embedded_external','copied_media','non_owned')
                    OR a.owned_for_capture<>0
                    OR a.eligible_for_attachment_evidence<>0
                    OR json_extract(a.ownership_evidence_json,'$.exact')<>1
                    OR json_extract(a.ownership_evidence_json,'$.owner_message_id')<>a.message_id
                    OR json_extract(a.ownership_evidence_json,'$.source_channel_id')<>a.source_channel_id
                    OR COALESCE(TRIM(json_extract(a.ownership_evidence_json,'$.dom_relation')),'')=''
                    OR a.capture_status<>'metadata_only'
                    OR a.capture_terminal<>0
                    OR a.capture_attempt_count<>0
                    OR json_array_length(a.capture_attempts_json)<>0
                    OR a.capture_failure_code IS NOT NULL
                    OR a.capture_failure_detail IS NOT NULL
                    OR a.local_package_path IS NOT NULL
                    OR a.content_sha256 IS NOT NULL
                    OR a.extraction_status<>'not_attempted'
                    OR json_array_length(a.extraction_artifacts_json)<>0
                    OR a.archive_manifest_source_file_id IS NOT NULL
                    OR a.chart_claim_eligible<>0
                    OR EXISTS(SELECT 1 FROM attachment_extractions x
                              WHERE x.attachment_id=a.attachment_id)
                    OR EXISTS(SELECT 1 FROM evidence_items e
                              WHERE e.attachment_id=a.attachment_id)
                  ))
            """
        ).fetchone()[0]
    )
    if invalid:
        raise ReleasePackageError(
            f"{label} has {invalid} unresolved, mislabeled, or byte/evidence-bearing non-owned attachment row(s)"
        )
    return {
        "attachment_count": table_count(con, "attachments"),
        "owned_exact_count": int(
            con.execute(
                "SELECT COUNT(*) FROM attachments WHERE ownership_status='owned_exact'"
            ).fetchone()[0]
        ),
        "non_owned_exact_count": int(
            con.execute(
                "SELECT COUNT(*) FROM attachments WHERE ownership_status='non_owned_exact'"
            ).fetchone()[0]
        ),
        "release_projection_sha256": digest,
    }


def validate_full_database(
    path: Path, *, manifest_sha256: str, manifest_payload: dict[str, Any]
) -> dict[str, Any]:
    with readonly_sqlite(path) as con:
        result = validate_sqlite_basics(con, label="Authoritative database")
        objects = sqlite_objects(con)
        required_tables = {
            "meta",
            "collection_runs",
            "analysis_runs",
            "analysis_documents",
            "source_artifacts",
            *CORE_SHARED_TABLES,
        }
        required_views = {"v_collection_gaps", "v_discord_only_audit"}
        missing = sorted(
            {name for name in required_tables if objects.get(name) != "table"}
            | {name for name in required_views if objects.get(name) != "view"}
        )
        if missing:
            raise ReleasePackageError(
                "Authoritative database lacks required Cardinal objects: " + ", ".join(missing)
            )
        if "llm_manifest" in objects:
            raise ReleasePackageError("Authoritative database is a compact companion, not the full Cardinal database")
        meta = key_value_table(con, "meta")
        if not str(meta.get("schema_version") or "").startswith("2."):
            raise ReleasePackageError("Authoritative database is not Cardinal schema v2")
        if meta.get("source_scope") != "discord_only" or not outside_zero(meta.get("outside_sources_used")):
            raise ReleasePackageError("Authoritative database meta is not Discord-only")
        expected_scope_sha = str(
            manifest_payload["authorized_collection_scope"]["source_sha256"]
        ).casefold()
        if meta.get("authorized_collection_scope_enabled") != "1" or str(
            meta.get("authorized_collection_scope_sha256") or ""
        ).casefold() != expected_scope_sha:
            raise ReleasePackageError(
                "Authoritative database does not bind the exact authorized scope SHA-256"
            )
        try:
            meta_parent_ids = set(
                json.loads(meta.get("authorized_parent_container_ids_json") or "[]")
            )
        except json.JSONDecodeError as exc:
            raise ReleasePackageError(
                "Authoritative database authorized parent metadata is invalid"
            ) from exc
        if meta_parent_ids != AUTHORIZED_PARENT_IDS:
            raise ReleasePackageError(
                "Authoritative database authorized parent metadata is not the exact three-channel set"
            )
        ensure_sqlite_scope_and_provenance(con, label="Authoritative database")
        validate_analysis_documents(con, label="Authoritative database")
        attachment_boundary = validate_attachment_ownership_boundary(
            con, label="Authoritative database"
        )
        gaps = int(con.execute("SELECT COUNT(*) FROM v_collection_gaps").fetchone()[0])
        if gaps:
            raise ReleasePackageError(f"Authoritative database has {gaps} unresolved collection gap(s)")
        source_hashes = {
            str(row[0]).casefold()
            for row in con.execute("SELECT sha256 FROM source_artifacts WHERE sha256 IS NOT NULL")
        }
        if manifest_sha256.casefold() not in source_hashes:
            raise ReleasePackageError(
                "Authoritative database source_artifacts does not contain the supplied final corpus manifest hash"
            )
        source_columns = {
            str(row[1]) for row in con.execute("PRAGMA table_info(source_artifacts)")
        }
        if "source_file" not in source_columns:
            raise ReleasePackageError(
                "Authoritative database source_artifacts lacks portable source paths"
            )
        unsafe_sources = [
            str(row[0])
            for row in con.execute(
                "SELECT source_file FROM source_artifacts WHERE source_file IS NOT NULL"
            )
            if Path(str(row[0])).is_absolute()
            or re.match(r"^[A-Za-z]:[\\/]", str(row[0]))
            or ".." in Path(str(row[0]).replace("\\", "/")).parts
        ]
        if unsafe_sources:
            raise ReleasePackageError(
                "Authoritative database contains nonportable absolute/escaping source paths"
            )
        message_columns = {
            str(row[1]) for row in con.execute("PRAGMA table_info(messages)")
        }
        if not {
            "message_id",
            "channel_id",
            "content_text",
            "raw_json",
        } <= message_columns:
            raise ReleasePackageError(
                "Authoritative database messages table lacks scoped identity/content/raw columns"
            )
        semantic_rows: list[dict[str, Any]] = []
        for database_message_id, raw_json in con.execute(
            "SELECT message_id,raw_json FROM messages ORDER BY message_id"
        ):
            try:
                raw_row = json.loads(str(raw_json))
            except (TypeError, json.JSONDecodeError) as exc:
                raise ReleasePackageError(
                    "Authoritative database contains unreadable message raw_json"
                ) from exc
            if not isinstance(raw_row, dict) or str(
                raw_row.get("message_id") or ""
            ) != str(database_message_id):
                raise ReleasePackageError(
                    "Authoritative database message raw_json identity mismatch"
                )
            semantic_rows.append(raw_row)
        semantic_errors = (
            reply_provenance_contract.release_executed_command_semantic_errors(
                semantic_rows,
                manifest_payload.get(
                    "executed_command_reply_provenance_integrity"
                ),
            )
        )
        if semantic_errors:
            raise ReleasePackageError(
                "Authoritative database executed-command row audit failed: "
                + ", ".join(semantic_errors)
            )
        allowed_ids = manifest_authorized_container_ids(manifest_payload)
        outside_message_count = int(
            con.execute(
                "SELECT COUNT(*) FROM messages WHERE channel_id NOT IN (%s)"
                % ",".join("?" for _ in allowed_ids),
                tuple(sorted(allowed_ids)),
            ).fetchone()[0]
        )
        if outside_message_count:
            raise ReleasePackageError(
                f"Authoritative database contains {outside_message_count} out-of-scope message(s)"
            )
        result.update(
            {
                "role": "authoritative_full_cardinal_v2",
                "schema_version": meta["schema_version"],
                "collection_status": "complete",
                "source_scope": "discord_only",
                "outside_sources_used": 0,
                "collection_gaps": 0,
                "manifest_hash_linked": True,
                "attachment_ownership_boundary": attachment_boundary,
            }
        )
        return result


def table_count(con: sqlite3.Connection, table: str) -> int:
    return int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def table_identity_content_digest(con: sqlite3.Connection, table: str) -> str:
    info = list(con.execute(f'PRAGMA table_info("{table}")'))
    columns = [str(row[1]) for row in info]
    if not columns:
        raise ReleasePackageError(f"Table {table} has no columns")
    primary = [
        str(row[1])
        for row in sorted(info, key=lambda item: int(item[5] or 0))
        if int(row[5] or 0) > 0
    ]
    order_columns = primary or columns
    quoted_columns = ",".join(f'"{name}"' for name in columns)
    quoted_order = ",".join(f'"{name}"' for name in order_columns)
    digest = hashlib.sha256()
    for row in con.execute(
        f'SELECT {quoted_columns} FROM "{table}" ORDER BY {quoted_order}'
    ):
        digest.update(
            json.dumps(
                list(row),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def validate_compact_database(
    path: Path,
    *,
    authoritative_path: Path,
    authoritative_sha256: str,
    authorized_scope_sha256: str,
) -> dict[str, Any]:
    with readonly_sqlite(path) as con:
        result = validate_sqlite_basics(con, label="Compact database")
        objects = sqlite_objects(con)
        required_tables = {
            "llm_manifest",
            "source_meta",
            "collection_runs",
            "analysis_runs",
            "analysis_documents",
            "query_rejection_blocks",
            "query_qa",
            "query_trade_episodes",
            "query_confluence_profiles",
            "query_models",
            "query_setup_cards",
            "query_collection_gaps",
            *CORE_SHARED_TABLES,
        }
        required_views = {"v_discord_only_audit"}
        missing = sorted(
            {name for name in required_tables if objects.get(name) != "table"}
            | {name for name in required_views if objects.get(name) != "view"}
        )
        if missing:
            raise ReleasePackageError(
                "Compact database lacks required LLM query objects: " + ", ".join(missing)
            )
        manifest = key_value_table(con, "llm_manifest")
        if manifest.get("source_database_sha256", "").casefold() != authoritative_sha256.casefold():
            raise ReleasePackageError("Compact database does not link to the supplied authoritative database hash")
        if manifest.get("source_database_is_authoritative") != "1":
            raise ReleasePackageError("Compact database does not mark its source as authoritative")
        if manifest.get("companion_role") != "portable_query_snapshot":
            raise ReleasePackageError("Compact database has the wrong companion_role")
        if manifest.get("source_scope") != "discord_only" or not outside_zero(manifest.get("outside_sources_used")):
            raise ReleasePackageError("Compact llm_manifest is not Discord-only")
        source_meta = key_value_table(con, "source_meta")
        if source_meta.get("source_scope") != "discord_only" or not outside_zero(source_meta.get("outside_sources_used")):
            raise ReleasePackageError("Compact source_meta is not Discord-only")
        if not str(source_meta.get("schema_version") or "").startswith("2."):
            raise ReleasePackageError("Compact database does not identify a Cardinal v2 source")
        if source_meta.get("authorized_collection_scope_enabled") != "1" or str(
            source_meta.get("authorized_collection_scope_sha256") or ""
        ).casefold() != authorized_scope_sha256.casefold():
            raise ReleasePackageError(
                "Compact database does not bind the exact authorized scope SHA-256"
            )
        ensure_sqlite_scope_and_provenance(con, label="Compact database")
        validate_analysis_documents(con, label="Compact database")
        compact_attachment_boundary = validate_attachment_ownership_boundary(
            con, label="Compact database"
        )
        audit_count = int(con.execute("SELECT COUNT(*) FROM v_discord_only_audit").fetchone()[0])
        if audit_count:
            raise ReleasePackageError(f"Compact database Discord-only audit has {audit_count} row(s)")
        gap_count = table_count(con, "query_collection_gaps")
        if gap_count:
            raise ReleasePackageError(f"Compact database has {gap_count} unresolved collection gap(s)")
        compact_counts = {name: table_count(con, name) for name in CORE_SHARED_TABLES}
        compact_digests = {
            name: table_identity_content_digest(con, name)
            for name in CORE_SHARED_TABLES
        }

    with readonly_sqlite(authoritative_path) as source:
        source_attachment_boundary = validate_attachment_ownership_boundary(
            source, label="Authoritative database"
        )
        source_counts = {name: table_count(source, name) for name in CORE_SHARED_TABLES}
        source_digests = {
            name: table_identity_content_digest(source, name)
            for name in CORE_SHARED_TABLES
        }
    differences = {
        name: {"authoritative": source_counts[name], "compact": compact_counts[name]}
        for name in CORE_SHARED_TABLES
        if source_counts[name] != compact_counts[name]
    }
    if differences:
        raise ReleasePackageError(
            "Compact database core-table counts differ from the authoritative database: "
            + json.dumps(differences, sort_keys=True)
        )
    digest_differences = {
        name: {
            "authoritative": source_digests[name],
            "compact": compact_digests[name],
        }
        for name in CORE_SHARED_TABLES
        if source_digests[name] != compact_digests[name]
    }
    if digest_differences:
        raise ReleasePackageError(
            "Compact database core-table identity/content differs from the authoritative database: "
            + json.dumps(digest_differences, sort_keys=True)
        )
    if compact_attachment_boundary != source_attachment_boundary:
        raise ReleasePackageError(
            "Compact attachment ownership/archive projection differs from the authoritative database"
        )
    result.update(
        {
            "role": "compact_llm_query_snapshot",
            "source_database_sha256": authoritative_sha256,
            "source_hash_linked": True,
            "source_scope": "discord_only",
            "outside_sources_used": 0,
            "collection_status": "complete",
            "collection_gaps": 0,
            "core_table_counts_match_authoritative": True,
            "core_table_counts": compact_counts,
            "core_table_identity_content_parity": True,
            "core_table_identity_content_sha256": compact_digests,
            "attachment_ownership_projection_parity": True,
            "attachment_ownership_boundary": compact_attachment_boundary,
        }
    )
    return result


def validate_research_json(payload: dict[str, Any], *, database_sha256: str, label: str) -> None:
    if payload.get("report_type") != "technical_evidence_report":
        raise ReleasePackageError(f"{label} has the wrong report_type")
    if payload.get("claim_scope") != "discord_only" or not outside_zero(payload.get("outside_sources_used")):
        raise ReleasePackageError(f"{label} is not Discord-only")
    input_db = payload.get("input_database")
    if not isinstance(input_db, dict) or str(input_db.get("sha256") or "").casefold() != database_sha256.casefold():
        raise ReleasePackageError(f"{label} does not link the authoritative database hash")
    release_validation = payload.get("release_validation")
    if not isinstance(release_validation, dict) or release_validation.get("status") != "passed":
        raise ReleasePackageError(f"{label} release_validation is not passed")
    failed = [key for key, value in release_validation.items() if isinstance(value, bool) and value is not True]
    if failed:
        raise ReleasePackageError(f"{label} has failed release-validation flags: {', '.join(failed)}")
    scope = payload.get("scope_and_coverage")
    if not isinstance(scope, dict):
        raise ReleasePackageError(f"{label} scope_and_coverage is missing")
    if str(scope.get("guild_id") or "") != EXPECTED_GUILD_ID:
        raise ReleasePackageError(f"{label} guild_id is wrong")
    utc_equal(scope.get("window_start_utc"), EXPECTED_START_UTC, label=f"{label} window_start_utc")
    utc_equal(scope.get("window_end_utc"), EXPECTED_END_UTC, label=f"{label} window_end_utc")
    if scope.get("collection_status") != "complete" or scope.get("analysis_completeness") != "complete":
        raise ReleasePackageError(f"{label} is not based on complete collection and analysis")
    if int(scope.get("gap_count") or 0) != 0:
        raise ReleasePackageError(f"{label} records collection gaps")
    if scope.get("source_scope") != "discord_only" or not outside_zero(scope.get("outside_sources_used")):
        raise ReleasePackageError(f"{label} scope is not Discord-only")
    required_sections = {
        "rejection_blocks",
        "trade_profiles",
        "model_cards",
        "question_and_answer_catalog",
        "analysis_methodology",
        "evidence_catalog",
    }
    missing = sorted(required_sections - set(payload))
    if missing:
        raise ReleasePackageError(f"{label} lacks detailed report sections: {', '.join(missing)}")
    models = payload.get("model_cards")
    if not isinstance(models, dict) or int(models.get("models_emitted") or 0) > 5:
        raise ReleasePackageError(f"{label} violates the five-model maximum")
    validate_structured_discord_only(payload, label=label)


def validate_research_markdown(path: Path, *, database_sha256: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ReleasePackageError(f"Research Markdown is not UTF-8: {path}") from exc
    required = (
        "Discord-Only Rejection Block and Trading Model Research",
        EXPECTED_START_UTC,
        EXPECTED_END_UTC,
        "Rejection block",
        "Strict self-reported win and loss profiles",
        "Evidence-backed trading model cards",
        "Relevant Discord questions and captured answers",
        "Source scope: `discord_only`; outside sources used: `0`",
        "browse the web",
        database_sha256,
    )
    missing = [token for token in required if token.casefold() not in text.casefold()]
    if missing:
        raise ReleasePackageError(
            f"Research Markdown is not the detailed final report; missing: {', '.join(missing)}"
        )
    if "{{" in text or "}}" in text:
        raise ReleasePackageError("Research Markdown contains unresolved template placeholders")


def validate_llm_guide(path: Path, *, full_sha256: str, compact_sha256: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ReleasePackageError(f"LLM handoff guide is not UTF-8: {path}") from exc
    if "{{" in text or "}}" in text:
        raise ReleasePackageError("LLM handoff guide contains unresolved template placeholders")
    required = (
        "Discord-only",
        "Full analyzed SQLite database",
        "Compact LLM SQLite companion",
        "Do not add web knowledge",
        "## Deterministic release binding",
        EXPECTED_GUILD_ID,
        EXPECTED_START_UTC,
        EXPECTED_END_UTC,
        "databases/authoritative_cardinal.sqlite",
        "databases/compact_llm.sqlite",
        "manifests/corpus_coverage_manifest.json",
        "qa/independent_qa_report.json",
        "NOT_PACKAGED",
        full_sha256,
        compact_sha256,
    )
    missing = [token for token in required if token.casefold() not in text.casefold()]
    if missing:
        raise ReleasePackageError(
            "LLM handoff guide is not final or lacks release safeguards; missing: "
            + ", ".join(missing)
        )
    has_iso_dates = EXPECTED_START_DATE in text and EXPECTED_END_DATE in text
    has_natural_dates = "January 1" in text and "July 20, 2026" in text
    if not (has_iso_dates or has_natural_dates):
        raise ReleasePackageError("LLM handoff guide lacks the exact Jan 1–Jul 20, 2026 scope")


def media_type(path: Path) -> str:
    return {
        ".sqlite": "application/vnd.sqlite3",
        ".db": "application/vnd.sqlite3",
        ".json": "application/json",
        ".md": "text/markdown; charset=utf-8",
    }.get(path.suffix.casefold(), "application/octet-stream")


def build_index_readme(package_id: str, entries: list[dict[str, Any]]) -> str:
    lines = [
        "# Cardinal Discord trading-research release",
        "",
        f"Package ID: `{package_id}`",
        "",
        (
            "This package contains the release-complete, Discord-only research corpus outputs "
            f"for guild `{EXPECTED_GUILD_ID}`, covering Jan 1 through Jul 20, 2026 in "
            f"`{EXPECTED_TIMEZONE}` (`{EXPECTED_START_UTC}` to `{EXPECTED_END_UTC}`, end-exclusive)."
        ),
        "Authorized message sources: `student-breakdowns`, `premium-journals`, and `questions`, "
        "plus only exact child threads whose parentage is proven by authenticated Discord evidence.",
        (
            f"Premium Journals authoritative daily canonicals come only from "
            f"`{PREMIUM_AUTHORITATIVE_DIRECTORY}` using collector v{PREMIUM_COLLECTOR_VERSION}; "
            f"all {PREMIUM_REQUIRED_DAILY_SEGMENTS} daily routes are closed. Legacy Premium files "
            f"under `{PREMIUM_LEGACY_DIRECTORY}` are preservation-only, and the lower-bound child "
            "inventory is not represented as a complete census."
        ),
        "",
        "Open either SQLite database read-only. Recommended URI parameters are "
        "`mode=ro&immutable=1`; do not create or rely on WAL/SHM sidecars. Start with the compact "
        "database for LLM questions and use the authoritative database for complete raw provenance.",
        "",
        "No web, market-data, or other outside source was used. Discord links stored in messages "
        "are provenance text, not external research evidence. Trading rates are descriptive, "
        "self-reported, overlapping, author-clustered, and non-causal—not forecasts.",
        "",
        "When present, `attachments/` contains SHA-256-verified local mirrors of Discord-owned "
        "media only. A final package may preserve substantiated terminal unavailable rows, but "
        "terminal failed rows are degraded and block publication. Media presence or filenames "
        "are not chart evidence; chart-dependent claims require an exact linked complete/partial "
        "local extraction artifact whose bytes were verified.",
        "",
        "## Files",
        "",
        "| File | Role | Bytes | SHA-256 |",
        "|---|---|---:|---|",
    ]
    for row in entries:
        lines.append(
            f"| `{row['path']}` | {row['role'].replace('_', ' ')} | "
            f"{int(row['size_bytes']):,} | `{row['sha256']}` |"
        )
    lines.extend(
        [
            "",
            "`RELEASE_MANIFEST.sha256.json` is the machine-readable size/hash inventory. "
            "Its own hash is intentionally excluded to avoid a circular manifest.",
            "",
        ]
    )
    return "\n".join(lines)


def ensure_safe_output_target(output_dir: Path, *, allow_existing_empty: bool) -> tuple[Path, bool]:
    if output_dir.absolute().is_symlink():
        raise ReleasePackageError("Release output must not be a symbolic link")
    output = output_dir.resolve()
    if output == Path(output.anchor):
        raise ReleasePackageError("Refusing to use a filesystem root as the release output")
    reject_nonfinal_path(output, label="Release output directory")
    existed_empty = False
    if output.exists():
        if not allow_existing_empty:
            raise ReleasePackageError(
                f"Output already exists: {output}; use --allow-existing-empty-target only for a verified empty directory"
            )
        if output.is_symlink() or not output.is_dir():
            raise ReleasePackageError("Existing output target must be a real directory")
        if any(output.iterdir()):
            raise ReleasePackageError("Existing output target is not empty")
        existed_empty = True
    return output, existed_empty


def copy_verified(source: Path, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    observed = sha256_file(destination)
    if observed != expected_sha256:
        raise ReleasePackageError(
            f"Copied artifact hash mismatch for {destination}: {observed} != {expected_sha256}"
        )


def package_release(
    *,
    authoritative_db: Path,
    compact_db: Path,
    corpus_manifest: Path,
    release_evidence: Path,
    qa_report: Path,
    research_markdown: Sequence[Path],
    research_json: Sequence[Path],
    llm_handoff_guide: Path,
    output_dir: Path,
    attachment_manifest: Path | None = None,
    attachment_archive_root: Path | None = None,
    allow_existing_empty_target: bool = False,
) -> dict[str, Any]:
    if not research_markdown or not research_json:
        raise ReleasePackageError("At least one explicit Markdown and one explicit JSON research report are required")

    full = require_regular_file(authoritative_db, label="Authoritative database", suffixes={".sqlite", ".db"})
    compact = require_regular_file(compact_db, label="Compact database", suffixes={".sqlite", ".db"})
    manifest_path = require_regular_file(corpus_manifest, label="Corpus/coverage manifest", suffixes={".json"})
    evidence_path = require_regular_file(
        release_evidence,
        label="Post-final release evidence",
        suffixes={".json"},
        # build_release_evidence.py deliberately writes only beneath working/;
        # content and final-hash linkage, not the directory name, prove finality.
        reject_working_path=False,
    )
    qa_path = require_regular_file(qa_report, label="QA report", suffixes={".json"})
    guide = require_regular_file(llm_handoff_guide, label="LLM handoff guide", suffixes={".md"})
    markdown_paths = [
        require_regular_file(path, label="Research Markdown", suffixes={".md"})
        for path in research_markdown
    ]
    json_paths = [
        require_regular_file(path, label="Research JSON", suffixes={".json"})
        for path in research_json
    ]
    manifest_payload = stable_read_json(manifest_path, label="Corpus/coverage manifest")
    validate_corpus_manifest(manifest_payload)
    attachment_validation, attachment_destinations = validate_attachment_archive_package(
        manifest_payload,
        attachment_manifest,
        attachment_archive_root,
    )
    all_sources = [
        full,
        compact,
        manifest_path,
        evidence_path,
        qa_path,
        guide,
        *markdown_paths,
        *json_paths,
        *(source for _role, source, _relative in attachment_destinations),
    ]
    if len(set(all_sources)) != len(all_sources):
        raise ReleasePackageError("Every input role must refer to a distinct explicit file")

    output, existing_empty = ensure_safe_output_target(
        output_dir, allow_existing_empty=allow_existing_empty_target
    )
    for source in all_sources:
        try:
            source.relative_to(output)
        except ValueError:
            pass
        else:
            raise ReleasePackageError(f"A source artifact is inside the requested output directory: {source}")

    source_hashes_before = {path: sha256_file(path) for path in all_sources}
    evidence_payload = stable_read_json(
        evidence_path, label="Post-final release evidence"
    )
    qa_payload = stable_read_json(qa_path, label="QA report")
    release_evidence_validation = validate_release_evidence(
        evidence_payload,
        authoritative_sha256=source_hashes_before[full],
        corpus_manifest_sha256=source_hashes_before[manifest_path],
        targeted_channel_count=(
            0
            if manifest_payload.get("authorized_collection_scope", {}).get(
                "enabled"
            )
            is True
            else int(
                (
                    manifest_payload.get("relevance_policy", {}).get(
                        "policy_counts", {}
                    )
                    or {}
                ).get("targeted_search_plus_residual_audit", -1)
            )
        ),
        authorized_scope_sha256=manifest_payload["authorized_collection_scope"][
            "source_sha256"
        ],
    )
    validate_qa_report(
        qa_payload,
        authoritative_sha256=source_hashes_before[full],
        release_evidence_path=evidence_path,
    )
    if attachment_validation.get("required"):
        qa_attachment = (
            (qa_payload.get("attachments") or {}).get("archive")
            if isinstance(qa_payload.get("attachments"), dict)
            else None
        )
        if not isinstance(qa_attachment, dict):
            raise ReleasePackageError("QA report lacks attachment archive validation")
        if (
            qa_attachment.get("terminal_coverage_complete") is not True
            or qa_attachment.get("entry_set_parity") is not True
            or qa_attachment.get("sha256") != attachment_validation.get("manifest_sha256")
        ):
            raise ReleasePackageError(
                "QA report does not bind the exact terminal attachment archive manifest"
            )

    full_validation = validate_full_database(
        full,
        manifest_sha256=source_hashes_before[manifest_path],
        manifest_payload=manifest_payload,
    )
    compact_validation = validate_compact_database(
        compact,
        authoritative_path=full,
        authoritative_sha256=source_hashes_before[full],
        authorized_scope_sha256=manifest_payload["authorized_collection_scope"][
            "source_sha256"
        ],
    )
    for index, path in enumerate(json_paths, start=1):
        report_payload = stable_read_json(path, label=f"Research JSON #{index}")
        validate_research_json(
            report_payload,
            database_sha256=source_hashes_before[full],
            label=f"Research JSON #{index}",
        )
    for path in markdown_paths:
        validate_research_markdown(path, database_sha256=source_hashes_before[full])
    validate_llm_guide(
        guide,
        full_sha256=source_hashes_before[full],
        compact_sha256=source_hashes_before[compact],
    )

    destinations: list[tuple[str, Path, str]] = [
        ("authoritative_cardinal_database", full, "databases/authoritative_cardinal.sqlite"),
        ("compact_llm_database", compact, "databases/compact_llm.sqlite"),
        ("corpus_coverage_manifest", manifest_path, "manifests/corpus_coverage_manifest.json"),
        ("post_final_release_evidence", evidence_path, "qa/post_final_release_evidence.json"),
        ("independent_qa_report", qa_path, "qa/independent_qa_report.json"),
        ("llm_handoff_guide", guide, "guidance/LLM_HANDOFF_GUIDE.md"),
        *attachment_destinations,
    ]
    report_names: set[str] = set()
    for role, paths in (("research_markdown", markdown_paths), ("research_json", json_paths)):
        for path in sorted(paths, key=lambda value: (value.name.casefold(), str(value).casefold())):
            folded = path.name.casefold()
            if folded in report_names:
                raise ReleasePackageError(f"Research report filename collision: {path.name}")
            report_names.add(folded)
            destinations.append((role, path, f"research/{path.name}"))
    destinations.sort(key=lambda row: row[2].casefold())

    artifact_entries = [
        {
            "path": relative,
            "role": role,
            "sha256": source_hashes_before[source],
            "size_bytes": source.stat().st_size,
            "media_type": media_type(source),
            "source_filename": source.name,
        }
        for role, source, relative in destinations
    ]
    package_id = "sha256:" + hashlib.sha256(canonical_json_bytes(artifact_entries)).hexdigest()
    index_text = build_index_readme(package_id, artifact_entries)

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=str(output.parent))
    ).resolve()
    published = False
    try:
        for role, source, relative in destinations:
            del role
            copy_verified(source, staging / relative, source_hashes_before[source])
        readme_path = staging / "README.md"
        readme_path.write_text(index_text, encoding="utf-8", newline="\n")
        readme_entry = {
            "path": "README.md",
            "role": "package_index",
            "sha256": sha256_file(readme_path),
            "size_bytes": readme_path.stat().st_size,
            "media_type": "text/markdown; charset=utf-8",
            "source_filename": None,
        }
        all_entries = sorted([*artifact_entries, readme_entry], key=lambda row: row["path"].casefold())
        release_manifest = {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": ARTIFACT_TYPE,
            "release_status": "complete",
            "package_id": package_id,
            "manifest_self_hash_excluded": True,
            "source_scope": "discord_only",
            "outside_sources_used": 0,
            "scope": {
                "guild_id": EXPECTED_GUILD_ID,
                "timezone": EXPECTED_TIMEZONE,
                "start_date_inclusive": EXPECTED_START_DATE,
                "end_date_inclusive": EXPECTED_END_DATE,
                "utc_start_inclusive": EXPECTED_START_UTC,
                "utc_end_exclusive": EXPECTED_END_UTC,
                "local_calendar_days": EXPECTED_LOCAL_DAYS,
                "authorized_parent_container_ids": sorted(
                    AUTHORIZED_PARENT_IDS
                ),
                "authorized_collection_scope_sha256": manifest_payload[
                    "authorized_collection_scope"
                ]["source_sha256"],
                "premium_authoritative_directory": PREMIUM_AUTHORITATIVE_DIRECTORY,
                "premium_collector_version_required": PREMIUM_COLLECTOR_VERSION,
                "premium_daily_segment_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
                "premium_inventory_census_complete": False,
            },
            "validation": {
                "corpus_release_gates": "passed",
                "post_final_release_evidence": release_evidence_validation,
                "independent_qa": "passed",
                "authoritative_sqlite": full_validation,
                "compact_sqlite": compact_validation,
                "discord_attachment_archive": attachment_validation,
                "source_files_unchanged": True,
                "atomic_publish": True,
            },
            "files": all_entries,
        }
        release_manifest_path = staging / "RELEASE_MANIFEST.sha256.json"
        release_manifest_path.write_text(
            pretty_json_text(release_manifest), encoding="utf-8", newline="\n"
        )

        for row in all_entries:
            packaged = staging / row["path"]
            if packaged.stat().st_size != row["size_bytes"] or sha256_file(packaged) != row["sha256"]:
                raise ReleasePackageError(f"Staged package verification failed: {row['path']}")

        source_hashes_after = {path: sha256_file(path) for path in all_sources}
        changed = [str(path) for path in all_sources if source_hashes_after[path] != source_hashes_before[path]]
        if changed:
            raise ReleasePackageError("Source artifact changed during packaging: " + ", ".join(changed))

        if existing_empty:
            if not output.is_dir() or output.is_symlink() or any(output.iterdir()):
                raise ReleasePackageError("Existing empty output target changed before atomic publish")
            output.rmdir()
        elif output.exists():
            raise ReleasePackageError("Output target appeared before atomic publish")
        os.replace(staging, output)
        published = True
        return {
            "status": "passed",
            "release_status": "complete",
            "output_directory": str(output),
            "package_id": package_id,
            "artifact_file_count": len(artifact_entries),
            "manifested_file_count": len(all_entries),
            "release_manifest_sha256": sha256_file(output / "RELEASE_MANIFEST.sha256.json"),
            "source_files_unchanged": True,
            "source_scope": "discord_only",
            "outside_sources_used": 0,
        }
    finally:
        if not published and staging.exists():
            shutil.rmtree(staging)


def parser() -> argparse.ArgumentParser:
    output = argparse.ArgumentParser(description=__doc__)
    output.add_argument("--authoritative-db", required=True, type=Path)
    output.add_argument("--compact-db", required=True, type=Path)
    output.add_argument("--corpus-manifest", required=True, type=Path)
    output.add_argument("--release-evidence", required=True, type=Path)
    output.add_argument("--qa-report", required=True, type=Path)
    output.add_argument("--research-markdown", required=True, action="append", type=Path)
    output.add_argument("--research-json", required=True, action="append", type=Path)
    output.add_argument("--llm-handoff-guide", required=True, type=Path)
    output.add_argument(
        "--attachment-manifest",
        type=Path,
        help="Terminal Discord attachment archive manifest when the corpus contains attachments.",
    )
    output.add_argument(
        "--attachment-archive-root",
        type=Path,
        help="Root containing the attachment manifest's package-relative local files.",
    )
    output.add_argument("--output-dir", required=True, type=Path)
    output.add_argument(
        "--allow-existing-empty-target",
        action="store_true",
        help="Allow only a real, verified-empty output directory; files are never overwritten.",
    )
    return output


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = package_release(
            authoritative_db=args.authoritative_db,
            compact_db=args.compact_db,
            corpus_manifest=args.corpus_manifest,
            release_evidence=args.release_evidence,
            qa_report=args.qa_report,
            research_markdown=args.research_markdown,
            research_json=args.research_json,
            llm_handoff_guide=args.llm_handoff_guide,
            output_dir=args.output_dir,
            attachment_manifest=args.attachment_manifest,
            attachment_archive_root=args.attachment_archive_root,
            allow_existing_empty_target=args.allow_existing_empty_target,
        )
    except (OSError, sqlite3.DatabaseError, ReleasePackageError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(pretty_json_text(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
