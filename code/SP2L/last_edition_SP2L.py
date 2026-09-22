"""
last_edition_SP2L.py
====================

Live SP2L bot = the **body** of ``SP2L_Advanced_Bot.py`` + the **trading
logic** of ``SP2LBot.mq5``.

Trading logic taken from ``SP2LBot.mq5``:

* Only the PENDING LIMIT order path trades.  In the Advanced bot
  ``Strategy()`` always returned ``trade_setup = None``, so its
  "EXECUTE ENTRY 1" / ``Meta.run()`` market block was dead code; it is
  removed here, exactly like the EA does.
* Filters: EMA, ADX range, trend structure, New York session, plus the
  MQ5-only server-hour filter and spread filter, and a trade-direction
  filter (BOTH / LONG_ONLY / SHORT_ONLY).
* Broker stop-level validation before a limit is placed or modified.
* Open-position management: maximum holding time, partial take profit,
  breakeven and trailing stop.
* Per-timeframe cooldown after a position closes and a throttled retry
  after a failed pending-order removal.
* The optional second entry is reported but never sent as an order, the
  same as the EA (the original never placed one).

The multi-timeframe loop, the Meta execution layer, the logging/Telegram
system, the settings layout and the helpers all come from
``SP2L_Advanced_Bot.py``.
"""

import MetaTrader5 as mt5
from datetime import datetime, timezone, time, timedelta
import time as time_module
from zoneinfo import ZoneInfo
from Meta import *
from TelegramBot import TeleBot
from colorama import init as colorama_init
from colorama import Fore
from colorama import Style
import math
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from io import BytesIO

colorama_init()

# ============================================================
# LOGGING / TELEGRAM LOG SYSTEM
# (ported from main.py: add_log + rate-limited Telegram alerts)
# ============================================================

logs = []

# Only explicitly marked outcome logs are mirrored to Telegram.
TELEGRAMLOG_FOR_STATAS = True

# Telegram log language:
#   True  -> Persian / Dari (دری)
#   False -> English
# The console output is always English; this only affects Telegram.
TELEGRAM_PERSIAN = True

# Trend-formation logs can be controlled independently from trade logs.
LOG_TREND_FORMATION = True
TELEGRAM_TREND_FORMATION = True
MIN_TREND_CANDLES = 3
TREND_CHART_ENABLED = True

# Hard alerts (entries, errors, closes) are always sent to Telegram.
# Minimum seconds between two Telegram log messages.
TELEGRAM_LOG_INTERVAL = 1.0

# Console-only diagnostics (MQ5 "Log()" vs "LogAlways()"). When False,
# verbose lines are neither printed nor stored.
VERBOSE_LOG = True

telegram_bot = TeleBot()
last_telegram_log_time = None

# ------------------------------------------------------------
# Persian / Dari (دری) translations for the Telegram messages.
#
# The console keeps the original English text; only the messages
# mirrored to Telegram are translated, so the terminal output stays
# easy to grep while the chat reads naturally in Persian.
# ------------------------------------------------------------

DIRECTION_FA = {
    "BUY": "خرید",
    "SELL": "فروش",
}

TREND_FA = {
    "UPTREND": "روند صعودی",
    "DOWNTREND": "روند نزولی",
}

STRUCTURE_FA = {
    "Higher Lows": "کف‌های بالاتر",
    "Lower Highs": "سقف‌های پایین‌تر",
}


def add_log(
    message,
    send_telegram=False,
    telegram_enabled=None,
    telegram_message=None
):
    global last_telegram_log_time

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"

    logs.insert(0, log_entry)
    if len(logs) > 300:
        logs.pop()

    print(log_entry)

    should_send_telegram = (
        TELEGRAMLOG_FOR_STATAS
        if telegram_enabled is None
        else telegram_enabled
    )

    if should_send_telegram or send_telegram:
        now = datetime.now()
        if (
            last_telegram_log_time is None
            or (now - last_telegram_log_time).total_seconds()
            >= TELEGRAM_LOG_INTERVAL
        ):
            # Use the Persian text for Telegram when provided and the
            # Persian language is enabled, otherwise fall back to the
            # English console message.
            if telegram_message is not None and TELEGRAM_PERSIAN:
                telegram_text = telegram_message
            else:
                telegram_text = message
            telegram_bot.SendMessage(
                f"🤖 SP2L Bot:\n[{timestamp}] {telegram_text}"
            )
            last_telegram_log_time = now


def log_verbose(message):
    """Console-only diagnostic, equivalent to the EA's ``Log()``.

    Never mirrored to Telegram, so repeated retries cannot spam the chat.
    """
    if VERBOSE_LOG:
        add_log(message, telegram_enabled=False)


def retcode_of(result):
    """Return ``result.retcode`` (or None) without raising on None."""
    return None if result is None else getattr(result, "retcode", None)


# ============================================================
# MT5 INITIALIZE
# ============================================================

if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    mt5.shutdown()
    quit()

# Mirror Meta-layer execution messages (opens/closes/errors) to Telegram.
Meta.teleBotMessage = True

# Keep the Meta-layer Telegram language in sync with TELEGRAM_PERSIAN.
Meta.teleBotPersian = TELEGRAM_PERSIAN

# ============================================================
# MT5 CONNECTION CHECK
#
# The raw socket test (internet()) can fail even when the bot is
# perfectly able to trade, for example when traffic is routed through a
# proxy/VPN or when the ISP blocks outbound TCP port 53. MT5 connects to
# the broker through its own channel, so the real connectivity
# requirement is the MT5 terminal/account connection, not a socket to
# Google DNS.
#
# This check is used by the main loop instead of internet().
# ============================================================

def mt5_is_connected():

    try:

        terminal = mt5.terminal_info()

        if terminal is None:
            return False

        if not terminal.connected:
            return False

        account = mt5.account_info()

        if account is None:
            return False

        return True

    except BaseException as e:

        print(
            "An exception has occurred in "
            f"mt5_is_connected: {str(e)}"
        )

        return False


# ============================================================
# SETTINGS
# ============================================================

SYMBOL = "XAUUSD"

# Enough history for the indicators to converge (EMA 60 / ADX 14 need
# roughly 3x their period of warm-up) plus the setup window. 500 was
# unnecessary; 200 keeps the same indicator values with a lighter feed.
NUMBER_OF_DATA = 200

# ------------------------------------------------------------
# Base magic number. Each timeframe uses MAGIC + its own offset
# (see TIMEFRAMES below) so positions/state never conflict
# between timeframes.
# ------------------------------------------------------------

MAGIC = 8

TIMEFRAMES = {
    "M1": {
        "mt5": mt5.TIMEFRAME_M1,
        "enabled": True,
        "magic": MAGIC + 1,
    },
    "M5": {
        "mt5": mt5.TIMEFRAME_M5,
        "enabled": True,
        "magic": MAGIC + 5,
    },
    "M15": {
        "mt5": mt5.TIMEFRAME_M15,
        "enabled": True,
        "magic": MAGIC + 15,
    },
}

# ------------------------------------------------------------
# Setup detection (MQ5: InpSpikeCandleSize / InpGapPoints / ...)
# ------------------------------------------------------------

SPIKE_CANDLE_SIZE = 1.5

PGAP_POINTS = 100
MAX_SL_DISTANCE_POINTS = 1000

TP_R = 1.0

# ------------------------------------------------------------
# EMA filter
# ------------------------------------------------------------

USE_EMA_FILTER = True

EMA_PERIOD = 60

# ------------------------------------------------------------
# Trend structure filter
# ------------------------------------------------------------

USE_TREND_FILTER = True

MAX_OPPOSITE_MOVES = 2

# ------------------------------------------------------------
# Range / ADX filter
# ------------------------------------------------------------

USE_RANGE_FILTER = True

ADX_PERIOD = 14
MIN_ADX = 20.0

# ------------------------------------------------------------
# New York session filter (disabled by default)
# ------------------------------------------------------------

USE_SESSION_FILTER = False

SESSION_START_HOUR = 1
SESSION_END_HOUR = 5

SESSION_TIMEZONE = "America/New_York"

# Broker server time GMT offset, used to convert server candles to
# New York time (MQ5: InpServerGmtOffset).
SERVER_GMT_OFFSET = 3

# ------------------------------------------------------------
# Server-hour filter (MQ5 only)
# ------------------------------------------------------------

USE_HOUR_FILTER = False

HOUR_FROM = 14
HOUR_TO = 18

# ------------------------------------------------------------
# Spread filter (MQ5 only)
# ------------------------------------------------------------

USE_SPREAD_FILTER = False

MAX_SPREAD_POINTS = 50

# ------------------------------------------------------------
# Trade-direction filter (MQ5 only)
#
# "BOTH" | "LONG_ONLY" | "SHORT_ONLY"
# ------------------------------------------------------------

TRADE_SIDE = "BOTH"


# ------------------------------------------------------------
# Second entry
#
# Kept for parity with the MQ5 EA: it is reported at startup, but no
# second order is ever placed (the original never placed one).
# ------------------------------------------------------------

USE_SECOND_ENTRY = True

SECOND_ENTRY_VOLUME_MULTIPLIER = 2.0

