#!/usr/bin/env python
"""Closed-candle SP2L backtest; never imports or runs the live trading loop."""
import argparse
import ast
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from datetime import time
from pathlib import Path
import sys

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Settings:
    # Defaults mirror the live bot, not the older notebook.
    point: float = 0.01
    digits: int = 2
    number_of_data: int = 500
    spike_candle_size: float = 1.5
    pgap_points: int = 100
    max_sl_distance_points: int = 1000
    tp_r: float = 1.0
    use_ema_filter: bool = True
    ema_period: int = 60
    use_trend_filter: bool = True
    max_opposite_moves: int = 1
    use_range_filter: bool = True
    adx_period: int = 14
    min_adx: float = 20.0
    use_session_filter: bool = False
    session_start_hour: int = 1
    session_end_hour: int = 5
    session_timezone: str = "America/New_York"
    minimum_distance_points: int = 0


def prepare_data(data):
    """Validate bid OHLC. Timestamps are candle opens, normalized to UTC."""
    data = data.copy()
    data.columns = [str(c).strip().lower() for c in data.columns]
    if not isinstance(data.index, pd.DatetimeIndex):
        column = next((c for c in ("time", "datetime", "date", "local time")
                       if c in data.columns), None)
        if column is None:
            raise ValueError("History requires a time column or DatetimeIndex.")
        values = data.pop(column)
        numeric = pd.to_numeric(values, errors="coerce")
        data.index = pd.to_datetime(
            numeric if numeric.notna().all() else values,
            unit="s" if numeric.notna().all() else None, utc=True,
        )
    else:
        data.index = pd.to_datetime(data.index, utc=True)
    columns = ["open", "high", "low", "close"]
    if not set(columns).issubset(data.columns):
        raise ValueError("History requires open, high, low, close columns.")
    for column in columns + (["spread"] if "spread" in data else []):
        data[column] = pd.to_numeric(data[column], errors="raise")
    if data.empty or data.index.hasnans or data.index.has_duplicates:
        raise ValueError("History is empty or contains missing/duplicate times.")
    if not np.isfinite(data[columns].to_numpy()).all():
        raise ValueError("OHLC prices must be finite.")
    if ((data.high < data[columns].max(axis=1))
            | (data.low > data[columns].min(axis=1))).any():
        raise ValueError("Invalid OHLC: high/low must enclose open and close.")
    if "spread" not in data:
        data["spread"] = 0.0
    if not np.isfinite(data.spread).all() or (data.spread < 0).any():
        raise ValueError("Spread must be finite, nonnegative broker points.")
    return data.sort_index()


def load_strategy(settings):
    """Compile named pure functions only; never execute bot startup code.

    The adjacent live source is trusted project code, not user input.
    """
    path = Path(__file__).with_name("SP2L_Advanced_Bot.py")
    names = {
        "calculate_ema", "calculate_adx", "is_in_new_york_session",
        "entry_filters_are_valid", "buy_trend_is_valid", "sell_trend_is_valid",
        "detect_buy_setup", "detect_sell_setup", "create_pending_buy",
        "create_pending_sell", "update_pending_limit", "pending_setup_is_invalid",
    }
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != names:
        raise ValueError("Live strategy functions changed; update the backtest adapter.")
    namespace = {key.upper(): value for key, value in vars(settings).items()}
    namespace.update(pd=pd, np=np, time=time,
                     NEW_YORK_TZ=ZoneInfo(settings.session_timezone),
                     P_GAP_PRICE=settings.pgap_points * settings.point,
                     MAX_SL_DISTANCE_PRICE=settings.max_sl_distance_points * settings.point)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return SimpleNamespace(**{name: namespace[name] for name in names})


