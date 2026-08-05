from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import build_cardinal_database_v2 as builder  # noqa: E402
import build_discord_analysis_layer as analysis  # noqa: E402
import build_llm_companion as companion  # noqa: E402


def snowflake(timestamp: str, sequence: int) -> str:
    moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)
    milliseconds = int(moment.timestamp() * 1000)
    return str(((milliseconds - 1420070400000) << 22) + sequence)


def message(
    timestamp: str,
    sequence: int,
    text: str,
    *,
    author: str,
    channel_id: str,
    channel_name: str,
    reply_to: str | None = None,
) -> dict:
    message_id = snowflake(timestamp, sequence)
    return {
        "message_id": message_id,
        "channel_id": channel_id,
        "inferred_thread_channel_id": channel_id,
        "thread_title": channel_name,
        "parent_channel": "PREMIUM",
        "author": author,
        "timestamp_utc": timestamp,
        "content_text": text,
        "visible_text": text,
        "reply_to_message_id": reply_to,
        "reply_to_content": "",
        "inferred_permalink": (
            "https://discord.com/channels/1167376964680691732/"
            f"{channel_id}/{message_id}"
        ),
        "attachments": [],
    }


def fixture() -> dict:
    question = message(
        "2026-01-02T14:00:00Z",
        1,
        "How do I identify a valid rejection block at 10am?",
        author="Student",
        channel_id="1273692573898113076",
        channel_name="questions",
    )
    question["attachments"] = [
        {
            "attachment_id": "1364178305632174100",
            "filename": "schizophrenicistalking.gif",
            "url": (
                "https://cdn.discordapp.com/attachments/1278211283656773643/"
                "1364178305632174100/schizophrenicistalking.gif"
            ),
            "thread_channel_id": "1278211283656773643",
            "dom_relation": "embed_descendant",
            "href_in_message_content": False,
            "relation_type": "embedded_external",
            "ownership_status": "non_owned_exact",
            "ownership_evidence": {
                "schema_version": "1.0.0",
                "exact": True,
                "basis": "discord_cdn_source_channel_differs_from_exact_message_container",
                "owner_message_id": question["message_id"],
                "owner_channel_id": "1273692573898113076",
                "source_channel_id": "1278211283656773643",
                "dom_relation": "embed_descendant",
            },
        }
    ]
    answer = message(
        "2026-01-02T14:01:00Z",
        2,
        "Wait for the RB to close and add confluences.",
        author="Member",
        channel_id="1273692573898113076",
        channel_name="questions",
        reply_to=question["message_id"],
    )
    win = message(
        "2026-01-03T15:05:00Z",
        3,
        (
            "DAY 1 Trade 1: I entered NQ long using a 5m RB after a liquidity "
            "sweep at 10am. ES was SMT context. TP hit +2R win."
        ),
        author="Trader One",
        channel_id="1283941772577472643",
        channel_name="journal-one",
    )
    loss = message(
        "2026-01-04T15:10:00Z",
        4,
        (
            "DAY 1 Trade 1: I entered ES short using a 5m rejection block and "
            "FVG at 10am. Stopped out -1R loss."
        ),
        author="Trader Two",
        channel_id="1283941772577472643",
        channel_name="journal-two",
    )
    quarantined = message(
        "2026-01-05T15:05:00Z",
        5,
        "quarantineonlytoken NQ rejection block TP hit win.",
        author="Legacy Trader",
        channel_id="1283941772577472643",
        channel_name="legacy-journal",
    )
    quarantined.update(
        {
            "migration_quarantined": True,
            "migration_quarantine_reasons": ["synthetic_untrusted_migration"],
            "_migration_occurrence": {"occurrence_id": "legacy_occ:llm-companion"},
            "_padding_for_raw_retention_test": "x" * 200_000,
        }
    )
    return {
        "metadata": {
            "guild_id": "1167376964680691732",
            "guild_name": "fixture",
            "source_scope": "discord_only",
            "outside_sources_used": 0,
            "requested_window_start_date": "2026-01-01",
            "requested_window_end_date": "2026-07-20",
            "collection_status": "partial",
        },
        "messages": [question, answer, win, loss, quarantined],
    }


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


class LlmCompanionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        raw = self.folder / "fixture.json"
        base = self.folder / "base.sqlite"
        raw.write_text(json.dumps(fixture()), encoding="utf-8")
        builder.build_database(
            [raw],
            base,
            window_start="2026-01-01T06:00:00Z",
            window_end="2026-07-21T05:00:00Z",
        )
        self.base = base
        self.source = self.folder / "analyzed.sqlite"
        analysis.build_analysis(
            base,
            self.source,
            curated_path=ROOT / "curated_analysis_3month.json",
            model_analysis_path=ROOT / "model_analysis_3month.json",
            trade_script=ROOT / "build_trade_analysis_3month.py",
            rb_script=ROOT / "build_rb_analysis_3month.py",
            model_script=ROOT / "build_model_analysis_3month.py",
            replace=False,
            min_candidate_score=4,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_builds_query_friendly_snapshot_without_changing_source(self) -> None:
        output = self.folder / "companion.sqlite"
        with closing(sqlite3.connect(self.source)) as source_con:
            run_id = source_con.execute(
                "SELECT analysis_run_id FROM analysis_runs LIMIT 1"
            ).fetchone()[0]
            source_con.execute(
                """
                INSERT INTO analysis_entities(
                  entity_id,entity_type,created_analysis_run_id,lifecycle_status,
                  source_scope,outside_sources_used,notes
                ) VALUES('test:rb-finding','rejection_block_finding',?,'active','discord_only',0,'fixture')
                """,
                (run_id,),
            )
            source_con.execute(
                """
                INSERT INTO claims(
                  claim_id,subject_entity_id,facet,claim_text,claim_kind,
                  epistemic_status,resolution_status,analysis_run_id,source_scope,
                  outside_sources_used,created_at_utc,limitations
                ) VALUES(
                  'test:rb-finding-claim','test:rb-finding','identification',
                  'Fixture RB finding','curated_synthesis','curated_synthesis',
                  'qualified',?,'discord_only',0,'2026-07-20T00:00:00Z','fixture'
                )
                """,
                (run_id,),
            )
            source_con.commit()
        before = sha(self.source)
        report = companion.build_companion(self.source, output)

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["source_database_unchanged"])
        self.assertEqual(sha(self.source), before)
        self.assertTrue(output.is_file())
        self.assertLess(output.stat().st_size, self.source.stat().st_size)

        with closing(sqlite3.connect(output)) as con:
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM v_discord_only_audit").fetchone()[0], 0)
            external = con.execute(
                """
                SELECT relation_type,ownership_status,source_channel_id,
                       owned_for_capture,eligible_for_attachment_evidence,
                       local_package_path,content_sha256,extraction_status
                FROM attachments WHERE attachment_id='1364178305632174100'
                """
            ).fetchone()
            self.assertEqual(
                external,
                (
                    "embedded_external",
                    "non_owned_exact",
                    "1278211283656773643",
                    0,
                    0,
                    None,
                    None,
                    "not_attempted",
                ),
            )
            raw_json_columns = con.execute(
                """
                SELECT COUNT(*)
                FROM sqlite_master m JOIN pragma_table_info(m.name) p
                WHERE m.type='table' AND lower(p.name)='raw_json'
                """
            ).fetchone()[0]
            self.assertEqual(raw_json_columns, 0)
            self.assertEqual(
                con.execute("SELECT value FROM llm_manifest WHERE key='source_database_sha256'").fetchone()[0],
                before,
            )
            self.assertEqual(
                con.execute("SELECT value FROM llm_manifest WHERE key='source_scope'").fetchone()[0],
                "discord_only",
            )
            rb_finding = con.execute(
                """
                SELECT entity_type,claim_text FROM query_rejection_blocks
                WHERE observation_id='test:rb-finding'
                """
            ).fetchone()
            self.assertEqual(rb_finding, ("rejection_block_finding", "Fixture RB finding"))

            # Quarantined source text is retained for audit/search, but not in the
            # analysis-eligible view.
            quarantine_id = con.execute(
                "SELECT message_id FROM messages_fts WHERE messages_fts MATCH 'quarantineonlytoken'"
            ).fetchone()[0]
            self.assertEqual(
                con.execute(
                    "SELECT eligible_for_accepted_evidence FROM messages WHERE message_id=?",
                    (quarantine_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM v_analysis_eligible_messages WHERE message_id=?",
                    (quarantine_id,),
                ).fetchone()[0],
                0,
            )

            qa = con.execute(
                """
                SELECT question_status,answer_status,direct_reply,
                       question_messages_json,answer_messages_json
                FROM query_qa
                WHERE normalized_question LIKE 'How do I identify%'
                """
            ).fetchone()
            self.assertIsNotNone(qa)
            self.assertEqual(qa[0], "partial")
            self.assertEqual(qa[1], "community_only")
            self.assertEqual(qa[2], 1)
            self.assertIn("rejection block", qa[3])
            self.assertIn("Wait for the RB", qa[4])

            strict = con.execute(
                "SELECT COUNT(*) FROM v_strict_trade_episodes"
            ).fetchone()[0]
            self.assertGreaterEqual(strict, 2)
            roles = {
                (row[0], row[1])
                for row in con.execute(
                    """
                    SELECT i.canonical_symbol,si.role
                    FROM setup_instruments si JOIN instruments i USING(instrument_id)
                    """
                )
            }
            self.assertIn(("NQ", "executed"), roles)
            self.assertIn(("ES", "market_context"), roles)

    def test_refuses_unsafe_inputs_and_implicit_replacement(self) -> None:
        output = self.folder / "companion.sqlite"
        companion.build_companion(self.source, output)
        with self.assertRaises(FileExistsError):
            companion.build_companion(self.source, output)
        replaced = companion.build_companion(self.source, output, replace=True)
        self.assertEqual(replaced["status"], "passed")
        self.assertTrue(output.is_file())
        with self.assertRaises(companion.CompanionError):
            companion.build_companion(self.source, self.source)

        no_analysis = self.folder / "no_analysis.sqlite"
        shutil.copy2(self.base, no_analysis)
        rejected = self.folder / "rejected.sqlite"
        with self.assertRaisesRegex(companion.CompanionError, "analysis_layer_present"):
            companion.build_companion(no_analysis, rejected)
        self.assertFalse(rejected.exists())
        self.assertFalse(rejected.with_name(rejected.name + ".building").exists())

        outside = self.folder / "outside.sqlite"
        shutil.copy2(self.source, outside)
        with closing(sqlite3.connect(outside)) as con:
            con.execute("UPDATE meta SET value='1' WHERE key='outside_sources_used'")
            con.commit()
        outside_output = self.folder / "outside_companion.sqlite"
        with self.assertRaisesRegex(companion.CompanionError, "outside_sources_zero"):
            companion.build_companion(outside, outside_output)
        self.assertFalse(outside_output.exists())


if __name__ == "__main__":
    unittest.main()
