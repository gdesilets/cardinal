from __future__ import annotations

"""Freeze Jan 9 forum-system-event evidence and build an append-only copy."""

import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import premium_journals_system_event_timestamp_v1 as v1  # noqa: E402


STAGE = ROOT / (
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-09_20260722T091933Z/"
    "v2_6_revalidated"
)
FILENAME = (
    "channel_premium_journals_1283941772577472643_"
    "2026-01-09_2026-01-09.json"
)
SOURCE = STAGE / FILENAME
NAVIGATION = STAGE / "forum_group_navigation_checkpoints"
EVIDENCE = STAGE / "system_event_dom_evidence_v1"
OBSERVATION = EVIDENCE / (
    "message_1459342322675224696.normalized_dom_observation.json"
)
MANIFEST = EVIDENCE / "manifest.json"
TARGET_DIR = STAGE / "system_event_timestamp_revalidated_v1"
TARGET = TARGET_DIR / FILENAME
STAGE_SIDECAR = v1.sidecar_path(TARGET)
CANONICAL_SIDECAR = (
    TARGET_DIR
    / "canonical_bindings_v1"
    / STAGE_SIDECAR.name
)
CANONICAL_RELATIVE = f"raw/channel_segments_v2_5/{FILENAME}"
MESSAGE_ID = "1459342322675224696"
QUERY = "in:premium-journals after:2026-01-08 before:2026-01-10"
SOURCE_SHA256 = (
    "02e2df498f63063fa7f5f0c202c133fc3f7599ed10726f49dca14fc34e90c4bc"
)
SOURCE_BYTES = 1_465_986
OBSERVATION_SHA256 = (
    "6ce29868f0d8029fe89bfc24e375536807e205edc3cf762a231802614413327e"
)
OBSERVATION_BYTES = 5_522
SOURCE_URL = (
    "https://discord.com/channels/"
    "1167376964680691732/1283941772577472643"
)
PAGE_HASHES = {
    1: "1081f73132c981dd85b0a61b91a812a870af31b42cdb068638aa0c1ad2cbac78",
    2: "03017f108e6ce89fc44b25228de52143113689e1cb852fbead69c1002bb6e2c9",
    3: "a072081ab62fecf1ce5756ea69632711a2518c0558fd65fb15d8e5561f85dec4",
    4: "f18356a77230bbb3aaa17964bbb32052bdbc939d42a2ac3238ccfb09cd5b9a88",
    5: "544aa45cc8586d525f8e0fb22bf25a2986fbcd04f834617ab765c19eef36a8c3",
    6: "9015fab1bccde5ff059975185b6016a720f39e6aa6cf3f9e14529751c24b2c10",
    7: "90294be06bb360cdc507a146c5654cdeaa4ea9aa61e7edd40ec758cae22d7688",
    8: "e0293e4359ce3261a5b34f2ba8f9d46c7313221586d9037cd5a4a600b832769a",
}
REACQUISITION = [
    (8, "2026-07-22T09:44:50.697Z", "initial_terminal", False, True),
    (7, "2026-07-22T09:44:58.404Z", "backward", False, False),
    (6, "2026-07-22T09:45:01.411Z", "backward", False, False),
    (5, "2026-07-22T09:45:04.539Z", "backward", False, False),
    (4, "2026-07-22T09:45:07.576Z", "backward", False, False),
    (3, "2026-07-22T09:45:10.679Z", "backward", False, False),
    (2, "2026-07-22T09:45:13.678Z", "backward", False, False),
    (1, "2026-07-22T09:45:16.881Z", "backward", True, False),
    (2, "2026-07-22T09:46:43.606Z", "forward", False, False),
    (3, "2026-07-22T09:46:47.013Z", "forward", False, False),
    (4, "2026-07-22T09:46:49.978Z", "forward", False, False),
    (5, "2026-07-22T09:46:53.021Z", "forward", False, False),
    (6, "2026-07-22T09:46:56.061Z", "forward", False, False),
    (7, "2026-07-22T09:46:59.118Z", "forward", False, False),
    (8, "2026-07-22T09:47:02.255Z", "forward", False, True),
    (8, "2026-07-22T09:47:03.779Z", "stable_terminal", False, True),
]


