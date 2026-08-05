from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import validate_relevance_plan as validator


PLAN = HERE.parent / "relevance_collection_plan.json"
INVENTORY = HERE.parent / "full_server_channel_inventory.json"


class RelevancePlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = validator.load_json(PLAN)
        cls.inventory = validator.load_json(INVENTORY)

    def validate_variant(self, value: dict) -> dict:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="relevance-plan-test-",
            dir=PLAN.parent,
            delete=False,
        )
        path = Path(handle.name)
        try:
            with handle:
                json.dump(value, handle, ensure_ascii=False)
            return validator.validate_plan(
                path,
                INVENTORY,
                check_source_hashes=False,
            )
        finally:
            path.unlink(missing_ok=True)

    def test_canonical_plan_passes_with_locked_sources(self) -> None:
        report = validator.validate_plan(PLAN, INVENTORY, check_source_hashes=True)
        self.assertEqual(report["status"], "passed", report["errors"])
        self.assertEqual(report["errors"], [])

    def test_exact_inventory_and_policy_partition(self) -> None:
        report = validator.validate_plan(PLAN, INVENTORY)
        metrics = report["metrics"]
        self.assertEqual(metrics["inventory_channels"], 38)
        self.assertEqual(metrics["planned_channels"], 38)
        self.assertEqual(
            metrics["policy_counts"],
            {
                "full_capture": 16,
                "targeted_search_plus_residual_audit": 0,
                "verified_empty_full_window": 22,
            },
        )
        self.assertEqual(
            {
                row["channel_id"]
                for row in self.plan["channel_policies"]
                if row["policy"] == "targeted_search_plus_residual_audit"
            },
            set(),
        )

    def test_all_queries_have_discord_derived_source_refs(self) -> None:
        source_ids = {
            source["source_id"] for source in self.plan["vocabulary_sources"]
        }
        self.assertEqual(len(self.plan["query_families"]), 9)
        self.assertEqual(
            sum(len(family["queries"]) for family in self.plan["query_families"]),
            94,
        )
        for family in self.plan["query_families"]:
            for query in family["queries"]:
                self.assertTrue(query["source_refs"])
                self.assertTrue(set(query["source_refs"]) <= source_ids)

    def test_expanded_jobs_match_collector_contract_without_collisions(self) -> None:
        jobs = validator.expand_collector_jobs(self.plan)
        self.assertEqual(len(jobs), 38)
        self.assertEqual(
            sum(
                validator.segment_count(
                    job["args"]["startIso"],
                    job["args"]["endIso"],
                    job["args"]["spanDays"],
                )
                for job in jobs
            ),
            1315,
        )
        self.assertEqual(len({job["job_id"] for job in jobs}), len(jobs))
        prefixes = [job["args"]["collectorOptions"]["prefix"] for job in jobs]
        self.assertEqual(len(prefixes), len(set(prefixes)))
        for job in jobs:
            self.assertEqual(job["collector_export"], "collectDateRange")
            self.assertEqual(
                set(job["args"]["collectorOptions"]),
                validator.REQUIRED_COLLECTOR_OPTION_KEYS,
            )
            self.assertEqual(
                {
                    key: job["args"]["collectorOptions"][key]
                    for key in validator.FULL_CAPTURE_RUNTIME_OPTIONS
                },
                validator.FULL_CAPTURE_RUNTIME_OPTIONS,
            )
            self.assertTrue(job["args"]["queryPrefix"].startswith("in:"))

    def test_full_capture_resume_runtime_options_fail_closed(self) -> None:
        invalid_values = {
            "checkpointEvery": 1,
            "pageDelayMs": 1500,
            "reuseActiveSearch": False,
        }
        for option_name, invalid_value in invalid_values.items():
            with self.subTest(option_name=option_name):
                variant = copy.deepcopy(self.plan)
                variant["job_expansion"]["full_capture_and_empty_verification"][
                    "collector_options"
                ][option_name] = invalid_value
                report = self.validate_variant(variant)
                self.assertEqual(report["status"], "failed")
                self.assertTrue(
                    any(
                        error.startswith(
                            "full_capture_runtime_option_mismatch:"
                        )
                        and f":{option_name}:" in error
                        for error in report["errors"]
                    ),
                    report["errors"],
                )

    def test_declared_collector_contract_requires_active_search_reuse(self) -> None:
        variant = copy.deepcopy(self.plan)
        variant["collector_contract"]["required_collector_options"].remove(
            "reuseActiveSearch"
        )
        report = self.validate_variant(variant)
        self.assertEqual(report["status"], "failed")
        self.assertIn(
            "required_collector_options_contract_mismatch",
            report["errors"],
        )

    def test_unknown_query_source_fails(self) -> None:
        variant = copy.deepcopy(self.plan)
        variant["query_families"][0]["queries"][0]["source_refs"] = [
            "outside_trading_source"
        ]
        report = self.validate_variant(variant)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(
            any(error.startswith("unknown_query_source_ref:") for error in report["errors"])
        )

    def test_missing_inventory_channel_fails(self) -> None:
        variant = copy.deepcopy(self.plan)
        variant["channel_policies"].pop()
        report = self.validate_variant(variant)
        self.assertEqual(report["status"], "failed")
        self.assertIn("plan_channel_count_not_38:37", report["errors"])
        self.assertTrue(
            any(error.startswith("missing_inventory_channels:") for error in report["errors"])
        )

    def test_noisy_channels_are_required_full_capture(self) -> None:
        required = {
            "1493590222703824997",
            "1359593949110472777",
            "1298788584475590727",
        }
        actual = {
            row["channel_id"]
            for row in self.plan["channel_policies"]
            if row["policy"] == "full_capture"
        }
        self.assertTrue(required <= actual)

    def test_noisy_channel_cannot_be_downgraded_to_targeted(self) -> None:
        variant = copy.deepcopy(self.plan)
        chat = next(
            row
            for row in variant["channel_policies"]
            if row["channel_id"] == "1359593949110472777"
        )
        chat["policy"] = "targeted_search_plus_residual_audit"
        report = self.validate_variant(variant)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(
            any(error.startswith("targeted_channel_set_mismatch:") for error in report["errors"])
        )

    def test_forum_requires_exact_thread_inventory_policy(self) -> None:
        variant = copy.deepcopy(self.plan)
        forum = next(
            row
            for row in variant["channel_policies"]
            if row["channel_id"] == "1283941772577472643"
        )
        forum["requires_forum_thread_inventory"] = False
        report = self.validate_variant(variant)
        self.assertEqual(report["status"], "failed")
        self.assertIn(
            "premium_journals_must_be_full_capture_with_thread_inventory",
            report["errors"],
        )


if __name__ == "__main__":
    unittest.main()
