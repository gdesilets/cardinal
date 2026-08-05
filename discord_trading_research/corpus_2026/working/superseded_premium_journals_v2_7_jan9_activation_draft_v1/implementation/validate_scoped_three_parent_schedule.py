from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import reply_provenance_contract
import timestamp_scope_revalidation
import premium_journals_provenance_contract as premium_contract
import questions_post_capture_promotion_exception as post_capture_exception


ROOT = Path(__file__).resolve().parent
DEFAULT_SCHEDULE = ROOT / "working" / "scoped_three_parent_collection_schedule.json"
STUDENT_RECONCILIATION_RELATIVE_PATH = (
    "working/"
    "student_breakdowns_2026-01-01_2026-07-20_20260721T173220703Z."
    "full-window-reconciliation.json"
)
EXPECTED_STUDENT_RECONCILIATION_SHA256 = (
    "caed83d1ac50ec8dc6d30abcc0d57993623bf51e85f86eb88b474f7d673c6795"
)
EXPECTED_STUDENT_SHARD_COUNT = 15
EXPECTED_STUDENT_CALENDAR_DAY_COUNT = 201
EXPECTED_STUDENT_REPORTED_TOTAL = 294

STUDENT_ID = "1370578463223975986"
QUESTIONS_ID = "1273692573898113076"
PREMIUM_ID = "1283941772577472643"
QUESTIONS_NAME = "\u2753\u2502questions"
QUESTIONS_LOGICAL_NAME = "questions"
QUESTIONS_CATEGORY_NAME = "PREMIUM"
QUESTIONS_QUERY_PREFIX = f"in:{QUESTIONS_NAME}"
QUESTIONS_RUNTIME_OPTIONS = {
    "checkpointEvery": 5,
    "maxPagesPerCall": 20,
    "reuseActiveSearch": True,
}
TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX = (
    timestamp_scope_revalidation.TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX
)
JAN5_LEGACY_PARTIAL_PATH = (
    "raw/channel_segments/"
    "channel_questions_1273692573898113076_2026-01-05_2026-01-05.partial.json"
)
JAN5_LEGACY_PARTIAL_SHA256 = (
    "12eb3d1252121d7f96386af3484ead60ea0cbad1ce79dece5ec39255ee738bb3"
)

