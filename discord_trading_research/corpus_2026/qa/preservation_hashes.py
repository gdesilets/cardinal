#!/usr/bin/env python3
"""Create or verify an immutable SHA-256 inventory for established artifacts.

The default inventory deliberately excludes the live full-server capture and this
new corpus directory.  It covers the validated 14-day and three-month deliverables
plus every completed historical segment used to build them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "1.0.0"

DEFAULT_ARTIFACT_NAMES = (
    "browser_context_followups_3month.json",
    "curated_analysis.json",
    "curated_analysis_3month.json",
    "discord_trading_research.sqlite",
    "discord_trading_research_3month.sqlite",
    "model_analysis.json",
    "model_analysis_3month.json",
    "raw_discord_export.json",
    "raw_discord_export_3month.json",
    "rb_analysis.json",
    "rb_analysis_3month.json",
    "README_3MONTH_DATABASE.md",
    "README_FOR_LLM.md",
    "README_FOR_LLM_3MONTH.md",
    "RESEARCH_SUMMARY.md",
    "RESEARCH_SUMMARY_3MONTH.md",
    "three_month_coverage_manifest.json",
    "trade_analysis.json",
    "trade_analysis_3month.json",
    "validation_report.json",
    "validation_report_3month.json",
)

DEFAULT_ARTIFACT_DIRECTORIES = (
    "three_month_segments",
    "three_month_supplemental",
)

IGNORED_SUFFIXES = ("-wal", "-shm", ".tmp", ".partial")


class PreservationError(RuntimeError):
    """Raised when an immutable-artifact operation cannot be completed safely."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def stable_file_record(path: Path, root: Path) -> dict[str, Any]:
    """Hash a file and fail if it changes while being read."""
    before = path.stat()
    digest = sha256_file(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise PreservationError(f"Artifact changed while hashing: {path}")
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "size_bytes": after.st_size,
        "mtime_ns_at_snapshot": after.st_mtime_ns,
        "sha256": digest,
    }


def discover_default_artifacts(root: Path) -> list[Path]:
    files: set[Path] = set()
    missing: list[str] = []
    for name in DEFAULT_ARTIFACT_NAMES:
        path = root / name
        if path.is_file():
            files.add(path.resolve())
        else:
            missing.append(name)
    for name in DEFAULT_ARTIFACT_DIRECTORIES:
        directory = root / name
        if not directory.is_dir():
            missing.append(name + "/")
            continue
        for path in directory.rglob("*"):
            if path.is_file() and not path.name.endswith(IGNORED_SUFFIXES):
                files.add(path.resolve())
    if missing:
        raise PreservationError("Expected established artifacts are missing: " + ", ".join(missing))
    return sorted(files, key=lambda item: item.relative_to(root).as_posix().casefold())


def normalize_explicit_paths(root: Path, values: Iterable[str]) -> list[Path]:
    files: set[Path] = set()
    for value in values:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PreservationError(f"Artifact is outside the protected root: {candidate}") from exc
        if candidate.is_dir():
            files.update(path.resolve() for path in candidate.rglob("*") if path.is_file())
        elif candidate.is_file():
            files.add(candidate)
        else:
            raise PreservationError(f"Artifact does not exist: {candidate}")
    return sorted(files, key=lambda item: item.relative_to(root).as_posix().casefold())


def build_manifest(root: Path, paths: list[Path], selection: dict[str, Any] | None = None) -> dict[str, Any]:
    records = [stable_file_record(path, root) for path in paths]
    aggregate = hashlib.sha256()
    for record in records:
        aggregate.update(record["relative_path"].encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(record["sha256"].encode("ascii"))
        aggregate.update(b"\n")
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "immutable_existing_artifact_hash_baseline",
        "generated_at_utc": utc_now(),
        "protected_root": str(root),
        "selection": selection
        or {
            "mode": "established_artifacts_default",
            "default_artifact_names": list(DEFAULT_ARTIFACT_NAMES),
            "default_artifact_directories": list(DEFAULT_ARTIFACT_DIRECTORIES),
            "excluded_live_capture_directories": [
                "full_server_segments",
                "full_server_channel_segments",
                "corpus_2026-01-01_2026-07-20",
            ],
        },
        "file_count": len(records),
        "aggregate_sha256": aggregate.hexdigest().upper(),
        "files": records,
    }


def write_exclusive(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise PreservationError(f"Refusing to overwrite an existing baseline: {path}") from exc


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreservationError(f"Cannot read preservation manifest {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise PreservationError(f"Unsupported preservation manifest: {path}")
    return value


def verify_manifest(path: Path, root_override: Path | None = None) -> dict[str, Any]:
    baseline = load_manifest(path)
    root = (root_override or Path(str(baseline.get("protected_root") or ""))).resolve()
    expected_rows = baseline.get("files")
    if not isinstance(expected_rows, list):
        raise PreservationError("Preservation manifest has no files array.")

    missing: list[str] = []
    changed: list[dict[str, Any]] = []
    verified: list[dict[str, Any]] = []
    for expected in expected_rows:
        if not isinstance(expected, dict) or not expected.get("relative_path"):
            raise PreservationError("Preservation manifest contains an invalid file record.")
        relative = str(expected["relative_path"])
        candidate = (root / Path(relative)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PreservationError(f"Manifest path escapes protected root: {relative}") from exc
        if not candidate.is_file():
            missing.append(relative)
            continue
        actual = stable_file_record(candidate, root)
        if actual["sha256"] != str(expected.get("sha256") or "").upper() or actual["size_bytes"] != expected.get(
            "size_bytes"
        ):
            changed.append(
                {
                    "relative_path": relative,
                    "expected_size_bytes": expected.get("size_bytes"),
                    "actual_size_bytes": actual["size_bytes"],
                    "expected_sha256": expected.get("sha256"),
                    "actual_sha256": actual["sha256"],
                }
            )
        else:
            verified.append(actual)

    result = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "immutable_existing_artifact_hash_verification",
        "verified_at_utc": utc_now(),
        "baseline_manifest": str(path.resolve()),
        "protected_root": str(root),
        "status": "passed" if not missing and not changed else "failed",
        "expected_file_count": len(expected_rows),
        "verified_file_count": len(verified),
        "missing": missing,
        "changed": changed,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot", help="Create a write-once immutable hash baseline.")
    snapshot.add_argument("--root", type=Path, required=True, help="Established artifact directory to protect.")
    snapshot.add_argument("--output", type=Path, required=True, help="New manifest path; must not already exist.")
    snapshot.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="Optional file/directory relative to --root. Repeat to replace the default selection.",
    )

    verify = subparsers.add_parser("verify", help="Verify artifacts against an existing baseline.")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--root", type=Path, help="Optional relocated protected root.")
    verify.add_argument("--output", type=Path, help="Optional write-once JSON verification report.")

    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            root = args.root.resolve()
            if not root.is_dir():
                raise PreservationError(f"Protected root is not a directory: {root}")
            paths = normalize_explicit_paths(root, args.artifact) if args.artifact else discover_default_artifacts(root)
            selection = (
                {"mode": "explicit", "requested_artifacts": list(args.artifact)}
                if args.artifact
                else None
            )
            payload = build_manifest(root, paths, selection)
            write_exclusive(args.output.resolve(), payload)
        else:
            payload = verify_manifest(args.manifest.resolve(), args.root.resolve() if args.root else None)
            if args.output:
                write_exclusive(args.output.resolve(), payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("status", "passed") == "passed" else 1
    except PreservationError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
