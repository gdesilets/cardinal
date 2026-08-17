#!/usr/bin/env python3
"""Market data reader for fast intraday ICT analysis -- source-agnostic.

This skill doesn't own data acquisition; whatever project feeds it live market
data (a scraper, a broker connection, a scheduled export) does. This script has
two independent ways to get bars, pick whichever matches how that project
actually delivers data:

  1. `read <path>` -- parse a local CSV/JSON/Parquet file of OHLCV bars (or raw
     trades, resampled locally) that the other project writes to disk. No API
     key, no network call, no vendor coupling -- point it at whatever file shows
     up. THIS IS THE DEFAULT PATH if you don't know yet how data will arrive.
  2. `quote` / `bars` / `tda <symbol>` -- fetch directly from Databento's
     Historical API, if this skill ends up being the one pulling data itself
     rather than reading another project's output. Needs `pip install databento`
     and a DATABENTO_API_KEY; see references/live_data_setup.md.

Both paths return the same JSON bar-record shape, and both are deliberately
built around CLOSED bars, not raw ticks -- this skill's own methodology waits
for a candle to close before treating structure as valid ("don't front-run an
unclosed candle", see references/ict_concept_glossary.md), so closed OHLCV is
the right primitive regardless of source. `tda` bundles every timeframe the
Top-Down Analysis template needs (1D/4H/1H/15M/5M/1M) in one call.

Usage:
  python live_market_data.py read /path/to/bars.csv --timeframe 15m
  python live_market_data.py quote NQ
  python live_market_data.py bars NQ --timeframe 1h --lookback 20
  python live_market_data.py tda NQ

Symbols for the Databento path default to the CME Globex continuous front-month
contract via a small lookup table (NQ -> NQ.c.0, ES -> ES.c.0, etc.) on dataset
GLBX.MDP3. Pass a symbol that already contains a '.' to use it as-is.

The Databento path is built against databento-python's documented Historical API
and verified against the installed SDK's method signatures, but NOT tested
against a live account -- this repo has no Databento credentials. Validate the
first real call yourself; see references/live_data_setup.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_DATASET = "GLBX.MDP3"

# Root symbol -> continuous front-month contract. Extend as needed; anything
# containing '.' is passed through untouched (assume the caller already knows
# the exact symbol/contract they want).
CONTINUOUS_ROOTS = {
    "NQ": "NQ.c.0", "ES": "ES.c.0", "MNQ": "MNQ.c.0", "MES": "MES.c.0",
    "YM": "YM.c.0", "MYM": "MYM.c.0", "RTY": "RTY.c.0", "M2K": "M2K.c.0",
    "GC": "GC.c.0", "CL": "CL.c.0",
}

NATIVE_TIMEFRAMES = {"1m": "ohlcv-1m", "1h": "ohlcv-1h", "1d": "ohlcv-1d"}
# Resampled from a native timeframe via pandas: (source_timeframe, pandas_rule)
RESAMPLED_TIMEFRAMES = {"5m": ("1m", "5min"), "15m": ("1m", "15min"), "4h": ("1h", "4h")}
ALL_TIMEFRAMES = sorted({*NATIVE_TIMEFRAMES, *RESAMPLED_TIMEFRAMES})


CONTINUOUS_PATTERN = re.compile(r"^[A-Z0-9]+\.c\.\d+$", re.IGNORECASE)


def resolve_symbol(symbol: str, stype_in_override: str | None) -> tuple[str, str]:
    """Returns (resolved_symbol, stype_in). Explicit --stype-in always wins; otherwise
    a bare root (NQ, ES, ...) resolves via CONTINUOUS_ROOTS, an explicit `X.c.N`
    continuous-contract symbol is detected automatically, and anything else dotted
    (e.g. a parent symbol like ES.FUT, or a specific contract code) is passed through
    as raw_symbol -- pass --stype-in parent/raw_symbol/... explicitly if that's wrong
    for what you're asking for."""
    if stype_in_override:
        return symbol, stype_in_override
    if CONTINUOUS_PATTERN.match(symbol):
        return symbol, "continuous"
    if "." not in symbol:
        root = symbol.upper()
        if root in CONTINUOUS_ROOTS:
            return CONTINUOUS_ROOTS[root], "continuous"
        raise SystemExit(
            f"Unknown root symbol {symbol!r}; not in {sorted(CONTINUOUS_ROOTS)}. "
            "Pass an explicit symbol (e.g. ES.c.0) or --stype-in to control resolution."
        )
    return symbol, "raw_symbol"


def get_client():
    try:
        import databento as db
    except ImportError:
        raise SystemExit(
            "The 'databento' package isn't installed. Run: pip install databento"
        )
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        raise SystemExit(
            "DATABENTO_API_KEY is not set. Export it in your environment first -- "
            "see references/live_data_setup.md. Never pass API keys as CLI arguments."
        )
    return db.Historical(key)


