"""Immutable, disabled authority-migration candidate for Premium Journals v2.7.

This module can build and validate a Jan 9 candidate, but it cannot activate it.
All schedule transitions are pure in-memory projections.  A separate reviewed
activation receipt and atomic schedule writer are intentionally out of scope.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import posixpath
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import premium_journals_provenance_contract as v26
import premium_journals_provenance_contract_v2_7 as v27
import premium_journals_v2_7_schedule as v27_schedule


SCHEMA_VERSION = "1.0.0"
ARTIFACT_TYPE = "premium_journals_v2_7_authority_migration_candidate"
MIGRATION_ID = "premium-journals-v2-7-authority-2026-01-09-v1"
DAY = "2026-01-09"
PREVIOUS_DAY = "2026-01-08"
STATUS = "disabled_pending_independent_review"
CANDIDATE_RELATIVE_PATH = "working/premium_journals_v2_7_authority_migration_v1_candidate.json"
MIGRATION_READINESS_RELATIVE_PATH = "working/premium_journals_v2_7_authority_migration_v1_readiness_report.json"
READINESS_RELATIVE_PATH = "working/premium_journals_v2_7_readiness_report.json"
SCHEDULE_RELATIVE_PATH = "working/scoped_three_parent_collection_schedule.json"
JAN8_RUN_ROOT = "working/jan8_dual_run_20260722T072547Z"
JAN8_FULL_RELATIVE_PATH = f"{JAN8_RUN_ROOT}/shadow_full_verification.json"
JAN8_STAGE_RELATIVE_PATH = (
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-08_20260722T072547Z/v2_6_revalidated/"
    "channel_premium_journals_1283941772577472643_2026-01-08_2026-01-08.json"
)
JAN8_AUTHORITY_RELATIVE_PATH = v26.expected_canonical_relative_path(PREVIOUS_DAY, PREVIOUS_DAY)

FROZEN_READINESS_SHA256 = "7c7434c2578edb3862914e9b8ce3c757a8b93f3d4a7daf40497a253fc61c1669"
FROZEN_READINESS_BYTES = 6428
FROZEN_SCHEDULE_SHA256 = "0a1fd787f0fbeb6cb142edd028d16daa3c0189027d2d42b82ba40bb209ca18d6"
FROZEN_SCHEDULE_BYTES = 930837
FROZEN_JAN8_FULL_SHA256 = "3acb44ced8cac7c74d350350170a479cadb701304b386f031b0dd551ac291168"
FROZEN_JAN8_FULL_BYTES = 3913
FROZEN_JAN8_CANONICAL_SHA256 = "7a9d71adb66ff0317750413c5cb89b459567bd202af3c71a126c4addc134bfb5"
FROZEN_JAN8_CANONICAL_BYTES = 1231302
FROZEN_PAGE_REPORTS = (
    (1, "9ad17ccc0d65c94914b2e3d0a3a11236ebad29abdf0bad51c8334a6e8e86354c", 11334, 6, 4, 2),
    (2, "379de1233801d6e703bde2af295f42959025a361a9e9fae319da0bd74b9c1ff6", 18707, 11, 9, 2),
    (3, "57dc9c6a97c1e247e4890cd45de6915aadf066812a764ae2156e35ef1d4d694d", 26461, 16, 8, 8),
    (4, "6cc20962ad47f582cb35a51a5b1b8a86bbabd00036b6cc9890fdf2dec9f3a5a7", 28128, 17, 6, 11),
    (5, "3f3680463dddfb7ef65b721a5e963b2b51d4342f2a0de35b3bd15d8c82c0e778", 22454, 13, 0, 13),
    (6, "a3ef6ba0ec98943758fa0001edc5842e79013a3d6c0c1a8f70f691e47a717633", 17298, 10, 7, 3),
    (7, "aae12179abff5fb755372d4e2824fa10ea7d72d2c0614e6e78dbdd6a9e0674b6", 9514, 5, 2, 3),
)
BASELINE = (
    ("2026-01-01", 198, 51, 26, 25, "d1c23f81d414b80fdd53f4846e7c998e94dff75afb42378197f500e966641297", "42336ac453ad8ed402874bdded95fc08d6bb8f146aa77778d44d736a9d11ff5c"),
    ("2026-01-02", 439, 159, 64, 95, "2523070584e8bc331ed15e7af91f223e51c25289d3f57322ab18476fe21878dd", "9420898958172e3dd90357b585faf21a2bc3c3fbfdb28f31c02ae4565a5036bc"),
    ("2026-01-03", 199, 42, 22, 20, "38cdd3d14358ac415bb4b6508a228ccf915a7fcd4e00bdb5d8d719db78438614", "686dfa30cca4b6fb4a3ae48076c181351e568036c90b88305f85841c578a61d7"),
    ("2026-01-04", 100, 20, 9, 11, "faaebdbf00d7348a1132a6905ddc05aadf1496a882908753832525bfc57a00dd", "2ca62620686a259bb4a0224dac19292f29cf29a263fe8e48768b639faafd427b"),
    ("2026-01-05", 430, 157, 55, 102, "923fcd44c954f5a98f5fe4123a63ab14ee0703ef11085df1cab4688b8cd76102", "b9c097693c80aa51bb5b0ce3a43767b20a55db1a0b657df171db00d0c4f4c5bd"),
    ("2026-01-06", 315, 156, 66, 90, "526623f847e488155abcce439a2ee043c75a74061ce656d66016c199ef12e65e", "5e239835f54718999d8aee59503851734713a4c2aa691e2fa28cc1ad10434487"),
)
IMPLEMENTATION_PATHS = (
    "../discord_browser_collector_v2_7.mjs",
    "../premium_v2_7_direct_parity_fixtures.json",
    "premium_journals_provenance_contract.py",
    "premium_journals_provenance_contract_v2_7.py",
    "premium_journals_attachment_accessory_contract_v2_7.py",
    "premium_journals_v2_7_schedule.py",
    "premium_journals_v2_7_integration.py",
    "qa/validate_premium_journals_v2_7.py",
    "premium_journals_v2_7_authority_migration_v1.py",
    "qa/validate_premium_journals_v2_7_authority_migration_v1.py",
    "test_premium_journals_v2_7_authority_migration_v1.py",
    "docs/premium_journals_v2_7_authority_migration_v1.md",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _normalized_relative(value: str) -> bool:
    return bool(value) and not Path(value).is_absolute() and "\\" not in value and posixpath.normpath(value) == value


def _resolve(root: Path, relative: str) -> Path:
    if not _normalized_relative(relative):
        raise ValueError(f"non-normalized relative path: {relative}")
    path = (root / relative).resolve()
    project = root.resolve().parent
    try:
        path.relative_to(project)
    except ValueError as exc:
        raise ValueError(f"path outside discord_trading_research: {relative}") from exc
    return path


def _binding(root: Path, relative: str, role: str) -> dict[str, Any]:
    path = _resolve(root, relative)
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"role": role, "path": relative, "sha256": sha256_file(path), "bytes": path.stat().st_size}


def _expected_v26_route() -> dict[str, Any]:
    return {
        "route_id": "premium_journals_2026-01-09_2026-01-09",
        "channel_id": v26.PREMIUM_ID,
        "channel_name": v26.PREMIUM_NAME,
        "channel_kind": "forum channel",
        "start": DAY,
        "end": DAY,
        "query_prefix": "in:premium-journals",
        "query": v27_schedule.exact_query(DAY),
        "expected_canonical_path": v26.expected_canonical_relative_path(DAY, DAY),
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


def build_authoritative_route() -> dict[str, Any]:
    """Return the proposed active grammar; this does not enable collection."""
    route = v27_schedule.build_disabled_route(DAY)
    route.update({
        "route_id": f"premium-journals-v2-7-authority:{DAY}:{DAY}",
        "authority_schema_version": SCHEMA_VERSION,
        "status": "active_v2_7_authority_only_after_atomic_activation_receipt",
        "live_collection_enabled": True,
        "promotion_allowed": True,
        "authority_enabled": True,
        "migration_id": MIGRATION_ID,
        "non_overlapping_daily_route": True,
        "activation_receipt_required": True,
    })
    return route


def _disabled_controls() -> dict[str, bool]:
    return {
        "activation_requested": False,
        "activation_allowed": False,
        "live_collection_enabled": False,
        "promotion_allowed": False,
        "independent_review_passed": False,
        "activation_receipt_present": False,
        "schedule_mutation_performed": False,
        "jan1_through_jan8_authority_modified": False,
    }


def _authority_scope() -> dict[str, Any]:
    return {
        "guild_id": v26.GUILD_ID,
        "parent_forum_channel_id": v26.PREMIUM_ID,
        "parent_forum_channel_name": v26.PREMIUM_NAME,
        "start": DAY,
        "end": DAY,
        "route_granularity": "one_exact_local_day",
        "timezone": "America/Chicago",
        "non_overlapping_route_grammar": "one_authority_per_parent_and_local_day",
        "future_days_require_separate_immutable_activation_records": True,
    }


def _evidence_boundary() -> dict[str, Any]:
    return {
        "content_source_scope": "authenticated_discord_only",
        "outside_content_sources_used": False,
        "external_network_evidence_used": False,
        "local_governance_artifacts_are_content_evidence": False,
        "all_local_source_artifacts_byte_bound": True,
    }


def _atomic_semantics() -> dict[str, Any]:
    return {
        "checkpoint_write": "exclusive_create_or_exact_semantic_reuse",
        "page_plan_write": "exclusive_immutable_before_group_resolution",
        "page_acceptance": "exact_full_partition_and_every_group_checkpointed",
        "canonical_acceptance": "all_exact_pages_implied_by_reported_total_then_generic_qa",
        "schedule_activation": "single_atomic_projection_with_reviewed_receipt_and_commit_marker",
        "partial_transaction_visibility": "pre_activation_v2_6_schedule_remains_authoritative",
        "automatic_authority_fallback": False,
    }


def _rollback_contract() -> dict[str, Any]:
    return {
        "before_activation": "exactly_v2_6_jan9_authority_and_zero_v2_7_jan9_authority",
        "after_activation": "retired_v2_6_jan9_route_and_exactly_one_v2_7_jan9_authority",
        "missing_or_invalid_commit_receipt": "ignore_v2_7_and_retain_frozen_v2_6_authority",
        "collection_failure_after_activation": "quarantine_and_stop_without_auto_reviving_v2_6",
        "rollback_requires_separate_immutable_reviewed_receipt": True,
        "rollback_requires_v2_7_canonical_quarantine_before_commit": True,
        "rollback_restores_exact_original_v2_6_route": True,
        "simultaneous_v2_6_and_v2_7_authority_forbidden": True,
    }


def _activation_preconditions() -> list[str]:
    return [
        "candidate_validation_passes",
        "independent_audit_receipt_is_exact_and_passed",
        "jan8_authoritative_canonical_exists_and_matches_staged_bytes",
        "frozen_pre_activation_schedule_snapshot_matches",
        "exact_jan9_v2_6_route_is_unchanged",
        "jan9_v2_6_canonical_does_not_exist",
        "jan9_v2_7_canonical_does_not_exist_before_activation",
        "atomic_schedule_writer_and_commit_marker_are_separately_reviewed",
    ]


def _baseline_claim(root: Path, readiness: dict[str, Any]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    declared = readiness.get("baseline", {}).get("per_day", [])
    by_day = {item.get("day"): item for item in declared if isinstance(item, dict)}
    for day, messages, groups, direct, fallback, key_sha, canonical_sha in BASELINE:
        relative = v26.expected_canonical_relative_path(day, day)
        binding = _binding(root, relative, "jan1_jan6_v2_6_authoritative_canonical")
        if binding["sha256"] != canonical_sha:
            raise ValueError(f"frozen baseline canonical changed: {day}")
        expected = {
            "day": day, "messages": messages, "groups": groups, "direct": direct,
            "fallback": fallback, "savings_percent": round(100 * direct / groups, 2),
            "eligible_key_set_sha256": key_sha, "canonical_sha256": canonical_sha,
        }
        if by_day.get(day) != expected:
            raise ValueError(f"readiness baseline drift: {day}")
        entries.append({**expected, "canonical": binding})
    return {
        "window": {"start": BASELINE[0][0], "end": BASELINE[-1][0], "days": 6},
        "header_navigation_groups": 585,
        "strict_direct_consensus_groups": 242,
        "required_header_fallback_groups": 343,
        "estimated_header_navigation_savings_percent": 41.37,
        "python_javascript_eligibility_parity": True,
        "per_day": entries,
    }


def _jan8_shadow_claim(root: Path, full: dict[str, Any]) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    declared = {item.get("page_number"): item for item in full.get("page_summaries", []) if isinstance(item, dict)}
    for page, digest, size, controls, direct, fallback in FROZEN_PAGE_REPORTS:
        relative = f"{JAN8_RUN_ROOT}/page_{page:03d}_shadow_control_comparison.json"
        report = _load(_resolve(root, relative))
        binding = _binding(root, relative, "jan8_v2_7_shadow_page_comparison")
        if binding["sha256"] != digest or binding["bytes"] != size:
            raise ValueError(f"frozen Jan8 page report changed: {page}")
        expected_summary = {
            "page_number": page,
            "rows": 25 if page < 7 else 12,
            "controls": controls,
            "direct": direct,
            "fallback": fallback,
            "all_resolution_child_matches": controls,
            "comparison_report": {"path": relative, "sha256": digest, "bytes": size},
        }
        if declared.get(page) != expected_summary:
            raise ValueError(f"Jan8 full/page summary disagreement: {page}")
        if report.get("page_number") != page or report.get("v2_6_control_group_count") != controls:
            raise ValueError(f"Jan8 page report count drift: {page}")
        shadow = report.get("shadow", {})
        if shadow.get("direct_count") != direct or shadow.get("fallback_count") != fallback:
            raise ValueError(f"Jan8 page method count drift: {page}")
        if shadow.get("accepted") is not True or shadow.get("canonical_written") is not False:
            raise ValueError(f"Jan8 page shadow boundary drift: {page}")
        pages.append({**expected_summary, "binding": binding})
    return {
        "day": PREVIOUS_DAY,
        "mode": "shadow_nonpromotable",
        "reported_total": 162,
        "reported_pages": 7,
        "v2_6_control_groups": 78,
        "v2_7_direct_groups": 36,
        "v2_7_header_fallback_groups": 42,
        "direct_key_and_child_matches": 36,
        "all_resolution_child_matches": 78,
        "full_verification": _binding(root, JAN8_FULL_RELATIVE_PATH, "jan8_v2_7_full_shadow_verification"),
        "page_reports": pages,
    }


def build_candidate(root: Path | None = None) -> dict[str, Any]:
    root = (root or Path(__file__).resolve().parent).resolve()
    readiness = _load(_resolve(root, READINESS_RELATIVE_PATH))
    full = _load(_resolve(root, JAN8_FULL_RELATIVE_PATH))
    schedule = _load(_resolve(root, SCHEDULE_RELATIVE_PATH))
    readiness_binding = _binding(root, READINESS_RELATIVE_PATH, "frozen_v2_7_readiness_report")
    schedule_binding = _binding(root, SCHEDULE_RELATIVE_PATH, "pre_activation_schedule_snapshot")
    full_binding = _binding(root, JAN8_FULL_RELATIVE_PATH, "jan8_v2_7_full_shadow_verification")
    if (readiness_binding["sha256"], readiness_binding["bytes"]) != (FROZEN_READINESS_SHA256, FROZEN_READINESS_BYTES):
        raise ValueError("frozen readiness report changed")
    if (schedule_binding["sha256"], schedule_binding["bytes"]) != (FROZEN_SCHEDULE_SHA256, FROZEN_SCHEDULE_BYTES):
        raise ValueError("pre-activation schedule snapshot changed")
    if (full_binding["sha256"], full_binding["bytes"]) != (FROZEN_JAN8_FULL_SHA256, FROZEN_JAN8_FULL_BYTES):
        raise ValueError("frozen Jan8 full verification changed")
    if readiness.get("status") != "shadow_ready_nonpromotable" or readiness.get("live_collection_enabled") is not False or readiness.get("promotion_allowed") is not False:
        raise ValueError("readiness boundary is not frozen shadow-only")
    if full.get("status") != "PASS" or full.get("shadow_live_collection_enabled") is not False or full.get("shadow_promotion_allowed") is not False or full.get("shadow_canonical_written") is not False:
        raise ValueError("Jan8 full shadow verification is not a passing nonpromotable run")

    v26_routes = schedule.get("routes", {}).get("premium_journals", [])
    current = [route for route in v26_routes if isinstance(route, dict) and route.get("start") == DAY and route.get("end") == DAY]
    expected_v26 = _expected_v26_route()
    if current != [expected_v26]:
        raise ValueError("exact Jan9 v2.6 route not found in frozen schedule")

    jan8_stage = _binding(root, JAN8_STAGE_RELATIVE_PATH, "jan8_v2_6_revalidated_staged_canonical")
    if (jan8_stage["sha256"], jan8_stage["bytes"]) != (FROZEN_JAN8_CANONICAL_SHA256, FROZEN_JAN8_CANONICAL_BYTES):
        raise ValueError("frozen Jan8 staged canonical changed")
    jan8_authority = _binding(root, JAN8_AUTHORITY_RELATIVE_PATH, "jan8_v2_6_authoritative_canonical")
    if (jan8_authority["sha256"], jan8_authority["bytes"]) != (FROZEN_JAN8_CANONICAL_SHA256, FROZEN_JAN8_CANONICAL_BYTES):
        raise ValueError("promoted Jan8 authoritative canonical changed")
    implementation = [_binding(root, path, "collector_or_contract_implementation") for path in IMPLEMENTATION_PATHS]
    source_bindings = [readiness_binding, schedule_binding, full_binding, jan8_stage, jan8_authority, *implementation]
    proposed = build_authoritative_route()
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "migration_id": MIGRATION_ID,
        "status": STATUS,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "immutable": True,
        "activation_controls": _disabled_controls(),
        "authority_scope": _authority_scope(),
        "evidence_boundary": _evidence_boundary(),
        "source_bindings": {
            "readiness_report": readiness_binding,
            "schedule_snapshot": schedule_binding,
            "jan8_staged_canonical": jan8_stage,
            "jan8_authoritative_canonical": jan8_authority,
            "implementation_files": implementation,
            "source_files": sorted(source_bindings, key=lambda item: (item["path"], item["role"])),
        },
        "baseline": _baseline_claim(root, readiness),
        "jan8_shadow_verification": _jan8_shadow_claim(root, full),
        "jan8_authority_promotion_gate": {
            "staged_source": jan8_stage,
            "authoritative_source": jan8_authority,
            "expected_authoritative_path": JAN8_AUTHORITY_RELATIVE_PATH,
            "required_sha256": FROZEN_JAN8_CANONICAL_SHA256,
            "required_bytes": FROZEN_JAN8_CANONICAL_BYTES,
            "present_at_candidate_build": True,
            "required_before_activation": True,
            "copy_or_reencode_forbidden": True,
            "must_be_byte_identical_to_stage": True,
        },
        "current_v2_6_authority": {
            "schedule_snapshot": schedule_binding,
            "route": expected_v26,
            "route_sha256": sha256_json(expected_v26),
            "jan9_v2_7_authority_present": False,
        },
        "proposed_v2_7_authority": {
            "route": proposed,
            "route_sha256": sha256_json(proposed),
            "authoritative_canonical_path": v27.expected_canonical_relative_path(DAY, DAY),
            "checkpoint_directory": v27.expected_checkpoint_relative_directory(DAY),
            "state_in_this_record": "disabled_unactivated_candidate",
        },
        "v2_6_route_retirement": {
            "before": expected_v26,
            "after_status": "retired_by_v2_7_authority_activation",
            "delete_route_or_artifacts": False,
            "allowed_only_inside_same_atomic_activation_transaction": True,
            "retirement_before_activation_forbidden": True,
            "all_other_v2_6_routes_must_remain_byte_equivalent": True,
        },
        "atomic_semantics": _atomic_semantics(),
        "no_double_authority_and_rollback": _rollback_contract(),
        "activation_preconditions": _activation_preconditions(),
    }
    payload["source_bindings"]["source_file_set_sha256"] = sha256_json(payload["source_bindings"]["source_files"])
    payload["record_fingerprint_sha256"] = sha256_json(payload)
    return payload


def _binding_errors(root: Path, binding: Any, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(binding, dict) or set(binding) != {"role", "path", "sha256", "bytes"}:
        return [f"{label}_schema_invalid"]
    try:
        path = _resolve(root, binding.get("path"))
    except (TypeError, ValueError):
        return [f"{label}_path_invalid"]
    if not path.is_file():
        return [f"{label}_missing"]
    if binding.get("sha256") != sha256_file(path):
        errors.append(f"{label}_sha256_mismatch")
    if type(binding.get("bytes")) is not int or binding.get("bytes") != path.stat().st_size:
        errors.append(f"{label}_bytes_mismatch")
    return errors


def _report_binding_errors(root: Path, binding: Any, label: str) -> list[str]:
    """Validate an immutable review-report binding without a role field."""
    errors: list[str] = []
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256", "bytes"}:
        return [f"{label}_schema_invalid"]
    try:
        path = _resolve(root, binding.get("path"))
    except (TypeError, ValueError):
        return [f"{label}_path_invalid"]
    if not path.is_file():
        return [f"{label}_missing"]
    if binding.get("sha256") != sha256_file(path):
        errors.append(f"{label}_sha256_mismatch")
    if type(binding.get("bytes")) is not int or binding.get("bytes") != path.stat().st_size:
        errors.append(f"{label}_bytes_mismatch")
    return errors


def _all_bindings(candidate: dict[str, Any]) -> Iterable[tuple[str, Any]]:
    sources = candidate.get("source_bindings", {})
    for index, item in enumerate(sources.get("source_files", []) if isinstance(sources, dict) else []):
        yield f"source_file_{index}", item
    baseline = candidate.get("baseline", {})
    for index, item in enumerate(baseline.get("per_day", []) if isinstance(baseline, dict) else []):
        yield f"baseline_canonical_{index}", item.get("canonical") if isinstance(item, dict) else None
    jan8 = candidate.get("jan8_shadow_verification", {})
    if isinstance(jan8, dict):
        yield "jan8_full_verification", jan8.get("full_verification")
        for index, item in enumerate(jan8.get("page_reports", [])):
            yield f"jan8_page_report_{index}", item.get("binding") if isinstance(item, dict) else None


def validate_candidate(candidate: dict[str, Any], root: Path | None = None, *, require_activation_preconditions: bool = False) -> list[str]:
    root = (root or Path(__file__).resolve().parent).resolve()
    errors: list[str] = []
    expected_keys = {
        "schema_version", "artifact_type", "migration_id", "status", "generated_at_utc",
        "immutable", "activation_controls", "authority_scope", "evidence_boundary",
        "source_bindings", "baseline", "jan8_shadow_verification",
        "jan8_authority_promotion_gate", "current_v2_6_authority",
        "proposed_v2_7_authority", "v2_6_route_retirement", "atomic_semantics",
        "no_double_authority_and_rollback", "activation_preconditions", "record_fingerprint_sha256",
    }
    if set(candidate) != expected_keys:
        errors.append("candidate_top_level_field_set_mismatch")
    for field, expected in (("schema_version", SCHEMA_VERSION), ("artifact_type", ARTIFACT_TYPE), ("migration_id", MIGRATION_ID), ("status", STATUS), ("immutable", True)):
        if candidate.get(field) != expected:
            errors.append(f"candidate_{field}_mismatch")
    if not v26._is_iso_timestamp(candidate.get("generated_at_utc")):
        errors.append("candidate_generated_at_invalid")
    fingerprint = candidate.get("record_fingerprint_sha256")
    unsigned = dict(candidate)
    unsigned.pop("record_fingerprint_sha256", None)
    if fingerprint != sha256_json(unsigned):
        errors.append("candidate_fingerprint_mismatch")

    controls = candidate.get("activation_controls", {})
    if controls != _disabled_controls():
        errors.append("candidate_disabled_controls_invalid")
    if candidate.get("authority_scope") != _authority_scope():
        errors.append("candidate_authority_scope_invalid")
    if candidate.get("evidence_boundary") != _evidence_boundary():
        errors.append("candidate_evidence_boundary_invalid")
    if candidate.get("atomic_semantics") != _atomic_semantics():
        errors.append("candidate_atomic_semantics_invalid")
    if candidate.get("no_double_authority_and_rollback") != _rollback_contract():
        errors.append("candidate_rollback_contract_invalid")
    if candidate.get("activation_preconditions") != _activation_preconditions():
        errors.append("candidate_activation_preconditions_invalid")

    seen: set[tuple[str, str]] = set()
    for label, binding in _all_bindings(candidate):
        errors.extend(_binding_errors(root, binding, label))
        if isinstance(binding, dict):
            key = (str(binding.get("path")), str(binding.get("role")))
            if key in seen and label.startswith("source_file_"):
                errors.append("candidate_duplicate_source_binding")
            seen.add(key)
    sources = candidate.get("source_bindings", {})
    source_files = sources.get("source_files", []) if isinstance(sources, dict) else []
    if not isinstance(source_files, list) or sources.get("source_file_set_sha256") != sha256_json(source_files):
        errors.append("candidate_source_file_set_hash_mismatch")
    source_by_path = {item.get("path"): item for item in source_files if isinstance(item, dict)}
    expected_source_paths = {READINESS_RELATIVE_PATH, SCHEDULE_RELATIVE_PATH, JAN8_FULL_RELATIVE_PATH, JAN8_STAGE_RELATIVE_PATH, JAN8_AUTHORITY_RELATIVE_PATH, *IMPLEMENTATION_PATHS}
    if set(source_by_path) != expected_source_paths:
        errors.append("candidate_source_file_path_set_not_exact")
    frozen_pairs = {
        READINESS_RELATIVE_PATH: (FROZEN_READINESS_SHA256, FROZEN_READINESS_BYTES),
        SCHEDULE_RELATIVE_PATH: (FROZEN_SCHEDULE_SHA256, FROZEN_SCHEDULE_BYTES),
        JAN8_FULL_RELATIVE_PATH: (FROZEN_JAN8_FULL_SHA256, FROZEN_JAN8_FULL_BYTES),
        JAN8_STAGE_RELATIVE_PATH: (FROZEN_JAN8_CANONICAL_SHA256, FROZEN_JAN8_CANONICAL_BYTES),
        JAN8_AUTHORITY_RELATIVE_PATH: (FROZEN_JAN8_CANONICAL_SHA256, FROZEN_JAN8_CANONICAL_BYTES),
    }
    for path, pair in frozen_pairs.items():
        item = source_by_path.get(path, {})
        if (item.get("sha256"), item.get("bytes")) != pair:
            errors.append(f"candidate_frozen_binding_mismatch:{path}")
    direct_source_fields = {
        "readiness_report": READINESS_RELATIVE_PATH,
        "schedule_snapshot": SCHEDULE_RELATIVE_PATH,
        "jan8_staged_canonical": JAN8_STAGE_RELATIVE_PATH,
        "jan8_authoritative_canonical": JAN8_AUTHORITY_RELATIVE_PATH,
    }
    for field, path in direct_source_fields.items():
        if sources.get(field) != source_by_path.get(path):
            errors.append(f"candidate_direct_source_binding_mismatch:{field}")
    if sources.get("implementation_files") != [source_by_path.get(path) for path in IMPLEMENTATION_PATHS]:
        errors.append("candidate_implementation_binding_order_or_set_mismatch")

    baseline = candidate.get("baseline", {})
    if not isinstance(baseline, dict) or baseline.get("header_navigation_groups") != 585 or baseline.get("strict_direct_consensus_groups") != 242 or baseline.get("required_header_fallback_groups") != 343 or baseline.get("estimated_header_navigation_savings_percent") != 41.37 or baseline.get("python_javascript_eligibility_parity") is not True:
        errors.append("candidate_baseline_totals_invalid")
    per_day = baseline.get("per_day", []) if isinstance(baseline, dict) else []
    if len(per_day) != len(BASELINE):
        errors.append("candidate_baseline_day_count_invalid")
    for expected, actual in zip(BASELINE, per_day):
        day, messages, groups, direct, fallback, key_sha, canonical_sha = expected
        canonical_binding = actual.get("canonical", {}) if isinstance(actual, dict) else {}
        expected_item = {
            "day": day, "messages": messages, "groups": groups, "direct": direct,
            "fallback": fallback, "savings_percent": round(100 * direct / groups, 2),
            "eligible_key_set_sha256": key_sha, "canonical_sha256": canonical_sha,
            "canonical": canonical_binding,
        }
        if actual != expected_item or canonical_binding.get("path") != v26.expected_canonical_relative_path(day, day) or canonical_binding.get("sha256") != canonical_sha:
            errors.append(f"candidate_baseline_day_invalid:{day}")

    jan8 = candidate.get("jan8_shadow_verification", {})
    if not isinstance(jan8, dict) or tuple(jan8.get(key) for key in ("reported_total", "reported_pages", "v2_6_control_groups", "v2_7_direct_groups", "v2_7_header_fallback_groups", "direct_key_and_child_matches", "all_resolution_child_matches")) != (162, 7, 78, 36, 42, 36, 78):
        errors.append("candidate_jan8_shadow_totals_invalid")
    expected_jan8_keys = {"day", "mode", "reported_total", "reported_pages", "v2_6_control_groups", "v2_7_direct_groups", "v2_7_header_fallback_groups", "direct_key_and_child_matches", "all_resolution_child_matches", "full_verification", "page_reports"}
    if not isinstance(jan8, dict) or set(jan8) != expected_jan8_keys or jan8.get("day") != PREVIOUS_DAY or jan8.get("mode") != "shadow_nonpromotable":
        errors.append("candidate_jan8_shadow_schema_invalid")
    full_binding = jan8.get("full_verification", {}) if isinstance(jan8, dict) else {}
    if full_binding.get("path") != JAN8_FULL_RELATIVE_PATH or (full_binding.get("sha256"), full_binding.get("bytes")) != (FROZEN_JAN8_FULL_SHA256, FROZEN_JAN8_FULL_BYTES):
        errors.append("candidate_jan8_full_binding_invalid")
    page_reports = jan8.get("page_reports", []) if isinstance(jan8, dict) else []
    if len(page_reports) != 7:
        errors.append("candidate_jan8_page_report_count_invalid")
    for frozen, actual in zip(FROZEN_PAGE_REPORTS, page_reports):
        page, digest, size, controls_count, direct_count, fallback_count = frozen
        binding = actual.get("binding", {}) if isinstance(actual, dict) else {}
        expected_report = {
            "page_number": page,
            "rows": 25 if page < 7 else 12,
            "controls": controls_count,
            "direct": direct_count,
            "fallback": fallback_count,
            "all_resolution_child_matches": controls_count,
            "comparison_report": {
                "path": f"{JAN8_RUN_ROOT}/page_{page:03d}_shadow_control_comparison.json",
                "sha256": digest,
                "bytes": size,
            },
            "binding": binding,
        }
        if actual != expected_report:
            errors.append(f"candidate_jan8_page_summary_invalid:{page}")
        if binding.get("path") != expected_report["comparison_report"]["path"] or (binding.get("sha256"), binding.get("bytes")) != (digest, size):
            errors.append(f"candidate_jan8_page_binding_invalid:{page}")

    current = candidate.get("current_v2_6_authority", {})
    expected_v26 = _expected_v26_route()
    schedule_binding = sources.get("schedule_snapshot", {}) if isinstance(sources, dict) else {}
    expected_current = {
        "schedule_snapshot": schedule_binding,
        "route": expected_v26,
        "route_sha256": sha256_json(expected_v26),
        "jan9_v2_7_authority_present": False,
    }
    if current != expected_current:
        errors.append("candidate_current_v2_6_authority_invalid")
    proposed = candidate.get("proposed_v2_7_authority", {})
    expected_proposed = build_authoritative_route()
    expected_proposed_record = {
        "route": expected_proposed,
        "route_sha256": sha256_json(expected_proposed),
        "authoritative_canonical_path": v27.expected_canonical_relative_path(DAY, DAY),
        "checkpoint_directory": v27.expected_checkpoint_relative_directory(DAY),
        "state_in_this_record": "disabled_unactivated_candidate",
    }
    if proposed != expected_proposed_record:
        errors.append("candidate_proposed_v2_7_authority_invalid")
    if expected_proposed["expected_canonical_path"] != v27.expected_canonical_relative_path(DAY, DAY) or expected_proposed["expected_checkpoint_directory"] != v27.expected_checkpoint_relative_directory(DAY):
        errors.append("candidate_v2_7_versioned_paths_invalid")

    retirement = candidate.get("v2_6_route_retirement", {})
    expected_retirement = {
        "before": expected_v26,
        "after_status": "retired_by_v2_7_authority_activation",
        "delete_route_or_artifacts": False,
        "allowed_only_inside_same_atomic_activation_transaction": True,
        "retirement_before_activation_forbidden": True,
        "all_other_v2_6_routes_must_remain_byte_equivalent": True,
    }
    if retirement != expected_retirement:
        errors.append("candidate_v2_6_retirement_contract_invalid")
    rollback = candidate.get("no_double_authority_and_rollback", {})
    if not isinstance(rollback, dict) or rollback.get("simultaneous_v2_6_and_v2_7_authority_forbidden") is not True or rollback.get("rollback_requires_separate_immutable_reviewed_receipt") is not True or rollback.get("rollback_requires_v2_7_canonical_quarantine_before_commit") is not True or rollback.get("rollback_restores_exact_original_v2_6_route") is not True:
        errors.append("candidate_rollback_contract_invalid")

    stage_binding = sources.get("jan8_staged_canonical", {}) if isinstance(sources, dict) else {}
    authority_binding = sources.get("jan8_authoritative_canonical", {}) if isinstance(sources, dict) else {}
    gate = candidate.get("jan8_authority_promotion_gate", {})
    expected_gate = {
        "staged_source": stage_binding,
        "authoritative_source": authority_binding,
        "expected_authoritative_path": JAN8_AUTHORITY_RELATIVE_PATH,
        "required_sha256": FROZEN_JAN8_CANONICAL_SHA256,
        "required_bytes": FROZEN_JAN8_CANONICAL_BYTES,
        "present_at_candidate_build": True,
        "required_before_activation": True,
        "copy_or_reencode_forbidden": True,
        "must_be_byte_identical_to_stage": True,
    }
    if gate != expected_gate:
        errors.append("candidate_jan8_promotion_gate_invalid")

    try:
        schedule_snapshot = _load(_resolve(root, SCHEDULE_RELATIVE_PATH))
        jan9 = [route for route in schedule_snapshot.get("routes", {}).get("premium_journals", []) if isinstance(route, dict) and route.get("start") == DAY and route.get("end") == DAY]
        if jan9 != [expected_v26]:
            errors.append("candidate_live_schedule_jan9_route_drift")
        active_v27 = [route for route in schedule_snapshot.get("premium_journals_v2_7_authoritative_routes", []) if isinstance(route, dict) and route.get("start") == DAY and route.get("status") == "active_v2_7_authority"]
        if active_v27:
            errors.append("candidate_live_schedule_already_has_v2_7_authority")
    except (OSError, ValueError, json.JSONDecodeError):
        errors.append("candidate_schedule_snapshot_unreadable")

    if require_activation_preconditions:
        gate = candidate.get("jan8_authority_promotion_gate", {})
        expected_path = _resolve(root, JAN8_AUTHORITY_RELATIVE_PATH)
        if not expected_path.is_file():
            errors.append("activation_blocked_jan8_authoritative_canonical_missing")
        elif sha256_file(expected_path) != FROZEN_JAN8_CANONICAL_SHA256 or expected_path.stat().st_size != FROZEN_JAN8_CANONICAL_BYTES:
            errors.append("activation_blocked_jan8_authoritative_canonical_mismatch")
        if not isinstance(gate, dict) or gate.get("expected_authoritative_path") != JAN8_AUTHORITY_RELATIVE_PATH or gate.get("must_be_byte_identical_to_stage") is not True:
            errors.append("activation_blocked_jan8_gate_invalid")
        for relative, label in ((v26.expected_canonical_relative_path(DAY, DAY), "v2_6"), (v27.expected_canonical_relative_path(DAY, DAY), "v2_7")):
            if _resolve(root, relative).is_file():
                errors.append(f"activation_blocked_jan9_{label}_canonical_already_exists")
    return sorted(set(errors))


def validate_activation_receipt(receipt: Any, candidate: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_fields = {"schema_version", "artifact_type", "migration_id", "candidate_fingerprint_sha256", "action", "status", "approved_at_utc", "reviewer", "independent_audit", "immutable"}
    if not isinstance(receipt, dict) or set(receipt) != expected_fields:
        return ["activation_receipt_schema_invalid"]
    expected = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_activation_receipt",
        "migration_id": MIGRATION_ID,
        "candidate_fingerprint_sha256": candidate.get("record_fingerprint_sha256"),
        "action": "activate",
        "status": "approved_for_atomic_activation",
        "immutable": True,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        errors.append("activation_receipt_binding_invalid")
    if not v26._is_iso_timestamp(receipt.get("approved_at_utc")) or not str(receipt.get("reviewer") or "").strip():
        errors.append("activation_receipt_approval_invalid")
    audit = receipt.get("independent_audit")
    if not isinstance(audit, dict) or set(audit) != {"passed", "report"} or audit.get("passed") is not True:
        errors.append("activation_receipt_independent_audit_invalid")
    elif not isinstance(audit.get("report"), dict) or set(audit["report"]) != {"path", "sha256", "bytes"}:
        errors.append("activation_receipt_audit_report_binding_invalid")
    return sorted(set(errors))


def validate_rollback_receipt(receipt: Any, candidate: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_fields = {"schema_version", "artifact_type", "migration_id", "candidate_fingerprint_sha256", "action", "status", "approved_at_utc", "reviewer", "rollback_review", "v2_7_canonical_quarantined", "immutable"}
    if not isinstance(receipt, dict) or set(receipt) != expected_fields:
        return ["rollback_receipt_schema_invalid"]
    expected = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_rollback_receipt",
        "migration_id": MIGRATION_ID,
        "candidate_fingerprint_sha256": candidate.get("record_fingerprint_sha256"),
        "action": "rollback",
        "status": "approved_for_atomic_rollback",
        "v2_7_canonical_quarantined": True,
        "immutable": True,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        errors.append("rollback_receipt_binding_invalid")
    if not v26._is_iso_timestamp(receipt.get("approved_at_utc")) or not str(receipt.get("reviewer") or "").strip():
        errors.append("rollback_receipt_approval_invalid")
    review = receipt.get("rollback_review")
    if not isinstance(review, dict) or set(review) != {"passed", "report"} or review.get("passed") is not True:
        errors.append("rollback_receipt_review_invalid")
    elif not isinstance(review.get("report"), dict) or set(review["report"]) != {"path", "sha256", "bytes"}:
        errors.append("rollback_receipt_report_binding_invalid")
    return sorted(set(errors))


def project_activation(schedule: dict[str, Any], candidate: dict[str, Any], receipt: dict[str, Any], root: Path | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Return a hypothetical activated schedule.  Never writes to disk."""
    root = (root or Path(__file__).resolve().parent).resolve()
    errors = validate_candidate(candidate, root, require_activation_preconditions=True)
    errors.extend(validate_activation_receipt(receipt, candidate))
    audit = receipt.get("independent_audit") if isinstance(receipt, dict) else None
    errors.extend(_report_binding_errors(root, audit.get("report") if isinstance(audit, dict) else None, "activation_independent_audit_report"))
    if sha256_json(schedule) != sha256_json(_load(_resolve(root, SCHEDULE_RELATIVE_PATH))):
        errors.append("activation_schedule_not_exact_frozen_snapshot")
    if errors:
        return None, sorted(set(errors))
    projected = copy.deepcopy(schedule)
    routes = projected["routes"]["premium_journals"]
    matches = [index for index, route in enumerate(routes) if route == _expected_v26_route()]
    if len(matches) != 1:
        return None, ["activation_exact_v2_6_route_count_not_one"]
    retired = copy.deepcopy(routes[matches[0]])
    retired["status"] = "retired_by_v2_7_authority_activation"
    retired["authority_retirement_migration_id"] = MIGRATION_ID
    retired["activation_receipt_candidate_fingerprint"] = candidate["record_fingerprint_sha256"]
    routes[matches[0]] = retired
    active = copy.deepcopy(candidate["proposed_v2_7_authority"]["route"])
    active["status"] = "active_v2_7_authority"
    projected.setdefault("premium_journals_v2_7_authoritative_routes", []).append(active)
    projected.setdefault("premium_journals_authority_activation_receipts", []).append(copy.deepcopy(receipt))
    return projected, validate_authority_state(projected, candidate, "activated")


