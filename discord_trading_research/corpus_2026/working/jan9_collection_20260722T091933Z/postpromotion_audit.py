from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


AUDIT_ROOT = Path(__file__).resolve().parent
CORPUS_ROOT = AUDIT_ROOT.parents[1]
STAGE = CORPUS_ROOT / (
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-09_20260722T091933Z/"
    "v2_6_revalidated"
)
FILENAME = (
    "channel_premium_journals_1283941772577472643_"
    "2026-01-09_2026-01-09.json"
)
ORIGINAL = STAGE / FILENAME
SOURCE = STAGE / "system_event_timestamp_revalidated_v1" / FILENAME
SIDECAR = (
    STAGE
    / "system_event_timestamp_revalidated_v1/canonical_bindings_v1"
    / FILENAME.replace(
        ".json", ".forum-system-event-timestamp-revalidation-v1.json"
    )
)
TARGET = CORPUS_ROOT / "raw/channel_segments_v2_5" / FILENAME
LEGACY = CORPUS_ROOT / "raw/channel_segments" / FILENAME
V27 = CORPUS_ROOT / "raw/channel_segments_v2_7" / FILENAME
SCHEDULE = CORPUS_ROOT / "working/scoped_three_parent_collection_schedule.json"
RECONCILIATION = (
    CORPUS_ROOT / "working/premium_journals_scoped_inventory_reconciliation.json"
)
AUTHORIZATION = AUDIT_ROOT / "promotion_authorization.json"
PROMOTION_RECEIPT = AUDIT_ROOT / "promotion_receipt.json"
INDEPENDENT_AUDIT = AUDIT_ROOT / "independent_audit.json"
SCHEDULE_AUTHORIZATION = AUDIT_ROOT / "schedule_rebuild_authorization.json"
QUERY = "in:premium-journals after:2026-01-08 before:2026-01-10"
ROUTE = {
    "start": "2026-01-09",
    "end": "2026-01-09",
    "query": QUERY,
    "expected_canonical_path": f"raw/channel_segments_v2_5/{FILENAME}",
}
TARGET_SHA256 = (
    "399f0df8ef52878442542043c3d64c0a4cb8070bac5dd0b8df58fcebf2df87ae"
)
SIDECAR_SHA256 = (
    "0dc3951fca360c49c506174cad220b6e6e9b26b3259e86bca2df03a02f5844e1"
)
SCHEDULE_SHA256 = (
    "64ab77a9520dbc80d072d3b51347169c825eb60eba4c6a6b6bc363b37647901a"
)
PROTECTED_TREE_SHA256 = (
    "ba59f65424487d24366265a14aeeefd3a209a7931895fbf3defdee2cf951099b"
)
FULL_STAGE_TREE_SHA256 = (
    "486fd41ceb28c5a047705775fa927d9df5a14ade8dbd6e8c29d349c11b619dfa"
)

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
    paths = [
        stage / FILENAME,
        *sorted(
            path
            for path in (stage / "forum_group_navigation_checkpoints").rglob("*")
            if path.is_file()
        ),
    ]
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
bindings = {
    "canonical": (TARGET, TARGET_SHA256, 1_786_921),
    "source": (SOURCE, TARGET_SHA256, 1_786_921),
    "sidecar": (SIDECAR, SIDECAR_SHA256, 3_044),
    "authorization": (
        AUTHORIZATION,
        "d477cb0f496045afe22c7795db3c42bb37ebf1af0f66f4706514c13a521097e4",
        4_940,
    ),
    "promotion_receipt": (
        PROMOTION_RECEIPT,
        "b8099887fc985c1e3b107d82a1f8dff144adacefb5406a1b05988a1ead787906",
        5_848,
    ),
    "independent_audit": (
        INDEPENDENT_AUDIT,
        "967a0164b5d73e5ac2e48d3c6aed92f458ff427976936658a6430cf5c9c86029",
        16_503,
    ),
    "schedule_rebuild_authorization": (
        SCHEDULE_AUTHORIZATION,
        "74fdeb0898fe1704cd8f271dcb73cd4b17e76a86a2ddc39a1dfb8d5aaeb05563",
        2_945,
    ),
    "schedule": (SCHEDULE, SCHEDULE_SHA256, 975_585),
    "protected_contract": (
        CORPUS_ROOT / "premium_journals_provenance_contract.py",
        "609285b8ea8a87cc4a8dc86595936b9906b635b5a5e88b37f284161d42003602",
        78_730,
    ),
    "system_event_contract": (
        CORPUS_ROOT / "premium_journals_system_event_timestamp_v1.py",
        "c20711a6b5957274d32349e4fa16bf9017bd9c2811f3b902c9988ec909dd323b",
        55_857,
    ),
}
for name, (path, expected_sha, expected_bytes) in bindings.items():
    if not path.is_file():
        errors.append(f"binding_missing:{name}")
    elif sha256_file(path) != expected_sha or path.stat().st_size != expected_bytes:
        errors.append(f"binding_mismatch:{name}")
