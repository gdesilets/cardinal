from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import premium_journals_v2_7_jan10_activation_review_v1 as review
import premium_journals_v2_7_jan10_authority_activation_v1 as activation
import test_premium_journals_v2_7_jan10_activation_review_v1 as review_tests
from qa import validate_premium_journals_v2_7_jan10_authority_activation_v1 as reader


LIVE_ROOT = Path(__file__).resolve().parent


def copy_activation_source(destination_root: Path, relative: str) -> None:
    source = activation._activation_source_path(LIVE_ROOT, relative)
    destination = activation._activation_source_path(destination_root, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def build_fixture() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary, root = review_tests.build_fixture()
    for relative in activation.ACTIVATION_SOURCE_PATHS:
        copy_activation_source(root, relative)
    for relative in (*review.PACKAGE_ARTIFACT_PATHS, review.INDEPENDENT_AUDIT_PATH):
        source = review.resolve_corpus_path(LIVE_ROOT, relative)
        destination = review.resolve_corpus_path(root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return temporary, root


class Jan10AuthorityActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary, self.root = build_fixture()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def activate(self) -> Path:
        return activation.execute_activation(self.root)

    def test_pre_activation_is_ready_but_not_live(self) -> None:
        state = reader.effective_authority(self.root)
        self.assertEqual(state["status"], "READY_FOR_ACTIVATION")
        self.assertFalse(state["live_collection_enabled"])
        self.assertFalse(state["query_submission_authorized"])
        self.assertIsNone(state["route"])
        with self.assertRaises(activation.ActivationError):
            reader.resolve_live_collection_route(self.root)

    def test_plan_binds_exact_audit_schedule_route_and_sources(self) -> None:
        snapshot = activation.capture_snapshot(self.root)
        self.assertEqual(activation.validate_preconditions(snapshot, allow_activation_artifacts=False), [])
        plan = activation.build_plan(snapshot)
        self.assertEqual(plan["active_collection_route"], activation.ACTIVE_ROUTE)
        self.assertEqual(plan["bound_schedule"]["sha256"], review.SCHEDULE_SHA256)
        self.assertEqual(plan["bound_schedule"]["bytes"], review.SCHEDULE_BYTES)
        audit = next(
            item for item in plan["bound_review_package"]
            if item["path"] == review.INDEPENDENT_AUDIT_PATH
        )
        self.assertEqual(audit["sha256"], activation.INDEPENDENT_AUDIT_SHA256)
        self.assertEqual(audit["bytes"], activation.INDEPENDENT_AUDIT_BYTES)
        self.assertEqual(
            [item["path"] for item in plan["activation_source_bindings"]],
            sorted(activation.ACTIVATION_SOURCE_PATHS),
        )

    def test_active_route_is_exact_collection_only(self) -> None:
        route = activation.ACTIVE_ROUTE
        self.assertEqual(route["start"], "2026-01-10")
        self.assertEqual(route["end"], "2026-01-10")
        self.assertEqual(route["timezone"], "America/Chicago")
        self.assertEqual(route["query"], "in:premium-journals after:2026-01-09 before:2026-01-11")
        self.assertEqual(route["collector_version"], "2.7")
        self.assertTrue(route["live_collection_enabled"])
        self.assertTrue(route["query_submission_authorized"])
        self.assertFalse(route["canonical_authority_enabled"])
        self.assertFalse(route["canonical_write_enabled"])
        self.assertFalse(route["promotion_allowed"])
        self.assertFalse(route["schedule_write_enabled"])

    def test_activation_publishes_exact_chain_without_schedule_write(self) -> None:
        schedule_path = review.resolve_corpus_path(self.root, review.SCHEDULE_PATH)
        schedule_before = schedule_path.read_bytes()
        marker = self.activate()
        self.assertEqual(marker, review.resolve_corpus_path(self.root, activation.MARKER_PATH))
        state = reader.effective_authority(self.root)
        self.assertEqual(state["status"], "LIVE_COLLECTION_AUTHORIZED")
        self.assertTrue(state["live_collection_enabled"])
        self.assertTrue(state["query_submission_authorized"])
        self.assertFalse(state["canonical_authority_enabled"])
        self.assertFalse(state["promotion_allowed"])
        self.assertEqual(schedule_path.read_bytes(), schedule_before)
        self.assertEqual(
            activation.validate_activation_snapshot(activation.capture_snapshot(self.root)), []
        )

    def test_route_resolver_requires_terminal_exact_chain(self) -> None:
        self.activate()
        resolved = reader.resolve_live_collection_route(self.root)
        self.assertEqual(resolved["route"], activation.ACTIVE_ROUTE)
        self.assertEqual(resolved["route_sha256"], review.sha256_json(activation.ACTIVE_ROUTE))
        self.assertEqual(resolved["schedule_sha256"], review.SCHEDULE_SHA256)

    def test_activation_hard_blocks_schedule_canonical_stage_and_query_actions(self) -> None:
        for call in (
            activation.mutate_schedule,
            activation.write_canonical,
            activation.create_collection_stage,
            activation.submit_discord_query,
        ):
            with self.assertRaises(activation.ActivationError):
                call()

    def test_every_crash_point_is_exactly_recoverable(self) -> None:
        for stop_at in activation.ACTIVATION_PUBLICATION_ORDER:
            with self.subTest(stop_at=stop_at):
                temporary, root = build_fixture()
                try:
                    def stop(relative: str) -> None:
                        if relative == stop_at:
                            raise RuntimeError("simulated crash")

                    with self.assertRaises(RuntimeError):
                        activation.execute_activation(root, _before_artifact=stop)
                    state = activation.classify_authority(root)
                    if stop_at == activation.PREIMAGE_PATH:
                        expected = "READY_FOR_ACTIVATION"
                    elif stop_at == activation.TERMINAL_AUDIT_PATH:
                        expected = "LIVE_COLLECTION_AUTHORIZED_PENDING_TERMINAL_AUDIT"
                    else:
                        expected = "FAIL_CLOSED_RECOVERY_REQUIRED"
                    self.assertEqual(state["status"], expected)
                    activation.execute_activation(root)
                    self.assertEqual(
                        activation.classify_authority(root)["status"],
                        "LIVE_COLLECTION_AUTHORIZED",
                    )
                finally:
                    temporary.cleanup()

    def test_two_concurrent_executors_publish_one_exact_chain(self) -> None:
        barrier = threading.Barrier(2)

        def run() -> str:
            barrier.wait()
            return str(activation.execute_activation(self.root))

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _item: run(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(
            activation.classify_authority(self.root)["status"],
            "LIVE_COLLECTION_AUTHORIZED",
        )

    def test_exact_replay_is_idempotent(self) -> None:
        self.activate()
        before = {
            relative: review.resolve_corpus_path(self.root, relative).read_bytes()
            for relative in activation.ACTIVATION_ARTIFACT_PATHS
        }
        self.activate()
        after = {
            relative: review.resolve_corpus_path(self.root, relative).read_bytes()
            for relative in activation.ACTIVATION_ARTIFACT_PATHS
        }
        self.assertEqual(before, after)

    def test_tamper_in_each_activation_artifact_fails_closed(self) -> None:
        for relative in activation.ACTIVATION_ARTIFACT_PATHS:
            with self.subTest(relative=relative):
                temporary, root = build_fixture()
                try:
                    activation.execute_activation(root)
                    path = review.resolve_corpus_path(root, relative)
                    path.write_bytes(path.read_bytes() + b" ")
                    self.assertEqual(activation.classify_authority(root)["status"], "FAIL_CLOSED")
                finally:
                    temporary.cleanup()

    def test_schedule_audit_and_source_drift_fail_closed(self) -> None:
        cases = (
            ("schedule", review.SCHEDULE_PATH, False),
            ("audit", review.INDEPENDENT_AUDIT_PATH, False),
            ("source", "premium_journals_v2_7_jan10_authority_activation_v1.py", True),
        )
        for _label, relative, source_path in cases:
            with self.subTest(relative=relative):
                temporary, root = build_fixture()
                try:
                    activation.execute_activation(root)
                    path = (
                        activation._activation_source_path(root, relative)
                        if source_path else review.resolve_corpus_path(root, relative)
                    )
                    path.write_bytes(path.read_bytes() + b" ")
                    self.assertEqual(activation.classify_authority(root)["status"], "FAIL_CLOSED")
                finally:
                    temporary.cleanup()

    def test_target_stage_and_unenumerated_marker_appearance_fail_closed(self) -> None:
        representatives = (
            review.JAN10_V25_CANONICAL,
            review.JAN10_V27_CANONICAL,
            review.JAN10_LEGACY_CANONICAL.removesuffix(".json") + ".partial.json",
            review.v27.expected_checkpoint_relative_directory(review.DAY),
            "raw/quarantine_collection_errors/terra_premium_journals_daily_2026-01-10_fixture/stage.json",
            "working/premium_journals_v2_7_jan10_authority_activation_surprise_marker.backup",
        )
        for relative in representatives:
            with self.subTest(relative=relative):
                temporary, root = build_fixture()
                try:
                    activation.execute_activation(root)
                    path = review.resolve_corpus_path(root, relative)
                    if relative == review.v27.expected_checkpoint_relative_directory(review.DAY):
                        path.mkdir(parents=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text("{}", encoding="utf-8")
                    self.assertEqual(activation.classify_authority(root)["status"], "FAIL_CLOSED")
                finally:
                    temporary.cleanup()

    def test_no_canonical_stage_query_or_rollback_artifact_is_written(self) -> None:
        self.activate()
        for relative in review.TARGET_ABSENCE_PATHS:
            if relative in set(activation.ACTIVATION_ARTIFACT_PATHS) | {activation.LOCK_PATH}:
                continue
            self.assertFalse(review.resolve_corpus_path(self.root, relative).exists(), relative)
        self.assertFalse(review.resolve_corpus_path(self.root, activation.ROLLBACK_RECEIPT_PATH).exists())

    def test_marker_without_terminal_audit_is_live_but_route_resolution_waits(self) -> None:
        def stop(relative: str) -> None:
            if relative == activation.TERMINAL_AUDIT_PATH:
                raise RuntimeError("stop before terminal audit")

        with self.assertRaises(RuntimeError):
            activation.execute_activation(self.root, _before_artifact=stop)
        state = activation.classify_authority(self.root)
        self.assertEqual(state["status"], "LIVE_COLLECTION_AUTHORIZED_PENDING_TERMINAL_AUDIT")
        self.assertTrue(state["live_collection_enabled"])
        with self.assertRaises(activation.ActivationError):
            activation.resolve_live_collection_route(self.root)

    def test_terminal_audit_binds_unchanged_schedule_and_no_write_claims(self) -> None:
        self.activate()
        raw = review.resolve_corpus_path(self.root, activation.TERMINAL_AUDIT_PATH).read_bytes()
        audit = json.loads(raw.decode("utf-8"))
        self.assertEqual(audit["status"], "PASS")
        self.assertEqual(audit["schedule_before"], audit["schedule_after"])
        self.assertTrue(audit["schedule_byte_equal"])
        self.assertFalse(audit["canonical_or_stage_written"])
        self.assertFalse(audit["query_submitted"])
        self.assertFalse(audit["collector_invoked"])
        self.assertTrue(audit["jan9_authority_unchanged"])

    def test_reader_is_lock_free_and_write_free(self) -> None:
        self.activate()
        files = [path for path in self.root.parent.rglob("*") if path.is_file()]
        before = {
            str(path): (path.stat().st_size, path.stat().st_mtime_ns, review.sha256_bytes(path.read_bytes()))
            for path in files
        }
        state = activation.classify_authority(self.root)
        self.assertEqual(state["status"], "LIVE_COLLECTION_AUTHORIZED")
        after_files = [path for path in self.root.parent.rglob("*") if path.is_file()]
        after = {
            str(path): (path.stat().st_size, path.stat().st_mtime_ns, review.sha256_bytes(path.read_bytes()))
            for path in after_files
        }
        self.assertEqual(before, after)

    def test_reader_detects_toctou_change(self) -> None:
        self.activate()
        schedule = review.resolve_corpus_path(self.root, review.SCHEDULE_PATH)
        original = schedule.read_bytes()
        original_capture = activation.capture_snapshot
        calls = 0

        def capture(root: Path):
            nonlocal calls
            calls += 1
            snapshot = original_capture(root)
            if calls == 1:
                schedule.write_bytes(original + b" ")
            return snapshot

        try:
            with mock.patch.object(activation, "capture_snapshot", side_effect=capture):
                state = activation.classify_authority(self.root)
            self.assertEqual(state["status"], "FAIL_CLOSED_SNAPSHOT_CHANGED")
        finally:
            schedule.write_bytes(original)

    def test_wrong_root_and_path_collision_fail(self) -> None:
        empty = tempfile.TemporaryDirectory()
        try:
            wrong = Path(empty.name) / "wrong"
            wrong.mkdir()
            self.assertEqual(activation.classify_authority(wrong)["status"], "FAIL_CLOSED")
        finally:
            empty.cleanup()
        path = review.resolve_corpus_path(self.root, activation.PREIMAGE_PATH)
        path.write_bytes(b"collision")
        with self.assertRaises(activation.ActivationError):
            self.activate()


if __name__ == "__main__":
    unittest.main()
