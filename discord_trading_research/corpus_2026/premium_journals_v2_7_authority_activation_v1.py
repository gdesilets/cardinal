"""Superseded, non-authoritative Jan 9 v2.7 activation-harness draft.

The frozen migration candidate and its audit are inputs, never mutable code
configuration.  This layer narrows that candidate to live collection authority
with canonical authority and promotion disabled.  Public plan/commit and route
entry points are permanently blocked for every root; only isolated fixtures exercise the
immutable plan, pre-image, receipt, bundle, and external-marker machinery.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import posixpath
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import premium_journals_provenance_contract as v26
import premium_journals_provenance_contract_v2_7 as v27
import premium_journals_v2_7_authority_migration_v1 as migration


SCHEMA_VERSION = "1.0.0"
ACTIVATION_ID = "premium-journals-v2-7-collection-authority-2026-01-09-v1"
DAY = "2026-01-09"
DRAFT_STATUS = "superseded_non_authoritative_never_activate"
SCHEDULE_PATH = "working/scoped_three_parent_collection_schedule.json"
PLAN_PATH = "working/premium_journals_v2_7_authority_activation_v1_plan.json"
PLAN_AUDIT_PATH = "working/premium_journals_v2_7_authority_activation_v1_plan_independent_audit_report.json"
PREIMAGE_PATH = "working/premium_journals_v2_7_authority_activation_v1_pre_schedule.json"
RECEIPT_PATH = "working/premium_journals_v2_7_authority_activation_v1_receipt.json"
PROJECTION_BUNDLE_PATH = "working/premium_journals_v2_7_authority_activation_v1_projection_bundle.json"
COMMIT_MARKER_PATH = "working/premium_journals_v2_7_authority_activation_v1_commit_marker.json"
ROLLBACK_RECEIPT_PATH = "working/premium_journals_v2_7_authority_activation_v1_rollback_receipt.json"
LOCK_PATH = "working/.premium_journals_v2_7_authority_activation_v1.lock"
SUPERSEDED_DRAFT_DIRECTORY = "working/superseded_premium_journals_v2_7_jan9_activation_draft_v1"
SUPERSEDED_PREIMAGE_PATH = f"{SUPERSEDED_DRAFT_DIRECTORY}/pre_activation_schedule.json"
SUPERSEDED_MANIFEST_PATH = f"{SUPERSEDED_DRAFT_DIRECTORY}/supersession_manifest.json"
SUPERSEDED_ARCHIVE_LOCK_PATH = f"{SUPERSEDED_DRAFT_DIRECTORY}/.archive.lock"
SUPERSESSION_REASON = "Jan9 collection proceeds on v2.6; v2.7 first future authority target moved to Jan10"

CANDIDATE_PATH = migration.CANDIDATE_RELATIVE_PATH
READINESS_PATH = migration.MIGRATION_READINESS_RELATIVE_PATH
PRIOR_AUDIT_PATH = "working/premium_journals_v2_7_authority_migration_v1_independent_audit_report.json"
CANDIDATE_SHA256 = "0637cdc6bbf0c3a49f110061cb718399ea81aaf694f0bc8f4754cba949ddd109"
CANDIDATE_BYTES = 27986
CANDIDATE_FINGERPRINT = "e78e85929904e3ccbf495646e961422627c46d766dc291efbb4d02243e150332"
READINESS_SHA256 = "706c6781f660d6556b2475095eee9fe7f9cf1b567de4a2f379925a01ff1b48dc"
READINESS_BYTES = 2898
READINESS_FINGERPRINT = "4e1c4084a7d33b9316171079cb44cf531e332ab64b2d2504e6b4a9df643eda81"
PRIOR_AUDIT_SHA256 = "9fd5f3bfadf83ef004b29ab6efa0a47240567c2e0c5f10421e6e5dccaa9d0af0"
PRIOR_AUDIT_BYTES = 13318
PRE_SCHEDULE_SHA256 = "0a1fd787f0fbeb6cb142edd028d16daa3c0189027d2d42b82ba40bb209ca18d6"
PRE_SCHEDULE_BYTES = 930837

IMPLEMENTATION_PATHS = (
    "premium_journals_v2_7_authority_activation_v1.py",
    "premium_journals_v2_7_authority_migration_v1.py",
    "premium_journals_provenance_contract.py",
    "premium_journals_provenance_contract_v2_7.py",
    "qa/validate_premium_journals_v2_7_authority_activation_v1.py",
    "test_premium_journals_v2_7_authority_activation_v1.py",
    "docs/premium_journals_v2_7_authority_activation_v1.md",
    "docs/premium_journals_v2_7_jan9_supersession_handoff.md",
    "validate_scoped_three_parent_schedule.py",
    "test_validate_scoped_three_parent_schedule.py",
)
ACTIVATION_TOP_LEVEL_KEYS = {
    "premium_journals_v2_7_authoritative_routes",
    "premium_journals_authority_activation_receipts",
    "premium_journals_v2_7_authority_activation",
}


class ActivationError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _normalized_relative(value: str) -> bool:
    return bool(value) and not Path(value).is_absolute() and "\\" not in value and posixpath.normpath(value) == value


def resolve_path(root: Path, relative: str) -> Path:
    if not _normalized_relative(relative):
        raise ActivationError(f"non-normalized relative path: {relative}")
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ActivationError(f"path outside discord_trading_research: {relative}") from exc
    return path


def expected_authority_state() -> dict[str, Any]:
    return {
        "collection_authority": "v2.7",
        "canonical_authority": "none_pending_separate_promotion",
        "collection_authority_enabled": True,
        "canonical_authority_enabled": False,
        "live_collection_enabled": True,
        "promotion_allowed": False,
        "canonical_present": False,
        "canonical_promoted": False,
        "automatic_v2_6_revival_on_failure": False,
    }


def expected_atomic_visibility() -> dict[str, Any]:
    return {
        "preimage_written_exclusively_before_receipt": True,
        "projected_schedule_written_and_fsynced_to_same_directory_temp": True,
        "schedule_replaced_atomically": True,
        "external_commit_marker_written_last_exclusively": True,
        "reader_requires_marker_matching_receipt_and_schedule": True,
        "missing_or_invalid_marker_uses_bound_preimage_v2_6_authority": True,
    }


def expected_rollback_contract() -> dict[str, Any]:
    return {
        "preimage_path": PREIMAGE_PATH,
        "rollback_receipt_path": ROLLBACK_RECEIPT_PATH,
        "separate_independent_rollback_review_required": True,
        "v2_7_canonical_must_be_quarantined_before_rollback": True,
        "restore_exact_preimage_bytes_without_reserialization": True,
        "automatic_rollback_or_v2_6_revival": False,
    }


def expected_activation_controls() -> dict[str, Any]:
    return {
        "activation_executed": False,
        "receipt_created": False,
        "schedule_modified": False,
        "commit_marker_created": False,
        "jan9_collection_performed": False,
        "jan9_canonical_written": False,
        "jan9_promotion_performed": False,
    }


def expected_rollback_projection() -> dict[str, Any]:
    return {
        "status": "dormant_requires_separate_reviewed_rollback_receipt",
        "restore_source_path": PREIMAGE_PATH,
        "restore_sha256": PRE_SCHEDULE_SHA256,
        "restore_bytes": PRE_SCHEDULE_BYTES,
        "restore_raw_bytes_without_json_reserialization": True,
        "v2_7_canonical_quarantine_required": True,
        "rollback_receipt_path": ROLLBACK_RECEIPT_PATH,
        "automatic_rollback_forbidden": True,
    }


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ActivationError(f"unreadable JSON {path}: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise ActivationError(f"JSON object required: {path}")
    return value


def binding(root: Path, relative: str, role: str) -> dict[str, Any]:
    path = resolve_path(root, relative)
    if not path.is_file():
        raise ActivationError(f"bound file missing: {relative}")
    return {"role": role, "path": relative, "sha256": sha256_file(path), "bytes": path.stat().st_size}


def simple_binding(root: Path, relative: str) -> dict[str, Any]:
    record = binding(root, relative, "unused")
    record.pop("role")
    return record


def binding_errors(root: Path, record: Any, label: str, *, with_role: bool = True) -> list[str]:
    fields = {"role", "path", "sha256", "bytes"} if with_role else {"path", "sha256", "bytes"}
    if not isinstance(record, dict) or set(record) != fields:
        return [f"{label}_binding_schema_invalid"]
    try:
        path = resolve_path(root, str(record.get("path") or ""))
    except ActivationError:
        return [f"{label}_binding_path_invalid"]
    if not path.is_file():
        return [f"{label}_binding_missing"]
    errors: list[str] = []
    if record.get("sha256") != sha256_file(path):
        errors.append(f"{label}_binding_sha256_mismatch")
    if type(record.get("bytes")) is not int or record.get("bytes") != path.stat().st_size:
        errors.append(f"{label}_binding_bytes_mismatch")
    return errors


def _exact_known_binding(root: Path, relative: str, role: str, digest: str, size: int) -> dict[str, Any]:
    record = binding(root, relative, role)
    if (record["sha256"], record["bytes"]) != (digest, size):
        raise ActivationError(f"frozen binding changed: {relative}")
    return record


def _candidate_inputs(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    candidate = load_object(resolve_path(root, CANDIDATE_PATH))
    readiness = load_object(resolve_path(root, READINESS_PATH))
    audit = load_object(resolve_path(root, PRIOR_AUDIT_PATH))
    schedule = load_object(resolve_path(root, SCHEDULE_PATH))
    return candidate, readiness, audit, schedule


def validate_prior_audit(audit: dict[str, Any], candidate: dict[str, Any], readiness: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    fixed = {
        "schema_version": "1.0.0",
        "artifact_type": "premium_journals_v2_7_authority_migration_independent_audit_report",
        "status": "PASS",
        "immutable": True,
        "append_only": True,
    }
    if any(audit.get(key) != value for key, value in fixed.items()):
        errors.append("prior_audit_fixed_contract_invalid")
    if audit.get("blockers") != [] or audit.get("verdict", {}).get("result") != "PASS" or audit.get("verdict", {}).get("blocker_count") != 0:
        errors.append("prior_audit_not_blocker_free_pass")
    bound = audit.get("bound_artifacts", {})
    expected_candidate = {
        "path": CANDIDATE_PATH, "sha256": CANDIDATE_SHA256, "bytes": CANDIDATE_BYTES,
        "record_fingerprint_sha256": CANDIDATE_FINGERPRINT,
    }
    expected_readiness = {
        "path": READINESS_PATH, "sha256": READINESS_SHA256, "bytes": READINESS_BYTES,
        "record_fingerprint_sha256": READINESS_FINGERPRINT,
    }
    if bound.get("candidate") != expected_candidate or bound.get("readiness_report") != expected_readiness:
        errors.append("prior_audit_candidate_or_readiness_binding_invalid")
    expected_schedule = {"path": SCHEDULE_PATH, "sha256": PRE_SCHEDULE_SHA256, "bytes": PRE_SCHEDULE_BYTES}
    if bound.get("pre_activation_schedule") != expected_schedule:
        errors.append("prior_audit_schedule_binding_invalid")
    if candidate.get("record_fingerprint_sha256") != CANDIDATE_FINGERPRINT or readiness.get("record_fingerprint_sha256") != READINESS_FINGERPRINT:
        errors.append("frozen_record_fingerprint_invalid")
    if audit.get("receipt_report_binding_adversarial_checks", {}).get("activation_valid_binding_projection_passed") is not True:
        errors.append("prior_audit_receipt_adversarial_check_missing")
    return sorted(set(errors))


def preservation_manifest(schedule: dict[str, Any]) -> dict[str, Any]:
    routes = schedule.get("routes", {})
    premium = routes.get("premium_journals", []) if isinstance(routes, dict) else []
    jan9_indices = [index for index, route in enumerate(premium) if isinstance(route, dict) and route.get("start") == DAY and route.get("end") == DAY]
    if len(jan9_indices) != 1:
        raise ActivationError("exact Jan9 premium route count is not one")
    jan9_index = jan9_indices[0]
    return {
        "pre_schedule_sha256_json": sha256_json(schedule),
        "original_top_level_key_set": sorted(schedule),
        "top_level_except_routes_sha256": sha256_json({key: value for key, value in schedule.items() if key != "routes"}),
        "student_routes_sha256": sha256_json(routes.get("student_breakdowns")),
        "questions_routes_sha256": sha256_json(routes.get("questions")),
        "premium_route_count": len(premium),
        "jan9_route_index": jan9_index,
        "jan1_through_jan8_routes_sha256": sha256_json(premium[:jan9_index]),
        "jan10_through_end_routes_sha256": sha256_json(premium[jan9_index + 1 :]),
        "all_premium_routes_except_jan9_sha256": sha256_json(premium[:jan9_index] + premium[jan9_index + 1 :]),
        "pre_jan9_v2_6_route_sha256": sha256_json(premium[jan9_index]),
    }


def retired_v26_route(candidate: dict[str, Any]) -> dict[str, Any]:
    route = copy.deepcopy(candidate["current_v2_6_authority"]["route"])
    route["status"] = "retired_by_v2_7_authority_activation"
    route["authority_retirement_migration_id"] = migration.MIGRATION_ID
    route["activation_receipt_candidate_fingerprint"] = CANDIDATE_FINGERPRINT
    return route


def pending_v27_route(candidate: dict[str, Any]) -> dict[str, Any]:
    """Explicit monotonic restriction of the audited proposed route."""
    route = copy.deepcopy(candidate["proposed_v2_7_authority"]["route"])
    route.update({
        "status": "active_v2_7_collection_pending_qa",
        "live_collection_enabled": True,
        "promotion_allowed": False,
        "authority_enabled": True,
        "collection_authority_enabled": True,
        "canonical_authority_enabled": False,
        "canonical_present_at_activation": False,
        "canonical_promoted": False,
        "activation_id": ACTIVATION_ID,
        "independent_audit_id": "premium-journals-v2-7-authority-2026-01-09-v1-independent-audit",
        "scraping_owner": "GPT-5.6 Terra",
        "heavy_pagination_lane": "discord_account_heavy_lane_1",
        "forum_exact_navigation": copy.deepcopy(candidate["current_v2_6_authority"]["route"]["forum_exact_navigation"]),
    })
    return route


def route_delta(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    proposed = candidate["proposed_v2_7_authority"]["route"]
    pending = pending_v27_route(candidate)
    return [
        {"field": "status", "audited_candidate_value": proposed["status"], "activated_plan_value": pending["status"], "reason": "collection authority is live but canonical QA is pending"},
        {"field": "promotion_allowed", "audited_candidate_value": True, "activated_plan_value": False, "reason": "monotonic restriction; promotion requires a separate receipt and marker"},
        {"field": "authority_enabled", "audited_candidate_value": True, "activated_plan_value": True, "reason": "retained only as the general route-selection flag"},
        {"field": "collection_authority_enabled", "audited_candidate_value": "absent", "activated_plan_value": True, "reason": "separates collection routing from canonical trust"},
        {"field": "canonical_authority_enabled", "audited_candidate_value": "absent", "activated_plan_value": False, "reason": "no Jan9 canonical is trusted or promoted"},
        {"field": "canonical_present_at_activation", "audited_candidate_value": "absent", "activated_plan_value": False, "reason": "both versioned Jan9 canonical paths must be absent"},
        {"field": "canonical_promoted", "audited_candidate_value": "absent", "activated_plan_value": False, "reason": "promotion is out of scope"},
    ]


def pre_activation_errors(root: Path, *, require_no_package_files: bool = False) -> list[str]:
    errors: list[str] = []
    try:
        candidate, readiness, audit, schedule = _candidate_inputs(root)
    except ActivationError as exc:
        return [str(exc)]
    frozen = (
        (CANDIDATE_PATH, CANDIDATE_SHA256, CANDIDATE_BYTES),
        (READINESS_PATH, READINESS_SHA256, READINESS_BYTES),
        (PRIOR_AUDIT_PATH, PRIOR_AUDIT_SHA256, PRIOR_AUDIT_BYTES),
        (SCHEDULE_PATH, PRE_SCHEDULE_SHA256, PRE_SCHEDULE_BYTES),
    )
    for relative, digest, size in frozen:
        path = resolve_path(root, relative)
        if not path.is_file() or sha256_file(path) != digest or path.stat().st_size != size:
            errors.append(f"pre_activation_frozen_file_mismatch:{relative}")
    errors.extend(f"candidate:{item}" for item in migration.validate_candidate(candidate, root, require_activation_preconditions=True))
    errors.extend(f"readiness:{item}" for item in migration.validate_readiness_report(readiness, root))
    errors.extend(validate_prior_audit(audit, candidate, readiness))
    expected_v26 = migration._expected_v26_route()
    premium = schedule.get("routes", {}).get("premium_journals", [])
    if [route for route in premium if isinstance(route, dict) and route.get("start") == DAY and route.get("end") == DAY] != [expected_v26]:
        errors.append("pre_activation_exact_jan9_v2_6_route_invalid")
    if any(key in schedule for key in ACTIVATION_TOP_LEVEL_KEYS):
        errors.append("pre_activation_schedule_already_contains_activation_keys")
    if resolve_path(root, v26.expected_canonical_relative_path(DAY, DAY)).is_file():
        errors.append("pre_activation_jan9_v2_6_canonical_exists")
    if resolve_path(root, v27.expected_canonical_relative_path(DAY, DAY)).is_file():
        errors.append("pre_activation_jan9_v2_7_canonical_exists")
    if resolve_path(root, v27.expected_checkpoint_relative_directory(DAY)).exists():
        errors.append("pre_activation_jan9_v2_7_checkpoint_directory_exists")
    if require_no_package_files:
        for relative in (PREIMAGE_PATH, RECEIPT_PATH, PROJECTION_BUNDLE_PATH, COMMIT_MARKER_PATH, ROLLBACK_RECEIPT_PATH):
            if resolve_path(root, relative).exists():
                errors.append(f"pre_activation_package_collision:{relative}")
    return sorted(set(errors))


def build_plan(root: Path | None = None) -> dict[str, Any]:
    root = (root or Path(__file__).resolve().parent).resolve()
    errors = pre_activation_errors(root)
    if errors:
        raise ActivationError("; ".join(errors))
    candidate, readiness, audit, schedule = _candidate_inputs(root)
    sources = [
        _exact_known_binding(root, CANDIDATE_PATH, "audited_disabled_candidate", CANDIDATE_SHA256, CANDIDATE_BYTES),
        _exact_known_binding(root, READINESS_PATH, "candidate_readiness_report", READINESS_SHA256, READINESS_BYTES),
        _exact_known_binding(root, PRIOR_AUDIT_PATH, "candidate_independent_audit_report", PRIOR_AUDIT_SHA256, PRIOR_AUDIT_BYTES),
        _exact_known_binding(root, PREIMAGE_PATH, "pre_activation_schedule_snapshot", PRE_SCHEDULE_SHA256, PRE_SCHEDULE_BYTES),
        *[binding(root, path, "activation_implementation") for path in IMPLEMENTATION_PATHS],
    ]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_activation_plan",
        "activation_id": ACTIVATION_ID,
        "migration_id": migration.MIGRATION_ID,
        "status": "disabled_plan_pending_independent_review",
        "generated_at_utc": utc_now(),
        "immutable": True,
        "append_only": True,
        "source_files": sorted(sources, key=lambda item: (item["path"], item["role"])),
        "source_file_set_sha256": "",
        "candidate_record_fingerprint_sha256": CANDIDATE_FINGERPRINT,
        "readiness_record_fingerprint_sha256": READINESS_FINGERPRINT,
        "prior_audit_id": audit["audit_id"],
        "pre_activation_live_schedule": {"path": SCHEDULE_PATH, "sha256": PRE_SCHEDULE_SHA256, "bytes": PRE_SCHEDULE_BYTES},
        "pre_activation_preservation_manifest": preservation_manifest(schedule),
        "candidate_route_delta": route_delta(candidate),
        "route_transition": {
            "retired_v2_6_route": retired_v26_route(candidate),
            "retired_v2_6_route_sha256": sha256_json(retired_v26_route(candidate)),
            "pending_v2_7_route": pending_v27_route(candidate),
            "pending_v2_7_route_sha256": sha256_json(pending_v27_route(candidate)),
            "only_exact_jan9_v2_6_route_changes": True,
            "all_other_schedule_objects_preserved": True,
        },
        "authority_state_after_commit": expected_authority_state(),
        "atomic_visibility": expected_atomic_visibility(),
        "rollback_contract": expected_rollback_contract(),
        "activation_controls": expected_activation_controls(),
        "next_action": "independent_review_of_this_exact_plan_before_receipt_or_schedule_mutation",
    }
    payload["source_file_set_sha256"] = sha256_json(payload["source_files"])
    payload["projection_plan_sha256"] = sha256_json({
        "candidate_route_delta": payload["candidate_route_delta"],
        "route_transition": payload["route_transition"],
        "authority_state_after_commit": payload["authority_state_after_commit"],
        "atomic_visibility": payload["atomic_visibility"],
        "rollback_contract": payload["rollback_contract"],
        "pre_activation_preservation_manifest": payload["pre_activation_preservation_manifest"],
    })
    payload["record_fingerprint_sha256"] = sha256_json(payload)
    return payload


def validate_plan(plan: dict[str, Any], root: Path | None = None, *, require_live_prestate: bool = True) -> list[str]:
    root = (root or Path(__file__).resolve().parent).resolve()
    errors: list[str] = []
    fixed = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_activation_plan",
        "activation_id": ACTIVATION_ID,
        "migration_id": migration.MIGRATION_ID,
        "status": "disabled_plan_pending_independent_review",
        "immutable": True,
        "append_only": True,
        "candidate_record_fingerprint_sha256": CANDIDATE_FINGERPRINT,
        "readiness_record_fingerprint_sha256": READINESS_FINGERPRINT,
        "next_action": "independent_review_of_this_exact_plan_before_receipt_or_schedule_mutation",
    }
    if any(plan.get(key) != value for key, value in fixed.items()):
        errors.append("activation_plan_fixed_contract_invalid")
    expected_keys = {
        "schema_version", "artifact_type", "activation_id", "migration_id", "status",
        "generated_at_utc", "immutable", "append_only", "source_files",
        "source_file_set_sha256", "candidate_record_fingerprint_sha256",
        "readiness_record_fingerprint_sha256", "prior_audit_id",
        "pre_activation_live_schedule", "pre_activation_preservation_manifest",
        "candidate_route_delta", "route_transition", "authority_state_after_commit",
        "atomic_visibility", "rollback_contract", "activation_controls", "next_action",
        "projection_plan_sha256", "record_fingerprint_sha256",
    }
    if set(plan) != expected_keys:
        errors.append("activation_plan_key_set_invalid")
    if not v26._is_iso_timestamp(plan.get("generated_at_utc")):
        errors.append("activation_plan_timestamp_invalid")
    unsigned = dict(plan)
    unsigned.pop("record_fingerprint_sha256", None)
    if plan.get("record_fingerprint_sha256") != sha256_json(unsigned):
        errors.append("activation_plan_fingerprint_mismatch")
    source_files = plan.get("source_files")
    if not isinstance(source_files, list) or plan.get("source_file_set_sha256") != sha256_json(source_files):
        errors.append("activation_plan_source_file_set_invalid")
        source_files = []
    for index, record in enumerate(source_files):
        errors.extend(binding_errors(root, record, f"activation_plan_source_{index}"))
    expected_paths = {CANDIDATE_PATH, READINESS_PATH, PRIOR_AUDIT_PATH, PREIMAGE_PATH, *IMPLEMENTATION_PATHS}
    if {item.get("path") for item in source_files if isinstance(item, dict)} != expected_paths:
        errors.append("activation_plan_source_path_set_not_exact")
    expected_roles = {
        CANDIDATE_PATH: "audited_disabled_candidate",
        READINESS_PATH: "candidate_readiness_report",
        PRIOR_AUDIT_PATH: "candidate_independent_audit_report",
        PREIMAGE_PATH: "pre_activation_schedule_snapshot",
        **{path: "activation_implementation" for path in IMPLEMENTATION_PATHS},
    }
    if len(source_files) != len(expected_roles) or {
        (item.get("path"), item.get("role"))
        for item in source_files if isinstance(item, dict)
    } != set(expected_roles.items()):
        errors.append("activation_plan_source_role_set_not_exact")
    known_sources = {
        CANDIDATE_PATH: (CANDIDATE_SHA256, CANDIDATE_BYTES),
        READINESS_PATH: (READINESS_SHA256, READINESS_BYTES),
        PRIOR_AUDIT_PATH: (PRIOR_AUDIT_SHA256, PRIOR_AUDIT_BYTES),
        PREIMAGE_PATH: (PRE_SCHEDULE_SHA256, PRE_SCHEDULE_BYTES),
    }
    for record in source_files:
        if isinstance(record, dict) and record.get("path") in known_sources:
            digest, size = known_sources[record["path"]]
            if (record.get("sha256"), record.get("bytes")) != (digest, size):
                errors.append(f"activation_plan_frozen_source_binding_invalid:{record['path']}")
    try:
        candidate = load_object(resolve_path(root, CANDIDATE_PATH))
        prior_audit = load_object(resolve_path(root, PRIOR_AUDIT_PATH))
        pre_schedule = load_object(resolve_path(root, PREIMAGE_PATH if not require_live_prestate else SCHEDULE_PATH))
        expected_transition = {
            "retired_v2_6_route": retired_v26_route(candidate),
            "retired_v2_6_route_sha256": sha256_json(retired_v26_route(candidate)),
            "pending_v2_7_route": pending_v27_route(candidate),
            "pending_v2_7_route_sha256": sha256_json(pending_v27_route(candidate)),
            "only_exact_jan9_v2_6_route_changes": True,
            "all_other_schedule_objects_preserved": True,
        }
        if plan.get("route_transition") != expected_transition or plan.get("candidate_route_delta") != route_delta(candidate):
            errors.append("activation_plan_route_transition_invalid")
        if plan.get("pre_activation_preservation_manifest") != preservation_manifest(pre_schedule):
            errors.append("activation_plan_preservation_manifest_invalid")
        if plan.get("prior_audit_id") != prior_audit.get("audit_id"):
            errors.append("activation_plan_prior_audit_id_invalid")
    except (ActivationError, KeyError) as exc:
        errors.append(f"activation_plan_source_rederivation_failed:{type(exc).__name__}")
    expected_projection_hash = sha256_json({
        "candidate_route_delta": plan.get("candidate_route_delta"),
        "route_transition": plan.get("route_transition"),
        "authority_state_after_commit": plan.get("authority_state_after_commit"),
        "atomic_visibility": plan.get("atomic_visibility"),
        "rollback_contract": plan.get("rollback_contract"),
        "pre_activation_preservation_manifest": plan.get("pre_activation_preservation_manifest"),
    })
    if plan.get("projection_plan_sha256") != expected_projection_hash:
        errors.append("activation_plan_projection_hash_invalid")
    if plan.get("pre_activation_live_schedule") != {"path": SCHEDULE_PATH, "sha256": PRE_SCHEDULE_SHA256, "bytes": PRE_SCHEDULE_BYTES}:
        errors.append("activation_plan_pre_live_schedule_binding_invalid")
    authority = plan.get("authority_state_after_commit", {})
    if authority != expected_authority_state():
        errors.append("activation_plan_live_pending_flags_invalid")
    if plan.get("atomic_visibility") != expected_atomic_visibility():
        errors.append("activation_plan_atomic_visibility_invalid")
    if plan.get("rollback_contract") != expected_rollback_contract():
        errors.append("activation_plan_rollback_contract_invalid")
    controls = plan.get("activation_controls", {})
    if controls != expected_activation_controls():
        errors.append("activation_plan_controls_not_disabled")
    if require_live_prestate:
        errors.extend(pre_activation_errors(root))
    return sorted(set(errors))


def validate_plan_audit(plan: dict[str, Any], audit: dict[str, Any], root: Path) -> list[str]:
    errors: list[str] = []
    fixed = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_activation_plan_independent_audit_report",
        "status": "PASS",
        "immutable": True,
        "append_only": True,
    }
    if any(audit.get(key) != value for key, value in fixed.items()):
        errors.append("activation_plan_audit_fixed_contract_invalid")
    if not isinstance(audit.get("audit_id"), str) or not audit.get("audit_id"):
        errors.append("activation_plan_audit_id_invalid")
    if not v26._is_iso_timestamp(audit.get("audited_at_utc")):
        errors.append("activation_plan_audit_timestamp_invalid")
    if audit.get("blockers") != []:
        errors.append("activation_plan_audit_has_blockers")
    expected_plan = simple_binding(root, PLAN_PATH)
    expected_plan["record_fingerprint_sha256"] = plan.get("record_fingerprint_sha256")
    bound = audit.get("bound_artifacts", {})
    if not isinstance(bound, dict):
        errors.append("activation_plan_audit_bound_artifacts_invalid")
        bound = {}
    expected_bound_keys = {
        "activation_plan", "candidate", "readiness_report",
        "prior_independent_audit", "pre_activation_schedule_snapshot",
    }
    if set(bound) != expected_bound_keys:
        errors.append("activation_plan_audit_bound_artifact_set_invalid")
    if bound.get("activation_plan") != expected_plan:
        errors.append("activation_plan_audit_plan_binding_invalid")
    for field, relative, digest, size in (
        ("candidate", CANDIDATE_PATH, CANDIDATE_SHA256, CANDIDATE_BYTES),
        ("readiness_report", READINESS_PATH, READINESS_SHA256, READINESS_BYTES),
        ("prior_independent_audit", PRIOR_AUDIT_PATH, PRIOR_AUDIT_SHA256, PRIOR_AUDIT_BYTES),
        ("pre_activation_schedule_snapshot", PREIMAGE_PATH, PRE_SCHEDULE_SHA256, PRE_SCHEDULE_BYTES),
    ):
        if bound.get(field) != {"path": relative, "sha256": digest, "bytes": size}:
            errors.append(f"activation_plan_audit_{field}_binding_invalid")
    verdict = audit.get("verdict", {})
    if verdict.get("result") != "PASS" or verdict.get("blocker_count") != 0 or verdict.get("exact_plan_approved") is not True:
        errors.append("activation_plan_audit_verdict_invalid")
    if audit.get("reviewed_projection_plan_sha256") != plan.get("projection_plan_sha256"):
        errors.append("activation_plan_audit_projection_hash_mismatch")
    if audit.get("reviewed_source_file_set_sha256") != plan.get("source_file_set_sha256"):
        errors.append("activation_plan_audit_source_file_set_hash_mismatch")
    return sorted(set(errors))


def build_receipt(root: Path, plan: dict[str, Any], plan_audit: dict[str, Any], preimage_binding: dict[str, Any], *, created_at_utc: str | None = None) -> dict[str, Any]:
    candidate = load_object(resolve_path(root, CANDIDATE_PATH))
    pre_schedule = load_object(resolve_path(root, PREIMAGE_PATH))
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_activation_receipt",
        "activation_id": ACTIVATION_ID,
        "migration_id": migration.MIGRATION_ID,
        "status": "approved_for_atomic_collection_authority_activation",
        "created_at_utc": created_at_utc or utc_now(),
        "immutable": True,
        "append_only": True,
        "bindings": {
            "candidate": _exact_known_binding(root, CANDIDATE_PATH, "audited_disabled_candidate", CANDIDATE_SHA256, CANDIDATE_BYTES),
            "readiness_report": _exact_known_binding(root, READINESS_PATH, "candidate_readiness_report", READINESS_SHA256, READINESS_BYTES),
            "prior_independent_audit": _exact_known_binding(root, PRIOR_AUDIT_PATH, "candidate_independent_audit_report", PRIOR_AUDIT_SHA256, PRIOR_AUDIT_BYTES),
            "activation_plan": binding(root, PLAN_PATH, "independently_reviewed_activation_plan"),
            "activation_plan_independent_audit": binding(root, PLAN_AUDIT_PATH, "activation_plan_independent_audit_report"),
            "pre_activation_schedule_snapshot": preimage_binding,
        },
        "candidate_record_fingerprint_sha256": CANDIDATE_FINGERPRINT,
        "readiness_record_fingerprint_sha256": READINESS_FINGERPRINT,
        "activation_plan_record_fingerprint_sha256": plan["record_fingerprint_sha256"],
        "reviewed_projection_plan_sha256": plan["projection_plan_sha256"],
        "pre_activation_live_schedule": {"path": SCHEDULE_PATH, "sha256": PRE_SCHEDULE_SHA256, "bytes": PRE_SCHEDULE_BYTES},
        "pre_activation_preservation_manifest": preservation_manifest(pre_schedule),
        "planned_transition": copy.deepcopy(plan["route_transition"]),
        "authority_after_commit": copy.deepcopy(plan["authority_state_after_commit"]),
        "commit_protocol": copy.deepcopy(plan["atomic_visibility"]),
        "rollback_contract": copy.deepcopy(plan["rollback_contract"]),
        "authorization": {
            "candidate_audit_passed": True,
            "activation_plan_audit_passed": True,
            "activation_plan_audit_id": plan_audit.get("audit_id"),
            "explicit_user_activation_instruction_received": True,
            "authorized_scope": "exactly Jan9 Premium Journals collection authority; no collection or promotion",
        },
        "creation_state": {
            "schedule_commit_completed": False,
            "commit_marker_created": False,
            "jan9_collection_performed": False,
            "jan9_canonical_written": False,
            "jan9_promotion_performed": False,
        },
    }
    payload["record_fingerprint_sha256"] = sha256_json(payload)
    return payload


def validate_receipt(receipt: dict[str, Any], root: Path, plan: dict[str, Any], plan_audit: dict[str, Any], *, require_live_prestate: bool) -> list[str]:
    errors: list[str] = []
    fixed = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_activation_receipt",
        "activation_id": ACTIVATION_ID,
        "migration_id": migration.MIGRATION_ID,
        "status": "approved_for_atomic_collection_authority_activation",
        "immutable": True,
        "append_only": True,
        "candidate_record_fingerprint_sha256": CANDIDATE_FINGERPRINT,
        "readiness_record_fingerprint_sha256": READINESS_FINGERPRINT,
        "activation_plan_record_fingerprint_sha256": plan.get("record_fingerprint_sha256"),
        "reviewed_projection_plan_sha256": plan.get("projection_plan_sha256"),
    }
    if any(receipt.get(key) != value for key, value in fixed.items()):
        errors.append("activation_receipt_fixed_contract_invalid")
    expected_keys = {
        "schema_version", "artifact_type", "activation_id", "migration_id", "status",
        "created_at_utc", "immutable", "append_only", "bindings",
        "candidate_record_fingerprint_sha256", "readiness_record_fingerprint_sha256",
        "activation_plan_record_fingerprint_sha256", "reviewed_projection_plan_sha256",
        "pre_activation_live_schedule", "pre_activation_preservation_manifest",
        "planned_transition", "authority_after_commit", "commit_protocol",
        "rollback_contract", "authorization", "creation_state",
        "record_fingerprint_sha256",
    }
    if set(receipt) != expected_keys:
        errors.append("activation_receipt_key_set_invalid")
    if not v26._is_iso_timestamp(receipt.get("created_at_utc")):
        errors.append("activation_receipt_timestamp_invalid")
    unsigned = dict(receipt)
    unsigned.pop("record_fingerprint_sha256", None)
    if receipt.get("record_fingerprint_sha256") != sha256_json(unsigned):
        errors.append("activation_receipt_fingerprint_mismatch")
    bindings = receipt.get("bindings", {})
    if not isinstance(bindings, dict) or set(bindings) != {"candidate", "readiness_report", "prior_independent_audit", "activation_plan", "activation_plan_independent_audit", "pre_activation_schedule_snapshot"}:
        errors.append("activation_receipt_binding_set_invalid")
        bindings = {}
    for label, record in bindings.items():
        errors.extend(binding_errors(root, record, f"activation_receipt_{label}"))
    try:
        expected_bindings = {
            "candidate": _exact_known_binding(root, CANDIDATE_PATH, "audited_disabled_candidate", CANDIDATE_SHA256, CANDIDATE_BYTES),
            "readiness_report": _exact_known_binding(root, READINESS_PATH, "candidate_readiness_report", READINESS_SHA256, READINESS_BYTES),
            "prior_independent_audit": _exact_known_binding(root, PRIOR_AUDIT_PATH, "candidate_independent_audit_report", PRIOR_AUDIT_SHA256, PRIOR_AUDIT_BYTES),
            "activation_plan": binding(root, PLAN_PATH, "independently_reviewed_activation_plan"),
            "activation_plan_independent_audit": binding(root, PLAN_AUDIT_PATH, "activation_plan_independent_audit_report"),
            "pre_activation_schedule_snapshot": binding(root, PREIMAGE_PATH, "pre_activation_schedule_snapshot"),
        }
        if bindings != expected_bindings:
            errors.append("activation_receipt_exact_bindings_invalid")
    except ActivationError:
        errors.append("activation_receipt_expected_binding_rederivation_failed")
    expected_pre = {"path": SCHEDULE_PATH, "sha256": PRE_SCHEDULE_SHA256, "bytes": PRE_SCHEDULE_BYTES}
    if receipt.get("pre_activation_live_schedule") != expected_pre:
        errors.append("activation_receipt_pre_live_schedule_invalid")
    try:
        pre_schedule = load_object(resolve_path(root, PREIMAGE_PATH))
        if receipt.get("pre_activation_preservation_manifest") != preservation_manifest(pre_schedule):
            errors.append("activation_receipt_preservation_manifest_invalid")
    except ActivationError:
        errors.append("activation_receipt_preimage_unreadable")
    if receipt.get("planned_transition") != plan.get("route_transition") or receipt.get("authority_after_commit") != plan.get("authority_state_after_commit") or receipt.get("commit_protocol") != plan.get("atomic_visibility") or receipt.get("rollback_contract") != plan.get("rollback_contract"):
        errors.append("activation_receipt_plan_binding_invalid")
    expected_authorization = {
        "candidate_audit_passed": True,
        "activation_plan_audit_passed": True,
        "activation_plan_audit_id": plan_audit.get("audit_id"),
        "explicit_user_activation_instruction_received": True,
        "authorized_scope": "exactly Jan9 Premium Journals collection authority; no collection or promotion",
    }
    if receipt.get("authorization") != expected_authorization:
        errors.append("activation_receipt_authorization_invalid")
    expected_creation_state = {
        "schedule_commit_completed": False,
        "commit_marker_created": False,
        "jan9_collection_performed": False,
        "jan9_canonical_written": False,
        "jan9_promotion_performed": False,
    }
    if receipt.get("creation_state") != expected_creation_state:
        errors.append("activation_receipt_creation_state_invalid")
    if require_live_prestate:
        path = resolve_path(root, SCHEDULE_PATH)
        if sha256_file(path) != PRE_SCHEDULE_SHA256 or path.stat().st_size != PRE_SCHEDULE_BYTES:
            errors.append("activation_receipt_live_pre_schedule_changed")
    return sorted(set(errors))


def activation_state(receipt: dict[str, Any], receipt_binding: dict[str, Any], preimage_binding: dict[str, Any], plan_audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_collection_authority_activation_state",
        "activation_id": ACTIVATION_ID,
        "migration_id": migration.MIGRATION_ID,
        "status": "projected_pending_external_commit_marker",
        "route_day": DAY,
        "receipt": receipt_binding,
        "receipt_record_fingerprint_sha256": receipt["record_fingerprint_sha256"],
        "activation_plan_audit_id": plan_audit.get("audit_id"),
        "pre_activation_schedule_snapshot": preimage_binding,
        "commit_marker_path": COMMIT_MARKER_PATH,
        "projection_bundle_path": PROJECTION_BUNDLE_PATH,
        "rollback_receipt_path": ROLLBACK_RECEIPT_PATH,
        "collection_authority_enabled": True,
        "canonical_authority_enabled": False,
        "live_collection_enabled": True,
        "promotion_allowed": False,
        "canonical_present_at_activation": False,
        "canonical_promoted": False,
        "reader_requires_matching_external_commit_marker": True,
        "immutable": True,
    }


def project_schedule(pre_schedule: dict[str, Any], receipt: dict[str, Any], receipt_binding: dict[str, Any], preimage_binding: dict[str, Any], plan: dict[str, Any], plan_audit: dict[str, Any]) -> dict[str, Any]:
    projected = copy.deepcopy(pre_schedule)
    premium = projected["routes"]["premium_journals"]
    old = migration._expected_v26_route()
    matches = [index for index, route in enumerate(premium) if route == old]
    if len(matches) != 1:
        raise ActivationError("projection exact Jan9 v2.6 route count is not one")
    premium[matches[0]] = copy.deepcopy(plan["route_transition"]["retired_v2_6_route"])
    projected["premium_journals_v2_7_authoritative_routes"] = [copy.deepcopy(plan["route_transition"]["pending_v2_7_route"])]
    projected["premium_journals_authority_activation_receipts"] = [copy.deepcopy(receipt_binding)]
    projected["premium_journals_v2_7_authority_activation"] = activation_state(receipt, receipt_binding, preimage_binding, plan_audit)
    return projected


def validate_schedule_projection(schedule: dict[str, Any], root: Path | None = None, *, pre_schedule: dict[str, Any] | None = None, receipt: dict[str, Any] | None = None, plan: dict[str, Any] | None = None, plan_audit: dict[str, Any] | None = None) -> list[str]:
    root = (root or Path(__file__).resolve().parent).resolve()
    errors: list[str] = []
    try:
        pre_schedule = pre_schedule or load_object(resolve_path(root, PREIMAGE_PATH))
        receipt = receipt or load_object(resolve_path(root, RECEIPT_PATH))
        plan = plan or load_object(resolve_path(root, PLAN_PATH))
        plan_audit = plan_audit or load_object(resolve_path(root, PLAN_AUDIT_PATH))
        receipt_record = binding(root, RECEIPT_PATH, "activation_receipt")
        preimage_record = binding(root, PREIMAGE_PATH, "pre_activation_schedule_snapshot")
        expected = project_schedule(pre_schedule, receipt, receipt_record, preimage_record, plan, plan_audit)
    except (ActivationError, KeyError) as exc:
        return [f"activation_projection_sources_invalid:{type(exc).__name__}:{exc}"]
    if schedule != expected:
        errors.append("activated_schedule_not_exact_reviewed_projection")
    if set(schedule) != set(pre_schedule) | ACTIVATION_TOP_LEVEL_KEYS:
        errors.append("activated_schedule_top_level_delta_invalid")
    pre_routes, post_routes = pre_schedule.get("routes", {}), schedule.get("routes", {})
    if set(pre_routes) != set(post_routes) or pre_routes.get("student_breakdowns") != post_routes.get("student_breakdowns") or pre_routes.get("questions") != post_routes.get("questions"):
        errors.append("activated_schedule_nonpremium_routes_changed")
    pre_premium, post_premium = pre_routes.get("premium_journals", []), post_routes.get("premium_journals", [])
    if len(pre_premium) != len(post_premium):
        errors.append("activated_schedule_premium_route_count_changed")
    else:
        changed = [index for index, (before, after) in enumerate(zip(pre_premium, post_premium)) if before != after]
        jan9 = plan.get("pre_activation_preservation_manifest", {}).get("jan9_route_index")
        if changed != [jan9]:
            errors.append("activated_schedule_changed_route_set_not_exact_jan9")
    for key, value in pre_schedule.items():
        if key != "routes" and schedule.get(key) != value:
            errors.append(f"activated_schedule_preexisting_top_level_changed:{key}")
    pending_routes = schedule.get("premium_journals_v2_7_authoritative_routes", [])
    if pending_routes != [plan["route_transition"]["pending_v2_7_route"]]:
        errors.append("activated_schedule_pending_v2_7_route_invalid")
    else:
        route = pending_routes[0]
        if route.get("live_collection_enabled") is not True or route.get("promotion_allowed") is not False or route.get("collection_authority_enabled") is not True or route.get("canonical_authority_enabled") is not False or route.get("canonical_promoted") is not False:
            errors.append("activated_schedule_live_pending_flags_invalid")
        if route.get("expected_canonical_path") != v27.expected_canonical_relative_path(DAY, DAY) or route.get("expected_checkpoint_directory") != v27.expected_checkpoint_relative_directory(DAY):
            errors.append("activated_schedule_versioned_paths_invalid")
    retired = [route for route in post_premium if isinstance(route, dict) and route.get("start") == DAY and route.get("end") == DAY]
    if retired != [plan["route_transition"]["retired_v2_6_route"]]:
        errors.append("activated_schedule_retired_v2_6_route_invalid")
    if len(retired) != 1 or len(pending_routes) != 1:
        errors.append("activated_schedule_authority_count_invalid")
    return sorted(set(errors))


def build_projection_bundle(root: Path, preimage_binding: dict[str, Any], receipt_binding: dict[str, Any], plan_binding: dict[str, Any], plan_audit_binding: dict[str, Any], schedule_bytes_value: bytes, plan: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_activation_projection_bundle",
        "activation_id": ACTIVATION_ID,
        "status": "prepared_for_atomic_schedule_replace",
        # The receipt timestamp makes this immutable bundle deterministic
        # across an exact retry after any pre-marker crash.
        "created_at_utc": receipt["created_at_utc"],
        "immutable": True,
        "append_only": True,
        "pre_activation_schedule": preimage_binding,
        "activation_receipt": receipt_binding,
        "activation_plan": plan_binding,
        "activation_plan_independent_audit": plan_audit_binding,
        "projected_schedule": {"path": SCHEDULE_PATH, "sha256": sha256_bytes(schedule_bytes_value), "bytes": len(schedule_bytes_value)},
        "projection_plan_sha256": plan["projection_plan_sha256"],
        "receipt_record_fingerprint_sha256": receipt["record_fingerprint_sha256"],
        "route_transition": copy.deepcopy(plan["route_transition"]),
        "preservation_manifest": copy.deepcopy(plan["pre_activation_preservation_manifest"]),
        "rollback_projection": expected_rollback_projection(),
    }
    payload["record_fingerprint_sha256"] = sha256_json(payload)
    return payload


def build_commit_marker(root: Path, receipt_binding: dict[str, Any], preimage_binding: dict[str, Any], plan_audit_binding: dict[str, Any], bundle_binding: dict[str, Any], schedule_bytes_value: bytes, plan: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_activation_commit_marker",
        "activation_id": ACTIVATION_ID,
        "status": "committed_collection_authority_pending_qa",
        # Deterministic across an exact retry; the receipt is created only
        # after both independent reviews have passed.
        "committed_at_utc": receipt["created_at_utc"],
        "immutable": True,
        "append_only": True,
        "activation_receipt": receipt_binding,
        "pre_activation_schedule": preimage_binding,
        "activation_plan_independent_audit": plan_audit_binding,
        "projection_bundle": bundle_binding,
        "activated_schedule": {"path": SCHEDULE_PATH, "sha256": sha256_bytes(schedule_bytes_value), "bytes": len(schedule_bytes_value)},
        "projection_plan_sha256": plan["projection_plan_sha256"],
        "receipt_record_fingerprint_sha256": receipt["record_fingerprint_sha256"],
        "retired_v2_6_route_sha256": plan["route_transition"]["retired_v2_6_route_sha256"],
        "pending_v2_7_route_sha256": plan["route_transition"]["pending_v2_7_route_sha256"],
        "activation_observed_state": {
            "jan9_v2_6_canonical_present": False,
            "jan9_v2_7_canonical_present": False,
            "jan9_v2_7_checkpoint_directory_present": False,
            "collection_performed": False,
            "promotion_performed": False,
        },
    }
    payload["record_fingerprint_sha256"] = sha256_json(payload)
    return payload


def _fingerprint_errors(value: dict[str, Any], label: str) -> list[str]:
    unsigned = dict(value)
    fingerprint = unsigned.pop("record_fingerprint_sha256", None)
    return [] if fingerprint == sha256_json(unsigned) else [f"{label}_fingerprint_mismatch"]


def validate_projection_bundle(
    bundle: dict[str, Any],
    root: Path,
    schedule_raw: bytes,
    plan: dict[str, Any],
    receipt: dict[str, Any],
) -> list[str]:
    errors = _fingerprint_errors(bundle, "projection_bundle")
    expected_keys = {
        "schema_version", "artifact_type", "activation_id", "status", "created_at_utc",
        "immutable", "append_only", "pre_activation_schedule", "activation_receipt",
        "activation_plan", "activation_plan_independent_audit", "projected_schedule",
        "projection_plan_sha256", "receipt_record_fingerprint_sha256",
        "route_transition", "preservation_manifest", "rollback_projection",
        "record_fingerprint_sha256",
    }
    if set(bundle) != expected_keys:
        errors.append("projection_bundle_key_set_invalid")
    fixed = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_activation_projection_bundle",
        "activation_id": ACTIVATION_ID,
        "status": "prepared_for_atomic_schedule_replace",
        "created_at_utc": receipt.get("created_at_utc"),
        "immutable": True,
        "append_only": True,
        "projection_plan_sha256": plan.get("projection_plan_sha256"),
        "receipt_record_fingerprint_sha256": receipt.get("record_fingerprint_sha256"),
    }
    if any(bundle.get(key) != value for key, value in fixed.items()) or not v26._is_iso_timestamp(bundle.get("created_at_utc")):
        errors.append("projection_bundle_fixed_contract_invalid")
    try:
        expected_bindings = {
            "pre_activation_schedule": binding(root, PREIMAGE_PATH, "pre_activation_schedule_snapshot"),
            "activation_receipt": binding(root, RECEIPT_PATH, "activation_receipt"),
            "activation_plan": binding(root, PLAN_PATH, "independently_reviewed_activation_plan"),
            "activation_plan_independent_audit": binding(root, PLAN_AUDIT_PATH, "activation_plan_independent_audit_report"),
        }
        for field, expected in expected_bindings.items():
            if bundle.get(field) != expected:
                errors.append(f"projection_bundle_{field}_binding_invalid")
    except ActivationError:
        errors.append("projection_bundle_expected_binding_rederivation_failed")
    if bundle.get("projected_schedule") != {
        "path": SCHEDULE_PATH,
        "sha256": sha256_bytes(schedule_raw),
        "bytes": len(schedule_raw),
    }:
        errors.append("projection_bundle_schedule_binding_invalid")
    if bundle.get("route_transition") != plan.get("route_transition"):
        errors.append("projection_bundle_route_transition_invalid")
    if bundle.get("preservation_manifest") != plan.get("pre_activation_preservation_manifest"):
        errors.append("projection_bundle_preservation_manifest_invalid")
    if bundle.get("rollback_projection") != expected_rollback_projection():
        errors.append("projection_bundle_rollback_projection_invalid")
    return sorted(set(errors))


def validate_commit_marker(
    marker: dict[str, Any],
    root: Path,
    schedule_raw: bytes,
    plan: dict[str, Any],
    receipt: dict[str, Any],
) -> list[str]:
    errors = _fingerprint_errors(marker, "commit_marker")
    expected_keys = {
        "schema_version", "artifact_type", "activation_id", "status", "committed_at_utc",
        "immutable", "append_only", "activation_receipt", "pre_activation_schedule",
        "activation_plan_independent_audit", "projection_bundle", "activated_schedule",
        "projection_plan_sha256", "receipt_record_fingerprint_sha256",
        "retired_v2_6_route_sha256", "pending_v2_7_route_sha256",
        "activation_observed_state", "record_fingerprint_sha256",
    }
    if set(marker) != expected_keys:
        errors.append("commit_marker_key_set_invalid")
    fixed = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_activation_commit_marker",
        "activation_id": ACTIVATION_ID,
        "status": "committed_collection_authority_pending_qa",
        "committed_at_utc": receipt.get("created_at_utc"),
        "immutable": True,
        "append_only": True,
        "projection_plan_sha256": plan.get("projection_plan_sha256"),
        "receipt_record_fingerprint_sha256": receipt.get("record_fingerprint_sha256"),
        "retired_v2_6_route_sha256": plan.get("route_transition", {}).get("retired_v2_6_route_sha256"),
        "pending_v2_7_route_sha256": plan.get("route_transition", {}).get("pending_v2_7_route_sha256"),
    }
    if any(marker.get(key) != value for key, value in fixed.items()) or not v26._is_iso_timestamp(marker.get("committed_at_utc")):
        errors.append("commit_marker_fixed_contract_invalid")
    try:
        expected_bindings = {
            "activation_receipt": binding(root, RECEIPT_PATH, "activation_receipt"),
            "pre_activation_schedule": binding(root, PREIMAGE_PATH, "pre_activation_schedule_snapshot"),
            "activation_plan_independent_audit": binding(root, PLAN_AUDIT_PATH, "activation_plan_independent_audit_report"),
            "projection_bundle": binding(root, PROJECTION_BUNDLE_PATH, "activation_projection_bundle"),
        }
        for field, expected in expected_bindings.items():
            if marker.get(field) != expected:
                errors.append(f"commit_marker_{field}_binding_invalid")
    except ActivationError:
        errors.append("commit_marker_expected_binding_rederivation_failed")
    if marker.get("activated_schedule") != {
        "path": SCHEDULE_PATH,
        "sha256": sha256_bytes(schedule_raw),
        "bytes": len(schedule_raw),
    }:
        errors.append("commit_marker_schedule_binding_invalid")
    expected_observed = {
        "jan9_v2_6_canonical_present": False,
        "jan9_v2_7_canonical_present": False,
        "jan9_v2_7_checkpoint_directory_present": False,
        "collection_performed": False,
        "promotion_performed": False,
    }
    if marker.get("activation_observed_state") != expected_observed:
        errors.append("commit_marker_activation_observed_state_invalid")
    return sorted(set(errors))


def validate_committed_activation(root: Path | None = None, *, require_activation_time_absence: bool = False) -> list[str]:
    root = (root or Path(__file__).resolve().parent).resolve()
    errors: list[str] = []
    try:
        plan = load_object(resolve_path(root, PLAN_PATH))
        plan_audit = load_object(resolve_path(root, PLAN_AUDIT_PATH))
        receipt = load_object(resolve_path(root, RECEIPT_PATH))
        bundle = load_object(resolve_path(root, PROJECTION_BUNDLE_PATH))
        marker = load_object(resolve_path(root, COMMIT_MARKER_PATH))
        schedule = load_object(resolve_path(root, SCHEDULE_PATH))
        pre_schedule = load_object(resolve_path(root, PREIMAGE_PATH))
    except ActivationError as exc:
        return [str(exc)]
    errors.extend(validate_plan(plan, root, require_live_prestate=False))
    errors.extend(validate_plan_audit(plan, plan_audit, root))
    errors.extend(validate_receipt(receipt, root, plan, plan_audit, require_live_prestate=False))
    errors.extend(validate_schedule_projection(schedule, root, pre_schedule=pre_schedule, receipt=receipt, plan=plan, plan_audit=plan_audit))
    schedule_raw = resolve_path(root, SCHEDULE_PATH).read_bytes()
    errors.extend(validate_projection_bundle(bundle, root, schedule_raw, plan, receipt))
    errors.extend(validate_commit_marker(marker, root, schedule_raw, plan, receipt))
    if require_activation_time_absence:
        if resolve_path(root, v26.expected_canonical_relative_path(DAY, DAY)).is_file() or resolve_path(root, v27.expected_canonical_relative_path(DAY, DAY)).is_file() or resolve_path(root, v27.expected_checkpoint_relative_directory(DAY)).exists():
            errors.append("post_activation_unexpected_jan9_collection_artifact")
    if (sha256_file(resolve_path(root, PREIMAGE_PATH)), resolve_path(root, PREIMAGE_PATH).stat().st_size) != (PRE_SCHEDULE_SHA256, PRE_SCHEDULE_BYTES):
        errors.append("preimage_raw_bytes_changed")
    return sorted(set(errors))


def load_committed_route_snapshot(root: Path) -> dict[str, Any]:
    """Load the v2.7 route from one schedule snapshot bound to the marker."""
    schedule_path = resolve_path(root, SCHEDULE_PATH)
    schedule_raw = schedule_path.read_bytes()
    marker = load_object(resolve_path(root, COMMIT_MARKER_PATH))
    plan = load_object(resolve_path(root, PLAN_PATH))
    receipt = load_object(resolve_path(root, RECEIPT_PATH))
    marker_errors = validate_commit_marker(marker, root, schedule_raw, plan, receipt)
    if marker_errors:
        raise ActivationError("route snapshot marker invalid: " + "; ".join(marker_errors))
    try:
        schedule = json.loads(schedule_raw.decode("utf-8"))
    except Exception as exc:
        raise ActivationError(f"route snapshot schedule unreadable: {type(exc).__name__}") from exc
    if not isinstance(schedule, dict):
        raise ActivationError("route snapshot schedule is not an object")
    routes = schedule.get("premium_journals_v2_7_authoritative_routes")
    if not isinstance(routes, list) or len(routes) != 1 or not isinstance(routes[0], dict):
        raise ActivationError("route snapshot has no single v2.7 authority route")
    route = routes[0]
    expected_route_hash = marker.get("pending_v2_7_route_sha256")
    if sha256_json(route) != expected_route_hash:
        raise ActivationError("route snapshot hash does not match commit marker")
    if route != plan.get("route_transition", {}).get("pending_v2_7_route"):
        raise ActivationError("route snapshot does not match independently reviewed plan")
    return {
        "route": route,
        "route_sha256": expected_route_hash,
        "schedule_sha256": sha256_bytes(schedule_raw),
        "schedule_bytes": len(schedule_raw),
    }


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exclusive_or_exact(path: Path, raw: bytes) -> str:
    """Crash-atomically publish immutable bytes without replacing a peer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_file() and path.read_bytes() == raw:
            return "reused_exact"
        raise ActivationError(f"immutable artifact collision: {path}")
    temp = path.with_name(f".{path.name}.{ACTIVATION_ID}.{uuid.uuid4().hex}.immutable.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if temp.read_bytes() != raw:
            raise ActivationError(f"immutable temp verification failed: {path}")
        try:
            # A same-directory hard-link publish is atomic and refuses to
            # overwrite an artifact another executor may have created.
            os.link(temp, path)
        except FileExistsError:
            if path.is_file() and path.read_bytes() == raw:
                return "reused_exact"
            raise ActivationError(f"immutable artifact collision: {path}")
        _fsync_directory(path.parent)
        if not path.is_file() or path.read_bytes() != raw:
            raise ActivationError(f"immutable publish verification failed: {path}")
        return "created"
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            # A fully written orphan temp is harmless and never authoritative.
            pass


def _atomic_replace(path: Path, raw: bytes) -> None:
    temp = path.with_name(f".{path.name}.{ACTIVATION_ID}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if temp.read_bytes() != raw:
            raise ActivationError("atomic schedule temp verification failed")
        os.replace(temp, path)
        _fsync_directory(path.parent)
        if not path.is_file() or path.read_bytes() != raw:
            raise ActivationError("atomic schedule target verification failed")
    finally:
        if temp.exists():
            temp.unlink()


def preserve_superseded_jan9_preimage(root: Path | None = None) -> Path:
    """Preserve the retired Jan 9 draft input without creating authority."""
    root = (root or Path(__file__).resolve().parent).resolve()
    path = resolve_path(root, SUPERSEDED_PREIMAGE_PATH)
    if path.is_file():
        if (sha256_file(path), path.stat().st_size) != (PRE_SCHEDULE_SHA256, PRE_SCHEDULE_BYTES):
            raise ActivationError("existing superseded Jan9 preimage is not exact")
        return path
    raw = resolve_path(root, SCHEDULE_PATH).read_bytes()
    if (sha256_bytes(raw), len(raw)) != (PRE_SCHEDULE_SHA256, PRE_SCHEDULE_BYTES):
        raise ActivationError("Jan9 draft preimage is no longer the exact reviewed schedule")
    _write_exclusive_or_exact(path, raw)
    return path


def _archive_superseded_file(
    root: Path,
    source_relative: str,
    archive_group: str,
    *,
    source_raw: bytes | None = None,
) -> dict[str, Any]:
    source = resolve_path(root, source_relative)
    if not source.is_file():
        raise ActivationError(f"superseded draft source missing: {source_relative}")
    archive_relative = f"{SUPERSEDED_DRAFT_DIRECTORY}/{archive_group}/{source_relative}"
    archive = resolve_path(root, archive_relative)
    raw = source.read_bytes() if source_raw is None else source_raw
    _write_exclusive_or_exact(archive, raw)
    return {
        "source_path_at_supersession": source_relative,
        "archive_path": archive_relative,
        "sha256": sha256_bytes(raw),
        "bytes": len(raw),
    }


def build_supersession_manifest(root: Path, archived_files: list[dict[str, Any]]) -> dict[str, Any]:
    forbidden = [
        PREIMAGE_PATH,
        PLAN_PATH,
        PLAN_AUDIT_PATH,
        RECEIPT_PATH,
        PROJECTION_BUNDLE_PATH,
        COMMIT_MARKER_PATH,
        ROLLBACK_RECEIPT_PATH,
    ]
    if any(resolve_path(root, relative).exists() for relative in forbidden):
        raise ActivationError("Jan9 activation artifact exists; supersession cannot be asserted")
    live_raw = resolve_path(root, SCHEDULE_PATH).read_bytes()
    if (sha256_bytes(live_raw), len(live_raw)) != (PRE_SCHEDULE_SHA256, PRE_SCHEDULE_BYTES):
        raise ActivationError("live schedule changed before Jan9 supersession manifest publication")
    preimage = resolve_path(root, SUPERSEDED_PREIMAGE_PATH)
    if (sha256_file(preimage), preimage.stat().st_size) != (PRE_SCHEDULE_SHA256, PRE_SCHEDULE_BYTES):
        raise ActivationError("superseded Jan9 preimage binding changed")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_jan9_activation_supersession_manifest",
        "activation_id": ACTIVATION_ID,
        "status": DRAFT_STATUS,
        "superseded_at_utc": utc_now(),
        "immutable": True,
        "append_only": True,
        "reason": SUPERSESSION_REASON,
        "live_schedule_at_supersession": {
            "path": SCHEDULE_PATH,
            "sha256": PRE_SCHEDULE_SHA256,
            "bytes": PRE_SCHEDULE_BYTES,
        },
        "preserved_pre_activation_schedule": {
            "path": SUPERSEDED_PREIMAGE_PATH,
            "sha256": PRE_SCHEDULE_SHA256,
            "bytes": PRE_SCHEDULE_BYTES,
            "authority": "none_historical_evidence_only",
        },
        "archived_files": sorted(archived_files, key=lambda item: item["archive_path"]),
        "archived_file_set_sha256": "",
        "forbidden_jan9_activation_artifacts": forbidden,
        "forbidden_artifacts_present_at_supersession": False,
        "jan9_authority": "v2.6_schedule_only",
        "jan9_v2_7_authority": False,
        "jan9_v2_7_collection_authorized": False,
        "jan9_v2_7_canonical_or_promotion_authorized": False,
        "first_future_activation_target": {
            "day": "2026-01-10",
            "requires_jan9_v2_6_promotion_first": True,
            "requires_new_schedule_sha256_and_bytes": True,
            "requires_new_candidate_plan_audit_receipt_bundle_and_marker": True,
            "inherits_jan9_activation_authority": False,
        },
    }
    payload["archived_file_set_sha256"] = sha256_json(payload["archived_files"])
    payload["record_fingerprint_sha256"] = sha256_json(payload)
    return payload


def validate_supersession_manifest(manifest: dict[str, Any], root: Path) -> list[str]:
    errors: list[str] = []
    expected_keys = {
        "schema_version", "artifact_type", "activation_id", "status",
        "superseded_at_utc", "immutable", "append_only", "reason",
        "live_schedule_at_supersession", "preserved_pre_activation_schedule",
        "archived_files", "archived_file_set_sha256",
        "forbidden_jan9_activation_artifacts",
        "forbidden_artifacts_present_at_supersession", "jan9_authority",
        "jan9_v2_7_authority", "jan9_v2_7_collection_authorized",
        "jan9_v2_7_canonical_or_promotion_authorized",
        "first_future_activation_target", "record_fingerprint_sha256",
    }
    if set(manifest) != expected_keys:
        errors.append("supersession_manifest_key_set_invalid")
    fixed = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_jan9_activation_supersession_manifest",
        "activation_id": ACTIVATION_ID,
        "status": DRAFT_STATUS,
        "immutable": True,
        "append_only": True,
        "jan9_authority": "v2.6_schedule_only",
        "jan9_v2_7_authority": False,
        "jan9_v2_7_collection_authorized": False,
        "jan9_v2_7_canonical_or_promotion_authorized": False,
        "forbidden_artifacts_present_at_supersession": False,
    }
    if any(manifest.get(key) != value for key, value in fixed.items()):
        errors.append("supersession_manifest_fixed_contract_invalid")
    if manifest.get("reason") != SUPERSESSION_REASON:
        errors.append("supersession_manifest_reason_invalid")
    if not v26._is_iso_timestamp(manifest.get("superseded_at_utc")):
        errors.append("supersession_manifest_timestamp_invalid")
    unsigned = dict(manifest)
    unsigned.pop("record_fingerprint_sha256", None)
    if manifest.get("record_fingerprint_sha256") != sha256_json(unsigned):
        errors.append("supersession_manifest_fingerprint_mismatch")
    files = manifest.get("archived_files")
    if not isinstance(files, list) or not files or manifest.get("archived_file_set_sha256") != sha256_json(files):
        errors.append("supersession_manifest_file_set_invalid")
        files = []
    for index, record in enumerate(files):
        if not isinstance(record, dict) or set(record) != {"source_path_at_supersession", "archive_path", "sha256", "bytes"}:
            errors.append(f"supersession_manifest_file_{index}_schema_invalid")
            continue
        errors.extend(binding_errors(
            root,
            {"path": record["archive_path"], "sha256": record["sha256"], "bytes": record["bytes"]},
            f"supersession_manifest_file_{index}",
            with_role=False,
        ))
    expected_mapping = {
        **{
            relative: f"{SUPERSEDED_DRAFT_DIRECTORY}/implementation/{relative}"
            for relative in IMPLEMENTATION_PATHS
        },
        **{
            relative: f"{SUPERSEDED_DRAFT_DIRECTORY}/historical_inputs/{relative}"
            for relative in (CANDIDATE_PATH, READINESS_PATH, PRIOR_AUDIT_PATH)
        },
    }
    valid_records = [record for record in files if isinstance(record, dict)]
    observed_pairs = [
        (record.get("source_path_at_supersession"), record.get("archive_path"))
        for record in valid_records
        if isinstance(record.get("source_path_at_supersession"), str)
        and isinstance(record.get("archive_path"), str)
    ]
    if len(valid_records) != len(expected_mapping) or len(observed_pairs) != len(valid_records) or len(set(observed_pairs)) != len(observed_pairs):
        errors.append("supersession_manifest_duplicate_or_missing_record")
    if dict(observed_pairs) != expected_mapping:
        errors.append("supersession_manifest_source_archive_mapping_invalid")
    if files != sorted(
        files,
        key=lambda item: (
            item.get("archive_path", "")
            if isinstance(item, dict) and isinstance(item.get("archive_path", ""), str)
            else ""
        ),
    ):
        errors.append("supersession_manifest_file_order_invalid")
    historical_expected = {
        CANDIDATE_PATH: (CANDIDATE_SHA256, CANDIDATE_BYTES),
        READINESS_PATH: (READINESS_SHA256, READINESS_BYTES),
        PRIOR_AUDIT_PATH: (PRIOR_AUDIT_SHA256, PRIOR_AUDIT_BYTES),
    }
    for record in valid_records:
        source = record.get("source_path_at_supersession")
        if source in historical_expected and (record.get("sha256"), record.get("bytes")) != historical_expected[source]:
            errors.append(f"supersession_manifest_historical_binding_invalid:{source}")
    if manifest.get("live_schedule_at_supersession") != {
        "path": SCHEDULE_PATH,
        "sha256": PRE_SCHEDULE_SHA256,
        "bytes": PRE_SCHEDULE_BYTES,
    }:
        errors.append("supersession_manifest_historical_live_schedule_binding_invalid")
    expected_preimage = {
        "path": SUPERSEDED_PREIMAGE_PATH,
        "sha256": PRE_SCHEDULE_SHA256,
        "bytes": PRE_SCHEDULE_BYTES,
        "authority": "none_historical_evidence_only",
    }
    if manifest.get("preserved_pre_activation_schedule") != expected_preimage:
        errors.append("supersession_manifest_preimage_binding_invalid")
    forbidden = [PREIMAGE_PATH, PLAN_PATH, PLAN_AUDIT_PATH, RECEIPT_PATH, PROJECTION_BUNDLE_PATH, COMMIT_MARKER_PATH, ROLLBACK_RECEIPT_PATH]
    if manifest.get("forbidden_jan9_activation_artifacts") != forbidden:
        errors.append("supersession_manifest_forbidden_path_set_invalid")
    if any(resolve_path(root, relative).exists() for relative in forbidden):
        errors.append("supersession_manifest_forbidden_artifact_exists")
    future = manifest.get("first_future_activation_target", {})
    if future != {
        "day": "2026-01-10",
        "requires_jan9_v2_6_promotion_first": True,
        "requires_new_schedule_sha256_and_bytes": True,
        "requires_new_candidate_plan_audit_receipt_bundle_and_marker": True,
        "inherits_jan9_activation_authority": False,
    }:
        errors.append("supersession_manifest_future_target_invalid")
    return sorted(set(errors))


