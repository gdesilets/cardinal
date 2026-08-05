#!/usr/bin/env python3
"""Conservatively extract trade episodes from a Discord journal export.

This script is intentionally deterministic and evidence-first. It does not use
screenshots, external market data, or outside trading knowledge. Ambiguous
outcomes stay unknown/mixed, and mentions of hypothetical profits never become
wins.

Default input/output:
    raw_discord_export_3month.json -> trade_analysis_3month.json

The script permanently refuses to write to trade_analysis.json so the curated
14-day artifact cannot be overwritten accidentally.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "raw_discord_export_3month.json"
DEFAULT_OUTPUT = SCRIPT_DIR / "trade_analysis_3month.json"
PROTECTED_14_DAY_OUTPUT = (SCRIPT_DIR / "trade_analysis.json").resolve()
ALLOWED_OUTCOMES = {
    "win", "loss", "breakeven", "mixed", "cancelled", "open", "unknown"
}
EXPECTED_3MONTH_START_DATE = "2026-04-20"
EXPECTED_3MONTH_END_DATE_INCLUSIVE = "2026-07-20"
EXPECTED_3MONTH_DAY_COUNT = 92

SPACE_RE = re.compile(r"\s+")
HORIZONTAL_SPACE_RE = re.compile(r"[^\S\r\n]+")
TRADE_NUMBER_RE = re.compile(
    r"(?im)^\s*trade\s*(?P<numbers>\d+(?:\s*\+\s*\d+)*)\s*[:\-]"
)
TRADE_TEMPLATE_RE = re.compile(
    r"(?i)\b(?:pair|position|setup|target|risk)\s*:"
)
EXECUTION_RE = re.compile(
    r"(?i)\b(?:i\s+)?(?:entered|entering|market\s+entered|"
    r"got\s+filled|filled\s+me|took\s+(?:a|the|this)?\s*trade|"
    r"took\s+(?:the\s+)?(?:long|short)s?|shorted|longed|"
    r"opened\s+(?:a\s+)?position|in\s+the\s+trade)\b"
)
ACTIVE_POSITION_RE = re.compile(
    r"(?i)\b(?:still\s+in|holding|leaving)\s+(?:the\s+)?"
    r"(?:trade|position|runner)|\btrade\s+is\s+(?:still\s+)?open\b"
)
TRADE_CONTEXT_RE = re.compile(
    r"(?i)\b(?:trade|entry|entered|position|setup|stop|sl\b|tp\b|"
    r"target|risk|rr\b|runner|partials?)\b"
)
DAY_JOURNAL_RE = re.compile(
    r"(?i)(?:^|\b)(?:day\s*\d+|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b"
)
RESULT_WORD_RE = re.compile(
    r"(?i)\b(?:win|wins|won|loss|losses|lost|lose|breakeven|"
    r"break\s*even|stopped\s+out|stop(?:ped)?\s+(?:me\s+)?out|"
    r"full\s+tp|tp\s+(?:was\s+)?hit|hit\s+(?:full\s+)?tp|"
    r"closed\s+in\s+profit|stopped\s+in\s+profit)\b"
)
NO_TRADE_RE = re.compile(
    r"(?i)\b(?:no\s+trade|did\s+not\s+trade|didn['’]?t\s+trade|"
    r"did\s+not\s+(?:enter|take\s+(?:it|the\s+trade))|"
    r"didn['’]?t\s+(?:enter|take\s+(?:it|the\s+trade))|"
    r"missed\s+(?:the\s+)?(?:entry|trade|limit)|"
    r"limit\s+(?:was\s+)?cancelled|cancelled\s+(?:the\s+)?limit|"
    r"chose\s+not\s+to\s+take)\b"
)
HYPOTHETICAL_RE = re.compile(
    r"(?i)\b(?:would(?:['’]?ve|\s+have)?|could(?:['’]?ve|\s+have)?|"
    r"should(?:['’]?ve|\s+have)?|if\s+i\s+(?:had|took|entered)|"
    r"paper\s+idea|hypothetical)\b"
)
_THIRD_PARTY_PATTERN = (
    r"(?i)\b(?:he|she|they|domme|powell|boy)\s+"
    r"(?:entered|took|went|got\s+stopped|closed|won|lost)\b|"
    r"(?i)\b(?:powell|domme)(?:['’]s)?\s+recap\b"
)
THIRD_PARTY_RE = re.compile(
    _THIRD_PARTY_PATTERN.replace("(?i)", ""), re.IGNORECASE
)
FIRST_PERSON_RE = re.compile(
    r"(?i)\b(?:i|my|me|we|our)\b"
)
RULE_DEFINITION_RE = re.compile(
    r"(?i)\b(?:rules?|criteria|strategy|model)\s*:|"
    r"\b(?:one|1)\s+win\s*=|\b(?:one|1)\s+loss\s*=|"
    r"\bmax(?:imum)?\s+trades?\b|\bminimum\s+rr\b"
)
HISTORICAL_REFLECTION_RE = re.compile(
    r"(?i)\b(?:over\s+my\s+journey|over\s+the\s+years?|"
    r"since\s+i\s+started|in\s+my\s+life|overall\s+i\s+have\s+lost|"
    r"lost\s+over\s+\$?[\d,.]+\s+(?:in|over)|"
    r"so\s+far\s+i['’]?m\s+\d+\s+days?\s+in|"
    r"\d+\s+losing\s+days?.*\d+\s+(?:winning|profit|be)\s+days?)\b"
)
PROVISIONAL_RE = re.compile(
    r"(?i)\b(?:might|may|probably|looks?\s+like|could\s+be)\b"
)

SIGNED_R_RE = re.compile(
    r"(?i)(?<![\w.])(?P<sign>[+\-])\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?:rr|r\b)"
)
SIGNED_DOLLAR_RE = re.compile(
    r"(?i)(?<!\w)(?P<sign>[+\-])\s*\$\s*"
    r"(?P<value>\d[\d,]*(?:\.\d+)?)"
)
AGGREGATE_COUNT_PATTERNS = {
    "win": re.compile(r"(?i)\b(?P<count>\d+)\s*(?:wins?|w(?:['’]?s)?)\b"),
    "loss": re.compile(
        r"(?i)\b(?P<count>\d+)\s*(?:loss(?:es)?|l(?:['’]?s)?)\b"
    ),
    "breakeven": re.compile(
        r"(?i)\b(?P<count>\d+)\s*(?:breakevens?|break\s*evens?|"
        r"be(?:['’]?s)?)\b"
    ),
}
AGGREGATE_LABEL_COUNT_PATTERNS = {
    "win": re.compile(r"(?i)\b(?:wins?|w(?:['’]?s)?)\s*[:=\-]\s*(?P<count>\d+)\b"),
    "loss": re.compile(
        r"(?i)\b(?:loss(?:es)?|l(?:['’]?s)?)\s*[:=\-]\s*(?P<count>\d+)\b"
    ),
    "breakeven": re.compile(
        r"(?i)\b(?:breakevens?|break\s*evens?|be(?:['’]?s)?)\s*"
        r"[:=\-]\s*(?P<count>\d+)\b"
    ),
}
TOTAL_TRADES_RE = re.compile(r"(?i)\b(?P<count>\d+)\s+trades?\b")


@dataclass
class Message:
    message_id: str
    author: str
    thread_title: str
    thread_key: str
    parent_channel: str
    timestamp_utc: datetime
    timestamp_raw: str
    local_date: str
    local_time: str
    text: str
    raw: dict[str, Any]


@dataclass
class Candidate:
    message: Message
    score: int
    reasons: list[str]
    reject_reasons: list[str] = field(default_factory=list)


@dataclass
class Signal:
    outcome: str
    basis: str
    matched_text: str
    start: int
    provisional: bool = False
    hypothetical: bool = False


def normalize_text(value: Any) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def normalize_message_text(value: Any) -> str:
    """Clean text while retaining line boundaries used by journal templates."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [HORIZONTAL_SPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def parse_timestamp(value: str) -> datetime:
    if not value:
        raise ValueError("missing timestamp_utc")
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


def enforce_three_month_window(
    validation: dict[str, Any], window: dict[str, Any]
) -> None:
    correct = bool(
        window.get("metadata_source") == "metadata.merge"
        and window.get("start_date_inclusive") == EXPECTED_3MONTH_START_DATE
        and window.get("end_date_inclusive") == EXPECTED_3MONTH_END_DATE_INCLUSIVE
        and window.get("inclusive_calendar_days") == EXPECTED_3MONTH_DAY_COUNT
    )
    validation["three_month_window"] = {
        **window,
        "expected_start_date_inclusive": EXPECTED_3MONTH_START_DATE,
        "expected_end_date_inclusive": EXPECTED_3MONTH_END_DATE_INCLUSIVE,
        "expected_inclusive_calendar_days": EXPECTED_3MONTH_DAY_COUNT,
        "passed": correct,
    }
    if not correct:
        validation["errors"].append(
            "three_month_scope_must_use_metadata_merge_2026-04-20_through_2026-07-20"
        )
    validation["passed"] = not validation["errors"]


def first_sunday_on_or_after(value: datetime) -> datetime:
    days_to_go = 6 - value.weekday()
    if days_to_go:
        value += timedelta(days=days_to_go)
    return value


def chicago_dst_range(year: int) -> tuple[datetime, datetime]:
    """US Central DST boundaries used when Windows has no IANA tzdata."""
    start = first_sunday_on_or_after(datetime(year, 3, 8, 2))
    end = first_sunday_on_or_after(datetime(year, 11, 1, 2))
    return start, end


class ChicagoFallbackTimezone(tzinfo):
    """America/Chicago for modern Discord dates (US rules since 2007)."""

    standard_offset = timedelta(hours=-6)
    daylight_delta = timedelta(hours=1)

    def tzname(self, value: datetime | None) -> str:
        return "CDT" if self.dst(value) else "CST"

    def utcoffset(self, value: datetime | None) -> timedelta:
        return self.standard_offset + self.dst(value)

    def dst(self, value: datetime | None) -> timedelta:
        if value is None or value.tzinfo is None:
            return timedelta(0)
        start, end = chicago_dst_range(value.year)
        naive = value.replace(tzinfo=None)
        if start <= naive < end:
            return self.daylight_delta
        return timedelta(0)

    def fromutc(self, value: datetime) -> datetime:
        if value.tzinfo is not self:
            raise ValueError("fromutc requires a datetime using this tzinfo")
        start, end = chicago_dst_range(value.year)
        start = start.replace(tzinfo=self)
        end = end.replace(tzinfo=self)
        standard_time = value + self.standard_offset
        daylight_time = standard_time + self.daylight_delta
        if end <= daylight_time < end + self.daylight_delta:
            return standard_time.replace(fold=1)
        if standard_time < start or daylight_time >= end:
            return standard_time
        return daylight_time


