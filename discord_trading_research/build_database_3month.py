from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import build_database as base


DEFAULT_RAW = Path(__file__).with_name("raw_discord_export_3month.json")
DEFAULT_CURATED = Path(__file__).with_name("curated_analysis_3month.json")
DEFAULT_OUTPUT = Path(__file__).with_name("discord_trading_research_3month.sqlite")
DEFAULT_BROWSER_CONTEXT_FOLLOWUPS = Path(__file__).with_name(
    "browser_context_followups_3month.json"
)
LEGACY_RAW = Path(__file__).with_name("raw_discord_export.json")
LEGACY_CURATED = Path(__file__).with_name("curated_analysis.json")
LEGACY_OUTPUT = Path(__file__).with_name("discord_trading_research.sqlite")

THREE_MONTH_COLLECTION_KEYS = tuple(
    dict.fromkeys((*base.COLLECTION_KEYS, "instrument_comparison_messages"))
)
BROWSER_CONTEXT_COLLECTION = "browser_context_followup_messages"
BROWSER_CONTEXT_STATUSES = {
    "answered",
    "unresolved",
    "partially_answered",
    "community_answer_only",
}

THREE_MONTH_DOCUMENTS = {
    "rb_analysis_3month": "rb_analysis_3month.json",
    "trade_analysis_3month": "trade_analysis_3month.json",
    "model_analysis_3month": "model_analysis_3month.json",
    "research_summary_3month": "RESEARCH_SUMMARY_3MONTH.md",
    "llm_readme_3month": "README_3MONTH_DATABASE.md",
}


def read_json_required(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(
            f"Required {label} does not exist: {path}. "
            "No database was created; supply the real three-month artifact first."
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a top-level JSON object: {path}")
    return value


def parse_boundary(value: Any, label: str) -> tuple[str, dt.datetime]:
    text = str(value or "").strip()
    if not text:
        raise ValueError(
            f"Missing {label}. Provide it in raw metadata or with the matching CLI option."
        )
    date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", text))
    if date_only:
        text = f"{text}T00:00:00Z"
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"Invalid {label} {value!r}; use ISO-8601.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone unless it is a YYYY-MM-DD date token.")
    parsed = parsed.astimezone(dt.timezone.utc)
    normalized = parsed.isoformat().replace("+00:00", "Z")
    return normalized, parsed


def resolve_window(
    metadata: dict[str, Any],
    start_override: str | None,
    end_override: str | None,
) -> tuple[str, str, dt.datetime, dt.datetime, float]:
    merge = metadata.get("merge") if isinstance(metadata.get("merge"), dict) else {}
    merge_window = merge.get("window") if isinstance(merge.get("window"), dict) else {}
    inclusive_merge_dates = False
    if bool(start_override) != bool(end_override):
        raise ValueError("Provide both --window-start and --window-end, or neither.")
    if start_override or end_override:
        start_value = start_override
        end_value = end_override
    elif merge.get("window_start_utc") or merge.get("window_end_utc"):
        start_value = merge.get("window_start_utc")
        end_value = merge.get("window_end_utc")
    elif merge.get("requested_window_start_date") or merge.get("requested_window_end_date"):
        start_value = merge.get("requested_window_start_date")
        end_value = merge.get("requested_window_end_date")
        inclusive_merge_dates = True
    elif merge_window:
        start_value = (
            merge_window.get("window_start_utc")
            or merge_window.get("start")
            or merge_window.get("after")
        )
        end_value = (
            merge_window.get("window_end_utc")
            or merge_window.get("end")
            or merge_window.get("before")
        )
    else:
        start_value = metadata.get("window_start_utc") or metadata.get("requested_window_start_date")
        end_value = metadata.get("window_end_utc") or metadata.get("requested_window_end_date")
    start_text, start_dt = parse_boundary(start_value, "three-month window start")
    end_text, end_dt = parse_boundary(end_value, "three-month window end")
    if inclusive_merge_dates:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(start_value or "")) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", str(end_value or "")
        ):
            raise ValueError(
                "metadata.merge requested_window dates must be YYYY-MM-DD inclusive calendar dates."
            )
        end_dt += dt.timedelta(days=1)
        end_text = end_dt.isoformat().replace("+00:00", "Z")
    duration_days = (end_dt - start_dt).total_seconds() / 86400
    expected_start = dt.datetime(2026, 4, 20, tzinfo=dt.timezone.utc)
    expected_end = dt.datetime(2026, 7, 21, tzinfo=dt.timezone.utc)
    if start_dt != expected_start or end_dt != expected_end:
        raise ValueError(
            "This production corpus must use the exact 92-day half-open window "
            "2026-04-20T00:00:00Z through 2026-07-21T00:00:00Z."
        )
    return start_text, end_text, start_dt, end_dt, duration_days


def discover_collection_keys(raw: dict[str, Any]) -> tuple[str, ...]:
    missing_expected = [key for key in THREE_MONTH_COLLECTION_KEYS if key not in raw]
    if missing_expected:
        raise ValueError(
            "Merged raw export is missing required collection arrays: "
            + ", ".join(missing_expected)
        )
    keys: list[str] = []
    for key in THREE_MONTH_COLLECTION_KEYS:
        if key in raw:
            keys.append(key)
    for key, value in raw.items():
        if key in keys or key == "metadata" or not isinstance(value, list):
            continue
        if any(isinstance(item, dict) and item.get("message_id") for item in value):
            keys.append(key)
    return tuple(keys)