def archive_superseded_jan9_draft(root: Path | None = None) -> Path:
    root = (root or Path(__file__).resolve().parent).resolve()
    with activation_lock(root, lock_relative=SUPERSEDED_ARCHIVE_LOCK_PATH):
        manifest_path = resolve_path(root, SUPERSEDED_MANIFEST_PATH)
        if manifest_path.is_file():
            manifest = load_object(manifest_path)
            errors = validate_supersession_manifest(manifest, root)
            if errors:
                raise ActivationError("existing supersession manifest invalid: " + "; ".join(errors))
            return manifest_path
        live_raw = resolve_path(root, SCHEDULE_PATH).read_bytes()
        if (sha256_bytes(live_raw), len(live_raw)) != (PRE_SCHEDULE_SHA256, PRE_SCHEDULE_BYTES):
            raise ActivationError("live schedule changed before Jan9 supersession archive")
        forbidden = [PREIMAGE_PATH, PLAN_PATH, PLAN_AUDIT_PATH, RECEIPT_PATH, PROJECTION_BUNDLE_PATH, COMMIT_MARKER_PATH, ROLLBACK_RECEIPT_PATH]
        if any(resolve_path(root, relative).exists() for relative in forbidden):
            raise ActivationError("Jan9 activation artifact exists; refusing supersession archive")
        candidate = load_object(resolve_path(root, CANDIDATE_PATH))
        readiness = load_object(resolve_path(root, READINESS_PATH))
        prior_audit = load_object(resolve_path(root, PRIOR_AUDIT_PATH))
        _exact_known_binding(root, CANDIDATE_PATH, "historical_candidate", CANDIDATE_SHA256, CANDIDATE_BYTES)
        _exact_known_binding(root, READINESS_PATH, "historical_readiness", READINESS_SHA256, READINESS_BYTES)
        _exact_known_binding(root, PRIOR_AUDIT_PATH, "historical_prior_audit", PRIOR_AUDIT_SHA256, PRIOR_AUDIT_BYTES)
        if candidate.get("record_fingerprint_sha256") != CANDIDATE_FINGERPRINT or readiness.get("record_fingerprint_sha256") != READINESS_FINGERPRINT:
            raise ActivationError("historical candidate/readiness fingerprint changed")
        prior_errors = validate_prior_audit(prior_audit, candidate, readiness)
        if prior_errors:
            raise ActivationError("historical independent audit invalid: " + "; ".join(prior_errors))
        preserve_superseded_jan9_preimage(root)
        entries = [
            (relative, "implementation") for relative in IMPLEMENTATION_PATHS
        ] + [
            (relative, "historical_inputs")
            for relative in (CANDIDATE_PATH, READINESS_PATH, PRIOR_AUDIT_PATH)
        ]
        snapshots = {
            relative: resolve_path(root, relative).read_bytes()
            for relative, _group in entries
        }
        for relative, raw in snapshots.items():
            if resolve_path(root, relative).read_bytes() != raw:
                raise ActivationError(f"supersession source changed during snapshot: {relative}")
        archived = [
            _archive_superseded_file(
                root,
                relative,
                group,
                source_raw=snapshots[relative],
            )
            for relative, group in entries
        ]
        for relative, raw in snapshots.items():
            if resolve_path(root, relative).read_bytes() != raw:
                raise ActivationError(f"supersession source changed before manifest: {relative}")
        manifest = build_supersession_manifest(root, archived)
        _write_exclusive_or_exact(manifest_path, json_bytes(manifest))
        errors = validate_supersession_manifest(load_object(manifest_path), root)
        if errors:
            raise ActivationError("written supersession manifest invalid: " + "; ".join(errors))
        return manifest_path


