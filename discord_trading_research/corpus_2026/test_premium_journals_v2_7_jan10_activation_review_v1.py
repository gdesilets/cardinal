from __future__ import annotations

import copy
import json
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import premium_journals_v2_7_jan10_activation_review_v1 as review
from qa import validate_premium_journals_v2_7_jan10_activation_review_v1 as reader


LIVE_ROOT = Path(__file__).resolve().parent


def copy_file(source_root: Path, fixture_root: Path, relative: str, *, protected: bool = False) -> None:
    source = (
        review.resolve_source_path(source_root, relative)
        if protected
        else review.resolve_corpus_path(source_root, relative)
    )
    destination = (
        review.resolve_source_path(fixture_root, relative)
        if protected
        else review.resolve_corpus_path(fixture_root, relative)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def build_fixture() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name) / "corpus_2026-01-01_2026-07-20"
    root.mkdir(parents=True)
    for relative in review.PROTECTED_SOURCE_PATHS:
        copy_file(LIVE_ROOT, root, relative, protected=True)
    for relative in (
        review.SCHEDULE_PATH,
        review.QUERY_CHECKLIST_PATH,
        review.JAN9_CANONICAL_PATH,
        review.JAN9_FINAL_AUDIT_PATH,
        review.JAN9_POSTPROMOTION_AUDIT_PATH,
    ):
        copy_file(LIVE_ROOT, root, relative)
    source_archive = review.resolve_corpus_path(
        LIVE_ROOT, review.jan9_activation.SUPERSEDED_DRAFT_DIRECTORY
    )
    destination_archive = review.resolve_corpus_path(
        root, review.jan9_activation.SUPERSEDED_DRAFT_DIRECTORY
    )
    shutil.copytree(source_archive, destination_archive)
    return temporary, root


def rewrite_json(path: Path, mutator) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutator(value)
    path.write_bytes(review.json_bytes(value))


