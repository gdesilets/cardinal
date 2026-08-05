# Premium-journals thread inventory release gate

`merge_forum_thread_inventory.py` validates and merges three Discord-only inputs:

1. a fresh post-cutoff authenticated 38-container snapshot at
   `raw/post_cutoff_top_level_inventory.json`;
2. the authenticated forum enumeration at `raw/forum_thread_inventory.json`;
3. the all-parent ordinary-thread census at
   `raw/ordinary_thread_inventory.json` (documented in
   `README_THREAD_INVENTORY.md`).

It writes a new, builder-compatible inventory. It never modifies either input,
never treats an attachment-CDN path as thread identity, and never writes an
output when any completeness or provenance check is partial.

## Why this is separate

The original July 20 inventory is a pre-cutoff baseline only and cannot satisfy
release. The merger requires a new navigation snapshot after the complete local
day closes. A message or image URL observed inside
`premium-journals` can contain an unrelated channel-shaped number. Therefore an
attachment URL is not container provenance. Exact thread identity must come from
the owning forum-card DOM identifier or the authenticated Discord thread URL.

Thread titles are labels, not keys. Duplicate titles are valid; exact Discord
snowflake IDs must be unique.

## Cutoff and scope

The requested local window is authoritative:

- timezone: `America/Chicago`
- start inclusive: `2026-01-01T00:00:00-06:00`
- end exclusive: `2026-07-21T00:00:00-05:00`
- UTC data cutoff: `2026-07-21T05:00:00Z`

Both active and discoverable-archived enumeration passes must start after the
data cutoff. This ensures that the complete July 20 local day has closed before
the forum snapshot begins. The raw inventory must repeat the top-level local window
exactly and declare the same UTC cutoff.

The completeness claim is bounded to threads accessible or discoverable to the
authenticated Discord account at capture. Deleted, inaccessible, or
no-longer-discoverable threads cannot be proven and are not silently invented.

## Raw input contract

The raw root must contain these fields:

```json
{
  "schema_version": "1.0",
  "guild_id": "1167376964680691732",
  "parent_forum_channel_id": "1283941772577472643",
  "source_scope": "discord_only",
  "outside_sources_used": false,
  "inventory_complete": true,
  "status": "complete",
  "requested_local_window": {
    "timezone": "America/Chicago",
    "start_inclusive": "2026-01-01T00:00:00-06:00",
    "end_exclusive": "2026-07-21T00:00:00-05:00"
  },
  "data_cutoff_utc": "2026-07-21T05:00:00Z",
  "capture_completed_at_utc": "2026-07-21T05:21:00Z",
  "enumeration_passes": {},
  "threads": []
}
```

The `enumeration_passes` object must have exactly the required evidence for both
`active` and `discoverable_archived`. Each pass must be authenticated,
Discord-only, complete, terminal, fully paginated, have no remaining cursor,
include at least one source reference, and reconcile its exact `thread_ids`
against `reported_thread_count`.

```json
{
  "active": {
    "parent_forum_channel_id": "1283941772577472643",
    "method": "authenticated_discord_forum_card_enumeration",
    "status": "complete",
    "authenticated": true,
    "source_scope": "discord_only",
    "outside_sources_used": false,
    "started_at_utc": "2026-07-21T05:05:00Z",
    "completed_at_utc": "2026-07-21T05:10:00Z",
    "source_refs": ["discord-ui:premium-journals:active:terminal-page"],
    "pagination_complete": true,
    "terminal_state_observed": true,
    "remaining_cursor": null,
    "reported_thread_count": 1,
    "thread_ids": ["1508933293322801183"]
  },
  "discoverable_archived": {
    "parent_forum_channel_id": "1283941772577472643",
    "method": "authenticated_discord_archived_thread_enumeration",
    "status": "complete",
    "authenticated": true,
    "source_scope": "discord_only",
    "outside_sources_used": false,
    "started_at_utc": "2026-07-21T05:11:00Z",
    "completed_at_utc": "2026-07-21T05:20:00Z",
    "source_refs": ["discord-ui:premium-journals:archive:terminal-page"],
    "pagination_complete": true,
    "terminal_state_observed": true,
    "remaining_cursor": null,
    "reported_thread_count": 0,
    "thread_ids": []
  }
}
```

An empty pass is acceptable only when its complete terminal state is explicitly
captured and its count is zero. Active and archived ID sets must not overlap.
Their union must equal the unique IDs represented by `threads`.

