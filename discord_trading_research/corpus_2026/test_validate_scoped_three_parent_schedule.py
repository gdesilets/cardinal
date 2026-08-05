from __future__ import annotations

import copy
import hashlib
import json
import unittest
from datetime import date, timedelta
from pathlib import Path

from validate_scoped_three_parent_schedule import validate_schedule


ROOT = Path(__file__).resolve().parent
SCHEDULE_PATH = ROOT / "working" / "scoped_three_parent_collection_schedule.json"


class ScopedThreeParentScheduleValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))

    def errors_for(self, mutated: dict) -> list[str]:
        return validate_schedule(ROOT, schedule_data=mutated)

    def test_baseline_schedule_is_valid(self) -> None:
        self.assertEqual([], self.errors_for(copy.deepcopy(self.baseline)))

    def test_rejects_unauthorized_container(self) -> None:
        mutated = copy.deepcopy(self.baseline)
        mutated["routes"]["student_breakdowns"][0]["channel_id"] = "999999999999999999"
        errors = self.errors_for(mutated)
        self.assertTrue(any("unauthorized container" in error for error in errors), errors)

    def test_rejects_route_overlap_and_gap(self) -> None:
        overlap = copy.deepcopy(self.baseline)
        first = overlap["routes"]["student_breakdowns"][0]
        overlap["routes"]["student_breakdowns"][1]["start"] = first["end"]
        overlap_errors = self.errors_for(overlap)
        self.assertTrue(any("route overlap" in error for error in overlap_errors), overlap_errors)

        gap = copy.deepcopy(self.baseline)
        gap["routes"]["student_breakdowns"][1]["start"] = "2026-01-16"
        gap_errors = self.errors_for(gap)
        self.assertTrue(any("route gap" in error for error in gap_errors), gap_errors)

    def test_rejects_wrong_query_name_and_channel_id(self) -> None:
        wrong_query = copy.deepcopy(self.baseline)
        wrong_query["routes"]["questions"][0]["query_prefix"] = "in:not-questions"
        errors = self.errors_for(wrong_query)
        self.assertTrue(any("wrong query name" in error for error in errors), errors)

        wrong_id = copy.deepcopy(self.baseline)
        wrong_id["routes"]["questions"][0]["channel_id"] = "1370578463223975986"
        errors = self.errors_for(wrong_id)
        self.assertTrue(any("wrong channel ID" in error for error in errors), errors)

    def test_rejects_normalized_questions_aliases_and_wrong_visible_parent(self) -> None:
        for normalized_prefix in ("in:questions", "in:live"):
            with self.subTest(normalized_prefix=normalized_prefix):
                mutated = copy.deepcopy(self.baseline)
                route = mutated["routes"]["questions"][0]
                route["query_prefix"] = normalized_prefix
                route["query"] = (
                    f"{normalized_prefix} after:2025-12-31 before:2026-01-02"
                )
                errors = self.errors_for(mutated)
                self.assertTrue(any("wrong query name" in error for error in errors), errors)

        wrong_route_parent = copy.deepcopy(self.baseline)
        wrong_route_parent["routes"]["questions"][0]["channel_name"] = "\U0001f4cd\u2502chat"
        errors = self.errors_for(wrong_route_parent)
        self.assertTrue(any("wrong query/channel name" in error for error in errors), errors)

        wrong_parent = copy.deepcopy(self.baseline)
        questions_parent = next(
            item
            for item in wrong_parent["parents"]
            if item["channel_id"] == "1273692573898113076"
        )
        questions_parent["name"] = "\U0001f4cd\u2502chat"
        errors = self.errors_for(wrong_parent)
        self.assertTrue(any("Questions parent exact visible name mismatch" in error for error in errors), errors)

        wrong_category = copy.deepcopy(self.baseline)
        wrong_category["routes"]["questions"][0]["visible_parent_category"] = "FREEMIUM"
        errors = self.errors_for(wrong_category)
        self.assertTrue(any("visible parent/category mismatch" in error for error in errors), errors)

    def test_questions_partition_is_five_daily_then_28_weekly(self) -> None:
        routes = self.baseline["routes"]["questions"]
        self.assertEqual(33, len(routes))
        self.assertEqual(
            [(f"2026-01-0{day}", f"2026-01-0{day}") for day in range(1, 6)],
            [(route["start"], route["end"]) for route in routes[:5]],
        )
        weekly = routes[5:]
        self.assertEqual(28, len(weekly))
        self.assertEqual(("2026-01-06", "2026-01-12"), (weekly[0]["start"], weekly[0]["end"]))
        self.assertEqual(("2026-07-14", "2026-07-20"), (weekly[-1]["start"], weekly[-1]["end"]))
        self.assertTrue(
            all(
                (date.fromisoformat(route["end"]) - date.fromisoformat(route["start"]))
                == timedelta(days=6)
                for route in weekly
            )
        )

        malformed = copy.deepcopy(self.baseline)
        malformed["routes"]["questions"][5]["start"] = "2026-01-07"
        errors = self.errors_for(malformed)
        self.assertTrue(any("Questions route shape mismatch" in error for error in errors), errors)
        self.assertTrue(any("route gap" in error for error in errors), errors)

        wrong_query_dates = copy.deepcopy(self.baseline)
        wrong_query_dates["routes"]["questions"][5]["query"] = (
            "in:\u2753\u2502questions after:2026-01-05 before:2026-01-12"
        )
        errors = self.errors_for(wrong_query_dates)
        self.assertTrue(any("wrong exact query" in error for error in errors), errors)

    def test_questions_runtime_checkpoint_and_resume_guards_are_required(self) -> None:
        route_options = copy.deepcopy(self.baseline)
        route_options["routes"]["questions"][5]["runtime_options"]["checkpointEvery"] = 6
        errors = self.errors_for(route_options)
        self.assertTrue(any("Questions runtime options mismatch" in error for error in errors), errors)

        resume_policy = copy.deepcopy(self.baseline)
        resume_policy["execution_policy"]["questions_resume_policy"][
            "reuse_active_search_without_new_submission"
        ] = False
        errors = self.errors_for(resume_policy)
        self.assertTrue(any("checkpoint/resume policy mismatch" in error for error in errors), errors)

        missing_stop = copy.deepcopy(self.baseline)
        missing_stop["execution_policy"]["stop_on_anomaly"].remove("search_count_drift")
        errors = self.errors_for(missing_stop)
        self.assertTrue(any("required stop-on-anomaly" in error for error in errors), errors)

        changed_granularity = copy.deepcopy(self.baseline)
        changed_granularity["routes"]["questions"][5]["message_granularity"] = "daily_summary"
        errors = self.errors_for(changed_granularity)
        self.assertTrue(any("message granularity changed" in error for error in errors), errors)

    def test_questions_promoted_canonicals_are_strictly_hash_bound(self) -> None:
        routes = self.baseline["routes"]["questions"]
        expected_accepted = [
            route for route in routes if (ROOT / route["expected_canonical_path"]).is_file()
        ]
        accepted_statuses = {
            "complete_accepted_v2_5",
            "complete_accepted_v2_6_v3_post_capture_exception",
        }
        actual_accepted = [
            route for route in routes if route["status"] in accepted_statuses
        ]
        self.assertEqual(
            [route["route_id"] for route in expected_accepted],
            [route["route_id"] for route in actual_accepted],
        )
        expected_total = 0
        for route in actual_accepted:
            path = ROOT / route["expected_canonical_path"]
            payload = json.loads(path.read_text(encoding="utf-8"))
            expected_total += payload["reported_total"]
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                route["accepted_artifact"]["sha256"],
            )
            self.assertTrue(route["accepted_artifact"]["full_qa_passed"])
        questions_parent = next(
            parent
            for parent in self.baseline["parents"]
            if parent["channel_id"] == "1273692573898113076"
        )
        self.assertEqual(len(actual_accepted), questions_parent["accepted_route_count"])
        self.assertEqual(33 - len(actual_accepted), questions_parent["pending_route_count"])
        self.assertEqual(expected_total, questions_parent["accepted_reported_total"])
        self.assertEqual(33, questions_parent["accepted_route_count"])
        self.assertEqual(0, questions_parent["pending_route_count"])
        self.assertEqual(39761, questions_parent["accepted_reported_total"])
        final_route = next(route for route in routes if route["route_id"] == "questions_2026-07-14_2026-07-20")
        self.assertEqual("complete_accepted_v2_6_v3_post_capture_exception", final_route["status"])
        self.assertNotIn(
            final_route["expected_canonical_path"],
            {artifact["path"] for artifact in self.baseline["preservation_only_artifacts"]},
        )

        wrong_hash = copy.deepcopy(self.baseline)
        wrong_hash["routes"]["questions"][4]["accepted_artifact"]["sha256"] = "0" * 64
        errors = self.errors_for(wrong_hash)
        self.assertTrue(any("canonical hash mismatch" in error for error in errors), errors)

        invented_acceptance = copy.deepcopy(self.baseline)
        first_pending = invented_acceptance["routes"]["questions"][-1]
        first_pending["expected_canonical_path"] = (
            "raw/channel_segments/"
            "channel_questions_1273692573898113076_2099-01-01_2099-01-01.json"
        )
        first_pending["status"] = "complete_accepted_v2_5"
        errors = self.errors_for(invented_acceptance)
        self.assertTrue(
            any("route without a canonical is not pending" in error for error in errors), errors
        )

        weakened_policy = copy.deepcopy(self.baseline)
        weakened_policy["questions_acceptance_policy"]["message_level_full_qa_required"] = False
        errors = self.errors_for(weakened_policy)
        self.assertTrue(any("strict content-bound acceptance policy" in error for error in errors), errors)

    def test_questions_timestamp_sidecar_and_recovery_hashes_are_bound(self) -> None:
        may_route = next(
            route
            for route in self.baseline["routes"]["questions"]
            if route["start"] == "2026-05-05"
        )
        roles = {
            row["role"]: row
            for row in may_route["accepted_artifact"]["source_files"]
        }
        self.assertEqual(
            {
                "canonical_segment",
                "timestamp_scope_revalidation_sidecar",
                "timestamp_scope_recovery_dom_evidence",
            },
            set(roles),
        )
        self.assertEqual(
            may_route["accepted_artifact"]["timestamp_scope_integrity"]["sidecar"][
                "sidecar_sha256"
            ],
            roles["timestamp_scope_revalidation_sidecar"]["sha256"],
        )

        mutated = copy.deepcopy(self.baseline)
        mutated_route = next(
            route
            for route in mutated["routes"]["questions"]
            if route["start"] == "2026-05-05"
        )
        next(
            row
            for row in mutated_route["accepted_artifact"]["source_files"]
            if row["role"] == "timestamp_scope_revalidation_sidecar"
        )["sha256"] = "0" * 64
        errors = self.errors_for(mutated)
        self.assertTrue(
            any("bound source-file set mismatch" in error for error in errors),
            errors,
        )

    def test_questions_executed_command_provenance_is_rederived(self) -> None:
        route = next(
            route
            for route in self.baseline["routes"]["questions"]
            if route["start"] == "2026-06-30"
        )
        audit = route["accepted_artifact"][
            "executed_command_reply_provenance_integrity"
        ]
        self.assertTrue(audit["passed"])
        self.assertEqual(1, audit["candidate_count"])
        self.assertEqual(["1523613360099295304"], audit["candidate_message_ids"])

        mutated = copy.deepcopy(self.baseline)
        mutated_route = next(
            row
            for row in mutated["routes"]["questions"]
            if row["start"] == "2026-06-30"
        )
        mutated_route["accepted_artifact"][
            "executed_command_reply_provenance_integrity"
        ]["candidate_message_ids"] = ["1523613360099295305"]
        errors = self.errors_for(mutated)
        self.assertTrue(
            any("executed-command reply provenance summary mismatch" in error for error in errors),
            errors,
        )

    def test_new_valid_weekly_canonical_updates_counts_without_source_edits(self) -> None:
        routes = self.baseline["routes"]["questions"]
        promoted = routes[6]
        self.assertEqual(("2026-01-13", "2026-01-19"), (promoted["start"], promoted["end"]))
        self.assertEqual("complete_accepted_v2_5", promoted["status"])
        self.assertEqual(2092, promoted["accepted_artifact"]["reported_total"])

        stale = copy.deepcopy(self.baseline)
        source_bindings = copy.deepcopy(stale["source_bindings"])
        questions_parent = next(
            parent
            for parent in stale["parents"]
            if parent["channel_id"] == "1273692573898113076"
        )
        questions_parent["accepted_route_count"] = 6
        questions_parent["pending_route_count"] = 27
        questions_parent["accepted_reported_total"] = 2360
        stale_coverage = stale["coverage_assertions"]["questions"]
        stale_coverage["accepted_route_count"] = 6
        stale_coverage["pending_route_count"] = 27
        stale_coverage["accepted_reported_total"] = 2360
        self.assertEqual(source_bindings, stale["source_bindings"])
        errors = self.errors_for(stale)
        self.assertTrue(
            any("independently derived canonical state" in error for error in errors), errors
        )

    def test_questions_rejects_malformed_partial_and_unplanned_acceptance(self) -> None:
        malformed = copy.deepcopy(self.baseline)
        malformed["routes"]["questions"][6]["accepted_artifact"]["full_qa_passed"] = False
        errors = self.errors_for(malformed)
        self.assertTrue(any("no longer passes exact v2.5 QA" in error for error in errors), errors)

        partial = copy.deepcopy(self.baseline)
        partial["routes"]["questions"][4]["accepted_artifact"]["path"] = (
            "raw/channel_segments/"
            "channel_questions_1273692573898113076_2026-01-05_2026-01-05.partial.json"
        )
        errors = self.errors_for(partial)
        self.assertTrue(any("partial artifact accepted" in error for error in errors), errors)

        unplanned = copy.deepcopy(self.baseline)
        unplanned["routes"]["questions"][0]["expected_canonical_path"] = (
            "raw/channel_segments/"
            "channel_questions_1273692573898113076_2026-01-01_2026-01-02.json"
        )
        errors = self.errors_for(unplanned)
        self.assertTrue(any("unplanned Questions canonical path" in error for error in errors), errors)

    def test_preexisting_jan5_partial_remains_preservation_only(self) -> None:
        expected_path = (
            "raw/channel_segments/"
            "channel_questions_1273692573898113076_2026-01-05_2026-01-05.partial.json"
        )
        matches = [
            artifact
            for artifact in self.baseline["preservation_only_artifacts"]
            if artifact["path"] == expected_path
        ]
        self.assertEqual(1, len(matches))
        self.assertFalse(matches[0]["accepted_for_scoped_release"])
        self.assertEqual(
            "12eb3d1252121d7f96386af3484ead60ea0cbad1ce79dece5ec39255ee738bb3",
            matches[0]["sha256"],
        )

        removed = copy.deepcopy(self.baseline)
        removed["preservation_only_artifacts"] = [
            artifact
            for artifact in removed["preservation_only_artifacts"]
            if artifact["path"] != expected_path
        ]
        errors = self.errors_for(removed)
        self.assertTrue(any("Jan 5 Questions partial is not preserved" in error for error in errors), errors)

    def test_rejects_inherited_obsolete_forum_closure(self) -> None:
        mutated = copy.deepcopy(self.baseline)
        mutated["premium_thread_census"]["status"] = "complete"
        mutated["premium_thread_census"]["inventory_complete"] = True
        mutated["premium_thread_census"]["closure_proven"] = True
        errors = self.errors_for(mutated)
        self.assertTrue(
            any(
                "inventory census was incorrectly declared complete" in error
                or "closure was declared before all closure gates passed" in error
                for error in errors
            ),
            errors,
        )
        self.assertTrue(
            any("census does not match independently rederived" in error for error in errors),
            errors,
        )

    def test_rejects_mutable_source_hash_mismatch(self) -> None:
        mutated = copy.deepcopy(self.baseline)
        mutated["source_bindings"]["authorized_collection_scope"]["sha256"] = "0" * 64
        errors = self.errors_for(mutated)
        self.assertTrue(any("mutable source hash mismatch" in error for error in errors), errors)

    def test_requires_all_student_routes_accepted_and_total_294(self) -> None:
        mutated = copy.deepcopy(self.baseline)
        mutated["routes"]["student_breakdowns"][-1]["status"] = "pending_fresh_v2_5_capture"
        mutated["parents"][0]["accepted_route_count"] = 14
        mutated["parents"][0]["pending_route_count"] = 1
        errors = self.errors_for(mutated)
        self.assertTrue(any("not 15/15 accepted" in error for error in errors), errors)
        self.assertTrue(any("Student route is not accepted" in error for error in errors), errors)

        wrong_total = copy.deepcopy(self.baseline)
        wrong_total["routes"]["student_breakdowns"][0]["accepted_artifact"][
            "reported_total"
        ] -= 1
        errors = self.errors_for(wrong_total)
        self.assertTrue(any("expected 294" in error for error in errors), errors)

    def test_requires_exact_student_full_window_reconciliation(self) -> None:
        mutated = copy.deepcopy(self.baseline)
        mutated["student_breakdowns_reconciliation"]["status"] = "pending"
        mutated["coverage_assertions"]["student_breakdowns"][
            "full_window_reconciled"
        ] = False
        errors = self.errors_for(mutated)
        self.assertTrue(
            any("Student reconciliation record status mismatch" in error for error in errors),
            errors,
        )
        self.assertTrue(
            any("Student coverage assertion full_window_reconciled mismatch" in error for error in errors),
            errors,
        )

        wrong_hash = copy.deepcopy(self.baseline)
        wrong_hash["source_bindings"]["student_breakdowns_full_window_reconciliation"][
            "sha256"
        ] = "0" * 64
        errors = self.errors_for(wrong_hash)
        self.assertTrue(any("mutable source hash mismatch" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
