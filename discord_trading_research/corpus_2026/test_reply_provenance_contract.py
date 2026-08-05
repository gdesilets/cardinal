from __future__ import annotations

import copy
import unittest

import build_corpus
import reply_provenance_contract as contract


def exact_wordle_row(message_id: str, actor: str) -> dict:
    context = f"{actor}\nused\nPlay"
    return {
        "message_id": message_id,
        "article_id": f"search-result-{message_id}",
        "article_aria_labelledby": (
            f"message-username-{message_id} uid_3 "
            f"message-content-{message_id} "
            f"message-accessories-{message_id} uid_4 "
            f"message-timestamp-{message_id}"
        ),
        "author": "Wordle",
        "author_id": contract.EXECUTED_COMMAND_AUTHOR_ID,
        "author_id_source": "owner_scoped_avatar_cdn_path",
        "author_id_conflict": False,
        "author_verified_app_exact": True,
        "content_scope_exact": True,
        "content_text": f"{actor} was playing",
        "reply_context": context,
        "reply_to_content": context,
        "reply_context_present": True,
        "reply_context_scope_exact": False,
        "reply_context_dom_class": (
            "repliedMessage_c19a55 messageSpine_c19a55 "
            "executedCommand_c19a55"
        ),
        "reply_context_dom_tag": "DIV",
        "reply_context_aria_hidden": True,
        "reply_context_article_binding_exact": True,
        "reply_context_owner_message_id": message_id,
        "reply_context_executed_command_exact": True,
        "reply_target_owner_scoped": False,
        "reply_target_scope_exact": False,
        "reply_target_content_text": "",
        "reply_to_message_id": None,
        "reply_to_channel_id": None,
        "reply_to_permalink": None,
        "reply_to_message_id_source": None,
        "reply_to_message_id_candidates": [],
        "reply_target_id_candidates": [],
        "reply_target_content_id": None,
        "reply_target_aria_labelledby": None,
        "reply_target_aria_describedby": None,
        "reply_target_data_list_item_id": None,
        "reply_to_message_id_conflict": False,
        "reply_to_channel_id_conflict": False,
        "reply_target_resolution_status": contract.EXECUTED_COMMAND_STATUS,
        "reply_target_unavailability_documented": True,
        "reply_context_non_reply_exact": True,
        "reply_context_non_reply_type": contract.EXECUTED_COMMAND_NON_REPLY_TYPE,
    }


class ExecutedCommandContractTests(unittest.TestCase):
    def test_multiple_rows_are_accepted_by_structure_not_an_id_allowlist(self) -> None:
        anchor = exact_wordle_row(
            contract.EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID, "LukeLarps"
        )
        dynamic_rows = [
            exact_wordle_row("1523977453436010537", "TenshiKira"),
            exact_wordle_row("1526000000000000001", "FuturePlayer"),
        ]
        anchor_audit = contract.audit_executed_command_contexts(
            [anchor],
            expected_message_ids=[
                contract.EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID
            ],
        )
        dynamic_audit = contract.audit_executed_command_contexts(
            dynamic_rows,
            expected_message_ids=[],
        )
        self.assertTrue(anchor_audit["passed"], anchor_audit)
        self.assertTrue(dynamic_audit["passed"], dynamic_audit)
        self.assertEqual(2, dynamic_audit["accepted_exact_context_count"])

        aggregate = build_corpus.summarize_executed_command_reply_provenance_integrity(
            [
                {
                    "segment_id": "questions:legacy",
                    "executed_command_reply_provenance_integrity": anchor_audit,
                },
                {
                    "segment_id": "questions:dynamic",
                    "executed_command_reply_provenance_integrity": dynamic_audit,
                },
            ]
        )
        self.assertTrue(aggregate["passed"], aggregate)
        self.assertEqual(3, aggregate["candidate_count"])
        self.assertEqual(3, aggregate["accepted_exact_context_count"])
        self.assertEqual(1, aggregate["legacy_anchor_count"])
        envelope = {
            "executed_command_reply_provenance_integrity": aggregate,
            "release_gates": [
                {
                    "gate": "executed_command_reply_provenance_integrity",
                    "passed": True,
                    "detail": copy.deepcopy(aggregate),
                }
            ],
        }
        self.assertEqual(
            [], contract.release_executed_command_integrity_errors(envelope)
        )

    def test_legacy_anchor_is_required_but_dynamic_ids_are_not_predeclared(self) -> None:
        dynamic = exact_wordle_row("1526000000000000002", "AnotherPlayer")
        missing_anchor = contract.audit_executed_command_contexts(
            [dynamic],
            expected_message_ids=[
                contract.EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID
            ],
        )
        self.assertFalse(missing_anchor["passed"])
        self.assertIn(
            "expected_executed_command_message_missing_or_duplicated",
            missing_anchor["failures"][-1]["reasons"],
        )
        dynamic_only = contract.audit_executed_command_contexts(
            [dynamic], expected_message_ids=[]
        )
        self.assertTrue(dynamic_only["passed"], dynamic_only)

    def test_structural_lookalikes_and_duplicate_ids_fail_closed(self) -> None:
        valid = exact_wordle_row("1526000000000000003", "Player")
        mutations = (
            {"reply_context_dom_class": "executedCommand_lookalike"},
            {"reply_context_aria_hidden": False},
            {"reply_context_article_binding_exact": False},
            {"reply_context_owner_message_id": "1526000000000000004"},
            {"author_verified_app_exact": False},
            {"author_id": "1211781489931452448"},
            {"reply_target_id_candidates": [{}]},
            {"reply_to_message_id_candidates": [{}]},
            {"reply_target_aria_describedby": "message-content-1"},
            {
                "reply_context": "Player\nused\nOther",
                "reply_to_content": "Player\nused\nOther",
            },
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                audit = contract.audit_executed_command_contexts(
                    [{**valid, **mutation}], expected_message_ids=[]
                )
                self.assertFalse(audit["passed"], audit)
        duplicate = contract.audit_executed_command_contexts(
            [valid, copy.deepcopy(valid)], expected_message_ids=[]
        )
        self.assertFalse(duplicate["passed"])
        self.assertTrue(
            any(
                "executed_command_candidate_message_id_duplicated"
                in row["reasons"]
                for row in duplicate["failures"]
            ),
            duplicate,
        )


if __name__ == "__main__":
    unittest.main()
