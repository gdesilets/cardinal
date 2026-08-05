from __future__ import annotations

"""Fail-closed selection for the user-authorized three-channel Discord scope.

The raw archive remains immutable and may contain earlier captures from other
channels.  This module creates a derived *view* of that archive: it validates
the signed-off scope document, accepts only segments whose exact requested
container is authorized, and derives an inventory whose completeness applies
only to the authorized parents and their provenance-proven child threads.
"""

import copy
import datetime as dt
import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse


SCOPE_SCHEMA_VERSION = "1.0.0"
SNOWFLAKE_RE = re.compile(r"\d{15,22}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# This is intentionally fixed.  Expanding the JSON file alone must never
# silently broaden a release after the user's explicit narrowing instruction.
REQUIRED_AUTHORIZED_CONTAINERS = {
    "1370578463223975986": ("student-breakdowns", "text channel"),
    "1283941772577472643": ("premium-journals", "forum channel"),
    "1273692573898113076": ("\u2753\u2502questions", "text channel"),
}

CANONICAL_PATH_POLICY = {
    "student_breakdowns": {
        "authoritative_directory": "raw/channel_segments",
    },
    "questions": {
        "authoritative_directory": "raw/channel_segments",
    },
    "premium_journals": {
        "authoritative_directory": "raw/channel_segments_v2_5",
        "collector_version_required": "2.6",
        "legacy_preservation_directory": "raw/channel_segments",
        "legacy_directory_policy": "preservation_only_not_authoritative",
    },
}
PREMIUM_PARENT_ID = "1283941772577472643"

TRUSTED_REQUEST_ID_SOURCES = {
    "navigation_inventory",
    "navigation_href",
    "search_result_exact",
    "authenticated_channel_url",
    "authenticated_discord_channel_url",
    "inventory_exact_href",
}

TRUSTED_CHILD_EVIDENCE_METHODS = {
    "forum_card_data_list_item_id",
}

DISCORD_QUERY_TOKEN_RE = re.compile(
    r'(?:^|\s)(?P<key>in|after|before):(?P<value>"[^"]+"|\S+)',
    re.IGNORECASE,
)


class AuthorizedScopeError(RuntimeError):
    """The authorized-scope document or a required provenance binding is invalid."""


def _exact_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if SNOWFLAKE_RE.fullmatch(text) else None


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AuthorizedScopeError(f"Could not read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AuthorizedScopeError(f"{label} {path} must contain a JSON object")
    return value


def _normalized_identity_text(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "").strip()).casefold()


def parse_discord_search_query(query: Any) -> tuple[str | None, list[str]]:
    """Return the exact ``in:`` target and fail-closed binding errors.

    Discord channel names do not contain spaces, but quoted values are accepted
    so a future collector cannot silently change this parser's security model.
    Exactly one in/after/before token is required.  The caller still validates
    the date window through the normal segment validator.
    """

    text = str(query or "").strip()
    tokens: dict[str, list[str]] = {"in": [], "after": [], "before": []}
    for match in DISCORD_QUERY_TOKEN_RE.finditer(text):
        key = match.group("key").casefold()
        value = match.group("value")
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        tokens[key].append(value)
    errors: list[str] = []
    for key in ("in", "after", "before"):
        if len(tokens[key]) != 1:
            errors.append(f"discord_query_{key}_token_count_not_one")
    for key in ("after", "before"):
        if len(tokens[key]) == 1 and not DATE_RE.fullmatch(tokens[key][0]):
            errors.append(f"discord_query_{key}_date_invalid")
    target = tokens["in"][0] if len(tokens["in"]) == 1 else None
    if target is not None and not target.strip():
        errors.append("discord_query_in_target_empty")
    return target, errors


def authorized_parent_name_aliases(
    inventory_payload: dict[str, Any] | None,
    scope: "AuthorizedScope",
) -> dict[str, frozenset[str]]:
    """Bind exact inventory display names to the fixed authorized parent IDs."""

    aliases: dict[str, set[str]] = {
        row.channel_id: {_normalized_identity_text(row.name)}
        for row in scope.containers
    }
    if not isinstance(inventory_payload, dict):
        return {key: frozenset(values) for key, values in aliases.items()}
    rows = inventory_payload.get("containers")
    if not isinstance(rows, list):
        rows = inventory_payload.get("channels")
    if not isinstance(rows, list):
        return {key: frozenset(values) for key, values in aliases.items()}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        container_id = _exact_id(
            raw.get("container_id") or raw.get("channel_id")
        )
        if container_id not in scope.parent_ids:
            continue
        parent_id = _exact_id(
            raw.get("parent_container_id")
            or raw.get("parent_channel_id")
            or raw.get("parent_id")
        )
        if parent_id:
            continue
        name = _normalized_identity_text(
            raw.get("name") or raw.get("channel_name")
        )
        kind = _normalized_identity_text(
            raw.get("kind") or raw.get("channel_kind")
        )
        expected_kind = _normalized_identity_text(
            scope.containers_by_id[container_id].kind
        )
        # A display-name alias is usable only when the inventory row itself is
        # unambiguously the expected top-level container type.
        if name and kind == expected_kind:
            aliases[container_id].add(name)
    return {key: frozenset(values) for key, values in aliases.items()}


@dataclass(frozen=True)
class AuthorizedContainer:
    channel_id: str
    name: str
    kind: str
    include_exact_child_threads: bool
    logical_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "name": self.name,
            "kind": self.kind,
            "include_exact_child_threads": self.include_exact_child_threads,
            "logical_name": self.logical_name,
        }


@dataclass(frozen=True)
class AuthorizedScope:
    source_path: Path
    source_sha256: str
    source_size_bytes: int
    guild_id: str
    timezone: str
    start_date_inclusive: str
    end_date_inclusive: str
    containers: tuple[AuthorizedContainer, ...]

    @property
    def parent_ids(self) -> frozenset[str]:
        return frozenset(row.channel_id for row in self.containers)

    @property
    def containers_by_id(self) -> dict[str, AuthorizedContainer]:
        return {row.channel_id: row for row in self.containers}

    @property
    def canonical_root(self) -> Path:
        return self.source_path.parent / "raw" / "channel_segments"

    @property
    def premium_v2_5_root(self) -> Path:
        return self.source_path.parent / "raw" / "channel_segments_v2_5"

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "schema_version": SCOPE_SCHEMA_VERSION,
            "scope_status": "user_narrowed",
            "source_sha256": self.source_sha256,
            "source_size_bytes": self.source_size_bytes,
            "guild_id": self.guild_id,
            "source_scope": "discord_only",
            "outside_sources_used": False,
            "window": {
                "timezone": self.timezone,
                "start_date_inclusive": self.start_date_inclusive,
                "end_date_inclusive": self.end_date_inclusive,
            },
            "allowed_top_level_containers": [
                row.as_dict() for row in self.containers
            ],
            "canonical_path_policy": copy.deepcopy(CANONICAL_PATH_POLICY),
            "selection_rule": (
                "A segment is included only when its exact authenticated requested "
                "container is an authorized parent or a provenance-proven child of one."
            ),
        }


