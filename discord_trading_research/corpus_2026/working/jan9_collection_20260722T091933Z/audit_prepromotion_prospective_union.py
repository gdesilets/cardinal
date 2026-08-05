from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import sys


AUDIT_ROOT = Path(__file__).resolve().parent
CORPUS_ROOT = AUDIT_ROOT.parents[1]
PROJECT_ROOT = CORPUS_ROOT.parent
MIRROR_ROOT = PROJECT_ROOT / (
    "j9r"
)
SCHEDULE_PATH = CORPUS_ROOT / "working/scoped_three_parent_collection_schedule.json"
RECONCILIATION_PATH = (
    CORPUS_ROOT / "working/premium_journals_scoped_inventory_reconciliation.json"
)
CANONICAL_DIR = CORPUS_ROOT / "raw/channel_segments_v2_5"
FILENAME = (
    "channel_premium_journals_1283941772577472643_"
    "2026-01-09_2026-01-09.json"
)
MIRROR_CANONICAL = MIRROR_ROOT / "raw/channel_segments_v2_5" / FILENAME
REAL_JAN9 = CANONICAL_DIR / FILENAME
EXPECTED_SCHEDULE_SHA256 = (
    "0a1fd787f0fbeb6cb142edd028d16daa3c0189027d2d42b82ba40bb209ca18d6"
)
EXPECTED_CONTRACT_SHA256 = (
    "609285b8ea8a87cc4a8dc86595936b9906b635b5a5e88b37f284161d42003602"
)

sys.path.insert(0, str(CORPUS_ROOT))
import premium_journals_provenance_contract as premium  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


errors: list[str] = []
schedule = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))
routes = schedule.get("routes", {}).get("premium_journals", [])
if len(routes) != 201:
    errors.append("premium_route_count_not_201")
route_by_start = {
    str(route.get("start")): route for route in routes if isinstance(route, dict)
}
existing_paths = sorted(CANONICAL_DIR.glob("*.json"))
if len(existing_paths) != 8:
    errors.append("existing_premium_canonical_count_not_8")
accepted_audits = []
for path in existing_paths:
    payload = json.loads(path.read_text(encoding="utf-8"))
    start = str((payload.get("segment") or {}).get("start") or "")
    route = route_by_start.get(start)
    if route is None:
        errors.append(f"existing_route_missing:{path.name}")
        continue
    try:
        accepted_audits.append(
            premium.audit_premium_canonical(
                path,
                route,
                artifact_root=CORPUS_ROOT,
            )
        )
    except Exception as exc:
        errors.append(f"existing_audit_failed:{path.name}:{type(exc).__name__}:{exc}")

jan9_route = route_by_start.get("2026-01-09")
if jan9_route is None:
    errors.append("jan9_route_missing")
else:
    try:
        accepted_audits.append(
            premium.audit_premium_canonical(
                MIRROR_CANONICAL,
                jan9_route,
                artifact_root=MIRROR_ROOT,
            )
        )
    except Exception as exc:
        errors.append(f"jan9_audit_failed:{type(exc).__name__}:{exc}")

reconciliation = json.loads(RECONCILIATION_PATH.read_text(encoding="utf-8"))
try:
    prospective = premium.derive_premium_summary(
        routes,
        accepted_audits,
        reconciliation,
    )
except Exception as exc:
    prospective = {}
    errors.append(f"prospective_summary_failed:{type(exc).__name__}:{exc}")

census = prospective.get("premium_thread_census") or {}
union = census.get("full_window_union_terminal_evidence") or {}
expected_values = {
    "accepted_route_count": prospective.get("accepted_route_count") == 9,
    "pending_route_count": prospective.get("pending_route_count") == 192,
    "accepted_reported_total": prospective.get("accepted_reported_total") == 2427,
    "union_accepted_route_count": union.get("accepted_daily_route_count") == 9,
    "union_pending_route_count": union.get("pending_daily_route_count") == 192,
    "union_accepted_reported_total": union.get("accepted_reported_total") == 2427,
    "unique_message_id_count": union.get("unique_message_id_count") == 2427,
    "cross_route_duplicate_message_id_count": union.get(
        "cross_route_duplicate_message_id_count"
    )
    == 0,
    "unresolved_occurrence_count": union.get("unresolved_occurrence_count") == 0,
    "conflict_occurrence_count": union.get("conflict_occurrence_count") == 0,
    "cross_route_attachment_owner_conflict_count": union.get(
        "cross_route_attachment_owner_conflict_count"
    )
    == 0,
    "nine_stable_bottom_routes": (union.get("terminal_state_counts") or {}).get(
        "stable_bottom"
    )
    == 9,
}
errors.extend(name for name, passed in expected_values.items() if not passed)
guardrails = {
    "schedule_unchanged": sha256_file(SCHEDULE_PATH) == EXPECTED_SCHEDULE_SHA256,
    "protected_collector_contract_unchanged": sha256_file(
        CORPUS_ROOT / "premium_journals_provenance_contract.py"
    )
    == EXPECTED_CONTRACT_SHA256,
    "real_jan9_canonical_absent": not REAL_JAN9.exists(),
    "real_jan9_partial_absent": not REAL_JAN9.with_suffix(".partial.json").exists(),
}
errors.extend(name for name, passed in guardrails.items() if not passed)
result = {
    "status": "PASS" if not errors else "FAIL",
    "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    ),
    "audit_scope": "prospective_existing_jan1_through_jan8_plus_isolated_jan9",
    "accepted_audit_count": len(accepted_audits),
    "prospective_counts": {
        "accepted_route_count": prospective.get("accepted_route_count"),
        "pending_route_count": prospective.get("pending_route_count"),
        "accepted_reported_total": prospective.get("accepted_reported_total"),
        "unique_message_id_count": union.get("unique_message_id_count"),
        "cross_route_duplicate_message_id_count": union.get(
            "cross_route_duplicate_message_id_count"
        ),
        "cross_route_duplicate_message_ids": union.get(
            "cross_route_duplicate_message_ids"
        ),
        "unresolved_occurrence_count": union.get("unresolved_occurrence_count"),
        "conflict_occurrence_count": union.get("conflict_occurrence_count"),
        "cross_route_attachment_owner_conflict_count": union.get(
            "cross_route_attachment_owner_conflict_count"
        ),
        "terminal_state_counts": union.get("terminal_state_counts"),
        "message_id_set_sha256": union.get("message_id_set_sha256"),
        "accepted_route_binding_set_sha256": union.get(
            "accepted_route_binding_set_sha256"
        ),
        "observed_message_bearing_child_thread_count": census.get(
            "observed_message_bearing_child_thread_count"
        ),
    },
    "expected_value_gates": expected_values,
    "guardrails": guardrails,
    "v2_7_involved": False,
    "errors": errors,
}
rendered = json.dumps(result, indent=2) + "\n"
(AUDIT_ROOT / "prepromotion_prospective_union.json").write_text(
    rendered, encoding="utf-8"
)
print(rendered, end="")
raise SystemExit(0 if not errors else 1)
