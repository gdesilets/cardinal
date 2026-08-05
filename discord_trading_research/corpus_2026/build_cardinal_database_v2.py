"""Build an evidence-first, Discord-only SQLite corpus for Cardinal.

The raw layer retains every discovered Discord message and source occurrence. The
analysis layer is intentionally empty unless a separate annotation process creates
Discord-backed evidence and claims. This module never seeds trading definitions,
timezones, instruments, setup rules, or outcomes from the Cardinal skill.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Sequence

import authorized_collection_scope
import reply_provenance_contract
import timestamp_scope_revalidation
import discord_attachment_archiver


SCHEMA_VERSION = "2.4.0"
SOURCE_SCOPE = "discord_only"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_AUTHORIZED_SCOPE = SCRIPT_DIR / "authorized_collection_scope.json"


SCHEMA_SQL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE schema_migrations (
  migration_id INTEGER PRIMARY KEY,
  from_version TEXT,
  to_version TEXT NOT NULL,
  applied_at_utc TEXT NOT NULL,
  source_db_sha256 TEXT,
  script_sha256 TEXT NOT NULL,
  row_counts_json TEXT NOT NULL DEFAULT '{}',
  validation_sha256 TEXT
);

CREATE TABLE collection_runs (
  run_id INTEGER PRIMARY KEY,
  guild_id TEXT NOT NULL,
  guild_name TEXT,
  window_start_utc TEXT NOT NULL,
  window_end_utc TEXT NOT NULL,
  scope TEXT NOT NULL,
  source_scope TEXT NOT NULL DEFAULT 'discord_only'
    CHECK(source_scope = 'discord_only'),
  outside_sources_used INTEGER NOT NULL DEFAULT 0
    CHECK(outside_sources_used = 0),
  status TEXT NOT NULL
    CHECK(status IN ('complete','partial','in_progress','failed')),
  collected_at_utc TEXT,
  built_at_utc TEXT NOT NULL,
  methodology TEXT NOT NULL,
  limitations TEXT NOT NULL DEFAULT ''
);

CREATE TABLE channel_inventory (
  channel_id TEXT PRIMARY KEY,
  guild_id TEXT NOT NULL,
  parent_channel_id TEXT,
  name TEXT,
  kind TEXT NOT NULL DEFAULT 'unknown',
  exact_id_known INTEGER NOT NULL DEFAULT 0 CHECK(exact_id_known IN (0,1)),
  is_archived INTEGER CHECK(is_archived IN (0,1) OR is_archived IS NULL),
  is_accessible INTEGER CHECK(is_accessible IN (0,1) OR is_accessible IS NULL),
  inventory_basis TEXT NOT NULL,
  discovered_at_utc TEXT,
  first_seen_utc TEXT,
  last_seen_utc TEXT,
  source_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE collection_units (
  unit_id TEXT PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES collection_runs(run_id),
  channel_id TEXT REFERENCES channel_inventory(channel_id),
  collection_name TEXT NOT NULL,
  unit_type TEXT NOT NULL,
  window_start_utc TEXT NOT NULL,
  window_end_utc TEXT NOT NULL,
  collection_method TEXT NOT NULL,
  query_text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL
    CHECK(status IN ('complete','partial','inaccessible','not_found','failed','unknown')),
  artifact_declared_complete INTEGER
    CHECK(artifact_declared_complete IN (0,1) OR artifact_declared_complete IS NULL),
  occurrences_seen INTEGER NOT NULL DEFAULT 0 CHECK(occurrences_seen >= 0),
  unique_messages_seen INTEGER NOT NULL DEFAULT 0 CHECK(unique_messages_seen >= 0),
  earliest_message_utc TEXT,
  latest_message_utc TEXT,
  gap_notes TEXT NOT NULL DEFAULT '',
  UNIQUE(run_id, channel_id, collection_name, query_text, window_start_utc, window_end_utc)
);

CREATE TABLE coverage_segments (
  segment_id TEXT PRIMARY KEY,
  unit_id TEXT NOT NULL REFERENCES collection_units(unit_id),
  segment_start_utc TEXT NOT NULL,
  segment_end_utc TEXT NOT NULL,
  status TEXT NOT NULL
    CHECK(status IN ('complete','partial','inaccessible','not_found','failed','unknown')),
  returned_count INTEGER NOT NULL DEFAULT 0 CHECK(returned_count >= 0),
  first_message_id TEXT,
  last_message_id TEXT,
  duplicate_count INTEGER NOT NULL DEFAULT 0 CHECK(duplicate_count >= 0),
  error_text TEXT,
  artifact_sha256 TEXT,
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE source_artifacts (
  artifact_id TEXT PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES collection_runs(run_id),
  parent_artifact_id TEXT REFERENCES source_artifacts(artifact_id),
  source_file TEXT NOT NULL,
  sha256 TEXT,
  collection_method TEXT NOT NULL,
  collection_name TEXT NOT NULL DEFAULT '',
  query_text TEXT NOT NULL DEFAULT '',
  captured_at_utc TEXT,
  declared_artifact_complete INTEGER
    CHECK(declared_artifact_complete IN (0,1) OR declared_artifact_complete IS NULL),
  descriptor_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(run_id, source_file)
);

CREATE TABLE source_segments (
  segment_id TEXT PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES collection_runs(run_id),
  artifact_id TEXT REFERENCES source_artifacts(artifact_id),
  channel_id TEXT REFERENCES channel_inventory(channel_id),
  segment_start_utc TEXT,
  segment_end_utc TEXT,
  status TEXT NOT NULL DEFAULT 'unknown',
  message_count INTEGER NOT NULL DEFAULT 0 CHECK(message_count >= 0),
  occurrence_count INTEGER NOT NULL DEFAULT 0 CHECK(occurrence_count >= 0),
  raw_json TEXT NOT NULL
);

CREATE TABLE authors (
  author_id TEXT PRIMARY KEY,
  discord_user_id TEXT,
  user_id_exact INTEGER NOT NULL DEFAULT 0 CHECK(user_id_exact IN (0,1)),
  identity_resolution TEXT NOT NULL,
  surrogate_key TEXT,
  first_seen_utc TEXT,
  last_seen_utc TEXT,
  source_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE author_names (
  author_id TEXT NOT NULL REFERENCES authors(author_id),
  display_name TEXT NOT NULL,
  valid_from_utc TEXT,
  valid_to_utc TEXT,
  evidence_message_id TEXT REFERENCES messages(message_id),
  PRIMARY KEY(author_id, display_name, valid_from_utc)
);

CREATE TABLE messages (
  message_id TEXT PRIMARY KEY,
  message_id_exact INTEGER NOT NULL DEFAULT 1 CHECK(message_id_exact IN (0,1)),
  run_id INTEGER NOT NULL REFERENCES collection_runs(run_id),
  guild_id TEXT NOT NULL,
  channel_id TEXT NOT NULL REFERENCES channel_inventory(channel_id),
  parent_channel_id TEXT,
  channel_name TEXT,
  thread_title TEXT,
  author_id TEXT REFERENCES authors(author_id),
  author_display_name TEXT,
  created_at_utc TEXT,
  displayed_time TEXT,
  edited INTEGER NOT NULL DEFAULT 0 CHECK(edited IN (0,1)),
  is_original_poster INTEGER NOT NULL DEFAULT 0 CHECK(is_original_poster IN (0,1)),
  reply_to_message_id TEXT,
  reply_to_content TEXT,
  reply_target_state TEXT NOT NULL DEFAULT 'not_applicable'
    CHECK(reply_target_state IN ('resolved','outside_window','context_stub','deleted','inaccessible','unavailable','not_applicable')),
  content_text TEXT NOT NULL DEFAULT '',
  visible_text TEXT,
  content_sha256 TEXT NOT NULL,
  permalink TEXT,
  permalink_confidence TEXT NOT NULL DEFAULT 'unavailable'
    CHECK(permalink_confidence IN ('exact','inferred','unavailable')),
  evidence_trust_state TEXT NOT NULL DEFAULT 'trusted_source'
    CHECK(evidence_trust_state IN ('trusted_canonical_recapture','trusted_source','quarantined_only','untrusted_noncanonical_only','conflicting')),
  eligible_for_accepted_evidence INTEGER NOT NULL DEFAULT 1
    CHECK(eligible_for_accepted_evidence IN (0,1)),
  has_quarantined_occurrences INTEGER NOT NULL DEFAULT 0
    CHECK(has_quarantined_occurrences IN (0,1)),
  trusted_canonical_occurrence_count INTEGER NOT NULL DEFAULT 0
    CHECK(trusted_canonical_occurrence_count >= 0),
  quarantined_occurrence_count INTEGER NOT NULL DEFAULT 0
    CHECK(quarantined_occurrence_count >= 0),
  canonical_selection_method TEXT NOT NULL,
  raw_json TEXT NOT NULL
);

CREATE INDEX idx_messages_channel_time
  ON messages(channel_id, created_at_utc, message_id);
CREATE INDEX idx_messages_author_time
  ON messages(author_id, created_at_utc);
CREATE INDEX idx_messages_reply
  ON messages(reply_to_message_id);
CREATE INDEX idx_messages_trust
  ON messages(eligible_for_accepted_evidence, evidence_trust_state);

CREATE TABLE message_versions (
  message_id TEXT NOT NULL REFERENCES messages(message_id),
  version_no INTEGER NOT NULL CHECK(version_no > 0),
  content_text TEXT NOT NULL DEFAULT '',
  visible_text TEXT,
  edited_at_utc TEXT,
  content_sha256 TEXT NOT NULL,
  version_basis TEXT NOT NULL,
  artifact_id TEXT REFERENCES source_artifacts(artifact_id),
  raw_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(message_id, version_no),
  UNIQUE(message_id, content_sha256)
);

CREATE TABLE message_relations (
  from_message_id TEXT NOT NULL REFERENCES messages(message_id),
  to_message_id TEXT NOT NULL,
  relation_type TEXT NOT NULL
    CHECK(relation_type IN ('reply','quotes','references','thread_context')),
  linkage_confidence REAL NOT NULL DEFAULT 1.0
    CHECK(linkage_confidence BETWEEN 0 AND 1),
  source_artifact_id TEXT REFERENCES source_artifacts(artifact_id),
  PRIMARY KEY(from_message_id, to_message_id, relation_type)
);

CREATE TABLE attachments (
  attachment_id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL REFERENCES messages(message_id),
  attachment_id_exact INTEGER NOT NULL DEFAULT 1 CHECK(attachment_id_exact IN (0,1)),
  filename TEXT,
  discord_url TEXT,
  source_channel_id TEXT,
  relation_type TEXT NOT NULL DEFAULT 'unresolved',
  ownership_status TEXT NOT NULL DEFAULT 'unresolved'
    CHECK(ownership_status IN ('owned_exact','non_owned_exact','unresolved')),
  ownership_evidence_json TEXT NOT NULL DEFAULT '{}',
  owned_for_capture INTEGER NOT NULL DEFAULT 0 CHECK(owned_for_capture IN (0,1)),
  eligible_for_attachment_evidence INTEGER NOT NULL DEFAULT 0
    CHECK(eligible_for_attachment_evidence IN (0,1)),
  mime_type TEXT,
  media_kind TEXT,
  width INTEGER,
  height INTEGER,
  byte_size INTEGER,
  content_sha256 TEXT,
  local_package_path TEXT,
  capture_status TEXT NOT NULL DEFAULT 'metadata_only'
    CHECK(capture_status IN ('metadata_only','pending','downloaded','unavailable','failed')),
  capture_terminal INTEGER NOT NULL DEFAULT 0 CHECK(capture_terminal IN (0,1)),
  capture_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(capture_attempt_count >= 0),
  capture_attempts_json TEXT NOT NULL DEFAULT '[]',
  capture_failure_code TEXT,
  capture_failure_detail TEXT,
  extraction_status TEXT NOT NULL DEFAULT 'not_attempted'
    CHECK(extraction_status IN ('not_attempted','complete','partial','failed')),
  extraction_artifacts_json TEXT NOT NULL DEFAULT '[]',
  archive_manifest_source_file_id TEXT,
  chart_claim_eligible INTEGER NOT NULL DEFAULT 0 CHECK(chart_claim_eligible=0),
  raw_json TEXT NOT NULL DEFAULT '{}',
  notes TEXT NOT NULL DEFAULT '',
  CHECK((ownership_status='owned_exact' AND owned_for_capture=1)
     OR (ownership_status<>'owned_exact' AND owned_for_capture=0)),
  CHECK(eligible_for_attachment_evidence<=owned_for_capture)
);

CREATE TABLE attachment_extractions (
  extraction_id TEXT PRIMARY KEY,
  attachment_id TEXT NOT NULL REFERENCES attachments(attachment_id),
  analysis_run_id INTEGER REFERENCES analysis_runs(analysis_run_id),
  method TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('complete','partial')),
  extracted_text TEXT NOT NULL DEFAULT '',
  local_package_path TEXT NOT NULL,
  content_sha256 TEXT NOT NULL CHECK(LENGTH(content_sha256)=64),
  byte_size INTEGER NOT NULL CHECK(byte_size>0),
  artifact_verified INTEGER NOT NULL CHECK(artifact_verified=1),
  locator_json TEXT NOT NULL DEFAULT '{}',
  confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
  source_scope TEXT NOT NULL DEFAULT 'discord_only' CHECK(source_scope='discord_only'),
  outside_sources_used INTEGER NOT NULL DEFAULT 0 CHECK(outside_sources_used=0),
  created_at_utc TEXT NOT NULL
);

CREATE TABLE message_links (
  message_id TEXT NOT NULL REFERENCES messages(message_id),
  url TEXT NOT NULL,
  link_kind TEXT NOT NULL DEFAULT 'unknown',
  PRIMARY KEY(message_id, url)
);

CREATE TABLE message_mentions (
  message_id TEXT NOT NULL REFERENCES messages(message_id),
  mentioned_id TEXT NOT NULL,
  mention_kind TEXT NOT NULL,
  raw_text TEXT,
  PRIMARY KEY(message_id, mentioned_id, mention_kind)
);

CREATE TABLE message_reactions (
  message_id TEXT NOT NULL REFERENCES messages(message_id),
  emoji_key TEXT NOT NULL,
  reaction_count INTEGER NOT NULL DEFAULT 1 CHECK(reaction_count >= 0),
  reacted_by_current_user INTEGER CHECK(reacted_by_current_user IN (0,1) OR reacted_by_current_user IS NULL),
  raw_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(message_id, emoji_key)
);

CREATE TABLE message_embeds (
  embed_id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL REFERENCES messages(message_id),
  embed_type TEXT,
  title TEXT,
  description TEXT,
  url TEXT,
  raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE message_source_occurrences (
  occurrence_id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL REFERENCES messages(message_id),
  artifact_id TEXT NOT NULL REFERENCES source_artifacts(artifact_id),
  collection_name TEXT NOT NULL,
  query_text TEXT NOT NULL DEFAULT '',
  result_index INTEGER,
  page_number INTEGER,
  segment_start_utc TEXT,
  segment_end_utc TEXT,
  artifact_declared_complete INTEGER
    CHECK(artifact_declared_complete IN (0,1) OR artifact_declared_complete IS NULL),
  source_kind TEXT NOT NULL DEFAULT 'unknown',
  migration_source INTEGER NOT NULL DEFAULT 0 CHECK(migration_source IN (0,1)),
  quarantined INTEGER NOT NULL DEFAULT 0 CHECK(quarantined IN (0,1)),
  trusted_canonical INTEGER NOT NULL DEFAULT 0 CHECK(trusted_canonical IN (0,1)),
  trust_state TEXT NOT NULL DEFAULT 'trusted_source'
    CHECK(trust_state IN ('trusted_canonical','trusted_source','quarantined_migration','quarantined_other','untrusted_noncanonical')),
  quarantine_reasons_json TEXT NOT NULL DEFAULT '[]',
  raw_json TEXT NOT NULL,
  field_variants_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(message_id, artifact_id, collection_name, query_text, result_index, page_number)
);

CREATE INDEX idx_occurrences_message ON message_source_occurrences(message_id);
CREATE INDEX idx_occurrences_artifact ON message_source_occurrences(artifact_id);
CREATE INDEX idx_occurrences_collection ON message_source_occurrences(collection_name, query_text);
CREATE INDEX idx_occurrences_trust
  ON message_source_occurrences(message_id, trusted_canonical, quarantined, migration_source);

CREATE TABLE quarantine_records (
  quarantine_id TEXT PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES collection_runs(run_id),
  artifact_id TEXT REFERENCES source_artifacts(artifact_id),
  message_id TEXT REFERENCES messages(message_id),
  occurrence_id TEXT REFERENCES message_source_occurrences(occurrence_id),
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'quarantined',
  raw_json TEXT NOT NULL
);

CREATE TABLE legacy_provenance_records (
  record_id TEXT PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES collection_runs(run_id),
  artifact_id TEXT REFERENCES source_artifacts(artifact_id),
  raw_json TEXT NOT NULL
);

CREATE TABLE relevance_annotations (
  message_id TEXT NOT NULL REFERENCES messages(message_id),
  analysis_run_id INTEGER NOT NULL REFERENCES analysis_runs(analysis_run_id),
  label TEXT NOT NULL,
  score REAL,
  reason TEXT NOT NULL,
  PRIMARY KEY(message_id, analysis_run_id, label)
);

CREATE TABLE analysis_runs (
  analysis_run_id INTEGER PRIMARY KEY,
  collection_run_id INTEGER NOT NULL REFERENCES collection_runs(run_id),
  schema_version TEXT NOT NULL,
  method TEXT NOT NULL,
  script_sha256 TEXT,
  created_at_utc TEXT NOT NULL,
  source_scope TEXT NOT NULL DEFAULT 'discord_only' CHECK(source_scope='discord_only'),
  outside_sources_used INTEGER NOT NULL DEFAULT 0 CHECK(outside_sources_used=0),
  limitations TEXT NOT NULL DEFAULT ''
);

CREATE TABLE analysis_entities (
  entity_id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  created_analysis_run_id INTEGER NOT NULL REFERENCES analysis_runs(analysis_run_id),
  parent_entity_id TEXT REFERENCES analysis_entities(entity_id),
  root_entity_id TEXT REFERENCES analysis_entities(entity_id),
  lifecycle_status TEXT NOT NULL DEFAULT 'active',
  source_scope TEXT NOT NULL DEFAULT 'discord_only' CHECK(source_scope='discord_only'),
  outside_sources_used INTEGER NOT NULL DEFAULT 0 CHECK(outside_sources_used=0),
  notes TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_entities_root ON analysis_entities(root_entity_id, entity_type);
CREATE INDEX idx_entities_parent ON analysis_entities(parent_entity_id);

CREATE TABLE evidence_items (
  evidence_id TEXT PRIMARY KEY,
  analysis_run_id INTEGER NOT NULL REFERENCES analysis_runs(analysis_run_id),
  message_id TEXT REFERENCES messages(message_id),
  attachment_id TEXT REFERENCES attachments(attachment_id),
  source_type TEXT NOT NULL
    CHECK(source_type IN ('message_text','reply_context','thread_context','attachment_ocr','attachment_visible_label','attachment_manual_visual','attachment_metadata')),
  exact_excerpt TEXT NOT NULL DEFAULT '',
  char_start INTEGER,
  char_end INTEGER,
  locator_json TEXT NOT NULL DEFAULT '{}',
  content_sha256 TEXT NOT NULL,
  extraction_method TEXT NOT NULL,
  extraction_confidence REAL NOT NULL CHECK(extraction_confidence BETWEEN 0 AND 1),
  evidence_trust_state TEXT NOT NULL DEFAULT 'trusted_source'
    CHECK(evidence_trust_state IN ('trusted_canonical_recapture','trusted_source','quarantined_only','untrusted_noncanonical_only','conflicting')),
  eligible_for_accepted_claims INTEGER NOT NULL DEFAULT 1
    CHECK(eligible_for_accepted_claims IN (0,1)),
  source_scope TEXT NOT NULL DEFAULT 'discord_only' CHECK(source_scope='discord_only'),
  outside_sources_used INTEGER NOT NULL DEFAULT 0 CHECK(outside_sources_used=0),
  CHECK(message_id IS NOT NULL OR attachment_id IS NOT NULL)
);

CREATE TABLE discord_roles (
  role_id TEXT PRIMARY KEY,
  guild_id TEXT NOT NULL,
  role_name TEXT NOT NULL,
  role_id_exact INTEGER NOT NULL DEFAULT 0 CHECK(role_id_exact IN (0,1)),
  source_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE author_role_observations (
  observation_id TEXT PRIMARY KEY,
  author_id TEXT NOT NULL REFERENCES authors(author_id),
  role_id TEXT NOT NULL REFERENCES discord_roles(role_id),
  observed_at_utc TEXT,
  evidence_id TEXT NOT NULL REFERENCES evidence_items(evidence_id),
  confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1)
);

CREATE TABLE authority_assignments (
  assignment_id TEXT PRIMARY KEY,
  author_id TEXT NOT NULL REFERENCES authors(author_id),
  authority_class TEXT NOT NULL,
  basis TEXT NOT NULL,
  valid_from_utc TEXT,
  valid_to_utc TEXT,
  evidence_id TEXT NOT NULL REFERENCES evidence_items(evidence_id),
  confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE claims (
  claim_id TEXT PRIMARY KEY,
  subject_entity_id TEXT NOT NULL REFERENCES analysis_entities(entity_id),
  facet TEXT NOT NULL,
  claim_text TEXT NOT NULL,
  normalized_value_json TEXT,
  claim_kind TEXT NOT NULL
    CHECK(claim_kind IN ('explicit_rule','explicit_example','explicit_outcome','explicit_question','explicit_answer','linked_context','observed_association','curated_synthesis','insufficient_evidence')),
  epistemic_status TEXT NOT NULL
    CHECK(epistemic_status IN ('explicit_source','linked_context','observed_association','curated_synthesis','insufficient_evidence')),
  resolution_status TEXT NOT NULL
    CHECK(resolution_status IN ('accepted','unresolved','conflicting','qualified','superseded','rejected')),
  speaker_author_id TEXT REFERENCES authors(author_id),
  authority_assignment_id TEXT REFERENCES authority_assignments(assignment_id),
  analysis_run_id INTEGER NOT NULL REFERENCES analysis_runs(analysis_run_id),
  source_scope TEXT NOT NULL DEFAULT 'discord_only' CHECK(source_scope='discord_only'),
  outside_sources_used INTEGER NOT NULL DEFAULT 0 CHECK(outside_sources_used=0),
  created_at_utc TEXT NOT NULL,
  limitations TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_claims_subject_facet ON claims(subject_entity_id, facet, resolution_status);
CREATE INDEX idx_claims_epistemic ON claims(epistemic_status, resolution_status);

CREATE TABLE claim_evidence (
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  evidence_id TEXT NOT NULL REFERENCES evidence_items(evidence_id),
  evidence_role TEXT NOT NULL
    CHECK(evidence_role IN ('supports','contradicts','qualifies','defines','outcome','answers')),
  PRIMARY KEY(claim_id, evidence_id, evidence_role)
);

CREATE TABLE confidence_assessments (
  assessment_id TEXT PRIMARY KEY,
  claim_id TEXT REFERENCES claims(claim_id),
  entity_id TEXT REFERENCES analysis_entities(entity_id),
  dimension TEXT NOT NULL
    CHECK(dimension IN ('extraction','linkage','normalization','outcome_resolution','qa_resolution','corpus_support')),
  score REAL NOT NULL CHECK(score BETWEEN 0 AND 1),
  band TEXT,
  basis_text TEXT NOT NULL,
  assessor_method TEXT NOT NULL,
  sample_size INTEGER CHECK(sample_size >= 0 OR sample_size IS NULL),
  caveat TEXT NOT NULL DEFAULT '',
  analysis_run_id INTEGER NOT NULL REFERENCES analysis_runs(analysis_run_id),
  CHECK(claim_id IS NOT NULL OR entity_id IS NOT NULL)
);

CREATE TABLE claim_relations (
  from_claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  to_claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  relation_type TEXT NOT NULL
    CHECK(relation_type IN ('supports','contradicts','qualifies','supersedes','duplicates','answers')),
  notes TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(from_claim_id, to_claim_id, relation_type)
);

CREATE TABLE contradiction_sets (
  contradiction_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  topic TEXT NOT NULL,
  resolution_status TEXT NOT NULL
    CHECK(resolution_status IN ('open','qualified','resolved')),
  resolution_summary TEXT,
  resolved_claim_id TEXT REFERENCES claims(claim_id),
  limitations TEXT NOT NULL DEFAULT ''
);

CREATE TABLE contradiction_members (
  contradiction_id TEXT NOT NULL REFERENCES contradiction_sets(contradiction_id),
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  stance TEXT NOT NULL CHECK(stance IN ('supports','opposes','qualifies','context')),
  notes TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(contradiction_id, claim_id)
);

CREATE TABLE concept_terms (
  term_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  canonical_name TEXT NOT NULL,
  term_class TEXT NOT NULL,
  discord_definition TEXT,
  definition_status TEXT NOT NULL
    CHECK(definition_status IN ('explicit','curated_from_discord','unknown')),
  name_claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  definition_claim_id TEXT REFERENCES claims(claim_id),
  limitations TEXT NOT NULL DEFAULT '',
  UNIQUE(canonical_name, term_class)
);

CREATE TABLE term_aliases (
  alias_id TEXT PRIMARY KEY,
  term_id TEXT NOT NULL REFERENCES concept_terms(term_id),
  alias_text TEXT NOT NULL,
  alias_kind TEXT NOT NULL
    CHECK(alias_kind IN ('exact_author_term','abbreviation','curated_normalization')),
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  UNIQUE(term_id, alias_text)
);

CREATE TABLE setup_models (
  model_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  canonical_name TEXT NOT NULL,
  thesis TEXT,
  evidence_status TEXT NOT NULL
    CHECK(evidence_status IN ('documented','provisional','conflicting','insufficient_evidence')),
  lifecycle_status TEXT NOT NULL DEFAULT 'active',
  identity_claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  limitations TEXT NOT NULL DEFAULT '',
  UNIQUE(canonical_name)
);

CREATE TABLE setup_aliases (
  alias_id TEXT PRIMARY KEY,
  model_id TEXT NOT NULL REFERENCES setup_models(model_id),
  alias_text TEXT NOT NULL,
  alias_kind TEXT NOT NULL
    CHECK(alias_kind IN ('exact_author_term','abbreviation','curated_normalization')),
  first_seen_message_id TEXT REFERENCES messages(message_id),
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  UNIQUE(model_id, alias_text)
);

CREATE TABLE setup_model_rules (
  rule_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  model_id TEXT NOT NULL REFERENCES setup_models(model_id),
  rule_order INTEGER NOT NULL CHECK(rule_order > 0),
  rule_type TEXT NOT NULL
    CHECK(rule_type IN ('context','eligibility','identity','sequence','confirmation','entry','invalidation','stop','target','management','no_trade')),
  rule_text TEXT NOT NULL,
  required_state TEXT NOT NULL
    CHECK(required_state IN ('required','supportive','optional','exclusion')),
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  UNIQUE(model_id, rule_order)
);

CREATE TABLE setup_instances (
  instance_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  occurrence_type TEXT NOT NULL
    CHECK(occurrence_type IN ('trade_journal','lesson','question_answer','live_call','retrospective','chart_example','hypothetical','unknown')),
  primary_message_id TEXT NOT NULL REFERENCES messages(message_id),
  primary_author_id TEXT REFERENCES authors(author_id),
  occurrence_date_text TEXT,
  occurrence_date_claim_id TEXT REFERENCES claims(claim_id),
  direction TEXT CHECK(direction IN ('long','short','mixed','neutral') OR direction IS NULL),
  direction_claim_id TEXT REFERENCES claims(claim_id),
  lifecycle_state TEXT,
  identity_resolution_status TEXT NOT NULL
    CHECK(identity_resolution_status IN ('explicit','linked','derived','unresolved','conflicting')),
  identity_claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  notes TEXT NOT NULL DEFAULT '',
  CHECK((direction IS NULL AND direction_claim_id IS NULL) OR
        (direction IS NOT NULL AND direction_claim_id IS NOT NULL)),
  CHECK((occurrence_date_text IS NULL AND occurrence_date_claim_id IS NULL) OR
        (occurrence_date_text IS NOT NULL AND occurrence_date_claim_id IS NOT NULL))
);

CREATE TABLE setup_model_matches (
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  model_id TEXT NOT NULL REFERENCES setup_models(model_id),
  match_status TEXT NOT NULL
    CHECK(match_status IN ('explicit','candidate','derived','rejected','conflicting')),
  match_method TEXT NOT NULL,
  matched_rule_count INTEGER NOT NULL DEFAULT 0 CHECK(matched_rule_count >= 0),
  missing_rule_count INTEGER NOT NULL DEFAULT 0 CHECK(missing_rule_count >= 0),
  violated_rule_count INTEGER NOT NULL DEFAULT 0 CHECK(violated_rule_count >= 0),
  confidence_assessment_id TEXT REFERENCES confidence_assessments(assessment_id),
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  PRIMARY KEY(instance_id, model_id)
);

CREATE TABLE setup_rule_states (
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  rule_id TEXT NOT NULL REFERENCES setup_model_rules(rule_id),
  state TEXT NOT NULL CHECK(state IN ('present','absent','violated','unknown')),
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  PRIMARY KEY(instance_id, rule_id)
);

CREATE TABLE instruments (
  instrument_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  canonical_symbol TEXT NOT NULL,
  asset_class_as_stated TEXT,
  name_claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  UNIQUE(canonical_symbol)
);

CREATE TABLE instrument_aliases (
  alias_id TEXT PRIMARY KEY,
  instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
  alias_text TEXT NOT NULL,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  UNIQUE(instrument_id, alias_text)
);

CREATE TABLE setup_instruments (
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
  role TEXT NOT NULL
    CHECK(role IN ('executed','market_context','intermarket_comparison','hedge','unknown')),
  raw_text TEXT NOT NULL,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  PRIMARY KEY(instance_id, instrument_id, role)
);

CREATE TABLE timeframes (
  timeframe_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  canonical_token TEXT NOT NULL,
  duration_seconds INTEGER CHECK(duration_seconds > 0 OR duration_seconds IS NULL),
  normalization_status TEXT NOT NULL
    CHECK(normalization_status IN ('explicit','curated_normalization','unresolved')),
  name_claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  UNIQUE(canonical_token)
);

CREATE TABLE setup_timeframes (
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  timeframe_id TEXT NOT NULL REFERENCES timeframes(timeframe_id),
  role TEXT NOT NULL
    CHECK(role IN ('narrative','poi','liquidity','confirmation','entry','management','unknown')),
  raw_text TEXT NOT NULL,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  PRIMARY KEY(instance_id, timeframe_id, role)
);

CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  canonical_label TEXT NOT NULL,
  definition_as_stated TEXT,
  timezone_as_stated TEXT,
  name_claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  definition_claim_id TEXT REFERENCES claims(claim_id),
  UNIQUE(canonical_label)
);

CREATE TABLE setup_sessions (
  setup_session_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  session_id TEXT REFERENCES sessions(session_id),
  role TEXT NOT NULL,
  stated_time_text TEXT,
  timezone_status TEXT NOT NULL
    CHECK(timezone_status IN ('explicit','normalized_from_explicit','unknown')),
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE setup_time_markers (
  marker_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  marker_type TEXT NOT NULL,
  stated_time_text TEXT NOT NULL,
  timezone_as_stated TEXT,
  role TEXT NOT NULL,
  sequence_order INTEGER,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE setup_narratives (
  narrative_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  horizon TEXT NOT NULL,
  bias TEXT,
  narrative_text TEXT NOT NULL,
  validity_start_utc TEXT,
  validity_end_utc TEXT,
  sequence_order INTEGER,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE setup_scenarios (
  scenario_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  scenario_type TEXT NOT NULL CHECK(scenario_type IN ('primary','alternate','no_trade')),
  direction TEXT,
  condition_text TEXT NOT NULL,
  target_text TEXT,
  invalidation_text TEXT,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE setup_context_events (
  context_event_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  event_name_as_stated TEXT NOT NULL,
  event_time_as_stated TEXT,
  event_role TEXT NOT NULL,
  observed_effect_text TEXT,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE liquidity_events (
  liquidity_event_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  event_role TEXT NOT NULL,
  side TEXT,
  pool_type TEXT,
  state TEXT,
  timeframe_id TEXT REFERENCES timeframes(timeframe_id),
  price_text TEXT,
  price_numeric REAL,
  occurred_at_text TEXT,
  sequence_order INTEGER,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE market_levels (
  level_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  level_type TEXT NOT NULL,
  role TEXT NOT NULL,
  price_text TEXT,
  price_numeric REAL,
  timeframe_id TEXT REFERENCES timeframes(timeframe_id),
  session_id TEXT REFERENCES sessions(session_id),
  state TEXT,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE price_arrays (
  array_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  term_id TEXT NOT NULL REFERENCES concept_terms(term_id),
  role TEXT NOT NULL,
  direction TEXT,
  timeframe_id TEXT REFERENCES timeframes(timeframe_id),
  lower_price_text TEXT,
  upper_price_text TEXT,
  ce_price_text TEXT,
  freshness_state TEXT,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE price_array_interactions (
  interaction_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  array_id TEXT NOT NULL REFERENCES price_arrays(array_id),
  interaction_type TEXT NOT NULL,
  observed_state TEXT,
  occurred_at_text TEXT,
  sequence_order INTEGER,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE setup_confirmations (
  confirmation_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  term_id TEXT REFERENCES concept_terms(term_id),
  confirmation_text TEXT NOT NULL,
  requirement_state TEXT NOT NULL
    CHECK(requirement_state IN ('required','supportive','optional','unknown')),
  observed_state TEXT NOT NULL
    CHECK(observed_state IN ('present','absent','violated','unknown')),
  timeframe_id TEXT REFERENCES timeframes(timeframe_id),
  occurred_at_text TEXT,
  sequence_order INTEGER,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE setup_invalidations (
  invalidation_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  scope TEXT NOT NULL
    CHECK(scope IN ('bias','model','entry','stop','no_trade','post_entry_failure')),
  invalidation_class TEXT NOT NULL,
  condition_text TEXT NOT NULL,
  price_text TEXT,
  time_cutoff_text TEXT,
  observed_state TEXT NOT NULL
    CHECK(observed_state IN ('triggered','not_triggered','unknown')),
  consequence TEXT,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE setup_features (
  instance_id TEXT NOT NULL REFERENCES setup_instances(instance_id),
  term_id TEXT NOT NULL REFERENCES concept_terms(term_id),
  feature_role TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('present','absent','violated','unknown')),
  timeframe_id TEXT REFERENCES timeframes(timeframe_id),
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  PRIMARY KEY(instance_id, term_id, feature_role)
);

CREATE TABLE trade_episodes (
  trade_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  instance_id TEXT REFERENCES setup_instances(instance_id),
  trader_id TEXT REFERENCES authors(author_id),
  trade_date_text TEXT,
  execution_mode TEXT
    CHECK(execution_mode IN ('actual','paper','backtest','hypothetical','unknown') OR execution_mode IS NULL),
  episode_kind TEXT NOT NULL,
  aggregate_group_id TEXT,
  strict_comparison_eligible INTEGER NOT NULL DEFAULT 0 CHECK(strict_comparison_eligible IN (0,1)),
  linkage_status TEXT NOT NULL,
  episode_claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  legacy_trade_id TEXT,
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE trade_orders (
  order_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  trade_id TEXT NOT NULL REFERENCES trade_episodes(trade_id),
  order_role TEXT NOT NULL
    CHECK(order_role IN ('entry','stop','target','partial_exit','breakeven_stop')),
  order_type TEXT,
  price_text TEXT,
  price_numeric REAL,
  quantity_text TEXT,
  placed_at_text TEXT,
  filled_at_text TEXT,
  status TEXT,
  sequence_order INTEGER,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE trade_management_events (
  management_event_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  trade_id TEXT NOT NULL REFERENCES trade_episodes(trade_id),
  event_type TEXT NOT NULL,
  event_text TEXT NOT NULL,
  occurred_at_text TEXT,
  sequence_order INTEGER,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE trade_outcome_claims (
  outcome_claim_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  trade_id TEXT NOT NULL REFERENCES trade_episodes(trade_id),
  outcome TEXT NOT NULL
    CHECK(outcome IN ('win','loss','breakeven','mixed_partial','cancelled_no_trade','open','unknown')),
  basis TEXT NOT NULL,
  terminal_at_text TEXT,
  is_aggregate INTEGER NOT NULL DEFAULT 0 CHECK(is_aggregate IN (0,1)),
  reported_trade_count INTEGER CHECK(reported_trade_count > 0 OR reported_trade_count IS NULL),
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE trade_outcome_measures (
  measure_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  outcome_claim_id TEXT NOT NULL REFERENCES trade_outcome_claims(outcome_claim_id),
  measure_type TEXT NOT NULL,
  numeric_value REAL,
  text_value TEXT NOT NULL,
  unit TEXT,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE trade_outcome_resolution (
  trade_id TEXT PRIMARY KEY REFERENCES trade_episodes(trade_id),
  resolved_outcome_claim_id TEXT NOT NULL REFERENCES trade_outcome_claims(outcome_claim_id),
  resolved_outcome TEXT NOT NULL,
  resolution_status TEXT NOT NULL
    CHECK(resolution_status IN ('resolved','partial','conflicting','unresolved')),
  strict_comparison_eligible INTEGER NOT NULL DEFAULT 0 CHECK(strict_comparison_eligible IN (0,1)),
  resolution_reason TEXT NOT NULL,
  confidence_assessment_id TEXT REFERENCES confidence_assessments(assessment_id)
);

CREATE TABLE questions (
  question_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  primary_message_id TEXT NOT NULL REFERENCES messages(message_id),
  normalized_question TEXT NOT NULL,
  topic TEXT NOT NULL,
  subtopic TEXT,
  resolution_status TEXT NOT NULL
    CHECK(resolution_status IN ('answered','partial','conflicting','unanswered','ambiguous')),
  question_claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE answers (
  answer_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  answer_summary TEXT NOT NULL,
  resolution_status TEXT NOT NULL
    CHECK(resolution_status IN ('answered','partial','conflicting','community_only','unresolved')),
  answer_claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE question_messages (
  question_id TEXT NOT NULL REFERENCES questions(question_id),
  message_id TEXT NOT NULL REFERENCES messages(message_id),
  sequence_order INTEGER NOT NULL,
  PRIMARY KEY(question_id, message_id)
);

CREATE TABLE answer_messages (
  answer_id TEXT NOT NULL REFERENCES answers(answer_id),
  message_id TEXT NOT NULL REFERENCES messages(message_id),
  sequence_order INTEGER NOT NULL,
  message_role TEXT NOT NULL,
  PRIMARY KEY(answer_id, message_id)
);

CREATE TABLE question_answer_links (
  question_id TEXT NOT NULL REFERENCES questions(question_id),
  answer_id TEXT NOT NULL REFERENCES answers(answer_id),
  link_type TEXT NOT NULL,
  direct_reply INTEGER NOT NULL DEFAULT 0 CHECK(direct_reply IN (0,1)),
  linkage_confidence REAL NOT NULL CHECK(linkage_confidence BETWEEN 0 AND 1),
  claim_id TEXT NOT NULL REFERENCES claims(claim_id),
  PRIMARY KEY(question_id, answer_id)
);

CREATE TABLE analysis_cohorts (
  cohort_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  name TEXT NOT NULL,
  eligibility_definition_json TEXT NOT NULL,
  exclusion_definition_json TEXT NOT NULL,
  window_start_utc TEXT NOT NULL,
  window_end_utc TEXT NOT NULL,
  analysis_run_id INTEGER NOT NULL REFERENCES analysis_runs(analysis_run_id),
  cohort_claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE setup_performance_rollups (
  rollup_id TEXT PRIMARY KEY REFERENCES analysis_entities(entity_id),
  cohort_id TEXT NOT NULL REFERENCES analysis_cohorts(cohort_id),
  model_id TEXT REFERENCES setup_models(model_id),
  instrument_id TEXT REFERENCES instruments(instrument_id),
  timeframe_id TEXT REFERENCES timeframes(timeframe_id),
  session_id TEXT REFERENCES sessions(session_id),
  eligible_count INTEGER NOT NULL CHECK(eligible_count >= 0),
  wins INTEGER NOT NULL CHECK(wins >= 0),
  losses INTEGER NOT NULL CHECK(losses >= 0),
  breakevens INTEGER NOT NULL CHECK(breakevens >= 0),
  unknowns INTEGER NOT NULL CHECK(unknowns >= 0),
  excluded_count INTEGER NOT NULL CHECK(excluded_count >= 0),
  distinct_authors INTEGER NOT NULL CHECK(distinct_authors >= 0),
  top_author_share REAL CHECK(top_author_share BETWEEN 0 AND 1 OR top_author_share IS NULL),
  observed_win_rate REAL CHECK(observed_win_rate BETWEEN 0 AND 1 OR observed_win_rate IS NULL),
  models_overlap INTEGER NOT NULL DEFAULT 1 CHECK(models_overlap IN (0,1)),
  not_causal INTEGER NOT NULL DEFAULT 1 CHECK(not_causal = 1),
  limitations TEXT NOT NULL,
  claim_id TEXT NOT NULL REFERENCES claims(claim_id)
);

CREATE TABLE legacy_id_map (
  legacy_table TEXT NOT NULL,
  legacy_id TEXT NOT NULL,
  new_entity_id TEXT NOT NULL REFERENCES analysis_entities(entity_id),
  migration_status TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(legacy_table, legacy_id)
);

CREATE TABLE analysis_documents (
  document_name TEXT PRIMARY KEY,
  analysis_run_id INTEGER REFERENCES analysis_runs(analysis_run_id),
  created_by TEXT NOT NULL,
  content_json TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE data_dictionary (
  table_name TEXT NOT NULL,
  column_name TEXT NOT NULL,
  description TEXT NOT NULL,
  null_semantics TEXT NOT NULL DEFAULT 'Unknown or not applicable; never infer from absence.',
  source_or_derived TEXT NOT NULL,
  PRIMARY KEY(table_name, column_name)
);

CREATE TRIGGER attachment_extractions_reject_non_owned_ai
BEFORE INSERT ON attachment_extractions
WHEN COALESCE((
  SELECT eligible_for_attachment_evidence
  FROM attachments WHERE attachment_id=new.attachment_id
),0)=0
BEGIN
  SELECT RAISE(ABORT,'non-owned or unresolved attachments cannot have extraction evidence');
END;

CREATE TRIGGER attachment_extractions_reject_non_owned_au
BEFORE UPDATE OF attachment_id ON attachment_extractions
WHEN COALESCE((
  SELECT eligible_for_attachment_evidence
  FROM attachments WHERE attachment_id=new.attachment_id
),0)=0
BEGIN
  SELECT RAISE(ABORT,'non-owned or unresolved attachments cannot have extraction evidence');
END;

CREATE TRIGGER evidence_items_reject_non_owned_attachment_ai
BEFORE INSERT ON evidence_items
WHEN new.attachment_id IS NOT NULL
 AND COALESCE((
   SELECT eligible_for_attachment_evidence
   FROM attachments WHERE attachment_id=new.attachment_id
 ),0)=0
BEGIN
  SELECT RAISE(ABORT,'non-owned or unresolved attachments cannot be evidence items');
END;

CREATE TRIGGER evidence_items_reject_non_owned_attachment_au
BEFORE UPDATE OF attachment_id ON evidence_items
WHEN new.attachment_id IS NOT NULL
 AND COALESCE((
   SELECT eligible_for_attachment_evidence
   FROM attachments WHERE attachment_id=new.attachment_id
 ),0)=0
BEGIN
  SELECT RAISE(ABORT,'non-owned or unresolved attachments cannot be evidence items');
END;

CREATE TRIGGER evidence_items_trust_ai AFTER INSERT ON evidence_items BEGIN
  UPDATE evidence_items
  SET evidence_trust_state=COALESCE((
        SELECT m.evidence_trust_state
        FROM messages m
        WHERE m.message_id=COALESCE(
          new.message_id,
          (SELECT a.message_id FROM attachments a WHERE a.attachment_id=new.attachment_id)
        )
      ),'untrusted_noncanonical_only'),
      eligible_for_accepted_claims=COALESCE((
        SELECT m.eligible_for_accepted_evidence
        FROM messages m
        WHERE m.message_id=COALESCE(
          new.message_id,
          (SELECT a.message_id FROM attachments a WHERE a.attachment_id=new.attachment_id)
        )
      ),0) * CASE WHEN new.attachment_id IS NULL THEN 1 ELSE COALESCE((
        SELECT a.eligible_for_attachment_evidence
        FROM attachments a WHERE a.attachment_id=new.attachment_id
      ),0) END
  WHERE evidence_id=new.evidence_id;
END;

CREATE TRIGGER messages_reject_migration_trust_upgrade_without_recapture
BEFORE UPDATE OF eligible_for_accepted_evidence,evidence_trust_state ON messages
WHEN new.eligible_for_accepted_evidence=1
 AND EXISTS(
   SELECT 1 FROM message_source_occurrences o
   WHERE o.message_id=new.message_id
     AND (o.migration_source=1 OR o.quarantined=1)
 )
 AND NOT EXISTS(
   SELECT 1 FROM message_source_occurrences o
   WHERE o.message_id=new.message_id AND o.trusted_canonical=1
 )
BEGIN
  SELECT RAISE(ABORT,'trusted canonical recapture required to upgrade migrated or quarantined message');
END;

CREATE TRIGGER evidence_items_trust_au
AFTER UPDATE OF message_id,attachment_id ON evidence_items BEGIN
  UPDATE evidence_items
  SET evidence_trust_state=COALESCE((
        SELECT m.evidence_trust_state
        FROM messages m
        WHERE m.message_id=COALESCE(
          new.message_id,
          (SELECT a.message_id FROM attachments a WHERE a.attachment_id=new.attachment_id)
        )
      ),'untrusted_noncanonical_only'),
      eligible_for_accepted_claims=COALESCE((
        SELECT m.eligible_for_accepted_evidence
        FROM messages m
        WHERE m.message_id=COALESCE(
          new.message_id,
          (SELECT a.message_id FROM attachments a WHERE a.attachment_id=new.attachment_id)
        )
      ),0) * CASE WHEN new.attachment_id IS NULL THEN 1 ELSE COALESCE((
        SELECT a.eligible_for_attachment_evidence
        FROM attachments a WHERE a.attachment_id=new.attachment_id
      ),0) END
  WHERE evidence_id=new.evidence_id;
END;

CREATE TRIGGER claim_evidence_rejects_untrusted_accepted_claim
BEFORE INSERT ON claim_evidence
WHEN (SELECT resolution_status FROM claims WHERE claim_id=new.claim_id)='accepted'
 AND COALESCE((SELECT eligible_for_accepted_claims FROM evidence_items WHERE evidence_id=new.evidence_id),0)=0
BEGIN
  SELECT RAISE(ABORT,'accepted claims cannot use quarantined or untrusted evidence');
END;

CREATE TRIGGER claims_reject_untrusted_evidence_on_accept
BEFORE UPDATE OF resolution_status ON claims
WHEN new.resolution_status='accepted'
 AND EXISTS(
   SELECT 1 FROM claim_evidence ce
   JOIN evidence_items ev ON ev.evidence_id=ce.evidence_id
   WHERE ce.claim_id=new.claim_id AND ev.eligible_for_accepted_claims=0
 )
BEGIN
  SELECT RAISE(ABORT,'claim cannot become accepted while linked to quarantined or untrusted evidence');
END;

CREATE TRIGGER setup_instances_reject_untrusted_positive_identity
BEFORE INSERT ON setup_instances
WHEN new.identity_resolution_status IN ('explicit','linked','derived')
 AND COALESCE((SELECT eligible_for_accepted_evidence FROM messages WHERE message_id=new.primary_message_id),0)=0
BEGIN
  SELECT RAISE(ABORT,'trusted canonical recapture required for resolved setup identity');
END;

CREATE TRIGGER setup_instances_reject_untrusted_positive_identity_update
BEFORE UPDATE OF identity_resolution_status,primary_message_id ON setup_instances
WHEN new.identity_resolution_status IN ('explicit','linked','derived')
 AND COALESCE((SELECT eligible_for_accepted_evidence FROM messages WHERE message_id=new.primary_message_id),0)=0
BEGIN
  SELECT RAISE(ABORT,'trusted canonical recapture required for resolved setup identity');
END;

CREATE TRIGGER setup_model_matches_reject_untrusted_positive_match
BEFORE INSERT ON setup_model_matches
WHEN new.match_status IN ('explicit','derived')
 AND EXISTS(
   SELECT 1 FROM setup_instances si
   JOIN messages m ON m.message_id=si.primary_message_id
   WHERE si.instance_id=new.instance_id AND m.eligible_for_accepted_evidence=0
 )
BEGIN
  SELECT RAISE(ABORT,'trusted canonical recapture required for positive setup-model match');
END;

CREATE TRIGGER setup_model_matches_reject_untrusted_positive_match_update
BEFORE UPDATE OF match_status,instance_id ON setup_model_matches
WHEN new.match_status IN ('explicit','derived')
 AND EXISTS(
   SELECT 1 FROM setup_instances si
   JOIN messages m ON m.message_id=si.primary_message_id
   WHERE si.instance_id=new.instance_id AND m.eligible_for_accepted_evidence=0
 )
BEGIN
  SELECT RAISE(ABORT,'trusted canonical recapture required for positive setup-model match');
END;

CREATE TRIGGER trade_episodes_reject_untrusted_strict_eligibility
BEFORE INSERT ON trade_episodes
WHEN new.strict_comparison_eligible=1
 AND EXISTS(
   SELECT 1 FROM setup_instances si
   JOIN messages m ON m.message_id=si.primary_message_id
   WHERE si.instance_id=new.instance_id AND m.eligible_for_accepted_evidence=0
 )
BEGIN
  SELECT RAISE(ABORT,'quarantined setup cannot enter strict trade comparison');
END;

CREATE TRIGGER trade_episodes_reject_untrusted_strict_eligibility_update
BEFORE UPDATE OF strict_comparison_eligible,instance_id ON trade_episodes
WHEN new.strict_comparison_eligible=1
 AND EXISTS(
   SELECT 1 FROM setup_instances si
   JOIN messages m ON m.message_id=si.primary_message_id
   WHERE si.instance_id=new.instance_id AND m.eligible_for_accepted_evidence=0
 )
BEGIN
  SELECT RAISE(ABORT,'quarantined setup cannot enter strict trade comparison');
END;

CREATE TRIGGER trade_outcome_resolution_rejects_untrusted_strict_eligibility
BEFORE INSERT ON trade_outcome_resolution
WHEN new.strict_comparison_eligible=1
 AND EXISTS(
   SELECT 1 FROM trade_episodes te
   JOIN setup_instances si ON si.instance_id=te.instance_id
   JOIN messages m ON m.message_id=si.primary_message_id
   WHERE te.trade_id=new.trade_id AND m.eligible_for_accepted_evidence=0
 )
BEGIN
  SELECT RAISE(ABORT,'quarantined setup cannot produce strict resolved trade outcome');
END;

CREATE TRIGGER trade_outcome_resolution_rejects_untrusted_strict_update
BEFORE UPDATE OF strict_comparison_eligible,resolved_outcome,resolution_status ON trade_outcome_resolution
WHEN new.strict_comparison_eligible=1
 AND EXISTS(
   SELECT 1 FROM trade_episodes te
   JOIN setup_instances si ON si.instance_id=te.instance_id
   JOIN messages m ON m.message_id=si.primary_message_id
   WHERE te.trade_id=new.trade_id AND m.eligible_for_accepted_evidence=0
 )
BEGIN
  SELECT RAISE(ABORT,'quarantined setup cannot produce strict resolved trade outcome');
END;

CREATE VIRTUAL TABLE messages_fts USING fts5(
  message_id UNINDEXED,
  channel_name,
  thread_title,
  author_display_name,
  content_text,
  reply_to_content,
  visible_text,
  tokenize='unicode61'
);

CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(
    message_id,channel_name,thread_title,author_display_name,
    content_text,reply_to_content,visible_text
  ) VALUES(
    new.message_id,new.channel_name,new.thread_title,new.author_display_name,
    new.content_text,new.reply_to_content,new.visible_text
  );
END;

CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
  DELETE FROM messages_fts WHERE message_id=old.message_id;
END;

CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
  DELETE FROM messages_fts WHERE message_id=old.message_id;
  INSERT INTO messages_fts(
    message_id,channel_name,thread_title,author_display_name,
    content_text,reply_to_content,visible_text
  ) VALUES(
    new.message_id,new.channel_name,new.thread_title,new.author_display_name,
    new.content_text,new.reply_to_content,new.visible_text
  );
END;

CREATE VIRTUAL TABLE attachment_extractions_fts USING fts5(
  extraction_id UNINDEXED,
  attachment_id UNINDEXED,
  extracted_text,
  tokenize='unicode61'
);

CREATE TRIGGER attachment_extractions_ai AFTER INSERT ON attachment_extractions BEGIN
  INSERT INTO attachment_extractions_fts(extraction_id,attachment_id,extracted_text)
  VALUES(new.extraction_id,new.attachment_id,new.extracted_text);
END;

CREATE TRIGGER attachment_extractions_ad AFTER DELETE ON attachment_extractions BEGIN
  DELETE FROM attachment_extractions_fts WHERE extraction_id=old.extraction_id;
END;

CREATE TRIGGER attachment_extractions_au AFTER UPDATE ON attachment_extractions BEGIN
  DELETE FROM attachment_extractions_fts WHERE extraction_id=old.extraction_id;
  INSERT INTO attachment_extractions_fts(extraction_id,attachment_id,extracted_text)
  VALUES(new.extraction_id,new.attachment_id,new.extracted_text);
END;

CREATE VIRTUAL TABLE claims_fts USING fts5(
  claim_id UNINDEXED,
  facet,
  claim_text,
  normalized_value_json,
  limitations,
  tokenize='unicode61'
);

CREATE TRIGGER claims_ai AFTER INSERT ON claims BEGIN
  INSERT INTO claims_fts(claim_id,facet,claim_text,normalized_value_json,limitations)
  VALUES(new.claim_id,new.facet,new.claim_text,new.normalized_value_json,new.limitations);
END;

CREATE TRIGGER claims_ad AFTER DELETE ON claims BEGIN
  DELETE FROM claims_fts WHERE claim_id=old.claim_id;
END;

CREATE TRIGGER claims_au AFTER UPDATE ON claims BEGIN
  DELETE FROM claims_fts WHERE claim_id=old.claim_id;
  INSERT INTO claims_fts(claim_id,facet,claim_text,normalized_value_json,limitations)
  VALUES(new.claim_id,new.facet,new.claim_text,new.normalized_value_json,new.limitations);
END;

CREATE VIEW v_whole_server_coverage AS
SELECT c.channel_id,c.parent_channel_id,c.name,c.kind,c.exact_id_known,
       c.is_archived,c.is_accessible,c.inventory_basis,
       COUNT(u.unit_id) AS collection_unit_count,
       SUM(CASE WHEN u.status='complete' THEN 1 ELSE 0 END) AS complete_units,
       SUM(CASE WHEN u.status<>'complete' THEN 1 ELSE 0 END) AS incomplete_units,
       COALESCE(SUM(u.unique_messages_seen),0) AS reported_unique_messages,
       MIN(u.window_start_utc) AS earliest_covered_boundary,
       MAX(u.window_end_utc) AS latest_covered_boundary
FROM channel_inventory c
LEFT JOIN collection_units u ON u.channel_id=c.channel_id
GROUP BY c.channel_id;

CREATE VIEW v_collection_gaps AS
SELECT u.unit_id,u.channel_id,c.name AS channel_name,u.collection_name,
       u.window_start_utc,u.window_end_utc,u.status,u.gap_notes,
       s.segment_id,s.segment_start_utc,s.segment_end_utc,
       s.status AS segment_status,s.error_text,s.notes AS segment_notes
FROM collection_units u
LEFT JOIN channel_inventory c ON c.channel_id=u.channel_id
LEFT JOIN coverage_segments s ON s.unit_id=u.unit_id
WHERE u.status<>'complete' OR (s.segment_id IS NOT NULL AND s.status<>'complete');

CREATE VIEW v_message_trust_lookup AS
SELECT m.message_id,m.created_at_utc,m.channel_name,m.thread_title,
       m.author_display_name,m.content_text,m.visible_text,m.reply_to_content,
       m.evidence_trust_state,m.eligible_for_accepted_evidence,
       m.has_quarantined_occurrences,m.trusted_canonical_occurrence_count,
       m.quarantined_occurrence_count,m.permalink,m.raw_json AS canonical_raw_json,
       o.occurrence_id,o.source_kind,o.migration_source,o.quarantined AS occurrence_quarantined,
       o.trusted_canonical,o.trust_state AS occurrence_trust_state,
       o.quarantine_reasons_json,o.collection_name,o.query_text,
       o.result_index,o.page_number,o.raw_json AS occurrence_raw_json
FROM messages m
LEFT JOIN message_source_occurrences o ON o.message_id=m.message_id;

CREATE VIEW v_analysis_eligible_messages AS
SELECT m.*
FROM messages m
WHERE m.eligible_for_accepted_evidence=1
  AND m.evidence_trust_state IN ('trusted_canonical_recapture','trusted_source');

CREATE VIEW v_cardinal_setup_evidence AS
SELECT si.instance_id,
       e.entity_id AS subject_entity_id,e.entity_type,c.claim_id,c.facet,
       c.claim_text,c.normalized_value_json,c.claim_kind,c.epistemic_status,
       c.resolution_status,c.speaker_author_id,c.authority_assignment_id,
       ce.evidence_role,ev.evidence_id,ev.source_type,ev.exact_excerpt,
       ev.message_id,ev.attachment_id,ev.locator_json,
       m.created_at_utc,m.author_display_name,m.channel_name,m.thread_title,m.permalink,
       ev.extraction_confidence,ev.evidence_trust_state,
       ev.eligible_for_accepted_claims,m.evidence_trust_state AS message_trust_state,
       m.eligible_for_accepted_evidence
FROM setup_instances si
JOIN analysis_entities e
  ON e.entity_id=si.instance_id OR e.root_entity_id=si.instance_id
JOIN claims c ON c.subject_entity_id=e.entity_id
LEFT JOIN claim_evidence ce ON ce.claim_id=c.claim_id
LEFT JOIN evidence_items ev ON ev.evidence_id=ce.evidence_id
LEFT JOIN messages m ON m.message_id=ev.message_id;

CREATE VIEW v_cardinal_missing_fields AS
SELECT si.instance_id,
       CASE WHEN EXISTS(SELECT 1 FROM setup_instruments x WHERE x.instance_id=si.instance_id AND x.role='executed') THEN 0 ELSE 1 END AS missing_executed_instrument,
       CASE WHEN si.direction IS NULL THEN 1 ELSE 0 END AS missing_direction,
       CASE WHEN EXISTS(SELECT 1 FROM setup_sessions x WHERE x.instance_id=si.instance_id) THEN 0 ELSE 1 END AS missing_session,
       CASE WHEN EXISTS(SELECT 1 FROM setup_sessions x WHERE x.instance_id=si.instance_id AND x.timezone_status<>'unknown') THEN 0 ELSE 1 END AS missing_timezone,
       CASE WHEN EXISTS(SELECT 1 FROM setup_narratives x WHERE x.instance_id=si.instance_id) THEN 0 ELSE 1 END AS missing_htf_narrative,
       CASE WHEN EXISTS(SELECT 1 FROM liquidity_events x WHERE x.instance_id=si.instance_id AND x.event_role IN ('draw','target')) THEN 0 ELSE 1 END AS missing_draw,
       CASE WHEN EXISTS(SELECT 1 FROM price_arrays x WHERE x.instance_id=si.instance_id) THEN 0 ELSE 1 END AS missing_poi,
       CASE WHEN EXISTS(SELECT 1 FROM setup_confirmations x WHERE x.instance_id=si.instance_id) THEN 0 ELSE 1 END AS missing_confirmation,
       CASE WHEN EXISTS(SELECT 1 FROM setup_invalidations x WHERE x.instance_id=si.instance_id) THEN 0 ELSE 1 END AS missing_invalidation,
       CASE WHEN EXISTS(SELECT 1 FROM trade_episodes t JOIN trade_orders o ON o.trade_id=t.trade_id WHERE t.instance_id=si.instance_id AND o.order_role='entry') THEN 0 ELSE 1 END AS missing_entry,
       CASE WHEN EXISTS(SELECT 1 FROM trade_episodes t JOIN trade_orders o ON o.trade_id=t.trade_id WHERE t.instance_id=si.instance_id AND o.order_role IN ('stop','breakeven_stop')) THEN 0 ELSE 1 END AS missing_stop,
       CASE WHEN EXISTS(SELECT 1 FROM trade_episodes t JOIN trade_orders o ON o.trade_id=t.trade_id WHERE t.instance_id=si.instance_id AND o.order_role='target') THEN 0 ELSE 1 END AS missing_target,
       CASE WHEN EXISTS(SELECT 1 FROM trade_episodes t JOIN trade_outcome_resolution r ON r.trade_id=t.trade_id WHERE t.instance_id=si.instance_id AND r.resolution_status='resolved') THEN 0 ELSE 1 END AS missing_resolved_outcome
FROM setup_instances si;

CREATE VIEW v_cardinal_setup_cards AS
SELECT si.instance_id,si.occurrence_type,si.primary_message_id,
       si.primary_author_id,si.occurrence_date_text,si.direction,
       si.lifecycle_state,si.identity_resolution_status,si.notes,
       m.created_at_utc AS source_post_time_utc,m.channel_name,m.thread_title,m.permalink,
       m.evidence_trust_state AS source_trust_state,
       m.eligible_for_accepted_evidence AS source_eligible_for_accepted_evidence,
       COALESCE((SELECT json_group_array(json_object(
         'model_id',x.model_id,'name',sm.canonical_name,'status',x.match_status,
         'matched_rules',x.matched_rule_count,'missing_rules',x.missing_rule_count,
         'violated_rules',x.violated_rule_count))
         FROM setup_model_matches x JOIN setup_models sm ON sm.model_id=x.model_id
         WHERE x.instance_id=si.instance_id),'[]') AS model_matches_json,
       COALESCE((SELECT json_group_array(json_object(
         'symbol',i.canonical_symbol,'role',x.role,'raw_text',x.raw_text))
         FROM setup_instruments x JOIN instruments i ON i.instrument_id=x.instrument_id
         WHERE x.instance_id=si.instance_id),'[]') AS instruments_json,
       COALESCE((SELECT json_group_array(json_object(
         'timeframe',t.canonical_token,'role',x.role,'raw_text',x.raw_text))
         FROM setup_timeframes x JOIN timeframes t ON t.timeframe_id=x.timeframe_id
         WHERE x.instance_id=si.instance_id),'[]') AS timeframes_json,
       COALESCE((SELECT json_group_array(json_object(
         'session',s.canonical_label,'role',x.role,'stated_time',x.stated_time_text,
         'timezone_status',x.timezone_status))
         FROM setup_sessions x LEFT JOIN sessions s ON s.session_id=x.session_id
         WHERE x.instance_id=si.instance_id),'[]') AS sessions_json,
       COALESCE((SELECT json_group_array(json_object(
         'horizon',x.horizon,'bias',x.bias,'narrative',x.narrative_text,
         'sequence',x.sequence_order))
         FROM setup_narratives x WHERE x.instance_id=si.instance_id),'[]') AS narratives_json,
       COALESCE((SELECT json_group_array(json_object(
         'type',x.scenario_type,'direction',x.direction,'condition',x.condition_text,
         'target',x.target_text,'invalidation',x.invalidation_text))
         FROM setup_scenarios x WHERE x.instance_id=si.instance_id),'[]') AS scenarios_json,
       COALESCE((SELECT json_group_array(json_object(
         'marker_type',x.marker_type,'stated_time',x.stated_time_text,
         'timezone',x.timezone_as_stated,'role',x.role,'sequence',x.sequence_order))
         FROM setup_time_markers x WHERE x.instance_id=si.instance_id),'[]') AS time_markers_json,
       COALESCE((SELECT json_group_array(json_object(
         'event_name',x.event_name_as_stated,'event_time',x.event_time_as_stated,
         'role',x.event_role,'observed_effect',x.observed_effect_text))
         FROM setup_context_events x WHERE x.instance_id=si.instance_id),'[]') AS context_events_json,
       COALESCE((SELECT json_group_array(json_object(
         'role',x.event_role,'side',x.side,'pool_type',x.pool_type,'state',x.state,
         'price_text',x.price_text,'sequence',x.sequence_order))
         FROM liquidity_events x WHERE x.instance_id=si.instance_id),'[]') AS liquidity_json,
       COALESCE((SELECT json_group_array(json_object(
         'type',x.level_type,'role',x.role,'price_text',x.price_text,
         'price_numeric',x.price_numeric,'state',x.state))
         FROM market_levels x WHERE x.instance_id=si.instance_id),'[]') AS market_levels_json,
       COALESCE((SELECT json_group_array(json_object(
         'term',ct.canonical_name,'role',x.role,'direction',x.direction,
         'lower',x.lower_price_text,'upper',x.upper_price_text,
         'ce',x.ce_price_text,'freshness',x.freshness_state))
         FROM price_arrays x JOIN concept_terms ct ON ct.term_id=x.term_id
         WHERE x.instance_id=si.instance_id),'[]') AS price_arrays_json,
       COALESCE((SELECT json_group_array(json_object(
         'text',x.confirmation_text,'requirement',x.requirement_state,
         'observed',x.observed_state,'sequence',x.sequence_order))
         FROM setup_confirmations x WHERE x.instance_id=si.instance_id),'[]') AS confirmations_json,
       COALESCE((SELECT json_group_array(json_object(
         'scope',x.scope,'class',x.invalidation_class,'condition',x.condition_text,
         'price',x.price_text,'time_cutoff',x.time_cutoff_text,
         'observed',x.observed_state,'consequence',x.consequence))
         FROM setup_invalidations x WHERE x.instance_id=si.instance_id),'[]') AS invalidations_json,
       COALESCE((SELECT json_group_array(json_object(
         'trade_id',t.trade_id,'role',o.order_role,'type',o.order_type,
         'price_text',o.price_text,'status',o.status,'sequence',o.sequence_order))
         FROM trade_episodes t JOIN trade_orders o ON o.trade_id=t.trade_id
         WHERE t.instance_id=si.instance_id),'[]') AS orders_json,
       COALESCE((SELECT json_group_array(json_object(
         'trade_id',t.trade_id,'event_type',x.event_type,'event_text',x.event_text,
         'occurred_at',x.occurred_at_text,'sequence',x.sequence_order))
         FROM trade_episodes t JOIN trade_management_events x ON x.trade_id=t.trade_id
         WHERE t.instance_id=si.instance_id),'[]') AS management_json,
       COALESCE((SELECT json_group_array(json_object(
         'trade_id',t.trade_id,'outcome',r.resolved_outcome,
         'status',r.resolution_status,'strict_eligible',r.strict_comparison_eligible,
         'reason',r.resolution_reason))
         FROM trade_episodes t JOIN trade_outcome_resolution r ON r.trade_id=t.trade_id
         WHERE t.instance_id=si.instance_id),'[]') AS outcomes_json,
       COALESCE((SELECT json_group_array(json_object(
         'dimension',ca.dimension,'score',ca.score,'band',ca.band,
         'basis',ca.basis_text,'sample_size',ca.sample_size,'caveat',ca.caveat))
         FROM confidence_assessments ca
         LEFT JOIN claims cc ON cc.claim_id=ca.claim_id
         LEFT JOIN analysis_entities ce
           ON ce.entity_id=COALESCE(ca.entity_id,cc.subject_entity_id)
         WHERE ce.entity_id=si.instance_id OR ce.root_entity_id=si.instance_id),'[]') AS confidence_json,
       COALESCE((SELECT json_group_array(json_object(
         'authority_class',aa.authority_class,'basis',aa.basis,
         'confidence',aa.confidence,'claim_id',cc.claim_id))
         FROM claims cc
         JOIN analysis_entities ce ON ce.entity_id=cc.subject_entity_id
         JOIN authority_assignments aa ON aa.assignment_id=cc.authority_assignment_id
         WHERE ce.entity_id=si.instance_id OR ce.root_entity_id=si.instance_id),'[]') AS authority_json,
       COALESCE((SELECT json_group_array(json_object(
         'contradiction_id',cs.contradiction_id,'topic',cs.topic,
         'status',cs.resolution_status,'claim_id',cm.claim_id,'stance',cm.stance))
         FROM contradiction_sets cs
         JOIN contradiction_members cm ON cm.contradiction_id=cs.contradiction_id
         JOIN claims cc ON cc.claim_id=cm.claim_id
         JOIN analysis_entities ce ON ce.entity_id=cc.subject_entity_id
         WHERE ce.entity_id=si.instance_id OR ce.root_entity_id=si.instance_id),'[]') AS contradictions_json,
       COALESCE((SELECT json_group_array(json_object(
         'message_id',e.message_id,'attachment_id',e.attachment_id,
         'permalink',e.permalink,'facet',e.facet,'evidence_role',e.evidence_role,
         'excerpt',e.exact_excerpt,'trust_state',e.evidence_trust_state,
         'eligible_for_accepted_claims',e.eligible_for_accepted_claims))
         FROM v_cardinal_setup_evidence e
         WHERE e.instance_id=si.instance_id),'[]') AS evidence_json,
       mf.missing_executed_instrument,mf.missing_direction,mf.missing_session,
       mf.missing_timezone,mf.missing_htf_narrative,mf.missing_draw,mf.missing_poi,
       mf.missing_confirmation,mf.missing_invalidation,mf.missing_entry,
       mf.missing_stop,mf.missing_target,mf.missing_resolved_outcome,
       (SELECT COUNT(*) FROM v_cardinal_setup_evidence e WHERE e.instance_id=si.instance_id) AS evidence_row_count,
       'discord_only' AS source_scope,
       0 AS outside_sources_used
FROM setup_instances si
JOIN messages m ON m.message_id=si.primary_message_id
JOIN v_cardinal_missing_fields mf ON mf.instance_id=si.instance_id;

CREATE VIEW v_setup_rule_matrix AS
SELECT mm.instance_id,mm.model_id,sm.canonical_name,r.rule_id,r.rule_order,
       r.rule_type,r.rule_text,r.required_state,
       COALESCE(rs.state,'unknown') AS observed_state,rs.claim_id AS state_claim_id
FROM setup_model_matches mm
JOIN setup_models sm ON sm.model_id=mm.model_id
JOIN setup_model_rules r ON r.model_id=mm.model_id
LEFT JOIN setup_rule_states rs ON rs.instance_id=mm.instance_id AND rs.rule_id=r.rule_id;

CREATE VIEW v_resolved_trade_outcomes AS
SELECT t.trade_id,t.instance_id,t.trader_id,t.trade_date_text,t.execution_mode,
       r.resolved_outcome,r.resolution_status,r.strict_comparison_eligible,
       r.resolution_reason,oc.basis,oc.terminal_at_text,oc.is_aggregate,
       oc.reported_trade_count
FROM trade_episodes t
JOIN trade_outcome_resolution r ON r.trade_id=t.trade_id
JOIN trade_outcome_claims oc ON oc.outcome_claim_id=r.resolved_outcome_claim_id
WHERE r.resolution_status='resolved';

CREATE VIEW v_selected_corpus_performance AS
SELECT p.rollup_id,c.name AS cohort_name,c.eligibility_definition_json,
       c.exclusion_definition_json,p.model_id,sm.canonical_name AS model_name,
       i.canonical_symbol,t.canonical_token AS timeframe,s.canonical_label AS session,
       p.eligible_count,p.wins,p.losses,p.breakevens,p.unknowns,p.excluded_count,
       p.distinct_authors,p.top_author_share,p.observed_win_rate,p.models_overlap,
       p.not_causal,p.limitations
FROM setup_performance_rollups p
JOIN analysis_cohorts c ON c.cohort_id=p.cohort_id
LEFT JOIN setup_models sm ON sm.model_id=p.model_id
LEFT JOIN instruments i ON i.instrument_id=p.instrument_id
LEFT JOIN timeframes t ON t.timeframe_id=p.timeframe_id
LEFT JOIN sessions s ON s.session_id=p.session_id;

CREATE VIEW v_instrument_setup_comparison AS
SELECT i.canonical_symbol,sm.canonical_name,
       COUNT(DISTINCT t.trade_id) AS resolved_executed_trades,
       SUM(CASE WHEN r.resolved_outcome='win' THEN 1 ELSE 0 END) AS wins,
       SUM(CASE WHEN r.resolved_outcome='loss' THEN 1 ELSE 0 END) AS losses,
       SUM(CASE WHEN r.resolved_outcome='breakeven' THEN 1 ELSE 0 END) AS breakevens
FROM setup_instruments si
JOIN instruments i ON i.instrument_id=si.instrument_id
JOIN setup_model_matches mm ON mm.instance_id=si.instance_id
JOIN setup_models sm ON sm.model_id=mm.model_id
JOIN trade_episodes t ON t.instance_id=si.instance_id
JOIN trade_outcome_resolution r ON r.trade_id=t.trade_id AND r.resolution_status='resolved'
WHERE si.role='executed'
GROUP BY i.instrument_id,sm.model_id;

CREATE VIEW v_authority_separated_qa AS
SELECT q.question_id,q.normalized_question,q.topic,q.resolution_status AS question_status,
       a.answer_id,a.answer_summary,a.resolution_status AS answer_status,
       aa.authority_class,aa.basis AS authority_basis,aa.confidence AS authority_confidence,
       l.link_type,l.direct_reply,l.linkage_confidence
FROM questions q
JOIN question_answer_links l ON l.question_id=q.question_id
JOIN answers a ON a.answer_id=l.answer_id
JOIN claims c ON c.claim_id=a.answer_claim_id
LEFT JOIN authority_assignments aa ON aa.assignment_id=c.authority_assignment_id;

CREATE VIEW v_unresolved_qa AS
SELECT q.question_id,q.primary_message_id,q.normalized_question,q.topic,q.subtopic,
       q.resolution_status,m.permalink
FROM questions q
JOIN messages m ON m.message_id=q.primary_message_id
WHERE q.resolution_status IN ('partial','conflicting','unanswered','ambiguous');

CREATE VIEW v_open_contradictions AS
SELECT cs.contradiction_id,cs.topic,cs.resolution_status,cs.resolution_summary,
       cm.claim_id,cm.stance,c.claim_text,c.epistemic_status,c.authority_assignment_id,
       cm.notes
FROM contradiction_sets cs
JOIN contradiction_members cm ON cm.contradiction_id=cs.contradiction_id
JOIN claims c ON c.claim_id=cm.claim_id
WHERE cs.resolution_status IN ('open','qualified');

CREATE VIEW v_discord_only_audit AS
SELECT 'accepted_claim_without_evidence' AS issue_type,c.claim_id AS entity_id,
       c.facet AS detail
FROM claims c
WHERE c.resolution_status='accepted'
  AND NOT EXISTS(SELECT 1 FROM claim_evidence ce WHERE ce.claim_id=c.claim_id)
UNION ALL
SELECT 'accepted_claim_with_untrusted_evidence',c.claim_id,c.facet
FROM claims c
JOIN claim_evidence ce ON ce.claim_id=c.claim_id
JOIN evidence_items ev ON ev.evidence_id=ce.evidence_id
WHERE c.resolution_status='accepted' AND ev.eligible_for_accepted_claims=0
UNION ALL
SELECT 'resolved_setup_with_untrusted_primary_message',si.instance_id,si.identity_resolution_status
FROM setup_instances si
JOIN messages m ON m.message_id=si.primary_message_id
WHERE si.identity_resolution_status IN ('explicit','linked','derived')
  AND m.eligible_for_accepted_evidence=0
UNION ALL
SELECT 'strict_trade_with_untrusted_primary_message',te.trade_id,te.linkage_status
FROM trade_episodes te
JOIN setup_instances si ON si.instance_id=te.instance_id
JOIN messages m ON m.message_id=si.primary_message_id
WHERE te.strict_comparison_eligible=1 AND m.eligible_for_accepted_evidence=0
UNION ALL
SELECT 'analysis_entity_without_discord_scope',e.entity_id,e.entity_type
FROM analysis_entities e
WHERE e.source_scope<>'discord_only' OR e.outside_sources_used<>0
UNION ALL
SELECT 'claim_without_discord_scope',c.claim_id,c.facet
FROM claims c
WHERE c.source_scope<>'discord_only' OR c.outside_sources_used<>0
UNION ALL
SELECT 'analysis_run_without_discord_scope',CAST(a.analysis_run_id AS TEXT),a.method
FROM analysis_runs a
WHERE a.source_scope<>'discord_only' OR a.outside_sources_used<>0
UNION ALL
SELECT 'non_owned_attachment_extraction',x.extraction_id,a.ownership_status
FROM attachment_extractions x
JOIN attachments a ON a.attachment_id=x.attachment_id
WHERE a.eligible_for_attachment_evidence<>1
UNION ALL
SELECT 'non_owned_attachment_evidence',e.evidence_id,a.ownership_status
FROM evidence_items e
JOIN attachments a ON a.attachment_id=e.attachment_id
WHERE a.eligible_for_attachment_evidence<>1;
"""


