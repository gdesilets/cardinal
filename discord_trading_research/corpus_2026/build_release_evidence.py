#!/usr/bin/env python3
"""Build fail-closed release evidence from local Discord artifacts.

The generator is intentionally read-only with respect to ``raw/``, corpus JSON,
SQLite databases, and legacy/staging data.  It writes one atomic copy of the
collection progress manifest under ``working/``.  The copy contains:

* a machine-generated ``release_evidence`` object consumed by
  :mod:`relevance_release_policy`; and
* deterministic ``release_review_packets`` for any supplemental residual census jobs.

No Browser or network API is used.  Count observations are never inferred.  A
residual review is required only when the plan actually contains supplemental
residual-census jobs; the literal full-capture plan has none.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

import collection_orchestrator as orchestrator


HERE = Path(__file__).resolve().parent
DEFAULT_PLAN = HERE / "relevance_collection_plan.json"
DEFAULT_PROGRESS = HERE / "working" / "collection_progress_manifest.json"
DEFAULT_CORPUS_MANIFEST = HERE / "working" / "corpus_partial_manifest.json"
DEFAULT_CORPUS_DATA = HERE / "working" / "corpus_partial.json"
DEFAULT_DATABASE = HERE / "working" / "cardinal_partial.sqlite"
DEFAULT_COUNT_OBSERVATIONS = HERE / "working" / "collection_orchestrator_state.json"
DEFAULT_OUTPUT = HERE / "working" / "collection_progress_with_release_evidence.json"

DISCORD_ID_RE = re.compile(r"^\d{15,22}$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ATTACHMENT_PATH_RE = re.compile(r"/attachments/(\d{15,22})/(\d{15,22})/")
VISUAL_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".mp4", ".webm"}
VALID_QUESTION_STATUSES = {"answered", "partial", "conflicting", "unanswered", "ambiguous"}
VALID_REVIEW_DECISIONS = {"relevant", "not_relevant", "ambiguous"}
TRUSTED_OCCURRENCE_STATES = {"trusted_canonical", "trusted_source"}
PROBABILITY_WORD_RE = re.compile(
    r"\b(?:probab(?:ility|ilities)|win\s*rate|success\s*rate|hit\s*rate|"
    r"expectancy|odds|high[- ]probability|low[- ]probability|chance\s+of\s+(?:winning|success))\b",
    re.I,
)
PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%")
PERCENT_CONTEXT_RE = re.compile(r"\b(?:win|winning|success|hit|probability|chance|rate)\b", re.I)
CALIBRATION_GUARD_RE = re.compile(
    r"(?:not\s+(?:a\s+)?(?:calibrated\s+)?(?:probability|causal|expectancy)|"
    r"non[- ]causal|uncalibrated|insufficient\s+evidence|self[- ]reported|"
    r"as\s+stated|unverified|descriptive|selected[- ]corpus|sample[- ]bound)",
    re.I,
)


class ReleaseEvidenceError(RuntimeError):
    """Raised when release evidence cannot be generated safely."""


@dataclass(frozen=True)
class HashedArtifact:
    path: Path
    kind: str
    sha256: str
    size_bytes: int
    display_path: str

    def ref(self, fragment: str | None = None) -> str:
        suffix = f"#{fragment}" if fragment else ""
        return f"sha256:{self.sha256}::{self.display_path}{suffix}"

    def serializable(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.display_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class RawArtifact:
    artifact: HashedArtifact
    relative_path: str
    inspected: orchestrator.Artifact
    manifest_errors: tuple[str, ...]

    @property
    def usable_complete(self) -> bool:
        return self.inspected.state == "complete" and not self.manifest_errors


@dataclass
class DatabaseContext:
    artifact: HashedArtifact
    connection: sqlite3.Connection
    tables: set[str]
    columns: dict[str, set[str]]
    prerequisite_errors: list[str]
    evidence_refs: list[str]
    analysis_run_count: int
    built_at_utc: str | None

    @property
    def ready(self) -> bool:
        return not self.prerequisite_errors


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_utc(value: Any) -> dt.datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def format_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def hash_artifact(path: Path, kind: str, root: Path) -> HashedArtifact:
    path = path.resolve()
    if not path.is_file():
        raise ReleaseEvidenceError(f"Missing {kind} artifact: {path}")
    before = path.stat()
    digest = sha256_file(path)
    after = path.stat()
    identity_before = (before.st_size, before.st_mtime_ns, getattr(before, "st_ino", None))
    identity_after = (after.st_size, after.st_mtime_ns, getattr(after, "st_ino", None))
    if identity_before != identity_after:
        raise ReleaseEvidenceError(f"{kind} changed while it was being hashed: {path}")
    return HashedArtifact(
        path=path,
        kind=kind,
        sha256=digest,
        size_bytes=after.st_size,
        display_path=display_path(path, root),
    )


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ReleaseEvidenceError(f"Could not read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseEvidenceError(f"{label} must contain a top-level JSON object: {path}")
    return value


def atomic_write_json(path: Path, payload: dict[str, Any], *, overwrite: bool) -> None:
    path = path.resolve()
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {path}. Use --overwrite explicitly.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.next-{os.getpid()}")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def require_working_output(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    working = (root / "working").resolve()
    try:
        resolved.relative_to(working)
    except ValueError as exc:
        raise ReleaseEvidenceError(
            f"Output must stay under the disposable working directory {working}: {resolved}"
        ) from exc
    if resolved.suffix.casefold() != ".json":
        raise ReleaseEvidenceError("Release-evidence output must be a JSON file")
    return resolved


def query_core(value: Any) -> str:
    return orchestrator.query_core(str(value or ""))


def job_complete(job: dict[str, Any]) -> bool:
    if str(job.get("status") or "") not in {"complete", "superseded"}:
        return False
    segments = [row for row in job.get("segments", []) if isinstance(row, dict)]
    return bool(segments) and all(str(row.get("status") or "") in {"complete", "superseded"} for row in segments)


def within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def load_raw_artifacts(
    root: Path, progress: dict[str, Any]
) -> tuple[dict[str, RawArtifact], list[HashedArtifact], list[str]]:
    raw_root = (root / "raw").resolve()
    output: dict[str, RawArtifact] = {}
    hashed: list[HashedArtifact] = []
    errors: list[str] = []
    rows = progress.get("artifacts")
    if not isinstance(rows, list):
        return {}, [], ["progress_artifacts_missing"]
    for row in rows:
        if not isinstance(row, dict):
            errors.append("progress_artifact_not_object")
            continue
        relative = str(row.get("relative_path") or "").replace("\\", "/")
        if not relative.startswith("raw/"):
            continue
        path = (root / relative).resolve()
        if not within(path, raw_root):
            errors.append(f"raw_artifact_path_escape:{relative}")
            continue
        if not path.is_file():
            errors.append(f"raw_artifact_missing:{relative}")
            continue
        before = path.stat()
        inspected = orchestrator.inspect_artifact(path, root.resolve())
        artifact = hash_artifact(path, "discord_raw_segment", root)
        after = path.stat()
        manifest_errors: list[str] = []
        if (
            before.st_size,
            before.st_mtime_ns,
            getattr(before, "st_ino", None),
        ) != (
            after.st_size,
            after.st_mtime_ns,
            getattr(after, "st_ino", None),
        ):
            manifest_errors.append("raw_artifact_changed_between_parse_and_hash")
        comparisons = {
            "file_state": inspected.state,
            "size_bytes": inspected.size_bytes,
            "channel_id": inspected.channel_id,
            "segment_start": inspected.start.isoformat() if inspected.start else None,
            "segment_end": inspected.end.isoformat() if inspected.end else None,
            "reported_messages": inspected.reported_total,
            "captured_messages": inspected.captured_rows,
            "unique_message_ids": inspected.unique_message_ids,
        }
        for field, actual in comparisons.items():
            if field in row and row.get(field) != actual:
                manifest_errors.append(f"progress_metadata_mismatch:{field}")
        output[relative] = RawArtifact(
            artifact=artifact,
            relative_path=relative,
            inspected=inspected,
            manifest_errors=tuple(sorted(manifest_errors)),
        )
        hashed.append(artifact)
        errors.extend(f"{relative}:{item}" for item in manifest_errors)
    return output, hashed, sorted(errors)


def corpus_segments(corpus_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    value = corpus_manifest.get("segments")
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    coverage = corpus_manifest.get("coverage")
    if isinstance(coverage, dict) and isinstance(coverage.get("segments"), list):
        return [row for row in coverage["segments"] if isinstance(row, dict)]
    return []


def corpus_cutoff(corpus_manifest: dict[str, Any]) -> dt.datetime | None:
    release = corpus_manifest.get("release") if isinstance(corpus_manifest.get("release"), dict) else {}
    return parse_utc(
        release.get("data_cutoff_utc")
        or corpus_manifest.get("data_cutoff_utc")
        or corpus_manifest.get("generated_at_utc")
    )


def raw_segment_match(
    segment: dict[str, Any], raw_by_relative: dict[str, RawArtifact]
) -> RawArtifact | None:
    expected_hash = str(segment.get("source_file_sha256") or "").upper()
    expected_path = str(segment.get("source_file_relative_path") or "").replace("\\", "/")
    candidates = [
        raw
        for raw in raw_by_relative.values()
        if expected_hash and raw.artifact.sha256 == expected_hash
    ]
    if expected_path:
        suffix = expected_path.split("/raw/", 1)[-1]
        candidates = [raw for raw in candidates if raw.relative_path.endswith(f"raw/{suffix}")]
    if len(candidates) != 1:
        return None
    raw = candidates[0]
    inspected = raw.inspected
    if (
        str(segment.get("query_container_id") or "") != str(inspected.channel_id or "")
        or query_core(segment.get("query")) != inspected.query_core
        or str(segment.get("start_date") or "") != (inspected.start.isoformat() if inspected.start else "")
        or str(segment.get("end_date") or "") != (inspected.end.isoformat() if inspected.end else "")
        or int(segment.get("reported_total") or 0) != inspected.reported_total
    ):
        return None
    return raw


def interval_days(start: dt.date, end: dt.date) -> set[dt.date]:
    if end < start:
        return set()
    return {start + dt.timedelta(days=index) for index in range((end - start).days + 1)}


def validate_selected_cover(
    candidates: Sequence[dict[str, Any]], selected_ids: Sequence[str], start: dt.date, end: dt.date
) -> tuple[list[dict[str, Any]], list[str]]:
    by_id = {str(row.get("segment_id") or ""): row for row in candidates}
    selected: list[dict[str, Any]] = []
    errors: list[str] = []
    if len(set(selected_ids)) != len(selected_ids):
        errors.append("selected_segment_ids_duplicate")
    for segment_id in selected_ids:
        row = by_id.get(str(segment_id))
        if row is None:
            errors.append(f"selected_segment_id_unknown:{segment_id}")
        else:
            selected.append(row)
    counts: defaultdict[dt.date, int] = defaultdict(int)
    for row in selected:
        row_start = parse_date(row.get("start_date"))
        row_end = parse_date(row.get("end_date"))
        if not row_start or not row_end:
            errors.append(f"selected_segment_dates_invalid:{row.get('segment_id')}")
            continue
        for day in interval_days(row_start, row_end):
            counts[day] += 1
    expected = interval_days(start, end)
    if set(counts) != expected:
        errors.append("selected_segments_do_not_cover_full_window")
    if any(value != 1 for value in counts.values()):
        errors.append("selected_segments_overlap")
    return selected, sorted(set(errors))


def unique_exact_cover(
    candidates: Sequence[dict[str, Any]], start: dt.date, end: dt.date
) -> tuple[list[dict[str, Any]], list[str]]:
    usable: list[tuple[dt.date, dt.date, dict[str, Any]]] = []
    for row in candidates:
        row_start = parse_date(row.get("start_date"))
        row_end = parse_date(row.get("end_date"))
        if row_start and row_end and start <= row_start <= row_end <= end:
            usable.append((row_start, row_end, row))
    by_start: defaultdict[dt.date, list[tuple[dt.date, dict[str, Any]]]] = defaultdict(list)
    for row_start, row_end, row in usable:
        by_start[row_start].append((row_end, row))
    memo: dict[dt.date, list[list[dict[str, Any]]]] = {}

    def solve(cursor: dt.date) -> list[list[dict[str, Any]]]:
        if cursor > end:
            return [[]]
        if cursor in memo:
            return memo[cursor]
        solutions: list[list[dict[str, Any]]] = []
        ordered = sorted(
            by_start.get(cursor, []),
            key=lambda item: (
                -((item[0] - cursor).days),
                str(item[1].get("segment_id") or ""),
            ),
        )
        for row_end, row in ordered:
            for tail in solve(row_end + dt.timedelta(days=1)):
                solutions.append([row, *tail])
                if len(solutions) >= 2:
                    memo[cursor] = solutions[:2]
                    return memo[cursor]
        memo[cursor] = solutions
        return solutions

    solutions = solve(start)
    if not solutions:
        return [], ["no_gap_free_exact_segment_cover"]
    if len(solutions) > 1:
        return [], ["ambiguous_multiple_exact_segment_covers_require_segment_ids"]
    return solutions[0], []


def load_evidence_rows(
    paths: Sequence[Path],
    list_key: str,
    kind: str,
    root: Path,
    *,
    allowed_artifact_types: set[str] | None = None,
) -> tuple[list[tuple[dict[str, Any], HashedArtifact, int]], list[HashedArtifact]]:
    output: list[tuple[dict[str, Any], HashedArtifact, int]] = []
    artifacts: list[HashedArtifact] = []
    for path in paths:
        artifact = hash_artifact(path, kind, root)
        payload = read_json_object(path, kind)
        if allowed_artifact_types is not None and str(payload.get("artifact_type") or "") not in allowed_artifact_types:
            raise ReleaseEvidenceError(
                f"{kind} {path} has unsupported artifact_type {payload.get('artifact_type')!r}"
            )
        rows = payload.get(list_key)
        if not isinstance(rows, list):
            raise ReleaseEvidenceError(f"{kind} {path} must contain a {list_key} array")
        artifacts.append(artifact)
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ReleaseEvidenceError(f"{kind} {path} {list_key}[{index}] is not an object")
            output.append((row, artifact, index))
    return output, artifacts


def valid_count_observation(
    row: dict[str, Any], *, channel_id: str, core: str, start: dt.date, end: dt.date
) -> list[str]:
    errors: list[str] = []
    if str(row.get("source") or "") != "operator_recorded_countSearch":
        errors.append("count_source_not_operator_recorded_countSearch")
    if not str(row.get("observation_id") or "").strip():
        errors.append("observation_id_missing")
    if str(row.get("channel_id") or "") != channel_id:
        errors.append("count_channel_mismatch")
    if query_core(row.get("query")) != core:
        errors.append("count_query_core_mismatch")
    if parse_date(row.get("start")) != start or parse_date(row.get("end")) != end:
        errors.append("count_window_mismatch")
    try:
        total = int(row.get("reported_total"))
        pages = int(row.get("reported_pages"))
        if total < 0 or pages != (math.ceil(total / 25) if total else 0):
            errors.append("count_total_or_pages_invalid")
    except (TypeError, ValueError):
        errors.append("count_total_or_pages_missing")
    if parse_utc(row.get("observed_at_utc")) is None:
        errors.append("count_timestamp_missing_or_invalid")
    return errors


def build_count_reconciliation(
    *,
    plan: dict[str, Any],
    progress: dict[str, Any],
    raw_by_relative: dict[str, RawArtifact],
    corpus_manifest: dict[str, Any],
    observations: Sequence[tuple[dict[str, Any], HashedArtifact, int]],
    cutoff: dt.datetime,
) -> list[dict[str, Any]]:
    policies = {
        str(row.get("channel_id")): row
        for row in plan.get("channel_policies", [])
        if isinstance(row, dict)
    }
    segments = corpus_segments(corpus_manifest)
    jobs = [
        row for row in progress.get("jobs", [])
        if isinstance(row, dict) and row.get("job_kind") == "full_capture_or_empty_verification"
    ]
    jobs_by_channel: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        jobs_by_channel[str(job.get("channel_id") or "")].append(job)
    required_channel_ids = sorted(
        channel_id
        for channel_id, row in policies.items()
        if row.get("policy") in {"full_capture", "verified_empty_full_window"}
    )
    results: list[dict[str, Any]] = []
    for channel_id in required_channel_ids:
        channel_jobs = jobs_by_channel.get(channel_id, [])
        if len(channel_jobs) != 1:
            results.append(
                {
                    "channel_id": channel_id,
                    "status": "pending",
                    "segment_reported_total": None,
                    "refreshed_full_window_reported_total": None,
                    "observed_at_utc": None,
                    "segment_ids": [],
                    "observation_ids": [],
                    "evidence_refs": [],
                    "discord_edit_deletion_provenance_refs": [],
                    "excluded_nonzero_segment_ids": [],
                    "pending_reasons": [
                        "full_capture_job_missing" if not channel_jobs else "duplicate_full_capture_jobs"
                    ],
                }
            )
            continue
        job = channel_jobs[0]
        policy = str(policies.get(channel_id, {}).get("policy") or "")
        start = parse_date((job.get("window") or {}).get("start"))
        end = parse_date((job.get("window") or {}).get("end"))
        core = query_core(job.get("query_prefix") or job.get("query_core"))
        reasons: list[str] = []
        if policy not in {"full_capture", "verified_empty_full_window"}:
            reasons.append("channel_policy_not_full_or_empty")
        if not start or not end:
            reasons.append("job_window_missing_or_invalid")
        if not job_complete(job):
            reasons.append("full_capture_job_not_complete")
        candidates: list[dict[str, Any]] = []
        for segment in segments:
            if str(segment.get("query_container_id") or "") != channel_id:
                continue
            if query_core(segment.get("query")) != core:
                continue
            if segment.get("computed_complete") is not True:
                continue
            raw = raw_segment_match(segment, raw_by_relative)
            if raw is None or not raw.usable_complete:
                continue
            copied = copy.deepcopy(segment)
            copied["_raw"] = raw
            candidates.append(copied)
        matching_observations: list[tuple[dict[str, Any], HashedArtifact, int]] = []
        if start and end:
            for item in observations:
                if not valid_count_observation(
                    item[0], channel_id=channel_id, core=core, start=start, end=end
                ):
                    matching_observations.append(item)
        fresh = [item for item in matching_observations if parse_utc(item[0].get("observed_at_utc")) >= cutoff]
        selected_observation: tuple[dict[str, Any], HashedArtifact, int] | None = None
        if not fresh:
            reasons.append("fresh_full_window_count_observation_missing")
        else:
            latest_time = max(parse_utc(item[0].get("observed_at_utc")) for item in fresh)
            latest = [item for item in fresh if parse_utc(item[0].get("observed_at_utc")) == latest_time]
            signatures = {
                (
                    int(item[0].get("reported_total")),
                    tuple(str(value) for value in item[0].get("segment_ids", []) if str(value)),
                )
                for item in latest
            }
            if len(signatures) != 1:
                reasons.append("conflicting_latest_count_observations")
            else:
                selected_observation = sorted(latest, key=lambda item: (item[1].sha256, item[2]))[0]
        selected: list[dict[str, Any]] = []
        if start and end:
            requested_ids = (
                [str(value) for value in selected_observation[0].get("segment_ids", []) if str(value)]
                if selected_observation else []
            )
            if requested_ids:
                selected, cover_errors = validate_selected_cover(candidates, requested_ids, start, end)
            else:
                selected, cover_errors = unique_exact_cover(candidates, start, end)
            reasons.extend(cover_errors)
        selected_ids = [str(row.get("segment_id") or "") for row in selected]
        selected_total = sum(int(row.get("reported_total") or 0) for row in selected)
        excluded_nonzero = [
            str(row.get("segment_id") or "")
            for row in candidates
            if str(row.get("segment_id") or "") not in set(selected_ids)
            and int(row.get("reported_total") or 0) != 0
        ]
        deletion_refs: list[str] = []
        refreshed_total: int | None = None
        observed_at: str | None = None
        observation_id: str | None = None
        observation_ref: str | None = None
        if selected_observation:
            observation, observation_artifact, index = selected_observation
            refreshed_total = int(observation.get("reported_total"))
            observed_at = str(observation.get("observed_at_utc"))
            observation_id = str(observation.get("observation_id"))
            observation_ref = observation_artifact.ref(f"count_observations/{index}")
            deletion_rows = observation.get("discord_edit_deletion_provenance")
            if isinstance(deletion_rows, list) and deletion_rows:
                valid_deletions = all(
                    isinstance(item, dict)
                    and str(item.get("reason") or "").strip()
                    and isinstance(item.get("affected_segment_ids"), list)
                    for item in deletion_rows
                )
                if valid_deletions:
                    deletion_refs = [observation_artifact.ref(f"count_observations/{index}/discord_edit_deletion_provenance")]
            if refreshed_total != selected_total:
                reasons.append("segment_total_does_not_match_refreshed_count")
        if excluded_nonzero and not deletion_refs:
            reasons.append("excluded_nonzero_segments_without_edit_deletion_provenance")
        if policy == "verified_empty_full_window" and refreshed_total not in {None, 0}:
            reasons.append("verified_empty_channel_refreshed_count_nonzero")
        evidence_refs = sorted(
            {row["_raw"].artifact.ref() for row in selected}
            | ({observation_ref} if observation_ref else set())
        )
        if not evidence_refs:
            reasons.append("evidence_refs_missing")
        passed = not reasons
        results.append(
            {
                "channel_id": channel_id,
                "status": "passed" if passed else "pending",
                "segment_reported_total": selected_total if selected else None,
                "refreshed_full_window_reported_total": refreshed_total,
                "observed_at_utc": observed_at,
                "segment_ids": selected_ids,
                "observation_ids": [observation_id] if observation_id else [],
                "evidence_refs": evidence_refs,
                "discord_edit_deletion_provenance_refs": deletion_refs,
                "excluded_nonzero_segment_ids": sorted(excluded_nonzero),
                "pending_reasons": sorted(set(reasons)),
            }
        )
    return results


def evidence_paths(job: dict[str, Any], expected_prefix: str) -> list[str]:
    paths: set[str] = set()
    for segment in job.get("segments", []):
        if not isinstance(segment, dict) or str(segment.get("status") or "") not in {"complete", "superseded"}:
            continue
        for value in segment.get("evidence_artifacts", []) or []:
            relative = str(value).replace("\\", "/")
            if relative.startswith(expected_prefix):
                paths.add(relative)
    return sorted(paths)


def read_raw_messages(raw: RawArtifact) -> list[dict[str, Any]]:
    payload = read_json_object(raw.artifact.path, "Discord raw segment")
    rows = payload.get("messages")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def residual_row(message: dict[str, Any], raw: RawArtifact) -> dict[str, Any]:
    return {
        "message_id": str(message.get("message_id") or ""),
        "timestamp_utc": message.get("snowflake_timestamp_utc") or message.get("timestamp_utc"),
        "author": message.get("author"),
        "content_text": str(message.get("content_text") or ""),
        "reply_context": str(message.get("reply_context") or ""),
        "attachments": copy.deepcopy(message.get("attachments") or []),
        "exact_permalink": message.get("exact_permalink") or message.get("inferred_permalink"),
        "source_artifact_ref": raw.artifact.ref(),
        "result_index": message.get("result_index"),
        "page_number": message.get("page_number"),
    }


def build_residual_packets(
    progress: dict[str, Any],
    raw_by_relative: dict[str, RawArtifact],
    cutoff: dt.datetime,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    jobs = [row for row in progress.get("jobs", []) if isinstance(row, dict)]
    targeted_by_channel: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        if job.get("job_kind") == "targeted_search":
            targeted_by_channel[str(job.get("channel_id") or "")].append(job)
    expected_targeted_jobs_per_channel: int | None = None
    if isinstance(plan, dict):
        query_families = [
            row for row in plan.get("query_families", []) if isinstance(row, dict)
        ]
        expected_targeted_jobs_per_channel = sum(
            len([query for query in family.get("queries", []) if isinstance(query, dict)])
            for family in query_families
        )
    targeted_ids: dict[str, set[str]] = {}
    targeted_refs: dict[str, set[str]] = {}
    targeted_complete: dict[str, bool] = {}
    for channel_id, channel_jobs in targeted_by_channel.items():
        ids: set[str] = set()
        refs: set[str] = set()
        complete = bool(channel_jobs) and all(job_complete(job) for job in channel_jobs)
        if expected_targeted_jobs_per_channel is not None:
            complete = complete and len(channel_jobs) == expected_targeted_jobs_per_channel
        for job in channel_jobs:
            paths = evidence_paths(job, "raw/relevance_segments/")
            if not paths:
                complete = False
            for relative in paths:
                raw = raw_by_relative.get(relative)
                if raw is None or not raw.usable_complete:
                    complete = False
                    continue
                refs.add(raw.artifact.ref())
                ids.update(str(row.get("message_id") or "") for row in read_raw_messages(raw))
        targeted_ids[channel_id] = {value for value in ids if DISCORD_ID_RE.fullmatch(value)}
        targeted_refs[channel_id] = refs
        targeted_complete[channel_id] = complete

    packets: list[dict[str, Any]] = []
    observed_audit_jobs = sorted(
        (job for job in jobs if job.get("job_kind") == "residual_audit_census_day"),
        key=lambda row: str(row.get("job_id") or ""),
    )
    audit_jobs = observed_audit_jobs
    if isinstance(plan, dict):
        targeted_policies = [
            row
            for row in plan.get("channel_policies", [])
            if isinstance(row, dict)
            and row.get("policy") == "targeted_search_plus_residual_audit"
        ]
        expansion = plan.get("job_expansion") if isinstance(plan.get("job_expansion"), dict) else {}
        residual = expansion.get("residual_audit") if isinstance(expansion.get("residual_audit"), dict) else {}
        audit_dates = [str(value) for value in residual.get("audit_dates", []) if parse_date(value)]
        observed_by_id = {str(job.get("job_id") or ""): job for job in observed_audit_jobs}
        audit_jobs = []
        for policy_row in targeted_policies:
            channel_id = str(policy_row.get("channel_id") or "")
            for audit_date in audit_dates:
                job_id = f"audit__{channel_id}__{audit_date}"
                audit_jobs.append(
                    observed_by_id.get(job_id)
                    or {
                        "job_id": job_id,
                        "job_kind": "residual_audit_census_day",
                        "channel_id": channel_id,
                        "channel_name": policy_row.get("name"),
                        "window": {"start": audit_date, "end": audit_date},
                        "status": "pending",
                        "segments": [],
                    }
                )
        audit_jobs.sort(key=lambda row: str(row.get("job_id") or ""))
    for job in audit_jobs:
        channel_id = str(job.get("channel_id") or "")
        paths = evidence_paths(job, "raw/relevance_audit_segments/")
        audit_refs: set[str] = set()
        rows_by_id: dict[str, dict[str, Any]] = {}
        duplicate_ids: set[str] = set()
        capture_complete = job_complete(job) and bool(paths)
        for relative in paths:
            raw = raw_by_relative.get(relative)
            if raw is None or not raw.usable_complete:
                capture_complete = False
                continue
            audit_refs.add(raw.artifact.ref())
            for message in read_raw_messages(raw):
                message_id = str(message.get("message_id") or "")
                if not DISCORD_ID_RE.fullmatch(message_id):
                    capture_complete = False
                    continue
                candidate = residual_row(message, raw)
                if message_id in rows_by_id and rows_by_id[message_id] != candidate:
                    duplicate_ids.add(message_id)
                rows_by_id.setdefault(message_id, candidate)
        if duplicate_ids:
            capture_complete = False
        matched = set(rows_by_id) & targeted_ids.get(channel_id, set())
        residual_ids = sorted(set(rows_by_id) - matched, key=int)
        body = {
            "job_id": str(job.get("job_id") or ""),
            "channel_id": channel_id,
            "channel_name": job.get("channel_name"),
            "audit_date": (job.get("window") or {}).get("start"),
            "required_cutoff_utc": format_utc(cutoff),
            "capture_complete": capture_complete,
            "targeted_query_matrix_complete": targeted_complete.get(channel_id, False),
            "audit_source_artifact_refs": sorted(audit_refs),
            "targeted_source_artifact_refs": sorted(targeted_refs.get(channel_id, set())),
            "audit_message_count": len(rows_by_id),
            "targeted_match_count": len(matched),
            "residual_message_count": len(residual_ids),
            "residual_message_ids": residual_ids,
            "residual_rows": [rows_by_id[message_id] for message_id in residual_ids],
            "integrity_errors": (
                [f"duplicate_audit_message_with_conflicting_payload:{value}" for value in sorted(duplicate_ids)]
                + ([] if paths else ["audit_evidence_artifact_missing"])
            ),
        }
        packet_id = sha256_bytes(canonical_json_bytes(body))
        packets.append({"packet_id": packet_id, **body})
    packet_payload = {
        "schema_version": "1.0.0",
        "artifact_type": "discord_residual_review_packets",
        "deterministic": True,
        "review_required": bool(packets),
        "packet_count": len(packets),
        "packets": packets,
    }
    packet_payload["content_sha256"] = sha256_bytes(canonical_json_bytes(packet_payload))
    return packet_payload


def build_residual_reviews(
    packets: dict[str, Any],
    review_rows: Sequence[tuple[dict[str, Any], HashedArtifact, int]],
    cutoff: dt.datetime,
) -> list[dict[str, Any]]:
    by_job: defaultdict[str, list[tuple[dict[str, Any], HashedArtifact, int]]] = defaultdict(list)
    for row in review_rows:
        by_job[str(row[0].get("job_id") or "")].append(row)
    results: list[dict[str, Any]] = []
    for packet in packets.get("packets", []):
        job_id = str(packet.get("job_id") or "")
        candidates = by_job.get(job_id, [])
        reasons: list[str] = []
        if not packet.get("capture_complete"):
            reasons.append("residual_capture_incomplete")
        if not packet.get("targeted_query_matrix_complete"):
            reasons.append("targeted_query_matrix_incomplete")
        if len(candidates) != 1:
            reasons.append("review_result_missing" if not candidates else "duplicate_review_results")
            review = None
        else:
            review = candidates[0]
        reviewed_at: str | None = None
        reviewer: dict[str, Any] | None = None
        classifications: list[dict[str, Any]] = []
        new_terms: list[dict[str, Any]] = []
        new_terms_declared = False
        review_ref: str | None = None
        if review:
            row, artifact, index = review
            review_ref = artifact.ref(f"reviews/{index}")
            if str(row.get("status") or "").casefold() not in {"complete", "completed"}:
                reasons.append("review_status_not_complete")
            reviewed_at = str(row.get("reviewed_at_utc") or "") or None
            reviewed_time = parse_utc(reviewed_at)
            if reviewed_time is None or reviewed_time < cutoff:
                reasons.append("review_timestamp_before_required_cutoff_or_invalid")
            if str(row.get("packet_id") or "") != str(packet.get("packet_id") or ""):
                reasons.append("review_packet_id_mismatch")
            reviewer = row.get("reviewer") if isinstance(row.get("reviewer"), dict) else None
            if not reviewer:
                reasons.append("reviewer_identity_missing")
            else:
                if str(reviewer.get("type") or "") not in {"human", "llm"}:
                    reasons.append("reviewer_type_invalid")
                if not str(reviewer.get("id") or "").strip() or not str(reviewer.get("method") or "").strip():
                    reasons.append("reviewer_id_or_method_missing")
            classifications_raw = row.get("classifications")
            if not isinstance(classifications_raw, list):
                reasons.append("classifications_missing")
            else:
                classifications = [item for item in classifications_raw if isinstance(item, dict)]
                if len(classifications) != len(classifications_raw):
                    reasons.append("classification_not_object")
            new_terms_raw = row.get("new_terms")
            if not isinstance(new_terms_raw, list):
                reasons.append("new_terms_array_missing")
            else:
                new_terms_declared = True
                new_terms = [item for item in new_terms_raw if isinstance(item, dict)]
                if len(new_terms) != len(new_terms_raw):
                    reasons.append("new_term_not_object")
        expected_ids = [str(value) for value in packet.get("residual_message_ids", [])]
        classification_ids = [str(item.get("message_id") or "") for item in classifications]
        if len(set(classification_ids)) != len(classification_ids):
            reasons.append("duplicate_residual_classification")
        missing_ids = sorted(set(expected_ids) - set(classification_ids), key=lambda value: int(value) if value.isdigit() else value)
        extra_ids = sorted(set(classification_ids) - set(expected_ids))
        if missing_ids:
            reasons.append("residual_rows_unreviewed")
        if extra_ids:
            reasons.append("review_contains_nonpacket_message_ids")
        ambiguous = 0
        for item in classifications:
            if str(item.get("decision") or "") not in VALID_REVIEW_DECISIONS:
                reasons.append("classification_decision_invalid")
            if str(item.get("decision") or "") == "ambiguous":
                ambiguous += 1
            if not str(item.get("rationale") or "").strip():
                reasons.append("classification_rationale_missing")
        if ambiguous:
            reasons.append("ambiguous_residual_rows_require_resolution")
        valid_source_ids = set(expected_ids)
        for item in new_terms:
            source_ids = [str(value) for value in item.get("discord_source_message_ids", []) if str(value)]
            if not str(item.get("term") or "").strip() or not source_ids or not set(source_ids) <= valid_source_ids:
                reasons.append("new_term_missing_packet_discord_source")
        # A discovery requires query expansion, recapture, packet regeneration,
        # and a fresh review.  This generator never accepts booleans claiming
        # that cycle happened against the now-stale packet.
        if new_terms:
            reasons.append("new_terms_require_rerun_regenerated_packet_and_repeat_review")
        unreviewed = len(missing_ids) + ambiguous
        evidence_refs = [
            f"sha256:{packet.get('packet_id')}::embedded_residual_review_packet#{job_id}"
        ]
        if review_ref:
            evidence_refs.append(review_ref)
        evidence_refs.extend(packet.get("audit_source_artifact_refs") or [])
        passed = not reasons
        results.append(
            {
                "job_id": job_id,
                "status": "complete" if passed else "pending",
                "reviewed_at_utc": reviewed_at,
                "reviewer": reviewer,
                "packet_id": packet.get("packet_id"),
                "residual_row_count": len(expected_ids),
                "unreviewed_residual_rows": unreviewed if review else len(expected_ids),
                "new_terms_found": len(new_terms) if new_terms_declared else None,
                "new_terms_added_with_discord_source_refs": False if review else None,
                "affected_query_jobs_rerun": False if review else None,
                "repeat_review_complete": False if review else None,
                "evidence_refs": sorted(set(evidence_refs)),
                "pending_reasons": sorted(set(reasons)),
            }
        )
    return results


def sqlite_table_columns(connection: sqlite3.Connection) -> tuple[set[str], dict[str, set[str]]]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    columns = {
        table: {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
        for table in tables
    }
    return tables, columns


def column_errors(
    context: DatabaseContext, requirements: dict[str, set[str]]
) -> list[str]:
    errors: list[str] = []
    for table, required in requirements.items():
        if table not in context.tables:
            continue
        missing = sorted(required - context.columns.get(table, set()))
        if missing:
            errors.append(f"required_columns_missing:{table}:" + ",".join(missing))
    return errors


def open_database_context(
    *,
    database_path: Path | None,
    corpus_data: HashedArtifact | None,
    corpus_manifest_artifact: HashedArtifact,
    corpus_manifest: dict[str, Any],
    root: Path,
    cutoff: dt.datetime,
    audit_time: dt.datetime,
) -> DatabaseContext | None:
    if database_path is None or not database_path.is_file():
        return None
    database = hash_artifact(database_path, "cardinal_sqlite_database", root)
    uri = database.path.as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    tables, columns = sqlite_table_columns(connection)
    errors: list[str] = []
    try:
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        quick = str(exc)
    if quick != "ok":
        errors.append("database_quick_check_failed")
    required = {"collection_runs", "source_artifacts", "messages", "message_source_occurrences"}
    missing = sorted(required - tables)
    if missing:
        errors.append("database_required_tables_missing:" + ",".join(missing))
    built_at: str | None = None
    analysis_run_count = 0
    if "collection_runs" in tables:
        required_collection_columns = {"built_at_utc", "outside_sources_used", "source_scope"}
        missing_collection_columns = sorted(
            required_collection_columns - columns.get("collection_runs", set())
        )
        if missing_collection_columns:
            errors.append(
                "database_required_columns_missing:collection_runs:"
                + ",".join(missing_collection_columns)
            )
        else:
            row = connection.execute(
                "SELECT MAX(built_at_utc),MAX(outside_sources_used),MAX(source_scope) FROM collection_runs"
            ).fetchone()
            built_at = str(row[0]) if row and row[0] else None
            if not row or int(row[1] or 0) != 0 or str(row[2] or "") != "discord_only":
                errors.append("database_source_scope_not_discord_only")
            built_time = parse_utc(built_at)
            if built_time is None or built_time < cutoff:
                errors.append("database_build_timestamp_before_required_cutoff")
    if audit_time < cutoff:
        errors.append("audit_timestamp_before_required_cutoff")
    manifest_cutoff = corpus_cutoff(corpus_manifest)
    if manifest_cutoff is None or manifest_cutoff < cutoff:
        errors.append("corpus_data_cutoff_before_required_cutoff")
    if corpus_data is None:
        errors.append("corpus_data_artifact_missing_for_database_lineage")
    elif "source_artifacts" in tables:
        if "sha256" not in columns.get("source_artifacts", set()):
            errors.append("database_required_columns_missing:source_artifacts:sha256")
        else:
            hashes = {
                str(row[0] or "").upper()
                for row in connection.execute("SELECT sha256 FROM source_artifacts WHERE sha256 IS NOT NULL")
            }
            if corpus_data.sha256 not in hashes:
                errors.append("database_does_not_reference_exact_corpus_data_sha256")
    counts = corpus_manifest.get("counts") if isinstance(corpus_manifest.get("counts"), dict) else {}
    if "messages" in tables and counts.get("unique_messages") is not None:
        if int(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]) != int(counts["unique_messages"]):
            errors.append("database_message_count_does_not_match_corpus_manifest")
    if "message_source_occurrences" in tables and counts.get("source_occurrences") is not None:
        if int(connection.execute("SELECT COUNT(*) FROM message_source_occurrences").fetchone()[0]) != int(counts["source_occurrences"]):
            errors.append("database_occurrence_count_does_not_match_corpus_manifest")
    if "analysis_runs" in tables:
        analysis_run_count = int(connection.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0])
    refs = [database.ref(), corpus_manifest_artifact.ref()]
    if corpus_data:
        refs.append(corpus_data.ref())
    return DatabaseContext(
        artifact=database,
        connection=connection,
        tables=tables,
        columns=columns,
        prerequisite_errors=sorted(set(errors)),
        evidence_refs=sorted(set(refs)),
        analysis_run_count=analysis_run_count,
        built_at_utc=built_at,
    )


def unwrap_occurrence_payload(raw_json: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw_json or "{}"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    payload = value.get("payload")
    return payload if isinstance(payload, dict) else value


def exact_owned_reply_target(payload: dict[str, Any], guild_id: str) -> str | None:
    target = str(payload.get("reply_to_message_id") or "")
    channel = str(payload.get("reply_to_channel_id") or "")
    content_id = str(payload.get("reply_target_content_id") or "")
    source = str(payload.get("reply_to_message_id_source") or "")
    permalink = str(payload.get("reply_to_permalink") or "")
    if (
        source != "owned_reply_context_descendant_content_id"
        or payload.get("reply_target_scope_exact") is not True
        or not DISCORD_ID_RE.fullmatch(target)
        or not DISCORD_ID_RE.fullmatch(channel)
        or content_id != f"message-content-{target}"
    ):
        return None
    parsed = urlparse(permalink)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.hostname not in {"discord.com", "www.discord.com"}
        or len(parts) < 4
        or parts[-3:] != [guild_id, channel, target]
    ):
        return None
    return target


def exact_reply_map(context: DatabaseContext, guild_id: str) -> dict[str, set[str]]:
    result: defaultdict[str, set[str]] = defaultdict(set)
    required = {"message_id", "raw_json", "quarantined", "trust_state"}
    if (
        "message_source_occurrences" not in context.tables
        or not required <= context.columns.get("message_source_occurrences", set())
    ):
        return result
    query = (
        "SELECT message_id,raw_json FROM message_source_occurrences "
        "WHERE quarantined=0 AND trust_state IN ('trusted_canonical','trusted_source')"
    )
    for row in context.connection.execute(query):
        target = exact_owned_reply_target(unwrap_occurrence_payload(row[1]), guild_id)
        if target:
            result[str(row[0])].add(target)
    return result


def pending_gate(name: str, audit_time: dt.datetime, reasons: Sequence[str]) -> dict[str, Any]:
    return {
        "status": "pending",
        "audited_at_utc": format_utc(audit_time),
        "evidence_refs": [],
        "pending_reasons": sorted(set([f"{name}_evidence_unavailable", *reasons])),
    }


def audit_reply_resolution(
    context: DatabaseContext | None, *, guild_id: str, audit_time: dt.datetime
) -> dict[str, Any]:
    required = {"questions", "answers", "answer_messages", "question_answer_links", "message_source_occurrences"}
    if context is None:
        return {
            **pending_gate("reply_resolution", audit_time, ["database_missing"]),
            "selected_question_count": 0,
            "resolution_status_count": 0,
            "questions_without_resolution_status": 0,
            "direct_answer_linkage_errors": 0,
            "adjacent_context_promoted_count": 0,
        }
    reasons = list(context.prerequisite_errors)
    missing = sorted(required - context.tables)
    if missing:
        reasons.append("required_tables_missing:" + ",".join(missing))
    required_columns = {
        "questions": {"question_id", "primary_message_id", "resolution_status"},
        "answer_messages": {"answer_id", "message_id", "sequence_order"},
        "question_answer_links": {"question_id", "answer_id", "direct_reply", "link_type"},
        "message_source_occurrences": {"message_id", "raw_json", "quarantined", "trust_state"},
    }
    column_failures = column_errors(context, required_columns)
    reasons.extend(column_failures)
    if context.analysis_run_count < 1:
        reasons.append("analysis_layer_absent")
    selected = resolution_count = without_status = direct_errors = adjacent = 0
    error_examples: list[dict[str, Any]] = []
    if not missing and not column_failures:
        question_rows = list(
            context.connection.execute(
                "SELECT question_id,primary_message_id,resolution_status FROM questions ORDER BY question_id"
            )
        )
        selected = len(question_rows)
        resolution_count = sum(str(row[2] or "") in VALID_QUESTION_STATUSES for row in question_rows)
        without_status = selected - resolution_count
        exact_map = exact_reply_map(context, guild_id)
        link_rows = list(
            context.connection.execute(
                """
                SELECT q.question_id,q.primary_message_id,q.resolution_status,
                       l.answer_id,l.direct_reply,l.link_type,am.message_id
                FROM questions q
                JOIN question_answer_links l ON l.question_id=q.question_id
                JOIN answer_messages am ON am.answer_id=l.answer_id
                ORDER BY q.question_id,l.answer_id,am.sequence_order
                """
            )
        )
        valid_direct_questions: set[str] = set()
        for row in link_rows:
            question_id, question_message, _, answer_id, direct, link_type, answer_message = row
            if int(direct or 0) == 1:
                if str(question_message) in exact_map.get(str(answer_message), set()):
                    valid_direct_questions.add(str(question_id))
                else:
                    direct_errors += 1
                    error_examples.append(
                        {
                            "question_id": question_id,
                            "question_message_id": question_message,
                            "answer_id": answer_id,
                            "answer_message_id": answer_message,
                            "reason": "no_exact_owned_scoped_descendant_reply_target",
                        }
                    )
            else:
                adjacent += 1
                error_examples.append(
                    {
                        "question_id": question_id,
                        "answer_id": answer_id,
                        "answer_message_id": answer_message,
                        "reason": f"non_direct_answer_link_promoted:{link_type}",
                    }
                )
        for row in question_rows:
            if str(row[2]) == "answered" and str(row[0]) not in valid_direct_questions:
                direct_errors += 1
                error_examples.append(
                    {"question_id": row[0], "reason": "answered_question_without_valid_exact_direct_reply"}
                )
    if without_status:
        reasons.append("questions_without_resolution_status")
    if direct_errors:
        reasons.append("direct_answer_linkage_errors")
    if adjacent:
        reasons.append("adjacent_context_promoted")
    passed = not reasons
    return {
        "status": "passed" if passed else "pending",
        "audited_at_utc": format_utc(audit_time),
        "selected_question_count": selected,
        "resolution_status_count": resolution_count,
        "questions_without_resolution_status": without_status,
        "direct_answer_linkage_errors": direct_errors,
        "adjacent_context_promoted_count": adjacent,
        "accepted_reply_target_source": "owned_reply_context_descendant_content_id",
        "preview_links_accepted": False,
        "evidence_refs": context.evidence_refs,
        "pending_reasons": sorted(set(reasons)),
        "error_examples": error_examples[:100],
    }


def snowflake_millis(value: str) -> int | None:
    if not DISCORD_ID_RE.fullmatch(value):
        return None
    try:
        return (int(value) >> 22) + 1420070400000
    except ValueError:
        return None


def attachment_ids_from_media(payload: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for item in payload.get("media_assets", []) or []:
        if not isinstance(item, dict):
            continue
        match = ATTACHMENT_PATH_RE.search(str(item.get("src") or ""))
        if match:
            ids.add(match.group(2))
    return ids


def claim_chart_label(normalized_json: Any, limitations: Any) -> bool:
    try:
        normalized = json.loads(str(normalized_json or "{}"))
    except json.JSONDecodeError:
        normalized = {}
    if isinstance(normalized, dict) and isinstance(normalized.get("chart_dependent"), bool):
        return True
    return bool(re.search(r"\bchart_dependent\s*[:=]\s*(?:true|false)\b", str(limitations or ""), re.I))


def claim_chart_dependency(normalized_json: Any, limitations: Any) -> bool | None:
    try:
        normalized = json.loads(str(normalized_json or "{}"))
    except json.JSONDecodeError:
        normalized = {}
    if isinstance(normalized, dict) and isinstance(normalized.get("chart_dependent"), bool):
        return bool(normalized["chart_dependent"])
    match = re.search(
        r"\bchart_dependent\s*[:=]\s*(true|false)\b", str(limitations or ""), re.I
    )
    return match.group(1).casefold() == "true" if match else None


def audit_attachments_and_charts(
    context: DatabaseContext | None, *, audit_time: dt.datetime
) -> dict[str, Any]:
    if context is None:
        return {
            **pending_gate("attachments_and_chart_dependence", audit_time, ["database_missing"]),
            "reply_preview_media_leak_count": 0,
            "unlabeled_chart_dependent_count": 0,
        }
    reasons = list(context.prerequisite_errors)
    required = {
        "message_source_occurrences",
        "attachments",
        "attachment_extractions",
        "claims",
        "claim_evidence",
        "evidence_items",
    }
    missing = sorted(required - context.tables)
    if missing:
        reasons.append("required_tables_missing:" + ",".join(missing))
    required_columns = {
        "message_source_occurrences": {"message_id", "raw_json", "quarantined", "trust_state"},
        "attachments": {
            "attachment_id",
            "message_id",
            "relation_type",
            "ownership_status",
            "ownership_evidence_json",
            "owned_for_capture",
            "eligible_for_attachment_evidence",
            "media_kind",
            "filename",
            "local_package_path",
            "content_sha256",
            "byte_size",
            "capture_status",
            "capture_terminal",
            "capture_attempt_count",
            "capture_failure_code",
            "capture_failure_detail",
            "extraction_status",
            "extraction_artifacts_json",
            "archive_manifest_source_file_id",
            "chart_claim_eligible",
        },
        "attachment_extractions": {
            "attachment_id",
            "status",
            "local_package_path",
            "content_sha256",
            "byte_size",
            "artifact_verified",
            "locator_json",
        },
        "claims": {"claim_id", "normalized_value_json", "limitations", "resolution_status"},
        "claim_evidence": {"claim_id", "evidence_id"},
        "evidence_items": {"evidence_id", "message_id", "attachment_id"},
    }
    column_failures = column_errors(context, required_columns)
    reasons.extend(column_failures)
    if context.analysis_run_count < 1:
        reasons.append("analysis_layer_absent")
    leak_issues: dict[str, dict[str, Any]] = {}
    owners: defaultdict[str, set[str]] = defaultdict(set)
    documented_non_owned: set[str] = set()
    if (
        "message_source_occurrences" in context.tables
        and not column_errors(context, {"message_source_occurrences": required_columns["message_source_occurrences"]})
    ):
        query = (
            "SELECT message_id,raw_json FROM message_source_occurrences "
            "WHERE quarantined=0 AND trust_state IN ('trusted_canonical','trusted_source')"
        )
        for message_id, raw_json in context.connection.execute(query):
            payload = unwrap_occurrence_payload(raw_json)
            owned: set[str] = set()
            declared: set[str] = set()
            for attachment in payload.get("attachments", []) or []:
                if not isinstance(attachment, dict):
                    leak_issues[f"{message_id}:attachment_not_object"] = {
                        "message_id": message_id,
                        "reason": "attachment_not_object",
                    }
                    continue
                attachment_id = str(attachment.get("attachment_id") or "")
                relation = str(
                    attachment.get("relation_type")
                    or attachment.get("ownership")
                    or "unresolved"
                ).casefold()
                ownership_status = str(
                    attachment.get("ownership_status") or "unresolved"
                ).casefold()
                evidence = attachment.get("ownership_evidence")
                evidence = evidence if isinstance(evidence, dict) else {}
                if not DISCORD_ID_RE.fullmatch(attachment_id):
                    leak_issues[f"{message_id}:invalid:{attachment_id}"] = {
                        "message_id": message_id,
                        "attachment_id": attachment_id,
                        "reason": "attachment_id_invalid",
                    }
                    continue
                declared.add(attachment_id)
                if (
                    relation in {"embedded_external", "copied_media", "non_owned"}
                    and ownership_status == "non_owned_exact"
                    and evidence.get("exact") is True
                    and str(evidence.get("owner_message_id") or "") == str(message_id)
                    and DISCORD_ID_RE.fullmatch(str(evidence.get("owner_channel_id") or ""))
                    and DISCORD_ID_RE.fullmatch(str(evidence.get("source_channel_id") or ""))
                    and str(evidence.get("dom_relation") or attachment.get("dom_relation") or "").strip()
                ):
                    documented_non_owned.add(attachment_id)
                    continue
                if not (
                    relation in {"owned", "attachment", "message_attachment"}
                    and ownership_status == "owned_exact"
                    and evidence.get("exact") is True
                    and str(evidence.get("owner_message_id") or "") == str(message_id)
                ):
                    leak_issues[f"{message_id}:relation:{attachment_id}"] = {
                        "message_id": message_id,
                        "attachment_id": attachment_id,
                        "reason": "attachment_ownership_unresolved_or_inexact",
                    }
                    continue
                owned.add(attachment_id)
                owners[attachment_id].add(str(message_id))
                owner_ms = snowflake_millis(str(message_id))
                attachment_ms = snowflake_millis(attachment_id)
                if owner_ms is not None and attachment_ms is not None and abs(owner_ms - attachment_ms) > 300_000:
                    leak_issues[f"{message_id}:timing:{attachment_id}"] = {
                        "message_id": message_id,
                        "attachment_id": attachment_id,
                        "reason": "attachment_snowflake_timing_not_owner_local",
                    }
            for attachment_id in attachment_ids_from_media(payload) - declared:
                leak_issues[f"{message_id}:media:{attachment_id}"] = {
                    "message_id": message_id,
                    "attachment_id": attachment_id,
                    "reason": "media_asset_not_present_in_owned_attachment_array",
                }
            reply_text = str(payload.get("reply_context") or "")
            preview_ids = {match.group(2) for match in ATTACHMENT_PATH_RE.finditer(reply_text)}
            for attachment_id in preview_ids & owned:
                leak_issues[f"{message_id}:preview:{attachment_id}"] = {
                    "message_id": message_id,
                    "attachment_id": attachment_id,
                    "reason": "reply_preview_attachment_promoted_to_owner",
                }
    for attachment_id, message_ids in owners.items():
        if len(message_ids) > 1:
            leak_issues[f"multiowner:{attachment_id}"] = {
                "attachment_id": attachment_id,
                "message_ids": sorted(message_ids),
                "reason": "attachment_has_multiple_message_owners",
            }

    unlabeled: list[dict[str, Any]] = []
    chart_without_local_extraction: list[dict[str, Any]] = []
    attachment_archive_issues: list[dict[str, Any]] = []
    terminal_media_gaps: list[dict[str, Any]] = []
    if not missing and not column_failures:
        annotations: defaultdict[str, set[str]] = defaultdict(set)
        annotation_columns = {"message_id", "label"}
        if (
            "relevance_annotations" in context.tables
            and annotation_columns <= context.columns.get("relevance_annotations", set())
        ):
            for message_id, label in context.connection.execute(
                "SELECT message_id,LOWER(label) FROM relevance_annotations"
            ):
                annotations[str(message_id)].add(str(label))
        elif "relevance_annotations" in context.tables:
            reasons.append(
                "required_columns_missing:relevance_annotations:"
                + ",".join(
                    sorted(annotation_columns - context.columns.get("relevance_annotations", set()))
                )
            )
        message_visuals: set[str] = set()
        attachment_visuals: set[str] = set()
        attachments_by_message: defaultdict[str, set[str]] = defaultdict(set)
        table_verified_attachment_ids: set[str] = set()
        json_verified_attachment_ids: set[str] = set()
        attachment_evidence_ids = {
            str(row[0])
            for row in context.connection.execute(
                "SELECT DISTINCT attachment_id FROM evidence_items WHERE attachment_id IS NOT NULL"
            )
        }
        for extraction_row in context.connection.execute(
            """
            SELECT x.attachment_id,x.status,x.local_package_path,x.content_sha256,
                   x.byte_size,x.artifact_verified,x.locator_json,a.ownership_status,
                   a.eligible_for_attachment_evidence
            FROM attachment_extractions x
            JOIN attachments a ON a.attachment_id=x.attachment_id
            """
        ):
            attachment_id = str(extraction_row[0] or "")
            status = str(extraction_row[1] or "")
            local_path = str(extraction_row[2] or "")
            digest = str(extraction_row[3] or "")
            byte_size = extraction_row[4]
            artifact_verified = extraction_row[5]
            try:
                locator = json.loads(str(extraction_row[6] or "{}"))
            except json.JSONDecodeError:
                locator = None
            structurally_verified = bool(
                extraction_row[7] == "owned_exact"
                and extraction_row[8] == 1
                and status in {"complete", "partial"}
                and local_path.startswith("attachments/extractions/")
                and re.fullmatch(r"[0-9a-fA-F]{64}", digest)
                and isinstance(byte_size, int)
                and not isinstance(byte_size, bool)
                and byte_size > 0
                and artifact_verified == 1
                and isinstance(locator, dict)
                and locator.get("status") == status
                and locator.get("local_package_path") == local_path
                and str(locator.get("content_sha256") or "").casefold()
                == digest.casefold()
                and locator.get("byte_size") == byte_size
                and locator.get("local_artifact_verified") is True
            )
            if structurally_verified:
                table_verified_attachment_ids.add(attachment_id)
            else:
                attachment_archive_issues.append(
                    {
                        "attachment_id": attachment_id,
                        "reason": "queryable_extraction_lacks_verified_local_artifact",
                    }
                )
        for row in context.connection.execute(
            """
            SELECT attachment_id,message_id,relation_type,ownership_status,
                   ownership_evidence_json,owned_for_capture,
                   eligible_for_attachment_evidence,media_kind,filename,
                   local_package_path,content_sha256,byte_size,capture_status,
                   capture_terminal,extraction_status,extraction_artifacts_json,
                   capture_attempt_count,archive_manifest_source_file_id,
                   capture_failure_code,capture_failure_detail,chart_claim_eligible
            FROM attachments
            """
        ):
            attachment_id = str(row[0] or "")
            ownership_status = str(row[3] or "")
            try:
                ownership_evidence = json.loads(str(row[4] or "{}"))
            except json.JSONDecodeError:
                ownership_evidence = None
            if ownership_status == "non_owned_exact":
                valid_non_owned = bool(
                    str(row[2] or "")
                    in {"embedded_external", "copied_media", "non_owned"}
                    and isinstance(ownership_evidence, dict)
                    and ownership_evidence.get("exact") is True
                    and str(ownership_evidence.get("owner_message_id") or "")
                    == str(row[1] or "")
                    and str(ownership_evidence.get("dom_relation") or "").strip()
                    and row[5] == 0
                    and row[6] == 0
                    and str(row[12] or "") == "metadata_only"
                    and int(row[13] or 0) == 0
                    and str(row[14] or "") == "not_attempted"
                    and not str(row[9] or "").strip()
                    and not str(row[10] or "").strip()
                    and int(row[16] or 0) == 0
                    and not str(row[17] or "").strip()
                    and not str(row[18] or "").strip()
                    and not str(row[19] or "").strip()
                    and int(row[20] or 0) == 0
                    and attachment_id not in table_verified_attachment_ids
                    and attachment_id not in attachment_evidence_ids
                )
                try:
                    valid_non_owned = valid_non_owned and json.loads(
                        str(row[15] or "[]")
                    ) == []
                except json.JSONDecodeError:
                    valid_non_owned = False
                if not valid_non_owned:
                    attachment_archive_issues.append(
                        {
                            "attachment_id": row[0],
                            "message_id": row[1],
                            "reason": "non_owned_attachment_has_archive_extraction_or_inexact_metadata",
                        }
                    )
                else:
                    documented_non_owned.add(attachment_id)
                continue
            if ownership_status != "owned_exact" or row[5] != 1 or row[6] != 1:
                attachment_archive_issues.append(
                    {
                        "attachment_id": row[0],
                        "message_id": row[1],
                        "reason": "attachment_ownership_unresolved_or_inexact",
                    }
                )
                continue
            suffix = Path(str(row[8] or "")).suffix.casefold()
            if str(row[7] or "").casefold() in {"image", "video"} or suffix in VISUAL_SUFFIXES:
                attachment_visuals.add(str(row[0]))
                message_visuals.add(str(row[1]))
                attachments_by_message[str(row[1])].add(str(row[0]))
            capture_status = str(row[12] or "")
            capture_terminal = int(row[13] or 0)
            if capture_status not in {"downloaded", "unavailable", "failed"} or capture_terminal != 1:
                attachment_archive_issues.append(
                    {
                        "attachment_id": row[0],
                        "message_id": row[1],
                        "reason": "nonterminal_capture_status",
                        "capture_status": capture_status,
                    }
                )
            elif capture_status == "downloaded" and (
                not str(row[9] or "").strip()
                or not re.fullmatch(r"[0-9a-fA-F]{64}", str(row[10] or ""))
                or not isinstance(row[11], int)
                or row[11] < 0
            ):
                attachment_archive_issues.append(
                    {
                        "attachment_id": row[0],
                        "message_id": row[1],
                        "reason": "downloaded_attachment_missing_path_hash_or_size",
                    }
                )
            elif capture_status in {"unavailable", "failed"}:
                terminal_media_gaps.append(
                    {
                        "attachment_id": row[0],
                        "message_id": row[1],
                        "capture_status": capture_status,
                    }
                )
                if capture_status == "failed":
                    attachment_archive_issues.append(
                        {
                            "attachment_id": row[0],
                            "message_id": row[1],
                            "reason": "terminal_failed_attachment_blocks_literal_release",
                        }
                    )
            extraction_status = str(row[14] or "")
            try:
                extraction_artifacts = json.loads(str(row[15] or "[]"))
            except json.JSONDecodeError:
                extraction_artifacts = None
            verified_json_artifact = False
            if isinstance(extraction_artifacts, list):
                for artifact in extraction_artifacts:
                    if not isinstance(artifact, dict):
                        continue
                    artifact_size = artifact.get("byte_size")
                    if (
                        artifact.get("status") in {"complete", "partial"}
                        and artifact.get("local_artifact_verified") is True
                        and str(artifact.get("local_package_path") or "").startswith(
                            "attachments/extractions/"
                        )
                        and re.fullmatch(
                            r"[0-9a-fA-F]{64}",
                            str(artifact.get("content_sha256") or ""),
                        )
                        and isinstance(artifact_size, int)
                        and not isinstance(artifact_size, bool)
                        and artifact_size > 0
                    ):
                        verified_json_artifact = True
                        break
            if verified_json_artifact:
                json_verified_attachment_ids.add(str(row[0]))
            elif extraction_status in {"complete", "partial"}:
                attachment_archive_issues.append(
                    {
                        "attachment_id": row[0],
                        "message_id": row[1],
                        "reason": "extraction_status_without_verified_local_artifact",
                    }
                )
        extracted_attachment_ids = (
            table_verified_attachment_ids & json_verified_attachment_ids
        )
        for attachment_id in sorted(
            table_verified_attachment_ids ^ json_verified_attachment_ids
        ):
            attachment_archive_issues.append(
                {
                    "attachment_id": attachment_id,
                    "reason": "verified_extraction_table_json_parity_mismatch",
                }
            )
        claim_rows = context.connection.execute(
            """
            SELECT c.claim_id,c.normalized_value_json,c.limitations,
                   ev.message_id,ev.attachment_id
            FROM claims c
            JOIN claim_evidence ce ON ce.claim_id=c.claim_id
            JOIN evidence_items ev ON ev.evidence_id=ce.evidence_id
            WHERE c.resolution_status IN ('accepted','qualified')
            ORDER BY c.claim_id
            """
        )
        seen_candidates: set[str] = set()
        for claim_id, normalized, limitations, message_id, attachment_id in claim_rows:
            visual = str(attachment_id or "") in attachment_visuals or str(message_id or "") in message_visuals
            if not visual or str(claim_id) in seen_candidates:
                continue
            seen_candidates.add(str(claim_id))
            labels = annotations.get(str(message_id or ""), set())
            explicit = claim_chart_label(normalized, limitations) or bool(
                labels & {"chart_dependent", "chart_independent"}
            )
            if not explicit:
                unlabeled.append(
                    {
                        "claim_id": claim_id,
                        "message_id": message_id,
                        "attachment_id": attachment_id,
                    }
                )
                continue
            dependency = claim_chart_dependency(normalized, limitations)
            if dependency is None and "chart_dependent" in labels:
                dependency = True
            elif dependency is None and "chart_independent" in labels:
                dependency = False
            if dependency is True:
                candidate_attachment_ids = (
                    {str(attachment_id)}
                    if str(attachment_id or "") in attachment_visuals
                    else attachments_by_message.get(str(message_id or ""), set())
                )
                if not candidate_attachment_ids or not (
                    candidate_attachment_ids & extracted_attachment_ids
                ):
                    chart_without_local_extraction.append(
                        {
                            "claim_id": claim_id,
                            "message_id": message_id,
                            "attachment_id": attachment_id,
                            "candidate_attachment_ids": sorted(candidate_attachment_ids),
                        }
                    )
    if leak_issues:
        reasons.append("reply_preview_or_attachment_ownership_issues")
    if unlabeled:
        reasons.append("chart_dependent_records_missing_explicit_label")
    if attachment_archive_issues:
        reasons.append("attachment_archive_not_terminal_or_missing_download_integrity_metadata")
    if chart_without_local_extraction:
        reasons.append("chart_dependent_claim_without_local_extraction")
    passed = not reasons
    return {
        "status": "passed" if passed else "pending",
        "audited_at_utc": format_utc(audit_time),
        "reply_preview_media_leak_count": len(leak_issues),
        "unlabeled_chart_dependent_count": len(unlabeled),
        "chart_claim_without_local_extraction_count": len(chart_without_local_extraction),
        "attachment_archive_issue_count": len(attachment_archive_issues),
        "terminal_media_gap_count": len(terminal_media_gaps),
        "attachment_byte_complete": not terminal_media_gaps,
        "attachment_owner_count": len(owners),
        "documented_non_owned_attachment_count": len(documented_non_owned),
        "evidence_refs": context.evidence_refs,
        "pending_reasons": sorted(set(reasons)),
        "reply_preview_media_leak_examples": list(leak_issues.values())[:100],
        "unlabeled_chart_dependent_examples": unlabeled[:100],
        "chart_claim_without_local_extraction_examples": chart_without_local_extraction[:100],
        "attachment_archive_issue_examples": attachment_archive_issues[:100],
        "terminal_media_gap_examples": terminal_media_gaps[:100],
    }


def probability_like(facet: Any, claim_text: Any, normalized: Any) -> bool:
    combined = " ".join([str(facet or ""), str(claim_text or ""), str(normalized or "")])
    if PROBABILITY_WORD_RE.search(combined):
        return True
    return bool(PERCENT_RE.search(combined) and PERCENT_CONTEXT_RE.search(combined))


def calibrated_probability_claim(
    *, claim_kind: str, epistemic_status: str, resolution_status: str, limitations: str
) -> bool:
    if resolution_status in {"unresolved", "rejected", "superseded"}:
        return True
    guarded = bool(CALIBRATION_GUARD_RE.search(limitations))
    if claim_kind == "observed_association" and epistemic_status == "observed_association":
        return guarded
    if claim_kind == "insufficient_evidence" or epistemic_status == "insufficient_evidence":
        return guarded
    if claim_kind in {"explicit_rule", "explicit_example", "explicit_outcome"} and epistemic_status == "explicit_source":
        return guarded
    return False


def audit_claim_calibration(
    context: DatabaseContext | None, *, audit_time: dt.datetime
) -> dict[str, Any]:
    if context is None:
        return {
            **pending_gate("claim_calibration", audit_time, ["database_missing"]),
            "unsupported_probability_claim_count": 0,
            "uncalibrated_success_probability_count": 0,
        }
    reasons = list(context.prerequisite_errors)
    required = {"claims", "claim_evidence", "evidence_items"}
    missing = sorted(required - context.tables)
    if missing:
        reasons.append("required_tables_missing:" + ",".join(missing))
    required_columns = {
        "claims": {
            "claim_id", "facet", "claim_text", "normalized_value_json", "claim_kind",
            "epistemic_status", "resolution_status", "limitations",
        },
        "claim_evidence": {"claim_id", "evidence_id"},
        "evidence_items": {
            "evidence_id", "eligible_for_accepted_claims", "source_scope", "outside_sources_used",
        },
    }
    column_failures = column_errors(context, required_columns)
    reasons.extend(column_failures)
    if context.analysis_run_count < 1:
        reasons.append("analysis_layer_absent")
    unsupported: list[dict[str, Any]] = []
    uncalibrated: list[dict[str, Any]] = []
    probability_claim_count = 0
    if not missing and not column_failures:
        rows = context.connection.execute(
            """
            SELECT c.claim_id,c.facet,c.claim_text,c.normalized_value_json,
                   c.claim_kind,c.epistemic_status,c.resolution_status,c.limitations,
                   COUNT(ce.evidence_id) AS evidence_count,
                   COALESCE(SUM(CASE WHEN ev.eligible_for_accepted_claims=1
                                      AND ev.source_scope='discord_only'
                                      AND ev.outside_sources_used=0 THEN 1 ELSE 0 END),0) AS trusted_count,
                   COALESCE(SUM(CASE WHEN ev.eligible_for_accepted_claims<>1
                                      OR ev.source_scope<>'discord_only'
                                      OR ev.outside_sources_used<>0 THEN 1 ELSE 0 END),0) AS bad_count
            FROM claims c
            LEFT JOIN claim_evidence ce ON ce.claim_id=c.claim_id
            LEFT JOIN evidence_items ev ON ev.evidence_id=ce.evidence_id
            GROUP BY c.claim_id
            ORDER BY c.claim_id
            """
        )
        for row in rows:
            if not probability_like(row[1], row[2], row[3]):
                continue
            probability_claim_count += 1
            evidence_count = int(row[8] or 0)
            trusted_count = int(row[9] or 0)
            bad_count = int(row[10] or 0)
            if evidence_count == 0 or trusted_count != evidence_count or bad_count:
                unsupported.append(
                    {
                        "claim_id": row[0],
                        "evidence_count": evidence_count,
                        "trusted_discord_evidence_count": trusted_count,
                        "bad_evidence_count": bad_count,
                    }
                )
            if not calibrated_probability_claim(
                claim_kind=str(row[4] or ""),
                epistemic_status=str(row[5] or ""),
                resolution_status=str(row[6] or ""),
                limitations=str(row[7] or ""),
            ):
                uncalibrated.append(
                    {
                        "claim_id": row[0],
                        "claim_kind": row[4],
                        "epistemic_status": row[5],
                        "resolution_status": row[6],
                    }
                )
        rollup_columns = {
            "rollup_id", "claim_id", "eligible_count", "wins", "losses", "breakevens",
            "unknowns", "observed_win_rate", "not_causal", "limitations",
        }
        if (
            "setup_performance_rollups" in context.tables
            and rollup_columns <= context.columns.get("setup_performance_rollups", set())
        ):
            bad_rollups = context.connection.execute(
                """
                SELECT rollup_id,claim_id,eligible_count,wins,losses,breakevens,unknowns,
                       observed_win_rate,not_causal,limitations
                FROM setup_performance_rollups
                WHERE eligible_count<>wins+losses+breakevens+unknowns
                   OR not_causal<>1
                   OR (eligible_count>0 AND ABS(observed_win_rate-(wins*1.0/eligible_count))>0.0000001)
                   OR (eligible_count=0 AND observed_win_rate IS NOT NULL)
                   OR LOWER(limitations) NOT LIKE '%not%'
                """
            )
            for row in bad_rollups:
                uncalibrated.append({"rollup_id": row[0], "claim_id": row[1], "reason": "rollup_not_calibrated"})
        elif "setup_performance_rollups" in context.tables:
            reasons.append(
                "required_columns_missing:setup_performance_rollups:"
                + ",".join(
                    sorted(rollup_columns - context.columns.get("setup_performance_rollups", set()))
                )
            )
    if unsupported:
        reasons.append("unsupported_probability_claims")
    if uncalibrated:
        reasons.append("uncalibrated_success_probability_claims")
    passed = not reasons
    return {
        "status": "passed" if passed else "pending",
        "audited_at_utc": format_utc(audit_time),
        "probability_like_claim_count": probability_claim_count,
        "unsupported_probability_claim_count": len(unsupported),
        "uncalibrated_success_probability_count": len(uncalibrated),
        "evidence_refs": context.evidence_refs,
        "pending_reasons": sorted(set(reasons)),
        "unsupported_examples": unsupported[:100],
        "uncalibrated_examples": uncalibrated[:100],
        "calibration_rule": (
            "Probability-like normalized claims require complete trusted Discord evidence and an explicit "
            "self-reported, descriptive/sample-bound, non-causal, uncalibrated, or insufficient-evidence caveat."
        ),
    }


def managed_gate_passed(value: Any) -> bool:
    if isinstance(value, list):
        return bool(value) and all(str(row.get("status") or "") in {"passed", "complete"} for row in value)
    return isinstance(value, dict) and str(value.get("status") or "") == "passed"


def generate_release_bundle(
    *,
    root: Path,
    plan_path: Path,
    progress_path: Path,
    corpus_manifest_path: Path,
    corpus_data_path: Path | None,
    database_path: Path | None,
    count_observation_paths: Sequence[Path],
    review_result_paths: Sequence[Path],
    audit_time: dt.datetime | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    audit_time = (audit_time or utc_now()).astimezone(dt.timezone.utc)
    plan_artifact = hash_artifact(plan_path, "relevance_collection_plan", root)
    progress_artifact = hash_artifact(progress_path, "collection_progress_manifest", root)
    corpus_manifest_artifact = hash_artifact(corpus_manifest_path, "corpus_manifest", root)
    plan = read_json_object(plan_path, "relevance collection plan")
    progress = read_json_object(progress_path, "collection progress manifest")
    corpus_manifest = read_json_object(corpus_manifest_path, "corpus manifest")
    if progress.get("artifact_type") != "discord_collection_progress_manifest":
        raise ReleaseEvidenceError("Unexpected progress artifact_type")
    source_policy = progress.get("source_policy") if isinstance(progress.get("source_policy"), dict) else {}
    if int(source_policy.get("browser_calls_made") or 0) != 0 or int(source_policy.get("raw_files_modified") or 0) != 0:
        raise ReleaseEvidenceError("Progress manifest violates local read-only source policy")
    window = plan.get("window") if isinstance(plan.get("window"), dict) else {}
    cutoff = parse_utc(window.get("utc_end_exclusive"))
    if cutoff is None:
        raise ReleaseEvidenceError("Plan required cutoff is missing or invalid")
    guild = plan.get("guild") if isinstance(plan.get("guild"), dict) else {}
    guild_id = str(guild.get("guild_id") or "")
    if not DISCORD_ID_RE.fullmatch(guild_id):
        raise ReleaseEvidenceError("Plan guild ID is missing or invalid")

    raw_by_relative, raw_artifacts, raw_manifest_errors = load_raw_artifacts(root, progress)
    count_rows, count_artifacts = load_evidence_rows(
        [path for path in count_observation_paths if path.is_file()],
        "count_observations",
        "count_observation_artifact",
        root,
        allowed_artifact_types={
            "discord_collection_orchestrator_state",
            "discord_count_observations",
        },
    ) if any(path.is_file() for path in count_observation_paths) else ([], [])
    review_rows, review_artifacts = load_evidence_rows(
        [path for path in review_result_paths if path.is_file()],
        "reviews",
        "residual_review_results",
        root,
        allowed_artifact_types={"discord_residual_review_results"},
    ) if review_result_paths else ([], [])
    corpus_data = (
        hash_artifact(corpus_data_path, "corpus_data", root)
        if corpus_data_path is not None and corpus_data_path.is_file()
        else None
    )
    database_context = open_database_context(
        database_path=database_path,
        corpus_data=corpus_data,
        corpus_manifest_artifact=corpus_manifest_artifact,
        corpus_manifest=corpus_manifest,
        root=root,
        cutoff=cutoff,
        audit_time=audit_time,
    )
    try:
        counts = build_count_reconciliation(
            plan=plan,
            progress=progress,
            raw_by_relative=raw_by_relative,
            corpus_manifest=corpus_manifest,
            observations=count_rows,
            cutoff=cutoff,
        )
        packets = build_residual_packets(progress, raw_by_relative, cutoff, plan)
        residual_reviews = build_residual_reviews(packets, review_rows, cutoff)
        reply = audit_reply_resolution(database_context, guild_id=guild_id, audit_time=audit_time)
        attachments = audit_attachments_and_charts(database_context, audit_time=audit_time)
        claims = audit_claim_calibration(database_context, audit_time=audit_time)
    finally:
        if database_context is not None:
            database_context.connection.close()

    managed = {
        "full_capture_count_reconciliation": counts,
        "residual_reviews": residual_reviews,
        "reply_resolution": reply,
        "attachments_and_chart_dependence": attachments,
        "claim_calibration": claims,
    }
    pending: list[dict[str, Any]] = []
    for gate, value in managed.items():
        if managed_gate_passed(value):
            continue
        if isinstance(value, list):
            if not value:
                if gate == "residual_reviews" and packets.get("packet_count") == 0:
                    continue
                pending.append({"gate": gate, "reasons": ["required_evidence_rows_missing"]})
                continue
            for row in value:
                if str(row.get("status") or "") not in {"passed", "complete"}:
                    pending.append(
                        {
                            "gate": gate,
                            "key": row.get("channel_id") or row.get("job_id"),
                            "reasons": row.get("pending_reasons") or ["pending"],
                        }
                    )
        else:
            pending.append({"gate": gate, "reasons": value.get("pending_reasons") or ["pending"]})

    input_artifacts = [
        plan_artifact,
        progress_artifact,
        corpus_manifest_artifact,
        *raw_artifacts,
        *count_artifacts,
        *review_artifacts,
    ]
    if corpus_data:
        input_artifacts.append(corpus_data)
    if database_context:
        input_artifacts.append(database_context.artifact)
    deduped = {f"{item.kind}:{item.sha256}:{item.display_path}": item for item in input_artifacts}
    existing = progress.get("release_evidence") if isinstance(progress.get("release_evidence"), dict) else {}
    release_evidence = {
        **{
            key: copy.deepcopy(value)
            for key, value in existing.items()
            if key not in {*managed, "outside_sources_used", "generator", "status", "pending_items", "source_artifacts"}
        },
        "schema_version": "1.0.0",
        "artifact_type": "discord_release_evidence",
        "status": "complete" if not pending and not raw_manifest_errors else "pending",
        "generated_at_utc": format_utc(audit_time),
        "required_cutoff_utc": format_utc(cutoff),
        "outside_sources_used": 0,
        "generator": {
            "script": display_path(Path(__file__), root),
            "script_sha256": sha256_file(Path(__file__)),
            "local_only": True,
            "browser_calls_made": 0,
            "network_calls_made": 0,
            "raw_files_modified": 0,
            "database_open_mode": "read_only_immutable",
            "managed_fields_recomputed_not_trusted_from_prior_manifest": sorted(managed),
        },
        "source_artifacts": [item.serializable() for item in sorted(deduped.values(), key=lambda value: (value.kind, value.display_path))],
        **managed,
        "pending_items": pending + (
            [{"gate": "raw_progress_consistency", "reasons": raw_manifest_errors}]
            if raw_manifest_errors else []
        ),
    }
    output = copy.deepcopy(progress)
    output["release_evidence"] = release_evidence
    output["release_review_packets"] = packets
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=HERE)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument("--corpus-manifest", type=Path, default=DEFAULT_CORPUS_MANIFEST)
    parser.add_argument("--corpus-data", type=Path, default=DEFAULT_CORPUS_DATA)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--count-observations",
        type=Path,
        action="append",
        default=None,
        help="Hashed JSON source containing operator-recorded count_observations; repeatable.",
    )
    parser.add_argument(
        "--review-results",
        type=Path,
        action="append",
        default=[],
        help="Hashed JSON source containing residual review results; repeatable.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        output = require_working_output(args.output, root)
        if output.exists() and not args.overwrite:
            raise FileExistsError(f"Output already exists: {output}. Use --overwrite explicitly.")
        count_paths = args.count_observations
        if count_paths is None:
            root_default_counts = root / "working" / "collection_orchestrator_state.json"
            count_paths = [root_default_counts] if root_default_counts.is_file() else []
        bundle = generate_release_bundle(
            root=root,
            plan_path=args.plan.resolve(),
            progress_path=args.progress.resolve(),
            corpus_manifest_path=args.corpus_manifest.resolve(),
            corpus_data_path=args.corpus_data.resolve() if args.corpus_data else None,
            database_path=args.database.resolve() if args.database else None,
            count_observation_paths=[path.resolve() for path in count_paths],
            review_result_paths=[path.resolve() for path in args.review_results],
        )
        atomic_write_json(output, bundle, overwrite=args.overwrite)
        evidence = bundle["release_evidence"]
        print(
            json.dumps(
                {
                    "status": evidence["status"],
                    "output": str(output),
                    "pending_item_count": len(evidence["pending_items"]),
                    "review_packet_count": bundle["release_review_packets"]["packet_count"],
                    "output_sha256": sha256_file(output),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, TypeError, KeyError, sqlite3.DatabaseError, ReleaseEvidenceError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
