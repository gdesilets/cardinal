"""Immutable Jan 10 Premium Journals v2.7 activation *review* package.

This module publishes evidence for an independent pre-publication audit.  It
has no schedule writer, activation receipt writer, commit-marker writer,
canonical writer, collector entry point, or live-route resolver.  Even a valid
independent audit remains non-authoritative until a separate future activation
transaction is designed, reviewed, and explicitly executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import premium_journals_provenance_contract as v26
import premium_journals_provenance_contract_v2_7 as v27
import premium_journals_v2_7_authority_activation_v1 as jan9_activation


SCHEMA_VERSION = "1.0.0"
PACKAGE_ID = "premium-journals-v2-7-jan10-activation-review-v1"
DAY = "2026-01-10"
TIMEZONE = "America/Chicago"
QUERY = "in:premium-journals after:2026-01-09 before:2026-01-11"
PACKAGE_PREPARED_AT_UTC = "2026-07-22T10:29:46.9572089Z"

SCHEDULE_PATH = "working/scoped_three_parent_collection_schedule.json"
SCHEDULE_SHA256 = "64ab77a9520dbc80d072d3b51347169c825eb60eba4c6a6b6bc363b37647901a"
SCHEDULE_BYTES = 975585
JAN9_SUPERSESSION_MANIFEST_PATH = (
    "working/superseded_premium_journals_v2_7_jan9_activation_draft_v1/"
    "supersession_manifest.json"
)
JAN9_SUPERSESSION_MANIFEST_SHA256 = (
    "711e540a5e032194496f1763b8144f6ff27b5ee77ca656865253270205f0a322"
)
JAN9_SUPERSESSION_MANIFEST_BYTES = 7202
JAN9_SUPERSESSION_RECORD_FINGERPRINT = (
    "43564b9b1022f969b3391c4028b71c461390094e024cff91a899421ba3788f2e"
)
JAN9_SUPERSESSION_ARCHIVE_LOCK_PATH = jan9_activation.SUPERSEDED_ARCHIVE_LOCK_PATH
JAN9_SUPERSESSION_ARCHIVE_LOCK_SHA256 = (
    "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d"
)
JAN9_SUPERSESSION_ARCHIVE_LOCK_BYTES = 1
QUERY_CHECKLIST_PATH = "working/premium_journals_jan10_query_timing_checklist.json"
QUERY_CHECKLIST_SHA256 = "5a5b1d9d3fbcd93fcb85d17a59118f029c3de0a59d83a36d2aa146e86658bdc0"
QUERY_CHECKLIST_BYTES = 3606
JAN9_CANONICAL_PATH = v26.expected_canonical_relative_path("2026-01-09", "2026-01-09")
JAN9_FINAL_AUDIT_PATH = "working/jan9_collection_20260722T091933Z/final_audit.json"
JAN9_POSTPROMOTION_AUDIT_PATH = (
    "working/jan9_collection_20260722T091933Z/independent_postpromotion_audit.json"
)

PACKAGE_DIRECTORY = "working/premium_journals_v2_7_jan10_activation_review_v1"
PREIMAGE_PATH = f"{PACKAGE_DIRECTORY}/pre_activation_schedule.json"
PLAN_PATH = f"{PACKAGE_DIRECTORY}/activation_plan.json"
AUDIT_BUNDLE_PATH = f"{PACKAGE_DIRECTORY}/prepublication_audit_bundle.json"
MANIFEST_PATH = f"{PACKAGE_DIRECTORY}/review_package_manifest.json"
INDEPENDENT_AUDIT_PATH = f"{PACKAGE_DIRECTORY}/independent_prepublication_audit.json"
PUBLICATION_LOCK_PATH = "working/.premium_journals_v2_7_jan10_review_publication.lock"

PACKAGE_ARTIFACT_PATHS = (PREIMAGE_PATH, PLAN_PATH, AUDIT_BUNDLE_PATH, MANIFEST_PATH)
PACKAGE_PUBLICATION_ORDER = PACKAGE_ARTIFACT_PATHS

HISTORICAL_INPUT_BINDINGS = (
    (
        "historical_jan9_migration_candidate",
        "working/superseded_premium_journals_v2_7_jan9_activation_draft_v1/"
        "historical_inputs/working/premium_journals_v2_7_authority_migration_v1_candidate.json",
        "0637cdc6bbf0c3a49f110061cb718399ea81aaf694f0bc8f4754cba949ddd109",
        27986,
    ),
    (
        "historical_jan9_migration_readiness",
        "working/superseded_premium_journals_v2_7_jan9_activation_draft_v1/"
        "historical_inputs/working/premium_journals_v2_7_authority_migration_v1_readiness_report.json",
        "706c6781f660d6556b2475095eee9fe7f9cf1b567de4a2f379925a01ff1b48dc",
        2898,
    ),
    (
        "historical_jan9_independent_audit",
        "working/superseded_premium_journals_v2_7_jan9_activation_draft_v1/"
        "historical_inputs/working/premium_journals_v2_7_authority_migration_v1_independent_audit_report.json",
        "9fd5f3bfadf83ef004b29ab6efa0a47240567c2e0c5f10421e6e5dccaa9d0af0",
        13318,
    ),
)

PROTECTED_SOURCE_PATHS = (
    "../discord_browser_collector_v2_7.mjs",
    "../premium_v2_7_direct_parity_fixtures.json",
    "../test_discord_browser_collector_v2_7.mjs",
    "build_scoped_three_parent_schedule.py",
    "build_scoped_three_parent_schedule_v2_7.py",
    "docs/premium_journals_v2_7_authority_activation_v1.md",
    "docs/premium_journals_v2_7_authority_migration_v1.md",
    "docs/premium_journals_v2_7_jan10_activation_review_v1.md",
    "docs/premium_journals_v2_7_jan9_supersession_handoff.md",
    "premium_journals_attachment_accessory_contract_v2_7.py",
    "premium_journals_provenance_contract.py",
    "premium_journals_provenance_contract_v2_7.py",
    "premium_journals_system_event_timestamp_v1.py",
    "premium_journals_v2_7_authority_activation_v1.py",
    "premium_journals_v2_7_authority_migration_v1.py",
    "premium_journals_v2_7_integration.py",
    "premium_journals_v2_7_jan10_activation_review_v1.py",
    "premium_journals_v2_7_schedule.py",
    "qa/validate_premium_journals_v2_7.py",
    "qa/validate_premium_journals_v2_7_authority_activation_v1.py",
    "qa/validate_premium_journals_v2_7_authority_migration_v1.py",
    "qa/validate_premium_journals_v2_7_jan10_activation_review_v1.py",
    "reply_provenance_contract.py",
    "test_premium_journals_provenance_contract_v2_7.py",
    "test_premium_journals_v2_7_authority_activation_v1.py",
    "test_premium_journals_v2_7_authority_migration_v1.py",
    "test_premium_journals_v2_7_jan10_activation_review_v1.py",
    "test_validate_scoped_three_parent_schedule.py",
    "timestamp_scope_revalidation.py",
    "validate_scoped_three_parent_schedule.py",
    "validate_scoped_three_parent_schedule_v2_7.py",
)


def _sidecar(relative: str) -> str:
    path = Path(relative)
    return path.with_name(path.stem + v26.TIMESTAMP_SIDECAR_SUFFIX).as_posix()


JAN10_V25_CANONICAL = v26.expected_canonical_relative_path(DAY, DAY)
JAN10_V27_CANONICAL = v27.expected_canonical_relative_path(DAY, DAY)
JAN10_LEGACY_CANONICAL = (
    f"{v26.LEGACY_PRESERVATION_DIRECTORY}/channel_premium_journals_"
    f"{v26.PREMIUM_ID}_{DAY}_{DAY}.json"
)

TARGET_ABSENCE_PATHS = tuple(sorted({
    JAN10_V25_CANONICAL,
    JAN10_V25_CANONICAL.removesuffix(".json") + ".partial.json",
    _sidecar(JAN10_V25_CANONICAL),
    JAN10_V27_CANONICAL,
    JAN10_V27_CANONICAL.removesuffix(".json") + ".partial.json",
    _sidecar(JAN10_V27_CANONICAL),
    JAN10_LEGACY_CANONICAL,
    JAN10_LEGACY_CANONICAL.removesuffix(".json") + ".partial.json",
    _sidecar(JAN10_LEGACY_CANONICAL),
    v27.expected_checkpoint_relative_directory(DAY),
    "raw/premium_journals_v2_7_staging/2026-01-10",
    "working/premium_journals_v2_7_jan10_collection_stage.json",
    "working/premium_journals_v2_7_jan10_authority_activation_pre_schedule.json",
    "working/premium_journals_v2_7_jan10_authority_activation_plan.json",
    "working/premium_journals_v2_7_jan10_authority_activation_plan_independent_audit_report.json",
    "working/premium_journals_v2_7_jan10_authority_activation_receipt.json",
    "working/premium_journals_v2_7_jan10_authority_activation_projection_bundle.json",
    "working/premium_journals_v2_7_jan10_authority_activation_commit_marker.json",
    "working/premium_journals_v2_7_jan10_authority_activation_rollback_receipt.json",
    "working/.premium_journals_v2_7_jan10_authority_activation.lock",
    jan9_activation.PREIMAGE_PATH,
    jan9_activation.PLAN_PATH,
    jan9_activation.PLAN_AUDIT_PATH,
    jan9_activation.RECEIPT_PATH,
    jan9_activation.PROJECTION_BUNDLE_PATH,
    jan9_activation.COMMIT_MARKER_PATH,
    jan9_activation.ROLLBACK_RECEIPT_PATH,
    jan9_activation.LOCK_PATH,
    f"{PACKAGE_DIRECTORY}/activation_receipt.json",
    f"{PACKAGE_DIRECTORY}/commit_marker.json",
    f"{PACKAGE_DIRECTORY}/authority_marker.json",
    f"{jan9_activation.SUPERSEDED_DRAFT_DIRECTORY}/activation_receipt.json",
    f"{jan9_activation.SUPERSEDED_DRAFT_DIRECTORY}/commit_marker.json",
    f"{jan9_activation.SUPERSEDED_DRAFT_DIRECTORY}/authority_marker.json",
}))

TARGET_ABSENCE_PATTERNS = (
    "raw/quarantine_collection_errors/terra_premium_journals_daily_2026-01-10_*",
    "working/jan10_collection_*",
    "working/terra_premium_journals_daily_2026-01-10_*",
    "working/**/*premium*v2_7*marker*",
    "working/**/*v2_7*/**/*marker*",
)

FIVE_SAFETY_GATES = {
    "exclusive_os_publication_lock": True,
    "crash_safe_immutable_no_clobber_publication": True,
    "non_authoritative_reader_state_machine": True,
    "exact_snapshot_recovery_and_tamper_fail_closed": True,
    "marker_aware_no_write_validation": True,
}

EXPECTED_JAN10_SCHEDULE_ROUTE = {
    "route_id": "premium_journals_2026-01-10_2026-01-10",
    "channel_id": v26.PREMIUM_ID,
    "channel_name": v26.PREMIUM_NAME,
    "channel_kind": "forum channel",
    "start": DAY,
    "end": DAY,
    "query_prefix": "in:premium-journals",
    "query": QUERY,
    "expected_canonical_path": JAN10_V25_CANONICAL,
    "status": "pending_fresh_v2_6_capture",
    "scraping_owner": "GPT-5.6 Terra",
    "heavy_pagination_lane": "discord_account_heavy_lane_1",
    "forum_exact_navigation": {
        "required": True,
        "evidence_key": "exact_query+page_number+sorted_group_message_ids",
        "trigger": "unique_direct_child_role_button_click",
        "destination": "exact_/channels/<guild_id>/<thread_id>_URL",
        "same_query_page_group_back_return_required": True,
        "title_only_identity_allowed": False,
        "attachment_or_media_channel_identity_allowed": False,
    },
}


class ReviewPackageError(RuntimeError):
    pass


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(raw)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _normalized_relative(value: str, *, allow_parent: bool = False) -> bool:
    if not value or Path(value).is_absolute() or "\\" in value or posixpath.normpath(value) != value:
        return False
    if not allow_parent and (value == ".." or value.startswith("../")):
        return False
    return True


def resolve_corpus_path(root: Path, relative: str) -> Path:
    if not _normalized_relative(relative):
        raise ReviewPackageError(f"non-normalized corpus-relative path: {relative}")
    root = root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ReviewPackageError(f"path outside corpus root: {relative}") from exc
    return path


def resolve_source_path(root: Path, relative: str) -> Path:
    if relative not in PROTECTED_SOURCE_PATHS or not _normalized_relative(relative, allow_parent=True):
        raise ReviewPackageError(f"unapproved protected source path: {relative}")
    root = root.resolve()
    project = root.parent
    path = (root / relative).resolve()
    try:
        path.relative_to(project)
    except ValueError as exc:
        raise ReviewPackageError(f"protected source outside project root: {relative}") from exc
    return path


def _load_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ReviewPackageError(f"unreadable JSON {label}: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise ReviewPackageError(f"JSON object required: {label}")
    return value


def _binding(relative: str, role: str, raw: bytes) -> dict[str, Any]:
    return {"role": role, "path": relative, "sha256": sha256_bytes(raw), "bytes": len(raw)}


def _simple_binding(relative: str, raw: bytes) -> dict[str, Any]:
    return {"path": relative, "sha256": sha256_bytes(raw), "bytes": len(raw)}


def _finalize(record: dict[str, Any]) -> dict[str, Any]:
    record = dict(record)
    record["record_fingerprint_sha256"] = sha256_json(record)
    return record


def _fingerprint_valid(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    stripped = dict(record)
    observed = stripped.pop("record_fingerprint_sha256", None)
    return isinstance(observed, str) and observed == sha256_json(stripped)


def _read_optional(path: Path) -> bytes | None:
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError as exc:
        raise ReviewPackageError(f"cannot read {path}: {type(exc).__name__}") from exc


def _describe_path(root: Path, relative: str) -> dict[str, Any] | None:
    path = resolve_corpus_path(root, relative)
    if not path.exists():
        return None
    if path.is_file():
        raw = path.read_bytes()
        return {"kind": "file", "sha256": sha256_bytes(raw), "bytes": len(raw)}
    if path.is_dir():
        entries: list[dict[str, Any]] = []
        for item in sorted(path.rglob("*")):
            if not item.is_file():
                continue
            resolved = item.resolve()
            try:
                relative_item = resolved.relative_to(root.resolve()).as_posix()
            except ValueError as exc:
                raise ReviewPackageError(f"absence directory escapes corpus: {relative}") from exc
            raw = item.read_bytes()
            entries.append({"path": relative_item, "sha256": sha256_bytes(raw), "bytes": len(raw)})
        return {"kind": "directory", "files": entries}
    return {"kind": "other"}


def _pattern_matches(root: Path, pattern: str) -> list[dict[str, Any]]:
    if pattern not in TARGET_ABSENCE_PATTERNS:
        raise ReviewPackageError(f"unapproved absence pattern: {pattern}")
    matches: list[dict[str, Any]] = []
    for path in sorted(root.glob(pattern)):
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise ReviewPackageError(f"absence pattern escaped corpus: {pattern}") from exc
        matches.append({"path": relative, "description": _describe_path(root, relative)})
    return matches


def _package_inventory(root: Path) -> list[str]:
    directory = resolve_corpus_path(root, PACKAGE_DIRECTORY)
    if not directory.exists():
        return []
    if not directory.is_dir():
        return [PACKAGE_DIRECTORY]
    return [
        item.resolve().relative_to(root.resolve()).as_posix()
        for item in sorted(directory.rglob("*"))
        if item.is_file()
    ]


def capture_snapshot(root: Path) -> dict[str, Any]:
    """Capture every protected byte and absence predicate without taking a lock."""
    root = root.resolve()
    input_paths = {
        "schedule": SCHEDULE_PATH,
        "jan9_supersession_manifest": JAN9_SUPERSESSION_MANIFEST_PATH,
        "jan9_supersession_archive_lock": JAN9_SUPERSESSION_ARCHIVE_LOCK_PATH,
        "query_checklist": QUERY_CHECKLIST_PATH,
        "jan9_canonical": JAN9_CANONICAL_PATH,
        "jan9_final_audit": JAN9_FINAL_AUDIT_PATH,
        "jan9_postpromotion_audit": JAN9_POSTPROMOTION_AUDIT_PATH,
        **{role: path for role, path, _digest, _size in HISTORICAL_INPUT_BINDINGS},
    }
    inputs = {label: _read_optional(resolve_corpus_path(root, relative)) for label, relative in input_paths.items()}
    sources = {relative: _read_optional(resolve_source_path(root, relative)) for relative in PROTECTED_SOURCE_PATHS}
    package = {
        relative: _read_optional(resolve_corpus_path(root, relative))
        for relative in (*PACKAGE_ARTIFACT_PATHS, INDEPENDENT_AUDIT_PATH)
    }
    absence = {
        "exact": {relative: _describe_path(root, relative) for relative in TARGET_ABSENCE_PATHS},
        "patterns": {pattern: _pattern_matches(root, pattern) for pattern in TARGET_ABSENCE_PATTERNS},
    }
    return {
        "inputs": inputs,
        "sources": sources,
        "package": package,
        "absence": absence,
        "package_inventory": _package_inventory(root),
    }


def _raw_signature(raw: bytes | None) -> Any:
    return None if raw is None else {"sha256": sha256_bytes(raw), "bytes": len(raw)}


def snapshot_signature(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "inputs": {key: _raw_signature(value) for key, value in snapshot["inputs"].items()},
        "sources": {key: _raw_signature(value) for key, value in snapshot["sources"].items()},
        "package": {key: _raw_signature(value) for key, value in snapshot["package"].items()},
        "absence": snapshot["absence"],
        "package_inventory": snapshot["package_inventory"],
    }


def _route(schedule: dict[str, Any], day: str) -> dict[str, Any]:
    routes = schedule.get("routes", {}).get("premium_journals", [])
    matches = [item for item in routes if isinstance(item, dict) and item.get("start") == day and item.get("end") == day]
    if len(matches) != 1:
        raise ReviewPackageError(f"exactly one Premium route required for {day}")
    return matches[0]


def _validate_checklist(checklist: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if checklist.get("schema_version") != "1.0.0" or checklist.get("artifact_type") != "premium_journals_jan10_query_timing_checklist":
        errors.append("query_checklist_identity_invalid")
    if checklist.get("status") != "HOLD_PENDING_V2_7_INDEPENDENT_AUDIT_AND_ACTIVATION":
        errors.append("query_checklist_not_hold")
    if checklist.get("checklist_only") is not True or checklist.get("authoritative_collection_artifact") is not False:
        errors.append("query_checklist_authority_flags_invalid")
    target = checklist.get("target")
    expected_target = {
        "day": DAY,
        "channel_name": v26.PREMIUM_NAME,
        "parent_forum_channel_id": v26.PREMIUM_ID,
        "exact_query": QUERY,
        "timezone": TIMEZONE,
        "utc_offset_on_target_date": "-06:00",
        "local_window": {
            "start_inclusive": "2026-01-10T00:00:00-06:00",
            "end_exclusive": "2026-01-11T00:00:00-06:00",
        },
        "utc_window": {
            "start_inclusive": "2026-01-10T06:00:00Z",
            "end_exclusive": "2026-01-11T06:00:00Z",
        },
    }
    if target != expected_target:
        errors.append("query_checklist_target_invalid")
    prerequisites = checklist.get("hard_prerequisites")
    if not isinstance(prerequisites, list) or len(prerequisites) != 3:
        errors.append("query_checklist_prerequisites_invalid")
    else:
        expected_states = ["PENDING", "PENDING", "NOT_EVALUATED_BECAUSE_ACTIVATION_IS_PENDING"]
        if [item.get("current_state") for item in prerequisites if isinstance(item, dict)] != expected_states:
            errors.append("query_checklist_prerequisite_state_invalid")
    timing = checklist.get("submission_timing", {})
    if timing.get("minimum_spacing_seconds") != 60 or timing.get("not_before_utc") is not None or timing.get("query_submission_count_authorized_now") != 0:
        errors.append("query_checklist_submission_timing_invalid")
    effects = checklist.get("preparation_side_effects")
    if not isinstance(effects, dict) or set(effects.values()) != {False}:
        errors.append("query_checklist_side_effects_invalid")
    terminal = checklist.get("current_terminal_jan9_state", {})
    if terminal.get("read_only") is not True:
        errors.append("query_checklist_jan9_not_read_only")
    if terminal.get("schedule") != {"path": SCHEDULE_PATH, "sha256": SCHEDULE_SHA256, "bytes": SCHEDULE_BYTES}:
        errors.append("query_checklist_schedule_binding_invalid")
    return errors


def validate_input_snapshot(snapshot: dict[str, Any], root: Path, *, require_audit_absent: bool = False) -> list[str]:
    errors: list[str] = []
    inputs = snapshot["inputs"]
    schedule_raw = inputs.get("schedule")
    if schedule_raw is None or (sha256_bytes(schedule_raw), len(schedule_raw)) != (SCHEDULE_SHA256, SCHEDULE_BYTES):
        errors.append("live_schedule_binding_invalid")
        schedule = {}
    else:
        try:
            schedule = _load_object(schedule_raw, SCHEDULE_PATH)
        except ReviewPackageError:
            schedule = {}
            errors.append("live_schedule_json_invalid")
    if schedule:
        if schedule.get("guild_id") != v26.GUILD_ID or schedule.get("window", {}).get("timezone") != TIMEZONE:
            errors.append("live_schedule_scope_invalid")
        try:
            jan10 = _route(schedule, DAY)
            if jan10 != EXPECTED_JAN10_SCHEDULE_ROUTE:
                errors.append("jan10_schedule_route_invalid")
            jan9 = _route(schedule, "2026-01-09")
            accepted = jan9.get("accepted_artifact", {})
            if not (
                jan9.get("status") == "complete_accepted_v2_6"
                and jan9.get("expected_canonical_path") == JAN9_CANONICAL_PATH
                and accepted.get("path") == JAN9_CANONICAL_PATH
                and accepted.get("collector_version") == "2.6"
                and accepted.get("full_qa_passed") is True
            ):
                errors.append("jan9_schedule_authority_not_exact_v2_6")
            jan9_raw = inputs.get("jan9_canonical")
            if jan9_raw is None or (accepted.get("sha256"), accepted.get("bytes")) != (sha256_bytes(jan9_raw), len(jan9_raw)):
                errors.append("jan9_canonical_binding_invalid")
        except ReviewPackageError as exc:
            errors.append(f"schedule_route_invalid:{exc}")
    manifest_raw = inputs.get("jan9_supersession_manifest")
    if manifest_raw is None or (sha256_bytes(manifest_raw), len(manifest_raw)) != (
        JAN9_SUPERSESSION_MANIFEST_SHA256, JAN9_SUPERSESSION_MANIFEST_BYTES
    ):
        errors.append("jan9_supersession_manifest_binding_invalid")
    else:
        try:
            manifest = _load_object(manifest_raw, JAN9_SUPERSESSION_MANIFEST_PATH)
            if manifest.get("record_fingerprint_sha256") != JAN9_SUPERSESSION_RECORD_FINGERPRINT:
                errors.append("jan9_supersession_fingerprint_invalid")
            if manifest.get("jan9_v2_7_authority") is not False or manifest.get("jan9_authority") != "v2.6_schedule_only":
                errors.append("jan9_supersession_authority_invalid")
            if manifest.get("first_future_activation_target", {}).get("day") != DAY:
                errors.append("jan9_supersession_future_target_invalid")
            manifest_errors = jan9_activation.validate_supersession_manifest(manifest, root)
            errors.extend(f"jan9_supersession:{item}" for item in manifest_errors)
        except (ReviewPackageError, jan9_activation.ActivationError) as exc:
            errors.append(f"jan9_supersession_invalid:{exc}")
    archive_lock_raw = inputs.get("jan9_supersession_archive_lock")
    if archive_lock_raw is None or (sha256_bytes(archive_lock_raw), len(archive_lock_raw)) != (
        JAN9_SUPERSESSION_ARCHIVE_LOCK_SHA256, JAN9_SUPERSESSION_ARCHIVE_LOCK_BYTES
    ):
        errors.append("jan9_supersession_archive_lock_binding_invalid")
    checklist_raw = inputs.get("query_checklist")
    if checklist_raw is None or (sha256_bytes(checklist_raw), len(checklist_raw)) != (QUERY_CHECKLIST_SHA256, QUERY_CHECKLIST_BYTES):
        errors.append("query_checklist_binding_invalid")
    else:
        try:
            checklist = _load_object(checklist_raw, QUERY_CHECKLIST_PATH)
            errors.extend(_validate_checklist(checklist))
            terminal = checklist.get("current_terminal_jan9_state", {})
            for label, key, path in (
                ("jan9_final_audit", "publisher_final_audit", JAN9_FINAL_AUDIT_PATH),
                ("jan9_postpromotion_audit", "independent_postpromotion_audit", JAN9_POSTPROMOTION_AUDIT_PATH),
            ):
                raw = inputs.get(label)
                record = terminal.get(key, {})
                if raw is None or record != {"path": path, "sha256": sha256_bytes(raw), "bytes": len(raw), "status": "PASS"}:
                    errors.append(f"query_checklist_{label}_binding_invalid")
                else:
                    terminal_audit = _load_object(raw, path)
                    observed_status = (
                        terminal_audit.get("status")
                        if label == "jan9_final_audit"
                        else terminal_audit.get("verdict", {}).get("status")
                    )
                    if observed_status != "PASS":
                        errors.append(f"{label}_not_pass")
        except ReviewPackageError as exc:
            errors.append(f"query_checklist_invalid:{exc}")
    for role, path, digest, size in HISTORICAL_INPUT_BINDINGS:
        raw = inputs.get(role)
        if raw is None or (sha256_bytes(raw), len(raw)) != (digest, size):
            errors.append(f"{role}_binding_invalid")
    if not errors:
        candidate = _load_object(inputs["historical_jan9_migration_candidate"], "historical candidate")
        readiness = _load_object(inputs["historical_jan9_migration_readiness"], "historical readiness")
        audit = _load_object(inputs["historical_jan9_independent_audit"], "historical audit")
        if candidate.get("authority_scope", {}).get("start") != "2026-01-09" or candidate.get("activation_controls", {}).get("activation_allowed") is not False:
            errors.append("historical_candidate_not_disabled_jan9_only")
        if readiness.get("status") != "candidate_ready_for_independent_audit_not_activation":
            errors.append("historical_readiness_status_invalid")
        if audit.get("status") != "PASS" or audit.get("decision_scope", "").find("does not") < 0:
            errors.append("historical_audit_scope_invalid")
    missing_sources = [relative for relative, raw in snapshot["sources"].items() if raw is None]
    errors.extend(f"protected_source_missing:{relative}" for relative in missing_sources)
    for relative, description in snapshot["absence"]["exact"].items():
        if description is not None:
            errors.append(f"target_or_authority_artifact_present:{relative}")
    for pattern, matches in snapshot["absence"]["patterns"].items():
        if matches:
            errors.append(f"collection_stage_pattern_present:{pattern}")
    if require_audit_absent and snapshot["package"].get(INDEPENDENT_AUDIT_PATH) is not None:
        errors.append("independent_audit_preexists_package_publication")
    return sorted(set(errors))


def _source_bindings(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _binding(relative, "protected_code_test_or_schedule_implementation", snapshot["sources"][relative])
        for relative in sorted(PROTECTED_SOURCE_PATHS)
    ]


def build_plan(snapshot: dict[str, Any]) -> dict[str, Any]:
    schedule = _load_object(snapshot["inputs"]["schedule"], SCHEDULE_PATH)
    jan9 = _route(schedule, "2026-01-09")
    checklist = _load_object(snapshot["inputs"]["query_checklist"], QUERY_CHECKLIST_PATH)
    sources = _source_bindings(snapshot)
    proposed = {
        "route_id": "premium_journals_v2_7_2026-01-10_2026-01-10",
        "start": DAY,
        "end": DAY,
        "query": QUERY,
        "timezone": TIMEZONE,
        "collector_version": "2.7",
        "provenance_version": "2.7",
        "v2_7_explicit_opt_in": True,
        "expected_canonical_path": JAN10_V27_CANONICAL,
        "expected_checkpoint_directory": v27.expected_checkpoint_relative_directory(DAY),
        "live_collection_enabled": False,
        "canonical_authority_enabled": False,
        "promotion_allowed": False,
        "status": "disabled_pending_independent_prepublication_audit",
    }
    route_validation_view = {key: proposed[key] for key in (
        "start", "end", "collector_version", "provenance_version", "v2_7_explicit_opt_in",
        "expected_canonical_path", "live_collection_enabled",
    )}
    if v27.validate_explicit_v2_7_route(route_validation_view):
        raise ReviewPackageError("internal proposed v2.7 route is invalid")
    record = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_jan10_activation_review_plan",
        "package_id": PACKAGE_ID,
        "status": "ready_for_independent_prepublication_audit_not_activation",
        "prepared_at_utc": PACKAGE_PREPARED_AT_UTC,
        "immutable": True,
        "append_only": True,
        "authority_effect": "none_review_evidence_only",
        "target": {
            "day": DAY,
            "timezone": TIMEZONE,
            "exact_query": QUERY,
            "guild_id": v26.GUILD_ID,
            "parent_forum_channel_id": v26.PREMIUM_ID,
            "parent_forum_channel_name": v26.PREMIUM_NAME,
            "source_schedule_route": EXPECTED_JAN10_SCHEDULE_ROUTE,
            "source_schedule_route_sha256": sha256_json(EXPECTED_JAN10_SCHEDULE_ROUTE),
            "proposed_v2_7_route": proposed,
        },
        "frozen_preconditions": {
            "live_schedule": _binding(SCHEDULE_PATH, "exact_pre_activation_schedule", snapshot["inputs"]["schedule"]),
            "jan9_supersession_manifest": _binding(
                JAN9_SUPERSESSION_MANIFEST_PATH, "frozen_jan9_supersession_manifest",
                snapshot["inputs"]["jan9_supersession_manifest"],
            ),
            "jan9_supersession_archive_lock": _binding(
                JAN9_SUPERSESSION_ARCHIVE_LOCK_PATH, "inert_os_released_supersession_archive_lock",
                snapshot["inputs"]["jan9_supersession_archive_lock"],
            ),
            "jan10_query_timing_checklist": _binding(
                QUERY_CHECKLIST_PATH, "hold_only_query_timing_checklist", snapshot["inputs"]["query_checklist"]
            ),
            "jan9_terminal_v2_6_route_sha256": sha256_json(jan9),
            "jan9_authoritative_canonical": _binding(
                JAN9_CANONICAL_PATH, "jan9_v2_6_authoritative_canonical", snapshot["inputs"]["jan9_canonical"]
            ),
            "jan9_publisher_final_audit": _binding(
                JAN9_FINAL_AUDIT_PATH, "jan9_publisher_final_audit", snapshot["inputs"]["jan9_final_audit"]
            ),
            "jan9_independent_postpromotion_audit": _binding(
                JAN9_POSTPROMOTION_AUDIT_PATH, "jan9_independent_postpromotion_audit",
                snapshot["inputs"]["jan9_postpromotion_audit"],
            ),
            "historical_jan9_capability_inputs": [
                _binding(path, role, snapshot["inputs"][role])
                for role, path, _digest, _size in HISTORICAL_INPUT_BINDINGS
            ],
            "historical_inputs_confer_jan10_authority": False,
            "jan9_authority_inherited": False,
        },
        "protected_source_bindings": sources,
        "protected_source_set_sha256": sha256_json(sources),
        "target_absence_contract": {
            "exact_paths": list(TARGET_ABSENCE_PATHS),
            "glob_patterns": list(TARGET_ABSENCE_PATTERNS),
            "all_absent_at_publication": True,
            "recheck_before_every_future_authority_decision": True,
        },
        "five_safety_gates": FIVE_SAFETY_GATES,
        "activation_controls": {
            "independent_audit_passed": False,
            "activation_authorized": False,
            "activation_receipt_present": False,
            "commit_marker_present": False,
            "live_collection_enabled": False,
            "canonical_write_enabled": False,
            "promotion_allowed": False,
            "schedule_write_enabled": False,
            "query_submission_authorized": False,
        },
        "external_independent_audit": {
            "path": INDEPENDENT_AUDIT_PATH,
            "required_before_any_separate_future_activation_design": True,
            "present_at_package_publication": False,
            "must_bind_exact_plan_preimage_audit_bundle_and_manifest": True,
            "audit_alone_confers_authority": False,
        },
        "no_write_contract": {
            "schedule_mutation_performed": False,
            "canonical_or_partial_written": False,
            "checkpoint_or_collection_stage_written": False,
            "activation_marker_written": False,
            "discord_query_submitted": False,
            "collector_invoked": False,
            "only_review_package_files_may_be_published": list(PACKAGE_ARTIFACT_PATHS),
        },
        "query_checklist_created_at_utc": checklist.get("created_at_utc"),
    }
    return _finalize(record)


def build_audit_bundle(snapshot: dict[str, Any], plan_raw: bytes) -> dict[str, Any]:
    plan = _load_object(plan_raw, PLAN_PATH)
    sources = _source_bindings(snapshot)
    record = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_jan10_prepublication_audit_bundle",
        "package_id": PACKAGE_ID,
        "status": "READY_FOR_INDEPENDENT_AUDIT_NOT_AUDITED",
        "prepared_at_utc": PACKAGE_PREPARED_AT_UTC,
        "immutable": True,
        "append_only": True,
        "independent_audit_passed": False,
        "activation_authorized": False,
        "authority_effect": "none",
        "bound_artifacts": {
            "plan": _binding(PLAN_PATH, "disabled_activation_review_plan", plan_raw),
            "pre_activation_schedule": _binding(
                PREIMAGE_PATH, "exact_raw_schedule_preimage", snapshot["inputs"]["schedule"]
            ),
            "live_schedule_at_packaging": _binding(
                SCHEDULE_PATH, "exact_live_schedule_at_packaging", snapshot["inputs"]["schedule"]
            ),
            "jan9_supersession_manifest": _binding(
                JAN9_SUPERSESSION_MANIFEST_PATH, "frozen_jan9_supersession_manifest",
                snapshot["inputs"]["jan9_supersession_manifest"],
            ),
            "jan9_supersession_archive_lock": _binding(
                JAN9_SUPERSESSION_ARCHIVE_LOCK_PATH, "inert_os_released_supersession_archive_lock",
                snapshot["inputs"]["jan9_supersession_archive_lock"],
            ),
            "jan10_query_timing_checklist": _binding(
                QUERY_CHECKLIST_PATH, "hold_only_query_timing_checklist", snapshot["inputs"]["query_checklist"]
            ),
        },
        "plan_record_fingerprint_sha256": plan.get("record_fingerprint_sha256"),
        "protected_source_bindings": sources,
        "protected_source_set_sha256": sha256_json(sources),
        "auditor_rederivation_requirements": [
            "recompute_every_file_sha256_and_byte_count",
            "recompute_all_record_fingerprints",
            "validate_exact_jan10_route_query_timezone_and_disabled_v2_7_projection",
            "validate_jan9_remains_v2_6_only_and_never_inherited",
            "validate_all_exact_and_pattern_absence_predicates",
            "validate_all_five_safety_gates",
            "run_activation_recovery_and_schedule_regression_suites",
            "confirm_no_schedule_canonical_checkpoint_stage_receipt_or_marker_write",
        ],
        "five_safety_gates": FIVE_SAFETY_GATES,
        "reader_expected_states": {
            "no_package": "PRE_ACTIVATION",
            "exact_prefix_only": "FAIL_CLOSED_RECOVERY_REQUIRED",
            "complete_exact_package": "REVIEW_PACKAGE_READY",
            "valid_external_audit": "INDEPENDENT_AUDIT_PASSED_NO_AUTHORITY",
            "tamper_drift_or_marker": "FAIL_CLOSED",
        },
        "external_audit_path": INDEPENDENT_AUDIT_PATH,
        "external_audit_missing_is_expected_before_review": True,
        "external_audit_must_never_resolve_live_authority": True,
    }
    return _finalize(record)


def build_manifest(snapshot: dict[str, Any], plan_raw: bytes, audit_bundle_raw: bytes) -> dict[str, Any]:
    package_bindings = [
        _binding(PREIMAGE_PATH, "exact_raw_schedule_preimage", snapshot["inputs"]["schedule"]),
        _binding(PLAN_PATH, "disabled_activation_review_plan", plan_raw),
        _binding(AUDIT_BUNDLE_PATH, "prepublication_audit_bundle", audit_bundle_raw),
    ]
    sources = _source_bindings(snapshot)
    record = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_jan10_review_package_manifest",
        "package_id": PACKAGE_ID,
        "status": "PUBLISHED_IMMUTABLE_REVIEW_PACKAGE_NOT_AUTHORITY",
        "prepared_at_utc": PACKAGE_PREPARED_AT_UTC,
        "immutable": True,
        "append_only": True,
        "publication_complete": True,
        "ready_for_independent_audit": True,
        "independent_audit_passed": False,
        "activation_authorized": False,
        "live_collection_enabled": False,
        "authority_effect": "none_review_package_only",
        "target_day": DAY,
        "package_artifacts": package_bindings,
        "package_artifact_set_sha256": sha256_json(package_bindings),
        "protected_source_bindings": sources,
        "protected_source_set_sha256": sha256_json(sources),
        "frozen_inputs": {
            "live_schedule": _simple_binding(SCHEDULE_PATH, snapshot["inputs"]["schedule"]),
            "jan9_supersession_manifest": _simple_binding(
                JAN9_SUPERSESSION_MANIFEST_PATH, snapshot["inputs"]["jan9_supersession_manifest"]
            ),
            "jan9_supersession_archive_lock": _simple_binding(
                JAN9_SUPERSESSION_ARCHIVE_LOCK_PATH, snapshot["inputs"]["jan9_supersession_archive_lock"]
            ),
            "jan10_query_timing_checklist": _simple_binding(
                QUERY_CHECKLIST_PATH, snapshot["inputs"]["query_checklist"]
            ),
        },
        "five_safety_gates": FIVE_SAFETY_GATES,
        "reader_contract": {
            "review_package_ready_is_authoritative": False,
            "review_package_ready_resolves_live_route": False,
            "independent_audit_pass_alone_is_authoritative": False,
            "missing_invalid_or_tampered_inputs_fail_closed": True,
            "reader_is_lock_free_and_write_free": True,
            "reader_compares_protected_snapshot_before_and_after": True,
        },
        "external_independent_audit": {
            "path": INDEPENDENT_AUDIT_PATH,
            "present_at_publication": False,
            "append_only_separate_artifact": True,
            "must_bind_exact_package_file_hashes_and_bytes": True,
            "must_report_all_five_gates_pass": True,
            "authority_effect_even_if_pass": "none_requires_separate_future_activation_chain",
        },
        "reserved_active_artifacts_absent": list(TARGET_ABSENCE_PATHS),
        "collection_stage_patterns_absent": list(TARGET_ABSENCE_PATTERNS),
        "jan9_authority": "v2.6_schedule_only",
        "jan9_v2_7_authority": False,
        "jan9_authority_inherited": False,
    }
    return _finalize(record)


def expected_package_bytes(snapshot: dict[str, Any]) -> dict[str, bytes]:
    plan_raw = json_bytes(build_plan(snapshot))
    audit_raw = json_bytes(build_audit_bundle(snapshot, plan_raw))
    manifest_raw = json_bytes(build_manifest(snapshot, plan_raw, audit_raw))
    return {
        PREIMAGE_PATH: snapshot["inputs"]["schedule"],
        PLAN_PATH: plan_raw,
        AUDIT_BUNDLE_PATH: audit_raw,
        MANIFEST_PATH: manifest_raw,
    }


def _allowed_inventory() -> set[str]:
    return {*PACKAGE_ARTIFACT_PATHS, INDEPENDENT_AUDIT_PATH}


def validate_package_snapshot(snapshot: dict[str, Any], root: Path) -> list[str]:
    errors = validate_input_snapshot(snapshot, root)
    unexpected = sorted(set(snapshot["package_inventory"]) - _allowed_inventory())
    errors.extend(f"unexpected_package_file:{item}" for item in unexpected)
    try:
        expected = expected_package_bytes(snapshot) if not errors else {}
    except ReviewPackageError as exc:
        errors.append(f"expected_package_derivation_failed:{exc}")
        expected = {}
    for relative in PACKAGE_ARTIFACT_PATHS:
        raw = snapshot["package"].get(relative)
        if raw is None:
            errors.append(f"package_artifact_missing:{relative}")
        elif relative in expected and raw != expected[relative]:
            errors.append(f"package_artifact_tampered:{relative}")
    manifest_raw = snapshot["package"].get(MANIFEST_PATH)
    if manifest_raw is not None:
        try:
            manifest = _load_object(manifest_raw, MANIFEST_PATH)
            if not _fingerprint_valid(manifest):
                errors.append("review_manifest_fingerprint_invalid")
            if manifest.get("five_safety_gates") != FIVE_SAFETY_GATES or not all(manifest.get("five_safety_gates", {}).values()):
                errors.append("review_manifest_five_gates_invalid")
            if manifest.get("activation_authorized") is not False or manifest.get("authority_effect") != "none_review_package_only":
                errors.append("review_manifest_authority_flags_invalid")
        except ReviewPackageError as exc:
            errors.append(f"review_manifest_invalid:{exc}")
    return sorted(set(errors))


def _independent_audit_expected_bindings(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative in PACKAGE_ARTIFACT_PATHS:
        raw = snapshot["package"].get(relative)
        if raw is None:
            raise ReviewPackageError(f"independent audit cannot bind missing package artifact: {relative}")
        records.append(_simple_binding(relative, raw))
    return records


def _independent_audit_expected_inputs(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "live_schedule": _simple_binding(SCHEDULE_PATH, snapshot["inputs"]["schedule"]),
        "jan9_supersession_manifest": _simple_binding(
            JAN9_SUPERSESSION_MANIFEST_PATH, snapshot["inputs"]["jan9_supersession_manifest"]
        ),
        "jan9_supersession_archive_lock": _simple_binding(
            JAN9_SUPERSESSION_ARCHIVE_LOCK_PATH, snapshot["inputs"]["jan9_supersession_archive_lock"]
        ),
        "jan10_query_timing_checklist": _simple_binding(
            QUERY_CHECKLIST_PATH, snapshot["inputs"]["query_checklist"]
        ),
        "jan9_authoritative_canonical": _simple_binding(
            JAN9_CANONICAL_PATH, snapshot["inputs"]["jan9_canonical"]
        ),
        "jan9_publisher_final_audit": _simple_binding(
            JAN9_FINAL_AUDIT_PATH, snapshot["inputs"]["jan9_final_audit"]
        ),
        "jan9_independent_postpromotion_audit": _simple_binding(
            JAN9_POSTPROMOTION_AUDIT_PATH, snapshot["inputs"]["jan9_postpromotion_audit"]
        ),
        "historical_jan9_capability_inputs": [
            _simple_binding(path, snapshot["inputs"][role])
            for role, path, _digest, _size in HISTORICAL_INPUT_BINDINGS
        ],
    }


def _pre_audit_signature(snapshot: dict[str, Any]) -> dict[str, Any]:
    signature = snapshot_signature(snapshot)
    signature["package"][INDEPENDENT_AUDIT_PATH] = None
    signature["package_inventory"] = [
        item for item in signature["package_inventory"] if item != INDEPENDENT_AUDIT_PATH
    ]
    return signature


def _valid_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True


def validate_independent_audit(snapshot: dict[str, Any]) -> list[str]:
    raw = snapshot["package"].get(INDEPENDENT_AUDIT_PATH)
    if raw is None:
        return ["independent_audit_missing"]
    try:
        audit = _load_object(raw, INDEPENDENT_AUDIT_PATH)
    except ReviewPackageError as exc:
        return [f"independent_audit_invalid:{exc}"]
    expected_keys = {
        "schema_version", "artifact_type", "audit_id", "package_id", "status", "audited_at_utc",
        "immutable", "append_only", "audit_method", "auditor_independent_of_package_author", "blockers",
        "bound_package_artifacts", "rederived_frozen_inputs", "rederived_protected_source_bindings",
        "protected_source_set_sha256", "test_results", "schedule_validation", "read_only_replay",
        "absence_validation", "five_safety_gates", "five_safety_gate_determinations",
        "activation_authorized", "authority_effect", "record_fingerprint_sha256",
    }
    errors: list[str] = []
    if set(audit) != expected_keys:
        errors.append("independent_audit_schema_invalid")
    if audit.get("schema_version") != SCHEMA_VERSION or audit.get("artifact_type") != "premium_journals_v2_7_jan10_independent_prepublication_audit":
        errors.append("independent_audit_identity_invalid")
    if audit.get("package_id") != PACKAGE_ID or audit.get("status") != "PASS" or audit.get("blockers") != []:
        errors.append("independent_audit_decision_invalid")
    if not _valid_utc_timestamp(audit.get("audited_at_utc")):
        errors.append("independent_audit_timestamp_invalid")
    if audit.get("immutable") is not True or audit.get("append_only") is not True or audit.get("auditor_independent_of_package_author") is not True:
        errors.append("independent_audit_independence_flags_invalid")
    if audit.get("audit_method") != "independent_read_only_rederivation_and_test_execution":
        errors.append("independent_audit_method_invalid")
    try:
        if audit.get("bound_package_artifacts") != _independent_audit_expected_bindings(snapshot):
            errors.append("independent_audit_package_bindings_invalid")
    except ReviewPackageError as exc:
        errors.append(f"independent_audit_binding_invalid:{exc}")
    if audit.get("rederived_frozen_inputs") != _independent_audit_expected_inputs(snapshot):
        errors.append("independent_audit_frozen_input_bindings_invalid")
    expected_sources = _source_bindings(snapshot)
    if audit.get("rederived_protected_source_bindings") != expected_sources:
        errors.append("independent_audit_source_bindings_invalid")
    if audit.get("protected_source_set_sha256") != sha256_json(expected_sources):
        errors.append("independent_audit_source_set_invalid")
    tests = audit.get("test_results")
    required_suites = {
        "jan10_activation_review_python": 25,
        "generic_activation_recovery_python": 42,
        "schedule_regression_python": 17,
        "v2_7_collector_node": 9,
        "v2_7_provenance_python": 9,
    }
    if not isinstance(tests, dict) or set(tests) != set(required_suites):
        errors.append("independent_audit_test_suite_set_invalid")
    else:
        for suite, minimum_passed in required_suites.items():
            result = tests.get(suite)
            if not isinstance(result, dict) or set(result) != {"command", "passed", "failed", "exit_code"}:
                errors.append(f"independent_audit_test_result_schema_invalid:{suite}")
                continue
            if (
                not isinstance(result.get("command"), str) or not result.get("command")
                or type(result.get("passed")) is not int or result.get("passed", 0) < minimum_passed
                or result.get("failed") != 0 or result.get("exit_code") != 0
            ):
                errors.append(f"independent_audit_test_result_invalid:{suite}")
    expected_schedule_validation = {
        "status": "PASS",
        "errors": [],
        "schedule": _simple_binding(SCHEDULE_PATH, snapshot["inputs"]["schedule"]),
    }
    if audit.get("schedule_validation") != expected_schedule_validation:
        errors.append("independent_audit_schedule_validation_invalid")
    replay = audit.get("read_only_replay")
    expected_signature_sha = sha256_json(_pre_audit_signature(snapshot))
    if not isinstance(replay, dict) or set(replay) != {
        "before_signature_sha256", "after_signature_sha256", "unchanged", "writes_performed",
        "locks_acquired", "reader_status",
    }:
        errors.append("independent_audit_replay_schema_invalid")
    elif replay != {
        "before_signature_sha256": expected_signature_sha,
        "after_signature_sha256": expected_signature_sha,
        "unchanged": True,
        "writes_performed": False,
        "locks_acquired": False,
        "reader_status": "REVIEW_PACKAGE_READY",
    }:
        errors.append("independent_audit_replay_invalid")
    if audit.get("absence_validation") != {
        "status": "PASS",
        "exact_paths": list(TARGET_ABSENCE_PATHS),
        "glob_patterns": list(TARGET_ABSENCE_PATTERNS),
        "unexpected_matches": [],
    }:
        errors.append("independent_audit_absence_validation_invalid")
    if audit.get("five_safety_gates") != FIVE_SAFETY_GATES or not all(audit.get("five_safety_gates", {}).values()):
        errors.append("independent_audit_five_gates_invalid")
    determinations = audit.get("five_safety_gate_determinations")
    if not isinstance(determinations, dict) or set(determinations) != set(FIVE_SAFETY_GATES):
        errors.append("independent_audit_gate_determination_set_invalid")
    else:
        for gate, determination in determinations.items():
            if (
                not isinstance(determination, dict)
                or set(determination) != {"status", "evidence"}
                or determination.get("status") != "PASS"
                or not isinstance(determination.get("evidence"), list)
                or not determination.get("evidence")
                or not all(isinstance(item, str) and item for item in determination["evidence"])
            ):
                errors.append(f"independent_audit_gate_determination_invalid:{gate}")
    if audit.get("activation_authorized") is not False or audit.get("authority_effect") != "none_audit_does_not_activate":
        errors.append("independent_audit_authority_flags_invalid")
    if not _fingerprint_valid(audit):
        errors.append("independent_audit_fingerprint_invalid")
    return sorted(set(errors))


def _build_independent_audit_fixture(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Private test helper; never evidence of an actual independent review."""
    return _finalize({
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_jan10_independent_prepublication_audit",
        "audit_id": f"{PACKAGE_ID}-fixture-independent-audit",
        "package_id": PACKAGE_ID,
        "status": "PASS",
        "audited_at_utc": "2026-07-22T10:30:00Z",
        "immutable": True,
        "append_only": True,
        "audit_method": "independent_read_only_rederivation_and_test_execution",
        "auditor_independent_of_package_author": True,
        "blockers": [],
        "bound_package_artifacts": _independent_audit_expected_bindings(snapshot),
        "rederived_frozen_inputs": _independent_audit_expected_inputs(snapshot),
        "rederived_protected_source_bindings": _source_bindings(snapshot),
        "protected_source_set_sha256": sha256_json(_source_bindings(snapshot)),
        "test_results": {
            "jan10_activation_review_python": {
                "command": "python -m unittest test_premium_journals_v2_7_jan10_activation_review_v1 -v",
                "passed": 25, "failed": 0, "exit_code": 0,
            },
            "generic_activation_recovery_python": {
                "command": "python -m unittest test_premium_journals_v2_7_authority_activation_v1 -v",
                "passed": 42, "failed": 0, "exit_code": 0,
            },
            "schedule_regression_python": {
                "command": "python -m unittest test_validate_scoped_three_parent_schedule -v",
                "passed": 17, "failed": 0, "exit_code": 0,
            },
            "v2_7_collector_node": {
                "command": "node --test test_discord_browser_collector_v2_7.mjs",
                "passed": 9, "failed": 0, "exit_code": 0,
            },
            "v2_7_provenance_python": {
                "command": "python -m unittest test_premium_journals_provenance_contract_v2_7 -v",
                "passed": 9, "failed": 0, "exit_code": 0,
            },
        },
        "schedule_validation": {
            "status": "PASS", "errors": [],
            "schedule": _simple_binding(SCHEDULE_PATH, snapshot["inputs"]["schedule"]),
        },
        "read_only_replay": {
            "before_signature_sha256": sha256_json(_pre_audit_signature(snapshot)),
            "after_signature_sha256": sha256_json(_pre_audit_signature(snapshot)),
            "unchanged": True, "writes_performed": False, "locks_acquired": False,
            "reader_status": "REVIEW_PACKAGE_READY",
        },
        "absence_validation": {
            "status": "PASS", "exact_paths": list(TARGET_ABSENCE_PATHS),
            "glob_patterns": list(TARGET_ABSENCE_PATTERNS), "unexpected_matches": [],
        },
        "five_safety_gates": FIVE_SAFETY_GATES,
        "five_safety_gate_determinations": {
            gate: {"status": "PASS", "evidence": [f"fixture evidence for {gate}"]}
            for gate in FIVE_SAFETY_GATES
        },
        "activation_authorized": False,
        "authority_effect": "none_audit_does_not_activate",
    })


