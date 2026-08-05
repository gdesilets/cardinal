# Discord collection-drift audit

`audit_collection_drift.py` is a local, read-only release gate for Discord
search-total changes observed while a segment is being paginated. It does not
collect messages, open links, alter quarantine files, or write anywhere under
`raw/`. Its sole output is an atomically replaced JSON report below `working/`.

The intended grain is one Discord search-total drift event, grouped into a
chronological drift chain for one exact guild/channel/date-window/query.

## What it proves

For every `*.total-drift-note.json` in
`raw/quarantine_collection_errors`, the audit verifies:

- the exact supported note schema, Discord guild/channel snowflakes, corpus
  date window, and one-day-exclusive Discord query boundaries;
- non-negative integer totals and checkpoint metrics, with
  `old_reported_total != new_reported_total`;
- strict UTC observation ordering (`old < new`) and a checkpoint capture time
  equal to the old-total observation;
- `outside_sources_used` is exactly `false`;
- safe, root-relative provenance paths in the expected raw directories;
- existence and SHA-256 identity of the referenced quarantined checkpoint;
- checkpoint row, page, unique-message, and result-index-gap metrics against
  both its JSON declarations and recomputed values;
- exact Discord message snowflakes and snowflake timestamps, with each message
  falling in the segment's America/Chicago calendar window;
- an unbroken chain when a replacement itself experiences another total drift;
- one and only one canonical replacement, with no simultaneous stale/current
  complete/partial candidate;
- a final canonical artifact marked complete at the latest total, containing
  exactly continuous result indices, unique message IDs, all pages, empty gap
  indices, matching per-row result-set sizes, and zero container mismatches;
- no orphan `*.partial.json` files in the collection-error quarantine.

A drift note may include one optional top-level `diagnostics` object. It must be
non-empty and may contain only `missing_result_index_before_recount`,
`missing_result_index_before_rerender`, and/or
`transient_zero_recount_observed`. Missing-index values must be positive
integers no greater than `old_reported_total`; the transient-zero value must be
a JSON boolean. Arbitrary diagnostic keys and untyped values are rejected. The
established legacy top-level
`missing_result_index_before_recount` field remains accepted for existing
notes, but new collector notes should use the nested object. A legacy recount
diagnostic cannot be duplicated in both locations.

For example:

```json
"diagnostics": {
  "missing_result_index_before_rerender": 775,
  "transient_zero_recount_observed": true
}
```

The audit also supports a second, deliberately separate event type for a
malformed checkpoint that is **not** a valid search-total drift chain:
`collection_error_resolution`. A valid resolution hashes and profiles the
invalid quarantine file, reproduces its complete defect set, and independently
binds one clean canonical replacement by path, SHA-256, metrics, continuity,
and chronology. It appears only in `collection_error_resolutions` in the
report; it never increments drift-note or drift-chain counts.

## Exact non-drift resolution schema

The filename must end in `.collection-error-resolution.json`. The top-level
object must contain exactly these keys:

```text
event_type
schema_version
guild_id
channel_id
channel_name
segment_start
segment_end
query
resolved_at_utc
invalid_artifact_path
invalid_artifact_sha256
invalid_artifact_metrics
defects
canonical_replacement_path
canonical_replacement_sha256
canonical_replacement_metrics
resolution_action
outside_sources_used
```

Fixed values are:

```text
event_type = collection_error_resolution
schema_version = 1.0.0
resolution_action = quarantined_malformed_checkpoint_and_verified_complete_canonical_replacement
outside_sources_used = false
```

Both metric objects must contain exactly:

```text
collector_version
reported_total
reported_pages
pages_captured
captured_rows
unique_message_ids
gap_indices
container_mismatch_count
complete
```

`invalid_artifact_metrics.complete` must be `false` and
`canonical_replacement_metrics.complete` must be `true`. All other metric
values must equal the referenced JSON files exactly. The invalid path must be a
quarantined `.partial.json`; the canonical path must be a non-partial `.json`
under `raw/channel_segments`. Both hashes are lowercase SHA-256.

The defects array is compared as an order-independent but otherwise exact set
against defects recomputed from the invalid file. Supported reproducible
defects include duplicate message IDs (with occurrence count and result
indices), duplicate/missing result indices, declared-vs-computed gap mismatch,
mixed result-set sizes with row counts, declared/computed row or unique-count
mismatch, observed-page mismatch, reported-page mismatch, and container
mismatch. Omitting a defect, inventing one, or changing any detail fails the
note and leaves the quarantine artifact orphaned.

Example shape (placeholders are intentional and are not valid final evidence):

