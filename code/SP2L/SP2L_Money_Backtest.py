#!/usr/bin/env python
"""Single-timeframe virtual USD account; never sends broker orders."""
import argparse
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from SP2L_Advanced_Backtest import Settings, prepare_data, run_backtest


@dataclass(frozen=True)
class MoneySettings:
    initial_cash: float = 1000.0
    risk_pct: float = 2.0
    # USD per 1.0 price movement per lot, not necessarily physical contract size.
    loss_value: float = 100.0
    profit_value: float = 100.0
    volume_min: float = 0.01
    volume_step: float = 0.01
    volume_max: float = 100.0


def validate_money(money):
    if not all(np.isfinite(v) and v > 0 for v in vars(money).values()):
        raise ValueError('All money settings must be finite and positive.')
    if money.risk_pct > 100 or money.volume_max < money.volume_min:
        raise ValueError('Risk must be <=100%; maximum lot must be >= minimum lot.')


def size_trade(budget, loss_per_lot, money):
    """Floor lot size; never round up above the requested risk budget."""
    if budget <= 0:
        return 0.0
    step = Decimal(str(money.volume_step))
    cap = min(Decimal(str(budget)) / Decimal(str(loss_per_lot)),
              Decimal(str(money.volume_max)))
    volume = (cap / step).to_integral_value(rounding=ROUND_FLOOR) * step
    return float(volume) if volume >= Decimal(str(money.volume_min)) else 0.0


def simulate_money(trades, money):
    """Event-driven compounding; entries sized with realized cash at entry time.

    Overlapping trades (e.g. M1 and M15 simultaneously) are allowed: each
    entry is sized with the cash realized before its entry timestamp, and each
    exit realizes PnL immediately. At identical timestamps, entries are sized
    before same-timestamp exits are credited (conservative: slightly less
    cash available for sizing). Skipped candidates still occupy their engine
    slot/cooldown; it does not rescan that interval for new trades.
    """
    validate_money(money)
    events = []
    for index, trade in enumerate(trades):
        outcome = trade['outcome']
        if outcome not in ('TP', 'SL', 'OPEN') or trade['direction'] not in ('BUY', 'SELL'):
            raise ValueError('Invalid trade direction or outcome.')
        start = pd.Timestamp(trade['entry_time'])
        if pd.isna(start):
            raise ValueError('Invalid trade timestamps.')
        events.append((start, 0, index))
        if outcome != 'OPEN':
            end = pd.Timestamp(trade['exit_time'])
            if pd.isna(end) or end < start:
                raise ValueError('Invalid trade timestamps.')
            events.append((end, 1, index))
    events.sort(key=lambda event: (event[0], event[1]))

    cash = float(money.initial_cash)
    peak = cash
    drawdown = drawdown_pct = 0.0
    volume = {}
    requested = {}
    cash_after_exit = {}
    for timestamp, kind, index in events:
        trade = trades[index]
        sign = 1 if trade['direction'] == 'BUY' else -1
        risk = sign * (float(trade['entry']) - float(trade['sl']))
        reward = sign * (float(trade['tp']) - float(trade['entry']))
        if not np.isfinite([risk, reward]).all() or min(risk, reward) <= 0:
            raise ValueError('Invalid entry/SL/TP levels.')
        if kind == 0:  # entry: size with currently realized cash
            budget = max(0, cash) * money.risk_pct / 100
            requested[index] = budget
            volume[index] = size_trade(budget, risk * money.loss_value, money)
        else:  # exit: realize PnL
            outcome = trade['outcome']
            pnl = (volume[index] * reward * money.profit_value if outcome == 'TP'
                   else -volume[index] * risk * money.loss_value)
            cash += pnl
            peak = max(peak, cash)
            drawdown = max(drawdown, peak - cash)
            drawdown_pct = max(drawdown_pct, 100 * (peak - cash) / peak)
            cash_after_exit[index] = cash
    rows = []
    for index, trade in enumerate(trades):
        outcome = trade['outcome']
        note = ''
        if volume[index] == 0:
            note = 'SKIPPED (volume below broker minimum)'
        elif outcome == 'OPEN':
            note = 'OPEN at history end; no cash change'
        pnl = (volume[index] * (1 if trade['direction'] == 'BUY' else -1)
               * ((float(trade['tp']) - float(trade['entry'])) if outcome == 'TP'
                  else -(float(trade['entry']) - float(trade['sl'])))
               * money.profit_value if outcome == 'TP'
               else (-volume[index] * (1 if trade['direction'] == 'BUY' else -1)
                     * (float(trade['entry']) - float(trade['sl']))
                     * money.loss_value if outcome == 'SL' else 0.0))
        rows.append(dict(trade, signal_outcome=outcome,
                         outcome=outcome if volume[index] else 'SKIPPED',
                         requested_risk=requested[index],
                         actual_risk=volume[index] * (
                             (float(trade['entry']) - float(trade['sl']))
                             if trade['direction'] == 'BUY'
                             else (float(trade['sl']) - float(trade['entry'])))
                         * money.loss_value,
                         volume=volume[index], pnl=pnl,
                         cash_after=cash_after_exit.get(index)))
    return dict(rows=rows, final_cash=cash, net_profit=cash - money.initial_cash,
                growth_pct=100 * (cash / money.initial_cash - 1),
                max_drawdown=drawdown, max_drawdown_pct=drawdown_pct)


def trade_counts(rows):
    return {key: sum(row['outcome'] == key for row in rows)
            for key in ('TP', 'SL', 'OPEN', 'SKIPPED')}



