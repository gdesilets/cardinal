"""Non-promoting v2.7 pilot gate and no-double-authority guard."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import premium_journals_provenance_contract as v26
import premium_journals_provenance_contract_v2_7 as v27
import premium_journals_v2_7_schedule as schedule
from qa.validate_premium_journals_v2_7 import validate_one_segment


def no_double_authority_errors(
    root: Path,
    v2_7_route: dict[str, Any],
    *,
    active_v2_6_routes: Sequence[dict[str, Any]] | None = None,
) -> list[str]:
    """A v2.7 release route cannot coexist with v2.6 authority for that day."""
    errors: list[str] = []
    day = str(v2_7_route.get("start") or "")
    if active_v2_6_routes is None:
        errors.append("active_v2_6_schedule_not_supplied")
        active_v2_6_routes = ()
    overlapping = [route for route in active_v2_6_routes
        if str(route.get("start") or "") <= day <= str(route.get("end") or "")]
    if overlapping:
        errors.append("v2_7_day_still_scheduled_under_v2_6_authority")
    old_path = root / v26.expected_canonical_relative_path(day, day)
    new_path = root / v27.expected_canonical_relative_path(day, day)
    if old_path.is_file() and new_path.is_file():
        errors.append("v2_6_and_v2_7_canonicals_both_exist_for_day")
    return errors


def audit_shadow_candidate(
    root: Path,
    route: dict[str, Any],
    candidate: Path,
    *,
    active_v2_6_routes: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute route -> canonical QA while keeping the current pilot non-promotable."""
    route_errors = schedule.validate_route(route)
    qa_errors = validate_one_segment(candidate, route, root) if not route_errors else []
    authority_errors = no_double_authority_errors(
        root, route, active_v2_6_routes=active_v2_6_routes
    )
    # Current route grammar requires live_collection_enabled=False.  Promotion
    # therefore remains impossible until a separate, reviewed migration changes
    # both the route grammar and removes overlapping v2.6 authority.
    return {
        "status": "shadow_only_non_promotable",
        "route_valid": not route_errors,
        "canonical_qa_passed": not qa_errors,
        "no_double_authority_passed": not authority_errors,
        "promotion_allowed": False,
        "route_errors": route_errors,
        "qa_errors": qa_errors,
        "authority_errors": authority_errors,
    }