# ------------------------------------------------------------
# Behaviour
# ------------------------------------------------------------

# NOTE: the base MAGIC is defined above, before TIMEFRAMES,
# because each timeframe's magic is derived from it.
LOT = 0.1

LOOP_SECONDS = 2

# Per-timeframe pause after a position closes.
COOLDOWN_SECONDS = 60

# A broker can reject removing a pending order while the symbol is
# closed or the order is temporarily frozen. Keep the setup for a
# later retry, but do not hammer the trade server every loop.
PENDING_REMOVE_RETRY_SECONDS = 60

# Deviation in broker points for market operations (partial close /
# forced close).
SLIPPAGE_POINTS = 20

# ------------------------------------------------------------
# Open-position management (MQ5 only, all disabled by default)
# ------------------------------------------------------------

USE_BREAKEVEN = False

BREAKEVEN_AT_R = 1.0
BREAKEVEN_BUFFER_POINTS = 10

USE_PARTIAL_TP = False

PARTIAL_AT_R = 1.0
PARTIAL_PERCENT = 50.0

USE_TRAIL_STOP = False

TRAIL_START_R = 1.0
TRAIL_DISTANCE_R = 1.0

MAX_HOLDING_MINUTES = 0

# ============================================================
# SYMBOL INFORMATION
# ============================================================

symbol_info = mt5.symbol_info(SYMBOL)

if symbol_info is None:
    mt5.shutdown()
    raise RuntimeError(
        f"Could not get symbol information for {SYMBOL}"
    )

BROKER_POINT = float(symbol_info.point)
DIGITS = int(symbol_info.digits)

if BROKER_POINT <= 0:
    mt5.shutdown()
    raise RuntimeError("Invalid broker point.")

P_GAP_PRICE = PGAP_POINTS * BROKER_POINT

MAX_SL_DISTANCE_PRICE = (
    MAX_SL_DISTANCE_POINTS * BROKER_POINT
)


# ============================================================
# PRINT SETTINGS
# ============================================================

print("-" * 75)
print("LAST EDITION SP2L TRADER")
print("-" * 75)
print("Symbol              :", SYMBOL)
print("Point               :", BROKER_POINT)
print("Digits              :", DIGITS)
print("Spike multiplier    :", SPIKE_CANDLE_SIZE)
print("Gap points          :", PGAP_POINTS)
print("Max SL points       :", MAX_SL_DISTANCE_POINTS)
print("TP                  :", f"{TP_R}R")
print("EMA filter          :", USE_EMA_FILTER)
print("EMA period          :", EMA_PERIOD)
print("Trend filter        :", USE_TREND_FILTER)
print("Max opposite moves  :", MAX_OPPOSITE_MOVES)
print("Range filter        :", USE_RANGE_FILTER)
print("ADX period          :", ADX_PERIOD)
print("Minimum ADX         :", MIN_ADX)
print("Session filter      :", USE_SESSION_FILTER)
print("Session timezone    :", SESSION_TIMEZONE)
print(
    "Session             :",
    f"{SESSION_START_HOUR:02d}:00 - {SESSION_END_HOUR:02d}:00",
    f"(server GMT+{SERVER_GMT_OFFSET})"
)
print("Server-hour filter  :", USE_HOUR_FILTER)
print(
    "Server hours        :",
    f"{HOUR_FROM:02d}:00 - {HOUR_TO:02d}:00"
)
print("Spread filter       :", USE_SPREAD_FILTER)
print("Max spread points   :", MAX_SPREAD_POINTS)
print("Trade side          :", TRADE_SIDE)
print("Trend formation log :", LOG_TREND_FORMATION)
print("Trend log Telegram  :", TELEGRAM_TREND_FORMATION)
print(
    "Telegram language   :",
    "Persian / Dari" if TELEGRAM_PERSIAN else "English"
)
print("Minimum trend bars  :", MIN_TREND_CANDLES)
print("Second entry        :", USE_SECOND_ENTRY, "(log only, never sent)")
print("Second entry volume :", SECOND_ENTRY_VOLUME_MULTIPLIER)
print("Breakeven           :", USE_BREAKEVEN)
print(
    "Breakeven trigger   :",
    f"{BREAKEVEN_AT_R}R",
    f"(buffer {BREAKEVEN_BUFFER_POINTS} points)"
)
print("Partial TP          :", USE_PARTIAL_TP)
print(
    "Partial trigger     :",
    f"{PARTIAL_AT_R}R",
    f"({PARTIAL_PERCENT}% of volume)"
)
print("Trailing stop       :", USE_TRAIL_STOP)
print(
    "Trailing            :",
    f"start {TRAIL_START_R}R",
    f"distance {TRAIL_DISTANCE_R}R"
)
print("Max holding minutes :", MAX_HOLDING_MINUTES)
print("Cooldown seconds    :", COOLDOWN_SECONDS)
print("Magic               :", MAGIC)
print("Lot                 :", LOT)
print(
    "Timeframes          :",
    ", ".join(
        f"{name}"
        f"({'ON' if cfg['enabled'] else 'OFF'}, magic {cfg['magic']})"
        for name, cfg in TIMEFRAMES.items()
    )
)
print("-" * 75)


# ============================================================
# EMA
# ============================================================

def calculate_ema(data):
    return (
        data["close"]
        .ewm(
            span=EMA_PERIOD,
            adjust=False
        )
        .mean()
    )


# ============================================================
# ADX
# ============================================================

def calculate_adx(data, period):

    high = data["high"]
    low = data["low"]
    close = data["close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where(
            (up_move > down_move) & (up_move > 0),
            up_move,
            0.0
        ),
        index=data.index
    )

    minus_dm = pd.Series(
        np.where(
            (down_move > up_move) & (down_move > 0),
            down_move,
            0.0
        ),
        index=data.index
    )

    previous_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs()
        ],
        axis=1
    ).max(axis=1)

    atr = tr.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    plus_di = (
        100
        *
        plus_dm.ewm(
            alpha=1 / period,
            adjust=False
        ).mean()
        /
        atr
    )

    minus_di = (
        100
        *
        minus_dm.ewm(
            alpha=1 / period,
            adjust=False
        ).mean()
        /
        atr
    )

    denominator = plus_di + minus_di

    dx = (
        100
        *
        (plus_di - minus_di).abs()
        /
        denominator.replace(0, np.nan)
    )

    adx = dx.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    return adx


# ============================================================
# NEW YORK SESSION
#
# Meta.GetRates returns naive broker-server timestamps, so a naive
# candle is localised to the server offset (SERVER_GMT_OFFSET) before
# being converted to New York time.
# ============================================================

NEW_YORK_TZ = ZoneInfo(SESSION_TIMEZONE)

SERVER_TZ = f"Etc/GMT-{SERVER_GMT_OFFSET}"


def is_in_new_york_session(timestamp):

    if pd.isna(timestamp):
        return False

    ts = pd.Timestamp(timestamp)

    if ts.tzinfo is None:
        ts = ts.tz_localize(SERVER_TZ)
    else:
        ts = ts.tz_convert("UTC")

    ny_time = ts.tz_convert(
        NEW_YORK_TZ
    ).time()

    start_time = time(
        SESSION_START_HOUR,
        0
    )

    end_time = time(
        SESSION_END_HOUR,
        0
    )

    return (
        start_time
        <=
        ny_time
        <
        end_time
    )


def is_in_allowed_server_hour(timestamp):
    """MQ5 InpUseHourFilter: raw broker-server hour of the entry bar."""

    if not USE_HOUR_FILTER:
        return True

    if pd.isna(timestamp):
        return False

    server_hour = pd.Timestamp(timestamp).hour

    return (
        server_hour >= HOUR_FROM
        and server_hour < HOUR_TO
    )


# ============================================================
# GET MARKET DATA
#
# Meta.GetRates returns naive broker-server timestamps. They are kept
# naive on purpose: the server-hour filter (and the session filter's
# SERVER_TZ localisation) must use the broker's server clock, exactly
# like the MQ5 EA does with ``g_rates[bar].time``.
# ============================================================

def get_data(symbol, timeframe):

    try:

        # copy_rates_from_pos ignores the local PC clock and always
        # returns the most recent N candles from the broker's own feed
        # (candle timestamps are broker-server / Cyprus time). The old
        # copy_rates_from path computed fromDate from the system clock,
        # which returned shifted/stale data whenever the PC clock was
        # wrong.
        rates = mt5.copy_rates_from_pos(
            symbol,
            timeframe,
            0,
            NUMBER_OF_DATA
        )

        data = pd.DataFrame(rates).copy()

        if data.empty:
            print("No data received")
            return None

        data["time"] = pd.to_datetime(
            data["time"],
            unit="s"
        )

        data.set_index("time", inplace=True)

        required = [
            "open",
            "high",
            "low",
            "close"
        ]

        for col in required:

            if col not in data.columns:

                print(
                    f"Missing required column: {col}"
                )

                return None

        for col in required:

            data[col] = pd.to_numeric(
                data[col],
                errors="coerce"
            )

        data.dropna(
            subset=required,
            inplace=True
        )

        data.sort_index(
            inplace=True
        )

        data["EMA"] = calculate_ema(data)

        if USE_RANGE_FILTER:

            data["ADX"] = calculate_adx(
                data,
                ADX_PERIOD
            )

        else:

            data["ADX"] = np.nan

        return data

    except BaseException as e:

        print(
            "An exception has occurred in "
            f"LastEditionSP2L.GetRates: {str(e)}"
        )

        return None