@dataclass(frozen=True)
class InputDocument:
    path: Path
    data: Any
    metadata: dict[str, Any]
    artifact_id: str
    sha256: str


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(prefix: str, *parts: Any) -> str:
    payload = "\x1f".join("" if p is None else str(p) for p in parts)
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def is_snowflake(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{15,22}", str(value or "")))


def first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def normalize_verified_extraction(
    extraction: dict[str, Any], *, attachment_id: str
) -> dict[str, Any] | None:
    """Validate corpus extraction provenance before making it queryable evidence."""

    status = first_text(extraction.get("status"))
    if status == "failed":
        if any(
            extraction.get(field) is not None
            for field in ("local_package_path", "content_sha256", "byte_size")
        ):
            raise ValueError(
                f"Failed extraction for attachment {attachment_id} claims a local artifact"
            )
        detail = " ".join(str(extraction.get("failure_detail") or "").split())
        if len(detail) < 8:
            raise ValueError(
                f"Failed extraction for attachment {attachment_id} lacks failure detail"
            )
        return None
    if status not in {"complete", "partial"}:
        raise ValueError(
            f"Attachment {attachment_id} has an invalid extraction artifact status"
        )
    if extraction.get("local_artifact_verified") is not True:
        raise ValueError(
            f"Attachment {attachment_id} extraction lacks verified local artifact provenance"
        )
    local_path = first_text(extraction.get("local_package_path"))
    if not local_path:
        raise ValueError(f"Attachment {attachment_id} extraction lacks local path")
    relative = PurePosixPath(local_path.replace("\\", "/"))
    if (
        relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or any(":" in part for part in relative.parts)
        or len(relative.parts) < 4
        or relative.parts[:2] != ("attachments", "extractions")
    ):
        raise ValueError(
            f"Attachment {attachment_id} extraction has an unsafe local path"
        )
    digest = str(extraction.get("content_sha256") or "").casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"Attachment {attachment_id} extraction lacks SHA-256")
    byte_size = extraction.get("byte_size")
    if not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size <= 0:
        raise ValueError(f"Attachment {attachment_id} extraction lacks byte size")
    confidence = extraction.get("confidence")
    if confidence is not None:
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            raise ValueError(
                f"Attachment {attachment_id} extraction confidence must be null or 0..1"
            )
        confidence = float(confidence)
    return {
        **extraction,
        "status": status,
        "local_package_path": relative.as_posix(),
        "content_sha256": digest,
        "byte_size": byte_size,
        "confidence": confidence,
    }


