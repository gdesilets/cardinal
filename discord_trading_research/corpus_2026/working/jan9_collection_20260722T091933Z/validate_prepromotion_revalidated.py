from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


AUDIT_ROOT = Path(__file__).resolve().parent
CORPUS_ROOT = AUDIT_ROOT.parents[1]
PROJECT_ROOT = CORPUS_ROOT.parent
MIRROR_ROOT = PROJECT_ROOT / (
    "j9r"
)
STAGE_RELATIVE = Path(
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-09_20260722T091933Z/"
    "v2_6_revalidated"
)
SOURCE_STAGE = CORPUS_ROOT / STAGE_RELATIVE
MIRROR_STAGE = MIRROR_ROOT / STAGE_RELATIVE
FILENAME = (
    "channel_premium_journals_1283941772577472643_"
    "2026-01-09_2026-01-09.json"
)
ORIGINAL = SOURCE_STAGE / FILENAME
REVALIDATED = (
    SOURCE_STAGE / "system_event_timestamp_revalidated_v1" / FILENAME
)
MIRROR_CANONICAL = MIRROR_ROOT / "raw/channel_segments_v2_5" / FILENAME
NAVIGATION = SOURCE_STAGE / "forum_group_navigation_checkpoints"
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
EXPECTED_ORIGINAL_SHA256 = (
    "02e2df498f63063fa7f5f0c202c133fc3f7599ed10726f49dca14fc34e90c4bc"
)
EXPECTED_REVALIDATED_SHA256 = (
    "399f0df8ef52878442542043c3d64c0a4cb8070bac5dd0b8df58fcebf2df87ae"
)
EXPECTED_SCHEDULE_SHA256 = (
    "0a1fd787f0fbeb6cb142edd028d16daa3c0189027d2d42b82ba40bb209ca18d6"
)
EXPECTED_NAVIGATION_SHA256 = (
    "9b20807d31dbc400f128d94ca7a4d024c47cf17e39e0f11b3e37ab756e5f0a0d"
)
EXPECTED_PROTECTED_SHA256 = (
    "ba59f65424487d24366265a14aeeefd3a209a7931895fbf3defdee2cf951099b"
)
MESSAGE_ID = "1459342322675224696"

