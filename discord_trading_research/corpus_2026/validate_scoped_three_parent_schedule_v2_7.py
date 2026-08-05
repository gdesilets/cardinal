"""Versioned validator extension; it leaves the v2.6 schedule grammar intact."""
from __future__ import annotations
from typing import Any
import premium_journals_v2_7_schedule as v27

def validate_explicit_v2_7_routes(schedule_data: dict[str, Any]) -> list[str]:
    return v27.validate_explicit_routes(schedule_data)