def fetch_bars(client, dataset: str, symbol: str, stype_in: str, schema: str, lookback: int):
    end = datetime.now(timezone.utc)
    # Generous lookback window so `lookback` closed bars are available even across
    # weekends/holidays/session gaps; trimmed to the last `lookback` rows below.
    span_days = {"ohlcv-1m": 3, "ohlcv-1h": 30, "ohlcv-1d": 400}[schema]
    start = end - timedelta(days=span_days)
    store = client.timeseries.get_range(
        dataset=dataset, symbols=symbol, schema=schema,
        stype_in=stype_in, start=start, end=end,
    )
    df = store.to_df(price_type="float", pretty_ts=True)
    return df.tail(lookback)


def bars_to_records(df, timeframe: str) -> list[dict]:
    records = []
    for ts, row in df.iterrows():
        records.append({
            "time": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "timeframe": timeframe,
            "open": round(float(row["open"]), 4),
            "high": round(float(row["high"]), 4),
            "low": round(float(row["low"]), 4),
            "close": round(float(row["close"]), 4),
            "volume": int(row["volume"]) if "volume" in row else None,
        })
    return records


def resample(df, rule: str):
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    agg = {k: v for k, v in agg.items() if k in df.columns}
    out = df.resample(rule).agg(agg).dropna(subset=["open"])
    return out


# --- local-file reading: format-agnostic, column-name-agnostic ---

TIME_COLUMN_CANDIDATES = ["timestamp", "time", "datetime", "date", "ts_event", "ts", "t"]
COLUMN_ALIASES = {
    "open": ["open", "o"],
    "high": ["high", "h"],
    "low": ["low", "l"],
    "close": ["close", "c", "price", "last", "last_price"],
    "volume": ["volume", "vol", "v", "size", "qty", "quantity"],
}


def load_data_file(path):
    import pandas as pd

    p = __import__("pathlib").Path(path)
    if not p.exists():
        raise SystemExit(f"File not found: {p}")
    suffix = p.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(p)
    elif suffix == ".json":
        df = pd.read_json(p)
    elif suffix in (".parquet", ".pq"):
        df = pd.read_parquet(p)
    elif suffix == ".dbn" or suffix.endswith(".dbn.zst"):
        import databento as db
        df = db.DBNStore.from_file(p).to_df(price_type="float", pretty_ts=True)
        return df  # DBN files already have a proper datetime index and OHLC/price columns.
    else:
        raise SystemExit(
            f"Unrecognized file type {suffix!r} for {p}. Supported: .csv, .json, .parquet, .dbn"
        )

    lower_cols = {c.lower(): c for c in df.columns}
    time_col = next((lower_cols[c] for c in TIME_COLUMN_CANDIDATES if c in lower_cols), None)
    if time_col is None:
        raise SystemExit(
            f"Couldn't find a timestamp column in {p} (tried {TIME_COLUMN_CANDIDATES}); "
            f"columns present: {list(df.columns)}"
        )
    df[time_col] = pd.to_datetime(df[time_col], utc=True)
    df = df.set_index(time_col).sort_index()

    resolved = {}
    for field, aliases in COLUMN_ALIASES.items():
        match = next((lower_cols[a] for a in aliases if a in lower_cols), None)
        if match:
            resolved[field] = match
    if "close" not in resolved:
        raise SystemExit(
            f"Couldn't find a close/price column in {p} (tried {COLUMN_ALIASES['close']}); "
            f"columns present: {list(df.columns)}"
        )
    df = df.rename(columns={v: k for k, v in resolved.items()})
    # Trade-level data (only a price, no OHLC): treat each row as its own micro-bar so
    # the same resample() path works for both bars-shaped and ticks-shaped input.
    for field in ("open", "high", "low"):
        if field not in df.columns:
            df[field] = df["close"]
    if "volume" not in df.columns:
        df["volume"] = 1
    return df[["open", "high", "low", "close", "volume"]]


RESAMPLE_RULES = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}


def cmd_read(args):
    df = load_data_file(args.path)

    if args.timeframe == "tda":
        # One file load, every timeframe the Top-Down Analysis template needs --
        # cheaper than re-reading the file per timeframe.
        bundle = {
            "source": str(args.path),
            "as_of": datetime.now(timezone.utc).isoformat(),
            **{
                tf: bars_to_records(resample(df, rule).tail(args.lookback or 40), tf)
                for tf, rule in RESAMPLE_RULES.items()
            },
            "note": "Bars resampled locally from the source file; last row of each timeframe is its most recently closed bar in the file, not necessarily 'now'.",
        }
        print(json.dumps(bundle, indent=2))
        return

    if args.timeframe:
        df = resample(df, RESAMPLE_RULES[args.timeframe])
    if args.lookback:
        df = df.tail(args.lookback)
    records = bars_to_records(df, args.timeframe or "raw")
    print(json.dumps({"source": str(args.path), "timeframe": args.timeframe or "raw (as read from file)", "bars": records}, indent=2))


