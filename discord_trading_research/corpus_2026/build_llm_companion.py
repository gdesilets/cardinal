"""Build a compact, read-only-friendly SQLite handoff from an analyzed Cardinal v2 DB.

The source database remains authoritative.  This builder removes bulky raw JSON and
internal collection payloads, retains Discord text and trust/provenance flags, and
materializes the analysis views most useful to a downstream LLM.  It never updates
the source database and refuses non-Discord or failed-audit inputs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, Sequence


COMPANION_SCHEMA_VERSION = "1.0.0"
SOURCE_SCOPE = "discord_only"


# These normalized tables are small compared with the raw browser payloads and are
# useful for advanced joins.  They are copied as snapshots without their source
# triggers or foreign keys; the full analyzed database remains the audit authority.
NORMALIZED_TABLES = (
    "analysis_runs",
    "analysis_documents",
    "analysis_entities",
    "evidence_items",
    "claims",
    "claim_evidence",
    "claim_relations",
    "confidence_assessments",
    "questions",
    "answers",
    "question_messages",
    "answer_messages",
    "question_answer_links",
    "authority_assignments",
    "discord_roles",
    "author_role_observations",
    "relevance_annotations",
    "concept_terms",
    "term_aliases",
    "instruments",
    "instrument_aliases",
    "timeframes",
    "sessions",
    "setup_models",
    "setup_aliases",
    "setup_model_rules",
    "setup_instances",
    "setup_features",
    "setup_instruments",
    "setup_timeframes",
    "setup_sessions",
    "setup_time_markers",
    "setup_narratives",
    "setup_scenarios",
    "setup_context_events",
    "setup_confirmations",
    "setup_invalidations",
    "setup_rule_states",
    "setup_model_matches",
    "liquidity_events",
    "market_levels",
    "price_arrays",
    "price_array_interactions",
    "trade_episodes",
    "trade_orders",
    "trade_management_events",
    "trade_outcome_claims",
    "trade_outcome_measures",
    "trade_outcome_resolution",
    "analysis_cohorts",
    "setup_performance_rollups",
    "contradiction_sets",
    "contradiction_members",
    "data_dictionary",
)


SLIM_TABLE_QUERIES = {
    "source_meta": "SELECT key,value FROM src.meta",
    "collection_runs": """
        SELECT run_id,guild_id,guild_name,window_start_utc,window_end_utc,scope,
               source_scope,outside_sources_used,status,collected_at_utc,built_at_utc,
               methodology,limitations
        FROM src.collection_runs
    """,
    "channel_inventory": """
        SELECT channel_id,guild_id,parent_channel_id,name,kind,exact_id_known,
               is_archived,is_accessible,inventory_basis,discovered_at_utc,
               first_seen_utc,last_seen_utc
        FROM src.channel_inventory
    """,
    "collection_units": "SELECT * FROM src.collection_units",
    "coverage_segments": "SELECT * FROM src.coverage_segments",
    "source_artifacts": """
        SELECT artifact_id,run_id,parent_artifact_id,source_file,sha256,
               collection_method,collection_name,query_text,captured_at_utc,
               declared_artifact_complete
        FROM src.source_artifacts
    """,
    "authors": """
        SELECT author_id,discord_user_id,user_id_exact,identity_resolution,
               surrogate_key,first_seen_utc,last_seen_utc
        FROM src.authors
    """,
    "author_names": "SELECT * FROM src.author_names",
    "messages": """
        SELECT message_id,message_id_exact,run_id,guild_id,channel_id,
               parent_channel_id,channel_name,thread_title,author_id,
               author_display_name,created_at_utc,displayed_time,edited,
               is_original_poster,reply_to_message_id,reply_to_content,
               reply_target_state,content_text,visible_text,content_sha256,
               permalink,permalink_confidence,evidence_trust_state,
               eligible_for_accepted_evidence,has_quarantined_occurrences,
               trusted_canonical_occurrence_count,quarantined_occurrence_count,
               canonical_selection_method
        FROM src.messages
    """,
    "message_source_occurrences": """
        SELECT occurrence_id,message_id,artifact_id,collection_name,query_text,
               result_index,page_number,segment_start_utc,segment_end_utc,
               artifact_declared_complete,source_kind,migration_source,quarantined,
               trusted_canonical,trust_state,quarantine_reasons_json
        FROM src.message_source_occurrences
    """,
    "quarantine_records": """
        SELECT quarantine_id,run_id,artifact_id,message_id,occurrence_id,reason,status
        FROM src.quarantine_records
    """,
    "attachments": """
        SELECT attachment_id,message_id,attachment_id_exact,filename,discord_url,
               source_channel_id,relation_type,ownership_status,
               ownership_evidence_json,owned_for_capture,
               eligible_for_attachment_evidence,mime_type,media_kind,width,height,byte_size,
               content_sha256,local_package_path,capture_status,capture_terminal,
               capture_attempt_count,capture_attempts_json,capture_failure_code,
               capture_failure_detail,extraction_status,extraction_artifacts_json,
               archive_manifest_source_file_id,chart_claim_eligible,notes
        FROM src.attachments
    """,
    "attachment_extractions": "SELECT * FROM src.attachment_extractions",
    "message_embeds": """
        SELECT embed_id,message_id,embed_type,title,description,url
        FROM src.message_embeds
    """,
    "message_links": "SELECT * FROM src.message_links",
    "message_mentions": "SELECT * FROM src.message_mentions",
    "message_reactions": """
        SELECT message_id,emoji_key,reaction_count,reacted_by_current_user
        FROM src.message_reactions
    """,
    "message_relations": "SELECT * FROM src.message_relations",
    "message_versions": """
        SELECT message_id,version_no,content_text,visible_text,edited_at_utc,
               content_sha256,version_basis,artifact_id
        FROM src.message_versions
    """,
}


MATERIALIZED_VIEW_QUERIES = {
    "query_setup_cards": "SELECT * FROM src.v_cardinal_setup_cards",
    "query_setup_evidence": "SELECT * FROM src.v_cardinal_setup_evidence",
    "query_resolved_trade_outcomes": "SELECT * FROM src.v_resolved_trade_outcomes",
    "query_performance": "SELECT * FROM src.v_selected_corpus_performance",
    "query_model_rule_matrix": "SELECT * FROM src.v_setup_rule_matrix",
    "query_instrument_comparison": "SELECT * FROM src.v_instrument_setup_comparison",
    "query_open_contradictions": "SELECT * FROM src.v_open_contradictions",
    "query_unresolved_questions": "SELECT * FROM src.v_unresolved_qa",
    "query_server_coverage": "SELECT * FROM src.v_whole_server_coverage",
    "query_collection_gaps": "SELECT * FROM src.v_collection_gaps",
}


CUSTOM_QUERY_TABLES = {
    "query_rejection_blocks": """
        SELECT ae.entity_id AS observation_id,ae.entity_type,c.claim_id,c.facet,c.claim_text,
               c.normalized_value_json,c.claim_kind,c.epistemic_status,
               c.resolution_status,c.speaker_author_id,c.authority_assignment_id,
               aa.authority_class,aa.basis AS authority_basis,
               aa.confidence AS authority_confidence,c.limitations,
               COALESCE((
                 SELECT json_group_array(json_object(
                   'evidence_id',ev.evidence_id,
                   'message_id',ev.message_id,
                   'evidence_role',ce.evidence_role,
                   'excerpt',ev.exact_excerpt,
                   'created_at_utc',m.created_at_utc,
                   'channel_name',m.channel_name,
                   'thread_title',m.thread_title,
                   'author',m.author_display_name,
                   'permalink',m.permalink,
                   'trust_state',ev.evidence_trust_state,
                   'eligible_for_accepted_claims',ev.eligible_for_accepted_claims
                 ))
                 FROM src.claim_evidence ce
                 JOIN src.evidence_items ev ON ev.evidence_id=ce.evidence_id
                 LEFT JOIN src.messages m ON m.message_id=ev.message_id
                 WHERE ce.claim_id=c.claim_id
               ),'[]') AS evidence_json
        FROM src.analysis_entities ae
        JOIN src.claims c ON c.subject_entity_id=ae.entity_id
        LEFT JOIN src.authority_assignments aa
          ON aa.assignment_id=c.authority_assignment_id
        WHERE ae.entity_type IN ('rejection_block_observation','rejection_block_finding')
    """,
    "query_qa": """
        SELECT q.question_id,q.primary_message_id,q.normalized_question,q.topic,
               q.subtopic,q.resolution_status AS question_status,
               qm.permalink AS question_permalink,
               a.answer_id,a.answer_summary,a.resolution_status AS answer_status,
               l.link_type,l.direct_reply,l.linkage_confidence,
               aa.authority_class,aa.basis AS authority_basis,
               aa.confidence AS authority_confidence,
               COALESCE((
                 SELECT json_group_array(json_object(
                   'message_id',x.message_id,
                   'sequence',x.sequence_order,
                   'text',mx.content_text,
                   'author',mx.author_display_name,
                   'created_at_utc',mx.created_at_utc,
                   'permalink',mx.permalink,
                   'trust_state',mx.evidence_trust_state,
                   'eligible',mx.eligible_for_accepted_evidence
                 ))
                 FROM src.question_messages x
                 JOIN src.messages mx ON mx.message_id=x.message_id
                 WHERE x.question_id=q.question_id
               ),'[]') AS question_messages_json,
               COALESCE((
                 SELECT json_group_array(json_object(
                   'message_id',x.message_id,
                   'sequence',x.sequence_order,
                   'role',x.message_role,
                   'text',mx.content_text,
                   'author',mx.author_display_name,
                   'created_at_utc',mx.created_at_utc,
                   'permalink',mx.permalink,
                   'trust_state',mx.evidence_trust_state,
                   'eligible',mx.eligible_for_accepted_evidence
                 ))
                 FROM src.answer_messages x
                 JOIN src.messages mx ON mx.message_id=x.message_id
                 WHERE x.answer_id=a.answer_id
               ),'[]') AS answer_messages_json
        FROM src.questions q
        LEFT JOIN src.messages qm ON qm.message_id=q.primary_message_id
        LEFT JOIN src.question_answer_links l ON l.question_id=q.question_id
        LEFT JOIN src.answers a ON a.answer_id=l.answer_id
        LEFT JOIN src.claims ac ON ac.claim_id=a.answer_claim_id
        LEFT JOIN src.authority_assignments aa
          ON aa.assignment_id=ac.authority_assignment_id
    """,
    "query_trade_episodes": """
        SELECT t.trade_id,t.instance_id,t.trader_id,t.trade_date_text,
               t.execution_mode,t.episode_kind,t.aggregate_group_id,
               t.strict_comparison_eligible AS episode_strict_comparison_eligible,
               t.linkage_status,t.episode_claim_id,t.legacy_trade_id,t.notes,
               si.primary_message_id,m.created_at_utc AS source_post_time_utc,
               m.channel_name,m.thread_title,m.author_display_name,m.permalink,
               r.resolved_outcome,r.resolution_status AS outcome_resolution_status,
               r.strict_comparison_eligible AS outcome_strict_comparison_eligible,
               r.resolution_reason,oc.basis AS outcome_basis,
               oc.terminal_at_text,oc.is_aggregate,oc.reported_trade_count,
               COALESCE((
                 SELECT json_group_array(json_object(
                   'symbol',i.canonical_symbol,'role',x.role,'raw_text',x.raw_text
                 ))
                 FROM src.setup_instruments x
                 JOIN src.instruments i ON i.instrument_id=x.instrument_id
                 WHERE x.instance_id=t.instance_id
               ),'[]') AS instruments_json,
               COALESCE((
                 SELECT json_group_array(json_object(
                   'term',ct.canonical_name,'role',x.feature_role,'state',x.state,
                   'timeframe',tf.canonical_token
                 ))
                 FROM src.setup_features x
                 JOIN src.concept_terms ct ON ct.term_id=x.term_id
                 LEFT JOIN src.timeframes tf ON tf.timeframe_id=x.timeframe_id
                 WHERE x.instance_id=t.instance_id
               ),'[]') AS features_json,
               COALESCE((
                 SELECT json_group_array(json_object(
                   'model_id',x.model_id,'name',sm.canonical_name,
                   'status',x.match_status,'matched_rules',x.matched_rule_count,
                   'missing_rules',x.missing_rule_count,
                   'violated_rules',x.violated_rule_count
                 ))
                 FROM src.setup_model_matches x
                 JOIN src.setup_models sm ON sm.model_id=x.model_id
                 WHERE x.instance_id=t.instance_id
               ),'[]') AS model_matches_json
        FROM src.trade_episodes t
        JOIN src.setup_instances si ON si.instance_id=t.instance_id
        LEFT JOIN src.messages m ON m.message_id=si.primary_message_id
        LEFT JOIN src.trade_outcome_resolution r ON r.trade_id=t.trade_id
        LEFT JOIN src.trade_outcome_claims oc
          ON oc.outcome_claim_id=r.resolved_outcome_claim_id
    """,
    "query_models": """
        SELECT sm.model_id,sm.canonical_name,sm.thesis,sm.evidence_status,
               sm.lifecycle_status,sm.identity_claim_id,sm.limitations,
               COALESCE((
                 SELECT json_group_array(json_object(
                   'rule_id',r.rule_id,'order',r.rule_order,'type',r.rule_type,
                   'text',r.rule_text,'required_state',r.required_state,
                   'claim_id',r.claim_id
                 ))
                 FROM (
                   SELECT rule_id,rule_order,rule_type,rule_text,required_state,claim_id
                   FROM src.setup_model_rules
                   WHERE model_id=sm.model_id
                   ORDER BY rule_order,rule_id
                 ) AS r
               ),'[]') AS rules_json,
               (SELECT COUNT(*) FROM src.setup_model_matches x
                 WHERE x.model_id=sm.model_id) AS matched_instance_rows,
               (SELECT COUNT(*) FROM src.setup_model_matches x
                 WHERE x.model_id=sm.model_id
                   AND EXISTS(
                     SELECT 1 FROM src.setup_model_rules r
                     WHERE r.model_id=sm.model_id
                   )
                   AND NOT EXISTS(
                     SELECT 1
                     FROM src.setup_model_rules r
                     LEFT JOIN src.setup_rule_states s
                       ON s.rule_id=r.rule_id AND s.instance_id=x.instance_id
                     WHERE r.model_id=sm.model_id
                       AND (
                         s.state IS NULL
                         OR (r.required_state='exclusion' AND s.state<>'absent')
                         OR (r.required_state<>'exclusion' AND s.state<>'present')
                       )
                   ))
                 AS fully_matched_instances
        FROM src.setup_models sm
    """,
    "query_confluence_profiles": """
        SELECT c.claim_id,
               json_extract(c.normalized_value_json,'$.confluence') AS confluence,
               json_extract(c.normalized_value_json,'$.wins') AS wins,
               json_extract(c.normalized_value_json,'$.losses') AS losses,
               json_extract(c.normalized_value_json,'$.eligible_count') AS eligible_count,
               json_extract(c.normalized_value_json,
                            '$.descriptive_selected_corpus_win_share')
                 AS descriptive_selected_corpus_win_share,
               json_extract(c.normalized_value_json,
                            '$.difference_from_selected_corpus_baseline')
                 AS difference_from_selected_corpus_baseline,
               c.claim_text,c.normalized_value_json,c.epistemic_status,
               c.resolution_status,c.limitations
        FROM src.claims c
        WHERE c.facet='selected_corpus_outcome_association'
    """,
}


REQUIRED_VIEWS = {
    "v_cardinal_setup_cards",
    "v_cardinal_setup_evidence",
    "v_collection_gaps",
    "v_discord_only_audit",
    "v_instrument_setup_comparison",
    "v_open_contradictions",
    "v_resolved_trade_outcomes",
    "v_selected_corpus_performance",
    "v_setup_rule_matrix",
    "v_unresolved_qa",
    "v_whole_server_coverage",
}


DATA_DICTIONARY_ROWS = (
    (
        "analysis_documents",
        "table",
        "Structured Discord-only analysis documents; start here for summaries.",
        "curated convenience",
        "Resolve material claims back to evidence_items and messages.",
    ),
    (
        "messages",
        "table",
        "One compact row per captured Discord message, including trust flags.",
        "source text snapshot",
        "Filter eligible_for_accepted_evidence=1 for analytical claims.",
    ),
    (
        "message_source_occurrences",
        "table",
        "Collection occurrences and quarantine state without bulky raw JSON.",
        "provenance summary",
        "Use the full database for raw occurrence payloads.",
    ),
    (
        "attachments",
        "table",
        "Discord media metadata with explicit exact-owned, exact-non-owned, or unresolved ownership state.",
        "source media archive index",
        "Only owned_exact rows may have archive/extraction state. non_owned_exact rows retain visible metadata only and cannot support model evidence.",
    ),
    (
        "attachment_extractions",
        "table",
        "Complete/partial OCR or manual artifacts with verified local path, SHA-256, byte size, and exact attachment provenance.",
        "verified local extraction index",
        "Failed/no-artifact attempts remain only in attachments JSON; NULL confidence means unreported and is never 1.0 by default.",
    ),
    (
        "query_rejection_blocks",
        "materialized query table",
        "RB observations and curated findings for identification, invalidation/non-actionability, timing, and confluence.",
        "curated/explicit claim index",
        "Keep claim_kind and epistemic_status separate.",
    ),
    (
        "query_qa",
        "materialized query table",
        "Answered, partial, community-context, and unanswered questions with exact message chains and authority fields.",
        "Q&A index",
        "Direct reply proves linkage, not correctness or mentor authority.",
    ),
    (
        "query_trade_episodes",
        "materialized query table",
        "All extracted episodes, outcomes, executed/context instruments, and confluences.",
        "episode index",
        "Use outcome_strict_comparison_eligible=1 for win/loss comparisons.",
    ),
    (
        "query_confluence_profiles",
        "materialized query table",
        "Strict selected-corpus confluence W/L/n summaries.",
        "descriptive association",
        "Shares overlap and are not causal or forward probabilities.",
    ),
    (
        "query_models",
        "materialized query table",
        "Discord-supported model cards with ordered rules.",
        "curated model index",
        "Models may overlap; signature membership does not prove rule satisfaction, and a fifth model is never forced.",
    ),
    (
        "query_setup_cards",
        "materialized query table",
        "Wide setup cards with JSON arrays and explicit missing-field flags.",
        "curated setup index",
        "Do not fill missing fields from outside knowledge.",
    ),
    (
        "query_setup_evidence",
        "materialized query table",
        "Claim-to-message evidence for setup instances.",
        "evidence linkage",
        "Accepted use requires eligible evidence.",
    ),
    (
        "query_server_coverage",
        "materialized query table",
        "Channel-level capture coverage summary.",
        "coverage summary",
        "Check query_collection_gaps and collection_runs before claiming completeness.",
    ),
    (
        "source_meta",
        "table",
        "Metadata copied from the authoritative analyzed database.",
        "source metadata",
        "Use llm_manifest for the source hash and companion build details.",
    ),
)


class CompanionError(RuntimeError):
    """Raised when an input cannot safely produce an LLM companion."""


SOURCE_WRITE_ACTIONS = {
    getattr(sqlite3, name)
    for name in (
        "SQLITE_INSERT",
        "SQLITE_UPDATE",
        "SQLITE_DELETE",
        "SQLITE_CREATE_INDEX",
        "SQLITE_CREATE_TABLE",
        "SQLITE_CREATE_TEMP_INDEX",
        "SQLITE_CREATE_TEMP_TABLE",
        "SQLITE_CREATE_TEMP_TRIGGER",
        "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER",
        "SQLITE_CREATE_VIEW",
        "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TABLE",
        "SQLITE_DROP_TEMP_INDEX",
        "SQLITE_DROP_TEMP_TABLE",
        "SQLITE_DROP_TEMP_TRIGGER",
        "SQLITE_DROP_TEMP_VIEW",
        "SQLITE_DROP_TRIGGER",
        "SQLITE_DROP_VIEW",
        "SQLITE_ALTER_TABLE",
        "SQLITE_REINDEX",
        "SQLITE_ANALYZE",
    )
    if hasattr(sqlite3, name)
}


def deny_attached_source_writes(
    action_code: int,
    _arg1: str | None,
    _arg2: str | None,
    database_name: str | None,
    _trigger_name: str | None,
) -> int:
    if database_name == "src" and action_code in SOURCE_WRITE_ACTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def source_connection(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def source_metadata(con: sqlite3.Connection) -> dict[str, str]:
    return {str(row[0]): str(row[1]) for row in con.execute("SELECT key,value FROM meta")}


def validate_source(con: sqlite3.Connection) -> dict[str, Any]:
    integrity = str(con.execute("PRAGMA integrity_check").fetchone()[0])
    foreign_keys = list(con.execute("PRAGMA foreign_key_check"))
    objects = {
        str(row[0]): str(row[1])
        for row in con.execute(
            "SELECT name,type FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    required_tables = set(NORMALIZED_TABLES) | {
        "meta",
        "collection_runs",
        "channel_inventory",
        "collection_units",
        "coverage_segments",
        "source_artifacts",
        "authors",
        "author_names",
        "messages",
        "message_source_occurrences",
        "quarantine_records",
        "attachments",
        "attachment_extractions",
        "message_embeds",
        "message_links",
        "message_mentions",
        "message_reactions",
        "message_relations",
        "message_versions",
    }
    missing_tables = sorted(name for name in required_tables if objects.get(name) != "table")
    missing_views = sorted(name for name in REQUIRED_VIEWS if objects.get(name) != "view")
    metadata = source_metadata(con) if objects.get("meta") == "table" else {}
    audit_count = (
        int(con.execute("SELECT COUNT(*) FROM v_discord_only_audit").fetchone()[0])
        if not missing_views
        else -1
    )
    analysis_runs = (
        int(con.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0])
        if objects.get("analysis_runs") == "table"
        else 0
    )
    attachment_columns = (
        {str(row[1]) for row in con.execute("PRAGMA table_info(attachments)")}
        if objects.get("attachments") == "table"
        else set()
    )
    required_attachment_columns = {
        "relation_type",
        "ownership_status",
        "ownership_evidence_json",
        "owned_for_capture",
        "eligible_for_attachment_evidence",
    }
    attachment_ownership_errors = -1
    if required_attachment_columns <= attachment_columns:
        attachment_ownership_errors = int(
            con.execute(
                """
                SELECT COUNT(*) FROM attachments a
                WHERE json_valid(a.ownership_evidence_json)=0
                   OR (a.ownership_status='non_owned_exact' AND (
                        a.owned_for_capture<>0
                        OR a.eligible_for_attachment_evidence<>0
                        OR a.capture_status<>'metadata_only'
                        OR a.capture_terminal<>0
                        OR a.capture_attempt_count<>0
                        OR a.capture_failure_code IS NOT NULL
                        OR a.capture_failure_detail IS NOT NULL
                        OR a.local_package_path IS NOT NULL
                        OR a.content_sha256 IS NOT NULL
                        OR a.extraction_status<>'not_attempted'
                        OR json_array_length(a.extraction_artifacts_json)<>0
                        OR a.archive_manifest_source_file_id IS NOT NULL
                        OR EXISTS(
                          SELECT 1 FROM attachment_extractions x
                          WHERE x.attachment_id=a.attachment_id
                        )
                        OR EXISTS(
                          SELECT 1 FROM evidence_items e
                          WHERE e.attachment_id=a.attachment_id
                        )
                      ))
                """
            ).fetchone()[0]
        )
    checks = {
        "integrity_ok": integrity == "ok",
        "foreign_keys_ok": not foreign_keys,
        "required_tables_present": not missing_tables,
        "required_views_present": not missing_views,
        "schema_is_cardinal_v2": metadata.get("schema_version", "").startswith("2."),
        "source_scope_discord_only": metadata.get("source_scope") == SOURCE_SCOPE,
        "outside_sources_zero": metadata.get("outside_sources_used") == "0",
        "discord_only_audit_empty": audit_count == 0,
        "analysis_layer_present": analysis_runs > 0,
        "attachment_ownership_columns_present": (
            required_attachment_columns <= attachment_columns
        ),
        "attachment_ownership_boundary_clean": attachment_ownership_errors == 0,
    }
    result = {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "metadata": metadata,
        "missing_tables": missing_tables,
        "missing_views": missing_views,
        "foreign_key_violations": [tuple(row) for row in foreign_keys],
        "discord_only_audit_count": audit_count,
        "analysis_run_count": analysis_runs,
    }
    if result["status"] != "passed":
        raise CompanionError("Source validation failed: " + json.dumps(result, sort_keys=True))
    return result


def create_table_as(con: sqlite3.Connection, name: str, query: str) -> None:
    con.execute(f"CREATE TABLE {quote_identifier(name)} AS {query}")


def create_indexes_and_views(con: sqlite3.Connection) -> None:
    statements = (
        "CREATE UNIQUE INDEX ux_messages_id ON messages(message_id)",
        "CREATE INDEX ix_messages_time ON messages(created_at_utc)",
        "CREATE INDEX ix_messages_channel_time ON messages(channel_id,created_at_utc)",
        "CREATE INDEX ix_messages_trust ON messages(eligible_for_accepted_evidence,evidence_trust_state)",
        "CREATE UNIQUE INDEX ux_occurrences_id ON message_source_occurrences(occurrence_id)",
        "CREATE INDEX ix_occurrences_message ON message_source_occurrences(message_id)",
        "CREATE UNIQUE INDEX ux_evidence_id ON evidence_items(evidence_id)",
        "CREATE INDEX ix_evidence_message ON evidence_items(message_id)",
        "CREATE UNIQUE INDEX ux_claims_id ON claims(claim_id)",
        "CREATE INDEX ix_claims_facet ON claims(facet,epistemic_status,resolution_status)",
        "CREATE INDEX ix_claim_evidence_claim ON claim_evidence(claim_id)",
        "CREATE INDEX ix_claim_evidence_evidence ON claim_evidence(evidence_id)",
        "CREATE UNIQUE INDEX ux_questions_id ON questions(question_id)",
        "CREATE INDEX ix_questions_topic ON questions(topic,resolution_status)",
        "CREATE UNIQUE INDEX ux_trade_episodes_id ON trade_episodes(trade_id)",
        "CREATE INDEX ix_trade_episode_strict ON trade_episodes(strict_comparison_eligible)",
        "CREATE UNIQUE INDEX ux_setup_instances_id ON setup_instances(instance_id)",
        "CREATE INDEX ix_setup_instruments_role ON setup_instruments(role,instrument_id)",
        "CREATE UNIQUE INDEX ux_setup_models_id ON setup_models(model_id)",
        "CREATE INDEX ix_query_rb_facet ON query_rejection_blocks(facet,claim_kind,epistemic_status)",
        "CREATE INDEX ix_query_qa_topic ON query_qa(topic,question_status)",
        "CREATE INDEX ix_query_trade_strict ON query_trade_episodes(outcome_strict_comparison_eligible,resolved_outcome)",
        "CREATE INDEX ix_query_confluence_n ON query_confluence_profiles(eligible_count)",
    )
    for statement in statements:
        con.execute(statement)

    con.executescript(
        """
        CREATE VIEW v_analysis_eligible_messages AS
        SELECT * FROM messages
        WHERE eligible_for_accepted_evidence=1
          AND evidence_trust_state IN ('trusted_canonical_recapture','trusted_source');

        CREATE VIEW v_quarantined_messages AS
        SELECT * FROM messages
        WHERE eligible_for_accepted_evidence=0
           OR evidence_trust_state NOT IN ('trusted_canonical_recapture','trusted_source');

        CREATE VIEW v_strict_trade_episodes AS
        SELECT * FROM query_trade_episodes
        WHERE outcome_strict_comparison_eligible=1
          AND resolved_outcome IN ('win','loss');

        CREATE VIEW v_discord_only_audit AS
        SELECT 'collection_run_outside_source' AS issue_type,
               CAST(run_id AS TEXT) AS entity_id,
               'outside_sources_used/source_scope violation' AS detail
        FROM collection_runs
        WHERE outside_sources_used<>0 OR source_scope<>'discord_only'
        UNION ALL
        SELECT 'analysis_run_outside_source',CAST(analysis_run_id AS TEXT),
               'outside_sources_used/source_scope violation'
        FROM analysis_runs
        WHERE outside_sources_used<>0 OR source_scope<>'discord_only'
        UNION ALL
        SELECT 'claim_outside_source',claim_id,
               'outside_sources_used/source_scope violation'
        FROM claims
        WHERE outside_sources_used<>0 OR source_scope<>'discord_only'
        UNION ALL
        SELECT 'evidence_outside_source',evidence_id,
               'outside_sources_used/source_scope violation'
        FROM evidence_items
        WHERE outside_sources_used<>0 OR source_scope<>'discord_only';
        """
    )

    con.execute(
        """
        CREATE VIRTUAL TABLE messages_fts USING fts5(
          message_id UNINDEXED,channel_name,thread_title,author_display_name,
          content_text,reply_to_content,visible_text,tokenize='unicode61'
        )
        """
    )
    con.execute(
        """
        INSERT INTO messages_fts(
          message_id,channel_name,thread_title,author_display_name,
          content_text,reply_to_content,visible_text
        )
        SELECT message_id,channel_name,thread_title,author_display_name,
               content_text,reply_to_content,visible_text
        FROM messages
        """
    )
    con.execute(
        """
        CREATE VIRTUAL TABLE claims_fts USING fts5(
          claim_id UNINDEXED,facet,claim_text,normalized_value_json,limitations,
          tokenize='unicode61'
        )
        """
    )
    con.execute(
        """
        INSERT INTO claims_fts(claim_id,facet,claim_text,normalized_value_json,limitations)
        SELECT claim_id,facet,claim_text,normalized_value_json,limitations FROM claims
        """
    )


def create_manifest(
    con: sqlite3.Connection,
    *,
    source_path: Path,
    source_sha256: str,
    source_validation: dict[str, Any],
) -> None:
    con.execute("CREATE TABLE llm_manifest(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    values = {
        "companion_schema_version": COMPANION_SCHEMA_VERSION,
        "created_at_utc": utc_now(),
        "source_database_name": source_path.name,
        "source_database_sha256": source_sha256,
        "source_database_is_authoritative": "1",
        "source_schema_version": source_validation["metadata"].get("schema_version", ""),
        "source_scope": SOURCE_SCOPE,
        "outside_sources_used": "0",
        "companion_role": "portable_query_snapshot",
        "raw_json_retained": "0",
        "raw_provenance_authority": "full_analyzed_database_only",
        "trust_policy": (
            "analytical claims require eligible_for_accepted_evidence=1; "
            "quarantined text remains searchable for audit only"
        ),
        "statistical_policy": (
            "selected-corpus shares are descriptive, overlapping, self-reported, "
            "author-clustered, non-causal, and not forward probabilities"
        ),
    }
    con.executemany(
        "INSERT INTO llm_manifest(key,value) VALUES(?,?)", sorted(values.items())
    )
    con.execute(
        """
        CREATE TABLE llm_data_dictionary(
          object_name TEXT NOT NULL,
          object_kind TEXT NOT NULL,
          purpose TEXT NOT NULL,
          authority TEXT NOT NULL,
          caution TEXT NOT NULL,
          PRIMARY KEY(object_name)
        )
        """
    )
    con.executemany(
        "INSERT INTO llm_data_dictionary VALUES(?,?,?,?,?)", DATA_DICTIONARY_ROWS
    )


def output_counts(con: sqlite3.Connection) -> dict[str, int]:
    names = (
        "messages",
        "message_source_occurrences",
        "quarantine_records",
        "analysis_documents",
        "attachments",
        "attachment_extractions",
        "evidence_items",
        "claims",
        "questions",
        "answers",
        "setup_instances",
        "trade_episodes",
        "setup_models",
        "query_rejection_blocks",
        "query_qa",
        "query_trade_episodes",
        "query_confluence_profiles",
        "query_models",
    )
    return {
        name: int(con.execute(f"SELECT COUNT(*) FROM {quote_identifier(name)}").fetchone()[0])
        for name in names
    }


def validate_output(
    con: sqlite3.Connection,
    *,
    source_counts: dict[str, int],
) -> dict[str, Any]:
    integrity = str(con.execute("PRAGMA integrity_check").fetchone()[0])
    discord_audit_count = int(con.execute("SELECT COUNT(*) FROM v_discord_only_audit").fetchone()[0])
    raw_json_columns = [
        (str(row[0]), str(row[1]))
        for row in con.execute(
            """
            SELECT m.name,p.name
            FROM sqlite_master m JOIN pragma_table_info(m.name) p
            WHERE m.type='table' AND lower(p.name)='raw_json'
            ORDER BY m.name
            """
        )
    ]
    counts = output_counts(con)
    attachment_ownership_errors = int(
        con.execute(
            """
            SELECT COUNT(*) FROM attachments a
            WHERE (a.ownership_status='non_owned_exact' AND (
                    a.owned_for_capture<>0
                    OR a.eligible_for_attachment_evidence<>0
                    OR a.capture_status<>'metadata_only'
                    OR a.capture_terminal<>0
                    OR a.capture_attempt_count<>0
                    OR a.capture_failure_code IS NOT NULL
                    OR a.capture_failure_detail IS NOT NULL
                    OR a.local_package_path IS NOT NULL
                    OR a.content_sha256 IS NOT NULL
                    OR a.extraction_status<>'not_attempted'
                    OR json_array_length(a.extraction_artifacts_json)<>0
                    OR a.archive_manifest_source_file_id IS NOT NULL
                    OR EXISTS(SELECT 1 FROM attachment_extractions x
                              WHERE x.attachment_id=a.attachment_id)
                    OR EXISTS(SELECT 1 FROM evidence_items e
                              WHERE e.attachment_id=a.attachment_id)
                 ))
            """
        ).fetchone()[0]
    )
    count_checks = {
        "messages_equal_source": counts["messages"] == source_counts["messages"],
        "occurrences_equal_source": (
            counts["message_source_occurrences"]
            == source_counts["message_source_occurrences"]
        ),
        "claims_equal_source": counts["claims"] == source_counts["claims"],
        "attachments_equal_source": counts["attachments"] == source_counts["attachments"],
        "attachment_extractions_equal_source": (
            counts["attachment_extractions"]
            == source_counts["attachment_extractions"]
        ),
        "trade_episodes_equal_source": (
            counts["trade_episodes"] == source_counts["trade_episodes"]
            and counts["query_trade_episodes"] == source_counts["trade_episodes"]
        ),
        "models_equal_source": (
            counts["setup_models"] == source_counts["setup_models"]
            and counts["query_models"] == source_counts["setup_models"]
        ),
        "setup_cards_equal_source": (
            int(con.execute("SELECT COUNT(*) FROM query_setup_cards").fetchone()[0])
            == source_counts["v_cardinal_setup_cards"]
        ),
        "message_fts_equal_messages": (
            int(con.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0])
            == counts["messages"]
        ),
        "claims_fts_equal_claims": (
            int(con.execute("SELECT COUNT(*) FROM claims_fts").fetchone()[0])
            == counts["claims"]
        ),
    }
    checks = {
        "integrity_ok": integrity == "ok",
        "discord_only_audit_empty": discord_audit_count == 0,
        "no_raw_json_columns": not raw_json_columns,
        "attachment_ownership_boundary_clean": attachment_ownership_errors == 0,
        **count_checks,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "counts": counts,
        "discord_only_audit_count": discord_audit_count,
        "raw_json_columns": raw_json_columns,
    }


def build_companion(
    source_database: Path,
    output_database: Path,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    source_database = source_database.resolve()
    output_database = output_database.resolve()
    if not source_database.is_file():
        raise FileNotFoundError(source_database)
    if source_database == output_database:
        raise CompanionError("Source and output database paths must differ")
    if output_database.exists() and not replace:
        raise FileExistsError(f"Output exists: {output_database}; use --replace explicitly")
    output_database.parent.mkdir(parents=True, exist_ok=True)
    building = output_database.with_name(output_database.name + ".building")
    if building.exists():
        raise FileExistsError(f"Stale build file exists: {building}")

    source_sha_before = sha256_file(source_database)
    with closing(source_connection(source_database)) as source:
        source_validation = validate_source(source)
        source_counts = {
            name: int(source.execute(f"SELECT COUNT(*) FROM {quote_identifier(name)}").fetchone()[0])
            for name in (
                "messages",
                "message_source_occurrences",
                "attachments",
                "attachment_extractions",
                "claims",
                "trade_episodes",
                "setup_models",
                "v_cardinal_setup_cards",
            )
        }

    created_building = False
    try:
        con = sqlite3.connect(building)
        created_building = True
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA temp_store=MEMORY")
        # ATTACH does not consistently honor URI filenames on Windows builds of
        # SQLite.  Every statement below addresses ``src`` only in SELECTs, and
        # the authorizer plus before/after SHA-256 check enforce immutability.
        con.execute("ATTACH DATABASE ? AS src", (str(source_database),))
        con.set_authorizer(deny_attached_source_writes)
        con.execute("BEGIN IMMEDIATE")

        for name, query in SLIM_TABLE_QUERIES.items():
            create_table_as(con, name, query)
        for name in NORMALIZED_TABLES:
            create_table_as(
                con,
                name,
                f"SELECT * FROM src.{quote_identifier(name)}",
            )
        for name, query in MATERIALIZED_VIEW_QUERIES.items():
            create_table_as(con, name, query)
        for name, query in CUSTOM_QUERY_TABLES.items():
            create_table_as(con, name, query)

        create_indexes_and_views(con)
        create_manifest(
            con,
            source_path=source_database,
            source_sha256=source_sha_before,
            source_validation=source_validation,
        )
        con.commit()
        validation = validate_output(con, source_counts=source_counts)
        if validation["status"] != "passed":
            raise CompanionError("Output validation failed: " + json.dumps(validation, sort_keys=True))
        # Detach before ANALYZE so SQLite cannot create/update statistics in the
        # attached authoritative database.
        con.execute("DETACH DATABASE src")
        con.set_authorizer(None)
        con.execute("ANALYZE")
        con.commit()
        con.execute("VACUUM")
        con.close()

        source_sha_after = sha256_file(source_database)
        if source_sha_after != source_sha_before:
            raise CompanionError("Source database changed while the companion was built")

        if output_database.exists():
            if not replace:
                raise FileExistsError(output_database)
        # os.replace preserves the old output until the completed build is ready.
        os.replace(building, output_database)
        created_building = False
        output_sha = sha256_file(output_database)
        return {
            "status": "passed",
            "database": str(output_database),
            "database_sha256": output_sha,
            "database_bytes": output_database.stat().st_size,
            "source_database": str(source_database),
            "source_database_sha256": source_sha_before,
            "source_database_bytes": source_database.stat().st_size,
            "source_database_unchanged": True,
            "source_scope": SOURCE_SCOPE,
            "outside_sources_used": 0,
            "source_validation": source_validation,
            "validation": validation,
        }
    except Exception:
        try:
            con.close()
        except Exception:
            pass
        if created_building and building.exists():
            building.unlink()
        raise


def write_json_atomic(path: Path, value: Any, *, replace: bool) -> None:
    path = path.resolve()
    if path.exists() and not replace:
        raise FileExistsError(f"Report exists: {path}; use --replace explicitly")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        raise FileExistsError(f"Stale report build file exists: {temporary}")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        # Replace only after the complete report has been written and flushed.
        os.replace(temporary, path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--database", required=True, type=Path, help="Analyzed Cardinal v2 SQLite database")
    value.add_argument("--output", required=True, type=Path, help="New compact SQLite companion")
    value.add_argument("--report", type=Path, help="Optional JSON build/validation report")
    value.add_argument("--replace", action="store_true", help="Explicitly replace output/report files")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    source = args.database.resolve()
    output = args.output.resolve()
    report_path = args.report.resolve() if args.report else None
    if report_path and report_path in {source, output}:
        print("ERROR: report path must differ from source and output database paths", file=sys.stderr)
        return 2
    if report_path and report_path.exists() and not args.replace:
        print(f"ERROR: report exists: {report_path}; use --replace explicitly", file=sys.stderr)
        return 1
    try:
        report = build_companion(source, output, replace=args.replace)
        if report_path:
            write_json_atomic(report_path, report, replace=args.replace)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "database": report["database"],
                    "database_sha256": report["database_sha256"],
                    "database_bytes": report["database_bytes"],
                    "source_database_unchanged": report["source_database_unchanged"],
                    "counts": report["validation"]["counts"],
                    "report": str(report_path) if report_path else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