@contextmanager
def activation_lock(root: Path, *, timeout_seconds: float = 30.0, lock_relative: str = LOCK_PATH) -> Iterable[None]:
    """Hold an OS-released cross-process lock across the complete commit."""
    path = resolve_path(root, lock_relative)
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
                    raise ActivationError("activation lock acquisition timed out") from exc
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


def _write_plan_exclusive_fixture(root: Path) -> Path:
    """Private isolated-fixture helper; never an authority API."""
    root = root.resolve()
    schedule_path = resolve_path(root, SCHEDULE_PATH)
    raw = schedule_path.read_bytes()
    if (sha256_bytes(raw), len(raw)) != (PRE_SCHEDULE_SHA256, PRE_SCHEDULE_BYTES):
        raise ActivationError("cannot preserve plan pre-image: live schedule bytes changed")
    _write_exclusive_or_exact(resolve_path(root, PREIMAGE_PATH), raw)
    path = resolve_path(root, PLAN_PATH)
    if path.is_file():
        existing = load_object(path)
        errors = validate_plan(existing, root)
        if errors:
            raise ActivationError("existing immutable activation plan invalid: " + "; ".join(errors))
        return path
    plan = build_plan(root)
    _write_exclusive_or_exact(path, json_bytes(plan))
    return path


