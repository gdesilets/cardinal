"""Lock-free reader for the Jan10 external-marker collection authority."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import premium_journals_v2_7_jan10_authority_activation_v1 as activation


def effective_authority(root: Path | None = None) -> dict[str, Any]:
    state = activation.classify_authority((root or ROOT).resolve())
    return {
        **state,
        "reader_contract": "lock_free_write_free_before_after_snapshot",
        "collection_authority": "v2.7" if state["live_collection_enabled"] else "none",
        "canonical_authority": "none_pending_separate_promotion",
        "jan9_authority": "v2.6_schedule_only",
        "jan9_v2_7_authority": False,
        "jan9_authority_inherited": False,
    }


def resolve_live_collection_route(root: Path | None = None) -> dict[str, Any]:
    return activation.resolve_live_collection_route((root or ROOT).resolve())


def main() -> int:
    result = effective_authority()
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in {
        "READY_FOR_ACTIVATION",
        "FAIL_CLOSED_RECOVERY_REQUIRED",
        "LIVE_COLLECTION_AUTHORIZED_PENDING_TERMINAL_AUDIT",
        "LIVE_COLLECTION_AUTHORIZED",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
