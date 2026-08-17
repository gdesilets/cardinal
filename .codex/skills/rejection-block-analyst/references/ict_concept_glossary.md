# ICT / Powell's Rejection Block Concept Glossary

This is the shared vocabulary for the rejection-block-analyst skill. Two kinds of
knowledge are blended here, and you must keep them distinguishable in your own head
even though you present them fluently as one voice:

- **General ICT theory** — the standard, widely-taught definitions of these concepts.
  Use this to explain mechanics and to reason about timeframes/instruments the corpus
  doesn't cover.
- **Corpus-specific nuance** — how Powell's Discord (Domme, Powell, and the community)
  actually apply, restrict, or argue about these concepts. Marked with `[corpus]`. This
  is what makes this skill *this trading community's* second brain rather than a generic
  ICT explainer. Pull exact wording with `scripts/query_corpus.py` when precision matters.

Everything on this page must be described using ICT vocabulary. Don't reach for
moving averages, RSI, MACD, trendlines, or classical chart patterns (head & shoulders,
triangles, etc.) as primary reasoning tools — they're outside the framework this skill
exists to apply. A non-ICT observation (e.g. a high-impact news release, a round number,
raw volume) can be mentioned as supplementary color if it reinforces an ICT-derived read,
never as the basis for a call on its own.

## The core cycle: AMD (Accumulation – Manipulation – Distribution)

The organizing loop nearly everything else here sits inside.

- **Accumulation**: price consolidates in a range, building resting liquidity above and
  below (often visible as relative equal highs/lows). Smart money builds position here.
  Low displacement, choppy price action.
- **Manipulation**: price makes a deliberate, often sharp move *against* the eventual
  real direction to run that resting liquidity (a **liquidity sweep**) before reversing.
  This is the phase that traps retail positioning on the wrong side.
- **Distribution**: the real, intended move. Displacement away from the manipulation
  point, leaving behind imbalance (FVGs) and a point-of-origin structure (an RB, order
  block, or breaker) that price may return to once before continuing.

Reading "which phase are we in" is the first job of any bias/market-read request: is
price ranging (accumulation), sweeping a level against the higher-timeframe draw
(manipulation), or displacing with intent toward an open draw (distribution)? `[corpus]`
The RB analyzer found `amd_cycle` tagged in only a modest, below-baseline share of
strict RB trades (2W/6L, n=8 in the 3-month set) — treat it as a framing lens, not a
confluence to count toward a score by itself; report that sparsity honestly if asked.

## Rejection Block (RB) — the skill's namesake concept

An RB is the last opposing candle (or opposing-close sequence) immediately before a
displacement in the other direction — the wick/body that got "rejected." Mechanically
it looks like a failed continuation: price pushes one way, gets rejected, and the
candle(s) that mark that rejection become a point of interest (POI) if price returns.

`[corpus]` The Discord corpus is explicit and consistent on one point: **an RB is not
"any wick."** It's treated as a *contextual rejection structure*, not a standalone
candle pattern. The recurring identification components, in order of how often they
appear in RB discussion: timeframe selection, volume imbalance/FVG relationship,
liquidity sweep, CE ("consequent encroachment")/start-boundary selection, and
"meaningful rejection" (explicitly distinguished from routine wicks). A direct mentor
reply ties reversal-point identification to PD arrays + liquidity sweep + bias + news,
not to the candle shape alone.

