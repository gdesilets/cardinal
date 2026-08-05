from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TOP_LEVEL_INVENTORY = SCRIPT_DIR / "raw" / "post_cutoff_top_level_inventory.json"
DEFAULT_FORUM_INVENTORY = SCRIPT_DIR / "raw" / "forum_thread_inventory.json"
DEFAULT_ORDINARY_THREAD_INVENTORY = SCRIPT_DIR / "raw" / "ordinary_thread_inventory.json"
DEFAULT_OUTPUT = SCRIPT_DIR / "working" / "full_server_channel_inventory_complete.json"

GUILD_ID = "1167376964680691732"
PREMIUM_JOURNALS_ID = "1283941772577472643"
EXPECTED_TOP_LEVEL_COUNT = 38
DISCORD_EPOCH_MS = 1420070400000

SNOWFLAKE_RE = re.compile(r"^[0-9]{17,20}$")
FORUM_CARD_RE = re.compile(
    r"^forum-channel-list-(?P<parent>[0-9]{17,20})___(?P<thread>[0-9]{17,20})$"
)
DISCORD_CHANNEL_URL_RE = re.compile(
    r"^(?:https://(?:(?:canary|ptb)\.)?discord\.com)?"
    r"/channels/(?P<guild>[0-9]{17,20})/(?P<channel>[0-9]{17,20})"
    r"(?:/(?P<message>[0-9]{17,20}))?/?$"
)

PASS_METHODS = {
    "active": {
        "authenticated_discord_forum_card_enumeration",
        "authenticated_discord_active_thread_enumeration",
    },
    "discoverable_archived": {
        "authenticated_discord_archived_thread_enumeration",
        "authenticated_discord_forum_archive_enumeration",
    },
}
EXACT_IDENTITY_METHODS = {
    "forum_card_data_list_item_id",
    "authenticated_discord_thread_url",
}
ORDINARY_EXACT_IDENTITY_METHODS = {"authenticated_discord_thread_url"}
ORDINARY_PASS_METHODS = {
    "active": {
        "authenticated_discord_active_thread_enumeration",
        "authenticated_discord_public_thread_navigation_enumeration",
    },
    "discoverable_archived": {
        "authenticated_discord_archived_thread_enumeration",
        "authenticated_discord_public_thread_archive_enumeration",
    },
}
MESSAGE_EVIDENCE_METHOD = "authenticated_discord_message_permalink"


