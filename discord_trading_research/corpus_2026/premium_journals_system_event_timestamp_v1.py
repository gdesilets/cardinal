from __future__ import annotations

"""Fail-closed v1 revalidation for Discord forum post-title system events.

Discord search sometimes renders a forum ``changed the post title:`` event
without the normal message-timestamp ARIA token.  This module is deliberately
separate from the historical timestamp contract: it accepts only a SHA-bound
v1 sidecar for an exact Premium Journals forum row.  Staged evidence may use an
adjacent sidecar.  An authoritative canonical may use only an explicitly
registered external sidecar, keeping the authoritative directory JSON-only.
"""

import copy
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = "1.0.0"
ARTIFACT_TYPE = "discord_premium_forum_system_event_timestamp_revalidation"
SIDECAR_SUFFIX = ".forum-system-event-timestamp-revalidation-v1.json"
FALLBACK_SOURCE = "discord_snowflake_exact_forum_post_title_changed_system_event"
EVENT_TYPE = "forum_post_title_changed"
EVIDENCE_TYPE = "discord_forum_post_title_changed_system_event_sole_row_owned_time"
DOM_EVENT_MARKER = "discord_forum_post_title_changed_system_event_exact"
GUILD_ID = "1167376964680691732"
PARENT_FORUM_ID = "1283941772577472643"
CHANNEL_NAME = "premium-journals"
DISCORD_EPOCH_MS = 1_420_070_400_000
ID_RE = re.compile(r"^\d{15,22}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHORT_TIME_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2}, \d{1,2}:\d{2} (?:AM|PM)")
LONG_TIME_RE = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), "
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December) "
    r"\d{1,2}, \d{4} at \d{1,2}:\d{2} (?:AM|PM)"
)

# The authoritative Premium directory must remain canonical-JSON-only.  Each
# exceptional external sidecar is therefore registered by all three immutable
# bindings needed to select it: exact canonical path, exact canonical SHA, and
# exact sidecar path/SHA.  Unlisted canonicals retain the ordinary timestamp
# contract and cannot discover an external sidecar by filename or directory
# scanning.
EXTERNAL_SIDECAR_REGISTRATIONS_V1: dict[str, dict[str, str]] = {
    (
        "raw/channel_segments_v2_5/"
        "channel_premium_journals_1283941772577472643_"
        "2026-01-06_2026-01-06.json"
    ): {
        "source_artifact_sha256": (
            "5e239835f54718999d8aee59503851734713a4c2aa691e2fa28cc1ad10434487"
        ),
        "sidecar_path": (
            "raw/quarantine_collection_errors/"
            "terra_premium_journals_daily_2026-01-06_20260722T041222Z/"
            "v2_6_revalidated/system_event_timestamp_revalidated_v1/"
            "canonical_bindings_v1/"
            "channel_premium_journals_1283941772577472643_"
            "2026-01-06_2026-01-06."
            "forum-system-event-timestamp-revalidation-v1.json"
        ),
        "sidecar_sha256": (
            "bc404665c81e948229d8f85cf2ab7c8a1e59a1d08f4deea456ab26f0700bc3f4"
        ),
    },
    (
        "raw/channel_segments_v2_5/"
        "channel_premium_journals_1283941772577472643_"
        "2026-01-09_2026-01-09.json"
    ): {
        "source_artifact_sha256": (
            "399f0df8ef52878442542043c3d64c0a4cb8070bac5dd0b8df58fcebf2df87ae"
        ),
        "sidecar_path": (
            "raw/quarantine_collection_errors/"
            "terra_premium_journals_daily_2026-01-09_20260722T091933Z/"
            "v2_6_revalidated/system_event_timestamp_revalidated_v1/"
            "canonical_bindings_v1/"
            "channel_premium_journals_1283941772577472643_"
            "2026-01-09_2026-01-09."
            "forum-system-event-timestamp-revalidation-v1.json"
        ),
        "sidecar_sha256": (
            "0dc3951fca360c49c506174cad220b6e6e9b26b3259e86bca2df03a02f5844e1"
        ),
    },
}


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_sha256(row: dict[str, Any]) -> str:
    return sha256_bytes(compact_json(row).encode("utf-8"))


def parse_utc(value: Any) -> dt.datetime:
    text = str(value or "").strip()
    parsed = dt.datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp_has_no_offset")
    return parsed.astimezone(dt.timezone.utc)


def snowflake_time(message_id: str) -> dt.datetime:
    if not ID_RE.fullmatch(message_id):
        raise ValueError("invalid_discord_snowflake")
    return dt.datetime.fromtimestamp(
        ((int(message_id) >> 22) + DISCORD_EPOCH_MS) / 1000,
        tz=dt.timezone.utc,
    )


def sidecar_path(segment_path: Path) -> Path:
    return segment_path.with_name(f"{segment_path.stem}{SIDECAR_SUFFIX}")