def write_plan_exclusive(root: Path | None = None) -> Path:
    raise ActivationError(
        "Jan9 activation draft is superseded for every root; first future authority plan must target Jan10"
    )


def activation_time_absence_errors(root: Path) -> list[str]:
    errors: list[str] = []
    if resolve_path(root, v26.expected_canonical_relative_path(DAY, DAY)).is_file():
        errors.append("activation_time_jan9_v2_6_canonical_exists")
    if resolve_path(root, v27.expected_canonical_relative_path(DAY, DAY)).is_file():
        errors.append("activation_time_jan9_v2_7_canonical_exists")
    if resolve_path(root, v27.expected_checkpoint_relative_directory(DAY)).exists():
        errors.append("activation_time_jan9_v2_7_checkpoint_directory_exists")
    if resolve_path(root, ROLLBACK_RECEIPT_PATH).exists():
        errors.append("activation_time_rollback_receipt_preexists")
    return errors


def _write_final_marker(
    root: Path,
    schedule_raw: bytes,
    plan: dict[str, Any],
    plan_audit: dict[str, Any],
    receipt: dict[str, Any],
) -> None:
    receipt_record = binding(root, RECEIPT_PATH, "activation_receipt")
    preimage_record = binding(root, PREIMAGE_PATH, "pre_activation_schedule_snapshot")
    plan_audit_record = binding(root, PLAN_AUDIT_PATH, "activation_plan_independent_audit_report")
    bundle_record = binding(root, PROJECTION_BUNDLE_PATH, "activation_projection_bundle")
    marker = build_commit_marker(
        root,
        receipt_record,
        preimage_record,
        plan_audit_record,
        bundle_record,
        schedule_raw,
        plan,
        receipt,
    )
    _write_exclusive_or_exact(resolve_path(root, COMMIT_MARKER_PATH), json_bytes(marker))