def resolve_timezone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except Exception:
        if name == "America/Chicago":
            return ChicagoFallbackTimezone()
        raise


def excerpt(text: str, limit: int = 600) -> str:
    text = normalize_text(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def timeframe(value: str | None) -> str:
    if not value:
        return "unspecified"
    raw = normalize_text(value).lower().replace(" ", "")
    aliases = {
        "daily": "daily", "weekly": "weekly", "monthly": "monthly",
        "htf": "htf", "ltf": "ltf",
    }
    if raw in aliases:
        return aliases[raw]
    match = re.match(r"(\d+)(s|sec|second|m|min|minute|h|hr|hour)s?$", raw)
    if not match:
        return raw
    number, unit = match.groups()
    if unit in {"s", "sec", "second"}:
        unit = "s"
    elif unit in {"m", "min", "minute"}:
        unit = "m"
    else:
        unit = "h"
    return f"{number}{unit}"


def dedupe_messages(
    rows: Sequence[dict[str, Any]], local_zone: tzinfo
) -> tuple[list[Message], dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    duplicate_ids: list[str] = []
    malformed: list[dict[str, str]] = []
    for row in rows:
        message_id = str(row.get("message_id") or "").strip()
        if not message_id:
            malformed.append({"message_id": "", "reason": "missing_message_id"})
            continue
        if message_id in by_id:
            duplicate_ids.append(message_id)
            old_text = normalize_text(
                by_id[message_id].get("content_text")
                or by_id[message_id].get("visible_text")
            )
            new_text = normalize_text(
                row.get("content_text") or row.get("visible_text")
            )
            if len(new_text) > len(old_text):
                by_id[message_id] = row
            continue
        by_id[message_id] = row

    messages: list[Message] = []
    for message_id, row in by_id.items():
        try:
            stamp = parse_timestamp(str(row.get("timestamp_utc") or ""))
        except Exception as exc:
            malformed.append({
                "message_id": message_id,
                "reason": f"bad_timestamp:{exc}",
            })
            continue
        local = stamp.astimezone(local_zone)
        content = normalize_message_text(
            row.get("content_text") or row.get("visible_text")
        )
        title = normalize_text(row.get("thread_title"))
        inferred = normalize_text(row.get("inferred_thread_channel_id"))
        author = normalize_text(row.get("author")) or "unknown"
        messages.append(Message(
            message_id=message_id,
            author=author,
            thread_title=title or "unknown",
            thread_key=inferred or title or "unknown",
            parent_channel=normalize_text(row.get("parent_channel")),
            timestamp_utc=stamp,
            timestamp_raw=str(row.get("timestamp_utc") or ""),
            local_date=local.date().isoformat(),
            local_time=local.time().isoformat(timespec="seconds"),
            text=content,
            raw=row,
        ))
    messages.sort(key=lambda item: (item.timestamp_utc, item.message_id))
    return messages, {
        "input_rows": len(rows),
        "unique_message_ids": len(by_id),
        "duplicate_rows": len(duplicate_ids),
        "duplicate_message_ids": sorted(set(duplicate_ids)),
        "malformed_rows": malformed,
    }


def signal_is_hypothetical(text: str, start: int) -> bool:
    window = text[max(0, start - 70): start]
    return bool(HYPOTHETICAL_RE.search(window))


def outcome_signals(text: str) -> list[Signal]:
    """Extract terminal-result signals without resolving conflicts."""
    signals: list[Signal] = []
    lowered = text.lower()

    def add(outcome: str, basis: str, match: re.Match[str]) -> None:
        signals.append(Signal(
            outcome=outcome,
            basis=basis,
            matched_text=match.group(0),
            start=match.start(),
            provisional=bool(
                PROVISIONAL_RE.search(text[max(0, match.start() - 35):match.start()])
            ),
            hypothetical=signal_is_hypothetical(text, match.start()),
        ))

    for match in SIGNED_R_RE.finditer(text):
        add("win" if match.group("sign") == "+" else "loss",
            "explicit_signed_r", match)
    for match in SIGNED_DOLLAR_RE.finditer(text):
        add("win" if match.group("sign") == "+" else "loss",
            "explicit_signed_dollar", match)

    specific_patterns: list[tuple[str, str, re.Pattern[str]]] = [
        ("win", "explicit_stopped_in_profit", re.compile(
            r"(?i)\b(?:stopped|closed)\s+(?:out\s+)?in\s+profit\b"
        )),
        ("breakeven", "explicit_stop_at_breakeven", re.compile(
            r"(?i)\b(?:stopped|tapped|closed)\s+(?:out\s+)?"
            r"(?:at\s+)?(?:be|breakeven|break\s*even)\b|"
            r"\bgot\s+stopped\s+(?:out\s+)?(?:at\s+)?be\b"
        )),
        ("breakeven", "explicit_breakeven", re.compile(
            r"(?i)\b(?:breakeven|break\s*even|went\s+to\s+be|"
            r"moved\s+to\s+be|got\s+be\b|trade\s+\d+\s*:\s*be\b)\b"
        )),
        ("win", "explicit_take_profit", re.compile(
            r"(?i)\b(?:hit|smacked|reached)\s+(?:my\s+|the\s+)?"
            r"(?:full\s+)?t/?p\b|\bfull\s+t/?p\s+(?:hit|smashed)\b|"
            r"\btook\s+profit\b"
        )),
        ("win", "explicit_profit", re.compile(
            r"(?i)\b(?:closed|ended|stopped)\s+(?:the\s+trade\s+)?"
            r"(?:with|for|at)?\s*(?:a\s+)?profit\b|"
            r"\bmade\s+\$\s*\d[\d,]*(?:\.\d+)?\b|"
            r"\bup\s+\$\s*\d[\d,]*(?:\.\d+)?\b"
        )),
        ("loss", "explicit_stop_out", re.compile(
            r"(?i)\b(?:got\s+|was\s+|i\s+)?stopp?ed\s+(?:me\s+)?out\b|"
            r"\bran\s+to\s+(?:my\s+)?s/?l\b|"
            r"\bhit\s+(?:my\s+)?s/?l\b"
        )),
        ("loss", "explicit_negative_dollars", re.compile(
            r"(?i)\b(?:down|lost|loss\s+(?:was|of))\s+"
            r"\$\s*\d[\d,]*(?:\.\d+)?\b"
        )),
        ("loss", "explicit_account_loss", re.compile(
            r"(?i)\b(?:blew|blown|blowed|liquidated)\s+"
            r"(?:the\s+|my\s+|an?\s+)?(?:account|eval|funded)\b"
        )),
    ]
    protected_ranges: list[tuple[int, int]] = []
    for outcome, basis, pattern in specific_patterns:
        for match in pattern.finditer(text):
            add(outcome, basis, match)
            if basis in {"explicit_stopped_in_profit",
                         "explicit_stop_at_breakeven"}:
                protected_ranges.append((match.start(), match.end()))

    word_patterns: list[tuple[str, str, re.Pattern[str]]] = [
        ("win", "explicit_win_word", re.compile(
            r"(?i)(?<!\w)(?:win|won)(?!\s*rate)(?!\w)|"
            r"\btook\s+(?:a\s+)?w\b|"
            r"\btrade\s+\d+(?:\s*\+\s*\d+)?\s*:\s*w(?:in)?\b"
        )),
        ("loss", "explicit_loss_word", re.compile(
            r"(?i)(?<!\w)(?:loss|losses|lost)(?!\s*limit)(?!\w)|"
            r"\btook\s+(?:an?\s+)?l\b|"
            r"\btrade\s+\d+(?:\s*\+\s*\d+)?\s*:\s*l(?:oss)?\b"
        )),
    ]
    for outcome, basis, pattern in word_patterns:
        for match in pattern.finditer(text):
            if any(start <= match.start() < end for start, end in protected_ranges):
                continue
            if (
                basis == "explicit_loss_word"
                and re.search(
                    r"(?i)(?:stop|stop[- ]?loss|s/?l)\s*$",
                    text[max(0, match.start() - 14):match.start()],
                )
            ):
                continue
            add(outcome, basis, match)

    # Remove generic stop-loss signals when the same phrase explicitly says
    # breakeven or profit.
    if any(s.outcome in {"win", "breakeven"} and
           s.basis in {"explicit_stopped_in_profit",
                       "explicit_stop_at_breakeven"} for s in signals):
        signals = [
            s for s in signals
            if not (s.outcome == "loss" and s.basis == "explicit_stop_out"
                    and any(abs(s.start - other.start) < 25
                            for other in signals
                            if other.outcome in {"win", "breakeven"}))
        ]
    return signals


def explicit_outcome_counts(text: str) -> tuple[dict[str, int], int | None]:
    counts: dict[str, int] = {}
    for outcome, pattern in AGGREGATE_COUNT_PATTERNS.items():
        values = []
        for match in pattern.finditer(text):
            value = int(match.group("count"))
            # Reject date fragments such as "14.7.26 WIN" and implausibly
            # large year-like values; both are common journal-header false hits.
            date_fragment = (
                match.start() >= 2
                and text[match.start() - 1] in "./-"
                and text[match.start() - 2].isdigit()
            )
            currency_amount = (
                match.start() > 0 and text[match.start() - 1] in "$£€"
            )
            crosses_line_boundary = "\n" in match.group(0)
            if (
                not date_fragment
                and not currency_amount
                and not crosses_line_boundary
                and value <= 100
            ):
                values.append(value)
        for match in AGGREGATE_LABEL_COUNT_PATTERNS[outcome].finditer(text):
            value = int(match.group("count"))
            if value <= 100:
                values.append(value)
        if values:
            counts[outcome] = max(values)
    totals = [int(match.group("count")) for match in TOTAL_TRADES_RE.finditer(text)]
    return counts, max(totals) if totals else None


def is_third_party_only(text: str) -> bool:
    return bool(THIRD_PARTY_RE.search(text) and not FIRST_PERSON_RE.search(text))


def candidate_score(message: Message) -> Candidate:
    text = message.text
    reasons: list[str] = []
    rejects: list[str] = []
    score = 0
    signals = outcome_signals(text)
    actual_terminal = [
        signal for signal in signals
        if not signal.hypothetical and not signal.provisional
    ]
    has_number = bool(TRADE_NUMBER_RE.search(text))
    has_template = bool(TRADE_TEMPLATE_RE.search(text))
    has_execution = bool(EXECUTION_RE.search(text))
    has_no_trade = bool(NO_TRADE_RE.search(text))
    has_active = bool(ACTIVE_POSITION_RE.search(text))
    has_result = bool(actual_terminal)

    if has_number:
        score += 5
        reasons.append("explicit_trade_number")
    if has_template:
        score += 4
        reasons.append("trade_template")
    if has_execution:
        score += 4
        reasons.append("execution_language")
    if has_result:
        score += 4
        reasons.append("terminal_result")
    if has_no_trade:
        score += 4
        reasons.append("explicit_no_trade")
    if has_active:
        score += 2
        reasons.append("active_position")
    if DAY_JOURNAL_RE.search(text) and (has_result or has_execution or has_no_trade):
        score += 2
        reasons.append("dated_journal_entry")
    if re.search(
        r"(?i)\b(?:rb|rejection\s+block|fvg|smt|ote|stdv|cisd|"
        r"breaker|order\s+block|amd|mmxm|po3)\b", text
    ) and (has_result or has_execution or has_no_trade):
        score += 1
        reasons.append("setup_detail")

    if is_third_party_only(text):
        score -= 7
        rejects.append("third_party_report")
    if RULE_DEFINITION_RE.search(text) and not has_execution:
        score -= 7
        rejects.append("rule_or_strategy_definition")
    if HISTORICAL_REFLECTION_RE.search(text) and not (
        has_number or has_template or DAY_JOURNAL_RE.search(text)
    ):
        score -= 6
        rejects.append("historical_reflection")
    if signals and not actual_terminal and not has_no_trade:
        score -= 3
        rejects.append("hypothetical_or_provisional_result_only")
    if not text:
        score = -100
        rejects.append("empty_text")
    if not (
        has_number or has_template or has_execution or has_result
        or has_no_trade or has_active
    ):
        rejects.append("no_trade_episode_anchor")
    return Candidate(message=message, score=score, reasons=reasons,
                     reject_reasons=rejects)


def resolve_outcome(
    texts: Sequence[tuple[str, str]]
) -> dict[str, Any]:
    """Resolve episode outcome from (message_id, text) pairs.

    Counts and terminal words are never inferred from price commentary.
    """
    all_signals: list[tuple[str, Signal]] = []
    count_options: list[tuple[str, dict[str, int], int | None]] = []
    any_execution = False
    any_active = False
    any_no_trade = False
    for message_id, text in texts:
        any_execution = any_execution or bool(EXECUTION_RE.search(text))
        any_active = any_active or bool(ACTIVE_POSITION_RE.search(text))
        any_no_trade = any_no_trade or bool(NO_TRADE_RE.search(text))
        for signal in outcome_signals(text):
            if not signal.hypothetical and not signal.provisional:
                all_signals.append((message_id, signal))
        counts, total = explicit_outcome_counts(text)
        if counts or total:
            count_options.append((message_id, counts, total))

    # Pick one summary count statement rather than summing repeated updates.
    best_count: tuple[str, dict[str, int], int | None] | None = None
    if count_options:
        best_count = max(
            count_options,
            key=lambda item: (
                sum(item[1].values()),
                len(item[1]),
                item[2] or 0,
            ),
        )
    outcome_counts = dict(best_count[1]) if best_count else {}
    reported_outcome_counts_raw = dict(outcome_counts)
    total_trades = best_count[2] if best_count else None
    positive_count_outcomes = {
        key for key, value in outcome_counts.items() if value > 0
    }
    bases: list[str] = []
    evidence: list[dict[str, Any]] = []
    for message_id, signal in all_signals:
        bases.append(signal.basis)
        evidence.append({
            "message_id": message_id,
            "basis": signal.basis,
            "matched_text": signal.matched_text,
        })

    terminal = {signal.outcome for _, signal in all_signals}
    count_conflict = bool(
        total_trades is not None
        and sum(outcome_counts.values()) > total_trades
    )
    count_conflict_detail: dict[str, Any] | None = None
    if count_conflict:
        count_conflict_detail = {
            "explicit_trade_count": total_trades,
            "reported_outcome_counts_raw": reported_outcome_counts_raw,
            "reason": (
                "Outcome labels/counts exceed the explicit trade total; this often "
                "indicates a cumulative stats block beside a current-day report. "
                "The raw counts are preserved but not allocated to this episode."
            ),
        }
        outcome = "mixed" if len(positive_count_outcomes | terminal) > 1 else "unknown"
        count = total_trades
        outcome_counts = {}
        basis = "conflicting_explicit_aggregate_counts_and_trade_total"
    elif positive_count_outcomes:
        if len(positive_count_outcomes) == 1:
            outcome = next(iter(positive_count_outcomes))
        else:
            outcome = "mixed"
        count = total_trades or sum(outcome_counts.values())
        basis = "explicit_aggregate_counts"
    else:
        if any_no_trade and not any_execution and not terminal:
            outcome = "cancelled"
            count = 1
            basis = "explicit_no_trade"
        elif len(terminal) == 1:
            outcome = next(iter(terminal))
            if total_trades and total_trades > 1:
                count = total_trades
                outcome_counts = {}
                basis = "explicit_multi_trade_total_with_partial_terminal_evidence"
            else:
                count = 1
                outcome_counts = {outcome: 1}
                basis = bases[-1] if bases else "explicit_terminal"
        elif len(terminal) > 1:
            outcome = "mixed"
            count = total_trades
            # Do not guess one instance per outcome.
            outcome_counts = {}
            basis = "conflicting_or_multiple_terminal_outcomes"
        elif any_active:
            outcome = "open"
            count = 1
            outcome_counts = {"open": 1}
            basis = "explicit_open_position"
        elif any_execution:
            outcome = "unknown"
            count = 1
            outcome_counts = {"unknown": 1}
            basis = "execution_without_terminal_result"
        elif any_no_trade:
            outcome = "cancelled"
            count = 1
            outcome_counts = {"cancelled": 1}
            basis = "explicit_no_trade"
        else:
            outcome = "unknown"
            count = total_trades
            outcome_counts = {}
            basis = "no_terminal_result"

    return {
        "outcome": outcome,
        "trade_count_reported": count,
        "outcome_counts": outcome_counts,
        "reported_outcome_counts_raw": reported_outcome_counts_raw,
        "count_conflict": count_conflict_detail,
        "outcome_basis": basis,
        "outcome_evidence": evidence,
        "explicit_total_trades": total_trades,
        "has_execution_language": any_execution,
        "has_no_trade_language": any_no_trade,
    }


TF_TOKEN = (
    r"(?:30\s*(?:s|sec(?:ond)?s?)|15\s*(?:s|sec(?:ond)?s?)|"
    r"[1-9]\d*\s*(?:m|min(?:ute)?s?|h|hr|hour)s?|"
    r"daily|weekly|monthly|htf|ltf)"
)
RB_RE = re.compile(
    rf"(?i)\b(?:(?P<tf>{TF_TOKEN})\s+)?"
    r"(?:(?:bullish|bearish)\s+)?(?:rb|rejection\s+block)s?\b"
)
FVG_RE = re.compile(
    rf"(?i)\b(?:(?P<tf>{TF_TOKEN})\s+)?"
    r"(?P<kind>ifvg|i\s*fvg|fvg|bisi|sibi|fair\s+value\s+gap)s?\b"
)
SMT_RE = re.compile(
    rf"(?i)\b(?:(?P<tf>{TF_TOKEN})\s+)?(?P<kind>ssmt|smt)s?\b"
)
ORDER_BLOCK_RE = re.compile(
    rf"(?i)\b(?:(?P<tf>{TF_TOKEN})\s+)?(?:ob|order\s+block)s?\b"
)
BREAKER_RE = re.compile(
    rf"(?i)\b(?:(?P<tf>{TF_TOKEN})\s+)?breaker(?:\s+block)?s?\b"
)
CISD_RE = re.compile(
    rf"(?i)\b(?:(?P<tf>{TF_TOKEN})\s+)?cisd\b"
)
OTE_RE = re.compile(r"(?i)\bote\b")
FIB_LEVEL_RE = re.compile(
    r"(?i)(?<!\d)(?P<level>0?\.(?:5|50|62|618|705|79)|"
    r"\b(?:50|62|61\.8|70\.5|79)\s*%)(?!\d)"
)
KEY_OPEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("10am", re.compile(r"(?i)\b10\s*(?::?00)?\s*a\.?m\.?\b|\b10\s*ko\b")),
    ("market_open", re.compile(r"(?i)\b(?:9\s*:?30|market\s+open|opening\s+bell)\b")),
    ("data_830", re.compile(r"(?i)\b8\s*:?30\b|\bdata\s+(?:high|low)s?\b")),
    ("midnight", re.compile(r"(?i)(?<!\d)(?:0?0\s*:?00|midnight\s+open)(?!\d)")),
    ("1800", re.compile(r"(?i)(?<!\d)18\s*:?00(?!\d)")),
    ("weekly", re.compile(r"(?i)\bweekly\s+open\b")),
]
SESSION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Asia", re.compile(r"(?i)\basia(?:n)?(?:\s+session)?\b")),
    ("London", re.compile(r"(?i)\b(?:london|ldn)(?:\s+session)?\b")),
    ("NY_AM", re.compile(r"(?i)\b(?:ny\s*am|new\s+york\s+am)\b")),
    ("NY_PM", re.compile(r"(?i)\b(?:ny\s*pm|pm\s+session)\b")),
]

