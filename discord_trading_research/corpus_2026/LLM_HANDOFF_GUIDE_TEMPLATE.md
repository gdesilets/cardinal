# Cardinal / LLM handoff guide — Discord-only trading research

This is a reusable handoff template for the Jan 1–Jul 20, 2026 collection. Replace
the bracketed values only after the final release checks pass. Do not paste guessed
counts or paths into this file.

## Release fields

- Merged corpus JSON: `{{MERGED_CORPUS_JSON_PATH}}`
- Coverage manifest JSON: `{{COVERAGE_MANIFEST_JSON_PATH}}`
- Pristine Cardinal v2 SQLite database: `{{PRISTINE_DATABASE_PATH}}`
- Full analyzed SQLite database: `{{FULL_DATABASE_PATH}}`
- Compact LLM SQLite companion: `{{COMPACT_DATABASE_PATH}}`
- Analysis report: `{{ANALYSIS_REPORT_PATH}}`
- Corpus QA report: `{{QA_REPORT_PATH}}`
- Compact-build report: `{{COMPACT_REPORT_PATH}}`
- Full database SHA-256: `{{FULL_DATABASE_SHA256}}`
- Compact database SHA-256: `{{COMPACT_DATABASE_SHA256}}`
- Release status: `{{RELEASE_STATUS}}`

Paths in a finalized guide are relative to the release-package root. A value
beginning with `NOT_PACKAGED` is an intentional label, not a usable path. The
packager omits the build-only merged corpus, pristine database, analysis-build
report, and compact-build report; the label names the packaged authority that
replaces each one. The deterministic release-binding section at the end retains
the build-only artifact filename and SHA-256 for audit without claiming that the
file is present in the package.

The intended local-date window is January 1 through July 20, 2026 in
America/Chicago. Read the exact inclusive/exclusive UTC boundaries and actual
collection status from `meta`/`source_meta` and `collection_runs`; do not infer them
from filenames. A database with gaps or a partial collection status must be
described as conditional on the captured corpus.

## Instructions for the answering LLM

Use only evidence stored in these SQLite files. Do not add web knowledge, generic
ICT teaching, live or historical market data, economic-calendar facts, or chart
interpretations that are absent from the Discord evidence.

Cardinal may organize the evidence into setup, context, confirmation, invalidation,
and outcome fields. It must not supply missing definitions, bias, levels, setup
rules, instruments, times, or outcomes from its own market knowledge.

For every substantive answer:

1. Check collection status and gaps.
2. Classify the support as explicit Discord wording, linked context, curated
   synthesis, observed association, or insufficient evidence.
3. Prefer accepted/qualified claims backed by trust-eligible evidence.
4. Cite message IDs and Discord permalinks. Use exact excerpts where useful.
5. Preserve disagreement, unresolved questions, missing fields, and sample-size
   limits.
6. Keep executed instruments separate from market-context instruments.
7. Use only strict eligible win/loss episodes for comparative outcome summaries.
8. Call rates and shares *descriptive selected-corpus associations*. Never present
   them as calibrated probability, causal lift, expectancy, or a forecast.
9. Treat Discord posting time as posting time. Use explicit setup time markers or
   session statements when answering when a setup appeared.
10. If the database cannot answer a question, say so instead of filling the gap
    from outside knowledge.

URLs captured inside Discord messages, embeds, or attachments are retained only as
Discord provenance. Do not open them or treat the destination content as evidence.
The release package may contain locally mirrored bytes for Discord-owned
attachments only; it never follows arbitrary external links. Read
`attachments.ownership_status`, `relation_type`, `ownership_evidence_json`,
`eligible_for_attachment_evidence`, `local_package_path`, `capture_status`,
`content_sha256`, and `extraction_status` before using media. A
`non_owned_exact` row is visible Discord metadata only: it must have no local bytes,
extraction, or evidence linkage and must never support a model claim. `unavailable` is a substantiated terminal
media gap; `failed` is a degraded audit record that blocks literal release. Unless a
complete/partial extraction with verified local path/hash/size is explicitly present
and linked, filename/URL/alt-text metadata and archived bytes alone cannot support a
chart-geometry claim.

Useful answer language:

- “The captured Discord evidence explicitly states …”
- “The analysis layer links this as context, not as an explicit rule …”
- “In the strict selected-corpus subset …”
- “This is a descriptive, overlapping association rather than a forward
  probability …”
- “The captured evidence is insufficient to resolve …”

Avoid language such as “true win rate,” “causes better performance,” “guaranteed,”
or “works best” unless the answer immediately explains that the corpus cannot
establish that claim.

## Which database to use

