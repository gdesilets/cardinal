# Discord-only relevance collection plan

This package defines how to cover the entire accessible Discord server from
January 1 through July 20, 2026 while leaving unimportant conversation out of
the curated trading layer. It does not use web research, generic trading
knowledge, or Cardinal content. Cardinal is only a downstream database consumer.

The machine-readable source of truth is
[`relevance_collection_plan.json`](relevance_collection_plan.json). Its exact
38-channel roster is locked to
[`full_server_channel_inventory.json`](full_server_channel_inventory.json).
That file is a pre-cutoff planning baseline, not final completeness evidence;
release requires `raw/post_cutoff_top_level_inventory.json`.

## What “entire Discord” means

Every one of the 38 accessible top-level containers has exactly one policy:

| Policy | Channels | Treatment |
| --- | ---: | --- |
| Full capture | 16 | Capture every in-window message, including `newsfeed`, chat, `levels`, journals, questions, Live, vc, outlooks, breakdowns, announcements, and optional teachings. |
| Verified empty | 22 | Run a complete full-window channel query and preserve a collector-validated zero result. These channels have no reported in-window messages; older evergreen material is outside the requested date window. |
| Targeted search plus residual audit | 0 | Retained only as supplemental research vocabulary; it cannot satisfy completeness. |

High volume does not weaken the contract: `newsfeed`, chat, and `levels` are
message-complete full captures. Query-family and residual artifacts may help
curation, but they are supplemental and cannot replace any full-capture segment.

“Leave off unimportant conversations” is implemented only in the curated
layer. Captured messages are never deleted because they were later judged
irrelevant. Every exclusion keeps a reason code, and ambiguous records go to an
audit queue.

## Query vocabulary and evidence discipline

The plan contains nine query families and 94 atomic searches:

1. rejection-block core terms;
2. rejection-block identification;
3. invalidation versus non-actionability;
4. timing and sessions;
5. confluences and high/low-probability wording;
6. trade outcomes and execution;
7. model rules and trade management;
8. NQ/MNQ versus ES/MES RB references;
9. rejection-block questions.

Every atomic query cites one or more locked source locators in the existing
Discord-derived analysis scripts or evidence artifacts. The validator checks
those references and their SHA-256 hashes. New terms may be added only after
they are observed in captured Discord text or an existing Discord artifact,
with that source added to the registry. External URLs embedded in Discord posts
may be stored but must not be opened or used as evidence.

The searches are retrieval aids, not trading claims. A co-mention does not prove
a confluence helped a trade, a Discord post timestamp does not establish setup
time, and NQ/ES mention counts do not establish instrument superiority.

## Full-capture high-signal containers

The plan captures all in-window messages from:

- `newsfeed`
- `⚫│boy`
- `premium-announcements`
- `📍│chat`
- `❓│questions`
- `levels`
- `student-breakdowns`
- `premium-journals`
- `Live`
- `vc`
- the six positive, low-volume optional-teaching channels

The other 22 channels receive complete zero-result verification. Full-capture
segment sizes are channel-specific: one day for the largest high-signal
containers, seven or fourteen days for moderate containers, and one full-window
segment for zero or tiny containers.

## Questions, answers, and context

Questions and answers are stored as linked records, not flattened prose.

- A direct answer must have an exact `reply_message_id` path to the question or
  one of its reply-chain ancestors.
- Reply chains are resolved recursively and keep every hop.
- Nearby messages can be captured as context, but adjacency alone is labeled
  `community_adjacent_context`, never an authoritative answer.
- Missing, deleted, inaccessible, or cross-container reply targets remain
  `unresolved_reference`.
- Named-mentor direct replies, community direct replies, adjacent context, and
  unresolved questions remain separate authority states.
- Attachment-dependent or deictic answers are marked `chart_dependent` and are
  not universalized.

Answered questions retain both sides of the exchange. Nonresponsive replies are
kept as context with `responsive=false` rather than silently discarded.

## Forum-thread handling

`premium-journals` is a forum, so the parent channel ID is not enough. The plan
requires a separate `raw/forum_thread_inventory.json` covering active threads,
discoverable archived threads, and threads with in-window replies even when the
starter predates January 1.

Every forum message must resolve to:

- the exact parent forum ID;
- the exact thread channel ID from its row-owned forum-card identifier, owned
  reply permalink, or a verified `forum_group_header_navigation_exact` record
  keyed by exact query, page, and group message-ID membership;
- an exact message permalink;
- the thread title as descriptive metadata only;
- active/archived state and enumeration method.