EXPECTED = {
    "student_breakdowns": {
        "id": STUDENT_ID,
        "name": "student-breakdowns",
        "kind": "text channel",
        "slug": "student_breakdowns",
        "prefix": "in:student-breakdowns",
        "route_count": 15,
        "max_span": 14,
        "calendar_days": 201,
    },
    "questions": {
        "id": QUESTIONS_ID,
        "name": QUESTIONS_NAME,
        "kind": "text channel",
        "slug": "questions",
        "prefix": QUESTIONS_QUERY_PREFIX,
        "route_count": 33,
        "max_span": 7,
        "calendar_days": 201,
    },
    "premium_journals": {
        "id": PREMIUM_ID,
        "name": "premium-journals",
        "kind": "forum channel",
        "slug": "premium_journals",
        "prefix": "in:premium-journals",
        "route_count": 201,
        "max_span": 1,
        "calendar_days": 201,
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def exact_query(prefix: str, start: str, end: str) -> str:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    return (
        f"{prefix} after:{(start_date - timedelta(days=1)).isoformat()} "
        f"before:{(end_date + timedelta(days=1)).isoformat()}"
    )


def make_segments(start: date, end: date, span_days: int) -> list[tuple[str, str]]:
    cursor = start
    segments: list[tuple[str, str]] = []
    while cursor <= end:
        segment_end = min(cursor + timedelta(days=span_days - 1), end)
        segments.append((cursor.isoformat(), segment_end.isoformat()))
        cursor = segment_end + timedelta(days=1)
    return segments


def expected_questions_segments() -> list[tuple[str, str]]:
    return make_segments(date(2026, 1, 1), date(2026, 1, 5), 1) + make_segments(
        date(2026, 1, 6), date(2026, 7, 20), 7
    )


def add(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def validate_source_hashes(root: Path, schedule: dict[str, Any], errors: list[str]) -> None:
    bindings = schedule.get("source_bindings")
    if not isinstance(bindings, dict):
        errors.append("source bindings missing")
        return
    required = {
        "authorized_collection_scope",
        "frozen_two_tab_schedule",
        "full_server_channel_inventory",
        "premium_journals_reconciliation",
        "student_breakdowns_full_window_reconciliation",
    }
    add(errors, set(bindings) == required, "source binding set mismatch")
    for name, binding in bindings.items():
        if not isinstance(binding, dict):
            errors.append(f"source binding {name} invalid")
            continue
        relative = binding.get("path")
        if not isinstance(relative, str):
            errors.append(f"source binding {name} path missing")
            continue
        path = root / relative
        if not path.is_file():
            errors.append(f"mutable source missing: {relative}")
            continue
        observed = sha256_file(path)
        if observed != binding.get("sha256"):
            errors.append(
                f"mutable source hash mismatch for {name}: {relative}: "
                f"expected {binding.get('sha256')}, observed {observed}"
            )


def validate_scope(root: Path, schedule: dict[str, Any], errors: list[str]) -> None:
    authorized = load_json(root / "authorized_collection_scope.json")
    expected_parents = {
        item["channel_id"]: (item["name"], item["kind"])
        for item in authorized["allowed_top_level_containers"]
    }
    local_expected = {
        STUDENT_ID: ("student-breakdowns", "text channel"),
        QUESTIONS_ID: (QUESTIONS_NAME, "text channel"),
        PREMIUM_ID: ("premium-journals", "forum channel"),
    }
    add(errors, expected_parents == local_expected, "authorized source parent set changed")
    scope = schedule.get("scope", {})
    add(
        errors,
        set(scope.get("authorized_parent_ids", [])) == set(local_expected),
        "unauthorized or missing container in schedule scope",
    )
    add(errors, scope.get("authorized_parent_count") == 3, "authorized parent count is not 3")
    add(errors, scope.get("other_channels_and_jobs_excluded") is True, "other jobs not excluded")
    parents = schedule.get("parents", [])
    observed = {
        item.get("channel_id"): (item.get("name"), item.get("kind"))
        for item in parents
        if isinstance(item, dict)
    }
    add(errors, observed == local_expected, "parent identity set does not match authorized scope")
    parent_by_id = {
        item.get("channel_id"): item for item in parents if isinstance(item, dict)
    }
    authorized_questions = next(
        (
            item
            for item in authorized.get("allowed_top_level_containers", [])
            if item.get("channel_id") == QUESTIONS_ID
        ),
        {},
    )
    add(
        errors,
        authorized_questions.get("name") == QUESTIONS_NAME,
        "authorized Questions exact visible name mismatch",
    )
    add(
        errors,
        authorized_questions.get("logical_name") == QUESTIONS_LOGICAL_NAME,
        "authorized Questions logical name mismatch",
    )
    student = parent_by_id.get(STUDENT_ID, {})
    add(errors, student.get("route_count") == 15, "Student parent route count is not 15")
    add(
        errors,
        student.get("accepted_route_count") == EXPECTED_STUDENT_SHARD_COUNT,
        "Student parent is not 15/15 accepted",
    )
    add(errors, student.get("pending_route_count") == 0, "Student parent still has pending routes")
    add(
        errors,
        student.get("accepted_reported_total") == EXPECTED_STUDENT_REPORTED_TOTAL,
        "Student parent accepted total is not 294",
    )
    add(errors, student.get("coverage_status") == "reconciled", "Student parent is not reconciled")
    questions_parent = parent_by_id.get(QUESTIONS_ID, {})
    add(errors, questions_parent.get("route_count") == 33, "Questions parent route count is not 33")
    add(
        errors,
        questions_parent.get("name") == QUESTIONS_NAME,
        "Questions parent exact visible name mismatch",
    )
    add(
        errors,
        questions_parent.get("logical_name") == QUESTIONS_LOGICAL_NAME,
        "Questions parent logical name mismatch",
    )
    add(
        errors,
        questions_parent.get("visible_parent_category") == QUESTIONS_CATEGORY_NAME,
        "Questions visible parent/category mismatch",
    )
    premium_parent = parent_by_id.get(PREMIUM_ID, {})
    add(errors, premium_parent.get("route_count") == 201, "Premium parent route count is not 201")
    add(
        errors,
        premium_parent.get("authoritative_canonical_directory")
        == premium_contract.AUTHORITATIVE_DIRECTORY,
        "Premium authoritative canonical directory mismatch",
    )
    add(
        errors,
        premium_parent.get("legacy_premium_directory_policy")
        == "preservation_only_not_authoritative",
        "Premium legacy directory is not preservation-only",
    )


def validate_route_cover(
    key: str, routes: list[dict[str, Any]], spec: dict[str, Any], errors: list[str]
) -> None:
    if len(routes) != spec["route_count"]:
        errors.append(f"{key} route count mismatch: {len(routes)}")
    parsed: list[tuple[date, date, dict[str, Any]]] = []
    for index, route in enumerate(routes):
        label = f"{key}[{index}]"
        channel_id = route.get("channel_id")
        if channel_id not in {STUDENT_ID, QUESTIONS_ID, PREMIUM_ID}:
            errors.append(f"unauthorized container in route {label}: {channel_id}")
        if channel_id != spec["id"]:
            errors.append(f"wrong channel ID in {label}: {channel_id}")
        if route.get("channel_name") != spec["name"]:
            errors.append(f"wrong query/channel name in {label}: {route.get('channel_name')}")
        if route.get("channel_kind") != spec["kind"]:
            errors.append(f"wrong channel kind in {label}: {route.get('channel_kind')}")
        if route.get("query_prefix") != spec["prefix"]:
            errors.append(f"wrong query name in {label}: {route.get('query_prefix')}")
        try:
            start = date.fromisoformat(route["start"])
            end = date.fromisoformat(route["end"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"invalid route date in {label}")
            continue
        if end < start:
            errors.append(f"negative route span in {label}")
        span = (end - start).days + 1
        if span > spec["max_span"]:
            errors.append(f"route span exceeds {spec['max_span']} in {label}")
        expected_query = exact_query(spec["prefix"], route["start"], route["end"])
        if route.get("query") != expected_query:
            errors.append(f"wrong exact query in {label}: {route.get('query')}")
        expected_path = (
            premium_contract.expected_canonical_relative_path(
                route["start"], route["end"]
            )
            if key == "premium_journals"
            else (
                f"raw/channel_segments/channel_{spec['slug']}_{spec['id']}_"
                f"{route['start']}_{route['end']}.json"
            )
        )
        if route.get("expected_canonical_path") != expected_path:
            errors.append(f"wrong canonical path in {label}")
        if route.get("scraping_owner") != "GPT-5.6 Terra":
            errors.append(f"wrong scraping owner in {label}")
        if route.get("heavy_pagination_lane") != "discord_account_heavy_lane_1":
            errors.append(f"wrong heavy lane in {label}")
        if key == "premium_journals":
            forum = route.get("forum_exact_navigation", {})
            required_forum = {
                "required": True,
                "evidence_key": "exact_query+page_number+sorted_group_message_ids",
                "trigger": "unique_direct_child_role_button_click",
                "destination": "exact_/channels/<guild_id>/<thread_id>_URL",
                "same_query_page_group_back_return_required": True,
                "title_only_identity_allowed": False,
                "attachment_or_media_channel_identity_allowed": False,
            }
            if forum != required_forum:
                errors.append(f"Premium exact-navigation policy mismatch in {label}")
        elif "forum_exact_navigation" in route:
            errors.append(f"unexpected forum navigation policy in {label}")
        if key == "questions":
            if route.get("logical_name") != QUESTIONS_LOGICAL_NAME:
                errors.append(f"Questions logical name mismatch in {label}")
            if route.get("visible_parent_category") != QUESTIONS_CATEGORY_NAME:
                errors.append(f"Questions visible parent/category mismatch in {label}")
            if route.get("message_granularity") != "individual_message":
                errors.append(f"Questions message granularity changed in {label}")
            expected_granularity = "daily" if span == 1 else "weekly_7_day"
            if route.get("granularity") != expected_granularity:
                errors.append(f"Questions granularity mismatch in {label}")
            if route.get("runtime_options") != QUESTIONS_RUNTIME_OPTIONS:
                errors.append(f"Questions runtime options mismatch in {label}")
            required_resume = {
                "checkpoint_resume_required": True,
                "new_search_submission_on_resume_allowed": False,
                "count_drift_stop_required": True,
                "resume_mismatch_stop_required": True,
            }
            if route.get("resume_behavior") != required_resume:
                errors.append(f"Questions resume behavior mismatch in {label}")
        parsed.append((start, end, route))

    if key == "questions":
        observed_segments = [
            (route.get("start"), route.get("end")) for route in routes
        ]
        required_segments = expected_questions_segments()
        if observed_segments != required_segments:
            errors.append(
                "Questions route shape mismatch: expected five daily routes followed by "
                "28 weekly routes"
            )

    parsed.sort(key=lambda item: (item[0], item[1]))
    if not parsed:
        return
    if parsed[0][0] != date(2026, 1, 1):
        errors.append(f"route gap before first {key} route")
    if parsed[-1][1] != date(2026, 7, 20):
        errors.append(f"route gap after last {key} route")
    for previous, current in zip(parsed, parsed[1:]):
        expected_next = previous[1] + timedelta(days=1)
        if current[0] < expected_next:
            errors.append(
                f"route overlap in {key}: {previous[2].get('route_id')} and "
                f"{current[2].get('route_id')}"
            )
        elif current[0] > expected_next:
            errors.append(
                f"route gap in {key}: {previous[1].isoformat()} to {current[0].isoformat()}"
            )
    covered_days = sum((end - start).days + 1 for start, end, _ in parsed)
    if covered_days != spec["calendar_days"]:
        errors.append(
            f"{key} calendar-day partition mismatch: {covered_days}, "
            f"expected {spec['calendar_days']}"
        )


def validate_student_acceptance(root: Path, routes: list[dict[str, Any]], errors: list[str]) -> None:
    accepted = [route for route in routes if route.get("status") == "complete_accepted_v2_5"]
    if len(accepted) != EXPECTED_STUDENT_SHARD_COUNT:
        errors.append(
            f"Student routes are not 15/15 accepted: {len(accepted)}/{EXPECTED_STUDENT_SHARD_COUNT}"
        )
    accepted_total = 0
    for route in routes:
        status = route.get("status")
        exceptional_v2_6 = (
            route.get("route_id") == post_capture_exception.EXCEPTION_ROUTE_ID
            and payload.get("collector_version") == "2.6"
        )
        expected_status = (
            "complete_accepted_v2_6_v3_post_capture_exception"
            if exceptional_v2_6
            else "complete_accepted_v2_5"
        )
        if status != expected_status:
            errors.append(f"Student route is not accepted: {route.get('route_id')}")
            continue
        artifact = route.get("accepted_artifact")
        if not isinstance(artifact, dict):
            errors.append(f"accepted Student route lacks artifact: {route.get('route_id')}")
            continue
        if artifact.get("path") != route.get("expected_canonical_path"):
            errors.append(f"accepted Student path mismatch: {route.get('route_id')}")
            continue
        path = root / artifact["path"]
        if not path.is_file():
            errors.append(f"accepted Student artifact missing: {artifact['path']}")
            continue
        observed_hash = sha256_file(path)
        if observed_hash != artifact.get("sha256"):
            errors.append(f"accepted Student artifact hash mismatch: {artifact['path']}")
        payload = load_json(path)
        if isinstance(artifact.get("reported_total"), int):
            accepted_total += artifact["reported_total"]
        required = (
            payload.get("collector_version") == "2.5"
            and payload.get("complete") is True
            and payload.get("segment", {}).get("start") == route.get("start")
            and payload.get("segment", {}).get("end") == route.get("end")
            and payload.get("segment", {}).get("query") == route.get("query")
            and payload.get("requested_container", {}).get("channel_id") == STUDENT_ID
            and payload.get("requested_container", {}).get("channel_name") == "student-breakdowns"
            and payload.get("requested_container", {}).get("channel_kind") == "text channel"
            and payload.get("reported_total") == payload.get("captured_rows")
            and payload.get("reported_total") == payload.get("unique_message_ids")
            and artifact.get("reported_total") == payload.get("reported_total")
            and artifact.get("captured_rows") == payload.get("captured_rows")
            and payload.get("gap_indices") in ([], None)
            and payload.get("container_mismatch_count") == 0
            and payload.get("completion_evidence_validation", {}).get("valid") is True
            and payload.get("completion_evidence", {}).get("terminal_state")
            in {"stable_bottom", "stable_empty"}
        )
        if not required:
            errors.append(f"accepted Student artifact no longer passes v2.5 QA: {artifact['path']}")
    if accepted_total != EXPECTED_STUDENT_REPORTED_TOTAL:
        errors.append(
            f"Student accepted reported total is {accepted_total}, "
            f"expected {EXPECTED_STUDENT_REPORTED_TOTAL}"
        )


def validate_questions_acceptance(
    root: Path, routes: list[dict[str, Any]], errors: list[str]
) -> dict[str, Any]:
    accepted_segments: list[tuple[str, str]] = []
    accepted_total = 0
    scheduled_paths = {
        str(route.get("expected_canonical_path")) for route in routes
    }
    raw_segments = root / "raw" / "channel_segments"
    for path in sorted(raw_segments.glob(f"channel_questions_{QUESTIONS_ID}_*.json")):
        if path.name.endswith(TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX):
            continue
        relative = path.relative_to(root).as_posix()
        if ".partial.json" not in path.name and relative not in scheduled_paths:
            errors.append(f"unplanned Questions canonical path present: {relative}")
    for sidecar in sorted(
        raw_segments.glob(
            f"channel_questions_{QUESTIONS_ID}_*{TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX}"
        )
    ):
        source_name = (
            sidecar.name[: -len(TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX)]
            + ".json"
        )
        source_path = sidecar.with_name(source_name)
        source_relative = source_path.relative_to(root).as_posix()
        if not source_path.is_file() or source_relative not in scheduled_paths:
            errors.append(
                "unbound or unplanned Questions timestamp-scope sidecar: "
                f"{sidecar.relative_to(root).as_posix()}"
            )
    for route in routes:
        start = str(route.get("start"))
        end = str(route.get("end"))
        status = route.get("status")
        expected_path = root / str(route.get("expected_canonical_path"))
        canonical_exists = expected_path.is_file()
        if not canonical_exists:
            if not str(status).startswith("pending_fresh_v2_5"):
                errors.append(
                    f"Questions route without a canonical is not pending: {route.get('route_id')}"
                )
            if "accepted_artifact" in route:
                errors.append(
                    f"Questions pending route has an accepted artifact: {route.get('route_id')}"
                )
            continue

        accepted_segments.append((start, end))
        artifact = route.get("accepted_artifact")
        if not isinstance(artifact, dict):
            errors.append(f"accepted Questions route lacks artifact: {route.get('route_id')}")
            artifact = {}
        if artifact.get("path") != route.get("expected_canonical_path"):
            errors.append(f"accepted Questions path mismatch: {route.get('route_id')}")
        if ".partial.json" in str(artifact.get("path")):
            errors.append(f"Questions partial artifact accepted: {artifact.get('path')}")
        path = expected_path
        observed_hash = sha256_file(path)
        if observed_hash != artifact.get("sha256"):
            errors.append(f"accepted Questions canonical hash mismatch: {artifact.get('path')}")
        payload = load_json(path)
        exceptional_v2_6 = (
            route.get("route_id") == post_capture_exception.EXCEPTION_ROUTE_ID
            and payload.get("collector_version") == "2.6"
        )
        expected_status = (
            "complete_accepted_v2_6_v3_post_capture_exception"
            if exceptional_v2_6
            else "complete_accepted_v2_5"
        )
        if status != expected_status:
            errors.append(
                f"Questions exact completed canonical is not accepted: {route.get('route_id')}"
            )
        reported = payload.get("reported_total")
        if isinstance(reported, int):
            accepted_total += reported
        requested = payload.get("requested_container", {})
        completion = payload.get("completion_evidence", {})
        version_accepted = payload.get("collector_version") == "2.5"
        if exceptional_v2_6:
            exception_errors = post_capture_exception.validate_promotable_copy(
                path, root=root, require_v3_current_schedule=False
            )
            if exception_errors:
                errors.extend(
                    f"accepted Questions post-capture exception invalid: {error}: "
                    f"{artifact.get('path')}" for error in exception_errors
                )
            else:
                version_accepted = True
        required = (
            payload.get("guild_id") == "1167376964680691732"
            and version_accepted
            and payload.get("collection_scope") == "channel-scoped"
            and payload.get("complete") is True
            and payload.get("segment", {}).get("start") == route.get("start")
            and payload.get("segment", {}).get("end") == route.get("end")
            and payload.get("segment", {}).get("query") == route.get("query")
            and requested.get("channel_id") == QUESTIONS_ID
            and requested.get("channel_name") == QUESTIONS_NAME
            and requested.get("channel_kind") == "text channel"
            and requested.get("category_name") == QUESTIONS_CATEGORY_NAME
            and requested.get("channel_id_source") == "inventory_exact_href"
            and isinstance(reported, int)
            and reported >= 0
            and reported == payload.get("captured_rows")
            and reported == payload.get("unique_message_ids")
            and artifact.get("reported_total") == reported
            and artifact.get("captured_rows") == payload.get("captured_rows")
            and payload.get("gap_indices") in ([], None)
            and payload.get("container_mismatch_count") == 0
            and payload.get("container_mismatch_message_ids") in ([], None)
            and payload.get("forum_group_navigation_unresolved_count") == 0
            and payload.get("pages_captured") == payload.get("reported_pages")
            and payload.get("completion_evidence_validation", {}).get("valid") is True
            and completion.get("terminal_state") in {"stable_bottom", "stable_empty"}
            and completion.get("query") == route.get("query")
            and completion.get("reported_total") == reported
            and completion.get("reported_pages") == payload.get("reported_pages")
            and completion.get("search_submission", {}).get("query") == route.get("query")
            and completion.get("search_submission", {}).get("mode")
            in {"fresh", "reuse_active_positive", "reuse_active_empty"}
            and artifact.get("full_qa_passed") is True
            and artifact.get("hash_binding_policy") == "sha256_of_exact_canonical_bytes"
        )
        if not required:
            errors.append(
                "accepted Questions artifact no longer passes exact v2.5 QA or the "
                f"one exact V3-bound v2.6 exception: {artifact.get('path')}"
            )

        messages = payload.get("messages")
        if not isinstance(messages, list) or len(messages) != reported:
            errors.append(
                f"accepted Questions message array length mismatch: {artifact.get('path')}"
            )
            messages = []
        message_ids = [message.get("message_id") for message in messages]
        valid_message_ids = all(
            isinstance(message_id, str) and message_id.isdigit()
            for message_id in message_ids
        )
        if not valid_message_ids or len(set(message_ids)) != len(message_ids):
            errors.append(f"accepted Questions message-ID QA failed: {artifact.get('path')}")
        result_indices = [message.get("result_index") for message in messages]
        if result_indices != list(range(1, len(messages) + 1)):
            errors.append(f"accepted Questions result-index QA failed: {artifact.get('path')}")
        timestamp_revalidation = (
            timestamp_scope_revalidation.load_adjacent_timestamp_scope_revalidation(
                path,
                payload,
                source_artifact_sha256=observed_hash,
                artifact_root=root,
            )
        )
        timestamp_scope_integrity = (
            timestamp_scope_revalidation.audit_segment_timestamp_scopes(
                messages, timestamp_revalidation
            )
        )
        if not timestamp_scope_integrity["passed"]:
            errors.append(
                "accepted Questions timestamp-scope integrity failed: "
                f"{artifact.get('path')}"
            )
        if artifact.get("timestamp_scope_integrity") != timestamp_scope_integrity:
            errors.append(
                "accepted Questions timestamp-scope summary mismatch: "
                f"{artifact.get('path')}"
            )
        expected_executed_command_ids: list[str] = []
        if start == "2026-06-30" and end == "2026-07-06":
            expected_executed_command_ids = [
                reply_provenance_contract.EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID
            ]
        executed_command_reply_provenance_integrity = (
            reply_provenance_contract.audit_executed_command_contexts(
                messages,
                expected_message_ids=expected_executed_command_ids,
            )
        )
        if not executed_command_reply_provenance_integrity["passed"]:
            errors.append(
                "accepted Questions executed-command reply provenance failed: "
                f"{artifact.get('path')}"
            )
        if artifact.get(
            "executed_command_reply_provenance_integrity"
        ) != executed_command_reply_provenance_integrity:
            errors.append(
                "accepted Questions executed-command reply provenance summary mismatch: "
                f"{artifact.get('path')}"
            )
        expected_source_files = [
            {
                "role": "canonical_segment",
                "path": path.relative_to(root).as_posix(),
                "sha256": observed_hash,
                "bytes": path.stat().st_size,
            }
        ]
        for source in timestamp_revalidation.source_artifacts():
            source_path = Path(source["path"])
            expected_source_files.append(
                {
                    "role": str(
                        source.get("kind") or "timestamp_scope_evidence"
                    ),
                    "path": source_path.relative_to(root).as_posix(),
                    "sha256": sha256_file(source_path),
                    "bytes": source_path.stat().st_size,
                }
            )
        if exceptional_v2_6:
            expected_source_files.extend(post_capture_exception.bound_source_files(root))
        if artifact.get("source_files") != expected_source_files:
            errors.append(
                "accepted Questions bound source-file set mismatch: "
                f"{artifact.get('path')}"
            )
        permalink_prefix = (
            f"https://discord.com/channels/1167376964680691732/{QUESTIONS_ID}/"
        )
        for index, message in enumerate(messages, start=1):
            message_id = message.get("message_id")
            required_message_fields = {
                "search_query": route.get("query"),
                "result_index": index,
                "result_set_size": reported,
                "collection_channel_id": QUESTIONS_ID,
                "collection_channel_name": QUESTIONS_NAME,
                "collection_channel_kind": "text channel",
                "collection_category_name": QUESTIONS_CATEGORY_NAME,
                "collection_channel_id_source": "inventory_exact_href",
                "content_scope_exact": True,
                "exact_permalink_status": "exact_inventoried_channel_id",
                "exact_parent_forum_conflict_detected": False,
                "exact_permalink_conflict_detected": False,
            }
            if any(
                message.get(key) != expected
                for key, expected in required_message_fields.items()
            ):
                errors.append(
                    f"accepted Questions message-level exact-scope QA failed: "
                    f"{artifact.get('path')} result {index}"
                )
                break
            if message.get("exact_permalink") != f"{permalink_prefix}{message_id}":
                errors.append(
                    f"accepted Questions exact permalink QA failed: "
                    f"{artifact.get('path')} result {index}"
                )
                break

        if valid_message_ids:
            ordered_ids = sorted(message_ids, key=int)
            message_id_set_sha256 = hashlib.sha256(
                json.dumps(ordered_ids, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if artifact.get("message_id_set_sha256") != message_id_set_sha256:
                errors.append(
                    f"accepted Questions message-ID set hash mismatch: {artifact.get('path')}"
                )

        terminal = completion.get("terminal_state")
        if terminal == "stable_bottom":
            bottom = completion.get("stable_bottom", {})
            observations = bottom.get("observations", [])
            if bottom.get("required_observations", 0) < 2 or len(observations) < 2:
                errors.append(
                    f"accepted Questions stable-bottom evidence is incomplete: {artifact.get('path')}"
                )
            for observation in observations:
                if not (
                    observation.get("query") == route.get("query")
                    and observation.get("result_set_size") == reported
                    and observation.get("last_result_index") == reported
                    and observation.get("has_enabled_next") is False
                ):
                    errors.append(
                        f"accepted Questions stable-bottom observation mismatch: "
                        f"{artifact.get('path')}"
                    )
                    break
        elif terminal == "stable_empty" and not (reported == 0 and messages == []):
            errors.append(
                f"accepted Questions stable-empty evidence mismatch: {artifact.get('path')}"
            )

    return {
        "accepted_route_count": len(accepted_segments),
        "pending_route_count": len(routes) - len(accepted_segments),
        "accepted_reported_total": accepted_total,
        "accepted_segments": accepted_segments,
    }


def validate_premium_acceptance(
    root: Path,
    routes: list[dict[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    """Independently re-derive every authoritative Premium route and closure."""

    discovery_errors = premium_contract.validate_authoritative_directory(root, routes)
    errors.extend(
        f"Premium authoritative directory: {reason}"
        for reason in discovery_errors
    )
    accepted_audits: list[dict[str, Any]] = []
    for route in routes:
        route_id = str(route.get("route_id") or "")
        expected_relative = str(route.get("expected_canonical_path") or "")
        expected_path = root / expected_relative
        status = str(route.get("status") or "")
        if not expected_path.is_file():
            retired_v2_7_cutover = (
                status == "retired_by_v2_7_authority_activation"
                and route.get("start") == "2026-01-09"
                and route.get("end") == "2026-01-09"
                and route.get("authority_retirement_migration_id")
                == "premium-journals-v2-7-authority-2026-01-09-v1"
            )
            if not status.startswith("pending_fresh_v2_6") and not retired_v2_7_cutover:
                errors.append(
                    f"Premium route without a canonical is not pending: {route_id}"
                )
            if "accepted_artifact" in route:
                errors.append(
                    f"Premium pending route has an accepted artifact: {route_id}"
                )
            continue
        if status != "complete_accepted_v2_6":
            errors.append(
                f"Premium exact completed canonical is not accepted: {route_id}"
            )
        artifact = route.get("accepted_artifact")
        if not isinstance(artifact, dict):
            errors.append(f"accepted Premium route lacks artifact: {route_id}")
            artifact = {}
        if artifact.get("path") != expected_relative:
            errors.append(f"accepted Premium path mismatch: {route_id}")
        if not expected_relative.startswith(
            premium_contract.AUTHORITATIVE_DIRECTORY + "/"
        ):
            errors.append(f"accepted Premium route is outside v2.5 root: {route_id}")
        try:
            audit = premium_contract.audit_premium_canonical(
                expected_path, route, artifact_root=root
            )
        except premium_contract.PremiumJournalsContractError as exc:
            errors.append(str(exc))
            continue
        accepted_audits.append(audit)
        if artifact != audit["accepted_artifact"]:
            errors.append(
                f"accepted Premium artifact summary does not match byte/content "
                f"rederivation: {route_id}"
            )

    reconciliation_path = (
        root / "working" / "premium_journals_scoped_inventory_reconciliation.json"
    )
    if not reconciliation_path.is_file():
        errors.append("Premium reconciliation artifact missing")
        reconciliation: dict[str, Any] = {}
    else:
        reconciliation = load_json(reconciliation_path)
    try:
        summary = premium_contract.derive_premium_summary(
            routes, accepted_audits, reconciliation
        )
    except premium_contract.PremiumJournalsContractError as exc:
        errors.append(str(exc))
        summary = {
            "accepted_route_count": len(accepted_audits),
            "pending_route_count": len(routes) - len(accepted_audits),
            "accepted_reported_total": sum(
                int(audit["accepted_artifact"]["reported_total"])
                for audit in accepted_audits
            ),
            "premium_thread_census": {},
        }
    if reconciliation_path.is_file():
        summary["premium_thread_census"]["reconciliation_sha256"] = sha256_file(
            reconciliation_path
        )
    return summary


def validate_student_reconciliation(
    root: Path,
    schedule: dict[str, Any],
    routes: list[dict[str, Any]],
    errors: list[str],
) -> None:
    record = schedule.get("student_breakdowns_reconciliation")
    if not isinstance(record, dict):
        errors.append("Student full-window reconciliation record missing")
        return
    expected_record = {
        "path": STUDENT_RECONCILIATION_RELATIVE_PATH,
        "sha256": EXPECTED_STUDENT_RECONCILIATION_SHA256,
        "status": "reconciled",
        "shard_count": EXPECTED_STUDENT_SHARD_COUNT,
        "calendar_day_count": EXPECTED_STUDENT_CALENDAR_DAY_COUNT,
        "accepted_reported_total": EXPECTED_STUDENT_REPORTED_TOTAL,
        "fresh_full_window_reported_total": EXPECTED_STUDENT_REPORTED_TOTAL,
    }
    for key, expected in expected_record.items():
        if record.get(key) != expected:
            errors.append(f"Student reconciliation record {key} mismatch")

    path = root / STUDENT_RECONCILIATION_RELATIVE_PATH
    if not path.is_file():
        errors.append("Student full-window reconciliation artifact missing")
        return
    observed_hash = sha256_file(path)
    if observed_hash != EXPECTED_STUDENT_RECONCILIATION_SHA256:
        errors.append(
            "Student full-window reconciliation artifact hash mismatch: "
            f"{observed_hash}"
        )
        return

    payload = load_json(path)
    expected_top_level = {
        "schema_version": "1.0.0",
        "artifact_type": "discord_full_window_reconciliation_observation",
        "guild_id": "1167376964680691732",
        "channel_id": STUDENT_ID,
        "channel_name": "student-breakdowns",
        "window_start_inclusive": "2026-01-01",
        "window_end_inclusive": "2026-07-20",
        "timezone": "America/Chicago",
        "full_window_query": "in:student-breakdowns after:2025-12-31 before:2026-07-21",
        "outside_sources_used": False,
    }
    for key, expected in expected_top_level.items():
        if payload.get(key) != expected:
            errors.append(f"Student reconciliation {key} mismatch")

    union = payload.get("local_shard_union", {})
    expected_union = {
        "shard_count": EXPECTED_STUDENT_SHARD_COUNT,
        "calendar_day_count": EXPECTED_STUDENT_CALENDAR_DAY_COUNT,
        "exact_contiguous_partition": True,
        "all_complete_collector_v2_5": True,
        "all_terminal_evidence_valid": True,
        "cross_shard_duplicate_message_id_count": 0,
        "sum_reported_totals": EXPECTED_STUDENT_REPORTED_TOTAL,
        "unique_message_id_count": EXPECTED_STUDENT_REPORTED_TOTAL,
    }
    for key, expected in expected_union.items():
        if union.get(key) != expected:
            errors.append(f"Student reconciliation local_shard_union.{key} mismatch")
    if record.get("message_id_set_sha256") != union.get("message_id_set_sha256"):
        errors.append("Student reconciliation message-ID set hash mismatch")

    expected_shards = []
    for route in routes:
        artifact = route.get("accepted_artifact", {})
        expected_shards.append(
            {
                "path": route.get("expected_canonical_path"),
                "sha256": artifact.get("sha256"),
                "reported_total": artifact.get("reported_total"),
            }
        )
    if payload.get("canonical_shards") != expected_shards:
        errors.append("Student reconciliation canonical shard bindings mismatch")

    fresh = payload.get("fresh_full_window_search", {})
    for key, expected in {
        "mode": "fresh",
        "submission_count": 1,
        "state": "positive",
        "reported_total": EXPECTED_STUDENT_REPORTED_TOTAL,
        "pagination_performed": False,
        "shard_data_rewritten": False,
    }.items():
        if fresh.get(key) != expected:
            errors.append(f"Student reconciliation fresh_full_window_search.{key} mismatch")

    coverage = payload.get("student_message_window_coverage", {})
    for key, expected in {
        "status": "reconciled",
        "window_start_inclusive": "2026-01-01",
        "window_end_inclusive": "2026-07-20",
        "local_union_count": EXPECTED_STUDENT_REPORTED_TOTAL,
        "fresh_full_window_reported_total": EXPECTED_STUDENT_REPORTED_TOTAL,
        "criteria_satisfied": True,
    }.items():
        if coverage.get(key) != expected:
            errors.append(f"Student reconciliation coverage {key} mismatch")


def validate_questions_identity(
    root: Path, schedule: dict[str, Any], errors: list[str]
) -> None:
    identity = schedule.get("questions_identity")
    if not isinstance(identity, dict):
        errors.append("Questions exact identity binding missing")
        return
    inventory_path = root / "full_server_channel_inventory.json"
    expected_identity = {
        "channel_id": QUESTIONS_ID,
        "exact_visible_name": QUESTIONS_NAME,
        "logical_name": QUESTIONS_LOGICAL_NAME,
        "kind": "text channel",
        "visible_parent_category": QUESTIONS_CATEGORY_NAME,
        "exact_query_prefix": QUESTIONS_QUERY_PREFIX,
        "rejected_normalized_query_prefixes": ["in:questions", "in:live"],
        "authorization_path": "authorized_collection_scope.json",
        "authorization_sha256": sha256_file(root / "authorized_collection_scope.json"),
        "inventory_path": "full_server_channel_inventory.json",
        "inventory_sha256": sha256_file(inventory_path),
    }
    for key, expected in expected_identity.items():
        if identity.get(key) != expected:
            errors.append(f"Questions exact identity {key} mismatch")

    inventory = load_json(inventory_path)
    matches = [
        item
        for item in inventory.get("channels", [])
        if item.get("channel_id") == QUESTIONS_ID
    ]
    if len(matches) != 1:
        errors.append("Questions ID does not resolve exactly once in authenticated inventory")
        return
    item = matches[0]
    for key, expected in {
        "name": QUESTIONS_NAME,
        "kind": "text channel",
        "href": f"/channels/1167376964680691732/{QUESTIONS_ID}",
        "exact_id_known": True,
        "category_name": QUESTIONS_CATEGORY_NAME,
        "full_window_query": (
            f"{QUESTIONS_QUERY_PREFIX} after:2025-12-31 before:2026-07-21"
        ),
        "count_status": "ok",
    }.items():
        if item.get(key) != expected:
            errors.append(f"Questions authenticated inventory {key} mismatch")


def validate_questions_acceptance_policy(
    schedule: dict[str, Any], errors: list[str]
) -> None:
    expected = {
        "discovery": "exact_scheduled_canonical_path_exists",
        "acceptance_mode": "strict_content_bound_fail_closed",
        "collector_version_required": "2.5",
        "one_exact_v3_bound_post_capture_exception": {
            "route_id": post_capture_exception.EXCEPTION_ROUTE_ID,
            "collector_version": "2.6",
            "exception_path": post_capture_exception.EXCEPTION_PATH.relative_to(ROOT).as_posix(),
            "all_other_questions_routes_remain_collector_version": "2.5",
        },
        "complete_required": True,
        "exact_route_query_channel_date_required": True,
        "completion_evidence_validation_required": True,
        "message_level_full_qa_required": True,
        "canonical_sha256_binding_required": True,
        "timestamp_scope_semantic_predicate_required": True,
        "adjacent_timestamp_revalidation_hash_binding_required": True,
        "executed_command_reply_provenance_semantic_predicate_required": True,
        "partial_artifacts_accepted": False,
        "unplanned_canonical_path_action": "raise_and_stop_schedule_generation",
        "validation_failure_action": "raise_and_stop_schedule_generation",
    }
    legacy_expected = dict(expected)
    legacy_expected.pop("one_exact_v3_bound_post_capture_exception")
    policy = schedule.get("questions_acceptance_policy")
    has_exceptional_route = any(
        isinstance(route, dict)
        and route.get("route_id") == post_capture_exception.EXCEPTION_ROUTE_ID
        and route.get("status") == "complete_accepted_v2_6_v3_post_capture_exception"
        for route in schedule.get("routes", {}).get("questions", [])
    )
    if policy != expected and (policy != legacy_expected or has_exceptional_route):
        errors.append("Questions strict content-bound acceptance policy mismatch")


def validate_premium_acceptance_policy(
    schedule: dict[str, Any], errors: list[str]
) -> None:
    expected = {
        "discovery": "exact_scheduled_canonical_path_in_dedicated_v2_5_root",
        "authoritative_canonical_directory": premium_contract.AUTHORITATIVE_DIRECTORY,
        "legacy_premium_directory": premium_contract.LEGACY_PRESERVATION_DIRECTORY,
        "legacy_premium_directory_policy": "preservation_only_not_authoritative",
        "acceptance_mode": "strict_content_and_forum_provenance_bound_fail_closed",
        "collector_version_required": premium_contract.COLLECTOR_VERSION,
        "complete_required": True,
        "exact_daily_route_query_parent_and_date_required": True,
        "stable_empty_or_stable_bottom_required": True,
        "page_result_continuity_required": True,
        "double_sampled_forum_group_membership_runtime_contract_required": True,
        "exact_authenticated_group_header_navigation_required": True,
        "exact_parent_forum_source_and_back_navigation_required": True,
        "immutable_page_plan_and_group_checkpoint_files_required": True,
        "page_plan_and_checkpoint_byte_hash_binding_required": True,
        "row_owned_exact_child_thread_id_required": True,
        "inferred_or_bootstrap_selected_thread_provenance_allowed": False,
        "timestamp_reply_attachment_semantic_contracts_required": True,
        "canonical_and_message_id_set_sha256_binding_required": True,
        "partial_artifacts_accepted": False,
        "unplanned_or_duplicate_canonical_or_sidecar_action": (
            "raise_and_stop_schedule_generation"
        ),
        "validation_failure_action": "raise_and_stop_schedule_generation",
    }
    if schedule.get("premium_journals_acceptance_policy") != expected:
        errors.append("Premium strict v2.6 forum acceptance policy mismatch")


def validate_coverage_assertions(
    schedule: dict[str, Any],
    questions_summary: dict[str, Any],
    premium_summary: dict[str, Any],
    errors: list[str],
) -> None:
    assertions = schedule.get("coverage_assertions", {})
    student = assertions.get("student_breakdowns", {})
    for key, expected in {
        "route_count": EXPECTED_STUDENT_SHARD_COUNT,
        "span_days": 14,
        "calendar_day_count": EXPECTED_STUDENT_CALENDAR_DAY_COUNT,
        "exact_nonoverlapping_cover": True,
        "accepted_route_count": EXPECTED_STUDENT_SHARD_COUNT,
        "pending_route_count": 0,
        "accepted_reported_total": EXPECTED_STUDENT_REPORTED_TOTAL,
        "full_window_reconciled": True,
    }.items():
        if student.get(key) != expected:
            errors.append(f"Student coverage assertion {key} mismatch")
    questions = assertions.get("questions", {})
    for key, expected in {
        "route_count": 33,
        "calendar_day_count": 201,
        "legacy_daily_route_count": 5,
        "legacy_daily_span_days": 1,
        "weekly_route_count": 28,
        "weekly_span_days": 7,
        "weekly_start": "2026-01-06",
        "weekly_end": "2026-07-20",
        "exact_nonoverlapping_cover": True,
    }.items():
        if questions.get(key) != expected:
            errors.append(f"Questions coverage assertion {key} mismatch")
    for key in (
        "accepted_route_count",
        "pending_route_count",
        "accepted_reported_total",
    ):
        if questions.get(key) != questions_summary.get(key):
            errors.append(
                f"Questions coverage assertion {key} does not match "
                "independently derived canonical state"
            )
    questions_parent = next(
        (
            parent
            for parent in schedule.get("parents", [])
            if parent.get("channel_id") == QUESTIONS_ID
        ),
        {},
    )
    for key in (
        "accepted_route_count",
        "pending_route_count",
        "accepted_reported_total",
    ):
        if questions_parent.get(key) != questions_summary.get(key):
            errors.append(
                f"Questions parent {key} does not match independently derived canonical state"
            )
    premium = assertions.get("premium_journals", {})
    for key, expected in {
        "route_count": 201,
        "span_days": 1,
        "calendar_day_count": 201,
        "exact_nonoverlapping_cover": True,
        "exact_forum_navigation_required_for_every_route": True,
    }.items():
        if premium.get(key) != expected:
            errors.append(f"Premium coverage assertion {key} mismatch")
    census = premium_summary.get("premium_thread_census", {})
    for key, expected in {
        "accepted_route_count": premium_summary.get("accepted_route_count"),
        "pending_route_count": premium_summary.get("pending_route_count"),
        "accepted_reported_total": premium_summary.get("accepted_reported_total"),
        "observed_message_bearing_child_thread_count": census.get(
            "observed_message_bearing_child_thread_count"
        ),
        "message_data_scope_closure_proven": census.get("closure_proven"),
    }.items():
        if premium.get(key) != expected:
            errors.append(
                f"Premium coverage assertion {key} does not match canonical rederivation"
            )
    premium_parent = next(
        (
            parent
            for parent in schedule.get("parents", [])
            if parent.get("channel_id") == PREMIUM_ID
        ),
        {},
    )
    for key in (
        "accepted_route_count",
        "pending_route_count",
        "accepted_reported_total",
    ):
        if premium_parent.get(key) != premium_summary.get(key):
            errors.append(
                f"Premium parent {key} does not match canonical rederivation"
            )


def validate_preservation(root: Path, schedule: dict[str, Any], errors: list[str]) -> None:
    accepted_paths = {
        route.get("accepted_artifact", {}).get("path")
        for routes in schedule.get("routes", {}).values()
        if isinstance(routes, list)
        for route in routes
        if isinstance(route, dict)
        and route.get("status") in {"complete_accepted_v2_5", "complete_accepted_v2_6"}
    }
    preservation = schedule.get("preservation_only_artifacts", [])
    for artifact in preservation:
        relative = artifact.get("path")
        if relative in accepted_paths:
            errors.append(f"accepted artifact is also marked preservation-only: {relative}")
        if artifact.get("accepted_for_scoped_release") is not False:
            errors.append(f"legacy/quarantined artifact incorrectly accepted: {relative}")
        if artifact.get("policy") not in {
            "preservation_only_not_accepted",
            "quarantined_incomplete_not_accepted",
        }:
            errors.append(f"invalid preservation policy: {relative}")
        path = root / str(relative)
        if not path.is_file():
            errors.append(f"preservation artifact missing: {relative}")
        elif sha256_file(path) != artifact.get("sha256"):
            errors.append(f"preservation artifact hash mismatch: {relative}")
    jan5_partial = [
        artifact for artifact in preservation if artifact.get("path") == JAN5_LEGACY_PARTIAL_PATH
    ]
    if len(jan5_partial) != 1:
        errors.append("pre-existing Jan 5 Questions partial is not preserved exactly once")
    else:
        artifact = jan5_partial[0]
        if artifact.get("sha256") != JAN5_LEGACY_PARTIAL_SHA256:
            errors.append("pre-existing Jan 5 Questions partial hash binding mismatch")
        if artifact.get("category") != "questions_incomplete_partial":
            errors.append("pre-existing Jan 5 Questions partial preservation category mismatch")
        if artifact.get("accepted_for_scoped_release") is not False:
            errors.append("pre-existing Jan 5 Questions partial was incorrectly accepted")


def validate_schedule(
    root: Path = ROOT,
    schedule_path: Path | None = None,
    schedule_data: dict[str, Any] | None = None,
) -> list[str]:
    schedule = copy.deepcopy(schedule_data) if schedule_data is not None else load_json(
        schedule_path or (root / "working" / DEFAULT_SCHEDULE.name)
    )
    errors: list[str] = []
    add(errors, schedule.get("schema_version") == "1.0.0", "schema version mismatch")
    add(
        errors,
        schedule.get("artifact_type") == "scoped_three_parent_collection_schedule",
        "artifact type mismatch",
    )
    add(errors, schedule.get("guild_id") == "1167376964680691732", "guild ID mismatch")
    add(
        errors,
        schedule.get("window")
        == {
            "timezone": "America/Chicago",
            "start_date_inclusive": "2026-01-01",
            "end_date_inclusive": "2026-07-20",
        },
        "window mismatch",
    )
    validate_source_hashes(root, schedule, errors)
    validate_scope(root, schedule, errors)
    validate_questions_identity(root, schedule, errors)
    validate_questions_acceptance_policy(schedule, errors)
    validate_premium_acceptance_policy(schedule, errors)

    routes = schedule.get("routes")
    if not isinstance(routes, dict):
        errors.append("routes missing")
        routes = {}
    add(errors, set(routes) == set(EXPECTED), "unauthorized or missing route group")
    questions_summary: dict[str, Any] = {
        "accepted_route_count": 0,
        "pending_route_count": 0,
        "accepted_reported_total": 0,
        "accepted_segments": [],
    }
    premium_summary: dict[str, Any] = {
        "accepted_route_count": 0,
        "pending_route_count": 201,
        "accepted_reported_total": 0,
        "premium_thread_census": {},
    }
    for key, spec in EXPECTED.items():
        route_list = routes.get(key, [])
        if not isinstance(route_list, list):
            errors.append(f"route group {key} is not a list")
            continue
        validate_route_cover(key, route_list, spec, errors)
        if key == "student_breakdowns":
            validate_student_acceptance(root, route_list, errors)
            validate_student_reconciliation(root, schedule, route_list, errors)
        elif key == "questions":
            questions_summary = validate_questions_acceptance(root, route_list, errors)
        else:
            premium_summary = validate_premium_acceptance(root, route_list, errors)

    validate_coverage_assertions(
        schedule, questions_summary, premium_summary, errors
    )

    if "premium_journals_v2_7_authority_activation" in schedule:
        errors.append(
            "Premium v2.7 activation: Jan9 draft is superseded and no Jan9 "
            "activation metadata is permitted; first future target is Jan10"
        )

    policy = schedule.get("execution_policy", {})
    owner = policy.get("scraping_owner", {})
    add(
        errors,
        owner
        == {"display_name": "GPT-5.6 Terra", "model_id": "gpt-5.6-terra", "exclusive": True},
        "GPT-5.6 Terra exclusive scraping ownership mismatch",
    )
    add(
        errors,
        policy.get("heavy_pagination", {}).get("capacity") == 1,
        "heavy pagination capacity must be one",
    )
    add(
        errors,
        policy.get("minimum_query_spacing_seconds", 0) >= 60,
        "query spacing must be at least 60 seconds",
    )
    add(
        errors,
        policy.get("questions_runtime_options") == QUESTIONS_RUNTIME_OPTIONS,
        "Questions runtime options mismatch",
    )
    expected_optimization = {
        "partition_only": True,
        "message_granularity": "individual_message",
        "authorized_scope_unchanged": True,
        "daily_recovery_route_count": 5,
        "weekly_route_count": 28,
    }
    add(
        errors,
        policy.get("questions_throughput_optimization") == expected_optimization,
        "Questions throughput optimization changed scope or message granularity",
    )
    expected_resume_policy = {
        "checkpoint_resume_required": True,
        "reuse_active_search_without_new_submission": True,
        "distinct_query_minimum_spacing_seconds": 60,
        "stop_on_count_drift": True,
        "stop_on_resume_mismatch": True,
    }
    add(
        errors,
        policy.get("questions_resume_policy") == expected_resume_policy,
        "Questions checkpoint/resume policy mismatch",
    )
    add(
        errors,
        policy.get("manifest_rebuild_while_collection_writes") == "forbidden",
        "manifest rebuild guard missing",
    )
    required_stops = {
        "unauthorized_container_or_child_parent",
        "query_name_or_channel_id_mismatch",
        "route_gap_or_overlap",
        "source_hash_mismatch",
        "reported_total_change_on_resume",
        "search_count_drift",
        "resume_checkpoint_mismatch",
        "forum_group_navigation_unverifiable",
        "Discord_throttle_or_search_instability",
        "timeout_or_browser_control_reset",
    }
    add(
        errors,
        required_stops.issubset(set(policy.get("stop_on_anomaly", []))),
        "required stop-on-anomaly rules missing",
    )

    census = schedule.get("premium_thread_census", {})
    expected_census = premium_summary.get("premium_thread_census", {})
    if census != expected_census:
        errors.append(
            "Premium thread/message-scope census does not match independently "
            "rederived authoritative canonicals"
        )
    if census.get("exact_known_thread_id_lower_bound") != 158:
        errors.append("Premium thread census lower bound is not 158")
    if census.get("inventory_complete") is not False:
        errors.append("Premium forum inventory census was incorrectly declared complete")
    if census.get("obsolete_156_thread_closure_claim_inherited") is not False:
        errors.append("Premium obsolete 156-thread closure claim inherited")
    if census.get("closure_proven") is True and not (
        census.get("enumeration_complete") is True
        and census.get("full_window_union_terminal_evidence", {}).get("passed") is True
        and census.get("observed_child_union_reconciled") is True
        and premium_summary.get("accepted_route_count") == 201
        and premium_summary.get("pending_route_count") == 0
    ):
        errors.append("Premium message-data closure was declared before all closure gates passed")

    validate_preservation(root, schedule, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("schedule", nargs="?", type=Path, default=DEFAULT_SCHEDULE)
    args = parser.parse_args()
    errors = validate_schedule(ROOT, args.schedule)
    if errors:
        print(json.dumps({"valid": False, "errors": errors}, indent=2))
        return 1
    digest = sha256_file(args.schedule)
    print(json.dumps({"valid": True, "schedule": str(args.schedule), "sha256": digest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
