# Final scoped Discord release pipeline

This runbook is the only release procedure for guild
`1167376964680691732`, January 1 through July 20, 2026 in
`America/Chicago` (`[2026-01-01T06:00:00Z, 2026-07-21T05:00:00Z)`).

The only authorized top-level Discord containers are:

| Exact Discord name | Exact ID | Kind |
|---|---:|---|
| `student-breakdowns` | `1370578463223975986` | text channel |
| `premium-journals` | `1283941772577472643` | forum channel |
| `❓│questions` | `1273692573898113076` | text channel |

`questions` is a logical display label only. It is never a Discord search
identity. An authenticated search for the Questions channel must use the exact
token `in:❓│questions`.

Do not supply broad collection plans, progress manifests, or supplemental
search directories to a scoped build. Do not add web information. Preserve all
pre-existing raw and quarantine bytes; scope filtering creates a derived view
and never deletes history.

## 0. Freeze the exact inputs

Start from the corpus directory after the complete July 20 Central day has
elapsed. Every downstream file is write-once.

```powershell
Set-Location "C:\Users\GageDesilets\OneDrive - Stay Lively\Documents\Codex PC Work\discord_trading_research\corpus_2026-01-01_2026-07-20"
$ErrorActionPreference = "Stop"
$RequiredEndUtc = [DateTimeOffset]"2026-07-21T05:00:00Z"
if ([DateTimeOffset]::UtcNow -lt $RequiredEndUtc) {
  throw "The complete July 20 America/Chicago day has not elapsed."
}
$CutoffUtc = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")

function Invoke-CheckedPython {
  & python @args
  if ($LASTEXITCODE -ne 0) {
    throw "Python command failed with exit code ${LASTEXITCODE}: $($args -join ' ')"
  }
}

$WriteOnceOutputs = @(
  "working/scoped_corpus_preflight.json",
  "working/scoped_manifest_preflight.json",
  "working/scoped_cardinal_preflight.sqlite",
  "working/scoped_collection_drift_final.json",
  "final",
  "Cardinal_Discord_Research_2026-01-01_2026-07-20"
)
foreach ($Path in $WriteOnceOutputs) {
  if (Test-Path -LiteralPath $Path) {
    throw "Write-once output already exists: $Path"
  }
}
```

Required immutable inputs:

- `authorized_collection_scope.json`;
- `working/full_server_channel_inventory_complete.json` (used only as the
  source inventory from which the three-parent view is derived);
- `working/premium_journals_scoped_inventory_reconciliation.json`;
- exact Student Breakdowns and Questions segment JSON under
  `raw/channel_segments/`;
- exact Premium Journals v2.6 daily canonical JSON under the dedicated
  authoritative directory `raw/channel_segments_v2_5/`; any Premium files
  under `raw/channel_segments/` are preservation-only and never authoritative;
- any adjacent `*.timestamp-scope-revalidation.json` sidecar, byte-bound to
  its immutable source segment and its preserved Discord recovery evidence;
- any byte-bound historical replacement notes under
  `raw/quarantine_collection_errors/`.

```powershell
foreach ($Path in @(
  "authorized_collection_scope.json",
  "working/full_server_channel_inventory_complete.json",
  "working/premium_journals_scoped_inventory_reconciliation.json"
)) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "Required scoped input is missing: $Path"
  }
}
if (-not (Test-Path -LiteralPath "raw/channel_segments" -PathType Container)) {
  throw "The standard scoped segment directory is missing."
}
if (-not (Test-Path -LiteralPath "raw/channel_segments_v2_5" -PathType Container)) {
  throw "The Premium v2.6 authoritative segment directory is missing."
}
```

## 1. Run the temp-only contract tests

These tests must not read or modify live Discord state.

```powershell
Invoke-CheckedPython -m unittest -v `
  test_authorized_collection_scope.py `
  test_build_corpus.py `
  test_timestamp_scope_revalidation.py `
  test_reply_provenance_contract.py `
  test_premium_journals_provenance_contract.py `
  test_cardinal_database_v2.py `
  test_package_final_release.py `
  test_final_pipeline_runbook_contract.py
```

