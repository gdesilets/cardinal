# Legacy premium-journals canonical-v2 staging

`convert_legacy_premium_journals_v2.py` migrates only the already validated
Discord `premium-journals` coverage from April 20 through July 20, 2026:

- `../three_month_segments/primary_*.json` for April 20 through July 6;
- `../raw_discord_export.json` → `primary_messages` for the complete July 7–20
  baseline tail.

It does not call a browser, access the network, use outside knowledge, filter
messages for relevance, or write under `raw/channel_segments`.

## Output

The default staging directory is:

`staging/legacy_premium_journals_v2/`

It contains:

- `segments/`: 40 canonical-v2-compatible, source-aligned segment files;
- `legacy_premium_journals_v2_manifest.json`: coverage, source/output hashes,
  preservation checks, and aggregate quarantine counts;
- `legacy_premium_journals_v2_quarantine.jsonl`: one machine-readable record
  for every occurrence requiring recapture or exact locator evidence.

Every staged message contains:

- `timestamp_utc` and `snowflake_timestamp_utc`, both derived from the Discord
  message snowflake;
- `legacy_captured_timestamp_utc`, retaining the collector's captured value;
- `legacy_original_payload`, an untouched copy of the source occurrence;
- `_migration_occurrence`, tying that occurrence to its source file hash,
  collection, row, query, segment, page, and result index;
- `legacy_contamination_audit`, `locator_audit`,
  `migration_quarantined`, and `migration_quarantine_reasons`.

Inferred attachment/thread values and inferred permalinks are preserved as
inferred. They are never promoted to exact. A missing exact thread ID or exact
Discord message permalink is a quarantine reason, not an invitation to invent
one from the known guild or parent forum.

Quarantine does not delete rows or make a complete source search incomplete.
It prevents source ambiguity from being mistaken for analysis-ready evidence.
An importer must consume either the per-message migration flags or the JSONL
sidecar before treating staged rows as trusted evidence.

`build_corpus.py` now consumes both forms automatically (including a sidecar
beside the segment directory), and `build_cardinal_database_v2.py` persists the
trust state and quarantine records. Staging is never copied into canonical raw
storage. Only an independently captured canonical occurrence can make the
message eligible for accepted analysis; the migrated occurrence remains
preserved and explicitly quarantined.

## Run and test

```powershell
python convert_legacy_premium_journals_v2.py
python -m unittest -v test_convert_legacy_premium_journals_v2.py
```

The converter refuses an existing destination and refuses any destination
equal to, inside, or above the protected `raw/channel_segments` directory.