def print_account(money, result):
    rows = result['rows']
    counts = {key: sum(t['outcome'] == key for t in rows)
              for key in ('TP', 'SL', 'OPEN', 'SKIPPED')}
    closed = counts['TP'] + counts['SL']
    rate = f"{100 * counts['TP'] / closed:.2f}%" if closed else 'N/A'
    print(f"Initial cash: ${money.initial_cash:,.2f}")
    print(f"Final realized cash: ${result['final_cash']:,.2f}")
    print(f"Net profit: ${result['net_profit']:+,.2f} | Growth: {result['growth_pct']:+.2f}%")
    print(f"TP={counts['TP']} | SL={counts['SL']} | Win rate={rate} | "
          f"Open={counts['OPEN']} | Skipped={counts['SKIPPED']}")
    print(f"Max realized drawdown: ${result['max_drawdown']:,.2f} "
          f"({result['max_drawdown_pct']:.2f}%)")



def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol', default='XAUUSD')
    parser.add_argument('--timeframe', choices=['M1', 'M5', 'M15'], default='M1')
    parser.add_argument('--bars', type=int, default=10000)
    parser.add_argument('--cash', type=float, default=1000.0)
    parser.add_argument('--risk-pct', type=float, default=2.0)
    parser.add_argument('--csv', type=Path)
    parser.add_argument('--point', type=float)
    parser.add_argument('--digits', type=int, default=2)
    parser.add_argument('--value-per-price', type=float,
                        help='CSV: USD per 1.0 price movement per lot, required')
    parser.add_argument('--volume-min', type=float, default=0.01, help='CSV lot minimum')
    parser.add_argument('--volume-step', type=float, default=0.01, help='CSV lot step')
    parser.add_argument('--volume-max', type=float, default=100.0, help='CSV lot maximum')
    parser.add_argument('--trades-csv', type=Path, help='Export volumes, risks, PnL and balance')
    args = parser.parse_args(argv)
    if args.bars < 501 or args.digits < 0:
        parser.error('Need >=501 bars including warmup, and nonnegative digits.')
    if args.csv and (args.point is None or args.value_per_price is None):
        parser.error('CSV requires --point and --value-per-price.')
    mt5 = None
    try:
        validate_money(MoneySettings(initial_cash=args.cash, risk_pct=args.risk_pct))
        if args.csv:
            if not np.isfinite(args.point) or args.point <= 0:
                raise ValueError('Point must be positive and finite.')
            settings = Settings(point=args.point, digits=args.digits)
            money = MoneySettings(args.cash, args.risk_pct, args.value_per_price,
                                  args.value_per_price, args.volume_min,
                                  args.volume_step, args.volume_max)
            data = prepare_data(pd.read_csv(args.csv.resolve())).tail(args.bars)
        else:
            import MetaTrader5 as mt5
            if not mt5.initialize():
                raise RuntimeError(f'MT5 initialize failed: {mt5.last_error()}')
            account = mt5.account_info()
            if account is None or account.currency != 'USD':
                raise ValueError('MT5 mode needs a USD account for USD tick values. '
                                 'Alternatively use CSV with explicit USD --value-per-price.')
            info = mt5.symbol_info(args.symbol)
            if info is None or info.trade_tick_size <= 0:
                raise ValueError('Symbol unavailable or invalid tick size; load symbol in MT5.')
            money = MoneySettings(args.cash, args.risk_pct,
                                  info.trade_tick_value_loss / info.trade_tick_size,
                                  info.trade_tick_value_profit / info.trade_tick_size,
                                  info.volume_min, info.volume_step, info.volume_max)
            settings = Settings(point=info.point, digits=info.digits,
                                minimum_distance_points=max(info.trade_stops_level,
                                                            info.trade_freeze_level))
            rates = mt5.copy_rates_from_pos(args.symbol,
                                           getattr(mt5, 'TIMEFRAME_' + args.timeframe),
                                           1, args.bars)
            if rates is None or len(rates) == 0:
                raise RuntimeError(f'No history: {mt5.last_error()}')
            data = prepare_data(pd.DataFrame(rates))
        validate_money(money)
        print(f'Virtual USD account; risk {money.risk_pct:g}% of realized balance per trade.')
        print('Closed-bar simulation; no orders. No fees, margin checks, slippage or '
              'unrealized PnL; current tick values held constant.')
        print(f'{args.symbol} {args.timeframe}: {len(data)} bars '
              f'{data.index[0]} to {data.index[-1]} (includes warmup)')
        if len(data) < args.bars:
            print(f'Warning: requested {args.bars} bars; received {len(data)}.')
        trades = run_backtest(data, settings, int(args.timeframe[1:]))
        result = simulate_money(trades, money)
        print_account(money, result)
        if args.trades_csv:
            columns = ['direction', 'setup_time', 'entry_time', 'exit_time', 'entry', 'sl',
                       'tp', 'signal_outcome', 'outcome', 'cash_before', 'requested_risk',
                       'actual_risk', 'volume', 'pnl', 'cash_after']
            pd.DataFrame(result['rows']).reindex(columns=columns).to_csv(
                args.trades_csv.resolve(), index=False)
            print(f'Trade ledger: {args.trades_csv.resolve()}')
        return 0
    except (ImportError, OSError, ValueError, RuntimeError) as error:
        print(f'Money backtest failed: {error}', file=sys.stderr)
        return 1
    finally:
        if mt5 is not None:
            mt5.shutdown()


if __name__ == '__main__':
    sys.exit(main())