The full analyzed database is authoritative. It retains exact raw payloads,
source artifacts, occurrence variants, coverage, quarantine records, normalized
analysis, triggers, and audit views.

The compact companion is the recommended first file for an LLM. It retains all
message text, trust flags, concise occurrence provenance, evidence, claims, Q&A,
setups, trades, model rules, rollups, and materialized query tables. It deliberately
omits bulky `raw_json` payloads. Use the full database whenever a provenance dispute,
field variant, source artifact, or raw attachment/embed payload matters.

In the compact database, `llm_manifest` records the authoritative source database
hash. `llm_data_dictionary` describes the principal query objects. The compact
file is a convenience snapshot, not a replacement authority.

When the release contains Discord media, the exact archive ledger is
`manifests/discord_attachment_archive_manifest.json` and verified files live at
their recorded `attachments/...` package paths. Terminal `unavailable` rows
intentionally have no local byte path and must be reported as media gaps. Terminal
`failed` rows remain in degraded working artifacts but cannot appear in a complete
literal release package.

## Evidence layers and authority

### Raw/source layer

- `messages` — canonical Discord message text, author/channel/thread, reply fields,
  permalink, and trust state. In the compact file this omits only bulky raw JSON.
- `message_source_occurrences` — every retained source occurrence, query/page
  location, migration status, and quarantine reason summary.
- `attachments`, `message_embeds`, `message_links`, `message_mentions`,
  `message_reactions`, `message_relations`, `message_versions` — captured Discord
  context. `attachments` explicitly distinguishes `owned_exact`,
  `non_owned_exact`, and unresolved ownership. Only `owned_exact` rows may include
  package-relative local paths, content hashes,
  byte/MIME metadata, browser-attempt history, terminal failure details, and local
  extraction status. A terminal failed attachment is a degraded audit record that
  blocks literal release. An attachment is not proof of a chart fact unless an
  evidence record explicitly extracts and supports it.
- `attachment_extractions` — local OCR/manual extraction provenance for exact
  attachment IDs. Only complete/partial rows with verified local path/hash/size are
  queryable evidence. Missing confidence remains `NULL`, never an assumed `1.0`;
  failed/no-artifact attempts remain in attachment JSON and never resolve a chart
  claim.
- `collection_runs`, `collection_units`, `coverage_segments`,
  `query_server_coverage`, `query_collection_gaps` — capture scope and gaps.

Raw messages may include chatter, questions, examples, uncertainty, mistakes, and
conflicting opinions. A matching keyword is not automatically a trading rule.

### Trust and quarantine layer

- `messages.evidence_trust_state` and
  `messages.eligible_for_accepted_evidence` are the message-level analytical gate.
- `v_analysis_eligible_messages` contains only `trusted_source` and independently
  trusted canonical recaptures that are eligible for accepted evidence.
- `v_quarantined_messages` exists in the compact file for audit-only retrieval.
- `quarantine_records` and `message_source_occurrences` explain why an occurrence
  was quarantined.
- `evidence_items.eligible_for_accepted_claims` repeats the evidence-level gate.

Quarantined-only text remains searchable so provenance is not hidden. It must not
support an accepted claim, resolved setup identity, or strict trade outcome unless
the same message was independently recaptured by a trusted canonical collection.

### Curated analysis layer

- `analysis_documents` — high-level JSON summaries. Start with
  `discord_rejection_block_research`, `discord_qa_catalog_summary`,
  `discord_contradiction_catalog_summary`, `discord_trade_profiles`,
  `discord_model_cards`,
  `discord_analysis_coverage`, and `discord_analysis_methodology` when present.
- `claims`, `claim_evidence`, `evidence_items` — normalized claims and their exact
  message-level support.
- `claims.claim_kind` and `claims.epistemic_status` distinguish explicit source
  wording, linked context, curated synthesis, and observed association.
- `confidence_assessments.dimension` describes extraction, linkage,
  normalization, resolution, or corpus support. It is not a trade-win probability.
- `authority_assignments` records captured speaker authority separately from reply
  linkage. A direct reply establishes a link, not correctness or mentor authority.
- `contradiction_sets`, `contradiction_members`, and
  `query_open_contradictions` preserve unresolved disagreements.
- `relevance_annotations` records whether an eligible message was selected by
  the current evidence passes or merely retained; `raw_retained_not_curated`
  never means unimportant.
- `data_dictionary` documents every non-technical user-table column, including
  null semantics and whether it is source or derived.

For exact support, join `claims → claim_evidence → evidence_items → messages` and
return `message_id`, `exact_excerpt`, and `permalink`.

## Domain-specific query map

### Rejection blocks

