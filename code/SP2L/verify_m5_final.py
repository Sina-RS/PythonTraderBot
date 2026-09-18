#!/usr/bin/env python
"""Final checks: JSON-only tail region and full signal-list comparison."""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
AUDIT = ROOT.parent.parent / "audit" / "M5"
JSON_PATH = ROOT / "market_data" / "XAUUSD_M5.json"
sys.path.insert(0, str(ROOT))
from SP2L_Advanced_Backtest import Settings, prepare_data, run_backtest  # noqa: E402
from SP2L_Audit import SignalAudit  # noqa: E402


def load_json(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    frame = pd.DataFrame(payload["candles"])
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame.set_index("time")


raw = load_json(JSON_PATH)
data = prepare_data(raw.copy())
settings = Settings(minimum_distance_points=25)

print("=" * 78)
print("A. Fresh run WITH audit on JSON -> compare signal list to audit signals.csv")
print("=" * 78)
audit = SignalAudit()
trades = run_backtest(data, settings, 5, audit=audit)
fresh = pd.DataFrame(audit.signals)
old = pd.read_csv(AUDIT / "signals.csv")
print(f"fresh signals: {len(fresh)}   audit signals: {len(old)}")

fresh["signal_bar_utc"] = pd.to_datetime(fresh["signal_bar_utc"], utc=True)
old["signal_bar_utc"] = pd.to_datetime(old["signal_bar_utc"], utc=True)
fk = list(zip(fresh["direction"], fresh["signal_bar_utc"].astype(str)))
ok = list(zip(old["direction"], old["signal_bar_utc"].astype(str)))
print(f"fresh == audit[1:] ? {fk == ok[1:]}")
print(f"audit[0] (only in audit): {ok[0]}")
print(f"fresh-only signals: {sorted(set(fk) - set(ok))}")
print(f"audit-only signals: {sorted(set(ok) - set(fk))}")

print()
print("=" * 78)
print("B. JSON-only tail region (after audit's last bar 2026-09-17 01:00)")
print("=" * 78)
tail_start = pd.Timestamp("2026-09-17 01:00:00", tz="UTC")
tail = data.loc[data.index > tail_start]
print(f"JSON-only tail bars: {len(tail)}  ({tail.index[0]} -> {tail.index[-1]})")
# any signals detected in the tail?
tail_sigs = [s for s in audit.signals
             if pd.Timestamp(s["signal_bar_utc"]) > tail_start]
print(f"signals detected in tail: {len(tail_sigs)}")
for s in tail_sigs:
    print(f"  #{s['signal_id']} {s['direction']} {s['signal_bar_utc']} "
          f"entry={s['initial_entry']} sl={s['initial_sl']} tp={s['initial_tp']}")

print()
print("=" * 78)
print("C. JSON-only head region (before audit's first bar 2026-07-28 16:15)")
print("=" * 78)
head_end = pd.Timestamp("2026-07-28 16:15:00", tz="UTC")
head = data.loc[data.index < head_end]
print(f"JSON bars before audit start: {len(head)}")
print("(JSON starts later than history.csv, so this is empty by construction)")

print()
print("=" * 78)
print("D. Final verdict numbers")
print("=" * 78)
tp = sum(t["outcome"] == "TP" for t in trades)
sl = sum(t["outcome"] == "SL" for t in trades)
print(f"fresh run on JSON: trades={len(trades)} TP={tp} SL={sl} "
      f"win_rate={100*tp/(tp+sl):.2f}%")
print(f"audit (history.csv): trades=49 TP=24 SL=25 win_rate=48.98%")
