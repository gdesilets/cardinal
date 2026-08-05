from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_BASELINE = SCRIPT_DIR / "raw" / "forum_thread_inventory.json"
DEFAULT_EVIDENCE = (
    SCRIPT_DIR
    / "raw"
    / "quarantine_collection_errors"
    / "collector_b_premium_journals_fresh_staging_20260721"
    / "premium_journals_2026-01-02_authenticated_group_navigation_evidence_page_2.json"
)
DEFAULT_OUTPUT = (
    SCRIPT_DIR / "working" / "premium_journals_scoped_inventory_reconciliation.json"
)

GUILD_ID = "1167376964680691732"
PARENT_FORUM_CHANNEL_ID = "1283941772577472643"
EXPECTED_QUERY = "in:premium-journals after:2026-01-01 before:2026-01-03"
EXPECTED_BASELINE_SHA256 = (
    "06dc88e5d1c93c8a7e927c4aa7e8713ecd0655469e227cbab9d9de288c658493"
)
EXPECTED_EVIDENCE_SHA256 = (
    "3c27fcd1a0630f2d7555e50f411ed3eef27c07fc62a44d2cc00af629c8458ab4"
)
EXPECTED_BASELINE_COUNT = 156
EXPECTED_ADDED_IDS = frozenset(
    {"1448404594731516058", "1456316273788063925"}
)

SNOWFLAKE_RE = re.compile(r"^[0-9]{17,20}$")
CHANNEL_URL_RE = re.compile(
    r"^https://discord\.com/channels/(?P<guild>[0-9]{17,20})/"
    r"(?P<channel>[0-9]{17,20})/?$"
)


