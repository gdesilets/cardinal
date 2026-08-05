"""Fail-closed validation for the one authorized Questions count-drift restart."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import authorized_collection_scope
import premium_journals_provenance_contract as premium_contract

ROOT = Path(__file__).resolve().parent
V1_AUTHORIZATION_PATH = (
    ROOT / "working" / "questions_2026-07-14_2026-07-20_drift_recovery_authorization.json"
)
V2_AUTHORIZATION_PATH = (
    ROOT / "working" / "questions_2026-07-14_2026-07-20_drift_recovery_authorization_v2.json"
)
V3_AUTHORIZATION_PATH = (
    ROOT / "working" / "questions_2026-07-14_2026-07-20_drift_recovery_authorization_v3.json"
)
DEFAULT_AUTHORIZATION_PATH = V3_AUTHORIZATION_PATH
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SNOWFLAKE_RE = re.compile(r"^\d{17,20}$")

EXPECTED_ROUTE = {
    "route_id": "questions_2026-07-14_2026-07-20",
    "guild_id": "1167376964680691732",
    "channel_id": "1273692573898113076",
    "channel_name": "❓│questions",
    "channel_kind": "text channel",
    "category_name": "PREMIUM",
    "segment": {
        "start": "2026-07-14",
        "end": "2026-07-20",
        "query": "in:❓│questions after:2026-07-13 before:2026-07-21",
    },
    "expected_canonical_path": (
        "raw/channel_segments/"
        "channel_questions_1273692573898113076_2026-07-14_2026-07-20.json"
    ),
}
EXPECTED_PARTIALS = {
    "first_deferred_partial": (1882, 125, False),
    "stabilization_partial": (1881, 425, False),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("authorization must be a JSON object")
    return data


def _safe_relative_path(root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return None
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _bound_file_errors(root: Path, binding: object, role: str) -> list[str]:
    if not isinstance(binding, dict):
        return [f"{role}_binding_invalid"]
    path = _safe_relative_path(root, binding.get("path"))
    if path is None:
        return [f"{role}_path_invalid"]
    if not path.is_file():
        return [f"{role}_file_missing"]
    errors: list[str] = []
    declared_sha = str(binding.get("sha256") or "").lower()
    if not SHA256_RE.fullmatch(declared_sha):
        errors.append(f"{role}_sha256_invalid")
    elif declared_sha != sha256_file(path):
        errors.append(f"{role}_sha256_mismatch")
    if binding.get("bytes") != path.stat().st_size:
        errors.append(f"{role}_bytes_mismatch")
    return errors


def validate_authorization(
    root: Path = ROOT,
    authorization_path: Path | None = None,
    *,
    require_target_absent: bool = True,
) -> list[str]:
    """Return deterministic errors; an empty list is the only approval state."""

    root = root.resolve()
    path = (authorization_path or DEFAULT_AUTHORIZATION_PATH).resolve()
    errors: list[str] = []
    try:
        authorization = _read_object(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"authorization_unreadable:{exc}"]

    version = authorization.get("authorization_version", 1)
    if version == 3:
        return sorted(
            set(
                _validate_v3_reauthorization(
                    root, authorization, require_target_absent=require_target_absent
                )
            )
        )

    required = {
        "schema_version": "1.0.0",
        "artifact_type": "questions_drift_recovery_authorization",
        "authorization_status": "authorized_not_consumed",
        "authorization_scope": "one_clean_restart_after_count_drift",
        "source_schedule_immutable": True,
        "raw_artifacts_immutable": True,
        "canonical_path_immutable": True,
        "observed_count_history": [1882, 1881, 1880],
        "last_confirmed_reported_total": 1880,
        "count_change_causal_claim": None,
    }
    for key, expected in required.items():
        if authorization.get(key) != expected:
            errors.append(f"authorization_{key}_mismatch")
    if version not in {1, 2}:
        errors.append("authorization_version_invalid")
    if authorization.get("route") != EXPECTED_ROUTE:
        errors.append("authorization_route_mismatch")

    schedule = authorization.get("bound_schedule")
    errors.extend(_bound_file_errors(root, schedule, "bound_schedule"))
    if isinstance(schedule, dict):
        source = _safe_relative_path(root, schedule.get("path"))
        if source is not None and source.is_file():
            try:
                schedule_payload = _read_object(source)
                routes = schedule_payload.get("routes", {}).get("questions", [])
                route = next(
                    (
                        item
                        for item in routes
                        if isinstance(item, dict)
                        and item.get("route_id") == EXPECTED_ROUTE["route_id"]
                    ),
                    None,
                )
                if route is None or route.get("status") != schedule.get("route_status"):
                    errors.append("bound_schedule_route_status_mismatch")
                if route is None or route.get("query") != EXPECTED_ROUTE["segment"]["query"]:
                    errors.append("bound_schedule_route_query_mismatch")
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                errors.append("bound_schedule_json_invalid")
        policy = {
            "source_schedule_requires_checkpoint_resume": True,
            "source_schedule_forbids_new_search_submission_on_resume": True,
            "exception_reason": (
                "reported totals changed during no-submission rerenders, so neither "
                "preserved checkpoint may be resumed"
            ),
        }
        for key, expected in policy.items():
            if schedule.get(key) != expected:
                errors.append(f"bound_schedule_{key}_mismatch")

    evidence = authorization.get("bound_preserved_evidence")
    if not isinstance(evidence, list) or len(evidence) != 4:
        errors.append("bound_preserved_evidence_shape_invalid")
    else:
        roles = [item.get("role") if isinstance(item, dict) else None for item in evidence]
        if len(set(roles)) != len(roles):
            errors.append("bound_preserved_evidence_duplicate_role")
        for binding in evidence:
            role = binding.get("role") if isinstance(binding, dict) else "unknown"
            errors.extend(_bound_file_errors(root, binding, f"evidence_{role}"))
            if role in EXPECTED_PARTIALS and isinstance(binding, dict):
                try:
                    payload = _read_object(_safe_relative_path(root, binding["path"]))
                    expected_total, expected_rows, expected_complete = EXPECTED_PARTIALS[role]
                    if (
                        payload.get("reported_total"),
                        payload.get("captured_rows"),
                        payload.get("complete"),
                    ) != (expected_total, expected_rows, expected_complete):
                        errors.append(f"evidence_{role}_payload_metrics_mismatch")
                except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
                    errors.append(f"evidence_{role}_payload_unreadable")

    target_absence = authorization.get("target_absence")
    target = _safe_relative_path(root, EXPECTED_ROUTE["expected_canonical_path"])
    if target_absence != {"must_be_absent_at_authorization": True, "target_exists": False}:
        errors.append("target_absence_declaration_mismatch")
    if target is None or (require_target_absent and target.exists()):
        errors.append("canonical_target_not_absent")

    permission = authorization.get("restart_permission")
    expected_permission = {
        "allowed_clean_restart_count": 1,
        "restart_number": 1,
        "fresh_search_submission_required": True,
        "resumed_from_partial_rows_required": 0,
        "merge_or_relabel_preserved_partials_forbidden": True,
        "new_staging_directory_prefix": "working/questions_2026-07-14_2026-07-20_drift_recovery/",
        "atomic_canonical_promotion_only_after_full_validation": True,
        "stop_and_quarantine_on_any_total_change_render_gap_or_scope_mismatch": True,
    }
    if permission != expected_permission:
        errors.append("restart_permission_mismatch")

    criteria = authorization.get("stable_run_acceptance_criteria")
    if not isinstance(criteria, dict):
        errors.append("stable_run_acceptance_criteria_missing")
    else:
        required_criteria = {
            ("preflight", "minimum_exact_page_1_observations"): 3,
            ("preflight", "all_observations_require_exact_query_and_channel_identity"): True,
            ("preflight", "all_observations_require_same_positive_reported_total"): True,
            ("capture", "every_page_must_report_the_same_total"): True,
            ("capture", "all_result_indices_must_be_contiguous_from_1_through_reported_total"): True,
            ("capture", "all_message_ids_must_be_unique_exact_snowflakes"): True,
            ("capture", "all_rows_require_exact_questions_channel_scope_and_permalink"): True,
            ("capture", "reported_pages_must_equal_ceil_reported_total_div_25"): True,
            ("terminal", "positive_result_terminal_state"): "stable_bottom",
            ("terminal", "stable_bottom_observation_count"): 2,
            ("terminal", "each_bottom_observation_requires_last_index_equal_reported_total"): True,
            ("terminal", "each_bottom_observation_requires_result_set_size_equal_reported_total"): True,
            ("terminal", "each_bottom_observation_requires_next_disabled"): True,
            ("terminal", "zero_result_terminal_state"): "stable_empty",
            ("terminal", "stable_empty_observation_count"): 3,
            ("promotion", "strict_questions_schedule_acceptance_required"): True,
            ("promotion", "strict_segment_qa_required"): True,
            ("promotion", "canonical_target_must_be_written_atomically"): True,
        }
        for (section, key), expected in required_criteria.items():
            if not isinstance(criteria.get(section), dict) or criteria[section].get(key) != expected:
                errors.append(f"stable_run_criterion_missing:{section}.{key}")
    if version == 2:
        errors.extend(_validate_v2_reauthorization(root, authorization))
    return sorted(set(errors))


def _validate_v2_reauthorization(root: Path, authorization: dict[str, Any]) -> list[str]:
    """V2 may advance Premium only; all Questions authorization state must equal V1."""

    errors: list[str] = []
    supersedes = authorization.get("supersedes")
    errors.extend(_bound_file_errors(root, supersedes, "supersedes"))
    if not isinstance(supersedes, dict):
        return errors
    prior_path = _safe_relative_path(root, supersedes.get("path"))
    if prior_path is None or not prior_path.is_file():
        return errors
    try:
        prior = _read_object(prior_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return errors + ["supersedes_json_invalid"]
    if prior.get("authorization_version", 1) != 1:
        errors.append("supersedes_not_v1")
    for key in (
        "route",
        "bound_preserved_evidence",
        "observed_count_history",
        "last_confirmed_reported_total",
        "count_change_causal_claim",
        "target_absence",
        "restart_permission",
        "stable_run_acceptance_criteria",
    ):
        if authorization.get(key) != prior.get(key):
            errors.append(f"v2_{key}_differs_from_v1")

    revalidation = authorization.get("questions_state_revalidation")
    expected_revalidation_flags = {
        "route_equals_v1": True,
        "preserved_evidence_equals_v1": True,
        "target_absence_equals_v1": True,
        "count_history_equals_v1": True,
        "restart_permission_equals_v1": True,
        "stable_run_acceptance_criteria_equals_v1": True,
    }
    if not isinstance(revalidation, dict):
        return errors + ["questions_state_revalidation_missing"]
    for key, expected in expected_revalidation_flags.items():
        if revalidation.get(key) != expected:
            errors.append(f"questions_state_revalidation_{key}_mismatch")

    schedule_path = _safe_relative_path(root, authorization.get("bound_schedule", {}).get("path"))
    try:
        schedule = _read_object(schedule_path) if schedule_path is not None else {}
        parents = schedule.get("parents") if isinstance(schedule.get("parents"), list) else []
        by_id = {
            parent.get("channel_id"): parent
            for parent in parents
            if isinstance(parent, dict) and isinstance(parent.get("channel_id"), str)
        }
        expected_parents = {
            "1273692573898113076": revalidation.get("questions_parent_summary"),
            "1370578463223975986": revalidation.get("student_parent_summary"),
            "1283941772577472643": revalidation.get("premium_progress_summary"),
        }
        for channel_id, expected in expected_parents.items():
            actual = by_id.get(channel_id)
            if not isinstance(expected, dict) or not isinstance(actual, dict):
                errors.append(f"revalidated_parent_missing:{channel_id}")
                continue
            for key, value in expected.items():
                if actual.get(key) != value:
                    errors.append(f"revalidated_parent_mismatch:{channel_id}.{key}")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        errors.append("revalidated_schedule_unreadable")
    return errors


def _validate_v3_reauthorization(
    root: Path, authorization: dict[str, Any], *, require_target_absent: bool
) -> list[str]:
    """V3 inherits V2 intact and authorizes only the exact 2.6 text output."""

    errors: list[str] = []
    for key, expected in {
        "schema_version": "1.0.0",
        "artifact_type": "questions_drift_recovery_authorization",
        "authorization_version": 3,
        "authorization_status": "authorized_not_consumed",
        "authorization_scope": "one_clean_restart_after_count_drift",
    }.items():
        if authorization.get(key) != expected:
            errors.append(f"v3_{key}_mismatch")
    supersedes = authorization.get("supersedes")
    errors.extend(_bound_file_errors(root, supersedes, "supersedes"))
    if not isinstance(supersedes, dict):
        return errors
    prior_path = _safe_relative_path(root, supersedes.get("path"))
    if prior_path is None or not prior_path.is_file():
        return errors
    try:
        prior = _read_object(prior_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return errors + ["supersedes_json_invalid"]
    if prior.get("authorization_version") != 2:
        errors.append("supersedes_not_v2")
    prior_errors = validate_authorization(
        root, prior_path, require_target_absent=require_target_absent
    )
    if prior_errors:
        errors.extend(f"superseded_v2_not_valid:{error}" for error in prior_errors)

    if authorization.get("bound_schedule") != {
        key: prior.get("bound_schedule", {}).get(key)
        for key in ("path", "sha256", "bytes")
    }:
        errors.append("v3_bound_schedule_differs_from_v2")
    inherited = authorization.get("inherited_v2_authorization_state")
    expected_inherited = {
        "exact_route_required": True,
        "preserved_evidence_hashes_required": True,
        "target_absence_required": True,
        "one_clean_restart_only": True,
        "fresh_search_and_zero_resumption_required": True,
        "stable_run_acceptance_criteria_required": True,
    }
    if inherited != expected_inherited:
        errors.append("v3_inherited_v2_authorization_state_mismatch")

    compatibility = authorization.get("collector_compatibility")
    if not isinstance(compatibility, dict):
        return errors + ["collector_compatibility_missing"]
    if compatibility.get("collector_version_required") != "2.6":
        errors.append("collector_compatibility_version_mismatch")
    if compatibility.get("collector_version_relabeling_forbidden") is not True:
        errors.append("collector_compatibility_relabeling_guard_missing")
    contract = compatibility.get("collector_contract")
    policy = compatibility.get("authorized_scope_policy")
    errors.extend(_bound_file_errors(root, contract, "collector_contract"))
    errors.extend(_bound_file_errors(root, policy, "authorized_scope_policy"))
    if isinstance(contract, dict):
        if contract.get("contract_version") != premium_contract.COLLECTOR_VERSION:
            errors.append("collector_contract_version_mismatch")
        if contract.get("forum_scope_parent_channel_id") != premium_contract.PREMIUM_ID:
            errors.append("collector_contract_forum_scope_mismatch")
    expected_policy = {
        "questions_authoritative_directory": "raw/channel_segments",
        "questions_collector_version_requirement": None,
        "premium_authoritative_directory": "raw/channel_segments_v2_5",
        "premium_collector_version_requirement": "2.6",
    }
    if isinstance(policy, dict):
        for key, expected in expected_policy.items():
            if policy.get(key) != expected:
                errors.append(f"authorized_scope_policy_{key}_mismatch")
    scope_policy = authorized_collection_scope.CANONICAL_PATH_POLICY
    if scope_policy.get("questions") != {"authoritative_directory": "raw/channel_segments"}:
        errors.append("runtime_questions_scope_policy_incompatible")
    if scope_policy.get("premium_journals", {}).get("collector_version_required") != "2.6":
        errors.append("runtime_premium_scope_policy_incompatible")
    semantics = compatibility.get("text_channel_semantics")
    expected_semantics = {
        "collection_scope": "channel-scoped",
        "requested_channel_kind": "text channel",
        "exact_questions_channel_id_required": EXPECTED_ROUTE["channel_id"],
        "result_index_continuity_required": True,
        "completion_evidence_required": True,
        "forum_navigation_evidence_not_required_for_questions": True,
    }
    if semantics != expected_semantics:
        errors.append("text_channel_semantics_mismatch")
    return errors


def _effective_policy_authorization(
    root: Path, authorization: dict[str, Any]
) -> dict[str, Any]:
    """V3 inherits operational restart criteria from its validated V2 predecessor."""

    if authorization.get("authorization_version") != 3:
        return authorization
    supersedes = authorization.get("supersedes")
    if not isinstance(supersedes, dict):
        return authorization
    prior_path = _safe_relative_path(root, supersedes.get("path"))
    if prior_path is None or not prior_path.is_file():
        return authorization
    try:
        return _read_object(prior_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return authorization


def validate_clean_restart_candidate(
    candidate: dict[str, Any], authorization_path: Path = DEFAULT_AUTHORIZATION_PATH
) -> list[str]:
    """Validate a staged full run before its atomic move to the canonical target."""

    errors = validate_authorization(ROOT, authorization_path)
    if errors:
        return [f"authorization_not_usable:{error}" for error in errors]
    authorization = _read_object(authorization_path)
    policy_authorization = _effective_policy_authorization(ROOT, authorization)
    expected_query = EXPECTED_ROUTE["segment"]["query"]
    reported = candidate.get("reported_total")
    messages = candidate.get("messages")
    execution = candidate.get("recovery_execution")
    if not isinstance(execution, dict):
        errors.append("recovery_execution_missing")
    else:
        authorization_sha = sha256_file(authorization_path)
        expected_execution = {
            "authorization_path": authorization_path.relative_to(ROOT).as_posix(),
            "authorization_sha256": authorization_sha,
            "restart_number": 1,
            "fresh_search_submission_count": 1,
            "resumed_from_partial_rows": 0,
            "promotion_mode": "atomic_after_full_validation",
        }
        for key, expected in expected_execution.items():
            if execution.get(key) != expected:
                errors.append(f"recovery_execution_{key}_mismatch")
        staging = _safe_relative_path(ROOT, execution.get("staging_path"))
        prefix = policy_authorization["restart_permission"]["new_staging_directory_prefix"]
        if staging is None or not str(execution.get("staging_path")).replace("\\", "/").startswith(prefix):
            errors.append("recovery_execution_staging_path_invalid")

    requested = candidate.get("requested_container")
    expected_requested = {
        "channel_id": EXPECTED_ROUTE["channel_id"],
        "channel_name": EXPECTED_ROUTE["channel_name"],
        "channel_kind": EXPECTED_ROUTE["channel_kind"],
        "category_name": EXPECTED_ROUTE["category_name"],
        "channel_id_source": "inventory_exact_href",
    }
    if candidate.get("guild_id") != EXPECTED_ROUTE["guild_id"]:
        errors.append("candidate_guild_mismatch")
    if candidate.get("collection_scope") != "channel-scoped":
        errors.append("candidate_collection_scope_mismatch")
    required_collector_version = (
        "2.6" if authorization.get("authorization_version") == 3 else "2.5"
    )
    if candidate.get("collector_version") != required_collector_version:
        errors.append("candidate_collector_version_mismatch")
    if candidate.get("segment") != EXPECTED_ROUTE["segment"]:
        errors.append("candidate_segment_mismatch")
    if requested != expected_requested:
        errors.append("candidate_requested_container_mismatch")
    if candidate.get("complete") is not True:
        errors.append("candidate_not_complete")
    if candidate.get("resumed_from_partial_rows") != 0:
        errors.append("candidate_resumed_rows_not_zero")
    if not isinstance(reported, int) or reported < 0:
        errors.append("candidate_reported_total_invalid")
        reported = -1
    if candidate.get("reported_pages") != (math.ceil(reported / 25) if reported > 0 else 0):
        errors.append("candidate_reported_pages_mismatch")
    if not isinstance(messages, list) or len(messages) != reported:
        errors.append("candidate_message_count_mismatch")
        messages = []
    if candidate.get("captured_rows") != len(messages) or candidate.get("unique_message_ids") != len(messages):
        errors.append("candidate_declared_counts_mismatch")
    if candidate.get("gap_indices") not in ([], None):
        errors.append("candidate_gap_indices_nonempty")
    if candidate.get("container_mismatch_count") != 0:
        errors.append("candidate_container_mismatch")

    message_ids: list[str] = []
    for index, row in enumerate(messages, start=1):
        if not isinstance(row, dict):
            errors.append("candidate_message_not_object")
            continue
        message_id = row.get("message_id")
        message_ids.append(message_id if isinstance(message_id, str) else "")
        expected_permalink = (
            f"https://discord.com/channels/{EXPECTED_ROUTE['guild_id']}/"
            f"{EXPECTED_ROUTE['channel_id']}/{message_id}"
        )
        if (
            not isinstance(message_id, str)
            or not SNOWFLAKE_RE.fullmatch(message_id)
            or row.get("result_index") != index
            or row.get("result_set_size") != reported
            or row.get("search_query") != expected_query
            or row.get("collection_channel_id") != EXPECTED_ROUTE["channel_id"]
            or row.get("collection_channel_name") != EXPECTED_ROUTE["channel_name"]
            or row.get("content_scope_exact") is not True
            or row.get("exact_permalink") != expected_permalink
        ):
            errors.append(f"candidate_row_scope_or_index_mismatch:{index}")
            break
    if len(set(message_ids)) != len(message_ids):
        errors.append("candidate_duplicate_message_ids")

    completion = candidate.get("completion_evidence")
    if not isinstance(completion, dict):
        errors.append("candidate_completion_evidence_missing")
    elif reported > 0:
        observations = completion.get("stable_bottom", {}).get("observations", [])
        expected_first = ((math.ceil(reported / 25)) - 1) * 25 + 1
        if completion.get("terminal_state") != "stable_bottom" or len(observations) != 2:
            errors.append("candidate_stable_bottom_missing")
        else:
            for observation in observations:
                if not isinstance(observation, dict) or any(
                    (
                        observation.get("query") != expected_query,
                        observation.get("current_page") != math.ceil(reported / 25),
                        observation.get("first_result_index") != expected_first,
                        observation.get("last_result_index") != reported,
                        observation.get("result_set_size") != reported,
                        observation.get("has_enabled_next") is not False,
                    )
                ):
                    errors.append("candidate_stable_bottom_observation_mismatch")
                    break
    else:
        observations = completion.get("stable_empty", {}).get("observations", [])
        if completion.get("terminal_state") != "stable_empty" or len(observations) != 3:
            errors.append("candidate_stable_empty_missing")
    return sorted(set(errors))