def validate_capture_attempts(
    attempts: list[Any], *, attachment_id: str, capture_status: str
) -> None:
    if any(not isinstance(item, dict) for item in attempts):
        raise ValueError(
            f"Attachment {attachment_id} capture_attempts contains a non-object"
        )
    for index, attempt in enumerate(attempts, start=1):
        if attempt.get("attempt_number") not in {None, index}:
            raise ValueError(
                f"Attachment {attachment_id} capture attempts are not sequential"
            )
        status = first_text(attempt.get("status"))
        if status == "failed":
            detail = " ".join(str(attempt.get("error_detail") or "").split())
            if len(detail) < 8 or detail.casefold() in {
                "error",
                "failed",
                "failure",
                "unknown",
            }:
                raise ValueError(
                    f"Attachment {attachment_id} failed attempt lacks substantive detail"
                )
    if capture_status in {"downloaded", "unavailable", "failed"} and (
        not attempts or first_text(attempts[-1].get("status")) != capture_status
    ):
        raise ValueError(
            f"Attachment {attachment_id} terminal capture status/final attempt disagree"
        )


def as_bool_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, str):
        return int(value.strip().lower() in {"1", "true", "yes", "complete"})
    return int(bool(value))


def parse_iso(value: Any) -> dt.datetime | None:
    text = first_text(value)
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text += "T00:00:00Z"
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso_text(value: Any) -> str | None:
    parsed = parse_iso(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


def load_document(path: Path) -> InputDocument:
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Input is not valid UTF-8 JSON: {path}: {exc}") from exc
    metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    sha = hashlib.sha256(raw).hexdigest().upper()
    return InputDocument(
        path=path.resolve(),
        data=data,
        metadata=metadata,
        # Bind the ID to a package-relative token so it is invariant when the
        # completed package is moved to another machine or directory.
        artifact_id=stable_id(
            "artifact", f"inputs/{sha[:16].casefold()}_{path.name}", ""
        ),
        sha256=sha,
    )


def portable_input_source(document: InputDocument) -> str:
    return f"inputs/{document.sha256[:16].casefold()}_{document.path.name}"


def portable_source_file(
    source_file: Any, *, descriptor: dict[str, Any], sha256: str | None = None
) -> str:
    text = str(source_file or "").strip().replace("\\", "/")
    candidate = PurePosixPath(text)
    windows_absolute = bool(re.match(r"^[A-Za-z]:/", text))
    if (
        text
        and not candidate.is_absolute()
        and not windows_absolute
        and all(part not in {"", ".", ".."} and ":" not in part for part in candidate.parts)
    ):
        return candidate.as_posix()
    basename = Path(text).name or "source.json"
    identity = first_text(
        sha256,
        descriptor.get("source_file_sha256"),
        descriptor.get("source_file_id"),
    )
    if not identity:
        raise ValueError(
            f"Absolute/unsafe source path lacks a portable hash or source ID: {text!r}"
        )
    token = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:16]
    return f"external/{token}_{basename}"


