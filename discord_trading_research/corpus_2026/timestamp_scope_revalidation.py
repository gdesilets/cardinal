from __future__ import annotations

"""Fail-closed Discord message timestamp-scope provenance checks.

Most Discord search rows expose a message-owned timestamp through the exact
``message-timestamp-<message_id>`` ARIA token.  A very small set of Discord
system events do not.  Those rows are accepted only when either the collector
stored the complete inline fallback contract or an immutable adjacent sidecar
binds preserved DOM evidence to the exact final segment bytes and message row.
"""

import copy
import datetime as dt
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import premium_journals_system_event_timestamp_v1


SCHEMA_VERSION = "1.0.0"
ARTIFACT_TYPE = "discord_timestamp_scope_revalidation"
TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX = (
    ".timestamp-scope-revalidation.json"
)
DISCORD_EPOCH_MS = 1_420_070_400_000
DISCORD_ID_RE = re.compile(r"^\d{15,22}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PIN_FALLBACK_SOURCE = "discord_snowflake_exact_pinned_message_system_event"


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_sha256(row: dict[str, Any]) -> str:
    return sha256_bytes(compact_json(row).encode("utf-8"))


def parse_iso_utc(value: Any) -> dt.datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("missing timestamp")
    parsed = dt.datetime.fromisoformat(
        text[:-1] + "+00:00" if text.endswith("Z") else text
    )
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no UTC offset")
    return parsed.astimezone(dt.timezone.utc)


def snowflake_time(message_id: Any) -> dt.datetime:
    text = str(message_id or "")
    if not DISCORD_ID_RE.fullmatch(text):
        raise ValueError("not a Discord snowflake")
    milliseconds = (int(text) >> 22) + DISCORD_EPOCH_MS
    return dt.datetime.fromtimestamp(milliseconds / 1000, tz=dt.timezone.utc)


def exact_dom_timestamp_scope(row: dict[str, Any], message_id: str) -> bool:
    """Require both the collector boolean and the exact owned ARIA token."""

    if row.get("timestamp_scope_exact") is not True:
        return False
    tokens = str(row.get("article_aria_labelledby") or "").split()
    return f"message-timestamp-{message_id}" in tokens


def exact_stage_system_event_timestamp_fallback(
    row: dict[str, Any], message_id: str
) -> bool:
    if not DISCORD_ID_RE.fullmatch(message_id):
        return False
    if str(row.get("collection_channel_kind") or "") != "stage channel":
        return False
    if str(row.get("author") or "").strip() or str(
        row.get("author_id") or ""
    ).strip():
        return False
    if (
        row.get("content_scope_exact") is not True
        or row.get("timestamp_scope_exact") is not False
    ):
        return False
    labelled_by = str(row.get("article_aria_labelledby") or "").strip()
    if labelled_by not in {
        f"message-content-{message_id}",
        f"message-content-{message_id} message-accessories-{message_id}",
    }:
        return False
    lines = [
        line.strip()
        for line in str(row.get("content_text") or "").splitlines()
        if line.strip()
    ]
    if len(lines) < 3 or not lines[0]:
        return False
    duplicated_stage_speaker_label = bool(
        len(lines) >= 4 and lines[0] and lines[0] == lines[1]
    )
    event_line = lines[2] if duplicated_stage_speaker_label else lines[1]
    stage_event = re.fullmatch(
        r"(?:(?:started|ended)\s+.+|is now a speaker\.)",
        event_line,
        flags=re.IGNORECASE,
    )
    poll_results_present = (
        any(re.match(r"The results?\b", line, flags=re.IGNORECASE) for line in lines)
        and any(re.fullmatch(r"\d+(?:\.\d+)?%", line) for line in lines)
    ) or any(
        re.fullmatch(
            r"Winning answer (?:•|â€¢) \d+(?:\.\d+)?%",
            line,
            flags=re.IGNORECASE,
        )
        for line in lines
    )
    poll_closed = bool(
        re.fullmatch(
            r".+(?:'|’|â€™)s poll .+ has closed\.",
            lines[0],
            flags=re.IGNORECASE,
        )
        and poll_results_present
    )
    if not stage_event and not poll_closed:
        return False
    if type(row.get("timestamp_discrepancy_ms")) is not int or row.get(
        "timestamp_discrepancy_ms"
    ) != 0:
        return False
    try:
        captured = parse_iso_utc(row.get("timestamp_utc"))
        declared_snowflake = parse_iso_utc(row.get("snowflake_timestamp_utc"))
        encoded = snowflake_time(message_id)
    except (TypeError, ValueError):
        return False
    return captured == declared_snowflake == encoded


def exact_pinned_message_system_event_timestamp_fallback(
    row: dict[str, Any], message_id: str
) -> bool:
    if not DISCORD_ID_RE.fullmatch(message_id):
        return False
    if str(row.get("collection_channel_kind") or "") != "text channel":
        return False
    if str(row.get("author") or "").strip() or str(
        row.get("author_id") or ""
    ).strip():
        return False
    if str(row.get("article_id") or "") != f"search-result-{message_id}":
        return False
    if (
        row.get("content_scope_exact") is not True
        or row.get("timestamp_scope_exact") is not False
    ):
        return False
    if (
        str(row.get("article_aria_labelledby") or "").strip()
        != f"message-content-{message_id}"
    ):
        return False
    if row.get("discord_system_event_exact") is not True:
        return False
    if row.get("discord_system_event_type") != "message_pinned":
        return False
    if row.get("timestamp_exact_fallback_source") != PIN_FALLBACK_SOURCE:
        return False
    lines = [
        line.strip()
        for line in str(row.get("content_text") or "").splitlines()
        if line.strip()
    ]
    if not (
        len(lines) == 5
        and 1 <= len(lines[0]) <= 80
        and lines[1]
        == "pinned a message to this channel. See all pinned messages."
        and lines[2] == "—"
        and re.fullmatch(
            r"\d{1,2}/\d{1,2}/\d{2}, \d{1,2}:\d{2} (?:AM|PM)", lines[3]
        )
        and re.fullmatch(
            r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), "
            r"(?:January|February|March|April|May|June|July|August|September|October|November|December) "
            r"\d{1,2}, \d{4} at \d{1,2}:\d{2} (?:AM|PM)",
            lines[4],
        )
    ):
        return False
    if (
        type(row.get("row_owned_time_count")) is not int
        or row.get("row_owned_time_count") != 1
    ):
        return False
    if str(row.get("row_owned_time_element_id") or "").strip():
        return False
    if type(row.get("timestamp_discrepancy_ms")) is not int or row.get(
        "timestamp_discrepancy_ms"
    ) != 0:
        return False
    try:
        captured = parse_iso_utc(row.get("timestamp_utc"))
        owned = parse_iso_utc(row.get("row_owned_time_datetime"))
        declared_snowflake = parse_iso_utc(row.get("snowflake_timestamp_utc"))
        encoded = snowflake_time(message_id)
    except (TypeError, ValueError):
        return False
    return captured == owned == declared_snowflake == encoded