def compact_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def tree_manifest(root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    encoded = json.dumps(
        records, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return {
        "path": rel(root),
        "sha256": sha256_bytes(encoded),
        "file_count": len(records),
        "bytes": sum(int(record["bytes"]) for record in records),
        "records": records,
    }


def render_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def write_exclusive(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        dt.timezone.utc
    )


def validate_observation(
    observation: dict[str, Any], row: dict[str, Any]
) -> None:
    errors: list[str] = []
    message_id = str(row.get("message_id") or "")
    lines = [
        line.strip()
        for line in str(row.get("content_text") or "").splitlines()
        if line.strip()
    ]
    expected_title = re.sub(r"^changed the post title:\s*", "", lines[1])
    if observation.get("schema") != "discord_forum_title_change_dom_observation_v1":
        errors.append("schema")
    if observation.get("messageId") != message_id:
        errors.append("message_id")
    if observation.get("sourceUrl") != SOURCE_URL:
        errors.append("source_url")
    article = observation.get("article") or {}
    attrs = article.get("attrs") or {}
    if not (
        article.get("tag") == "div"
        and attrs.get("id") == f"search-result-{message_id}"
        and attrs.get("role") == "article"
        and attrs.get("data-list-item-id") == f"NO_LIST___{message_id}"
        and attrs.get("aria-labelledby") == f"message-content-{message_id}"
    ):
        errors.append("article_binding")
    content = observation.get("messageContent") or {}
    if not (
        content.get("ownedByArticle") is True
        and (content.get("attrs") or {}).get("id")
        == f"message-content-{message_id}"
        and len(content.get("directChildren") or []) == 1
    ):
        errors.append("message_content_owner")
    container = observation.get("systemContainer") or {}
    children = container.get("directChildren") or []
    if not (
        container.get("tag") == "div"
        and len(children) == 2
        and (children[0].get("attrs") or {}).get("class")
        == "iconContainer__235ca"
        and (children[1].get("attrs") or {}).get("class") == "content__235ca"
    ):
        errors.append("product_system_container")
    body = observation.get("systemBody") or {}
    nodes = body.get("orderedChildNodes") or []
    if not (
        body.get("containerClass") == "content__235ca"
        and body.get("innerTag") == "div"
        and len(nodes) == 4
        and nodes[0].get("node") == "a"
        and (nodes[0].get("attrs") or {}).get("role") == "link"
        and nodes[1] == {"node": "#text", "text": "changed the post title:"}
        and nodes[2] == {"attrs": {}, "node": "strong", "text": expected_title}
        and nodes[3].get("node") == "span"
        and (nodes[3].get("attrs") or {}).get("class")
        == "timestamp_c19a55 timestampInline_c19a55"
    ):
        errors.append("product_system_grammar")
    actor = observation.get("actor") or {}
    if not (
        actor.get("inlineLinkCount") == 1
        and (actor.get("inlineLinkAttrs") or {}).get("role") == "link"
        and actor.get("inlineUsernameText") == lines[0]
        and actor.get("avatarCount") == 0
        and actor.get("standardHeaderCount") == 0
        and actor.get("standardUsernameIdCount") == 0
        and actor.get("dataTextNodeCount") == 0
    ):
        errors.append("actor_system_shape")
    icon = observation.get("systemIcon") or {}
    if not (
        icon.get("containerClass") == "iconContainer__235ca"
        and (icon.get("svgAttrs") or {}).get("role") == "img"
        and (icon.get("svgAttrs") or {}).get("aria-hidden") == "true"
        and icon.get("pathDSha256")
        == "6fb33f317e9d5cbdb14723626d95625ac6bc6ec7239156203d532bbd7e82f957"
    ):
        errors.append("pencil_icon")
    timestamp = observation.get("timestamp") or {}
    if not (
        timestamp.get("ownedByMessageContent") is True
        and timestamp.get("hasMessageSpecificTimeId") is False
        and (timestamp.get("timeAttrs") or {}).get("datetime")
        == row.get("timestamp_utc")
        and (timestamp.get("separator") or {}).get("ariaHidden") == "true"
        and (timestamp.get("separator") or {}).get("text") == "—"
    ):
        errors.append("timestamp_owner")
    if observation.get("semanticMarkers") != {
        "ariaLabels": [],
        "eventTypeDataAttributes": [],
        "roleStatusCount": 0,
    }:
        errors.append("semantic_marker_state")
    snowflake_ms = (int(message_id) >> 22) + 1_420_070_400_000
    encoded = dt.datetime.fromtimestamp(
        snowflake_ms / 1000, tz=dt.timezone.utc
    )
    try:
        timestamp_utc = parse_utc(str(row.get("timestamp_utc") or ""))
        owned_utc = parse_utc(str(row.get("row_owned_time_datetime") or ""))
        declared_snowflake = parse_utc(
            str(row.get("snowflake_timestamp_utc") or "")
        )
    except ValueError:
        errors.append("timestamp_parse")
    else:
        if not (
            row.get("timestamp_scope_exact") is False
            and row.get("row_owned_time_count") == 1
            and row.get("row_owned_time_element_id") in (None, "")
            and row.get("timestamp_discrepancy_ms") == 0
            and timestamp_utc == owned_utc == declared_snowflake == encoded
        ):
            errors.append("timestamp_equality")
    if errors:
        raise RuntimeError("observation_invalid:" + ",".join(errors))


def main() -> None:
    for path in (MANIFEST, TARGET, STAGE_SIDECAR, CANONICAL_SIDECAR):
        if path.exists():
            raise RuntimeError(f"append_only_target_exists:{rel(path)}")
    if sha256_file(SOURCE) != SOURCE_SHA256 or SOURCE.stat().st_size != SOURCE_BYTES:
        raise RuntimeError("source_binding_mismatch")
    if (
        sha256_file(OBSERVATION) != OBSERVATION_SHA256
        or OBSERVATION.stat().st_size != OBSERVATION_BYTES
    ):
        raise RuntimeError("observation_binding_mismatch")

    source_raw = SOURCE.read_bytes()
    source = json.loads(source_raw.decode("utf-8"))
    if not (
        source.get("collector_version") == "2.6"
        and source.get("complete") is True
        and source.get("reported_total") == 194
        and source.get("reported_pages") == 8
        and len(source.get("messages") or []) == 194
        and (source.get("segment") or {}).get("start") == "2026-01-09"
        and (source.get("segment") or {}).get("end") == "2026-01-09"
        and (source.get("segment") or {}).get("timezone") == "America/Chicago"
        and (source.get("segment") or {}).get("query") == QUERY
    ):
        raise RuntimeError("source_route_or_terminal_mismatch")
    source_rows = {
        str(row.get("message_id")): row
        for row in source.get("messages") or []
        if isinstance(row, dict)
    }
    row = source_rows.get(MESSAGE_ID)
    if not isinstance(row, dict) or row.get("result_index") != 6:
        raise RuntimeError("target_row_missing_or_index_mismatch")
    observation = json.loads(OBSERVATION.read_text(encoding="utf-8"))
    validate_observation(observation, row)

    page_ids: dict[int, list[str]] = {}
    for page_number in range(1, 9):
        page_ids[page_number] = [
            str(item.get("message_id"))
            for item in sorted(
                (
                    item
                    for item in source.get("messages") or []
                    if int(item.get("page_number") or 0) == page_number
                ),
                key=lambda item: int(item.get("result_index") or 0),
            )
        ]
        observed_hash = sha256_bytes(
            json.dumps(page_ids[page_number], separators=(",", ":")).encode()
        )
        if observed_hash != PAGE_HASHES[page_number]:
            raise RuntimeError(f"source_page_membership_hash_mismatch:{page_number}")

    navigation_before = tree_manifest(NAVIGATION)
    if (
        navigation_before["file_count"] != 75
        or navigation_before["bytes"] != 215_252
    ):
        raise RuntimeError("source_navigation_tree_shape_mismatch")
    source_before = (sha256_file(SOURCE), SOURCE.stat().st_size)
    records = []
    for sequence, (page, observed_at, direction, back_disabled, next_disabled) in enumerate(
        REACQUISITION, start=1
    ):
        visible = 19 if page == 8 else 25
        records.append(
            {
                "sequence": sequence,
                "page": page,
                "observed_at_utc": observed_at,
                "direction": direction,
                "query_exact": True,
                "reported_total": 194,
                "visible_result_count": visible,
                "ordered_message_id_count": visible,
                "ordered_message_ids_sha256": PAGE_HASHES[page],
                "expected_ordered_message_ids_sha256": PAGE_HASHES[page],
                "ordered_membership_exact": True,
                "back_disabled": back_disabled,
                "next_disabled": next_disabled,
                "source_url": SOURCE_URL,
            }
        )

    observation_compact = compact_json(observation)
    manifest = {
        "schema_version": "1.0.0",
        "artifact_type": "discord_forum_system_event_dom_evidence_manifest",
        "immutable": True,
        "append_only": True,
        "created_at_utc": "2026-07-22T09:47:03.779Z",
        "evidence_directory": rel(EVIDENCE),
        "source_segment": {
            "path": rel(SOURCE),
            "sha256": SOURCE_SHA256,
            "bytes": SOURCE_BYTES,
            "collector_version": "2.6",
            "complete": True,
            "reported_total": 194,
            "reported_pages": 8,
            "captured_rows": 194,
        },
        "source_navigation_tree": {
            key: value
            for key, value in navigation_before.items()
            if key != "records"
        },
        "search_binding": {
            "query": QUERY,
            "guild_id": v1.GUILD_ID,
            "parent_forum_channel_id": v1.PARENT_FORUM_ID,
            "source_url": SOURCE_URL,
            "target_page_number": 1,
            "reported_total": 194,
            "reacquisition_used_still_active_search": True,
            "reacquisition_query_submission_count": 0,
            "no_new_query_submitted": True,
            "outside_sources_used": False,
        },
        "pagination_reacquisition": {
            "navigation_controls": "visible_search_pagination_back_and_next_only",
            "backward_pages": [8, 7, 6, 5, 4, 3, 2, 1],
            "forward_pages": [1, 2, 3, 4, 5, 6, 7, 8],
            "exact_query_verified_each_page": True,
            "reported_total_verified_each_page": True,
            "source_url_verified_each_page": True,
            "full_ordered_message_membership_verified_each_page": True,
            "ordered_membership_hash_algorithm": (
                "sha256_utf8_compact_json_ordered_message_ids"
            ),
            "observation_count": len(records),
            "observations": records,
            "drift_detected": False,
        },
        "observations": [
            {
                "message_id": MESSAGE_ID,
                "page_number": 1,
                "event_type": "forum_post_title_changed",
                "actor_text": "adams",
                "new_title": "30 day diaries 📚 (boy challenge)",
                "timestamp_utc": row.get("timestamp_utc"),
                "path": rel(OBSERVATION),
                "sha256": OBSERVATION_SHA256,
                "bytes": OBSERVATION_BYTES,
                "canonical_compact_json_sha256": sha256_bytes(
                    observation_compact
                ),
                "canonical_compact_json_bytes": len(observation_compact),
                "pencil_svg_path_sha256": (
                    "6fb33f317e9d5cbdb14723626d95625ac6bc6ec7239156203d532bbd7e82f957"
                ),
            }
        ],
        "classifier_evidence": {
            "product_system_component_structure_present": True,
            "product_generated_literal_grammar_present": True,
            "product_pencil_icon_present": True,
            "normal_avatar_header_username_structure_absent": True,
            "exact_message_content_owns_timestamp": True,
            "message_specific_timestamp_id_absent": True,
            "stable_semantic_event_type_attribute_present": False,
            "role_status_present": False,
            "aria_event_label_present": False,
            "required_exact_grammar": (
                "<inline actor link> + changed the post title: + "
                "<strong new title> + <owned inline timestamp>"
            ),
        },
        "timestamp_equality": {
            "message_id": MESSAGE_ID,
            "captured_timestamp_utc": row.get("timestamp_utc"),
            "row_owned_time_utc": row.get("row_owned_time_datetime"),
            "declared_snowflake_timestamp_utc": row.get(
                "snowflake_timestamp_utc"
            ),
            "discord_snowflake_decoded_timestamp_utc": row.get(
                "snowflake_timestamp_utc"
            ),
            "all_equal": True,
        },
        "terminal_restoration": {
            "restored_to_page_number": 8,
            "exact_query": True,
            "reported_total": 194,
            "source_url": SOURCE_URL,
            "visible_result_count": 19,
            "full_ordered_message_membership_verified": True,
            "ordered_message_ids_sha256": PAGE_HASHES[8],
            "next_button_present": True,
            "next_button_disabled": True,
            "stable_terminal_state": True,
            "stable_observation_count": 2,
        },
        "mutation_scope": {
            "staged_segment_modified": False,
            "navigation_artifacts_modified": False,
            "canonical_target_created": False,
            "legacy_target_created": False,
            "revalidated_copy_created": False,
            "promoted": False,
            "only_new_paths_under_evidence_directory": True,
        },
    }
    manifest_raw = render_json(manifest)

    target_payload = copy.deepcopy(source)
    target_row = next(
        item
        for item in target_payload["messages"]
        if str(item.get("message_id")) == MESSAGE_ID
    )
    target_row.update(v1._expected_correction(str(row.get("timestamp_utc") or "")))
    target_raw = render_json(target_payload)
    target_sha = sha256_bytes(target_raw)
    manifest_sha = sha256_bytes(manifest_raw)
    route = {
        "guild_id": v1.GUILD_ID,
        "parent_forum_channel_id": v1.PARENT_FORUM_ID,
        "start": "2026-01-09",
        "end": "2026-01-09",
        "timezone": "America/Chicago",
        "query": QUERY,
        "page_number": row.get("page_number"),
        "forum_group_navigation_evidence_key": row.get(
            "forum_group_navigation_evidence_key"
        ),
        "exact_permalink": row.get("exact_permalink"),
    }
    record = {
        "status": "passed",
        "evidence_type": v1.EVIDENCE_TYPE,
        "message_id": MESSAGE_ID,
        "source_row_sha256": v1.row_sha256(row),
        "revalidated_row_sha256": v1.row_sha256(target_row),
        "effective_correction": v1._expected_correction(
            str(row.get("timestamp_utc") or "")
        ),
        "route": route,
        "dom_observation": {
            "path": rel(OBSERVATION),
            "sha256": OBSERVATION_SHA256,
            "bytes": OBSERVATION_BYTES,
        },
    }
    sidecar_base = {
        "schema_version": v1.SCHEMA_VERSION,
        "artifact_type": v1.ARTIFACT_TYPE,
        "contract_profile": "premium_forum_title_change_revalidated_copy_v1",
        "source_scope": "discord_only",
        "outside_sources_used": False,
        "source_original": {
            "path": rel(SOURCE),
            "sha256": SOURCE_SHA256,
            "bytes": SOURCE_BYTES,
        },
        "revalidated_artifact": {
            "path": rel(TARGET),
            "sha256": target_sha,
            "bytes": len(target_raw),
        },
        "dom_evidence_manifest": {
            "path": rel(MANIFEST),
            "sha256": manifest_sha,
            "bytes": len(manifest_raw),
        },
        "revalidations": [record],
    }
    stage_sidecar_raw = render_json(sidecar_base)
    canonical_sidecar = copy.deepcopy(sidecar_base)
    canonical_sidecar["revalidated_artifact"]["path"] = CANONICAL_RELATIVE
    canonical_sidecar_raw = render_json(canonical_sidecar)

    write_exclusive(MANIFEST, manifest_raw)
    write_exclusive(TARGET, target_raw)
    write_exclusive(STAGE_SIDECAR, stage_sidecar_raw)
    write_exclusive(CANONICAL_SIDECAR, canonical_sidecar_raw)

    navigation_after = tree_manifest(NAVIGATION)
    source_after = (sha256_file(SOURCE), SOURCE.stat().st_size)
    if source_after != source_before or navigation_after != navigation_before:
        raise RuntimeError("immutable_source_or_navigation_changed")
    print(
        json.dumps(
            {
                "source": {
                    "path": rel(SOURCE),
                    "sha256": SOURCE_SHA256,
                    "bytes": SOURCE_BYTES,
                },
                "navigation_tree": {
                    key: value
                    for key, value in navigation_before.items()
                    if key != "records"
                },
                "observation": {
                    "path": rel(OBSERVATION),
                    "sha256": OBSERVATION_SHA256,
                    "bytes": OBSERVATION_BYTES,
                },
                "manifest": {
                    "path": rel(MANIFEST),
                    "sha256": sha256_file(MANIFEST),
                    "bytes": MANIFEST.stat().st_size,
                },
                "revalidated_copy": {
                    "path": rel(TARGET),
                    "sha256": sha256_file(TARGET),
                    "bytes": TARGET.stat().st_size,
                },
                "stage_sidecar": {
                    "path": rel(STAGE_SIDECAR),
                    "sha256": sha256_file(STAGE_SIDECAR),
                    "bytes": STAGE_SIDECAR.stat().st_size,
                },
                "canonical_sidecar": {
                    "path": rel(CANONICAL_SIDECAR),
                    "sha256": sha256_file(CANONICAL_SIDECAR),
                    "bytes": CANONICAL_SIDECAR.stat().st_size,
                },
                "original_source_unchanged": True,
                "original_navigation_unchanged": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
