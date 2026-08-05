from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import convert_legacy_premium_journals_v2 as migrator


UTC = dt.timezone.utc


class LegacyPremiumJournalsMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def source_descriptor(self, message: dict) -> migrator.SourceSegment:
        return migrator.SourceSegment(
            path=self.root / "source.json",
            source_kind="test_source",
            source_collection="primary_messages",
            start=dt.date(2026, 4, 20),
            end=dt.date(2026, 4, 20),
            query="in:premium-journals after:2026-04-19 before:2026-04-21",
            complete=True,
            reported_total=1,
            reported_pages=1,
            pages_captured=1,
            captured_rows=1,
            unique_message_ids=1,
            gap_indices=[],
            messages=[message],
        )

    def basic_message(self, created_at: dt.datetime, **updates: object) -> dict:
        message_id = migrator.snowflake_id_for_datetime(created_at)
        message = {
            "message_id": message_id,
            "timestamp_utc": migrator.iso_z(created_at),
            "content_text": "source-authored message",
            "visible_text": "source-authored message",
            "reply_context": "",
            "reply_to_content": "",
            "search_query": "in:premium-journals after:2026-04-19 before:2026-04-21",
            "parent_channel": "premium-journals",
            "thread_title": "Journal",
            "result_index": 1,
            "page_number": 1,
            "attachments": [],
            "links": [],
            "image_alt": [],
        }
        message.update(updates)
        return message

    def test_snowflake_timestamp_is_canonical_and_original_payload_is_preserved(self) -> None:
        canonical = dt.datetime(2026, 4, 20, 17, 30, 15, 321000, tzinfo=UTC)
        captured = "2026-04-20T17:29:00.000Z"
        original = self.basic_message(canonical, timestamp_utc=captured)
        source = self.source_descriptor(original)

        converted, quarantine = migrator.convert_message(
            original,
            source=source,
            source_relative_path="three_month_segments/source.json",
            source_sha256="a" * 64,
            row_index=0,
        )

        self.assertEqual(converted["timestamp_utc"], migrator.iso_z(canonical))
        self.assertEqual(converted["snowflake_timestamp_utc"], migrator.iso_z(canonical))
        self.assertEqual(converted["legacy_captured_timestamp_utc"], captured)
        self.assertEqual(converted["legacy_original_payload"], original)
        self.assertEqual(converted["legacy_original_payload"]["timestamp_utc"], captured)
        self.assertIsNotNone(quarantine)
        self.assertIn(
            "legacy_captured_timestamp_snowflake_mismatch_gt_1000ms",
            quarantine["reasons"],
        )

    def test_reply_preview_timestamp_and_content_contamination_are_quarantined(self) -> None:
        canonical = dt.datetime(2026, 4, 21, 19, 32, 44, 356000, tzinfo=UTC)
        preview = (
            "@User\nPreview text copied instead of the reply body (edited)\n"
            "Tuesday, April 21, 2026 at 2:25 PM"
        )
        original = self.basic_message(
            canonical,
            timestamp_utc="2026-04-21T19:25:48.801Z",
            content_text=(
                "Preview text copied instead of the reply body (edited)\n"
                "Tuesday, April 21, 2026 at 2:25 PM"
            ),
            reply_context=preview,
            reply_to_content=preview,
        )
        source = self.source_descriptor(original)

        converted, quarantine = migrator.convert_message(
            original,
            source=source,
            source_relative_path="three_month_segments/source.json",
            source_sha256="b" * 64,
            row_index=0,
        )

        self.assertFalse(converted["content_scope_exact"])
        self.assertFalse(converted["legacy_timestamp_scope_exact"])
        self.assertIsNotNone(quarantine)
        self.assertIn("reply_preview_content_contamination_suspected", quarantine["reasons"])
        self.assertIn("reply_preview_timestamp_contamination_suspected", quarantine["reasons"])
        self.assertIn("rendered_timestamp_embedded_in_content", quarantine["reasons"])
        self.assertTrue(
            converted["legacy_contamination_audit"][
                "legacy_captured_timestamp_matches_reply_preview_minute"
            ]
        )

    def test_inferred_locator_is_preserved_but_never_promoted_to_exact(self) -> None:
        created = dt.datetime(2026, 4, 20, 17, 0, tzinfo=UTC)
        message_id = migrator.snowflake_id_for_datetime(created)
        thread_id = migrator.snowflake_id_for_datetime(created, increment=5)
        original = self.basic_message(
            created,
            message_id=message_id,
            inferred_thread_channel_id=thread_id,
            inferred_permalink=(
                f"https://discord.com/channels/{migrator.GUILD_ID}/{thread_id}/{message_id}"
            ),
        )
        audit, reasons = migrator.locator_audit(original, message_id)
        self.assertEqual(audit["thread_locator_confidence"], "inferred")
        self.assertEqual(audit["permalink_confidence"], "inferred")
        self.assertFalse(audit["inferred_values_promoted_to_exact"])
        self.assertIn("exact_thread_id_unavailable", reasons)
        self.assertIn("exact_permalink_unavailable", reasons)

    def test_invalid_undefined_guild_permalink_is_flagged(self) -> None:
        created = dt.datetime(2026, 4, 20, 17, 0, tzinfo=UTC)
        message_id = migrator.snowflake_id_for_datetime(created)
        thread_id = migrator.snowflake_id_for_datetime(created, increment=6)
        original = self.basic_message(
            created,
            message_id=message_id,
            inferred_thread_channel_id=thread_id,
            inferred_permalink=(
                f"https://discord.com/channels/undefined/{thread_id}/{message_id}"
            ),
        )
        audit, reasons = migrator.locator_audit(original, message_id)
        self.assertEqual(audit["permalink_confidence"], "invalid")
        self.assertIn("inferred_permalink_invalid", reasons)

    def test_output_inside_or_above_protected_raw_is_rejected(self) -> None:
        protected = self.root / "corpus" / "raw" / "channel_segments"
        protected.mkdir(parents=True)
        with self.assertRaises(migrator.MigrationError):
            migrator.ensure_safe_output(protected, protected)
        with self.assertRaises(migrator.MigrationError):
            migrator.ensure_safe_output(protected / "staging", protected)
        with self.assertRaises(migrator.MigrationError):
            migrator.ensure_safe_output(self.root / "corpus", protected)

    def test_two_source_integration_emits_manifest_and_preserves_raw(self) -> None:
        three_month = self.root / "three_month_segments"
        three_month.mkdir()
        protected = self.root / "corpus" / "raw" / "channel_segments"
        protected.mkdir(parents=True)
        sentinel = protected / "existing.json"
        sentinel.write_text('{"do_not_touch":true}\n', encoding="utf-8")
        sentinel_hash = migrator.sha256_file(sentinel)

        first_time = dt.datetime(2026, 4, 20, 17, 0, tzinfo=UTC)
        first = self.basic_message(first_time)
        first_query = first["search_query"]
        segment_payload = {
            "segment": {"start": "2026-04-20", "end": "2026-04-20", "query": first_query},
            "reported_total": 1,
            "reported_pages": 1,
            "pages_captured": 1,
            "captured_rows": 1,
            "unique_message_ids": 1,
            "gap_indices": [],
            "complete": True,
            "messages": [first],
        }
        (three_month / "primary_2026-04-20_2026-04-20.json").write_text(
            json.dumps(segment_payload), encoding="utf-8"
        )

        second_time = dt.datetime(2026, 4, 21, 17, 0, tzinfo=UTC)
        second_id = migrator.snowflake_id_for_datetime(second_time)
        thread_id = migrator.snowflake_id_for_datetime(second_time, increment=9)
        second_query = "in:premium-journals after:2026-04-20 before:2026-04-22"
        second = self.basic_message(
            second_time,
            message_id=second_id,
            search_query=second_query,
            thread_channel_id=thread_id,
            permalink=(
                f"https://discord.com/channels/{migrator.GUILD_ID}/{thread_id}/{second_id}"
            ),
        )
        baseline = self.root / "raw_discord_export.json"
        baseline.write_text(
            json.dumps(
                {
                    "metadata": {
                        "primary_search_complete": True,
                        "primary_result_count": 1,
                        "primary_channel_id": migrator.CHANNEL_ID,
                        "primary_channel_name": migrator.CHANNEL_NAME,
                        "collected_at_utc": "2026-04-22T00:00:00.000Z",
                    },
                    "primary_messages": [second],
                }
            ),
            encoding="utf-8",
        )

        output = self.root / "staging" / "migration"
        manifest = migrator.run_migration(
            three_month_dir=three_month,
            baseline_path=baseline,
            output_dir=output,
            protected_raw_dir=protected,
            window=migrator.MigrationWindow(
                start=dt.date(2026, 4, 20),
                segment_end=dt.date(2026, 4, 20),
                tail_start=dt.date(2026, 4, 21),
                end=dt.date(2026, 4, 21),
            ),
            generated_at_utc="2026-07-20T23:30:00.000Z",
        )

        self.assertEqual(manifest["coverage"]["segment_count"], 2)
        self.assertEqual(manifest["preservation"]["input_occurrence_count"], 2)
        self.assertEqual(manifest["preservation"]["output_occurrence_count"], 2)
        self.assertEqual(manifest["preservation"]["missing_input_occurrences"], 0)
        self.assertTrue(manifest["staging"]["protected_raw_unchanged"])
        self.assertFalse(
            manifest["validation"]["canonical_raw_channel_segments_mutated"]
        )
        self.assertEqual(migrator.sha256_file(sentinel), sentinel_hash)
        self.assertEqual(len(list((output / "segments").glob("*.json"))), 2)

        staged_first = json.loads(
            (
                output
                / "segments"
                / f"channel_premium_journals_{migrator.CHANNEL_ID}_2026-04-20_2026-04-20.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(staged_first["messages"][0]["legacy_original_payload"], first)
        self.assertEqual(
            staged_first["messages"][0]["timestamp_utc"], migrator.iso_z(first_time)
        )
        self.assertTrue(
            (output / "legacy_premium_journals_v2_manifest.json").is_file()
        )
        self.assertTrue(
            (output / "legacy_premium_journals_v2_quarantine.jsonl").is_file()
        )


if __name__ == "__main__":
    unittest.main()
