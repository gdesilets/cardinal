#!/usr/bin/env python3
"""Populate the Cardinal v2 analysis schema from a local Discord corpus.

This is a local-only post-processing stage for ``build_cardinal_database_v2.py``.
It copies the raw Cardinal database to a new file, then adds evidence-backed
rejection-block findings, linked Q&A, conservative trade episodes, descriptive
win/loss and confluence profiles, executed-instrument comparisons, and up to
five Discord-supported model cards.

No web, market data, Cardinal defaults, or outside trading definitions are used.
The legacy three-month scripts/artifacts are read-only Discord-derived inputs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
RESEARCH_ROOT = SCRIPT_DIR.parent
DEFAULT_CURATED = RESEARCH_ROOT / "curated_analysis_3month.json"
DEFAULT_MODEL_ANALYSIS = RESEARCH_ROOT / "model_analysis_3month.json"
DEFAULT_FOLLOWUPS = RESEARCH_ROOT / "browser_context_followups_3month.json"
DEFAULT_TRADE_SCRIPT = RESEARCH_ROOT / "build_trade_analysis_3month.py"
DEFAULT_RB_SCRIPT = RESEARCH_ROOT / "build_rb_analysis_3month.py"
DEFAULT_MODEL_SCRIPT = RESEARCH_ROOT / "build_model_analysis_3month.py"

SCHEMA_VERSION = "1.1.0"
METHOD = "discord_local_evidence_analysis_v2_full_window_model_discovery"
SOURCE_SCOPE = "discord_only"
SPACE_RE = re.compile(r"\s+")
QUESTION_PREFIX_RE = re.compile(
    r"^(?:@\S+\s+){0,4}(?:can|could|do|does|did|is|are|would|should|"
    r"must|how|what|when|where|why|which)\b",
    re.IGNORECASE,
)
RELEVANT_Q_RE = re.compile(
    r"\b(?:rbs?|rejection\s+blocks?|trade|entry|stop|target|bias|"
    r"liquidity|sweep|fvg|fair\s+value\s+gap|smt|ssmt|ote|fib|"
    r"order\s+block|breaker|cisd|nq|mnq|es|mes|10\s*(?:am|:00)|"
    r"market\s+open|session|confluence|invalidation|invalid)\b",
    re.IGNORECASE,
)
INSTRUMENT_FAMILY = {"NQ": "NQ", "MNQ": "NQ", "ES": "ES", "MES": "ES"}
OUTCOME_MAP = {
    "win": "win",
    "loss": "loss",
    "breakeven": "breakeven",
    "mixed": "mixed_partial",
    "cancelled": "cancelled_no_trade",
    "open": "open",
    "unknown": "unknown",
}
MODEL_LIMIT = 5
NOVEL_MIN_STRICT_EPISODES = 5
NOVEL_MIN_DISTINCT_AUTHORS = 3
NOVEL_MAX_TOP_AUTHOR_SHARE = 0.60
NOVEL_MIN_OPERATIONAL_MESSAGES = 2
NOVEL_MIN_OPERATIONAL_AUTHORS = 2
NOVEL_MIN_DISTINCT_DATES = 3
NEAR_DUPLICATE_JACCARD = 0.80
SUBSET_DUPLICATE_JACCARD = 0.70
OPERATIONAL_LANGUAGE_RE = re.compile(
    r"\b(?:setup|model|strategy|playbook|rules?|criteria|entry|enter(?:ed|ing)?|"
    r"confirmation|confirm(?:ed|ation)?|wait\s+for|must|need(?:ed)?\s+to|require[ds]?|"
    r"valid|invalid|invalidation|stop|sl|target|take\s+profit|tp|no\s+trade|skip|avoid)\b",
    re.IGNORECASE,
)
PRESCRIPTIVE_LANGUAGE_RE = re.compile(
    r"\b(?:must|need(?:ed)?\s+to|require[ds]?|only\s+(?:enter|take|use)|"
    r"wait\s+for|do\s+not|don't|should(?:n't|\s+not)?|avoid|skip|no\s+trade|"
    r"entry\s+(?:is|at|on|after|when)|enter\s+(?:at|on|after|when|once)|"
    r"stop\s+(?:is|at|above|below|beyond)|sl\s+(?:is|at|above|below|beyond)|"
    r"target\s+(?:is|at|the)|tp\s+(?:is|at)|invalid(?:ates?|ation|\s+if)|"
    r"valid\s+(?:only|if|when)|criteria|rules?)\b",
    re.IGNORECASE,
)
NO_TRADE_LANGUAGE_RE = re.compile(
    r"\b(?:no\s+trade|skip|avoid|do\s+not|don't|shouldn't|not\s+valid|"
    r"low\s+probability|mitigated|against\s+(?:the|my)?\s*bias)\b",
    re.IGNORECASE,
)
INVALIDATION_LANGUAGE_RE = re.compile(
    r"\b(?:invalid|invalidation|stop|\bsl\b|fails?|breaks?)\b",
    re.IGNORECASE,
)
ENTRY_LANGUAGE_RE = re.compile(
    r"\b(?:entry|enter(?:ed|ing)?|limit|confirmation|confirm(?:ed|ation)?|"
    r"trigger|wait\s+for)\b",
    re.IGNORECASE,
)
TARGET_LANGUAGE_RE = re.compile(
    r"\b(?:target|take\s+profit|\btp\b|draw\s+on\s+liquidity|\bdol\b)\b",
    re.IGNORECASE,
)
MANAGEMENT_LANGUAGE_RE = re.compile(
    r"\b(?:partial|breakeven|break\s+even|trail|scale|runner|management)\b",
    re.IGNORECASE,
)
NAMED_SETUP_RE = re.compile(
    r"\b([a-z0-9][a-z0-9/&+\- ]{1,48}?)\s+(?:setup|model|strategy|playbook)\b",
    re.IGNORECASE,
)


class AnalysisError(RuntimeError):
    """Raised when source discipline or database compatibility fails."""


def normalize(value: Any) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join("" if part is None else str(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AnalysisError(f"Expected JSON object: {path}")
    return value


def load_local_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AnalysisError(f"Cannot load local module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def assert_cardinal_v2(con: sqlite3.Connection) -> None:
    required = {
        "messages",
        "analysis_runs",
        "analysis_entities",
        "evidence_items",
        "claims",
        "claim_evidence",
        "setup_instances",
        "trade_episodes",
        "questions",
        "setup_models",
        "analysis_documents",
    }
    tables = {
        row[0]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = sorted(required - tables)
    if missing:
        raise AnalysisError(f"Not a compatible Cardinal v2 database; missing: {missing}")
    source_scope = con.execute(
        "SELECT value FROM meta WHERE key='source_scope'"
    ).fetchone()
    outside = con.execute(
        "SELECT value FROM meta WHERE key='outside_sources_used'"
    ).fetchone()
    if not source_scope or source_scope[0] != "discord_only" or not outside or str(outside[0]) != "0":
        raise AnalysisError("Database does not satisfy Discord-only source discipline")
    existing = con.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0]
    if existing:
        raise AnalysisError(
            "Analysis tables are not empty. Rebuild the raw v2 database before rerunning."
        )


def copy_database(source: Path, destination: Path, *, replace: bool) -> Path:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise AnalysisError("Input and output database must be different files")
    if destination.exists() and not replace:
        raise FileExistsError(f"Output exists: {destination}; use --replace explicitly")
    destination.parent.mkdir(parents=True, exist_ok=True)
    building = destination.with_suffix(destination.suffix + ".building")
    if building.exists():
        building.unlink()
    source_con = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    target_con = sqlite3.connect(building)
    try:
        source_con.backup(target_con)
        target_con.commit()
    finally:
        target_con.close()
        source_con.close()
    return building


def message_rows(con: sqlite3.Connection) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    run = con.execute(
        "SELECT window_start_utc,window_end_utc FROM collection_runs ORDER BY run_id LIMIT 1"
    ).fetchone()
    if not run:
        raise AnalysisError("Missing collection run")
    start, end = run
    attachment_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in con.execute(
        """
        SELECT attachment_id,message_id,filename,discord_url,media_kind
        FROM attachments
        WHERE ownership_status='owned_exact'
          AND eligible_for_attachment_evidence=1
        """
    ):
        attachment_map[str(row[1])].append(
            {
                "attachment_id": row[0],
                "filename": row[2],
                "url": row[3],
                "media_kind": row[4],
            }
        )
    rows: list[dict[str, Any]] = []
    lookup: dict[str, dict[str, Any]] = {}
    sql = """
        SELECT m.message_id,m.author_display_name,m.thread_title,m.channel_name,m.channel_id,
               m.parent_channel_id,m.created_at_utc,m.content_text,m.visible_text,m.permalink,
               m.reply_to_message_id,m.reply_to_content,m.reply_target_state,m.author_id,
               a.user_id_exact,a.identity_resolution
        FROM v_analysis_eligible_messages AS m
        LEFT JOIN authors AS a ON a.author_id=m.author_id
        WHERE m.created_at_utc>=? AND m.created_at_utc<?
        ORDER BY m.created_at_utc,m.message_id
    """
    try:
        eligible_rows = con.execute(sql, (start, end))
    except sqlite3.OperationalError as exc:
        raise AnalysisError(
            "Database does not expose v_analysis_eligible_messages; rebuild it with "
            "the trust-aware Cardinal database builder before running analysis."
        ) from exc
    for item in eligible_rows:
        row = {
            "message_id": str(item[0]),
            "author": normalize(item[1]) or "unknown",
            "thread_title": normalize(item[2]) or normalize(item[3]) or "unknown",
            "group_label": normalize(item[3]),
            "parent_channel": normalize(item[3]),
            "inferred_thread_channel_id": str(item[4] or ""),
            "parent_channel_id": str(item[5] or ""),
            "timestamp_utc": str(item[6] or ""),
            "content_text": str(item[7] or ""),
            "visible_text": str(item[8] or ""),
            "inferred_permalink": str(item[9] or ""),
            "reply_to_message_id": str(item[10]) if item[10] else None,
            "reply_to_content": str(item[11] or ""),
            "reply_target_state": str(item[12] or ""),
            "author_id": str(item[13]) if item[13] else None,
            "author_id_exact": bool(item[14]) if item[14] is not None else None,
            "author_identity_resolution": str(item[15]) if item[15] else None,
            "attachments": attachment_map.get(str(item[0]), []),
        }
        rows.append(row)
        lookup[row["message_id"]] = row
    return rows, lookup


def coverage_snapshot(con: sqlite3.Connection) -> dict[str, Any]:
    run = con.execute(
        "SELECT run_id,status,window_start_utc,window_end_utc,limitations FROM collection_runs ORDER BY run_id LIMIT 1"
    ).fetchone()
    gaps = []
    try:
        gaps = [dict(row) for row in con.execute("SELECT * FROM v_collection_gaps")]
    except sqlite3.Error:
        pass
    units = dict(
        con.execute(
            "SELECT status,COUNT(*) FROM collection_units GROUP BY status"
        ).fetchall()
    )
    return {
        "collection_run_status": run[1],
        "window_start_utc": run[2],
        "window_end_utc": run[3],
        "collection_limitations": run[4],
        "collection_unit_status_counts": units,
        "gap_count": len(gaps),
        "gap_sample": gaps[:100],
        "analysis_completeness": "complete" if run[1] == "complete" and not gaps else "partial",
        "interpretation": (
            "All analysis is conditional on captured corpus coverage. Unanswered questions, "
            "frequency counts, and selected-corpus shares are not server-wide conclusions when partial."
        ),
    }


class Writer:
    def __init__(
        self,
        con: sqlite3.Connection,
        analysis_run_id: int,
        messages: dict[str, dict[str, Any]],
        coverage: dict[str, Any],
    ) -> None:
        self.con = con
        self.run_id = analysis_run_id
        self.messages = messages
        self.coverage = coverage
        self.evidence_cache: dict[str, str] = {}
        self.term_cache: dict[str, str] = {}
        self.instrument_cache: dict[str, str] = {}
        self.timeframe_cache: dict[str, str] = {}
        self.session_cache: dict[str, str] = {}

    def entity(
        self,
        entity_id: str,
        entity_type: str,
        *,
        parent: str | None = None,
        root: str | None = None,
        notes: str = "",
    ) -> None:
        self.con.execute(
            """
            INSERT OR IGNORE INTO analysis_entities(
              entity_id,entity_type,created_analysis_run_id,parent_entity_id,
              root_entity_id,lifecycle_status,source_scope,outside_sources_used,notes
            ) VALUES(?,?,?, ?,?,'active','discord_only',0,?)
            """,
            (entity_id, entity_type, self.run_id, parent, root, notes),
        )

    def evidence(self, message_id: str, *, source_type: str = "message_text") -> str | None:
        message_id = str(message_id or "")
        row = self.messages.get(message_id)
        if not row:
            return None
        key = f"{source_type}:{message_id}"
        if key in self.evidence_cache:
            return self.evidence_cache[key]
        text = str(row.get("content_text") or row.get("visible_text") or "")
        if source_type == "reply_context":
            text = str(row.get("reply_to_content") or "")
        excerpt = text[:4000]
        evidence_id = stable_id("evidence", self.run_id, source_type, message_id)
        self.con.execute(
            """
            INSERT INTO evidence_items(
              evidence_id,analysis_run_id,message_id,attachment_id,source_type,
              exact_excerpt,char_start,char_end,locator_json,content_sha256,
              extraction_method,extraction_confidence,source_scope,outside_sources_used
            ) VALUES(?,?,?,NULL,?,?,0,?,?,?,?,?,'discord_only',0)
            """,
            (
                evidence_id,
                self.run_id,
                message_id,
                source_type,
                excerpt,
                len(excerpt),
                json_text({"message_id": message_id, "field": "content_text"}),
                sha256_text(excerpt),
                "exact_database_text",
                1.0,
            ),
        )
        self.evidence_cache[key] = evidence_id
        return evidence_id

    def claim(
        self,
        subject_id: str,
        facet: str,
        text: str,
        *,
        claim_kind: str,
        epistemic_status: str,
        resolution_status: str,
        evidence_message_ids: Iterable[str] = (),
        evidence_role: str = "supports",
        normalized: Any = None,
        speaker_author_id: str | None = None,
        authority_assignment_id: str | None = None,
        limitations: str = "",
        confidence: float | None = None,
        confidence_dimension: str = "extraction",
        sample_size: int | None = None,
    ) -> str:
        evidence_ids = [
            evidence_id
            for message_id in dict.fromkeys(str(v) for v in evidence_message_ids if v)
            if (evidence_id := self.evidence(message_id)) is not None
        ]
        if resolution_status == "accepted" and not evidence_ids:
            resolution_status = "unresolved"
            limitations = (limitations + " No captured evidence message resolved.").strip()
        claim_id = stable_id("claim", self.run_id, subject_id, facet, text)
        self.con.execute(
            """
            INSERT OR IGNORE INTO claims(
              claim_id,subject_entity_id,facet,claim_text,normalized_value_json,
              claim_kind,epistemic_status,resolution_status,speaker_author_id,
              authority_assignment_id,analysis_run_id,source_scope,outside_sources_used,
              created_at_utc,limitations
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'discord_only',0,?,?)
            """,
            (
                claim_id,
                subject_id,
                facet,
                text,
                json_text(normalized) if normalized is not None else None,
                claim_kind,
                epistemic_status,
                resolution_status,
                speaker_author_id,
                authority_assignment_id,
                self.run_id,
                utc_now(),
                limitations,
            ),
        )
        for evidence_id in evidence_ids:
            self.con.execute(
                "INSERT OR IGNORE INTO claim_evidence(claim_id,evidence_id,evidence_role) VALUES(?,?,?)",
                (claim_id, evidence_id, evidence_role),
            )
        if confidence is not None:
            assessment_id = stable_id("confidence", claim_id, confidence_dimension)
            band = "high" if confidence >= 0.85 else "medium" if confidence >= 0.65 else "low"
            self.con.execute(
                """
                INSERT OR IGNORE INTO confidence_assessments(
                  assessment_id,claim_id,entity_id,dimension,score,band,basis_text,
                  assessor_method,sample_size,caveat,analysis_run_id
                ) VALUES(?, ?,NULL,?,?,?,?,?,?,?,?)
                """,
                (
                    assessment_id,
                    claim_id,
                    confidence_dimension,
                    max(0.0, min(1.0, confidence)),
                    band,
                    "Deterministic Discord-text extraction/linkage confidence; not trade probability.",
                    METHOD,
                    sample_size,
                    "Corpus coverage and self-reporting limitations remain.",
                    self.run_id,
                ),
            )
        return claim_id

    def term(self, raw_name: str, evidence_message_id: str) -> str:
        canonical = normalize(raw_name).lower().replace(" ", "_") or "unknown"
        if canonical in self.term_cache:
            return self.term_cache[canonical]
        term_id = stable_id("term", canonical)
        self.entity(term_id, "concept_term", notes="Name normalized only from captured Discord text.")
        name_claim = self.claim(
            term_id,
            "term_name",
            f"Captured Discord term: {canonical}",
            claim_kind="explicit_example",
            epistemic_status="explicit_source",
            resolution_status="qualified",
            evidence_message_ids=[evidence_message_id],
            normalized={"canonical_name": canonical},
        )
        self.con.execute(
            """
            INSERT INTO concept_terms(
              term_id,canonical_name,term_class,discord_definition,definition_status,
              name_claim_id,definition_claim_id,limitations
            ) VALUES(?,?, 'observed_confluence',NULL,'unknown',?,NULL,?)
            """,
            (
                term_id,
                canonical,
                name_claim,
                "Canonical token is a normalization aid, not an outside definition.",
            ),
        )
        self.term_cache[canonical] = term_id
        return term_id

    def instrument(self, symbol: str, evidence_message_id: str) -> str:
        symbol = normalize(symbol).upper()
        if symbol in self.instrument_cache:
            return self.instrument_cache[symbol]
        instrument_id = stable_id("instrument", symbol)
        self.entity(instrument_id, "instrument")
        claim_id = self.claim(
            instrument_id,
            "instrument_symbol",
            f"Instrument symbol explicitly extracted as {symbol}",
            claim_kind="explicit_example",
            epistemic_status="explicit_source",
            resolution_status="qualified",
            evidence_message_ids=[evidence_message_id],
        )
        self.con.execute(
            "INSERT INTO instruments(instrument_id,canonical_symbol,asset_class_as_stated,name_claim_id) VALUES(?,?,NULL,?)",
            (instrument_id, symbol, claim_id),
        )
        self.instrument_cache[symbol] = instrument_id
        return instrument_id

    def timeframe(self, token: str, evidence_message_id: str) -> str:
        token = normalize(token).lower()
        if token in self.timeframe_cache:
            return self.timeframe_cache[token]
        timeframe_id = stable_id("timeframe", token)
        self.entity(timeframe_id, "timeframe")
        claim_id = self.claim(
            timeframe_id,
            "timeframe_token",
            f"Timeframe explicitly extracted as {token}",
            claim_kind="explicit_example",
            epistemic_status="explicit_source",
            resolution_status="qualified",
            evidence_message_ids=[evidence_message_id],
        )
        duration: int | None = None
        match = re.fullmatch(r"(\d+)([smh])", token)
        if match:
            multiplier = {"s": 1, "m": 60, "h": 3600}[match.group(2)]
            duration = int(match.group(1)) * multiplier
        self.con.execute(
            "INSERT INTO timeframes(timeframe_id,canonical_token,duration_seconds,normalization_status,name_claim_id) VALUES(?,?,?,?,?)",
            (timeframe_id, token, duration, "explicit", claim_id),
        )
        self.timeframe_cache[token] = timeframe_id
        return timeframe_id

    def session(
        self,
        label: str,
        evidence_message_id: str,
        *,
        definition_as_stated: str | None = None,
    ) -> str:
        """Store only a session label that was explicitly captured in Discord text."""

        canonical = normalize(label) or "unknown"
        cache_key = canonical.casefold()
        if cache_key in self.session_cache:
            return self.session_cache[cache_key]
        session_id = stable_id("session", cache_key)
        self.entity(
            session_id,
            "session",
            notes="Session name normalized only from explicit captured Discord text.",
        )
        claim_id = self.claim(
            session_id,
            "session_name",
            f"Session explicitly extracted as {canonical}",
            claim_kind="explicit_example",
            epistemic_status="explicit_source",
            resolution_status="qualified",
            evidence_message_ids=[evidence_message_id],
            normalized={
                "canonical_label": canonical,
                "definition_as_stated": definition_as_stated,
            },
            limitations="No timezone or clock interval is inferred from the session name.",
        )
        self.con.execute(
            """
            INSERT INTO sessions(
              session_id,canonical_label,definition_as_stated,timezone_as_stated,
              name_claim_id,definition_claim_id
            ) VALUES(?,?,?,NULL,?,NULL)
            """,
            (session_id, canonical, definition_as_stated, claim_id),
        )
        self.session_cache[cache_key] = session_id
        return session_id


def create_analysis_run(con: sqlite3.Connection, coverage: dict[str, Any]) -> int:
    run_id = int(con.execute("SELECT COALESCE(MAX(analysis_run_id),0)+1 FROM analysis_runs").fetchone()[0])
    collection_run_id = int(con.execute("SELECT run_id FROM collection_runs ORDER BY run_id LIMIT 1").fetchone()[0])
    con.execute(
        """
        INSERT INTO analysis_runs(
          analysis_run_id,collection_run_id,schema_version,method,script_sha256,
          created_at_utc,source_scope,outside_sources_used,limitations
        ) VALUES(?,?,?,?,?,?,'discord_only',0,?)
        """,
        (
            run_id,
            collection_run_id,
            SCHEMA_VERSION,
            METHOD,
            sha256_file(Path(__file__).resolve()),
            utc_now(),
            (
                "Text-only, self-reported Discord corpus. Chart-only facts are unresolved. "
                f"Corpus analysis completeness={coverage['analysis_completeness']}. "
                "Selected-corpus shares are descriptive, overlapping, and non-causal."
            ),
        ),
    )
    return run_id


def build_trade_episodes(
    legacy_trade: Any,
    rows: list[dict[str, Any]],
    *,
    min_candidate_score: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    zone = legacy_trade.resolve_timezone("America/Chicago")
    messages, dedupe = legacy_trade.dedupe_messages(rows, zone)
    args = SimpleNamespace(
        min_candidate_score=min_candidate_score,
        cluster_gap_minutes=45,
        numbered_follow_minutes=20,
        context_before_minutes=30,
        context_after_minutes=15,
        max_evidence_messages=20,
        audit_sample_size=50,
    )
    episodes, audit = legacy_trade.extract_episodes(messages, args)
    validation = legacy_trade.validate_output(messages, episodes, audit)
    if not validation.get("passed"):
        raise AnalysisError(f"Trade extraction validation failed: {validation.get('errors')}")
    return episodes, {"deduplication": dedupe, "candidate_audit": audit, "validation": validation}


def feature_base(value: str) -> str:
    return normalize(value).split(":", 1)[0].lower().replace(" ", "_")


def episode_feature_bases(episode: dict[str, Any]) -> list[str]:
    """Return canonical confluence families once per trade episode.

    The extractor intentionally preserves detailed tags such as
    ``rejection_block:1m:entry`` and ``rejection_block:5m:context``.  Marginal
    win/loss profiles are episode-grain, so those detail variants must not turn
    one trade into multiple observations of the same confluence family.
    """

    return list(
        dict.fromkeys(
            base
            for raw in episode.get("confluences", []) or []
            if (base := feature_base(str(raw)))
        )
    )


def episode_feature_evidence_ids(
    episode: dict[str, Any], canonical_base: str
) -> list[str]:
    """Resolve direct feature evidence before falling back to episode context."""

    output: list[str] = []
    field_rows = (episode.get("field_evidence") or {}).get("confluences") or {}
    if isinstance(field_rows, dict):
        for raw_feature, rows in field_rows.items():
            if feature_base(str(raw_feature)) != canonical_base:
                continue
            for row in rows if isinstance(rows, list) else []:
                message_id = str(row.get("message_id") or "") if isinstance(row, dict) else ""
                if message_id and message_id not in output:
                    output.append(message_id)
    return output or evidence_ids_for_episode(episode)


def episode_instrument_families(
    episode: dict[str, Any], field: str
) -> list[str]:
    """Return each normalized instrument family at most once per episode/role."""

    return list(
        dict.fromkeys(
            INSTRUMENT_FAMILY.get(symbol, symbol)
            for value in episode.get(field, []) or []
            if (symbol := str(value).upper()) != "UNKNOWN"
        )
    )


def episode_field_evidence_ids(
    episode: dict[str, Any], field_name: str, value: str
) -> list[str]:
    """Return direct extractor evidence IDs for one explicit stored field value."""

    field_map = (episode.get("field_evidence") or {}).get(field_name) or {}
    rows = field_map.get(value) if isinstance(field_map, dict) else None
    return list(
        dict.fromkeys(
            str(row.get("message_id"))
            for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)
            if row.get("message_id")
        )
    )


def episode_instrument_family_evidence_ids(
    episode: dict[str, Any], family: str
) -> list[str]:
    output: list[str] = []
    for raw in episode.get("instrument", []) or []:
        symbol = str(raw).upper()
        if INSTRUMENT_FAMILY.get(symbol, symbol) != family:
            continue
        for message_id in episode_field_evidence_ids(episode, "instrument", str(raw)):
            if message_id not in output:
                output.append(message_id)
        if symbol != str(raw):
            for message_id in episode_field_evidence_ids(episode, "instrument", symbol):
                if message_id not in output:
                    output.append(message_id)
    return output


def evidence_ids_for_episode(episode: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(row.get("message_id"))
            for row in episode.get("evidence", [])
            if row.get("message_id")
        )
    )


def _explicit_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    token = normalize(value).casefold()
    if token in {"1", "true", "yes", "exact"}:
        return True
    if token in {"0", "false", "no", "surrogate"}:
        return False
    return None


def episode_author_identity(episode: dict[str, Any]) -> dict[str, Any]:
    """Resolve a stable profile identity without pretending a name is an exact ID."""

    raw_author_id = normalize(episode.get("author_id"))
    display_name = normalize(
        episode.get("author_display_name") or episode.get("author")
    ) or "unknown"
    explicit_exact = _explicit_bool(episode.get("author_id_exact"))
    inferred_exact = bool(
        raw_author_id.startswith("discord-user:")
        or re.fullmatch(r"\d{15,22}", raw_author_id)
    )
    exact = explicit_exact if explicit_exact is not None else inferred_exact

    if raw_author_id and exact:
        discord_user_id = raw_author_id.removeprefix("discord-user:")
        author_key = f"discord-user:{discord_user_id}"
        identity_kind = "exact_discord_user_id"
        surrogate_key = None
    elif raw_author_id:
        discord_user_id = None
        author_key = raw_author_id
        identity_kind = "database_surrogate_id"
        surrogate_key = raw_author_id
    else:
        discord_user_id = None
        author_key = stable_id("profile-author-display", display_name.casefold())
        identity_kind = "legacy_display_name_surrogate"
        surrogate_key = author_key

    return {
        "author_key": author_key,
        "author_id": author_key,
        "discord_user_id": discord_user_id,
        "author_id_exact": bool(exact),
        "identity_kind": identity_kind,
        "surrogate_key": surrogate_key,
        "display_name": display_name,
    }


def author_concentration(
    episodes: Sequence[dict[str, Any]],
    *,
    top_limit: int = 20,
) -> dict[str, Any]:
    """Return descriptive author clustering metadata for an episode cohort."""

    counts: Counter[str] = Counter()
    labels: dict[str, Counter[str]] = defaultdict(Counter)
    identities: dict[str, dict[str, Any]] = {}
    exact_episode_count = 0
    for episode in episodes:
        identity = episode_author_identity(episode)
        key = str(identity["author_key"])
        counts[key] += 1
        labels[key][str(identity["display_name"])] += 1
        identities[key] = identity
        exact_episode_count += int(bool(identity["author_id_exact"]))

    episode_count = sum(counts.values())
    ordered = sorted(
        counts.items(),
        key=lambda item: (
            -item[1],
            -max(labels[item[0]].values(), default=0),
            min((label.casefold() for label in labels[item[0]]), default=""),
            item[0],
        ),
    )
    top_authors: list[dict[str, Any]] = []
    for key, count in ordered[:top_limit]:
        identity = identities[key]
        variants = sorted(
            labels[key].items(), key=lambda item: (-item[1], item[0].casefold(), item[0])
        )
        label = variants[0][0] if variants else "unknown"
        top_authors.append(
            {
                "author_key": key,
                "author_id": identity["author_id"],
                "discord_user_id": identity["discord_user_id"],
                "author_id_exact": identity["author_id_exact"],
                "identity_kind": identity["identity_kind"],
                "surrogate_key": identity["surrogate_key"],
                "display_name": label,
                "display_name_variants": [name for name, _count in variants],
                "episode_count": count,
                "descriptive_share_of_cohort": round(count / episode_count, 6)
                if episode_count
                else None,
            }
        )

    exact_keys = {key for key, row in identities.items() if row["author_id_exact"]}
    surrogate_keys = set(identities) - exact_keys
    top_count = ordered[0][1] if ordered else 0
    top_three_count = sum(count for _key, count in ordered[:3])
    return {
        "distinct_authors": len(counts),
        "distinct_exact_authors": len(exact_keys),
        "distinct_surrogate_authors": len(surrogate_keys),
        "episodes_with_exact_author_id": exact_episode_count,
        "episodes_with_surrogate_author": episode_count - exact_episode_count,
        "descriptive_exact_author_episode_share": round(exact_episode_count / episode_count, 6)
        if episode_count
        else None,
        "top_author_share": round(top_count / episode_count, 6) if episode_count else None,
        "top_three_author_share": round(top_three_count / episode_count, 6)
        if episode_count
        else None,
        "top_authors": top_authors,
    }


def explicit_field_records(
    episode: dict[str, Any],
    field_name: str,
    value: str,
    messages: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Return stored extractor evidence for one value, restricted to trusted rows."""

    field_map = (episode.get("field_evidence") or {}).get(field_name) or {}
    values = field_map.get(value) if isinstance(field_map, dict) else None
    output: list[dict[str, str]] = []
    for row in values if isinstance(values, list) else []:
        if not isinstance(row, dict):
            continue
        message_id = str(row.get("message_id") or "")
        if not message_id or message_id not in messages:
            continue
        record = {
            "message_id": message_id,
            "matched_text": normalize(row.get("matched_text")),
        }
        if record not in output:
            output.append(record)
    return output


