#!/usr/bin/env python3
"""Build a deterministic, Discord-only rejection-block research artifact.

Inputs:
    raw_discord_export_3month.json
    trade_analysis_3month.json

Output:
    rb_analysis_3month.json

The analyzer never reads market data, screenshots, or online sources. It keeps
Discord statements separate from observed journal associations and from the
script's cautious synthesis. Chart-dependent details remain unresolved unless
the message text itself states them.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_RAW = SCRIPT_DIR / "raw_discord_export_3month.json"
DEFAULT_TRADES = SCRIPT_DIR / "trade_analysis_3month.json"
DEFAULT_FOLLOWUPS = SCRIPT_DIR / "browser_context_followups_3month.json"
DEFAULT_OUTPUT = SCRIPT_DIR / "rb_analysis_3month.json"
PROTECTED_14_DAY_OUTPUT = (SCRIPT_DIR / "rb_analysis.json").resolve()
EXPECTED_3MONTH_START_DATE = "2026-04-20"
EXPECTED_3MONTH_END_DATE_INCLUSIVE = "2026-07-20"
EXPECTED_3MONTH_DAY_COUNT = 92

SPACE_RE = re.compile(r"\s+")
RB_RE = re.compile(r"\b(?:rbs?|rejection\s+blocks?)\b", re.IGNORECASE)
QUESTION_RE = re.compile(
    r"(?:\?|\b(?:can|could|do|does|did|is|are|would|should|must|how|"
    r"what|when|where|why|which)\b)",
    re.IGNORECASE,
)
PRESCRIPTIVE_RE = re.compile(
    r"\b(?:has\s+to|have\s+to|must|need(?:s)?\s+to|should|always|never|"
    r"wait(?:ed|ing)?\s+(?:for|until|til)|do\s+not|don['’]?t|"
    r"wouldn['’]?t\s+take|invalid(?:ated|ation)?|valid|not\s+valid|"
    r"not\s+good|better\s+if|i\s+stop\s+at|stops?\s+go)\b",
    re.IGNORECASE,
)
DEICTIC_RE = re.compile(
    r"\b(?:this|that|here|marked|in\s+the\s+image|on\s+the\s+chart|"
    r"the\s+one\s+i\s+marked)\b",
    re.IGNORECASE,
)

TIME_PATTERNS: dict[str, re.Pattern[str]] = {
    "10am_or_10_00_or_10ko": re.compile(
        r"\b(?:10\s*(?::?00)?\s*(?:a\.?m\.?)?|10\s*ko)\b", re.IGNORECASE
    ),
    "9_30_or_market_open": re.compile(
        r"\b(?:9\s*:?30|market\s+open|opening\s+bell)\b", re.IGNORECASE
    ),
    "8_30": re.compile(r"\b8\s*:?30\b", re.IGNORECASE),
    "7_30": re.compile(r"\b7\s*:?30\b", re.IGNORECASE),
    "00_00_or_midnight": re.compile(
        r"(?<!\d)(?:0?0\s*:?00|midnight(?:\s+open)?)(?!\d)", re.IGNORECASE
    ),
    "18_00_or_1800": re.compile(r"(?<!\d)18\s*:?00(?!\d)", re.IGNORECASE),
    "asia": re.compile(r"\basia(?:n)?(?:\s+session)?\b", re.IGNORECASE),
    "london": re.compile(r"\b(?:london|ldn)(?:\s+session)?\b", re.IGNORECASE),
    "3_to_5_am": re.compile(r"\b3\s*(?:-|to)\s*5\s*a\.?m\.?\b", re.IGNORECASE),
    "11am_stop": re.compile(r"\b11\s*(?::?00)?\s*a\.?m\.?\b", re.IGNORECASE),
}

CONFLUENCE_PATTERNS: dict[str, re.Pattern[str]] = {
    "liquidity_or_sweep": re.compile(
        r"\b(?:liquidity|sweep(?:ed|ing)?|bsl|ssl|pdh|pdl|"
        r"equal\s+(?:highs?|lows?))\b", re.IGNORECASE
    ),
    "fvg_or_imbalance": re.compile(
        r"\b(?:fvg|ifvg|bisi|sibi|fair\s+value\s+gap|"
        r"volume\s+imbalance|gap)\b", re.IGNORECASE
    ),
    "smt_or_ssmt": re.compile(r"\b(?:smt|ssmt)\b", re.IGNORECASE),
    "ote_or_fib": re.compile(
        r"\b(?:ote|fib(?:onacci)?|0?\.(?:5|50|62|618|705|79))\b|"
        r"\b(?:50|62|61\.8|70\.5|79)\s*%", re.IGNORECASE
    ),
    "bias_or_htf_alignment": re.compile(
        r"\b(?:bias|htf\s+align(?:ed|ment)|higher\s+timeframe)\b", re.IGNORECASE
    ),
    "key_open": re.compile(
        r"\b(?:10\s*(?::?00)?\s*(?:a\.?m\.?)?|10\s*ko|9\s*:?30|"
        r"8\s*:?30|18\s*:?00|midnight\s+open|weekly\s+open)\b",
        re.IGNORECASE,
    ),
    "order_block": re.compile(r"\b(?:ob|order\s+block)s?\b", re.IGNORECASE),
    "cisd": re.compile(r"\bcisd\b", re.IGNORECASE),
    "breaker": re.compile(r"\bbreaker(?:\s+block)?s?\b", re.IGNORECASE),
    "pd_array": re.compile(r"\b(?:pd\s*array|pda)s?\b", re.IGNORECASE),
    "standard_deviation": re.compile(
        r"\b(?:stdv|standard\s+deviation)\b", re.IGNORECASE
    ),
    "premium_or_discount": re.compile(r"\b(?:premium|discount)\b", re.IGNORECASE),
}

IDENTIFICATION_COMPONENTS: dict[str, re.Pattern[str]] = {
    "close_confirmation": re.compile(
        r"\b(?:wait(?:ed)?\s+for\s+(?:the\s+)?rb\s+to\s+close|"
        r"rb\s+(?:to\s+)?close|closed?\s+(?:bull|bear)|"
        r"close(?:d)?\s+(?:confirmation|bullish|bearish))\b", re.IGNORECASE
    ),
    "meaningful_rejection_or_wick": re.compile(
        r"\b(?:reject(?:ed|ion)?\s+(?:off|from)|not\s+(?:a\s+)?random\s+wick|"
        r"wick\s+rejection|swing\s+(?:high|low).*wick)\b", re.IGNORECASE
    ),
    "liquidity_sweep": CONFLUENCE_PATTERNS["liquidity_or_sweep"],
    "volume_imbalance_or_fvg": CONFLUENCE_PATTERNS["fvg_or_imbalance"],
    "ce_or_start_boundary": re.compile(
        r"\b(?:rb\s+ce|ce\s+of\s+(?:the\s+)?rb|start\s+of\s+(?:the\s+)?rb|"
        r"consequent\s+encroachment)\b", re.IGNORECASE
    ),
    "timeframe_selection": re.compile(
        r"\b(?:30\s*s|1\s*m(?:in)?|3\s*m(?:in)?|5\s*m(?:in)?|"
        r"10\s*m(?:in)?|15\s*m(?:in)?|1\s*h|4\s*h|daily)\s+rb\b",
        re.IGNORECASE,
    ),
}

INVALIDATION_COMPONENTS: dict[str, re.Pattern[str]] = {
    "already_mitigated": re.compile(r"\balready\s+mitigat(?:ed|ion)\b", re.IGNORECASE),
    "delivered_to_target": re.compile(
        r"\balready\s+delivered|delivered\s+to\s+(?:the\s+)?(?:daily\s+)?(?:high|low|target)\b",
        re.IGNORECASE,
    ),
    "disrespected_or_ran_through": re.compile(
        r"\b(?:disrespect(?:ed|s)?|ran\s+through|run\s+through|"
        r"didn['’]?t\s+hold|failed\s+to\s+hold|close(?:d)?\s+through)\b",
        re.IGNORECASE,
    ),
    "opposite_bias": re.compile(
        r"\b(?:opposite|against)\s+(?:of\s+)?(?:my\s+|the\s+)?bias\b",
        re.IGNORECASE,
    ),
    "poor_shape_or_large_wick": re.compile(
        r"\b(?:rb\s+(?:looked\s+)?(?:ass|bad|poor)|wick\s+(?:was\s+)?too\s+big|"
        r"meaningless\s+stop)\b", re.IGNORECASE
    ),
    "bad_candle_open_proximity": re.compile(
        r"\bright\s+before\s+(?:a\s+)?\d+\s*(?:m|min(?:ute)?)\s+candle\s+open\b",
        re.IGNORECASE,
    ),
    "missing_level_tap_or_confirmation": re.compile(
        r"\b(?:didn['’]?t|did\s+not)\s+(?:tap|wait|close)|"
        r"\bno\s+(?:confirmation|tap)\b", re.IGNORECASE
    ),
    "unfilled_imbalance_origin": re.compile(
        r"\bbeginning\s+of\s+an?\s+unfilled\s+imbalance|"
        r"\bunfilled\s+imbalance.*(?:not\s+good|avoid)\b", re.IGNORECASE
    ),
}

HIGH_CLAIM_RE = re.compile(
    r"\b(?:high(?:er)?\s+probability|add\s+confluences?|"
    r"a\+\s+setup|perfect\s+setup|"
    r"stacked\s+confluences?|multiple\s+confluences?|"
    r"good\s+(?:rb|setup)|ideally\s+.*confluence|"
    r"align(?:ed|ment)\s+with\s+(?:the\s+)?bias)\b",
    re.IGNORECASE,
)
LOW_CLAIM_RE = re.compile(
    r"\b(?:low(?:er)?\s+probability|single\s+rb|wouldn['’]?t\s+rely|"
    r"not\s+good\s+to\s+take|rb\s+(?:looked\s+)?(?:ass|bad|poor)|"
    r"random\s+(?:rb|wick)|against\s+(?:my\s+|the\s+)?bias|"
    r"already\s+mitigated|ran\s+through)\b",
    re.IGNORECASE,
)

INSTRUMENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "NQ": re.compile(r"(?<![A-Z0-9])NQ(?![A-Z0-9])", re.IGNORECASE),
    "MNQ": re.compile(r"(?<![A-Z0-9])MNQ(?![A-Z0-9])", re.IGNORECASE),
    "ES": re.compile(r"(?<![A-Z0-9])ES(?![A-Z0-9])", re.IGNORECASE),
    "MES": re.compile(r"(?<![A-Z0-9])MES(?![A-Z0-9])", re.IGNORECASE),
}

CONTRADICTION_PATTERNS: dict[str, dict[str, re.Pattern[str]]] = {
    "liquidity_sweep_requirement": {
        "required": re.compile(
            r"\b(?:rb.*(?:has|have|need)s?\s+to.*sweep|"
            r"sweep.*(?:required|must|has\s+to).*rb)\b", re.IGNORECASE
        ),
        "not_universal": re.compile(
            r"\b(?:rb.*(?:doesn['’]?t|does\s+not)\s+(?:have|need)\s+to.*sweep|"
            r"rb.*without.*sweep|not\s+every\s+rb.*sweep)\b", re.IGNORECASE
        ),
    },
    "one_minute_rb_quality": {
        "positive": re.compile(
            r"\b1\s*m(?:in(?:ute)?)?\s+rb\b.*\b(?:valid|good|win|profit|"
            r"entered|entry|took)\b", re.IGNORECASE
        ),
        "negative": re.compile(
            r"\b1\s*m(?:in(?:ute)?)?\s+rb\b.*\b(?:ass|bad|poor|"
            r"ran\s+through|wouldn['’]?t\s+rely|not\s+like)\b", re.IGNORECASE
        ),
    },
    "close_confirmation": {
        "required": re.compile(
            r"\b(?:wait.*rb.*close|rb.*must.*close|if\s+it\s+closed.*yes)\b",
            re.IGNORECASE,
        ),
        "not_required": re.compile(
            r"\b(?:rb.*(?:doesn['’]?t|does\s+not)\s+need\s+to\s+close|"
            r"enter.*before.*rb.*close)\b", re.IGNORECASE
        ),
    },
}

ARRAY_PRIORITY = {
    "browser_context_followup_messages": 120,
    "contextual_qa_messages": 100,
    "primary_messages": 90,
    "questions_rb_messages": 80,
    "questions_nq_es_messages": 75,
    "server_rejection_phrase_messages": 70,
    "broad_rb_shorthand_partial_messages": 60,
}


@dataclass
class Message:
    message_id: str
    timestamp_utc: str
    timestamp: datetime
    author: str
    text: str
    thread_title: str
    group_label: str
    parent_channel: str
    permalink: str | None
    reply_to_message_id: str | None
    reply_to_content: str
    reply_context: str
    attachments: list[dict[str, Any]]
    source_arrays: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def normalize(value: Any) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def excerpt(value: Any, limit: int = 700) -> str:
    text = normalize(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_timestamp(value: str) -> datetime:
    if not value:
        raise ValueError("missing timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def resolve_requested_window(metadata: dict[str, Any]) -> dict[str, Any]:
    merge = metadata.get("merge")
    if isinstance(merge, dict) and merge.get("requested_window_start_date"):
        start = str(merge.get("requested_window_start_date"))
        end_inclusive = str(merge.get("requested_window_end_date"))
        source = "metadata.merge"
    else:
        start = str(metadata.get("requested_window_start_date") or "")
        end_inclusive = str(metadata.get("requested_window_end_date") or "")
        source = "metadata_top_level_fallback"
    try:
        start_date = datetime.fromisoformat(start).date()
        end_date = datetime.fromisoformat(end_inclusive).date()
        end_exclusive = (end_date + timedelta(days=1)).isoformat()
        day_count = (end_date - start_date).days + 1
    except Exception:
        end_exclusive = None
        day_count = None
    return {
        "start_date_inclusive": start or None,
        "end_date_inclusive": end_inclusive or None,
        "end_date_exclusive": end_exclusive,
        "inclusive_calendar_days": day_count,
        "metadata_source": source,
    }


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def iter_message_rows(raw: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for array_name, value in raw.items():
        if not isinstance(value, list):
            continue
        for row in value:
            if isinstance(row, dict) and row.get("message_id"):
                yield array_name, row


def row_quality(array_name: str, row: dict[str, Any]) -> tuple[int, int, int]:
    text = normalize(row.get("content_text") or row.get("visible_text"))
    reply_text = normalize(row.get("reply_to_content"))
    score = ARRAY_PRIORITY.get(array_name, 40)
    if row.get("reply_to_message_id"):
        score += 30
    if reply_text and text == reply_text:
        score -= 50
    if text and text not in normalize(row.get("reply_context")):
        score += 10
    return score, min(len(text), 2000), -len(array_name)


def load_messages(raw: dict[str, Any]) -> tuple[list[Message], dict[str, Any]]:
    chosen: dict[str, tuple[str, dict[str, Any]]] = {}
    arrays_by_id: dict[str, set[str]] = defaultdict(set)
    row_count = 0
    malformed: list[dict[str, str]] = []
    for array_name, row in iter_message_rows(raw):
        row_count += 1
        message_id = str(row.get("message_id") or "").strip()
        arrays_by_id[message_id].add(array_name)
        old = chosen.get(message_id)
        if old is None or row_quality(array_name, row) > row_quality(*old):
            chosen[message_id] = (array_name, row)

    messages: list[Message] = []
    guild_id = normalize(raw.get("metadata", {}).get("guild_id"))
    thread_channels: dict[tuple[str, str], set[str]] = defaultdict(set)
    for _, row in chosen.values():
        channel_id = normalize(row.get("inferred_thread_channel_id"))
        if channel_id:
            thread_channels[(
                normalize(row.get("thread_title")),
                normalize(row.get("parent_channel")),
            )].add(channel_id)
    for message_id, (_, row) in chosen.items():
        try:
            stamp = parse_timestamp(str(row.get("timestamp_utc") or ""))
        except Exception as exc:
            malformed.append({"message_id": message_id, "reason": str(exc)})
            continue
        text = normalize(row.get("content_text") or row.get("visible_text"))
        permalink = (
            normalize(row.get("inferred_permalink"))
            or normalize(row.get("source_url"))
            or next(
                (
                    normalize(link) for link in (row.get("links") or [])
                    if "discord.com/channels/" in normalize(link)
                ),
                "",
            )
        )
        if permalink and guild_id:
            permalink = permalink.replace(
                "discord.com/channels/undefined/",
                f"discord.com/channels/{guild_id}/",
            )
        channel_id = normalize(row.get("inferred_thread_channel_id"))
        if not channel_id:
            candidates = thread_channels.get((
                normalize(row.get("thread_title")),
                normalize(row.get("parent_channel")),
            ), set())
            if len(candidates) == 1:
                channel_id = next(iter(candidates))
        if not permalink and guild_id and channel_id:
            permalink = (
                f"https://discord.com/channels/{guild_id}/"
                f"{channel_id}/{message_id}"
            )
        messages.append(Message(
            message_id=message_id,
            timestamp_utc=iso_z(stamp),
            timestamp=stamp,
            author=normalize(row.get("author")) or "unknown",
            text=text,
            thread_title=(
                normalize(row.get("thread_title"))
                or (
                    f"browser_context:{normalize(row.get('context_id'))}"
                    if row.get("context_id") else "unknown"
                )
            ),
            group_label=(
                normalize(row.get("group_label"))
                or (
                    "targeted_browser_followup"
                    if row.get("context_id") else ""
                )
            ),
            parent_channel=normalize(row.get("parent_channel")),
            permalink=permalink or None,
            reply_to_message_id=(
                str(row.get("reply_to_message_id")).strip()
                if row.get("reply_to_message_id") else None
            ),
            reply_to_content=normalize(row.get("reply_to_content")),
            reply_context=normalize(row.get("reply_context")),
            attachments=list(row.get("attachments") or []),
            source_arrays=sorted(arrays_by_id[message_id]),
            raw=row,
        ))
    messages.sort(key=lambda item: (item.timestamp, item.message_id))
    return messages, {
        "array_rows": row_count,
        "unique_message_ids_before_timestamp_validation": len(chosen),
        "duplicate_rows_removed": row_count - len(chosen),
        "malformed_messages": malformed,
        "usable_unique_messages": len(messages),
        "array_row_counts": dict(Counter(
            array_name for array_name, _ in iter_message_rows(raw)
        )),
    }


def merge_followup_messages(
    raw: dict[str, Any], followups: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Add browser follow-ups as a provenance-labeled message array."""
    existing_rows: dict[str, dict[str, Any]] = {}
    for _, row in iter_message_rows(raw):
        message_id = str(row.get("message_id") or "")
        if message_id and message_id not in existing_rows:
            existing_rows[message_id] = row
    rows = followups.get("messages")
    if not isinstance(rows, list):
        raise ValueError("follow-up artifact must contain a messages array")
    enriched: list[dict[str, Any]] = []
    for source_row in rows:
        if not isinstance(source_row, dict) or not source_row.get("message_id"):
            continue
        row = dict(source_row)
        old = existing_rows.get(str(row["message_id"]), {})
        row.setdefault("thread_title", old.get("thread_title"))
        row.setdefault("group_label", old.get("group_label"))
        row.setdefault("parent_channel", old.get("parent_channel"))
        row.setdefault("attachments", old.get("attachments") or [])
        row.setdefault("inferred_permalink", row.get("source_url"))
        row.setdefault("inferred_thread_channel_id", row.get("channel_id"))
        row.setdefault("visible_text", row.get("content_text"))
        enriched.append(row)
    combined = dict(raw)
    combined["browser_context_followup_messages"] = enriched
    followup_ids = {str(row.get("message_id")) for row in enriched}
    existing_ids = set(existing_rows)
    contexts = followups.get("contexts") or []
    return combined, {
        "schema_version": followups.get("schema_version"),
        "context_count": len(contexts),
        "message_rows": len(enriched),
        "unique_message_ids": len(followup_ids),
        "already_present_in_merged_raw": len(followup_ids & existing_ids),
        "new_to_merged_raw": len(followup_ids - existing_ids),
        "all_followup_message_ids": sorted(followup_ids),
        "scope": followups.get("scope"),
        "methodology": followups.get("methodology"),
    }


