from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "1.1.0"
GUILD_ID = "1167376964680691732"
PRIMARY_CHANNEL_ID = "1283941772577472643"
QUESTIONS_CHANNEL_ID = "1273692573898113076"
PREMIUM_CHAT_CHANNEL_ID = "1359593949110472777"

COLLECTION_KEYS = (
    "primary_messages",
    "server_rejection_phrase_messages",
    "questions_rb_messages",
    "questions_nq_es_messages",
    "broad_rb_shorthand_partial_messages",
    "contextual_qa_messages",
)

TRADING_PATTERNS = [
    re.compile(r"\btrade(?:d|s|r|rs|ing)?\b", re.I),
    re.compile(
        r"\b(?:win|wins|won|loss|losses|lost|breakeven|break\s*even|"
        r"pnl|profit|stop|stopped|target|tp|sl|rr|risk)\b",
        re.I,
    ),
    re.compile(r"\b(?:nq|mnq|es|mes|ym|mym)\b", re.I),
    re.compile(
        r"\b(?:rb|rbs|fvg|ifvg|ob|smt|ssmt|cisd|mss|bpr|ote|tfa|"
        r"mmxm|amd|po3|stdv|vwap|pda|breaker)\b|"
        r"rejection\s+block|order\s+block|pd\s+array",
        re.I,
    ),
    re.compile(
        r"\b(?:bias|bullish|bearish|longs?|shorts?|entry|execution|"
        r"invalidation|invalid|valid|confluence|setup|liquidity|sweep|"
        r"wick|ce|fib|gap|premium|discount|draw|inducement)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:10\s*am|9:?30|market\s+open|london|nyam|new\s+york|"
        r"premarket|pre-market|session|daily|4h|1h|15m|10m|5m|3m|1m|30s)\b",
        re.I,
    ),
]

OFFTOPIC_PATTERNS = [
    re.compile(r"\b(?:porn|girlfriend|gaming|motorcycle|gun|copytrader|backtrader)\b", re.I),
    re.compile(r"\b(?:welcome|boosted the server|wave to say hi)\b", re.I),
]

CONFLUENCE_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("rejection_block", "entry_structure", re.compile(r"\b(?:rb|rbs)\b|rejection\s+block", re.I)),
    (
        "10am_key_open",
        "time_level",
        re.compile(r"\b10\s*(?:am|a\.?m\.?)\b|\b10:00\b|\b10\s*(?:ko|key\s+open)\b", re.I),
    ),
    ("market_open_930", "time_level", re.compile(r"\b9:?30\b|market\s+open", re.I)),
    ("smt_ssmt", "intermarket", re.compile(r"\b(?:smt|ssmt)\b", re.I)),
    ("fvg_ifvg", "pd_array", re.compile(r"\b(?:fvg|ifvg|gap)\b", re.I)),
    ("order_block", "pd_array", re.compile(r"\border\s+block\b|\bob\b", re.I)),
    ("breaker", "entry_structure", re.compile(r"\bbreaker\b", re.I)),
    ("liquidity_sweep", "liquidity", re.compile(r"\bliquidity\b|\bliq\b|\bsweep(?:ed|s|ing)?\b", re.I)),
    ("ote_fibonacci", "location", re.compile(r"\bote\b|\bfib(?:onacci)?\b|\b(?:0?\.)?(?:5|50|618|62|705|705|79)\b", re.I)),
    ("premium_discount", "location", re.compile(r"\bpremium\b|\bdiscount\b|\bequilibrium\b|\beq\b", re.I)),
    ("higher_timeframe_bias", "context", re.compile(r"\bhtf\b|higher\s+time\s*frame|\b(?:daily|4h|1h)\s+bias\b", re.I)),
    ("timeframe_alignment", "context", re.compile(r"\balign(?:ed|ment|ing)?\b|\bmultiple\s+time\s*frames?\b", re.I)),
    ("cisd_mss_displacement", "confirmation", re.compile(r"\b(?:cisd|mss|displacement)\b", re.I)),
    ("engineered_liquidity", "liquidity", re.compile(r"engineered\s+liquidity|\bel\b", re.I)),
    ("standard_deviation", "location", re.compile(r"\bstdv\b|standard\s+deviation", re.I)),
    ("key_opens", "time_level", re.compile(r"\b(?:midnight|00:00|key\s+open|daily\s+open|weekly\s+open)\b", re.I)),
    ("news_filter", "risk_filter", re.compile(r"\b(?:news|red\s+folder|cpi|ppi|fomc|fed|tweet|trump)\b", re.I)),
    ("risk_management", "management", re.compile(r"\b(?:risk|stop|sl|breakeven|break\s*even|partial|runner|lockout)\b", re.I)),
    ("draw_on_liquidity", "target", re.compile(r"\bdraw\b|\b(?:pdh|pdl|bsl|ssl|eqh|eql)\b", re.I)),
    ("vwap_tdo", "context", re.compile(r"\b(?:vwap|tdo)\b", re.I)),
]

INSTRUMENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("NQ", re.compile(r"(?<![A-Z])NQ(?![A-Z])", re.I)),
    ("MNQ", re.compile(r"(?<![A-Z])MNQ(?![A-Z])", re.I)),
    ("ES", re.compile(r"(?<![A-Z])ES(?![A-Z])", re.I)),
    ("MES", re.compile(r"(?<![A-Z])MES(?![A-Z])", re.I)),
    ("YM", re.compile(r"(?<![A-Z])YM(?![A-Z])", re.I)),
    ("MYM", re.compile(r"(?<![A-Z])MYM(?![A-Z])", re.I)),
]


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def parse_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def normalized_text(message: dict[str, Any]) -> str:
    return " ".join(
        part.strip()
        for part in (
            message.get("content_text") or "",
            message.get("reply_to_content") or "",
            message.get("reply_context") or "",
        )
        if part and part.strip()
    )


def relevance_score(message: dict[str, Any]) -> tuple[int, str, str | None]:
    text = normalized_text(message)
    score = sum(bool(pattern.search(text)) for pattern in TRADING_PATTERNS)
    if re.search(r"\b(?:rb|rbs)\b|rejection\s+block", text, re.I):
        score += 4
    if re.search(
        r"\b(?:win|wins|won|loss|losses|lost|breakeven|break\s*even|"
        r"stopped\s+out|tp\s+hit|target\s+hit|took\s+(?:an?\s+)?[wl])\b",
        text,
        re.I,
    ):
        score += 2
    if "?" in text and score >= 2:
        score += 1
    if message.get("attachments") and score >= 1:
        score += 1
    if message.get("reply_to_message_id") and score >= 2:
        score += 1

    off_topic_hits = sum(bool(pattern.search(text)) for pattern in OFFTOPIC_PATTERNS)
    if off_topic_hits and score < 5:
        score -= off_topic_hits * 2

    if score >= 6:
        return score, "core", None
    if score >= 3:
        return score, "supporting", None
    reason = "empty_or_chart_only" if not text else "off_topic_or_low_trading_signal"
    return score, "excluded", reason