def exact_discord_system_event_timestamp_fallback(
    row: dict[str, Any], message_id: str
) -> bool:
    return exact_stage_system_event_timestamp_fallback(
        row, message_id
    ) or exact_pinned_message_system_event_timestamp_fallback(row, message_id)


def timestamp_scope_revalidation_sidecar_path(segment_path: Path) -> Path:
    return segment_path.with_name(
        f"{segment_path.stem}{TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX}"
    )


def _portable_relative_path(value: Any, *, field_name: str) -> PurePosixPath:
    text = str(value or "").strip().replace("\\", "/")
    candidate = PurePosixPath(text)
    if (
        not text
        or candidate.is_absolute()
        or re.match(r"^[A-Za-z]:/", text)
        or any(part in {"", ".", ".."} or ":" in part for part in candidate.parts)
    ):
        raise ValueError(f"{field_name}_not_portable_relative_path")
    return candidate


def _resolve_bound_path(
    value: Any, *, artifact_root: Path, field_name: str
) -> tuple[Path, str]:
    relative = _portable_relative_path(value, field_name=field_name)
    root = artifact_root.resolve()
    resolved = (root / Path(*relative.parts)).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{field_name}_escapes_artifact_root") from exc
    return resolved, relative.as_posix()


def _stable_read_json(path: Path) -> tuple[dict[str, Any], str, int]:
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError(f"artifact_changed_while_reading:{path.name}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid_utf8_json:{path.name}:{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"top_level_not_object:{path.name}")
    return payload, sha256_bytes(raw), len(raw)


def _expected_effective_correction(timestamp_utc: str) -> dict[str, Any]:
    return {
        "timestamp_scope_exact": False,
        "row_owned_time_count": 1,
        "row_owned_time_datetime": timestamp_utc,
        "row_owned_time_element_id": None,
        "discord_system_event_exact": True,
        "discord_system_event_type": "message_pinned",
        "timestamp_exact_fallback_source": PIN_FALLBACK_SOURCE,
    }


@dataclass
class SegmentTimestampScopeRevalidation:
    segment_path: Path
    artifact_root: Path
    source_artifact_sha256: str
    provided: bool = False
    sidecar_path: Path | None = None
    sidecar_sha256: str | None = None
    sidecar_size_bytes: int | None = None
    proofs: dict[str, dict[str, Any]] = field(default_factory=dict)
    evidence_artifacts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    used_message_ids: set[str] = field(default_factory=set)
    forum_system_event_revalidation: Any = None

    def proof_for(self, row: dict[str, Any]) -> dict[str, Any] | None:
        message_id = str(row.get("message_id") or "")
        proof = self.proofs.get(message_id)
        if proof is None:
            return None
        if proof.get("source_row_sha256") != row_sha256(row):
            return None
        self.used_message_ids.add(message_id)
        return proof

    def unused_message_ids(self) -> list[str]:
        return sorted(set(self.proofs) - self.used_message_ids, key=int)

    def source_artifacts(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if self.sidecar_path is not None and self.sidecar_path.is_file():
            rows.append(
                {
                    "path": self.sidecar_path,
                    "kind": "timestamp_scope_revalidation_sidecar",
                    "sha256": self.sidecar_sha256,
                }
            )
        rows.extend(copy.deepcopy(self.evidence_artifacts))
        if self.forum_system_event_revalidation is not None:
            rows.extend(self.forum_system_event_revalidation.source_artifacts())
        return rows

    def summary(self) -> dict[str, Any]:
        try:
            segment_relative = self.segment_path.resolve().relative_to(
                self.artifact_root.resolve()
            ).as_posix()
        except ValueError:
            segment_relative = self.segment_path.name
        sidecar_relative: str | None = None
        if self.sidecar_path is not None:
            try:
                sidecar_relative = self.sidecar_path.resolve().relative_to(
                    self.artifact_root.resolve()
                ).as_posix()
            except ValueError:
                sidecar_relative = self.sidecar_path.name
        unused = self.unused_message_ids()
        return {
            "schema_version": SCHEMA_VERSION,
            "provided": self.provided,
            "valid": self.provided and not self.errors and not unused,
            "segment_path": segment_relative,
            "source_artifact_sha256": self.source_artifact_sha256,
            "sidecar_path": sidecar_relative,
            "sidecar_sha256": self.sidecar_sha256,
            "sidecar_size_bytes": self.sidecar_size_bytes,
            "record_count": len(self.proofs),
            "used_record_count": len(self.used_message_ids),
            "unused_record_count": len(unused),
            "unused_message_ids": unused,
            "message_ids": sorted(self.proofs, key=int),
            "content_hash_bound": bool(
                self.provided
                and self.sidecar_sha256
                and not self.errors
                and not unused
                and all(row.get("source_row_sha256") for row in self.proofs.values())
            ),
            "errors": sorted(set(self.errors)),
        }


def _validate_recovery_note(
    *,
    note: dict[str, Any],
    row: dict[str, Any],
    message_id: str,
    correction: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    actor = next(
        (
            line.strip()
            for line in str(row.get("content_text") or "").splitlines()
            if line.strip()
        ),
        "",
    )
    expected_event_text = (
        f"{actor} pinned a message to this channel. See all pinned messages."
    )
    exact_pairs = {
        "message_id": message_id,
        "result_index": row.get("result_index"),
        "search_page": row.get("page_number"),
        "search_query": row.get("search_query"),
        "event_text": expected_event_text,
    }
    for name, expected in exact_pairs.items():
        if note.get(name) != expected:
            errors.append(f"recovery_{name}_mismatch")

    dom = note.get("dom_evidence")
    if not isinstance(dom, dict):
        errors.append("recovery_dom_evidence_missing")
        dom = {}
    expected_dom = {
        "article_id": f"search-result-{message_id}",
        "article_data_list_item_id": f"NO_LIST___{message_id}",
        "owning_listitem_id": row.get("result_listitem_id"),
        "owning_result_index": row.get("result_index"),
        "owning_result_set_size": row.get("result_set_size"),
        "row_owned_time_count": correction["row_owned_time_count"],
        "row_owned_time_datetime": correction["row_owned_time_datetime"],
        "row_owned_time_element_id": correction["row_owned_time_element_id"],
        "discord_pin_icon_present": True,
    }
    for name, expected in expected_dom.items():
        if dom.get(name) != expected:
            errors.append(f"recovery_dom_{name}_mismatch")

    reconciliation = note.get("timestamp_reconciliation")
    if not isinstance(reconciliation, dict):
        errors.append("recovery_timestamp_reconciliation_missing")
        reconciliation = {}
    expected_reconciliation = {
        "timestamp_utc": row.get("timestamp_utc"),
        "snowflake_timestamp_utc": row.get("snowflake_timestamp_utc"),
        "timestamp_discrepancy_ms": 0,
        # This preserved note records the historical manual correction.  It is
        # evidence, not authority; the sidecar explicitly corrects its effective
        # semantic value to False before applying the fallback predicate.
        "timestamp_scope_exact": True,
    }
    for name, expected in expected_reconciliation.items():
        if reconciliation.get(name) != expected:
            errors.append(f"recovery_timestamp_{name}_mismatch")
    return errors


def load_adjacent_timestamp_scope_revalidation(
    segment_path: Path,
    payload: dict[str, Any],
    *,
    source_artifact_sha256: str,
    artifact_root: Path,
) -> SegmentTimestampScopeRevalidation:
    segment_path = segment_path.resolve()
    artifact_root = artifact_root.resolve()
    bundle = SegmentTimestampScopeRevalidation(
        segment_path=segment_path,
        artifact_root=artifact_root,
        source_artifact_sha256=source_artifact_sha256.lower(),
    )
    def attach_forum_revalidation() -> SegmentTimestampScopeRevalidation:
        bundle.forum_system_event_revalidation = (
            premium_journals_system_event_timestamp_v1.load_adjacent_forum_system_event_revalidation(
                segment_path, payload,
                source_artifact_sha256=source_artifact_sha256,
                artifact_root=artifact_root,
            )
        )
        return bundle
    sidecar_path = timestamp_scope_revalidation_sidecar_path(segment_path)
    if not sidecar_path.is_file():
        return attach_forum_revalidation()
    bundle.provided = True
    bundle.sidecar_path = sidecar_path
    try:
        sidecar, observed_sidecar_sha, sidecar_size = _stable_read_json(sidecar_path)
    except (OSError, ValueError) as exc:
        bundle.errors.append(f"sidecar_unreadable:{exc}")
        return bundle
    bundle.sidecar_sha256 = observed_sidecar_sha
    bundle.sidecar_size_bytes = sidecar_size

    if sidecar.get("schema_version") != SCHEMA_VERSION:
        bundle.errors.append("sidecar_schema_version_mismatch")
    if sidecar.get("artifact_type") != ARTIFACT_TYPE:
        bundle.errors.append("sidecar_artifact_type_mismatch")
    if sidecar.get("source_scope") != "discord_only":
        bundle.errors.append("sidecar_source_scope_not_discord_only")
    if sidecar.get("outside_sources_used") is not False:
        bundle.errors.append("sidecar_outside_sources_used_not_false")

    try:
        expected_source_relative = segment_path.relative_to(artifact_root).as_posix()
    except ValueError:
        bundle.errors.append("segment_outside_artifact_root")
        expected_source_relative = ""
    if sidecar.get("source_artifact_path") != expected_source_relative:
        bundle.errors.append("sidecar_source_artifact_path_mismatch")
    declared_source_sha = str(sidecar.get("source_artifact_sha256") or "").lower()
    if not SHA256_RE.fullmatch(declared_source_sha):
        bundle.errors.append("sidecar_source_artifact_sha256_invalid")
    elif declared_source_sha != source_artifact_sha256.lower():
        bundle.errors.append("sidecar_source_artifact_sha256_mismatch")
    if sidecar.get("source_artifact_bytes") != segment_path.stat().st_size:
        bundle.errors.append("sidecar_source_artifact_bytes_mismatch")

    raw_records = sidecar.get("revalidations")
    if not isinstance(raw_records, list) or not raw_records:
        bundle.errors.append("sidecar_revalidations_missing")
        return attach_forum_revalidation()
    messages = payload.get("messages")
    messages = messages if isinstance(messages, list) else []

    for record_index, raw_record in enumerate(raw_records, start=1):
        prefix = f"record_{record_index}"
        if not isinstance(raw_record, dict):
            bundle.errors.append(f"{prefix}_not_object")
            continue
        message_id = str(raw_record.get("message_id") or "")
        if not DISCORD_ID_RE.fullmatch(message_id):
            bundle.errors.append(f"{prefix}_message_id_invalid")
            continue
        if message_id in bundle.proofs:
            bundle.errors.append(f"{prefix}_duplicate_message_id")
            continue
        matching_rows = [
            row
            for row in messages
            if isinstance(row, dict)
            and str(row.get("message_id") or "") == message_id
        ]
        if len(matching_rows) != 1:
            bundle.errors.append(f"{prefix}_source_message_row_count_not_one")
            continue
        row = matching_rows[0]
        if raw_record.get("result_index") != row.get("result_index"):
            bundle.errors.append(f"{prefix}_result_index_mismatch")
        declared_row_sha = str(raw_record.get("source_row_sha256") or "").lower()
        observed_row_sha = row_sha256(row)
        if not SHA256_RE.fullmatch(declared_row_sha):
            bundle.errors.append(f"{prefix}_source_row_sha256_invalid")
        elif declared_row_sha != observed_row_sha:
            bundle.errors.append(f"{prefix}_source_row_sha256_mismatch")

        timestamp_utc = str(row.get("timestamp_utc") or "")
        correction = raw_record.get("effective_correction")
        expected_correction = _expected_effective_correction(timestamp_utc)
        if correction != expected_correction:
            bundle.errors.append(f"{prefix}_effective_correction_mismatch")
            correction = expected_correction

        if raw_record.get("status") != "passed":
            bundle.errors.append(f"{prefix}_status_not_passed")
        if raw_record.get("evidence_type") != (
            "discord_pinned_message_system_event_sole_row_owned_time"
        ):
            bundle.errors.append(f"{prefix}_evidence_type_mismatch")

        recovery = raw_record.get("recovery_evidence")
        if not isinstance(recovery, dict):
            bundle.errors.append(f"{prefix}_recovery_evidence_missing")
            continue
        try:
            recovery_path, recovery_relative = _resolve_bound_path(
                recovery.get("path"),
                artifact_root=artifact_root,
                field_name="recovery_evidence_path",
            )
        except ValueError as exc:
            bundle.errors.append(f"{prefix}_{exc}")
            continue
        if not recovery_path.is_file():
            bundle.errors.append(f"{prefix}_recovery_evidence_missing_file")
            continue
        try:
            recovery_note, recovery_sha, recovery_size = _stable_read_json(
                recovery_path
            )
        except (OSError, ValueError) as exc:
            bundle.errors.append(f"{prefix}_recovery_evidence_unreadable:{exc}")
            continue
        declared_recovery_sha = str(recovery.get("sha256") or "").lower()
        if not SHA256_RE.fullmatch(declared_recovery_sha):
            bundle.errors.append(f"{prefix}_recovery_evidence_sha256_invalid")
        elif declared_recovery_sha != recovery_sha:
            bundle.errors.append(f"{prefix}_recovery_evidence_sha256_mismatch")
        if recovery.get("bytes") != recovery_size:
            bundle.errors.append(f"{prefix}_recovery_evidence_bytes_mismatch")
        bundle.evidence_artifacts.append(
            {
                "path": recovery_path,
                "relative_path": recovery_relative,
                "kind": "timestamp_scope_recovery_dom_evidence",
                "sha256": recovery_sha,
                "size_bytes": recovery_size,
            }
        )
        bundle.errors.extend(
            f"{prefix}_{error}"
            for error in _validate_recovery_note(
                note=recovery_note,
                row=row,
                message_id=message_id,
                correction=correction,
            )
        )

        effective_row = copy.deepcopy(row)
        effective_row.update(correction)
        if not exact_pinned_message_system_event_timestamp_fallback(
            effective_row, message_id
        ):
            bundle.errors.append(f"{prefix}_effective_pinned_fallback_not_exact")
        bundle.proofs[message_id] = {
            "message_id": message_id,
            "result_index": row.get("result_index"),
            "source_row_sha256": observed_row_sha,
            "effective_correction": copy.deepcopy(correction),
            "recovery_evidence_path": recovery_relative,
            "recovery_evidence_sha256": recovery_sha,
        }
    return attach_forum_revalidation()


def timestamp_scope_mode(
    row: dict[str, Any],
    bundle: SegmentTimestampScopeRevalidation | None = None,
) -> str | None:
    message_id = str(row.get("message_id") or "")
    if exact_dom_timestamp_scope(row, message_id):
        return "message_timestamp_aria_exact"
    if exact_stage_system_event_timestamp_fallback(row, message_id):
        return "discord_snowflake_exact_stage_system_event"
    if exact_pinned_message_system_event_timestamp_fallback(row, message_id):
        return PIN_FALLBACK_SOURCE
    proof = bundle.proof_for(row) if bundle is not None else None
    if proof is not None:
        effective_row = copy.deepcopy(row)
        effective_row.update(proof["effective_correction"])
        if exact_pinned_message_system_event_timestamp_fallback(
            effective_row, message_id
        ):
            return f"{PIN_FALLBACK_SOURCE}_sidecar_revalidated"
    if bundle is not None:
        forum_mode = premium_journals_system_event_timestamp_v1.timestamp_scope_mode(
            row, bundle.forum_system_event_revalidation
        )
        if forum_mode is not None:
            return forum_mode
    return None


def audit_segment_timestamp_scopes(
    rows: Iterable[dict[str, Any]],
    bundle: SegmentTimestampScopeRevalidation | None = None,
) -> dict[str, Any]:
    mode_counts: Counter[str] = Counter()
    unresolved: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        mode = timestamp_scope_mode(row, bundle)
        if mode is None:
            unresolved.append(
                {
                    "row_index": row_index,
                    "message_id": str(row.get("message_id") or ""),
                    "timestamp_scope_exact": row.get("timestamp_scope_exact"),
                    "article_aria_labelledby": row.get(
                        "article_aria_labelledby"
                    ),
                }
            )
        else:
            mode_counts[mode] += 1
    bundle_errors = sorted(set(bundle.errors)) if bundle is not None else []
    unused = bundle.unused_message_ids() if bundle is not None else []
    if bundle is not None and bundle.forum_system_event_revalidation is not None:
        forum_bundle = bundle.forum_system_event_revalidation
        bundle_errors.extend(f"forum_system_event:{item}" for item in forum_bundle.errors)
        unused.extend(forum_bundle.unused_message_ids())
    bundle_errors = sorted(set(bundle_errors))
    unused = sorted(set(unused), key=int)
    return {
        "passed": not unresolved and not bundle_errors and not unused,
        "message_count": sum(mode_counts.values()) + len(unresolved),
        "mode_counts": dict(sorted(mode_counts.items())),
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "sidecar_error_count": len(bundle_errors),
        "sidecar_errors": bundle_errors,
        "unused_revalidation_record_count": len(unused),
        "unused_revalidation_message_ids": unused,
        "sidecar": bundle.summary() if bundle is not None else None,
    }


def release_timestamp_scope_integrity_errors(
    payload: dict[str, Any],
) -> list[str]:
    """Validate the normalized release gate and its registered hash bindings."""

    errors: list[str] = []
    summary = payload.get("timestamp_scope_integrity")
    if not isinstance(summary, dict):
        return ["timestamp_scope_integrity_missing"]
    if summary.get("schema_version") != SCHEMA_VERSION:
        errors.append("timestamp_scope_integrity_schema_mismatch")
    if summary.get("passed") is not True:
        errors.append("timestamp_scope_integrity_not_passed")
    if summary.get("content_hash_bound") is not True:
        errors.append("timestamp_scope_integrity_not_content_hash_bound")
    for field_name in (
        "unresolved_message_count",
        "invalid_sidecar_count",
        "unused_revalidation_record_count",
    ):
        if type(summary.get(field_name)) is not int or summary.get(field_name) != 0:
            errors.append(f"timestamp_scope_integrity_{field_name}_not_zero")
    external_count = summary.get("external_revalidation_message_count")
    used_count = summary.get("external_revalidation_used_record_count")
    if (
        type(external_count) is not int
        or type(used_count) is not int
        or external_count < 0
        or external_count != used_count
    ):
        errors.append("timestamp_scope_integrity_external_record_count_mismatch")

    gates = payload.get("release_gates")
    matching_gates = [
        row
        for row in (gates if isinstance(gates, list) else [])
        if isinstance(row, dict) and row.get("gate") == "timestamp_scope_integrity"
    ]
    if len(matching_gates) != 1:
        errors.append("timestamp_scope_integrity_release_gate_count_not_one")
    else:
        gate = matching_gates[0]
        if gate.get("passed") is not True:
            errors.append("timestamp_scope_integrity_release_gate_failed")
        if gate.get("detail") != summary:
            errors.append("timestamp_scope_integrity_release_gate_detail_mismatch")

    source_files = payload.get("source_files")
    source_files = source_files if isinstance(source_files, list) else []
    source_by_id = {
        str(row.get("source_file_id") or ""): row
        for row in source_files
        if isinstance(row, dict) and str(row.get("source_file_id") or "")
    }
    sidecars = summary.get("sidecars")
    if not isinstance(sidecars, list):
        errors.append("timestamp_scope_integrity_sidecars_not_array")
        sidecars = []
    if summary.get("sidecar_count") != len(sidecars):
        errors.append("timestamp_scope_integrity_sidecar_count_mismatch")
    if bool(sidecars) != bool(external_count):
        errors.append("timestamp_scope_integrity_sidecar_external_presence_mismatch")
    for index, sidecar in enumerate(sidecars, start=1):
        prefix = f"timestamp_scope_sidecar_{index}"
        if not isinstance(sidecar, dict):
            errors.append(f"{prefix}_not_object")
            continue
        if sidecar.get("valid") is not True or sidecar.get(
            "content_hash_bound"
        ) is not True:
            errors.append(f"{prefix}_not_valid_and_hash_bound")
        if (
            type(sidecar.get("record_count")) is not int
            or sidecar.get("record_count") < 1
            or sidecar.get("used_record_count") != sidecar.get("record_count")
            or sidecar.get("unused_record_count") != 0
        ):
            errors.append(f"{prefix}_record_counts_invalid")
        sidecar_sha = str(sidecar.get("sidecar_sha256") or "").lower()
        source_sha = str(sidecar.get("source_artifact_sha256") or "").lower()
        if not SHA256_RE.fullmatch(sidecar_sha):
            errors.append(f"{prefix}_sha256_invalid")
        if not SHA256_RE.fullmatch(source_sha):
            errors.append(f"{prefix}_source_artifact_sha256_invalid")
        sidecar_path = str(sidecar.get("sidecar_path") or "").replace("\\", "/")
        segment_path = str(sidecar.get("segment_path") or "").replace("\\", "/")
        if not sidecar_path.endswith(TIMESTAMP_SCOPE_REVALIDATION_SIDECAR_SUFFIX):
            errors.append(f"{prefix}_path_suffix_invalid")
        if not segment_path or not sidecar_path.startswith(
            segment_path[: -len(".json")] if segment_path.endswith(".json") else "\0"
        ):
            errors.append(f"{prefix}_segment_sidecar_path_binding_invalid")

        source_file_id = str(sidecar.get("source_file_id") or "")
        sidecar_source = source_by_id.get(source_file_id)
        if not isinstance(sidecar_source, dict):
            errors.append(f"{prefix}_registered_sidecar_source_missing")
        elif (
            sidecar_source.get("kind") != "timestamp_scope_revalidation_sidecar"
            or str(sidecar_source.get("sha256") or "").lower() != sidecar_sha
            or not str(sidecar_source.get("relative_path") or "")
            .replace("\\", "/")
            .endswith(sidecar_path)
        ):
            errors.append(f"{prefix}_registered_sidecar_source_mismatch")

        segment_sources = [
            row
            for row in source_files
            if isinstance(row, dict)
            and str(row.get("relative_path") or "")
            .replace("\\", "/")
            .endswith(segment_path)
            and str(row.get("sha256") or "").lower() == source_sha
            and str(row.get("kind") or "").endswith("_segment")
        ]
        if len(segment_sources) != 1:
            errors.append(f"{prefix}_registered_segment_source_count_not_one")

        evidence_ids = sidecar.get("evidence_source_file_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            errors.append(f"{prefix}_evidence_source_file_ids_missing")
            evidence_ids = []
        for evidence_id in evidence_ids:
            evidence = source_by_id.get(str(evidence_id or ""))
            if not isinstance(evidence, dict):
                errors.append(f"{prefix}_registered_evidence_source_missing")
            elif (
                evidence.get("kind")
                != "timestamp_scope_recovery_dom_evidence"
                or not SHA256_RE.fullmatch(
                    str(evidence.get("sha256") or "").lower()
                )
            ):
                errors.append(f"{prefix}_registered_evidence_source_invalid")
    return sorted(set(errors))