- Compact: `query_rejection_blocks`
- Full: `analysis_entities` filtered to `rejection_block_observation` or
  `rejection_block_finding`, then joined
  to `claims`, `claim_evidence`, `evidence_items`, and `messages`
- Summary document: `discord_rejection_block_research`

Keep these facets separate:

- `identification`
- `invalidation_or_non_actionability`
- `timing`
- `confluence`

Do not silently turn “not actionable yet,” “needs confirmation,” or a chart-specific
example into a universal invalidation rule.

The full-window analyzer overrides the protected legacy timing pattern that could
mistake a bare quantity such as “10 points” for 10AM. A 10AM timing observation
requires an explicit clock/open token; posting time still remains provenance only.

### Questions and answers

- Compact: `query_qa` includes answered and unanswered questions, message-chain
  JSON, direct-reply state, linkage confidence, and authority class.
- Full: `questions`, `answers`, `question_messages`, `answer_messages`, and
  `question_answer_links`; `v_authority_separated_qa` is a convenient answered
  subset and `v_unresolved_qa` lists unresolved items.

Preserve the recorded statuses. A `community_only` answer is direct-reply context,
not proof of responsiveness or correctness, and its corpus-wide question remains
`partial`. Only a curated answer status supports calling the question `answered`.
“Unanswered in this capture” does not prove that no answer ever existed elsewhere
in the server.

### Trades, wins, and losses

- Compact: `query_trade_episodes` includes all extracted episodes;
  `v_strict_trade_episodes` contains the strict win/loss denominator.
- Full: `trade_episodes`, `trade_outcome_claims`,
  `trade_outcome_resolution`, and `v_resolved_trade_outcomes`.
- Summary document: `discord_trade_profiles`.

The strict comparison requires one attributable executed-trade episode, an explicit
win or loss, at least one confluence, and no shared multi-trade attribution.
Breakeven, mixed, cancelled, open, unknown, paper, aggregate, and otherwise
unresolved episodes may remain in the database but do not belong in the strict
win/loss denominator.

### Confluence catalog

- Compact: `query_confluence_profiles`
- Full: `claims` where `facet='selected_corpus_outcome_association'`
- Detailed features: `setup_features` joined to `concept_terms`

Confluences overlap within trades and model memberships overlap. Counts must not be
added as though the groups were disjoint. Rank only as higher/lower *observed share
within the strict selected corpus*, always show wins, losses, eligible `n`, and the
limitations, and flag small samples. This database does not establish “high
probability” in the statistical or forward-looking sense.

Canonical marginals count a family at most once per episode even when multiple
timeframe/role tags occur. For combinations and additional dimensions, inspect
`discord_trade_profiles.strict_slice_profiles`: it contains exact canonical
confluence sets plus explicit executed-instrument, direction, session, setup-time,
and stored-model slices with evidence IDs and missing-dimension counts.

### Models

- Compact: `query_models`, `setup_model_rules`, and
  `query_model_rule_matrix`
- Full: `setup_models`, `setup_model_rules`, `setup_model_matches`,
  `setup_rule_states`, and `v_setup_rule_matrix`
- Summary document: `discord_model_cards`

At most five Discord-supported models may appear, and no fifth model is created just
to fill a slot. Report each model’s required rules, exclusions/invalidation,
evidence status, limitations, matched/missing/violated rule counts, and strict
descriptive outcome subset. Do not add model memberships together.

Current model membership is signature-derived. An assigned model does not prove
that its operational rules matched. Unless a rule was explicitly evaluated,
`setup_rule_states.state` is `unknown`, matched count is zero, and the rule is
included in missing count. `fully_matched_instances` requires a complete satisfying
rule-state matrix; never infer it from `match_status='derived'`.

### NQ versus ES

Use `setup_instruments.role` as the hard boundary:

- `executed` — explicit execution wording was attributed to that instrument.
- `market_context` — the symbol was intermarket/SMT/context evidence only.

Only `executed` rows can enter an NQ-versus-ES execution comparison. ES mentioned as
SMT context for an NQ execution is not an ES trade. A descriptive difference in this
captured sample does not prove rejection blocks objectively work better on one
instrument. Report both denominators and say “insufficient” when the sample cannot
support a stable comparison.

## Practical SQL patterns

The examples below use the compact companion where possible. They also work against
the full database for shared normalized tables.

### 1. Verify source scope, window, and gaps

```sql
SELECT key, value
FROM source_meta
WHERE key IN (
  'schema_version','source_scope','outside_sources_used',
  'window_start_utc','window_end_utc'
)
ORDER BY key;

SELECT run_id, window_start_utc, window_end_utc, status, scope, limitations
FROM collection_runs
ORDER BY run_id;

SELECT COUNT(*) AS collection_gap_rows FROM query_collection_gaps;
SELECT COUNT(*) AS discord_only_audit_issues FROM v_discord_only_audit;
```

