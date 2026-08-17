# Querying the Discord Research Corpus

The corpus is 15,202 curated Discord messages from Powell's trading server (window:
2026-04-20 through 2026-07-20), distilled into a SQLite database with full-text search,
curated trade episodes (1,471 of them, each win/loss/breakeven/etc.), strategies, and
Q&A pairs. It's bundled directly inside this skill at
`data/discord_trading_research_3month.sqlite` so the whole `rejection-block-analyst`
folder is self-contained and portable — dropping it into another project's
`.claude/skills/` (Claude Code) or `.codex/skills/` (Codex CLI), or the `~/.claude/`
/ `~/.codex/` equivalents for a user-level install, brings the corpus with it. Query
the database — don't try to load the larger source JSON into context.

## Use the helper script first

`scripts/query_corpus.py` wraps the common question shapes. Run it with `python`:

```bash
python .claude/skills/rejection-block-analyst/scripts/query_corpus.py <command> [args]
```

It auto-detects the bundled database next to itself, so it works regardless of the
current working directory or which project it's running in.

| Command | Use it for |
|---|---|
| `search "<text>"` | Full-text search over all message content (FTS5). Best for finding what was actually said about a specific phrase, e.g. `search "close vs wick"`. |
| `findings [--facet X]` | Curated rejection-block findings. Facets: `timing`, `high_probability`, `identification`, `invalidation`, `low_probability`, `instrument_comparison`, `other`. Each finding includes its `evidence_status` and linked message excerpts. |
| `qa "<text>"` | Search the curated question/answer pairs (mentor and community replies), with `status` and `confidence`. |
| `strategy <key>` | Full operational strategy card (context, entry, invalidation, risk, target, rules, evidence, win/loss outcomes) for one strategy, e.g. `strategy 10am-key-open-rb`. Run `strategies` first if you don't know the key. |
| `strategies [--family X] [--status X]` | Summary table of every strategy. `status` is `documented` \| `provisional_derived` \| `planned` (a `planned` row like `ifvg-retrace` has no rules/evidence yet — see `references/adding_a_strategy.md`). |
| `strategy-report [--min-n N]` | Wins/losses/breakevens/win_rate/sample_flag per strategy, ranked highest win_rate first — this is the "which strategy is highest probability" report. Always relay `decided_n` and `sample_flag` alongside `win_rate`, never the bare rate. |
| `wins [--strategy X] [--instrument X]` / `losses [...]` / `breakevens [...]` | Outcome-filtered trades, each joined to its strategy (if one was attributed) and the attribution tier (`explicit` vs `curated_inference`). |
| `confluence-stats [--min-n N]` | Win/loss/breakeven counts per curated confluence, for building or checking the association claims used in `references/ict_concept_glossary.md`. |
| `trades [--confluence X] [--outcome win\|loss\|breakeven\|unknown] [--instrument X]` | Filter curated trade episodes, any outcome, not strategy-scoped. |
| `message <id>` | Full record (text, author, permalink, timestamp) for one Discord message ID. |
| `sql "<SELECT ...>"` | Escape hatch for anything not covered above — read-only SELECT against any table/view listed below. |

## When to reach for raw SQL

Use `sql` for anything the canned commands don't cover — joining across tables, custom
aggregations, or checking a specific research question. Useful tables/views:

- `messages`, `messages_fts` — raw message text, author, channel, permalink, relevance
- `rejection_block_findings` + `rejection_block_finding_evidence` — the curated RB rule
  book with evidence
- `trades`, `trade_confluences`, `trade_evidence` — episode-level outcomes and their
  tagged confluences (the row-level source of truth; `wins`/`losses`/`breakevens` below
  are just outcome-filtered, strategy-joined views over this table)
- `strategies`, `strategy_rules`, `strategy_evidence`, `trade_strategies` — the
  strategy schema. `trade_strategies` is the many-to-many link (a trade can match more
  than one strategy) with an `attribution` of `explicit` (the trade's own evidence
  message doubles as strategy evidence) or `curated_inference` (matched the strategy's
  confluence-tag signature). A resolved trade with no `trade_strategies` row isn't
  hidden — see `v_unclassified_trades`.
- `wins`, `losses`, `breakevens` — `trades` pre-filtered by outcome and left-joined to
  strategy name/key/attribution. Query these directly rather than hand-writing the join.
- `qa_pairs` — direct question → answer pairs with authority/status
- `confluences` — canonical confluence taxonomy (name, category, corpus definition)
- `outcome_profiles`, `outcome_profile_confluences` — win/loss profile summaries
- `research_questions`, `contradictions` — explicit open questions and unresolved
  tensions the corpus deliberately did not force an answer on
- `v_strategy_cards`, `v_strategy_report`, `v_unclassified_trades`,
  `v_win_loss_confluence_comparison`, `v_llm_research_answers`, `v_answered_qa`,
  `v_browser_context_followups` — pre-joined views for the above
- `data_dictionary` — column-by-column meaning if a field's semantics are unclear;
  `sql "SELECT * FROM data_dictionary WHERE table_name = 'trades'"`

## Evidence discipline — carry this into every answer

The corpus README is explicit about evidence tiers, and you must preserve them rather
than flattening everything into one confident voice:

- **`explicit`** — direct Discord wording or a linked reply. State it as "the Discord
  corpus explicitly says…" Still may be chart-specific — say so if the finding's caveat
  says so.
- **`observed_association`** — a count or descriptive share in the selected corpus. Not
  causal, not a forward probability. State it as "in the strict selected-corpus subset…"
- **`derived`** — conservative synthesis across explicit + observed evidence.
- **`insufficient_evidence`** — don't force an answer; say plainly that the corpus
  doesn't resolve this (e.g., NQ vs ES superiority, exact close-vs-wick invalidation
  distance).
- **`general_ict_knowledge`** — this is a fifth tag *you* apply, not one native to the
  database: standard ICT theory not specifically addressed by this Discord corpus. Use
  it whenever you're explaining mechanics from `ict_concept_glossary.md`'s general
  theory sections rather than quoting the corpus.

Practical rules:
- Cite `evidence_message_ids` / permalinks when you make a specific factual claim about
  what was said. If a permalink is unavailable, cite the message ID with a note rather
  than inventing a channel.
- Confluence frequencies overlap within trades (a trade can be tagged with 5+
  confluences) — don't sum shares across confluences as if they were mutually exclusive.
- Small-n associations (rule of thumb: under ~15 resolved instances) should be flagged
  as anecdotal even when they look like a strong percentage.
- Strategy membership overlaps too — the same trade can arguably fit more than one
  strategy (it'll appear once per matched strategy in `wins`/`losses`/`breakevens`).
  `curated_inference` attribution is weaker than `explicit`; say which one a claim
  rests on when it matters.
- Never convert a descriptive win share into "this setup has an X% win rate," an
  expectancy figure, or a forward-looking probability.

## Refreshing / regenerating source docs

The bundled database is a static, finished research artifact — this skill only reads
it, never regenerates it. If a narrative (non-queryable) version of the same research
is ever useful, it originated from `pipeline/reports/RESEARCH_SUMMARY_3MONTH.md` (the
original authoring standard this skill's evidence discipline is built from), but that
file is not bundled with this skill and isn't required for it to function. The full
rebuild pipeline (raw Discord scrapes, the database builder, and this skill's own
strategy-attribution step) lives in `pipeline/` at the repo root — see
`pipeline/README.md`. That folder is local-only (not tracked in git) and entirely
separate from what this skill reads at runtime.