def is_question(message: Message) -> bool:
    if "?" in message.text:
        return True
    # Capture terse Discord questions without punctuation, but do not classify a
    # journal as a question merely because it contains words such as "did".
    prefix = re.sub(r"^(?:@\S+\s+){0,4}", "", message.text).lstrip()
    return bool(re.match(
        r"(?i)^(?:can|could|do|does|did|is|are|would|should|must|how|"
        r"what|when|where|why|which)\b",
        prefix,
    ))


def is_chart_dependent(message: Message) -> bool:
    return bool(message.attachments or DEICTIC_RE.search(message.text))


def instrument_mentions(text: str) -> set[str]:
    return {
        symbol for symbol, pattern in INSTRUMENT_PATTERNS.items()
        if pattern.search(text)
    }


def topic_labels(text: str) -> set[str]:
    topics: set[str] = set()
    if any(pattern.search(text) for pattern in IDENTIFICATION_COMPONENTS.values()):
        topics.add("identification")
    if any(pattern.search(text) for pattern in INVALIDATION_COMPONENTS.values()):
        topics.add("invalidation_or_non_actionability")
    if any(pattern.search(text) for pattern in TIME_PATTERNS.values()):
        topics.add("timing")
    if HIGH_CLAIM_RE.search(text):
        topics.add("higher_probability_claim")
    if LOW_CLAIM_RE.search(text):
        topics.add("lower_probability_claim")
    if instrument_mentions(text):
        topics.add("NQ_vs_ES_or_instrument")
    if not topics:
        topics.add("general_rejection_block")
    return topics