def validate_unmarked_projection(root: Path) -> list[str]:
    """Validate a complete reviewed projection that is not yet authoritative."""
    errors: list[str] = []
    if resolve_path(root, COMMIT_MARKER_PATH).exists():
        errors.append("unmarked_projection_commit_marker_present")
    try:
        plan = load_object(resolve_path(root, PLAN_PATH))
        plan_audit = load_object(resolve_path(root, PLAN_AUDIT_PATH))
        receipt = load_object(resolve_path(root, RECEIPT_PATH))
        bundle = load_object(resolve_path(root, PROJECTION_BUNDLE_PATH))
        schedule = load_object(resolve_path(root, SCHEDULE_PATH))
        pre_schedule = load_object(resolve_path(root, PREIMAGE_PATH))
        schedule_raw = resolve_path(root, SCHEDULE_PATH).read_bytes()
    except ActivationError as exc:
        return [f"unmarked_projection_source_invalid:{exc}"]
    errors.extend(validate_plan(plan, root, require_live_prestate=False))
    errors.extend(validate_plan_audit(plan, plan_audit, root))
    errors.extend(validate_receipt(receipt, root, plan, plan_audit, require_live_prestate=False))
    errors.extend(validate_schedule_projection(
        schedule,
        root,
        pre_schedule=pre_schedule,
        receipt=receipt,
        plan=plan,
        plan_audit=plan_audit,
    ))
    errors.extend(validate_projection_bundle(bundle, root, schedule_raw, plan, receipt))
    errors.extend(activation_time_absence_errors(root))
    return sorted(set(errors))


