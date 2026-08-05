#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build_corpus  # noqa: E402


QUARANTINE = ROOT / "raw" / "quarantine_collection_errors"
CANONICAL_DIR = ROOT / "raw" / "channel_segments"
ID_RE = re.compile(r"^\d{15,22}$")


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def contained(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def validate_replacement(stage: Path, canonical: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    staged = load_object(stage)
    old = load_object(canonical)
    if staged.get("collector_version") != "2.5":
        raise ValueError("staged collector_version must be 2.5")
    if staged.get("complete") is not True or old.get("complete") is not True:
        raise ValueError("both staged and legacy artifacts must be declared complete")
    if staged.get("guild_id") != old.get("guild_id") or staged.get("guild_id") != "1167376964680691732":
        raise ValueError("guild binding mismatch")
    if staged.get("segment") != old.get("segment"):
        raise ValueError("segment/query binding mismatch")
    staged_container = staged.get("requested_container") or {}
    old_container = old.get("requested_container") or {}
    for field in ("channel_id", "channel_name", "channel_kind", "category_name"):
        if staged_container.get(field) != old_container.get(field):
            raise ValueError(f"requested container {field} binding mismatch")

    rows = staged.get("messages")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("staged messages must be an object array")
    total = staged.get("reported_total")
    pages = staged.get("reported_pages")
    expected_pages = (total + 24) // 25 if isinstance(total, int) and total else 0
    ids = [str(row.get("message_id") or "") for row in rows]
    indices = [row.get("result_index") for row in rows]
    if not isinstance(total, int) or total < 0:
        raise ValueError("reported_total invalid")
    if len(rows) != total or staged.get("captured_rows") != total:
        raise ValueError("row count mismatch")
    if len(set(ids)) != total or staged.get("unique_message_ids") != total:
        raise ValueError("unique message count mismatch")
    if any(not ID_RE.fullmatch(message_id) for message_id in ids):
        raise ValueError("invalid message ID")
    if indices != list(range(1, total + 1)):
        raise ValueError("result index sequence mismatch")
    if pages != expected_pages or staged.get("pages_captured") != expected_pages:
        raise ValueError("page count mismatch")
    if staged.get("gap_indices") not in ([], None):
        raise ValueError("gap indices are nonempty")
    if staged.get("container_mismatch_count") not in (0, None):
        raise ValueError("container mismatch count is nonzero")

    container = staged.get("requested_container") or {}
    channel_id = str(container.get("channel_id") or "")
    expected_prefix = f"https://discord.com/channels/1167376964680691732/{channel_id}/"
    exact_permalinks = sum(
        row.get("exact_permalink") == f"{expected_prefix}{row.get('message_id')}" for row in rows
    )
    if exact_permalinks != total:
        raise ValueError("one or more exact permalinks failed channel/message binding")
    conflict_count = sum(
        row.get("author_id_conflict") is True
        or row.get("reply_to_message_id_conflict") is True
        or row.get("reply_to_channel_id_conflict") is True
        or row.get("thread_channel_id_conflict") is True
        or row.get("exact_permalink_conflict_detected") is True
        for row in rows
    )
    if conflict_count:
        raise ValueError("one or more row provenance conflicts are present")

    attachments = [
        attachment
        for row in rows
        for attachment in (row.get("attachments") if isinstance(row.get("attachments"), list) else [])
        if isinstance(attachment, dict)
    ]
    invalid_owned = [
        attachment
        for attachment in attachments
        if attachment.get("ownership_status") == "owned_exact"
        and (
            (attachment.get("ownership_evidence") or {}).get("exact") is not True
            or (attachment.get("ownership_evidence") or {}).get("source_channel_id") != channel_id
            or attachment.get("href_in_message_content") is not False
        )
    ]
    if invalid_owned:
        raise ValueError("owned attachment proof is invalid")

    scope = build_corpus.make_scope(
        "1167376964680691732", "2026-01-01", "2026-07-20", "America/Chicago"
    )
    normalized, _ = build_corpus.validate_segment_payload(stage, staged, scope)
    if not normalized.get("computed_complete"):
        raise ValueError(
            "corpus contract rejected staged artifact: "
            + ",".join(normalized.get("validation_errors") or [])
        )
    evidence = staged.get("completion_evidence") or {}
    if normalized.get("completion_evidence_valid") is not True:
        raise ValueError("inline completion evidence is invalid")
    if total == 0:
        observations = ((evidence.get("stable_empty") or {}).get("observations") or [])
        if evidence.get("terminal_state") != "stable_empty" or len(observations) != 3:
            raise ValueError("zero result requires exactly three durable stable-empty observations")
    else:
        observations = ((evidence.get("stable_bottom") or {}).get("observations") or [])
        if (
            evidence.get("terminal_state") != "stable_bottom"
            or len(observations) != 2
            or any(observation.get("has_enabled_next") is not False for observation in observations)
        ):
            raise ValueError("positive result requires two durable stable-bottom observations")

    old_ids = {str(row.get("message_id")) for row in old.get("messages", []) if isinstance(row, dict)}
    new_ids = set(ids)
    exact_author_ids = sum(
        ID_RE.fullmatch(str(row.get("author_id") or "")) is not None
        and bool(row.get("author_id_source"))
        and row.get("author_id_conflict") is not True
        for row in rows
    )
    qa = {
        "corpus_contract_computed_complete": True,
        "inline_completion_evidence_valid": True,
        "completion_terminal_state": evidence.get("terminal_state"),
        "completion_observation_count": len(observations),
        "exact_result_index_sequence": True,
        "exact_permalinks": exact_permalinks,
        "exact_author_ids": exact_author_ids,
        "unresolved_author_rows": total - exact_author_ids,
        "row_provenance_conflict_count": conflict_count,
        "attachment_count": len(attachments),
        "owned_exact_attachment_count": sum(
            attachment.get("ownership_status") == "owned_exact" for attachment in attachments
        ),
        "unresolved_attachment_count": sum(
            attachment.get("ownership_status") == "unresolved" for attachment in attachments
        ),
        "invalid_owned_attachment_count": len(invalid_owned),
        "legacy_unique_ids": len(old_ids),
        "replacement_unique_ids": len(new_ids),
        "shared_ids": len(old_ids & new_ids),
        "added_ids": sorted(new_ids - old_ids),
        "missing_ids": sorted(old_ids - new_ids),
    }
    return old, staged, qa


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.next-{os.getpid()}")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--canonical", required=True)
    args = parser.parse_args()
    stage = (ROOT / args.stage).resolve()
    canonical = (ROOT / args.canonical).resolve()
    if not contained(stage, QUARANTINE):
        raise ValueError("stage must remain inside raw/quarantine_collection_errors")
    if not contained(canonical, CANONICAL_DIR):
        raise ValueError("canonical must remain inside raw/channel_segments")
    if not stage.is_file() or not canonical.is_file():
        raise FileNotFoundError("stage and canonical must both exist")

    old, staged, qa = validate_replacement(stage, canonical)
    old_sha = digest(canonical)
    staged_sha = digest(stage)
    old_version = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(old.get("collector_version") or "unknown"))
    backup = QUARANTINE / f"{canonical.stem}.legacy-v{old_version}_{old_sha[:12]}.json"
    note = QUARANTINE / f"{canonical.stem}.v2.5-replacement-note.json"
    if backup.exists() or note.exists():
        raise FileExistsError("preservation backup or replacement note already exists")

    with canonical.open("rb") as source, backup.open("xb") as destination:
        shutil.copyfileobj(source, destination)
        destination.flush()
        os.fsync(destination.fileno())
    if digest(backup) != old_sha:
        raise ValueError("preserved legacy hash mismatch")

    os.replace(stage, canonical)
    if digest(canonical) != staged_sha:
        raise ValueError("promoted replacement hash mismatch")

    container = staged.get("requested_container") or {}
    note_payload = {
        "event_type": "discord_collector_version_replacement",
        "guild_id": staged.get("guild_id"),
        "channel_id": container.get("channel_id"),
        "channel_name": container.get("channel_name"),
        "segment_start": (staged.get("segment") or {}).get("start"),
        "segment_end": (staged.get("segment") or {}).get("end"),
        "query": (staged.get("segment") or {}).get("query"),
        "promoted_at_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "old_collector_version": old.get("collector_version"),
        "new_collector_version": staged.get("collector_version"),
        "old_reported_total": old.get("reported_total"),
        "new_reported_total": staged.get("reported_total"),
        "legacy_final_original_path": canonical.relative_to(ROOT).as_posix(),
        "legacy_final_quarantine_path": backup.relative_to(ROOT).as_posix(),
        "legacy_final_sha256": old_sha,
        "replacement_final_path": canonical.relative_to(ROOT).as_posix(),
        "replacement_final_sha256": staged_sha,
        "message_id_reconciliation": {
            "shared_ids": qa["shared_ids"],
            "added_ids": qa["added_ids"],
            "missing_ids": qa["missing_ids"],
            "causal_claim": "No deletion, edit, or other cause is claimed from search-set differences alone.",
        },
        "validation": qa,
        "action": "Preserved the byte-exact legacy canonical artifact and atomically promoted the independently validated v2.5 staged recapture.",
        "outside_sources_used": False,
    }
    atomic_write_json(note, note_payload)
    print(
        json.dumps(
            {
                "canonical": canonical.relative_to(ROOT).as_posix(),
                "replacement_sha256": staged_sha,
                "preserved": backup.relative_to(ROOT).as_posix(),
                "preserved_sha256": old_sha,
                "note": note.relative_to(ROOT).as_posix(),
                "old_total": old.get("reported_total"),
                "new_total": staged.get("reported_total"),
                "qa": qa,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
