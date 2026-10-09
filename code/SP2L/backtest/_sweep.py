#!/usr/bin/env python
"""Parameter sweep harness for SP2L backtest over saved JSON market data."""
import itertools
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from SP2L_Advanced_Backtest import Settings, prepare_data, run_backtest, summarize  # noqa: E402


def load_json(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    frame = pd.DataFrame(payload["candles"])
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame.set_index("time")


DATA = {label: prepare_data(load_json(ROOT / "market_data" / f"XAUUSD_{label}.json").copy())
        for label in ("M1", "M5", "M15")}


def evaluate(settings, labels=("M1", "M5", "M15")):
    out = {}
    for label in labels:
        trades = run_backtest(DATA[label], settings, int(label[1:]))
        out[label] = summarize(trades)
    return out


def fmt(result, tp_r):
    parts = []
    for label in ("M1", "M5", "M15"):
        r = result[label]
        rate = "N/A" if r["win_rate"] is None else f"{r['win_rate']:.1f}"
        closed = r["tp"] + r["sl"]
        expectancy = ((r["tp"] * tp_r - r["sl"]) / closed
                      if closed else None)
        exp = "N/A" if expectancy is None else f"{expectancy:+.2f}R"
        parts.append(f"{label}={rate}({r['tp']}/{r['sl']},E={exp})")
    return " ".join(parts)


def sweep(grid, base=None, labels=("M1", "M5", "M15")):
    base = base or Settings(minimum_distance_points=25)
    keys = list(grid)
    rows = []
    for values in itertools.product(*(grid[k] for k in keys)):
        overrides = dict(zip(keys, values))
        settings = Settings(**{**base.__dict__, **overrides})
        result = evaluate(settings, labels)
        closed = [result[l]["tp"] + result[l]["sl"] for l in labels]
        if any(n == 0 for n in closed):
            continue
        expectancies = [
            (result[l]["tp"] * settings.tp_r - result[l]["sl"]) / (result[l]["tp"] + result[l]["sl"])
            for l in labels
        ]
        total_r = sum(result[l]["tp"] * settings.tp_r - result[l]["sl"] for l in labels)
        rows.append((min(expectancies), total_r, min(closed), overrides, result))
    rows.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    return rows


if __name__ == "__main__":
    grid = {
        "pgap_points": [150, 175, 200, 225, 250],
        "spike_candle_size": [0.75, 1.0, 1.25, 1.5],
        "tp_r": [1.0, 1.25, 1.5, 2.0, 3.0],
        "max_opposite_moves": [1, 2],
    }
    rows = sweep(grid)
    lines = [
        f"minE={min_exp:+.3f} totalR={total_r:+.1f} minN={min_trades} "
        f"{overrides} | {fmt(result, overrides['tp_r'])}"
        for min_exp, total_r, min_trades, overrides, result in rows
    ]
    (ROOT / "_sweep_out.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))