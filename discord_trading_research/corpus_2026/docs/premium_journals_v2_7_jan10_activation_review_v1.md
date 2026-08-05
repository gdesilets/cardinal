# Premium Journals v2.7 Jan10 activation review package

This package is immutable review evidence only. It does not activate v2.7,
authorize a Discord query, resolve a live route, write a schedule, create a
canonical, create a collection stage, or publish an activation marker.

The exact target is one America/Chicago local day, `2026-01-10`, using:

`in:premium-journals after:2026-01-09 before:2026-01-11`

The package is freshly bound to the current schedule at SHA-256
`64ab77a9520dbc80d072d3b51347169c825eb60eba4c6a6b6bc363b37647901a`
and 975,585 bytes. It also binds the immutable Jan9 supersession manifest at
SHA-256 `711e540a5e032194496f1763b8144f6ff27b5ee77ca656865253270205f0a322`
and 7,202 bytes. Jan9 remains v2.6-only and can never confer Jan10 authority.

The frozen query-timing checklist remains on
`HOLD_PENDING_V2_7_INDEPENDENT_AUDIT_AND_ACTIVATION`. Its current submission
allowance is zero. A PASS audit of this package still does not activate or
authorize collection; a separate future authority transaction would be needed.

## Immutable publication

The builder publishes four files under
`working/premium_journals_v2_7_jan10_activation_review_v1/`, in this order:

1. `pre_activation_schedule.json` — the exact unparsed schedule bytes.
2. `activation_plan.json` — disabled Jan10 plan and all protected bindings.
3. `prepublication_audit_bundle.json` — rederivation instructions and bindings.
4. `review_package_manifest.json` — final review-package publication record.

The manifest is a package-completeness record, not an activation marker. Each
write is exclusive, no-clobber, same-directory, fsynced, and atomic. Publication
is serialized by an OS-backed lock outside the package directory. Exact replay
is idempotent; a byte collision fails closed.

## Five required gates

The plan, audit bundle, and manifest name and require all five gates:

- exclusive OS publication lock;
- crash-safe immutable no-clobber publication;
- non-authoritative reader state machine;
- exact-snapshot recovery with tamper failure;
- marker-aware no-write validation.

The lock-free reader snapshots the schedule, Jan9 supersession evidence,
Jan10 HOLD checklist, Jan9 accepted canonical and audits, historical v2.7
capability inputs, every protected code/test file, all package files, and all
target-absence predicates before and after validation.

Reader classifications are:

- `PRE_ACTIVATION`: no package exists and all frozen preconditions hold;
- `FAIL_CLOSED_RECOVERY_REQUIRED`: an exact publication prefix exists;
- `REVIEW_PACKAGE_READY`: the complete exact package exists, pending audit;
- `INDEPENDENT_AUDIT_PASSED_NO_AUTHORITY`: a separately written exact PASS
  audit exists, but activation and collection remain disabled;
- `FAIL_CLOSED` or `FAIL_CLOSED_SNAPSHOT_CHANGED`: tamper, drift, a marker,
  target appearance, bad audit, wrong root, or a read-time race was detected.

No reader state ever maps to `LIVE`, `ACTIVE`, or `AUTHORIZED`.

## Independent audit

The independent report, if created, belongs at
`working/premium_journals_v2_7_jan10_activation_review_v1/independent_prepublication_audit.json`.
It must bind the exact hash and byte count of all four package files, independently
rederive every frozen input and protected source binding, record all five test
suite commands/counts/exit codes (Jan10 review, generic activation/recovery,
schedule regression, v2.7 Node collector, and v2.7 Python provenance), record
the schedule validator result, report
identical before/after write-free snapshot signatures, prove every absence
predicate, report evidence for all five gates as PASS, and explicitly state that
it confers no authority. A bare or self-asserted `PASS` is rejected.
The frozen package manifest truthfully records that this report was absent at
package publication and does not self-assert independent approval.

Any source edit, schedule change, Jan9 evidence change, target/stage appearance,
or package correction requires a new versioned package. Existing immutable
package bytes must not be overwritten.