def merge_message(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key in {"attachments", "links", "emoji_alt", "image_alt"}:
            old_items = merged.get(key) or []
            seen = {compact_json(item) if isinstance(item, dict) else str(item) for item in old_items}
            combined = list(old_items)
            for item in value or []:
                marker = compact_json(item) if isinstance(item, dict) else str(item)
                if marker not in seen:
                    seen.add(marker)
                    combined.append(item)
            merged[key] = combined
        elif value not in (None, "", [], {}) and merged.get(key) in (None, "", [], {}):
            merged[key] = value
        elif key in {"content_text", "visible_text", "reply_context", "reply_to_content"}:
            if len(str(value or "")) > len(str(merged.get(key) or "")):
                merged[key] = value
    return merged


def collect_messages(raw: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    messages: dict[str, dict[str, Any]] = {}
    sources: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for collection_name in COLLECTION_KEYS:
        for row in raw.get(collection_name, []) or []:
            message_id = str(row.get("message_id") or "").strip()
            if not message_id:
                continue
            messages[message_id] = merge_message(messages.get(message_id, {}), row)
            sources[message_id].append(
                {
                    "collection_name": collection_name,
                    "query": row.get("search_query"),
                    "result_index": row.get("result_index") or 0,
                    "page_number": row.get("page_number") or 0,
                }
            )
    return messages, sources


def propagate_unique_thread_channel_ids(messages: dict[str, dict[str, Any]]) -> None:
    """Reuse an attachment-derived thread ID within an unambiguous journal thread.

    Discord search results expose a thread/channel ID reliably on some attachment
    records but not on every text-only message. A unique observed ID for a thread
    title can therefore be propagated to its other messages. Duplicate thread
    titles that expose more than one ID are deliberately left unresolved.
    """
    ids_by_thread: dict[str, set[str]] = collections.defaultdict(set)
    for row in messages.values():
        group = row.get("group_label") or ""
        if not (row.get("parent_channel") == "premium-journals" or group.endswith(", premium-journals")):
            continue
        thread_key = (row.get("thread_title") or group).strip()
        channel_id = str(row.get("inferred_thread_channel_id") or "").strip()
        if thread_key and re.fullmatch(r"\d{15,22}", channel_id):
            ids_by_thread[thread_key].add(channel_id)

    for row in messages.values():
        if row.get("inferred_thread_channel_id"):
            continue
        group = row.get("group_label") or ""
        if not (row.get("parent_channel") == "premium-journals" or group.endswith(", premium-journals")):
            continue
        thread_key = (row.get("thread_title") or group).strip()
        candidates = ids_by_thread.get(thread_key, set())
        if len(candidates) == 1:
            row["inferred_thread_channel_id"] = next(iter(candidates))
            row["thread_channel_id_propagated"] = True


def curated_message_ids(curated: dict[str, Any]) -> set[str]:
    """Collect every Discord message ID referenced by the curated artifact."""
    found: set[str] = set()

    def visit(value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, child_key)
            return
        if isinstance(value, list):
            for child in value:
                visit(child, key)
            return
        if isinstance(value, (str, int)):
            text = str(value)
            if (
                (key and "message_id" in key)
                or (key in {"evidence_message_ids", "message_ids"})
            ) and re.fullmatch(r"\d{15,22}", text):
                found.add(text)

    visit(curated)
    return found


def infer_author_continuations(messages: dict[str, dict[str, Any]]) -> None:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in messages.values():
        grouped[(row.get("group_label") or "", row.get("search_query") or "")].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda item: item.get("timestamp_utc") or "")
        last_author = ""
        for row in rows:
            if row.get("author"):
                last_author = row["author"]
            elif last_author:
                row["author"] = last_author
                row["author_inferred_from_continuation"] = True


def resolve_container(message: dict[str, Any]) -> dict[str, str | None]:
    group = message.get("group_label") or ""
    thread_title = message.get("thread_title") or ""
    parent = message.get("parent_channel") or ""
    inferred = message.get("inferred_thread_channel_id")

    if parent == "premium-journals" or group.endswith(", premium-journals"):
        channel_name = "premium-journals"
        parent_channel_id = PRIMARY_CHANNEL_ID
        channel_id = inferred or f"thread-unknown:{hashlib.sha1(thread_title.encode('utf-8')).hexdigest()[:16]}"
        return {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "parent_channel_id": parent_channel_id,
            "thread_title": thread_title or None,
            "container_kind": "thread",
        }
    if "❓│questions" in group or thread_title == "❓│questions":
        return {
            "channel_id": QUESTIONS_CHANNEL_ID,
            "channel_name": "❓│questions",
            "parent_channel_id": None,
            "thread_title": None,
            "container_kind": "text",
        }
    if "📍│chat" in group or thread_title == "📍│chat":
        return {
            "channel_id": PREMIUM_CHAT_CHANNEL_ID,
            "channel_name": "📍│chat",
            "parent_channel_id": None,
            "thread_title": None,
            "container_kind": "text",
        }
    if "💬│chat" in group or thread_title == "💬│chat":
        return {
            "channel_id": "channel-unknown:freemium-chat",
            "channel_name": "💬│chat",
            "parent_channel_id": None,
            "thread_title": None,
            "container_kind": "text",
        }
    return {
        "channel_id": inferred or f"container-unknown:{hashlib.sha1(group.encode('utf-8')).hexdigest()[:16]}",
        "channel_name": thread_title or parent or group or "unknown",
        "parent_channel_id": None,
        "thread_title": None,
        "container_kind": "other",
    }


def extract_instruments(text: str) -> list[str]:
    return [name for name, pattern in INSTRUMENT_PATTERNS if pattern.search(text)]


def extract_confluences(text: str) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for name, category, pattern in CONFLUENCE_PATTERNS:
        match = pattern.search(text)
        if match:
            found.append((name, category, match.group(0)))
    return found


def classify_message(text: str) -> list[str]:
    tags: list[str] = []
    checks = [
        ("rejection_block", r"\b(?:rb|rbs)\b|rejection\s+block"),
        ("question", r"\?"),
        ("rule_or_definition", r"\b(?:rule|always|never|valid|invalid|should|need\s+to|wait\s+for)\b"),
        ("trade_report", r"\b(?:trade|entry|entered|long|short|risked|target|stop)\b"),
        ("outcome", r"\b(?:win|won|loss|lost|breakeven|break\s*even|stopped\s+out|tp\s+hit)\b"),
        ("rule_breach", r"\b(?:broke\s+(?:my\s+)?rules?|rule\s+break|forced|revenge|overtrad)\b"),
        ("no_trade", r"\b(?:no\s+trade|didn['’]?t\s+trade|skipped|missed\s+the\s+entry|cancelled)\b"),
        ("high_probability_claim", r"\b(?:high\s+probability|a\+|a\s+setup|picture\s+perfect|perfect\s+setup)\b"),
        ("low_probability_claim", r"\b(?:low\s+probability|lower\s+probability|bad\s+setup|not\s+valid)\b"),
    ]
    for tag, pattern in checks:
        if re.search(pattern, text, re.I):
            tags.append(tag)
    return tags


def outcome_mentions(text: str) -> dict[str, int]:
    lower = text.lower()
    counts = {"win": 0, "loss": 0, "breakeven": 0}

    compact = re.sub(r"\s+", " ", lower)
    summary_patterns = {
        "win": [r"(\d+)\s*(?:wins?|w)\b", r"(?:wins?|w)\s*[:=-]?\s*(\d+)\b"],
        "loss": [r"(\d+)\s*(?:loss(?:es)?|l(?:'s)?)\b", r"(?:loss(?:es)?|l)\s*[:=-]?\s*(\d+)\b"],
        "breakeven": [r"(\d+)\s*(?:breakevens?|break\s*evens?|be(?:'s)?)\b"],
    }
    for outcome, patterns in summary_patterns.items():
        values = [int(m.group(1)) for pattern in patterns for m in re.finditer(pattern, compact, re.I)]
        if values:
            counts[outcome] = max(values)

    explicit_lines = {
        "win": len(re.findall(r"\btrade\s*\d+\s*:\s*(?:win|w)\b", lower, re.I)),
        "loss": len(re.findall(r"\btrade\s*\d+\s*:\s*(?:loss|lose|l)\b", lower, re.I)),
        "breakeven": len(re.findall(r"\btrade\s*\d+\s*:\s*(?:breakeven|break\s*even|be)\b", lower, re.I)),
    }
    for outcome, value in explicit_lines.items():
        counts[outcome] = max(counts[outcome], value)

    if sum(counts.values()) == 0:
        if re.search(r"\b(?:took|had|was|ended)\s+(?:an?\s+)?(?:win|w)\b|\b(?:tp|target)\s+(?:hit|reached)\b", lower, re.I):
            counts["win"] = 1
        if re.search(r"\b(?:took|had|was|ended)\s+(?:an?\s+)?(?:loss|l)\b|\bstopped\s+out\b|\bhit\s+(?:my\s+)?sl\b", lower, re.I):
            counts["loss"] = 1
        if re.search(r"\b(?:breakeven|break\s*even|stopped\s+(?:at\s+)?be|got\s+be)\b", lower, re.I):
            counts["breakeven"] = 1
    return counts


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = FULL;

        CREATE TABLE meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TABLE research_runs (
          run_id INTEGER PRIMARY KEY,
          schema_version TEXT NOT NULL,
          guild_id TEXT NOT NULL,
          guild_name TEXT NOT NULL,
          primary_channel_id TEXT NOT NULL,
          primary_channel_name TEXT NOT NULL,
          window_start_utc TEXT NOT NULL,
          window_end_utc TEXT NOT NULL,
          collected_at_utc TEXT NOT NULL,
          source_scope TEXT NOT NULL CHECK(source_scope='discord_only'),
          status TEXT NOT NULL CHECK(status IN ('complete','partial')),
          methodology TEXT NOT NULL,
          limitations TEXT NOT NULL
        );

        CREATE TABLE collection_coverage (
          coverage_id INTEGER PRIMARY KEY,
          run_id INTEGER NOT NULL REFERENCES research_runs(run_id),
          collection_name TEXT NOT NULL,
          query_text TEXT,
          scan_complete INTEGER NOT NULL CHECK(scan_complete IN (0,1)),
          messages_seen INTEGER NOT NULL,
          earliest_message_utc TEXT,
          latest_message_utc TEXT,
          gap_notes TEXT,
          UNIQUE(run_id, collection_name, query_text)
        );

        CREATE TABLE exclusion_stats (
          run_id INTEGER NOT NULL REFERENCES research_runs(run_id),
          collection_name TEXT NOT NULL,
          reason TEXT NOT NULL,
          excluded_count INTEGER NOT NULL,
          PRIMARY KEY(run_id, collection_name, reason)
        );

        CREATE TABLE channels (
          channel_id TEXT PRIMARY KEY,
          guild_id TEXT NOT NULL,
          name TEXT NOT NULL,
          kind TEXT NOT NULL,
          parent_channel_id TEXT,
          exact_id_known INTEGER NOT NULL CHECK(exact_id_known IN (0,1)),
          notes TEXT
        );

        CREATE TABLE messages (
          message_id TEXT PRIMARY KEY,
          channel_id TEXT NOT NULL REFERENCES channels(channel_id),
          parent_channel_id TEXT,
          channel_name TEXT NOT NULL,
          thread_title TEXT,
          author_display_name TEXT,
          author_inferred INTEGER NOT NULL DEFAULT 0 CHECK(author_inferred IN (0,1)),
          created_at_utc TEXT NOT NULL,
          displayed_time TEXT,
          edited INTEGER NOT NULL DEFAULT 0 CHECK(edited IN (0,1)),
          is_original_poster INTEGER NOT NULL DEFAULT 0 CHECK(is_original_poster IN (0,1)),
          reply_to_message_id TEXT,
          reply_to_content TEXT,
          content_text TEXT NOT NULL DEFAULT '',
          visible_text TEXT,
          content_sha256 TEXT NOT NULL,
          permalink TEXT,
          permalink_confidence TEXT NOT NULL CHECK(permalink_confidence IN ('exact','inferred','unavailable')),
          relevance TEXT NOT NULL CHECK(relevance IN ('core','supporting')),
          relevance_score INTEGER NOT NULL,
          source_json TEXT NOT NULL
        );

        CREATE TABLE message_sources (
          message_id TEXT NOT NULL REFERENCES messages(message_id),
          collection_name TEXT NOT NULL,
          query_text TEXT NOT NULL DEFAULT '',
          result_index INTEGER,
          page_number INTEGER,
          PRIMARY KEY(message_id, collection_name, query_text)
        );

        CREATE TABLE attachments (
          attachment_id TEXT PRIMARY KEY,
          message_id TEXT NOT NULL REFERENCES messages(message_id),
          filename TEXT,
          discord_url TEXT,
          source_channel_id TEXT,
          media_kind TEXT,
          extracted_text TEXT,
          extraction_status TEXT NOT NULL CHECK(extraction_status IN ('not_attempted','complete','partial','failed')),
          notes TEXT
        );

        CREATE TABLE message_tags (
          message_id TEXT NOT NULL REFERENCES messages(message_id),
          tag TEXT NOT NULL,
          PRIMARY KEY(message_id, tag)
        );

        CREATE TABLE message_instruments (
          message_id TEXT NOT NULL REFERENCES messages(message_id),
          instrument TEXT NOT NULL,
          PRIMARY KEY(message_id, instrument)
        );

        CREATE TABLE confluences (
          confluence_id INTEGER PRIMARY KEY,
          canonical_name TEXT NOT NULL UNIQUE,
          category TEXT NOT NULL,
          corpus_definition TEXT,
          caveat TEXT
        );

        CREATE TABLE message_confluences (
          message_id TEXT NOT NULL REFERENCES messages(message_id),
          confluence_id INTEGER NOT NULL REFERENCES confluences(confluence_id),
          exact_phrase TEXT,
          attribution TEXT NOT NULL CHECK(attribution IN ('explicit_term_match','curated')),
          PRIMARY KEY(message_id, confluence_id)
        );

        CREATE TABLE outcome_mentions (
          message_id TEXT NOT NULL REFERENCES messages(message_id),
          outcome TEXT NOT NULL CHECK(outcome IN ('win','loss','breakeven')),
          stated_count INTEGER NOT NULL CHECK(stated_count > 0),
          extraction_method TEXT NOT NULL,
          confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
          PRIMARY KEY(message_id, outcome)
        );

        CREATE TABLE qa_pairs (
          qa_id INTEGER PRIMARY KEY,
          question_message_id TEXT REFERENCES messages(message_id),
          answer_message_id TEXT REFERENCES messages(message_id),
          normalized_question TEXT NOT NULL,
          answer_summary TEXT,
          status TEXT NOT NULL CHECK(status IN ('answered','partial','conflicting','unanswered','ambiguous')),
          topic TEXT NOT NULL,
          confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
          notes TEXT
        );

        CREATE TABLE rejection_block_findings (
          finding_id INTEGER PRIMARY KEY,
          facet TEXT NOT NULL CHECK(facet IN ('definition','identification','invalidation','timing','high_probability','low_probability','instrument_comparison','execution','risk','other')),
          finding TEXT NOT NULL,
          evidence_status TEXT NOT NULL CHECK(evidence_status IN ('explicit','observed_association','derived','insufficient_evidence')),
          confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
          instrument_scope TEXT,
          timeframe_scope TEXT,
          session_scope TEXT,
          caveat TEXT
        );

        CREATE TABLE rejection_block_finding_evidence (
          finding_id INTEGER NOT NULL REFERENCES rejection_block_findings(finding_id),
          message_id TEXT NOT NULL REFERENCES messages(message_id),
          evidence_role TEXT NOT NULL CHECK(evidence_role IN ('supports','contradicts','qualifies')),
          excerpt TEXT NOT NULL,
          PRIMARY KEY(finding_id, message_id, evidence_role)
        );

        CREATE TABLE trades (
          trade_id TEXT PRIMARY KEY,
          trader TEXT,
          trade_date TEXT,
          setup_time_text TEXT,
          post_time_utc TEXT,
          instrument TEXT,
          direction TEXT CHECK(direction IN ('long','short','unknown')),
          setup_name TEXT,
          timeframe TEXT,
          session_name TEXT,
          outcome TEXT NOT NULL CHECK(outcome IN ('win','loss','breakeven','mixed_partial','cancelled_no_trade','open','unknown')),
          outcome_basis TEXT NOT NULL,
          outcome_confidence REAL NOT NULL CHECK(outcome_confidence BETWEEN 0 AND 1),
          entry_text TEXT,
          invalidation_text TEXT,
          stop_text TEXT,
          target_text TEXT,
          management_text TEXT,
          notes TEXT
        );

        CREATE TABLE trade_evidence (
          trade_id TEXT NOT NULL REFERENCES trades(trade_id),
          message_id TEXT NOT NULL REFERENCES messages(message_id),
          evidence_role TEXT NOT NULL CHECK(evidence_role IN ('context','setup','entry','invalidation','outcome','management')),
          excerpt TEXT,
          PRIMARY KEY(trade_id, message_id, evidence_role)
        );

        CREATE TABLE trade_confluences (
          trade_id TEXT NOT NULL REFERENCES trades(trade_id),
          confluence_id INTEGER NOT NULL REFERENCES confluences(confluence_id),
          state TEXT NOT NULL CHECK(state IN ('present','absent','violated','unknown')),
          attribution TEXT NOT NULL CHECK(attribution IN ('explicit','curated_inference')),
          evidence_message_id TEXT REFERENCES messages(message_id),
          notes TEXT,
          PRIMARY KEY(trade_id, confluence_id)
        );

        CREATE TABLE outcome_profiles (
          profile_id INTEGER PRIMARY KEY,
          outcome TEXT NOT NULL CHECK(outcome IN ('win','loss','breakeven')),
          summary TEXT NOT NULL,
          resolved_trade_count INTEGER NOT NULL,
          unknown_trade_count INTEGER NOT NULL,
          author_concentration TEXT,
          limitations TEXT NOT NULL,
          UNIQUE(outcome)
        );

        CREATE TABLE outcome_profile_confluences (
          profile_id INTEGER NOT NULL REFERENCES outcome_profiles(profile_id),
          confluence_id INTEGER NOT NULL REFERENCES confluences(confluence_id),
          role TEXT NOT NULL,
          observed_count INTEGER,
          observed_share REAL,
          rationale TEXT,
          PRIMARY KEY(profile_id, confluence_id, role)
        );

        CREATE TABLE probability_tiers (
          tier_id INTEGER PRIMARY KEY,
          label TEXT NOT NULL,
          rank_order INTEGER NOT NULL,
          basis TEXT NOT NULL CHECK(basis IN ('source_claimed','corpus_observed','synthesis')),
          definition TEXT NOT NULL,
          resolved_count INTEGER CHECK(resolved_count IS NULL OR resolved_count >= 0),
          wins INTEGER CHECK(wins IS NULL OR wins >= 0),
          losses INTEGER CHECK(losses IS NULL OR losses >= 0),
          breakevens INTEGER CHECK(breakevens IS NULL OR breakevens >= 0),
          unknowns INTEGER CHECK(unknowns IS NULL OR unknowns >= 0),
          observed_win_rate REAL CHECK(observed_win_rate IS NULL OR observed_win_rate BETWEEN 0 AND 1),
          limitations TEXT NOT NULL,
          UNIQUE(label),
          UNIQUE(rank_order)
        );

        CREATE TABLE trading_models (
          model_id INTEGER PRIMARY KEY,
          model_no INTEGER NOT NULL CHECK(model_no BETWEEN 1 AND 5),
          name TEXT NOT NULL,
          evidence_status TEXT NOT NULL CHECK(evidence_status IN ('documented','provisional_derived')),
          thesis TEXT NOT NULL,
          eligibility_context TEXT,
          identification TEXT,
          trigger_confirmation TEXT,
          invalidation TEXT,
          entry TEXT,
          stop TEXT,
          target TEXT,
          management TEXT,
          instrument_scope TEXT,
          timeframe_scope TEXT,
          session_scope TEXT,
          win_count INTEGER,
          loss_count INTEGER,
          breakeven_count INTEGER,
          unknown_count INTEGER,
          limitations TEXT NOT NULL,
          UNIQUE(model_no)
        );

        CREATE TABLE model_rules (
          rule_id INTEGER PRIMARY KEY,
          model_id INTEGER NOT NULL REFERENCES trading_models(model_id),
          rule_order INTEGER NOT NULL,
          rule_type TEXT NOT NULL CHECK(rule_type IN ('context','identification','entry','invalidation','risk','target','management','no_trade')),
          rule_text TEXT NOT NULL,
          required INTEGER NOT NULL CHECK(required IN (0,1)),
          UNIQUE(model_id, rule_order)
        );

        CREATE TABLE model_evidence (
          model_id INTEGER NOT NULL REFERENCES trading_models(model_id),
          message_id TEXT NOT NULL REFERENCES messages(message_id),
          evidence_role TEXT NOT NULL CHECK(evidence_role IN ('supports','failed_example','contradicts','qualifies')),
          excerpt TEXT,
          PRIMARY KEY(model_id, message_id, evidence_role)
        );

        CREATE TABLE research_questions (
          research_question_id INTEGER PRIMARY KEY,
          question_text TEXT NOT NULL,
          answer_status TEXT NOT NULL CHECK(answer_status IN ('answered','partial','insufficient_evidence')),
          answer_summary TEXT NOT NULL,
          limitations TEXT,
          evidence_message_ids_json TEXT NOT NULL
        );

        CREATE TABLE contradictions (
          contradiction_id INTEGER PRIMARY KEY,
          topic TEXT NOT NULL,
          description TEXT NOT NULL,
          message_id_a TEXT REFERENCES messages(message_id),
          message_id_b TEXT REFERENCES messages(message_id),
          resolution_status TEXT NOT NULL CHECK(resolution_status IN ('unresolved','qualified','resolved')),
          notes TEXT
        );

        CREATE TABLE analysis_documents (
          document_name TEXT PRIMARY KEY,
          created_by TEXT NOT NULL,
          content_json TEXT NOT NULL,
          notes TEXT
        );

        CREATE TABLE data_dictionary (
          table_name TEXT NOT NULL,
          column_name TEXT NOT NULL,
          description TEXT NOT NULL,
          PRIMARY KEY(table_name, column_name)
        );

        CREATE VIRTUAL TABLE messages_fts USING fts5(
          message_id UNINDEXED,
          author_display_name,
          channel_name,
          thread_title,
          content_text,
          reply_to_content,
          tokenize='unicode61'
        );

        CREATE VIEW v_rejection_block_evidence AS
        SELECT m.message_id, m.created_at_utc, m.author_display_name,
               m.channel_name, m.thread_title, m.content_text, m.permalink,
               GROUP_CONCAT(t.tag, ', ') AS tags
        FROM messages m
        JOIN message_tags t ON t.message_id=m.message_id
        WHERE EXISTS (
          SELECT 1 FROM message_tags r
          WHERE r.message_id=m.message_id AND r.tag='rejection_block'
        )
        GROUP BY m.message_id;

        CREATE VIEW v_answered_qa AS
        SELECT q.qa_id, q.topic, q.status, q.normalized_question, q.answer_summary,
               qm.permalink AS question_permalink, am.permalink AS answer_permalink,
               q.confidence, q.notes
        FROM qa_pairs q
        LEFT JOIN messages qm ON qm.message_id=q.question_message_id
        LEFT JOIN messages am ON am.message_id=q.answer_message_id
        WHERE q.status IN ('answered','partial','conflicting');

        CREATE VIEW v_trade_feature_matrix AS
        SELECT t.*,
               (
                 SELECT GROUP_CONCAT(feature, '; ')
                 FROM (
                   SELECT c.canonical_name || '=' || tc.state AS feature
                   FROM trade_confluences tc
                   JOIN confluences c ON c.confluence_id=tc.confluence_id
                   WHERE tc.trade_id=t.trade_id
                   ORDER BY c.canonical_name
                 )
               ) AS confluences
        FROM trades t;

        CREATE VIEW v_win_loss_confluence_comparison AS
        SELECT c.canonical_name,
               SUM(CASE WHEN t.outcome='win' AND tc.state='present' THEN 1 ELSE 0 END) AS win_trades,
               SUM(CASE WHEN t.outcome='loss' AND tc.state='present' THEN 1 ELSE 0 END) AS loss_trades,
               SUM(CASE WHEN t.outcome='breakeven' AND tc.state='present' THEN 1 ELSE 0 END) AS breakeven_trades,
               COUNT(DISTINCT CASE
                 WHEN tc.state='present' AND t.outcome IN ('win','loss','breakeven') THEN t.trade_id
               END) AS total_resolved_mentions,
               COUNT(DISTINCT CASE
                 WHEN tc.state='present' AND t.outcome NOT IN ('win','loss','breakeven') THEN t.trade_id
               END) AS unresolved_or_other_mentions
        FROM confluences c
        LEFT JOIN trade_confluences tc ON tc.confluence_id=c.confluence_id
        LEFT JOIN trades t ON t.trade_id=tc.trade_id
        GROUP BY c.confluence_id;

        CREATE VIEW v_model_cards AS
        SELECT m.model_no, m.name, m.evidence_status, m.thesis,
               m.eligibility_context, m.identification, m.trigger_confirmation,
               m.invalidation, m.entry, m.stop, m.target, m.management,
               m.instrument_scope, m.timeframe_scope, m.session_scope,
               m.win_count, m.loss_count, m.breakeven_count, m.unknown_count,
               m.limitations,
               (
                 SELECT GROUP_CONCAT(rule_line, char(10))
                 FROM (
                   SELECT r.rule_type || ': ' || r.rule_text AS rule_line
                   FROM model_rules r
                   WHERE r.model_id=m.model_id
                   ORDER BY r.rule_order
                 )
               ) AS rules
        FROM trading_models m
        ;

        CREATE VIEW v_llm_research_answers AS
        SELECT research_question_id, question_text, answer_status,
               answer_summary, limitations, evidence_message_ids_json
        FROM research_questions;
        """
    )


def insert_run_and_coverage(conn: sqlite3.Connection, raw: dict[str, Any]) -> None:
    metadata = raw.get("metadata") or {}
    limitations = (
        "Primary premium-journals coverage is complete for the 14-day query. "
        "The broad server-wide RB shorthand search is partial after result 325; "
        "the exact phrase search and the premium questions-channel RB search are complete. "
        "Discord search snippets and text were captured; chart images were not interpreted, "
        "and clean CDN URLs may later require refreshed Discord signatures. "
        "Post timestamps are not assumed to equal setup times."
    )
    methodology = (
        "Discord-only browser collection. The primary forum was retrieved page by page with "
        "message IDs, UTC timestamps, thread titles, text and attachment metadata. Supplemental "
        "searches targeted rejection-block phrases, RB shorthand in the premium questions channel, "
        "NQ/ES questions, and eight reply-linked contexts. Curated findings distinguish explicit "
        "source statements, observed associations and analyst-derived synthesis."
    )
    conn.execute(
        """
        INSERT INTO research_runs(
          run_id,schema_version,guild_id,guild_name,primary_channel_id,primary_channel_name,
          window_start_utc,window_end_utc,collected_at_utc,source_scope,status,methodology,limitations
        ) VALUES(1,?,?,?,?,?,?,?,?,?,'partial',?,?)
        """,
        (
            SCHEMA_VERSION,
            metadata.get("guild_id") or GUILD_ID,
            metadata.get("guild_name") or "unknown",
            metadata.get("primary_channel_id") or PRIMARY_CHANNEL_ID,
            metadata.get("primary_channel_name") or "premium-journals",
            "2026-07-06T00:00:00Z",
            "2026-07-21T00:00:00Z",
            metadata.get("collected_at_utc") or dt.datetime.now(dt.timezone.utc).isoformat(),
            "discord_only",
            methodology,
            limitations,
        ),
    )

    specs = [
        ("primary_messages", "in:premium-journals after:2026-07-06 before:2026-07-21", True, None),
        ("server_rejection_phrase_messages", "rejection block after:2026-07-06 before:2026-07-21", True, None),
        ("questions_rb_messages", "in:❓│questions RB after:2026-07-06 before:2026-07-21", True, None),
        ("questions_nq_es_messages", "in:❓│questions NQ ES after:2026-07-06 before:2026-07-21", True, None),
        (
            "broad_rb_shorthand_partial_messages",
            "RB after:2026-07-06 before:2026-07-21",
            False,
            "Search became unstable after result 325; this collection is supplemental only.",
        ),
        ("contextual_qa_messages", "eight direct message-context jumps", True, None),
    ]
    for name, query, complete, gap in specs:
        rows = raw.get(name, []) or []
        times = sorted(row.get("timestamp_utc") for row in rows if row.get("timestamp_utc"))
        conn.execute(
            """
            INSERT INTO collection_coverage(
              run_id,collection_name,query_text,scan_complete,messages_seen,
              earliest_message_utc,latest_message_utc,gap_notes
            ) VALUES(1,?,?,?,?,?,?,?)
            """,
            (name, query, int(complete), len(rows), times[0] if times else None, times[-1] if times else None, gap),
        )


def insert_channels(conn: sqlite3.Connection, messages: Iterable[dict[str, Any]]) -> None:
    channels: dict[str, dict[str, Any]] = {
        PRIMARY_CHANNEL_ID: {
            "channel_id": PRIMARY_CHANNEL_ID,
            "name": "premium-journals",
            "kind": "forum",
            "parent_channel_id": None,
            "exact_id_known": 1,
            "notes": "Requested primary forum channel.",
        },
        QUESTIONS_CHANNEL_ID: {
            "channel_id": QUESTIONS_CHANNEL_ID,
            "name": "❓│questions",
            "kind": "text",
            "parent_channel_id": None,
            "exact_id_known": 1,
            "notes": "Premium questions channel used for targeted Q&A context.",
        },
        PREMIUM_CHAT_CHANNEL_ID: {
            "channel_id": PREMIUM_CHAT_CHANNEL_ID,
            "name": "📍│chat",
            "kind": "text",
            "parent_channel_id": None,
            "exact_id_known": 1,
            "notes": "Premium chat channel.",
        },
    }
    for message in messages:
        info = resolve_container(message)
        channel_id = str(info["channel_id"])
        channels.setdefault(
            channel_id,
            {
                "channel_id": channel_id,
                "name": info["thread_title"] or info["channel_name"] or "unknown",
                "kind": info["container_kind"],
                "parent_channel_id": info["parent_channel_id"],
                "exact_id_known": int(not channel_id.startswith(("thread-unknown:", "channel-unknown:", "container-unknown:"))),
                "notes": "Exact Discord container ID inferred from attachment metadata."
                if not channel_id.startswith(("thread-unknown:", "channel-unknown:", "container-unknown:"))
                else "Surrogate container ID; no exact channel/thread ID was exposed in the captured result.",
            },
        )
    for row in channels.values():
        conn.execute(
            "INSERT OR IGNORE INTO channels(channel_id,guild_id,name,kind,parent_channel_id,exact_id_known,notes) VALUES(?,?,?,?,?,?,?)",
            (row["channel_id"], GUILD_ID, row["name"], row["kind"], row["parent_channel_id"], row["exact_id_known"], row["notes"]),
        )


def insert_messages(
    conn: sqlite3.Connection,
    messages: dict[str, dict[str, Any]],
    sources: dict[str, list[dict[str, Any]]],
    force_include_ids: set[str] | None = None,
) -> tuple[set[str], dict[str, int]]:
    included_ids: set[str] = set()
    exclusion_counts: collections.Counter[str] = collections.Counter()
    force_include_ids = force_include_ids or set()

    for message_id, row in sorted(messages.items(), key=lambda item: item[1].get("timestamp_utc") or ""):
        score, relevance, exclusion_reason = relevance_score(row)
        source_names = {item["collection_name"] for item in sources.get(message_id, [])}
        if message_id in force_include_ids and relevance == "excluded":
            score, relevance, exclusion_reason = 6, "core", None
        if relevance == "excluded":
            for source_name in source_names or {"unknown"}:
                exclusion_counts[f"{source_name}|{exclusion_reason}"] += 1
            continue

        info = resolve_container(row)
        content = row.get("content_text") or ""
        timestamp = row.get("timestamp_utc") or "1970-01-01T00:00:00Z"
        permalink = row.get("inferred_permalink")
        if not permalink and re.fullmatch(r"\d{15,22}", str(info["channel_id"])):
            permalink = f"https://discord.com/channels/{GUILD_ID}/{info['channel_id']}/{message_id}"
        if permalink:
            permalink_confidence = "exact" if info["channel_id"] in {QUESTIONS_CHANNEL_ID, PREMIUM_CHAT_CHANNEL_ID} else "inferred"
        else:
            permalink_confidence = "unavailable"

        conn.execute(
            """
            INSERT INTO messages(
              message_id,channel_id,parent_channel_id,channel_name,thread_title,
              author_display_name,author_inferred,created_at_utc,displayed_time,edited,
              is_original_poster,reply_to_message_id,reply_to_content,content_text,visible_text,
              content_sha256,permalink,permalink_confidence,relevance,relevance_score,source_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                message_id,
                info["channel_id"],
                info["parent_channel_id"],
                info["channel_name"],
                info["thread_title"],
                row.get("author") or None,
                int(bool(row.get("author_inferred_from_continuation"))),
                timestamp,
                row.get("displayed_time"),
                int(bool(row.get("edited"))),
                int(bool(row.get("is_op"))),
                row.get("reply_to_message_id"),
                row.get("reply_to_content"),
                content,
                row.get("visible_text"),
                hashlib.sha256(content.encode("utf-8")).hexdigest(),
                permalink,
                permalink_confidence,
                relevance,
                score,
                compact_json(row),
            ),
        )
        included_ids.add(message_id)

        for source in sources.get(message_id, []):
            conn.execute(
                "INSERT OR IGNORE INTO message_sources(message_id,collection_name,query_text,result_index,page_number) VALUES(?,?,?,?,?)",
                (message_id, source["collection_name"], source.get("query") or "", source.get("result_index"), source.get("page_number")),
            )

        for attachment in row.get("attachments") or []:
            attachment_id = str(attachment.get("attachment_id") or "").strip()
            if not attachment_id:
                attachment_id = hashlib.sha1((message_id + "|" + str(attachment.get("url"))).encode("utf-8")).hexdigest()
            filename = attachment.get("filename")
            suffix = Path(filename or "").suffix.lower()
            media_kind = "image" if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else "file"
            conn.execute(
                """
                INSERT OR IGNORE INTO attachments(
                  attachment_id,message_id,filename,discord_url,source_channel_id,
                  media_kind,extracted_text,extraction_status,notes
                ) VALUES(?,?,?,?,?,?,NULL,'not_attempted',?)
                """,
                (
                    attachment_id,
                    message_id,
                    filename,
                    attachment.get("url"),
                    attachment.get("thread_channel_id"),
                    media_kind,
                    "Chart/media retained as metadata only; no OCR or chart interpretation was performed.",
                ),
            )

        text = normalized_text(row)
        for tag in classify_message(text):
            conn.execute("INSERT OR IGNORE INTO message_tags(message_id,tag) VALUES(?,?)", (message_id, tag))
        for instrument in extract_instruments(text):
            conn.execute("INSERT OR IGNORE INTO message_instruments(message_id,instrument) VALUES(?,?)", (message_id, instrument))
        for outcome, count in outcome_mentions(text).items():
            if count:
                conn.execute(
                    "INSERT OR REPLACE INTO outcome_mentions(message_id,outcome,stated_count,extraction_method,confidence) VALUES(?,?,?,'regex_explicit_text',0.70)",
                    (message_id, outcome, count),
                )

        conn.execute(
            "INSERT INTO messages_fts(message_id,author_display_name,channel_name,thread_title,content_text,reply_to_content) VALUES(?,?,?,?,?,?)",
            (message_id, row.get("author") or "", info["channel_name"], info["thread_title"] or "", content, row.get("reply_to_content") or ""),
        )

    for marker, count in sorted(exclusion_counts.items()):
        collection_name, reason = marker.split("|", 1)
        conn.execute(
            "INSERT INTO exclusion_stats(run_id,collection_name,reason,excluded_count) VALUES(1,?,?,?)",
            (collection_name, reason, count),
        )
    return included_ids, dict(exclusion_counts)