def cmd_quote(args):
    client = get_client()
    symbol, stype_in = resolve_symbol(args.symbol, args.stype_in)
    df = fetch_bars(client, args.dataset, symbol, stype_in, "ohlcv-1m", lookback=1)
    if df.empty:
        print(json.dumps({"error": "No recent bars returned; market may be closed."}))
        return
    last = df.iloc[-1]
    ts = df.index[-1]
    print(json.dumps({
        "symbol": symbol,
        "as_of": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
        "last_close": round(float(last["close"]), 4),
        "note": (
            "Price is the close of the most recently completed 1-minute bar, not a raw "
            "tick -- consistent with this skill's own 'wait for the close' discipline."
        ),
    }, indent=2))


def cmd_bars(args):
    client = get_client()
    symbol, stype_in = resolve_symbol(args.symbol, args.stype_in)
    if args.timeframe in NATIVE_TIMEFRAMES:
        df = fetch_bars(client, args.dataset, symbol, stype_in, NATIVE_TIMEFRAMES[args.timeframe], args.lookback)
        records = bars_to_records(df, args.timeframe)
    else:
        source_tf, rule = RESAMPLED_TIMEFRAMES[args.timeframe]
        source_schema = NATIVE_TIMEFRAMES[source_tf]
        # Fetch extra source-timeframe history so resampling yields `lookback` complete bars.
        multiplier = {"5min": 5, "15min": 15, "4h": 4}[rule]
        df = fetch_bars(client, args.dataset, symbol, stype_in, source_schema, args.lookback * multiplier + multiplier)
        df = resample(df, rule).tail(args.lookback)
        records = bars_to_records(df, args.timeframe)
    print(json.dumps({"symbol": symbol, "timeframe": args.timeframe, "bars": records}, indent=2))


def cmd_tda(args):
    """One-shot bundle covering every timeframe the Top-Down Analysis template needs."""
    client = get_client()
    symbol, stype_in = resolve_symbol(args.symbol, args.stype_in)

    daily = fetch_bars(client, args.dataset, symbol, stype_in, "ohlcv-1d", lookback=10)
    hourly = fetch_bars(client, args.dataset, symbol, stype_in, "ohlcv-1h", lookback=120)
    minute = fetch_bars(client, args.dataset, symbol, stype_in, "ohlcv-1m", lookback=600)

    four_hour = resample(hourly, "4h").tail(20)
    fifteen_min = resample(minute, "15min").tail(40)
    five_min = resample(minute, "5min").tail(60)
    one_min = minute.tail(30)

    bundle = {
        "symbol": symbol,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "1d": bars_to_records(daily, "1d"),
        "4h": bars_to_records(four_hour, "4h"),
        "1h": bars_to_records(hourly.tail(40), "1h"),
        "15m": bars_to_records(fifteen_min, "15m"),
        "5m": bars_to_records(five_min, "5m"),
        "1m": bars_to_records(one_min, "1m"),
        "note": (
            "All bars are closed candles as of the request time; the last row of '1m' "
            "is the most recently completed minute, not an in-progress candle."
        ),
    }
    print(json.dumps(bundle, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help=f"Databento dataset (default: {DEFAULT_DATASET})")
    parser.add_argument(
        "--stype-in", default=None,
        help="Override symbology resolution (e.g. 'parent' for ES.FUT, 'raw_symbol' for an exact contract code). "
             "Default: auto-detect from the symbol shape.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("read", help="Parse local bars/ticks from a CSV/JSON/Parquet/DBN file -- no API key needed")
    p.add_argument("path", help="Path to the file another project writes/updates")
    p.add_argument(
        "--timeframe", choices=[*RESAMPLE_RULES, "tda"], default=None,
        help="Resample to this timeframe, or 'tda' for the full 1D/4H/1H/15M/5M/1M bundle in one read. "
             "Omit to return the file's rows as-is.",
    )
    p.add_argument("--lookback", type=int, default=None, help="Trim to the last N bars (per timeframe, if --timeframe tda)")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("quote", help="[Databento] Most recently closed 1-minute bar's close price")
    p.add_argument("symbol", help="Root symbol (NQ, ES, ...) or an explicit Databento symbol")
    p.set_defaults(func=cmd_quote)

    p = sub.add_parser("bars", help="[Databento] Recent closed bars at one timeframe")
    p.add_argument("symbol")
    p.add_argument("--timeframe", choices=ALL_TIMEFRAMES, default="1m")
    p.add_argument("--lookback", type=int, default=30, help="Number of closed bars to return")
    p.set_defaults(func=cmd_bars)

    p = sub.add_parser("tda", help="[Databento] One-shot multi-timeframe bundle for the Top-Down Analysis template")
    p.add_argument("symbol")
    p.set_defaults(func=cmd_tda)

    args = parser.parse_args()
    try:
        args.func(args)
    except ImportError:
        raise
    except SystemExit:
        raise
    except Exception as exc:  # Databento's BentoError family, network errors, etc.
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
