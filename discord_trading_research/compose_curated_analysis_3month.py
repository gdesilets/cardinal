#!/usr/bin/env python3
from __future__ import annotations

"""Compose the final three-month Discord trading synthesis and LLM guides.

The outputs are deterministic functions of five local artifacts:
raw_discord_export_3month.json, trade_analysis_3month.json,
rb_analysis_3month.json, model_analysis_3month.json, and the targeted
browser_context_followups_3month.json permalink audit.

No web data, market data, chart inference, or external trading knowledge is
used.  Running the script again safely refreshes only the three-month curated
outputs, which makes it suitable for an updated RB analysis.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RAW = BASE_DIR / "raw_discord_export_3month.json"
DEFAULT_TRADE = BASE_DIR / "trade_analysis_3month.json"
DEFAULT_RB = BASE_DIR / "rb_analysis_3month.json"
DEFAULT_MODEL = BASE_DIR / "model_analysis_3month.json"
DEFAULT_FOLLOWUP = BASE_DIR / "browser_context_followups_3month.json"
DEFAULT_OUTPUT = BASE_DIR / "curated_analysis_3month.json"
DEFAULT_SUMMARY = BASE_DIR / "RESEARCH_SUMMARY_3MONTH.md"
DEFAULT_README = BASE_DIR / "README_FOR_LLM_3MONTH.md"
PROTECTED_14DAY = {
    (BASE_DIR / "curated_analysis.json").resolve(),
    (BASE_DIR / "RESEARCH_SUMMARY.md").resolve(),
    (BASE_DIR / "README_FOR_LLM.md").resolve(),
}
SCHEMA_VERSION = "3.0.0-curated-discord-only"
COLLECTION_KEYS = (
    "primary_messages",
    "server_rejection_phrase_messages",
    "questions_rb_messages",
    "questions_nq_es_messages",
    "broad_rb_shorthand_partial_messages",
    "contextual_qa_messages",
    "instrument_comparison_messages",
)
ALLOWED_EVIDENCE_STATUS = {"explicit", "observed_association", "derived", "insufficient_evidence"}
ALLOWED_QA_STATUS = {"answered", "partial", "conflicting", "unanswered", "ambiguous"}
ALLOWED_FINDING_FACETS = {
    "definition", "identification", "invalidation", "timing", "high_probability",
    "low_probability", "instrument_comparison", "execution", "risk", "other",
}
ALLOWED_TRADE_OUTCOMES = {"win", "loss", "breakeven", "mixed_partial", "cancelled_no_trade", "open", "unknown"}
ALLOWED_MODEL_STATUS = {"documented", "provisional_derived"}


class ComposeError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ComposeError(f"Could not parse {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ComposeError(f"Expected a JSON object in {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def unique(values: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    output: list[Any] = []
    for value in values:
        marker = compact(value)
        if marker not in seen:
            seen.add(marker)
            output.append(value)
    return output


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def text_lines(values: Iterable[Any]) -> str | None:
    rendered: list[str] = []
    for value in values:
        if isinstance(value, dict):
            text = normalize_space(value.get("rule") or value.get("statement") or value.get("text"))
        else:
            text = normalize_space(value)
        if text and text not in rendered:
            rendered.append(text)
    return "\n".join(rendered) or None


def followup_authority(row: dict[str, Any], target_ids: set[str]) -> str:
    """Assign a narrow source tier without treating adjacency as an answer."""
    message_id = str(row.get("message_id") or "")
    author = normalize_space(row.get("author")).lower()
    reply_to = str(row.get("reply_to_message_id") or "")
    if message_id in target_ids:
        return "question_author"
    if author == "domme" and reply_to:
        return "named_mentor_direct_reply"
    if author == "domme":
        return "named_mentor_adjacent_context"
    if reply_to:
        return "community_direct_reply"
    return "community_adjacent_context"


def build_message_index(
    raw: dict[str, Any], followup: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, str | None]]:
    messages: dict[str, dict[str, Any]] = {}
    for collection in COLLECTION_KEYS:
        rows = raw.get(collection) or []
        if not isinstance(rows, list):
            raise ComposeError(f"Raw collection {collection!r} is not an array")
        for row in rows:
            if not isinstance(row, dict) or not row.get("message_id"):
                continue
            message_id = str(row["message_id"])
            row = dict(row)
            row["_capture_source"] = "merged_raw_discord_export"
            row["_authority_tier"] = "uncategorized_discord_message"
            existing = messages.get(message_id)
            candidate_text = str(row.get("content_text") or row.get("visible_text") or "")
            existing_text = str((existing or {}).get("content_text") or (existing or {}).get("visible_text") or "")
            if existing is None or len(candidate_text) > len(existing_text):
                messages[message_id] = row

    target_ids = {
        str(item.get("target_message_id") or "")
        for item in followup.get("contexts", []) or []
        if isinstance(item, dict)
    }
    for source_row in followup.get("messages", []) or []:
        if not isinstance(source_row, dict) or not source_row.get("message_id"):
            continue
        message_id = str(source_row["message_id"])
        row = dict(source_row)
        row["_capture_source"] = "browser_context_followups_3month"
        row["_authority_tier"] = followup_authority(row, target_ids)
        existing = messages.get(message_id)
        if existing:
            merged = dict(existing)
            merged.update({key: value for key, value in row.items() if value not in (None, "")})
            merged["_capture_source"] = "merged_raw_and_browser_context_followup"
            messages[message_id] = merged
        else:
            messages[message_id] = row

    raw_guild_id = str((raw.get("metadata") or {}).get("guild_id") or "")
    guild_id = raw_guild_id if re.fullmatch(r"\d{15,22}", raw_guild_id) else "1167376964680691732"
    thread_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in messages.values():
        thread_id = row.get("inferred_thread_channel_id")
        if thread_id:
            key = (normalize_space(row.get("parent_channel")).lower(), normalize_space(row.get("thread_title")).lower())
            thread_ids[key].add(str(thread_id))

    permalinks: dict[str, str | None] = {}
    for message_id, row in messages.items():
        permalink = row.get("permalink") or row.get("inferred_permalink")
        permalink_text = str(permalink or "").split("?", 1)[0]
        valid_link = re.fullmatch(
            rf"https://discord\.com/channels/{guild_id}/\d{{15,22}}/{re.escape(message_id)}/?",
            permalink_text,
        )
        if valid_link:
            permalinks[message_id] = permalink_text.rstrip("/")
            continue
        if row.get("guild_id") and row.get("channel_id"):
            permalinks[message_id] = (
                f"https://discord.com/channels/{row['guild_id']}/{row['channel_id']}/{message_id}"
            )
            continue
        embedded_channel = re.search(
            r"https://discord\.com/channels/(?:undefined|\d{15,22})/(\d{15,22})/\d{15,22}",
            permalink_text,
        )
        if embedded_channel:
            permalinks[message_id] = (
                f"https://discord.com/channels/{guild_id}/{embedded_channel.group(1)}/{message_id}"
            )
            continue
        key = (normalize_space(row.get("parent_channel")).lower(), normalize_space(row.get("thread_title")).lower())
        possible = thread_ids.get(key, set())
        if len(possible) == 1:
            channel_id = next(iter(possible))
            permalinks[message_id] = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
        else:
            permalinks[message_id] = None
    return messages, permalinks


def excerpt(messages: dict[str, dict[str, Any]], message_id: str, fallback: str | None = None) -> str:
    row = messages.get(message_id) or {}
    return str(row.get("content_text") or row.get("visible_text") or fallback or "").strip()


def evidence_rows(
    message_ids: Iterable[Any],
    messages: dict[str, dict[str, Any]],
    permalinks: dict[str, str | None],
    *,
    role: str = "supports",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw_id in message_ids:
        message_id = str(raw_id or "")
        if message_id not in messages:
            continue
        output.append(
            {
                "message_id": message_id,
                "role": role,
                "excerpt": excerpt(messages, message_id),
                "permalink": permalinks.get(message_id),
                "permalink_status": "exact_or_uniquely_inferred" if permalinks.get(message_id) else "unavailable_from_export",
                "capture_source": (messages.get(message_id) or {}).get("_capture_source"),
                "source_authority": (messages.get(message_id) or {}).get("_authority_tier"),
                "author": (messages.get(message_id) or {}).get("author"),
                "reply_to_message_id": (messages.get(message_id) or {}).get("reply_to_message_id"),
            }
        )
        if limit is not None and len(output) >= limit:
            break
    return dedupe_evidence(output)


def dedupe_evidence(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        message_id = str(item.get("message_id") or "")
        role = str(item.get("role") or "supports")
        if not re.fullmatch(r"\d{15,22}", message_id):
            continue
        key = (message_id, role)
        if key not in merged:
            merged[key] = dict(item)
            continue
        old_excerpt = str(merged[key].get("excerpt") or "")
        new_excerpt = str(item.get("excerpt") or "")
        if len(new_excerpt) > len(old_excerpt):
            merged[key]["excerpt"] = new_excerpt
        if not merged[key].get("permalink") and item.get("permalink"):
            merged[key]["permalink"] = item["permalink"]
            merged[key]["permalink_status"] = item.get("permalink_status")
    return [merged[key] for key in sorted(merged)]


def confidence_number(value: Any, default: float = 0.7) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 1.0))
    text = str(value or "").lower()
    if "insufficient" in text:
        return 0.9
    if "high" in text or "adequate" in text:
        return 0.85
    if "moderate" in text or "limited" in text:
        return 0.68
    if "low" in text:
        return 0.45
    return default


def claim_facet(claim: dict[str, Any], section: str) -> str:
    topics = {str(value) for value in claim.get("topics", []) or []}
    if "invalidation_or_non_actionability" in topics or section.startswith("invalidation"):
        return "invalidation"
    if "timing" in topics or section == "timing":
        return "timing"
    if "NQ_vs_ES_or_instrument" in topics or section == "instrument_comparison":
        return "instrument_comparison"
    if "higher_probability_claim" in topics:
        return "high_probability"
    if "lower_probability_claim" in topics:
        return "low_probability"
    return "identification"


def build_rb_findings(
    rb: dict[str, Any], messages: dict[str, dict[str, Any]], permalinks: dict[str, str | None]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    seen_claims: set[str] = set()

    # Direct replies are the narrowest explicit Discord rules/answers.
    for section_name, section in (rb.get("answers") or {}).items():
        if not isinstance(section, dict):
            continue
        explicit_lists = [
            value for key, value in section.items()
            if "explicit" in key and isinstance(value, list)
        ]
        for claim in [item for values in explicit_lists for item in values if isinstance(item, dict)]:
            if "direct_reply" not in str(claim.get("source_tier") or ""):
                continue
            claim_id = str(claim.get("claim_id") or claim.get("message_id") or "")
            if claim_id in seen_claims:
                continue
            seen_claims.add(claim_id)
            ids = [str(value) for value in claim.get("evidence_message_ids", []) or []]
            evidence: list[dict[str, Any]] = []
            for message_id in ids:
                role = "supports" if message_id == str(claim.get("message_id")) else "qualifies"
                evidence.extend(evidence_rows([message_id], messages, permalinks, role=role))
            findings.append(
                {
                    "source_finding_id": claim_id,
                    "facet": claim_facet(claim, section_name),
                    "finding": normalize_space(claim.get("claim_excerpt")),
                    "evidence_status": "explicit",
                    "confidence": 0.95 if str(claim.get("source_tier")).startswith("named_mentor") else 0.8,
                    "instrument_scope": "As stated in the example" if "NQ_vs_ES_or_instrument" in (claim.get("topics") or []) else None,
                    "timeframe_scope": "Example-specific; chart warning retained" if claim.get("chart_dependent_warning") else None,
                    "session_scope": "Clock/session label retained exactly as written" if "timing" in (claim.get("topics") or []) else None,
                    "caveat": claim.get("interpretation_guard"),
                    "evidence_message_ids": ids,
                    "evidence": dedupe_evidence(evidence),
                }
            )

    def add_observed(item: dict[str, Any], facet: str, statement: str, source_id: str) -> None:
        ids = [str(value) for value in item.get("evidence_message_ids", []) or []]
        findings.append(
            {
                "source_finding_id": source_id,
                "facet": facet,
                "finding": statement,
                "evidence_status": "observed_association",
                "confidence": 0.72,
                "instrument_scope": "NQ/MNQ and ES/MES only where explicitly executed" if facet == "instrument_comparison" else None,
                "timeframe_scope": "Text labels as captured; no chart inference" if facet in {"identification", "invalidation"} else None,
                "session_scope": "Setup/session text only; post timestamps excluded" if facet == "timing" else None,
                "caveat": item.get("interpretation_guard"),
                "evidence_message_ids": ids,
                "evidence": evidence_rows(ids, messages, permalinks, limit=12),
            }
        )

    answers = rb.get("answers") or {}
    for item in (answers.get("identification") or {}).get("observed_textual_associations", []) or []:
        add_observed(
            item,
            "identification",
            f"Identification component '{item['component']}' appears in {item['message_count']} RB-containing messages.",
            f"RB-IDENT-OBS-{item['component']}",
        )
    for item in (answers.get("invalidation_and_non_actionability") or {}).get("observed_textual_associations", []) or []:
        add_observed(
            item,
            "invalidation",
            f"Invalidation/non-actionability component '{item['component']}' appears in {item['message_count']} captured messages.",
            f"RB-INVAL-OBS-{item['component']}",
        )
    timing = answers.get("timing") or {}
    for item in timing.get("rb_message_time_co_mentions", []) or []:
        add_observed(
            item,
            "timing",
            f"Time/session label '{item['component']}' is co-mentioned in {item['message_count']} RB-containing messages.",
            f"RB-TIME-MENTION-{item['component']}",
        )
    for item in timing.get("eligible_trade_episode_time_associations", []) or []:
        add_observed(
            item,
            "timing",
            (
                f"Among strict eligible RB episodes with '{item['time_or_session_label']}', the corpus has "
                f"{item['eligible_rb_wins']} wins and {item['eligible_rb_losses']} losses "
                f"(n={item['eligible_rb_instances']}, descriptive win share={item['descriptive_win_share']:.4f})."
            ),
            f"RB-TIME-ASSOC-{item['time_or_session_label']}",
        )

    probability = answers.get("probability_profile") or {}
    associations = (probability.get("eligible_trade_associations") or {}).get("all_associations", []) or []
    for item in associations:
        facet = "high_probability" if float(item.get("difference_from_all_eligible_rb_win_share") or 0) > 0 else "low_probability"
        add_observed(
            item,
            facet,
            (
                f"Confluence family '{item['confluence_family']}' has {item['eligible_rb_wins']} wins and "
                f"{item['eligible_rb_losses']} losses among eligible RB episodes "
                f"(n={item['eligible_rb_instances']}, descriptive win share={item['descriptive_win_share']:.4f}, "
                f"difference from RB baseline={item['difference_from_all_eligible_rb_win_share']:+.4f})."
            ),
            f"RB-PROB-ASSOC-{item['confluence_family']}",
        )

    synthesis_sections = {
        "identification": "identification",
        "invalidation_and_non_actionability": "invalidation",
        "timing": "timing",
        "probability_profile": "other",
        "instrument_comparison": "instrument_comparison",
    }
    for section_name, facet in synthesis_sections.items():
        for item in (answers.get(section_name) or {}).get("analyst_synthesis", []) or []:
            ids = [str(value) for value in item.get("evidence_message_ids", []) or []]
            insufficient = "insufficient" in str(item.get("confidence") or "").lower()
            findings.append(
                {
                    "source_finding_id": item.get("finding_id"),
                    "facet": facet,
                    "finding": item.get("statement"),
                    "evidence_status": "insufficient_evidence" if insufficient else "derived",
                    "confidence": confidence_number(item.get("confidence")),
                    "instrument_scope": "Executed-instrument comparison only" if facet == "instrument_comparison" else None,
                    "timeframe_scope": None,
                    "session_scope": "Message co-mentions plus strict episode labels" if facet == "timing" else None,
                    "caveat": "Analyst synthesis of the Discord artifacts; not a direct universal rule.",
                    "evidence_message_ids": ids,
                    "evidence": evidence_rows(ids, messages, permalinks, limit=12),
                }
            )
    return sorted(findings, key=lambda item: (item["facet"], str(item.get("source_finding_id") or "")))


FOLLOWUP_EVIDENCE_IDS: dict[str, list[str]] = {
    "higher_probability_confluences": [
        "1495760891348779110",
        "1495770180188377188",
        "1495733759528534106",
        "1495733936712847481",
        "1495765871891710012",
    ],
    "es_applicability": ["1496515500832718898", "1496515526632149096"],
    "close_vs_wick_validity": ["1496953286350340196"],
    "nested_rejection_blocks": ["1499025665553338438", "1499025711065989260"],
    "timeframe_preferences": [
        "1500203147996434782",
        "1500203408685142208",
        "1500203596388761825",
        "1500203630232342671",
        "1500203750504272024",
        "1500203807060004924",
    ],
    "timeframe_and_trading_window": ["1506012118317662338", "1506012133672882186"],
    "cross_market_mitigation": ["1511011249780162691"],
    "liquidity_sweep_probability": [
        "1522108259962454047",
        "1522108347522748476",
        "1522109024399659028",
        "1522109182248222730",
    ],
}

FOLLOWUP_PRIMARY_ANSWER: dict[str, str | None] = {
    "higher_probability_confluences": "1495770180188377188",
    "es_applicability": None,
    "close_vs_wick_validity": None,
    "nested_rejection_blocks": "1499025711065989260",
    "timeframe_preferences": "1500203596388761825",
    "timeframe_and_trading_window": "1506012133672882186",
    "cross_market_mitigation": None,
    "liquidity_sweep_probability": "1522108347522748476",
}


def build_browser_followup_findings(
    followup: dict[str, Any], messages: dict[str, dict[str, Any]], permalinks: dict[str, str | None]
) -> list[dict[str, Any]]:
    """Keep the eight audited permalink contexts distinct from corpus-wide extraction."""
    specifications: dict[str, dict[str, Any]] = {
        "higher_probability_confluences": {
            "facet": "high_probability",
            "finding": (
                "In a direct mentor reply, Domme said to add confluences to an RB. In a nearby direct answer, "
                "Domme named PD arrays, liquidity sweeps, bias, and news as reasons for identifying a reversal point."
            ),
            "evidence_status": "explicit",
            "confidence": 0.95,
            "source_authority": "named_mentor_direct_reply",
            "caveat": "The reply does not quantify the value of any confluence or provide a universal ranking.",
        },
        "es_applicability": {
            "facet": "instrument_comparison",
            "finding": "The audited context did not answer whether rejection blocks work on ES.",
            "evidence_status": "insufficient_evidence",
            "confidence": 0.96,
            "source_authority": "unresolved_question_with_off_target_community_reply",
            "caveat": "A community reply said they do not work on NQ; it is off-target and inconsistent with extensive NQ use elsewhere in the corpus.",
        },
        "close_vs_wick_validity": {
            "facet": "invalidation",
            "finding": "The audited context contained no visible answer to whether close confirmation or a wick sweep is required for validity.",
            "evidence_status": "insufficient_evidence",
            "confidence": 0.97,
            "source_authority": "unresolved_question",
            "caveat": "Do not infer a universal close-versus-wick rule from adjacent or chart-specific material.",
        },
        "nested_rejection_blocks": {
            "facet": "high_probability",
            "finding": "Domme directly replied that rejection blocks nested within rejection blocks work too.",
            "evidence_status": "explicit",
            "confidence": 0.94,
            "source_authority": "named_mentor_direct_reply",
            "caveat": "The reply does not affirm the question's claim that nesting is higher probability.",
        },
        "timeframe_preferences": {
            "facet": "identification",
            "finding": "A community member described using 5m/15m context and looking at 1m mainly for entry.",
            "evidence_status": "explicit",
            "confidence": 0.62,
            "source_authority": "community_personal_experience",
            "caveat": "The member explicitly framed this as personal experience and not Powell's method.",
        },
        "timeframe_and_trading_window": {
            "facet": "timing",
            "finding": "An adjacent community response described 1h/30m order-block context with a 1m RB precision entry; the requested trading-time window remained unanswered.",
            "evidence_status": "insufficient_evidence",
            "confidence": 0.82,
            "source_authority": "community_adjacent_context",
            "caveat": "No direct reply linkage was visible, and no universal trading window was supplied.",
        },
        "cross_market_mitigation": {
            "facet": "instrument_comparison",
            "finding": "The audited context did not answer whether an ES tap invalidates an otherwise untapped NQ rejection block.",
            "evidence_status": "insufficient_evidence",
            "confidence": 0.97,
            "source_authority": "unresolved_question",
            "caveat": "Cross-market mitigation remains unresolved in the captured evidence.",
        },
        "liquidity_sweep_probability": {
            "facet": "high_probability",
            "finding": "A community member called a liquidity sweep necessary and clarified that it need not occur in the same candle.",
            "evidence_status": "explicit",
            "confidence": 0.58,
            "source_authority": "community_direct_reply",
            "caveat": "No mentor answer was visible, and the claim conflicts with conditional treatment elsewhere in the corpus.",
        },
    }
    output: list[dict[str, Any]] = []
    for context in followup.get("contexts", []) or []:
        context_id = str(context.get("context_id") or "")
        specification = specifications.get(context_id)
        if not specification:
            continue
        ids = [message_id for message_id in FOLLOWUP_EVIDENCE_IDS.get(context_id, []) if message_id in messages]
        row = {
            "source_finding_id": f"BROWSER-FOLLOWUP-{context_id}",
            "facet": specification["facet"],
            "finding": specification["finding"],
            "evidence_status": specification["evidence_status"],
            "confidence": specification["confidence"],
            "instrument_scope": "NQ/ES question context only" if context_id in {"es_applicability", "cross_market_mitigation"} else None,
            "timeframe_scope": "Personal/community timeframe description" if "timeframe" in context_id else None,
            "session_scope": "Requested time window remained unanswered" if context_id == "timeframe_and_trading_window" else None,
            "caveat": specification["caveat"],
            "evidence_message_ids": ids,
            "evidence": evidence_rows(ids, messages, permalinks),
            "source_capture": "browser_context_followups_3month.json",
            "source_authority": specification["source_authority"],
            "browser_context_status": context.get("status"),
            "browser_context_resolution": context.get("resolution"),
        }
        output.append(row)
    return output


def qa_priority(item: dict[str, Any]) -> tuple[int, str, str]:
    text = normalize_space(item.get("question_excerpt")).lower()
    score = 0
    weights = {
        "invalid": 8,
        "valid": 7,
        "close": 6,
        "mitigat": 6,
        "sweep": 5,
        "nq": 5,
        "es": 5,
        "10am": 5,
        "10 am": 5,
        "wick": 4,
        "ce": 4,
        "ote": 4,
        "smt": 4,
        "timeframe": 4,
        "1m": 3,
        "5m": 3,
    }
    for phrase, weight in weights.items():
        if phrase in text:
            score += weight
    return (-score, str(item.get("question_timestamp_utc") or ""), str(item.get("question_message_id") or ""))


def build_qa_pairs(
    rb: dict[str, Any],
    followup: dict[str, Any],
    messages: dict[str, dict[str, Any]],
    permalinks: dict[str, str | None],
    unanswered_limit: int = 25,
) -> list[dict[str, Any]]:
    rows = rb.get("related_qa") or []
    followup_target_ids = {
        str(item.get("target_message_id") or "")
        for item in followup.get("contexts", []) or []
        if isinstance(item, dict)
    }
    answered = [item for item in rows if item.get("status") == "answered_by_direct_reply"]
    unanswered = sorted(
        [
            item for item in rows
            if item.get("status") != "answered_by_direct_reply"
            and str(item.get("question_message_id") or "") not in followup_target_ids
        ],
        key=qa_priority,
    )[:unanswered_limit]
    output: list[dict[str, Any]] = []
    ordered_answered = sorted(
        answered,
        key=lambda row: (str(row.get("question_timestamp_utc") or ""), str(row.get("qa_id") or "")),
    )
    for item in ordered_answered + unanswered:
        is_answered = item.get("status") == "answered_by_direct_reply" and bool(item.get("answers"))
        answers = item.get("answers") or []
        answer = sorted(
            answers,
            key=lambda value: (
                0 if str(value.get("source_tier") or "").startswith("named_mentor") else 1,
                str(value.get("timestamp_utc") or ""),
                str(value.get("message_id") or ""),
            ),
        )[0] if answers else {}
        question_id = str(item.get("question_message_id") or "") or None
        answer_id = str(answer.get("message_id") or "") or None
        evidence_ids = [str(value) for value in item.get("evidence_message_ids", []) or []]
        output.append(
            {
                "source_qa_id": item.get("qa_id"),
                "question_message_id": question_id,
                "answer_message_id": answer_id,
                "question_timestamp_utc": item.get("question_timestamp_utc"),
                "normalized_question": normalize_space(item.get("question_excerpt"))[:1800],
                "answer_summary": normalize_space(answer.get("answer_excerpt")) if is_answered else None,
                "status": "answered" if is_answered else "unanswered",
                "topic": "rejection_block",
                "confidence": 0.95 if str(answer.get("source_tier") or "").startswith("named_mentor") else 0.82 if is_answered else 0.9,
                "notes": item.get("scope_note") or "No direct reply was captured in the export.",
                "question_permalink": permalinks.get(question_id) if question_id else None,
                "answer_permalink": permalinks.get(answer_id) if answer_id else None,
                "evidence_message_ids": evidence_ids,
                "chart_dependent_warning": bool(item.get("chart_dependent_warning")),
                "evidence": evidence_rows(evidence_ids, messages, permalinks),
                "source_capture": "rb_analysis_3month.json",
                "source_authority": answer.get("source_tier") if is_answered else "unresolved_in_export",
            }
        )

    status_map = {
        "answered": "answered",
        "partially_answered": "partial",
        "community_answer_only": "partial",
        "unresolved": "unanswered",
    }
    for context in followup.get("contexts", []) or []:
        context_id = str(context.get("context_id") or "")
        question_id = str(context.get("target_message_id") or "")
        message = messages.get(question_id) or {}
        answer_id = FOLLOWUP_PRIMARY_ANSWER.get(context_id)
        evidence_ids = [value for value in FOLLOWUP_EVIDENCE_IDS.get(context_id, [question_id]) if value in messages]
        browser_status = str(context.get("status") or "unresolved")
        qa_status = status_map.get(browser_status, "ambiguous")
        primary = messages.get(str(answer_id)) or {} if answer_id else {}
        authority = primary.get("_authority_tier") if answer_id else "unresolved_question"
        output.append(
            {
                "source_qa_id": f"browser-{context_id}",
                "question_message_id": question_id,
                "answer_message_id": answer_id,
                "question_timestamp_utc": message.get("timestamp_utc"),
                "normalized_question": normalize_space(message.get("content_text"))[:1800],
                "answer_summary": normalize_space(context.get("resolution")) if qa_status != "unanswered" else None,
                "status": qa_status,
                "topic": "rejection_block",
                "confidence": 0.95 if authority == "named_mentor_direct_reply" else 0.68 if qa_status == "partial" else 0.94,
                "notes": (
                    f"Targeted browser permalink audit status={browser_status}. {context.get('resolution')} "
                    "The capture is complete only for this selected visible context. Adjacent messages without reply linkage are not authoritative answers."
                ),
                "question_permalink": permalinks.get(question_id),
                "answer_permalink": permalinks.get(str(answer_id)) if answer_id else None,
                "evidence_message_ids": evidence_ids,
                "chart_dependent_warning": False,
                "evidence": evidence_rows(evidence_ids, messages, permalinks),
                "source_capture": "browser_context_followups_3month.json",
                "source_authority": authority,
                "browser_resolution_status": browser_status,
            }
        )
    by_question: dict[str, dict[str, Any]] = {}
    for item in output:
        question_id = str(item.get("question_message_id") or item.get("source_qa_id") or "")
        # Browser-audited contexts replace the less complete search-export record.
        if question_id not in by_question or item.get("source_capture") == "browser_context_followups_3month.json":
            by_question[question_id] = item
    return sorted(
        by_question.values(),
        key=lambda item: (str(item.get("question_timestamp_utc") or ""), str(item.get("question_message_id") or "")),
    )


def build_contradictions(
    rb: dict[str, Any],
    followup: dict[str, Any],
    messages: dict[str, dict[str, Any]],
    permalinks: dict[str, str | None],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in rb.get("contradictions_and_tensions", []) or []:
        ids = [str(value) for value in item.get("evidence_message_ids", []) or [] if str(value) in messages]
        output.append(
            {
                "source_tension_id": item.get("tension_id"),
                "topic": item.get("topic") or "unspecified",
                "description": item.get("statement") or item.get("description"),
                "message_id_a": ids[0] if ids else None,
                "message_id_b": ids[1] if len(ids) > 1 else None,
                "resolution_status": "unresolved" if "unresolved" in str(item.get("resolution") or "") else "qualified",
                "notes": f"Evidence type={item.get('evidence_type')}; resolution={item.get('resolution')}",
                "evidence_message_ids": ids,
                "evidence": evidence_rows(ids, messages, permalinks),
            }
        )
    for item in output:
        tension_id = item.get("source_tension_id")
        if tension_id == "liquidity_sweep_requirement":
            added_ids = [
                value for value in FOLLOWUP_EVIDENCE_IDS["liquidity_sweep_probability"]
                if value in messages and value not in item["evidence_message_ids"]
            ]
            item["evidence_message_ids"].extend(added_ids)
            item["evidence"] = dedupe_evidence(
                item["evidence"] + evidence_rows(added_ids, messages, permalinks, role="qualifies")
            )
            item["notes"] += "; targeted browser follow-up adds a community-only necessity claim, not a mentor resolution"
        elif tension_id == "one_minute_rb_quality":
            added_ids = [value for value in ("1500203750504272024", "1506012133672882186") if value in messages]
            item["evidence_message_ids"] = list(dict.fromkeys(item["evidence_message_ids"] + added_ids))
            item["evidence"] = dedupe_evidence(
                item["evidence"] + evidence_rows(added_ids, messages, permalinks, role="qualifies")
            )

    es_ids = [value for value in FOLLOWUP_EVIDENCE_IDS["es_applicability"] if value in messages]
    if es_ids:
        output.append(
            {
                "source_tension_id": "browser_es_applicability_off_target_reply",
                "topic": "NQ versus ES applicability",
                "description": (
                    "The ES-applicability question remains unresolved. A community reply said RBs do not work on NQ, "
                    "which is off-target and inconsistent with extensive NQ usage elsewhere in the captured corpus."
                ),
                "message_id_a": es_ids[0],
                "message_id_b": es_ids[1] if len(es_ids) > 1 else None,
                "resolution_status": "unresolved",
                "notes": "Targeted browser context; no mentor or directly responsive ES answer was visible.",
                "evidence_message_ids": es_ids,
                "evidence": dedupe_evidence(
                    evidence_rows(es_ids[:1], messages, permalinks, role="supports")
                    + evidence_rows(es_ids[1:], messages, permalinks, role="contradicts")
                ),
                "source_capture": "browser_context_followups_3month.json",
            }
        )
    return output


def canonical_confluence(tag: str) -> str | None:
    lower = str(tag or "").lower()
    base = lower.split(":", 1)[0]
    if base == "rejection_block":
        return "rejection_block"
    if base in {"fair_value_gap", "inverse_fair_value_gap"}:
        return "fvg_ifvg"
    if base == "key_open":
        return "10am_key_open" if "10am" in lower else "key_opens"
    if base in {"smt_divergence", "ssmt", "monthly_cycle_ssmt", "relative_strength"}:
        return "smt_ssmt"
    if base in {"liquidity_sweep", "judas_swing"}:
        return "liquidity_sweep"
    if base in {"ote_fibonacci", "fibonacci_level", "range_midpoint"}:
        return "ote_fibonacci"
    if base in {"premium_discount", "discount", "premium", "equilibrium"}:
        return "premium_discount"
    if base == "order_block":
        return "order_block"
    if base == "breaker":
        return "breaker"
    if base in {"cisd", "displacement", "break_of_structure", "entry_trigger"}:
        return "cisd_mss_displacement"
    if base == "engineered_liquidity":
        return "engineered_liquidity"
    if base == "standard_deviation":
        return "standard_deviation"
    if base in {"bias_alignment", "original_bias", "higher_timeframe_bias"}:
        return "higher_timeframe_bias"
    if base in {"draw_on_liquidity", "liquidity_low", "liquidity_high"}:
        return "draw_on_liquidity"
    if base in {"news_filter", "news", "news_exposure"}:
        return "news_filter"
    if base in {"market_open", "market_open_930"}:
        return "market_open_930"
    if base in {"nwog", "ndog"}:
        return "key_opens"
    return None


def issue_confluence(issue: str) -> str | None:
    lower = str(issue or "").lower()
    if "news" in lower:
        return "news_filter"
    if "bias" in lower:
        return "higher_timeframe_bias"
    if any(word in lower for word in ("confirm", "early", "front")):
        return "cisd_mss_displacement"
    if any(word in lower for word in ("stop", "risk", "trail", "overtrad", "revenge", "fomo", "impat")):
        return "risk_management"
    return None


def outcome_to_db(value: str) -> str:
    return {"cancelled": "cancelled_no_trade", "mixed": "mixed_partial"}.get(value, value)


def build_trade_rows(
    trade: dict[str, Any], messages: dict[str, dict[str, Any]], permalinks: dict[str, str | None]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for episode in trade.get("episodes", []) or []:
        outcome = outcome_to_db(str(episode.get("outcome") or "unknown"))
        if outcome not in {"win", "loss", "breakeven", "mixed_partial", "cancelled_no_trade", "open", "unknown"}:
            outcome = "unknown"
        tags = [str(value) for value in episode.get("confluences", []) or []]
        issues = [str(value) for value in episode.get("rules_violated_or_execution_issues", []) or []]
        eligible_weight = int(episode.get("eligible_trade_instances_for_win_loss_confluence_comparison") or 0)
        comparison_eligible = episode.get("outcome") in {"win", "loss"} and eligible_weight > 0
        evidence_source = episode.get("evidence") or []
        first_evidence_id = str(evidence_source[0].get("message_id")) if evidence_source else None
        mapped: dict[str, list[str]] = defaultdict(list)
        for tag in tags:
            canonical = canonical_confluence(tag)
            if canonical:
                mapped[canonical].append(tag)
        for issue in issues:
            canonical = issue_confluence(issue)
            if canonical:
                mapped[canonical].append(f"violated:{issue}")
        rb_instances = (episode.get("rejection_block_use") or {}).get("instances") or []
        timeframes = sorted({str(item.get("timeframe") or "unknown") for item in rb_instances if isinstance(item, dict)})
        entry_tags = [tag for tag in tags if tag.endswith(":entry") or tag.split(":", 1)[0] in {"cisd", "breaker"}]
        target_tags = [tag for tag in tags if tag.endswith(":target") or tag.split(":", 1)[0] == "draw_on_liquidity"]
        outcome_basis = str(episode.get("outcome_basis") or "not_stated")
        confidence = 0.96 if outcome_basis.startswith("explicit") else 0.86 if "explicit" in outcome_basis else 0.72
        notes = [
            f"episode_kind={episode.get('episode_kind')}",
            f"execution_mode={episode.get('execution_mode')}",
            f"linkage_strength={episode.get('linkage_strength')}",
            f"trade_count_reported={episode.get('trade_count_reported')}",
            f"strict_win_loss_comparison_eligible={int(comparison_eligible)}",
            f"raw_confluences={'; '.join(tags) or 'none'}",
        ]
        if episode.get("count_conflict"):
            notes.append(f"count_conflict={compact(episode.get('count_conflict'))}")
        if episode.get("notes"):
            notes.append(normalize_space(episode.get("notes")))

        evidence_role = "outcome" if outcome in {"win", "loss", "breakeven", "mixed_partial"} else "setup"
        trade_evidence = dedupe_evidence(
            {
                "message_id": str(item.get("message_id") or ""),
                "role": evidence_role,
                "excerpt": str(item.get("excerpt") or ""),
                "permalink": permalinks.get(str(item.get("message_id") or "")),
                "permalink_status": "exact_or_uniquely_inferred" if permalinks.get(str(item.get("message_id") or "")) else "unavailable_from_export",
            }
            for item in evidence_source
            if item.get("message_id")
        )
        confluences: list[dict[str, Any]] = []
        # Win/loss feature rows remain strict; other outcomes can retain descriptive tags.
        mapped_for_table = mapped if comparison_eligible or outcome not in {"win", "loss"} else {}
        for canonical in sorted(mapped_for_table):
            originals = mapped_for_table[canonical]
            violated = all(value.startswith("violated:") for value in originals)
            confluences.append(
                {
                    "name": canonical,
                    "state": "violated" if violated else "present",
                    "attribution": "explicit" if not violated else "curated_inference",
                    "evidence_message_id": first_evidence_id,
                    "notes": "Mapped from: " + "; ".join(sorted(set(originals))),
                }
            )
        output.append(
            {
                "trade_id": str(episode.get("episode_id")),
                "trader": episode.get("author"),
                "trade_date": episode.get("trade_date_local"),
                "setup_time_text": episode.get("setup_time"),
                "post_time_utc": episode.get("primary_post_timestamp_utc"),
                "instrument": ", ".join(episode.get("instrument") or ["unknown"]),
                "direction": episode.get("direction") if episode.get("direction") in {"long", "short"} else "unknown",
                "setup_name": episode.get("episode_kind"),
                "timeframe": ", ".join(timeframes) or None,
                "session_name": episode.get("session"),
                "outcome": outcome,
                "outcome_basis": outcome_basis,
                "outcome_confidence": confidence,
                "entry_text": "; ".join(entry_tags) or None,
                "invalidation_text": "; ".join(issues) or None,
                "stop_text": None,
                "target_text": "; ".join(target_tags) or None,
                "management_text": None,
                "notes": " | ".join(notes),
                "primary_permalink": permalinks.get(first_evidence_id) if first_evidence_id else None,
                "evidence": trade_evidence,
                "confluences": confluences,
            }
        )
    return output


def strict_profile(
    trade: dict[str, Any], outcome: str
) -> tuple[int, Counter[str], Counter[str], Counter[str]]:
    confluences: Counter[str] = Counter()
    issues: Counter[str] = Counter()
    authors: Counter[str] = Counter()
    denominator = 0
    for episode in trade.get("episodes", []) or []:
        weight = int(episode.get("eligible_trade_instances_for_win_loss_confluence_comparison") or 0)
        if episode.get("outcome") != outcome or weight <= 0:
            continue
        denominator += weight
        families = {
            canonical
            for tag in episode.get("confluences", []) or []
            if (canonical := canonical_confluence(str(tag)))
        }
        for family in families:
            confluences[family] += weight
        for issue in set(str(value) for value in episode.get("rules_violated_or_execution_issues", []) or []):
            issues[issue] += weight
        authors[str(episode.get("author") or "unknown")] += weight
    return denominator, confluences, issues, authors


def build_outcome_profiles(trade: dict[str, Any]) -> list[dict[str, Any]]:
    excluded = int((trade.get("episode_summary") or {}).get("records_excluded_from_win_loss_confluence_comparison") or 0)
    output: list[dict[str, Any]] = []
    for outcome in ("win", "loss"):
        denominator, confluences, issues, authors = strict_profile(trade, outcome)
        top_confluences = confluences.most_common(15)
        top_issues = issues.most_common(12)
        author_text = "; ".join(
            f"{author}={count} ({count / denominator:.1%})" for author, count in authors.most_common(5)
        ) if denominator else None
        output.append(
            {
                "outcome": outcome,
                "summary": (
                    f"Strict comparable {outcome} profile contains {denominator} eligible instances. "
                    f"Most frequent canonical confluences: "
                    + ", ".join(f"{name} {count}/{denominator}" for name, count in top_confluences[:8])
                    + ". Explicit issues: "
                    + (", ".join(f"{name} {count}" for name, count in top_issues[:8]) or "none repeatedly extracted")
                    + "."
                ),
                "resolved_trade_count": denominator,
                "unknown_trade_count": excluded,
                "author_concentration": author_text,
                "limitations": (
                    "Selected self-reported Discord journals. Confluences overlap, screenshots are not decoded, "
                    "most executed instruments are unknown, and counts are not causal or expectancy estimates."
                ),
                "confluences": [
                    {
                        "name": name,
                        "role": f"common_in_{outcome}_profile",
                        "observed_count": count,
                        "observed_share": round(count / denominator, 6) if denominator else None,
                        "rationale": f"Present in {count} of {denominator} strict {outcome} instances; overlapping descriptive frequency only.",
                    }
                    for name, count in top_confluences
                ],
                "rules_or_execution_issues": [
                    {"issue": name, "observed_count": count, "observed_share": round(count / denominator, 6) if denominator else None}
                    for name, count in top_issues
                ],
            }
        )
    return output


def build_association_catalog(rb: dict[str, Any]) -> dict[str, Any]:
    profile = ((rb.get("answers") or {}).get("probability_profile") or {}).get("eligible_trade_associations") or {}
    baseline = {
        "eligible_rb_wins": profile.get("eligible_rb_wins"),
        "eligible_rb_losses": profile.get("eligible_rb_losses"),
        "eligible_rb_instances": profile.get("eligible_rb_instances"),
        "baseline_descriptive_win_share": profile.get("baseline_descriptive_win_share"),
        "minimum_confluence_sample": profile.get("minimum_confluence_sample"),
        "eligibility_basis": profile.get("eligibility_basis"),
    }
    high = sorted(
        profile.get("observed_higher_win_share_associations", []) or [],
        key=lambda item: (-float(item.get("difference_from_all_eligible_rb_win_share") or 0), -int(item.get("eligible_rb_instances") or 0), item.get("confluence_family") or ""),
    )
    low = sorted(
        profile.get("observed_lower_win_share_associations", []) or [],
        key=lambda item: (float(item.get("difference_from_all_eligible_rb_win_share") or 0), -int(item.get("eligible_rb_instances") or 0), item.get("confluence_family") or ""),
    )
    return {
        "baseline": baseline,
        "higher_observed_associations": high,
        "lower_observed_associations": low,
        "guardrail": "Overlapping selected-corpus associations only; not causal, not a backtest, and not market expectancy.",
    }


def build_probability_tiers(association_catalog: dict[str, Any]) -> list[dict[str, Any]]:
    high = association_catalog["higher_observed_associations"][:6]
    low = association_catalog["lower_observed_associations"][:6]
    high_text = "; ".join(
        f"{item['confluence_family']} {item['eligible_rb_wins']}W/{item['eligible_rb_losses']}L n={item['eligible_rb_instances']} share={item['descriptive_win_share']:.1%}"
        for item in high
    )
    low_text = "; ".join(
        f"{item['confluence_family']} {item['eligible_rb_wins']}W/{item['eligible_rb_losses']}L n={item['eligible_rb_instances']} share={item['descriptive_win_share']:.1%}"
        for item in low
    )
    limitation = "Selected Discord-corpus association or synthesis only; no tier is a calibrated probability or expectancy estimate."
    return [
        {
            "label": "Higher observed association — exploratory",
            "rank_order": 1,
            "basis": "corpus_observed",
            "definition": high_text,
            "limitations": limitation + " Components overlap and smaller samples are unstable.",
        },
        {
            "label": "Stacked and confirmed — qualitative",
            "rank_order": 2,
            "basis": "synthesis",
            "definition": "Explicit bias/unresolved draw, fresh meaningful PDA or rejection, correct location, manipulation/sweep where required, closed RB/CISD/breaker/displacement trigger, structural stop, and an open realistic target.",
            "limitations": limitation,
        },
        {
            "label": "Lower observed association — exploratory",
            "rank_order": 3,
            "basis": "corpus_observed",
            "definition": low_text,
            "limitations": limitation + " A lower share does not prove the component is harmful.",
        },
        {
            "label": "Non-actionable or rejected",
            "rank_order": 4,
            "basis": "synthesis",
            "definition": "Unclosed/front-run trigger, mitigated block, delivered draw, bias conflict, incomplete required tap, poor/random lower-timeframe shape, unresolved opposing liquidity, chop/news instability, or arbitrary stop geometry.",
            "limitations": limitation,
        },
    ]


def build_models(
    model_analysis: dict[str, Any], messages: dict[str, dict[str, Any]], permalinks: dict[str, str | None]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for number, model in enumerate(model_analysis.get("models", []) or [], start=1):
        rules: list[dict[str, Any]] = []
        for item in model.get("exact_inclusion_rules", []) or []:
            rules.append({"type": "context" if item.get("rule_order") == 1 else "identification", "text": item["rule"], "required": item.get("required", True)})
        for item in model.get("exact_exclusion_rules", []) or []:
            rules.append({"type": "no_trade", "text": item["rule"], "required": item.get("required", True)})
        for item in model.get("entry_and_execution", []) or []:
            rules.append({"type": "entry", "text": item["rule"], "required": item.get("required", True)})
        for item in model.get("risk_and_stop_management", []) or []:
            rules.append({"type": "risk", "text": item["rule"], "required": item.get("required", True)})
        for item in model.get("target_and_trade_management", []) or []:
            rule_type = "management" if re.search(r"partial|breakeven|runner|trail|management", item["rule"], re.I) else "target"
            rules.append({"type": rule_type, "text": item["rule"], "required": item.get("required", False)})
        invalidation = model.get("invalidation") or {}
        if invalidation.get("price_or_structure"):
            rules.append({"type": "invalidation", "text": invalidation["price_or_structure"], "required": True})
        if invalidation.get("narrative_or_eligibility"):
            rules.append({"type": "no_trade", "text": invalidation["narrative_or_eligibility"], "required": True})

        evidence: list[dict[str, Any]] = []
        for item in model.get("evidence", []) or []:
            message_id = str(item.get("message_id") or "")
            if message_id not in messages:
                continue
            roles = set(item.get("roles") or [])
            role = "failed_example" if "failed_example" in roles else "supports"
            evidence.append(
                {
                    "message_id": message_id,
                    "role": role,
                    "excerpt": item.get("exact_excerpt") or excerpt(messages, message_id),
                    "permalink": permalinks.get(message_id),
                    "permalink_status": "exact_or_uniquely_inferred" if permalinks.get(message_id) else "unavailable_from_export",
                }
            )
        counts = model.get("supporting_outcome_counts") or {}
        by_outcome = counts.get("matched_episode_records_by_outcome") or {}
        time_scope = model.get("time_and_session") or {}
        instrument_scope = model.get("instruments") or {}
        inclusion_text = text_lines(item.get("rule") for item in model.get("exact_inclusion_rules", []) or [])
        exclusion_text = text_lines(item.get("rule") for item in model.get("exact_exclusion_rules", []) or [])
        output.append(
            {
                "source_model_id": model.get("model_id"),
                "model_no": number,
                "name": model.get("name"),
                "evidence_status": "documented" if model.get("classification") == "documented_recurring" else "provisional_derived",
                "thesis": model.get("material_distinction"),
                "eligibility_context": inclusion_text,
                "identification": (
                    "Required: " + "; ".join((model.get("confluences") or {}).get("required_or_near_required", []) or [])
                    + "\nSupportive: " + "; ".join((model.get("confluences") or {}).get("supportive", []) or [])
                ),
                "trigger_confirmation": text_lines(item.get("rule") for item in model.get("entry_and_execution", []) or []),
                "invalidation": (invalidation.get("price_or_structure") or "") + "\n" + (invalidation.get("narrative_or_eligibility") or ""),
                "entry": text_lines(item.get("rule") for item in model.get("entry_and_execution", []) or []),
                "stop": text_lines(item.get("rule") for item in model.get("risk_and_stop_management", []) or []),
                "target": text_lines(item.get("rule") for item in model.get("target_and_trade_management", []) or []),
                "management": text_lines(item.get("rule") for item in model.get("target_and_trade_management", []) or []),
                "instrument_scope": compact(instrument_scope),
                "timeframe_scope": "Timeframes retained in model rules and observed confluence labels; no chart inference.",
                "session_scope": compact(time_scope),
                "win_count": counts.get("comparable_wins"),
                "loss_count": counts.get("comparable_losses"),
                "breakeven_count": by_outcome.get("breakeven", 0),
                "unknown_count": sum(int(by_outcome.get(key, 0) or 0) for key in ("unknown", "mixed", "cancelled", "open")),
                "limitations": (
                    f"Confidence={compact(model.get('confidence'))}. Comparable denominator={counts.get('comparable_resolved_instances')}. "
                    "Counts are selected-corpus associations, models overlap, and no expectancy or causal claim is supported. "
                    f"Exclusions: {exclusion_text or 'see source model artifact'}."
                ),
                "rules": rules,
                "evidence": dedupe_evidence(evidence),
            }
        )
    return output


def first_ids(items: Iterable[dict[str, Any]], limit: int = 12) -> list[str]:
    values: list[str] = []
    for item in items:
        for message_id in item.get("evidence_message_ids", []) or []:
            value = str(message_id)
            if value not in values:
                values.append(value)
                if len(values) >= limit:
                    return values
    return values


def profile_top(profile: dict[str, Any], limit: int = 6) -> str:
    denominator = int(profile.get("resolved_trade_count") or 0)
    return ", ".join(
        f"{item['name']} {item['observed_count']}/{denominator} ({item['observed_share']:.1%})"
        for item in (profile.get("confluences") or [])[:limit]
    )


def build_research_answers(
    raw: dict[str, Any],
    rb: dict[str, Any],
    trade: dict[str, Any],
    model_analysis: dict[str, Any],
    followup: dict[str, Any],
    profiles: list[dict[str, Any]],
    associations: dict[str, Any],
    qa_pairs: list[dict[str, Any]],
    contradictions: list[dict[str, Any]],
    messages: dict[str, dict[str, Any]],
    permalinks: dict[str, str | None],
) -> list[dict[str, Any]]:
    answers = rb.get("answers") or {}
    identification = answers.get("identification") or {}
    invalidation = answers.get("invalidation_and_non_actionability") or {}
    timing = answers.get("timing") or {}
    ident_counts = {item["component"]: item["message_count"] for item in identification.get("observed_textual_associations", []) or []}
    invalid_counts = {item["component"]: item["message_count"] for item in invalidation.get("observed_textual_associations", []) or []}
    time_counts = {item["component"]: item["message_count"] for item in timing.get("rb_message_time_co_mentions", []) or []}
    high = associations["higher_observed_associations"]
    low = associations["lower_observed_associations"]
    baseline = associations["baseline"]
    instrument_synthesis = ((answers.get("instrument_comparison") or {}).get("analyst_synthesis") or [{}])[0]
    instrument_basis = instrument_synthesis.get("basis_metrics") or {}
    direct_findings = [
        item
        for section in answers.values() if isinstance(section, dict)
        for key, values in section.items() if "explicit" in key and isinstance(values, list)
        for item in values if isinstance(item, dict) and "direct_reply" in str(item.get("source_tier") or "")
    ]
    direct_by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in direct_findings:
        for topic in item.get("topics", []) or []:
            direct_by_topic[str(topic)].append(item)

    win_profile = next(profile for profile in profiles if profile["outcome"] == "win")
    loss_profile = next(profile for profile in profiles if profile["outcome"] == "loss")
    strict = (trade.get("episode_summary") or {}).get("eligible_actual_trade_instances_for_win_loss_confluence_comparison") or {}
    browser_contexts = {
        str(item.get("context_id") or ""): item
        for item in followup.get("contexts", []) or []
        if isinstance(item, dict)
    }

    def browser_ids(context_id: str) -> list[str]:
        return [value for value in FOLLOWUP_EVIDENCE_IDS.get(context_id, []) if value in messages]

    def record(key: str, question: str, status: str, summary: str, limitations: str, ids: list[str]) -> dict[str, Any]:
        valid_ids = list(dict.fromkeys(value for value in ids if value in messages))
        return {
            "key": key,
            "question_text": question,
            "answer_status": status,
            "answer_summary": summary,
            "limitations": limitations,
            "evidence_message_ids": valid_ids,
            "evidence": evidence_rows(valid_ids, messages, permalinks),
        }

    high_text = "; ".join(
        f"{item['confluence_family']} {item['eligible_rb_wins']}W/{item['eligible_rb_losses']}L n={item['eligible_rb_instances']} ({item['descriptive_win_share']:.1%})"
        for item in high[:6]
    )
    low_text = "; ".join(
        f"{item['confluence_family']} {item['eligible_rb_wins']}W/{item['eligible_rb_losses']}L n={item['eligible_rb_instances']} ({item['descriptive_win_share']:.1%})"
        for item in low[:6]
    )
    model_text = "; ".join(
        f"{model['name']} — {model['supporting_outcome_counts']['comparable_wins']}W/"
        f"{model['supporting_outcome_counts']['comparable_losses']}L, "
        f"n={model['supporting_outcome_counts']['comparable_resolved_instances']}"
        for model in model_analysis.get("models", []) or []
    )
    return [
        record(
            "rb_identification",
            "How do members identify a rejection block?",
            "partial",
            (
                "The corpus does not provide one universal candle formula. An RB is treated as a meaningful rejection, not every wick. "
                f"Frequent text components are timeframe selection ({ident_counts.get('timeframe_selection', 0)} messages), "
                f"volume imbalance/FVG ({ident_counts.get('volume_imbalance_or_fvg', 0)}), liquidity sweep ({ident_counts.get('liquidity_sweep', 0)}), "
                f"CE/start boundary ({ident_counts.get('ce_or_start_boundary', 0)}), and meaningful rejection/wick ({ident_counts.get('meaningful_rejection_or_wick', 0)}). "
                "Direct replies support waiting for the RB close in cited setups, permit a volume-imbalance-based marking in one chart, and reject poor 1m shape before a 15m open. "
                "The targeted browser audit adds a mentor reply naming PD arrays, liquidity sweeps, bias, and news as reversal-point context."
            ),
            "Several replies are chart-specific. Candle color, liquidity-sweep necessity, and CE/start selection are not universalized.",
            first_ids(identification.get("analyst_synthesis", [])) + first_ids(direct_by_topic.get("identification", [])) + browser_ids("higher_probability_confluences"),
        ),
        record(
            "rb_invalidation",
            "How is a rejection block or rejection-block trade invalidated?",
            "partial",
            (
                "The export distinguishes technical invalidation from non-actionability. Recurrent reasons are missing tap/confirmation "
                f"({invalid_counts.get('missing_level_tap_or_confirmation', 0)} messages), disrespected/ran-through structure "
                f"({invalid_counts.get('disrespected_or_ran_through', 0)}), opposite bias ({invalid_counts.get('opposite_bias', 0)}), "
                f"already mitigated ({invalid_counts.get('already_mitigated', 0)}), and poor shape/large wick ({invalid_counts.get('poor_shape_or_large_wick', 0)}). "
                "A direct reply says a block can remain valid after the daily draw has delivered, while the trade is no longer actionable. Models use stops beyond the structure/wick, but no universal close-through formula was captured. "
                "The targeted close-versus-wick and cross-market-mitigation questions also remained unanswered."
            ),
            "Exact price invalidation remains chart/model-specific; do not convert non-actionability into a universal technical invalidation rule.",
            first_ids(invalidation.get("analyst_synthesis", [])) + first_ids(direct_by_topic.get("invalidation_or_non_actionability", [])) + browser_ids("close_vs_wick_validity") + browser_ids("cross_market_mitigation"),
        ),
        record(
            "rb_timing",
            "At what times do rejection blocks primarily appear?",
            "partial",
            (
                f"10AM/10:00/10KO dominates RB-message co-mentions ({time_counts.get('10am_or_10_00_or_10ko', 0)}), followed by "
                f"9:30/market open ({time_counts.get('9_30_or_market_open', 0)}), midnight ({time_counts.get('00_00_or_midnight', 0)}), "
                f"London ({time_counts.get('london', 0)}), 18:00 ({time_counts.get('18_00_or_1800', 0)}), and Asia ({time_counts.get('asia', 0)}). "
                "Strict eligible 10AM RB episodes are 38W/102L (n=140, 27.1% descriptive share). A direct reply gives an 11AM cutoff for that setup. "
                "A targeted timeframe/window question received only adjacent community timeframe context, not a responsive universal trading window."
            ),
            "Message co-mentions are not setup occurrences. Only explicit setup/session labels are used; Discord post timestamps are provenance, not block time. Timezones are often unstated.",
            first_ids(timing.get("rb_message_time_co_mentions", [])[:3]) + first_ids(direct_by_topic.get("timing", [])) + browser_ids("timeframe_and_trading_window"),
        ),
        record(
            "rb_higher_association",
            "Which confluences have the higher selected-corpus association?",
            "partial",
            (
                f"The strict RB baseline is {baseline.get('eligible_rb_wins')}W/{baseline.get('eligible_rb_losses')}L "
                f"(n={baseline.get('eligible_rb_instances')}, {float(baseline.get('baseline_descriptive_win_share') or 0):.1%}). "
                f"Largest above-baseline associations meeting the script's minimum sample are: {high_text}. "
                "Qualitative A/A+ descriptions still emphasize aligned bias/draw, meaningful fresh location, manipulation, confirmation, protected risk, and an open target—not tag count alone. "
                "In the targeted browser audit, Domme's direct answer was simply to add confluences; a separate mentor reply said nested RBs work too but did not claim they are superior."
            ),
            "Components overlap, models and authors overlap, and smaller samples are unstable. These are not calibrated probabilities, causal effects, or expectancy.",
            first_ids(high[:6]) + browser_ids("higher_probability_confluences") + browser_ids("nested_rejection_blocks"),
        ),
        record(
            "rb_lower_association",
            "Which confluences or conditions have the lower selected-corpus association?",
            "partial",
            (
                f"Below-baseline component associations are: {low_text}. This does not prove those components are harmful. "
                "More actionable low-quality filters in explicit reviews are unclosed/front-run triggers, mitigated or exhausted blocks, "
                "bias conflict, poor/random 1m shape, incomplete planned taps, unresolved correlated liquidity, news/chop, and arbitrary stop placement."
            ),
            "Observed component shares are selected-corpus descriptions; use explicit no-trade rules separately from numeric association.",
            first_ids(low[:6]) + first_ids(direct_by_topic.get("lower_probability_claim", [])),
        ),
        record(
            "nq_vs_es",
            "Do rejection blocks work better on NQ than ES?",
            "insufficient_evidence",
            (
                "No supportable superiority conclusion. The strict executed-instrument RB denominator contains "
                f"{instrument_basis.get('eligible_executed_NQ_family_instances', 0)} NQ-family instances and "
                f"{instrument_basis.get('eligible_executed_ES_family_instances', 0)} ES-family instances; the analyzer requires at least "
                f"{instrument_basis.get('minimum_each_for_descriptive_head_to_head', 10)} in each. Both instruments appear, and ES is often context/SMT rather than the executed instrument."
                " The targeted browser follow-up also found no responsive mentor answer to the ES-applicability question; an off-target community reply does not resolve it."
            ),
            "Mention counts and context instruments cannot substitute for executed-instrument outcomes. Most strict trades have unknown executed instrument.",
            first_ids((answers.get("instrument_comparison") or {}).get("analyst_synthesis", [])) + browser_ids("es_applicability"),
        ),
        record(
            "win_profile",
            "What does the strict win profile look like?",
            "answered",
            f"There are {strict.get('win', 0)} strict comparable wins. Canonical confluence frequencies: {profile_top(win_profile)}. These labels overlap within trades.",
            "Self-reported selected journals; screenshots are not decoded, most instruments are unknown, and frequency among wins does not show incremental lift.",
            [str(item.get("message_id")) for episode in trade.get("episodes", []) if episode.get("outcome") == "win" and int(episode.get("eligible_trade_instances_for_win_loss_confluence_comparison") or 0) > 0 for item in (episode.get("evidence") or [])][:12],
        ),
        record(
            "loss_profile",
            "What does the strict loss profile look like?",
            "answered",
            f"There are {strict.get('loss', 0)} strict comparable losses. Canonical confluence frequencies: {profile_top(loss_profile)}. Explicit execution issues are cataloged separately and do not explain every loss.",
            "Self-reported selected journals; confluence presence in losses does not prove causation, and missing chart context remains unknown.",
            [str(item.get("message_id")) for episode in trade.get("episodes", []) if episode.get("outcome") == "loss" and int(episode.get("eligible_trade_instances_for_win_loss_confluence_comparison") or 0) > 0 for item in (episode.get("evidence") or [])][:12],
        ),
        record(
            "trading_models",
            "Which materially distinct trading models are supported?",
            "answered",
            f"Four models cleared the evidence threshold: {model_text}. No fifth model was added merely to fill the limit.",
            "Model counts can overlap on the same episodes. M4 has only n=2 strict outcomes and is explicitly insufficient for association ranking.",
            [str(item.get("message_id")) for model in model_analysis.get("models", []) for item in (model.get("evidence") or [])[:3]][:12],
        ),
        record(
            "qa_coverage",
            "What direct rejection-block Q&A was captured?",
            "partial",
            (
                f"The curated layer contains {sum(item['status'] == 'answered' for item in qa_pairs)} answered, "
                f"{sum(item['status'] == 'partial' for item in qa_pairs)} partial, and "
                f"{sum(item['status'] == 'unanswered' for item in qa_pairs)} selected unanswered Q&A records. "
                f"The RB artifact contains {len(rb.get('related_qa') or [])} related questions, and the browser audit resolves or qualifies eight selected contexts."
            ),
            "Only direct linked replies are authoritative answers. Community-only and adjacent responses remain partial; unresolved questions are not completed by inference.",
            [str(value) for item in qa_pairs if item["status"] in {"answered", "partial"} for value in item.get("evidence_message_ids", [])][:12],
        ),
        record(
            "contradictions",
            "Which contradictions or unresolved tensions matter?",
            "partial",
            "The artifact records conditional tension over whether a liquidity sweep is universally required, mixed positive/negative treatment of 1m RB quality, an off-target ES-applicability reply, and a large unanswered-question gap.",
            "These tensions can be chart-conditional. The corpus does not resolve them into universal rules.",
            [str(value) for item in contradictions for value in item.get("evidence_message_ids", [])][:12],
        ),
    ]


def markdown_citation(message_id: str, permalinks: dict[str, str | None]) -> str:
    link = permalinks.get(message_id)
    return f"[{message_id}]({link})" if link else f"`{message_id}` (permalink unavailable in export)"


def render_summary(
    curated: dict[str, Any],
    raw: dict[str, Any],
    rb: dict[str, Any],
    trade: dict[str, Any],
    model: dict[str, Any],
    followup: dict[str, Any],
    permalinks: dict[str, str | None],
) -> str:
    answers = {item["key"]: item for item in curated["research_questions"]}
    coverage = (raw.get("metadata") or {}).get("merge") or {}
    rb_counts = rb.get("corpus_counts") or {}
    trade_summary = trade.get("episode_summary") or {}
    associations = curated["synthesis"]["confluence_associations"]
    followup_scope = followup.get("scope") or {}
    capture_summary = curated["synthesis"]["browser_context_followup"]

    def evidence_line(item: dict[str, Any], limit: int = 4) -> str:
        ids = item.get("evidence_message_ids", [])[:limit]
        return "Evidence: " + ", ".join(markdown_citation(value, permalinks) for value in ids) if ids else "Evidence: see embedded source artifact."

    lines: list[str] = [
        "# Three-Month Discord Trading Research Summary",
        "",
        "## Scope and evidence standard",
        "",
        f"This synthesis covers the inclusive requested window **{coverage.get('requested_window_start_date')} through {coverage.get('requested_window_end_date')}** and uses only the supplied Discord server artifacts. The merge contains **{coverage.get('unique_messages', len(curated.get('trades', [])))} unique messages**, with **{coverage.get('completed_segments')} / {coverage.get('expected_segments')}** primary segments and **{coverage.get('supplemental_validated_file_count')}** validated supplemental files. A targeted authenticated-browser audit captured **{followup_scope.get('captured_contexts')} selected permalink contexts / {capture_summary['captured_messages']} visible messages**, adding **{capture_summary['messages_not_in_merged_raw']} messages** not already present in the merged export.",
        "",
        "Evidence labels mean:",
        "",
        "- **Explicit Discord rule/answer:** direct wording from a captured message or reply; chart-specific warnings still apply.",
        "- **Observed association:** a count or descriptive win share inside the selected, self-reported corpus.",
        "- **Synthesis:** a conservative combination of explicit and observed evidence.",
        "- **Insufficient evidence:** the required denominator or direct answer is absent.",
        "- **Authority tier:** named-mentor direct replies, community direct replies, and adjacent context are kept separate; adjacency is never silently upgraded into an answer.",
        "",
        "> Nothing here is a backtest, causal estimate, calibrated probability, or market expectancy.",
        "",
        "## Executive answer",
        "",
        "The Discord corpus treats rejection blocks as **contextual rejection structures**, not standalone wick patterns. The most defensible workflow is: establish bias or unresolved draw; locate a fresh, meaningful PDA/liquidity/key-open interaction; wait for the required tap and close/trigger; define risk beyond real structure; and target a still-open draw. A technically valid block can still be non-actionable when mitigated, exhausted, off-bias, mistimed, or poorly risk-defined.",
        "",
    ]
    sections = [
        ("How members identify rejection blocks", "rb_identification"),
        ("Invalidation and non-actionability", "rb_invalidation"),
        ("When rejection blocks appear", "rb_timing"),
        ("Higher selected-corpus associations", "rb_higher_association"),
        ("Lower selected-corpus associations and no-trade filters", "rb_lower_association"),
        ("NQ versus ES", "nq_vs_es"),
        ("Win profile", "win_profile"),
        ("Loss profile", "loss_profile"),
    ]
    for heading, key in sections:
        item = answers[key]
        lines.extend([f"## {heading}", "", item["answer_summary"], "", f"**Limit:** {item['limitations']}", "", evidence_line(item), ""])

    lines.extend([
        "## Selected-corpus RB association table",
        "",
        f"Strict RB baseline: **{associations['baseline']['eligible_rb_wins']}W / {associations['baseline']['eligible_rb_losses']}L**, n={associations['baseline']['eligible_rb_instances']}, descriptive win share **{associations['baseline']['baseline_descriptive_win_share']:.1%}**.",
        "",
        "| Direction | Component | W | L | n | Descriptive share | Difference vs RB baseline |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for direction, items in (("Higher", associations["higher_observed_associations"][:8]), ("Lower", associations["lower_observed_associations"][:8])):
        for item in items:
            lines.append(
                f"| {direction} | {item['confluence_family']} | {item['eligible_rb_wins']} | {item['eligible_rb_losses']} | {item['eligible_rb_instances']} | {item['descriptive_win_share']:.1%} | {item['difference_from_all_eligible_rb_win_share']:+.1%} |"
            )
    lines.extend(["", "Components overlap; this table does not isolate incremental value.", ""])

    profiles_by_outcome = {item["outcome"]: item for item in curated["outcome_profiles"]}
    win = profiles_by_outcome["win"]
    loss = profiles_by_outcome["loss"]
    win_features = {item["name"]: item for item in win.get("confluences", [])}
    loss_features = {item["name"]: item for item in loss.get("confluences", [])}
    profile_features = sorted(
        set(win_features) | set(loss_features),
        key=lambda name: -(
            int((win_features.get(name) or {}).get("observed_count") or 0)
            + int((loss_features.get(name) or {}).get("observed_count") or 0)
        ),
    )[:12]
    lines.extend([
        "## Strict win/loss profile comparison",
        "",
        f"This comparison uses **{win['resolved_trade_count']} wins** and **{loss['resolved_trade_count']} losses**. It shows prevalence within each outcome profile, not causal importance.",
        "",
        "| Canonical feature | Wins | Win share | Losses | Loss share |",
        "|---|---:|---:|---:|---:|",
    ])
    for feature in profile_features:
        win_item = win_features.get(feature) or {}
        loss_item = loss_features.get(feature) or {}
        win_count = int(win_item.get("observed_count") or 0)
        loss_count = int(loss_item.get("observed_count") or 0)
        win_share = float(win_item.get("observed_share") or 0)
        loss_share = float(loss_item.get("observed_share") or 0)
        lines.append(f"| {feature} | {win_count} | {win_share:.1%} | {loss_count} | {loss_share:.1%} |")
    for outcome_name, profile in (("Win", win), ("Loss", loss)):
        issues = ", ".join(
            f"{item['issue']} ({item['observed_count']})"
            for item in (profile.get("rules_or_execution_issues") or [])[:6]
        ) or "none repeatedly extracted"
        lines.extend([
            "",
            f"**{outcome_name} profile execution/issues:** {issues}.",
            f"**Author concentration:** {profile.get('author_concentration') or 'not available'}.",
        ])
    lines.extend(["", "Confluences overlap within a trade; the win and loss columns must not be summed into mutually exclusive buckets.", ""])

    lines.extend([
        "## Trading models",
        "",
        "| Model | Classification | Comparable W/L | n | Corpus-support confidence |",
        "|---|---|---:|---:|---|",
    ])
    for item in model.get("models", []) or []:
        counts = item["supporting_outcome_counts"]
        lines.append(
            f"| {item['name']} | {item['classification']} | {counts['comparable_wins']}W/{counts['comparable_losses']}L | {counts['comparable_resolved_instances']} | {item['confidence']['level']} |"
        )
    lines.extend([
        "",
        "The model artifact contains exact inclusion/exclusion rules, entry, invalidation, risk, target/management, failure examples, and evidence. M4's 2W/0L has only n=2 and is intentionally excluded from association ranking.",
        "",
        evidence_line(answers["trading_models"]),
        "",
        "## Operational model cards",
        "",
    ])
    for item in curated["models"]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for rule in item.get("rules", []) or []:
            rule_type = str(rule.get("type") or "other")
            rule_text = normalize_space(rule.get("text"))
            if rule_text and rule_text not in grouped[rule_type]:
                grouped[rule_type].append(rule_text)
        model_evidence = ", ".join(
            markdown_citation(str(value.get("message_id") or ""), permalinks)
            for value in (item.get("evidence") or [])[:4]
        ) or "See the model artifact."
        lines.extend([
            f"### Model {item['model_no']}: {item['name']}",
            "",
            item.get("thesis") or "",
            "",
            f"- **Context/identification:** {'; '.join(grouped.get('context', []) + grouped.get('identification', [])) or 'See source model artifact.'}",
            f"- **Entry/confirmation:** {'; '.join(grouped.get('entry', [])) or item.get('entry') or 'See source model artifact.'}",
            f"- **Invalidation/no-trade:** {'; '.join(grouped.get('invalidation', []) + grouped.get('no_trade', [])) or item.get('invalidation') or 'See source model artifact.'}",
            f"- **Risk/stop:** {'; '.join(grouped.get('risk', [])) or item.get('stop') or 'See source model artifact.'}",
            f"- **Target/management:** {'; '.join(grouped.get('target', []) + grouped.get('management', [])) or item.get('target') or 'See source model artifact.'}",
            f"- **Strict selected-corpus outcome:** {item.get('win_count', 0)}W/{item.get('loss_count', 0)}L; evidence status `{item.get('evidence_status')}`. Counts are overlapping, descriptive corpus associations—not expectancy.",
            f"- **Evidence:** {model_evidence}",
            "",
        ])
    lines.extend(["## Direct answered Q&A", ""])
    answered_qa = [item for item in curated["qa_pairs"] if item["status"] == "answered"]
    for index, item in enumerate(answered_qa, start=1):
        qid = str(item.get("question_message_id") or "")
        aid = str(item.get("answer_message_id") or "")
        lines.extend(
            [
                f"{index}. **Question:** {item['normalized_question']}",
                f"   **Captured reply:** {item['answer_summary']}",
                f"   **Evidence:** {markdown_citation(qid, permalinks)} → {markdown_citation(aid, permalinks)}",
            ]
        )
    lines.extend(["", "## Unresolved tensions and questions", ""])
    for item in curated["contradictions"]:
        lines.append(f"- **{item['topic']}:** {item['description']} ({item['resolution_status']})")
    selected_unanswered = [item for item in curated["qa_pairs"] if item["status"] == "unanswered"][:10]
    lines.extend(["", "Selected unanswered questions retained for LLM lookup:", ""])
    for item in selected_unanswered:
        lines.append(f"- {item['normalized_question']} — {markdown_citation(str(item['question_message_id']), permalinks)}")

    lines.extend([
        "",
        "## Targeted browser-context audit",
        "",
        "These eight contexts were opened by direct Discord permalink after the search-export pass. The audit is complete only for the selected visible contexts, not for their containing channels.",
        "",
        "| Context | Captured status | Authority/result | Target |",
        "|---|---|---|---|",
    ])
    browser_qa = {
        str(item.get("source_qa_id") or "").removeprefix("browser-"): item
        for item in curated["qa_pairs"]
        if str(item.get("source_qa_id") or "").startswith("browser-")
    }
    for context in followup.get("contexts", []) or []:
        context_id = str(context.get("context_id") or "")
        qa = browser_qa.get(context_id) or {}
        authority = str(qa.get("source_authority") or "unresolved")
        target_id = str(context.get("target_message_id") or "")
        lines.append(
            f"| {context_id} | {context.get('status')} | {authority}: {normalize_space(context.get('resolution'))} | {markdown_citation(target_id, permalinks)} |"
        )

    lines.extend([
        "",
        "## Data quality and limitations",
        "",
        f"- Trade extractor: **{trade_summary.get('episode_records')} episode records**; strict comparison denominator **{(trade_summary.get('eligible_actual_trade_instances_for_win_loss_confluence_comparison') or {}).get('win', 0) + (trade_summary.get('eligible_actual_trade_instances_for_win_loss_confluence_comparison') or {}).get('loss', 0)}** ({(trade_summary.get('eligible_actual_trade_instances_for_win_loss_confluence_comparison') or {}).get('win', 0)} wins, {(trade_summary.get('eligible_actual_trade_instances_for_win_loss_confluence_comparison') or {}).get('loss', 0)} losses).",
        f"- RB analyzer: **{rb_counts.get('rb_term_unique_messages', rb_counts.get('rb_containing_messages'))} RB-term messages** and **{(associations['baseline']).get('eligible_rb_instances')} strict eligible RB instances**.",
        f"- Curated Q&A includes **{sum(item['status'] == 'answered' for item in curated['qa_pairs'])} answered**, **{sum(item['status'] == 'partial' for item in curated['qa_pairs'])} partial**, and **{sum(item['status'] == 'unanswered' for item in curated['qa_pairs'])} selected unanswered** records; the full RB artifact retains the larger unresolved set.",
        f"- Browser follow-up: **{capture_summary['captured_contexts']} contexts / {capture_summary['captured_messages']} captured messages**; only those contexts were audited, and community/adjacent statements retain lower authority.",
        "- Most executed instruments are unknown; context mentions such as ES SMT are not recoded as executed ES trades.",
        "- Images/screenshots were not independently interpreted, so chart-only rules remain unknown.",
        "- Journals are self-reported and selectively posted; author and strategy overlap are substantial.",
        "- Time labels are retained as written. Discord posting timestamps are not setup timestamps.",
        "- Exact Discord permalinks are included when present or uniquely inferable from the export; otherwise the immutable message ID is retained with an unavailable marker.",
        "",
        "## Companion artifacts",
        "",
        "- `curated_analysis_3month.json` — database-ready synthesis and all curated evidence links",
        "- `raw_discord_export_3month.json` — merged raw Discord export",
        "- `trade_analysis_3month.json` — conservative trade episodes and strict profiles",
        "- `rb_analysis_3month.json` — explicit rules, associations, Q&A, tensions, and sufficiency",
        "- `model_analysis_3month.json` — operational model rules, counts, failures, and evidence",
        "- `browser_context_followups_3month.json` — eight targeted permalink contexts with direct-reply linkage and authority cautions",
        "- `README_FOR_LLM_3MONTH.md` — instructions for reliable LLM use",
        "",
    ])
    return "\n".join(lines)


def render_readme(
    curated: dict[str, Any], raw: dict[str, Any], trade: dict[str, Any], followup: dict[str, Any]
) -> str:
    merge = (raw.get("metadata") or {}).get("merge") or {}
    strict = (trade.get("episode_summary") or {}).get("eligible_actual_trade_instances_for_win_loss_confluence_comparison") or {}
    browser = curated["synthesis"]["browser_context_followup"]
    return f"""# LLM Guide — Three-Month Discord Trading Research

