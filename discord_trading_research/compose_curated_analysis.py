from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


BASE_DIR = Path(__file__).resolve().parent
RB_PATH = BASE_DIR / "rb_analysis.json"
MODEL_PATH = BASE_DIR / "model_analysis.json"
TRADE_PATH = BASE_DIR / "trade_analysis.json"
OUTPUT_PATH = BASE_DIR / "curated_analysis.json"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def lines(values: Iterable[Any]) -> str | None:
    rendered: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
        elif isinstance(value, dict):
            text = str(
                value.get("rule")
                or value.get("instruction")
                or value.get("condition")
                or value.get("finding")
                or ""
            ).strip()
        else:
            text = str(value).strip()
        if text and text not in rendered:
            rendered.append(text)
    return "\n".join(rendered) or None


def confidence_number(value: Any) -> float:
    text = str(value or "").lower()
    if text.startswith("high") or "high_" in text:
        return 0.9
    if text.startswith("moderate_to_high"):
        return 0.8
    if text.startswith("moderate") or "moderate_" in text:
        return 0.7
    if text.startswith("low_to_moderate"):
        return 0.5
    if text.startswith("low"):
        return 0.4
    return 0.6


def dedupe_evidence(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        message_id = str(item.get("message_id") or "")
        role = str(item.get("role") or "supports")
        if not re.fullmatch(r"\d{15,22}", message_id):
            continue
        excerpt = str(item.get("excerpt") or "").strip()
        key = (message_id, role)
        if key not in merged:
            merged[key] = {"message_id": message_id, "role": role, "excerpt": excerpt}
            continue
        prior = merged[key]["excerpt"]
        if excerpt and excerpt not in prior:
            merged[key]["excerpt"] = f"{prior} | {excerpt}" if prior else excerpt
    return list(merged.values())


def rb_curated(rb: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    evidence_catalog = rb["evidence_catalog"]
    facet_map = {
        "identification": "identification",
        "invalidation_and_non_actionability": "invalidation",
        "timing": "timing",
        "high_probability_profile": "high_probability",
        "low_probability_profile": "low_probability",
        "instrument_profile": "instrument_comparison",
    }
    findings: list[dict[str, Any]] = []
    for group_name, group_items in rb["findings"].items():
        facet = facet_map[group_name]
        for item in group_items:
            evidence = []
            for ref in item.get("evidence_refs", []):
                source = evidence_catalog[ref]
                evidence.append(
                    {
                        "message_id": source["message_id"],
                        "role": "supports",
                        "excerpt": source.get("excerpt") or "",
                    }
                )
            if facet in {"timing", "instrument_comparison"}:
                evidence_status = "observed_association"
            elif facet in {"high_probability", "low_probability"}:
                evidence_status = "derived"
            else:
                evidence_status = "derived"
            findings.append(
                {
                    "facet": facet,
                    "finding": item["statement"],
                    "evidence_status": evidence_status,
                    "confidence": confidence_number(item.get("confidence")),
                    "instrument_scope": "NQ/MNQ and ES/MES" if facet == "instrument_comparison" else None,
                    "timeframe_scope": "As stated in the cited message; no universal timeframe rule" if facet in {"identification", "invalidation"} else None,
                    "session_scope": "Clock labels retained as written" if facet == "timing" else None,
                    "caveat": item.get("caveat"),
                    "evidence": dedupe_evidence(evidence),
                }
            )

    qa_special_question_ref = {
        "RB-QA-10": "E056",
        "RB-QA-17": "E041",
    }
    qa_rows: list[dict[str, Any]] = []
    for item in rb["qa_catalog"]:
        status_text = item["status"]
        if status_text.startswith("answered"):
            status = "answered"
        elif status_text.startswith("partially"):
            status = "partial"
        else:
            status = "unanswered"
        refs = item.get("evidence_refs", [])
        question_ref = qa_special_question_ref.get(item["qa_id"], refs[0] if refs else None)
        question_id = evidence_catalog[question_ref]["message_id"] if question_ref else None
        answer_id = None
        if status in {"answered", "partial"} and len(refs) > 1:
            answer_id = evidence_catalog[refs[-1]]["message_id"]
        qa_rows.append(
            {
                "question_message_id": question_id,
                "answer_message_id": answer_id,
                "normalized_question": item["question"],
                "answer_summary": item.get("answer") if status != "unanswered" else None,
                "status": status,
                "topic": "rejection_block",
                "confidence": 0.95 if status == "answered" else 0.75 if status == "partial" else 0.85,
                "notes": (
                    f"Curated review {item['qa_id']}; source status={status_text}. "
                    + (item.get("answer") or "No authoritative answer was captured.")
                ),
            }
        )

    contradictions: list[dict[str, Any]] = []
    for item in rb.get("contradictions_and_tensions", []):
        refs = item.get("evidence_refs", [])
        ids = [evidence_catalog[ref]["message_id"] for ref in refs if ref in evidence_catalog]
        unresolved = item["topic"] in {"liquidity_sweep_requirement", "nq_vs_es_signal"}
        contradictions.append(
            {
                "topic": item["topic"],
                "description": item["description"],
                "message_id_a": ids[0] if ids else None,
                "message_id_b": ids[1] if len(ids) > 1 else None,
                "resolution_status": "unresolved" if unresolved else "qualified",
                "notes": "All supporting and qualifying evidence remains in rb_analysis.json embedded in analysis_documents.",
            }
        )
    return findings, qa_rows, contradictions


def flatten_mapping(mapping: Any, ignored: set[str] | None = None) -> str | None:
    ignored = ignored or set()
    if isinstance(mapping, str):
        return mapping
    if isinstance(mapping, list):
        return lines(mapping)
    if not isinstance(mapping, dict):
        return None
    parts: list[str] = []
    for key, value in mapping.items():
        if key in ignored or key.endswith("_ids"):
            continue
        if isinstance(value, list):
            text = lines(value)
        elif isinstance(value, dict):
            text = flatten_mapping(value, ignored)
        else:
            text = str(value).strip() if value is not None else ""
        if text:
            parts.append(f"{key.replace('_', ' ')}: {text}")
    return "\n".join(parts) or None


def models_curated(model_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    timeframe_scope = {
        "M1_10AM_KEY_OPEN_RB": "HTF context; 15m/5m reaction; 1m/30s refinement",
        "M2_HTF_PDA_TO_LTF_RB": "Daily/4h/1h context; 15m/5m rejection; 1m/30s trigger",
        "M3_MMXM_LABS_STDV_BREAKER": "Daily/4h/1h context with 15m/5m/1m TFA",
        "M4_NY_OPEN_SWEEP_DISPLACEMENT_RETRACE": "HTF context; 15m/5m reaction; 1m/30s execution",
    }
    output: list[dict[str, Any]] = []
    for number, model in enumerate(model_analysis["models"], start=1):
        rules: list[dict[str, Any]] = []
        for item in model.get("eligibility_context", []):
            rules.append({"type": "context", "text": item["rule"], "required": True})
        for item in model.get("identification", []):
            rules.append({"type": "identification", "text": item["instruction"], "required": True})
        trigger = model.get("trigger") or {}
        for key in ("primary_documented", "named_answer", "alternate_documented"):
            if trigger.get(key):
                rules.append({"type": "entry", "text": trigger[key], "required": key == "primary_documented"})
        if trigger.get("lower_timeframe_caveat"):
            rules.append({"type": "no_trade", "text": trigger["lower_timeframe_caveat"], "required": True})
        for item in model.get("invalidation", []):
            rules.append({"type": "invalidation", "text": item["condition"], "required": True})
        execution = model.get("execution") or {}
        for text in execution.get("stop", []) or []:
            rules.append({"type": "risk", "text": text, "required": True})
        for text in execution.get("target", []) or []:
            rules.append({"type": "target", "text": text, "required": False})
        for text in execution.get("management", []) or []:
            rules.append({"type": "management", "text": text, "required": False})

        evidence_items: list[dict[str, Any]] = []
        for item in model.get("evidence", []):
            evidence_items.append(
                {
                    "message_id": item["message_id"],
                    "role": "supports",
                    "excerpt": item.get("exact_excerpt") or "",
                }
            )
        for item in model.get("failed_or_contradictory_evidence", []):
            evidence_items.append(
                {
                    "message_id": item["message_id"],
                    "role": "failed_example",
                    "excerpt": item.get("finding") or "",
                }
            )

        output.append(
            {
                "model_no": number,
                "name": model["name"],
                "evidence_status": "documented" if model["classification"] == "documented_recurring" else "provisional_derived",
                "thesis": model["why_materially_distinct"],
                "eligibility_context": lines(item["rule"] for item in model.get("eligibility_context", [])),
                "identification": lines(item["instruction"] for item in model.get("identification", [])),
                "trigger_confirmation": flatten_mapping(trigger, {"evidence_ids"}),
                "invalidation": lines(item["condition"] for item in model.get("invalidation", [])),
                "entry": lines(execution.get("entry", [])),
                "stop": lines(execution.get("stop", [])),
                "target": lines(execution.get("target", [])),
                "management": lines(execution.get("management", [])),
                "instrument_scope": flatten_mapping(model.get("instrument_scope")),
                "timeframe_scope": timeframe_scope[model["model_id"]],
                "session_scope": flatten_mapping(model.get("time_scope")),
                "win_count": None,
                "loss_count": None,
                "breakeven_count": None,
                "unknown_count": None,
                "limitations": (
                    f"Model confidence label in source analysis: {model.get('confidence')}. "
                    "No controlled model-level expectancy was calculated; review failed examples and full model_analysis.json."
                ),
                "rules": rules,
                "evidence": dedupe_evidence(evidence_items),
            }
        )
    return output


def canonical_confluence(tag: str) -> str | None:
    lower = tag.lower()
    base = lower.split(":", 1)[0]
    if base == "rejection_block":
        return "rejection_block"
    if base in {"fair_value_gap", "inverse_fair_value_gap"}:
        return "fvg_ifvg"
    if base == "key_open":
        return "10am_key_open" if "10am" in lower else "key_opens"
    if base in {"smt_divergence", "ssmt", "monthly_cycle_ssmt", "relative_strength"}:
        return "smt_ssmt"
    if base in {"liquidity_sweep", "liquidity", "judas_swing", "data_lows"}:
        return "liquidity_sweep"
    if base in {"ote_fibonacci", "range_midpoint"}:
        return "ote_fibonacci"
    if base in {"discount", "premium", "equilibrium"}:
        return "premium_discount"
    if base == "order_block":
        return "order_block"
    if base == "breaker":
        return "breaker"
    if base in {"cisd", "displacement", "break_of_structure", "entry_trigger"}:
        return "cisd_mss_displacement"
    if base == "engineered_liquidity":
        return "engineered_liquidity"
    if base == "standard_deviation":
        return "standard_deviation"
    if base in {"bias_alignment", "original_bias"}:
        return "higher_timeframe_bias"
    if base in {"draw_on_liquidity", "liquidity_low", "liquidity_high"}:
        return "draw_on_liquidity"
    if base in {"news_filter", "news"}:
        return "news_filter"
    if base in {"market_open", "market_open_930"}:
        return "market_open_930"
    return None


def episode_trade_rows(episode: dict[str, Any]) -> list[dict[str, Any]]:
    outcome_map = {
        "cancelled": "cancelled_no_trade",
        "mixed": "mixed_partial",
    }
    outcome = outcome_map.get(episode["outcome"], episode["outcome"])
    eligible_weight = int(episode.get("eligible_trade_instances_for_win_loss_confluence_comparison") or 0)
    comparison_eligible = episode["outcome"] in {"win", "loss"} and eligible_weight > 0
    copies = eligible_weight if eligible_weight > 1 else 1
    rows: list[dict[str, Any]] = []
    confluence_tags = list(episode.get("confluences") or [])
    evidence_rows = episode.get("evidence") or []
    first_evidence_id = evidence_rows[0]["message_id"] if evidence_rows else None
    mapped: dict[str, list[str]] = {}
    for tag in confluence_tags:
        canonical = canonical_confluence(tag)
        if canonical:
            mapped.setdefault(canonical, []).append(tag)
    rb_instances = (episode.get("rejection_block_use") or {}).get("instances") or []
    timeframe = ", ".join(sorted({item.get("timeframe", "unknown") for item in rb_instances})) or None
    entry_tags = [tag for tag in confluence_tags if any(word in tag.lower() for word in ("entry", "cisd", "breaker", "rejection_block"))]
    target_tags = [tag for tag in confluence_tags if "target" in tag.lower() or tag.split(":", 1)[0] == "draw_on_liquidity"]
    issues = list(episode.get("rules_violated_or_execution_issues") or [])
    explicit = str(episode.get("outcome_basis") or "").startswith("explicit")
    confidence = 0.95 if explicit else 0.8 if episode.get("linkage_strength") in {"explicit_single_message", "strong_sequence"} else 0.65
    for copy_no in range(1, copies + 1):
        trade_id = episode["episode_id"] if copies == 1 else f"{episode['episode_id']}-{copy_no}"
        notes_parts = [
            f"episode_kind={episode.get('episode_kind')}",
            f"execution_mode={episode.get('execution_mode')}",
            f"linkage_strength={episode.get('linkage_strength')}",
            f"eligible_for_strict_win_loss_confluence_comparison={int(comparison_eligible)}",
            f"original_confluences={'; '.join(confluence_tags) or 'none documented'}",
        ]
        if copies > 1:
            notes_parts.append(f"Expanded instance {copy_no} of {copies} from one shared-confluence episode record.")
        if episode.get("notes"):
            notes_parts.append(str(episode["notes"]))
        row = {
            "trade_id": trade_id,
            "trader": episode.get("author"),
            "trade_date": episode.get("trade_date_local"),
            "setup_time_text": episode.get("setup_time"),
            "post_time_utc": episode.get("primary_post_timestamp_utc"),
            "instrument": ", ".join(episode.get("instrument") or ["unknown"]),
            "direction": episode.get("direction") if episode.get("direction") in {"long", "short"} else "unknown",
            "setup_name": episode.get("episode_kind"),
            "timeframe": timeframe,
            "session_name": episode.get("session"),
            "outcome": outcome,
            "outcome_basis": episode.get("outcome_basis") or "not_stated",
            "outcome_confidence": confidence,
            "entry_text": "; ".join(entry_tags) or None,
            "invalidation_text": "; ".join(issues) or None,
            "stop_text": None,
            "target_text": "; ".join(target_tags) or None,
            "management_text": None,
            "notes": " | ".join(notes_parts),
            "evidence": [],
            "confluences": [],
        }
        evidence_role = "outcome" if outcome in {"win", "loss", "breakeven", "mixed_partial"} else "setup"
        row["evidence"] = dedupe_evidence(
            {
                "message_id": evidence["message_id"],
                "role": evidence_role,
                "excerpt": evidence.get("excerpt") or "",
            }
            for evidence in evidence_rows
        )
        # Preserve all original tags in notes/full embedded analysis. For executed
        # win/loss rows, populate the relational feature matrix only when the
        # episode passed the strict comparison policy; this prevents paper,
        # unresolved aggregate, or otherwise ineligible results from inflating
        # the win/loss confluence view.
        mapped_for_table = mapped if comparison_eligible or outcome not in {"win", "loss"} else {}
        for canonical, original_tags in mapped_for_table.items():
            row["confluences"].append(
                {
                    "name": canonical,
                    "state": "present",
                    "attribution": "explicit",
                    "evidence_message_id": first_evidence_id,
                    "notes": "Mapped from: " + "; ".join(sorted(set(original_tags))),
                }
            )
        rows.append(row)
    return rows


def profiles_curated(trade: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for outcome, source_key in (("win", "wins"), ("loss", "losses")):
        profile = trade["profiles"][source_key]
        author_rows = profile.get("author_concentration") or []
        author_note = "; ".join(
            f"{item['author']}={item['instances']} ({item['share']:.1%})" for item in author_rows[:5]
        )
        confluence_rows = []
        seen: set[str] = set()
        for item in profile.get("top_confluences", []):
            canonical = canonical_confluence(item["name"])
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            confluence_rows.append(
                {
                    "name": canonical,
                    "role": f"common_in_{outcome}_profile",
                    "observed_count": item["instances"],
                    "observed_share": item["share"],
                    "rationale": (
                        f"Explicitly documented in {item['instances']} of the strict {outcome} instances. "
                        "This is overlapping descriptive frequency, not causal attribution."
                    ),
                }
            )
        issue_text = lines(
            f"{item['issue']} ({item['instances']})" for item in profile.get("rules_or_execution_issues", [])[:10]
        )
        result.append(
            {
                "outcome": outcome,
                "summary": (
                    f"Strict attributable {outcome} profile contains {profile['eligible_trade_instances']} instances. "
                    f"Common labels are preserved below. Execution issues: {issue_text or 'none repeatedly documented'}."
                ),
                "resolved_trade_count": profile["eligible_trade_instances"],
                "unknown_trade_count": trade["episode_summary"]["records_excluded_from_win_loss_confluence_comparison"],
                "author_concentration": author_note,
                "limitations": (
                    "Selected self-reported journals; labels overlap, outcomes are not independently verified, "
                    "and most instruments are unknown. Counts do not estimate strategy expectancy."
                ),
                "confluences": confluence_rows,
            }
        )
    return result


def probability_tiers() -> list[dict[str, Any]]:
    common_limit = "Qualitative Discord-corpus synthesis, not a measured probability or backtest result."
    return [
        {
            "label": "A — stacked and confirmed",
            "rank_order": 1,
            "basis": "synthesis",
            "definition": "Clear HTF bias/unresolved draw; fresh meaningful PDA or liquidity rejection; correct location; sweep/manipulation; full timed-level interaction when required; closed RB/CISD/breaker/displacement trigger; structural stop; realistic remaining target.",
            "limitations": common_limit,
        },
        {
            "label": "B — context aligned",
            "rank_order": 2,
            "basis": "synthesis",
            "definition": "Meaningful fresh rejection and aligned narrative with a closed trigger, but one supportive layer such as SMT, nesting, OTE, or key-open overlap is absent or unstated.",
            "limitations": common_limit,
        },
        {
            "label": "C — technically plausible but weak",
            "rank_order": 3,
            "basis": "synthesis",
            "definition": "RB marking may be technically valid, but timeframe quality, freshness, draw, correlation, or confirmation is incomplete. Treat as low confidence or no trade until resolved.",
            "limitations": common_limit,
        },
        {
            "label": "D — non-actionable",
            "rank_order": 4,
            "basis": "synthesis",
            "definition": "Already mitigated or exhausted draw; bias conflict; incomplete 10AM tap/expired window; unclosed or front-run trigger; unresolved correlated liquidity; chop/news instability; or invalid stop geometry.",
            "limitations": common_limit,
        },
    ]


def research_questions(rb: dict[str, Any]) -> list[dict[str, Any]]:
    e = rb["evidence_catalog"]

    def ids(*refs: str) -> list[str]:
        return [e[ref]["message_id"] for ref in refs]

    return [
        {
            "question_text": "How do members identify a rejection block?",
            "answer_status": "partial",
            "answer_summary": "An RB rejects something meaningful—liquidity, a PDA, key level/open, or imbalance—not every wick. Close confirmation, freshness, higher-timeframe context, and a clean lower-timeframe trigger recur. The corpus does not provide one universal candle-color, sweep, or CE rule.",
            "limitations": "Several rules are trader-specific; volume imbalance and liquidity-sweep language create an unresolved universal-definition tension.",
            "evidence_message_ids": ids("E001", "E003", "E004", "E005", "E010", "E018"),
        },
        {
            "question_text": "How is a rejection block or rejection-block trade invalidated?",
            "answer_status": "partial",
            "answer_summary": "Operational invalidation includes an unclosed/front-run trigger, prior mitigation, completed draw, bias conflict, incomplete required tap, expired 10AM window, unresolved paired-market liquidity, or a stop beyond the structure. No universal price-close formula was captured.",
            "limitations": "The OTE-close-versus-RB question and universal wick/close rule remain unanswered.",
            "evidence_message_ids": ids("E015", "E017", "E018", "E019", "E021", "E022", "E023", "E036", "E055"),
        },
        {
            "question_text": "At what times do rejection blocks primarily appear?",
            "answer_status": "answered",
            "answer_summary": "Within 201 primary RB messages, 10AM is mentioned in 67 and 9:30/market open in 18; midnight appears in 8, 18:00 in 6, Asia in 6, and London in 4. The strict 10AM variant requires a full tap and has an 11AM cutoff.",
            "limitations": "These are message-mention counts, not unique setup counts; timezones are usually unstated.",
            "evidence_message_ids": ids("E013", "E014", "E015", "E016", "E017"),
        },
        {
            "question_text": "What makes a rejection-block trade high probability in this corpus?",
            "answer_status": "partial",
            "answer_summary": "The strongest profile combines unresolved HTF draw/bias, fresh meaningful PDA/liquidity rejection, correct premium/discount or key-open location, timeframe nesting, sweep/manipulation, supportive SMT, a closed RB/CISD/breaker/displacement trigger, structural risk, and a remaining realistic target.",
            "limitations": "High probability is qualitative. Self-reported examples are selected and do not form a controlled backtest.",
            "evidence_message_ids": ids("E025", "E028", "E029", "E030", "E031"),
        },
        {
            "question_text": "What characterizes the lowest-probability rejection-block trades?",
            "answer_status": "answered",
            "answer_summary": "Poor or isolated 1m RBs, no HTF context, bias conflict, mitigation/exhausted draw, missed confirmation, bad stop geometry, chop, news volatility, and unresolved NQ/ES liquidity recur in failed or rejected examples.",
            "limitations": "These are descriptive failure patterns and no-trade filters, not calibrated loss probabilities.",
            "evidence_message_ids": ids("E021", "E022", "E023", "E034", "E035", "E036"),
        },
        {
            "question_text": "Do rejection blocks work better on NQ than ES?",
            "answer_status": "insufficient_evidence",
            "answer_summary": "Both NQ and ES use RB logic, often with NQ execution and ES as SMT/PDA confirmation. The strict ledger has 120 of 128 eligible instances with unknown executed instrument and only four explicit ES plus four explicit MNQ instances, so no superiority claim is supportable.",
            "limitations": "The direct same-RB-on-ES question was not answered; occurrence counts are not performance denominators.",
            "evidence_message_ids": ids("E038", "E040", "E041", "E047"),
        },
        {
            "question_text": "How should SMT be used with rejection blocks?",
            "answer_status": "partial",
            "answer_summary": "SMT is a supportive filter; Domme said it is better if present. It does not override unresolved correlated liquidity, and stacked SMT/RB trades still failed when ES had an outstanding draw.",
            "limitations": "No universal SMT requirement or standardized sweep size was stated.",
            "evidence_message_ids": ids("E042", "E043", "E044", "E045", "E046", "E036"),
        },
        {
            "question_text": "Which evidence-distinct trading models are supported?",
            "answer_status": "answered",
            "answer_summary": "Four models survived the evidence threshold: 10AM key-open rejection; HTF-PDA to LTF-RB; MMXM/LABS with STDV and breaker entry; and NY-open/macro sweep with displacement and FVG retest. Two are documented recurring and two are provisional derived.",
            "limitations": "No fifth model was added because remaining variants overlapped or lacked independent operational rules.",
            "evidence_message_ids": [
                "1524280852115230750",
                "1524771525377392650",
                "1526449090408218754",
                "1524533692557426698",
                "1524058116264820747",
            ],
        },
    ]


def main() -> None:
    rb = load(RB_PATH)
    model_analysis = load(MODEL_PATH)
    trade = load(TRADE_PATH)
    rb_findings, qa_rows, contradictions = rb_curated(rb)
    trade_rows = [row for episode in trade["episodes"] for row in episode_trade_rows(episode)]
    result = {
        "schema_version": "1.0.0",
        "source_scope": "Discord only",
        "rejection_block_findings": rb_findings,
        "qa_pairs": qa_rows,
        "trades": trade_rows,
        "outcome_profiles": profiles_curated(trade),
        "probability_tiers": probability_tiers(),
        "models": models_curated(model_analysis),
        "research_questions": research_questions(rb),
        "contradictions": contradictions,
    }
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(OUTPUT_PATH),
                "rb_findings": len(result["rejection_block_findings"]),
                "qa_pairs": len(result["qa_pairs"]),
                "trade_rows": len(result["trades"]),
                "profiles": len(result["outcome_profiles"]),
                "probability_tiers": len(result["probability_tiers"]),
                "models": len(result["models"]),
                "research_questions": len(result["research_questions"]),
                "contradictions": len(result["contradictions"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