def exit_result(position, bar, spread, newly_filled=False):
    """SL first on ambiguous bars; never credit a pre-entry high/low as TP."""
    buy = position["direction"] == "BUY"
    offset = 0 if buy else spread
    opening, high, low, closing = (float(bar[k]) + offset
                                   for k in ("open", "high", "low", "close"))
    sl, tp = position["sl"], position["tp"]
    if not newly_filled:
        if (opening <= sl if buy else opening >= sl):
            return "SL"
        if (opening >= tp if buy else opening <= tp):
            return "TP"
    if (low <= sl if buy else high >= sl):
        return "SL"
    if newly_filled:
        # Only a close beyond TP proves a post-entry TP after an intrabar fill.
        fill_at_open = (bar.open + spread <= position["entry"] if buy
                        else bar.open >= position["entry"])
        if not fill_at_open:
            return "TP" if (closing >= tp if buy else closing <= tp) else None
    return "TP" if (high >= tp if buy else low <= tp) else None


def summarize(trades):
    tp = sum(trade["outcome"] == "TP" for trade in trades)
    sl = sum(trade["outcome"] == "SL" for trade in trades)
    return {"tp": tp, "sl": sl, "open": sum(t["outcome"] == "OPEN" for t in trades),
            "win_rate": 100 * tp / (tp + sl) if tp + sl else None}


