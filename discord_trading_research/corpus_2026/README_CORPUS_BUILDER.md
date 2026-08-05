# Server-wide corpus merger

`build_corpus.py` builds the immutable-message layer for the January 1–July 20,
2026 Discord corpus. It does not modify or overwrite any validated 14-day or
three-month artifact.

## Canonical inputs

- Channel search segments: `raw/channel_segments/**/*.json`
- Targeted-query segments: `raw/relevance_segments/**/*.json`
- Residual census segments: `raw/relevance_audit_segments/**/*.json`
- Exact channel/thread inventory: supplied with `--inventory`
- Discord-only relevance plan: supplied with `--relevance-plan`
- Optional read-only scheduler/review state: supplied with
  `--orchestrator-progress-manifest`
- Optional legacy provenance: supplied with `--legacy-raw`

Root-level `full_server_segments/` and `full_server_channel_segments/` are not
release inputs. Pass a different segment directory only for a deliberate test or
recovery build.

Each segment must contain:

- an exact requested channel/container ID, either in `requested_container` or
  `segment`;
- inclusive local `segment.start` and `segment.end` dates;
- the exact Discord search query;
- reported totals/pages, captured totals/pages, unique count, gap indices, and a
  `complete` flag;
- a `messages` array with message IDs, UTC timestamps, page numbers, and result
  indices.

Zero-result segments are valid when all declared counts are zero and
`complete=true`.

## Accessible-scope inventory

`full_server_channel_inventory.json` contains 38 exact top-level container IDs
from the authenticated account's visible Discord navigation/search snapshot. It
deliberately declares `inventory_complete=false`: the top-level snapshot is
represented, but it predates the final cutoff and neither forum nor ordinary
threads have been independently enumerated across the full accessible scope.

During a build, exact premium-journals thread IDs exposed by captured rows are
added to `inventory.containers` as `observed_forum_thread` records. Each record
includes its parent forum ID, the parent search used for coverage, source file
IDs, source occurrence IDs, evidence message IDs, observed dates, and title
variants. An observation proves that thread's identity and accessibility at the
time of capture; it does not prove that the forum archive was exhaustively
enumerated. Rows with no row-owned exact thread evidence remain attributed to
the parent forum and are counted under
`inventory.accessible_scope.forum_threads.unresolved_observed_occurrence_count`.

Inventory completeness is split explicitly into:

- exact top-level-container snapshot completeness;
- active/archived forum-thread enumeration completeness; and
- all-38-parent ordinary-thread applicability/active/archive completeness;
- fresh post-cutoff authenticated navigation-resnapshot completeness; and
- overall validated inventory completeness.

The overall inventory cannot validate while a forum exists and independent
thread-enumeration evidence is absent, even if a legacy input sets
`inventory_complete=true`.

## Working build

```powershell
python build_corpus.py `
  --inventory full_server_channel_inventory.json `
  --historical-reconciliation-dir raw\quarantine_collection_errors `
  --legacy-raw ..\raw_discord_export_3month.json
```

This atomically refreshes:

- `raw_corpus_working.json`
- `coverage_manifest_working.json`

A working artifact is always labelled `partial`, even if its current inputs pass
all release gates.

Use `--dry-run` to validate without writing. `--segment-dir` may be repeated;
when omitted it uses the canonical `raw/channel_segments/` directory.

For the policy-aware build, use:

```powershell
python build_corpus.py `
  --inventory full_server_channel_inventory.json `
  --relevance-plan relevance_collection_plan.json `
  --relevance-segment-dir raw\relevance_segments `
  --audit-segment-dir raw\relevance_audit_segments `
  --orchestrator-progress-manifest working\collection_progress_manifest.json `
  --historical-reconciliation-dir raw\quarantine_collection_errors `
  --dry-run
```

The progress manifest can locate the raw files listed in its `artifacts` array,
so the two policy segment-directory flags are optional when those exact files
are present. The raw JSON is still opened, validated, hashed, and ingested. A
progress status never substitutes for raw evidence.