def classify_review_state(root: Path | None = None) -> dict[str, Any]:
    """Lock-free, write-free, before/after-bound reader classification."""
    root = (root or Path(__file__).resolve().parent).resolve()
    try:
        before = capture_snapshot(root)
        input_errors = validate_input_snapshot(before, root)
        unexpected = sorted(set(before["package_inventory"]) - _allowed_inventory())
        package_present = [relative for relative in PACKAGE_ARTIFACT_PATHS if before["package"].get(relative) is not None]
        if input_errors or unexpected:
            status = "FAIL_CLOSED"
            errors = input_errors + [f"unexpected_package_file:{item}" for item in unexpected]
        elif not package_present:
            status = "PRE_ACTIVATION"
            errors = []
        else:
            expected = expected_package_bytes(before)
            mismatches = [relative for relative in package_present if before["package"][relative] != expected[relative]]
            if mismatches:
                status = "FAIL_CLOSED"
                errors = [f"package_artifact_tampered:{relative}" for relative in mismatches]
            elif len(package_present) != len(PACKAGE_ARTIFACT_PATHS):
                status = "FAIL_CLOSED_RECOVERY_REQUIRED"
                errors = []
            else:
                errors = validate_package_snapshot(before, root)
                audit_raw = before["package"].get(INDEPENDENT_AUDIT_PATH)
                if errors:
                    status = "FAIL_CLOSED"
                elif audit_raw is None:
                    status = "REVIEW_PACKAGE_READY"
                else:
                    audit_errors = validate_independent_audit(before)
                    errors.extend(audit_errors)
                    status = "INDEPENDENT_AUDIT_PASSED_NO_AUTHORITY" if not audit_errors else "FAIL_CLOSED"
        after = capture_snapshot(root)
        if snapshot_signature(before) != snapshot_signature(after):
            status = "FAIL_CLOSED_SNAPSHOT_CHANGED"
            errors = [*errors, "protected_snapshot_changed_during_read"]
    except Exception as exc:
        status = "FAIL_CLOSED"
        errors = [f"reader_exception:{type(exc).__name__}:{exc}"]
    return {
        "package_id": PACKAGE_ID,
        "target_day": DAY,
        "status": status,
        "errors": sorted(set(errors)),
        "review_package_ready": status in {"REVIEW_PACKAGE_READY", "INDEPENDENT_AUDIT_PASSED_NO_AUTHORITY"},
        "independent_audit_passed": status == "INDEPENDENT_AUDIT_PASSED_NO_AUTHORITY",
        "activation_authorized": False,
        "live_collection_enabled": False,
        "canonical_write_enabled": False,
        "promotion_allowed": False,
        "schedule_write_enabled": False,
        "query_submission_authorized": False,
        "route": None,
        "authority_effect": "none",
    }


