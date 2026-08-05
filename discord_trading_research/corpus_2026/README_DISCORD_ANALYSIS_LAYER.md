# Discord-only Cardinal analysis layer

`build_discord_analysis_layer.py` is the local analysis stage that runs after
`build_cardinal_database_v2.py`. It creates a **new** SQLite database and never
edits the raw Cardinal database or any 14-day/three-month legacy artifact.

The stage uses only:

- messages and provenance already present in the local Cardinal v2 database;
- the read-only Discord-derived three-month curated/model artifacts; and
- the proven local deterministic trade, rejection-block, and model extraction
  code.

It never requests the web, market data, economic-calendar data, or Cardinal/ICT
definitions. Cardinal supplies the destination schema only.

## Run order

First build a pristine Cardinal v2 database from the merged Jan 1–Jul 20 corpus.
Then run:

```powershell
python .\discord_trading_research\corpus_2026-01-01_2026-07-20\build_discord_analysis_layer.py `
  --database .\discord_trading_research\corpus_2026-01-01_2026-07-20\discord_cardinal_v2.sqlite `
  --output .\discord_trading_research\corpus_2026-01-01_2026-07-20\discord_cardinal_analyzed.sqlite `
  --report .\discord_trading_research\corpus_2026-01-01_2026-07-20\discord_analysis_report.json