sys.path.insert(0, str(CORPUS_ROOT))
sys.path.insert(0, str(CORPUS_ROOT / "qa"))
import premium_journals_provenance_contract as premium  # noqa: E402
import premium_journals_system_event_timestamp_v1 as system_event  # noqa: E402
from qa import validate_corpus  # noqa: E402


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_manifest(root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    encoded = json.dumps(
        records, ensure_ascii=False, separators=(",", ":")
    ).encode()
    return {
        "file_count": len(records),
        "total_bytes": sum(int(record["bytes"]) for record in records),
        "tree_manifest_sha256": sha256_bytes(encoded),
    }


def protected_manifest(stage: Path) -> dict[str, Any]:
    source = stage / FILENAME
    navigation = stage / "forum_group_navigation_checkpoints"
    paths = [source, *sorted(path for path in navigation.rglob("*") if path.is_file())]
    records = [
        {
            "path": path.relative_to(stage).as_posix(),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in paths
    ]
    encoded = json.dumps(
        records, ensure_ascii=False, separators=(",", ":")
    ).encode()
    return {
        "file_count": len(records),
        "total_bytes": sum(int(record["bytes"]) for record in records),
        "tree_manifest_sha256": sha256_bytes(encoded),
    }


errors: list[str] = []
stage_before = tree_manifest(SOURCE_STAGE)
mirror_stage = tree_manifest(MIRROR_STAGE)
protected_before = protected_manifest(SOURCE_STAGE)
mirror_protected = protected_manifest(MIRROR_STAGE)
if stage_before != mirror_stage:
    errors.append("mirror_stage_tree_not_byte_equal")
if protected_before != mirror_protected:
    errors.append("mirror_protected_tree_not_byte_equal")
if protected_before != {
    "file_count": 76,
    "total_bytes": 1_681_238,
    "tree_manifest_sha256": EXPECTED_PROTECTED_SHA256,
}:
    errors.append("protected_v2_6_tree_binding_mismatch")

navigation_binding = tree_manifest(NAVIGATION)
if navigation_binding != {
    "file_count": 75,
    "total_bytes": 215_252,
    "tree_manifest_sha256": EXPECTED_NAVIGATION_SHA256,
}:
    errors.append("navigation_tree_binding_mismatch")
if sha256_file(ORIGINAL) != EXPECTED_ORIGINAL_SHA256 or ORIGINAL.stat().st_size != 1_465_986:
    errors.append("original_binding_mismatch")
if (
    sha256_file(REVALIDATED) != EXPECTED_REVALIDATED_SHA256
    or REVALIDATED.stat().st_size != 1_786_921
    or REVALIDATED.read_bytes() != MIRROR_CANONICAL.read_bytes()
):
    errors.append("revalidated_or_mirror_canonical_binding_mismatch")

original_payload = json.loads(ORIGINAL.read_text(encoding="utf-8"))
revalidated_payload = json.loads(REVALIDATED.read_text(encoding="utf-8"))
expected_payload = copy.deepcopy(original_payload)
expected_row = next(
    row
    for row in expected_payload["messages"]
    if str(row.get("message_id")) == MESSAGE_ID
)
expected_row.update(
    system_event._expected_correction(str(expected_row.get("timestamp_utc") or ""))
)
if expected_payload != revalidated_payload:
    errors.append("revalidated_copy_not_exact_one_row_delta")

try:
    strict = premium.audit_premium_canonical(
        MIRROR_CANONICAL,
        ROUTE,
        artifact_root=MIRROR_ROOT,
    )
except Exception as exc:
    strict = None
    errors.append(f"strict_premium_audit_exception:{type(exc).__name__}:{exc}")

accepted: dict[str, Any] = {}
if strict is not None:
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
    timestamp_modes = (accepted.get("timestamp_scope_integrity") or {}).get(
        "mode_counts"
    )
    if timestamp_modes != {
        system_event.FALLBACK_SOURCE + "_sidecar_revalidated": 1,
        "message_timestamp_aria_exact": 193,
    }:
        errors.append("timestamp_scope_mode_counts_mismatch")

generic_issues: dict[str, list[dict[str, Any]]] = {}
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
    "stage_partial_absent": not ORIGINAL.with_suffix(".partial.json").exists(),
    "schedule_unchanged": sha256_file(SCHEDULE) == EXPECTED_SCHEDULE_SHA256,
}
errors.extend(key for key, passed in guardrails.items() if not passed)
stage_after = tree_manifest(SOURCE_STAGE)
protected_after = protected_manifest(SOURCE_STAGE)
if stage_after != stage_before:
    errors.append("source_stage_changed_during_validation")
if protected_after != protected_before:
    errors.append("protected_v2_6_tree_changed_during_validation")

result = {
    "status": "PASS" if not errors else "FAIL",
    "validated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    "route": ROUTE,
    "original": {
        "path": ORIGINAL.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_file(ORIGINAL),
        "bytes": ORIGINAL.stat().st_size,
    },
    "revalidated": {
        "path": REVALIDATED.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_file(REVALIDATED),
        "bytes": REVALIDATED.stat().st_size,
        "exact_one_row_delta": expected_payload == revalidated_payload,
    },
    "source_stage_tree": stage_before,
    "protected_v2_6_tree": protected_before,
    "navigation_tree": navigation_binding,
    "strict": {
        "completed": strict is not None,
        "terminal_valid": strict.get("terminal_valid") if strict else None,
        "unresolved_count": strict.get("unresolved_count") if strict else None,
        "conflict_count": strict.get("conflict_count") if strict else None,
        "reported_total": accepted.get("reported_total"),
        "reported_pages": accepted.get("reported_pages"),
        "forum_group_count": accepted.get("forum_group_count"),
        "observed_child_thread_count": accepted.get("observed_child_thread_count"),
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
(AUDIT_ROOT / "prepromotion_revalidated_validation.json").write_text(
    rendered, encoding="utf-8"
)
print(rendered, end="")
raise SystemExit(0 if not errors else 1)
