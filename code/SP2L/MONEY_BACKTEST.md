# Virtual-money SP2L backtest

This separate command-line app starts with **$1,000** and budgets **2% of the
current realized balance** as the planned stop-loss risk for each trade.
It reuses the existing candle engine. No real orders are submitted.

## Run

Open MT5 on a USD account, then run:

```powershell
python "c:\Users\Sina\Desktop\Pour samadi bot\PythonTraderBot\code\SP2L\SP2L_Money_Backtest.py" --timeframe M1 --bars 10000 --trades-csv "c:\Users\Sina\Desktop\Pour samadi bot\PythonTraderBot\code\SP2L\money_trades.csv"
```

Select M1, M5 or M15 with `--timeframe`. Each run is **one independent virtual
account on one timeframe**, not a combined multi-timeframe portfolio.
Defaults are `--cash 1000 --risk-pct 2 --symbol XAUUSD`. Use the broker's exact
symbol name if it has a suffix. The first 500 bars provide indicator warmup.
At least 501 completed bars are required. Output shows the actual history range.

## Sizing and growth

- Risk budget = current realized balance * 0.02.
- Lots = risk budget / (entry-to-SL distance * USD loss value per price unit per lot).
- Lots round **down** to the broker step and are capped at its maximum.
- If the minimum lot exceeds the budget, the trade is skipped, never rounded up.
- TP adds the sized profit; SL subtracts the sized risk. Subsequent trades use
  the updated balance. Example with sufficiently fine lot precision:
  $1,000 -> $1,020 after a 1R win -> $999.60 after a 1R loss.
- Lot steps may make actual risk less than 2%. The ledger records both requested
  and actual risk, variable volume, PnL, and balance before/after every trade.
- Output reports final realized balance, net profit, percentage growth,
  executed TP/SL counts, skipped/open trades and maximum realized drawdown.
  Skipped candidates do not count as wins/losses.

MT5 mode uses its current tick loss/profit values divided by tick size and
requires a USD account to avoid falsely labeling another currency as dollars.
The real account balance is not used; only the configured virtual cash is used.

## Offline CSV

Use bid OHLC with UTC candle-open timestamps and optional spread in broker points,
just like the original backtest. Supply the correct USD conversion explicitly:

```powershell
python "c:\Users\Sina\Desktop\Pour samadi bot\PythonTraderBot\code\SP2L\SP2L_Money_Backtest.py" --csv "c:\Users\Sina\Desktop\Pour samadi bot\PythonTraderBot\code\SP2L\XAUUSD_M1.csv" --timeframe M1 --point 0.01 --value-per-price 100
```

`--value-per-price 100` means a 1.0 price move is worth $100 per lot; verify
this against your symbol specification, do not assume it for every instrument.
CSV lot defaults: minimum 0.01, step 0.01, maximum 100. Override with
`--volume-min`, `--volume-step`, `--volume-max`.

## Limitations

This is a planned-level, closed-bar simulation, not a live account guarantee.
The existing candle engine's conservative TP/SL ambiguity rules still apply.
PnL is valued at the attached SL/TP levels, including across gaps: actual losses
can exceed planned 2% risk because of gaps, spread changes and slippage.
Current tick values are held constant throughout history. Fees, swaps, margin
requirements and liquidation are not modeled. Open trades are not marked to
market, so final cash and drawdown are **realized balance**, not equity.
Sizing is applied to the engine's candidate trades: skipped trades still occupy
the engine's original trade/cooldown interval; no alternative setups are scanned
inside that interval. Pending-order lot size is calculated at simulated fill,
not repeatedly managed at order-placement time as a live executor would require.

## Verification

```powershell
python "c:\Users\Sina\Desktop\Pour samadi bot\PythonTraderBot\code\SP2L\test_sp2l_money.py"
```

Six offline tests cover exact compounding, lot flooring/capping, minimum-lot
skips, open trades, sell valuation, invalid/overlapping trades and CSV output.
An MT5 smoke run of 2,000 XAUUSD M1 bars (2026-09-15 14:18 UTC through
2026-09-17 01:37 UTC, including warmup) produced $894.45 from $1,000,
2 TP and 8 SL. This historical sample is not a prediction of future performance.
