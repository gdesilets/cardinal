"""Fail-closed QA for explicitly scheduled Premium Journals v2.7 shards."""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import premium_journals_provenance_contract as v26
import premium_journals_provenance_contract_v2_7 as v27
import premium_journals_v2_7_schedule as schedule


DIRECT_METHOD = "direct_consensus_v2_7"
HEADER_METHOD = "header_navigation_v2_6"
RECORD_FIELDS = {
    "method", "evidence_key", "page_number", "thread_channel_id",
    "current_source_url", "page_plan_path", "page_membership_sha256",
    "page_plan_sha256", "page_plan_bytes", "checkpoint_path",
    "checkpoint_sha256", "checkpoint_bytes", "evidence",
}


def _json(path: Path, errors: list[str], label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"{label}_not_json:{type(exc).__name__}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label}_not_object")
        return {}
    return value


def _relative_file(root: Path, value: Any, errors: list[str], label: str) -> Path | None:
    text = str(value or "").strip().replace("\\", "/")
    if not text or Path(text).is_absolute():
        errors.append(f"{label}_not_relative")
        return None
    path = (root / text).resolve()
    try:
        normalized = path.relative_to(root.resolve()).as_posix()
    except ValueError:
        errors.append(f"{label}_outside_root")
        return None
    if normalized != text:
        errors.append(f"{label}_not_normalized")
    return path


def _expected_page(messages: Sequence[dict[str, Any]], groups: dict[str, list[dict[str, Any]]], page: int) -> tuple[dict[str, Any], list[str]]:
    rows = sorted((row for row in messages if row.get("page_number") == page), key=lambda row: int(row.get("result_index") or 0))
    group_rows: list[tuple[int, str, list[str]]] = []
    for key, values in groups.items():
        if values and values[0].get("page_number") == page:
            membership = v26._normalized_ids(values[0].get("forum_group_message_ids")) or []
            group_rows.append((min(int(row.get("result_index") or 0) for row in values), key, membership))
    group_rows.sort(key=lambda item: (item[0], item[1]))
    return {
        "groups": [{"message_ids": membership, "direct_header_button_count": 1} for _, _, membership in group_rows],
        "rows": [{"message_id": str(row.get("message_id") or ""), "result_index": int(row.get("result_index") or 0)} for row in rows],
    }, sorted(key for _, key, _ in group_rows)


def _header_evidence_errors(evidence: Any, group: Sequence[dict[str, Any]], *, query: str, page: int, membership: list[str], page_hash: str, current_source: str) -> tuple[str, list[str]]:
    errors: list[str] = []
    if not isinstance(evidence, dict):
        return "", ["header_evidence_not_object"]
    key = v26.forum_group_evidence_key(query, page, membership)
    destination = v26._exact_thread_destination(evidence.get("destination_url"))
    child = destination[1] if destination else ""
    source = v26._exact_guild_navigation_url(evidence.get("source_url"))
    back = v26._exact_guild_navigation_url(evidence.get("back_url"))
    expected = {
        "schema_version": v26.FORUM_NAVIGATION_CONTRACT_VERSION,
        "evidence_type": "forum_group_header_navigation_exact", "evidence_key": key,
        "guild_id": v26.GUILD_ID, "parent_forum_channel_id": v26.PREMIUM_ID,
        "query": query, "page_number": page, "group_message_ids": membership,
        "navigation_trigger": "unique_direct_child_role_button_click",
        "header_match_count": 1, "header_button_match_count": 1,
        "source_url": current_source, "source_parent_forum_channel_id": v26.PREMIUM_ID,
        "source_parent_forum_verified": True,
        "destination_url": f"https://discord.com/channels/{v26.GUILD_ID}/{child}",
        "destination_guild_id": v26.GUILD_ID, "thread_channel_id": child,
        "destination_verified": True, "back_url": current_source,
        "back_parent_forum_verified": True, "source_url_restored": True,
        "restored_query": query, "restored_page_number": page,
        "restored_group_message_ids": membership,
        "restored_group_membership_sha256": v26.forum_group_membership_sha256(query, page, membership),
        "pre_navigation_page_membership_sha256": page_hash,
        "restored_page_membership_sha256": page_hash, "page_plan_verified": True,
        "return_state_verified": True, "authenticated": True,
        "source_scope": "discord_only", "outside_sources_used": False,
    }
    if set(evidence) != set(expected) | {"observed_at_utc"}:
        errors.append("header_evidence_field_set_mismatch")
    if any(evidence.get(field) != value for field, value in expected.items()):
        errors.append("header_evidence_binding_mismatch")
    if not v26._is_iso_timestamp(evidence.get("observed_at_utc")):
        errors.append("header_evidence_timestamp_invalid")
    if source != (v26.GUILD_ID, v26.PREMIUM_ID, None) or back != source or not destination or destination[0] != v26.GUILD_ID or child == v26.PREMIUM_ID:
        errors.append("header_source_back_or_destination_invalid")
    return child, sorted(set(errors))