Key corpus-sourced rules to apply when you evaluate a candidate RB:
- **Wait for the close.** Multiple direct replies favor waiting for the RB (or the
  CISD/trigger it's part of) to close before treating it as valid, especially before a
  higher-timeframe candle open. Don't front-run an unclosed candle.
- **Freshness matters.** A technically valid RB can be **non-actionable** if it has
  already delivered to its draw, is already mitigated, is off higher-timeframe bias, or
  is mistimed (see the 11AM cutoff below). "Valid" and "actionable" are different
  questions — always answer both.
- **No single universal candle-color/CE rule.** Candle color, whether a liquidity sweep
  must occur in the *same* candle as the RB (corpus answer: no, it doesn't have to be
  in the same candle), and exact CE/start-boundary selection remain chart-specific in
  the captured record — don't state these as fixed formulas.
- **Stop placement**: beyond the real structure/wick that would invalidate the
  rejection thesis, not at an arbitrary point inside a large wick.
- **Nested RBs** (an RB inside a higher-timeframe RB) are corpus-confirmed as usable,
  not merely tolerated — but a mentor reply stopped short of calling nested RBs
  categorically *higher probability* than a standalone RB. Treat "nested" as a
  legitimate confluence-stacking pattern, not an automatic upgrade.
- **Session/timing concentration**: RB discussion overwhelmingly clusters around the
  10AM key open, then 9:30 market open, midnight open, London, 18:00, and Asia, in that
  order. A direct mentor reply gives an explicit **11AM cutoff** for new 10AM-model RB
  entries — after that, stop hunting for that specific model's entries for the session.

## Fair Value Gap (FVG) and Inverse Fair Value Gap (IFVG)

- **FVG**: a three-candle imbalance where candle 1's wick and candle 3's wick don't
  overlap, leaving a gap that represents inefficient, one-sided delivery. Price often
  returns to rebalance part or all of this gap before continuing the move it formed
  from — a classic PD (premium/discount) array entry location.
- **IFVG**: an FVG that gets violated/traded through and then flips role — the zone that
  was support/resistance-by-imbalance becomes the opposite. Signals a potential shift
  in delivery, often coinciding with a CISD.

`[corpus]` FVG/IFVG is one of the most heavily corpus-tagged confluences and shows up
in both wins and losses at a similar rate to baseline — it's a near-universal location
tool, not a differentiator by itself. It's most useful as the *location* leg of a stack
(HTF PDA → LTF RB/CISD at the FVG), matching Model 2's structure below
(`query_corpus.py strategy htf-pda-ltf-rb`).

## Order Block (OB), Breaker Block, and Imbalanced Order Block

- **Order Block**: the last opposing candle before a displacement that leaves structure
  behind (a mini origin point), similar in spirit to an RB but typically drawn from
  full candle body/range on a higher timeframe and tied to a break of structure.
- **Breaker Block**: an order block that fails — price sweeps through it, invalidating
  it as a continuation zone — and then that same zone flips to work as support/
  resistance in the *other* direction on the retest. Conceptually the OB-family
  equivalent of an IFVG: the failure itself becomes the new confluence.
- **Imbalanced Order Block**: an order block whose formation candle itself contains a
  FVG/imbalance inside it — i.e., the OB and a fair value gap co-occur on the same
  candle(s). Treated as a higher-quality OB because it stacks a PD-array imbalance
  directly onto the structural POI instead of requiring two separate zones to align.

`[corpus]` Breaker showed one of the largest above-baseline associations in the strict
RB comparison (4W/5L, n=9 — a small sample, flag that explicitly if you cite it) and
underpins **Model 3**, `mmxm-stdv-breaker` (market-maker model + standard deviation
zone + breaker entry). Order block on its own was also above baseline (15W/25L, n=40)
and is a required "supportive" identification component in Model 1
(`10am-key-open-rb`) and Model 4 (`sweep-displacement-fvg-retrace` — currently below
the corpus's own n=15 reliability threshold, see `references/corpus_query_guide.md`).

## SMT Divergence (Smart Money Technique / Inter-market divergence)

Compares two correlated instruments (e.g., ES vs NQ, or a pair's components) at the
same structural point. When one instrument makes a new high/low and the correlated one
fails to (or vice versa), that non-confirmation — the divergence — suggests the move is
not genuinely supported and is a manipulation signature rather than real distribution.
SMT at a liquidity sweep is one of the strongest confluences for calling manipulation
completed and a reversal likely.

`[corpus]` SMT/SSMT is a heavily-used confluence (roughly a third of both wins and
losses tag it) — like FVG, it's necessary supporting context in many setups rather than
a rare edge. The corpus explicitly could **not** resolve NQ-vs-ES superiority (only
13 NQ-family vs 3 ES-family strict executed instances — both under the analyzer's
minimum-10 threshold). Never assert one instrument is "better" for RBs from this corpus;
say the comparison is data-insufficient if asked. Also keep **market-context** instrument
mentions (e.g., "ES showed SMT") separate from the **executed** instrument of a trade —
the corpus repeatedly warns these are not interchangeable.

## Liquidity Sweep and Engineered Liquidity

- **Liquidity sweep**: price runs through a resting pool of stops/orders (a prior
  high/low, an equal-highs/lows cluster) and reverses, using that liquidity as fuel for
  the real move. This is the mechanical signature of the Manipulation phase of AMD.
- **Engineered liquidity**: a pool that looks *deliberately* obvious — clean, repeated
  equal highs or equal lows that are unusually visible on the chart. The read is that
  this is liquidity smart money engineered to be attractive to retail stops/entries
  specifically so it can be swept, versus liquidity that formed incidentally.

`[corpus]` The corpus treats sweep requirement as **genuinely unresolved/chart-dependent**
— captured text contains both "sweep required" and "not universal" positions, and the
RB analysis explicitly flags this as an unresolved tension rather than picking a side.
A direct community reply does call the sweep "necessary" but clarifies it doesn't need
to happen in the same candle as the RB. Standalone `liquidity_sweep` tag was
*below*-baseline in the strict comparison (3W/8L, n=11) — small sample, but don't imply
a sweep alone is a green light; it's usually valuable in combination with SMT and a PDA,
not as an isolated trigger.

## Draw on Liquidity / Data Highs / Data Lows

- **Draw on liquidity**: the next significant resting liquidity or imbalance that price
  is "drawn" toward — this is what defines your target and, often, your directional
  bias (price tends to seek the nearest unresolved draw).
- **Data high / Data low**: the highest/lowest point of a defined data range (commonly
  a session or a specific reference window, e.g., the day's high/low, week's high/low).
  These act as both potential liquidity pools to be swept and boundaries that define
  premium/discount for that range.

`[corpus]` `draw_on_liquidity` was one of the largest above-baseline associations in
the strict comparison (11W/13L, n=24) — use it explicitly as the "why" behind a target,
not just a label. A direct mentor reply on invalidation stresses that a block can remain
technically valid while the *daily draw has already delivered* — at that point the trade
stops being actionable even though the pattern is intact. Always check whether the
relevant draw is still open before calling a setup live.

## Key Opens (Midnight Open, 9:30 Market Open/True Day Open, 10AM Open)

Specific clock-time price levels treated as reference/PD-array-generating anchors:
- **Midnight Open (00:00)**: commonly used as a bias reference — price trading above/
  below it frames intraday premium/discount.
- **9:30 Market Open / "True Day" Open**: the NYSE cash open; a major volatility and
  reference-level event.
- **10AM Open**: in this community specifically, the single most discussed key level —
  see Model 1 (`10am-key-open-rb`) below. Treated as a high-conviction reversal/rejection window in its own
  right, not merely a reference line.

`[corpus]` `10am_key_open` co-mentions dominate RB discussion by a wide margin (987
messages) versus 9:30/market open (134), midnight (125), London (95), 18:00 (84), Asia
(65). Standalone `key_opens`/`key_open` tags were slightly *below* the strict RB
baseline (27.5–27.9% vs 29.8% baseline) — a key open alone is common but not a strong
discriminator; it earns its keep by anchoring **which** RB/FVG/OB you should be
watching, not as a confluence to count on its own. See `10am-key-open-rb`'s operational rules
below for the concrete workflow.

## CISD (Change in State of Delivery)

The moment price closes back through a short-term structural boundary in the opposite
direction it had just been delivering in — a close-based confirmation that the near-term
order flow has flipped. Functions similarly to a lower-timeframe break of structure, and
is frequently the "trigger" event that turns a PDA (RB/OB/FVG) from a watch-item into an
executable signal.

`[corpus]` `cisd` was one of the larger above-baseline associations in the strict
comparison (7W/10L, n=17). It's explicitly named as the entry trigger in Models 1 and 2
— "wait for the CISD/RB to close" is the corpus's dominant answer to "how do I avoid
front-running an unconfirmed setup."

## Fibonacci Levels / OTE (Optimal Trade Entry) and Premium/Discount

- **Premium/Discount**: given a defined range (swing high to swing low), the midpoint
  (0.5 fib) splits it into a premium (upper) half — where you look to sell/short — and
  a discount (lower) half — where you look to buy/long. This is the framework, not a
  specific number.
- **OTE (Optimal Trade Entry)**: the 0.62–0.79 retracement zone of a move, treated as
  the statistically favored re-entry pocket after a displacement, ideally overlapping a
  PDA (RB/OB/FVG) for confluence.
- **Standard Deviation**: fib-extension-based projection zones beyond 1.0 used to locate
  probable reaction/reversal areas for larger, model-driven moves (see Model 3,
  `mmxm-stdv-breaker`).

`[corpus]` `ote_fibonacci` was one of the most-tagged confluences across both wins and
losses (roughly a third to two-fifths of each) — it's a workhorse location tool used
constantly, not a rare edge. `standard_deviation`, despite a small sample (n=15), showed
the single largest above-baseline association in the strict comparison (46.7% vs 29.8%
baseline) — worth naming explicitly when a market-maker-model / standard-deviation setup
is present, while flagging the small n. `premium_discount` alone was below baseline
(24.0%, n=25) — like key opens, it's a locational filter, not a standalone trigger.

## Data quality reminder

Every `[corpus]` statement above traces to `RESEARCH_SUMMARY_3MONTH.md` and the
`discord_trading_research_3month.sqlite` findings/confluence tables — pull exact
message text with `scripts/query_corpus.py` before quoting a specific number or claim
verbatim to the user. See `references/corpus_query_guide.md` for how, and
`references/confidence_framework.md` for how to turn all of this into an actual rating.
