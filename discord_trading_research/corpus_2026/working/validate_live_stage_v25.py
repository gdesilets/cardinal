from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qa"))

import build_corpus as corpus_builder  # noqa: E402
import validate_corpus as corpus_qa  # noqa: E402


GUILD_ID = "1167376964680691732"
CHANNEL_ID = "1329615478716502097"
PREFIX = f"channel_live_{CHANNEL_ID}"


def validate(date_text: str, source: str) -> tuple[dict[str, object], bool]:
    directory = (
        ROOT / "working" / "live_v25_replacements"
        if source == "stage"
        else ROOT / "raw" / "channel_segments"
    )
    path = directory / f"{PREFIX}_{date_text}_{date_text}.json"
    data = path.read_bytes()
    payload = json.loads(data.decode("utf-8"))
    rows = payload.get("messages") if isinstance(payload.get("messages"), list) else []

    scope = corpus_builder.make_scope(
        GUILD_ID,
        "2026-01-01",
        "2026-07-20",
        "America/Chicago",
    )
    normalized, _ = corpus_builder.validate_segment_payload(path, payload, scope)
    qa_issues: dict[str, list[dict[str, object]]] = collections.defaultdict(list)
    corpus_qa.validate_one_segment(
        path,
        guild_id=GUILD_ID,
        window_start=dt.date(2026, 1, 1),
        window_end=dt.date(2026, 7, 20),
        cutoff_utc=dt.datetime.now(dt.timezone.utc),
        issues=qa_issues,
    )

    evidence = payload.get("completion_evidence") or {}
    bottom = (evidence.get("stable_bottom") or {}).get("observations") or []
    empty = (evidence.get("stable_empty") or {}).get("observations") or []
    reported_total = payload.get("reported_total")
    reported_pages = payload.get("reported_pages")
    reply_statuses = collections.Counter(
        str(row.get("reply_target_resolution_status")) for row in rows
    )
    reply_context_rows = [
        row
        for row in rows
        if row.get("reply_context_present") is True
        or bool(str(row.get("reply_context") or "").strip())
    ]
    attachments = [
        attachment
        for row in rows
        for attachment in (row.get("attachments") or [])
        if isinstance(attachment, dict)
    ]
    attachment_statuses = collections.Counter(
        str(attachment.get("ownership_status")) for attachment in attachments
    )
    explicit_fields = (
        "reply_target_resolution_status",
        "reply_target_unavailability_documented",
        "discord_system_event_exact",
        "discord_system_event_type",
        "timestamp_exact_fallback_source",
    )

    checks = {
        "collector_version_2_5": payload.get("collector_version") == "2.5",
        "counts_reconcile": (
            isinstance(reported_total, int)
            and payload.get("captured_rows") == reported_total
            and len(rows) == reported_total
            and payload.get("unique_message_ids") == reported_total
            and len({row.get("message_id") for row in rows}) == reported_total
        ),
        "pages_reconcile": (
            isinstance(reported_total, int)
            and isinstance(reported_pages, int)
            and reported_pages == (reported_total + 24) // 25
            and payload.get("pages_captured") == reported_pages
        ),
        "gaps_and_container_clean": (
            payload.get("gap_indices") == []
            and payload.get("container_mismatch_count") == 0
        ),
        "inline_completion_valid": (
            normalized.get("completion_evidence_source") == "inline"
            and normalized.get("completion_evidence_valid") is True
            and payload.get("completion_evidence_validation", {}).get("valid") is True
        ),
        "terminal_proof_exact": (
            evidence.get("terminal_state") == "stable_empty"
            and reported_total == 0
            and len(empty) == 3
        )
        or (
            evidence.get("terminal_state") == "stable_bottom"
            and isinstance(reported_total, int)
            and reported_total > 0
            and len(bottom) == 2
            and all(item.get("has_enabled_next") is False for item in bottom)
        ),
        "core_validation_clean": (
            normalized.get("computed_complete") is True
            and normalized.get("validation_errors") == []
            and normalized.get("validation_warnings") == []
        ),
        "independent_row_qa_clean": not qa_issues,
        "all_rows_have_explicit_field_families": all(
            all(field in row for field in explicit_fields) for row in rows
        ),
        "timestamp_provenance_clean": all(
            row.get("timestamp_scope_exact") is True
            or row.get("discord_system_event_exact") is True
            for row in rows
        ),
        "content_and_container_provenance_clean": all(
            row.get("content_scope_exact") is not False
            and row.get("collection_channel_id") == CHANNEL_ID
            and row.get("exact_permalink_conflict_detected") is not True
            for row in rows
        ),
        "reply_provenance_clean": all(
            row.get("reply_to_message_id_conflict") is not True
            and row.get("reply_to_channel_id_conflict") is not True
            and row.get("reply_target_resolution_status")
            != "unresolved_without_exact_target_id"
            for row in rows
        )
        and all(row.get("reply_target_resolution_status") for row in reply_context_rows),
        "attachment_ownership_provenance_clean": all(
            attachment.get("ownership_status") in {"owned_exact", "non_owned_exact"}
            and isinstance(attachment.get("ownership_evidence"), dict)
            and attachment["ownership_evidence"].get("exact") is True
            for attachment in attachments
        ),
    }

    report: dict[str, object] = {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "date": date_text,
        "source": source,
        "counts": {
            "reported": reported_total,
            "captured": len(rows),
            "unique": len({row.get("message_id") for row in rows}),
            "reported_pages": reported_pages,
            "pages_captured": payload.get("pages_captured"),
        },
        "completion": {
            "terminal_state": evidence.get("terminal_state"),
            "stable_empty_observations": len(empty),
            "stable_bottom_observations": len(bottom),
            "stable_bottom_next_enabled_states": [
                item.get("has_enabled_next") for item in bottom
            ],
        },
        "row_qa": {
            "stage_system_events_exact": sum(
                row.get("discord_system_event_exact") is True
                and row.get("discord_system_event_type") != "poll_closed"
                for row in rows
            ),
            "poll_closed_events_exact": sum(
                row.get("discord_system_event_exact") is True
                and row.get("discord_system_event_type") == "poll_closed"
                for row in rows
            ),
            "reply_status_counts": dict(reply_statuses),
            "reply_context_rows": len(reply_context_rows),
            "attachment_ownership_counts": dict(attachment_statuses),
            "qa_issue_counts": {key: len(value) for key, value in qa_issues.items()},
        },
        "checks": checks,
        "failed_checks": sorted(key for key, passed in checks.items() if not passed),
    }
    return report, all(checks.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--source", choices=("stage", "canonical"), default="stage")
    args = parser.parse_args()
    report, passed = validate(args.date, args.source)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
