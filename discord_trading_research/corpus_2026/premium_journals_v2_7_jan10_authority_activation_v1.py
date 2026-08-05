"""External-marker Jan 10 Premium Journals v2.7 collection activation.

This transaction enables only live collection routing.  It never mutates the
schedule, submits a Discord query, invokes the collector, creates a collection
stage/checkpoint/canonical, or permits promotion.  The independently audited
review package is an immutable precondition and the external commit marker is
the only authority edge.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import premium_journals_v2_7_jan10_activation_review_v1 as review


SCHEMA_VERSION = "1.0.0"
ACTIVATION_ID = "premium-journals-v2-7-jan10-collection-authority-v1"
ACTIVATED_AT_UTC = "2026-07-22T11:01:44.8454391Z"
DAY = review.DAY
QUERY = review.QUERY
TIMEZONE = review.TIMEZONE

INDEPENDENT_AUDIT_SHA256 = "ebb04c1236201dea1a6b92ec1341c087430c64d2485611f4f0044cd83b11e4b2"
INDEPENDENT_AUDIT_BYTES = 19397
INDEPENDENT_AUDIT_FINGERPRINT = "afc3eb841cffff2e18a921980f7e6d6f87f40a7be4cf8060ece27d82e394a7b7"

PREIMAGE_PATH = "working/premium_journals_v2_7_jan10_authority_activation_pre_schedule.json"
PLAN_PATH = "working/premium_journals_v2_7_jan10_authority_activation_plan.json"
RECEIPT_PATH = "working/premium_journals_v2_7_jan10_authority_activation_receipt.json"
BUNDLE_PATH = "working/premium_journals_v2_7_jan10_authority_activation_projection_bundle.json"
MARKER_PATH = "working/premium_journals_v2_7_jan10_authority_activation_commit_marker.json"
TERMINAL_AUDIT_PATH = "working/premium_journals_v2_7_jan10_authority_activation_terminal_audit.json"
ROLLBACK_RECEIPT_PATH = "working/premium_journals_v2_7_jan10_authority_activation_rollback_receipt.json"
LOCK_PATH = "working/.premium_journals_v2_7_jan10_external_authority_transaction.lock"

ACTIVATION_ARTIFACT_PATHS = (
    PREIMAGE_PATH,
    PLAN_PATH,
    RECEIPT_PATH,
    BUNDLE_PATH,
    MARKER_PATH,
    TERMINAL_AUDIT_PATH,
)
ACTIVATION_PUBLICATION_ORDER = ACTIVATION_ARTIFACT_PATHS
AUTHORITY_PREFIX = "working/premium_journals_v2_7_jan10_authority_activation"

ACTIVATION_SOURCE_PATHS = (
    "../discord_browser_collector_v2_7.mjs",
    "../test_discord_browser_collector_v2_7.mjs",
    "docs/premium_journals_v2_7_jan10_activation_review_v1.md",
    "docs/premium_journals_v2_7_jan10_authority_activation_v1.md",
    "premium_journals_provenance_contract_v2_7.py",
    "premium_journals_v2_7_authority_activation_v1.py",
    "premium_journals_v2_7_jan10_activation_review_v1.py",
    "premium_journals_v2_7_jan10_authority_activation_v1.py",
    "qa/validate_premium_journals_v2_7_jan10_activation_review_v1.py",
    "qa/validate_premium_journals_v2_7_jan10_authority_activation_v1.py",
    "test_premium_journals_provenance_contract_v2_7.py",
    "test_premium_journals_v2_7_authority_activation_v1.py",
    "test_premium_journals_v2_7_jan10_activation_review_v1.py",
    "test_premium_journals_v2_7_jan10_authority_activation_v1.py",
    "test_validate_scoped_three_parent_schedule.py",
    "validate_scoped_three_parent_schedule.py",
)

ACTIVE_ROUTE = {
    "route_id": "premium_journals_v2_7_2026-01-10_2026-01-10",
    "source_schedule_route_id": review.EXPECTED_JAN10_SCHEDULE_ROUTE["route_id"],
    "source_schedule_route_sha256": review.sha256_json(review.EXPECTED_JAN10_SCHEDULE_ROUTE),
    "guild_id": review.v26.GUILD_ID,
    "parent_forum_channel_id": review.v26.PREMIUM_ID,
    "parent_forum_channel_name": review.v26.PREMIUM_NAME,
    "start": DAY,
    "end": DAY,
    "timezone": TIMEZONE,
    "query": QUERY,
    "collector_version": "2.7",
    "provenance_version": "2.7",
    "v2_7_explicit_opt_in": True,
    "collection_authority": "v2.7",
    "canonical_authority": "none_pending_separate_promotion",
    "expected_canonical_path": review.JAN10_V27_CANONICAL,
    "expected_checkpoint_directory": review.v27.expected_checkpoint_relative_directory(DAY),
    "status": "active_collection_authority_pending_capture",
    "live_collection_enabled": True,
    "query_submission_authorized": True,
    "minimum_query_spacing_seconds": 60,
    "canonical_authority_enabled": False,
    "canonical_write_enabled": False,
    "canonical_present": False,
    "promotion_allowed": False,
    "schedule_write_enabled": False,
}


class ActivationError(RuntimeError):
    pass


def _finalize(record: dict[str, Any]) -> dict[str, Any]:
    record = dict(record)
    record["record_fingerprint_sha256"] = review.sha256_json(record)
    return record


def _fingerprint_valid(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    stripped = dict(record)
    observed = stripped.pop("record_fingerprint_sha256", None)
    return isinstance(observed, str) and observed == review.sha256_json(stripped)


def _simple_binding(relative: str, raw: bytes) -> dict[str, Any]:
    return {"path": relative, "sha256": review.sha256_bytes(raw), "bytes": len(raw)}


def _binding(relative: str, role: str, raw: bytes) -> dict[str, Any]:
    return {"role": role, **_simple_binding(relative, raw)}


def _load_object(raw: bytes, label: str) -> dict[str, Any]:
    return review._load_object(raw, label)


def _activation_source_path(root: Path, relative: str) -> Path:
    if relative not in ACTIVATION_SOURCE_PATHS:
        raise ActivationError(f"unapproved activation source: {relative}")
    root = root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root.parent)
    except ValueError as exc:
        raise ActivationError(f"activation source outside project: {relative}") from exc
    return path


def _read_optional(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _authority_inventory(root: Path) -> list[str]:
    paths = set(ACTIVATION_ARTIFACT_PATHS) | {ROLLBACK_RECEIPT_PATH, LOCK_PATH}
    for path in root.glob(AUTHORITY_PREFIX + "*"):
        if path.is_file():
            paths.add(path.resolve().relative_to(root.resolve()).as_posix())
    return sorted(relative for relative in paths if review.resolve_corpus_path(root, relative).is_file())


def capture_snapshot(root: Path) -> dict[str, Any]:
    root = root.resolve()
    base = review.capture_snapshot(root)
    sources = {
        relative: _read_optional(_activation_source_path(root, relative))
        for relative in ACTIVATION_SOURCE_PATHS
    }
    artifacts: dict[str, bytes | None] = {}
    for relative in (*ACTIVATION_ARTIFACT_PATHS, ROLLBACK_RECEIPT_PATH, LOCK_PATH):
        path = review.resolve_corpus_path(root, relative)
        try:
            artifacts[relative] = _read_optional(path)
        except PermissionError:
            if relative != LOCK_PATH:
                raise
            # Windows denies a second handle read while this process holds the
            # one-byte exclusive region.  The byte was validated before lock
            # acquisition and is excluded from the protected transaction
            # signature while held.
            artifacts[relative] = b"\0"
    return {
        "base": base,
        "activation_sources": sources,
        "activation_artifacts": artifacts,
        "authority_inventory": _authority_inventory(root),
    }


def _raw_signature(raw: bytes | None) -> Any:
    return None if raw is None else {"sha256": review.sha256_bytes(raw), "bytes": len(raw)}


def snapshot_signature(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "base": review.snapshot_signature(snapshot["base"]),
        "activation_sources": {
            key: _raw_signature(value) for key, value in snapshot["activation_sources"].items()
        },
        "activation_artifacts": {
            key: _raw_signature(value) for key, value in snapshot["activation_artifacts"].items()
        },
        "authority_inventory": snapshot["authority_inventory"],
    }


def _external_audit_errors(base: dict[str, Any]) -> list[str]:
    raw = base["package"].get(review.INDEPENDENT_AUDIT_PATH)
    if raw is None or (review.sha256_bytes(raw), len(raw)) != (
        INDEPENDENT_AUDIT_SHA256, INDEPENDENT_AUDIT_BYTES
    ):
        return ["independent_audit_binding_invalid"]
    try:
        audit = _load_object(raw, review.INDEPENDENT_AUDIT_PATH)
    except Exception as exc:
        return [f"independent_audit_json_invalid:{exc}"]
    errors: list[str] = []
    if audit.get("status") != "PASS" or audit.get("blockers") != []:
        errors.append("independent_audit_not_pass")
    if audit.get("record_fingerprint_sha256") != INDEPENDENT_AUDIT_FINGERPRINT or not _fingerprint_valid(audit):
        errors.append("independent_audit_fingerprint_invalid")
    if audit.get("activation_authorized") is not False or audit.get("authority_effect") != "none_audit_does_not_activate":
        errors.append("independent_audit_scope_invalid")
    expected_package = []
    for relative in review.PACKAGE_ARTIFACT_PATHS:
        package_raw = base["package"].get(relative)
        if package_raw is None:
            errors.append(f"review_package_artifact_missing:{relative}")
            continue
        expected_package.append(_simple_binding(relative, package_raw))
    if audit.get("bound_package_artifacts") != expected_package:
        errors.append("independent_audit_package_bindings_invalid")
    expected_sources = review._source_bindings(base)
    if audit.get("rederived_protected_source_bindings") != expected_sources:
        errors.append("independent_audit_protected_sources_changed")
    if audit.get("protected_source_set_sha256") != review.sha256_json(expected_sources):
        errors.append("independent_audit_source_set_changed")
    if audit.get("rederived_frozen_inputs") != review._independent_audit_expected_inputs(base):
        errors.append("independent_audit_frozen_inputs_changed")
    if audit.get("schedule_validation") != {
        "status": "PASS", "errors": [],
        "schedule": _simple_binding(review.SCHEDULE_PATH, base["inputs"]["schedule"]),
    }:
        errors.append("independent_audit_schedule_binding_changed")
    return sorted(set(errors))


def _filtered_absence_errors(snapshot: dict[str, Any]) -> list[str]:
    base = snapshot["base"]
    allowed_exact = set(ACTIVATION_ARTIFACT_PATHS) | {LOCK_PATH}
    errors: list[str] = []
    for relative, description in base["absence"]["exact"].items():
        if relative not in allowed_exact and description is not None:
            errors.append(f"forbidden_target_or_authority_artifact_present:{relative}")
    for pattern, matches in base["absence"]["patterns"].items():
        unexpected = [item["path"] for item in matches if item.get("path") not in allowed_exact]
        errors.extend(f"forbidden_pattern_match:{pattern}:{item}" for item in unexpected)
    return errors


def validate_preconditions(snapshot: dict[str, Any], *, allow_activation_artifacts: bool) -> list[str]:
    base = snapshot["base"]
    errors: list[str] = []
    schedule_raw = base["inputs"].get("schedule")
    if schedule_raw is None or (review.sha256_bytes(schedule_raw), len(schedule_raw)) != (
        review.SCHEDULE_SHA256, review.SCHEDULE_BYTES
    ):
        errors.append("live_schedule_binding_invalid")
    else:
        schedule = _load_object(schedule_raw, review.SCHEDULE_PATH)
        try:
            if review._route(schedule, DAY) != review.EXPECTED_JAN10_SCHEDULE_ROUTE:
                errors.append("jan10_source_schedule_route_invalid")
            jan9 = review._route(schedule, "2026-01-09")
            if jan9.get("status") != "complete_accepted_v2_6" or jan9.get("accepted_artifact", {}).get("path") != review.JAN9_CANONICAL_PATH:
                errors.append("jan9_not_terminal_v2_6")
        except Exception as exc:
            errors.append(f"schedule_route_invalid:{exc}")
    errors.extend(_external_audit_errors(base))
    errors.extend(_filtered_absence_errors(snapshot))
    missing_sources = [relative for relative, raw in snapshot["activation_sources"].items() if raw is None]
    errors.extend(f"activation_source_missing:{relative}" for relative in missing_sources)
    if snapshot["activation_artifacts"].get(ROLLBACK_RECEIPT_PATH) is not None:
        errors.append("rollback_receipt_preexists")
    lock_raw = snapshot["activation_artifacts"].get(LOCK_PATH)
    if lock_raw is not None and lock_raw != b"\0":
        errors.append("activation_lock_bytes_invalid")
    allowed_inventory = set(ACTIVATION_ARTIFACT_PATHS) | {LOCK_PATH}
    unexpected_inventory = sorted(set(snapshot["authority_inventory"]) - allowed_inventory)
    errors.extend(f"unexpected_activation_artifact:{item}" for item in unexpected_inventory)
    if not allow_activation_artifacts:
        for relative in (*ACTIVATION_ARTIFACT_PATHS, LOCK_PATH):
            if snapshot["activation_artifacts"].get(relative) is not None:
                errors.append(f"activation_artifact_preexists:{relative}")
    return sorted(set(errors))


def _source_bindings(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _binding(relative, "activation_code_test_or_validator", snapshot["activation_sources"][relative])
        for relative in sorted(ACTIVATION_SOURCE_PATHS)
    ]


def build_plan(snapshot: dict[str, Any]) -> dict[str, Any]:
    base = snapshot["base"]
    sources = _source_bindings(snapshot)
    return _finalize({
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_jan10_collection_authority_activation_plan",
        "activation_id": ACTIVATION_ID,
        "status": "approved_for_external_marker_transaction",
        "activated_at_utc": ACTIVATED_AT_UTC,
        "immutable": True,
        "append_only": True,
        "target_day": DAY,
        "active_collection_route": ACTIVE_ROUTE,
        "active_collection_route_sha256": review.sha256_json(ACTIVE_ROUTE),
        "bound_review_package": [
            _simple_binding(relative, base["package"][relative])
            for relative in (*review.PACKAGE_ARTIFACT_PATHS, review.INDEPENDENT_AUDIT_PATH)
        ],
        "bound_independent_audit_fingerprint_sha256": INDEPENDENT_AUDIT_FINGERPRINT,
        "bound_schedule": _binding(
            review.SCHEDULE_PATH, "unchanged_external_authority_schedule", base["inputs"]["schedule"]
        ),
        "activation_source_bindings": sources,
        "activation_source_set_sha256": review.sha256_json(sources),
        "authority_controls": {
            "collection_authority_enable_on_exact_marker": True,
            "query_submission_authorized_after_exact_marker": True,
            "canonical_authority_enabled": False,
            "canonical_write_enabled": False,
            "promotion_allowed": False,
            "schedule_write_enabled": False,
            "collector_invoked_by_activation": False,
            "query_submitted_by_activation": False,
        },
        "transaction_contract": {
            "exclusive_os_lock": LOCK_PATH,
            "immutable_no_clobber": True,
            "preimage_written_first": PREIMAGE_PATH,
            "receipt_path": RECEIPT_PATH,
            "bundle_path": BUNDLE_PATH,
            "external_commit_marker_written_after_receipt_and_bundle": MARKER_PATH,
            "publisher_terminal_audit_written_after_marker": TERMINAL_AUDIT_PATH,
            "schedule_replacement": False,
            "canonical_or_stage_write": False,
        },
        "jan9_authority": "v2.6_schedule_only",
        "jan9_v2_7_authority": False,
        "jan9_authority_inherited": False,
    })


def build_receipt(snapshot: dict[str, Any], plan_raw: bytes) -> dict[str, Any]:
    base = snapshot["base"]
    plan = _load_object(plan_raw, PLAN_PATH)
    return _finalize({
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_jan10_collection_authority_activation_receipt",
        "activation_id": ACTIVATION_ID,
        "status": "APPROVED_COLLECTION_ONLY_PENDING_EXTERNAL_MARKER",
        "activated_at_utc": ACTIVATED_AT_UTC,
        "immutable": True,
        "append_only": True,
        "decision": "enable_v2_7_live_collection_only_after_exact_external_marker",
        "plan": _binding(PLAN_PATH, "activation_plan", plan_raw),
        "pre_activation_schedule": _binding(
            PREIMAGE_PATH, "exact_raw_schedule_preimage", base["inputs"]["schedule"]
        ),
        "independent_prepublication_audit": _binding(
            review.INDEPENDENT_AUDIT_PATH, "independent_prepublication_audit",
            base["package"][review.INDEPENDENT_AUDIT_PATH],
        ),
        "active_collection_route": ACTIVE_ROUTE,
        "active_collection_route_sha256": plan["active_collection_route_sha256"],
        "authority_effect_before_marker": "none",
        "authority_effect_after_exact_marker": "v2.7_live_collection_only",
        "schedule_mutated": False,
        "canonical_or_stage_written": False,
        "query_submitted": False,
        "collector_invoked": False,
        "promotion_allowed": False,
    })


def build_bundle(snapshot: dict[str, Any], plan_raw: bytes, receipt_raw: bytes) -> dict[str, Any]:
    base = snapshot["base"]
    return _finalize({
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_jan10_external_authority_projection_bundle",
        "activation_id": ACTIVATION_ID,
        "status": "EXACT_EXTERNAL_MARKER_PROJECTION_NOT_SCHEDULE_MUTATION",
        "activated_at_utc": ACTIVATED_AT_UTC,
        "immutable": True,
        "append_only": True,
        "plan": _binding(PLAN_PATH, "activation_plan", plan_raw),
        "receipt": _binding(RECEIPT_PATH, "activation_receipt", receipt_raw),
        "pre_activation_schedule": _binding(
            PREIMAGE_PATH, "exact_raw_schedule_preimage", base["inputs"]["schedule"]
        ),
        "live_schedule": _binding(
            review.SCHEDULE_PATH, "unchanged_live_schedule", base["inputs"]["schedule"]
        ),
        "independent_prepublication_audit": _binding(
            review.INDEPENDENT_AUDIT_PATH, "independent_prepublication_audit",
            base["package"][review.INDEPENDENT_AUDIT_PATH],
        ),
        "external_authority_projection": ACTIVE_ROUTE,
        "external_authority_projection_sha256": review.sha256_json(ACTIVE_ROUTE),
        "schedule_projection": None,
        "schedule_write_required": False,
        "canonical_or_stage_write_required": False,
        "marker_path": MARKER_PATH,
    })


def build_marker(
    snapshot: dict[str, Any], plan_raw: bytes, receipt_raw: bytes, bundle_raw: bytes
) -> dict[str, Any]:
    base = snapshot["base"]
    return _finalize({
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_jan10_external_collection_authority_commit_marker",
        "activation_id": ACTIVATION_ID,
        "status": "ACTIVE_COLLECTION_AUTHORITY",
        "activated_at_utc": ACTIVATED_AT_UTC,
        "immutable": True,
        "append_only": True,
        "marker_is_external_to_schedule": True,
        "plan": _binding(PLAN_PATH, "activation_plan", plan_raw),
        "receipt": _binding(RECEIPT_PATH, "activation_receipt", receipt_raw),
        "projection_bundle": _binding(BUNDLE_PATH, "external_authority_projection_bundle", bundle_raw),
        "pre_activation_schedule": _binding(
            PREIMAGE_PATH, "exact_raw_schedule_preimage", base["inputs"]["schedule"]
        ),
        "live_schedule": _binding(
            review.SCHEDULE_PATH, "unchanged_live_schedule", base["inputs"]["schedule"]
        ),
        "independent_prepublication_audit": _binding(
            review.INDEPENDENT_AUDIT_PATH, "independent_prepublication_audit",
            base["package"][review.INDEPENDENT_AUDIT_PATH],
        ),
        "active_collection_route": ACTIVE_ROUTE,
        "active_collection_route_sha256": review.sha256_json(ACTIVE_ROUTE),
        "authority_effect": "v2.7_live_collection_only",
        "query_submission_authorized": True,
        "schedule_mutated": False,
        "canonical_or_stage_written": False,
        "query_submitted": False,
        "collector_invoked": False,
        "canonical_authority_enabled": False,
        "promotion_allowed": False,
    })


def build_terminal_audit(
    snapshot: dict[str, Any], plan_raw: bytes, receipt_raw: bytes,
    bundle_raw: bytes, marker_raw: bytes,
) -> dict[str, Any]:
    base = snapshot["base"]
    return _finalize({
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_jan10_activation_publisher_terminal_audit",
        "activation_id": ACTIVATION_ID,
        "status": "PASS",
        "completed_at_utc": ACTIVATED_AT_UTC,
        "immutable": True,
        "append_only": True,
        "blockers": [],
        "bound_artifacts": [
            _simple_binding(PLAN_PATH, plan_raw),
            _simple_binding(RECEIPT_PATH, receipt_raw),
            _simple_binding(BUNDLE_PATH, bundle_raw),
            _simple_binding(MARKER_PATH, marker_raw),
            _simple_binding(PREIMAGE_PATH, base["inputs"]["schedule"]),
            _simple_binding(review.INDEPENDENT_AUDIT_PATH, base["package"][review.INDEPENDENT_AUDIT_PATH]),
        ],
        "schedule_before": _simple_binding(review.SCHEDULE_PATH, base["inputs"]["schedule"]),
        "schedule_after": _simple_binding(review.SCHEDULE_PATH, base["inputs"]["schedule"]),
        "schedule_byte_equal": True,
        "reader_state_before_terminal_audit": "LIVE_COLLECTION_AUTHORIZED_PENDING_TERMINAL_AUDIT",
        "reader_state_after_terminal_audit": "LIVE_COLLECTION_AUTHORIZED",
        "active_collection_route_sha256": review.sha256_json(ACTIVE_ROUTE),
        "activation_effect": "v2.7_live_collection_only",
        "canonical_or_stage_written": False,
        "query_submitted": False,
        "collector_invoked": False,
        "promotion_allowed": False,
        "jan9_authority_unchanged": True,
    })


def expected_artifact_bytes(snapshot: dict[str, Any]) -> dict[str, bytes]:
    plan_raw = review.json_bytes(build_plan(snapshot))
    receipt_raw = review.json_bytes(build_receipt(snapshot, plan_raw))
    bundle_raw = review.json_bytes(build_bundle(snapshot, plan_raw, receipt_raw))
    marker_raw = review.json_bytes(build_marker(snapshot, plan_raw, receipt_raw, bundle_raw))
    terminal_raw = review.json_bytes(
        build_terminal_audit(snapshot, plan_raw, receipt_raw, bundle_raw, marker_raw)
    )
    return {
        PREIMAGE_PATH: snapshot["base"]["inputs"]["schedule"],
        PLAN_PATH: plan_raw,
        RECEIPT_PATH: receipt_raw,
        BUNDLE_PATH: bundle_raw,
        MARKER_PATH: marker_raw,
        TERMINAL_AUDIT_PATH: terminal_raw,
    }


def validate_activation_snapshot(snapshot: dict[str, Any]) -> list[str]:
    errors = validate_preconditions(snapshot, allow_activation_artifacts=True)
    try:
        expected = expected_artifact_bytes(snapshot) if not errors else {}
    except Exception as exc:
        errors.append(f"activation_derivation_failed:{type(exc).__name__}:{exc}")
        expected = {}
    for relative in ACTIVATION_ARTIFACT_PATHS:
        raw = snapshot["activation_artifacts"].get(relative)
        if raw is None:
            errors.append(f"activation_artifact_missing:{relative}")
        elif relative in expected and raw != expected[relative]:
            errors.append(f"activation_artifact_tampered:{relative}")
    for relative in (PLAN_PATH, RECEIPT_PATH, BUNDLE_PATH, MARKER_PATH, TERMINAL_AUDIT_PATH):
        raw = snapshot["activation_artifacts"].get(relative)
        if raw is None:
            continue
        try:
            record = _load_object(raw, relative)
            if not _fingerprint_valid(record):
                errors.append(f"activation_record_fingerprint_invalid:{relative}")
        except Exception as exc:
            errors.append(f"activation_record_json_invalid:{relative}:{exc}")
    return sorted(set(errors))


def classify_authority(root: Path | None = None) -> dict[str, Any]:
    root = (root or Path(__file__).resolve().parent).resolve()
    try:
        before = capture_snapshot(root)
        precondition_errors = validate_preconditions(before, allow_activation_artifacts=True)
        present = [
            relative for relative in ACTIVATION_ARTIFACT_PATHS
            if before["activation_artifacts"].get(relative) is not None
        ]
        expected = expected_artifact_bytes(before) if not precondition_errors else {}
        prefix = list(ACTIVATION_ARTIFACT_PATHS[: len(present)])
        if precondition_errors:
            status = "FAIL_CLOSED"
            errors = precondition_errors
        elif present != prefix:
            status = "FAIL_CLOSED"
            errors = ["activation_artifact_set_is_not_publication_prefix"]
        else:
            mismatches = [relative for relative in present if before["activation_artifacts"][relative] != expected[relative]]
            if mismatches:
                status = "FAIL_CLOSED"
                errors = [f"activation_artifact_tampered:{relative}" for relative in mismatches]
            elif not present:
                status = "READY_FOR_ACTIVATION"
                errors = []
            elif MARKER_PATH not in present:
                status = "FAIL_CLOSED_RECOVERY_REQUIRED"
                errors = []
            elif TERMINAL_AUDIT_PATH not in present:
                status = "LIVE_COLLECTION_AUTHORIZED_PENDING_TERMINAL_AUDIT"
                errors = []
            else:
                full_errors = validate_activation_snapshot(before)
                status = "LIVE_COLLECTION_AUTHORIZED" if not full_errors else "FAIL_CLOSED"
                errors = full_errors
        after = capture_snapshot(root)
        if snapshot_signature(before) != snapshot_signature(after):
            status = "FAIL_CLOSED_SNAPSHOT_CHANGED"
            errors = [*errors, "protected_snapshot_changed_during_read"]
    except Exception as exc:
        status = "FAIL_CLOSED"
        errors = [f"reader_exception:{type(exc).__name__}:{exc}"]
    live = status in {
        "LIVE_COLLECTION_AUTHORIZED_PENDING_TERMINAL_AUDIT",
        "LIVE_COLLECTION_AUTHORIZED",
    }
    return {
        "activation_id": ACTIVATION_ID,
        "target_day": DAY,
        "status": status,
        "errors": sorted(set(errors)),
        "live_collection_enabled": live,
        "query_submission_authorized": live,
        "canonical_authority_enabled": False,
        "canonical_write_enabled": False,
        "promotion_allowed": False,
        "schedule_write_enabled": False,
        "route": ACTIVE_ROUTE if live else None,
        "route_sha256": review.sha256_json(ACTIVE_ROUTE) if live else None,
        "schedule_sha256": review.SCHEDULE_SHA256,
        "schedule_bytes": review.SCHEDULE_BYTES,
        "authority_effect": "v2.7_live_collection_only" if live else "none",
    }


def resolve_live_collection_route(root: Path | None = None) -> dict[str, Any]:
    state = classify_authority(root)
    if state["status"] != "LIVE_COLLECTION_AUTHORIZED":
        raise ActivationError("exact terminal activation chain required before route resolution")
    return {
        "route": state["route"],
        "route_sha256": state["route_sha256"],
        "schedule_sha256": state["schedule_sha256"],
        "schedule_bytes": state["schedule_bytes"],
    }


def _protected_signature(snapshot: dict[str, Any]) -> dict[str, Any]:
    signature = snapshot_signature(snapshot)
    allowed = set(ACTIVATION_ARTIFACT_PATHS) | {LOCK_PATH}
    for relative in (*ACTIVATION_ARTIFACT_PATHS, LOCK_PATH):
        signature["activation_artifacts"][relative] = None
    signature["authority_inventory"] = [
        item for item in signature["authority_inventory"] if item not in allowed
    ]
    base = signature["base"]
    for relative in (*ACTIVATION_ARTIFACT_PATHS, LOCK_PATH):
        if relative in base["absence"]["exact"]:
            base["absence"]["exact"][relative] = None
    for pattern, matches in base["absence"]["patterns"].items():
        base["absence"]["patterns"][pattern] = [
            item for item in matches if item.get("path") not in allowed
        ]
    return signature


@contextmanager
def activation_lock(root: Path, *, timeout_seconds: float = 30.0) -> Iterable[None]:
    path = review.resolve_corpus_path(root, LOCK_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    acquired = False
    try:
        if path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + timeout_seconds
        while not acquired:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise ActivationError("activation lock timed out") from exc
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def execute_activation(
    root: Path | None = None,
    *,
    _before_artifact: Callable[[str], None] | None = None,
) -> Path:
    root = (root or Path(__file__).resolve().parent).resolve()
    initial_review = review.classify_review_state(root)
    if initial_review["status"] not in {
        "INDEPENDENT_AUDIT_PASSED_NO_AUTHORITY",
        "FAIL_CLOSED",
    }:
        raise ActivationError("frozen independent review PASS required")
    pre_lock = capture_snapshot(root)
    pre_lock_errors = validate_preconditions(pre_lock, allow_activation_artifacts=True)
    if pre_lock_errors:
        raise ActivationError("pre-lock activation preflight failed: " + "; ".join(pre_lock_errors))
    # A rerun after marker makes the historical review reader fail closed on
    # intentional active artifacts; the activation reader below must validate
    # the exact already-published chain instead.
    with activation_lock(root):
        before = capture_snapshot(root)
        errors = validate_preconditions(before, allow_activation_artifacts=True)
        if errors:
            raise ActivationError("activation preflight failed: " + "; ".join(errors))
        expected = expected_artifact_bytes(before)
        present = [
            relative for relative in ACTIVATION_ARTIFACT_PATHS
            if before["activation_artifacts"].get(relative) is not None
        ]
        if present != list(ACTIVATION_ARTIFACT_PATHS[: len(present)]):
            raise ActivationError("existing activation artifacts are not an exact publication prefix")
        for relative in present:
            if before["activation_artifacts"][relative] != expected[relative]:
                raise ActivationError(f"immutable activation artifact collision: {relative}")
        protected = _protected_signature(before)
        for relative in ACTIVATION_PUBLICATION_ORDER:
            if _before_artifact is not None:
                _before_artifact(relative)
            if relative == MARKER_PATH:
                current = capture_snapshot(root)
                if _protected_signature(current) != protected:
                    raise ActivationError("protected input changed before external marker")
                for prerequisite in ACTIVATION_ARTIFACT_PATHS[:4]:
                    if current["activation_artifacts"].get(prerequisite) != expected[prerequisite]:
                        raise ActivationError("activation prefix changed before external marker")
            review._write_exclusive_or_exact(
                review.resolve_corpus_path(root, relative), expected[relative]
            )
            if relative == MARKER_PATH:
                pending = classify_authority(root)
                if pending["status"] != "LIVE_COLLECTION_AUTHORIZED_PENDING_TERMINAL_AUDIT":
                    raise ActivationError("external marker did not validate before terminal audit")
        final = classify_authority(root)
        if final["status"] != "LIVE_COLLECTION_AUTHORIZED":
            raise ActivationError("terminal activation chain did not validate: " + "; ".join(final["errors"]))
        return review.resolve_corpus_path(root, MARKER_PATH)


def write_canonical(*_args: Any, **_kwargs: Any) -> None:
    raise ActivationError("canonical writes are forbidden during collection-only activation")


def create_collection_stage(*_args: Any, **_kwargs: Any) -> None:
    raise ActivationError("collection-stage creation is outside the activation transaction")


def submit_discord_query(*_args: Any, **_kwargs: Any) -> None:
    raise ActivationError("Discord query submission is outside the activation transaction")


def mutate_schedule(*_args: Any, **_kwargs: Any) -> None:
    raise ActivationError("schedule mutation is forbidden by the external-marker transaction")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    if args.activate:
        marker = execute_activation()
        result = classify_authority()
        result["marker"] = _simple_binding(MARKER_PATH, marker.read_bytes())
    else:
        result = classify_authority()
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in {
        "READY_FOR_ACTIVATION", "FAIL_CLOSED_RECOVERY_REQUIRED",
        "LIVE_COLLECTION_AUTHORIZED_PENDING_TERMINAL_AUDIT", "LIVE_COLLECTION_AUTHORIZED",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