def validate_input_source_discipline(documents: Sequence[InputDocument]) -> None:
    for document in documents:
        containers: list[dict[str, Any]] = []
        if isinstance(document.data, dict):
            containers.append(document.data)
            if isinstance(document.data.get("scope"), dict):
                containers.append(document.data["scope"])
        containers.append(document.metadata)
        for container in containers:
            source_scope = first_text(container.get("source_scope"))
            if source_scope and source_scope.strip().lower().replace(" ", "_") != "discord_only":
                raise ValueError(
                    f"Non-Discord source scope is forbidden in {document.path}: {source_scope!r}"
                )
            outside = container.get("outside_sources_used")
            if outside is not None and as_bool_int(outside) != 0:
                raise ValueError(
                    f"outside_sources_used must be false/0 in {document.path}."
                )


def looks_like_message(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    keys = set(value)
    return bool(
        keys
        & {
            "message_id",
            "id",
            "content_text",
            "content",
            "visible_text",
            "timestamp_utc",
            "created_at_utc",
        }
    )


def discover_message_collections(data: Any) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    if isinstance(data, list):
        rows = [row for row in data if looks_like_message(row)]
        if rows:
            yield "messages", rows
        return
    if not isinstance(data, dict):
        return

    seen_lists: set[int] = set()
    preferred = ("messages", "raw_messages", "merged_messages", "all_messages")
    for key in preferred:
        value = data.get(key)
        if isinstance(value, list) and id(value) not in seen_lists:
            rows = [row for row in value if looks_like_message(row)]
            if rows:
                seen_lists.add(id(value))
                yield key, rows

    for key, value in data.items():
        if key in preferred or not isinstance(value, list) or id(value) in seen_lists:
            continue
        if key.endswith("_messages") or key in {"results", "items"}:
            rows = [row for row in value if looks_like_message(row)]
            if rows:
                seen_lists.add(id(value))
                yield key, rows

    collections = data.get("collections")
    if isinstance(collections, dict):
        for key, value in collections.items():
            if isinstance(value, list) and id(value) not in seen_lists:
                rows = [row for row in value if looks_like_message(row)]
                if rows:
                    seen_lists.add(id(value))
                    yield str(key), rows


def validate_authorized_scope_inputs(
    documents: Sequence[InputDocument],
    policy: authorized_collection_scope.AuthorizedScope,
) -> set[str]:
    """Reject a mixed/unscoped message document before SQLite creation."""

    def norm(value: Any) -> str:
        return unicodedata.normalize("NFC", str(value or "").strip()).casefold()

    authorized_ids = set(policy.parent_ids)
    proven_parent_by_child: dict[str, str] = {}
    container_names: dict[str, str] = {
        row.channel_id: row.name for row in policy.containers
    }
    container_kinds: dict[str, str] = {
        row.channel_id: row.kind for row in policy.containers
    }
    scope_summary_count = 0
    inventory_seen = False
    release_timestamp_scope_summaries: list[str] = []
    release_executed_command_summaries: list[str] = []
    for document in documents:
        data = document.data if isinstance(document.data, dict) else {}
        release_shaped = bool(
            data.get("artifact_type") == "discord_serverwide_corpus_release"
            or (
                data.get("artifact_type")
                == "discord_serverwide_coverage_manifest"
                and data.get("status") == "complete"
            )
        )
        if release_shaped:
            timestamp_errors = (
                timestamp_scope_revalidation.release_timestamp_scope_integrity_errors(
                    data
                )
            )
            if timestamp_errors:
                raise ValueError(
                    f"Release timestamp-scope integrity failed in {document.path}: "
                    + ", ".join(timestamp_errors)
                )
            release_timestamp_scope_summaries.append(
                json_text(data.get("timestamp_scope_integrity"))
            )
            executed_command_errors = (
                reply_provenance_contract.release_executed_command_integrity_errors(
                    data
                )
            )
            if executed_command_errors:
                raise ValueError(
                    f"Release executed-command reply provenance failed in {document.path}: "
                    + ", ".join(executed_command_errors)
                )
            release_executed_command_summaries.append(
                json_text(
                    data.get("executed_command_reply_provenance_integrity")
                )
            )
            if data.get("artifact_type") == "discord_serverwide_corpus_release":
                release_messages = data.get("messages")
                if not isinstance(release_messages, list) or any(
                    not isinstance(row, dict) for row in release_messages
                ):
                    raise ValueError(
                        f"Release corpus messages are malformed in {document.path}"
                    )
                semantic_errors = (
                    reply_provenance_contract
                    .release_executed_command_semantic_errors(
                        release_messages,
                        data.get(
                            "executed_command_reply_provenance_integrity"
                        ),
                    )
                )
                if semantic_errors:
                    raise ValueError(
                        "Release executed-command rows disagree with the "
                        f"declared summary in {document.path}: "
                        + ", ".join(semantic_errors)
                    )
        summary = data.get("authorized_collection_scope")
        message_collections = list(discover_message_collections(data))
        if isinstance(summary, dict) and summary.get("enabled") is True:
            scope_summary_count += 1
            if summary.get("source_sha256") != policy.source_sha256:
                raise ValueError(
                    f"Authorized scope hash mismatch in {document.path}"
                )
            if summary.get("guild_id") != policy.guild_id:
                raise ValueError(
                    f"Authorized scope guild mismatch in {document.path}"
                )
            allowed_rows = summary.get("allowed_top_level_containers")
            allowed_rows = allowed_rows if isinstance(allowed_rows, list) else []
            allowed_ids = {
                str(row.get("channel_id") or "")
                for row in allowed_rows
                if isinstance(row, dict)
            }
            if allowed_ids != set(policy.parent_ids) or len(allowed_rows) != 3:
                raise ValueError(
                    f"Authorized parent set mismatch in {document.path}"
                )
            if summary.get("canonical_path_policy") is None:
                raise ValueError(
                    f"Authorized canonical path policy is missing in {document.path}"
                )
            path_policy = summary.get("canonical_path_policy")
            path_policy = path_policy if isinstance(path_policy, dict) else {}
            required_path_policy = {
                "gate": "premium_journals_authoritative_v2_5_source_integrity",
                "passed": True,
                "standard_authoritative_directory": "raw/channel_segments",
                "premium_authoritative_directory": "raw/channel_segments_v2_5",
                "premium_collector_version_required": "2.6",
                "premium_legacy_preservation_directory": "raw/channel_segments",
                "premium_legacy_directory_policy": (
                    "preservation_only_not_authoritative"
                ),
                "required_roots_supplied_exactly_once": True,
                "legacy_premium_authoritative_occurrence_count": 0,
                "premium_collector_version_mismatch_count": 0,
                "premium_collector_version_mismatch_paths": [],
                "premium_provenance_missing_segment_count": 0,
                "premium_provenance_missing_segments": [],
                "invalid_premium_authoritative_file_count": 0,
                "invalid_premium_authoritative_paths": [],
            }
            for key, expected in required_path_policy.items():
                if path_policy.get(key) != expected:
                    raise ValueError(
                        f"Authorized canonical path policy {key} mismatch in "
                        f"{document.path}"
                    )
            for key in (
                "accepted_premium_source_file_set_sha256",
                "accepted_premium_message_id_set_sha256",
            ):
                if not re.fullmatch(r"[0-9a-f]{64}", str(path_policy.get(key) or "")):
                    raise ValueError(
                        f"Authorized canonical path policy {key} missing in {document.path}"
                    )
            if release_shaped and not (
                path_policy.get("accepted_premium_segment_count") == 201
                and path_policy.get("accepted_premium_daily_date_count") == 201
                and path_policy.get("duplicate_premium_daily_dates") == []
                and type(path_policy.get("accepted_premium_bound_source_file_count"))
                is int
                and path_policy.get("accepted_premium_bound_source_file_count")
                >= 201
            ):
                raise ValueError(
                    f"Authorized Premium provenance file coverage is incomplete in "
                    f"{document.path}"
                )
            gate = summary.get("release_gate")
            if not isinstance(gate, dict) or gate.get("passed") is not True:
                raise ValueError(
                    f"Authorized scope selection gate did not pass in {document.path}"
                )
            excluded = summary.get("excluded")
            if not isinstance(excluded, dict) or int(
                excluded.get("ambiguous_fail_closed_file_count") or 0
            ) != 0:
                raise ValueError(
                    f"Ambiguous authorized-scope exclusions remain in {document.path}"
                )
            reconciliation = summary.get("child_inventory_reconciliation")
            if not isinstance(reconciliation, dict) or reconciliation.get(
                "provided"
            ) is not True:
                raise ValueError(
                    f"Premium child reconciliation is missing in {document.path}"
                )
            if not re.fullmatch(
                r"[0-9a-f]{64}", str(reconciliation.get("source_sha256") or "")
            ):
                raise ValueError(
                    f"Premium child reconciliation SHA-256 is missing in {document.path}"
                )
            bound_inputs = reconciliation.get("bound_inputs")
            bound_inputs = bound_inputs if isinstance(bound_inputs, list) else []
            bound_roles = {
                str(row.get("role") or "")
                for row in bound_inputs
                if isinstance(row, dict)
                and re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256") or ""))
                and str(row.get("relative_path") or "")
            }
            if bound_roles != {
                "baseline",
                "additive_evidence_source",
                "additive_evidence_bound_partial",
            }:
                raise ValueError(
                    f"Premium reconciliation source bindings are incomplete in {document.path}"
                )
            message_scope_closure = reconciliation.get("message_scope_closure")
            if not isinstance(message_scope_closure, dict) or message_scope_closure.get(
                "gate"
            ) != "premium_journals_message_data_scope_closure" or message_scope_closure.get(
                "passed"
            ) is not True or message_scope_closure.get("closure_proven") is not True:
                raise ValueError(
                    f"Premium message-scope closure gate did not pass in {document.path}"
                )
            if release_shaped and not (
                message_scope_closure.get("required_parent_container_id")
                == "1283941772577472643"
                and message_scope_closure.get("required_calendar_day_count") == 201
                and message_scope_closure.get(
                    "required_exact_daily_parent_segment_count"
                )
                == 201
                and message_scope_closure.get("parent_segment_count") == 201
                and message_scope_closure.get("complete_calendar_day_count") == 201
                and message_scope_closure.get("invalid_daily_partition_segment_count")
                == 0
                and message_scope_closure.get("duplicate_daily_date_count") == 0
                and message_scope_closure.get("incomplete_segment_count") == 0
                and message_scope_closure.get(
                    "terminal_evidence_invalid_segment_count"
                )
                == 0
                and message_scope_closure.get("unresolved_row_binding_count") == 0
                and message_scope_closure.get("row_binding_conflict_count") == 0
                and message_scope_closure.get(
                    "observed_child_outside_derived_union_count"
                )
                == 0
            ):
                raise ValueError(
                    f"Premium 201-day exact canonical closure is incomplete in {document.path}"
                )
            if data.get("artifact_type") == "discord_serverwide_corpus_release":
                for occurrence in data.get("occurrences") or []:
                    if not isinstance(occurrence, dict) or str(
                        occurrence.get("query_container_id") or ""
                    ) != "1283941772577472643":
                        continue
                    source_path = str(
                        occurrence.get("source_file_relative_path") or ""
                    ).replace("\\", "/")
                    if not source_path.startswith("raw/channel_segments_v2_5/"):
                        raise ValueError(
                            "Premium occurrence is not sourced from the authoritative "
                            f"v2.5 root in {document.path}"
                        )
        elif message_collections:
            raise ValueError(
                f"Message-bearing input lacks the authorized three-channel scope: {document.path}"
            )

        inventory = data.get("inventory")
        if isinstance(inventory, dict) and isinstance(
            inventory.get("containers"), list
        ):
            inventory_seen = True
            derivation = inventory.get("scope_derivation")
            derivation = derivation if isinstance(derivation, dict) else {}
            inventory_reconciliation = derivation.get(
                "child_inventory_reconciliation"
            )
            if json_text(inventory_reconciliation) != json_text(
                summary.get("child_inventory_reconciliation")
                if isinstance(summary, dict)
                else None
            ):
                raise ValueError(
                    f"Scoped inventory/reconciliation summary mismatch in {document.path}"
                )
            added_ids = {
                str(value)
                for value in (inventory_reconciliation or {}).get(
                    "added_thread_ids", []
                )
                if is_snowflake(value)
            }
            top_level_ids: set[str] = set()
            for index, row in enumerate(inventory["containers"]):
                if not isinstance(row, dict):
                    raise ValueError(
                        f"Scoped inventory row {index} is not an object in {document.path}"
                    )
                container_id = str(row.get("container_id") or "")
                parent_id = str(row.get("parent_container_id") or "")
                if parent_id:
                    if parent_id not in policy.parent_ids:
                        raise ValueError(
                            f"Scoped inventory child {container_id} has unauthorized parent {parent_id}"
                        )
                    identity = (
                        row.get("identity_provenance")
                        if isinstance(row.get("identity_provenance"), dict)
                        else {}
                    )
                    evidence_rows = identity.get("evidence")
                    evidence_rows = (
                        evidence_rows if isinstance(evidence_rows, list) else []
                    )
                    exact_forum_card = any(
                        isinstance(evidence, dict)
                        and evidence.get("authenticated") is True
                        and evidence.get("source_scope") == "discord_only"
                        and evidence.get("outside_sources_used") is False
                        and evidence.get("method") == "forum_card_data_list_item_id"
                        and evidence.get("forum_card_data_list_item_id")
                        == f"forum-channel-list-{parent_id}___{container_id}"
                        for evidence in evidence_rows
                    )
                    verified_binding = identity.get(
                        "verified_parent_child_binding"
                    )
                    verified_binding = (
                        verified_binding
                        if isinstance(verified_binding, dict)
                        else {}
                    )
                    binding_payload = {
                        "guild_id": policy.guild_id,
                        "parent_container_id": parent_id,
                        "child_container_id": container_id,
                        "forum_card_data_list_item_id": (
                            f"forum-channel-list-{parent_id}___{container_id}"
                        ),
                    }
                    exact_forum_card = exact_forum_card or bool(
                        verified_binding.get("guild_id") == policy.guild_id
                        and verified_binding.get("parent_container_id") == parent_id
                        and verified_binding.get("child_container_id") == container_id
                        and verified_binding.get("forum_card_data_list_item_id")
                        == binding_payload["forum_card_data_list_item_id"]
                        and verified_binding.get("verification_method")
                        == "forum_card_data_list_item_id"
                        and verified_binding.get("binding_sha256")
                        == hashlib.sha256(
                            json_text(binding_payload).encode("utf-8")
                        ).hexdigest()
                    )
                    reconciled_exact = bool(
                        row.get("inventory_layer")
                        == "reconciled_exact_forum_thread"
                        and container_id in added_ids
                        and identity.get("method")
                        == "forum_group_header_navigation_exact"
                        and re.fullmatch(
                            r"[0-9a-f]{64}",
                            str(identity.get("reconciliation_source_sha256") or ""),
                        )
                        and identity.get("reconciliation_source_sha256")
                        == (inventory_reconciliation or {}).get("source_sha256")
                    )
                    if not exact_forum_card and not reconciled_exact:
                        raise ValueError(
                            f"Scoped inventory child {container_id} lacks cryptographic/semantic parentage proof"
                        )
                    previous_parent = proven_parent_by_child.get(container_id)
                    if previous_parent and previous_parent != parent_id:
                        raise ValueError(
                            f"Scoped child {container_id} has conflicting parents"
                        )
                    proven_parent_by_child[container_id] = parent_id
                    authorized_ids.add(container_id)
                    container_names[container_id] = str(row.get("name") or "")
                    container_kinds[container_id] = str(row.get("kind") or "")
                else:
                    top_level_ids.add(container_id)
                    expected = policy.containers_by_id.get(container_id)
                    if not expected or norm(row.get("name")) != norm(
                        expected.name
                    ) or norm(row.get("kind")) != norm(expected.kind):
                        raise ValueError(
                            f"Scoped top-level inventory identity mismatch for {container_id} in {document.path}"
                        )
            if top_level_ids != set(policy.parent_ids):
                raise ValueError(
                    f"Scoped inventory must contain exactly the three authorized parents in {document.path}"
                )

    if scope_summary_count == 0:
        raise ValueError("No input carries the authorized three-channel scope")
    if len(set(release_timestamp_scope_summaries)) > 1:
        raise ValueError(
            "Release corpus and manifest disagree on timestamp_scope_integrity"
        )
    if len(set(release_executed_command_summaries)) > 1:
        raise ValueError(
            "Release corpus and manifest disagree on "
            "executed_command_reply_provenance_integrity"
        )
    if not inventory_seen:
        raise ValueError("No input carries the derived scoped inventory")

    for document in documents:
        data = document.data if isinstance(document.data, dict) else {}
        message_rows: dict[str, dict[str, Any]] = {}
        for collection_name, rows in discover_message_collections(data):
            for row_index, row in enumerate(rows, start=1):
                channel_id = str(
                    row.get("channel_id")
                    or row.get("message_channel_id")
                    or ""
                )
                if channel_id not in authorized_ids:
                    raise ValueError(
                        "Out-of-scope message container in scoped database input: "
                        f"{document.path}:{collection_name}[{row_index}]={channel_id!r}"
                    )
                message_id = str(row.get("message_id") or row.get("id") or "")
                if not is_snowflake(message_id):
                    raise ValueError(
                        f"Scoped message has no exact Discord ID in {document.path}:{collection_name}[{row_index}]"
                    )
                message_rows[message_id] = row

        if not message_rows:
            continue
        segments = data.get("segments")
        segments = segments if isinstance(segments, list) else []
        segments_by_id = {
            str(row.get("segment_id")): row
            for row in segments
            if isinstance(row, dict) and str(row.get("segment_id") or "")
        }
        occurrences = explicit_occurrence_rows(data)
        if not occurrences:
            raise ValueError(
                f"Scoped message input lacks explicit provenance occurrences: {document.path}"
            )
        occurrences_by_id: dict[str, dict[str, Any]] = {}
        occurrences_by_message: dict[str, list[dict[str, Any]]] = {}
        for index, occurrence in enumerate(occurrences, start=1):
            occurrence_id = str(occurrence.get("occurrence_id") or "")
            message_id = str(occurrence.get("message_id") or "")
            if not occurrence_id or occurrence_id in occurrences_by_id:
                raise ValueError(
                    f"Scoped occurrence ID missing/duplicate in {document.path}:occurrences[{index}]"
                )
            if message_id not in message_rows:
                raise ValueError(
                    f"Scoped occurrence references no accepted message in {document.path}:occurrences[{index}]"
                )
            query_container_id = str(
                occurrence.get("query_container_id") or ""
            )
            message_container_id = str(
                occurrence.get("message_container_id") or ""
            )
            parent_id = str(occurrence.get("parent_container_id") or "")
            query = str(occurrence.get("source_query") or "").strip()
            if query_container_id not in authorized_ids:
                raise ValueError(
                    f"Out-of-scope explicit occurrence query container in {document.path}:occurrences[{index}]={query_container_id!r}"
                )
            query_target, query_errors = (
                authorized_collection_scope.parse_discord_search_query(query)
            )
            if query_errors:
                raise ValueError(
                    f"Malformed scoped occurrence query in {document.path}:occurrences[{index}]:{','.join(query_errors)}"
                )
            segment_id = str(occurrence.get("segment_id") or "")
            segment = segments_by_id.get(segment_id)
            if occurrence.get("source_kind") == "channel_segment" and not segment:
                raise ValueError(
                    f"Scoped channel occurrence lacks bound segment in {document.path}:occurrences[{index}]"
                )
            if segment:
                if (
                    str(segment.get("query") or "") != query
                    or str(segment.get("query_container_id") or "")
                    != query_container_id
                ):
                    raise ValueError(
                        f"Scoped occurrence/segment query binding mismatch in {document.path}:occurrences[{index}]"
                    )
                expected_query_name = str(
                    segment.get("query_container_name") or ""
                )
                expected_kind = str(segment.get("query_container_kind") or "")
            else:
                expected_query_name = container_names.get(query_container_id, "")
                expected_kind = container_kinds.get(query_container_id, "")
            if norm(query_target) != norm(expected_query_name):
                raise ValueError(
                    f"Scoped occurrence query target/name mismatch in {document.path}:occurrences[{index}]"
                )
            payload = (
                occurrence.get("payload")
                if isinstance(occurrence.get("payload"), dict)
                else {}
            )
            if not (
                str(payload.get("search_query") or "").strip() == query
                and str(payload.get("collection_channel_id") or "")
                == query_container_id
                and norm(payload.get("collection_channel_name"))
                == norm(expected_query_name)
                and norm(payload.get("collection_channel_kind"))
                == norm(expected_kind)
                and str(payload.get("collection_channel_id_source") or "")
                in authorized_collection_scope.TRUSTED_REQUEST_ID_SOURCES
            ):
                raise ValueError(
                    f"Scoped occurrence row collection provenance mismatch in {document.path}:occurrences[{index}]"
                )
            if query_container_id == "1283941772577472643":
                if proven_parent_by_child.get(message_container_id) != query_container_id:
                    raise ValueError(
                        f"Premium occurrence has unproven child container in {document.path}:occurrences[{index}]"
                    )
                if parent_id != query_container_id:
                    raise ValueError(
                        f"Premium occurrence parent binding mismatch in {document.path}:occurrences[{index}]"
                    )
            elif query_container_id in policy.parent_ids:
                if message_container_id != query_container_id or parent_id:
                    raise ValueError(
                        f"Text-channel occurrence container binding mismatch in {document.path}:occurrences[{index}]"
                    )
            else:
                expected_parent = proven_parent_by_child.get(query_container_id)
                if (
                    message_container_id != query_container_id
                    or not expected_parent
                    or parent_id not in {"", expected_parent}
                ):
                    raise ValueError(
                        f"Direct-child occurrence parent binding mismatch in {document.path}:occurrences[{index}]"
                    )
            occurrences_by_id[occurrence_id] = occurrence
            occurrences_by_message.setdefault(message_id, []).append(occurrence)

        for message_id, message in message_rows.items():
            provenance = (
                message.get("_corpus_provenance")
                if isinstance(message.get("_corpus_provenance"), dict)
                else {}
            )
            canonical_occurrence_id = str(
                provenance.get("canonical_occurrence_id") or ""
            )
            canonical = occurrences_by_id.get(canonical_occurrence_id)
            if canonical is None or canonical.get("message_id") != message_id:
                raise ValueError(
                    f"Scoped canonical message lacks exact occurrence binding in {document.path}:{message_id}"
                )
            if str(canonical.get("message_container_id") or "") != str(
                message.get("channel_id") or ""
            ):
                raise ValueError(
                    f"Scoped canonical message/occurrence container mismatch in {document.path}:{message_id}"
                )
            declared_occurrence_ids = {
                str(value) for value in provenance.get("occurrence_ids") or []
            }
            actual_occurrence_ids = {
                str(row.get("occurrence_id"))
                for row in occurrences_by_message.get(message_id, [])
            }
            if declared_occurrence_ids != actual_occurrence_ids:
                raise ValueError(
                    f"Scoped message occurrence-set parity mismatch in {document.path}:{message_id}"
                )
    return authorized_ids


def message_timestamp(message: dict[str, Any]) -> str | None:
    return iso_text(
        first_text(
            message.get("timestamp_utc"),
            message.get("created_at_utc"),
            message.get("created_at"),
            message.get("timestamp"),
        )
    )


MESSAGE_TRUST_STATES = {
    "trusted_canonical_recapture",
    "trusted_source",
    "quarantined_only",
    "untrusted_noncanonical_only",
    "conflicting",
}


def int_or_zero(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def message_trust_fields(message: dict[str, Any]) -> tuple[str, int, int, int, int]:
    provenance = (
        message.get("_corpus_provenance")
        if isinstance(message.get("_corpus_provenance"), dict)
        else {}
    )
    explicit_state = first_text(
        message.get("evidence_trust_state"), provenance.get("evidence_trust_state")
    )
    explicit_eligible = message.get(
        "eligible_for_accepted_evidence",
        provenance.get("eligible_for_accepted_evidence"),
    )
    migration_quarantined = bool(
        message.get("migration_quarantined")
        or message.get("migration_quarantine_reasons")
    )
    source_quarantined = bool(message.get("quarantined") or migration_quarantined)
    if explicit_eligible is not None:
        eligible = as_bool_int(explicit_eligible)
    elif source_quarantined:
        eligible = 0
    else:
        # Backward-compatible direct Discord inputs remain trusted sources. A
        # corpus produced by build_corpus always supplies an explicit state.
        eligible = 1
    state = explicit_state if explicit_state in MESSAGE_TRUST_STATES else None
    if state is None:
        state = "quarantined_only" if not eligible else "trusted_source"
    if state in {"quarantined_only", "untrusted_noncanonical_only", "conflicting"}:
        eligible = 0
    trusted_canonical_count = int_or_zero(
        message.get(
            "trusted_canonical_occurrence_count",
            provenance.get("trusted_canonical_occurrence_count"),
        )
    )
    quarantined_count = int_or_zero(
        message.get(
            "quarantined_occurrence_count",
            provenance.get("quarantined_occurrence_count"),
        )
    )
    has_quarantined = as_bool_int(
        message.get("has_quarantined_occurrences"),
        1 if source_quarantined or quarantined_count else 0,
    )
    return state, eligible, has_quarantined, trusted_canonical_count, quarantined_count


def occurrence_trust_fields(
    row: dict[str, Any], payload: dict[str, Any] | None = None
) -> tuple[str, int, int, int, str, list[str]]:
    payload = payload if isinstance(payload, dict) else {}
    provenance = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
    source_kind = first_text(row.get("source_kind"), payload.get("source_kind")) or "unknown"
    migration_source = as_bool_int(
        row.get("migration_source"),
        as_bool_int(
            payload.get("migration_source"),
            int(
                bool(
                    row.get("migration_occurrence_id")
                    or payload.get("migration_occurrence_id")
                    or payload.get("_migration_occurrence")
                )
            ),
        ),
    )
    reasons = normalize_reason_values(
        row.get(
            "quarantine_reasons",
            row.get(
                "migration_quarantine_reasons",
                payload.get(
                    "quarantine_reasons", payload.get("migration_quarantine_reasons")
                ),
            ),
        )
    )
    quarantined = as_bool_int(
        row.get("quarantined"),
        as_bool_int(
            row.get("migration_quarantined"),
            as_bool_int(
                payload.get("quarantined"),
                as_bool_int(payload.get("migration_quarantined"), int(bool(reasons))),
            ),
        ),
    )
    complete_source = as_bool_int(
        row.get("complete_source"),
        as_bool_int(
            row.get("artifact_declared_complete"),
            as_bool_int(
                provenance.get("complete_source"),
                as_bool_int(payload.get("complete_source"), 0),
            ),
        ),
    )
    trusted_canonical = int(
        source_kind == "channel_segment"
        and complete_source == 1
        and not migration_source
        and not quarantined
    )
    if trusted_canonical:
        state = "trusted_canonical"
    elif quarantined and migration_source:
        state = "quarantined_migration"
    elif quarantined:
        state = "quarantined_other"
    elif (
        migration_source
        or source_kind != "channel_segment"
        or complete_source != 1
    ):
        state = "untrusted_noncanonical"
    else:
        state = "trusted_source"
    return source_kind, migration_source, quarantined, trusted_canonical, state, reasons


def normalize_reason_values(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return sorted(
        {
            str(item).strip()
            for item in values
            if isinstance(item, (str, int, float)) and str(item).strip()
        }
    )


def resolve_window(
    documents: Sequence[InputDocument],
    collections: Sequence[tuple[InputDocument, str, list[dict[str, Any]]]],
    start_override: str | None,
    end_override: str | None,
) -> tuple[str, str]:
    if bool(start_override) != bool(end_override):
        raise ValueError("Provide both --window-start and --window-end, or neither.")
    if start_override and end_override:
        start = parse_iso(start_override)
        end = parse_iso(end_override)
        if not start or not end:
            raise ValueError("Window overrides must be ISO-8601 timestamps or YYYY-MM-DD dates.")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_override):
            end += dt.timedelta(days=1)
    else:
        start = end = None
        for document in documents:
            metadata = document.metadata
            merge = metadata.get("merge") if isinstance(metadata.get("merge"), dict) else {}
            scope = (
                document.data.get("scope")
                if isinstance(document.data, dict) and isinstance(document.data.get("scope"), dict)
                else {}
            )
            local_window = (
                document.data.get("requested_local_window")
                if isinstance(document.data, dict)
                and isinstance(document.data.get("requested_local_window"), dict)
                else {}
            )
            start_value = first_text(
                scope.get("utc_start_inclusive"),
                scope.get("window_start_utc"),
                scope.get("window_start"),
                scope.get("start_utc"),
                scope.get("start"),
                local_window.get("start_inclusive"),
                merge.get("window_start_utc"),
                merge.get("requested_window_start_date"),
                metadata.get("window_start_utc"),
                metadata.get("requested_window_start_date"),
            )
            end_value = first_text(
                scope.get("utc_end_exclusive"),
                scope.get("window_end_utc"),
                scope.get("window_end"),
                scope.get("end_utc"),
                scope.get("end"),
                local_window.get("end_exclusive"),
                merge.get("window_end_utc"),
                merge.get("requested_window_end_date"),
                metadata.get("window_end_utc"),
                metadata.get("requested_window_end_date"),
            )
            candidate_start = parse_iso(start_value)
            candidate_end = parse_iso(end_value)
            if candidate_start and (start is None or candidate_start < start):
                start = candidate_start
            if candidate_end:
                if end_value and re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_value):
                    candidate_end += dt.timedelta(days=1)
                if end is None or candidate_end > end:
                    end = candidate_end
        if start is None or end is None:
            timestamps = [
                parsed
                for _doc, _name, rows in collections
                for row in rows
                if (parsed := parse_iso(message_timestamp(row))) is not None
            ]
            if not timestamps:
                raise ValueError("Could not determine a collection window; use --window-start/--window-end.")
            start = start or min(timestamps).replace(hour=0, minute=0, second=0, microsecond=0)
            if end is None:
                latest = max(timestamps)
                end = latest.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)
    if start >= end:
        raise ValueError("Collection window start must be before its end.")
    return (
        start.isoformat().replace("+00:00", "Z"),
        end.isoformat().replace("+00:00", "Z"),
    )


