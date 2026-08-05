from __future__ import annotations

"""Strict, byte-bound acceptance for Premium Journals collector-v2.6 shards.

The schedule builder and its independent validator both call this module.  A
file is accepted only from the dedicated v2.5 root and only when every search
row has exact forum-group membership, exact authenticated group-header
navigation, an exact child-thread binding, and the shared timestamp/reply/
attachment semantic contracts.
"""

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

import reply_provenance_contract
import timestamp_scope_revalidation


GUILD_ID = "1167376964680691732"
PREMIUM_ID = "1283941772577472643"
PREMIUM_NAME = "premium-journals"
PREMIUM_CATEGORY = "PREMIUM"
AUTHORITATIVE_DIRECTORY = "raw/channel_segments_v2_5"
LEGACY_PRESERVATION_DIRECTORY = "raw/channel_segments"
COLLECTOR_VERSION = "2.6"
FORUM_NAVIGATION_CONTRACT_VERSION = "1.1.0"
FORUM_PAGE_PLAN_SCHEMA_VERSION = "1.0.0"
FORUM_CHECKPOINT_SCHEMA_VERSION = "1.0.0"
OWNED_REPLY_ANCHOR_SCHEMA_VERSION = "1.0.0"
OWNED_REPLY_ANCHOR_EVIDENCE_TYPE = "forum_group_owned_reply_anchor_exact"
TIMESTAMP_SIDECAR_SUFFIX = (
    timestamp_scope_revalidation.TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX
)
SNOWFLAKE_RE = re.compile(r"\d{15,22}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
DISCORD_ATTACHMENT_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}
TRUSTED_THREAD_SOURCES = {
    "forum_group_header_data_list_item_id": "thread_id_from_forum_group_header",
    "forum_group_header_navigation_exact": (
        "thread_id_from_forum_group_header_navigation"
    ),
    OWNED_REPLY_ANCHOR_EVIDENCE_TYPE: "thread_id_from_owned_reply_permalink",
}