# ============================================================
# ENTRY FILTERS
#
# Mirrors the MQ5 ``EntryFiltersAreValid(bar)``:
# EMA -> ADX range -> server hour -> New York session.
# ============================================================

def entry_filters_are_valid(
    data,
    entry_pos,
    direction
):

    entry_idx = data.index[entry_pos]

    # --------------------------------------------------------
    # EMA FILTER
    # --------------------------------------------------------

    if USE_EMA_FILTER:

        entry_close = float(
            data.iloc[entry_pos]["close"]
        )

        entry_ema = float(
            data.iloc[entry_pos]["EMA"]
        )

        if not np.isfinite(entry_ema):
            return False

        if direction == "BUY":

            if entry_close <= entry_ema:
                return False

        else:

            if entry_close >= entry_ema:
                return False

    # --------------------------------------------------------
    # RANGE / ADX FILTER
    # --------------------------------------------------------

    if USE_RANGE_FILTER:

        entry_adx = float(
            data.iloc[entry_pos]["ADX"]
        )

        if not np.isfinite(entry_adx):
            return False

        if entry_adx < MIN_ADX:
            return False

    # --------------------------------------------------------
    # SERVER-HOUR FILTER (MQ5 only)
    # --------------------------------------------------------

    if not is_in_allowed_server_hour(entry_idx):
        return False

    # --------------------------------------------------------
    # NEW YORK SESSION FILTER
    # --------------------------------------------------------

    if USE_SESSION_FILTER:

        if not is_in_new_york_session(
            entry_idx
        ):
            return False

    return True


# ============================================================
# REJECTION DIAGNOSTICS
#
# When a trend has formed and the pullback happened but the trade was
# NOT opened, these helpers explain exactly which filter blocked it
# (EMA, ADX, trend structure, spread, broker stop level, price ran
# away, ...). Each unique rejection is logged once per setup/entry
# level so the console and Telegram are not spammed every loop.
# ============================================================

REJECTION_FA = {
    "EMA": "فیلتر EMA",
    "ADX": "فیلتر ADX (بازار رنج)",
    "TREND": "فیلتر ساختار روند",
    "HOUR": "فیلتر ساعت سرور",
    "SESSION": "فیلتر سشن نیویورک",
    "SPREAD": "اسپرد زیاد",
    "STOP_LEVEL": "حد ضرر/سود خیلی نزدیک به قیمت (محدودیت بروکر)",
    "PRICE_RAN": "قیمت از ناحیه ورود دور شد (پولبک تمام شد)",
    "RISK": "ریسک نامعتبر (فاصله SL)",
    "MIN_DISTANCE": "فاصله قیمت بازار تا ناحیه ورود کمتر از حد مجاز بروکر",
    "TICKET": "سفارش قبلی هنوز در انتظار است",
    "INVALID": "ستاپ نامعتبر شد (SL لمس شد یا ریسک بیش از حد)",
}

# One log per (timeframe, direction, entry level). A new trailing level
# is a new key, so a re-rejection at a better price is reported again.
last_rejection_log_keys = {}


def collect_entry_rejections(data, direction):
    """Return a list of (code, detail) for every failed entry filter."""

    rejections = []
    entry_pos = len(data) - 1

    if USE_EMA_FILTER:

        entry_close = float(data.iloc[entry_pos]["close"])
        entry_ema = float(data.iloc[entry_pos]["EMA"])

        if not np.isfinite(entry_ema):
            rejections.append(
                ("EMA", f"EMA not converged yet (EMA={entry_ema})")
            )
        elif direction == "BUY" and entry_close <= entry_ema:
            rejections.append(
                ("EMA",
                 f"close {entry_close:.{DIGITS}f} <= EMA{EMA_PERIOD} "
                 f"{entry_ema:.{DIGITS}f} (price below EMA for BUY)")
            )
        elif direction == "SELL" and entry_close >= entry_ema:
            rejections.append(
                ("EMA",
                 f"close {entry_close:.{DIGITS}f} >= EMA{EMA_PERIOD} "
                 f"{entry_ema:.{DIGITS}f} (price above EMA for SELL)")
            )

    if USE_RANGE_FILTER:

        entry_adx = float(data.iloc[entry_pos]["ADX"])

        if not np.isfinite(entry_adx):
            rejections.append(("ADX", "ADX not converged yet"))
        elif entry_adx < MIN_ADX:
            rejections.append(
                ("ADX",
                 f"ADX {entry_adx:.1f} < MIN_ADX {MIN_ADX:.1f} (ranging market)")
            )

    entry_idx = data.index[entry_pos]

    if not is_in_allowed_server_hour(entry_idx):
        rejections.append(
            ("HOUR",
             f"server hour {pd.Timestamp(entry_idx).hour:02d} outside "
             f"{HOUR_FROM:02d}:00-{HOUR_TO:02d}:00")
        )

    if USE_SESSION_FILTER and not is_in_new_york_session(entry_idx):
        rejections.append(
            ("SESSION",
             f"bar time {entry_idx} outside New York session "
             f"{SESSION_START_HOUR:02d}:00-{SESSION_END_HOUR:02d}:00")
        )

    return rejections


def collect_trend_rejections(data, start_pos, direction):
    """Explain a failed trend-structure filter with concrete numbers."""

    if USE_TREND_FILTER is False:
        return []

    consecutive_opposite = 0
    worst = 0

    for pos in range(start_pos + 1, len(data)):

        if direction == "BUY":
            current = float(data.iloc[pos]["high"])
            previous = float(data.iloc[pos - 1]["high"])
            reset = current > previous
        else:
            current = float(data.iloc[pos]["low"])
            previous = float(data.iloc[pos - 1]["low"])
            reset = current < previous

        if reset:
            consecutive_opposite = 0
        else:
            consecutive_opposite += 1
            worst = max(worst, consecutive_opposite)

    if worst > MAX_OPPOSITE_MOVES:
        return [
            ("TREND",
             f"{worst} consecutive opposite candles since the spike "
             f"(max allowed {MAX_OPPOSITE_MOVES})")
        ]

    return []


def log_pending_rejection(tf_label, pending, code, detail):
    """Throttled diagnostic: why this pending setup cannot trade yet."""

    key = (
        tf_label,
        pending["direction"],
        str(pending.get("setup_time")),
        round(float(pending["entry"]), DIGITS),
        code,
    )

    if last_rejection_log_keys.get(key) is True:
        return

    last_rejection_log_keys[key] = True

    direction = pending["direction"]
    entry = float(pending["entry"])

    message = (
        f"[{tf_label}] {direction} setup NOT taken @ {entry:.{DIGITS}f} - "
        f"{code}: {detail}"
    )

    fa_text = (
        f"[{tf_label}] ستاپ {DIRECTION_FA.get(direction, direction)} در قیمت "
        f"{entry:.{DIGITS}f} باز نشد - دلیل: "
        f"{REJECTION_FA.get(code, code)} ({detail})"
    )

    add_log(
        message,
        telegram_enabled=True,
        telegram_message=fa_text
    )


def prune_rejection_keys():
    """Keep the throttle dict bounded (one entry per setup/level/code)."""

    if len(last_rejection_log_keys) > 500:
        last_rejection_log_keys.clear()


# ============================================================
# TREND FILTER
# ============================================================

def buy_trend_is_valid(
    data,
    start_pos,
    entry_pos
):

    if not USE_TREND_FILTER:
        return True

    consecutive_opposite = 0

    for pos in range(
        start_pos + 1,
        entry_pos + 1
    ):

        current_high = float(
            data.iloc[pos]["high"]
        )

        previous_high = float(
            data.iloc[pos - 1]["high"]
        )

        if current_high > previous_high:

            consecutive_opposite = 0

        else:

            consecutive_opposite += 1

            if (
                consecutive_opposite
                >
                MAX_OPPOSITE_MOVES
            ):

                return False

    return True


def sell_trend_is_valid(
    data,
    start_pos,
    entry_pos
):

    if not USE_TREND_FILTER:
        return True

    consecutive_opposite = 0

    for pos in range(
        start_pos + 1,
        entry_pos + 1
    ):

        current_low = float(
            data.iloc[pos]["low"]
        )

        previous_low = float(
            data.iloc[pos - 1]["low"]
        )

        if current_low < previous_low:

            consecutive_opposite = 0

        else:

            consecutive_opposite += 1

            if (
                consecutive_opposite
                >
                MAX_OPPOSITE_MOVES
            ):

                return False

    return True


last_trend_log_keys = {}


