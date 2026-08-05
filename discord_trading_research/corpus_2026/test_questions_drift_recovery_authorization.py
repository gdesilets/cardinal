from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import questions_drift_recovery_authorization as recovery


ROOT = Path(__file__).resolve().parent
AUTHORIZATION_PATH = (
    ROOT / "working" / "questions_2026-07-14_2026-07-20_drift_recovery_authorization_v3.json"
)


class QuestionsDriftRecoveryAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.authorization = json.loads(AUTHORIZATION_PATH.read_text(encoding="utf-8"))
        cls.policy = json.loads(recovery.V2_AUTHORIZATION_PATH.read_text(encoding="utf-8"))

    def test_authorization_is_consumed_after_canonical_promotion(self) -> None:
        errors = recovery.validate_authorization(ROOT, AUTHORIZATION_PATH)
        self.assertIn("superseded_v2_not_valid:canonical_target_not_absent", errors)

    def test_v3_binds_v2_and_requires_exact_collector_contract(self) -> None:
        self.assertEqual(3, self.authorization["authorization_version"])
        self.assertEqual(
            recovery.V2_AUTHORIZATION_PATH.relative_to(ROOT).as_posix(),
            self.authorization["supersedes"]["path"],
        )
        mutated = copy.deepcopy(self.authorization)
        mutated["collector_compatibility"]["collector_version_required"] = "2.5"
        path = ROOT / "working" / "_test_questions_drift_auth_v3_contract.json"
        try:
            path.write_text(json.dumps(mutated), encoding="utf-8")
            errors = recovery.validate_authorization(ROOT, path)
        finally:
            path.unlink(missing_ok=True)
        self.assertIn("collector_compatibility_version_mismatch", errors)

    def test_v3_superseded_source_hash_drift_fails_closed(self) -> None:
        mutated = copy.deepcopy(self.authorization)
        mutated["supersedes"]["sha256"] = "0" * 64
        path = ROOT / "working" / "_test_questions_drift_auth_hash.json"
        try:
            path.write_text(json.dumps(mutated), encoding="utf-8")
            errors = recovery.validate_authorization(ROOT, path)
        finally:
            path.unlink(missing_ok=True)
        self.assertIn("supersedes_sha256_mismatch", errors)

    def test_restart_permission_cannot_allow_partial_reuse(self) -> None:
        mutated = copy.deepcopy(self.authorization)
        mutated["inherited_v2_authorization_state"][
            "fresh_search_and_zero_resumption_required"
        ] = False
        path = ROOT / "working" / "_test_questions_drift_auth_permission.json"
        try:
            path.write_text(json.dumps(mutated), encoding="utf-8")
            errors = recovery.validate_authorization(ROOT, path)
        finally:
            path.unlink(missing_ok=True)
        self.assertIn("v3_inherited_v2_authorization_state_mismatch", errors)

    def test_clean_restart_candidate_is_refused_after_promotion(self) -> None:
        auth_sha = recovery.sha256_file(AUTHORIZATION_PATH)
        query = self.policy["route"]["segment"]["query"]
        ids = ["1528960449884721152", "1528959999999999999"]
        messages = [
            {
                "message_id": message_id,
                "result_index": index,
                "result_set_size": 2,
                "search_query": query,
                "collection_channel_id": "1273692573898113076",
                "collection_channel_name": "❓│questions",
                "content_scope_exact": True,
                "exact_permalink": "https://discord.com/channels/1167376964680691732/1273692573898113076/" + message_id,
            }
            for index, message_id in enumerate(ids, start=1)
        ]
        observation = {
            "query": query,
            "current_page": 1,
            "first_result_index": 1,
            "last_result_index": 2,
            "result_set_size": 2,
            "has_enabled_next": False,
        }
        candidate = {
            "collector_version": "2.6",
            "guild_id": "1167376964680691732",
            "collection_scope": "channel-scoped",
            "requested_container": {
                "channel_id": "1273692573898113076",
                "channel_name": "❓│questions",
                "channel_kind": "text channel",
                "category_name": "PREMIUM",
                "channel_id_source": "inventory_exact_href",
            },
            "segment": self.policy["route"]["segment"],
            "reported_total": 2,
            "reported_pages": 1,
            "captured_rows": 2,
            "unique_message_ids": 2,
            "gap_indices": [],
            "container_mismatch_count": 0,
            "complete": True,
            "resumed_from_partial_rows": 0,
            "messages": messages,
            "completion_evidence": {"terminal_state": "stable_bottom", "stable_bottom": {"observations": [observation, copy.deepcopy(observation)]}},
            "recovery_execution": {
                "authorization_path": AUTHORIZATION_PATH.relative_to(ROOT).as_posix(),
                "authorization_sha256": auth_sha,
                "restart_number": 1,
                "fresh_search_submission_count": 1,
                "resumed_from_partial_rows": 0,
                "promotion_mode": "atomic_after_full_validation",
                "staging_path": "working/questions_2026-07-14_2026-07-20_drift_recovery/restart_001/candidate.json",
            },
        }
        errors = recovery.validate_clean_restart_candidate(candidate, AUTHORIZATION_PATH)
        self.assertIn(
            "authorization_not_usable:superseded_v2_not_valid:canonical_target_not_absent",
            errors,
        )
        candidate["resumed_from_partial_rows"] = 1
        candidate["recovery_execution"]["resumed_from_partial_rows"] = 1
        candidate["completion_evidence"]["stable_bottom"]["observations"].pop()
        self.assertIn(
            "authorization_not_usable:superseded_v2_not_valid:canonical_target_not_absent",
            recovery.validate_clean_restart_candidate(candidate, AUTHORIZATION_PATH),
        )


if __name__ == "__main__":
    unittest.main()
