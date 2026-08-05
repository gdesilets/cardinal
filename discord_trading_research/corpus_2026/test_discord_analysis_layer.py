from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
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


builder = load_module("analysis_test_builder", HERE / "build_cardinal_database_v2.py")
analysis = load_module("analysis_test_target", HERE / "build_discord_analysis_layer.py")


def snowflake(timestamp: str, sequence: int) -> str:
    from datetime import datetime, timezone

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
    author_id: str | None = None,
) -> dict:
    message_id = snowflake(timestamp, sequence)
    row = {
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
        "inferred_permalink": f"https://discord.com/channels/1167376964680691732/{channel_id}/{message_id}",
        "attachments": [],
    }
    if author_id:
        row["author_id"] = author_id
    return row


def fixture() -> dict:
    question = message(
        "2026-01-02T14:00:00Z",
        1,
        "How do I identify a valid rejection block at 10am?",
        author="Student",
        channel_id="1273692573898113076",
        channel_name="questions",
    )
    answer = message(
        "2026-01-02T14:01:00Z",
        2,
        "Wait for the RB to close and add confluences.",
        author="Member",
        channel_id="1273692573898113076",
        channel_name="questions",
        reply_to=question["message_id"],
    )
    unresolved = message(
        "2026-01-02T15:00:00Z",
        3,
        "Does a mitigated RB remain valid on ES?",
        author="Student Two",
        channel_id="1273692573898113076",
        channel_name="questions",
    )
    win = message(
        "2026-01-03T15:05:00Z",
        4,
        "DAY 1 Trade 1: I entered NQ long using a 5m RB after a liquidity sweep at 10am. ES was SMT context. TP hit +2R win.",
        author="Trader One",
        author_id="111111111111111111",
        channel_id="1283941772577472643",
        channel_name="journal-one",
    )
    loss = message(
        "2026-01-04T15:10:00Z",
        5,
        "DAY 1 Trade 1: I entered ES short using a 5m rejection block and FVG at 10am. Stopped out -1R loss.",
        author="Trader Two",
        channel_id="1283941772577472643",
        channel_name="journal-two",
    )
    bare_ten = message(
        "2026-01-05T15:15:00Z",
        6,
        "That rejection block trade made 10 points.",
        author="Commenter",
        channel_id="1283941772577472643",
        channel_name="journal-two",
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
        "messages": [question, answer, unresolved, win, loss, bare_ten],
    }


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AnalysisLayerTests(unittest.TestCase):
    def build_fixture(self, folder: Path) -> tuple[Path, Path]:
        raw = folder / "fixture.json"
        base = folder / "base.sqlite"
        raw.write_text(json.dumps(fixture()), encoding="utf-8")
        builder.build_database(
            [raw],
            base,
            window_start="2026-01-01T06:00:00Z",
            window_end="2026-07-21T05:00:00Z",
        )
        return raw, base

    def test_end_to_end_discord_only_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            _raw, base = self.build_fixture(folder)
            output = folder / "analyzed.sqlite"
            before = sha(base)
            report = analysis.build_analysis(
                base,
                output,
                curated_path=ROOT / "curated_analysis_3month.json",
                model_analysis_path=ROOT / "model_analysis_3month.json",
                trade_script=ROOT / "build_trade_analysis_3month.py",
                rb_script=ROOT / "build_rb_analysis_3month.py",
                model_script=ROOT / "build_model_analysis_3month.py",
                replace=False,
                min_candidate_score=4,
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["source_scope"], "discord_only")
            self.assertEqual(report["outside_sources_used"], 0)
            self.assertEqual(sha(base), before, "raw Cardinal database must remain unchanged")
            self.assertEqual(report["coverage"]["analysis_completeness"], "partial")
            self.assertLessEqual(len(report["model_cards"]), 5)
            self.assertTrue(report["validation"]["checks"]["instrument_roles_not_collapsed"])
            self.assertTrue(report["validation"]["checks"]["strict_outcomes_only_win_loss"])
            self.assertGreaterEqual(report["validation"]["counts"]["strict_trade_episodes"], 2)
            self.assertGreaterEqual(report["rejection_block"]["rb_term_message_count"], 4)
            self.assertTrue(
                report["rejection_block"]["legacy_bare_10_timing_pattern_overridden"]
            )
            bare_ten_id = next(
                row["message_id"] for row in fixture()["messages"]
                if row["content_text"] == "That rejection block trade made 10 points."
            )
            timing_evidence = {
                message_id
                for row in report["rejection_block"]["whole_corpus_textual_components"]["timing"]
                for message_id in row["evidence_message_ids"]
            }
            self.assertNotIn(bare_ten_id, timing_evidence)
            overall_authors = report["trade_profiles"]["overall"]
            self.assertEqual(overall_authors["distinct_authors"], 2)
            self.assertEqual(overall_authors["distinct_exact_authors"], 1)
            self.assertEqual(overall_authors["distinct_surrogate_authors"], 1)
            self.assertEqual(overall_authors["episodes_with_exact_author_id"], 1)
            self.assertEqual(overall_authors["episodes_with_surrogate_author"], 1)

            with closing(sqlite3.connect(output)) as con:
                con.row_factory = sqlite3.Row
                self.assertEqual(con.execute("SELECT COUNT(*) FROM v_discord_only_audit").fetchone()[0], 0)
                partial = con.execute(
                    "SELECT COUNT(*) FROM questions WHERE resolution_status='partial'"
                ).fetchone()[0]
                unresolved = con.execute(
                    "SELECT COUNT(*) FROM questions WHERE resolution_status='unanswered'"
                ).fetchone()[0]
                self.assertGreaterEqual(partial, 1)
                self.assertGreaterEqual(unresolved, 1)
                linked_answer = con.execute(
                    """
                    SELECT a.resolution_status
                    FROM questions q
                    JOIN question_answer_links l USING(question_id)
                    JOIN answers a USING(answer_id)
                    WHERE q.normalized_question LIKE 'How do I identify%'
                    LIMIT 1
                    """
                ).fetchone()
                self.assertEqual(linked_answer[0], "community_only")
                eligible_messages = con.execute(
                    "SELECT COUNT(*) FROM v_analysis_eligible_messages"
                ).fetchone()[0]
                self.assertEqual(
                    con.execute("SELECT COUNT(*) FROM relevance_annotations").fetchone()[0],
                    eligible_messages,
                )
                self.assertGreater(con.execute("SELECT COUNT(*) FROM data_dictionary").fetchone()[0], 0)
                authority_row = con.execute(
                    """
                    SELECT authority_class
                    FROM v_authority_separated_qa
                    WHERE normalized_question LIKE 'How do I identify%'
                    LIMIT 1
                    """
                ).fetchone()
                self.assertIsNotNone(authority_row)
                self.assertIsNone(authority_row["authority_class"])
                rows = con.execute(
                    "SELECT canonical_symbol,role FROM setup_instruments si JOIN instruments i USING(instrument_id)"
                ).fetchall()
                roles = {(row["canonical_symbol"], row["role"]) for row in rows}
                self.assertIn(("NQ", "executed"), roles)
                self.assertIn(("ES", "executed"), roles)
                self.assertIn(("ES", "market_context"), roles)
                self.assertNotIn(("NQ", "market_context"), roles)
                exact_trader = con.execute(
                    """
                    SELECT a.discord_user_id,a.user_id_exact,m.author_display_name
                    FROM setup_instances si
                    JOIN messages m ON m.message_id=si.primary_message_id
                    JOIN authors a ON a.author_id=si.primary_author_id
                    WHERE m.author_display_name='Trader One'
                    """
                ).fetchone()
                self.assertEqual(tuple(exact_trader), ("111111111111111111", 1, "Trader One"))
                overall_rollup = con.execute(
                    """
                    SELECT distinct_authors,top_author_share
                    FROM setup_performance_rollups
                    WHERE model_id IS NULL
                    """
                ).fetchone()
                self.assertEqual(overall_rollup["distinct_authors"], 2)
                self.assertEqual(overall_rollup["top_author_share"], 0.5)

    def test_profile_language_and_role_separation(self) -> None:
        episodes = [
            {
                "outcome": "win",
                "author": "A",
                "confluences": ["rejection_block:5m:entry", "liquidity_sweep"],
                "instrument": ["NQ"],
                "market_context_instruments": ["ES"],
                "evidence": [{"message_id": "1"}],
            },
            {
                "outcome": "loss",
                "author": "B",
                "confluences": ["rejection_block:5m:entry"],
                "instrument": ["ES"],
                "market_context_instruments": ["NQ"],
                "evidence": [{"message_id": "2"}],
            },
        ]
        result = analysis.profile_rows(episodes)
        self.assertEqual(result["overall"]["eligible_count"], 2)
        self.assertEqual(result["overall"]["descriptive_selected_corpus_win_share"], 0.5)
        self.assertIn("descriptive", result["global_warning"].lower())
        executed = {row["instrument_family"]: row for row in result["executed_instrument_comparison"]}
        context = {row["instrument_family"]: row for row in result["market_context_instrument_mentions"]}
        self.assertEqual(executed["NQ"]["wins"], 1)
        self.assertEqual(executed["ES"]["losses"], 1)
        self.assertEqual(context["ES"]["wins"], 1)

    def test_profiles_are_episode_grain_and_include_explicit_strict_slices(self) -> None:
        episodes = [
            {
                "episode_id": "e1",
                "outcome": "win",
                "author": "A",
                "confluences": [
                    "rejection_block:1m:entry",
                    "rejection_block:5m:context",
                    "liquidity_sweep",
                ],
                "instrument": ["NQ", "MNQ"],
                "market_context_instruments": [],
                "direction": "long",
                "setup_times_mentioned": ["10am"],
                "field_evidence": {
                    "confluences": {
                        "rejection_block:1m:entry": [{"message_id": "m1"}],
                        "rejection_block:5m:context": [{"message_id": "m1"}],
                        "liquidity_sweep": [{"message_id": "m1"}],
                    },
                    "instrument": {
                        "NQ": [{"message_id": "m1"}],
                        "MNQ": [{"message_id": "m1"}],
                    },
                    "direction": {"long": [{"message_id": "m1"}]},
                    "setup_times": {"10am": [{"message_id": "m1"}]},
                    "sessions": {"New York AM": [{"message_id": "m1"}]},
                },
                "evidence": [{"message_id": "m1"}],
            },
            {
                "episode_id": "e2",
                "outcome": "loss",
                "author": "B",
                "confluences": ["rejection_block:5m:entry"],
                "instrument": ["NQ"],
                "market_context_instruments": [],
                "direction": "short",
                "field_evidence": {
                    "confluences": {
                        "rejection_block:5m:entry": [{"message_id": "m2"}],
                    },
                    "instrument": {"NQ": [{"message_id": "m2"}]},
                    "direction": {"short": [{"message_id": "m2"}]},
                },
                "evidence": [{"message_id": "m2"}],
            },
        ]
        model_cards = [
            {
                "model_id": "model:rb",
                "name": "RB fixture",
                "strict_legacy_episode_ids": ["e1", "e2"],
            }
        ]
        result = analysis.profile_rows(episodes, model_cards)
        confluence = {
            row["confluence"]: row for row in result["confluence_profiles"]
        }
        instruments = {
            row["instrument_family"]: row
            for row in result["executed_instrument_comparison"]
        }
        self.assertEqual(confluence["rejection_block"]["eligible_count"], 2)
        self.assertEqual(instruments["NQ"]["eligible_count"], 2)
        self.assertTrue(result["denominator_invariants"]["confluence_and_instrument_subsets_do_not_exceed_overall"])

        slices = result["strict_slice_profiles"]
        combinations = slices["canonical_confluence_combinations"]["rows"]
        self.assertEqual(sum(row["sample_count"] for row in combinations), 2)
        self.assertEqual(slices["executed_instrument"]["rows"][0]["sample_count"], 2)
        self.assertEqual(slices["explicit_session"]["rows"][0]["slice_key"], "New York AM")
        self.assertEqual(slices["explicit_setup_time"]["rows"][0]["slice_key"], "10am")
        self.assertEqual(slices["model"]["rows"][0]["sample_count"], 2)
        self.assertEqual(slices["model"]["rows"][0]["distinct_authors"], 2)
        self.assertIn("not confidence or probability", slices["global_warning"])

    def test_profiles_use_exact_author_ids_and_preserve_legacy_surrogates(self) -> None:
        episodes = [
            {
                "outcome": "win",
                "author": "Alpha",
                "author_display_name": "Alpha",
                "author_id": "111111111111111111",
                "author_id_exact": True,
                "confluences": ["rejection_block:5m:entry"],
                "instrument": ["NQ"],
                "market_context_instruments": [],
                "evidence": [{"message_id": "1"}],
            },
            {
                "outcome": "loss",
                "author": "Alpha Renamed",
                "author_display_name": "Alpha Renamed",
                "author_id": "discord-user:111111111111111111",
                "author_id_exact": 1,
                "confluences": ["rejection_block:1m:entry"],
                "instrument": ["MNQ"],
                "market_context_instruments": [],
                "evidence": [{"message_id": "2"}],
            },
            {
                "outcome": "win",
                "author": "Alpha",
                "author_display_name": "Alpha",
                "author_id": "222222222222222222",
                "author_id_exact": True,
                "confluences": ["rejection_block:5m:entry"],
                "instrument": ["NQ"],
                "market_context_instruments": [],
                "evidence": [{"message_id": "3"}],
            },
            {
                "outcome": "loss",
                "author": "Legacy Trader",
                "confluences": ["fair_value_gap"],
                "instrument": ["ES"],
                "market_context_instruments": [],
                "evidence": [{"message_id": "4"}],
            },
            {
                "outcome": "win",
                "author": "Legacy Trader",
                "confluences": ["fair_value_gap"],
                "instrument": ["MES"],
                "market_context_instruments": [],
                "evidence": [{"message_id": "5"}],
            },
        ]

        result = analysis.profile_rows(episodes)
        overall = result["overall"]
        self.assertEqual(overall["distinct_authors"], 3)
        self.assertEqual(overall["distinct_exact_authors"], 2)
        self.assertEqual(overall["distinct_surrogate_authors"], 1)
        self.assertEqual(overall["episodes_with_exact_author_id"], 3)
        self.assertEqual(overall["episodes_with_surrogate_author"], 2)
        self.assertEqual(overall["top_author_share"], 0.4)
        self.assertEqual(result["win_profile"]["distinct_authors"], 3)
        self.assertEqual(result["loss_profile"]["distinct_authors"], 2)

        exact_top = [row for row in overall["top_authors"] if row["author_id_exact"]]
        self.assertEqual(len(exact_top), 2)
        self.assertEqual(
            {row["discord_user_id"] for row in exact_top},
            {"111111111111111111", "222222222222222222"},
        )
        renamed = next(
            row for row in exact_top if row["discord_user_id"] == "111111111111111111"
        )
        self.assertEqual(renamed["display_name"], "Alpha")
        self.assertEqual(renamed["display_name_variants"], ["Alpha", "Alpha Renamed"])

        confluences = {row["confluence"]: row for row in result["confluence_profiles"]}
        self.assertEqual(confluences["rejection_block"]["distinct_authors"], 2)
        self.assertEqual(confluences["rejection_block"]["distinct_exact_authors"], 2)
        self.assertEqual(confluences["fair_value_gap"]["distinct_surrogate_authors"], 1)
        instruments = {
            row["instrument_family"]: row
            for row in result["executed_instrument_comparison"]
        }
        self.assertEqual(instruments["NQ"]["distinct_authors"], 2)
        self.assertEqual(instruments["ES"]["distinct_surrogate_authors"], 1)
        self.assertIn("exact Discord user ID", result["author_identity_policy"])

    def test_model_cards_expose_strict_author_concentration(self) -> None:
        class NoopConnection:
            def __init__(self):
                self.calls = []

            def execute(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return None

        class FakeWriter:
            def __init__(self):
                self.messages = {"m1": {"message_id": "m1"}}
                self.con = NoopConnection()

            def entity(self, *_args, **_kwargs):
                return None

            def claim(self, *args, **_kwargs):
                return analysis.stable_id("test-claim", *args[:2])

        spec = type(
            "Spec",
            (),
            {"model_id": "MODEL", "matcher": staticmethod(lambda _episode: True)},
        )()
        legacy_model = type(
            "LegacyModel",
            (),
            {"candidate_specs": staticmethod(lambda: (spec,))},
        )()
        episodes = [
            {
                "episode_id": "e1",
                "outcome": "win",
                "episode_kind": "executed_trade",
                "eligible_trade_instances_for_win_loss_confluence_comparison": 1,
                "author_id": "111111111111111111",
                "author_id_exact": True,
                "author_display_name": "Exact Trader",
                "evidence": [{"message_id": "m1"}],
            },
            {
                "episode_id": "e2",
                "outcome": "loss",
                "episode_kind": "executed_trade",
                "eligible_trade_instances_for_win_loss_confluence_comparison": 1,
                "author": "Legacy Trader",
                "evidence": [{"message_id": "m1"}],
            },
        ]
        source = {
            "models": [
                {
                    "model_id": "MODEL",
                    "name": "Test model",
                    "material_distinction": "Discord-derived fixture model",
                    "evidence": [{"message_id": "m1"}],
                    "exact_inclusion_rules": [
                        {
                            "rule": "Wait for the stored Discord trigger before entry.",
                            "required": True,
                            "evidence_message_ids": ["m1"],
                        }
                    ],
                    "exclusion_rules": [],
                }
            ]
        }
        writer = FakeWriter()
        _model_map, cards = analysis.import_models(
            writer, source, legacy_model, episodes, {"e1": "i1", "e2": "i2"}
        )
        self.assertEqual(len(cards), 1)
        strict = cards[0]["strict_selected_corpus"]
        self.assertEqual(strict["distinct_authors"], 2)
        self.assertEqual(strict["distinct_exact_authors"], 1)
        self.assertEqual(strict["distinct_surrogate_authors"], 1)
        self.assertEqual(strict["top_author_share"], 0.5)
        self.assertEqual(cards[0]["matched_author_concentration"]["distinct_authors"], 2)
        self.assertIn("not expectancy or probability", cards[0]["warning"])
        self.assertIn("unknown", cards[0]["rule_state_policy"])
        state_calls = [
            call for call in writer.con.calls
            if call[0] and "INSERT INTO setup_rule_states" in call[0][0]
        ]
        self.assertEqual(len(state_calls), 2)
        match_calls = [
            call for call in writer.con.calls
            if call[0] and "INSERT INTO setup_model_matches" in call[0][0]
        ]
        self.assertEqual(len(match_calls), 2)
        self.assertTrue(
            all("signature_only_rules_not_evaluated" in call[0][1][2] for call in match_calls)
        )
        self.assertTrue(all(call[0][1][3] == 1 for call in match_calls))

    def test_full_window_discovers_novel_family_and_rejects_weak_candidates(self) -> None:
        class NoopConnection:
            def execute(self, *_args, **_kwargs):
                return None

        class FakeWriter:
            def __init__(self, messages):
                self.messages = messages
                self.con = NoopConnection()

            def entity(self, *_args, **_kwargs):
                return None

            def claim(self, *args, **_kwargs):
                return analysis.stable_id("test-claim", *args[:2])

        def evidence_message(index: int, author_no: int, label: str) -> dict:
            message_id = f"m{index}"
            return {
                "message_id": message_id,
                "author": f"Trader {author_no}",
                "author_id": str(111111111111111110 + author_no),
                "author_id_exact": True,
                "timestamp_utc": f"2026-01-{index:02d}T15:00:00Z",
                "inferred_permalink": f"https://discord.com/channels/g/c/{message_id}",
                "content_text": (
                    f"{label} setup rules.\n"
                    "Wait for confirmation before entry.\n"
                    "Entry at the stored trigger.\n"
                    "Target is the stated level.\n"
                    "Invalid if the stated structure fails."
                ),
            }

        def episode(
            episode_id: str,
            message_id: str,
            author_no: int,
            date_no: int,
            features: list[str],
            outcome: str,
        ) -> dict:
            return {
                "episode_id": episode_id,
                "outcome": outcome,
                "episode_kind": "executed_trade",
                "eligible_trade_instances_for_win_loss_confluence_comparison": 1,
                "shared_confluence_attribution_across_instances": False,
                "author": f"Trader {author_no}",
                "author_id": str(111111111111111110 + author_no),
                "author_id_exact": True,
                "trade_date_local": f"2026-01-{date_no:02d}",
                "confluences": features,
                "evidence": [{"message_id": message_id}],
            }

        messages = {
            f"m{index}": evidence_message(index, ((index - 1) % 3) + 1, "Atlas")
            for index in range(1, 7)
        }
        messages["m6"]["content_text"] += "\nSkip when the pattern is invalid."
        episodes = [
            episode(
                f"novel-{index}",
                f"m{index}",
                ((index - 1) % 3) + 1,
                index,
                ["new_window_feature_a", "new_window_feature_b", "new_window_feature_c"],
                "win" if index % 2 else "loss",
            )
            for index in range(1, 7)
        ]

        # A one-off signature is enumerated but cannot clear recurrence.
        messages["one"] = evidence_message(7, 1, "Oneoff")
        episodes.append(
            episode("one-off", "one", 1, 7, ["one_off_a", "one_off_b"], "win")
        )

        # Six posts by one author remain author-dominated even with enough dates.
        for index in range(8, 14):
            message_id = f"solo-{index}"
            messages[message_id] = evidence_message(index, 1, "Solo")
            episodes.append(
                episode(
                    f"solo-{index}", message_id, 1, index,
                    ["dominated_a", "dominated_b"], "loss" if index % 2 else "win"
                )
            )

        # This otherwise threshold-sized family has no messages in the trusted lookup.
        for index in range(14, 20):
            episodes.append(
                episode(
                    f"untrusted-{index}", f"quarantined-only-{index}",
                    ((index - 14) % 3) + 1, index,
                    ["hidden_a", "hidden_b"], "win" if index % 2 else "loss"
                )
            )
        instance_map = {
            str(value["episode_id"]): f"instance:{value['episode_id']}" for value in episodes
        }
        legacy_model = type(
            "NoLegacyModels",
            (),
            {"candidate_specs": staticmethod(lambda: ())},
        )()
        audit: dict = {}
        _model_map, cards = analysis.import_models(
            FakeWriter(messages),
            {"models": []},
            legacy_model,
            episodes,
            instance_map,
            discovery_audit=audit,
        )

        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["candidate_origin"], "full_window_recurrent_signature_discovery")
        self.assertTrue(any("new_window_feature" in value for value in card["candidate_signature"]))
        self.assertGreaterEqual(card["strict_selected_corpus"]["distinct_authors"], 3)
        self.assertLessEqual(card["strict_selected_corpus"]["top_author_share"], 0.60)
        self.assertTrue(card["rules"])
        self.assertTrue(card["evidence"][0]["permalink"])
        self.assertTrue(card["contradictions_or_counterevidence"])
        self.assertIn("non-causal", card["warning"])

        discovery = audit["novel_candidate_discovery"]
        self.assertGreater(
            discovery["rejection_reason_counts"].get("below_minimum_strict_episodes", 0), 0
        )
        self.assertGreater(discovery["rejection_reason_counts"].get("author_dominated", 0), 0)
        self.assertEqual(discovery["strict_episodes_without_trusted_message_evidence"], 6)
        self.assertFalse(audit["fifth_model_forced"])

    def test_five_supported_legacy_models_leave_novel_candidate_audited_not_emitted(self) -> None:
        class NoopConnection:
            def execute(self, *_args, **_kwargs):
                return None

        class FakeWriter:
            def __init__(self, messages):
                self.messages = messages
                self.con = NoopConnection()

            def entity(self, *_args, **_kwargs):
                return None

            def claim(self, *args, **_kwargs):
                return analysis.stable_id("test-claim", *args[:2])

        messages: dict[str, dict] = {}
        episodes: list[dict] = []
        specs = []
        sources = []
        for model_no in range(5):
            source_id = f"LEGACY_{model_no}"
            for author_no in (1, 2):
                message_id = f"legacy-{model_no}-{author_no}"
                messages[message_id] = {
                    "message_id": message_id,
                    "author": f"Legacy Trader {author_no}",
                    "author_id": str(200000000000000000 + author_no),
                    "author_id_exact": True,
                    "timestamp_utc": f"2026-01-{model_no + 1:02d}T14:00:00Z",
                    "inferred_permalink": f"https://discord.com/channels/g/c/{message_id}",
                    "content_text": f"Legacy {model_no} setup evidence.",
                }
                episodes.append(
                    {
                        "episode_id": f"legacy-episode-{model_no}-{author_no}",
                        "legacy_group": model_no,
                        "outcome": "unknown",
                        "episode_kind": "paper_trade",
                        "eligible_trade_instances_for_win_loss_confluence_comparison": 0,
                        "author": f"Legacy Trader {author_no}",
                        "author_id": str(200000000000000000 + author_no),
                        "author_id_exact": True,
                        "confluences": [f"legacy_feature_{model_no}"],
                        "evidence": [{"message_id": message_id}],
                    }
                )
            specs.append(
                type(
                    f"LegacySpec{model_no}",
                    (),
                    {
                        "model_id": source_id,
                        "matcher": staticmethod(
                            lambda episode, expected=model_no: episode.get("legacy_group") == expected
                        ),
                    },
                )()
            )
            sources.append(
                {
                    "model_id": source_id,
                    "name": f"Legacy model {model_no}",
                    "material_distinction": f"Preserved model {model_no}.",
                    "evidence": [{"message_id": f"legacy-{model_no}-1"}],
                    "exact_inclusion_rules": [],
                    "exact_exclusion_rules": [],
                }
            )

        # This separate full-window family clears every novel-candidate threshold.
        for index in range(6):
            message_id = f"novel-slot-{index}"
            author_no = (index % 3) + 1
            messages[message_id] = {
                "message_id": message_id,
                "author": f"Novel Trader {author_no}",
                "author_id": str(300000000000000000 + author_no),
                "author_id_exact": True,
                "timestamp_utc": f"2026-02-{index + 1:02d}T15:00:00Z",
                "inferred_permalink": f"https://discord.com/channels/g/c/{message_id}",
                "content_text": (
                    "Fresh setup rules.\nWait for confirmation before entry.\n"
                    "Target is the stated level."
                ),
            }
            episodes.append(
                {
                    "episode_id": f"novel-slot-episode-{index}",
                    "outcome": "win" if index % 2 else "loss",
                    "episode_kind": "executed_trade",
                    "eligible_trade_instances_for_win_loss_confluence_comparison": 1,
                    "shared_confluence_attribution_across_instances": False,
                    "author": f"Novel Trader {author_no}",
                    "author_id": str(300000000000000000 + author_no),
                    "author_id_exact": True,
                    "trade_date_local": f"2026-02-{index + 1:02d}",
                    "confluences": ["fresh_feature_a", "fresh_feature_b", "fresh_feature_c"],
                    "evidence": [{"message_id": message_id}],
                }
            )

        legacy_model = type(
            "FiveLegacyModels",
            (),
            {"candidate_specs": staticmethod(lambda: tuple(specs))},
        )()
        instance_map = {
            str(value["episode_id"]): f"instance:{value['episode_id']}" for value in episodes
        }
        audit: dict = {}
        _model_map, cards = analysis.import_models(
            FakeWriter(messages),
            {"models": sources},
            legacy_model,
            episodes,
            instance_map,
            discovery_audit=audit,
        )

        self.assertEqual(len(cards), 5)
        self.assertTrue(
            all(
                card["candidate_origin"]
                == "preserved_three_month_discord_template_full_window_rematch"
                for card in cards
            )
        )
        self.assertEqual(audit["retained_legacy_models"], 5)
        self.assertEqual(audit["promoted_novel_models"], 0)
        self.assertGreater(
            audit["novel_candidate_discovery"]["rejection_reason_counts"].get(
                "model_limit_slot_not_available", 0
            ),
            0,
        )
        self.assertFalse(audit["fifth_model_forced"])

    def test_quarantined_only_messages_remain_searchable_but_are_not_analyzed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            payload = fixture()
            quarantined = message(
                "2026-01-05T15:05:00Z",
                6,
                (
                    "quarantineanalysistoken DAY 1 Trade 1: NQ rejection block "
                    "liquidity sweep at 10am, TP hit +4R win."
                ),
                author="Legacy Trader",
                channel_id="1283941772577472643",
                channel_name="legacy-journal",
            )
            quarantined.update(
                {
                    "migration_quarantined": True,
                    "migration_quarantine_reasons": [
                        "reply_preview_content_contamination_suspected"
                    ],
                    "_migration_occurrence": {
                        "occurrence_id": "legacy_occ:analysis-filter"
                    },
                }
            )
            payload["messages"].append(quarantined)
            raw = folder / "trust_fixture.json"
            base = folder / "trust_base.sqlite"
            raw.write_text(json.dumps(payload), encoding="utf-8")
            builder.build_database(
                [raw],
                base,
                window_start="2026-01-01T06:00:00Z",
                window_end="2026-07-21T05:00:00Z",
            )

            with closing(sqlite3.connect(base)) as con:
                rows, lookup = analysis.message_rows(con)
                self.assertNotIn(quarantined["message_id"], lookup)
                self.assertEqual(len(rows), len(payload["messages"]) - 1)
                self.assertEqual(
                    con.execute(
                        "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'quarantineanalysistoken'"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    con.execute(
                        "SELECT evidence_trust_state,eligible_for_accepted_evidence FROM messages WHERE message_id=?",
                        (quarantined["message_id"],),
                    ).fetchone(),
                    ("quarantined_only", 0),
                )

            output = folder / "trust_analyzed.sqlite"
            report = analysis.build_analysis(
                base,
                output,
                curated_path=ROOT / "curated_analysis_3month.json",
                model_analysis_path=ROOT / "model_analysis_3month.json",
                trade_script=ROOT / "build_trade_analysis_3month.py",
                rb_script=ROOT / "build_rb_analysis_3month.py",
                model_script=ROOT / "build_model_analysis_3month.py",
                replace=False,
                min_candidate_score=4,
            )
            self.assertEqual(report["status"], "passed")
            with closing(sqlite3.connect(output)) as con:
                self.assertEqual(
                    con.execute(
                        "SELECT COUNT(*) FROM evidence_items WHERE message_id=?",
                        (quarantined["message_id"],),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    con.execute(
                        "SELECT COUNT(*) FROM v_discord_only_audit"
                    ).fetchone()[0],
                    0,
                )

    def test_higher_lower_catalog_is_descriptive_and_sample_gated(self) -> None:
        episodes = []
        for index in range(5):
            episodes.append(
                {
                    "outcome": "win",
                    "author": f"W{index}",
                    "confluences": ["higher_component"],
                    "instrument": ["NQ"],
                    "market_context_instruments": [],
                    "evidence": [{"message_id": f"w{index}"}],
                }
            )
            episodes.append(
                {
                    "outcome": "loss",
                    "author": f"L{index}",
                    "confluences": ["lower_component"],
                    "instrument": ["ES"],
                    "market_context_instruments": [],
                    "evidence": [{"message_id": f"l{index}"}],
                }
            )
        result = analysis.profile_rows(episodes)
        self.assertEqual(result["observed_higher_share_associations"][0]["confluence"], "higher_component")
        self.assertEqual(result["observed_lower_share_associations"][0]["confluence"], "lower_component")
        self.assertIn("descriptive", result["association_catalog_policy"].lower())
        self.assertIn("not a calibrated probability", result["association_catalog_policy"].lower())

    def test_refuses_to_reanalyze_populated_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            _raw, base = self.build_fixture(folder)
            output = folder / "analyzed.sqlite"
            analysis.build_analysis(
                base,
                output,
                curated_path=ROOT / "curated_analysis_3month.json",
                model_analysis_path=ROOT / "model_analysis_3month.json",
                trade_script=ROOT / "build_trade_analysis_3month.py",
                rb_script=ROOT / "build_rb_analysis_3month.py",
                model_script=ROOT / "build_model_analysis_3month.py",
                replace=False,
                min_candidate_score=4,
            )
            with self.assertRaises(analysis.AnalysisError):
                analysis.build_analysis(
                    output,
                    folder / "second.sqlite",
                    curated_path=ROOT / "curated_analysis_3month.json",
                    model_analysis_path=ROOT / "model_analysis_3month.json",
                    trade_script=ROOT / "build_trade_analysis_3month.py",
                    rb_script=ROOT / "build_rb_analysis_3month.py",
                    model_script=ROOT / "build_model_analysis_3month.py",
                    replace=False,
                    min_candidate_score=4,
                )


if __name__ == "__main__":
    unittest.main()
