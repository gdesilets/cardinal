from __future__ import annotations

"""Shared fail-closed contract for row-owned Discord reply provenance."""

import re
from typing import Any
from urllib.parse import urlparse


DISCORD_ID_RE = re.compile(r"^\d{15,22}$")

# This is the first row that exposed the Discord application-command DOM shape.
# Keep it as a release anchor so a broad structural rule cannot silently replace
# the evidence that motivated the exception.  Later rows are discovered and
# accepted solely through ``exact_executed_command_context``; their IDs must not
# become an allowlist.
EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID = "1523613360099295304"
EXECUTED_COMMAND_MESSAGE_ID = EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID
EXECUTED_COMMAND_AUTHOR_ID = "1211781489931452447"
EXECUTED_COMMAND_AUTHOR = "Wordle"
EXECUTED_COMMAND_CLASS_TOKEN = "executedCommand_c19a55"
EXECUTED_COMMAND_STATUS = (
    "discord_executed_command_context_without_reply_target"
)
EXECUTED_COMMAND_NON_REPLY_TYPE = "discord_application_command_invocation"

DOCUMENTED_NO_ID_STATUSES = frozenset(
    {
        "discord_message_not_loaded",
        "discord_attachment_preview_without_exact_target_id",
        "discord_sticker_preview_without_exact_target_id",
        "discord_voice_message_preview_without_exact_target_id",
        "discord_dyno_command_context_without_reply_target",
        "discord_executed_command_context_without_reply_target",
    }
)

EXACT_ROW_OWNED_REPLY_SOURCES = frozenset(
    {
        "owned_reply_context_descendant_content_id",
        "owned_reply_descendant_message_id",
        "owned_reply_descendant_aria_reference",
        "owned_reply_descendant_data_list_item_id",
        "owned_reply_descendant_data_message_id",
        "owned_reply_permalink",
    }
)


def reply_context(message: dict[str, Any]) -> str:
    return str(
        message.get("reply_context") or message.get("reply_to_content") or ""
    ).strip()


def has_reply_context(message: dict[str, Any]) -> bool:
    return bool(reply_context(message)) or message.get("reply_context_present") is True


def _context_lines(message: dict[str, Any]) -> list[str]:
    return [line.strip() for line in reply_context(message).splitlines() if line.strip()]