def collect_messages(
    raw: dict[str, Any], collection_keys: Iterable[str]
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    messages: dict[str, dict[str, Any]] = {}
    sources: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    merged_provenance: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for collection_name in collection_keys:
        rows = raw.get(collection_name)
        if not isinstance(rows, list):
            raise ValueError(f"Raw collection {collection_name!r} must be a JSON array.")
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise ValueError(f"{collection_name}[{index}] is not a JSON object.")
            message_id = str(row.get("message_id") or "").strip()
            if not re.fullmatch(r"\d{15,22}", message_id):
                raise ValueError(f"{collection_name}[{index}] has no valid Discord message_id.")
            messages[message_id] = base.merge_message(messages.get(message_id, {}), row)
            provenance = row.get("_merge_provenance")
            provenance_sources = provenance.get("sources") if isinstance(provenance, dict) else None
            observed_queries: set[str] = set()
            if isinstance(provenance_sources, list) and provenance_sources:
                for source in provenance_sources:
                    if not isinstance(source, dict):
                        continue
                    source_collection = str(source.get("collection") or collection_name)
                    source_query = str(source.get("query") or "").strip()
                    if source_query:
                        observed_queries.add(source_query)
                    sources[message_id].append(
                        {
                            "collection_name": source_collection,
                            "query": source_query,
                            "result_index": source.get("result_index") or 0,
                            "page_number": source.get("page_number") or 0,
                        }
                    )
                    merged_provenance[message_id].append(
                        {
                            "source_file": str(source.get("source_file") or ""),
                            "source_collection": source_collection,
                            "source_query": source_query,
                            "segment_start": source.get("segment_start"),
                            "segment_end": source.get("segment_end"),
                            "complete_source": coerce_bool(source.get("complete_source")),
                            "source_json": base.compact_json(source),
                        }
                    )
            else:
                source_query = str(row.get("search_query") or row.get("source_query") or "").strip()
                if source_query:
                    observed_queries.add(source_query)
                sources[message_id].append(
                    {
                        "collection_name": collection_name,
                        "query": source_query,
                        "result_index": row.get("result_index") or 0,
                        "page_number": row.get("page_number") or 0,
                    }
                )
                fallback_source = {
                    "source_file": "",
                    "collection": collection_name,
                    "query": source_query,
                    "result_index": row.get("result_index"),
                    "page_number": row.get("page_number"),
                    "complete_source": None,
                }
                merged_provenance[message_id].append(
                    {
                        "source_file": "",
                        "source_collection": collection_name,
                        "source_query": source_query,
                        "segment_start": None,
                        "segment_end": None,
                        "complete_source": None,
                        "source_json": base.compact_json(fallback_source),
                    }
                )

            query_candidates: list[Any] = []
            if isinstance(provenance, dict):
                provenance_queries = provenance.get("source_queries") or []
                query_candidates.extend(
                    provenance_queries if isinstance(provenance_queries, list) else [provenance_queries]
                )
            row_queries = row.get("source_queries") or []
            query_candidates.extend(row_queries if isinstance(row_queries, list) else [row_queries])
            query_candidates.extend((row.get("search_query"), row.get("source_query")))
            for value in query_candidates:
                source_query = str(value or "").strip()
                if not source_query or source_query in observed_queries:
                    continue
                source_collection = collection_for_query(source_query) or collection_name
                sources[message_id].append(
                    {
                        "collection_name": source_collection,
                        "query": source_query,
                        "result_index": 0,
                        "page_number": 0,
                    }
                )
                synthetic_source = {
                    "source_file": "",
                    "collection": source_collection,
                    "query": source_query,
                    "complete_source": None,
                    "provenance_note": "Query retained from merged source_queries; no separate source descriptor was present.",
                }
                merged_provenance[message_id].append(
                    {
                        "source_file": "",
                        "source_collection": source_collection,
                        "source_query": source_query,
                        "segment_start": None,
                        "segment_end": None,
                        "complete_source": None,
                        "source_json": base.compact_json(synthetic_source),
                    }
                )
                observed_queries.add(source_query)
    if not messages:
        raise ValueError("Merged raw export contains no Discord messages; refusing to create an empty database.")
    return messages, sources, merged_provenance


def inherited_baseline_query_limitation(
    provenance: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    affected_messages: set[str] = set()
    affected_collections: set[str] = set()
    descriptor_count = 0
    for message_id, records in provenance.items():
        for record in records:
            source_file = Path(str(record.get("source_file") or "")).name
            source_collection = str(record.get("source_collection") or "")
            source_query = str(record.get("source_query") or "")
            if (
                source_file == LEGACY_RAW.name
                and source_collection != "primary_messages"
                and "premium-journals" in source_query.lower()
            ):
                descriptor_count += 1
                affected_messages.add(message_id)
                affected_collections.add(source_collection)
    note = ""
    if descriptor_count:
        note = (
            "Inherited baseline source-metadata limitation: some non-primary rows in "
            f"{LEGACY_RAW.name} carry the primary premium-journals search_query string. "
            "Those source descriptors are retained losslessly and are not rewritten; use "
            "source_collection plus the embedded raw merge metadata and collection coverage "
            "for intended supplemental-query context."
        )
    return {
        "descriptor_count": descriptor_count,
        "affected_message_count": len(affected_messages),
        "affected_collections": sorted(affected_collections),
        "note": note,
    }


def parse_message_timestamp(value: Any, context: str) -> dt.datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Missing timestamp_utc for {context}.")
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp_utc {text!r} for {context}.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp_utc lacks a timezone for {context}.")
    return parsed.astimezone(dt.timezone.utc)


def validate_raw_scope(
    raw: dict[str, Any],
    metadata: dict[str, Any],
    collection_keys: Iterable[str],
    start_dt: dt.datetime,
    end_dt: dt.datetime,
) -> None:
    scope = str(metadata.get("source_scope") or raw.get("source_scope") or "").strip().lower()
    if scope.replace(" ", "_") != "discord_only":
        raise ValueError("Raw metadata must explicitly declare source_scope='discord_only'.")
    if str(metadata.get("guild_id") or "") != base.GUILD_ID:
        raise ValueError("Raw metadata guild_id is missing or does not match the scoped Discord server.")
    if str(metadata.get("primary_channel_id") or "") != base.PRIMARY_CHANNEL_ID:
        raise ValueError("Raw metadata primary_channel_id is missing or does not match premium-journals.")

    unknown_collections = sorted(set(collection_keys) - set(THREE_MONTH_COLLECTION_KEYS))
    if unknown_collections:
        raise ValueError(
            "Raw export contains undeclared collection arrays: " + ", ".join(unknown_collections)
        )

    outside: list[str] = []
    invalid_provenance: list[str] = []
    for collection_name in collection_keys:
        for row in raw.get(collection_name, []):
            message_id = str(row.get("message_id") or "")
            timestamp = parse_message_timestamp(row.get("timestamp_utc"), f"message {message_id}")
            if timestamp < start_dt or timestamp >= end_dt:
                outside.append(message_id)
                if len(outside) >= 10:
                    break
            provenance = row.get("_merge_provenance")
            provenance_sources = provenance.get("sources") if isinstance(provenance, dict) else []
            if not isinstance(provenance, dict) or not isinstance(provenance_sources, list) or not provenance_sources:
                invalid_provenance.append(f"{message_id}: missing merged source descriptors")
                if len(invalid_provenance) >= 10:
                    break
                continue
            for source in provenance_sources or []:
                if not isinstance(source, dict):
                    invalid_provenance.append(f"{message_id}: non-object source descriptor")
                    continue
                source_collection = str(source.get("collection") or "")
                source_query = str(source.get("query") or "")
                source_file = str(source.get("source_file") or "")
                if source_collection not in THREE_MONTH_COLLECTION_KEYS:
                    invalid_provenance.append(
                        f"{message_id}: unknown source collection {source_collection!r}"
                    )
                if re.search(r"https?://", source_query, re.I):
                    invalid_provenance.append(f"{message_id}: non-query URL in source query")
                if source_file and Path(source_file).suffix.lower() != ".json":
                    invalid_provenance.append(f"{message_id}: non-JSON source file {source_file!r}")
                if len(invalid_provenance) >= 10:
                    break
            if len(invalid_provenance) >= 10:
                break
        if len(outside) >= 10 or len(invalid_provenance) >= 10:
            break
    if outside:
        raise ValueError(
            "Raw export contains messages outside the declared three-month boundaries; "
            f"first IDs: {', '.join(outside)}. Fix the merge instead of silently filtering them."
        )
    if invalid_provenance:
        raise ValueError(
            "Merged provenance is not restricted to declared Discord collection metadata; first issues: "
            + "; ".join(invalid_provenance)
        )


def validate_browser_context_followups(
    path: Path,
    start_text: str,
    end_text: str,
    start_dt: dt.datetime,
    end_dt: dt.datetime,
) -> dict[str, Any]:
    artifact = read_json_required(path, "audited browser context follow-ups")
    scope = artifact.get("scope")
    methodology = artifact.get("methodology")
    contexts = artifact.get("contexts")
    context_messages = artifact.get("messages")
    if artifact.get("schema_version") != "1.0.0":
        raise ValueError("Browser context follow-ups must use schema_version='1.0.0'.")
    if not isinstance(scope, dict) or not isinstance(methodology, dict):
        raise ValueError("Browser context follow-ups must contain scope and methodology objects.")
    if not isinstance(contexts, list) or not isinstance(context_messages, list):
        raise ValueError("Browser context follow-ups must contain contexts and messages arrays.")
    if str(scope.get("guild_id") or "") != base.GUILD_ID:
        raise ValueError("Browser context follow-ups guild_id does not match the scoped Discord server.")
    if scope.get("window_start_utc") != start_text or scope.get("window_end_exclusive_utc") != end_text:
        raise ValueError("Browser context follow-up boundaries do not match the three-month corpus window.")
    if scope.get("captured_contexts") != 8 or len(contexts) != 8:
        raise ValueError("Browser context follow-ups must contain the eight audited permalink contexts.")
    if methodology.get("outside_sources_used") is not False:
        raise ValueError("Browser context follow-ups must declare outside_sources_used=false.")
    for field in ("source", "selection", "completeness_boundary", "answer_linkage", "authority_caution"):
        if not str(methodology.get(field) or "").strip():
            raise ValueError(f"Browser context methodology is missing {field!r}.")
    completeness_boundary = str(methodology.get("completeness_boundary") or "").lower()
    if "not a complete export" not in completeness_boundary:
        raise ValueError(
            "Browser context completeness_boundary must explicitly say the capture is not channel-wide."
        )

    context_by_id: dict[str, dict[str, Any]] = {}
    target_ids: set[str] = set()
    for index, context in enumerate(contexts, start=1):
        if not isinstance(context, dict):
            raise ValueError(f"Browser context {index} is not an object.")
        context_id = str(context.get("context_id") or "").strip()
        target_id = str(context.get("target_message_id") or "").strip()
        status = str(context.get("status") or "").strip()
        if not context_id or context_id in context_by_id:
            raise ValueError(f"Browser context {index} has a missing or duplicate context_id.")
        if not re.fullmatch(r"\d{15,22}", target_id) or target_id in target_ids:
            raise ValueError(f"Browser context {context_id!r} has an invalid or duplicate target ID.")
        if status not in BROWSER_CONTEXT_STATUSES:
            raise ValueError(f"Browser context {context_id!r} has unknown status {status!r}.")
        if not str(context.get("resolution") or "").strip():
            raise ValueError(f"Browser context {context_id!r} is missing its resolution/status note.")
        context_by_id[context_id] = context
        target_ids.add(target_id)

    if len(context_messages) != 35:
        raise ValueError("Browser context follow-ups must contain exactly 35 audited messages.")
    message_ids: set[str] = set()
    contexts_with_messages: set[str] = set()
    targets_observed: set[str] = set()
    discord_url_pattern = re.compile(
        rf"^https://discord\.com/channels/{base.GUILD_ID}/(?P<channel>\d{{15,22}})/(?P<target>\d{{15,22}})$"
    )
    for index, row in enumerate(context_messages, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Browser follow-up message {index} is not an object.")
        message_id = str(row.get("message_id") or "").strip()
        context_id = str(row.get("context_id") or "").strip()
        channel_id = str(row.get("channel_id") or "").strip()
        if not re.fullmatch(r"\d{15,22}", message_id) or message_id in message_ids:
            raise ValueError(f"Browser follow-up message {index} has an invalid or duplicate ID.")
        if context_id not in context_by_id:
            raise ValueError(f"Browser follow-up message {message_id} has unknown context_id {context_id!r}.")
        if str(row.get("guild_id") or "") != base.GUILD_ID:
            raise ValueError(f"Browser follow-up message {message_id} has the wrong guild_id.")
        if not re.fullmatch(r"\d{15,22}", channel_id):
            raise ValueError(f"Browser follow-up message {message_id} has no exact Discord channel_id.")
        timestamp = parse_message_timestamp(
            row.get("timestamp_utc"), f"browser follow-up message {message_id}"
        )
        if timestamp < start_dt or timestamp >= end_dt:
            raise ValueError(f"Browser follow-up message {message_id} is outside the corpus window.")
        if row.get("collection_method") != "direct_permalink_visible_context":
            raise ValueError(f"Browser follow-up message {message_id} has an unknown collection method.")
        url_match = discord_url_pattern.fullmatch(str(row.get("source_url") or ""))
        expected_target = str(context_by_id[context_id]["target_message_id"])
        if not url_match or url_match.group("channel") != channel_id or url_match.group("target") != expected_target:
            raise ValueError(
                f"Browser follow-up message {message_id} source_url does not match its context target."
            )
        message_ids.add(message_id)
        contexts_with_messages.add(context_id)
        if message_id == expected_target:
            targets_observed.add(message_id)
    if contexts_with_messages != set(context_by_id):
        raise ValueError("One or more audited browser contexts have no captured messages.")
    if targets_observed != target_ids:
        raise ValueError("One or more audited target messages are absent from the 35-message capture.")
    return artifact


def merge_browser_context_messages(
    messages: dict[str, dict[str, Any]],
    sources: dict[str, list[dict[str, Any]]],
    provenance: dict[str, list[dict[str, Any]]],
    artifact: dict[str, Any],
    source_path: Path,
) -> set[str]:
    contexts = {
        str(item["context_id"]): item
        for item in artifact["contexts"]
        if isinstance(item, dict)
    }
    methodology = artifact["methodology"]
    included_ids: set[str] = set()
    for result_index, captured in enumerate(artifact["messages"], start=1):
        row = dict(captured)
        message_id = str(row["message_id"])
        context_id = str(row["context_id"])
        channel_id = str(row["channel_id"])
        context = contexts[context_id]
        query = f"direct_permalink_context:{context_id}"
        row.update(
            {
                "visible_text": row.get("content_text") or "",
                "inferred_thread_channel_id": channel_id,
                "group_label": f"targeted-browser-context:{channel_id}",
                "search_query": query,
                "result_index": result_index,
                "page_number": 1,
                "inferred_permalink": (
                    f"https://discord.com/channels/{base.GUILD_ID}/{channel_id}/{message_id}"
                ),
            }
        )
        merged_message = base.merge_message(messages.get(message_id, {}), row)
        # The audited browser row exposes an exact channel ID. Override any
        # older search-result surrogate location and malformed inferred URL,
        # while retaining both original source descriptors in provenance.
        merged_message.update(
            {
                "inferred_thread_channel_id": channel_id,
                "group_label": f"targeted-browser-context:{channel_id}",
                "thread_title": "",
                "parent_channel": "",
                "inferred_permalink": (
                    f"https://discord.com/channels/{base.GUILD_ID}/{channel_id}/{message_id}"
                ),
            }
        )
        messages[message_id] = merged_message
        sources[message_id].append(
            {
                "collection_name": BROWSER_CONTEXT_COLLECTION,
                "query": query,
                "result_index": result_index,
                "page_number": 1,
            }
        )
        descriptor = {
            "source_file": str(source_path.resolve()),
            "collection": BROWSER_CONTEXT_COLLECTION,
            "query": query,
            "result_index": result_index,
            "page_number": 1,
            "complete_source": True,
            "collection_method": captured.get("collection_method"),
            "source_url": captured.get("source_url"),
            "context_id": context_id,
            "target_message_id": context.get("target_message_id"),
            "context_status": context.get("status"),
            "context_resolution": context.get("resolution"),
            "authority_caution": methodology.get("authority_caution"),
            "answer_linkage": methodology.get("answer_linkage"),
            "captured_message": captured,
        }
        provenance[message_id].append(
            {
                "source_file": str(source_path.resolve()),
                "source_collection": BROWSER_CONTEXT_COLLECTION,
                "source_query": query,
                "segment_start": None,
                "segment_end": None,
                "complete_source": True,
                "source_json": base.compact_json(descriptor),
            }
        )
        included_ids.add(message_id)
    return included_ids


def coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "complete", "completed", "yes", "1"}:
            return True
        if normalized in {"false", "partial", "incomplete", "no", "0", "unknown"}:
            return False
    return None


def collection_for_query(query: str) -> str | None:
    lower = query.lower().strip()
    if not lower:
        return None
    if "jump_context:" in lower or "message-context" in lower:
        return "contextual_qa_messages"
    if "premium-journals" in lower:
        return "primary_messages"
    if "rejection block" in lower:
        return "server_rejection_phrase_messages"
    if "questions" in lower and "nq" in lower and "es" in lower:
        return "questions_nq_es_messages"
    if "questions" in lower and re.search(r"\brb\b", lower):
        return "questions_rb_messages"
    if re.match(r"^\s*rb\s+(?:nq|es)\b", lower):
        return "instrument_comparison_messages"
    if re.match(r"^\s*rb\b", lower):
        return "broad_rb_shorthand_partial_messages"
    return None


def extract_declared_coverage(
    metadata: dict[str, Any], collection_keys: Iterable[str]
) -> dict[str, list[dict[str, Any]]]:
    known = set(collection_keys)
    declarations: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    seen_nodes: set[int] = set()

    def add(name: str | None, node: dict[str, Any]) -> None:
        query_value = node.get("query_text") or node.get("query") or node.get("search_query")
        queries = node.get("queries") or node.get("source_queries")
        if not query_value and isinstance(queries, list):
            query_value = " || ".join(str(item) for item in queries if item)
        resolved_name = str(name or node.get("collection_name") or node.get("name") or "").strip()
        if not resolved_name:
            resolved_name = collection_for_query(str(query_value or "")) or ""
        if resolved_name not in known:
            return
        complete = None
        for field in ("scan_complete", "complete", "completed", "is_complete", "status"):
            if field in node:
                complete = coerce_bool(node.get(field))
                if complete is not None:
                    break
        if complete is None:
            return
        raw_gap = node.get("gap_notes") or node.get("gap_note") or node.get("notes") or ""
        if isinstance(raw_gap, list):
            gap = " ".join(str(item).strip() for item in raw_gap if str(item).strip())
        else:
            gap = str(raw_gap).strip()
        declarations[resolved_name].append(
            {
                "query": str(query_value or "").strip(),
                "complete": complete,
                "gap": gap,
                "declared_count": node.get("messages_seen")
                or node.get("declared_messages_seen")
                or node.get("result_count")
                or node.get("captured_result_count"),
            }
        )

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            marker = id(value)
            if marker in seen_nodes:
                return
            seen_nodes.add(marker)
            add(None, value)
            for field in ("completeness", "collection_completeness", "collection_status"):
                mapping = value.get(field)
                if isinstance(mapping, dict):
                    for name, status in mapping.items():
                        if isinstance(status, dict):
                            add(str(name), status)
                        else:
                            add(str(name), {"complete": status})
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    walk(child)

    walk(metadata)
    primary_complete = coerce_bool(metadata.get("primary_search_complete"))
    if primary_complete is not None:
        declarations["primary_messages"].append(
            {
                "query": str(metadata.get("primary_search_query") or "").strip(),
                "complete": primary_complete,
                "gap": "",
                "declared_count": metadata.get("primary_result_count"),
            }
        )
    return declarations


def make_coverage_rows(
    raw: dict[str, Any], metadata: dict[str, Any], collection_keys: Iterable[str]
) -> list[dict[str, Any]]:
    declarations = extract_declared_coverage(metadata, collection_keys)
    rows: list[dict[str, Any]] = []
    for collection_name in collection_keys:
        messages = raw.get(collection_name, [])
        times = sorted(
            str(row.get("timestamp_utc"))
            for row in messages
            if isinstance(row, dict) and row.get("timestamp_utc")
        )
        declared = declarations.get(collection_name, [])
        complete = bool(declared) and all(item["complete"] for item in declared)
        queries = sorted({item["query"] for item in declared if item.get("query")})
        if not queries:
            queries = sorted(
                {
                    str(row.get("search_query") or row.get("source_query") or "").strip()
                    for row in messages
                    if isinstance(row, dict) and (row.get("search_query") or row.get("source_query"))
                }
            )
        gaps = sorted({item["gap"] for item in declared if item.get("gap")})
        if not declared:
            gaps.append("Completeness was not declared in the merged raw metadata; marked partial conservatively.")
        elif not complete and not gaps:
            gaps.append("At least one contributing collection segment was declared incomplete.")
        declared_counts = [item.get("declared_count") for item in declared if item.get("declared_count") is not None]
        if declared_counts:
            gaps.append(
                "Declared source-result counts are retained in raw merge metadata; messages_seen here is the actual merged row count."
            )
        rows.append(
            {
                "collection_name": collection_name,
                "query_text": " || ".join(queries) or f"merged:{collection_name}",
                "scan_complete": int(complete),
                "messages_seen": len(messages),
                "earliest_message_utc": times[0] if times else None,
                "latest_message_utc": times[-1] if times else None,
                "gap_notes": " ".join(gaps) or None,
            }
        )
    return rows


def insert_run_and_coverage(
    conn: sqlite3.Connection,
    metadata: dict[str, Any],
    start_text: str,
    end_text: str,
    coverage_rows: list[dict[str, Any]],
    source_metadata_limitation: str,
) -> str:
    incomplete = [row["collection_name"] for row in coverage_rows if not row["scan_complete"]]
    status = "partial" if incomplete else "complete"
    declared_limitations = metadata.get("limitations")
    if isinstance(declared_limitations, list):
        declared_limitations = " ".join(str(item) for item in declared_limitations)
    limitations = " ".join(
        part
        for part in (
            str(declared_limitations or "").strip(),
            source_metadata_limitation.strip(),
            f"Incomplete or undeclared collections: {', '.join(incomplete)}." if incomplete else "",
            "Discord text and attachment metadata are stored; chart images are not automatically reinterpreted.",
            "Post timestamps are not assumed to equal setup times.",
        )
        if part
    )
    methodology = str(metadata.get("methodology") or "").strip() or (
        "Discord-only merged browser collection. Source message IDs are deduplicated across declared "
        "collection arrays, while collection/query provenance remains attached to each included message."
    )
    merge = metadata.get("merge") if isinstance(metadata.get("merge"), dict) else {}
    conn.execute(
        """
        INSERT INTO research_runs(
          run_id,schema_version,guild_id,guild_name,primary_channel_id,primary_channel_name,
          window_start_utc,window_end_utc,collected_at_utc,source_scope,status,methodology,limitations
        ) VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            base.SCHEMA_VERSION,
            metadata["guild_id"],
            metadata.get("guild_name") or "unknown",
            metadata["primary_channel_id"],
            metadata.get("primary_channel_name") or "premium-journals",
            start_text,
            end_text,
            merge.get("generated_at_utc")
            or metadata.get("collected_at_utc")
            or dt.datetime.now(dt.timezone.utc).isoformat(),
            "discord_only",
            status,
            methodology,
            limitations,
        ),
    )
    for row in coverage_rows:
        conn.execute(
            """
            INSERT INTO collection_coverage(
              run_id,collection_name,query_text,scan_complete,messages_seen,
              earliest_message_utc,latest_message_utc,gap_notes
            ) VALUES(1,?,?,?,?,?,?,?)
            """,
            (
                row["collection_name"],
                row["query_text"],
                row["scan_complete"],
                row["messages_seen"],
                row["earliest_message_utc"],
                row["latest_message_utc"],
                row["gap_notes"],
            ),
        )
    return status


def create_three_month_extension_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE merged_message_provenance (
          provenance_id TEXT PRIMARY KEY,
          message_id TEXT NOT NULL REFERENCES messages(message_id),
          source_file TEXT NOT NULL DEFAULT '',
          source_collection TEXT NOT NULL CHECK(source_collection IN (
            'primary_messages',
            'server_rejection_phrase_messages',
            'questions_rb_messages',
            'questions_nq_es_messages',
            'broad_rb_shorthand_partial_messages',
            'contextual_qa_messages',
            'instrument_comparison_messages',
            'browser_context_followup_messages'
          )),
          source_query TEXT NOT NULL DEFAULT '',
          segment_start_date TEXT NOT NULL DEFAULT '',
          segment_end_date TEXT NOT NULL DEFAULT '',
          complete_source INTEGER CHECK(complete_source IN (0,1) OR complete_source IS NULL),
          source_json TEXT NOT NULL,
          UNIQUE(
            message_id,source_file,source_collection,source_query,
            segment_start_date,segment_end_date
          )
        );

        CREATE INDEX idx_merged_provenance_file
          ON merged_message_provenance(source_file);
        CREATE INDEX idx_merged_provenance_collection_query
          ON merged_message_provenance(source_collection,source_query);

        CREATE TABLE browser_context_followup_artifacts (
          artifact_id INTEGER PRIMARY KEY CHECK(artifact_id=1),
          schema_version TEXT NOT NULL,
          source_file TEXT NOT NULL,
          guild_id TEXT NOT NULL,
          window_start_utc TEXT NOT NULL,
          window_end_utc TEXT NOT NULL,
          captured_contexts INTEGER NOT NULL CHECK(captured_contexts=8),
          purpose TEXT NOT NULL,
          source_description TEXT NOT NULL,
          outside_sources_used INTEGER NOT NULL CHECK(outside_sources_used=0),
          selection TEXT NOT NULL,
          completeness_boundary TEXT NOT NULL,
          answer_linkage TEXT NOT NULL,
          authority_caution TEXT NOT NULL,
          methodology_json TEXT NOT NULL,
          source_json TEXT NOT NULL
        );

        CREATE TABLE browser_followup_contexts (
          context_id TEXT PRIMARY KEY,
          artifact_id INTEGER NOT NULL REFERENCES browser_context_followup_artifacts(artifact_id),
          target_message_id TEXT NOT NULL UNIQUE REFERENCES messages(message_id),
          status TEXT NOT NULL CHECK(status IN (
            'answered','unresolved','partially_answered','community_answer_only'
          )),
          resolution TEXT NOT NULL
        );

        CREATE TABLE browser_followup_context_messages (
          context_id TEXT NOT NULL REFERENCES browser_followup_contexts(context_id),
          message_id TEXT NOT NULL REFERENCES messages(message_id),
          context_source_url TEXT NOT NULL,
          collection_method TEXT NOT NULL CHECK(collection_method='direct_permalink_visible_context'),
          author_as_captured TEXT,
          authority_class TEXT NOT NULL CHECK(authority_class IN ('domme','non_domme')),
          is_target_message INTEGER NOT NULL CHECK(is_target_message IN (0,1)),
          PRIMARY KEY(context_id,message_id)
        );

        CREATE INDEX idx_browser_followup_message
          ON browser_followup_context_messages(message_id);

        CREATE VIEW v_merged_message_provenance AS
        SELECT p.message_id,m.created_at_utc,m.author_display_name,
               p.source_collection,p.source_query,p.source_file,
               p.segment_start_date,p.segment_end_date,p.complete_source,
               m.permalink
        FROM merged_message_provenance p
        JOIN messages m ON m.message_id=p.message_id;

        CREATE VIEW v_browser_context_followups AS
        SELECT c.context_id,c.status,c.resolution,c.target_message_id,
               cm.message_id,cm.is_target_message,cm.author_as_captured,cm.authority_class,
               m.created_at_utc,m.author_display_name,m.content_text,m.reply_to_message_id,
               m.permalink,cm.context_source_url,cm.collection_method,
               a.authority_caution,a.answer_linkage,a.completeness_boundary
        FROM browser_followup_contexts c
        JOIN browser_followup_context_messages cm ON cm.context_id=c.context_id
        JOIN messages m ON m.message_id=cm.message_id
        JOIN browser_context_followup_artifacts a ON a.artifact_id=c.artifact_id;
        """
    )


def insert_merged_provenance(
    conn: sqlite3.Connection,
    provenance: dict[str, list[dict[str, Any]]],
    included_ids: set[str],
) -> None:
    for message_id in sorted(included_ids):
        records = provenance.get(message_id, [])
        if not records:
            raise ValueError(f"Included message {message_id} has no merged source provenance.")
        for record in records:
            source_file = str(record.get("source_file") or "")
            source_collection = str(record.get("source_collection") or "")
            source_query = str(record.get("source_query") or "")
            segment_start = str(record.get("segment_start") or "")
            segment_end = str(record.get("segment_end") or "")
            complete = record.get("complete_source")
            fingerprint = base.compact_json(
                {
                    "message_id": message_id,
                    "source_file": source_file,
                    "source_collection": source_collection,
                    "source_query": source_query,
                    "segment_start": segment_start,
                    "segment_end": segment_end,
                }
            )
            provenance_id = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
            conn.execute(
                """
                INSERT OR IGNORE INTO merged_message_provenance(
                  provenance_id,message_id,source_file,source_collection,source_query,
                  segment_start_date,segment_end_date,complete_source,source_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    provenance_id,
                    message_id,
                    source_file,
                    source_collection,
                    source_query,
                    segment_start,
                    segment_end,
                    None if complete is None else int(bool(complete)),
                    record["source_json"],
                ),
            )


def insert_browser_context_followups(
    conn: sqlite3.Connection,
    artifact: dict[str, Any],
    source_path: Path,
    included_ids: set[str],
) -> None:
    captured_ids = {str(row["message_id"]) for row in artifact["messages"]}
    missing_ids = sorted(captured_ids - included_ids)
    if missing_ids:
        raise ValueError(
            "Audited browser context messages were not force-included in the database: "
            + ", ".join(missing_ids)
        )
    scope = artifact["scope"]
    methodology = artifact["methodology"]
    conn.execute(
        """
        INSERT INTO browser_context_followup_artifacts(
          artifact_id,schema_version,source_file,guild_id,window_start_utc,window_end_utc,
          captured_contexts,purpose,source_description,outside_sources_used,selection,
          completeness_boundary,answer_linkage,authority_caution,methodology_json,source_json
        ) VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            artifact["schema_version"],
            str(source_path.resolve()),
            scope["guild_id"],
            scope["window_start_utc"],
            scope["window_end_exclusive_utc"],
            int(scope["captured_contexts"]),
            scope["purpose"],
            methodology["source"],
            int(bool(methodology["outside_sources_used"])),
            methodology["selection"],
            methodology["completeness_boundary"],
            methodology["answer_linkage"],
            methodology["authority_caution"],
            base.compact_json(methodology),
            base.compact_json(artifact),
        ),
    )
    contexts = {str(row["context_id"]): row for row in artifact["contexts"]}
    for context_id, context in contexts.items():
        conn.execute(
            """
            INSERT INTO browser_followup_contexts(
              context_id,artifact_id,target_message_id,status,resolution
            ) VALUES(?,1,?,?,?)
            """,
            (
                context_id,
                str(context["target_message_id"]),
                str(context["status"]),
                str(context["resolution"]),
            ),
        )
    for captured in artifact["messages"]:
        context_id = str(captured["context_id"])
        message_id = str(captured["message_id"])
        conn.execute(
            """
            INSERT INTO browser_followup_context_messages(
              context_id,message_id,context_source_url,collection_method,
              author_as_captured,authority_class,is_target_message
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                context_id,
                message_id,
                str(captured["source_url"]),
                str(captured["collection_method"]),
                str(captured.get("author") or "") or None,
                "domme"
                if str(captured.get("author") or "").strip().casefold() == "domme"
                else "non_domme",
                int(message_id == str(contexts[context_id]["target_message_id"])),
            ),
        )
def insert_analysis_documents(
    conn: sqlite3.Connection,
    curated_path: Path,
    optional_paths: dict[str, Path],
    merge_metadata: dict[str, Any],
    browser_context_path: Path,
    browser_context_artifact: dict[str, Any],
) -> None:
    conn.execute(
        "INSERT INTO analysis_documents(document_name,created_by,content_json,notes) VALUES(?,?,?,?)",
        (
            "raw_merge_metadata_3month",
            "discord_merge_pipeline",
            base.compact_json(merge_metadata),
            "Verbatim metadata from raw_discord_export_3month.json; Discord-source and merge coverage metadata only.",
        ),
    )
    conn.execute(
        "INSERT INTO analysis_documents(document_name,created_by,content_json,notes) VALUES(?,?,?,?)",
        (
            "browser_context_followups_3month",
            "audited_authenticated_discord_browser_capture",
            base.compact_json(browser_context_artifact),
            (
                f"Imported verbatim from {browser_context_path.name}; complete only for the "
                "eight selected permalink contexts, not for the containing channels."
            ),
        ),
    )
    artifacts = {"curated_analysis_3month": curated_path, **optional_paths}
    for name, path in artifacts.items():
        if not path.is_file():
            continue
        if path.suffix.lower() == ".json":
            content = read_json_required(path, name)
        else:
            content = {"format": "markdown", "content": path.read_text(encoding="utf-8")}
        conn.execute(
            "INSERT INTO analysis_documents(document_name,created_by,content_json,notes) VALUES(?,?,?,?)",
            (name, "three_month_corpus_only_analysis", base.compact_json(content), f"Imported from {path.name}."),
        )


def write_meta(
    conn: sqlite3.Connection,
    raw_path: Path,
    curated_path: Path,
    output_path: Path,
    duration_days: float,
    status: str,
    primary_complete: bool,
    source_metadata_limitation: dict[str, Any],
    browser_context_path: Path,
    browser_context_artifact: dict[str, Any],
) -> None:
    rows = {
        "schema_version": base.SCHEMA_VERSION,
        "three_month_extension_schema_version": "1.0.0",
        "source_scope": "Discord only",
        "corpus_label": "three_month",
        "raw_export": str(raw_path),
        "curated_analysis": str(curated_path),
        "database_file": str(output_path),
        "browser_context_followups": str(browser_context_path),
        "browser_context_count": str(len(browser_context_artifact["contexts"])),
        "browser_context_message_count": str(len(browser_context_artifact["messages"])),
        "browser_context_completeness_boundary": str(
            browser_context_artifact["methodology"]["completeness_boundary"]
        ),
        "browser_context_authority_caution": str(
            browser_context_artifact["methodology"]["authority_caution"]
        ),
        "build_timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "primary_channel_complete": str(primary_complete).lower(),
        "requested_days": f"{duration_days:g}",
        "collection_status": status,
        "source_metadata_limitation": str(source_metadata_limitation.get("note") or ""),
        "inherited_baseline_query_descriptor_count": str(
            source_metadata_limitation.get("descriptor_count") or 0
        ),
        "inherited_baseline_query_affected_message_count": str(
            source_metadata_limitation.get("affected_message_count") or 0
        ),
        "inherited_baseline_query_affected_collections": ",".join(
            source_metadata_limitation.get("affected_collections") or []
        ),
        "important_note": "Models and probability tiers describe this selected Discord corpus, not universal market expectancy.",
    }
    conn.executemany("INSERT INTO meta(key,value) VALUES(?,?)", rows.items())


def build_three_month_database(
    raw_path: Path,
    curated_path: Path,
    browser_context_path: Path,
    output_path: Path,
    start_override: str | None,
    end_override: str | None,
    optional_documents: dict[str, Path],
) -> dict[str, Any]:
    raw_path = raw_path.resolve()
    curated_path = curated_path.resolve()
    browser_context_path = browser_context_path.resolve()
    output_path = output_path.resolve()
    if raw_path == LEGACY_RAW.resolve() or curated_path == LEGACY_CURATED.resolve():
        raise ValueError("Refusing to use the 14-day raw or curated artifact for a three-month build.")
    if output_path == LEGACY_OUTPUT.resolve():
        raise ValueError("Refusing to overwrite the existing 14-day database.")

    raw = read_json_required(raw_path, "three-month raw export")
    curated = read_json_required(curated_path, "three-month curated analysis")
    metadata = raw.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("Three-month raw export must contain a metadata object.")
    start_text, end_text, start_dt, end_dt, duration_days = resolve_window(
        metadata, start_override, end_override
    )
    browser_context_artifact = validate_browser_context_followups(
        browser_context_path,
        start_text,
        end_text,
        start_dt,
        end_dt,
    )
    collection_keys = discover_collection_keys(raw)
    validate_raw_scope(raw, metadata, collection_keys, start_dt, end_dt)
    messages, sources, merged_provenance = collect_messages(raw, collection_keys)
    browser_context_message_ids = merge_browser_context_messages(
        messages,
        sources,
        merged_provenance,
        browser_context_artifact,
        browser_context_path,
    )
    source_metadata_limitation = inherited_baseline_query_limitation(merged_provenance)
    base.infer_author_continuations(messages)
    base.propagate_unique_thread_channel_ids(messages)
    force_include_ids = base.curated_message_ids(curated) | browser_context_message_ids
    coverage_rows = make_coverage_rows(raw, metadata, collection_keys)
    browser_timestamps = sorted(
        str(row["timestamp_utc"]) for row in browser_context_artifact["messages"]
    )
    coverage_rows.append(
        {
            "collection_name": BROWSER_CONTEXT_COLLECTION,
            "query_text": " || ".join(
                f"direct_permalink_context:{row['context_id']}"
                for row in browser_context_artifact["contexts"]
            ),
            "scan_complete": 0,
            "messages_seen": len(browser_context_message_ids),
            "earliest_message_utc": browser_timestamps[0],
            "latest_message_utc": browser_timestamps[-1],
            "gap_notes": (
                str(browser_context_artifact["methodology"]["completeness_boundary"])
                + " The targeted eight-context capture itself is audited and complete."
            ),
        }
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    if temp_path.exists():
        temp_path.unlink()
    conn = sqlite3.connect(temp_path)
    conn.row_factory = sqlite3.Row
    try:
        base.create_schema(conn)
        create_three_month_extension_schema(conn)
        status = insert_run_and_coverage(
            conn,
            metadata,
            start_text,
            end_text,
            coverage_rows,
            str(source_metadata_limitation.get("note") or ""),
        )
        base.insert_channels(conn, messages.values())
        included_ids, exclusion_counts = base.insert_messages(
            conn, messages, sources, force_include_ids
        )
        insert_browser_context_followups(
            conn,
            browser_context_artifact,
            browser_context_path,
            included_ids,
        )
        insert_merged_provenance(conn, merged_provenance, included_ids)
        confluence_ids = base.insert_confluences(conn, messages, included_ids)
        base.insert_auto_qa(conn, messages, included_ids)
        base.insert_curated(conn, curated, included_ids, confluence_ids)
        insert_analysis_documents(
            conn,
            curated_path,
            optional_documents,
            metadata,
            browser_context_path,
            browser_context_artifact,
        )
        base.insert_data_dictionary(conn)
        conn.executemany(
            "INSERT INTO data_dictionary(table_name,column_name,description) VALUES(?,?,?)",
            (
                (
                    "merged_message_provenance",
                    "source_file",
                    "Local JSON export or segment file that exposed the Discord message; retained from _merge_provenance.sources.",
                ),
                (
                    "merged_message_provenance",
                    "source_collection",
                    "Normalized Discord collection/search family, including instrument_comparison_messages.",
                ),
                (
                    "merged_message_provenance",
                    "source_query",
                    "Discord search query retained losslessly from source descriptors and source_queries arrays.",
                ),
                (
                    "merged_message_provenance",
                    "complete_source",
                    "Whether the individual source artifact declared its bounded capture complete; this does not imply channel-wide coverage. NULL only for legacy/synthetic provenance without that field.",
                ),
                (
                    "browser_context_followup_artifacts",
                    "completeness_boundary",
                    "Verbatim disclosure that the audited browser source is complete only for eight selected permalink contexts, not the containing channels.",
                ),
                (
                    "browser_context_followup_artifacts",
                    "authority_caution",
                    "Verbatim authority boundary distinguishing Domme replies from ordinary member discussion without treating community statements as verified rules.",
                ),
                (
                    "browser_followup_contexts",
                    "status",
                    "Audited resolution status from the browser artifact: answered, partially_answered, community_answer_only, or unresolved.",
                ),
                (
                    "browser_followup_contexts",
                    "resolution",
                    "Verbatim audited context-resolution note, including unresolved and community-only limitations.",
                ),
                (
                    "browser_followup_context_messages",
                    "context_source_url",
                    "Direct Discord permalink used to load the selected context; adjacent message permalinks are constructed separately from exact IDs.",
                ),
                (
                    "browser_followup_context_messages",
                    "is_target_message",
                    "Whether this is the selected permalink target rather than an adjacent visible context message.",
                ),
                (
                    "browser_followup_context_messages",
                    "authority_class",
                    "Deterministic author marker: domme only when author_as_captured is exactly Domme (case-insensitive); non_domme otherwise. It does not make non-Domme messages authoritative answers.",
                ),
            ),
        )
        conn.execute(
            """
            UPDATE data_dictionary
            SET description='Overall collection status for the declared three-month inputs; inspect collection_coverage for incomplete or undeclared collections.'
            WHERE table_name='research_runs' AND column_name='status'
            """
        )
        write_meta(
            conn,
            raw_path,
            curated_path,
            output_path,
            duration_days,
            status,
            any(
                row["collection_name"] == "primary_messages" and bool(row["scan_complete"])
                for row in coverage_rows
            ),
            source_metadata_limitation,
            browser_context_path,
            browser_context_artifact,
        )
        conn.commit()

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise RuntimeError(
                f"Database validation failed: integrity={integrity}, foreign_keys={foreign_keys}"
            )
        stats = {
            "window_start_utc": start_text,
            "window_end_utc": end_text,
            "window_days": duration_days,
            "collection_status": status,
            "collection_coverage": coverage_rows,
            "source_unique_messages": len(messages),
            "included_messages": conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "excluded_unique_messages": len(messages) - len(included_ids),
            "attachments": conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0],
            "qa_pairs": conn.execute("SELECT COUNT(*) FROM qa_pairs").fetchone()[0],
            "rb_findings": conn.execute(
                "SELECT COUNT(*) FROM rejection_block_findings"
            ).fetchone()[0],
            "trades": conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
            "models": conn.execute("SELECT COUNT(*) FROM trading_models").fetchone()[0],
            "integrity_check": integrity,
            "foreign_key_violations": len(foreign_keys),
            "merged_provenance_rows": conn.execute(
                "SELECT COUNT(*) FROM merged_message_provenance"
            ).fetchone()[0],
            "browser_contexts": conn.execute(
                "SELECT COUNT(*) FROM browser_followup_contexts"
            ).fetchone()[0],
            "browser_context_messages": conn.execute(
                "SELECT COUNT(*) FROM browser_followup_context_messages"
            ).fetchone()[0],
            "source_metadata_limitation": source_metadata_limitation,
            "exclusion_buckets": exclusion_counts,
            "output": str(output_path),
        }
    except Exception:
        conn.close()
        if temp_path.exists():
            temp_path.unlink()
        raise
    else:
        conn.close()

    os.replace(temp_path, output_path)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a separate three-month Discord trading research database."
    )
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--curated", type=Path, default=DEFAULT_CURATED)
    parser.add_argument(
        "--browser-context-followups",
        type=Path,
        default=DEFAULT_BROWSER_CONTEXT_FOLLOWUPS,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--window-start",
        help="Optional ISO-8601 override; otherwise read from raw metadata.",
    )
    parser.add_argument(
        "--window-end",
        help="Optional ISO-8601 override; otherwise read from raw metadata.",
    )
    for document_name, filename in THREE_MONTH_DOCUMENTS.items():
        parser.add_argument(
            f"--{document_name.replace('_', '-')}",
            type=Path,
            default=Path(__file__).with_name(filename),
        )
    args = parser.parse_args()
    optional_documents = {
        name: getattr(args, name).resolve() for name in THREE_MONTH_DOCUMENTS
    }
    try:
        stats = build_three_month_database(
            args.raw,
            args.curated,
            args.browser_context_followups,
            args.output,
            args.window_start,
            args.window_end,
            optional_documents,
        )
    except (FileNotFoundError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"status": "not_built", "error": str(exc)}, ensure_ascii=True, indent=2))
        return 1
    print(json.dumps(stats, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
