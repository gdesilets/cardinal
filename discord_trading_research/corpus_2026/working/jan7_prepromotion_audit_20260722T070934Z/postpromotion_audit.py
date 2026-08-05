from __future__ import annotations

import hashlib
import json
import pathlib
import sys


AUDIT_DIR = pathlib.Path(__file__).resolve().parent
CORPUS_ROOT = AUDIT_DIR.parents[1]
SOURCE_ROOT = CORPUS_ROOT / (
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-07_20260722T055743Z/"
    "v2_6_revalidated"
)
FILENAME = (
    "channel_premium_journals_1283941772577472643_"
    "2026-01-07_2026-01-07.json"
)
SOURCE = SOURCE_ROOT / FILENAME
TARGET = CORPUS_ROOT / "raw/channel_segments_v2_5" / FILENAME
LEGACY = CORPUS_ROOT / "raw/channel_segments" / FILENAME
EXPECTED_SHA256 = "19486bee534ac150e76d70cc2f070ba07735c77ded7846e9ad090c026a81cb72"
EXPECTED_BYTES = 2_919_929
EXPECTED_TREE_SHA256 = "9a1c9ecb843e216cb2b8e11b5fb9cb610601e0f46392e7866e15980447104423"
ROUTE = {
    "start": "2026-01-07",
    "end": "2026-01-07",
    "query": "in:premium-journals after:2026-01-06 before:2026-01-08",
    "expected_canonical_path": (
        "raw/channel_segments_v2_5/"
        "channel_premium_journals_1283941772577472643_"
        "2026-01-07_2026-01-07.json"
    ),
}

sys.path.insert(0, str(CORPUS_ROOT))
import premium_journals_provenance_contract as premium  # noqa: E402


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_manifest(root: pathlib.Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "file_count": len(rows),
        "total_bytes": sum(int(row["bytes"]) for row in rows),
        "tree_manifest_sha256": hashlib.sha256(encoded).hexdigest(),
    }


errors: list[str] = []
for label, path in (("source", SOURCE), ("target", TARGET)):
    if not path.is_file():
        errors.append(f"{label}_missing")
    else:
        if path.stat().st_size != EXPECTED_BYTES:
            errors.append(f"{label}_bytes_mismatch")
        if sha256_file(path) != EXPECTED_SHA256:
            errors.append(f"{label}_sha256_mismatch")
stage_tree_before = tree_manifest(SOURCE_ROOT)
if stage_tree_before != {
    "file_count": 198,
    "total_bytes": 3_480_038,
    "tree_manifest_sha256": EXPECTED_TREE_SHA256,
}:
    errors.append("stage_tree_mismatch")
if LEGACY.exists():
    errors.append("legacy_exists")
if LEGACY.with_suffix(".partial.json").exists():
    errors.append("legacy_partial_exists")
if TARGET.with_suffix(".partial.json").exists():
    errors.append("canonical_partial_exists")

audit = premium.audit_premium_canonical(TARGET, ROUTE, artifact_root=CORPUS_ROOT)
accepted = audit["accepted_artifact"]
if audit.get("terminal_valid") is not True:
    errors.append("terminal_invalid")
if audit.get("unresolved_count") != 0:
    errors.append("unresolved_nonzero")
if audit.get("conflict_count") != 0:
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
stage_tree_after = tree_manifest(SOURCE_ROOT)
if stage_tree_after != stage_tree_before:
    errors.append("stage_tree_changed_during_postaudit")

result = {
    "status": "PASS" if not errors else "FAIL",
    "v2_7_involved": False,
    "source": {
        "path": SOURCE.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_file(SOURCE),
        "bytes": SOURCE.stat().st_size,
    },
    "canonical": {
        "path": TARGET.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_file(TARGET),
        "bytes": TARGET.stat().st_size,
        "collector_version": accepted.get("collector_version"),
        "reported_total": accepted.get("reported_total"),
        "captured_rows": accepted.get("captured_rows"),
        "reported_pages": accepted.get("reported_pages"),
        "message_id_set_sha256": accepted.get("message_id_set_sha256"),
        "observed_child_thread_count": accepted.get("observed_child_thread_count"),
        "observed_child_thread_id_set_sha256": accepted.get("observed_child_thread_id_set_sha256"),
        "forum_group_count": accepted.get("forum_group_count"),
        "source_file_count": len(accepted.get("source_files") or []),
        "source_file_set_sha256": accepted.get("source_file_set_sha256"),
        "terminal_valid": audit.get("terminal_valid"),
        "unresolved_count": audit.get("unresolved_count"),
        "conflict_count": audit.get("conflict_count"),
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
    "stage_tree_before": stage_tree_before,
    "stage_tree_after": stage_tree_after,
    "source_equals_canonical": SOURCE.read_bytes() == TARGET.read_bytes(),
    "canonical_partial_absent": not TARGET.with_suffix(".partial.json").exists(),
    "legacy_absent": not LEGACY.exists(),
    "legacy_partial_absent": not LEGACY.with_suffix(".partial.json").exists(),
    "errors": errors,
}
rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
(AUDIT_DIR / "postpromotion_audit.json").write_text(rendered, encoding="utf-8")
print(rendered, end="")
raise SystemExit(0 if not errors else 1)
