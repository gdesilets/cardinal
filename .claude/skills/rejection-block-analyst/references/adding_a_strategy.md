# Adding a new strategy family

The `strategies` table is deliberately generic — it isn't specific to ICT rejection
blocks. It's scoped by a `family` column (`rejection-block`, `ifvg`, and whatever
comes next) and a `status` of `documented` / `provisional_derived` / `planned`, so a
new strategy family slots in without any schema changes. `ifvg-retrace` is currently
seeded as a `status='planned'` row with no rules or trade attribution yet — a worked
example of stage 1 below, and the one to promote first.

This is a pipeline-side task (touches `pipeline/`, not this skill folder directly),
run from `pipeline/`. See `pipeline/README.md` for the full rebuild context.

## Stage 1 — placeholder (already done for iFVG)

A `strategies` row with `status='planned'`, a `thesis` describing what the strategy
*would* be, and no rules/evidence/trade_strategies rows. Lets the skill answer "do we
have an X strategy yet?" honestly instead of the LLM guessing. Add one via
`strategy_schema.insert_strategy()` (see `seed_ifvg_placeholder()` in
`pipeline/strategy_schema.py` for the pattern) or directly:

```python
import strategy_schema, sqlite3
conn = sqlite3.connect("discord_trading_research_3month.sqlite")
strategy_schema.insert_strategy(
    conn,
    {"name": "My New Strategy (planned)", "thesis": "...", "limitations": "status=planned: no rules yet."},
    family="my-family", strategy_key="my-strategy-key", status="planned",
)
conn.commit()
```

## Stage 2 — real rules, no trade attribution yet (`provisional_derived`)

Once you've curated the strategy's entry/invalidation/target rules from Discord
evidence (the same way the 4 rejection-block models were originally curated into
`curated_analysis_3month.json`'s `models[]` array), update that row: fill in
`thesis`, `eligibility_context`, `identification`, `entry`, `stop`, `target`,
`invalidation`, add `strategy_rules` rows, and cite `strategy_evidence` message IDs.
Bump `status` to `provisional_derived` once the rule set is real, even before any
trade has been linked to it.

## Stage 3 — link trades (`documented`, or `provisional_derived` with real numbers)

This is what makes the strategy show up with real numbers in `strategy-report` /
`wins` / `losses` instead of all-zero. Two ways to populate `trade_strategies`,
mirroring how the 4 rejection-block models were re-derived in
`pipeline/build_trade_strategy_attribution.py`:

- **`explicit`**: a trade's own `trade_evidence.message_id` is also cited in this
  strategy's `strategy_evidence` — the strongest signal. Computed automatically by
  the shared `explicit_tier_pairs()` query in `build_trade_strategy_attribution.py`
  (message-overlap join, not strategy-specific) once `strategy_evidence` has rows.
- **`curated_inference`**: write a matcher predicate against each trade's confluence
  tags (see `is_10am_rb`, `is_mmxm_stdv_breaker`, etc. in
  `build_trade_strategy_attribution.py` for the pattern — they check
  `episode.get("confluences")` from `trade_analysis_3month.json`, keyed by
  `episode_id == trades.trade_id`). Add your predicate to `MATCHERS_BY_MODEL_NO`-style
  mapping (or a new dict, if this strategy isn't part of the `model_no` 1-4 legacy
  numbering) and re-run the script.

Once `trade_strategies` has rows for the strategy, `v_strategy_report` computes its
wins/losses/win_rate/sample_flag automatically — no view changes needed. Promote
`status` to `documented` once the rule set and evidence are solid, keep it
`provisional_derived` if it's still a reasonable-but-thin pattern (matching the same
distinction already used for the 4 rejection-block models).

## Checklist

1. `strategies` row exists (`strategy_key`, `family`, `status`).
2. Rules curated (`strategy_rules`) and cited (`strategy_evidence`) once you're past
   the placeholder stage.
3. `trade_strategies` populated (explicit and/or curated_inference).
4. `python query_corpus.py strategy <key>` shows the full card; `strategy-report`
   shows real wins/losses instead of zeros.
5. Re-copy the rebuilt database into the skill and re-sync the Codex mirror:
   ```powershell
   Copy-Item pipeline\discord_trading_research_3month.sqlite `
     .claude\skills\rejection-block-analyst\data\ -Force
   python scripts\sync_codex_skill.py
   ```