def _resume_exact_unmarked_projection(root: Path, schedule_raw: bytes) -> dict[str, Any]:
    """Finish only an exact, fully reviewed projection left by a pre-marker crash."""
    errors = validate_unmarked_projection(root)
    if errors:
        raise ActivationError("unmarked projection recovery validation failed: " + "; ".join(sorted(set(errors))))
    if resolve_path(root, SCHEDULE_PATH).read_bytes() != schedule_raw:
        raise ActivationError("unmarked projection changed during locked recovery")
    plan = load_object(resolve_path(root, PLAN_PATH))
    plan_audit = load_object(resolve_path(root, PLAN_AUDIT_PATH))
    receipt = load_object(resolve_path(root, RECEIPT_PATH))
    _write_final_marker(root, schedule_raw, plan, plan_audit, receipt)
    committed_errors = validate_committed_activation(root, require_activation_time_absence=True)
    if committed_errors:
        raise ActivationError("resumed commit validation failed: " + "; ".join(committed_errors))
    return {
        "status": "resumed_exact_unmarked_projection_and_committed",
        "schedule": simple_binding(root, SCHEDULE_PATH),
        "receipt": simple_binding(root, RECEIPT_PATH),
        "commit_marker": simple_binding(root, COMMIT_MARKER_PATH),
        "projection_bundle": simple_binding(root, PROJECTION_BUNDLE_PATH),
        "preimage": simple_binding(root, PREIMAGE_PATH),
    }


