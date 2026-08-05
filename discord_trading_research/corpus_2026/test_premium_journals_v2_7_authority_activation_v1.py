from __future__ import annotations

import copy
import concurrent.futures
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import premium_journals_v2_7_authority_activation_v1 as activation
from qa.validate_premium_journals_v2_7_authority_activation_v1 import (
    _effective_authority_fixture,
    _resolve_live_collection_route_fixture,
    effective_authority,
    resolve_live_collection_route,
)


ROOT = Path(__file__).resolve().parent


def refingerprint(value: dict) -> dict:
    value.pop("record_fingerprint_sha256", None)
    value["record_fingerprint_sha256"] = activation.sha256_json(value)
    return value


def execute_fixture(root: Path) -> dict:
    with activation.activation_lock(root):
        return activation._execute_activation_locked(root)


def fake_plan_audit(plan: dict, root: Path) -> dict:
    return {
        "schema_version": activation.SCHEMA_VERSION,
        "artifact_type": "premium_journals_v2_7_authority_activation_plan_independent_audit_report",
        "audit_id": "activation-plan-independent-audit-test",
        "status": "PASS",
        "audited_at_utc": "2026-07-22T10:00:00Z",
        "immutable": True,
        "append_only": True,
        "blockers": [],
        "bound_artifacts": {
            "activation_plan": {
                **activation.simple_binding(root, activation.PLAN_PATH),
                "record_fingerprint_sha256": plan["record_fingerprint_sha256"],
            },
            "candidate": {"path": activation.CANDIDATE_PATH, "sha256": activation.CANDIDATE_SHA256, "bytes": activation.CANDIDATE_BYTES},
            "readiness_report": {"path": activation.READINESS_PATH, "sha256": activation.READINESS_SHA256, "bytes": activation.READINESS_BYTES},
            "prior_independent_audit": {"path": activation.PRIOR_AUDIT_PATH, "sha256": activation.PRIOR_AUDIT_SHA256, "bytes": activation.PRIOR_AUDIT_BYTES},
            "pre_activation_schedule_snapshot": {"path": activation.PREIMAGE_PATH, "sha256": activation.PRE_SCHEDULE_SHA256, "bytes": activation.PRE_SCHEDULE_BYTES},
        },
        "reviewed_projection_plan_sha256": plan["projection_plan_sha256"],
        "reviewed_source_file_set_sha256": plan["source_file_set_sha256"],
        "verdict": {"result": "PASS", "blocker_count": 0, "exact_plan_approved": True},
    }


def _collect_existing_bound_paths(value: object, source_root: Path = ROOT) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        relative = value.get("path")
        if isinstance(relative, str) and activation._normalized_relative(relative) and (source_root / relative).is_file():
            paths.add(relative)
        for nested in value.values():
            paths.update(_collect_existing_bound_paths(nested, source_root))
    elif isinstance(value, list):
        for nested in value:
            paths.update(_collect_existing_bound_paths(nested, source_root))
    elif isinstance(value, str) and activation._normalized_relative(value) and (source_root / value).is_file():
        paths.add(value)
    return paths


def _expand_json_references(paths: set[str], source_root: Path) -> set[str]:
    expanded = set(paths)
    inspected: set[str] = set()
    while True:
        pending = sorted(expanded - inspected)
        if not pending:
            return expanded
        for relative in pending:
            inspected.add(relative)
            path = source_root / relative
            if not relative.startswith("working/") or path.suffix.lower() != ".json" or not path.is_file():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            expanded.update(_collect_existing_bound_paths(value, source_root))


