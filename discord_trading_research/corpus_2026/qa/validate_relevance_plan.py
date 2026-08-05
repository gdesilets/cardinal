#!/usr/bin/env python3
"""Validate and expand the Discord-only server relevance collection plan.

The validator is intentionally standard-library-only.  It does not browse, touch
raw segment files, or modify any legacy artifact.  Expanded jobs map one-for-one
to ``collectDateRange`` arguments in ``discord_browser_collector.mjs``.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
DEFAULT_PLAN = HERE.parent / "relevance_collection_plan.json"
ALLOWED_POLICIES = {
    "full_capture",
    "verified_empty_full_window",
    "targeted_search_plus_residual_audit",
}
TARGETED_CHANNEL_IDS: set[str] = set()
EXPECTED_POLICY_COUNTS = {
    "full_capture": 16,
    "verified_empty_full_window": 22,
    "targeted_search_plus_residual_audit": 0,
}
REQUIRED_COLLECTOR_OPTION_KEYS = {
    "prefix",
    "scope",
    "channelId",
    "channelName",
    "channelKind",
    "categoryName",
    "channelIdSource",
    "checkpointEvery",
    "pageDelayMs",
    "reuseActiveSearch",
    "maxAttempts",
}
FULL_CAPTURE_RUNTIME_OPTIONS = {
    "checkpointEvery": 5,
    "pageDelayMs": 1200,
    "reuseActiveSearch": True,
}
DISCORD_ID_RE = re.compile(r"^\d{15,22}$")
DATE_OPERATOR_RE = re.compile(r"(?:^|\s)(?:after|before):\d{4}-\d{2}-\d{2}(?:\s|$)", re.I)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return normalized or "channel"


def inclusive_day_count(start_text: str, end_text: str) -> int:
    start = date.fromisoformat(start_text)
    end = date.fromisoformat(end_text)
    return (end - start).days + 1


def segment_count(start_text: str, end_text: str, span_days: int) -> int:
    return math.ceil(inclusive_day_count(start_text, end_text) / span_days)


def _collector_options(
    channel: dict[str, Any],
    prefix: str,
    defaults: dict[str, Any],
) -> dict[str, Any]:
    return {
        "prefix": prefix,
        "scope": defaults["scope"],
        "channelId": channel["channel_id"],
        "channelName": channel["name"],
        "channelKind": channel["kind"],
        "categoryName": channel["category_name"],
        "channelIdSource": channel.get("channel_id_source") or "inventory_exact_href",
        "checkpointEvery": defaults["checkpointEvery"],
        "pageDelayMs": defaults["pageDelayMs"],
        "reuseActiveSearch": defaults["reuseActiveSearch"],
        "maxAttempts": defaults["maxAttempts"],
    }


def _job(
    *,
    job_id: str,
    start: str,
    end: str,
    output_directory: str,
    query_prefix: str,
    span_days: int,
    collector_options: dict[str, Any],
    scheduler_options: dict[str, Any],
    job_kind: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "job_id": job_id,
        "job_kind": job_kind,
        "collector_export": "collectDateRange",
        "args": {
            "startIso": start,
            "endIso": end,
            "outputDirectory": output_directory,
            "queryPrefix": query_prefix,
            "spanDays": span_days,
            "collectorOptions": collector_options,
            "schedulerOptions": copy.deepcopy(scheduler_options),
        },
    }
    if extra:
        result.update(extra)
    return result


def expand_collector_jobs(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand policy templates into concrete ``collectDateRange`` jobs."""

    window = plan["window"]
    start = window["start_date"]
    end = window["end_date_inclusive"]
    scheduler = plan["collector_contract"]["default_scheduler_options"]
    expansion = plan["job_expansion"]
    jobs: list[dict[str, Any]] = []

    full_spec = expansion["full_capture_and_empty_verification"]
    targeted_spec = expansion["targeted_search"]
    audit_spec = expansion["residual_audit"]
    full_policies = set(full_spec["eligible_policies"])

    for channel in plan["channel_policies"]:
        channel_slug = slug(channel["name"])
        policy = channel["policy"]
        if policy in full_policies:
            prefix = full_spec["file_prefix_template"].format(
                channel_slug=channel_slug,
                channel_id=channel["channel_id"],
            )
            query_prefix = full_spec["query_prefix_template"].format(
                channel_name=channel["name"]
            )
            jobs.append(
                _job(
                    job_id=f"full__{channel['channel_id']}",
                    start=start,
                    end=end,
                    output_directory=full_spec["output_directory_relative_to_plan"],
                    query_prefix=query_prefix,
                    span_days=int(channel["span_days"]),
                    collector_options=_collector_options(
                        channel, prefix, full_spec["collector_options"]
                    ),
                    scheduler_options=scheduler,
                    job_kind="full_capture_or_empty_verification",
                    extra={"channel_policy": policy},
                )
            )
            continue

        if policy != targeted_spec["eligible_policy"]:
            continue

        for family in plan["query_families"]:
            for query in family["queries"]:
                prefix = targeted_spec["file_prefix_template"].format(
                    channel_slug=channel_slug,
                    channel_id=channel["channel_id"],
                    family_id=family["family_id"],
                    query_id=query["query_id"],
                )
                query_prefix = targeted_spec["query_prefix_template"].format(
                    channel_name=channel["name"],
                    search_text=query["search_text"],
                )
                jobs.append(
                    _job(
                        job_id=(
                            f"target__{channel['channel_id']}__{family['family_id']}"
                            f"__{query['query_id']}"
                        ),
                        start=start,
                        end=end,
                        output_directory=targeted_spec[
                            "output_directory_relative_to_plan"
                        ],
                        query_prefix=query_prefix,
                        span_days=int(family["span_days"]),
                        collector_options=_collector_options(
                            channel, prefix, targeted_spec["collector_options"]
                        ),
                        scheduler_options=scheduler,
                        job_kind="targeted_search",
                        extra={
                            "family_id": family["family_id"],
                            "query_id": query["query_id"],
                            "source_refs": list(query["source_refs"]),
                        },
                    )
                )

        for audit_date in audit_spec["audit_dates"]:
            prefix = audit_spec["file_prefix_template"].format(
                channel_slug=channel_slug,
                channel_id=channel["channel_id"],
                audit_date=audit_date,
            )
            query_prefix = audit_spec["query_prefix_template"].format(
                channel_name=channel["name"]
            )
            jobs.append(
                _job(
                    job_id=f"audit__{channel['channel_id']}__{audit_date}",
                    start=audit_date,
                    end=audit_date,
                    output_directory=audit_spec["output_directory_relative_to_plan"],
                    query_prefix=query_prefix,
                    span_days=int(audit_spec["span_days"]),
                    collector_options=_collector_options(
                        channel, prefix, audit_spec["collector_options"]
                    ),
                    scheduler_options=scheduler,
                    job_kind="residual_audit_census_day",
                )
            )

    return jobs


