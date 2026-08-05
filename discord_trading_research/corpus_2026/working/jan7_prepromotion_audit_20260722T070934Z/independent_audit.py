from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import math
import pathlib
import re
import sys
from urllib.parse import urlparse


CORPUS_ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE_ROOT = CORPUS_ROOT / (
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-07_20260722T055743Z/"
    "v2_6_revalidated"
)
SOURCE_PATH = SOURCE_ROOT / (
    "channel_premium_journals_1283941772577472643_"
    "2026-01-07_2026-01-07.json"
)
CANONICAL_PATH = CORPUS_ROOT / (
    "raw/channel_segments_v2_5/"
    "channel_premium_journals_1283941772577472643_"
    "2026-01-07_2026-01-07.json"
)
LEGACY_PATH = CORPUS_ROOT / (
    "raw/channel_segments/"
    "channel_premium_journals_1283941772577472643_"
    "2026-01-07_2026-01-07.json"
)
PARTIAL_PATH = CANONICAL_PATH.with_suffix(".partial.json")
LEGACY_PARTIAL_PATH = LEGACY_PATH.with_suffix(".partial.json")

EXPECTED_SHA256 = "19486bee534ac150e76d70cc2f070ba07735c77ded7846e9ad090c026a81cb72"
EXPECTED_BYTES = 2_919_929
GUILD_ID = "1167376964680691732"
PARENT_ID = "1283941772577472643"
QUERY = "in:premium-journals after:2026-01-06 before:2026-01-08"
LOCAL_DAY = "2026-01-07"
SNOWFLAKE_RE = re.compile(r"^[0-9]{15,22}$")
DISCORD_EPOCH_MS = 1420070400000
# Jan. 7, 2026 is unambiguously Central Standard Time (UTC-06:00).
CENTRAL = dt.timezone(dt.timedelta(hours=-6), name="CST")
ATTACHMENT_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}


