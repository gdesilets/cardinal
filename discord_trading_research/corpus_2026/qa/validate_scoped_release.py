#!/usr/bin/env python3
"""Independent, read-only QA for the authorized three-channel Discord release."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path, PurePosixPath
from typing import Any


QA_DIR = Path(__file__).resolve().parent
ROOT = QA_DIR.parent
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import authorized_collection_scope
import reply_provenance_contract
import timestamp_scope_revalidation
import build_cardinal_database_v2
import validate_corpus


EXPECTED_GUILD_ID = "1167376964680691732"
EXPECTED_START = "2026-01-01"
EXPECTED_END = "2026-07-20"
EXPECTED_END_EXCLUSIVE_UTC = "2026-07-21T05:00:00Z"
EXPECTED_PARENT_IDS = frozenset(
    authorized_collection_scope.REQUIRED_AUTHORIZED_CONTAINERS
)
PREMIUM_PARENT_ID = "1283941772577472643"
PREMIUM_AUTHORITATIVE_DIRECTORY = "raw/channel_segments_v2_5"
PREMIUM_LEGACY_DIRECTORY = "raw/channel_segments"
PREMIUM_COLLECTOR_VERSION = "2.6"
PREMIUM_REQUIRED_DAILY_SEGMENTS = 201


class ScopedQaError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ScopedQaError(f"Could not read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ScopedQaError(f"{label} must contain a JSON object: {path}")
    return value


def parse_utc(value: Any) -> dt.datetime:
    text = str(value or "").strip()
    if not text:
        raise ScopedQaError("A required UTC timestamp is missing")
    try:
        parsed = dt.datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text
        )
    except ValueError as exc:
        raise ScopedQaError(f"Invalid UTC timestamp: {text!r}") from exc
    if parsed.tzinfo is None:
        raise ScopedQaError(f"UTC timestamp has no offset: {text!r}")
    return parsed.astimezone(dt.timezone.utc)


def portable_relative(value: Any) -> bool:
    text = str(value or "").replace("\\", "/").strip()
    if not text or re.match(r"^[A-Za-z]:", text):
        return False
    path = PurePosixPath(text)
    return not path.is_absolute() and all(
        part not in {"", ".", ".."} and ":" not in part for part in path.parts
    )


def message_text(row: dict[str, Any]) -> str:
    for key in ("content_text", "content"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def premium_release_contract_errors(
    manifest: dict[str, Any], authorized: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    path_policy = authorized.get("canonical_path_policy")
    path_policy = path_policy if isinstance(path_policy, dict) else {}
    required_path_values = {
        "gate": "premium_journals_authoritative_v2_5_source_integrity",
        "passed": True,
        "standard_authoritative_directory": PREMIUM_LEGACY_DIRECTORY,
        "premium_authoritative_directory": PREMIUM_AUTHORITATIVE_DIRECTORY,
        "premium_legacy_preservation_directory": PREMIUM_LEGACY_DIRECTORY,
        "premium_legacy_directory_policy": "preservation_only_not_authoritative",
        "premium_collector_version_required": PREMIUM_COLLECTOR_VERSION,
        "required_roots_supplied_exactly_once": True,
        "legacy_premium_authoritative_occurrence_count": 0,
        "premium_collector_version_mismatch_count": 0,
        "premium_collector_version_mismatch_paths": [],
        "premium_provenance_missing_segment_count": 0,
        "premium_provenance_missing_segments": [],
        "invalid_premium_authoritative_file_count": 0,
        "invalid_premium_authoritative_paths": [],
        "accepted_premium_segment_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
        "accepted_premium_daily_date_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
        "duplicate_premium_daily_dates": [],
    }
    for key, expected in required_path_values.items():
        if path_policy.get(key) != expected:
            errors.append(f"premium_authoritative_source_integrity_{key}_mismatch")
    if (
        type(path_policy.get("accepted_premium_bound_source_file_count")) is not int
        or path_policy.get("accepted_premium_bound_source_file_count")
        < PREMIUM_REQUIRED_DAILY_SEGMENTS
    ):
        errors.append("premium_authoritative_source_file_coverage_incomplete")
    for key in (
        "accepted_premium_source_file_set_sha256",
        "accepted_premium_message_id_set_sha256",
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", str(path_policy.get(key) or "")):
            errors.append(f"premium_authoritative_source_integrity_{key}_missing")
    release_path_gates = [
        row
        for row in manifest.get("release_gates") or []
        if isinstance(row, dict)
        and row.get("gate")
        == "premium_journals_authoritative_v2_5_source_integrity"
    ]
    if len(release_path_gates) != 1 or release_path_gates[0] != path_policy:
        errors.append("premium_authoritative_source_integrity_gate_unbound")

    reconciliation = authorized.get("child_inventory_reconciliation")
    reconciliation = reconciliation if isinstance(reconciliation, dict) else {}
    if any(
        reconciliation.get(key) is not False
        for key in ("inventory_complete", "enumeration_complete", "closure_proven")
    ):
        errors.append("premium_inventory_census_must_remain_non_closed")
    closure = reconciliation.get("message_scope_closure")
    closure = closure if isinstance(closure, dict) else {}
    required_closure_values = {
        "gate": "premium_journals_message_data_scope_closure",
        "passed": True,
        "closure_proven": True,
        "status": "complete",
        "required_parent_container_id": PREMIUM_PARENT_ID,
        "required_calendar_day_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
        "complete_calendar_day_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
        "parent_segment_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
        "required_exact_daily_parent_segment_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
        "invalid_daily_partition_segment_count": 0,
        "duplicate_daily_date_count": 0,
    }
    for key, expected in required_closure_values.items():
        if closure.get(key) != expected:
            errors.append(f"premium_message_scope_closure_{key}_mismatch")
    if closure.get("missing_date_ranges") != []:
        errors.append("premium_message_scope_closure_missing_dates")
    release_closure_gates = [
        row
        for row in manifest.get("release_gates") or []
        if isinstance(row, dict)
        and row.get("gate") == "premium_journals_message_data_scope_closure"
    ]
    if len(release_closure_gates) != 1 or release_closure_gates[0] != closure:
        errors.append("premium_message_scope_closure_gate_unbound")
    return errors


def validate_manifest(
    manifest: dict[str, Any],
    *,
    scope: authorized_collection_scope.AuthorizedScope,
) -> list[str]:
    errors: list[str] = []
    if manifest.get("status") != "complete" or manifest.get("release_ready") is not True:
        errors.append("manifest_not_complete_and_release_ready")
    gates = manifest.get("release_gates")
    if not isinstance(gates, list) or not gates or any(
        not isinstance(row, dict) or row.get("passed") is not True for row in gates
    ):
        errors.append("manifest_has_failed_or_missing_release_gates")
    errors.extend(
        timestamp_scope_revalidation.release_timestamp_scope_integrity_errors(
            manifest
        )
    )
    errors.extend(
        reply_provenance_contract.release_executed_command_integrity_errors(
            manifest
        )
    )
    authorized = manifest.get("authorized_collection_scope")
    if not isinstance(authorized, dict):
        errors.append("manifest_authorized_scope_missing")
        return errors
    if authorized.get("source_sha256") != scope.source_sha256:
        errors.append("manifest_authorized_scope_sha256_mismatch")
    allowed = authorized.get("allowed_top_level_containers")
    allowed = allowed if isinstance(allowed, list) else []
    allowed_ids = {
        str(row.get("channel_id") or "")
        for row in allowed
        if isinstance(row, dict)
    }
    if allowed_ids != EXPECTED_PARENT_IDS or len(allowed) != 3:
        errors.append("manifest_authorized_parent_set_mismatch")
    gate = authorized.get("release_gate")
    if not isinstance(gate, dict) or gate.get("passed") is not True:
        errors.append("manifest_authorized_scope_gate_failed")
    excluded = authorized.get("excluded")
    if not isinstance(excluded, dict) or int(
        excluded.get("ambiguous_fail_closed_file_count") or 0
    ) != 0:
        errors.append("manifest_ambiguous_scope_exclusions_remain")
    errors.extend(premium_release_contract_errors(manifest, authorized))
    reconciliation = authorized.get("child_inventory_reconciliation")
    reconciliation = reconciliation if isinstance(reconciliation, dict) else {}
    closure = reconciliation.get("message_scope_closure")
    if not (
        reconciliation.get("provided") is True
        and isinstance(closure, dict)
        and closure.get("gate") == "premium_journals_message_data_scope_closure"
        and closure.get("passed") is True
        and closure.get("status") == "complete"
    ):
        errors.append("premium_journals_message_scope_closure_failed")
    relevance = manifest.get("relevance_policy")
    if relevance != {"enabled": False}:
        errors.append("obsolete_server_wide_relevance_policy_not_strictly_disabled")
    cutoff = parse_utc(manifest.get("data_cutoff_utc"))
    if cutoff < parse_utc(EXPECTED_END_EXCLUSIVE_UTC):
        errors.append("manifest_cutoff_precedes_full_july_20_central_day")
    return errors


def validate_release_evidence(
    payload: dict[str, Any],
    *,
    scope_sha256: str,
    database_sha256: str,
    manifest_sha256: str,
) -> list[str]:
    errors: list[str] = []
    if payload.get("artifact_type") != "discord_collection_progress_manifest":
        errors.append("release_evidence_envelope_type_invalid")
    evidence = payload.get("release_evidence")
    if not isinstance(evidence, dict):
        return [*errors, "release_evidence_object_missing"]
    if evidence.get("artifact_type") != "discord_release_evidence":
        errors.append("release_evidence_type_invalid")
    if evidence.get("status") != "complete" or evidence.get("pending_items") != []:
        errors.append("release_evidence_not_complete")
    if evidence.get("outside_sources_used") not in {False, 0, "0"}:
        errors.append("release_evidence_uses_outside_sources")
    bindings = evidence.get("authorized_collection_scope")
    if not (
        isinstance(bindings, dict)
        and bindings.get("status") == "passed"
        and str(bindings.get("source_sha256") or "").casefold()
        == scope_sha256.casefold()
        and set(bindings.get("authorized_parent_ids") or []) == EXPECTED_PARENT_IDS
        and bindings.get("premium_message_scope_closure_passed") is True
        and bindings.get("premium_authoritative_source_integrity_passed") is True
        and bindings.get("premium_authoritative_directory")
        == PREMIUM_AUTHORITATIVE_DIRECTORY
        and bindings.get("premium_legacy_directory_policy")
        == "preservation_only_not_authoritative"
        and bindings.get("premium_collector_version_required")
        == PREMIUM_COLLECTOR_VERSION
        and bindings.get("premium_accepted_daily_segment_count")
        == PREMIUM_REQUIRED_DAILY_SEGMENTS
        and bindings.get("premium_inventory_census_complete") is False
    ):
        errors.append("release_evidence_scope_binding_invalid")
    source_rows = evidence.get("source_artifacts")
    source_rows = source_rows if isinstance(source_rows, list) else []
    hashes_by_kind: dict[str, set[str]] = {}
    for row in source_rows:
        if not isinstance(row, dict):
            continue
        hashes_by_kind.setdefault(str(row.get("kind") or ""), set()).add(
            str(row.get("sha256") or "").casefold()
        )
    if database_sha256.casefold() not in hashes_by_kind.get(
        "cardinal_sqlite_database", set()
    ):
        errors.append("release_evidence_database_hash_missing")
    if manifest_sha256.casefold() not in hashes_by_kind.get("corpus_manifest", set()):
        errors.append("release_evidence_manifest_hash_missing")
    managed = (
        "scoped_collection_reconciliation",
        "reply_resolution",
        "attachments_and_chart_dependence",
        "claim_calibration",
    )
    for key in managed:
        value = evidence.get(key)
        rows = value if isinstance(value, list) else [value]
        if not rows or any(
            not isinstance(row, dict)
            or str(row.get("status") or "").casefold() not in {"passed", "complete"}
            for row in rows
        ):
            errors.append(f"release_evidence_{key}_not_passed")
    return errors


def validate_database(
    path: Path,
    *,
    scope: authorized_collection_scope.AuthorizedScope,
    allowed_container_ids: set[str],
    expected_messages: dict[str, str],
    executed_command_summary: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        quick = connection.execute("PRAGMA quick_check").fetchone()
        if quick is None or str(quick[0]).casefold() != "ok":
            errors.append("database_quick_check_failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            errors.append("database_foreign_key_check_failed")
        meta = {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT key,value FROM meta")
        }
        if meta.get("authorized_collection_scope_sha256") != scope.source_sha256:
            errors.append("database_authorized_scope_sha256_mismatch")
        if meta.get("source_scope") != "discord_only" or meta.get(
            "outside_sources_used"
        ) != "0":
            errors.append("database_not_discord_only")
        rows = connection.execute(
            "SELECT message_id,channel_id,content_text,raw_json "
            "FROM messages ORDER BY message_id"
        ).fetchall()
        actual_messages = {str(row[0]): str(row[2] or "") for row in rows}
        if actual_messages != expected_messages:
            errors.append("database_corpus_message_identity_or_content_mismatch")
        outside = sorted(
            {str(row[1]) for row in rows if str(row[1]) not in allowed_container_ids}
        )
        if outside:
            errors.append("database_contains_out_of_scope_message_container")
        semantic_rows: list[dict[str, Any]] = []
        for row in rows:
            try:
                raw_row = json.loads(str(row[3]))
            except (TypeError, json.JSONDecodeError):
                errors.append("database_message_raw_json_unreadable")
                continue
            if not isinstance(raw_row, dict) or str(
                raw_row.get("message_id") or ""
            ) != str(row[0]):
                errors.append("database_message_raw_json_identity_mismatch")
                continue
            semantic_rows.append(raw_row)
        errors.extend(
            reply_provenance_contract
            .release_executed_command_semantic_errors(
                semantic_rows,
                executed_command_summary,
            )
        )
        fts_ids = {
            str(row[0])
            for row in connection.execute("SELECT message_id FROM messages_fts")
        }
        if fts_ids != set(expected_messages):
            errors.append("database_message_fts_identity_mismatch")
        source_paths = [
            str(row[0])
            for row in connection.execute("SELECT source_file FROM source_artifacts")
        ]
        if any(not portable_relative(value) for value in source_paths):
            errors.append("database_source_artifact_path_not_portable")
        summary = {
            "message_count": len(actual_messages),
            "allowed_container_count": len(allowed_container_ids),
            "message_fts_count": len(fts_ids),
            "source_artifact_count": len(source_paths),
        }
    finally:
        connection.close()
    return errors, summary


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    paths = {
        "corpus": args.corpus.resolve(),
        "corpus_manifest": args.corpus_manifest.resolve(),
        "authorized_scope": args.authorized_scope.resolve(),
        "database": args.database.resolve(),
        "post_final_release_evidence": args.release_evidence.resolve(),
        "collection_drift_audit": args.drift_audit.resolve(),
    }
    missing = [label for label, path in paths.items() if not path.is_file()]
    if missing:
        raise ScopedQaError("Missing required input(s): " + ", ".join(missing))
    before = {label: sha256_file(path) for label, path in paths.items()}
    scope = authorized_collection_scope.load_validated_scope(
        paths["authorized_scope"],
        expected_guild_id=EXPECTED_GUILD_ID,
        expected_timezone="America/Chicago",
        expected_start_date=EXPECTED_START,
        expected_end_date=EXPECTED_END,
    )
    corpus = read_json_object(paths["corpus"], "scoped corpus")
    manifest = read_json_object(paths["corpus_manifest"], "corpus manifest")
    release_evidence = read_json_object(
        paths["post_final_release_evidence"], "post-final release evidence"
    )
    drift_payload = read_json_object(paths["collection_drift_audit"], "drift audit")

    checks: list[dict[str, Any]] = []

    def add(name: str, errors: list[str], observed: Any = None) -> None:
        checks.append(
            {
                "name": name,
                "passed": not errors,
                "severity": "critical",
                "observed": observed if observed is not None else {"errors": errors},
                "expected": {"errors": []},
                "errors": errors,
            }
        )

    manifest_errors = validate_manifest(manifest, scope=scope)
    add("scoped_manifest_release_contract_passed", manifest_errors)

    scope_errors: list[str] = []
    allowed_ids: set[str] = set()
    try:
        documents = [
            build_cardinal_database_v2.load_document(paths["corpus"]),
            build_cardinal_database_v2.load_document(paths["corpus_manifest"]),
        ]
        allowed_ids = build_cardinal_database_v2.validate_authorized_scope_inputs(
            documents, scope
        )
    except (OSError, ValueError) as exc:
        scope_errors.append(str(exc))
    add(
        "corpus_exact_scope_and_occurrence_binding_passed",
        scope_errors,
        {"allowed_container_count": len(allowed_ids), "errors": scope_errors},
    )

    messages = corpus.get("messages")
    messages = messages if isinstance(messages, list) else []
    expected_messages: dict[str, str] = {}
    corpus_errors: list[str] = []
    for index, row in enumerate(messages, start=1):
        if not isinstance(row, dict):
            corpus_errors.append(f"message_row_not_object:{index}")
            continue
        message_id = str(row.get("message_id") or "")
        if not re.fullmatch(r"\d{15,22}", message_id):
            corpus_errors.append(f"message_id_invalid:{index}")
            continue
        if message_id in expected_messages:
            corpus_errors.append(f"duplicate_message_id:{message_id}")
        expected_messages[message_id] = message_text(row)
    add(
        "corpus_message_identity_unique_and_exact",
        corpus_errors,
        {"message_count": len(expected_messages), "errors": corpus_errors},
    )

    db_errors: list[str]
    db_summary: dict[str, Any]
    if scope_errors or corpus_errors:
        db_errors = ["database_parity_not_evaluable_after_corpus_scope_failure"]
        db_summary = {}
    else:
        try:
            db_errors, db_summary = validate_database(
                paths["database"],
                scope=scope,
                allowed_container_ids=allowed_ids,
                expected_messages=expected_messages,
                executed_command_summary=manifest.get(
                    "executed_command_reply_provenance_integrity"
                ),
            )
        except (sqlite3.Error, OSError, ValueError) as exc:
            db_errors, db_summary = [str(exc)], {}
    add("database_scope_content_and_fts_parity_passed", db_errors, db_summary)

    evidence_errors = validate_release_evidence(
        release_evidence,
        scope_sha256=scope.source_sha256,
        database_sha256=before["database"],
        manifest_sha256=before["corpus_manifest"],
    )
    add("post_final_scoped_release_evidence_passed", evidence_errors)

    drift_summary = validate_corpus.validate_collection_drift_audit(
        drift_payload,
        path=paths["collection_drift_audit"],
        sha256=before["collection_drift_audit"],
        window_start=dt.date(2026, 1, 1),
        window_end=dt.date(2026, 7, 20),
        required_end_exclusive_utc=parse_utc(EXPECTED_END_EXCLUSIVE_UTC),
    )
    drift_errors = list(drift_summary.get("errors") or [])
    if drift_summary.get("passed") is not True:
        drift_errors.append("final_collection_drift_audit_not_passed")
    add("collection_drift_final_audit_passed", sorted(set(drift_errors)), drift_summary)

    after = {label: sha256_file(path) for label, path in paths.items()}
    changed = sorted(label for label in paths if before[label] != after[label])
    add("selected_inputs_preserved_during_validation", changed, {"changed": changed})

    failed = [row for row in checks if row["passed"] is not True]
    cutoff = str(manifest.get("data_cutoff_utc") or EXPECTED_END_EXCLUSIVE_UTC)
    attachment_archive = manifest.get("attachment_archive")
    attachment_archive = (
        attachment_archive if isinstance(attachment_archive, dict) else {}
    )
    attachment_gate = attachment_archive.get("release_gate")
    attachment_gate = attachment_gate if isinstance(attachment_gate, dict) else {}
    return {
        "schema_version": "1.0.0",
        "artifact_type": "independent_discord_corpus_validation",
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "status": "passed" if not failed else "needs_revision",
        "overall_assessment": "Ready to share" if not failed else "Not ready to share",
        "scope": {
            "guild_id": EXPECTED_GUILD_ID,
            "source_scope": "discord_only",
            "outside_sources_used": False,
            "window_calendar_timezone": "America/Chicago",
            "window_start_local_date": EXPECTED_START,
            "window_end_local_date_inclusive": EXPECTED_END,
            "window_start_utc": "2026-01-01T06:00:00Z",
            "window_end_exclusive_utc": EXPECTED_END_EXCLUSIVE_UTC,
            "local_calendar_days": 201,
            "data_cutoff_utc": cutoff,
            "final_day_complete": parse_utc(cutoff)
            >= parse_utc(EXPECTED_END_EXCLUSIVE_UTC),
            "authorized_scope_sha256": scope.source_sha256,
            "authorized_parent_ids": sorted(scope.parent_ids),
            "premium_authoritative_directory": PREMIUM_AUTHORITATIVE_DIRECTORY,
            "premium_collector_version_required": PREMIUM_COLLECTOR_VERSION,
            "premium_daily_segment_count": PREMIUM_REQUIRED_DAILY_SEGMENTS,
            "premium_inventory_census_complete": False,
        },
        "inputs": {key: str(path) for key, path in paths.items()},
        "source_artifacts": [
            {
                "kind": label,
                "path": path.name,
                "sha256": before[label],
                "size_bytes": path.stat().st_size,
            }
            for label, path in paths.items()
        ],
        "relevance_policy": {"enabled": False},
        "attachments": {
            "archive": {
                "terminal_coverage_complete": attachment_gate.get(
                    "terminal_coverage_complete"
                )
                is True,
                "literal_release_complete": attachment_gate.get(
                    "literal_release_complete"
                )
                is True,
                "entry_set_parity": attachment_archive.get("entry_set_parity")
                is True,
                "sha256": attachment_archive.get("manifest_sha256"),
            }
        },
        "collection_drift_audit": drift_summary,
        "database_validation": {
            **db_summary,
            "status": "passed" if not db_errors else "failed",
            "sha256": before["database"],
            "source_scope": "discord_only",
            "outside_sources_used": 0,
        },
        "preservation": {
            "before": {"status": "passed", "input_sha256": before},
            "after": {"status": "passed", "input_sha256": after},
        },
        "source_hash_verification": {
            "before": {"status": "passed", "input_sha256": before},
            "after": {"status": "passed", "input_sha256": after},
        },
        "checks": checks,
        "failure_counts": {
            "critical": len(failed),
            "high": 0,
            "medium_or_low": 0,
        },
        "limitations": [
            "Completeness is limited to the three user-authorized Discord parents and provenance-proven Premium Journals child threads visible to the authenticated account.",
            "Deleted-before-collection or inaccessible Discord content cannot be independently recovered.",
            "Discord message time is not assumed to equal the market setup time discussed in the message.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--authorized-scope", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--release-evidence", type=Path, required=True)
    parser.add_argument("--drift-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build_report(args)
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "overall_assessment": report["overall_assessment"],
                    "output": str(output),
                    "failure_counts": report["failure_counts"],
                },
                indent=2,
            )
        )
        return 0 if report["status"] == "passed" else 1
    except (
        OSError,
        ValueError,
        sqlite3.Error,
        ScopedQaError,
        authorized_collection_scope.AuthorizedScopeError,
    ) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
