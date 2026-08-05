"""Versioned, disabled route builder for the future-only v2.7 pilot."""
from __future__ import annotations
from typing import Any
import premium_journals_v2_7_schedule as v27

def build_disabled_v2_7_extension(day: str = "2026-01-08") -> dict[str, Any]:
    return {"premium_journals_v2_7_routes": [v27.build_disabled_route(day)]}
