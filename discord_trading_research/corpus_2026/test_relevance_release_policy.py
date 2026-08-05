from __future__ import annotations

import copy
import datetime as dt
import unittest

import relevance_release_policy as policy


GUILD_ID = "1167376964680691732"
TARGET_ID = "1493590222703824997"
FORUM_ID = "1283941772577472643"
THREAD_ID = "1456316273788063925"
MESSAGE_ID = "1456316273788063926"


class RelevanceReleasePolicyTests(unittest.TestCase):
    def test_post_cutoff_inventory_gate_uses_authenticated_resnapshot_evidence(self) -> None:
        cutoff = dt.datetime(2026, 7, 21, 5, tzinfo=dt.timezone.utc)
        inventory = {
            "inventory_complete": True,
            "captured_at_utc": "2026-07-21T05:10:00Z",
            "accessible_scope": {
                "top_level_containers": {"declared_complete": True},
                "post_cutoff_navigation_resnapshot": {
                    "declared_complete": True,
                    "validated_complete": True,
                    "status": "complete",
                    "completion_evidence": {
                        "authenticated": True,
                        "navigation_pass_complete": True,
                        "terminal_state_observed": True,
                        "capture_completed_at_utc": "2026-07-21T05:10:00Z",
                        "source_refs": ["discord-ui:server-navigation:terminal"],
                    },
                },
            },
        }
        self.assertTrue(policy._post_cutoff_inventory_gate(inventory, cutoff)["passed"])
        inventory["captured_at_utc"] = "2026-07-20T22:59:00Z"
        self.assertFalse(policy._post_cutoff_inventory_gate(inventory, cutoff)["passed"])

    def test_release_evidence_envelope_is_fail_closed(self) -> None:
        cutoff = dt.datetime(2026, 7, 21, 5, tzinfo=dt.timezone.utc)
        evidence = {
            "artifact_type": "discord_release_evidence",
            "status": "complete",
            "outside_sources_used": 0,
            "required_cutoff_utc": "2026-07-21T05:00:00Z",
            "generated_at_utc": "2026-07-21T05:01:00Z",
            "generator": {
                "local_only": True,
                "browser_calls_made": 0,
                "network_calls_made": 0,
                "raw_files_modified": 0,
            },
            "source_artifacts": [
                {
                    "kind": "corpus_data",
                    "path": "working/corpus.json",
                    "sha256": "A" * 64,
                    "size_bytes": 12,
                }
            ],
        }
        self.assertTrue(
            policy._release_evidence_envelope(evidence, cutoff)["passed"]
        )
        evidence["status"] = "pending"
        result = policy._release_evidence_envelope(evidence, cutoff)
        self.assertFalse(result["passed"])
        self.assertIn("release_evidence_status_not_complete", result["errors"])

        evidence["status"] = "complete"
        evidence["outside_sources_used"] = []
        result = policy._release_evidence_envelope(evidence, cutoff)
        self.assertFalse(result["passed"])
        self.assertIn("outside_sources_used_not_zero", result["errors"])

    def test_partial_targeted_full_capture_is_diagnostic_not_required(self) -> None:
        plan = {
            "channel_policies": [
                {
                    "channel_id": TARGET_ID,
                    "name": "newsfeed",
                    "policy": "targeted_search_plus_residual_audit",
                }
            ]
        }
        segments = [
            {
                "segment_id": "diagnostic-partial",
                "input_role": "channel_capture",
                "query_container_id": TARGET_ID,
                "query": "in:newsfeed after:2025-12-31 before:2026-01-02",
                "computed_complete": False,
            }
        ]
        classified = policy.classify_segments(plan, segments)
        self.assertEqual(
            classified[0]["policy_role"], "diagnostic_targeted_full_capture"
        )
        wrapped = {"classified_segments": classified}
        self.assertEqual(policy.policy_required_partial_segments(wrapped), [])

    def test_targeted_job_requires_targeted_query_role_and_full_date_union(self) -> None:
        job = {
            "job_id": "target__x",
            "job_kind": "targeted_search",
            "args": {
                "startIso": "2026-01-01",
                "endIso": "2026-01-02",
                "queryPrefix": "in:newsfeed rejection block",
                "collectorOptions": {"channelId": TARGET_ID},
            },
        }
        diagnostic = {
            "segment_id": "diagnostic",
            "policy_role": "diagnostic_targeted_full_capture",
            "query_container_id": TARGET_ID,
            "query": "in:newsfeed after:2025-12-31 before:2026-01-03",
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
            "computed_complete": True,
            "reported_total": 99,
        }
        first = {
            "segment_id": "first",
            "policy_role": "required_targeted_query",
            "query_container_id": TARGET_ID,
            "query": "in:newsfeed rejection block after:2025-12-31 before:2026-01-02",
            "start_date": "2026-01-01",
            "end_date": "2026-01-01",
            "computed_complete": True,
            "reported_total": 1,
        }
        result = policy._segment_job_coverage(job, [diagnostic, first])
        self.assertFalse(result["passed"])
        second = copy.deepcopy(first)
        second.update(
            {
                "segment_id": "second",
                "query": "in:newsfeed rejection block after:2026-01-01 before:2026-01-03",
                "start_date": "2026-01-02",
                "end_date": "2026-01-02",
            }
        )
        result = policy._segment_job_coverage(job, [diagnostic, first, second])
        self.assertTrue(result["passed"])
        self.assertEqual(result["reported_total_sum"], 2)

    def test_count_reconciliation_requires_late_cutoff_equal_totals_and_refs(self) -> None:
        jobs = [{"channel_id": "111111111111111", "reported_total_sum": 7}]
        required = dt.datetime(2026, 7, 21, 5, tzinfo=dt.timezone.utc)
        evidence = {
            "full_capture_count_reconciliation": [
                {
                    "channel_id": "111111111111111",
                    "status": "passed",
                    "segment_reported_total": 7,
                    "refreshed_full_window_reported_total": 7,
                    "observed_at_utc": "2026-07-21T05:01:00Z",
                    "observation_ids": ["count-observation-1"],
                }
            ]
        }
        self.assertTrue(policy._count_reconciliation(jobs, evidence, required)["passed"])
        evidence["full_capture_count_reconciliation"][0]["observation_ids"] = []
        self.assertFalse(policy._count_reconciliation(jobs, evidence, required)["passed"])

    def test_forum_gate_accepts_group_header_parent_and_rejects_conflict(self) -> None:
        plan = {
            "guild": {"guild_id": GUILD_ID},
            "forum_thread_policy": {"parent_channel_id": FORUM_ID},
        }
        inventory = {
            "accessible_scope": {
                "forum_threads": {
                    "validated_complete": True,
                    "unresolved_observed_occurrence_count": 0,
                }
            }
        }
        segments = [
            {"segment_id": "forum-segment", "policy_role": "required_full_capture"}
        ]
        occurrence = {
            "segment_id": "forum-segment",
            "query_container_id": FORUM_ID,
            "message_id": MESSAGE_ID,
            "message_container_id": THREAD_ID,
            "message_container_id_source": (
                "premium_whole_artifact_byte_bound_row_mapping"
            ),
            "parent_container_id": None,
            "payload": {
                "group_header_parent_forum_channel_id": FORUM_ID,
                "exact_permalink": (
                    f"https://discord.com/channels/{GUILD_ID}/{THREAD_ID}/{MESSAGE_ID}"
                ),
                "exact_permalink_status": "thread_id_from_forum_group_header",
                "exact_permalink_conflict_detected": False,
                "thread_channel_id_source": (
                    "forum_group_header_data_list_item_id"
                ),
                "thread_channel_id_exact": True,
            },
        }
        self.assertTrue(policy._forum_gate(plan, inventory, [occurrence], segments)["passed"])
        occurrence["payload"].update(
            {
                "exact_permalink_status": "thread_id_from_forum_group_header_navigation",
                "thread_channel_id_source": "forum_group_header_navigation_exact",
                "thread_channel_id_exact": True,
            }
        )
        self.assertTrue(policy._forum_gate(plan, inventory, [occurrence], segments)["passed"])
        occurrence["payload"]["exact_permalink_conflict_detected"] = True
        result = policy._forum_gate(plan, inventory, [occurrence], segments)
        self.assertFalse(result["passed"])
        self.assertIn("exact_permalink_conflict_detected", result["failures"][0]["reasons"])

        occurrence["payload"]["exact_permalink_conflict_detected"] = False
        occurrence["payload"].update(
            {
                "exact_permalink_status": "thread_id_from_unverified_attachment",
                "thread_channel_id_source": "attachment_cdn_path_unverified",
                "thread_channel_id_exact": False,
            }
        )
        result = policy._forum_gate(plan, inventory, [occurrence], segments)
        self.assertFalse(result["passed"])
        self.assertIn(
            "thread_id_source_not_exact_row_owned_evidence",
            result["failures"][0]["reasons"],
        )

    def test_reply_gate_accepts_owned_scoped_descendant_and_rejects_preview_source(self) -> None:
        owner_id = "1456316273788063999"
        target_id = "1456316273788063000"
        channel_id = "1359593949110472777"
        occurrence = {
            "message_id": owner_id,
            "payload": {
                "message_id": owner_id,
                "reply_context_present": True,
                "reply_to_message_id": target_id,
                "reply_to_message_id_source": "owned_reply_context_descendant_content_id",
                "reply_target_content_id": f"message-content-{target_id}",
                "reply_target_scope_exact": True,
                "reply_to_channel_id": channel_id,
                "reply_to_permalink": (
                    f"https://discord.com/channels/{GUILD_ID}/{channel_id}/{target_id}"
                ),
            },
        }
        plan = {"guild": {"guild_id": GUILD_ID}}
        self.assertTrue(policy._raw_reply_scope_gate(plan, [occurrence])["passed"])
        occurrence["payload"]["reply_to_message_id_source"] = "reply_preview_link"
        result = policy._raw_reply_scope_gate(plan, [occurrence])
        self.assertFalse(result["passed"])
        self.assertIn(
            "reply_target_source_not_row_owned_exact",
            result["failures"][0]["reasons"],
        )

    def test_reply_gate_accepts_only_exact_documented_no_id_states(self) -> None:
        plan = {"guild": {"guild_id": GUILD_ID}}
        base = {
            "message_id": "1459199677718200543",
            "reply_context_present": True,
            "reply_to_message_id": None,
            "reply_to_message_id_source": None,
            "reply_to_channel_id": None,
            "reply_to_permalink": None,
            "reply_to_message_id_candidates": [],
            "reply_target_id_candidates": [],
            "reply_target_content_id": None,
            "reply_target_aria_labelledby": None,
            "reply_target_aria_describedby": None,
            "reply_target_data_list_item_id": None,
            "reply_target_scope_exact": False,
            "reply_to_message_id_conflict": False,
            "reply_to_channel_id_conflict": False,
            "reply_context_non_reply_exact": False,
            "reply_context_non_reply_type": None,
        }
        executed_extra = {
            "message_id": "1523613360099295304",
            "article_id": "search-result-1523613360099295304",
            "article_aria_labelledby": (
                "message-username-1523613360099295304 uid_3 "
                "message-content-1523613360099295304 "
                "message-accessories-1523613360099295304 uid_4 "
                "message-timestamp-1523613360099295304"
            ),
            "author": "Wordle",
            "author_id": "1211781489931452447",
            "author_id_source": "owner_scoped_avatar_cdn_path",
            "author_id_conflict": False,
            "content_scope_exact": True,
            "content_text": "LukeLarps was playing",
            "reply_to_content": "LukeLarps\nused\nPlay",
            "reply_context_scope_exact": False,
            "reply_context_dom_class": (
                "repliedMessage_c19a55 messageSpine_c19a55 "
                "executedCommand_c19a55"
            ),
            "reply_context_dom_tag": "DIV",
            "reply_context_aria_hidden": True,
            "reply_context_article_binding_exact": True,
            "reply_context_owner_message_id": "1523613360099295304",
            "reply_context_executed_command_exact": True,
            "author_verified_app_exact": True,
            "reply_target_owner_scoped": False,
            "reply_target_content_text": "",
            "reply_context_non_reply_exact": True,
            "reply_context_non_reply_type": (
                "discord_application_command_invocation"
            ),
        }
        cases = (
            ("Message could not be loaded", "discord_message_not_loaded", {}),
            (
                "@vale\nClick to see attachment",
                "discord_attachment_preview_without_exact_target_id",
                {},
            ),
            (
                "@target\nClick to see sticker",
                "discord_sticker_preview_without_exact_target_id",
                {},
            ),
            (
                "Click to see voice message",
                "discord_voice_message_preview_without_exact_target_id",
                {},
            ),
            (
                "boy\nused\nmute",
                "discord_dyno_command_context_without_reply_target",
                {
                    "author_id": "155149108183695360",
                    "content_scope_exact": True,
                    "content_text": "",
                    "reply_context_non_reply_exact": True,
                    "reply_context_non_reply_type": "discord_dyno_command_invocation",
                },
            ),
            (
                "LukeLarps\nused\nPlay",
                "discord_executed_command_context_without_reply_target",
                executed_extra,
            ),
        )
        for context, status, extra in cases:
            with self.subTest(status=status):
                payload = {
                    **base,
                    **extra,
                    "reply_context": context,
                    "reply_target_resolution_status": status,
                    "reply_target_unavailability_documented": True,
                }
                occurrence = {"message_id": payload["message_id"], "payload": payload}
                self.assertTrue(
                    policy._raw_reply_scope_gate(plan, [occurrence])["passed"]
                )

        valid_executed = {
            **base,
            **executed_extra,
            "reply_context": "LukeLarps\nused\nPlay",
            "reply_target_resolution_status": (
                "discord_executed_command_context_without_reply_target"
            ),
            "reply_target_unavailability_documented": True,
        }
        for mutation in (
            {"reply_context_dom_class": "executedCommand_lookalike"},
            {"reply_context_article_binding_exact": False},
            {"reply_context_owner_message_id": "1523613360099295305"},
            {"article_id": "search-result-1523613360099295305"},
            {"author": "Wordle lookalike"},
            {"author_id": "1211781489931452448"},
            {"reply_target_aria_describedby": "ambiguous-reference"},
            {"reply_target_id_candidates": [{}]},
            {
                "reply_context": "LukeLarps\nused\nOther",
                "reply_to_content": "LukeLarps\nused\nOther",
            },
        ):
            with self.subTest(executed_command_mutation=mutation):
                payload = {**valid_executed, **mutation}
                result = policy._raw_reply_scope_gate(
                    plan, [{"message_id": payload["message_id"], "payload": payload}]
                )
                self.assertFalse(result["passed"])

        valid_voice = {
            **base,
            "reply_context": "Click to see voice message",
            "reply_target_resolution_status": (
                "discord_voice_message_preview_without_exact_target_id"
            ),
            "reply_target_unavailability_documented": True,
        }
        for mutation in (
            {"reply_target_unavailability_documented": False},
            {"reply_target_resolution_status": "discord_message_not_loaded"},
            {"reply_context": "@target\nClick to see voice message"},
            {
                "reply_target_resolution_status": None,
                "reply_target_unavailability_documented": None,
                "reply_target_state": "unavailable",
            },
        ):
            with self.subTest(mutation=mutation):
                payload = {**valid_voice, **mutation}
                result = policy._raw_reply_scope_gate(
                    plan, [{"message_id": payload["message_id"], "payload": payload}]
                )
                self.assertFalse(result["passed"])

    def test_reply_gate_accepts_owner_scoped_aria_and_data_list_targets(self) -> None:
        plan = {"guild": {"guild_id": GUILD_ID}}
        owner_id = "1456316273788063999"
        target_id = "1456316273788063000"
        channel_id = "1359593949110472777"
        for source, raw_value in (
            (
                "owned_reply_descendant_aria_reference",
                f"message-username-{target_id} message-content-{target_id}",
            ),
            (
                "owned_reply_descendant_data_list_item_id",
                f"chat-messages___{target_id}",
            ),
        ):
            with self.subTest(source=source):
                payload = {
                    "message_id": owner_id,
                    "reply_context_present": True,
                    "reply_context": "@target\nquoted text",
                    "reply_to_message_id": target_id,
                    "reply_to_message_id_source": source,
                    "reply_to_message_id_candidates": [
                        {
                            "message_id": target_id,
                            "channel_id": None,
                            "source": source,
                            "raw_value": raw_value,
                            "owner_scoped": True,
                        }
                    ],
                    "reply_to_channel_id": channel_id,
                    "reply_to_permalink": (
                        f"https://discord.com/channels/{GUILD_ID}/{channel_id}/{target_id}"
                    ),
                    "reply_target_scope_exact": True,
                    "reply_to_message_id_conflict": False,
                    "reply_to_channel_id_conflict": False,
                    "reply_target_resolution_status": "exact_target_id",
                    "reply_target_unavailability_documented": False,
                }
                occurrence = {"message_id": owner_id, "payload": payload}
                self.assertTrue(
                    policy._raw_reply_scope_gate(plan, [occurrence])["passed"]
                )
                payload["reply_to_message_id_candidates"][0]["owner_scoped"] = False
                result = policy._raw_reply_scope_gate(plan, [occurrence])
                self.assertFalse(result["passed"])
                self.assertIn(
                    "reply_target_row_owned_candidate_evidence_invalid",
                    result["failures"][0]["reasons"],
                )


if __name__ == "__main__":
    unittest.main()