def load_validated_scope(
    path: Path,
    *,
    expected_guild_id: str,
    expected_timezone: str,
    expected_start_date: str,
    expected_end_date: str,
) -> AuthorizedScope:
    """Load the immutable user scope and reject every silent broadening vector."""

    resolved = path.resolve()
    payload = _json_object(resolved, "authorized collection scope")
    errors: list[str] = []
    if payload.get("schema_version") != SCOPE_SCHEMA_VERSION:
        errors.append("schema_version_must_be_1.0.0")
    if payload.get("scope_status") != "user_narrowed":
        errors.append("scope_status_must_be_user_narrowed")
    if _exact_id(payload.get("guild_id")) != expected_guild_id:
        errors.append("guild_id_mismatch")
    if payload.get("source_scope") != "discord_only":
        errors.append("source_scope_must_be_discord_only")
    if payload.get("outside_sources_used") is not False:
        errors.append("outside_sources_used_must_be_false")

    window = payload.get("window") if isinstance(payload.get("window"), dict) else {}
    expected_window = {
        "timezone": expected_timezone,
        "start_date_inclusive": expected_start_date,
        "end_date_inclusive": expected_end_date,
    }
    for key, expected in expected_window.items():
        if window.get(key) != expected:
            errors.append(f"window_{key}_mismatch")
    if not DATE_RE.fullmatch(str(window.get("start_date_inclusive") or "")):
        errors.append("window_start_date_invalid")
    if not DATE_RE.fullmatch(str(window.get("end_date_inclusive") or "")):
        errors.append("window_end_date_invalid")

    raw_rows = payload.get("allowed_top_level_containers")
    if not isinstance(raw_rows, list):
        raw_rows = []
        errors.append("allowed_top_level_containers_not_array")
    rows: list[AuthorizedContainer] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rows, start=1):
        if not isinstance(raw, dict):
            errors.append(f"allowed_container_{index}_not_object")
            continue
        channel_id = _exact_id(raw.get("channel_id"))
        if not channel_id:
            errors.append(f"allowed_container_{index}_id_invalid")
            continue
        if channel_id in seen:
            errors.append(f"allowed_container_duplicate_id:{channel_id}")
            continue
        seen.add(channel_id)
        expected_identity = REQUIRED_AUTHORIZED_CONTAINERS.get(channel_id)
        if expected_identity is None:
            errors.append(f"unauthorized_container_added:{channel_id}")
            continue
        expected_name, expected_kind = expected_identity
        name = str(raw.get("name") or "").strip()
        logical_name = str(raw.get("logical_name") or "").strip() or None
        kind = str(raw.get("kind") or "").strip()
        if name != expected_name:
            errors.append(f"allowed_container_name_mismatch:{channel_id}")
        if channel_id == "1273692573898113076" and logical_name != "questions":
            errors.append(f"allowed_container_logical_name_mismatch:{channel_id}")
        if kind != expected_kind:
            errors.append(f"allowed_container_kind_mismatch:{channel_id}")
        if raw.get("include_exact_child_threads") is not True:
            errors.append(f"child_thread_policy_not_true:{channel_id}")
        rows.append(
            AuthorizedContainer(
                channel_id=channel_id,
                name=name,
                kind=kind,
                include_exact_child_threads=True,
                logical_name=logical_name,
            )
        )
    missing = sorted(set(REQUIRED_AUTHORIZED_CONTAINERS) - seen)
    if missing:
        errors.append("required_authorized_containers_missing:" + ",".join(missing))
    if len(rows) != len(REQUIRED_AUTHORIZED_CONTAINERS):
        errors.append("authorized_container_count_not_three")
    for key in ("collection_rule", "release_rule", "deletion_rule"):
        if not str(payload.get(key) or "").strip():
            errors.append(f"{key}_missing")
    if payload.get("canonical_path_policy") != CANONICAL_PATH_POLICY:
        errors.append("canonical_path_policy_mismatch")
    if errors:
        raise AuthorizedScopeError(
            "Authorized collection scope failed validation: " + "; ".join(sorted(set(errors)))
        )
    return AuthorizedScope(
        source_path=resolved,
        source_sha256=_sha256_file(resolved),
        source_size_bytes=resolved.stat().st_size,
        guild_id=expected_guild_id,
        timezone=expected_timezone,
        start_date_inclusive=expected_start_date,
        end_date_inclusive=expected_end_date,
        containers=tuple(sorted(rows, key=lambda row: row.channel_id)),
    )


def validate_authoritative_segment_directories(
    segment_dirs: Sequence[Path], scope: AuthorizedScope
) -> dict[str, Any]:
    """Require the two non-overlapping canonical roots in an authorized build."""

    observed = {Path(path).resolve() for path in segment_dirs}
    expected = {scope.canonical_root.resolve(), scope.premium_v2_5_root.resolve()}
    missing = sorted(path.as_posix() for path in expected - observed)
    unexpected = sorted(path.as_posix() for path in observed - expected)
    if missing or unexpected:
        reasons: list[str] = []
        if missing:
            reasons.append("missing=" + ",".join(missing))
        if unexpected:
            reasons.append("unexpected=" + ",".join(unexpected))
        raise AuthorizedScopeError(
            "Authorized canonical segment roots must be the exact standard and "
            "Premium-v2.5 directories: " + "; ".join(reasons)
        )
    return {
        "gate": "premium_journals_authoritative_v2_5_source_integrity",
        "passed": True,
        "standard_authoritative_directory": CANONICAL_PATH_POLICY[
            "student_breakdowns"
        ]["authoritative_directory"],
        "premium_authoritative_directory": CANONICAL_PATH_POLICY[
            "premium_journals"
        ]["authoritative_directory"],
        "premium_collector_version_required": CANONICAL_PATH_POLICY[
            "premium_journals"
        ]["collector_version_required"],
        "premium_legacy_preservation_directory": CANONICAL_PATH_POLICY[
            "premium_journals"
        ]["legacy_preservation_directory"],
        "premium_legacy_directory_policy": CANONICAL_PATH_POLICY[
            "premium_journals"
        ]["legacy_directory_policy"],
        "required_roots_supplied_exactly_once": True,
        "legacy_premium_authoritative_occurrence_count": 0,
    }


def apply_canonical_path_policy(
    path: Path,
    classification: dict[str, Any],
    scope: AuthorizedScope,
) -> dict[str, Any]:
    """Make requested-container authorization path-aware and fail closed."""

    if classification.get("included") is not True:
        return classification
    requested_id = _exact_id(classification.get("requested_container_id"))
    parent_id = _exact_id(classification.get("parent_container_id"))
    premium_related = requested_id == PREMIUM_PARENT_ID or parent_id == PREMIUM_PARENT_ID
    expected_root = scope.premium_v2_5_root if premium_related else scope.canonical_root
    if path.resolve().parent == expected_root.resolve():
        return classification
    updated = copy.deepcopy(classification)
    updated["included"] = False
    if premium_related and path.resolve().parent == scope.canonical_root.resolve():
        updated["classification"] = "preservation_only"
        updated["reason"] = "premium_journals_legacy_directory_preservation_only"
    else:
        updated["classification"] = "ambiguous_fail_closed"
        updated["reason"] = "authorized_container_in_non_authoritative_directory"
    return updated


def _trusted_discord_evidence(
    evidence: dict[str, Any], *, guild_id: str, parent_id: str, child_id: str
) -> bool:
    if (
        evidence.get("authenticated") is not True
        or evidence.get("source_scope") != "discord_only"
        or evidence.get("outside_sources_used") is not False
    ):
        return False
    method = str(evidence.get("method") or "")
    if method not in TRUSTED_CHILD_EVIDENCE_METHODS:
        return False
    # A Discord URL proves only guild + destination child identity.  It says
    # nothing about which forum owns that child and can therefore never prove
    # parentage by itself.  Baseline inventory parentage is accepted only from
    # Discord's exact parent___child forum-card identifier.  Group-navigation
    # evidence is accepted separately by the byte-bound reconciliation loader,
    # where query, page, row membership, parent and source hashes are all bound.
    return bool(
        method == "forum_card_data_list_item_id"
        and evidence.get("forum_card_data_list_item_id")
        == f"forum-channel-list-{parent_id}___{child_id}"
    )


def proven_child_relationships(
    inventory_payload: dict[str, Any], scope: AuthorizedScope
) -> dict[str, dict[str, Any]]:
    """Return child -> parent only for exact authenticated inventory evidence."""

    if _exact_id(inventory_payload.get("guild_id")) != scope.guild_id:
        raise AuthorizedScopeError("Inventory guild does not match authorized scope")
    raw_rows = inventory_payload.get("containers")
    if not isinstance(raw_rows, list):
        raw_rows = inventory_payload.get("channels")
    if not isinstance(raw_rows, list):
        raise AuthorizedScopeError("Inventory must expose containers or channels")

    relationships: dict[str, dict[str, Any]] = {}
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        child_id = _exact_id(
            raw.get("container_id") or raw.get("thread_id") or raw.get("channel_id")
        )
        parent_id = _exact_id(
            raw.get("parent_container_id")
            or raw.get("parent_forum_channel_id")
            or raw.get("parent_channel_id")
            or raw.get("parent_id")
        )
        if (
            not child_id
            or not parent_id
            or child_id == parent_id
            or parent_id not in scope.parent_ids
            or not scope.containers_by_id[parent_id].include_exact_child_threads
        ):
            continue
        identity = raw.get("identity_provenance")
        identity = identity if isinstance(identity, dict) else {}
        evidence_rows = identity.get("evidence")
        evidence_rows = evidence_rows if isinstance(evidence_rows, list) else []
        exact = bool(identity.get("exact_row_owned_evidence") is True)
        trusted = [
            copy.deepcopy(item)
            for item in evidence_rows
            if isinstance(item, dict)
            and _trusted_discord_evidence(
                item,
                guild_id=scope.guild_id,
                parent_id=parent_id,
                child_id=child_id,
            )
        ]
        # ``exact_row_owned_evidence`` is a merged-inventory assertion, but it
        # must still carry at least one independently inspectable evidence row.
        if not exact or not trusted:
            continue
        if child_id in relationships and relationships[child_id]["parent_container_id"] != parent_id:
            raise AuthorizedScopeError(
                f"Child container {child_id} has conflicting proven parents"
            )
        relationships[child_id] = {
            "child_container_id": child_id,
            "parent_container_id": parent_id,
            "inventory_layer": raw.get("inventory_layer"),
            "kind": raw.get("kind"),
            "evidence_method_count": len(trusted),
            "evidence_methods": sorted(
                {str(item.get("method") or "") for item in trusted}
            ),
            "forum_card_data_list_item_id": (
                f"forum-channel-list-{parent_id}___{child_id}"
            ),
            "parent_child_binding_sha256": _sha256_bytes(
                _compact_json(
                    {
                        "guild_id": scope.guild_id,
                        "parent_container_id": parent_id,
                        "child_container_id": child_id,
                        "forum_card_data_list_item_id": (
                            f"forum-channel-list-{parent_id}___{child_id}"
                        ),
                    }
                ).encode("utf-8")
            ),
        }
    return relationships


