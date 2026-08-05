from __future__ import annotations

"""Policy-aware release evaluation for the Discord relevance collection plan.

This module is deliberately read-only.  It does not collect Discord data and it
does not infer trading claims.  It turns the validated collection plan, strict
collector segments, and separately provenance-backed review/reconciliation
evidence into fail-closed release gates shared by the corpus builder and the
independent QA tool.
"""

import copy
import datetime as dt
import importlib.util
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

import reply_provenance_contract


ALLOWED_POLICIES = {
    "full_capture",
    "verified_empty_full_window",
    "targeted_search_plus_residual_audit",
}
TARGETED_POLICY = "targeted_search_plus_residual_audit"
FULL_POLICIES = {"full_capture", "verified_empty_full_window"}
PASS_STATES = {"pass", "passed", "complete", "completed", "ok", "verified"}
DISCORD_ID_RE = re.compile(r"^\d{15,22}$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
DATE_OPERATOR_RE = re.compile(
    r"(?:^|\s)(?:after|before):\d{4}-\d{2}-\d{2}(?=\s|$)", re.I
)
SUPPORTED_HARD_GATES = {
    "inventory_exact",
    "inventory_post_cutoff_authenticated",
    "window_final",
    "full_capture_segment_coverage",
    "full_capture_count_reconciliation",
    "targeted_query_matrix",
    "targeted_not_mislabeled",
    "residual_audit",
    "query_overlap_provenance",
    "reply_resolution",
    "forum_exact_ids",
    "thread_inventory_complete",
    "attachments_and_chart_dependence",
    "discord_only",
    "claim_calibration",
}


class RelevancePolicyError(RuntimeError):
    """Raised when a supplied relevance policy artifact is unreadable."""


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RelevancePolicyError(f"Could not read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RelevancePolicyError(f"{label} {path} must contain a top-level object")
    return payload


def load_validated_plan(
    plan_path: Path,
    inventory_path: Path | None,
    *,
    check_source_hashes: bool = True,
) -> dict[str, Any]:
    """Load a plan and run the canonical plan validator without mutating it."""

    plan_path = plan_path.resolve()
    validator_path = Path(__file__).resolve().parent / "qa" / "validate_relevance_plan.py"
    spec = importlib.util.spec_from_file_location(
        "discord_relevance_plan_validator_for_release", validator_path
    )
    if spec is None or spec.loader is None:
        raise RelevancePolicyError(f"Could not load relevance-plan validator {validator_path}")
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    report = validator.validate_plan(
        plan_path,
        inventory_path.resolve() if inventory_path else None,
        check_source_hashes=check_source_hashes,
    )
    jobs = report.pop("expanded_jobs", [])
    plan = load_json_object(plan_path, "relevance plan")
    return {
        "provided": True,
        "valid": report.get("status") == "passed",
        "path": str(plan_path),
        "validation": report,
        "plan": plan,
        "expanded_jobs": jobs,
    }


def normalize_query(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def query_core(value: Any) -> str:
    return " ".join(DATE_OPERATOR_RE.sub(" ", str(value or "")).casefold().split())


def parse_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def parse_utc(value: Any) -> dt.datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def date_set(start: dt.date, end: dt.date) -> set[dt.date]:
    if start > end:
        return set()
    return {start + dt.timedelta(days=offset) for offset in range((end - start).days + 1)}


def _status_passed(value: Any) -> bool:
    return str(value or "").strip().casefold() in PASS_STATES


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        output: list[dict[str, Any]] = []
        for key, row in value.items():
            if not isinstance(row, dict):
                continue
            copied = copy.deepcopy(row)
            copied.setdefault("channel_id", str(key))
            output.append(copied)
        return output
    return []


def _evidence_refs(row: dict[str, Any]) -> list[str]:
    refs: set[str] = set()
    for key in (
        "evidence_refs",
        "source_file_ids",
        "segment_ids",
        "observation_ids",
        "review_artifact_ids",
    ):
        value = row.get(key)
        if isinstance(value, list):
            refs.update(str(item).strip() for item in value if str(item).strip())
    for key in ("evidence_ref", "source_file_id", "observation_id", "review_artifact_id"):
        value = str(row.get(key) or "").strip()
        if value:
            refs.add(value)
    return sorted(refs)


def _release_evidence(progress: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(progress, dict):
        return {}
    for key in ("release_evidence", "policy_release_evidence", "review_evidence"):
        value = progress.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _release_evidence_envelope(
    evidence: dict[str, Any], required_cutoff: dt.datetime
) -> dict[str, Any]:
    """Validate the generator-owned envelope before trusting managed gates.

    Individual managed rows can all look passed while the generator has marked
    the bundle pending because a raw/progress consistency check failed.  The
    envelope therefore participates in every evidence-backed hard gate.
    """

    errors: list[str] = []
    if evidence.get("artifact_type") != "discord_release_evidence":
        errors.append("unexpected_artifact_type")
    if not _status_passed(evidence.get("status")):
        errors.append("release_evidence_status_not_complete")
    outside_sources = evidence.get("outside_sources_used")
    if not (outside_sources is False or (type(outside_sources) is int and outside_sources == 0)):
        errors.append("outside_sources_used_not_zero")
    declared_cutoff = parse_utc(evidence.get("required_cutoff_utc"))
    if declared_cutoff != required_cutoff.astimezone(dt.timezone.utc):
        errors.append("required_cutoff_mismatch")
    generated_at = parse_utc(evidence.get("generated_at_utc"))
    if generated_at is None or generated_at < required_cutoff:
        errors.append("generated_before_required_cutoff")

    generator = evidence.get("generator") if isinstance(evidence.get("generator"), dict) else {}
    if generator.get("local_only") is not True:
        errors.append("generator_not_declared_local_only")
    for field in ("browser_calls_made", "network_calls_made", "raw_files_modified"):
        try:
            if int(generator.get(field)) != 0:
                errors.append(f"generator_{field}_not_zero")
        except (TypeError, ValueError):
            errors.append(f"generator_{field}_missing_or_invalid")

    sources = evidence.get("source_artifacts")
    if not isinstance(sources, list) or not sources:
        errors.append("source_artifacts_missing")
        sources = []
    invalid_sources = [
        index
        for index, row in enumerate(sources)
        if not isinstance(row, dict)
        or not str(row.get("kind") or "").strip()
        or not str(row.get("path") or "").strip()
        or not SHA256_RE.fullmatch(str(row.get("sha256") or ""))
        or not isinstance(row.get("size_bytes"), int)
        or int(row.get("size_bytes")) < 0
    ]
    if invalid_sources:
        errors.append("source_artifacts_invalid")
    return {
        "passed": not errors,
        "errors": errors,
        "required_cutoff_utc": required_cutoff.astimezone(dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "generated_at_utc": evidence.get("generated_at_utc"),
        "source_artifact_count": len(sources),
        "invalid_source_artifact_indices": invalid_sources[:100],
    }


def _plan_policy_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("channel_id")): row
        for row in plan.get("channel_policies", [])
        if isinstance(row, dict) and DISCORD_ID_RE.fullmatch(str(row.get("channel_id") or ""))
    }


def _inventory_top_level_ids(inventory: dict[str, Any]) -> set[str]:
    raw_rows = inventory.get("containers")
    if not isinstance(raw_rows, list):
        raw_rows = inventory.get("channels")
    rows = raw_rows if isinstance(raw_rows, list) else []
    result: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        layer = str(row.get("inventory_layer") or "top_level_container")
        if layer not in {"", "top_level_container"}:
            continue
        channel_id = str(
            row.get("container_id") or row.get("channel_id") or row.get("id") or ""
        )
        if DISCORD_ID_RE.fullmatch(channel_id):
            result.add(channel_id)
    return result


def classify_segments(
    plan: dict[str, Any], segments: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Add policy roles without altering the source segment records."""

    policies = _plan_policy_map(plan)
    classified: list[dict[str, Any]] = []
    for source in segments:
        row = copy.deepcopy(source)
        channel_id = str(row.get("query_container_id") or row.get("channel_id") or "")
        policy_row = policies.get(channel_id, {})
        policy = str(policy_row.get("policy") or "unplanned")
        input_role = str(row.get("input_role") or "channel_capture")
        expected_unfiltered_core = query_core(f"in:{policy_row.get('name') or ''}")
        observed_core = query_core(row.get("query"))
        is_unfiltered = bool(expected_unfiltered_core and observed_core == expected_unfiltered_core)
        if policy in FULL_POLICIES and input_role == "channel_capture" and is_unfiltered:
            role = "required_full_capture"
        elif policy == TARGETED_POLICY and input_role == "relevance_query":
            role = "required_targeted_query"
        elif policy == TARGETED_POLICY and input_role == "residual_audit" and is_unfiltered:
            role = "required_residual_audit"
        elif policy == TARGETED_POLICY and input_role == "channel_capture" and is_unfiltered:
            role = "diagnostic_targeted_full_capture"
        else:
            role = "unmatched_policy_evidence"
        row["channel_policy"] = policy
        row["policy_role"] = role
        row["query_core"] = observed_core
        classified.append(row)
    return classified


def _segment_job_coverage(
    job: dict[str, Any], classified_segments: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    args = job.get("args") if isinstance(job.get("args"), dict) else {}
    options = args.get("collectorOptions") if isinstance(args.get("collectorOptions"), dict) else {}
    channel_id = str(options.get("channelId") or "")
    expected_core = query_core(args.get("queryPrefix"))
    expected_start = parse_date(args.get("startIso"))
    expected_end = parse_date(args.get("endIso"))
    expected_dates = (
        date_set(expected_start, expected_end) if expected_start and expected_end else set()
    )
    kind = str(job.get("job_kind") or "")
    required_role = {
        "full_capture_or_empty_verification": "required_full_capture",
        "targeted_search": "required_targeted_query",
        "residual_audit_census_day": "required_residual_audit",
    }.get(kind)
    candidates: list[dict[str, Any]] = []
    coverage_counts: Counter[dt.date] = Counter()
    for segment in classified_segments:
        if str(segment.get("query_container_id") or "") != channel_id:
            continue
        if required_role and segment.get("policy_role") != required_role:
            continue
        if query_core(segment.get("query")) != expected_core:
            continue
        start = parse_date(segment.get("start_date"))
        end = parse_date(segment.get("end_date"))
        if not start or not end or not expected_dates:
            continue
        if start < expected_start or end > expected_end:
            continue
        candidates.append(segment)
        if segment.get("computed_complete"):
            for day in date_set(start, end):
                coverage_counts[day] += 1
    missing = sorted(expected_dates - set(coverage_counts))
    overlap = sorted(day for day, count in coverage_counts.items() if count > 1)
    complete_candidates = [row for row in candidates if row.get("computed_complete")]
    passed = bool(expected_dates) and not missing
    return {
        "job_id": job.get("job_id"),
        "job_kind": kind,
        "channel_id": channel_id,
        "channel_policy": job.get("channel_policy"),
        "query_core": expected_core,
        "passed": passed,
        "expected_day_count": len(expected_dates),
        "covered_day_count": len(expected_dates & set(coverage_counts)),
        "missing_dates": [day.isoformat() for day in missing],
        "overlap_dates": [day.isoformat() for day in overlap],
        "complete_segment_ids": sorted(
            str(row.get("segment_id") or "") for row in complete_candidates
        ),
        "partial_segment_ids": sorted(
            str(row.get("segment_id") or "")
            for row in candidates
            if not row.get("computed_complete")
        ),
        "reported_total_sum": sum(int(row.get("reported_total") or 0) for row in complete_candidates),
        "complete_segments": [
            {
                "segment_id": str(row.get("segment_id") or ""),
                "start_date": row.get("start_date"),
                "end_date": row.get("end_date"),
                "reported_total": int(row.get("reported_total") or 0),
            }
            for row in complete_candidates
        ],
    }


def _progress_jobs(progress: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    rows = progress.get("jobs") if isinstance(progress, dict) else None
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("job_id")): row
        for row in rows
        if isinstance(row, dict) and str(row.get("job_id") or "")
    }


def _progress_consistency(
    expected_jobs: Sequence[dict[str, Any]], progress: dict[str, Any] | None
) -> dict[str, Any]:
    if progress is None:
        return {"provided": False, "passed": True, "errors": [], "job_count": 0}
    errors: list[str] = []
    if progress.get("artifact_type") != "discord_collection_progress_manifest":
        errors.append("unexpected_progress_artifact_type")
    policy = progress.get("source_policy") if isinstance(progress.get("source_policy"), dict) else {}
    if int(policy.get("browser_calls_made") or 0) != 0:
        errors.append("progress_manifest_made_browser_calls")
    if int(policy.get("raw_files_modified") or 0) != 0:
        errors.append("progress_manifest_modified_raw_files")
    expected_by_id = {str(row.get("job_id")): row for row in expected_jobs}
    observed_by_id = _progress_jobs(progress)
    if set(expected_by_id) != set(observed_by_id):
        missing = sorted(set(expected_by_id) - set(observed_by_id))
        extra = sorted(set(observed_by_id) - set(expected_by_id))
        if missing:
            errors.append(f"progress_missing_expected_jobs:{len(missing)}")
        if extra:
            errors.append(f"progress_has_unplanned_jobs:{len(extra)}")
    for job_id in sorted(set(expected_by_id) & set(observed_by_id)):
        expected = expected_by_id[job_id]
        observed = observed_by_id[job_id]
        args = expected.get("args") if isinstance(expected.get("args"), dict) else {}
        options = args.get("collectorOptions") if isinstance(args.get("collectorOptions"), dict) else {}
        comparisons = {
            "job_kind": expected.get("job_kind"),
            "channel_id": options.get("channelId"),
            "query_prefix": args.get("queryPrefix"),
        }
        for field, expected_value in comparisons.items():
            if normalize_query(observed.get(field)) != normalize_query(expected_value):
                errors.append(f"progress_job_mismatch:{job_id}:{field}")
    return {
        "provided": True,
        "passed": not errors,
        "errors": errors,
        "job_count": len(observed_by_id),
    }


def _count_reconciliation(
    full_jobs: Sequence[dict[str, Any]],
    evidence: dict[str, Any],
    required_cutoff: dt.datetime,
) -> dict[str, Any]:
    rows = _rows(
        evidence.get("full_capture_count_reconciliation")
        or evidence.get("count_reconciliation")
    )
    by_channel = {str(row.get("channel_id") or ""): row for row in rows}
    results: list[dict[str, Any]] = []
    for job in full_jobs:
        channel_id = str(job.get("channel_id") or "")
        row = by_channel.get(channel_id, {})
        observed_at = parse_utc(
            row.get("observed_at_utc")
            or row.get("data_cutoff_utc")
            or row.get("refreshed_at_utc")
        )
        complete_segments = [
            item
            for item in (job.get("complete_segments") or [])
            if isinstance(item, dict) and str(item.get("segment_id") or "")
        ]
        complete_by_id = {
            str(item.get("segment_id")): item for item in complete_segments
        }
        requested_ids = [
            str(item)
            for item in (row.get("segment_ids") or [])
            if str(item)
        ]
        selection_unambiguous = True
        if complete_segments:
            if requested_ids:
                selection_unambiguous = set(requested_ids) <= set(complete_by_id)
                selected = [
                    complete_by_id[segment_id]
                    for segment_id in requested_ids
                    if segment_id in complete_by_id
                ]
            elif job.get("overlap_dates"):
                selection_unambiguous = False
                selected = []
            else:
                selected = complete_segments
                requested_ids = sorted(complete_by_id)
            selected_dates: set[dt.date] = set()
            for item in selected:
                start = parse_date(item.get("start_date"))
                end = parse_date(item.get("end_date"))
                if start and end:
                    selected_dates.update(date_set(start, end))
            selected_coverage_complete = (
                len(selected_dates) == int(job.get("expected_day_count") or 0)
            )
            raw_selected_total = sum(
                int(item.get("reported_total") or 0) for item in selected
            )
        else:
            selected = []
            selected_coverage_complete = bool(job.get("passed", True))
            raw_selected_total = int(job.get("reported_total_sum") or 0)
        deletion_refs = [
            str(item).strip()
            for item in (row.get("discord_edit_deletion_provenance_refs") or [])
            if str(item).strip()
        ]
        excluded_nonzero = [
            str(item.get("segment_id"))
            for item in complete_segments
            if str(item.get("segment_id")) not in set(requested_ids)
            and int(item.get("reported_total") or 0) != 0
        ]
        deletion_provenance_ok = not excluded_nonzero or bool(deletion_refs)
        try:
            segment_total = int(row.get("segment_reported_total"))
            refreshed_total = int(
                row.get("refreshed_full_window_reported_total", row.get("full_window_reported_total"))
            )
            totals_match = segment_total == refreshed_total == raw_selected_total
        except (TypeError, ValueError):
            segment_total = refreshed_total = None
            totals_match = False
        passed = bool(
            row
            and job.get("passed", True)
            and _status_passed(row.get("status"))
            and observed_at is not None
            and observed_at >= required_cutoff
            and totals_match
            and selection_unambiguous
            and selected_coverage_complete
            and deletion_provenance_ok
            and (
                job.get("channel_policy") != "verified_empty_full_window"
                or refreshed_total == 0
            )
            and _evidence_refs(row)
        )
        results.append(
            {
                "channel_id": channel_id,
                "passed": passed,
                "segment_reported_total": segment_total,
                "refreshed_full_window_reported_total": refreshed_total,
                "raw_selected_segment_total": raw_selected_total,
                "selected_segment_ids": requested_ids,
                "selection_unambiguous": selection_unambiguous,
                "selected_coverage_complete": selected_coverage_complete,
                "excluded_nonzero_segment_ids": excluded_nonzero,
                "discord_edit_deletion_provenance_refs": deletion_refs,
                "observed_at_utc": str(
                    row.get("observed_at_utc")
                    or row.get("data_cutoff_utc")
                    or row.get("refreshed_at_utc")
                    or ""
                )
                or None,
                "evidence_refs": _evidence_refs(row),
            }
        )
    return {
        "passed": bool(results) and all(row["passed"] for row in results),
        "required_channel_count": len(full_jobs),
        "passed_channel_count": sum(row["passed"] for row in results),
        "channels": results,
    }


def _residual_reviews(
    audit_jobs: Sequence[dict[str, Any]], evidence: dict[str, Any]
) -> dict[str, Any]:
    rows = _rows(evidence.get("residual_reviews") or evidence.get("residual_audit_reviews"))
    by_job = {str(row.get("job_id") or ""): row for row in rows}
    results: list[dict[str, Any]] = []
    for job in audit_jobs:
        job_id = str(job.get("job_id") or "")
        row = by_job.get(job_id, {})
        try:
            unreviewed = int(row.get("unreviewed_residual_rows"))
            new_terms = int(row.get("new_terms_found") or 0)
        except (TypeError, ValueError):
            unreviewed = -1
            new_terms = -1
        new_term_cycle_ok = new_terms == 0 or bool(
            row.get("new_terms_added_with_discord_source_refs") is True
            and row.get("affected_query_jobs_rerun") is True
            and row.get("repeat_review_complete") is True
        )
        passed = bool(
            job.get("passed")
            and row
            and _status_passed(row.get("status"))
            and unreviewed == 0
            and new_terms >= 0
            and new_term_cycle_ok
            and _evidence_refs(row)
        )
        results.append(
            {
                "job_id": job_id,
                "passed": passed,
                "capture_complete": bool(job.get("passed")),
                "unreviewed_residual_rows": unreviewed,
                "new_terms_found": new_terms,
                "new_term_cycle_complete": new_term_cycle_ok,
                "evidence_refs": _evidence_refs(row),
            }
        )
    return {
        "passed": not audit_jobs or all(row["passed"] for row in results),
        "required_review_count": len(audit_jobs),
        "passed_review_count": sum(row["passed"] for row in results),
        "reviews": results,
    }


def _evidence_gate(
    evidence: dict[str, Any],
    keys: Sequence[str],
    *,
    zero_fields: Sequence[str],
    equality_fields: Sequence[tuple[str, str]] = (),
) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key in keys:
        value = evidence.get(key)
        if isinstance(value, dict):
            row = value
            break
    errors: list[str] = []
    if not row:
        errors.append("evidence_missing")
    elif not _status_passed(row.get("status")):
        errors.append("status_not_passed")
    for field in zero_fields:
        try:
            if int(row.get(field)) != 0:
                errors.append(f"{field}_not_zero")
        except (TypeError, ValueError):
            errors.append(f"{field}_missing_or_invalid")
    for left, right in equality_fields:
        try:
            if int(row.get(left)) != int(row.get(right)):
                errors.append(f"{left}_does_not_equal_{right}")
        except (TypeError, ValueError):
            errors.append(f"{left}_or_{right}_missing_or_invalid")
    refs = _evidence_refs(row)
    if row and not refs:
        errors.append("evidence_refs_missing")
    return {
        "passed": not errors,
        "errors": errors,
        "evidence_refs": refs,
        "summary": copy.deepcopy(row),
    }


def _forum_gate(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    occurrences: Sequence[dict[str, Any]],
    classified_segments: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    forum = plan.get("forum_thread_policy") if isinstance(plan.get("forum_thread_policy"), dict) else {}
    parent_id = str(forum.get("parent_channel_id") or "")
    segment_roles = {
        str(row.get("segment_id") or ""): row.get("policy_role")
        for row in classified_segments
    }
    scope = inventory.get("accessible_scope") if isinstance(inventory.get("accessible_scope"), dict) else {}
    threads = scope.get("forum_threads") if isinstance(scope.get("forum_threads"), dict) else {}
    forum_inventory_complete = bool(
        (
            threads.get("validated_complete") is True
            or (
                threads.get("declared_complete") is True
                and (
                    threads.get("completion_evidence")
                    or threads.get("verification_method")
                )
            )
        )
        and int(threads.get("unresolved_observed_occurrence_count") or 0) == 0
    )
    failures: list[dict[str, Any]] = []
    inspected = 0
    for occurrence in occurrences:
        if str(occurrence.get("query_container_id") or "") != parent_id:
            continue
        if segment_roles.get(str(occurrence.get("segment_id") or "")) != "required_full_capture":
            continue
        inspected += 1
        message_id = str(occurrence.get("message_id") or "")
        payload = occurrence.get("payload") if isinstance(occurrence.get("payload"), dict) else {}
        thread_id = str(
            occurrence.get("message_container_id")
            or payload.get("inferred_thread_channel_id")
            or ""
        )
        parent = str(
            occurrence.get("parent_container_id")
            or payload.get("group_header_parent_forum_channel_id")
            or payload.get("parent_channel_id")
            or ""
        )
        permalink = str(
            payload.get("exact_permalink")
            or payload.get("permalink")
            or payload.get("inferred_permalink")
            or ""
        )
        permalink_status = str(payload.get("exact_permalink_status") or "")
        thread_id_source = str(payload.get("thread_channel_id_source") or "")
        exact_thread_source = bool(
            occurrence.get("message_container_id_source")
            == "premium_whole_artifact_byte_bound_row_mapping"
            and (
                thread_id_source
                in {
                    "forum_group_header_data_list_item_id",
                    "forum_group_header_navigation_exact",
                    "forum_group_owned_reply_anchor_exact",
                }
                or permalink_status
                in {
                    "thread_id_from_forum_group_header",
                    "thread_id_from_forum_group_header_navigation",
                    "thread_id_from_owned_reply_permalink",
                }
            )
        )
        parsed = urlparse(permalink) if permalink else None
        path_parts = [part for part in (parsed.path.split("/") if parsed else []) if part]
        expected_tail = [str(plan.get("guild", {}).get("guild_id") or ""), thread_id, message_id]
        exact_link = bool(
            parsed
            and parsed.hostname in {"discord.com", "www.discord.com"}
            and len(path_parts) >= 4
            and path_parts[-3:] == expected_tail
            and "thread_id_unresolved" not in permalink_status
        )
        reasons: list[str] = []
        if not DISCORD_ID_RE.fullmatch(thread_id) or thread_id == parent_id:
            reasons.append("exact_thread_id_missing")
        if parent != parent_id:
            reasons.append("parent_forum_id_missing_or_wrong")
        if not exact_thread_source or payload.get("thread_channel_id_exact") is False:
            reasons.append("thread_id_source_not_exact_row_owned_evidence")
        if not exact_link:
            reasons.append("exact_thread_permalink_missing_or_wrong")
        if payload.get("exact_permalink_conflict_detected") is True:
            reasons.append("exact_permalink_conflict_detected")
        if reasons:
            failures.append({"message_id": message_id, "thread_id": thread_id, "reasons": reasons})
    return {
        "passed": forum_inventory_complete and not failures,
        "parent_channel_id": parent_id,
        "forum_inventory_complete": forum_inventory_complete,
        "inspected_message_occurrences": inspected,
        "failure_count": len(failures),
        "failures": failures[:100],
    }


def _inventory_rows(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    raw = inventory.get("containers")
    if not isinstance(raw, list):
        raw = inventory.get("channels")
    return [row for row in (raw or []) if isinstance(row, dict)]


def _post_cutoff_inventory_gate(
    inventory: dict[str, Any], required_cutoff: dt.datetime
) -> dict[str, Any]:
    scope = inventory.get("accessible_scope") if isinstance(inventory.get("accessible_scope"), dict) else {}
    top = scope.get("top_level_containers") if isinstance(scope.get("top_level_containers"), dict) else {}
    resnapshot = (
        scope.get("post_cutoff_navigation_resnapshot")
        if isinstance(scope.get("post_cutoff_navigation_resnapshot"), dict)
        else {}
    )
    evidence = (
        resnapshot.get("completion_evidence")
        if isinstance(resnapshot.get("completion_evidence"), dict)
        else {}
    )
    capture = parse_utc(
        inventory.get("captured_at_utc")
        or inventory.get("capture_as_of_utc")
        or evidence.get("captured_at_utc")
    )
    evidence_capture = parse_utc(
        evidence.get("captured_at_utc")
        or evidence.get("completed_at_utc")
        or inventory.get("captured_at_utc")
        or inventory.get("capture_as_of_utc")
    )
    source_refs = evidence.get("source_refs")
    source_refs_ok = isinstance(source_refs, list) and bool(
        [item for item in source_refs if str(item).strip()]
    )
    inventory_complete = bool(
        inventory.get("validated_complete") is True
        or inventory.get("inventory_complete") is True
    )
    passed = bool(
        inventory_complete
        and top.get("declared_complete") is True
        and resnapshot.get("declared_complete") is True
        and (resnapshot.get("validated_complete") is True or resnapshot.get("status") == "complete")
        and capture is not None
        and capture >= required_cutoff
        and evidence_capture is not None
        and evidence_capture >= required_cutoff
        and evidence.get("authenticated") is True
        and evidence.get("navigation_pass_complete") is True
        and evidence.get("terminal_state_observed") is True
        and source_refs_ok
    )
    return {
        "passed": passed,
        "inventory_complete": inventory_complete,
        "resnapshot_declared_complete": resnapshot.get("declared_complete") is True,
        "resnapshot_validated_complete": resnapshot.get("validated_complete") is True,
        "capture_at_utc": capture.isoformat().replace("+00:00", "Z") if capture else None,
        "evidence_capture_at_utc": evidence_capture.isoformat().replace("+00:00", "Z")
        if evidence_capture
        else None,
        "required_cutoff_utc": required_cutoff.isoformat().replace("+00:00", "Z"),
        "authenticated": evidence.get("authenticated") is True,
        "navigation_pass_complete": evidence.get("navigation_pass_complete") is True,
        "terminal_state_observed": evidence.get("terminal_state_observed") is True,
        "source_refs": list(source_refs) if isinstance(source_refs, list) else [],
    }


def _ordinary_thread_gate(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    required_cutoff: dt.datetime,
) -> dict[str, Any]:
    scope = inventory.get("accessible_scope") if isinstance(inventory.get("accessible_scope"), dict) else {}
    ordinary = scope.get("ordinary_threads") if isinstance(scope.get("ordinary_threads"), dict) else {}
    completion = (
        ordinary.get("completion_evidence")
        if isinstance(ordinary.get("completion_evidence"), dict)
        else {}
    )
    plan_ids = set(_plan_policy_map(plan))
    audited_parent_ids = {
        str(item)
        for item in (ordinary.get("audited_parent_ids") or completion.get("audited_parent_ids") or [])
        if DISCORD_ID_RE.fullmatch(str(item))
    }
    capture = parse_utc(
        completion.get("capture_completed_at_utc")
        or ordinary.get("capture_completed_at_utc")
    )
    rows = _inventory_rows(inventory)
    thread_rows = [
        row
        for row in rows
        if str(row.get("inventory_layer") or "")
        in {"observed_ordinary_thread", "declared_ordinary_thread"}
    ]
    invalid_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in thread_rows:
        thread_id = str(row.get("container_id") or row.get("thread_id") or row.get("channel_id") or "")
        parent_id = str(row.get("parent_container_id") or row.get("parent_channel_id") or "")
        identity = row.get("identity_provenance") if isinstance(row.get("identity_provenance"), dict) else {}
        reasons: list[str] = []
        if not DISCORD_ID_RE.fullmatch(thread_id) or thread_id in seen:
            reasons.append("thread_id_missing_invalid_or_duplicate")
        if parent_id not in plan_ids:
            reasons.append("parent_not_in_post_cutoff_top_level_set")
        if row.get("exact_id_known") is not True:
            reasons.append("exact_id_known_not_true")
        if identity.get("exact_row_owned_evidence") is not True:
            reasons.append("exact_identity_evidence_missing")
        if reasons:
            invalid_rows.append(
                {"thread_id": thread_id or None, "parent_channel_id": parent_id or None, "reasons": reasons}
            )
        seen.add(thread_id)
    expected_count = ordinary.get("expected_parent_audit_count")
    audited_count = ordinary.get("audited_parent_count")
    exact_thread_count = ordinary.get("exact_thread_count", completion.get("exact_thread_count"))
    try:
        count_contract = (
            int(expected_count) == len(plan_ids)
            and int(audited_count) == len(plan_ids)
            and int(exact_thread_count) == len(thread_rows)
        )
    except (TypeError, ValueError):
        count_contract = False
    unresolved = ordinary.get(
        "unresolved_observed_occurrence_count",
        completion.get("unresolved_observed_occurrence_count"),
    )
    try:
        unresolved_zero = int(unresolved) == 0
    except (TypeError, ValueError):
        unresolved_zero = False
    passed = bool(
        (ordinary.get("validated_complete") is True or ordinary.get("declared_complete") is True)
        and completion
        and completion.get("authenticated") is True
        and completion.get("parent_audits_complete") is True
        and capture is not None
        and capture >= required_cutoff
        and audited_parent_ids == plan_ids
        and count_contract
        and unresolved_zero
        and not invalid_rows
    )
    return {
        "passed": passed,
        "declared_complete": ordinary.get("declared_complete") is True,
        "validated_complete": ordinary.get("validated_complete") is True,
        "expected_parent_count": len(plan_ids),
        "audited_parent_count": len(audited_parent_ids),
        "audited_parent_set_matches_plan": audited_parent_ids == plan_ids,
        "capture_completed_at_utc": capture.isoformat().replace("+00:00", "Z") if capture else None,
        "required_cutoff_utc": required_cutoff.isoformat().replace("+00:00", "Z"),
        "ordinary_thread_count": len(thread_rows),
        "count_contract_passed": count_contract,
        "unresolved_observed_occurrence_count": unresolved,
        "invalid_thread_row_count": len(invalid_rows),
        "invalid_thread_rows": invalid_rows[:100],
    }


def _raw_reply_scope_gate(
    plan: dict[str, Any], occurrences: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    inspected_reply_targets = 0
    unresolved_context_rows = 0
    guild_id = str(plan.get("guild", {}).get("guild_id") or "")
    for occurrence in occurrences:
        payload = occurrence.get("payload") if isinstance(occurrence.get("payload"), dict) else {}
        owner_id = str(occurrence.get("message_id") or payload.get("message_id") or "")
        contract_payload = {**payload, "message_id": owner_id}
        target_id = str(payload.get("reply_to_message_id") or "").strip()
        has_context = reply_provenance_contract.has_reply_context(contract_payload)
        if not target_id:
            if has_context:
                unresolved_context_rows += 1
                reasons = (
                    reply_provenance_contract.documented_no_id_contract_errors(
                        contract_payload
                    )
                )
                if reasons:
                    failures.append(
                        {
                            "message_id": owner_id,
                            "reason": "reply_preview_context_without_exact_documented_no_id_state",
                            "declared_status": payload.get(
                                "reply_target_resolution_status"
                            ),
                            "documented": payload.get(
                                "reply_target_unavailability_documented"
                            ),
                            "reasons": reasons,
                        }
                    )
            else:
                reasons = (
                    reply_provenance_contract.resolution_status_boolean_errors(
                        contract_payload
                    )
                )
                if reasons:
                    failures.append(
                        {
                            "message_id": owner_id,
                            "reason": "reply_resolution_status_boolean_mismatch",
                            "reasons": reasons,
                        }
                    )
            continue
        inspected_reply_targets += 1
        source = str(payload.get("reply_to_message_id_source") or "")
        reasons = reply_provenance_contract.exact_reply_target_contract_errors(
            contract_payload,
            guild_id=guild_id,
        )
        if reasons:
            failures.append(
                {
                    "message_id": owner_id,
                    "reply_to_message_id": target_id,
                    "source": source or None,
                    "reasons": reasons,
                }
            )
    return {
        "passed": not failures,
        "inspected_reply_targets": inspected_reply_targets,
        "unresolved_context_rows": unresolved_context_rows,
        "failure_count": len(failures),
        "failures": failures[:100],
        "accepted_exact_source": "owned_reply_context_descendant_content_id",
        "accepted_exact_sources": sorted(
            reply_provenance_contract.EXACT_ROW_OWNED_REPLY_SOURCES
        ),
        "accepted_documented_no_id_statuses": sorted(
            reply_provenance_contract.DOCUMENTED_NO_ID_STATUSES
        ),
        "preview_only_links_are_resolved_answers": False,
    }


def evaluate_relevance_policy(
    *,
    plan_bundle: dict[str, Any],
    segments: Sequence[dict[str, Any]],
    inventory: dict[str, Any],
    progress: dict[str, Any] | None,
    data_cutoff_utc: dt.datetime,
    required_end_exclusive_utc: dt.datetime,
    occurrences: Sequence[dict[str, Any]] = (),
    messages: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Evaluate policy-appropriate completeness and every plan hard gate."""

    plan = plan_bundle.get("plan") if isinstance(plan_bundle.get("plan"), dict) else {}
    expected_jobs = plan_bundle.get("expanded_jobs") or []
    policies = _plan_policy_map(plan)
    classified = classify_segments(plan, segments)
    jobs = [_segment_job_coverage(job, classified) for job in expected_jobs]
    full_jobs = [row for row in jobs if row.get("job_kind") == "full_capture_or_empty_verification"]
    targeted_jobs = [row for row in jobs if row.get("job_kind") == "targeted_search"]
    audit_jobs = [row for row in jobs if row.get("job_kind") == "residual_audit_census_day"]
    progress_check = _progress_consistency(expected_jobs, progress)
    evidence = _release_evidence(progress)
    evidence_envelope = _release_evidence_envelope(
        evidence, required_end_exclusive_utc
    )

    plan_ids = set(policies)
    policy_counts = Counter(row.get("policy") for row in policies.values())
    policy_count_contract = policy_counts == Counter(
        {
            "full_capture": 16,
            "verified_empty_full_window": 22,
        }
    )
    inventory_ids = _inventory_top_level_ids(inventory)
    inventory_exact = bool(
        plan_bundle.get("valid")
        and len(plan_ids) == 38
        and inventory_ids == plan_ids
        and policy_count_contract
    )
    window_final = data_cutoff_utc >= required_end_exclusive_utc
    full_coverage = bool(full_jobs) and len(full_jobs) == 38 and policy_count_contract and all(
        row["passed"] for row in full_jobs
    )
    targeted_matrix = not targeted_jobs or all(row["passed"] for row in targeted_jobs)
    audit_capture_matrix = not audit_jobs or all(row["passed"] for row in audit_jobs)
    count_reconciliation = _count_reconciliation(
        full_jobs, evidence, required_end_exclusive_utc
    )
    residual_reviews = _residual_reviews(audit_jobs, evidence)
    reply = _evidence_gate(
        evidence,
        ("reply_resolution", "reply_context"),
        zero_fields=(
            "questions_without_resolution_status",
            "direct_answer_linkage_errors",
            "adjacent_context_promoted_count",
        ),
        equality_fields=(("selected_question_count", "resolution_status_count"),),
    )
    raw_reply_scope = _raw_reply_scope_gate(plan, occurrences)
    attachments = _evidence_gate(
        evidence,
        ("attachments_and_chart_dependence", "attachment_chart_review"),
        zero_fields=("reply_preview_media_leak_count", "unlabeled_chart_dependent_count"),
    )
    forum = _forum_gate(plan, inventory, occurrences, classified)
    post_cutoff_inventory = _post_cutoff_inventory_gate(
        inventory, required_end_exclusive_utc
    )
    ordinary_threads = _ordinary_thread_gate(
        plan, inventory, required_end_exclusive_utc
    )

    segment_by_id = {str(row.get("segment_id") or ""): row for row in classified}
    expected_query_occurrences = sum(
        int(row.get("captured_rows_computed") or 0)
        for row in classified
        if row.get("policy_role") == "required_targeted_query"
    )
    actual_query_occurrences = sum(
        1
        for row in occurrences
        if segment_by_id.get(str(row.get("segment_id") or ""), {}).get("policy_role")
        == "required_targeted_query"
    )
    canonical_ids = [str(row.get("message_id") or "") for row in messages]
    query_overlap = bool(
        expected_query_occurrences == actual_query_occurrences
        and len(canonical_ids) == len(set(canonical_ids))
    )

    source_policy = plan.get("source_policy") if isinstance(plan.get("source_policy"), dict) else {}
    evidence_outside = evidence.get("outside_sources_used", 0)
    discord_only = bool(
        source_policy.get("scope") == "discord_only"
        and source_policy.get("outside_sources_used") == 0
        and evidence_outside in {0, False, None}
    )
    claim_row = evidence.get("claim_calibration")
    if isinstance(claim_row, dict):
        claim_calibration = _evidence_gate(
            evidence,
            ("claim_calibration",),
            zero_fields=("unsupported_probability_claim_count", "uncalibrated_success_probability_count"),
        )
    else:
        claim_calibration = {
            "passed": True,
            "basis": "raw_corpus_emits_no_normalized_trading_claims",
            "errors": [],
            "evidence_refs": [],
        }

    gate_values: dict[str, tuple[bool, Any]] = {
        "inventory_exact": (
            inventory_exact,
            {
                "plan_ids": len(plan_ids),
                "inventory_ids": len(inventory_ids),
                "policy_counts": dict(sorted(policy_counts.items())),
                "required_policy_counts": {
                    "full_capture": 16,
                    "verified_empty_full_window": 22,
                },
            },
        ),
        "inventory_post_cutoff_authenticated": (
            post_cutoff_inventory["passed"],
            post_cutoff_inventory,
        ),
        "window_final": (
            window_final,
            {
                "data_cutoff_utc": data_cutoff_utc.isoformat().replace("+00:00", "Z"),
                "required_end_exclusive_utc": required_end_exclusive_utc.isoformat().replace("+00:00", "Z"),
            },
        ),
        "full_capture_segment_coverage": (
            full_coverage,
            {
                "required_jobs": len(full_jobs),
                "passed_jobs": sum(row["passed"] for row in full_jobs),
                "full_capture_channels": sum(
                    row.get("policy") == "full_capture" for row in policies.values()
                ),
                "verified_empty_channels": sum(
                    row.get("policy") == "verified_empty_full_window" for row in policies.values()
                ),
            },
        ),
        "full_capture_count_reconciliation": (
            count_reconciliation["passed"] and evidence_envelope["passed"],
            {
                "reconciliation": count_reconciliation,
                "release_evidence_envelope": evidence_envelope,
            },
        ),
        "targeted_query_matrix": (
            targeted_matrix and progress_check["passed"],
            {
                "required_jobs": len(targeted_jobs),
                "passed_jobs": sum(row["passed"] for row in targeted_jobs),
                "atomic_queries_per_targeted_channel": len(targeted_jobs) // 3 if targeted_jobs else 0,
                "progress_manifest": progress_check,
            },
        ),
        "residual_audit": (
            audit_capture_matrix
            and residual_reviews["passed"]
            and evidence_envelope["passed"],
            {
                "required_capture_jobs": len(audit_jobs),
                "passed_capture_jobs": sum(row["passed"] for row in audit_jobs),
                "review": residual_reviews,
                "release_evidence_envelope": evidence_envelope,
            },
        ),
        "query_overlap_provenance": (
            query_overlap,
            {
                "expected_targeted_query_occurrences": expected_query_occurrences,
                "retained_targeted_query_occurrences": actual_query_occurrences,
                "canonical_message_ids_unique": len(canonical_ids) == len(set(canonical_ids)),
            },
        ),
        "reply_resolution": (
            reply["passed"]
            and raw_reply_scope["passed"]
            and evidence_envelope["passed"],
            {
                "review_evidence": reply,
                "raw_reply_scope": raw_reply_scope,
                "release_evidence_envelope": evidence_envelope,
            },
        ),
        "forum_exact_ids": (forum["passed"], forum),
        "thread_inventory_complete": (
            ordinary_threads["passed"],
            ordinary_threads,
        ),
        "attachments_and_chart_dependence": (
            attachments["passed"] and evidence_envelope["passed"],
            {
                "review_evidence": attachments,
                "release_evidence_envelope": evidence_envelope,
            },
        ),
        "discord_only": (
            discord_only and evidence_envelope["passed"],
            {
                "outside_sources_used": evidence_outside,
                "release_evidence_envelope": evidence_envelope,
            },
        ),
        "claim_calibration": (
            claim_calibration["passed"] and evidence_envelope["passed"],
            {
                "review_evidence": claim_calibration,
                "release_evidence_envelope": evidence_envelope,
            },
        ),
    }

    # The label gate is evaluated after every other substantive hard gate so a
    # targeted channel is never called complete while one of those gates fails.
    preliminary_hard_ids = {
        str(row.get("gate_id"))
        for row in plan.get("coverage_and_reconciliation_gates", [])
        if isinstance(row, dict)
        and row.get("severity") == "hard"
        and row.get("gate_id") != "targeted_not_mislabeled"
    }
    preliminary_ready = bool(preliminary_hard_ids) and all(
        gate_values.get(gate_id, (False, None))[0] for gate_id in preliminary_hard_ids
    )
    targeted_labels = {
        channel_id: "topic-complete_targeted" if preliminary_ready else "topic-partial_targeted"
        for channel_id, row in policies.items()
        if row.get("policy") == TARGETED_POLICY
    }
    targeted_not_mislabeled = not targeted_labels or all(
        label in {"topic-complete_targeted", "topic-partial_targeted"}
        and "message-complete" not in label
        for label in targeted_labels.values()
    )
    gate_values["targeted_not_mislabeled"] = (
        targeted_not_mislabeled,
        {"labels": targeted_labels},
    )

    hard_plan_gates = [
        row
        for row in plan.get("coverage_and_reconciliation_gates", [])
        if isinstance(row, dict) and row.get("severity") == "hard"
    ]
    hard_gate_rows: list[dict[str, Any]] = []
    for gate in hard_plan_gates:
        gate_id = str(gate.get("gate_id") or "")
        supported = gate_id in SUPPORTED_HARD_GATES
        passed, detail = gate_values.get(gate_id, (False, {"reason": "unsupported_hard_gate"}))
        hard_gate_rows.append(
            {
                "gate_id": gate_id,
                "severity": "hard",
                "supported": supported,
                "passed": bool(supported and passed),
                "detail": detail,
            }
        )

    hard_ready = bool(hard_gate_rows) and all(row["passed"] for row in hard_gate_rows)
    full_by_channel = {str(row.get("channel_id")): row for row in full_jobs}
    count_by_channel = {
        str(row.get("channel_id")): row for row in count_reconciliation.get("channels", [])
    }
    channel_rows: list[dict[str, Any]] = []
    for channel_id, policy_row in sorted(policies.items()):
        policy = str(policy_row.get("policy") or "")
        if policy == TARGETED_POLICY:
            label = targeted_labels.get(channel_id, "topic-partial_targeted")
            message_complete = False
            passed = hard_ready
        else:
            capture_passed = bool(full_by_channel.get(channel_id, {}).get("passed"))
            count_passed = bool(count_by_channel.get(channel_id, {}).get("passed"))
            passed = capture_passed and count_passed
            if policy == "verified_empty_full_window":
                label = "verified-empty_full-window" if passed else "empty-verification-incomplete"
            else:
                label = "message-complete" if passed else "message-capture-incomplete"
            message_complete = bool(passed)
        diagnostic = [
            str(row.get("segment_id") or "")
            for row in classified
            if str(row.get("query_container_id") or "") == channel_id
            and row.get("policy_role") == "diagnostic_targeted_full_capture"
        ]
        channel_rows.append(
            {
                "channel_id": channel_id,
                "name": policy_row.get("name"),
                "policy": policy,
                "completion_label": label,
                "policy_gate_passed": passed,
                "message_complete": message_complete,
                "diagnostic_targeted_full_capture_segment_ids": sorted(diagnostic),
            }
        )

    return {
        "enabled": True,
        "plan_valid": bool(plan_bundle.get("valid")),
        "plan_validation": copy.deepcopy(plan_bundle.get("validation") or {}),
        "policy_counts": dict(sorted(policy_counts.items())),
        "classified_segments": classified,
        "channel_coverage": channel_rows,
        "job_coverage": {
            "total": len(jobs),
            "full_capture_or_empty_verification": len(full_jobs),
            "targeted_search": len(targeted_jobs),
            "residual_audit_census_day": len(audit_jobs),
            "passed": sum(row["passed"] for row in jobs),
            "jobs": jobs,
        },
        "progress_manifest": progress_check,
        "count_reconciliation": count_reconciliation,
        "residual_reviews": residual_reviews,
        "reply_resolution": reply,
        "raw_reply_scope": raw_reply_scope,
        "attachments_and_chart_dependence": attachments,
        "release_evidence_envelope": evidence_envelope,
        "inventory_post_cutoff_authenticated": post_cutoff_inventory,
        "forum_exact_ids": forum,
        "thread_inventory_complete": ordinary_threads,
        "hard_gates": hard_gate_rows,
        "release_ready": hard_ready,
        "diagnostic_partial_targeted_full_capture_count": sum(
            row.get("policy_role") == "diagnostic_targeted_full_capture"
            and not row.get("computed_complete")
            for row in classified
        ),
        "rules": {
            "all_top_level_channels_require_message_complete_or_verified_empty_coverage": True,
            "supplemental_targeted_searches_never_replace_full_capture": True,
            "forum_full_capture_requires_complete_thread_inventory_and_exact_message_permalinks": True,
            "ordinary_threads_require_complete_parent_audit_and_exact_identity": True,
            "top_level_inventory_requires_post_cutoff_authenticated_resnapshot": True,
            "progress_assertions_never_replace_ingested_raw_segment_evidence": True,
        },
    }


def policy_required_partial_segments(policy: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in policy.get("classified_segments", [])
        if row.get("policy_role")
        in {"required_full_capture", "required_targeted_query", "required_residual_audit"}
        and not row.get("computed_complete")
    ]