```

The input and output paths must differ. Existing output/report files are refused
unless `--replace` is explicit. A failed run removes only its `.building` copy;
the input database remains unchanged.

## What is populated

The analyzer fills the existing Cardinal v2 evidence/analysis schema:

- `analysis_runs`, `analysis_entities`, `evidence_items`, `claims`,
  `claim_evidence`, and `confidence_assessments`;
- `questions`, `answers`, `question_messages`, `answer_messages`, and
  `question_answer_links`;
- `setup_instances`, `setup_features`, `setup_instruments`, `setup_timeframes`,
  `setup_sessions`, `setup_time_markers`, and `setup_invalidations`;
- `trade_episodes`, `trade_outcome_claims`, and `trade_outcome_resolution`;
- `setup_models`, `setup_model_rules`, `setup_model_matches`, and
  `setup_rule_states`;
- `analysis_cohorts`, `setup_performance_rollups`, and `analysis_documents`.
- `contradiction_sets`, `contradiction_members`, `relevance_annotations`, and
  the column-level `data_dictionary`.

The following structured documents are also stored in `analysis_documents`:

- `discord_analysis_coverage`
- `discord_rejection_block_research`
- `discord_qa_catalog_summary`
- `discord_contradiction_catalog_summary`
- `discord_trade_profiles`
- `discord_model_cards`
- `discord_analysis_methodology`

These documents are convenient for an LLM, while the normalized tables retain
the message-level evidence and queryable relationships.

## Rejection-block evidence

The layer combines two evidence streams without conflating them:

1. Existing Discord-derived curated findings whose evidence message IDs resolve
   in the new database.
2. A whole-corpus text scan using the already-proven local RB component patterns.

Identification, invalidation/non-actionability, timing, and confluence
co-mentions remain separate facets. Every whole-corpus observation points back
to a Discord message. The timing catalog counts only explicit setup-time/session
wording inside RB messages; message posting time is never substituted for the
time at which a block formed. Attachments are not interpreted as chart facts.
This stage does not fetch or add durable media bytes; attachment filename/URL
metadata alone remains provenance rather than visual evidence. Only a pre-existing
complete/partial extraction with verified local path/hash/size can support a
chart-dependent claim, and only through exact evidence linkage. Failed/no-artifact
rows never count; unreported extraction confidence remains `NULL`.
The protected legacy RB script is not modified, but its permissive bare-`10`
timing pattern is replaced inside this full-window analyzer with a fail-closed
clock-token pattern; text such as “10 points” cannot count as a 10AM mention.

## Q&A and authority

An exact `reply_to_message_id` chain is automatically retained as direct linked
context. It does not automatically become an answered question. Corpus-wide
reply scans store the answer row as `community_only` and the question as
`partial`; only the Discord-derived curated Q&A artifact may mark a reply
`answered`, `partial`, or `conflicting`. A captured direct reply establishes
linkage, not responsiveness, correctness, or mentor authority.

The targeted three-month Q&A authority classes are preserved when their question
and answer evidence both resolve. All other direct replies keep speaker authority
unresolved. Questions without a captured direct reply remain `unanswered` in the
current capture; that status is not proof that no answer ever existed.

Useful views:

- `v_authority_separated_qa`
- `v_unresolved_qa`

## Trust eligibility

Raw migrated/quarantined messages remain in `messages`, `messages_fts`, and
`v_message_trust_lookup` so an LLM can retrieve their exact text and provenance.
The analysis scan reads only `v_analysis_eligible_messages`. A quarantined or
migrated message enters that view only after an independent trusted canonical
channel-segment recapture. Database triggers also prevent ineligible evidence
from backing accepted claims, resolved setup identities, or strict trade
outcomes.

## Strict trade episodes and profiles

The same conservative extraction logic used by the three-month analysis is run
against the database window. A trade enters the strict win/loss comparison only
when the extractor resolves an executed-trade episode, one attributable instance,
an explicit win or loss, at least one confluence, and no shared multi-trade
attribution. Breakeven, mixed, cancelled, open, unknown, paper, and aggregate
episodes remain in the database but do not enter that denominator.

Win/loss profiles and confluence rows report only:

- wins;
- losses;
- eligible selected-corpus count; and
- descriptive selected-corpus win share.

The shares are overlapping, self-reported, author-clustered, non-causal, and not
forward probabilities or expectancy estimates. Small samples receive lower
`corpus_support` confidence, but no probability model is fitted.

Marginal confluence and instrument-family denominators are episode-grain. A
single trade containing `rejection_block:1m:entry` and
`rejection_block:5m:context`, or both NQ/MNQ tokens, counts once in the canonical
family row. The analyzer fails if a canonical subset denominator exceeds the
strict overall denominator.

`discord_trade_profiles.strict_slice_profiles` also supplies strict win/loss
slices for exact canonical-confluence sets, explicitly executed instrument,
explicit direction, explicit session text, explicit setup-time text, and stored
model membership. Every row includes sample count, author concentration,
evidence message IDs, and overlap/non-causal warnings. Missing dimensions remain
missing; posting time is not substituted for setup time, session clocks are not
inferred, and model membership is labeled signature-derived.

### Author identity and concentration

Strict trade episodes inherit the canonical author identity and current readable
display label from their primary Discord message. When the database has an exact
Discord user ID, profiles group renamed display labels under that exact ID and do
not merge different exact IDs merely because their visible names match. When an
exact ID was not captured, the database's author surrogate remains the grouping
key. Older in-memory episodes with no `author_id` remain supported through a
deterministic display-name surrogate; that fallback is explicitly labeled and is
never presented as a verified unique person.

Overall, win, loss, confluence, executed-instrument, context-instrument, RB-only
instrument, and model-card cohorts expose descriptive author clustering fields:

- distinct authors, split into exact-ID and surrogate counts;
- episode counts split by exact-ID and surrogate attribution;
- the top-author and top-three-author shares of the selected cohort; and
- readable top-author records with canonical keys and observed display-name
  variants.

Confluence and instrument rows also retain separate win/loss author summaries.
`setup_performance_rollups.distinct_authors` and `top_author_share` are populated
from the same canonical-ID-first grouping. These fields reveal concentration in
the selected Discord evidence; they are not independence corrections, market
probabilities, or claims that multiple posts from one author are separate samples.

## NQ versus ES

`setup_instruments.role` is the hard separation:

- `executed` means the extractor found explicit execution wording for that
  symbol;
- `market_context` means the symbol appeared only as context/intermarket
  evidence.

Only `executed` rows may be used for NQ-versus-ES trade comparisons. ES appearing
for SMT/context in an NQ trade does not become an ES trade. The analysis report
shows the two catalogs separately. It also includes a rejection-block-only
executed-instrument comparison. If either NQ-family or ES-family has fewer than
five strict RB episodes, that comparison is explicitly labeled insufficient;
even with larger counts it remains descriptive rather than proof that one market
is objectively better.

## Model cards

Up to five models may be emitted. Supported Discord-derived templates from the
preserved three-month artifact are re-matched first, but they are no longer the
entire candidate universe. The analyzer also exhaustively enumerates every valid
two- and three-token combination formed from the full Jan 1–Jul 20 strict trade
episodes' stored confluence, rejection-block detail, session, setup-time, and
explicit named-setup fields. No concept mapping is added from Cardinal, the web,
or general trading knowledge.

A newly discovered family is eligible only when all of these deterministic
safeguards pass:

- at least five trust-eligible strict executed win/loss episodes;
- at least three authors and three trade dates;
- no author supplies more than 60% of its strict episodes;
- at least two explicit setup/rule/entry/invalidation/target messages from at
  least two authors; and
- its matched-episode set is not a near-duplicate of a preserved template or a
  stronger full-window candidate.

Only remaining slots after supported preserved templates are considered. A
one-off, author-dominated, quarantined-only, weakly documented, or overlapping
candidate stays in the discovery audit as insufficient/rejected and is not
promoted. A model is omitted when its local evidence cannot be resolved. Model
membership may overlap, so model counts must not be added.

Novel cards quote entry, invalidation, stop, target, management, or no-trade
rules only when explicit trust-eligible Discord text supports that facet. Missing
facets remain listed as unresolved. Each card includes message IDs, exact Discord
permalinks, evidence excerpts, promotion metrics, retained counterevidence, and
the descriptive/self-reported/non-causal warning.

Candidate membership is signature-derived. It does not prove that every stored
operational rule matched an episode. For every model-match/rule pair the current
pass writes `setup_rule_states.state='unknown'`, with matched count zero and the
unknown rules included in `missing_rule_count`, unless a later evidence pass
evaluates the rule explicitly. Compact `fully_matched_instances` is computed
from rule states, not from a nonexistent match-status label.

Each emitted card reports author concentration for all matched episodes and for
its strict win/loss subset, using the same exact-ID-first identity policy as the
trade profiles.

No fifth card is created merely to fill a slot.

## Coverage and confidence

Collection coverage is copied from `collection_runs`, `collection_units`, and
`v_collection_gaps`. If collection is partial, every analysis output is
conditional on the captured corpus. The layer never relabels a partial corpus as
complete.

Confidence dimensions describe extraction, linkage, normalization, outcome
resolution, Q&A resolution, or corpus support. They never describe the chance
that a future trade will win.

## Validation

Run the isolated tests:

```powershell
python .\discord_trading_research\corpus_2026-01-01_2026-07-20\test_discord_analysis_layer.py
```

The release-stage analyzer also refuses completion unless:

- SQLite integrity and foreign keys pass;
- `v_discord_only_audit` is empty;
- accepted claims have evidence;
- accepted claims, resolved setups, and strict trades use only trust-eligible
  message evidence;
- strict outcomes contain only wins/losses;
- executed and context instrument roles remain separate;
- rollup arithmetic reconciles and all rollups are marked non-causal;
- rollup `excluded_count` records imported/matched episodes outside the strict
  denominator;
- every answered question has a curated `answered` answer;
- every model match has one state for every stored rule and its rule counts
  reconcile;
- every analysis-eligible message has exactly one non-destructive relevance
  label;
- the data dictionary covers every non-technical user-table column;
- every contradiction set has at least two captured members; and
- no more than five models are present.
