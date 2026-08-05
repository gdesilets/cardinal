# Discord trading research database

`discord_trading_research.sqlite` is a self-contained SQLite research file built only from the specified Discord server. It covers the 14 observed dates from 2026-07-07 through 2026-07-20 UTC (`after:2026-07-06 before:2026-07-21`). No public-web trading material was added.

Start with `research_questions` or `v_llm_research_answers`, then trace important claims to their Discord message IDs. Use `messages_fts` only for discovery; use the curated finding, model, trade, and Q&A tables for conclusions.

## Best tables for questions

- `research_questions`: concise answers to the main rejection-block questions, with evidence IDs and limitations.
- `rejection_block_findings` and `rejection_block_finding_evidence`: normalized findings and the Discord messages supporting them.
- `trading_models`, `model_rules`, and `model_evidence`: up to five model slots; only evidence-distinct models are populated.
- `trades`, `trade_evidence`, and `trade_confluences`: conservatively reconstructed trade episodes. Unknown values are preserved rather than guessed.
- `outcome_profiles` and `outcome_profile_confluences`: win, loss, and (when populated) breakeven profiles.
- `probability_tiers`: qualitative setup-quality tiers. These are corpus syntheses, not measured market probabilities.
- `qa_pairs`: captured questions, direct replies where found, and unanswered items.
- `messages`, `attachments`, `message_confluences`, and `message_fts`: filtered source material and full-text search.
- `contradictions`: tensions or questions the Discord evidence did not settle.
- `collection_coverage`, `exclusion_stats`, `research_runs`, and `meta`: collection completeness, date boundaries, and methodology.
- `analysis_documents`: the full structured analytical JSON artifacts embedded inside the database.

Useful views include `v_rejection_block_evidence`, `v_answered_qa`, `v_trade_feature_matrix`, `v_win_loss_confluence_comparison`, `v_model_cards`, and `v_llm_research_answers`.

## Interpretation rules

1. Treat Discord statements and outcomes as self-reported observations, not independently verified market data.
2. Do not infer a win rate from keyword frequency or from the number of messages mentioning wins and losses.
3. A message timestamp is a posting time, not automatically the setup time. Setup-time language is retained separately when it was stated.
4. Screenshot attachments are cataloged, but chart geometry was not visually reclassified unless it was described in text.
5. `NQ` and `ES` occurrence counts are not performance denominators. The corpus does not establish that rejection blocks work better on one instrument.
6. Preserve `unknown`, `partial`, `unanswered`, and `provisional_derived` labels when answering questions; they encode genuine evidence gaps.
7. `messages` contains the relevant filtered corpus, not all 1,514 primary search results. Low-signal conversation was excluded, while message IDs used by curated evidence were forced into the database.
8. `exclusion_stats` counts exclusions by source collection; because one Discord message can appear in multiple searches, those buckets are not a distinct-message total.
9. The overall research run is marked `partial` because the broad server-wide shorthand `RB` search stopped at result 325. The requested `premium-journals` collection itself is complete.
10. `v_win_loss_confluence_comparison` uses normalized families. For example, `fvg_ifvg` combines FVG and inverse-FVG labels, and `cisd_mss_displacement` combines related confirmation labels. Use the embedded `trade_analysis` document when exact original-tag counts are required.
11. `trade_analysis` contains 194 source episode records. `trades` contains 197 relational rows because three explicitly two-trade, shared-confluence episodes were expanded so the strict 46-win/82-loss comparison keeps its correct denominator.

## Example SQLite prompts

```sql
SELECT question_text, answer_status, answer_summary, limitations
FROM research_questions;

SELECT f.facet, f.finding, f.evidence_status, f.confidence,
       e.message_id, e.excerpt
FROM rejection_block_findings f
LEFT JOIN rejection_block_finding_evidence e USING (finding_id)
ORDER BY f.facet, f.finding_id;

SELECT * FROM v_model_cards ORDER BY model_no;

SELECT * FROM v_trade_feature_matrix ORDER BY trade_date, trade_id;

SELECT * FROM v_win_loss_confluence_comparison
ORDER BY total_resolved_mentions DESC, canonical_name;

SELECT normalized_question, answer_summary, status
FROM qa_pairs
WHERE status IN ('unanswered', 'partial', 'ambiguous');
```

## Known collection gap

The requested premium-journals search is complete at 1,514 unique results. Targeted server searches for the full phrase `rejection block`, rejection-block questions, and NQ/ES questions were also completed. A broader server-wide shorthand `RB` search was retained only through result 325 because Discord's search backend stopped returning later pages; it is explicitly marked partial in `collection_coverage` and must not be treated as exhaustive.
