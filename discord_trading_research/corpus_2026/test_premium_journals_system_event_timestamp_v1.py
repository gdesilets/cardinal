from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import premium_journals_system_event_timestamp_v1 as v1
import timestamp_scope_revalidation as timestamp_scope


MESSAGE_ID = "1458135984737747005"
THREAD_ID = "1405897225845997588"
QUERY = "in:premium-journals after:2026-01-05 before:2026-01-07"


class PremiumForumSystemEventTimestampV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.segment_path = self.root / "raw" / "channel_segments_v2_5" / "jan6.json"
        self.segment_path.parent.mkdir(parents=True)
        self.row = self.make_row()
        self.payload = {
            "guild_id": v1.GUILD_ID,
            "requested_container": {"channel_id": v1.PARENT_FORUM_ID},
            "segment": {"start": "2026-01-06", "end": "2026-01-06", "timezone": "America/Chicago", "query": QUERY},
            "messages": [self.row],
        }
        self.write_json(self.segment_path, self.payload)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def make_row() -> dict:
        timestamp = v1.snowflake_time(MESSAGE_ID).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        article_id = f"search-result-{MESSAGE_ID}"
        nav_key = "forum-group-navigation:test"
        return {
            "message_id": MESSAGE_ID,
            "article_id": article_id,
            "article_aria_labelledby": f"message-content-{MESSAGE_ID}",
            "attachments": [], "links": [], "media_assets": [], "reactions": [],
            "author": "", "author_id": None, "author_avatar_url": None,
            "author_id_source": None, "author_id_candidates": [], "author_id_conflict": False,
            "content_present": True, "content_scope_exact": True,
            "content_text": "Hessen\n changed the post title: journalen min\n — \n1/6/26, 10:31 AM\nTuesday, January 6, 2026 at 10:31 AM",
            "timestamp_scope_exact": False, "timestamp_utc": timestamp,
            "snowflake_timestamp_utc": timestamp, "timestamp_discrepancy_ms": 0,
            "row_owned_time_count": 1, "row_owned_time_datetime": timestamp,
            "row_owned_time_element_id": None,
            "collection_channel_id": v1.PARENT_FORUM_ID, "collection_channel_name": v1.CHANNEL_NAME,
            "collection_channel_kind": "forum channel", "parent_channel": v1.CHANNEL_NAME,
            "page_number": 7, "result_index": 164, "search_query": QUERY,
            "forum_group_membership_exact": True, "forum_group_message_ids": [MESSAGE_ID],
            "forum_group_navigation_evidence_key": nav_key,
            "forum_group_navigation_validation": {"valid": True},
            "forum_group_navigation_evidence": {"guild_id": v1.GUILD_ID, "parent_forum_channel_id": v1.PARENT_FORUM_ID, "thread_channel_id": THREAD_ID, "page_number": 7, "group_message_ids": [MESSAGE_ID]},
            "inferred_thread_channel_id": THREAD_ID, "thread_channel_id_exact": True,
            "thread_channel_id_conflict": False,
            "exact_permalink": f"https://discord.com/channels/{v1.GUILD_ID}/{THREAD_ID}/{MESSAGE_ID}",
            "exact_permalink_conflict_detected": False, "exact_parent_forum_conflict_detected": False,
            "reply_context_present": False, "reply_context": "", "reply_to_message_id": None,
            "reply_to_permalink": None, "reply_to_channel_id": None, "reply_target_content_id": None,
            "reply_target_content_text": "", "reply_target_data_list_item_id": None,
            "reply_target_aria_describedby": None, "reply_target_aria_labelledby": None,
            "reply_context_owner_message_id": None, "reply_target_id_candidates": [],
            "reply_target_owner_scoped": False, "reply_to_message_id_conflict": False,
            "reply_to_channel_id_conflict": False,
            "discord_system_event_exact": False, "discord_system_event_type": None,
            "timestamp_exact_fallback_source": None,
        }

    def sidecar_payload(self) -> dict:
        row = self.row
        return {
            "schema_version": v1.SCHEMA_VERSION, "artifact_type": v1.ARTIFACT_TYPE,
            "source_scope": "discord_only", "outside_sources_used": False,
            "source_artifact_path": self.segment_path.relative_to(self.root).as_posix(),
            "source_artifact_sha256": v1.sha256_file(self.segment_path),
            "source_artifact_bytes": self.segment_path.stat().st_size,
            "revalidations": [{
                "status": "passed", "evidence_type": v1.EVIDENCE_TYPE,
                "message_id": MESSAGE_ID, "result_index": row["result_index"],
                "source_row_sha256": v1.row_sha256(row),
                "effective_correction": v1._expected_correction(row["timestamp_utc"]),
                "route": {"guild_id": v1.GUILD_ID, "parent_forum_channel_id": v1.PARENT_FORUM_ID, "start": "2026-01-06", "end": "2026-01-06", "timezone": "America/Chicago", "query": QUERY, "page_number": 7, "forum_group_navigation_evidence_key": row["forum_group_navigation_evidence_key"], "exact_permalink": row["exact_permalink"]},
                "dom_evidence": {"article_id": row["article_id"], "article_aria_labelledby": row["article_aria_labelledby"], "author": "", "author_id": None, "row_owned_time_count": 1, "row_owned_time_datetime": row["timestamp_utc"], "row_owned_time_element_id": None, "content_text": row["content_text"], "system_event_dom_exact": True, "system_event_dom_marker": v1.DOM_EVENT_MARKER, "system_event_dom_marker_article_id": row["article_id"], "system_event_dom_marker_message_id": MESSAGE_ID},
            }],
        }

    def load(self) -> v1.ForumSystemEventRevalidation:
        return v1.load_adjacent_forum_system_event_revalidation(self.segment_path, self.payload, source_artifact_sha256=v1.sha256_file(self.segment_path), artifact_root=self.root)

    def external_registration(
        self,
        *,
        sidecar: dict | None = None,
        sidecar_relative: str = "raw/system_event_timestamp_evidence_v1/jan6-proof.json",
    ) -> tuple[Path, dict[str, dict[str, str]]]:
        external_path = self.root / Path(*sidecar_relative.split("/"))
        self.write_json(external_path, sidecar or self.sidecar_payload())
        segment_relative = self.segment_path.relative_to(self.root).as_posix()
        return external_path, {
            segment_relative: {
                "source_artifact_sha256": v1.sha256_file(self.segment_path),
                "sidecar_path": sidecar_relative,
                "sidecar_sha256": v1.sha256_file(external_path),
            }
        }

    def test_reacquired_exact_marker_sidecar_is_accepted(self) -> None:
        self.write_json(v1.sidecar_path(self.segment_path), self.sidecar_payload())
        bundle = self.load()
        self.assertFalse(bundle.errors, bundle.errors)
        self.assertEqual(v1.FALLBACK_SOURCE + "_sidecar_revalidated", v1.timestamp_scope_mode(self.row, bundle))
        self.assertTrue(bundle.summary()["valid"])

    def test_current_unmarked_source_cannot_self_accept(self) -> None:
        self.assertIsNone(v1.timestamp_scope_mode(self.row, self.load()))

    def test_user_lookalike_text_is_rejected(self) -> None:
        sidecar = self.sidecar_payload(); sidecar["revalidations"][0]["dom_evidence"]["content_text"] = self.row["content_text"].replace("changed the post title:", "said changed the post title:")
        self.write_json(v1.sidecar_path(self.segment_path), sidecar)
        self.assertTrue(self.load().errors)

    def test_author_time_attachment_reply_and_route_conflicts_fail_closed(self) -> None:
        cases = [
            ("author", "Hessen"),
            ("attachments", [{"attachment_id": "1"}]), ("reply_context_present", True),
            ("exact_permalink", "https://discord.com/channels/1/2/3"),
        ]
        for field, value in cases:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.row); changed[field] = value
                effective = copy.deepcopy(changed); effective.update(v1._expected_correction(changed["timestamp_utc"]))
                self.assertFalse(v1.exact_forum_post_title_system_event_fallback(effective, MESSAGE_ID))
        effective = copy.deepcopy(self.row); effective.update(v1._expected_correction(self.row["timestamp_utc"]))
        effective["row_owned_time_count"] = 2
        self.assertFalse(v1.exact_forum_post_title_system_event_fallback(effective, MESSAGE_ID))
        effective["row_owned_time_count"] = 1; effective["row_owned_time_element_id"] = "message-timestamp-" + MESSAGE_ID
        self.assertFalse(v1.exact_forum_post_title_system_event_fallback(effective, MESSAGE_ID))

    def test_wrong_marker_or_snowflake_mismatch_fails_closed(self) -> None:
        sidecar = self.sidecar_payload(); sidecar["revalidations"][0]["dom_evidence"]["system_event_dom_marker"] = "not-a-marker"
        self.write_json(v1.sidecar_path(self.segment_path), sidecar)
        self.assertTrue(self.load().errors)
        sidecar = self.sidecar_payload(); sidecar["revalidations"][0]["dom_evidence"]["row_owned_time_datetime"] = "2026-01-06T16:31:49.000Z"
        self.write_json(v1.sidecar_path(self.segment_path), sidecar)
        self.assertTrue(self.load().errors)

    def test_bad_route_or_navigation_evidence_fails_closed(self) -> None:
        sidecar = self.sidecar_payload(); sidecar["revalidations"][0]["route"]["page_number"] = 8
        self.write_json(v1.sidecar_path(self.segment_path), sidecar)
        self.assertTrue(self.load().errors)
        changed = copy.deepcopy(self.row); changed["forum_group_navigation_validation"] = {"valid": False}
        effective = copy.deepcopy(changed); effective.update(v1._expected_correction(changed["timestamp_utc"]))
        self.assertFalse(v1.exact_forum_post_title_system_event_fallback(effective, MESSAGE_ID))

    def test_registered_external_sidecar_is_accepted_without_adjacent_file(self) -> None:
        external_path, registration = self.external_registration()
        self.assertFalse(v1.sidecar_path(self.segment_path).exists())
        with mock.patch.object(v1, "EXTERNAL_SIDECAR_REGISTRATIONS_V1", registration):
            bundle = self.load()
        self.assertFalse(bundle.errors, bundle.errors)
        self.assertTrue(external_path.samefile(bundle.sidecar_path))
        self.assertEqual("registered_external_v1", bundle.sidecar_resolution)
        self.assertEqual(
            v1.FALLBACK_SOURCE + "_sidecar_revalidated",
            v1.timestamp_scope_mode(self.row, bundle),
        )
        self.assertTrue(bundle.summary()["valid"])

    def test_unregistered_external_sidecar_is_not_discovered(self) -> None:
        self.external_registration()
        with mock.patch.object(v1, "EXTERNAL_SIDECAR_REGISTRATIONS_V1", {}):
            bundle = self.load()
        self.assertFalse(bundle.provided)
        self.assertFalse(bundle.errors)
        self.assertIsNone(v1.timestamp_scope_mode(self.row, bundle))

    def test_external_registration_rejects_escape_missing_and_tamper(self) -> None:
        segment_relative = self.segment_path.relative_to(self.root).as_posix()
        source_sha = v1.sha256_file(self.segment_path)
        base = {
            "source_artifact_sha256": source_sha,
            "sidecar_path": "raw/system_event_timestamp_evidence_v1/missing.json",
            "sidecar_sha256": "0" * 64,
        }
        cases = [
            (
                "escape",
                {**base, "sidecar_path": "../outside.json"},
                "external_sidecar_registered_path_invalid",
            ),
            ("missing", base, "external_sidecar_registered_file_missing"),
        ]
        for label, record, expected_error in cases:
            with self.subTest(label=label), mock.patch.object(
                v1,
                "EXTERNAL_SIDECAR_REGISTRATIONS_V1",
                {segment_relative: record},
            ):
                bundle = self.load()
                self.assertTrue(
                    any(error.startswith(expected_error) for error in bundle.errors),
                    bundle.errors,
                )

        external_path, registration = self.external_registration()
        self.write_json(external_path, {**self.sidecar_payload(), "tampered": True})
        with mock.patch.object(v1, "EXTERNAL_SIDECAR_REGISTRATIONS_V1", registration):
            bundle = self.load()
        self.assertIn("external_sidecar_registered_sha256_mismatch", bundle.errors)

    def test_external_registration_rejects_wrong_canonical_bindings(self) -> None:
        external_path, registration = self.external_registration()
        segment_relative = self.segment_path.relative_to(self.root).as_posix()

        wrong_source_sha = copy.deepcopy(registration)
        wrong_source_sha[segment_relative]["source_artifact_sha256"] = "0" * 64
        with mock.patch.object(v1, "EXTERNAL_SIDECAR_REGISTRATIONS_V1", wrong_source_sha):
            bundle = self.load()
        self.assertIn(
            "external_sidecar_registered_canonical_sha256_mismatch", bundle.errors
        )

        wrong_path_sidecar = self.sidecar_payload()
        wrong_path_sidecar["source_artifact_path"] = (
            "raw/channel_segments_v2_5/not-this-canonical.json"
        )
        self.write_json(external_path, wrong_path_sidecar)
        wrong_path_registration = copy.deepcopy(registration)
        wrong_path_registration[segment_relative]["sidecar_sha256"] = v1.sha256_file(
            external_path
        )
        with mock.patch.object(
            v1,
            "EXTERNAL_SIDECAR_REGISTRATIONS_V1",
            wrong_path_registration,
        ):
            bundle = self.load()
        self.assertIn("sidecar_source_artifact_path_mismatch", bundle.errors)

    def test_external_registration_does_not_change_other_routes(self) -> None:
        _, registration = self.external_registration()
        other_path = self.segment_path.with_name("jan7.json")
        self.write_json(other_path, self.payload)
        with mock.patch.object(v1, "EXTERNAL_SIDECAR_REGISTRATIONS_V1", registration):
            bundle = v1.load_adjacent_forum_system_event_revalidation(
                other_path,
                self.payload,
                source_artifact_sha256=v1.sha256_file(other_path),
                artifact_root=self.root,
            )
        self.assertFalse(bundle.provided)
        self.assertFalse(bundle.errors)


