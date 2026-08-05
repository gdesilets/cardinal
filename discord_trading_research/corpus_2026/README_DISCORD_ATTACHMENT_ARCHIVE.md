# Discord attachment archive contract

`discord_attachment_archiver.py` is the durable media bridge for the local
Cardinal/LLM handoff. It catalogs message-owned Discord attachments from a merged
corpus, gives an authenticated browser worker one exact request at a time, atomically
stores returned bytes, and maintains a resumable manifest.

The archiver itself performs no network request. It never reads browser cookies,
profiles, local/session storage, passwords, or credentials. It ignores ordinary
message links and accepts only HTTPS URLs on `cdn.discordapp.com` or
`media.discordapp.net` with an exact
`/attachments/<channel-id>/<attachment-id>/<filename>` path. The CDN channel ID is
retained as attachment provenance and is never promoted to forum/thread identity.

## Literal-release rule

A literal release requires every discovered owned attachment to reach a release-safe
terminal disposition:

- `downloaded`: locally present bytes match the stored byte size and SHA-256;
- `unavailable`: an observed 404/410, or a specific Discord unavailable/deleted state
  accompanied by substantive diagnostic detail;
- `failed`: at least three documented browser attempts ended without obtainable
  bytes, every failed attempt has substantive `error_detail`, and the final attempt
  was explicitly marked terminal. This is audit-terminal but not release-safe.

All bytes returned successfully by Discord must be archived. A substantiated terminal
`unavailable` row is release-safe because deleted media cannot be fabricated, but it
makes `release_gate.byte_complete` false. A terminal `failed` row is preserved as a
degraded working result and always makes `release_gate.passed=false`,
`literal_release_complete=false`, and blocks final packaging.

`release_gate.terminal_coverage_complete=true` means every attachment was accounted
for; it does not imply literal release eligibility. Proceed only when
`literal_release_complete=true`, `release_gate.passed=true`, and `failed_count=0`.

## Browser-worker sequence

First build a frozen working corpus, then catalog it:

```powershell
python discord_attachment_archiver.py plan `
  --corpus working/corpus_attachment_catalog.json `
  --manifest working/attachment_archive_manifest.json
```

Get the next exact request:

```powershell
python discord_attachment_archiver.py next `
  --manifest working/attachment_archive_manifest.json `
  --limit 1
```

The browser worker must fetch only the returned `discord_url` in the already
authenticated Discord browser. It must reject a final URL unless its host and path,
including channel ID, attachment ID, and filename, exactly match the planned request.
Signed query parameters may vary. It must not inspect or export authentication state.
The browser response is a JSON object with this shape:

```json
{
  "contract": "discord_attachment_browser_response_v1",
  "request_id": "<exact request_id>",
  "message_id": "<exact Discord message ID>",
  "attachment_id": "<exact Discord attachment ID>",
  "final_url": "<observed Discord attachment URL>",
  "status": "downloaded",
  "http_status": 200,
  "mime_type": "image/png",
  "attempted_at_utc": "<real UTC timestamp>",
  "body_base64": "<response bytes as base64>",
  "byte_size": 12345,
  "sha256": "<optional browser-computed SHA-256; locally rechecked>",
  "outside_sources_used": 0,
  "credentials_or_browser_storage_inspected": false
}
```

For large responses, omit `body_base64` and provide `staged_file`, a safe path under
the explicitly supplied `--staging-root`. Never provide both transports. For an
unavailable/failed response, omit both. `unavailable` requires HTTP 404/410 or an
allowed Discord-unavailable code plus substantive diagnostic detail. Every `failed`
response requires substantive `error_detail` and stays pending until the third
documented attempt; only then may the browser worker set `terminal:true`. A terminal
failed response preserves the audit trail but does not permit release.

Ingest the response and repeat:

```powershell
python discord_attachment_archiver.py ingest `
  --manifest working/attachment_archive_manifest.json `
  --archive-root working/attachment_archive `
  --response working/attachment_response.json `
  --staging-root working/attachment_staging
```

The tool writes each byte file via same-directory temporary file, flush, `fsync`, and
atomic replace. It updates the JSON manifest the same way. Re-running `plan` against
the unchanged corpus resumes without losing completed rows. If the frozen corpus grew,
`plan --reconcile` preserves matching rows, adds new attachments, and refuses to drop
an old row silently.

After the last request:

```powershell
python discord_attachment_archiver.py verify `
  --manifest working/attachment_archive_manifest.json `
  --archive-root working/attachment_archive `
  --require-terminal
```

Do not proceed to literal release unless this exits zero and reports `status=passed`,
`terminal_coverage_complete=true`, `literal_release_complete=true`, `pending=0`, and
`failed=0`. A degraded all-terminal manifest can pass structural verification while
still failing the literal-release gate.

## Local OCR/manual extraction

Extraction is optional for preservation, but required before a chart-dependent claim
can be resolved. The source attachment must already be downloaded and terminal. A
local OCR/manual tool writes its output beneath a controlled staging directory; record
it with:

```powershell
python discord_attachment_archiver.py record-extraction `
  --manifest working/attachment_archive_manifest.json `
  --archive-root working/attachment_archive `
  --extraction working/attachment_extraction_response.json `
  --staging-root working/attachment_staging
```

An extraction response identifies the exact `attachment_id`, `status`, `method`, real
`created_at_utc`, and (for complete/partial output) `staged_file`. The archiver hashes
and atomically copies that artifact after rehashing the source attachment. UTF-8 text
is read from the staged artifact; if `extracted_text` is supplied it must match those
bytes exactly. Missing confidence remains `null`; it is never defaulted to `1.0`. A
failed extraction keeps metadata and substantive failure detail,
but has no artifact and never enters the queryable `attachment_extractions` evidence
table. Only complete/partial artifacts whose local bytes are rehashed successfully can
satisfy the chart guard; exact claim-to-attachment evidence linkage is still required.

## Downstream binding

Pass the same terminal manifest/root to every final gate:

- `build_corpus.py --attachment-manifest ... --attachment-archive-root ...`
- `qa/validate_corpus.py --attachment-manifest ... --attachment-archive-root ...`
- `package_final_release.py --attachment-manifest ... --attachment-archive-root ...`

The corpus annotates each owned attachment with its package-relative path, hash,
capture/extraction status, attempts, and failures. The authoritative and compact SQLite
files retain those fields in `attachments`; local extraction locators appear in
`attachment_extractions`. Archiver verification, QA, and packaging independently
rehash every downloaded attachment and every complete/partial extraction artifact,
require exact manifest/corpus attachment parity, and bind the exact manifest SHA-256.

The release package places the manifest at
`manifests/discord_attachment_archive_manifest.json` and copies verified media and
extraction files to their recorded `attachments/...` paths.
