from __future__ import annotations

import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import build_corpus
import premium_journals_attachment_accessory_contract_v2_7 as accessory
import premium_journals_provenance_contract as v26
import premium_journals_provenance_contract_v2_7 as v27
import premium_journals_v2_7_schedule as schedule
import premium_journals_v2_7_integration as integration
from qa.validate_premium_journals_v2_7 import validate_one_segment


GUILD = v26.GUILD_ID
PARENT = v26.PREMIUM_ID
CHILD = "1456316273788063925"
OWNER = "1456000000000000000"
ATTACHMENT = "1456000000000000001"
QUERY = "in:premium-journals after:2026-01-07 before:2026-01-09"


def group() -> list[dict]:
    return [{
        "message_id": OWNER, "search_query": QUERY, "page_number": 1,
        "forum_group_message_ids": [OWNER], "forum_group_membership_exact": True,
        "forum_group_membership_key": v26.forum_group_evidence_key(QUERY, 1, [OWNER]),
        "reply_target_resolution_status": "not_applicable", "attachments": [{
            "attachment_id": ATTACHMENT,
            "url": f"https://cdn.discordapp.com/attachments/{CHILD}/{ATTACHMENT}/fixture.png",
            "thread_channel_id": CHILD, "relation_type": "owned", "ownership_status": "owned_exact",
            "dom_relation": "exact_message_accessories_descendant", "href_in_message_content": False,
            "ownership_evidence": {"schema_version": "1.0.0", "exact": True, "owner_message_id": OWNER, "owner_channel_id": CHILD,
              "source_channel_id": CHILD, "dom_relation": "exact_message_accessories_descendant"},
        }],
    }]


def reply_group() -> list[dict]:
    rows = group()
    row = rows[0]
    target = str(int(OWNER) + 20)
    row["attachments"] = []
    row.update({
        "reply_context_present": True, "reply_context_scope_exact": True,
        "reply_target_owner_scoped": True, "reply_target_scope_exact": True,
        "reply_to_message_id": target, "reply_to_channel_id": CHILD,
        "reply_to_permalink": f"https://discord.com/channels/{GUILD}/{CHILD}/{target}",
        "reply_to_message_id_source": "owned_reply_context_descendant_content_id",
        "reply_target_content_id": f"message-content-{target}",
        "reply_to_message_id_conflict": False, "reply_to_channel_id_conflict": False,
        "reply_target_resolution_status": "exact_target_id",
        "reply_target_unavailability_documented": False,
        "reply_to_message_id_candidates": [{"message_id": target, "channel_id": None,
            "source": "owned_reply_context_descendant_content_id", "owner_scoped": True}],
        "reply_target_id_candidates": [{"message_id": target, "channel_id": None,
            "source": "owned_reply_context_descendant_content_id", "owner_scoped": True}],
    })
    return rows