def extract_guild(documents: Sequence[InputDocument]) -> tuple[str, str | None]:
    for document in documents:
        scope = (
            document.data.get("scope")
            if isinstance(document.data, dict) and isinstance(document.data.get("scope"), dict)
            else {}
        )
        guild_id = first_text(
            document.metadata.get("guild_id"),
            scope.get("guild_id"),
            document.data.get("guild_id") if isinstance(document.data, dict) else None,
        )
        if guild_id:
            return guild_id, first_text(
                document.metadata.get("guild_name"),
                scope.get("guild_name"),
                document.data.get("guild_name") if isinstance(document.data, dict) else None,
            )
    return "1167376964680691732", None


def extract_author(message: dict[str, Any], timestamp: str | None) -> tuple[str, str | None, int, str]:
    author_value = message.get("author")
    nested = author_value if isinstance(author_value, dict) else {}
    display_name = first_text(
        message.get("author_display_name"),
        nested.get("display_name"),
        nested.get("global_name"),
        nested.get("username"),
        author_value if not isinstance(author_value, dict) else None,
    )
    raw_id = first_text(
        message.get("author_id"),
        message.get("user_id"),
        nested.get("id"),
        nested.get("user_id"),
    )
    if is_snowflake(raw_id):
        return f"discord-user:{raw_id}", display_name, 1, "exact_discord_user_id"
    token = display_name or first_text(raw_id) or "unavailable"
    return (
        stable_id("author-display-token", token),
        display_name,
        0,
        "display_name_token_not_verified_unique",
    )


def extract_channel(message: dict[str, Any], guild_id: str) -> tuple[str, int, str | None, str | None, str | None, str]:
    attachments = message.get("attachments") if isinstance(message.get("attachments"), list) else []
    attachment_channel = None
    for attachment in attachments:
        if isinstance(attachment, dict):
            attachment_channel = first_text(
                attachment.get("thread_channel_id"), attachment.get("channel_id")
            )
            if attachment_channel:
                break
    exact_raw_id = first_text(
        message.get("channel_id"),
        message.get("thread_channel_id"),
    )
    inferred_raw_id = first_text(
        message.get("inferred_thread_channel_id"), attachment_channel
    )
    thread_title = first_text(message.get("thread_title"), message.get("channel_name"))
    parent_name = first_text(message.get("parent_channel"), message.get("parent_channel_name"))
    channel_name = thread_title or first_text(message.get("group_label"), parent_name)
    parent_id = first_text(message.get("parent_channel_id"))
    if is_snowflake(exact_raw_id):
        channel_id = str(exact_raw_id)
        exact = 1
        basis = "exact_row_owned_discord_id"
    elif is_snowflake(inferred_raw_id):
        # A syntactically valid snowflake parsed from an attachment CDN path or
        # another explicitly inferred field is still not row-owned container
        # evidence.  Retain the locator, but never promote it to exact.
        channel_id = str(inferred_raw_id)
        exact = 0
        basis = "inferred_attachment_or_legacy_discord_id"
    else:
        channel_id = stable_id("channel-observed", guild_id, parent_name, channel_name)
        exact = 0
        basis = "observed_name_surrogate"
    return channel_id, exact, channel_name, thread_title, parent_id, basis


def extract_message_id(message: dict[str, Any], channel_id: str, collection_name: str) -> tuple[str, int]:
    raw_id = first_text(message.get("message_id"), message.get("id"))
    if is_snowflake(raw_id):
        return str(raw_id), 1
    timestamp = message_timestamp(message)
    content = first_text(message.get("content_text"), message.get("content"), message.get("visible_text")) or ""
    return stable_id("message-surrogate", channel_id, timestamp, content, collection_name), 0


def extract_attachments(message: dict[str, Any]) -> list[dict[str, Any]]:
    value = message.get("attachments")
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def normalized_attachment_ownership(
    attachment: dict[str, Any], *, message_id: str, url: str | None
) -> tuple[str, str, dict[str, Any], int, int, str | None]:
    """Return fail-closed ownership fields and the exact CDN source channel.

    Only exact owner/source/DOM evidence can make an attachment capture- or
    evidence-eligible.  Everything else remains queryable metadata with an
    explicit unresolved/non-owned label.
    """

    raw_relation = str(
        attachment.get("relation_type") or attachment.get("ownership") or ""
    ).strip()
    evidence = (
        copy.deepcopy(attachment.get("ownership_evidence"))
        if isinstance(attachment.get("ownership_evidence"), dict)
        else {}
    )
    source_channel_id: str | None = None
    if url:
        try:
            source_channel_id = str(
                discord_attachment_archiver.parse_discord_attachment_url(url)[
                    "source_channel_id"
                ]
            )
        except discord_attachment_archiver.AttachmentArchiveError:
            source_channel_id = None
    try:
        relation_class = discord_attachment_archiver.exact_attachment_relation(
            attachment, message_id=message_id
        )
    except discord_attachment_archiver.AttachmentArchiveError:
        return (
            raw_relation or "unresolved",
            "unresolved",
            evidence,
            0,
            0,
            source_channel_id,
        )

    owner_channel_id = str(evidence.get("owner_channel_id") or "")
    evidence_source_channel_id = str(evidence.get("source_channel_id") or "")
    exact_common = bool(
        evidence.get("exact") is True
        and str(evidence.get("owner_message_id") or "") == message_id
        and is_snowflake(owner_channel_id)
        and source_channel_id
    )
    if relation_class == "owned":
        exact = bool(
            exact_common
            and owner_channel_id == source_channel_id
            and evidence_source_channel_id in {"", source_channel_id}
        )
        if exact:
            return raw_relation or "owned", "owned_exact", evidence, 1, 1, source_channel_id
    elif relation_class == "non_owned":
        exact = bool(
            exact_common
            and evidence_source_channel_id == source_channel_id
            and str(evidence.get("dom_relation") or attachment.get("dom_relation") or "").strip()
        )
        if exact:
            return (
                raw_relation or "non_owned",
                "non_owned_exact",
                evidence,
                0,
                0,
                source_channel_id,
            )
    return raw_relation or "unresolved", "unresolved", evidence, 0, 0, source_channel_id


def canonical_permalink(message: dict[str, Any], guild_id: str, channel_id: str, message_id: str) -> tuple[str | None, str]:
    exact = first_text(message.get("permalink"), message.get("message_url"))
    if exact and "/channels/" in exact and "undefined" not in exact:
        return exact, "exact"
    inferred = first_text(message.get("inferred_permalink"), message.get("source_url"))
    if inferred and "/channels/" in inferred and "undefined" not in inferred:
        return inferred, "inferred"
    if is_snowflake(guild_id) and is_snowflake(channel_id) and is_snowflake(message_id):
        return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}", "inferred"
    return None, "unavailable"


def insert_author(
    con: sqlite3.Connection,
    author_id: str,
    display_name: str | None,
    exact: int,
    resolution: str,
    timestamp: str | None,
    raw_author: Any,
) -> None:
    con.execute(
        """
        INSERT INTO authors(
          author_id,discord_user_id,user_id_exact,identity_resolution,surrogate_key,
          first_seen_utc,last_seen_utc,source_json
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(author_id) DO UPDATE SET
          first_seen_utc=CASE
            WHEN excluded.first_seen_utc IS NULL THEN authors.first_seen_utc
            WHEN authors.first_seen_utc IS NULL OR excluded.first_seen_utc<authors.first_seen_utc THEN excluded.first_seen_utc
            ELSE authors.first_seen_utc END,
          last_seen_utc=CASE
            WHEN excluded.last_seen_utc IS NULL THEN authors.last_seen_utc
            WHEN authors.last_seen_utc IS NULL OR excluded.last_seen_utc>authors.last_seen_utc THEN excluded.last_seen_utc
            ELSE authors.last_seen_utc END
        """,
        (
            author_id,
            author_id.removeprefix("discord-user:") if exact else None,
            exact,
            resolution,
            None if exact else author_id,
            timestamp,
            timestamp,
            json_text(raw_author if raw_author is not None else {}),
        ),
    )
    if display_name:
        con.execute(
            """
            INSERT OR IGNORE INTO author_names(
              author_id,display_name,valid_from_utc,valid_to_utc,evidence_message_id
            ) VALUES(?,?,?,?,NULL)
            """,
            (author_id, display_name, timestamp or "", None),
        )


def insert_channel(
    con: sqlite3.Connection,
    guild_id: str,
    channel_id: str,
    exact: int,
    channel_name: str | None,
    parent_id: str | None,
    basis: str,
    timestamp: str | None,
    raw: dict[str, Any],
) -> None:
    kind = first_text(raw.get("channel_kind"), raw.get("kind")) or (
        "thread" if raw.get("thread_title") else "unknown"
    )
    con.execute(
        """
        INSERT INTO channel_inventory(
          channel_id,guild_id,parent_channel_id,name,kind,exact_id_known,
          is_archived,is_accessible,inventory_basis,discovered_at_utc,
          first_seen_utc,last_seen_utc,source_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(channel_id) DO UPDATE SET
          name=COALESCE(channel_inventory.name,excluded.name),
          exact_id_known=MAX(channel_inventory.exact_id_known,excluded.exact_id_known),
          first_seen_utc=CASE
            WHEN excluded.first_seen_utc IS NULL THEN channel_inventory.first_seen_utc
            WHEN channel_inventory.first_seen_utc IS NULL OR excluded.first_seen_utc<channel_inventory.first_seen_utc THEN excluded.first_seen_utc
            ELSE channel_inventory.first_seen_utc END,
          last_seen_utc=CASE
            WHEN excluded.last_seen_utc IS NULL THEN channel_inventory.last_seen_utc
            WHEN channel_inventory.last_seen_utc IS NULL OR excluded.last_seen_utc>channel_inventory.last_seen_utc THEN excluded.last_seen_utc
            ELSE channel_inventory.last_seen_utc END
        """,
        (
            channel_id,
            guild_id,
            parent_id,
            channel_name,
            kind,
            exact,
            None,
            1,
            basis,
            timestamp,
            timestamp,
            timestamp,
            json_text({
                "channel_id": raw.get("channel_id"),
                "thread_title": raw.get("thread_title"),
                "parent_channel": raw.get("parent_channel"),
                "group_label": raw.get("group_label"),
            }),
        ),
    )


def ensure_source_artifact(
    con: sqlite3.Connection,
    run_id: int,
    source_file: str,
    collection_method: str,
    collection_name: str,
    query_text: str,
    parent_artifact_id: str | None,
    declared_complete: int | None,
    descriptor: dict[str, Any],
    sha256: str | None = None,
) -> str:
    source_file = portable_source_file(
        source_file, descriptor=descriptor, sha256=sha256
    )
    existing = con.execute(
        "SELECT artifact_id,sha256 FROM source_artifacts WHERE run_id=? AND source_file=?",
        (run_id, source_file),
    ).fetchone()
    if existing is not None:
        if sha256 and existing[1] and str(existing[1]).casefold() != str(
            sha256
        ).casefold():
            raise ValueError(
                f"Conflicting SHA-256 values for portable source {source_file}"
            )
        return str(existing[0])
    artifact_id = stable_id("artifact", source_file, sha256 or "")
    con.execute(
        """
        INSERT INTO source_artifacts(
          artifact_id,run_id,parent_artifact_id,source_file,sha256,
          collection_method,collection_name,query_text,captured_at_utc,
          declared_artifact_complete,descriptor_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(artifact_id) DO NOTHING
        """,
        (
            artifact_id,
            run_id,
            parent_artifact_id,
            source_file,
            sha256,
            collection_method,
            collection_name,
            query_text,
            None,
            declared_complete,
            json_text(descriptor),
        ),
    )
    return artifact_id