class InventoryValidationError(ValueError):
    def __init__(self, issues: Iterable[str]):
        self.issues = tuple(dict.fromkeys(str(issue) for issue in issues))
        super().__init__("; ".join(self.issues))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json_object(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InventoryValidationError([f"{label}_unreadable:{exc}"]) from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryValidationError([f"{label}_invalid_json:{exc}"]) from exc
    if not isinstance(value, dict):
        raise InventoryValidationError([f"{label}_root_not_object"])
    return value, raw


def parse_timestamp(value: Any, field: str, issues: list[str]) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        issues.append(f"{field}_missing")
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        issues.append(f"{field}_invalid")
        return None
    if parsed.tzinfo is None:
        issues.append(f"{field}_timezone_missing")
        return None
    return parsed.astimezone(dt.timezone.utc)


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def exact_snowflake(value: Any) -> str | None:
    text = str(value or "").strip()
    if not SNOWFLAKE_RE.fullmatch(text):
        return None
    try:
        timestamp_ms = (int(text) >> 22) + DISCORD_EPOCH_MS
        dt.datetime.fromtimestamp(timestamp_ms / 1000, tz=dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return text


def snowflake_timestamp(value: str) -> dt.datetime:
    timestamp_ms = (int(value) >> 22) + DISCORD_EPOCH_MS
    return dt.datetime.fromtimestamp(timestamp_ms / 1000, tz=dt.timezone.utc)


def validate_source_ref(value: Any, field: str, issues: list[str]) -> str | None:
    text = str(value or "").strip()
    if not text:
        issues.append(f"{field}_missing")
        return None
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        match = DISCORD_CHANNEL_URL_RE.fullmatch(text)
        if not match or match.group("guild") != GUILD_ID:
            issues.append(f"{field}_outside_discord")
            return None
    return text


def require_discord_only(
    value: dict[str, Any], field: str, issues: list[str], *, authenticated: bool = False
) -> None:
    if value.get("source_scope") != "discord_only":
        issues.append(f"{field}_source_scope_not_discord_only")
    if value.get("outside_sources_used") is not False:
        issues.append(f"{field}_outside_sources_not_explicitly_false")
    if authenticated and value.get("authenticated") is not True:
        issues.append(f"{field}_authenticated_not_true")


def validate_post_cutoff_top_level(
    payload: dict[str, Any], raw: bytes
) -> tuple[list[dict[str, Any]], dt.datetime, dt.datetime]:
    issues: list[str] = []
    if str(payload.get("guild_id") or "") != GUILD_ID:
        issues.append("post_cutoff_top_level_inventory_wrong_guild")
    require_discord_only(payload, "post_cutoff_top_level_inventory", issues)
    if payload.get("inventory_complete") is not True:
        issues.append("post_cutoff_top_level_inventory_not_declared_complete")
    if payload.get("status") != "complete":
        issues.append("post_cutoff_top_level_inventory_status_not_complete")

    window = payload.get("requested_local_window")
    if not isinstance(window, dict):
        issues.append("post_cutoff_requested_local_window_missing")
        window = {}
    if window.get("timezone") != "America/Chicago":
        issues.append("post_cutoff_requested_local_window_wrong_timezone")
    window_start = parse_timestamp(
        window.get("start_inclusive"), "post_cutoff_window_start_inclusive", issues
    )
    cutoff = parse_timestamp(
        window.get("end_exclusive"), "post_cutoff_window_end_exclusive", issues
    )
    if window_start and cutoff and window_start >= cutoff:
        issues.append("post_cutoff_requested_local_window_not_increasing")

    capture_as_of = parse_timestamp(
        payload.get("capture_as_of_utc"),
        "post_cutoff_top_level_capture_as_of_utc",
        issues,
    )
    if capture_as_of and cutoff and capture_as_of < cutoff:
        issues.append("post_cutoff_top_level_capture_before_data_cutoff")

    channels = payload.get("channels")
    if not isinstance(channels, list):
        issues.append("post_cutoff_top_level_channels_not_array")
        channels = []
    if len(channels) != EXPECTED_TOP_LEVEL_COUNT:
        issues.append(
            "post_cutoff_top_level_channel_count_mismatch:"
            f"expected={EXPECTED_TOP_LEVEL_COUNT},actual={len(channels)}"
        )
    seen: set[str] = set()
    forum_rows: list[dict[str, Any]] = []
    for index, row in enumerate(channels):
        if not isinstance(row, dict):
            issues.append(f"post_cutoff_channel_{index}_not_object")
            continue
        channel_id = exact_snowflake(row.get("channel_id"))
        if not channel_id:
            issues.append(f"post_cutoff_channel_{index}_missing_exact_snowflake")
            continue
        if channel_id in seen:
            issues.append(f"post_cutoff_duplicate_channel_id:{channel_id}")
        seen.add(channel_id)
        if row.get("exact_id_known") is not True:
            issues.append(f"post_cutoff_channel_{channel_id}_exact_id_not_known")
        if "forum" in str(row.get("kind") or "").lower():
            forum_rows.append(row)
    if len(forum_rows) != 1:
        issues.append(f"post_cutoff_forum_parent_count_not_one:{len(forum_rows)}")
    elif str(forum_rows[0].get("channel_id") or "") != PREMIUM_JOURNALS_ID:
        issues.append("post_cutoff_forum_parent_not_premium_journals")

    top_scope = (
        payload.get("accessible_scope", {}).get("top_level_containers", {})
        if isinstance(payload.get("accessible_scope"), dict)
        else {}
    )
    if top_scope.get("declared_complete") is not True:
        issues.append("post_cutoff_top_level_scope_not_declared_complete")
    if int(top_scope.get("expected_count") or -1) != EXPECTED_TOP_LEVEL_COUNT:
        issues.append("post_cutoff_top_level_scope_expected_count_mismatch")

    resnapshot_scope = (
        payload.get("accessible_scope", {}).get("post_cutoff_navigation_resnapshot", {})
        if isinstance(payload.get("accessible_scope"), dict)
        else {}
    )
    if resnapshot_scope.get("declared_complete") is not True:
        issues.append("post_cutoff_navigation_resnapshot_not_declared_complete")
    if resnapshot_scope.get("status") != "complete":
        issues.append("post_cutoff_navigation_resnapshot_status_not_complete")
    completion = resnapshot_scope.get("completion_evidence")
    if not isinstance(completion, dict):
        issues.append("post_cutoff_navigation_completion_evidence_missing")
        completion = {}
    require_discord_only(
        completion,
        "post_cutoff_navigation_completion_evidence",
        issues,
        authenticated=True,
    )
    if completion.get("navigation_pass_complete") is not True:
        issues.append("post_cutoff_navigation_pass_not_complete")
    if completion.get("terminal_state_observed") is not True:
        issues.append("post_cutoff_navigation_terminal_state_not_observed")
    completion_at = parse_timestamp(
        completion.get("capture_completed_at_utc"),
        "post_cutoff_navigation_capture_completed_at_utc",
        issues,
    )
    if completion_at and cutoff and completion_at < cutoff:
        issues.append("post_cutoff_navigation_completed_before_data_cutoff")
    if completion_at and capture_as_of and completion_at != capture_as_of:
        issues.append("post_cutoff_navigation_capture_timestamp_mismatch")
    source_refs = completion.get("source_refs")
    if not isinstance(source_refs, list) or not source_refs:
        issues.append("post_cutoff_navigation_source_refs_missing")
    else:
        for index, item in enumerate(source_refs):
            validate_source_ref(
                item, f"post_cutoff_navigation_source_ref_{index}", issues
            )

    if issues:
        raise InventoryValidationError(issues)
    assert window_start is not None and cutoff is not None and capture_as_of is not None
    return channels, window_start, cutoff


def validate_enumeration_pass(
    pass_name: str,
    value: Any,
    cutoff: dt.datetime,
    issues: list[str],
) -> dict[str, Any] | None:
    field = f"enumeration_pass_{pass_name}"
    if not isinstance(value, dict):
        issues.append(f"{field}_missing_or_not_object")
        return None
    require_discord_only(value, field, issues, authenticated=True)
    if str(value.get("parent_forum_channel_id") or "") != PREMIUM_JOURNALS_ID:
        issues.append(f"{field}_wrong_parent")
    method = str(value.get("method") or "")
    if method not in PASS_METHODS[pass_name]:
        issues.append(f"{field}_method_not_allowed:{method or 'missing'}")
    if value.get("status") != "complete":
        issues.append(f"{field}_status_not_complete")
    if value.get("pagination_complete") is not True:
        issues.append(f"{field}_pagination_not_complete")
    if value.get("terminal_state_observed") is not True:
        issues.append(f"{field}_terminal_state_not_observed")
    if value.get("remaining_cursor") not in (None, ""):
        issues.append(f"{field}_remaining_cursor_not_empty")

    started = parse_timestamp(value.get("started_at_utc"), f"{field}_started_at_utc", issues)
    completed = parse_timestamp(
        value.get("completed_at_utc"), f"{field}_completed_at_utc", issues
    )
    if started and started < cutoff:
        issues.append(f"{field}_started_before_data_cutoff")
    if completed and completed < cutoff:
        issues.append(f"{field}_completed_before_data_cutoff")
    if started and completed and completed < started:
        issues.append(f"{field}_completed_before_started")

    source_refs = value.get("source_refs")
    if not isinstance(source_refs, list) or not source_refs:
        issues.append(f"{field}_source_refs_missing")
        source_refs = []
    normalized_refs = [
        ref
        for index, item in enumerate(source_refs)
        if (ref := validate_source_ref(item, f"{field}_source_ref_{index}", issues))
    ]

    raw_ids = value.get("thread_ids")
    if not isinstance(raw_ids, list):
        issues.append(f"{field}_thread_ids_not_array")
        raw_ids = []
    thread_ids: list[str] = []
    seen: set[str] = set()
    for index, raw_id in enumerate(raw_ids):
        thread_id = exact_snowflake(raw_id)
        if not thread_id:
            issues.append(f"{field}_thread_id_{index}_not_exact_snowflake")
            continue
        if thread_id in seen:
            issues.append(f"{field}_duplicate_thread_id:{thread_id}")
            continue
        seen.add(thread_id)
        thread_ids.append(thread_id)
    reported_count = value.get("reported_thread_count")
    if isinstance(reported_count, bool) or not isinstance(reported_count, int):
        issues.append(f"{field}_reported_thread_count_not_integer")
    elif reported_count != len(thread_ids):
        issues.append(
            f"{field}_reported_thread_count_mismatch:"
            f"reported={reported_count},represented={len(thread_ids)}"
        )

    return {
        "pass_name": pass_name,
        "method": method,
        "started_at_utc": iso_utc(started) if started else None,
        "completed_at_utc": iso_utc(completed) if completed else None,
        "source_refs": normalized_refs,
        "thread_ids": thread_ids,
        "reported_thread_count": reported_count,
        "pagination_complete": value.get("pagination_complete") is True,
        "terminal_state_observed": value.get("terminal_state_observed") is True,
    }


def validate_identity_evidence(
    value: Any,
    *,
    thread_id: str,
    expected_pass: str,
    pass_summary: dict[str, Any],
    capture_completed: dt.datetime,
    field: str,
    issues: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        issues.append(f"{field}_missing_or_empty")
        return []
    accepted: list[dict[str, Any]] = []
    pass_started = parse_timestamp(
        pass_summary.get("started_at_utc"), f"{field}_pass_started_at", issues
    )
    pass_completed = parse_timestamp(
        pass_summary.get("completed_at_utc"), f"{field}_pass_completed_at", issues
    )
    for index, row in enumerate(value):
        item_field = f"{field}_{index}"
        if not isinstance(row, dict):
            issues.append(f"{item_field}_not_object")
            continue
        method = str(row.get("method") or "")
        lowered_method = method.lower()
        if "attachment" in lowered_method or "cdn" in lowered_method:
            issues.append(f"{item_field}_attachment_cdn_identity_forbidden")
            continue
        if method not in EXACT_IDENTITY_METHODS:
            issues.append(f"{item_field}_method_not_exact_row_owned_evidence:{method or 'missing'}")
            continue
        require_discord_only(row, item_field, issues, authenticated=True)
        if row.get("enumeration_pass") != expected_pass:
            issues.append(f"{item_field}_wrong_enumeration_pass")
        observed = parse_timestamp(
            row.get("observed_at_utc"), f"{item_field}_observed_at_utc", issues
        )
        if observed and pass_started and observed < pass_started:
            issues.append(f"{item_field}_observed_before_pass")
        if observed and pass_completed and observed > pass_completed:
            issues.append(f"{item_field}_observed_after_pass")
        if observed and observed > capture_completed:
            issues.append(f"{item_field}_observed_after_capture_completed")
        source_ref = validate_source_ref(row.get("source_ref"), f"{item_field}_source_ref", issues)

        normalized: dict[str, Any] = {
            "method": method,
            "enumeration_pass": expected_pass,
            "observed_at_utc": iso_utc(observed) if observed else None,
            "source_ref": source_ref,
            "authenticated": True,
            "source_scope": "discord_only",
            "outside_sources_used": False,
        }
        if method == "forum_card_data_list_item_id":
            card_id = str(row.get("forum_card_data_list_item_id") or "")
            match = FORUM_CARD_RE.fullmatch(card_id)
            if not match:
                issues.append(f"{item_field}_invalid_forum_card_data_list_item_id")
            elif match.group("parent") != PREMIUM_JOURNALS_ID:
                issues.append(f"{item_field}_forum_card_wrong_parent")
            elif match.group("thread") != thread_id:
                issues.append(f"{item_field}_forum_card_wrong_thread")
            normalized["forum_card_data_list_item_id"] = card_id
        else:
            thread_url = str(row.get("thread_url") or "")
            match = DISCORD_CHANNEL_URL_RE.fullmatch(thread_url)
            if not match:
                issues.append(f"{item_field}_invalid_authenticated_thread_url")
            elif match.group("guild") != GUILD_ID:
                issues.append(f"{item_field}_thread_url_wrong_guild")
            elif match.group("channel") != thread_id:
                issues.append(f"{item_field}_thread_url_wrong_thread")
            elif match.group("message"):
                issues.append(f"{item_field}_thread_url_must_not_be_message_permalink")
            normalized["thread_url"] = thread_url
        accepted.append(normalized)
    if not accepted:
        issues.append(f"{field}_has_no_accepted_exact_row_owned_evidence")
    return accepted


def validate_message_evidence(
    value: Any,
    *,
    role: str,
    thread_id: str,
    cutoff: dt.datetime,
    capture_completed: dt.datetime,
    field: str,
    issues: list[str],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        issues.append(f"{field}_missing_or_not_object")
        return None
    method = str(value.get("method") or "")
    lowered_method = method.lower()
    if "attachment" in lowered_method or "cdn" in lowered_method:
        issues.append(f"{field}_attachment_cdn_evidence_forbidden")
    if method != MESSAGE_EVIDENCE_METHOD:
        issues.append(f"{field}_method_not_authenticated_discord_message_permalink")
    require_discord_only(value, field, issues, authenticated=True)
    if value.get("role") != role:
        issues.append(f"{field}_wrong_role")
    if value.get("position_verified") is not True:
        issues.append(f"{field}_position_not_verified")
    if role == "last_message_at_or_before_cutoff" and value.get("cutoff_bounded") is not True:
        issues.append(f"{field}_cutoff_bounded_not_true")

    message_id = exact_snowflake(value.get("message_id"))
    if not message_id:
        issues.append(f"{field}_message_id_not_exact_snowflake")
    permalink = str(value.get("permalink") or "")
    match = DISCORD_CHANNEL_URL_RE.fullmatch(permalink)
    if not match or not match.group("message"):
        issues.append(f"{field}_invalid_message_permalink")
    else:
        if match.group("guild") != GUILD_ID:
            issues.append(f"{field}_permalink_wrong_guild")
        if match.group("channel") != thread_id:
            issues.append(f"{field}_permalink_wrong_thread")
        if message_id and match.group("message") != message_id:
            issues.append(f"{field}_permalink_wrong_message")
    observed = parse_timestamp(
        value.get("observed_at_utc"), f"{field}_observed_at_utc", issues
    )
    if observed and observed > capture_completed:
        issues.append(f"{field}_observed_after_capture_completed")
    if message_id and snowflake_timestamp(message_id) >= cutoff:
        issues.append(f"{field}_message_not_before_cutoff")
    source_ref = validate_source_ref(value.get("source_ref"), f"{field}_source_ref", issues)
    return {
        "role": role,
        "method": method,
        "message_id": message_id,
        "permalink": permalink,
        "position_verified": value.get("position_verified") is True,
        "cutoff_bounded": value.get("cutoff_bounded") is True
        if role == "last_message_at_or_before_cutoff"
        else None,
        "observed_at_utc": iso_utc(observed) if observed else None,
        "source_ref": source_ref,
        "authenticated": True,
        "source_scope": "discord_only",
        "outside_sources_used": False,
    }


def validate_forum_inventory(
    payload: dict[str, Any],
    *,
    frozen_payload: dict[str, Any],
    window_start: dt.datetime,
    cutoff: dt.datetime,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dt.datetime]:
    issues: list[str] = []
    if payload.get("schema_version") != "1.0":
        issues.append("forum_inventory_schema_version_not_1.0")
    if str(payload.get("guild_id") or "") != GUILD_ID:
        issues.append("forum_inventory_wrong_guild")
    if str(payload.get("parent_forum_channel_id") or "") != PREMIUM_JOURNALS_ID:
        issues.append("forum_inventory_wrong_parent")
    require_discord_only(payload, "forum_inventory", issues)
    if payload.get("inventory_complete") is not True:
        issues.append("forum_inventory_not_declared_complete")
    if payload.get("status") != "complete":
        issues.append("forum_inventory_status_not_complete")

    frozen_window = frozen_payload.get("requested_local_window")
    raw_window = payload.get("requested_local_window")
    if not isinstance(raw_window, dict) or raw_window != frozen_window:
        issues.append("forum_inventory_requested_local_window_mismatch")
    raw_cutoff = parse_timestamp(payload.get("data_cutoff_utc"), "forum_data_cutoff_utc", issues)
    if raw_cutoff and raw_cutoff != cutoff:
        issues.append("forum_inventory_data_cutoff_mismatch")
    capture_completed = parse_timestamp(
        payload.get("capture_completed_at_utc"),
        "forum_capture_completed_at_utc",
        issues,
    )
    if capture_completed and capture_completed < cutoff:
        issues.append("forum_capture_completed_before_data_cutoff")

    pass_values = payload.get("enumeration_passes")
    if not isinstance(pass_values, dict):
        issues.append("forum_enumeration_passes_missing_or_not_object")
        pass_values = {}
    passes: dict[str, dict[str, Any]] = {}
    for pass_name in ("active", "discoverable_archived"):
        summary = validate_enumeration_pass(
            pass_name, pass_values.get(pass_name), cutoff, issues
        )
        if summary:
            passes[pass_name] = summary
    if capture_completed:
        for pass_name, summary in passes.items():
            completed = parse_timestamp(
                summary.get("completed_at_utc"),
                f"enumeration_pass_{pass_name}_normalized_completed_at_utc",
                issues,
            )
            if completed and completed > capture_completed:
                issues.append(f"enumeration_pass_{pass_name}_completed_after_capture")

    raw_threads = payload.get("threads")
    if not isinstance(raw_threads, list):
        issues.append("forum_threads_not_array")
        raw_threads = []
    normalized_threads: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    active_ids = set(passes.get("active", {}).get("thread_ids", []))
    archived_ids = set(passes.get("discoverable_archived", {}).get("thread_ids", []))
    overlap = active_ids & archived_ids
    if overlap:
        issues.append("forum_thread_ids_in_both_active_and_archived_passes:" + ",".join(sorted(overlap)))

    for index, row in enumerate(raw_threads):
        field = f"forum_thread_{index}"
        if not isinstance(row, dict):
            issues.append(f"{field}_not_object")
            continue
        thread_id = exact_snowflake(row.get("thread_id"))
        if not thread_id:
            issues.append(f"{field}_thread_id_not_exact_snowflake")
            continue
        if thread_id in seen_ids:
            issues.append(f"forum_duplicate_thread_id:{thread_id}")
            continue
        seen_ids.add(thread_id)
        if thread_id == PREMIUM_JOURNALS_ID:
            issues.append(f"{field}_thread_id_equals_parent")
        if snowflake_timestamp(thread_id) >= cutoff:
            issues.append(f"{field}_thread_created_at_or_after_cutoff")
        if str(row.get("parent_forum_channel_id") or "") != PREMIUM_JOURNALS_ID:
            issues.append(f"{field}_wrong_parent")
        title = str(row.get("title") or "").strip()
        if not title:
            issues.append(f"{field}_title_missing")
        archived = row.get("archived")
        if not isinstance(archived, bool):
            issues.append(f"{field}_archived_not_boolean")
            archived = False
        expected_pass = "discoverable_archived" if archived else "active"
        expected_set = archived_ids if archived else active_ids
        if thread_id not in expected_set:
            issues.append(f"{field}_missing_from_{expected_pass}_pass")
        if not capture_completed or expected_pass not in passes:
            identity = []
        else:
            identity = validate_identity_evidence(
                row.get("identity_evidence"),
                thread_id=thread_id,
                expected_pass=expected_pass,
                pass_summary=passes[expected_pass],
                capture_completed=capture_completed,
                field=f"{field}_identity_evidence",
                issues=issues,
            )

        evidence: dict[str, dict[str, Any] | None] = {}
        roles = {
            "starter_message_evidence": "thread_starter",
            "first_message_evidence": "first_message",
            "last_message_evidence": "last_message_at_or_before_cutoff",
        }
        for key, role in roles.items():
            evidence[key] = (
                validate_message_evidence(
                    row.get(key),
                    role=role,
                    thread_id=thread_id,
                    cutoff=cutoff,
                    capture_completed=capture_completed,
                    field=f"{field}_{key}",
                    issues=issues,
                )
                if capture_completed
                else None
            )
        starter_id = (evidence["starter_message_evidence"] or {}).get("message_id")
        first_id = (evidence["first_message_evidence"] or {}).get("message_id")
        last_id = (evidence["last_message_evidence"] or {}).get("message_id")
        if starter_id and first_id and snowflake_timestamp(starter_id) > snowflake_timestamp(first_id):
            issues.append(f"{field}_starter_after_first")
        if first_id and last_id and snowflake_timestamp(first_id) > snowflake_timestamp(last_id):
            issues.append(f"{field}_first_after_last")
        if first_id and snowflake_timestamp(first_id) < window_start:
            window_relation = "thread_begins_before_window"
        else:
            window_relation = "thread_begins_in_window"
        if last_id and snowflake_timestamp(last_id) < window_start:
            window_relation = "no_messages_in_requested_window"

        methods = sorted({item["method"] for item in identity})
        normalized_threads.append(
            {
                "container_id": thread_id,
                "thread_id": thread_id,
                "name": title,
                "title": title,
                "kind": "forum thread",
                "parent_container_id": PREMIUM_JOURNALS_ID,
                "parent_forum_channel_id": PREMIUM_JOURNALS_ID,
                "inventory_layer": "observed_forum_thread",
                "exact_id_known": True,
                "message_bearing": True,
                "accessible": True,
                "searchable": True,
                "accessible_scope_status": (
                    "enumerated_in_authenticated_discord_active_pass"
                    if not archived
                    else "enumerated_in_authenticated_discord_discoverable_archive_pass"
                ),
                "archived": archived,
                "locked": row.get("locked") if isinstance(row.get("locked"), bool) else None,
                "tags": row.get("tags") if isinstance(row.get("tags"), list) else [],
                "coverage_container_id": PREMIUM_JOURNALS_ID,
                "coverage_start_date": str(
                    frozen_payload["requested_local_window"]["start_inclusive"]
                )[:10],
                "coverage_end_date": (
                    cutoff.astimezone(dt.timezone(dt.timedelta(hours=-5))).date()
                    - dt.timedelta(days=1)
                ).isoformat(),
                "count_status": "complete_parent_forum_enumeration",
                "channel_created_at_utc": iso_utc(snowflake_timestamp(thread_id)),
                "window_relation": window_relation,
                "identity_provenance": {
                    "method": "+".join(methods),
                    "exact_row_owned_evidence": True,
                    "attachment_cdn_used_as_exact_identity": False,
                    "enumeration_pass": expected_pass,
                    "evidence": identity,
                },
                "starter_message_evidence": evidence["starter_message_evidence"],
                "first_message_evidence": evidence["first_message_evidence"],
                "last_message_evidence": evidence["last_message_evidence"],
                "accessible_scope_evidence": {
                    "parent_forum_container_id": PREMIUM_JOURNALS_ID,
                    "enumeration_pass": expected_pass,
                    "pass_method": passes.get(expected_pass, {}).get("method"),
                    "pass_completed_at_utc": passes.get(expected_pass, {}).get(
                        "completed_at_utc"
                    ),
                    "source_refs": passes.get(expected_pass, {}).get("source_refs", []),
                    "archive_enumeration_complete": True,
                },
            }
        )

    represented_ids = set(seen_ids)
    enumerated_ids = active_ids | archived_ids
    missing_rows = enumerated_ids - represented_ids
    extra_rows = represented_ids - enumerated_ids
    if missing_rows:
        issues.append("enumerated_thread_ids_missing_rows:" + ",".join(sorted(missing_rows)))
    if extra_rows:
        issues.append("thread_rows_missing_enumeration_membership:" + ",".join(sorted(extra_rows)))
    if len(normalized_threads) != len(raw_threads):
        issues.append(
            "forum_thread_normalization_count_mismatch:"
            f"raw={len(raw_threads)},normalized={len(normalized_threads)}"
        )

    if issues:
        raise InventoryValidationError(issues)
    assert capture_completed is not None
    return normalized_threads, passes, capture_completed


def validate_ordinary_thread_inventory(
    payload: dict[str, Any],
    *,
    top_payload: dict[str, Any],
    top_level_ids: set[str],
    window_start: dt.datetime,
    cutoff: dt.datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dt.datetime]:
    """Validate a fail-closed ordinary-thread census for every top-level parent.

    An audit row is required even when a parent cannot host ordinary threads.  This
    prevents a missing browser pass from being silently reinterpreted as zero threads.
    """

    issues: list[str] = []
    if payload.get("schema_version") != "1.0":
        issues.append("ordinary_thread_inventory_schema_version_not_1.0")
    if str(payload.get("guild_id") or "") != GUILD_ID:
        issues.append("ordinary_thread_inventory_wrong_guild")
    require_discord_only(payload, "ordinary_thread_inventory", issues)
    if payload.get("inventory_complete") is not True:
        issues.append("ordinary_thread_inventory_not_declared_complete")
    if payload.get("status") != "complete":
        issues.append("ordinary_thread_inventory_status_not_complete")
    if payload.get("requested_local_window") != top_payload.get("requested_local_window"):
        issues.append("ordinary_thread_inventory_requested_local_window_mismatch")
    raw_cutoff = parse_timestamp(
        payload.get("data_cutoff_utc"), "ordinary_thread_data_cutoff_utc", issues
    )
    if raw_cutoff and raw_cutoff != cutoff:
        issues.append("ordinary_thread_inventory_data_cutoff_mismatch")
    capture_completed = parse_timestamp(
        payload.get("capture_completed_at_utc"),
        "ordinary_thread_capture_completed_at_utc",
        issues,
    )
    if capture_completed and capture_completed < cutoff:
        issues.append("ordinary_thread_capture_completed_before_data_cutoff")

    raw_audits = payload.get("parent_audits")
    if not isinstance(raw_audits, list):
        issues.append("ordinary_thread_parent_audits_not_array")
        raw_audits = []
    audit_by_parent: dict[str, dict[str, Any]] = {}
    enumerated_membership: dict[str, tuple[str, str]] = {}
    normalized_audits: list[dict[str, Any]] = []
    for index, audit in enumerate(raw_audits):
        field = f"ordinary_thread_parent_audit_{index}"
        if not isinstance(audit, dict):
            issues.append(f"{field}_not_object")
            continue
        parent_id = exact_snowflake(audit.get("parent_channel_id"))
        if not parent_id:
            issues.append(f"{field}_parent_channel_id_not_exact_snowflake")
            continue
        if parent_id not in top_level_ids:
            issues.append(f"{field}_parent_not_in_top_level_inventory:{parent_id}")
        if parent_id in audit_by_parent:
            issues.append(f"ordinary_thread_duplicate_parent_audit:{parent_id}")
            continue
        audit_by_parent[parent_id] = audit
        require_discord_only(audit, field, issues, authenticated=True)
        if audit.get("status") != "complete":
            issues.append(f"{field}_status_not_complete")
        applicable = audit.get("applicable")
        if not isinstance(applicable, bool):
            issues.append(f"{field}_applicable_not_boolean")
            applicable = False
        basis = str(audit.get("applicability_basis") or "").strip()
        if not basis:
            issues.append(f"{field}_applicability_basis_missing")
        completed_at = parse_timestamp(
            audit.get("completed_at_utc"), f"{field}_completed_at_utc", issues
        )
        if completed_at and completed_at < cutoff:
            issues.append(f"{field}_completed_before_data_cutoff")
        if capture_completed and completed_at and completed_at > capture_completed:
            issues.append(f"{field}_completed_after_inventory_capture")
        refs = audit.get("source_refs")
        normalized_refs: list[str] = []
        if not isinstance(refs, list) or not refs:
            issues.append(f"{field}_source_refs_missing")
        else:
            normalized_refs = [
                ref
                for ref_index, item in enumerate(refs)
                if (
                    ref := validate_source_ref(
                        item, f"{field}_source_ref_{ref_index}", issues
                    )
                )
            ]

        raw_passes = audit.get("enumeration_passes")
        pass_summaries: dict[str, dict[str, Any]] = {}
        if applicable:
            if not isinstance(raw_passes, dict):
                issues.append(f"{field}_enumeration_passes_missing")
                raw_passes = {}
            for pass_name in ("active", "discoverable_archived"):
                pass_field = f"{field}_{pass_name}_pass"
                value = raw_passes.get(pass_name) if isinstance(raw_passes, dict) else None
                if not isinstance(value, dict):
                    issues.append(f"{pass_field}_missing_or_not_object")
                    continue
                require_discord_only(value, pass_field, issues, authenticated=True)
                if str(value.get("parent_channel_id") or "") != parent_id:
                    issues.append(f"{pass_field}_wrong_parent")
                method = str(value.get("method") or "")
                if method not in ORDINARY_PASS_METHODS[pass_name]:
                    issues.append(f"{pass_field}_method_not_allowed:{method or 'missing'}")
                if value.get("status") != "complete":
                    issues.append(f"{pass_field}_status_not_complete")
                if value.get("pagination_complete") is not True:
                    issues.append(f"{pass_field}_pagination_not_complete")
                if value.get("terminal_state_observed") is not True:
                    issues.append(f"{pass_field}_terminal_state_not_observed")
                if value.get("remaining_cursor") not in (None, ""):
                    issues.append(f"{pass_field}_remaining_cursor_not_empty")
                started = parse_timestamp(
                    value.get("started_at_utc"), f"{pass_field}_started_at_utc", issues
                )
                ended = parse_timestamp(
                    value.get("completed_at_utc"), f"{pass_field}_completed_at_utc", issues
                )
                if started and started < cutoff:
                    issues.append(f"{pass_field}_started_before_data_cutoff")
                if ended and ended < cutoff:
                    issues.append(f"{pass_field}_completed_before_data_cutoff")
                if started and ended and ended < started:
                    issues.append(f"{pass_field}_completed_before_started")
                if capture_completed and ended and ended > capture_completed:
                    issues.append(f"{pass_field}_completed_after_inventory_capture")
                pass_refs = value.get("source_refs")
                normalized_pass_refs: list[str] = []
                if not isinstance(pass_refs, list) or not pass_refs:
                    issues.append(f"{pass_field}_source_refs_missing")
                else:
                    normalized_pass_refs = [
                        ref
                        for ref_index, item in enumerate(pass_refs)
                        if (
                            ref := validate_source_ref(
                                item, f"{pass_field}_source_ref_{ref_index}", issues
                            )
                        )
                    ]
                raw_ids = value.get("thread_ids")
                if not isinstance(raw_ids, list):
                    issues.append(f"{pass_field}_thread_ids_not_array")
                    raw_ids = []
                thread_ids: list[str] = []
                local_seen: set[str] = set()
                for thread_index, item in enumerate(raw_ids):
                    thread_id = exact_snowflake(item)
                    if not thread_id:
                        issues.append(
                            f"{pass_field}_thread_id_{thread_index}_not_exact_snowflake"
                        )
                        continue
                    if thread_id in local_seen:
                        issues.append(f"{pass_field}_duplicate_thread_id:{thread_id}")
                        continue
                    local_seen.add(thread_id)
                    thread_ids.append(thread_id)
                    previous = enumerated_membership.get(thread_id)
                    membership = (parent_id, pass_name)
                    if previous and previous != membership:
                        issues.append(
                            "ordinary_thread_enumerated_in_multiple_parent_or_pass_locations:"
                            f"{thread_id}"
                        )
                    enumerated_membership[thread_id] = membership
                reported = value.get("reported_thread_count")
                if isinstance(reported, bool) or not isinstance(reported, int):
                    issues.append(f"{pass_field}_reported_thread_count_not_integer")
                elif reported != len(thread_ids):
                    issues.append(f"{pass_field}_reported_thread_count_mismatch")
                pass_summaries[pass_name] = {
                    "method": method,
                    "started_at_utc": iso_utc(started) if started else None,
                    "completed_at_utc": iso_utc(ended) if ended else None,
                    "source_refs": normalized_pass_refs,
                    "thread_ids": thread_ids,
                    "reported_thread_count": reported,
                }
        elif raw_passes not in (None, {}):
            issues.append(f"{field}_non_applicable_parent_has_enumeration_passes")
        if parent_id == PREMIUM_JOURNALS_ID and applicable:
            issues.append("ordinary_thread_forum_parent_must_be_non_applicable")

        normalized_audits.append(
            {
                "parent_channel_id": parent_id,
                "applicable": applicable,
                "applicability_basis": basis,
                "status": audit.get("status"),
                "completed_at_utc": iso_utc(completed_at) if completed_at else None,
                "source_refs": normalized_refs,
                "enumeration_passes": pass_summaries,
            }
        )

    missing_parent_audits = top_level_ids - set(audit_by_parent)
    extra_parent_audits = set(audit_by_parent) - top_level_ids
    if missing_parent_audits:
        issues.append(
            "ordinary_thread_missing_parent_audits:" + ",".join(sorted(missing_parent_audits))
        )
    if extra_parent_audits:
        issues.append(
            "ordinary_thread_extra_parent_audits:" + ",".join(sorted(extra_parent_audits))
        )
    if len(raw_audits) != EXPECTED_TOP_LEVEL_COUNT:
        issues.append(
            "ordinary_thread_parent_audit_count_mismatch:"
            f"expected={EXPECTED_TOP_LEVEL_COUNT},actual={len(raw_audits)}"
        )

    raw_threads = payload.get("threads")
    if not isinstance(raw_threads, list):
        issues.append("ordinary_threads_not_array")
        raw_threads = []
    normalized_threads: list[dict[str, Any]] = []
    seen_threads: set[str] = set()
    for index, row in enumerate(raw_threads):
        field = f"ordinary_thread_{index}"
        if not isinstance(row, dict):
            issues.append(f"{field}_not_object")
            continue
        thread_id = exact_snowflake(row.get("thread_id"))
        parent_id = exact_snowflake(row.get("parent_channel_id"))
        if not thread_id:
            issues.append(f"{field}_thread_id_not_exact_snowflake")
            continue
        if thread_id in seen_threads:
            issues.append(f"ordinary_thread_duplicate_thread_id:{thread_id}")
            continue
        seen_threads.add(thread_id)
        if not parent_id or parent_id not in top_level_ids:
            issues.append(f"{field}_parent_not_in_top_level_inventory")
        archived = row.get("archived")
        if not isinstance(archived, bool):
            issues.append(f"{field}_archived_not_boolean")
            archived = False
        pass_name = "discoverable_archived" if archived else "active"
        if enumerated_membership.get(thread_id) != (parent_id, pass_name):
            issues.append(f"{field}_missing_exact_parent_pass_membership")
        title = str(row.get("title") or "").strip()
        if not title:
            issues.append(f"{field}_title_missing")
        thread_type = str(row.get("thread_type") or "").strip()
        if thread_type not in {"public_thread", "private_thread", "announcement_thread"}:
            issues.append(f"{field}_thread_type_not_allowed")
        if snowflake_timestamp(thread_id) >= cutoff:
            issues.append(f"{field}_thread_created_at_or_after_cutoff")

        raw_identity = row.get("identity_evidence")
        identity: list[dict[str, Any]] = []
        if not isinstance(raw_identity, list) or not raw_identity:
            issues.append(f"{field}_identity_evidence_missing_or_empty")
            raw_identity = []
        for evidence_index, evidence in enumerate(raw_identity):
            evidence_field = f"{field}_identity_evidence_{evidence_index}"
            if not isinstance(evidence, dict):
                issues.append(f"{evidence_field}_not_object")
                continue
            method = str(evidence.get("method") or "")
            if "attachment" in method.lower() or "cdn" in method.lower():
                issues.append(f"{evidence_field}_attachment_cdn_identity_forbidden")
                continue
            if method not in ORDINARY_EXACT_IDENTITY_METHODS:
                issues.append(f"{evidence_field}_method_not_exact_row_owned_evidence")
                continue
            require_discord_only(evidence, evidence_field, issues, authenticated=True)
            if evidence.get("enumeration_pass") != pass_name:
                issues.append(f"{evidence_field}_wrong_enumeration_pass")
            thread_url = str(evidence.get("thread_url") or "")
            match = DISCORD_CHANNEL_URL_RE.fullmatch(thread_url)
            if (
                not match
                or match.group("guild") != GUILD_ID
                or match.group("channel") != thread_id
                or match.group("message")
            ):
                issues.append(f"{evidence_field}_invalid_authenticated_thread_url")
            observed = parse_timestamp(
                evidence.get("observed_at_utc"),
                f"{evidence_field}_observed_at_utc",
                issues,
            )
            if observed and observed < cutoff:
                issues.append(f"{evidence_field}_observed_before_data_cutoff")
            if capture_completed and observed and observed > capture_completed:
                issues.append(f"{evidence_field}_observed_after_inventory_capture")
            source_ref = validate_source_ref(
                evidence.get("source_ref"), f"{evidence_field}_source_ref", issues
            )
            identity.append(
                {
                    "method": method,
                    "thread_url": thread_url,
                    "enumeration_pass": pass_name,
                    "observed_at_utc": iso_utc(observed) if observed else None,
                    "source_ref": source_ref,
                    "authenticated": True,
                    "source_scope": "discord_only",
                    "outside_sources_used": False,
                }
            )
        if not identity:
            issues.append(f"{field}_has_no_accepted_exact_row_owned_evidence")

        if snowflake_timestamp(thread_id) < window_start:
            window_relation = "thread_begins_before_window"
        else:
            window_relation = "thread_begins_in_window"
        parent_audit = next(
            (
                item
                for item in normalized_audits
                if item.get("parent_channel_id") == parent_id
            ),
            {},
        )
        pass_summary = parent_audit.get("enumeration_passes", {}).get(pass_name, {})
        normalized_threads.append(
            {
                "container_id": thread_id,
                "thread_id": thread_id,
                "name": title,
                "title": title,
                "kind": thread_type.replace("_", " "),
                "parent_container_id": parent_id,
                "parent_channel_id": parent_id,
                "inventory_layer": "observed_ordinary_thread",
                "exact_id_known": True,
                "message_bearing": True,
                "accessible": True,
                "searchable": True,
                "accessible_scope_status": (
                    "enumerated_in_authenticated_discord_active_pass"
                    if not archived
                    else "enumerated_in_authenticated_discord_discoverable_archive_pass"
                ),
                "archived": archived,
                "locked": row.get("locked") if isinstance(row.get("locked"), bool) else None,
                "coverage_container_id": parent_id,
                "coverage_start_date": str(
                    top_payload["requested_local_window"]["start_inclusive"]
                )[:10],
                "coverage_end_date": (
                    cutoff.astimezone(dt.timezone(dt.timedelta(hours=-5))).date()
                    - dt.timedelta(days=1)
                ).isoformat(),
                "count_status": "complete_parent_thread_enumeration",
                "channel_created_at_utc": iso_utc(snowflake_timestamp(thread_id)),
                "window_relation": window_relation,
                "identity_provenance": {
                    "method": "authenticated_discord_thread_url",
                    "exact_row_owned_evidence": True,
                    "attachment_cdn_used_as_exact_identity": False,
                    "enumeration_pass": pass_name,
                    "evidence": identity,
                },
                "accessible_scope_evidence": {
                    "parent_container_id": parent_id,
                    "enumeration_pass": pass_name,
                    "pass_method": pass_summary.get("method"),
                    "pass_completed_at_utc": pass_summary.get("completed_at_utc"),
                    "source_refs": pass_summary.get("source_refs", []),
                    "parent_audit_complete": True,
                },
            }
        )

    enumerated_ids = set(enumerated_membership)
    if enumerated_ids - seen_threads:
        issues.append(
            "ordinary_enumerated_thread_ids_missing_rows:"
            + ",".join(sorted(enumerated_ids - seen_threads))
        )
    if seen_threads - enumerated_ids:
        issues.append(
            "ordinary_thread_rows_missing_enumeration_membership:"
            + ",".join(sorted(seen_threads - enumerated_ids))
        )
    reported_count = payload.get("reported_thread_count")
    if isinstance(reported_count, bool) or not isinstance(reported_count, int):
        issues.append("ordinary_thread_reported_thread_count_not_integer")
    elif reported_count != len(seen_threads):
        issues.append("ordinary_thread_reported_thread_count_mismatch")

    if issues:
        raise InventoryValidationError(issues)
    assert capture_completed is not None
    return normalized_threads, normalized_audits, capture_completed


def build_merged_inventory(
    top_level_path: Path,
    forum_path: Path,
    ordinary_thread_path: Path,
) -> dict[str, Any]:
    top_level_path = top_level_path.resolve()
    forum_path = forum_path.resolve()
    ordinary_thread_path = ordinary_thread_path.resolve()
    top_payload, top_raw = read_json_object(
        top_level_path, "post_cutoff_top_level_inventory"
    )
    forum_payload, forum_raw = read_json_object(forum_path, "forum_thread_inventory")
    ordinary_payload, ordinary_raw = read_json_object(
        ordinary_thread_path, "ordinary_thread_inventory"
    )
    channels, window_start, cutoff = validate_post_cutoff_top_level(top_payload, top_raw)
    forum_threads, passes, forum_capture_completed = validate_forum_inventory(
        forum_payload,
        frozen_payload=top_payload,
        window_start=window_start,
        cutoff=cutoff,
    )
    top_level_ids = {str(row["channel_id"]) for row in channels}
    ordinary_threads, ordinary_parent_audits, ordinary_capture_completed = (
        validate_ordinary_thread_inventory(
            ordinary_payload,
            top_payload=top_payload,
            top_level_ids=top_level_ids,
            window_start=window_start,
            cutoff=cutoff,
        )
    )
    capture_completed = max(forum_capture_completed, ordinary_capture_completed)

    top_level_containers: list[dict[str, Any]] = []
    for row in channels:
        normalized = dict(row)
        normalized["container_id"] = str(row["channel_id"])
        normalized["inventory_layer"] = "top_level_container"
        normalized["message_bearing"] = True
        normalized["accessible"] = True
        normalized["searchable"] = row.get("count_status") == "ok"
        normalized["accessible_scope_status"] = (
            "accessible_and_searchable_as_of_inventory_capture"
            if normalized["searchable"]
            else "accessible_not_searchable"
        )
        normalized["coverage_container_id"] = str(row["channel_id"])
        normalized["coverage_end_date"] = (
            cutoff.astimezone(dt.timezone(dt.timedelta(hours=-5))).date()
            - dt.timedelta(days=1)
        ).isoformat()
        normalized["identity_provenance"] = {
            "method": "post_cutoff_authenticated_discord_navigation_inventory",
            "source_sha256": sha256_bytes(top_raw),
            "observed_at_utc": top_payload.get("capture_as_of_utc"),
        }
        top_level_containers.append(normalized)

    source_inputs = [
        {
            "role": "post_cutoff_authenticated_top_level_inventory",
            "path": top_level_path.name,
            "size_bytes": len(top_raw),
            "sha256": sha256_bytes(top_raw),
        },
        {
            "role": "authenticated_forum_thread_inventory",
            "path": forum_path.name,
            "size_bytes": len(forum_raw),
            "sha256": sha256_bytes(forum_raw),
        },
        {
            "role": "authenticated_ordinary_thread_inventory",
            "path": ordinary_thread_path.name,
            "size_bytes": len(ordinary_raw),
            "sha256": sha256_bytes(ordinary_raw),
        },
    ]
    active = passes["active"]
    archived = passes["discoverable_archived"]
    completion_evidence = {
        "validator": "merge_forum_thread_inventory.py",
        "data_cutoff_utc": iso_utc(cutoff),
        "capture_completed_at_utc": iso_utc(capture_completed),
        "exact_thread_count": len(forum_threads),
        "active_thread_count": len(active["thread_ids"]),
        "discoverable_archived_thread_count": len(archived["thread_ids"]),
        "active_pass": {key: value for key, value in active.items() if key != "thread_ids"},
        "discoverable_archived_pass": {
            key: value for key, value in archived.items() if key != "thread_ids"
        },
        "starter_first_last_evidence_complete": True,
        "row_owned_exact_identity_evidence_complete": True,
        "attachment_cdn_ids_accepted_as_exact": False,
        "outside_sources_used": False,
        "source_input_sha256": {
            row["role"]: row["sha256"] for row in source_inputs
        },
    }
    ordinary_completion_evidence = {
        "validator": "merge_forum_thread_inventory.py",
        "data_cutoff_utc": iso_utc(cutoff),
        "capture_completed_at_utc": iso_utc(ordinary_capture_completed),
        "authenticated": True,
        "parent_audits_complete": True,
        "expected_parent_audit_count": EXPECTED_TOP_LEVEL_COUNT,
        "audited_parent_count": len(ordinary_parent_audits),
        "audited_parent_ids": sorted(
            str(row["parent_channel_id"]) for row in ordinary_parent_audits
        ),
        "applicable_parent_count": sum(
            1 for row in ordinary_parent_audits if row.get("applicable") is True
        ),
        "exact_thread_count": len(ordinary_threads),
        "unresolved_observed_occurrence_count": 0,
        "row_owned_exact_identity_evidence_complete": True,
        "attachment_cdn_ids_accepted_as_exact": False,
        "outside_sources_used": False,
        "source_input_sha256": sha256_bytes(ordinary_raw),
    }
    post_cutoff_scope = top_payload.get("accessible_scope", {}).get(
        "post_cutoff_navigation_resnapshot", {}
    )

    output = {
        "schema_version": "1.2",
        "guild_id": GUILD_ID,
        "guild_name": top_payload.get("guild_name"),
        "scope_definition": top_payload.get("scope_definition"),
        "requested_local_window": top_payload.get("requested_local_window"),
        "data_cutoff_utc": iso_utc(cutoff),
        "captured_at_utc": iso_utc(capture_completed),
        "capture_as_of_utc": iso_utc(capture_completed),
        "source_scope": "discord_only",
        "outside_sources_used": False,
        "inventory_method": (
            "Post-cutoff authenticated Discord top-level navigation inventory merged with "
            "post-cutoff authenticated forum and all-parent ordinary-thread enumeration"
        ),
        "inventory_complete": True,
        "status": "complete",
        "source_inputs": source_inputs,
        "accessible_scope": {
            "basis": top_payload.get("accessible_scope", {}).get("basis"),
            "top_level_containers": {
                "declared_complete": True,
                "expected_count": EXPECTED_TOP_LEVEL_COUNT,
                "status": "complete",
                "evidence": top_payload.get("accessible_scope", {})
                .get("top_level_containers", {})
                .get("evidence"),
            },
            "post_cutoff_navigation_resnapshot": {
                "declared_complete": True,
                "status": "complete",
                "required_capture_at_or_after_utc": iso_utc(cutoff),
                "completion_evidence": post_cutoff_scope.get("completion_evidence"),
            },
            "forum_threads": {
                "declared_complete": True,
                "status": "complete",
                "parent_forum_channel_id": PREMIUM_JOURNALS_ID,
                "expected_count": len(forum_threads),
                "discovery_method": (
                    "Authenticated Discord UI active-thread and discoverable-archive "
                    "enumeration, reconciled by exact thread snowflake ID"
                ),
                "completion_evidence": completion_evidence,
                "remaining_limitation": (
                    "Deleted, inaccessible, or no-longer-discoverable threads remain outside "
                    "the provable authenticated-account scope."
                ),
            },
            "ordinary_threads": {
                "declared_complete": True,
                "validated_complete": True,
                "status": "complete",
                "expected_parent_audit_count": EXPECTED_TOP_LEVEL_COUNT,
                "audited_parent_count": len(ordinary_parent_audits),
                "expected_count": len(ordinary_threads),
                "unresolved_observed_occurrence_count": 0,
                "discovery_method": (
                    "Authenticated Discord UI applicability plus active and discoverable-archive "
                    "passes for every one of the 38 top-level parents"
                ),
                "completion_evidence": ordinary_completion_evidence,
                "remaining_limitation": (
                    "Deleted, inaccessible, or no-longer-discoverable threads remain outside "
                    "the provable authenticated-account scope."
                ),
            },
        },
        "completeness": {
            "overall_inventory_complete": True,
            "top_level_exact_container_inventory_complete": True,
            "forum_thread_enumeration_complete": True,
            "active_forum_thread_enumeration_complete": True,
            "discoverable_archived_forum_thread_enumeration_complete": True,
            "ordinary_thread_enumeration_complete": True,
            "post_cutoff_authenticated_navigation_resnapshot_complete": True,
            "reason": (
                "All 38 post-cutoff-resnapshotted top-level containers, every exact premium-journals "
                "thread, and every ordinary thread represented by complete all-parent authenticated "
                "active/archive audits are included."
            ),
        },
        "provenance": {
            "source": "authenticated Discord UI only",
            "capture_method": (
                "validated merger of post-cutoff top-level, forum-thread, and ordinary-thread inputs"
            ),
            "capture_as_of_utc": iso_utc(capture_completed),
            "outside_sources_used": False,
            "source_inputs": source_inputs,
            "attachment_cdn_identity_policy": "never_exact",
        },
        "containers": top_level_containers + forum_threads + ordinary_threads,
        "top_level_container_count": len(top_level_containers),
        "forum_thread_count": len(forum_threads),
        "ordinary_thread_count": len(ordinary_threads),
        "container_count": (
            len(top_level_containers) + len(forum_threads) + len(ordinary_threads)
        ),
        "known_limitations": [
            "Inventory completeness is bounded to threads accessible or discoverable to the authenticated account at capture.",
            "Attachment-CDN path IDs are retained by collectors only as unverified metadata and are never accepted here as exact thread identity.",
        ],
    }
    return output


def write_json_atomic_no_overwrite(path: Path, value: dict[str, Any]) -> None:
    path = path.resolve()
    if path.exists():
        raise InventoryValidationError([f"output_already_exists:{path}"])
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # A hard link publishes the already-fsynced inode atomically and, unlike
            # os.replace(), fails if another process created the destination after
            # our initial check.  The temporary name is removed in the finally block.
            os.link(temporary_path, path)
        except FileExistsError as exc:
            raise InventoryValidationError([f"output_already_exists:{path}"]) from exc
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def merge_to_path(
    top_level_path: Path,
    forum_path: Path,
    ordinary_thread_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    resolved_output = output_path.resolve()
    for input_path in (
        top_level_path.resolve(),
        forum_path.resolve(),
        ordinary_thread_path.resolve(),
    ):
        if resolved_output == input_path:
            raise InventoryValidationError(["output_path_must_not_equal_input_path"])
    if resolved_output == DEFAULT_TOP_LEVEL_INVENTORY.resolve():
        raise InventoryValidationError(["source_top_level_inventory_must_never_be_modified"])
    merged = build_merged_inventory(top_level_path, forum_path, ordinary_thread_path)
    write_json_atomic_no_overwrite(resolved_output, merged)
    return merged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed validation and merge of a post-cutoff 38-container Discord inventory "
            "with authenticated forum and all-parent ordinary-thread enumeration."
        )
    )
    parser.add_argument(
        "--top-level-inventory", type=Path, default=DEFAULT_TOP_LEVEL_INVENTORY
    )
    parser.add_argument(
        "--forum-thread-inventory", type=Path, default=DEFAULT_FORUM_INVENTORY
    )
    parser.add_argument(
        "--ordinary-thread-inventory",
        type=Path,
        default=DEFAULT_ORDINARY_THREAD_INVENTORY,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        merged = merge_to_path(
            args.top_level_inventory,
            args.forum_thread_inventory,
            args.ordinary_thread_inventory,
            args.output,
        )
    except InventoryValidationError as exc:
        for issue in exc.issues:
            print(f"ERROR {issue}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "top_level_container_count": merged["top_level_container_count"],
                "forum_thread_count": merged["forum_thread_count"],
                "ordinary_thread_count": merged["ordinary_thread_count"],
                "container_count": merged["container_count"],
                "inventory_complete": merged["inventory_complete"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
