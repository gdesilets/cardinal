# Database schema reference (for human review)

`data/discord_trading_research_3month.sqlite` is a 60MB binary file — not
something you can open and skim. This doc is that skim: every table/view, what
it's for, and its current row count, grouped by subsystem rather than
alphabetically so a manual reviewer can find the relevant section fast. Numbers
below are current as of the last database rebuild; re-run the counts yourself
(`sql "SELECT COUNT(*) FROM <name>"` via `scripts/query_corpus.py`) if this doc
and the live file ever disagree — the file is authoritative, this is a map of it.

## 1. Trades, wins/losses, strategy attribution

The core fact table plus the views built on top of it.

| Name | Rows | What it is |
|---|---|---|
| `trades` | 1,471 | One row per curated trade episode: outcome (`win`/`loss`/`breakeven`/`mixed_partial`/`cancelled_no_trade`/`unknown`/`open`), instrument, direction, dates, entry/stop/target text. `trade_id` (e.g. `AUTO000001`) is the primary key referenced everywhere else. |
| `trade_evidence` | 3,183 | Discord `message_id`s backing each trade, tagged by role (context/setup/entry/invalidation/outcome/management). |
| `trade_confluences` | 2,899 | Which `confluences` (below) each trade exhibits, and whether `present`/`absent`/`violated`. |
| `trade_strategies` | 534 | Many-to-many link: which `strategies` (below) each trade matches, and how confidently (`explicit` vs `curated_inference`). A trade can match 0, 1, or several strategies. |
| `wins` / `losses` / `breakevens` (views) | 288 / 686 / 119 | `trades` filtered by outcome, left-joined to strategy name/key. **Row count ≠ distinct trade count** — a trade matching 2 strategies appears twice. `wins` has 264 distinct trades in 288 rows; `losses` has 642 distinct trades in 686 rows. Use `COUNT(DISTINCT trade_id)` if you need the trade count, not `COUNT(*)`. |
| `v_unclassified_trades` (view) | 749 | Resolved (win/loss/breakeven) trades with **no** `trade_strategies` row — matched no strategy's confluence signature. Not an error; the corpus is honest about what it can't attribute. |
| `v_trade_feature_matrix` (view) | 1,471 | One row per trade with all its confluences concatenated into one string column — convenience view for scanning, not for aggregation. |

## 2. Strategies (the "which setup" taxonomy)

Generalized from the original 4 ICT rejection-block "models" so a future,
differently-shaped strategy family (e.g. iFVG) can be added without a schema
change — see `references/adding_a_strategy.md`.

| Name | Rows | What it is |
|---|---|---|
| `strategies` | 5 | 4 documented rejection-block strategies (`10am-key-open-rb`, `htf-pda-ltf-rb`, `mmxm-stdv-breaker`, `sweep-displacement-fvg-retrace`) + 1 `planned`-status placeholder (`ifvg-retrace`, no rules/evidence yet). `strategy_key` is the human-typeable id used everywhere (`query_corpus.py strategy <key>`); `family` groups strategies (`rejection-block`, `ifvg`); `status` is `documented`/`provisional_derived`/`planned`. |
| `strategy_rules` | 49 | Ordered entry/invalidation/risk/target/management rules per strategy. |
| `strategy_evidence` | 96 | Discord `message_id`s supporting (or contradicting) each strategy's existence as a pattern. |
| `v_strategy_cards` (view) | 5 | One row per strategy with its rules concatenated — what `query_corpus.py strategy <key>` returns. |
| `v_strategy_report` (view) | 5 | Per-strategy wins/losses/breakevens/`win_rate`/`sample_flag` (`small_sample` below n=15) — the "which strategy is highest probability" report. `query_corpus.py strategy-report` defaults to hiding rows below n=15 (currently: `sweep-displacement-fvg-retrace` at n=6 and the empty `ifvg-retrace` placeholder). |

## 3. Confluence taxonomy (the "which ingredients" vocabulary)

| Name | Rows | What it is |
|---|---|---|
| `confluences` | 20 | Canonical ICT concept names (`rejection_block`, `fvg_ifvg`, `smt_ssmt`, `key_open`, `standard_deviation`, `draw_on_liquidity`, `cisd`, ...) — the normalized vocabulary `trade_confluences` and `message_confluences` point into. |
| `message_confluences` | 13,081 | Which confluences each Discord message discusses (message-level, broader than the curated `trade_confluences`). |
| `v_win_loss_confluence_comparison` (view) | 20 | Win/loss/breakeven counts per confluence — backs the specific numbers cited throughout `references/ict_concept_glossary.md`. |
| `outcome_profiles` | 2 | Narrative win-profile / loss-profile summaries. |
| `outcome_profile_confluences` | 28 | Which confluences characterize each outcome profile, with observed share. |
| `outcome_mentions` | 975 | Raw win/loss self-report mentions in Discord messages (broader, less curated than `trades`). |