## Purpose

Use these artifacts to answer questions about the supplied Discord corpus only. Do not supplement answers with web knowledge, generic ICT teaching, chart guesses, or market data.

Coverage: **{merge.get('requested_window_start_date')} through {merge.get('requested_window_end_date')}**, inclusive. The merged export contains **{merge.get('unique_messages')} unique Discord messages**. A targeted browser audit adds **{browser['messages_not_in_merged_raw']} unique messages** across **{browser['captured_contexts']} selected contexts**; it is not a complete export of those channels. The strict trade-comparison denominator is **{int(strict.get('win', 0)) + int(strict.get('loss', 0))} instances** ({strict.get('win', 0)} wins and {strict.get('loss', 0)} losses).

## Recommended artifact order

1. `curated_analysis_3month.json` — start here for direct research answers, Q&A, profiles, models, contradictions, and evidence IDs.
2. `browser_context_followups_3month.json` — use for the eight targeted permalink contexts, visible direct-reply links, and context-level resolution status.
3. `rb_analysis_3month.json` — use for the full explicit-rule/association distinction, related questions, and evidence sufficiency.
4. `model_analysis_3month.json` — use for exact model inclusion/exclusion, risk, failure profiles, and strict model counts.
5. `trade_analysis_3month.json` — use for episode-level audits and strict outcome/confluence denominators.
6. `raw_discord_export_3month.json` — use for exact merged-export message text and provenance.
7. `discord_trading_research_3month.sqlite` — once built, use for relational/FTS queries.