```json
{
  "event_type": "collection_error_resolution",
  "schema_version": "1.0.0",
  "guild_id": "1167376964680691732",
  "channel_id": "1329615478716502097",
  "channel_name": "Live",
  "segment_start": "2026-01-20",
  "segment_end": "2026-01-20",
  "query": "in:Live after:2026-01-19 before:2026-01-21",
  "resolved_at_utc": "<AT_OR_AFTER_FROZEN_CANONICAL_CAPTURE>",
  "invalid_artifact_path": "raw/quarantine_collection_errors/<INVALID>.partial.json",
  "invalid_artifact_sha256": "<64_LOWERCASE_HEX>",
  "invalid_artifact_metrics": {
    "collector_version": "2.0",
    "reported_total": 648,
    "reported_pages": 26,
    "pages_captured": 11,
    "captured_rows": 274,
    "unique_message_ids": 273,
    "gap_indices": [],
    "container_mismatch_count": 0,
    "complete": false
  },
  "defects": [
    {
      "code": "duplicate_message_id",
      "message_id": "1463199570933584093",
      "occurrences": 2,
      "result_indices": [200, 201]
    },
    {"code": "missing_result_index", "result_index": 100},
    {"code": "declared_gap_indices_mismatch", "declared": [], "computed": [100]},
    {
      "code": "mixed_result_set_sizes",
      "counts": [
        {"reported_total": 647, "rows": 50},
        {"reported_total": 648, "rows": 224}
      ]
    }
  ],
  "canonical_replacement_path": "raw/channel_segments/<POST_RECAPTURE>.json",
  "canonical_replacement_sha256": "<POST_RECAPTURE_64_LOWERCASE_HEX>",
  "canonical_replacement_metrics": {
    "collector_version": "<POST_RECAPTURE_VERSION>",
    "reported_total": 647,
    "reported_pages": 26,
    "pages_captured": 26,
    "captured_rows": 647,
    "unique_message_ids": 647,
    "gap_indices": [],
    "container_mismatch_count": 0,
    "complete": true
  },
  "resolution_action": "quarantined_malformed_checkpoint_and_verified_complete_canonical_replacement",
  "outside_sources_used": false
}
```

The January 20 invalid-file values above are a diagnostic snapshot. The
canonical placeholders must be populated only after the planned replacement
recapture is complete and frozen; an earlier v2.0 canonical hash must not be
copied into final provenance.

America/Chicago conversion uses the applicable US daylight-saving transition
rule directly, so this audit does not depend on an online timezone service or a
host `tzdata` package.

## Status and exit codes

The report has one of three statuses:

| Status | Exit | Meaning |
|---|---:|---|
| `PASS` | 0 | Every drift chain is structurally valid and fully resolved; no orphan partial remains. |
| `FAIL` | 1 | Structural evidence failed, or final mode found any unresolved item. |
| `PENDING` | 2 | Collection mode found valid but unfinished replacement/orphan work. |

Structural problems such as an invalid note, bad hash, disconnected chain,
malformed artifact, duplicate canonical candidate, or metric mismatch fail in
both modes. Collection mode only permits unfinished resolution to be reported
as `PENDING`; it never silently treats pending evidence as passed.

## During collection

Run from this corpus directory:

```powershell
python audit_collection_drift.py `
  --root . `
  --mode collection `
  --output working/collection_drift_audit.json
```

The script refuses to overwrite that report. An intentional rerun must say so:

```powershell
python audit_collection_drift.py `
  --root . `
  --mode collection `
  --output working/collection_drift_audit.json `
  --overwrite
```

## Exact final release command

Stop collection first and freeze the selected raw inputs. Then run this as an
independent, narrow QA gate alongside the main corpus validator:

```powershell
python audit_collection_drift.py `
  --root . `
  --mode final `
  --window-start 2026-01-01 `
  --window-end 2026-07-20 `
  --output working/collection_drift_audit.final.json
if ($LASTEXITCODE -ne 0) { throw "Discord collection-drift release gate failed" }
```

For an intentional repeat, add `--overwrite`. Release is permitted only when
the command exits `0`, `overall_status` is `PASS`, `release_gate_passed` is
`true`, and both `summary.structural_failure_count` and
`summary.unresolved_count` are zero. Include the final report in the release
manifest and hash it after generation. Do not substitute a collection-mode
`PENDING` report for the final gate.

This is intentionally documented as a standalone optional QA input instead of
editing `qa/validate_corpus.py` while that pipeline is active.

## Report safety

- The output must resolve beneath this corpus's `working/` directory.
- Existing output is preserved unless `--overwrite` is explicit.
- The report is written to a same-directory temporary file, flushed, and then
  atomically moved into place.
- Raw collector artifacts and quarantine evidence are only opened for reading.
- Message links and attachment URLs are recorded evidence only; the audit never
  fetches them and never consults non-Discord sources.

## Tests

```powershell
python -m unittest -v test_audit_collection_drift.py
```

The focused tests cover resolved and chained drift, pending/final behavior,
schema and snowflake checks, observation order, query windows, source hashes,
checkpoint metrics, canonical continuity and uniqueness, stale/current
ambiguity, orphan quarantine files, non-drift malformed-checkpoint resolutions,
exact defect evidence, dual artifact hashes, atomic output, overwrite refusal,
and the `working/` write boundary.
