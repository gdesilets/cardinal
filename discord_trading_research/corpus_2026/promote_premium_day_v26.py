from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import premium_journals_provenance_contract as premium
from validate_scoped_three_parent_schedule import validate_schedule


ROOT = Path(__file__).resolve().parent
PREMIUM_ID = "1283941772577472643"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", required=True, help="YYYY-MM-DD")
    parser.add_argument("--stamp", required=True, help="immutable collection run stamp")
    args = parser.parse_args()
    day = args.day
    stamp = args.stamp
    stage = ROOT / (
        "raw/quarantine_collection_errors/"
        f"terra_premium_journals_daily_{day}_{stamp}/v2_6_revalidated"
    )
    source_candidates = sorted(stage.glob(f"primary_{day}_{day}.json"))
    if len(source_candidates) != 1:
        raise SystemExit(f"expected one staged primary for {day}, found {len(source_candidates)}")
    source = source_candidates[0]
    canonical = ROOT / (
        f"raw/channel_segments_v2_5/channel_premium_journals_{PREMIUM_ID}_{day}_{day}.json"
    )
    if canonical.exists():
        raise SystemExit(f"refusing to overwrite existing canonical: {canonical}")
    checkpoint = stage / "forum_group_navigation_checkpoints"
    if not checkpoint.is_dir():
        raise SystemExit(f"missing checkpoint directory: {checkpoint}")
    query = f"in:premium-journals after:{day} before:{day}"
    # The search query is the day before/day after form, not an inclusive query.
    from datetime import date, timedelta

    day_value = date.fromisoformat(day)
    query = (
        f"in:premium-journals after:{(day_value - timedelta(days=1)).isoformat()} "
        f"before:{(day_value + timedelta(days=1)).isoformat()}"
    )
    route = {"start": day, "end": day, "query": query}

    with tempfile.TemporaryDirectory(prefix=f"cardinal_premium_{day}_") as tmp_name:
        tmp = Path(tmp_name)
        tmp_canonical = tmp / canonical.relative_to(ROOT)
        tmp_canonical.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, tmp_canonical)
        tmp_checkpoint = tmp / (
            "raw/quarantine_collection_errors/"
            f"terra_premium_journals_daily_{day}_{stamp}/v2_6_revalidated/"
            "forum_group_navigation_checkpoints"
        )
        shutil.copytree(checkpoint, tmp_checkpoint)
        pre = premium.audit_premium_canonical(tmp_canonical, route, artifact_root=tmp)

    canonical.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, canonical)
    if sha256_file(source) != sha256_file(canonical):
        raise SystemExit("canonical copy is not byte-equal to staged source")

    subprocess.run([sys.executable, str(ROOT / "build_scoped_three_parent_schedule.py")], check=True)
    schedule_errors = validate_schedule(ROOT)
    if schedule_errors:
        raise SystemExit("schedule validation failed: " + "; ".join(schedule_errors))
    post = premium.audit_premium_canonical(canonical, route, artifact_root=ROOT)
    report_dir = ROOT / "working" / f"premium_{day}_collection_{stamp}"
    report_dir.mkdir(parents=True, exist_ok=True)
    schedule = ROOT / "working/scoped_three_parent_collection_schedule.json"
    report = {
        "artifact_type": "premium_journals_v2_6_day_promotion_audit",
        "source_scope": "discord_only",
        "outside_sources_used": False,
        "route": route,
        "stage_path": source.relative_to(ROOT).as_posix(),
        "canonical": post["accepted_artifact"],
        "prepromotion_contract": pre["accepted_artifact"],
        "contract_summary": {
            "terminal_valid": post["terminal_valid"],
            "conflict_count": post["conflict_count"],
            "unresolved_count": post["unresolved_count"],
        },
        "schedule": {
            "path": schedule.relative_to(ROOT).as_posix(),
            "sha256": sha256_file(schedule),
            "bytes": schedule.stat().st_size,
            "valid": True,
        },
        "verdict": "PASS",
    }
    report_path = report_dir / "postpromotion_audit.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "day": day,
        "canonical_sha256": post["accepted_artifact"]["sha256"],
        "canonical_bytes": post["accepted_artifact"]["bytes"],
        "reported_total": post["accepted_artifact"]["reported_total"],
        "reported_pages": post["accepted_artifact"]["reported_pages"],
        "schedule_sha256": sha256_file(schedule),
        "audit_path": report_path.as_posix(),
        "verdict": "PASS",
    }, sort_keys=True))


if __name__ == "__main__":
    main()
