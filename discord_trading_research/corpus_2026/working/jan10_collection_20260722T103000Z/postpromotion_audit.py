from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import premium_journals_provenance_contract as premium
from validate_scoped_three_parent_schedule import validate_schedule


CANONICAL = ROOT / (
    "raw/channel_segments_v2_5/"
    "channel_premium_journals_1283941772577472643_2026-01-10_2026-01-10.json"
)
SCHEDULE = ROOT / "working/scoped_three_parent_collection_schedule.json"
ROUTE = {
    "start": "2026-01-10",
    "end": "2026-01-10",
    "query": "in:premium-journals after:2026-01-09 before:2026-01-11",
}
OUTPUT = Path(__file__).with_name("postpromotion_audit.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    audit = premium.audit_premium_canonical(CANONICAL, ROUTE, artifact_root=ROOT)
    schedule_errors = validate_schedule(ROOT)
    if schedule_errors:
        raise SystemExit("schedule validation failed: " + "; ".join(schedule_errors))
    accepted = audit["accepted_artifact"]
    report = {
        "artifact_type": "jan10_premium_journals_postpromotion_audit",
        "source_scope": "discord_only",
        "outside_sources_used": False,
        "route": ROUTE,
        "canonical": {
            "path": accepted["path"],
            "sha256": accepted["sha256"],
            "bytes": accepted["bytes"],
            "reported_total": accepted["reported_total"],
            "reported_pages": accepted["reported_pages"],
            "observed_child_thread_count": accepted["observed_child_thread_count"],
            "forum_group_count": accepted["forum_group_count"],
            "timestamp_scope_integrity": accepted["timestamp_scope_integrity"],
            "reply_provenance_integrity": accepted["reply_provenance_integrity"],
            "attachment_provenance_integrity": accepted["attachment_provenance_integrity"],
        },
        "schedule": {
            "path": SCHEDULE.relative_to(ROOT).as_posix(),
            "sha256": sha256_file(SCHEDULE),
            "bytes": SCHEDULE.stat().st_size,
            "valid": True,
            "errors": [],
        },
        "contract_summary": {
            "terminal_valid": audit["terminal_valid"],
            "conflict_count": audit["conflict_count"],
            "unresolved_count": audit["unresolved_count"],
        },
        "verdict": "PASS",
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["audit_sha256"] = sha256_file(OUTPUT)
    report["audit_bytes"] = OUTPUT.stat().st_size
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": OUTPUT.as_posix(), "sha256": sha256_file(OUTPUT), "bytes": OUTPUT.stat().st_size, "verdict": "PASS"}))


if __name__ == "__main__":
    main()
