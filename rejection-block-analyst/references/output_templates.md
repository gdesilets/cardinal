# Output Templates

Use these as structural skeletons, not rigid forms to fill in mechanically — adapt
section depth to what the user actually asked and what data you actually have. Every
template ends with the three-way confidence rating from
`references/confidence_framework.md`.

## 1. Market Read / Bias Check ("what's price doing right now")

Use when the user asks something like "what's your read on NQ" or "bullish or bearish
right now" without asking for a full multi-timeframe breakdown.

```
## Market Read — <instrument>, <timeframe(s) referenced>

**AMD phase:** Accumulation / Manipulation / Distribution — one line on why.
**Directional lean:** Bullish / Bearish / Neutral, with the draw on liquidity driving it.
**Key level in play:** the nearest relevant key open / PDA / liquidity pool, and what
would confirm or invalidate the lean.

**Long confidence:** Tier (X/5) — reasoning
**Short confidence:** Tier (X/5) — reasoning
**No-Trade confidence:** Tier (X/5) — reasoning
```

If the user hasn't given you current price/structure (no screenshot, no described
highs/lows/candles), say so and ask for it before rating anything — do not invent price
levels or pretend to have a live feed. See the "no live feed" rule in SKILL.md.

## 2. Levels / Zones of High Liquidity

Use when asked to map out liquidity rather than call a direction.

```
## Liquidity Map — <instrument>

### Resting liquidity (draws)
- <level/zone> — <why it's liquidity: data high/low, relative equal highs/lows,
  engineered vs incidental, prior session high/low, key open> — <untouched/swept status>

### PDAs in play (RB / OB / FVG / Breaker)
- <zone> — <type, timeframe, fresh/mitigated status, what it would confirm>

### Most likely next draw
State the single draw price action is most likely seeking right now, and why (AMD
phase + freshness + HTF bias), separate from any specific trade idea.
```

## 3. Full Setup Idea (sequence of events before triggering)

This is the core "give me a trade idea" output. The sequence-of-events framing matters
— the user explicitly wants to know what has to happen first, not just an entry price.

```
## Setup Idea — <direction> <instrument>

**Thesis (one line):** the AMD/bias story in a sentence.

**Required sequence of events (in order):**
1. <e.g., price sweeps the London low, running engineered relative-equal-lows liquidity>
2. <e.g., SMT confirms against <correlated instrument>>
3. <e.g., CISD closes back above <level>, confirming the shift>
4. <e.g., price retraces into the <RB/OB/FVG> at the <OTE zone>>
5. <trigger: closed 1m RB inside that zone>

**Status right now:** which of the above have already happened vs. still pending —
be explicit about where in the sequence price currently is.

**If triggered:**
- Entry: <trigger description, not just a price>
- Invalidation/stop: <structural level, and why it invalidates the thesis>
- Target(s): <the draw on liquidity being sought>
- Model match (if applicable): note if this matches one of the four corpus models
  (`query_corpus.py models`) and cite its corpus win/loss n if relevant.

**Long confidence:** Tier (X/5) — reasoning
**Short confidence:** Tier (X/5) — reasoning
**No-Trade confidence:** Tier (X/5) — reasoning (often the *current* correct answer if
steps 1-3 above haven't happened yet — say so plainly rather than implying urgency)
```

## 4. Reviewing/Critiquing the User's Own Setup

Use whenever the user presents their own trade idea or bias for evaluation. This is
where the independent-bias directive matters most — read SKILL.md's directive section
before writing this.

```
## Review — <what the user proposed>

**Independent read:** state your own bias/AMD read *before* referencing theirs, so it's
clear this isn't anchored to what they said.

**Agreement/disagreement:** does your independent read support their setup? If not,
name the specific confluence, freshness issue, HTF conflict, or missing trigger
confirmation that breaks it — cite the corpus finding or general-ICT reasoning behind
the objection, don't just assert it.

**If it holds up:** confirm, then tighten it — exact trigger to wait for, better stop
placement (beyond real structure vs. their stated stop), and whether the target draw is
still open.

**If it doesn't hold up:** say so directly. Don't soften a real invalidation reason to
avoid disagreeing with the user — that defeats the purpose of this skill.

**Long confidence:** Tier (X/5)
**Short confidence:** Tier (X/5)
**No-Trade confidence:** Tier (X/5)
```

## 5. Top-Down Analysis (full multi-timeframe ICT trend read)

The most complete output type — use when asked for a "top-down analysis," "TDA," or
"what's the plan for today/this session." Walk the timeframe hierarchy top to bottom,
narrowing the draw and bias at each step, per the user's stated timeframe hierarchy:

```
## Top-Down Analysis — <instrument>, <date/session>

### 1D
Structure, current draw on liquidity, premium/discount context. HTF bias.

### 4HR
How 4H structure sits inside the 1D bias — confirming or introducing conflict.
Relevant PDAs (OB/FVG/Breaker) still open on this timeframe.

### 1HR
Narrower structure and draw. Note AMD phase read at this resolution.

### 15M
Session-relevant key opens (midnight/9:30/10AM as applicable) and any PDAs forming
around them. This is usually where the session's actual thesis crystallizes.

### 5M
Confirmation-level detail — SMT status, liquidity sweep status, CISD watch level.

### 1M — Entry trigger only
State explicitly what 1-minute confirmation (closed RB/CISD) would trigger the idea.
Do not treat 1M structure as a bias input — per the user's own framework it's
entry-trigger-only, not a trend-confirmation timeframe.

### Session bias summary
One paragraph tying the above into a single directional (or neutral) session bias.

### Confidence-based level predictions
Name the specific levels/draws you expect price to interact with *this session*, each
with its own confidence tier — this is different from the trade-level confidence rating
above; it's about which liquidity gets reached, not whether a specific entry triggers.
- <level> — Tier (X/5) — reasoning
- <level> — Tier (X/5) — reasoning

### Trade-level confidence
**Long confidence:** Tier (X/5)
**Short confidence:** Tier (X/5)
**No-Trade confidence:** Tier (X/5)
```

Keep the "confidence-based predictions" section honest: these are still framed as
ICT-reasoned expectations, not calibrated probabilities of price reaching a level. Use
the same tier/reasoning discipline as everywhere else — never a bare percentage.
