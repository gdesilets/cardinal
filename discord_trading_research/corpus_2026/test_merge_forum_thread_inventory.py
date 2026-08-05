from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import build_corpus
import merge_forum_thread_inventory as merger


def snowflake(value: str, increment: int = 0) -> str:
    moment = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    milliseconds = int(moment.timestamp() * 1000)
    return str(((milliseconds - merger.DISCORD_EPOCH_MS) << 22) + increment)


class ForumThreadInventoryMergerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.top = self.root / "post_cutoff_top_level_inventory.json"
        top_payload = json.loads(
            (merger.SCRIPT_DIR / "full_server_channel_inventory.json").read_text(
                encoding="utf-8"
            )
        )
        top_payload["capture_as_of_utc"] = "2026-07-21T05:04:00Z"
        top_payload["inventory_complete"] = True
        top_payload["status"] = "complete"
        top_payload["accessible_scope"]["top_level_containers"].update(
            {"declared_complete": True, "expected_count": 38, "status": "complete"}
        )
        top_payload["accessible_scope"]["post_cutoff_navigation_resnapshot"] = {
            "declared_complete": True,
            "status": "complete",
            "required_capture_at_or_after_utc": "2026-07-21T05:00:00Z",
            "completion_evidence": {
                "authenticated": True,
                "source_scope": "discord_only",
                "outside_sources_used": False,
                "navigation_pass_complete": True,
                "terminal_state_observed": True,
                "capture_completed_at_utc": "2026-07-21T05:04:00Z",
                "source_refs": ["discord-ui:server-navigation:terminal"],
            },
        }
        self.top.write_text(
            json.dumps(top_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.active_thread_id = snowflake("2026-01-10T14:00:00Z", 1)
        self.archived_thread_id = snowflake("2026-02-10T14:00:00Z", 2)
        self.ordinary_thread_id = snowflake("2026-03-10T14:00:00Z", 3)
        self.ordinary_parent_id = "1359593949110472777"
        self.raw_path = self.root / "forum_thread_inventory.json"
        self.ordinary_path = self.root / "ordinary_thread_inventory.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def evidence(
        self,
        *,
        thread_id: str,
        message_id: str,
        role: str,
        observed_at: str,
    ) -> dict[str, object]:
        value: dict[str, object] = {
            "role": role,
            "method": "authenticated_discord_message_permalink",
            "message_id": message_id,
            "permalink": (
                f"https://discord.com/channels/{merger.GUILD_ID}/{thread_id}/{message_id}"
            ),
            "position_verified": True,
            "observed_at_utc": observed_at,
            "source_ref": f"discord-ui:thread:{thread_id}:{role}",
            "authenticated": True,
            "source_scope": "discord_only",
            "outside_sources_used": False,
        }
        if role == "last_message_at_or_before_cutoff":
            value["cutoff_bounded"] = True
        return value

    def thread(
        self,
        *,
        thread_id: str,
        archived: bool,
        title: str = "Same title is allowed",
    ) -> dict[str, object]:
        if archived:
            pass_name = "discoverable_archived"
            observed = "2026-07-21T05:18:00Z"
            identity = {
                "method": "authenticated_discord_thread_url",
                "thread_url": (
                    f"https://discord.com/channels/{merger.GUILD_ID}/{thread_id}"
                ),
                "enumeration_pass": pass_name,
                "observed_at_utc": observed,
                "source_ref": f"discord-ui:archive-row:{thread_id}",
                "authenticated": True,
                "source_scope": "discord_only",
                "outside_sources_used": False,
            }
            first = snowflake("2026-02-10T14:01:00Z", 11)
            last = snowflake("2026-06-10T14:02:00Z", 12)
        else:
            pass_name = "active"
            observed = "2026-07-21T05:08:00Z"
            identity = {
                "method": "forum_card_data_list_item_id",
                "forum_card_data_list_item_id": (
                    f"forum-channel-list-{merger.PREMIUM_JOURNALS_ID}___{thread_id}"
                ),
                "enumeration_pass": pass_name,
                "observed_at_utc": observed,
                "source_ref": f"discord-ui:active-forum-card:{thread_id}",
                "authenticated": True,
                "source_scope": "discord_only",
                "outside_sources_used": False,
            }
            first = snowflake("2026-01-10T14:01:00Z", 21)
            last = snowflake("2026-07-20T20:02:00Z", 22)
        return {
            "thread_id": thread_id,
            "title": title,
            "parent_forum_channel_id": merger.PREMIUM_JOURNALS_ID,
            "archived": archived,
            "locked": False,
            "tags": ["journal"],
            "identity_evidence": [identity],
            "starter_message_evidence": self.evidence(
                thread_id=thread_id,
                message_id=thread_id,
                role="thread_starter",
                observed_at="2026-07-21T05:20:00Z",
            ),
            "first_message_evidence": self.evidence(
                thread_id=thread_id,
                message_id=first,
                role="first_message",
                observed_at="2026-07-21T05:20:00Z",
            ),
            "last_message_evidence": self.evidence(
                thread_id=thread_id,
                message_id=last,
                role="last_message_at_or_before_cutoff",
                observed_at="2026-07-21T05:20:00Z",
            ),
        }

    def valid_payload(self) -> dict[str, object]:
        frozen = json.loads(self.top.read_text(encoding="utf-8"))
        return {
            "schema_version": "1.0",
            "guild_id": merger.GUILD_ID,
            "parent_forum_channel_id": merger.PREMIUM_JOURNALS_ID,
            "source_scope": "discord_only",
            "outside_sources_used": False,
            "inventory_complete": True,
            "status": "complete",
            "requested_local_window": frozen["requested_local_window"],
            "data_cutoff_utc": "2026-07-21T05:00:00Z",
            "capture_completed_at_utc": "2026-07-21T05:21:00Z",
            "enumeration_passes": {
                "active": {
                    "parent_forum_channel_id": merger.PREMIUM_JOURNALS_ID,
                    "method": "authenticated_discord_forum_card_enumeration",
                    "status": "complete",
                    "authenticated": True,
                    "source_scope": "discord_only",
                    "outside_sources_used": False,
                    "started_at_utc": "2026-07-21T05:05:00Z",
                    "completed_at_utc": "2026-07-21T05:10:00Z",
                    "source_refs": ["discord-ui:premium-journals:active:page-1"],
                    "pagination_complete": True,
                    "terminal_state_observed": True,
                    "remaining_cursor": None,
                    "reported_thread_count": 1,
                    "thread_ids": [self.active_thread_id],
                },
                "discoverable_archived": {
                    "parent_forum_channel_id": merger.PREMIUM_JOURNALS_ID,
                    "method": "authenticated_discord_archived_thread_enumeration",
                    "status": "complete",
                    "authenticated": True,
                    "source_scope": "discord_only",
                    "outside_sources_used": False,
                    "started_at_utc": "2026-07-21T05:11:00Z",
                    "completed_at_utc": "2026-07-21T05:20:00Z",
                    "source_refs": ["discord-ui:premium-journals:archive:terminal-page"],
                    "pagination_complete": True,
                    "terminal_state_observed": True,
                    "remaining_cursor": None,
                    "reported_thread_count": 1,
                    "thread_ids": [self.archived_thread_id],
                },
            },
            "threads": [
                self.thread(thread_id=self.active_thread_id, archived=False),
                self.thread(thread_id=self.archived_thread_id, archived=True),
            ],
        }

    def valid_ordinary_payload(self) -> dict[str, object]:
        top = json.loads(self.top.read_text(encoding="utf-8"))
        audits: list[dict[str, object]] = []
        for channel in top["channels"]:
            parent_id = str(channel["channel_id"])
            applicable = parent_id == self.ordinary_parent_id
            audit: dict[str, object] = {
                "parent_channel_id": parent_id,
                "applicable": applicable,
                "applicability_basis": (
                    "authenticated Discord thread controls and active/archive surfaces audited"
                    if applicable
                    else "authenticated navigation proves this parent has no ordinary-thread surface"
                ),
                "status": "complete",
                "authenticated": True,
                "source_scope": "discord_only",
                "outside_sources_used": False,
                "completed_at_utc": "2026-07-21T05:38:00Z",
                "source_refs": [f"discord-ui:parent-thread-audit:{parent_id}"],
            }
            if applicable:
                audit["enumeration_passes"] = {
                    "active": {
                        "parent_channel_id": parent_id,
                        "method": "authenticated_discord_active_thread_enumeration",
                        "status": "complete",
                        "authenticated": True,
                        "source_scope": "discord_only",
                        "outside_sources_used": False,
                        "started_at_utc": "2026-07-21T05:30:00Z",
                        "completed_at_utc": "2026-07-21T05:33:00Z",
                        "source_refs": [f"discord-ui:active-threads:{parent_id}:terminal"],
                        "pagination_complete": True,
                        "terminal_state_observed": True,
                        "remaining_cursor": None,
                        "reported_thread_count": 1,
                        "thread_ids": [self.ordinary_thread_id],
                    },
                    "discoverable_archived": {
                        "parent_channel_id": parent_id,
                        "method": "authenticated_discord_archived_thread_enumeration",
                        "status": "complete",
                        "authenticated": True,
                        "source_scope": "discord_only",
                        "outside_sources_used": False,
                        "started_at_utc": "2026-07-21T05:34:00Z",
                        "completed_at_utc": "2026-07-21T05:37:00Z",
                        "source_refs": [f"discord-ui:archived-threads:{parent_id}:terminal"],
                        "pagination_complete": True,
                        "terminal_state_observed": True,
                        "remaining_cursor": None,
                        "reported_thread_count": 0,
                        "thread_ids": [],
                    },
                }
            audits.append(audit)
        return {
            "schema_version": "1.0",
            "guild_id": merger.GUILD_ID,
            "source_scope": "discord_only",
            "outside_sources_used": False,
            "inventory_complete": True,
            "status": "complete",
            "requested_local_window": top["requested_local_window"],
            "data_cutoff_utc": "2026-07-21T05:00:00Z",
            "capture_completed_at_utc": "2026-07-21T05:40:00Z",
            "reported_thread_count": 1,
            "parent_audits": audits,
            "threads": [
                {
                    "thread_id": self.ordinary_thread_id,
                    "parent_channel_id": self.ordinary_parent_id,
                    "title": "Exact ordinary thread",
                    "thread_type": "public_thread",
                    "archived": False,
                    "locked": False,
                    "identity_evidence": [
                        {
                            "method": "authenticated_discord_thread_url",
                            "thread_url": (
                                f"https://discord.com/channels/{merger.GUILD_ID}/"
                                f"{self.ordinary_thread_id}"
                            ),
                            "enumeration_pass": "active",
                            "observed_at_utc": "2026-07-21T05:32:00Z",
                            "source_ref": (
                                f"discord-ui:ordinary-thread:{self.ordinary_thread_id}"
                            ),
                            "authenticated": True,
                            "source_scope": "discord_only",
                            "outside_sources_used": False,
                        }
                    ],
                }
            ],
        }

    def write_payload(self, payload: dict[str, object]) -> Path:
        self.raw_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return self.raw_path

    def write_ordinary_payload(self, payload: dict[str, object] | None = None) -> Path:
        self.ordinary_path.write_text(
            json.dumps(
                payload if payload is not None else self.valid_ordinary_payload(),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return self.ordinary_path

    def assert_invalid(self, payload: dict[str, object], expected: str) -> None:
        self.write_payload(payload)
        self.write_ordinary_payload()
        with self.assertRaises(merger.InventoryValidationError) as raised:
            merger.build_merged_inventory(
                self.top, self.raw_path, self.ordinary_path
            )
        self.assertTrue(
            any(expected in issue for issue in raised.exception.issues),
            raised.exception.issues,
        )

    def assert_invalid_ordinary(
        self, payload: dict[str, object], expected: str
    ) -> None:
        self.write_payload(self.valid_payload())
        self.write_ordinary_payload(payload)
        with self.assertRaises(merger.InventoryValidationError) as raised:
            merger.build_merged_inventory(
                self.top, self.raw_path, self.ordinary_path
            )
        self.assertTrue(
            any(expected in issue for issue in raised.exception.issues),
            raised.exception.issues,
        )

    def test_duplicate_titles_are_allowed_and_builder_accepts_output(self) -> None:
        before = hashlib.sha256(self.top.read_bytes()).hexdigest()
        self.write_payload(self.valid_payload())
        self.write_ordinary_payload()
        output = self.root / "working" / "release_inventory.json"
        merged = merger.merge_to_path(
            self.top, self.raw_path, self.ordinary_path, output
        )
        after = hashlib.sha256(self.top.read_bytes()).hexdigest()

        self.assertEqual(before, after)
        self.assertEqual(merged["top_level_container_count"], 38)
        self.assertEqual(merged["forum_thread_count"], 2)
        self.assertEqual(merged["ordinary_thread_count"], 1)
        self.assertEqual(merged["container_count"], 41)
        thread_rows = [
            row
            for row in merged["containers"]
            if row["inventory_layer"] == "observed_forum_thread"
        ]
        self.assertEqual([row["name"] for row in thread_rows], ["Same title is allowed"] * 2)
        self.assertEqual(len({row["container_id"] for row in thread_rows}), 2)
        self.assertEqual(merged["source_inputs"][0]["sha256"], before)
        self.assertEqual(
            merged["source_inputs"][1]["sha256"],
            hashlib.sha256(self.raw_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            merged["source_inputs"][2]["sha256"],
            hashlib.sha256(self.ordinary_path.read_bytes()).hexdigest(),
        )
        voice_stage = [
            row
            for row in merged["containers"]
            if row.get("kind") in {"voice channel", "stage channel"}
        ]
        self.assertEqual(len(voice_stage), 2)
        self.assertTrue(all(row["message_bearing"] for row in voice_stage))

        scope = build_corpus.make_scope(
            merger.GUILD_ID, "2026-01-01", "2026-07-20", "America/Chicago"
        )
        normalized = build_corpus.normalize_inventory(
            output,
            scope,
            self.root,
            {},
            (),
        )
        self.assertTrue(normalized["declared_complete"])
        self.assertTrue(normalized["validated_complete"], normalized["validation_errors"])
        self.assertEqual(normalized["top_level_container_count"], 38)
        self.assertEqual(normalized["observed_forum_thread_count"], 2)
        self.assertEqual(normalized["ordinary_thread_count"], 1)
        self.assertEqual(normalized["container_count"], 41)
        self.assertTrue(normalized["accessible_scope"]["forum_threads"]["validated_complete"])
        self.assertTrue(
            normalized["accessible_scope"]["ordinary_threads"]["validated_complete"]
        )

    def test_pre_cutoff_top_level_snapshot_is_rejected(self) -> None:
        top = json.loads(self.top.read_text(encoding="utf-8"))
        top["capture_as_of_utc"] = "2026-07-20T22:59:10.947Z"
        top["accessible_scope"]["post_cutoff_navigation_resnapshot"][
            "completion_evidence"
        ]["capture_completed_at_utc"] = "2026-07-20T22:59:10.947Z"
        self.top.write_text(
            json.dumps(top, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self.write_payload(self.valid_payload())
        self.write_ordinary_payload()
        with self.assertRaises(merger.InventoryValidationError) as raised:
            merger.build_merged_inventory(self.top, self.raw_path, self.ordinary_path)
        self.assertIn(
            "post_cutoff_top_level_capture_before_data_cutoff",
            raised.exception.issues,
        )

    def test_missing_ordinary_parent_audit_is_rejected(self) -> None:
        payload = self.valid_ordinary_payload()
        payload["parent_audits"].pop()
        self.assert_invalid_ordinary(payload, "ordinary_thread_missing_parent_audits")

    def test_attachment_only_thread_identity_is_rejected(self) -> None:
        payload = self.valid_payload()
        payload["threads"][0]["identity_evidence"] = [
            {
                "method": "attachment_cdn_path",
                "attachment_url": (
                    f"https://cdn.discordapp.com/attachments/{self.active_thread_id}/file.png"
                ),
                "enumeration_pass": "active",
                "observed_at_utc": "2026-07-21T05:08:00Z",
                "source_ref": "discord-ui:attachment",
                "authenticated": True,
                "source_scope": "discord_only",
                "outside_sources_used": False,
            }
        ]
        self.assert_invalid(payload, "attachment_cdn_identity_forbidden")

    def test_incomplete_archive_pass_is_rejected(self) -> None:
        payload = self.valid_payload()
        archive = payload["enumeration_passes"]["discoverable_archived"]
        archive["status"] = "partial"
        archive["pagination_complete"] = False
        archive["terminal_state_observed"] = False
        archive["remaining_cursor"] = "next-page"
        self.assert_invalid(payload, "enumeration_pass_discoverable_archived_status_not_complete")

    def test_wrong_parent_is_rejected(self) -> None:
        payload = self.valid_payload()
        payload["parent_forum_channel_id"] = "1273692573898113076"
        self.assert_invalid(payload, "forum_inventory_wrong_parent")

    def test_duplicate_thread_ids_are_rejected_even_when_titles_differ(self) -> None:
        payload = self.valid_payload()
        duplicate = copy.deepcopy(payload["threads"][0])
        duplicate["title"] = "Different title cannot rescue a duplicate ID"
        payload["threads"].append(duplicate)
        self.assert_invalid(payload, "forum_duplicate_thread_id")

    def test_pass_started_before_cutoff_is_rejected(self) -> None:
        payload = self.valid_payload()
        payload["enumeration_passes"]["active"]["started_at_utc"] = (
            "2026-07-21T04:59:59Z"
        )
        self.assert_invalid(payload, "enumeration_pass_active_started_before_data_cutoff")

    def test_refuses_overwrite_and_does_not_replace_existing_bytes(self) -> None:
        self.write_payload(self.valid_payload())
        self.write_ordinary_payload()
        output = self.root / "existing.json"
        output.write_bytes(b"user-owned")
        with self.assertRaises(merger.InventoryValidationError) as raised:
            merger.merge_to_path(
                self.top, self.raw_path, self.ordinary_path, output
            )
        self.assertIn("output_already_exists", str(raised.exception))
        self.assertEqual(output.read_bytes(), b"user-owned")

    def test_partial_evidence_writes_no_output(self) -> None:
        payload = self.valid_payload()
        del payload["threads"][1]["last_message_evidence"]
        self.write_payload(payload)
        self.write_ordinary_payload()
        output = self.root / "must-not-exist.json"
        with self.assertRaises(merger.InventoryValidationError):
            merger.merge_to_path(
                self.top, self.raw_path, self.ordinary_path, output
            )
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