def require_independent_audit(root: Path | None = None) -> dict[str, Any]:
    state = classify_review_state(root)
    if state["status"] != "INDEPENDENT_AUDIT_PASSED_NO_AUTHORITY":
        raise ReviewPackageError("valid exact independent audit required; authority remains disabled")
    return state


def resolve_live_route(root: Path | None = None) -> dict[str, Any]:
    raise ReviewPackageError("Jan10 review package never resolves a live or authorized route")


def execute_activation(root: Path | None = None) -> None:
    raise ReviewPackageError("activation is outside this no-write review package and is not authorized")


def publish_commit_marker(root: Path | None = None) -> None:
    raise ReviewPackageError("commit-marker publication is forbidden by the Jan10 review package")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exclusive_or_exact(path: Path, raw: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_file() and path.read_bytes() == raw:
            return "reused_exact"
        raise ReviewPackageError(f"immutable artifact collision: {path}")
    temp = path.with_name(f".{path.name}.{PACKAGE_ID}.{uuid.uuid4().hex}.immutable.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if temp.read_bytes() != raw:
            raise ReviewPackageError(f"immutable temp verification failed: {path}")
        try:
            os.link(temp, path)
        except FileExistsError:
            if path.is_file() and path.read_bytes() == raw:
                return "reused_exact"
            raise ReviewPackageError(f"immutable artifact collision: {path}")
        _fsync_directory(path.parent)
        if not path.is_file() or path.read_bytes() != raw:
            raise ReviewPackageError(f"immutable publish verification failed: {path}")
        return "created"
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


@contextmanager
def publication_lock(root: Path, *, timeout_seconds: float = 30.0) -> Iterable[None]:
    path = resolve_corpus_path(root, PUBLICATION_LOCK_PATH)
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
                    raise ReviewPackageError("review package publication lock timed out") from exc
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


def _protected_publication_signature(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "inputs": {key: _raw_signature(value) for key, value in snapshot["inputs"].items()},
        "sources": {key: _raw_signature(value) for key, value in snapshot["sources"].items()},
        "absence": snapshot["absence"],
    }


def publish_review_package(
    root: Path | None = None,
    *,
    _before_artifact: Callable[[str], None] | None = None,
) -> Path:
    """Publish only the four immutable review artifacts; never authority."""
    root = (root or Path(__file__).resolve().parent).resolve()
    with publication_lock(root):
        before = capture_snapshot(root)
        errors = validate_input_snapshot(before, root, require_audit_absent=True)
        unexpected = sorted(set(before["package_inventory"]) - _allowed_inventory())
        errors.extend(f"unexpected_package_file:{item}" for item in unexpected)
        if errors:
            raise ReviewPackageError("review package preflight failed: " + "; ".join(sorted(set(errors))))
        expected = expected_package_bytes(before)
        present = [relative for relative in PACKAGE_ARTIFACT_PATHS if before["package"].get(relative) is not None]
        for relative in present:
            if before["package"][relative] != expected[relative]:
                raise ReviewPackageError(f"immutable review artifact collision: {relative}")
        if MANIFEST_PATH in present and len(present) != len(PACKAGE_ARTIFACT_PATHS):
            raise ReviewPackageError("review manifest exists without complete bound prefix")
        protected_signature = _protected_publication_signature(before)
        for relative in PACKAGE_PUBLICATION_ORDER:
            if _before_artifact is not None:
                _before_artifact(relative)
            if relative == MANIFEST_PATH:
                current = capture_snapshot(root)
                if _protected_publication_signature(current) != protected_signature:
                    raise ReviewPackageError("protected input or absence state changed before final manifest")
                for prerequisite in PACKAGE_ARTIFACT_PATHS[:-1]:
                    if current["package"].get(prerequisite) != expected[prerequisite]:
                        raise ReviewPackageError("review package prefix changed before final manifest")
            _write_exclusive_or_exact(resolve_corpus_path(root, relative), expected[relative])
        state = classify_review_state(root)
        if state["status"] != "REVIEW_PACKAGE_READY":
            raise ReviewPackageError("published review package did not validate: " + "; ".join(state["errors"]))
        return resolve_corpus_path(root, MANIFEST_PATH)


def package_bindings(root: Path | None = None) -> list[dict[str, Any]]:
    root = (root or Path(__file__).resolve().parent).resolve()
    state = classify_review_state(root)
    if state["status"] not in {"REVIEW_PACKAGE_READY", "INDEPENDENT_AUDIT_PASSED_NO_AUTHORITY"}:
        raise ReviewPackageError("exact complete review package required")
    return [
        _simple_binding(relative, resolve_corpus_path(root, relative).read_bytes())
        for relative in PACKAGE_ARTIFACT_PATHS
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish-review-package", action="store_true")
    parser.add_argument("--classify", action="store_true")
    args = parser.parse_args()
    if args.publish_review_package:
        path = publish_review_package()
        payload = {"status": "REVIEW_PACKAGE_READY", "manifest": _simple_binding(MANIFEST_PATH, path.read_bytes()), "activation_authorized": False}
        print(json.dumps(payload, indent=2))
        return 0
    print(json.dumps(classify_review_state(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
