#!/usr/bin/env python3
"""Tests for the local-only Discord collection orchestrator."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from collection_orchestrator import (
    build_observations,
    build_outputs,
    inspect_artifact,
    initial_state,
    make_segments,
    query_core,
    reconcile_job,
    record_count,
    record_throttle,
    unfiltered_cores,
)


def job(
    job_id: str,
    *,
    kind: str,
    channel_id: str = "123456789012345678",
    channel_name: str = "desk-chat",
    query_prefix: str = "in:desk-chat",
    start: str = "2026-01-01",
    end: str = "2026-01-10",
    span: int = 2,
    output: str = "raw/channel_segments",
    prefix: str | None = None,
) -> dict:
    return {
        "job_id": job_id,
        "job_kind": kind,
        "collector_export": "collectDateRange",
        "args": {
            "startIso": start,
            "endIso": end,
            "outputDirectory": output,
            "queryPrefix": query_prefix,
            "spanDays": span,
            "collectorOptions": {
                "prefix": prefix or job_id,
                "scope": "channel-scoped",
                "channelId": channel_id,
                "channelName": channel_name,
                "channelKind": "text channel",
                "categoryName": "TEST",
                "channelIdSource": "test",
                "checkpointEvery": 1,
                "pageDelayMs": 0,
                "maxAttempts": 1,
            },
            "schedulerOptions": {
                "batchSize": 1,
                "cooldownMs": 0,
                "throttleCooldownMs": 300000,
            },
        },
    }


def artifact_payload(
    *,
    channel_id: str,
    channel_name: str,
    query: str,
    start: str,
    end: str,
    total: int,
    complete: bool = True,
    captured: int | None = None,
) -> dict:
    captured = total if captured is None else captured
    pages = math.ceil(total / 25) if total else 0
    captured_pages = math.ceil(captured / 25) if captured else 0
    messages = [{"message_id": str(10_000_000_000_000_000 + i)} for i in range(captured)]
    completion_evidence = None
    if complete:
        if total == 0:
            observations = [
                {
                    "sequence": sequence,
                    "observed_at_utc": f"2026-07-20T20:00:0{sequence}Z",
                    "state": "empty_candidate",
                    "visible_result_count": 0,
                    "panel_text": "No Results",
                }
                for sequence in (1, 2, 3)
            ]
            completion_evidence = {
                "schema_version": "1.0.0",
                "query": query,
                "reported_total": 0,
                "reported_pages": 0,
                "terminal_state": "stable_empty",
                "search_submission": {
                    "mode": "fresh",
                    "query": query,
                    "submission_count": 1,
                    "submitted_at_utc": "2026-07-20T20:00:00Z",
                },
                "stable_empty": {"required_observations": 3, "observations": observations},
                "stable_bottom": None,
            }
        else:
            first_index = (pages - 1) * 25 + 1
            observations = [
                {
                    "sequence": sequence,
                    "observed_at_utc": f"2026-07-20T20:00:0{sequence}Z",
                    "query": query,
                    "visible_result_count": total - first_index + 1,
                    "first_result_index": first_index,
                    "last_result_index": total,
                    "current_page": pages,
                    "result_set_size": total,
                    "has_enabled_next": False,
                }
                for sequence in (1, 2)
            ]
            completion_evidence = {
                "schema_version": "1.0.0",
                "query": query,
                "reported_total": total,
                "reported_pages": pages,
                "terminal_state": "stable_bottom",
                "search_submission": {
                    "mode": "fresh",
                    "query": query,
                    "submission_count": 1,
                    "submitted_at_utc": "2026-07-20T20:00:00Z",
                },
                "stable_empty": None,
                "stable_bottom": {"required_observations": 2, "observations": observations},
            }
    return {
        "collector_version": "test",
        "guild_id": "1167376964680691732",
        "collection_scope": "channel-scoped",
        "captured_at_utc": "2026-07-20T20:00:00Z",
        "requested_container": {
            "channel_id": channel_id,
            "channel_name": channel_name,
        },
        "segment": {"start": start, "end": end, "query": query},
        "reported_total": total,
        "reported_pages": pages,
        "pages_captured": pages if complete else captured_pages,
        "captured_rows": captured,
        "unique_message_ids": captured,
        "gap_indices": [],
        "container_mismatch_count": 0,
        **({"completion_evidence": completion_evidence} if completion_evidence else {}),
        "complete": complete,
        "messages": messages,
    }


class OrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "raw" / "channel_segments").mkdir(parents=True)
        (self.root / "raw" / "relevance_segments").mkdir(parents=True)
        (self.root / "raw" / "audit_segments").mkdir(parents=True)
        self.collector = self.root / "discord_browser_collector.mjs"
        self.collector.write_text(
            "\n".join(
                [
                    "export function makeSegments() {}",
                    "export async function countSearch() {}",
                    "export async function collectSegmentResilient() {}",
                    "export async function collectDateRange() {}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_artifact(self, relative: str, payload: dict) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def jobs_payload(self, jobs: list[dict]) -> dict:
        return {
            "schema_version": "1.0.0",
            "collector_module": str(self.collector),
            "working_directory": str(self.root),
            "jobs": jobs,
        }

    def test_make_segments_preserves_exact_query_and_dates(self) -> None:
        target = job("full", kind="full_capture_or_empty_verification", span=4)
        rows = make_segments(target)
        self.assertEqual([(row["start"], row["end"]) for row in rows], [
            ("2026-01-01", "2026-01-04"),
            ("2026-01-05", "2026-01-08"),
            ("2026-01-09", "2026-01-10"),
        ])
        self.assertEqual(
            rows[0]["query"],
            "in:desk-chat after:2025-12-31 before:2026-01-05",
        )

    def test_real_complete_artifact_without_completion_evidence_is_invalid(self) -> None:
        payload = artifact_payload(
            channel_id="123456789012345678",
            channel_name="desk-chat",
            query="in:desk-chat after:2025-12-31 before:2026-01-02",
            start="2026-01-01",
            end="2026-01-01",
            total=0,
        )
        payload["collector_version"] = "2.4"
        payload.pop("completion_evidence")
        path = self.write_artifact("raw/channel_segments/legacy-empty.json", payload)
        inspected = inspect_artifact(path, self.root)
        self.assertEqual(inspected.state, "invalid")
        self.assertIn(
            "completion_evidence_missing_recapture_or_sidecar_required",
            inspected.errors,
        )
        payload["collector_version"] = "legacy"
        nonnumeric_path = self.write_artifact(
            "raw/channel_segments/legacy-empty-nonnumeric.json", payload
        )
        nonnumeric = inspect_artifact(nonnumeric_path, self.root)
        self.assertEqual(nonnumeric.state, "invalid")
        self.assertIn(
            "completion_evidence_missing_recapture_or_sidecar_required",
            nonnumeric.errors,
        )

    def test_exact_complete_segment_is_complete_even_above_adaptive_threshold(self) -> None:
        target = job("full", kind="full_capture_or_empty_verification", start="2026-01-01", end="2026-01-01", span=1)
        segment = make_segments(target)[0]
        path = self.write_artifact(
            segment["expected_relative_path"],
            artifact_payload(
                channel_id="123456789012345678",
                channel_name="desk-chat",
                query=segment["query"],
                start=segment["start"],
                end=segment["end"],
                total=75,
            ),
        )
        inspected = inspect_artifact(path, self.root)
        progress = reconcile_job(
            target,
            [inspected],
            unfiltered_by_channel=unfiltered_cores([target]),
            max_messages=10,
            max_pages=1,
        )
        self.assertEqual(progress["status"], "complete")
        self.assertEqual(progress["segments"][0]["status"], "complete")

    def test_safe_broad_segment_supersedes_narrow_planned_segments(self) -> None:
        target = job("full", kind="full_capture_or_empty_verification", span=2)
        query = "in:desk-chat after:2025-12-31 before:2026-01-11"
        path = self.write_artifact(
            "raw/channel_segments/broad.json",
            artifact_payload(
                channel_id="123456789012345678",
                channel_name="desk-chat",
                query=query,
                start="2026-01-01",
                end="2026-01-10",
                total=9,
            ),
        )
        progress = reconcile_job(
            target,
            [inspect_artifact(path, self.root)],
            unfiltered_by_channel=unfiltered_cores([target]),
            max_messages=100,
            max_pages=4,
        )
        self.assertEqual(progress["status"], "superseded")
        self.assertEqual({row["status"] for row in progress["segments"]}, {"superseded"})

    def test_gap_free_union_of_safe_captures_supersedes_crossing_segments(self) -> None:
        target = job("full", kind="full_capture_or_empty_verification", span=4)
        artifacts = []
        for index, (start, end, after, before) in enumerate(
            [
                ("2026-01-01", "2026-01-05", "2025-12-31", "2026-01-06"),
                ("2026-01-06", "2026-01-10", "2026-01-05", "2026-01-11"),
            ]
        ):
            path = self.write_artifact(
                f"raw/channel_segments/slice-{index}.json",
                artifact_payload(
                    channel_id="123456789012345678",
                    channel_name="desk-chat",
                    query=f"in:desk-chat after:{after} before:{before}",
                    start=start,
                    end=end,
                    total=2,
                ),
            )
            artifacts.append(inspect_artifact(path, self.root))
        progress = reconcile_job(
            target,
            artifacts,
            unfiltered_by_channel=unfiltered_cores([target]),
            max_messages=100,
            max_pages=4,
        )
        self.assertEqual(progress["status"], "superseded")
        self.assertEqual({row["status"] for row in progress["segments"]}, {"superseded"})

    def test_unsafe_broad_segment_does_not_supersede(self) -> None:
        target = job("full", kind="full_capture_or_empty_verification", span=2)
        path = self.write_artifact(
            "raw/channel_segments/large-broad.json",
            artifact_payload(
                channel_id="123456789012345678",
                channel_name="desk-chat",
                query="in:desk-chat after:2025-12-31 before:2026-01-11",
                start="2026-01-01",
                end="2026-01-10",
                total=101,
            ),
        )
        progress = reconcile_job(
            target,
            [inspect_artifact(path, self.root)],
            unfiltered_by_channel=unfiltered_cores([target]),
            max_messages=100,
            max_pages=4,
        )
        self.assertEqual(progress["status"], "partial")
        self.assertNotIn("superseded", {row["status"] for row in progress["segments"]})

    def test_unfiltered_complete_capture_supersedes_targeted_query(self) -> None:
        full = job("full", kind="full_capture_or_empty_verification", span=10)
        targeted = job(
            "target",
            kind="targeted_search",
            query_prefix="in:desk-chat rejection block",
            output="raw/relevance_segments",
            span=2,
        )
        path = self.write_artifact(
            "raw/channel_segments/full.json",
            artifact_payload(
                channel_id="123456789012345678",
                channel_name="desk-chat",
                query="in:desk-chat after:2025-12-31 before:2026-01-11",
                start="2026-01-01",
                end="2026-01-10",
                total=20,
            ),
        )
        progress = reconcile_job(
            targeted,
            [inspect_artifact(path, self.root)],
            unfiltered_by_channel=unfiltered_cores([full, targeted]),
            max_messages=100,
            max_pages=4,
        )
        self.assertEqual(progress["status"], "superseded")

    def test_partial_checkpoint_is_partial_and_counts_pages(self) -> None:
        target = job("full", kind="full_capture_or_empty_verification", start="2026-01-01", end="2026-01-01", span=1)
        segment = make_segments(target)[0]
        path = self.write_artifact(
            "raw/channel_segments/full_2026-01-01_2026-01-01.partial.json",
            artifact_payload(
                channel_id="123456789012345678",
                channel_name="desk-chat",
                query=segment["query"],
                start=segment["start"],
                end=segment["end"],
                total=75,
                complete=False,
                captured=25,
            ),
        )
        inspected = inspect_artifact(path, self.root)
        self.assertEqual(inspected.state, "partial")
        self.assertEqual(inspected.reported_pages, 3)
        self.assertEqual(inspected.pages_captured, 1)

    def test_query_date_bounds_mismatch_is_invalid(self) -> None:
        path = self.write_artifact(
            "raw/channel_segments/wrong-bounds.json",
            artifact_payload(
                channel_id="123456789012345678",
                channel_name="desk-chat",
                query="in:desk-chat after:2026-01-01 before:2026-01-04",
                start="2026-01-01",
                end="2026-01-01",
                total=0,
            ),
        )
        inspected = inspect_artifact(path, self.root)
        self.assertEqual(inspected.state, "invalid")
        self.assertIn("query_date_bounds_mismatch", inspected.errors)

    def test_adaptive_targeted_job_probes_then_captures_broad_window(self) -> None:
        target = job(
            "target",
            kind="targeted_search",
            query_prefix="in:desk-chat rejection block",
            output="raw/relevance_segments",
            span=2,
        )
        payload = self.jobs_payload([target])
        jobs_path = self.root / "working" / "relevance_jobs.json"
        jobs_path.parent.mkdir(parents=True)
        jobs_path.write_text(json.dumps(payload), encoding="utf-8")
        manifest, next_batch = build_outputs(
            self.root,
            payload,
            initial_state(),
            jobs_path=jobs_path,
            now=datetime(2026, 7, 20, 20, tzinfo=timezone.utc),
            max_messages=100,
            max_pages=4,
            batch_size=1,
        )
        self.assertEqual(manifest["summary"]["jobs"]["pending"], 1)
        self.assertEqual(next_batch["actions"][0]["action"], "count_probe")
        self.assertEqual(next_batch["actions"][0]["segment"]["start"], "2026-01-01")
        self.assertEqual(next_batch["actions"][0]["segment"]["end"], "2026-01-10")

        state = initial_state()
        record_count(
            state,
            target,
            start=date(2026, 1, 1),
            end=date(2026, 1, 10),
            total=24,
            pages=1,
            observed_at=datetime(2026, 7, 20, 20, tzinfo=timezone.utc),
        )
        _, next_batch = build_outputs(
            self.root,
            payload,
            state,
            jobs_path=jobs_path,
            now=datetime(2026, 7, 20, 20, tzinfo=timezone.utc),
            max_messages=100,
            max_pages=4,
            batch_size=1,
        )
        self.assertEqual(next_batch["actions"][0]["action"], "collect_segment")
        self.assertIn("within_threshold", next_batch["actions"][0]["strategy"])
        self.assertEqual(next_batch["actions"][0]["collector_call_args"]["spanDays"], 10)

    def test_oversize_full_window_splits_to_month_before_smaller_slices(self) -> None:
        target = job(
            "target",
            kind="targeted_search",
            query_prefix="in:desk-chat RB",
            start="2026-01-01",
            end="2026-03-20",
            span=7,
            output="raw/relevance_segments",
        )
        state = initial_state()
        record_count(
            state,
            target,
            start=date(2026, 1, 1),
            end=date(2026, 3, 20),
            total=500,
            pages=20,
            observed_at=datetime(2026, 7, 20, 20, tzinfo=timezone.utc),
        )
        payload = self.jobs_payload([target])
        jobs_path = self.root / "working" / "relevance_jobs.json"
        jobs_path.parent.mkdir(parents=True)
        jobs_path.write_text(json.dumps(payload), encoding="utf-8")
        _, next_batch = build_outputs(
            self.root,
            payload,
            state,
            jobs_path=jobs_path,
            now=datetime(2026, 7, 20, 20, tzinfo=timezone.utc),
            max_messages=100,
            max_pages=4,
            batch_size=1,
        )
        action = next_batch["actions"][0]
        self.assertEqual(action["action"], "count_probe")
        self.assertEqual(action["segment"]["start"], "2026-01-01")
        self.assertEqual(action["segment"]["end"], "2026-01-31")
        self.assertEqual(action["strategy"], "probe_monthly_slice")

    def test_global_throttle_emits_cooldown_with_exact_expiry(self) -> None:
        target = job("full", kind="full_capture_or_empty_verification")
        payload = self.jobs_payload([target])
        jobs_path = self.root / "working" / "relevance_jobs.json"
        jobs_path.parent.mkdir(parents=True)
        jobs_path.write_text(json.dumps(payload), encoding="utf-8")
        state = initial_state()
        event = record_throttle(
            state,
            scope="global",
            scope_key=None,
            occurred_at=datetime(2026, 7, 20, 20, tzinfo=timezone.utc),
            cooldown_seconds=300,
            reason="test_throttle",
            job_id=target["job_id"],
        )
        self.assertEqual(event["cooldown_until_utc"], "2026-07-20T20:05:00Z")
        _, next_batch = build_outputs(
            self.root,
            payload,
            state,
            jobs_path=jobs_path,
            now=datetime(2026, 7, 20, 20, 1, tzinfo=timezone.utc),
            max_messages=100,
            max_pages=4,
            batch_size=1,
        )
        self.assertEqual(next_batch["status"], "cooldown")
        self.assertEqual(next_batch["actions"], [])
        self.assertEqual(next_batch["active_cooldowns"][0]["remaining_seconds"], 240)

    def test_scan_does_not_modify_raw_artifacts(self) -> None:
        target = job("full", kind="full_capture_or_empty_verification", start="2026-01-01", end="2026-01-01", span=1)
        segment = make_segments(target)[0]
        path = self.write_artifact(
            segment["expected_relative_path"],
            artifact_payload(
                channel_id="123456789012345678",
                channel_name="desk-chat",
                query=segment["query"],
                start=segment["start"],
                end=segment["end"],
                total=1,
            ),
        )
        before = path.read_bytes()
        payload = self.jobs_payload([target])
        jobs_path = self.root / "working" / "relevance_jobs.json"
        jobs_path.parent.mkdir(parents=True)
        jobs_path.write_text(json.dumps(payload), encoding="utf-8")
        manifest, next_batch = build_outputs(
            self.root,
            payload,
            initial_state(),
            jobs_path=jobs_path,
            now=datetime(2026, 7, 20, 20, tzinfo=timezone.utc),
            max_messages=100,
            max_pages=4,
            batch_size=1,
        )
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(manifest["summary"]["artifacts"]["unique_message_ids_across_artifacts"], 1)
        self.assertEqual(next_batch["status"], "complete")


if __name__ == "__main__":
    unittest.main()
