# Querying the Discord Research Corpus

The corpus is 15,202 curated Discord messages from Powell's trading server (window:
2026-04-20 through 2026-07-20), distilled into a SQLite database with full-text search,
curated trade episodes, rejection-block findings, trading models, and Q&A pairs. It's
bundled directly inside this skill at `data/discord_trading_research_3month.sqlite` so
the whole `rejection-block-analyst` folder is self-contained and portable — dropping it
into another project's `.claude/skills/` (or `~/.claude/skills/`) brings the corpus with
it. Query the database — don't try to load the larger source JSON into context.

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
| `model <1-4>` | Full operational model card (context, entry, invalidation, risk, target, win/loss counts, evidence) for one of the four documented models. |
| `models` | Summary table of all four models. |
| `confluence-stats [--min-n N]` | Win/loss/breakeven counts per curated confluence, for building or checking the association claims used in `references/ict_concept_glossary.md`. |
| `trades [--confluence X] [--outcome win\|loss\|breakeven\|unknown] [--instrument X]` | Filter curated trade episodes. |
| `message <id>` | Full record (text, author, permalink, timestamp) for one Discord message ID. |
| `sql "<SELECT ...>"` | Escape hatch for anything not covered above — read-only SELECT against any table/view listed below. |

## When to reach for raw SQL

Use `sql` for anything the canned commands don't cover — joining across tables, custom
aggregations, or checking a specific research question. Useful tables/views:

- `messages`, `messages_fts` — raw message text, author, channel, permalink, relevance
- `rejection_block_findings` + `rejection_block_finding_evidence` — the curated RB rule
  book with evidence
- `trades`, `trade_confluences`, `trade_evidence` — episode-level outcomes and their
  tagged confluences
- `trading_models`, `model_rules`, `model_evidence` — the four operational models
- `qa_pairs` — direct question → answer pairs with authority/status
- `confluences` — canonical confluence taxonomy (name, category, corpus definition)
- `outcome_profiles`, `outcome_profile_confluences` — win/loss profile summaries
- `research_questions`, `contradictions` — explicit open questions and unresolved
  tensions the corpus deliberately did not force an answer on
- `v_model_cards`, `v_win_loss_confluence_comparison`, `v_llm_research_answers`,
  `v_answered_qa`, `v_browser_context_followups` — pre-joined views for the above
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
- Model membership overlaps too — the same trade can arguably fit more than one model.
- Never convert a descriptive win share into "this setup has an X% win rate," an
  expectancy figure, or a forward-looking probability.

## Refreshing / regenerating source docs

The bundled database is a static, finished research artifact — this skill only reads
it, never regenerates it. If a narrative (non-queryable) version of the same research
is ever useful, it originated from `RESEARCH_SUMMARY_3MONTH.md` and
`README_FOR_LLM_3MONTH.md` in the source `discord_trading_research/` project (the
original authoring standard this skill's evidence discipline is built from), but those
files are not bundled with this skill and aren't required for it to function.