def source_tier(
    message: Message, mentor_names: set[str], question_ids: set[str]
) -> str:
    author_key = message.author.casefold()
    is_mentor = author_key in mentor_names
    is_direct_answer = bool(
        message.reply_to_message_id
        and message.reply_to_message_id in question_ids
    )
    if is_mentor and is_direct_answer:
        return "named_mentor_direct_reply"
    if is_mentor:
        return "named_mentor_statement"
    if is_direct_answer:
        return "peer_direct_reply"
    if "primary_messages" in message.source_arrays:
        return "journal_or_primary_channel_statement"
    return "peer_statement"


class EvidenceRegistry:
    def __init__(self, message_lookup: dict[str, Message]) -> None:
        self.message_lookup = message_lookup
        self.reasons: dict[str, set[str]] = defaultdict(set)
        self.missing_ids: set[str] = set()

    def add(self, message_id: str, reason: str) -> str | None:
        if message_id not in self.message_lookup:
            self.missing_ids.add(message_id)
            return None
        self.reasons[message_id].add(reason)
        return message_id

    def add_many(self, ids: Iterable[str], reason: str, limit: int | None = None) -> list[str]:
        kept: list[str] = []
        for message_id in ids:
            if limit is not None and len(kept) >= limit:
                break
            added = self.add(str(message_id), reason)
            if added and added not in kept:
                kept.append(added)
        return kept

    def catalog(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for message_id in sorted(
            self.reasons,
            key=lambda key: (
                self.message_lookup[key].timestamp,
                key,
            ),
        ):
            message = self.message_lookup[message_id]
            result[message_id] = {
                "message_id": message_id,
                "timestamp_utc": message.timestamp_utc,
                "author": message.author,
                "group_label": message.group_label,
                "parent_channel": message.parent_channel,
                "thread_title": message.thread_title,
                "permalink": message.permalink,
                "excerpt": excerpt(message.text),
                "reply_to_message_id": message.reply_to_message_id,
                "source_arrays": message.source_arrays,
                "has_attachment": bool(message.attachments),
                "chart_dependent_warning": is_chart_dependent(message),
                "included_for": sorted(self.reasons[message_id]),
            }
        return result


def select_rb_corpus(
    messages: Sequence[Message],
) -> tuple[list[Message], list[Message], set[str]]:
    lookup = {message.message_id: message for message in messages}
    seed_ids = {
        message.message_id for message in messages if RB_RE.search(message.text)
    }
    followup_ids = {
        message.message_id for message in messages
        if "browser_context_followup_messages" in message.source_arrays
    }
    question_ids = {
        message.message_id for message in messages
        if message.message_id in (seed_ids | followup_ids) and is_question(message)
    }
    related_ids = set(seed_ids | followup_ids)
    # Direct replies can be terse ("yes", "no") and therefore omit RB terms.
    changed = True
    while changed:
        changed = False
        for message in messages:
            if (
                message.reply_to_message_id in related_ids
                and message.message_id not in related_ids
            ):
                related_ids.add(message.message_id)
                changed = True
    seed = [lookup[message_id] for message_id in seed_ids]
    related = [lookup[message_id] for message_id in related_ids]
    seed.sort(key=lambda item: (item.timestamp, item.message_id))
    related.sort(key=lambda item: (item.timestamp, item.message_id))
    return seed, related, question_ids


def build_qa_catalog(
    related_messages: Sequence[Message],
    question_ids: set[str],
    mentor_names: set[str],
    registry: EvidenceRegistry,
    max_items: int,
) -> list[dict[str, Any]]:
    lookup = {message.message_id: message for message in related_messages}
    replies: dict[str, list[Message]] = defaultdict(list)
    for message in related_messages:
        if message.reply_to_message_id:
            replies[message.reply_to_message_id].append(message)
    rows: list[dict[str, Any]] = []
    for question_id in sorted(
        question_ids,
        key=lambda key: (lookup[key].timestamp, key) if key in lookup else (
            datetime.max.replace(tzinfo=timezone.utc), key
        ),
    ):
        question = lookup.get(question_id)
        if not question:
            continue
        answers = sorted(
            replies.get(question_id, []),
            key=lambda item: (item.timestamp, item.message_id),
        )
        question_ref = registry.add(question_id, "related_qa_question")
        answer_rows = []
        for answer in answers:
            registry.add(answer.message_id, "related_qa_answer")
            answer_rows.append({
                "message_id": answer.message_id,
                "author": answer.author,
                "timestamp_utc": answer.timestamp_utc,
                "permalink": answer.permalink,
                "answer_excerpt": excerpt(answer.text),
                "source_tier": source_tier(answer, mentor_names, question_ids),
                "chart_dependent_warning": is_chart_dependent(answer),
            })
        high_priority_question = bool(
            re.search(
                r"(?i)\b(?:valid|invalid|identify|mark|close|mitigat|"
                r"high(?:er)?\s+probability|low(?:er)?\s+probability|"
                r"what\s+time|when|NQ|MNQ|ES|MES)\b",
                question.text,
            )
        )
        mentor_answer = any(
            answer.author.casefold() in mentor_names for answer in answers
        )
        rows.append({
            "qa_id": None,
            "question_message_id": question_id,
            "question_author": question.author,
            "question_timestamp_utc": question.timestamp_utc,
            "question_permalink": question.permalink,
            "question_excerpt": excerpt(question.text),
            "topics": sorted(topic_labels(question.text)),
            "status": "answered_by_direct_reply" if answers else "unanswered_in_export",
            "answers": answer_rows,
            "evidence_message_ids": [
                value for value in [question_ref] + [
                    answer.message_id for answer in answers
                ] if value
            ],
            "chart_dependent_warning": bool(
                is_chart_dependent(question)
                or any(is_chart_dependent(answer) for answer in answers)
            ),
            "scope_note": (
                "The answer is retained as chart/example-specific where the text "
                "depends on an attachment or deictic wording."
                if is_chart_dependent(question)
                or any(is_chart_dependent(answer) for answer in answers)
                else "Textual question and direct reply; universality is not inferred."
            ),
            "_selection_priority": (
                0 if mentor_answer else 1 if answers
                else 2 if high_priority_question else 3
            ),
        })
    rows.sort(key=lambda row: (
        row["_selection_priority"], row["question_timestamp_utc"],
        row["question_message_id"],
    ))
    rows = rows[:max_items]
    for index, row in enumerate(rows, start=1):
        row["qa_id"] = f"RB-QA-{index:04d}"
        row.pop("_selection_priority", None)
    return rows


def build_followup_context_assessments(
    followups: dict[str, Any],
    message_lookup: dict[str, Message],
    mentor_names: set[str],
    registry: EvidenceRegistry,
) -> list[dict[str, Any]]:
    messages_by_context: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in followups.get("messages") or []:
        if isinstance(row, dict) and row.get("context_id"):
            messages_by_context[str(row["context_id"])].append(row)
    completeness = normalize(
        (followups.get("methodology") or {}).get("completeness_boundary")
    )
    assessments: list[dict[str, Any]] = []
    for context in followups.get("contexts") or []:
        if not isinstance(context, dict):
            continue
        context_id = str(context.get("context_id") or "")
        target_id = str(context.get("target_message_id") or "")
        rows = messages_by_context.get(context_id, [])
        roles = []
        evidence_ids: list[str] = []
        for row in rows:
            message_id = str(row.get("message_id") or "")
            message = message_lookup.get(message_id)
            if not message:
                registry.missing_ids.add(message_id)
                continue
            registry.add(message_id, f"targeted_browser_context:{context_id}")
            evidence_ids.append(message_id)
            author_is_mentor = message.author.casefold() in mentor_names
            if message_id == target_id:
                role = "target_question"
            elif message.reply_to_message_id == target_id and author_is_mentor:
                role = "named_mentor_direct_reply_to_target"
            elif message.reply_to_message_id == target_id:
                role = "community_direct_reply_to_target"
            elif author_is_mentor and message.reply_to_message_id:
                role = "named_mentor_direct_reply_to_other_question_in_context"
            elif author_is_mentor:
                role = "named_mentor_adjacent_unlinked_message"
            elif message.reply_to_message_id:
                role = "community_reply_to_other_message_in_context"
            else:
                role = "community_adjacent_unlinked_message"
            roles.append({
                "message_id": message_id,
                "author": message.author,
                "role": role,
                "reply_to_message_id": message.reply_to_message_id,
            })
        target = message_lookup.get(target_id)
        assessments.append({
            "context_id": context_id,
            "target_message_id": target_id,
            "target_permalink": target.permalink if target else None,
            "status": context.get("status"),
            "resolution": context.get("resolution"),
            "evidence_type": "analyst_synthesis",
            "message_count": len(evidence_ids),
            "message_roles": roles,
            "evidence_message_ids": evidence_ids,
            "completeness_boundary": completeness,
            "authority_boundary": (
                "Only named_mentor_direct_reply_to_target is treated as a mentor "
                "answer to the target. Nearby mentor replies, unlinked adjacency, "
                "and community replies retain their narrower roles."
            ),
        })
    return assessments


def apply_followup_qa_statuses(
    qa_catalog: list[dict[str, Any]],
    followup_assessments: Sequence[dict[str, Any]],
) -> None:
    by_target = {
        row["target_message_id"]: row for row in followup_assessments
    }
    status_mapping = {
        "answered": "answered_in_targeted_followup",
        "partially_answered": "partially_answered_in_targeted_followup",
        "community_answer_only": "community_answer_only_in_targeted_followup",
        "unresolved": "unresolved_after_targeted_followup",
    }
    for row in qa_catalog:
        assessment = by_target.get(row["question_message_id"])
        if not assessment:
            continue
        row["pre_followup_status"] = row["status"]
        row["status"] = status_mapping.get(
            str(assessment.get("status")), row["status"]
        )
        row["targeted_followup_context_id"] = assessment["context_id"]
        row["targeted_followup_resolution"] = assessment["resolution"]
        row["targeted_followup_evidence_message_ids"] = assessment[
            "evidence_message_ids"
        ]
        row["evidence_message_ids"] = list(dict.fromkeys(
            row["evidence_message_ids"] + assessment["evidence_message_ids"]
        ))


def build_explicit_claims(
    related_messages: Sequence[Message],
    question_ids: set[str],
    mentor_names: set[str],
    registry: EvidenceRegistry,
    max_per_topic: int,
    followup_by_target: dict[str, dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    lookup = {message.message_id: message for message in related_messages}
    claims: list[dict[str, Any]] = []
    for message in related_messages:
        question = lookup.get(message.reply_to_message_id or "")
        is_answer = bool(question and question.message_id in question_ids)
        has_rb = bool(RB_RE.search(message.text))
        explicit = bool(
            is_answer
            or (has_rb and PRESCRIPTIVE_RE.search(message.text))
            or (has_rb and (HIGH_CLAIM_RE.search(message.text)
                            or LOW_CLAIM_RE.search(message.text)))
        )
        if not explicit:
            continue
        context_text = " ".join(
            part for part in [question.text if question else "", message.text]
            if part
        )
        topics = topic_labels(context_text)
        followup_context = (
            (followup_by_target or {}).get(question.message_id)
            if question else None
        )
        # "They work too" answers existence, not the question's higher-
        # probability premise. Keep it in general RB evidence instead.
        if (
            followup_context
            and followup_context.get("context_id")
            == "higher_probability_confluences"
        ):
            if question and question.message_id == followup_context.get(
                "target_message_id"
            ):
                topics.add("higher_probability_claim")
            elif message.author.casefold() in mentor_names:
                topics.add("identification")
        if (
            followup_context
            and followup_context.get("context_id") == "nested_rejection_blocks"
            and message.author.casefold() in mentor_names
        ):
            topics.discard("higher_probability_claim")
            topics.add("general_rejection_block")
        registry.add(message.message_id, "discord_explicit_rule_or_answer")
        evidence_ids = [message.message_id]
        if question:
            registry.add(question.message_id, "question_context_for_explicit_answer")
            evidence_ids.insert(0, question.message_id)
        claims.append({
            "claim_id": f"RB-CLAIM-{len(claims) + 1:05d}",
            "evidence_type": "discord_explicit_rule_or_answer",
            "message_id": message.message_id,
            "author": message.author,
            "timestamp_utc": message.timestamp_utc,
            "source_tier": source_tier(message, mentor_names, question_ids),
            "topics": sorted(topics),
            "claim_excerpt": excerpt(message.text),
            "question_context_message_id": question.message_id if question else None,
            "question_context_excerpt": excerpt(question.text) if question else None,
            "evidence_message_ids": evidence_ids,
            "chart_dependent_warning": bool(
                is_chart_dependent(message)
                or (question is not None and is_chart_dependent(question))
            ),
            "interpretation_guard": (
                "Direct Discord wording is preserved. Treat as example-specific "
                "when the chart is not recoverable; do not promote to a universal rule."
            ),
            "targeted_followup_status": (
                followup_context.get("status") if followup_context else None
            ),
            "targeted_followup_resolution": (
                followup_context.get("resolution") if followup_context else None
            ),
        })

    tier_order = {
        "named_mentor_direct_reply": 0,
        "named_mentor_statement": 1,
        "peer_direct_reply": 2,
        "journal_or_primary_channel_statement": 3,
        "peer_statement": 4,
    }
    claims.sort(key=lambda row: (
        tier_order.get(row["source_tier"], 9),
        row["timestamp_utc"],
        row["message_id"],
    ))
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        for topic in claim["topics"]:
            if len(buckets[topic]) < max_per_topic:
                buckets[topic].append(claim)
    return dict(sorted(buckets.items()))


def observed_component_rows(
    messages: Sequence[Message],
    patterns: dict[str, re.Pattern[str]],
    registry: EvidenceRegistry,
    reason_prefix: str,
    example_limit: int,
) -> list[dict[str, Any]]:
    rows = []
    for component, pattern in patterns.items():
        matched = [message for message in messages if pattern.search(message.text)]
        if not matched:
            continue
        ids = registry.add_many(
            (message.message_id for message in matched),
            f"{reason_prefix}:{component}",
            limit=example_limit,
        )
        rows.append({
            "component": component,
            "message_count": len(matched),
            "evidence_type": "observed_textual_association",
            "evidence_message_ids": ids,
            "interpretation_guard": (
                "Co-mention in an RB-containing Discord message; not proof of a "
                "required condition or causal effect."
            ),
        })
    rows.sort(key=lambda row: (-row["message_count"], row["component"]))
    return rows


def episode_evidence_ids(
    episode: dict[str, Any], registry: EvidenceRegistry, reason: str, limit: int = 6
) -> list[str]:
    ids = [
        str(item.get("message_id")) for item in episode.get("evidence", [])
        if item.get("message_id")
    ]
    return registry.add_many(ids, reason, limit=limit)


def eligible_rb_episodes(trade_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        episode for episode in trade_analysis.get("episodes", [])
        if episode.get("rejection_block_use", {}).get("used")
        and episode.get(
            "eligible_trade_instances_for_win_loss_confluence_comparison"
        ) == 1
        and episode.get("outcome") in {"win", "loss"}
    ]


def build_confluence_associations(
    trade_analysis: dict[str, Any],
    registry: EvidenceRegistry,
    min_sample: int,
) -> dict[str, Any]:
    episodes = eligible_rb_episodes(trade_analysis)
    baseline_wins = sum(episode.get("outcome") == "win" for episode in episodes)
    baseline_total = len(episodes)
    baseline = baseline_wins / baseline_total if baseline_total else None
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes:
        families = {
            str(tag).split(":", 1)[0]
            for tag in episode.get("confluences", [])
            if not str(tag).startswith("rejection_block:")
        }
        for family in families:
            counts[family][str(episode.get("outcome"))] += 1
            examples[family].append(episode)

    rows = []
    for family, values in counts.items():
        wins, losses = values["win"], values["loss"]
        total = wins + losses
        if total < min_sample:
            continue
        refs: list[str] = []
        for episode in examples[family]:
            for message_id in episode_evidence_ids(
                episode, registry, f"episode_association:{family}", limit=3
            ):
                if message_id not in refs:
                    refs.append(message_id)
                if len(refs) >= 8:
                    break
            if len(refs) >= 8:
                break
        share = wins / total
        delta = share - baseline if baseline is not None else None
        rows.append({
            "confluence_family": family,
            "evidence_type": "observed_association",
            "eligible_rb_wins": wins,
            "eligible_rb_losses": losses,
            "eligible_rb_instances": total,
            "descriptive_win_share": round(share, 6),
            "difference_from_all_eligible_rb_win_share": round(delta, 6)
            if delta is not None else None,
            "evidence_message_ids": refs,
            "interpretation_guard": (
                "Self-reported Discord journal association only. It is not a "
                "causal estimate, backtest, or out-of-sample probability."
            ),
        })
    rows.sort(key=lambda row: (
        -row["eligible_rb_instances"], row["confluence_family"]
    ))
    higher = [
        row for row in rows
        if row["difference_from_all_eligible_rb_win_share"] is not None
        and row["difference_from_all_eligible_rb_win_share"] > 0
    ]
    lower = [
        row for row in rows
        if row["difference_from_all_eligible_rb_win_share"] is not None
        and row["difference_from_all_eligible_rb_win_share"] < 0
    ]
    higher.sort(key=lambda row: (
        -row["difference_from_all_eligible_rb_win_share"],
        -row["eligible_rb_instances"], row["confluence_family"]
    ))
    lower.sort(key=lambda row: (
        row["difference_from_all_eligible_rb_win_share"],
        -row["eligible_rb_instances"], row["confluence_family"]
    ))
    return {
        "eligibility_basis": (
            "Single-instance actual-or-unspecified episodes with explicit win/loss, "
            "attributable setup detail, and an RB linked by the trade extractor."
        ),
        "eligible_rb_instances": baseline_total,
        "eligible_rb_wins": baseline_wins,
        "eligible_rb_losses": baseline_total - baseline_wins,
        "baseline_descriptive_win_share": round(baseline, 6)
        if baseline is not None else None,
        "minimum_confluence_sample": min_sample,
        "all_associations": rows,
        "observed_higher_win_share_associations": higher,
        "observed_lower_win_share_associations": lower,
    }


def build_timing_associations(
    rb_messages: Sequence[Message],
    trade_analysis: dict[str, Any],
    registry: EvidenceRegistry,
    example_limit: int,
) -> dict[str, Any]:
    raw_rows = observed_component_rows(
        rb_messages, TIME_PATTERNS, registry, "rb_timing_mention", example_limit
    )
    episodes = eligible_rb_episodes(trade_analysis)
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes:
        labels = set(episode.get("setup_times_mentioned") or [])
        if not labels and episode.get("setup_time"):
            labels.add(str(episode["setup_time"]))
        if episode.get("session") not in {None, "unknown", "mixed"}:
            labels.add(f"session:{episode['session']}")
        for label in labels:
            counts[label][str(episode.get("outcome"))] += 1
            examples[label].append(episode)
    episode_rows = []
    for label, values in counts.items():
        wins, losses = values["win"], values["loss"]
        refs: list[str] = []
        for episode in examples[label]:
            for message_id in episode_evidence_ids(
                episode, registry, f"rb_episode_timing:{label}", limit=3
            ):
                if message_id not in refs:
                    refs.append(message_id)
                if len(refs) >= example_limit:
                    break
            if len(refs) >= example_limit:
                break
        episode_rows.append({
            "time_or_session_label": label,
            "evidence_type": "observed_association",
            "eligible_rb_wins": wins,
            "eligible_rb_losses": losses,
            "eligible_rb_instances": wins + losses,
            "descriptive_win_share": round(wins / (wins + losses), 6)
            if wins + losses else None,
            "evidence_message_ids": refs,
            "interpretation_guard": (
                "Setup/session labels come only from linked message text. Discord "
                "post timestamps are never treated as setup times."
            ),
        })
    episode_rows.sort(key=lambda row: (
        -row["eligible_rb_instances"], row["time_or_session_label"]
    ))
    return {
        "rb_message_time_co_mentions": raw_rows,
        "eligible_trade_episode_time_associations": episode_rows,
        "posting_timestamp_policy": (
            "All timestamp_utc values are Discord posting times; they are provenance "
            "only and are not interpreted as the time an RB appeared."
        ),
    }


def family_for_instrument(symbol: str) -> str | None:
    upper = symbol.upper()
    if upper in {"NQ", "MNQ"}:
        return "NQ_family"
    if upper in {"ES", "MES"}:
        return "ES_family"
    return None


def build_instrument_comparison(
    rb_messages: Sequence[Message],
    trade_analysis: dict[str, Any],
    registry: EvidenceRegistry,
    example_limit: int,
) -> dict[str, Any]:
    raw_counts = Counter()
    raw_examples: dict[str, list[str]] = defaultdict(list)
    for message in rb_messages:
        mentions = instrument_mentions(message.text)
        for symbol in mentions:
            raw_counts[symbol] += 1
            raw_examples[symbol].append(message.message_id)
        families = {family_for_instrument(symbol) for symbol in mentions}
        if "NQ_family" in families:
            raw_counts["NQ_family"] += 1
            raw_examples["NQ_family"].append(message.message_id)
        if "ES_family" in families:
            raw_counts["ES_family"] += 1
            raw_examples["ES_family"].append(message.message_id)
        if {"NQ_family", "ES_family"}.issubset(families):
            raw_counts["both_index_families_same_message"] += 1
            raw_examples["both_index_families_same_message"].append(
                message.message_id
            )
    raw_rows = []
    for label, count in sorted(raw_counts.items(), key=lambda item: (-item[1], item[0])):
        refs = registry.add_many(
            raw_examples[label], f"rb_instrument_coverage:{label}", limit=example_limit
        )
        raw_rows.append({
            "instrument_or_family": label,
            "rb_message_count": count,
            "evidence_type": "observed_textual_association",
            "evidence_message_ids": refs,
            "interpretation_guard": "Mention coverage; not an execution count or win rate.",
        })

    episodes = eligible_rb_episodes(trade_analysis)
    executed: dict[str, Counter[str]] = defaultdict(Counter)
    context: Counter[str] = Counter()
    episode_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes:
        execution_families = {
            family_for_instrument(str(symbol))
            for symbol in episode.get("instrument", [])
        } - {None}
        context_families = {
            family_for_instrument(str(symbol))
            for symbol in episode.get("market_context_instruments", [])
        } - {None}
        for family in execution_families:
            executed[str(family)][str(episode.get("outcome"))] += 1
            episode_examples[str(family)].append(episode)
        for family in context_families:
            context[str(family)] += 1
    executed_rows = []
    for family, values in sorted(executed.items()):
        wins, losses = values["win"], values["loss"]
        refs: list[str] = []
        for episode in episode_examples[family]:
            for message_id in episode_evidence_ids(
                episode, registry, f"rb_executed_instrument:{family}", limit=3
            ):
                if message_id not in refs:
                    refs.append(message_id)
                if len(refs) >= example_limit:
                    break
            if len(refs) >= example_limit:
                break
        executed_rows.append({
            "executed_instrument_family": family,
            "evidence_type": "observed_association",
            "eligible_rb_wins": wins,
            "eligible_rb_losses": losses,
            "eligible_rb_instances": wins + losses,
            "descriptive_win_share": round(wins / (wins + losses), 6)
            if wins + losses else None,
            "evidence_message_ids": refs,
            "interpretation_guard": (
                "Executed instrument requires explicit execution phrasing in the "
                "trade extractor; chart/context symbols are excluded."
            ),
        })
    nq_total = sum(
        row["eligible_rb_instances"] for row in executed_rows
        if row["executed_instrument_family"] == "NQ_family"
    )
    es_total = sum(
        row["eligible_rb_instances"] for row in executed_rows
        if row["executed_instrument_family"] == "ES_family"
    )
    head_to_head_supported = nq_total >= 10 and es_total >= 10
    return {
        "rb_message_mention_coverage": raw_rows,
        "eligible_rb_executed_instrument_outcomes": executed_rows,
        "eligible_rb_market_context_mentions": dict(sorted(context.items())),
        "head_to_head_evidence_sufficient": head_to_head_supported,
        "analyst_synthesis": [{
            "finding_id": "RB-INSTR-SYN-001",
            "evidence_type": "analyst_synthesis",
            "statement": (
                "The eligible executed-instrument sample contains enough explicit "
                "NQ-family and ES-family episodes for a descriptive comparison only; "
                "it still does not establish causal superiority."
                if head_to_head_supported else
                "The Discord export does not contain enough explicitly executed, "
                "eligible RB episodes in both NQ and ES families to conclude that RBs "
                "work better on either instrument. Mention counts cannot fill that gap."
            ),
            "basis_metrics": {
                "eligible_executed_NQ_family_instances": nq_total,
                "eligible_executed_ES_family_instances": es_total,
                "minimum_each_for_descriptive_head_to_head": 10,
            },
            "evidence_message_ids": [
                message_id
                for row in executed_rows
                for message_id in row["evidence_message_ids"]
            ][:example_limit],
            "confidence": (
                "limited_descriptive" if head_to_head_supported
                else "insufficient_for_relative_performance"
            ),
        }],
    }


def build_contradictions(
    related_messages: Sequence[Message],
    qa_catalog: Sequence[dict[str, Any]],
    registry: EvidenceRegistry,
    example_limit: int,
) -> list[dict[str, Any]]:
    tensions: list[dict[str, Any]] = []
    for topic, polarities in CONTRADICTION_PATTERNS.items():
        matched: dict[str, list[Message]] = {
            polarity: [
                message for message in related_messages
                if pattern.search(message.text)
            ]
            for polarity, pattern in polarities.items()
        }
        nonempty = [key for key, values in matched.items() if values]
        if len(nonempty) < 2:
            continue
        refs: list[str] = []
        for polarity in sorted(matched):
            refs.extend(registry.add_many(
                (message.message_id for message in matched[polarity]),
                f"contradiction:{topic}:{polarity}",
                limit=example_limit,
            ))
        refs = list(dict.fromkeys(refs))
        tensions.append({
            "tension_id": f"RB-TENSION-{len(tensions) + 1:03d}",
            "topic": topic,
            "kind": "textual_contradiction_or_conditional_tension",
            "evidence_type": "analyst_synthesis",
            "statement": (
                f"Captured Discord text contains both {nonempty[0]!r} and "
                f"{nonempty[1]!r} positions for {topic.replace('_', ' ')}. The "
                "script does not resolve chart-dependent conditions between them."
            ),
            "polarity_message_counts": {
                key: len(values) for key, values in matched.items()
            },
            "evidence_message_ids": refs,
            "resolution": "unresolved_or_conditional_in_captured_text",
        })

    unanswered = [
        row for row in qa_catalog
        if row["status"] in {
            "unanswered_in_export", "unresolved_after_targeted_followup"
        }
    ]
    if unanswered:
        refs = []
        for row in unanswered[:example_limit]:
            message_id = row["question_message_id"]
            registry.add(message_id, "unanswered_rb_question")
            refs.append(message_id)
        tensions.append({
            "tension_id": f"RB-TENSION-{len(tensions) + 1:03d}",
            "topic": "unanswered_questions",
            "kind": "evidence_gap",
            "evidence_type": "analyst_synthesis",
            "statement": (
                "Some RB questions have no direct reply in the merged export; the "
                "analyzer leaves them unresolved rather than supplying an answer."
            ),
            "unanswered_question_count": len(unanswered),
            "evidence_message_ids": refs,
            "resolution": "unresolved_in_export",
        })
    return tensions


def sufficiency_label(count: int, mentor_count: int, *, strong: int = 8) -> str:
    if count >= strong and mentor_count >= 1:
        return "adequate_for_descriptive_synthesis"
    if count >= 2:
        return "limited"
    return "insufficient"


def build_evidence_sufficiency(
    claims: dict[str, list[dict[str, Any]]],
    associations: dict[str, Any],
    instrument: dict[str, Any],
    qa_catalog: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    def claim_metrics(topic: str) -> tuple[int, int]:
        rows = claims.get(topic, [])
        mentors = sum(
            str(row.get("source_tier", "")).startswith("named_mentor")
            for row in rows
        )
        return len(rows), mentors

    topics = {}
    mapping = {
        "identification": "identification",
        "invalidation": "invalidation_or_non_actionability",
        "timing": "timing",
        "high_probability_claims": "higher_probability_claim",
        "low_probability_claims": "lower_probability_claim",
    }
    for output_topic, claim_topic in mapping.items():
        count, mentors = claim_metrics(claim_topic)
        topics[output_topic] = {
            "explicit_rule_or_answer_claims": count,
            "named_mentor_claims": mentors,
            "assessment": sufficiency_label(count, mentors),
        }
    eligible = associations["eligible_rb_instances"]
    topics["win_loss_associations"] = {
        "eligible_rb_instances": eligible,
        "minimum_confluence_sample": associations["minimum_confluence_sample"],
        "assessment": (
            "adequate_for_descriptive_association" if eligible >= 30
            else "limited" if eligible >= 10 else "insufficient"
        ),
        "guard": "Never sufficient for causal or out-of-sample probability claims.",
    }
    instrument_synthesis = instrument["analyst_synthesis"][0]
    topics["NQ_vs_ES_relative_performance"] = {
        **instrument_synthesis["basis_metrics"],
        "assessment": instrument_synthesis["confidence"],
    }
    answered_statuses = {
        "answered_by_direct_reply",
        "answered_in_targeted_followup",
        "partially_answered_in_targeted_followup",
        "community_answer_only_in_targeted_followup",
    }
    answered = sum(row["status"] in answered_statuses for row in qa_catalog)
    unresolved_after_followup = sum(
        row["status"] == "unresolved_after_targeted_followup"
        for row in qa_catalog
    )
    topics["related_qa"] = {
        "questions": len(qa_catalog),
        "answered_or_partially_answered": answered,
        "unresolved_after_targeted_followup": unresolved_after_followup,
        "unanswered_in_export": len(qa_catalog) - answered,
        "assessment": "mixed_answer_coverage" if qa_catalog else "insufficient",
    }
    return {
        "topic_assessments": topics,
        "overall": (
            "Suitable for evidence-backed Discord-corpus synthesis with caveats; "
            "not a validated trading system or market-performance study."
        ),
    }


def evidence_refs_from_rows(
    rows: Sequence[dict[str, Any]], limit: int = 12
) -> list[str]:
    refs: list[str] = []
    for row in rows:
        for message_id in row.get("evidence_message_ids", []):
            if message_id not in refs:
                refs.append(message_id)
            if len(refs) >= limit:
                return refs
    return refs


def component_summary(rows: Sequence[dict[str, Any]], limit: int = 5) -> str:
    if not rows:
        return "none captured"
    return ", ".join(
        f"{row['component']} ({row['message_count']})" for row in rows[:limit]
    )


def build_chart_dependent_records(
    related_messages: Sequence[Message],
    registry: EvidenceRegistry,
    limit: int,
) -> list[dict[str, Any]]:
    rows = []
    for message in related_messages:
        if not is_chart_dependent(message):
            continue
        registry.add(message.message_id, "chart_dependent_rb_record")
        reasons = []
        if message.attachments:
            reasons.append("attachment_present_but_not_interpreted")
        if DEICTIC_RE.search(message.text):
            reasons.append("text_depends_on_this_that_here_or_marked_chart_context")
        rows.append({
            "message_id": message.message_id,
            "permalink": message.permalink,
            "timestamp_utc": message.timestamp_utc,
            "author": message.author,
            "excerpt": excerpt(message.text),
            "reasons": reasons,
            "treatment": (
                "Retained as Discord evidence, but excluded from geometry-specific "
                "or universal chart-rule inference."
            ),
            "evidence_message_ids": [message.message_id],
        })
        if len(rows) >= limit:
            break
    return rows


def collect_declared_evidence_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "evidence_message_ids" and isinstance(child, list):
                found.update(str(item) for item in child if item)
            else:
                found.update(collect_declared_evidence_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(collect_declared_evidence_ids(child))
    return found


def validate_document(
    document: dict[str, Any],
    message_lookup: dict[str, Message],
    registry: EvidenceRegistry,
    trade_analysis: dict[str, Any],
    followup_stats: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    catalog = document["evidence_catalog"]
    declared = collect_declared_evidence_ids({
        key: value for key, value in document.items()
        if key not in {"evidence_catalog", "validation"}
    })
    missing_catalog = sorted(declared - set(catalog))
    if missing_catalog:
        errors.append("declared_evidence_ids_missing_from_catalog")
    unknown_catalog = sorted(set(catalog) - set(message_lookup))
    if unknown_catalog:
        errors.append("catalog_message_ids_missing_from_raw_export")
    excerpt_mismatches = []
    for message_id, card in catalog.items():
        expected = excerpt(message_lookup[message_id].text)
        if card.get("excerpt") != expected:
            excerpt_mismatches.append(message_id)
    if excerpt_mismatches:
        errors.append("evidence_excerpt_normalization_mismatch")
    if registry.missing_ids:
        warnings.append(
            f"{len(registry.missing_ids)} trade-evidence IDs were absent from the "
            "merged raw lookup and were not cited"
        )
    followup_ids = set(followup_stats.get("all_followup_message_ids") or [])
    missing_followup_catalog = sorted(followup_ids - set(catalog))
    if missing_followup_catalog:
        errors.append("followup_messages_missing_from_evidence_universe")
    if not trade_analysis.get("validation", {}).get("passed"):
        errors.append("upstream_trade_analysis_validation_not_passed")
    requested_window = document.get("source", {}).get("requested_window", {})
    window_correct = bool(
        requested_window.get("metadata_source") == "metadata.merge"
        and requested_window.get("start_date_inclusive")
        == EXPECTED_3MONTH_START_DATE
        and requested_window.get("end_date_inclusive")
        == EXPECTED_3MONTH_END_DATE_INCLUSIVE
        and requested_window.get("inclusive_calendar_days")
        == EXPECTED_3MONTH_DAY_COUNT
    )
    if not window_correct:
        errors.append(
            "three_month_scope_must_use_metadata_merge_2026-04-20_through_2026-07-20"
        )
    evidence_types = Counter()

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            if item.get("evidence_type"):
                evidence_types[str(item["evidence_type"])] += 1
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(document.get("answers"))
    walk(document.get("contradictions_and_tensions"))
    required_types = {
        "discord_explicit_rule_or_answer",
        "observed_association",
        "analyst_synthesis",
    }
    if not required_types.issubset(evidence_types):
        warnings.append(
            "One or more evidence classes had zero qualifying records: "
            + ", ".join(sorted(required_types - set(evidence_types)))
        )
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": {
            "declared_evidence_references": len(declared),
            "evidence_catalog_records": len(catalog),
            "missing_catalog_references": missing_catalog,
            "unknown_catalog_message_ids": unknown_catalog,
            "excerpt_mismatches": excerpt_mismatches,
            "upstream_trade_analysis_passed": bool(
                trade_analysis.get("validation", {}).get("passed")
            ),
            "evidence_type_counts": dict(sorted(evidence_types.items())),
            "chart_images_interpreted": 0,
            "outside_sources_used": 0,
            "three_month_window": {
                **requested_window,
                "expected_start_date_inclusive": EXPECTED_3MONTH_START_DATE,
                "expected_end_date_inclusive": EXPECTED_3MONTH_END_DATE_INCLUSIVE,
                "expected_inclusive_calendar_days": EXPECTED_3MONTH_DAY_COUNT,
                "passed": window_correct,
            },
            "followup_expected_message_ids": len(followup_ids),
            "followup_message_ids_in_evidence_catalog": len(
                followup_ids.intersection(catalog)
            ),
            "followup_message_ids_missing_from_catalog": missing_followup_catalog,
        },
        "assessment": "share_with_caveats" if not errors else "do_not_publish",
    }


def build_document(
    raw: dict[str, Any],
    trade_analysis: dict[str, Any],
    messages: Sequence[Message],
    load_stats: dict[str, Any],
    followups: dict[str, Any],
    followup_stats: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    message_lookup = {message.message_id: message for message in messages}
    registry = EvidenceRegistry(message_lookup)
    rb_messages, related_messages, question_ids = select_rb_corpus(messages)
    mentor_names = {
        normalize(name).casefold() for name in args.mentor_authors.split(",")
        if normalize(name)
    }
    # Every targeted-browser message is part of the evidence universe, including
    # adjacent/unlinked messages that are not usable as answers.
    for message_id in followup_stats["all_followup_message_ids"]:
        registry.add(message_id, "targeted_browser_followup_evidence_universe")
    qa_catalog = build_qa_catalog(
        related_messages, question_ids, mentor_names, registry, args.max_qa_items
    )
    followup_assessments = build_followup_context_assessments(
        followups, message_lookup, mentor_names, registry
    )
    apply_followup_qa_statuses(qa_catalog, followup_assessments)
    followup_by_target: dict[str, dict[str, Any]] = {}
    for assessment in followup_assessments:
        followup_by_target[assessment["target_message_id"]] = assessment
        for message_id in assessment["evidence_message_ids"]:
            message = message_lookup.get(message_id)
            if message and is_question(message):
                followup_by_target[message_id] = assessment
    claims = build_explicit_claims(
        related_messages, question_ids, mentor_names, registry,
        args.max_claims_per_topic,
        followup_by_target,
    )
    identification_rows = observed_component_rows(
        rb_messages, IDENTIFICATION_COMPONENTS, registry,
        "rb_identification_component", args.example_limit,
    )
    invalidation_rows = observed_component_rows(
        rb_messages, INVALIDATION_COMPONENTS, registry,
        "rb_invalidation_component", args.example_limit,
    )
    confluence_rows = observed_component_rows(
        rb_messages, CONFLUENCE_PATTERNS, registry,
        "rb_confluence_comention", args.example_limit,
    )
    associations = build_confluence_associations(
        trade_analysis, registry, args.min_association_sample
    )
    timing = build_timing_associations(
        rb_messages, trade_analysis, registry, args.example_limit
    )
    instrument = build_instrument_comparison(
        rb_messages, trade_analysis, registry, args.example_limit
    )
    contradictions = build_contradictions(
        related_messages, qa_catalog, registry, args.example_limit
    )
    chart_dependent = build_chart_dependent_records(
        related_messages, registry, args.max_chart_dependent_records
    )
    sufficiency = build_evidence_sufficiency(
        claims, associations, instrument, qa_catalog
    )

    identification_claims = claims.get("identification", [])
    invalidation_claims = claims.get("invalidation_or_non_actionability", [])
    timing_claims = claims.get("timing", [])
    high_claims = claims.get("higher_probability_claim", [])
    low_claims = claims.get("lower_probability_claim", [])
    instrument_claims = claims.get("NQ_vs_ES_or_instrument", [])

    identification_synthesis = {
        "finding_id": "RB-ID-SYN-001",
        "evidence_type": "analyst_synthesis",
        "statement": (
            f"Across {len(rb_messages)} RB-containing Discord messages, the most "
            f"frequent identification-related textual components are "
            f"{component_summary(identification_rows)}. Explicit rules and direct "
            "answers are listed separately; co-mentions do not create a universal "
            "candlestick definition."
        ),
        "evidence_message_ids": list(dict.fromkeys(
            evidence_refs_from_rows(identification_claims, 8)
            + evidence_refs_from_rows(identification_rows, 8)
        ))[:12],
        "confidence": sufficiency["topic_assessments"]["identification"]["assessment"],
        "chart_guard": "No wick/close geometry is inferred from attachments.",
    }
    invalidation_synthesis = {
        "finding_id": "RB-INV-SYN-001",
        "evidence_type": "analyst_synthesis",
        "statement": (
            "The export distinguishes technical invalidation from non-actionability. "
            f"Frequently captured textual reasons are {component_summary(invalidation_rows)}. "
            "A message saying an RB was not worth taking is not automatically recoded "
            "as a formally invalid RB."
        ),
        "evidence_message_ids": list(dict.fromkeys(
            evidence_refs_from_rows(invalidation_claims, 8)
            + evidence_refs_from_rows(invalidation_rows, 8)
        ))[:12],
        "confidence": sufficiency["topic_assessments"]["invalidation"]["assessment"],
    }
    raw_time_rows = timing["rb_message_time_co_mentions"]
    dominant_time = raw_time_rows[0] if raw_time_rows else None
    timing_synthesis = {
        "finding_id": "RB-TIME-SYN-001",
        "evidence_type": "analyst_synthesis",
        "statement": (
            f"The dominant explicit RB time/session co-mention is "
            f"{dominant_time['component']} in {dominant_time['message_count']} messages. "
            "This is a text-coverage result, not a claim about when blocks objectively "
            "form or perform best."
            if dominant_time else
            "No explicit RB setup-time family was captured; posting times are not substituted."
        ),
        "evidence_message_ids": evidence_refs_from_rows(raw_time_rows, 12),
        "confidence": sufficiency["topic_assessments"]["timing"]["assessment"],
    }
    probability_synthesis = {
        "finding_id": "RB-PROB-SYN-001",
        "evidence_type": "analyst_synthesis",
        "statement": (
            f"There are {associations['eligible_rb_instances']} eligible single-instance "
            "RB win/loss episodes with a baseline descriptive win share of "
            f"{associations['baseline_descriptive_win_share']}. Confluences above or "
            "below that share are cataloged as observed associations only; the script "
            "does not turn them into validated probabilities or a trading model."
        ),
        "evidence_message_ids": list(dict.fromkeys(
            evidence_refs_from_rows(
                associations["observed_higher_win_share_associations"], 6
            ) + evidence_refs_from_rows(
                associations["observed_lower_win_share_associations"], 6
            )
        )),
        "confidence": sufficiency["topic_assessments"]["win_loss_associations"][
            "assessment"
        ],
    }

    def followup_contexts(*context_ids: str) -> list[dict[str, Any]]:
        wanted = set(context_ids)
        return [
            row for row in followup_assessments
            if row["context_id"] in wanted
        ]

    metadata = raw.get("metadata", {})
    requested_window = resolve_requested_window(metadata)
    dates = [message.timestamp for message in messages]
    document: dict[str, Any] = {
        "artifact_schema": "discord_rejection_block_analysis.v2_auto_conservative",
        "source": {
            "raw_file": str(Path(args.raw).resolve()),
            "trade_analysis_file": str(Path(args.trades).resolve()),
            "targeted_browser_followup_file": str(
                Path(args.followups).resolve()
            ),
            "guild_id": metadata.get("guild_id"),
            "primary_channel_id": metadata.get("primary_channel_id"),
            "requested_window": requested_window,
            "actual_message_timestamp_range_utc": {
                "first": iso_z(min(dates)) if dates else None,
                "last": iso_z(max(dates)) if dates else None,
            },
            "source_scope": "Discord export and Discord-derived trade analysis only",
            "outside_sources_used": [],
            "targeted_followup_scope": followup_stats.get("scope"),
            "targeted_followup_completeness_boundary": (
                (followup_stats.get("methodology") or {}).get(
                    "completeness_boundary"
                )
            ),
            "generated_at_utc": iso_z(datetime.now(timezone.utc)),
        },
        "methodology": {
            "evidence_classes": {
                "discord_explicit_rule_or_answer": (
                    "Prescriptive Discord wording or a direct reply to an RB question; "
                    "kept in the speaker's own text and not assumed universal."
                ),
                "observed_textual_association": (
                    "Deterministic co-mention within RB-containing messages."
                ),
                "observed_association": (
                    "Descriptive outcome association in strict eligible trade episodes."
                ),
                "analyst_synthesis": (
                    "A deterministic summary of the preceding Discord evidence with "
                    "scope and sufficiency guards."
                ),
            },
            "message_deduplication": (
                "One row per message_id; contextual Q&A rows with explicit reply links "
                "are preferred over malformed search-result duplicates."
            ),
            "qa_linkage": "Only explicit reply_to_message_id chains are treated as answers.",
            "outcome_eligibility": associations["eligibility_basis"],
            "chart_policy": (
                "Attachments and deictic chart references are flagged. No chart-only "
                "geometry, candle state, level, or result is inferred."
            ),
            "timestamp_policy": timing["posting_timestamp_policy"],
            "mentor_author_labels": sorted(mentor_names),
            "targeted_followup_authority_policy": (
                (followup_stats.get("methodology") or {}).get(
                    "authority_caution"
                )
            ),
            "parameters": {
                "minimum_association_sample": args.min_association_sample,
                "max_claims_per_topic": args.max_claims_per_topic,
                "max_qa_items": args.max_qa_items,
                "example_limit": args.example_limit,
            },
        },
        "corpus_counts": {
            **load_stats,
            "rb_term_unique_messages": len(rb_messages),
            "rb_related_messages_including_reply_chains": len(related_messages),
            "rb_question_messages": len(question_ids),
            "rb_primary_channel_messages": sum(
                "primary_messages" in message.source_arrays for message in rb_messages
            ),
            "eligible_rb_trade_instances": associations["eligible_rb_instances"],
            "targeted_browser_followup": followup_stats,
            "explicit_claim_counts_by_topic": {
                topic: len(rows) for topic, rows in claims.items()
            },
        },
        "evidence_catalog": {},
        "answers": {
            "identification": {
                "explicit_rules_and_answers": identification_claims,
                "observed_textual_associations": identification_rows,
                "targeted_followup_contexts": followup_contexts(
                    "higher_probability_confluences",
                    "close_vs_wick_validity",
                ),
                "analyst_synthesis": [identification_synthesis],
            },
            "invalidation_and_non_actionability": {
                "explicit_rules_and_answers": invalidation_claims,
                "observed_textual_associations": invalidation_rows,
                "targeted_followup_contexts": followup_contexts(
                    "close_vs_wick_validity", "cross_market_mitigation"
                ),
                "analyst_synthesis": [invalidation_synthesis],
            },
            "timing": {
                "explicit_rules_and_answers": timing_claims,
                **timing,
                "targeted_followup_contexts": followup_contexts(
                    "timeframe_preferences", "timeframe_and_trading_window"
                ),
                "analyst_synthesis": [timing_synthesis],
            },
            "probability_profile": {
                "discord_explicit_higher_probability_claims": high_claims,
                "discord_explicit_lower_probability_claims": low_claims,
                "rb_confluence_message_coverage": confluence_rows,
                "eligible_trade_associations": associations,
                "targeted_followup_contexts": followup_contexts(
                    "higher_probability_confluences",
                    "nested_rejection_blocks",
                    "liquidity_sweep_probability",
                ),
                "analyst_synthesis": [probability_synthesis],
            },
            "instrument_comparison": {
                "explicit_rules_and_answers": instrument_claims,
                "targeted_followup_contexts": followup_contexts(
                    "es_applicability", "cross_market_mitigation"
                ),
                **instrument,
            },
        },
        "targeted_browser_followup_contexts": followup_assessments,
        "related_qa": qa_catalog,
        "contradictions_and_tensions": contradictions,
        "evidence_sufficiency": sufficiency,
        "chart_dependent_records": chart_dependent,
        "limitations": [
            "Discord statements and journal outcomes are self-reported.",
            "Message co-mentions are not independent trade observations.",
            "Eligible trade associations are descriptive and author-clustered.",
            "Image-only/chart-only details are not inferred.",
            "Unanswered questions remain unresolved.",
            "NQ/ES mentions are separated from explicitly executed instruments.",
        ],
        "validation": {},
    }
    document["evidence_catalog"] = registry.catalog()
    document["validation"] = validate_document(
        document, message_lookup, registry, trade_analysis, followup_stats
    )
    return document


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic Discord-only rejection-block evidence analysis "
            "from the merged raw export and conservative trade episodes."
        )
    )
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--trades", type=Path, default=DEFAULT_TRADES)
    parser.add_argument(
        "--followups", type=Path, default=DEFAULT_FOLLOWUPS,
        help="Targeted browser context artifact for unresolved RB questions.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--mentor-authors", default="Domme,Powell,boy",
        help="Comma-separated author names used only for source-tier labels.",
    )
    parser.add_argument("--min-association-sample", type=positive_int, default=5)
    parser.add_argument("--max-claims-per-topic", type=positive_int, default=250)
    parser.add_argument("--max-qa-items", type=positive_int, default=500)
    parser.add_argument("--max-chart-dependent-records", type=positive_int, default=300)
    parser.add_argument("--example-limit", type=positive_int, default=10)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args(argv)


def resolve_path(path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.raw = resolve_path(args.raw)
    args.trades = resolve_path(args.trades)
    args.followups = resolve_path(args.followups)
    args.output = resolve_path(args.output)
    if args.output == PROTECTED_14_DAY_OUTPUT:
        print(
            "ERROR: rb_analysis.json is permanently protected; choose a new output.",
            file=sys.stderr,
        )
        return 2
    for label, path in (
        ("raw export", args.raw),
        ("trade analysis", args.trades),
        ("targeted browser follow-ups", args.followups),
    ):
        if not path.exists():
            print(f"ERROR: {label} not found: {path}", file=sys.stderr)
            return 2
    if args.output.exists() and not args.force and not args.dry_run:
        print(
            f"ERROR: output already exists: {args.output} (use --force to replace it)",
            file=sys.stderr,
        )
        return 2
    try:
        with args.raw.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        with args.trades.open("r", encoding="utf-8") as handle:
            trades = json.load(handle)
        with args.followups.open("r", encoding="utf-8") as handle:
            followups = json.load(handle)
    except Exception as exc:
        print(f"ERROR: input JSON could not be loaded: {exc}", file=sys.stderr)
        return 2
    combined_raw, followup_stats = merge_followup_messages(raw, followups)
    messages, load_stats = load_messages(combined_raw)
    document = build_document(
        raw, trades, messages, load_stats, followups, followup_stats, args
    )
    validation = document["validation"]
    unanswered = [
        row for row in document["related_qa"]
        if row["status"] in {
            "unanswered_in_export", "unresolved_after_targeted_followup"
        }
    ]
    summary = {
        "raw": str(args.raw),
        "trades": str(args.trades),
        "followups": str(args.followups),
        "output": None if args.dry_run else str(args.output),
        "dry_run": args.dry_run,
        "validation_passed": validation["passed"],
        "validation_errors": validation["errors"],
        "rb_term_unique_messages": document["corpus_counts"][
            "rb_term_unique_messages"
        ],
        "eligible_rb_trade_instances": document["corpus_counts"][
            "eligible_rb_trade_instances"
        ],
        "related_qa": len(document["related_qa"]),
        "unanswered_qa": len(unanswered),
        "unanswered_high_priority_examples": [
            {
                "message_id": row["question_message_id"],
                "permalink": row["question_permalink"],
                "question_excerpt": row["question_excerpt"],
            }
            for row in unanswered[:20]
        ],
        "evidence_catalog_records": len(document["evidence_catalog"]),
        "followup_contexts": followup_stats["context_count"],
        "followup_messages": followup_stats["unique_message_ids"],
        "followup_new_to_merged_raw": followup_stats["new_to_merged_raw"],
        "followup_messages_in_evidence_universe": validation["checks"][
            "followup_message_ids_in_evidence_catalog"
        ],
    }
    # ASCII-safe console summary works on Windows cp1252 terminals; the artifact
    # itself is still written as UTF-8 with ensure_ascii=False.
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    if not validation["passed"]:
        return 1
    if args.dry_run:
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            document, handle, ensure_ascii=False,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
        )
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
