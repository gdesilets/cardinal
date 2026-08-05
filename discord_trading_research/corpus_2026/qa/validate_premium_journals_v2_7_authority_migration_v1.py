"""Generic fail-closed QA for the disabled Premium v2.7 authority candidate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import premium_journals_v2_7_authority_migration_v1 as migration
import premium_journals_v2_7_schedule as v27_schedule
from qa.validate_premium_journals_v2_7 import validate_one_segment


def _load(path: Path, errors: list[str], label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"{label}_not_json:{type(exc).__name__}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label}_not_object")
        return {}
    return value


def validate_candidate_file(path: Path, artifact_root: Path, *, require_activation_preconditions: bool = False) -> list[str]:
    errors: list[str] = []
    candidate = _load(path, errors, "migration_candidate")
    if errors:
        return sorted(set(errors))
    expected = (artifact_root / migration.CANDIDATE_RELATIVE_PATH).resolve()
    if path.resolve() != expected:
        errors.append("migration_candidate_path_not_exact")
    errors.extend(migration.validate_candidate(candidate, artifact_root, require_activation_preconditions=require_activation_preconditions))
    return sorted(set(errors))


def validate_readiness_file(path: Path, artifact_root: Path) -> list[str]:
    errors: list[str] = []
    report = _load(path, errors, "migration_readiness")
    if errors:
        return sorted(set(errors))
    expected = (artifact_root / migration.MIGRATION_READINESS_RELATIVE_PATH).resolve()
    if path.resolve() != expected:
        errors.append("migration_readiness_path_not_exact")
    errors.extend(migration.validate_readiness_report(report, artifact_root))
    return sorted(set(errors))


def _audit_report_binding_errors(receipt: dict[str, Any], artifact_root: Path) -> list[str]:
    audit = receipt.get("independent_audit") if isinstance(receipt, dict) else None
    binding = audit.get("report") if isinstance(audit, dict) else None
    return migration._report_binding_errors(artifact_root, binding, "activation_audit_report")


def validate_activated_canonical(
    candidate_path: Path,
    canonical_path: Path,
    activated_schedule_path: Path,
    activation_receipt_path: Path,
    artifact_root: Path,
) -> list[str]:
    """Validate receipt -> authority state -> canonical provenance as one chain."""
    errors: list[str] = []
    candidate = _load(candidate_path, errors, "migration_candidate")
    schedule = _load(activated_schedule_path, errors, "activated_schedule")
    receipt = _load(activation_receipt_path, errors, "activation_receipt")
    if errors:
        return sorted(set(errors))
    errors.extend(migration.validate_candidate(candidate, artifact_root, require_activation_preconditions=True))
    errors.extend(migration.validate_activation_receipt(receipt, candidate))
    errors.extend(_audit_report_binding_errors(receipt, artifact_root))
    errors.extend(migration.validate_authority_state(schedule, candidate, "activated"))
    receipts = schedule.get("premium_journals_authority_activation_receipts", [])
    if not isinstance(receipts, list) or receipts.count(receipt) != 1:
        errors.append("activated_schedule_receipt_not_exactly_once")
    route = v27_schedule.build_disabled_route(migration.DAY)
    expected = (artifact_root / route["expected_canonical_path"]).resolve()
    if canonical_path.resolve() != expected:
        errors.append("activated_canonical_path_not_exact_versioned_path")
    if not errors:
        errors.extend(validate_one_segment(canonical_path, route, artifact_root))
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--candidate", type=Path, default=None)
    parser.add_argument("--readiness", type=Path)
    parser.add_argument("--require-activation-preconditions", action="store_true")
    parser.add_argument("--canonical", type=Path)
    parser.add_argument("--activated-schedule", type=Path)
    parser.add_argument("--activation-receipt", type=Path)
    args = parser.parse_args()
    root = args.artifact_root.resolve()
    candidate = (args.candidate or (root / migration.CANDIDATE_RELATIVE_PATH)).resolve()
    activated_inputs = (args.canonical, args.activated_schedule, args.activation_receipt)
    if args.readiness is not None:
        errors = validate_readiness_file(args.readiness.resolve(), root)
        mode = "audit_readiness"
    elif any(value is not None for value in activated_inputs):
        if not all(value is not None for value in activated_inputs):
            errors = ["activated_mode_requires_canonical_schedule_and_receipt"]
        else:
            errors = validate_activated_canonical(candidate, args.canonical.resolve(), args.activated_schedule.resolve(), args.activation_receipt.resolve(), root)
        mode = "activated_canonical"
    else:
        errors = validate_candidate_file(candidate, root, require_activation_preconditions=args.require_activation_preconditions)
        mode = "disabled_candidate"
    print(json.dumps({"status": "PASS" if not errors else "FAIL", "mode": mode, "errors": errors}, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
