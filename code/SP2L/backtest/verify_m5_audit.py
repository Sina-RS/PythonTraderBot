#!/usr/bin/env python
"""Independent verification of the M5 SP2L audit against real market data.

Checks:
  1. XAUUSD_M5.json vs audit/M5/history.csv (same candles?)
  2. Re-run the backtest on the JSON data and diff against audit trades.csv
  3. Independently re-derive every trade outcome from raw candles
  4. Independently re-derive every signal's entry/SL/TP from raw candles
  5. Report look-ahead / logic concerns
"""
import json
import sys
from pathlib import Path

import numpy as np
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
    frame = frame.set_index("time")
    return payload, frame


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main():
    payload, raw = load_json(JSON_PATH)
    print(f"JSON: {payload['symbol']} {payload['timeframe']} "
          f"requested={payload['requested_candles']} returned={payload['returned_candles']} "
          f"rows={len(raw)}")
    print(f"JSON range: {raw.index[0]} -> {raw.index[-1]}")

    hist = pd.read_csv(AUDIT / "history.csv")
    hist["time"] = pd.to_datetime(hist["time"], utc=True)
    hist = hist.set_index("time")
    print(f"history.csv rows={len(hist)} range={hist.index[0]} -> {hist.index[-1]}")

    # ---------------------------------------------------------------- 1
    section("1. DATA INTEGRITY: JSON vs history.csv")
    cols = ["open", "high", "low", "close", "spread"]
    common = raw.index.intersection(hist.index)
    print(f"common timestamps: {len(common)}")
    if len(common) == 0:
        print("!! No overlapping timestamps at all.")
    else:
        a = raw.loc[common, cols].astype(float)
        b = hist.loc[common, cols].astype(float)
        diff = (a - b).abs()
        print("max abs diff per column:")
        print(diff.max().to_string())
        mism = (diff > 1e-9).any(axis=1)
        print(f"rows with any OHLC/spread mismatch: {int(mism.sum())}")
        if mism.any():
            print(a[mism].head(10).to_string())
            print(b[mism].head(10).to_string())
    only_json = raw.index.difference(hist.index)
    only_hist = hist.index.difference(raw.index)
    print(f"only in JSON: {len(only_json)}  only in history.csv: {len(only_hist)}")
    if len(only_json):
        print("  JSON-only first/last:", only_json[0], only_json[-1])
    if len(only_hist):
        print("  hist-only first/last:", only_hist[0], only_hist[-1])

    # ---------------------------------------------------------------- 2
    section("2. RE-RUN BACKTEST ON JSON DATA vs audit trades.csv")
    settings = Settings(minimum_distance_points=25)
    data = prepare_data(raw.copy())
    trades = run_backtest(data, settings, 5)
    print(f"re-run trades: {len(trades)}")

    audit_trades = pd.read_csv(AUDIT / "trades.csv")
    print(f"audit trades: {len(audit_trades)}")

    def key(t):
        return (str(t["direction"]), str(pd.Timestamp(t["setup_time"])),
                str(pd.Timestamp(t["entry_time"])), round(float(t["entry"]), 2),
                round(float(t["sl"]), 2), round(float(t["tp"]), 2), t["outcome"])

    rerun_keys = [key(t) for t in trades]
    audit_keys = [key(r) for _, r in audit_trades.iterrows()]
    print(f"identical trade sequences: {rerun_keys == audit_keys}")
    if rerun_keys != audit_keys:
        for i, (x, y) in enumerate(zip(rerun_keys, audit_keys)):
            if x != y:
                print(f"  first diff at #{i}:")
                print(f"    rerun: {x}")
                print(f"    audit: {y}")
                break
        print(f"  lengths: rerun={len(rerun_keys)} audit={len(audit_keys)}")

    # ---------------------------------------------------------------- 3
    section("3. INDEPENDENT OUTCOME RE-DERIVATION (raw candles)")
    # For each trade, walk candles from entry_time forward and decide TP/SL
    # using the same conservative rule (SL first on ambiguous bars).
    idx = data.index
    pos_of = {ts: i for i, ts in enumerate(idx)}
    bad = 0
    for t in trades:
        et = pd.Timestamp(t["entry_time"])
        i0 = pos_of.get(et)
        if i0 is None:
            print(f"  !! entry_time not in data: {et}")
            bad += 1
            continue
        buy = t["direction"] == "BUY"
        sl, tp = float(t["sl"]), float(t["tp"])
        outcome = None
        exit_i = None
        for i in range(i0, len(data)):
            bar = data.iloc[i]
            spread = float(bar.spread) * settings.point
            off = 0 if buy else spread
            hi, lo = float(bar.high) + off, float(bar.low) + off
            if (lo <= sl) if buy else (hi >= sl):
                outcome, exit_i = "SL", i
                break
            if (hi >= tp) if buy else (lo <= tp):
                outcome, exit_i = "TP", i
                break
        if outcome != t["outcome"]:
            print(f"  MISMATCH {t['direction']} entry={t['entry']} sl={sl} tp={tp} "
                  f"entry_time={et} audit={t['outcome']} independent={outcome}")
            bad += 1
    print(f"outcome mismatches: {bad} / {len(trades)}")

    # ---------------------------------------------------------------- 4
    section("4. INDEPENDENT SIGNAL RE-DERIVATION (raw candles)")
    # Rebuild the setup detection directly from raw candles for each signal.
    sig = pd.read_csv(AUDIT / "signals.csv")
    sig["signal_bar_utc"] = pd.to_datetime(sig["signal_bar_utc"], utc=True)
    spike = settings.spike_candle_size
    pgap = settings.pgap_points * settings.point
    mism = 0
    for _, s in sig.iterrows():
        ts = s["signal_bar_utc"]
        if ts not in pos_of:
            print(f"  !! signal bar not in data: {ts}")
            mism += 1
            continue
        i = pos_of[ts]
        if i < 4:
            print(f"  !! not enough history for signal {s['signal_id']}")
            mism += 1
            continue
        # window ends at signal bar (index -1 == ts)
        w = data.iloc[i - 4:i + 1]
        o, h, l, c = (w[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        # indices: 0=context(-4),1=before_spike(-3),2=spike(-2),3=after_spike(-1)
        # NOTE: bot uses -1=latest, -2=after spike, -3=spike, -4=before spike
        # so with window of 5 ending at ts: -1=w[4], -2=w[3], -3=w[2], -4=w[1]
        buy = (
            c[3] > c[2] and o[3] > o[2] and c[2] > c[1] and o[2] > o[1]
            and c[3] > o[3] and c[2] > o[2] and c[1] > o[1]
            and l[3] > h[1] + pgap
            and (c[2] - o[2]) > spike * (c[3] - o[3])
            and (c[2] - o[2]) > spike * (c[1] - o[1])
            and (c[2] - o[2]) > spike * (c[4] - o[4])
        )
        sell = (
            c[3] < c[2] and o[3] < o[2] and c[2] < c[1] and o[2] < o[1]
            and c[3] < o[3] and c[2] < o[2] and c[1] < o[1]
            and h[3] < l[1] - pgap
            and (o[2] - c[2]) > spike * (o[3] - c[3])
            and (o[2] - c[2]) > spike * (o[1] - c[1])
            and (o[2] - c[2]) > spike * (o[4] - c[4])
        )
        detected = "BUY" if buy else ("SELL" if sell else None)
        if detected != s["direction"]:
            print(f"  MISMATCH signal #{s['signal_id']} at {ts}: "
                  f"audit={s['direction']} independent={detected}")
            mism += 1
            continue
        # entry / sl
        if detected == "BUY":
            entry, sl = l[3], l[1]
        else:
            entry, sl = h[3], h[1]
        if abs(entry - float(s["initial_entry"])) > 1e-6 or abs(sl - float(s["initial_sl"])) > 1e-6:
            print(f"  ENTRY/SL MISMATCH #{s['signal_id']}: "
                  f"audit=({s['initial_entry']},{s['initial_sl']}) indep=({entry},{sl})")
            mism += 1
    print(f"signal mismatches: {mism} / {len(sig)}")

    # ---------------------------------------------------------------- 5
    section("5. SIGNAL CANDLE CROSS-CHECK (signal_candles.csv vs raw)")
    sc = pd.read_csv(AUDIT / "signal_candles.csv")
    sc["bar_utc"] = pd.to_datetime(sc["bar_utc"], utc=True)
    bad = 0
    for _, r in sc.iterrows():
        ts = r["bar_utc"]
        if ts not in pos_of:
            bad += 1
            continue
        row = data.loc[ts]
        for col in ("open", "high", "low", "close", "spread"):
            if abs(float(row[col]) - float(r[col])) > 1e-6:
                print(f"  candle mismatch #{r['signal_id']} {r['role']} {ts} {col}: "
                      f"audit={r[col]} raw={row[col]}")
                bad += 1
    print(f"signal-candle mismatches: {bad} / {len(sc)}")

    # ---------------------------------------------------------------- 6
    section("6. SUMMARY STATS")
    tp = sum(t["outcome"] == "TP" for t in trades)
    sl = sum(t["outcome"] == "SL" for t in trades)
    op = sum(t["outcome"] == "OPEN" for t in trades)
    print(f"re-run: TP={tp} SL={sl} OPEN={op} "
          f"win_rate={100*tp/(tp+sl) if tp+sl else float('nan'):.2f}%")
    print(f"audit : TP={sum(audit_trades.outcome=='TP')} "
          f"SL={sum(audit_trades.outcome=='SL')} "
          f"OPEN={sum(audit_trades.outcome=='OPEN')}")
    print(f"signals detected: {len(sig)}")
    print("signal status counts:")
    print(sig["status"].value_counts().to_string())


if __name__ == "__main__":
    main()
