# Final release packager

`package_final_release.py` is the last, fail-closed step for the Jan 1–Jul 20,
2026 Discord research release. It does not collect, curate, analyze, repair, or
promote data. It packages only already-final artifacts after independently
checking their release state and cross-file provenance.

Do not run it against the current `working/` products. A final package is
accepted only after the complete collection, analysis, reports, compact
database, and independent QA outputs exist.

## Required inputs

Every input is explicit and must be a distinct regular file:

- the authoritative, fully analyzed Cardinal v2 SQLite database;
- the compact LLM SQLite companion built from that exact authoritative file;
- the final `discord_serverwide_coverage_manifest` JSON;
- the post-final `discord_collection_progress_manifest` JSON containing a
  complete `discord_release_evidence` envelope bound to that exact final
  database and coverage manifest;
- the final `independent_discord_corpus_validation` QA JSON;
- one or more detailed research Markdown reports;
- one or more matching detailed structured research JSON reports; and
- a finalized LLM handoff guide with all template fields replaced.

Names or directory paths identifying an input as `partial`, `smoke`,
`working`, `draft`, `provisional`, `staging`, `template`, or `incomplete` are
rejected. Content gates are authoritative as well: renaming a partial product
does not make it releasable.

The sole path exception is `--release-evidence`: its generator intentionally
writes only below `working/`. The packager accepts that location only after the
evidence envelope is complete, post-cutoff, local-only, has no pending items,
passes all five managed evidence sections, and contains exact SHA-256 links to
the supplied final analyzed database and coverage manifest. Independent QA
must also name that exact post-final evidence file as its progress input.

## Final command

Run this only after the source set is frozen and the independent QA report says
`passed` / `Ready to share`:

```powershell
python package_final_release.py `
  --authoritative-db final/cardinal_analyzed.sqlite `
  --compact-db final/cardinal_llm.sqlite `
  --corpus-manifest final/coverage_manifest_release.json `
  --release-evidence working/collection_progress_post_final_evidence.json `
  --qa-report final/independent_qa_report.json `
  --research-markdown final/discord_trading_research.md `
  --research-json final/discord_trading_research.json `
  --llm-handoff-guide final/LLM_HANDOFF_GUIDE.md `
  --output-dir Cardinal_Discord_Research_2026-01-01_2026-07-20
```

`--research-markdown` and `--research-json` are repeatable. Report basenames
must be unique because they are retained under `research/`.

The output must not exist. The sole exception is an existing real, empty
directory supplied with `--allow-existing-empty-target`; it is rechecked
immediately before the atomic publish. A nonempty directory is never replaced.

## Fail-closed validation

Before copying, the packager verifies:

- guild `1167376964680691732` and the exact `America/Chicago` Jan 1–Jul 20
  window (`2026-01-01T06:00:00Z` through `2026-07-21T05:00:00Z`,
  end-exclusive);
- a collection cutoff at or after the complete Jul 20 Central day;
- complete corpus status, `release_ready=true`, exact complete inventory,
  every corpus release gate, all 13/22/3 relevance-policy assignments, every
  relevance hard gate, and all planned jobs passed;
- complete post-final release evidence generated at or after the cutoff, zero
  outside-source/browser/network/raw-write activity, no pending items, exact
  final database and manifest hash links, and passed count, residual-review,
  reply, attachment/chart, and claim-calibration sections;
- no coverage file failures, unresolved release-critical quarantine, or
  unhashed manifest source rows;
- independent QA status `passed`, assessment `Ready to share`, zero failures,
  every QA check passed, final-day completeness, completed database inspection,
  an exact QA-to-authoritative-database hash link, and before/after preservation
  and source-hash verification, with its progress input bound to the supplied
  post-final release-evidence artifact, plus a hash-bound final collection-drift
  `PASS` with zero structural, unresolved, or orphan-partial counts;
- Discord-only structured provenance and `outside_sources_used=0` throughout
  the corpus manifest, QA report, research JSON, SQLite metadata, collection
  run, analysis run, and analysis documents;
- SQLite `integrity_check`, foreign keys, a successful immutable/read-only
  open, enforced `query_only`, a blocked write probe, and absence of WAL, SHM,
  or rollback-journal sidecars;
- complete Cardinal v2 scope, zero collection gaps, an empty Discord-only audit,
  required analysis documents, and a source-artifact hash link from the full
  database to the supplied corpus manifest;
- a compact-database hash link to the exact authoritative database, its LLM
  query tables, complete scope, zero gaps, and equality of core-table row
  counts with the authoritative database;
- detailed Markdown/JSON report shape, exact scope, passed report validation,
  authoritative-database hash linkage, Discord-only limitations, and the
  maximum of five emitted models; and
- a renderer-bound handoff guide containing both database hashes, the exact
  guild/UTC scope, portable paths for every packaged database/manifest/QA
  artifact, explicit `NOT_PACKAGED` labels for build-only inputs, and no
  unresolved `{{...}}` placeholders.
- when owned attachments exist, an exact hash-bound archive manifest with zero
  terminal `failed` rows, `literal_release_complete=true`, and fresh local size/SHA-256
  verification for every downloaded attachment and complete/partial extraction
  artifact. Failed/no-artifact extraction rows are never packaged as chart evidence.

The source files are SHA-256 hashed before validation and again immediately
before publication. Any change aborts the package. SQLite files are opened via
`mode=ro&immutable=1`; no input is opened writable.

## Atomic deterministic output

Files are copied into a private sibling staging directory, flushed, rehashed,
and then published with one directory rename. A failed build removes only its
own staging directory. It never deletes or modifies an input or an existing
nonempty output.

The package layout is stable:

```text
README.md
RELEASE_MANIFEST.sha256.json
databases/
  authoritative_cardinal.sqlite
  compact_llm.sqlite
guidance/
  LLM_HANDOFF_GUIDE.md
manifests/
  corpus_coverage_manifest.json
qa/
  post_final_release_evidence.json
  independent_qa_report.json
research/
  <supplied final Markdown and JSON basenames>
```

`README.md` is a concise consumer index. `RELEASE_MANIFEST.sha256.json`
contains the release scope, validation summaries, package ID, and SHA-256 plus
byte size for every other packaged file. The manifest intentionally excludes
its own hash to avoid a circular digest. Package content is deterministic for
an identical validated input set; no run timestamp or absolute source path is
embedded.

Consumers should open both packaged databases read-only. The compact database
is the recommended first query target; the authoritative database remains the
source for full raw provenance. Discord URLs retained in messages are not
outside research evidence.

## Tests

```powershell
python -m unittest -v test_package_final_release.py
```

The focused suite covers deterministic output, source preservation, immutable
SQLite validation, compact/full hash linkage, existing-target safety,
partial/working rejection, failed QA, outside-source refusal, sidecar refusal,
unresolved guide placeholders, incorrect post-final evidence database linkage,
and QA that is not bound to the supplied post-final evidence artifact.
