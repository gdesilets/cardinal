# Independent corpus QA

These tools are isolated from the existing 14-day and three-month pipeline.
They never edit source artifacts and refuse to overwrite their own manifests or
reports.

## Scope contract

- Guild: `1167376964680691732`
- Calendar: `America/Chicago`
- Local dates: January 1 through July 20, 2026, inclusive
- UTC interval for a full window: `2026-01-01T06:00:00Z` through
  `2026-07-21T05:00:00Z`, end-exclusive
- Canonical raw input: `../raw/channel_segments/`
- All derived evidence must be Discord-only.

The validator implements the 2026 US Central daylight-saving transition itself
so it does not require a network-installed timezone package on Windows.

## 1. Protect established artifacts

Create this once before the new build:

```powershell
python preservation_hashes.py snapshot `
  --root ../../ `
  --output existing_artifacts_baseline.sha256.json
```

The default selection includes both existing databases, merged exports,
analytical JSON, reports, documentation, and all completed three-month segment
and supplemental JSON files. Live full-server capture paths are excluded.

Verify at any later point:

```powershell
python preservation_hashes.py verify `
  --manifest existing_artifacts_baseline.sha256.json `
  --output preservation_verification_after.json
```

## 2. Validate the canonical corpus

`--segments` defaults to `../raw/channel_segments/`. Production validation also
requires an exact channel/thread inventory, the new all-message SQLite database,
an explicit collection cutoff, and the preservation baseline:

After collection is frozen, create a write-once hash manifest covering exactly
the canonical segment set:

```powershell
python preservation_hashes.py snapshot `
  --root ../raw `
  --artifact channel_segments `
  --artifact relevance_segments `
  --artifact relevance_audit_segments `
  --output canonical_source_hashes.sha256.json
```

All three `--artifact` arguments are required for a relevance-plan release so
the write-once manifest exactly matches the full-capture, targeted-query, and
residual-audit segment set selected by the validator.

```powershell
python validate_corpus.py `
  --relevance-plan ../relevance_collection_plan.json `
  --relevance-segments ../raw/relevance_segments `
  --audit-segments ../raw/relevance_audit_segments `
  --orchestrator-progress-manifest ../working/collection_progress_post_final_evidence.json `
  --drift-audit ../working/collection_drift_final.json `
  --inventory ../working/full_server_channel_inventory_complete.json `
  --database ../final/cardinal_analyzed.sqlite `
  --preservation-manifest existing_artifacts_baseline.sha256.json `
  --source-hash-manifest canonical_source_hashes.sha256.json `
  --data-cutoff-utc 2026-07-21T05:00:00Z `
  --output validation_report.json
```

The output is write-once. A release with any critical failure exits with status
1 and is labeled `needs_revision`. `--allow-failures` is intended only for an
inspectable smoke report from an incomplete capture; it does not change or hide
failed checks.

With the relevance plan enabled, QA uses the same fail-closed policy evaluator
as the corpus builder. It independently validates the raw files rather than
trusting orchestrator statuses. The progress manifest may be used alone to
locate its provenance-listed raw artifacts, or the targeted/audit paths may be
provided explicitly.

The report requires strict message completeness for all 16 nonempty channels
and independently verified emptiness for 22 channels. Supplemental targeted or
residual artifacts are hashed and inspected when present but never satisfy or
weaken completeness. The 38 canonical jobs, refreshed counts, post-cutoff
authenticated navigation, all-parent ordinary-thread audit, reply/context
review, attachment/chart review, and all plan hard gates remain critical.

Diagnostic root-level captures must be passed explicitly and are never included
by the default command:

```powershell
python validate_corpus.py `
  --segments ../../full_server_segments `
  --segments ../../full_server_channel_segments `
  --preservation-manifest existing_artifacts_baseline.sha256.json `
  --output diagnostic_partial_smoke.json `
  --allow-failures
```

## Checks

The independent validator recomputes:

- inventory-backed channel/thread and 201-day coverage;
- valid completed zero-result segments;
- search totals, 25-result pages, captured rows, unique IDs, and contiguous indices;
- SHA-256 for each input and stability throughout the run;
- Central-local segment and requested-window bounds;
- the partial-live-day cutoff for July 20;
- message-ID/snowflake versus DOM timestamps;
- duplicate occurrences, edits, and unresolved field variants;
- explicit reply targets, resolution state, temporal order, and cycles;
- attachment ownership, Discord URLs, timing, and capture/extraction status;
- SQLite integrity, foreign keys, all-message/source parity, FTS parity, and attachment parity;
- exact external-inventory/database parity and full-window database coverage units;
- separate message, attachment-extraction, and claim FTS parity against their own source tables;
- evidence foreign keys, excerpt traceability, evidence coverage, and Discord-only assertions;
- preservation hashes both before and after validation.
- a separate final collection-drift `PASS`, generated after the full window,
  with zero structural failures, unresolved replacement chains, or orphan
  quarantined partials; the drift report is hashed and rechecked for stability;
- exact policy counts (16 full, 22 empty, 0 targeted), all 38 canonical jobs,
  and provenance-backed count reconciliation;
- post-cutoff authenticated top-level navigation and one ordinary-thread audit
  for each of the 38 exact parent IDs;
- strict forum group-header parent/thread/permalink resolution, including a
  hard failure for `exact_permalink_conflict_detected=true`.
- row-owned reply-target proof from
  `owned_reply_context_descendant_content_id`, exact target scope/content ID,
  and a matching target channel/permalink; preview-only links never qualify as
  resolved answers.

Run unit tests with:

```powershell
python -m unittest -v test_qa_tools.py
```

The shared policy regression suite is run from the package root:

```powershell
python -m unittest -v test_relevance_release_policy.py
```

The canonical v2 database builder has a separate regression suite:

```powershell
cd ..
python -m unittest -v test_cardinal_database_v2.py
```
