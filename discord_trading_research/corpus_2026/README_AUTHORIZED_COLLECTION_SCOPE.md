# User-authorized three-channel release scope

`authorized_collection_scope.json` is the authoritative release boundary. The
corpus, database, analysis, reports, and trading-model evidence may contain only:

- `student-breakdowns` (`1370578463223975986`)
- `premium-journals` (`1283941772577472643`)
- `❓│questions` (`1273692573898113076`)

`questions` is a logical display label only. Discord search collection must use
the exact token `in:❓│questions`; `in:questions` does not bind to this channel.

Exact child threads are eligible only when the merged authenticated inventory
proves their allowed parent with inspectable Discord-only identity evidence.
Titles, filenames, attachment URLs, row inference, and legacy labels never prove
parentage.

The additive child-inventory input is
`working/premium_journals_scoped_inventory_reconciliation.json`. Its current
158-ID union (156 preserved baseline IDs plus 2 exact authenticated additions)
is usable for exact parentage and selection. Its `closure_proven=false` state is
authoritative: it overrides the obsolete 156-ID baseline completion claim and
keeps inventory/release completeness blocked until fresh complete parent-forum
captures prove the message-data closure defined below.

For this narrowed dataset, “closure” is explicitly message-data closure: every
Jan 1–Jul 20 parent-forum date segment is exact and strictly complete with
terminal search evidence; every captured row is bound to an exact authenticated
group/thread ID; unresolved groups and container conflicts are zero; and the
final derived child union covers every observed message-bearing child. Threads
with zero messages in the window, inaccessible threads, and undiscoverable
threads outside that bounded parent-forum result set are outside the requested
message-bearing scope and are not required to prove closure.

## How the derived view works

`build_corpus.py` now uses this scope file by default. It reads the existing raw
directories without copying, moving, rewriting, or deleting their bytes. A
segment enters the derived corpus only when all of the following are exact:

1. the guild is the requested Discord guild;
2. `requested_container` carries an authenticated exact ID source;
3. the date-bounded Discord query is present; and
4. that ID is one of the three allowed parents or a provenance-proven child.

Every excluded segment and completion sidecar is represented in
`authorized_collection_scope.excluded` by portable path, byte size, SHA-256,
message-row count, message-ID-set hash, and exclusion reason. Message text and
message IDs from excluded channels are not copied into the scoped corpus. Mixed
migration sidecar and attachment-manifest entries are also reduced in memory to
the scoped message set; the source files remain unchanged and their excluded
record/key sets are count-and-hash audited.

An ambiguous file is excluded and fails the
`authorized_collection_scope_enforced` release gate. The three-parent set is
hard-coded in the validator as an anti-broadening check, so adding a fourth JSON
entry cannot silently expand a release.

## Scoped corpus command

Use the merged exact inventory, canonical segments, and the normal date/cutoff
arguments. The scope argument is shown explicitly even though it is the CLI
default:

```powershell
python build_corpus.py `
  --authorized-scope authorized_collection_scope.json `
  --scoped-child-inventory-reconciliation working/premium_journals_scoped_inventory_reconciliation.json `
  --segment-dir raw/channel_segments `
  --historical-reconciliation-dir raw/quarantine_collection_errors `
  --inventory working/full_server_channel_inventory_complete.json `
  --provenance-root ../.. `
  --guild-id 1167376964680691732 `
  --start-date 2026-01-01 `
  --end-date 2026-07-20 `
  --timezone America/Chicago `
  --data-cutoff-utc $CutoffUtc `
  --output working/scoped_corpus_preflight.json `
  --manifest working/scoped_coverage_preflight.json
```

For the final build, add the scoped attachment manifest/root and `--release`.
The resulting corpus and manifest are the only permitted inputs to the Cardinal
database, analysis, report, and package builders.

The database builder independently revalidates the same boundary and rejects an
unscoped message-bearing input or any message whose canonical container is not
an authorized parent/proven child:

```powershell
python build_cardinal_database_v2.py `
  --authorized-scope authorized_collection_scope.json `
  --input working/scoped_corpus_preflight.json `
  --input working/scoped_coverage_preflight.json `
  --output databases/scoped_cardinal.sqlite
```

It records the scope SHA-256 and authorized-container count in SQLite metadata.
The final packager also requires the three-channel scope summary, its passed
selection gate, zero ambiguous exclusions, the scoped inventory, and the
disabled obsolete server-wide relevance policy.

Do not pass the old server-wide `relevance_collection_plan.json`, server-wide
orchestrator progress manifest, or mixed-channel legacy merged raw file to a
scoped build. The builder rejects those combinations. Targeted segment
directories are allowed, but every segment inside them is independently checked
against the same exact requested-container rule.

Final evidence and QA use `build_scoped_release_evidence.py` and
`qa/validate_scoped_release.py`. These read the scoped corpus, manifest, and
database directly and have no server-wide relevance-plan or orchestrator input.

## Tests

`test_authorized_collection_scope.py` covers scope tampering, authenticated child
parentage, out-of-scope exclusion, direct proven-child inclusion, ambiguous-ID
fail closure, hashes/counts, and the derived inventory. Existing corpus tests
remain backward-compatible when the Python API is called without an authorized
scope.