In the full database use `meta` instead of `source_meta`.

### 2. Search trusted Discord text

```sql
SELECT m.message_id, m.created_at_utc, m.channel_name, m.thread_title,
       m.author_display_name, m.content_text, m.permalink,
       m.evidence_trust_state
FROM messages_fts
JOIN messages AS m USING (message_id)
WHERE messages_fts MATCH '"rejection block"'
  AND m.eligible_for_accepted_evidence = 1
ORDER BY m.created_at_utc;
```

Remove the eligibility filter only for an explicit quarantine/provenance audit, and
label those results untrusted.

### 3. Retrieve RB identification and invalidation findings

```sql
SELECT facet, claim_kind, epistemic_status, resolution_status,
       claim_text, limitations, evidence_json
FROM query_rejection_blocks
WHERE facet IN ('identification','invalidation_or_non_actionability')
ORDER BY facet, claim_kind, claim_id;
```

### 4. Retrieve RB timing without confusing posting time for setup time

```sql
SELECT facet, claim_kind, claim_text, normalized_value_json,
       limitations, evidence_json
FROM query_rejection_blocks
WHERE facet = 'timing'
ORDER BY claim_kind, claim_id;

SELECT stm.stated_time_text, stm.timezone_as_stated, stm.marker_type, stm.role,
       COUNT(DISTINCT stm.instance_id) AS setup_instances
FROM setup_time_markers AS stm
JOIN setup_features AS sf ON sf.instance_id = stm.instance_id
JOIN concept_terms AS ct ON ct.term_id = sf.term_id
WHERE ct.canonical_name = 'rejection_block'
GROUP BY stm.stated_time_text, stm.timezone_as_stated, stm.marker_type, stm.role
ORDER BY setup_instances DESC;
```

### 5. List Q&A with authority and exact message chains

```sql
SELECT question_id, normalized_question, topic, subtopic, question_status,
       answer_summary, answer_status, direct_reply, linkage_confidence,
       authority_class, authority_basis,
       question_messages_json, answer_messages_json
FROM query_qa
WHERE topic LIKE '%rejection%' OR normalized_question LIKE '%rejection%'
ORDER BY question_status, question_id;
```

### 6. Audit a claim back to Discord

```sql
SELECT c.claim_id, c.facet, c.claim_kind, c.epistemic_status,
       c.resolution_status, c.claim_text,
       ce.evidence_role, e.evidence_id, e.exact_excerpt,
       e.evidence_trust_state, e.eligible_for_accepted_claims,
       m.message_id, m.author_display_name, m.created_at_utc,
       m.channel_name, m.thread_title, m.permalink
FROM claims AS c
JOIN claim_evidence AS ce ON ce.claim_id = c.claim_id
JOIN evidence_items AS e ON e.evidence_id = ce.evidence_id
LEFT JOIN messages AS m ON m.message_id = e.message_id
WHERE c.claim_id = :claim_id
ORDER BY e.message_id, e.evidence_id;
```

### 7. Build strict win and loss profiles

```sql
SELECT resolved_outcome, COUNT(*) AS strict_episodes
FROM v_strict_trade_episodes
GROUP BY resolved_outcome
ORDER BY resolved_outcome;

SELECT confluence, wins, losses, eligible_count,
       descriptive_selected_corpus_win_share,
       difference_from_selected_corpus_baseline,
       limitations
FROM query_confluence_profiles
ORDER BY difference_from_selected_corpus_baseline DESC, eligible_count DESC;
```

Always report `wins`, `losses`, and `eligible_count` beside any share. Do not hide
low denominators.

### 8. Compare strict RB episodes on NQ and ES using executed roles only

```sql
SELECT i.canonical_symbol,
       COUNT(DISTINCT t.trade_id) AS strict_rb_episodes,
       COUNT(DISTINCT CASE WHEN r.resolved_outcome='win' THEN t.trade_id END) AS wins,
       COUNT(DISTINCT CASE WHEN r.resolved_outcome='loss' THEN t.trade_id END) AS losses
FROM trade_episodes AS t
JOIN trade_outcome_resolution AS r ON r.trade_id = t.trade_id
JOIN setup_instruments AS si
  ON si.instance_id = t.instance_id AND si.role = 'executed'
JOIN instruments AS i ON i.instrument_id = si.instrument_id
WHERE r.strict_comparison_eligible = 1
  AND r.resolved_outcome IN ('win','loss')
  AND i.canonical_symbol IN ('NQ','ES')
  AND EXISTS (
    SELECT 1
    FROM setup_features AS sf
    JOIN concept_terms AS ct ON ct.term_id = sf.term_id
    WHERE sf.instance_id = t.instance_id
      AND ct.canonical_name = 'rejection_block'
  )
GROUP BY i.canonical_symbol
ORDER BY i.canonical_symbol;
```