if TARGET.is_file() and SOURCE.is_file() and TARGET.read_bytes() != SOURCE.read_bytes():
    errors.append("source_target_not_byte_equal")

protected = protected_manifest(STAGE)
full_stage = tree_manifest(STAGE)
if protected != {
    "file_count": 76,
    "total_bytes": 1_681_238,
    "tree_manifest_sha256": PROTECTED_TREE_SHA256,
}:
    errors.append("protected_original_v2_6_tree_mismatch")
if full_stage != {
    "file_count": 81,
    "total_bytes": 3_496_110,
    "tree_manifest_sha256": FULL_STAGE_TREE_SHA256,
}:
    errors.append("full_append_only_stage_tree_mismatch")

try:
    strict = premium.audit_premium_canonical(
        TARGET,
        ROUTE,
        artifact_root=CORPUS_ROOT,
    )
except Exception as exc:
    strict = None
    errors.append(f"strict_audit_exception:{type(exc).__name__}:{exc}")
accepted: dict[str, Any] = strict["accepted_artifact"] if strict else {}
if strict is not None:
    if not (
        strict.get("terminal_valid") is True
        and strict.get("unresolved_count") == 0
        and strict.get("conflict_count") == 0
        and accepted.get("reported_total") == 194
        and accepted.get("reported_pages") == 8
        and accepted.get("forum_group_count") == 67
        and accepted.get("observed_child_thread_count") == 24
        and accepted.get("source_file_set_sha256")
        == "86848277172b7731201a25606363407b368daac1f7b35ba86701c4525e5f2e3c"
        and len(accepted.get("source_files") or []) == 80
    ):
        errors.append("strict_audit_counts_or_bindings_mismatch")
    for section in (
        "forum_membership_integrity",
        "forum_navigation_artifact_integrity",
        "timestamp_scope_integrity",
        "reply_provenance_integrity",
        "attachment_provenance_integrity",
    ):
        if (accepted.get(section) or {}).get("passed") is not True:
            errors.append(f"strict_section_failed:{section}")
    if (accepted.get("timestamp_scope_integrity") or {}).get("mode_counts") != {
        system_event.FALLBACK_SOURCE + "_sidecar_revalidated": 1,
        "message_timestamp_aria_exact": 193,
    }:
        errors.append("timestamp_mode_counts_mismatch")

generic_issues: dict[str, list[dict[str, Any]]] = {}
generic = validate_corpus.validate_one_segment(
    TARGET,
    guild_id="1167376964680691732",
    window_start=dt.date(2026, 1, 9),
    window_end=dt.date(2026, 1, 9),
    cutoff_utc=dt.datetime(2026, 7, 20, 23, 59, 59, tzinfo=dt.timezone.utc),
    issues=generic_issues,
)
if generic is None or generic_issues:
    errors.append("generic_segment_validation_failed")

schedule = json.loads(SCHEDULE.read_text(encoding="utf-8"))
routes = schedule.get("routes", {}).get("premium_journals", [])
route_by_start = {
    str(route.get("start")): route for route in routes if isinstance(route, dict)
}
directory_errors = premium.validate_authoritative_directory(CORPUS_ROOT, routes)
if directory_errors:
    errors.extend(f"authoritative_directory:{item}" for item in directory_errors)

all_audits = []
for path in sorted((CORPUS_ROOT / "raw/channel_segments_v2_5").glob("*.json")):
    payload = json.loads(path.read_text(encoding="utf-8"))
    start = str((payload.get("segment") or {}).get("start") or "")
    route = route_by_start.get(start)
    if route is None:
        errors.append(f"route_missing_for_canonical:{path.name}")
        continue
    try:
        all_audits.append(
            premium.audit_premium_canonical(
                path,
                route,
                artifact_root=CORPUS_ROOT,
            )
        )
    except Exception as exc:
        errors.append(f"union_audit_failed:{path.name}:{type(exc).__name__}:{exc}")
reconciliation = json.loads(RECONCILIATION.read_text(encoding="utf-8"))
try:
    summary = premium.derive_premium_summary(routes, all_audits, reconciliation)
except Exception as exc:
    summary = {}
    errors.append(f"union_summary_failed:{type(exc).__name__}:{exc}")
census = summary.get("premium_thread_census") or {}
union = census.get("full_window_union_terminal_evidence") or {}
union_gates = {
    "accepted_route_count": summary.get("accepted_route_count") == 9,
    "pending_route_count": summary.get("pending_route_count") == 192,
    "accepted_reported_total": summary.get("accepted_reported_total") == 2427,
    "unique_message_id_count": union.get("unique_message_id_count") == 2427,
    "duplicate_count": union.get("cross_route_duplicate_message_id_count") == 0,
    "unresolved_count": union.get("unresolved_occurrence_count") == 0,
    "conflict_count": union.get("conflict_occurrence_count") == 0,
    "attachment_owner_conflict_count": union.get(
        "cross_route_attachment_owner_conflict_count"
    )
    == 0,
    "terminal_route_count": (union.get("terminal_state_counts") or {}).get(
        "stable_bottom"
    )
    == 9,
}
errors.extend(f"union_gate_failed:{name}" for name, passed in union_gates.items() if not passed)

