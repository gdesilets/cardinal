from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


AUDIT_ROOT = Path(__file__).resolve().parent
CORPUS_ROOT = AUDIT_ROOT.parents[1]
PROJECT_ROOT = CORPUS_ROOT.parent
MIRROR_ROOT = PROJECT_ROOT / (
    "j9r"
)
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
TARGET_PARTIAL = TARGET.with_suffix(".partial.json")
LEGACY = CORPUS_ROOT / "raw/channel_segments" / FILENAME
LEGACY_PARTIAL = LEGACY.with_suffix(".partial.json")
V27 = CORPUS_ROOT / "raw/channel_segments_v2_7" / FILENAME
MIRROR_CANONICAL = MIRROR_ROOT / "raw/channel_segments_v2_5" / FILENAME
SCHEDULE = CORPUS_ROOT / "working/scoped_three_parent_collection_schedule.json"
AUTHORIZATION = AUDIT_ROOT / "promotion_authorization.json"
INDEPENDENT_AUDIT = AUDIT_ROOT / "independent_audit.json"
RECEIPT = AUDIT_ROOT / "promotion_receipt.json"
QUERY = "in:premium-journals after:2026-01-08 before:2026-01-10"
ROUTE = {
    "start": "2026-01-09",
    "end": "2026-01-09",
    "query": QUERY,
    "expected_canonical_path": f"raw/channel_segments_v2_5/{FILENAME}",
}
SOURCE_SHA256 = (
    "399f0df8ef52878442542043c3d64c0a4cb8070bac5dd0b8df58fcebf2df87ae"
)
SOURCE_BYTES = 1_786_921
SIDECAR_SHA256 = (
    "0dc3951fca360c49c506174cad220b6e6e9b26b3259e86bca2df03a02f5844e1"
)
AUTHORIZATION_SHA256 = (
    "d477cb0f496045afe22c7795db3c42bb37ebf1af0f66f4706514c13a521097e4"
)
INDEPENDENT_AUDIT_SHA256 = (
    "967a0164b5d73e5ac2e48d3c6aed92f458ff427976936658a6430cf5c9c86029"
)
SCHEDULE_SHA256 = (
    "0a1fd787f0fbeb6cb142edd028d16daa3c0189027d2d42b82ba40bb209ca18d6"
)
PROTECTED_CONTRACT_SHA256 = (
    "609285b8ea8a87cc4a8dc86595936b9906b635b5a5e88b37f284161d42003602"
)
SYSTEM_CONTRACT_SHA256 = (
    "c20711a6b5957274d32349e4fa16bf9017bd9c2811f3b902c9988ec909dd323b"
)
PROTECTED_TREE_SHA256 = (
    "ba59f65424487d24366265a14aeeefd3a209a7931895fbf3defdee2cf951099b"
)
FULL_STAGE_TREE_SHA256 = (
    "486fd41ceb28c5a047705775fa927d9df5a14ade8dbd6e8c29d349c11b619dfa"
)
REPORT_BINDINGS = {
    "prepromotion_revalidated_validation.json": (
        "9ee53ac42b116c65546478d2633ce67e6f3166253f3f7591859edaca861a3753",
        6_740,
    ),
    "prepromotion_revalidated_generic_qa_gate.json": (
        "9f6d443bc92a546c15c73364335d7f8678d2be9c0e7781eb0d071ce592885d76",
        1_707,
    ),
    "prepromotion_prospective_union.json": (
        "1033afcd33d2a6abf9a8be4e9923750613ca270defc4fd96f040e2aae9a28dc6",
        1_690,
    ),
    "promotion_plan_no_write.json": (
        "8970d2ed1ccf217c9c888254ec36a1ee54106c3b0bd933728a08045929a857d6",
        4_412,
    ),
}

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


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_top_level_not_object:{path.name}")
    return value


def require_binding(path: Path, expected_sha: str, expected_bytes: int) -> None:
    if not path.is_file():
        raise RuntimeError(f"required_file_missing:{path}")
    if sha256_file(path) != expected_sha or path.stat().st_size != expected_bytes:
        raise RuntimeError(f"required_file_binding_mismatch:{path}")