def insert_confluences(conn: sqlite3.Connection, messages: dict[str, dict[str, Any]], included_ids: set[str]) -> dict[str, int]:
    confluence_ids: dict[str, int] = {}
    for name, category, _ in CONFLUENCE_PATTERNS:
        cursor = conn.execute(
            "INSERT INTO confluences(canonical_name,category,corpus_definition,caveat) VALUES(?,?,?,?)",
            (
                name,
                category,
                "Normalized label for the channel's own terminology; see linked message evidence.",
                "Term matching shows co-occurrence, not causality or mandatory use.",
            ),
        )
        confluence_ids[name] = int(cursor.lastrowid)
    for message_id in included_ids:
        text = normalized_text(messages[message_id])
        for name, _category, phrase in extract_confluences(text):
            conn.execute(
                "INSERT OR IGNORE INTO message_confluences(message_id,confluence_id,exact_phrase,attribution) VALUES(?,?,?,'explicit_term_match')",
                (message_id, confluence_ids[name], phrase),
            )
    return confluence_ids


def insert_auto_qa(conn: sqlite3.Connection, messages: dict[str, dict[str, Any]], included_ids: set[str]) -> None:
    answered_question_ids: set[str] = set()
    for answer_id, row in messages.items():
        if answer_id not in included_ids:
            continue
        question_id = row.get("reply_to_message_id")
        question_text = row.get("reply_to_content") or ""
        answer_text = row.get("content_text") or ""
        if not question_id or "?" not in question_text or not answer_text.strip():
            continue
        if not re.search(r"\b(?:rb|rbs|rejection|10\s*am|smt|fvg|nq|es|entry|stop|invalid|valid)\b", question_text, re.I):
            continue
        qid = question_id if question_id in included_ids else None
        conn.execute(
            """
            INSERT INTO qa_pairs(
              question_message_id,answer_message_id,normalized_question,answer_summary,
              status,topic,confidence,notes
            ) VALUES(?,?,?,?, 'ambiguous','trading_rule',0.60,?)
            """,
            (
                qid,
                answer_id,
                question_text.strip(),
                answer_text.strip(),
                "Direct Discord reply captured automatically; whether it fully resolves the question requires curated review.",
            ),
        )
        answered_question_ids.add(question_id)

    for question_id, row in messages.items():
        if question_id not in included_ids or question_id in answered_question_ids:
            continue
        text = row.get("content_text") or ""
        if "?" not in text:
            continue
        if not re.search(r"\b(?:rb|rbs|rejection\s+block|nq|es)\b", text, re.I):
            continue
        conn.execute(
            """
            INSERT INTO qa_pairs(
              question_message_id,answer_message_id,normalized_question,answer_summary,
              status,topic,confidence,notes
            ) VALUES(?,NULL,?,NULL,'ambiguous','rejection_block',0.50,?)
            """,
            (
                question_id,
                text.strip(),
                "No direct reply was captured; this is not proof that the question was unanswered in Discord.",
            ),
        )