Each thread row requires:

- exact `thread_id` and exact premium-journals `parent_forum_channel_id`;
- a non-empty title (titles need not be unique);
- boolean `archived`, which determines its required enumeration pass;
- at least one exact, row-owned `identity_evidence` record;
- `starter_message_evidence`;
- position-verified `first_message_evidence`; and
- position-verified, `cutoff_bounded=true` `last_message_evidence`.

Forum-card identity evidence uses the exact DOM value:

```json
{
  "method": "forum_card_data_list_item_id",
  "forum_card_data_list_item_id": "forum-channel-list-1283941772577472643___1508933293322801183",
  "enumeration_pass": "active",
  "observed_at_utc": "2026-07-21T05:08:00Z",
  "source_ref": "discord-ui:premium-journals:active:card-1",
  "authenticated": true,
  "source_scope": "discord_only",
  "outside_sources_used": false
}
```

Authenticated thread-URL identity evidence uses an exact Discord URL with no
message suffix:

```json
{
  "method": "authenticated_discord_thread_url",
  "thread_url": "https://discord.com/channels/1167376964680691732/1508933293322801183",
  "enumeration_pass": "discoverable_archived",
  "observed_at_utc": "2026-07-21T05:18:00Z",
  "source_ref": "discord-ui:premium-journals:archive:row-1",
  "authenticated": true,
  "source_scope": "discord_only",
  "outside_sources_used": false
}
```

Every starter/first/last object uses an authenticated message permalink whose
guild, thread, and message snowflakes match the row:

```json
{
  "role": "last_message_at_or_before_cutoff",
  "method": "authenticated_discord_message_permalink",
  "message_id": "1520000000000000000",
  "permalink": "https://discord.com/channels/1167376964680691732/1508933293322801183/1520000000000000000",
  "position_verified": true,
  "cutoff_bounded": true,
  "observed_at_utc": "2026-07-21T05:20:00Z",
  "source_ref": "discord-ui:thread:1508933293322801183:last-before-cutoff",
  "authenticated": true,
  "source_scope": "discord_only",
  "outside_sources_used": false
}
```

Use roles `thread_starter`, `first_message`, and
`last_message_at_or_before_cutoff` for the three respective objects. Message
snowflake timestamps must be strictly before the cutoff. Observation timestamps
must not be after `capture_completed_at_utc`; identity observations must also
fall within their enumeration pass.

Methods containing `attachment` or `cdn` are rejected as exact identity or
message evidence, even if the value happens to look like a Discord snowflake.

## Run

After the full local day closes and the raw file is complete:

```powershell
python merge_forum_thread_inventory.py `
  --top-level-inventory raw/post_cutoff_top_level_inventory.json `
  --forum-thread-inventory raw/forum_thread_inventory.json `
  --ordinary-thread-inventory raw/ordinary_thread_inventory.json `
  --output working/full_server_channel_inventory_complete.json
```

The destination must not already exist. There is intentionally no force or
overwrite option. Publication is an atomic no-overwrite operation. The output
records the SHA-256 and byte size of all three inputs and embeds pass-level
completeness evidence.

For a separately named release candidate, run validation again against the same
validated inputs and choose a new path under `working/` or a release directory. Do
not point `--output` at either input.

## Builder compatibility

The output has `inventory_complete=true` and one `containers` array containing:

- 38 rows with `inventory_layer=top_level_container`; and
- one row per exact thread with `inventory_layer=observed_forum_thread`, parent
  `1283941772577472643`, and parent-forum coverage attribution; and
- one row per exact non-forum thread with
  `inventory_layer=observed_ordinary_thread`.

`build_corpus.py --inventory working/full_server_channel_inventory_complete.json`
therefore sees the exact top-level count, exact forum-thread rows, and non-empty
active/archive completion evidence. The merger's tests call the corpus builder's
own inventory normalizer to prevent schema drift.

## Tests

```powershell
python -m unittest -v test_merge_forum_thread_inventory.py
```

The focused suite covers duplicate titles, duplicate IDs, attachment-only
identity rejection, incomplete archive pagination, wrong parent, pre-cutoff
enumeration, missing starter/first/last evidence, overwrite refusal, input-hash
preservation, pre-cutoff top-level rejection, missing all-parent audit rejection,
stage/voice message-bearing preservation, and successful corpus-builder
normalization.
