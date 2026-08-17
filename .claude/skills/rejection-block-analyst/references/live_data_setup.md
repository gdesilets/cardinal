# Live market data setup

`scripts/live_market_data.py` gets current/recent price data into this skill so it
can analyze the live market instead of waiting for the user to paste in levels.
Read this once when wiring it up or when a call fails unexpectedly; you don't
need it for routine use.

This skill doesn't own data acquisition. It has two independent ways to get
bars — pick whichever matches how market data actually arrives:

## Path 1: read a file another process writes (default, no API key needed)

If whatever project/process is scanning the market for live data writes its
output to disk — CSV, JSON, Parquet, or Databento's native DBN format — point
`read` at it:

```bash
pip install pandas   # or: pip install -r scripts/requirements.txt (installs databento too)
python scripts/live_market_data.py read /path/to/bars.csv --timeframe tda
```

No API key, no network call, no vendor coupling — just `pandas` for the file
parsing/resampling. Column names are matched
case-insensitively against common variants (`Open`/`open`/`o`, `Close`/`close`/
`price`/`last`, a timestamp column named `timestamp`/`time`/`date`/`ts_event`, ...
— see `COLUMN_ALIASES`/`TIME_COLUMN_CANDIDATES` in `live_market_data.py`). If the
source file only has trade-level price ticks (no open/high/low), each tick is
treated as its own micro-bar and resampled up to whatever `--timeframe` was
asked for — the same code path as bar-shaped input.

`--timeframe tda` reads the file once and returns the full 1D/4H/1H/15M/5M/1M
bundle the Top-Down Analysis template needs. Plain `--timeframe {1m,5m,15m,1h,4h,1d}`
returns just that one. Omit `--timeframe` to get the file's rows as-is.

If a real call raises a `KeyError` or "couldn't find a close/price column"
error, the source file's columns don't match what's assumed — add the actual
column name to `COLUMN_ALIASES`/`TIME_COLUMN_CANDIDATES` in `live_market_data.py`
rather than renaming the source file.

## Path 2: pull directly from Databento (optional, only if this skill fetches its own data)

If instead this skill needs to pull data itself rather than reading another
project's output, `quote <symbol>` / `bars <symbol>` / `tda <symbol>` call
Databento's Historical API directly:

```bash
pip install -r scripts/requirements.txt   # pandas + databento
export DATABENTO_API_KEY=db-...           # never hardcode this or pass it as a CLI arg
python scripts/live_market_data.py quote NQ
```

Built on closed OHLCV bars (`Historical.timeseries.get_range`), not raw L1 tick
streaming — this skill's own discipline is to wait for a candle to close before
treating structure as valid (see `references/ict_concept_glossary.md`'s "wait
for the close" rule), so a tick stream isn't the right primitive here regardless
of source. 1m/1h/1d bars come directly from Databento; 5m/15m/4h are resampled
locally with pandas (Databento's native OHLCV schemas only cover 1s/1m/1h/1d/eod).
Root symbols (`NQ`, `ES`, `MNQ`, ...) auto-resolve to the CME Globex continuous
front-month contract on dataset `GLBX.MDP3`; pass `--stype-in`/`--dataset` to
override for a specific contract, a different venue, or equities.

**Untested against a live account.** Built to Databento's documented API and
verified against the installed SDK's method signatures, but there are no
Databento credentials in this repo — the request-construction path was
confirmed correct (a placeholder key reaches Databento's servers and fails
cleanly at the 401 auth step, not before), but no real response has ever been
parsed. If a symbol/entitlement error comes back once you have a real key: a
pure L1 real-time subscription may not include historical OHLCV entitlement on
its own — check your Databento portal if `bars`/`tda` fail while `quote` (also
OHLCV-based) fails identically the same way, that's a plan/entitlement gap, not
a bug in this script.

## Either path

All errors come back as clean `{"error": "..."}` JSON on exit code 1, never a
raw traceback. `python scripts/test_live_market_data_offline.py` regression-checks
the data-shaping logic (column normalization, resampling, tick-to-bar aggregation)
against synthetic data with no network/credentials needed — run it after editing
`live_market_data.py`.
