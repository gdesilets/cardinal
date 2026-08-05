from __future__ import annotations

"""Durably archive Discord-hosted message attachments without inspecting auth state.

This module deliberately does not make network requests.  An authenticated browser
worker obtains one request at a time with ``next`` and returns a small response
envelope to ``ingest``.  Successful response bytes are written atomically and
hashed; failures remain first-class manifest records.  External links are never
catalogued as attachments and no cookie, local-storage, profile, or credential
material is accepted by the contract.
"""

import argparse
import base64
import binascii
import copy
import datetime as dt
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence
from urllib.parse import unquote, urlsplit


SCHEMA_VERSION = "1.1.0"
ARTIFACT_TYPE = "discord_attachment_archive_manifest"
CORPUS_ARTIFACT_TYPES = {
    "discord_serverwide_corpus_working",
    "discord_serverwide_corpus_release",
}
ALLOWED_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}
DISCORD_ID_RE = re.compile(r"\d{15,22}")
ATTACHMENT_PATH_RE = re.compile(
    r"^/attachments/(?P<channel_id>\d{15,22})/"
    r"(?P<attachment_id>\d{15,22})/(?P<filename>[^/?#]+)$"
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
TERMINAL_CAPTURE_STATES = {"downloaded", "unavailable", "failed"}
CAPTURE_STATES = TERMINAL_CAPTURE_STATES | {"pending"}
EXTRACTION_STATES = {"not_attempted", "complete", "partial", "failed"}
SUCCESSFUL_EXTRACTION_STATES = {"complete", "partial"}
UNAVAILABLE_HTTP_STATUSES = {404, 410}
UNAVAILABLE_ERROR_CODES = {
    "discord_ui_unavailable",
    "attachment_deleted",
    "not_found",
    "gone",
}
MIN_TERMINAL_FAILURE_ATTEMPTS = 3
NON_SUBSTANTIVE_DETAILS = {
    "",
    "error",
    "failed",
    "failure",
    "n/a",
    "na",
    "none",
    "unknown",
}


class AttachmentArchiveError(RuntimeError):
    """Raised when attachment provenance or durability cannot be proved."""


def substantive_failure_detail(value: Any) -> str | None:
    """Return a normalized diagnostic only when it is useful audit evidence."""

    text = " ".join(str(value or "").split())
    if len(text) < 8 or text.casefold() in NON_SUBSTANTIVE_DETAILS:
        return None
    return text


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def parse_utc(value: Any, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AttachmentArchiveError(f"{label} is required")
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise AttachmentArchiveError(f"{label} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise AttachmentArchiveError(f"{label} must include a timezone")
    return parsed.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stable_id(*parts: Any) -> str:
    payload = "\x1f".join(str(part or "") for part in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AttachmentArchiveError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AttachmentArchiveError(f"{label} must be a JSON object")
    return value


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def safe_filename(value: Any, *, fallback: str) -> str:
    filename = PurePosixPath(unquote(str(value or "").replace("\\", "/"))).name
    filename = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", filename).strip(" .")
    if not filename:
        filename = fallback
    if len(filename) > 180:
        suffix = Path(filename).suffix[:20]
        filename = filename[: 180 - len(suffix)] + suffix
    return filename


def validate_package_relative_path(value: Any, *, label: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or text.startswith("/")
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(":" in part for part in path.parts)
    ):
        raise AttachmentArchiveError(f"{label} is not a safe package-relative path")
    return path.as_posix()


def resolve_under(root: Path, package_relative: str, *, label: str) -> Path:
    relative = validate_package_relative_path(package_relative, label=label)
    root = root.resolve()
    resolved = (root / Path(*PurePosixPath(relative).parts)).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AttachmentArchiveError(f"{label} escapes the archive root") from exc
    return resolved


def parse_discord_attachment_url(
    value: Any, *, expected_attachment_id: str | None = None
) -> dict[str, str]:
    text = str(value or "").strip()
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise AttachmentArchiveError(f"Malformed attachment URL: {value!r}") from exc
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or host not in ALLOWED_HOSTS:
        raise AttachmentArchiveError(
            "Attachment URL must use HTTPS on cdn.discordapp.com or media.discordapp.net"
        )
    match = ATTACHMENT_PATH_RE.fullmatch(parsed.path)
    if not match:
        raise AttachmentArchiveError("Attachment URL must use Discord's /attachments/<id>/<id>/<file> path")
    parts = match.groupdict()
    if expected_attachment_id and parts["attachment_id"] != expected_attachment_id:
        raise AttachmentArchiveError("Attachment URL ID does not match the owned attachment ID")
    return {
        "url": text,
        "url_host": host,
        "url_path": parsed.path,
        "source_channel_id": parts["channel_id"],
        "attachment_id": parts["attachment_id"],
        "url_filename": unquote(parts["filename"]),
    }


def corpus_messages(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    if corpus.get("artifact_type") not in CORPUS_ARTIFACT_TYPES:
        raise AttachmentArchiveError("Source is not a supported server-wide Discord corpus")
    messages = corpus.get("messages")
    if not isinstance(messages, list) or any(not isinstance(row, dict) for row in messages):
        raise AttachmentArchiveError("Source corpus messages must be an array of objects")
    return messages


def matching_message_attachment_urls(
    message: dict[str, Any], attachment_id: str
) -> list[str]:
    candidates: list[str] = []
    for link in message.get("links") or []:
        candidates.append(str(link.get("url") if isinstance(link, dict) else link or ""))
    for asset in message.get("media_assets") or []:
        if isinstance(asset, dict):
            candidates.append(str(asset.get("src") or ""))
    matched: list[str] = []
    for candidate in candidates:
        try:
            parse_discord_attachment_url(candidate, expected_attachment_id=attachment_id)
        except AttachmentArchiveError:
            continue
        matched.append(candidate)
    return matched


def select_attachment_url(
    attachment: dict[str, Any],
    supplemental_urls: Sequence[str] = (),
    *,
    expected_attachment_id: str,
) -> str:
    explicit_urls = [
        str(value).strip()
        for value in (attachment.get("url"), attachment.get("discord_url"))
        if str(value or "").strip()
    ]
    for explicit_url in explicit_urls:
        try:
            parse_discord_attachment_url(
                explicit_url, expected_attachment_id=expected_attachment_id
            )
        except AttachmentArchiveError as exc:
            if "does not match" in str(exc):
                raise
            raise AttachmentArchiveError(
                "Owned attachment has no valid Discord-hosted URL"
            ) from exc
    candidates = [
        *sorted(
            supplemental_urls,
            key=lambda value: ("?" not in value, value.casefold()),
        ),
        *explicit_urls,
    ]
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        try:
            parse_discord_attachment_url(
                text, expected_attachment_id=expected_attachment_id
            )
        except AttachmentArchiveError:
            continue
        return text
    raise AttachmentArchiveError("Owned attachment has no valid Discord-hosted URL")


OWNED_RELATIONS = {"owned", "attachment", "message_attachment"}
NON_OWNED_RELATIONS = {"embedded_external", "copied_media", "non_owned"}


def exact_attachment_relation(raw: dict[str, Any], *, message_id: str) -> str:
    relation = str(raw.get("relation_type") or raw.get("ownership") or "").casefold()
    status = str(raw.get("ownership_status") or "").casefold()
    evidence = raw.get("ownership_evidence")
    if relation in OWNED_RELATIONS:
        if status != "owned_exact" or not isinstance(evidence, dict) or evidence.get("exact") is not True:
            raise AttachmentArchiveError(
                f"Message {message_id} attachment claims ownership without exact ownership evidence"
            )
        if str(evidence.get("owner_message_id") or "") != message_id:
            raise AttachmentArchiveError(
                f"Message {message_id} attachment ownership evidence names a different owner"
            )
        return "owned"
    if relation in NON_OWNED_RELATIONS:
        if status != "non_owned_exact" or not isinstance(evidence, dict) or evidence.get("exact") is not True:
            raise AttachmentArchiveError(
                f"Message {message_id} non-owned attachment relation lacks exact evidence"
            )
        if str(evidence.get("owner_message_id") or "") != message_id:
            raise AttachmentArchiveError(
                f"Message {message_id} non-owned attachment evidence names a different container message"
            )
        return "non_owned"
    raise AttachmentArchiveError(
        f"Message {message_id} attachment ownership is unresolved; recapture exact DOM relation before planning"
    )


def discover_non_owned_entries(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for message in corpus_messages(corpus):
        message_id = str(message.get("message_id") or "").strip()
        raw_attachments = message.get("attachments") or []
        if not isinstance(raw_attachments, list):
            raise AttachmentArchiveError(f"Message {message_id} attachments is not an array")
        for raw in raw_attachments:
            if not isinstance(raw, dict):
                raise AttachmentArchiveError(f"Message {message_id} has a non-object attachment")
            if exact_attachment_relation(raw, message_id=message_id) != "non_owned":
                continue
            attachment_id = str(raw.get("attachment_id") or raw.get("id") or "").strip()
            if not DISCORD_ID_RE.fullmatch(attachment_id):
                raise AttachmentArchiveError(
                    f"Message {message_id} non-owned media lacks an exact Discord attachment ID"
                )
            url = select_attachment_url(
                raw,
                matching_message_attachment_urls(message, attachment_id),
                expected_attachment_id=attachment_id,
            )
            parsed = parse_discord_attachment_url(url, expected_attachment_id=attachment_id)
            evidence = raw.get("ownership_evidence") or {}
            owner_channel_id = str(evidence.get("owner_channel_id") or "")
            source_channel_id = str(evidence.get("source_channel_id") or "")
            if (
                not DISCORD_ID_RE.fullmatch(owner_channel_id)
                or source_channel_id != parsed["source_channel_id"]
                or not str(evidence.get("dom_relation") or "").strip()
            ):
                raise AttachmentArchiveError(
                    f"Message {message_id} non-owned attachment lacks exact owner/source/DOM evidence"
                )
            rows.append(
                {
                    "message_id": message_id,
                    "attachment_id": attachment_id,
                    "relation_type": str(raw.get("relation_type") or raw.get("ownership")),
                    "ownership_status": raw.get("ownership_status"),
                    "ownership_evidence": copy.deepcopy(raw.get("ownership_evidence")),
                    "source_channel_id": parsed["source_channel_id"],
                    "discord_url": parsed["url"],
                    "archive_requested": False,
                    "raw_attachment_metadata": copy.deepcopy(raw),
                }
            )
    return sorted(rows, key=lambda row: (row["message_id"], row["attachment_id"]))


def discover_entries(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for message in corpus_messages(corpus):
        message_id = str(message.get("message_id") or "").strip()
        if not DISCORD_ID_RE.fullmatch(message_id):
            raise AttachmentArchiveError("Every attachment owner must have an exact Discord message ID")
        raw_attachments = message.get("attachments") or []
        if not isinstance(raw_attachments, list):
            raise AttachmentArchiveError(f"Message {message_id} attachments is not an array")
        for raw in raw_attachments:
            if not isinstance(raw, dict):
                raise AttachmentArchiveError(f"Message {message_id} has a non-object attachment")
            relation = exact_attachment_relation(raw, message_id=message_id)
            if relation != "owned":
                continue
            attachment_id = str(raw.get("attachment_id") or raw.get("id") or "").strip()
            if not DISCORD_ID_RE.fullmatch(attachment_id):
                raise AttachmentArchiveError(
                    f"Message {message_id} attachment lacks an exact Discord attachment ID"
                )
            previous_owner = seen.get(attachment_id)
            if previous_owner and previous_owner != message_id:
                raise AttachmentArchiveError(
                    f"Attachment {attachment_id} is owned by multiple messages"
                )
            seen[attachment_id] = message_id
            url = select_attachment_url(
                raw,
                matching_message_attachment_urls(message, attachment_id),
                expected_attachment_id=attachment_id,
            )
            url_data = parse_discord_attachment_url(url, expected_attachment_id=attachment_id)
            ownership_evidence = raw.get("ownership_evidence") or {}
            if str(ownership_evidence.get("owner_channel_id") or "") != url_data["source_channel_id"]:
                raise AttachmentArchiveError(
                    f"Message {message_id} owned attachment channel disagrees with ownership evidence"
                )
            filename = safe_filename(
                raw.get("filename") or raw.get("name") or url_data["url_filename"],
                fallback=f"attachment-{attachment_id}",
            )
            local_path = PurePosixPath(
                "attachments",
                url_data["source_channel_id"],
                message_id,
                f"{attachment_id}_{filename}",
            ).as_posix()
            declared_size = raw.get("size") if isinstance(raw.get("size"), int) else raw.get("byte_size")
            declared_mime = str(raw.get("content_type") or raw.get("mime_type") or "").strip() or None
            entries.append(
                {
                    "request_id": stable_id("discord-attachment", message_id, attachment_id),
                    "message_id": message_id,
                    "attachment_id": attachment_id,
                    "attachment_id_exact": True,
                    "source_channel_id": url_data["source_channel_id"],
                    "source_channel_id_basis": "discord_attachment_cdn_path_not_container_identity",
                    "filename": filename,
                    "discord_url": url_data["url"],
                    "url_host": url_data["url_host"],
                    "url_path": url_data["url_path"],
                    "local_package_path": local_path,
                    "declared_byte_size": declared_size if isinstance(declared_size, int) and declared_size >= 0 else None,
                    "declared_mime_type": declared_mime,
                    "capture_status": "pending",
                    "terminal": False,
                    "attempt_count": 0,
                    "attempts": [],
                    "content_sha256": None,
                    "byte_size": None,
                    "mime_type": declared_mime,
                    "captured_at_utc": None,
                    "failure_code": None,
                    "failure_detail": None,
                    "extraction_status": "not_attempted",
                    "extraction_artifacts": [],
                    "chart_claim_eligible": False,
                    "source_present": True,
                    "raw_attachment_metadata": copy.deepcopy(raw),
                }
            )
    return sorted(entries, key=lambda row: (row["message_id"], row["attachment_id"]))


def manifest_policy() -> dict[str, Any]:
    return {
        "allowed_hosts": sorted(ALLOWED_HOSTS),
        "external_links_fetched": False,
        "credentials_or_browser_storage_inspected": False,
        "network_requests_performed_by_archiver": False,
        "browser_response_contract": "discord_attachment_browser_response_v1",
        "minimum_terminal_failure_attempts": MIN_TERMINAL_FAILURE_ATTEMPTS,
        "ownership_rule": (
            "Only attachment rows carrying exact owned-message DOM evidence are requested. "
            "Copied or embedded Discord CDN media remains in non_owned_attachments with its "
            "evidence and is never fetched. Missing or ambiguous ownership blocks planning."
        ),
        "literal_release_rule": (
            "Every discovered Discord-owned attachment must reach a terminal disposition. "
            "An available response must be archived with verified bytes and SHA-256. A deleted "
            "attachment may remain unavailable only with substantive Discord/HTTP evidence. "
            "A failed state may be recorded only after the minimum documented attempts, but it "
            "is a degraded working result and blocks literal release and final packaging."
        ),
        "chart_claim_rule": (
            "Attachment presence, filenames, alt labels, and failed OCR cannot establish chart "
            "geometry or setup facts. Chart-dependent claims remain unresolved unless a complete "
            "or partial local extraction artifact has exact attachment provenance and verified "
            "local bytes."
        ),
    }


def make_manifest(corpus_path: Path, corpus: dict[str, Any]) -> dict[str, Any]:
    scope = corpus.get("scope") if isinstance(corpus.get("scope"), dict) else {}
    now = utc_now()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "guild_id": str(scope.get("guild_id") or corpus.get("guild_id") or ""),
        "source_scope": "discord_only",
        "outside_sources_used": 0,
        "source_corpus": {
            "path": corpus_path.name,
            "path_scope": "build_input_filename_only",
            "sha256": sha256_file(corpus_path),
            "artifact_type": corpus.get("artifact_type"),
        },
        "archive_root_package_relative": ".",
        "created_at_utc": now,
        "updated_at_utc": now,
        "status": "planned",
        "policy": manifest_policy(),
        "entries": discover_entries(corpus),
        "non_owned_attachments": discover_non_owned_entries(corpus),
        "counts": {},
        "release_gate": {},
    }
    refresh_manifest(manifest)
    return manifest


def entry_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("message_id") or ""), str(row.get("attachment_id") or "")


def reconcile_manifest(
    existing: dict[str, Any], corpus_path: Path, corpus: dict[str, Any]
) -> dict[str, Any]:
    validate_manifest_structure(existing, require_terminal=False)
    discovered = discover_entries(corpus)
    old_by_key = {entry_key(row): row for row in existing["entries"]}
    new_by_key = {entry_key(row): row for row in discovered}
    stale = sorted(set(old_by_key) - set(new_by_key))
    if stale:
        raise AttachmentArchiveError(
            "Reconciliation would drop previously catalogued attachments: "
            + ", ".join(f"{message}/{attachment}" for message, attachment in stale[:10])
        )
    merged: list[dict[str, Any]] = []
    for current in discovered:
        old = old_by_key.get(entry_key(current))
        if old is None:
            merged.append(current)
            continue
        if old.get("local_package_path") != current.get("local_package_path"):
            raise AttachmentArchiveError("Attachment package path changed during reconciliation")
        if (
            old.get("url_host") != current.get("url_host")
            or old.get("url_path") != current.get("url_path")
        ):
            raise AttachmentArchiveError(
                "Attachment planned host/path changed during reconciliation"
            )
        preserved = copy.deepcopy(old)
        preserved.update(
            {
                "discord_url": current["discord_url"],
                "url_host": current["url_host"],
                "url_path": current["url_path"],
                "declared_byte_size": current["declared_byte_size"],
                "declared_mime_type": current["declared_mime_type"],
                "raw_attachment_metadata": current["raw_attachment_metadata"],
                "source_present": True,
            }
        )
        merged.append(preserved)
    result = copy.deepcopy(existing)
    result["source_corpus"] = {
        "path": corpus_path.name,
        "path_scope": "build_input_filename_only",
        "sha256": sha256_file(corpus_path),
        "artifact_type": corpus.get("artifact_type"),
    }
    result["guild_id"] = str(
        (corpus.get("scope") or {}).get("guild_id") or corpus.get("guild_id") or ""
    )
    result["entries"] = sorted(merged, key=entry_key)
    result["non_owned_attachments"] = discover_non_owned_entries(corpus)
    result["updated_at_utc"] = utc_now()
    refresh_manifest(result)
    return result


def create_or_resume_manifest(
    corpus_path: Path, manifest_path: Path, *, reconcile: bool = False
) -> dict[str, Any]:
    corpus_path = corpus_path.resolve()
    corpus = load_json_object(corpus_path, label="source corpus")
    if not manifest_path.exists():
        manifest = make_manifest(corpus_path, corpus)
    else:
        existing = load_json_object(manifest_path, label="attachment manifest")
        source = existing.get("source_corpus") if isinstance(existing.get("source_corpus"), dict) else {}
        current_hash = sha256_file(corpus_path)
        if source.get("sha256") == current_hash:
            manifest = existing
            validate_manifest_structure(manifest, require_terminal=False)
            refresh_manifest(manifest)
        elif reconcile:
            manifest = reconcile_manifest(existing, corpus_path, corpus)
        else:
            raise AttachmentArchiveError(
                "Source corpus changed; use --reconcile only after reviewing the changed attachment set"
            )
    write_json_atomic(manifest_path, manifest)
    return manifest


def refresh_manifest(manifest: dict[str, Any]) -> None:
    entries = manifest.get("entries") if isinstance(manifest.get("entries"), list) else []
    non_owned = (
        manifest.get("non_owned_attachments")
        if isinstance(manifest.get("non_owned_attachments"), list)
        else []
    )
    states = Counter(str(row.get("capture_status") or "") for row in entries if isinstance(row, dict))
    terminal_count = sum(bool(row.get("terminal")) for row in entries if isinstance(row, dict))
    byte_complete = states["downloaded"] == len(entries)
    terminal_coverage_complete = terminal_count == len(entries) and all(
        str(row.get("capture_status")) in TERMINAL_CAPTURE_STATES for row in entries
    )
    literal_release_complete = terminal_coverage_complete and states["failed"] == 0
    manifest["counts"] = {
        "total": len(entries),
        "non_owned_not_requested": len(non_owned),
        "pending": states["pending"],
        "downloaded": states["downloaded"],
        "unavailable": states["unavailable"],
        "failed": states["failed"],
        "terminal": terminal_count,
        "extraction_complete": sum(
            str(row.get("extraction_status")) == "complete" for row in entries
        ),
        "extraction_partial": sum(
            str(row.get("extraction_status")) == "partial" for row in entries
        ),
        "extraction_failed": sum(
            str(row.get("extraction_status")) == "failed" for row in entries
        ),
    }
    manifest["status"] = (
        "complete"
        if literal_release_complete
        else "degraded"
        if terminal_coverage_complete
        else "in_progress"
        if any(states[state] for state in TERMINAL_CAPTURE_STATES)
        else "planned"
    )
    manifest["release_gate"] = {
        "gate": "discord_attachment_terminal_coverage",
        "passed": literal_release_complete,
        "terminal_coverage_complete": terminal_coverage_complete,
        "literal_release_complete": literal_release_complete,
        "byte_complete": byte_complete,
        "all_available_bytes_required": True,
        "terminal_unavailable_allowed": True,
        "terminal_failed_release_allowed": False,
        "pending_count": states["pending"],
        "unavailable_count": states["unavailable"],
        "failed_count": states["failed"],
    }


def validate_attempt(attempt: dict[str, Any], *, index: int) -> None:
    parse_utc(attempt.get("attempted_at_utc"), label=f"attempt[{index}].attempted_at_utc")
    status = str(attempt.get("status") or "")
    if status not in TERMINAL_CAPTURE_STATES:
        raise AttachmentArchiveError(f"attempt[{index}] has invalid status")
    if attempt.get("outside_sources_used") not in {0, False}:
        raise AttachmentArchiveError(f"attempt[{index}] used an outside source")
    if attempt.get("credentials_or_browser_storage_inspected") not in {0, False}:
        raise AttachmentArchiveError(f"attempt[{index}] inspected credential or storage state")
    if status == "failed" and substantive_failure_detail(attempt.get("error_detail")) is None:
        raise AttachmentArchiveError(
            f"attempt[{index}] failed without substantive error_detail"
        )


def validate_extraction_artifact(
    artifact: dict[str, Any], *, attachment_id: str, index: int
) -> None:
    extraction_id = str(artifact.get("extraction_id") or "").strip()
    if not extraction_id:
        raise AttachmentArchiveError(
            f"Attachment {attachment_id} extraction[{index}] lacks extraction_id"
        )
    method = str(artifact.get("method") or "").strip()
    if not method:
        raise AttachmentArchiveError(
            f"Attachment {attachment_id} extraction[{index}] lacks method"
        )
    parse_utc(
        artifact.get("created_at_utc"),
        label=f"attachment[{attachment_id}].extraction[{index}].created_at_utc",
    )
    if artifact.get("outside_sources_used") not in {0, False}:
        raise AttachmentArchiveError(
            f"Attachment {attachment_id} extraction[{index}] used an outside source"
        )
    status = str(artifact.get("status") or "")
    if status not in SUCCESSFUL_EXTRACTION_STATES | {"failed"}:
        raise AttachmentArchiveError(
            f"Attachment {attachment_id} extraction[{index}] has invalid status"
        )
    confidence = artifact.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= float(confidence) <= 1
    ):
        raise AttachmentArchiveError(
            f"Attachment {attachment_id} extraction[{index}] confidence must be null or 0..1"
        )
    local_path = artifact.get("local_package_path")
    digest = artifact.get("content_sha256")
    size = artifact.get("byte_size")
    if status in SUCCESSFUL_EXTRACTION_STATES:
        relative = validate_package_relative_path(
            local_path,
            label=f"attachment[{attachment_id}].extraction[{index}].local_package_path",
        )
        parts = PurePosixPath(relative).parts
        if len(parts) < 4 or parts[:2] != ("attachments", "extractions"):
            raise AttachmentArchiveError(
                f"Attachment {attachment_id} extraction[{index}] path is outside attachments/extractions"
            )
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise AttachmentArchiveError(
                f"Attachment {attachment_id} extraction[{index}] lacks SHA-256"
            )
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise AttachmentArchiveError(
                f"Attachment {attachment_id} extraction[{index}] lacks nonempty bytes"
            )
    else:
        if any(value is not None for value in (local_path, digest, size)):
            raise AttachmentArchiveError(
                f"Failed extraction {extraction_id} cannot claim a local artifact"
            )
        if substantive_failure_detail(artifact.get("failure_detail")) is None:
            raise AttachmentArchiveError(
                f"Failed extraction {extraction_id} lacks substantive failure_detail"
            )


def validate_entry(row: dict[str, Any], *, require_terminal: bool) -> None:
    message_id = str(row.get("message_id") or "")
    attachment_id = str(row.get("attachment_id") or "")
    if not DISCORD_ID_RE.fullmatch(message_id) or not DISCORD_ID_RE.fullmatch(attachment_id):
        raise AttachmentArchiveError("Manifest entry lacks exact message/attachment IDs")
    if row.get("attachment_id_exact") is not True:
        raise AttachmentArchiveError("Manifest entry attachment_id_exact must be true")
    url = parse_discord_attachment_url(row.get("discord_url"), expected_attachment_id=attachment_id)
    if row.get("url_host") != url["url_host"] or row.get("url_path") != url["url_path"]:
        raise AttachmentArchiveError("Manifest entry URL fields disagree")
    if row.get("source_channel_id") != url["source_channel_id"]:
        raise AttachmentArchiveError("Manifest entry source channel does not match URL path")
    validate_package_relative_path(row.get("local_package_path"), label="local_package_path")
    declared_size = row.get("declared_byte_size")
    if declared_size is not None and (
        not isinstance(declared_size, int)
        or isinstance(declared_size, bool)
        or declared_size < 0
    ):
        raise AttachmentArchiveError("Attachment declared_byte_size is invalid")
    state = str(row.get("capture_status") or "")
    if state not in CAPTURE_STATES:
        raise AttachmentArchiveError(f"Invalid attachment capture status {state!r}")
    attempts = row.get("attempts")
    if not isinstance(attempts, list) or any(not isinstance(item, dict) for item in attempts):
        raise AttachmentArchiveError("Attachment attempts must be an array of objects")
    if row.get("attempt_count") != len(attempts):
        raise AttachmentArchiveError("Attachment attempt_count does not match attempts")
    for index, attempt in enumerate(attempts):
        validate_attempt(attempt, index=index)
        if attempt.get("attempt_number") != index + 1:
            raise AttachmentArchiveError("Attachment attempts are not sequentially numbered")
        if (
            attempt.get("response_url_host") != url["url_host"]
            or attempt.get("response_url_path") != url["url_path"]
        ):
            raise AttachmentArchiveError(
                "Attachment attempt URL does not match the planned host/path"
            )
    terminal = row.get("terminal") is True
    if require_terminal and not terminal:
        raise AttachmentArchiveError(f"Attachment {attachment_id} is not terminal")
    if terminal != (state in TERMINAL_CAPTURE_STATES):
        raise AttachmentArchiveError("Capture state and terminal flag disagree")
    digest = row.get("content_sha256")
    size = row.get("byte_size")
    if state == "downloaded":
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise AttachmentArchiveError("Downloaded attachment lacks SHA-256")
        if not isinstance(size, int) or size < 0:
            raise AttachmentArchiveError("Downloaded attachment lacks byte size")
        if isinstance(declared_size, int) and size != declared_size:
            raise AttachmentArchiveError(
                "Downloaded attachment byte size disagrees with Discord metadata"
            )
        if not attempts or attempts[-1].get("status") != "downloaded":
            raise AttachmentArchiveError("Downloaded attachment lacks a successful final attempt")
    elif digest is not None or size is not None:
        raise AttachmentArchiveError("Non-downloaded attachment cannot claim archived bytes")
    if state == "unavailable":
        final = attempts[-1] if attempts else {}
        http_status = final.get("http_status")
        error_code = str(final.get("error_code") or "")
        if http_status not in UNAVAILABLE_HTTP_STATUSES and error_code not in UNAVAILABLE_ERROR_CODES:
            raise AttachmentArchiveError("Unavailable attachment lacks terminal unavailability evidence")
        if (
            http_status not in UNAVAILABLE_HTTP_STATUSES
            and substantive_failure_detail(final.get("error_detail")) is None
        ):
            raise AttachmentArchiveError(
                "Discord UI unavailability requires substantive error_detail"
            )
    if state == "failed" and terminal and len(attempts) < MIN_TERMINAL_FAILURE_ATTEMPTS:
        raise AttachmentArchiveError("Terminal failed attachment has too few documented attempts")
    if state in TERMINAL_CAPTURE_STATES and (
        not attempts or attempts[-1].get("status") != state
    ):
        raise AttachmentArchiveError("Terminal capture state does not match the final attempt")
    if state == "pending" and any(
        attempt.get("status") != "failed" for attempt in attempts
    ):
        raise AttachmentArchiveError("Pending attachment may contain only failed attempts")
    extraction = str(row.get("extraction_status") or "")
    if extraction not in EXTRACTION_STATES:
        raise AttachmentArchiveError("Invalid extraction status")
    artifacts = row.get("extraction_artifacts")
    if not isinstance(artifacts, list) or any(not isinstance(item, dict) for item in artifacts):
        raise AttachmentArchiveError("extraction_artifacts must be an array of objects")
    for index, artifact in enumerate(artifacts):
        validate_extraction_artifact(
            artifact, attachment_id=attachment_id, index=index
        )
    if extraction == "not_attempted" and artifacts:
        raise AttachmentArchiveError(
            "not_attempted extraction status cannot have extraction artifacts"
        )
    if extraction != "not_attempted" and (
        not artifacts or artifacts[-1].get("status") != extraction
    ):
        raise AttachmentArchiveError(
            "Attachment extraction_status does not match its final extraction record"
        )
    if row.get("chart_claim_eligible") is not False:
        raise AttachmentArchiveError(
            "Archiving/extraction status alone cannot automatically mark a chart claim eligible"
        )


def validate_manifest_structure(
    manifest: dict[str, Any], *, require_terminal: bool = False
) -> dict[str, Any]:
    if manifest.get("artifact_type") != ARTIFACT_TYPE or manifest.get("schema_version") != SCHEMA_VERSION:
        raise AttachmentArchiveError("Unsupported attachment manifest type or schema")
    if manifest.get("source_scope") != "discord_only" or manifest.get("outside_sources_used") not in {0, False}:
        raise AttachmentArchiveError("Attachment manifest is not Discord-only")
    policy = manifest.get("policy")
    if not isinstance(policy, dict):
        raise AttachmentArchiveError("Attachment manifest policy is missing")
    if policy.get("external_links_fetched") is not False:
        raise AttachmentArchiveError("Attachment manifest permits external links")
    if policy.get("credentials_or_browser_storage_inspected") is not False:
        raise AttachmentArchiveError("Attachment manifest permits credential/storage inspection")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
        raise AttachmentArchiveError("Attachment manifest entries must be objects")
    non_owned = manifest.get("non_owned_attachments")
    if not isinstance(non_owned, list) or any(not isinstance(row, dict) for row in non_owned):
        raise AttachmentArchiveError("Attachment manifest non_owned_attachments must be objects")
    keys: set[tuple[str, str]] = set()
    ids: set[str] = set()
    paths: set[str] = set()
    extraction_ids: set[str] = set()
    for row in entries:
        validate_entry(row, require_terminal=require_terminal)
        key = entry_key(row)
        row_path_key = str(row["local_package_path"]).casefold()
        if key in keys or key[1] in ids or row_path_key in paths:
            raise AttachmentArchiveError("Duplicate attachment key, ID, or local path")
        keys.add(key)
        ids.add(key[1])
        paths.add(row_path_key)
        for artifact in row["extraction_artifacts"]:
            extraction_id = str(artifact["extraction_id"])
            if extraction_id in extraction_ids:
                raise AttachmentArchiveError("Duplicate extraction_id in attachment manifest")
            extraction_ids.add(extraction_id)
            local_path = artifact.get("local_package_path")
            if local_path:
                path_key = str(local_path).casefold()
                if path_key in paths:
                    raise AttachmentArchiveError(
                        "Duplicate attachment/extraction local package path"
                    )
                paths.add(path_key)
    for row in non_owned:
        message_id = str(row.get("message_id") or "")
        attachment_id = str(row.get("attachment_id") or "")
        evidence = row.get("ownership_evidence")
        if (
            not DISCORD_ID_RE.fullmatch(message_id)
            or not DISCORD_ID_RE.fullmatch(attachment_id)
            or row.get("archive_requested") is not False
            or row.get("ownership_status") != "non_owned_exact"
            or not isinstance(evidence, dict)
            or evidence.get("exact") is not True
            or str(evidence.get("owner_message_id") or "") != message_id
            or not DISCORD_ID_RE.fullmatch(str(evidence.get("owner_channel_id") or ""))
            or str(evidence.get("source_channel_id") or "")
            != str(row.get("source_channel_id") or "")
            or not str(evidence.get("dom_relation") or "").strip()
        ):
            raise AttachmentArchiveError("Invalid auditable non-owned attachment row")
        if attachment_id in ids:
            raise AttachmentArchiveError("Attachment ID appears in both owned and non-owned sets")
    return {
        "entry_count": len(entries),
        "non_owned_count": len(non_owned),
        "terminal_required": require_terminal,
    }


def verify_archive(
    manifest: dict[str, Any], archive_root: Path, *, require_terminal: bool = False
) -> dict[str, Any]:
    validate_manifest_structure(manifest, require_terminal=require_terminal)
    problems: list[dict[str, Any]] = []
    verified_bytes = 0
    verified_extraction_ids: list[str] = []
    expected_extraction_count = 0
    for row in manifest["entries"]:
        if row["capture_status"] == "downloaded":
            path = resolve_under(
                archive_root,
                row["local_package_path"],
                label="attachment local_package_path",
            )
            if not path.is_file():
                problems.append(
                    {"attachment_id": row["attachment_id"], "reason": "file_missing"}
                )
            else:
                size = path.stat().st_size
                digest = sha256_file(path)
                if size != row["byte_size"]:
                    problems.append(
                        {
                            "attachment_id": row["attachment_id"],
                            "reason": "byte_size_mismatch",
                        }
                    )
                if digest != row["content_sha256"]:
                    problems.append(
                        {
                            "attachment_id": row["attachment_id"],
                            "reason": "sha256_mismatch",
                        }
                    )
                if size == row["byte_size"] and digest == row["content_sha256"]:
                    verified_bytes += 1
        for artifact in row["extraction_artifacts"]:
            if artifact["status"] not in SUCCESSFUL_EXTRACTION_STATES:
                continue
            expected_extraction_count += 1
            extraction_id = str(artifact["extraction_id"])
            path = resolve_under(
                archive_root,
                artifact["local_package_path"],
                label="extraction local_package_path",
            )
            if not path.is_file():
                problems.append(
                    {
                        "attachment_id": row["attachment_id"],
                        "extraction_id": extraction_id,
                        "reason": "extraction_file_missing",
                    }
                )
                continue
            size = path.stat().st_size
            digest = sha256_file(path)
            if size != artifact["byte_size"]:
                problems.append(
                    {
                        "attachment_id": row["attachment_id"],
                        "extraction_id": extraction_id,
                        "reason": "extraction_byte_size_mismatch",
                    }
                )
            if digest != artifact["content_sha256"]:
                problems.append(
                    {
                        "attachment_id": row["attachment_id"],
                        "extraction_id": extraction_id,
                        "reason": "extraction_sha256_mismatch",
                    }
                )
            if size == artifact["byte_size"] and digest == artifact["content_sha256"]:
                verified_extraction_ids.append(extraction_id)
    refresh_manifest(manifest)
    gate = manifest["release_gate"]
    passed = not problems and (
        not require_terminal or gate["terminal_coverage_complete"]
    )
    return {
        "status": "passed" if passed else "failed",
        "manifest_structure_valid": True,
        "terminal_required": require_terminal,
        "terminal_coverage_complete": gate["terminal_coverage_complete"],
        "literal_release_complete": gate["literal_release_complete"],
        "byte_complete": gate["byte_complete"],
        "verified_download_count": verified_bytes,
        "expected_extraction_artifact_count": expected_extraction_count,
        "verified_extraction_artifact_count": len(verified_extraction_ids),
        "verified_extraction_ids": sorted(verified_extraction_ids),
        "problem_count": len(problems),
        "problems": problems,
        "counts": copy.deepcopy(manifest["counts"]),
    }


def pending_requests(manifest: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    validate_manifest_structure(manifest, require_terminal=False)
    if limit < 1:
        raise AttachmentArchiveError("limit must be positive")
    requests: list[dict[str, Any]] = []
    for row in manifest["entries"]:
        if row["terminal"]:
            continue
        requests.append(
            {
                "contract": "discord_attachment_browser_request_v1",
                "request_id": row["request_id"],
                "guild_id": manifest.get("guild_id"),
                "message_id": row["message_id"],
                "attachment_id": row["attachment_id"],
                "discord_url": row["discord_url"],
                "expected_url_host": row["url_host"],
                "expected_url_path": row["url_path"],
                "local_package_path": row["local_package_path"],
                "attempt_number": row["attempt_count"] + 1,
                "instructions": (
                    "Fetch only this exact Discord-hosted URL in the authenticated browser. "
                    "Do not inspect cookies, storage, profiles, or credentials; do not follow "
                    "redirects to a non-Discord host. Return the response envelope only."
                ),
            }
        )
        if len(requests) >= limit:
            break
    return requests


def decode_response_bytes(response: dict[str, Any], *, staging_root: Path | None) -> bytes:
    body_base64 = response.get("body_base64")
    staged_path = response.get("staged_file")
    if bool(body_base64 is not None) == bool(staged_path is not None):
        raise AttachmentArchiveError("Downloaded response requires exactly one byte transport")
    if body_base64 is not None:
        if not isinstance(body_base64, str):
            raise AttachmentArchiveError("body_base64 must be a string")
        try:
            return base64.b64decode(body_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AttachmentArchiveError("body_base64 is invalid") from exc
    if staging_root is None:
        raise AttachmentArchiveError("--staging-root is required for staged_file transport")
    path = resolve_under(staging_root, staged_path, label="staged_file")
    if not path.is_file():
        raise AttachmentArchiveError("staged_file does not exist")
    return path.read_bytes()


def write_bytes_atomic(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    try:
        with temporary.open("xb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def locate_entry(manifest: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    request_id = str(response.get("request_id") or "")
    message_id = str(response.get("message_id") or "")
    attachment_id = str(response.get("attachment_id") or "")
    matches = [row for row in manifest["entries"] if row.get("request_id") == request_id]
    if len(matches) != 1:
        raise AttachmentArchiveError("Response request_id is unknown or ambiguous")
    row = matches[0]
    if row["message_id"] != message_id or row["attachment_id"] != attachment_id:
        raise AttachmentArchiveError("Response IDs do not match the planned request")
    if row["terminal"]:
        raise AttachmentArchiveError("Attachment is already terminal")
    return row


def ingest_browser_response(
    manifest: dict[str, Any],
    response: dict[str, Any],
    archive_root: Path,
    *,
    staging_root: Path | None = None,
) -> dict[str, Any]:
    validate_manifest_structure(manifest, require_terminal=False)
    if response.get("contract") != "discord_attachment_browser_response_v1":
        raise AttachmentArchiveError("Unsupported browser response contract")
    if response.get("outside_sources_used") not in {0, False}:
        raise AttachmentArchiveError("Browser response used an outside source")
    if response.get("credentials_or_browser_storage_inspected") not in {0, False}:
        raise AttachmentArchiveError("Browser response inspected credential/storage state")
    row = locate_entry(manifest, response)
    if not str(response.get("final_url") or "").strip():
        raise AttachmentArchiveError("Browser response final_url is required")
    final_url = parse_discord_attachment_url(
        response.get("final_url"),
        expected_attachment_id=row["attachment_id"],
    )
    planned_url = parse_discord_attachment_url(
        row["discord_url"], expected_attachment_id=row["attachment_id"]
    )
    if any(
        final_url[field] != planned_url[field]
        for field in (
            "url_host",
            "url_path",
            "source_channel_id",
            "attachment_id",
            "url_filename",
        )
    ):
        raise AttachmentArchiveError(
            "Browser response final_url host/path/channel/attachment/filename "
            "does not exactly match the planned request"
        )
    status = str(response.get("status") or "")
    if status not in TERMINAL_CAPTURE_STATES:
        raise AttachmentArchiveError("Browser response status must be downloaded, unavailable, or failed")
    attempted_at = parse_utc(response.get("attempted_at_utc") or utc_now(), label="attempted_at_utc")
    http_status = response.get("http_status")
    if http_status is not None and (not isinstance(http_status, int) or not 100 <= http_status <= 599):
        raise AttachmentArchiveError("http_status must be an integer from 100 through 599")
    attempt = {
        "attempt_number": row["attempt_count"] + 1,
        "attempted_at_utc": attempted_at,
        "status": status,
        "http_status": http_status,
        "error_code": str(response.get("error_code") or "").strip() or None,
        "error_detail": substantive_failure_detail(response.get("error_detail"))
        if status == "failed"
        else str(response.get("error_detail") or "").strip() or None,
        "response_mime_type": str(response.get("mime_type") or "").strip() or None,
        "response_url_host": final_url["url_host"],
        "response_url_path": final_url["url_path"],
        "outside_sources_used": 0,
        "credentials_or_browser_storage_inspected": False,
    }
    validate_attempt(attempt, index=row["attempt_count"])
    next_attempt_count = row["attempt_count"] + 1
    if status == "downloaded":
        if http_status is not None and not 200 <= http_status <= 299:
            raise AttachmentArchiveError("Downloaded response must have a 2xx HTTP status")
        value = decode_response_bytes(response, staging_root=staging_root)
        digest = sha256_bytes(value)
        declared_hash = str(response.get("sha256") or "").strip().casefold()
        if declared_hash and declared_hash != digest:
            raise AttachmentArchiveError("Browser response SHA-256 does not match response bytes")
        declared_size = response.get("byte_size")
        if declared_size is not None and declared_size != len(value):
            raise AttachmentArchiveError("Browser response byte_size does not match response bytes")
        source_declared_size = row.get("declared_byte_size")
        if (
            isinstance(source_declared_size, int)
            and not isinstance(source_declared_size, bool)
            and source_declared_size != len(value)
        ):
            raise AttachmentArchiveError(
                "Downloaded bytes do not match the Discord attachment's declared size"
            )
        target = resolve_under(
            archive_root, row["local_package_path"], label="attachment local_package_path"
        )
        if target.exists():
            if target.is_file() and target.stat().st_size == len(value) and sha256_file(target) == digest:
                pass
            else:
                raise AttachmentArchiveError("Existing attachment path has different bytes")
        else:
            write_bytes_atomic(target, value)
        row.update(
            {
                "capture_status": "downloaded",
                "terminal": True,
                "content_sha256": digest,
                "byte_size": len(value),
                "mime_type": attempt["response_mime_type"]
                or row.get("declared_mime_type")
                or mimetypes.guess_type(row["filename"])[0]
                or "application/octet-stream",
                "captured_at_utc": attempted_at,
                "failure_code": None,
                "failure_detail": None,
            }
        )
    else:
        if response.get("body_base64") is not None or response.get("staged_file") is not None:
            raise AttachmentArchiveError("Failed/unavailable response cannot include archive bytes")
        terminal_requested = response.get("terminal") is True
        if status == "unavailable":
            if http_status not in UNAVAILABLE_HTTP_STATUSES and attempt["error_code"] not in UNAVAILABLE_ERROR_CODES:
                raise AttachmentArchiveError("Unavailable response lacks 404/410 or an allowed Discord reason")
            if (
                http_status not in UNAVAILABLE_HTTP_STATUSES
                and substantive_failure_detail(attempt["error_detail"]) is None
            ):
                raise AttachmentArchiveError(
                    "Discord UI unavailability requires substantive error_detail"
                )
            terminal = True
        else:
            terminal = (
                terminal_requested
                and next_attempt_count >= MIN_TERMINAL_FAILURE_ATTEMPTS
            )
            if terminal_requested and not terminal:
                raise AttachmentArchiveError(
                    f"Terminal failed status requires {MIN_TERMINAL_FAILURE_ATTEMPTS} documented attempts"
                )
        row.update(
            {
                "capture_status": status if terminal else "pending",
                "terminal": terminal,
                "content_sha256": None,
                "byte_size": None,
                "captured_at_utc": attempted_at if terminal else None,
                "failure_code": attempt["error_code"],
                "failure_detail": attempt["error_detail"],
            }
        )
    row["attempts"].append(attempt)
    row["attempt_count"] = next_attempt_count
    manifest["updated_at_utc"] = utc_now()
    refresh_manifest(manifest)
    validate_manifest_structure(manifest, require_terminal=False)
    return row


def record_extraction(
    manifest: dict[str, Any],
    extraction: dict[str, Any],
    archive_root: Path,
    *,
    staging_root: Path | None = None,
) -> dict[str, Any]:
    validate_manifest_structure(manifest, require_terminal=False)
    if extraction.get("outside_sources_used") not in {None, 0, False}:
        raise AttachmentArchiveError("Extraction used an outside source")
    attachment_id = str(extraction.get("attachment_id") or "")
    matches = [row for row in manifest["entries"] if row["attachment_id"] == attachment_id]
    if len(matches) != 1:
        raise AttachmentArchiveError("Extraction attachment_id is unknown or ambiguous")
    row = matches[0]
    if row.get("capture_status") != "downloaded" or row.get("terminal") is not True:
        raise AttachmentArchiveError(
            "Local extraction requires a downloaded, terminal source attachment"
        )
    source_path = resolve_under(
        archive_root,
        row["local_package_path"],
        label="source attachment local_package_path",
    )
    if (
        not source_path.is_file()
        or source_path.stat().st_size != row.get("byte_size")
        or sha256_file(source_path) != row.get("content_sha256")
    ):
        raise AttachmentArchiveError(
            "Local extraction source attachment failed size/SHA-256 verification"
        )
    status = str(extraction.get("status") or "")
    if status not in {"complete", "partial", "failed"}:
        raise AttachmentArchiveError("Extraction status must be complete, partial, or failed")
    method = str(extraction.get("method") or "").strip()
    if not method:
        raise AttachmentArchiveError("Extraction method is required")
    created_at = parse_utc(extraction.get("created_at_utc") or utc_now(), label="created_at_utc")
    confidence = extraction.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= float(confidence) <= 1
    ):
        raise AttachmentArchiveError("Extraction confidence must be null or 0..1")
    failure_detail = (
        substantive_failure_detail(extraction.get("failure_detail"))
        if status == "failed"
        else str(extraction.get("failure_detail") or "").strip() or None
    )
    if status == "failed" and failure_detail is None:
        raise AttachmentArchiveError(
            "Failed extraction requires substantive failure_detail"
        )
    declared_extracted_text = str(extraction.get("extracted_text") or "")
    if status == "failed" and declared_extracted_text:
        raise AttachmentArchiveError(
            "Failed extraction cannot claim extracted_text without an artifact"
        )
    artifact: dict[str, Any] = {
        "extraction_id": stable_id("attachment-extraction", attachment_id, method, created_at),
        "method": method,
        "status": status,
        "created_at_utc": created_at,
        "local_package_path": None,
        "content_sha256": None,
        "byte_size": None,
        "mime_type": str(extraction.get("mime_type") or "").strip() or None,
        "confidence": float(confidence) if confidence is not None else None,
        "extracted_text": "",
        "failure_code": str(extraction.get("failure_code") or "").strip() or None,
        "failure_detail": failure_detail,
        "outside_sources_used": 0,
    }
    if status in {"complete", "partial"}:
        if staging_root is None:
            raise AttachmentArchiveError("Extraction artifact requires --staging-root")
        staged = resolve_under(staging_root, extraction.get("staged_file"), label="extraction staged_file")
        if not staged.is_file():
            raise AttachmentArchiveError("Extraction staged_file does not exist")
        filename = safe_filename(
            extraction.get("filename") or staged.name,
            fallback=f"{attachment_id}-extraction.txt",
        )
        local_path = PurePosixPath(
            "attachments", "extractions", attachment_id, f"{artifact['extraction_id']}_{filename}"
        ).as_posix()
        target = resolve_under(archive_root, local_path, label="extraction local_package_path")
        value = staged.read_bytes()
        if not value:
            raise AttachmentArchiveError(
                "Complete/partial extraction artifact cannot be empty"
            )
        digest = sha256_bytes(value)
        resolved_mime = (
            artifact["mime_type"]
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )
        textual = resolved_mime.startswith("text/") or Path(filename).suffix.casefold() in {
            ".csv",
            ".json",
            ".md",
            ".txt",
        }
        decoded_text = ""
        if textual:
            try:
                decoded_text = value.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AttachmentArchiveError(
                    "Text extraction artifact is not valid UTF-8"
                ) from exc
        if declared_extracted_text and (
            not textual or declared_extracted_text != decoded_text
        ):
            raise AttachmentArchiveError(
                "extracted_text must exactly match the verified local text artifact"
            )
        if target.exists():
            if not target.is_file() or sha256_file(target) != digest:
                raise AttachmentArchiveError("Existing extraction artifact has different bytes")
        else:
            write_bytes_atomic(target, value)
        artifact.update(
            {
                "local_package_path": local_path,
                "content_sha256": digest,
                "byte_size": len(value),
                "mime_type": resolved_mime,
                "extracted_text": decoded_text,
            }
        )
    validate_extraction_artifact(
        artifact,
        attachment_id=attachment_id,
        index=len(row["extraction_artifacts"]),
    )
    row["extraction_artifacts"].append(artifact)
    row["extraction_status"] = status
    row["chart_claim_eligible"] = False
    manifest["updated_at_utc"] = utc_now()
    refresh_manifest(manifest)
    return artifact


def summarize(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": manifest.get("artifact_type"),
        "status": manifest.get("status"),
        "counts": manifest.get("counts"),
        "release_gate": manifest.get("release_gate"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Subcommand options: --corpus --manifest --reconcile --limit "
            "--archive-root --response --staging-root --extraction --require-terminal"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Catalog exact owned attachments from a corpus")
    plan.add_argument("--corpus", required=True, type=Path)
    plan.add_argument("--manifest", required=True, type=Path)
    plan.add_argument(
        "--reconcile",
        action="store_true",
        help="Reconcile a reviewed growing corpus without dropping prior attachment records",
    )

    next_parser = subparsers.add_parser("next", help="Emit pending browser fetch requests")
    next_parser.add_argument("--manifest", required=True, type=Path)
    next_parser.add_argument("--limit", type=int, default=1)

    ingest = subparsers.add_parser("ingest", help="Ingest one authenticated-browser response")
    ingest.add_argument("--manifest", required=True, type=Path)
    ingest.add_argument("--archive-root", required=True, type=Path)
    ingest.add_argument("--response", required=True, type=Path)
    ingest.add_argument("--staging-root", type=Path)

    extraction = subparsers.add_parser("record-extraction", help="Record a local OCR/manual extraction")
    extraction.add_argument("--manifest", required=True, type=Path)
    extraction.add_argument("--archive-root", required=True, type=Path)
    extraction.add_argument("--extraction", required=True, type=Path)
    extraction.add_argument("--staging-root", type=Path)

    verify = subparsers.add_parser("verify", help="Re-hash archived files and validate terminal state")
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--archive-root", required=True, type=Path)
    verify.add_argument("--require-terminal", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            manifest = create_or_resume_manifest(
                args.corpus, args.manifest, reconcile=args.reconcile
            )
            result = summarize(manifest)
        elif args.command == "next":
            manifest = load_json_object(args.manifest, label="attachment manifest")
            result = {"requests": pending_requests(manifest, limit=args.limit)}
        elif args.command == "ingest":
            manifest = load_json_object(args.manifest, label="attachment manifest")
            response = load_json_object(args.response, label="browser response")
            row = ingest_browser_response(
                manifest,
                response,
                args.archive_root,
                staging_root=args.staging_root,
            )
            write_json_atomic(args.manifest, manifest)
            result = {"entry": row, **summarize(manifest)}
        elif args.command == "record-extraction":
            manifest = load_json_object(args.manifest, label="attachment manifest")
            extraction = load_json_object(args.extraction, label="extraction response")
            artifact = record_extraction(
                manifest,
                extraction,
                args.archive_root,
                staging_root=args.staging_root,
            )
            write_json_atomic(args.manifest, manifest)
            result = {"extraction_artifact": artifact, **summarize(manifest)}
        else:
            manifest = load_json_object(args.manifest, label="attachment manifest")
            result = verify_archive(
                manifest, args.archive_root, require_terminal=args.require_terminal
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("status") != "failed" else 1
    except (AttachmentArchiveError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