def run_backtest(data, settings=None, timeframe_minutes=1, audit=None):
    settings = settings or Settings()
    if (not np.isfinite(settings.point) or settings.point <= 0
            or settings.number_of_data < 5 or settings.tp_r <= 0
            or timeframe_minutes <= 0):
        raise ValueError("Invalid point, history window, TP ratio or timeframe.")
    data = prepare_data(data)
    if len(data) < settings.number_of_data + 1:
        raise ValueError(f"Need at least {settings.number_of_data + 1} bars including warmup.")
    strategy = load_strategy(settings)
    pending = order = position = None
    cooldown_until = None
    trades = []
    duration = pd.Timedelta(minutes=timeframe_minutes)

    def record(setup, event, reason='', **extra):
        if audit is not None:
            audit.record(setup, timestamp, event, reason, **extra)

    for i in range(settings.number_of_data - 1, len(data)):
        timestamp = data.index[i]
        bar = data.iloc[i]
        spread = float(bar.spread) * settings.point
        filled = False
        # Orders execute BEFORE this bar's close-based decisions.
        if position is None and order is not None:
            buy = order["direction"] == "BUY"
            if (bar.low + spread <= order["entry"] if buy else bar.high >= order["entry"]):
                position = dict(order, entry_time=timestamp, outcome="OPEN")
                record(position, 'FILLED', 'Simulated limit touch; exact tick time unknown',
                       spread_points=float(bar.spread))
                pending = order = None
                filled = True
        if position is not None:
            outcome = exit_result(position, bar, spread, filled)
            if audit is not None:
                is_buy = position['direction'] == 'BUY'
                offset = 0 if is_buy else spread
                sl_touch = (bar.low <= position['sl'] if is_buy
                            else bar.high + offset >= position['sl'])
                tp_touch = (bar.high >= position['tp'] if is_buy
                            else bar.low + offset <= position['tp'])
                if (sl_touch and tp_touch) or (filled and tp_touch):
                    record(position, 'REVIEW', 'Both levels touched or TP on fill bar; inspect tick sequence',
                           sl_touched=bool(sl_touch), tp_touched=bool(tp_touch))
            if outcome is not None:
                record(position, outcome, 'exit_result: open-gap priority, otherwise SL-first; fill-bar TP requires proof')
                position.update(outcome=outcome, exit_time=timestamp)
                trades.append(position)
                position = None
                cooldown_until = timestamp + duration + pd.Timedelta(seconds=60)
            continue
        if cooldown_until is not None and timestamp + duration < cooldown_until:
            continue
        window = data.iloc[max(0, i + 1 - settings.number_of_data):i + 1].copy()
        window["EMA"] = strategy.calculate_ema(window)
        window["ADX"] = (strategy.calculate_adx(window, settings.adx_period)
                         if settings.use_range_filter else np.nan)
        if pending is not None:
            strategy.update_pending_limit(window, pending)
        buy = strategy.detect_buy_setup(window)
        sell = strategy.detect_sell_setup(window)
        if buy != sell:
            # One logical setup/order per timeframe; no orphan broker orders.
            record(pending, 'REPLACED', 'New setup replaces previous setup/order')
            pending = (strategy.create_pending_buy(window) if buy
                       else strategy.create_pending_sell(window))
            if audit is not None:
                pending['signal_id'] = audit.signal(window, pending, settings, duration)
                record(pending, 'DETECTED', 'Decision at candle close',
                       decision_utc=timestamp + duration)
            order = None
        if pending is None:
            continue
        buy = pending["direction"] == "BUY"
        price = round(float(pending["entry"]), settings.digits)
        sl = round(float(pending["sl"]), settings.digits)
        risk = price - sl if buy else sl - price
        if risk <= 0 or strategy.pending_setup_is_invalid(window, pending):
            record(pending, 'CANCELLED', 'SL breached or risk outside permitted distance')
            pending = order = None
            continue
        try:
            start = window.index.get_loc(pending["setup_pos_time"])
        except KeyError:
            record(pending, 'WAIT', 'Setup outside rolling history; existing order unchanged')
            continue
        trend = strategy.buy_trend_is_valid if buy else strategy.sell_trend_is_valid
        if not trend(window, start, len(window) - 1):
            record(pending, 'WAIT', 'Trend filter failed; existing order unchanged')
            continue
        if not strategy.entry_filters_are_valid(window, len(window) - 1, pending["direction"]):
            record(pending, 'WAIT', 'EMA/ADX/session filter failed; existing order unchanged',
                   ema=float(window.iloc[-1]['EMA']), adx=float(window.iloc[-1]['ADX']))
            continue  # Live sync also leaves already placed orders active.
        distance = bar.close + spread - price if buy else price - bar.close
        if distance <= 0 or distance < settings.minimum_distance_points * settings.point:
            record(pending, 'WAIT', 'Limit not passive or too close to market; existing order unchanged')
            continue
        tp = round(price + settings.tp_r * risk if buy else price - settings.tp_r * risk,
                   settings.digits)
        if (tp <= price if buy else tp >= price):
            record(pending, 'WAIT', 'Rounded TP invalid')
            continue
        previous_order = order
        order = dict(pending, entry=price, sl=sl, tp=tp)
        if previous_order is None or any(previous_order[k] != order[k] for k in ('entry', 'sl', 'tp')):
            record(order, 'PLACED' if previous_order is None else 'MODIFIED',
                   'Effective after this candle closes; no same-bar fill',
                   decision_utc=timestamp + duration)
    if position is not None:
        record(position, 'OPEN', 'History ended before exit')
        trades.append(position)
    if pending is not None:
        record(pending, 'UNFILLED', 'History ended; order active' if order is not None
               else 'History ended; no active order')
    return trades


