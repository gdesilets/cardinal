# Three-month Discord trading research database

This workflow builds `discord_trading_research_3month.sqlite` as a separate, Discord-only research artifact. It does not modify or replace the original 14-day `discord_trading_research.sqlite` database.

The production scope is the exact half-open UTC interval `2026-04-20T00:00:00Z` through `2026-07-21T00:00:00Z`: April 20 through July 20, 2026, inclusive, for 92 calendar days. The builder rejects any other window.

## Required source artifacts

- `raw_discord_export_3month.json`: the Discord-only merged export from `merge_three_month_sources.py`.
- `browser_context_followups_3month.json`: an authenticated Discord browser audit of 35 visible messages in eight selected permalink contexts.
- `curated_analysis_3month.json`: findings, Q&A, trade episodes, profiles, probability tiers, models, research answers, and contradictions for the same corpus.

The merged export contains seven top-level arrays:

- `primary_messages`
- `server_rejection_phrase_messages`
- `questions_rb_messages`
- `questions_nq_es_messages`
- `broad_rb_shorthand_partial_messages`
- `contextual_qa_messages`
- `instrument_comparison_messages`

The browser audit becomes an eighth database coverage/source collection named `browser_context_followup_messages`. It is complete only for the eight selected permalink contexts, not for the containing channels.

Supporting analyses are embedded when present:

- `rb_analysis_3month.json`
- `trade_analysis_3month.json`
- `model_analysis_3month.json`
- `RESEARCH_SUMMARY_3MONTH.md`
- `README_FOR_LLM_3MONTH.md`
- `README_3MONTH_DATABASE.md`

## Production coverage

- Primary history: 39 of 39 two-day files complete for April 20 through July 6, plus the complete July 7-20 baseline tail.
- Supplemental older-window searches: 15 validated files and 2,401 source occurrences.
- Exact rejection-block phrase and questions-channel RB coverage are complete for the requested interval.
- Older-window instrument searches are complete through July 6. Equivalent `RB NQ` and `RB ES` searches were not captured for July 7-20, so `instrument_comparison_messages` remains partial and the gap is explicit.
- `questions_nq_es_messages` preserves the July 7-20 baseline search separately; the older `questions_nq_es` search is classified under `instrument_comparison_messages`.
- The broad server-wide RB shorthand search is partial in the latest-period baseline.
- Contextual Q&A was not expanded channel-wide beyond the baseline capture.
- The browser follow-up is complete for its eight selected contexts only and is deliberately marked `scan_complete=0` at channel-coverage level.

The overall research-run status is therefore `partial` even when validation passes. A passing validator means the database accurately preserves and discloses these boundaries; it does not claim every Discord channel/search family was exhaustively exported.

## Why the database has fewer messages than the raw union

The merged export has 15,202 unique IDs. The browser audit adds 24 new IDs and overlaps 11 existing IDs, producing a raw-plus-browser union of 15,226 unique messages.

The production `messages` table has 7,170 rows by design. It force-includes every curated/evidence-referenced message and all 35 browser-follow-up messages, then retains other messages that pass deterministic trading-relevance rules. Unimportant chatter and low-signal rows are omitted, matching the requested scope.

This filtering does not rewrite coverage counts:

- `collection_coverage.messages_seen` retains raw collection-array counts before relevance filtering.
- `raw_discord_export_3month.json` remains available beside the database for the complete merged export.
- `raw_merge_metadata_3month` in `analysis_documents` embeds the merge inventory and coverage metadata.
- `messages.source_json`, `message_sources`, and `merged_message_provenance` retain raw/source/query provenance for every stored message.
- `exclusion_stats` records omitted-message counts by source collection and deterministic exclusion reason.

## Build and validate

From the `discord_trading_research` directory:

```powershell
python .\merge_three_month_sources.py --dry-run
python .\merge_three_month_sources.py
python .\build_database_3month.py
python .\validate_database_3month.py
```

Outputs:

- `discord_trading_research_3month.sqlite`
- `validation_report_3month.json`

The builder uses an atomic temporary file, requires the audited browser artifact, and refuses to use or overwrite the 14-day raw, curated, or database paths.

Paths can be overridden, but the window cannot:

```powershell
python .\build_database_3month.py `
  --raw C:\path\to\raw_discord_export_3month.json `
  --browser-context-followups C:\path\to\browser_context_followups_3month.json `
  --curated C:\path\to\curated_analysis_3month.json `
  --output C:\path\to\discord_trading_research_3month.sqlite `
  --window-start 2026-04-20T00:00:00Z `
  --window-end 2026-07-21T00:00:00Z

python .\validate_database_3month.py `
  C:\path\to\discord_trading_research_3month.sqlite `
  --report C:\path\to\validation_report_3month.json
```

