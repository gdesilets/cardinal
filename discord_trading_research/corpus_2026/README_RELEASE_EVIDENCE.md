# Local release-evidence generator

`build_release_evidence.py` creates an atomic copy of the collection progress
manifest with the evidence needed by `relevance_release_policy.py`. It performs
no browser or network calls and opens SQLite with `mode=ro`, `immutable=1`, and
`PRAGMA query_only=ON`. It never writes to `raw/`, a corpus file, a database,
`staging/`, or legacy data. Its only allowed output is a JSON file below
`working/`.

The output contains two top-level additions:

- `release_evidence`: count reconciliation, any supplemental residual review results, exact
  reply audit, attachment/chart audit, and claim-calibration audit.
- `release_review_packets`: deterministic row-by-row packets for any planned
  supplemental residual census job. The canonical literal full-capture plan has
  zero such jobs, so this object has `packet_count: 0` and
  `review_required: false`. Packet IDs, when present, are SHA-256 hashes of
  canonical packet content and do not include run time.

Every conclusion cites a SHA-256 artifact reference. The generator recomputes
the five managed evidence fields on every run; an older manifest cannot carry a
manually edited `passed` value forward.

## Typical final-window run

First rescan the collection orchestrator and build a provisional corpus,
corpus manifest, and analyzed Cardinal database. Record fresh full-window
Discord counts in the orchestrator state at or after
`2026-07-21T05:00:00Z`. Then run:

```powershell
python build_release_evidence.py `
  --count-observations working/collection_orchestrator_state.json `
  --output working/collection_progress_with_release_evidence.json
```

Existing output is refused. Use `--overwrite` only when intentionally replacing
that disposable working product. The source progress manifest is never changed
unless it is itself explicitly supplied as `--output` together with
`--overwrite`.

The defaults use:

- `relevance_collection_plan.json`
- `working/collection_progress_manifest.json`
- `working/corpus_partial_manifest.json`
- `working/corpus_partial.json`
- `working/cardinal_partial.sqlite`

For the final names, pass `--corpus-manifest`, `--corpus-data`, and `--database`
explicitly. The database is accepted for audit only when its `source_artifacts`
table contains the exact SHA-256 of `--corpus-data`, its counts reconcile to the
corpus manifest, and its build/corpus/audit timestamps are all at or after the
required cutoff.

## Count-observation contract

The input must be either `discord_collection_orchestrator_state` or
`discord_count_observations` and contain a `count_observations` array. A count
row is usable only when it has all of the following:

- `source: "operator_recorded_countSearch"`
- a nonempty `observation_id`
- the exact channel, unfiltered query core, and full plan window
- nonnegative `reported_total` and the matching 25-result page count
- `observed_at_utc` at or after the final cutoff

The generator independently hashes and validates the selected complete raw
segments and requires their total to equal the refreshed count. It never turns
a segment's captured total into a count observation. If multiple exact segment
covers exist, the count row must name an unambiguous `segment_ids` set.
Excluded nonzero recaptures keep the row pending unless the count artifact
contains explicit structured `discord_edit_deletion_provenance`.

## Residual review workflow

This workflow is conditional and is not a release requirement for the
canonical 16-full/22-verified-empty/zero-targeted plan. Targeted queries and
residual audits are supplemental only and cannot substitute for complete
message capture. If a future plan deliberately contains supplemental residual
census jobs, use the workflow below for those packets.

Run once without `--review-results`. This emits all review packets but every
review remains `pending`; even a packet with zero residual rows requires an
explicit signed-off result, so no review is fabricated.

Create a separate JSON file shaped like:

```json
{
  "schema_version": "1.0.0",
  "artifact_type": "discord_residual_review_results",
  "reviews": [
    {
      "job_id": "audit__CHANNEL_ID__2026-01-01",
      "packet_id": "THE_PACKET_SHA256",
      "status": "complete",
      "reviewed_at_utc": "2026-07-21T05:10:00Z",
      "reviewer": {
        "type": "human",
        "id": "stable-reviewer-id",
        "method": "row_by_row"
      },
      "classifications": [
        {
          "message_id": "DISCORD_MESSAGE_ID",
          "decision": "relevant",
          "rationale": "Concise Discord-only reason"
        }
      ],
      "new_terms": []
    }
  ]
}
```

`classifications` must cover the packet's residual message IDs exactly once.
Allowed decisions are `relevant`, `not_relevant`, and `ambiguous`; an ambiguous
row blocks release. A newly found term must name its packet-local Discord source
message IDs. Any new term also blocks the current packet: add the sourced term,
rerun affected searches, regenerate the packet, and submit a fresh review with
no newly discovered term. Boolean assertions cannot bypass that cycle.

## Exact reply, attachment, and claim rules

- Direct answer linkage is accepted only from the collector field
  `reply_to_message_id_source=owned_reply_context_descendant_content_id`, with
  `reply_target_scope_exact=true`, a matching `message-content-{id}`, and an
  exact Discord guild/channel/message permalink. Preview-only links are never
  promoted.
- Attachment audit checks owner identity, attachment/message snowflake timing,
  media-to-owned-attachment membership, and multiple owners. Accepted or
  qualified claims backed by visual attachments require an explicit
  `chart_dependent` boolean in normalized claim JSON, an exact
  `chart_dependent=true|false` limitation marker, or a message annotation of
  `chart_dependent`/`chart_independent`.
- Terminal failed attachments make the audit pending and block literal release.
  A chart-dependent claim is resolved only by a complete/partial extraction with
  verified local path, SHA-256, byte size, and exact attachment provenance. Failed,
  empty, or metadata-only extraction rows never satisfy the guard, and missing
  extraction confidence is not treated as `1.0`.
- Probability-like claims require complete trusted Discord evidence and an
  explicit calibration caveat appropriate to their epistemic type. Descriptive
  selected-corpus shares must remain sample-bound/non-causal and must not be
  presented as forward probability or expectancy. Performance rollup arithmetic
  and `not_causal=1` are independently checked.

The generator does not certify the separate `forum_exact_ids` gate. In
particular, attachment/CDN channel IDs are locator-only and are never treated as
exact forum-thread provenance. The release policy separately accepts only
row-owned forum-card IDs or owned exact reply/authenticated URL evidence.

## Fail-closed behavior

Missing, malformed, stale, ambiguous, unhashed, unlinked, pre-cutoff, or
incomplete evidence remains `pending` with explicit reasons. A packet is not a
review, a raw segment total is not a refreshed count, an adjacent message is not
a direct answer, and a descriptive win share is not a calibrated market
probability.

Run focused tests with:

```powershell
python -m unittest -v test_build_release_evidence.py
```