def make_trend_chart(candles, tf_label, direction):
    figure, axis = plt.subplots(figsize=(4.2, 3.0), dpi=130)
    figure.patch.set_facecolor("white")
    axis.set_facecolor("white")

    for position, (_, candle) in enumerate(candles.iterrows()):
        open_price = float(candle["open"])
        close_price = float(candle["close"])
        high_price = float(candle["high"])
        low_price = float(candle["low"])
        bullish = close_price >= open_price
        color = "#159957" if bullish else "#d64545"
        axis.vlines(position, low_price, high_price, color="#263746", linewidth=1.2)
        body_bottom = min(open_price, close_price)
        body_height = max(abs(close_price - open_price), (high_price - low_price) * 0.015)
        axis.add_patch(
            plt.Rectangle(
                (position - 0.28, body_bottom),
                0.56,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.8
            )
        )

    axis.set_title(f"{tf_label} - {direction}", fontsize=10, fontweight="bold")
    axis.set_xticks(range(len(candles)))
    axis.set_xticklabels([str(index)[11:16] for index in candles.index], fontsize=7)
    axis.grid(axis="y", alpha=0.2)
    axis.margins(x=0.15, y=0.12)
    figure.tight_layout(pad=0.8)

    image = BytesIO()
    figure.savefig(image, format="png", bbox_inches="tight")
    plt.close(figure)
    image.seek(0)
    return image


def trend_candle_summary(candles):
    values = []
    for index, candle in candles.iterrows():
        values.append(
            f"{str(index)} "
            f"O={float(candle['open']):.{DIGITS}f} "
            f"H={float(candle['high']):.{DIGITS}f} "
            f"L={float(candle['low']):.{DIGITS}f} "
            f"C={float(candle['close']):.{DIGITS}f}"
        )
    return " | ".join(values)


# Market-open detection: the PC clock is unreliable, so "is the
# market open" is decided by watching whether the broker's tick time
# actually advances between loop iterations. If the tick time has not
# moved for more than MARKET_CLOSED_AFTER_SECONDS, the market is
# closed (or the feed is frozen) and no analysis/log is produced.
last_tick_time_seen = None
last_tick_progress_time = None
MARKET_CLOSED_AFTER_SECONDS = 120.0


def market_is_open():
    """True only while the broker's tick time keeps advancing."""

    global last_tick_time_seen
    global last_tick_progress_time

    try:

        tick = mt5.symbol_info_tick(SYMBOL)

        if tick is None or not tick.time:
            return False

        tick_time = int(tick.time)
        now = datetime.now()

        if last_tick_time_seen is None or tick_time > last_tick_time_seen:
            last_tick_time_seen = tick_time
            last_tick_progress_time = now
            return True

        # Tick time frozen: closed market / frozen feed.
        elapsed = (
            now - last_tick_progress_time
        ).total_seconds()

        return elapsed < MARKET_CLOSED_AFTER_SECONDS

    except BaseException as e:

        print(
            "An exception has occurred in "
            f"market_is_open: {str(e)}"
        )

        return False


