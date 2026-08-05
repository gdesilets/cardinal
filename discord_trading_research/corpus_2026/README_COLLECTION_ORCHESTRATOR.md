# Local Discord collection orchestrator

`collection_orchestrator.py` is the read-only scheduler for the Jan. 1–July 20,
2026 Discord collection. It reconciles the concrete jobs in
`working/relevance_jobs.json` against the JSON already present in:

- `raw/channel_segments/`
- `raw/relevance_segments/`
- `raw/audit_segments/`

It does **not** open Discord, call Browser, import trading knowledge, or alter a
raw segment. Its only normal outputs are disposable JSON files under `working/`.

## Products

Running a scan writes:

- `working/collection_progress_manifest.json` — every discovered segment,
  validation result, planned job/segment status, source path, exact date/query
  provenance, message counts, and page counts.
- `working/collection_next_batch.json` — the next advisory Browser action or
  count probe. This is input for a separate authenticated Browser worker; it is
  never executed by the orchestrator.
- `working/collection_orchestrator_state.json` — created only when an operator
  records a count observation or throttle event.

The state file is operational metadata, not Discord evidence. Raw Discord JSON
remains the source of truth.

## Status rules

Each planned segment and job is one of:

- `complete`: an exact query and exact date segment passed all collector count,
  page, unique-ID, gap, and container checks.
- `superseded`: a gap-free union of compatible complete captures covers the
  planned date range. A broader capture is credited only when its reported
  messages and pages are within the configured safety thresholds. An
  unfiltered full-channel capture may safely cover a narrower targeted query in
  the same channel; a targeted query can never cover an unfiltered capture.
- `partial`: a valid partial checkpoint or some compatible but incomplete date
  coverage exists.
- `pending`: no compatible local coverage exists.

Invalid JSON or a file claiming completion without fully captured pages,
messages, unique IDs, or gap-free indices appears as `invalid` in the artifact
inventory and never earns coverage.

## Adaptive targeted-query scheduling

The static plan expands to thousands of conservative date slices. The
orchestrator avoids running all of them blindly:

1. credit existing exact or safely superseding complete captures;
2. resume a safe partial checkpoint when one exists;
3. for an uncovered atomic targeted query, request one exact full-uncovered-
   window `countSearch` probe;
4. if the count is within both thresholds, recommend one broad capture;
5. if it is too large, probe/capture calendar-month slices;
6. if a month is still too large, bisect it until safe;
7. stop for review if a single local date still exceeds the threshold.

Every proposed action retains its job ID, channel ID, query string, query core,
inclusive local dates, collector export, collector arguments, expected output
path, and the count observation that justified it. A count alone never marks a
segment complete; even a stable zero must be materialized by the collector.

Defaults are 1,000 reported messages and 40 reported pages (25 results per
page). They are scheduling safeguards, not trading or Discord claims, and can
be tightened without changing raw evidence.

## Browser worker resume contract

Full-capture actions inherit `checkpointEvery: 5`, `pageDelayMs: 1200`, and
`reuseActiveSearch: true` from the validated plan. Active-search reuse is valid
only in the same browser tab and only for the exact query and unchanged
positive total. The collector independently revalidates checkpoint query,
total, page continuity, message-ID uniqueness, result-index continuity, and
container identity before appending. A mismatch is not a resumable success: it
must go through the existing fresh-count and drift-resolution path.

The reuse flag does not apply to a zero total. Verified-empty dates still
require a newly submitted search and three stable empty observations. Atomic
five-page checkpoints alter only interruption replay (at most four validated
pages); all completion and provenance gates are unchanged.

Collector 2.5 persists those observations under `completion_evidence`. Empty
segments carry exactly three timestamped `No Results` observations bound to one
fresh query submission. Positive segments carry two timestamped, matching
terminal-page observations proving the exact query, page, result-set total,
first/last indices, visible row count, and absence of an enabled Next control.
The corpus builder and orchestrator reject numeric-version complete artifacts
without valid evidence.

Pre-2.5 files are never upgraded by copying `complete=true` or synthesizing
observation timestamps. Either recapture the segment with 2.5 or run the
authenticated browser worker's exported `verifySegmentCompletionEvidence`
function. Revalidation writes a sibling
`<segment-stem>.completion-evidence.json` sidecar only when a fresh Discord
query still has the exact stored total. The sidecar binds the original segment
by SHA-256, guild, container, query, dates, total, and page count. Total drift
aborts without writing and requires ordinary recapture/drift handling.

## Commands

From the workspace root:

```powershell
python discord_trading_research\corpus_2026-01-01_2026-07-20\collection_orchestrator.py scan
```

Request up to three actions while still preferring distinct channels:

```powershell
python discord_trading_research\corpus_2026-01-01_2026-07-20\collection_orchestrator.py scan --batch-size 3
```

Change adaptive safety thresholds:

```powershell
python discord_trading_research\corpus_2026-01-01_2026-07-20\collection_orchestrator.py scan --max-messages 500 --max-pages 20
```

After a Browser worker runs the exact `countSearch` query listed in the next
batch, record its result. Page count must equal `ceil(total / 25)`:

```powershell
python discord_trading_research\corpus_2026-01-01_2026-07-20\collection_orchestrator.py record-count `
  --job-id full__1493590222703824997 `
  --start 2026-01-01 --end 2026-07-20 `
  --reported-total 24 --reported-pages 1
```

Record a Discord throttle. Global is the conservative default:

```powershell
python discord_trading_research\corpus_2026-01-01_2026-07-20\collection_orchestrator.py record-throttle `
  --scope global --cooldown-seconds 300 --reason search_error
```

Channel- and job-scoped cooldowns can keep independent work moving:

```powershell
python discord_trading_research\corpus_2026-01-01_2026-07-20\collection_orchestrator.py record-throttle `
  --scope channel --job-id full__1329615478716502097 --cooldown-seconds 300
```

The manifest records exact UTC occurrence and expiry timestamps plus remaining
seconds. Expired events remain in state as an audit trail but do not block new
work.

## Verification

```powershell
python discord_trading_research\corpus_2026-01-01_2026-07-20\test_collection_orchestrator.py
```

The tests cover exact completion, safe and unsafe supersession, unfiltered
full-capture credit for targeted work, partial checkpoints, full-window-to-
monthly adaptation, throttle expiry, progress counts, and raw-file immutability.

## Preservation boundary

The scanner hashes the jobs file and collector module for provenance. It reads
raw segment files but never rewrites, renames, deletes, or quarantines them.
Legacy artifacts outside the three canonical segment directories are not used
for release coverage and are left untouched.
