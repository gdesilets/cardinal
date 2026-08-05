"""Explicit, disabled-only schedule support for the future Premium v2.7 pilot."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import premium_journals_provenance_contract_v2_7 as contract


def exact_query(day: str) -> str:
    value = date.fromisoformat(day)
    return f"in:premium-journals after:{value - timedelta(days=1)} before:{value + timedelta(days=1)}"


def build_disabled_route(day: str = contract.PILOT_START) -> dict[str, Any]:
    """Return a route that cannot collect until a separate enablement change."""
    return {
        "route_id": f"premium-journals-v2-7:{day}:{day}", "channel_id": contract.v26.PREMIUM_ID,
        "channel_name": contract.v26.PREMIUM_NAME, "channel_kind": "forum channel",
        "start": day, "end": day, "query": exact_query(day), "query_prefix": "in:premium-journals",
        "collector_version": contract.COLLECTOR_VERSION, "provenance_version": contract.PROVENANCE_VERSION,
        "v2_7_explicit_opt_in": True, "live_collection_enabled": False,
        "expected_canonical_path": contract.expected_canonical_relative_path(day, day),
        "expected_checkpoint_directory": contract.expected_checkpoint_relative_directory(day),
        "resolution_policy": "direct_consensus_then_v2_6_header_fallback",
        "page_acceptance": "all_groups_checkpointed_before_page_acceptance",
    }


def validate_route(route: dict[str, Any]) -> list[str]:
    errors = contract.validate_explicit_v2_7_route(route)
    day = str(route.get("start") or "")
    if route.get("route_id") != f"premium-journals-v2-7:{day}:{day}": errors.append("v2_7_route_id_mismatch")
    if route.get("channel_id") != contract.v26.PREMIUM_ID or route.get("channel_name") != contract.v26.PREMIUM_NAME or route.get("channel_kind") != "forum channel": errors.append("v2_7_parent_scope_mismatch")
    try:
        expected_query = exact_query(day)
    except (TypeError, ValueError):
        expected_query = None
        errors.append("v2_7_route_date_invalid")
    if route.get("query") != expected_query: errors.append("v2_7_exact_query_mismatch")
    if route.get("expected_checkpoint_directory") != contract.expected_checkpoint_relative_directory(day): errors.append("v2_7_checkpoint_directory_mismatch")
    if route.get("resolution_policy") != "direct_consensus_then_v2_6_header_fallback": errors.append("v2_7_resolution_policy_mismatch")
    if route.get("page_acceptance") != "all_groups_checkpointed_before_page_acceptance": errors.append("v2_7_page_acceptance_mismatch")
    return sorted(set(errors))


def validate_explicit_routes(schedule: dict[str, Any]) -> list[str]:
    """v2.7 routes live under their own key and cannot be inferred from v2.6."""
    raw = schedule.get("premium_journals_v2_7_routes", [])
    if raw is None: return []
    if not isinstance(raw, list): return ["v2_7_routes_not_list"]
    errors: list[str] = []
    seen: set[str] = set()
    for index, route in enumerate(raw):
        if not isinstance(route, dict): errors.append(f"v2_7_route_{index}_not_object"); continue
        errors.extend(f"v2_7_route_{index}:{item}" for item in validate_route(route))
        key = str(route.get("route_id") or "")
        if key in seen: errors.append(f"v2_7_route_{index}:duplicate_route_id")
        seen.add(key)
    return sorted(set(errors))