sys.path.insert(0, str(CORPUS_ROOT))
import premium_journals_provenance_contract as premium  # noqa: E402
import reply_provenance_contract as replies  # noqa: E402
import timestamp_scope_revalidation as timestamps  # noqa: E402


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def tree_manifest(root: pathlib.Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        content = path.read_bytes()
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(content),
                "sha256": sha256_bytes(content),
            }
        )
    encoded = json.dumps(
        rows, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return {
        "file_count": len(rows),
        "total_bytes": sum(int(row["bytes"]) for row in rows),
        "tree_manifest_sha256": sha256_bytes(encoded),
    }


def duplicate_values(values: list[object]) -> list[str]:
    counts = collections.Counter(str(value) for value in values)
    return sorted(value for value, count in counts.items() if count > 1)


def snowflake_local_date(message_id: str) -> str:
    milliseconds = (int(message_id) >> 22) + DISCORD_EPOCH_MS
    instant = dt.datetime.fromtimestamp(milliseconds / 1000, tz=dt.timezone.utc)
    return instant.astimezone(CENTRAL).date().isoformat()


source_before = tree_manifest(SOURCE_ROOT)
canonical_before = CANONICAL_PATH.exists()
legacy_before = LEGACY_PATH.exists()
partial_before = PARTIAL_PATH.exists()
legacy_partial_before = LEGACY_PARTIAL_PATH.exists()
source_bytes = SOURCE_PATH.read_bytes()
payload = json.loads(source_bytes)
messages = payload["messages"]

message_ids = [str(row.get("message_id") or "") for row in messages]
indices = [row.get("result_index") for row in messages]
pages = [row.get("page_number") for row in messages]
permalinks = [str(row.get("exact_permalink") or "") for row in messages]
serialized_rows = [
    json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for row in messages
]

required_scope = {
    "collection_channel_id": PARENT_ID,
    "collection_channel_name": "premium-journals",
    "collection_channel_kind": "forum channel",
    "collection_category_name": "PREMIUM",
    "collection_channel_id_source": "inventory_exact_href",
    "content_scope_exact": True,
    "exact_parent_forum_conflict_detected": False,
    "exact_permalink_conflict_detected": False,
}
scope_mismatch_ids: list[str] = []
permalink_mismatch_ids: list[str] = []
local_day_mismatch_ids: list[str] = []
page_mismatch_ids: list[str] = []
for ordinal, row in enumerate(messages, start=1):
    message_id = str(row.get("message_id") or "")
    if any(row.get(key) != value for key, value in required_scope.items()):
        scope_mismatch_ids.append(message_id)
    child_id = str(row.get("inferred_thread_channel_id") or "")
    expected_permalink = f"https://discord.com/channels/{GUILD_ID}/{child_id}/{message_id}"
    if row.get("exact_permalink") != expected_permalink:
        permalink_mismatch_ids.append(message_id)
    if SNOWFLAKE_RE.fullmatch(message_id) and snowflake_local_date(message_id) != LOCAL_DAY:
        local_day_mismatch_ids.append(message_id)
    if row.get("page_number") != ((ordinal - 1) // 25) + 1:
        page_mismatch_ids.append(message_id)

groups: dict[str, list[dict[str, object]]] = collections.defaultdict(list)
for row in messages:
    groups[str(row.get("forum_group_membership_key") or "")].append(row)

group_errors: list[str] = []
page_group_membership: dict[int, collections.Counter[str]] = collections.defaultdict(
    collections.Counter
)
for key, rows in groups.items():
    first = rows[0]
    membership = sorted(str(value) for value in first.get("forum_group_message_ids") or [])
    page_number = int(first.get("page_number") or 0)
    expected_key = premium.forum_group_evidence_key(QUERY, page_number, membership)
    observed_ids = sorted(str(row.get("message_id") or "") for row in rows)
    if key != expected_key:
        group_errors.append(f"key_mismatch:{key}")
    if observed_ids != membership:
        group_errors.append(f"membership_mismatch:{key}")
    if any(
        sorted(str(value) for value in row.get("forum_group_message_ids") or [])
        != membership
        for row in rows
    ):
        group_errors.append(f"member_array_disagreement:{key}")
    if any(row.get("page_number") != page_number for row in rows):
        group_errors.append(f"cross_page_group:{key}")
    page_group_membership[page_number].update(membership)

for page_number in range(1, 17):
    expected_ids = {
        str(row.get("message_id") or "")
        for row in messages
        if row.get("page_number") == page_number
    }
    observed = page_group_membership[page_number]
    if set(observed) != expected_ids:
        group_errors.append(f"page_partition_set_mismatch:{page_number}")
    if any(count != 1 for count in observed.values()):
        group_errors.append(f"page_partition_multiplicity:{page_number}")

attachment_ids: list[str] = []
attachment_owner_ids: dict[str, set[str]] = collections.defaultdict(set)
attachment_errors: list[str] = []
for row in messages:
    message_id = str(row.get("message_id") or "")
    child_id = str(row.get("inferred_thread_channel_id") or "")
    for attachment in row.get("attachments") or []:
        attachment_id = str(attachment.get("attachment_id") or "")
        attachment_ids.append(attachment_id)
        attachment_owner_ids[attachment_id].add(message_id)
        evidence = attachment.get("ownership_evidence") or {}
        parsed = urlparse(str(attachment.get("url") or ""))
        url_parts = [part for part in parsed.path.split("/") if part]
        exact = (
            SNOWFLAKE_RE.fullmatch(attachment_id) is not None
            and parsed.scheme == "https"
            and parsed.hostname in ATTACHMENT_HOSTS
            and len(url_parts) >= 3
            and url_parts[0] == "attachments"
            and url_parts[1] == child_id
            and url_parts[2] == attachment_id
            and attachment.get("thread_channel_id") == child_id
            and attachment.get("relation_type") == "owned"
            and attachment.get("ownership_status") == "owned_exact"
            and attachment.get("href_in_message_content") is False
            and evidence.get("schema_version") == "1.0.0"
            and evidence.get("exact") is True
            and evidence.get("owner_message_id") == message_id
            and evidence.get("owner_channel_id") == child_id
            and evidence.get("source_channel_id") == child_id
            and evidence.get("dom_relation") == "exact_message_accessories_descendant"
        )
        if not exact:
            attachment_errors.append(f"{message_id}:{attachment_id}")

reply_status_counts = collections.Counter(
    str(row.get("reply_target_resolution_status") or "missing") for row in messages
)
reply_errors: list[str] = []
for row in messages:
    message_id = str(row.get("message_id") or "")
    row_errors = replies.resolution_status_boolean_errors(row)
    target_id = str(row.get("reply_to_message_id") or "")
    status = str(row.get("reply_target_resolution_status") or "")
    if target_id:
        row_errors.extend(replies.exact_reply_target_contract_errors(row, guild_id=GUILD_ID))
        if row.get("reply_to_channel_id") != row.get("inferred_thread_channel_id"):
            row_errors.append("target_channel_not_owner_thread")
    elif status in replies.DOCUMENTED_NO_ID_STATUSES:
        row_errors.extend(replies.documented_no_id_contract_errors(row))
    elif status == "unresolved_without_exact_target_id":
        row_errors.append("unresolved_without_exact_target_id")
    elif status != "not_applicable":
        row_errors.append("unknown_status")
    reply_errors.extend(f"{message_id}:{reason}" for reason in row_errors)

timestamp_bundle = timestamps.load_adjacent_timestamp_scope_revalidation(
    SOURCE_PATH,
    payload,
    source_artifact_sha256=sha256_bytes(source_bytes),
    artifact_root=CORPUS_ROOT,
)
timestamp_audit = timestamps.audit_segment_timestamp_scopes(messages, timestamp_bundle)

conflicts = {
    field: sum(row.get(field) is True for row in messages)
    for field in [
        "author_id_conflict",
        "exact_parent_forum_conflict_detected",
        "exact_permalink_conflict_detected",
        "thread_channel_id_conflict",
        "reply_to_message_id_conflict",
        "reply_to_channel_id_conflict",
    ]
}
unresolved = {
    "declared_forum_navigation": int(
        payload.get("forum_group_navigation_unresolved_count") or 0
    ),
    "invalid_navigation_validation_rows": sum(
        not (
            isinstance(row.get("forum_group_navigation_validation"), dict)
            and row["forum_group_navigation_validation"].get("valid") is True
            and row["forum_group_navigation_validation"].get("errors") == []
        )
        for row in messages
    ),
    "thread_channel_not_exact_rows": sum(
        row.get("thread_channel_id_exact") is not True for row in messages
    ),
    "reply_unresolved_rows": reply_status_counts.get(
        "unresolved_without_exact_target_id", 0
    ),
    "timestamp_unresolved_rows": int(timestamp_audit.get("unresolved_count") or 0),
    "attachment_ownership_error_rows": len(attachment_errors),
}

all_error_counts = {
    "artifact_hash_mismatch": int(sha256_bytes(source_bytes) != EXPECTED_SHA256),
    "artifact_bytes_mismatch": int(len(source_bytes) != EXPECTED_BYTES),
    "collector_version_mismatch": int(payload.get("collector_version") != "2.6"),
    "complete_flag_mismatch": int(payload.get("complete") is not True),
    "query_mismatch": int((payload.get("segment") or {}).get("query") != QUERY),
    "timezone_mismatch": int(
        (payload.get("segment") or {}).get("timezone") != "America/Chicago"
    ),
    "reported_total_mismatch": int(payload.get("reported_total") != 390),
    "reported_pages_mismatch": int(payload.get("reported_pages") != 16),
    "captured_rows_mismatch": int(payload.get("captured_rows") != 390),
    "unique_message_count_mismatch": int(payload.get("unique_message_ids") != 390),
    "gap_indices_not_empty": int(payload.get("gap_indices") != []),
    "container_mismatch_count_nonzero": int(
        payload.get("container_mismatch_count") != 0
    ),
    "message_count_mismatch": int(len(messages) != 390),
    "invalid_message_ids": sum(SNOWFLAKE_RE.fullmatch(value) is None for value in message_ids),
    "duplicate_message_ids": len(duplicate_values(message_ids)),
    "duplicate_result_indices": len(duplicate_values(indices)),
    "duplicate_exact_permalinks": len(duplicate_values(permalinks)),
    "exact_duplicate_rows": len(duplicate_values(serialized_rows)),
    "noncontiguous_result_indices": int(indices != list(range(1, 391))),
    "unexpected_page_distribution": int(
        collections.Counter(pages)
        != collections.Counter({**{page: 25 for page in range(1, 16)}, 16: 15})
    ),
    "page_mismatch_rows": len(page_mismatch_ids),
    "local_day_mismatch_rows": len(local_day_mismatch_ids),
    "scope_mismatch_rows": len(scope_mismatch_ids),
    "permalink_mismatch_rows": len(permalink_mismatch_ids),
    "group_partition_errors": len(group_errors),
    "duplicate_attachment_ids": len(duplicate_values(attachment_ids)),
    "attachment_semantic_errors": len(attachment_errors),
    "attachment_multiple_owned_owner_ids": sum(
        len(owners) > 1 for owners in attachment_owner_ids.values()
    ),
    "reply_semantic_errors": len(reply_errors),
    "timestamp_audit_failed": int(timestamp_audit.get("passed") is not True),
    "conflict_flags": sum(conflicts.values()),
    "unresolved_flags": sum(unresolved.values()),
    "canonical_target_preexists": int(canonical_before),
    "legacy_target_preexists": int(legacy_before),
    "canonical_partial_preexists": int(partial_before),
    "legacy_partial_preexists": int(legacy_partial_before),
}

source_after = tree_manifest(SOURCE_ROOT)
canonical_after = CANONICAL_PATH.exists()
legacy_after = LEGACY_PATH.exists()
partial_after = PARTIAL_PATH.exists()
legacy_partial_after = LEGACY_PARTIAL_PATH.exists()
immutability = {
    "source_tree_before": source_before,
    "source_tree_after": source_after,
    "source_tree_unchanged": source_before == source_after,
    "canonical_exists_before": canonical_before,
    "canonical_exists_after": canonical_after,
    "legacy_exists_before": legacy_before,
    "legacy_exists_after": legacy_after,
    "partial_exists_before": partial_before,
    "partial_exists_after": partial_after,
    "legacy_partial_exists_before": legacy_partial_before,
    "legacy_partial_exists_after": legacy_partial_after,
}

result = {
    "status": "PASS"
    if not any(all_error_counts.values())
    and source_before == source_after
    and canonical_before == canonical_after
    and legacy_before == legacy_after
    and partial_before == partial_after
    and legacy_partial_before == legacy_partial_after
    else "FAIL",
    "artifact": {
        "path": SOURCE_PATH.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_bytes(source_bytes),
        "bytes": len(source_bytes),
        "collector_version": payload.get("collector_version"),
        "complete": payload.get("complete"),
        "reported_total": payload.get("reported_total"),
        "reported_pages": payload.get("reported_pages"),
        "captured_rows": payload.get("captured_rows"),
        "completion_terminal_state": (
            payload.get("completion_evidence") or {}
        ).get("terminal_state"),
    },
    "grain_and_uniqueness": {
        "message_rows": len(messages),
        "unique_message_ids": len(set(message_ids)),
        "unique_result_indices": len(set(indices)),
        "unique_exact_permalinks": len(set(permalinks)),
        "exact_duplicate_row_count": len(duplicate_values(serialized_rows)),
        "page_counts": dict(sorted(collections.Counter(pages).items())),
    },
    "scope_and_navigation": {
        "scope_mismatch_rows": len(scope_mismatch_ids),
        "permalink_mismatch_rows": len(permalink_mismatch_ids),
        "local_day_mismatch_rows": len(local_day_mismatch_ids),
        "forum_group_count": len(groups),
        "forum_evidence_map_count": len(payload.get("forum_group_header_navigation_exact") or {}),
        "evidence_type_counts": dict(
            sorted(
                collections.Counter(
                    str(value.get("evidence_type") or "missing")
                    for value in (payload.get("forum_group_header_navigation_exact") or {}).values()
                ).items()
            )
        ),
        "thread_source_counts": dict(
            sorted(
                collections.Counter(
                    str(row.get("thread_channel_id_source") or "missing")
                    for row in messages
                ).items()
            )
        ),
        "unique_child_thread_ids": len(
            {str(row.get("inferred_thread_channel_id") or "") for row in messages}
        ),
        "group_partition_error_count": len(group_errors),
    },
    "attachments": {
        "occurrence_count": len(attachment_ids),
        "unique_attachment_id_count": len(set(attachment_ids)),
        "duplicate_attachment_id_count": len(duplicate_values(attachment_ids)),
        "semantic_error_count": len(attachment_errors),
        "multiple_owned_message_owner_count": sum(
            len(owners) > 1 for owners in attachment_owner_ids.values()
        ),
    },
    "replies": {
        "status_counts": dict(sorted(reply_status_counts.items())),
        "reply_context_present_count": sum(
            row.get("reply_context_present") is True for row in messages
        ),
        "exact_target_id_count": sum(
            bool(row.get("reply_to_message_id")) for row in messages
        ),
        "documented_no_id_count": sum(
            str(row.get("reply_target_resolution_status") or "")
            in replies.DOCUMENTED_NO_ID_STATUSES
            for row in messages
        ),
        "semantic_error_count": len(reply_errors),
    },
    "timestamps": {
        "passed": timestamp_audit.get("passed"),
        "message_count": timestamp_audit.get("message_count"),
        "mode_counts": timestamp_audit.get("mode_counts"),
        "unresolved_count": timestamp_audit.get("unresolved_count"),
        "sidecar_error_count": timestamp_audit.get("sidecar_error_count"),
        "unused_revalidation_record_count": timestamp_audit.get(
            "unused_revalidation_record_count"
        ),
    },
    "conflicts": conflicts,
    "unresolved": unresolved,
    "error_counts": all_error_counts,
    "immutability": immutability,
}

rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
(pathlib.Path(__file__).resolve().parent / "independent_audit.json").write_text(
    rendered, encoding="utf-8"
)
print(rendered, end="")
raise SystemExit(0 if result["status"] == "PASS" else 1)