If a contract alias appears, inspect `instrument_aliases` rather than folding it
into NQ or ES without a Discord-backed normalization.

### 9. Inspect model rules and observed rule states

```sql
SELECT model_id, canonical_name, thesis, evidence_status,
       lifecycle_status, limitations, rules_json,
       matched_instance_rows, fully_matched_instances
FROM query_models
ORDER BY model_id;

SELECT instance_id, model_id, canonical_name, rule_order, rule_type,
       rule_text, required_state, observed_state, state_claim_id
FROM query_model_rule_matrix
WHERE model_id = :model_id
ORDER BY instance_id, rule_order;
```

### 10. Read the curated JSON documents

```sql
SELECT document_name, created_by, content_json, notes
FROM analysis_documents
WHERE document_name IN (
  'discord_rejection_block_research',
  'discord_qa_catalog_summary',
  'discord_contradiction_catalog_summary',
  'discord_trade_profiles',
  'discord_model_cards',
  'discord_analysis_coverage',
  'discord_analysis_methodology'
)
ORDER BY document_name;
```

Use these documents to orient the answer, then use normalized evidence tables for
quotes, message IDs, permalinks, denominators, and trust checks.

## Canonical release build order

The commands in this section are a provenance blueprint, not a promise that all
build-stage inputs are shipped. If a substituted argument begins with
`NOT_PACKAGED`, the command is intentionally non-executable from the package;
use `FINAL_PIPELINE_RUNBOOK.md` in the build workspace to reproduce the release.

Build the pristine Cardinal v2 database with **both** the merged corpus JSON and the
coverage manifest. They are separate repeated `--input` values. The merged corpus
supplies message/occurrence evidence; the manifest supplies the authoritative
inventory and coverage containers. Omitting the manifest can produce a database
whose message text exists but whose whole-server coverage provenance is incomplete.

```powershell
python .\discord_trading_research\corpus_2026-01-01_2026-07-20\build_cardinal_database_v2.py `
  --input "{{MERGED_CORPUS_JSON_PATH}}" `
  --input "{{COVERAGE_MANIFEST_JSON_PATH}}" `
  --output "{{PRISTINE_DATABASE_PATH}}" `
  --window-start "2026-01-01T06:00:00Z" `
  --window-end "2026-07-21T05:00:00Z"
```

The UTC interval above is half-open and corresponds to the requested inclusive
America/Chicago local-date window. Verify that `collection_runs`,
`query_server_coverage`/`v_whole_server_coverage`, and the QA report preserve both
the inventory and coverage containers before release.

Then create a new analyzed database; do not analyze in place:

```powershell
python .\discord_trading_research\corpus_2026-01-01_2026-07-20\build_discord_analysis_layer.py `
  --database "{{PRISTINE_DATABASE_PATH}}" `
  --output "{{FULL_DATABASE_PATH}}" `
  --report "{{ANALYSIS_REPORT_PATH}}"
```

Only after the analyzed database and corpus QA report pass should the optional
compact handoff be built.

## Build the optional compact companion

Run only after the analyzed database and its QA report are ready:

```powershell
python .\discord_trading_research\corpus_2026-01-01_2026-07-20\build_llm_companion.py `
  --database "{{FULL_DATABASE_PATH}}" `
  --output "{{COMPACT_DATABASE_PATH}}" `
  --report "{{COMPACT_REPORT_PATH}}"
```

The builder opens the source for validation, copies only a whitelist of useful
fields/tables, checks the source hash before and after, refuses a non-Discord or
failed-audit source, validates the output, and writes the new file atomically. It
refuses to overwrite an existing output unless `--replace` is explicit.

## Minimum answer checklist

Before returning an answer, verify:

- The collection status/gaps were stated when relevant.
- No source outside this Discord database was used.
- Explicit wording and synthesis were not blended.
- Every material claim can be traced to an eligible evidence row and message.
- Question linkage and speaker authority were not conflated.
- Only strict episodes were used for win/loss comparisons.
- Executed and market-context instruments stayed separate.
- All shares were labeled descriptive, overlapping, and non-causal.
- Small or unbalanced samples were disclosed.
- Missing, unresolved, or contradictory evidence stayed unresolved.