def print_summary(label, trades):
    result = summarize(trades)
    rate = "N/A (no closed trades)" if result["win_rate"] is None else f"{result['win_rate']:.2f}%"
    print(f"{label}: TP={result['tp']} | SL={result['sl']} | Win rate={rate}"
          f" | Open/excluded={result['open']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="SP2L TP/SL counter (closed-bar approximation).")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--timeframes", nargs="+", choices=["M1", "M5", "M15"],
                        default=["M1", "M5", "M15"])
    parser.add_argument("--bars", type=int, default=10000,
                        help="Closed bars per timeframe, including 500 warmup bars")
    parser.add_argument("--csv", type=Path, help="Offline bid OHLC CSV; use one timeframe")
    parser.add_argument("--point", type=float, help="Required broker point for CSV")
    parser.add_argument("--digits", type=int, default=2, help="CSV price decimal places")
    parser.add_argument("--trades-csv", type=Path, help="Optional individual trade output")
    parser.add_argument("--audit-dir", type=Path, help="Write signal evidence CSVs and HTML per timeframe")
    parser.add_argument("--chart-offset-hours", type=float,
                        help="Optional fixed UTC-to-MT5-chart offset; verify with your broker")
    args = parser.parse_args(argv)
    if args.bars < 501:
        parser.error("--bars must be at least 501 (500 warmup bars).")
    if len(set(args.timeframes)) != len(args.timeframes):
        parser.error("Duplicate timeframes would double-count trades.")
    if args.csv and (len(args.timeframes) != 1 or args.point is None):
        parser.error("CSV needs one --timeframes value and an explicit --point.")
    if args.digits < 0 or (args.point is not None and (not np.isfinite(args.point) or args.point <= 0)):
        parser.error("Point must be positive and finite; digits must be nonnegative.")
    if args.chart_offset_hours is not None and (
            not np.isfinite(args.chart_offset_hours) or abs(args.chart_offset_hours) > 14):
        parser.error("Chart offset must be finite and between -14 and 14 hours.")
    all_trades = []
    mt5 = None
    try:
        if not args.csv:
            import MetaTrader5 as mt5
            if not mt5.initialize():
                raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
            info = mt5.symbol_info(args.symbol)
            if info is None:
                raise RuntimeError(f"Unknown broker symbol: {args.symbol}")
            settings = Settings(point=float(info.point), digits=int(info.digits),
                                minimum_distance_points=max(info.trade_stops_level,
                                                            info.trade_freeze_level))
        else:
            settings = Settings(point=args.point, digits=args.digits)
        print("Closed-bar approximation; SL-first ambiguity; open trades excluded.")
        for label in args.timeframes:
            if args.csv:
                data = prepare_data(pd.read_csv(args.csv.resolve())).tail(args.bars)
            else:
                rates = mt5.copy_rates_from_pos(args.symbol, getattr(mt5, f"TIMEFRAME_{label}"),
                                               1, args.bars)
                if rates is None or len(rates) == 0:
                    raise RuntimeError(f"No {label} history: {mt5.last_error()}. Load history in MT5.")
                data = prepare_data(pd.DataFrame(rates))
            print(f"{args.symbol} {label}: {len(data)} bars, {data.index[0]} to {data.index[-1]}")
            if not args.csv and len(data) < args.bars:
                print(f"Warning: requested {args.bars} bars; broker returned {len(data)}.")
            trades = run_backtest(data, settings, int(label[1:]))
            for trade in trades:
                trade["timeframe"] = label
            if args.audit_dir:
                from SP2L_Audit import SignalAudit
                audit = SignalAudit()
                audited = run_backtest(data, settings, int(label[1:]), audit=audit)
                fields = ("direction", "setup_time", "entry_time", "exit_time",
                          "entry", "sl", "tp", "outcome")
                if [{k: t.get(k) for k in fields} for t in audited] != [
                        {k: t.get(k) for k in fields} for t in trades]:
                    print(f"Warning: audit run diverged from main run for {label}; "
                          "report not written.")
                else:
                    report = audit.export(args.audit_dir / label, data, audited, settings,
                                          args.symbol, label, args.chart_offset_hours)
                    print(f"Audit: {report}")
            print_summary(label, trades)
            all_trades.extend(trades)
        if len(args.timeframes) > 1:
            print_summary("TOTAL", all_trades)
        if args.trades_csv:
            columns = ["timeframe", "direction", "setup_time", "entry_time", "entry",
                       "sl", "tp", "exit_time", "outcome"]
            pd.DataFrame(all_trades).reindex(columns=columns).to_csv(
                args.trades_csv.resolve(), index=False)
        return 0
    except (ImportError, OSError, ValueError, RuntimeError) as error:
        print(f"Backtest failed: {error}", file=sys.stderr)
        return 1
    finally:
        if mt5 is not None:
            mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())