## Evidence taxonomy

- `explicit`: captured Discord wording or a directly linked reply. It can still be chart-specific.
- `observed_association`: corpus count or descriptive share. It is not causal and is not out-of-sample probability.
- `derived`: conservative synthesis across Discord evidence.
- `insufficient_evidence`: denominator/direct answer is inadequate; do not force an answer.

Always state the evidence type in the answer. Prefer `evidence_message_ids` and `evidence[].permalink`. If a permalink is unavailable, cite the message ID without inventing a channel.

## Authority and linkage rules

For browser-follow-up evidence, preserve `source_authority` exactly:

- `named_mentor_direct_reply` is the strongest captured answer tier, while still potentially chart-specific.
- `community_direct_reply` is a member answer, not a mentor rule.
- `community_adjacent_context` is context only and must not be presented as a direct answer.
- `unresolved_question` means the audit did not supply a responsive answer.

The browser file covers eight deliberately selected contexts and 35 visible messages only. Never generalize its local completeness to an entire channel.

## Database tables and views

The three-month database builder imports the curated JSON into:

- `messages`, `message_sources`, `merged_message_provenance`
- `browser_context_followup_artifacts`, `browser_followup_contexts`, `browser_followup_context_messages`
- `rejection_block_findings`, `rejection_block_finding_evidence`
- `qa_pairs`
- `trades`, `trade_evidence`, `trade_confluences`
- `outcome_profiles`, `outcome_profile_confluences`
- `probability_tiers`
- `trading_models`, `model_rules`, `model_evidence`
- `research_questions`, `contradictions`, `analysis_documents`

