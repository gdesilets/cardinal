#!/usr/bin/env python3
from __future__ import annotations

"""Build an auditable, Discord-only three-month trading-model analysis.

The builder consumes the merged Discord export plus the conservative trade and
rejection-block analyses.  Candidate models are promoted only when their own
local evidence clears support/diversity thresholds.  Outcome associations are
descriptive of the selected, self-reported corpus and are never presented as
market expectancy or backtest results.
"""

import argparse
import copy
import datetime as dt
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RAW = BASE_DIR / "raw_discord_export_3month.json"
DEFAULT_TRADE = BASE_DIR / "trade_analysis_3month.json"
DEFAULT_RB = BASE_DIR / "rb_analysis_3month.json"
DEFAULT_OUTPUT = BASE_DIR / "model_analysis_3month.json"
PROTECTED_OUTPUTS = {
    (BASE_DIR / "model_analysis.json").resolve(),
    (BASE_DIR / "raw_discord_export.json").resolve(),
}
SCHEMA_VERSION = "3.0.0-discord-evidence-models"
MAX_MODELS = 5


class AnalysisError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuleSpec:
    text: str
    patterns: tuple[str, ...]
    required: bool = True
    scope: str = "corpus_operational_rule"


@dataclass(frozen=True)
class CandidateSpec:
    model_id: str
    name: str
    material_distinction: str
    matcher: Callable[[dict[str, Any]], bool]
    context_patterns: tuple[str, ...]
    inclusion: tuple[RuleSpec, ...]
    exclusion: tuple[RuleSpec, ...]
    required_confluences: tuple[str, ...]
    supportive_confluences: tuple[str, ...]
    insufficient_alone: tuple[str, ...]
    price_invalidation: str
    narrative_invalidation: str
    entry_execution: tuple[RuleSpec, ...]
    risk_management: tuple[RuleSpec, ...]
    target_management: tuple[RuleSpec, ...]


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AnalysisError(f"Could not parse {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AnalysisError(f"Expected a JSON object in {path}")
    return value


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def unique(values: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    output: list[Any] = []
    for value in values:
        marker = compact(value)
        if marker not in seen:
            seen.add(marker)
            output.append(value)
    return output


def message_text(row: dict[str, Any]) -> str:
    return str(row.get("content_text") or row.get("visible_text") or "").strip()


def build_message_index(raw: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    index: dict[str, dict[str, Any]] = {}
    array_counts: dict[str, int] = {}
    for name, value in raw.items():
        if not isinstance(value, list):
            continue
        count = 0
        for row in value:
            if not isinstance(row, dict) or not row.get("message_id"):
                continue
            count += 1
            message_id = str(row["message_id"])
            prior = index.get(message_id)
            if prior is None or len(message_text(row)) > len(message_text(prior)):
                index[message_id] = copy.deepcopy(row)
        array_counts[name] = count
    return index, array_counts


def base_tags(episode: dict[str, Any]) -> set[str]:
    return {
        str(tag).split(":", 1)[0].lower()
        for tag in episode.get("confluences", []) or []
        if str(tag).strip()
    }


def full_tags(episode: dict[str, Any]) -> list[str]:
    return [str(tag).lower() for tag in episode.get("confluences", []) or []]


def has_base(episode: dict[str, Any], *names: str) -> bool:
    return bool(base_tags(episode).intersection(name.lower() for name in names))


def has_tag_prefix(episode: dict[str, Any], prefix: str) -> bool:
    needle = prefix.lower()
    return any(tag == needle or tag.startswith(needle + ":") for tag in full_tags(episode))


def rb_timeframes(episode: dict[str, Any]) -> set[str]:
    rb = episode.get("rejection_block_use") or {}
    return {
        str(item.get("timeframe") or "unspecified").lower()
        for item in rb.get("instances", []) or []
        if isinstance(item, dict)
    }


def is_10am_rb(episode: dict[str, Any]) -> bool:
    return has_tag_prefix(episode, "key_open:10am") and has_base(episode, "rejection_block")


def is_htf_pda_ltf_rb(episode: dict[str, Any]) -> bool:
    if not has_base(episode, "rejection_block"):
        return False
    has_pda = has_base(episode, "fair_value_gap", "inverse_fair_value_gap", "order_block")
    timeframes = rb_timeframes(episode)
    multi_tf = len({item for item in timeframes if item != "unspecified"}) >= 2
    has_entry_rb = any(tag.startswith("rejection_block:") and tag.endswith(":entry") for tag in full_tags(episode))
    return has_pda and (multi_tf or has_entry_rb)


def is_mmxm_stdv_breaker(episode: dict[str, Any]) -> bool:
    tags = base_tags(episode)
    return (
        "market_maker_model" in tags
        and "standard_deviation" in tags
        and bool(tags.intersection({"breaker", "order_block"}))
    )


def is_sweep_displacement_retrace(episode: dict[str, Any]) -> bool:
    tags = base_tags(episode)
    retrace = bool(tags.intersection({"fair_value_gap", "inverse_fair_value_gap"}))
    confirmation = bool(tags.intersection({"displacement", "break_of_structure", "cisd"}))
    return "liquidity_sweep" in tags and retrace and confirmation


def rule(text: str, *patterns: str, required: bool = True, scope: str = "corpus_operational_rule") -> RuleSpec:
    return RuleSpec(text=text, patterns=tuple(patterns), required=required, scope=scope)


def candidate_specs() -> tuple[CandidateSpec, ...]:
    return (
        CandidateSpec(
            model_id="M1_10AM_KEY_OPEN_RB",
            name="10AM key-open rejection model",
            material_distinction=(
                "A time-boxed key-open setup: eligibility depends on interaction with the stated 10AM level, "
                "so later generic rejection-block trades are not the same model."
            ),
            matcher=is_10am_rb,
            context_patterns=(r"\b10\s*(?::?00)?\s*(?:a\.?m\.?)?\b", r"\b(?:rb|rejection block)\b"),
            inclusion=(
                rule("Establish a directional bias or unresolved draw before treating the 10AM line as actionable.", r"\b(?:bias|draw|dol)\b"),
                rule("For the strict variant, price must interact with or fully tap the stated 10AM key open.", r"\b(?:tap|tapped|touch|reached)[^\n.]{0,55}\b10\s*(?::?00)?\b", r"\b10\s*(?::?00)?\b[^\n.]{0,55}\b(?:tap|tapped|touch)\b"),
                rule("Require overlapping location/context such as an RB, FVG, order block, or OTE/fibonacci area.", r"\b(?:fvg|ifvg|order block|\bob\b|ote|fib|rejection block|\brb\b)\b"),
                rule("When the direct-limit variant is not explicitly used, wait for the stated RB/CISD trigger to close.", r"\b(?:rb|cisd)[^\n.]{0,60}\bclos(?:e|ed)\b", r"\bwait[^\n.]{0,45}\b(?:close|closed)\b"),
            ),
            exclusion=(
                rule("Do not classify a nearby block as the strict setup when the full 10AM tap has not occurred.", r"\b(?:didn.?t|did not|no|without)[^\n.]{0,45}\btap[^\n.]{0,35}\b10\s*(?::?00)?\b"),
                rule("Stop considering new 10AM-model entries at the corpus-stated 11AM cutoff.", r"\b(?:stop|cutoff|done)[^\n.]{0,45}\b11\s*(?::?00)?\s*(?:a\.?m\.?)?\b"),
                rule("Exclude entries described as chop, unsupported by higher-timeframe context, or contrary to bias.", r"\b(?:no (?:high|higher) time frame bias|no htf|chop|against (?:my )?bias|opposite (?:my )?bias)\b"),
            ),
            required_confluences=("10AM key open", "meaningful PDA/rejection location", "bias or unresolved draw"),
            supportive_confluences=("liquidity manipulation/sweep", "SMT/SSMT", "OTE/fibonacci", "closed 5m/1m/30s trigger"),
            insufficient_alone=("the 10AM line", "an isolated 1m wick", "a still-forming trigger"),
            price_invalidation="The selected-corpus rule places the stop beyond the structure/wick that makes the reversal wrong; no universal point distance is supplied.",
            narrative_invalidation="No full tap in the strict variant, the intended draw already delivered, bias conflict, or the stated time cutoff has passed.",
            entry_execution=(
                rule("Use the documented direct-key-open limit variant only when its full context is present.", r"\b(?:limit|limit order)[^\n.]{0,80}\b10\s*(?::?00)?\b", required=False),
                rule("Otherwise enter only after the documented closed RB/CISD confirmation.", r"\b(?:closed?\s+(?:5m|1m|30s)?\s*(?:rb|cisd)|(?:rb|cisd)[^\n.]{0,40}\bclosed)\b"),
            ),
            risk_management=(
                rule("Place risk beyond the actual rejecting structure rather than at an arbitrary point inside a large wick.", r"\b(?:stop|sl)[^\n.]{0,65}\b(?:wick|structure|invalid)\b", r"\b(?:wick|structure)[^\n.]{0,65}\b(?:stop|sl)\b"),
                rule("Treat lower-timeframe refinement as optional because tight entries were also described as edge-out risk.", r"\b(?:tight|tighter)[^\n.]{0,45}\b(?:stop|risk|1m|30s)\b", required=False),
            ),
            target_management=(
                rule("Target the stated open draw, swing, or opposing liquidity rather than assuming one fixed R multiple.", r"\b(?:target|tp|draw|dol|liquidity|swing high|swing low)\b", required=False),
                rule("Management/scaling is trader-specific; preserve explicit partial or breakeven rules when stated.", r"\b(?:partial|break\s*even|breakeven|runner|trail)\b", required=False),
            ),
        ),
        CandidateSpec(
            model_id="M2_HTF_PDA_TO_LTF_RB",
            name="Higher-timeframe PDA into lower-timeframe rejection entry",
            material_distinction=(
                "A location-to-trigger sequence that is not tied to one clock time: higher-timeframe PDA/bias "
                "provides context and a lower-timeframe RB/CISD supplies execution."
            ),
            matcher=is_htf_pda_ltf_rb,
            context_patterns=(r"\b(?:htf|higher time frame|4h|1h|15m)\b", r"\b(?:rb|rejection block|fvg|order block)\b"),
            inclusion=(
                rule("Start from an explicit higher-timeframe bias, draw, or PDA rather than an isolated lower-timeframe signal.", r"\b(?:htf|higher time frame|4h|1h|daily)[^\n.]{0,70}\b(?:bias|draw|fvg|rb|order block|ob)\b"),
                rule("Require price to reject a meaningful PDA/liquidity location; a random wick is not enough.", r"\b(?:reject|rejection|sweep|liquidity|fvg|order block|ote)\b"),
                rule("Use a stated lower-timeframe RB/CISD or displacement event as the execution trigger.", r"\b(?:30s|1m|2m|3m|5m)[^\n.]{0,45}\b(?:rb|rejection block|cisd|displacement)\b"),
                rule("Wait for the trigger close when the source setup defines close confirmation.", r"\b(?:wait|closed?|close)[^\n.]{0,55}\b(?:rb|cisd|trigger)\b", r"\b(?:rb|cisd|trigger)[^\n.]{0,55}\bclos(?:e|ed)\b"),
            ),
            exclusion=(
                rule("Exclude already-mitigated rejection blocks for a fresh entry.", r"\b(?:already )?mitigated[^\n.]{0,55}\b(?:rb|rejection block)\b", r"\b(?:rb|rejection block)[^\n.]{0,55}\bmitigated\b"),
                rule("Exclude the trade when the intended draw has already delivered even if the pattern remains technically valid.", r"\b(?:draw|daily (?:high|low)|target)[^\n.]{0,70}\b(?:delivered|already hit|taken)\b"),
                rule("Exclude poor/random lower-timeframe blocks, missing confirmation, and bias-conflict entries.", r"\b(?:poor|random|bad)[^\n.]{0,40}\b(?:1m|rb|rejection block|wick)\b", r"\b(?:missing|didn.?t wait|no)[^\n.]{0,45}\bconfirmation\b", r"\bagainst (?:my )?bias\b"),
            ),
            required_confluences=("higher-timeframe bias/PDA", "meaningful rejection", "lower-timeframe execution trigger"),
            supportive_confluences=("liquidity sweep", "premium/discount or OTE", "SMT/SSMT", "nested FVG/order block"),
            insufficient_alone=("one lower-timeframe RB", "SMT without a location", "a mitigated block"),
            price_invalidation="Operational invalidation is beyond the rejecting wick/structure or the trigger structure; the corpus does not establish one universal close-through rule.",
            narrative_invalidation="Bias conflict, delivered draw, mitigated location, missing required tap, or unresolved opposing liquidity.",
            entry_execution=(
                rule("Execute at the documented start/CE only when that variant is explicit; otherwise use the stated 30s/1m/5m trigger.", r"\b(?:ce|consequent encroachment|start of (?:the )?(?:rb|block)|30s|1m|5m)\b", required=False),
                rule("Do not front-run a trigger that the source requires to close.", r"\b(?:front\s*run|forming|not closed|wait for (?:it|the .{0,20}) to close)\b"),
            ),
            risk_management=(
                rule("Keep the stop outside the structure that defines the setup; reject arbitrary inside-wick risk.", r"\b(?:stop|sl)[^\n.]{0,70}\b(?:wick|structure|meaningless|arbitrary)\b"),
            ),
            target_management=(
                rule("Use the unresolved draw or opposing liquidity named in the setup as the target.", r"\b(?:target|draw|dol|opposing liquidity|pdh|pdl|bsl|ssl)\b", required=False),
            ),
        ),
        CandidateSpec(
            model_id="M3_MMXM_STDV_BREAKER",
            name="Market-maker model with standard-deviation zone and breaker entry",
            material_distinction=(
                "A narrative-and-location model: an MMXM/MMBM/MMSM sequence and standard-deviation area "
                "define context, while a breaker/order-block structure defines execution."
            ),
            matcher=is_mmxm_stdv_breaker,
            context_patterns=(r"\b(?:mmxm|mmbm|mmsm|market maker)\b", r"\b(?:stdv|standard deviation|breaker)\b"),
            inclusion=(
                rule("Require an explicit market-maker model narrative rather than labeling any reversal MMXM.", r"\b(?:mmxm|mmbm|mmsm|market maker (?:buy|sell)? model)\b"),
                rule("Require an explicitly stated standard-deviation reaction/location.", r"\b(?:stdv|standard deviation)\b"),
                rule("Use the stated breaker or order-block structure for entry confirmation.", r"\b(?:breaker|order block|\bob\b)[^\n.]{0,55}\b(?:entry|enter|entered|trigger|confirm)\b", r"\b(?:entry|entered|trigger)[^\n.]{0,55}\b(?:breaker|order block|\bob\b)\b"),
            ),
            exclusion=(
                rule("Do not promote a standalone breaker without the model narrative and stated location.", r"\bbreaker\b", required=True),
                rule("Exclude trades described as forced, against bias, or inside chop.", r"\b(?:forced|against (?:my )?bias|chop)\b"),
            ),
            required_confluences=("MMXM/MMBM/MMSM narrative", "standard-deviation location", "breaker/order-block entry structure"),
            supportive_confluences=("liquidity sweep", "inducement", "SMT/SSMT", "FVG"),
            insufficient_alone=("a breaker", "a standard-deviation label", "a market-maker label without execution"),
            price_invalidation="The breaker/order-block structure used for entry must remain intact under the source's stated stop; no universal tick distance is inferred.",
            narrative_invalidation="The expected market-maker sequence fails, location is absent, or the entry is contrary to the stated bias/draw.",
            entry_execution=(
                rule("Enter from the documented breaker/order-block trigger after the model reaches its stated reversal zone.", r"\b(?:breaker|order block|\bob\b)[^\n.]{0,80}\b(?:entry|entered|limit|trigger)\b"),
            ),
            risk_management=(
                rule("Place the stop beyond the entry structure or explicitly stated invalidation point.", r"\b(?:stop|sl|invalidation)[^\n.]{0,65}\b(?:breaker|order block|structure|high|low)\b", required=False),
            ),
            target_management=(
                rule("Target the liquidity/draw specified by the model; no fixed corpus-wide multiple is assumed.", r"\b(?:target|draw|dol|liquidity)\b", required=False),
            ),
        ),
        CandidateSpec(
            model_id="M4_SWEEP_DISPLACEMENT_RETRACE",
            name="Liquidity sweep, displacement, and FVG/IFVG retrace",
            material_distinction=(
                "An event-sequence model rather than an RB-at-level model: liquidity is swept, structural "
                "displacement confirms the turn, and the imbalance retrace provides execution."
            ),
            matcher=is_sweep_displacement_retrace,
            context_patterns=(r"\b(?:sweep|swept|liquidity)\b", r"\b(?:displacement|bos|cisd|fvg|ifvg)\b"),
            inclusion=(
                rule("Require an explicit liquidity sweep or run on a named high/low/liquidity pool.", r"\b(?:swept|sweep|ran)[^\n.]{0,60}\b(?:liquidity|high|low|bsl|ssl|pdh|pdl)\b"),
                rule("Require displacement, BOS, or CISD after the sweep; the sweep alone is not the trigger.", r"\b(?:displacement|bos|break of structure|cisd)\b"),
                rule("Use the documented FVG/IFVG retrace or retest as entry location.", r"\b(?:retrace|retest|tap|entry|entered)[^\n.]{0,55}\b(?:fvg|ifvg|gap)\b", r"\b(?:fvg|ifvg|gap)[^\n.]{0,55}\b(?:retrace|retest|tap|entry|entered)\b"),
            ),
            exclusion=(
                rule("Exclude entries taken before the required displacement/structure confirmation closes.", r"\b(?:before|didn.?t wait|without|no)[^\n.]{0,55}\b(?:displacement|bos|cisd|confirmation|close)\b"),
                rule("Exclude setups with unresolved opposing-index liquidity when the source treats it as a blocker.", r"\b(?:es|nq)[^\n.]{0,80}\b(?:still|not|unswept|unresolved)[^\n.]{0,45}\b(?:liquidity|high|low)\b"),
                rule("Exclude premature lower-timeframe entries described as front-running the planned retrace.", r"\b(?:premature|front\s*run|too early|didn.?t wait)\b"),
            ),
            required_confluences=("liquidity sweep", "displacement/BOS/CISD", "FVG/IFVG retrace"),
            supportive_confluences=("SMT/SSMT", "NY open or macro time", "higher-timeframe draw", "RB at the retrace"),
            insufficient_alone=("a sweep", "an FVG without displacement", "a lower-timeframe signal before the retrace"),
            price_invalidation="The stop is outside the sweep/reversal structure or the explicitly stated entry structure; no universal distance is inferred.",
            narrative_invalidation="No displacement/structure confirmation, unresolved opposing liquidity, or failure to retrace the planned location under the stated rules.",
            entry_execution=(
                rule("Enter on the documented FVG/IFVG retest after structural confirmation, not merely on the sweep.", r"\b(?:fvg|ifvg)[^\n.]{0,65}\b(?:retest|retrace|entry|entered|tap)\b"),
            ),
            risk_management=(
                rule("Keep risk beyond the sweep or confirmed entry structure and avoid arbitrary ultra-tight placement.", r"\b(?:stop|sl|risk)[^\n.]{0,70}\b(?:sweep|structure|high|low|tight)\b", required=False),
            ),
            target_management=(
                rule("Use the next named opposing liquidity/draw or explicit R target from the source episode.", r"\b(?:target|tp|draw|dol|liquidity|\d+(?:\.\d+)?\s*r)\b", required=False),
            ),
        ),
    )


def flatten_claim_text(value: dict[str, Any]) -> str:
    preferred = (
        "statement", "finding", "claim", "answer", "answer_summary", "summary",
        "description", "question", "normalized_question", "rule", "text",
    )
    parts = [str(value[key]) for key in preferred if isinstance(value.get(key), str) and value.get(key)]
    return " ".join(parts)


def collect_rb_claims(rb: dict[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            ids = value.get("evidence_message_ids") or value.get("evidence_ids")
            if isinstance(ids, list) and ids:
                claims.append(
                    {
                        "path": path,
                        "text": flatten_claim_text(value),
                        "evidence_message_ids": [str(item) for item in ids if str(item)],
                        "evidence_type": value.get("evidence_type") or value.get("evidence_status"),
                    }
                )
            for key, child in value.items():
                visit(child, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    answers = rb.get("answers") if "answers" in rb else rb.get("findings", rb)
    visit(answers, "answers")
    visit(rb.get("related_qa", []), "related_qa")
    visit(rb.get("contradictions_and_tensions", []), "contradictions_and_tensions")
    return unique(claims)


def episode_evidence_ids(episode: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in episode.get("evidence", []) or []:
        if isinstance(item, dict) and item.get("message_id"):
            values.append(str(item["message_id"]))
    for item in episode.get("source_candidate_message_ids", []) or []:
        if item:
            values.append(str(item))
    return list(dict.fromkeys(values))


def claim_matches_spec(claim: dict[str, Any], spec: CandidateSpec) -> bool:
    text = claim.get("text") or ""
    return any(re.search(pattern, text, re.I) for pattern in spec.context_patterns)


def raw_context_matches(text: str, spec: CandidateSpec) -> bool:
    return all(re.search(pattern, text, re.I) for pattern in spec.context_patterns)


def select_candidate_pool(
    spec: CandidateSpec,
    episodes: list[dict[str, Any]],
    rb_claims: list[dict[str, Any]],
    messages: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    matched = [episode for episode in episodes if spec.matcher(episode)]
    relevant_claims = [claim for claim in rb_claims if claim_matches_spec(claim, spec)]
    ids: list[str] = []
    for episode in matched:
        ids.extend(episode_evidence_ids(episode))
    for claim in relevant_claims:
        ids.extend(claim["evidence_message_ids"])
    # Add recurring descriptive posts that contain the candidate's core context.
    contextual = [message_id for message_id, row in messages.items() if raw_context_matches(message_text(row), spec)]
    ids.extend(contextual[:60])
    return matched, relevant_claims, list(dict.fromkeys(message_id for message_id in ids if message_id in messages))


def evidence_for_rule(
    spec: CandidateSpec,
    rule_spec: RuleSpec,
    pool_ids: list[str],
    messages: dict[str, dict[str, Any]],
    limit: int = 6,
) -> list[str]:
    matched: list[str] = []
    for message_id in pool_ids:
        text = message_text(messages[message_id])
        if any(re.search(pattern, text, re.I) for pattern in rule_spec.patterns):
            matched.append(message_id)
            if len(matched) >= limit:
                return matched
    # Conservative fallback: still require both the rule phrase and candidate context.
    for message_id, row in messages.items():
        if message_id in matched:
            continue
        text = message_text(row)
        if not raw_context_matches(text, spec):
            continue
        if any(re.search(pattern, text, re.I) for pattern in rule_spec.patterns):
            matched.append(message_id)
            if len(matched) >= limit:
                break
    return matched


def build_rule_records(
    spec: CandidateSpec,
    rules: Sequence[RuleSpec],
    pool_ids: list[str],
    messages: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, item in enumerate(rules, start=1):
        evidence_ids = evidence_for_rule(spec, item, pool_ids, messages)
        output.append(
            {
                "rule_order": index,
                "rule": item.text,
                "required": item.required,
                "scope": item.scope,
                "evidence_status": "supported_in_corpus" if evidence_ids else "not_directly_resolved",
                "evidence_message_ids": evidence_ids,
            }
        )
    return output


def comparable_outcomes(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    wins = losses = 0
    excluded = Counter()
    records = Counter(str(episode.get("outcome") or "unknown") for episode in episodes)
    for episode in episodes:
        weight = int(episode.get("eligible_trade_instances_for_win_loss_confluence_comparison") or 0)
        outcome = episode.get("outcome")
        if outcome == "win" and weight:
            wins += weight
        elif outcome == "loss" and weight:
            losses += weight
        else:
            excluded[str(outcome or "unknown")] += 1
    resolved = wins + losses
    return {
        "comparable_wins": wins,
        "comparable_losses": losses,
        "comparable_resolved_instances": resolved,
        "observed_win_share": round(wins / resolved, 4) if resolved else None,
        "matched_episode_records_by_outcome": dict(records),
        "noncomparable_records_by_outcome": dict(excluded),
        "basis": (
            "Only trade-analysis episodes already marked eligible for actual/unspecified, single-instance, "
            "explicit win/loss confluence comparison. Models can overlap, so counts are not additive."
        ),
    }


def author_profile(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(episode.get("author") or "unknown") for episode in episodes)
    total = sum(counts.values())
    top = counts.most_common(10)
    return {
        "distinct_authors": len(counts),
        "top_authors": [
            {"author": author, "episode_records": count, "share": round(count / total, 4) if total else None}
            for author, count in top
        ],
        "top_author_share": round(top[0][1] / total, 4) if total and top else None,
    }


def time_instrument_profile(episodes: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    sessions = Counter(str(episode.get("session") or "unknown") for episode in episodes)
    setup_times = Counter(str(episode.get("setup_time") or "unknown") for episode in episodes)
    executed: Counter[str] = Counter()
    context: Counter[str] = Counter()
    for episode in episodes:
        for symbol in episode.get("instrument", []) or ["unknown"]:
            executed[str(symbol)] += 1
        for symbol in episode.get("market_context_instruments", []) or []:
            context[str(symbol)] += 1
    return (
        {
            "sessions": dict(sessions.most_common()),
            "setup_times_as_stated": dict(setup_times.most_common()),
            "timezone_caveat": "Times are retained as Discord authors stated them; unstated timezones are not normalized.",
        },
        {
            "executed_instruments_as_stated": dict(executed.most_common()),
            "market_context_instruments": dict(context.most_common()),
            "comparative_caveat": "Unknown executed instruments and intermarket context are kept separate; no NQ-vs-ES superiority is inferred.",
        },
    )


def failure_profile(episodes: list[dict[str, Any]], messages: dict[str, dict[str, Any]]) -> dict[str, Any]:
    losses = [episode for episode in episodes if episode.get("outcome") == "loss"]
    issues = Counter(
        str(issue)
        for episode in losses
        for issue in (episode.get("rules_violated_or_execution_issues") or episode.get("rules") or [])
    )
    quality_flags = Counter(
        str(flag)
        for episode in losses
        for flag in ((episode.get("rejection_block_use") or {}).get("explicit_quality_flags") or [])
    )
    examples: list[dict[str, Any]] = []
    for episode in losses[:8]:
        ids = episode_evidence_ids(episode)
        message_id = next((value for value in ids if value in messages), None)
        examples.append(
            {
                "episode_id": episode.get("episode_id"),
                "message_id": message_id,
                "issues": episode.get("rules_violated_or_execution_issues") or episode.get("rules") or [],
                "exact_excerpt": message_text(messages[message_id])[:700] if message_id else None,
            }
        )
    return {
        "matched_loss_episode_records": len(losses),
        "common_rule_or_execution_issues": [
            {"issue": issue, "episode_records": count} for issue, count in issues.most_common(12)
        ],
        "explicit_quality_flags": [
            {"flag": flag, "episode_records": count} for flag, count in quality_flags.most_common()
        ],
        "examples": examples,
        "caveat": "Failure frequencies are selected-corpus descriptions and can reflect author/reporting concentration.",
    }


def evidence_records(
    matched: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    pool_ids: list[str],
    messages: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    roles: dict[str, set[str]] = defaultdict(set)
    episode_ids: dict[str, set[str]] = defaultdict(set)
    outcomes: dict[str, set[str]] = defaultdict(set)
    for episode in matched:
        role = "failed_example" if episode.get("outcome") == "loss" else "trade_example"
        for message_id in episode_evidence_ids(episode):
            if message_id in messages:
                roles[message_id].add(role)
                episode_ids[message_id].add(str(episode.get("episode_id")))
                outcomes[message_id].add(str(episode.get("outcome") or "unknown"))
    for claim in claims:
        for message_id in claim["evidence_message_ids"]:
            if message_id in messages:
                roles[message_id].add("rule_or_answer")
    for message_id in pool_ids:
        roles[message_id].add("recurring_context")

    # Balance resolved wins/losses, rules, then recurring descriptions.
    ranked = sorted(
        roles,
        key=lambda message_id: (
            0 if "rule_or_answer" in roles[message_id] else 1,
            0 if "failed_example" in roles[message_id] else 1,
            0 if "trade_example" in roles[message_id] else 1,
            str(messages[message_id].get("timestamp_utc") or ""),
        ),
    )
    output: list[dict[str, Any]] = []
    for message_id in ranked[:24]:
        row = messages[message_id]
        output.append(
            {
                "message_id": message_id,
                "timestamp_utc": row.get("timestamp_utc"),
                "author": row.get("author"),
                "thread_title": row.get("thread_title"),
                "roles": sorted(roles[message_id]),
                "episode_ids": sorted(value for value in episode_ids[message_id] if value != "None"),
                "outcomes": sorted(outcomes[message_id]),
                "exact_excerpt": message_text(row)[:900],
                "excerpt_truncated": len(message_text(row)) > 900,
            }
        )
    return output


def confidence_profile(
    episodes: list[dict[str, Any]], claims: list[dict[str, Any]], outcomes: dict[str, Any], authors: dict[str, Any]
) -> dict[str, Any]:
    score = 0.25
    score += min(len(episodes), 20) / 20 * 0.25
    score += min(authors["distinct_authors"], 8) / 8 * 0.18
    score += min(outcomes["comparable_resolved_instances"], 20) / 20 * 0.17
    score += min(len(claims), 5) / 5 * 0.15
    if (authors.get("top_author_share") or 0) > 0.6:
        score -= 0.12
    score = max(0.0, min(score, 1.0))
    level = "high_corpus_support" if score >= 0.75 else "moderate_corpus_support" if score >= 0.55 else "low_corpus_support"
    return {
        "level": level,
        "score": round(score, 3),
        "meaning": "Confidence that this is a recurring, auditable corpus model; not confidence that the model will win.",
        "basis": {
            "matched_episode_records": len(episodes),
            "distinct_authors": authors["distinct_authors"],
            "comparable_resolved_instances": outcomes["comparable_resolved_instances"],
            "relevant_rb_claims_or_answers": len(claims),
            "top_author_share": authors.get("top_author_share"),
        },
    }


def promote_candidate(
    spec: CandidateSpec,
    episodes: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    pool_ids: list[str],
    messages: dict[str, dict[str, Any]],
    min_episodes: int,
    min_authors: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    authors = author_profile(episodes)
    outcomes = comparable_outcomes(episodes)
    inclusion = build_rule_records(spec, spec.inclusion, pool_ids, messages)
    exclusion = build_rule_records(spec, spec.exclusion, pool_ids, messages)
    missing_required_inclusion = [item["rule"] for item in inclusion if item["required"] and not item["evidence_message_ids"]]
    eligible = (
        len(episodes) >= min_episodes
        and authors["distinct_authors"] >= min_authors
        and len(pool_ids) >= 3
        and len(missing_required_inclusion) <= 1
    )
    audit = {
        "model_id": spec.model_id,
        "matched_episode_records": len(episodes),
        "distinct_authors": authors["distinct_authors"],
        "evidence_message_ids": len(pool_ids),
        "missing_required_inclusion_rules": missing_required_inclusion,
        "promoted": eligible,
        "reason": None if eligible else "support/diversity/rule-evidence threshold not met",
    }
    if not eligible:
        return None, audit

    time_profile, instrument_profile = time_instrument_profile(episodes)
    confidence = confidence_profile(episodes, claims, outcomes, authors)
    classification = (
        "documented_recurring"
        if len(episodes) >= 5 and authors["distinct_authors"] >= 3
        else "provisional_derived"
    )
    model = {
        "model_id": spec.model_id,
        "name": spec.name,
        "classification": classification,
        "confidence": confidence,
        "material_distinction": spec.material_distinction,
        "exact_inclusion_rules": inclusion,
        "exact_exclusion_rules": exclusion,
        "confluences": {
            "required_or_near_required": list(spec.required_confluences),
            "supportive": list(spec.supportive_confluences),
            "not_sufficient_alone": list(spec.insufficient_alone),
            "observed_in_matched_episodes": [
                {"confluence": tag, "episode_records": count}
                for tag, count in Counter(
                    tag for episode in episodes for tag in set(full_tags(episode))
                ).most_common(24)
            ],
        },
        "invalidation": {
            "price_or_structure": spec.price_invalidation,
            "narrative_or_eligibility": spec.narrative_invalidation,
            "caveat": "Where the Discord corpus did not state a universal technical invalidation, none is invented.",
        },
        "time_and_session": time_profile,
        "instruments": instrument_profile,
        "entry_and_execution": build_rule_records(spec, spec.entry_execution, pool_ids, messages),
        "risk_and_stop_management": build_rule_records(spec, spec.risk_management, pool_ids, messages),
        "target_and_trade_management": build_rule_records(spec, spec.target_management, pool_ids, messages),
        "supporting_outcome_counts": outcomes,
        "selected_corpus_association": {
            "observed_win_share": outcomes["observed_win_share"],
            "comparable_resolved_instances": outcomes["comparable_resolved_instances"],
            "interpretation": "Descriptive association in selected Discord journal episodes only; not probability or expectancy.",
        },
        "author_concentration": authors,
        "failure_profile": failure_profile(episodes, messages),
        "evidence": evidence_records(episodes, claims, pool_ids, messages),
        "caveats": [
            "Discord journals are self-reported and selectively posted.",
            "Screenshots and image-only rules were not independently interpreted.",
            "A matched episode can support more than one model; model counts are not additive.",
            "Unknown instruments, timezones, and outcomes remain unknown.",
            "Outcome shares are not a backtest, causal estimate, market probability, or forward expectancy.",
        ],
    }
    return model, audit


def add_association_ranking(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comparable = [
        model for model in models
        if model["supporting_outcome_counts"]["comparable_resolved_instances"] >= 5
        and model["supporting_outcome_counts"]["observed_win_share"] is not None
    ]
    comparable.sort(
        key=lambda model: (
            -model["supporting_outcome_counts"]["observed_win_share"],
            -model["supporting_outcome_counts"]["comparable_resolved_instances"],
            model["model_id"],
        )
    )
    output: list[dict[str, Any]] = []
    for index, model in enumerate(comparable, start=1):
        denominator = model["supporting_outcome_counts"]["comparable_resolved_instances"]
        share = model["supporting_outcome_counts"]["observed_win_share"]
        if len(comparable) == 1:
            label = "only_model_with_minimum_comparable_denominator"
        elif index == 1:
            label = "higher_observed_share_within_selected_models"
        elif index == len(comparable):
            label = "lower_observed_share_within_selected_models"
        else:
            label = "middle_observed_share_within_selected_models"
        record = {
            "rank": index,
            "model_id": model["model_id"],
            "name": model["name"],
            "association_label": label,
            "observed_win_share": share,
            "comparable_resolved_instances": denominator,
            "warning": "Selected-corpus association only; never interpret as market probability or expectancy.",
        }
        model["selected_corpus_association"]["within_model_set_rank"] = index
        model["selected_corpus_association"]["association_label"] = label
        output.append(record)
    for model in models:
        if model not in comparable:
            model["selected_corpus_association"]["association_label"] = "insufficient_comparable_denominator"
    return output


def validate_document(
    document: dict[str, Any], messages: dict[str, dict[str, Any]], trade: dict[str, Any], rb: dict[str, Any]
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    models = document.get("models", [])
    if len(models) > MAX_MODELS:
        errors.append(f"model_count_exceeds_{MAX_MODELS}")
    model_ids = [model.get("model_id") for model in models]
    if len(model_ids) != len(set(model_ids)):
        errors.append("duplicate_model_ids")
    unknown_evidence: list[dict[str, str]] = []
    duplicate_evidence: list[dict[str, str]] = []
    for model in models:
        evidence_ids = [str(item.get("message_id")) for item in model.get("evidence", [])]
        if len(evidence_ids) != len(set(evidence_ids)):
            duplicate_evidence.append({"model_id": model["model_id"], "reason": "duplicate evidence IDs"})
        for message_id in evidence_ids:
            if message_id not in messages:
                unknown_evidence.append({"model_id": model["model_id"], "message_id": message_id})
        for section in ("exact_inclusion_rules", "exact_exclusion_rules", "entry_and_execution", "risk_and_stop_management", "target_and_trade_management"):
            for item in model.get(section, []):
                for message_id in item.get("evidence_message_ids", []):
                    if message_id not in messages:
                        unknown_evidence.append({"model_id": model["model_id"], "message_id": message_id})
        counts = model["supporting_outcome_counts"]
        if counts["comparable_wins"] + counts["comparable_losses"] != counts["comparable_resolved_instances"]:
            errors.append(f"outcome_arithmetic:{model['model_id']}")
    if unknown_evidence:
        errors.append("unknown_evidence_message_ids")
    if duplicate_evidence:
        errors.append("duplicate_model_evidence_ids")
    if trade.get("validation", {}).get("passed") is False:
        warnings.append("trade_analysis_input_validation_not_passed")
    if rb.get("validation", {}).get("passed") is False:
        warnings.append("rb_analysis_input_validation_not_passed")
    if not models:
        warnings.append("no_candidate_cleared_promotion_thresholds")
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "unknown_evidence": unknown_evidence,
        "duplicate_evidence": duplicate_evidence,
        "invariants_checked": [
            "at most five materially distinct model templates",
            "unique model IDs and evidence IDs",
            "all model and rule evidence IDs resolve to the merged Discord export",
            "comparable win/loss arithmetic reconciles",
            "corpus associations are labeled non-causal and non-expectancy",
        ],
    }


def build_document(
    raw: dict[str, Any], trade: dict[str, Any], rb: dict[str, Any], min_episodes: int, min_authors: int
) -> dict[str, Any]:
    messages, array_counts = build_message_index(raw)
    episodes = trade.get("episodes")
    if not isinstance(episodes, list):
        raise AnalysisError("trade analysis must contain an episodes array")
    claims = collect_rb_claims(rb)
    promoted: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    promoted_episode_sets: dict[str, set[str]] = {}
    for spec in candidate_specs():
        matched, relevant_claims, pool_ids = select_candidate_pool(spec, episodes, claims, messages)
        model, candidate_audit = promote_candidate(
            spec, matched, relevant_claims, pool_ids, messages, min_episodes, min_authors
        )
        audit.append(candidate_audit)
        if model is not None:
            promoted.append(model)
            promoted_episode_sets[model["model_id"]] = {
                str(episode.get("episode_id")) for episode in matched
            }

    # Remove a weaker near-duplicate if two candidate episode sets are almost identical.
    removed_overlap: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for model in sorted(
        promoted,
        key=lambda item: (
            -item["confidence"]["score"],
            -item["supporting_outcome_counts"]["comparable_resolved_instances"],
            item["model_id"],
        ),
    ):
        current = promoted_episode_sets[model["model_id"]]
        duplicate_of = None
        for other in kept:
            prior = promoted_episode_sets[other["model_id"]]
            union = current | prior
            jaccard = len(current & prior) / len(union) if union else 0.0
            if jaccard >= 0.85:
                duplicate_of = {"model_id": other["model_id"], "episode_jaccard": round(jaccard, 4)}
                break
        if duplicate_of:
            removed_overlap.append({"model_id": model["model_id"], "near_duplicate_of": duplicate_of})
        else:
            kept.append(model)
    models = kept[:MAX_MODELS]
    ranking = add_association_ranking(models)

    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "discord_three_month_trading_models",
        "source": {
            "raw_export": str(DEFAULT_RAW.resolve()),
            "trade_analysis": str(DEFAULT_TRADE.resolve()),
            "rejection_block_analysis": str(DEFAULT_RB.resolve()),
            "source_scope": "Discord artifacts only; no web, market data, or external trading knowledge",
            "guild_id": (raw.get("metadata") or {}).get("guild_id"),
            "requested_window_start_date": ((raw.get("metadata") or {}).get("merge") or {}).get("requested_window_start_date"),
            "requested_window_end_date": ((raw.get("metadata") or {}).get("merge") or {}).get("requested_window_end_date"),
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        "methodology": {
            "candidate_templates": (
                "Four operational templates previously observed in the same Discord corpus are re-tested "
                "against the independent three-month trade/RB artifacts. A template is not promoted merely to fill slots."
            ),
            "promotion_thresholds": {
                "minimum_matched_episode_records": min_episodes,
                "minimum_distinct_authors": min_authors,
                "minimum_resolved_rule_evidence": "all but at most one required inclusion rule",
                "maximum_models": MAX_MODELS,
            },
            "outcome_comparison": (
                "Uses only episodes the trade artifact marks eligible for single-instance explicit win/loss "
                "confluence comparison. Breakeven, mixed, cancelled, open, paper, aggregate, and unknown records are excluded."
            ),
            "probability_language": (
                "Higher/lower labels rank observed win shares only among promoted selected-corpus models "
                "with at least five comparable instances; they are not market probabilities or expectancy."
            ),
            "unknown_policy": "Unknown values remain unknown and screenshots are not inferred.",
        },
        "corpus_counts": {
            "raw_unique_message_ids": len(messages),
            "raw_array_rows": array_counts,
            "trade_episode_records": len(episodes),
            "rb_claims_or_answers_with_evidence": len(claims),
            "promoted_models": len(models),
        },
        "models": models,
        "selected_corpus_association_ranking": {
            "basis": "Observed selected-corpus win share among comparable matched episodes; overlapping model membership allowed.",
            "minimum_denominator": 5,
            "ranking": ranking,
            "warning": "This ranking is not a backtest, probability estimate, causal effect, or forward expectancy.",
        },
        "candidate_audit": audit,
        "near_duplicate_candidates_not_promoted": removed_overlap,
        "limitations": [
            "All evidence comes from the provided Discord artifacts; no external market knowledge is used.",
            "Journal outcomes are self-reported and selectively posted.",
            "Automated text extraction cannot recover chart-only rules.",
            "Model templates can overlap on the same episode; counts cannot be summed across models.",
            "Author concentration, unknown instruments, and reporting style can move observed shares.",
            "No selected-corpus association establishes market expectancy.",
        ],
    }
    document["validation"] = validate_document(document, messages, trade, rb)
    return document


def self_test() -> None:
    base = {
        "confluences": ["key_open:10am", "rejection_block:5m:entry", "fair_value_gap:15m:context"],
        "rejection_block_use": {"instances": [{"timeframe": "5m", "role": "entry"}]},
    }
    assert is_10am_rb(base)
    assert not is_mmxm_stdv_breaker(base)
    assert is_sweep_displacement_retrace(
        {"confluences": ["liquidity_sweep", "displacement", "fair_value_gap:1m:entry"]}
    )
    segments = comparable_outcomes(
        [
            {"outcome": "win", "eligible_trade_instances_for_win_loss_confluence_comparison": 1},
            {"outcome": "loss", "eligible_trade_instances_for_win_loss_confluence_comparison": 1},
            {"outcome": "breakeven", "eligible_trade_instances_for_win_loss_confluence_comparison": 0},
        ]
    )
    assert segments["comparable_resolved_instances"] == 2
    assert segments["observed_win_share"] == 0.5


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--trade", type=Path, default=DEFAULT_TRADE)
    parser.add_argument("--rb", type=Path, default=DEFAULT_RB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--min-episodes", type=int, default=2)
    parser.add_argument("--min-authors", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Replace only the 3-month model output if it already exists.")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        self_test()
        print(json.dumps({"self_test": "passed", "candidate_templates": len(candidate_specs())}, indent=2))
        return 0
    for name in ("raw", "trade", "rb"):
        path = getattr(args, name).resolve()
        if not path.is_file():
            print(f"ERROR: missing --{name} input: {path}", file=sys.stderr)
            return 2
    output = args.output.resolve()
    if output in PROTECTED_OUTPUTS:
        print(f"ERROR: protected output path: {output}", file=sys.stderr)
        return 2
    if output.exists() and not args.force and not args.dry_run:
        print(f"ERROR: refusing to overwrite {output}; pass --force for this 3-month artifact", file=sys.stderr)
        return 2
    try:
        raw = read_json(args.raw.resolve())
        trade = read_json(args.trade.resolve())
        rb = read_json(args.rb.resolve())
        document = build_document(raw, trade, rb, args.min_episodes, args.min_authors)
    except AnalysisError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    summary = {
        "output": str(output),
        "dry_run": args.dry_run,
        "model_count": len(document["models"]),
        "models": [model["model_id"] for model in document["models"]],
        "validation": document["validation"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not document["validation"]["passed"]:
        return 1
    if args.dry_run:
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
