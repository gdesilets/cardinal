# LLM Guide — Three-Month Discord Trading Research

## Purpose

Use these artifacts to answer questions about the supplied Discord corpus only. Do not supplement answers with web knowledge, generic ICT teaching, chart guesses, or market data.

Coverage: **2026-04-20 through 2026-07-20**, inclusive. The merged export contains **15202 unique Discord messages**. A targeted browser audit adds **24 unique messages** across **8 selected contexts**; it is not a complete export of those channels. The strict trade-comparison denominator is **530 instances** (172 wins and 358 losses).

## Recommended artifact order

1. `curated_analysis_3month.json` — start here for direct research answers, Q&A, profiles, models, contradictions, and evidence IDs.
2. `browser_context_followups_3month.json` — use for the eight targeted permalink contexts, visible direct-reply links, and context-level resolution status.
3. `rb_analysis_3month.json` — use for the full explicit-rule/association distinction, related questions, and evidence sufficiency.
4. `model_analysis_3month.json` — use for exact model inclusion/exclusion, risk, failure profiles, and strict model counts.
5. `trade_analysis_3month.json` — use for episode-level audits and strict outcome/confluence denominators.
6. `raw_discord_export_3month.json` — use for exact merged-export message text and provenance.
7. `discord_trading_research_3month.sqlite` — once built, use for relational/FTS queries.

## Evidence taxonomy

- `explicit`: captured Discord wording or a directly linked reply. It can still be chart-specific.
- `observed_association`: corpus count or descriptive share. It is not causal and is not out-of-sample probability.
- `derived`: conservative synthesis across Discord evidence.
- `insufficient_evidence`: denominator/direct answer is inadequate; do not force an answer.

Always state the evidence type in the answer. Prefer `evidence_message_ids` and `evidence[].permalink`. If a permalink is unavailable, cite the message ID without inventing a channel.

## Authority and linkage rules

For browser-follow-up evidence, preserve `source_authority` exactly:

- `named_mentor_direct_reply` is the strongest captured answer tier, while still potentially chart-specific.
- `community_direct_reply` is a member answer, not a mentor rule.
- `community_adjacent_context` is context only and must not be presented as a direct answer.
- `unresolved_question` means the audit did not supply a responsive answer.

The browser file covers eight deliberately selected contexts and 35 visible messages only. Never generalize its local completeness to an entire channel.

## Database tables and views

The three-month database builder imports the curated JSON into:

- `messages`, `message_sources`, `merged_message_provenance`
- `browser_context_followup_artifacts`, `browser_followup_contexts`, `browser_followup_context_messages`
- `rejection_block_findings`, `rejection_block_finding_evidence`
- `qa_pairs`
- `trades`, `trade_evidence`, `trade_confluences`
- `outcome_profiles`, `outcome_profile_confluences`
- `probability_tiers`
- `trading_models`, `model_rules`, `model_evidence`
- `research_questions`, `contradictions`, `analysis_documents`

Useful views include `v_browser_context_followups`, `v_rejection_block_evidence`, `v_answered_qa`, `v_trade_feature_matrix`, `v_model_cards`, and `v_llm_research_answers`. Use full-text search for exact source wording, then join back to evidence tables.

## Required answering procedure

1. Find the closest `research_questions` or curated `research_questions[]` record.
2. State whether it is answered, partial, or insufficient.
3. Separate explicit rules from observed associations and synthesis.
4. Cite the exact message IDs/permalinks supporting the claim.
5. When using outcomes, use only the strict eligible denominator unless explicitly discussing all episode records.
6. Keep executed instruments separate from market-context instruments. ES used for SMT does not prove the trade was executed on ES.
7. Preserve chart-dependent and timezone caveats.
8. Preserve browser-context statuses (`answered`, `partially_answered`, `community_answer_only`, `unresolved`) and do not promote community or adjacent text to mentor authority.
9. Never convert descriptive win share into expectancy, causal lift, or a forward success rate.

## Key denominator warnings

- Strict overall comparison: 172 wins / 358 losses.
- Strict RB comparison: 92 wins / 217 losses, n=309.
- NQ-vs-ES RB comparison is insufficient: 13 NQ-family versus 3 ES-family eligible executed instances.
- Model membership overlaps; model counts cannot be added.
- Confluence frequencies overlap within trades; sums can exceed the denominator.
- M4 has only n=2 strict outcomes and must not be ranked as high probability.

## High-value query examples

- “Show the explicit Discord answers about RB close confirmation, then distinguish them from synthesis.”
- “List RB invalidation versus non-actionability findings with evidence links.”
- “Compare higher and lower observed confluence associations, including W/L/n and the baseline.”
- “Give me the 10AM model’s required rules, exclusions, failure profile, and exact evidence.”
- “Why can’t this corpus answer NQ versus ES?”
- “Show the strict loss profile without implying causation.”

## Output style for downstream LLMs

Use language such as:

- “The Discord corpus explicitly states…”
- “In the strict selected-corpus subset…”
- “This association is descriptive and overlapping…”
- “The available evidence is insufficient…”

Avoid:

- “This setup has a true X% win rate.”
- “This confluence causes better performance.”
- “RBs objectively work best at…”
- Any rule supplied from outside this Discord export.

## Refreshing after an RB-analysis update

Run:

```powershell
python ./discord_trading_research/compose_curated_analysis_3month.py
```

The composer deterministically rewrites only the three three-month curated outputs and validates every referenced Discord message ID and permalink before succeeding.