Useful views include `v_browser_context_followups`, `v_rejection_block_evidence`, `v_answered_qa`, `v_trade_feature_matrix`, `v_model_cards`, and `v_llm_research_answers`. Use full-text search for exact source wording, then join back to evidence tables.

## Required answering procedure

1. Find the closest `research_questions` or curated `research_questions[]` record.
2. State whether it is answered, partial, or insufficient.
3. Separate explicit rules from observed associations and synthesis.
4. Cite the exact message IDs/permalinks supporting the claim.
5. When using outcomes, use only the strict eligible denominator unless explicitly discussing all episode records.
6. Keep executed instruments separate from market-context instruments. ES used for SMT does not prove the trade was executed on ES.
7. Preserve chart-dependent and timezone caveats.
8. Preserve browser-context statuses (`answered`, `partially_answered`, `community_answer_only`, `unresolved`) and do not promote community or adjacent text to mentor authority.
9. Never convert descriptive win share into expectancy, causal lift, or a forward success rate.

## Key denominator warnings

- Strict overall comparison: {strict.get('win', 0)} wins / {strict.get('loss', 0)} losses.
- Strict RB comparison: 92 wins / 217 losses, n=309.
- NQ-vs-ES RB comparison is insufficient: 13 NQ-family versus 3 ES-family eligible executed instances.
- Model membership overlaps; model counts cannot be added.
- Confluence frequencies overlap within trades; sums can exceed the denominator.
- M4 has only n=2 strict outcomes and must not be ranked as high probability.

