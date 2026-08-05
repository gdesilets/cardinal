from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import sys


AUDIT_ROOT = Path(__file__).resolve().parent
CORPUS_ROOT = AUDIT_ROOT.parents[1]
PROJECT_ROOT = CORPUS_ROOT.parent
MIRROR_ROOT = PROJECT_ROOT / "jan8_dual_isolated_20260722T072547Z"
SOURCE_ROOT = CORPUS_ROOT / (
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-08_20260722T072547Z/"
    "v2_6_revalidated"
)
FILENAME = (
    "channel_premium_journals_1283941772577472643_"
    "2026-01-08_2026-01-08.json"
)
SOURCE_PATH = SOURCE_ROOT / FILENAME
MIRROR_CANONICAL = MIRROR_ROOT / "raw/channel_segments_v2_5" / FILENAME
MIRROR_STAGE = MIRROR_ROOT / (
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-08_20260722T072547Z/"
    "v2_6_revalidated"
)
QUERY = "in:premium-journals after:2026-01-07 before:2026-01-09"
ROUTE = {
    "start": "2026-01-08",
    "end": "2026-01-08",
    "query": QUERY,
    "expected_canonical_path": f"raw/channel_segments_v2_5/{FILENAME}",
}

sys.path.insert(0, str(CORPUS_ROOT))
sys.path.insert(0, str(CORPUS_ROOT / "qa"))
import premium_journals_provenance_contract as premium  # noqa: E402
from qa import validate_corpus  # noqa: E402


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def tree_manifest(root: Path) -> dict[str, object]:
    records: list[dict[str, object]] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        content = path.read_bytes()
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_bytes(content),
                "bytes": len(content),
            }
        )
    encoded = json.dumps(records, separators=(",", ":"), ensure_ascii=False).encode()
    return {
        "file_count": len(records),
        "total_bytes": sum(int(record["bytes"]) for record in records),
        "tree_manifest_sha256": sha256_bytes(encoded),
    }


source_before = tree_manifest(SOURCE_ROOT)
mirror_stage = tree_manifest(MIRROR_STAGE)
source_bytes = SOURCE_PATH.read_bytes()
mirror_bytes = MIRROR_CANONICAL.read_bytes()
if source_bytes != mirror_bytes:
    raise SystemExit("Isolated canonical is not byte-equal to the v2.6 stage artifact")
if source_before != mirror_stage:
    raise SystemExit("Isolated stage tree is not byte-equal to the source stage tree")

specialized = premium.audit_premium_canonical(
    MIRROR_CANONICAL,
    ROUTE,
    artifact_root=MIRROR_ROOT,
)

issues: dict[str, list[dict[str, object]]] = {}
generic_artifact = validate_corpus.validate_one_segment(
    MIRROR_CANONICAL,
    guild_id="1167376964680691732",
    window_start=dt.date(2026, 1, 8),
    window_end=dt.date(2026, 1, 8),
    cutoff_utc=dt.datetime(2026, 7, 20, 23, 59, 59, tzinfo=dt.timezone.utc),
    issues=issues,
)
source_after = tree_manifest(SOURCE_ROOT)

accepted = specialized["accepted_artifact"]
real_v26_canonical = CORPUS_ROOT / "raw/channel_segments_v2_5" / FILENAME
real_v27_canonical = CORPUS_ROOT / "raw/channel_segments_v2_7" / FILENAME
real_legacy = CORPUS_ROOT / "raw/channel_segments" / FILENAME
partial = SOURCE_PATH.with_suffix(".partial.json")
result = {
    "schema_version": "1.0.0",
    "artifact_type": "premium_journals_v2_6_stage_validation",
    "status": "PASS"
    if (
        specialized.get("terminal_valid") is True
        and specialized.get("unresolved_count") == 0
        and specialized.get("conflict_count") == 0
        and generic_artifact is not None
        and not issues
        and source_before == source_after
        and not real_v26_canonical.exists()
        and not real_v27_canonical.exists()
        and not real_legacy.exists()
        and not partial.exists()
    )
    else "FAIL",
    "route": ROUTE,
    "source_artifact": {
        "path": SOURCE_PATH.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_bytes(source_bytes),
        "bytes": len(source_bytes),
        "byte_equal_to_isolated_canonical": source_bytes == mirror_bytes,
    },
    "stage_tree_before": source_before,
    "stage_tree_after": source_after,
    "stage_tree_unchanged": source_before == source_after,
    "isolated_stage_tree": mirror_stage,
    "specialized": {
        "terminal_valid": specialized.get("terminal_valid"),
        "unresolved_count": specialized.get("unresolved_count"),
        "conflict_count": specialized.get("conflict_count"),
        "reported_total": accepted.get("reported_total"),
        "captured_rows": accepted.get("captured_rows"),
        "reported_pages": accepted.get("reported_pages"),
        "completion_terminal_state": accepted.get("completion_terminal_state"),
        "message_id_set_sha256": accepted.get("message_id_set_sha256"),
        "observed_child_thread_count": accepted.get("observed_child_thread_count"),
        "observed_child_thread_id_set_sha256": accepted.get(
            "observed_child_thread_id_set_sha256"
        ),
        "forum_group_count": accepted.get("forum_group_count"),
        "forum_navigation_evidence_map_sha256": accepted.get(
            "forum_navigation_evidence_map_sha256"
        ),
        "forum_navigation_unresolved_count": accepted.get(
            "forum_navigation_unresolved_count"
        ),
        "thread_channel_id_conflict_count": accepted.get(
            "thread_channel_id_conflict_count"
        ),
        "forbidden_selected_thread_source_count": accepted.get(
            "forbidden_selected_thread_source_count"
        ),
        "full_qa_passed": accepted.get("full_qa_passed"),
        "source_file_set_sha256": accepted.get("source_file_set_sha256"),
        "source_file_count": len(accepted.get("source_files") or []),
        "reply_provenance_integrity": accepted.get("reply_provenance_integrity"),
        "attachment_provenance_integrity": accepted.get(
            "attachment_provenance_integrity"
        ),
        "timestamp_scope_integrity": accepted.get("timestamp_scope_integrity"),
        "forum_membership_integrity": accepted.get("forum_membership_integrity"),
        "forum_navigation_artifact_integrity": accepted.get(
            "forum_navigation_artifact_integrity"
        ),
    },
    "generic": {
        "artifact_returned": generic_artifact is not None,
        "issue_count": sum(len(rows) for rows in issues.values()),
        "issues": issues,
    },
    "guardrails": {
        "real_v2_6_canonical_absent": not real_v26_canonical.exists(),
        "real_v2_7_canonical_absent": not real_v27_canonical.exists(),
        "real_legacy_canonical_absent": not real_legacy.exists(),
        "stage_partial_absent": not partial.exists(),
    },
}

rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
output_path = AUDIT_ROOT / "v2_6_stage_validation.json"
output_path.write_text(rendered, encoding="utf-8")
print(rendered, end="")
raise SystemExit(0 if result["status"] == "PASS" else 1)