def copy_draft_base_inputs(destination: Path) -> None:
    source_objects = [
        activation.load_object(ROOT / activation.CANDIDATE_PATH),
        activation.load_object(ROOT / activation.READINESS_PATH),
        activation.load_object(ROOT / activation.PRIOR_AUDIT_PATH),
        activation.load_object(ROOT / activation.SUPERSEDED_PREIMAGE_PATH),
    ]
    paths = {
        activation.CANDIDATE_PATH,
        activation.READINESS_PATH,
        activation.PRIOR_AUDIT_PATH,
        activation.PREIMAGE_PATH,
        activation.SCHEDULE_PATH,
        *activation.IMPLEMENTATION_PATHS,
    }
    for value in source_objects:
        paths.update(_collect_existing_bound_paths(value))
    paths = _expand_json_references(paths, ROOT)
    # This is a historical pre-activation fixture.  Jan9 has since been
    # accepted under v2.6 in the live corpus, so reference discovery must not
    # copy that now-present canonical (or a future v2.7 counterpart) into the
    # preserved Jan9 preimage state exercised by this harness.
    paths.discard(activation.v26.expected_canonical_relative_path(activation.DAY, activation.DAY))
    paths.discard(activation.v27.expected_canonical_relative_path(activation.DAY, activation.DAY))
    for relative in sorted(paths):
        source = ROOT / relative
        if relative in {activation.SCHEDULE_PATH, activation.PREIMAGE_PATH}:
            source = ROOT / activation.SUPERSEDED_PREIMAGE_PATH
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def copy_supersession_fixture(destination: Path) -> None:
    paths = {
        *activation.IMPLEMENTATION_PATHS,
        activation.CANDIDATE_PATH,
        activation.READINESS_PATH,
        activation.PRIOR_AUDIT_PATH,
    }
    for relative in sorted(paths):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    raw = (ROOT / activation.SUPERSEDED_PREIMAGE_PATH).read_bytes()
    for relative in (activation.SCHEDULE_PATH, activation.SUPERSEDED_PREIMAGE_PATH):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def create_reviewed_fixture(destination: Path) -> Path:
    copy_draft_base_inputs(destination)
    plan = activation.build_plan(destination)
    plan_path = destination / activation.PLAN_PATH
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_bytes(activation.json_bytes(plan))
    audit = fake_plan_audit(plan, destination)
    (destination / activation.PLAN_AUDIT_PATH).write_bytes(activation.json_bytes(audit))
    return destination


_FIXTURE_DIRECTORY: tempfile.TemporaryDirectory[str] | None = None
_FIXTURE_ROOT: Path | None = None


def reviewed_fixture_root() -> Path:
    global _FIXTURE_DIRECTORY, _FIXTURE_ROOT
    if _FIXTURE_ROOT is None:
        _FIXTURE_DIRECTORY = tempfile.TemporaryDirectory()
        _FIXTURE_ROOT = create_reviewed_fixture(Path(_FIXTURE_DIRECTORY.name) / "reviewed_corpus")
    return _FIXTURE_ROOT


def copy_reviewed_inputs(destination: Path) -> None:
    source_root = reviewed_fixture_root()
    source_objects = [
        activation.load_object(source_root / activation.CANDIDATE_PATH),
        activation.load_object(source_root / activation.READINESS_PATH),
        activation.load_object(source_root / activation.PRIOR_AUDIT_PATH),
        activation.load_object(source_root / activation.PLAN_PATH),
        activation.load_object(source_root / activation.PLAN_AUDIT_PATH),
        activation.load_object(source_root / activation.SCHEDULE_PATH),
    ]
    paths = {
        activation.CANDIDATE_PATH,
        activation.READINESS_PATH,
        activation.PRIOR_AUDIT_PATH,
        activation.PLAN_PATH,
        activation.PLAN_AUDIT_PATH,
        activation.PREIMAGE_PATH,
        activation.SCHEDULE_PATH,
    }
    for value in source_objects:
        paths.update(_collect_existing_bound_paths(value, source_root))
    paths = _expand_json_references(paths, source_root)
    for relative in sorted(paths):
        source = source_root / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def prepare_unmarked_projection(root: Path) -> bytes:
    plan = activation.load_object(root / activation.PLAN_PATH)
    audit = activation.load_object(root / activation.PLAN_AUDIT_PATH)
    preimage = activation.load_object(root / activation.PREIMAGE_PATH)
    preimage_record = activation.binding(root, activation.PREIMAGE_PATH, "pre_activation_schedule_snapshot")
    receipt = activation.build_receipt(
        root,
        plan,
        audit,
        preimage_record,
        created_at_utc="2026-07-22T12:00:00Z",
    )
    receipt_path = root / activation.RECEIPT_PATH
    receipt_path.write_bytes(activation.json_bytes(receipt))
    receipt_record = activation.binding(root, activation.RECEIPT_PATH, "activation_receipt")
    projected = activation.project_schedule(
        preimage,
        receipt,
        receipt_record,
        preimage_record,
        plan,
        audit,
    )
    projected_raw = activation.json_bytes(projected)
    (root / activation.SCHEDULE_PATH).write_bytes(projected_raw)
    bundle = activation.build_projection_bundle(
        root,
        preimage_record,
        receipt_record,
        activation.binding(root, activation.PLAN_PATH, "independently_reviewed_activation_plan"),
        activation.binding(root, activation.PLAN_AUDIT_PATH, "activation_plan_independent_audit_report"),
        projected_raw,
        plan,
        receipt,
    )
    (root / activation.PROJECTION_BUNDLE_PATH).write_bytes(activation.json_bytes(bundle))
    return projected_raw


class ActivationPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = reviewed_fixture_root()
        cls.plan = activation.load_object(cls.root / activation.PLAN_PATH)
        cls.candidate = activation.load_object(cls.root / activation.CANDIDATE_PATH)
        cls.pre_schedule = activation.load_object(cls.root / activation.PREIMAGE_PATH)

    def test_plan_validates(self) -> None:
        self.assertEqual(
            [],
            activation.validate_plan(
                self.plan,
                self.root,
                require_live_prestate=True,
            ),
        )

    def test_preimage_is_exact_raw_schedule(self) -> None:
        path = self.root / activation.PREIMAGE_PATH
        self.assertEqual(activation.PRE_SCHEDULE_SHA256, activation.sha256_file(path))
        self.assertEqual(activation.PRE_SCHEDULE_BYTES, path.stat().st_size)

    def test_candidate_narrowing_is_explicit(self) -> None:
        delta = {item["field"]: item for item in self.plan["candidate_route_delta"]}
        self.assertTrue(delta["promotion_allowed"]["audited_candidate_value"])
        self.assertFalse(delta["promotion_allowed"]["activated_plan_value"])
        self.assertTrue(delta["collection_authority_enabled"]["activated_plan_value"])
        self.assertFalse(delta["canonical_authority_enabled"]["activated_plan_value"])

    def test_live_route_is_collection_only_pending_qa(self) -> None:
        route = self.plan["route_transition"]["pending_v2_7_route"]
        self.assertEqual("active_v2_7_collection_pending_qa", route["status"])
        self.assertTrue(route["live_collection_enabled"])
        self.assertTrue(route["collection_authority_enabled"])
        self.assertFalse(route["canonical_authority_enabled"])
        self.assertFalse(route["promotion_allowed"])
        self.assertFalse(route["canonical_promoted"])

    def test_live_route_uses_exact_query_and_versioned_paths(self) -> None:
        route = self.plan["route_transition"]["pending_v2_7_route"]
        self.assertEqual("in:premium-journals after:2026-01-08 before:2026-01-10", route["query"])
        self.assertEqual("raw/channel_segments_v2_7/channel_premium_journals_1283941772577472643_2026-01-09_2026-01-09.json", route["expected_canonical_path"])
        self.assertEqual("raw/premium_journals_v2_7_checkpoints/2026-01-09", route["expected_checkpoint_directory"])

    def test_retired_route_is_exact_candidate_v26_route_plus_receipt_fields(self) -> None:
        retired = self.plan["route_transition"]["retired_v2_6_route"]
        expected = activation.retired_v26_route(self.candidate)
        self.assertEqual(expected, retired)
        self.assertEqual("retired_by_v2_7_authority_activation", retired["status"])

    def test_plan_controls_are_all_false(self) -> None:
        self.assertTrue(all(value is False for value in self.plan["activation_controls"].values()))

    def test_plan_writer_exact_replay_is_idempotent(self) -> None:
        before = (self.root / activation.PLAN_PATH).read_bytes()
        path = activation._write_plan_exclusive_fixture(self.root)
        self.assertEqual(before, path.read_bytes())

    def test_projection_hash_is_deterministic(self) -> None:
        first = activation.build_plan(self.root)
        second = activation.build_plan(self.root)
        self.assertEqual(first["projection_plan_sha256"], second["projection_plan_sha256"])
        self.assertEqual(first["route_transition"], second["route_transition"])

    def test_plan_fingerprint_detects_tamper(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["status"] = "active"
        self.assertIn("activation_plan_fingerprint_mismatch", activation.validate_plan(plan, self.root))

    def test_refingerprinted_promotion_tamper_fails_semantics(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["authority_state_after_commit"]["promotion_allowed"] = True
        refingerprint(plan)
        self.assertIn("activation_plan_live_pending_flags_invalid", activation.validate_plan(plan, self.root))

    def test_source_path_escape_fails(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["source_files"][0]["path"] = "../../escape.json"
        plan["source_file_set_sha256"] = activation.sha256_json(plan["source_files"])
        refingerprint(plan)
        self.assertTrue(any("binding_path_invalid" in item for item in activation.validate_plan(plan, self.root)))

    def test_preservation_manifest_covers_all_non_jan9_routes(self) -> None:
        manifest = self.plan["pre_activation_preservation_manifest"]
        premium = self.pre_schedule["routes"]["premium_journals"]
        self.assertEqual(201, manifest["premium_route_count"])
        index = manifest["jan9_route_index"]
        self.assertEqual("2026-01-09", premium[index]["start"])
        self.assertEqual(activation.sha256_json(premium[:index] + premium[index + 1 :]), manifest["all_premium_routes_except_jan9_sha256"])


class ProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = reviewed_fixture_root()
        cls.plan = activation.load_object(cls.root / activation.PLAN_PATH)
        cls.pre = activation.load_object(cls.root / activation.PREIMAGE_PATH)
        cls.audit = fake_plan_audit(cls.plan, cls.root)
        cls.receipt = {"record_fingerprint_sha256": "f" * 64}
        cls.receipt_binding = {"role": "activation_receipt", "path": activation.RECEIPT_PATH, "sha256": "a" * 64, "bytes": 1}
        cls.preimage_binding = activation.binding(cls.root, activation.PREIMAGE_PATH, "pre_activation_schedule_snapshot")

    def projected(self) -> dict:
        return activation.project_schedule(self.pre, self.receipt, self.receipt_binding, self.preimage_binding, self.plan, self.audit)

    def test_projection_is_pure_and_deterministic(self) -> None:
        before = copy.deepcopy(self.pre)
        one, two = self.projected(), self.projected()
        self.assertEqual(before, self.pre)
        self.assertEqual(one, two)

    def test_only_jan9_route_changes(self) -> None:
        projected = self.projected()
        before = self.pre["routes"]["premium_journals"]
        after = projected["routes"]["premium_journals"]
        changed = [index for index, pair in enumerate(zip(before, after)) if pair[0] != pair[1]]
        self.assertEqual([8], changed)
        self.assertEqual(self.pre["routes"]["student_breakdowns"], projected["routes"]["student_breakdowns"])
        self.assertEqual(self.pre["routes"]["questions"], projected["routes"]["questions"])

    def test_all_preexisting_top_level_objects_are_equal(self) -> None:
        projected = self.projected()
        for key, value in self.pre.items():
            if key != "routes":
                self.assertEqual(value, projected[key], key)

    def test_projection_has_one_retired_and_one_collection_authority(self) -> None:
        projected = self.projected()
        retired = [route for route in projected["routes"]["premium_journals"] if route.get("start") == activation.DAY]
        active = projected["premium_journals_v2_7_authoritative_routes"]
        self.assertEqual(1, len(retired))
        self.assertEqual(1, len(active))
        self.assertEqual("retired_by_v2_7_authority_activation", retired[0]["status"])
        self.assertTrue(active[0]["collection_authority_enabled"])
        self.assertFalse(active[0]["canonical_authority_enabled"])

    def test_projection_never_embeds_commit_marker_hash(self) -> None:
        state = self.projected()["premium_journals_v2_7_authority_activation"]
        self.assertEqual(activation.COMMIT_MARKER_PATH, state["commit_marker_path"])
        self.assertNotIn("commit_marker_sha256", state)

    def test_promotion_is_impossible_in_projected_route(self) -> None:
        route = self.projected()["premium_journals_v2_7_authoritative_routes"][0]
        self.assertFalse(route["promotion_allowed"])
        self.assertFalse(route["canonical_authority_enabled"])
        self.assertFalse(route["canonical_promoted"])


class PlanAuditContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = reviewed_fixture_root()
        cls.plan = activation.load_object(cls.root / activation.PLAN_PATH)

    def test_exact_fake_plan_audit_passes_contract(self) -> None:
        self.assertEqual([], activation.validate_plan_audit(self.plan, fake_plan_audit(self.plan, self.root), self.root))

    def test_plan_audit_must_be_pass(self) -> None:
        audit = fake_plan_audit(self.plan, self.root)
        audit["status"] = "FAIL"
        self.assertIn("activation_plan_audit_fixed_contract_invalid", activation.validate_plan_audit(self.plan, audit, self.root))

    def test_plan_audit_must_bind_exact_plan(self) -> None:
        audit = fake_plan_audit(self.plan, self.root)
        audit["bound_artifacts"]["activation_plan"]["sha256"] = "0" * 64
        self.assertIn("activation_plan_audit_plan_binding_invalid", activation.validate_plan_audit(self.plan, audit, self.root))

    def test_plan_audit_must_approve_exact_projection_hash(self) -> None:
        audit = fake_plan_audit(self.plan, self.root)
        audit["reviewed_projection_plan_sha256"] = "0" * 64
        self.assertIn("activation_plan_audit_projection_hash_mismatch", activation.validate_plan_audit(self.plan, audit, self.root))


class AtomicIoAndCrashTests(unittest.TestCase):
    def test_exclusive_write_exact_replay_and_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "immutable.json"
            self.assertEqual("created", activation._write_exclusive_or_exact(path, b"one"))
            self.assertEqual("reused_exact", activation._write_exclusive_or_exact(path, b"one"))
            with self.assertRaises(activation.ActivationError):
                activation._write_exclusive_or_exact(path, b"two")

    def test_atomic_replace_writes_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.json"
            path.write_bytes(b"before")
            activation._atomic_replace(path, b"after")
            self.assertEqual(b"after", path.read_bytes())
            self.assertEqual([], list(Path(directory).glob("*.tmp")))

    def test_immutable_publish_recovers_after_prepublication_failure_for_every_artifact(self) -> None:
        relatives = (
            activation.PREIMAGE_PATH,
            activation.PLAN_PATH,
            activation.RECEIPT_PATH,
            activation.PROJECTION_BUNDLE_PATH,
            activation.COMMIT_MARKER_PATH,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, relative in enumerate(relatives):
                with self.subTest(relative=relative):
                    path = root / relative
                    raw = f"immutable-{index}".encode()
                    with mock.patch.object(activation.os, "link", side_effect=OSError("simulated prepublication crash")):
                        with self.assertRaisesRegex(OSError, "simulated prepublication crash"):
                            activation._write_exclusive_or_exact(path, raw)
                    self.assertFalse(path.exists())
                    orphan = path.with_name(f".{path.name}.orphan.immutable.tmp")
                    orphan.parent.mkdir(parents=True, exist_ok=True)
                    orphan.write_bytes(b"partial-unpublished-temp")
                    self.assertEqual("created", activation._write_exclusive_or_exact(path, raw))
                    self.assertEqual(raw, path.read_bytes())

    def test_live_repository_jan9_plan_and_commit_are_permanently_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for root in (ROOT, Path(directory).resolve()):
                with self.subTest(root=root):
                    with self.assertRaisesRegex(activation.ActivationError, "superseded"):
                        activation.write_plan_exclusive(root)
                    with self.assertRaisesRegex(activation.ActivationError, "superseded"):
                        activation.execute_activation(root)
        reader = effective_authority(ROOT)
        self.assertEqual(activation.DRAFT_STATUS, reader["status"])
        self.assertFalse(reader["live_collection_enabled"])
        self.assertIsNone(reader["effective_route"])
        self.assertEqual("2026-01-10", reader["first_future_activation_target"])
        with self.assertRaisesRegex(activation.ActivationError, "superseded"):
            resolve_live_collection_route(ROOT)
        self.assertFalse((ROOT / activation.PREIMAGE_PATH).exists())
        self.assertFalse((ROOT / activation.PLAN_PATH).exists())
        self.assertFalse((ROOT / activation.RECEIPT_PATH).exists())
        self.assertFalse((ROOT / activation.COMMIT_MARKER_PATH).exists())

    def test_superseded_preimage_is_exact_and_non_authoritative(self) -> None:
        path = ROOT / activation.SUPERSEDED_PREIMAGE_PATH
        self.assertEqual(activation.PRE_SCHEDULE_SHA256, activation.sha256_file(path))
        self.assertEqual(activation.PRE_SCHEDULE_BYTES, path.stat().st_size)

    def test_supersession_manifest_is_immutable_and_non_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "supersession"
            root.mkdir()
            copy_supersession_fixture(root)
            path = activation.archive_superseded_jan9_draft(root)
            manifest = activation.load_object(path)
            self.assertEqual([], activation.validate_supersession_manifest(manifest, root))
            self.assertEqual(activation.DRAFT_STATUS, manifest["status"])
            self.assertFalse(manifest["jan9_v2_7_authority"])
            self.assertFalse(manifest["jan9_v2_7_collection_authorized"])
            self.assertEqual("2026-01-10", manifest["first_future_activation_target"]["day"])
            self.assertEqual(path.read_bytes(), activation.archive_superseded_jan9_draft(root).read_bytes())

            extra = copy.deepcopy(manifest)
            extra["unexpected"] = True
            refingerprint(extra)
            self.assertIn("supersession_manifest_key_set_invalid", activation.validate_supersession_manifest(extra, root))

            wrong_reason = copy.deepcopy(manifest)
            wrong_reason["reason"] = "different"
            refingerprint(wrong_reason)
            self.assertIn("supersession_manifest_reason_invalid", activation.validate_supersession_manifest(wrong_reason, root))

            swapped = copy.deepcopy(manifest)
            swapped["archived_files"][0]["source_path_at_supersession"], swapped["archived_files"][1]["source_path_at_supersession"] = (
                swapped["archived_files"][1]["source_path_at_supersession"],
                swapped["archived_files"][0]["source_path_at_supersession"],
            )
            swapped["archived_file_set_sha256"] = activation.sha256_json(swapped["archived_files"])
            refingerprint(swapped)
            self.assertIn("supersession_manifest_source_archive_mapping_invalid", activation.validate_supersession_manifest(swapped, root))

    def test_supersession_archive_rejects_changed_schedule_and_historical_input(self) -> None:
        for mutation in ("schedule", "candidate"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve() / "supersession"
                root.mkdir()
                copy_supersession_fixture(root)
                if mutation == "schedule":
                    (root / activation.SCHEDULE_PATH).write_text("{}\n", encoding="utf-8")
                else:
                    candidate = root / activation.CANDIDATE_PATH
                    candidate.write_bytes(candidate.read_bytes() + b"\n")
                with self.assertRaises(activation.ActivationError):
                    activation.archive_superseded_jan9_draft(root)
                self.assertFalse((root / activation.SUPERSEDED_MANIFEST_PATH).exists())

    def test_pre_activation_reader_uses_exact_preimage_bytes_as_live_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working = root / "working"
            working.mkdir()
            (working / "scoped_three_parent_collection_schedule.json").write_bytes((ROOT / activation.SUPERSEDED_PREIMAGE_PATH).read_bytes())
            result = _effective_authority_fixture(root)
            self.assertEqual("PRE_ACTIVATION", result["status"])
            self.assertEqual("premium_journals_v2_6_preimage", result["effective_collection_authority"])

    def test_missing_marker_after_schedule_replace_falls_back_to_preimage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working = root / "working"
            working.mkdir()
            (working / Path(activation.PREIMAGE_PATH).name).write_bytes((ROOT / activation.SUPERSEDED_PREIMAGE_PATH).read_bytes())
            (working / Path(activation.SCHEDULE_PATH).name).write_text("{}\n", encoding="utf-8")
            result = _effective_authority_fixture(root)
            self.assertEqual("FAIL_CLOSED", result["status"])
            self.assertTrue(result["fail_closed_to_preimage"])
            self.assertFalse(result["live_collection_enabled"])

    def test_invalid_marker_falls_back_to_preimage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working = root / "working"
            working.mkdir()
            (working / Path(activation.PREIMAGE_PATH).name).write_bytes((ROOT / activation.SUPERSEDED_PREIMAGE_PATH).read_bytes())
            (working / Path(activation.SCHEDULE_PATH).name).write_text("{}\n", encoding="utf-8")
            (working / Path(activation.COMMIT_MARKER_PATH).name).write_text("{}\n", encoding="utf-8")
            result = _effective_authority_fixture(root)
            self.assertEqual("FAIL_CLOSED", result["status"])
            self.assertTrue(result["fail_closed_to_preimage"])
            self.assertFalse(result["live_collection_enabled"])

    def test_path_escape_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(activation.ActivationError):
                activation.resolve_path(Path(directory), "../escape.json")


class ReviewedPackageCrashReplayTests(unittest.TestCase):
    def temp_root(self, directory: str) -> Path:
        root = Path(directory).resolve() / "corpus"
        root.mkdir()
        copy_reviewed_inputs(root)
        return root

    def test_receipt_requires_exact_semantic_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.temp_root(directory)
            plan = activation.load_object(root / activation.PLAN_PATH)
            audit = activation.load_object(root / activation.PLAN_AUDIT_PATH)
            preimage_record = activation.binding(root, activation.PREIMAGE_PATH, "pre_activation_schedule_snapshot")
            receipt = activation.build_receipt(
                root,
                plan,
                audit,
                preimage_record,
                created_at_utc="2026-07-22T12:00:00Z",
            )
            (root / activation.RECEIPT_PATH).write_bytes(activation.json_bytes(receipt))
            self.assertEqual([], activation.validate_receipt(receipt, root, plan, audit, require_live_prestate=True))
            tampered = copy.deepcopy(receipt)
            tampered["bindings"]["candidate"] = copy.deepcopy(tampered["bindings"]["readiness_report"])
            refingerprint(tampered)
            self.assertIn(
                "activation_receipt_exact_bindings_invalid",
                activation.validate_receipt(tampered, root, plan, audit, require_live_prestate=True),
            )

    def test_projection_bundle_is_deterministic_and_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.temp_root(directory)
            projected_raw = prepare_unmarked_projection(root)
            plan = activation.load_object(root / activation.PLAN_PATH)
            receipt = activation.load_object(root / activation.RECEIPT_PATH)
            bundle = activation.load_object(root / activation.PROJECTION_BUNDLE_PATH)
            self.assertEqual([], activation.validate_projection_bundle(bundle, root, projected_raw, plan, receipt))
            rebuilt = activation.build_projection_bundle(
                root,
                activation.binding(root, activation.PREIMAGE_PATH, "pre_activation_schedule_snapshot"),
                activation.binding(root, activation.RECEIPT_PATH, "activation_receipt"),
                activation.binding(root, activation.PLAN_PATH, "independently_reviewed_activation_plan"),
                activation.binding(root, activation.PLAN_AUDIT_PATH, "activation_plan_independent_audit_report"),
                projected_raw,
                plan,
                receipt,
            )
            self.assertEqual(bundle, rebuilt)

    def test_power_loss_after_schedule_replace_resumes_exact_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.temp_root(directory)
            prepare_unmarked_projection(root)
            reader = _effective_authority_fixture(root)
            self.assertEqual("FAIL_CLOSED_RECOVERY_REQUIRED", reader["status"])
            self.assertTrue(reader["fail_closed_to_preimage"])
            self.assertTrue(reader["recovery_permitted"])
            self.assertFalse(reader["live_collection_enabled"])
            result = execute_fixture(root)
            self.assertEqual("resumed_exact_unmarked_projection_and_committed", result["status"])
            self.assertEqual([], activation.validate_committed_activation(root, require_activation_time_absence=True))

    def test_tampered_unmarked_projection_cannot_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.temp_root(directory)
            prepare_unmarked_projection(root)
            schedule = activation.load_object(root / activation.SCHEDULE_PATH)
            schedule["premium_journals_v2_7_authoritative_routes"][0]["promotion_allowed"] = True
            (root / activation.SCHEDULE_PATH).write_bytes(activation.json_bytes(schedule))
            with self.assertRaisesRegex(activation.ActivationError, "recovery validation failed"):
                execute_fixture(root)
            self.assertFalse((root / activation.COMMIT_MARKER_PATH).exists())

    def test_marker_write_failure_restores_preimage_and_exact_retry_commits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.temp_root(directory)
            pre_raw = (root / activation.PREIMAGE_PATH).read_bytes()
            original = activation._write_exclusive_or_exact

            def fail_marker(path: Path, raw: bytes) -> str:
                if path == root / activation.COMMIT_MARKER_PATH:
                    raise OSError("simulated marker write failure")
                return original(path, raw)

            with mock.patch.object(activation, "_write_exclusive_or_exact", side_effect=fail_marker):
                with self.assertRaisesRegex(OSError, "simulated marker write failure"):
                    execute_fixture(root)
            self.assertEqual(pre_raw, (root / activation.SCHEDULE_PATH).read_bytes())
            self.assertFalse((root / activation.COMMIT_MARKER_PATH).exists())
            result = execute_fixture(root)
            self.assertEqual("committed_collection_authority_pending_qa", result["status"])
            self.assertEqual([], activation.validate_committed_activation(root, require_activation_time_absence=True))

    def test_two_concurrent_executors_cannot_undo_valid_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.temp_root(directory)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(execute_fixture, root) for _ in range(2)]
                results = [future.result(timeout=60) for future in futures]
            statuses = sorted(result["status"] for result in results)
            self.assertEqual(
                ["already_committed_exact", "committed_collection_authority_pending_qa"],
                statuses,
            )
            self.assertEqual([], activation.validate_committed_activation(root, require_activation_time_absence=True))
            reader = _effective_authority_fixture(root)
            self.assertEqual("PASS", reader["status"])
            route = _resolve_live_collection_route_fixture(root)
            self.assertEqual("active_v2_7_collection_pending_qa", route["status"])
            self.assertFalse(reader["ordinary_premium_route_array_selectable"])

    def test_reader_rejects_schedule_swap_after_initial_chain_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.temp_root(directory)
            execute_fixture(root)
            original = activation.validate_committed_activation

            def validate_then_swap(*args: object, **kwargs: object) -> list[str]:
                errors = original(*args, **kwargs)
                if not errors:
                    (root / activation.SCHEDULE_PATH).write_bytes(
                        (root / activation.PREIMAGE_PATH).read_bytes()
                    )
                return errors

            with mock.patch.object(activation, "validate_committed_activation", side_effect=validate_then_swap):
                reader = _effective_authority_fixture(root)
            self.assertEqual("FAIL_CLOSED", reader["status"])
            self.assertIsNone(reader["effective_route"])
            self.assertFalse(reader["live_collection_enabled"])
            self.assertTrue(any("route_snapshot" in item for item in reader["errors"]))

    def test_main_schedule_validator_rejects_all_superseded_jan9_activation_metadata(self) -> None:
        from validate_scoped_three_parent_schedule import validate_schedule

        with tempfile.TemporaryDirectory() as directory:
            root = self.temp_root(directory)
            prepare_unmarked_projection(root)
            unmarked_errors = validate_schedule(root, root / activation.SCHEDULE_PATH)
            self.assertTrue(
            any(
                "Jan9 draft is superseded" in item and "first future target is Jan10" in item
                for item in unmarked_errors
            ),
                unmarked_errors,
            )
            execute_fixture(root)
            self.assertEqual([], activation.validate_committed_activation(root, require_activation_time_absence=True))
            committed_errors = validate_schedule(root, root / activation.SCHEDULE_PATH)
            self.assertTrue(
                any("Jan9 draft is superseded" in item for item in committed_errors),
                committed_errors,
            )

if __name__ == "__main__":
    unittest.main()