## High-value query examples

- “Show the explicit Discord answers about RB close confirmation, then distinguish them from synthesis.”
- “List RB invalidation versus non-actionability findings with evidence links.”
- “Compare higher and lower observed confluence associations, including W/L/n and the baseline.”
- “Give me the 10AM model’s required rules, exclusions, failure profile, and exact evidence.”
- “Why can’t this corpus answer NQ versus ES?”
- “Show the strict loss profile without implying causation.”

## Output style for downstream LLMs

Use language such as:

- “The Discord corpus explicitly states…”
- “In the strict selected-corpus subset…”
- “This association is descriptive and overlapping…”
- “The available evidence is insufficient…”

Avoid:

- “This setup has a true X% win rate.”
- “This confluence causes better performance.”
- “RBs objectively work best at…”
- Any rule supplied from outside this Discord export.

## Refreshing after an RB-analysis update

Run:

```powershell
python ./discord_trading_research/compose_curated_analysis_3month.py
```

The composer deterministically rewrites only the three three-month curated outputs and validates every referenced Discord message ID and permalink before succeeding.
"""


def collect_message_refs(value: Any, key: str | None = None) -> set[str]:
    output: set[str] = set()
    if isinstance(value, dict):
        for child_key, child in value.items():
            output.update(collect_message_refs(child, child_key))
    elif isinstance(value, list):
        for child in value:
            output.update(collect_message_refs(child, key))
    elif isinstance(value, (str, int)):
        text = str(value)
        if (
            key in {"message_id", "question_message_id", "answer_message_id", "message_id_a", "message_id_b", "evidence_message_id"}
            or key == "evidence_message_ids"
        ) and re.fullmatch(r"\d{15,22}", text):
            output.add(text)
    return output


def validate_outputs(
    curated: dict[str, Any], messages: dict[str, dict[str, Any]], guild_id: str
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    refs = collect_message_refs(curated)
    unknown = sorted(refs - set(messages))
    if unknown:
        errors.append(f"unknown_message_references:{len(unknown)}")
    invalid_permalinks: list[dict[str, str]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            message_id = str(value.get("message_id") or "")
            permalink = value.get("permalink")
            if message_id and permalink:
                expected_suffix = "/" + message_id
                if not str(permalink).startswith(f"https://discord.com/channels/{guild_id}/") or not str(permalink).rstrip("/").endswith(expected_suffix):
                    invalid_permalinks.append({"message_id": message_id, "permalink": str(permalink)})
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(curated)
    if invalid_permalinks:
        errors.append(f"invalid_permalinks:{len(invalid_permalinks)}")
    trade_ids = [item.get("trade_id") for item in curated.get("trades", [])]
    if len(trade_ids) != len(set(trade_ids)):
        errors.append("duplicate_trade_ids")
    model_numbers = [item.get("model_no") for item in curated.get("models", [])]
    if len(model_numbers) > 5 or len(model_numbers) != len(set(model_numbers)):
        errors.append("model_count_or_number_uniqueness")
    for item in curated.get("rejection_block_findings", []):
        if item.get("facet") not in ALLOWED_FINDING_FACETS:
            errors.append(f"invalid_finding_facet:{item.get('source_finding_id')}")
        if item.get("evidence_status") not in ALLOWED_EVIDENCE_STATUS:
            errors.append(f"invalid_evidence_status:{item.get('source_finding_id')}")
        seen = [(e.get("message_id"), e.get("role")) for e in item.get("evidence", [])]
        if len(seen) != len(set(seen)):
            errors.append(f"duplicate_finding_evidence:{item.get('source_finding_id')}")
    for item in curated.get("qa_pairs", []):
        if item.get("status") not in ALLOWED_QA_STATUS:
            errors.append(f"invalid_qa_status:{item.get('source_qa_id')}")
        if item.get("status") == "answered" and not item.get("answer_message_id"):
            errors.append(f"answered_qa_missing_answer:{item.get('source_qa_id')}")
    if any(item.get("outcome") not in ALLOWED_TRADE_OUTCOMES for item in curated.get("trades", [])):
        errors.append("invalid_trade_outcome")
    if any(item.get("evidence_status") not in ALLOWED_MODEL_STATUS for item in curated.get("models", [])):
        errors.append("invalid_model_evidence_status")
    profile_counts = {item["outcome"]: item["resolved_trade_count"] for item in curated.get("outcome_profiles", [])}
    strict_trade_rows = Counter(
        item["outcome"]
        for item in curated.get("trades", [])
        if "strict_win_loss_comparison_eligible=1" in str(item.get("notes"))
    )
    if profile_counts.get("win") != strict_trade_rows.get("win") or profile_counts.get("loss") != strict_trade_rows.get("loss"):
        errors.append("profile_denominator_mismatch")
    if not curated.get("research_questions"):
        errors.append("missing_research_questions")
    if any(item.get("answer_status") not in {"answered", "partial", "insufficient_evidence"} for item in curated.get("research_questions", [])):
        errors.append("invalid_research_question_status")
    browser_scope = (curated.get("synthesis") or {}).get("browser_context_followup") or {}
    browser_qa_count = sum(
        str(item.get("source_qa_id") or "").startswith("browser-")
        for item in curated.get("qa_pairs", [])
    )
    if browser_qa_count != int(browser_scope.get("captured_contexts") or 0):
        errors.append("browser_context_qa_count_mismatch")
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "referenced_message_ids": len(refs),
        "unknown_message_ids": unknown,
        "invalid_permalinks": invalid_permalinks,
        "checks": [
            "all curated message references resolve to the merged raw plus targeted browser-followup union",
            "all emitted Discord permalinks match the guild and message ID",
            "trade and model identifiers are unique",
            "finding/QA evidence enums satisfy database constraints",
            "strict win/loss profile denominators reconcile to curated trade rows",
            "no more than five models are emitted",
            "every targeted browser context has one curated Q&A record with an explicit authority/status tier",
        ],
    }


def compose(
    raw_path: Path, trade_path: Path, rb_path: Path, model_path: Path, followup_path: Path
) -> tuple[dict[str, Any], str, str]:
    raw = load_json(raw_path)
    trade = load_json(trade_path)
    rb = load_json(rb_path)
    model = load_json(model_path)
    followup = load_json(followup_path)
    messages, permalinks = build_message_index(raw, followup)
    findings = build_rb_findings(rb, messages, permalinks)
    findings.extend(build_browser_followup_findings(followup, messages, permalinks))
    findings = sorted(findings, key=lambda item: (item["facet"], str(item.get("source_finding_id") or "")))
    qa_pairs = build_qa_pairs(rb, followup, messages, permalinks)
    contradictions = build_contradictions(rb, followup, messages, permalinks)
    trade_rows = build_trade_rows(trade, messages, permalinks)
    profiles = build_outcome_profiles(trade)
    association_catalog = build_association_catalog(rb)
    models = build_models(model, messages, permalinks)
    research = build_research_answers(
        raw, rb, trade, model, followup, profiles, association_catalog, qa_pairs, contradictions, messages, permalinks
    )
    inputs = {
        "raw_discord_export_3month.json": sha256(raw_path),
        "trade_analysis_3month.json": sha256(trade_path),
        "rb_analysis_3month.json": sha256(rb_path),
        "model_analysis_3month.json": sha256(model_path),
        "browser_context_followups_3month.json": sha256(followup_path),
    }
    followup_statuses = Counter(
        str(item.get("status") or "unknown") for item in followup.get("contexts", []) or []
    )
    browser_capture = {
        "captured_contexts": len(followup.get("contexts", []) or []),
        "captured_messages": len({str(item.get("message_id")) for item in followup.get("messages", []) or []}),
        "messages_not_in_merged_raw": sum(
            1 for item in messages.values() if item.get("_capture_source") == "browser_context_followups_3month"
        ),
        "combined_unique_message_index": len(messages),
        "context_status_counts": dict(sorted(followup_statuses.items())),
        "completeness_boundary": (followup.get("methodology") or {}).get("completeness_boundary"),
        "answer_linkage_policy": (followup.get("methodology") or {}).get("answer_linkage"),
        "authority_caution": (followup.get("methodology") or {}).get("authority_caution"),
    }
    curated: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_scope": "Discord only; no external trading knowledge or market data",
        "source_sha256": inputs,
        "rejection_block_findings": findings,
        "qa_pairs": qa_pairs,
        "trades": trade_rows,
        "outcome_profiles": profiles,
        "probability_tiers": build_probability_tiers(association_catalog),
        "models": models,
        "research_questions": research,
        "contradictions": contradictions,
        "synthesis": {
            "evidence_taxonomy": {
                "explicit": "Direct Discord wording or linked answer; may be chart-specific.",
                "observed_association": "Selected-corpus count/share; non-causal and not expectancy.",
                "derived": "Conservative synthesis of Discord artifacts.",
                "insufficient_evidence": "Required denominator or direct answer is absent.",
            },
            "confluence_associations": association_catalog,
            "coverage": (raw.get("metadata") or {}).get("merge"),
            "browser_context_followup": browser_capture,
            "guardrail": "No claim in this artifact is a market expectancy, causal estimate, or externally validated strategy result.",
        },
    }
    guild_id = str((raw.get("metadata") or {}).get("guild_id") or "1167376964680691732")
    curated["validation"] = validate_outputs(curated, messages, guild_id)
    summary = render_summary(curated, raw, rb, trade, model, followup, permalinks)
    readme = render_readme(curated, raw, trade, followup)
    return curated, summary, readme


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--trade", type=Path, default=DEFAULT_TRADE)
    parser.add_argument("--rb", type=Path, default=DEFAULT_RB)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--followup", type=Path, default=DEFAULT_FOLLOWUP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--readme", type=Path, default=DEFAULT_README)
    parser.add_argument("--check", action="store_true", help="Compose and validate without writing files.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    for name in ("raw", "trade", "rb", "model", "followup"):
        path = getattr(args, name).resolve()
        if not path.is_file():
            print(f"ERROR: missing --{name} input: {path}", file=sys.stderr)
            return 2
    for path in (args.output.resolve(), args.summary.resolve(), args.readme.resolve()):
        if path in PROTECTED_14DAY:
            print(f"ERROR: refusing to replace protected 14-day artifact: {path}", file=sys.stderr)
            return 2
    try:
        curated, summary, readme = compose(
            args.raw.resolve(),
            args.trade.resolve(),
            args.rb.resolve(),
            args.model.resolve(),
            args.followup.resolve(),
        )
    except ComposeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    validation = curated["validation"]
    report = {
        "check_only": args.check,
        "output": str(args.output.resolve()),
        "summary": str(args.summary.resolve()),
        "readme": str(args.readme.resolve()),
        "rb_findings": len(curated["rejection_block_findings"]),
        "qa_pairs": len(curated["qa_pairs"]),
        "answered_qa": sum(item["status"] == "answered" for item in curated["qa_pairs"]),
        "trade_rows": len(curated["trades"]),
        "profiles": len(curated["outcome_profiles"]),
        "models": len(curated["models"]),
        "research_questions": len(curated["research_questions"]),
        "contradictions": len(curated["contradictions"]),
        "validation": validation,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not validation["passed"]:
        return 1
    if args.check:
        return 0
    write_atomic(args.output.resolve(), json.dumps(curated, ensure_ascii=False, indent=2) + "\n")
    write_atomic(args.summary.resolve(), summary.rstrip() + "\n")
    write_atomic(args.readme.resolve(), readme.rstrip() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