## 2. Validate Premium child identity before any corpus build

The reconciliation must hash and semantically bind all three inputs: baseline
inventory, authenticated group-navigation evidence, and its exact source
partial. Each addition must match the Premium parent query, page, group,
result index, and message ID. A thread URL by itself never proves parentage.

The corpus builder performs this check. A missing reconciliation, missing bound
partial, hash mismatch, query mismatch, membership mismatch, or arbitrary child
ID stops the build.

## 3. Build a scoped preflight corpus

No source-provenance contradiction is repaired. The exact requested container,
`in:` target, completion evidence queries, and every row's collection
ID/name/kind/source must agree. Contradictory segments are excluded as
ambiguous, and the scope gate remains failed.

```powershell
Invoke-CheckedPython build_corpus.py `
  --segment-dir raw/channel_segments `
  --segment-dir raw/channel_segments_v2_5 `
  --historical-reconciliation-dir raw/quarantine_collection_errors `
  --inventory working/full_server_channel_inventory_complete.json `
  --authorized-scope authorized_collection_scope.json `
  --scoped-child-inventory-reconciliation working/premium_journals_scoped_inventory_reconciliation.json `
  --provenance-root ../.. `
  --guild-id 1167376964680691732 `
  --start-date 2026-01-01 `
  --end-date 2026-07-20 `
  --timezone America/Chicago `
  --data-cutoff-utc $CutoffUtc `
  --output working/scoped_corpus_preflight.json `
  --manifest working/scoped_manifest_preflight.json
```

```powershell
$Preflight = Get-Content working/scoped_manifest_preflight.json -Raw | ConvertFrom-Json
$Scope = $Preflight.authorized_collection_scope
$PremiumSource = $Scope.canonical_path_policy
$PremiumClosure = $Scope.child_inventory_reconciliation.message_scope_closure
if ($Scope.release_gate.passed -ne $true -or
    $Scope.excluded.ambiguous_fail_closed_file_count -ne 0 -or
    $PremiumSource.gate -ne "premium_journals_authoritative_v2_5_source_integrity" -or
    $PremiumSource.passed -ne $true -or
    $PremiumSource.premium_authoritative_directory -ne "raw/channel_segments_v2_5" -or
    $PremiumSource.premium_legacy_directory_policy -ne "preservation_only_not_authoritative" -or
    $PremiumSource.premium_collector_version_required -ne "2.6" -or
    $PremiumSource.accepted_premium_segment_count -ne 201 -or
    $PremiumSource.accepted_premium_daily_date_count -ne 201 -or
    $PremiumSource.duplicate_premium_daily_dates.Count -ne 0 -or
    $PremiumClosure.gate -ne "premium_journals_message_data_scope_closure" -or
    $PremiumClosure.passed -ne $true -or
    $PremiumClosure.closure_proven -ne $true -or
    $PremiumClosure.parent_segment_count -ne 201 -or
    $PremiumClosure.required_exact_daily_parent_segment_count -ne 201 -or
    $PremiumClosure.invalid_daily_partition_segment_count -ne 0 -or
    $PremiumClosure.duplicate_daily_date_count -ne 0 -or
    $Scope.child_inventory_reconciliation.inventory_complete -ne $false -or
    $Scope.child_inventory_reconciliation.enumeration_complete -ne $false -or
    $Scope.child_inventory_reconciliation.closure_proven -ne $false) {
  throw "Scoped selection or Premium message-data closure is incomplete."
}
```

The Premium closure gate requires complete authenticated parent-forum coverage
for every local day, terminal evidence, exact row-to-child binding, zero
binding conflicts, and inclusion of every observed message-bearing child in the
proven union. It does not infer inaccessible or zero-message threads.
The 201-route message-data closure is not an inventory-census claim: the
158-ID reconciliation remains a lower bound, and its `inventory_complete`,
`enumeration_complete`, and `closure_proven` fields must remain false.

## 4. Archive exact Discord-owned attachments, if any

