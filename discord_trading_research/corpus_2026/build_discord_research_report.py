#!/usr/bin/env python3
"""Build deterministic Markdown and JSON research reports from Cardinal SQLite.

The input must be the release-ready, analyzed database produced by
``build_discord_analysis_layer.py``.  This program reads SQLite only.  It does
not inspect raw exports, legacy artifacts, the web, or trading references.

The report deliberately preserves the analysis layer's epistemic limits:
selected-corpus outcome shares are descriptive, self-reported, overlapping,
author-clustered, non-causal, and never forward probabilities or expectancy.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REPORT_SCHEMA_VERSION = "1.0.0"
EXPECTED_GUILD_ID = "1167376964680691732"
EXPECTED_WINDOW_START_UTC = "2026-01-01T06:00:00Z"
EXPECTED_WINDOW_END_UTC = "2026-07-21T05:00:00Z"
MAX_MODELS = 5

REQUIRED_TABLES = {
    "meta",
    "analysis_runs",
    "analysis_documents",
    "analysis_entities",
    "claims",
    "claim_evidence",
    "evidence_items",
    "collection_runs",
    "collection_units",
    "coverage_segments",
    "channel_inventory",
    "source_artifacts",
    "messages",
    "attachments",
    "attachment_extractions",
    "questions",
    "question_answer_links",
    "answers",
    "answer_messages",
    "setup_models",
    "setup_model_rules",
    "setup_model_matches",
    "setup_instances",
    "setup_features",
    "concept_terms",
    "setup_instruments",
    "instruments",
    "trade_episodes",
    "trade_outcome_resolution",
    "contradiction_sets",
    "contradiction_members",
}

REQUIRED_VIEWS = {
    "v_discord_only_audit",
    "v_collection_gaps",
    "v_authority_separated_qa",
}

REQUIRED_DOCUMENTS = {
    "discord_analysis_coverage",
    "discord_analysis_methodology",
    "discord_model_cards",
    "discord_qa_catalog_summary",
    "discord_rejection_block_research",
    "discord_trade_profiles",
}

RATE_WARNING = (
    "Every rate in this report is descriptive within the selected Discord "
    "corpus, self-reported, overlapping where confluences or models overlap, "
    "author-clustered, non-causal, and not a forward probability or expectancy."
)

EVIDENCE_TIMESTAMP_WARNING = (
    "Evidence timestamps are Discord message-post timestamps only. They are "
    "never treated as setup times; setup times and sessions appear only when "
    "explicitly stated in the captured Discord text."
)


class ReportError(RuntimeError):
    """Raised when a release prerequisite or evidence invariant is absent."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        separators=(",", ": "),
    ) + "\n"


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def sorted_message_ids(values: Iterable[Any]) -> list[str]:
    unique = {str(value) for value in values if str(value or "").strip()}

    def key(value: str) -> tuple[int, int | str]:
        return (0, int(value)) if value.isdigit() else (1, value)

    return sorted(unique, key=key)


def grouped_counts(rows: Iterable[sqlite3.Row], field: str) -> dict[str, int]:
    counter = Counter(str(row[field] if row[field] is not None else "unknown") for row in rows)
    return dict(sorted(counter.items()))