def _checkpoint_errors(checkpoint: dict[str, Any], evidence: dict[str, Any], *, method: str, key: str, query: str, page: int, membership: list[str], page_hash: str, group: Sequence[dict[str, Any]], plan_sha: str, plan_bytes: int, current_source: str) -> tuple[str, list[str]]:
    if method == DIRECT_METHOD:
        child, errors = v27.validate_evidence(
            evidence, group, query=query, page_number=page,
            group_message_ids=membership, page_membership_sha256=page_hash,
            page_plan_sha256=plan_sha, page_plan_bytes=plan_bytes,
            current_source_url=current_source,
        )
        expected = v27.build_checkpoint(evidence, str(checkpoint.get("checkpointed_at_utc") or ""))
        if checkpoint != expected:
            errors.append("direct_checkpoint_binding_mismatch")
        return str(child or ""), sorted(set(errors))
    child, errors = _header_evidence_errors(
        evidence, group, query=query, page=page, membership=membership,
        page_hash=page_hash, current_source=current_source,
    )
    expected = {
        "schema_version": v26.FORUM_CHECKPOINT_SCHEMA_VERSION,
        "artifact_type": "discord_forum_group_navigation_checkpoint",
        "evidence_key": key, "query": query, "page_number": page,
        "group_message_ids": membership, "source_url": current_source,
        "destination_url": evidence.get("destination_url"), "thread_channel_id": child,
        "back_url": current_source,
        "restored_group_membership_sha256": evidence.get("restored_group_membership_sha256"),
        "pre_navigation_page_membership_sha256": page_hash,
        "restored_page_membership_sha256": page_hash, "immutable": True,
        "evidence": evidence,
    }
    if set(checkpoint) != set(expected) | {"checkpointed_at_utc"}:
        errors.append("header_checkpoint_field_set_mismatch")
    if any(checkpoint.get(field) != value for field, value in expected.items()):
        errors.append("header_checkpoint_binding_mismatch")
    if not v26._is_iso_timestamp(checkpoint.get("checkpointed_at_utc")):
        errors.append("header_checkpoint_timestamp_invalid")
    return child, sorted(set(errors))