def write_exclusive_json(path: Path, value: Any) -> None:
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> None:
    if RECEIPT.exists():
        raise RuntimeError("append_only_promotion_receipt_exists")
    for path in (TARGET, TARGET_PARTIAL, LEGACY, LEGACY_PARTIAL, V27):
        if path.exists():
            raise RuntimeError(f"exclusive_target_or_forbidden_path_exists:{path}")

    require_binding(AUTHORIZATION, AUTHORIZATION_SHA256, 4_940)
    authorization = read_json(AUTHORIZATION)
    if not (
        authorization.get("status") == "AUTHORIZED"
        and authorization.get("write_executed_at_authorization_time") is False
        and authorization.get("schedule_mutated_at_authorization_time") is False
        and authorization.get("v2_7_involved") is False
        and (authorization.get("independent_gate") or {}).get("verdict") == "PASS"
        and (authorization.get("independent_gate") or {}).get(
            "promotion_gate_passed"
        )
        is True
    ):
        raise RuntimeError("promotion_authorization_semantics_invalid")
    require_binding(INDEPENDENT_AUDIT, INDEPENDENT_AUDIT_SHA256, 16_503)
    independent = read_json(INDEPENDENT_AUDIT)
    verdict = independent.get("verdict") or {}
    if not (
        verdict.get("status") == "PASS"
        and verdict.get("blocker_count") == 0
        and verdict.get("promotion_gate_passed") is True
        and verdict.get("authority_mutated_by_audit") is False
        and verdict.get("schedule_mutated_by_audit") is False
        and verdict.get("canonical_target_created_by_audit") is False
    ):
        raise RuntimeError("independent_audit_verdict_invalid")
    for name, (expected_sha, expected_bytes) in REPORT_BINDINGS.items():
        path = AUDIT_ROOT / name
        require_binding(path, expected_sha, expected_bytes)
        if name.endswith(".json") and name != "promotion_plan_no_write.json":
            payload = read_json(path)
            if payload.get("status") != "PASS":
                raise RuntimeError(f"frozen_gate_not_pass:{name}")

    require_binding(SOURCE, SOURCE_SHA256, SOURCE_BYTES)
    require_binding(SIDECAR, SIDECAR_SHA256, 3_044)
    if SOURCE.read_bytes() != MIRROR_CANONICAL.read_bytes():
        raise RuntimeError("source_mirror_canonical_not_byte_equal")
    require_binding(SCHEDULE, SCHEDULE_SHA256, 930_837)
    require_binding(
        CORPUS_ROOT / "premium_journals_provenance_contract.py",
        PROTECTED_CONTRACT_SHA256,
        78_730,
    )
    require_binding(
        CORPUS_ROOT / "premium_journals_system_event_timestamp_v1.py",
        SYSTEM_CONTRACT_SHA256,
        55_857,
    )
    registration = system_event.EXTERNAL_SIDECAR_REGISTRATIONS_V1.get(
        f"raw/channel_segments_v2_5/{FILENAME}"
    )
    if registration != {
        "source_artifact_sha256": SOURCE_SHA256,
        "sidecar_path": SIDECAR.relative_to(CORPUS_ROOT).as_posix(),
        "sidecar_sha256": SIDECAR_SHA256,
    }:
        raise RuntimeError("canonical_external_sidecar_registration_mismatch")

    protected_before = protected_manifest(STAGE)
    stage_before = tree_manifest(STAGE)
    if protected_before != {
        "file_count": 76,
        "total_bytes": 1_681_238,
        "tree_manifest_sha256": PROTECTED_TREE_SHA256,
    }:
        raise RuntimeError("protected_original_v2_6_tree_mismatch")
    if stage_before != {
        "file_count": 81,
        "total_bytes": 3_496_110,
        "tree_manifest_sha256": FULL_STAGE_TREE_SHA256,
    }:
        raise RuntimeError("full_append_only_stage_tree_mismatch")

    prewrite_strict = premium.audit_premium_canonical(
        MIRROR_CANONICAL,
        ROUTE,
        artifact_root=MIRROR_ROOT,
    )
    if not (
        prewrite_strict.get("terminal_valid") is True
        and prewrite_strict.get("unresolved_count") == 0
        and prewrite_strict.get("conflict_count") == 0
    ):
        raise RuntimeError("prewrite_strict_reaudit_failed")

    source_raw = SOURCE.read_bytes()
    created = False
    try:
        with TARGET.open("xb") as handle:
            created = True
            handle.write(source_raw)
            handle.flush()
            os.fsync(handle.fileno())
        if (
            TARGET.stat().st_size != SOURCE_BYTES
            or sha256_file(TARGET) != SOURCE_SHA256
            or TARGET.read_bytes() != source_raw
        ):
            raise RuntimeError("postwrite_target_binding_mismatch")

        strict = premium.audit_premium_canonical(
            TARGET,
            ROUTE,
            artifact_root=CORPUS_ROOT,
        )
        accepted = strict["accepted_artifact"]
        if not (
            strict.get("terminal_valid") is True
            and strict.get("unresolved_count") == 0
            and strict.get("conflict_count") == 0
            and accepted.get("reported_total") == 194
            and accepted.get("reported_pages") == 8
            and accepted.get("forum_group_count") == 67
            and (accepted.get("timestamp_scope_integrity") or {}).get("passed")
            is True
            and (accepted.get("reply_provenance_integrity") or {}).get("passed")
            is True
            and (accepted.get("attachment_provenance_integrity") or {}).get(
                "passed"
            )
            is True
        ):
            raise RuntimeError("immediate_postwrite_strict_audit_failed")

        generic_issues: dict[str, list[dict[str, Any]]] = {}
        generic = validate_corpus.validate_one_segment(
            TARGET,
            guild_id="1167376964680691732",
            window_start=dt.date(2026, 1, 9),
            window_end=dt.date(2026, 1, 9),
            cutoff_utc=dt.datetime(
                2026, 7, 20, 23, 59, 59, tzinfo=dt.timezone.utc
            ),
            issues=generic_issues,
        )
        if generic is None or generic_issues:
            raise RuntimeError("immediate_postwrite_generic_segment_audit_failed")
        if any(
            path.exists()
            for path in (TARGET_PARTIAL, LEGACY, LEGACY_PARTIAL, V27)
        ):
            raise RuntimeError("forbidden_path_created_during_promotion")
        if sha256_file(SCHEDULE) != SCHEDULE_SHA256:
            raise RuntimeError("schedule_changed_during_promotion")
        protected_after = protected_manifest(STAGE)
        stage_after = tree_manifest(STAGE)
        if protected_after != protected_before or stage_after != stage_before:
            raise RuntimeError("immutable_stage_changed_during_promotion")

        promoted_at = dt.datetime.now(dt.timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        receipt = {
            "schema_version": "1.0.0",
            "artifact_type": "premium_journals_jan9_exclusive_promotion_receipt",
            "immutable": True,
            "append_only": True,
            "status": "PASS",
            "promoted_at_utc": promoted_at,
            "authorization": {
                "path": AUTHORIZATION.relative_to(CORPUS_ROOT).as_posix(),
                "sha256": AUTHORIZATION_SHA256,
                "bytes": AUTHORIZATION.stat().st_size,
            },
            "independent_audit": {
                "path": INDEPENDENT_AUDIT.relative_to(CORPUS_ROOT).as_posix(),
                "sha256": INDEPENDENT_AUDIT_SHA256,
                "bytes": INDEPENDENT_AUDIT.stat().st_size,
                "verdict": "PASS",
            },
            "source": {
                "path": SOURCE.relative_to(CORPUS_ROOT).as_posix(),
                "sha256": SOURCE_SHA256,
                "bytes": SOURCE_BYTES,
            },
            "target": {
                "path": TARGET.relative_to(CORPUS_ROOT).as_posix(),
                "sha256": sha256_file(TARGET),
                "bytes": TARGET.stat().st_size,
                "exclusive_create": True,
                "source_target_byte_equal": TARGET.read_bytes() == source_raw,
            },
            "external_canonical_binding_sidecar": {
                "path": SIDECAR.relative_to(CORPUS_ROOT).as_posix(),
                "sha256": sha256_file(SIDECAR),
                "bytes": SIDECAR.stat().st_size,
                "inside_authoritative_directory": False,
            },
            "immediate_postwrite_audit": {
                "strict_premium_passed": True,
                "generic_segment_issue_count": 0,
                "reported_total": accepted.get("reported_total"),
                "reported_pages": accepted.get("reported_pages"),
                "forum_group_count": accepted.get("forum_group_count"),
                "observed_child_thread_count": accepted.get(
                    "observed_child_thread_count"
                ),
                "timestamp_scope_integrity": accepted.get(
                    "timestamp_scope_integrity"
                ),
                "reply_provenance_integrity": accepted.get(
                    "reply_provenance_integrity"
                ),
                "attachment_provenance_integrity": accepted.get(
                    "attachment_provenance_integrity"
                ),
            },
            "immutable_stage": {
                "protected_before": protected_before,
                "protected_after": protected_after,
                "full_stage_before": stage_before,
                "full_stage_after": stage_after,
                "unchanged": True,
            },
            "schedule": {
                "path": SCHEDULE.relative_to(CORPUS_ROOT).as_posix(),
                "sha256": sha256_file(SCHEDULE),
                "bytes": SCHEDULE.stat().st_size,
                "mutated_by_promotion": False,
            },
            "guardrails": {
                "canonical_partial_absent": not TARGET_PARTIAL.exists(),
                "legacy_canonical_absent": not LEGACY.exists(),
                "legacy_partial_absent": not LEGACY_PARTIAL.exists(),
                "v2_7_canonical_absent": not V27.exists(),
                "v2_7_involved": False,
            },
            "postpromotion_schedule_rebuild_pending": True,
        }
        write_exclusive_json(RECEIPT, receipt)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "target": receipt["target"],
                    "receipt": {
                        "path": RECEIPT.relative_to(CORPUS_ROOT).as_posix(),
                        "sha256": sha256_file(RECEIPT),
                        "bytes": RECEIPT.stat().st_size,
                    },
                    "schedule_unchanged": True,
                    "postpromotion_schedule_rebuild_pending": True,
                },
                indent=2,
            )
        )
    except Exception:
        if created and TARGET.is_file():
            TARGET.unlink()
        raise


if __name__ == "__main__":
    main()