def attachment_archive_summary(
    con: sqlite3.Connection,
    meta: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    metadata = (
        dict(meta)
        if meta is not None
        else {str(row[0]): str(row[1]) for row in con.execute("SELECT key,value FROM meta")}
    )
    capture_rows = list(
        con.execute(
            "SELECT capture_status,COUNT(*) AS row_count "
            "FROM attachments GROUP BY capture_status ORDER BY capture_status"
        )
    )
    extraction_rows = list(
        con.execute(
            "SELECT status,COUNT(*) AS row_count "
            "FROM attachment_extractions GROUP BY status ORDER BY status"
        )
    )
    capture_counts = {str(row[0]): int(row[1]) for row in capture_rows}
    extraction_counts = {str(row[0]): int(row[1]) for row in extraction_rows}
    owned_count = sum(capture_counts.values())
    terminal_complete = metadata.get("attachment_archive_terminal_coverage_complete") == "1"
    literal_complete = metadata.get("attachment_archive_literal_release_complete") == "1"
    if owned_count == 0:
        release_status = "not_required"
    elif terminal_complete and literal_complete:
        release_status = "complete"
    else:
        release_status = "incomplete"
    return {
        "owned_attachment_count": owned_count,
        "capture_status_counts": capture_counts,
        "queryable_verified_extraction_count": sum(extraction_counts.values()),
        "extraction_status_counts": extraction_counts,
        "terminal_coverage_complete": terminal_complete,
        "literal_release_complete": literal_complete,
        "byte_complete": metadata.get("attachment_archive_byte_complete") == "1",
        "release_status": release_status,
        "manifest_sha256": metadata.get("attachment_archive_manifest_sha256", ""),
        "chart_claim_policy": metadata.get("attachment_chart_claim_policy", ""),
        "attachments_auto_prove_chart_geometry": False,
    }


def chunks(values: Sequence[str], size: int = 800) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def parse_json(value: Any, *, label: str) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise ReportError(f"Invalid JSON in {label}: {exc}") from exc


def recursive_evidence_message_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "evidence_message_ids" and isinstance(child, list):
                found.update(str(item) for item in child if str(item or "").strip())
            else:
                found.update(recursive_evidence_message_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(recursive_evidence_message_ids(child))
    return found


def _sqlite_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def connect_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    con = sqlite3.connect(_sqlite_uri(path), uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def schema_objects(con: sqlite3.Connection, object_type: str) -> set[str]:
    return {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type=?", (object_type,)
        )
    }


def one_row(rows: list[sqlite3.Row], label: str) -> sqlite3.Row:
    if len(rows) != 1:
        raise ReportError(f"Expected exactly one {label}; found {len(rows)}")
    return rows[0]


def load_documents(
    con: sqlite3.Connection, analysis_run_id: int
) -> dict[str, dict[str, Any]]:
    rows = list(
        con.execute(
            """
            SELECT document_name,analysis_run_id,created_by,content_json,notes
            FROM analysis_documents
            ORDER BY document_name
            """
        )
    )
    names = {str(row["document_name"]) for row in rows}
    missing = sorted(REQUIRED_DOCUMENTS - names)
    extra_runs = sorted(
        {
            int(row["analysis_run_id"])
            for row in rows
            if int(row["analysis_run_id"]) != analysis_run_id
        }
    )
    if missing:
        raise ReportError(f"Missing required analysis documents: {', '.join(missing)}")
    if extra_runs:
        raise ReportError(
            "Analysis documents are not isolated to the selected analysis run: "
            + ", ".join(map(str, extra_runs))
        )
    return {
        str(row["document_name"]): parse_json(
            row["content_json"], label=f"analysis document {row['document_name']}"
        )
        for row in rows
    }


def validate_release(
    con: sqlite3.Connection,
    *,
    expected_guild_id: str,
    expected_window_start_utc: str,
    expected_window_end_utc: str,
) -> tuple[sqlite3.Row, sqlite3.Row, dict[str, dict[str, Any]], dict[str, Any]]:
    tables = schema_objects(con, "table")
    views = schema_objects(con, "view")
    missing_tables = sorted(REQUIRED_TABLES - tables)
    missing_views = sorted(REQUIRED_VIEWS - views)
    if missing_tables or missing_views:
        parts = []
        if missing_tables:
            parts.append("tables=" + ",".join(missing_tables))
        if missing_views:
            parts.append("views=" + ",".join(missing_views))
        raise ReportError("Analyzed Cardinal schema is incomplete: " + "; ".join(parts))

    integrity = str(con.execute("PRAGMA integrity_check").fetchone()[0])
    if integrity != "ok":
        raise ReportError(f"SQLite integrity_check failed: {integrity}")
    foreign_keys = list(con.execute("PRAGMA foreign_key_check"))
    if foreign_keys:
        raise ReportError(f"SQLite foreign_key_check found {len(foreign_keys)} violation(s)")

    meta = {str(row[0]): str(row[1]) for row in con.execute("SELECT key,value FROM meta")}
    if meta.get("source_scope") != "discord_only":
        raise ReportError("meta.source_scope must be discord_only")
    attachment_archive = attachment_archive_summary(con, meta)
    if attachment_archive["owned_attachment_count"] > 0 and (
        not attachment_archive["terminal_coverage_complete"]
        or not attachment_archive["literal_release_complete"]
    ):
        raise ReportError(
            "Attachment archive is not release-complete for owned Discord attachments: "
            f"count={attachment_archive['owned_attachment_count']}, "
            "terminal_coverage_complete="
            f"{attachment_archive['terminal_coverage_complete']}, "
            "literal_release_complete="
            f"{attachment_archive['literal_release_complete']}"
        )

    analysis_run = one_row(
        list(con.execute("SELECT * FROM analysis_runs ORDER BY analysis_run_id")),
        "analysis run",
    )
    if str(analysis_run["source_scope"]) != "discord_only":
        raise ReportError("analysis_runs.source_scope must be discord_only")
    if int(analysis_run["outside_sources_used"]) != 0:
        raise ReportError("analysis run records outside_sources_used != 0")

    collection_run = one_row(
        list(
            con.execute(
                "SELECT * FROM collection_runs WHERE run_id=?",
                (int(analysis_run["collection_run_id"]),),
            )
        ),
        "collection run linked to the analysis run",
    )
    if str(collection_run["source_scope"]) != "discord_only":
        raise ReportError("collection_runs.source_scope must be discord_only")
    if int(collection_run["outside_sources_used"]) != 0:
        raise ReportError("collection run records outside_sources_used != 0")
    if str(collection_run["status"]).lower() != "complete":
        raise ReportError(
            f"Collection run is not release-complete: {collection_run['status']}"
        )
    if str(collection_run["guild_id"]) != expected_guild_id:
        raise ReportError(
            f"Guild mismatch: expected {expected_guild_id}, got {collection_run['guild_id']}"
        )
    if str(collection_run["window_start_utc"]) != expected_window_start_utc:
        raise ReportError(
            "Window-start mismatch: expected "
            f"{expected_window_start_utc}, got {collection_run['window_start_utc']}"
        )
    if str(collection_run["window_end_utc"]) != expected_window_end_utc:
        raise ReportError(
            "Window-end mismatch: expected "
            f"{expected_window_end_utc}, got {collection_run['window_end_utc']}"
        )

    documents = load_documents(con, int(analysis_run["analysis_run_id"]))
    coverage = documents["discord_analysis_coverage"]
    if str(coverage.get("analysis_completeness")) != "complete":
        raise ReportError(
            "Analysis coverage is not complete: "
            f"{coverage.get('analysis_completeness')!r}"
        )
    if str(coverage.get("collection_run_status")) != "complete":
        raise ReportError(
            "Analysis coverage document does not record a complete collection run"
        )
    if int(coverage.get("gap_count") or 0) != 0:
        raise ReportError(
            f"Analysis coverage document records {coverage.get('gap_count')} gap(s)"
        )

    gaps = list(con.execute("SELECT * FROM v_collection_gaps"))
    if gaps:
        raise ReportError(f"v_collection_gaps contains {len(gaps)} unresolved row(s)")
    audit = [dict(row) for row in con.execute("SELECT * FROM v_discord_only_audit")]
    if audit:
        raise ReportError(
            "Discord-only audit failed: " + compact_json(audit[:10])
        )

    methodology = documents["discord_analysis_methodology"]
    if str(methodology.get("source_scope")) != "discord_only":
        raise ReportError("Methodology document is not Discord-only")
    if int(methodology.get("outside_sources_used") or 0) != 0:
        raise ReportError("Methodology document records outside sources")

    timing_policy = str(
        documents["discord_rejection_block_research"].get("timing_policy") or ""
    ).lower()
    if "posting time" not in timing_policy or "never" not in timing_policy:
        raise ReportError(
            "Rejection-block timing policy does not forbid setup-time inference "
            "from Discord post timestamps"
        )
    invalidation_policy = str(
        documents["discord_rejection_block_research"].get("invalidation_policy") or ""
    ).lower()
    if "technical invalidation" not in invalidation_policy or "non-actionability" not in invalidation_policy:
        raise ReportError(
            "Rejection-block analysis does not preserve technical invalidation "
            "and non-actionability as separate concepts"
        )

    trade = documents["discord_trade_profiles"]
    warning = str(trade.get("global_warning") or "").lower()
    for token in ("descriptive", "self-reported", "non-causal"):
        if token not in warning:
            raise ReportError(
                f"Trade-profile global warning is missing required label: {token}"
            )
    if not isinstance(trade.get("executed_instrument_comparison"), list):
        raise ReportError("Executed-instrument comparison is absent")
    if not isinstance(trade.get("market_context_instrument_mentions"), list):
        raise ReportError("Market-context instrument comparison is absent")

    model_document = documents["discord_model_cards"]
    models = model_document.get("models")
    if not isinstance(models, list):
        raise ReportError("Model-card analysis document has no models list")
    table_model_count = int(con.execute("SELECT COUNT(*) FROM setup_models").fetchone()[0])
    declared_model_count = int(model_document.get("models_emitted") or 0)
    if len(models) != declared_model_count or table_model_count != len(models):
        raise ReportError(
            "Model-card document/table count mismatch: "
            f"document={len(models)}, declared={declared_model_count}, table={table_model_count}"
        )
    if len(models) > MAX_MODELS:
        raise ReportError(f"Model limit exceeded: {len(models)} > {MAX_MODELS}")
    discovery = model_document.get("discovery")
    if not isinstance(discovery, dict) or not discovery:
        raise ReportError("Model-card document lacks the full-window discovery audit")
    if int(discovery.get("models_emitted") or 0) != len(models):
        raise ReportError(
            "Model discovery/document count mismatch: "
            f"discovery={discovery.get('models_emitted')}, document={len(models)}"
        )
    if int(discovery.get("maximum_models") or 0) != MAX_MODELS:
        raise ReportError("Model discovery audit does not preserve the five-model ceiling")
    if bool(discovery.get("fifth_model_forced")):
        raise ReportError("Model discovery audit indicates that a fifth model was forced")
    novel_discovery = discovery.get("novel_candidate_discovery")
    if not isinstance(novel_discovery, dict) or not normalized_text(
        novel_discovery.get("method")
    ):
        raise ReportError("Model discovery audit lacks its deterministic full-window method")

    answered_without_links = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM questions AS q
            WHERE q.resolution_status='answered'
              AND NOT EXISTS(
                SELECT 1 FROM question_answer_links AS l
                JOIN answers AS a ON a.answer_id=l.answer_id
                WHERE l.question_id=q.question_id
                  AND a.resolution_status='answered'
              )
            """
        ).fetchone()[0]
    )
    if answered_without_links:
        raise ReportError(
            f"{answered_without_links} answered question(s) lack a curated answered reply"
        )

    validation = {
        "status": "passed",
        "integrity_check": "ok",
        "foreign_key_violations": 0,
        "discord_only_audit_issues": 0,
        "collection_gaps": 0,
        "analysis_completeness": "complete",
        "model_count_within_limit": True,
        "answered_questions_have_links": True,
        "window_matches_requested_local_dates": True,
        "attachment_archive": attachment_archive,
    }
    return analysis_run, collection_run, documents, validation


def claim_evidence_map(
    con: sqlite3.Connection, claim_ids: Iterable[str]
) -> dict[str, list[str]]:
    output: dict[str, list[str]] = defaultdict(list)
    values = sorted({str(value) for value in claim_ids if value})
    for part in chunks(values):
        placeholders = ",".join("?" for _ in part)
        rows = con.execute(
            f"""
            SELECT ce.claim_id,ei.message_id
            FROM claim_evidence AS ce
            JOIN evidence_items AS ei ON ei.evidence_id=ce.evidence_id
            WHERE ce.claim_id IN ({placeholders}) AND ei.message_id IS NOT NULL
            ORDER BY ce.claim_id,ei.message_id
            """,
            tuple(part),
        )
        for row in rows:
            message_id = str(row["message_id"])
            if message_id not in output[str(row["claim_id"])]:
                output[str(row["claim_id"])].append(message_id)
    return output


def rb_claim_rows(con: sqlite3.Connection, analysis_run_id: int) -> list[dict[str, Any]]:
    rows = list(
        con.execute(
            """
            SELECT c.*,e.entity_type
            FROM claims AS c
            JOIN analysis_entities AS e ON e.entity_id=c.subject_entity_id
            WHERE c.analysis_run_id=?
              AND e.entity_type IN ('rejection_block_finding','rejection_block_observation')
            ORDER BY c.facet,c.claim_id
            """,
            (analysis_run_id,),
        )
    )
    evidence = claim_evidence_map(con, [str(row["claim_id"]) for row in rows])
    output = []
    for row in rows:
        claim_message_ids = evidence.get(str(row["claim_id"]), [])
        if not claim_message_ids:
            raise ReportError(
                f"Rejection-block claim {row['claim_id']} has no message evidence"
            )
        normalized = None
        if row["normalized_value_json"]:
            normalized = parse_json(
                row["normalized_value_json"], label=f"claim {row['claim_id']} normalized value"
            )
        output.append(
            {
                "claim_id": str(row["claim_id"]),
                "entity_type": str(row["entity_type"]),
                "source_facet": str(row["facet"]),
                "claim_text": str(row["claim_text"]),
                "claim_kind": str(row["claim_kind"]),
                "epistemic_status": str(row["epistemic_status"]),
                "resolution_status": str(row["resolution_status"]),
                "normalized_value": normalized,
                "limitations": str(row["limitations"] or ""),
                "evidence_message_ids": claim_message_ids,
            }
        )
    return output


def facet_bucket(facet: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", facet.lower()).strip("_")
    if "invalidation_or_non_actionability" in normalized:
        return "unclassified_invalidation_or_non_actionability"
    if "non_action" in normalized or "no_trade" in normalized:
        return "non_actionability"
    if normalized == "invalidation" or normalized.startswith("technical_invalidation"):
        return "technical_invalidation"
    if normalized.startswith("invalidation_"):
        return "technical_invalidation"
    if any(token in normalized for token in ("identification", "definition", "formation")):
        return "identification"
    if any(token in normalized for token in ("timing", "session", "time_marker")):
        return "timing"
    if "confluence" in normalized or "quality" in normalized:
        return "confluence_or_quality"
    return "other"


def build_rejection_block_section(
    rb_document: dict[str, Any], claims: list[dict[str, Any]]
) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "identification": [],
        "technical_invalidation": [],
        "non_actionability": [],
        "unclassified_invalidation_or_non_actionability": [],
        "timing": [],
        "confluence_or_quality": [],
        "other": [],
    }
    for claim in claims:
        row = copy.deepcopy(claim)
        row["classification_basis"] = "source_facet_only"
        buckets[facet_bucket(str(row["source_facet"]))].append(row)

    components = rb_document.get("whole_corpus_textual_components") or {}
    if not isinstance(components, dict):
        raise ReportError("Rejection-block component catalog is malformed")

    def component_rows(name: str) -> list[dict[str, Any]]:
        values = components.get(name) or []
        if not isinstance(values, list):
            raise ReportError(f"Rejection-block component group {name!r} is not a list")
        return sorted(
            [copy.deepcopy(value) for value in values],
            key=lambda row: (-int(row.get("message_count") or 0), str(row.get("component") or "")),
        )

    return {
        "term_message_count": int(rb_document.get("rb_term_message_count") or 0),
        "identification": {
            "source_claims": buckets["identification"],
            "textual_components": component_rows("identification"),
        },
        "invalidation": {
            "policy": str(rb_document.get("invalidation_policy") or ""),
            "technical_invalidation_source_claims": buckets["technical_invalidation"],
            "non_actionability_source_claims": buckets["non_actionability"],
            "unclassified_combined_source_claims": buckets[
                "unclassified_invalidation_or_non_actionability"
            ],
            "unclassified_textual_components": component_rows(
                "invalidation_or_non_actionability"
            ),
            "classification_guard": (
                "Claims are separated only when their stored source facet separates "
                "them. Combined facets are never split by interpreting claim text."
            ),
        },
        "times_and_sessions": {
            "policy": str(rb_document.get("timing_policy") or ""),
            "source_claims": buckets["timing"],
            "explicit_textual_components": component_rows("timing"),
            "post_timestamp_used_as_setup_time": False,
        },
        "quality_and_confluences": {
            "source_claims": buckets["confluence_or_quality"],
            "textual_components": component_rows("confluence"),
            "interpretation": (
                "These are Discord RB-text co-mentions. They are not objective "
                "formation frequencies or outcome probabilities."
            ),
        },
        "other_source_claims": buckets["other"],
        "legacy_findings_imported": int(rb_document.get("legacy_findings_imported") or 0),
        "legacy_findings_missing_all_evidence": int(
            rb_document.get("legacy_findings_missing_all_evidence") or 0
        ),
    }


def build_models(
    con: sqlite3.Connection, model_document: dict[str, Any]
) -> list[dict[str, Any]]:
    table_rows = {
        str(row["model_id"]): dict(row)
        for row in con.execute("SELECT * FROM setup_models ORDER BY canonical_name,model_id")
    }
    rule_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in con.execute(
        "SELECT * FROM setup_model_rules ORDER BY model_id,rule_order,rule_id"
    ):
        rule_rows[str(row["model_id"])].append(dict(row))
    match_counts = {
        str(row["model_id"]): int(row["n"])
        for row in con.execute(
            "SELECT model_id,COUNT(*) AS n FROM setup_model_matches GROUP BY model_id"
        )
    }
    model_claim_ids = [
        str(row["identity_claim_id"])
        for row in table_rows.values()
        if row.get("identity_claim_id")
    ] + [
        str(rule["claim_id"])
        for values in rule_rows.values()
        for rule in values
        if rule.get("claim_id")
    ]
    model_evidence = claim_evidence_map(con, model_claim_ids)
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in model_document.get("models") or []:
        model_id = str(source.get("model_id") or "")
        if not model_id or model_id not in table_rows:
            raise ReportError(f"Model document references unknown model_id {model_id!r}")
        if model_id in seen:
            raise ReportError(f"Duplicate model card for {model_id}")
        seen.add(model_id)
        table = table_rows[model_id]
        identity_claim_id = str(table["identity_claim_id"])
        identity_evidence = model_evidence.get(identity_claim_id, [])
        if not identity_evidence:
            raise ReportError(f"Model {model_id} has no identity-claim message evidence")
        card = copy.deepcopy(source)
        card.update(
            {
                "model_id": model_id,
                "name": str(table["canonical_name"]),
                "thesis": str(table["thesis"] or ""),
                "evidence_status": str(table["evidence_status"]),
                "lifecycle_status": str(table["lifecycle_status"]),
                "limitations": str(table["limitations"] or ""),
                "identity_claim_id": identity_claim_id,
                "evidence_message_ids": identity_evidence,
                "database_rules": [
                    {
                        "rule_id": str(rule["rule_id"]),
                        "order": int(rule["rule_order"]),
                        "type": str(rule["rule_type"]),
                        "text": str(rule["rule_text"]),
                        "required_state": str(rule["required_state"]),
                        "claim_id": str(rule["claim_id"]),
                        "evidence_message_ids": model_evidence.get(
                            str(rule["claim_id"]), []
                        ),
                    }
                    for rule in rule_rows.get(model_id, [])
                ],
                "database_match_count": match_counts.get(model_id, 0),
                "forward_probability_claimed": False,
            }
        )
        output.append(card)
    return output


def build_questions(con: sqlite3.Connection) -> dict[str, Any]:
    authority: dict[tuple[str, str], dict[str, Any]] = {}
    for row in con.execute(
        """
        SELECT * FROM v_authority_separated_qa
        ORDER BY question_id,answer_id,authority_class
        """
    ):
        if row["answer_id"] is None:
            continue
        key = (str(row["question_id"]), str(row["answer_id"]))
        authority.setdefault(
            key,
            {
                "authority_class": str(row["authority_class"] or "unresolved"),
                "authority_basis": str(row["authority_basis"] or ""),
                "authority_confidence": row["authority_confidence"],
            },
        )

    answer_messages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in con.execute(
        """
        SELECT answer_id,message_id,sequence_order,message_role
        FROM answer_messages
        ORDER BY answer_id,sequence_order,message_id
        """
    ):
        answer_messages[str(row["answer_id"])].append(
            {
                "message_id": str(row["message_id"]),
                "sequence_order": int(row["sequence_order"]),
                "message_role": str(row["message_role"]),
            }
        )

    links: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in con.execute(
        """
        SELECT l.*,a.answer_summary,a.resolution_status AS answer_resolution_status,
               a.answer_claim_id
        FROM question_answer_links AS l
        JOIN answers AS a ON a.answer_id=l.answer_id
        ORDER BY l.question_id,l.answer_id
        """
    ):
        question_id = str(row["question_id"])
        answer_id = str(row["answer_id"])
        messages = answer_messages.get(answer_id, [])
        if not messages:
            raise ReportError(f"Linked answer {answer_id} has no answer message")
        item = {
            "answer_id": answer_id,
            "answer_summary": str(row["answer_summary"]),
            "resolution_status": str(row["answer_resolution_status"]),
            "answer_claim_id": str(row["answer_claim_id"]),
            "link_claim_id": str(row["claim_id"]),
            "link_type": str(row["link_type"]),
            "direct_reply": bool(row["direct_reply"]),
            "linkage_confidence": float(row["linkage_confidence"]),
            "authority": authority.get(
                (question_id, answer_id),
                {
                    "authority_class": "unresolved",
                    "authority_basis": "No captured authority assignment in the analyzed database.",
                    "authority_confidence": None,
                },
            ),
            "answer_messages": messages,
            "evidence_message_ids": [value["message_id"] for value in messages],
        }
        links[question_id].append(item)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in con.execute(
        """
        SELECT * FROM questions
        ORDER BY resolution_status,topic,subtopic,question_id
        """
    ):
        question_id = str(row["question_id"])
        status = str(row["resolution_status"])
        answers_for_question = links.get(question_id, [])
        if status == "answered" and not any(
            answer["resolution_status"] == "answered"
            for answer in answers_for_question
        ):
            raise ReportError(
                f"Answered question {question_id} has no curated answered reply"
            )
        if status == "unanswered" and answers_for_question:
            raise ReportError(f"Unanswered question {question_id} has linked answer rows")
        item = {
            "question_id": question_id,
            "primary_message_id": str(row["primary_message_id"]),
            "normalized_question": str(row["normalized_question"]),
            "topic": str(row["topic"]),
            "subtopic": str(row["subtopic"] or ""),
            "resolution_status": status,
            "question_claim_id": str(row["question_claim_id"]),
            "answers": answers_for_question,
            "evidence_message_ids": [str(row["primary_message_id"])] + [
                message_id
                for answer in answers_for_question
                for message_id in answer["evidence_message_ids"]
            ],
        }
        groups[status].append(item)

    ordered_statuses = ("answered", "partial", "conflicting", "ambiguous", "unanswered")
    grouped = {status: groups.get(status, []) for status in ordered_statuses}
    for status in sorted(set(groups) - set(ordered_statuses)):
        grouped[status] = groups[status]
    return {
        "status_counts": {key: len(value) for key, value in grouped.items()},
        "questions_by_status": grouped,
        "unanswered_interpretation": (
            "Unanswered means no captured linked answer in the released coverage; "
            "it does not establish that no answer exists outside the capture."
        ),
    }


def build_contradictions(con: sqlite3.Connection) -> list[dict[str, Any]]:
    members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = list(
        con.execute(
            """
            SELECT cm.contradiction_id,cm.claim_id,cm.stance,cm.notes,
                   c.facet,c.claim_text,c.claim_kind,c.epistemic_status,
                   c.resolution_status,c.limitations
            FROM contradiction_members AS cm
            JOIN claims AS c ON c.claim_id=cm.claim_id
            ORDER BY cm.contradiction_id,cm.stance,cm.claim_id
            """
        )
    )
    evidence = claim_evidence_map(con, [str(row["claim_id"]) for row in rows])
    for row in rows:
        member_evidence = evidence.get(str(row["claim_id"]), [])
        if not member_evidence:
            raise ReportError(
                f"Contradiction member claim {row['claim_id']} has no message evidence"
            )
        members[str(row["contradiction_id"])].append(
            {
                "claim_id": str(row["claim_id"]),
                "stance": str(row["stance"]),
                "member_notes": str(row["notes"] or ""),
                "source_facet": str(row["facet"]),
                "claim_text": str(row["claim_text"]),
                "claim_kind": str(row["claim_kind"]),
                "epistemic_status": str(row["epistemic_status"]),
                "resolution_status": str(row["resolution_status"]),
                "limitations": str(row["limitations"] or ""),
                "evidence_message_ids": member_evidence,
            }
        )
    output = []
    for row in con.execute("SELECT * FROM contradiction_sets ORDER BY topic,contradiction_id"):
        contradiction_members = members.get(str(row["contradiction_id"]), [])
        if not contradiction_members:
            raise ReportError(
                f"Contradiction set {row['contradiction_id']} has no evidence-bearing members"
            )
        output.append(
            {
                "contradiction_id": str(row["contradiction_id"]),
                "topic": str(row["topic"]),
                "resolution_status": str(row["resolution_status"]),
                "resolution_summary": str(row["resolution_summary"] or ""),
                "resolved_claim_id": str(row["resolved_claim_id"] or ""),
                "limitations": str(row["limitations"] or ""),
                "members": contradiction_members,
            }
        )
    return output


def build_strict_trade_evidence(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = list(
        con.execute(
            """
            SELECT te.trade_id,te.instance_id,te.trader_id,te.trade_date_text,
                   te.execution_mode,te.episode_kind,te.linkage_status,
                   te.episode_claim_id,tor.resolved_outcome_claim_id,
                   tor.resolved_outcome,tor.resolution_status,tor.resolution_reason,
                   si.primary_message_id
            FROM trade_episodes AS te
            JOIN trade_outcome_resolution AS tor ON tor.trade_id=te.trade_id
            JOIN setup_instances AS si ON si.instance_id=te.instance_id
            WHERE te.strict_comparison_eligible=1
              AND tor.strict_comparison_eligible=1
              AND tor.resolved_outcome IN ('win','loss')
            ORDER BY te.trade_date_text,te.trade_id
            """
        )
    )
    claim_ids = [
        str(value)
        for row in rows
        for value in (row["episode_claim_id"], row["resolved_outcome_claim_id"])
        if value
    ]
    evidence = claim_evidence_map(con, claim_ids)
    features: dict[str, list[str]] = defaultdict(list)
    for row in con.execute(
        """
        SELECT sf.instance_id,ct.canonical_name
        FROM setup_features AS sf
        JOIN concept_terms AS ct ON ct.term_id=sf.term_id
        ORDER BY sf.instance_id,ct.canonical_name
        """
    ):
        value = str(row["canonical_name"])
        if value not in features[str(row["instance_id"])]:
            features[str(row["instance_id"])].append(value)
    instruments: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in con.execute(
        """
        SELECT si.instance_id,i.canonical_symbol,si.role,si.raw_text,si.claim_id
        FROM setup_instruments AS si
        JOIN instruments AS i ON i.instrument_id=si.instrument_id
        ORDER BY si.instance_id,si.role,i.canonical_symbol,si.claim_id
        """
    ):
        instruments[str(row["instance_id"])].append(
            {
                "canonical_symbol": str(row["canonical_symbol"]),
                "role": str(row["role"]),
                "raw_text": str(row["raw_text"] or ""),
                "claim_id": str(row["claim_id"]),
            }
        )
    output = []
    for row in rows:
        ids = [str(row["primary_message_id"])]
        ids.extend(evidence.get(str(row["episode_claim_id"]), []))
        ids.extend(evidence.get(str(row["resolved_outcome_claim_id"]), []))
        output.append(
            {
                "trade_id": str(row["trade_id"]),
                "instance_id": str(row["instance_id"]),
                "trader_author_key": str(row["trader_id"] or ""),
                "trade_date_text": str(row["trade_date_text"] or ""),
                "execution_mode": str(row["execution_mode"]),
                "episode_kind": str(row["episode_kind"]),
                "linkage_status": str(row["linkage_status"]),
                "resolved_outcome": str(row["resolved_outcome"]),
                "resolution_status": str(row["resolution_status"]),
                "resolution_reason": str(row["resolution_reason"] or ""),
                "episode_claim_id": str(row["episode_claim_id"]),
                "outcome_claim_id": str(row["resolved_outcome_claim_id"]),
                "confluences": features.get(str(row["instance_id"]), []),
                "instrument_roles": instruments.get(str(row["instance_id"]), []),
                "evidence_message_ids": sorted_message_ids(ids),
            }
        )
    return output


def build_instrument_role_evidence(
    con: sqlite3.Connection, strict_trades: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    strict_by_instance = {str(row["instance_id"]): row for row in strict_trades}
    rows = list(
        con.execute(
            """
            SELECT si.instance_id,i.canonical_symbol,si.role,si.raw_text,si.claim_id
            FROM setup_instruments AS si
            JOIN instruments AS i ON i.instrument_id=si.instrument_id
            ORDER BY si.role,i.canonical_symbol,si.instance_id,si.claim_id
            """
        )
    )
    claim_ids = [
        str(row["claim_id"])
        for row in rows
        if str(row["instance_id"]) in strict_by_instance
    ]
    evidence = claim_evidence_map(con, claim_ids)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        instance_id = str(row["instance_id"])
        if instance_id not in strict_by_instance:
            continue
        role = str(row["role"])
        symbol = str(row["canonical_symbol"])
        key = (role, symbol)
        item = grouped.setdefault(
            key,
            {
                "role": role,
                "canonical_symbol": symbol,
                "strict_episode_count": 0,
                "wins": 0,
                "losses": 0,
                "rejection_block_episode_count": 0,
                "instance_ids": [],
                "claim_ids": [],
                "evidence_message_ids": [],
                "interpretation": (
                    "Exact stored instrument symbol and role for strict episodes; "
                    "no unstored instrument-family mapping is added by the report."
                ),
            },
        )
        if instance_id not in item["instance_ids"]:
            item["instance_ids"].append(instance_id)
            item["strict_episode_count"] += 1
            outcome = str(strict_by_instance[instance_id]["resolved_outcome"])
            item["wins" if outcome == "win" else "losses"] += 1
            if "rejection_block" in strict_by_instance[instance_id].get("confluences", []):
                item["rejection_block_episode_count"] += 1
        claim_id = str(row["claim_id"])
        if claim_id not in item["claim_ids"]:
            item["claim_ids"].append(claim_id)
        for message_id in evidence.get(claim_id, []):
            if message_id not in item["evidence_message_ids"]:
                item["evidence_message_ids"].append(message_id)
    return [grouped[key] for key in sorted(grouped)]


def coverage_summary(
    con: sqlite3.Connection,
    collection_run: sqlite3.Row,
    coverage_document: dict[str, Any],
) -> dict[str, Any]:
    inventory = list(
        con.execute(
            "SELECT * FROM channel_inventory ORDER BY kind,name,channel_id"
        )
    )
    units = list(
        con.execute(
            "SELECT * FROM collection_units ORDER BY unit_type,collection_name,unit_id"
        )
    )
    segments = list(
        con.execute(
            "SELECT * FROM coverage_segments ORDER BY segment_start_utc,segment_id"
        )
    )
    message_row = con.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN eligible_for_accepted_evidence=1 THEN 1 ELSE 0 END) AS eligible,
               SUM(CASE WHEN has_quarantined_occurrences=1 THEN 1 ELSE 0 END) AS quarantined,
               SUM(CASE WHEN message_id_exact=1 THEN 1 ELSE 0 END) AS exact_ids,
               MIN(created_at_utc) AS earliest,
               MAX(created_at_utc) AS latest
        FROM messages
        """
    ).fetchone()
    source_rows = list(
        con.execute(
            """
            SELECT artifact_id,sha256,collection_method,collection_name,
                   captured_at_utc,declared_artifact_complete
            FROM source_artifacts
            ORDER BY artifact_id
            """
        )
    )
    attachment_archive = attachment_archive_summary(con)
    return {
        "guild_id": str(collection_run["guild_id"]),
        "guild_name": str(collection_run["guild_name"] or ""),
        "window_start_utc": str(collection_run["window_start_utc"]),
        "window_end_utc": str(collection_run["window_end_utc"]),
        "scope": str(collection_run["scope"]),
        "collection_status": str(collection_run["status"]),
        "source_scope": str(collection_run["source_scope"]),
        "outside_sources_used": int(collection_run["outside_sources_used"]),
        "collection_methodology": str(collection_run["methodology"] or ""),
        "collection_limitations": str(collection_run["limitations"] or ""),
        "analysis_completeness": str(coverage_document.get("analysis_completeness")),
        "gap_count": int(coverage_document.get("gap_count") or 0),
        "attachment_archive": attachment_archive,
        "channel_inventory": {
            "total_records": len(inventory),
            "exact_id_records": sum(int(row["exact_id_known"] or 0) == 1 for row in inventory),
            "unresolved_id_records": sum(int(row["exact_id_known"] or 0) != 1 for row in inventory),
            "accessible_records": sum(int(row["is_accessible"] or 0) == 1 for row in inventory),
            "archived_records": sum(int(row["is_archived"] or 0) == 1 for row in inventory),
            "by_kind": grouped_counts(inventory, "kind"),
            "by_inventory_basis": grouped_counts(inventory, "inventory_basis"),
            "records": [
                {
                    "channel_id": str(row["channel_id"]),
                    "parent_channel_id": str(row["parent_channel_id"] or ""),
                    "name": str(row["name"] or ""),
                    "kind": str(row["kind"] or ""),
                    "exact_id_known": bool(row["exact_id_known"]),
                    "is_archived": (
                        None if row["is_archived"] is None else bool(row["is_archived"])
                    ),
                    "is_accessible": (
                        None if row["is_accessible"] is None else bool(row["is_accessible"])
                    ),
                    "inventory_basis": str(row["inventory_basis"] or ""),
                    "discovered_at_utc": str(row["discovered_at_utc"] or ""),
                    "first_seen_utc": str(row["first_seen_utc"] or ""),
                    "last_seen_utc": str(row["last_seen_utc"] or ""),
                }
                for row in inventory
            ],
        },
        "collection_units": {
            "total": len(units),
            "by_status": grouped_counts(units, "status"),
            "by_type": grouped_counts(units, "unit_type"),
            "by_collection_method": grouped_counts(units, "collection_method"),
            "reported_unique_messages_sum": sum(int(row["unique_messages_seen"] or 0) for row in units),
            "records": [
                {
                    "unit_id": str(row["unit_id"]),
                    "channel_id": str(row["channel_id"]),
                    "collection_name": str(row["collection_name"] or ""),
                    "unit_type": str(row["unit_type"] or ""),
                    "window_start_utc": str(row["window_start_utc"]),
                    "window_end_utc": str(row["window_end_utc"]),
                    "collection_method": str(row["collection_method"] or ""),
                    "query_text": str(row["query_text"] or ""),
                    "status": str(row["status"]),
                    "artifact_declared_complete": bool(row["artifact_declared_complete"]),
                    "occurrences_seen": int(row["occurrences_seen"] or 0),
                    "unique_messages_seen": int(row["unique_messages_seen"] or 0),
                    "earliest_message_utc": str(row["earliest_message_utc"] or ""),
                    "latest_message_utc": str(row["latest_message_utc"] or ""),
                    "gap_notes": str(row["gap_notes"] or ""),
                }
                for row in units
            ],
        },
        "coverage_segments": {
            "total": len(segments),
            "by_status": grouped_counts(segments, "status"),
            "returned_count_sum": sum(int(row["returned_count"] or 0) for row in segments),
            "duplicate_count_sum": sum(int(row["duplicate_count"] or 0) for row in segments),
            "records": [
                {
                    "segment_id": str(row["segment_id"]),
                    "unit_id": str(row["unit_id"]),
                    "segment_start_utc": str(row["segment_start_utc"]),
                    "segment_end_utc": str(row["segment_end_utc"]),
                    "status": str(row["status"]),
                    "returned_count": int(row["returned_count"] or 0),
                    "first_message_id": str(row["first_message_id"] or ""),
                    "last_message_id": str(row["last_message_id"] or ""),
                    "duplicate_count": int(row["duplicate_count"] or 0),
                    "error_text": str(row["error_text"] or ""),
                    "artifact_sha256": str(row["artifact_sha256"] or ""),
                    "notes": str(row["notes"] or ""),
                }
                for row in segments
            ],
        },
        "messages": {
            "total": int(message_row["total"] or 0),
            "eligible_for_accepted_evidence": int(message_row["eligible"] or 0),
            "with_quarantined_occurrences": int(message_row["quarantined"] or 0),
            "exact_message_ids": int(message_row["exact_ids"] or 0),
            "earliest_message_post_utc": str(message_row["earliest"] or ""),
            "latest_message_post_utc": str(message_row["latest"] or ""),
            "timestamp_interpretation": EVIDENCE_TIMESTAMP_WARNING,
        },
        "source_artifacts": {
            "count": len(source_rows),
            "declared_complete_count": sum(
                int(row["declared_artifact_complete"] or 0) == 1 for row in source_rows
            ),
            "by_collection_method": grouped_counts(source_rows, "collection_method"),
            "artifact_hashes": [
                {
                    "artifact_id": str(row["artifact_id"]),
                    "sha256": str(row["sha256"]),
                    "collection_method": str(row["collection_method"] or ""),
                    "collection_name": str(row["collection_name"] or ""),
                    "captured_at_utc": str(row["captured_at_utc"] or ""),
                    "declared_complete": bool(row["declared_artifact_complete"]),
                }
                for row in source_rows
            ],
        },
    }


def evidence_item_map(
    con: sqlite3.Connection, message_ids: Sequence[str]
) -> dict[str, list[str]]:
    output: dict[str, list[str]] = defaultdict(list)
    for part in chunks(message_ids):
        placeholders = ",".join("?" for _ in part)
        rows = con.execute(
            f"""
            SELECT message_id,evidence_id,eligible_for_accepted_claims,
                   source_scope,outside_sources_used
            FROM evidence_items
            WHERE message_id IN ({placeholders})
            ORDER BY message_id,evidence_id
            """,
            tuple(part),
        )
        for row in rows:
            if int(row["eligible_for_accepted_claims"]) != 1:
                continue
            if str(row["source_scope"]) != "discord_only" or int(row["outside_sources_used"]) != 0:
                raise ReportError(
                    f"Evidence item {row['evidence_id']} violates Discord-only provenance"
                )
            output[str(row["message_id"])].append(str(row["evidence_id"]))
    return output


def load_evidence_catalog(
    con: sqlite3.Connection, message_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    ids = sorted_message_ids(message_ids)
    rows: dict[str, sqlite3.Row] = {}
    for part in chunks(ids):
        placeholders = ",".join("?" for _ in part)
        for row in con.execute(
            f"SELECT * FROM messages WHERE message_id IN ({placeholders})",
            tuple(part),
        ):
            rows[str(row["message_id"])] = row
    missing = [message_id for message_id in ids if message_id not in rows]
    if missing:
        raise ReportError(
            "Evidence references missing message rows: " + ", ".join(missing[:20])
        )
    items = evidence_item_map(con, ids)
    output: dict[str, dict[str, Any]] = {}
    for message_id in ids:
        row = rows[message_id]
        if int(row["message_id_exact"]) != 1:
            raise ReportError(f"Evidence message {message_id} lacks an exact message ID")
        if int(row["eligible_for_accepted_evidence"]) != 1:
            raise ReportError(f"Evidence message {message_id} is not analysis-eligible")
        if not items.get(message_id):
            raise ReportError(f"Evidence message {message_id} has no eligible evidence item")
        permalink = str(row["permalink"] or "")
        expected_suffix = "/" + message_id
        if not permalink.startswith("https://discord.com/channels/") or not permalink.endswith(expected_suffix):
            raise ReportError(
                f"Evidence message {message_id} has no message-specific Discord permalink"
            )
        content = normalized_text(row["content_text"] or row["visible_text"])
        excerpt = content[:500]
        output[f"message:{message_id}"] = {
            "message_id": message_id,
            "permalink": permalink,
            "permalink_confidence": str(row["permalink_confidence"] or ""),
            "guild_id": str(row["guild_id"] or ""),
            "channel_id": str(row["channel_id"] or ""),
            "parent_channel_id": str(row["parent_channel_id"] or ""),
            "channel_name": str(row["channel_name"] or ""),
            "thread_title": str(row["thread_title"] or ""),
            "author_id": str(row["author_id"] or ""),
            "author_display_name": str(row["author_display_name"] or ""),
            "message_posted_at_utc": str(row["created_at_utc"] or ""),
            "message_post_timestamp_is_setup_time": False,
            "excerpt": excerpt,
            "excerpt_truncated": len(content) > len(excerpt),
            "evidence_trust_state": str(row["evidence_trust_state"]),
            "evidence_item_ids": items[message_id],
        }
    return output


def decorate_evidence(value: Any, catalog: Mapping[str, dict[str, Any]]) -> Any:
    if isinstance(value, dict):
        output = {key: decorate_evidence(child, catalog) for key, child in value.items()}
        if "evidence_message_ids" in value and isinstance(value["evidence_message_ids"], list):
            ids = sorted_message_ids(value["evidence_message_ids"])
            output["evidence_message_ids"] = ids
            output["evidence_refs"] = [
                {
                    "evidence_ref": f"message:{message_id}",
                    "message_id": message_id,
                    "permalink": catalog[f"message:{message_id}"]["permalink"],
                }
                for message_id in ids
            ]
        return output
    if isinstance(value, list):
        return [decorate_evidence(child, catalog) for child in value]
    return value


def unique_limitations(*sources: Any) -> list[str]:
    output: list[str] = []

    def add(value: Any) -> None:
        text = normalized_text(value)
        if text and text not in output:
            output.append(text)

    for source in sources:
        if isinstance(source, (list, tuple)):
            for value in source:
                add(value)
        else:
            add(source)
    return output


def build_report_data(
    database: Path,
    *,
    expected_guild_id: str = EXPECTED_GUILD_ID,
    expected_window_start_utc: str = EXPECTED_WINDOW_START_UTC,
    expected_window_end_utc: str = EXPECTED_WINDOW_END_UTC,
) -> dict[str, Any]:
    database = database.resolve()
    with closing(connect_read_only(database)) as con:
        analysis_run, collection_run, documents, validation = validate_release(
            con,
            expected_guild_id=expected_guild_id,
            expected_window_start_utc=expected_window_start_utc,
            expected_window_end_utc=expected_window_end_utc,
        )
        run_id = int(analysis_run["analysis_run_id"])
        rb_claims = rb_claim_rows(con, run_id)
        rejection_blocks = build_rejection_block_section(
            documents["discord_rejection_block_research"], rb_claims
        )
        models = build_models(con, documents["discord_model_cards"])
        model_discovery = copy.deepcopy(
            documents["discord_model_cards"].get("discovery") or {}
        )
        questions = build_questions(con)
        contradictions = build_contradictions(con)
        strict_trade_evidence = build_strict_trade_evidence(con)
        instrument_role_evidence = build_instrument_role_evidence(
            con, strict_trade_evidence
        )
        coverage = coverage_summary(
            con, collection_run, documents["discord_analysis_coverage"]
        )
        trade_profiles = copy.deepcopy(documents["discord_trade_profiles"])
        overall = trade_profiles.get("overall") or {}
        strict_wins = sum(
            row["resolved_outcome"] == "win" for row in strict_trade_evidence
        )
        strict_losses = sum(
            row["resolved_outcome"] == "loss" for row in strict_trade_evidence
        )
        expected_triplet = (
            int(overall.get("wins") or 0),
            int(overall.get("losses") or 0),
            int(overall.get("eligible_count") or 0),
        )
        observed_triplet = (
            strict_wins,
            strict_losses,
            len(strict_trade_evidence),
        )
        if expected_triplet != observed_triplet:
            raise ReportError(
                "Strict-trade profile/evidence mismatch: "
                f"document={expected_triplet}, database={observed_triplet}"
            )
        trade_profiles["strict_trade_evidence"] = strict_trade_evidence
        trade_profiles["instrument_role_evidence"] = instrument_role_evidence

        evidence_ids: set[str] = set()
        for value in (
            rejection_blocks,
            trade_profiles,
            models,
            model_discovery,
            questions,
            contradictions,
        ):
            evidence_ids.update(recursive_evidence_message_ids(value))
        catalog = load_evidence_catalog(con, evidence_ids)

        rejection_blocks = decorate_evidence(rejection_blocks, catalog)
        trade_profiles = decorate_evidence(trade_profiles, catalog)
        models = decorate_evidence(models, catalog)
        model_discovery = decorate_evidence(model_discovery, catalog)
        questions = decorate_evidence(questions, catalog)
        contradictions = decorate_evidence(contradictions, catalog)

        limitations = unique_limitations(
            RATE_WARNING,
            EVIDENCE_TIMESTAMP_WARNING,
            analysis_run["limitations"],
            collection_run["limitations"],
            documents["discord_analysis_coverage"].get("collection_limitations"),
            documents["discord_trade_profiles"].get("global_warning"),
            documents["discord_trade_profiles"].get("instrument_guard"),
            documents["discord_trade_profiles"].get("association_catalog_policy"),
            documents["discord_rejection_block_research"].get("timing_policy"),
            documents["discord_rejection_block_research"].get("invalidation_policy"),
            [
                str(value)
                for value in documents["discord_analysis_methodology"].get("guardrails", [])
            ],
        )

        return {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "title": "Discord-Only Rejection Block and Trading Model Research",
            "report_type": "technical_evidence_report",
            "claim_scope": "discord_only",
            "outside_sources_used": 0,
            "rate_interpretation": RATE_WARNING,
            "input_database": {
                "sha256": sha256_file(database),
                "schema_version": {
                    str(row[0]): str(row[1])
                    for row in con.execute(
                        "SELECT key,value FROM meta WHERE key IN ('schema_version','source_scope')"
                    )
                }.get("schema_version", ""),
                "source_scope": "discord_only",
            },
            "analysis_run": {
                "analysis_run_id": run_id,
                "collection_run_id": int(analysis_run["collection_run_id"]),
                "schema_version": str(analysis_run["schema_version"]),
                "method": str(analysis_run["method"]),
                "script_sha256": str(analysis_run["script_sha256"] or ""),
                "created_at_utc": str(analysis_run["created_at_utc"]),
                "source_scope": str(analysis_run["source_scope"]),
                "outside_sources_used": int(analysis_run["outside_sources_used"]),
                "limitations": str(analysis_run["limitations"]),
            },
            "release_validation": validation,
            "scope_and_coverage": coverage,
            "definitions": {
                "strict_trade_eligibility": str(trade_profiles.get("strict_eligibility") or ""),
                "higher_lower_association_policy": str(
                    trade_profiles.get("association_catalog_policy") or ""
                ),
                "instrument_role_policy": str(trade_profiles.get("instrument_guard") or ""),
                "author_identity_policy": str(
                    trade_profiles.get("author_identity_policy") or ""
                ),
                "setup_time_policy": str(
                    documents["discord_rejection_block_research"].get("timing_policy") or ""
                ),
                "invalidation_classification_policy": str(
                    documents["discord_rejection_block_research"].get("invalidation_policy") or ""
                ),
            },
            "rejection_blocks": rejection_blocks,
            "trade_profiles": {
                **trade_profiles,
                "all_rate_claims_are_descriptive_self_reported_non_causal": True,
                "forward_probability_or_expectancy_claimed": False,
            },
            "model_cards": {
                "maximum_models": MAX_MODELS,
                "models_emitted": len(models),
                "fifth_model_forced": False,
                "models": models,
                "discovery": model_discovery,
                "warning": RATE_WARNING,
            },
            "question_and_answer_catalog": questions,
            "contradictions": {
                "sets": contradictions,
                "set_count": len(contradictions),
                "conflicting_question_count": questions["status_counts"].get("conflicting", 0),
            },
            "limitations": limitations,
            "recommended_next_steps": [
                "Use evidence_ref values with any downstream LLM answer so the claim remains auditable to Discord.",
                "Resolve unanswered, partial, ambiguous, or conflicting items only with additional captured Discord replies and exact linkage.",
                "Preserve an insufficient RB NQ-versus-ES comparison until both executed-role denominators satisfy the stored comparison rule.",
                "Rebuild from a release-gated analyzed database; do not merge partial report output into a release report.",
            ],
            "further_questions": {
                "answered_count": questions["status_counts"].get("answered", 0),
                "unanswered_count": questions["status_counts"].get("unanswered", 0),
                "partial_count": questions["status_counts"].get("partial", 0),
                "ambiguous_count": questions["status_counts"].get("ambiguous", 0),
                "conflicting_count": questions["status_counts"].get("conflicting", 0),
                "catalog_location": "question_and_answer_catalog.questions_by_status",
            },
            "analysis_methodology": documents["discord_analysis_methodology"],
            "evidence_catalog": catalog,
        }


def pct(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return f"{float(value) * 100:.1f}%"


def counted(value: int, singular: str, plural: str | None = None) -> str:
    return f"{value:,} {singular if value == 1 else (plural or singular + 's')}"


def md_escape(value: Any) -> str:
    return normalized_text(value).replace("|", "\\|")


def md_label(value: Any) -> str:
    text = str(value or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "—"


def evidence_links(ids: Iterable[str], catalog: Mapping[str, dict[str, Any]]) -> str:
    links = []
    for message_id in sorted_message_ids(ids):
        row = catalog[f"message:{message_id}"]
        links.append(f"[{message_id}]({row['permalink']})")
    return ", ".join(links) if links else "—"


def author_note(row: Mapping[str, Any]) -> str:
    distinct = row.get("distinct_authors")
    share = row.get("top_author_share")
    if distinct is None and share is None:
        return "not available"
    parts = []
    if distinct is not None:
        parts.append(f"{int(distinct)} distinct author key(s)")
    if share is not None:
        parts.append(f"top-author share {pct(share)}")
    exact = row.get("distinct_exact_authors")
    surrogate = row.get("distinct_surrogate_authors")
    if exact is not None or surrogate is not None:
        parts.append(f"exact {int(exact or 0)} / surrogate {int(surrogate or 0)}")
    return "; ".join(parts)


def append_component_table(
    lines: list[str],
    rows: list[dict[str, Any]],
    catalog: Mapping[str, dict[str, Any]],
) -> None:
    if not rows:
        lines.append("No eligible source rows were emitted for this category.")
        lines.append("")
        return
    lines.extend(
        [
            "| Stored component | Message count | Interpretation | Evidence |",
            "|---|---:|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| {component} | {count} | {interpretation} | {evidence} |".format(
                component=md_escape(md_label(row.get("component"))),
                count=int(row.get("message_count") or 0),
                interpretation=md_escape(row.get("interpretation") or "Discord textual co-mention."),
                evidence=evidence_links(row.get("evidence_message_ids") or [], catalog),
            )
        )
    lines.append("")


def append_claim_table(
    lines: list[str],
    rows: list[dict[str, Any]],
    catalog: Mapping[str, dict[str, Any]],
) -> None:
    if not rows:
        lines.append("No separately faceted source claims were emitted for this category.")
        lines.append("")
        return
    lines.extend(
        [
            "| Claim ID | Source facet | Stored claim | Status | Evidence |",
            "|---|---|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| {claim} | {facet} | {text} | {status} | {evidence} |".format(
                claim=md_escape(row.get("claim_id")),
                facet=md_escape(row.get("source_facet")),
                text=md_escape(row.get("claim_text")),
                status=md_escape(
                    f"{row.get('epistemic_status')} / {row.get('resolution_status')}"
                ),
                evidence=evidence_links(row.get("evidence_message_ids") or [], catalog),
            )
        )
    lines.append("")


def append_profile_table(
    lines: list[str],
    rows: list[dict[str, Any]],
    catalog: Mapping[str, dict[str, Any]],
) -> None:
    if not rows:
        lines.append("No association met the stored comparison rule for this side of the baseline.")
        lines.append("")
        return
    lines.extend(
        [
            "| Confluence | Wins | Losses | Denominator | Descriptive win share | Difference vs baseline | Author concentration | Evidence |",
            "|---|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| {name} | {wins} | {losses} | {n} | {share} | {difference} | {authors} | {evidence} |".format(
                name=md_escape(md_label(row.get("confluence"))),
                wins=int(row.get("wins") or 0),
                losses=int(row.get("losses") or 0),
                n=int(row.get("eligible_count") or 0),
                share=pct(row.get("descriptive_selected_corpus_win_share")),
                difference=pct(row.get("difference_from_selected_corpus_baseline")),
                authors=md_escape(author_note(row)),
                evidence=evidence_links(row.get("evidence_message_ids") or [], catalog),
            )
        )
    lines.append("")


def render_markdown(report: dict[str, Any]) -> str:
    catalog = report["evidence_catalog"]
    coverage = report["scope_and_coverage"]
    profiles = report["trade_profiles"]
    overall = profiles.get("overall") or {}
    rb = report["rejection_blocks"]
    models = report["model_cards"]
    qa = report["question_and_answer_catalog"]
    contradictions = report["contradictions"]
    overall_n = int(overall.get("eligible_count") or 0)
    overall_wins = int(overall.get("wins") or 0)
    overall_losses = int(overall.get("losses") or 0)
    model_count = int(models["models_emitted"])
    lines: list[str] = [
        f"# {report['title']}",
        "",
        "## Technical summary",
        "",
        (
            f"This is a Discord-only, release-gated synthesis of guild `{coverage['guild_id']}` "
            f"for `{coverage['window_start_utc']}` through `{coverage['window_end_utc']}`. "
            f"The released database contains {coverage['messages']['total']:,} messages, of which "
            f"{coverage['messages']['eligible_for_accepted_evidence']:,} are eligible for accepted analysis evidence."
        ),
        "",
        (
            f"The strict executed-trade cohort contains {counted(overall_n, 'eligible episode')}: "
            f"{counted(overall_wins, 'self-reported win')} and "
            f"{counted(overall_losses, 'self-reported loss')}, for a descriptive "
            f"selected-corpus win share of {pct(overall.get('descriptive_selected_corpus_win_share'))}."
        ),
        "",
        (
            f"The rejection-block catalog contains {rb['term_message_count']:,} RB-term messages, "
            f"and {counted(model_count, 'evidence-backed model card')} were emitted. "
            "No fifth model is forced."
        ),
        "",
        f"**Interpretation guard:** {report['rate_interpretation']}",
        "",
        "## Coverage is release-complete and Discord-only",
        "",
        f"- Source scope: `{coverage['source_scope']}`; outside sources used: `{coverage['outside_sources_used']}`.",
        f"- Collection status: `{coverage['collection_status']}`; analysis completeness: `{coverage['analysis_completeness']}`; unresolved coverage gaps: `{coverage['gap_count']}`.",
        f"- Channel inventory records: `{coverage['channel_inventory']['total_records']}` total, `{coverage['channel_inventory']['exact_id_records']}` exact-ID, `{coverage['channel_inventory']['unresolved_id_records']}` unresolved-ID.",
        f"- Collection units: `{coverage['collection_units']['total']}`; status counts: `{compact_json(coverage['collection_units']['by_status'])}`.",
        f"- Coverage segments: `{coverage['coverage_segments']['total']}`; status counts: `{compact_json(coverage['coverage_segments']['by_status'])}`.",
        f"- Source artifacts: `{coverage['source_artifacts']['count']}`; every artifact remains represented by its database-stored SHA-256 provenance.",
        (
            "- Owned Discord attachments: "
            f"`{coverage['attachment_archive']['owned_attachment_count']}`; archive status: "
            f"`{coverage['attachment_archive']['release_status']}`; queryable verified "
            "complete/partial extraction artifacts: "
            f"`{coverage['attachment_archive']['queryable_verified_extraction_count']}`. "
            "Attachment presence never proves chart geometry."
        ),
        "",
        f"{EVIDENCE_TIMESTAMP_WARNING}",
        "",
        "## Definitions and comparison basis",
        "",
        f"- Strict cohort: {report['definitions']['strict_trade_eligibility']}",
        f"- Higher/lower catalog: {report['definitions']['higher_lower_association_policy']}",
        f"- Instrument roles: {report['definitions']['instrument_role_policy']}",
        f"- Author identity: {report['definitions']['author_identity_policy'] or 'The analyzed database did not emit an expanded author-identity note; author counts remain labeled as stored.'}",
        f"- Setup time: {report['definitions']['setup_time_policy']}",
        f"- Invalidation: {report['definitions']['invalidation_classification_policy']}",
        "",
        "## Methodology keeps claims inside the analyzed database",
        "",
        (
            f"The report reads only the analyzed Cardinal SQLite database and selects analysis run "
            f"`{report['analysis_run']['analysis_run_id']}` using method `{report['analysis_run']['method']}`. "
            "It reproduces stored analysis documents, normalized claims, strict trade rows, Q&A links, "
            "contradiction sets, and trusted message evidence. It does not read raw exports, browse the web, "
            "interpret chart geometry, or add Cardinal concept defaults."
        ),
        "",
        (
            "A claim's evidence reference resolves to a message-specific Discord permalink plus database "
            "evidence-item IDs. Technical invalidation and non-actionability are separated only by stored "
            "source facets. Instrument roles are taken from stored executed or market-context assignments."
        ),
        "",
        "## Rejection blocks: identification evidence",
        "",
        (
            "The following components and claims reproduce the analyzed database. "
            "Component counts are textual RB co-mentions, not objective formation frequencies."
        ),
        "",
        "### Stored identification components",
        "",
    ]
    append_component_table(lines, rb["identification"]["textual_components"], catalog)
    lines.extend(["### Separately stored identification claims", ""])
    append_claim_table(lines, rb["identification"]["source_claims"], catalog)

    lines.extend(
        [
            "## Invalidation and non-actionability remain separate",
            "",
            rb["invalidation"]["classification_guard"],
            "",
            "### Technical invalidation claims",
            "",
        ]
    )
    append_claim_table(
        lines, rb["invalidation"]["technical_invalidation_source_claims"], catalog
    )
    lines.extend(["### Non-actionability claims", ""])
    append_claim_table(lines, rb["invalidation"]["non_actionability_source_claims"], catalog)
    lines.extend(["### Combined or unclassified invalidation/actionability evidence", ""])
    append_claim_table(
        lines, rb["invalidation"]["unclassified_combined_source_claims"], catalog
    )
    append_component_table(
        lines, rb["invalidation"]["unclassified_textual_components"], catalog
    )

    lines.extend(
        [
            "## Explicit setup times and sessions",
            "",
            rb["times_and_sessions"]["policy"],
            "",
        ]
    )
    append_component_table(
        lines, rb["times_and_sessions"]["explicit_textual_components"], catalog
    )
    append_claim_table(lines, rb["times_and_sessions"]["source_claims"], catalog)

    lines.extend(
        [
            "## RB quality evidence and outcome associations answer different questions",
            "",
            (
                "The textual component table shows what the Discord RB messages co-mentioned. "
                "The outcome tables that follow use only the strict executed-trade cohort. "
                "They must not be collapsed into a probability claim."
            ),
            "",
            "### RB-text quality and confluence components",
            "",
        ]
    )
    append_component_table(lines, rb["quality_and_confluences"]["textual_components"], catalog)
    append_claim_table(lines, rb["quality_and_confluences"]["source_claims"], catalog)

    lines.extend(
        [
            "## Higher and lower selected-corpus confluence profiles",
            "",
            f"Baseline descriptive win share: **{pct(overall.get('descriptive_selected_corpus_win_share'))}** across **{int(overall.get('eligible_count') or 0)}** eligible episodes. {RATE_WARNING}",
            "",
            "### Above-baseline associations meeting the stored denominator rule",
            "",
        ]
    )
    append_profile_table(lines, profiles.get("observed_higher_share_associations") or [], catalog)
    lines.extend(["### Below-baseline associations meeting the stored denominator rule", ""])
    append_profile_table(lines, profiles.get("observed_lower_share_associations") or [], catalog)

    lines.extend(
        [
            "## Strict self-reported win and loss profiles",
            "",
            f"Wins: **{int((profiles.get('win_profile') or {}).get('trade_count') or 0)}**; author concentration: {author_note(profiles.get('win_profile') or {})}.",
            "",
            f"Losses: **{int((profiles.get('loss_profile') or {}).get('trade_count') or 0)}**; author concentration: {author_note(profiles.get('loss_profile') or {})}.",
            "",
            "| Outcome | Confluence | Trades in outcome cohort | Share of outcome cohort | Author concentration |",
            "|---|---|---:|---:|---|",
        ]
    )
    for outcome, profile_key in (("Win", "win_profile"), ("Loss", "loss_profile")):
        profile = profiles.get(profile_key) or {}
        for row in profile.get("confluences") or []:
            lines.append(
                f"| {outcome} | {md_escape(md_label(row.get('confluence')))} | "
                f"{int(row.get('trade_count') or 0)} | {pct(row.get('descriptive_share_of_outcome_cohort'))} | "
                f"{md_escape(author_note(row))} |"
            )
    lines.append("")
    lines.append(RATE_WARNING)
    lines.append("")
    lines.extend(
        [
            "### Strict episode evidence catalog",
            "",
            "| Trade ID | Date label | Outcome | Confluences | Stored instrument roles | Trader author key | Evidence |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in profiles.get("strict_trade_evidence") or []:
        instrument_text = ", ".join(
            f"{value.get('canonical_symbol')} ({value.get('role')})"
            for value in row.get("instrument_roles") or []
        ) or "—"
        lines.append(
            f"| `{md_escape(row.get('trade_id'))}` | {md_escape(row.get('trade_date_text'))} | "
            f"{md_escape(row.get('resolved_outcome'))} | "
            f"{md_escape(', '.join(row.get('confluences') or []) or '—')} | "
            f"{md_escape(instrument_text)} | {md_escape(row.get('trader_author_key'))} | "
            f"{evidence_links(row.get('evidence_message_ids') or [], catalog)} |"
        )
    if not (profiles.get("strict_trade_evidence") or []):
        lines.append("| — | — | — | — | — | — | — |")
    lines.append("")

    lines.extend(
        [
            "## NQ and ES: executed role is not market context",
            "",
            str(profiles.get("instrument_guard") or ""),
            "",
            "### All strict executed-instrument episodes",
            "",
            "| Instrument family | Wins | Losses | Denominator | Descriptive win share | Author concentration |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in profiles.get("executed_instrument_comparison") or []:
        lines.append(
            f"| {md_escape(row.get('instrument_family'))} | {int(row.get('wins') or 0)} | "
            f"{int(row.get('losses') or 0)} | {int(row.get('eligible_count') or 0)} | "
            f"{pct(row.get('descriptive_selected_corpus_win_share'))} | {md_escape(author_note(row))} |"
        )
    if not (profiles.get("executed_instrument_comparison") or []):
        lines.append("| — | 0 | 0 | 0 | — | not available |")
    lines.extend(["", "### RB-only executed-instrument comparison", ""])
    rb_instruments = profiles.get("rejection_block_executed_instrument_comparison") or {}
    lines.append(f"Status: `{md_escape(rb_instruments.get('status'))}`. {md_escape(rb_instruments.get('answer_guard'))}")
    lines.append("")
    lines.extend(
        [
            "| Instrument family | Wins | Losses | Denominator | Descriptive win share | Author concentration |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in rb_instruments.get("rows") or []:
        lines.append(
            f"| {md_escape(row.get('instrument_family'))} | {int(row.get('wins') or 0)} | "
            f"{int(row.get('losses') or 0)} | {int(row.get('eligible_count') or 0)} | "
            f"{pct(row.get('descriptive_selected_corpus_win_share'))} | {md_escape(author_note(row))} |"
        )
    if not (rb_instruments.get("rows") or []):
        lines.append("| — | 0 | 0 | 0 | — | not available |")
    lines.extend(["", "### Market-context mentions (not executed trades)", ""])
    lines.extend(
        [
            "| Instrument family | Wins | Losses | Denominator | Descriptive win share | Author concentration |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in profiles.get("market_context_instrument_mentions") or []:
        lines.append(
            f"| {md_escape(row.get('instrument_family'))} | {int(row.get('wins') or 0)} | "
            f"{int(row.get('losses') or 0)} | {int(row.get('eligible_count') or 0)} | "
            f"{pct(row.get('descriptive_selected_corpus_win_share'))} | {md_escape(author_note(row))} |"
        )
    if not (profiles.get("market_context_instrument_mentions") or []):
        lines.append("| — | 0 | 0 | 0 | — | not available |")
    lines.append("")
    lines.extend(
        [
            "### Exact-symbol role evidence from strict episodes",
            "",
            (
                "This table is the message-level audit trail for stored instrument roles. It retains exact "
                "database symbols and does not add an unstored family mapping."
            ),
            "",
            "| Role | Exact symbol | Strict episodes | Wins | Losses | RB episodes | Evidence |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in profiles.get("instrument_role_evidence") or []:
        lines.append(
            f"| {md_escape(row.get('role'))} | {md_escape(row.get('canonical_symbol'))} | "
            f"{int(row.get('strict_episode_count') or 0)} | {int(row.get('wins') or 0)} | "
            f"{int(row.get('losses') or 0)} | {int(row.get('rejection_block_episode_count') or 0)} | "
            f"{evidence_links(row.get('evidence_message_ids') or [], catalog)} |"
        )
    if not (profiles.get("instrument_role_evidence") or []):
        lines.append("| — | — | 0 | 0 | 0 | 0 | — |")
    lines.append("")

    discovery = models.get("discovery") or {}
    novel_discovery = discovery.get("novel_candidate_discovery") or {}
    lines.extend(
        [
            "## Evidence-backed trading model cards",
            "",
            (
                "The analyzer retained supported Discord-derived legacy candidates, then exhaustively "
                "enumerated stored two- and three-token signatures across the full trust-eligible strict "
                "episode cohort. Remaining slots were available only to recurrent candidates that passed "
                "sample, multi-author, multi-date, explicit-operational-text, author-concentration, and "
                "near-duplicate safeguards."
                if discovery
                else "The analyzed database did not include an expanded full-window discovery audit."
            ),
            "",
        ]
    )
    if discovery:
        lines.extend(
            [
                f"- Preserved candidates retained: `{int(discovery.get('retained_legacy_models') or 0)}`.",
                f"- Novel full-window candidates promoted: `{int(discovery.get('promoted_novel_models') or 0)}`.",
                f"- Trust-eligible strict episodes scanned: `{int(novel_discovery.get('trust_eligible_strict_episodes_scanned') or 0)}`.",
                f"- Candidate signatures enumerated: `{int(novel_discovery.get('candidate_signatures_enumerated') or 0)}`; distinct threshold-clearing candidates before slot limit: `{int(novel_discovery.get('distinct_novel_candidates_pre_slot_limit') or 0)}`.",
                f"- Rejection reasons: `{compact_json(novel_discovery.get('rejection_reason_counts') or {})}`.",
                f"- Slot policy: {md_escape(discovery.get('slot_policy'))}",
                "",
            ]
        )
    if not models["models"]:
        lines.extend(
            [
                "No model card met the analyzed database's evidence requirements. A fifth model was not forced.",
                "",
            ]
        )
    for index, model in enumerate(models["models"], start=1):
        strict = model.get("strict_selected_corpus") or {}
        lines.extend(
            [
                f"### Model {index}: {md_escape(model.get('name'))}",
                "",
                f"**Candidate origin:** `{md_escape(model.get('candidate_origin') or 'stored_discord_model')}`.",
                "",
                f"**Stored thesis:** {model.get('thesis') or model.get('material_distinction') or '—'}",
                "",
                (
                    f"Stored matches: **{int(model.get('database_match_count') or 0)}**. "
                    f"Strict cohort: **{int(strict.get('wins') or 0)} wins / {int(strict.get('losses') or 0)} losses "
                    f"(n={int(strict.get('eligible_count') or 0)}, descriptive win share {pct(strict.get('descriptive_win_share'))})**. "
                    f"Author concentration: {author_note(strict)}."
                ),
                "",
                f"Limitations: {model.get('limitations') or model.get('warning') or RATE_WARNING}",
                "",
                (
                    "Stored candidate signature: `"
                    + md_escape(", ".join(model.get("candidate_signature") or []) or "preserved-template")
                    + "`."
                ),
                "",
                (
                    "Unresolved explicit rule facets: `"
                    + md_escape(", ".join(model.get("unresolved_rule_facets") or []) or "none")
                    + "`. No missing entry, invalidation, or target rule is invented."
                ),
                "",
                "| Order | Rule type | Required state | Stored rule | Evidence |",
                "|---:|---|---|---|---|",
            ]
        )
        source_rules = model.get("rules") or []
        for rule in source_rules:
            lines.append(
                f"| {int(rule.get('order') or 0)} | {md_escape(rule.get('type'))} | "
                f"{md_escape(rule.get('required_state'))} | {md_escape(rule.get('text'))} | "
                f"{evidence_links(rule.get('evidence_message_ids') or [], catalog)} |"
            )
        if not source_rules:
            for rule in model.get("database_rules") or []:
                lines.append(
                    f"| {int(rule.get('order') or 0)} | {md_escape(rule.get('type'))} | "
                    f"{md_escape(rule.get('required_state'))} | {md_escape(rule.get('text'))} | — |"
                )
        lines.append("")
        counterevidence = model.get("contradictions_or_counterevidence") or []
        if counterevidence:
            lines.append("Explicit counterevidence or no-trade text retained for this candidate:")
            lines.append("")
            for item in counterevidence:
                lines.append(
                    "- "
                    + md_escape(item.get("exact_excerpt") or "")
                    + "; evidence "
                    + evidence_links(item.get("evidence_message_ids") or [], catalog)
                    + "."
                )
            lines.append("")
        lines.append(RATE_WARNING)
        lines.append("")

    lines.extend(["## Relevant Discord questions and captured answers", ""])
    lines.append(
        "Status counts: `" + compact_json(qa["status_counts"]) + "`. " + qa["unanswered_interpretation"]
    )
    lines.append("")
    for status, questions in qa["questions_by_status"].items():
        lines.extend([f"### {md_label(status)} ({len(questions)})", ""])
        if not questions:
            lines.extend(["No questions in this status.", ""])
            continue
        for question in questions:
            qid = question["primary_message_id"]
            qlink = evidence_links([qid], catalog)
            lines.append(
                f"- **{md_escape(question['normalized_question'])}** — topic `{md_escape(question['topic'])}`"
                + (f", subtopic `{md_escape(question['subtopic'])}`" if question["subtopic"] else "")
                + f"; question {qlink}; ID `{question['question_id']}`."
            )
            for answer in question.get("answers") or []:
                answer_links = evidence_links(answer.get("evidence_message_ids") or [], catalog)
                authority = answer.get("authority") or {}
                lines.append(
                    f"  - Answer: {md_escape(answer.get('answer_summary'))} "
                    f"({answer_links}; link `{md_escape(answer.get('link_type'))}`, "
                    f"direct reply `{str(bool(answer.get('direct_reply'))).lower()}`, "
                    f"linkage confidence `{float(answer.get('linkage_confidence') or 0):.3f}`, "
                    f"authority `{md_escape(authority.get('authority_class'))}`)."
                )
        lines.append("")

    lines.extend(["## Contradictions, unresolved evidence, and limitations", ""])
    if contradictions["sets"]:
        for item in contradictions["sets"]:
            lines.append(
                f"### {md_escape(item['topic'])} — {md_escape(item['resolution_status'])}"
            )
            lines.append("")
            if item.get("resolution_summary"):
                lines.append(md_escape(item["resolution_summary"]))
                lines.append("")
            for member in item.get("members") or []:
                lines.append(
                    f"- `{member['stance']}` — {md_escape(member['claim_text'])}; "
                    f"claim `{member['claim_id']}`; evidence "
                    f"{evidence_links(member.get('evidence_message_ids') or [], catalog)}."
                )
            lines.append("")
    else:
        lines.extend(
            [
                "No contradiction set was stored in the analyzed database. This is not evidence that the Discord contained no disagreement; conflicting Q&A remains counted separately.",
                "",
            ]
        )
    for limitation in report["limitations"]:
        lines.append(f"- {limitation}")
    lines.append("")

    lines.extend(
        [
            "## Evidence-bounded next steps",
            "",
            "- Use the structured JSON for LLM retrieval and retain each `evidence_ref` with any answer derived from it.",
            "- Treat unanswered, partial, ambiguous, and conflicting questions as evidence gaps; resolve them only with additional captured Discord replies and exact linkage.",
            "- If the RB-only NQ-versus-ES status is insufficient, preserve that status until both executed-role denominators satisfy the stored comparison rule.",
            "- Re-run this generator only after the analyzed database passes the same release gates; never merge partial report output with a release report.",
            "",
            "## Further questions retained by the evidence",
            "",
            (
                f"The catalog retains {qa['status_counts'].get('unanswered', 0)} unanswered, "
                f"{qa['status_counts'].get('partial', 0)} partial, "
                f"{qa['status_counts'].get('ambiguous', 0)} ambiguous, and "
                f"{qa['status_counts'].get('conflicting', 0)} conflicting question(s). "
                "Their full text, status, message IDs, links, and any captured answer linkage appear in the Q&A section and structured JSON."
            ),
            "",
        ]
    )

    lines.extend(
        [
            "## Evidence reference catalog",
            "",
            (
                "Every evidence reference below resolves to an analysis-eligible Discord message and one or more "
                "eligible evidence-item IDs. Posted-at timestamps are provenance only, not setup times."
            ),
            "",
            "| Evidence ref | Message | Channel / thread | Author key | Posted UTC | Excerpt | Evidence item IDs |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for ref, row in catalog.items():
        channel = row.get("thread_title") or row.get("channel_name") or row.get("channel_id")
        author = row.get("author_id") or ("display surrogate: " + str(row.get("author_display_name") or "unknown"))
        excerpt = row.get("excerpt") or "[no text; evidence may be attachment-linked]"
        lines.append(
            f"| `{ref}` | [{row['message_id']}]({row['permalink']}) | {md_escape(channel)} | "
            f"{md_escape(author)} | {md_escape(row.get('message_posted_at_utc'))} | "
            f"{md_escape(excerpt)} | {md_escape(', '.join(row.get('evidence_item_ids') or []))} |"
        )
    lines.append("")
    lines.append(
        f"Report input SHA-256: `{report['input_database']['sha256']}`. "
        f"Analysis run: `{report['analysis_run']['analysis_run_id']}`; method: `{report['analysis_run']['method']}`."
    )
    lines.append("")
    return "\n".join(lines)


def atomic_write_pair(
    markdown_path: Path,
    json_path: Path,
    markdown: str,
    structured_json: str,
    *,
    replace: bool,
) -> None:
    markdown_path = markdown_path.resolve()
    json_path = json_path.resolve()
    if markdown_path == json_path:
        raise ReportError("Markdown and JSON output paths must be different")
    existing = [path for path in (markdown_path, json_path) if path.exists()]
    if existing and not replace:
        raise FileExistsError(
            "Output exists; pass --replace: " + ", ".join(str(path) for path in existing)
        )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_tmp = markdown_path.with_name(markdown_path.name + ".tmp")
    json_tmp = json_path.with_name(json_path.name + ".tmp")
    if markdown_tmp.exists() or json_tmp.exists():
        raise ReportError("Temporary report output already exists; inspect before retrying")
    try:
        markdown_tmp.write_text(markdown, encoding="utf-8", newline="\n")
        json_tmp.write_text(structured_json, encoding="utf-8", newline="\n")
        if replace:
            os.replace(markdown_tmp, markdown_path)
            os.replace(json_tmp, json_path)
        else:
            markdown_tmp.replace(markdown_path)
            json_tmp.replace(json_path)
    except Exception:
        markdown_tmp.unlink(missing_ok=True)
        json_tmp.unlink(missing_ok=True)
        raise


def build_reports(
    database: Path,
    markdown_output: Path,
    json_output: Path,
    *,
    replace: bool = False,
    expected_guild_id: str = EXPECTED_GUILD_ID,
    expected_window_start_utc: str = EXPECTED_WINDOW_START_UTC,
    expected_window_end_utc: str = EXPECTED_WINDOW_END_UTC,
) -> dict[str, Any]:
    database = database.resolve()
    if database in {markdown_output.resolve(), json_output.resolve()}:
        raise ReportError("Output path must not overwrite the input database")
    before_hash = sha256_file(database)
    report = build_report_data(
        database,
        expected_guild_id=expected_guild_id,
        expected_window_start_utc=expected_window_start_utc,
        expected_window_end_utc=expected_window_end_utc,
    )
    markdown = render_markdown(report)
    atomic_write_pair(
        markdown_output,
        json_output,
        markdown,
        json_text(report),
        replace=replace,
    )
    after_hash = sha256_file(database)
    if before_hash != after_hash:
        raise ReportError("Input database changed during report generation")
    return {
        "status": "passed",
        "database_sha256": before_hash,
        "markdown": str(markdown_output.resolve()),
        "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest().upper(),
        "json": str(json_output.resolve()),
        "json_sha256": hashlib.sha256(json_text(report).encode("utf-8")).hexdigest().upper(),
        "analysis_run_id": report["analysis_run"]["analysis_run_id"],
        "evidence_message_count": len(report["evidence_catalog"]),
        "model_count": report["model_cards"]["models_emitted"],
        "question_counts": report["question_and_answer_catalog"]["status_counts"],
    }


def parser() -> argparse.ArgumentParser:
    output = argparse.ArgumentParser(
        description="Build deterministic Discord-only research Markdown and JSON from analyzed Cardinal SQLite."
    )
    output.add_argument("--database", required=True, type=Path)
    output.add_argument("--markdown-output", required=True, type=Path)
    output.add_argument("--json-output", required=True, type=Path)
    output.add_argument("--replace", action="store_true")
    output.add_argument("--expected-guild-id", default=EXPECTED_GUILD_ID)
    output.add_argument("--expected-window-start-utc", default=EXPECTED_WINDOW_START_UTC)
    output.add_argument("--expected-window-end-utc", default=EXPECTED_WINDOW_END_UTC)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = build_reports(
            args.database,
            args.markdown_output,
            args.json_output,
            replace=args.replace,
            expected_guild_id=args.expected_guild_id,
            expected_window_start_utc=args.expected_window_start_utc,
            expected_window_end_utc=args.expected_window_end_utc,
        )
    except (FileNotFoundError, FileExistsError, ReportError, sqlite3.DatabaseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json_text(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
