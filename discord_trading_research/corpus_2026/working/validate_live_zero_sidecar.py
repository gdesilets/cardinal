from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qa"))

from qa import validate_corpus


PREFIX = "channel_live_1329615478716502097"
GUILD_ID = "1167376964680691732"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    day = dt.date.fromisoformat(args.date)
    path = ROOT / "raw" / "channel_segments" / f"{PREFIX}_{args.date}_{args.date}.json"
    sidecar_path = path.with_name(path.name.replace(".json", ".completion-evidence.json"))
    issues: dict[str, list[dict[str, object]]] = collections.defaultdict(list)
    artifact = validate_corpus.validate_one_segment(
        path,
        guild_id=GUILD_ID,
        window_start=dt.date(2026, 1, 1),
        window_end=dt.date(2026, 7, 20),
        cutoff_utc=dt.datetime.now(dt.timezone.utc),
        issues=issues,
    )
    source_bytes = path.read_bytes()
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    evidence = sidecar.get("completion_evidence") or {}
    observations = (evidence.get("stable_empty") or {}).get("observations") or []
    checks = {
        "independent_segment_qa_clean": artifact is not None and not any(issues.values()),
        "source_sha_bound": sidecar.get("source_artifact_sha256")
        == hashlib.sha256(source_bytes).hexdigest(),
        "source_name_bound": sidecar.get("source_artifact_path") == path.name,
        "reported_zero": sidecar.get("reported_total") == 0
        and sidecar.get("reported_pages") == 0,
        "stable_empty_terminal": evidence.get("terminal_state") == "stable_empty",
        "exactly_three_empty_observations": len(observations) == 3
        and all(item.get("state") == "empty_candidate" for item in observations),
        "one_fresh_submission": (evidence.get("search_submission") or {}).get("mode") == "fresh"
        and (evidence.get("search_submission") or {}).get("submission_count") == 1,
        "exact_live_query": evidence.get("query")
        == f"in:Live after:{day - dt.timedelta(days=1)} before:{day + dt.timedelta(days=1)}",
    }
    output = {
        "date": args.date,
        "source_path": str(path),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "sidecar_path": str(sidecar_path),
        "sidecar_sha256": hashlib.sha256(sidecar_path.read_bytes()).hexdigest(),
        "checks": checks,
        "issue_counts": {key: len(value) for key, value in issues.items() if value},
        "submitted_at_utc": (evidence.get("search_submission") or {}).get("submitted_at_utc"),
        "observation_times_utc": [item.get("observed_at_utc") for item in observations],
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