class CurrentJan6RevalidatedCopyTests(unittest.TestCase):
    def test_copy_is_hash_bound_to_the_immutable_stage_and_dom_evidence(self) -> None:
        root = Path(__file__).resolve().parent
        copy_path = root / (
            "raw/quarantine_collection_errors/terra_premium_journals_daily_2026-01-06_20260722T041222Z/"
            "v2_6_revalidated/system_event_timestamp_revalidated_v1/"
            "channel_premium_journals_1283941772577472643_2026-01-06_2026-01-06.json"
        )
        payload = json.loads(copy_path.read_text(encoding="utf-8"))
        bundle = timestamp_scope.load_adjacent_timestamp_scope_revalidation(
            copy_path, payload, source_artifact_sha256=v1.sha256_file(copy_path), artifact_root=root
        )
        audit = timestamp_scope.audit_segment_timestamp_scopes(payload["messages"], bundle)
        self.assertTrue(audit["passed"], audit)
        self.assertEqual(2, audit["mode_counts"][v1.FALLBACK_SOURCE + "_sidecar_revalidated"])
        self.assertEqual("a43e51c3e78fe88c7daedc5e9b683bead1fad9fb18c16910a6c04ce5e41e3786", v1.sha256_file(root / "raw/quarantine_collection_errors/terra_premium_journals_daily_2026-01-06_20260722T041222Z/v2_6_revalidated/channel_premium_journals_1283941772577472643_2026-01-06_2026-01-06.json"))

    def test_authoritative_canonical_uses_only_registered_external_evidence(self) -> None:
        root = Path(__file__).resolve().parent
        canonical_path = root / (
            "raw/channel_segments_v2_5/"
            "channel_premium_journals_1283941772577472643_"
            "2026-01-06_2026-01-06.json"
        )
        self.assertEqual(
            "5e239835f54718999d8aee59503851734713a4c2aa691e2fa28cc1ad10434487",
            v1.sha256_file(canonical_path),
        )
        self.assertFalse(v1.sidecar_path(canonical_path).exists())
        payload = json.loads(canonical_path.read_text(encoding="utf-8"))
        bundle = timestamp_scope.load_adjacent_timestamp_scope_revalidation(
            canonical_path,
            payload,
            source_artifact_sha256=v1.sha256_file(canonical_path),
            artifact_root=root,
        )
        audit = timestamp_scope.audit_segment_timestamp_scopes(
            payload["messages"], bundle
        )
        self.assertTrue(audit["passed"], audit)
        self.assertEqual(
            {
                v1.FALLBACK_SOURCE + "_sidecar_revalidated": 2,
                "message_timestamp_aria_exact": 313,
            },
            audit["mode_counts"],
        )
        forum_bundle = bundle.forum_system_event_revalidation
        self.assertIsNotNone(forum_bundle)
        self.assertEqual("registered_external_v1", forum_bundle.sidecar_resolution)
        self.assertTrue(forum_bundle.summary()["valid"])
        self.assertEqual(
            "bc404665c81e948229d8f85cf2ab7c8a1e59a1d08f4deea456ab26f0700bc3f4",
            forum_bundle.sidecar_sha256,
        )
        self.assertNotEqual(canonical_path.parent, forum_bundle.sidecar_path.parent)


