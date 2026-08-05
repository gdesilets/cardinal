import hashlib
import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


import build_llm_handoff_guide as guide


HERE = Path(__file__).resolve().parent


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HandoffGuideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.final = self.root / "final"
        self.final.mkdir()
        self.template = self.root / "LLM_HANDOFF_GUIDE_TEMPLATE.md"
        shutil.copy2(HERE / "LLM_HANDOFF_GUIDE_TEMPLATE.md", self.template)

        self.paths = {
            "merged_corpus": self.final / "raw_corpus_release.json",
            "coverage_manifest": self.final / "coverage_manifest_release.json",
            "pristine_database": self.final / "cardinal_pristine.sqlite",
            "full_database": self.final / "cardinal_analyzed.sqlite",
            "compact_database": self.final / "cardinal_llm.sqlite",
            "analysis_report": self.final / "analysis_report.json",
            "qa_report": self.final / "independent_qa_report.json",
            "compact_report": self.final / "llm_companion_report.json",
            "output": self.final / "LLM_HANDOFF_GUIDE.md",
        }
        self.paths["pristine_database"].write_bytes(b"pristine-final-database")
        self.paths["full_database"].write_bytes(b"authoritative-analyzed-database")
        self.paths["compact_database"].write_bytes(b"compact-llm-database")
        self.write_payloads()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def scope() -> dict:
        return {
            "guild_id": guide.EXPECTED_GUILD_ID,
            "timezone": guide.EXPECTED_TIMEZONE,
            "start_date_inclusive": guide.EXPECTED_START_DATE,
            "end_date_inclusive": guide.EXPECTED_END_DATE,
            "utc_start_inclusive": guide.EXPECTED_START_UTC,
            "utc_end_exclusive": guide.EXPECTED_END_UTC,
            "local_calendar_days": 201,
        }

    def write_json(self, role: str, value: dict) -> None:
        self.paths[role].write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def write_payloads(self) -> None:
        full_hash = sha(self.paths["full_database"])
        compact_hash = sha(self.paths["compact_database"])
        pristine_hash = sha(self.paths["pristine_database"])
        timestamp_integrity = {
            "schema_version": "1.0.0",
            "passed": True,
            "content_hash_bound": True,
            "unresolved_message_count": 0,
            "invalid_sidecar_count": 0,
            "unused_revalidation_record_count": 0,
            "external_revalidation_message_count": 0,
            "external_revalidation_used_record_count": 0,
            "sidecar_count": 0,
            "sidecars": [],
        }
        anchor_id = (
            guide.reply_provenance_contract
            .EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID
        )
        executed_integrity = {
            "schema_version": "1.0.0",
            "passed": True,
            "audited_segment_count": 1,
            "audited_message_count": 1,
            "expected_segment_count": 1,
            "expected_segment_present": True,
            "legacy_anchor_message_id": anchor_id,
            "legacy_anchor_count": 1,
            "candidate_count": 1,
            "accepted_exact_context_count": 1,
            "failure_count": 0,
            "candidate_message_ids": [anchor_id],
            "failures": [],
        }
        path_policy = {
            "gate": "premium_journals_authoritative_v2_5_source_integrity",
            "passed": True,
            "standard_authoritative_directory": "raw/channel_segments",
            "premium_authoritative_directory": "raw/channel_segments_v2_5",
            "premium_legacy_preservation_directory": "raw/channel_segments",
            "premium_legacy_directory_policy": "preservation_only_not_authoritative",
            "premium_collector_version_required": "2.6",
            "required_roots_supplied_exactly_once": True,
            "legacy_premium_authoritative_occurrence_count": 0,
            "premium_collector_version_mismatch_count": 0,
            "premium_collector_version_mismatch_paths": [],
            "premium_provenance_missing_segment_count": 0,
            "premium_provenance_missing_segments": [],
            "invalid_premium_authoritative_file_count": 0,
            "invalid_premium_authoritative_paths": [],
            "accepted_premium_bound_source_file_count": 201,
            "accepted_premium_segment_count": 201,
            "accepted_premium_daily_date_count": 201,
            "duplicate_premium_daily_dates": [],
            "accepted_premium_source_file_set_sha256": "a" * 64,
            "accepted_premium_message_id_set_sha256": "b" * 64,
        }
        closure = {
            "gate": "premium_journals_message_data_scope_closure",
            "passed": True,
            "closure_proven": True,
            "status": "complete",
            "required_parent_container_id": "1283941772577472643",
            "required_calendar_day_count": 201,
            "complete_calendar_day_count": 201,
            "parent_segment_count": 201,
            "required_exact_daily_parent_segment_count": 201,
            "invalid_daily_partition_segment_count": 0,
            "duplicate_daily_date_count": 0,
            "missing_date_ranges": [],
        }
        authorized = {
            "enabled": True,
            "canonical_path_policy": path_policy,
            "child_inventory_reconciliation": {
                "provided": True,
                "inventory_complete": False,
                "enumeration_complete": False,
                "closure_proven": False,
                "message_scope_closure": closure,
            },
        }
        self.write_json(
            "merged_corpus",
            {
                "artifact_type": "discord_serverwide_corpus_release",
                "scope": self.scope(),
                "source_scope": "discord_only",
                "outside_sources_used": 0,
                "release": {
                    "status": "complete",
                    "release_requested": True,
                    "release_ready": True,
                },
                "authorized_collection_scope": deepcopy(authorized),
                "release_gates": [deepcopy(path_policy), deepcopy(closure)],
            },
        )
        self.write_json(
            "coverage_manifest",
            {
                "artifact_type": "discord_serverwide_coverage_manifest",
                "status": "complete",
                "release_ready": True,
                "scope": self.scope(),
                "source_scope": "discord_only",
                "outside_sources_used": 0,
                "timestamp_scope_integrity": timestamp_integrity,
                "executed_command_reply_provenance_integrity": (
                    executed_integrity
                ),
                "authorized_collection_scope": deepcopy(authorized),
                "release_gates": [
                    deepcopy(path_policy),
                    deepcopy(closure),
                    {
                        "gate": "timestamp_scope_integrity",
                        "passed": True,
                        "detail": timestamp_integrity,
                    },
                    {
                        "gate": "executed_command_reply_provenance_integrity",
                        "passed": True,
                        "detail": executed_integrity,
                    },
                ],
                "source_files": [],
            },
        )
        self.write_json(
            "analysis_report",
            {
                "status": "passed",
                "source_scope": "discord_only",
                "outside_sources_used": 0,
                "database": str(self.paths["full_database"]),
                "database_sha256": full_hash,
                "coverage": {
                    "analysis_completeness": "complete",
                    "window_start_utc": guide.EXPECTED_START_UTC,
                    "window_end_utc": guide.EXPECTED_END_UTC,
                },
                "provenance": {
                    "input_database": str(self.paths["pristine_database"]),
                    "input_database_sha256": pristine_hash,
                },
            },
        )
        qa_scope = {
            "guild_id": guide.EXPECTED_GUILD_ID,
            "source_scope": "discord_only",
            "outside_sources_used": False,
            "window_calendar_timezone": guide.EXPECTED_TIMEZONE,
            "window_start_local_date": guide.EXPECTED_START_DATE,
            "window_end_local_date_inclusive": guide.EXPECTED_END_DATE,
            "window_start_utc": guide.EXPECTED_START_UTC,
            "window_end_exclusive_utc": guide.EXPECTED_END_UTC,
            "local_calendar_days": 201,
            "premium_authoritative_directory": "raw/channel_segments_v2_5",
            "premium_collector_version_required": "2.6",
            "premium_daily_segment_count": 201,
            "premium_inventory_census_complete": False,
        }
        self.write_json(
            "qa_report",
            {
                "artifact_type": "independent_discord_corpus_validation",
                "status": "passed",
                "overall_assessment": "Ready to share",
                "scope": qa_scope,
                "failure_counts": {"critical": 0, "high": 0, "medium_or_low": 0},
                "checks": [
                    {
                        "name": "collection_drift_final_audit_passed",
                        "passed": True,
                    }
                ],
                "database_validation": {"sha256": full_hash},
                "inputs": {
                    "database": str(self.paths["full_database"]),
                    "collection_drift_audit": "working/collection_drift_final.json",
                },
                "collection_drift_audit": {
                    "status": "passed",
                    "passed": True,
                    "path": "working/collection_drift_final.json",
                    "sha256": "D" * 64,
                    "mode": "final",
                    "overall_status": "PASS",
                    "release_gate_passed": True,
                    "summary": {
                        "structural_failure_count": 0,
                        "unresolved_count": 0,
                        "effective_final_failure_count": 0,
                        "orphan_quarantined_partial_count": 0,
                    },
                    "errors": [],
                },
            },
        )
        self.write_json(
            "compact_report",
            {
                "status": "passed",
                "source_scope": "discord_only",
                "outside_sources_used": 0,
                "source_database": str(self.paths["full_database"]),
                "source_database_sha256": full_hash,
                "source_database_unchanged": True,
                "database": str(self.paths["compact_database"]),
                "database_sha256": compact_hash,
            },
        )

    def render(self) -> dict:
        return guide.render_handoff_guide(
            template=self.template,
            output=self.paths["output"],
            merged_corpus=self.paths["merged_corpus"],
            coverage_manifest=self.paths["coverage_manifest"],
            pristine_database=self.paths["pristine_database"],
            full_database=self.paths["full_database"],
            compact_database=self.paths["compact_database"],
            analysis_report=self.paths["analysis_report"],
            qa_report=self.paths["qa_report"],
            compact_report=self.paths["compact_report"],
        )

    def test_real_template_has_exact_frozen_placeholder_contract(self) -> None:
        text = (HERE / "LLM_HANDOFF_GUIDE_TEMPLATE.md").read_text(encoding="utf-8")
        guide.validate_template(text)

    def test_renders_portable_hash_bound_guide_and_preserves_every_input(self) -> None:
        inputs = [self.template, *[self.paths[key] for key in self.paths if key != "output"]]
        before = {path: sha(path) for path in inputs}
        result = self.render()
        rendered = self.paths["output"].read_text(encoding="utf-8")
        self.assertEqual(result["status"], "passed")
        self.assertNotIn("{{", rendered)
        self.assertIn("databases/authoritative_cardinal.sqlite", rendered)
        self.assertIn("databases/compact_llm.sqlite", rendered)
        self.assertIn("NOT_PACKAGED", rendered)
        self.assertIn(sha(self.paths["full_database"]), rendered)
        self.assertIn(sha(self.paths["compact_database"]), rendered)
        self.assertIn("raw/channel_segments_v2_5", rendered)
        self.assertIn("`201` exact daily segments accepted", rendered)
        self.assertIn("inventory census complete: `false`", rendered)
        self.assertEqual(before, {path: sha(path) for path in inputs})

    def test_rejects_premium_source_or_inventory_census_regression(self) -> None:
        manifest = json.loads(
            self.paths["coverage_manifest"].read_text(encoding="utf-8")
        )
        manifest["authorized_collection_scope"]["canonical_path_policy"][
            "premium_authoritative_directory"
        ] = "raw/channel_segments"
        self.write_json("coverage_manifest", manifest)
        with self.assertRaises(guide.HandoffGuideError):
            self.render()
        self.assertFalse(self.paths["output"].exists())

        self.write_payloads()
        manifest = json.loads(
            self.paths["coverage_manifest"].read_text(encoding="utf-8")
        )
        manifest["authorized_collection_scope"]["child_inventory_reconciliation"][
            "inventory_complete"
        ] = True
        self.write_json("coverage_manifest", manifest)
        with self.assertRaises(guide.HandoffGuideError):
            self.render()
        self.assertFalse(self.paths["output"].exists())

    def test_unknown_or_wrong_placeholder_multiplicity_fails_without_output(self) -> None:
        with self.template.open("a", encoding="utf-8") as handle:
            handle.write("\n{{UNKNOWN_FINAL_FIELD}}\n")
        with self.assertRaises(guide.HandoffGuideError):
            self.render()
        self.assertFalse(self.paths["output"].exists())

        text = (HERE / "LLM_HANDOFF_GUIDE_TEMPLATE.md").read_text(encoding="utf-8")
        self.template.write_text(
            text.replace("{{FULL_DATABASE_PATH}}", "", 1), encoding="utf-8"
        )
        with self.assertRaises(guide.HandoffGuideError):
            self.render()
        self.assertFalse(self.paths["output"].exists())

    def test_scope_or_hash_mismatch_fails_closed(self) -> None:
        corpus = json.loads(self.paths["merged_corpus"].read_text(encoding="utf-8"))
        corpus["scope"]["end_date_inclusive"] = "2026-07-19"
        self.write_json("merged_corpus", corpus)
        with self.assertRaises(guide.HandoffGuideError):
            self.render()
        self.assertFalse(self.paths["output"].exists())

        self.write_payloads()
        compact = json.loads(self.paths["compact_report"].read_text(encoding="utf-8"))
        compact["source_database_sha256"] = "0" * 64
        self.write_json("compact_report", compact)
        with self.assertRaises(guide.HandoffGuideError):
            self.render()
        self.assertFalse(self.paths["output"].exists())

        self.write_payloads()
        qa = json.loads(self.paths["qa_report"].read_text(encoding="utf-8"))
        qa["collection_drift_audit"]["summary"]["unresolved_count"] = 1
        self.write_json("qa_report", qa)
        with self.assertRaises(guide.HandoffGuideError):
            self.render()
        self.assertFalse(self.paths["output"].exists())

    def test_refuses_overwrite_and_preserves_existing_guide(self) -> None:
        self.render()
        original = self.paths["output"].read_bytes()
        with self.assertRaises(guide.HandoffGuideError):
            self.render()
        self.assertEqual(original, self.paths["output"].read_bytes())

    def test_rejects_noncanonical_final_filename(self) -> None:
        wrong = self.final / "analyzed.sqlite"
        shutil.copy2(self.paths["full_database"], wrong)
        with self.assertRaises(guide.HandoffGuideError):
            guide.render_handoff_guide(
                template=self.template,
                output=self.paths["output"],
                merged_corpus=self.paths["merged_corpus"],
                coverage_manifest=self.paths["coverage_manifest"],
                pristine_database=self.paths["pristine_database"],
                full_database=wrong,
                compact_database=self.paths["compact_database"],
                analysis_report=self.paths["analysis_report"],
                qa_report=self.paths["qa_report"],
                compact_report=self.paths["compact_report"],
            )
        self.assertFalse(self.paths["output"].exists())


if __name__ == "__main__":
    unittest.main()