def data_is_fresh(data):
    """Reject stale data before any analysis or Telegram log.

    Meta.GetRates (mt5.copy_rates_from) can return candles that are
    minutes behind the broker when the terminal feed lags. Analysing
    old candles produced "trend formed" logs for setups that were long
    over on the live chart. The broker's last tick time is used as the
    server clock and the newest bar in ``data`` must be the currently
    forming bar (or at most one bar behind).
    """

    try:

        if len(data) < 3:
            return False

        tick = mt5.symbol_info_tick(SYMBOL)

        if tick is None or not tick.time:
            return False

        period = data.index[-1] - data.index[-2]

        if period <= pd.Timedelta(0):
            return False

        now_server = pd.Timestamp(int(tick.time), unit="s")
        epoch = pd.Timestamp("1970-01-01")
        forming_open = epoch + ((now_server - epoch) // period) * period

        # The newest candle in the feed must be the forming bar itself
        # or, at most, the bar right before it. Anything older is stale.
        return data.index[-1] >= forming_open - period

    except BaseException as e:

        print(
            "An exception has occurred in "
            f"data_is_fresh: {str(e)}"
        )

        return False


def log_trend_formation(data, tf_label):
    """Log a newly formed three-candle trend without repeating each loop."""

    if not LOG_TREND_FORMATION:
        return

    required_candles = max(MIN_TREND_CANDLES, 3)
    if len(data) < required_candles + 1:
        return

    # Never announce a trend built from old candles or while the
    # market is closed.
    if not data_is_fresh(data) or not market_is_open():
        return

    # Ignore the currently forming candle and use the latest closed candles.
    candles = data.iloc[-(required_candles + 1):-1]
    bullish = all(
        float(candles.iloc[pos]["close"])
        > float(candles.iloc[pos]["open"])
        for pos in range(required_candles)
    )
    higher_lows = all(
        float(candles.iloc[pos]["low"])
        > float(candles.iloc[pos - 1]["low"])
        for pos in range(1, required_candles)
    )

    bearish = all(
        float(candles.iloc[pos]["close"])
        < float(candles.iloc[pos]["open"])
        for pos in range(required_candles)
    )
    lower_highs = all(
        float(candles.iloc[pos]["high"])
        < float(candles.iloc[pos - 1]["high"])
        for pos in range(1, required_candles)
    )

    if bullish and higher_lows:
        direction = "UPTREND"
        structure = "Higher Lows"
    elif bearish and lower_highs:
        direction = "DOWNTREND"
        structure = "Lower Highs"
    else:
        last_trend_log_keys.pop(tf_label, None)
        return

    if last_trend_log_keys.get(tf_label) == direction:
        return

    last_trend_log_keys[tf_label] = direction
    add_log(
        f"[{tf_label}] {direction} formed: "
        f"{required_candles} aligned candles with {structure}. "
        "Entry trigger: first pullback after the spike for Leg 2.",
        telegram_enabled=TELEGRAM_TREND_FORMATION,
        telegram_message=(
            f"[{tf_label}] {TREND_FA.get(direction, direction)} تشکیل شد: "
            f"{required_candles} کندل هم‌جهت با {STRUCTURE_FA.get(structure, structure)}. "
            "تریگر ورود: اولین پول‌بک بعد از اسپایک برای لگ دوم."
        )
    )

    if TELEGRAM_TREND_FORMATION and TREND_CHART_ENABLED:
        candle_summary = trend_candle_summary(candles)
        log_verbose(
            f"[{tf_label}] Trend chart OHLC: {candle_summary}"
        )
        chart = make_trend_chart(candles, tf_label, direction)
        telegram_bot.SendPhoto(
            chart,
            caption=(
                f"[{tf_label}] {TREND_FA.get(direction, direction)} تشکیل شد - "
                f"{STRUCTURE_FA.get(structure, structure)}\n"
                f"{candle_summary}"
            )
        )


# ============================================================
# SETUP DETECTION
#
# Live-trader indexing follows the same index shift used when
# converting "Simple Backtest" into "Simple Trader":
#
#   -1 = latest candle
#   -2 = candle after spike
#   -3 = spike candle
#   -4 = candle before spike
#
# The MQ5 EA uses series indexing where 0 = -1, 1 = -2, 2 = -3 and
# 3 = -4, so both implementations evaluate identical candles.
# ============================================================

def detect_buy_setup(data):

    if len(data) < 5:
        return False
    buy1 = (
        data["close"].iloc[-2]
        >
        data["close"].iloc[-3]
    )

    buy2 = (
        data["open"].iloc[-2]
        >
        data["open"].iloc[-3]
    )

    buy3 = (
        data["close"].iloc[-3]
        >
        data["close"].iloc[-4]
    )

    buy4 = (
        data["open"].iloc[-3]
        >
        data["open"].iloc[-4]
    )

    buy5 = (
        data["close"].iloc[-2]
        >
        data["open"].iloc[-2]
    )

    buy6 = (
        data["close"].iloc[-3]
        >
        data["open"].iloc[-3]
    )

    buy7 = (
        data["close"].iloc[-4]
        >
        data["open"].iloc[-4]
    )

    p_gap_buy = (
        data["low"].iloc[-2]
        >
        data["high"].iloc[-4]
        +
        P_GAP_PRICE
    )

    spike_buy = (

        (
            data["close"].iloc[-3]
            -
            data["open"].iloc[-3]
        )
        >
        SPIKE_CANDLE_SIZE
        *
        (
            data["close"].iloc[-2]
            -
            data["open"].iloc[-2]
        )

    ) & (

        (
            data["close"].iloc[-3]
            -
            data["open"].iloc[-3]
        )
        >
        SPIKE_CANDLE_SIZE
        *
        (
            data["close"].iloc[-4]
            -
            data["open"].iloc[-4]
        )

    ) & (

        (
            data["close"].iloc[-3]
            -
            data["open"].iloc[-3]
        )
        >
        SPIKE_CANDLE_SIZE
        *
        (
            data["close"].iloc[-1]
            -
            data["open"].iloc[-1]
        )
    )

    return (
        buy1
        & buy2
        & buy3
        & buy4
        & buy5
        & buy6
        & buy7
        & p_gap_buy
        & spike_buy
    )


def detect_sell_setup(data):

    if len(data) < 5:
        return False

    sell1 = (
        data["close"].iloc[-2]
        <
        data["close"].iloc[-3]
    )

    sell2 = (
        data["open"].iloc[-2]
        <
        data["open"].iloc[-3]
    )

    sell3 = (
        data["close"].iloc[-3]
        <
        data["close"].iloc[-4]
    )

    sell4 = (
        data["open"].iloc[-3]
        <
        data["open"].iloc[-4]
    )

    sell5 = (
        data["close"].iloc[-2]
        <
        data["open"].iloc[-2]
    )

    sell6 = (
        data["close"].iloc[-3]
        <
        data["open"].iloc[-3]
    )

    sell7 = (
        data["close"].iloc[-4]
        <
        data["open"].iloc[-4]
    )

    p_gap_sell = (
        data["high"].iloc[-2]
        <
        data["low"].iloc[-4]
        -
        P_GAP_PRICE
    )

    spike_sell = (

        (
            data["open"].iloc[-3]
            -
            data["close"].iloc[-3]
        )
        >
        SPIKE_CANDLE_SIZE
        *
        (
            data["open"].iloc[-2]
            -
            data["close"].iloc[-2]
        )

    ) & (

        (
            data["open"].iloc[-3]
            -
            data["close"].iloc[-3]
        )
        >
        SPIKE_CANDLE_SIZE
        *
        (
            data["open"].iloc[-4]
            -
            data["close"].iloc[-4]
        )

    ) & (

        (
            data["open"].iloc[-3]
            -
            data["close"].iloc[-3]
        )
        >
        SPIKE_CANDLE_SIZE
        *
        (
            data["open"].iloc[-1]
            -
            data["close"].iloc[-1]
        )
    )

    return (
        sell1
        & sell2
        & sell3
        & sell4
        & sell5
        & sell6
        & sell7
        & p_gap_sell
        & spike_sell
    )


# ============================================================
# PENDING SETUP
#
# A pending setup is NOT an open position. It only becomes an order
# when sync_pending_limit_order() succeeds.
# ============================================================

def create_pending_buy(data):

    # The live setup corresponds to the backtest setup row.
    setup_time = data.index[-1]

    # SL = low of the candle before the spike (MQ5 g_rates[3].low).
    sl = float(
        data["low"].iloc[-4]
    )

    spike_body = abs(
        float(data["close"].iloc[-3])
        -
        float(data["open"].iloc[-3])
    )

    return {
        "direction": "BUY",
        "setup_time": setup_time,
        "setup_pos_time": setup_time,
        # The limit starts on the previous candle's low.
        "entry": float(data["low"].iloc[-2]),
        "sl": sl,
        "spike_body": spike_body,
        "second_entry_active": False
    }


def create_pending_sell(data):

    setup_time = data.index[-1]

    # SL = high of the candle before the spike (MQ5 g_rates[3].high).
    sl = float(
        data["high"].iloc[-4]
    )

    spike_body = abs(
        float(data["open"].iloc[-3])
        -
        float(data["close"].iloc[-3])
    )

    return {
        "direction": "SELL",
        "setup_time": setup_time,
        "setup_pos_time": setup_time,
        # The limit starts on the previous candle's high.
        "entry": float(data["high"].iloc[-2]),
        "sl": sl,
        "spike_body": spike_body,
        "second_entry_active": False
    }


# ============================================================
# LIMIT PRICE TRAILING
#
# The order starts on the previous candle's low/high. A BUY limit
# follows higher lows and a SELL limit follows lower highs. A new lower
# low / higher high never causes a market entry (MQ5 TrailPendingLimit).
# ============================================================

def update_pending_limit(data, pending):

    if len(data) < 2:
        return pending

    if pending["direction"] == "BUY":

        current_low = float(data["low"].iloc[-1])

        if current_low > pending["entry"]:
            pending["entry"] = current_low

    else:

        current_high = float(data["high"].iloc[-1])

        if current_high < pending["entry"]:
            pending["entry"] = current_high

    return pending


def pending_setup_is_invalid(data, pending):

    if pending["direction"] == "BUY":
        current_price = float(data["low"].iloc[-1])
        risk = float(pending["entry"]) - float(pending["sl"])
        return (
            current_price <= float(pending["sl"])
            or risk > MAX_SL_DISTANCE_PRICE
        )

    current_price = float(data["high"].iloc[-1])
    risk = float(pending["sl"]) - float(pending["entry"])
    return (
        current_price >= float(pending["sl"])
        or risk > MAX_SL_DISTANCE_PRICE
    )


# ============================================================
# ORDER / SYMBOL HELPERS
# ============================================================

def normalize_volume(symbol, volume):
    """Clamp a volume to the symbol's step/min/max grid (MQ5 NormalizeVolume)."""

    info = mt5.symbol_info(symbol)

    if info is None:
        return volume

    step = float(getattr(info, "volume_step", 0.0) or 0.0)
    if step <= 0.0:
        step = 0.01

    vmin = float(getattr(info, "volume_min", 0.0) or 0.0)
    vmax = float(getattr(info, "volume_max", 0.0) or 0.0)

    if vmax <= 0.0:
        vmax = volume

    volume = round(volume / step) * step
    volume = min(max(volume, vmin), max(vmax, vmin))

    return round(volume, 8)


def get_pending_order(symbol, magic, direction):

    orders = mt5.orders_get(symbol=symbol)

    if orders is None:
        return None

    expected_type = (
        mt5.ORDER_TYPE_BUY_LIMIT
        if direction == "BUY"
        else mt5.ORDER_TYPE_SELL_LIMIT
    )

    matching = [
        order for order in orders
        if int(getattr(order, "magic", -1)) == int(magic)
        and int(getattr(order, "type", -1)) == expected_type
    ]

    return matching[-1] if matching else None


# Next allowed removal attempt per live order. The ticket is included so
# a new order is never throttled by an older failed removal.
pending_remove_retry_after = {}


def remove_pending_order(symbol, magic, direction, tf_label):

    order = get_pending_order(symbol, magic, direction)

    if order is None:
        return True

    ticket = int(order.ticket)
    retry_key = (symbol, int(magic), direction, ticket)
    retry_after = pending_remove_retry_after.get(retry_key)
    now = datetime.now()

    if retry_after is not None and now < retry_after:
        return False

    result = mt5.order_send(
        {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": ticket,
            "symbol": symbol,
        }
    )

    if retcode_of(result) not in (
        mt5.TRADE_RETCODE_DONE,
        mt5.TRADE_RETCODE_PLACED,
    ):
        pending_remove_retry_after[retry_key] = (
            now + timedelta(seconds=PENDING_REMOVE_RETRY_SECONDS)
        )
        add_log(
            f"[{tf_label}] Failed to remove invalid "
            f"{direction} LIMIT order #{ticket} "
            f"(retcode={retcode_of(result)} "
            f"{getattr(result, 'comment', '')}); retrying in "
            f"{PENDING_REMOVE_RETRY_SECONDS}s",
            telegram_enabled=False
        )
        return False

    pending_remove_retry_after.pop(retry_key, None)
    add_log(
        f"[{tf_label}] Removed invalid {direction} LIMIT order #{ticket}",
        send_telegram=True,
        telegram_message=(
            f"[{tf_label}] سفارش لیمیت {DIRECTION_FA.get(direction, direction)} "
            f"نامعتبر حذف شد #{ticket}"
        )
    )
    return True


def minimum_pending_distance(symbol):
    """max(trade_stops_level, trade_freeze_level) in price terms."""

    info = mt5.symbol_info(symbol)

    if info is None:
        return None

    levels = [
        int(getattr(info, "trade_stops_level", 0) or 0),
        int(getattr(info, "trade_freeze_level", 0) or 0),
    ]

    return max(levels) * float(info.point)


def stops_are_valid(symbol, direction, tf_label, price, sl, tp):
    """MQ5 StopsAreValid: broker minimum distance for SL / TP."""

    info = mt5.symbol_info(symbol)

    if info is None:
        return True

    stops = (
        int(getattr(info, "trade_stops_level", 0) or 0)
        * float(info.point)
    )

    if stops <= 0.0:
        return True

    if abs(price - sl) < stops:

        log_verbose(
            f"[{tf_label}] {direction} limit skipped: SL too close "
            f"to price ({abs(price - sl)} < {stops})"
        )
        return False

    if abs(tp - price) < stops:

        log_verbose(
            f"[{tf_label}] {direction} limit skipped: TP too close "
            f"to price ({abs(tp - price)} < {stops})"
        )
        return False

    return True


def spread_is_acceptable(symbol):
    """MQ5 InpUseSpreadFilter: do not place/refresh limits on wide spread."""

    if not USE_SPREAD_FILTER:
        return True

    info = mt5.symbol_info(symbol)

    if info is None:
        return True

    spread = int(getattr(info, "spread", 0) or 0)

    return spread <= MAX_SPREAD_POINTS


# ============================================================
# SYNC PENDING LIMIT ORDER (MQ5 SyncPendingLimitOrder)
#
# This is the ONLY trading path: the EA never sends a market entry.
# ============================================================

def sync_pending_limit_order(
    symbol,
    lot,
    magic,
    tf_label,
    data,
    pending
):

    direction = pending["direction"]

    try:
        start_pos = data.index.get_loc(
            pending["setup_pos_time"]
        )
    except KeyError:
        return None

    if direction == "BUY":
        trend_valid = buy_trend_is_valid(
            data,
            start_pos,
            len(data) - 1
        )
    else:
        trend_valid = sell_trend_is_valid(
            data,
            start_pos,
            len(data) - 1
        )

    # --------------------------------------------------------
    # DIAGNOSTICS: explain exactly why the setup cannot trade.
    # --------------------------------------------------------

    if not trend_valid:
        for code, detail in collect_trend_rejections(
            data,
            start_pos,
            direction
        ):
            log_pending_rejection(tf_label, pending, code, detail)

    filter_rejections = collect_entry_rejections(data, direction)

    if not trend_valid or filter_rejections:

        for code, detail in filter_rejections:
            log_pending_rejection(tf_label, pending, code, detail)

        return None

    price = round(float(pending["entry"]), DIGITS)
    sl = round(float(pending["sl"]), DIGITS)
    risk = price - sl if direction == "BUY" else sl - price

    if risk <= 0 or risk > MAX_SL_DISTANCE_PRICE:
        log_verbose(
            f"[{tf_label}] {direction} limit setup invalid: "
            f"entry={price}, sl={sl}"
        )
        code = "RISK"
        detail = (
            f"risk {risk:.{DIGITS}f} "
            f"(max {MAX_SL_DISTANCE_PRICE:.{DIGITS}f})"
        )
        log_pending_rejection(tf_label, pending, code, detail)
        return None

    tp = round(
        price + TP_R * risk
        if direction == "BUY"
        else price - TP_R * risk,
        DIGITS
    )

    pending["entry"] = price
    pending["tp"] = tp

    tick = mt5.symbol_info_tick(symbol)

    if tick is None:
        return None

    # Spread filter: do not (re)place limits while the spread is too wide.
    if not spread_is_acceptable(symbol):
        spread = int(getattr(mt5.symbol_info(symbol), "spread", 0) or 0)
        log_pending_rejection(
            tf_label,
            pending,
            "SPREAD",
            f"spread {spread} points > MAX_SPREAD_POINTS {MAX_SPREAD_POINTS}"
        )
        return None

    minimum_distance = minimum_pending_distance(symbol)

    if minimum_distance is None:
        return None

    market_distance = (
        float(tick.ask) - price
        if direction == "BUY"
        else price - float(tick.bid)
    )

    if market_distance < minimum_distance:
        log_pending_rejection(
            tf_label,
            pending,
            "MIN_DISTANCE",
            f"market is {market_distance:.{DIGITS}f} from the entry level, "
            f"broker minimum is {minimum_distance:.{DIGITS}f}"
        )
        return None

    order = get_pending_order(symbol, magic, direction)

    # --------------------------------------------------------
    # No live order: place one (unless a recorded ticket is still
    # believed to be pending, exactly like the EA).
    # --------------------------------------------------------

    if order is None:

        if pending.get("order_ticket") is not None:
            log_pending_rejection(
                tf_label,
                pending,
                "TICKET",
                f"order #{pending['order_ticket']} is still believed "
                "to be pending"
            )
            return None

        if (
            direction == "BUY" and price >= float(tick.ask)
        ) or (
            direction == "SELL" and price <= float(tick.bid)
        ):
            log_pending_rejection(
                tf_label,
                pending,
                "PRICE_RAN",
                f"price ran away: market ask/bid is beyond the limit "
                f"level {price:.{DIGITS}f} (pullback finished)"
            )
            return None

        if not stops_are_valid(symbol, direction, tf_label, price, sl, tp):
            log_pending_rejection(
                tf_label,
                pending,
                "STOP_LEVEL",
                f"SL {sl:.{DIGITS}f} or TP {tp:.{DIGITS}f} too close to "
                f"limit {price:.{DIGITS}f} for the broker"
            )
            return None

        result = Meta.PlacePendingOrder(
            symbol,
            normalize_volume(symbol, lot),
            direction == "BUY",
            direction == "SELL",
            price,
            sl,
            tp,
            magic=magic,
            comment=f"SP2L {tf_label} {direction} LIMIT"
        )

        if retcode_of(result) in (
            mt5.TRADE_RETCODE_DONE,
            mt5.TRADE_RETCODE_PLACED,
        ):
            pending["order_ticket"] = getattr(result, "order", None)
            add_log(
                f"[{tf_label}] {direction} LIMIT placed at {price} "
                f"(SL {sl}, TP {tp}, "
                f"#{pending['order_ticket']})",
                send_telegram=True,
                telegram_message=(
                    f"[{tf_label}] سفارش لیمیت "
                    f"{DIRECTION_FA.get(direction, direction)} ثبت شد "
                    f"در قیمت {price} "
                    f"(حد ضرر {sl}، حد سود {tp}، "
                    f"#{pending['order_ticket']})"
                )
            )
        else:
            add_log(
                f"[{tf_label}] {direction} LIMIT failed: "
                f"retcode={retcode_of(result)} "
                f"{getattr(result, 'comment', '')}",
                telegram_enabled=False
            )

        return result


    # Order already exists: only move when the limit price
    # improved, and only when the broker stop levels still allow it.
    # --------------------------------------------------------

    old_price = round(float(getattr(order, "price_open", price)), DIGITS)

    if abs(old_price - price) < BROKER_POINT / 2:
        return order

    if not stops_are_valid(symbol, direction, tf_label, price, sl, tp):
        return None

    request = {
        "action": mt5.TRADE_ACTION_MODIFY,
        "order": int(order.ticket),
        "symbol": symbol,
        "price": price,
        "sl": sl,
        "tp": tp,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }

    result = mt5.order_send(request)

    if retcode_of(result) in (
        mt5.TRADE_RETCODE_DONE,
        mt5.TRADE_RETCODE_PLACED,
    ):
        add_log(
            f"[{tf_label}] {direction} LIMIT moved "
            f"from {old_price} to {price} (#{int(order.ticket)})",
            send_telegram=True,
            telegram_message=(
                f"[{tf_label}] سفارش لیمیت "
                f"{DIRECTION_FA.get(direction, direction)} جابه‌جا شد "
                f"از {old_price} به {price} (#{int(order.ticket)})"
            )
        )
    else:
        log_verbose(
            f"[{tf_label}] {direction} LIMIT modify failed: "
            f"retcode={retcode_of(result)} "
            f"{getattr(result, 'comment', '')}"
        )

    return result


# ============================================================
# OPEN POSITION MANAGEMENT (MQ5 ManageOpenPosition)
#
# The risk is recovered from the fixed TP distance that the limit
# carried (|tp - entry| / TP_R), so no extra state has to survive a
# restart.
# ============================================================

def get_position(symbol, magic):
    """Return the live position object for this symbol/magic (or None)."""

    positions = mt5.positions_get(symbol=symbol)

    if positions is None:
        return None

    matching = [
        position for position in positions
        if int(getattr(position, "magic", -1)) == int(magic)
    ]

    return matching[-1] if matching else None


def get_trade_state(symbol, magic):

    resume = Meta.resume()

    if resume is None:
        return False, None

    if resume.shape[0] == 0:
        return False, None

    row = resume.loc[
        (resume["symbol"] == symbol)
        &
        (resume["magic"] == magic)
    ]

    if row.empty:
        return False, None

    return True, row


def position_risk(entry, tp):
    """Initial risk in price terms, recovered from the fixed TP distance."""

    if TP_R <= 0:
        return 0.0

    return abs(float(tp) - float(entry)) / TP_R


def position_stops_are_ok(symbol, price, sl):

    info = mt5.symbol_info(symbol)

    if info is None:
        return False

    stops = (
        int(getattr(info, "trade_stops_level", 0) or 0)
        * float(info.point)
    )

    if stops <= 0.0:
        return True

    return abs(price - sl) >= stops


def modify_position_stops(symbol, ticket, sl, tp):

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": int(ticket),
        "sl": sl,
        "tp": tp,
        "type_filling": Meta.FindFillingMode(symbol),
        "type_time": mt5.ORDER_TIME_GTC,
    }

    return mt5.order_send(request)


