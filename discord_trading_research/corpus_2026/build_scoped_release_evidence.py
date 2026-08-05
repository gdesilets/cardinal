#!/usr/bin/env python3
"""Build hash-bound release evidence for the authorized three-channel corpus."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import authorized_collection_scope
import build_cardinal_database_v2
import reply_provenance_contract
import timestamp_scope_revalidation


GUILD_ID = "1167376964680691732"
WINDOW_START = "2026-01-01"
WINDOW_END = "2026-07-20"
END_EXCLUSIVE_UTC = "2026-07-21T05:00:00Z"
PREMIUM_PARENT_ID = "1283941772577472643"
PREMIUM_AUTHORITATIVE_DIRECTORY = "raw/channel_segments_v2_5"
PREMIUM_LEGACY_DIRECTORY = "raw/channel_segments"
PREMIUM_COLLECTOR_VERSION = "2.6"
PREMIUM_REQUIRED_DAILY_SEGMENTS = 201


class ScopedReleaseEvidenceError(RuntimeError):
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
        raise ScopedReleaseEvidenceError(f"Could not read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ScopedReleaseEvidenceError(f"{label} must contain a JSON object")
    return value


def parse_utc(value: Any) -> dt.datetime:
    text = str(value or "").strip()
    try:
        parsed = dt.datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text
        )
    except ValueError as exc:
        raise ScopedReleaseEvidenceError(f"Invalid UTC timestamp: {text!r}") from exc
    if parsed.tzinfo is None:
        raise ScopedReleaseEvidenceError(f"UTC timestamp has no offset: {text!r}")
    return parsed.astimezone(dt.timezone.utc)


def source_record(kind: str, path: Path) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": path.name,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def validate_premium_release_contract(
    manifest: dict[str, Any],
    authorized: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
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
            raise ScopedReleaseEvidenceError(
                f"Premium authoritative source-integrity field {key} mismatch"
            )
    if (
        type(path_policy.get("accepted_premium_bound_source_file_count")) is not int
        or path_policy.get("accepted_premium_bound_source_file_count")
        < PREMIUM_REQUIRED_DAILY_SEGMENTS
    ):
        raise ScopedReleaseEvidenceError(
            "Premium immutable provenance source-file coverage is incomplete"
        )
    for key in (
        "accepted_premium_source_file_set_sha256",
        "accepted_premium_message_id_set_sha256",
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", str(path_policy.get(key) or "")):
            raise ScopedReleaseEvidenceError(
                f"Premium authoritative source-integrity field {key} is missing"
            )
    release_path_gates = [
        row
        for row in manifest.get("release_gates") or []
        if isinstance(row, dict)
        and row.get("gate")
        == "premium_journals_authoritative_v2_5_source_integrity"
    ]
    if len(release_path_gates) != 1 or release_path_gates[0] != path_policy:
        raise ScopedReleaseEvidenceError(
            "Premium authoritative source-integrity gate is missing, duplicated, or unbound"
        )

    reconciliation = authorized.get("child_inventory_reconciliation")
    reconciliation = reconciliation if isinstance(reconciliation, dict) else {}
    if any(
        reconciliation.get(key) is not False
        for key in ("inventory_complete", "enumeration_complete", "closure_proven")
    ):
        raise ScopedReleaseEvidenceError(
            "Premium lower-bound child inventory must remain explicitly non-closed"
        )
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
            raise ScopedReleaseEvidenceError(
                f"Premium 201-day message-scope closure field {key} mismatch"
            )
    if closure.get("missing_date_ranges") != []:
        raise ScopedReleaseEvidenceError(
            "Premium 201-day message-scope closure still has missing dates"
        )
    release_closure_gates = [
        row
        for row in manifest.get("release_gates") or []
        if isinstance(row, dict)
        and row.get("gate") == "premium_journals_message_data_scope_closure"
    ]
    if len(release_closure_gates) != 1 or release_closure_gates[0] != closure:
        raise ScopedReleaseEvidenceError(
            "Premium 201-day message-scope closure gate is missing, duplicated, or unbound"
        )
    return path_policy, reconciliation


def validate_manifest(
    manifest: dict[str, Any],
    scope: authorized_collection_scope.AuthorizedScope,
) -> dict[str, Any]:
    if manifest.get("status") != "complete" or manifest.get("release_ready") is not True:
        raise ScopedReleaseEvidenceError("Corpus manifest is not complete and release-ready")
    gates = manifest.get("release_gates")
    if not isinstance(gates, list) or not gates or any(
        not isinstance(row, dict) or row.get("passed") is not True for row in gates
    ):
        raise ScopedReleaseEvidenceError("Corpus manifest has a failed release gate")
    timestamp_errors = (
        timestamp_scope_revalidation.release_timestamp_scope_integrity_errors(
            manifest
        )
    )
    if timestamp_errors:
        raise ScopedReleaseEvidenceError(
            "Corpus timestamp-scope integrity failed: "
            + ", ".join(timestamp_errors)
        )
    executed_command_errors = (
        reply_provenance_contract.release_executed_command_integrity_errors(
            manifest
        )
    )
    if executed_command_errors:
        raise ScopedReleaseEvidenceError(
            "Corpus executed-command reply provenance failed: "
            + ", ".join(executed_command_errors)
        )
    if manifest.get("relevance_policy") != {"enabled": False}:
        raise ScopedReleaseEvidenceError(
            "Obsolete server-wide relevance policy is not strictly disabled"
        )
    authorized = manifest.get("authorized_collection_scope")
    if not isinstance(authorized, dict):
        raise ScopedReleaseEvidenceError("Manifest authorized scope is missing")
    if authorized.get("source_sha256") != scope.source_sha256:
        raise ScopedReleaseEvidenceError("Manifest authorized scope hash mismatch")
    parent_rows = authorized.get("allowed_top_level_containers")
    parent_rows = parent_rows if isinstance(parent_rows, list) else []
    if {
        str(row.get("channel_id") or "")
        for row in parent_rows
        if isinstance(row, dict)
    } != set(scope.parent_ids) or len(parent_rows) != 3:
        raise ScopedReleaseEvidenceError("Manifest authorized parent set mismatch")
    if not isinstance(authorized.get("release_gate"), dict) or authorized[
        "release_gate"
    ].get("passed") is not True:
        raise ScopedReleaseEvidenceError("Manifest authorized-scope gate failed")
    _path_policy, reconciliation = validate_premium_release_contract(
        manifest, authorized
    )
    if reconciliation.get("provided") is not True:
        raise ScopedReleaseEvidenceError(
            "Premium Journals message-scope closure did not pass"
        )
    if parse_utc(manifest.get("data_cutoff_utc")) < parse_utc(END_EXCLUSIVE_UTC):
        raise ScopedReleaseEvidenceError("Manifest cutoff precedes the full Jul 20 day")
    return reconciliation


def inspect_database(
    database: Path,
    scope: authorized_collection_scope.AuthorizedScope,
    executed_command_summary: dict[str, Any],
) -> dict[str, Any]:
    connection = sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    )
    try:
        quick = connection.execute("PRAGMA quick_check").fetchone()
        if quick is None or str(quick[0]).casefold() != "ok":
            raise ScopedReleaseEvidenceError("Database quick_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ScopedReleaseEvidenceError("Database foreign_key_check failed")
        meta = {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT key,value FROM meta")
        }
        if meta.get("authorized_collection_scope_sha256") != scope.source_sha256:
            raise ScopedReleaseEvidenceError("Database authorized scope hash mismatch")
        if meta.get("source_scope") != "discord_only" or meta.get(
            "outside_sources_used"
        ) != "0":
            raise ScopedReleaseEvidenceError("Database is not Discord-only")
        message_count = int(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
        semantic_rows: list[dict[str, Any]] = []
        for database_message_id, raw_json in connection.execute(
            "SELECT message_id,raw_json FROM messages ORDER BY message_id"
        ):
            try:
                raw_row = json.loads(str(raw_json))
            except (TypeError, json.JSONDecodeError) as exc:
                raise ScopedReleaseEvidenceError(
                    "Database contains unreadable message raw_json"
                ) from exc
            if not isinstance(raw_row, dict) or str(
                raw_row.get("message_id") or ""
            ) != str(database_message_id):
                raise ScopedReleaseEvidenceError(
                    "Database message raw_json identity mismatch"
                )
            semantic_rows.append(raw_row)
        semantic_errors = (
            reply_provenance_contract.release_executed_command_semantic_errors(
                semantic_rows,
                executed_command_summary,
            )
        )
        if semantic_errors:
            raise ScopedReleaseEvidenceError(
                "Database executed-command row audit failed: "
                + ", ".join(semantic_errors)
            )
        unresolved_reply_rows = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM messages
                WHERE reply_to_message_id IS NOT NULL
                  AND reply_target_state NOT IN
                    ('resolved','outside_window','context_stub','deleted',
                     'inaccessible','unavailable')
                """
            ).fetchone()[0]
        )
        unanswered_with_answer_link = 0
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        if {"questions", "question_answer_links"} <= tables:
            unanswered_with_answer_link = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM questions q
                    WHERE q.resolution_status='answered'
                      AND NOT EXISTS(
                        SELECT 1 FROM question_answer_links l
                        WHERE l.question_id=q.question_id
                      )
                    """
                ).fetchone()[0]
            )
        if unresolved_reply_rows or unanswered_with_answer_link:
            raise ScopedReleaseEvidenceError(
                "Reply/question resolution contains unclassified or unlinked answered rows"
            )
        discord_audit_count = 0
        if "v_discord_only_audit" in tables:
            discord_audit_count = int(
                connection.execute("SELECT COUNT(*) FROM v_discord_only_audit").fetchone()[0]
            )
        else:
            raise ScopedReleaseEvidenceError("Database Discord-only audit view is missing")
        if discord_audit_count:
            raise ScopedReleaseEvidenceError("Database Discord-only audit has failures")
        accepted_without_evidence = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM claims c
                WHERE c.resolution_status='accepted'
                  AND NOT EXISTS(
                    SELECT 1 FROM claim_evidence ce WHERE ce.claim_id=c.claim_id
                  )
                """
            ).fetchone()[0]
        )
        if accepted_without_evidence:
            raise ScopedReleaseEvidenceError("Accepted claims without evidence remain")
        return {
            "message_count": message_count,
            "unclassified_reply_count": unresolved_reply_rows,
            "answered_question_without_link_count": unanswered_with_answer_link,
            "discord_only_audit_failure_count": discord_audit_count,
            "accepted_claim_without_evidence_count": accepted_without_evidence,
        }
    finally:
        connection.close()


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    paths = {
        "scoped_corpus": args.corpus.resolve(),
        "corpus_manifest": args.corpus_manifest.resolve(),
        "authorized_collection_scope": args.authorized_scope.resolve(),
        "cardinal_sqlite_database": args.database.resolve(),
    }
    missing = [kind for kind, path in paths.items() if not path.is_file()]
    if missing:
        raise ScopedReleaseEvidenceError("Missing input(s): " + ", ".join(missing))
    before = {kind: sha256_file(path) for kind, path in paths.items()}
    scope = authorized_collection_scope.load_validated_scope(
        paths["authorized_collection_scope"],
        expected_guild_id=GUILD_ID,
        expected_timezone="America/Chicago",
        expected_start_date=WINDOW_START,
        expected_end_date=WINDOW_END,
    )
    corpus = read_json_object(paths["scoped_corpus"], "scoped corpus")
    manifest = read_json_object(paths["corpus_manifest"], "corpus manifest")
    reconciliation = validate_manifest(manifest, scope)
    path_policy = manifest["authorized_collection_scope"]["canonical_path_policy"]
    documents = [
        build_cardinal_database_v2.load_document(paths["scoped_corpus"]),
        build_cardinal_database_v2.load_document(paths["corpus_manifest"]),
    ]
    allowed_ids = build_cardinal_database_v2.validate_authorized_scope_inputs(
        documents, scope
    )
    database_summary = inspect_database(
        paths["cardinal_sqlite_database"],
        scope,
        manifest.get("executed_command_reply_provenance_integrity"),
    )
    corpus_messages = corpus.get("messages")
    corpus_messages = corpus_messages if isinstance(corpus_messages, list) else []
    if database_summary["message_count"] != len(corpus_messages):
        raise ScopedReleaseEvidenceError("Corpus/database message count mismatch")
    attachment_archive = manifest.get("attachment_archive")
    attachment_archive = attachment_archive if isinstance(attachment_archive, dict) else {}
    attachment_gate = attachment_archive.get("release_gate")
    attachment_gate = attachment_gate if isinstance(attachment_gate, dict) else {}
    if not (
        attachment_gate.get("passed") is True
        and attachment_gate.get("terminal_coverage_complete") is True
        and attachment_gate.get("literal_release_complete") is True
        and int((attachment_archive.get("counts") or {}).get("failed") or 0) == 0
    ):
        raise ScopedReleaseEvidenceError("Attachment/chart release gate did not pass")
    after = {kind: sha256_file(path) for kind, path in paths.items()}
    if before != after:
        raise ScopedReleaseEvidenceError("An input changed during evidence generation")

    generated = dt.datetime.now(dt.timezone.utc)
    if generated < parse_utc(END_EXCLUSIVE_UTC):
        raise ScopedReleaseEvidenceError("Evidence cannot be generated before the window ends")
    source_rows = [source_record(kind, path) for kind, path in paths.items()]
    return {
        "schema_version": "1.0.0",
        "artifact_type": "discord_collection_progress_manifest",
        "generated_at_utc": generated.isoformat().replace("+00:00", "Z"),
        "status": "complete",
        "scope": {
            "guild_id": GUILD_ID,
            "source_scope": "discord_only",
            "outside_sources_used": False,
            "authorized_parent_ids": sorted(scope.parent_ids),
        },
        "release_evidence": {
            "schema_version": "1.0.0",
            "artifact_type": "discord_release_evidence",
            "status": "complete",
            "generated_at_utc": generated.isoformat().replace("+00:00", "Z"),
            "required_cutoff_utc": END_EXCLUSIVE_UTC,
            "source_scope": "discord_only",
            "outside_sources_used": 0,
            "pending_items": [],
            "generator": {
                "local_only": True,
                "browser_calls_made": 0,
                "network_calls_made": 0,
                "raw_files_modified": 0,
            },
            "source_artifacts": source_rows,
            "authorized_collection_scope": {
                "status": "passed",
                "source_sha256": scope.source_sha256,
                "authorized_parent_ids": sorted(scope.parent_ids),
                "authorized_container_count_including_proven_children": len(allowed_ids),
                "premium_message_scope_closure_passed": True,
                "premium_authoritative_source_integrity_passed": True,
                "premium_authoritative_directory": PREMIUM_AUTHORITATIVE_DIRECTORY,
                "premium_legacy_directory_policy": "preservation_only_not_authoritative",
                "premium_collector_version_required": PREMIUM_COLLECTOR_VERSION,
                "premium_accepted_daily_segment_count": path_policy.get(
                    "accepted_premium_segment_count"
                ),
                "premium_inventory_census_complete": False,
            },
            "scoped_collection_reconciliation": {
                "status": "passed",
                "source_sha256": reconciliation.get("source_sha256"),
                "bound_inputs": reconciliation.get("bound_inputs"),
                "message_scope_closure": reconciliation.get("message_scope_closure"),
            },
            "reply_resolution": {
                "status": "passed",
                "unclassified_reply_count": database_summary[
                    "unclassified_reply_count"
                ],
                "answered_question_without_link_count": database_summary[
                    "answered_question_without_link_count"
                ],
            },
            "attachments_and_chart_dependence": {
                "status": "passed",
                "terminal_coverage_complete": True,
                "literal_release_complete": True,
                "failed_attachment_count": 0,
            },
            "claim_calibration": {
                "status": "passed",
                "discord_only_audit_failure_count": database_summary[
                    "discord_only_audit_failure_count"
                ],
                "accepted_claim_without_evidence_count": database_summary[
                    "accepted_claim_without_evidence_count"
                ],
            },
            "residual_reviews": [],
        },
        "release_review_packets": {
            "schema_version": "1.0.0",
            "artifact_type": "discord_residual_review_packets",
            "review_required": False,
            "packet_count": 0,
            "packets": [],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--authorized-scope", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = build_payload(args)
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        print(
            json.dumps(
                {
                    "status": "complete",
                    "output": str(output),
                    "sha256": sha256_file(output),
                },
                indent=2,
            )
        )
        return 0
    except (
        OSError,
        ValueError,
        sqlite3.Error,
        ScopedReleaseEvidenceError,
        authorized_collection_scope.AuthorizedScopeError,
    ) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
