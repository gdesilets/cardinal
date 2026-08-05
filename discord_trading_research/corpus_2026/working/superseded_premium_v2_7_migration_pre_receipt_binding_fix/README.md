# Superseded pre-audit draft

These immutable JSON files were rejected during independent audit because the pure activation projection validated the audit receipt schema but did not re-read and hash-check the bound audit-report file. No activation, collection, promotion, receipt, or schedule mutation occurred.

The corrected candidate at the canonical `working/` path verifies report path, SHA-256, and byte count for both activation and rollback projections. Reviewers must not use files in this directory as activation inputs.