def insert_message(
    con: sqlite3.Connection,
    run_id: int,
    guild_id: str,
    collection_name: str,
    message: dict[str, Any],
    input_artifact_id: str,
) -> tuple[str, str, str | None]:
    timestamp = message_timestamp(message)
    channel_id, channel_exact, channel_name, thread_title, parent_id, channel_basis = extract_channel(message, guild_id)
    message_id, message_exact = extract_message_id(message, channel_id, collection_name)
    author_id, display_name, author_exact, author_resolution = extract_author(message, timestamp)
    insert_author(con, author_id, display_name, author_exact, author_resolution, timestamp, message.get("author"))
    insert_channel(con, guild_id, channel_id, channel_exact, channel_name, parent_id, channel_basis, timestamp, message)

    content_text = first_text(message.get("content_text"), message.get("content")) or ""
    visible_text = first_text(message.get("visible_text"))
    reply_to_content = first_text(message.get("reply_to_content"))
    reply_to_message_id = first_text(message.get("reply_to_message_id"))
    source_reply_state = first_text(message.get("reply_target_state"))
    allowed_reply_states = {
        "resolved",
        "outside_window",
        "context_stub",
        "deleted",
        "inaccessible",
        "unavailable",
        "not_applicable",
    }
    if not reply_to_message_id:
        reply_target_state = "not_applicable"
    elif source_reply_state in allowed_reply_states:
        reply_target_state = source_reply_state
    else:
        reply_target_state = "unavailable"
    permalink, permalink_confidence = canonical_permalink(message, guild_id, channel_id, message_id)
    content_sha = hashlib.sha256(content_text.encode("utf-8")).hexdigest().upper()
    raw_json = json_text(message)
    (
        evidence_trust_state,
        eligible_for_accepted_evidence,
        has_quarantined_occurrences,
        trusted_canonical_occurrence_count,
        quarantined_occurrence_count,
    ) = message_trust_fields(message)
    corpus_provenance = (
        message.get("_corpus_provenance")
        if isinstance(message.get("_corpus_provenance"), dict)
        else {}
    )
    canonical_selection_method = first_text(
        message.get("canonical_selection_method"),
        corpus_provenance.get("canonical_selection_rule"),
    ) or "first_seen_then_richer_text"
    existing = con.execute(
        """
        SELECT content_text,visible_text,raw_json,evidence_trust_state,
               eligible_for_accepted_evidence,has_quarantined_occurrences,
               trusted_canonical_occurrence_count,quarantined_occurrence_count,
               canonical_selection_method
        FROM messages WHERE message_id=?
        """,
        (message_id,),
    ).fetchone()
    if existing is None:
        con.execute(
            """
            INSERT INTO messages(
              message_id,message_id_exact,run_id,guild_id,channel_id,parent_channel_id,
              channel_name,thread_title,author_id,author_display_name,created_at_utc,
              displayed_time,edited,is_original_poster,reply_to_message_id,
              reply_to_content,reply_target_state,content_text,visible_text,content_sha256,permalink,
              permalink_confidence,evidence_trust_state,eligible_for_accepted_evidence,
              has_quarantined_occurrences,trusted_canonical_occurrence_count,
              quarantined_occurrence_count,canonical_selection_method,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                message_id,
                message_exact,
                run_id,
                guild_id,
                channel_id,
                parent_id,
                channel_name,
                thread_title,
                author_id,
                display_name,
                timestamp,
                first_text(message.get("displayed_time")),
                as_bool_int(message.get("edited")),
                as_bool_int(message.get("is_op"), as_bool_int(message.get("is_original_poster"))),
                reply_to_message_id,
                reply_to_content,
                reply_target_state,
                content_text,
                visible_text,
                content_sha,
                permalink,
                permalink_confidence,
                evidence_trust_state,
                eligible_for_accepted_evidence,
                has_quarantined_occurrences,
                trusted_canonical_occurrence_count,
                quarantined_occurrence_count,
                canonical_selection_method,
                raw_json,
            ),
        )
    else:
        (
            old_content,
            old_visible,
            old_raw,
            old_trust_state,
            old_eligible,
            old_has_quarantined,
            old_trusted_count,
            old_quarantined_count,
            old_selection_method,
        ) = existing
        if eligible_for_accepted_evidence > old_eligible:
            selected_content = content_text
            selected_visible = visible_text
            selected_raw = raw_json
            selected_selection_method = canonical_selection_method
        elif eligible_for_accepted_evidence < old_eligible:
            selected_content = old_content
            selected_visible = old_visible
            selected_raw = old_raw
            selected_selection_method = old_selection_method
        else:
            selected_content = (
                content_text if len(content_text) > len(old_content or "") else old_content
            )
            selected_visible = (
                visible_text if len(visible_text or "") > len(old_visible or "") else old_visible
            )
            selected_raw = raw_json if len(raw_json) > len(old_raw or "") else old_raw
            selected_selection_method = (
                canonical_selection_method
                if len(canonical_selection_method) > len(old_selection_method or "")
                else old_selection_method
            )
        merged_eligible = max(int(old_eligible), eligible_for_accepted_evidence)
        if merged_eligible:
            merged_trust_state = (
                "trusted_canonical_recapture"
                if "trusted_canonical_recapture"
                in {old_trust_state, evidence_trust_state}
                else "trusted_source"
            )
        elif "conflicting" in {old_trust_state, evidence_trust_state}:
            merged_trust_state = "conflicting"
        elif "quarantined_only" in {old_trust_state, evidence_trust_state}:
            merged_trust_state = "quarantined_only"
        else:
            merged_trust_state = "untrusted_noncanonical_only"
        con.execute(
            """
            UPDATE messages
            SET content_text=?,visible_text=?,raw_json=?,content_sha256=?,
                evidence_trust_state=?,eligible_for_accepted_evidence=?,
                has_quarantined_occurrences=?,trusted_canonical_occurrence_count=?,
                quarantined_occurrence_count=?,canonical_selection_method=?
            WHERE message_id=?
            """,
            (
                selected_content,
                selected_visible,
                selected_raw,
                hashlib.sha256((selected_content or "").encode("utf-8")).hexdigest().upper(),
                merged_trust_state,
                merged_eligible,
                max(int(old_has_quarantined), has_quarantined_occurrences),
                max(int(old_trusted_count), trusted_canonical_occurrence_count),
                max(int(old_quarantined_count), quarantined_occurrence_count),
                selected_selection_method,
                message_id,
            ),
        )

    if not con.execute(
        "SELECT 1 FROM message_versions WHERE message_id=? AND content_sha256=?",
        (message_id, content_sha),
    ).fetchone():
        version_no = con.execute(
            "SELECT COALESCE(MAX(version_no),0)+1 FROM message_versions WHERE message_id=?",
            (message_id,),
        ).fetchone()[0]
        con.execute(
            """
            INSERT INTO message_versions(
              message_id,version_no,content_text,visible_text,edited_at_utc,
              content_sha256,version_basis,artifact_id,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                message_id,
                version_no,
                content_text,
                visible_text,
                None,
                content_sha,
                "source_occurrence_not_assumed_edit",
                input_artifact_id,
                raw_json,
            ),
        )

    if reply_to_message_id:
        con.execute(
            """
            INSERT OR IGNORE INTO message_relations(
              from_message_id,to_message_id,relation_type,linkage_confidence,source_artifact_id
            ) VALUES(?,?,'reply',1.0,?)
            """,
            (message_id, reply_to_message_id, input_artifact_id),
        )

    for attachment in extract_attachments(message):
        raw_attachment_id = first_text(attachment.get("attachment_id"), attachment.get("id"))
        url = first_text(attachment.get("url"), attachment.get("discord_url"))
        attachment_id = (
            str(raw_attachment_id)
            if is_snowflake(raw_attachment_id)
            else stable_id("attachment-surrogate", message_id, raw_attachment_id, url, attachment.get("filename"))
        )
        (
            relation_type,
            ownership_status,
            ownership_evidence,
            owned_for_capture,
            eligible_for_attachment_evidence,
            url_source_channel_id,
        ) = normalized_attachment_ownership(
            attachment, message_id=message_id, url=url
        )
        filename = first_text(attachment.get("filename"), attachment.get("name"))
        mime_type = first_text(attachment.get("content_type"), attachment.get("mime_type"))
        suffix = Path(filename).suffix.lower() if filename else ""
        media_kind = "image" if (mime_type or "").startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"} else "file"
        capture_status = first_text(
            attachment.get("capture_status"), attachment.get("download_status")
        ) or "metadata_only"
        if not owned_for_capture:
            capture_status = "metadata_only"
        if capture_status not in {
            "metadata_only",
            "pending",
            "downloaded",
            "unavailable",
            "failed",
        }:
            capture_status = "metadata_only"
        extraction_status = first_text(attachment.get("extraction_status")) or "not_attempted"
        if not owned_for_capture:
            extraction_status = "not_attempted"
        if extraction_status not in {"not_attempted", "complete", "partial", "failed"}:
            extraction_status = "not_attempted"
        capture_attempts = (
            attachment.get("capture_attempts")
            if owned_for_capture and isinstance(attachment.get("capture_attempts"), list)
            else []
        )
        validate_capture_attempts(
            capture_attempts,
            attachment_id=attachment_id,
            capture_status=capture_status,
        )
        extraction_artifacts = (
            attachment.get("extraction_artifacts")
            if owned_for_capture and isinstance(attachment.get("extraction_artifacts"), list)
            else []
        )
        if any(not isinstance(item, dict) for item in extraction_artifacts):
            raise ValueError(
                f"Attachment {attachment_id} extraction_artifacts contains a non-object"
            )
        if extraction_status == "not_attempted" and extraction_artifacts:
            raise ValueError(
                f"Attachment {attachment_id} is not_attempted but has extraction records"
            )
        if extraction_status != "not_attempted" and (
            not extraction_artifacts
            or first_text(extraction_artifacts[-1].get("status")) != extraction_status
        ):
            raise ValueError(
                f"Attachment {attachment_id} extraction status/final record disagree"
            )
        verified_extractions = [
            normalized
            for normalized in (
                normalize_verified_extraction(item, attachment_id=attachment_id)
                for item in extraction_artifacts
            )
            if normalized is not None
        ]
        if extraction_status in {"complete", "partial"} and not verified_extractions:
            raise ValueError(
                f"Attachment {attachment_id} extraction status lacks a verified local artifact"
            )
        capture_terminal = int(
            owned_for_capture
            and (
                attachment.get("capture_terminal") is True
                or capture_status in {"downloaded", "unavailable", "failed"}
            )
        )
        capture_attempt_count = attachment.get("capture_attempt_count")
        if not owned_for_capture:
            capture_attempt_count = 0
        elif not isinstance(capture_attempt_count, int) or capture_attempt_count < 0:
            capture_attempt_count = len(capture_attempts)
        con.execute(
            """
            INSERT OR IGNORE INTO attachments(
              attachment_id,message_id,attachment_id_exact,filename,discord_url,
              source_channel_id,relation_type,ownership_status,ownership_evidence_json,
              owned_for_capture,eligible_for_attachment_evidence,
              mime_type,media_kind,width,height,byte_size,
              content_sha256,local_package_path,capture_status,capture_terminal,
              capture_attempt_count,capture_attempts_json,capture_failure_code,
              capture_failure_detail,extraction_status,extraction_artifacts_json,
              archive_manifest_source_file_id,chart_claim_eligible,raw_json,notes
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                attachment_id,
                message_id,
                int(is_snowflake(raw_attachment_id)),
                filename,
                url,
                url_source_channel_id
                or first_text(attachment.get("thread_channel_id"), attachment.get("channel_id")),
                relation_type,
                ownership_status,
                json_text(ownership_evidence),
                owned_for_capture,
                eligible_for_attachment_evidence,
                mime_type,
                media_kind,
                attachment.get("width") if isinstance(attachment.get("width"), int) else None,
                attachment.get("height") if isinstance(attachment.get("height"), int) else None,
                (
                    attachment.get("byte_size")
                    if isinstance(attachment.get("byte_size"), int)
                    else attachment.get("size")
                    if isinstance(attachment.get("size"), int)
                    else None
                ),
                (
                    first_text(attachment.get("content_sha256"), attachment.get("sha256"))
                    if owned_for_capture
                    else None
                ),
                first_text(attachment.get("local_package_path")) if owned_for_capture else None,
                capture_status,
                capture_terminal,
                capture_attempt_count,
                json_text(capture_attempts),
                first_text(attachment.get("capture_failure_code")) if owned_for_capture else None,
                first_text(attachment.get("capture_failure_detail")) if owned_for_capture else None,
                extraction_status,
                json_text(extraction_artifacts),
                (
                    first_text(attachment.get("archive_manifest_source_file_id"))
                    if owned_for_capture
                    else None
                ),
                0,
                json_text(attachment),
                (
                    "Exact non-owned Discord embed metadata only; no local bytes, archive state, "
                    "extraction, or model evidence is permitted."
                    if ownership_status == "non_owned_exact"
                    else "Unresolved attachment ownership metadata only; no local bytes, archive "
                    "state, extraction, or model evidence is permitted."
                    if ownership_status == "unresolved"
                    else "Discord attachment archive metadata retained. Chart/setup facts remain "
                    "unresolved unless linked to a complete/partial extraction with a verified "
                    "local artifact."
                    if capture_status != "metadata_only"
                    else "Exact-owned Discord attachment metadata only; no setup values inferred."
                ),
            ),
        )
        for extraction in verified_extractions:
            extraction_id = first_text(extraction.get("extraction_id")) or stable_id(
                "attachment-extraction",
                attachment_id,
                extraction.get("method"),
                extraction.get("created_at_utc"),
                extraction.get("local_package_path"),
            )
            con.execute(
                """
                INSERT OR IGNORE INTO attachment_extractions(
                  extraction_id,attachment_id,analysis_run_id,method,status,
                  extracted_text,local_package_path,content_sha256,byte_size,
                  artifact_verified,locator_json,confidence,source_scope,
                  outside_sources_used,created_at_utc
                ) VALUES(?,?,NULL,?,?,?,?,?,?,1,?,?,'discord_only',0,?)
                """,
                (
                    extraction_id,
                    attachment_id,
                    first_text(extraction.get("method")) or "local_attachment_extraction",
                    extraction["status"],
                    first_text(extraction.get("extracted_text")) or "",
                    extraction["local_package_path"],
                    extraction["content_sha256"],
                    extraction["byte_size"],
                    json_text(
                        {
                            "local_package_path": extraction["local_package_path"],
                            "content_sha256": extraction["content_sha256"],
                            "byte_size": extraction["byte_size"],
                            "mime_type": extraction.get("mime_type"),
                            "status": extraction["status"],
                            "local_artifact_verified": True,
                            "failure_code": extraction.get("failure_code"),
                            "failure_detail": extraction.get("failure_detail"),
                        }
                    ),
                    extraction["confidence"],
                    iso_text(extraction.get("created_at_utc")) or utc_now(),
                ),
            )

    links = message.get("links")
    if isinstance(links, list):
        for link in links:
            url = first_text(link.get("url") if isinstance(link, dict) else link)
            if url:
                kind = "discord_attachment" if "cdn.discordapp.com/attachments/" in url else "unknown"
                con.execute(
                    "INSERT OR IGNORE INTO message_links(message_id,url,link_kind) VALUES(?,?,?)",
                    (message_id, url, kind),
                )
    return message_id, channel_id, timestamp


def provenance_sources(message: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("_corpus_provenance", "_merge_provenance"):
        value = message.get(key)
        if isinstance(value, list):
            rows = [row for row in value if isinstance(row, dict)]
            if rows:
                return rows
        if isinstance(value, dict):
            for nested_key in ("sources", "occurrences", "provenance"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    rows = [row for row in nested if isinstance(row, dict)]
                    if rows:
                        return rows
    migration = message.get("_migration_occurrence")
    if isinstance(migration, dict):
        return [migration]
    return []


def insert_occurrence_quarantine_reasons(
    con: sqlite3.Connection,
    run_id: int,
    artifact_id: str | None,
    message_id: str,
    occurrence_id: str,
    reasons: Sequence[str],
    raw: dict[str, Any],
) -> int:
    inserted = 0
    for reason in normalize_reason_values(list(reasons)):
        quarantine_id = stable_id("quarantine", occurrence_id, reason)
        cursor = con.execute(
            """
            INSERT OR IGNORE INTO quarantine_records(
              quarantine_id,run_id,artifact_id,message_id,occurrence_id,
              reason,status,raw_json
            ) VALUES(?,?,?,?,?,?,'quarantined',?)
            """,
            (
                quarantine_id,
                run_id,
                artifact_id,
                message_id,
                occurrence_id,
                reason,
                json_text(raw),
            ),
        )
        inserted += max(cursor.rowcount, 0)
    return inserted


def insert_occurrences(
    con: sqlite3.Connection,
    run_id: int,
    message_id: str,
    collection_name: str,
    message: dict[str, Any],
    document: InputDocument,
) -> None:
    sources = provenance_sources(message)
    if not sources:
        sources = [
            {
                "source_file": portable_input_source(document),
                "collection": collection_name,
                "query": first_text(message.get("search_query")) or "",
                "result_index": message.get("result_index"),
                "page_number": message.get("page_number"),
                "complete_source": None,
            }
        ]
    field_variants = {}
    merge = message.get("_merge_provenance")
    if isinstance(merge, dict) and isinstance(merge.get("field_variants"), dict):
        field_variants = merge["field_variants"]
    if isinstance(message.get("_field_variants"), dict):
        field_variants = message["_field_variants"]
    for source in sources:
        source_file = first_text(source.get("source_file"), source.get("file")) or portable_input_source(document)
        source_collection = first_text(source.get("collection"), source.get("collection_name")) or collection_name
        query = first_text(source.get("query"), source.get("search_query")) or ""
        complete_raw = source.get("complete_source", source.get("complete"))
        complete = None if complete_raw is None else as_bool_int(complete_raw)
        artifact_id = ensure_source_artifact(
            con,
            run_id,
            source_file,
            "discord_collection_source",
            source_collection,
            query,
            document.artifact_id,
            complete,
            source,
        )
        result_index = source.get("result_index", message.get("result_index"))
        page_number = source.get("page_number", message.get("page_number"))
        occurrence_id = stable_id(
            "occurrence",
            message_id,
            artifact_id,
            source_collection,
            query,
            result_index,
            page_number,
        )
        (
            source_kind,
            migration_source,
            quarantined,
            trusted_canonical,
            occurrence_trust_state,
            quarantine_reasons,
        ) = occurrence_trust_fields(source, message)
        con.execute(
            """
            INSERT OR IGNORE INTO message_source_occurrences(
              occurrence_id,message_id,artifact_id,collection_name,query_text,
              result_index,page_number,segment_start_utc,segment_end_utc,
              artifact_declared_complete,source_kind,migration_source,quarantined,
              trusted_canonical,trust_state,quarantine_reasons_json,
              raw_json,field_variants_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                occurrence_id,
                message_id,
                artifact_id,
                source_collection,
                query,
                result_index if isinstance(result_index, int) else None,
                page_number if isinstance(page_number, int) else None,
                iso_text(source.get("segment_start")),
                iso_text(source.get("segment_end")),
                complete,
                source_kind,
                migration_source,
                quarantined,
                trusted_canonical,
                occurrence_trust_state,
                json_text(quarantine_reasons),
                json_text(source),
                json_text(field_variants),
            ),
        )
        if quarantined:
            insert_occurrence_quarantine_reasons(
                con,
                run_id,
                artifact_id,
                message_id,
                occurrence_id,
                quarantine_reasons or ["source_occurrence_quarantined_without_reason"],
                source,
            )


