"""Fail-closed reader harness for the superseded Jan 9 v2.7 draft."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import premium_journals_v2_7_authority_activation_v1 as activation


def _effective_authority_locked(root: Path) -> dict[str, Any]:
    marker = activation.resolve_path(root, activation.COMMIT_MARKER_PATH)
    preimage = activation.resolve_path(root, activation.PREIMAGE_PATH)
    schedule = activation.resolve_path(root, activation.SCHEDULE_PATH)
    if marker.is_file():
        errors = activation.validate_committed_activation(root)
        if not errors:
            try:
                snapshot = activation.load_committed_route_snapshot(root)
            except activation.ActivationError as exc:
                errors = [f"committed_route_snapshot_invalid:{exc}"]
            else:
                return {
                    "status": "PASS",
                    "effective_collection_authority": "premium_journals_v2_7_jan9",
                    "effective_canonical_authority": "none_pending_separate_promotion",
                    "live_collection_enabled": True,
                    "promotion_allowed": False,
                    "commit_marker_valid": True,
                    "fail_closed_to_preimage": False,
                    "effective_route": snapshot["route"],
                    "effective_route_sha256": snapshot["route_sha256"],
                    "effective_schedule_sha256": snapshot["schedule_sha256"],
                    "effective_schedule_bytes": snapshot["schedule_bytes"],
                    "effective_route_source": "premium_journals_v2_7_authoritative_routes[0]",
                    "ordinary_premium_route_array_selectable": False,
                    "recovery_permitted": False,
                    "errors": [],
                }
        fallback_valid = preimage.is_file() and activation.sha256_file(preimage) == activation.PRE_SCHEDULE_SHA256 and preimage.stat().st_size == activation.PRE_SCHEDULE_BYTES
        return {
            "status": "FAIL_CLOSED",
            "effective_collection_authority": "premium_journals_v2_6_preimage" if fallback_valid else "none",
            "effective_canonical_authority": "premium_journals_v2_6_preimage" if fallback_valid else "none",
            "live_collection_enabled": False,
            "promotion_allowed": False,
            "commit_marker_valid": False,
            "fail_closed_to_preimage": fallback_valid,
            "effective_route": None,
            "effective_route_sha256": None,
            "effective_route_source": None,
            "ordinary_premium_route_array_selectable": False,
            "recovery_permitted": False,
            "errors": errors,
        }
    preimage_present = preimage.is_file()
    preimage_valid = preimage_present and activation.sha256_file(preimage) == activation.PRE_SCHEDULE_SHA256 and preimage.stat().st_size == activation.PRE_SCHEDULE_BYTES
    schedule_preimage_exact = schedule.is_file() and activation.sha256_file(schedule) == activation.PRE_SCHEDULE_SHA256 and schedule.stat().st_size == activation.PRE_SCHEDULE_BYTES
    if not preimage_present and schedule_preimage_exact:
        status = "PRE_ACTIVATION"
        fallback_valid = True
        errors: list[str] = []
        recovery_permitted = False
    elif preimage_valid and schedule_preimage_exact:
        status = "PRE_ACTIVATION"
        fallback_valid = True
        errors = []
        recovery_permitted = False
    elif preimage_valid and schedule.is_file():
        recovery_errors = activation.validate_unmarked_projection(root)
        if not recovery_errors:
            status = "FAIL_CLOSED_RECOVERY_REQUIRED"
            errors = ["exact_unmarked_projection_requires_locked_activation_recovery"]
            recovery_permitted = True
        else:
            status = "FAIL_CLOSED"
            errors = ["unmarked_live_schedule_invalid", *recovery_errors]
            recovery_permitted = False
        fallback_valid = True
    else:
        status = "FAIL_CLOSED"
        fallback_valid = False
        errors = ["no_valid_commit_marker_or_exact_pre_activation_schedule"]
        recovery_permitted = False
    return {
        "status": status,
        "effective_collection_authority": "premium_journals_v2_6_preimage" if fallback_valid else "none",
        "effective_canonical_authority": "premium_journals_v2_6_preimage" if fallback_valid else "none",
        "live_collection_enabled": False,
        "promotion_allowed": False,
        "commit_marker_valid": False,
        "fail_closed_to_preimage": preimage_valid and status != "PRE_ACTIVATION",
        "effective_route": None,
        "effective_route_sha256": None,
        "effective_route_source": None,
        "ordinary_premium_route_array_selectable": False,
        "recovery_permitted": recovery_permitted,
        "errors": errors,
    }


def _effective_authority_fixture(root: Path) -> dict[str, Any]:
    root = root.resolve()
    with activation.activation_lock(root):
        return _effective_authority_locked(root)


def effective_authority(root: Path) -> dict[str, Any]:
    """Public Jan9 reader: always non-authoritative, including archived copies."""
    return {
        "status": activation.DRAFT_STATUS,
        "effective_collection_authority": "none_from_this_superseded_harness",
        "effective_canonical_authority": "none_from_this_superseded_harness",
        "live_collection_enabled": False,
        "promotion_allowed": False,
        "commit_marker_valid": False,
        "fail_closed_to_preimage": False,
        "effective_route": None,
        "effective_route_sha256": None,
        "effective_route_source": None,
        "ordinary_premium_route_array_selectable": False,
        "recovery_permitted": False,
        "first_future_activation_target": "2026-01-10",
        "errors": ["Jan9 v2.7 activation is superseded; follow the current v2.6 schedule"],
    }


def _resolve_live_collection_route_fixture(root: Path) -> dict[str, Any]:
    """Return only the marker-authorized v2.7 route; never the retired route."""
    state = _effective_authority_fixture(root)
    if state.get("status") != "PASS" or state.get("live_collection_enabled") is not True:
        raise activation.ActivationError(
            f"no marker-authorized live v2.7 route: {state.get('status')}"
        )
    route = state.get("effective_route")
    if not isinstance(route, dict) or route.get("collection_authority_enabled") is not True:
        raise activation.ActivationError("marker-authorized route payload invalid")
    if route.get("canonical_authority_enabled") is not False or route.get("promotion_allowed") is not False:
        raise activation.ActivationError("live route exceeds collection-only authority")
    return route


def resolve_live_collection_route(root: Path) -> dict[str, Any]:
    raise activation.ActivationError(
        "Jan9 v2.7 activation is superseded for every root; no public live route exists"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    result = effective_authority(root)
    print(json.dumps(result, indent=2))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