class PremiumV27Tests(unittest.TestCase):
    def audit(self, rows: list[dict]) -> dict:
        return v27.audit_group(rows, query=QUERY, page_number=1, group_message_ids=[OWNER],
            page_membership_sha256="a" * 64, page_plan_sha256="b" * 64, page_plan_bytes=100,
            current_source_url=f"https://discord.com/channels/{GUILD}/{PARENT}")

    def test_exact_owned_accessory_is_a_future_only_direct_candidate(self) -> None:
        audit = self.audit(group())
        self.assertTrue(audit["eligible"], audit["errors"])
        evidence = {**audit["expected_evidence"], "observed_at_utc": "2026-07-22T00:00:00Z"}
        child, errors = v27.validate_evidence(evidence, group(), query=QUERY, page_number=1,
            group_message_ids=[OWNER], page_membership_sha256="a" * 64, page_plan_sha256="b" * 64,
            page_plan_bytes=100, current_source_url=f"https://discord.com/channels/{GUILD}/{PARENT}")
        self.assertEqual([], errors)
        self.assertEqual(CHILD, child)
        self.assertEqual(v27.build_checkpoint(evidence, "2026-07-22T00:00:01Z")["artifact_type"], v27.CHECKPOINT_TYPE)

    def test_rejects_untrusted_source_parent_and_disagreeing_candidates(self) -> None:
        wrong_source = self.audit(group())
        self.assertTrue(wrong_source["eligible"])
        rows = group(); rows[0]["attachments"][0]["url"] = f"https://cdn.discordapp.com/attachments/{PARENT}/{ATTACHMENT}/fixture.png"
        rows[0]["attachments"][0]["thread_channel_id"] = PARENT
        rows[0]["attachments"][0]["ownership_evidence"].update({"owner_channel_id": PARENT, "source_channel_id": PARENT})
        self.assertFalse(self.audit(rows)["eligible"])
        evidence = wrong_source["expected_evidence"]
        _child, errors = v27.validate_evidence({**evidence, "current_source_url": f"https://discord.com/channels/{GUILD}/{CHILD}", "observed_at_utc": "2026-07-22T00:00:00Z"}, group(), query=QUERY, page_number=1, group_message_ids=[OWNER], page_membership_sha256="a" * 64, page_plan_sha256="b" * 64, page_plan_bytes=100, current_source_url=f"https://discord.com/channels/{GUILD}/{CHILD}")
        self.assertTrue(errors)

    def test_shared_python_javascript_attachment_signal_fixtures_fail_closed(self) -> None:
        fixture_path = Path(__file__).resolve().parent.parent / "premium_v2_7_direct_parity_fixtures.json"
        fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
        baseline = self.audit(reply_group())
        self.assertTrue(baseline["eligible"], baseline["errors"])
        for fixture in fixtures["reply_cases"]:
            rows = reply_group()
            rows[0][fixture["field"]] = copy.deepcopy(fixture["value"])
            audit = self.audit(rows)
            self.assertEqual(fixture["expected_eligible"], audit["eligible"], fixture["name"])
        for fixture in fixtures["accessory_cases"]:
            rows = group()
            if field := fixture.get("delete_ownership_evidence_field"):
                del rows[0]["attachments"][0]["ownership_evidence"][field]
            if field := fixture.get("set_row_field"):
                rows[0][field] = copy.deepcopy(fixture["value"])
            audit = self.audit(rows)
            self.assertEqual(fixture["expected_eligible"], audit["eligible"], fixture["name"])

    def test_rejects_alternate_guild_and_parent_scope(self) -> None:
        other_guild = "1167376964680691733"
        other_parent = "1283941772577472644"
        kwargs = dict(query=QUERY, page_number=1, group_message_ids=[OWNER],
            page_membership_sha256="a" * 64, page_plan_sha256="b" * 64,
            page_plan_bytes=100, guild_id=other_guild,
            parent_forum_channel_id=other_parent)
        direct = v27.audit_group(group(), **kwargs,
            current_source_url=f"https://discord.com/channels/{other_guild}/{other_parent}")
        self.assertFalse(direct["eligible"])
        self.assertIn("guild_not_authorized_premium_scope", direct["errors"])
        self.assertIn("parent_forum_not_authorized_premium_scope", direct["errors"])
        accessory_audit = accessory.audit_group(group(), **kwargs)
        self.assertFalse(accessory_audit["eligible"])
        self.assertIn("guild_not_authorized_premium_scope", accessory_audit["errors"])
        self.assertIn("parent_forum_not_authorized_premium_scope", accessory_audit["errors"])
        rows = group()
        rows[0]["search_query"] = " "
        rows[0]["forum_group_membership_key"] = None
        blank = v27.audit_group(rows, query=" ", page_number=1,
            group_message_ids=[OWNER], page_membership_sha256="a" * 64,
            page_plan_sha256="b" * 64, page_plan_bytes=100,
            current_source_url=f"https://discord.com/channels/{GUILD}/{PARENT}")
        self.assertFalse(blank["eligible"])
        malformed_bytes = v27.audit_group(group(), query=QUERY, page_number=1,
            group_message_ids=[OWNER], page_membership_sha256="a" * 64,
            page_plan_sha256="b" * 64, page_plan_bytes="100",  # type: ignore[arg-type]
            current_source_url=f"https://discord.com/channels/{GUILD}/{PARENT}")
        self.assertFalse(malformed_bytes["eligible"])

    def test_schedule_is_explicit_future_and_disabled(self) -> None:
        route = schedule.build_disabled_route()
        self.assertEqual([], schedule.validate_route(route))
        historical = copy.deepcopy(route); historical["start"] = historical["end"] = "2026-01-07"
        self.assertIn("v2_7_route_not_future_single_day", schedule.validate_route(historical))
        enabled = copy.deepcopy(route); enabled["live_collection_enabled"] = True
        self.assertIn("v2_7_live_collection_must_remain_disabled", schedule.validate_route(enabled))
        malformed = copy.deepcopy(route); malformed["start"] = malformed["end"] = "not-a-date"
        self.assertIn("v2_7_route_date_invalid", schedule.validate_route(malformed))


