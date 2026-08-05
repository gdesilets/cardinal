from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import premium_journals_v2_7_authority_migration_v1 as migration
from qa import validate_premium_journals_v2_7_authority_migration_v1 as generic_qa


ROOT = Path(__file__).resolve().parent


def refingerprint(candidate: dict) -> dict:
    candidate.pop("record_fingerprint_sha256", None)
    candidate["record_fingerprint_sha256"] = migration.sha256_json(candidate)
    return candidate


def review_report_binding() -> dict:
    path = ROOT / migration.READINESS_RELATIVE_PATH
    return {"path": migration.READINESS_RELATIVE_PATH, "sha256": migration.sha256_file(path), "bytes": path.stat().st_size}


def valid_receipt(candidate: dict) -> dict:
    return {
        "schema_version": migration.SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_activation_receipt",
        "migration_id": migration.MIGRATION_ID,
        "candidate_fingerprint_sha256": candidate["record_fingerprint_sha256"],
        "action": "activate",
        "status": "approved_for_atomic_activation",
        "approved_at_utc": "2026-07-22T09:00:00Z",
        "reviewer": "independent-auditor",
        "independent_audit": {
            "passed": True,
            "report": review_report_binding(),
        },
        "immutable": True,
    }


def valid_rollback_receipt(candidate: dict) -> dict:
    return {
        "schema_version": migration.SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_rollback_receipt",
        "migration_id": migration.MIGRATION_ID,
        "candidate_fingerprint_sha256": candidate["record_fingerprint_sha256"],
        "action": "rollback",
        "status": "approved_for_atomic_rollback",
        "approved_at_utc": "2026-07-22T10:00:00Z",
        "reviewer": "independent-rollback-reviewer",
        "rollback_review": {
            "passed": True,
            "report": review_report_binding(),
        },
        "v2_7_canonical_quarantined": True,
        "immutable": True,
    }


class CandidateBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.candidate = migration.build_candidate(ROOT)

    def clone(self) -> dict:
        return copy.deepcopy(self.candidate)

    def test_candidate_is_disabled_and_nonpromotable(self) -> None:
        controls = self.candidate["activation_controls"]
        self.assertTrue(self.candidate["immutable"])
        self.assertEqual(migration.STATUS, self.candidate["status"])
        self.assertTrue(all(value is False for value in controls.values()))

    def test_candidate_validation_passes(self) -> None:
        self.assertEqual([], migration.validate_candidate(self.candidate, ROOT))

    def test_record_fingerprint_is_exact(self) -> None:
        unsigned = self.clone()
        fingerprint = unsigned.pop("record_fingerprint_sha256")
        self.assertEqual(fingerprint, migration.sha256_json(unsigned))

    def test_fingerprint_detects_tampering(self) -> None:
        candidate = self.clone()
        candidate["status"] = "active"
        errors = migration.validate_candidate(candidate, ROOT)
        self.assertIn("candidate_fingerprint_mismatch", errors)

    def test_refingerprinted_status_tamper_still_fails(self) -> None:
        candidate = self.clone()
        candidate["status"] = "active"
        refingerprint(candidate)
        self.assertIn("candidate_status_mismatch", migration.validate_candidate(candidate, ROOT))

    def test_each_control_must_remain_false(self) -> None:
        for field in self.candidate["activation_controls"]:
            with self.subTest(field=field):
                candidate = self.clone()
                candidate["activation_controls"][field] = True
                refingerprint(candidate)
                self.assertIn("candidate_disabled_controls_invalid", migration.validate_candidate(candidate, ROOT))

    def test_source_file_set_is_exact_and_byte_bound(self) -> None:
        sources = self.candidate["source_bindings"]
        self.assertEqual(sources["source_file_set_sha256"], migration.sha256_json(sources["source_files"]))
        self.assertEqual(
            {migration.READINESS_RELATIVE_PATH, migration.SCHEDULE_RELATIVE_PATH, migration.JAN8_FULL_RELATIVE_PATH, migration.JAN8_STAGE_RELATIVE_PATH, migration.JAN8_AUTHORITY_RELATIVE_PATH, *migration.IMPLEMENTATION_PATHS},
            {item["path"] for item in sources["source_files"]},
        )

    def test_refingerprinted_source_hash_tamper_fails(self) -> None:
        candidate = self.clone()
        candidate["source_bindings"]["source_files"][0]["sha256"] = "0" * 64
        candidate["source_bindings"]["source_file_set_sha256"] = migration.sha256_json(candidate["source_bindings"]["source_files"])
        refingerprint(candidate)
        errors = migration.validate_candidate(candidate, ROOT)
        self.assertTrue(any(error.endswith("sha256_mismatch") for error in errors))

    def test_path_escape_is_rejected(self) -> None:
        candidate = self.clone()
        candidate["source_bindings"]["source_files"][0]["path"] = "../../outside.json"
        candidate["source_bindings"]["source_file_set_sha256"] = migration.sha256_json(candidate["source_bindings"]["source_files"])
        refingerprint(candidate)
        self.assertTrue(any("path_invalid" in error for error in migration.validate_candidate(candidate, ROOT)))

    def test_baseline_exact_totals_and_per_day_hashes(self) -> None:
        baseline = self.candidate["baseline"]
        self.assertEqual((585, 242, 343, 41.37), tuple(baseline[key] for key in ("header_navigation_groups", "strict_direct_consensus_groups", "required_header_fallback_groups", "estimated_header_navigation_savings_percent")))
        self.assertEqual([item[0] for item in migration.BASELINE], [item["day"] for item in baseline["per_day"]])

    def test_refingerprinted_baseline_tamper_fails(self) -> None:
        candidate = self.clone()
        candidate["baseline"]["strict_direct_consensus_groups"] += 1
        refingerprint(candidate)
        self.assertIn("candidate_baseline_totals_invalid", migration.validate_candidate(candidate, ROOT))

    def test_jan8_exact_shadow_totals(self) -> None:
        shadow = self.candidate["jan8_shadow_verification"]
        self.assertEqual((162, 7, 78, 36, 42, 78), tuple(shadow[key] for key in ("reported_total", "reported_pages", "v2_6_control_groups", "v2_7_direct_groups", "v2_7_header_fallback_groups", "all_resolution_child_matches")))
        self.assertEqual(7, len(shadow["page_reports"]))

    def test_refingerprinted_page_count_tamper_fails(self) -> None:
        candidate = self.clone()
        candidate["jan8_shadow_verification"]["page_reports"][4]["fallback"] = 12
        refingerprint(candidate)
        self.assertIn("candidate_jan8_page_summary_invalid:5", migration.validate_candidate(candidate, ROOT))

    def test_jan8_promoted_authority_is_exact(self) -> None:
        gate = self.candidate["jan8_authority_promotion_gate"]
        path = ROOT / gate["expected_authoritative_path"]
        self.assertTrue(path.is_file())
        self.assertEqual(gate["required_sha256"], migration.sha256_file(path))
        self.assertEqual(gate["required_bytes"], path.stat().st_size)

    def test_activation_filesystem_preconditions_pass_after_jan8_promotion(self) -> None:
        self.assertEqual([], migration.validate_candidate(self.candidate, ROOT, require_activation_preconditions=True))

    def test_current_v26_jan9_route_is_exact(self) -> None:
        current = self.candidate["current_v2_6_authority"]
        self.assertEqual(migration._expected_v26_route(), current["route"])
        self.assertEqual(migration.sha256_json(current["route"]), current["route_sha256"])

    def test_proposed_route_uses_nonoverlapping_versioned_paths(self) -> None:
        route = self.candidate["proposed_v2_7_authority"]["route"]
        self.assertEqual(migration.DAY, route["start"])
        self.assertEqual(migration.DAY, route["end"])
        self.assertTrue(route["expected_canonical_path"].startswith("raw/channel_segments_v2_7/"))
        self.assertEqual("raw/premium_journals_v2_7_checkpoints/2026-01-09", route["expected_checkpoint_directory"])

    def test_refingerprinted_route_path_tamper_fails(self) -> None:
        candidate = self.clone()
        candidate["proposed_v2_7_authority"]["route"]["expected_canonical_path"] = "raw/channel_segments_v2_5/wrong.json"
        refingerprint(candidate)
        self.assertIn("candidate_proposed_v2_7_authority_invalid", migration.validate_candidate(candidate, ROOT))

    def test_retirement_is_atomic_and_non_destructive(self) -> None:
        retirement = self.candidate["v2_6_route_retirement"]
        self.assertTrue(retirement["allowed_only_inside_same_atomic_activation_transaction"])
        self.assertTrue(retirement["retirement_before_activation_forbidden"])
        self.assertFalse(retirement["delete_route_or_artifacts"])

    def test_rollback_is_separate_and_no_double_authority(self) -> None:
        rollback = self.candidate["no_double_authority_and_rollback"]
        self.assertTrue(rollback["rollback_requires_separate_immutable_reviewed_receipt"])
        self.assertTrue(rollback["rollback_requires_v2_7_canonical_quarantine_before_commit"])
        self.assertTrue(rollback["simultaneous_v2_6_and_v2_7_authority_forbidden"])

    def test_before_authority_state_passes(self) -> None:
        schedule = json.loads((ROOT / migration.SCHEDULE_RELATIVE_PATH).read_text(encoding="utf-8"))
        self.assertEqual([], migration.validate_authority_state(schedule, self.candidate, "before"))


class ReceiptAndProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.candidate = migration.build_candidate(ROOT)
        cls.schedule = json.loads((ROOT / migration.SCHEDULE_RELATIVE_PATH).read_text(encoding="utf-8"))

    def test_valid_activation_receipt_contract(self) -> None:
        self.assertEqual([], migration.validate_activation_receipt(valid_receipt(self.candidate), self.candidate))

    def test_receipt_wrong_candidate_fails(self) -> None:
        receipt = valid_receipt(self.candidate)
        receipt["candidate_fingerprint_sha256"] = "0" * 64
        self.assertIn("activation_receipt_binding_invalid", migration.validate_activation_receipt(receipt, self.candidate))

    def test_receipt_requires_independent_pass(self) -> None:
        receipt = valid_receipt(self.candidate)
        receipt["independent_audit"]["passed"] = False
        self.assertIn("activation_receipt_independent_audit_invalid", migration.validate_activation_receipt(receipt, self.candidate))

    def test_receipt_requires_named_reviewer(self) -> None:
        receipt = valid_receipt(self.candidate)
        receipt["reviewer"] = ""
        self.assertIn("activation_receipt_approval_invalid", migration.validate_activation_receipt(receipt, self.candidate))

    def test_projection_is_pure_and_yields_one_authority(self) -> None:
        original = copy.deepcopy(self.schedule)
        receipt = valid_receipt(self.candidate)
        with mock.patch.object(migration, "validate_candidate", return_value=[]):
            projected, errors = migration.project_activation(self.schedule, self.candidate, receipt, ROOT)
        self.assertEqual([], errors)
        self.assertEqual(original, self.schedule)
        self.assertIsNotNone(projected)
        self.assertEqual([], migration.validate_authority_state(projected, self.candidate, "activated"))

    def test_projection_rejects_schedule_drift(self) -> None:
        schedule = copy.deepcopy(self.schedule)
        schedule["status"] = "tampered"
        receipt = valid_receipt(self.candidate)
        with mock.patch.object(migration, "validate_candidate", return_value=[]):
            projected, errors = migration.project_activation(schedule, self.candidate, receipt, ROOT)
        self.assertIsNone(projected)
        self.assertIn("activation_schedule_not_exact_frozen_snapshot", errors)

    def test_projection_rejects_missing_audit_report(self) -> None:
        receipt = valid_receipt(self.candidate)
        receipt["independent_audit"]["report"] = {"path": "working/does_not_exist.json", "sha256": "a" * 64, "bytes": 1}
        with mock.patch.object(migration, "validate_candidate", return_value=[]):
            projected, errors = migration.project_activation(self.schedule, self.candidate, receipt, ROOT)
        self.assertIsNone(projected)
        self.assertIn("activation_independent_audit_report_missing", errors)

    def test_projection_rejects_audit_report_hash_drift(self) -> None:
        receipt = valid_receipt(self.candidate)
        receipt["independent_audit"]["report"]["sha256"] = "0" * 64
        with mock.patch.object(migration, "validate_candidate", return_value=[]):
            projected, errors = migration.project_activation(self.schedule, self.candidate, receipt, ROOT)
        self.assertIsNone(projected)
        self.assertIn("activation_independent_audit_report_sha256_mismatch", errors)

    def test_double_authority_is_detected(self) -> None:
        schedule = copy.deepcopy(self.schedule)
        active = copy.deepcopy(self.candidate["proposed_v2_7_authority"]["route"])
        active["status"] = "active_v2_7_authority"
        schedule["premium_journals_v2_7_authoritative_routes"] = [active]
        errors = migration.validate_authority_state(schedule, self.candidate, "activated")
        self.assertIn("authority_state_double_authority", errors)

    def test_unknown_authority_state_fails(self) -> None:
        self.assertEqual(["authority_state_unknown"], migration.validate_authority_state(self.schedule, self.candidate, "mystery"))

    def test_valid_rollback_receipt_contract(self) -> None:
        self.assertEqual([], migration.validate_rollback_receipt(valid_rollback_receipt(self.candidate), self.candidate))

    def test_rollback_receipt_requires_quarantine_attestation(self) -> None:
        receipt = valid_rollback_receipt(self.candidate)
        receipt["v2_7_canonical_quarantined"] = False
        self.assertIn("rollback_receipt_binding_invalid", migration.validate_rollback_receipt(receipt, self.candidate))

    def test_projection_round_trip_restores_exact_v26_route(self) -> None:
        activation_receipt = valid_receipt(self.candidate)
        with mock.patch.object(migration, "validate_candidate", return_value=[]):
            activated, activation_errors = migration.project_activation(self.schedule, self.candidate, activation_receipt, ROOT)
        self.assertEqual([], activation_errors)
        rollback_receipt = valid_rollback_receipt(self.candidate)
        with mock.patch.object(migration, "validate_candidate", return_value=[]):
            rolled_back, rollback_errors = migration.project_rollback(activated, self.candidate, rollback_receipt, ROOT)
        self.assertEqual([], rollback_errors)
        self.assertEqual([], migration.validate_authority_state(rolled_back, self.candidate, "rollback"))
        jan9 = [route for route in rolled_back["routes"]["premium_journals"] if route.get("start") == migration.DAY]
        self.assertEqual([migration._expected_v26_route()], jan9)

    def test_rollback_does_not_accept_unretired_v26_route(self) -> None:
        receipt = valid_rollback_receipt(self.candidate)
        projected, errors = migration.project_rollback(self.schedule, self.candidate, receipt, ROOT)
        self.assertIsNone(projected)
        self.assertTrue(errors)

    def test_rollback_projection_rejects_missing_review_report(self) -> None:
        activation_receipt = valid_receipt(self.candidate)
        with mock.patch.object(migration, "validate_candidate", return_value=[]):
            activated, activation_errors = migration.project_activation(self.schedule, self.candidate, activation_receipt, ROOT)
        self.assertEqual([], activation_errors)
        receipt = valid_rollback_receipt(self.candidate)
        receipt["rollback_review"]["report"] = {"path": "working/does_not_exist.json", "sha256": "b" * 64, "bytes": 1}
        with mock.patch.object(migration, "validate_candidate", return_value=[]):
            projected, errors = migration.project_rollback(activated, self.candidate, receipt, ROOT)
        self.assertIsNone(projected)
        self.assertIn("rollback_review_report_missing", errors)


class GenericQaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.candidate = migration.build_candidate(ROOT)

    def test_candidate_file_qa_rejects_noncanonical_path(self) -> None:
        errors = generic_qa.validate_candidate_file(ROOT / migration.READINESS_RELATIVE_PATH, ROOT)
        self.assertTrue(errors)

    def test_audit_report_binding_rejects_missing_file(self) -> None:
        receipt = valid_receipt(self.candidate)
        receipt["independent_audit"]["report"] = {"path": "working/does_not_exist.json", "sha256": "a" * 64, "bytes": 1}
        self.assertEqual(["activation_audit_report_missing"], generic_qa._audit_report_binding_errors(receipt, ROOT))

    def test_activated_mode_requires_receipt_in_schedule(self) -> None:
        schedule = json.loads((ROOT / migration.SCHEDULE_RELATIVE_PATH).read_text(encoding="utf-8"))
        self.assertNotIn("premium_journals_authority_activation_receipts", schedule)


class ReadinessReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.candidate = migration.build_candidate(ROOT)

    def _candidate_file(self):
        handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", prefix="migration_candidate_test_", dir=ROOT / "working", delete=False)
        json.dump(self.candidate, handle, indent=2)
        handle.write("\n")
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return Path(handle.name)

    def test_readiness_report_builds_and_validates(self) -> None:
        report = migration.build_readiness_report(
            self._candidate_file(),
            {"focused_migration_tests_passed": 42, "full_python_discovery_passed": 352, "full_node_tests_passed": 63},
            ROOT,
        )
        self.assertEqual([], migration.validate_readiness_report(report, ROOT))
        self.assertTrue(all(value is False for value in report["activation_controls"].values()))

    def test_readiness_report_refingerprinted_status_tamper_fails(self) -> None:
        report = migration.build_readiness_report(
            self._candidate_file(),
            {"focused_migration_tests_passed": 42, "full_python_discovery_passed": 352, "full_node_tests_passed": 63},
            ROOT,
        )
        report["status"] = "active"
        report.pop("record_fingerprint_sha256")
        report["record_fingerprint_sha256"] = migration.sha256_json(report)
        self.assertIn("readiness_fixed_contract_mismatch", migration.validate_readiness_report(report, ROOT))

    def test_readiness_builder_rejects_zero_test_count(self) -> None:
        with self.assertRaises(ValueError):
            migration.build_readiness_report(
                self._candidate_file(),
                {"focused_migration_tests_passed": 0, "full_python_discovery_passed": 1, "full_node_tests_passed": 1},
                ROOT,
            )


if __name__ == "__main__":
    unittest.main()
