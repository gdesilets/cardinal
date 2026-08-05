from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import sys


AUDIT_ROOT = Path(__file__).resolve().parent
CORPUS_ROOT = AUDIT_ROOT.parents[1]
PROJECT_ROOT = CORPUS_ROOT.parent
MIRROR_ROOT = PROJECT_ROOT / "jan9_prepromotion_isolated_20260722T091933Z"
SOURCE_ROOT = CORPUS_ROOT / (
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-09_20260722T091933Z/"
    "v2_6_revalidated"
)
FILENAME = (
    "channel_premium_journals_1283941772577472643_"
    "2026-01-09_2026-01-09.json"
)
SOURCE = SOURCE_ROOT / FILENAME
MIRROR_CANONICAL = MIRROR_ROOT / "raw/channel_segments_v2_5" / FILENAME
MIRROR_STAGE = MIRROR_ROOT / (
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-09_20260722T091933Z/"
    "v2_6_revalidated"
)
REAL_CANONICAL = CORPUS_ROOT / "raw/channel_segments_v2_5" / FILENAME
LEGACY = CORPUS_ROOT / "raw/channel_segments" / FILENAME
V27_CANONICAL = CORPUS_ROOT / "raw/channel_segments_v2_7" / FILENAME
SCHEDULE = CORPUS_ROOT / "working/scoped_three_parent_collection_schedule.json"
QUERY = "in:premium-journals after:2026-01-08 before:2026-01-10"
ROUTE = {
    "start": "2026-01-09",
    "end": "2026-01-09",
    "query": QUERY,
    "expected_canonical_path": f"raw/channel_segments_v2_5/{FILENAME}",
}
EXPECTED_ARTIFACT_SHA256 = (
    "02e2df498f63063fa7f5f0c202c133fc3f7599ed10726f49dca14fc34e90c4bc"
)
EXPECTED_ARTIFACT_BYTES = 1_465_986
EXPECTED_SCHEDULE_SHA256 = (
    "0a1fd787f0fbeb6cb142edd028d16daa3c0189027d2d42b82ba40bb209ca18d6"
)

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
source_before = tree_manifest(SOURCE_ROOT)
mirror_stage = tree_manifest(MIRROR_STAGE)
source_bytes = SOURCE.read_bytes()
mirror_bytes = MIRROR_CANONICAL.read_bytes()
if sha256_bytes(source_bytes) != EXPECTED_ARTIFACT_SHA256:
    errors.append("source_sha256_mismatch")
if len(source_bytes) != EXPECTED_ARTIFACT_BYTES:
    errors.append("source_bytes_mismatch")
if source_bytes != mirror_bytes:
    errors.append("mirror_canonical_not_byte_equal")
if source_before != mirror_stage:
    errors.append("mirror_stage_tree_not_byte_equal")

strict = premium.audit_premium_canonical(
    MIRROR_CANONICAL,
    ROUTE,
    artifact_root=MIRROR_ROOT,
)
accepted = strict["accepted_artifact"]
if strict.get("terminal_valid") is not True:
    errors.append("terminal_invalid")
if strict.get("unresolved_count") != 0:
    errors.append("unresolved_nonzero")
if strict.get("conflict_count") != 0:
    errors.append("conflict_nonzero")
for section in (
    "forum_membership_integrity",
    "forum_navigation_artifact_integrity",
    "timestamp_scope_integrity",
    "reply_provenance_integrity",
    "attachment_provenance_integrity",
):
    if (accepted.get(section) or {}).get("passed") is not True:
        errors.append(f"{section}_failed")

generic_issues: dict[str, list[dict[str, object]]] = {}
generic_artifact = validate_corpus.validate_one_segment(
    MIRROR_CANONICAL,
    guild_id="1167376964680691732",
    window_start=dt.date(2026, 1, 9),
    window_end=dt.date(2026, 1, 9),
    cutoff_utc=dt.datetime(2026, 7, 20, 23, 59, 59, tzinfo=dt.timezone.utc),
    issues=generic_issues,
)
if generic_artifact is None or generic_issues:
    errors.append("generic_segment_validation_failed")

guardrails = {
    "real_canonical_absent": not REAL_CANONICAL.exists(),
    "canonical_partial_absent": not REAL_CANONICAL.with_suffix(".partial.json").exists(),
    "legacy_absent": not LEGACY.exists(),
    "legacy_partial_absent": not LEGACY.with_suffix(".partial.json").exists(),
    "v2_7_canonical_absent": not V27_CANONICAL.exists(),
    "stage_partial_absent": not SOURCE.with_suffix(".partial.json").exists(),
    "schedule_unchanged": sha256_bytes(SCHEDULE.read_bytes())
    == EXPECTED_SCHEDULE_SHA256,
}
errors.extend(key for key, passed in guardrails.items() if not passed)
source_after = tree_manifest(SOURCE_ROOT)
if source_after != source_before:
    errors.append("source_tree_changed_during_validation")

result = {
    "schema_version": "1.0.0",
    "artifact_type": "premium_journals_v2_6_jan9_prepromotion_validation",
    "status": "PASS" if not errors else "FAIL",
    "route": ROUTE,
    "source": {
        "path": SOURCE.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_bytes(source_bytes),
        "bytes": len(source_bytes),
        "byte_equal_to_isolated_canonical": source_bytes == mirror_bytes,
    },
    "stage_tree_before": source_before,
    "stage_tree_after": source_after,
    "stage_tree_unchanged": source_before == source_after,
    "isolated_stage_tree": mirror_stage,
    "strict": {
        "terminal_valid": strict.get("terminal_valid"),
        "unresolved_count": strict.get("unresolved_count"),
        "conflict_count": strict.get("conflict_count"),
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
        "forum_membership_integrity": accepted.get("forum_membership_integrity"),
        "forum_navigation_artifact_integrity": accepted.get(
            "forum_navigation_artifact_integrity"
        ),
        "timestamp_scope_integrity": accepted.get("timestamp_scope_integrity"),
        "reply_provenance_integrity": accepted.get("reply_provenance_integrity"),
        "attachment_provenance_integrity": accepted.get(
            "attachment_provenance_integrity"
        ),
    },
    "generic": {
        "artifact_returned": generic_artifact is not None,
        "issue_count": sum(len(rows) for rows in generic_issues.values()),
        "issues": generic_issues,
    },
    "guardrails": guardrails,
    "v2_7_involved": False,
    "errors": errors,
}
rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
(AUDIT_ROOT / "prepromotion_validation.json").write_text(rendered, encoding="utf-8")
print(rendered, end="")
raise SystemExit(0 if not errors else 1)