class PremiumJournalsContractError(ValueError):
    """An artifact cannot be promoted into the authoritative Premium corpus."""


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(compact_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_canonical_relative_path(start: str, end: str) -> str:
    return (
        f"{AUTHORITATIVE_DIRECTORY}/channel_premium_journals_{PREMIUM_ID}_"
        f"{start}_{end}.json"
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PremiumJournalsContractError(
            f"Premium canonical is not readable JSON: {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise PremiumJournalsContractError(
            f"Premium canonical must contain a JSON object: {path}"
        )
    return value


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise PremiumJournalsContractError(
            f"Premium artifact is outside its declared artifact root: {path}"
        ) from exc


def _resolve_declared_path(value: Any, root: Path) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    declared = Path(text)
    resolved = declared.resolve() if declared.is_absolute() else (root / declared).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


def _is_iso_timestamp(value: Any) -> bool:
    text = str(value or "")
    if not text.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _normalized_ids(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    ids = [str(item or "") for item in value]
    if any(not SNOWFLAKE_RE.fullmatch(item) for item in ids):
        return None
    if len(set(ids)) != len(ids):
        return None
    return sorted(ids)


def forum_group_evidence_key(query: str, page_number: int, message_ids: Any) -> str | None:
    ids = _normalized_ids(message_ids)
    if not str(query or "").strip() or type(page_number) is not int or page_number < 1 or not ids:
        return None
    fingerprint = json.dumps(
        {
            "query": str(query).strip(),
            "page_number": page_number,
            "group_message_ids": ids,
        },
        separators=(",", ":"),
    )
    return "forum-group-navigation:" + hashlib.sha256(
        fingerprint.encode("utf-8")
    ).hexdigest()


def forum_group_membership_sha256(
    query: str, page_number: int, message_ids: Any
) -> str | None:
    key = forum_group_evidence_key(query, page_number, message_ids)
    return key.split(":", 1)[1] if key else None


def forum_page_membership_sha256(
    query: str,
    page_number: int,
    reported_total: int,
    canonical: Any,
) -> str | None:
    """Mirror collector-v2.6's insertion-ordered JSON page-plan digest."""

    if not (
        str(query or "").strip()
        and type(page_number) is int
        and page_number >= 1
        and type(reported_total) is int
        and reported_total >= 1
        and isinstance(canonical, dict)
        and isinstance(canonical.get("groups"), list)
        and isinstance(canonical.get("rows"), list)
    ):
        return None
    fingerprint = json.dumps(
        {
            "query": str(query).strip(),
            "page_number": page_number,
            "reported_total": reported_total,
            "canonical": canonical,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def forum_group_navigation_checkpoint_filename(evidence_key: str) -> str | None:
    match = re.fullmatch(r"forum-group-navigation:([0-9a-f]{64})", evidence_key)
    return f"forum_group_navigation_{match.group(1)}.json" if match else None


def _exact_guild_navigation_url(value: Any) -> tuple[str, str, str | None] | None:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not (
        parsed.scheme == "https"
        and parsed.hostname in {"discord.com", "www.discord.com"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and len(parts) in {3, 4}
        and parts[0] == "channels"
        and SNOWFLAKE_RE.fullmatch(parts[1])
        and SNOWFLAKE_RE.fullmatch(parts[2])
        and (len(parts) == 3 or SNOWFLAKE_RE.fullmatch(parts[3]))
    ):
        return None
    return parts[1], parts[2], parts[3] if len(parts) == 4 else None


def _exact_thread_destination(value: Any) -> tuple[str, str] | None:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not (
        parsed.scheme == "https"
        and parsed.hostname in {"discord.com", "www.discord.com"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and len(parts) == 3
        and parts[0] == "channels"
        and SNOWFLAKE_RE.fullmatch(parts[1])
        and SNOWFLAKE_RE.fullmatch(parts[2])
    ):
        return None
    return parts[1], parts[2]


def audit_owned_reply_anchor_group(
    group: Sequence[dict[str, Any]],
    *,
    query: str,
    page_number: int,
    group_message_ids: Any,
    page_membership_sha256: str,
    page_plan_sha256: str,
    page_plan_bytes: int,
    guild_id: str = GUILD_ID,
    parent_forum_channel_id: str = PREMIUM_ID,
) -> dict[str, Any]:
    """Derive one child solely from current, row-owned reply permalinks.

    This predicate deliberately ignores titles, group labels, attachment URLs,
    cached mappings, prior thread IDs, and previously collected days.  It is
    safe to use as an alternative to a group-header click only when the full
    current group and immutable current-page plan are exact and every exact
    reply candidate resolves to one and only one child channel.
    """

    errors: list[str] = []
    normalized_membership = _normalized_ids(group_message_ids)
    if not normalized_membership:
        errors.append("owned_reply_anchor_group_membership_invalid")
        normalized_membership = []
    if not str(query or "").strip():
        errors.append("owned_reply_anchor_query_missing")
    if type(page_number) is not int or page_number < 1:
        errors.append("owned_reply_anchor_page_number_invalid")
    if not SHA256_RE.fullmatch(str(page_membership_sha256 or "")):
        errors.append("owned_reply_anchor_page_membership_hash_invalid")
    if not SHA256_RE.fullmatch(str(page_plan_sha256 or "")):
        errors.append("owned_reply_anchor_page_plan_sha256_invalid")
    if type(page_plan_bytes) is not int or page_plan_bytes < 1:
        errors.append("owned_reply_anchor_page_plan_bytes_invalid")
    if not SNOWFLAKE_RE.fullmatch(str(guild_id or "")):
        errors.append("owned_reply_anchor_guild_id_invalid")
    if not SNOWFLAKE_RE.fullmatch(str(parent_forum_channel_id or "")):
        errors.append("owned_reply_anchor_parent_id_invalid")

    expected_key = forum_group_evidence_key(
        str(query or ""), page_number, normalized_membership
    )
    observed_owner_ids: list[str] = []
    candidates: list[dict[str, str]] = []
    candidate_channels: set[str] = set()
    card_ids: list[str] = []
    for row_index, row in enumerate(group, start=1):
        prefix = f"owned_reply_anchor_row_{row_index}"
        if not isinstance(row, dict):
            errors.append(f"{prefix}_not_object")
            continue
        owner_id = str(row.get("message_id") or "")
        observed_owner_ids.append(owner_id)
        row_membership = _normalized_ids(row.get("forum_group_message_ids"))
        if not (
            SNOWFLAKE_RE.fullmatch(owner_id)
            and owner_id in normalized_membership
            and row.get("forum_group_membership_exact") is True
            and row_membership == normalized_membership
            and row.get("forum_group_membership_key") == expected_key
            and row.get("search_query") == query
            and row.get("page_number") == page_number
        ):
            errors.append(f"{prefix}_membership_query_or_page_drift")

        parent = row.get("group_header_parent_forum_channel_id")
        if parent not in (None, parent_forum_channel_id):
            errors.append(f"{prefix}_card_parent_mismatch")
        raw_card_id = str(row.get("group_header_data_list_item_id") or "")
        if raw_card_id:
            card_match = re.fullmatch(
                r"forum-channel-list-(\d{15,22})___(\d{15,22})", raw_card_id
            )
            if not card_match or card_match.group(1) != parent_forum_channel_id:
                errors.append(f"{prefix}_card_identifier_malformed_or_wrong_parent")
            else:
                card_ids.append(card_match.group(2))

        target_id = str(row.get("reply_to_message_id") or "")
        reply_channel_id = str(row.get("reply_to_channel_id") or "")
        permalink = str(row.get("reply_to_permalink") or "")
        reply_source = str(row.get("reply_to_message_id_source") or "")
        status = str(row.get("reply_target_resolution_status") or "")
        has_exact_reply_signal = bool(
            target_id
            or reply_channel_id
            or permalink
            or reply_source
            or status == "exact_target_id"
        )
        if not target_id:
            if has_exact_reply_signal:
                errors.append(f"{prefix}_partial_or_malformed_reply_candidate")
            continue

        reply_errors = reply_provenance_contract.exact_reply_target_contract_errors(
            row, guild_id=guild_id
        )
        errors.extend(f"{prefix}_{reason}" for reason in reply_errors)
        parsed = _exact_guild_navigation_url(permalink)
        if not (
            parsed
            and parsed[0] == guild_id
            and parsed[1] == reply_channel_id
            and parsed[2] == target_id
        ):
            errors.append(f"{prefix}_reply_permalink_not_exact")
        if not (
            row.get("reply_context_present") is True
            and row.get("reply_context_scope_exact") is True
            and row.get("reply_target_owner_scoped") is True
            and row.get("reply_target_scope_exact") is True
            and row.get("reply_to_message_id_conflict") is False
            and row.get("reply_to_channel_id_conflict") is False
            and row.get("reply_target_resolution_status") == "exact_target_id"
            and row.get("reply_target_unavailability_documented") is False
        ):
            errors.append(f"{prefix}_reply_owner_scope_or_conflict_state_invalid")
        if not SNOWFLAKE_RE.fullmatch(reply_channel_id):
            errors.append(f"{prefix}_reply_channel_id_invalid")
        else:
            candidate_channels.add(reply_channel_id)

        for field in ("reply_to_message_id_candidates", "reply_target_id_candidates"):
            raw_candidates = row.get(field)
            if not isinstance(raw_candidates, list) or not raw_candidates:
                errors.append(f"{prefix}_{field}_missing")
                continue
            for candidate_index, candidate in enumerate(raw_candidates, start=1):
                if not isinstance(candidate, dict):
                    errors.append(
                        f"{prefix}_{field}_{candidate_index}_not_object"
                    )
                    continue
                if (
                    str(candidate.get("message_id") or "") != target_id
                    or candidate.get("owner_scoped") is not True
                    or str(candidate.get("source") or "")
                    not in reply_provenance_contract.EXACT_ROW_OWNED_REPLY_SOURCES
                ):
                    errors.append(
                        f"{prefix}_{field}_{candidate_index}_identity_or_scope_invalid"
                    )
                candidate_channel = str(candidate.get("channel_id") or "")
                if candidate_channel:
                    if not SNOWFLAKE_RE.fullmatch(candidate_channel):
                        errors.append(
                            f"{prefix}_{field}_{candidate_index}_channel_invalid"
                        )
                    else:
                        candidate_channels.add(candidate_channel)

        candidates.append(
            {
                "owner_message_id": owner_id,
                "reply_target_message_id": target_id,
                "reply_permalink": permalink,
                "reply_channel_id": reply_channel_id,
            }
        )

    if sorted(observed_owner_ids) != normalized_membership:
        errors.append("owned_reply_anchor_group_rows_not_exact_membership")
    if len(set(observed_owner_ids)) != len(observed_owner_ids):
        errors.append("owned_reply_anchor_duplicate_owner_message")
    if not candidates:
        errors.append("owned_reply_anchor_candidate_missing")
    if len(candidate_channels) != 1:
        errors.append("owned_reply_anchor_channel_candidate_count_not_one")
    child_id = next(iter(candidate_channels)) if len(candidate_channels) == 1 else ""
    if child_id == parent_forum_channel_id:
        errors.append("owned_reply_anchor_parent_forum_cannot_be_child")
    if any(card_id != child_id for card_id in card_ids):
        errors.append("owned_reply_anchor_card_identifier_conflict")

    candidates.sort(
        key=lambda row: (
            int(row["owner_message_id"])
            if SNOWFLAKE_RE.fullmatch(row["owner_message_id"])
            else 0,
            int(row["reply_target_message_id"])
            if SNOWFLAKE_RE.fullmatch(row["reply_target_message_id"])
            else 0,
            row["reply_permalink"],
        )
    )
    selected = candidates[0] if candidates else {
        "owner_message_id": "",
        "reply_target_message_id": "",
        "reply_permalink": "",
        "reply_channel_id": "",
    }
    source_url = (
        f"https://discord.com/channels/{guild_id}/{parent_forum_channel_id}"
    )
    destination_url = f"https://discord.com/channels/{guild_id}/{child_id}"
    membership_hash = forum_group_membership_sha256(
        str(query or ""), page_number, normalized_membership
    )
    expected_evidence = {
        "schema_version": FORUM_NAVIGATION_CONTRACT_VERSION,
        "evidence_type": OWNED_REPLY_ANCHOR_EVIDENCE_TYPE,
        "reply_anchor_schema_version": OWNED_REPLY_ANCHOR_SCHEMA_VERSION,
        "evidence_key": expected_key,
        "guild_id": guild_id,
        "parent_forum_channel_id": parent_forum_channel_id,
        "query": query,
        "page_number": page_number,
        "group_message_ids": normalized_membership,
        "navigation_trigger": "owner_scoped_reply_permalink_no_navigation",
        "navigation_performed": False,
        "anchor_owner_message_id": selected["owner_message_id"],
        "anchor_reply_target_message_id": selected["reply_target_message_id"],
        "anchor_reply_permalink": selected["reply_permalink"],
        "anchor_reply_channel_id": selected["reply_channel_id"],
        "owner_scoped_reply_anchor_candidates": candidates,
        "owner_scoped_reply_anchor_candidate_count": len(candidates),
        "owner_scoped_reply_channel_candidates": sorted(
            candidate_channels, key=int
        ),
        "owner_scoped_reply_channel_candidate_count": len(candidate_channels),
        "source_url": source_url,
        "source_parent_forum_channel_id": parent_forum_channel_id,
        "source_parent_forum_verified": True,
        "destination_url": destination_url,
        "destination_guild_id": guild_id,
        "thread_channel_id": child_id,
        "destination_verified": True,
        "destination_verification_method": "owner_scoped_reply_permalink_exact",
        "back_url": source_url,
        "back_parent_forum_verified": True,
        "source_url_restored": True,
        "restored_query": query,
        "restored_page_number": page_number,
        "restored_group_message_ids": normalized_membership,
        "restored_group_membership_sha256": membership_hash,
        "pre_navigation_page_membership_sha256": page_membership_sha256,
        "restored_page_membership_sha256": page_membership_sha256,
        "page_plan_sha256": page_plan_sha256,
        "page_plan_bytes": page_plan_bytes,
        "page_plan_verified": True,
        "return_state_verified": True,
        "authenticated": True,
        "source_scope": "discord_only",
        "outside_sources_used": False,
    }
    return {
        "eligible": not errors,
        "errors": sorted(set(errors)),
        "thread_channel_id": child_id or None,
        "candidate_count": len(candidates),
        "channel_candidate_count": len(candidate_channels),
        "expected_evidence": expected_evidence,
    }


def validate_owned_reply_anchor_evidence(
    evidence: Any,
    group: Sequence[dict[str, Any]],
    *,
    query: str,
    page_number: int,
    group_message_ids: Any,
    page_membership_sha256: str,
    page_plan_sha256: str,
    page_plan_bytes: int,
) -> tuple[str | None, list[str]]:
    """Validate the exact on-disk evidence for the reply-anchor method."""

    audit = audit_owned_reply_anchor_group(
        group,
        query=query,
        page_number=page_number,
        group_message_ids=group_message_ids,
        page_membership_sha256=page_membership_sha256,
        page_plan_sha256=page_plan_sha256,
        page_plan_bytes=page_plan_bytes,
    )
    errors = list(audit["errors"])
    if not isinstance(evidence, dict):
        errors.append("owned_reply_anchor_evidence_not_object")
        return None, sorted(set(errors))
    expected = audit["expected_evidence"]
    if set(evidence) != set(expected) | {"observed_at_utc"}:
        errors.append("owned_reply_anchor_evidence_field_set_mismatch")
    if any(evidence.get(field) != value for field, value in expected.items()):
        errors.append("owned_reply_anchor_evidence_binding_mismatch")
    if not _is_iso_timestamp(evidence.get("observed_at_utc")):
        errors.append("owned_reply_anchor_observed_at_invalid")
    return (
        str(audit.get("thread_channel_id") or "") or None,
        sorted(set(errors)),
    )


def _snowflake_local_date(message_id: str) -> str | None:
    try:
        milliseconds = (int(message_id) >> 22) + 1420070400000
        observed = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
        march_first = datetime(observed.year, 3, 1, tzinfo=timezone.utc).date()
        november_first = datetime(observed.year, 11, 1, tzinfo=timezone.utc).date()
        second_sunday_march = march_first + timedelta(
            days=(6 - march_first.weekday()) % 7 + 7
        )
        first_sunday_november = november_first + timedelta(
            days=(6 - november_first.weekday()) % 7
        )
        daylight_start = datetime.combine(
            second_sunday_march, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=8)
        daylight_end = datetime.combine(
            first_sunday_november, datetime.min.time(), tzinfo=timezone.utc
        ) + timedelta(hours=7)
        offset = timedelta(hours=-5 if daylight_start <= observed < daylight_end else -6)
        return (observed + offset).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _validate_completion(
    completion: Any,
    *,
    query: str,
    reported_total: int,
    reported_pages: int,
) -> tuple[str | None, list[str]]:
    errors: list[str] = []
    if not isinstance(completion, dict):
        return None, ["completion_evidence_missing"]
    for key, expected in {
        "schema_version": "1.0.0",
        "query": query,
        "reported_total": reported_total,
        "reported_pages": reported_pages,
    }.items():
        if completion.get(key) != expected:
            errors.append(f"completion_evidence_{key}_mismatch")
    submission = completion.get("search_submission")
    if not isinstance(submission, dict):
        errors.append("completion_search_submission_missing")
        submission = {}
    if submission.get("query") != query:
        errors.append("completion_search_submission_query_mismatch")
    if not _is_iso_timestamp(
        submission.get("submitted_at_utc") or submission.get("observed_at_utc")
    ):
        errors.append("completion_search_submission_timestamp_invalid")

    terminal = completion.get("terminal_state")
    if reported_total == 0:
        if terminal != "stable_empty":
            errors.append("completion_terminal_not_stable_empty")
        if submission.get("mode") != "fresh" or submission.get("submission_count") != 1:
            errors.append("stable_empty_not_one_fresh_submission")
        stable = completion.get("stable_empty")
        stable = stable if isinstance(stable, dict) else {}
        observations = stable.get("observations")
        observations = observations if isinstance(observations, list) else []
        if stable.get("required_observations") != 2 or len(observations) != 2:
            errors.append("stable_empty_observation_count_not_two")
        for index, observation in enumerate(observations, start=1):
            if not isinstance(observation, dict):
                errors.append("stable_empty_observation_not_object")
                continue
            if observation.get("sequence") != index:
                errors.append("stable_empty_sequence_mismatch")
            if observation.get("state") != "empty_candidate":
                errors.append("stable_empty_state_mismatch")
            if observation.get("visible_result_count") != 0:
                errors.append("stable_empty_visible_count_nonzero")
            if not _is_iso_timestamp(observation.get("observed_at_utc")):
                errors.append("stable_empty_observed_at_invalid")
            if not re.search("no results", str(observation.get("panel_text") or ""), re.I):
                errors.append("stable_empty_panel_text_invalid")
    else:
        if terminal != "stable_bottom":
            errors.append("completion_terminal_not_stable_bottom")
        stable = completion.get("stable_bottom")
        stable = stable if isinstance(stable, dict) else {}
        observations = stable.get("observations")
        observations = observations if isinstance(observations, list) else []
        if stable.get("required_observations") != 2 or len(observations) != 2:
            errors.append("stable_bottom_observation_count_not_two")
        expected_first = (reported_pages - 1) * 25 + 1
        expected_visible = reported_total - expected_first + 1
        for index, observation in enumerate(observations, start=1):
            if not isinstance(observation, dict):
                errors.append("stable_bottom_observation_not_object")
                continue
            expected = {
                "sequence": index,
                "query": query,
                "current_page": reported_pages,
                "first_result_index": expected_first,
                "last_result_index": reported_total,
                "visible_result_count": expected_visible,
                "result_set_size": reported_total,
                "has_enabled_next": False,
            }
            if any(observation.get(key) != value for key, value in expected.items()):
                errors.append("stable_bottom_observation_mismatch")
            if not _is_iso_timestamp(observation.get("observed_at_utc")):
                errors.append("stable_bottom_observed_at_invalid")
    return str(terminal) if terminal is not None else None, sorted(set(errors))


def _attachment_semantic_audit(
    messages: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, set[str]], list[str]]:
    errors: list[str] = []
    attachment_ids: set[str] = set()
    owned_attachment_owners: dict[str, set[str]] = defaultdict(set)
    attachment_occurrences = 0
    non_owned_occurrences = 0
    for row_index, message in enumerate(messages, start=1):
        message_id = str(message.get("message_id") or "")
        child_id = str(message.get("inferred_thread_channel_id") or "")
        attachments = message.get("attachments")
        if not isinstance(attachments, list):
            errors.append(f"message_{row_index}_attachments_not_array")
            continue
        for attachment_index, attachment in enumerate(attachments, start=1):
            prefix = f"message_{row_index}_attachment_{attachment_index}"
            attachment_occurrences += 1
            if not isinstance(attachment, dict):
                errors.append(f"{prefix}_not_object")
                continue
            attachment_id = str(attachment.get("attachment_id") or "")
            if not SNOWFLAKE_RE.fullmatch(attachment_id):
                errors.append(f"{prefix}_id_invalid")
                continue
            attachment_ids.add(attachment_id)
            parsed = urlparse(str(attachment.get("url") or ""))
            url_parts = [part for part in parsed.path.split("/") if part]
            if not (
                parsed.scheme == "https"
                and parsed.hostname in DISCORD_ATTACHMENT_HOSTS
                and len(url_parts) >= 3
                and url_parts[0] == "attachments"
                and SNOWFLAKE_RE.fullmatch(url_parts[1])
                and url_parts[2] == attachment_id
            ):
                errors.append(f"{prefix}_discord_cdn_url_invalid")
            evidence = attachment.get("ownership_evidence")
            evidence = evidence if isinstance(evidence, dict) else {}
            relation = str(attachment.get("relation_type") or "")
            ownership = str(attachment.get("ownership_status") or "")
            base_exact = bool(
                evidence.get("schema_version") == "1.0.0"
                and evidence.get("exact") is True
                and str(evidence.get("owner_message_id") or "") == message_id
                and str(evidence.get("owner_channel_id") or "") == child_id
            )
            if relation == "owned" and ownership == "owned_exact":
                if not (
                    base_exact
                    and str(evidence.get("source_channel_id") or "") == child_id
                    and evidence.get("dom_relation")
                    == "exact_message_accessories_descendant"
                    and attachment.get("href_in_message_content") is False
                ):
                    errors.append(f"{prefix}_owned_exact_evidence_invalid")
                owned_attachment_owners[attachment_id].add(message_id)
            elif relation in {"embedded_external", "copied_media", "non_owned"} and ownership == "non_owned_exact":
                non_owned_occurrences += 1
                if not base_exact:
                    errors.append(f"{prefix}_non_owned_exact_evidence_invalid")
            else:
                errors.append(f"{prefix}_ownership_unresolved")
    multiple = {
        attachment_id: owners
        for attachment_id, owners in owned_attachment_owners.items()
        if len(owners) > 1
    }
    if multiple:
        errors.append("attachment_id_has_multiple_owned_message_owners")
    summary = {
        "passed": not errors,
        "attachment_occurrence_count": attachment_occurrences,
        "unique_attachment_id_count": len(attachment_ids),
        "unique_owned_attachment_id_count": len(owned_attachment_owners),
        "non_owned_attachment_occurrence_count": non_owned_occurrences,
        "multiple_owned_message_owner_count": len(multiple),
        "attachment_id_set_sha256": sha256_json(sorted(attachment_ids, key=int)),
    }
    return summary, owned_attachment_owners, sorted(set(errors))


def _reply_semantic_audit(
    messages: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    status_counts: Counter[str] = Counter()
    for row_index, message in enumerate(messages, start=1):
        status = str(message.get("reply_target_resolution_status") or "")
        status_counts[status or "missing"] += 1
        row_errors = reply_provenance_contract.resolution_status_boolean_errors(message)
        target_id = str(message.get("reply_to_message_id") or "")
        if target_id:
            row_errors.extend(
                reply_provenance_contract.exact_reply_target_contract_errors(
                    message, guild_id=GUILD_ID
                )
            )
            if str(message.get("reply_to_channel_id") or "") != str(
                message.get("inferred_thread_channel_id") or ""
            ):
                row_errors.append("reply_target_channel_not_exact_owner_thread")
        elif status in reply_provenance_contract.DOCUMENTED_NO_ID_STATUSES:
            row_errors.extend(
                reply_provenance_contract.documented_no_id_contract_errors(message)
            )
        elif status == "unresolved_without_exact_target_id":
            row_errors.append("reply_context_unresolved_without_exact_target")
        elif status != "not_applicable":
            row_errors.append("reply_resolution_status_missing_or_unknown")
        errors.extend(f"message_{row_index}_{reason}" for reason in row_errors)
    executed = reply_provenance_contract.audit_executed_command_contexts(
        list(messages), expected_message_ids=[]
    )
    if executed.get("passed") is not True:
        errors.append("executed_command_context_semantic_audit_failed")
    return {
        "passed": not errors,
        "status_counts": dict(sorted(status_counts.items())),
        "executed_command_contexts": executed,
    }, sorted(set(errors))


def _expected_forum_page_canonical(
    page_number: int,
    messages: Sequence[dict[str, Any]],
    group_rows: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], list[str]]:
    page_messages = sorted(
        (row for row in messages if row.get("page_number") == page_number),
        key=lambda row: (int(row.get("result_index") or 0), str(row.get("message_id") or "")),
    )
    page_groups: list[tuple[int, str, list[str]]] = []
    for evidence_key, rows in group_rows.items():
        if not rows or rows[0].get("page_number") != page_number:
            continue
        message_ids = _normalized_ids(rows[0].get("forum_group_message_ids")) or []
        first_index = min(int(row.get("result_index") or 0) for row in rows)
        page_groups.append((first_index, evidence_key, message_ids))
    page_groups.sort(key=lambda item: (item[0], item[1]))
    canonical = {
        "groups": [
            {
                "message_ids": message_ids,
                "direct_header_button_count": 1,
            }
            for _, _, message_ids in page_groups
        ],
        "rows": [
            {
                "message_id": str(row.get("message_id") or ""),
                "result_index": int(row.get("result_index") or 0),
            }
            for row in page_messages
        ],
    }
    return canonical, sorted(item[1] for item in page_groups)


def _read_navigation_json(path: Path, errors: list[str], label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"{label}_not_readable_json:{type(exc).__name__}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label}_not_object")
        return {}
    return value


def _audit_forum_navigation_artifacts(
    *,
    checkpoint_directory: Path | None,
    declared_checkpoint_directory: Any,
    artifact_root: Path,
    payload: dict[str, Any],
    route: dict[str, Any],
    reported_total: int,
    reported_pages: int,
    messages: Sequence[dict[str, Any]],
    group_rows: dict[str, list[dict[str, Any]]],
    evidence_map: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Validate and byte-bind every immutable page plan and group checkpoint."""

    errors: list[str] = []
    bound_files: list[dict[str, Any]] = []
    declared = str(declared_checkpoint_directory or "").strip().replace("\\", "/")
    if checkpoint_directory is None or not declared or Path(declared).is_absolute():
        errors.append("forum_navigation_checkpoint_directory_not_corpus_relative")
    elif _relative(checkpoint_directory, artifact_root) != declared:
        errors.append("forum_navigation_checkpoint_directory_not_normalized")

    top_page_plans = payload.get("forum_group_navigation_page_plans")
    if not isinstance(top_page_plans, dict):
        errors.append("forum_navigation_page_plans_not_object")
        top_page_plans = {}
    expected_top_page_keys = {str(page) for page in range(1, reported_pages + 1)}
    if set(top_page_plans) != expected_top_page_keys:
        errors.append("forum_navigation_page_plan_key_set_mismatch")
    if payload.get("forum_group_navigation_checkpoint_count") != len(group_rows):
        errors.append("forum_navigation_checkpoint_count_mismatch")

    expected_files: set[Path] = set()
    page_plan_hashes: dict[str, str] = {}
    checkpoint_count = 0
    for page_number in range(1, reported_pages + 1):
        canonical, expected_group_keys = _expected_forum_page_canonical(
            page_number, messages, group_rows
        )
        page_hash = forum_page_membership_sha256(
            str(route.get("query") or ""),
            page_number,
            reported_total,
            canonical,
        )
        if not page_hash:
            errors.append(f"forum_navigation_page_{page_number}_hash_not_derivable")
            continue
        page_plan_hashes[str(page_number)] = page_hash
        expected_summary = {
            "page_number": page_number,
            "page_membership_sha256": page_hash,
            "message_count": len(canonical["rows"]),
            "group_count": len(expected_group_keys),
            "group_evidence_keys": expected_group_keys,
            "all_rows_exact": True,
        }
        if top_page_plans.get(str(page_number)) != expected_summary:
            errors.append(f"forum_navigation_page_{page_number}_summary_mismatch")

        if checkpoint_directory is None:
            errors.append(f"forum_navigation_page_{page_number}_checkpoint_base_missing")
            continue
        page_directory = checkpoint_directory / f"page_{page_number:03d}"
        page_plan_path = page_directory / "page_plan.json"
        expected_files.add(page_plan_path.resolve())
        if not page_plan_path.is_file():
            errors.append(f"forum_navigation_page_{page_number}_plan_file_missing")
            page_plan: dict[str, Any] = {}
        else:
            page_plan = _read_navigation_json(
                page_plan_path, errors, f"forum_navigation_page_{page_number}_plan"
            )
            bound_files.append(
                {
                    "role": "forum_navigation_page_plan",
                    "path": _relative(page_plan_path, artifact_root),
                    "sha256": sha256_file(page_plan_path),
                    "bytes": page_plan_path.stat().st_size,
                }
            )
        expected_plan = {
            "schema_version": FORUM_PAGE_PLAN_SCHEMA_VERSION,
            "artifact_type": "discord_forum_navigation_page_plan",
            "query": route.get("query"),
            "page_number": page_number,
            "reported_total": reported_total,
            "page_membership_sha256": page_hash,
            "expected_group_count": len(expected_group_keys),
            "expected_message_count": len(canonical["rows"]),
            "expected_group_evidence_keys": expected_group_keys,
            "canonical": canonical,
            "immutable": True,
        }
        if set(page_plan) != set(expected_plan) | {"observed_at_utc"}:
            errors.append(f"forum_navigation_page_{page_number}_plan_field_set_mismatch")
        if any(page_plan.get(field) != value for field, value in expected_plan.items()):
            errors.append(f"forum_navigation_page_{page_number}_plan_binding_mismatch")
        if not _is_iso_timestamp(page_plan.get("observed_at_utc")):
            errors.append(f"forum_navigation_page_{page_number}_plan_timestamp_invalid")

        for evidence_key in expected_group_keys:
            filename = forum_group_navigation_checkpoint_filename(evidence_key)
            if not filename:
                errors.append(f"forum_navigation_page_{page_number}_checkpoint_key_invalid")
                continue
            checkpoint_path = page_directory / filename
            expected_files.add(checkpoint_path.resolve())
            checkpoint_count += 1
            evidence = evidence_map.get(evidence_key)
            evidence = evidence if isinstance(evidence, dict) else {}
            group = group_rows.get(evidence_key) or []
            membership = _normalized_ids(
                group[0].get("forum_group_message_ids") if group else None
            ) or []
            if not checkpoint_path.is_file():
                errors.append(
                    f"forum_navigation_checkpoint_missing:{page_number}:{evidence_key}"
                )
                checkpoint: dict[str, Any] = {}
            else:
                checkpoint = _read_navigation_json(
                    checkpoint_path,
                    errors,
                    f"forum_navigation_checkpoint_{page_number}_{evidence_key}",
                )
                bound_files.append(
                    {
                        "role": "forum_group_navigation_checkpoint",
                        "path": _relative(checkpoint_path, artifact_root),
                        "sha256": sha256_file(checkpoint_path),
                        "bytes": checkpoint_path.stat().st_size,
                    }
                )
            expected_checkpoint = {
                "schema_version": FORUM_CHECKPOINT_SCHEMA_VERSION,
                "artifact_type": "discord_forum_group_navigation_checkpoint",
                "evidence_key": evidence_key,
                "query": route.get("query"),
                "page_number": page_number,
                "group_message_ids": membership,
                "source_url": evidence.get("source_url"),
                "destination_url": evidence.get("destination_url"),
                "thread_channel_id": evidence.get("thread_channel_id"),
                "back_url": evidence.get("back_url"),
                "restored_group_membership_sha256": evidence.get(
                    "restored_group_membership_sha256"
                ),
                "pre_navigation_page_membership_sha256": page_hash,
                "restored_page_membership_sha256": page_hash,
                "immutable": True,
                "evidence": evidence,
            }
            if set(checkpoint) != set(expected_checkpoint) | {"checkpointed_at_utc"}:
                errors.append(
                    f"forum_navigation_checkpoint_field_set_mismatch:{page_number}:{evidence_key}"
                )
            if any(
                checkpoint.get(field) != value
                for field, value in expected_checkpoint.items()
            ):
                errors.append(
                    f"forum_navigation_checkpoint_binding_mismatch:{page_number}:{evidence_key}"
                )
            if not _is_iso_timestamp(checkpoint.get("checkpointed_at_utc")):
                errors.append(
                    f"forum_navigation_checkpoint_timestamp_invalid:{page_number}:{evidence_key}"
                )

    if checkpoint_count != len(group_rows):
        errors.append("forum_navigation_expected_checkpoint_count_mismatch")
    if checkpoint_directory is not None and checkpoint_directory.exists():
        actual_files = {
            path.resolve()
            for path in checkpoint_directory.rglob("*")
            if path.is_file()
        }
        extras = actual_files - expected_files
        missing = expected_files - actual_files
        if extras:
            errors.append(
                "forum_navigation_unplanned_checkpoint_artifacts:"
                + ",".join(
                    sorted(_relative(path, artifact_root) for path in extras)
                )
            )
        if missing:
            errors.append("forum_navigation_checkpoint_file_set_incomplete")
    elif reported_pages > 0:
        errors.append("forum_navigation_checkpoint_directory_missing")

    bound_files.sort(key=lambda row: (row["path"], row["role"]))
    summary = {
        "passed": not errors,
        "checkpoint_directory": declared,
        "page_plan_count": reported_pages,
        "group_checkpoint_count": checkpoint_count,
        "page_plan_hashes": page_plan_hashes,
        "page_plan_hash_set_sha256": sha256_json(page_plan_hashes),
        "bound_file_count": len(bound_files),
        "bound_file_set_sha256": sha256_json(
            [
                {
                    "path": row["path"],
                    "sha256": row["sha256"],
                    "bytes": row["bytes"],
                }
                for row in bound_files
            ]
        ),
    }
    return summary, bound_files, sorted(set(errors))


def audit_premium_canonical(
    path: Path,
    route: dict[str, Any],
    *,
    artifact_root: Path,
) -> dict[str, Any]:
    """Re-derive one accepted-artifact record and private aggregate inputs."""

    path = path.resolve()
    artifact_root = artifact_root.resolve()
    relative = _relative(path, artifact_root)
    expected_relative = expected_canonical_relative_path(
        str(route.get("start") or ""), str(route.get("end") or "")
    )
    errors: list[str] = []
    if relative != expected_relative:
        errors.append(
            f"canonical_path_mismatch:{relative!r}!={expected_relative!r}"
        )
    payload = _read_json_object(path)
    observed_sha256 = sha256_file(path)
    for key, expected in {
        "guild_id": GUILD_ID,
        "collector_version": COLLECTOR_VERSION,
        "collection_scope": "channel-scoped",
        "complete": True,
    }.items():
        if payload.get(key) != expected:
            errors.append(f"{key}_mismatch")
    segment = payload.get("segment")
    segment = segment if isinstance(segment, dict) else {}
    for key, expected in {
        "start": route.get("start"),
        "end": route.get("end"),
        "query": route.get("query"),
        "timezone": "America/Chicago",
    }.items():
        if segment.get(key) != expected:
            errors.append(f"segment_{key}_mismatch")
    if route.get("start") != route.get("end"):
        errors.append("premium_route_not_single_local_day")
    requested = payload.get("requested_container")
    requested = requested if isinstance(requested, dict) else {}
    for key, expected in {
        "channel_id": PREMIUM_ID,
        "channel_name": PREMIUM_NAME,
        "channel_kind": "forum channel",
        "category_name": PREMIUM_CATEGORY,
        "channel_id_source": "inventory_exact_href",
    }.items():
        if requested.get(key) != expected:
            errors.append(f"requested_container_{key}_mismatch")

    reported_total = payload.get("reported_total")
    if type(reported_total) is not int or reported_total < 0:
        errors.append("reported_total_invalid")
        reported_total = 0
    reported_pages = payload.get("reported_pages")
    expected_pages = math.ceil(reported_total / 25)
    if reported_pages != expected_pages:
        errors.append("reported_pages_mismatch")
    if payload.get("pages_captured") != expected_pages:
        errors.append("pages_captured_mismatch")
    for key in ("captured_rows", "unique_message_ids"):
        if payload.get(key) != reported_total:
            errors.append(f"{key}_mismatch")
    if payload.get("gap_indices") not in ([], None):
        errors.append("gap_indices_nonempty")
    if payload.get("container_mismatch_count") != 0 or payload.get(
        "container_mismatch_message_ids"
    ) not in ([], None):
        errors.append("container_mismatch_present")
    if payload.get("forum_group_navigation_unresolved_count") != 0 or payload.get(
        "forum_group_navigation_unresolved_message_ids"
    ) not in ([], None):
        errors.append("forum_group_navigation_unresolved")
    if (
        payload.get("forum_group_navigation_contract_version")
        != FORUM_NAVIGATION_CONTRACT_VERSION
    ):
        errors.append("forum_group_navigation_contract_version_mismatch")
    if payload.get("forum_group_navigation_page_acceptance") != (
        "all_groups_exact_before_page_acceptance"
    ):
        errors.append("forum_group_navigation_page_acceptance_mismatch")
    declared_checkpoint_directory = payload.get(
        "forum_group_navigation_checkpoint_directory"
    )
    checkpoint_directory = _resolve_declared_path(
        declared_checkpoint_directory, artifact_root
    )
    if checkpoint_directory is None:
        errors.append("forum_group_navigation_checkpoint_directory_invalid")
    completion_validation = payload.get("completion_evidence_validation")
    if not (
        isinstance(completion_validation, dict)
        and completion_validation.get("valid") is True
        and completion_validation.get("errors") in ([], None)
    ):
        errors.append("completion_evidence_declared_validation_invalid")
    terminal, completion_errors = _validate_completion(
        payload.get("completion_evidence"),
        query=str(route.get("query") or ""),
        reported_total=reported_total,
        reported_pages=expected_pages,
    )
    errors.extend(completion_errors)

    messages = payload.get("messages")
    if not isinstance(messages, list) or any(not isinstance(row, dict) for row in messages):
        errors.append("messages_not_object_array")
        messages = []
    if len(messages) != reported_total:
        errors.append("messages_length_mismatch")
    message_ids = [str(row.get("message_id") or "") for row in messages]
    if any(not SNOWFLAKE_RE.fullmatch(message_id) for message_id in message_ids):
        errors.append("message_id_invalid")
    if len(set(message_ids)) != len(message_ids):
        errors.append("duplicate_message_id_within_route")
    if [row.get("result_index") for row in messages] != list(
        range(1, len(messages) + 1)
    ):
        errors.append("result_indices_not_contiguous")

    evidence_map = payload.get("forum_group_header_navigation_exact")
    if not isinstance(evidence_map, dict):
        errors.append("forum_navigation_evidence_map_not_object")
        evidence_map = {}
    group_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    page_row_ids: dict[int, set[str]] = defaultdict(set)
    child_ids: set[str] = set()
    row_child_container_ids: dict[str, str] = {}
    row_membership_arrays: list[list[Any]] = []
    navigation_unresolved_count = 0
    thread_conflict_count = 0
    forbidden_selected_source_count = 0
    for row_index, message in enumerate(messages, start=1):
        prefix = f"message_{row_index}"
        message_id = message_ids[row_index - 1]
        page_number = message.get("page_number")
        expected_page = ((row_index - 1) // 25) + 1
        if page_number != expected_page:
            errors.append(f"{prefix}_page_number_mismatch")
        if message.get("result_set_size") != reported_total:
            errors.append(f"{prefix}_result_set_size_mismatch")
        if message.get("search_query") != route.get("query"):
            errors.append(f"{prefix}_search_query_mismatch")
        if _snowflake_local_date(message_id) != route.get("start"):
            errors.append(f"{prefix}_snowflake_local_date_outside_route")
        required_scope = {
            "collection_channel_id": PREMIUM_ID,
            "collection_channel_name": PREMIUM_NAME,
            "collection_channel_kind": "forum channel",
            "collection_category_name": PREMIUM_CATEGORY,
            "collection_channel_id_source": "inventory_exact_href",
            "content_scope_exact": True,
            "exact_parent_forum_conflict_detected": False,
            "exact_permalink_conflict_detected": False,
        }
        if any(message.get(key) != expected for key, expected in required_scope.items()):
            errors.append(f"{prefix}_exact_scope_fields_mismatch")

        raw_membership = message.get("forum_group_message_ids")
        if isinstance(raw_membership, list):
            row_membership_arrays.append(raw_membership)
        membership = _normalized_ids(raw_membership)
        if not (
            message.get("forum_group_membership_exact") is True
            and membership
            and message_id in membership
        ):
            errors.append(f"{prefix}_forum_group_membership_not_exact")
            membership = []
        expected_key = forum_group_evidence_key(
            str(route.get("query") or ""), expected_page, membership
        )
        key = str(message.get("forum_group_membership_key") or "")
        if not expected_key or key != expected_key:
            errors.append(f"{prefix}_forum_group_membership_key_mismatch")
        if key:
            group_rows[key].append(message)
        page_row_ids[expected_page].add(message_id)

        evidence = evidence_map.get(key) if key else None
        row_evidence = message.get("forum_group_navigation_evidence")
        validation = message.get("forum_group_navigation_validation")
        validation = validation if isinstance(validation, dict) else {}
        if message.get("forum_group_navigation_evidence_key") != key:
            errors.append(f"{prefix}_navigation_evidence_key_mismatch")
        if not isinstance(evidence, dict) or row_evidence != evidence:
            errors.append(f"{prefix}_navigation_evidence_map_binding_mismatch")
            navigation_unresolved_count += 1
            evidence = evidence if isinstance(evidence, dict) else {}
        page_plan_summary = payload.get("forum_group_navigation_page_plans")
        page_plan_summary = (
            page_plan_summary if isinstance(page_plan_summary, dict) else {}
        )
        expected_page_plan_hash = str(
            (
                page_plan_summary.get(str(expected_page))
                if isinstance(page_plan_summary.get(str(expected_page)), dict)
                else {}
            ).get("page_membership_sha256")
            or ""
        )
        evidence_type = str(evidence.get("evidence_type") or "")
        child_id = ""
        if evidence_type == "forum_group_header_navigation_exact":
            destination = _exact_thread_destination(evidence.get("destination_url"))
            child_id = destination[1] if destination else ""
            expected_evidence = {
                "schema_version": FORUM_NAVIGATION_CONTRACT_VERSION,
                "evidence_type": "forum_group_header_navigation_exact",
                "evidence_key": key,
                "guild_id": GUILD_ID,
                "parent_forum_channel_id": PREMIUM_ID,
                "query": route.get("query"),
                "page_number": expected_page,
                "group_message_ids": membership,
                "navigation_trigger": "unique_direct_child_role_button_click",
                "header_match_count": 1,
                "header_button_match_count": 1,
                "source_parent_forum_channel_id": PREMIUM_ID,
                "source_parent_forum_verified": True,
                "destination_guild_id": GUILD_ID,
                "thread_channel_id": child_id,
                "destination_verified": True,
                "back_parent_forum_verified": True,
                "return_state_verified": True,
                "authenticated": True,
                "source_scope": "discord_only",
                "outside_sources_used": False,
            }
            source_url = str(evidence.get("source_url") or "")
            back_url = str(evidence.get("back_url") or "")
            source_destination = _exact_guild_navigation_url(source_url)
            restored_membership_hash = forum_group_membership_sha256(
                str(route.get("query") or ""), expected_page, membership
            )
            expected_evidence.update(
                {
                    "back_url": source_url,
                    "source_url_restored": True,
                    "restored_query": route.get("query"),
                    "restored_page_number": expected_page,
                    "restored_group_message_ids": membership,
                    "restored_group_membership_sha256": restored_membership_hash,
                    "pre_navigation_page_membership_sha256": expected_page_plan_hash,
                    "restored_page_membership_sha256": expected_page_plan_hash,
                    "page_plan_verified": True,
                }
            )
            if not (
                source_destination
                and source_destination[0] == GUILD_ID
                and source_destination[1] == PREMIUM_ID
                and source_destination[2] is None
                and back_url == source_url
                and source_url != str(evidence.get("destination_url") or "")
            ):
                errors.append(
                    f"{prefix}_navigation_source_url_not_exactly_restored"
                )
            if (
                not destination
                or destination[0] != GUILD_ID
                or child_id == PREMIUM_ID
            ):
                errors.append(f"{prefix}_navigation_destination_invalid")
            if any(
                evidence.get(field) != value
                for field, value in expected_evidence.items()
            ):
                errors.append(f"{prefix}_navigation_evidence_fields_mismatch")
            if not _is_iso_timestamp(evidence.get("observed_at_utc")):
                errors.append(f"{prefix}_navigation_observed_at_invalid")
        elif evidence_type == OWNED_REPLY_ANCHOR_EVIDENCE_TYPE:
            page_plan_path = (
                checkpoint_directory
                / f"page_{expected_page:03d}"
                / "page_plan.json"
                if checkpoint_directory is not None
                else None
            )
            page_plan_sha256 = (
                sha256_file(page_plan_path)
                if page_plan_path is not None and page_plan_path.is_file()
                else ""
            )
            page_plan_bytes = (
                page_plan_path.stat().st_size
                if page_plan_path is not None and page_plan_path.is_file()
                else 0
            )
            complete_group = [
                row
                for row in messages
                if str(row.get("forum_group_membership_key") or "") == key
            ]
            derived_child_id, reply_anchor_errors = (
                validate_owned_reply_anchor_evidence(
                    evidence,
                    complete_group,
                    query=str(route.get("query") or ""),
                    page_number=expected_page,
                    group_message_ids=membership,
                    page_membership_sha256=expected_page_plan_hash,
                    page_plan_sha256=page_plan_sha256,
                    page_plan_bytes=page_plan_bytes,
                )
            )
            child_id = str(derived_child_id or "")
            errors.extend(
                f"{prefix}_{reason}" for reason in reply_anchor_errors
            )
            if reply_anchor_errors:
                navigation_unresolved_count += 1
        else:
            errors.append(f"{prefix}_navigation_evidence_type_untrusted")
            navigation_unresolved_count += 1
        expected_validation = {
            "valid": True,
            "errors": [],
            "evidence_key": key,
            "thread_channel_id": child_id,
        }
        if any(validation.get(field) != value for field, value in expected_validation.items()):
            errors.append(f"{prefix}_navigation_validation_mismatch")
            navigation_unresolved_count += 1
        source = str(message.get("thread_channel_id_source") or "")
        if source not in TRUSTED_THREAD_SOURCES:
            errors.append(f"{prefix}_thread_source_not_row_owned_exact")
            forbidden_selected_source_count += 1
        if (
            evidence_type == OWNED_REPLY_ANCHOR_EVIDENCE_TYPE
            and source != OWNED_REPLY_ANCHOR_EVIDENCE_TYPE
        ):
            errors.append(f"{prefix}_reply_anchor_thread_source_mismatch")
        if evidence_type == OWNED_REPLY_ANCHOR_EVIDENCE_TYPE and message.get(
            "thread_channel_id_candidates"
        ) != [
            {
                "channel_id": child_id,
                "source": OWNED_REPLY_ANCHOR_EVIDENCE_TYPE,
            }
        ]:
            errors.append(f"{prefix}_reply_anchor_thread_candidates_mismatch")
        if message.get("thread_channel_id_exact") is not True:
            errors.append(f"{prefix}_thread_channel_id_not_exact")
        if message.get("thread_channel_id_conflict") is not False:
            errors.append(f"{prefix}_thread_channel_id_conflict")
            thread_conflict_count += 1
        if str(message.get("inferred_thread_channel_id") or "") != child_id:
            errors.append(f"{prefix}_thread_channel_id_mismatch")
        group_header_id = str(message.get("group_header_data_list_item_id") or "")
        if source == "forum_group_header_data_list_item_id" and group_header_id != (
            f"forum-channel-list-{PREMIUM_ID}___{child_id}"
        ):
            errors.append(f"{prefix}_forum_header_data_id_mismatch")
        if group_header_id and group_header_id != (
            f"forum-channel-list-{PREMIUM_ID}___{child_id}"
        ):
            errors.append(f"{prefix}_forum_header_data_id_conflict")
        if message.get("group_header_parent_forum_channel_id") not in (None, PREMIUM_ID):
            errors.append(f"{prefix}_forum_header_parent_mismatch")
        expected_permalink = f"https://discord.com/channels/{GUILD_ID}/{child_id}/{message_id}"
        if message.get("exact_permalink") != expected_permalink:
            errors.append(f"{prefix}_exact_permalink_mismatch")
        if message.get("exact_permalink_status") != TRUSTED_THREAD_SOURCES.get(source):
            errors.append(f"{prefix}_exact_permalink_status_mismatch")
        if child_id:
            child_ids.add(child_id)
            row_child_container_ids[message_id] = child_id

    if reported_total == 0:
        if evidence_map != {}:
            errors.append("stable_empty_navigation_evidence_map_nonempty")
    else:
        if set(evidence_map) != set(group_rows):
            errors.append("navigation_evidence_map_key_set_mismatch")
        if len({id(value) for value in row_membership_arrays}) != len(messages):
            errors.append("forum_group_membership_arrays_not_independent_per_row")
        for key, rows in group_rows.items():
            representative = rows[0]
            membership = _normalized_ids(representative.get("forum_group_message_ids")) or []
            page_number = int(representative.get("page_number") or 0)
            observed_ids = {str(row.get("message_id") or "") for row in rows}
            if observed_ids != set(membership):
                errors.append(f"group_{key}_row_membership_incomplete")
            for row in rows:
                if _normalized_ids(row.get("forum_group_message_ids")) != membership:
                    errors.append(f"group_{key}_member_arrays_disagree")
            if not set(membership) <= page_row_ids.get(page_number, set()):
                errors.append(f"group_{key}_membership_crosses_page_or_route")
        for page_number, ids in page_row_ids.items():
            group_union: set[str] = set()
            membership_occurrences: Counter[str] = Counter()
            for rows in group_rows.values():
                if int(rows[0].get("page_number") or 0) != page_number:
                    continue
                membership = _normalized_ids(rows[0].get("forum_group_message_ids")) or []
                group_union.update(membership)
                membership_occurrences.update(membership)
            if group_union != ids or any(count != 1 for count in membership_occurrences.values()):
                errors.append(f"page_{page_number}_forum_groups_not_exact_partition")

    navigation_artifact_audit, navigation_source_files, navigation_artifact_errors = (
        _audit_forum_navigation_artifacts(
            checkpoint_directory=checkpoint_directory,
            declared_checkpoint_directory=declared_checkpoint_directory,
            artifact_root=artifact_root,
            payload=payload,
            route=route,
            reported_total=reported_total,
            reported_pages=expected_pages,
            messages=messages,
            group_rows=group_rows,
            evidence_map=evidence_map,
        )
    )
    errors.extend(navigation_artifact_errors)

    try:
        timestamp_bundle = (
            timestamp_scope_revalidation.load_adjacent_timestamp_scope_revalidation(
                path,
                payload,
                source_artifact_sha256=observed_sha256,
                artifact_root=artifact_root,
            )
        )
        timestamp_audit = timestamp_scope_revalidation.audit_segment_timestamp_scopes(
            messages, timestamp_bundle
        )
    except Exception as exc:
        timestamp_bundle = None
        timestamp_audit = {
            "passed": False,
            "error": f"{type(exc).__name__}:{exc}",
        }
    if timestamp_audit.get("passed") is not True:
        errors.append("timestamp_scope_semantic_audit_failed")
    reply_audit, reply_errors = _reply_semantic_audit(messages)
    errors.extend(reply_errors)
    attachment_audit, attachment_owners, attachment_errors = (
        _attachment_semantic_audit(messages)
    )
    errors.extend(attachment_errors)

    if errors:
        raise PremiumJournalsContractError(
            f"Existing Premium Journals canonical is not accepted: {relative}: "
            + "; ".join(sorted(set(errors)))
        )

    ordered_message_ids = sorted(message_ids, key=int)
    ordered_child_ids = sorted(child_ids, key=int)
    bound_source_files = [
        {
            "role": "canonical_segment",
            "path": relative,
            "sha256": observed_sha256,
            "bytes": path.stat().st_size,
        }
    ]
    bound_source_files.extend(navigation_source_files)
    if timestamp_bundle is not None:
        for source in timestamp_bundle.source_artifacts():
            source_path = Path(source["path"])
            bound_source_files.append(
                {
                    "role": str(source.get("kind") or "timestamp_scope_evidence"),
                    "path": _relative(source_path, artifact_root),
                    "sha256": sha256_file(source_path),
                    "bytes": source_path.stat().st_size,
                }
            )
    bound_source_files.sort(key=lambda row: (row["path"], row["role"]))
    artifact = {
        "path": relative,
        "sha256": observed_sha256,
        "bytes": path.stat().st_size,
        "collector_version": COLLECTOR_VERSION,
        "reported_total": reported_total,
        "captured_rows": reported_total,
        "reported_pages": reported_pages,
        "completion_terminal_state": terminal,
        "message_id_set_sha256": sha256_json(ordered_message_ids),
        "observed_child_thread_count": len(ordered_child_ids),
        "observed_child_thread_ids": ordered_child_ids,
        "observed_child_thread_id_set_sha256": sha256_json(ordered_child_ids),
        "forum_group_count": len(group_rows),
        "forum_navigation_evidence_map_sha256": sha256_json(evidence_map),
        "forum_membership_integrity": {
            "passed": True,
            "row_count": len(messages),
            "independent_row_membership_array_count": len(row_membership_arrays),
            "group_count": len(group_rows),
            "page_count": reported_pages,
            "double_sample_runtime_contract": (
                "collector_v2.6_extractPageValidated_two_stable_pre_navigation_samples"
            ),
        },
        "forum_navigation_artifact_integrity": navigation_artifact_audit,
        "timestamp_scope_integrity": timestamp_audit,
        "reply_provenance_integrity": reply_audit,
        "attachment_provenance_integrity": attachment_audit,
        "forum_navigation_unresolved_count": navigation_unresolved_count,
        "thread_channel_id_conflict_count": thread_conflict_count,
        "forbidden_selected_thread_source_count": forbidden_selected_source_count,
        "full_qa_passed": True,
        "hash_binding_policy": (
            "sha256_of_exact_canonical_navigation_artifact_bytes_and_message_id_set"
        ),
        "source_file_set_sha256": sha256_json(bound_source_files),
        "source_files": bound_source_files,
    }
    return {
        "accepted_artifact": artifact,
        "message_ids": ordered_message_ids,
        "child_thread_ids": ordered_child_ids,
        "row_child_container_ids": dict(
            sorted(row_child_container_ids.items(), key=lambda item: int(item[0]))
        ),
        "owned_attachment_owners": {
            attachment_id: sorted(owners, key=int)
            for attachment_id, owners in attachment_owners.items()
        },
        "terminal_valid": True,
        "unresolved_count": 0,
        "conflict_count": 0,
    }


def validate_premium_row_container_bindings(
    path: Path,
    *,
    artifact_root: Path,
    source_artifact_sha256: str | None = None,
) -> dict[str, Any]:
    """Return row->child IDs only after the whole byte-bound canonical passes.

    Callers must never promote ``inferred_thread_channel_id`` directly.  This
    predicate first validates the canonical, every immutable page plan and group
    checkpoint, and all row membership/navigation/permalink contracts.  The
    returned mapping is therefore safe for generic container-ID consumers.
    """

    payload = _read_json_object(path.resolve())
    segment = payload.get("segment")
    segment = segment if isinstance(segment, dict) else {}
    route = {
        "start": segment.get("start"),
        "end": segment.get("end"),
        "query": segment.get("query"),
        "expected_canonical_path": expected_canonical_relative_path(
            str(segment.get("start") or ""), str(segment.get("end") or "")
        ),
    }
    audit = audit_premium_canonical(path, route, artifact_root=artifact_root)
    accepted = audit["accepted_artifact"]
    if source_artifact_sha256 is not None and accepted.get("sha256") != str(
        source_artifact_sha256
    ).lower():
        raise PremiumJournalsContractError(
            "Premium canonical changed between the caller's stable read and "
            "row-container validation"
        )
    mapping = audit.get("row_child_container_ids")
    if not isinstance(mapping, dict) or len(mapping) != accepted.get("reported_total"):
        raise PremiumJournalsContractError(
            "Premium row-container mapping is incomplete after canonical acceptance"
        )
    return audit


def validate_authoritative_directory(
    root: Path,
    routes: Sequence[dict[str, Any]],
) -> list[str]:
    """Return discovery errors for extra canonicals, partials, or sidecars."""

    directory = root / AUTHORITATIVE_DIRECTORY
    if not directory.exists():
        return []
    scheduled = {
        str(route.get("expected_canonical_path") or ""): route for route in routes
    }
    allowed_sidecars = {
        Path(relative)
        .with_name(Path(relative).stem + TIMESTAMP_SIDECAR_SUFFIX)
        .as_posix()
        for relative in scheduled
    }
    errors: list[str] = []
    seen_route_keys: Counter[tuple[str, str]] = Counter()
    for path in sorted(directory.rglob("*.json")):
        if not path.is_file():
            continue
        relative = _relative(path, root)
        if relative in allowed_sidecars:
            source_name = path.name[: -len(TIMESTAMP_SIDECAR_SUFFIX)] + ".json"
            source = path.with_name(source_name)
            source_relative = _relative(source, root)
            if not source.is_file() or source_relative not in scheduled:
                errors.append(f"unbound_or_unplanned_timestamp_sidecar:{relative}")
            continue
        if relative not in scheduled:
            errors.append(f"unplanned_premium_v2_5_artifact:{relative}")
            continue
        try:
            payload = _read_json_object(path)
        except PremiumJournalsContractError as exc:
            errors.append(str(exc))
            continue
        segment = payload.get("segment")
        segment = segment if isinstance(segment, dict) else {}
        key = (str(segment.get("start") or ""), str(segment.get("end") or ""))
        seen_route_keys[key] += 1
    for key, count in seen_route_keys.items():
        if count > 1:
            errors.append(f"multiple_premium_v2_5_canonicals_for_route:{key}:{count}")
    return sorted(set(errors))


def _duplicate_values(values: Iterable[str]) -> list[str]:
    counts = Counter(values)
    return sorted((value for value, count in counts.items() if count > 1), key=int)


def derive_premium_summary(
    routes: Sequence[dict[str, Any]],
    accepted_audits: Sequence[dict[str, Any]],
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    """Derive dynamic parent totals, child reconciliation, and closure state."""

    baseline_ids = reconciliation.get("exact_known_union_thread_ids")
    if not isinstance(baseline_ids, list):
        baseline_ids = []
    baseline_ids = sorted(
        {str(value) for value in baseline_ids if SNOWFLAKE_RE.fullmatch(str(value))},
        key=int,
    )
    counts = reconciliation.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    if len(baseline_ids) != 158 or counts.get("exact_known_union_thread_ids") != 158:
        raise PremiumJournalsContractError(
            "Premium reconciliation no longer binds the exact 158-ID lower bound"
        )
    message_ids = [
        message_id for audit in accepted_audits for message_id in audit["message_ids"]
    ]
    duplicate_message_ids = _duplicate_values(message_ids)
    observed_child_ids = sorted(
        {
            child_id
            for audit in accepted_audits
            for child_id in audit["child_thread_ids"]
        },
        key=int,
    )
    baseline_set = set(baseline_ids)
    observed_set = set(observed_child_ids)
    reconciled_ids = sorted(baseline_set | observed_set, key=int)
    new_observed_ids = sorted(observed_set - baseline_set, key=int)
    baseline_not_observed = sorted(baseline_set - observed_set, key=int)
    attachment_owners: dict[str, set[str]] = defaultdict(set)
    for audit in accepted_audits:
        for attachment_id, owners in audit["owned_attachment_owners"].items():
            attachment_owners[attachment_id].update(owners)
    attachment_owner_conflicts = sorted(
        attachment_id
        for attachment_id, owners in attachment_owners.items()
        if len(owners) > 1
    )
    accepted_count = len(accepted_audits)
    pending_count = len(routes) - accepted_count
    accepted_total = sum(
        int(audit["accepted_artifact"]["reported_total"])
        for audit in accepted_audits
    )
    terminal_counts = Counter(
        str(audit["accepted_artifact"]["completion_terminal_state"])
        for audit in accepted_audits
    )
    route_binding_rows = [
        {
            "path": audit["accepted_artifact"]["path"],
            "sha256": audit["accepted_artifact"]["sha256"],
            "message_id_set_sha256": audit["accepted_artifact"][
                "message_id_set_sha256"
            ],
            "reported_total": audit["accepted_artifact"]["reported_total"],
            "terminal_state": audit["accepted_artifact"][
                "completion_terminal_state"
            ],
        }
        for audit in accepted_audits
    ]
    zero_unresolved = all(audit.get("unresolved_count") == 0 for audit in accepted_audits)
    zero_conflicts = all(audit.get("conflict_count") == 0 for audit in accepted_audits)
    enumeration_complete = accepted_count == len(routes) == 201 and pending_count == 0
    union_terminal_passed = bool(
        enumeration_complete
        and not duplicate_message_ids
        and not attachment_owner_conflicts
        and zero_unresolved
        and zero_conflicts
        and sum(terminal_counts.values()) == 201
    )
    observed_union_reconciled = set(observed_child_ids) <= set(reconciled_ids)
    closure_proven = bool(
        union_terminal_passed and observed_union_reconciled
    )
    census = {
        "status": (
            "message_data_closed_inventory_lower_bound"
            if closure_proven
            else "unresolved_lower_bound"
        ),
        "exact_known_thread_id_lower_bound": 158,
        "inventory_complete": False,
        "enumeration_complete": enumeration_complete,
        "closure_proven": closure_proven,
        "closure_kind": "message_data_scope_closure",
        "obsolete_156_thread_closure_claim_inherited": False,
        "reconciliation_path": (
            "working/premium_journals_scoped_inventory_reconciliation.json"
        ),
        "baseline_reconciliation": {
            "status": reconciliation.get("status"),
            "closure_proven": reconciliation.get("closure_proven"),
            "exact_known_thread_id_count": len(baseline_ids),
            "exact_known_thread_id_set_sha256": sha256_json(baseline_ids),
        },
        "observed_message_bearing_child_thread_count": len(observed_child_ids),
        "observed_message_bearing_child_thread_ids": observed_child_ids,
        "observed_child_thread_id_set_sha256": sha256_json(observed_child_ids),
        "observed_baseline_intersection_count": len(observed_set & baseline_set),
        "new_exact_observed_child_thread_count": len(new_observed_ids),
        "new_exact_observed_child_thread_ids": new_observed_ids,
        "baseline_thread_not_observed_in_window_count": len(baseline_not_observed),
        "reconciled_exact_child_union_count": len(reconciled_ids),
        "reconciled_exact_child_union_ids": reconciled_ids,
        "reconciled_exact_child_union_sha256": sha256_json(reconciled_ids),
        "observed_child_union_reconciled": observed_union_reconciled,
        "full_window_union_terminal_evidence": {
            "passed": union_terminal_passed,
            "required_daily_route_count": 201,
            "accepted_daily_route_count": accepted_count,
            "pending_daily_route_count": pending_count,
            "accepted_reported_total": accepted_total,
            "unique_message_id_count": len(set(message_ids)),
            "cross_route_duplicate_message_id_count": len(duplicate_message_ids),
            "cross_route_duplicate_message_ids": duplicate_message_ids,
            "terminal_state_counts": dict(sorted(terminal_counts.items())),
            "unresolved_occurrence_count": sum(
                int(audit.get("unresolved_count") or 0) for audit in accepted_audits
            ),
            "conflict_occurrence_count": sum(
                int(audit.get("conflict_count") or 0) for audit in accepted_audits
            ),
            "cross_route_attachment_owner_conflict_count": len(
                attachment_owner_conflicts
            ),
            "message_id_set_sha256": sha256_json(sorted(set(message_ids), key=int)),
            "accepted_route_binding_set_sha256": sha256_json(route_binding_rows),
        },
        "outside_message_bearing_scope": (
            "Zero-message, inaccessible, out-of-window, or otherwise undiscoverable "
            "threads outside the Jan 1-Jul 20 authenticated parent-forum search result "
            "sets do not count as missing message data; they remain documented by the "
            "unresolved inventory lower bound and are not silently declared enumerated."
        ),
    }
    return {
        "accepted_route_count": accepted_count,
        "pending_route_count": pending_count,
        "accepted_reported_total": accepted_total,
        "premium_thread_census": census,
    }