def project_rollback(schedule: dict[str, Any], candidate: dict[str, Any], receipt: dict[str, Any], root: Path | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Return a hypothetical rollback projection.  Never writes or quarantines."""
    root = (root or Path(__file__).resolve().parent).resolve()
    errors = validate_candidate(candidate, root)
    errors.extend(validate_authority_state(schedule, candidate, "activated"))
    errors.extend(validate_rollback_receipt(receipt, candidate))
    review = receipt.get("rollback_review") if isinstance(receipt, dict) else None
    errors.extend(_report_binding_errors(root, review.get("report") if isinstance(review, dict) else None, "rollback_review_report"))
    if _resolve(root, v27.expected_canonical_relative_path(DAY, DAY)).is_file():
        errors.append("rollback_v2_7_canonical_not_quarantined")
    if errors:
        return None, sorted(set(errors))
    projected = copy.deepcopy(schedule)
    routes = projected["routes"]["premium_journals"]
    expected_retired = copy.deepcopy(_expected_v26_route())
    expected_retired["status"] = "retired_by_v2_7_authority_activation"
    expected_retired["authority_retirement_migration_id"] = MIGRATION_ID
    expected_retired["activation_receipt_candidate_fingerprint"] = candidate["record_fingerprint_sha256"]
    matches = [index for index, route in enumerate(routes) if route == expected_retired]
    if len(matches) != 1:
        return None, ["rollback_exact_retired_v2_6_route_count_not_one"]
    routes[matches[0]] = copy.deepcopy(_expected_v26_route())
    active_routes = projected.get("premium_journals_v2_7_authoritative_routes", [])
    expected_active = copy.deepcopy(candidate["proposed_v2_7_authority"]["route"])
    expected_active["status"] = "active_v2_7_authority"
    active_matches = [index for index, route in enumerate(active_routes) if route == expected_active]
    if len(active_matches) != 1:
        return None, ["rollback_exact_active_v2_7_route_count_not_one"]
    del active_routes[active_matches[0]]
    projected.setdefault("premium_journals_authority_rollback_receipts", []).append(copy.deepcopy(receipt))
    return projected, validate_authority_state(projected, candidate, "rollback")


def validate_authority_state(schedule: dict[str, Any], candidate: dict[str, Any], state: str) -> list[str]:
    errors: list[str] = []
    routes = schedule.get("routes", {}).get("premium_journals", [])
    same_day = [route for route in routes if isinstance(route, dict) and route.get("start") == DAY and route.get("end") == DAY]
    active_v26 = [route for route in same_day if route.get("status") != "retired_by_v2_7_authority_activation"]
    same_day_v27 = [route for route in schedule.get("premium_journals_v2_7_authoritative_routes", []) if isinstance(route, dict) and route.get("start") == DAY and route.get("end") == DAY]
    active_v27 = [route for route in same_day_v27 if route.get("status") == "active_v2_7_authority"]
    if state == "before":
        if same_day != [_expected_v26_route()] or len(active_v26) != 1 or same_day_v27:
            errors.append("authority_state_before_invalid")
    elif state == "activated":
        expected_retired = copy.deepcopy(_expected_v26_route())
        expected_retired["status"] = "retired_by_v2_7_authority_activation"
        expected_retired["authority_retirement_migration_id"] = MIGRATION_ID
        expected_retired["activation_receipt_candidate_fingerprint"] = candidate.get("record_fingerprint_sha256")
        expected_active = copy.deepcopy(candidate.get("proposed_v2_7_authority", {}).get("route", {}))
        expected_active["status"] = "active_v2_7_authority"
        if same_day != [expected_retired] or active_v26 or same_day_v27 != [expected_active] or len(active_v27) != 1:
            errors.append("authority_state_activated_count_invalid")
        elif active_v27[0].get("expected_canonical_path") != candidate.get("proposed_v2_7_authority", {}).get("authoritative_canonical_path"):
            errors.append("authority_state_activated_path_invalid")
    elif state == "rollback":
        if same_day != [_expected_v26_route()] or len(active_v26) != 1 or same_day_v27:
            errors.append("authority_state_rollback_invalid")
    else:
        errors.append("authority_state_unknown")
    if active_v26 and active_v27:
        errors.append("authority_state_double_authority")
    return sorted(set(errors))


def write_candidate_exclusive(path: Path | None = None, root: Path | None = None) -> Path:
    root = (root or Path(__file__).resolve().parent).resolve()
    output = path or _resolve(root, CANDIDATE_RELATIVE_PATH)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build_candidate(root)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return output


def build_readiness_report(candidate_path: Path, verification: dict[str, int], root: Path | None = None) -> dict[str, Any]:
    root = (root or Path(__file__).resolve().parent).resolve()
    candidate = _load(candidate_path)
    candidate_errors = validate_candidate(candidate, root)
    source_gate_errors = validate_candidate(candidate, root, require_activation_preconditions=True)
    expected_verification_fields = {"focused_migration_tests_passed", "full_python_discovery_passed", "full_node_tests_passed"}
    if set(verification) != expected_verification_fields or any(type(value) is not int or value <= 0 for value in verification.values()):
        raise ValueError("positive exact verification counts required")
    if candidate_errors or source_gate_errors:
        raise ValueError(f"candidate not readiness-reportable: {candidate_errors + source_gate_errors}")
    candidate_relative = candidate_path.resolve().relative_to(root).as_posix()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_migration_readiness_report",
        "migration_id": MIGRATION_ID,
        "status": "candidate_ready_for_independent_audit_not_activation",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "immutable": True,
        "candidate": _binding(root, candidate_relative, "disabled_authority_migration_candidate"),
        "candidate_record_fingerprint_sha256": candidate["record_fingerprint_sha256"],
        "bound_schedule_snapshot": candidate["source_bindings"]["schedule_snapshot"],
        "bound_jan8_authoritative_canonical": candidate["source_bindings"]["jan8_authoritative_canonical"],
        "bound_jan8_full_shadow_verification": candidate["jan8_shadow_verification"]["full_verification"],
        "baseline_summary": {
            "days": 6,
            "groups": 585,
            "strict_direct": 242,
            "fallback": 343,
            "estimated_savings_percent": 41.37,
        },
        "jan8_shadow_summary": {
            "pages": 7,
            "rows": 162,
            "groups": 78,
            "strict_direct": 36,
            "fallback": 42,
            "all_child_matches": 78,
        },
        "verification": {
            **verification,
            "candidate_validator_passed": True,
            "jan8_authority_source_gate_passed": True,
            "authority_state_before_passed": True,
            "activation_and_rollback_projection_tests_passed": True,
        },
        "activation_controls": _disabled_controls(),
        "not_performed": [
            "independent_audit_approval",
            "activation_receipt_creation",
            "schedule_activation_transaction",
            "jan9_v2_7_collection",
            "jan9_v2_7_canonical_promotion",
        ],
        "next_action": "independent_read_only_audit_of_candidate_and_readiness_report",
    }
    report["record_fingerprint_sha256"] = sha256_json(report)
    return report


def validate_readiness_report(report: dict[str, Any], root: Path | None = None) -> list[str]:
    root = (root or Path(__file__).resolve().parent).resolve()
    errors: list[str] = []
    expected_keys = {"schema_version", "artifact_type", "migration_id", "status", "generated_at_utc", "immutable", "candidate", "candidate_record_fingerprint_sha256", "bound_schedule_snapshot", "bound_jan8_authoritative_canonical", "bound_jan8_full_shadow_verification", "baseline_summary", "jan8_shadow_summary", "verification", "activation_controls", "not_performed", "next_action", "record_fingerprint_sha256"}
    if set(report) != expected_keys:
        errors.append("readiness_top_level_field_set_mismatch")
    expected_values = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_migration_readiness_report",
        "migration_id": MIGRATION_ID,
        "status": "candidate_ready_for_independent_audit_not_activation",
        "immutable": True,
        "activation_controls": _disabled_controls(),
        "baseline_summary": {"days": 6, "groups": 585, "strict_direct": 242, "fallback": 343, "estimated_savings_percent": 41.37},
        "jan8_shadow_summary": {"pages": 7, "rows": 162, "groups": 78, "strict_direct": 36, "fallback": 42, "all_child_matches": 78},
        "not_performed": ["independent_audit_approval", "activation_receipt_creation", "schedule_activation_transaction", "jan9_v2_7_collection", "jan9_v2_7_canonical_promotion"],
        "next_action": "independent_read_only_audit_of_candidate_and_readiness_report",
    }
    if any(report.get(key) != value for key, value in expected_values.items()):
        errors.append("readiness_fixed_contract_mismatch")
    if not v26._is_iso_timestamp(report.get("generated_at_utc")):
        errors.append("readiness_generated_at_invalid")
    unsigned = dict(report)
    unsigned.pop("record_fingerprint_sha256", None)
    if report.get("record_fingerprint_sha256") != sha256_json(unsigned):
        errors.append("readiness_fingerprint_mismatch")
    errors.extend(_binding_errors(root, report.get("candidate"), "readiness_candidate"))
    candidate_binding = report.get("candidate", {})
    try:
        candidate = _load(_resolve(root, str(candidate_binding.get("path") or "")))
    except Exception:
        candidate = {}
        errors.append("readiness_candidate_unreadable")
    if candidate:
        errors.extend(f"readiness_candidate:{item}" for item in validate_candidate(candidate, root, require_activation_preconditions=True))
        if report.get("candidate_record_fingerprint_sha256") != candidate.get("record_fingerprint_sha256"):
            errors.append("readiness_candidate_fingerprint_binding_mismatch")
        comparisons = {
            "bound_schedule_snapshot": candidate.get("source_bindings", {}).get("schedule_snapshot"),
            "bound_jan8_authoritative_canonical": candidate.get("source_bindings", {}).get("jan8_authoritative_canonical"),
            "bound_jan8_full_shadow_verification": candidate.get("jan8_shadow_verification", {}).get("full_verification"),
        }
        for field, expected in comparisons.items():
            if report.get(field) != expected:
                errors.append(f"readiness_{field}_mismatch")
    verification = report.get("verification", {})
    count_fields = {"focused_migration_tests_passed", "full_python_discovery_passed", "full_node_tests_passed"}
    boolean_fields = {"candidate_validator_passed", "jan8_authority_source_gate_passed", "authority_state_before_passed", "activation_and_rollback_projection_tests_passed"}
    if not isinstance(verification, dict) or set(verification) != count_fields | boolean_fields or any(type(verification.get(field)) is not int or verification.get(field) <= 0 for field in count_fields) or any(verification.get(field) is not True for field in boolean_fields):
        errors.append("readiness_verification_invalid")
    return sorted(set(errors))


def write_readiness_exclusive(candidate_path: Path, verification: dict[str, int], path: Path | None = None, root: Path | None = None) -> Path:
    root = (root or Path(__file__).resolve().parent).resolve()
    output = path or _resolve(root, MIGRATION_READINESS_RELATIVE_PATH)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = build_readiness_report(candidate_path, verification, root)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="write the immutable disabled candidate exclusively")
    parser.add_argument("--build-readiness", action="store_true", help="write the immutable audit-readiness report exclusively")
    parser.add_argument("--candidate", type=Path, default=None)
    parser.add_argument("--require-activation-preconditions", action="store_true")
    parser.add_argument("--focused-tests", type=int)
    parser.add_argument("--full-python-tests", type=int)
    parser.add_argument("--full-node-tests", type=int)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.build:
        path = write_candidate_exclusive(args.candidate, root)
        print(json.dumps({"status": "written_disabled_candidate", "path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}, indent=2))
        return 0
    if args.build_readiness:
        candidate_path = args.candidate or _resolve(root, CANDIDATE_RELATIVE_PATH)
        verification = {
            "focused_migration_tests_passed": args.focused_tests,
            "full_python_discovery_passed": args.full_python_tests,
            "full_node_tests_passed": args.full_node_tests,
        }
        path = write_readiness_exclusive(candidate_path, verification, root=root)
        print(json.dumps({"status": "written_audit_readiness_report", "path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}, indent=2))
        return 0
    path = args.candidate or _resolve(root, CANDIDATE_RELATIVE_PATH)
    candidate = _load(path)
    errors = validate_candidate(candidate, root, require_activation_preconditions=args.require_activation_preconditions)
    print(json.dumps({"status": "PASS" if not errors else "FAIL", "activation_preconditions_required": args.require_activation_preconditions, "errors": errors}, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