class ReconciliationValidationError(ValueError):
    def __init__(self, issues: Iterable[str]):
        self.issues = tuple(dict.fromkeys(str(issue) for issue in issues))
        super().__init__("; ".join(self.issues))


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_json_object(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReconciliationValidationError([f"{label}_unreadable:{exc}"]) from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReconciliationValidationError([f"{label}_invalid_json:{exc}"]) from exc
    if not isinstance(value, dict):
        raise ReconciliationValidationError([f"{label}_root_not_object"])
    return value, raw


def require(condition: bool, issue: str, issues: list[str]) -> None:
    if not condition:
        issues.append(issue)


def is_snowflake(value: Any) -> bool:
    return bool(SNOWFLAKE_RE.fullmatch(str(value or "")))


def validate_baseline(
    payload: dict[str, Any],
    raw: bytes,
    *,
    expected_sha256: str,
    expected_count: int,
) -> tuple[list[str], dict[str, str]]:
    issues: list[str] = []
    actual_sha = sha256_bytes(raw)
    require(actual_sha == expected_sha256, "baseline_source_sha256_mismatch", issues)
    require(payload.get("guild_id") == GUILD_ID, "baseline_wrong_guild", issues)
    require(
        payload.get("parent_forum_channel_id") == PARENT_FORUM_CHANNEL_ID,
        "baseline_wrong_parent",
        issues,
    )
    require(payload.get("source_scope") == "discord_only", "baseline_not_discord_only", issues)
    require(payload.get("outside_sources_used") is False, "baseline_outside_sources_used", issues)

    rows = payload.get("threads")
    if not isinstance(rows, list):
        issues.append("baseline_threads_not_array")
        rows = []
    require(len(rows) == expected_count, "baseline_thread_count_mismatch", issues)

    ids: list[str] = []
    titles: dict[str, str] = {}
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(f"baseline_thread_{index}_not_object")
            continue
        thread_id = str(row.get("thread_id") or "")
        if not is_snowflake(thread_id):
            issues.append(f"baseline_thread_{index}_invalid_id")
            continue
        if thread_id in seen:
            issues.append(f"baseline_duplicate_thread_id:{thread_id}")
            continue
        seen.add(thread_id)
        ids.append(thread_id)
        titles[thread_id] = str(row.get("title") or "")
        require(
            row.get("parent_forum_channel_id") == PARENT_FORUM_CHANNEL_ID,
            f"baseline_thread_wrong_parent:{thread_id}",
            issues,
        )

    if issues:
        raise ReconciliationValidationError(issues)
    return ids, titles


def resolve_bound_partial(
    corpus_root: Path,
    evidence: dict[str, Any],
) -> tuple[Path, dict[str, Any], bytes]:
    issues: list[str] = []
    source_ref = evidence.get("source_partial_path")
    if not isinstance(source_ref, str) or not source_ref.strip():
        raise ReconciliationValidationError(["evidence_source_partial_path_missing"])
    candidate = Path(source_ref)
    require(not candidate.is_absolute(), "evidence_source_partial_path_absolute", issues)
    root = corpus_root.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        issues.append("evidence_source_partial_path_outside_corpus")
    if issues:
        raise ReconciliationValidationError(issues)

    partial, partial_raw = load_json_object(resolved, "source_partial")
    expected_partial_sha = str(evidence.get("source_partial_sha256") or "")
    if sha256_bytes(partial_raw) != expected_partial_sha:
        raise ReconciliationValidationError(["source_partial_sha256_mismatch"])
    return resolved, partial, partial_raw


def validate_evidence(
    payload: dict[str, Any],
    raw: bytes,
    *,
    corpus_root: Path,
    expected_sha256: str,
) -> tuple[list[dict[str, Any]], Path, bytes]:
    issues: list[str] = []
    require(sha256_bytes(raw) == expected_sha256, "evidence_source_sha256_mismatch", issues)
    require(
        payload.get("evidence_type")
        == "authenticated_discord_search_group_header_navigation",
        "evidence_type_not_exact_authenticated_navigation",
        issues,
    )
    require(payload.get("guild_id") == GUILD_ID, "evidence_wrong_guild", issues)
    require(
        payload.get("parent_forum_channel_id") == PARENT_FORUM_CHANNEL_ID,
        "evidence_wrong_parent",
        issues,
    )
    require(payload.get("query") == EXPECTED_QUERY, "evidence_wrong_query", issues)
    require(payload.get("page_number") == 2, "evidence_wrong_page", issues)

    identity_policy = payload.get("identity_policy")
    if not isinstance(identity_policy, dict):
        issues.append("evidence_identity_policy_missing")
        identity_policy = {}
    require(
        identity_policy.get("title_only_identity_used") is False,
        "evidence_title_only_identity_not_explicitly_false",
        issues,
    )
    require(
        identity_policy.get("attachment_or_media_channel_ids_used") is False,
        "evidence_attachment_identity_not_explicitly_false",
        issues,
    )

    validation = payload.get("page_validation")
    if not isinstance(validation, dict):
        issues.append("evidence_page_validation_missing")
        validation = {}
    for field in (
        "all_result_indices_contiguous",
        "all_result_indices_unique",
        "all_message_ids_unique",
        "same_title_groups_kept_separate",
        "direct_child_header_count_equaled_group_count",
        "back_return_same_query_page_verified",
    ):
        require(validation.get(field) is True, f"evidence_page_validation_false:{field}", issues)
    expected_parent_url = f"https://discord.com/channels/{GUILD_ID}/{PARENT_FORUM_CHANNEL_ID}"
    require(
        validation.get("back_return_parent_url") == expected_parent_url,
        "evidence_back_return_wrong_parent",
        issues,
    )

    if issues:
        raise ReconciliationValidationError(issues)
    partial_path, partial, partial_raw = resolve_bound_partial(corpus_root, payload)

    issues = []
    require(partial.get("guild_id") == GUILD_ID, "source_partial_wrong_guild", issues)
    requested = partial.get("requested_container")
    if not isinstance(requested, dict):
        issues.append("source_partial_requested_container_missing")
        requested = {}
    require(
        requested.get("channel_id") == PARENT_FORUM_CHANNEL_ID,
        "source_partial_wrong_parent",
        issues,
    )
    segment = partial.get("segment")
    if not isinstance(segment, dict):
        issues.append("source_partial_segment_missing")
        segment = {}
    require(segment.get("query") == EXPECTED_QUERY, "source_partial_wrong_query", issues)
    require(partial.get("complete") is False, "source_partial_must_remain_partial", issues)

    messages = partial.get("messages")
    if not isinstance(messages, list):
        issues.append("source_partial_messages_not_array")
        messages = []
    page_rows: dict[tuple[int, str], dict[str, Any]] = {}
    for row in messages:
        if not isinstance(row, dict) or row.get("page_number") != 2:
            continue
        key = (row.get("result_index"), str(row.get("message_id") or ""))
        if key in page_rows:
            issues.append(f"source_partial_duplicate_page2_tuple:{key[0]}:{key[1]}")
        page_rows[key] = row

    groups = payload.get("groups")
    if not isinstance(groups, list):
        issues.append("evidence_groups_not_array")
        groups = []
    require(payload.get("search_group_count") == len(groups), "evidence_group_count_mismatch", issues)

    normalized: list[dict[str, Any]] = []
    seen_ordinals: set[int] = set()
    seen_indices: set[int] = set()
    seen_messages: set[str] = set()
    for position, group in enumerate(groups):
        if not isinstance(group, dict):
            issues.append(f"evidence_group_{position}_not_object")
            continue
        ordinal = group.get("group_ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int):
            issues.append(f"evidence_group_{position}_ordinal_invalid")
            continue
        if ordinal in seen_ordinals:
            issues.append(f"evidence_duplicate_group_ordinal:{ordinal}")
        seen_ordinals.add(ordinal)

        indices = group.get("result_indices")
        message_ids = group.get("message_ids")
        if not isinstance(indices, list) or not isinstance(message_ids, list):
            issues.append(f"evidence_group_{ordinal}_membership_not_arrays")
            continue
        if not indices or len(indices) != len(message_ids):
            issues.append(f"evidence_group_{ordinal}_membership_length_mismatch")
            continue

        destination = str(group.get("click_destination_url") or "")
        match = CHANNEL_URL_RE.fullmatch(destination)
        thread_id = str(group.get("observed_thread_id") or "")
        if not match:
            issues.append(f"evidence_group_{ordinal}_destination_invalid")
        else:
            if match.group("guild") != GUILD_ID:
                issues.append(f"evidence_group_{ordinal}_destination_wrong_guild")
            if match.group("channel") != thread_id:
                issues.append(f"evidence_group_{ordinal}_destination_thread_mismatch")
        if group.get("observed_guild_id") != GUILD_ID:
            issues.append(f"evidence_group_{ordinal}_observed_wrong_guild")
        if not is_snowflake(thread_id):
            issues.append(f"evidence_group_{ordinal}_observed_thread_invalid")
        require(
            group.get("unique_direct_child_header_within_group") is True,
            f"evidence_group_{ordinal}_header_not_unique",
            issues,
        )
        require(
            group.get("thread_identity_exact") is True,
            f"evidence_group_{ordinal}_identity_not_exact",
            issues,
        )
        require(
            group.get("back_return_succeeded") is True,
            f"evidence_group_{ordinal}_back_return_failed",
            issues,
        )

        normalized_membership: list[dict[str, Any]] = []
        local_indices: set[int] = set()
        local_messages: set[str] = set()
        for index, message_id_raw in zip(indices, message_ids):
            message_id = str(message_id_raw or "")
            if isinstance(index, bool) or not isinstance(index, int):
                issues.append(f"evidence_group_{ordinal}_result_index_invalid")
                continue
            if not is_snowflake(message_id):
                issues.append(f"evidence_group_{ordinal}_message_id_invalid:{message_id}")
                continue
            if index in local_indices or index in seen_indices:
                issues.append(f"evidence_duplicate_result_index:{index}")
            if message_id in local_messages or message_id in seen_messages:
                issues.append(f"evidence_duplicate_message_id:{message_id}")
            local_indices.add(index)
            local_messages.add(message_id)
            seen_indices.add(index)
            seen_messages.add(message_id)

            source_row = page_rows.get((index, message_id))
            if source_row is None:
                issues.append(f"evidence_membership_not_in_bound_partial:{index}:{message_id}")
                continue
            for source_field, evidence_field in (
                ("group_label", "group_label"),
                ("group_header_text", "group_header_text"),
            ):
                if source_row.get(source_field) != group.get(evidence_field):
                    issues.append(
                        f"evidence_group_{ordinal}_{source_field}_bound_partial_mismatch"
                    )
            if source_row.get("search_query") != EXPECTED_QUERY:
                issues.append(f"evidence_group_{ordinal}_row_wrong_query")
            if source_row.get("collection_channel_id") != PARENT_FORUM_CHANNEL_ID:
                issues.append(f"evidence_group_{ordinal}_row_wrong_parent")
            normalized_membership.append(
                {"result_index": index, "message_id": message_id}
            )

        normalized.append(
            {
                "group_ordinal": ordinal,
                "observed_display_title": str(group.get("group_label") or "").split(
                    ", premium-journals", 1
                )[0],
                "title_identity_role": "display_only_not_used_for_identity",
                "thread_id": thread_id,
                "destination_url": destination,
                "identity_method": "forum_group_header_navigation_exact",
                "exact_message_membership": normalized_membership,
            }
        )

    first = payload.get("expected_result_index_first")
    last = payload.get("expected_result_index_last")
    expected_indices = set(range(first, last + 1)) if isinstance(first, int) and isinstance(last, int) else set()
    require(seen_indices == expected_indices, "evidence_result_index_coverage_mismatch", issues)
    require(
        len(seen_messages) == payload.get("captured_result_count"),
        "evidence_message_count_mismatch",
        issues,
    )
    require(
        set(page_rows) == {(item["result_index"], item["message_id"]) for group in normalized for item in group["exact_message_membership"]},
        "evidence_bound_partial_page2_membership_not_exhaustive",
        issues,
    )

    if issues:
        raise ReconciliationValidationError(issues)
    return normalized, partial_path, partial_raw


def build_reconciliation(
    baseline_path: Path = DEFAULT_BASELINE,
    evidence_path: Path = DEFAULT_EVIDENCE,
    *,
    corpus_root: Path = SCRIPT_DIR,
    expected_baseline_sha256: str = EXPECTED_BASELINE_SHA256,
    expected_evidence_sha256: str = EXPECTED_EVIDENCE_SHA256,
    expected_baseline_count: int = EXPECTED_BASELINE_COUNT,
    expected_added_ids: frozenset[str] = EXPECTED_ADDED_IDS,
) -> dict[str, Any]:
    baseline, baseline_raw = load_json_object(baseline_path, "baseline")
    evidence, evidence_raw = load_json_object(evidence_path, "evidence")
    baseline_ids, baseline_titles = validate_baseline(
        baseline,
        baseline_raw,
        expected_sha256=expected_baseline_sha256,
        expected_count=expected_baseline_count,
    )
    groups, partial_path, partial_raw = validate_evidence(
        evidence,
        evidence_raw,
        corpus_root=corpus_root,
        expected_sha256=expected_evidence_sha256,
    )

    by_thread: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for group in groups:
        by_thread[group["thread_id"]].append(group)
    observed_ids = set(by_thread)
    additions = observed_ids.difference(baseline_ids)
    if additions != set(expected_added_ids):
        raise ReconciliationValidationError(
            [
                "exact_added_thread_set_mismatch:"
                f"expected={sorted(expected_added_ids)},actual={sorted(additions)}"
            ]
        )

    union_ids = list(baseline_ids) + sorted(additions)
    if len(union_ids) != len(set(union_ids)):
        raise ReconciliationValidationError(["reconciled_union_contains_duplicate_id"])

    navigation_observations: list[dict[str, Any]] = []
    for thread_id in sorted(by_thread):
        thread_groups = sorted(by_thread[thread_id], key=lambda row: row["group_ordinal"])
        navigation_observations.append(
            {
                "thread_id": thread_id,
                "classification": (
                    "exact_addition" if thread_id in additions else "baseline_overlap"
                ),
                "baseline_display_title": baseline_titles.get(thread_id),
                "observed_display_titles": sorted(
                    {row["observed_display_title"] for row in thread_groups}
                ),
                "title_identity_role": "display_only_not_used_for_identity",
                "exact_group_evidence": thread_groups,
            }
        )

    evidence_relative = evidence_path.resolve().relative_to(corpus_root.resolve()).as_posix()
    baseline_relative = baseline_path.resolve().relative_to(corpus_root.resolve()).as_posix()
    partial_relative = partial_path.resolve().relative_to(corpus_root.resolve()).as_posix()
    return {
        "schema_version": "1.0.0",
        "artifact_type": "scoped_forum_thread_inventory_reconciliation",
        "guild_id": GUILD_ID,
        "parent_forum_channel_id": PARENT_FORUM_CHANNEL_ID,
        "parent_forum_channel_name": "premium-journals",
        "source_scope": "authenticated_discord_only",
        "outside_sources_used": False,
        "status": "unresolved_census",
        "inventory_complete": False,
        "enumeration_complete": False,
        "closure_proven": False,
        "closure_requirement": (
            "A fresh exhaustive authenticated enumeration of every discoverable active and "
            "archived Premium Journals thread must reach and preserve terminal-state evidence."
        ),
        "baseline": {
            "path": baseline_relative,
            "sha256": sha256_bytes(baseline_raw),
            "thread_count": len(baseline_ids),
            "source_declared_inventory_complete": baseline.get("inventory_complete") is True,
            "source_declared_status": baseline.get("status"),
            "preservation_policy": "immutable_source_not_modified",
            "reconciliation_effect": (
                "The baseline remains preserved, but its closure claim is not carried into this "
                "derived scoped artifact because later exact evidence proves omissions."
            ),
        },
        "additive_evidence_source": {
            "path": evidence_relative,
            "sha256": sha256_bytes(evidence_raw),
            "evidence_type": evidence.get("evidence_type"),
            "query": evidence.get("query"),
            "page_number": evidence.get("page_number"),
            "bound_partial_path": partial_relative,
            "bound_partial_sha256": sha256_bytes(partial_raw),
            "page_scope_only": True,
            "proves_census_closure": False,
        },
        "identity_policy": {
            "exact_key": "thread_id_from_authenticated_group_header_destination_url",
            "row_binding": "exact_page_result_index_and_message_id_membership",
            "title_only_inference_used": False,
            "attachment_or_media_channel_ids_used": False,
            "same_title_groups_may_remain_distinct": True,
            "repeated_groups_for_one_exact_thread_id_are_consolidated_by_thread_id": True,
        },
        "counts": {
            "baseline_exact_thread_ids": len(baseline_ids),
            "exact_additional_thread_ids": len(additions),
            "exact_known_union_thread_ids": len(union_ids),
            "exact_navigation_observed_thread_ids": len(observed_ids),
            "exact_navigation_group_count": len(groups),
        },
        "baseline_thread_ids": baseline_ids,
        "added_thread_ids": sorted(additions),
        "exact_known_union_thread_ids": union_ids,
        "navigation_observations": navigation_observations,
        "limitations": [
            "The 158-ID union is a lower bound, not a closed census.",
            "The additive evidence covers only search page 2 for one bounded Jan. 2 query.",
            "The source partial is intentionally incomplete and remains quarantined.",
            "Display titles are retained for audit readability and never establish identity.",
        ],
    }


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a fail-closed, evidence-bound Premium Journals inventory lower bound "
            "without modifying the original forum inventory or capture evidence."
        )
    )
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--corpus-root", type=Path, default=SCRIPT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        result = build_reconciliation(
            args.baseline,
            args.evidence,
            corpus_root=args.corpus_root,
        )
        write_json_atomic(args.output, result)
    except (ReconciliationValidationError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "status": result["status"],
                "inventory_complete": result["inventory_complete"],
                "exact_known_union_thread_ids": result["counts"][
                    "exact_known_union_thread_ids"
                ],
                "added_thread_ids": result["added_thread_ids"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