def validate_plan(
    plan_path: Path = DEFAULT_PLAN,
    inventory_path: Path | None = None,
    *,
    check_source_hashes: bool = True,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        plan = load_json(plan_path)
    except Exception as exc:  # pragma: no cover - defensive CLI path
        return {
            "status": "failed",
            "errors": [f"plan_unreadable: {exc}"],
            "warnings": [],
            "metrics": {},
        }

    inventory_path = inventory_path or plan_path.parent / "full_server_channel_inventory.json"
    try:
        inventory = load_json(inventory_path)
    except Exception as exc:
        return {
            "status": "failed",
            "errors": [f"inventory_unreadable: {exc}"],
            "warnings": [],
            "metrics": {},
        }

    if plan.get("schema_version") != "1.0.0":
        errors.append("schema_version_must_be_1.0.0")
    if plan.get("artifact_type") != "discord_only_serverwide_relevance_collection_plan":
        errors.append("unexpected_artifact_type")
    if plan.get("guild", {}).get("guild_id") != inventory.get("guild_id"):
        errors.append("guild_id_does_not_match_inventory")
    if plan.get("source_policy", {}).get("scope") != "discord_only":
        errors.append("source_scope_not_discord_only")
    if plan.get("source_policy", {}).get("outside_sources_used") != 0:
        errors.append("outside_sources_used_must_equal_zero")

    collector_contract = plan.get("collector_contract", {})
    declared_options = collector_contract.get("required_collector_options") or []
    if (
        len(declared_options) != len(REQUIRED_COLLECTOR_OPTION_KEYS)
        or set(declared_options) != REQUIRED_COLLECTOR_OPTION_KEYS
    ):
        errors.append("required_collector_options_contract_mismatch")

    window = plan.get("window", {})
    if window.get("timezone") != "America/Chicago":
        errors.append("window_timezone_must_be_America/Chicago")
    try:
        days = inclusive_day_count(window["start_date"], window["end_date_inclusive"])
    except Exception as exc:
        days = 0
        errors.append(f"invalid_window_dates: {exc}")
    if days != 201 or window.get("local_calendar_days") != 201:
        errors.append("window_must_cover_201_local_calendar_days")
    if window.get("utc_start_inclusive") != "2026-01-01T06:00:00Z":
        errors.append("unexpected_utc_start")
    if window.get("utc_end_exclusive") != "2026-07-21T05:00:00Z":
        errors.append("unexpected_utc_end")

    sources = plan.get("vocabulary_sources", [])
    source_ids = [source.get("source_id") for source in sources]
    if len(source_ids) != len(set(source_ids)):
        errors.append("duplicate_vocabulary_source_id")
    if not sources:
        errors.append("vocabulary_sources_empty")
    for source in sources:
        source_id = source.get("source_id") or "<missing>"
        relative = source.get("path_relative_to_plan")
        if not relative:
            errors.append(f"source_missing_path:{source_id}")
            continue
        resolved = (plan_path.parent / relative).resolve()
        if not resolved.exists():
            errors.append(f"source_file_missing:{source_id}:{relative}")
            continue
        if not source.get("locator"):
            errors.append(f"source_locator_missing:{source_id}")
        expected_hash = str(source.get("sha256") or "").upper()
        if check_source_hashes and sha256_file(resolved) != expected_hash:
            errors.append(f"source_sha256_mismatch:{source_id}:{relative}")

    families = plan.get("query_families", [])
    family_ids = [family.get("family_id") for family in families]
    if len(family_ids) != len(set(family_ids)):
        errors.append("duplicate_query_family_id")
    if not families:
        errors.append("query_families_empty")
    query_key_pairs: list[tuple[str, str]] = []
    for family in families:
        family_id = str(family.get("family_id") or "<missing>")
        if not isinstance(family.get("span_days"), int) or family["span_days"] < 1:
            errors.append(f"invalid_family_span_days:{family_id}")
        queries = family.get("queries") or []
        if not queries:
            errors.append(f"query_family_empty:{family_id}")
        local_ids: set[str] = set()
        for query in queries:
            query_id = str(query.get("query_id") or "")
            search_text = str(query.get("search_text") or "").strip()
            query_key_pairs.append((family_id, query_id))
            if not query_id or query_id in local_ids:
                errors.append(f"duplicate_or_missing_query_id:{family_id}:{query_id}")
            local_ids.add(query_id)
            if not search_text:
                errors.append(f"empty_search_text:{family_id}:{query_id}")
            if search_text.casefold().startswith("in:") or DATE_OPERATOR_RE.search(search_text):
                errors.append(f"search_text_contains_scope_or_date:{family_id}:{query_id}")
            refs = query.get("source_refs") or []
            if not refs:
                errors.append(f"query_without_source_refs:{family_id}:{query_id}")
            for source_ref in refs:
                if source_ref not in source_ids:
                    errors.append(
                        f"unknown_query_source_ref:{family_id}:{query_id}:{source_ref}"
                    )

    inventory_channels = inventory.get("containers")
    if isinstance(inventory_channels, list):
        inventory_channels = [
            row
            for row in inventory_channels
            if isinstance(row, dict)
            and str(row.get("inventory_layer") or "top_level_container")
            == "top_level_container"
        ]
    else:
        inventory_channels = inventory.get("channels") or []
    plan_channels = plan.get("channel_policies") or []
    if len(inventory_channels) != 38:
        errors.append(f"inventory_channel_count_not_38:{len(inventory_channels)}")
    if len(plan_channels) != 38:
        errors.append(f"plan_channel_count_not_38:{len(plan_channels)}")

    inventory_by_id = {
        str(row.get("container_id") or row.get("channel_id")): row
        for row in inventory_channels
    }
    plan_by_id: dict[str, dict[str, Any]] = {}
    for channel in plan_channels:
        channel_id = str(channel.get("channel_id") or "")
        if not DISCORD_ID_RE.fullmatch(channel_id):
            errors.append(f"invalid_channel_id:{channel_id}")
        if channel_id in plan_by_id:
            errors.append(f"duplicate_channel_policy:{channel_id}")
        plan_by_id[channel_id] = channel
        policy = channel.get("policy")
        if policy not in ALLOWED_POLICIES:
            errors.append(f"invalid_channel_policy:{channel_id}:{policy}")
        if policy == "verified_empty_full_window" and channel.get("reported_full_window_total") != 0:
            errors.append(f"nonzero_channel_marked_verified_empty:{channel_id}")
        if policy in {"full_capture", "verified_empty_full_window"}:
            span = channel.get("span_days")
            if not isinstance(span, int) or not 1 <= span <= 201:
                errors.append(f"invalid_channel_span_days:{channel_id}")

    missing = sorted(set(inventory_by_id) - set(plan_by_id))
    extra = sorted(set(plan_by_id) - set(inventory_by_id))
    if missing:
        errors.append("missing_inventory_channels:" + ",".join(missing))
    if extra:
        errors.append("extra_plan_channels:" + ",".join(extra))

    for channel_id in sorted(set(inventory_by_id) & set(plan_by_id)):
        expected = inventory_by_id[channel_id]
        actual = plan_by_id[channel_id]
        comparisons = {
            "name": expected.get("name"),
            "kind": expected.get("kind"),
            "category_name": expected.get("category_name"),
            "created_at_utc": expected.get("channel_created_at_utc"),
        }
        for field, expected_value in comparisons.items():
            if actual.get(field) != expected_value:
                errors.append(
                    f"channel_inventory_mismatch:{channel_id}:{field}:"
                    f"expected={expected_value!r}:actual={actual.get(field)!r}"
                )

    targeted_ids = {
        channel_id
        for channel_id, row in plan_by_id.items()
        if row.get("policy") == "targeted_search_plus_residual_audit"
    }
    if targeted_ids != TARGETED_CHANNEL_IDS:
        errors.append(
            "targeted_channel_set_mismatch:"
            + ",".join(sorted(targeted_ids ^ TARGETED_CHANNEL_IDS))
        )
    if set(plan_by_id) != {
        channel_id
        for channel_id, row in plan_by_id.items()
        if row.get("policy") in {"full_capture", "verified_empty_full_window"}
    }:
        errors.append("every_top_level_container_must_require_full_or_verified_empty_coverage")
    forum = plan_by_id.get("1283941772577472643", {})
    if forum.get("policy") != "full_capture" or not forum.get(
        "requires_forum_thread_inventory"
    ):
        errors.append("premium_journals_must_be_full_capture_with_thread_inventory")
    ordinary_thread_policy = plan.get("ordinary_thread_policy")
    if not isinstance(ordinary_thread_policy, dict):
        errors.append("ordinary_thread_policy_missing")
    else:
        if ordinary_thread_policy.get("inventory_output_relative_to_plan") != (
            "raw/ordinary_thread_inventory.json"
        ):
            errors.append("ordinary_thread_inventory_output_mismatch")
        if "38" not in str(ordinary_thread_policy.get("parent_scope") or ""):
            errors.append("ordinary_thread_policy_must_audit_all_38_parents")
    post_cutoff_policy = plan.get("post_cutoff_inventory_policy")
    if not isinstance(post_cutoff_policy, dict):
        errors.append("post_cutoff_inventory_policy_missing")
    else:
        if post_cutoff_policy.get("minimum_capture_completed_at_utc") != (
            "2026-07-21T05:00:00Z"
        ):
            errors.append("post_cutoff_inventory_minimum_timestamp_mismatch")
        if post_cutoff_policy.get("authenticated") is not True:
            errors.append("post_cutoff_inventory_must_require_authentication")
        if post_cutoff_policy.get("navigation_pass_complete") is not True:
            errors.append("post_cutoff_inventory_must_require_complete_navigation_pass")
        if post_cutoff_policy.get("terminal_state_observed") is not True:
            errors.append("post_cutoff_inventory_must_require_terminal_state")

    audit_dates = plan.get("job_expansion", {}).get("residual_audit", {}).get(
        "audit_dates", []
    )
    if len(audit_dates) != 14 or len(audit_dates) != len(set(audit_dates)):
        errors.append("residual_audit_must_have_14_unique_dates")
    for audit_date in audit_dates:
        try:
            parsed = date.fromisoformat(audit_date)
            if not date(2026, 1, 1) <= parsed <= date(2026, 7, 20):
                errors.append(f"audit_date_outside_window:{audit_date}")
        except Exception:
            errors.append(f"invalid_audit_date:{audit_date}")

    try:
        jobs = expand_collector_jobs(plan)
    except Exception as exc:
        jobs = []
        errors.append(f"job_expansion_failed:{exc}")

    job_ids: set[str] = set()
    prefixes: set[str] = set()
    segment_total = 0
    for job in jobs:
        job_id = job.get("job_id")
        if job_id in job_ids:
            errors.append(f"duplicate_expanded_job_id:{job_id}")
        job_ids.add(job_id)
        args = job.get("args") or {}
        if job.get("collector_export") != "collectDateRange":
            errors.append(f"invalid_collector_export:{job_id}")
        required_args = {
            "startIso",
            "endIso",
            "outputDirectory",
            "queryPrefix",
            "spanDays",
            "collectorOptions",
            "schedulerOptions",
        }
        if set(args) != required_args:
            errors.append(f"collector_arg_contract_mismatch:{job_id}")
            continue
        options = args.get("collectorOptions") or {}
        if set(options) != REQUIRED_COLLECTOR_OPTION_KEYS:
            errors.append(f"collector_options_contract_mismatch:{job_id}")
        prefix = options.get("prefix")
        if prefix in prefixes:
            errors.append(f"duplicate_output_prefix:{prefix}")
        prefixes.add(prefix)
        if options.get("scope") != "channel-scoped":
            errors.append(f"expanded_job_not_channel_scoped:{job_id}")
        if job.get("job_kind") == "full_capture_or_empty_verification":
            for option_name, expected_value in FULL_CAPTURE_RUNTIME_OPTIONS.items():
                actual_value = options.get(option_name)
                if actual_value != expected_value:
                    errors.append(
                        "full_capture_runtime_option_mismatch:"
                        f"{job_id}:{option_name}:"
                        f"expected={expected_value!r}:actual={actual_value!r}"
                    )
        if not str(args.get("queryPrefix") or "").startswith("in:"):
            errors.append(f"expanded_query_not_in_scoped:{job_id}")
        try:
            segment_total += segment_count(
                args["startIso"], args["endIso"], int(args["spanDays"])
            )
        except Exception as exc:
            errors.append(f"invalid_expanded_job_dates:{job_id}:{exc}")

    query_count = sum(len(family.get("queries") or []) for family in families)
    expected_job_count = (38 - len(TARGETED_CHANNEL_IDS)) + (
        len(TARGETED_CHANNEL_IDS) * query_count
    ) + (len(TARGETED_CHANNEL_IDS) * len(audit_dates))
    if jobs and len(jobs) != expected_job_count:
        errors.append(
            f"expanded_job_count_mismatch:expected={expected_job_count}:actual={len(jobs)}"
        )

    hard_gate_ids = {
        gate.get("gate_id")
        for gate in plan.get("coverage_and_reconciliation_gates", [])
        if gate.get("severity") == "hard"
    }
    required_gates = {
        "inventory_exact",
        "inventory_post_cutoff_authenticated",
        "window_final",
        "full_capture_segment_coverage",
        "reply_resolution",
        "forum_exact_ids",
        "thread_inventory_complete",
        "discord_only",
        "claim_calibration",
    }
    if not required_gates <= hard_gate_ids:
        errors.append(
            "missing_required_hard_gates:"
            + ",".join(sorted(required_gates - hard_gate_ids))
        )

    policy_counts = {
        policy: sum(1 for row in plan_channels if row.get("policy") == policy)
        for policy in sorted(ALLOWED_POLICIES)
    }
    required_policy_counts = EXPECTED_POLICY_COUNTS
    if policy_counts != required_policy_counts:
        errors.append(
            "policy_count_contract_mismatch:"
            f"expected={required_policy_counts}:actual={policy_counts}"
        )
    job_kind_counts: dict[str, int] = {}
    for job in jobs:
        kind = str(job.get("job_kind"))
        job_kind_counts[kind] = job_kind_counts.get(kind, 0) + 1

    metrics = {
        "inventory_channels": len(inventory_channels),
        "planned_channels": len(plan_channels),
        "policy_counts": policy_counts,
        "query_families": len(families),
        "atomic_queries": query_count,
        "expanded_collector_jobs": len(jobs),
        "expanded_segment_count": segment_total,
        "job_kind_counts": job_kind_counts,
        "vocabulary_sources": len(sources),
        "hard_release_gates": len(hard_gate_ids),
    }
    return {
        "status": "passed" if not errors else "failed",
        "plan_path": str(plan_path.resolve()),
        "inventory_path": str(inventory_path.resolve()),
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
        "expanded_jobs": jobs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument(
        "--skip-source-hashes",
        action="store_true",
        help="Skip source SHA-256 checks; structural/source-reference checks still run.",
    )
    parser.add_argument(
        "--emit-expanded-jobs",
        type=Path,
        help="Write executable collectDateRange job JSON. Use '-' to print it.",
    )
    args = parser.parse_args(argv)

    report = validate_plan(
        args.plan,
        args.inventory,
        check_source_hashes=not args.skip_source_hashes,
    )
    jobs = report.pop("expanded_jobs", [])
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.emit_expanded_jobs and report["status"] == "passed":
        payload = {
            "schema_version": "1.0.0",
            "collector_module": str(
                (args.plan.resolve().parent / "../discord_browser_collector.mjs").resolve()
            ),
            "working_directory": str(args.plan.resolve().parent),
            "jobs": jobs,
        }
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        if str(args.emit_expanded_jobs) == "-":
            print(rendered)
        else:
            args.emit_expanded_jobs.write_text(rendered + "\n", encoding="utf-8")

    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