def _execute_activation_locked(root: Path) -> dict[str, Any]:
    marker_path = resolve_path(root, COMMIT_MARKER_PATH)
    if marker_path.is_file():
        errors = validate_committed_activation(root)
        if errors:
            raise ActivationError("existing commit marker invalid: " + "; ".join(errors))
        return {"status": "already_committed_exact", "schedule": simple_binding(root, SCHEDULE_PATH), "receipt": simple_binding(root, RECEIPT_PATH), "commit_marker": simple_binding(root, COMMIT_MARKER_PATH)}
    schedule_path = resolve_path(root, SCHEDULE_PATH)
    live_raw = schedule_path.read_bytes()
    if (sha256_bytes(live_raw), len(live_raw)) != (PRE_SCHEDULE_SHA256, PRE_SCHEDULE_BYTES):
        return _resume_exact_unmarked_projection(root, live_raw)
    pre_errors = pre_activation_errors(root)
    pre_errors.extend(activation_time_absence_errors(root))
    if pre_errors:
        raise ActivationError("pre-activation validation failed: " + "; ".join(sorted(set(pre_errors))))
    plan = load_object(resolve_path(root, PLAN_PATH))
    plan_errors = validate_plan(plan, root)
    if plan_errors:
        raise ActivationError("activation plan invalid: " + "; ".join(plan_errors))
    plan_audit = load_object(resolve_path(root, PLAN_AUDIT_PATH))
    audit_errors = validate_plan_audit(plan, plan_audit, root)
    if audit_errors:
        raise ActivationError("activation plan audit invalid: " + "; ".join(audit_errors))
    pre_raw = schedule_path.read_bytes()
    if (sha256_bytes(pre_raw), len(pre_raw)) != (PRE_SCHEDULE_SHA256, PRE_SCHEDULE_BYTES):
        raise ActivationError("live pre-schedule bytes changed")
    preimage_path = resolve_path(root, PREIMAGE_PATH)
    _write_exclusive_or_exact(preimage_path, pre_raw)
    preimage_record = binding(root, PREIMAGE_PATH, "pre_activation_schedule_snapshot")
    receipt_path = resolve_path(root, RECEIPT_PATH)
    if receipt_path.is_file():
        receipt = load_object(receipt_path)
    else:
        receipt = build_receipt(root, plan, plan_audit, preimage_record)
        _write_exclusive_or_exact(receipt_path, json_bytes(receipt))
    receipt_record = binding(root, RECEIPT_PATH, "activation_receipt")
    receipt_errors = validate_receipt(receipt, root, plan, plan_audit, require_live_prestate=True)
    if receipt_errors:
        raise ActivationError("activation receipt invalid: " + "; ".join(receipt_errors))
    pre_schedule = load_object(preimage_path)
    projected = project_schedule(pre_schedule, receipt, receipt_record, preimage_record, plan, plan_audit)
    projection_errors = validate_schedule_projection(projected, root, pre_schedule=pre_schedule, receipt=receipt, plan=plan, plan_audit=plan_audit)
    if projection_errors:
        raise ActivationError("schedule projection invalid: " + "; ".join(projection_errors))
    projected_raw = json_bytes(projected)
    plan_record = binding(root, PLAN_PATH, "independently_reviewed_activation_plan")
    plan_audit_record = binding(root, PLAN_AUDIT_PATH, "activation_plan_independent_audit_report")
    bundle = build_projection_bundle(root, preimage_record, receipt_record, plan_record, plan_audit_record, projected_raw, plan, receipt)
    bundle_path = resolve_path(root, PROJECTION_BUNDLE_PATH)
    _write_exclusive_or_exact(bundle_path, json_bytes(bundle))
    bundle_errors = validate_projection_bundle(load_object(bundle_path), root, projected_raw, plan, receipt)
    if bundle_errors:
        raise ActivationError("activation projection bundle invalid: " + "; ".join(bundle_errors))
    # Marker is intentionally written last.  Until then readers must use the
    # bound pre-image as v2.6 authority even if a crash follows replacement.
    _atomic_replace(schedule_path, projected_raw)
    try:
        _write_final_marker(root, projected_raw, plan, plan_audit, receipt)
    except Exception as marker_error:
        # Never overwrite an exact valid commit, even if an unexpected peer
        # ignored the lock. Restore only the projection this executor wrote.
        if marker_path.is_file() and not validate_committed_activation(root):
            return {
                "status": "concurrent_exact_commit_already_valid",
                "schedule": simple_binding(root, SCHEDULE_PATH),
                "receipt": simple_binding(root, RECEIPT_PATH),
                "commit_marker": simple_binding(root, COMMIT_MARKER_PATH),
            }
        current_raw = schedule_path.read_bytes()
        if current_raw == projected_raw:
            _atomic_replace(schedule_path, pre_raw)
        elif current_raw != pre_raw:
            raise ActivationError(
                "marker failure found unknown live schedule; refusing destructive restore"
            ) from marker_error
        raise
    errors = validate_committed_activation(root, require_activation_time_absence=True)
    if errors:
        raise ActivationError("post-commit validation failed: " + "; ".join(errors))
    return {"status": "committed_collection_authority_pending_qa", "schedule": simple_binding(root, SCHEDULE_PATH), "receipt": simple_binding(root, RECEIPT_PATH), "commit_marker": simple_binding(root, COMMIT_MARKER_PATH), "projection_bundle": simple_binding(root, PROJECTION_BUNDLE_PATH), "preimage": simple_binding(root, PREIMAGE_PATH)}


def execute_activation(root: Path | None = None) -> dict[str, Any]:
    raise ActivationError(
        "Jan9 activation draft is superseded for every root and cannot commit; wait for the Jan10 plan"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-superseded", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    try:
        if args.archive_superseded:
            path = archive_superseded_jan9_draft(root)
            result = {"status": DRAFT_STATUS, "path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
        else:
            result = {
                "status": DRAFT_STATUS,
                "mode": "superseded_harness_only",
                "jan9_v2_7_authority": False,
                "first_future_activation_target": "2026-01-10",
                "errors": [],
            }
        print(json.dumps(result, indent=2))
        return 0 if args.archive_superseded and result.get("status") == DRAFT_STATUS else 2
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}:{exc}"}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