class Jan10ReviewPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary, self.root = build_fixture()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def publish(self) -> Path:
        return review.publish_review_package(self.root)

    def test_exact_target_query_timezone_and_source_route(self) -> None:
        snapshot = review.capture_snapshot(self.root)
        self.assertEqual(review.validate_input_snapshot(snapshot, self.root), [])
        plan = review.build_plan(snapshot)
        self.assertEqual(plan["target"]["day"], "2026-01-10")
        self.assertEqual(plan["target"]["timezone"], "America/Chicago")
        self.assertEqual(
            plan["target"]["exact_query"],
            "in:premium-journals after:2026-01-09 before:2026-01-11",
        )
        self.assertEqual(plan["target"]["source_schedule_route"], review.EXPECTED_JAN10_SCHEDULE_ROUTE)
        self.assertEqual(plan["target"]["source_schedule_route"]["status"], "pending_fresh_v2_6_capture")

    def test_plan_is_disabled_and_jan9_never_inherited(self) -> None:
        plan = review.build_plan(review.capture_snapshot(self.root))
        self.assertEqual(plan["authority_effect"], "none_review_evidence_only")
        self.assertFalse(plan["frozen_preconditions"]["historical_inputs_confer_jan10_authority"])
        self.assertFalse(plan["frozen_preconditions"]["jan9_authority_inherited"])
        self.assertTrue(all(value is False for value in plan["activation_controls"].values()))
        self.assertFalse(plan["target"]["proposed_v2_7_route"]["live_collection_enabled"])
        self.assertFalse(plan["target"]["proposed_v2_7_route"]["promotion_allowed"])

    def test_all_five_named_safety_gates_are_required(self) -> None:
        snapshot = review.capture_snapshot(self.root)
        plan_raw = review.json_bytes(review.build_plan(snapshot))
        audit = review.build_audit_bundle(snapshot, plan_raw)
        manifest = review.build_manifest(snapshot, plan_raw, review.json_bytes(audit))
        expected = {
            "exclusive_os_publication_lock",
            "crash_safe_immutable_no_clobber_publication",
            "non_authoritative_reader_state_machine",
            "exact_snapshot_recovery_and_tamper_fail_closed",
            "marker_aware_no_write_validation",
        }
        for record in (json.loads(plan_raw), audit, manifest):
            self.assertEqual(set(record["five_safety_gates"]), expected)
            self.assertTrue(all(record["five_safety_gates"].values()))

    def test_plan_binds_every_protected_source_and_historical_input(self) -> None:
        plan = review.build_plan(review.capture_snapshot(self.root))
        self.assertEqual(
            [item["path"] for item in plan["protected_source_bindings"]],
            sorted(review.PROTECTED_SOURCE_PATHS),
        )
        self.assertEqual(
            [item["path"] for item in plan["frozen_preconditions"]["historical_jan9_capability_inputs"]],
            [item[1] for item in review.HISTORICAL_INPUT_BINDINGS],
        )
        self.assertEqual(plan["frozen_preconditions"]["live_schedule"]["sha256"], review.SCHEDULE_SHA256)
        self.assertEqual(
            plan["frozen_preconditions"]["jan9_supersession_manifest"]["sha256"],
            review.JAN9_SUPERSESSION_MANIFEST_SHA256,
        )
        self.assertEqual(
            plan["frozen_preconditions"]["jan9_supersession_archive_lock"]["sha256"],
            review.JAN9_SUPERSESSION_ARCHIVE_LOCK_SHA256,
        )
        self.assertEqual(plan["frozen_preconditions"]["jan9_supersession_archive_lock"]["bytes"], 1)

    def test_jan9_accepted_v26_canonical_is_exactly_bound(self) -> None:
        plan = review.build_plan(review.capture_snapshot(self.root))
        binding = plan["frozen_preconditions"]["jan9_authoritative_canonical"]
        self.assertEqual(binding["path"], review.JAN9_CANONICAL_PATH)
        self.assertEqual(binding["sha256"], "399f0df8ef52878442542043c3d64c0a4cb8070bac5dd0b8df58fcebf2df87ae")
        self.assertEqual(binding["bytes"], 1786921)

    def test_checklist_is_hold_with_zero_query_authorization(self) -> None:
        snapshot = review.capture_snapshot(self.root)
        checklist = review._load_object(snapshot["inputs"]["query_checklist"], review.QUERY_CHECKLIST_PATH)
        self.assertEqual(review._validate_checklist(checklist), [])
        self.assertEqual(checklist["status"], "HOLD_PENDING_V2_7_INDEPENDENT_AUDIT_AND_ACTIVATION")
        self.assertEqual(checklist["submission_timing"]["query_submission_count_authorized_now"], 0)
        self.assertFalse(checklist["preparation_side_effects"]["query_submitted"])

    def test_target_absence_contract_covers_all_required_classes(self) -> None:
        paths = set(review.TARGET_ABSENCE_PATHS)
        self.assertIn(review.JAN10_V25_CANONICAL, paths)
        self.assertIn(review.JAN10_V27_CANONICAL, paths)
        self.assertIn(review.JAN10_LEGACY_CANONICAL, paths)
        self.assertIn(review.JAN10_V25_CANONICAL.removesuffix(".json") + ".partial.json", paths)
        self.assertIn(review._sidecar(review.JAN10_V27_CANONICAL), paths)
        self.assertIn(review.v27.expected_checkpoint_relative_directory(review.DAY), paths)
        self.assertIn("working/premium_journals_v2_7_jan10_authority_activation_commit_marker.json", paths)
        self.assertIn("working/.premium_journals_v2_7_jan10_authority_activation.lock", paths)
        self.assertIn(review.jan9_activation.COMMIT_MARKER_PATH, paths)
        self.assertIn(review.jan9_activation.PLAN_PATH, paths)
        self.assertIn(review.jan9_activation.PLAN_AUDIT_PATH, paths)
        self.assertIn(review.jan9_activation.PREIMAGE_PATH, paths)
        self.assertIn(review.jan9_activation.RECEIPT_PATH, paths)
        self.assertIn(review.jan9_activation.PROJECTION_BUNDLE_PATH, paths)
        self.assertIn(review.jan9_activation.ROLLBACK_RECEIPT_PATH, paths)
        self.assertIn(review.jan9_activation.LOCK_PATH, paths)
        self.assertIn(
            "raw/quarantine_collection_errors/terra_premium_journals_daily_2026-01-10_*",
            review.TARGET_ABSENCE_PATTERNS,
        )
        self.assertIn("working/**/*premium*v2_7*marker*", review.TARGET_ABSENCE_PATTERNS)
        self.assertIn("working/**/*v2_7*/**/*marker*", review.TARGET_ABSENCE_PATTERNS)

    def test_reader_pre_activation_is_explicitly_non_authoritative(self) -> None:
        state = reader.effective_authority(self.root)
        self.assertEqual(state["status"], "PRE_ACTIVATION")
        self.assertFalse(state["live"])
        self.assertFalse(state["activation_authorized"])
        self.assertIsNone(state["authorized_route"])
        self.assertEqual(state["effective_authority"], "none")

    def test_public_activation_marker_and_route_apis_are_hard_blocked(self) -> None:
        for call in (review.execute_activation, review.publish_commit_marker, review.resolve_live_route):
            with self.assertRaises(review.ReviewPackageError):
                call(self.root)
        with self.assertRaises(review.ReviewPackageError):
            reader.resolve_live_collection_route(self.root)

    def test_publication_is_complete_exact_and_does_not_change_schedule(self) -> None:
        schedule_before = review.resolve_corpus_path(self.root, review.SCHEDULE_PATH).read_bytes()
        manifest_path = self.publish()
        self.assertEqual(manifest_path, review.resolve_corpus_path(self.root, review.MANIFEST_PATH))
        self.assertEqual(review.classify_review_state(self.root)["status"], "REVIEW_PACKAGE_READY")
        self.assertEqual(review.validate_package_snapshot(review.capture_snapshot(self.root), self.root), [])
        self.assertEqual(review.resolve_corpus_path(self.root, review.SCHEDULE_PATH).read_bytes(), schedule_before)
        self.assertEqual(set(review._package_inventory(self.root)), set(review.PACKAGE_ARTIFACT_PATHS))
        self.assertFalse(review.resolve_corpus_path(self.root, review.INDEPENDENT_AUDIT_PATH).exists())
        self.assertTrue(all(review._describe_path(self.root, item) is None for item in review.TARGET_ABSENCE_PATHS))

    def test_exact_replay_is_idempotent(self) -> None:
        self.publish()
        before = {
            relative: review.resolve_corpus_path(self.root, relative).read_bytes()
            for relative in review.PACKAGE_ARTIFACT_PATHS
        }
        self.publish()
        after = {
            relative: review.resolve_corpus_path(self.root, relative).read_bytes()
            for relative in review.PACKAGE_ARTIFACT_PATHS
        }
        self.assertEqual(before, after)

    def test_two_concurrent_publishers_are_exact_and_safe(self) -> None:
        barrier = threading.Barrier(2)

        def run() -> str:
            barrier.wait()
            return str(review.publish_review_package(self.root))

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _item: run(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(review.classify_review_state(self.root)["status"], "REVIEW_PACKAGE_READY")

    def test_every_prepublication_crash_point_recovers_exactly(self) -> None:
        for stop_at in review.PACKAGE_PUBLICATION_ORDER:
            with self.subTest(stop_at=stop_at):
                temporary, root = build_fixture()
                try:
                    def stop(relative: str) -> None:
                        if relative == stop_at:
                            raise RuntimeError("simulated crash")

                    with self.assertRaises(RuntimeError):
                        review.publish_review_package(root, _before_artifact=stop)
                    state = review.classify_review_state(root)
                    expected = "PRE_ACTIVATION" if stop_at == review.PREIMAGE_PATH else "FAIL_CLOSED_RECOVERY_REQUIRED"
                    self.assertEqual(state["status"], expected)
                    review.publish_review_package(root)
                    self.assertEqual(review.classify_review_state(root)["status"], "REVIEW_PACKAGE_READY")
                finally:
                    temporary.cleanup()

    def test_exact_partial_prefix_requires_recovery(self) -> None:
        snapshot = review.capture_snapshot(self.root)
        expected = review.expected_package_bytes(snapshot)
        review._write_exclusive_or_exact(
            review.resolve_corpus_path(self.root, review.PREIMAGE_PATH), expected[review.PREIMAGE_PATH]
        )
        self.assertEqual(review.classify_review_state(self.root)["status"], "FAIL_CLOSED_RECOVERY_REQUIRED")

    def test_tampered_partial_prefix_fails_closed_and_cannot_publish(self) -> None:
        path = review.resolve_corpus_path(self.root, review.PREIMAGE_PATH)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"tampered")
        self.assertEqual(review.classify_review_state(self.root)["status"], "FAIL_CLOSED")
        with self.assertRaises(review.ReviewPackageError):
            self.publish()

    def test_wrong_schedule_fails_closed(self) -> None:
        path = review.resolve_corpus_path(self.root, review.SCHEDULE_PATH)
        path.write_bytes(path.read_bytes() + b" ")
        self.assertEqual(review.classify_review_state(self.root)["status"], "FAIL_CLOSED")
        with self.assertRaises(review.ReviewPackageError):
            self.publish()

    def test_missing_or_tampered_jan9_manifest_fails_closed(self) -> None:
        for mode in ("missing", "tampered"):
            with self.subTest(mode=mode):
                temporary, root = build_fixture()
                try:
                    path = review.resolve_corpus_path(root, review.JAN9_SUPERSESSION_MANIFEST_PATH)
                    if mode == "missing":
                        path.unlink()
                    else:
                        path.write_bytes(path.read_bytes() + b" ")
                    self.assertEqual(review.classify_review_state(root)["status"], "FAIL_CLOSED")
                    with self.assertRaises(review.ReviewPackageError):
                        review.publish_review_package(root)
                finally:
                    temporary.cleanup()

    def test_wrong_date_query_path_and_jan9_inheritance_tamper_fail(self) -> None:
        mutations = (
            lambda plan: plan["target"].__setitem__("day", "2026-01-11"),
            lambda plan: plan["target"].__setitem__("exact_query", "in:premium-journals after:2026-01-10 before:2026-01-12"),
            lambda plan: plan["target"]["proposed_v2_7_route"].__setitem__("expected_canonical_path", review.JAN10_V25_CANONICAL),
            lambda plan: plan["frozen_preconditions"].__setitem__("jan9_authority_inherited", True),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                temporary, root = build_fixture()
                try:
                    review.publish_review_package(root)
                    path = review.resolve_corpus_path(root, review.PLAN_PATH)
                    plan = json.loads(path.read_text(encoding="utf-8"))
                    mutate(plan)
                    plan.pop("record_fingerprint_sha256", None)
                    plan = review._finalize(plan)
                    path.write_bytes(review.json_bytes(plan))
                    self.assertEqual(review.classify_review_state(root)["status"], "FAIL_CLOSED")
                finally:
                    temporary.cleanup()

    def test_target_stage_partial_sidecar_checkpoint_and_marker_appearance_fail(self) -> None:
        representatives = (
            review.JAN10_V25_CANONICAL,
            review.JAN10_V27_CANONICAL.removesuffix(".json") + ".partial.json",
            review._sidecar(review.JAN10_LEGACY_CANONICAL),
            review.v27.expected_checkpoint_relative_directory(review.DAY),
            "working/premium_journals_v2_7_jan10_authority_activation_commit_marker.json",
            review.jan9_activation.COMMIT_MARKER_PATH,
            f"{review.PACKAGE_DIRECTORY}/commit_marker.json",
            f"{review.jan9_activation.SUPERSEDED_DRAFT_DIRECTORY}/commit_marker.json",
            "working/premium_journals_v2_7_unenumerated_authority_marker.backup",
            "raw/quarantine_collection_errors/terra_premium_journals_daily_2026-01-10_fixture/stage.json",
        )
        for relative in representatives:
            with self.subTest(relative=relative):
                temporary, root = build_fixture()
                try:
                    review.publish_review_package(root)
                    path = review.resolve_corpus_path(root, relative)
                    if relative == review.v27.expected_checkpoint_relative_directory(review.DAY):
                        path.mkdir(parents=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text("{}", encoding="utf-8")
                    self.assertEqual(review.classify_review_state(root)["status"], "FAIL_CLOSED")
                finally:
                    temporary.cleanup()

    def test_source_or_checklist_tamper_fails_closed(self) -> None:
        cases = (
            ("source", "premium_journals_provenance_contract_v2_7.py"),
            ("checklist", review.QUERY_CHECKLIST_PATH),
        )
        for kind, relative in cases:
            with self.subTest(kind=kind):
                temporary, root = build_fixture()
                try:
                    review.publish_review_package(root)
                    path = (
                        review.resolve_source_path(root, relative)
                        if kind == "source"
                        else review.resolve_corpus_path(root, relative)
                    )
                    path.write_bytes(path.read_bytes() + b"\n")
                    self.assertEqual(review.classify_review_state(root)["status"], "FAIL_CLOSED")
                finally:
                    temporary.cleanup()

    def test_writer_detects_schedule_change_before_manifest(self) -> None:
        schedule_path = review.resolve_corpus_path(self.root, review.SCHEDULE_PATH)
        original = schedule_path.read_bytes()

        def mutate(relative: str) -> None:
            if relative == review.MANIFEST_PATH:
                schedule_path.write_bytes(original + b" ")

        with self.assertRaises(review.ReviewPackageError):
            review.publish_review_package(self.root, _before_artifact=mutate)
        self.assertFalse(review.resolve_corpus_path(self.root, review.MANIFEST_PATH).exists())
        schedule_path.write_bytes(original)
        self.assertEqual(review.classify_review_state(self.root)["status"], "FAIL_CLOSED_RECOVERY_REQUIRED")
        self.publish()

    def test_reader_detects_toctou_snapshot_change(self) -> None:
        self.publish()
        schedule_path = review.resolve_corpus_path(self.root, review.SCHEDULE_PATH)
        original_raw = schedule_path.read_bytes()
        original_capture = review.capture_snapshot
        calls = 0

        def capture(root: Path):
            nonlocal calls
            calls += 1
            snapshot = original_capture(root)
            if calls == 1:
                schedule_path.write_bytes(original_raw + b" ")
            return snapshot

        try:
            with mock.patch.object(review, "capture_snapshot", side_effect=capture):
                state = review.classify_review_state(self.root)
            self.assertEqual(state["status"], "FAIL_CLOSED_SNAPSHOT_CHANGED")
            self.assertIn("protected_snapshot_changed_during_read", state["errors"])
        finally:
            schedule_path.write_bytes(original_raw)

    def test_reader_replay_is_lock_free_and_write_free(self) -> None:
        self.publish()
        files = [item for item in self.root.parent.rglob("*") if item.is_file()]
        before = {str(path): (path.stat().st_size, path.stat().st_mtime_ns, review.sha256_bytes(path.read_bytes())) for path in files}
        state = review.classify_review_state(self.root)
        self.assertEqual(state["status"], "REVIEW_PACKAGE_READY")
        after_files = [item for item in self.root.parent.rglob("*") if item.is_file()]
        after = {str(path): (path.stat().st_size, path.stat().st_mtime_ns, review.sha256_bytes(path.read_bytes())) for path in after_files}
        self.assertEqual(before, after)

    def test_missing_independent_audit_never_authorizes(self) -> None:
        self.publish()
        state = review.classify_review_state(self.root)
        self.assertEqual(state["status"], "REVIEW_PACKAGE_READY")
        self.assertFalse(state["independent_audit_passed"])
        self.assertFalse(state["activation_authorized"])
        with self.assertRaises(review.ReviewPackageError):
            review.require_independent_audit(self.root)

    def test_invalid_independent_audit_fails_closed(self) -> None:
        self.publish()
        path = review.resolve_corpus_path(self.root, review.INDEPENDENT_AUDIT_PATH)
        path.write_text("{}", encoding="utf-8")
        state = review.classify_review_state(self.root)
        self.assertEqual(state["status"], "FAIL_CLOSED")
        self.assertTrue(any("independent_audit" in item for item in state["errors"]))

    def test_valid_fixture_audit_still_has_no_authority(self) -> None:
        self.publish()
        snapshot = review.capture_snapshot(self.root)
        audit = review._build_independent_audit_fixture(snapshot)
        review._write_exclusive_or_exact(
            review.resolve_corpus_path(self.root, review.INDEPENDENT_AUDIT_PATH),
            review.json_bytes(audit),
        )
        state = reader.effective_authority(self.root)
        self.assertEqual(state["status"], "INDEPENDENT_AUDIT_PASSED_NO_AUTHORITY")
        self.assertTrue(state["independent_audit_passed"])
        self.assertFalse(state["activation_authorized"])
        self.assertFalse(state["live"])
        self.assertIsNone(state["route"])

    def test_independent_audit_requires_concrete_rederived_evidence(self) -> None:
        self.publish()
        baseline = review.capture_snapshot(self.root)
        mutations = (
            lambda audit: audit["test_results"]["generic_activation_recovery_python"].__setitem__("passed", 1),
            lambda audit: audit["read_only_replay"].__setitem__("unchanged", False),
            lambda audit: audit["schedule_validation"].__setitem__("status", "FAIL"),
            lambda audit: audit["absence_validation"].__setitem__("unexpected_matches", ["marker"]),
            lambda audit: audit["five_safety_gate_determinations"]["exclusive_os_publication_lock"].__setitem__("evidence", []),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                audit = review._build_independent_audit_fixture(baseline)
                mutate(audit)
                audit.pop("record_fingerprint_sha256", None)
                audit = review._finalize(audit)
                changed = copy.deepcopy(baseline)
                changed["package"][review.INDEPENDENT_AUDIT_PATH] = review.json_bytes(audit)
                changed["package_inventory"] = sorted(
                    set(changed["package_inventory"]) | {review.INDEPENDENT_AUDIT_PATH}
                )
                self.assertTrue(review.validate_independent_audit(changed))

    def test_manifest_tamper_and_unexpected_package_file_fail(self) -> None:
        for mode in ("manifest", "unexpected"):
            with self.subTest(mode=mode):
                temporary, root = build_fixture()
                try:
                    review.publish_review_package(root)
                    if mode == "manifest":
                        path = review.resolve_corpus_path(root, review.MANIFEST_PATH)
                        path.write_bytes(path.read_bytes() + b" ")
                    else:
                        path = review.resolve_corpus_path(root, f"{review.PACKAGE_DIRECTORY}/extra.json")
                        path.write_text("{}", encoding="utf-8")
                    self.assertEqual(review.classify_review_state(root)["status"], "FAIL_CLOSED")
                finally:
                    temporary.cleanup()

    def test_copied_package_wrong_root_and_path_escape_fail(self) -> None:
        self.publish()
        empty_temp = tempfile.TemporaryDirectory()
        try:
            wrong_root = Path(empty_temp.name) / "wrong"
            wrong_root.mkdir()
            source = review.resolve_corpus_path(self.root, review.PACKAGE_DIRECTORY)
            destination = review.resolve_corpus_path(wrong_root, review.PACKAGE_DIRECTORY)
            shutil.copytree(source, destination)
            self.assertEqual(review.classify_review_state(wrong_root)["status"], "FAIL_CLOSED")
        finally:
            empty_temp.cleanup()
        for relative in ("../escape.json", "../../escape.json", "/absolute/path.json", "working\\escape.json"):
            with self.assertRaises(review.ReviewPackageError):
                review.resolve_corpus_path(self.root, relative)
        with self.assertRaises(review.ReviewPackageError):
            review.resolve_source_path(self.root, "../unapproved.py")

    def test_exclusive_write_reuses_exact_and_rejects_collision(self) -> None:
        path = review.resolve_corpus_path(self.root, "working/immutable-test.bin")
        self.assertEqual(review._write_exclusive_or_exact(path, b"exact"), "created")
        self.assertEqual(review._write_exclusive_or_exact(path, b"exact"), "reused_exact")
        with self.assertRaises(review.ReviewPackageError):
            review._write_exclusive_or_exact(path, b"different")


if __name__ == "__main__":
    unittest.main()
