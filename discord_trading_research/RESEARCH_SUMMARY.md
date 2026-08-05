# Rejection-block and trade-model study

## Scope

This study uses only messages from the specified Discord server. The primary `premium-journals` search covers the requested 14-day window from 2026-07-06 through 2026-07-20, with observed posts from 2026-07-07 through 2026-07-20 UTC. It contains 1,514 unique primary messages across 51 journal threads, plus completed targeted searches for rejection-block wording and premium-channel questions.

The primary search is complete. A separate broad server-wide search for the shorthand `RB` became unstable after result 325; those 325 results are retained only as supplemental evidence and are marked partial. No public-web trading knowledge was added.

## Direct answers

### How the channel identifies a rejection block

The strongest recurring idea is that an RB must reject something meaningful; it is not every candle wick. The rejected feature can be liquidity, a PD array, a key open, an imbalance, or another planned higher-timeframe level. A direct answer also accepted volume imbalance as the reason for one specific RB marking, so the corpus does not support a universal rule that every RB must have the same kind of sweep.

Confirmation by candle close matters. In one direct answer, a proposed bearish RB was accepted if the candle closed bearish. Across the journals, the cleaner workflow is to establish higher-timeframe bias and location first, then use a closed 5m/1m/30s RB, CISD, breaker, or displacement as the execution trigger. A lower-timeframe RB is used for tighter risk, but several posts show that a standalone or poorly shaped 1m RB is easy to edge or misclassify.

Freshness matters. A direct answer called an already mitigated RB non-actionable. A technically valid pattern can also be exhausted when the intended draw has already delivered.

The Discord material does not establish one universal candle-color rule, one universal CE-versus-origin entry, or one universal requirement for the prior candle. It also contains a direct “No” to using a 10m-chart RB for one specific setup; this is evidence against that particular marking, not proof that every 10m RB is invalid.

### How the channel invalidates or rejects an RB trade

The corpus separates price invalidation from setup non-eligibility:

- Price invalidation is generally beyond the wick or structure that makes the reversal thesis wrong. No universal tick distance or single candle-close formula is stated.
- An unclosed trigger is not confirmed. Front-running a breaker/RB or skipping the planned lower-timeframe confirmation repeatedly appears in losses.
- An already mitigated RB is not fresh.
- If the planned draw has already delivered, the pattern may remain technically valid but the trade is considered exhausted.
- A bias conflict, wrong premium/discount location, unresolved opposing liquidity on the correlated index, or entry inside chop invalidates the narrative even when several technical labels are present.
- For the strict 10AM model, failure to fully tap the 10AM level removes eligibility. Domme also stated that he stops looking for the 10AM setup at 11AM.
- A stop arbitrarily placed inside a large rejection wick is treated as meaningless risk geometry; the trade needs either a wider structural stop or a valid lower-timeframe trigger that genuinely reduces the invalidation distance.

The captured questions do not resolve whether a close below OTE invalidates an RB that still respects its own level, or whether every RB should be entered at CE, at the start of the block, or only after a lower-timeframe trigger.

### When RBs primarily appear

Among the 201 primary-channel messages that mention RBs, setup-time language appears most often around:

| Time/session phrase | RB messages |
|---|---:|
| 10AM | 67 |
| 9:30 / market open | 18 |
| Midnight | 8 |
| 18:00 | 6 |
| Asia | 6 |
| London | 4 |

These are message mentions, not unique setups, and most posts do not explicitly declare a timezone. The 10AM language is consistently tied to the U.S. index-session/key-open model in the messages, but the database preserves the authors’ exact labels instead of silently normalizing them. The direct operating rules captured for the strict variant are a full 10AM tap and an 11AM cutoff.

### What makes an RB higher probability in this corpus

The strongest combined profile is:

1. A clear higher-timeframe bias or unresolved draw on liquidity.
2. Rejection from a meaningful, fresh higher-timeframe PDA or liquidity event rather than an isolated wick.
3. Correct premium/discount location and, for the timed model, a full key-open interaction.
4. Multiple-timeframe alignment, often a 15m/5m rejection area followed by a 1m/30s trigger.
5. A liquidity sweep or manipulation and, when available, NQ/ES SMT. Domme’s direct answer says 1m SMT is “better if there is,” which makes it supportive rather than universally mandatory.
6. A closed trigger—RB, CISD, breaker, BOS/displacement, or FVG retest—rather than anticipation.
7. A stop beyond meaningful structure and a remaining, realistic target/draw.