def explicit_occurrence_rows(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    for key in ("occurrences", "message_occurrences", "source_occurrences"):
        value = data.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def insert_explicit_occurrences(
    con: sqlite3.Connection,
    run_id: int,
    document: InputDocument,
    rows: Sequence[dict[str, Any]],
) -> tuple[int, int]:
    inserted = 0
    skipped_missing_message = 0
    for index, row in enumerate(rows):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        provenance = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
        message_id = first_text(
            row.get("message_id"),
            payload.get("message_id"),
            payload.get("id"),
            provenance.get("message_id"),
        )
        if not message_id or not con.execute(
            "SELECT 1 FROM messages WHERE message_id=?", (message_id,)
        ).fetchone():
            skipped_missing_message += 1
            continue
        source_file = first_text(
            row.get("source_file"),
            row.get("source_file_relative_path"),
            provenance.get("source_file"),
            provenance.get("source_file_relative_path"),
            provenance.get("file"),
        ) or portable_input_source(document)
        collection_name = first_text(
            row.get("collection_name"),
            row.get("collection"),
            row.get("source_collection"),
            provenance.get("collection_name"),
            provenance.get("collection"),
            provenance.get("source_collection"),
        ) or "explicit_occurrences"
        query = first_text(
            row.get("query_text"),
            row.get("query"),
            row.get("source_query"),
            provenance.get("query_text"),
            provenance.get("query"),
            provenance.get("source_query"),
        ) or ""
        source_sha256 = first_text(
            row.get("source_file_sha256"),
            provenance.get("source_file_sha256"),
            provenance.get("sha256"),
        )
        complete_raw = row.get(
            "artifact_declared_complete",
            row.get("complete_source", provenance.get("complete_source")),
        )
        complete = None if complete_raw is None else as_bool_int(complete_raw)
        artifact_id = ensure_source_artifact(
            con,
            run_id,
            source_file,
            "explicit_corpus_occurrence",
            collection_name,
            query,
            document.artifact_id,
            complete,
            provenance or row,
            sha256=source_sha256,
        )
        result_index = row.get("result_index", provenance.get("result_index"))
        page_number = row.get("page_number", provenance.get("page_number"))
        occurrence_id = first_text(row.get("occurrence_id"), row.get("id")) or stable_id(
            "occurrence",
            message_id,
            artifact_id,
            collection_name,
            query,
            result_index,
            page_number,
            index,
        )
        field_variants = row.get("field_variants")
        if not isinstance(field_variants, dict):
            field_variants = payload.get("_field_variants")
        if not isinstance(field_variants, dict):
            field_variants = {}
        (
            source_kind,
            migration_source,
            quarantined,
            trusted_canonical,
            occurrence_trust_state,
            quarantine_reasons,
        ) = occurrence_trust_fields(row, payload)
        cursor = con.execute(
            """
            INSERT OR IGNORE INTO message_source_occurrences(
              occurrence_id,message_id,artifact_id,collection_name,query_text,
              result_index,page_number,segment_start_utc,segment_end_utc,
              artifact_declared_complete,source_kind,migration_source,quarantined,
              trusted_canonical,trust_state,quarantine_reasons_json,
              raw_json,field_variants_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                occurrence_id,
                message_id,
                artifact_id,
                collection_name,
                query,
                result_index if isinstance(result_index, int) else None,
                page_number if isinstance(page_number, int) else None,
                iso_text(row.get("segment_start_utc"))
                or iso_text(row.get("segment_start_date"))
                or iso_text(provenance.get("segment_start_utc"))
                or iso_text(provenance.get("segment_start_date"))
                or iso_text(provenance.get("segment_start")),
                iso_text(row.get("segment_end_utc"))
                or iso_text(row.get("segment_end_date"))
                or iso_text(provenance.get("segment_end_utc"))
                or iso_text(provenance.get("segment_end_date"))
                or iso_text(provenance.get("segment_end")),
                complete,
                source_kind,
                migration_source,
                quarantined,
                trusted_canonical,
                occurrence_trust_state,
                json_text(quarantine_reasons),
                json_text(row),
                json_text(field_variants),
            ),
        )
        if cursor.rowcount != 1:
            id_conflict = con.execute(
                """
                SELECT occurrence_id,message_id,artifact_id,collection_name,query_text,
                       result_index,page_number
                FROM message_source_occurrences
                WHERE occurrence_id=?
                """,
                (occurrence_id,),
            ).fetchone()
            logical_conflict = con.execute(
                """
                SELECT occurrence_id,message_id,artifact_id,collection_name,query_text,
                       result_index,page_number
                FROM message_source_occurrences
                WHERE message_id=? AND artifact_id=? AND collection_name=? AND query_text=?
                  AND result_index IS ? AND page_number IS ?
                LIMIT 1
                """,
                (
                    message_id,
                    artifact_id,
                    collection_name,
                    query,
                    result_index if isinstance(result_index, int) else None,
                    page_number if isinstance(page_number, int) else None,
                ),
            ).fetchone()
            conflict = id_conflict or logical_conflict
            conflict_detail = tuple(conflict) if conflict is not None else "constraint violation"
            raise ValueError(
                "Explicit occurrence collision: "
                f"occurrence_id={occurrence_id!r}, message_id={message_id!r}, "
                f"source_file={source_file!r}, collection_name={collection_name!r}, "
                f"query_text={query!r}, result_index={result_index!r}, "
                f"page_number={page_number!r}; existing={conflict_detail!r}"
            )
        inserted += 1
        if quarantined:
            insert_occurrence_quarantine_reasons(
                con,
                run_id,
                artifact_id,
                message_id,
                occurrence_id,
                quarantine_reasons or ["source_occurrence_quarantined_without_reason"],
                row,
            )
    return inserted, skipped_missing_message


def reconcile_message_trust_from_occurrences(con: sqlite3.Connection) -> None:
    """Make occurrence-level trust authoritative for migrated/quarantined rows.

    Direct legacy inputs without occurrence trust metadata retain the historical
    ``trusted_source`` default.  Once an occurrence is explicitly marked as a
    migration or quarantine, however, the message is analysis-ineligible unless
    a separate trusted canonical channel-segment occurrence exists.
    """

    rows = con.execute(
        """
        SELECT m.message_id,m.evidence_trust_state,m.eligible_for_accepted_evidence,
               SUM(CASE WHEN o.trusted_canonical=1 THEN 1 ELSE 0 END),
               SUM(CASE WHEN o.quarantined=1 THEN 1 ELSE 0 END),
               SUM(CASE WHEN o.migration_source=1 THEN 1 ELSE 0 END)
        FROM messages m
        JOIN message_source_occurrences o ON o.message_id=m.message_id
        GROUP BY m.message_id,m.evidence_trust_state,m.eligible_for_accepted_evidence
        """
    ).fetchall()
    for (
        message_id,
        old_state,
        old_eligible,
        trusted_count,
        quarantined_count,
        migration_count,
    ) in rows:
        trusted_count = int(trusted_count or 0)
        quarantined_count = int(quarantined_count or 0)
        migration_count = int(migration_count or 0)
        has_quarantined = int(quarantined_count > 0)
        if old_state == "conflicting":
            state = "conflicting"
            eligible = 0
        elif trusted_count > 0:
            state = (
                "trusted_canonical_recapture"
                if quarantined_count > 0
                or migration_count > 0
                or old_state == "trusted_canonical_recapture"
                else "trusted_source"
            )
            eligible = 1
        elif quarantined_count > 0:
            state = "quarantined_only"
            eligible = 0
        elif migration_count > 0 or old_state == "trusted_canonical_recapture":
            state = "untrusted_noncanonical_only"
            eligible = 0
        else:
            state = old_state
            eligible = int(old_eligible)
        con.execute(
            """
            UPDATE messages
            SET evidence_trust_state=?,eligible_for_accepted_evidence=?,
                has_quarantined_occurrences=?,
                trusted_canonical_occurrence_count=?,quarantined_occurrence_count=?
            WHERE message_id=?
            """,
            (
                state,
                eligible,
                has_quarantined,
                trusted_count,
                quarantined_count,
                message_id,
            ),
        )


def resolve_reply_target_states(con: sqlite3.Connection) -> None:
    con.execute(
        """
        UPDATE messages AS child
        SET reply_target_state='resolved'
        WHERE child.reply_to_message_id IS NOT NULL
          AND EXISTS(
            SELECT 1 FROM messages AS parent
            WHERE parent.message_id=child.reply_to_message_id
          )
        """
    )
    con.execute(
        """
        UPDATE messages
        SET reply_target_state='context_stub'
        WHERE reply_to_message_id IS NOT NULL
          AND reply_target_state='unavailable'
          AND COALESCE(reply_to_content,'')<>''
        """
    )


def ingest_auxiliary_corpus_records(
    con: sqlite3.Connection,
    documents: Sequence[InputDocument],
    run_id: int,
) -> dict[str, int]:
    counts = {"source_segments": 0, "quarantine": 0, "legacy_provenance": 0}
    for document in documents:
        if not isinstance(document.data, dict):
            continue
        segments = document.data.get("segments")
        if isinstance(segments, list):
            for index, row in enumerate(segments):
                if not isinstance(row, dict):
                    continue
                source_file = first_text(
                    row.get("source_file"),
                    row.get("source_file_relative_path"),
                    row.get("file"),
                )
                artifact_id = None
                if source_file:
                    artifact_id = ensure_source_artifact(
                        con,
                        run_id,
                        source_file,
                        "corpus_segment",
                        first_text(row.get("collection_name")) or "",
                        first_text(row.get("query_text")) or "",
                        document.artifact_id,
                        None,
                        row,
                    )
                channel_id = first_text(
                    row.get("channel_id"),
                    row.get("query_container_id"),
                    row.get("container_id"),
                )
                if channel_id and not con.execute(
                    "SELECT 1 FROM channel_inventory WHERE channel_id=?", (channel_id,)
                ).fetchone():
                    channel_id = None
                segment_id = first_text(row.get("segment_id"), row.get("id")) or stable_id(
                    "source-segment", document.artifact_id, index, json_text(row)
                )
                con.execute(
                    """
                    INSERT OR IGNORE INTO source_segments(
                      segment_id,run_id,artifact_id,channel_id,segment_start_utc,
                      segment_end_utc,status,message_count,occurrence_count,raw_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        segment_id,
                        run_id,
                        artifact_id,
                        channel_id,
                        iso_text(row.get("segment_start_utc"))
                        or iso_text(row.get("start"))
                        or iso_text(row.get("start_date")),
                        iso_text(row.get("segment_end_utc"))
                        or iso_text(row.get("end"))
                        or iso_text(row.get("end_date")),
                        first_text(row.get("status")) or "unknown",
                        int(
                            row.get(
                                "message_count",
                                row.get("unique_message_ids_computed", 0),
                            )
                            or 0
                        ),
                        int(
                            row.get(
                                "occurrence_count",
                                row.get("captured_rows_computed", 0),
                            )
                            or 0
                        ),
                        json_text(row),
                    ),
                )
                counts["source_segments"] += 1

        quarantine = document.data.get("quarantine")
        quarantine_rows = list(quarantine) if isinstance(quarantine, list) else []
        if isinstance(quarantine, dict):
            for key in ("occurrences", "records", "items"):
                value = quarantine.get(key)
                if isinstance(value, list):
                    quarantine_rows.extend(value)
        for index, row in enumerate(quarantine_rows):
            if not isinstance(row, dict):
                row = {"value": row}
            message_id = first_text(row.get("message_id"))
            if message_id and not con.execute(
                "SELECT 1 FROM messages WHERE message_id=?", (message_id,)
            ).fetchone():
                message_id = None
            occurrence_id = first_text(row.get("occurrence_id"))
            if occurrence_id and not con.execute(
                "SELECT 1 FROM message_source_occurrences WHERE occurrence_id=?",
                (occurrence_id,),
            ).fetchone():
                occurrence_id = None
            reasons = normalize_reason_values(
                row.get("reasons", row.get("reason", row.get("quarantine_reason")))
            ) or ["Unspecified corpus quarantine record."]
            occurrence_artifact_id = document.artifact_id
            if occurrence_id:
                found = con.execute(
                    "SELECT artifact_id FROM message_source_occurrences WHERE occurrence_id=?",
                    (occurrence_id,),
                ).fetchone()
                if found:
                    occurrence_artifact_id = found[0]
            if occurrence_id and message_id:
                counts["quarantine"] += insert_occurrence_quarantine_reasons(
                    con,
                    run_id,
                    occurrence_artifact_id,
                    message_id,
                    occurrence_id,
                    reasons,
                    row,
                )
            else:
                for reason_index, reason in enumerate(reasons):
                    quarantine_id = first_text(row.get("quarantine_id"), row.get("id"))
                    if not quarantine_id or len(reasons) > 1:
                        quarantine_id = stable_id(
                            "quarantine", document.artifact_id, index, reason_index, reason
                        )
                    cursor = con.execute(
                        """
                        INSERT OR IGNORE INTO quarantine_records(
                          quarantine_id,run_id,artifact_id,message_id,occurrence_id,
                          reason,status,raw_json
                        ) VALUES(?,?,?,?,?,?,?,?)
                        """,
                        (
                            quarantine_id,
                            run_id,
                            occurrence_artifact_id,
                            message_id,
                            occurrence_id,
                            reason,
                            first_text(row.get("status")) or "quarantined",
                            json_text(row),
                        ),
                    )
                    counts["quarantine"] += max(cursor.rowcount, 0)

        legacy = document.data.get("legacy_provenance")
        if legacy is not None:
            rows = legacy if isinstance(legacy, list) else [legacy]
            for index, row in enumerate(rows):
                record_id = stable_id(
                    "legacy-provenance", document.artifact_id, index, json_text(row)
                )
                con.execute(
                    """
                    INSERT OR IGNORE INTO legacy_provenance_records(
                      record_id,run_id,artifact_id,raw_json
                    ) VALUES(?,?,?,?)
                    """,
                    (record_id, run_id, document.artifact_id, json_text(row)),
                )
                counts["legacy_provenance"] += 1
    return counts


def insert_observed_collection_units(
    con: sqlite3.Connection,
    run_id: int,
    window_start: str,
    window_end: str,
) -> None:
    rows = con.execute(
        """
        SELECT o.collection_name,o.query_text,m.channel_id,
               COUNT(*) occurrences,COUNT(DISTINCT o.message_id) unique_messages,
               MIN(m.created_at_utc),MAX(m.created_at_utc),
               MIN(o.artifact_declared_complete),MAX(o.artifact_declared_complete)
        FROM message_source_occurrences o
        JOIN messages m ON m.message_id=o.message_id
        GROUP BY o.collection_name,o.query_text,m.channel_id
        """
    ).fetchall()
    for collection_name, query, channel_id, occurrences, unique_messages, earliest, latest, min_complete, max_complete in rows:
        declared_complete = min_complete if min_complete == max_complete else None
        unit_id = stable_id("collection-unit", run_id, channel_id, collection_name, query, window_start, window_end)
        con.execute(
            """
            INSERT OR IGNORE INTO collection_units(
              unit_id,run_id,channel_id,collection_name,unit_type,window_start_utc,
              window_end_utc,collection_method,query_text,status,
              artifact_declared_complete,occurrences_seen,unique_messages_seen,
              earliest_message_utc,latest_message_utc,gap_notes
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                unit_id,
                run_id,
                channel_id,
                collection_name,
                "observed_message_source",
                window_start,
                window_end,
                "merged_discord_artifact",
                query,
                "unknown",
                declared_complete,
                occurrences,
                unique_messages,
                earliest,
                latest,
                "Observed from source occurrences only; not promoted to channel/window complete without an explicit inventory-backed coverage record.",
            ),
        )


def iter_coverage_records(data: Any) -> Iterator[dict[str, Any]]:
    if not isinstance(data, dict):
        return
    for key in ("coverage_units", "collection_coverage", "coverage"):
        value = data.get(key)
        if isinstance(value, list):
            yield from (row for row in value if isinstance(row, dict))
        elif isinstance(value, dict):
            found = False
            # ``build_corpus.py`` publishes one coverage unit per accessible
            # container in ``coverage.containers``.  Its sibling lists
            # (``segments`` and ``gaps``) are different grains and must not be
            # swept into collection_units by the generic fallback.
            for nested_key in ("units", "containers", "items", "records", "channels"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    found = True
                    yield from (row for row in nested if isinstance(row, dict))
            if not found:
                for nested in value.values():
                    if isinstance(nested, list):
                        yield from (row for row in nested if isinstance(row, dict))


def iter_channel_inventory_records(data: Any) -> Iterator[dict[str, Any]]:
    if not isinstance(data, dict):
        return
    for key in ("channel_inventory", "inventory", "channels", "guild_channels"):
        value = data.get(key)
        if isinstance(value, list):
            yield from (row for row in value if isinstance(row, dict))
        elif isinstance(value, dict):
            # The current canonical merger contract is
            # ``inventory.containers``.  Keep the older aliases for backwards
            # compatibility with the original synthetic fixtures.
            for nested_key in ("containers", "items", "channels", "threads", "units"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    yield from (row for row in nested if isinstance(row, dict))


def ingest_channel_inventory(
    con: sqlite3.Connection,
    documents: Sequence[InputDocument],
    guild_id: str,
) -> int:
    inserted = 0
    for document in documents:
        for row in iter_channel_inventory_records(document.data):
            channel_id = first_text(
                row.get("channel_id"), row.get("container_id"), row.get("id")
            )
            if not channel_id:
                channel_id = stable_id(
                    "channel-inventory-surrogate",
                    guild_id,
                    row.get("parent_channel_id"),
                    row.get("name"),
                    row.get("kind"),
                )
            is_archived = row.get("is_archived")
            if is_archived not in (0, 1, True, False):
                is_archived = None
            is_accessible = row.get("is_accessible", row.get("accessible"))
            if is_accessible not in (0, 1, True, False):
                count_status = first_text(row.get("count_status"))
                is_accessible = 1 if count_status == "ok" else None
            con.execute(
                """
                INSERT INTO channel_inventory(
                  channel_id,guild_id,parent_channel_id,name,kind,exact_id_known,
                  is_archived,is_accessible,inventory_basis,discovered_at_utc,
                  first_seen_utc,last_seen_utc,source_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(channel_id) DO UPDATE SET
                  parent_channel_id=COALESCE(excluded.parent_channel_id,channel_inventory.parent_channel_id),
                  name=COALESCE(excluded.name,channel_inventory.name),
                  kind=CASE WHEN excluded.kind<>'unknown' THEN excluded.kind ELSE channel_inventory.kind END,
                  exact_id_known=MAX(channel_inventory.exact_id_known,excluded.exact_id_known),
                  is_archived=COALESCE(excluded.is_archived,channel_inventory.is_archived),
                  is_accessible=COALESCE(excluded.is_accessible,channel_inventory.is_accessible),
                  inventory_basis='explicit_merger_inventory',
                  source_json=excluded.source_json
                """,
                (
                    channel_id,
                    first_text(row.get("guild_id")) or guild_id,
                    first_text(
                        row.get("parent_channel_id"),
                        row.get("parent_container_id"),
                        row.get("parent_id"),
                    ),
                    first_text(row.get("name"), row.get("channel_name")),
                    first_text(
                        row.get("kind"),
                        row.get("channel_kind"),
                        row.get("container_kind"),
                    )
                    or "unknown",
                    int(is_snowflake(channel_id)),
                    None if is_archived is None else int(bool(is_archived)),
                    None if is_accessible is None else int(bool(is_accessible)),
                    "explicit_merger_inventory",
                    iso_text(row.get("discovered_at_utc")),
                    iso_text(row.get("first_seen_utc")),
                    iso_text(row.get("last_seen_utc")),
                    json_text(row),
                ),
            )
            inserted += 1
    return inserted


def ingest_explicit_coverage(
    con: sqlite3.Connection,
    documents: Sequence[InputDocument],
    run_id: int,
    guild_id: str,
    window_start: str,
    window_end: str,
) -> int:
    inserted = 0
    for document in documents:
        for row in iter_coverage_records(document.data):
            channel_id = first_text(
                row.get("channel_id"), row.get("container_id"), row.get("coverage_container_id")
            )
            if channel_id:
                con.execute(
                    """
                    INSERT INTO channel_inventory(
                      channel_id,guild_id,parent_channel_id,name,kind,exact_id_known,
                      is_archived,is_accessible,inventory_basis,source_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(channel_id) DO UPDATE SET
                      name=COALESCE(excluded.name,channel_inventory.name),
                      kind=CASE WHEN excluded.kind<>'unknown' THEN excluded.kind ELSE channel_inventory.kind END,
                      exact_id_known=MAX(channel_inventory.exact_id_known,excluded.exact_id_known),
                      is_archived=COALESCE(excluded.is_archived,channel_inventory.is_archived),
                      is_accessible=COALESCE(excluded.is_accessible,channel_inventory.is_accessible),
                      inventory_basis='explicit_merger_coverage',
                      source_json=excluded.source_json
                    """,
                    (
                        channel_id,
                        guild_id,
                        first_text(row.get("parent_channel_id"), row.get("parent_container_id")),
                        first_text(row.get("channel_name"), row.get("name")),
                        first_text(row.get("channel_kind"), row.get("kind")) or "unknown",
                        int(is_snowflake(channel_id)),
                        row.get("is_archived") if row.get("is_archived") in (0, 1) else None,
                        (
                            row.get("is_accessible", row.get("accessible"))
                            if row.get("is_accessible", row.get("accessible")) in (0, 1, True, False)
                            else None
                        ),
                        "explicit_merger_inventory",
                        json_text(row),
                    ),
                )
            collection_name = (
                first_text(row.get("collection_name")) or "inventory_coverage"
            )
            query = first_text(
                row.get("query_text"), row.get("query"), row.get("full_window_query")
            ) or ""
            start = iso_text(row.get("window_start_utc"),) or iso_text(row.get("start")) or window_start
            end = iso_text(row.get("window_end_utc")) or iso_text(row.get("end")) or window_end
            status = first_text(row.get("status"))
            if status == "gap":
                status = "partial"
            elif status == "verified_empty":
                status = "complete"
            if status not in {"complete", "partial", "inaccessible", "not_found", "failed", "unknown"}:
                status = "complete" if row.get("scan_complete") is True or row.get("scan_complete") == 1 else "unknown"
            unit_id = first_text(row.get("unit_id")) or stable_id(
                "collection-unit", run_id, channel_id, collection_name, query, start, end
            )
            complete_raw = row.get("artifact_declared_complete", row.get("complete_source"))
            if complete_raw is None and first_text(row.get("status")) in {
                "complete",
                "verified_empty",
            }:
                complete_raw = True
            declared_complete = None if complete_raw is None else as_bool_int(complete_raw)
            con.execute(
                """
                INSERT INTO collection_units(
                  unit_id,run_id,channel_id,collection_name,unit_type,window_start_utc,
                  window_end_utc,collection_method,query_text,status,
                  artifact_declared_complete,occurrences_seen,unique_messages_seen,
                  earliest_message_utc,latest_message_utc,gap_notes
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(unit_id) DO UPDATE SET
                  status=excluded.status,gap_notes=excluded.gap_notes,
                  occurrences_seen=excluded.occurrences_seen,
                  unique_messages_seen=excluded.unique_messages_seen
                """,
                (
                    unit_id,
                    run_id,
                    channel_id,
                    collection_name,
                    first_text(row.get("unit_type")) or "explicit_coverage",
                    start,
                    end,
                    first_text(row.get("collection_method"), row.get("method")) or "merger_declared",
                    query,
                    status,
                    declared_complete,
                    int(row.get("occurrences_seen", row.get("messages_seen", 0)) or 0),
                    int(row.get("unique_messages_seen", row.get("messages_seen", 0)) or 0),
                    iso_text(row.get("earliest_message_utc")),
                    iso_text(row.get("latest_message_utc")),
                    first_text(row.get("gap_notes"), row.get("notes"))
                    or (
                        json_text(
                            {
                                "missing_day_count": row.get("missing_day_count"),
                                "missing_date_ranges": row.get("missing_date_ranges", []),
                                "incomplete_segment_ids": row.get("incomplete_segment_ids", []),
                            }
                        )
                        if row.get("missing_day_count") or row.get("incomplete_segment_ids")
                        else ""
                    ),
                ),
            )
            segments = row.get("segments")
            if isinstance(segments, list):
                for index, segment in enumerate(segments):
                    if not isinstance(segment, dict):
                        continue
                    segment_status = first_text(segment.get("status")) or "unknown"
                    if segment_status not in {"complete", "partial", "inaccessible", "not_found", "failed", "unknown"}:
                        segment_status = "unknown"
                    segment_id = first_text(segment.get("segment_id")) or stable_id(
                        "coverage-segment", unit_id, index, segment.get("start"), segment.get("end")
                    )
                    con.execute(
                        """
                        INSERT OR REPLACE INTO coverage_segments(
                          segment_id,unit_id,segment_start_utc,segment_end_utc,status,
                          returned_count,first_message_id,last_message_id,duplicate_count,
                          error_text,artifact_sha256,notes
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            segment_id,
                            unit_id,
                            iso_text(segment.get("segment_start_utc")) or iso_text(segment.get("start")) or start,
                            iso_text(segment.get("segment_end_utc")) or iso_text(segment.get("end")) or end,
                            segment_status,
                            int(segment.get("returned_count", 0) or 0),
                            first_text(segment.get("first_message_id")),
                            first_text(segment.get("last_message_id")),
                            int(segment.get("duplicate_count", 0) or 0),
                            first_text(segment.get("error_text")),
                            first_text(segment.get("artifact_sha256")),
                            first_text(segment.get("notes")) or "",
                        ),
                    )
            inserted += 1
    return inserted


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_SQL)


def validate_database(con: sqlite3.Connection) -> dict[str, Any]:
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_key_violations = con.execute("PRAGMA foreign_key_check").fetchall()
    message_count = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    fts_count = con.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    attachment_extraction_count = con.execute(
        "SELECT COUNT(*) FROM attachment_extractions"
    ).fetchone()[0]
    attachment_fts_count = con.execute(
        "SELECT COUNT(*) FROM attachment_extractions_fts"
    ).fetchone()[0]
    claim_count = con.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    claims_fts_count = con.execute("SELECT COUNT(*) FROM claims_fts").fetchone()[0]
    audit_count = con.execute("SELECT COUNT(*) FROM v_discord_only_audit").fetchone()[0]
    attachment_archive_state_errors = con.execute(
        """
        SELECT COUNT(*) FROM attachments
        WHERE json_valid(capture_attempts_json)=0
           OR json_type(capture_attempts_json)<>'array'
           OR capture_attempt_count<>json_array_length(capture_attempts_json)
           OR json_valid(extraction_artifacts_json)=0
           OR json_type(extraction_artifacts_json)<>'array'
           OR (capture_status IN ('metadata_only','pending') AND capture_terminal<>0)
           OR (capture_status IN ('downloaded','unavailable','failed') AND capture_terminal<>1)
           OR (capture_status='downloaded' AND (
                 local_package_path IS NULL OR TRIM(local_package_path)=''
                 OR content_sha256 IS NULL OR LENGTH(content_sha256)<>64
                 OR byte_size IS NULL OR byte_size<0
              ))
           OR (capture_status IN ('unavailable','failed') AND (
                 local_package_path IS NOT NULL OR content_sha256 IS NOT NULL
              ))
           OR (extraction_status IN ('complete','partial')
               AND json_array_length(extraction_artifacts_json)=0)
           OR json_valid(ownership_evidence_json)=0
           OR json_type(ownership_evidence_json)<>'object'
           OR (ownership_status='owned_exact' AND (
                owned_for_capture<>1 OR eligible_for_attachment_evidence<>1
                OR relation_type NOT IN ('owned','attachment','message_attachment')
                OR json_extract(ownership_evidence_json,'$.exact')<>1
                OR json_extract(ownership_evidence_json,'$.owner_message_id')<>message_id
                OR json_extract(ownership_evidence_json,'$.owner_channel_id')<>source_channel_id
              ))
           OR (ownership_status='non_owned_exact' AND (
                owned_for_capture<>0 OR eligible_for_attachment_evidence<>0
                OR relation_type NOT IN ('embedded_external','copied_media','non_owned')
                OR json_extract(ownership_evidence_json,'$.exact')<>1
                OR json_extract(ownership_evidence_json,'$.owner_message_id')<>message_id
                OR json_extract(ownership_evidence_json,'$.source_channel_id')<>source_channel_id
                OR COALESCE(TRIM(json_extract(ownership_evidence_json,'$.dom_relation')),'')=''
                OR capture_status<>'metadata_only' OR capture_terminal<>0
                OR capture_attempt_count<>0 OR json_array_length(capture_attempts_json)<>0
                OR capture_failure_code IS NOT NULL OR capture_failure_detail IS NOT NULL
                OR local_package_path IS NOT NULL OR content_sha256 IS NOT NULL
                OR extraction_status<>'not_attempted'
                OR json_array_length(extraction_artifacts_json)<>0
                OR archive_manifest_source_file_id IS NOT NULL
              ))
           OR (ownership_status='unresolved' AND (
                owned_for_capture<>0 OR eligible_for_attachment_evidence<>0
                OR capture_status<>'metadata_only' OR capture_terminal<>0
                OR capture_attempt_count<>0 OR json_array_length(capture_attempts_json)<>0
                OR capture_failure_code IS NOT NULL OR capture_failure_detail IS NOT NULL
                OR local_package_path IS NOT NULL OR content_sha256 IS NOT NULL
                OR extraction_status<>'not_attempted'
                OR json_array_length(extraction_artifacts_json)<>0
                OR archive_manifest_source_file_id IS NOT NULL
              ))
           OR chart_claim_eligible<>0
        """
    ).fetchone()[0]
    attachment_extraction_state_errors = con.execute(
        """
        SELECT COUNT(*) FROM attachment_extractions x
        JOIN attachments a ON a.attachment_id=x.attachment_id
        WHERE a.ownership_status<>'owned_exact'
           OR a.eligible_for_attachment_evidence<>1
           OR x.status NOT IN ('complete','partial')
           OR x.local_package_path NOT LIKE 'attachments/extractions/%'
           OR x.content_sha256 GLOB '*[^0-9a-f]*'
           OR LENGTH(x.content_sha256)<>64
           OR x.byte_size<=0
           OR x.artifact_verified<>1
           OR json_valid(x.locator_json)=0
           OR json_extract(x.locator_json,'$.status')<>x.status
           OR json_extract(x.locator_json,'$.local_package_path')<>x.local_package_path
           OR json_extract(x.locator_json,'$.content_sha256')<>x.content_sha256
           OR json_extract(x.locator_json,'$.byte_size')<>x.byte_size
           OR json_extract(x.locator_json,'$.local_artifact_verified')<>1
           OR (x.confidence IS NOT NULL AND (x.confidence<0 OR x.confidence>1))
        """
    ).fetchone()[0]
    reply_state_errors = con.execute(
        """
        SELECT COUNT(*) FROM messages child
        WHERE (child.reply_to_message_id IS NULL AND child.reply_target_state<>'not_applicable')
           OR (child.reply_to_message_id IS NOT NULL AND child.reply_target_state='not_applicable')
           OR (child.reply_target_state='resolved' AND NOT EXISTS(
                 SELECT 1 FROM messages parent
                 WHERE parent.message_id=child.reply_to_message_id
              ))
           OR (child.reply_to_message_id IS NOT NULL AND EXISTS(
                 SELECT 1 FROM messages parent
                 WHERE parent.message_id=child.reply_to_message_id
              ) AND child.reply_target_state<>'resolved')
        """
    ).fetchone()[0]
    messages_without_occurrence = con.execute(
        """
        SELECT COUNT(*) FROM messages m
        WHERE NOT EXISTS(
          SELECT 1 FROM message_source_occurrences o WHERE o.message_id=m.message_id
        )
        """
    ).fetchone()[0]
    message_trust_state_errors = con.execute(
        """
        SELECT COUNT(*) FROM messages m
        WHERE (m.eligible_for_accepted_evidence=1
               AND m.evidence_trust_state NOT IN ('trusted_canonical_recapture','trusted_source'))
           OR (m.eligible_for_accepted_evidence=0
               AND m.evidence_trust_state IN ('trusted_canonical_recapture','trusted_source'))
           OR (m.evidence_trust_state='trusted_canonical_recapture'
               AND NOT EXISTS(
                 SELECT 1 FROM message_source_occurrences o
                 WHERE o.message_id=m.message_id AND o.trusted_canonical=1
               ))
        """
    ).fetchone()[0]
    eligible_migration_without_recapture = con.execute(
        """
        SELECT COUNT(*) FROM messages m
        WHERE m.eligible_for_accepted_evidence=1
          AND EXISTS(
            SELECT 1 FROM message_source_occurrences o
            WHERE o.message_id=m.message_id
              AND (o.migration_source=1 OR o.quarantined=1)
          )
          AND NOT EXISTS(
            SELECT 1 FROM message_source_occurrences o
            WHERE o.message_id=m.message_id AND o.trusted_canonical=1
          )
        """
    ).fetchone()[0]
    occurrence_trust_state_errors = con.execute(
        """
        SELECT COUNT(*) FROM message_source_occurrences
        WHERE (trusted_canonical=1 AND
               (source_kind<>'channel_segment' OR migration_source<>0 OR quarantined<>0
                OR trust_state<>'trusted_canonical'))
           OR (trust_state='trusted_canonical' AND trusted_canonical<>1)
           OR (trust_state='quarantined_migration'
               AND (quarantined<>1 OR migration_source<>1))
        """
    ).fetchone()[0]
    quarantined_occurrences_without_record = con.execute(
        """
        SELECT COUNT(*) FROM message_source_occurrences o
        WHERE o.quarantined=1
          AND NOT EXISTS(
            SELECT 1 FROM quarantine_records q WHERE q.occurrence_id=o.occurrence_id
          )
        """
    ).fetchone()[0]
    evidence_trust_mismatches = con.execute(
        """
        SELECT COUNT(*)
        FROM evidence_items ev
        LEFT JOIN attachments a ON a.attachment_id=ev.attachment_id
        LEFT JOIN messages m ON m.message_id=COALESCE(ev.message_id,a.message_id)
        WHERE m.message_id IS NULL
           OR ev.evidence_trust_state<>m.evidence_trust_state
           OR ev.eligible_for_accepted_claims<>(
                m.eligible_for_accepted_evidence *
                CASE WHEN ev.attachment_id IS NULL THEN 1
                     ELSE COALESCE(a.eligible_for_attachment_evidence,0) END
              )
           OR (ev.attachment_id IS NOT NULL
               AND COALESCE(a.eligible_for_attachment_evidence,0)<>1)
        """
    ).fetchone()[0]
    accepted_with_untrusted_evidence = con.execute(
        """
        SELECT COUNT(*) FROM claims c
        JOIN claim_evidence ce ON ce.claim_id=c.claim_id
        JOIN evidence_items ev ON ev.evidence_id=ce.evidence_id
        WHERE c.resolution_status='accepted'
          AND ev.eligible_for_accepted_claims=0
        """
    ).fetchone()[0]
    resolved_setups_with_untrusted_message = con.execute(
        """
        SELECT COUNT(*) FROM setup_instances si
        JOIN messages m ON m.message_id=si.primary_message_id
        WHERE si.identity_resolution_status IN ('explicit','linked','derived')
          AND m.eligible_for_accepted_evidence=0
        """
    ).fetchone()[0]
    strict_trades_with_untrusted_message = con.execute(
        """
        SELECT COUNT(*) FROM trade_episodes te
        JOIN setup_instances si ON si.instance_id=te.instance_id
        JOIN messages m ON m.message_id=si.primary_message_id
        WHERE te.strict_comparison_eligible=1
          AND m.eligible_for_accepted_evidence=0
        """
    ).fetchone()[0]
    strict_outcomes_with_untrusted_message = con.execute(
        """
        SELECT COUNT(*) FROM trade_outcome_resolution tor
        JOIN trade_episodes te ON te.trade_id=tor.trade_id
        JOIN setup_instances si ON si.instance_id=te.instance_id
        JOIN messages m ON m.message_id=si.primary_message_id
        WHERE tor.strict_comparison_eligible=1
          AND m.eligible_for_accepted_evidence=0
        """
    ).fetchone()[0]
    views = [
        "v_whole_server_coverage",
        "v_collection_gaps",
        "v_message_trust_lookup",
        "v_analysis_eligible_messages",
        "v_cardinal_setup_evidence",
        "v_cardinal_missing_fields",
        "v_cardinal_setup_cards",
        "v_setup_rule_matrix",
        "v_resolved_trade_outcomes",
        "v_selected_corpus_performance",
        "v_instrument_setup_comparison",
        "v_authority_separated_qa",
        "v_unresolved_qa",
        "v_open_contradictions",
        "v_discord_only_audit",
    ]
    view_errors: list[str] = []
    for view in views:
        try:
            con.execute(f'SELECT * FROM "{view}" LIMIT 1').fetchall()
        except sqlite3.Error as exc:
            view_errors.append(f"{view}: {exc}")
    accepted_without_evidence = con.execute(
        """
        SELECT COUNT(*) FROM claims c
        WHERE c.resolution_status='accepted'
          AND NOT EXISTS(SELECT 1 FROM claim_evidence ce WHERE ce.claim_id=c.claim_id)
        """
    ).fetchone()[0]
    checks = {
        "integrity_ok": integrity == "ok",
        "foreign_keys_ok": len(foreign_key_violations) == 0,
        "fts_parity_ok": message_count == fts_count,
        "attachment_fts_parity_ok": attachment_extraction_count == attachment_fts_count,
        "attachment_archive_states_consistent": attachment_archive_state_errors == 0,
        "attachment_extraction_artifacts_verified": attachment_extraction_state_errors == 0,
        "claims_fts_parity_ok": claim_count == claims_fts_count,
        "reply_target_states_ok": reply_state_errors == 0,
        "message_provenance_complete": messages_without_occurrence == 0,
        "message_trust_states_consistent": message_trust_state_errors == 0,
        "migrated_or_quarantined_messages_require_canonical_recapture": eligible_migration_without_recapture == 0,
        "occurrence_trust_states_consistent": occurrence_trust_state_errors == 0,
        "quarantined_occurrences_have_records": quarantined_occurrences_without_record == 0,
        "evidence_trust_matches_source_message": evidence_trust_mismatches == 0,
        "accepted_claims_use_only_eligible_evidence": accepted_with_untrusted_evidence == 0,
        "resolved_setups_use_only_eligible_messages": resolved_setups_with_untrusted_message == 0,
        "strict_trades_use_only_eligible_messages": strict_trades_with_untrusted_message == 0,
        "strict_outcomes_use_only_eligible_messages": strict_outcomes_with_untrusted_message == 0,
        "discord_only_audit_ok": audit_count == 0,
        "accepted_claim_evidence_ok": accepted_without_evidence == 0,
        "views_queryable": len(view_errors) == 0,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "schema_version": SCHEMA_VERSION,
        "checks": checks,
        "counts": {
            "messages": message_count,
            "analysis_eligible_messages": con.execute(
                "SELECT COUNT(*) FROM v_analysis_eligible_messages"
            ).fetchone()[0],
            "analysis_ineligible_messages": con.execute(
                "SELECT COUNT(*) FROM messages WHERE eligible_for_accepted_evidence=0"
            ).fetchone()[0],
            "messages_fts": fts_count,
            "attachment_extractions": attachment_extraction_count,
            "attachment_extractions_fts": attachment_fts_count,
            "attachment_extraction_state_errors": attachment_extraction_state_errors,
            "downloaded_attachments": con.execute(
                "SELECT COUNT(*) FROM attachments WHERE capture_status='downloaded'"
            ).fetchone()[0],
            "terminal_unavailable_attachments": con.execute(
                "SELECT COUNT(*) FROM attachments WHERE capture_status='unavailable'"
            ).fetchone()[0],
            "terminal_failed_attachments": con.execute(
                "SELECT COUNT(*) FROM attachments WHERE capture_status='failed'"
            ).fetchone()[0],
            "message_source_occurrences": con.execute("SELECT COUNT(*) FROM message_source_occurrences").fetchone()[0],
            "channels": con.execute("SELECT COUNT(*) FROM channel_inventory").fetchone()[0],
            "attachments": con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0],
            "owned_exact_attachments": con.execute(
                "SELECT COUNT(*) FROM attachments WHERE ownership_status='owned_exact'"
            ).fetchone()[0],
            "non_owned_exact_attachments": con.execute(
                "SELECT COUNT(*) FROM attachments WHERE ownership_status='non_owned_exact'"
            ).fetchone()[0],
            "unresolved_ownership_attachments": con.execute(
                "SELECT COUNT(*) FROM attachments WHERE ownership_status='unresolved'"
            ).fetchone()[0],
            "collection_units": con.execute("SELECT COUNT(*) FROM collection_units").fetchone()[0],
            "source_segments": con.execute("SELECT COUNT(*) FROM source_segments").fetchone()[0],
            "quarantine_records": con.execute("SELECT COUNT(*) FROM quarantine_records").fetchone()[0],
            "legacy_provenance_records": con.execute("SELECT COUNT(*) FROM legacy_provenance_records").fetchone()[0],
            "claims": claim_count,
            "claims_fts": claims_fts_count,
            "setup_instances": con.execute("SELECT COUNT(*) FROM setup_instances").fetchone()[0],
            "trade_episodes": con.execute("SELECT COUNT(*) FROM trade_episodes").fetchone()[0],
            "questions": con.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
        },
        "integrity_result": integrity,
        "foreign_key_violation_count": len(foreign_key_violations),
        "discord_only_audit_issue_count": audit_count,
        "attachment_archive_state_error_count": attachment_archive_state_errors,
        "reply_target_state_error_count": reply_state_errors,
        "messages_without_source_occurrence": messages_without_occurrence,
        "message_trust_state_error_count": message_trust_state_errors,
        "eligible_migration_without_recapture_count": eligible_migration_without_recapture,
        "occurrence_trust_state_error_count": occurrence_trust_state_errors,
        "quarantined_occurrences_without_record_count": quarantined_occurrences_without_record,
        "evidence_trust_mismatch_count": evidence_trust_mismatches,
        "accepted_claims_with_untrusted_evidence_count": accepted_with_untrusted_evidence,
        "resolved_setups_with_untrusted_message_count": resolved_setups_with_untrusted_message,
        "strict_trades_with_untrusted_message_count": strict_trades_with_untrusted_message,
        "strict_outcomes_with_untrusted_message_count": strict_outcomes_with_untrusted_message,
        "view_errors": view_errors,
    }


def build_database(
    input_paths: Sequence[Path],
    output_path: Path,
    *,
    window_start: str | None = None,
    window_end: str | None = None,
    replace: bool = False,
    authorized_scope_path: Path | None = None,
) -> dict[str, Any]:
    if not input_paths:
        raise ValueError("At least one JSON input is required.")
    for path in input_paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not replace:
        raise FileExistsError(f"Output already exists: {output_path}. Use --replace explicitly.")
    building_path = output_path.with_suffix(output_path.suffix + ".building")
    if building_path.exists():
        building_path.unlink()

    documents = [load_document(path.resolve()) for path in input_paths]
    validate_input_source_discipline(documents)
    authorized_scope_policy: authorized_collection_scope.AuthorizedScope | None = None
    authorized_container_ids: set[str] = set()
    if authorized_scope_path is not None:
        authorized_scope_policy = authorized_collection_scope.load_validated_scope(
            authorized_scope_path,
            expected_guild_id="1167376964680691732",
            expected_timezone="America/Chicago",
            expected_start_date="2026-01-01",
            expected_end_date="2026-07-20",
        )
        authorized_container_ids = validate_authorized_scope_inputs(
            documents, authorized_scope_policy
        )
    attachment_archive_summaries = [
        document.data.get("attachment_archive")
        for document in documents
        if isinstance(document.data.get("attachment_archive"), dict)
    ]
    attachment_archive = (
        copy.deepcopy(attachment_archive_summaries[0])
        if attachment_archive_summaries
        else {}
    )
    if any(
        json_text(value) != json_text(attachment_archive)
        for value in attachment_archive_summaries[1:]
    ):
        raise ValueError("Input corpus and manifest disagree on attachment_archive")
    collections = [
        (document, name, rows)
        for document in documents
        for name, rows in discover_message_collections(document.data)
    ]
    if not collections:
        raise ValueError("No message arrays were discovered in the supplied JSON inputs.")
    resolved_start, resolved_end = resolve_window(
        documents, collections, window_start, window_end
    )
    guild_id, guild_name = extract_guild(documents)
    built_at = utc_now()
    script_hash = file_sha256(Path(__file__).resolve())

    con = sqlite3.connect(building_path)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("PRAGMA synchronous=FULL")
        create_schema(con)
        collected_values = [
            iso_text(document.metadata.get("collected_at_utc")) for document in documents
        ]
        collected_at = max((v for v in collected_values if v), default=None)
        run_status = "partial"
        con.execute(
            """
            INSERT INTO collection_runs(
              run_id,guild_id,guild_name,window_start_utc,window_end_utc,scope,
              source_scope,outside_sources_used,status,collected_at_utc,built_at_utc,
              methodology,limitations
            ) VALUES(1,?,?,?,?,?,'discord_only',0,?,?,?,?,?)
            """,
            (
                guild_id,
                guild_name,
                resolved_start,
                resolved_end,
                (
                    "user_authorized_three_channel_scope_with_proven_children"
                    if authorized_scope_policy
                    else "entire_guild_requested"
                ),
                run_status,
                collected_at,
                built_at,
                "Lossless Discord JSON ingestion. Raw messages are retained independently from evidence-backed analysis tables. Migrated or quarantined occurrences require an independent trusted canonical recapture before analytical eligibility.",
                "Observed source artifacts are not promoted to whole-channel completeness unless explicit inventory-backed coverage records say so. Cardinal skill content is not inserted as trading evidence. Quarantined-only text remains searchable for provenance lookup but is excluded from accepted claims, resolved setups, and strict trade outcomes.",
            ),
        )
        con.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?)",
            [
                ("schema_version", SCHEMA_VERSION),
                ("source_scope", SOURCE_SCOPE),
                ("outside_sources_used", "0"),
                ("window_start_utc", resolved_start),
                ("window_end_utc", resolved_end),
                ("raw_message_retention", "all_discovered_messages"),
                ("analysis_population_policy", "evidence_backed_discord_only_trusted_messages_no_skill_defaults"),
                (
                    "authorized_collection_scope_enabled",
                    "1" if authorized_scope_policy else "0",
                ),
                (
                    "authorized_collection_scope_sha256",
                    authorized_scope_policy.source_sha256
                    if authorized_scope_policy
                    else "",
                ),
                (
                    "authorized_parent_container_ids_json",
                    json_text(sorted(authorized_scope_policy.parent_ids))
                    if authorized_scope_policy
                    else "[]",
                ),
                (
                    "authorized_container_count_including_proven_children",
                    str(len(authorized_container_ids)),
                ),
                ("accepted_evidence_trust_policy", "trusted_source_or_independent_trusted_canonical_recapture_required"),
                (
                    "attachment_archive_manifest_sha256",
                    first_text(attachment_archive.get("manifest_sha256")) or "",
                ),
                (
                    "attachment_archive_terminal_coverage_complete",
                    "1"
                    if (attachment_archive.get("release_gate") or {}).get(
                        "terminal_coverage_complete"
                    )
                    is True
                    else "0",
                ),
                (
                    "attachment_archive_literal_release_complete",
                    "1"
                    if (attachment_archive.get("release_gate") or {}).get(
                        "literal_release_complete"
                    )
                    is True
                    and int(
                        (attachment_archive.get("counts") or {}).get("failed") or 0
                    )
                    == 0
                    else "0",
                ),
                (
                    "attachment_archive_byte_complete",
                    "1"
                    if (attachment_archive.get("release_gate") or {}).get("byte_complete") is True
                    else "0",
                ),
                (
                    "attachment_archive_counts_json",
                    json_text(attachment_archive.get("counts") or {}),
                ),
                (
                    "attachment_chart_claim_policy",
                    "unresolved_without_exact_linked_verified_complete_or_partial_local_extraction",
                ),
                ("build_timestamp_utc", built_at),
            ],
        )
        con.execute(
            """
            INSERT INTO schema_migrations(
              from_version,to_version,applied_at_utc,script_sha256,row_counts_json
            ) VALUES(NULL,?,?,?,'{}')
            """,
            (SCHEMA_VERSION, built_at, script_hash),
        )

        for document in documents:
            con.execute(
                """
                INSERT INTO source_artifacts(
                  artifact_id,run_id,parent_artifact_id,source_file,sha256,
                  collection_method,collection_name,query_text,captured_at_utc,
                  declared_artifact_complete,descriptor_json
                ) VALUES(?,1,NULL,?,?,?,'','',?,NULL,?)
                """,
                (
                    document.artifact_id,
                    portable_input_source(document),
                    document.sha256,
                    "merged_json_input",
                    iso_text(document.metadata.get("collected_at_utc")),
                    json_text(document.metadata),
                ),
            )

        explicit_inventory_count = ingest_channel_inventory(con, documents, guild_id)
        explicit_occurrences_by_artifact = {
            document.artifact_id: explicit_occurrence_rows(document.data)
            for document in documents
        }
        discovered_occurrences = 0
        for document, collection_name, rows in collections:
            for message in rows:
                message_id, _channel_id, _timestamp = insert_message(
                    con,
                    1,
                    guild_id,
                    collection_name,
                    message,
                    document.artifact_id,
                )
                if not explicit_occurrences_by_artifact[document.artifact_id]:
                    insert_occurrences(
                        con, 1, message_id, collection_name, message, document
                    )
                discovered_occurrences += 1

        explicit_occurrence_inserted = 0
        explicit_occurrence_skipped = 0
        for document in documents:
            inserted, skipped = insert_explicit_occurrences(
                con,
                1,
                document,
                explicit_occurrences_by_artifact[document.artifact_id],
            )
            explicit_occurrence_inserted += inserted
            explicit_occurrence_skipped += skipped

        reconcile_message_trust_from_occurrences(con)
        resolve_reply_target_states(con)
        auxiliary_counts = ingest_auxiliary_corpus_records(con, documents, 1)

        insert_observed_collection_units(con, 1, resolved_start, resolved_end)
        explicit_coverage_count = ingest_explicit_coverage(
            con, documents, 1, guild_id, resolved_start, resolved_end
        )
        if explicit_coverage_count:
            incomplete = con.execute(
                """
                SELECT COUNT(*) FROM collection_units
                WHERE unit_type<>'observed_message_source' AND status<>'complete'
                """
            ).fetchone()[0]
            explicit_units = con.execute(
                "SELECT COUNT(*) FROM collection_units WHERE unit_type<>'observed_message_source'"
            ).fetchone()[0]
            explicit_inventory_channels = con.execute(
                """
                SELECT COUNT(*) FROM channel_inventory
                WHERE inventory_basis LIKE 'explicit_merger_%'
                """
            ).fetchone()[0]
            observed_outside_inventory = con.execute(
                """
                SELECT COUNT(*) FROM channel_inventory
                WHERE inventory_basis NOT LIKE 'explicit_merger_%'
                  AND COALESCE(is_accessible,1)=1
                """
            ).fetchone()[0]
            uncovered_inventory_channels = con.execute(
                """
                SELECT COUNT(*)
                FROM channel_inventory c
                WHERE c.inventory_basis LIKE 'explicit_merger_%'
                  AND LOWER(COALESCE(c.kind,'')) NOT LIKE '%category%'
                  AND LOWER(COALESCE(c.kind,'')) NOT LIKE '%voice%'
                  AND LOWER(COALESCE(c.kind,'')) NOT LIKE '%stage%'
                  AND LOWER(COALESCE(c.kind,'')) NOT LIKE '%directory%'
                  AND (
                    c.is_accessible IS NULL OR
                    (c.is_accessible=1 AND NOT EXISTS(
                      SELECT 1 FROM collection_units u
                      WHERE u.channel_id=c.channel_id
                        AND u.unit_type<>'observed_message_source'
                        AND u.status='complete'
                        AND u.window_start_utc<=?
                        AND u.window_end_utc>=?
                    ))
                  )
                """,
                (resolved_start, resolved_end),
            ).fetchone()[0]
            if (
                explicit_inventory_count > 0
                and explicit_inventory_channels > 0
                and explicit_units > 0
                and incomplete == 0
                and observed_outside_inventory == 0
                and uncovered_inventory_channels == 0
            ):
                con.execute("UPDATE collection_runs SET status='complete' WHERE run_id=1")
        else:
            explicit_units = 0
            explicit_inventory_channels = 0
            observed_outside_inventory = con.execute(
                "SELECT COUNT(*) FROM channel_inventory"
            ).fetchone()[0]
            uncovered_inventory_channels = 0

        con.execute(
            """
            UPDATE schema_migrations
            SET row_counts_json=?
            WHERE migration_id=1
            """,
            (
                json_text(
                    {
                        "discovered_array_occurrences": discovered_occurrences,
                        "messages": con.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                        "source_occurrences": con.execute("SELECT COUNT(*) FROM message_source_occurrences").fetchone()[0],
                        "channels": con.execute("SELECT COUNT(*) FROM channel_inventory").fetchone()[0],
                        "explicit_inventory_records": explicit_inventory_count,
                        "explicit_coverage_records": explicit_coverage_count,
                        "explicit_coverage_units": explicit_units,
                        "explicit_inventory_channels": explicit_inventory_channels,
                        "observed_channels_outside_inventory": observed_outside_inventory,
                        "uncovered_inventory_channels": uncovered_inventory_channels,
                        "explicit_occurrences_inserted": explicit_occurrence_inserted,
                        "explicit_occurrences_skipped_missing_message": explicit_occurrence_skipped,
                        **auxiliary_counts,
                        "attachments": con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0],
                    }
                ),
            ),
        )
        con.commit()
        report = validate_database(con)
        if report["status"] != "passed":
            raise RuntimeError(f"Database validation failed: {json_text(report)}")
        con.commit()
    except Exception:
        con.close()
        if building_path.exists():
            building_path.unlink()
        raise
    else:
        con.close()

    if output_path.exists():
        if not replace:
            building_path.unlink(missing_ok=True)
            raise FileExistsError(output_path)
        output_path.unlink()
    os.replace(building_path, output_path)
    report["database"] = str(output_path)
    report["database_sha256"] = file_sha256(output_path)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build the Discord-only Cardinal-compatible SQLite v2 corpus. "
            "Raw messages are retained; trading fields are never inferred from the Cardinal skill."
        )
    )
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        type=Path,
        help="Merged or legacy Discord JSON input. Repeat for multiple inputs.",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--authorized-scope",
        type=Path,
        default=DEFAULT_AUTHORIZED_SCOPE,
        help=(
            "Require every message-bearing input and channel ID to match the "
            "user-authorized three-channel scope."
        ),
    )
    parser.add_argument("--window-start", help="ISO timestamp or inclusive YYYY-MM-DD start")
    parser.add_argument("--window-end", help="ISO timestamp or inclusive YYYY-MM-DD end")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Explicitly replace an existing output database.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_database(
        args.input,
        args.output,
        window_start=args.window_start,
        window_end=args.window_end,
        replace=args.replace,
        authorized_scope_path=args.authorized_scope,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