def insert_structured_episode_context(
    writer: Writer,
    episode: dict[str, Any],
    instance_id: str,
) -> dict[str, int]:
    """Normalize only explicit, already-stored Discord timing/session/RB flags."""

    counts = {"time_markers": 0, "sessions": 0, "invalidations": 0}
    setup_times = list(
        dict.fromkeys(str(value) for value in episode.get("setup_times_mentioned", []) or [] if value)
    )
    for sequence, label in enumerate(setup_times, start=1):
        records = explicit_field_records(
            episode, "setup_times", label, writer.messages
        )
        if not records:
            continue
        evidence_ids = [row["message_id"] for row in records]
        stated = next(
            (row["matched_text"] for row in records if row["matched_text"]), label
        )
        marker_id = stable_id("setup_time_marker", instance_id, label)
        writer.entity(marker_id, "setup_time_marker", parent=instance_id, root=instance_id)
        claim_id = writer.claim(
            marker_id,
            "explicit_setup_time",
            f"Discord text explicitly mentions setup time marker {stated}",
            claim_kind="explicit_example",
            epistemic_status="explicit_source",
            resolution_status="qualified",
            evidence_message_ids=evidence_ids,
            normalized={"extractor_label": label, "stated_text": stated},
            limitations=(
                "Stored as stated text only. No timezone, date, or Discord-post-time "
                "substitution is inferred."
            ),
        )
        writer.con.execute(
            """
            INSERT INTO setup_time_markers(
              marker_id,instance_id,marker_type,stated_time_text,
              timezone_as_stated,role,sequence_order,claim_id
            ) VALUES(?,?, 'explicit_setup_time',?,NULL,'setup_context',?,?)
            """,
            (marker_id, instance_id, stated, sequence, claim_id),
        )
        counts["time_markers"] += 1

    session_values = list(
        dict.fromkeys(
            str(value)
            for value in ((episode.get("field_evidence") or {}).get("sessions") or {})
            if value
        )
    )
    for sequence, label in enumerate(session_values, start=1):
        records = explicit_field_records(episode, "sessions", label, writer.messages)
        if not records:
            continue
        evidence_ids = [row["message_id"] for row in records]
        stated = next(
            (row["matched_text"] for row in records if row["matched_text"]), label
        )
        session_id = writer.session(
            label, evidence_ids[0], definition_as_stated=stated
        )
        setup_session_id = stable_id("setup_session", instance_id, label)
        writer.entity(
            setup_session_id,
            "setup_session",
            parent=instance_id,
            root=instance_id,
        )
        claim_id = writer.claim(
            setup_session_id,
            "explicit_session",
            f"Discord text explicitly mentions session {stated}",
            claim_kind="explicit_example",
            epistemic_status="explicit_source",
            resolution_status="qualified",
            evidence_message_ids=evidence_ids,
            normalized={"extractor_label": label, "stated_text": stated},
            limitations="No timezone or clock interval is inferred.",
        )
        writer.con.execute(
            """
            INSERT INTO setup_sessions(
              setup_session_id,instance_id,session_id,role,stated_time_text,
              timezone_status,claim_id
            ) VALUES(?,?,?,'explicit_mention',?,'unknown',?)
            """,
            (setup_session_id, instance_id, session_id, stated, claim_id),
        )
        counts["sessions"] += 1

    flag_specs = {
        "explicit_invalid": (
            "model",
            "explicit_invalid_mention",
            "Explicit invalidation wording was captured; applicability beyond this text is unresolved.",
        ),
        "explicit_failed": (
            "post_entry_failure",
            "explicit_failure_or_disrespect_mention",
            "Explicit failure/disrespect wording was captured; no chart state is inferred.",
        ),
        "explicit_mitigated": (
            "no_trade",
            "already_mitigated_non_actionability_mention",
            "Mitigation is retained as a non-actionability mention, not promoted to a universal technical invalidation rule.",
        ),
    }
    flags = list(
        dict.fromkeys(
            str(value)
            for value in (episode.get("rejection_block_use") or {}).get(
                "explicit_quality_flags", []
            )
            if value
        )
    )
    for flag in flags:
        if flag not in flag_specs:
            continue
        records = explicit_field_records(
            episode, "rb_quality_flags", flag, writer.messages
        )
        if not records:
            continue
        scope, invalidation_class, consequence = flag_specs[flag]
        evidence_ids = [row["message_id"] for row in records]
        stated = next(
            (row["matched_text"] for row in records if row["matched_text"]), flag
        )
        invalidation_id = stable_id("setup_invalidation", instance_id, flag)
        writer.entity(
            invalidation_id,
            "setup_invalidation",
            parent=instance_id,
            root=instance_id,
        )
        claim_id = writer.claim(
            invalidation_id,
            "explicit_rb_invalidation_or_non_actionability",
            f"Discord RB context contains explicit wording: {stated}",
            claim_kind="explicit_example",
            epistemic_status="explicit_source",
            resolution_status="qualified",
            evidence_message_ids=evidence_ids,
            normalized={
                "extractor_flag": flag,
                "invalidation_class": invalidation_class,
                "observed_state": "unknown",
            },
            limitations=(
                "The wording is stored without inferring chart geometry, a price boundary, "
                "or whether the condition was technically triggered."
            ),
        )
        writer.con.execute(
            """
            INSERT INTO setup_invalidations(
              invalidation_id,instance_id,scope,invalidation_class,condition_text,
              price_text,time_cutoff_text,observed_state,consequence,claim_id
            ) VALUES(?,?,?,?,?,NULL,NULL,'unknown',?,?)
            """,
            (
                invalidation_id,
                instance_id,
                scope,
                invalidation_class,
                stated,
                consequence,
                claim_id,
            ),
        )
        counts["invalidations"] += 1
    return counts