Strong self-reported examples combined nested RBs, FVG/OTE/key-open location, SMT, and a closed trigger, with reported outcomes including 2.08R, 4R, 4RR, and 6.75R. These are selected journal reports, not an independently verified backtest.

The lowest-quality profile is the inverse: isolated or poor-looking 1m RB; no HTF bias/PDA; bias conflict; already mitigated level; target already delivered; incomplete 10AM tap; unclosed/front-run trigger; unresolved NQ/ES liquidity; news volatility; chop; or an arbitrary stop inside the wick.

### NQ versus ES

The channel uses RB logic on both NQ and ES. NQ/MNQ appears in 27 primary RB messages, ES/MES in 30, and both appear together in 19. A common workflow is NQ execution with ES used for SMT, relative strength, a PDA, or an opposing-liquidity check.

The outcome ledger cannot support a product comparison: 120 of the 128 strict win/loss instances do not explicitly identify the executed instrument; only four explicit ES and four explicit MNQ instances remain. A direct question asking whether the exact same RB applies on ES was not answered. The correct conclusion is therefore “both are used; superiority is unproven,” not that RBs work better on NQ.

## Outcome profiles

The conservative ledger contains 194 episode records: 50 wins, 105 losses, 18 breakevens, 10 cancelled/no-trade records, 8 unknowns, and 3 mixed sessions. Resolvable aggregate statements bring the reported instance totals to 51 wins, 117 losses, and 19 breakevens. For the confluence comparison, only 128 actual, attributable win/loss instances are eligible: 46 wins and 82 losses. The relational `trades` table has 197 rows because three two-trade episodes with shared, attributable confluences were expanded into two analytical trade instances apiece; the original 194-episode ledger remains embedded as `trade_analysis`.

This loss-heavy sample is a journal/reporting corpus, not a strategy backtest. It has no common risk unit, no denominator of all market opportunities, inconsistent instrument labels, and strong author/reporting concentration. The counts describe what was posted; they do not estimate future expectancy.

The strict winning profile contains 46 instances. Most frequently documented were RB (22), FVG (21), key open (17), OTE/fibonacci (11), liquidity sweep (11), SMT (10), breaker (8), CISD (7), and order block (7). Thirty-two of the 46 were labeled NY AM. Instruments were unknown in 44 of 46.

The strict loss profile contains 82 instances. Most frequently documented were RB (46), FVG (32), key open (25), SMT (24), OTE/fibonacci (17), liquidity sweep (13), standard deviation (12), consequent encroachment (9), order block (9), and bias alignment (7). Repeated execution failures include not following the plan, entering before the marked level or confirmation, impatience, front-running breakers, news exposure, overtrading, and stops placed inside large wicks. Sixty-four of the 82 were labeled NY AM; instruments were unknown in 76.

The important distinction is that familiar labels occur in both wins and losses. Context, confirmation, freshness, and execution quality separate the stronger examples more consistently than any single confluence word.

## Descriptive confluence catalog

The following figures use the strict 128-instance denominator. They are overlapping labels, not independent models, and the “observed win share” is not a market probability.

| Confluence | Wins | Losses | Observed win share | Main caveat |
|---|---:|---:|---:|---|
| CISD | 7 | 4 | 63.6% | 11 occurrences |
| Breaker | 8 | 6 | 57.1% | 78.6% of occurrences came from one author |
| Liquidity sweep | 11 | 13 | 45.8% | Stronger as context than standalone signal |
| Order block | 7 | 9 | 43.8% | 16 occurrences |
| Bias alignment | 5 | 7 | 41.7% | Often under-documented |
| Key open | 17 | 25 | 40.5% | Includes different opens and variants |
| FVG | 21 | 32 | 39.6% | Very common in both outcomes |
| OTE/fibonacci | 11 | 17 | 39.3% | Location alone did not prevent losses |
| RB | 22 | 46 | 32.4% | Broad label; quality varies materially |
| SMT | 10 | 24 | 29.4% | Often present during unresolved decorrelation too |