def load_proven_child_relationships(
    inventory_path: Path | None, scope: AuthorizedScope
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    if inventory_path is None:
        return {}, None
    payload = _json_object(inventory_path.resolve(), "channel inventory")
    return proven_child_relationships(payload, scope), payload


def load_scoped_child_inventory_reconciliation(
    path: Path | None,
    scope: AuthorizedScope,
    baseline_relationships: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    """Validate and add exact Premium Journals IDs without inheriting false closure.

    The reconciliation is deliberately additive.  Exact new identities may be
    used for selection immediately, while ``closure_proven=false`` must continue
    to block scoped inventory/release completeness.
    """

    if path is None:
        return copy.deepcopy(baseline_relationships), None
    resolved = path.resolve()
    payload = _json_object(resolved, "scoped child inventory reconciliation")
    errors: list[str] = []
    if payload.get("schema_version") != "1.0.0":
        errors.append("schema_version_invalid")
    if payload.get("artifact_type") != "scoped_forum_thread_inventory_reconciliation":
        errors.append("artifact_type_invalid")
    if _exact_id(payload.get("guild_id")) != scope.guild_id:
        errors.append("guild_id_mismatch")
    parent_id = _exact_id(payload.get("parent_forum_channel_id"))
    if parent_id != "1283941772577472643" or parent_id not in scope.parent_ids:
        errors.append("parent_forum_not_authorized_premium_journals")
    if payload.get("source_scope") != "authenticated_discord_only":
        errors.append("source_scope_invalid")
    if payload.get("outside_sources_used") is not False:
        errors.append("outside_sources_used_not_false")

    baseline_ids = [str(value) for value in payload.get("baseline_thread_ids") or []]
    added_ids = [str(value) for value in payload.get("added_thread_ids") or []]
    union_ids = [str(value) for value in payload.get("exact_known_union_thread_ids") or []]
    if any(not SNOWFLAKE_RE.fullmatch(value) for value in baseline_ids + added_ids + union_ids):
        errors.append("one_or_more_thread_ids_invalid")
    if len(set(baseline_ids)) != len(baseline_ids):
        errors.append("baseline_thread_ids_duplicate")
    if len(set(added_ids)) != len(added_ids):
        errors.append("added_thread_ids_duplicate")
    if set(baseline_ids) & set(added_ids):
        errors.append("baseline_and_added_thread_ids_overlap")
    if set(union_ids) != set(baseline_ids) | set(added_ids) or len(union_ids) != len(
        set(union_ids)
    ):
        errors.append("exact_known_union_is_not_exact_set_union")
    baseline_premium_ids = {
        child_id
        for child_id, row in baseline_relationships.items()
        if row.get("parent_container_id") == parent_id
    }
    if set(baseline_ids) != baseline_premium_ids:
        errors.append("reconciliation_baseline_does_not_match_proven_inventory_baseline")

    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    expected_counts = {
        "baseline_exact_thread_ids": len(set(baseline_ids)),
        "exact_additional_thread_ids": len(set(added_ids)),
        "exact_known_union_thread_ids": len(set(union_ids)),
    }
    for key, expected in expected_counts.items():
        if counts.get(key) != expected:
            errors.append(f"count_mismatch:{key}")

    corpus_root = resolved.parent.parent.resolve()
    bound_inputs: list[dict[str, Any]] = []

    def resolve_binding(
        label: str, binding: Any, *, path_key: str = "path", sha_key: str = "sha256"
    ) -> tuple[Path | None, str | None]:
        binding = binding if isinstance(binding, dict) else {}
        declared_path = str(binding.get(path_key) or "")
        declared_sha = str(binding.get(sha_key) or "").casefold()
        if not declared_path or Path(declared_path).is_absolute():
            errors.append(f"{label}_path_missing_or_absolute")
            return None, None
        if not SHA256_RE.fullmatch(declared_sha):
            errors.append(f"{label}_sha256_invalid")
            return None, None
        source = (corpus_root / declared_path).resolve()
        try:
            source.relative_to(corpus_root)
        except ValueError:
            errors.append(f"{label}_path_escapes_corpus_root")
            return None, None
        if not source.is_file() or _sha256_file(source) != declared_sha:
            errors.append(f"{label}_source_hash_mismatch_or_missing")
            return None, None
        bound_inputs.append(
            {
                "role": label,
                "relative_path": declared_path.replace("\\", "/"),
                "sha256": declared_sha,
                "size_bytes": source.stat().st_size,
            }
        )
        return source, declared_sha

    baseline_path, _baseline_sha = resolve_binding("baseline", payload.get("baseline"))
    evidence_binding = (
        payload.get("additive_evidence_source")
        if isinstance(payload.get("additive_evidence_source"), dict)
        else {}
    )
    evidence_path, evidence_sha = resolve_binding(
        "additive_evidence_source", evidence_binding
    )
    partial_path, partial_sha = resolve_binding(
        "additive_evidence_bound_partial",
        evidence_binding,
        path_key="bound_partial_path",
        sha_key="bound_partial_sha256",
    )

    # The baseline bytes are not just hashed: the exact thread set in those
    # bytes must equal the already independently proven Premium baseline.
    if baseline_path is not None:
        baseline_payload = _json_object(baseline_path, "reconciliation baseline")
        baseline_rows = baseline_payload.get("threads")
        if isinstance(baseline_rows, list):
            baseline_source_ids = {
                str(row.get("thread_id") or "")
                for row in baseline_rows
                if isinstance(row, dict) and _exact_id(row.get("thread_id"))
            }
        else:
            raw_ids = baseline_payload.get("thread_ids")
            baseline_source_ids = {
                str(value) for value in raw_ids or [] if _exact_id(value)
            }
        if baseline_source_ids != set(baseline_ids):
            errors.append("baseline_source_thread_set_mismatch")
        if _exact_id(baseline_payload.get("guild_id")) not in {None, scope.guild_id}:
            errors.append("baseline_source_guild_mismatch")
        source_parent = _exact_id(baseline_payload.get("parent_forum_channel_id"))
        if source_parent not in {None, parent_id}:
            errors.append("baseline_source_parent_mismatch")

    evidence_payload: dict[str, Any] = {}
    partial_payload: dict[str, Any] = {}
    if evidence_path is not None:
        evidence_payload = _json_object(
            evidence_path, "authenticated group-navigation evidence"
        )
    if partial_path is not None:
        partial_payload = _json_object(partial_path, "bound collection partial")

    expected_parent_name = scope.containers_by_id[
        "1283941772577472643"
    ].name
    evidence_query = str(evidence_payload.get("query") or "").strip()
    binding_query = str(evidence_binding.get("query") or "").strip()
    evidence_page = evidence_payload.get("page_number")
    binding_page = evidence_binding.get("page_number")
    if not (
        evidence_payload.get("schema_version") == "1.0.0"
        and evidence_payload.get("evidence_type")
        == "authenticated_discord_search_group_header_navigation"
        and _exact_id(evidence_payload.get("guild_id")) == scope.guild_id
        and _exact_id(evidence_payload.get("parent_forum_channel_id")) == parent_id
        and _normalized_identity_text(
            evidence_payload.get("parent_forum_channel_name")
        )
        == _normalized_identity_text(expected_parent_name)
    ):
        errors.append("additive_evidence_parent_or_identity_invalid")
    if not evidence_query or evidence_query != binding_query:
        errors.append("additive_evidence_query_binding_mismatch")
    if type(evidence_page) is not int or evidence_page < 1 or evidence_page != binding_page:
        errors.append("additive_evidence_page_binding_mismatch")
    if evidence_payload.get("source_partial_sha256") != partial_sha:
        errors.append("additive_evidence_partial_sha_binding_mismatch")
    if str(evidence_payload.get("source_partial_path") or "").replace(
        "\\", "/"
    ) != str(evidence_binding.get("bound_partial_path") or "").replace("\\", "/"):
        errors.append("additive_evidence_partial_path_binding_mismatch")
    target, query_errors = parse_discord_search_query(evidence_query)
    errors.extend(f"additive_evidence_{value}" for value in query_errors)
    if _normalized_identity_text(target) != _normalized_identity_text(expected_parent_name):
        errors.append("additive_evidence_query_parent_name_mismatch")

    requested = (
        partial_payload.get("requested_container")
        if isinstance(partial_payload.get("requested_container"), dict)
        else {}
    )
    partial_segment = (
        partial_payload.get("segment")
        if isinstance(partial_payload.get("segment"), dict)
        else {}
    )
    requested_name = str(requested.get("channel_name") or "").strip()
    if not (
        _exact_id(partial_payload.get("guild_id")) == scope.guild_id
        and _exact_id(requested.get("channel_id")) == parent_id
        and str(requested.get("channel_id_source") or "")
        in TRUSTED_REQUEST_ID_SOURCES
        and _normalized_identity_text(requested_name)
        == _normalized_identity_text(expected_parent_name)
        and _normalized_identity_text(requested.get("channel_kind"))
        == _normalized_identity_text("forum channel")
        and str(partial_segment.get("query") or "").strip() == evidence_query
    ):
        errors.append("bound_partial_requested_parent_or_query_mismatch")

    partial_rows = partial_payload.get("messages")
    partial_rows = partial_rows if isinstance(partial_rows, list) else []
    page_membership: dict[tuple[int, str], dict[str, Any]] = {}
    for row in partial_rows:
        if not isinstance(row, dict) or row.get("page_number") != evidence_page:
            continue
        result_index = row.get("result_index")
        message_id = _exact_id(row.get("message_id"))
        if type(result_index) is not int or result_index < 1 or not message_id:
            errors.append("bound_partial_page_row_identity_invalid")
            continue
        if not (
            str(row.get("search_query") or "").strip() == evidence_query
            and _exact_id(row.get("collection_channel_id")) == parent_id
            and _normalized_identity_text(row.get("collection_channel_name"))
            == _normalized_identity_text(expected_parent_name)
            and _normalized_identity_text(row.get("collection_channel_kind"))
            == _normalized_identity_text("forum channel")
        ):
            errors.append(
                f"bound_partial_page_row_provenance_mismatch:{result_index}:{message_id}"
            )
            continue
        key = (result_index, message_id)
        if key in page_membership:
            errors.append("bound_partial_page_membership_duplicate")
        page_membership[key] = row
    if not page_membership:
        errors.append("bound_partial_has_no_rows_for_evidence_page")

    page_validation = (
        evidence_payload.get("page_validation")
        if isinstance(evidence_payload.get("page_validation"), dict)
        else {}
    )
    expected_back_url = f"https://discord.com/channels/{scope.guild_id}/{parent_id}"
    if not all(
        page_validation.get(key) is True
        for key in (
            "all_result_indices_contiguous",
            "all_result_indices_unique",
            "all_message_ids_unique",
            "direct_child_header_count_equaled_group_count",
            "back_return_same_query_page_verified",
        )
    ) or page_validation.get("back_return_parent_url") != expected_back_url:
        errors.append("additive_evidence_page_validation_invalid")

    source_groups = evidence_payload.get("groups")
    source_groups = source_groups if isinstance(source_groups, list) else []
    source_group_by_ordinal: dict[int, dict[str, Any]] = {}
    source_membership_keys: set[tuple[int, str]] = set()
    for group in source_groups:
        if not isinstance(group, dict):
            errors.append("additive_evidence_group_not_object")
            continue
        ordinal = group.get("group_ordinal")
        thread_id = _exact_id(group.get("observed_thread_id"))
        indices = group.get("result_indices")
        message_ids = group.get("message_ids")
        indices = indices if isinstance(indices, list) else []
        message_ids = message_ids if isinstance(message_ids, list) else []
        destination = str(group.get("click_destination_url") or "")
        expected_destination = (
            f"https://discord.com/channels/{scope.guild_id}/{thread_id}"
            if thread_id
            else ""
        )
        pairs = list(zip(indices, [str(value) for value in message_ids]))
        valid_pairs = bool(pairs) and len(indices) == len(message_ids) and all(
            type(index) is int
            and index >= 1
            and _exact_id(message_id)
            and (index, message_id) in page_membership
            for index, message_id in pairs
        )
        if not (
            type(ordinal) is int
            and ordinal >= 1
            and ordinal not in source_group_by_ordinal
            and thread_id
            and destination == expected_destination
            and _exact_id(group.get("observed_guild_id")) == scope.guild_id
            and group.get("unique_direct_child_header_within_group") is True
            and group.get("back_return_succeeded") is True
            and group.get("thread_identity_exact") is True
            and valid_pairs
        ):
            errors.append(f"additive_evidence_source_group_invalid:{ordinal}")
            continue
        source_group_by_ordinal[ordinal] = group
        source_membership_keys.update((index, message_id) for index, message_id in pairs)
    if source_membership_keys != set(page_membership):
        errors.append("additive_evidence_groups_do_not_exactly_cover_bound_page_rows")

    observations = payload.get("navigation_observations")
    observations = observations if isinstance(observations, list) else []
    evidence_by_thread: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        if not isinstance(observation, dict):
            errors.append("navigation_observation_not_object")
            continue
        thread_id = _exact_id(observation.get("thread_id"))
        if not thread_id or thread_id not in set(union_ids):
            errors.append("navigation_observation_thread_not_in_union")
            continue
        groups = observation.get("exact_group_evidence")
        groups = groups if isinstance(groups, list) else []
        if not groups:
            errors.append(f"navigation_group_evidence_missing:{thread_id}")
            continue
        valid_groups: list[dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict):
                errors.append(f"navigation_group_not_object:{thread_id}")
                continue
            ordinal = group.get("group_ordinal")
            source_group = source_group_by_ordinal.get(ordinal)
            membership = group.get("exact_message_membership")
            membership = membership if isinstance(membership, list) else []
            declared_pairs = [
                (item.get("result_index"), str(item.get("message_id") or ""))
                for item in membership
                if isinstance(item, dict)
            ]
            source_pairs = (
                list(
                    zip(
                        source_group.get("result_indices") or [],
                        [str(value) for value in source_group.get("message_ids") or []],
                    )
                )
                if source_group
                else []
            )
            expected_destination = f"https://discord.com/channels/{scope.guild_id}/{thread_id}"
            if not (
                source_group
                and _exact_id(source_group.get("observed_thread_id")) == thread_id
                and group.get("thread_id") == thread_id
                and group.get("identity_method")
                == "forum_group_header_navigation_exact"
                and group.get("destination_url") == expected_destination
                and declared_pairs == source_pairs
                and declared_pairs
                and all(pair in page_membership for pair in declared_pairs)
            ):
                errors.append(f"navigation_group_evidence_invalid:{thread_id}:{ordinal}")
                continue
            valid_groups.append(copy.deepcopy(group))
        if valid_groups:
            evidence_by_thread.setdefault(thread_id, []).extend(valid_groups)

    for thread_id in added_ids:
        matching_observation = next(
            (
                row
                for row in observations
                if isinstance(row, dict)
                and row.get("thread_id") == thread_id
                and row.get("classification") == "exact_addition"
            ),
            None,
        )
        if thread_id not in evidence_by_thread or matching_observation is None:
            errors.append(f"exact_addition_lacks_bound_navigation_evidence:{thread_id}")
    if errors:
        raise AuthorizedScopeError(
            "Scoped child inventory reconciliation failed validation: "
            + "; ".join(sorted(set(errors)))
        )

    relationships = copy.deepcopy(baseline_relationships)
    assert parent_id is not None
    for thread_id in added_ids:
        relationships[thread_id] = {
            "child_container_id": thread_id,
            "parent_container_id": parent_id,
            "inventory_layer": "reconciled_exact_forum_thread",
            "kind": "forum thread",
            "evidence_method_count": len(evidence_by_thread[thread_id]),
            "evidence_methods": ["forum_group_header_navigation_exact"],
            "relationship_source": "scoped_forum_thread_inventory_reconciliation",
            "reconciliation_source_sha256": _sha256_file(resolved),
            "census_closure_proven": bool(payload.get("closure_proven") is True),
        }

    closure_proven = bool(payload.get("closure_proven") is True)
    if closure_proven:
        if not (
            payload.get("inventory_complete") is True
            and payload.get("enumeration_complete") is True
            and payload.get("status") == "complete"
        ):
            raise AuthorizedScopeError(
                "Reconciliation claims closure without complete inventory/enumeration status"
            )
    else:
        if (
            payload.get("inventory_complete") is not False
            or payload.get("enumeration_complete") is not False
            or payload.get("status") != "unresolved_census"
        ):
            raise AuthorizedScopeError(
                "Unclosed reconciliation must remain unresolved_census and incomplete"
            )
    summary = {
        "provided": True,
        "source_sha256": _sha256_file(resolved),
        "source_size_bytes": resolved.stat().st_size,
        "status": payload.get("status"),
        "inventory_complete": payload.get("inventory_complete") is True,
        "enumeration_complete": payload.get("enumeration_complete") is True,
        "closure_proven": closure_proven,
        "baseline_exact_thread_count": len(baseline_ids),
        "exact_additional_thread_count": len(added_ids),
        "exact_known_union_thread_count": len(union_ids),
        "exact_known_union_thread_ids_sha256": _sha256_bytes(
            _compact_json(sorted(union_ids)).encode("utf-8")
        ),
        "added_thread_ids": sorted(added_ids),
        "added_thread_ids_sha256": _sha256_bytes(
            _compact_json(sorted(added_ids)).encode("utf-8")
        ),
        "bound_inputs": bound_inputs,
        "release_effect": (
            "blocks_inventory_and_release_completeness_until_fresh_census_closure"
            if not closure_proven
            else "census_closure_proven"
        ),
        "raw_source_bytes_mutated": False,
    }
    return relationships, summary


def classify_segment_payload(
    payload: dict[str, Any],
    scope: AuthorizedScope,
    proven_children: dict[str, dict[str, Any]],
    *,
    parent_name_aliases: dict[str, frozenset[str]] | None = None,
) -> dict[str, Any]:
    """Classify a raw segment without filename, row, title, or CDN inference."""

    requested = payload.get("requested_container")
    requested = requested if isinstance(requested, dict) else {}
    segment = payload.get("segment") if isinstance(payload.get("segment"), dict) else {}
    requested_id = _exact_id(
        requested.get("channel_id")
        or requested.get("container_id")
        or requested.get("thread_id")
    )
    requested_id_source = str(
        requested.get("channel_id_source")
        or requested.get("container_id_source")
        or requested.get("thread_id_source")
        or ""
    ).strip()
    query = str(segment.get("query") or segment.get("search_query") or "").strip()
    requested_name = str(
        requested.get("channel_name")
        or requested.get("container_name")
        or requested.get("thread_name")
        or ""
    ).strip()
    requested_kind = str(
        requested.get("channel_kind")
        or requested.get("container_kind")
        or requested.get("thread_kind")
        or ""
    ).strip()
    guild_id = _exact_id(payload.get("guild_id"))
    ambiguous_reasons: list[str] = []
    if guild_id != scope.guild_id:
        ambiguous_reasons.append("segment_guild_missing_or_mismatch")
    if not requested_id:
        ambiguous_reasons.append("exact_requested_container_id_missing")
    if requested_id_source not in TRUSTED_REQUEST_ID_SOURCES:
        ambiguous_reasons.append("requested_container_id_source_not_authenticated_exact")
    query_target, query_errors = parse_discord_search_query(query)
    ambiguous_reasons.extend(query_errors)
    if not query:
        ambiguous_reasons.append("authenticated_date_bounded_search_query_missing")
    payload_query = str(payload.get("query") or "").strip()
    if payload_query and payload_query != query:
        ambiguous_reasons.append("payload_segment_query_mismatch")
    if _normalized_identity_text(query_target) != _normalized_identity_text(
        requested_name
    ):
        ambiguous_reasons.append("query_in_target_requested_container_name_mismatch")

    relationship = proven_children.get(requested_id or "")
    if requested_id in scope.parent_ids:
        allowed_names = (
            (parent_name_aliases or {}).get(requested_id or "")
            or frozenset(
                {
                    _normalized_identity_text(
                        scope.containers_by_id[requested_id].name  # type: ignore[index]
                    )
                }
            )
        )
        if _normalized_identity_text(requested_name) not in allowed_names:
            ambiguous_reasons.append("requested_parent_name_not_authorized_inventory_alias")
        expected_kind = scope.containers_by_id[requested_id].kind  # type: ignore[index]
        if _normalized_identity_text(requested_kind) != _normalized_identity_text(
            expected_kind
        ):
            ambiguous_reasons.append("requested_parent_kind_mismatch")
    elif relationship:
        if not requested_name:
            ambiguous_reasons.append("requested_child_name_missing")
        if _normalized_identity_text(requested_kind) not in {
            _normalized_identity_text("forum thread"),
            _normalized_identity_text("thread"),
            _normalized_identity_text("text channel"),
        }:
            ambiguous_reasons.append("requested_child_kind_invalid")

    completion = (
        payload.get("completion_evidence")
        if isinstance(payload.get("completion_evidence"), dict)
        else {}
    )
    completion_queries: list[tuple[str, Any]] = [
        ("completion_evidence", completion.get("query")),
    ]
    search_submission = (
        completion.get("search_submission")
        if isinstance(completion.get("search_submission"), dict)
        else {}
    )
    completion_queries.append(
        ("completion_evidence_search_submission", search_submission.get("query"))
    )
    for state_name in ("stable_bottom", "stable_empty"):
        state = completion.get(state_name)
        state = state if isinstance(state, dict) else {}
        observations = state.get("observations")
        observations = observations if isinstance(observations, list) else []
        for index, observation in enumerate(observations, start=1):
            if isinstance(observation, dict):
                completion_queries.append(
                    (
                        f"completion_evidence_{state_name}_observation_{index}",
                        observation.get("query"),
                    )
                )
    for label, value in completion_queries:
        text = str(value or "").strip()
        if text and text != query:
            ambiguous_reasons.append(f"{label}_query_mismatch")

    rows = payload.get("messages")
    rows = rows if isinstance(rows, list) else []
    expected_collection_id = requested_id
    expected_collection_name = requested_name
    expected_collection_kind = requested_kind
    if requested_id in scope.parent_ids:
        expected_collection_kind = scope.containers_by_id[requested_id].kind  # type: ignore[index]
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            ambiguous_reasons.append(f"row_{index}_not_object")
            continue
        row_query = str(row.get("search_query") or "").strip()
        row_id = _exact_id(row.get("collection_channel_id"))
        row_name = str(row.get("collection_channel_name") or "").strip()
        row_kind = str(row.get("collection_channel_kind") or "").strip()
        row_id_source = str(row.get("collection_channel_id_source") or "").strip()
        if row_query != query:
            ambiguous_reasons.append(f"row_{index}_search_query_mismatch_or_missing")
        if row_id != expected_collection_id:
            ambiguous_reasons.append(f"row_{index}_collection_channel_id_mismatch_or_missing")
        if _normalized_identity_text(row_name) != _normalized_identity_text(
            expected_collection_name
        ):
            ambiguous_reasons.append(f"row_{index}_collection_channel_name_mismatch_or_missing")
        if _normalized_identity_text(row_kind) != _normalized_identity_text(
            expected_collection_kind
        ):
            ambiguous_reasons.append(f"row_{index}_collection_channel_kind_mismatch_or_missing")
        if row_id_source not in TRUSTED_REQUEST_ID_SOURCES:
            ambiguous_reasons.append(
                f"row_{index}_collection_channel_id_source_not_authenticated_exact"
            )
        declared_parent_ids = {
            value
            for value in (
                _exact_id(row.get("parent_channel_id")),
                _exact_id(row.get("group_header_parent_forum_channel_id")),
                _exact_id(row.get("parent_id")),
                _exact_id(row.get("forum_channel_id")),
                _exact_id(row.get("thread_parent_id")),
            )
            if value
        }
        expected_parent_id = (
            str(relationship.get("parent_container_id")) if relationship else None
        )
        if expected_parent_id and declared_parent_ids and declared_parent_ids != {
            expected_parent_id
        }:
            ambiguous_reasons.append(f"row_{index}_declared_parent_conflict")
        if requested_id in scope.parent_ids and declared_parent_ids and requested_id not in declared_parent_ids:
            ambiguous_reasons.append(f"row_{index}_declared_parent_not_requested_forum")
    if ambiguous_reasons:
        return {
            "included": False,
            "classification": "ambiguous_fail_closed",
            "reason": "+".join(sorted(set(ambiguous_reasons))),
            "requested_container_id": requested_id,
            "requested_container_id_source": requested_id_source or None,
            "query": query or None,
            "query_in_target": query_target,
            "requested_container_name": requested_name or None,
            "requested_container_kind": requested_kind or None,
            "parent_container_id": None,
        }
    assert requested_id is not None
    if requested_id in scope.parent_ids:
        return {
            "included": True,
            "classification": "authorized_parent_query",
            "reason": "exact_authenticated_requested_parent_allowed",
            "requested_container_id": requested_id,
            "requested_container_id_source": requested_id_source,
            "query": query,
            "query_in_target": query_target,
            "requested_container_name": requested_name,
            "requested_container_kind": requested_kind,
            "parent_container_id": requested_id,
        }
    child = relationship
    if child:
        return {
            "included": True,
            "classification": "authorized_proven_child_query",
            "reason": "exact_authenticated_requested_child_has_proven_allowed_parent",
            "requested_container_id": requested_id,
            "requested_container_id_source": requested_id_source,
            "query": query,
            "query_in_target": query_target,
            "requested_container_name": requested_name,
            "requested_container_kind": requested_kind,
            "parent_container_id": child["parent_container_id"],
        }
    return {
        "included": False,
        "classification": "outside_authorized_scope",
        "reason": "exact_requested_container_not_authorized",
        "requested_container_id": requested_id,
        "requested_container_id_source": requested_id_source,
        "query": query,
        "query_in_target": query_target,
        "requested_container_name": requested_name or None,
        "requested_container_kind": requested_kind or None,
        "parent_container_id": None,
    }


def evaluate_premium_journals_message_scope_closure(
    *,
    scope: AuthorizedScope,
    segments: Sequence[dict[str, Any]],
    occurrences: Sequence[dict[str, Any]],
    proven_children: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Prove closure for message-bearing Premium Journals data in the date window.

    This deliberately does *not* require proof about zero-message, inaccessible,
    or otherwise undiscoverable threads outside the searched window.  The unit
    of completeness is the authenticated parent-forum search result set for all
    201 local dates plus exact row-to-child binding for every captured result.
    """

    parent_id = "1283941772577472643"
    start = dt.date.fromisoformat(scope.start_date_inclusive)
    end = dt.date.fromisoformat(scope.end_date_inclusive)
    required_dates = {
        start + dt.timedelta(days=index)
        for index in range((end - start).days + 1)
    }
    parent_segments = [
        row
        for row in segments
        if str(row.get("query_container_id") or "") == parent_id
        and row.get("input_role") == "channel_capture"
    ]
    complete_dates: set[dt.date] = set()
    terminal_invalid_segments: list[str] = []
    incomplete_segments: list[str] = []
    invalid_daily_partition_segments: list[str] = []
    accepted_daily_dates: list[dt.date] = []
    for segment in parent_segments:
        segment_id = str(segment.get("segment_id") or "")
        evidence = segment.get("completion_evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        terminal_state = evidence.get("terminal_state")
        terminal_valid = bool(
            segment.get("completion_evidence_valid") is True
            and terminal_state in {"stable_empty", "stable_bottom"}
        )
        if not terminal_valid:
            terminal_invalid_segments.append(segment_id)
        if not segment.get("computed_complete"):
            incomplete_segments.append(segment_id)
            continue
        try:
            segment_start = dt.date.fromisoformat(str(segment.get("start_date") or ""))
            segment_end = dt.date.fromisoformat(str(segment.get("end_date") or ""))
        except ValueError:
            incomplete_segments.append(segment_id)
            continue
        expected_query = (
            "in:premium-journals "
            f"after:{(segment_start - dt.timedelta(days=1)).isoformat()} "
            f"before:{(segment_start + dt.timedelta(days=1)).isoformat()}"
        )
        exact_daily_partition = bool(
            segment_start == segment_end
            and segment_start in required_dates
            and str(segment.get("query") or "") == expected_query
        )
        if not exact_daily_partition:
            invalid_daily_partition_segments.append(segment_id)
            continue
        if terminal_valid:
            complete_dates.add(segment_start)
            accepted_daily_dates.append(segment_start)

    parent_occurrences = [
        row
        for row in occurrences
        if str(row.get("query_container_id") or "") == parent_id
        and row.get("source_kind") == "channel_segment"
    ]
    trusted_sources = {
        "forum_group_header_data_list_item_id",
        "forum_group_header_navigation_exact",
        "forum_group_owned_reply_anchor_exact",
    }
    trusted_permalink_statuses = {
        "thread_id_from_forum_group_header",
        "thread_id_from_forum_group_header_navigation",
    }
    unresolved_occurrence_ids: list[str] = []
    observed_child_ids: set[str] = set()
    conflict_occurrence_ids: list[str] = []
    for occurrence in parent_occurrences:
        occurrence_id = str(occurrence.get("occurrence_id") or "")
        child_id = _exact_id(occurrence.get("message_container_id"))
        parent_binding = _exact_id(occurrence.get("parent_container_id"))
        payload = occurrence.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        source = str(payload.get("thread_channel_id_source") or "")
        permalink_status = str(payload.get("exact_permalink_status") or "")
        row_exact = bool(
            child_id
            and child_id != parent_id
            and parent_binding == parent_id
            and occurrence.get("message_container_id_source")
            == "premium_whole_artifact_byte_bound_row_mapping"
            and (
                source in trusted_sources
                or permalink_status in trusted_permalink_statuses
            )
        )
        reasons = set(occurrence.get("quarantine_reasons") or [])
        conflict = bool(
            "message_container_does_not_match_query_container_or_parent" in reasons
            or "conflicting_exact_message_container_ids" in reasons
        )
        if conflict:
            conflict_occurrence_ids.append(occurrence_id)
        if not row_exact:
            unresolved_occurrence_ids.append(occurrence_id)
            continue
        assert child_id is not None
        observed_child_ids.add(child_id)

    baseline_union_ids = {
        child_id
        for child_id, row in proven_children.items()
        if row.get("parent_container_id") == parent_id
    }
    union_ids = baseline_union_ids | observed_child_ids
    observed_outside_union = sorted(observed_child_ids - union_ids)
    missing_dates = sorted(required_dates - complete_dates)
    duplicate_daily_dates = sorted(
        value
        for value, count in Counter(accepted_daily_dates).items()
        if count > 1
    )

    def compressed(values: Sequence[dt.date]) -> list[dict[str, Any]]:
        if not values:
            return []
        output: list[dict[str, Any]] = []
        range_start = values[0]
        previous = values[0]
        for value in values[1:]:
            if value != previous + dt.timedelta(days=1):
                output.append(
                    {
                        "start_date": range_start.isoformat(),
                        "end_date": previous.isoformat(),
                    }
                )
                range_start = value
            previous = value
        output.append(
            {
                "start_date": range_start.isoformat(),
                "end_date": previous.isoformat(),
            }
        )
        return output

    passed = bool(
        parent_segments
        and len(parent_segments) == len(required_dates)
        and not missing_dates
        and not incomplete_segments
        and not terminal_invalid_segments
        and not invalid_daily_partition_segments
        and not duplicate_daily_dates
        and not unresolved_occurrence_ids
        and not conflict_occurrence_ids
        and not observed_outside_union
    )
    return {
        "gate": "premium_journals_message_data_scope_closure",
        "passed": passed,
        "closure_proven": passed,
        "status": "complete" if passed else "unresolved_census",
        "scope_definition": (
            "All Jan 1-Jul 20 Premium Journals parent-forum date searches are "
            "strictly complete with terminal evidence; every captured result is "
            "bound to an exact authenticated child ID; conflicts are zero; and "
            "the derived child union covers every observed message-bearing child."
        ),
        "required_parent_container_id": parent_id,
        "required_calendar_day_count": len(required_dates),
        "complete_calendar_day_count": len(complete_dates),
        "missing_date_ranges": compressed(missing_dates),
        "parent_segment_count": len(parent_segments),
        "required_exact_daily_parent_segment_count": len(required_dates),
        "invalid_daily_partition_segment_count": len(
            set(invalid_daily_partition_segments)
        ),
        "duplicate_daily_date_count": len(duplicate_daily_dates),
        "incomplete_segment_count": len(set(incomplete_segments)),
        "terminal_evidence_invalid_segment_count": len(
            set(terminal_invalid_segments)
        ),
        "captured_parent_forum_occurrence_count": len(parent_occurrences),
        "unresolved_row_binding_count": len(set(unresolved_occurrence_ids)),
        "row_binding_conflict_count": len(set(conflict_occurrence_ids)),
        "observed_message_bearing_child_count": len(observed_child_ids),
        "baseline_exact_child_union_count": len(baseline_union_ids),
        "new_exact_observed_child_count": len(
            observed_child_ids - baseline_union_ids
        ),
        "derived_exact_child_union_count": len(union_ids),
        "observed_child_outside_derived_union_count": len(observed_outside_union),
        "observed_child_set_sha256": _sha256_bytes(
            _compact_json(sorted(observed_child_ids)).encode("utf-8")
        ),
        "derived_child_union_sha256": _sha256_bytes(
            _compact_json(sorted(union_ids)).encode("utf-8")
        ),
        "baseline_child_union_sha256": _sha256_bytes(
            _compact_json(sorted(baseline_union_ids)).encode("utf-8")
        ),
        "outside_message_bearing_scope": (
            "Zero-message, inaccessible, out-of-window, or otherwise undiscoverable "
            "threads outside the Jan 1-Jul 20 authenticated parent-forum search result "
            "set are not required for message-data closure."
        ),
    }


def extend_proven_children_from_premium_occurrences(
    proven_children: dict[str, dict[str, Any]],
    occurrences: Sequence[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Add only row-owned exact v2.5 child bindings after closure was audited."""

    output = copy.deepcopy(proven_children)
    for occurrence in occurrences:
        if (
            str(occurrence.get("query_container_id") or "") != PREMIUM_PARENT_ID
            or occurrence.get("source_kind") != "channel_segment"
        ):
            continue
        child_id = _exact_id(occurrence.get("message_container_id"))
        parent_id = _exact_id(occurrence.get("parent_container_id"))
        payload = occurrence.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        source = str(payload.get("thread_channel_id_source") or "")
        evidence = payload.get("forum_group_navigation_evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        validation = payload.get("forum_group_navigation_validation")
        validation = validation if isinstance(validation, dict) else {}
        if not (
            child_id
            and child_id != PREMIUM_PARENT_ID
            and parent_id == PREMIUM_PARENT_ID
            and source
            in {
                "forum_group_header_data_list_item_id",
                "forum_group_header_navigation_exact",
                "forum_group_owned_reply_anchor_exact",
            }
            and occurrence.get("message_container_id_source")
            == "premium_whole_artifact_byte_bound_row_mapping"
            and payload.get("thread_channel_id_exact") is True
            and payload.get("thread_channel_id_conflict") is False
            and str(payload.get("inferred_thread_channel_id") or "") == child_id
            and validation.get("valid") is True
            and validation.get("thread_channel_id") == child_id
            and evidence.get("authenticated") is True
            and evidence.get("source_scope") == "discord_only"
            and evidence.get("outside_sources_used") is False
            and evidence.get("parent_forum_channel_id") == PREMIUM_PARENT_ID
            and evidence.get("thread_channel_id") == child_id
            and evidence.get("evidence_type")
            == (
                "forum_group_owned_reply_anchor_exact"
                if source == "forum_group_owned_reply_anchor_exact"
                else "forum_group_header_navigation_exact"
            )
        ):
            continue
        if child_id in output:
            continue
        output[child_id] = {
            "parent_container_id": PREMIUM_PARENT_ID,
            "relationship_source": "fresh_premium_v2_5_daily_canonical",
            "parent_child_binding_sha256": _sha256_bytes(
                _compact_json(
                    {
                        "guild_id": str(payload.get("guild_id") or ""),
                        "parent_container_id": PREMIUM_PARENT_ID,
                        "child_container_id": child_id,
                        "source_file_sha256": occurrence.get("source_file_sha256"),
                        "message_id": occurrence.get("message_id"),
                        "forum_group_navigation_evidence_key": payload.get(
                            "forum_group_navigation_evidence_key"
                        ),
                    }
                ).encode("utf-8")
            ),
            "source_file_sha256": occurrence.get("source_file_sha256"),
            "source_file_relative_path": occurrence.get(
                "source_file_relative_path"
            ),
            "message_id": occurrence.get("message_id"),
            "identity_method": source,
            "forum_group_navigation_evidence_key": payload.get(
                "forum_group_navigation_evidence_key"
            ),
        }
    return output


def audit_file_record(
    path: Path,
    *,
    provenance_root: Path,
    classification: dict[str, Any],
    payload: dict[str, Any] | None,
    artifact_role: str,
) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(provenance_root.resolve()).as_posix()
        path_status = "within_provenance_root"
    except ValueError:
        token = _sha256_bytes(str(resolved).encode("utf-8"))[:12]
        relative = f"external/{token}_{resolved.name}"
        path_status = "outside_provenance_root"
    raw_rows = payload.get("messages") if isinstance(payload, dict) else None
    rows = raw_rows if isinstance(raw_rows, list) else []
    message_ids = sorted(
        {
            str(row.get("message_id"))
            for row in rows
            if isinstance(row, dict) and SNOWFLAKE_RE.fullmatch(str(row.get("message_id") or ""))
        }
    )
    return {
        "relative_path": relative,
        "path_status": path_status,
        "artifact_role": artifact_role,
        "sha256": _sha256_file(resolved),
        "size_bytes": resolved.stat().st_size,
        "classification": classification.get("classification"),
        "reason": classification.get("reason"),
        "requested_container_id": classification.get("requested_container_id"),
        "requested_container_id_source": classification.get(
            "requested_container_id_source"
        ),
        "parent_container_id": classification.get("parent_container_id"),
        "declared_message_row_count": len(rows),
        "unique_valid_message_id_count": len(message_ids),
        "unique_valid_message_id_set_sha256": _sha256_bytes(
            _compact_json(message_ids).encode("utf-8")
        ),
    }


def summarize_selection_audit(
    *,
    scope: AuthorizedScope,
    included_segments: Sequence[dict[str, Any]],
    included_occurrences: Sequence[dict[str, Any]],
    excluded_files: Sequence[dict[str, Any]],
    proven_children: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    excluded = sorted(
        (copy.deepcopy(row) for row in excluded_files),
        key=lambda row: (str(row.get("relative_path")), str(row.get("sha256"))),
    )
    excluded_ids_by_file = [
        {
            "relative_path": row.get("relative_path"),
            "set_sha256": row.get("unique_valid_message_id_set_sha256"),
            "count": row.get("unique_valid_message_id_count"),
        }
        for row in excluded
    ]
    ambiguous = [row for row in excluded if row.get("classification") == "ambiguous_fail_closed"]
    reason_counts = Counter(str(row.get("reason") or "unknown") for row in excluded)
    included_source_ids = sorted(
        {
            str(row.get("source_file_id"))
            for row in included_segments
            if str(row.get("source_file_id") or "")
        }
    )
    return {
        **scope.as_dict(),
        "proven_child_relationship_count": len(proven_children),
        "proven_child_relationship_set_sha256": _sha256_bytes(
            _compact_json(
                [
                    [child_id, row.get("parent_container_id")]
                    for child_id, row in sorted(proven_children.items())
                ]
            ).encode("utf-8")
        ),
        "included": {
            "segment_file_count": len(included_segments),
            "segment_source_file_ids": included_source_ids,
            "occurrence_count": len(included_occurrences),
            "unique_valid_message_id_count": len(
                {
                    str(row.get("message_id"))
                    for row in included_occurrences
                    if SNOWFLAKE_RE.fullmatch(str(row.get("message_id") or ""))
                }
            ),
        },
        "excluded": {
            "file_count": len(excluded),
            "segment_file_count": sum(
                row.get("artifact_role") == "segment" for row in excluded
            ),
            "completion_evidence_sidecar_file_count": sum(
                row.get("artifact_role") == "completion_evidence_sidecar"
                for row in excluded
            ),
            "declared_message_row_count": sum(
                int(row.get("declared_message_row_count") or 0) for row in excluded
            ),
            "unique_message_ids_by_file_count": sum(
                int(row.get("unique_valid_message_id_count") or 0) for row in excluded
            ),
            "ambiguous_fail_closed_file_count": len(ambiguous),
            "reason_counts": dict(sorted(reason_counts.items())),
            "file_set_sha256": _sha256_bytes(
                _compact_json(
                    [
                        [row.get("relative_path"), row.get("sha256"), row.get("size_bytes")]
                        for row in excluded
                    ]
                ).encode("utf-8")
            ),
            "message_id_sets_sha256": _sha256_bytes(
                _compact_json(excluded_ids_by_file).encode("utf-8")
            ),
            "files": excluded,
        },
        "release_gate": {
            "gate": "authorized_collection_scope_enforced",
            "passed": not ambiguous,
            "ambiguous_fail_closed_file_count": len(ambiguous),
            "allowed_parent_count": len(scope.parent_ids),
            "allowed_parent_ids": sorted(scope.parent_ids),
        },
    }


def derive_scoped_inventory(
    inventory: dict[str, Any],
    scope: AuthorizedScope,
    proven_children: dict[str, dict[str, Any]],
    child_inventory_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive completeness for only the three parents and their proven children."""

    containers = inventory.get("containers")
    if not isinstance(containers, list):
        containers = []
    scoped_rows: list[dict[str, Any]] = []
    represented_parent_ids: set[str] = set()
    represented_child_ids: set[str] = set()
    for raw in containers:
        if not isinstance(raw, dict):
            continue
        container_id = _exact_id(raw.get("container_id"))
        if not container_id:
            continue
        if container_id in scope.parent_ids:
            scoped_rows.append(copy.deepcopy(raw))
            represented_parent_ids.add(container_id)
            continue
        relationship = proven_children.get(container_id)
        if not relationship:
            continue
        if _exact_id(raw.get("parent_container_id")) != relationship["parent_container_id"]:
            continue
        scoped_row = copy.deepcopy(raw)
        identity = scoped_row.get("identity_provenance")
        identity = identity if isinstance(identity, dict) else {}
        identity["verified_parent_child_binding"] = {
            "guild_id": scope.guild_id,
            "parent_container_id": relationship["parent_container_id"],
            "child_container_id": container_id,
            "forum_card_data_list_item_id": relationship.get(
                "forum_card_data_list_item_id"
            ),
            "binding_sha256": relationship.get("parent_child_binding_sha256"),
            "verification_method": (
                str(
                    relationship.get("identity_method")
                    or "forum_group_header_navigation_exact"
                )
                if relationship.get("relationship_source")
                == "fresh_premium_v2_5_daily_canonical"
                else "forum_card_data_list_item_id"
            ),
        }
        scoped_row["identity_provenance"] = identity
        scoped_rows.append(scoped_row)
        represented_child_ids.add(container_id)

    source_scoped_row_count = len(scoped_rows)

    for child_id, relationship in sorted(proven_children.items()):
        if child_id in represented_child_ids:
            continue
        if relationship.get("relationship_source") not in {
            "scoped_forum_thread_inventory_reconciliation",
            "fresh_premium_v2_5_daily_canonical",
        }:
            continue
        parent_id = str(relationship.get("parent_container_id") or "")
        scoped_rows.append(
            {
                "container_id": child_id,
                "name": "",
                "kind": "forum thread",
                "parent_container_id": parent_id,
                "category_id": None,
                "category_name": None,
                "inventory_layer": (
                    "fresh_v2_5_observed_exact_forum_thread"
                    if relationship.get("relationship_source")
                    == "fresh_premium_v2_5_daily_canonical"
                    else "reconciled_exact_forum_thread"
                ),
                "message_bearing": True,
                "accessible": True,
                "searchable": True,
                "accessible_scope_status": (
                    "observed_accessible_in_authenticated_parent_forum_search"
                ),
                "archived": None,
                "locked": None,
                "coverage_container_id": parent_id,
                "coverage_start_date": scope.start_date_inclusive,
                "coverage_end_date": scope.end_date_inclusive,
                "full_window_query": None,
                "full_window_reported_total": None,
                "count_status": "exact_addition_census_not_closed",
                "channel_created_at_utc": None,
                "notes": (
                    "Exact byte-bound current-group Discord evidence proves identity and "
                    "parentage; the scoped forum census remains unresolved."
                ),
                "identity_provenance": {
                    "method": str(
                        relationship.get("identity_method")
                        or "forum_group_header_navigation_exact"
                    ),
                    "source_file_ids": [],
                    "source_occurrence_ids": [],
                    "evidence_message_ids": [],
                    "reconciliation_source_sha256": relationship.get(
                        "reconciliation_source_sha256"
                    ),
                    "source_file_sha256": relationship.get("source_file_sha256"),
                    "source_file_relative_path": relationship.get(
                        "source_file_relative_path"
                    ),
                    "forum_group_navigation_evidence_key": relationship.get(
                        "forum_group_navigation_evidence_key"
                    ),
                },
                "accessible_scope_evidence": {
                    "parent_forum_container_id": parent_id,
                    "archive_enumeration_complete": False,
                    "census_closure_proven": False,
                },
            }
        )
        represented_child_ids.add(child_id)

    errors: list[str] = []
    missing_parents = sorted(scope.parent_ids - represented_parent_ids)
    if missing_parents:
        errors.append("authorized_parent_missing:" + ",".join(missing_parents))
    missing_children = sorted(set(proven_children) - represented_child_ids)
    if missing_children:
        errors.append("proven_child_missing_from_normalized_inventory:" + ",".join(missing_children))
    for row in scoped_rows:
        container_id = str(row.get("container_id") or "")
        if container_id in scope.parent_ids:
            if not row.get("message_bearing"):
                errors.append(f"authorized_parent_not_message_bearing:{container_id}")
            if not row.get("accessible") or not row.get("searchable"):
                errors.append(f"authorized_parent_not_accessible_searchable:{container_id}")
        else:
            relationship = proven_children.get(container_id)
            if not relationship or row.get("parent_container_id") != relationship.get(
                "parent_container_id"
            ):
                errors.append(f"child_parentage_not_proven:{container_id}")

    accessible_scope = inventory.get("accessible_scope")
    accessible_scope = accessible_scope if isinstance(accessible_scope, dict) else {}
    original_top = accessible_scope.get("top_level_containers")
    original_top = original_top if isinstance(original_top, dict) else {}
    original_forum = accessible_scope.get("forum_threads")
    original_forum = original_forum if isinstance(original_forum, dict) else {}
    original_ordinary = accessible_scope.get("ordinary_threads")
    original_ordinary = original_ordinary if isinstance(original_ordinary, dict) else {}
    original_resnapshot = accessible_scope.get("post_cutoff_navigation_resnapshot")
    original_resnapshot = original_resnapshot if isinstance(original_resnapshot, dict) else {}

    top_complete = bool(original_top.get("validated_complete"))
    # Premium child closure is never inherited from the old broad inventory.
    # It must be re-proven by the exact, byte-bound scoped reconciliation and
    # its message-scope closure gate.
    forum_complete = False
    if child_inventory_reconciliation is not None:
        message_scope_closure = child_inventory_reconciliation.get(
            "message_scope_closure"
        )
        message_scope_closure = (
            message_scope_closure
            if isinstance(message_scope_closure, dict)
            else {}
        )
        forum_complete = bool(
            message_scope_closure.get("passed") is True
        )
    ordinary_children = [
        row
        for row in scoped_rows
        if row.get("parent_container_id") in scope.parent_ids
        and "forum" not in str(row.get("kind") or "").casefold()
    ]
    ordinary_complete = bool(not ordinary_children or original_ordinary.get("validated_complete"))
    resnapshot_complete = bool(
        not original_resnapshot or original_resnapshot.get("validated_complete")
    )
    if not top_complete:
        errors.append("authorized_top_level_inventory_not_validated_complete")
    if not forum_complete:
        if child_inventory_reconciliation is None:
            errors.append("premium_journals_scoped_reconciliation_missing")
        elif not (
            child_inventory_reconciliation.get("message_scope_closure") or {}
        ).get("passed"):
            errors.append("premium_journals_scoped_reconciliation_closure_not_proven")
        else:
            errors.append("premium_journals_thread_inventory_not_validated_complete")
    if not ordinary_complete:
        errors.append("authorized_ordinary_child_inventory_not_validated_complete")
    if not resnapshot_complete:
        errors.append("authorized_post_cutoff_resnapshot_not_validated_complete")

    declared_complete = bool(inventory.get("declared_complete"))
    validated_complete = bool(declared_complete and not errors)
    scoped_rows.sort(key=lambda row: str(row.get("container_id") or ""))
    forum_rows = [
        row for row in scoped_rows if "forum thread" in str(row.get("kind") or "").casefold()
    ]
    return {
        "provided": bool(inventory.get("provided")),
        "source_file_id": inventory.get("source_file_id"),
        "source_file_relative_path": inventory.get("source_file_relative_path"),
        "source_file_size_bytes": inventory.get("source_file_size_bytes"),
        "source_file_sha256": inventory.get("source_file_sha256"),
        "declared_complete": declared_complete,
        "validated_complete": validated_complete,
        "guild_id": inventory.get("guild_id"),
        "captured_at_utc": inventory.get("captured_at_utc"),
        "containers": scoped_rows,
        "container_count": len(scoped_rows),
        "top_level_container_count": len(represented_parent_ids),
        "observed_forum_thread_count": len(forum_rows),
        "ordinary_thread_count": len(ordinary_children),
        "message_bearing_accessible_searchable_count": sum(
            bool(row.get("message_bearing") and row.get("accessible") and row.get("searchable"))
            for row in scoped_rows
        ),
        "accessible_scope": {
            "status": "complete" if validated_complete else "partial",
            "definition": "User-authorized three-parent derived inventory.",
            "authenticated_account_only": True,
            "source_scope": "discord_only",
            "top_level_containers": {
                "declared_complete": declared_complete,
                "validated_complete": top_complete and not missing_parents,
                "expected_count": len(scope.parent_ids),
                "represented_count": len(represented_parent_ids),
                "status": "complete" if top_complete and not missing_parents else "partial",
                "authorized_parent_ids": sorted(scope.parent_ids),
            },
            "forum_threads": {
                "parent_forum_count": 1,
                "parent_forum_container_ids": ["1283941772577472643"],
                "declared_complete": bool(original_forum.get("declared_complete")),
                "validated_complete": forum_complete and not missing_children,
                "status": "complete" if forum_complete and not missing_children else "partial",
                "observed_exact_id_count": len(forum_rows),
                "observed_exact_ids": sorted(
                    str(row.get("container_id")) for row in forum_rows
                ),
                "unresolved_observed_occurrence_count": original_forum.get(
                    "unresolved_observed_occurrence_count"
                ),
                "remaining_limitation": original_forum.get("remaining_limitation"),
            },
            "ordinary_threads": {
                "declared_complete": bool(original_ordinary.get("declared_complete"))
                if ordinary_children
                else True,
                "validated_complete": ordinary_complete,
                "status": "complete" if ordinary_complete else "partial",
                "observed_exact_id_count": len(ordinary_children),
            },
            "post_cutoff_navigation_resnapshot": {
                "declared_complete": bool(original_resnapshot.get("declared_complete"))
                if original_resnapshot
                else True,
                "validated_complete": resnapshot_complete,
                "status": "complete" if resnapshot_complete else "partial",
            },
        },
        "completeness": {
            "overall_declared_complete": declared_complete,
            "overall_validated_complete": validated_complete,
            "top_level_exact_container_inventory_complete": top_complete and not missing_parents,
            "forum_thread_enumeration_complete": forum_complete and not missing_children,
            "ordinary_thread_enumeration_complete": ordinary_complete,
            "post_cutoff_authenticated_navigation_resnapshot_complete": resnapshot_complete,
            "rule": (
                "Completeness is evaluated only for student-breakdowns, premium-journals, "
                "questions, and exact child threads whose allowed parentage is proven."
            ),
        },
        "provenance": copy.deepcopy(inventory.get("provenance")),
        "scope_derivation": {
            "authorized_scope_sha256": scope.source_sha256,
            "authorized_parent_ids": sorted(scope.parent_ids),
            "proven_child_count": len(proven_children),
            "proven_child_ids_sha256": _sha256_bytes(
                _compact_json(sorted(proven_children)).encode("utf-8")
            ),
            "out_of_scope_inventory_rows_excluded": (
                len(containers) - source_scoped_row_count
            ),
            "raw_inventory_bytes_duplicated": False,
            "child_inventory_reconciliation": copy.deepcopy(
                child_inventory_reconciliation
            ),
        },
        "validation_errors": sorted(set(errors)),
    }


def is_authorized_container_id(
    container_id: Any,
    scope: AuthorizedScope,
    proven_children: dict[str, dict[str, Any]],
) -> bool:
    exact = _exact_id(container_id)
    return bool(exact and (exact in scope.parent_ids or exact in proven_children))
