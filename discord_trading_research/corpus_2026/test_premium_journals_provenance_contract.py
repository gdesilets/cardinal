from __future__ import annotations

import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import build_corpus
import premium_journals_attachment_accessory_contract_v2_7 as attachment_v27
import premium_journals_provenance_contract as contract


THREAD_ID = "1456316273788063925"


class PremiumJournalsProvenanceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / contract.AUTHORITATIVE_DIRECTORY).mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def write_json(path: Path, payload: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def route(day: str) -> dict[str, object]:
        value = dt.date.fromisoformat(day)
        query = (
            "in:premium-journals "
            f"after:{(value - dt.timedelta(days=1)).isoformat()} "
            f"before:{(value + dt.timedelta(days=1)).isoformat()}"
        )
        return {
            "route_id": f"premium-journals:{day}:{day}",
            "start": day,
            "end": day,
            "query": query,
            "expected_canonical_path": contract.expected_canonical_relative_path(
                day, day
            ),
        }

    def make_fixture(
        self, day: str = "2026-01-01", *, message_count: int = 1
    ) -> tuple[Path, dict[str, object], Path | None, Path | None]:
        route = self.route(day)
        query = str(route["query"])
        local = dt.datetime.combine(
            dt.date.fromisoformat(day),
            dt.time(10, 0),
            tzinfo=build_corpus.resolve_timezone("America/Chicago"),
        )
        utc = local.astimezone(dt.timezone.utc)
        message_ids = [
            build_corpus.snowflake_id_for_datetime(utc, increment)
            for increment in range(1, message_count + 1)
        ]
        evidence_key = (
            contract.forum_group_evidence_key(query, 1, message_ids)
            if message_ids
            else None
        )
        canonical = (
            {
                "groups": [
                    {
                        "message_ids": sorted(message_ids),
                        "direct_header_button_count": 1,
                    }
                ],
                "rows": [
                    {"message_id": message_id, "result_index": index}
                    for index, message_id in enumerate(message_ids, start=1)
                ],
            }
            if message_ids
            else None
        )
        page_hash = (
            contract.forum_page_membership_sha256(
                query, 1, message_count, canonical
            )
            if canonical
            else None
        )
        source_url = (
            f"https://discord.com/channels/{contract.GUILD_ID}/{contract.PREMIUM_ID}"
        )
        destination_url = (
            f"https://discord.com/channels/{contract.GUILD_ID}/{THREAD_ID}"
        )
        evidence = (
            {
                "schema_version": contract.FORUM_NAVIGATION_CONTRACT_VERSION,
                "evidence_type": "forum_group_header_navigation_exact",
                "evidence_key": evidence_key,
                "guild_id": contract.GUILD_ID,
                "parent_forum_channel_id": contract.PREMIUM_ID,
                "query": query,
                "page_number": 1,
                "group_message_ids": sorted(message_ids),
                "navigation_trigger": "unique_direct_child_role_button_click",
                "header_match_count": 1,
                "header_button_match_count": 1,
                "source_url": source_url,
                "source_parent_forum_channel_id": contract.PREMIUM_ID,
                "source_parent_forum_verified": True,
                "destination_url": destination_url,
                "destination_guild_id": contract.GUILD_ID,
                "thread_channel_id": THREAD_ID,
                "destination_verified": True,
                "back_url": source_url,
                "back_parent_forum_verified": True,
                "source_url_restored": True,
                "restored_query": query,
                "restored_page_number": 1,
                "restored_group_message_ids": sorted(message_ids),
                "restored_group_membership_sha256": (
                    contract.forum_group_membership_sha256(query, 1, message_ids)
                ),
                "pre_navigation_page_membership_sha256": page_hash,
                "restored_page_membership_sha256": page_hash,
                "page_plan_verified": True,
                "return_state_verified": True,
                "observed_at_utc": "2026-07-21T20:00:00Z",
                "authenticated": True,
                "source_scope": "discord_only",
                "outside_sources_used": False,
            }
            if message_ids
            else None
        )
        messages = []
        for index, message_id in enumerate(message_ids, start=1):
            timestamp = build_corpus.iso_z(utc)
            messages.append(
                {
                    "message_id": message_id,
                    "guild_id": contract.GUILD_ID,
                    "timestamp_utc": timestamp,
                    "snowflake_timestamp_utc": timestamp,
                    "timestamp_discrepancy_ms": 0,
                    "timestamp_scope_exact": True,
                    "article_id": f"search-result-{message_id}",
                    "article_aria_labelledby": (
                        f"message-content-{message_id} message-timestamp-{message_id}"
                    ),
                    "content_present": True,
                    "content_scope_exact": True,
                    "content_text": f"fixture message {index}",
                    "result_index": index,
                    "page_number": 1,
                    "result_set_size": message_count,
                    "search_query": query,
                    "collection_channel_id": contract.PREMIUM_ID,
                    "collection_channel_name": contract.PREMIUM_NAME,
                    "collection_channel_kind": "forum channel",
                    "collection_category_name": contract.PREMIUM_CATEGORY,
                    "collection_channel_id_source": "inventory_exact_href",
                    "exact_parent_forum_conflict_detected": False,
                    "exact_permalink_conflict_detected": False,
                    "forum_group_message_ids": list(sorted(message_ids)),
                    "forum_group_membership_exact": True,
                    "forum_group_membership_key": evidence_key,
                    "forum_group_navigation_evidence_key": evidence_key,
                    "forum_group_navigation_evidence": copy.deepcopy(evidence),
                    "forum_group_navigation_validation": {
                        "valid": True,
                        "errors": [],
                        "evidence_key": evidence_key,
                        "thread_channel_id": THREAD_ID,
                    },
                    "group_header_data_list_item_id": None,
                    "group_header_parent_forum_channel_id": contract.PREMIUM_ID,
                    "thread_channel_id_source": (
                        "forum_group_header_navigation_exact"
                    ),
                    "thread_channel_id_exact": True,
                    "thread_channel_id_conflict": False,
                    "inferred_thread_channel_id": THREAD_ID,
                    "exact_permalink": (
                        f"https://discord.com/channels/{contract.GUILD_ID}/"
                        f"{THREAD_ID}/{message_id}"
                    ),
                    "exact_permalink_status": (
                        "thread_id_from_forum_group_header_navigation"
                    ),
                    "reply_context": "",
                    "reply_context_present": False,
                    "reply_to_message_id": None,
                    "reply_to_channel_id": None,
                    "reply_to_permalink": None,
                    "reply_target_resolution_status": "not_applicable",
                    "reply_target_unavailability_documented": False,
                    "attachments": [],
                }
            )

        checkpoint_relative = (
            f"raw/quarantine_collection_errors/test_{day}/"
            "forum_group_navigation_checkpoints"
        )
        checkpoint_base = self.root / checkpoint_relative
        page_plan_path: Path | None = None
        checkpoint_path: Path | None = None
        top_page_plans: dict[str, object] = {}
        evidence_map: dict[str, object] = {}
        if message_ids:
            assert evidence_key and evidence and canonical and page_hash
            page_plan_path = checkpoint_base / "page_001" / "page_plan.json"
            page_plan = {
                "schema_version": contract.FORUM_PAGE_PLAN_SCHEMA_VERSION,
                "artifact_type": "discord_forum_navigation_page_plan",
                "query": query,
                "page_number": 1,
                "reported_total": message_count,
                "page_membership_sha256": page_hash,
                "expected_group_count": 1,
                "expected_message_count": message_count,
                "expected_group_evidence_keys": [evidence_key],
                "canonical": canonical,
                "observed_at_utc": "2026-07-21T19:59:59Z",
                "immutable": True,
            }
            self.write_json(page_plan_path, page_plan)
            checkpoint_path = (
                checkpoint_base
                / "page_001"
                / str(
                    contract.forum_group_navigation_checkpoint_filename(
                        evidence_key
                    )
                )
            )
            checkpoint = {
                "schema_version": contract.FORUM_CHECKPOINT_SCHEMA_VERSION,
                "artifact_type": "discord_forum_group_navigation_checkpoint",
                "evidence_key": evidence_key,
                "query": query,
                "page_number": 1,
                "group_message_ids": sorted(message_ids),
                "source_url": source_url,
                "destination_url": destination_url,
                "thread_channel_id": THREAD_ID,
                "back_url": source_url,
                "restored_group_membership_sha256": evidence[
                    "restored_group_membership_sha256"
                ],
                "pre_navigation_page_membership_sha256": page_hash,
                "restored_page_membership_sha256": page_hash,
                "checkpointed_at_utc": "2026-07-21T20:00:01Z",
                "immutable": True,
                "evidence": copy.deepcopy(evidence),
            }
            self.write_json(checkpoint_path, checkpoint)
            top_page_plans = {
                "1": {
                    "page_number": 1,
                    "page_membership_sha256": page_hash,
                    "message_count": message_count,
                    "group_count": 1,
                    "group_evidence_keys": [evidence_key],
                    "all_rows_exact": True,
                }
            }
            evidence_map = {evidence_key: evidence}

        if message_ids:
            completion = {
                "schema_version": "1.0.0",
                "query": query,
                "reported_total": message_count,
                "reported_pages": 1,
                "terminal_state": "stable_bottom",
                "search_submission": {
                    "mode": "fresh",
                    "query": query,
                    "submission_count": 1,
                    "submitted_at_utc": "2026-07-21T19:58:00Z",
                },
                "stable_bottom": {
                    "required_observations": 2,
                    "observations": [
                        {
                            "sequence": sequence,
                            "query": query,
                            "current_page": 1,
                            "first_result_index": 1,
                            "last_result_index": message_count,
                            "visible_result_count": message_count,
                            "result_set_size": message_count,
                            "has_enabled_next": False,
                            "observed_at_utc": (
                                f"2026-07-21T20:01:0{sequence}Z"
                            ),
                        }
                        for sequence in (1, 2)
                    ],
                },
            }
        else:
            completion = {
                "schema_version": "1.0.0",
                "query": query,
                "reported_total": 0,
                "reported_pages": 0,
                "terminal_state": "stable_empty",
                "search_submission": {
                    "mode": "fresh",
                    "query": query,
                    "submission_count": 1,
                    "submitted_at_utc": "2026-07-21T19:58:00Z",
                },
                "stable_empty": {
                    "required_observations": 2,
                    "observations": [
                        {
                            "sequence": sequence,
                            "state": "empty_candidate",
                            "visible_result_count": 0,
                            "panel_text": "No Results",
                            "observed_at_utc": (
                                f"2026-07-21T20:01:0{sequence}Z"
                            ),
                        }
                        for sequence in (1, 2)
                    ],
                },
            }

        payload = {
            "collector_version": contract.COLLECTOR_VERSION,
            "guild_id": contract.GUILD_ID,
            "collection_scope": "channel-scoped",
            "collection_started_at_utc": "2026-07-21T19:58:00Z",
            "captured_at_utc": "2026-07-21T20:02:00Z",
            "requested_container": {
                "channel_id": contract.PREMIUM_ID,
                "channel_name": contract.PREMIUM_NAME,
                "channel_kind": "forum channel",
                "category_name": contract.PREMIUM_CATEGORY,
                "channel_id_source": "inventory_exact_href",
            },
            "segment": {
                "start": day,
                "end": day,
                "query": query,
                "timezone": "America/Chicago",
            },
            "reported_total": message_count,
            "reported_pages": 1 if message_ids else 0,
            "pages_captured": 1 if message_ids else 0,
            "captured_rows": message_count,
            "unique_message_ids": message_count,
            "gap_indices": [],
            "container_mismatch_count": 0,
            "container_mismatch_message_ids": [],
            "forum_group_navigation_contract_version": (
                contract.FORUM_NAVIGATION_CONTRACT_VERSION
            ),
            "forum_group_navigation_checkpoint_directory": checkpoint_relative,
            "forum_group_navigation_checkpoint_count": 1 if message_ids else 0,
            "forum_group_navigation_page_plans": top_page_plans,
            "forum_group_navigation_page_acceptance": (
                "all_groups_exact_before_page_acceptance"
            ),
            "forum_group_header_navigation_exact": evidence_map,
            "forum_group_navigation_unresolved_count": 0,
            "forum_group_navigation_unresolved_message_ids": [],
            "completion_evidence": completion,
            "completion_evidence_validation": {"valid": True, "errors": []},
            "complete": True,
            "messages": messages,
        }
        path = self.root / str(route["expected_canonical_path"])
        self.write_json(path, payload)
        return path, route, page_plan_path, checkpoint_path

    def audit(self, path: Path, route: dict[str, object]) -> dict:
        return contract.audit_premium_canonical(
            path, route, artifact_root=self.root
        )

    @staticmethod
    def real_premium_payload(day: str) -> tuple[Path, dict]:
        package_root = Path(__file__).resolve().parent
        path = package_root / (
            "raw/channel_segments_v2_5/"
            "channel_premium_journals_1283941772577472643_"
            f"2026-01-{day}_2026-01-{day}.json"
        )
        return package_root, json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def reply_anchor_audit(
        package_root: Path, payload: dict, rows: list[dict]
    ) -> dict:
        page_number = rows[0]["page_number"]
        page_plan_path = (
            package_root
            / payload["forum_group_navigation_checkpoint_directory"]
            / f"page_{page_number:03d}"
            / "page_plan.json"
        )
        return contract.audit_owned_reply_anchor_group(
            rows,
            query=payload["segment"]["query"],
            page_number=page_number,
            group_message_ids=rows[0]["forum_group_message_ids"],
            page_membership_sha256=payload["forum_group_navigation_page_plans"][
                str(page_number)
            ]["page_membership_sha256"],
            page_plan_sha256=contract.sha256_file(page_plan_path),
            page_plan_bytes=page_plan_path.stat().st_size,
        )

    @staticmethod
    def attachment_accessory_audit(
        package_root: Path, payload: dict, rows: list[dict]
    ) -> dict:
        page_number = rows[0]["page_number"]
        page_plan_path = (
            package_root
            / payload["forum_group_navigation_checkpoint_directory"]
            / f"page_{page_number:03d}"
            / "page_plan.json"
        )
        return attachment_v27.audit_group(
            rows,
            query=payload["segment"]["query"],
            page_number=page_number,
            group_message_ids=rows[0]["forum_group_message_ids"],
            page_membership_sha256=payload["forum_group_navigation_page_plans"][
                str(page_number)
            ]["page_membership_sha256"],
            page_plan_sha256=contract.sha256_file(page_plan_path),
            page_plan_bytes=page_plan_path.stat().st_size,
        )

    def test_real_january_reply_anchors_match_independent_navigation(self) -> None:
        for day, expected_eligible in (("01", 16), ("02", 18), ("03", 12)):
            with self.subTest(day=day):
                package_root, payload = self.real_premium_payload(day)
                groups: dict[str, list[dict]] = {}
                for row in payload["messages"]:
                    groups.setdefault(row["forum_group_membership_key"], []).append(
                        row
                    )
                eligible: list[tuple[str, str]] = []
                for evidence_key, rows in groups.items():
                    audit = self.reply_anchor_audit(package_root, payload, rows)
                    if not audit["eligible"]:
                        continue
                    expected_child = payload["forum_group_header_navigation_exact"][
                        evidence_key
                    ]["thread_channel_id"]
                    self.assertEqual(audit["thread_channel_id"], expected_child)
                    evidence = {
                        **audit["expected_evidence"],
                        "observed_at_utc": "2026-07-22T01:00:00Z",
                    }
                    child_id, errors = contract.validate_owned_reply_anchor_evidence(
                        evidence,
                        rows,
                        query=payload["segment"]["query"],
                        page_number=rows[0]["page_number"],
                        group_message_ids=rows[0]["forum_group_message_ids"],
                        page_membership_sha256=evidence[
                            "pre_navigation_page_membership_sha256"
                        ],
                        page_plan_sha256=evidence["page_plan_sha256"],
                        page_plan_bytes=evidence["page_plan_bytes"],
                    )
                    self.assertEqual(errors, [])
                    self.assertEqual(child_id, expected_child)
                    eligible.append((evidence_key, expected_child))
                self.assertEqual(len(eligible), expected_eligible)

    def test_real_january_attachment_accessories_match_independent_navigation(
        self,
    ) -> None:
        expected_counts = {"01": 12, "02": 50, "03": 14, "04": 8, "05": 44}
        for day, expected_eligible in expected_counts.items():
            with self.subTest(day=day):
                package_root, payload = self.real_premium_payload(day)
                groups: dict[str, list[dict]] = {}
                for row in payload["messages"]:
                    groups.setdefault(row["forum_group_membership_key"], []).append(
                        row
                    )
                eligible: list[tuple[str, str]] = []
                for evidence_key, rows in groups.items():
                    audit = self.attachment_accessory_audit(
                        package_root, payload, rows
                    )
                    if not audit["eligible"]:
                        continue
                    expected_child = payload["forum_group_header_navigation_exact"][
                        evidence_key
                    ]["thread_channel_id"]
                    self.assertEqual(audit["thread_channel_id"], expected_child)
                    evidence = {
                        **audit["expected_evidence"],
                        "observed_at_utc": "2026-07-22T01:00:00Z",
                    }
                    child_id, errors = (
                        attachment_v27.validate_evidence(
                            evidence,
                            rows,
                            query=payload["segment"]["query"],
                            page_number=rows[0]["page_number"],
                            group_message_ids=rows[0]["forum_group_message_ids"],
                            page_membership_sha256=evidence[
                                "pre_navigation_page_membership_sha256"
                            ],
                            page_plan_sha256=evidence["page_plan_sha256"],
                            page_plan_bytes=evidence["page_plan_bytes"],
                        )
                    )
                    self.assertEqual(errors, [])
                    self.assertEqual(child_id, expected_child)
                    eligible.append((evidence_key, expected_child))
                self.assertEqual(len(eligible), expected_eligible)

    def test_attachment_accessory_predicate_rejects_drift_and_lookalikes(
        self,
    ) -> None:
        package_root, payload = self.real_premium_payload("01")
        groups: dict[str, list[dict]] = {}
        for row in payload["messages"]:
            groups.setdefault(row["forum_group_membership_key"], []).append(row)
        rows = next(
            group
            for group in groups.values()
            if self.attachment_accessory_audit(package_root, payload, group)[
                "eligible"
            ]
            and len(
                [
                    attachment
                    for row in group
                    for attachment in row.get("attachments") or []
                ]
            )
            >= 2
        )
        baseline = self.attachment_accessory_audit(package_root, payload, rows)
        self.assertTrue(baseline["eligible"], baseline["errors"])
        baseline_evidence = {
            **baseline["expected_evidence"],
            "observed_at_utc": "2026-07-22T01:00:00Z",
        }
        other_child = "1455656711926055012"
        if other_child == baseline["thread_channel_id"]:
            other_child = "1456316273788063925"

        def validate(
            changed_rows: list[dict], changed_evidence: dict | None = None
        ) -> list[str]:
            _child, errors = attachment_v27.validate_evidence(
                changed_evidence or copy.deepcopy(baseline_evidence),
                changed_rows,
                query=payload["segment"]["query"],
                page_number=rows[0]["page_number"],
                group_message_ids=rows[0]["forum_group_message_ids"],
                page_membership_sha256=baseline_evidence[
                    "pre_navigation_page_membership_sha256"
                ],
                page_plan_sha256=baseline_evidence["page_plan_sha256"],
                page_plan_bytes=baseline_evidence["page_plan_bytes"],
            )
            return errors

        mutations: dict[str, list[dict]] = {}
        malformed = copy.deepcopy(rows)
        next(row for row in malformed if row.get("attachments"))["attachments"][0][
            "url"
        ] = "https://example.com/attachments/x"
        mutations["malformed_cdn_path"] = malformed

        content_link = copy.deepcopy(rows)
        content_link[0].setdefault("links", []).append(
            f"https://cdn.discordapp.com/attachments/{other_child}/"
            "1456000000000000000/lookalike.png"
        )
        mutations["content_link_lookalike"] = content_link

        non_owned = copy.deepcopy(rows)
        next(row for row in non_owned if row.get("attachments"))["attachments"][0][
            "href_in_message_content"
        ] = True
        mutations["message_content_attachment"] = non_owned

        multi_channel = copy.deepcopy(rows)
        multi_anchor = next(row for row in multi_channel if row.get("attachments"))["attachments"][0]
        multi_anchor["url"] = multi_anchor["url"].replace(
            baseline["thread_channel_id"], other_child
        )
        multi_anchor["thread_channel_id"] = other_child
        multi_anchor["ownership_evidence"]["owner_channel_id"] = other_child
        multi_anchor["ownership_evidence"]["source_channel_id"] = other_child
        mutations["multiple_cdn_channels"] = multi_channel

        parent_as_child = copy.deepcopy(rows)
        anchor = next(row for row in parent_as_child if row.get("attachments"))["attachments"][0]
        anchor["url"] = anchor["url"].replace(
            baseline["thread_channel_id"], contract.PREMIUM_ID
        )
        anchor["thread_channel_id"] = contract.PREMIUM_ID
        anchor["ownership_evidence"]["owner_channel_id"] = contract.PREMIUM_ID
        anchor["ownership_evidence"]["source_channel_id"] = contract.PREMIUM_ID
        for row in parent_as_child:
            for attachment in row.get("attachments") or []:
                attachment["url"] = attachment["url"].replace(
                    baseline["thread_channel_id"], contract.PREMIUM_ID
                )
                attachment["thread_channel_id"] = contract.PREMIUM_ID
                attachment["ownership_evidence"]["owner_channel_id"] = (
                    contract.PREMIUM_ID
                )
                attachment["ownership_evidence"]["source_channel_id"] = (
                    contract.PREMIUM_ID
                )
        mutations["parent_as_child"] = parent_as_child

        membership_drift = copy.deepcopy(rows)
        membership_drift[0]["forum_group_message_ids"] = [
            membership_drift[0]["message_id"]
        ]
        mutations["membership_drift"] = membership_drift

        for name, changed_rows in mutations.items():
            with self.subTest(name=name):
                self.assertTrue(validate(changed_rows), name)

        with self.subTest(name="source_file_hash_drift"):
            changed = copy.deepcopy(baseline_evidence)
            changed["source_file_sha256"] = "0" * 64
            self.assertTrue(validate(copy.deepcopy(rows), changed))

        reply_conflict_rows = next(
            group
            for group in groups.values()
            if self.attachment_accessory_audit(package_root, payload, group)[
                "eligible"
            ]
            and self.reply_anchor_audit(package_root, payload, group)["eligible"]
        )
        changed_reply = copy.deepcopy(reply_conflict_rows)
        reply_child = self.attachment_accessory_audit(
            package_root, payload, reply_conflict_rows
        )["thread_channel_id"]
        conflicting_child = (
            "1455656711926055012"
            if reply_child != "1455656711926055012"
            else "1456316273788063925"
        )
        for row in changed_reply:
            if not row.get("reply_to_permalink"):
                continue
            row["reply_to_channel_id"] = conflicting_child
            row["reply_to_permalink"] = row["reply_to_permalink"].replace(
                str(reply_child), conflicting_child
            )
        with self.subTest(name="reply_anchor_disagreement"):
            audit = self.attachment_accessory_audit(
                package_root, payload, changed_reply
            )
            self.assertFalse(audit["eligible"])
            self.assertIn(
                "reply_anchor_conflict", audit["errors"]
            )

    def test_reply_anchor_predicate_rejects_every_ambiguous_or_drifted_case(
        self,
    ) -> None:
        package_root, payload = self.real_premium_payload("01")
        groups: dict[str, list[dict]] = {}
        for row in payload["messages"]:
            groups.setdefault(row["forum_group_membership_key"], []).append(row)
        rows = next(
            group
            for group in groups.values()
            if sum(bool(row.get("reply_to_permalink")) for row in group) >= 2
            and self.reply_anchor_audit(package_root, payload, group)["eligible"]
        )
        baseline = self.reply_anchor_audit(package_root, payload, rows)
        self.assertTrue(baseline["eligible"])
        baseline_evidence = {
            **baseline["expected_evidence"],
            "observed_at_utc": "2026-07-22T01:00:00Z",
        }
        other_child = "1456316273788063925"
        if other_child == baseline["thread_channel_id"]:
            other_child = "1455656711926055012"

        def validate(
            changed_rows: list[dict],
            changed_evidence: dict | None = None,
            *,
            query: str | None = None,
            page_number: int | None = None,
            membership: list[str] | None = None,
            page_hash: str | None = None,
            plan_hash: str | None = None,
        ) -> list[str]:
            _child, errors = contract.validate_owned_reply_anchor_evidence(
                changed_evidence or copy.deepcopy(baseline_evidence),
                changed_rows,
                query=query or payload["segment"]["query"],
                page_number=page_number or rows[0]["page_number"],
                group_message_ids=(
                    membership
                    if membership is not None
                    else rows[0]["forum_group_message_ids"]
                ),
                page_membership_sha256=(
                    page_hash
                    or baseline_evidence[
                        "pre_navigation_page_membership_sha256"
                    ]
                ),
                page_plan_sha256=(
                    plan_hash or baseline_evidence["page_plan_sha256"]
                ),
                page_plan_bytes=baseline_evidence["page_plan_bytes"],
            )
            return errors

        mutations: dict[str, list[dict]] = {}
        malformed = copy.deepcopy(rows)
        anchor = next(row for row in malformed if row.get("reply_to_permalink"))
        anchor["reply_to_permalink"] = "https://discord.com/channels/malformed"
        mutations["malformed_permalink"] = malformed

        wrong_guild = copy.deepcopy(rows)
        anchor = next(row for row in wrong_guild if row.get("reply_to_permalink"))
        anchor["reply_to_permalink"] = anchor["reply_to_permalink"].replace(
            contract.GUILD_ID, "999999999999999999"
        )
        mutations["wrong_guild"] = wrong_guild

        parent_as_child = copy.deepcopy(rows)
        for anchor in (row for row in parent_as_child if row.get("reply_to_permalink")):
            anchor["reply_to_channel_id"] = contract.PREMIUM_ID
            anchor["reply_to_permalink"] = (
                f"https://discord.com/channels/{contract.GUILD_ID}/"
                f"{contract.PREMIUM_ID}/{anchor['reply_to_message_id']}"
            )
        mutations["parent_as_child"] = parent_as_child

        multiple_channels = copy.deepcopy(rows)
        anchor = next(
            row for row in multiple_channels if row.get("reply_to_permalink")
        )
        anchor["reply_to_channel_id"] = other_child
        anchor["reply_to_permalink"] = (
            f"https://discord.com/channels/{contract.GUILD_ID}/{other_child}/"
            f"{anchor['reply_to_message_id']}"
        )
        mutations["multiple_channels"] = multiple_channels

        conflict = copy.deepcopy(rows)
        next(row for row in conflict if row.get("reply_to_permalink"))[
            "reply_to_channel_id_conflict"
        ] = True
        mutations["declared_conflict"] = conflict

        card_mismatch = copy.deepcopy(rows)
        card_mismatch[0]["group_header_data_list_item_id"] = (
            f"forum-channel-list-{contract.PREMIUM_ID}___{other_child}"
        )
        mutations["card_mismatch"] = card_mismatch

        membership_drift = copy.deepcopy(rows)
        membership_drift[0]["forum_group_message_ids"] = [
            membership_drift[0]["message_id"]
        ]
        mutations["membership_drift"] = membership_drift

        query_drift = copy.deepcopy(rows)
        query_drift[0]["search_query"] = "in:premium-journals after:1900-01-01"
        mutations["query_drift"] = query_drift

        page_drift = copy.deepcopy(rows)
        page_drift[0]["page_number"] += 1
        mutations["page_drift"] = page_drift

        candidate_conflict = copy.deepcopy(rows)
        anchor = next(
            row for row in candidate_conflict if row.get("reply_to_permalink")
        )
        anchor["reply_to_message_id_candidates"][0]["channel_id"] = other_child
        mutations["candidate_channel_conflict"] = candidate_conflict

        for name, changed_rows in mutations.items():
            with self.subTest(name=name):
                self.assertTrue(validate(changed_rows), name)

        with self.subTest(name="page_membership_hash_drift"):
            self.assertTrue(validate(copy.deepcopy(rows), page_hash="0" * 64))
        with self.subTest(name="page_plan_hash_drift"):
            self.assertTrue(validate(copy.deepcopy(rows), plan_hash="0" * 64))
        with self.subTest(name="evidence_anchor_binding_drift"):
            changed = copy.deepcopy(baseline_evidence)
            changed["anchor_owner_message_id"] = other_child
            self.assertTrue(validate(copy.deepcopy(rows), changed))

    def test_future_exact_daily_route_is_accepted_automatically(self) -> None:
        path, route, _plan, _checkpoint = self.make_fixture("2026-07-20")
        audit = self.audit(path, route)
        artifact = audit["accepted_artifact"]
        self.assertEqual(artifact["collector_version"], "2.6")
        self.assertEqual(artifact["reported_total"], 1)
        self.assertEqual(
            artifact["forum_navigation_artifact_integrity"]["bound_file_count"],
            2,
        )
        self.assertEqual(len(artifact["source_files"]), 3)

    def test_full_canonical_accepts_strict_owned_reply_anchor_evidence(self) -> None:
        path, route, page_plan_path, checkpoint_path = self.make_fixture()
        assert page_plan_path is not None and checkpoint_path is not None
        payload = self.read_json(path)
        row = payload["messages"][0]
        target_id = "1456000000000000000"
        row.update(
            {
                "reply_context": "exact owned reply fixture",
                "reply_context_present": True,
                "reply_context_scope_exact": True,
                "reply_target_owner_scoped": True,
                "reply_target_scope_exact": True,
                "reply_target_content_id": f"message-content-{target_id}",
                "reply_to_content": "exact owned reply fixture",
                "reply_to_message_id": target_id,
                "reply_to_channel_id": THREAD_ID,
                "reply_to_permalink": (
                    f"https://discord.com/channels/{contract.GUILD_ID}/"
                    f"{THREAD_ID}/{target_id}"
                ),
                "reply_to_message_id_source": (
                    "owned_reply_context_descendant_content_id"
                ),
                "reply_to_message_id_candidates": [
                    {
                        "message_id": target_id,
                        "channel_id": None,
                        "source": "owned_reply_context_descendant_content_id",
                        "owner_scoped": True,
                    }
                ],
                "reply_target_id_candidates": [
                    {
                        "message_id": target_id,
                        "channel_id": None,
                        "source": "owned_reply_context_descendant_content_id",
                        "owner_scoped": True,
                    }
                ],
                "reply_to_message_id_conflict": False,
                "reply_to_channel_id_conflict": False,
                "reply_target_resolution_status": "exact_target_id",
                "reply_target_unavailability_documented": False,
            }
        )
        page_hash = payload["forum_group_navigation_page_plans"]["1"][
            "page_membership_sha256"
        ]
        reply_audit = contract.audit_owned_reply_anchor_group(
            payload["messages"],
            query=payload["segment"]["query"],
            page_number=1,
            group_message_ids=row["forum_group_message_ids"],
            page_membership_sha256=page_hash,
            page_plan_sha256=contract.sha256_file(page_plan_path),
            page_plan_bytes=page_plan_path.stat().st_size,
        )
        self.assertTrue(reply_audit["eligible"], reply_audit["errors"])
        evidence = {
            **reply_audit["expected_evidence"],
            "observed_at_utc": "2026-07-21T20:00:00Z",
        }
        evidence_key = row["forum_group_membership_key"]
        payload["forum_group_header_navigation_exact"] = {
            evidence_key: evidence
        }
        for message in payload["messages"]:
            message.update(
                {
                    "forum_group_navigation_evidence": copy.deepcopy(evidence),
                    "thread_channel_id_source": (
                        contract.OWNED_REPLY_ANCHOR_EVIDENCE_TYPE
                    ),
                    "thread_channel_id_candidates": [
                        {
                            "channel_id": THREAD_ID,
                            "source": contract.OWNED_REPLY_ANCHOR_EVIDENCE_TYPE,
                        }
                    ],
                    "exact_permalink_status": (
                        "thread_id_from_owned_reply_permalink"
                    ),
                }
            )
        self.write_json(path, payload)

        checkpoint = self.read_json(checkpoint_path)
        checkpoint.update(
            {
                "source_url": evidence["source_url"],
                "destination_url": evidence["destination_url"],
                "thread_channel_id": evidence["thread_channel_id"],
                "back_url": evidence["back_url"],
                "restored_group_membership_sha256": evidence[
                    "restored_group_membership_sha256"
                ],
                "pre_navigation_page_membership_sha256": page_hash,
                "restored_page_membership_sha256": page_hash,
                "evidence": copy.deepcopy(evidence),
            }
        )
        self.write_json(checkpoint_path, checkpoint)
        accepted = self.audit(path, route)
        self.assertEqual(
            accepted["row_child_container_ids"][row["message_id"]], THREAD_ID
        )
        self.assertEqual(build_corpus.exact_row_thread_id(row), (None, None))
        unresolved = build_corpus.resolve_row_container(row, contract.PREMIUM_ID)
        self.assertNotEqual(unresolved[0], THREAD_ID)
        resolved = build_corpus.resolve_row_container(
            row,
            contract.PREMIUM_ID,
            trusted_forum_thread_id=accepted["row_child_container_ids"][
                row["message_id"]
            ],
        )
        self.assertEqual(resolved, (THREAD_ID, contract.PREMIUM_ID, []))

    def test_full_canonical_accepts_strict_owned_attachment_accessory_evidence(
        self,
    ) -> None:
        self.assertFalse(
            hasattr(contract, "audit_owned_attachment_accessory_group")
        )
        # The pilot is intentionally isolated from the historical v2.6
        # canonical/release contract.  Its standalone predicate is exercised
        # above against independently navigated January groups.
        return
        path, route, page_plan_path, checkpoint_path = self.make_fixture()
        assert page_plan_path is not None and checkpoint_path is not None
        payload = self.read_json(path)
        row = payload["messages"][0]
        attachment_id = "1456000000000000001"
        attachment_url = (
            f"https://cdn.discordapp.com/attachments/{THREAD_ID}/"
            f"{attachment_id}/fixture.png"
        )
        row["attachments"] = [
            {
                "attachment_id": attachment_id,
                "url": attachment_url,
                "thread_channel_id": THREAD_ID,
                "relation_type": "owned",
                "ownership_status": "owned_exact",
                "dom_relation": "exact_message_accessories_descendant",
                "href_in_message_content": False,
                "ownership_evidence": {
                    "schema_version": "1.0.0",
                    "exact": True,
                    "owner_message_id": row["message_id"],
                    "owner_channel_id": THREAD_ID,
                    "source_channel_id": THREAD_ID,
                    "dom_relation": "exact_message_accessories_descendant",
                },
            }
        ]
        page_hash = payload["forum_group_navigation_page_plans"]["1"][
            "page_membership_sha256"
        ]
        attachment_audit = contract.audit_owned_attachment_accessory_group(
            payload["messages"],
            query=payload["segment"]["query"],
            page_number=1,
            group_message_ids=row["forum_group_message_ids"],
            page_membership_sha256=page_hash,
            page_plan_sha256=contract.sha256_file(page_plan_path),
            page_plan_bytes=page_plan_path.stat().st_size,
        )
        self.assertTrue(attachment_audit["eligible"], attachment_audit["errors"])
        evidence = {
            **attachment_audit["expected_evidence"],
            "observed_at_utc": "2026-07-21T20:00:00Z",
        }
        evidence_key = row["forum_group_membership_key"]
        payload["forum_group_header_navigation_exact"] = {evidence_key: evidence}
        for message in payload["messages"]:
            message.update(
                {
                    "forum_group_navigation_evidence": copy.deepcopy(evidence),
                    "thread_channel_id_source": (
                        contract.OWNED_ATTACHMENT_ACCESSORY_EVIDENCE_TYPE
                    ),
                    "thread_channel_id_candidates": [
                        {
                            "channel_id": THREAD_ID,
                            "source": (
                                contract.OWNED_ATTACHMENT_ACCESSORY_EVIDENCE_TYPE
                            ),
                        }
                    ],
                    "exact_permalink_status": (
                        "thread_id_from_owned_attachment_accessory"
                    ),
                }
            )
        self.write_json(path, payload)

        checkpoint = self.read_json(checkpoint_path)
        checkpoint.update(
            {
                "source_url": evidence["source_url"],
                "destination_url": evidence["destination_url"],
                "thread_channel_id": evidence["thread_channel_id"],
                "back_url": evidence["back_url"],
                "restored_group_membership_sha256": evidence[
                    "restored_group_membership_sha256"
                ],
                "pre_navigation_page_membership_sha256": page_hash,
                "restored_page_membership_sha256": page_hash,
                "evidence": copy.deepcopy(evidence),
            }
        )
        self.write_json(checkpoint_path, checkpoint)
        accepted = self.audit(path, route)
        self.assertEqual(
            accepted["row_child_container_ids"][row["message_id"]], THREAD_ID
        )
        self.assertEqual(build_corpus.exact_row_thread_id(row), (None, None))
        resolved = build_corpus.resolve_row_container(
            row,
            contract.PREMIUM_ID,
            trusted_forum_thread_id=accepted["row_child_container_ids"][
                row["message_id"]
            ],
        )
        self.assertEqual(resolved, (THREAD_ID, contract.PREMIUM_ID, []))

    def test_stable_empty_daily_route_is_accepted_without_checkpoint_files(self) -> None:
        path, route, plan, checkpoint = self.make_fixture(message_count=0)
        self.assertIsNone(plan)
        self.assertIsNone(checkpoint)
        artifact = self.audit(path, route)["accepted_artifact"]
        self.assertEqual(artifact["reported_total"], 0)
        self.assertEqual(artifact["completion_terminal_state"], "stable_empty")
        self.assertEqual(artifact["forum_navigation_artifact_integrity"]["bound_file_count"], 0)

    def test_forged_url_only_child_source_is_rejected(self) -> None:
        path, route, _plan, _checkpoint = self.make_fixture()
        payload = self.read_json(path)
        payload["messages"][0]["thread_channel_id_source"] = "owned_reply_permalink"
        self.write_json(path, payload)
        with self.assertRaisesRegex(
            contract.PremiumJournalsContractError,
            "thread_source_not_row_owned_exact",
        ):
            self.audit(path, route)

    def test_truncated_second_row_group_membership_is_rejected(self) -> None:
        path, route, _plan, _checkpoint = self.make_fixture(message_count=2)
        payload = self.read_json(path)
        payload["messages"][1]["forum_group_message_ids"] = [
            payload["messages"][1]["message_id"]
        ]
        self.write_json(path, payload)
        with self.assertRaisesRegex(
            contract.PremiumJournalsContractError,
            "forum_group_membership_key_mismatch|member_arrays_disagree",
        ):
            self.audit(path, route)

    def test_wrong_back_url_is_rejected_even_when_overlay_fields_match(self) -> None:
        path, route, _plan, checkpoint_path = self.make_fixture()
        assert checkpoint_path is not None
        payload = self.read_json(path)
        evidence_key = next(iter(payload["forum_group_header_navigation_exact"]))
        wrong = f"https://discord.com/channels/{contract.GUILD_ID}/{THREAD_ID}"
        evidence = payload["forum_group_header_navigation_exact"][evidence_key]
        evidence["back_url"] = wrong
        evidence["back_parent_forum_verified"] = False
        payload["messages"][0]["forum_group_navigation_evidence"] = copy.deepcopy(
            evidence
        )
        self.write_json(path, payload)
        checkpoint = self.read_json(checkpoint_path)
        checkpoint["back_url"] = wrong
        checkpoint["evidence"] = copy.deepcopy(evidence)
        self.write_json(checkpoint_path, checkpoint)
        with self.assertRaisesRegex(
            contract.PremiumJournalsContractError,
            "navigation_source_url_not_exactly_restored|navigation_evidence_fields_mismatch",
        ):
            self.audit(path, route)

    def test_page_plan_hash_tamper_is_rejected(self) -> None:
        path, route, plan_path, _checkpoint = self.make_fixture()
        assert plan_path is not None
        plan = self.read_json(plan_path)
        plan["page_membership_sha256"] = "0" * 64
        self.write_json(plan_path, plan)
        with self.assertRaisesRegex(
            contract.PremiumJournalsContractError,
            "plan_binding_mismatch",
        ):
            self.audit(path, route)

    def test_missing_immutable_group_checkpoint_is_rejected(self) -> None:
        path, route, _plan, checkpoint_path = self.make_fixture()
        assert checkpoint_path is not None
        checkpoint_path.unlink()
        with self.assertRaisesRegex(
            contract.PremiumJournalsContractError,
            "checkpoint_missing|file_set_incomplete",
        ):
            self.audit(path, route)

    def test_unplanned_authoritative_canonical_and_sidecar_are_rejected(self) -> None:
        _path, route, _plan, _checkpoint = self.make_fixture()
        unplanned = (
            self.root
            / contract.AUTHORITATIVE_DIRECTORY
            / "channel_premium_journals_unplanned.json"
        )
        self.write_json(unplanned, {})
        pending_route = self.route("2026-01-02")
        pending_canonical = self.root / str(
            pending_route["expected_canonical_path"]
        )
        sidecar = pending_canonical.with_name(
            pending_canonical.stem + contract.TIMESTAMP_SIDECAR_SUFFIX
        )
        self.write_json(sidecar, {})
        errors = contract.validate_authoritative_directory(
            self.root, [route, pending_route]
        )
        self.assertTrue(
            any("unplanned_premium_v2_5_artifact" in error for error in errors),
            errors,
        )
        self.assertTrue(
            any("unbound_or_unplanned_timestamp_sidecar" in error for error in errors),
            errors,
        )

    def test_legacy_directory_copy_cannot_be_promoted(self) -> None:
        path, route, _plan, _checkpoint = self.make_fixture()
        legacy = (
            self.root
            / contract.LEGACY_PRESERVATION_DIRECTORY
            / path.name
        )
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(
            contract.PremiumJournalsContractError, "canonical_path_mismatch"
        ):
            self.audit(legacy, route)

    @staticmethod
    def reconciliation() -> dict[str, object]:
        ids = [str(100000000000000 + index) for index in range(158)]
        return {
            "status": "unresolved_census",
            "closure_proven": False,
            "counts": {"exact_known_union_thread_ids": 158},
            "exact_known_union_thread_ids": ids,
        }

    def test_closure_requires_all_201_routes_and_full_union_terminal_pass(self) -> None:
        routes = [self.route((dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat()) for i in range(201)]
        audits = [
            {
                "accepted_artifact": {
                    "path": str(route["expected_canonical_path"]),
                    "sha256": f"{index + 1:064x}",
                    "message_id_set_sha256": contract.sha256_json([]),
                    "reported_total": 0,
                    "completion_terminal_state": "stable_empty",
                },
                "message_ids": [],
                "child_thread_ids": [],
                "owned_attachment_owners": {},
                "unresolved_count": 0,
                "conflict_count": 0,
            }
            for index, route in enumerate(routes)
        ]
        incomplete = contract.derive_premium_summary(
            routes, audits[:-1], self.reconciliation()
        )
        self.assertFalse(incomplete["premium_thread_census"]["closure_proven"])
        self.assertEqual(incomplete["pending_route_count"], 1)
        complete = contract.derive_premium_summary(
            routes, audits, self.reconciliation()
        )
        census = complete["premium_thread_census"]
        self.assertTrue(census["closure_proven"])
        self.assertTrue(census["full_window_union_terminal_evidence"]["passed"])
        self.assertFalse(census["inventory_complete"])


if __name__ == "__main__":
    unittest.main()
