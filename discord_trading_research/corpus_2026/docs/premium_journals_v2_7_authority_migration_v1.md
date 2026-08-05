# Premium Journals v2.7 authority-migration candidate v1

Status: disabled and pending independent review. This record does not enable a collector, permit promotion, mutate the schedule, or change Jan. 1–8 authority.

## Exact scope

The candidate covers only Premium Journals on Jan. 9, 2026 in America/Chicago. The proposed route is one exact local day, uses the exact Discord search `in:premium-journals after:2026-01-08 before:2026-01-10`, writes only to `raw/channel_segments_v2_7`, and checkpoints only under `raw/premium_journals_v2_7_checkpoints/2026-01-09`. Every later day requires its own immutable activation record, so there is no range grammar that can silently overlap the v2.6 schedule.

The record byte-binds the frozen Jan. 1–6 baseline, the Jan. 8 full shadow verification, all seven Jan. 8 page comparison reports, the staged Jan. 8 v2.6 canonical, the current schedule snapshot, and the exact collector and contract implementations. Content evidence remains authenticated Discord-only. Local reports and code are governance evidence, not new content evidence.

## Activation gate

Activation is fail-closed. Before a separate atomic schedule transaction can be considered, all candidate QA must pass, an independent immutable audit receipt must bind this candidate fingerprint, and the projection must re-read the receipt's audit report and verify its exact relative path, SHA-256, and byte count. The Jan. 8 staged canonical must exist at its v2.6 authoritative path with identical bytes, the frozen Jan. 9 v2.6 route must remain unchanged, and neither Jan. 9 canonical may already exist. The Jan. 8 authoritative source gate now passes with SHA-256 `7a9d71adb66ff0317750413c5cb89b459567bd202af3c71a126c4addc134bfb5`; this does not satisfy the separate audit-receipt or atomic-writer requirements and does not authorize activation.

The same atomic transaction must retire exactly the Jan. 9 v2.6 route and install exactly one Jan. 9 v2.7 authority route. It must not delete any route or artifact and must leave every other v2.6 route byte-equivalent. A partial transaction or missing commit receipt leaves the original v2.6 schedule authoritative and makes all v2.7 output non-authoritative.

## Collection and publication semantics

The page plan is created exclusively before group resolution. A direct checkpoint is either created exclusively or an existing exact checkpoint is revalidated and reused byte-for-byte. A page is accepted only when its rows and groups form the exact full partition and every group has one valid checkpoint. The canonical is eligible for QA only after all pages implied by `ceil(reported_total / 25)` are accepted. Generic v2.7 QA then re-derives the entire canonical, source-file set, and reply/attachment provenance.

An activated collection failure is quarantined; it never auto-revives v2.6. Rollback requires a different immutable reviewed receipt whose review report is also re-read and byte/hash-bound, prior quarantine of any v2.7 canonical, atomic restoration of the exact original v2.6 route, and removal of v2.7 authority in the same transaction. Both authorities may never be active together.

## Review commands

Run the focused migration tests, then the generic candidate validator. The validator has a separate activated-canonical mode that requires a candidate, activated schedule, activation receipt, bound independent-audit report, and v2.7 canonical as one proof chain. Candidate and filesystem/source-gate validation now pass; activated-canonical mode remains unavailable until a later reviewed activation transaction and Jan. 9 v2.7 collection actually exist.