class PremiumV27GenericQATests(unittest.TestCase):
    @staticmethod
    def write(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def fixture(self, root: Path, *, method: str = "direct_consensus_v2_7") -> tuple[Path, dict, Path, Path]:
        route = schedule.build_disabled_route("2026-01-08")
        local = dt.datetime(2026, 1, 8, 10, 0, tzinfo=build_corpus.resolve_timezone("America/Chicago"))
        message_id = build_corpus.snowflake_id_for_datetime(local.astimezone(dt.timezone.utc), 1)
        query = route["query"]
        key = v26.forum_group_evidence_key(query, 1, [message_id])
        row = {
            "message_id": message_id, "result_index": 1, "page_number": 1,
            "result_set_size": 1, "search_query": query,
            "collection_channel_id": v26.PREMIUM_ID,
            "collection_channel_name": v26.PREMIUM_NAME,
            "collection_channel_kind": "forum channel",
            "collection_category_name": v26.PREMIUM_CATEGORY,
            "collection_channel_id_source": "inventory_exact_href",
            "content_scope_exact": True,
            "exact_parent_forum_conflict_detected": False,
            "exact_permalink_conflict_detected": False,
            "forum_group_message_ids": [message_id],
            "forum_group_membership_exact": True,
            "forum_group_membership_key": key,
            "group_header_data_list_item_id": None,
            "group_header_parent_forum_channel_id": v26.PREMIUM_ID,
            "reply_context": "", "reply_context_present": False,
            "reply_to_message_id": None, "reply_to_channel_id": None,
            "reply_to_permalink": None, "reply_to_message_id_source": None,
            "reply_target_resolution_status": "not_applicable",
            "reply_target_unavailability_documented": False,
            "attachments": [],
        }
        if method == "direct_consensus_v2_7":
            attachment_id = str(int(message_id) + 1)
            row["attachments"] = [{
                "attachment_id": attachment_id,
                "url": f"https://cdn.discordapp.com/attachments/{CHILD}/{attachment_id}/fixture.png",
                "thread_channel_id": CHILD, "relation_type": "owned",
                "ownership_status": "owned_exact",
                "dom_relation": "exact_message_accessories_descendant",
                "href_in_message_content": False,
                "ownership_evidence": {"schema_version": "1.0.0", "exact": True,
                    "owner_message_id": message_id, "owner_channel_id": CHILD,
                    "source_channel_id": CHILD,
                    "dom_relation": "exact_message_accessories_descendant"},
            }]
        canonical = {"groups": [{"message_ids": [message_id], "direct_header_button_count": 1}],
                     "rows": [{"message_id": message_id, "result_index": 1}]}
        page_hash = v26.forum_page_membership_sha256(query, 1, 1, canonical)
        checkpoint_relative = v27.expected_checkpoint_relative_directory("2026-01-08")
        page_dir = root / checkpoint_relative / "page_001"
        plan_path = page_dir / "page_plan.json"
        plan = {"schema_version": v26.FORUM_PAGE_PLAN_SCHEMA_VERSION,
            "artifact_type": "discord_forum_navigation_page_plan", "query": query,
            "page_number": 1, "reported_total": 1,
            "page_membership_sha256": page_hash, "expected_group_count": 1,
            "expected_message_count": 1, "expected_group_evidence_keys": [key],
            "canonical": canonical, "observed_at_utc": "2026-07-22T01:00:00Z",
            "immutable": True}
        self.write(plan_path, plan)
        source = f"https://discord.com/channels/{v26.GUILD_ID}/{v26.PREMIUM_ID}"
        if method == "direct_consensus_v2_7":
            audit = v27.audit_group([row], query=query, page_number=1,
                group_message_ids=[message_id], page_membership_sha256=page_hash,
                page_plan_sha256=v26.sha256_file(plan_path),
                page_plan_bytes=plan_path.stat().st_size, current_source_url=source)
            self.assertTrue(audit["eligible"], audit["errors"])
            evidence = {**audit["expected_evidence"], "observed_at_utc": "2026-07-22T01:01:00Z"}
            checkpoint = v27.build_checkpoint(evidence, "2026-07-22T01:01:01Z")
            checkpoint_path = page_dir / str(v27.checkpoint_filename(key))
            row_source = v27.EVIDENCE_TYPE
            permalink_status = "thread_id_from_direct_candidate_consensus"
            direct_map, header_map = {key: evidence}, {}
            checkpoint_role = "forum_group_direct_consensus_checkpoint"
        else:
            evidence = {
                "schema_version": v26.FORUM_NAVIGATION_CONTRACT_VERSION,
                "evidence_type": "forum_group_header_navigation_exact",
                "evidence_key": key, "guild_id": v26.GUILD_ID,
                "parent_forum_channel_id": v26.PREMIUM_ID, "query": query,
                "page_number": 1, "group_message_ids": [message_id],
                "navigation_trigger": "unique_direct_child_role_button_click",
                "header_match_count": 1, "header_button_match_count": 1,
                "source_url": source, "source_parent_forum_channel_id": v26.PREMIUM_ID,
                "source_parent_forum_verified": True,
                "destination_url": f"https://discord.com/channels/{v26.GUILD_ID}/{CHILD}",
                "destination_guild_id": v26.GUILD_ID, "thread_channel_id": CHILD,
                "destination_verified": True, "back_url": source,
                "back_parent_forum_verified": True, "source_url_restored": True,
                "restored_query": query, "restored_page_number": 1,
                "restored_group_message_ids": [message_id],
                "restored_group_membership_sha256": v26.forum_group_membership_sha256(query, 1, [message_id]),
                "pre_navigation_page_membership_sha256": page_hash,
                "restored_page_membership_sha256": page_hash,
                "page_plan_verified": True, "return_state_verified": True,
                "observed_at_utc": "2026-07-22T01:01:00Z", "authenticated": True,
                "source_scope": "discord_only", "outside_sources_used": False,
            }
            checkpoint = {"schema_version": v26.FORUM_CHECKPOINT_SCHEMA_VERSION,
                "artifact_type": "discord_forum_group_navigation_checkpoint",
                "evidence_key": key, "query": query, "page_number": 1,
                "group_message_ids": [message_id], "source_url": source,
                "destination_url": evidence["destination_url"],
                "thread_channel_id": CHILD, "back_url": source,
                "restored_group_membership_sha256": evidence["restored_group_membership_sha256"],
                "pre_navigation_page_membership_sha256": page_hash,
                "restored_page_membership_sha256": page_hash,
                "checkpointed_at_utc": "2026-07-22T01:01:01Z",
                "immutable": True, "evidence": evidence}
            checkpoint_path = page_dir / str(v26.forum_group_navigation_checkpoint_filename(key))
            row_source = "forum_group_header_navigation_exact"
            permalink_status = "thread_id_from_forum_group_header_navigation"
            direct_map, header_map = {}, {key: evidence}
            checkpoint_role = "forum_group_header_navigation_checkpoint"
        self.write(checkpoint_path, checkpoint)
        row.update({
            "forum_group_navigation_evidence_key": key,
            "forum_group_navigation_evidence": copy.deepcopy(evidence),
            "forum_group_navigation_validation": {"valid": True, "errors": [],
                "evidence_key": key, "thread_channel_id": CHILD},
            "thread_channel_id_source": row_source, "thread_channel_id_exact": True,
            "thread_channel_id_conflict": False,
            "inferred_thread_channel_id": CHILD,
            "exact_permalink": f"https://discord.com/channels/{v26.GUILD_ID}/{CHILD}/{message_id}",
            "exact_permalink_status": permalink_status,
        })
        plan_source = {"role": "forum_navigation_page_plan",
            "path": plan_path.relative_to(root).as_posix(), "sha256": v26.sha256_file(plan_path),
            "bytes": plan_path.stat().st_size}
        checkpoint_source = {"role": checkpoint_role,
            "path": checkpoint_path.relative_to(root).as_posix(),
            "sha256": v26.sha256_file(checkpoint_path), "bytes": checkpoint_path.stat().st_size}
        sources = sorted([plan_source, checkpoint_source], key=lambda item: (item["path"], item["role"]))
        record = {"method": method, "evidence_key": key, "page_number": 1,
            "thread_channel_id": CHILD, "current_source_url": source,
            "page_plan_path": plan_source["path"], "page_membership_sha256": page_hash,
            "page_plan_sha256": plan_source["sha256"], "page_plan_bytes": plan_source["bytes"],
            "checkpoint_path": checkpoint_source["path"],
            "checkpoint_sha256": checkpoint_source["sha256"],
            "checkpoint_bytes": checkpoint_source["bytes"], "evidence": evidence}
        completion = {"schema_version": "1.0.0", "query": query,
            "reported_total": 1, "reported_pages": 1, "terminal_state": "stable_bottom",
            "search_submission": {"mode": "fresh", "query": query,
                "submission_count": 1, "submitted_at_utc": "2026-07-22T00:59:00Z"},
            "stable_bottom": {"required_observations": 2, "observations": [
                {"sequence": sequence, "query": query, "current_page": 1,
                 "first_result_index": 1, "last_result_index": 1,
                 "visible_result_count": 1, "result_set_size": 1,
                 "has_enabled_next": False,
                 "observed_at_utc": f"2026-07-22T01:02:0{sequence}Z"}
                for sequence in (1, 2)]}}
        payload = {"collector_version": v27.COLLECTOR_VERSION,
            "provenance_version": v27.PROVENANCE_VERSION, "guild_id": v26.GUILD_ID,
            "collection_scope": "channel-scoped", "complete": True,
            "authenticated": True, "source_scope": "discord_only",
            "outside_sources_used": False,
            "collection_started_at_utc": "2026-07-22T00:58:00Z",
            "captured_at_utc": "2026-07-22T01:03:00Z",
            "requested_container": {"channel_id": v26.PREMIUM_ID,
                "channel_name": v26.PREMIUM_NAME, "channel_kind": "forum channel",
                "category_name": v26.PREMIUM_CATEGORY,
                "channel_id_source": "inventory_exact_href"},
            "observed_container": {"channel_id": v26.PREMIUM_ID,
                "channel_name": v26.PREMIUM_NAME, "channel_kind": "forum channel",
                "category_name": v26.PREMIUM_CATEGORY, "source_url": source},
            "segment": {"start": "2026-01-08", "end": "2026-01-08",
                "query": query, "timezone": "America/Chicago"},
            "reported_total": 1, "reported_pages": 1, "pages_captured": 1,
            "captured_rows": 1, "unique_message_ids": 1, "gap_indices": [],
            "container_mismatch_count": 0, "container_mismatch_message_ids": [],
            "forum_group_navigation_unresolved_count": 0,
            "forum_group_navigation_unresolved_message_ids": [],
            "forum_group_navigation_page_acceptance": "all_groups_exact_before_page_acceptance",
            "forum_group_navigation_checkpoint_directory": checkpoint_relative,
            "forum_group_navigation_checkpoint_count": 1,
            "forum_group_navigation_page_plans": {"1": {"page_number": 1,
                "page_membership_sha256": page_hash, "message_count": 1,
                "group_count": 1, "group_evidence_keys": [key], "all_rows_exact": True}},
            "forum_group_direct_consensus_exact": direct_map,
            "forum_group_header_navigation_exact": header_map,
            "forum_group_resolution_methods": {key: method},
            "forum_group_resolution_records": {key: record},
            "forum_group_resolution_source_files": sources,
            "forum_group_resolution_source_file_set_sha256": v26.sha256_json(sources),
            "completion_evidence_validation": {"valid": True, "errors": []},
            "completion_evidence": completion, "messages": [row]}
        payload["reply_provenance_integrity"] = v26._reply_semantic_audit(payload["messages"])[0]
        payload["attachment_provenance_integrity"] = v26._attachment_semantic_audit(payload["messages"])[0]
        canonical_path = root / route["expected_canonical_path"]
        self.write(canonical_path, payload)
        return canonical_path, route, plan_path, checkpoint_path

    def test_full_direct_and_header_canonicals_pass(self) -> None:
        for method in ("direct_consensus_v2_7", "header_navigation_v2_6"):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                path, route, _, _ = self.fixture(root, method=method)
                self.assertEqual([], validate_one_segment(path, route, root))

    def test_exploit_mutations_fail_closed(self) -> None:
        mutations = (
            "truncated_messages", "extra_evidence_key", "bad_terminal",
            "plan_tamper", "checkpoint_tamper", "source_hash_tamper",
            "partition_drift", "resolution_record_drift", "checkpoint_root_drift",
            "declared_summary_drift", "discord_boundary_drift",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                path, route, plan_path, checkpoint_path = self.fixture(root)
                payload = json.loads(path.read_text(encoding="utf-8"))
                key = next(iter(payload["forum_group_resolution_methods"]))
                if mutation == "truncated_messages": payload["messages"] = []
                elif mutation == "extra_evidence_key": payload["forum_group_direct_consensus_exact"]["forum-group-navigation:" + "0" * 64] = copy.deepcopy(payload["forum_group_direct_consensus_exact"][key])
                elif mutation == "bad_terminal": payload["completion_evidence"]["terminal_state"] = "provisional"
                elif mutation == "source_hash_tamper": payload["forum_group_resolution_source_files"][0]["sha256"] = "0" * 64
                elif mutation == "partition_drift": payload["messages"][0]["forum_group_message_ids"] = [str(int(payload["messages"][0]["message_id"]) + 99)]
                elif mutation == "resolution_record_drift": payload["forum_group_resolution_records"][key]["checkpoint_sha256"] = "0" * 64
                elif mutation == "checkpoint_root_drift": payload["forum_group_navigation_checkpoint_directory"] = "raw/channel_segments_v2_5"
                elif mutation == "declared_summary_drift": payload["attachment_provenance_integrity"]["attachment_occurrence_count"] += 1
                elif mutation == "discord_boundary_drift": payload["outside_sources_used"] = True
                elif mutation == "plan_tamper":
                    plan = json.loads(plan_path.read_text(encoding="utf-8")); plan["expected_message_count"] = 2; self.write(plan_path, plan)
                elif mutation == "checkpoint_tamper":
                    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")); checkpoint["thread_channel_id"] = v26.PREMIUM_ID; self.write(checkpoint_path, checkpoint)
                self.write(path, payload)
                self.assertTrue(validate_one_segment(path, route, root), mutation)

    def test_arbitrary_header_exact_flag_and_wrong_back_fail_generic_qa(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, route, _, checkpoint_path = self.fixture(root, method="header_navigation_v2_6")
            payload = json.loads(path.read_text(encoding="utf-8")); key = next(iter(payload["forum_group_header_navigation_exact"]))
            payload["forum_group_header_navigation_exact"][key] = {"exact": True}
            self.write(path, payload)
            self.assertTrue(validate_one_segment(path, route, root))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, route, _, checkpoint_path = self.fixture(root, method="header_navigation_v2_6")
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["evidence"]["back_url"] = f"https://discord.com/channels/{v26.GUILD_ID}/{CHILD}"
            self.write(checkpoint_path, checkpoint)
            self.assertTrue(validate_one_segment(path, route, root))

    def test_shadow_integration_never_promotes_and_blocks_double_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, route, _, _ = self.fixture(root)
            audit = integration.audit_shadow_candidate(root, route, path)
            self.assertTrue(audit["canonical_qa_passed"], audit)
            self.assertFalse(audit["promotion_allowed"])
            self.assertFalse(audit["no_double_authority_passed"])
            self.assertIn("active_v2_6_schedule_not_supplied", audit["authority_errors"])
            audit = integration.audit_shadow_candidate(
                root, route, path, active_v2_6_routes=[]
            )
            self.assertTrue(audit["no_double_authority_passed"], audit)
            overlapping = [{"start": "2026-01-08", "end": "2026-01-08"}]
            audit = integration.audit_shadow_candidate(
                root, route, path, active_v2_6_routes=overlapping
            )
            self.assertFalse(audit["no_double_authority_passed"])
