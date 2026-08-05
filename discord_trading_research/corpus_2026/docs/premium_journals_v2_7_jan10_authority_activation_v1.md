# Premium Journals v2.7 Jan10 collection-authority activation

This is a separate transaction from the immutable Jan10 review package. It is
bound to the independently audited review report at SHA-256
`ebb04c1236201dea1a6b92ec1341c087430c64d2485611f4f0044cd83b11e4b2`
and to the unchanged schedule at SHA-256
`64ab77a9520dbc80d072d3b51347169c825eb60eba4c6a6b6bc363b37647901a`.

The exact collection route is the America/Chicago local day `2026-01-10`:

`in:premium-journals after:2026-01-09 before:2026-01-11`

The transaction grants only v2.7 live collection authority. Canonical
authority remains absent, promotion remains forbidden, and Jan9 remains
v2.6-only. The activation itself never submits the query or invokes the
collector.

## Immutable artifacts

The OS-locked transaction writes these files exclusively and in order:

1. exact raw schedule preimage;
2. activation plan;
3. collection-only activation receipt;
4. external authority projection bundle;
5. external commit marker;
6. publisher terminal audit.

Every file is same-directory fsynced and atomically published without replacing
an existing path. Exact replay is idempotent. A crash before the marker leaves
no authority and an exact recoverable prefix. A crash after the marker but
before the terminal audit leaves live collection authority with an explicit
pending-terminal-audit classification; replay can only append the exact audit.

The schedule is never replaced or reserialized. No canonical, `.partial.json`,
checkpoint, collection stage, rollback receipt, browser action, collector call,
or Discord search is produced by this transaction.

## Reader contract

The reader is lock-free and write-free. It validates exact schedule, review
package, independent audit, code/test bindings, activation chain, target
absences, and before/after snapshot equality. Only the complete exact marker
chain resolves the v2.7 route. The terminal state is
`LIVE_COLLECTION_AUTHORIZED`; canonical and promotion flags remain false.