jan9_schedule_route = route_by_start.get("2026-01-09") or {}
parent = next(
    (
        value
        for value in schedule.get("parents", [])
        if isinstance(value, dict)
        and (
            value.get("name") == "premium-journals"
            or value.get("logical_name") == "premium_journals"
        )
    ),
    {},
)
schedule_gates = {
    "jan9_status": jan9_schedule_route.get("status") == "complete_accepted_v2_6",
    "jan9_sha": (jan9_schedule_route.get("accepted_artifact") or {}).get("sha256")
    == TARGET_SHA256,
    "jan9_total": (jan9_schedule_route.get("accepted_artifact") or {}).get(
        "reported_total"
    )
    == 194,
    "parent_accepted": parent.get("accepted_route_count") == 9,
    "parent_pending": parent.get("pending_route_count") == 192,
    "parent_total": parent.get("accepted_reported_total") == 2427,
}
errors.extend(
    f"schedule_gate_failed:{name}" for name, passed in schedule_gates.items() if not passed
)

guardrails = {
    "canonical_partial_absent": not TARGET.with_suffix(".partial.json").exists(),
    "legacy_absent": not LEGACY.exists(),
    "legacy_partial_absent": not LEGACY.with_suffix(".partial.json").exists(),
    "v2_7_absent": not V27.exists(),
    "target_adjacent_sidecar_absent": not system_event.sidecar_path(TARGET).exists(),
    "external_sidecar_exact": sha256_file(SIDECAR) == SIDECAR_SHA256,
    "protected_contract_exact": sha256_file(
        CORPUS_ROOT / "premium_journals_provenance_contract.py"
    )
    == "609285b8ea8a87cc4a8dc86595936b9906b635b5a5e88b37f284161d42003602",
    "v2_7_involved": False,
}
errors.extend(
    f"guardrail_failed:{name}"
    for name, passed in guardrails.items()
    if passed is not True and name != "v2_7_involved"
)
if guardrails["v2_7_involved"] is not False:
    errors.append("guardrail_failed:v2_7_involved")

result = {
    "status": "PASS" if not errors else "FAIL",
    "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    ),
    "canonical": {
        "path": TARGET.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_file(TARGET),
        "bytes": TARGET.stat().st_size,
        "source_target_byte_equal": TARGET.read_bytes() == SOURCE.read_bytes(),
    },
    "external_canonical_binding_sidecar": {
        "path": SIDECAR.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_file(SIDECAR),
        "bytes": SIDECAR.stat().st_size,
    },
    "schedule": {
        "path": SCHEDULE.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_file(SCHEDULE),
        "bytes": SCHEDULE.stat().st_size,
        "gates": schedule_gates,
    },
    "strict": {
        "completed": strict is not None,
        "terminal_valid": strict.get("terminal_valid") if strict else None,
        "unresolved_count": strict.get("unresolved_count") if strict else None,
        "conflict_count": strict.get("conflict_count") if strict else None,
        "reported_total": accepted.get("reported_total"),
        "reported_pages": accepted.get("reported_pages"),
        "forum_group_count": accepted.get("forum_group_count"),
        "observed_child_thread_count": accepted.get("observed_child_thread_count"),
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
        "artifact_returned": generic is not None,
        "issue_count": sum(len(rows) for rows in generic_issues.values()),
        "issues": generic_issues,
    },
    "union": {
        "audited_route_count": len(all_audits),
        "gates": union_gates,
        "accepted_route_count": summary.get("accepted_route_count"),
        "pending_route_count": summary.get("pending_route_count"),
        "accepted_reported_total": summary.get("accepted_reported_total"),
        "unique_message_id_count": union.get("unique_message_id_count"),
        "cross_route_duplicate_message_id_count": union.get(
            "cross_route_duplicate_message_id_count"
        ),
        "unresolved_occurrence_count": union.get("unresolved_occurrence_count"),
        "conflict_occurrence_count": union.get("conflict_occurrence_count"),
        "cross_route_attachment_owner_conflict_count": union.get(
            "cross_route_attachment_owner_conflict_count"
        ),
        "message_id_set_sha256": union.get("message_id_set_sha256"),
        "accepted_route_binding_set_sha256": union.get(
            "accepted_route_binding_set_sha256"
        ),
        "observed_message_bearing_child_thread_count": census.get(
            "observed_message_bearing_child_thread_count"
        ),
    },
    "authoritative_directory_errors": directory_errors,
    "protected_original_v2_6_tree": protected,
    "full_append_only_stage_tree": full_stage,
    "guardrails": guardrails,
    "errors": errors,
}
rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
(AUDIT_ROOT / "postpromotion_audit.json").write_text(rendered, encoding="utf-8")
print(rendered, end="")
raise SystemExit(0 if not errors else 1)