Duplicate thread titles are never treated as the same thread. Any
`thread_id_unresolved` row blocks release. A channel snowflake found only in
an attachment/CDN path is retained as unverified locator evidence and cannot
certify the owning thread or message container. Forum navigation evidence is
accepted only when a unique direct-child group header opens an exact Discord
guild/thread URL and Browser Back restores the same query, page, and exact
message-ID membership; titles never participate in the key.

Ordinary/public threads are not inferred absent. The final browser inventory
must audit thread applicability for all 38 top-level parents and complete both
active and discoverable-archive passes for every applicable parent. Exact
thread URLs are keys; titles and attachment paths are not. See
`README_THREAD_INVENTORY.md`.

## Supplemental residual audits

The canonical release plan schedules no targeted or residual-audit jobs because
all 16 nonempty channels are full capture. If supplemental audit artifacts are
created for research convenience, review every unmatched message. If a reviewer
finds a relevant concept that the query families missed:

1. cite the Discord message or existing Discord-derived artifact containing the
   new term;
2. add the term and source reference to the plan;
3. rerun the affected query family for the relevant channels and the entire
   window;
4. repeat residual review before release.

The census is a deterministic coverage audit, not a statistical estimate of
server content.

## Collector compatibility

Each expanded job maps directly to `collectDateRange` in
`discord_browser_collector.mjs`. The canonical plan expands to:

- 38 full-capture or empty-verification jobs;
- zero targeted-search jobs;
- zero residual-audit jobs;
- 38 jobs and 1,315 date segments total.

From the workspace root, validate the plan:

```powershell
python discord_trading_research\corpus_2026-01-01_2026-07-20\qa\validate_relevance_plan.py
```

To inspect the concrete collector calls without starting browser work:

```powershell
python discord_trading_research\corpus_2026-01-01_2026-07-20\qa\validate_relevance_plan.py --emit-expanded-jobs -
```

The emitted payload supplies `startIso`, `endIso`, `outputDirectory`,
`queryPrefix`, `spanDays`, `collectorOptions`, and `schedulerOptions` exactly as
the collector expects. It does not execute searches by itself.

Every full-capture job now emits `checkpointEvery: 5`, `pageDelayMs: 1200`,
and `reuseActiveSearch: true`. Reuse is only a same-tab resume optimization:
the collector accepts it only when the active query is exact and its positive
reported total still matches the partial checkpoint. Before appending a row it
also requires contiguous captured pages, unique message IDs, gap-free result
indices, and the exact requested container. A changed query, changed total, or
broken index sequence leaves the checkpoint untouched and requires a fresh
recount/reconciliation. Zero-result searches are never reused; they still
submit the query and require three stable empty observations.

Five-page atomic checkpoints can replay at most four already validated pages
after an interruption. They do not relax per-page or final reported-total,
message-ID, order, reply, attachment, provenance, gap, container, or drift
checks. The 1.2-second page delay is the collector's established default; any
throttle-like state still stops the batch and retains the five-minute cooldown
recommendation.

Run the tests:

```powershell
python -m unittest discord_trading_research\corpus_2026-01-01_2026-07-20\qa\test_relevance_plan.py
```

## Release gates

A final release is blocked unless all hard gates pass. The most important are:

- exact 38-channel inventory coverage;
- a final cutoff at or after July 21, 2026 05:00 UTC, so July 20 Central is
  complete;
- a fresh post-cutoff authenticated navigation resnapshot of all 38 exact
  top-level IDs, with terminal-state and source-reference proof;
- an authenticated ordinary-thread applicability, active, and
  discoverable-archive audit for every one of the 38 parents;
- gap-free local-date segments with exact per-segment counts;
- refreshed full-window count reconciliation for all full-capture channels;
- exact forum thread IDs and permalinks;
- resolved or explicitly unresolved reply states;
- attachment and chart-dependence labeling;
- retained query-overlap provenance;
- `outside_sources_used = 0`;
- separation of explicit Discord rules, observed associations, synthesis, and
  insufficient evidence.

Guild-wide searches may be used as a diagnostic reconciliation pass when
Discord returns a stable result. They are not the completeness backbone because
the prior collection showed broad guild searches can be unstable.

## Database handoff

The downstream database should retain all captured message text and full-text
search, query occurrences, coverage, forum threads, relevance decisions,
reply edges, Q&A, trade outcomes, confluences, claims, and unresolved/quarantine
records. Every normalized Cardinal-facing setup field must link back to Discord
message IDs and exact permalinks when resolvable.

Cardinal-facing fields are limited to organization and retrieval: instrument
role, stated setup time/session, bias, location/array, liquidity context,
confirmation, entry, risk/invalidation, target, outcome, and failure mode. Their
values must come from Discord evidence; the schema does not authorize adding
outside market rules.
