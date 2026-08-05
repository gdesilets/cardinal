from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "cardinal_partial_fixed_full.sqlite"
MANIFEST = ROOT / "corpus_partial_manifest.json"
EXTERNAL_INVENTORY = ROOT / "inputs" / "full_server_channel_inventory.json"


def rows_from_inventory(value: object) -> list[dict[str, object]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("containers", "channels", "items"):
        rows = value.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
external = json.loads(EXTERNAL_INVENTORY.read_text(encoding="utf-8"))
canonical_inventory = rows_from_inventory(manifest.get("inventory"))
external_inventory = rows_from_inventory(external)
canonical_ids = {
    str(row.get("container_id") or row.get("channel_id"))
    for row in canonical_inventory
    if row.get("container_id") or row.get("channel_id")
}
external_ids = {
    str(row.get("container_id") or row.get("channel_id") or row.get("id"))
    for row in external_inventory
    if row.get("container_id") or row.get("channel_id") or row.get("id")
}
expected_coverage = manifest["coverage"]["containers"]
expected_coverage_status = Counter(
    "partial" if str(row.get("status")) == "gap" else str(row.get("status"))
    for row in expected_coverage
)

uri = DATABASE.resolve().as_uri() + "?mode=ro"
with sqlite3.connect(uri, uri=True) as con:
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_keys = con.execute("PRAGMA foreign_key_check").fetchall()
    db_exact_ids = {
        str(row[0])
        for row in con.execute(
            "SELECT channel_id FROM channel_inventory WHERE exact_id_known=1"
        )
    }
    db_all_ids = {
        str(row[0]) for row in con.execute("SELECT channel_id FROM channel_inventory")
    }
    unit_status = Counter(
        str(row[0])
        for row in con.execute(
            "SELECT status FROM collection_units WHERE unit_type='explicit_coverage'"
        )
    )
    quarantined = con.execute(
        "SELECT message_id,content_text,evidence_trust_state "
        "FROM messages WHERE eligible_for_accepted_evidence=0 "
        "AND evidence_trust_state='quarantined_only' AND LENGTH(TRIM(content_text))>20 "
        "ORDER BY message_id LIMIT 200"
    ).fetchall()
    fts_match = None
    for message_id, content, trust_state in quarantined:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{7,}", str(content)):
            hit = con.execute(
                "SELECT 1 FROM messages_fts WHERE messages_fts MATCH ? "
                "AND message_id=? LIMIT 1",
                (f'"{token}"', message_id),
            ).fetchone()
            if hit:
                fts_match = {
                    "message_id": str(message_id),
                    "token": token,
                    "trust_state": str(trust_state),
                }
                break
        if fts_match:
            break
    qid = fts_match["message_id"] if fts_match else None
    result = {
        "integrity": integrity,
        "foreign_key_violations": len(foreign_keys),
        "discord_only_audit_rows": con.execute(
            "SELECT COUNT(*) FROM v_discord_only_audit"
        ).fetchone()[0],
        "messages": con.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
        "messages_fts": con.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0],
        "occurrences": con.execute(
            "SELECT COUNT(*) FROM message_source_occurrences"
        ).fetchone()[0],
        "analysis_eligible": con.execute(
            "SELECT COUNT(*) FROM v_analysis_eligible_messages"
        ).fetchone()[0],
        "analysis_ineligible": con.execute(
            "SELECT COUNT(*) FROM messages WHERE eligible_for_accepted_evidence=0"
        ).fetchone()[0],
        "quarantined_only": con.execute(
            "SELECT COUNT(*) FROM messages WHERE evidence_trust_state='quarantined_only'"
        ).fetchone()[0],
        "eligible_migration_without_trusted_recapture": con.execute(
            "SELECT COUNT(*) FROM messages m "
            "WHERE m.eligible_for_accepted_evidence=1 "
            "AND EXISTS(SELECT 1 FROM message_source_occurrences o "
            "WHERE o.message_id=m.message_id AND (o.migration_source=1 OR o.quarantined=1)) "
            "AND NOT EXISTS(SELECT 1 FROM message_source_occurrences o "
            "WHERE o.message_id=m.message_id AND o.trusted_canonical=1)"
        ).fetchone()[0],
        "quarantined_fts_match": fts_match,
        "quarantined_in_trust_view": (
            con.execute(
                "SELECT evidence_trust_state,eligible_for_accepted_evidence "
                "FROM v_message_trust_lookup WHERE message_id=?",
                (qid,),
            ).fetchone()
            if qid
            else None
        ),
        "quarantined_in_eligible_view": (
            con.execute(
                "SELECT COUNT(*) FROM v_analysis_eligible_messages WHERE message_id=?",
                (qid,),
            ).fetchone()[0]
            if qid
            else None
        ),
        "canonical_inventory_expected": len(canonical_ids),
        "database_exact_inventory": len(db_exact_ids),
        "missing_canonical_exact_ids": sorted(canonical_ids - db_exact_ids),
        "extra_database_exact_ids": sorted(db_exact_ids - canonical_ids),
        "external_top_level_expected": len(external_ids),
        "missing_external_top_level_ids": sorted(external_ids - db_exact_ids),
        "database_surrogate_channels": len(db_all_ids - db_exact_ids),
        "explicit_coverage_units": sum(unit_status.values()),
        "explicit_coverage_status": dict(sorted(unit_status.items())),
        "expected_coverage_units": len(expected_coverage),
        "expected_coverage_status": dict(sorted(expected_coverage_status.items())),
        "source_segments": con.execute(
            "SELECT COUNT(*) FROM source_segments"
        ).fetchone()[0],
        "source_segments_with_channel": con.execute(
            "SELECT COUNT(*) FROM source_segments WHERE channel_id IS NOT NULL"
        ).fetchone()[0],
        "source_segment_message_total": con.execute(
            "SELECT COALESCE(SUM(message_count),0) FROM source_segments"
        ).fetchone()[0],
        "source_segment_occurrence_total": con.execute(
            "SELECT COALESCE(SUM(occurrence_count),0) FROM source_segments"
        ).fetchone()[0],
    }

checks = {
    "integrity_ok": result["integrity"] == "ok",
    "foreign_keys_ok": result["foreign_key_violations"] == 0,
    "discord_only_ok": result["discord_only_audit_rows"] == 0,
    "message_fts_parity": result["messages"] == result["messages_fts"],
    "migration_requires_recapture": result[
        "eligible_migration_without_trusted_recapture"
    ]
    == 0,
    "quarantined_searchable": result["quarantined_fts_match"] is not None,
    "quarantined_ineligible": result["quarantined_in_eligible_view"] == 0,
    "canonical_inventory_exact": not result["missing_canonical_exact_ids"]
    and not result["extra_database_exact_ids"],
    "external_top_level_subset_complete": not result["missing_external_top_level_ids"],
    "coverage_units_exact": result["explicit_coverage_units"]
    == result["expected_coverage_units"],
    "coverage_status_exact": result["explicit_coverage_status"]
    == result["expected_coverage_status"],
    "source_segments_linked": result["source_segments"]
    == result["source_segments_with_channel"],
}
result["checks"] = checks
result["status"] = "passed" if all(checks.values()) else "failed"
print(json.dumps(result, indent=2, ensure_ascii=False, default=list))
