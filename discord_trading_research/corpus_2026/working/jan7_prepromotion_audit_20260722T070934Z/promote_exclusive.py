from __future__ import annotations

import hashlib
import json
import os
import pathlib


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
GATE = AUDIT_DIR / "prepromotion_gate.json"
EXPECTED_SHA256 = "19486bee534ac150e76d70cc2f070ba07735c77ded7846e9ad090c026a81cb72"
EXPECTED_BYTES = 2_919_929
EXPECTED_TREE_SHA256 = "9a1c9ecb843e216cb2b8e11b5fb9cb610601e0f46392e7866e15980447104423"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_manifest(root: pathlib.Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        content = path.read_bytes()
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(content),
                "sha256": sha256_bytes(content),
            }
        )
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "file_count": len(rows),
        "total_bytes": sum(int(row["bytes"]) for row in rows),
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
expected_tree = {
    "file_count": 198,
    "total_bytes": 3_480_038,
    "tree_manifest_sha256": EXPECTED_TREE_SHA256,
}
if stage_tree_before != expected_tree:
    raise RuntimeError(f"stage_tree_prewrite_mismatch:{stage_tree_before!r}")
source_bytes = SOURCE.read_bytes()
if len(source_bytes) != EXPECTED_BYTES or sha256_bytes(source_bytes) != EXPECTED_SHA256:
    raise RuntimeError("source_byte_contract_mismatch")

absence_targets = (
    TARGET,
    TARGET.with_suffix(".partial.json"),
    LEGACY,
    LEGACY.with_suffix(".partial.json"),
)
preexisting = [path for path in absence_targets if path.exists()]
if preexisting:
    raise FileExistsError(f"promotion_target_or_legacy_preexists:{preexisting!r}")

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
if LEGACY.exists() or LEGACY.with_suffix(".partial.json").exists():
    raise RuntimeError("legacy_artifact_created_during_promotion")

receipt = {
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
    },
    "stage_tree_before": stage_tree_before,
    "stage_tree_after": stage_tree_after,
    "legacy_absent": not LEGACY.exists(),
    "legacy_partial_absent": not LEGACY.with_suffix(".partial.json").exists(),
    "canonical_partial_absent": not TARGET.with_suffix(".partial.json").exists(),
    "v2_7_involved": False,
}
rendered = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
(AUDIT_DIR / "promotion_receipt.json").write_text(rendered, encoding="utf-8")
print(rendered, end="")
