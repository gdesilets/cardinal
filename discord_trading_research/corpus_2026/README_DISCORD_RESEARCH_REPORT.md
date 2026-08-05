# Discord research report generator

`build_discord_research_report.py` turns the release-ready Cardinal SQLite
database into two deterministic, audit-friendly artifacts:

- a detailed Markdown research report for reading;
- structured JSON for retrieval and question answering by another LLM.

The generator reads only the analyzed SQLite database produced by
`build_discord_analysis_layer.py`. It never reads raw Discord exports, legacy
analysis files, websites, market data, or outside trading references. The input
database is opened read-only and its SHA-256 is checked before any result is
accepted.

## Final command

Run this only after the final analyzed database has passed collection, corpus,
and database QA:

```powershell
python .\build_discord_research_report.py `
  --database .\cardinal_discord_2026-01-01_2026-07-20_analyzed.sqlite `
  --markdown-output .\discord_trading_research_2026-01-01_2026-07-20.md `
  --json-output .\discord_trading_research_2026-01-01_2026-07-20.json
```

Add `--replace` only when intentionally rebuilding both report artifacts from a
newly validated database.

The default scope is deliberately fixed to this research release:

- guild: `1167376964680691732`;
- start: `2026-01-01T06:00:00Z` (January 1 at midnight Central Standard Time);
- end: `2026-07-21T05:00:00Z` (the end of July 20 at midnight Central Daylight
  Time).

The three `--expected-*` options exist for controlled tests or a separately
authorized future release. They should not be changed for this task.

## What the reports contain

Both outputs cover the same evidence and reading path:

1. Discord-only scope, window, channel inventory, collection units, coverage
   segments, message counts, and source-artifact hashes.
2. Rejection-block identification claims and textual components.
3. Technical invalidation, non-actionability, and combined/unclassified
   evidence in separate buckets. Classification uses stored source facets only;
   the generator never guesses from trading language.
4. Explicit setup-time and session mentions. Discord post timestamps are
   provenance only and are never substituted for setup time.
5. Higher and lower selected-corpus confluence associations with wins, losses,
   denominators, baseline difference, author concentration, and evidence links.
6. Strict executed-trade win and loss profiles plus a trade-level evidence
   catalog.
7. NQ/ES executed-role comparisons, RB-only executed-role comparisons, and
   market-context mentions as separate evidence types. Exact-symbol role
   evidence is included without adding an unstored symbol-family mapping.
8. Zero to five Discord-supported model cards. A fifth model is never forced.
   The report exposes preserved-versus-full-window-discovered origin, the
   exhaustive candidate audit, deterministic promotion safeguards, stored
   signature, author concentration, unresolved entry/invalidation/target
   facets, and retained counterevidence. Model evidence resolves to exact
   message-specific Discord permalinks.
9. Relevant answered, partial, ambiguous, conflicting, and unanswered Q&A with
   direct-reply/linkage metadata, authority classification, message IDs, and
   permalinks.
10. Stored contradiction sets, limitations, evidence-bounded next steps, and a
    global message/evidence-item catalog.

The JSON preserves full objects and evidence references. Any
`evidence_ref` such as `message:1456185169361768581` resolves in
`evidence_catalog` to the message ID, Discord permalink, exact channel/thread,
author key, posted-at timestamp, excerpt, trust state, and eligible
`evidence_item_ids`.

## Interpretation contract

Every outcome rate is labeled as:

- descriptive within the selected Discord corpus;
- self-reported;
- overlapping where confluences or models overlap;
- author-clustered;
- non-causal;
- not a forward probability or expectancy.

“Higher” and “lower” therefore mean only above or below the stored strict-cohort
baseline after applying the analysis layer's denominator rule. They do not mean
that a setup is objectively high-probability or low-probability in a market.

## Fail-closed release gates

No report files are written when any required condition fails. The generator
checks:

- all required Cardinal analysis tables and views exist;
- SQLite integrity and foreign keys pass;
- the database, collection run, analysis run, and methodology are all labeled
  `discord_only` with `outside_sources_used = 0`;
- there is exactly one analysis run and all required analysis documents belong
  to it;
- the collection run and analysis coverage document are `complete`;
- the coverage document and `v_collection_gaps` contain zero gaps;
- `v_discord_only_audit` contains zero issues;
- the guild and exact UTC boundaries match this release;
- the timing policy explicitly forbids inferring setup time from message-post
  timestamps;
- the invalidation policy preserves technical invalidation and
  non-actionability separately;
- executed-instrument and market-context result sets both exist and remain
  separate;
- the model document, model table, and declared count agree and contain no more
  than five models;
- every answered question has an answer link;
- strict trade counts in the profile document exactly reconcile to strict
  win/loss rows in SQLite;
- every report evidence message has an exact message ID, a message-specific
  Discord permalink, accepted-evidence eligibility, and at least one eligible
  Discord-only evidence item.

The current partial working database is expected to fail these gates. That is a
feature: it prevents a provisional capture from looking like the final
January-through-July release.

## Determinism and safety

For the same database bytes, the Markdown and JSON bytes are stable:

- SQL queries and report lists have explicit sorting;
- JSON keys are sorted and UTF-8 output uses normalized newlines;
- the analysis run timestamp is used instead of the current clock;
- the database SHA-256 is embedded in both handoff artifacts;
- both outputs are staged through same-directory temporary files;
- existing outputs are preserved unless `--replace` is supplied;
- the input database is never modified.

On success, the command prints a compact JSON receipt containing the input and
output hashes, analysis-run ID, evidence-message count, model count, and Q&A
status counts.

## Tests

```powershell
python -m unittest -v test_build_discord_research_report.py
```

The tests exercise deterministic output, all requested report sections,
source-facet-only invalidation classification, partial-release blocking,
Discord-only audit blocking, untrusted-evidence blocking, zero-model behavior,
and the five-model maximum.