Small, concentrated labels can show high observed shares by chance. The corpus supports stacked model rules more strongly than ranking trades from one isolated feature.

## Four evidence-distinct trading models

### 1. 10AM key-open rejection model — documented recurring

Establish bias and the remaining draw, then mark the 10AM key open. Prefer overlap with a meaningful PDA; one documented checklist asks for at least two among FVG, OB, RB, and OTE. Under the strict variant, require a full 10AM tap and stop looking at 11AM. Entry variants include a direct limit at the key open or a closed 5m RB/CISD, with 1m/30s refinement only when structure is clean. Invalidation is no full tap, expired time window, narrative conflict/chop, completed draw, or a structural stop being reached.

### 2. HTF PDA to lower-timeframe RB — provisional derived

Start with a daily/4h/1h draw or PDA and identify a meaningful rejection, often on 15m/5m. Require freshness, correct location, and alignment with the intended draw. Use a closed 5m/1m/30s RB, CISD, or displacement trigger; SMT and a sweep strengthen the case. The model becomes non-actionable after mitigation, after the draw completes, when bias conflicts, or when no clean lower-timeframe confirmation forms. This workflow is repeated across journals but is not named as one canonical server model.

### 3. MMXM/LABS with STDV zone and breaker entry — documented recurring

Align HTF/LTF bias and identify the AMD/PO3 or MMXM stage. Use the HTF PDA, premium/discount, timeframe alignment, and standard-deviation zone to locate the reversal area. Wait for the appropriate low-risk model stage or a confirmed breaker and retest. Avoid front-running, consolidation without confirmation, NQ/ES decorrelation where the traded instrument has not reached its own condition, and HTF/LTF conflict. Targets in one documented strategy use standard-deviation objectives; no universal stop formula exists.

### 4. NY-open/macro sweep, displacement, and FVG retest — provisional derived

Begin with HTF bias and a planned liquidity objective. Around the NY open or named macro, wait for a liquidity/Judas sweep, then require BOS/displacement. Enter on a retest of the resulting FVG/iFVG/OB or a confirmed RB. SMT can support the reversal. Skip the trade when displacement never appears, the entry is premature, or the market is still in chop. This sequence recurs in successful and failed examples but is not presented as one canonical named playbook.

A fifth model was not added. Fractal CISD/FVG/breaker entries, SMT-only ideas, Silver Bullet references, and the 10AM 4-hour-expansion discussion did not have enough independent operational evidence to justify separate models without duplicating the four above.

## Captured Q&A and unresolved items

Directly resolved items include:

- A proposed bearish RB was valid if it closed bearish.
- One questioned 1m RB inside a 15m FVG was rejected because of poor shape and proximity to the new 15m candle; the preferred sequence was to let the new candle take the prior low first.
- Domme did not use the proposed 10m-chart RB for the specific 10AM/4h-expansion example.
- The strict 10AM variant waits for a full level tap and stops at 11AM.
- 1m SMT is better when present for RB entries but was not stated as universally required.
- A one- or two-tick NQ/ES sweep can still qualify; the responses say that such sweeps can work.
- Volume imbalance justified one specific RB marking.

Important unresolved questions include:

- Must every RB sweep liquidity, or can volume imbalance/another meaningful rejection substitute?
- Does a close below OTE invalidate an RB that still respects the block?
- Is CE, the origin/start, or a lower-timeframe trigger the preferred universal entry?
- Does wick theory change the preferred CE rule?
- Does the exact same RB work identically on ES as on NQ?

These gaps are preserved as unanswered or conflicting records in the database rather than filled with outside knowledge.

## Data-quality limits

- Outcomes and explanations are self-reported.
- The journals are a selected, loss-heavy sample and do not record every opportunity.
- Images are cataloged as attachments, but chart geometry was not independently reclassified from screenshots.
- Posting time is not assumed to be setup time.
- Most executed instruments and many exact stops/targets are unstated.
- Clock labels are usually not timezone-normalized.
- The broad shorthand search outside the requested forum is partial after result 325.

The database preserves these limitations, evidence links, contradictory examples, and unknown values so another LLM can answer questions without silently converting missing data into trading rules.