RULE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("fomo", re.compile(r"(?i)\bfomo\b")),
    ("overtrading", re.compile(r"(?i)\bover\s*trad(?:e|ed|ing)\b")),
    ("revenge_trade", re.compile(r"(?i)\brevenge\s+trad(?:e|ed|ing)\b")),
    ("traded_against_bias", re.compile(
        r"(?i)\b(?:against|opposite)\s+(?:of\s+)?(?:my\s+)?bias\b|"
        r"\btraded?\s+against\s+(?:my\s+)?bias\b"
    )),
    ("plan_not_followed", re.compile(
        r"(?i)\b(?:did\s+not|didn['’]?t)\s+follow\s+(?:my\s+)?plan\b|"
        r"\bplan\s+not\s+followed\b"
    )),
    ("no_trade_plan", re.compile(
        r"(?i)\bno\s+(?:plan|analysis)\s+for\s+(?:the\s+)?trade\b"
    )),
    ("impatience", re.compile(r"(?i)\bimpatient|impatience|rushed\b")),
    ("entered_too_early", re.compile(
        r"(?i)\b(?:entered|jumped\s+in|entry)\s+too\s+(?:early|quick)\b"
    )),
    ("entered_too_late", re.compile(
        r"(?i)\b(?:entered|entry)\s+too\s+late\b|\blate\s+entry\b"
    )),
    ("missing_confirmation", re.compile(
        r"(?i)\b(?:did\s+not|didn['’]?t|forgot\s+to)\s+wait\s+for\s+"
        r"(?:a\s+|the\s+)?(?:confirmation|entry\s+trigger|\d+\s*(?:s|m)\s+rb)\b|"
        r"\bno\s+confirmation\b"
    )),
    ("stop_too_tight", re.compile(
        r"(?i)\b(?:stop|sl)\s+(?:was\s+)?too\s+tight\b|"
        r"\btight\s+(?:stop|sl)\b"
    )),
    ("stop_too_wide", re.compile(
        r"(?i)\b(?:stop|sl)\s+(?:was\s+)?too\s+wide\b|"
        r"\bwide\s+(?:stop|sl)\b"
    )),
    ("trailed_too_early", re.compile(
        r"(?i)\btrail(?:ed|ing)?\s+(?:my\s+)?(?:stop\s+)?too\s+early\b"
    )),
    ("stop_order_failed", re.compile(
        r"(?i)\b(?:sl|stop(?:\s+loss)?)\s+(?:did\s+not|didn['’]?t)\s+"
        r"(?:set|work|place)\b"
    )),
    ("target_order_failed", re.compile(
        r"(?i)\b(?:tp|take\s+profit)\s+(?:did\s+not|didn['’]?t)\s+"
        r"(?:work|fill|take\s+me\s+out)\b"
    )),
    ("overrisked", re.compile(
        r"(?i)\b(?:over\s*risk(?:ed|ing)?|risked\s+(?:too\s+much|more))\b"
    )),
    ("news_exposure", re.compile(
        r"(?i)\b(?:trump\s+(?:tweet|candle)|fomc|fed\s+(?:speaker|conference)|"
        r"cpi|ppi|nfp|high\s+impact\s+news)\b"
    )),
    ("outside_time_window", re.compile(
        r"(?i)\b(?:outside|after)\s+(?:my\s+)?(?:trading\s+)?time\s+window\b"
    )),
    ("low_conviction", re.compile(
        r"(?i)\b(?:not\s+100%|low\s+conviction|knew\s+(?:it|i)\s+"
        r"(?:was\s+going\s+to\s+)?lose)\b"
    )),
]


