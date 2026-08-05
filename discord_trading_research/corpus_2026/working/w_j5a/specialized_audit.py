from __future__ import annotations

import json
import pathlib
import sys


CORPUS_ROOT = pathlib.Path(__file__).resolve().parents[2]
MIRROR_ROOT = pathlib.Path(__file__).resolve().parent / "mirror"
ARTIFACT = MIRROR_ROOT / (
    "raw/channel_segments_v2_5/"
    "channel_premium_journals_1283941772577472643_"
    "2026-01-05_2026-01-05.json"
)
ROUTE = {
    "start": "2026-01-05",
    "end": "2026-01-05",
    "query": "in:premium-journals after:2026-01-04 before:2026-01-06",
    "expected_canonical_path": (
        "raw/channel_segments_v2_5/"
        "channel_premium_journals_1283941772577472643_"
        "2026-01-05_2026-01-05.json"
    ),
}

sys.path.insert(0, str(CORPUS_ROOT))
import premium_journals_provenance_contract as premium  # noqa: E402


audit = premium.audit_premium_canonical(
    ARTIFACT,
    ROUTE,
    artifact_root=MIRROR_ROOT,
)
accepted = audit["accepted_artifact"]
result = {
    "status": "PASS",
    "terminal_valid": audit["terminal_valid"],
    "unresolved_count": audit["unresolved_count"],
    "conflict_count": audit["conflict_count"],
    "accepted_artifact": {
        key: accepted[key]
        for key in [
            "path",
            "sha256",
            "bytes",
            "collector_version",
            "reported_total",
            "captured_rows",
            "reported_pages",
            "completion_terminal_state",
            "message_id_set_sha256",
            "observed_child_thread_count",
            "observed_child_thread_id_set_sha256",
            "forum_group_count",
            "forum_navigation_evidence_map_sha256",
            "forum_navigation_unresolved_count",
            "thread_channel_id_conflict_count",
            "forbidden_selected_thread_source_count",
            "full_qa_passed",
            "source_file_set_sha256",
        ]
    },
    "forum_membership_integrity": accepted["forum_membership_integrity"],
    "forum_navigation_artifact_integrity": accepted[
        "forum_navigation_artifact_integrity"
    ],
    "timestamp_scope_integrity": accepted["timestamp_scope_integrity"],
    "reply_provenance_integrity": accepted["reply_provenance_integrity"],
    "attachment_provenance_integrity": accepted[
        "attachment_provenance_integrity"
    ],
    "source_file_count": len(accepted["source_files"]),
    "message_id_count": len(audit["message_ids"]),
    "child_thread_id_count": len(audit["child_thread_ids"]),
    "row_child_binding_count": len(audit["row_child_container_ids"]),
    "owned_attachment_id_count": len(audit["owned_attachment_owners"]),
}
print(json.dumps(result, indent=2, ensure_ascii=False))
