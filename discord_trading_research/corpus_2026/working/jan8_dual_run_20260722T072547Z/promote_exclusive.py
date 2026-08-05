from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


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
GATE = AUDIT_ROOT / "prepromotion_gate.json"
EXPECTED_SHA256 = "7a9d71adb66ff0317750413c5cb89b459567bd202af3c71a126c4addc134bfb5"
EXPECTED_BYTES = 1_231_302
EXPECTED_TREE = {
    "file_count": 86,
    "total_bytes": 1_471_666,
    "tree_manifest_sha256": "82bc960858880db60f2627656705cf36e58494b027e02620ddc290fdde25ab3e",
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


gate = json.loads(GATE.read_text(encoding="utf-8"))
if gate.get("status") != "PASS" or gate.get("promotion_authorized") is not True:
    raise RuntimeError("prepromotion_gate_did_not_authorize_promotion")
if gate.get("expected_stage_sha256") != EXPECTED_SHA256:
    raise RuntimeError("prepromotion_gate_stage_sha_mismatch")
if gate.get("v2_7_involved") is not False:
    raise RuntimeError("prepromotion_gate_v2_7_involvement_detected")

stage_tree_before = tree_manifest(SOURCE_ROOT)
if stage_tree_before != EXPECTED_TREE:
    raise RuntimeError(f"stage_tree_prewrite_mismatch:{stage_tree_before!r}")
source_bytes = SOURCE.read_bytes()
if len(source_bytes) != EXPECTED_BYTES or sha256_bytes(source_bytes) != EXPECTED_SHA256:
    raise RuntimeError("source_byte_contract_mismatch")

absence_targets = (
    TARGET,
    TARGET.with_suffix(".partial.json"),
    LEGACY,
    LEGACY.with_suffix(".partial.json"),
    V27_TARGET,
    V27_TARGET.with_suffix(".partial.json"),
)
preexisting = [path for path in absence_targets if path.exists()]
if preexisting:
    raise FileExistsError(f"promotion_target_or_guardrail_preexists:{preexisting!r}")

created = False
try:
    with TARGET.open("xb") as handle:
        created = True
        handle.write(source_bytes)
        handle.flush()
        os.fsync(handle.fileno())
    if TARGET.stat().st_size != EXPECTED_BYTES or sha256_file(TARGET) != EXPECTED_SHA256:
        raise RuntimeError("exclusive_target_postwrite_byte_mismatch")
except Exception:
    if created and TARGET.exists():
        TARGET.unlink()
    raise

stage_tree_after = tree_manifest(SOURCE_ROOT)
if stage_tree_after != stage_tree_before:
    raise RuntimeError("source_tree_changed_during_promotion")
if any(path.exists() for path in (LEGACY, LEGACY.with_suffix(".partial.json"), V27_TARGET)):
    raise RuntimeError("non_v2_6_artifact_created_during_promotion")

receipt = {
    "schema_version": "1.0.0",
    "artifact_type": "premium_journals_v2_6_promotion_receipt",
    "status": "PASS",
    "operation": "exclusive_create",
    "write_mode": "xb",
    "prepromotion_gate_sha256": sha256_file(GATE),
    "source": {
        "path": SOURCE.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_file(SOURCE),
        "bytes": SOURCE.stat().st_size,
        "preserved": True,
    },
    "target": {
        "path": TARGET.relative_to(CORPUS_ROOT).as_posix(),
        "sha256": sha256_file(TARGET),
        "bytes": TARGET.stat().st_size,
        "created_exclusively": True,
        "byte_equal_to_source": TARGET.read_bytes() == source_bytes,
    },
    "stage_tree_before": stage_tree_before,
    "stage_tree_after": stage_tree_after,
    "legacy_absent": not LEGACY.exists(),
    "legacy_partial_absent": not LEGACY.with_suffix(".partial.json").exists(),
    "canonical_partial_absent": not TARGET.with_suffix(".partial.json").exists(),
    "v2_7_canonical_absent": not V27_TARGET.exists(),
    "v2_7_involved": False,
}
rendered = json.dumps(receipt, indent=2, ensure_ascii=False) + "\n"
(AUDIT_ROOT / "promotion_receipt.json").write_text(rendered, encoding="utf-8")
print(rendered, end="")
