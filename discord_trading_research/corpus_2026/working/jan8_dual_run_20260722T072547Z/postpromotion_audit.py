from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import sys


AUDIT_ROOT = Path(__file__).resolve().parent
CORPUS_ROOT = AUDIT_ROOT.parents[1]
SOURCE_ROOT = CORPUS_ROOT / (
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-08_20260722T072547Z/"
    "v2_6_revalidated"
)
FILENAME = (
    "channel_premium_journals_1283941772577472643_"
    "2026-01-08_2026-01-08.json"
)
SOURCE = SOURCE_ROOT / FILENAME
TARGET = CORPUS_ROOT / "raw/channel_segments_v2_5" / FILENAME
LEGACY = CORPUS_ROOT / "raw/channel_segments" / FILENAME
V27_TARGET = CORPUS_ROOT / "raw/channel_segments_v2_7" / FILENAME
EXPECTED_SHA256 = "7a9d71adb66ff0317750413c5cb89b459567bd202af3c71a126c4addc134bfb5"
EXPECTED_BYTES = 1_231_302
EXPECTED_TREE = {
    "file_count": 86,
    "total_bytes": 1_471_666,
    "tree_manifest_sha256": "82bc960858880db60f2627656705cf36e58494b027e02620ddc290fdde25ab3e",
}
ROUTE = {
    "start": "2026-01-08",
    "end": "2026-01-08",
    "query": "in:premium-journals after:2026-01-07 before:2026-01-09",
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
    encoded = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode()
    return {
        "file_count": len(records),
        "total_bytes": sum(int(record["bytes"]) for record in records),
        "tree_manifest_sha256": sha256_bytes(encoded),
    }


errors: list[str] = []
source_bytes = SOURCE.read_bytes()
target_bytes = TARGET.read_bytes()
if len(source_bytes) != EXPECTED_BYTES or sha256_bytes(source_bytes) != EXPECTED_SHA256:
    errors.append("source_byte_contract_mismatch")
if len(target_bytes) != EXPECTED_BYTES or sha256_bytes(target_bytes) != EXPECTED_SHA256:
    errors.append("target_byte_contract_mismatch")
if source_bytes != target_bytes:
    errors.append("source_target_not_byte_equal")
stage_tree_before = tree_manifest(SOURCE_ROOT)
if stage_tree_before != EXPECTED_TREE:
    errors.append("stage_tree_mismatch")

strict = premium.audit_premium_canonical(TARGET, ROUTE, artifact_root=CORPUS_ROOT)
accepted = strict["accepted_artifact"]
if strict.get("terminal_valid") is not True:
    errors.append("terminal_invalid")
if strict.get("unresolved_count") != 0:
    errors.append("unresolved_nonzero")
if strict.get("conflict_count") != 0:
    errors.append("conflict_nonzero")

generic_issues: dict[str, list[dict[str, object]]] = {}
generic_artifact = validate_corpus.validate_one_segment(
    TARGET,
    guild_id="1167376964680691732",
    window_start=dt.date(2026, 1, 8),
    window_end=dt.date(2026, 1, 8),
    cutoff_utc=dt.datetime(2026, 7, 20, 23, 59, 59, tzinfo=dt.timezone.utc),
    issues=generic_issues,
)
if generic_artifact is None or generic_issues:
    errors.append("generic_segment_validation_failed")
if LEGACY.exists() or LEGACY.with_suffix(".partial.json").exists():
    errors.append("legacy_artifact_exists")
if TARGET.with_suffix(".partial.json").exists():
    errors.append("canonical_partial_exists")
if V27_TARGET.exists() or V27_TARGET.with_suffix(".partial.json").exists():
    errors.append("v2_7_canonical_exists")
stage_tree_after = tree_manifest(SOURCE_ROOT)
if stage_tree_after != stage_tree_before:
    errors.append("stage_tree_changed_during_postaudit")

result = {
    "schema_version": "1.0.0",
    "artifact_type": "premium_journals_v2_6_postpromotion_audit",
    "status": "PASS" if not errors else "FAIL",
    "source": {
        "path": SOURCE.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_bytes(source_bytes),
        "bytes": len(source_bytes),
    },
    "canonical": {
        "path": TARGET.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_bytes(target_bytes),
        "bytes": len(target_bytes),
        "byte_equal_to_source": target_bytes == source_bytes,
        "collector_version": accepted.get("collector_version"),
        "reported_total": accepted.get("reported_total"),
        "captured_rows": accepted.get("captured_rows"),
        "reported_pages": accepted.get("reported_pages"),
        "message_id_set_sha256": accepted.get("message_id_set_sha256"),
        "observed_child_thread_count": accepted.get("observed_child_thread_count"),
        "observed_child_thread_id_set_sha256": accepted.get(
            "observed_child_thread_id_set_sha256"
        ),
        "forum_group_count": accepted.get("forum_group_count"),
        "source_file_count": len(accepted.get("source_files") or []),
        "source_file_set_sha256": accepted.get("source_file_set_sha256"),
        "terminal_valid": strict.get("terminal_valid"),
        "unresolved_count": strict.get("unresolved_count"),
        "conflict_count": strict.get("conflict_count"),
        "full_qa_passed": accepted.get("full_qa_passed"),
    },
    "semantic_integrity": {
        section: accepted.get(section)
        for section in (
            "forum_membership_integrity",
            "forum_navigation_artifact_integrity",
            "timestamp_scope_integrity",
            "reply_provenance_integrity",
            "attachment_provenance_integrity",
        )
    },
    "generic_segment_validation": {
        "artifact_returned": generic_artifact is not None,
        "issue_count": sum(len(rows) for rows in generic_issues.values()),
        "issues": generic_issues,
    },
    "stage_tree_before": stage_tree_before,
    "stage_tree_after": stage_tree_after,
    "stage_tree_unchanged": stage_tree_before == stage_tree_after,
    "canonical_partial_absent": not TARGET.with_suffix(".partial.json").exists(),
    "legacy_absent": not LEGACY.exists(),
    "legacy_partial_absent": not LEGACY.with_suffix(".partial.json").exists(),
    "v2_7_canonical_absent": not V27_TARGET.exists(),
    "v2_7_involved": False,
    "errors": errors,
}
rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
(AUDIT_ROOT / "postpromotion_audit.json").write_text(rendered, encoding="utf-8")
print(rendered, end="")
raise SystemExit(0 if not errors else 1)

