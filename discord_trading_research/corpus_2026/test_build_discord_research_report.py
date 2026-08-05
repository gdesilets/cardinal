from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cardinal = load_module("report_fixture_cardinal", HERE / "build_cardinal_database_v2.py")
analysis = load_module("report_fixture_analysis", HERE / "build_discord_analysis_layer.py")
reporter = load_module("report_test_target", HERE / "build_discord_research_report.py")


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
    author_id: str,
    channel_id: str,
    channel_name: str,
    reply_to: str | None = None,
) -> dict:
    message_id = snowflake(timestamp, sequence)
    row = {
        "message_id": message_id,
        "message_id_exact": True,
        "guild_id": reporter.EXPECTED_GUILD_ID,
        "collection_channel_id": channel_id,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "thread_title": channel_name,
        "author": author,
        "author_id": author_id,
        "timestamp_utc": timestamp,
        "content_text": text,
        "visible_text": text,
        "reply_to_message_id": reply_to,
        "reply_to_content": "",
        "inferred_permalink": (
            f"https://discord.com/channels/{reporter.EXPECTED_GUILD_ID}/{channel_id}/{message_id}"
        ),
        "attachments": [],
    }
    return row


def fixture_messages() -> list[dict]:
    question = message(
        "2026-01-02T14:00:00Z",
        1,
        "How do I identify a rejection block at 10am?",
        author="Student",
        author_id="100000000000000001",
        channel_id="1273692573898113076",
        channel_name="questions",
    )
    answer = message(
        "2026-01-02T14:01:00Z",
        2,
        "The captured answer says to wait for the RB close and add confluences.",
        author="Member",
        author_id="100000000000000002",
        channel_id="1273692573898113076",
        channel_name="questions",
        reply_to=question["message_id"],
    )
    unanswered = message(
        "2026-01-02T15:00:00Z",
        3,
        "Does a mitigated rejection block remain valid on ES?",
        author="Student Two",
        author_id="100000000000000003",
        channel_id="1273692573898113076",
        channel_name="questions",
    )
    win = message(
        "2026-01-03T15:05:00Z",
        4,
        "I entered NQ long using a 5m RB after a liquidity sweep at 10am. ES was SMT context. TP hit +2R win.",
        author="Trader One",
        author_id="100000000000000004",
        channel_id="1283941772577472643",
        channel_name="journal-one",
    )
    loss = message(
        "2026-01-04T15:10:00Z",
        5,
        "I entered ES short using a 5m rejection block and FVG at 10am. Stopped out -1R loss.",
        author="Trader Two",
        author_id="100000000000000005",
        channel_id="1283941772577472643",
        channel_name="journal-two",
    )
    return [question, answer, unanswered, win, loss]


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def update_document(con: sqlite3.Connection, name: str, mutate) -> None:
    row = con.execute(
        "SELECT content_json FROM analysis_documents WHERE document_name=?", (name,)
    ).fetchone()
    assert row
    value = json.loads(row[0])
    mutate(value)
    con.execute(
        "UPDATE analysis_documents SET content_json=? WHERE document_name=?",
        (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), name),
    )


def insert_entity(con: sqlite3.Connection, entity_id: str, entity_type: str, run_id: int) -> None:
    con.execute(
        """
        INSERT INTO analysis_entities(
          entity_id,entity_type,created_analysis_run_id,parent_entity_id,root_entity_id,
          lifecycle_status,source_scope,outside_sources_used,notes
        ) VALUES(?,?,?,NULL,?,'active','discord_only',0,'test fixture')
        """,
        (entity_id, entity_type, run_id, entity_id),
    )