def insert_curated(conn: sqlite3.Connection, curated: dict[str, Any], included_ids: set[str], confluence_ids: dict[str, int]) -> None:
    if not curated:
        return

    def require_message_id(value: Any, context: str, *, allow_empty: bool = False) -> str | None:
        message_id = str(value or "").strip()
        if not message_id and allow_empty:
            return None
        if not message_id:
            raise ValueError(f"Missing evidence message ID for {context}.")
        if message_id not in included_ids:
            raise ValueError(f"Evidence message ID {message_id} for {context} is absent from the collected corpus.")
        return message_id

    for finding in curated.get("rejection_block_findings", []) or []:
        cursor = conn.execute(
            """
            INSERT INTO rejection_block_findings(
              facet,finding,evidence_status,confidence,instrument_scope,timeframe_scope,session_scope,caveat
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                finding["facet"],
                finding["finding"],
                finding.get("evidence_status", "derived"),
                float(finding.get("confidence", 0.5)),
                finding.get("instrument_scope"),
                finding.get("timeframe_scope"),
                finding.get("session_scope"),
                finding.get("caveat"),
            ),
        )
        finding_id = int(cursor.lastrowid)
        for evidence in finding.get("evidence", []) or []:
            message_id = require_message_id(evidence.get("message_id"), f"rejection-block finding {finding_id}")
            conn.execute(
                "INSERT INTO rejection_block_finding_evidence(finding_id,message_id,evidence_role,excerpt) VALUES(?,?,?,?)",
                (finding_id, message_id, evidence.get("role", "supports"), evidence.get("excerpt") or ""),
            )

    existing_qa_keys = {
        (row[0] or "", row[1] or "")
        for row in conn.execute("SELECT question_message_id,answer_message_id FROM qa_pairs")
    }
    for qa in curated.get("qa_pairs", []) or []:
        qid = require_message_id(qa.get("question_message_id"), "curated Q&A question", allow_empty=True)
        aid = require_message_id(qa.get("answer_message_id"), "curated Q&A answer", allow_empty=True)
        stored_qid = qid
        stored_aid = aid
        key = (stored_qid or "", stored_aid or "")
        if key in existing_qa_keys:
            conn.execute(
                """
                UPDATE qa_pairs
                SET normalized_question=?, answer_summary=?, status=?, topic=?, confidence=?, notes=?
                WHERE COALESCE(question_message_id,'')=?
                  AND COALESCE(answer_message_id,'')=?
                """,
                (
                    qa["normalized_question"], qa.get("answer_summary"), qa.get("status", "ambiguous"),
                    qa.get("topic", "rejection_block"), float(qa.get("confidence", 0.5)), qa.get("notes"),
                    key[0], key[1],
                ),
            )
            continue
        conn.execute(
            """
            INSERT INTO qa_pairs(
              question_message_id,answer_message_id,normalized_question,answer_summary,
              status,topic,confidence,notes
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                stored_qid,
                stored_aid,
                qa["normalized_question"],
                qa.get("answer_summary"),
                qa.get("status", "ambiguous"),
                qa.get("topic", "rejection_block"),
                float(qa.get("confidence", 0.5)),
                qa.get("notes"),
            ),
        )
        existing_qa_keys.add(key)

    for trade in curated.get("trades", []) or []:
        conn.execute(
            """
            INSERT INTO trades(
              trade_id,trader,trade_date,setup_time_text,post_time_utc,instrument,direction,
              setup_name,timeframe,session_name,outcome,outcome_basis,outcome_confidence,
              entry_text,invalidation_text,stop_text,target_text,management_text,notes
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade["trade_id"], trade.get("trader"), trade.get("trade_date"), trade.get("setup_time_text"),
                trade.get("post_time_utc"), trade.get("instrument"), trade.get("direction", "unknown"),
                trade.get("setup_name"), trade.get("timeframe"), trade.get("session_name"),
                trade.get("outcome", "unknown"), trade.get("outcome_basis", "not_stated"),
                float(trade.get("outcome_confidence", 0.5)), trade.get("entry_text"),
                trade.get("invalidation_text"), trade.get("stop_text"), trade.get("target_text"),
                trade.get("management_text"), trade.get("notes"),
            ),
        )
        for evidence in trade.get("evidence", []) or []:
            message_id = require_message_id(evidence.get("message_id"), f"trade {trade['trade_id']}")
            conn.execute(
                "INSERT INTO trade_evidence(trade_id,message_id,evidence_role,excerpt) VALUES(?,?,?,?)",
                (trade["trade_id"], message_id, evidence.get("role", "setup"), evidence.get("excerpt")),
            )
        for confluence in trade.get("confluences", []) or []:
            name = confluence.get("name") if isinstance(confluence, dict) else str(confluence)
            if name not in confluence_ids:
                raise ValueError(f"Unknown confluence {name!r} on trade {trade['trade_id']}; map it to a canonical confluence.")
            item = confluence if isinstance(confluence, dict) else {}
            evidence_id = require_message_id(
                item.get("evidence_message_id"),
                f"confluence {name!r} on trade {trade['trade_id']}",
                allow_empty=True,
            )
            conn.execute(
                """
                INSERT INTO trade_confluences(
                  trade_id,confluence_id,state,attribution,evidence_message_id,notes
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    trade["trade_id"], confluence_ids[name], item.get("state", "present"),
                    item.get("attribution", "explicit"), evidence_id, item.get("notes"),
                ),
            )

    for profile in curated.get("outcome_profiles", []) or []:
        cursor = conn.execute(
            """
            INSERT INTO outcome_profiles(
              outcome,summary,resolved_trade_count,unknown_trade_count,
              author_concentration,limitations
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                profile["outcome"], profile["summary"], int(profile.get("resolved_trade_count", 0)),
                int(profile.get("unknown_trade_count", 0)), profile.get("author_concentration"),
                profile.get("limitations") or "Corpus-selected journal evidence; not a universal expectancy estimate.",
            ),
        )
        profile_id = int(cursor.lastrowid)
        for item in profile.get("confluences", []) or []:
            name = item.get("name")
            if name not in confluence_ids:
                raise ValueError(f"Unknown confluence {name!r} on outcome profile {profile['outcome']}; map it to a canonical confluence.")
            conn.execute(
                """
                INSERT INTO outcome_profile_confluences(
                  profile_id,confluence_id,role,observed_count,observed_share,rationale
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    profile_id, confluence_ids[name], item.get("role", "common"),
                    item.get("observed_count"), item.get("observed_share"), item.get("rationale"),
                ),
            )

    for tier in curated.get("probability_tiers", []) or []:
        conn.execute(
            """
            INSERT INTO probability_tiers(
              label,rank_order,basis,definition,resolved_count,wins,losses,breakevens,
              unknowns,observed_win_rate,limitations
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tier["label"], int(tier["rank_order"]), tier.get("basis", "synthesis"),
                tier["definition"], tier.get("resolved_count"), tier.get("wins"), tier.get("losses"),
                tier.get("breakevens"), tier.get("unknowns"), tier.get("observed_win_rate"),
                tier.get("limitations") or "Qualitative corpus tier; not a market-wide probability.",
            ),
        )

    for model in curated.get("models", []) or []:
        cursor = conn.execute(
            """
            INSERT INTO trading_models(
              model_no,name,evidence_status,thesis,eligibility_context,identification,
              trigger_confirmation,invalidation,entry,stop,target,management,
              instrument_scope,timeframe_scope,session_scope,win_count,loss_count,
              breakeven_count,unknown_count,limitations
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(model["model_no"]), model["name"], model.get("evidence_status", "provisional_derived"),
                model["thesis"], model.get("eligibility_context"), model.get("identification"),
                model.get("trigger_confirmation"), model.get("invalidation"), model.get("entry"),
                model.get("stop"), model.get("target"), model.get("management"),
                model.get("instrument_scope"), model.get("timeframe_scope"), model.get("session_scope"),
                model.get("win_count"), model.get("loss_count"), model.get("breakeven_count"),
                model.get("unknown_count"), model.get("limitations") or "Corpus-derived; validate prospectively.",
            ),
        )
        model_id = int(cursor.lastrowid)
        for order, rule in enumerate(model.get("rules", []) or [], start=1):
            conn.execute(
                "INSERT INTO model_rules(model_id,rule_order,rule_type,rule_text,required) VALUES(?,?,?,?,?)",
                (model_id, order, rule.get("type", "context"), rule["text"], int(rule.get("required", True))),
            )
        for evidence in model.get("evidence", []) or []:
            message_id = require_message_id(evidence.get("message_id"), f"model {model['model_no']}")
            conn.execute(
                "INSERT INTO model_evidence(model_id,message_id,evidence_role,excerpt) VALUES(?,?,?,?)",
                (model_id, message_id, evidence.get("role", "supports"), evidence.get("excerpt")),
            )

    for question in curated.get("research_questions", []) or []:
        evidence_message_ids = [
            require_message_id(message_id, f"research question {question['question_text']!r}")
            for message_id in question.get("evidence_message_ids", [])
        ]
        conn.execute(
            """
            INSERT INTO research_questions(
              question_text,answer_status,answer_summary,limitations,evidence_message_ids_json
            ) VALUES(?,?,?,?,?)
            """,
            (
                question["question_text"], question.get("answer_status", "partial"),
                question["answer_summary"], question.get("limitations"),
                compact_json(evidence_message_ids),
            ),
        )

    for contradiction in curated.get("contradictions", []) or []:
        a = require_message_id(contradiction.get("message_id_a"), f"contradiction {contradiction['topic']!r} side A", allow_empty=True)
        b = require_message_id(contradiction.get("message_id_b"), f"contradiction {contradiction['topic']!r} side B", allow_empty=True)
        conn.execute(
            """
            INSERT INTO contradictions(
              topic,description,message_id_a,message_id_b,resolution_status,notes
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                contradiction["topic"], contradiction["description"],
                a, b,
                contradiction.get("resolution_status", "unresolved"), contradiction.get("notes"),
            ),
        )


def insert_analysis_documents(conn: sqlite3.Connection, base_dir: Path) -> None:
    json_files = {
        "rb_analysis": "rb_analysis.json",
        "trade_analysis": "trade_analysis.json",
        "model_analysis": "model_analysis.json",
        "curated_analysis": "curated_analysis.json",
    }
    for name, filename in json_files.items():
        path = base_dir / filename
        if not path.exists():
            continue
        content = json.loads(path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO analysis_documents(document_name,created_by,content_json,notes) VALUES(?,?,?,?)",
            (name, "corpus_only_analysis", compact_json(content), f"Imported from {filename}."),
        )

    markdown_files = {
        "research_summary_markdown": "RESEARCH_SUMMARY.md",
        "llm_readme_markdown": "README_FOR_LLM.md",
    }
    for name, filename in markdown_files.items():
        path = base_dir / filename
        if not path.exists():
            continue
        wrapped = {"format": "markdown", "content": path.read_text(encoding="utf-8")}
        conn.execute(
            "INSERT INTO analysis_documents(document_name,created_by,content_json,notes) VALUES(?,?,?,?)",
            (name, "corpus_only_analysis", compact_json(wrapped), f"Imported from {filename}."),
        )


def insert_data_dictionary(conn: sqlite3.Connection) -> None:
    descriptions = {
        ("research_runs", "status"): "Overall collection status. Partial reflects the known broad server-wide RB shorthand gap even though the requested premium-journals scan is complete.",
        ("collection_coverage", "messages_seen"): "Messages returned by that source collection before cross-collection deduplication or relevance filtering.",
        ("exclusion_stats", "excluded_count"): "Source-association count by exclusion reason; do not sum it as a distinct-message total across collections.",
        ("messages", "content_text"): "Curated Discord-visible message text; excluded chatter is not stored here.",
        ("messages", "relevance"): "Core or supporting classification from deterministic trading-term rules.",
        ("messages", "permalink_confidence"): "Whether the Discord permalink is exact, inferred from attachment channel metadata, or unavailable.",
        ("message_sources", "collection_name"): "The browser collection or supplemental search that exposed the message.",
        ("outcome_mentions", "stated_count"): "Explicit text-level outcome count; not automatically treated as a distinct trade episode.",
        ("qa_pairs", "status"): "Curated resolution state. Automatically linked replies remain ambiguous until reviewed.",
        ("trades", "outcome"): "Curated trade-episode outcome using explicit or linked evidence; unknown when unresolved.",
        ("trade_confluences", "attribution"): "Explicitly stated versus curated inference from linked episode evidence.",
        ("rejection_block_findings", "evidence_status"): "Explicit source rule, observed association, analyst-derived synthesis, or insufficient evidence.",
        ("probability_tiers", "observed_win_rate"): "Corpus-only rate where available; never a universal market probability.",
        ("research_questions", "answer_status"): "Whether the requested question was answered, partially answered, or lacked enough evidence.",
    }
    for (table, column), description in descriptions.items():
        conn.execute(
            "INSERT INTO data_dictionary(table_name,column_name,description) VALUES(?,?,?)",
            (table, column, description),
        )


def write_meta(conn: sqlite3.Connection, raw_path: Path, output_path: Path) -> None:
    rows = {
        "schema_version": SCHEMA_VERSION,
        "source_scope": "Discord only",
        "raw_export": str(raw_path),
        "database_file": str(output_path),
        "build_timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "primary_channel_complete": "true",
        "requested_days": "14",
        "important_note": "Models and probability tiers describe this selected Discord corpus, not universal market expectancy.",
    }
    conn.executemany("INSERT INTO meta(key,value) VALUES(?,?)", rows.items())


def build_database(raw_path: Path, curated_path: Path, output_path: Path) -> dict[str, Any]:
    raw = parse_json(raw_path, {})
    curated = parse_json(curated_path, {})
    messages, sources = collect_messages(raw)
    infer_author_continuations(messages)
    propagate_unique_thread_channel_ids(messages)
    force_include_ids = curated_message_ids(curated)

    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    if temp_path.exists():
        temp_path.unlink()
    conn = sqlite3.connect(temp_path)
    conn.row_factory = sqlite3.Row
    try:
        create_schema(conn)
        insert_run_and_coverage(conn, raw)
        insert_channels(conn, messages.values())
        included_ids, exclusion_counts = insert_messages(conn, messages, sources, force_include_ids)
        confluence_ids = insert_confluences(conn, messages, included_ids)
        insert_auto_qa(conn, messages, included_ids)
        insert_curated(conn, curated, included_ids, confluence_ids)
        insert_analysis_documents(conn, raw_path.parent)
        insert_data_dictionary(conn)
        write_meta(conn, raw_path, output_path)
        conn.commit()

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise RuntimeError(f"Database validation failed: integrity={integrity}, foreign_keys={foreign_keys}")

        stats = {
            "source_unique_messages": len(messages),
            "included_messages": conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "excluded_unique_messages": len(messages) - len(included_ids),
            "attachments": conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0],
            "qa_pairs": conn.execute("SELECT COUNT(*) FROM qa_pairs").fetchone()[0],
            "rb_findings": conn.execute("SELECT COUNT(*) FROM rejection_block_findings").fetchone()[0],
            "trades": conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
            "models": conn.execute("SELECT COUNT(*) FROM trading_models").fetchone()[0],
            "integrity_check": integrity,
            "foreign_key_violations": len(foreign_keys),
            "exclusion_buckets": exclusion_counts,
        }
    finally:
        conn.close()

    os.replace(temp_path, output_path)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Discord trading research SQLite database.")
    parser.add_argument("--raw", type=Path, default=Path(__file__).with_name("raw_discord_export.json"))
    parser.add_argument("--curated", type=Path, default=Path(__file__).with_name("curated_analysis.json"))
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("discord_trading_research.sqlite"))
    args = parser.parse_args()

    stats = build_database(args.raw.resolve(), args.curated.resolve(), args.output.resolve())
    print(json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
