#!/usr/bin/env python3
"""Offline checks for live_market_data.py's data-shaping logic (resample,
bars_to_records, resolve_symbol) using a synthetic DataFrame shaped like
Databento's documented OHLCV to_df() output -- no network, no API key needed.

This validates that OUR post-processing code is correct. It does NOT validate
that Databento's real response actually has this shape; that still needs one
real authenticated call (see references/live_data_setup.md). If a real call
raises a KeyError from bars_to_records, the column names differ from what's
assumed here and in live_market_data.py -- fix both places together.

Run: python test_live_market_data_offline.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import live_market_data as lmd

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "ok" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def synthetic_minute_df(n: int = 120) -> pd.DataFrame:
    start = datetime(2026, 8, 17, 13, 30, tzinfo=timezone.utc)
    rows = []
    price = 20000.0
    for i in range(n):
        o = price
        h = o + 5
        l = o - 5
        c = o + (1 if i % 2 == 0 else -1)
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 100 + i})
        price = c
    idx = pd.date_range(start, periods=n, freq="1min")
    return pd.DataFrame(rows, index=idx)


def main() -> None:
    df = synthetic_minute_df(120)

    # resolve_symbol
    check("resolve_symbol root lookup", lmd.resolve_symbol("NQ", None) == ("NQ.c.0", "continuous"))
    check("resolve_symbol case-insensitive", lmd.resolve_symbol("nq", None) == ("NQ.c.0", "continuous"))
    check("resolve_symbol continuous passthrough", lmd.resolve_symbol("ES.c.0", None) == ("ES.c.0", "continuous"))
    check("resolve_symbol dotted default raw_symbol", lmd.resolve_symbol("ES.FUT", None) == ("ES.FUT", "raw_symbol"))
    check("resolve_symbol override wins", lmd.resolve_symbol("ES.FUT", "parent") == ("ES.FUT", "parent"))
    try:
        lmd.resolve_symbol("NOTAROOT", None)
        check("resolve_symbol unknown root raises", False, "did not raise")
    except SystemExit:
        check("resolve_symbol unknown root raises", True)

    # bars_to_records on the native shape
    records = lmd.bars_to_records(df.tail(5), "1m")
    check("bars_to_records row count", len(records) == 5)
    check("bars_to_records keys", set(records[0]) == {"time", "timeframe", "open", "high", "low", "close", "volume"})
    check("bars_to_records numeric types", isinstance(records[0]["close"], float) and isinstance(records[0]["volume"], int))
    check("bars_to_records chronological order", records[0]["time"] < records[-1]["time"])

    # resample 1m -> 5m/15m and 1h -> 4h
    five_min = lmd.resample(df, "5min")
    check("resample 5m bar count", len(five_min) == 120 // 5, f"got {len(five_min)}")
    check("resample 5m open==first source open", five_min.iloc[0]["open"] == df.iloc[0]["open"])
    check(
        "resample 5m high==max of source window",
        five_min.iloc[0]["high"] == df.iloc[0:5]["high"].max(),
    )
    check(
        "resample 5m volume==sum of source window",
        five_min.iloc[0]["volume"] == df.iloc[0:5]["volume"].sum(),
    )

    hourly_idx = pd.date_range(datetime(2026, 8, 11, tzinfo=timezone.utc), periods=48, freq="1h")
    hourly_df = pd.DataFrame(
        {"open": range(48), "high": [x + 2 for x in range(48)], "low": [x - 2 for x in range(48)],
         "close": [x + 1 for x in range(48)], "volume": [10] * 48},
        index=hourly_idx,
    )
    four_hour = lmd.resample(hourly_df, "4h")
    check("resample 4h bar count", len(four_hour) == 48 // 4, f"got {len(four_hour)}")

    # empty-df edge case (e.g. market closed / no data returned)
    empty = df.iloc[0:0]
    check("bars_to_records handles empty df", lmd.bars_to_records(empty, "1m") == [])

    # JSON-serializability of a full record (what actually gets printed to stdout)
    import json
    try:
        json.dumps({"bars": records})
        check("records are JSON-serializable", True)
    except TypeError as exc:
        check("records are JSON-serializable", False, str(exc))

    # load_data_file: format + column-name normalization for files another project
    # might actually produce (Yahoo-style capitalized OHLCV CSV, tick-shaped JSON
    # with only a timestamp+price, missing file).
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        bars_csv = tmp / "bars.csv"
        bars_csv.write_text(
            "Date,Open,High,Low,Close,Volume\n"
            "2026-08-17T13:30:00+00:00,100,105,95,101,10\n"
            "2026-08-17T13:31:00+00:00,101,106,96,100,11\n"
            "2026-08-17T13:32:00+00:00,100,105,95,102,12\n",
            encoding="utf-8",
        )
        df_csv = lmd.load_data_file(bars_csv)
        check(
            "load_data_file normalizes capitalized OHLCV CSV columns",
            list(df_csv.columns) == ["open", "high", "low", "close", "volume"] and len(df_csv) == 3,
            f"columns={list(df_csv.columns)} rows={len(df_csv)}",
        )

        ticks_json = tmp / "ticks.json"
        ticks_json.write_text(
            json.dumps([
                {"timestamp": "2026-08-17T13:30:00Z", "price": 100},
                {"timestamp": "2026-08-17T13:30:30Z", "price": 101},
                {"timestamp": "2026-08-17T13:31:00Z", "price": 99},
            ]),
            encoding="utf-8",
        )
        df_ticks = lmd.load_data_file(ticks_json)
        resampled_ticks = lmd.resample(df_ticks, "1min")
        check(
            "load_data_file + resample turns tick/price rows into OHLC bars",
            len(resampled_ticks) == 2
            and resampled_ticks.iloc[0]["open"] == 100
            and resampled_ticks.iloc[0]["high"] == 101,
            f"got {resampled_ticks.to_dict('records') if len(resampled_ticks) else 'empty'}",
        )

        try:
            lmd.load_data_file(tmp / "missing.csv")
            check("load_data_file missing file raises", False, "did not raise")
        except SystemExit:
            check("load_data_file missing file raises", True)

        unsupported = tmp / "unsupported.xlsx"
        unsupported.write_bytes(b"not really an xlsx, just needs to exist")
        try:
            lmd.load_data_file(unsupported)
            check("load_data_file unsupported extension raises", False, "did not raise")
        except SystemExit:
            check("load_data_file unsupported extension raises", True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
        sys.exit(1)
    print("All offline checks passed. Reminder: this does not confirm Databento's real "
          "response shape matches these assumptions -- validate one live call too.")


if __name__ == "__main__":
    main()
