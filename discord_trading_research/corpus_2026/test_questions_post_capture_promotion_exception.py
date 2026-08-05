from __future__ import annotations
import copy, json, unittest
from pathlib import Path
import questions_post_capture_promotion_exception as gate

class PostCaptureExceptionTests(unittest.TestCase):
    def test_preserved_stage_is_valid_against_historical_v3_binding(self):
        self.assertEqual(
            [],
            gate.validate_exception(
                require_canonical_absent=False, require_v3_current_schedule=False
            ),
        )

    def _write_exception(self, data):
        path = gate.ROOT / "working/_test_exception.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    @property
    def canonical(self):
        return gate.ROOT / gate.EXCEPTION_TARGET_RELATIVE_PATH

    def test_tampered_sidecar_binding_fails(self):
        data=json.loads(gate.EXCEPTION_PATH.read_text(encoding="utf-8")); data["execution_sidecar"]["sha256"]="0"*64
        self.assertIn("execution_sidecar_sha256_mismatch",gate.validate_exception(exception_path=self._write_exception(data)))

    def test_missing_sidecar_fails(self):
        data=json.loads(gate.EXCEPTION_PATH.read_text(encoding="utf-8")); data["execution_sidecar"]["path"]="working/no-such-sidecar.json"
        self.assertIn("execution_sidecar_file_missing",gate.validate_exception(exception_path=self._write_exception(data)))

    def test_tampered_sidecar_content_fails(self):
        candidate=json.loads((gate.ROOT/"working/questions_2026-07-14_2026-07-20_drift_recovery/restart_001/channel_questions_1273692573898113076_2026-07-14_2026-07-20.json").read_text(encoding="utf-8"))
        sidecar=json.loads((gate.ROOT/"working/questions_2026-07-14_2026-07-20_post_capture_execution_sidecar.json").read_text(encoding="utf-8"))
        sidecar["execution_summary"]["actual_search_submission_count"] = 2
        self.assertIn("sidecar_execution_summary_mismatch",gate._candidate_errors(candidate,sidecar))

    def test_candidate_binding_sha_drift_fails(self):
        data=json.loads(gate.EXCEPTION_PATH.read_text(encoding="utf-8")); data["candidate"]["sha256"]="0"*64
        self.assertIn("candidate_sha256_mismatch",gate.validate_exception(exception_path=self._write_exception(data)))

    def test_historical_mode_requires_exact_promoted_canonical(self):
        stage=gate.ROOT/"working/questions_2026-07-14_2026-07-20_drift_recovery/restart_001/channel_questions_1273692573898113076_2026-07-14_2026-07-20.json"
        self.assertEqual(
            ["historical_v3_schedule_mode_requires_exact_canonical_target"],
            gate.validate_promotable_copy(stage, require_v3_current_schedule=False),
        )

    def test_historical_mode_rejects_candidate_exception_and_schedule_drift(self):
        candidate_drift=json.loads(gate.EXCEPTION_PATH.read_text(encoding="utf-8")); candidate_drift["candidate"]["sha256"]="0"*64
        errors=gate.validate_promotable_copy(self.canonical, require_v3_current_schedule=False, exception_path=self._write_exception(candidate_drift))
        self.assertIn("candidate_sha256_mismatch", errors)

        schedule_drift=json.loads(gate.EXCEPTION_PATH.read_text(encoding="utf-8")); schedule_drift["bound_schedule"]["sha256"]="0"*64
        errors=gate.validate_promotable_copy(self.canonical, require_v3_current_schedule=False, exception_path=self._write_exception(schedule_drift))
        self.assertIn("historical_schedule_binding_differs_from_v3", errors)

    def test_candidate_timezone_version_and_route_drift_fail(self):
        candidate=json.loads((gate.ROOT/"working/questions_2026-07-14_2026-07-20_drift_recovery/restart_001/channel_questions_1273692573898113076_2026-07-14_2026-07-20.json").read_text(encoding="utf-8"))
        sidecar=json.loads((gate.ROOT/"working/questions_2026-07-14_2026-07-20_post_capture_execution_sidecar.json").read_text(encoding="utf-8"))
        mutated=copy.deepcopy(candidate); mutated["segment"].pop("timezone")
        self.assertIn("candidate_segment_not_exact_allowed_timezone_shape",gate._candidate_errors(mutated,sidecar))
        mutated=copy.deepcopy(candidate); mutated["collector_version"]="2.5"
        self.assertIn("candidate_collector_version_mismatch",gate._candidate_errors(mutated,sidecar))
        mutated=copy.deepcopy(candidate); mutated["requested_container"]["channel_id"]="0"
        self.assertIn("candidate_requested_channel_mismatch",gate._candidate_errors(mutated,sidecar))

    def test_promoted_canonical_is_promotable_in_historical_mode(self):
        self.assertEqual(
            [],
            gate.validate_promotable_copy(
                self.canonical, require_v3_current_schedule=False
            ),
        )

if __name__ == "__main__": unittest.main()
