"""Fail-closed promotion gate for one V3-authorized Questions v2.6 stage."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import questions_drift_recovery_authorization as recovery

ROOT = Path(__file__).resolve().parent
EXCEPTION_PATH = ROOT / "working/questions_2026-07-14_2026-07-20_post_capture_promotion_exception.json"
EXPECTED_ROUTE = {
    "guild_id": "1167376964680691732", "channel_id": "1273692573898113076",
    "channel_name": "❓│questions", "category_name": "PREMIUM",
    "start": "2026-07-14", "end": "2026-07-20",
    "query": "in:❓│questions after:2026-07-13 before:2026-07-21",
}
EXCEPTION_ROUTE_ID = "questions_2026-07-14_2026-07-20"
EXCEPTION_TARGET_RELATIVE_PATH = (
    "raw/channel_segments/"
    "channel_questions_1273692573898113076_2026-07-14_2026-07-20.json"
)
EXPECTED_PAGE_1_ORDERED_IDS_SHA256 = (
    "b192ae341d4c43e1d9de9b4fb36dc6e97b6d0abb75704df770b3304b795554f7"
)

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ValueError("JSON object required")
    return value

def _path(root: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or Path(value).is_absolute(): return None
    candidate = (root / value).resolve()
    try: candidate.relative_to(root.resolve())
    except ValueError: return None
    return candidate

def _binding_errors(root: Path, binding: Any, role: str) -> list[str]:
    if not isinstance(binding, dict): return [f"{role}_binding_invalid"]
    path = _path(root, binding.get("path"))
    if path is None or not path.is_file(): return [f"{role}_file_missing"]
    errors=[]
    if binding.get("sha256") != sha256_file(path): errors.append(f"{role}_sha256_mismatch")
    if binding.get("bytes") != path.stat().st_size: errors.append(f"{role}_bytes_mismatch")
    return errors

def _candidate_errors(candidate: dict[str, Any], sidecar: dict[str, Any]) -> list[str]:
    errors=[]; query=EXPECTED_ROUTE["query"]
    expected_segment={"start":EXPECTED_ROUTE["start"],"end":EXPECTED_ROUTE["end"],"query":query,"timezone":"America/Chicago"}
    if candidate.get("segment") != expected_segment: errors.append("candidate_segment_not_exact_allowed_timezone_shape")
    if candidate.get("collector_version") != "2.6": errors.append("candidate_collector_version_mismatch")
    if candidate.get("collection_scope") != "channel-scoped" or candidate.get("complete") is not True: errors.append("candidate_scope_or_completion_mismatch")
    if candidate.get("guild_id") != EXPECTED_ROUTE["guild_id"]:
        errors.append("candidate_guild_mismatch")
    requested=candidate.get("requested_container",{})
    if any(requested.get(k)!=v for k,v in {"channel_id":EXPECTED_ROUTE["channel_id"],"channel_name":EXPECTED_ROUTE["channel_name"],"channel_kind":"text channel","category_name":"PREMIUM","channel_id_source":"inventory_exact_href"}.items()): errors.append("candidate_requested_channel_mismatch")
    if candidate.get("resumed_from_partial_rows") != 0 or candidate.get("recovery_execution") is not None: errors.append("candidate_resume_or_embedded_execution_mismatch")
    total=candidate.get("reported_total"); rows=candidate.get("messages")
    if total != 1880 or candidate.get("reported_pages") != 76 or candidate.get("pages_captured") != 76 or candidate.get("captured_rows") != 1880 or candidate.get("unique_message_ids") != 1880: errors.append("candidate_counts_mismatch")
    if candidate.get("gap_indices") not in ([],None) or candidate.get("container_mismatch_count") != 0 or candidate.get("forum_group_navigation_unresolved_count") != 0: errors.append("candidate_scope_counts_mismatch")
    if not isinstance(rows,list) or len(rows)!=1880: errors.append("candidate_rows_mismatch"); rows=[]
    ids=[]
    for index,row in enumerate(rows,1):
        mid=row.get("message_id") if isinstance(row,dict) else None; ids.append(mid)
        expected_permalink=f"https://discord.com/channels/{EXPECTED_ROUTE['guild_id']}/{EXPECTED_ROUTE['channel_id']}/{mid}"
        if not isinstance(mid,str) or not mid.isdigit() or row.get("result_index")!=index or row.get("result_set_size")!=1880 or row.get("search_query")!=query or row.get("collection_channel_id")!=EXPECTED_ROUTE["channel_id"] or row.get("collection_channel_name")!=EXPECTED_ROUTE["channel_name"] or row.get("collection_channel_kind")!="text channel" or row.get("content_scope_exact") is not True or row.get("exact_permalink")!=expected_permalink:
            errors.append(f"candidate_row_mismatch:{index}"); break
    if len(set(ids)) != len(ids): errors.append("candidate_duplicate_ids")
    page_1_ids = [
        row.get("message_id") for row in rows
        if isinstance(row, dict) and row.get("page_number") == 1
    ]
    page_1_ids_sha256 = hashlib.sha256(
        json.dumps(page_1_ids, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if page_1_ids_sha256 != EXPECTED_PAGE_1_ORDERED_IDS_SHA256:
        errors.append("candidate_page_1_ordered_id_set_mismatch")
    completion=candidate.get("completion_evidence",{}); bottom=completion.get("stable_bottom",{}) if isinstance(completion,dict) else {}; observations=bottom.get("observations",[]) if isinstance(bottom,dict) else []
    if candidate.get("completion_evidence_validation",{}).get("valid") is not True or completion.get("terminal_state")!="stable_bottom" or completion.get("reported_total")!=1880 or completion.get("search_submission",{}).get("submission_count")!=1 or len(observations)!=2: errors.append("candidate_completion_mismatch")
    else:
        for obs in observations:
            if not isinstance(obs,dict) or (obs.get("query"),obs.get("current_page"),obs.get("first_result_index"),obs.get("last_result_index"),obs.get("result_set_size"),obs.get("has_enabled_next")) != (query,76,1876,1880,1880,False): errors.append("candidate_bottom_observation_mismatch"); break
    if sidecar.get("route") != EXPECTED_ROUTE:
        errors.append("sidecar_route_mismatch")
    source_candidate = sidecar.get("source_candidate")
    execution=sidecar.get("execution_summary",{})
    expected_execution = {
        "collector_version": "2.6",
        "actual_search_submission_count": 1,
        "old_partial_reuse_rows": 0,
        "same_run_transport_continuation_rows": 0,
        "embedded_recovery_execution_present": False,
        "outer_transport_timeout_occurred": True,
        "original_in_flight_operation_completed_without_second_submission": True,
    }
    if execution != expected_execution: errors.append("sidecar_execution_summary_mismatch")
    observations=sidecar.get("preflight_page_1_observations")
    if not isinstance(observations,list) or len(observations)!=3: errors.append("sidecar_preflight_observation_count_mismatch")
    else:
        expected_observations = [
            {
                "sequence": 1, "observed_at_utc": "2026-07-22T01:57:06.307Z",
                "timestamp_status": "exact_recorded", "state": "positive", "query": query,
                "current_page": 1, "reported_total": 1880, "visible_result_count": 25,
                "first_result_index": 1, "last_result_index": None,
                "last_result_index_status": "not_separately_recorded",
                "panel_identity_tokens": ["1,880 Results", "\u2502questions", "\u2753\u2502questions", "PREMIUM"],
            },
            {
                "sequence": 2, "observed_at_utc": None,
                "timestamp_status": "not_recorded_bounded_after_sequence_1_before_2026-07-22T01:59:01.228Z",
                "state": "positive", "query": query, "current_page": 1, "reported_total": 1880,
                "visible_result_count": 25, "first_result_index": 1, "last_result_index": 25,
                "page_1_ordered_message_id_set_sha256": EXPECTED_PAGE_1_ORDERED_IDS_SHA256,
                "thread_title": "\u2753\u2502questions", "parent_channel": "PREMIUM",
                "all_content_scope_exact": True,
            },
            {
                "sequence": 3, "observed_at_utc": None,
                "timestamp_status": "not_recorded_bounded_after_sequence_2_before_2026-07-22T01:59:01.228Z",
                "state": "positive", "query": query, "current_page": 1, "reported_total": 1880,
                "visible_result_count": 25, "first_result_index": 1, "last_result_index": 25,
                "page_1_ordered_message_id_set_sha256": EXPECTED_PAGE_1_ORDERED_IDS_SHA256,
                "thread_title": "\u2753\u2502questions", "parent_channel": "PREMIUM",
                "all_content_scope_exact": True,
            },
        ]
        for n, expected in enumerate(expected_observations, 1):
            if observations[n - 1] != expected:
                errors.append(f"sidecar_preflight_observation_mismatch:{n}")
    return sorted(set(errors))

def validate_exception(root: Path=ROOT, exception_path: Path=EXCEPTION_PATH, *, require_canonical_absent: bool=True, require_v3_current_schedule: bool=True) -> list[str]:
    root=root.resolve(); errors=[]
    try: exception=_read(exception_path)
    except Exception as exc: return [f"exception_unreadable:{exc}"]
    for key,value in {"schema_version":"1.0.0","artifact_type":"questions_post_capture_promotion_exception","exception_version":1,"exception_scope":"one_exact_staged_v2_6_questions_candidate"}.items():
        if exception.get(key)!=value: errors.append(f"exception_{key}_mismatch")
    binding_keys = ["v3_authorization", "candidate", "execution_sidecar", "collector_contract", "authorized_scope_policy"]
    if require_v3_current_schedule:
        binding_keys.append("bound_schedule")
    for key in binding_keys: errors.extend(_binding_errors(root,exception.get(key),key))
    v3=_path(root,exception.get("v3_authorization",{}).get("path")); candidate_path=_path(root,exception.get("candidate",{}).get("path")); sidecar_path=_path(root,exception.get("execution_sidecar",{}).get("path"))
    if v3 and v3.is_file():
        if require_v3_current_schedule:
            errors.extend(f"v3_invalid:{e}" for e in recovery.validate_authorization(root,v3,require_target_absent=require_canonical_absent))
        else:
            try:
                if exception.get("bound_schedule") != _read(v3).get("bound_schedule"):
                    errors.append("historical_schedule_binding_differs_from_v3")
            except Exception as exc:
                errors.append(f"v3_historical_schedule_binding_unreadable:{exc}")
    if exception.get("route") != EXPECTED_ROUTE: errors.append("exception_route_mismatch")
    truth = exception.get("truthful_collector_output")
    if truth != {
        "collector_version": "2.6", "version_relabeling_forbidden": True,
        "allowed_segment_extra_field": {"timezone": "America/Chicago"},
        "embedded_recovery_execution_required": False, "execution_sidecar_required": True,
    }: errors.append("exception_truthful_collector_output_mismatch")
    constraints = exception.get("promotion_constraints")
    expected_constraints = {
        "candidate_sha256_must_match_exactly": True,
        "candidate_must_remain_preserved_after_atomic_copy": True,
        "canonical_target_must_be_absent_before_promotion": True,
        "old_partial_reuse_rows_required": 0,
        "same_run_transport_continuation_rows_required": 0,
        "actual_search_submission_count_required": 1,
        "preflight_page_1_observation_count_required": 3,
        "terminal_stable_bottom_observation_count_required": 2,
        "all_other_questions_routes_remain_collector_version": "2.5",
    }
    if constraints != expected_constraints: errors.append("exception_promotion_constraints_mismatch")
    target=root / EXCEPTION_TARGET_RELATIVE_PATH
    if require_canonical_absent and target.exists(): errors.append("canonical_target_not_absent")
    try:
        if candidate_path is not None and sidecar_path is not None:
            candidate = _read(candidate_path)
            sidecar = _read(sidecar_path)
            if sidecar.get("source_candidate") != exception.get("candidate"):
                errors.append("sidecar_source_candidate_binding_mismatch")
            errors.extend(_candidate_errors(candidate, sidecar))
    except Exception as exc: errors.append(f"candidate_or_sidecar_unreadable:{exc}")
    return sorted(set(errors))

def validate_promotable_copy(path: Path, root: Path=ROOT, *, require_v3_current_schedule: bool=True, exception_path: Path=EXCEPTION_PATH) -> list[str]:
    root = root.resolve()
    if not require_v3_current_schedule and path.resolve() != (root / EXCEPTION_TARGET_RELATIVE_PATH).resolve():
        return ["historical_v3_schedule_mode_requires_exact_canonical_target"]
    errors=validate_exception(root,exception_path,require_canonical_absent=False,require_v3_current_schedule=require_v3_current_schedule)
    if errors: return errors
    exception=_read(exception_path); binding=exception["candidate"]
    if sha256_file(path)!=binding["sha256"] or path.stat().st_size!=binding["bytes"]: errors.append("promotion_candidate_bytes_or_sha_mismatch")
    return errors


def bound_source_files(root: Path = ROOT) -> list[dict[str, Any]]:
    """Return the immutable exception evidence to bind into the accepted route."""

    exception = _read(root / EXCEPTION_PATH.relative_to(ROOT))
    roles = (
        ("v3_authorization", "v3_authorization"),
        ("post_capture_promotion_exception", None),
        ("post_capture_execution_sidecar", "execution_sidecar"),
        ("collector_contract", "collector_contract"),
        ("authorized_scope_policy", "authorized_scope_policy"),
    )
    bindings: list[dict[str, Any]] = []
    for role, key in roles:
        if key is None:
            path = EXCEPTION_PATH.relative_to(ROOT).as_posix()
            artifact = root / path
            bindings.append(
                {
                    "role": role,
                    "path": path,
                    "sha256": sha256_file(artifact),
                    "bytes": artifact.stat().st_size,
                }
            )
            continue
        binding = exception[key]
        bindings.append(
            {
                "role": role,
                "path": binding["path"],
                "sha256": binding["sha256"],
                "bytes": binding["bytes"],
            }
        )
    return bindings
