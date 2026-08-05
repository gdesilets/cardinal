from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import reply_provenance_contract
import timestamp_scope_revalidation
import premium_journals_provenance_contract as premium_contract
import questions_post_capture_promotion_exception as post_capture_exception


ROOT = Path(__file__).resolve().parent
OUTPUT_PATH = ROOT / "working" / "scoped_three_parent_collection_schedule.json"

AUTHORIZED_PATH = ROOT / "authorized_collection_scope.json"
FROZEN_SCHEDULE_PATH = ROOT / "working" / "two_tab_collection_schedule.json"
FULL_INVENTORY_PATH = ROOT / "full_server_channel_inventory.json"
RECONCILIATION_PATH = (
    ROOT / "working" / "premium_journals_scoped_inventory_reconciliation.json"
)
STUDENT_RECONCILIATION_PATH = (
    ROOT
    / "working"
    / "student_breakdowns_2026-01-01_2026-07-20_20260721T173220703Z.full-window-reconciliation.json"
)
EXPECTED_STUDENT_RECONCILIATION_SHA256 = (
    "caed83d1ac50ec8dc6d30abcc0d57993623bf51e85f86eb88b474f7d673c6795"
)
ACCEPTED_QUESTIONS_STATUSES = {
    "complete_accepted_v2_5",
    "complete_accepted_v2_6_v3_post_capture_exception",
}
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

