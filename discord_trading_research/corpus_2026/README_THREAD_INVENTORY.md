# Post-cutoff top-level and ordinary-thread inventory gate

Final release requires two browser-owned inputs in addition to the
premium-journals forum inventory:

- `raw/post_cutoff_top_level_inventory.json`
- `raw/ordinary_thread_inventory.json`

Both must be produced from the authenticated Discord UI after
`2026-07-21T05:00:00Z`. The merger is fail closed: absence of a thread surface
must be recorded as an audited finding, not inferred from a missing row.

## Fresh top-level resnapshot

The post-cutoff file repeats the requested local window and the exact 38 channel
rows. It must declare `inventory_complete: true`, `status: "complete"`, and:

```json
{
  "capture_as_of_utc": "2026-07-21T05:04:00Z",
  "source_scope": "discord_only",
  "outside_sources_used": false,
  "accessible_scope": {
    "top_level_containers": {
      "declared_complete": true,
      "expected_count": 38,
      "status": "complete"
    },
    "post_cutoff_navigation_resnapshot": {
      "declared_complete": true,
      "status": "complete",
      "required_capture_at_or_after_utc": "2026-07-21T05:00:00Z",
      "completion_evidence": {
        "authenticated": true,
        "source_scope": "discord_only",
        "outside_sources_used": false,
        "navigation_pass_complete": true,
        "terminal_state_observed": true,
        "capture_completed_at_utc": "2026-07-21T05:04:00Z",
        "source_refs": ["discord-ui:server-navigation:terminal"]
      }
    }
  },
  "channels": []
}
```

The original `full_server_channel_inventory.json` remains useful as a baseline,
but its July 20 capture time is before the requested window closes. It cannot be
renamed or edited into a valid final snapshot.

Stage and voice channels are message-bearing when their Discord text surfaces
are searchable. `Live` and `vc` therefore remain part of the 38-container
message-completeness contract.

## Ordinary-thread census

The ordinary-thread root repeats the same guild, requested window, and cutoff:

```json
{
  "schema_version": "1.0",
  "guild_id": "1167376964680691732",
  "source_scope": "discord_only",
  "outside_sources_used": false,
  "inventory_complete": true,
  "status": "complete",
  "requested_local_window": {},
  "data_cutoff_utc": "2026-07-21T05:00:00Z",
  "capture_completed_at_utc": "2026-07-21T05:40:00Z",
  "reported_thread_count": 0,
  "parent_audits": [],
  "threads": []
}
```

`parent_audits` must contain exactly one row for every exact top-level ID in the
fresh resnapshot: 38 unique rows and no extras. Every row requires
`authenticated: true`, Discord-only provenance, `status: "complete"`, a real
post-cutoff `completed_at_utc`, nonempty `source_refs`, boolean `applicable`, and
a nonempty `applicability_basis`.

For a non-applicable parent, omit `enumeration_passes`; this is an explicit
authenticated finding, not a default. The premium-journals forum parent is
non-applicable here because its threads are proven by the separate forum
inventory.

For an applicable parent, `enumeration_passes` must contain both `active` and
`discoverable_archived`. Each pass requires:

- the exact `parent_channel_id`;
- an allowed authenticated Discord enumeration method;
- complete status, pagination, and terminal-state evidence;
- no remaining cursor;
- post-cutoff start and completion timestamps;
- nonempty Discord source references; and
- unique exact `thread_ids` reconciled to `reported_thread_count`.

An empty pass is valid only when the UI terminal state and zero count were
actually observed.

Every `threads` row requires exact `thread_id`, exact `parent_channel_id`, a
nonempty title, boolean `archived`, and one of `public_thread`, `private_thread`,
or `announcement_thread`. Its active/archive membership must agree with the
parent pass. Exact identity evidence is an authenticated Discord thread URL
owned by that row:

```json
{
  "thread_id": "1490000000000000000",
  "parent_channel_id": "1359593949110472777",
  "title": "Example public thread",
  "thread_type": "public_thread",
  "archived": false,
  "identity_evidence": [
    {
      "method": "authenticated_discord_thread_url",
      "thread_url": "https://discord.com/channels/1167376964680691732/1490000000000000000",
      "enumeration_pass": "active",
      "observed_at_utc": "2026-07-21T05:32:00Z",
      "source_ref": "discord-ui:ordinary-thread:1490000000000000000",
      "authenticated": true,
      "source_scope": "discord_only",
      "outside_sources_used": false
    }
  ]
}
```

Attachment/CDN paths, titles, screenshots without an exact owning URL, and
channel-shaped numbers in message content are never accepted as exact identity.

## Merge

```powershell
python merge_forum_thread_inventory.py `
  --top-level-inventory raw/post_cutoff_top_level_inventory.json `
  --forum-thread-inventory raw/forum_thread_inventory.json `
  --ordinary-thread-inventory raw/ordinary_thread_inventory.json `
  --output working/full_server_channel_inventory_complete.json
```

The destination is atomic and write-once. Successful output binds all three
inputs by SHA-256 and records the exact top-level, forum-thread, ordinary-thread,
and total container counts.
