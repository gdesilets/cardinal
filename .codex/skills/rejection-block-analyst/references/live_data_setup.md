# Live market data setup (Databento)

`scripts/live_market_data.py` pulls current/recent price data from Databento so
this skill can analyze the live market instead of waiting for the user to paste in
levels. Read this once when setting it up or when a call fails unexpectedly; you
don't need it for routine use.

## One-time setup

```bash
pip install -r scripts/requirements.txt   # just databento
export DATABENTO_API_KEY=db-...           # never hardcode this or pass it as a CLI arg
```

Confirm it works: `python scripts/live_market_data.py quote NQ` should return a
JSON object with `last_close` and `as_of`. A `401 auth_authentication_failed`
error means the key is missing/wrong; any other `BentoClientError` (all errors
come back as clean `{"error": "..."}` JSON, never a raw traceback) usually means
a symbol, dataset, or entitlement problem -- see below.

## Why bars, not a tick stream

This skill's own discipline is to wait for a candle to close before treating
structure as valid (see `references/ict_concept_glossary.md`'s "wait for the
close" rule). A raw L1 top-of-book tick isn't the right input for that -- closed
OHLCV bars are. So `live_market_data.py` is built entirely on Databento's
**Historical** API (`Historical.timeseries.get_range`), not the streaming `Live`
client:

- `quote <symbol>` -- the close of the most recently completed 1-minute bar.
- `bars <symbol> --timeframe {1m,5m,15m,1h,4h,1d} --lookback N` -- closed bars.
  1m/1h/1d come directly from Databento; 5m/15m/4h are resampled locally from 1m/1h
  with pandas, since Databento's native OHLCV schemas only cover 1s/1m/1h/1d/eod.
- `tda <symbol>` -- one call that returns the full 1D/4H/1H/15M/5M/1M bundle the
  Top-Down Analysis output template needs, in 3 HTTP requests (daily, hourly,
  minute) instead of six separate ones. Use this one for anything TDA-shaped
  rather than calling `bars` six times.

This also means it's a plain stateless HTTP request per call -- no persistent
connection to manage, no snapshot/timeout handling, fast and simple to reason
about, which matters for an agent that's invoked fresh per analysis request.

## Symbols

Root symbols (`NQ`, `ES`, `MNQ`, `MES`, `YM`, `MYM`, `RTY`, `M2K`, `GC`, `CL`) auto-resolve
to the CME Globex continuous front-month contract (`NQ` -> `NQ.c.0`) on dataset
`GLBX.MDP3`. For anything else -- a specific contract month, a different venue,
equities -- pass the exact Databento symbol yourself (it's used as-is once it
contains a `.`) and set `--dataset` (e.g. `XNAS.ITCH` for Nasdaq equities).

## Untested against a live account

This integration is built to Databento's documented Historical API and verified
against the installed `databento` SDK's actual method signatures (parameter names,
schema/stype enum values) -- but there are no Databento credentials in this repo,
so it has never made a real authenticated call. The request-construction path was
confirmed correct (a call with a placeholder key reaches Databento's servers and
fails cleanly at the 401 auth step, not before). Validate the first real call
against your subscription and adjust `bars_to_records()`'s column access in
`live_market_data.py` if your plan's `to_df()` output shapes differently than
expected (this would surface as a `KeyError` in the JSON error output, not a
silent wrong answer).

## If a symbol/entitlement error comes back

- Confirm the root is in `CONTINUOUS_ROOTS` in `live_market_data.py`, or pass an
  explicit symbol.
- Confirm your plan is entitled to `GLBX.MDP3` (or whichever `--dataset` you're
  using) for the schemas being requested (`ohlcv-1m`, `ohlcv-1h`, `ohlcv-1d`).
  A pure L1 real-time subscription may not include historical OHLCV on its own --
  check your Databento portal's entitlements if `bars`/`tda` calls fail while
  `quote` (also OHLCV-based) fails identically, that's an entitlement gap, not a
  bug in this script.