## 4. Discord source messages

| Name | Rows | What it is |
|---|---|---|
| `messages` | 7,170 | The curated (relevance-filtered) message set — full text, author, channel, permalink. `messages_fts` (FTS5 virtual table, not counted above) is the full-text search index over this; query via `scripts/query_corpus.py search "<text>"`, not directly. |
| `channels` | 210 | Discord channel metadata referenced by `messages`. |
| `message_tags` | 10,993 | Coarse message-level tags (`rejection_block`, `trade_report`, `question`, `rule_or_definition`, `outcome`, `no_trade`, `rule_breach`, `high_probability_claim`, `low_probability_claim`). |
| `message_instruments` | 1,057 | Which instrument (NQ/ES/MNQ/...) each message mentions — market-context mentions, not necessarily the executed instrument (see the corpus's own warning about conflating the two, in `references/corpus_query_guide.md`). |
| `message_sources` | 7,374 | Which raw collection/search query each message came from. |
| `attachments` | 2,619 | Chart/image attachments cataloged (not visually reinterpreted — text-described only). |
| `merged_message_provenance` / `v_merged_message_provenance` | 7,374 | Merge-source lineage for messages combined from multiple raw collection passes. |
| `v_rejection_block_evidence` (view) | 4,296 | Messages tagged `rejection_block`, with their other tags concatenated — a fast way to eyeball raw RB discussion. |

## 5. Curated findings, Q&A, and open questions

| Name | Rows | What it is |
|---|---|---|
| `rejection_block_findings` | 80 | The curated RB rule book: identification, invalidation, timing, probability, instrument, execution, and risk findings, each with an `evidence_status` tier. |
| `rejection_block_finding_evidence` | 541 | Message-level evidence backing each finding. |
| `qa_pairs` / `v_answered_qa` | 932 / 17 | Captured Discord question→answer pairs with `status` (answered/partial/unanswered/conflicting) and `confidence`. |
| `research_questions` / `v_llm_research_answers` | 11 / 11 | The main research questions this corpus was built to answer, with concise answers and limitations. |
| `contradictions` | 4 | Tensions the corpus deliberately left unresolved rather than forcing an answer. |
| `probability_tiers` | 4 | Qualitative setup-quality tiers — corpus syntheses, not measured probabilities. |

## 6. Targeted browser-context audit

An authenticated Discord browser audit of 35 messages across 8 hand-selected
permalink contexts — complete only for those 8, not for the containing channels.

| Name | Rows |
|---|---|
| `browser_context_followup_artifacts` | 1 |
| `browser_followup_contexts` | 8 |
| `browser_followup_context_messages` | 35 |
| `v_browser_context_followups` (view) | 35 |

## 7. Meta / provenance / collection completeness

| Name | Rows | What it is |
|---|---|---|
| `meta` | 23 | Key-value build metadata (schema version, build timestamp, source file paths, coverage caveats). |
| `research_runs` | 1 | One row describing this build's scope/window/status. |
| `collection_coverage` | 8 | Per-source-collection message counts before relevance filtering. |
| `exclusion_stats` | 8 | Counts of messages excluded and why, by source. |
| `analysis_documents` | 8 | The full structured analysis JSON artifacts (findings/trades/models/curated) embedded as text, for provenance. |
| `data_dictionary` | 25 | Column-by-column descriptions for 16 of the tables above (not yet extended to `strategies`/`trade_strategies`/`confluences` — this doc covers those instead). Query: `sql "SELECT * FROM data_dictionary WHERE table_name = 'trades'"`. |

## Quick sanity checks

```bash
python scripts/query_corpus.py sql "SELECT COUNT(*) FROM trades"                    # 1471
python scripts/query_corpus.py sql "SELECT outcome, COUNT(*) FROM trades GROUP BY outcome"
python scripts/query_corpus.py strategies                                            # 5 rows
python scripts/query_corpus.py strategy-report                                       # 3 rows (n>=15 default)
python scripts/query_corpus.py sql "SELECT COUNT(*) FROM v_unclassified_trades"      # 749
```

If any of these don't match this doc, the doc is stale — trust the query, then fix
this file.