def close_position_volume(symbol, position, volume):

    tick = mt5.symbol_info_tick(symbol)

    if tick is None:
        return None

    is_buy = int(position.type) == mt5.POSITION_TYPE_BUY

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": int(position.ticket),
        "symbol": symbol,
        "volume": volume,
        "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
        "price": float(tick.bid) if is_buy else float(tick.ask),
        "deviation": SLIPPAGE_POINTS,
        "magic": int(position.magic),
        "comment": "SP2L exit",
        "type_filling": Meta.FindFillingMode(symbol),
        "type_time": mt5.ORDER_TIME_GTC,
    }

    return mt5.order_send(request)


def manage_open_position(symbol, magic, tf_label, state):
    """MQ5 ManageOpenPosition: holding time, partial TP, BE, trailing."""

    position = get_position(symbol, magic)

    if position is None:
        return

    ticket = int(position.ticket)

    if state.get("mgmt_ticket") != ticket:
        state["mgmt_ticket"] = ticket
        state["partial_done"] = False
        state["be_diag_done"] = False

    entry = float(position.price_open)
    ref_tp = float(position.tp)
    current_sl = float(position.sl)
    volume = float(position.volume)

    risk = position_risk(entry, ref_tp)

    if risk <= 0:
        return

    tick = mt5.symbol_info_tick(symbol)

    if tick is None:
        return

    is_buy = int(position.type) == mt5.POSITION_TYPE_BUY

    price = float(tick.bid) if is_buy else float(tick.ask)

    r_multiple = (
        (price - entry) / risk
        if is_buy
        else (entry - price) / risk
    )

    # --------------------------------------------------------
    # 1. MAXIMUM HOLDING TIME
    # --------------------------------------------------------

    if MAX_HOLDING_MINUTES > 0:

        holding_seconds = int(tick.time) - int(position.time)

        if holding_seconds >= MAX_HOLDING_MINUTES * 60:

            result = close_position_volume(symbol, position, volume)

            if retcode_of(result) == mt5.TRADE_RETCODE_DONE:

                add_log(
                    f"[{tf_label}] Position #{ticket} closed: "
                    f"max holding time ({MAX_HOLDING_MINUTES} min) "
                    "reached",
                    send_telegram=True,
                    telegram_message=(
                        f"[{tf_label}] پوزیشن #{ticket} بسته شد: "
                        f"به حداکثر زمان نگه‌داری "
                        f"({MAX_HOLDING_MINUTES} دقیقه) رسید"
                    )
                )

            return

    # --------------------------------------------------------
    # 2. PARTIAL TAKE PROFIT
    # --------------------------------------------------------

    if (
        USE_PARTIAL_TP
        and not state.get("partial_done", False)
        and r_multiple >= PARTIAL_AT_R
    ):

        info = mt5.symbol_info(symbol)

        step = float(getattr(info, "volume_step", 0.0) or 0.0)
        if step <= 0.0:
            step = 0.01

        vmin = float(getattr(info, "volume_min", 0.0) or 0.0)

        # Round the partial volume onto the step grid, then never let it
        # consume the whole position.
        part = round(
            math.floor(
                (volume * PARTIAL_PERCENT / 100.0) / step + 0.5
            ) * step,
            8
        )

        rest = round(volume - part, 8)

        if part >= vmin and rest >= vmin:

            result = close_position_volume(symbol, position, part)

            if retcode_of(result) == mt5.TRADE_RETCODE_DONE:

                state["partial_done"] = True

                add_log(
                    f"[{tf_label}] Position #{ticket} partial close "
                    f"{part} lots at {r_multiple:.2f}R",
                    send_telegram=True,
                    telegram_message=(
                        f"[{tf_label}] بستن جزئی پوزیشن #{ticket}: "
                        f"{part} لات در {r_multiple:.2f}R"
                    )
                )

            else:

                log_verbose(
                    f"[{tf_label}] Position #{ticket} partial close "
                    f"failed: retcode={retcode_of(result)} "
                    f"{getattr(result, 'comment', '')}"
                )

        else:

            # Position too small to split: never retry every loop.
            state["partial_done"] = True


    # --------------------------------------------------------
    # 3. BREAKEVEN
    # --------------------------------------------------------

    if USE_BREAKEVEN and r_multiple >= BREAKEVEN_AT_R:

        buffer = BREAKEVEN_BUFFER_POINTS * BROKER_POINT

        new_sl = round(
            entry + buffer if is_buy else entry - buffer,
            DIGITS
        )

        improves = (
            new_sl > current_sl + BROKER_POINT / 2
            if is_buy
            else (
                current_sl == 0
                or new_sl < current_sl - BROKER_POINT / 2
            )
        )

        if improves and position_stops_are_ok(symbol, price, new_sl):

            result = modify_position_stops(
                symbol,
                ticket,
                new_sl,
                ref_tp
            )

            if retcode_of(result) in (
                mt5.TRADE_RETCODE_DONE,
                mt5.TRADE_RETCODE_PLACED,
            ):

                add_log(
                    f"[{tf_label}] Position #{ticket} SL -> "
                    f"breakeven {new_sl}",
                    send_telegram=True,
                    telegram_message=(
                        f"[{tf_label}] پوزیشن #{ticket} حد ضرر به "
                        f"نقطه سربه‌سر منتقل شد: {new_sl}"
                    )
                )

            elif not state.get("be_diag_done", False):

                state["be_diag_done"] = True

                log_verbose(
                    f"[{tf_label}] Position #{ticket} BE modify FAILED "
                    f"retcode={retcode_of(result)} "
                    f"{getattr(result, 'comment', '')} "
                    f"(price={price}, sl={current_sl}, newSl={new_sl})"
                )

    # --------------------------------------------------------
    # 4. TRAILING STOP
    # --------------------------------------------------------

    if USE_TRAIL_STOP and r_multiple >= TRAIL_START_R:

        new_sl = round(
            price - TRAIL_DISTANCE_R * risk
            if is_buy
            else price + TRAIL_DISTANCE_R * risk,
            DIGITS
        )

        improves = (
            new_sl > current_sl + BROKER_POINT / 2
            if is_buy
            else (
                current_sl == 0
                or new_sl < current_sl - BROKER_POINT / 2
            )
        )

        if improves and position_stops_are_ok(symbol, price, new_sl):

            result = modify_position_stops(
                symbol,
                ticket,
                new_sl,
                ref_tp
            )

            if retcode_of(result) in (
                mt5.TRADE_RETCODE_DONE,
                mt5.TRADE_RETCODE_PLACED,
            ):

                add_log(
                    f"[{tf_label}] Position #{ticket} SL trailed "
                    f"to {new_sl}",
                    send_telegram=True,
                    telegram_message=(
                        f"[{tf_label}] حد ضرر پوزیشن #{ticket} "
                        f"دنبال‌کننده شد به {new_sl}"
                    )
                )


