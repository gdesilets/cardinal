from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import authorized_collection_scope as scoped
import build_corpus
import build_cardinal_database_v2 as cardinal_db
import discord_attachment_archiver as attachment_archiver


GUILD_ID = "1167376964680691732"
STUDENT_ID = "1370578463223975986"
PREMIUM_ID = "1283941772577472643"
QUESTIONS_ID = "1273692573898113076"
QUESTIONS_NAME = "\u2753\u2502questions"
THREAD_ID = "1456316273788063925"
NEW_THREAD_ONE = "1448404594731516058"
NEW_THREAD_TWO = "1457000000000000001"
OUTSIDE_ID = "1329615478716502097"


class AuthorizedCollectionScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.segments = self.root / "raw" / "channel_segments"
        self.premium_segments = self.root / "raw" / "channel_segments_v2_5"
        self.segments.mkdir(parents=True)
        self.premium_segments.mkdir(parents=True)
        self.scope_path = self.write_json(
            self.root / "authorized_collection_scope.json",
            self.scope_payload(),
        )
        self.inventory_path = self.write_json(
            self.root / "inventory.json",
            self.inventory_payload(),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_json(self, path: Path, payload: object) -> Path:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def scope_payload(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "scope_status": "user_narrowed",
            "scope_effective_date": "2026-07-21",
            "guild_id": GUILD_ID,
            "source_scope": "discord_only",
            "outside_sources_used": False,
            "window": {
                "timezone": "America/Chicago",
                "start_date_inclusive": "2026-01-01",
                "end_date_inclusive": "2026-07-20",
            },
            "allowed_top_level_containers": [
                {
                    "channel_id": STUDENT_ID,
                    "name": "student-breakdowns",
                    "kind": "text channel",
                    "include_exact_child_threads": True,
                },
                {
                    "channel_id": PREMIUM_ID,
                    "name": "premium-journals",
                    "kind": "forum channel",
                    "include_exact_child_threads": True,
                },
                {
                    "channel_id": QUESTIONS_ID,
                    "name": QUESTIONS_NAME,
                    "logical_name": "questions",
                    "kind": "text channel",
                    "include_exact_child_threads": True,
                },
            ],
            "canonical_path_policy": copy.deepcopy(scoped.CANONICAL_PATH_POLICY),
            "collection_rule": "Only exact authenticated requested containers.",
            "release_rule": "Exclude every prior out-of-scope capture.",
            "deletion_rule": "Do not mutate prior raw artifacts.",
        }

    def inventory_payload(self) -> dict[str, object]:
        parent_rows = [
            {
                "container_id": QUESTIONS_ID,
                "name": QUESTIONS_NAME,
                "kind": "text channel",
                "inventory_layer": "top_level_container",
                "message_bearing": True,
                "accessible": True,
                "searchable": True,
                "coverage_container_id": QUESTIONS_ID,
            },
            {
                "container_id": STUDENT_ID,
                "name": "student-breakdowns",
                "kind": "text channel",
                "inventory_layer": "top_level_container",
                "message_bearing": True,
                "accessible": True,
                "searchable": True,
                "coverage_container_id": STUDENT_ID,
            },
            {
                "container_id": PREMIUM_ID,
                "name": "premium-journals",
                "kind": "forum channel",
                "inventory_layer": "top_level_container",
                "message_bearing": True,
                "accessible": True,
                "searchable": True,
                "coverage_container_id": PREMIUM_ID,
            },
        ]
        child = {
            "container_id": THREAD_ID,
            "thread_id": THREAD_ID,
            "name": "same title is not identity",
            "kind": "forum thread",
            "inventory_layer": "observed_forum_thread",
            "parent_container_id": PREMIUM_ID,
            "message_bearing": True,
            "accessible": True,
            "searchable": True,
            "coverage_container_id": PREMIUM_ID,
            "identity_provenance": {
                "exact_row_owned_evidence": True,
                "method": "authenticated_discord_thread_url+forum_card_data_list_item_id",
                "evidence": [
                    {
                        "method": "forum_card_data_list_item_id",
                        "forum_card_data_list_item_id": (
                            f"forum-channel-list-{PREMIUM_ID}___{THREAD_ID}"
                        ),
                        "authenticated": True,
                        "source_scope": "discord_only",
                        "outside_sources_used": False,
                    },
                    {
                        "method": "authenticated_discord_thread_url",
                        "thread_url": f"https://discord.com/channels/{GUILD_ID}/{THREAD_ID}",
                        "authenticated": True,
                        "source_scope": "discord_only",
                        "outside_sources_used": False,
                    },
                ],
            },
        }
        outside = {
            "container_id": OUTSIDE_ID,
            "name": "live",
            "kind": "stage channel",
            "inventory_layer": "top_level_container",
            "message_bearing": True,
            "accessible": True,
            "searchable": True,
            "coverage_container_id": OUTSIDE_ID,
        }
        return {
            "schema_version": "2.0.0",
            "guild_id": GUILD_ID,
            "inventory_complete": True,
            "status": "complete",
            "captured_at_utc": "2026-07-21T06:00:00Z",
            "accessible_scope": {
                "top_level_containers": {
                    "declared_complete": True,
                    "expected_count": 4,
                },
                "forum_threads": {
                    "declared_complete": True,
                    "completion_evidence": {"method": "authenticated enumeration"},
                },
                "ordinary_threads": {
                    "declared_complete": True,
                    "completion_evidence": {"method": "authenticated parent audit"},
                },
                "post_cutoff_navigation_resnapshot": {
                    "declared_complete": True,
                    "completion_evidence": {"method": "authenticated resnapshot"},
                },
            },
            "containers": [*parent_rows, child, outside],
        }

    def policy(self) -> scoped.AuthorizedScope:
        return scoped.load_validated_scope(
            self.scope_path,
            expected_guild_id=GUILD_ID,
            expected_timezone="America/Chicago",
            expected_start_date="2026-01-01",
            expected_end_date="2026-07-20",
        )

    def message(self, day: int, increment: int, content: str) -> dict[str, object]:
        zone = build_corpus.resolve_timezone("America/Chicago")
        local = dt.datetime(2026, 1, day, 10, tzinfo=zone)
        utc = local.astimezone(dt.timezone.utc)
        return {
            "message_id": build_corpus.snowflake_id_for_datetime(utc, increment),
            "timestamp_utc": build_corpus.iso_z(utc),
            "result_index": 1,
            "page_number": 1,
            "content": content,
        }

    def write_segment(
        self,
        filename: str,
        *,
        container_id: str,
        channel_name: str,
        message: dict[str, object],
        id_source: str = "navigation_inventory",
    ) -> Path:
        query = f"in:{channel_name} after:2025-12-31 before:2026-01-02"
        channel_kind = "forum channel" if container_id == PREMIUM_ID else "text channel"
        message = copy.deepcopy(message)
        message.update(
            {
                "search_query": query,
                "collection_channel_id": container_id,
                "collection_channel_name": channel_name,
                "collection_channel_kind": channel_kind,
                "collection_channel_id_source": id_source,
            }
        )
        evidence = {
            "schema_version": "1.0.0",
            "query": query,
            "reported_total": 1,
            "reported_pages": 1,
            "terminal_state": "stable_bottom",
            "search_submission": {
                "mode": "fresh",
                "query": query,
                "submission_count": 1,
                "submitted_at_utc": "2026-07-21T06:00:00Z",
            },
            "stable_bottom": {
                "required_observations": 2,
                "observations": [
                    {
                        "sequence": sequence,
                        "observed_at_utc": f"2026-07-21T06:00:0{sequence}Z",
                        "query": query,
                        "visible_result_count": 1,
                        "first_result_index": 1,
                        "last_result_index": 1,
                        "current_page": 1,
                        "result_set_size": 1,
                        "has_enabled_next": False,
                    }
                    for sequence in (1, 2)
                ],
            },
        }
        destination = (
            self.premium_segments
            if container_id in {PREMIUM_ID, THREAD_ID, NEW_THREAD_ONE, NEW_THREAD_TWO}
            else self.segments
        )
        return self.write_json(
            destination / filename,
            {
                "collector_version": "2.5",
                "guild_id": GUILD_ID,
                "requested_container": {
                    "channel_id": container_id,
                    "channel_name": channel_name,
                    "channel_kind": channel_kind,
                    "channel_id_source": id_source,
                },
                "segment": {
                    "start": "2026-01-01",
                    "end": "2026-01-01",
                    "query": query,
                    "timezone": "America/Chicago",
                },
                "reported_total": 1,
                "reported_pages": 1,
                "pages_captured": 1,
                "captured_rows": 1,
                "unique_message_ids": 1,
                "gap_indices": [],
                "completion_evidence": evidence,
                "complete": True,
                "messages": [message],
            },
        )

    def build(self) -> tuple[dict[str, object], dict[str, object]]:
        return build_corpus.build_corpus(
            segment_dirs=[self.segments, self.premium_segments],
            inventory_path=self.inventory_path,
            authorized_scope_path=self.scope_path,
            scoped_child_inventory_reconciliation_path=(
                self.write_child_reconciliation()
            ),
            provenance_root=self.root,
            data_cutoff_utc=dt.datetime(2026, 7, 21, 6, tzinfo=dt.timezone.utc),
        )

    def mark_premium_message_scope_closed(
        self, corpus: dict[str, object]
    ) -> None:
        authorized = corpus["authorized_collection_scope"]  # type: ignore[index]
        reconciliation = authorized["child_inventory_reconciliation"]  # type: ignore[index]
        closure = reconciliation["message_scope_closure"]  # type: ignore[index]
        closure.update(  # type: ignore[union-attr]
            {
                "gate": "premium_journals_message_data_scope_closure",
                "passed": True,
                "closure_proven": True,
                "status": "complete",
                "required_parent_container_id": PREMIUM_ID,
                "required_calendar_day_count": 201,
                "required_exact_daily_parent_segment_count": 201,
                "parent_segment_count": 201,
                "complete_calendar_day_count": 201,
                "invalid_daily_partition_segment_count": 0,
                "duplicate_daily_date_count": 0,
                "incomplete_segment_count": 0,
                "terminal_evidence_invalid_segment_count": 0,
                "unresolved_row_binding_count": 0,
                "row_binding_conflict_count": 0,
                "observed_child_outside_derived_union_count": 0,
            }
        )
        corpus["inventory"]["scope_derivation"][  # type: ignore[index]
            "child_inventory_reconciliation"
        ] = copy.deepcopy(reconciliation)

    def write_child_reconciliation(self) -> Path:
        (self.root / "raw").mkdir(exist_ok=True)
        baseline_source = self.write_json(
            self.root / "raw" / "forum_inventory_fixture.json",
            {"thread_ids": [THREAD_ID]},
        )
        query = "in:premium-journals after:2026-01-01 before:2026-01-03"
        partial_rows = []
        source_groups = []
        observations = []
        for ordinal, thread_id in enumerate((NEW_THREAD_ONE, NEW_THREAD_TWO), start=1):
            result_index = ordinal + 25
            message_id = str(self.message(1, ordinal + 20, "evidence")["message_id"])
            partial_rows.append(
                {
                    **self.message(1, ordinal + 20, "evidence"),
                    "result_index": result_index,
                    "page_number": 2,
                    "search_query": query,
                    "collection_channel_id": PREMIUM_ID,
                    "collection_channel_name": "premium-journals",
                    "collection_channel_kind": "forum channel",
                    "collection_channel_id_source": "navigation_inventory",
                }
            )
            source_groups.append(
                {
                    "group_ordinal": ordinal,
                    "group_label": f"fixture {ordinal}, premium-journals",
                    "group_header_text": f"fixture {ordinal}\npremium-journals",
                    "result_indices": [result_index],
                    "message_ids": [message_id],
                    "unique_direct_child_header_within_group": True,
                    "click_destination_url": (
                        f"https://discord.com/channels/{GUILD_ID}/{thread_id}"
                    ),
                    "observed_guild_id": GUILD_ID,
                    "observed_thread_id": thread_id,
                    "back_return_succeeded": True,
                    "back_return_attempts": 1,
                    "thread_identity_exact": True,
                }
            )
            observations.append(
                {
                    "thread_id": thread_id,
                    "classification": "exact_addition",
                    "observed_display_titles": ["duplicate title is display only"],
                    "title_identity_role": "display_only_not_used_for_identity",
                    "exact_group_evidence": [
                        {
                            "group_ordinal": ordinal,
                            "thread_id": thread_id,
                            "destination_url": (
                                f"https://discord.com/channels/{GUILD_ID}/{thread_id}"
                            ),
                            "identity_method": "forum_group_header_navigation_exact",
                            "exact_message_membership": [
                                {
                                    "result_index": result_index,
                                    "message_id": message_id,
                                }
                            ],
                        }
                    ],
                }
            )
        partial_source = self.write_json(
            self.root / "raw" / "authenticated_group_navigation_fixture.partial.json",
            {
                "collector_version": "2.5",
                "guild_id": GUILD_ID,
                "requested_container": {
                    "channel_id": PREMIUM_ID,
                    "channel_name": "premium-journals",
                    "channel_kind": "forum channel",
                    "channel_id_source": "navigation_inventory",
                },
                "segment": {
                    "start": "2026-01-01",
                    "end": "2026-01-01",
                    "query": query,
                    "timezone": "America/Chicago",
                },
                "complete": False,
                "messages": partial_rows,
            },
        )
        partial_sha = hashlib.sha256(partial_source.read_bytes()).hexdigest()
        evidence_source = self.write_json(
            self.root / "raw" / "authenticated_group_navigation_fixture.json",
            {
                "schema_version": "1.0.0",
                "evidence_type": "authenticated_discord_search_group_header_navigation",
                "guild_id": GUILD_ID,
                "parent_forum_channel_id": PREMIUM_ID,
                "parent_forum_channel_name": "premium-journals",
                "query": query,
                "page_number": 2,
                "source_partial_sha256": partial_sha,
                "source_partial_path": "raw/authenticated_group_navigation_fixture.partial.json",
                "page_validation": {
                    "all_result_indices_contiguous": True,
                    "all_result_indices_unique": True,
                    "all_message_ids_unique": True,
                    "same_title_groups_kept_separate": True,
                    "direct_child_header_count_equaled_group_count": True,
                    "back_return_parent_url": (
                        f"https://discord.com/channels/{GUILD_ID}/{PREMIUM_ID}"
                    ),
                    "back_return_same_query_page_verified": True,
                },
                "groups": source_groups,
            },
        )
        payload = {
            "schema_version": "1.0.0",
            "artifact_type": "scoped_forum_thread_inventory_reconciliation",
            "guild_id": GUILD_ID,
            "parent_forum_channel_id": PREMIUM_ID,
            "source_scope": "authenticated_discord_only",
            "outside_sources_used": False,
            "status": "unresolved_census",
            "inventory_complete": False,
            "enumeration_complete": False,
            "closure_proven": False,
            "baseline": {
                "path": "raw/forum_inventory_fixture.json",
                "sha256": hashlib.sha256(baseline_source.read_bytes()).hexdigest(),
            },
            "additive_evidence_source": {
                "path": "raw/authenticated_group_navigation_fixture.json",
                "sha256": hashlib.sha256(evidence_source.read_bytes()).hexdigest(),
                "query": query,
                "page_number": 2,
                "bound_partial_path": "raw/authenticated_group_navigation_fixture.partial.json",
                "bound_partial_sha256": partial_sha,
            },
            "counts": {
                "baseline_exact_thread_ids": 1,
                "exact_additional_thread_ids": 2,
                "exact_known_union_thread_ids": 3,
            },
            "baseline_thread_ids": [THREAD_ID],
            "added_thread_ids": [NEW_THREAD_ONE, NEW_THREAD_TWO],
            "exact_known_union_thread_ids": [
                THREAD_ID,
                NEW_THREAD_ONE,
                NEW_THREAD_TWO,
            ],
            "navigation_observations": observations,
        }
        path = self.root / "working" / "child_reconciliation.json"
        path.parent.mkdir(exist_ok=True)
        return self.write_json(path, payload)

    def test_scope_file_exactly_allows_three_parents(self) -> None:
        policy = self.policy()
        self.assertEqual(policy.parent_ids, frozenset({STUDENT_ID, PREMIUM_ID, QUESTIONS_ID}))
        self.assertRegex(policy.source_sha256, r"^[0-9a-f]{64}$")

    def test_scope_file_rejects_silent_fourth_channel(self) -> None:
        payload = self.scope_payload()
        payload["allowed_top_level_containers"].append(  # type: ignore[union-attr]
            {
                "channel_id": OUTSIDE_ID,
                "name": "live",
                "kind": "stage channel",
                "include_exact_child_threads": True,
            }
        )
        path = self.write_json(self.root / "tampered_scope.json", payload)
        with self.assertRaises(scoped.AuthorizedScopeError):
            scoped.load_validated_scope(
                path,
                expected_guild_id=GUILD_ID,
                expected_timezone="America/Chicago",
                expected_start_date="2026-01-01",
                expected_end_date="2026-07-20",
            )

    def test_only_authenticated_inventory_evidence_proves_child_parentage(self) -> None:
        policy = self.policy()
        inventory = self.inventory_payload()
        relationships = scoped.proven_child_relationships(inventory, policy)
        self.assertEqual(relationships[THREAD_ID]["parent_container_id"], PREMIUM_ID)
        tampered = copy.deepcopy(inventory)
        child = next(row for row in tampered["containers"] if row.get("container_id") == THREAD_ID)  # type: ignore[index,union-attr]
        child["identity_provenance"]["evidence"][0]["authenticated"] = False
        child["identity_provenance"]["evidence"][1]["authenticated"] = False
        self.assertNotIn(THREAD_ID, scoped.proven_child_relationships(tampered, policy))

    def test_url_only_child_evidence_never_proves_parentage(self) -> None:
        policy = self.policy()
        inventory = self.inventory_payload()
        child = next(
            row
            for row in inventory["containers"]  # type: ignore[index]
            if row.get("container_id") == THREAD_ID
        )
        child["identity_provenance"]["evidence"] = [  # type: ignore[index]
            {
                "method": "authenticated_discord_thread_url",
                "thread_url": f"https://discord.com/channels/{GUILD_ID}/{THREAD_ID}",
                "authenticated": True,
                "source_scope": "discord_only",
                "outside_sources_used": False,
            }
        ]
        self.assertNotIn(
            THREAD_ID, scoped.proven_child_relationships(inventory, policy)
        )

    def test_reconciliation_requires_bound_partial_and_semantic_source_match(self) -> None:
        reconciliation = self.write_child_reconciliation()
        payload = json.loads(reconciliation.read_text(encoding="utf-8"))
        del payload["additive_evidence_source"]["bound_partial_sha256"]
        malformed = self.write_json(
            self.root / "working" / "missing_partial_binding.json", payload
        )
        relationships = scoped.proven_child_relationships(
            self.inventory_payload(), self.policy()
        )
        with self.assertRaisesRegex(
            scoped.AuthorizedScopeError, "bound_partial|partial_sha"
        ):
            scoped.load_scoped_child_inventory_reconciliation(
                malformed, self.policy(), relationships
            )

        payload = json.loads(reconciliation.read_text(encoding="utf-8"))
        payload["added_thread_ids"] = [OUTSIDE_ID, NEW_THREAD_TWO]
        payload["exact_known_union_thread_ids"] = [
            THREAD_ID,
            OUTSIDE_ID,
            NEW_THREAD_TWO,
        ]
        detached = self.write_json(
            self.root / "working" / "detached_outside_child.json", payload
        )
        with self.assertRaisesRegex(
            scoped.AuthorizedScopeError, "exact_addition_lacks_bound_navigation_evidence"
        ):
            scoped.load_scoped_child_inventory_reconciliation(
                detached, self.policy(), relationships
            )

    def test_query_request_mismatch_canary_is_excluded_before_sqlite_fts(self) -> None:
        valid_path = self.write_segment(
            "questions_valid.json",
            container_id=QUESTIONS_ID,
            channel_name=QUESTIONS_NAME,
            message=self.message(1, 71, "VALID_SCOPE_ROW"),
        )
        valid_corpus, _valid_manifest = self.build()

        canary_path = self.write_segment(
            "questions_requested_but_live_query.json",
            container_id=QUESTIONS_ID,
            channel_name=QUESTIONS_NAME,
            message=self.message(1, 72, "OUTSIDE_LIVE_CANARY"),
        )
        canary_payload = json.loads(canary_path.read_text(encoding="utf-8"))
        live_query = "in:live after:2025-12-31 before:2026-01-02"
        canary_payload["segment"]["query"] = live_query
        canary_payload["completion_evidence"]["query"] = live_query
        canary_payload["completion_evidence"]["search_submission"]["query"] = live_query
        for observation in canary_payload["completion_evidence"]["stable_bottom"][
            "observations"
        ]:
            observation["query"] = live_query
        canary_payload["messages"][0]["search_query"] = live_query
        self.write_json(canary_path, canary_payload)

        scoped_corpus, _manifest = self.build()
        self.assertEqual(
            [row["content"] for row in scoped_corpus["messages"]],
            ["VALID_SCOPE_ROW"],
        )
        excluded = scoped_corpus["authorized_collection_scope"]["excluded"]
        self.assertEqual(excluded["ambiguous_fail_closed_file_count"], 1)
        self.assertIn(
            "query_in_target_requested_container_name_mismatch",
            excluded["files"][0]["reason"],
        )

        valid_path.unlink()
        canary_path.unlink()
        self.write_segment(
            "questions_valid_only.json",
            container_id=QUESTIONS_ID,
            channel_name=QUESTIONS_NAME,
            message=self.message(1, 73, "VALID_SCOPE_ROW"),
        )
        valid_corpus, _valid_manifest = self.build()
        self.mark_premium_message_scope_closed(valid_corpus)
        corpus_path = self.write_json(self.root / "valid_scope_corpus.json", valid_corpus)
        database_path = self.root / "query_binding.sqlite"
        cardinal_db.build_database(
            [corpus_path], database_path, authorized_scope_path=self.scope_path
        )
        import sqlite3

        connection = sqlite3.connect(database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM messages WHERE content_text LIKE '%OUTSIDE_LIVE_CANARY%'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'OUTSIDE_LIVE_CANARY'"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_external_embed_source_channel_never_expands_questions_scope(self) -> None:
        message = self.message(1, 574, "")
        message["attachments"] = [
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
                    "owner_message_id": message["message_id"],
                    "owner_channel_id": QUESTIONS_ID,
                    "source_channel_id": "1278211283656773643",
                    "dom_relation": "embed_descendant",
                },
            }
        ]
        self.write_segment(
            "questions_external_embed.json",
            container_id=QUESTIONS_ID,
            channel_name=QUESTIONS_NAME,
            message=message,
        )
        corpus, _manifest = self.build()
        self.assertEqual(len(corpus["messages"]), 1)
        stored = corpus["messages"][0]
        self.assertEqual(stored["collection_channel_id"], QUESTIONS_ID)
        self.assertEqual(
            stored["attachments"][0]["thread_channel_id"],
            "1278211283656773643",
        )
        self.assertEqual(
            stored["attachments"][0]["ownership_status"], "non_owned_exact"
        )
        self.assertEqual(stored["attachments"][0]["capture_status"], "metadata_only")
        self.assertNotIn(
            "1278211283656773643",
            {
                row["container_id"]
                for row in corpus["inventory"]["containers"]
            },
        )

    def test_contradictory_row_collection_provenance_is_not_rewritten(self) -> None:
        path = self.write_segment(
            "questions_row_conflict.json",
            container_id=QUESTIONS_ID,
            channel_name=QUESTIONS_NAME,
            message=self.message(1, 74, "ROW_PROVENANCE_CANARY"),
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["messages"][0]["collection_channel_id"] = OUTSIDE_ID
        payload["messages"][0]["collection_channel_name"] = "live"
        self.write_json(path, payload)
        corpus, _manifest = self.build()
        self.assertEqual(corpus["messages"], [])
        excluded = corpus["authorized_collection_scope"]["excluded"]
        self.assertEqual(excluded["ambiguous_fail_closed_file_count"], 1)
        self.assertIn(
            "collection_channel_id_mismatch_or_missing",
            excluded["files"][0]["reason"],
        )

    def test_logical_questions_name_is_not_a_discord_query_identity(self) -> None:
        path = self.write_segment(
            "logical_questions_token.json",
            container_id=QUESTIONS_ID,
            channel_name=QUESTIONS_NAME,
            message=self.message(1, 75, "LOGICAL_NAME_CANARY"),
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        wrong_query = "in:questions after:2025-12-31 before:2026-01-02"
        payload["segment"]["query"] = wrong_query
        payload["messages"][0]["search_query"] = wrong_query
        payload["completion_evidence"]["query"] = wrong_query
        payload["completion_evidence"]["search_submission"]["query"] = wrong_query
        for observation in payload["completion_evidence"]["stable_bottom"][
            "observations"
        ]:
            observation["query"] = wrong_query
        self.write_json(path, payload)
        corpus, _manifest = self.build()
        self.assertEqual(corpus["messages"], [])
        self.assertIn(
            "query_in_target_requested_container_name_mismatch",
            corpus["authorized_collection_scope"]["excluded"]["files"][0][
                "reason"
            ],
        )

    def test_db_rejects_outside_explicit_occurrence_before_attachment_fts(self) -> None:
        self.write_segment(
            "questions_attachment_occurrence.json",
            container_id=QUESTIONS_ID,
            channel_name=QUESTIONS_NAME,
            message=self.message(1, 76, "VALID_MESSAGE_WITH_TAMPERED_OCCURRENCE"),
        )
        corpus, _manifest = self.build()
        self.mark_premium_message_scope_closed(corpus)
        occurrence = corpus["occurrences"][0]
        occurrence["query_container_id"] = OUTSIDE_ID
        occurrence["message_container_id"] = OUTSIDE_ID
        occurrence["source_query"] = (
            "in:live after:2025-12-31 before:2026-01-02"
        )
        occurrence["payload"]["search_query"] = occurrence["source_query"]
        occurrence["payload"]["collection_channel_id"] = OUTSIDE_ID
        occurrence["payload"]["collection_channel_name"] = "live"
        attachment_id = str(
            self.message(1, 77, "attachment identity")["message_id"]
        )
        extraction_text = "OUTSIDE_ATTACHMENT_EXTRACTION_CANARY"
        corpus["messages"][0]["attachments"] = [
            {
                "attachment_id": attachment_id,
                "filename": "outside.txt",
                "capture_status": "metadata_only",
                "extraction_status": "complete",
                "extraction_artifacts": [
                    {
                        "status": "complete",
                        "method": "fixture",
                        "extracted_text": extraction_text,
                        "local_artifact_verified": True,
                        "local_package_path": (
                            "attachments/extractions/fixture/outside.txt"
                        ),
                        "content_sha256": hashlib.sha256(
                            extraction_text.encode("utf-8")
                        ).hexdigest(),
                        "byte_size": len(extraction_text.encode("utf-8")),
                    }
                ],
            }
        ]
        corpus_path = self.write_json(
            self.root / "outside_occurrence_attachment.json", corpus
        )
        database_path = self.root / "outside_occurrence_attachment.sqlite"
        with self.assertRaisesRegex(
            ValueError, "Out-of-scope explicit occurrence query container"
        ):
            cardinal_db.build_database(
                [corpus_path],
                database_path,
                authorized_scope_path=self.scope_path,
            )
        self.assertFalse(database_path.exists())

    def test_build_excludes_outside_segment_and_records_hash_counts(self) -> None:
        included = self.write_segment(
            "questions_2026-01-01_2026-01-01.json",
            container_id=QUESTIONS_ID,
            channel_name=QUESTIONS_NAME,
            message=self.message(1, 1, "included"),
        )
        outside = self.write_segment(
            "live_2026-01-01_2026-01-01.json",
            container_id=OUTSIDE_ID,
            channel_name="live",
            message=self.message(1, 2, "must not enter scoped corpus"),
        )
        corpus, manifest = self.build()
        self.assertEqual([row["content"] for row in corpus["messages"]], ["included"])
        self.assertEqual(len(corpus["segments"]), 1)
        self.assertEqual(corpus["segments"][0]["source_file_sha256"], hashlib.sha256(included.read_bytes()).hexdigest())
        excluded = corpus["authorized_collection_scope"]["excluded"]
        self.assertEqual(excluded["segment_file_count"], 1)
        self.assertEqual(excluded["declared_message_row_count"], 1)
        self.assertEqual(excluded["files"][0]["sha256"], hashlib.sha256(outside.read_bytes()).hexdigest())
        self.assertNotIn(OUTSIDE_ID, {row["container_id"] for row in manifest["inventory"]["containers"]})

    def test_direct_child_query_is_included_only_with_proven_parentage(self) -> None:
        self.write_segment(
            "thread_2026-01-01_2026-01-01.json",
            container_id=THREAD_ID,
            channel_name="journal-thread",
            message=self.message(1, 3, "proven child"),
        )
        corpus, _manifest = self.build()
        self.assertEqual(corpus["messages"][0]["content"], "proven child")
        self.assertEqual(
            corpus["segments"][0]["query_container_id"],
            THREAD_ID,
        )

    def test_ambiguous_requested_id_source_is_excluded_and_blocks_scope_gate(self) -> None:
        self.write_segment(
            "questions_2026-01-01_2026-01-01.json",
            container_id=QUESTIONS_ID,
            channel_name=QUESTIONS_NAME,
            message=self.message(1, 4, "ambiguous"),
            id_source="filename_inference",
        )
        corpus, manifest = self.build()
        self.assertEqual(corpus["counts"]["unique_messages"], 0)
        self.assertEqual(
            corpus["authorized_collection_scope"]["excluded"]["ambiguous_fail_closed_file_count"],
            1,
        )
        gate = next(
            row for row in manifest["release_gates"]
            if row["gate"] == "authorized_collection_scope_enforced"
        )
        self.assertFalse(gate["passed"])

    def test_scoped_inventory_contains_only_parents_and_proven_children(self) -> None:
        self.write_segment(
            "questions_2026-01-01_2026-01-01.json",
            container_id=QUESTIONS_ID,
            channel_name=QUESTIONS_NAME,
            message=self.message(1, 5, "included"),
        )
        _corpus, manifest = self.build()
        ids = {row["container_id"] for row in manifest["inventory"]["containers"]}
        self.assertEqual(
            ids,
            {
                STUDENT_ID,
                PREMIUM_ID,
                QUESTIONS_ID,
                THREAD_ID,
                NEW_THREAD_ONE,
                NEW_THREAD_TWO,
            },
        )
        self.assertEqual(
            manifest["inventory"]["scope_derivation"]["out_of_scope_inventory_rows_excluded"],
            1,
        )

    def test_additive_child_reconciliation_adds_ids_but_blocks_false_closure(self) -> None:
        reconciliation = self.write_child_reconciliation()
        self.write_segment(
            "questions_2026-01-01_2026-01-01.json",
            container_id=QUESTIONS_ID,
            channel_name=QUESTIONS_NAME,
            message=self.message(1, 30, "included"),
        )
        corpus, manifest = build_corpus.build_corpus(
            segment_dirs=[self.segments, self.premium_segments],
            inventory_path=self.inventory_path,
            authorized_scope_path=self.scope_path,
            scoped_child_inventory_reconciliation_path=reconciliation,
            provenance_root=self.root,
            data_cutoff_utc=dt.datetime(2026, 7, 21, 6, tzinfo=dt.timezone.utc),
        )
        inventory_ids = {
            row["container_id"] for row in manifest["inventory"]["containers"]
        }
        self.assertTrue({NEW_THREAD_ONE, NEW_THREAD_TWO}.issubset(inventory_ids))
        self.assertFalse(manifest["inventory"]["validated_complete"])
        self.assertIn(
            "premium_journals_scoped_reconciliation_closure_not_proven",
            manifest["inventory"]["validation_errors"],
        )
        summary = corpus["authorized_collection_scope"][
            "child_inventory_reconciliation"
        ]
        self.assertEqual(summary["exact_known_union_thread_count"], 3)
        self.assertEqual(summary["added_thread_ids"], sorted([NEW_THREAD_ONE, NEW_THREAD_TWO]))
        self.assertFalse(summary["closure_proven"])
        self.assertFalse(manifest["release_ready"])

    def test_legacy_premium_root_is_preserved_but_never_authoritative(self) -> None:
        fresh_path = self.write_segment(
            "premium_legacy_fixture.json",
            container_id=PREMIUM_ID,
            channel_name="premium-journals",
            message=self.message(1, 31, "legacy Premium bytes"),
        )
        legacy_path = self.segments / fresh_path.name
        self.write_json(
            legacy_path,
            json.loads(fresh_path.read_text(encoding="utf-8")),
        )
        fresh_path.unlink()
        corpus, _manifest = self.build()
        self.assertEqual(corpus["messages"], [])
        excluded = corpus["authorized_collection_scope"]["excluded"]
        matching = [
            row
            for row in excluded["files"]
            if row.get("relative_path", "").endswith(legacy_path.name)
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(
            matching[0]["reason"],
            "premium_journals_legacy_directory_preservation_only",
        )
        path_gate = corpus["authorized_collection_scope"]["canonical_path_policy"]
        self.assertEqual(path_gate["legacy_premium_preservation_file_count"], 1)
        self.assertEqual(path_gate["accepted_premium_segment_count"], 0)
        self.assertEqual(path_gate["premium_collector_version_required"], "2.6")

    def test_premium_authoritative_root_rejects_wrong_collector_generation(self) -> None:
        self.write_segment(
            "premium_wrong_generation.json",
            container_id=PREMIUM_ID,
            channel_name="premium-journals",
            message=self.message(1, 32, "wrong generation"),
        )
        corpus, _manifest = self.build()
        path_gate = corpus["authorized_collection_scope"]["canonical_path_policy"]
        self.assertFalse(path_gate["passed"])
        self.assertEqual(path_gate["premium_collector_version_mismatch_count"], 0)
        self.assertEqual(
            path_gate["invalid_premium_authoritative_paths"],
            ["raw/channel_segments_v2_5/premium_wrong_generation.json"],
        )
        self.assertEqual(path_gate["invalid_premium_authoritative_file_count"], 1)
        matching = [
            gate
            for gate in corpus["release_gates"]
            if gate.get("gate")
            == "premium_journals_authoritative_v2_5_source_integrity"
        ]
        self.assertEqual(len(matching), 1)
        self.assertFalse(matching[0]["passed"])

    def test_message_scope_closure_is_satisfiable_without_zero_message_threads(self) -> None:
        policy = self.policy()
        start = dt.date(2026, 1, 1)
        segments = []
        for offset in range(201):
            day = start + dt.timedelta(days=offset)
            segments.append(
                {
                    "segment_id": f"premium-{day.isoformat()}",
                    "query_container_id": PREMIUM_ID,
                    "input_role": "channel_capture",
                    "start_date": day.isoformat(),
                    "end_date": day.isoformat(),
                    "query": (
                        "in:premium-journals "
                        f"after:{(day - dt.timedelta(days=1)).isoformat()} "
                        f"before:{(day + dt.timedelta(days=1)).isoformat()}"
                    ),
                    "computed_complete": True,
                    "completion_evidence_valid": True,
                    "completion_evidence": {"terminal_state": "stable_bottom"},
                }
            )
        closure = scoped.evaluate_premium_journals_message_scope_closure(
            scope=policy,
            segments=segments,
            occurrences=[
                {
                    "occurrence_id": "exact-row",
                    "source_kind": "channel_segment",
                    "query_container_id": PREMIUM_ID,
                    "message_container_id": THREAD_ID,
                    "message_container_id_source": (
                        "premium_whole_artifact_byte_bound_row_mapping"
                    ),
                    "parent_container_id": PREMIUM_ID,
                    "quarantine_reasons": [],
                    "payload": {
                        "thread_channel_id": THREAD_ID,
                        "thread_channel_id_source": (
                            "forum_group_header_navigation_exact"
                        ),
                    },
                }
            ],
            proven_children={
                THREAD_ID: {
                    "child_container_id": THREAD_ID,
                    "parent_container_id": PREMIUM_ID,
                }
            },
        )
        self.assertTrue(closure["passed"])
        self.assertEqual(closure["complete_calendar_day_count"], 201)
        self.assertEqual(closure["unresolved_row_binding_count"], 0)
        self.assertIn("Zero-message", closure["outside_message_bearing_scope"])

    def test_message_scope_closure_rejects_incomplete_daily_census(self) -> None:
        closure = scoped.evaluate_premium_journals_message_scope_closure(
            scope=self.policy(),
            segments=[
                {
                    "segment_id": "forged-full-window-shortcut",
                    "query_container_id": PREMIUM_ID,
                    "input_role": "channel_capture",
                    "start_date": "2026-01-01",
                    "end_date": "2026-07-20",
                    "query": (
                        "in:premium-journals after:2025-12-31 before:2026-07-21"
                    ),
                    "computed_complete": True,
                    "completion_evidence_valid": True,
                    "completion_evidence": {"terminal_state": "stable_bottom"},
                }
            ],
            occurrences=[],
            proven_children={},
        )
        self.assertFalse(closure["passed"])
        self.assertEqual(closure["parent_segment_count"], 1)
        self.assertEqual(closure["invalid_daily_partition_segment_count"], 1)
        self.assertEqual(closure["complete_calendar_day_count"], 0)

    def test_cardinal_database_accepts_scoped_corpus_and_records_scope_hash(self) -> None:
        self.write_segment(
            "questions_2026-01-01_2026-01-01.json",
            container_id=QUESTIONS_ID,
            channel_name=QUESTIONS_NAME,
            message=self.message(1, 50, "database scoped"),
        )
        corpus, _manifest = self.build()
        self.mark_premium_message_scope_closed(corpus)
        corpus_path = self.write_json(self.root / "scoped_corpus.json", corpus)
        database_path = self.root / "scoped.sqlite"
        cardinal_db.build_database(
            [corpus_path],
            database_path,
            authorized_scope_path=self.scope_path,
        )
        import sqlite3

        connection = sqlite3.connect(database_path)
        try:
            channels = {
                row[0]
                for row in connection.execute("SELECT DISTINCT channel_id FROM messages")
            }
            meta = dict(connection.execute("SELECT key,value FROM meta"))
        finally:
            connection.close()
        self.assertEqual(channels, {QUESTIONS_ID})
        self.assertEqual(meta["authorized_collection_scope_enabled"], "1")
        self.assertEqual(
            meta["authorized_collection_scope_sha256"], self.policy().source_sha256
        )

    def test_cardinal_database_rejects_out_of_scope_message_even_with_scope_summary(self) -> None:
        self.write_segment(
            "questions_2026-01-01_2026-01-01.json",
            container_id=QUESTIONS_ID,
            channel_name=QUESTIONS_NAME,
            message=self.message(1, 51, "tampered channel"),
        )
        corpus, _manifest = self.build()
        self.mark_premium_message_scope_closed(corpus)
        corpus["messages"][0]["channel_id"] = OUTSIDE_ID
        corpus_path = self.write_json(self.root / "tampered_scoped_corpus.json", corpus)
        with self.assertRaisesRegex(ValueError, "Out-of-scope message container"):
            cardinal_db.build_database(
                [corpus_path],
                self.root / "must_not_exist.sqlite",
                authorized_scope_path=self.scope_path,
            )

    def test_attachment_manifest_is_reduced_in_memory_to_scoped_messages(self) -> None:
        inside = self.message(1, 60, "inside attachment")
        outside = self.message(1, 61, "outside attachment")
        for offset, (row, channel_id) in enumerate(
            ((inside, QUESTIONS_ID), (outside, OUTSIDE_ID)), start=1
        ):
            attachment_id = build_corpus.snowflake_id_for_datetime(
                dt.datetime(2026, 1, 1, 18, 0, offset, tzinfo=dt.timezone.utc),
                offset,
            )
            row["channel_id"] = channel_id
            row["attachments"] = [
                {
                    "attachment_id": attachment_id,
                    "relation_type": "owned",
                    "ownership_status": "owned_exact",
                    "ownership_evidence": {
                        "schema_version": "1.0.0",
                        "exact": True,
                        "basis": "test_exact_message_accessories",
                        "owner_message_id": row["message_id"],
                        "owner_channel_id": channel_id,
                        "source_channel_id": channel_id,
                    },
                    "filename": f"chart-{offset}.png",
                    "url": (
                        f"https://cdn.discordapp.com/attachments/{channel_id}/"
                        f"{attachment_id}/chart-{offset}.png"
                    ),
                }
            ]
        source_corpus = self.write_json(
            self.root / "attachment_source.json",
            {
                "artifact_type": build_corpus.ARTIFACT_TYPE_WORKING,
                "messages": [inside, outside],
            },
        )
        manifest_path = self.root / "attachment_manifest.json"
        attachment_archiver.create_or_resume_manifest(source_corpus, manifest_path)
        source_registry: dict[str, dict[str, object]] = {}
        summary = build_corpus.apply_attachment_archive_manifest(
            messages=[inside],
            manifest_path=manifest_path,
            archive_root=None,
            provenance_root=self.root,
            source_registry=source_registry,
            authorized_message_ids={str(inside["message_id"])},
        )
        self.assertEqual(summary["manifest_attachment_count"], 1)
        self.assertEqual(
            summary["authorized_scope_filtering"]["excluded_owned_entry_count"],
            1,
        )
        self.assertEqual(summary["extra_entries"], [])
        self.assertFalse(summary["authorized_scope_filtering"]["source_manifest_bytes_mutated"])


if __name__ == "__main__":
    unittest.main()