def import_trades(
    writer: Writer,
    episodes: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, Any]]:
    instance_by_legacy: dict[str, str] = {}
    strict: list[dict[str, Any]] = []
    imported = 0
    skipped = 0
    structured_counts = {"time_markers": 0, "sessions": 0, "invalidations": 0}
    for episode in episodes:
        legacy_id = str(episode.get("episode_id") or "")
        evidence_ids = evidence_ids_for_episode(episode)
        primary = evidence_ids[0] if evidence_ids else None
        if not legacy_id or not primary or primary not in writer.messages:
            skipped += 1
            continue
        instance_id = stable_id("setup_instance", METHOD, legacy_id)
        trade_id = stable_id("trade", METHOD, legacy_id)
        instance_by_legacy[legacy_id] = instance_id
        writer.entity(instance_id, "setup_instance")
        primary_row = writer.messages[primary]
        # The legacy extractor carries a readable author label, but the primary
        # database message is authoritative for identity. Mutating the imported
        # episode keeps the same object available to strict profiles and model
        # matching while preserving display-name-only fallback compatibility.
        episode["author_id"] = primary_row.get("author_id") or episode.get("author_id")
        if primary_row.get("author_id_exact") is not None:
            episode["author_id_exact"] = primary_row.get("author_id_exact")
        episode["author_identity_resolution"] = primary_row.get(
            "author_identity_resolution"
        ) or episode.get("author_identity_resolution")
        episode["author_display_name"] = (
            normalize(primary_row.get("author"))
            or normalize(episode.get("author"))
            or "unknown"
        )
        identity_text = (
            f"Discord-derived trade episode {legacy_id}; kind={episode.get('episode_kind')}; "
            f"linkage={episode.get('linkage_strength')}"
        )
        identity_claim = writer.claim(
            instance_id,
            "episode_identity",
            identity_text,
            claim_kind="linked_context",
            epistemic_status="linked_context",
            resolution_status="qualified",
            evidence_message_ids=evidence_ids,
            normalized={
                "legacy_episode_id": legacy_id,
                "linkage_strength": episode.get("linkage_strength"),
            },
            confidence=0.96 if episode.get("linkage_strength") == "explicit_single_message" else 0.78,
            confidence_dimension="linkage",
        )
        direction = episode.get("direction") if episode.get("direction") in {"long", "short", "mixed", "neutral"} else None
        direction_claim = None
        if direction:
            direction_claim = writer.claim(
                instance_id,
                "direction",
                f"Direction extracted as {direction}",
                claim_kind="explicit_example",
                epistemic_status="explicit_source",
                resolution_status="qualified",
                evidence_message_ids=evidence_ids,
            )
        date_text = episode.get("trade_date_local")
        date_claim = None
        if date_text:
            date_claim = writer.claim(
                instance_id,
                "occurrence_date",
                f"Trade date grouped as {date_text} in America/Chicago",
                claim_kind="linked_context",
                epistemic_status="linked_context",
                resolution_status="qualified",
                evidence_message_ids=[primary],
                limitations="Date is a deterministic timezone grouping of the Discord timestamp.",
            )
        occurrence_type = "trade_journal" if episode.get("episode_kind") != "paper_trade" else "retrospective"
        writer.con.execute(
            """
            INSERT INTO setup_instances(
              instance_id,occurrence_type,primary_message_id,primary_author_id,
              occurrence_date_text,occurrence_date_claim_id,direction,direction_claim_id,
              lifecycle_state,identity_resolution_status,identity_claim_id,notes
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                instance_id,
                occurrence_type,
                primary,
                primary_row.get("author_id"),
                date_text,
                date_claim,
                direction,
                direction_claim,
                str(episode.get("outcome") or "unknown"),
                "explicit" if episode.get("linkage_strength") == "explicit_single_message" else "linked",
                identity_claim,
                str(episode.get("notes") or ""),
            ),
        )
        writer.entity(trade_id, "trade_episode", parent=instance_id, root=instance_id)
        episode_claim = writer.claim(
            trade_id,
            "trade_episode",
            identity_text,
            claim_kind="linked_context",
            epistemic_status="linked_context",
            resolution_status="qualified",
            evidence_message_ids=evidence_ids,
        )
        strict_eligible = int(
            episode.get("eligible_trade_instances_for_win_loss_confluence_comparison") == 1
            and episode.get("outcome") in {"win", "loss"}
            and episode.get("episode_kind") == "executed_trade"
            and not episode.get("shared_confluence_attribution_across_instances")
        )
        execution_mode = "paper" if episode.get("episode_kind") == "paper_trade" else (
            "actual" if episode.get("episode_kind") == "executed_trade" else "unknown"
        )
        writer.con.execute(
            """
            INSERT INTO trade_episodes(
              trade_id,instance_id,trader_id,trade_date_text,execution_mode,episode_kind,
              aggregate_group_id,strict_comparison_eligible,linkage_status,
              episode_claim_id,legacy_trade_id,notes
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade_id,
                instance_id,
                primary_row.get("author_id"),
                date_text,
                execution_mode,
                str(episode.get("episode_kind") or "unknown"),
                None,
                strict_eligible,
                str(episode.get("linkage_strength") or "unknown"),
                episode_claim,
                legacy_id,
                (
                    "Strict eligibility is inherited from the proven conservative Discord extractor; "
                    "it is not independent outcome verification. " + str(episode.get("notes") or "")
                ),
            ),
        )
        raw_outcome = str(episode.get("outcome") or "unknown")
        resolved_outcome = OUTCOME_MAP.get(raw_outcome, "unknown")
        outcome_entity = stable_id("outcome", trade_id, raw_outcome)
        writer.entity(outcome_entity, "trade_outcome_claim", parent=trade_id, root=instance_id)
        outcome_claim = writer.claim(
            outcome_entity,
            "reported_outcome",
            f"Self-reported Discord outcome: {resolved_outcome}",
            claim_kind="explicit_outcome",
            epistemic_status="explicit_source",
            resolution_status="accepted" if raw_outcome in {"win", "loss", "breakeven", "cancelled"} else "qualified",
            evidence_message_ids=[
                str(row.get("message_id"))
                for row in episode.get("outcome_evidence", [])
                if row.get("message_id")
            ] or evidence_ids,
            normalized={"outcome": resolved_outcome, "basis": episode.get("outcome_basis")},
            confidence=0.96 if raw_outcome in {"win", "loss"} else 0.72,
            confidence_dimension="outcome_resolution",
        )
        count = episode.get("trade_count_reported")
        writer.con.execute(
            """
            INSERT INTO trade_outcome_claims(
              outcome_claim_id,trade_id,outcome,basis,terminal_at_text,is_aggregate,
              reported_trade_count,claim_id
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                outcome_entity,
                trade_id,
                resolved_outcome,
                str(episode.get("outcome_basis") or "unknown"),
                None,
                int(bool(episode.get("shared_confluence_attribution_across_instances"))),
                int(count) if isinstance(count, int) and count > 0 else None,
                outcome_claim,
            ),
        )
        resolution_status = (
            "resolved" if raw_outcome in {"win", "loss", "breakeven", "cancelled"}
            else "conflicting" if raw_outcome == "mixed" else "unresolved"
        )
        confidence_id = stable_id("confidence", outcome_claim, "outcome_resolution")
        writer.con.execute(
            """
            INSERT INTO trade_outcome_resolution(
              trade_id,resolved_outcome_claim_id,resolved_outcome,resolution_status,
              strict_comparison_eligible,resolution_reason,confidence_assessment_id
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                trade_id,
                outcome_entity,
                resolved_outcome,
                resolution_status,
                strict_eligible,
                "Explicit Discord text extraction; self-reported and not market-verified.",
                confidence_id,
            ),
        )
        feature_evidence = episode.get("field_evidence", {}).get("confluences", {})
        for raw_feature in episode.get("confluences", []) or []:
            base = feature_base(str(raw_feature))
            if not base:
                continue
            feature_rows = feature_evidence.get(raw_feature) or []
            feature_message = next(
                (str(row.get("message_id")) for row in feature_rows if row.get("message_id") in writer.messages),
                primary,
            )
            term_id = writer.term(base, feature_message)
            feature_claim = writer.claim(
                instance_id,
                f"feature:{base}",
                f"Discord text marks confluence {raw_feature}",
                claim_kind="explicit_example",
                epistemic_status="explicit_source",
                resolution_status="qualified",
                evidence_message_ids=[feature_message],
                normalized={"raw": raw_feature, "canonical": base, "state": "present"},
            )
            writer.con.execute(
                "INSERT OR IGNORE INTO setup_features(instance_id,term_id,feature_role,state,timeframe_id,claim_id) VALUES(?,?,?,'present',NULL,?)",
                (instance_id, term_id, "confluence", feature_claim),
            )
        executed = [str(value).upper() for value in episode.get("instrument", []) if str(value).lower() != "unknown"]
        context = [
            str(value).upper()
            for value in episode.get("market_context_instruments", [])
            if str(value).lower() != "unknown" and str(value).upper() not in executed
        ]
        for symbol, role in [(value, "executed") for value in executed] + [(value, "market_context") for value in context]:
            if not re.fullmatch(r"[A-Z0-9]{1,12}", symbol):
                continue
            instrument_id = writer.instrument(symbol, primary)
            instrument_claim = writer.claim(
                instance_id,
                f"instrument:{role}:{symbol}",
                f"{symbol} extracted with role={role}",
                claim_kind="explicit_example",
                epistemic_status="explicit_source",
                resolution_status="qualified",
                evidence_message_ids=evidence_ids,
                limitations="Executed and market-context roles are intentionally not interchangeable.",
            )
            writer.con.execute(
                "INSERT OR IGNORE INTO setup_instruments(instance_id,instrument_id,role,raw_text,claim_id) VALUES(?,?,?,?,?)",
                (instance_id, instrument_id, role, symbol, instrument_claim),
            )
        for rb in episode.get("rejection_block_use", {}).get("instances", []) or []:
            token = str(rb.get("timeframe") or "").lower()
            if not token or token == "unspecified":
                continue
            timeframe_id = writer.timeframe(token, primary)
            role = str(rb.get("role") or "unknown")
            if role not in {"narrative", "poi", "liquidity", "confirmation", "entry", "management", "unknown"}:
                role = "unknown"
            timeframe_claim = writer.claim(
                instance_id,
                f"timeframe:{role}:{token}",
                f"Rejection-block timeframe extracted as {token}; role={role}",
                claim_kind="explicit_example",
                epistemic_status="explicit_source",
                resolution_status="qualified",
                evidence_message_ids=evidence_ids,
            )
            writer.con.execute(
                "INSERT OR IGNORE INTO setup_timeframes(instance_id,timeframe_id,role,raw_text,claim_id) VALUES(?,?,?,?,?)",
                (instance_id, timeframe_id, role, token, timeframe_claim),
            )
        episode_structured = insert_structured_episode_context(
            writer, episode, instance_id
        )
        for key, value in episode_structured.items():
            structured_counts[key] += value
        imported += 1
        if strict_eligible:
            strict.append(episode)
    return instance_by_legacy, {
        "imported": imported,
        "skipped": skipped,
        "non_strict_imported": imported - len(strict),
        "structured_episode_context": structured_counts,
        "strict_episodes": strict,
    }


def is_question(text: str) -> bool:
    compact = normalize(text)
    return "?" in compact or bool(QUESTION_PREFIX_RE.search(compact))


def topic_for_question(text: str) -> tuple[str, str | None]:
    low = text.lower()
    if re.search(r"\b(?:rbs?|rejection\s+blocks?)\b", low):
        if re.search(r"invalid|invalidation|mitigat|disrespect|hold", low):
            return "rejection_block", "invalidation"
        if re.search(r"when|time|session|10\s*(?:am|:00)|open", low):
            return "rejection_block", "timing"
        if re.search(r"nq|mnq|es|mes", low):
            return "rejection_block", "instrument"
        return "rejection_block", "identification_or_quality"
    if re.search(r"\b(?:nq|mnq|es|mes)\b", low):
        return "instrument", "NQ_vs_ES"
    return "related_trading", None


def authority_assignment(
    writer: Writer,
    answer_message_id: str,
    authority_class: str | None,
    confidence: float,
) -> str | None:
    if not authority_class or authority_class in {"unknown", "unresolved_question", "community_adjacent_context"}:
        return None
    row = writer.messages.get(answer_message_id)
    if not row or not row.get("author_id"):
        return None
    evidence_id = writer.evidence(answer_message_id)
    if not evidence_id:
        return None
    assignment_id = stable_id("authority", writer.run_id, answer_message_id, authority_class)
    writer.con.execute(
        """
        INSERT OR IGNORE INTO authority_assignments(
          assignment_id,author_id,authority_class,basis,valid_from_utc,valid_to_utc,
          evidence_id,confidence,notes
        ) VALUES(?,?,?,?,NULL,NULL,?,?,?)
        """,
        (
            assignment_id,
            row["author_id"],
            authority_class,
            "Discord-derived legacy targeted-context authority label; not independently inferred.",
            evidence_id,
            max(0.0, min(1.0, confidence)),
            "Authority class is preserved separately from answer linkage and content.",
        ),
    )
    return assignment_id


