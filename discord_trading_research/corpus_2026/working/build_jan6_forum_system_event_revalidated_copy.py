from __future__ import annotations

"""Create the append-only Jan 6 timestamp-revalidated copy; never touch stage."""

import copy
import json
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import premium_journals_system_event_timestamp_v1 as v1  # noqa: E402


SOURCE = ROOT / "raw/quarantine_collection_errors/terra_premium_journals_daily_2026-01-06_20260722T041222Z/v2_6_revalidated/channel_premium_journals_1283941772577472643_2026-01-06_2026-01-06.json"
EVIDENCE = SOURCE.parent / "system_event_dom_evidence_v1"
TARGET_DIR = SOURCE.parent / "system_event_timestamp_revalidated_v1"
TARGET = TARGET_DIR / SOURCE.name
TARGET_IDS = ("1458135984737747005", "1458135642662895720")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def main() -> None:
    if TARGET.exists() or v1.sidecar_path(TARGET).exists():
        raise RuntimeError("append_only_target_already_exists")
    source_bytes = SOURCE.read_bytes()
    source = json.loads(source_bytes.decode("utf-8"))
    copy_payload = copy.deepcopy(source)
    source_by_id = {str(row.get("message_id")): row for row in source["messages"]}
    copy_by_id = {str(row.get("message_id")): row for row in copy_payload["messages"]}
    for message_id in TARGET_IDS:
        copy_by_id[message_id].update(v1._expected_correction(source_by_id[message_id]["timestamp_utc"]))
    write_json(TARGET, copy_payload)
    manifest = EVIDENCE / "manifest.json"
    records = []
    for message_id in TARGET_IDS:
        source_row = source_by_id[message_id]
        copy_row = copy_by_id[message_id]
        observation = EVIDENCE / f"message_{message_id}.normalized_dom_observation.json"
        records.append({
            "status": "passed",
            "evidence_type": v1.EVIDENCE_TYPE,
            "message_id": message_id,
            "source_row_sha256": v1.row_sha256(source_row),
            "revalidated_row_sha256": v1.row_sha256(copy_row),
            "effective_correction": v1._expected_correction(source_row["timestamp_utc"]),
            "route": {
                "guild_id": v1.GUILD_ID,
                "parent_forum_channel_id": v1.PARENT_FORUM_ID,
                "start": "2026-01-06", "end": "2026-01-06", "timezone": "America/Chicago",
                "query": "in:premium-journals after:2026-01-05 before:2026-01-07",
                "page_number": source_row["page_number"],
                "forum_group_navigation_evidence_key": source_row["forum_group_navigation_evidence_key"],
                "exact_permalink": source_row["exact_permalink"],
            },
            "dom_observation": {"path": rel(observation), "sha256": v1.sha256_file(observation), "bytes": observation.stat().st_size},
        })
    sidecar = {
        "schema_version": v1.SCHEMA_VERSION,
        "artifact_type": v1.ARTIFACT_TYPE,
        "source_scope": "discord_only",
        "outside_sources_used": False,
        "source_original": {"path": rel(SOURCE), "sha256": v1.sha256_file(SOURCE), "bytes": SOURCE.stat().st_size},
        "revalidated_artifact": {"path": rel(TARGET), "sha256": v1.sha256_file(TARGET), "bytes": TARGET.stat().st_size},
        "dom_evidence_manifest": {"path": rel(manifest), "sha256": v1.sha256_file(manifest), "bytes": manifest.stat().st_size},
        "revalidations": records,
    }
    write_json(v1.sidecar_path(TARGET), sidecar)
    print(json.dumps({"target": rel(TARGET), "target_sha256": v1.sha256_file(TARGET), "sidecar": rel(v1.sidecar_path(TARGET)), "sidecar_sha256": v1.sha256_file(v1.sidecar_path(TARGET))}, indent=2))


if __name__ == "__main__":
    main()