def _stable_read_object(path: Path) -> tuple[dict[str, Any], str, int]:
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("artifact_changed_while_reading")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid_utf8_json:{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("top_level_not_object")
    return value, sha256_bytes(raw), len(raw)


def _portable_path(value: Any, root: Path) -> tuple[Path, str]:
    text = str(value or "").strip().replace("\\", "/")
    candidate = PurePosixPath(text)
    if not text or candidate.is_absolute() or re.match(r"^[A-Za-z]:/", text) or any(
        part in {"", ".", ".."} or ":" in part for part in candidate.parts
    ):
        raise ValueError("source_artifact_path_not_portable_relative")
    resolved = (root / Path(*candidate.parts)).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("source_artifact_path_escapes_artifact_root") from exc
    return resolved, candidate.as_posix()


def _registered_external_sidecar(
    bundle: "ForumSystemEventRevalidation",
) -> tuple[Path | None, str | None, bool]:
    """Resolve one exact external registration without scanning the corpus."""

    try:
        segment_relative = bundle.segment_path.relative_to(
            bundle.artifact_root
        ).as_posix()
    except ValueError:
        return None, None, False
    registration = EXTERNAL_SIDECAR_REGISTRATIONS_V1.get(segment_relative)
    if registration is None:
        return None, None, False
    bundle.provided = True
    bundle.sidecar_resolution = "registered_external_v1"
    if not isinstance(registration, dict):
        bundle.errors.append("external_sidecar_registration_not_object")
        return None, None, True
    if set(registration) != {
        "source_artifact_sha256",
        "sidecar_path",
        "sidecar_sha256",
    }:
        bundle.errors.append("external_sidecar_registration_fields_mismatch")
        return None, None, True
    registered_source_sha = str(
        registration.get("source_artifact_sha256") or ""
    ).lower()
    if (
        not SHA256_RE.fullmatch(registered_source_sha)
        or registered_source_sha != bundle.source_artifact_sha256
    ):
        bundle.errors.append("external_sidecar_registered_canonical_sha256_mismatch")
        return None, None, True
    registered_sidecar_sha = str(
        registration.get("sidecar_sha256") or ""
    ).lower()
    if not SHA256_RE.fullmatch(registered_sidecar_sha):
        bundle.errors.append("external_sidecar_registered_sha256_invalid")
        return None, None, True
    try:
        candidate, candidate_relative = _portable_path(
            registration.get("sidecar_path"), bundle.artifact_root
        )
    except (AttributeError, ValueError) as exc:
        bundle.errors.append(f"external_sidecar_registered_path_invalid:{exc}")
        return None, None, True
    if candidate == sidecar_path(bundle.segment_path):
        bundle.errors.append("external_sidecar_not_separate_from_canonical")
        return None, None, True
    if candidate_relative.startswith("raw/channel_segments_v2_5/"):
        bundle.errors.append("external_sidecar_inside_authoritative_directory")
        return None, None, True
    bundle.sidecar_path = candidate
    if not candidate.is_file():
        bundle.errors.append("external_sidecar_registered_file_missing")
        return None, None, True
    return candidate, registered_sidecar_sha, True


def _exact_dom_observation_errors(
    observation: dict[str, Any], row: dict[str, Any], message_id: str
) -> list[str]:
    """Validate Discord's product-system component conjunction, not prose alone."""
    errors: list[str] = []
    article_id = f"search-result-{message_id}"
    content_id = f"message-content-{message_id}"
    content_lines = [
        line.strip()
        for line in str(row.get("content_text") or "").splitlines()
        if line.strip()
    ]
    expected_actor = content_lines[0] if len(content_lines) >= 2 else ""
    expected_title = re.sub(
        r"^.*?changed the post title:\s*", "",
        content_lines[1] if len(content_lines) >= 2 else "",
    )
    if observation.get("schema") != "discord_forum_title_change_dom_observation_v1": errors.append("observation_schema")
    if observation.get("messageId") != message_id: errors.append("observation_message_id")
    article = observation.get("article", {}).get("attrs", {})
    if article.get("id") != article_id or article.get("role") != "article" or article.get("aria-labelledby") != content_id or article.get("data-list-item-id") != f"NO_LIST___{message_id}": errors.append("observation_article_binding")
    actor = observation.get("actor", {})
    if not (expected_actor and actor.get("inlineLinkCount") == 1 and actor.get("avatarCount") == 0 and actor.get("standardHeaderCount") == 0 and actor.get("standardUsernameIdCount") == 0 and actor.get("inlineUsernameText") == expected_actor and actor.get("inlineLinkAttrs", {}).get("role") == "link"): errors.append("observation_actor_system_shape")
    content = observation.get("messageContent", {})
    if content.get("ownedByArticle") is not True or content.get("attrs", {}).get("id") != content_id: errors.append("observation_content_owner")
    container = observation.get("systemContainer", {})
    children = container.get("directChildren") or []
    if container.get("tag") != "div" or len(children) != 2 or children[0].get("attrs", {}).get("class") != "iconContainer__235ca" or children[1].get("attrs", {}).get("class") != "content__235ca": errors.append("observation_system_container")
    body = observation.get("systemBody", {})
    nodes = body.get("orderedChildNodes") or []
    if not (body.get("containerClass") == "content__235ca" and len(nodes) == 4 and nodes[0].get("node") == "a" and nodes[0].get("attrs", {}).get("role") == "link" and nodes[1] == {"node": "#text", "text": "changed the post title:"} and nodes[2] == {"attrs": {}, "node": "strong", "text": expected_title} and nodes[3].get("node") == "span" and nodes[3].get("attrs", {}).get("class") == "timestamp_c19a55 timestampInline_c19a55"): errors.append("observation_product_system_grammar")
    icon = observation.get("systemIcon", {})
    if not (icon.get("containerClass") == "iconContainer__235ca" and icon.get("svgAttrs", {}).get("role") == "img" and icon.get("svgAttrs", {}).get("aria-hidden") == "true" and icon.get("pathDSha256") == "6fb33f317e9d5cbdb14723626d95625ac6bc6ec7239156203d532bbd7e82f957"): errors.append("observation_product_icon")
    timestamp = observation.get("timestamp", {})
    if not (timestamp.get("ownedByMessageContent") is True and timestamp.get("hasMessageSpecificTimeId") is False and timestamp.get("timeAttrs", {}).get("datetime") == row.get("timestamp_utc") and timestamp.get("separator", {}).get("ariaHidden") == "true" and timestamp.get("separator", {}).get("text") == "\u2014"): errors.append("observation_owned_timestamp")
    semantic = observation.get("semanticMarkers", {})
    if semantic != {"ariaLabels": [], "eventTypeDataAttributes": [], "roleStatusCount": 0}: errors.append("observation_semantic_marker_state")
    return errors


def _load_revalidated_copy(
    bundle: "ForumSystemEventRevalidation", sidecar: dict[str, Any], payload: dict[str, Any]
) -> "ForumSystemEventRevalidation":
    """Strictly validate a copy whose two rows were normalized from immutable DOM proof."""
    root = bundle.artifact_root
    try:
        copy_rel = bundle.segment_path.relative_to(root).as_posix()
    except ValueError:
        bundle.errors.append("revalidated_copy_outside_artifact_root"); return bundle
    copy_spec = sidecar.get("revalidated_artifact") if isinstance(sidecar.get("revalidated_artifact"), dict) else {}
    if not (copy_spec.get("path") == copy_rel and copy_spec.get("sha256") == bundle.source_artifact_sha256 and copy_spec.get("bytes") == bundle.segment_path.stat().st_size): bundle.errors.append("revalidated_copy_binding_mismatch")
    try:
        original_path, original_rel = _portable_path(sidecar.get("source_original", {}).get("path"), root)
        original_payload, original_sha, original_bytes = _stable_read_object(original_path)
    except (AttributeError, OSError, ValueError) as exc:
        bundle.errors.append(f"source_original_unreadable:{exc}"); return bundle
    original_spec = sidecar.get("source_original", {})
    if not (original_spec.get("sha256") == original_sha and original_spec.get("bytes") == original_bytes and original_sha == "a43e51c3e78fe88c7daedc5e9b683bead1fad9fb18c16910a6c04ce5e41e3786"): bundle.errors.append("source_original_hash_binding_mismatch")
    bundle.evidence_artifacts.append({"path": original_path, "kind": "premium_forum_system_event_timestamp_original_stage", "sha256": original_sha})
    try:
        manifest_path, manifest_rel = _portable_path(sidecar.get("dom_evidence_manifest", {}).get("path"), root)
        manifest, manifest_sha, manifest_bytes = _stable_read_object(manifest_path)
    except (AttributeError, OSError, ValueError) as exc:
        bundle.errors.append(f"dom_manifest_unreadable:{exc}"); return bundle
    manifest_spec = sidecar.get("dom_evidence_manifest", {})
    if not (manifest_spec.get("sha256") == manifest_sha and manifest_spec.get("bytes") == manifest_bytes and manifest_sha == "026317b4d9e42c3797cc9f5ac3a5d4d2f20c3b2f6d15c224d5a1d341b4ef91b0"): bundle.errors.append("dom_manifest_hash_binding_mismatch")
    bundle.evidence_artifacts.append({"path": manifest_path, "kind": "premium_forum_system_event_dom_manifest", "sha256": manifest_sha})
    source = manifest.get("source_segment", {})
    binding = manifest.get("search_binding", {})
    terminal = manifest.get("terminal_restoration", {})
    expected_source = {"path": original_rel, "sha256": original_sha, "bytes": original_bytes, "collector_version": "2.6", "complete": True, "reported_total": 315, "reported_pages": 13, "captured_rows": 315}
    if source != expected_source: bundle.errors.append("manifest_source_segment_mismatch")
    if not (binding.get("query") == "in:premium-journals after:2026-01-05 before:2026-01-07" and binding.get("guild_id") == GUILD_ID and binding.get("parent_forum_channel_id") == PARENT_FORUM_ID and binding.get("target_page_number") == 7 and binding.get("source_url") == f"https://discord.com/channels/{GUILD_ID}/{PARENT_FORUM_ID}" and binding.get("no_new_query_submitted") is True and binding.get("outside_sources_used") is False): bundle.errors.append("manifest_search_binding_mismatch")
    if not (terminal.get("restored_to_page_number") == 13 and terminal.get("exact_query") is True and terminal.get("reported_total") == 315 and terminal.get("source_url") == f"https://discord.com/channels/{GUILD_ID}/{PARENT_FORUM_ID}" and terminal.get("next_button_disabled") is True and terminal.get("stable_terminal_state") is True and terminal.get("full_ordered_message_membership_verified") is True): bundle.errors.append("manifest_terminal_restoration_mismatch")
    if manifest.get("mutation_scope", {}).get("staged_segment_modified") is not False or manifest.get("mutation_scope", {}).get("navigation_artifacts_modified") is not False: bundle.errors.append("manifest_mutation_scope_mismatch")
    original_rows = {str(r.get("message_id")): r for r in original_payload.get("messages", []) if isinstance(r, dict)}
    copy_rows = {str(r.get("message_id")): r for r in payload.get("messages", []) if isinstance(r, dict)}
    records = sidecar.get("revalidations")
    observations = {str(x.get("message_id")): x for x in manifest.get("observations", []) if isinstance(x, dict)}
    if not isinstance(records, list) or {str(x.get("message_id")) for x in records if isinstance(x, dict)} != {"1458135984737747005", "1458135642662895720"}: bundle.errors.append("revalidation_records_not_exact_target_pair"); return bundle
    for index, record in enumerate(records, 1):
        prefix = f"record_{index}"; message_id = str(record.get("message_id") or "")
        original_row, copy_row, obs_spec = original_rows.get(message_id), copy_rows.get(message_id), observations.get(message_id)
        if not isinstance(original_row, dict) or not isinstance(copy_row, dict) or not isinstance(obs_spec, dict): bundle.errors.append(f"{prefix}_row_or_manifest_observation_missing"); continue
        correction = _expected_correction(str(original_row.get("timestamp_utc") or ""))
        if not (record.get("status") == "passed" and record.get("evidence_type") == EVIDENCE_TYPE and record.get("source_row_sha256") == row_sha256(original_row) and record.get("revalidated_row_sha256") == row_sha256(copy_row) and record.get("effective_correction") == correction): bundle.errors.append(f"{prefix}_row_hash_or_correction_mismatch")
        expected_copy = copy.deepcopy(original_row); expected_copy.update(correction)
        if copy_row != expected_copy: bundle.errors.append(f"{prefix}_revalidated_row_not_exact_delta")
        route = record.get("route") if isinstance(record.get("route"), dict) else {}
        expected_route = {"guild_id": GUILD_ID, "parent_forum_channel_id": PARENT_FORUM_ID, "start": "2026-01-06", "end": "2026-01-06", "timezone": "America/Chicago", "query": "in:premium-journals after:2026-01-05 before:2026-01-07", "page_number": original_row.get("page_number"), "forum_group_navigation_evidence_key": original_row.get("forum_group_navigation_evidence_key"), "exact_permalink": original_row.get("exact_permalink")}
        if route != expected_route: bundle.errors.append(f"{prefix}_route_or_navigation_mismatch")
        try:
            observation_path, obs_rel = _portable_path(record.get("dom_observation", {}).get("path"), root)
            observation, observation_sha, observation_bytes = _stable_read_object(observation_path)
        except (AttributeError, OSError, ValueError) as exc:
            bundle.errors.append(f"{prefix}_dom_observation_unreadable:{exc}"); continue
        obs = record.get("dom_observation", {})
        if not (obs.get("sha256") == observation_sha and obs.get("bytes") == observation_bytes and obs.get("path") == obs_spec.get("path") and observation_sha == obs_spec.get("sha256") and observation_bytes == obs_spec.get("bytes")): bundle.errors.append(f"{prefix}_dom_observation_hash_binding_mismatch")
        bundle.evidence_artifacts.append({"path": observation_path, "kind": "premium_forum_system_event_dom_observation", "sha256": observation_sha})
        bundle.errors.extend(f"{prefix}_{item}" for item in _exact_dom_observation_errors(observation, original_row, message_id))
        effective = copy.deepcopy(copy_row)
        if not exact_forum_post_title_system_event_fallback(effective, message_id): bundle.errors.append(f"{prefix}_effective_forum_system_event_fallback_not_exact")
        bundle.proofs[message_id] = {"message_id": message_id, "source_row_sha256": row_sha256(copy_row), "effective_correction": {}}
    return bundle


def _load_profiled_revalidated_copy_v1(
    bundle: "ForumSystemEventRevalidation",
    sidecar: dict[str, Any],
    payload: dict[str, Any],
) -> "ForumSystemEventRevalidation":
    """Validate the exact Jan 9 append-only copy and its live DOM proof."""

    root = bundle.artifact_root
    message_id = "1459342322675224696"
    query = "in:premium-journals after:2026-01-08 before:2026-01-10"
    source_url = f"https://discord.com/channels/{GUILD_ID}/{PARENT_FORUM_ID}"
    filename = (
        "channel_premium_journals_1283941772577472643_"
        "2026-01-09_2026-01-09.json"
    )
    source_relative = (
        "raw/quarantine_collection_errors/"
        "terra_premium_journals_daily_2026-01-09_20260722T091933Z/"
        f"v2_6_revalidated/{filename}"
    )
    staged_copy_relative = (
        "raw/quarantine_collection_errors/"
        "terra_premium_journals_daily_2026-01-09_20260722T091933Z/"
        "v2_6_revalidated/system_event_timestamp_revalidated_v1/"
        f"{filename}"
    )
    canonical_relative = f"raw/channel_segments_v2_5/{filename}"
    manifest_relative = (
        "raw/quarantine_collection_errors/"
        "terra_premium_journals_daily_2026-01-09_20260722T091933Z/"
        "v2_6_revalidated/system_event_dom_evidence_v1/manifest.json"
    )
    observation_relative = (
        "raw/quarantine_collection_errors/"
        "terra_premium_journals_daily_2026-01-09_20260722T091933Z/"
        "v2_6_revalidated/system_event_dom_evidence_v1/"
        f"message_{message_id}.normalized_dom_observation.json"
    )
    navigation_relative = (
        "raw/quarantine_collection_errors/"
        "terra_premium_journals_daily_2026-01-09_20260722T091933Z/"
        "v2_6_revalidated/forum_group_navigation_checkpoints"
    )
    source_sha = "02e2df498f63063fa7f5f0c202c133fc3f7599ed10726f49dca14fc34e90c4bc"
    copy_sha = "399f0df8ef52878442542043c3d64c0a4cb8070bac5dd0b8df58fcebf2df87ae"
    manifest_sha = "97f5661d661d55a08a2f48eb228ac5ed3ca00cf2dda785ae9181d2d79a6e3e27"
    observation_sha = "6ce29868f0d8029fe89bfc24e375536807e205edc3cf762a231802614413327e"
    expected_sidecar_shas = {
        staged_copy_relative: "6536558fb260f5be9c87a8877ec0266d48ae6a4124820216613a8bf655e152b2",
        canonical_relative: "0dc3951fca360c49c506174cad220b6e6e9b26b3259e86bca2df03a02f5844e1",
    }
    page_hashes = {
        1: "1081f73132c981dd85b0a61b91a812a870af31b42cdb068638aa0c1ad2cbac78",
        2: "03017f108e6ce89fc44b25228de52143113689e1cb852fbead69c1002bb6e2c9",
        3: "a072081ab62fecf1ce5756ea69632711a2518c0558fd65fb15d8e5561f85dec4",
        4: "f18356a77230bbb3aaa17964bbb32052bdbc939d42a2ac3238ccfb09cd5b9a88",
        5: "544aa45cc8586d525f8e0fb22bf25a2986fbcd04f834617ab765c19eef36a8c3",
        6: "9015fab1bccde5ff059975185b6016a720f39e6aa6cf3f9e14529751c24b2c10",
        7: "90294be06bb360cdc507a146c5654cdeaa4ea9aa61e7edd40ec758cae22d7688",
        8: "e0293e4359ce3261a5b34f2ba8f9d46c7313221586d9037cd5a4a600b832769a",
    }
    reacquisition = [
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

    if sidecar.get("contract_profile") != (
        "premium_forum_title_change_revalidated_copy_v1"
    ):
        bundle.errors.append("profiled_copy_contract_profile_mismatch")
        return bundle
    try:
        copy_relative = bundle.segment_path.relative_to(root).as_posix()
    except ValueError:
        bundle.errors.append("revalidated_copy_outside_artifact_root")
        return bundle
    if copy_relative not in expected_sidecar_shas:
        bundle.errors.append("profiled_copy_path_not_registered")
        return bundle
    if (
        bundle.source_artifact_sha256 != copy_sha
        or bundle.segment_path.stat().st_size != 1_786_921
    ):
        bundle.errors.append("profiled_copy_hash_or_size_mismatch")
    if bundle.sidecar_sha256 != expected_sidecar_shas[copy_relative]:
        bundle.errors.append("profiled_copy_sidecar_sha256_mismatch")
    copy_spec = (
        sidecar.get("revalidated_artifact")
        if isinstance(sidecar.get("revalidated_artifact"), dict)
        else {}
    )
    if copy_spec != {
        "path": copy_relative,
        "sha256": copy_sha,
        "bytes": 1_786_921,
    }:
        bundle.errors.append("profiled_copy_binding_mismatch")

    try:
        original_path, original_relative = _portable_path(
            (sidecar.get("source_original") or {}).get("path"), root
        )
        original_payload, original_observed_sha, original_bytes = (
            _stable_read_object(original_path)
        )
    except (AttributeError, OSError, ValueError) as exc:
        bundle.errors.append(f"profiled_source_original_unreadable:{exc}")
        return bundle
    if (
        original_relative != source_relative
        or sidecar.get("source_original")
        != {"path": source_relative, "sha256": source_sha, "bytes": 1_465_986}
        or original_observed_sha != source_sha
        or original_bytes != 1_465_986
    ):
        bundle.errors.append("profiled_source_original_binding_mismatch")
    bundle.evidence_artifacts.append(
        {
            "path": original_path,
            "kind": "premium_forum_system_event_timestamp_original_stage",
            "sha256": original_observed_sha,
        }
    )

    try:
        manifest_path, manifest_observed_relative = _portable_path(
            (sidecar.get("dom_evidence_manifest") or {}).get("path"), root
        )
        manifest, manifest_observed_sha, manifest_bytes = _stable_read_object(
            manifest_path
        )
    except (AttributeError, OSError, ValueError) as exc:
        bundle.errors.append(f"profiled_dom_manifest_unreadable:{exc}")
        return bundle
    if (
        manifest_observed_relative != manifest_relative
        or sidecar.get("dom_evidence_manifest")
        != {
            "path": manifest_relative,
            "sha256": manifest_sha,
            "bytes": 16_222,
        }
        or manifest_observed_sha != manifest_sha
        or manifest_bytes != 16_222
    ):
        bundle.errors.append("profiled_dom_manifest_binding_mismatch")
    bundle.evidence_artifacts.append(
        {
            "path": manifest_path,
            "kind": "premium_forum_system_event_dom_manifest",
            "sha256": manifest_observed_sha,
        }
    )

    expected_source_segment = {
        "path": source_relative,
        "sha256": source_sha,
        "bytes": 1_465_986,
        "collector_version": "2.6",
        "complete": True,
        "reported_total": 194,
        "reported_pages": 8,
        "captured_rows": 194,
    }
    if not (
        manifest.get("schema_version") == SCHEMA_VERSION
        and manifest.get("artifact_type")
        == "discord_forum_system_event_dom_evidence_manifest"
        and manifest.get("immutable") is True
        and manifest.get("append_only") is True
        and manifest.get("created_at_utc") == "2026-07-22T09:47:03.779Z"
        and manifest.get("source_segment") == expected_source_segment
        and manifest.get("source_navigation_tree")
        == {
            "path": navigation_relative,
            "sha256": "9b20807d31dbc400f128d94ca7a4d024c47cf17e39e0f11b3e37ab756e5f0a0d",
            "file_count": 75,
            "bytes": 215_252,
        }
    ):
        bundle.errors.append("profiled_manifest_source_binding_mismatch")
    if manifest.get("search_binding") != {
        "query": query,
        "guild_id": GUILD_ID,
        "parent_forum_channel_id": PARENT_FORUM_ID,
        "source_url": source_url,
        "target_page_number": 1,
        "reported_total": 194,
        "reacquisition_used_still_active_search": True,
        "reacquisition_query_submission_count": 0,
        "no_new_query_submitted": True,
        "outside_sources_used": False,
    }:
        bundle.errors.append("profiled_manifest_search_binding_mismatch")

    original_rows = {
        str(row.get("message_id")): row
        for row in original_payload.get("messages", [])
        if isinstance(row, dict)
    }
    copy_rows = {
        str(row.get("message_id")): row
        for row in payload.get("messages", [])
        if isinstance(row, dict)
    }
    if len(original_rows) != 194 or len(copy_rows) != 194:
        bundle.errors.append("profiled_copy_message_count_mismatch")
    source_page_hashes: dict[int, str] = {}
    for page_number in range(1, 9):
        ids = [
            str(row.get("message_id"))
            for row in sorted(
                (
                    row
                    for row in original_payload.get("messages", [])
                    if isinstance(row, dict)
                    and int(row.get("page_number") or 0) == page_number
                ),
                key=lambda row: int(row.get("result_index") or 0),
            )
        ]
        source_page_hashes[page_number] = sha256_bytes(
            json.dumps(ids, separators=(",", ":")).encode("utf-8")
        )
    if source_page_hashes != page_hashes:
        bundle.errors.append("profiled_source_page_membership_hash_mismatch")

    expected_reacquisition = []
    for sequence, (page, observed_at, direction, back_disabled, next_disabled) in enumerate(
        reacquisition, start=1
    ):
        visible = 19 if page == 8 else 25
        expected_reacquisition.append(
            {
                "sequence": sequence,
                "page": page,
                "observed_at_utc": observed_at,
                "direction": direction,
                "query_exact": True,
                "reported_total": 194,
                "visible_result_count": visible,
                "ordered_message_id_count": visible,
                "ordered_message_ids_sha256": page_hashes[page],
                "expected_ordered_message_ids_sha256": page_hashes[page],
                "ordered_membership_exact": True,
                "back_disabled": back_disabled,
                "next_disabled": next_disabled,
                "source_url": source_url,
            }
        )
    pagination = manifest.get("pagination_reacquisition")
    pagination = pagination if isinstance(pagination, dict) else {}
    if pagination != {
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
        "observation_count": 16,
        "observations": expected_reacquisition,
        "drift_detected": False,
    }:
        bundle.errors.append("profiled_manifest_pagination_reacquisition_mismatch")

    original_row = original_rows.get(message_id)
    copy_row = copy_rows.get(message_id)
    if not isinstance(original_row, dict) or not isinstance(copy_row, dict):
        bundle.errors.append("profiled_target_row_missing")
        return bundle
    expected_copy_payload = copy.deepcopy(original_payload)
    expected_copy_rows = {
        str(row.get("message_id")): row
        for row in expected_copy_payload.get("messages", [])
        if isinstance(row, dict)
    }
    expected_copy_rows[message_id].update(
        _expected_correction(str(original_row.get("timestamp_utc") or ""))
    )
    if payload != expected_copy_payload:
        bundle.errors.append("profiled_revalidated_copy_not_exact_one_row_delta")

    manifest_observations = manifest.get("observations")
    if not isinstance(manifest_observations, list) or len(manifest_observations) != 1:
        bundle.errors.append("profiled_manifest_observation_count_mismatch")
        manifest_observation = {}
    else:
        manifest_observation = manifest_observations[0]
    try:
        observation_path, observation_observed_relative = _portable_path(
            observation_relative, root
        )
        observation, observation_observed_sha, observation_bytes = (
            _stable_read_object(observation_path)
        )
    except (OSError, ValueError) as exc:
        bundle.errors.append(f"profiled_dom_observation_unreadable:{exc}")
        return bundle
    expected_title = re.sub(
        r"^.*?changed the post title:\s*",
        "",
        [
            line.strip()
            for line in str(original_row.get("content_text") or "").splitlines()
            if line.strip()
        ][1],
    )
    observation_compact = compact_json(observation).encode("utf-8")
    expected_manifest_observation = {
        "message_id": message_id,
        "page_number": 1,
        "event_type": EVENT_TYPE,
        "actor_text": "adams",
        "new_title": expected_title,
        "timestamp_utc": original_row.get("timestamp_utc"),
        "path": observation_relative,
        "sha256": observation_sha,
        "bytes": 5_522,
        "canonical_compact_json_sha256": sha256_bytes(observation_compact),
        "canonical_compact_json_bytes": len(observation_compact),
        "pencil_svg_path_sha256": (
            "6fb33f317e9d5cbdb14723626d95625ac6bc6ec7239156203d532bbd7e82f957"
        ),
    }
    if (
        observation_observed_relative != observation_relative
        or observation_observed_sha != observation_sha
        or observation_bytes != 5_522
        or manifest_observation != expected_manifest_observation
    ):
        bundle.errors.append("profiled_dom_observation_binding_mismatch")
    bundle.evidence_artifacts.append(
        {
            "path": observation_path,
            "kind": "premium_forum_system_event_dom_observation",
            "sha256": observation_observed_sha,
        }
    )
    bundle.errors.extend(
        f"profiled_{item}"
        for item in _exact_dom_observation_errors(
            observation, original_row, message_id
        )
    )

    if manifest.get("classifier_evidence") != {
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
    }:
        bundle.errors.append("profiled_manifest_classifier_mismatch")
    if manifest.get("timestamp_equality") != {
        "message_id": message_id,
        "captured_timestamp_utc": original_row.get("timestamp_utc"),
        "row_owned_time_utc": original_row.get("row_owned_time_datetime"),
        "declared_snowflake_timestamp_utc": original_row.get(
            "snowflake_timestamp_utc"
        ),
        "discord_snowflake_decoded_timestamp_utc": original_row.get(
            "snowflake_timestamp_utc"
        ),
        "all_equal": True,
    }:
        bundle.errors.append("profiled_manifest_timestamp_equality_mismatch")
    if manifest.get("terminal_restoration") != {
        "restored_to_page_number": 8,
        "exact_query": True,
        "reported_total": 194,
        "source_url": source_url,
        "visible_result_count": 19,
        "full_ordered_message_membership_verified": True,
        "ordered_message_ids_sha256": page_hashes[8],
        "next_button_present": True,
        "next_button_disabled": True,
        "stable_terminal_state": True,
        "stable_observation_count": 2,
    }:
        bundle.errors.append("profiled_manifest_terminal_restoration_mismatch")
    if manifest.get("mutation_scope") != {
        "staged_segment_modified": False,
        "navigation_artifacts_modified": False,
        "canonical_target_created": False,
        "legacy_target_created": False,
        "revalidated_copy_created": False,
        "promoted": False,
        "only_new_paths_under_evidence_directory": True,
    }:
        bundle.errors.append("profiled_manifest_mutation_scope_mismatch")

    records = sidecar.get("revalidations")
    if not isinstance(records, list) or len(records) != 1:
        bundle.errors.append("profiled_revalidation_records_not_exact_singleton")
        return bundle
    record = records[0] if isinstance(records[0], dict) else {}
    correction = _expected_correction(str(original_row.get("timestamp_utc") or ""))
    expected_route = {
        "guild_id": GUILD_ID,
        "parent_forum_channel_id": PARENT_FORUM_ID,
        "start": "2026-01-09",
        "end": "2026-01-09",
        "timezone": "America/Chicago",
        "query": query,
        "page_number": original_row.get("page_number"),
        "forum_group_navigation_evidence_key": original_row.get(
            "forum_group_navigation_evidence_key"
        ),
        "exact_permalink": original_row.get("exact_permalink"),
    }
    if not (
        record.get("status") == "passed"
        and record.get("evidence_type") == EVIDENCE_TYPE
        and record.get("message_id") == message_id
        and record.get("source_row_sha256") == row_sha256(original_row)
        and record.get("revalidated_row_sha256") == row_sha256(copy_row)
        and record.get("effective_correction") == correction
        and record.get("route") == expected_route
        and record.get("dom_observation")
        == {
            "path": observation_relative,
            "sha256": observation_sha,
            "bytes": 5_522,
        }
    ):
        bundle.errors.append("profiled_revalidation_record_mismatch")
    if not (
        original_row.get("discord_system_event_exact") is False
        and original_row.get("discord_system_event_type") is None
        and original_row.get("timestamp_exact_fallback_source") is None
        and copy_row.get("discord_system_event_exact") is True
        and copy_row.get("discord_system_event_type") == EVENT_TYPE
        and copy_row.get("timestamp_exact_fallback_source") == FALLBACK_SOURCE
    ):
        bundle.errors.append("profiled_source_or_copy_classification_mismatch")
    if not exact_forum_post_title_system_event_fallback(copy_row, message_id):
        bundle.errors.append("profiled_effective_forum_system_event_fallback_not_exact")
    bundle.proofs[message_id] = {
        "message_id": message_id,
        "source_row_sha256": row_sha256(copy_row),
        "effective_correction": {},
    }
    return bundle


def _expected_correction(timestamp_utc: str) -> dict[str, Any]:
    return {
        "timestamp_scope_exact": False,
        "row_owned_time_count": 1,
        "row_owned_time_datetime": timestamp_utc,
        "row_owned_time_element_id": None,
        "discord_system_event_exact": True,
        "discord_system_event_type": EVENT_TYPE,
        "timestamp_exact_fallback_source": FALLBACK_SOURCE,
        # This is not inferred from prose.  It can only be set by a fresh,
        # hash-bound revalidation record that preserved Discord's system-event
        # semantic marker for the same article/message.
        "discord_system_event_dom_exact": True,
        "discord_system_event_dom_marker": DOM_EVENT_MARKER,
    }


def exact_forum_post_title_system_event_fallback(row: dict[str, Any], message_id: str) -> bool:
    """Predicate for an already-normalized/revalidated exact forum event row."""

    if not ID_RE.fullmatch(message_id):
        return False
    if str(row.get("collection_channel_id") or "") != PARENT_FORUM_ID:
        return False
    if str(row.get("collection_channel_name") or "") != CHANNEL_NAME:
        return False
    if str(row.get("collection_channel_kind") or "") != "forum channel":
        return False
    if str(row.get("parent_channel") or "") != CHANNEL_NAME:
        return False
    if str(row.get("article_id") or "") != f"search-result-{message_id}":
        return False
    if str(row.get("article_aria_labelledby") or "") != f"message-content-{message_id}":
        return False
    if row.get("content_present") is not True or row.get("content_scope_exact") is not True:
        return False
    if row.get("timestamp_scope_exact") is not False:
        return False
    if any(str(row.get(field) or "").strip() for field in ("author", "author_id", "author_avatar_url", "author_id_source")):
        return False
    if row.get("author_id_candidates") not in ([], None) or row.get("author_id_conflict") is not False:
        return False
    if any(row.get(field) not in ([], None) for field in ("attachments", "links", "media_assets", "reactions")):
        return False
    if row.get("reply_context_present") is not False or row.get("reply_context") not in ("", None):
        return False
    if any(row.get(field) not in (None, "", []) for field in (
        "reply_to_message_id", "reply_to_permalink", "reply_to_channel_id", "reply_target_content_id",
        "reply_target_content_text", "reply_target_data_list_item_id", "reply_target_aria_describedby",
        "reply_target_aria_labelledby", "reply_context_owner_message_id",
    )):
        return False
    if row.get("reply_target_id_candidates") not in ([], None) or row.get("reply_target_owner_scoped") is not False:
        return False
    if row.get("reply_to_message_id_conflict") is not False or row.get("reply_to_channel_id_conflict") is not False:
        return False
    if row.get("thread_channel_id_exact") is not True or row.get("thread_channel_id_conflict") is not False:
        return False
    if row.get("forum_group_membership_exact") is not True or message_id not in (row.get("forum_group_message_ids") or []):
        return False
    navigation = row.get("forum_group_navigation_evidence")
    if not isinstance(navigation, dict) or row.get("forum_group_navigation_validation", {}).get("valid") is not True:
        return False
    if (
        navigation.get("guild_id") != GUILD_ID
        or navigation.get("parent_forum_channel_id") != PARENT_FORUM_ID
        or navigation.get("thread_channel_id") != row.get("inferred_thread_channel_id")
        or navigation.get("page_number") != row.get("page_number")
        or message_id not in (navigation.get("group_message_ids") or [])
    ):
        return False
    thread_id = str(row.get("inferred_thread_channel_id") or "")
    if not ID_RE.fullmatch(thread_id):
        return False
    if row.get("exact_permalink") != f"https://discord.com/channels/{GUILD_ID}/{thread_id}/{message_id}":
        return False
    if row.get("exact_permalink_conflict_detected") is not False or row.get("exact_parent_forum_conflict_detected") is not False:
        return False
    lines = [line.strip() for line in str(row.get("content_text") or "").splitlines() if line.strip()]
    if not (
        len(lines) == 5
        and 1 <= len(lines[0]) <= 80
        and re.fullmatch(r"changed the post title: .+", lines[1])
        and lines[2] == "—"
        and SHORT_TIME_RE.fullmatch(lines[3])
        and LONG_TIME_RE.fullmatch(lines[4])
    ):
        return False
    if row.get("discord_system_event_exact") is not True or row.get("discord_system_event_type") != EVENT_TYPE:
        return False
    if row.get("timestamp_exact_fallback_source") != FALLBACK_SOURCE:
        return False
    if row.get("discord_system_event_dom_exact") is not True or row.get("discord_system_event_dom_marker") != DOM_EVENT_MARKER:
        return False
    if row.get("row_owned_time_count") != 1 or row.get("row_owned_time_element_id") not in (None, ""):
        return False
    if row.get("timestamp_discrepancy_ms") != 0:
        return False
    try:
        captured = parse_utc(row.get("timestamp_utc"))
        owned = parse_utc(row.get("row_owned_time_datetime"))
        snowflake = parse_utc(row.get("snowflake_timestamp_utc"))
        encoded = snowflake_time(message_id)
    except (TypeError, ValueError):
        return False
    return captured == owned == snowflake == encoded


@dataclass
class ForumSystemEventRevalidation:
    segment_path: Path
    artifact_root: Path
    source_artifact_sha256: str
    provided: bool = False
    sidecar_path: Path | None = None
    sidecar_sha256: str | None = None
    sidecar_size_bytes: int | None = None
    sidecar_resolution: str | None = None
    proofs: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    used_message_ids: set[str] = field(default_factory=set)
    evidence_artifacts: list[dict[str, Any]] = field(default_factory=list)

    def proof_for(self, row: dict[str, Any]) -> dict[str, Any] | None:
        proof = self.proofs.get(str(row.get("message_id") or ""))
        if proof is None or proof.get("source_row_sha256") != row_sha256(row):
            return None
        self.used_message_ids.add(str(row.get("message_id") or ""))
        return proof

    def unused_message_ids(self) -> list[str]:
        return sorted(set(self.proofs) - self.used_message_ids, key=int)

    def source_artifacts(self) -> list[dict[str, Any]]:
        if self.sidecar_path is None or not self.sidecar_path.is_file():
            return []
        return [
            {"path": self.sidecar_path, "kind": "premium_forum_system_event_timestamp_revalidation_sidecar", "sha256": self.sidecar_sha256},
            *copy.deepcopy(self.evidence_artifacts),
        ]

    def summary(self) -> dict[str, Any]:
        try:
            segment_relative = self.segment_path.resolve().relative_to(self.artifact_root.resolve()).as_posix()
        except ValueError:
            segment_relative = self.segment_path.name
        try:
            sidecar_relative = self.sidecar_path.resolve().relative_to(self.artifact_root.resolve()).as_posix() if self.sidecar_path else None
        except ValueError:
            sidecar_relative = self.sidecar_path.name if self.sidecar_path else None
        unused = self.unused_message_ids()
        return {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": ARTIFACT_TYPE,
            "provided": self.provided,
            "valid": self.provided and not self.errors and not unused,
            "segment_path": segment_relative,
            "source_artifact_sha256": self.source_artifact_sha256,
            "sidecar_path": sidecar_relative,
            "sidecar_sha256": self.sidecar_sha256,
            "sidecar_size_bytes": self.sidecar_size_bytes,
            "sidecar_resolution": self.sidecar_resolution,
            "record_count": len(self.proofs),
            "used_record_count": len(self.used_message_ids),
            "unused_record_count": len(unused),
            "unused_message_ids": unused,
            "message_ids": sorted(self.proofs, key=int),
            "content_hash_bound": bool(self.provided and self.sidecar_sha256 and not self.errors and not unused and all(p.get("source_row_sha256") for p in self.proofs.values())),
            "errors": sorted(set(self.errors)),
        }


def load_adjacent_forum_system_event_revalidation(segment_path: Path, payload: dict[str, Any], *, source_artifact_sha256: str, artifact_root: Path) -> ForumSystemEventRevalidation:
    segment_path, artifact_root = segment_path.resolve(), artifact_root.resolve()
    bundle = ForumSystemEventRevalidation(segment_path, artifact_root, source_artifact_sha256.lower())
    candidate, registered_sha, registered = _registered_external_sidecar(bundle)
    if registered:
        if candidate is None:
            return bundle
    else:
        candidate = sidecar_path(segment_path)
        if not candidate.is_file():
            return bundle
        bundle.provided = True
        bundle.sidecar_path = candidate
        bundle.sidecar_resolution = "adjacent"
    try:
        sidecar, bundle.sidecar_sha256, bundle.sidecar_size_bytes = _stable_read_object(candidate)
    except (OSError, ValueError) as exc:
        bundle.errors.append(f"sidecar_unreadable:{exc}")
        return bundle
    if registered_sha is not None and bundle.sidecar_sha256 != registered_sha:
        bundle.errors.append("external_sidecar_registered_sha256_mismatch")
        return bundle
    # A revalidated copy is always bound back to the immutable staged source;
    # its sidecar therefore has a deliberately different envelope from the
    # pre-capture/recovery sidecar above.
    if isinstance(sidecar.get("source_original"), dict):
        if not (
            sidecar.get("schema_version") == SCHEMA_VERSION
            and sidecar.get("artifact_type") == ARTIFACT_TYPE
            and sidecar.get("source_scope") == "discord_only"
            and sidecar.get("outside_sources_used") is False
        ):
            bundle.errors.append("revalidated_copy_sidecar_envelope_mismatch")
            return bundle
        if sidecar.get("contract_profile") == (
            "premium_forum_title_change_revalidated_copy_v1"
        ):
            return _load_profiled_revalidated_copy_v1(bundle, sidecar, payload)
        if "contract_profile" in sidecar:
            bundle.errors.append("revalidated_copy_contract_profile_unknown")
            return bundle
        return _load_revalidated_copy(bundle, sidecar, payload)
    try:
        expected_path = segment_path.relative_to(artifact_root).as_posix()
    except ValueError:
        expected_path = ""; bundle.errors.append("segment_outside_artifact_root")
    for field, expected in (("schema_version", SCHEMA_VERSION), ("artifact_type", ARTIFACT_TYPE), ("source_scope", "discord_only"), ("outside_sources_used", False), ("source_artifact_path", expected_path), ("source_artifact_sha256", source_artifact_sha256.lower()), ("source_artifact_bytes", segment_path.stat().st_size)):
        if sidecar.get(field) != expected:
            bundle.errors.append(f"sidecar_{field}_mismatch")
    segment = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
    container = payload.get("requested_container") if isinstance(payload.get("requested_container"), dict) else {}
    if not (payload.get("guild_id") == GUILD_ID and container.get("channel_id") == PARENT_FORUM_ID and segment.get("start") == "2026-01-06" and segment.get("end") == "2026-01-06" and segment.get("timezone") == "America/Chicago" and segment.get("query") == "in:premium-journals after:2026-01-05 before:2026-01-07"):
        bundle.errors.append("source_segment_scope_not_exact_jan6_premium_forum")
    records = sidecar.get("revalidations")
    if not isinstance(records, list) or not records:
        bundle.errors.append("sidecar_revalidations_missing")
        return bundle
    rows = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    for index, record in enumerate(records, 1):
        prefix = f"record_{index}"
        if not isinstance(record, dict):
            bundle.errors.append(f"{prefix}_not_object"); continue
        message_id = str(record.get("message_id") or "")
        if not ID_RE.fullmatch(message_id) or message_id in bundle.proofs:
            bundle.errors.append(f"{prefix}_message_id_invalid_or_duplicate"); continue
        matching = [row for row in rows if isinstance(row, dict) and str(row.get("message_id") or "") == message_id]
        if len(matching) != 1:
            bundle.errors.append(f"{prefix}_source_message_row_count_not_one"); continue
        row = matching[0]
        if record.get("status") != "passed" or record.get("evidence_type") != EVIDENCE_TYPE:
            bundle.errors.append(f"{prefix}_status_or_evidence_type_mismatch")
        if record.get("result_index") != row.get("result_index") or record.get("source_row_sha256") != row_sha256(row):
            bundle.errors.append(f"{prefix}_row_binding_mismatch")
        correction = _expected_correction(str(row.get("timestamp_utc") or ""))
        if record.get("effective_correction") != correction:
            bundle.errors.append(f"{prefix}_effective_correction_mismatch")
        route = record.get("route") if isinstance(record.get("route"), dict) else {}
        expected_route = {"guild_id": GUILD_ID, "parent_forum_channel_id": PARENT_FORUM_ID, "start": "2026-01-06", "end": "2026-01-06", "timezone": "America/Chicago", "query": "in:premium-journals after:2026-01-05 before:2026-01-07", "page_number": row.get("page_number"), "forum_group_navigation_evidence_key": row.get("forum_group_navigation_evidence_key"), "exact_permalink": row.get("exact_permalink")}
        if route != expected_route:
            bundle.errors.append(f"{prefix}_route_or_navigation_mismatch")
        if not (
            row.get("discord_system_event_exact") is False
            and row.get("discord_system_event_type") is None
            and row.get("timestamp_exact_fallback_source") is None
        ):
            bundle.errors.append(f"{prefix}_source_row_not_unclassified_system_event")
        dom = record.get("dom_evidence") if isinstance(record.get("dom_evidence"), dict) else {}
        expected_dom = {"article_id": row.get("article_id"), "article_aria_labelledby": row.get("article_aria_labelledby"), "author": row.get("author"), "author_id": row.get("author_id"), "row_owned_time_count": 1, "row_owned_time_datetime": row.get("timestamp_utc"), "row_owned_time_element_id": None, "content_text": row.get("content_text"), "system_event_dom_exact": True, "system_event_dom_marker": DOM_EVENT_MARKER, "system_event_dom_marker_article_id": row.get("article_id"), "system_event_dom_marker_message_id": message_id}
        if dom != expected_dom:
            bundle.errors.append(f"{prefix}_dom_evidence_mismatch")
        effective = copy.deepcopy(row); effective.update(correction)
        if not exact_forum_post_title_system_event_fallback(effective, message_id):
            bundle.errors.append(f"{prefix}_effective_forum_system_event_fallback_not_exact")
        bundle.proofs[message_id] = {"message_id": message_id, "source_row_sha256": row_sha256(row), "effective_correction": correction}
    return bundle


def timestamp_scope_mode(row: dict[str, Any], bundle: ForumSystemEventRevalidation | None) -> str | None:
    if bundle is None:
        return None
    proof = bundle.proof_for(row)
    if proof is None:
        return None
    effective = copy.deepcopy(row); effective.update(proof["effective_correction"])
    if exact_forum_post_title_system_event_fallback(effective, str(row.get("message_id") or "")):
        return f"{FALLBACK_SOURCE}_sidecar_revalidated"
    return None