## Release build

```powershell
python build_corpus.py `
  --inventory working/full_server_channel_inventory_complete.json `
  --historical-reconciliation-dir raw\quarantine_collection_errors `
  --legacy-raw ..\raw_discord_export_3month.json `
  --data-cutoff-utc 2026-07-21T05:00:00Z `
  --release
```

Release mode writes `raw_corpus_release.json` and
`coverage_manifest_release.json` exclusively—it never overwrites them. If any
strict gate fails, it exits with code 2 and writes neither release file.

Required release gates include:

- the exact 201-day America/Chicago window;
- a finished data cutoff (`2026-07-21T05:00:00Z` or later);
- a complete, valid exact-container inventory;
- full local-date coverage for every accessible/searchable message container;
- strictly complete or verified-empty segment files with no global gaps;
- a trusted channel recapture for every critical quarantined legacy message;
- valid byte-bound reconciliation notes for historical Discord messages that are absent from a later exact-scope recapture; these rows remain searchable but analysis-ineligible and carry no inferred deletion/edit cause;
- byte size, SHA-256, and portable relative path for every source file;
- preservation of every legacy message ID when legacy provenance is supplied.

When `--relevance-plan` is supplied, the generic all-channel completeness gate
is replaced by the validated policy contract:

- all 16 `full_capture` channels are message-complete and count-reconciled;
- all 22 `verified_empty_full_window` channels have complete, count-reconciled
  zero captures;
- `newsfeed`, chat, and `levels` are full-capture channels; targeted-query and
  residual artifacts are supplemental only and cannot satisfy completeness;
- reply/context, attachment/chart-dependence, Discord-only, calibrated-claim,
  overlap-provenance, final-window, and exact-inventory gates pass; and
- `premium-journals` retains strict active/archived thread inventory, exact
  parent/thread IDs, and exact message permalinks. A collector
  `exact_permalink_conflict_detected=true` blocks release; and
- ordinary-thread applicability is audited for all 38 parents, while a fresh
  authenticated top-level navigation resnapshot proves the final post-cutoff
  roster.

A partial capture of any full-capture channel fails release. Legacy diagnostic
targeted artifacts remain preserved when present but cannot satisfy a job,
count-reconciliation, or message-completeness requirement.

### Review and reconciliation evidence

The orchestrator manifest may be augmented with a `release_evidence` object.
Collection job status alone is intentionally insufficient for final release.
The builder requires:

- `outside_sources_used: 0`;
- `full_capture_count_reconciliation`: one row for every full/empty channel,
  with `status=passed`, equal `segment_reported_total` and
  `refreshed_full_window_reported_total`, an observation time at or after the
  final window cutoff, and at least one observation/source/segment evidence
  reference;
- `residual_reviews`: no rows for the canonical zero-targeted plan. If a future
  plan includes supplemental audit jobs, each planned packet must be reviewed
  completely and cannot weaken the full-capture contract;
- `reply_resolution`: equal `selected_question_count` and
  `resolution_status_count`, with zero questions lacking a status, zero direct
  linkage errors, zero adjacent-context promotions, and evidence references.
  A raw direct target is accepted only when
  `reply_to_message_id_source=owned_reply_context_descendant_content_id`,
  `reply_target_scope_exact=true`, `reply_target_content_id` matches the target,
  and `reply_to_channel_id`/`reply_to_permalink` agree. Preview-only links or
  text remain context and fail direct-linkage certification;
- `attachments_and_chart_dependence`: zero reply-preview media leaks and zero
  unlabeled chart-dependent records, with evidence references. Any chart-dependent
  accepted/qualified claim also requires a complete/partial extraction artifact whose
  local bytes were verified; failed/no-artifact rows never count; and
- when normalized claims are present, `claim_calibration` with zero unsupported
  or uncalibrated probability claims. The raw corpus emits no normalized
  claims, so this last gate is recorded as not applicable at that layer.

When owned Discord attachments exist, pass the manifest and archive root described in
`README_DISCORD_ATTACHMENT_ARCHIVE.md`. Release requires exact attachment-set parity,
rehashing of every downloaded attachment and complete/partial extraction artifact,
and `literal_release_complete=true`. A substantiated 404/410/Discord-unavailable row
may remain an explicit byte gap; a terminal `failed` row is degraded and blocks
release even after three documented attempts.

## Corpus contract

The corpus JSON contains:

- `scope`: Central-local and UTC half-open boundaries;
- `release`: status, cutoff, readiness, and completeness-through time;
- `inventory`: normalized exact top-level containers plus provenance-backed
  forum thread IDs observed in captured rows, with accessible-scope and
  completeness fields;
- `relevance_policy`: per-channel completion labels, all 38 job-coverage
  evaluations, count/review evidence, and every hard plan gate;
- `orchestrator_progress`: only the hashed manifest identity and compact
  summary; raw release evidence is normalized into `relevance_policy`;
- `source_files`: portable relative path, byte size, and SHA-256;
- `segments`: channel/query/date/page validation and completeness;
- `messages`: one globally unique row per valid Discord message ID;
- `occurrences`: every source occurrence with its full payload and normalized
  channel/query/segment/page provenance;
- `quarantine`: timestamp/container/ID discrepancies plus imported migration
  quarantine reasons;
- `migration_quarantine_sidecars`: sidecar source hashes, matched/unmatched
  occurrence counts, invalid records, and normalized reasons;
- `legacy_provenance`: reconstructed three-month source membership that never
  contributes to server-wide coverage;
- `field_conflicts`: every message field with multiple captured values;
- `release_gates` and `counts`.

Each canonical message contains:

- `canonical_created_at_utc`, derived from the Discord snowflake;
- `_timestamp_audit`, including captured variants and mismatch IDs;
- `_field_variants`, where every value links to its source occurrence IDs;
- `_corpus_provenance`, with every occurrence, source file, query, segment, and
  collection;
- explicit quarantine state;
- `evidence_trust_state` and `eligible_for_accepted_evidence`; and
- trusted-canonical and quarantined occurrence counts.

Migrated occurrences remain fully searchable and retain their payloads. They
are not analysis-eligible unless the same Discord message ID also has a
separate, non-migration, non-quarantined `channel_segment` occurrence. That
trusted recapture becomes the canonical message payload; the migrated variant
and its quarantine reasons remain in `occurrences` and `quarantine`.

No post-capture relevance filter is applied: every returned row remains in the
immutable corpus and can be hidden later by an analysis view without losing
source data. Under the relevance plan, every nonempty channel has a full-capture
policy; no targeted acquisition label can substitute for message completeness.

## Manifest shapes

- `inventory` is an object whose normalized records are in
  `inventory.containers`.
- `coverage` is an object with `coverage.segments`, `coverage.containers`,
  `coverage.gaps`, `coverage.summary`, and `coverage.file_failures`.

This shape is the handoff contract for `build_cardinal_database_v2.py`.

## Tests

```powershell
python -m unittest -v test_build_corpus.py test_relevance_release_policy.py
```

The tests cover Central daylight-saving boundaries, verified-empty segments,
partial date gaps, release refusal, snowflake mismatch quarantine, occurrence
retention, field variants, legacy provenance expansion, forum-thread identity
derivation, unresolved forum evidence, and refusal to certify a forum inventory
without archive-enumeration proof. They also cover inline migration flags,
auto-discovered JSONL sidecar flags, and independent canonical recapture.
Policy tests additionally cover targeted diagnostic partials, exact targeted
query/date coverage, refreshed count provenance, forum group-header parent
resolution, exact-thread precedence over a generic parent `channel_id`, and
permalink-conflict rejection.