Use `discord_attachment_archiver.py` only for attachment requests already
owned by an authorized message. The browser worker fetches only the exact
Discord URL in each request envelope and never exports authentication state.

```powershell
Invoke-CheckedPython discord_attachment_archiver.py plan `
  --corpus working/scoped_corpus_preflight.json `
  --manifest working/attachment_archive_manifest.json
```

After the authenticated browser loop has ingested each response, require
terminal local verification. A terminal failure blocks release.

```powershell
Invoke-CheckedPython discord_attachment_archiver.py verify `
  --manifest working/attachment_archive_manifest.json `
  --archive-root working/attachment_archive `
  --require-terminal
```

If attachments exist, rebuild the preflight corpus in a fresh workspace with
`--attachment-manifest` and `--attachment-archive-root`. The resulting entry
set must have exact authorized-message parity.

## 5. Build and analyze the scoped database

The database builder independently repeats the scope checks, including explicit
occurrence provenance and Premium child proof, before creating SQLite. It also
stores portable source tokens rather than absolute build paths.

```powershell
Invoke-CheckedPython build_cardinal_database_v2.py `
  --input working/scoped_corpus_preflight.json `
  --input working/scoped_manifest_preflight.json `
  --authorized-scope authorized_collection_scope.json `
  --output working/scoped_cardinal_preflight.sqlite `
  --window-start 2026-01-01T06:00:00Z `
  --window-end 2026-07-21T05:00:00Z
```

```powershell
Invoke-CheckedPython build_discord_analysis_layer.py `
  --database working/scoped_cardinal_preflight.sqlite `
  --output working/scoped_cardinal_preflight_analyzed.sqlite `
  --report working/scoped_analysis_preflight_report.json `
  --curated ../curated_analysis_3month.json `
  --model-analysis ../model_analysis_3month.json `
  --trade-script ../build_trade_analysis_3month.py `
  --rb-script ../build_rb_analysis_3month.py `
  --model-script ../build_model_analysis_3month.py
```

The analysis layer may emit at most five evidence-backed models. It must leave
entry, invalidation, target, time, instrument, win, and loss fields unknown when
the scoped Discord evidence does not establish them.

## 6. Build the final corpus from the same frozen inputs

Create `final/` only after preflight analysis succeeds. Repeat the exact scoped
build with `--release`; never broaden its inputs.

```powershell
New-Item -ItemType Directory -Path final | Out-Null
Invoke-CheckedPython build_corpus.py `
  --segment-dir raw/channel_segments `
  --segment-dir raw/channel_segments_v2_5 `
  --historical-reconciliation-dir raw/quarantine_collection_errors `
  --inventory working/full_server_channel_inventory_complete.json `
  --authorized-scope authorized_collection_scope.json `
  --scoped-child-inventory-reconciliation working/premium_journals_scoped_inventory_reconciliation.json `
  --provenance-root ../.. `
  --guild-id 1167376964680691732 `
  --start-date 2026-01-01 `
  --end-date 2026-07-20 `
  --timezone America/Chicago `
  --data-cutoff-utc $CutoffUtc `
  --release `
  --output final/raw_corpus_release.json `
  --manifest final/coverage_manifest_release.json
```

## 7. Final database, reports, and LLM companion

```powershell
Invoke-CheckedPython build_cardinal_database_v2.py `
  --input final/raw_corpus_release.json `
  --input final/coverage_manifest_release.json `
  --authorized-scope authorized_collection_scope.json `
  --output final/cardinal_pristine.sqlite `
  --window-start 2026-01-01T06:00:00Z `
  --window-end 2026-07-21T05:00:00Z
```

