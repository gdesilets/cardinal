"""Future-only no-navigation proof for Premium attachment accessories.

This module is intentionally not imported by the v2.6 collector, the
historically-bound Premium contract, or release promotion.  It is a shadow
pilot predicate only; activating it requires an explicit future contract and
release-policy change after independent navigation comparison.
"""
from __future__ import annotations

from typing import Any, Sequence
from urllib.parse import urlparse

import premium_journals_provenance_contract as v26


EVIDENCE_TYPE = "forum_group_owned_attachment_accessory_exact"
SCHEMA_VERSION = "1.0.0"


def _cdn_pair(value: Any, *, exact: bool) -> tuple[str, str] | None:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not (
        parsed.scheme == "https"
        and parsed.hostname in v26.DISCORD_ATTACHMENT_HOSTS
        and len(parts) >= 4
        and parts[0] == "attachments"
        and v26.SNOWFLAKE_RE.fullmatch(parts[1])
        and v26.SNOWFLAKE_RE.fullmatch(parts[2])
    ):
        return None
    if exact and (
        parsed.params
        or parsed.query
        or parsed.fragment
        or len(parts) != 4
        or not parts[3]
    ):
        return None
    return parts[1], parts[2]


def audit_group(
    group: Sequence[dict[str, Any]],
    *,
    query: str,
    page_number: int,
    group_message_ids: Any,
    page_membership_sha256: str,
    page_plan_sha256: str,
    page_plan_bytes: int,
    guild_id: str = v26.GUILD_ID,
    parent_forum_channel_id: str = v26.PREMIUM_ID,
) -> dict[str, Any]:
    """Fail closed unless exact current accessories prove exactly one child."""

    errors: list[str] = []
    if guild_id != v26.GUILD_ID:
        errors.append("guild_not_authorized_premium_scope")
    if parent_forum_channel_id != v26.PREMIUM_ID:
        errors.append("parent_forum_not_authorized_premium_scope")
    membership = v26._normalized_ids(group_message_ids)
    if not membership:
        errors.append("membership_invalid")
        membership = []
    if (
        not isinstance(query, str)
        or not query.strip()
        or query != query.strip()
        or type(page_number) is not int
        or page_number < 1
    ):
        errors.append("query_or_page_invalid")
    if not v26.SHA256_RE.fullmatch(str(page_membership_sha256 or "")):
        errors.append("page_membership_hash_invalid")
    if (
        not v26.SHA256_RE.fullmatch(str(page_plan_sha256 or ""))
        or type(page_plan_bytes) is not int
        or page_plan_bytes < 1
    ):
        errors.append("source_file_binding_invalid")
    key = v26.forum_group_evidence_key(query, page_number, membership)
    candidates: list[dict[str, str]] = []
    owner_ids: list[str] = []
    card_ids: set[str] = set()
    for index, row in enumerate(group, 1):
        prefix = f"row_{index}"
        if not isinstance(row, dict):
            errors.append(f"{prefix}_not_object")
            continue
        owner_id = str(row.get("message_id") or "")
        owner_ids.append(owner_id)
        if not (
            v26.SNOWFLAKE_RE.fullmatch(owner_id)
            and row.get("forum_group_membership_exact") is True
            and v26._normalized_ids(row.get("forum_group_message_ids")) == membership
            and row.get("forum_group_membership_key") == key
            and row.get("search_query") == query
            and row.get("page_number") == page_number
        ):
            errors.append(f"{prefix}_membership_query_or_page_drift")
        if row.get("group_header_parent_forum_channel_id") not in (
            None, parent_forum_channel_id
        ):
            errors.append(f"{prefix}_card_parent_mismatch")
        card = str(row.get("group_header_data_list_item_id") or "")
        if card:
            match = v26.re.fullmatch(r"forum-channel-list-(\d{15,22})___(\d{15,22})", card)
            if not match or match.group(1) != parent_forum_channel_id:
                errors.append(f"{prefix}_card_invalid")
            else:
                card_ids.add(match.group(2))
        exact_pairs: set[tuple[str, str]] = set()
        attachments = row.get("attachments")
        if not isinstance(attachments, list):
            errors.append(f"{prefix}_attachments_not_array")
            attachments = []
        for attachment_index, attachment in enumerate(attachments, 1):
            if not isinstance(attachment, dict):
                errors.append(f"{prefix}_attachment_{attachment_index}_not_object")
                continue
            pair = _cdn_pair(attachment.get("url"), exact=True)
            evidence = attachment.get("ownership_evidence")
            evidence = evidence if isinstance(evidence, dict) else {}
            attachment_id = str(attachment.get("attachment_id") or "")
            if not (
                pair
                and pair[1] == attachment_id
                and attachment.get("relation_type") == "owned"
                and attachment.get("ownership_status") == "owned_exact"
                and attachment.get("dom_relation") == "exact_message_accessories_descendant"
                and attachment.get("href_in_message_content") is False
                and evidence.get("schema_version") == "1.0.0"
                and evidence.get("exact") is True
                and evidence.get("owner_message_id") == owner_id
                and evidence.get("owner_channel_id") == pair[0]
                and evidence.get("source_channel_id") == pair[0]
                and evidence.get("dom_relation") == "exact_message_accessories_descendant"
            ):
                errors.append(f"{prefix}_attachment_{attachment_index}_not_exact_owned_accessory")
                continue
            channel_id = pair[0]
            for value in (
                attachment.get("thread_channel_id"),
                evidence.get("owner_channel_id"),
                evidence.get("source_channel_id"),
            ):
                if value not in (None, "") and str(value) != channel_id:
                    errors.append(f"{prefix}_attachment_{attachment_index}_channel_conflict")
            exact_pairs.add(pair)
            candidates.append({
                "owner_message_id": owner_id,
                "attachment_id": attachment_id,
                "attachment_url": str(attachment.get("url")),
                "attachment_channel_id": channel_id,
            })
        # Links, embeds, and media are never candidates.  An attachment-like
        # URL that lacks a matching exact accessory makes the group ambiguous.
        seen: set[tuple[str, str]] = set()
        for value in row.get("links") if isinstance(row.get("links"), list) else []:
            pair = _cdn_pair(value, exact=False)
            if pair: seen.add(pair)
        for field in ("media_assets", "embeds"):
            values = row.get(field)
            if not isinstance(values, list):
                continue
            for item in values:
                probes = [item] if isinstance(item, str) else [item.get(k) for k in ("url", "src", "href")] if isinstance(item, dict) else []
                for value in probes:
                    pair = _cdn_pair(value, exact=False)
                    if pair: seen.add(pair)
        if not seen <= exact_pairs:
            errors.append(f"{prefix}_content_link_or_embed_not_owned_accessory")
    if sorted(owner_ids) != membership or len(owner_ids) != len(set(owner_ids)):
        errors.append("group_membership_not_exact")
    channels = {row["attachment_channel_id"] for row in candidates}
    if not candidates:
        errors.append("candidate_missing")
    if len(channels) != 1:
        errors.append("channel_candidate_count_not_one")
    child_id = next(iter(channels)) if len(channels) == 1 else ""
    if child_id == parent_forum_channel_id:
        errors.append("parent_forum_cannot_be_child")
    if card_ids and card_ids != {child_id}:
        errors.append("card_identifier_conflict")
    reply = v26.audit_owned_reply_anchor_group(
        group, query=query, page_number=page_number, group_message_ids=membership,
        page_membership_sha256=page_membership_sha256,
        page_plan_sha256=page_plan_sha256, page_plan_bytes=page_plan_bytes,
        guild_id=guild_id, parent_forum_channel_id=parent_forum_channel_id,
    )
    if reply["eligible"] and reply["thread_channel_id"] != child_id:
        errors.append("reply_anchor_conflict")
    candidates.sort(key=lambda item: (int(item["owner_message_id"]), int(item["attachment_id"])))
    selected = candidates[0] if candidates else {key: "" for key in ("owner_message_id", "attachment_id", "attachment_url", "attachment_channel_id")}
    evidence = {
        "future_contract_version": "2.7-pilot",
        "evidence_type": EVIDENCE_TYPE,
        "schema_version": SCHEMA_VERSION,
        "evidence_key": key,
        "query": query,
        "page_number": page_number,
        "group_message_ids": membership,
        "page_membership_sha256": page_membership_sha256,
        "pre_navigation_page_membership_sha256": page_membership_sha256,
        "page_plan_sha256": page_plan_sha256,
        "page_plan_bytes": page_plan_bytes,
        "source_file_role": "immutable_forum_navigation_page_plan",
        "source_file_sha256": page_plan_sha256,
        "source_file_bytes": page_plan_bytes,
        "guild_id": guild_id,
        "parent_forum_channel_id": parent_forum_channel_id,
        "thread_channel_id": child_id,
        "anchor": selected,
        "candidates": candidates,
        "channel_candidates": sorted(channels, key=int),
        "reply_anchor_thread_channel_id": reply["thread_channel_id"] if reply["eligible"] else None,
        "navigation_performed": False,
        "source_scope": "discord_only",
        "outside_sources_used": False,
    }
    return {"eligible": not errors, "errors": sorted(set(errors)), "thread_channel_id": child_id or None, "expected_evidence": evidence}


def validate_evidence(evidence: Any, group: Sequence[dict[str, Any]], **kwargs: Any) -> tuple[str | None, list[str]]:
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
