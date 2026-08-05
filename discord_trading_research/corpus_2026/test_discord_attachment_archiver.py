from __future__ import annotations

import base64
import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import discord_attachment_archiver as archive


MESSAGE_ID = "1457078514107941056"
ATTACHMENT_ID = "1457078513864802415"
CHANNEL_ID = "1359593949110472777"
SECOND_MESSAGE_ID = "1457078498500935864"
SECOND_ATTACHMENT_ID = "1457078496911429704"


def corpus(*, second: bool = False) -> dict:
    messages = [
        {
            "message_id": MESSAGE_ID,
            "content_text": "chart",
            "attachments": [
                {
                    "attachment_id": ATTACHMENT_ID,
                    "relation_type": "owned",
                    "ownership_status": "owned_exact",
                    "ownership_evidence": {
                        "schema_version": "1.0.0",
                        "exact": True,
                        "basis": "test_exact_message_accessories",
                        "owner_message_id": MESSAGE_ID,
                        "owner_channel_id": CHANNEL_ID,
                        "source_channel_id": CHANNEL_ID,
                    },
                    "filename": "chart ../ one.png",
                    "content_type": "image/png",
                    "url": (
                        f"https://cdn.discordapp.com/attachments/{CHANNEL_ID}/"
                        f"{ATTACHMENT_ID}/chart%20one.png?ex=signed"
                    ),
                }
            ],
            "links": [
                "https://example.com/never-fetch-me.png",
                (
                    f"https://cdn.discordapp.com/attachments/{CHANNEL_ID}/"
                    f"{ATTACHMENT_ID}/chart%20one.png?ex=signed&hm=token"
                ),
            ],
        }
    ]
    if second:
        messages.append(
            {
                "message_id": SECOND_MESSAGE_ID,
                "attachments": [
                    {
                        "attachment_id": SECOND_ATTACHMENT_ID,
                        "relation_type": "owned",
                        "ownership_status": "owned_exact",
                        "ownership_evidence": {
                            "schema_version": "1.0.0",
                            "exact": True,
                            "basis": "test_exact_message_accessories",
                            "owner_message_id": SECOND_MESSAGE_ID,
                            "owner_channel_id": CHANNEL_ID,
                            "source_channel_id": CHANNEL_ID,
                        },
                        "filename": "second.jpg",
                        "url": (
                            f"https://media.discordapp.net/attachments/{CHANNEL_ID}/"
                            f"{SECOND_ATTACHMENT_ID}/second.jpg"
                        ),
                    }
                ],
            }
        )
    return {
        "schema_version": "2.1.0",
        "artifact_type": "discord_serverwide_corpus_working",
        "scope": {"guild_id": "1167376964680691732"},
        "messages": messages,
        "source_scope": "discord_only",
        "outside_sources_used": 0,
    }


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def response(
    entry: dict,
    *,
    status: str,
    body: bytes | None = None,
    terminal: bool = False,
    http_status: int | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> dict:
    value = {
        "contract": "discord_attachment_browser_response_v1",
        "request_id": entry["request_id"],
        "message_id": entry["message_id"],
        "attachment_id": entry["attachment_id"],
        "final_url": entry["discord_url"],
        "status": status,
        "terminal": terminal,
        "attempted_at_utc": "2026-07-21T05:01:00Z",
        "http_status": http_status,
        "error_code": error_code,
        "error_detail": error_detail,
        "outside_sources_used": 0,
        "credentials_or_browser_storage_inspected": False,
    }
    if body is not None:
        value["body_base64"] = base64.b64encode(body).decode("ascii")
        value["byte_size"] = len(body)
        value["sha256"] = archive.sha256_bytes(body)
        value["mime_type"] = "image/png"
    return value


class AttachmentArchiverTests(unittest.TestCase):
    def build(self, root: Path, *, second: bool = False) -> tuple[Path, Path, dict]:
        corpus_path = root / "corpus.json"
        manifest_path = root / "manifest.json"
        write_json(corpus_path, corpus(second=second))
        manifest = archive.create_or_resume_manifest(corpus_path, manifest_path)
        return corpus_path, manifest_path, manifest

    def test_catalogues_owned_discord_attachments_and_ignores_external_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, _, manifest = self.build(Path(directory))
        self.assertEqual(manifest["counts"]["total"], 1)
        entry = manifest["entries"][0]
        self.assertEqual(entry["message_id"], MESSAGE_ID)
        self.assertEqual(entry["attachment_id"], ATTACHMENT_ID)
        self.assertEqual(entry["url_host"], "cdn.discordapp.com")
        self.assertIn("?ex=signed", entry["discord_url"])
        self.assertNotIn("example.com", json.dumps(manifest))
        self.assertTrue(entry["local_package_path"].startswith("attachments/"))
        self.assertNotIn("..", entry["local_package_path"])
        self.assertFalse(manifest["policy"]["external_links_fetched"])
        self.assertFalse(manifest["policy"]["credentials_or_browser_storage_inspected"])

    def test_rejects_external_or_mismatched_attachment_url(self) -> None:
        external = corpus()
        external["messages"][0]["attachments"][0]["url"] = "https://example.com/file.png"
        with self.assertRaisesRegex(archive.AttachmentArchiveError, "no valid Discord-hosted URL"):
            archive.discover_entries(external)

        mismatch = corpus()
        mismatch["messages"][0]["attachments"][0]["url"] = (
            f"https://cdn.discordapp.com/attachments/{CHANNEL_ID}/"
            f"{SECOND_ATTACHMENT_ID}/file.png"
        )
        with self.assertRaisesRegex(archive.AttachmentArchiveError, "does not match"):
            archive.discover_entries(mismatch)

    def test_ownership_is_fail_closed_and_non_owned_media_is_auditable(self) -> None:
        unresolved = corpus()
        raw = unresolved["messages"][0]["attachments"][0]
        raw.pop("relation_type")
        raw.pop("ownership_status")
        raw.pop("ownership_evidence")
        with self.assertRaisesRegex(archive.AttachmentArchiveError, "ownership is unresolved"):
            archive.discover_entries(unresolved)

        copied = corpus()
        raw = copied["messages"][0]["attachments"][0]
        raw["relation_type"] = "embedded_external"
        raw["ownership_status"] = "non_owned_exact"
        raw["ownership_evidence"] = {
            "schema_version": "1.0.0",
            "exact": True,
            "basis": "discord_cdn_source_channel_differs_from_exact_message_container",
            "owner_message_id": MESSAGE_ID,
            "owner_channel_id": "1329615478716502097",
            "source_channel_id": CHANNEL_ID,
            "dom_relation": "embed_descendant",
        }
        self.assertEqual(archive.discover_entries(copied), [])
        non_owned = archive.discover_non_owned_entries(copied)
        self.assertEqual(len(non_owned), 1)
        self.assertFalse(non_owned[0]["archive_requested"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus_path = root / "corpus.json"
            write_json(corpus_path, copied)
            manifest = archive.make_manifest(corpus_path, copied)
        self.assertEqual(manifest["counts"]["total"], 0)
        self.assertEqual(manifest["counts"]["non_owned_not_requested"], 1)
        self.assertEqual(len(manifest["non_owned_attachments"]), 1)

    def test_same_attachment_id_cannot_be_owned_by_multiple_messages(self) -> None:
        repeated = corpus(second=True)
        second = repeated["messages"][1]["attachments"][0]
        second["attachment_id"] = ATTACHMENT_ID
        second["url"] = (
            f"https://cdn.discordapp.com/attachments/{CHANNEL_ID}/{ATTACHMENT_ID}/chart.png"
        )
        with self.assertRaisesRegex(archive.AttachmentArchiveError, "owned by multiple messages"):
            archive.discover_entries(repeated)

    def test_success_bytes_are_atomic_hashed_and_resume_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus_path, manifest_path, manifest = self.build(root)
            entry = manifest["entries"][0]
            body = b"exact discord attachment bytes"
            archived = root / "archive"
            archive.ingest_browser_response(
                manifest,
                response(entry, status="downloaded", body=body, http_status=200),
                archived,
            )
            archive.write_json_atomic(manifest_path, manifest)
            target = archive.resolve_under(
                archived, entry["local_package_path"], label="test attachment"
            )
            self.assertEqual(target.read_bytes(), body)
            self.assertEqual(entry["byte_size"], len(body))
            self.assertEqual(entry["content_sha256"], archive.sha256_bytes(body))
            self.assertTrue(entry["terminal"])
            self.assertEqual(manifest["status"], "complete")
            self.assertTrue(manifest["release_gate"]["byte_complete"])
            self.assertFalse(list(target.parent.glob("*.partial")))

            resumed = archive.create_or_resume_manifest(corpus_path, manifest_path)
            self.assertEqual(resumed["entries"][0]["content_sha256"], archive.sha256_bytes(body))
            result = archive.verify_archive(resumed, archived, require_terminal=True)
            self.assertEqual(result["status"], "passed")

    def test_terminal_failed_state_requires_three_documented_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, manifest = self.build(root)
            entry = manifest["entries"][0]
            for _ in range(2):
                archive.ingest_browser_response(
                    manifest,
                    response(
                        entry,
                        status="failed",
                        terminal=False,
                        http_status=503,
                        error_code="network_error",
                        error_detail="Discord attachment request returned HTTP 503",
                    ),
                    root / "archive",
                )
                self.assertEqual(entry["capture_status"], "pending")
                self.assertFalse(entry["terminal"])
            archive.ingest_browser_response(
                manifest,
                response(
                    entry,
                    status="failed",
                    terminal=True,
                    http_status=503,
                    error_code="network_error",
                    error_detail="Discord attachment request returned HTTP 503",
                ),
                root / "archive",
            )
            self.assertEqual(entry["capture_status"], "failed")
            self.assertTrue(entry["terminal"])
            self.assertEqual(entry["attempt_count"], 3)
            self.assertEqual(manifest["status"], "degraded")
            self.assertTrue(manifest["release_gate"]["terminal_coverage_complete"])
            self.assertFalse(manifest["release_gate"]["literal_release_complete"])
            self.assertFalse(manifest["release_gate"]["passed"])
            self.assertFalse(manifest["release_gate"]["byte_complete"])
            verification = archive.verify_archive(
                manifest, root / "archive", require_terminal=True
            )
            self.assertEqual(verification["status"], "passed")
            self.assertTrue(verification["terminal_coverage_complete"])
            self.assertFalse(verification["literal_release_complete"])

    def test_failed_attempt_requires_substantive_detail_without_mutating_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, manifest = self.build(root)
            entry = manifest["entries"][0]
            bad = response(
                entry,
                status="failed",
                terminal=False,
                http_status=503,
                error_code="network_error",
                error_detail="failed",
            )
            with self.assertRaisesRegex(archive.AttachmentArchiveError, "substantive"):
                archive.ingest_browser_response(manifest, bad, root / "archive")
            self.assertEqual(entry["attempt_count"], 0)
            self.assertEqual(entry["attempts"], [])

    def test_unavailable_state_requires_terminal_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, manifest = self.build(root)
            entry = manifest["entries"][0]
            bad = response(
                entry,
                status="unavailable",
                terminal=True,
                http_status=403,
                error_code="forbidden",
            )
            with self.assertRaisesRegex(archive.AttachmentArchiveError, "lacks 404/410"):
                archive.ingest_browser_response(manifest, bad, root / "archive")

            manifest = archive.make_manifest(root / "corpus.json", corpus())
            entry = manifest["entries"][0]
            archive.ingest_browser_response(
                manifest,
                response(entry, status="unavailable", terminal=True, http_status=404),
                root / "archive",
            )
            self.assertEqual(entry["capture_status"], "unavailable")
            self.assertTrue(manifest["release_gate"]["passed"])
            self.assertFalse(manifest["release_gate"]["byte_complete"])

            ui_manifest = archive.make_manifest(root / "corpus.json", corpus())
            ui_entry = ui_manifest["entries"][0]
            ui_bad = response(
                ui_entry,
                status="unavailable",
                terminal=True,
                error_code="discord_ui_unavailable",
            )
            with self.assertRaisesRegex(archive.AttachmentArchiveError, "substantive"):
                archive.ingest_browser_response(ui_manifest, ui_bad, root / "archive")

    def test_final_url_must_match_exact_planned_host_and_path(self) -> None:
        mutations = {
            "host": lambda value: value.replace(
                "cdn.discordapp.com", "media.discordapp.net"
            ),
            "channel": lambda value: value.replace(CHANNEL_ID, SECOND_MESSAGE_ID),
            "filename": lambda value: value.replace("chart%20one.png", "other.png"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _, _, manifest = self.build(root)
                entry = manifest["entries"][0]
                payload = response(
                    entry, status="downloaded", body=b"bytes", http_status=200
                )
                payload["final_url"] = mutate(entry["discord_url"])
                with self.assertRaisesRegex(
                    archive.AttachmentArchiveError, "exactly match"
                ):
                    archive.ingest_browser_response(
                        manifest, payload, root / "archive"
                    )
                self.assertEqual(entry["attempt_count"], 0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, manifest = self.build(root)
            entry = manifest["entries"][0]
            payload = response(
                entry, status="downloaded", body=b"bytes", http_status=200
            )
            payload.pop("final_url")
            with self.assertRaisesRegex(
                archive.AttachmentArchiveError, "final_url is required"
            ):
                archive.ingest_browser_response(manifest, payload, root / "archive")

    def test_verify_detects_corrupted_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, manifest = self.build(root)
            entry = manifest["entries"][0]
            archived = root / "archive"
            archive.ingest_browser_response(
                manifest,
                response(entry, status="downloaded", body=b"original", http_status=200),
                archived,
            )
            target = archive.resolve_under(archived, entry["local_package_path"], label="target")
            target.write_bytes(b"corrupt")
            result = archive.verify_archive(manifest, archived, require_terminal=True)
            self.assertEqual(result["status"], "failed")
            self.assertGreaterEqual(result["problem_count"], 1)
            self.assertIn("mismatch", json.dumps(result))

    def test_download_must_match_discord_declared_attachment_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, manifest = self.build(root)
            entry = manifest["entries"][0]
            entry["declared_byte_size"] = 999
            with self.assertRaisesRegex(
                archive.AttachmentArchiveError, "declared size"
            ):
                archive.ingest_browser_response(
                    manifest,
                    response(
                        entry, status="downloaded", body=b"wrong-size", http_status=200
                    ),
                    root / "archive",
                )
            self.assertEqual(entry["attempt_count"], 0)

    def test_reconcile_preserves_terminal_rows_and_adds_new_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus_path, manifest_path, manifest = self.build(root)
            entry = manifest["entries"][0]
            archive.ingest_browser_response(
                manifest,
                response(entry, status="downloaded", body=b"one", http_status=200),
                root / "archive",
            )
            archive.write_json_atomic(manifest_path, manifest)
            write_json(corpus_path, corpus(second=True))
            reconciled = archive.create_or_resume_manifest(
                corpus_path, manifest_path, reconcile=True
            )
            self.assertEqual(reconciled["counts"]["total"], 2)
            by_id = {row["attachment_id"]: row for row in reconciled["entries"]}
            self.assertEqual(by_id[ATTACHMENT_ID]["capture_status"], "downloaded")
            self.assertEqual(by_id[SECOND_ATTACHMENT_ID]["capture_status"], "pending")

    def test_pending_request_has_exact_ids_and_excludes_terminal_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, manifest = self.build(root, second=True)
            requests = archive.pending_requests(manifest, limit=5)
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0]["contract"], "discord_attachment_browser_request_v1")
            entry = manifest["entries"][0]
            archive.ingest_browser_response(
                manifest,
                response(entry, status="unavailable", terminal=True, http_status=410),
                root / "archive",
            )
            requests = archive.pending_requests(manifest, limit=5)
            self.assertEqual(len(requests), 1)
            self.assertNotEqual(requests[0]["attachment_id"], entry["attachment_id"])

    def test_local_extraction_is_hashed_but_never_auto_accepts_chart_claims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, manifest = self.build(root)
            entry = manifest["entries"][0]
            archive.ingest_browser_response(
                manifest,
                response(
                    entry,
                    status="downloaded",
                    body=b"source chart bytes",
                    http_status=200,
                ),
                root / "archive",
            )
            staging = root / "staging"
            staging.mkdir()
            (staging / "ocr.txt").write_text("locally extracted labels", encoding="utf-8")
            artifact = archive.record_extraction(
                manifest,
                {
                    "attachment_id": ATTACHMENT_ID,
                    "status": "complete",
                    "method": "local_ocr_v1",
                    "created_at_utc": "2026-07-21T05:02:00Z",
                    "staged_file": "ocr.txt",
                    "filename": "ocr.txt",
                    "mime_type": "text/plain",
                },
                root / "archive",
                staging_root=staging,
            )
            entry = manifest["entries"][0]
            self.assertEqual(entry["extraction_status"], "complete")
            self.assertFalse(entry["chart_claim_eligible"])
            self.assertRegex(artifact["content_sha256"], r"^[0-9a-f]{64}$")
            self.assertIsNone(artifact["confidence"])
            self.assertEqual(artifact["extracted_text"], "locally extracted labels")
            target = archive.resolve_under(
                root / "archive", artifact["local_package_path"], label="extraction"
            )
            self.assertTrue(target.is_file())
            verified = archive.verify_archive(
                manifest, root / "archive", require_terminal=True
            )
            self.assertEqual(verified["status"], "passed")
            self.assertEqual(verified["verified_extraction_artifact_count"], 1)
            target.write_text("tampered extraction", encoding="utf-8")
            tampered = archive.verify_archive(
                manifest, root / "archive", require_terminal=True
            )
            self.assertEqual(tampered["status"], "failed")
            self.assertIn("extraction_sha256_mismatch", json.dumps(tampered))

    def test_extraction_text_and_confidence_are_preserved_without_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, manifest = self.build(root)
            entry = manifest["entries"][0]
            archive.ingest_browser_response(
                manifest,
                response(entry, status="downloaded", body=b"chart", http_status=200),
                root / "archive",
            )
            staging = root / "staging"
            staging.mkdir()
            (staging / "ocr.txt").write_text("exact extracted text", encoding="utf-8")
            artifact = archive.record_extraction(
                manifest,
                {
                    "attachment_id": ATTACHMENT_ID,
                    "status": "complete",
                    "method": "local_ocr_v1",
                    "created_at_utc": "2026-07-21T05:04:00Z",
                    "staged_file": "ocr.txt",
                    "filename": "ocr.txt",
                    "extracted_text": "exact extracted text",
                    "confidence": 0.82,
                },
                root / "archive",
                staging_root=staging,
            )
            self.assertEqual(artifact["extracted_text"], "exact extracted text")
            self.assertEqual(artifact["confidence"], 0.82)

            with self.assertRaisesRegex(
                archive.AttachmentArchiveError, "exactly match"
            ):
                archive.record_extraction(
                    manifest,
                    {
                        "attachment_id": ATTACHMENT_ID,
                        "status": "partial",
                        "method": "local_ocr_v2",
                        "created_at_utc": "2026-07-21T05:05:00Z",
                        "staged_file": "ocr.txt",
                        "filename": "ocr.txt",
                        "extracted_text": "fabricated mismatch",
                    },
                    root / "archive",
                    staging_root=staging,
                )
            (staging / "empty.txt").write_bytes(b"")
            with self.assertRaisesRegex(archive.AttachmentArchiveError, "empty"):
                archive.record_extraction(
                    manifest,
                    {
                        "attachment_id": ATTACHMENT_ID,
                        "status": "partial",
                        "method": "local_ocr_empty",
                        "created_at_utc": "2026-07-21T05:06:00Z",
                        "staged_file": "empty.txt",
                        "filename": "empty.txt",
                    },
                    root / "archive",
                    staging_root=staging,
                )

    def test_failed_extraction_has_no_artifact_and_never_counts_as_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, manifest = self.build(root)
            entry = manifest["entries"][0]
            archive.ingest_browser_response(
                manifest,
                response(entry, status="downloaded", body=b"chart", http_status=200),
                root / "archive",
            )
            artifact = archive.record_extraction(
                manifest,
                {
                    "attachment_id": ATTACHMENT_ID,
                    "status": "failed",
                    "method": "local_ocr_v1",
                    "created_at_utc": "2026-07-21T05:03:00Z",
                    "failure_code": "ocr_parse_error",
                    "failure_detail": "Local OCR produced no readable chart labels",
                },
                root / "archive",
            )
            self.assertIsNone(artifact["local_package_path"])
            result = archive.verify_archive(
                manifest, root / "archive", require_terminal=True
            )
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["verified_extraction_artifact_count"], 0)


if __name__ == "__main__":
    unittest.main()
