from __future__ import annotations

import pathlib
import sys


CORPUS_ROOT = pathlib.Path(__file__).resolve().parents[2]
ARTIFACT = CORPUS_ROOT / (
    "raw/quarantine_collection_errors/"
    "terra_premium_journals_daily_2026-01-05_20260722T023110Z/"
    "v2_6_revalidated/"
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


try:
    premium.audit_premium_canonical(ARTIFACT, ROUTE, artifact_root=CORPUS_ROOT)
except premium.PremiumJournalsContractError as exc:
    text = str(exc)
    expected_marker = "canonical_path_mismatch:"
    if expected_marker in text and ";" not in text.rsplit(": ", 1)[-1]:
        print("EXPECTED_STAGING_LOCATION_FAILURE_ONLY")
        print(text)
        raise SystemExit(0)
    print("UNEXPECTED_CONTRACT_FAILURE")
    print(text)
    raise SystemExit(1)
else:
    print("UNEXPECTED_DIRECT_STAGING_PASS")
    raise SystemExit(1)