# ============================================================
# STRATEGY (MQ5 RunStrategy, step 1 + 2)
#
# Returns:
#
#   preBuy
#   preSell
#   status
#   sl
#   tp
#   pending_setup
#   trade_setup
#
# pending_setup is deliberately kept separate from status: a pending
# setup is NOT an open position.
#
# ``trade_setup`` is always None here, exactly like the EA: the only
# execution path is the pending limit order, so the Advanced bot's
# market-entry block was removed instead of ported.
# ============================================================

def Strategy(
    symbol,
    timeframe,
    tf_label,
    preBuy,
    preSell,
    status,
    pending_setup
):

    sl = 0
    tp = 0
    trade_setup = None

    data = get_data(symbol, timeframe)

    if data is None:
        return (
            preBuy,
            preSell,
            status,
            sl,
            tp,
            pending_setup,
            trade_setup
        )

    # Stale feed or closed market: do not analyse and do not
    # create/keep setups.
    if not data_is_fresh(data) or not market_is_open():

        global last_market_closed_log_time

        now = datetime.now()

        if (
            last_market_closed_log_time is None
            or (now - last_market_closed_log_time).total_seconds()
            >= MARKET_CLOSED_LOG_INTERVAL
        ):

            print(
                f"[{now:%Y-%m-%d %H:%M:%S}] "
                "Market closed / feed frozen - analysis paused, "
                "waiting for ticks..."
            )

            last_market_closed_log_time = now

        return (
            preBuy,
            preSell,
            status,
            sl,
            tp,
            None,
            trade_setup
        )

    log_trend_formation(data, tf_label)

    # ========================================================
    # If a position is already open, do not search for another
    # setup.
    # ========================================================

    if status:

        return (
            preBuy,
            preSell,
            status,
            sl,
            tp,
            pending_setup,
            trade_setup
        )

    # ========================================================
    # PENDING SETUP
    #
    # This is the key difference from Simple Trader:
    # the setup waits for the first valid entry.
    # ========================================================

    if pending_setup is not None:

        update_pending_limit(data, pending_setup)

        direction = pending_setup["direction"]
        entry = float(pending_setup["entry"])
        sl = float(pending_setup["sl"])
        risk = (
            entry - sl
            if direction == "BUY"
            else sl - entry
        )

        if risk <= 0 or risk > MAX_SL_DISTANCE_PRICE:
            pending_setup = None
        else:
            tp = (
                entry + TP_R * risk
                if direction == "BUY"
                else entry - TP_R * risk
            )
            pending_setup["tp"] = tp

    # ========================================================
    # LOOK FOR A NEW SETUP
    #
    # A setup is only created here. It is NOT an entry.
    # The direction filter is applied before detection, exactly like
    # the EA does with InpTradeSide.
    # ========================================================

    buy = (
        TRADE_SIDE != "SHORT_ONLY"
        and bool(detect_buy_setup(data))
    )

    sell = (
        TRADE_SIDE != "LONG_ONLY"
        and bool(detect_sell_setup(data))
    )

    if buy and not sell:

        pending_setup = create_pending_buy(
            data
        )

    elif sell and not buy:

        pending_setup = create_pending_sell(
            data
        )

    return (
        preBuy,
        preSell,
        status,
        sl,
        tp,
        pending_setup,
        trade_setup
    )


# ============================================================
# ACCOUNT INFORMATION
# ============================================================

accountInfo = mt5.account_info()

print("-" * 75)