## Tables and views for an LLM

Start with these relational tables:

- `research_questions`: concise answers to the requested research questions, with evidence-message ID JSON.
- `rejection_block_findings` and `rejection_block_finding_evidence`: identification, invalidation, timing, probability, instrument, execution, and risk findings.
- `trades`, `trade_evidence`, and `trade_confluences`: curated episodes and their attributable evidence.
- `outcome_profiles` and `outcome_profile_confluences`: separate win/loss profiles.
- `probability_tiers`: descriptive corpus tiers, not universal probabilities.
- `trading_models`, `model_rules`, and `model_evidence`: four evidence-backed model variants and their rules.
- `qa_pairs`: direct and curated question/answer links, including unresolved cases.
- `messages` and `messages_fts`: stored Discord text and full-text search.
- `message_sources` and `merged_message_provenance`: collection, query, file, and merge-source lineage.

Browser-audit tables preserve the targeted follow-up boundary:

- `browser_context_followup_artifacts`: source, window, outside-source flag, selection method, answer-linkage policy, authority caution, and completeness boundary.
- `browser_followup_contexts`: all eight target IDs, exact statuses, and audited resolutions.
- `browser_followup_context_messages`: all 35 visible messages, context source URL, exact captured author, `domme`/`non_domme` author marker, and target-message flag.
- `v_browser_context_followups`: joined context, message, permalink, status, authority, linkage, and completeness fields.

Other useful views include `v_rejection_block_evidence`, `v_answered_qa`, `v_trade_feature_matrix`, `v_win_loss_confluence_comparison`, `v_model_cards`, and `v_llm_research_answers`.

## Authority and interpretation rules

Use only the supplied Discord corpus. Do not add web knowledge, generic ICT teaching, chart guesses, or external market data.

- Preserve evidence labels: `explicit`, `observed_association`, `derived`, and `insufficient_evidence` are not interchangeable.
- Preserve browser context statuses: `answered`, `partially_answered`, `community_answer_only`, and `unresolved`.
- `authority_class='domme'` is a deterministic author marker, not a guarantee that every statement is universal. `non_domme` includes community members and questioners; it must not be promoted to mentor authority.
- The embedded curated artifact retains the more specific browser evidence authority labels, including mentor direct reply, community direct reply, adjacent context, and unresolved question.
- A direct reply link is stronger than adjacency. Adjacent text without explicit linkage is context, not an authoritative answer.
- Discord timestamps are posting times, not automatically trade setup times.
- Chart attachments are cataloged but not reinterpreted as price-action evidence.
- Self-reported outcomes are not independently verified market records.
- Descriptive win shares are not causal effects, forward probabilities, or expectancy estimates.
- Instrument mentions are not execution denominators. ES used for SMT context does not prove the trade was executed on ES.
- NQ-versus-ES rejection-block comparison is insufficient in the strict eligible subset: 13 NQ-family versus 3 ES-family executed instances.
- Model memberships and confluence frequencies overlap; do not add them as though mutually exclusive.
- Preserve `partial`, `unknown`, `community_answer_only`, `unresolved`, and `provisional_derived` caveats in downstream answers.

## Source-provenance limitation

Some inherited non-primary rows in the original 14-day baseline carry the primary `premium-journals` query string in their row-level `search_query` descriptor. The database retains those descriptors losslessly instead of silently rewriting evidence. For intended supplemental-query context, use `source_collection`, `collection_coverage`, and the embedded raw merge metadata together. The limitation and affected counts are also recorded in `meta` and `research_runs.limitations`.

## Validation meaning

`validate_database_3month.py` opens the final SQLite file read-only and verifies:

- the exact 92-day production boundaries;
- SQLite integrity and all foreign keys;
- separation from the unchanged 14-day database;
- all eight database collections and disclosed partial boundaries;
- 39/39 primary files, 15/15 supplemental files, and 2,401 supplemental source occurrences;
- all 35 browser messages, all eight contexts, exact statuses, authority markers, channel IDs, message permalinks, and dedicated provenance;
- unique message IDs, timestamp scope, full-text-search parity, and source-query parity;
- resolvable evidence for findings, trades, models, Q&A, and research answers;
- win/loss profiles, probability tiers, four models, and embedded three-month artifacts.

Report states:

- `passed`: ready to share; no validation warnings or failures.
- `passed_with_warnings`: usable only with the stated warnings.
- `failed`: a hard integrity, scope, provenance, evidence, or analytical requirement was not met.