```powershell
Invoke-CheckedPython build_discord_analysis_layer.py `
  --database final/cardinal_pristine.sqlite `
  --output final/cardinal_analyzed.sqlite `
  --report final/analysis_report.json `
  --curated ../curated_analysis_3month.json `
  --model-analysis ../model_analysis_3month.json `
  --trade-script ../build_trade_analysis_3month.py `
  --rb-script ../build_rb_analysis_3month.py `
  --model-script ../build_model_analysis_3month.py

Invoke-CheckedPython build_discord_research_report.py `
  --database final/cardinal_analyzed.sqlite `
  --markdown-output final/discord_trading_research.md `
  --json-output final/discord_trading_research.json `
  --expected-guild-id 1167376964680691732 `
  --expected-window-start-utc 2026-01-01T06:00:00Z `
  --expected-window-end-utc 2026-07-21T05:00:00Z

Invoke-CheckedPython build_llm_companion.py `
  --database final/cardinal_analyzed.sqlite `
  --output final/cardinal_llm.sqlite `
  --report final/llm_companion_report.json
```

Generate scoped post-final release evidence and independent QA from these exact
final hashes. Neither command accepts the obsolete server-wide relevance plan
or orchestrator manifest.

```powershell
Invoke-CheckedPython audit_collection_drift.py `
  --root . `
  --mode final `
  --window-start 2026-01-01 `
  --window-end 2026-07-20 `
  --output working/scoped_collection_drift_final.json

Invoke-CheckedPython build_scoped_release_evidence.py `
  --corpus final/raw_corpus_release.json `
  --corpus-manifest final/coverage_manifest_release.json `
  --authorized-scope authorized_collection_scope.json `
  --database final/cardinal_analyzed.sqlite `
  --output final/scoped_post_final_release_evidence.json

Invoke-CheckedPython qa/validate_scoped_release.py `
  --corpus final/raw_corpus_release.json `
  --corpus-manifest final/coverage_manifest_release.json `
  --authorized-scope authorized_collection_scope.json `
  --database final/cardinal_analyzed.sqlite `
  --release-evidence final/scoped_post_final_release_evidence.json `
  --drift-audit working/scoped_collection_drift_final.json `
  --output final/independent_qa_report.json
```

The evidence and QA schemas bind the authorized-scope SHA, the three exact
parent IDs, the passed Premium closure gate, the final corpus manifest, and the
authoritative database. They explicitly record the obsolete broad policy as
disabled.

## 8. Render the handoff guide and package

```powershell
Invoke-CheckedPython build_llm_handoff_guide.py `
  --template LLM_HANDOFF_GUIDE_TEMPLATE.md `
  --output final/LLM_HANDOFF_GUIDE.md `
  --merged-corpus final/raw_corpus_release.json `
  --coverage-manifest final/coverage_manifest_release.json `
  --pristine-database final/cardinal_pristine.sqlite `
  --full-database final/cardinal_analyzed.sqlite `
  --compact-database final/cardinal_llm.sqlite `
  --analysis-report final/analysis_report.json `
  --qa-report final/independent_qa_report.json `
  --compact-report final/llm_companion_report.json `
  --release-status complete
```

```powershell
Invoke-CheckedPython package_final_release.py `
  --authoritative-db final/cardinal_analyzed.sqlite `
  --compact-db final/cardinal_llm.sqlite `
  --corpus-manifest final/coverage_manifest_release.json `
  --release-evidence final/scoped_post_final_release_evidence.json `
  --qa-report final/independent_qa_report.json `
  --research-markdown final/discord_trading_research.md `
  --research-json final/discord_trading_research.json `
  --llm-handoff-guide final/LLM_HANDOFF_GUIDE.md `
  --output-dir Cardinal_Discord_Research_2026-01-01_2026-07-20
```

The packager rejects a merely well-formed but wrong scope hash, an unproven
child, missing Premium closure, an out-of-scope database message, absolute
source paths, and any compact/full identity or content difference. It opens
both databases read-only and publishes atomically without changing its inputs.

## 9. Final invariants

- Raw and quarantine bytes are unchanged.
- Every included segment has exact query/request/row provenance.
- `in:questions` and `in:live` are rejected for the Questions ID.
- URL-only thread evidence never proves a Premium parent.
- Historical replacement notes bind both the legacy and current SHA-256.
- Attachments and extraction text belong only to authorized messages.
- SQLite `messages`, `messages_fts`, and `attachment_extractions_fts` contain no
  excluded canary data.
- The package uses only portable paths and compact/full row content is equal.