def nearby_role(text: str, start: int, end: int) -> str:
    window = text[max(0, start - 55): min(len(text), end + 55)]
    if re.search(r"(?i)\b(?:entry|entered|enter|execute|limit(?:ed)?)\b", window):
        return "entry"
    if re.search(r"(?i)\b(?:target|tp|draw|dol|exit)\b", window):
        return "target"
    return "context"


def add_field_evidence(
    evidence: dict[str, dict[str, list[dict[str, str]]]],
    field_name: str,
    value: str,
    message_id: str,
    matched_text: str,
) -> None:
    evidence.setdefault(field_name, {}).setdefault(value, []).append({
        "message_id": message_id,
        "matched_text": normalize_text(matched_text),
    })


def extract_features(
    texts: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    confluences: list[str] = []
    rules: list[str] = []
    field_evidence: dict[str, dict[str, list[dict[str, str]]]] = {}
    rb_quality_flags: list[str] = []
    setup_mentions: list[tuple[int, str]] = []
    session_mentions: list[tuple[int, str]] = []
    direction_values: list[str] = []
    executed_instruments: list[str] = []
    context_instruments: list[str] = []

    def confluence(tag: str, message_id: str, matched: str) -> None:
        if tag not in confluences:
            confluences.append(tag)
        add_field_evidence(
            field_evidence, "confluences", tag, message_id, matched
        )

    for text_index, (message_id, text) in enumerate(texts):
        for match in RB_RE.finditer(text):
            tf = timeframe(match.group("tf"))
            role = nearby_role(text, match.start(), match.end())
            tag = f"rejection_block:{tf}:{role}"
            confluence(tag, message_id, match.group(0))
            neighborhood = text[
                max(0, match.start() - 70): min(len(text), match.end() + 90)
            ]
            quality_patterns = {
                "explicit_valid": r"(?i)\bvalid\b",
                "explicit_invalid": r"(?i)\binvalid(?:ated|ation)?\b",
                "explicit_failed": r"(?i)\b(?:failed|didn['’]?t\s+hold|"
                                   r"disrespect(?:ed)?)\b",
                "explicit_mitigated": r"(?i)\bmitigat(?:ed|ion)\b",
                "explicit_low_probability": r"(?i)\blow\s+probability\b",
                "explicit_high_probability": r"(?i)\bhigh\s+probability\b",
            }
            for flag, pattern in quality_patterns.items():
                quality_match = re.search(pattern, neighborhood)
                if quality_match:
                    if flag not in rb_quality_flags:
                        rb_quality_flags.append(flag)
                    add_field_evidence(
                        field_evidence, "rb_quality_flags", flag,
                        message_id, quality_match.group(0)
                    )

        for match in FVG_RE.finditer(text):
            tf = timeframe(match.group("tf"))
            kind = normalize_text(match.group("kind")).lower().replace(" ", "")
            base = (
                "inverse_fair_value_gap"
                if kind in {"ifvg", "ifvg"}
                else "fair_value_gap"
            )
            role = nearby_role(text, match.start(), match.end())
            confluence(f"{base}:{tf}:{role}", message_id, match.group(0))
        for match in SMT_RE.finditer(text):
            tf = timeframe(match.group("tf"))
            kind = match.group("kind").lower()
            confluence(
                f"{'ssmt' if kind == 'ssmt' else 'smt_divergence'}:{tf}",
                message_id, match.group(0)
            )
        for match in ORDER_BLOCK_RE.finditer(text):
            tf = timeframe(match.group("tf"))
            confluence(
                f"order_block:{tf}:{nearby_role(text, match.start(), match.end())}",
                message_id, match.group(0)
            )
        for match in BREAKER_RE.finditer(text):
            tf = timeframe(match.group("tf"))
            confluence(
                f"breaker:{tf}:{nearby_role(text, match.start(), match.end())}",
                message_id, match.group(0)
            )
        for match in CISD_RE.finditer(text):
            confluence(
                f"cisd:{timeframe(match.group('tf'))}",
                message_id, match.group(0)
            )
        for match in OTE_RE.finditer(text):
            confluence("ote_fibonacci", message_id, match.group(0))
        for match in FIB_LEVEL_RE.finditer(text):
            raw_level = match.group("level").replace("%", "").strip()
            mapping = {
                ".5": "0.5", "0.5": "0.5", ".50": "0.5", "0.50": "0.5",
                ".62": "0.62", "0.62": "0.62", ".618": "0.618",
                "0.618": "0.618", ".705": "0.705", "0.705": "0.705",
                ".79": "0.79", "0.79": "0.79", "50": "0.5",
                "62": "0.62", "61.8": "0.618", "70.5": "0.705",
                "79": "0.79",
            }
            confluence(
                f"fibonacci_level:{mapping.get(raw_level, raw_level)}",
                message_id, match.group(0)
            )

        simple_patterns: list[tuple[str, re.Pattern[str]]] = [
            ("liquidity_sweep", re.compile(
                r"(?i)\b(?:swept|sweep(?:ing)?|ran)\s+(?:out\s+)?"
                r"(?:buy\s*side|sell\s*side|bsl|ssl|liquidity|"
                r"(?:equal|relative\s+equal)\s+(?:highs?|lows?)|"
                r"pdh|pdl|session\s+(?:highs?|lows?))\b"
            )),
            ("engineered_liquidity", re.compile(
                r"(?i)\b(?:engineered\s+liquidity|\bel\b)"
            )),
            ("relative_equal_highs_lows", re.compile(
                r"(?i)\b(?:relative\s+equal|equal)\s+(?:highs?|lows?)\b|"
                r"\b(?:eqh|eql|reh|rel)\b"
            )),
            ("standard_deviation", re.compile(r"(?i)\bstdv\b|\bstandard\s+deviation\b")),
            ("amd_cycle", re.compile(r"(?i)\bamd\b|\baccumulation\s+manipulation\s+distribution\b")),
            ("market_maker_model", re.compile(r"(?i)\b(?:mmxm|mmbm|mmsm|market\s+maker\s+(?:buy|sell)?\s*model)\b")),
            ("power_of_three", re.compile(r"(?i)\bpo3\b|\bpower\s+of\s+three\b")),
            ("judas_swing", re.compile(r"(?i)\bjudas\s+swing\b")),
            ("balanced_price_range", re.compile(r"(?i)\bbpr\b|\bbalanced\s+price\s+range\b")),
            ("premium_discount", re.compile(r"(?i)\b(?:premium|discount)\b")),
            ("break_of_structure", re.compile(r"(?i)\b(?:bos|break\s+of\s+structure)\b")),
            ("displacement", re.compile(r"(?i)\bdisplacement\b")),
            ("nwog", re.compile(r"(?i)\bnwog\b|\bnew\s+week\s+opening\s+gap\b")),
            ("ndog", re.compile(r"(?i)\bndog\b|\bnew\s+day\s+opening\s+gap\b")),
            ("draw_on_liquidity", re.compile(r"(?i)\b(?:draw\s+on\s+liquidity|\bdol\b)")),
        ]
        for tag, pattern in simple_patterns:
            for match in pattern.finditer(text):
                confluence(tag, message_id, match.group(0))

        for label, pattern in KEY_OPEN_PATTERNS:
            for match in pattern.finditer(text):
                tag = f"key_open:{label}"
                confluence(tag, message_id, match.group(0))
                setup_mentions.append((text_index * 100000 + match.start(), label))
                add_field_evidence(
                    field_evidence, "setup_times", label,
                    message_id, match.group(0)
                )
        for label, pattern in SESSION_PATTERNS:
            for match in pattern.finditer(text):
                session_mentions.append(
                    (text_index * 100000 + match.start(), label)
                )
                add_field_evidence(
                    field_evidence, "sessions", label,
                    message_id, match.group(0)
                )

        pair_pattern = re.compile(
            r"(?i)\bpair\s*:\s*(?P<symbol>MNQ|NQ|MES|ES|"
            r"NAS100|XAUUSD|YM|MYM|RTY|M2K)\b"
        )
        execution_instrument_patterns = [
            pair_pattern,
            re.compile(
                r"(?i)\b(?:entered|traded|took|opened)\s+"
                r"(?:a\s+|the\s+|this\s+)?(?:trade\s+)?"
                r"(?:on\s+)?(?P<symbol>MNQ|NQ|MES|ES|NAS100|"
                r"XAUUSD|YM|MYM|RTY|M2K)\b"
            ),
            re.compile(
                r"(?i)\b(?P<symbol>MNQ|NQ|MES|ES|NAS100|XAUUSD|"
                r"YM|MYM|RTY|M2K)\s+(?:entry|trade|position)\b"
            ),
            re.compile(r"(?i)\bchose\s+(?P<symbol>MNQ|NQ|MES|ES|YM)\b"),
        ]
        for pattern in execution_instrument_patterns:
            for match in pattern.finditer(text):
                symbol = match.group("symbol").upper()
                if symbol not in executed_instruments:
                    executed_instruments.append(symbol)
                add_field_evidence(
                    field_evidence, "instrument", symbol,
                    message_id, match.group(0)
                )
        context_pattern = re.compile(
            r"(?i)\b(MNQ|NQ|MES|ES|NAS100|NASDAQ|XAUUSD|GOLD|"
            r"YM|MYM|RTY|M2K)\b"
        )
        for match in context_pattern.finditer(text):
            symbol = match.group(1).upper()
            symbol = {"NASDAQ": "NQ", "GOLD": "XAUUSD"}.get(symbol, symbol)
            if symbol not in context_instruments:
                context_instruments.append(symbol)

        direction_patterns = [
            re.compile(r"(?i)\bposition\s*:\s*(?P<direction>long|short)s?\b"),
            re.compile(
                r"(?i)\b(?:entered|took|opened|market\s+entered)\b"
                r"[^.!?]{0,35}\b(?P<direction>long|short)s?\b"
            ),
            re.compile(r"(?i)\b(?P<verb>shorted|longed)\b"),
        ]
        for pattern in direction_patterns:
            for match in pattern.finditer(text):
                if "verb" in match.groupdict() and match.group("verb"):
                    value = "short" if match.group("verb").lower().startswith(
                        "short"
                    ) else "long"
                else:
                    value = match.group("direction").lower()
                direction_values.append(value)
                add_field_evidence(
                    field_evidence, "direction", value,
                    message_id, match.group(0)
                )

        for rule, pattern in RULE_PATTERNS:
            for match in pattern.finditer(text):
                if rule not in rules:
                    rules.append(rule)
                add_field_evidence(
                    field_evidence, "rules", rule,
                    message_id, match.group(0)
                )

    unique_directions = set(direction_values)
    if len(unique_directions) == 1:
        direction = next(iter(unique_directions))
    elif len(unique_directions) > 1:
        direction = "mixed"
    else:
        direction = "unknown"

    setup_mentions.sort()
    setup_times = list(dict.fromkeys(value for _, value in setup_mentions))
    session_mentions.sort()
    sessions = list(dict.fromkeys(value for _, value in session_mentions))
    if len(sessions) == 1:
        session = sessions[0]
    elif len(sessions) > 1:
        session = "mixed"
    elif any(value in {"10am", "market_open", "data_830"}
             for value in setup_times):
        session = "NY_AM"
    else:
        session = "unknown"

    rb_instances: list[dict[str, str]] = []
    for tag in confluences:
        if not tag.startswith("rejection_block:"):
            continue
        parts = tag.split(":")
        rb_instances.append({
            "timeframe": parts[1] if len(parts) > 1 else "unspecified",
            "role": parts[2] if len(parts) > 2 else "context",
        })
    return {
        "confluences": confluences,
        "rules": rules,
        "field_evidence": field_evidence,
        "rejection_block_use": {
            "used": bool(rb_instances),
            "instances": rb_instances,
            "explicit_quality_flags": rb_quality_flags,
        },
        "instrument": executed_instruments or ["unknown"],
        "market_context_instruments": context_instruments,
        "direction": direction,
        "session": session,
        "setup_time": setup_times[0] if setup_times else None,
        "setup_times_mentioned": setup_times,
    }


PAPER_EXECUTION_RE = re.compile(
    r"(?i)\b(?:paper\s+trad(?:e|ing)|paper\s+account|demo\s+account|"
    r"simulated\s+trad(?:e|ing)|backtest(?:ed|ing)?)\b"
)
FEATURE_CONTEXT_RE = re.compile(
    r"(?i)\b(?:rb|rejection\s+block|fvg|fair\s+value\s+gap|"
    r"smt|ssmt|order\s+block|breaker|cisd|ote|fib(?:onacci)?|"
    r"liquidity|equal\s+(?:highs?|lows?)|stdv|amd|mmxm|po3|"
    r"judas|bpr|premium|discount|displacement|nwog|ndog|dol|"
    r"nq|es|mnq|mes|nasdaq|s&p|spx|entry|target|bias|setup)\b"
)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def message_group_key(message: Message) -> tuple[str, str, str]:
    return (message.author, message.thread_key, message.local_date)


def numbered_trade_count(label: str) -> int:
    values = re.findall(r"\d+", label)
    return max(1, len(values))


def split_structured_trade_sections(text: str) -> list[dict[str, Any]]:
    """Split multi-trade journal templates at explicit Trade N headers."""
    matches = list(TRADE_NUMBER_RE.finditer(text))
    if len(matches) < 2:
        return []
    sections: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        label = normalize_text(match.group(0)).rstrip(":-")
        numbers = match.group("numbers")
        sections.append({
            "label": label,
            "numbers": numbers,
            "explicit_trade_count": numbered_trade_count(numbers),
            "text": text[match.start():end].strip(),
        })
    return sections


def is_hard_rejected(candidate: Candidate) -> bool:
    hard_reasons = {
        "third_party_report",
        "historical_reflection",
        "hypothetical_or_provisional_result_only",
        "no_trade_episode_anchor",
        "empty_text",
    }
    return bool(hard_reasons.intersection(candidate.reject_reasons))


def context_relevant(message: Message, anchor_ids: set[str]) -> bool:
    if message.message_id in anchor_ids:
        return True
    if not message.text or is_third_party_only(message.text):
        return False
    return bool(
        TRADE_CONTEXT_RE.search(message.text)
        or RESULT_WORD_RE.search(message.text)
        or NO_TRADE_RE.search(message.text)
        or FEATURE_CONTEXT_RE.search(message.text)
        or any(pattern.search(message.text) for _, pattern in KEY_OPEN_PATTERNS)
        or any(pattern.search(message.text) for _, pattern in SESSION_PATTERNS)
    )


def bounded_context(
    anchor_messages: Sequence[Message],
    group_messages: Sequence[Message],
    before_minutes: int,
    after_minutes: int,
    max_messages: int,
    lower_bound: datetime | None = None,
    upper_bound: datetime | None = None,
) -> list[Message]:
    """Select compact, same-author/thread/day evidence around episode anchors."""
    if not anchor_messages:
        return []
    anchor_ids = {message.message_id for message in anchor_messages}
    start = min(message.timestamp_utc for message in anchor_messages) - timedelta(
        minutes=before_minutes
    )
    end = max(message.timestamp_utc for message in anchor_messages) + timedelta(
        minutes=after_minutes
    )
    if lower_bound is not None:
        start = max(start, lower_bound)
    if upper_bound is not None:
        end = min(end, upper_bound)
    relevant = [
        message for message in group_messages
        if start <= message.timestamp_utc <= end
        and context_relevant(message, anchor_ids)
    ]
    relevant.sort(key=lambda item: (item.timestamp_utc, item.message_id))
    if len(relevant) <= max_messages:
        return relevant

    # Never trim anchors. Fill remaining slots by temporal proximity to an anchor.
    anchors = [message for message in relevant if message.message_id in anchor_ids]
    others = [message for message in relevant if message.message_id not in anchor_ids]
    others.sort(key=lambda item: min(
        abs((item.timestamp_utc - anchor.timestamp_utc).total_seconds())
        for anchor in anchor_messages
    ))
    selected = anchors + others[:max(0, max_messages - len(anchors))]
    selected.sort(key=lambda item: (item.timestamp_utc, item.message_id))
    return selected


def candidate_audit_row(candidate: Candidate, accepted: bool) -> dict[str, Any]:
    return {
        "message_id": candidate.message.message_id,
        "timestamp_utc": iso_z(candidate.message.timestamp_utc),
        "author": candidate.message.author,
        "thread_title": candidate.message.thread_title,
        "score": candidate.score,
        "accepted": accepted,
        "positive_reasons": candidate.reasons,
        "reject_reasons": candidate.reject_reasons,
        "excerpt": excerpt(candidate.message.text, 280),
    }


def determine_episode_kind(
    outcome: dict[str, Any], execution_mode: str, shared: bool
) -> str:
    count = outcome.get("trade_count_reported")
    if shared or (isinstance(count, int) and count > 1) or outcome["outcome"] == "mixed":
        return "aggregate_or_unresolved_multi_trade"
    if outcome["outcome"] == "cancelled":
        return "missed_or_cancelled_trade"
    if execution_mode == "paper":
        return "paper_trade"
    if outcome["has_execution_language"] or outcome["outcome"] in {
        "win", "loss", "breakeven", "open"
    }:
        return "executed_trade"
    return "trade_report_with_unknown_execution"


def make_episode(
    *,
    episode_id: str,
    anchor_messages: Sequence[Message],
    outcome_texts: Sequence[tuple[str, str]],
    feature_texts: Sequence[tuple[str, str]],
    evidence_messages: Sequence[Message],
    linkage_strength: str,
    linkage_rationale: str,
    candidate_lookup: dict[str, Candidate],
    explicit_trade_count: int | None = None,
    section_label: str | None = None,
    section_excerpt: str | None = None,
) -> dict[str, Any]:
    if not anchor_messages:
        raise ValueError("episode requires at least one anchor message")
    anchor = min(anchor_messages, key=lambda item: (
        item.timestamp_utc, item.message_id
    ))
    outcome = resolve_outcome(outcome_texts)
    if explicit_trade_count and explicit_trade_count > 1:
        # A label such as "Trade 3+4: Loss" explicitly describes two instances.
        if outcome["outcome"] in {"win", "loss", "breakeven", "cancelled"}:
            if outcome["outcome_basis"] != "explicit_aggregate_counts":
                outcome["trade_count_reported"] = explicit_trade_count
                outcome["outcome_counts"] = {
                    outcome["outcome"]: explicit_trade_count
                }
                outcome["outcome_basis"] += "+explicit_composite_trade_label"
        elif outcome["trade_count_reported"] is None:
            outcome["trade_count_reported"] = explicit_trade_count

    features = extract_features(feature_texts)
    combined_text = "\n".join(text for _, text in outcome_texts)
    execution_mode = (
        "paper" if PAPER_EXECUTION_RE.search(combined_text)
        else "actual_or_unspecified"
    )
    execution_anchor_count = sum(
        1 for _, text in outcome_texts if EXECUTION_RE.search(text)
    )
    count = outcome.get("trade_count_reported")
    shared = bool(
        (isinstance(count, int) and count > 1)
        or explicit_trade_count and explicit_trade_count > 1
        or execution_anchor_count > 1
        or outcome["outcome"] == "mixed"
    )
    episode_kind = determine_episode_kind(outcome, execution_mode, shared)
    eligible = int(
        execution_mode == "actual_or_unspecified"
        and episode_kind == "executed_trade"
        and outcome["outcome"] in {"win", "loss"}
        and outcome.get("trade_count_reported") == 1
        and bool(features["confluences"])
        and not shared
    )

    override = {
        anchor.message_id: section_excerpt
    } if section_excerpt else {}
    evidence: list[dict[str, Any]] = []
    seen_evidence: set[str] = set()
    for message in sorted(evidence_messages, key=lambda item: (
        item.timestamp_utc, item.message_id
    )):
        if message.message_id in seen_evidence:
            continue
        seen_evidence.add(message.message_id)
        evidence.append({
            "message_id": message.message_id,
            "timestamp_utc": iso_z(message.timestamp_utc),
            "author": message.author,
            "thread_title": message.thread_title,
            "excerpt": excerpt(override.get(message.message_id) or message.text),
        })
    for message in anchor_messages:
        if message.message_id not in seen_evidence:
            evidence.append({
                "message_id": message.message_id,
                "timestamp_utc": iso_z(message.timestamp_utc),
                "author": message.author,
                "thread_title": message.thread_title,
                "excerpt": excerpt(override.get(message.message_id) or message.text),
            })

    candidate_rows = []
    for message in anchor_messages:
        candidate = candidate_lookup.get(message.message_id)
        if candidate:
            candidate_rows.append({
                "message_id": message.message_id,
                "score": candidate.score,
                "reasons": candidate.reasons,
            })

    notes: list[str] = []
    if section_label:
        notes.append(f"Explicit journal section: {section_label}.")
    if outcome["outcome"] in {"unknown", "mixed"}:
        notes.append("Ambiguity retained; no terminal result was inferred.")
    if features["instrument"] == ["unknown"]:
        notes.append("Executed instrument was not explicit in linked text.")

    return {
        "episode_id": episode_id,
        "episode_kind": episode_kind,
        "execution_mode": execution_mode,
        "author": anchor.author,
        "thread_title": anchor.thread_title,
        "thread_key": anchor.thread_key,
        "trade_date_local": anchor.local_date,
        "primary_post_timestamp_utc": iso_z(anchor.timestamp_utc),
        "instrument": features["instrument"],
        "market_context_instruments": features["market_context_instruments"],
        "direction": features["direction"],
        "session": features["session"],
        "setup_time": features["setup_time"],
        "setup_times_mentioned": features["setup_times_mentioned"],
        "outcome": outcome["outcome"],
        "trade_count_reported": outcome["trade_count_reported"],
        "outcome_counts": outcome["outcome_counts"],
        "reported_outcome_counts_raw": outcome["reported_outcome_counts_raw"],
        "count_conflict": outcome["count_conflict"],
        "outcome_basis": outcome["outcome_basis"],
        "linkage_strength": linkage_strength,
        "linkage_rationale": linkage_rationale,
        "confluences": features["confluences"],
        "rejection_block_use": features["rejection_block_use"],
        "rules_violated_or_execution_issues": features["rules"],
        "eligible_trade_instances_for_win_loss_confluence_comparison": eligible,
        "shared_confluence_attribution_across_instances": shared,
        "notes": " ".join(notes) or None,
        "section_label": section_label,
        "source_candidate_message_ids": [
            message.message_id for message in anchor_messages
        ],
        "candidate_scores": candidate_rows,
        "outcome_evidence": outcome["outcome_evidence"],
        "field_evidence": features["field_evidence"],
        "evidence": evidence,
    }


def extract_episodes(
    messages: Sequence[Message], args: argparse.Namespace
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates = [candidate_score(message) for message in messages]
    candidate_lookup = {
        candidate.message.message_id: candidate for candidate in candidates
    }
    accepted = [
        candidate for candidate in candidates
        if candidate.score >= args.min_candidate_score
        and not is_hard_rejected(candidate)
    ]
    accepted_ids = {candidate.message.message_id for candidate in accepted}
    rejected = [
        candidate for candidate in candidates
        if candidate.message.message_id not in accepted_ids
    ]

    groups: dict[tuple[str, str, str], list[Message]] = defaultdict(list)
    for message in messages:
        groups[message_group_key(message)].append(message)
    for group in groups.values():
        group.sort(key=lambda item: (item.timestamp_utc, item.message_id))

    episodes: list[dict[str, Any]] = []
    consumed: set[str] = set()
    linkage_counts: Counter[str] = Counter()

    def next_id() -> str:
        return f"AUTO{len(episodes) + 1:06d}"

    # Pass 1: one explicit multi-trade template can safely yield one section per
    # Trade N header. Setup details never cross section boundaries here.
    for candidate in accepted:
        message = candidate.message
        sections = split_structured_trade_sections(message.text)
        if not sections:
            continue
        for section in sections:
            episode = make_episode(
                episode_id=next_id(),
                anchor_messages=[message],
                outcome_texts=[(message.message_id, section["text"])],
                feature_texts=[(message.message_id, section["text"])],
                evidence_messages=[message],
                linkage_strength="explicit_structured_section",
                linkage_rationale=(
                    "Multiple explicit Trade N headers in one journal post; "
                    "text was bounded at the next header."
                ),
                candidate_lookup=candidate_lookup,
                explicit_trade_count=section["explicit_trade_count"],
                section_label=section["label"],
                section_excerpt=section["text"],
            )
            episodes.append(episode)
            linkage_counts["explicit_structured_section"] += 1
        consumed.add(message.message_id)

    # Pass 2: explicit single Trade N anchors receive only nearby candidate
    # updates before the next numbered trade. Conflicts remain mixed.
    numbered_by_group: dict[tuple[str, str, str], list[Candidate]] = defaultdict(list)
    for candidate in accepted:
        if candidate.message.message_id in consumed:
            continue
        if TRADE_NUMBER_RE.search(candidate.message.text):
            numbered_by_group[message_group_key(candidate.message)].append(candidate)

    for key, numbered in numbered_by_group.items():
        numbered.sort(key=lambda item: (
            item.message.timestamp_utc, item.message.message_id
        ))
        group_messages = groups[key]
        group_candidates = [
            candidate for candidate in accepted
            if message_group_key(candidate.message) == key
        ]
        for index, anchor_candidate in enumerate(numbered):
            anchor = anchor_candidate.message
            if anchor.message_id in consumed:
                continue
            natural_end = anchor.timestamp_utc + timedelta(
                minutes=args.numbered_follow_minutes
            )
            next_number_time = (
                numbered[index + 1].message.timestamp_utc
                if index + 1 < len(numbered) else None
            )
            end = min(natural_end, next_number_time) if next_number_time else natural_end
            episode_candidates = [
                candidate for candidate in group_candidates
                if candidate.message.message_id not in consumed
                and anchor.timestamp_utc <= candidate.message.timestamp_utc < end
            ]
            if anchor_candidate not in episode_candidates:
                episode_candidates.insert(0, anchor_candidate)
            anchors = [candidate.message for candidate in episode_candidates]
            context = bounded_context(
                anchors, group_messages,
                args.context_before_minutes, args.context_after_minutes,
                args.max_evidence_messages,
                upper_bound=end,
            )
            outcome_texts = [
                (candidate.message.message_id, candidate.message.text)
                for candidate in episode_candidates
            ]
            feature_texts = [
                (message.message_id, message.text) for message in context
            ]
            number_match = TRADE_NUMBER_RE.search(anchor.text)
            explicit_count = numbered_trade_count(
                number_match.group("numbers")
            ) if number_match else 1
            episodes.append(make_episode(
                episode_id=next_id(),
                anchor_messages=anchors,
                outcome_texts=outcome_texts,
                feature_texts=feature_texts,
                evidence_messages=context,
                linkage_strength="explicit_numbered_sequence",
                linkage_rationale=(
                    "Explicit Trade N anchor plus same-author, same-thread, "
                    "same-day candidate updates before the next Trade N anchor."
                ),
                candidate_lookup=candidate_lookup,
                explicit_trade_count=explicit_count,
                section_label=normalize_text(number_match.group(0)).rstrip(":-")
                if number_match else None,
            ))
            linkage_counts["explicit_numbered_sequence"] += 1
            consumed.update(
                candidate.message.message_id for candidate in episode_candidates
            )

    # Pass 3: remaining anchors are clustered only within author/thread/local
    # date, and only across short gaps. A cluster is kept aggregate when it may
    # contain multiple executions or terminal outcomes.
    remaining_by_group: dict[tuple[str, str, str], list[Candidate]] = defaultdict(list)
    for candidate in accepted:
        if candidate.message.message_id not in consumed:
            remaining_by_group[message_group_key(candidate.message)].append(candidate)
    for key, remaining in remaining_by_group.items():
        remaining.sort(key=lambda item: (
            item.message.timestamp_utc, item.message.message_id
        ))
        clusters: list[list[Candidate]] = []
        current: list[Candidate] = []
        for candidate in remaining:
            if not current:
                current = [candidate]
                continue
            gap = candidate.message.timestamp_utc - current[-1].message.timestamp_utc
            if gap <= timedelta(minutes=args.cluster_gap_minutes):
                current.append(candidate)
            else:
                clusters.append(current)
                current = [candidate]
        if current:
            clusters.append(current)

        for cluster in clusters:
            anchors = [candidate.message for candidate in cluster]
            context = bounded_context(
                anchors, groups[key],
                args.context_before_minutes, args.context_after_minutes,
                args.max_evidence_messages,
            )
            episodes.append(make_episode(
                episode_id=next_id(),
                anchor_messages=anchors,
                outcome_texts=[
                    (candidate.message.message_id, candidate.message.text)
                    for candidate in cluster
                ],
                feature_texts=[
                    (message.message_id, message.text) for message in context
                ],
                evidence_messages=context,
                linkage_strength=(
                    "explicit_single_message" if len(cluster) == 1
                    else "conservative_same_thread_cluster"
                ),
                linkage_rationale=(
                    "One qualifying trade anchor."
                    if len(cluster) == 1 else
                    "Unnumbered qualifying anchors grouped by identical author, "
                    "thread, local date, and bounded time gap; unresolved multiplicity "
                    "is preserved."
                ),
                candidate_lookup=candidate_lookup,
            ))
            linkage = (
                "explicit_single_message" if len(cluster) == 1
                else "conservative_same_thread_cluster"
            )
            linkage_counts[linkage] += 1
            consumed.update(candidate.message.message_id for candidate in cluster)

    episodes.sort(key=lambda item: (
        item["primary_post_timestamp_utc"], item["episode_id"]
    ))
    for index, episode in enumerate(episodes, start=1):
        episode["episode_id"] = f"AUTO{index:06d}"

    audit = {
        "candidate_scoring": {
            "minimum_score": args.min_candidate_score,
            "accepted_candidates": len(accepted),
            "rejected_messages": len(rejected),
            "accepted_reason_frequency": dict(Counter(
                reason for candidate in accepted for reason in candidate.reasons
            )),
            "rejected_reason_frequency": dict(Counter(
                reason for candidate in rejected
                for reason in candidate.reject_reasons
            )),
            "accepted_samples": [
                candidate_audit_row(candidate, True)
                for candidate in accepted[:args.audit_sample_size]
            ],
            "rejected_samples": [
                candidate_audit_row(candidate, False)
                for candidate in rejected
                if candidate.score >= max(0, args.min_candidate_score - 2)
            ][:args.audit_sample_size],
        },
        "linkage_counts": dict(linkage_counts),
        "accepted_candidate_ids_consumed": len(consumed.intersection(accepted_ids)),
        "accepted_candidate_ids_unconsumed": sorted(accepted_ids - consumed),
    }
    return episodes, audit


def counter_rows(counter: Counter[str], denominator: int) -> list[dict[str, Any]]:
    return [
        {
            "value": value,
            "count": count,
            "share": round(count / denominator, 6) if denominator else None,
        }
        for value, count in sorted(
            counter.items(), key=lambda item: (-item[1], item[0])
        )
    ]


def profile_for_outcome(
    episodes: Sequence[dict[str, Any]], outcome: str
) -> dict[str, Any]:
    eligible = [
        episode for episode in episodes
        if episode["outcome"] == outcome
        and episode["eligible_trade_instances_for_win_loss_confluence_comparison"] == 1
    ]
    return {
        "basis": (
            "Only actual-or-unspecified, single-instance episodes with an "
            "explicit win/loss and attributable setup detail."
        ),
        "eligible_trade_instances": len(eligible),
        "confluences": counter_rows(Counter(
            tag for episode in eligible for tag in set(episode["confluences"])
        ), len(eligible)),
        "instruments": counter_rows(Counter(
            instrument for episode in eligible
            for instrument in set(episode["instrument"])
        ), len(eligible)),
        "sessions": counter_rows(Counter(
            episode["session"] for episode in eligible
        ), len(eligible)),
        "setup_times": counter_rows(Counter(
            episode["setup_time"] or "unknown" for episode in eligible
        ), len(eligible)),
        "directions": counter_rows(Counter(
            episode["direction"] for episode in eligible
        ), len(eligible)),
        "rules_or_execution_issues": counter_rows(Counter(
            issue for episode in eligible
            for issue in set(episode["rules_violated_or_execution_issues"])
        ), len(eligible)),
    }


def summarize_episodes(episodes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_outcome = Counter(episode["outcome"] for episode in episodes)
    by_kind = Counter(episode["episode_kind"] for episode in episodes)
    by_linkage = Counter(episode["linkage_strength"] for episode in episodes)
    reported_instances: Counter[str] = Counter()
    eligible: Counter[str] = Counter()
    exclusion_reasons: Counter[str] = Counter()
    for episode in episodes:
        if episode["outcome_counts"]:
            reported_instances.update(episode["outcome_counts"])
        elif (
            episode["outcome"] != "mixed"
            and isinstance(episode["trade_count_reported"], int)
        ):
            reported_instances[episode["outcome"]] += episode["trade_count_reported"]
        eligible_count = episode[
            "eligible_trade_instances_for_win_loss_confluence_comparison"
        ]
        if eligible_count:
            eligible[episode["outcome"]] += eligible_count
        else:
            if episode["outcome"] not in {"win", "loss"}:
                exclusion_reasons[f"outcome_{episode['outcome']}"] += 1
            if episode["execution_mode"] == "paper":
                exclusion_reasons["paper_execution"] += 1
            if episode["shared_confluence_attribution_across_instances"]:
                exclusion_reasons["shared_or_multi_trade_attribution"] += 1
            if not episode["confluences"]:
                exclusion_reasons["no_attributable_confluence"] += 1
            if episode["trade_count_reported"] != 1:
                exclusion_reasons["not_explicit_single_instance"] += 1

    return {
        "episode_records": len(episodes),
        "records_by_outcome": dict(sorted(by_outcome.items())),
        "records_by_episode_kind": dict(sorted(by_kind.items())),
        "records_by_linkage_strength": dict(sorted(by_linkage.items())),
        "reported_trade_instances_by_outcome_including_resolvable_aggregates": dict(
            sorted(reported_instances.items())
        ),
        "eligible_actual_trade_instances_for_win_loss_confluence_comparison": dict(
            sorted(eligible.items())
        ),
        "records_excluded_from_win_loss_confluence_comparison": (
            len(episodes) - sum(eligible.values())
        ),
        "exclusion_reason_frequency_nonexclusive": dict(
            sorted(exclusion_reasons.items())
        ),
    }


def confluence_comparison(
    episodes: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    eligible = [
        episode for episode in episodes
        if episode["eligible_trade_instances_for_win_loss_confluence_comparison"] == 1
    ]
    denominators = Counter(episode["outcome"] for episode in eligible)
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for episode in eligible:
        for tag in set(episode["confluences"]):
            counts[tag][episode["outcome"]] += 1
    rows: list[dict[str, Any]] = []
    for tag, values in counts.items():
        wins = values["win"]
        losses = values["loss"]
        total = wins + losses
        rows.append({
            "confluence": tag,
            "eligible_wins_with_confluence": wins,
            "eligible_losses_with_confluence": losses,
            "eligible_instances_with_confluence": total,
            "descriptive_win_share_when_present": round(wins / total, 6)
            if total else None,
            "frequency_among_all_eligible_wins": round(
                wins / denominators["win"], 6
            ) if denominators["win"] else None,
            "frequency_among_all_eligible_losses": round(
                losses / denominators["loss"], 6
            ) if denominators["loss"] else None,
            "warning": (
                "Descriptive Discord-journal frequency only; not a causal or "
                "out-of-sample probability estimate."
            ),
        })
    rows.sort(key=lambda row: (
        -row["eligible_instances_with_confluence"], row["confluence"]
    ))
    return rows


def rejection_block_comparison(
    episodes: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    eligible = [
        episode for episode in episodes
        if episode["eligible_trade_instances_for_win_loss_confluence_comparison"] == 1
    ]
    rows: Counter[tuple[str, str, str]] = Counter()
    rb_episodes = 0
    for episode in eligible:
        instances = episode["rejection_block_use"]["instances"]
        if instances:
            rb_episodes += 1
        for instance in {
            (item["timeframe"], item["role"]) for item in instances
        }:
            rows[(instance[0], instance[1], episode["outcome"])] += 1
    grouped: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"win": 0, "loss": 0}
    )
    for (tf, role, outcome), count in rows.items():
        grouped[(tf, role)][outcome] += count
    variants = []
    for (tf, role), values in grouped.items():
        total = values["win"] + values["loss"]
        variants.append({
            "timeframe": tf,
            "role": role,
            "eligible_wins": values["win"],
            "eligible_losses": values["loss"],
            "eligible_instances": total,
            "descriptive_win_share": round(values["win"] / total, 6)
            if total else None,
        })
    variants.sort(key=lambda row: (
        -row["eligible_instances"], row["timeframe"], row["role"]
    ))
    return {
        "eligible_episodes_with_any_rejection_block": rb_episodes,
        "eligible_episodes_without_rejection_block": len(eligible) - rb_episodes,
        "variants": variants,
        "caution": (
            "A rejection-block mention is counted only when linked to an "
            "eligible episode; missing mentions are not proof the concept was unused."
        ),
    }


def instrument_comparison(
    episodes: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for episode in episodes:
        if episode[
            "eligible_trade_instances_for_win_loss_confluence_comparison"
        ] != 1:
            continue
        for instrument in set(episode["instrument"]):
            counts[instrument][episode["outcome"]] += 1
    rows = []
    for instrument, values in counts.items():
        total = values["win"] + values["loss"]
        rows.append({
            "instrument": instrument,
            "eligible_wins": values["win"],
            "eligible_losses": values["loss"],
            "eligible_instances": total,
            "descriptive_win_share": round(values["win"] / total, 6)
            if total else None,
        })
    rows.sort(key=lambda row: (-row["eligible_instances"], row["instrument"]))
    return rows


def author_concentration(
    episodes: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    counts = Counter(
        episode["author"] for episode in episodes
        if episode["eligible_trade_instances_for_win_loss_confluence_comparison"] == 1
    )
    total = sum(counts.values())
    rows = counter_rows(counts, total)
    return {
        "eligible_instances": total,
        "authors": rows,
        "largest_author_share": rows[0]["share"] if rows else None,
        "caution": (
            "Results describe the captured journal authors and may be dominated "
            "by prolific posters; they are not an independent trade sample."
        ),
    }


def load_reference(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    if not path.exists():
        return {
            "reference_path": str(path.resolve()),
            "loaded": False,
            "reason": "reference_file_not_found",
        }
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    summary = data.get("episode_summary", {})
    return {
        "reference_path": str(path.resolve()),
        "loaded": True,
        "schema_version": data.get("schema_version"),
        "episode_records": summary.get("episode_records"),
        "records_by_outcome": summary.get("records_by_outcome"),
        "note": (
            "Reference is reported only as a regression benchmark. It does not "
            "alter candidate scoring, linkage, features, or outcomes."
        ),
    }


def validate_output(
    messages: Sequence[Message],
    episodes: Sequence[dict[str, Any]],
    audit: dict[str, Any],
) -> dict[str, Any]:
    known_ids = {message.message_id for message in messages}
    errors: list[str] = []
    warnings: list[str] = []
    episode_ids = [episode["episode_id"] for episode in episodes]
    if len(episode_ids) != len(set(episode_ids)):
        errors.append("duplicate_episode_ids")
    unknown_evidence_refs = []
    evidence_ref_count = 0
    evidence_distinct_ids: set[str] = set()
    for episode in episodes:
        if episode["outcome"] not in ALLOWED_OUTCOMES:
            errors.append(
                f"invalid_outcome:{episode['episode_id']}:{episode['outcome']}"
            )
        if not episode["evidence"]:
            errors.append(f"missing_evidence:{episode['episode_id']}")
        if not episode["source_candidate_message_ids"]:
            errors.append(f"missing_candidate_anchor:{episode['episode_id']}")
        for evidence in episode["evidence"]:
            evidence_ref_count += 1
            message_id = evidence.get("message_id")
            evidence_distinct_ids.add(str(message_id))
            if message_id not in known_ids:
                unknown_evidence_refs.append({
                    "episode_id": episode["episode_id"],
                    "message_id": message_id,
                })
        count = episode["trade_count_reported"]
        outcome_sum = sum(episode["outcome_counts"].values())
        if isinstance(count, int) and outcome_sum and outcome_sum > count:
            errors.append(
                f"outcome_counts_exceed_trade_count:{episode['episode_id']}"
            )
    if unknown_evidence_refs:
        errors.append("evidence_references_unknown_message_ids")
    if audit["accepted_candidate_ids_unconsumed"]:
        errors.append("accepted_candidate_ids_left_unconsumed")

    unknown_outcomes = sum(
        episode["outcome"] in {"unknown", "mixed"} for episode in episodes
    )
    if unknown_outcomes:
        warnings.append(
            f"{unknown_outcomes} episodes retain unknown or mixed outcomes by design"
        )
    unknown_instruments = sum(
        episode["instrument"] == ["unknown"] for episode in episodes
    )
    if unknown_instruments:
        warnings.append(
            f"{unknown_instruments} episodes retain unknown executed instrument"
        )

    totals = {
        "parsed_primary_messages": len(messages),
        "candidate_messages_accepted": audit["candidate_scoring"][
            "accepted_candidates"
        ],
        "candidate_messages_rejected": audit["candidate_scoring"][
            "rejected_messages"
        ],
        "episode_records": len(episodes),
        "episode_records_unknown_or_mixed_outcome": unknown_outcomes,
        "episode_records_unknown_executed_instrument": unknown_instruments,
        "episode_records_unknown_direction": sum(
            episode["direction"] == "unknown" for episode in episodes
        ),
        "episode_records_unknown_setup_time": sum(
            episode["setup_time"] is None for episode in episodes
        ),
        "evidence_references": evidence_ref_count,
        "distinct_evidence_message_ids": len(evidence_distinct_ids),
        "eligible_win_loss_comparison_instances": sum(
            episode[
                "eligible_trade_instances_for_win_loss_confluence_comparison"
            ] for episode in episodes
        ),
    }
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "unknown_evidence_references": unknown_evidence_refs,
        "totals": totals,
        "invariants_checked": [
            "episode IDs are unique",
            "outcomes belong to the declared vocabulary",
            "every episode has an anchor and evidence",
            "all evidence message IDs exist in the input",
            "explicit outcome counts do not exceed explicit trade totals",
            "every accepted candidate is consumed exactly once as an anchor",
        ],
    }


def build_document(
    raw: dict[str, Any],
    messages: Sequence[Message],
    dedupe_stats: dict[str, Any],
    episodes: Sequence[dict[str, Any]],
    audit: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    validation = validate_output(messages, episodes, audit)
    dates = [message.timestamp_utc for message in messages]
    metadata = raw.get("metadata", {})
    requested_window = resolve_requested_window(metadata)
    enforce_three_month_window(validation, requested_window)
    return {
        "schema_version": "3.0.0-auto-conservative",
        "scope": {
            "source_file": str(Path(args.input).resolve()),
            "source_scope": "Discord export only; no outside knowledge or market data",
            "message_collection": "primary_messages",
            "guild_id": metadata.get("guild_id"),
            "primary_channel_id": metadata.get("primary_channel_id"),
            "requested_window_start_date": requested_window[
                "start_date_inclusive"
            ],
            "requested_window_end_date": requested_window[
                "end_date_inclusive"
            ],
            "requested_window_end_exclusive": requested_window[
                "end_date_exclusive"
            ],
            "requested_window_inclusive_calendar_days": requested_window[
                "inclusive_calendar_days"
            ],
            "requested_window_metadata_source": requested_window[
                "metadata_source"
            ],
            "observed_first_timestamp_utc": iso_z(min(dates)) if dates else None,
            "observed_last_timestamp_utc": iso_z(max(dates)) if dates else None,
            "timezone_for_date_grouping": args.timezone,
            "generated_at_utc": iso_z(datetime.now(timezone.utc)),
        },
        "methodology": {
            "candidate_scoring": (
                "Deterministic text rules require explicit trade numbering, a "
                "journal template, execution language, terminal result language, "
                "active-position language, or explicit no-trade language."
            ),
            "linkage_order": [
                "explicit multi-trade sections within one message",
                "explicit Trade N sequences bounded by the next numbered trade",
                "remaining same-author/thread/local-date candidates within a bounded gap",
            ],
            "outcome_policy": (
                "Only explicit result words, signed R/dollar results, aggregate "
                "counts, open-position language, or no-trade language are used. "
                "Hypothetical/provisional statements are excluded; conflicts stay mixed."
            ),
            "feature_policy": (
                "Confluences, instruments, time references, direction, sessions, "
                "and rejection-block flags come from linked evidence text and retain "
                "per-field message IDs. Executed instrument uses strict execution phrasing; "
                "other symbols remain market context."
            ),
            "unknown_policy": (
                "Missing or ambiguous outcome, instrument, direction, time, role, "
                "or timeframe stays unknown/unspecified."
            ),
            "win_loss_comparison_eligibility": (
                "Actual-or-unspecified execution, exactly one reported instance, "
                "explicit win/loss, and at least one attributable confluence."
            ),
            "parameters": {
                "minimum_candidate_score": args.min_candidate_score,
                "cluster_gap_minutes": args.cluster_gap_minutes,
                "numbered_follow_minutes": args.numbered_follow_minutes,
                "context_before_minutes": args.context_before_minutes,
                "context_after_minutes": args.context_after_minutes,
                "max_evidence_messages": args.max_evidence_messages,
            },
        },
        "data_quality": {
            "deduplication": dedupe_stats,
            "limitations": [
                "Text-only export: image-only setup details cannot be recovered.",
                "Discord posts are self-reported and are not independently verified.",
                "Thread/time proximity is conservative linkage evidence, not certainty.",
                "Unknown fields and mixed outcomes are intentionally not imputed.",
                "Supplemental Q&A arrays are not treated as personal trade episodes.",
            ],
        },
        "candidate_audit": audit,
        "episode_summary": summarize_episodes(episodes),
        "profiles": {
            "win_profile": profile_for_outcome(episodes, "win"),
            "loss_profile": profile_for_outcome(episodes, "loss"),
        },
        "rejection_block_comparison": rejection_block_comparison(episodes),
        "confluence_frequency_comparison": confluence_comparison(episodes),
        "instrument_outcome_comparison": instrument_comparison(episodes),
        "eligible_author_concentration": author_concentration(episodes),
        "reference_benchmark": load_reference(
            Path(args.reference_analysis) if args.reference_analysis else None
        ),
        "validation": validation,
        "episodes": list(episodes),
    }


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Conservatively extract auditable trade episodes from the primary "
            "messages in a Discord JSON export."
        )
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timezone", default="America/Chicago")
    parser.add_argument("--min-candidate-score", type=positive_int, default=4)
    parser.add_argument("--cluster-gap-minutes", type=positive_int, default=45)
    parser.add_argument("--numbered-follow-minutes", type=positive_int, default=20)
    parser.add_argument("--context-before-minutes", type=positive_int, default=30)
    parser.add_argument("--context-after-minutes", type=positive_int, default=15)
    parser.add_argument("--max-evidence-messages", type=positive_int, default=20)
    parser.add_argument("--audit-sample-size", type=positive_int, default=50)
    parser.add_argument(
        "--reference-analysis", type=Path,
        help="Optional prior analysis used only for count-level regression reporting.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite the requested output if it exists (never trade_analysis.json).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run extraction and validation but do not write the output.",
    )
    parser.add_argument(
        "--compact", action="store_true",
        help="Write compact JSON instead of indented JSON.",
    )
    return parser.parse_args(argv)


def resolve_cli_path(path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (Path.cwd() / path).resolve()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.input = resolve_cli_path(args.input)
    args.output = resolve_cli_path(args.output)
    if args.reference_analysis:
        args.reference_analysis = resolve_cli_path(args.reference_analysis)

    if args.output.resolve() == PROTECTED_14_DAY_OUTPUT:
        print(
            "ERROR: trade_analysis.json is permanently protected; choose a new output.",
            file=sys.stderr,
        )
        return 2
    if not args.input.exists():
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        return 2
    if args.output.exists() and not args.force and not args.dry_run:
        print(
            f"ERROR: output already exists: {args.output} (use --force to replace it)",
            file=sys.stderr,
        )
        return 2

    try:
        local_zone = resolve_timezone(args.timezone)
    except Exception as exc:
        print(f"ERROR: invalid timezone {args.timezone!r}: {exc}", file=sys.stderr)
        return 2
    try:
        with args.input.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception as exc:
        print(f"ERROR: could not load input JSON: {exc}", file=sys.stderr)
        return 2
    rows = raw.get("primary_messages")
    if not isinstance(rows, list):
        print("ERROR: input JSON must contain a primary_messages array", file=sys.stderr)
        return 2

    messages, dedupe_stats = dedupe_messages(rows, local_zone)
    episodes, audit = extract_episodes(messages, args)
    document = build_document(
        raw, messages, dedupe_stats, episodes, audit, args
    )
    validation = document["validation"]
    summary = {
        "input": str(args.input),
        "output": None if args.dry_run else str(args.output),
        "dry_run": args.dry_run,
        "validation_passed": validation["passed"],
        "validation_errors": validation["errors"],
        "totals": validation["totals"],
        "records_by_outcome": document["episode_summary"]["records_by_outcome"],
        "reference_benchmark": document["reference_benchmark"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not validation["passed"]:
        return 1
    if args.dry_run:
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            document,
            handle,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
        )
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
