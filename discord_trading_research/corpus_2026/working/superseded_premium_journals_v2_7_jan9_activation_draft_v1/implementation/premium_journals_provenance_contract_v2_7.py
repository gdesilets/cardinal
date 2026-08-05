"""Future-only Premium Journals v2.7 direct-consensus provenance.

This module is deliberately separate from :mod:`premium_journals_provenance_contract`.
It can only validate an explicitly scheduled v2.7 route in ``raw/channel_segments_v2_7``.
Historical v2.6 files, including their checkpoint grammar, are never read as v2.7
artifacts or rewritten by this module.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

import premium_journals_attachment_accessory_contract_v2_7 as attachment
import premium_journals_provenance_contract as v26


COLLECTOR_VERSION = "2.7"
PROVENANCE_VERSION = "2.7"
AUTHORITATIVE_DIRECTORY = "raw/channel_segments_v2_7"
CHECKPOINT_DIRECTORY_PREFIX = "raw/premium_journals_v2_7_checkpoints"
PILOT_START = "2026-01-08"
EVIDENCE_TYPE = "forum_group_direct_candidate_consensus_exact"
EVIDENCE_SCHEMA_VERSION = "1.0.0"
CHECKPOINT_TYPE = "discord_forum_group_direct_consensus_checkpoint"
CHECKPOINT_SCHEMA_VERSION = "1.0.0"


def expected_canonical_relative_path(start: str, end: str) -> str:
    return f"{AUTHORITATIVE_DIRECTORY}/channel_premium_journals_{v26.PREMIUM_ID}_{start}_{end}.json"


def checkpoint_filename(evidence_key: str) -> str | None:
    suffix = str(evidence_key or "").removeprefix("forum-group-navigation:")
    return f"forum_group_direct_consensus_{suffix}.json" if v26.SHA256_RE.fullmatch(suffix) else None


def expected_checkpoint_relative_directory(day: str) -> str:
    return f"{CHECKPOINT_DIRECTORY_PREFIX}/{day}"


def _exact_parent_source(value: Any, guild_id: str, parent_id: str) -> str | None:
    parsed = v26._exact_guild_navigation_url(value)
    if not parsed or parsed != (guild_id, parent_id, None):
        return None
    return str(value)


def _has_attachment_signal(group: Sequence[dict[str, Any]]) -> bool:
    for row in group:
        if not isinstance(row, dict):
            return True
        if row.get("attachments") or row.get("links") or row.get("media_assets") or row.get("embeds"):
            return True
    return False


def _has_reply_signal(group: Sequence[dict[str, Any]]) -> bool:
    fields = (
        "reply_to_message_id", "reply_to_channel_id", "reply_to_permalink",
        "reply_to_message_id_source", "reply_target_resolution_status",
    )
    return any(
        any(row.get(field) for field in fields[:-1])
        or row.get("reply_target_resolution_status") == "exact_target_id"
        for row in group if isinstance(row, dict)
    )


def _reply_tuples(audit: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "method": "owned_reply_anchor",
            "owner_message_id": item["owner_message_id"],
            "target_message_id": item["reply_target_message_id"],
            "attachment_id": "",
            "target_url": item["reply_permalink"],
            "thread_channel_id": item["reply_channel_id"],
        }
        for item in audit.get("expected_evidence", {}).get("owner_scoped_reply_anchor_candidates", [])
    ]


def _attachment_tuples(audit: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "method": "owned_attachment_accessory",
            "owner_message_id": item["owner_message_id"],
            "target_message_id": "",
            "attachment_id": item["attachment_id"],
            "target_url": item["attachment_url"],
            "thread_channel_id": item["attachment_channel_id"],
        }
        for item in audit.get("expected_evidence", {}).get("candidates", [])
    ]


def audit_group(
    group: Sequence[dict[str, Any]], *, query: str, page_number: int,
    group_message_ids: Any, page_membership_sha256: str, page_plan_sha256: str,
    page_plan_bytes: int, current_source_url: str, guild_id: str = v26.GUILD_ID,
    parent_forum_channel_id: str = v26.PREMIUM_ID,
) -> dict[str, Any]:
    """Fail closed unless every present exact candidate family agrees on one child.

    The supplied ``current_source_url`` must be the current parent-forum root at
    the time of extraction; it is not synthesized from a candidate permalink.
    """
    errors: list[str] = []
    if not isinstance(query, str) or not query.strip() or query != query.strip():
        errors.append("query_not_exact_nonempty_string")
    if guild_id != v26.GUILD_ID:
        errors.append("guild_not_authorized_premium_scope")
    if parent_forum_channel_id != v26.PREMIUM_ID:
        errors.append("parent_forum_not_authorized_premium_scope")
    if not _exact_parent_source(current_source_url, guild_id, parent_forum_channel_id):
        errors.append("current_source_url_not_exact_authorized_parent")
    reply = v26.audit_owned_reply_anchor_group(
        group, query=query, page_number=page_number, group_message_ids=group_message_ids,
        page_membership_sha256=page_membership_sha256, page_plan_sha256=page_plan_sha256,
        page_plan_bytes=page_plan_bytes, guild_id=guild_id,
        parent_forum_channel_id=parent_forum_channel_id,
    )
    accessory = attachment.audit_group(
        group, query=query, page_number=page_number, group_message_ids=group_message_ids,
        page_membership_sha256=page_membership_sha256, page_plan_sha256=page_plan_sha256,
        page_plan_bytes=page_plan_bytes, guild_id=guild_id,
        parent_forum_channel_id=parent_forum_channel_id,
    )
    reply_present, accessory_present = _has_reply_signal(group), _has_attachment_signal(group)
    if reply_present and not reply["eligible"]:
        errors.extend(f"reply_candidate:{item}" for item in reply["errors"])
    if accessory_present and not accessory["eligible"]:
        errors.extend(f"attachment_candidate:{item}" for item in accessory["errors"])
    candidates = ([] if not reply["eligible"] else _reply_tuples(reply)) + ([] if not accessory["eligible"] else _attachment_tuples(accessory))
    candidates.sort(key=lambda item: (int(item["owner_message_id"]), item["method"], int(item["target_message_id"] or "0"), int(item["attachment_id"] or "0")))
    channels = sorted({item["thread_channel_id"] for item in candidates}, key=int)
    if not candidates:
        errors.append("direct_candidate_missing")
    if len(channels) != 1:
        errors.append("direct_candidate_channel_count_not_one")
    child = channels[0] if len(channels) == 1 else ""
    if child == parent_forum_channel_id:
        errors.append("direct_candidate_parent_forum_cannot_be_child")
    membership = v26._normalized_ids(group_message_ids) or []
    expected = {
        "provenance_version": PROVENANCE_VERSION,
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_type": EVIDENCE_TYPE,
        "evidence_key": v26.forum_group_evidence_key(query, page_number, membership),
        "query": query, "page_number": page_number, "group_message_ids": membership,
        "page_membership_sha256": page_membership_sha256,
        "page_plan_sha256": page_plan_sha256, "page_plan_bytes": page_plan_bytes,
        "guild_id": guild_id, "parent_forum_channel_id": parent_forum_channel_id,
        "current_source_url": current_source_url,
        "current_source_parent_verified": not any(x == "current_source_url_not_exact_authorized_parent" for x in errors),
        "thread_channel_id": child,
        "destination_url": f"https://discord.com/channels/{guild_id}/{child}",
        "candidate_tuples": candidates, "candidate_count": len(candidates),
        "channel_candidates": channels, "channel_candidate_count": len(channels),
        "candidate_methods": sorted({item["method"] for item in candidates}),
        "navigation_performed": False, "source_scope": "discord_only",
        "outside_sources_used": False, "authenticated": True,
    }
    return {"eligible": not errors, "errors": sorted(set(errors)), "thread_channel_id": child or None, "expected_evidence": expected}


def validate_evidence(evidence: Any, group: Sequence[dict[str, Any],], **kwargs: Any) -> tuple[str | None, list[str]]:
    audit = audit_group(group, **kwargs)
    errors = list(audit["errors"])
    if not isinstance(evidence, dict):
        return None, sorted(set(errors + ["evidence_not_object"]))
    expected = audit["expected_evidence"]
    if set(evidence) != set(expected) | {"observed_at_utc"}:
        errors.append("evidence_field_set_mismatch")
    if any(evidence.get(key) != value for key, value in expected.items()):
        errors.append("evidence_binding_mismatch")
    if not v26._is_iso_timestamp(evidence.get("observed_at_utc")):
        errors.append("evidence_timestamp_invalid")
    return audit["thread_channel_id"], sorted(set(errors))


def build_checkpoint(evidence: dict[str, Any], observed_at_utc: str) -> dict[str, Any]:
    """Return the immutable per-group checkpoint; caller must write exclusively."""
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION, "artifact_type": CHECKPOINT_TYPE,
        "immutable": True, "checkpointed_at_utc": observed_at_utc,
        "evidence_key": evidence.get("evidence_key"), "query": evidence.get("query"),
        "page_number": evidence.get("page_number"), "group_message_ids": evidence.get("group_message_ids"),
        "current_source_url": evidence.get("current_source_url"),
        "destination_url": evidence.get("destination_url"), "thread_channel_id": evidence.get("thread_channel_id"),
        "page_membership_sha256": evidence.get("page_membership_sha256"),
        "page_plan_sha256": evidence.get("page_plan_sha256"), "page_plan_bytes": evidence.get("page_plan_bytes"),
        "candidate_tuples": evidence.get("candidate_tuples"), "evidence": evidence,
    }


def validate_explicit_v2_7_route(route: dict[str, Any]) -> list[str]:
    """Schedule guard: v2.7 is future-only and never inferred from a date."""
    errors: list[str] = []
    if route.get("collector_version") != COLLECTOR_VERSION:
        errors.append("collector_version_not_v2_7")
    if route.get("provenance_version") != PROVENANCE_VERSION:
        errors.append("provenance_version_not_v2_7")
    if route.get("v2_7_explicit_opt_in") is not True:
        errors.append("v2_7_route_not_explicit_opt_in")
    if str(route.get("start") or "") < PILOT_START or route.get("start") != route.get("end"):
        errors.append("v2_7_route_not_future_single_day")
    if route.get("expected_canonical_path") != expected_canonical_relative_path(str(route.get("start") or ""), str(route.get("end") or "")):
        errors.append("v2_7_expected_path_mismatch")
    if route.get("live_collection_enabled") is not False:
        errors.append("v2_7_live_collection_must_remain_disabled")
    return errors
