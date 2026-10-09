#!/usr/bin/env python
"""Deep-dive on the two discrepancies found by verify_m5_audit.py."""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
AUDIT = ROOT.parent.parent / "audit" / "M5"
JSON_PATH = ROOT / "market_data" / "XAUUSD_M5.json"
sys.path.insert(0, str(ROOT))
from SP2L_Advanced_Backtest import Settings, prepare_data, run_backtest  # noqa: E402


def load_json(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    frame = pd.DataFrame(payload["candles"])
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame.set_index("time")


raw = load_json(JSON_PATH)
data = prepare_data(raw.copy())
settings = Settings(minimum_distance_points=25)
trades = run_backtest(data, settings, 5)
audit = pd.read_csv(AUDIT / "trades.csv")

print("=" * 78)
print("A. Is re-run == audit minus its first trade?")
print("=" * 78)


def key(t):
    return (str(t["direction"]), str(pd.Timestamp(t["setup_time"])),
            str(pd.Timestamp(t["entry_time"])), round(float(t["entry"]), 2),
            round(float(t["sl"]), 2), round(float(t["tp"]), 2), t["outcome"])


rk = [key(t) for t in trades]
ak = [key(r) for _, r in audit.iterrows()]
print(f"rerun[0:] == audit[1:] ? {rk == ak[1:]}")
if rk != ak[1:]:
    for i, (x, y) in enumerate(zip(rk, ak[1:])):
        if x != y:
            print(f"  diff at rerun#{i} vs audit#{i+1}:")
            print(f"    rerun: {x}")
            print(f"    audit: {y}")
            break
print(f"audit[0] (missing from rerun): {ak[0]}")

print()
print("=" * 78)
print("B. Warmup boundary explanation")
print("=" * 78)
hist = pd.read_csv(AUDIT / "history.csv")
hist["time"] = pd.to_datetime(hist["time"], utc=True)
hist = hist.set_index("time")
print(f"JSON  bar[499] = {data.index[499]}")
print(f"hist  bar[499] = {hist.index[499]}")
print(f"JSON  bar[0]   = {data.index[0]}")
print(f"hist  bar[0]   = {hist.index[0]}")
print("-> audit warmup ended earlier, so the 2026-07-30 12:10 signal was scanned there.")

print()
print("=" * 78)
print("C. Ambiguous trade: BUY entry=4622.24 sl=4619.68 tp=4624.80 @ 2026-08-21 22:00")
print("=" * 78)
ts = pd.Timestamp("2026-08-21 22:00:00", tz="UTC")
i = data.index.get_loc(ts)
print("candles around entry (entry bar is the row at 22:00):")
print(data.iloc[i - 3:i + 4][["open", "high", "low", "close", "spread"]].to_string())
bar = data.iloc[i]
spread = float(bar.spread) * settings.point
print(f"\nentry bar: open={bar.open} high={bar.high} low={bar.low} close={bar.close} "
      f"spread={bar.spread}pts ({spread})")
print(f"BUY sl=4619.68 tp=4624.80")
print(f"  low  <= sl ? {bar.low <= 4619.68}   (low={bar.low})")
print(f"  high >= tp ? {bar.high >= 4624.80}  (high={bar.high})")
print("-> both levels touched inside the same candle; OHLC cannot order them.")
print("   Backtest rule (newly_filled=True): SL wins unless close proves TP.")
print(f"   close={bar.close} >= tp? {bar.close >= 4624.80} -> audit outcome SL")

print()
print("=" * 78)
print("D. Signal status vs trade outcome consistency")
print("=" * 78)
sig = pd.read_csv(AUDIT / "signals.csv")
print("signals by status:")
print(sig["status"].value_counts().to_string())
print(f"\ntrades in trades.csv: {len(audit)}")
print(f"signals with status TP/SL: {int(sig['status'].isin(['TP','SL']).sum())}")
print(f"signals with status OPEN: {int((sig['status']=='OPEN').sum())}")
print(f"signals with status UNFILLED: {int((sig['status']=='UNFILLED').sum())}")
print("\n-> every TP/SL signal should appear as a trade.")
trade_sids = set(audit["signal_id"].astype(int))
outcome_sids = set(sig.loc[sig["status"].isin(["TP", "SL"]), "signal_id"].astype(int))
print(f"TP/SL signal ids not in trades: {sorted(outcome_sids - trade_sids)}")
print(f"trade signal ids not marked TP/SL: {sorted(trade_sids - outcome_sids)}")

print()
print("=" * 78)
print("E. Entry-price drift: initial_entry (signal) vs entry (trade)")
print("=" * 78)
merged = audit.merge(sig[["signal_id", "initial_entry", "initial_sl", "initial_tp"]],
                     on="signal_id", how="left")
merged["entry_drift"] = (merged["entry"] - merged["initial_entry"]).round(2)
print(f"trades where entry moved from the signal's initial entry: "
      f"{int((merged['entry_drift'] != 0).sum())} / {len(merged)}")
print(merged[["signal_id", "direction", "initial_entry", "entry", "entry_drift"]]
      .head(15).to_string(index=False))