class CurrentJan9RevalidatedCopyTests(unittest.TestCase):
    @staticmethod
    def paths() -> tuple[Path, Path, Path, Path, Path]:
        root = Path(__file__).resolve().parent
        stage = root / (
            "raw/quarantine_collection_errors/"
            "terra_premium_journals_daily_2026-01-09_20260722T091933Z/"
            "v2_6_revalidated"
        )
        source = stage / (
            "channel_premium_journals_1283941772577472643_"
            "2026-01-09_2026-01-09.json"
        )
        revalidated = stage / "system_event_timestamp_revalidated_v1" / source.name
        observation = stage / "system_event_dom_evidence_v1" / (
            "message_1459342322675224696.normalized_dom_observation.json"
        )
        manifest = stage / "system_event_dom_evidence_v1" / "manifest.json"
        return root, source, revalidated, observation, manifest

    def test_copy_is_exactly_hash_bound_and_timestamp_complete(self) -> None:
        root, source, revalidated, observation, manifest = self.paths()
        self.assertEqual(
            "02e2df498f63063fa7f5f0c202c133fc3f7599ed10726f49dca14fc34e90c4bc",
            v1.sha256_file(source),
        )
        self.assertEqual(
            "399f0df8ef52878442542043c3d64c0a4cb8070bac5dd0b8df58fcebf2df87ae",
            v1.sha256_file(revalidated),
        )
        self.assertEqual(
            "6ce29868f0d8029fe89bfc24e375536807e205edc3cf762a231802614413327e",
            v1.sha256_file(observation),
        )
        self.assertEqual(
            "97f5661d661d55a08a2f48eb228ac5ed3ca00cf2dda785ae9181d2d79a6e3e27",
            v1.sha256_file(manifest),
        )
        payload = json.loads(revalidated.read_text(encoding="utf-8"))
        bundle = timestamp_scope.load_adjacent_timestamp_scope_revalidation(
            revalidated,
            payload,
            source_artifact_sha256=v1.sha256_file(revalidated),
            artifact_root=root,
        )
        audit = timestamp_scope.audit_segment_timestamp_scopes(
            payload["messages"], bundle
        )
        self.assertTrue(audit["passed"], audit)
        self.assertEqual(
            {
                v1.FALLBACK_SOURCE + "_sidecar_revalidated": 1,
                "message_timestamp_aria_exact": 193,
            },
            audit["mode_counts"],
        )
        forum_bundle = bundle.forum_system_event_revalidation
        self.assertIsNotNone(forum_bundle)
        self.assertTrue(forum_bundle.summary()["valid"], forum_bundle.summary())
        self.assertEqual(
            "6536558fb260f5be9c87a8877ec0266d48ae6a4124820216613a8bf655e152b2",
            forum_bundle.sidecar_sha256,
        )

    def test_revalidated_copy_is_an_exact_one_row_delta(self) -> None:
        _, source, revalidated, _, _ = self.paths()
        original = json.loads(source.read_text(encoding="utf-8"))
        observed = json.loads(revalidated.read_text(encoding="utf-8"))
        expected = copy.deepcopy(original)
        row = next(
            item
            for item in expected["messages"]
            if item.get("message_id") == "1459342322675224696"
        )
        row.update(v1._expected_correction(row["timestamp_utc"]))
        self.assertEqual(expected, observed)
        registration = v1.EXTERNAL_SIDECAR_REGISTRATIONS_V1[
            "raw/channel_segments_v2_5/"
            "channel_premium_journals_1283941772577472643_"
            "2026-01-09_2026-01-09.json"
        ]
        self.assertEqual(v1.sha256_file(revalidated), registration["source_artifact_sha256"])
        self.assertEqual(
            "0dc3951fca360c49c506174cad220b6e6e9b26b3259e86bca2df03a02f5844e1",
            registration["sidecar_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
