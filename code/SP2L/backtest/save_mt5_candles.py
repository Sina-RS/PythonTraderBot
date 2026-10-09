#!/usr/bin/env python
"""Save closed MetaTrader 5 candles for M1, M5 and M15 as JSON files."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd


TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
}


def save_candles(symbol, timeframe_name, timeframe, output_directory, count):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, count)

    if rates is None or len(rates) == 0:
        raise RuntimeError(
            f"No {timeframe_name} candle data returned for {symbol}: "
            f"{mt5.last_error()}"
        )

    data = pd.DataFrame(rates)
    if "time" not in data.columns:
        raise RuntimeError(
            f"MT5 returned {timeframe_name} data without a time column."
        )

    data["time"] = pd.to_datetime(data["time"], unit="s", utc=True)
    data.sort_values("time", inplace=True)

    payload = {
        "symbol": symbol,
        "timeframe": timeframe_name,
        "requested_candles": count,
        "returned_candles": len(data),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "candles": json.loads(
            data.to_json(
                orient="records",
                date_format="iso",
                date_unit="s",
                indent=2,
            )
        ),
    }

    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / f"{symbol}_{timeframe_name}.json"
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if len(data) < count:
        print(
            f"Warning: requested {count} {timeframe_name} candles, "
            f"but MT5 returned {len(data)}."
        )

    print(f"Saved {len(data)} {timeframe_name} candles to {output_path}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export closed MT5 candles to JSON."
    )
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "market_data",
    )
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be greater than zero.")
    return args


def main():
    args = parse_args()

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    try:
        if not mt5.symbol_select(args.symbol, True):
            raise RuntimeError(
                f"Could not select {args.symbol}: {mt5.last_error()}"
            )

        for timeframe_name, timeframe in TIMEFRAMES.items():
            save_candles(
                args.symbol,
                timeframe_name,
                timeframe,
                args.output_dir,
                args.count,
            )
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
