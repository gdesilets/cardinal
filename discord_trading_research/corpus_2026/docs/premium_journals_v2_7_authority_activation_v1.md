# Superseded Jan. 9 Premium Journals v2.7 activation draft

**Non-authoritative:** Jan. 9 remains on v2.6 and this draft's public APIs are unconditionally blocked from writing a plan, committing, or returning a live route for any root, including an archived copy. Its exact pre-activation schedule was preserved only as superseded evidence. No Jan. 9 activation preimage, plan, activation-plan audit, receipt, projection bundle, rollback receipt, or commit marker exists at an active package path. The first future v2.7 authority plan must target Jan. 10 and bind the new schedule hash after Jan. 9 v2.6 is promoted.

The implementation remains as a tested activation-harness draft. It does not collect Jan. 9, create a canonical, approve a canonical, or promote data into Cardinal.

## Explicit narrowing of the audited candidate

The frozen candidate used a general `authority_enabled: true` flag and described future promotion as allowed. The activation plan retains that general route-selection flag but narrows the committed state explicitly:

- `live_collection_enabled: true`
- `collection_authority_enabled: true`
- `canonical_authority_enabled: false`
- `promotion_allowed: false`
- `canonical_present_at_activation: false`
- `canonical_promoted: false`
- `status: active_v2_7_collection_pending_qa`

The exact delta is stored in the immutable activation plan and requires its own independent PASS report before a receipt may be created. A later canonical needs a separate promotion receipt and external commit marker.

## Atomic activation chain

The executor preserves the exact pre-activation schedule bytes first. The activation receipt binds the candidate, readiness report, original independent audit, independently reviewed activation plan, exact pre-image, original schedule hash, planned route delta, and preservation hashes. The schedule projection retires only the exact Jan. 9 v2.6 route, installs exactly one Jan. 9 v2.7 collection route, and leaves every other route and pre-existing top-level object equal.

The projected schedule is written and flushed to a temporary file in the schedule directory and atomically replaces the live schedule. The immutable external commit marker is written last and binds the receipt, pre-image, projection bundle, and exact activated-schedule bytes. Readers must validate that marker before treating v2.7 as live. A missing or invalid marker makes the preserved v2.6 pre-image the effective authority.

If a process-level marker write fails, the executor restores the exact raw pre-image before returning the error. If power is lost after the schedule replacement but before the marker becomes visible, an exact replay may finish the commit only after independently revalidating the plan, plan audit, receipt, projection bundle, activated schedule bytes, and activation-time absence controls. Any mismatch remains fail-closed and cannot resume.

An OS-backed lock spans every precheck, immutable publication, schedule replacement, marker publication, and post-commit validation. Immutable artifacts publish through fully written and flushed sibling temporary files with atomic no-clobber linking; a crash can leave only a non-authoritative temp or a complete final artifact. The schedule target is re-read after replacement. Restoration is permitted only when the live bytes are still the exact projection written by that executor and no valid marker exists.

The reader takes the same lock and distinguishes ordinary pre-activation state from an exact unmarked projection that needs recovery. It returns a live route only from a single schedule byte snapshot bound to the valid marker and reviewed route hash. The retired route in the ordinary 201-route array is never selectable by the v2.7 route resolver.

## Rollback

The projection bundle preserves the exact raw pre-image and rollback projection. Rollback is not preapproved. It requires a separate immutable reviewed rollback receipt, quarantine of any Jan. 9 v2.7 canonical, and an atomic raw-byte restoration of the pre-image without JSON reserialization. Collection failure never auto-revives v2.6.

## Historical draft collection boundary

This Jan. 9 draft can never produce a valid live-repository commit. Its retained rule for a future, newly reviewed implementation is that an operator must run the activation reader first and receive `PASS`, then consume only the exact route returned by the authoritative-route resolver. Page plans and checkpoints remain exclusive and immutable. A page is accepted only after its exact full partition is resolved. No canonical may be consumed or promoted merely because collection finishes; generic v2.7 canonical QA and a separate promotion receipt/marker remain mandatory.