if accountInfo is not None:

    print(
        f"Login: {accountInfo.login}"
        f"\tserver: {accountInfo.server}"
        f"\tleverage: {accountInfo.leverage}"
    )

    print(
        f"Balance: {accountInfo.balance}"
        f"\tEquity: {accountInfo.equity}"
        f"\tProfit: {accountInfo.profit}"
    )

print(
    "Run time:",
    datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
)

print("-" * 75)

if USE_SECOND_ENTRY:
    add_log(
        "NOTE: USE_SECOND_ENTRY is enabled, but the original bot never "
        "placed a second order (it only logged it). "
        "No second entry is executed.",
        telegram_enabled=False
    )


# ============================================================
# SYMBOLS
# ============================================================

symbols_list = {
    SYMBOL: [SYMBOL, LOT],
}


# ============================================================
# INITIAL STATE
#
# One independent state block per enabled timeframe so M1, M5 and M15
# never share status / pending setups / magic numbers.
#
# The management fields mirror the EA's per-instance globals:
# ``mgmt_ticket`` (g_mgmtTicket), ``partial_done`` (g_partialDone) and
# ``be_diag_done`` (g_beDiagDone).
#
# ``buy`` / ``sell`` are kept because the Advanced body's ``Strategy()``
# signature takes and returns them. They are never set to True: the only
# execution path is the pending limit order, so there is no market entry
# to flag.
# ============================================================

tf_states = {}

for tf_name, tf_cfg in TIMEFRAMES.items():

    tf_states[tf_name] = {
        "buy": False,
        "sell": False,
        "status": False,
        "pending_setup": None,
        # Per-timeframe cooldown after a position closes. This pauses
        # only this timeframe, not the whole loop.
        "cooldown_until": None,
        "mgmt_ticket": None,
        "partial_done": False,
        "be_diag_done": False,
    }

# Throttle for the "MT5 disconnected" notice so it does not spam.
last_disconnect_log_time = None
DISCONNECT_LOG_INTERVAL = 30.0

# Throttle for the "market closed" console notice.
last_market_closed_log_time = None
MARKET_CLOSED_LOG_INTERVAL = 300.0


# ============================================================
# MAIN LOOP
#
# Mirrors the EA's OnTick order per timeframe:
#
#   1. an existing position -> resync status, manage it, done
#   2. status set but no position -> position closed, cooldown
#   3. still in cooldown -> skip this timeframe
#   4. run the strategy: trail -> detect -> invalidate/remove or sync
# ============================================================

while True:

    if mt5_is_connected() is True:

        prune_rejection_keys()

        for asset in symbols_list.keys():

            symbol = symbols_list[asset][0]
            lot = symbols_list[asset][1]

            selected = mt5.symbol_select(
                symbol
            )

            if not selected:

                print(
                    f"\nERROR - Failed to select "
                    f"'{symbol}' in MetaTrader 5 "
                    f"with error :",
                    mt5.last_error()
                )

                continue

            # =================================================
            # LOOP OVER EACH ENABLED TIMEFRAME INDEPENDENTLY
            # =================================================

            for tf_label, tf_cfg in TIMEFRAMES.items():

                if not tf_cfg["enabled"]:
                    continue

                timeframe = tf_cfg["mt5"]
                magic = tf_cfg["magic"]

                state = tf_states[tf_label]

                buy = state["buy"]
                sell = state["sell"]
                status = state["status"]
                pending_setup = state["pending_setup"]
                cooldown_until = state.get("cooldown_until")

                # =============================================
                # CHECK EXISTING POSITION (for this magic)
                # =============================================

                position_exists, row = get_trade_state(
                    symbol,
                    magic
                )

                # ---------------------------------------------
                # Position is open: manage it and stop scanning
                # this timeframe (MQ5: OnTick -> ManageOpenPosition).
                # ---------------------------------------------

                if position_exists:

                    if not status:

                        add_log(
                            f"[{tf_label}] Position detected but the "
                            "status key was False (a limit filled) - "
                            "state resynchronised",
                            send_telegram=True,
                            telegram_message=(
                                f"[{tf_label}] پوزیشن شناسایی شد ولی "
                                "وضعیت ثبت‌شده False بود (لیمیت پر شد) - "
                                "وضعیت همگام‌سازی شد"
                            )
                        )

                        status = True
                        pending_setup = None

                        state["mgmt_ticket"] = None
                        state["partial_done"] = False
                        state["be_diag_done"] = False

                    manage_open_position(
                        symbol,
                        magic,
                        tf_label,
                        state
                    )

                    tf_states[tf_label] = {
                        "buy": buy,
                        "sell": sell,
                        "status": status,
                        "pending_setup": pending_setup,
                        "cooldown_until": cooldown_until,
                        "mgmt_ticket": state.get("mgmt_ticket"),
                        "partial_done": state.get("partial_done", False),
                        "be_diag_done": state.get("be_diag_done", False),
                    }

                    continue


                # ---------------------------------------------
                # Stop loss / take profit / manual close
                # ---------------------------------------------

                if status:

                    status = False
                    buy = False
                    sell = False
                    pending_setup = None

                    state["mgmt_ticket"] = None
                    state["partial_done"] = False
                    state["be_diag_done"] = False

                    # Pause only this timeframe.
                    cooldown_until = (
                        datetime.now()
                        + timedelta(seconds=COOLDOWN_SECONDS)
                    )

                    add_log(
                        f"[{tf_label}] Strategy "
                        f"{Fore.YELLOW}"
                        f"Position closed / SL or TP hit - "
                        f"cooldown started"
                        f"{Style.RESET_ALL}",
                        send_telegram=True,
                        telegram_message=(
                            f"[{tf_label}] پوزیشن بسته شد / حد ضرر یا "
                            "حد سود فعال شد - دوره انتظار شروع شد"
                        )
                    )

                    tf_states[tf_label] = {
                        "buy": buy,
                        "sell": sell,
                        "status": status,
                        "pending_setup": pending_setup,
                        "cooldown_until": cooldown_until,
                        "mgmt_ticket": None,
                        "partial_done": False,
                        "be_diag_done": False,
                    }

                    continue

                # =============================================
                # PER-TIMEFRAME COOLDOWN
                #
                # Other timeframes keep scanning while this one waits.
                # =============================================

                if cooldown_until is not None:

                    if datetime.now() < cooldown_until:
                        continue

                    cooldown_until = None

                # =============================================
                # STRATEGY (MQ5 RunStrategy)
                # =============================================

                (
                    buy,
                    sell,
                    status,
                    sl,
                    tp,
                    pending_setup,
                    trade_setup
                ) = Strategy(
                    symbol,
                    timeframe,
                    tf_label,
                    buy,
                    sell,
                    status,
                    pending_setup
                )

                # Keep the entry passive: place the limit at the
                # previous candle low/high and move it only when the
                # new candle improves that level.
                if (
                    pending_setup is not None
                    and not status
                ):

                    pending_data = get_data(
                        symbol,
                        timeframe
                    )

                    if pending_data is not None:

                        if pending_setup_is_invalid(
                            pending_data,
                            pending_setup
                        ):

                            if remove_pending_order(
                                symbol,
                                magic,
                                pending_setup["direction"],
                                tf_label
                            ):

                                if pending_setup["direction"] == "BUY":
                                    invalid_detail = (
                                        f"price low "
                                        f"{float(pending_data['low'].iloc[-1]):.{DIGITS}f} "
                                        f"touched SL "
                                        f"{float(pending_setup['sl']):.{DIGITS}f}"
                                    )
                                else:
                                    invalid_detail = (
                                        f"price high "
                                        f"{float(pending_data['high'].iloc[-1]):.{DIGITS}f} "
                                        f"touched SL "
                                        f"{float(pending_setup['sl']):.{DIGITS}f}"
                                    )

                                log_pending_rejection(
                                    tf_label,
                                    pending_setup,
                                    "INVALID",
                                    invalid_detail
                                )

                                pending_setup = None

                        else:

                            sync_pending_limit_order(
                                symbol,
                                lot,
                                magic,
                                tf_label,
                                pending_data,
                                pending_setup
                            )


                # =============================================
                # PERSIST THIS TIMEFRAME'S STATE
                # =============================================

                tf_states[tf_label] = {
                    "buy": buy,
                    "sell": sell,
                    "status": status,
                    "pending_setup": pending_setup,
                    "cooldown_until": cooldown_until,
                    "mgmt_ticket": state.get("mgmt_ticket"),
                    "partial_done": state.get("partial_done", False),
                    "be_diag_done": state.get("be_diag_done", False),
                }

    else:

        # MT5 is not connected. Log a throttled notice so the console
        # does not get spammed every loop iteration.
        now = datetime.now()

        if (
            last_disconnect_log_time is None
            or (now - last_disconnect_log_time).total_seconds()
            >= DISCONNECT_LOG_INTERVAL
        ):

            add_log(
                f"{Fore.YELLOW}"
                f"MT5 is not connected. Waiting for the "
                f"terminal/broker connection..."
                f"{Style.RESET_ALL}",
                telegram_message=(
                    "اتصال متاتریدر ۵ قطع است. در انتظار اتصال "
                    "ترمینال/بروکر..."
                )
            )

            last_disconnect_log_time = now

    time_module.sleep(
        LOOP_SECONDS
    )