EXPECTED_PARENTS = {
    STUDENT_ID: ("student-breakdowns", "text channel"),
    QUESTIONS_ID: (QUESTIONS_NAME, "text channel"),
    PREMIUM_ID: ("premium-journals", "forum channel"),
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_binding(path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def parse_iso(value: str) -> date:
    return date.fromisoformat(value)


def make_segments(start: str, end: str, span_days: int) -> list[tuple[str, str]]:
    cursor = parse_iso(start)
    end_date = parse_iso(end)
    segments: list[tuple[str, str]] = []
    while cursor <= end_date:
        segment_end = min(cursor + timedelta(days=span_days - 1), end_date)
        segments.append((cursor.isoformat(), segment_end.isoformat()))
        cursor = segment_end + timedelta(days=1)
    return segments


def find_generator(schedule: dict[str, Any], collector: str, channel_id: str) -> dict[str, Any]:
    group = schedule["action_groups"][f"{collector}_bulk_generators"]
    matches = [item for item in group["generators"] if item["channel_id"] == channel_id]
    if len(matches) != 1:
        raise ValueError(f"Expected one {collector} generator for {channel_id}; found {len(matches)}")
    return matches[0]


def exact_query(prefix: str, start: str, end: str) -> str:
    after = parse_iso(start) - timedelta(days=1)
    before = parse_iso(end) + timedelta(days=1)
    return f"{prefix} after:{after.isoformat()} before:{before.isoformat()}"


def expected_path(slug: str, channel_id: str, start: str, end: str) -> str:
    if channel_id == PREMIUM_ID:
        return premium_contract.expected_canonical_relative_path(start, end)
    return f"raw/channel_segments/channel_{slug}_{channel_id}_{start}_{end}.json"


def strict_student_acceptance(path: Path, route: dict[str, Any]) -> dict[str, Any]:
    payload = read_json(path)
    errors: list[str] = []
    expected = {
        "collector_version": "2.5",
        "complete": True,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            errors.append(f"{key}={payload.get(key)!r}, expected {value!r}")
    if payload.get("segment", {}).get("start") != route["start"]:
        errors.append("segment.start mismatch")
    if payload.get("segment", {}).get("end") != route["end"]:
        errors.append("segment.end mismatch")
    if payload.get("segment", {}).get("query") != route["query"]:
        errors.append("segment.query mismatch")
    requested = payload.get("requested_container", {})
    for key, value in {
        "channel_id": STUDENT_ID,
        "channel_name": "student-breakdowns",
        "channel_kind": "text channel",
    }.items():
        if requested.get(key) != value:
            errors.append(f"requested_container.{key} mismatch")
    reported = payload.get("reported_total")
    if not (
        isinstance(reported, int)
        and payload.get("captured_rows") == reported
        and payload.get("unique_message_ids") == reported
    ):
        errors.append("reported/captured/unique totals mismatch")
    if payload.get("gap_indices") not in ([], None):
        errors.append("gap_indices is nonempty")
    if payload.get("container_mismatch_count") != 0:
        errors.append("container mismatch")
    if payload.get("completion_evidence_validation", {}).get("valid") is not True:
        errors.append("completion evidence invalid")
    terminal = payload.get("completion_evidence", {}).get("terminal_state")
    if terminal not in {"stable_bottom", "stable_empty"}:
        errors.append("terminal state is not durable")
    if errors:
        raise ValueError(f"Existing Student canonical is not accepted: {path}: {errors}")
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "collector_version": "2.5",
        "reported_total": reported,
        "captured_rows": payload["captured_rows"],
        "completion_terminal_state": terminal,
    }


def strict_questions_acceptance(path: Path, route: dict[str, Any]) -> dict[str, Any]:
    payload = read_json(path)
    errors: list[str] = []
    observed_hash = sha256_file(path)
    exceptional_v2_6 = (
        route.get("route_id") == post_capture_exception.EXCEPTION_ROUTE_ID
        and payload.get("collector_version") == "2.6"
    )
    for key, expected in {
        "guild_id": "1167376964680691732",
        "collection_scope": "channel-scoped",
        "complete": True,
    }.items():
        if payload.get(key) != expected:
            errors.append(f"{key}={payload.get(key)!r}, expected {expected!r}")
    if exceptional_v2_6:
        post_promotion_canonical = (
            path.resolve()
            == (ROOT / post_capture_exception.EXCEPTION_TARGET_RELATIVE_PATH).resolve()
        )
        errors.extend(
            "post_capture_exception:" + error
            for error in post_capture_exception.validate_promotable_copy(
                # Historical mode is only allowed for the exact promoted canonical.
                # A staging path still requires the contemporaneous V3 schedule.
                path, require_v3_current_schedule=not post_promotion_canonical
            )
        )
    elif payload.get("collector_version") != "2.5":
        errors.append(
            f"collector_version={payload.get('collector_version')!r}, expected '2.5'"
        )
    segment = payload.get("segment", {})
    if segment.get("start") != route["start"]:
        errors.append("segment.start mismatch")
    if segment.get("end") != route["end"]:
        errors.append("segment.end mismatch")
    if segment.get("query") != route["query"]:
        errors.append("segment.query mismatch")
    if not exceptional_v2_6 and segment != {
        "start": route["start"], "end": route["end"], "query": route["query"]
    }:
        errors.append("segment has unapproved extra fields")
    requested = payload.get("requested_container", {})
    for key, expected in {
        "channel_id": QUESTIONS_ID,
        "channel_name": QUESTIONS_NAME,
        "channel_kind": "text channel",
        "category_name": QUESTIONS_CATEGORY_NAME,
        "channel_id_source": "inventory_exact_href",
    }.items():
        if requested.get(key) != expected:
            errors.append(f"requested_container.{key} mismatch")
    reported = payload.get("reported_total")
    if not (
        isinstance(reported, int)
        and reported >= 0
        and payload.get("captured_rows") == reported
        and payload.get("unique_message_ids") == reported
    ):
        errors.append("reported/captured/unique totals mismatch")
    if payload.get("gap_indices") not in ([], None):
        errors.append("gap_indices is nonempty")
    if payload.get("container_mismatch_count") != 0:
        errors.append("container mismatch")
    if payload.get("container_mismatch_message_ids") not in ([], None):
        errors.append("container mismatch IDs are nonempty")
    if payload.get("forum_group_navigation_unresolved_count") != 0:
        errors.append("forum-group navigation unresolved count is nonzero")
    if payload.get("pages_captured") != payload.get("reported_pages"):
        errors.append("pages captured/reported mismatch")
    if payload.get("completion_evidence_validation", {}).get("valid") is not True:
        errors.append("completion evidence invalid")
    completion = payload.get("completion_evidence", {})
    terminal = completion.get("terminal_state")
    if terminal not in {"stable_bottom", "stable_empty"}:
        errors.append("terminal state is not durable")
    if completion.get("query") != route["query"]:
        errors.append("completion evidence query mismatch")
    if completion.get("reported_total") != reported:
        errors.append("completion evidence total mismatch")
    if completion.get("reported_pages") != payload.get("reported_pages"):
        errors.append("completion evidence pages mismatch")
    if completion.get("search_submission", {}).get("query") != route["query"]:
        errors.append("search submission query mismatch")
    if completion.get("search_submission", {}).get("mode") not in {
        "fresh",
        "reuse_active_positive",
        "reuse_active_empty",
    }:
        errors.append("search submission mode is not an accepted fresh/resume mode")

    messages = payload.get("messages")
    if not isinstance(messages, list) or len(messages) != reported:
        errors.append("message array length does not equal reported total")
        messages = []
    message_ids = [message.get("message_id") for message in messages]
    if any(not isinstance(message_id, str) or not message_id.isdigit() for message_id in message_ids):
        errors.append("message IDs are not exact Discord snowflakes")
    if len(set(message_ids)) != len(message_ids):
        errors.append("duplicate message IDs detected")
    result_indices = [message.get("result_index") for message in messages]
    if result_indices != list(range(1, len(messages) + 1)):
        errors.append("result indices are not exact contiguous 1..N")
    timestamp_revalidation = (
        timestamp_scope_revalidation.load_adjacent_timestamp_scope_revalidation(
            path,
            payload,
            source_artifact_sha256=observed_hash,
            artifact_root=ROOT,
        )
    )
    timestamp_scope_integrity = (
        timestamp_scope_revalidation.audit_segment_timestamp_scopes(
            messages, timestamp_revalidation
        )
    )
    if not timestamp_scope_integrity["passed"]:
        errors.append(
            "timestamp-scope integrity failed: "
            + json.dumps(timestamp_scope_integrity, ensure_ascii=False, sort_keys=True)
        )
    expected_executed_command_ids: list[str] = []
    if route["start"] == "2026-06-30" and route["end"] == "2026-07-06":
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
            "executed-command reply provenance failed: "
            + json.dumps(
                executed_command_reply_provenance_integrity,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    exact_permalink_prefix = (
        f"https://discord.com/channels/1167376964680691732/{QUESTIONS_ID}/"
    )
    for index, message in enumerate(messages, start=1):
        message_id = message.get("message_id")
        required_message_fields = {
            "search_query": route["query"],
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
        if any(message.get(key) != expected for key, expected in required_message_fields.items()):
            errors.append(f"message-level exact-scope QA mismatch at result {index}")
            break
        if message.get("exact_permalink") != f"{exact_permalink_prefix}{message_id}":
            errors.append(f"message-level exact permalink mismatch at result {index}")
            break

    if terminal == "stable_bottom":
        bottom = completion.get("stable_bottom", {})
        observations = bottom.get("observations", [])
        if bottom.get("required_observations", 0) < 2 or len(observations) < 2:
            errors.append("stable-bottom evidence lacks two observations")
        for observation in observations:
            if not (
                observation.get("query") == route["query"]
                and observation.get("result_set_size") == reported
                and observation.get("last_result_index") == reported
                and observation.get("has_enabled_next") is False
            ):
                errors.append("stable-bottom observation mismatch")
                break
    elif terminal == "stable_empty" and not (reported == 0 and messages == []):
        errors.append("stable-empty terminal does not match an empty result set")

    if errors:
        raise ValueError(f"Existing Questions canonical is not accepted: {path}: {errors}")
    ordered_ids = sorted(message_ids, key=int)
    message_id_set_sha256 = hashlib.sha256(
        json.dumps(ordered_ids, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    bound_source_files = [
        {
            "role": "canonical_segment",
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": observed_hash,
            "bytes": path.stat().st_size,
        }
    ]
    for source in timestamp_revalidation.source_artifacts():
        source_path = Path(source["path"])
        bound_source_files.append(
            {
                "role": str(source.get("kind") or "timestamp_scope_evidence"),
                "path": source_path.relative_to(ROOT).as_posix(),
                "sha256": sha256_file(source_path),
                "bytes": source_path.stat().st_size,
            }
        )
    if exceptional_v2_6:
        bound_source_files.extend(post_capture_exception.bound_source_files())
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": observed_hash,
        "bytes": path.stat().st_size,
        "collector_version": payload["collector_version"],
        "reported_total": reported,
        "captured_rows": payload["captured_rows"],
        "completion_terminal_state": terminal,
        "message_id_set_sha256": message_id_set_sha256,
        "full_qa_passed": True,
        "hash_binding_policy": "sha256_of_exact_canonical_bytes",
        "timestamp_scope_integrity": timestamp_scope_integrity,
        "executed_command_reply_provenance_integrity": (
            executed_command_reply_provenance_integrity
        ),
        "source_files": bound_source_files,
    }


def strict_student_reconciliation(
    path: Path, student_routes: list[dict[str, Any]]
) -> dict[str, Any]:
    observed_hash = sha256_file(path)
    errors: list[str] = []
    if observed_hash != EXPECTED_STUDENT_RECONCILIATION_SHA256:
        errors.append(
            "reconciliation SHA-256 mismatch: "
            f"{observed_hash}, expected {EXPECTED_STUDENT_RECONCILIATION_SHA256}"
        )

    payload = read_json(path)
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
            errors.append(f"{key}={payload.get(key)!r}, expected {expected!r}")

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
            errors.append(f"local_shard_union.{key} mismatch")

    accepted_routes = [
        route for route in student_routes if route.get("status") == "complete_accepted_v2_5"
    ]
    expected_shards = [
        {
            "path": route["expected_canonical_path"],
            "sha256": route["accepted_artifact"]["sha256"],
            "reported_total": route["accepted_artifact"]["reported_total"],
        }
        for route in accepted_routes
    ]
    if payload.get("canonical_shards") != expected_shards:
        errors.append("canonical_shards do not exactly bind all accepted routes")

    fresh = payload.get("fresh_full_window_search", {})
    expected_fresh = {
        "mode": "fresh",
        "submission_count": 1,
        "state": "positive",
        "reported_total": EXPECTED_STUDENT_REPORTED_TOTAL,
        "pagination_performed": False,
        "shard_data_rewritten": False,
    }
    for key, expected in expected_fresh.items():
        if fresh.get(key) != expected:
            errors.append(f"fresh_full_window_search.{key} mismatch")

    coverage = payload.get("student_message_window_coverage", {})
    expected_coverage = {
        "status": "reconciled",
        "window_start_inclusive": "2026-01-01",
        "window_end_inclusive": "2026-07-20",
        "local_union_count": EXPECTED_STUDENT_REPORTED_TOTAL,
        "fresh_full_window_reported_total": EXPECTED_STUDENT_REPORTED_TOTAL,
        "criteria_satisfied": True,
    }
    for key, expected in expected_coverage.items():
        if coverage.get(key) != expected:
            errors.append(f"student_message_window_coverage.{key} mismatch")

    if errors:
        raise ValueError(f"Student full-window reconciliation is not accepted: {errors}")
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": observed_hash,
        "bytes": path.stat().st_size,
        "status": "reconciled",
        "shard_count": EXPECTED_STUDENT_SHARD_COUNT,
        "calendar_day_count": EXPECTED_STUDENT_CALENDAR_DAY_COUNT,
        "accepted_reported_total": EXPECTED_STUDENT_REPORTED_TOTAL,
        "fresh_full_window_reported_total": EXPECTED_STUDENT_REPORTED_TOTAL,
        "message_id_set_sha256": union.get("message_id_set_sha256"),
    }


def strict_questions_identity(
    authorized: dict[str, Any], inventory: dict[str, Any]
) -> dict[str, Any]:
    authorized_matches = [
        item
        for item in authorized.get("allowed_top_level_containers", [])
        if item.get("channel_id") == QUESTIONS_ID
    ]
    inventory_matches = [
        item
        for item in inventory.get("channels", [])
        if item.get("channel_id") == QUESTIONS_ID
    ]
    if len(authorized_matches) != 1 or len(inventory_matches) != 1:
        raise ValueError("Questions identity must resolve exactly once in authorization and inventory")
    authorized_item = authorized_matches[0]
    inventory_item = inventory_matches[0]
    expected_authorized = {
        "channel_id": QUESTIONS_ID,
        "name": QUESTIONS_NAME,
        "logical_name": QUESTIONS_LOGICAL_NAME,
        "kind": "text channel",
        "include_exact_child_threads": True,
    }
    for key, expected in expected_authorized.items():
        if authorized_item.get(key) != expected:
            raise ValueError(f"Authorized Questions identity {key} mismatch")
    expected_inventory = {
        "channel_id": QUESTIONS_ID,
        "name": QUESTIONS_NAME,
        "kind": "text channel",
        "href": f"/channels/1167376964680691732/{QUESTIONS_ID}",
        "exact_id_known": True,
        "category_name": QUESTIONS_CATEGORY_NAME,
        "full_window_query": (
            f"{QUESTIONS_QUERY_PREFIX} after:2025-12-31 before:2026-07-21"
        ),
        "count_status": "ok",
    }
    for key, expected in expected_inventory.items():
        if inventory_item.get(key) != expected:
            raise ValueError(f"Inventory Questions identity {key} mismatch")
    return {
        "channel_id": QUESTIONS_ID,
        "exact_visible_name": QUESTIONS_NAME,
        "logical_name": QUESTIONS_LOGICAL_NAME,
        "kind": "text channel",
        "visible_parent_category": QUESTIONS_CATEGORY_NAME,
        "exact_query_prefix": QUESTIONS_QUERY_PREFIX,
        "rejected_normalized_query_prefixes": ["in:questions", "in:live"],
        "authorization_path": AUTHORIZED_PATH.relative_to(ROOT).as_posix(),
        "authorization_sha256": sha256_file(AUTHORIZED_PATH),
        "inventory_path": FULL_INVENTORY_PATH.relative_to(ROOT).as_posix(),
        "inventory_sha256": sha256_file(FULL_INVENTORY_PATH),
    }


def artifact_record(path: Path, category: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "category": category,
        "accepted_for_scoped_release": False,
        "policy": "preservation_only_not_accepted",
    }
    try:
        payload = read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return record
    for key in ("collector_version", "reported_total", "captured_rows", "complete"):
        if key in payload:
            record[key] = payload[key]
    if isinstance(payload.get("segment"), dict):
        record["start"] = payload["segment"].get("start")
        record["end"] = payload["segment"].get("end")
    if payload.get("artifact_type") == "legacy_premium_journals_canonical_v2_staging_manifest":
        record["legacy_coverage"] = payload.get("coverage")
        record["legacy_scope"] = payload.get("scope")
        record["quarantined_occurrence_count"] = payload.get("quarantine", {}).get(
            "occurrence_count"
        )
    return record


def make_route(
    *,
    channel_id: str,
    name: str,
    kind: str,
    slug: str,
    query_prefix: str,
    start: str,
    end: str,
    status: str,
) -> dict[str, Any]:
    route = {
        "route_id": f"{slug}_{start}_{end}",
        "channel_id": channel_id,
        "channel_name": name,
        "channel_kind": kind,
        "start": start,
        "end": end,
        "query_prefix": query_prefix,
        "query": exact_query(query_prefix, start, end),
        "expected_canonical_path": expected_path(slug, channel_id, start, end),
        "status": status,
        "scraping_owner": "GPT-5.6 Terra",
        "heavy_pagination_lane": "discord_account_heavy_lane_1",
    }
    if channel_id == PREMIUM_ID:
        route["forum_exact_navigation"] = {
            "required": True,
            "evidence_key": "exact_query+page_number+sorted_group_message_ids",
            "trigger": "unique_direct_child_role_button_click",
            "destination": "exact_/channels/<guild_id>/<thread_id>_URL",
            "same_query_page_group_back_return_required": True,
            "title_only_identity_allowed": False,
            "attachment_or_media_channel_identity_allowed": False,
        }
    if channel_id == QUESTIONS_ID:
        route["logical_name"] = QUESTIONS_LOGICAL_NAME
        route["visible_parent_category"] = QUESTIONS_CATEGORY_NAME
        route["message_granularity"] = "individual_message"
        route["granularity"] = (
            "daily" if start == end else "weekly_7_day"
        )
        route["runtime_options"] = dict(QUESTIONS_RUNTIME_OPTIONS)
        route["resume_behavior"] = {
            "checkpoint_resume_required": True,
            "new_search_submission_on_resume_allowed": False,
            "count_drift_stop_required": True,
            "resume_mismatch_stop_required": True,
        }
    return route


def build() -> dict[str, Any]:
    authorized = read_json(AUTHORIZED_PATH)
    frozen = read_json(FROZEN_SCHEDULE_PATH)
    inventory = read_json(FULL_INVENTORY_PATH)
    reconciliation = read_json(RECONCILIATION_PATH)

    allowed = {
        item["channel_id"]: (item["name"], item["kind"])
        for item in authorized["allowed_top_level_containers"]
    }
    if allowed != EXPECTED_PARENTS:
        raise ValueError(f"Authorized scope changed: {allowed!r}")
    questions_identity = strict_questions_identity(authorized, inventory)
    if authorized.get("window") != {
        "timezone": "America/Chicago",
        "start_date_inclusive": "2026-01-01",
        "end_date_inclusive": "2026-07-20",
    }:
        raise ValueError("Authorized window changed")

    student_generator = find_generator(frozen, "A", STUDENT_ID)
    questions_generator = find_generator(frozen, "A", QUESTIONS_ID)
    premium_generator = find_generator(frozen, "B", PREMIUM_ID)
    student_segments = make_segments(
        student_generator["start"], student_generator["end"], student_generator["span_days"]
    )
    if len(student_segments) != student_generator["segment_count"]:
        raise ValueError("Student segment count does not match frozen schedule")

    questions_old = frozen["action_groups"]["A_questions_old_schema_recaptures"]
    question_partial = next(
        item
        for item in frozen["action_groups"]["A_partial_resumptions"]["actions"]
        if item["channel_id"] == QUESTIONS_ID
    )
    premium_old = next(
        item
        for item in frozen["action_groups"]["B_old_schema_nonempty_recaptures"]["actions"]
        if item["channel_id"] == PREMIUM_ID
    )
    premium_partial = next(
        item
        for item in frozen["action_groups"]["B_partial_resumptions"]["actions"]
        if item["channel_id"] == PREMIUM_ID
    )

    student_routes: list[dict[str, Any]] = []
    for start, end in student_segments:
        route = make_route(
            channel_id=STUDENT_ID,
            name="student-breakdowns",
            kind="text channel",
            slug="student_breakdowns",
            query_prefix="in:student-breakdowns",
            start=start,
            end=end,
            status="pending_fresh_v2_5_capture",
        )
        canonical = ROOT / route["expected_canonical_path"]
        if canonical.exists():
            route["status"] = "complete_accepted_v2_5"
            route["accepted_artifact"] = strict_student_acceptance(canonical, route)
        student_routes.append(route)

    old_question_dates = set(questions_old["dates"])
    question_partial_date = question_partial["date"]
    if old_question_dates != {
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
        "2026-01-04",
    }:
        raise ValueError("Frozen legacy Questions recovery dates changed")
    if question_partial_date != "2026-01-05":
        raise ValueError("Frozen Questions partial-recovery date changed")
    question_daily_segments = make_segments("2026-01-01", "2026-01-05", 1)
    question_weekly_segments = make_segments("2026-01-06", "2026-07-20", 7)
    if (
        len(question_daily_segments) != 5
        or len(question_weekly_segments) != 28
        or question_weekly_segments[0] != ("2026-01-06", "2026-01-12")
        or question_weekly_segments[-1] != ("2026-07-14", "2026-07-20")
    ):
        raise ValueError("Optimized Questions 5-daily + 28-weekly partition changed")
    question_segments = question_daily_segments + question_weekly_segments
    question_routes: list[dict[str, Any]] = []
    for start, end in question_segments:
        status = "pending_fresh_v2_5_capture"
        if start in old_question_dates:
            status = "pending_fresh_v2_5_recapture_legacy_preserved"
        elif start == question_partial_date:
            status = "pending_fresh_v2_5_restart_partial_preserved"
        route = make_route(
            channel_id=QUESTIONS_ID,
            name=QUESTIONS_NAME,
            kind="text channel",
            slug="questions",
            query_prefix=QUESTIONS_QUERY_PREFIX,
            start=start,
            end=end,
            status=status,
        )
        canonical = ROOT / route["expected_canonical_path"]
        if canonical.is_file():
            route["accepted_artifact"] = strict_questions_acceptance(canonical, route)
            route["status"] = (
                "complete_accepted_v2_6_v3_post_capture_exception"
                if route["accepted_artifact"]["collector_version"] == "2.6"
                else "complete_accepted_v2_5"
            )
        question_routes.append(route)
    scheduled_question_paths = {
        route["expected_canonical_path"] for route in question_routes
    }
    for path in sorted(
        (ROOT / "raw" / "channel_segments").glob(
            f"channel_questions_{QUESTIONS_ID}_*.json"
        )
    ):
        if path.name.endswith(TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if ".partial.json" not in path.name and relative not in scheduled_question_paths:
            raise ValueError(f"Unplanned Questions canonical path present: {relative}")
    for sidecar in sorted(
        (ROOT / "raw" / "channel_segments").glob(
            f"channel_questions_{QUESTIONS_ID}_*{TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX}"
        )
    ):
        source_name = (
            sidecar.name[: -len(TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX)]
            + ".json"
        )
        source_path = sidecar.with_name(source_name)
        source_relative = source_path.relative_to(ROOT).as_posix()
        if not source_path.is_file() or source_relative not in scheduled_question_paths:
            raise ValueError(
                "Unbound or unplanned Questions timestamp-scope sidecar: "
                f"{sidecar.relative_to(ROOT).as_posix()}"
            )
    if (questions_generator["start"], questions_generator["end"], questions_generator["span_days"]) != (
        "2026-01-06",
        "2026-07-20",
        1,
    ):
        raise ValueError("Questions bulk generator changed")

    premium_segments = make_segments("2026-01-01", "2026-07-20", 1)
    premium_routes: list[dict[str, Any]] = []
    premium_acceptance_audits: list[dict[str, Any]] = []
    for start, end in premium_segments:
        status = "pending_fresh_v2_6_capture"
        if start == premium_old["start"]:
            status = "pending_fresh_v2_6_recapture_legacy_preserved"
        elif start == premium_partial["date"]:
            status = "pending_fresh_v2_6_restart_quarantined_partials_preserved"
        route = make_route(
            channel_id=PREMIUM_ID,
            name="premium-journals",
            kind="forum channel",
            slug="premium_journals",
            query_prefix="in:premium-journals",
            start=start,
            end=end,
            status=status,
        )
        canonical = ROOT / route["expected_canonical_path"]
        if canonical.is_file():
            audit = premium_contract.audit_premium_canonical(
                canonical, route, artifact_root=ROOT
            )
            route["status"] = "complete_accepted_v2_6"
            route["accepted_artifact"] = audit["accepted_artifact"]
            premium_acceptance_audits.append(audit)
        premium_routes.append(route)
    premium_discovery_errors = premium_contract.validate_authoritative_directory(
        ROOT, premium_routes
    )
    if premium_discovery_errors:
        raise ValueError(
            "Premium v2.5 authoritative directory failed discovery: "
            + "; ".join(premium_discovery_errors)
        )
    if (premium_generator["start"], premium_generator["end"], premium_generator["span_days"]) != (
        "2026-01-03",
        "2026-07-20",
        1,
    ):
        raise ValueError("Premium Journals bulk generator changed")

    preserved: list[dict[str, Any]] = []
    raw_segments = ROOT / "raw" / "channel_segments"
    accepted_question_paths = {
        route["accepted_artifact"]["path"]
        for route in question_routes
        if route.get("status") in ACCEPTED_QUESTIONS_STATUSES
    }
    for path in sorted(raw_segments.glob(f"channel_questions_{QUESTIONS_ID}_*.json")):
        if path.name.endswith(TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative in accepted_question_paths:
            continue
        category = (
            "questions_incomplete_partial"
            if ".partial.json" in path.name
            else "questions_legacy_or_unaccepted"
        )
        preserved.append(artifact_record(path, category))
    for path in sorted(raw_segments.glob(f"channel_premium_journals_{PREMIUM_ID}_*.json")):
        if path.name.endswith(TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX):
            continue
        preserved.append(artifact_record(path, "premium_journals_legacy_v2_0"))
    premium_legacy_manifest = (
        ROOT
        / "staging"
        / "legacy_premium_journals_v2"
        / "legacy_premium_journals_v2_manifest.json"
    )
    if premium_legacy_manifest.exists():
        try:
            preserved.append(
                artifact_record(
                    premium_legacy_manifest,
                    "premium_journals_legacy_staging_manifest",
                )
            )
        except PermissionError:
            # OneDrive can leave preservation-only staging trees ACL-locked
            # after a sync interruption.  Use the byte-identical integration
            # QA copy so schedule generation remains read-only with respect to
            # accepted Discord artifacts; never silently omit the manifest.
            fallback_manifest = (
                ROOT
                / "working"
                / "integration_qa_20260720_234634"
                / "inputs"
                / "legacy_premium_journals_v2"
                / "legacy_premium_journals_v2_manifest.json"
            )
            if not fallback_manifest.is_file():
                raise
            preserved.append(
                artifact_record(
                    fallback_manifest,
                    "premium_journals_legacy_staging_manifest",
                )
            )
    premium_fresh_partial = (
        ROOT
        / "raw"
        / "quarantine_collection_errors"
        / "collector_b_premium_journals_fresh_staging_20260721"
        / f"collector_b_fresh_channel_premium_journals_{PREMIUM_ID}_2026-01-02_2026-01-02.partial.json"
    )
    if premium_fresh_partial.exists():
        partial = artifact_record(premium_fresh_partial, "premium_journals_incomplete_v2_5_partial")
        partial["policy"] = "quarantined_incomplete_not_accepted"
        preserved.append(partial)

    counts = reconciliation["counts"]
    if not (
        reconciliation.get("status") == "unresolved_census"
        and reconciliation.get("closure_proven") is False
        and counts.get("exact_known_union_thread_ids") == 158
    ):
        raise ValueError("Premium reconciliation no longer proves an unresolved 158-ID lower bound")
    premium_summary = premium_contract.derive_premium_summary(
        premium_routes, premium_acceptance_audits, reconciliation
    )
    premium_summary["premium_thread_census"]["reconciliation_sha256"] = (
        sha256_file(RECONCILIATION_PATH)
    )

    accepted_student = [r for r in student_routes if r["status"] == "complete_accepted_v2_5"]
    accepted_student_total = sum(
        route["accepted_artifact"]["reported_total"] for route in accepted_student
    )
    if len(accepted_student) != EXPECTED_STUDENT_SHARD_COUNT:
        raise ValueError(
            "Student Breakdowns must be complete before schedule regeneration: "
            f"accepted {len(accepted_student)}/{EXPECTED_STUDENT_SHARD_COUNT}"
        )
    if accepted_student_total != EXPECTED_STUDENT_REPORTED_TOTAL:
        raise ValueError(
            "Student Breakdowns accepted total mismatch: "
            f"{accepted_student_total}, expected {EXPECTED_STUDENT_REPORTED_TOTAL}"
        )
    student_reconciliation = strict_student_reconciliation(
        STUDENT_RECONCILIATION_PATH, student_routes
    )
    accepted_questions = [
        route
        for route in question_routes
        if route["status"] in ACCEPTED_QUESTIONS_STATUSES
    ]
    accepted_question_total = sum(
        route["accepted_artifact"]["reported_total"] for route in accepted_questions
    )
    if any(
        ".partial.json" in route["accepted_artifact"]["path"]
        for route in accepted_questions
    ):
        raise ValueError("A Questions partial artifact was incorrectly accepted")
    return {
        "schema_version": "1.0.0",
        "artifact_type": "scoped_three_parent_collection_schedule",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "active_scoped_schedule",
        "guild_id": authorized["guild_id"],
        "window": authorized["window"],
        "scope": {
            "authorized_parent_count": 3,
            "authorized_parent_ids": [STUDENT_ID, QUESTIONS_ID, PREMIUM_ID],
            "other_channels_and_jobs_excluded": True,
            "source_scope": "discord_only",
            "outside_sources_used": False,
        },
        "source_bindings": {
            "authorized_collection_scope": source_binding(AUTHORIZED_PATH),
            "frozen_two_tab_schedule": source_binding(FROZEN_SCHEDULE_PATH),
            "full_server_channel_inventory": source_binding(FULL_INVENTORY_PATH),
            "premium_journals_reconciliation": source_binding(RECONCILIATION_PATH),
            "student_breakdowns_full_window_reconciliation": source_binding(
                STUDENT_RECONCILIATION_PATH
            ),
        },
        "execution_policy": {
            "scraping_owner": {
                "display_name": "GPT-5.6 Terra",
                "model_id": "gpt-5.6-terra",
                "exclusive": True,
            },
            "heavy_pagination": {
                "lane_id": "discord_account_heavy_lane_1",
                "capacity": 1,
                "account_scope": "Discord account",
            },
            "minimum_query_spacing_seconds": 60,
            "global_throttle_cooldown_seconds": 300,
            "questions_runtime_options": dict(QUESTIONS_RUNTIME_OPTIONS),
            "questions_throughput_optimization": {
                "partition_only": True,
                "message_granularity": "individual_message",
                "authorized_scope_unchanged": True,
                "daily_recovery_route_count": 5,
                "weekly_route_count": 28,
            },
            "questions_resume_policy": {
                "checkpoint_resume_required": True,
                "reuse_active_search_without_new_submission": True,
                "distinct_query_minimum_spacing_seconds": 60,
                "stop_on_count_drift": True,
                "stop_on_resume_mismatch": True,
            },
            "source_schedule_immutable": True,
            "raw_and_canonical_mutation_by_schedule_builder": False,
            "manifest_rebuild_while_collection_writes": "forbidden",
            "stop_on_anomaly": [
                "unauthorized_container_or_child_parent",
                "path_lease_conflict",
                "query_name_or_channel_id_mismatch",
                "route_gap_or_overlap",
                "source_hash_mismatch",
                "reported_total_change_on_resume",
                "search_count_drift",
                "resume_checkpoint_mismatch",
                "result_index_gap_or_duplicate_message_id",
                "container_or_thread_id_conflict",
                "forum_group_navigation_unverifiable",
                "same_query_page_group_back_return_unverified",
                "Discord_throttle_or_search_instability",
                "timeout_or_browser_control_reset",
            ],
        },
        "parents": [
            {
                "channel_id": STUDENT_ID,
                "name": "student-breakdowns",
                "kind": "text channel",
                "route_count": len(student_routes),
                "accepted_route_count": len(accepted_student),
                "pending_route_count": len(student_routes) - len(accepted_student),
                "accepted_reported_total": accepted_student_total,
                "coverage_status": "reconciled",
            },
            {
                "channel_id": QUESTIONS_ID,
                "name": QUESTIONS_NAME,
                "logical_name": QUESTIONS_LOGICAL_NAME,
                "kind": "text channel",
                "visible_parent_category": QUESTIONS_CATEGORY_NAME,
                "route_count": len(question_routes),
                "accepted_route_count": len(accepted_questions),
                "pending_route_count": len(question_routes) - len(accepted_questions),
                "accepted_reported_total": accepted_question_total,
            },
            {
                "channel_id": PREMIUM_ID,
                "name": "premium-journals",
                "kind": "forum channel",
                "route_count": len(premium_routes),
                "accepted_route_count": premium_summary["accepted_route_count"],
                "pending_route_count": premium_summary["pending_route_count"],
                "accepted_reported_total": premium_summary[
                    "accepted_reported_total"
                ],
                "authoritative_canonical_directory": (
                    premium_contract.AUTHORITATIVE_DIRECTORY
                ),
                "legacy_premium_directory_policy": "preservation_only_not_authoritative",
            },
        ],
        "premium_thread_census": premium_summary["premium_thread_census"],
        "student_breakdowns_reconciliation": student_reconciliation,
        "questions_identity": questions_identity,
        "questions_acceptance_policy": {
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
        },
        "premium_journals_acceptance_policy": {
            "discovery": "exact_scheduled_canonical_path_in_dedicated_v2_5_root",
            "authoritative_canonical_directory": (
                premium_contract.AUTHORITATIVE_DIRECTORY
            ),
            "legacy_premium_directory": (
                premium_contract.LEGACY_PRESERVATION_DIRECTORY
            ),
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
        },
        "routes": {
            "student_breakdowns": student_routes,
            "questions": question_routes,
            "premium_journals": premium_routes,
        },
        "preservation_only_artifacts": preserved,
        "coverage_assertions": {
            "student_breakdowns": {
                "route_count": 15,
                "span_days": 14,
                "calendar_day_count": EXPECTED_STUDENT_CALENDAR_DAY_COUNT,
                "exact_nonoverlapping_cover": True,
                "accepted_route_count": EXPECTED_STUDENT_SHARD_COUNT,
                "pending_route_count": 0,
                "accepted_reported_total": EXPECTED_STUDENT_REPORTED_TOTAL,
                "full_window_reconciled": True,
            },
            "questions": {
                "route_count": 33,
                "calendar_day_count": 201,
                "legacy_daily_route_count": 5,
                "legacy_daily_span_days": 1,
                "weekly_route_count": 28,
                "weekly_span_days": 7,
                "weekly_start": "2026-01-06",
                "weekly_end": "2026-07-20",
                "exact_nonoverlapping_cover": True,
                "accepted_route_count": len(accepted_questions),
                "pending_route_count": len(question_routes) - len(accepted_questions),
                "accepted_reported_total": accepted_question_total,
            },
            "premium_journals": {
                "route_count": 201,
                "span_days": 1,
                "calendar_day_count": 201,
                "exact_nonoverlapping_cover": True,
                "exact_forum_navigation_required_for_every_route": True,
                "accepted_route_count": premium_summary["accepted_route_count"],
                "pending_route_count": premium_summary["pending_route_count"],
                "accepted_reported_total": premium_summary[
                    "accepted_reported_total"
                ],
                "observed_message_bearing_child_thread_count": (
                    premium_summary["premium_thread_census"][
                        "observed_message_bearing_child_thread_count"
                    ]
                ),
                "message_data_scope_closure_proven": premium_summary[
                    "premium_thread_census"
                ]["closure_proven"],
            },
        },
    }


def main() -> None:
    schedule = build()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(schedule, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
