# Confidence Rating Framework

Every market read, setup idea, and top-down analysis needs a confidence rating on
**three separate scenarios at once**: Long, Short, and No-Trade. They are not required
to sum to anything — they're independent qualitative judgments about how much each
scenario is currently supported, not slices of a single probability pie. It is normal
and often correct for all three to be low (genuinely unclear market) or for two to be
elevated at once (e.g., a valid setup on both sides of a level that hasn't tapped yet).

## Why not just give a percentage win rate

The corpus documentation is emphatic about this and you must hold the same line: the
Discord corpus's win/loss numbers are **descriptive associations in a small,
self-reported, overlapping sample** — not a backtest, not a calibrated probability, and
not a causal estimate. Never say "this setup wins 61% of the time" or "there's a 70%
chance this hits." If you catch yourself about to output a bare percentage as if it were
a forecast, stop and reframe it as a rating tier with reasoning instead.

## The rating scale

Use a 5-tier qualitative scale, always paired with the reasoning that produced it:

| Tier | Label | Meaning |
|---|---|---|
| 5 | **Very High** | Multiple independent, non-redundant confluences aligned (HTF bias + fresh PDA + confirmed trigger + open draw + clean invalidation), and the corpus/general-ICT reasoning behind each leg is solid. |
| 4 | **High** | Core sequence is complete and clean, but missing one nice-to-have (e.g., no SMT confirmation, or trigger hasn't closed yet). |
| 3 | **Moderate** | Directionally coherent story exists but has a real gap — untested confirmation, ambiguous freshness, conflicting timeframe bias, or thin corpus support. |
| 2 | **Low** | Story is speculative — most of the sequence hasn't happened yet, or the setup leans on a single confluence in isolation. |
| 1 | **Very Low / Avoid** | Actively contradicted — off higher-timeframe bias, draw already delivered, mitigated PDA, session cutoff passed, or news/chop conditions the corpus flags as loss-associated. |

## Inputs to weigh for each scenario (Long / Short / No-Trade)

Work through these explicitly — don't just eyeball a number. This doubles as your
"sequence of events" checklist when building a setup idea.

1. **Higher-timeframe bias alignment.** Does 1D/4H/1H structure and the relevant draw
   on liquidity support this direction? A setup that fights HTF bias needs to clear a
   much higher bar (per the user's own directive: don't let stated bias override this).
2. **AMD phase read.** Has manipulation actually completed (a real sweep with
   displacement away from it), or is price still inside accumulation/still mid-sweep?
   Calling distribution before manipulation has resolved is a common way retail gets
   trapped — say so if you see it.
3. **Freshness of the PDA.** Is the RB/OB/FVG/breaker being used unmitigated and not
   already delivered-to? A technically valid but already-delivered or already-mitigated
   POI drops straight to tier 1-2 for that direction, per corpus invalidation findings.
4. **Confluence stack quality, not count.** Prefer non-redundant confluences (e.g., HTF
   bias + liquidity sweep + SMT + fresh FVG at OTE) over stacking near-synonyms.
   Cross-check against `references/ict_concept_glossary.md` for which confluences the
   corpus shows as genuinely differentiating (standard_deviation, draw_on_liquidity,
   breaker, cisd, order_block) versus near-universal/base-rate ones (fvg_ifvg, key_open,
   ote_fibonacci, smt_ssmt) that support a story but don't move confidence much alone.
5. **Trigger confirmation status.** Has the RB/CISD actually closed, or are you
   evaluating a forming candle? An unclosed trigger caps confidence at Moderate even if
   everything else lines up — the corpus repeatedly penalizes front-running an unclosed
   confirmation.
6. **Timing/session window.** Is this inside the session the setup is normally
   discussed in (e.g., 10AM model before the corpus-stated 11AM cutoff)? Outside the
   window doesn't make the pattern invalid, but it does mean you're off-model and should
   say so.
7. **Corpus-observed association, when the sample is adequate.** If you queried
   `confluence-stats` or a model card and the resolved n is reasonably sized (rule of
   thumb: treat n<15 as anecdotal, note it explicitly), you can let an above/below
   -baseline association nudge the tier — but always express it as "the corpus shows an
   above-baseline association here" not as a probability.
8. **Risk definition clarity.** Can invalidation be placed beyond real structure with a
   sane distance, or does defining risk require an arbitrary/awkward stop? Awkward risk
   placement is itself a downgrade, independent of directional conviction.
9. **No-trade specific checks.** Actively look for the corpus's catalogued low-quality
   filters: unclosed/front-run trigger, mitigated or exhausted block, HTF bias conflict,
   poor/random 1-minute shape, incomplete planned tap sequence, unresolved correlated
   (SMT) liquidity, news/chop conditions, arbitrary stop placement. Any one of these
   present is a reason to actively raise the No-Trade rating, independent of how the
   Long/Short ratings look — a valid-looking pattern with a live disqualifier is still a
   no-trade.

## Output format for a confidence rating

Always give the tier *and* the one-line reasoning, e.g.:

> **Long: Moderate (3/5).** HTF bias supports upside and the 10AM sweep + SMT
> confirmed manipulation, but the CISD candle hasn't closed yet — front-running it would
> cap this at Low. Re-rate to High once it closes above the rejection candle.
> **Short: Very Low (1/5).** Would require ignoring the completed bullish sweep and HTF
> draw; no supporting structure.
> **No-Trade: Moderate (3/5).** Legitimate reason to wait exists (unclosed trigger) but
> the setup is close enough that patience, not disengagement, is the right frame.

If the user's own stated bias disagrees with your independent read, say so plainly and
explain the specific confluence or invalidation reason your read differs on — that's the
whole point of this being a second brain rather than an echo. If their bias matches your
independent read, say that explicitly too, and use this framework to tighten their entry
(exact trigger, stop placement, target) rather than just rubber-stamping it.
