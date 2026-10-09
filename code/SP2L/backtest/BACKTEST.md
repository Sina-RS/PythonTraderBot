  # SP2L TP / SL counter

  Run with the Python environment containing the project's pandas, numpy,
  MetaTrader5 and tzdata dependencies. Open MT5 and log into your broker first.
  The backtest reads history only: no orders, Telegram messages or live loop.

  ```powershell
  python "c:\Users\Sina\Desktop\Pour samadi bot\PythonTraderBot\code\SP2L\SP2L_Advanced_Backtest.py" --bars 10000
  ```

  Default: XAUUSD, M1/M5/M15 independently, 10,000 **closed** bars per timeframe.
  Use `--symbol` for a broker suffix and `--timeframes M1` for a single timeframe.
  History count includes 500 warmup bars. The terminal's available history and
  "Max. bars in chart" setting can limit the requested count. Each timeframe's
  latest N bars covers a different date range, printed before its summary.

  Output gives TP count, SL count and **100 * TP / (TP + SL)**. The TOTAL row uses
  combined counts, not the average of timeframe percentages. Open trades are
  reported separately and excluded; unfilled setups are not trades. With no
  closed trades, win rate is N/A. No balance, profit factor or money management
  statistics are computed. `--trades-csv` optionally exports individual trades.

  ## Offline CSV

  ```powershell
  python "c:\Users\Sina\Desktop\Pour samadi bot\PythonTraderBot\code\SP2L\SP2L_Advanced_Backtest.py" --csv "c:\Users\Sina\Desktop\Pour samadi bot\PythonTraderBot\code\SP2L\XAUUSD_M1.csv" --timeframes M1 --point 0.01 --digits 2
  ```

  Supply your own comma-separated file with `time,open,high,low,close` and
  optional `spread` (broker points). Times are candle-open UTC timestamps:
  Unix seconds or ISO timestamps. Naive timestamps are assumed UTC; convert
  broker-local timestamps first. OHLC must be bid prices and include only
  completed candles. Missing spread means zero spread. Point is required for
  CSV; MT5 mode reads point and digits from the broker. The CSV must match the
  selected timeframe; no resampling is performed.

  ## Strategy and execution assumptions

  - The original signal, EMA/ADX, session/trend filter, setup and trailing-limit
    functions are loaded from the adjacent live source with an AST allowlist.
    Imports, top-level statements, broker execution functions and the infinite
    loop are never executed. Keep the live source alongside this app. Settings
    are explicit in the backtest's `Settings` class and match the supplied live
    bot; later edits to live constants do not automatically update these defaults.
  - This is a **closed-bar approximation**, not tick-for-tick live-bot parity.
    Decisions use the last completed candle, recalculating indicators on the
    latest 500 bars. New or moved limits become eligible on the next candle.
    The live bot instead examines forming candles every two seconds.
  - Buy limits fill when ask reaches entry; sell limits when bid reaches entry.
    Ask is approximated as bid plus that bar's fixed historical spread. Fills
    use the requested limit price even across gaps; TP remains attached to that
    price. Intrabar spread changes, slippage, commissions and broker rejection
    behaviour are not simulated. Current broker stop/freeze distance is used
    as a minimum placement distance, not historical broker restrictions.
  - Existing orders execute before close-based cancellations or filter checks.
    Failed filters prevent placement/modification but leave existing orders
    active, as live sync does. One setup/order and position per timeframe is
    modeled. Replaced/invalid setups cancel the simulated order; accidental
    orphan orders possible in live state management are intentionally not modeled.
  - On an existing position, a gap beyond TP/SL exits at the open's outcome.
    Otherwise a bar touching both levels counts SL first. On an intrabar limit
    fill, an SL touch counts SL; TP counts only if the close proves price moved
    past TP after entry. A high/low possibly reached before entry is not credited
    as a win. This conservative rule can undercount wins. Tick data is needed
    to resolve the actual sequence.
  - A 60-second cooldown starts at the exit bar's close (exit time within the
    bar is unknown). Each timeframe is independent; no cross-timeframe netting.
  - No second entry is simulated: the live setting does not place a second
    order. Unfinished trades remain OPEN at history end, without forced exits.

  ## Signal audit for manual MT5 verification

  ```powershell
  python "c:\Users\Sina\Desktop\Pour samadi bot\PythonTraderBot\code\SP2L\SP2L_Advanced_Backtest.py" --bars 10000 --audit-dir "c:\Users\Sina\Desktop\Pour samadi bot\PythonTraderBot\code\SP2L\audit" --chart-offset-hours 3
  ```

  The run is executed twice per timeframe; the report is written only when the
  audited trades are identical to the normal run. Output folder per timeframe:

  - `report.html` — open in a browser; each signal expands into its candles,
    event timeline and simulated trade.
  - `signals.csv` — one row per detected setup: direction, signal-bar UTC,
    decision time (candle close), initial entry/SL/TP, EMA/ADX, final status.
  - `signal_candles.csv` — the five detection candles (context, before_spike,
    spike, after_spike, signal) with OHLC and spread, plus an optional chart
    label column using `--chart-offset-hours`.
  - `events.csv` — timeline per signal: DETECTED, PLACED, MODIFIED, WAIT
    (which filter blocked placement; existing orders stay active), FILLED,
    REVIEW (both levels touched or TP on the fill bar), TP, SL, CANCELLED,
    REPLACED, UNFILLED, OPEN.
  - `trades.csv`, `history.csv` — simulated trades and the exact input candles.
  - `metadata.json` — settings, bar range and SHA-256 hashes of the history
    export and source files, so results can be tied to a code state.

  ### Manual check in MT5

  1. Open the same symbol and timeframe, turn off Auto Scroll.
  2. The report times are UTC; MT5 charts show server time. Set
    `--chart-offset-hours` to your broker's fixed UTC offset (verify it once by
    comparing one candle's OHLC with `history.csv`) or compare using the Data
    Window after locating the candle by shape.
  3. For a signal row, find `signal_bar_utc` and confirm the five candles match
    `signal_candles.csv` (three rising bodies, the middle one the largest, and
    the price gap between before_spike high/low and after_spike low/high).
  4. Step forward candle by candle: watch the limit trail to `entry`, the fill,
    and which candle touches `sl` or `tp` first. Where a candle touches both
    levels, or TP occurs on the fill candle, OHLC cannot prove the intrabar
    order; the event log marks these REVIEW and tick history is required.
  5. Compare with `trades.csv` outcomes to confirm TP/SL counts.

  ### What this validates — and what it cannot

  - It validates that the backtest applies the recorded rules to the recorded
    candles: signal detection, filters, trailing limit, fills, TP/SL counting.
  - It cannot prove the live bot's real fills. The live bot scans forming
    candles every two seconds; the backtest decides on closed candles. Only
    tick history (or a forward/paper run) can confirm exact entry timing,
    intrabar sequence and spread at fill time. Reported entry/exit timestamps
    identify candles, not tick execution times.

  ## Tests

  ```powershell
  python "c:\Users\Sina\Desktop\Pour samadi bot\PythonTraderBot\code\SP2L\test_sp2l_backtest.py"
  ```

  Tests run offline and cover BUY/SELL, TP/SL, ambiguity, trailing, spread,
  next-bar entry, open/unfilled trades, validation, and an end-to-end CSV run.
  Synthetic test win rates are not historical strategy performance.