def _candidate_rows(message: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("reply_to_message_id_candidates", "reply_target_id_candidates"):
        raw = message.get(key)
        if isinstance(raw, list):
            rows.extend(row for row in raw if isinstance(row, dict))
    return rows


def no_exact_target_evidence(message: dict[str, Any]) -> bool:
    """True only when the row preserves no candidate for an exact target."""

    return bool(
        not str(message.get("reply_to_message_id") or "").strip()
        and not str(message.get("reply_to_channel_id") or "").strip()
        and not str(message.get("reply_to_permalink") or "").strip()
        and not str(message.get("reply_to_message_id_source") or "").strip()
        and not _candidate_rows(message)
        and not str(message.get("reply_target_content_id") or "").strip()
        and not str(message.get("reply_target_aria_labelledby") or "").strip()
        and not str(message.get("reply_target_aria_describedby") or "").strip()
        and not str(message.get("reply_target_data_list_item_id") or "").strip()
        and message.get("reply_to_message_id_conflict") is not True
        and message.get("reply_to_channel_id_conflict") is not True
        and message.get("reply_target_scope_exact") is not True
    )


def exact_executed_command_context(message: dict[str, Any]) -> bool:
    """Recognize only a preserved, message-bound Wordle ``used Play`` widget."""

    message_id = str(message.get("message_id") or "").strip()
    article_label = str(message.get("article_aria_labelledby") or "").strip()
    referenced_ids = set(
        re.findall(
            r"message-(?:reply-context|username|content|accessories|timestamp)-(\d{15,22})",
            article_label,
        )
    )
    required_label_tokens = {
        f"message-username-{message_id}",
        f"message-content-{message_id}",
        f"message-timestamp-{message_id}",
    }
    class_tokens = set(
        str(message.get("reply_context_dom_class") or "").split()
    )
    return bool(
        DISCORD_ID_RE.fullmatch(message_id)
        and message.get("article_id") == f"search-result-{message_id}"
        and required_label_tokens.issubset(set(article_label.split()))
        and referenced_ids == {message_id}
        and message.get("reply_context_article_binding_exact") is True
        and message.get("reply_context_owner_message_id") == message_id
        and message.get("reply_context_dom_tag") == "DIV"
        and message.get("reply_context_executed_command_exact") is True
        and message.get("reply_context_aria_hidden") is True
        and message.get("reply_context_scope_exact") is False
        and EXECUTED_COMMAND_CLASS_TOKEN in class_tokens
        and str(message.get("author") or "").strip()
        == EXECUTED_COMMAND_AUTHOR
        and str(message.get("author_id") or "").strip()
        == EXECUTED_COMMAND_AUTHOR_ID
        and str(message.get("author_id_source") or "").strip()
        in {
            "exact_username_bound_data_user_id",
            "owner_scoped_avatar_cdn_path",
        }
        and message.get("author_verified_app_exact") is True
        and message.get("author_id_conflict") is False
        and message.get("content_scope_exact") is True
        and message.get("reply_context_present") is True
        and message.get("reply_target_owner_scoped") is False
        and message.get("reply_target_scope_exact") is False
        and message.get("reply_to_message_id_conflict") is False
        and message.get("reply_to_channel_id_conflict") is False
        and message.get("reply_target_content_text") == ""
        and _context_lines(
            {"reply_context": message.get("reply_to_content")}
        )
        == _context_lines(message)
        and message.get("reply_to_message_id_candidates") == []
        and message.get("reply_target_id_candidates") == []
        and all(
            message.get(key) is None
            for key in (
                "reply_to_message_id",
                "reply_to_channel_id",
                "reply_to_permalink",
                "reply_to_message_id_source",
                "reply_target_content_id",
                "reply_target_aria_labelledby",
                "reply_target_aria_describedby",
                "reply_target_data_list_item_id",
            )
        )
        and len(_context_lines(message)) == 3
        and bool(_context_lines(message)[0])
        and _context_lines(message)[1:] == ["used", "Play"]
        and no_exact_target_evidence(message)
    )


def is_executed_command_context_candidate(message: dict[str, Any]) -> bool:
    """Select the legacy anchor plus every structural command claim/lookalike."""

    class_tokens = str(message.get("reply_context_dom_class") or "").split()
    return bool(
        str(message.get("message_id") or "").strip()
        == EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID
        or (
            len(_context_lines(message)) == 3
            and bool(_context_lines(message)[0])
            and _context_lines(message)[1:] == ["used", "Play"]
        )
        or any(token.startswith("executedCommand_") for token in class_tokens)
        or message.get("reply_context_executed_command_exact") is True
        or str(message.get("reply_target_resolution_status") or "").strip()
        == EXECUTED_COMMAND_STATUS
        or str(message.get("reply_context_non_reply_type") or "").strip()
        == EXECUTED_COMMAND_NON_REPLY_TYPE
    )


def classify_documented_no_id_status(message: dict[str, Any]) -> str | None:
    """Classify only exact Discord widgets that expose no target snowflake."""

    if not has_reply_context(message) or not no_exact_target_evidence(message):
        return None
    context = reply_context(message)
    lines = _context_lines(message)
    exact_dyno_command_context = bool(
        str(message.get("author_id") or "") == "155149108183695360"
        and message.get("content_scope_exact") is True
        and not str(message.get("content_text") or "").strip()
        and len(lines) == 3
        and lines[0]
        and lines[1] == "used"
        and lines[2]
    )
    exact_application_command_context = exact_executed_command_context(message)
    matches: list[str] = []
    if exact_dyno_command_context:
        matches.append("discord_dyno_command_context_without_reply_target")
    if exact_application_command_context:
        matches.append(EXECUTED_COMMAND_STATUS)
    if context.casefold() == "message could not be loaded".casefold():
        matches.append("discord_message_not_loaded")
    preview_lines = {
        "Click to see attachment": (
            "discord_attachment_preview_without_exact_target_id"
        ),
        "Click to see sticker": "discord_sticker_preview_without_exact_target_id",
    }
    for exact_text, status in preview_lines.items():
        if exact_text in lines:
            matches.append(status)
    if context.casefold() == "Click to see voice message".casefold():
        matches.append("discord_voice_message_preview_without_exact_target_id")
    return matches[0] if len(matches) == 1 else None


def documented_no_id_contract_errors(message: dict[str, Any]) -> list[str]:
    """Validate an accepted no-ID widget, including status/boolean agreement."""

    errors: list[str] = []
    expected = classify_documented_no_id_status(message)
    declared = str(message.get("reply_target_resolution_status") or "").strip()
    documented = message.get("reply_target_unavailability_documented")
    if expected is None:
        errors.append("reply_context_not_exact_documented_no_id_widget")
    elif declared != expected:
        errors.append("reply_target_resolution_status_mismatch")
    if documented is not True:
        errors.append("reply_target_unavailability_documented_not_true")
    if declared in DOCUMENTED_NO_ID_STATUSES and expected != declared:
        errors.append("documented_status_not_supported_by_exact_context")
    if not no_exact_target_evidence(message):
        errors.append("documented_no_id_row_contains_target_evidence")
    if expected == "discord_dyno_command_context_without_reply_target":
        if message.get("reply_context_non_reply_exact") is not True:
            errors.append("dyno_non_reply_exact_flag_not_true")
        if message.get("reply_context_non_reply_type") != "discord_dyno_command_invocation":
            errors.append("dyno_non_reply_type_mismatch")
    elif expected == EXECUTED_COMMAND_STATUS:
        if message.get("reply_context_non_reply_exact") is not True:
            errors.append("executed_command_non_reply_exact_flag_not_true")
        if message.get("reply_context_non_reply_type") != EXECUTED_COMMAND_NON_REPLY_TYPE:
            errors.append("executed_command_non_reply_type_mismatch")
    elif message.get("reply_context_non_reply_exact") is True or str(
        message.get("reply_context_non_reply_type") or ""
    ).strip():
        errors.append("non_dyno_preview_claims_non_reply_command_evidence")
    return sorted(set(errors))


def audit_executed_command_contexts(
    messages: list[dict[str, Any]],
    *,
    expected_message_present: bool = False,
    expected_message_ids: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """Fail closed for structural application-command contexts and lookalikes.

    ``expected_message_ids`` names required legacy anchors, not the complete set
    of acceptable rows.  Any number of additional rows may pass when each one
    independently satisfies the exact DOM contract.
    """

    candidates: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    accepted_exact_context_count = 0
    for row_index, message in enumerate(messages, start=1):
        if not isinstance(message, dict) or not is_executed_command_context_candidate(
            message
        ):
            continue
        message_id = str(message.get("message_id") or "").strip()
        reasons = documented_no_id_contract_errors(message)
        candidate = {
            "row_index": row_index,
            "result_index": message.get("result_index"),
            "message_id": message_id or None,
            "status": message.get("reply_target_resolution_status"),
        }
        candidates.append(candidate)
        if reasons:
            failures.append({**candidate, "reasons": reasons})
        else:
            accepted_exact_context_count += 1
    observed_candidate_ids = [row["message_id"] for row in candidates]
    expected_ids = list(expected_message_ids or [])
    expected_presence = (
        bool(expected_ids)
        if expected_message_ids is not None
        else expected_message_present
    )
    if expected_message_ids is not None:
        presence_valid = all(
            observed_candidate_ids.count(expected_id) == 1
            for expected_id in expected_ids
        )
    elif expected_message_present:
        presence_valid = bool(observed_candidate_ids)
    else:
        presence_valid = True
    if not presence_valid:
        failures.append(
            {
                "message_id": expected_ids or None,
                "reasons": [
                    "expected_executed_command_message_missing_or_duplicated"
                ],
            }
        )
    duplicate_ids = sorted(
        {
            message_id
            for message_id in observed_candidate_ids
            if message_id and observed_candidate_ids.count(message_id) > 1
        }
    )
    if duplicate_ids:
        failures.append(
            {
                "message_id": duplicate_ids,
                "reasons": ["executed_command_candidate_message_id_duplicated"],
            }
        )
    passed = not failures
    return {
        "schema_version": "1.0.0",
        "passed": passed,
        "audited_message_count": len(messages),
        "candidate_count": len(candidates),
        "accepted_exact_context_count": accepted_exact_context_count,
        "expected_message_present": expected_presence,
        "expected_message_ids": expected_ids,
        "expected_message_presence_valid": presence_valid,
        "failure_count": len(failures),
        "candidate_message_ids": [row["message_id"] for row in candidates],
        "failures": failures,
        "policy": (
            "The legacy anchor must remain present in its expected segment. "
            "Any Wordle verified-app row with exact article/message binding, "
            "the literal executedCommand_c19a55 DOM token, aria-hidden=true, the "
            "Wordle application identity, exact <nonempty>/used/Play context, and "
            "zero target evidence is a documented Discord application-command "
            "non-reply. Text or class lookalikes remain unresolved."
        ),
    }


def rederive_release_executed_command_integrity(
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Re-audit release rows without trusting a declared aggregate summary."""

    audit = audit_executed_command_contexts(
        messages,
        expected_message_ids=[EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID],
    )
    candidate_ids = list(audit.get("candidate_message_ids") or [])
    anchor_count = candidate_ids.count(EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID)
    return {
        "schema_version": "1.0.0",
        "passed": audit.get("passed") is True,
        "audited_message_count": len(messages),
        "expected_segment_count": 1,
        "expected_segment_present": bool(
            audit.get("expected_message_presence_valid") is True
            and anchor_count == 1
        ),
        "legacy_anchor_message_id": EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID,
        "legacy_anchor_count": anchor_count,
        "candidate_count": audit.get("candidate_count"),
        "accepted_exact_context_count": audit.get(
            "accepted_exact_context_count"
        ),
        "failure_count": audit.get("failure_count"),
        "candidate_message_ids": candidate_ids,
        "failures": list(audit.get("failures") or []),
        "policy": audit.get("policy"),
    }


def release_executed_command_semantic_errors(
    messages: list[dict[str, Any]],
    declared_summary: Any,
) -> list[str]:
    """Compare release declarations with a fresh audit of canonical messages."""

    if not isinstance(declared_summary, dict):
        return ["executed_command_reply_provenance_integrity_missing"]
    derived = rederive_release_executed_command_integrity(messages)
    errors: list[str] = []
    if derived.get("passed") is not True:
        errors.append("executed_command_release_message_rederivation_failed")
    for key in (
        "expected_segment_present",
        "legacy_anchor_message_id",
        "legacy_anchor_count",
        "candidate_count",
        "accepted_exact_context_count",
        "failure_count",
    ):
        if declared_summary.get(key) != derived.get(key):
            errors.append(f"executed_command_rederived_{key}_mismatch")
    declared_ids = declared_summary.get("candidate_message_ids")
    derived_ids = derived.get("candidate_message_ids")
    if (
        not isinstance(declared_ids, list)
        or any(not isinstance(value, str) for value in declared_ids)
        or sorted(declared_ids) != sorted(derived_ids)
    ):
        errors.append("executed_command_rederived_message_id_set_mismatch")
    return sorted(set(errors))


def _message_reference_ids(raw_value: str) -> set[str]:
    return set(
        re.findall(
            r"message-(?:content|username|timestamp)-(\d{15,22})", raw_value
        )
    )


def _candidate_evidence_valid(
    message: dict[str, Any], *, target_id: str, source: str
) -> bool:
    matching = [
        row
        for row in _candidate_rows(message)
        if str(row.get("message_id") or "") == target_id
        and str(row.get("source") or "") == source
    ]
    if not matching:
        return False
    for row in matching:
        raw = str(row.get("raw_value") or "")
        if source in {
            "owned_reply_descendant_aria_reference",
            "owned_reply_descendant_data_list_item_id",
        } and row.get("owner_scoped") is not True:
            continue
        if source in {
            "owned_reply_descendant_message_id",
            "owned_reply_descendant_aria_reference",
        }:
            if _message_reference_ids(raw) == {target_id}:
                return True
        elif source == "owned_reply_descendant_data_list_item_id":
            if re.fullmatch(
                rf"(?:chat-messages___|NO_LIST___|search-result-){re.escape(target_id)}(?:[^\d].*)?",
                raw,
            ):
                return True
        elif source == "owned_reply_descendant_data_message_id":
            if raw == target_id:
                return True
        elif source == "owned_reply_permalink":
            if raw == str(message.get("reply_to_permalink") or ""):
                return True
    return False


def exact_reply_target_contract_errors(
    message: dict[str, Any], *, guild_id: str
) -> list[str]:
    """Validate a resolved target using row-owned evidence and an exact permalink."""

    errors: list[str] = []
    owner_id = str(message.get("message_id") or "").strip()
    target_id = str(message.get("reply_to_message_id") or "").strip()
    source = str(message.get("reply_to_message_id_source") or "").strip()
    if not DISCORD_ID_RE.fullmatch(target_id) or target_id == owner_id:
        errors.append("reply_target_id_missing_invalid_or_self")
    if source not in EXACT_ROW_OWNED_REPLY_SOURCES:
        errors.append("reply_target_source_not_row_owned_exact")
    if message.get("reply_target_scope_exact") is not True:
        errors.append("reply_target_scope_not_exact")
    if message.get("reply_to_message_id_conflict") is True:
        errors.append("reply_target_message_id_conflict")
    if message.get("reply_to_channel_id_conflict") is True:
        errors.append("reply_target_channel_id_conflict")

    content_id = str(message.get("reply_target_content_id") or "").strip()
    if content_id and content_id != f"message-content-{target_id}":
        errors.append("reply_target_content_id_mismatch")
    if source == "owned_reply_context_descendant_content_id":
        if content_id != f"message-content-{target_id}":
            errors.append("reply_target_content_id_evidence_missing")
    elif source in {
        "owned_reply_descendant_message_id",
        "owned_reply_descendant_aria_reference",
        "owned_reply_descendant_data_list_item_id",
        "owned_reply_descendant_data_message_id",
        "owned_reply_permalink",
    } and not _candidate_evidence_valid(message, target_id=target_id, source=source):
        errors.append("reply_target_row_owned_candidate_evidence_invalid")

    candidate_ids = {
        str(row.get("message_id") or "")
        for row in _candidate_rows(message)
        if str(row.get("message_id") or "").strip()
    }
    if candidate_ids and candidate_ids != {target_id}:
        errors.append("reply_target_candidate_set_conflicts_with_selected_target")

    channel_id = str(message.get("reply_to_channel_id") or "").strip()
    permalink = str(message.get("reply_to_permalink") or "").strip()
    parsed = urlparse(permalink) if permalink else None
    parts = [part for part in (parsed.path.split("/") if parsed else []) if part]
    if not (
        parsed
        and parsed.hostname in {"discord.com", "www.discord.com"}
        and DISCORD_ID_RE.fullmatch(channel_id)
        and len(parts) >= 4
        and parts[-3:] == [guild_id, channel_id, target_id]
    ):
        errors.append("reply_target_permalink_or_channel_mismatch")

    status_present = "reply_target_resolution_status" in message
    documented_present = "reply_target_unavailability_documented" in message
    if status_present or documented_present:
        if message.get("reply_target_resolution_status") != "exact_target_id":
            errors.append("reply_target_resolution_status_not_exact_target_id")
        if message.get("reply_target_unavailability_documented") is not False:
            errors.append("resolved_target_claims_unavailability_documented")
    return sorted(set(errors))


def resolution_status_boolean_errors(message: dict[str, Any]) -> list[str]:
    """Validate collector resolution-status/boolean consistency for every row."""

    target_id = str(message.get("reply_to_message_id") or "").strip()
    if target_id:
        # Exact-target consistency is validated with the stronger source contract.
        return []
    context_present = has_reply_context(message)
    status_present = "reply_target_resolution_status" in message
    documented_present = "reply_target_unavailability_documented" in message
    if not status_present and not documented_present:
        return []
    status = str(message.get("reply_target_resolution_status") or "").strip()
    documented = message.get("reply_target_unavailability_documented")
    expected_documented = classify_documented_no_id_status(message)
    errors: list[str] = []
    if expected_documented:
        if status != expected_documented:
            errors.append("reply_target_resolution_status_mismatch")
        if documented is not True:
            errors.append("reply_target_unavailability_documented_not_true")
    elif context_present:
        if status != "unresolved_without_exact_target_id":
            errors.append("unknown_reply_context_status_not_unresolved")
        if documented is not False:
            errors.append("unknown_reply_context_documented_flag_not_false")
    else:
        if status != "not_applicable":
            errors.append("no_reply_context_status_not_applicable")
        if documented is not False:
            errors.append("no_reply_context_documented_flag_not_false")
    return sorted(set(errors))


def release_executed_command_integrity_errors(
    payload: dict[str, Any],
    *,
    allow_empty_non_release: bool = False,
) -> list[str]:
    """Require the named, rederived application-command gate in release artifacts."""

    errors: list[str] = []
    summary = payload.get("executed_command_reply_provenance_integrity")
    if not isinstance(summary, dict):
        return ["executed_command_reply_provenance_integrity_missing"]
    gates = payload.get("release_gates")
    matching = [
        gate
        for gate in gates or []
        if isinstance(gate, dict)
        and gate.get("gate") == "executed_command_reply_provenance_integrity"
    ]
    if len(matching) != 1:
        errors.append("executed_command_reply_provenance_gate_missing_or_duplicate")
    else:
        gate = matching[0]
        if gate.get("passed") is not True:
            errors.append("executed_command_reply_provenance_gate_not_passed")
        if gate.get("detail") != summary:
            errors.append("executed_command_reply_provenance_gate_detail_mismatch")
    if summary.get("passed") is not True:
        errors.append("executed_command_reply_provenance_summary_not_passed")
    empty_non_release_summary = bool(
        allow_empty_non_release
        and summary.get("passed") is True
        and summary.get("expected_segment_count") == 0
        and summary.get("expected_segment_present") is False
        and summary.get("legacy_anchor_count") == 0
        and summary.get("candidate_count") == 0
        and summary.get("accepted_exact_context_count") == 0
        and summary.get("failure_count") == 0
        and summary.get("candidate_message_ids") == []
    )
    if empty_non_release_summary:
        return sorted(set(errors))
    if summary.get("expected_segment_present") is not True:
        errors.append("executed_command_expected_segment_not_present")
    if summary.get("expected_segment_count") != 1:
        errors.append("executed_command_expected_segment_count_mismatch")
    if (
        summary.get("legacy_anchor_message_id")
        != EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID
        or summary.get("legacy_anchor_count") != 1
    ):
        errors.append("executed_command_legacy_anchor_mismatch")
    candidate_count = summary.get("candidate_count")
    accepted_count = summary.get("accepted_exact_context_count")
    candidate_ids = summary.get("candidate_message_ids")
    if (
        not isinstance(candidate_count, int)
        or isinstance(candidate_count, bool)
        or candidate_count < 1
    ):
        errors.append("executed_command_reply_provenance_candidate_count_mismatch")
    if accepted_count != candidate_count:
        errors.append(
            "executed_command_reply_provenance_accepted_exact_context_count_mismatch"
        )
    if summary.get("failure_count") != 0:
        errors.append("executed_command_reply_provenance_failure_count_mismatch")
    if not isinstance(candidate_ids, list) or len(candidate_ids) != candidate_count:
        errors.append("executed_command_reply_provenance_message_id_set_mismatch")
    elif (
        any(
            not isinstance(value, str) or not DISCORD_ID_RE.fullmatch(value)
            for value in candidate_ids
        )
        or len(set(candidate_ids)) != len(candidate_ids)
        or candidate_ids.count(EXECUTED_COMMAND_LEGACY_ANCHOR_MESSAGE_ID) != 1
    ):
        errors.append("executed_command_reply_provenance_message_id_set_mismatch")
    return sorted(set(errors))