def validate_one_segment(path: Path, route: dict[str, Any], artifact_root: Path) -> list[str]:
    """Re-derive the whole canonical and every source byte; never promote."""
    artifact_root, path = artifact_root.resolve(), path.resolve()
    errors = list(schedule.validate_route(route))
    if errors:
        return sorted(set(errors))
    expected_path = (artifact_root / route["expected_canonical_path"]).resolve()
    if path != expected_path:
        errors.append("v2_7_canonical_path_mismatch")
    payload = _json(path, errors, "v2_7_canonical")
    for field, expected in {
        "collector_version": v27.COLLECTOR_VERSION,
        "provenance_version": v27.PROVENANCE_VERSION,
        "guild_id": v26.GUILD_ID, "collection_scope": "channel-scoped",
        "complete": True,
    }.items():
        if payload.get(field) != expected:
            errors.append(f"v2_7_{field}_mismatch")
    if payload.get("authenticated") is not True or payload.get("source_scope") != "discord_only" or payload.get("outside_sources_used") is not False:
        errors.append("v2_7_authenticated_discord_only_boundary_invalid")
    if not v26._is_iso_timestamp(payload.get("collection_started_at_utc")) or not v26._is_iso_timestamp(payload.get("captured_at_utc")):
        errors.append("v2_7_collection_timestamps_invalid")
    segment = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
    for field, expected in {"start": route["start"], "end": route["end"], "query": route["query"], "timezone": "America/Chicago"}.items():
        if segment.get(field) != expected:
            errors.append(f"v2_7_segment_{field}_mismatch")
    requested = payload.get("requested_container") if isinstance(payload.get("requested_container"), dict) else {}
    for field, expected in {"channel_id": v26.PREMIUM_ID, "channel_name": v26.PREMIUM_NAME, "channel_kind": "forum channel", "category_name": v26.PREMIUM_CATEGORY, "channel_id_source": "inventory_exact_href"}.items():
        if requested.get(field) != expected:
            errors.append(f"v2_7_requested_{field}_mismatch")
    observed = payload.get("observed_container") if isinstance(payload.get("observed_container"), dict) else {}
    for field, expected in {"channel_id": v26.PREMIUM_ID, "channel_name": v26.PREMIUM_NAME, "channel_kind": "forum channel", "category_name": v26.PREMIUM_CATEGORY, "source_url": f"https://discord.com/channels/{v26.GUILD_ID}/{v26.PREMIUM_ID}"}.items():
        if observed.get(field) != expected:
            errors.append(f"v2_7_observed_{field}_mismatch")

    total = payload.get("reported_total")
    if type(total) is not int or total < 0:
        errors.append("v2_7_reported_total_invalid")
        total = 0
    pages = math.ceil(total / 25)
    for field, expected in {"reported_pages": pages, "pages_captured": pages, "captured_rows": total, "unique_message_ids": total}.items():
        if payload.get(field) != expected:
            errors.append(f"v2_7_{field}_mismatch")
    if payload.get("gap_indices") not in ([], None) or payload.get("container_mismatch_count") != 0 or payload.get("container_mismatch_message_ids") not in ([], None):
        errors.append("v2_7_gap_or_container_mismatch")
    if payload.get("forum_group_navigation_unresolved_count") != 0 or payload.get("forum_group_navigation_unresolved_message_ids") not in ([], None):
        errors.append("v2_7_navigation_unresolved")
    if payload.get("forum_group_navigation_page_acceptance") != "all_groups_exact_before_page_acceptance":
        errors.append("v2_7_page_acceptance_mismatch")
    completion_declared = payload.get("completion_evidence_validation")
    if not isinstance(completion_declared, dict) or completion_declared.get("valid") is not True or completion_declared.get("errors") not in ([], None):
        errors.append("v2_7_completion_declared_invalid")
    _, completion_errors = v26._validate_completion(payload.get("completion_evidence"), query=route["query"], reported_total=total, reported_pages=pages)
    errors.extend(f"v2_7_{item}" for item in completion_errors)

    messages = payload.get("messages")
    if not isinstance(messages, list) or any(not isinstance(row, dict) for row in messages):
        errors.append("v2_7_messages_not_object_array")
        messages = []
    if len(messages) != total:
        errors.append("v2_7_messages_length_mismatch")
    message_ids = [str(row.get("message_id") or "") for row in messages]
    if any(not v26.SNOWFLAKE_RE.fullmatch(value) for value in message_ids) or len(set(message_ids)) != len(message_ids):
        errors.append("v2_7_message_ids_invalid_or_duplicate")
    if [row.get("result_index") for row in messages] != list(range(1, total + 1)):
        errors.append("v2_7_result_indices_not_contiguous")

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    page_ids: dict[int, set[str]] = defaultdict(set)
    for index, row in enumerate(messages, 1):
        page = ((index - 1) // 25) + 1
        message_id = message_ids[index - 1]
        if row.get("page_number") != page or row.get("result_set_size") != total or row.get("search_query") != route["query"]:
            errors.append(f"v2_7_row_{index}_page_total_or_query_mismatch")
        if v26._snowflake_local_date(message_id) != route["start"]:
            errors.append(f"v2_7_row_{index}_outside_local_day")
        required = {"collection_channel_id": v26.PREMIUM_ID, "collection_channel_name": v26.PREMIUM_NAME, "collection_channel_kind": "forum channel", "collection_category_name": v26.PREMIUM_CATEGORY, "collection_channel_id_source": "inventory_exact_href", "content_scope_exact": True, "exact_parent_forum_conflict_detected": False, "exact_permalink_conflict_detected": False}
        if any(row.get(field) != expected for field, expected in required.items()):
            errors.append(f"v2_7_row_{index}_scope_mismatch")
        membership = v26._normalized_ids(row.get("forum_group_message_ids"))
        key = v26.forum_group_evidence_key(route["query"], page, membership)
        if not membership or message_id not in membership or row.get("forum_group_membership_exact") is not True or row.get("forum_group_membership_key") != key:
            errors.append(f"v2_7_row_{index}_membership_invalid")
        groups[str(key or "")].append(row)
        page_ids[page].add(message_id)

    for key, group in groups.items():
        membership = v26._normalized_ids(group[0].get("forum_group_message_ids")) if group else None
        if not membership or {str(row.get("message_id") or "") for row in group} != set(membership) or any(v26._normalized_ids(row.get("forum_group_message_ids")) != membership for row in group):
            errors.append(f"v2_7_group_{key}_not_exact_membership")
    for page, ids_on_page in page_ids.items():
        counts: Counter[str] = Counter()
        for group in groups.values():
            if group and group[0].get("page_number") == page:
                counts.update(v26._normalized_ids(group[0].get("forum_group_message_ids")) or [])
        if set(counts) != ids_on_page or any(count != 1 for count in counts.values()):
            errors.append(f"v2_7_page_{page}_groups_not_exact_partition")

    direct_map = payload.get("forum_group_direct_consensus_exact")
    header_map = payload.get("forum_group_header_navigation_exact")
    methods = payload.get("forum_group_resolution_methods")
    records = payload.get("forum_group_resolution_records")
    plans = payload.get("forum_group_navigation_page_plans")
    for label, value in (("direct_map", direct_map), ("header_map", header_map), ("methods", methods), ("records", records), ("page_plans", plans)):
        if not isinstance(value, dict):
            errors.append(f"v2_7_{label}_not_object")
    direct_map = direct_map if isinstance(direct_map, dict) else {}
    header_map = header_map if isinstance(header_map, dict) else {}
    methods = methods if isinstance(methods, dict) else {}
    records = records if isinstance(records, dict) else {}
    plans = plans if isinstance(plans, dict) else {}
    group_keys = set(groups)
    if set(methods) != group_keys or set(records) != group_keys or set(direct_map) & set(header_map) or set(direct_map) | set(header_map) != group_keys:
        errors.append("v2_7_evidence_method_record_key_sets_not_exact")
    if any((methods.get(key) == DIRECT_METHOD) != (key in direct_map) or (methods.get(key) == HEADER_METHOD) != (key in header_map) for key in group_keys):
        errors.append("v2_7_method_evidence_map_disagreement")

    checkpoint_root = _relative_file(artifact_root, payload.get("forum_group_navigation_checkpoint_directory"), errors, "v2_7_checkpoint_directory")
    expected_checkpoint_relative = v27.expected_checkpoint_relative_directory(route["start"])
    if str(payload.get("forum_group_navigation_checkpoint_directory") or "").replace("\\", "/") != expected_checkpoint_relative:
        errors.append("v2_7_checkpoint_directory_not_exact_versioned_day_root")
    expected_files: set[Path] = set()
    source_files: list[dict[str, Any]] = []
    page_details: dict[int, tuple[str, str, int, Path]] = {}
    current_source = f"https://discord.com/channels/{v26.GUILD_ID}/{v26.PREMIUM_ID}"
    for page in range(1, pages + 1):
        canonical, keys = _expected_page(messages, groups, page)
        page_hash = v26.forum_page_membership_sha256(route["query"], page, total, canonical) or ""
        plan_path = checkpoint_root / f"page_{page:03d}" / "page_plan.json" if checkpoint_root else None
        if plan_path:
            expected_files.add(plan_path.resolve())
        if not plan_path or not plan_path.is_file():
            errors.append(f"v2_7_page_{page}_plan_missing")
            continue
        plan = _json(plan_path, errors, f"v2_7_page_{page}_plan")
        expected_plan = {"schema_version": v26.FORUM_PAGE_PLAN_SCHEMA_VERSION, "artifact_type": "discord_forum_navigation_page_plan", "query": route["query"], "page_number": page, "reported_total": total, "page_membership_sha256": page_hash, "expected_group_count": len(keys), "expected_message_count": len(canonical["rows"]), "expected_group_evidence_keys": keys, "canonical": canonical, "immutable": True}
        if set(plan) != set(expected_plan) | {"observed_at_utc"} or any(plan.get(field) != expected for field, expected in expected_plan.items()) or not v26._is_iso_timestamp(plan.get("observed_at_utc")):
            errors.append(f"v2_7_page_{page}_plan_binding_invalid")
        plan_sha, plan_bytes = v26.sha256_file(plan_path), plan_path.stat().st_size
        page_details[page] = (page_hash, plan_sha, plan_bytes, plan_path)
        summary = {"page_number": page, "page_membership_sha256": page_hash, "message_count": len(canonical["rows"]), "group_count": len(keys), "group_evidence_keys": keys, "all_rows_exact": True}
        if plans.get(str(page)) != summary:
            errors.append(f"v2_7_page_{page}_summary_mismatch")
        source_files.append({"role": "forum_navigation_page_plan", "path": plan_path.relative_to(artifact_root).as_posix(), "sha256": plan_sha, "bytes": plan_bytes})
    if set(plans) != {str(page) for page in range(1, pages + 1)}:
        errors.append("v2_7_page_plan_summary_key_set_mismatch")

    for key, group in groups.items():
        method = methods.get(key)
        if method not in {DIRECT_METHOD, HEADER_METHOD}:
            errors.append(f"v2_7_group_{key}_method_invalid")
            continue
        page = int(group[0].get("page_number") or 0)
        if page not in page_details:
            errors.append(f"v2_7_group_{key}_page_plan_unavailable")
            continue
        page_hash, plan_sha, plan_bytes, plan_path = page_details[page]
        membership = v26._normalized_ids(group[0].get("forum_group_message_ids")) or []
        evidence = (direct_map if method == DIRECT_METHOD else header_map).get(key)
        filename = v27.checkpoint_filename(key) if method == DIRECT_METHOD else v26.forum_group_navigation_checkpoint_filename(key)
        checkpoint_path = checkpoint_root / f"page_{page:03d}" / str(filename) if checkpoint_root else None
        if checkpoint_path:
            expected_files.add(checkpoint_path.resolve())
        checkpoint = _json(checkpoint_path, errors, f"v2_7_group_{key}_checkpoint") if checkpoint_path and checkpoint_path.is_file() else {}
        if not checkpoint:
            errors.append(f"v2_7_group_{key}_checkpoint_missing")
        child, checkpoint_errors = _checkpoint_errors(checkpoint, evidence if isinstance(evidence, dict) else {}, method=method, key=key, query=route["query"], page=page, membership=membership, page_hash=page_hash, group=group, plan_sha=plan_sha, plan_bytes=plan_bytes, current_source=current_source)
        errors.extend(f"v2_7_group_{key}:{item}" for item in checkpoint_errors)
        record = records.get(key)
        checkpoint_sha = v26.sha256_file(checkpoint_path) if checkpoint_path and checkpoint_path.is_file() else ""
        checkpoint_bytes = checkpoint_path.stat().st_size if checkpoint_path and checkpoint_path.is_file() else 0
        expected_record = {"method": method, "evidence_key": key, "page_number": page, "thread_channel_id": child, "current_source_url": current_source, "page_plan_path": plan_path.relative_to(artifact_root).as_posix(), "page_membership_sha256": page_hash, "page_plan_sha256": plan_sha, "page_plan_bytes": plan_bytes, "checkpoint_path": checkpoint_path.relative_to(artifact_root).as_posix() if checkpoint_path else "", "checkpoint_sha256": checkpoint_sha, "checkpoint_bytes": checkpoint_bytes, "evidence": evidence}
        if not isinstance(record, dict) or set(record) != RECORD_FIELDS or record != expected_record:
            errors.append(f"v2_7_group_{key}_resolution_record_mismatch")
        for row in group:
            expected_validation = {"valid": True, "errors": [], "evidence_key": key, "thread_channel_id": child}
            if row.get("forum_group_navigation_evidence_key") != key or row.get("forum_group_navigation_evidence") != evidence or row.get("forum_group_navigation_validation") != expected_validation:
                errors.append(f"v2_7_group_{key}_row_evidence_binding_mismatch")
            source = str(row.get("thread_channel_id_source") or "")
            allowed = {v27.EVIDENCE_TYPE} if method == DIRECT_METHOD else {"forum_group_header_navigation_exact", "forum_group_header_data_list_item_id"}
            if source not in allowed or row.get("thread_channel_id_exact") is not True or row.get("thread_channel_id_conflict") is not False or str(row.get("inferred_thread_channel_id") or "") != child:
                errors.append(f"v2_7_group_{key}_row_thread_binding_invalid")
            expected_status = "thread_id_from_direct_candidate_consensus" if method == DIRECT_METHOD else v26.TRUSTED_THREAD_SOURCES.get(source)
            message_id = str(row.get("message_id") or "")
            if row.get("exact_permalink") != f"https://discord.com/channels/{v26.GUILD_ID}/{child}/{message_id}" or row.get("exact_permalink_status") != expected_status:
                errors.append(f"v2_7_group_{key}_row_permalink_invalid")
            card = str(row.get("group_header_data_list_item_id") or "")
            if card and card != f"forum-channel-list-{v26.PREMIUM_ID}___{child}":
                errors.append(f"v2_7_group_{key}_row_card_conflict")
        role = "forum_group_direct_consensus_checkpoint" if method == DIRECT_METHOD else "forum_group_header_navigation_checkpoint"
        if checkpoint_path and checkpoint_path.is_file():
            source_files.append({"role": role, "path": checkpoint_path.relative_to(artifact_root).as_posix(), "sha256": checkpoint_sha, "bytes": checkpoint_bytes})

    if payload.get("forum_group_navigation_checkpoint_count") != len(groups):
        errors.append("v2_7_checkpoint_count_mismatch")
    if checkpoint_root and checkpoint_root.exists():
        actual_files = {file.resolve() for file in checkpoint_root.rglob("*") if file.is_file()}
        if actual_files != expected_files:
            errors.append("v2_7_checkpoint_source_file_set_not_exact")
    elif pages:
        errors.append("v2_7_checkpoint_directory_missing")
    source_files.sort(key=lambda item: (item["path"], item["role"]))
    if payload.get("forum_group_resolution_source_files") != source_files or payload.get("forum_group_resolution_source_file_set_sha256") != v26.sha256_json(source_files):
        errors.append("v2_7_declared_source_file_set_mismatch")

    reply_summary, reply_errors = v26._reply_semantic_audit(messages)
    attachment_summary, _, attachment_errors = v26._attachment_semantic_audit(messages)
    if reply_errors or reply_summary.get("passed") is not True:
        errors.append("v2_7_reply_semantic_audit_failed")
    if attachment_errors or attachment_summary.get("passed") is not True:
        errors.append("v2_7_attachment_semantic_audit_failed")
    if payload.get("reply_provenance_integrity") != reply_summary:
        errors.append("v2_7_declared_reply_summary_mismatch")
    if payload.get("attachment_provenance_integrity") != attachment_summary:
        errors.append("v2_7_declared_attachment_summary_mismatch")
    return sorted(set(errors))