def insert_claim(
    con: sqlite3.Connection,
    *,
    claim_id: str,
    entity_id: str,
    facet: str,
    text: str,
    run_id: int,
    evidence_id: str,
) -> None:
    con.execute(
        """
        INSERT INTO claims(
          claim_id,subject_entity_id,facet,claim_text,normalized_value_json,
          claim_kind,epistemic_status,resolution_status,speaker_author_id,
          authority_assignment_id,analysis_run_id,source_scope,outside_sources_used,
          created_at_utc,limitations
        ) VALUES(?,?,?,?,NULL,'explicit_rule','explicit_source','qualified',NULL,NULL,?,
                 'discord_only',0,'2026-07-21T05:00:00Z','Fixture source claim.')
        """,
        (claim_id, entity_id, facet, text, run_id),
    )
    con.execute(
        "INSERT INTO claim_evidence(claim_id,evidence_id,evidence_role) VALUES(?,?,'supports')",
        (claim_id, evidence_id),
    )


class ResearchReportTests(unittest.TestCase):
    def build_release_fixture(self, folder: Path, *, add_model: bool = True) -> Path:
        raw = folder / "fixture.json"
        base = folder / "base.sqlite"
        analyzed = folder / "analyzed.sqlite"
        messages = fixture_messages()
        raw.write_text(
            json.dumps(
                {
                    "metadata": {
                        "guild_id": reporter.EXPECTED_GUILD_ID,
                        "guild_name": "Discord fixture",
                        "source_scope": "discord_only",
                        "outside_sources_used": 0,
                        "requested_window_start_date": "2026-01-01",
                        "requested_window_end_date": "2026-07-20",
                        "collection_status": "partial",
                    },
                    "messages": messages,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        cardinal.build_database(
            [raw],
            base,
            window_start=reporter.EXPECTED_WINDOW_START_UTC,
            window_end=reporter.EXPECTED_WINDOW_END_UTC,
        )
        analysis.build_analysis(
            base,
            analyzed,
            curated_path=ROOT / "curated_analysis_3month.json",
            model_analysis_path=ROOT / "model_analysis_3month.json",
            trade_script=ROOT / "build_trade_analysis_3month.py",
            rb_script=ROOT / "build_rb_analysis_3month.py",
            model_script=ROOT / "build_model_analysis_3month.py",
            replace=False,
            min_candidate_score=4,
        )

        with closing(sqlite3.connect(analyzed)) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("UPDATE collection_runs SET status='complete'")
            con.execute(
                "UPDATE collection_units SET status='complete',artifact_declared_complete=1,gap_notes=''"
            )
            con.execute(
                "UPDATE coverage_segments SET status='complete',error_text=NULL"
            )
            update_document(
                con,
                "discord_analysis_coverage",
                lambda value: value.update(
                    {
                        "analysis_completeness": "complete",
                        "collection_run_status": "complete",
                        "collection_unit_status_counts": {"complete": con.execute(
                            "SELECT COUNT(*) FROM collection_units"
                        ).fetchone()[0]},
                        "gap_count": 0,
                        "gap_sample": [],
                    }
                ),
            )

            run_id = int(con.execute("SELECT analysis_run_id FROM analysis_runs").fetchone()[0])
            evidence_row = con.execute(
                """
                SELECT evidence_id,message_id FROM evidence_items
                WHERE eligible_for_accepted_claims=1
                ORDER BY evidence_id LIMIT 1
                """
            ).fetchone()
            assert evidence_row
            evidence_id = str(evidence_row["evidence_id"])
            evidence_message_id = str(evidence_row["message_id"])

            for suffix, facet, text in (
                ("technical", "technical_invalidation", "Stored technical invalidation condition."),
                ("action", "non_actionability", "Stored non-actionability condition."),
                (
                    "combined",
                    "invalidation_or_non_actionability",
                    "Stored combined condition that must not be reclassified from its text.",
                ),
            ):
                entity_id = f"rb-fixture:{suffix}"
                claim_id = f"claim:rb-fixture:{suffix}"
                insert_entity(con, entity_id, "rejection_block_finding", run_id)
                insert_claim(
                    con,
                    claim_id=claim_id,
                    entity_id=entity_id,
                    facet=facet,
                    text=text,
                    run_id=run_id,
                    evidence_id=evidence_id,
                )

            contradiction_id = "contradiction:fixture"
            insert_entity(con, contradiction_id, "contradiction", run_id)
            con.execute(
                """
                INSERT INTO contradiction_sets(
                  contradiction_id,topic,resolution_status,resolution_summary,resolved_claim_id,limitations
                ) VALUES(?, 'RB invalidation', 'open', '', NULL, 'Fixture unresolved conflict.')
                """,
                (contradiction_id,),
            )
            con.execute(
                "INSERT INTO contradiction_members(contradiction_id,claim_id,stance,notes) VALUES(?,?,?,?)",
                (contradiction_id, "claim:rb-fixture:technical", "supports", "fixture"),
            )
            con.execute(
                "INSERT INTO contradiction_members(contradiction_id,claim_id,stance,notes) VALUES(?,?,?,?)",
                (contradiction_id, "claim:rb-fixture:action", "opposes", "fixture"),
            )

            if add_model:
                model_id = "model:fixture"
                identity_claim = "claim:model:fixture"
                insert_entity(con, model_id, "setup_model", run_id)
                insert_claim(
                    con,
                    claim_id=identity_claim,
                    entity_id=model_id,
                    facet="model_identity",
                    text="Stored fixture model identity.",
                    run_id=run_id,
                    evidence_id=evidence_id,
                )
                con.execute(
                    """
                    INSERT INTO setup_models(
                      model_id,canonical_name,thesis,evidence_status,lifecycle_status,
                      identity_claim_id,limitations
                    ) VALUES(?, 'Fixture RB model', 'Stored Discord-derived thesis.',
                             'documented','active',?,'Descriptive selected-corpus support only.')
                    """,
                    (model_id, identity_claim),
                )
                rule_id = "model-rule:fixture"
                rule_claim = "claim:model-rule:fixture"
                insert_entity(con, rule_id, "setup_model_rule", run_id)
                insert_claim(
                    con,
                    claim_id=rule_claim,
                    entity_id=rule_id,
                    facet="model_rule:entry",
                    text="Use the stored Discord entry condition.",
                    run_id=run_id,
                    evidence_id=evidence_id,
                )
                con.execute(
                    """
                    INSERT INTO setup_model_rules(
                      rule_id,model_id,rule_order,rule_type,rule_text,required_state,claim_id
                    ) VALUES(?,?,1,'entry','Use the stored Discord entry condition.','required',?)
                    """,
                    (rule_id, model_id, rule_claim),
                )

                def model_doc(value: dict) -> None:
                    value["models"] = [
                        {
                            "source_model_id": "fixture",
                            "model_id": model_id,
                            "candidate_origin": "full_window_recurrent_signature_discovery",
                            "name": "Fixture RB model",
                            "material_distinction": "Stored Discord-derived thesis.",
                            "candidate_signature": [
                                "feature:fixture_a",
                                "feature:fixture_b",
                            ],
                            "unresolved_rule_facets": ["invalidation", "target"],
                            "contradictions_or_counterevidence": [],
                            "rules": [
                                {
                                    "order": 1,
                                    "type": "entry",
                                    "text": "Use the stored Discord entry condition.",
                                    "required_state": "required",
                                    "evidence_message_ids": [evidence_message_id],
                                }
                            ],
                            "matched_episode_records": 0,
                            "matched_author_concentration": {
                                "distinct_authors": 0,
                                "top_author_share": None,
                            },
                            "strict_selected_corpus": {
                                "wins": 0,
                                "losses": 0,
                                "eligible_count": 0,
                                "descriptive_win_share": None,
                                "distinct_authors": 0,
                                "top_author_share": None,
                            },
                            "warning": reporter.RATE_WARNING,
                        }
                    ]
                    value["models_emitted"] = 1
                    value["discovery"] = {
                        "retained_legacy_models": 0,
                        "promoted_novel_models": 1,
                        "models_emitted": 1,
                        "maximum_models": 5,
                        "fifth_model_forced": False,
                        "slot_policy": "Fixture full-window evidence gate.",
                        "novel_candidate_discovery": {
                            "method": "fixture_exhaustive_full_window_method",
                            "trust_eligible_strict_episodes_scanned": 6,
                            "candidate_signatures_enumerated": 12,
                            "distinct_novel_candidates_pre_slot_limit": 1,
                            "rejection_reason_counts": {
                                "below_minimum_strict_episodes": 4
                            },
                        },
                    }

                update_document(con, "discord_model_cards", model_doc)

            con.commit()
        return analyzed

    def test_deterministic_report_contains_all_requested_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            database = self.build_release_fixture(folder)
            database_before = hash_file(database)
            md_one = folder / "one.md"
            json_one = folder / "one.json"
            md_two = folder / "two.md"
            json_two = folder / "two.json"
            first = reporter.build_reports(database, md_one, json_one)
            second = reporter.build_reports(database, md_two, json_two)
            self.assertEqual(md_one.read_bytes(), md_two.read_bytes())
            self.assertEqual(json_one.read_bytes(), json_two.read_bytes())
            self.assertEqual(hash_file(database), database_before)
            self.assertEqual(first["database_sha256"], second["database_sha256"])

            payload = json.loads(json_one.read_text(encoding="utf-8"))
            self.assertEqual(payload["claim_scope"], "discord_only")
            self.assertEqual(payload["outside_sources_used"], 0)
            self.assertEqual(payload["release_validation"]["status"], "passed")
            self.assertEqual(
                payload["release_validation"]["attachment_archive"][
                    "owned_attachment_count"
                ],
                0,
            )
            self.assertEqual(
                payload["scope_and_coverage"]["attachment_archive"]["release_status"],
                "not_required",
            )
            self.assertTrue(
                payload["trade_profiles"][
                    "all_rate_claims_are_descriptive_self_reported_non_causal"
                ]
            )
            self.assertFalse(
                payload["trade_profiles"]["forward_probability_or_expectancy_claimed"]
            )
            self.assertIn("executed_instrument_comparison", payload["trade_profiles"])
            self.assertIn("market_context_instrument_mentions", payload["trade_profiles"])
            self.assertEqual(
                len(payload["trade_profiles"]["strict_trade_evidence"]),
                payload["trade_profiles"]["overall"]["eligible_count"],
            )
            self.assertIn("instrument_role_evidence", payload["trade_profiles"])
            self.assertEqual(payload["model_cards"]["models_emitted"], 1)
            self.assertFalse(payload["model_cards"]["fifth_model_forced"])
            self.assertEqual(
                payload["model_cards"]["discovery"]["promoted_novel_models"], 1
            )
            self.assertEqual(
                payload["model_cards"]["models"][0]["candidate_origin"],
                "full_window_recurrent_signature_discovery",
            )
            self.assertGreaterEqual(
                payload["question_and_answer_catalog"]["status_counts"]["partial"], 1
            )
            self.assertGreaterEqual(
                payload["question_and_answer_catalog"]["status_counts"]["unanswered"], 1
            )
            self.assertEqual(payload["contradictions"]["set_count"], 1)
            self.assertTrue(payload["evidence_catalog"])
            markdown = md_one.read_text(encoding="utf-8")
            for heading in (
                "## Rejection blocks: identification evidence",
                "## Invalidation and non-actionability remain separate",
                "## Explicit setup times and sessions",
                "## Higher and lower selected-corpus confluence profiles",
                "## Strict self-reported win and loss profiles",
                "## NQ and ES: executed role is not market context",
                "## Evidence-backed trading model cards",
                "## Relevant Discord questions and captured answers",
                "## Evidence-bounded next steps",
                "## Further questions retained by the evidence",
                "## Evidence reference catalog",
            ):
                self.assertIn(heading, markdown)
            self.assertIn("descriptive", markdown.lower())
            self.assertIn("self-reported", markdown.lower())
            self.assertIn("non-causal", markdown.lower())
            self.assertIn("candidate signatures enumerated", markdown.lower())
            self.assertIn("unresolved explicit rule facets", markdown.lower())
            self.assertIn("owned discord attachments", markdown.lower())

    def test_source_facets_control_invalidation_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = self.build_release_fixture(Path(temporary))
            payload = reporter.build_report_data(database)
            invalidation = payload["rejection_blocks"]["invalidation"]
            self.assertEqual(
                [row["source_facet"] for row in invalidation["technical_invalidation_source_claims"]],
                ["technical_invalidation"],
            )
            self.assertEqual(
                [row["source_facet"] for row in invalidation["non_actionability_source_claims"]],
                ["non_actionability"],
            )
            self.assertEqual(
                [row["source_facet"] for row in invalidation["unclassified_combined_source_claims"]],
                ["invalidation_or_non_actionability"],
            )

    def test_partial_collection_fails_closed_without_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            database = self.build_release_fixture(folder)
            with closing(sqlite3.connect(database)) as con:
                con.execute("UPDATE collection_runs SET status='partial'")
                con.commit()
            markdown = folder / "blocked.md"
            structured = folder / "blocked.json"
            with self.assertRaisesRegex(reporter.ReportError, "not release-complete"):
                reporter.build_reports(database, markdown, structured)
            self.assertFalse(markdown.exists())
            self.assertFalse(structured.exists())

    def test_incomplete_owned_attachment_archive_fails_closed_without_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            database = self.build_release_fixture(folder)
            with closing(sqlite3.connect(database)) as con:
                message_id = str(
                    con.execute("SELECT message_id FROM messages ORDER BY message_id LIMIT 1").fetchone()[0]
                )
                con.execute(
                    """
                    INSERT INTO attachments(
                      attachment_id,message_id,filename,capture_status,
                      chart_claim_eligible,raw_json
                    ) VALUES(?,?,'owned-chart.png','metadata_only',0,'{}')
                    """,
                    ("1480000000000000999", message_id),
                )
                con.execute(
                    """
                    UPDATE meta SET value='0'
                    WHERE key IN (
                      'attachment_archive_terminal_coverage_complete',
                      'attachment_archive_literal_release_complete'
                    )
                    """
                )
                con.commit()
            markdown = folder / "blocked-attachment.md"
            structured = folder / "blocked-attachment.json"
            with self.assertRaisesRegex(
                reporter.ReportError, "Attachment archive is not release-complete"
            ):
                reporter.build_reports(database, markdown, structured)
            self.assertFalse(markdown.exists())
            self.assertFalse(structured.exists())
            self.assertFalse(markdown.with_suffix(markdown.suffix + ".tmp").exists())
            self.assertFalse(structured.with_suffix(structured.suffix + ".tmp").exists())

    def test_outside_source_or_discord_audit_failure_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = self.build_release_fixture(Path(temporary))
            with closing(sqlite3.connect(database)) as con:
                run_id = int(con.execute("SELECT analysis_run_id FROM analysis_runs").fetchone()[0])
                insert_entity(con, "rb-fixture:orphan", "rejection_block_finding", run_id)
                con.execute(
                    """
                    INSERT INTO claims(
                      claim_id,subject_entity_id,facet,claim_text,normalized_value_json,
                      claim_kind,epistemic_status,resolution_status,speaker_author_id,
                      authority_assignment_id,analysis_run_id,source_scope,outside_sources_used,
                      created_at_utc,limitations
                    ) VALUES('claim:orphan','rb-fixture:orphan','identification','Orphan claim',NULL,
                             'explicit_rule','explicit_source','accepted',NULL,NULL,?,
                             'discord_only',0,'2026-07-21T05:00:00Z','fixture')
                    """,
                    (run_id,),
                )
                con.commit()
            with self.assertRaisesRegex(reporter.ReportError, "Discord-only audit failed"):
                reporter.build_report_data(database)

    def test_untrusted_referenced_message_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = self.build_release_fixture(Path(temporary))
            with closing(sqlite3.connect(database)) as con:
                message_id = con.execute(
                    """
                    SELECT message_id FROM evidence_items
                    WHERE evidence_id=(
                      SELECT evidence_id FROM claim_evidence
                      WHERE claim_id='claim:rb-fixture:technical'
                    )
                    """
                ).fetchone()[0]
                con.execute(
                    "UPDATE messages SET eligible_for_accepted_evidence=0 WHERE message_id=?",
                    (message_id,),
                )
                con.commit()
            with self.assertRaisesRegex(
                reporter.ReportError, "Discord-only audit failed|not analysis-eligible"
            ):
                reporter.build_report_data(database)

    def test_no_model_is_valid_and_does_not_force_fifth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = self.build_release_fixture(Path(temporary), add_model=False)
            payload = reporter.build_report_data(database)
            self.assertEqual(payload["model_cards"]["models_emitted"], 0)
            self.assertEqual(payload["model_cards"]["models"], [])
            self.assertFalse(payload["model_cards"]["fifth_model_forced"])

    def test_missing_full_window_model_discovery_audit_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = self.build_release_fixture(Path(temporary), add_model=False)
            with closing(sqlite3.connect(database)) as con:
                def remove_discovery(value: dict) -> None:
                    value.pop("discovery", None)

                update_document(con, "discord_model_cards", remove_discovery)
                con.commit()
            with self.assertRaisesRegex(
                reporter.ReportError, "lacks the full-window discovery audit"
            ):
                reporter.build_report_data(database)

    def test_more_than_five_models_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = self.build_release_fixture(Path(temporary), add_model=False)
            with closing(sqlite3.connect(database)) as con:
                con.row_factory = sqlite3.Row
                run_id = int(con.execute("SELECT analysis_run_id FROM analysis_runs").fetchone()[0])
                evidence_id, message_id = con.execute(
                    "SELECT evidence_id,message_id FROM evidence_items WHERE eligible_for_accepted_claims=1 LIMIT 1"
                ).fetchone()
                cards = []
                for index in range(6):
                    model_id = f"model:overflow:{index}"
                    claim_id = f"claim:model:overflow:{index}"
                    insert_entity(con, model_id, "setup_model", run_id)
                    insert_claim(
                        con,
                        claim_id=claim_id,
                        entity_id=model_id,
                        facet="model_identity",
                        text=f"Model {index}",
                        run_id=run_id,
                        evidence_id=str(evidence_id),
                    )
                    con.execute(
                        """
                        INSERT INTO setup_models(
                          model_id,canonical_name,thesis,evidence_status,lifecycle_status,
                          identity_claim_id,limitations
                        ) VALUES(?,?,?,'documented','active',?,'fixture')
                        """,
                        (model_id, f"Model {index}", f"Thesis {index}", claim_id),
                    )
                    cards.append(
                        {
                            "model_id": model_id,
                            "name": f"Model {index}",
                            "rules": [
                                {
                                    "order": 1,
                                    "type": "entry",
                                    "text": "Stored rule",
                                    "required_state": "required",
                                    "evidence_message_ids": [str(message_id)],
                                }
                            ],
                            "strict_selected_corpus": {
                                "wins": 0,
                                "losses": 0,
                                "eligible_count": 0,
                                "descriptive_win_share": None,
                            },
                        }
                    )

                def overflow(value: dict) -> None:
                    value["models"] = cards
                    value["models_emitted"] = 6

                update_document(con, "discord_model_cards", overflow)
                con.commit()
            with self.assertRaisesRegex(reporter.ReportError, "Model limit exceeded"):
                reporter.build_report_data(database)


if __name__ == "__main__":
    unittest.main()
