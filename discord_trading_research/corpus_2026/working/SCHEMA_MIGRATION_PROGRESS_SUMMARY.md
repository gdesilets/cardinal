# Schema migration progress

Snapshot: `2026-07-21T15:48:04Z`  
Raw snapshot SHA-256: `3d160b92c5f126a683b203d36ceaab185732396987b672ddf37c8cd512b358e3`  
Immutable baseline membership SHA-256: `57498816b468feaadb64a3f1a5b05950080d3a63d84a5a2b569164b1b67f82af`

## Current result

| Measure | Count |
|---|---:|
| Current canonical segments (all collection work) | 262 |
| Frozen migration baseline segments | 242 |
| Post-baseline newly collected segments | 20 |
| Prior zero-sidecar population | 169 |
| Prior fresh-recapture population | 73 |
| Accepted Collector 2.5 replacements | 26 |
| Accepted valid zero sidecars | 39 |
| Positive fresh recaptures remaining | 42 |
| Zero sidecar revalidations remaining | 130 |
| Pre-2.5 partial restarts remaining | 5 |
| Total resolved | 65 |
| Total remaining | 177 |

The prior `242 = 169 + 73` population reconciles exactly: **True**.

## Post-baseline collection

The 20 newly collected canonical segments are listed separately under `post_baseline_new_segments`. Their path-list SHA-256 is `18cd5b01acb7fbd68881ea5ce446bfabed76b7917b6d390cb5702d87ac4d448f` and their path/size/artifact-SHA inventory hash is `ecceb9cd03f50041b44bba8b13d37aca1355c93db1fae95ae6983962eec8954d`. They do not change migration classifications or the 242 denominator.

## Schedule validation

The current two-tab schedule covers 177 of 177 remaining migration segments with the required action type. It omits or misroutes **0** positive pre-2.5 Live recaptures. Every remaining positive pre-v2.5 Live artifact is routed to fresh recapture; none is misrouted to completion-evidence refresh.

Omitted Live dates: none.

All remaining zero-result candidates and all pre-2.5 partial checkpoints have an explicit schedule route.

## Quarantine

The manifest separately indexes 48 historical quarantined data artifacts and 48 quarantine note artifacts. These do not inflate the 242 canonical-segment denominator.
