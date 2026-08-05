# Two-tab Discord collection handoff

The root operator confirmed the collector patch gate before browser work. Evidence recorded in `working/two_tab_collection_schedule.json` includes the 38/38 collector tests, 16/16 collector QA tests, 173/173 Python tests, and a successful partial end-to-end dry corpus build.

Collector A keeps the existing Live tab. It owns all Live work, Questions, Newsfeed, Boy, Student Breakdowns, Chat from January 1 through April 30, and its 11 assigned empty-channel reverifications. Collector B uses the new dedicated tab. It owns Premium Journals, Levels, VC, Premium Announcements, the small old-schema recaptures, Chat from May 1 through July 20, and its 11 empty-channel reverifications. The Chat shards have no overlapping date or output path.

The schema audit supersedes the earlier 16-date estimate: every positive pre-v2.5 Live artifact requires a fresh recapture, never a synthetic completion sidecar. The authoritative `A_live_legacy_nonempty_recaptures` route therefore lists 54 dates, including the already-promoted January 5 and January 20 artifacts; the live progress manifest determines which listed dates remain.

Run the waves in order:

1. Verify the collector patch, run a fresh read-only scan, confirm no in-flight write, and acquire path leases.
2. Repair legacy evidence. Interleave Collector A's heavy Live days with Collector B's <=4-page recount, small recaptures, and empty checks.
3. Handle the five partial checkpoints. Resume only if every retained row can satisfy the patched provenance contract; otherwise preserve the partial and restart fresh in staging.
4. Run bulk capture using the exact disjoint generators. Only one heavy search may paginate at a time across the account.
5. Refresh durable completion evidence for the 147 Live dates whose prior canonical result is zero. Each requires three fresh empty observations; any positive result is diverted to staged fresh recapture.
6. Pause Collector A. Collector B serially records all 38 final full-window counts, then run local release evidence and the 199-container/thread reconciliation.

Use a 45-second tab start offset, at least 60 seconds between normal searches, and a global 300-second pause after any throttle. Never overwrite existing complete raw evidence: stage, validate, hash/preserve the old artifact, and then promote exactly one canonical replacement.