def insert_question(
    writer: Writer,
    question_message_id: str,
    *,
    normalized_question: str,
    status: str,
    topic: str,
    subtopic: str | None,
    answers: list[dict[str, Any]],
    source_key: str,
    notes: str,
) -> str | None:
    if question_message_id not in writer.messages:
        return None
    question_id = stable_id("question", METHOD, source_key, question_message_id)
    writer.entity(question_id, "question")
    question_claim = writer.claim(
        question_id,
        "question",
        normalized_question,
        claim_kind="explicit_question",
        epistemic_status="explicit_source",
        resolution_status="qualified" if status != "answered" else "accepted",
        evidence_message_ids=[question_message_id],
        limitations=notes,
        confidence=1.0,
        confidence_dimension="extraction",
    )
    writer.con.execute(
        """
        INSERT INTO questions(
          question_id,primary_message_id,normalized_question,topic,subtopic,
          resolution_status,question_claim_id
        ) VALUES(?,?,?,?,?,?,?)
        """,
        (question_id, question_message_id, normalized_question, topic, subtopic, status, question_claim),
    )
    writer.con.execute(
        "INSERT INTO question_messages(question_id,message_id,sequence_order) VALUES(?,?,1)",
        (question_id, question_message_id),
    )
    for sequence, answer in enumerate(answers, start=1):
        answer_message_id = str(answer.get("message_id") or "")
        if answer_message_id not in writer.messages:
            continue
        summary = normalize(answer.get("summary") or writer.messages[answer_message_id].get("content_text"))
        authority_class = answer.get("authority_class")
        assignment_id = authority_assignment(
            writer,
            answer_message_id,
            str(authority_class) if authority_class else None,
            float(answer.get("confidence") or 0.7),
        )
        answer_id = stable_id("answer", question_id, answer_message_id)
        writer.entity(answer_id, "answer", parent=question_id, root=question_id)
        # Reply linkage, responsiveness, and speaker authority are orthogonal.
        # A corpus-wide direct-reply scan establishes linked context only; a
        # curated QA record may explicitly qualify the reply as an answer.
        answer_status = str(answer.get("answer_status") or "community_only")
        if answer_status not in {
            "answered", "partial", "conflicting", "community_only", "unresolved"
        }:
            answer_status = "unresolved"
        curated_answer = answer_status in {"answered", "partial", "conflicting"}
        answer_claim = writer.claim(
            answer_id,
            "answer",
            summary,
            claim_kind="explicit_answer" if curated_answer else "linked_context",
            epistemic_status="explicit_source" if curated_answer else "linked_context",
            resolution_status="accepted" if answer_status == "answered" else "qualified",
            evidence_message_ids=[answer_message_id],
            evidence_role="answers",
            speaker_author_id=writer.messages[answer_message_id].get("author_id"),
            authority_assignment_id=assignment_id,
            limitations=(
                (
                    "Curated QA status explicitly treats this reply as responsive. "
                    if curated_answer
                    else "A direct Discord reply is linked community context; linkage alone does not prove that it answers the question. "
                )
                + "Reply linkage is separate from speaker authority. "
                + ("Authority is preserved from the Discord-derived curated artifact." if assignment_id else "Speaker authority is unresolved.")
            ),
            confidence=float(answer.get("confidence") or 0.8),
            confidence_dimension="qa_resolution",
        )
        writer.con.execute(
            "INSERT INTO answers(answer_id,answer_summary,resolution_status,answer_claim_id) VALUES(?,?,?,?)",
            (answer_id, summary, answer_status, answer_claim),
        )
        writer.con.execute(
            "INSERT INTO answer_messages(answer_id,message_id,sequence_order,message_role) VALUES(?,?,?,?)",
            (answer_id, answer_message_id, sequence, "direct_reply"),
        )
        direct = int(bool(answer.get("direct_reply", True)))
        link_claim = writer.claim(
            question_id,
            "answer_linkage",
            f"Answer {answer_message_id} linked to question {question_message_id}",
            claim_kind="linked_context",
            epistemic_status="linked_context",
            resolution_status="accepted",
            evidence_message_ids=[question_message_id, answer_message_id],
            normalized={"direct_reply": bool(direct)},
            confidence=float(answer.get("linkage_confidence") or (1.0 if direct else 0.7)),
            confidence_dimension="linkage",
        )
        writer.con.execute(
            """
            INSERT INTO question_answer_links(
              question_id,answer_id,link_type,direct_reply,linkage_confidence,claim_id
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                question_id,
                answer_id,
                "discord_reply_to" if direct else "curated_context_link",
                direct,
                float(answer.get("linkage_confidence") or (1.0 if direct else 0.7)),
                link_claim,
            ),
        )
    return question_id


def import_questions(
    writer: Writer,
    curated: dict[str, Any],
) -> dict[str, Any]:
    imported_message_ids: set[str] = set()
    legacy_imported = 0
    for item in curated.get("qa_pairs", []) or []:
        qid = str(item.get("question_message_id") or "")
        if qid not in writer.messages:
            continue
        aid = str(item.get("answer_message_id") or "")
        answers: list[dict[str, Any]] = []
        if aid in writer.messages:
            answers.append(
                {
                    "message_id": aid,
                    "summary": item.get("answer_summary"),
                    "authority_class": item.get("source_authority"),
                    "confidence": item.get("confidence") or 0.8,
                    "direct_reply": True,
                    "linkage_confidence": item.get("confidence") or 0.9,
                    "answer_status": (
                        "answered" if str(item.get("status") or "").lower() == "answered"
                        else "conflicting" if str(item.get("status") or "").lower() == "conflicting"
                        else "partial"
                    ),
                }
            )
        raw_status = str(item.get("status") or "unanswered")
        status = raw_status if raw_status in {"answered", "partial", "conflicting", "unanswered", "ambiguous"} else "ambiguous"
        if status == "answered" and not answers:
            status = "partial"
        insert_question(
            writer,
            qid,
            normalized_question=normalize(item.get("normalized_question") or writer.messages[qid].get("content_text")),
            status=status,
            topic=str(item.get("topic") or "rejection_block"),
            subtopic=None,
            answers=answers,
            source_key=str(item.get("source_qa_id") or "legacy"),
            notes=str(item.get("notes") or "") + " Imported from read-only three-month Discord-derived curated analysis.",
        )
        imported_message_ids.add(qid)
        legacy_imported += 1

    children: dict[str, list[str]] = defaultdict(list)
    for message_id, row in writer.messages.items():
        parent = row.get("reply_to_message_id")
        if parent and parent in writer.messages:
            children[str(parent)].append(message_id)
    generic_imported = 0
    generic_with_linked_replies = 0
    generic_unanswered = 0
    for message_id, row in writer.messages.items():
        if message_id in imported_message_ids:
            continue
        text = normalize(row.get("content_text"))
        if not text or not is_question(text) or not RELEVANT_Q_RE.search(text):
            continue
        answers = [
            {
                "message_id": child,
                "summary": writer.messages[child].get("content_text"),
                "authority_class": None,
                "confidence": 0.85,
                "direct_reply": True,
                "linkage_confidence": 1.0,
                "answer_status": "community_only",
            }
            for child in sorted(children.get(message_id, []))
            if normalize(writer.messages[child].get("content_text"))
        ]
        topic, subtopic = topic_for_question(text)
        status = "partial" if answers else "unanswered"
        insert_question(
            writer,
            message_id,
            normalized_question=text,
            status=status,
            topic=topic,
            subtopic=subtopic,
            answers=answers,
            source_key="whole_corpus_direct_reply_scan",
            notes=(
                "Partial status means captured direct-reply context exists but was not curated as an explicit answer. "
                "Unanswered means no captured direct reply in the current coverage, not that no answer ever existed."
            ),
        )
        generic_imported += 1
        generic_with_linked_replies += int(bool(answers))
        generic_unanswered += int(not answers)
    return {
        "legacy_curated_questions": legacy_imported,
        "whole_corpus_relevant_questions": generic_imported,
        "whole_corpus_with_linked_direct_replies": generic_with_linked_replies,
        "whole_corpus_answered_by_direct_reply": 0,
        "whole_corpus_unanswered_in_capture": generic_unanswered,
    }


def import_contradictions(
    writer: Writer,
    curated: dict[str, Any],
) -> dict[str, Any]:
    """Preserve curated Discord-only tensions without resolving their meaning."""

    imported = 0
    member_count = 0
    skipped_insufficient_endpoints = 0
    for index, item in enumerate(curated.get("contradictions", []) or [], start=1):
        source_id = str(item.get("source_tension_id") or f"curated_tension_{index}")
        endpoint_a = str(item.get("message_id_a") or "")
        endpoint_b = str(item.get("message_id_b") or "")
        ordered_ids = list(
            dict.fromkeys(
                [endpoint_a, endpoint_b]
                + [str(value) for value in item.get("evidence_message_ids", []) or []]
            )
        )
        evidence_ids = [value for value in ordered_ids if value in writer.messages]
        endpoint_ids = [value for value in (endpoint_a, endpoint_b) if value in writer.messages]
        if len(set(endpoint_ids)) < 2:
            skipped_insufficient_endpoints += 1
            continue

        contradiction_id = stable_id("contradiction", METHOD, source_id)
        writer.entity(
            contradiction_id,
            "contradiction",
            notes="Imported from the read-only Discord-derived curated tension catalog.",
        )
        raw_status = str(item.get("resolution_status") or "").casefold()
        resolution_status = (
            "resolved" if raw_status == "resolved"
            else "open" if raw_status in {"unresolved", "open"}
            else "qualified"
        )
        description = normalize(item.get("description")) or None
        limitations = (
            "The curated artifact identifies a Discord-text tension. Endpoint stances are retained "
            "as catalog structure only; this importer does not infer chart conditions, truth, or a resolution. "
            + normalize(item.get("notes"))
        ).strip()
        writer.con.execute(
            """
            INSERT INTO contradiction_sets(
              contradiction_id,topic,resolution_status,resolution_summary,
              resolved_claim_id,limitations
            ) VALUES(?,?,?,?,NULL,?)
            """,
            (
                contradiction_id,
                str(item.get("topic") or "unspecified_discord_tension"),
                resolution_status,
                description,
                limitations,
            ),
        )
        for message_id in evidence_ids:
            stance = (
                "supports" if message_id == endpoint_a
                else "opposes" if message_id == endpoint_b
                else "context"
            )
            row = writer.messages[message_id]
            text = normalize(row.get("content_text") or row.get("visible_text"))
            if not text:
                continue
            claim_id = writer.claim(
                contradiction_id,
                f"contradiction_member:{stance}:{message_id}",
                text,
                claim_kind="explicit_example",
                epistemic_status="explicit_source",
                resolution_status="qualified",
                evidence_message_ids=[message_id],
                evidence_role="qualifies",
                speaker_author_id=row.get("author_id"),
                limitations=(
                    f"Curated tension member {source_id}; stance={stance} is imported catalog metadata, "
                    "not a new semantic inference from this message."
                ),
            )
            writer.con.execute(
                """
                INSERT INTO contradiction_members(contradiction_id,claim_id,stance,notes)
                VALUES(?,?,?,?)
                """,
                (
                    contradiction_id,
                    claim_id,
                    stance,
                    "Endpoint or context role preserved from the Discord-derived curated tension record.",
                ),
            )
            member_count += 1
        imported += 1
    return {
        "curated_contradiction_sets": imported,
        "contradiction_members": member_count,
        "skipped_without_two_captured_endpoints": skipped_insufficient_endpoints,
        "policy": (
            "Discord-derived curated tensions are retained unresolved unless the artifact explicitly "
            "marks them resolved; endpoint roles are catalog metadata, not inferred truth judgments."
        ),
    }


def import_rb_findings(
    writer: Writer,
    curated: dict[str, Any],
    legacy_rb: Any,
) -> dict[str, Any]:
    imported = 0
    missing_evidence = 0
    facet_counts: Counter[str] = Counter()
    for item in curated.get("rejection_block_findings", []) or []:
        source_id = str(item.get("source_finding_id") or stable_id("legacy_finding", imported))
        entity_id = stable_id("rb_finding", source_id)
        evidence_ids = [
            str(value) for value in item.get("evidence_message_ids", []) or []
            if str(value) in writer.messages
        ]
        if not evidence_ids:
            missing_evidence += 1
            continue
        facet = str(item.get("facet") or "related")
        writer.entity(entity_id, "rejection_block_finding", notes="Imported from Discord-derived curated analysis.")
        writer.claim(
            entity_id,
            facet,
            str(item.get("finding") or ""),
            claim_kind="explicit_rule" if item.get("evidence_status") == "explicit" else "curated_synthesis",
            epistemic_status="explicit_source" if item.get("evidence_status") == "explicit" else "curated_synthesis",
            resolution_status="accepted" if item.get("evidence_status") == "explicit" else "qualified",
            evidence_message_ids=evidence_ids,
            normalized={
                "instrument_scope": item.get("instrument_scope"),
                "timeframe_scope": item.get("timeframe_scope"),
                "session_scope": item.get("session_scope"),
                "source_authority": item.get("source_authority"),
            },
            limitations=str(item.get("caveat") or ""),
            confidence=float(item.get("confidence") or 0.7),
            confidence_dimension="corpus_support",
            sample_size=len(evidence_ids),
        )
        imported += 1
        facet_counts[facet] += 1

    rb_patterns = legacy_rb.RB_RE
    safe_timing_patterns = dict(legacy_rb.TIME_PATTERNS)
    # The preserved RB script's 10AM component makes the AM/00 suffix optional,
    # so a bare quantity such as "10 points" can match. Keep the protected
    # legacy source unchanged, but fail closed in this full-window pass by
    # requiring an actual clock/open token.
    if "10am_or_10_00_or_10ko" in safe_timing_patterns:
        safe_timing_patterns["10am_or_10_00_or_10ko"] = re.compile(
            r"(?<!\d)(?:10\s*:?\s*00(?:\s*a\.?m\.?)?|10\s*a\.?m\.?|10\s*ko)(?!\d)",
            re.IGNORECASE,
        )
    components = {
        "identification": legacy_rb.IDENTIFICATION_COMPONENTS,
        "invalidation_or_non_actionability": legacy_rb.INVALIDATION_COMPONENTS,
        "timing": safe_timing_patterns,
        "confluence": legacy_rb.CONFLUENCE_PATTERNS,
    }
    observed: dict[str, Counter[str]] = {key: Counter() for key in components}
    examples: dict[str, dict[str, list[str]]] = {
        key: defaultdict(list) for key in components
    }
    rb_messages = 0
    for message_id, row in writer.messages.items():
        text = normalize(row.get("content_text"))
        if not text or not rb_patterns.search(text):
            continue
        rb_messages += 1
        for facet, patterns in components.items():
            for name, pattern in patterns.items():
                if pattern.search(text):
                    observed[facet][name] += 1
                    if len(examples[facet][name]) < 20:
                        examples[facet][name].append(message_id)
                    entity_id = stable_id("rb_observation", facet, name, message_id)
                    writer.entity(entity_id, "rejection_block_observation")
                    writer.claim(
                        entity_id,
                        facet,
                        text,
                        claim_kind="explicit_rule" if legacy_rb.PRESCRIPTIVE_RE.search(text) else "explicit_example",
                        epistemic_status="explicit_source",
                        resolution_status="qualified",
                        evidence_message_ids=[message_id],
                        normalized={"component": name, "facet": facet},
                        limitations="Message-level textual evidence; no chart geometry inferred and no universal rule implied.",
                        confidence=0.9,
                    )
    rows = {
        facet: [
            {
                "component": component,
                "message_count": count,
                "evidence_message_ids": examples[facet][component],
                "interpretation": "Discord RB text co-mention; not objective formation/performance frequency.",
            }
            for component, count in counter.most_common()
        ]
        for facet, counter in observed.items()
    }
    return {
        "legacy_findings_imported": imported,
        "legacy_findings_missing_all_evidence": missing_evidence,
        "legacy_facet_counts": dict(facet_counts),
        "rb_term_message_count": rb_messages,
        "whole_corpus_textual_components": rows,
        "timing_policy": "Only explicit time/session text in RB messages is counted; Discord posting time is never substituted for setup time.",
        "legacy_bare_10_timing_pattern_overridden": True,
        "invalidation_policy": "Technical invalidation and statements of non-actionability remain distinct facets when the source wording permits.",
    }


def model_specs_by_id(legacy_model: Any) -> dict[str, Any]:
    return {str(spec.model_id): spec for spec in legacy_model.candidate_specs()}


def model_evidence_ids(model: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for row in model.get("evidence", []) or []:
        if row.get("message_id"):
            ids.append(str(row["message_id"]))
    for key in (
        "exact_inclusion_rules",
        "exact_exclusion_rules",
        "exclusion_rules",
        "entry_and_execution",
        "risk_and_stop_management",
        "target_and_trade_management",
    ):
        for row in model.get(key, []) or []:
            ids.extend(str(v) for v in row.get("evidence_message_ids", []) or [])
    return list(dict.fromkeys(ids))


def strict_model_episode(episode: dict[str, Any]) -> bool:
    return bool(
        episode.get("eligible_trade_instances_for_win_loss_confluence_comparison") == 1
        and episode.get("outcome") in {"win", "loss"}
        and episode.get("episode_kind") == "executed_trade"
        and not episode.get("shared_confluence_attribution_across_instances")
    )


def trusted_episode_evidence_ids(
    episode: dict[str, Any], messages: dict[str, dict[str, Any]]
) -> list[str]:
    return [value for value in evidence_ids_for_episode(episode) if value in messages]


def message_author_key(row: dict[str, Any]) -> str:
    raw_id = normalize(row.get("author_id"))
    exact = _explicit_bool(row.get("author_id_exact"))
    if raw_id and (exact is True or re.fullmatch(r"\d{15,22}", raw_id)):
        return "discord-user:" + raw_id.removeprefix("discord-user:")
    display = normalize(row.get("author")) or "unknown"
    return stable_id("model-evidence-author", display.casefold())


def canonical_setup_label(value: str) -> str | None:
    words = re.findall(r"[a-z0-9][a-z0-9+&/\-]*", value.casefold())
    while words and words[0] in {
        "a", "an", "the", "this", "that", "my", "our", "your", "their",
        "another", "favorite", "current", "new", "same", "good", "best",
    }:
        words.pop(0)
    if len(words) > 6:
        words = words[-6:]
    if not words or set(words) <= {
        "trade", "trading", "day", "entry", "one", "only", "normal", "basic",
    }:
        return None
    return " ".join(words)


def named_setup_labels(text: str) -> list[str]:
    output: list[str] = []
    for match in NAMED_SETUP_RE.finditer(text):
        label = canonical_setup_label(match.group(1))
        if label and label not in output:
            output.append(label)
    return output


def discovery_token_family(token: str) -> str:
    if token.startswith(("feature:", "detail:")):
        return token.split(":", 2)[1]
    if token.startswith("label:"):
        return token
    return token.split(":", 1)[0]


def episode_discovery_tokens(
    episode: dict[str, Any], messages: dict[str, dict[str, Any]]
) -> tuple[str, ...]:
    """Return only tokens already stored in episode fields or its Discord text."""

    tokens: set[str] = set()
    for raw in episode.get("confluences", []) or []:
        value = normalize(raw).casefold().replace(" ", "_")
        if not value or value == "unknown":
            continue
        base = value.split(":", 1)[0]
        tokens.add(f"feature:{base}")
        if ":" in value and base in {"rejection_block", "key_open"}:
            tokens.add(f"detail:{value}")
    for item in (episode.get("rejection_block_use") or {}).get("instances", []) or []:
        if not isinstance(item, dict):
            continue
        timeframe = normalize(item.get("timeframe")).casefold() or "unspecified"
        role = normalize(item.get("role")).casefold() or "context"
        tokens.add(f"feature:rejection_block")
        tokens.add(f"detail:rejection_block:{timeframe}:{role}")
    for field, prefix in (("session", "session"), ("setup_time", "setup_time")):
        value = normalize(episode.get(field)).casefold().replace(" ", "_")
        if value and value not in {"unknown", "unspecified", "none"}:
            tokens.add(f"{prefix}:{value}")
    for message_id in trusted_episode_evidence_ids(episode, messages):
        text = str(messages[message_id].get("content_text") or messages[message_id].get("visible_text") or "")
        for label in named_setup_labels(text):
            tokens.add(f"label:{label.replace(' ', '_')}")
    return tuple(sorted(tokens))


def valid_discovery_signature(signature: tuple[str, ...]) -> bool:
    families = [discovery_token_family(token) for token in signature]
    if len(set(families)) != len(families):
        return False
    substantive = [
        token for token in signature
        if token.startswith(("feature:", "detail:", "label:"))
    ]
    return bool(substantive) and not all(
        token.startswith(("session:", "setup_time:")) for token in signature
    )


def signature_display(signature: Sequence[str]) -> str:
    return " + ".join(
        token.replace("feature:", "").replace("detail:", "").replace("label:", "")
        .replace("_", " ")
        for token in signature
    )


def jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def overlap_coefficient(left: set[str], right: set[str]) -> float:
    denominator = min(len(left), len(right))
    return len(left & right) / denominator if denominator else 0.0


def split_explicit_sentences(text: str) -> list[str]:
    values = re.split(r"(?:\r?\n)+|(?<=[.!?])\s+", text)
    output: list[str] = []
    for value in values:
        cleaned = normalize(value)
        if 12 <= len(cleaned) <= 360 and cleaned not in output:
            output.append(cleaned)
    return output


def explicit_rule_type(text: str) -> str:
    if NO_TRADE_LANGUAGE_RE.search(text):
        return "no_trade"
    if TARGET_LANGUAGE_RE.search(text):
        return "target"
    if MANAGEMENT_LANGUAGE_RE.search(text):
        return "management"
    if re.search(r"\b(?:stop|\bsl\b)\b", text, re.IGNORECASE):
        return "stop"
    if INVALIDATION_LANGUAGE_RE.search(text):
        return "invalidation"
    if ENTRY_LANGUAGE_RE.search(text):
        return "entry"
    if re.search(r"\b(?:confirm|confirmation|trigger)\b", text, re.IGNORECASE):
        return "confirmation"
    return "eligibility"


def discovered_rule_records(
    evidence_ids: Sequence[str], messages: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    counterevidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for message_id in sorted(
        set(evidence_ids),
        key=lambda value: (
            str(messages[value].get("timestamp_utc") or ""), value
        ),
    ):
        row = messages[message_id]
        text = str(row.get("content_text") or row.get("visible_text") or "")
        for sentence in split_explicit_sentences(text):
            if NO_TRADE_LANGUAGE_RE.search(sentence):
                counterevidence.append(
                    {
                        "message_id": message_id,
                        "evidence_message_ids": [message_id],
                        "permalink": str(row.get("inferred_permalink") or ""),
                        "author_key": message_author_key(row),
                        "exact_excerpt": sentence,
                        "classification": "explicit_no_trade_or_counterevidence_text",
                    }
                )
            if not PRESCRIPTIVE_LANGUAGE_RE.search(sentence):
                continue
            rule_type = explicit_rule_type(sentence)
            marker = (rule_type, sentence.casefold())
            if marker in seen:
                continue
            seen.add(marker)
            records.append(
                {
                    "type": rule_type,
                    "text": sentence,
                    "required_state": "exclusion" if rule_type == "no_trade" else "supportive",
                    "evidence_message_ids": [message_id],
                    "evidence_basis": "verbatim_explicit_discord_prescriptive_text",
                }
            )
    priority = {
        "eligibility": 0, "confirmation": 1, "entry": 2, "invalidation": 3,
        "stop": 4, "target": 5, "management": 6, "no_trade": 7,
    }
    records.sort(
        key=lambda item: (
            priority.get(str(item["type"]), 99),
            item["text"].casefold(),
            item["evidence_message_ids"][0],
        )
    )
    records = records[:12]
    for order, record in enumerate(records, start=1):
        record["order"] = order
    counterevidence = list(
        {
            (row["message_id"], row["exact_excerpt"].casefold()): row
            for row in counterevidence
        }.values()
    )[:12]
    present = {str(record["type"]) for record in records}
    unresolved = [
        facet for facet in ("entry", "invalidation", "target") if facet not in present
    ]
    return records, counterevidence, unresolved


def candidate_evidence_records(
    evidence_ids: Sequence[str],
    messages: dict[str, dict[str, Any]],
    *,
    operational_ids: set[str],
    outcome_by_message: dict[str, set[str]],
    limit: int = 36,
) -> list[dict[str, Any]]:
    ranked = sorted(
        {value for value in evidence_ids if value in messages},
        key=lambda value: (
            0 if value in operational_ids else 1,
            0 if "loss" in outcome_by_message.get(value, set()) else 1,
            0 if "win" in outcome_by_message.get(value, set()) else 1,
            str(messages[value].get("timestamp_utc") or ""),
            value,
        ),
    )
    output: list[dict[str, Any]] = []
    for message_id in ranked[:limit]:
        row = messages[message_id]
        text = str(row.get("content_text") or row.get("visible_text") or "")
        output.append(
            {
                "message_id": message_id,
                "permalink": str(row.get("inferred_permalink") or ""),
                "timestamp_utc": str(row.get("timestamp_utc") or ""),
                "author_key": message_author_key(row),
                "author_display_name": normalize(row.get("author")) or "unknown",
                "roles": sorted(
                    ({"explicit_setup_or_rule_text"} if message_id in operational_ids else set())
                    | {f"strict_{value}_example" for value in outcome_by_message.get(message_id, set())}
                ),
                "exact_excerpt": text[:700],
                "excerpt_truncated": len(text) > 700,
            }
        )
    return output


def legacy_rule_records(
    source: dict[str, Any], messages: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sections = (
        ("exact_inclusion_rules", "eligibility", "required"),
        ("exact_exclusion_rules", "no_trade", "exclusion"),
        ("exclusion_rules", "no_trade", "exclusion"),
        ("entry_and_execution", "entry", "supportive"),
        ("risk_and_stop_management", "stop", "supportive"),
        ("target_and_trade_management", "target", "supportive"),
    )
    records: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for section, default_type, default_state in sections:
        for row in source.get(section, []) or []:
            text = normalize(row.get("rule") or row.get("text"))
            if not text:
                continue
            evidence = [
                str(value) for value in row.get("evidence_message_ids", []) or []
                if str(value) in messages
            ]
            key = (section, text.casefold())
            if key in seen:
                continue
            seen.add(key)
            if not evidence:
                unresolved.append(
                    {
                        "section": section,
                        "text": text,
                        "reason": "no_trust_eligible_full_window_message_evidence_resolved",
                    }
                )
                continue
            rule_type = explicit_rule_type(text)
            if rule_type == "eligibility" and default_type != "eligibility":
                rule_type = default_type
            records.append(
                {
                    "type": rule_type,
                    "text": text,
                    "required_state": (
                        "exclusion" if default_state == "exclusion" else
                        "required" if row.get("required", default_state == "required") else
                        "supportive"
                    ),
                    "evidence_message_ids": evidence[:20],
                    "evidence_basis": "preserved_discord_derived_three_month_rule",
                }
            )
    for order, record in enumerate(records, start=1):
        record["order"] = order
    return records, unresolved


def prepare_legacy_candidates(
    model_analysis: dict[str, Any],
    legacy_model: Any,
    episodes: list[dict[str, Any]],
    instance_by_legacy: dict[str, str],
    messages: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[set[str]]]:
    specs = model_specs_by_id(legacy_model)
    candidates: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    all_episode_sets: list[set[str]] = []
    for source in model_analysis.get("models", []) or []:
        source_id = str(source.get("model_id") or "")
        spec = specs.get(source_id)
        evidence_ids = [value for value in model_evidence_ids(source) if value in messages]
        matched = [
            episode for episode in episodes
            if str(episode.get("episode_id") or "") in instance_by_legacy
            and spec is not None and spec.matcher(episode)
        ]
        strict = [episode for episode in matched if strict_model_episode(episode)]
        strict_ids = {str(episode.get("episode_id")) for episode in strict}
        if spec is not None:
            all_episode_sets.append(strict_ids)
        authors = author_concentration(matched, top_limit=10)
        reasons: list[str] = []
        if not source_id:
            reasons.append("missing_source_model_id")
        if spec is None:
            reasons.append("missing_preserved_matcher")
        if not evidence_ids:
            reasons.append("no_trust_eligible_preserved_model_evidence")
        if len(matched) < 2:
            reasons.append("fewer_than_two_full_window_matched_episodes")
        if authors["distinct_authors"] < 2:
            reasons.append("fewer_than_two_full_window_authors")
        audit_row = {
            "source_model_id": source_id,
            "candidate_origin": "preserved_three_month_discord_template",
            "matched_episode_records": len(matched),
            "strict_matched_episode_records": len(strict),
            "distinct_authors": authors["distinct_authors"],
            "resolved_identity_evidence_messages": len(evidence_ids),
            "status": "retained" if not reasons else "insufficient_evidence",
            "reasons": reasons,
        }
        audit.append(audit_row)
        if reasons:
            continue
        rules, unresolved_rules = legacy_rule_records(source, messages)
        matched_evidence: list[str] = []
        outcome_by_message: dict[str, set[str]] = defaultdict(set)
        for episode in matched:
            for message_id in trusted_episode_evidence_ids(episode, messages):
                matched_evidence.append(message_id)
                outcome_by_message[message_id].add(str(episode.get("outcome") or "unknown"))
        identity_evidence = list(dict.fromkeys(evidence_ids + matched_evidence))
        operational = {
            value for value in identity_evidence
            if OPERATIONAL_LANGUAGE_RE.search(
                str(messages[value].get("content_text") or messages[value].get("visible_text") or "")
            )
        }
        _derived_rules, counterevidence, _derived_unresolved = discovered_rule_records(
            identity_evidence, messages
        )
        unresolved_facets = [
            facet
            for facet in ("entry", "invalidation", "target")
            if not any(row["type"] == facet for row in rules)
        ]
        candidates.append(
            {
                "source_id": source_id,
                "origin": "preserved_three_month_discord_template_full_window_rematch",
                "name": str(source.get("name") or source_id),
                "material_distinction": str(source.get("material_distinction") or ""),
                "matched": matched,
                "strict": strict,
                "evidence_ids": identity_evidence,
                "operational_ids": operational,
                "outcome_by_message": outcome_by_message,
                "rules": rules,
                "unresolved_rules": unresolved_rules,
                "unresolved_rule_facets": unresolved_facets,
                "counterevidence": counterevidence,
                "signature": [],
                "promotion_metrics": {
                    "legacy_artifact_prevalidated": True,
                    "matched_episode_records": len(matched),
                    "strict_episode_records": len(strict),
                    **authors,
                },
            }
        )
    return candidates, audit, all_episode_sets


def discover_novel_candidates(
    episodes: list[dict[str, Any]],
    instance_by_legacy: dict[str, str],
    messages: dict[str, dict[str, Any]],
    legacy_episode_sets: Sequence[set[str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trusted_strict: list[dict[str, Any]] = []
    skipped_untrusted = 0
    tokens_by_episode: dict[str, tuple[str, ...]] = {}
    for episode in episodes:
        episode_id = str(episode.get("episode_id") or "")
        if not strict_model_episode(episode) or episode_id not in instance_by_legacy:
            continue
        if not trusted_episode_evidence_ids(episode, messages):
            skipped_untrusted += 1
            continue
        trusted_strict.append(episode)
        tokens_by_episode[episode_id] = episode_discovery_tokens(episode, messages)

    support: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for episode in trusted_strict:
        episode_id = str(episode.get("episode_id"))
        tokens = tokens_by_episode[episode_id]
        for size in (2, 3):
            for signature in combinations(tokens, size):
                if valid_discovery_signature(signature):
                    support[signature].append(episode)

    reason_counts: Counter[str] = Counter()
    rejected_by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    material_insufficient: list[dict[str, Any]] = []
    threshold_passers: list[dict[str, Any]] = []

    def reject(reason: str, record: dict[str, Any]) -> None:
        reason_counts[reason] += 1
        if len(rejected_by_reason[reason]) < 30:
            rejected_by_reason[reason].append(record)

    for signature in sorted(support, key=lambda value: (-len(support[value]), value)):
        matched = support[signature]
        episode_ids = {str(episode.get("episode_id")) for episode in matched}
        basic = {
            "candidate_id": stable_id("full-window-model-candidate", *signature),
            "signature": list(signature),
            "signature_display": signature_display(signature),
            "strict_episode_records": len(matched),
        }
        if len(matched) < NOVEL_MIN_STRICT_EPISODES:
            reject("below_minimum_strict_episodes", basic)
            continue
        authors = author_concentration(matched, top_limit=10)
        evidence_ids: list[str] = []
        outcome_by_message: dict[str, set[str]] = defaultdict(set)
        dates: set[str] = set()
        for episode in matched:
            date_value = normalize(episode.get("trade_date_local"))
            if date_value:
                dates.add(date_value)
            for message_id in trusted_episode_evidence_ids(episode, messages):
                evidence_ids.append(message_id)
                outcome_by_message[message_id].add(str(episode.get("outcome") or "unknown"))
        evidence_ids = list(dict.fromkeys(evidence_ids))
        operational_ids = {
            message_id for message_id in evidence_ids
            if OPERATIONAL_LANGUAGE_RE.search(
                str(messages[message_id].get("content_text") or messages[message_id].get("visible_text") or "")
            )
        }
        operational_authors = {message_author_key(messages[value]) for value in operational_ids}
        metrics = {
            **basic,
            "distinct_authors": authors["distinct_authors"],
            "top_author_share": authors["top_author_share"],
            "distinct_trade_dates": len(dates),
            "operational_evidence_messages": len(operational_ids),
            "operational_evidence_authors": len(operational_authors),
        }
        reasons: list[str] = []
        if authors["distinct_authors"] < NOVEL_MIN_DISTINCT_AUTHORS:
            reasons.append("below_minimum_distinct_authors")
        if (authors["top_author_share"] or 1.0) > NOVEL_MAX_TOP_AUTHOR_SHARE:
            reasons.append("author_dominated")
        if len(dates) < NOVEL_MIN_DISTINCT_DATES:
            reasons.append("below_minimum_distinct_dates")
        if len(operational_ids) < NOVEL_MIN_OPERATIONAL_MESSAGES:
            reasons.append("below_minimum_explicit_operational_messages")
        if len(operational_authors) < NOVEL_MIN_OPERATIONAL_AUTHORS:
            reasons.append("below_minimum_operational_authors")
        for legacy_set in legacy_episode_sets:
            overlap = jaccard(episode_ids, legacy_set)
            containment = overlap_coefficient(episode_ids, legacy_set)
            if (
                overlap >= NEAR_DUPLICATE_JACCARD
                or containment >= NEAR_DUPLICATE_JACCARD
            ):
                reasons.append("near_duplicate_of_preserved_legacy_candidate")
                metrics["legacy_episode_jaccard"] = round(overlap, 6)
                metrics["legacy_episode_overlap_coefficient"] = round(containment, 6)
                break
        if reasons:
            material_insufficient.append({**metrics, "reasons": sorted(set(reasons))})
            for reason in reasons:
                reject(reason, metrics)
            continue
        rules, counterevidence, unresolved = discovered_rule_records(evidence_ids, messages)
        threshold_passers.append(
            {
                "source_id": basic["candidate_id"],
                "origin": "full_window_recurrent_signature_discovery",
                "name": f"Discord recurrent setup: {signature_display(signature)}",
                "material_distinction": (
                    "A deterministic full-window candidate whose strict, trust-eligible Discord "
                    f"episodes repeatedly share the stored signature {signature_display(signature)}. "
                    "The grouping is descriptive and does not establish causality or forward edge."
                ),
                "matched": matched,
                "strict": matched,
                "evidence_ids": evidence_ids,
                "operational_ids": operational_ids,
                "outcome_by_message": outcome_by_message,
                "rules": rules,
                "unresolved_rules": [],
                "unresolved_rule_facets": unresolved,
                "counterevidence": counterevidence,
                "signature": list(signature),
                "episode_ids": episode_ids,
                "promotion_metrics": {
                    **metrics,
                    **authors,
                    "thresholds_passed": True,
                },
            }
        )

    threshold_passers.sort(
        key=lambda row: (
            0 if any(str(value).startswith("label:") for value in row["signature"]) else 1,
            -len(row["signature"]),
            -int(row["promotion_metrics"]["operational_evidence_authors"]),
            -int(row["promotion_metrics"]["distinct_authors"]),
            -len(row["strict"]),
            float(row["promotion_metrics"]["top_author_share"] or 1.0),
            row["source_id"],
        )
    )
    deduplicated: list[dict[str, Any]] = []
    for candidate in threshold_passers:
        duplicate = None
        current_set = set(candidate["episode_ids"])
        current_signature = set(candidate["signature"])
        for prior in deduplicated:
            prior_set = set(prior["episode_ids"])
            overlap = jaccard(current_set, prior_set)
            containment = overlap_coefficient(current_set, prior_set)
            signature_subset = (
                current_signature <= set(prior["signature"])
                or set(prior["signature"]) <= current_signature
            )
            if overlap >= NEAR_DUPLICATE_JACCARD or containment >= NEAR_DUPLICATE_JACCARD or (
                signature_subset and overlap >= SUBSET_DUPLICATE_JACCARD
            ):
                duplicate = {
                    "candidate_id": candidate["source_id"],
                    "duplicate_of": prior["source_id"],
                    "episode_jaccard": round(overlap, 6),
                    "episode_overlap_coefficient": round(containment, 6),
                    "signature_subset_relation": signature_subset,
                }
                break
        if duplicate:
            material_insufficient.append(
                {**duplicate, "reasons": ["near_duplicate_of_stronger_full_window_candidate"]}
            )
            reject("near_duplicate_of_stronger_full_window_candidate", duplicate)
            continue
        deduplicated.append(candidate)

    audit = {
        "method": "exhaustive_pairs_and_triples_of_stored_episode_tokens_plus_explicit_named_setup_labels",
        "strict_episode_policy": (
            "Executed, single-attributable, explicit win/loss episodes with at least one "
            "trust-eligible Discord message. No quarantined-only episode can contribute."
        ),
        "thresholds": {
            "minimum_strict_episodes": NOVEL_MIN_STRICT_EPISODES,
            "minimum_distinct_authors": NOVEL_MIN_DISTINCT_AUTHORS,
            "maximum_top_author_share": NOVEL_MAX_TOP_AUTHOR_SHARE,
            "minimum_explicit_operational_messages": NOVEL_MIN_OPERATIONAL_MESSAGES,
            "minimum_explicit_operational_authors": NOVEL_MIN_OPERATIONAL_AUTHORS,
            "minimum_distinct_trade_dates": NOVEL_MIN_DISTINCT_DATES,
            "near_duplicate_episode_jaccard": NEAR_DUPLICATE_JACCARD,
            "near_duplicate_episode_overlap_coefficient": NEAR_DUPLICATE_JACCARD,
            "subset_duplicate_episode_jaccard": SUBSET_DUPLICATE_JACCARD,
        },
        "trust_eligible_strict_episodes_scanned": len(trusted_strict),
        "strict_episodes_without_trusted_message_evidence": skipped_untrusted,
        "candidate_signatures_enumerated": len(support),
        "threshold_passing_pre_deduplication": len(threshold_passers),
        "distinct_novel_candidates_pre_slot_limit": len(deduplicated),
        "rejection_reason_counts": dict(sorted(reason_counts.items())),
        "material_insufficient_candidates": material_insufficient,
        "rejected_candidate_samples_by_reason": dict(sorted(rejected_by_reason.items())),
        "warnings": [
            "Candidate membership can overlap; counts are not additive.",
            "Promotion means recurring Discord support, not probability, causality, expectancy, or validation on market data.",
            "Rule facets remain unresolved unless explicit trust-eligible Discord text supports them.",
            "Chart-only content and quarantined occurrences cannot promote a candidate.",
        ],
    }
    return deduplicated, audit


def insert_model_candidate(
    writer: Writer,
    candidate: dict[str, Any],
    instance_by_legacy: dict[str, str],
) -> tuple[str, dict[str, Any]]:
    source_id = str(candidate["source_id"])
    evidence_ids = [value for value in candidate["evidence_ids"] if value in writer.messages]
    if not evidence_ids:
        raise AnalysisError(f"Promoted model {source_id} has no trusted evidence")
    model_id = stable_id("model", source_id)
    writer.entity(model_id, "setup_model")
    identity_claim = writer.claim(
        model_id,
        "model_identity",
        str(candidate["material_distinction"]),
        claim_kind="curated_synthesis",
        epistemic_status="curated_synthesis",
        resolution_status="qualified",
        evidence_message_ids=evidence_ids[:100],
        limitations=(
            "Discord-only recurrent template. Membership overlaps, journal outcomes are self-reported, "
            "and no probability, causal effect, expectancy, or chart-only fact is inferred."
        ),
        confidence=0.85,
        confidence_dimension="corpus_support",
        sample_size=len(candidate["strict"]),
    )
    writer.con.execute(
        """
        INSERT INTO setup_models(
          model_id,canonical_name,thesis,evidence_status,lifecycle_status,
          identity_claim_id,limitations
        ) VALUES(?,?,?,'documented','active',?,?)
        """,
        (
            model_id,
            str(candidate["name"]),
            str(candidate["material_distinction"]),
            identity_claim,
            (
                "Selected-corpus, self-reported Discord support only; membership can overlap and "
                "descriptive outcome shares are non-causal and not forward probabilities."
            ),
        ),
    )
    stored_rules: list[dict[str, Any]] = []
    for order, source_rule in enumerate(candidate["rules"], start=1):
        evidence = [
            str(value) for value in source_rule.get("evidence_message_ids", [])
            if str(value) in writer.messages
        ]
        if not evidence:
            continue
        rule_type = str(source_rule.get("type") or "eligibility")
        required_state = str(source_rule.get("required_state") or "supportive")
        text = normalize(source_rule.get("text"))
        rule_id = stable_id("model_rule", source_id, order, text)
        writer.entity(rule_id, "setup_model_rule", parent=model_id, root=model_id)
        claim_id = writer.claim(
            rule_id,
            f"model_rule:{rule_type}",
            text,
            claim_kind="explicit_rule" if candidate["origin"].startswith("full_window") else "curated_synthesis",
            epistemic_status="explicit_source" if candidate["origin"].startswith("full_window") else "curated_synthesis",
            resolution_status="qualified",
            evidence_message_ids=evidence,
            limitations=(
                "For a newly discovered family this is retained explicit Discord wording, not a universalized rule. "
                "For a preserved family it is the existing Discord-derived synthesis re-resolved to trusted evidence."
            ),
        )
        writer.con.execute(
            """
            INSERT INTO setup_model_rules(
              rule_id,model_id,rule_order,rule_type,rule_text,required_state,claim_id
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (rule_id, model_id, len(stored_rules) + 1, rule_type, text, required_state, claim_id),
        )
        stored_rules.append(
            {
                "rule_id": rule_id,
                "order": len(stored_rules) + 1,
                "type": rule_type,
                "text": text,
                "required_state": required_state,
                "evidence_message_ids": evidence,
                "evidence_basis": source_rule.get("evidence_basis"),
            }
        )

    matched: list[dict[str, Any]] = []
    for episode in candidate["matched"]:
        legacy_id = str(episode.get("episode_id") or "")
        instance_id = instance_by_legacy.get(legacy_id)
        evidence = trusted_episode_evidence_ids(episode, writer.messages)
        if not instance_id or not evidence:
            continue
        match_claim = writer.claim(
            instance_id,
            f"model_match:{source_id}",
            f"Episode {legacy_id} has the stored signature used to assign Discord-only candidate {source_id}",
            claim_kind="curated_synthesis",
            epistemic_status="curated_synthesis",
            resolution_status="qualified",
            evidence_message_ids=evidence,
            limitations=(
                "Signature-only selected-corpus membership; the model's operational rules were not "
                "evaluated independently for this episode. Memberships can overlap."
            ),
            confidence=0.8,
            confidence_dimension="normalization",
        )
        writer.con.execute(
            """
            INSERT INTO setup_model_matches(
              instance_id,model_id,match_status,match_method,matched_rule_count,
              missing_rule_count,violated_rule_count,confidence_assessment_id,claim_id
            ) VALUES(?,?,'derived',?,0,?,0,NULL,?)
            """,
            (
                instance_id,
                model_id,
                METHOD + ":signature_only_rules_not_evaluated",
                len(stored_rules),
                match_claim,
            ),
        )
        for rule in stored_rules:
            state_claim = writer.claim(
                instance_id,
                f"model_rule_state:{model_id}:{rule['rule_id']}",
                (
                    f"Episode {legacy_id}: stored rule {rule['order']} for {source_id} "
                    "was not independently evaluated"
                ),
                claim_kind="insufficient_evidence",
                epistemic_status="insufficient_evidence",
                resolution_status="unresolved",
                evidence_message_ids=evidence,
                evidence_role="qualifies",
                normalized={
                    "model_id": model_id,
                    "rule_id": rule["rule_id"],
                    "state": "unknown",
                },
                limitations=(
                    "The episode's candidate signature supports model membership only; it does not "
                    "establish that this operational rule was present, absent, or violated."
                ),
            )
            writer.con.execute(
                "INSERT INTO setup_rule_states(instance_id,rule_id,state,claim_id) VALUES(?,?,'unknown',?)",
                (instance_id, rule["rule_id"], state_claim),
            )
        matched.append(episode)

    strict = [episode for episode in matched if strict_model_episode(episode)]
    wins = sum(episode.get("outcome") == "win" for episode in strict)
    losses = sum(episode.get("outcome") == "loss" for episode in strict)
    evidence_records = candidate_evidence_records(
        evidence_ids,
        writer.messages,
        operational_ids=set(candidate["operational_ids"]),
        outcome_by_message=candidate["outcome_by_message"],
    )
    card = {
        "source_model_id": source_id,
        "model_id": model_id,
        "candidate_origin": candidate["origin"],
        "name": candidate["name"],
        "material_distinction": candidate["material_distinction"],
        "candidate_signature": candidate["signature"],
        "promotion_metrics": candidate["promotion_metrics"],
        "rules": stored_rules,
        "unresolved_rule_facets": candidate["unresolved_rule_facets"],
        "insufficient_rule_evidence": candidate["unresolved_rules"],
        "contradictions_or_counterevidence": candidate["counterevidence"],
        "evidence": evidence_records,
        "evidence_message_ids": [row["message_id"] for row in evidence_records],
        "matched_episode_records": len(matched),
        "matched_legacy_episode_ids": [
            str(episode.get("episode_id")) for episode in matched if episode.get("episode_id")
        ],
        "strict_legacy_episode_ids": [
            str(episode.get("episode_id")) for episode in strict if episode.get("episode_id")
        ],
        "matched_author_concentration": author_concentration(matched, top_limit=10),
        "strict_selected_corpus": {
            "wins": wins,
            "losses": losses,
            "eligible_count": wins + losses,
            "descriptive_win_share": round(wins / (wins + losses), 6) if wins + losses else None,
            **author_concentration(strict, top_limit=10),
        },
        "membership_policy": "Overlapping descriptive membership; model counts are not additive.",
        "rule_state_policy": (
            "Candidate membership is signature-derived. Every stored operational rule is recorded as "
            "unknown for each matched instance unless a future evidence pass evaluates it explicitly."
        ),
        "warning": (
            "Descriptive, self-reported selected-corpus share and author concentration only; "
            "non-causal, not independently validated, and not expectancy or probability."
        ),
    }
    return model_id, card


def import_models(
    writer: Writer,
    model_analysis: dict[str, Any],
    legacy_model: Any,
    episodes: list[dict[str, Any]],
    instance_by_legacy: dict[str, str],
    *,
    discovery_audit: dict[str, Any] | None = None,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    legacy_candidates, legacy_audit, legacy_episode_sets = prepare_legacy_candidates(
        model_analysis, legacy_model, episodes, instance_by_legacy, writer.messages
    )
    novel_candidates, novel_audit = discover_novel_candidates(
        episodes, instance_by_legacy, writer.messages, legacy_episode_sets
    )
    available_novel_slots = max(0, MODEL_LIMIT - len(legacy_candidates[:MODEL_LIMIT]))
    selected_legacy = legacy_candidates[:MODEL_LIMIT]
    selected_novel = novel_candidates[:available_novel_slots]
    for candidate in novel_candidates[available_novel_slots:]:
        novel_audit["rejection_reason_counts"]["model_limit_slot_not_available"] = (
            int(novel_audit["rejection_reason_counts"].get("model_limit_slot_not_available") or 0) + 1
        )
        novel_audit["rejected_candidate_samples_by_reason"].setdefault(
            "model_limit_slot_not_available", []
        )
        if len(novel_audit["rejected_candidate_samples_by_reason"]["model_limit_slot_not_available"]) < 30:
            novel_audit["rejected_candidate_samples_by_reason"]["model_limit_slot_not_available"].append(
                {
                    "candidate_id": candidate["source_id"],
                    "signature": candidate["signature"],
                    "strict_episode_records": len(candidate["strict"]),
                }
            )

    model_id_map: dict[str, str] = {}
    cards: list[dict[str, Any]] = []
    for candidate in selected_legacy + selected_novel:
        model_id, card = insert_model_candidate(writer, candidate, instance_by_legacy)
        source_id = str(candidate["source_id"])
        model_id_map[source_id] = model_id
        cards.append(card)
    if discovery_audit is not None:
        discovery_audit.update(
            {
                "scope": "full_trust_eligible_Jan1_through_Jul20_corpus",
                "legacy_candidate_audit": legacy_audit,
                "novel_candidate_discovery": novel_audit,
                "retained_legacy_models": len(selected_legacy),
                "promoted_novel_models": len(selected_novel),
                "models_emitted": len(cards),
                "maximum_models": MODEL_LIMIT,
                "fifth_model_forced": False,
                "slot_policy": (
                    "Retain supported preserved Discord-derived candidates first; use only remaining "
                    "slots for distinct full-window discoveries. No model is emitted merely to fill a slot."
                ),
            }
        )
    return model_id_map, cards


def profile_rows(
    strict: list[dict[str, Any]],
    model_cards: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    overall = Counter(str(row.get("outcome")) for row in strict)
    feature: dict[str, Counter[str]] = defaultdict(Counter)
    evidence: dict[str, list[str]] = defaultdict(list)
    outcome_episodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    feature_author_episodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    feature_outcome_author_episodes: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    executed_instruments: dict[str, Counter[str]] = defaultdict(Counter)
    rb_executed_instruments: dict[str, Counter[str]] = defaultdict(Counter)
    context_instruments: dict[str, Counter[str]] = defaultdict(Counter)
    executed_instrument_author_episodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    executed_instrument_outcome_author_episodes: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    rb_instrument_author_episodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rb_instrument_outcome_author_episodes: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    context_instrument_author_episodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    context_instrument_outcome_author_episodes: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    combination_episodes: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    combination_evidence: dict[tuple[str, ...], list[str]] = defaultdict(list)
    direction_episodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    direction_evidence: dict[str, list[str]] = defaultdict(list)
    explicit_session_episodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    explicit_session_evidence: dict[str, list[str]] = defaultdict(list)
    explicit_time_episodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    explicit_time_evidence: dict[str, list[str]] = defaultdict(list)
    executed_instrument_evidence: dict[str, list[str]] = defaultdict(list)
    explicit_executed_instrument_episodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for episode in strict:
        outcome = str(episode.get("outcome"))
        outcome_episodes[outcome].append(episode)
        canonical_features = episode_feature_bases(episode)
        combination = tuple(sorted(canonical_features))
        if combination:
            combination_episodes[combination].append(episode)
            for name in combination:
                for message_id in episode_feature_evidence_ids(episode, name):
                    if message_id not in combination_evidence[combination]:
                        combination_evidence[combination].append(message_id)
        for name in canonical_features:
            feature[name][outcome] += 1
            feature_author_episodes[name].append(episode)
            feature_outcome_author_episodes[(name, outcome)].append(episode)
            for message_id in episode_feature_evidence_ids(episode, name):
                if len(evidence[name]) < 20 and message_id not in evidence[name]:
                    evidence[name].append(message_id)
        uses_rb = "rejection_block" in canonical_features
        for family in episode_instrument_families(episode, "instrument"):
            executed_instruments[family][outcome] += 1
            executed_instrument_author_episodes[family].append(episode)
            executed_instrument_outcome_author_episodes[(family, outcome)].append(episode)
            direct_instrument_ids = episode_instrument_family_evidence_ids(episode, family)
            if direct_instrument_ids:
                explicit_executed_instrument_episodes[family].append(episode)
            for message_id in direct_instrument_ids:
                if message_id not in executed_instrument_evidence[family]:
                    executed_instrument_evidence[family].append(message_id)
            if uses_rb:
                rb_executed_instruments[family][outcome] += 1
                rb_instrument_author_episodes[family].append(episode)
                rb_instrument_outcome_author_episodes[(family, outcome)].append(episode)
        for family in episode_instrument_families(
            episode, "market_context_instruments"
        ):
            context_instruments[family][outcome] += 1
            context_instrument_author_episodes[family].append(episode)
            context_instrument_outcome_author_episodes[(family, outcome)].append(episode)
        direction = str(episode.get("direction") or "")
        if direction in {"long", "short", "mixed", "neutral"}:
            direct_ids = episode_field_evidence_ids(episode, "direction", direction)
            if direct_ids:
                direction_episodes[direction].append(episode)
                for message_id in direct_ids:
                    if message_id not in direction_evidence[direction]:
                        direction_evidence[direction].append(message_id)
        session_map = (episode.get("field_evidence") or {}).get("sessions") or {}
        if isinstance(session_map, dict):
            for session_label in session_map:
                direct_ids = episode_field_evidence_ids(
                    episode, "sessions", str(session_label)
                )
                if not direct_ids:
                    continue
                key = normalize(session_label)
                if not key:
                    continue
                explicit_session_episodes[key].append(episode)
                for message_id in direct_ids:
                    if message_id not in explicit_session_evidence[key]:
                        explicit_session_evidence[key].append(message_id)
        for time_label in dict.fromkeys(
            str(value) for value in episode.get("setup_times_mentioned", []) or [] if value
        ):
            direct_ids = episode_field_evidence_ids(episode, "setup_times", time_label)
            if not direct_ids:
                continue
            key = normalize(time_label)
            if not key:
                continue
            explicit_time_episodes[key].append(episode)
            for message_id in direct_ids:
                if message_id not in explicit_time_evidence[key]:
                    explicit_time_evidence[key].append(message_id)
    baseline_n = overall["win"] + overall["loss"]
    baseline = overall["win"] / baseline_n if baseline_n else None
    features = []
    for name, counts in feature.items():
        n = counts["win"] + counts["loss"]
        share = counts["win"] / n if n else None
        author_profile = author_concentration(feature_author_episodes[name], top_limit=10)
        features.append(
            {
                "confluence": name,
                "wins": counts["win"],
                "losses": counts["loss"],
                "eligible_count": n,
                "descriptive_selected_corpus_win_share": round(share, 6) if share is not None else None,
                "difference_from_selected_corpus_baseline": round(share - baseline, 6) if share is not None and baseline is not None else None,
                "evidence_message_ids": evidence[name],
                **author_profile,
                "author_concentration_by_outcome": {
                    outcome: author_concentration(
                        feature_outcome_author_episodes[(name, outcome)], top_limit=10
                    )
                    for outcome in ("win", "loss")
                },
                "warning": "Overlapping descriptive association; not causal and not a forward probability.",
            }
        )
    features.sort(key=lambda row: (-row["eligible_count"], row["confluence"]))
    comparison_pool = [row for row in features if row["eligible_count"] >= 5]
    higher = sorted(
        [
            row for row in comparison_pool
            if baseline is not None
            and row["descriptive_selected_corpus_win_share"] is not None
            and row["descriptive_selected_corpus_win_share"] > baseline
        ],
        key=lambda row: (-row["descriptive_selected_corpus_win_share"], -row["eligible_count"], row["confluence"]),
    )
    lower = sorted(
        [
            row for row in comparison_pool
            if baseline is not None
            and row["descriptive_selected_corpus_win_share"] is not None
            and row["descriptive_selected_corpus_win_share"] < baseline
        ],
        key=lambda row: (row["descriptive_selected_corpus_win_share"], -row["eligible_count"], row["confluence"]),
    )

    def outcome_feature_profile(outcome: str) -> list[dict[str, Any]]:
        denominator = overall[outcome]
        output = []
        for name, counts in feature.items():
            count = counts[outcome]
            if not count:
                continue
            output.append(
                {
                    "confluence": name,
                    "trade_count": count,
                    "descriptive_share_of_outcome_cohort": round(count / denominator, 6) if denominator else None,
                    **author_concentration(
                        feature_outcome_author_episodes[(name, outcome)], top_limit=10
                    ),
                }
            )
        return sorted(output, key=lambda row: (-row["trade_count"], row["confluence"]))

    def instrument_rows(
        values: dict[str, Counter[str]],
        author_values: dict[str, list[dict[str, Any]]],
        outcome_author_values: dict[tuple[str, str], list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        output = []
        for symbol, counts in sorted(values.items()):
            n = counts["win"] + counts["loss"]
            output.append(
                {
                    "instrument_family": symbol,
                    "wins": counts["win"],
                    "losses": counts["loss"],
                    "eligible_count": n,
                    "descriptive_selected_corpus_win_share": round(counts["win"] / n, 6) if n else None,
                    **author_concentration(author_values[symbol], top_limit=10),
                    "author_concentration_by_outcome": {
                        outcome: author_concentration(
                            outcome_author_values[(symbol, outcome)], top_limit=10
                        )
                        for outcome in ("win", "loss")
                    },
                }
            )
        return output

    rb_instrument_rows = instrument_rows(
        rb_executed_instruments,
        rb_instrument_author_episodes,
        rb_instrument_outcome_author_episodes,
    )
    nq_es_rb_counts = {
        row["instrument_family"]: row["eligible_count"]
        for row in rb_instrument_rows
        if row["instrument_family"] in {"NQ", "ES"}
    }
    rb_comparison_status = (
        "descriptive_only"
        if nq_es_rb_counts.get("NQ", 0) >= 5 and nq_es_rb_counts.get("ES", 0) >= 5
        else "insufficient_balanced_executed_sample"
    )

    def strict_slice_rows(
        groups: dict[Any, list[dict[str, Any]]],
        evidence_by_key: dict[Any, list[str]],
        *,
        dimension: str,
        overlaps_within_dimension: bool,
        extra_by_key: dict[Any, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for key, episodes in groups.items():
            wins = sum(str(episode.get("outcome")) == "win" for episode in episodes)
            losses = sum(str(episode.get("outcome")) == "loss" for episode in episodes)
            count = wins + losses
            if not count:
                continue
            display_key: Any = list(key) if isinstance(key, tuple) else key
            output.append(
                {
                    "dimension": dimension,
                    "slice_key": display_key,
                    "wins": wins,
                    "losses": losses,
                    "sample_count": count,
                    "descriptive_selected_corpus_win_share": round(wins / count, 6),
                    **author_concentration(episodes, top_limit=10),
                    "evidence_message_ids": list(
                        dict.fromkeys(evidence_by_key.get(key, []))
                    )[:50],
                    "overlaps_within_dimension": overlaps_within_dimension,
                    "warning": (
                        "Strict selected-corpus outcome slice only. Author-clustered and non-causal; "
                        "overlapping slices are not additive and this share is not a forecast, "
                        "confidence score, or trade probability."
                    ),
                    **((extra_by_key or {}).get(key, {})),
                }
            )
        return sorted(
            output,
            key=lambda row: (-int(row["sample_count"]), json_text(row["slice_key"])),
        )

    strict_by_legacy_id = {
        str(episode.get("episode_id")): episode
        for episode in strict if episode.get("episode_id")
    }
    model_episodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    model_evidence: dict[str, list[str]] = defaultdict(list)
    model_metadata: dict[str, dict[str, Any]] = {}
    for card in model_cards:
        model_id = str(card.get("model_id") or "")
        if not model_id:
            continue
        model_metadata[model_id] = {
            "model_name": str(card.get("name") or card.get("source_model_id") or model_id),
            "membership_basis": "signature_derived_rules_not_independently_evaluated",
        }
        for legacy_id in dict.fromkeys(
            str(value) for value in card.get("strict_legacy_episode_ids", []) if value
        ):
            episode = strict_by_legacy_id.get(legacy_id)
            if not episode:
                continue
            model_episodes[model_id].append(episode)
            for message_id in evidence_ids_for_episode(episode):
                if message_id not in model_evidence[model_id]:
                    model_evidence[model_id].append(message_id)

    def dimension_coverage(groups: dict[Any, list[dict[str, Any]]]) -> dict[str, int]:
        represented = {
            str(episode.get("episode_id"))
            for episodes in groups.values()
            for episode in episodes
            if episode.get("episode_id")
        }
        return {
            "strict_episodes_with_stored_dimension": len(represented),
            "strict_episodes_without_stored_dimension": max(0, baseline_n - len(represented)),
        }

    strict_slice_profiles = {
        "policy": (
            "Each row is episode-grain and restricted to strict win/loss episodes. Missing dimensions "
            "remain missing. Evidence IDs are retained, exact canonical confluence sets are not collapsed "
            "to marginals, and model membership is clearly labeled signature-derived."
        ),
        "canonical_confluence_combinations": {
            "combination_basis": "exact_sorted_set_of_canonical_confluence_families_per_episode",
            **dimension_coverage(combination_episodes),
            "rows": strict_slice_rows(
                combination_episodes,
                combination_evidence,
                dimension="canonical_confluence_combination",
                overlaps_within_dimension=False,
            ),
        },
        "executed_instrument": {
            "inclusion_basis": "explicit_extractor_field_evidence_only",
            **dimension_coverage(explicit_executed_instrument_episodes),
            "rows": strict_slice_rows(
                explicit_executed_instrument_episodes,
                executed_instrument_evidence,
                dimension="executed_instrument_family",
                overlaps_within_dimension=True,
            ),
        },
        "direction": {
            "inclusion_basis": "explicit_extractor_field_evidence_only",
            **dimension_coverage(direction_episodes),
            "rows": strict_slice_rows(
                direction_episodes,
                direction_evidence,
                dimension="direction",
                overlaps_within_dimension=False,
            ),
        },
        "explicit_session": {
            "inclusion_basis": "explicit_session_text_only_no_timezone_or_clock_inference",
            **dimension_coverage(explicit_session_episodes),
            "rows": strict_slice_rows(
                explicit_session_episodes,
                explicit_session_evidence,
                dimension="explicit_session_label",
                overlaps_within_dimension=True,
            ),
        },
        "explicit_setup_time": {
            "inclusion_basis": "explicit_setup_time_text_only_no_timezone_inference",
            **dimension_coverage(explicit_time_episodes),
            "rows": strict_slice_rows(
                explicit_time_episodes,
                explicit_time_evidence,
                dimension="explicit_setup_time_label",
                overlaps_within_dimension=True,
            ),
        },
        "model": {
            "inclusion_basis": "stored_signature_derived_model_membership",
            **dimension_coverage(model_episodes),
            "rows": strict_slice_rows(
                model_episodes,
                model_evidence,
                dimension="model",
                overlaps_within_dimension=True,
                extra_by_key=model_metadata,
            ),
        },
        "global_warning": (
            "All rows are descriptive selected-corpus slices with self-reported outcomes. They are "
            "author-clustered, often overlapping, non-causal, and are not confidence or probability claims."
        ),
    }
    denominator_violations = [
        {
            "dimension": "confluence",
            "key": row["confluence"],
            "eligible_count": row["eligible_count"],
            "overall_eligible_count": baseline_n,
        }
        for row in features
        if int(row["eligible_count"]) > baseline_n
    ]
    for dimension, values in (
        ("executed_instrument_family", executed_instruments),
        ("rb_executed_instrument_family", rb_executed_instruments),
        ("market_context_instrument_family", context_instruments),
    ):
        denominator_violations.extend(
            {
                "dimension": dimension,
                "key": key,
                "eligible_count": counts["win"] + counts["loss"],
                "overall_eligible_count": baseline_n,
            }
            for key, counts in values.items()
            if counts["win"] + counts["loss"] > baseline_n
        )
    if denominator_violations:
        raise AnalysisError(
            "Episode-grain profile denominator invariant failed: "
            + json_text(denominator_violations[:20])
        )
    return {
        "strict_eligibility": (
            "Conservative extractor: executed-trade episode, one attributable instance, explicit win/loss, "
            "at least one confluence, and no shared multi-trade attribution."
        ),
        "overall": {
            "wins": overall["win"],
            "losses": overall["loss"],
            "eligible_count": baseline_n,
            "descriptive_selected_corpus_win_share": round(baseline, 6) if baseline is not None else None,
            **author_concentration(strict, top_limit=20),
        },
        "win_profile": {
            "trade_count": overall["win"],
            **author_concentration(outcome_episodes["win"], top_limit=20),
            "confluences": outcome_feature_profile("win"),
        },
        "loss_profile": {
            "trade_count": overall["loss"],
            **author_concentration(outcome_episodes["loss"], top_limit=20),
            "confluences": outcome_feature_profile("loss"),
        },
        "confluence_profiles": features,
        "observed_higher_share_associations": higher,
        "observed_lower_share_associations": lower,
        "association_catalog_policy": (
            "Higher/lower means above/below the overall strict selected-corpus descriptive win share, "
            "with at least five eligible episodes. Each canonical confluence and instrument family is "
            "counted at most once per episode. It is not a calibrated probability ranking."
        ),
        "episode_grain_deduplication": (
            "Detailed timeframe/role tags are preserved in episode evidence, but marginal confluence "
            "and instrument-family denominators count each strict episode once per canonical family."
        ),
        "denominator_invariants": {
            "status": "passed",
            "overall_strict_episode_count": baseline_n,
            "confluence_and_instrument_subsets_do_not_exceed_overall": True,
        },
        "strict_slice_profiles": strict_slice_profiles,
        "executed_instrument_comparison": instrument_rows(
            executed_instruments,
            executed_instrument_author_episodes,
            executed_instrument_outcome_author_episodes,
        ),
        "rejection_block_executed_instrument_comparison": {
            "status": rb_comparison_status,
            "rows": rb_instrument_rows,
            "answer_guard": (
                "A higher selected-corpus share cannot establish that rejection blocks objectively work best "
                "on one instrument. If either executed family has fewer than five strict episodes, the "
                "NQ-versus-ES comparison is explicitly insufficient."
            ),
        },
        "market_context_instrument_mentions": instrument_rows(
            context_instruments,
            context_instrument_author_episodes,
            context_instrument_outcome_author_episodes,
        ),
        "author_identity_policy": (
            "Profiles group by canonical exact Discord user ID when captured. Database surrogate IDs "
            "remain separate and display-name-only legacy episodes use deterministic surrogate keys; "
            "no display name is treated as a verified unique person."
        ),
        "instrument_guard": (
            "Only executed-instrument rows may answer NQ-vs-ES execution questions. "
            "Market-context/intermarket mentions are reported separately and never promoted to executed trades."
        ),
        "global_warning": (
            "All shares are descriptive within the strict selected Discord corpus. They are overlapping, "
            "self-reported, author-clustered, non-causal, and not a market probability or expectancy."
        ),
    }


def add_profile_claims(writer: Writer, profiles: dict[str, Any]) -> None:
    for row in profiles.get("confluence_profiles", []):
        entity_id = stable_id("confluence_profile", row["confluence"])
        writer.entity(entity_id, "confluence_profile")
        writer.claim(
            entity_id,
            "selected_corpus_outcome_association",
            (
                f"{row['confluence']}: {row['wins']} wins / {row['losses']} losses "
                f"(n={row['eligible_count']}) in the strict selected Discord corpus."
            ),
            claim_kind="observed_association",
            epistemic_status="observed_association",
            resolution_status="qualified",
            evidence_message_ids=row.get("evidence_message_ids", []),
            normalized=row,
            limitations=row["warning"],
            confidence=0.9 if row["eligible_count"] >= 20 else 0.7 if row["eligible_count"] >= 5 else 0.5,
            confidence_dimension="corpus_support",
            sample_size=row["eligible_count"],
        )


def add_cohorts_and_rollups(
    writer: Writer,
    profiles: dict[str, Any],
    model_cards: list[dict[str, Any]],
    total_imported_episode_count: int,
) -> None:
    coverage = writer.coverage
    cohort_id = stable_id("cohort", METHOD, "strict_win_loss")
    writer.entity(cohort_id, "analysis_cohort")
    evidence_ids = [
        message_id
        for row in profiles.get("confluence_profiles", [])[:20]
        for message_id in row.get("evidence_message_ids", [])[:2]
    ]
    cohort_claim = writer.claim(
        cohort_id,
        "cohort_definition",
        profiles["strict_eligibility"],
        claim_kind="curated_synthesis",
        epistemic_status="curated_synthesis",
        resolution_status="qualified",
        evidence_message_ids=evidence_ids,
        limitations=profiles["global_warning"],
    )
    writer.con.execute(
        """
        INSERT INTO analysis_cohorts(
          cohort_id,name,eligibility_definition_json,exclusion_definition_json,
          window_start_utc,window_end_utc,analysis_run_id,cohort_claim_id
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            cohort_id,
            "Strict Discord win/loss comparison",
            json_text({"definition": profiles["strict_eligibility"]}),
            json_text({"excluded": ["breakeven", "mixed", "cancelled", "open", "unknown", "aggregate", "paper", "no attributable confluence"]}),
            coverage["window_start_utc"],
            coverage["window_end_utc"],
            writer.run_id,
            cohort_claim,
        ),
    )

    def rollup(
        model_id: str | None,
        label: str,
        wins: int,
        losses: int,
        excluded: int,
        evidence: list[str],
        author_metrics: dict[str, Any],
    ) -> None:
        eligible = wins + losses
        entity_id = stable_id("rollup", cohort_id, model_id or "overall")
        writer.entity(entity_id, "setup_performance_rollup", parent=cohort_id, root=cohort_id)
        claim_id = writer.claim(
            entity_id,
            "descriptive_selected_corpus_share",
            (
                f"{label}: {wins} wins / {losses} losses (n={eligible}); "
                f"{max(0, excluded)} imported or matched episodes excluded from this strict denominator."
            ),
            claim_kind="observed_association",
            epistemic_status="observed_association",
            resolution_status="qualified",
            evidence_message_ids=evidence,
            normalized={
                "wins": wins,
                "losses": losses,
                "eligible_count": eligible,
                "excluded_count": excluded,
                "distinct_authors": int(author_metrics.get("distinct_authors") or 0),
                "top_author_share": author_metrics.get("top_author_share"),
                "author_identity_basis": "exact_id_when_available_else_surrogate",
            },
            limitations=profiles["global_warning"],
            confidence=0.9 if eligible >= 20 else 0.7 if eligible >= 5 else 0.5,
            confidence_dimension="corpus_support",
            sample_size=eligible,
        )
        writer.con.execute(
            """
            INSERT INTO setup_performance_rollups(
              rollup_id,cohort_id,model_id,instrument_id,timeframe_id,session_id,
              eligible_count,wins,losses,breakevens,unknowns,excluded_count,
              distinct_authors,top_author_share,observed_win_rate,models_overlap,
              not_causal,limitations,claim_id
            ) VALUES(?,?,?,NULL,NULL,NULL,?,?,?,0,0,?,?,?,?,1,1,?,?)
            """,
            (
                entity_id,
                cohort_id,
                model_id,
                eligible,
                wins,
                losses,
                max(0, excluded),
                int(author_metrics.get("distinct_authors") or 0),
                author_metrics.get("top_author_share"),
                wins / eligible if eligible else None,
                profiles["global_warning"],
                claim_id,
            ),
        )

    overall = profiles["overall"]
    rollup(
        None,
        "All strict eligible episodes",
        overall["wins"],
        overall["losses"],
        max(0, total_imported_episode_count - int(overall["eligible_count"])),
        evidence_ids,
        overall,
    )
    for card in model_cards:
        strict = card["strict_selected_corpus"]
        rule_evidence = [
            message_id for rule in card.get("rules", []) for message_id in rule.get("evidence_message_ids", [])[:2]
        ]
        rollup(
            card["model_id"],
            str(card.get("name") or card["source_model_id"]),
            strict["wins"],
            strict["losses"],
            max(0, int(card.get("matched_episode_records") or 0) - int(strict["eligible_count"])),
            rule_evidence,
            strict,
        )


def insert_documents(
    writer: Writer,
    *,
    coverage: dict[str, Any],
    rb: dict[str, Any],
    qa: dict[str, Any],
    contradictions: dict[str, Any],
    relevance: dict[str, Any],
    dictionary: dict[str, Any],
    profiles: dict[str, Any],
    models: list[dict[str, Any]],
    model_discovery: dict[str, Any],
    extraction: dict[str, Any],
    provenance: dict[str, Any],
) -> None:
    documents = {
        "discord_analysis_coverage": coverage,
        "discord_rejection_block_research": rb,
        "discord_qa_catalog_summary": qa,
        "discord_contradiction_catalog_summary": contradictions,
        "discord_trade_profiles": profiles,
        "discord_model_cards": {
            "models": models,
            "maximum_models": MODEL_LIMIT,
            "models_emitted": len(models),
            "fifth_model_policy": "No fifth model is forced; only locally Discord-supported templates are emitted.",
            "discovery": model_discovery,
        },
        "discord_analysis_methodology": {
            "schema_version": SCHEMA_VERSION,
            "method": METHOD,
            "source_scope": SOURCE_SCOPE,
            "outside_sources_used": 0,
            "trade_extraction": extraction,
            "relevance_annotation_audit": relevance,
            "data_dictionary_audit": dictionary,
            "provenance": provenance,
            "guardrails": [
                "No outside trading knowledge, web, market data, or Cardinal concept defaults.",
                "No chart-only facts inferred from attachment metadata.",
                "Exact non-owned embeds remain labeled metadata only and are excluded from attachment model inputs, evidence, extraction, and archive bytes.",
                "This analysis stage does not fetch attachment bytes; filename/URL metadata and any pre-existing local extraction remain provenance only.",
                "Direct reply linkage and source authority are separate fields.",
                "Executed instruments and market-context instruments are separate roles.",
                "Only descriptive selected-corpus shares are calculated; no forward probability or expectancy.",
                "Coverage status is retained and unresolved fields remain unresolved.",
                "Direct reply linkage alone is community context, not proof that a question was answered.",
                "Model signature membership does not imply that every operational rule matched; unevaluated rule states remain unknown.",
                "Model candidates are exhaustively discovered from stored full-window episode signatures; preserved three-month templates are retained only when their evidence and rematch resolve.",
                "Novel models require repeated strict episodes, multiple authors and dates, explicit operational Discord text, bounded author concentration, and deduplication against preserved and stronger candidates.",
            ],
        },
    }
    for name, content in documents.items():
        writer.con.execute(
            "INSERT INTO analysis_documents(document_name,analysis_run_id,created_by,content_json,notes) VALUES(?,?,?,?,?)",
            (name, writer.run_id, METHOD, json_text(content), "Discord-only local analysis document."),
        )


def populate_relevance_annotations(writer: Writer) -> dict[str, Any]:
    """Give each trust-eligible in-window message one non-destructive audit label."""

    used = {
        str(row[0])
        for row in writer.con.execute(
            "SELECT DISTINCT message_id FROM evidence_items WHERE analysis_run_id=? AND message_id IS NOT NULL",
            (writer.run_id,),
        )
    }
    curated_count = 0
    retained_count = 0
    for message_id in sorted(writer.messages):
        if message_id in used:
            label = "curated_relevant_evidence"
            score = 1.0
            reason = (
                "Selected by at least one current analysis evidence pass. This is a pipeline-use label, "
                "not a claim that every other message is unimportant."
            )
            curated_count += 1
        else:
            label = "raw_retained_not_curated"
            score = 0.0
            reason = (
                "Retained in the local corpus but not selected by the current evidence passes. Absence "
                "from curated analysis does not establish irrelevance or lack of future analytical value."
            )
            retained_count += 1
        writer.con.execute(
            """
            INSERT INTO relevance_annotations(
              message_id,analysis_run_id,label,score,reason
            ) VALUES(?,?,?,?,?)
            """,
            (message_id, writer.run_id, label, score, reason),
        )
    return {
        "eligible_messages_labeled": curated_count + retained_count,
        "curated_relevant_evidence": curated_count,
        "raw_retained_not_curated": retained_count,
        "destructive_filtering_used": False,
    }


SOURCE_DATA_TABLES = {
    "meta", "collection_runs", "guilds", "channel_inventory", "collection_units",
    "coverage_segments", "source_artifacts", "authors", "author_names", "messages",
    "message_versions", "message_source_occurrences", "attachments", "embeds", "reactions",
    "discord_roles", "author_role_observations",
}
TECHNICAL_TABLE_SUFFIXES = (
    "_fts", "_fts_data", "_fts_idx", "_fts_content", "_fts_docsize", "_fts_config"
)
TABLE_DESCRIPTIONS = {
    "messages": "Canonical captured Discord message record.",
    "attachments": (
        "Discord-owned attachment metadata plus durable local archive disposition. "
        "Terminal unavailable rows preserve substantiated media gaps; terminal failed rows "
        "are degraded, block literal release, and are not chart evidence."
    ),
    "attachment_extractions": (
        "Complete/partial local OCR or manual artifacts with exact attachment provenance, "
        "verified path/hash/size metadata, and no implicit confidence. Failed extraction "
        "history remains in attachments.extraction_artifacts_json and is not queryable evidence."
    ),
    "evidence_items": "Exact, traceable excerpts selected from captured Discord records.",
    "claims": "Evidence-backed analytical claims with explicit epistemic and resolution status.",
    "trade_episodes": "Conservatively extracted trade-journal episodes.",
    "trade_outcome_resolution": "Resolved outcome and strict-comparison eligibility for a trade episode.",
    "setup_models": "Discord-supported setup model catalog; not market-validated probabilities.",
    "setup_model_matches": "Instance membership in a model, including rule-count audit fields.",
    "setup_rule_states": "Per-instance stored model-rule evaluation state.",
    "setup_time_markers": "Clock text explicitly associated with a setup; no unstated timezone inference.",
    "setup_sessions": "Session labels explicitly associated with a setup.",
    "setup_invalidations": "Explicit invalidation or non-actionability text associated with a setup.",
    "questions": "Relevant Discord questions and conservative resolution status.",
    "answers": "Curated answers or reply-linked community context.",
    "relevance_annotations": "Non-destructive current-pass usage labels for eligible messages.",
    "contradiction_sets": "Curated Discord-text tensions with open/qualified/resolved status.",
    "setup_performance_rollups": "Selected-corpus descriptive outcome rollups; non-causal.",
    "data_dictionary": "Column-level local database documentation.",
}
COLUMN_DESCRIPTIONS = {
    ("messages", "content_text"): "Captured Discord message text used for text analysis.",
    ("messages", "evidence_trust_state"): "Capture/trust classification controlling analytical evidence use.",
    ("messages", "eligible_for_accepted_evidence"): "Whether this captured occurrence may support accepted claims.",
    ("attachments", "local_package_path"): (
        "Release-package-relative path to verified local bytes; NULL means no bytes were archived."
    ),
    ("attachments", "relation_type"): (
        "Exact DOM relation classification; embedded_external/copy relations are metadata only."
    ),
    ("attachments", "ownership_status"): (
        "owned_exact, non_owned_exact, or unresolved; only owned_exact may have archive or extraction state."
    ),
    ("attachments", "ownership_evidence_json"): (
        "Exact owner message/channel, CDN source channel, and DOM-relation evidence retained for audit."
    ),
    ("attachments", "eligible_for_attachment_evidence"): (
        "One only for exact-owned attachments; non-owned and unresolved media cannot enter evidence items."
    ),
    ("attachments", "capture_status"): (
        "metadata_only, pending, downloaded, unavailable, or failed terminal archive disposition."
    ),
    ("attachments", "capture_terminal"): (
        "One only after downloaded, unavailable, or fully documented terminal failure disposition."
    ),
    ("attachments", "capture_attempts_json"): (
        "Ordered browser-assisted attempts; never contains credentials, cookies, or browser storage."
    ),
    ("attachments", "content_sha256"): (
        "SHA-256 of archived bytes when capture_status is downloaded; NULL otherwise."
    ),
    ("attachments", "extraction_status"): (
        "not_attempted, complete, partial, or failed local extraction status; status alone is not a chart claim."
    ),
    ("attachments", "chart_claim_eligible"): (
        "Always zero here: attachment metadata or extraction presence never auto-accepts chart-dependent claims."
    ),
    ("attachment_extractions", "locator_json"): (
        "Redundant local package path/hash/status locator for the exact verified extraction artifact."
    ),
    ("attachment_extractions", "status"): (
        "Only complete or partial; failed/no-artifact extraction attempts are excluded from this table."
    ),
    ("attachment_extractions", "local_package_path"): (
        "Package-relative path under attachments/extractions for locally verified artifact bytes."
    ),
    ("attachment_extractions", "content_sha256"): (
        "SHA-256 of the verified local extraction artifact."
    ),
    ("attachment_extractions", "byte_size"): (
        "Verified local extraction artifact byte size."
    ),
    ("attachment_extractions", "artifact_verified"): (
        "Always one: corpus assembly rehashed the local artifact before database ingestion."
    ),
    ("attachment_extractions", "confidence"): (
        "Optional extraction confidence; NULL means unreported and is never defaulted to 1.0."
    ),
    ("claims", "claim_kind"): "How the claim was obtained: explicit, linked, observed, synthesized, or insufficient.",
    ("claims", "epistemic_status"): "Evidence basis of the claim; never interpret as trade probability.",
    ("claims", "resolution_status"): "Whether the claim is accepted, qualified, unresolved, conflicting, or rejected.",
    ("trade_episodes", "strict_comparison_eligible"): "One only for attributable executed episodes eligible for strict win/loss comparison.",
    ("setup_performance_rollups", "observed_win_rate"): "Descriptive selected-corpus win share; not a forecast or causal probability.",
    ("setup_performance_rollups", "excluded_count"): "Imported episodes excluded from this strict rollup denominator.",
    ("setup_model_matches", "match_status"): "Nature of model membership; derived may be signature-only.",
    ("setup_model_matches", "missing_rule_count"): "Rules absent or not evaluable for this instance under the stored rule-state audit.",
    ("setup_rule_states", "state"): "Present, absent, violated, or unknown evidence state for one stored rule on one instance.",
    ("answers", "resolution_status"): "Answered/partial/conflicting only when curated; community_only for linkage without answer proof.",
    ("relevance_annotations", "label"): "Current-pass selection label; raw_retained_not_curated is not a judgment of irrelevance.",
}


def dictionary_table_names(con: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        if not str(row[0]).endswith(TECHNICAL_TABLE_SUFFIXES)
    ]


def populate_data_dictionary(con: sqlite3.Connection) -> dict[str, Any]:
    con.execute("DELETE FROM data_dictionary")
    inserted = 0
    tables = dictionary_table_names(con)
    for table_name in tables:
        quoted = table_name.replace('"', '""')
        table_description = TABLE_DESCRIPTIONS.get(
            table_name,
            "Local database table; use claim/evidence fields and coverage metadata when interpreting it.",
        )
        source_or_derived = "source" if table_name in SOURCE_DATA_TABLES else "derived"
        for column in con.execute(f'PRAGMA table_info("{quoted}")'):
            column_name = str(column[1])
            description = COLUMN_DESCRIPTIONS.get(
                (table_name, column_name),
                f"{column_name} field in {table_name}. {table_description}",
            )
            if int(column[3]) == 1:
                null_semantics = "Not nullable in the database schema."
            elif column_name.endswith("_id"):
                null_semantics = "NULL means no captured or resolved linked entity; never infer the relationship from absence."
            elif "time" in column_name or column_name.endswith("_utc"):
                null_semantics = "NULL means no explicit or normalized time was stored; never infer a time or timezone."
            elif column_name.endswith("_json"):
                null_semantics = "NULL means no structured value was stored; consult linked text/evidence and do not infer one."
            else:
                null_semantics = "NULL means unknown, unstated, unresolved, or not applicable; never infer from absence."
            con.execute(
                """
                INSERT INTO data_dictionary(
                  table_name,column_name,description,null_semantics,source_or_derived
                ) VALUES(?,?,?,?,?)
                """,
                (table_name, column_name, description, null_semantics, source_or_derived),
            )
            inserted += 1
    return {"tables_documented": len(tables), "columns_documented": inserted}


def validate_analysis(con: sqlite3.Connection) -> dict[str, Any]:
    con.row_factory = sqlite3.Row
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    fk = [dict(row) for row in con.execute("PRAGMA foreign_key_check")]
    audits = [dict(row) for row in con.execute("SELECT * FROM v_discord_only_audit")]
    model_count = con.execute("SELECT COUNT(*) FROM setup_models").fetchone()[0]
    strict_bad = con.execute(
        """
        SELECT COUNT(*) FROM trade_outcome_resolution
        WHERE strict_comparison_eligible=1 AND resolved_outcome NOT IN ('win','loss')
        """
    ).fetchone()[0]
    instrument_overlap = con.execute(
        """
        SELECT COUNT(*) FROM setup_instruments a JOIN setup_instruments b
          ON a.instance_id=b.instance_id AND a.instrument_id=b.instrument_id
         AND a.role='executed' AND b.role='market_context'
        """
    ).fetchone()[0]
    rollup_bad = con.execute(
        """
        SELECT COUNT(*) FROM setup_performance_rollups
        WHERE eligible_count<>wins+losses+breakevens+unknowns
           OR not_causal<>1
           OR (eligible_count>0 AND ABS(observed_win_rate-(wins*1.0/eligible_count))>0.0000001)
           OR (eligible_count=0 AND observed_win_rate IS NOT NULL)
        """
    ).fetchone()[0]
    qa_bad = con.execute(
        """
        SELECT COUNT(*) FROM questions q
        WHERE q.resolution_status='answered'
          AND NOT EXISTS(
            SELECT 1 FROM question_answer_links l
            JOIN answers a ON a.answer_id=l.answer_id
            WHERE l.question_id=q.question_id AND a.resolution_status='answered'
          )
        """
    ).fetchone()[0]
    model_matrix_missing = con.execute(
        """
        SELECT COUNT(*)
        FROM setup_model_matches m
        JOIN setup_model_rules r ON r.model_id=m.model_id
        LEFT JOIN setup_rule_states s
          ON s.instance_id=m.instance_id AND s.rule_id=r.rule_id
        WHERE s.rule_id IS NULL
        """
    ).fetchone()[0]
    model_count_mismatch = con.execute(
        """
        SELECT COUNT(*)
        FROM setup_model_matches m
        WHERE m.matched_rule_count<>(
                SELECT COUNT(*) FROM setup_model_rules r
                JOIN setup_rule_states s ON s.rule_id=r.rule_id
                WHERE r.model_id=m.model_id AND s.instance_id=m.instance_id
                  AND s.state='present'
              )
           OR m.missing_rule_count<>(
                SELECT COUNT(*) FROM setup_model_rules r
                JOIN setup_rule_states s ON s.rule_id=r.rule_id
                WHERE r.model_id=m.model_id AND s.instance_id=m.instance_id
                  AND s.state IN ('absent','unknown')
              )
           OR m.violated_rule_count<>(
                SELECT COUNT(*) FROM setup_model_rules r
                JOIN setup_rule_states s ON s.rule_id=r.rule_id
                WHERE r.model_id=m.model_id AND s.instance_id=m.instance_id
                  AND s.state='violated'
              )
        """
    ).fetchone()[0]
    run_row = con.execute(
        """
        SELECT ar.analysis_run_id,cr.window_start_utc,cr.window_end_utc
        FROM analysis_runs ar
        JOIN collection_runs cr ON cr.run_id=ar.collection_run_id
        ORDER BY ar.analysis_run_id LIMIT 1
        """
    ).fetchone()
    relevance_bad = con.execute(
        """
        SELECT COUNT(*)
        FROM v_analysis_eligible_messages m
        WHERE m.created_at_utc>=? AND m.created_at_utc<?
          AND (
            SELECT COUNT(*) FROM relevance_annotations r
            WHERE r.message_id=m.message_id AND r.analysis_run_id=?
              AND r.label IN ('curated_relevant_evidence','raw_retained_not_curated')
          )<>1
        """,
        (run_row[1], run_row[2], run_row[0]),
    ).fetchone()[0]
    dictionary_missing = 0
    for table_name in dictionary_table_names(con):
        quoted = table_name.replace('"', '""')
        for column in con.execute(f'PRAGMA table_info("{quoted}")'):
            dictionary_missing += int(
                con.execute(
                    "SELECT 1 FROM data_dictionary WHERE table_name=? AND column_name=?",
                    (table_name, str(column[1])),
                ).fetchone() is None
            )
    contradiction_bad = con.execute(
        """
        SELECT COUNT(*) FROM contradiction_sets c
        WHERE (SELECT COUNT(*) FROM contradiction_members m
               WHERE m.contradiction_id=c.contradiction_id)<2
        """
    ).fetchone()[0]
    checks = {
        "integrity_ok": integrity == "ok",
        "foreign_keys_ok": not fk,
        "discord_only_audit_ok": not audits,
        "model_limit_ok": model_count <= MODEL_LIMIT,
        "strict_outcomes_only_win_loss": strict_bad == 0,
        "instrument_roles_not_collapsed": instrument_overlap == 0,
        "rollup_arithmetic_and_noncausal_guard_ok": rollup_bad == 0,
        "answered_questions_have_curated_answer": qa_bad == 0,
        "model_rule_matrix_complete": model_matrix_missing == 0,
        "model_rule_counts_reconcile_to_states": model_count_mismatch == 0,
        "eligible_messages_have_one_primary_relevance_label": relevance_bad == 0,
        "data_dictionary_covers_user_tables": dictionary_missing == 0,
        "contradiction_sets_have_multiple_members": contradiction_bad == 0,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "counts": {
            "analysis_runs": con.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0],
            "claims": con.execute("SELECT COUNT(*) FROM claims").fetchone()[0],
            "evidence_items": con.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0],
            "questions": con.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
            "answers": con.execute("SELECT COUNT(*) FROM answers").fetchone()[0],
            "trade_episodes": con.execute("SELECT COUNT(*) FROM trade_episodes").fetchone()[0],
            "strict_trade_episodes": con.execute("SELECT COUNT(*) FROM trade_episodes WHERE strict_comparison_eligible=1").fetchone()[0],
            "setup_models": model_count,
            "setup_features": con.execute("SELECT COUNT(*) FROM setup_features").fetchone()[0],
            "setup_rule_states": con.execute("SELECT COUNT(*) FROM setup_rule_states").fetchone()[0],
            "relevance_annotations": con.execute("SELECT COUNT(*) FROM relevance_annotations").fetchone()[0],
            "data_dictionary_columns": con.execute("SELECT COUNT(*) FROM data_dictionary").fetchone()[0],
            "contradiction_sets": con.execute("SELECT COUNT(*) FROM contradiction_sets").fetchone()[0],
        },
        "foreign_key_violations": fk,
        "discord_only_audit_issues": audits,
    }


def build_analysis(
    input_database: Path,
    output_database: Path,
    *,
    curated_path: Path,
    model_analysis_path: Path,
    trade_script: Path,
    rb_script: Path,
    model_script: Path,
    replace: bool,
    min_candidate_score: int,
) -> dict[str, Any]:
    for path in (input_database, curated_path, model_analysis_path, trade_script, rb_script, model_script):
        if not path.is_file():
            raise FileNotFoundError(path)
    building = copy_database(input_database, output_database, replace=replace)
    try:
        con = sqlite3.connect(building)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        assert_cardinal_v2(con)
        coverage = coverage_snapshot(con)
        rows, lookup = message_rows(con)
        curated = load_json(curated_path)
        model_analysis = load_json(model_analysis_path)
        if str(curated.get("source_scope") or "").lower().find("discord") < 0:
            raise AnalysisError("Curated input is not labeled Discord-only")
        legacy_trade = load_local_module("discord_legacy_trade_analysis", trade_script)
        legacy_rb = load_local_module("discord_legacy_rb_analysis", rb_script)
        legacy_model = load_local_module("discord_legacy_model_analysis", model_script)
        analysis_run_id = create_analysis_run(con, coverage)
        writer = Writer(con, analysis_run_id, lookup, coverage)
        episodes, extraction = build_trade_episodes(
            legacy_trade, rows, min_candidate_score=min_candidate_score
        )
        instance_map, trade_stats = import_trades(writer, episodes)
        rb = import_rb_findings(writer, curated, legacy_rb)
        qa = import_questions(writer, curated)
        contradictions = import_contradictions(writer, curated)
        model_discovery: dict[str, Any] = {}
        _model_map, cards = import_models(
            writer,
            model_analysis,
            legacy_model,
            episodes,
            instance_map,
            discovery_audit=model_discovery,
        )
        profiles = profile_rows(trade_stats["strict_episodes"], cards)
        add_profile_claims(writer, profiles)
        add_cohorts_and_rollups(writer, profiles, cards, int(trade_stats["imported"]))
        provenance = {
            "input_database": str(input_database.resolve()),
            "input_database_sha256": sha256_file(input_database),
            "curated_three_month_artifact": str(curated_path.resolve()),
            "curated_three_month_sha256": sha256_file(curated_path),
            "model_analysis_artifact": str(model_analysis_path.resolve()),
            "model_analysis_sha256": sha256_file(model_analysis_path),
            "legacy_scripts": {
                str(path.resolve()): sha256_file(path)
                for path in (trade_script, rb_script, model_script)
            },
            "legacy_artifacts_read_only": True,
        }
        extraction_summary = {
            **extraction,
            "episodes_extracted": len(episodes),
            "trade_import": {key: value for key, value in trade_stats.items() if key != "strict_episodes"},
            "strict_episode_count": len(trade_stats["strict_episodes"]),
        }
        relevance = populate_relevance_annotations(writer)
        dictionary = populate_data_dictionary(con)
        insert_documents(
            writer,
            coverage=coverage,
            rb=rb,
            qa=qa,
            contradictions=contradictions,
            relevance=relevance,
            dictionary=dictionary,
            profiles=profiles,
            models=cards,
            model_discovery=model_discovery,
            extraction=extraction_summary,
            provenance=provenance,
        )
        con.commit()
        validation = validate_analysis(con)
        if validation["status"] != "passed":
            raise AnalysisError(f"Analysis validation failed: {json_text(validation)}")
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
    except Exception:
        try:
            con.close()
        except Exception:
            pass
        if building.exists():
            building.unlink()
        raise
    output_database = output_database.resolve()
    if output_database.exists():
        if not replace:
            building.unlink(missing_ok=True)
            raise FileExistsError(output_database)
        output_database.unlink()
    os.replace(building, output_database)
    return {
        "status": "passed",
        "database": str(output_database),
        "database_sha256": sha256_file(output_database),
        "source_scope": SOURCE_SCOPE,
        "outside_sources_used": 0,
        "coverage": coverage,
        "rejection_block": rb,
        "qa": qa,
        "contradictions": contradictions,
        "relevance_annotations": relevance,
        "data_dictionary": dictionary,
        "trade_profiles": profiles,
        "model_cards": cards,
        "model_discovery": model_discovery,
        "trade_extraction": extraction_summary,
        "validation": validation,
        "provenance": provenance,
    }


def write_json_atomic(path: Path, value: Any, *, replace: bool) -> None:
    path = path.resolve()
    if path.exists() and not replace:
        raise FileExistsError(f"Report exists: {path}; use --replace explicitly")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    if path.exists():
        path.unlink()
    os.replace(temporary, path)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--database", required=True, type=Path, help="Pristine Cardinal v2 database")
    value.add_argument("--output", required=True, type=Path, help="New analyzed database")
    value.add_argument("--report", type=Path, help="Optional JSON analysis/validation report")
    value.add_argument("--curated", type=Path, default=DEFAULT_CURATED)
    value.add_argument("--model-analysis", type=Path, default=DEFAULT_MODEL_ANALYSIS)
    value.add_argument("--trade-script", type=Path, default=DEFAULT_TRADE_SCRIPT)
    value.add_argument("--rb-script", type=Path, default=DEFAULT_RB_SCRIPT)
    value.add_argument("--model-script", type=Path, default=DEFAULT_MODEL_SCRIPT)
    value.add_argument("--min-candidate-score", type=int, default=4)
    value.add_argument("--replace", action="store_true", help="Explicitly replace output/report files")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.min_candidate_score < 1:
        print("ERROR: --min-candidate-score must be >=1", file=sys.stderr)
        return 2
    try:
        report = build_analysis(
            args.database.resolve(),
            args.output.resolve(),
            curated_path=args.curated.resolve(),
            model_analysis_path=args.model_analysis.resolve(),
            trade_script=args.trade_script.resolve(),
            rb_script=args.rb_script.resolve(),
            model_script=args.model_script.resolve(),
            replace=args.replace,
            min_candidate_score=args.min_candidate_score,
        )
        if args.report:
            write_json_atomic(args.report, report, replace=args.replace)
        print(json.dumps({
            "status": report["status"],
            "database": report["database"],
            "database_sha256": report["database_sha256"],
            "coverage_status": report["coverage"]["analysis_completeness"],
            "counts": report["validation"]["counts"],
            "models": len(report["model_cards"]),
            "report": str(args.report.resolve()) if args.report else None,
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
