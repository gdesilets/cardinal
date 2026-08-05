"""Read-only Jan 10 v2.7 activation-review package classifier.

Every returned state is non-authoritative.  This reader never takes a lock,
writes an artifact, resolves a live route, submits a query, or activates v2.7.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import premium_journals_v2_7_jan10_activation_review_v1 as review


def effective_authority(root: Path | None = None) -> dict[str, Any]:
    state = review.classify_review_state((root or ROOT).resolve())
    return {
        **state,
        "reader_contract": "write_free_lock_free_before_after_snapshot",
        "effective_authority": "none",
        "live": False,
        "authorized_route": None,
        "jan9_authority": "v2.6_schedule_only",
        "jan9_v2_7_authority": False,
        "jan9_authority_inherited": False,
        "future_activation_chain_required": True,
    }


def resolve_live_collection_route(root: Path | None = None) -> dict[str, Any]:
    raise review.ReviewPackageError(
        "Jan10 review evidence cannot resolve a live collection route"
    )


def main() -> int:
    result = effective_authority()
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in {
        "PRE_ACTIVATION",
        "FAIL_CLOSED_RECOVERY_REQUIRED",
        "REVIEW_PACKAGE_READY",
        "INDEPENDENT_AUDIT_PASSED_NO_AUTHORITY",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
