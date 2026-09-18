#!/usr/bin/env python
__author__ = "Alireza Sadabadi"
__copyright__ = "Copyright (c) 2026 Alireza Sadabadi. All rights reserved."
__credits__ = ["Alireza Sadabadi"]
__license__ = "Apache"
__version__ = "4.0"
__maintainer__ = "Alireza Sadabadi"
__email__ = "alirezasadabady@gmail.com"
__status__ = "Test"
__doc__ = "you can see the tutorials in https://youtube.com/@alirezasadabadi?si=d8o7LK_Ai1Hf68is"

import MetaTrader5 as mt5
from datetime import datetime, timezone, time, timedelta
import time as time_module
from zoneinfo import ZoneInfo
from Meta import *
from TelegramBot import TeleBot
from colorama import init as colorama_init
from colorama import Fore
from colorama import Style
import socket
import sys
import pandas as pd
import numpy as np

colorama_init()

# ============================================================
# LOGGING / TELEGRAM LOG SYSTEM
# (ported from main.py: add_log + rate-limited Telegram alerts)
# ============================================================

logs = []

# Only explicitly marked outcome logs are mirrored to Telegram.
TELEGRAMLOG_FOR_STATAS = True

# Trend-formation logs can be controlled independently from trade logs.
LOG_TREND_FORMATION = True
TELEGRAM_TREND_FORMATION = True
MIN_TREND_CANDLES = 3

# Hard alerts (entries, errors, closes) are always sent to Telegram.
# Minimum seconds between two Telegram log messages.
TELEGRAM_LOG_INTERVAL = 1.0

telegram_bot = TeleBot()
last_telegram_log_time = None


def add_log(message, send_telegram=False, telegram_enabled=None):
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
            telegram_bot.SendMessage(f"🤖 SP2L Bot:\n{log_entry}")
            last_telegram_log_time = now


# ============================================================
# MT5 INITIALIZE
# ============================================================

if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    mt5.shutdown()
    quit()

# Mirror Meta-layer execution messages (opens/closes/errors) to Telegram.
Meta.teleBotMessage = True


# ============================================================
# INTERNET CHECK
# ============================================================

def internet(host="8.8.8.8", port=53, timeout=3):
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM
        ).connect((host, port))
        return True

    except socket.error:
        print("@", end="")
        sys.stdout.flush()
        return False


# ============================================================
# MT5 CONNECTION CHECK
#
# The raw socket test above (internet()) can fail even when the
# bot is perfectly able to trade, for example when traffic is
# routed through a proxy/VPN or when the ISP blocks outbound
# TCP port 53. MT5 connects to the broker through its own
# channel, so the real connectivity requirement is the MT5
# terminal/account connection, not a socket to Google DNS.
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
NUMBER_OF_DATA = 500

# ------------------------------------------------------------
# Base magic number. Each timeframe uses MAGIC + its own
# offset (see TIMEFRAMES below) so positions/state never
# conflict between timeframes.
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

SPIKE_CANDLE_SIZE = 1.25

PGAP_POINTS = 150
MAX_SL_DISTANCE_POINTS = 1000

TP_R = 3.0

# ------------------------------------------------------------
# Second entry
# ------------------------------------------------------------

USE_SECOND_ENTRY = True

SECOND_ENTRY_VOLUME_MULTIPLIER = 2.0

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
# New York session filter
# ------------------------------------------------------------

USE_SESSION_FILTER = False

SESSION_START_HOUR = 1
SESSION_END_HOUR = 5

SESSION_TIMEZONE = "America/New_York"

# ------------------------------------------------------------
# Trading
# ------------------------------------------------------------

# NOTE: the base MAGIC is defined above, before TIMEFRAMES,
# because each timeframe's magic is derived from it.
LOT = 0.1

LOOP_SECONDS = 2

# A broker can reject removing a pending order while the symbol is
# closed or the order is temporarily frozen. Keep the setup for a
# later retry, but do not hammer the trade server every loop.
PENDING_REMOVE_RETRY_SECONDS = 60

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
print("ADVANCED TRADER")
print("-" * 75)
print("Symbol              :", SYMBOL)
print("Point               :", BROKER_POINT)
print("Digits              :", DIGITS)
print("Spike multiplier    :", SPIKE_CANDLE_SIZE)
print("Gap points          :", PGAP_POINTS)
print("Max SL points       :", MAX_SL_DISTANCE_POINTS)
print("TP                  :", f"{TP_R}R")
print("Second entry        :", USE_SECOND_ENTRY)
print("Second entry volume :", SECOND_ENTRY_VOLUME_MULTIPLIER)
print("EMA filter          :", USE_EMA_FILTER)
print("EMA period          :", EMA_PERIOD)
print("Trend filter        :", USE_TREND_FILTER)
print("Max opposite moves  :", MAX_OPPOSITE_MOVES)
print("Trend formation log :", LOG_TREND_FORMATION)
print("Trend log Telegram  :", TELEGRAM_TREND_FORMATION)
print("Minimum trend bars  :", MIN_TREND_CANDLES)
print("Range filter        :", USE_RANGE_FILTER)
print("ADX period          :", ADX_PERIOD)
print("Minimum ADX         :", MIN_ADX)
print("Session filter      :", USE_SESSION_FILTER)
print("Session timezone    :", SESSION_TIMEZONE)
print(
    "Session             :",
    f"{SESSION_START_HOUR:02d}:00 - {SESSION_END_HOUR:02d}:00"
)
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
# ============================================================

NEW_YORK_TZ = ZoneInfo(SESSION_TIMEZONE)


def is_in_new_york_session(timestamp):

    if pd.isna(timestamp):
        return False

    ts = pd.Timestamp(timestamp)

    if ts.tzinfo is None:
        ts = ts.tz_localize("Etc/GMT-3")
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


# ============================================================
# GET MARKET DATA
# ============================================================

def get_data(symbol, timeframe):

    try:

        data = Meta.GetRates(
            symbol,
            NUMBER_OF_DATA,
            timeFrame=timeframe
        ).copy()

        if data.empty:
            print("No data received")
            return None

        data.columns = [
            str(c).lower()
            for c in data.columns
        ]

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

        if not isinstance(
            data.index,
            pd.DatetimeIndex
        ):

            possible_time_columns = [
                "time",
                "datetime",
                "date",
                "local time"
            ]

            found_time = None

            for c in possible_time_columns:

                if c in data.columns:
                    found_time = c
                    break

            if found_time is not None:

                data[found_time] = pd.to_datetime(
                    data[found_time]
                )

                data.set_index(
                    found_time,
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
            f"AdvancedTrader.GetRates: {str(e)}"
        )

        return None


# ============================================================
# ENTRY FILTERS
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
    # NEW YORK SESSION FILTER
    # --------------------------------------------------------

    if USE_SESSION_FILTER:

        if not is_in_new_york_session(
            entry_idx
        ):
            return False

    return True


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


def log_trend_formation(data, tf_label):
    """Log a newly formed three-candle trend without repeating each loop."""

    if not LOG_TREND_FORMATION:
        return

    required_candles = max(MIN_TREND_CANDLES, 3)
    if len(data) < required_candles + 1:
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
        telegram_enabled=TELEGRAM_TREND_FORMATION
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
# The current candle is used exactly as in the live trader style.
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
# ============================================================

def create_pending_buy(data):

    # The live setup corresponds to the backtest setup row.
    setup_time = data.index[-1]

    # In the advanced backtest the BUY SL is the low of the
    # candle before the spike.
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
        "entry": float(data["low"].iloc[-2]),
        "sl": sl,
        "spike_body": spike_body,
        "second_entry_active": False
    }


def create_pending_sell(data):

    setup_time = data.index[-1]

    # In the advanced backtest the SELL SL is the high of the
    # candle before the spike.
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
        "entry": float(data["high"].iloc[-2]),
        "sl": sl,
        "spike_body": spike_body,
        "second_entry_active": False
    }


# ============================================================
# LIMIT PRICE TRAILING
#
# The order starts on the previous candle's low/high. A BUY
# limit follows higher lows and a SELL limit follows lower highs.
# A new lower low/higher high never causes a market entry.
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

    if result is None or getattr(result, "retcode", None) not in (
        mt5.TRADE_RETCODE_DONE,
        mt5.TRADE_RETCODE_PLACED,
    ):
        pending_remove_retry_after[retry_key] = (
            now + timedelta(seconds=PENDING_REMOVE_RETRY_SECONDS)
        )
        retcode = getattr(result, "retcode", None)
        add_log(
            f"[{tf_label}] Failed to remove invalid "
            f"{direction} LIMIT order {ticket} "
            f"(retcode={retcode}); retrying in "
            f"{PENDING_REMOVE_RETRY_SECONDS}s"
        )
        return False

    pending_remove_retry_after.pop(retry_key, None)
    add_log(
        f"[{tf_label}] Removed invalid {direction} LIMIT order",
        send_telegram=True
    )
    return True


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


def minimum_pending_distance(symbol):

    info = mt5.symbol_info(symbol)

    if info is None:
        return None

    levels = [
        int(getattr(info, "trade_stops_level", 0) or 0),
        int(getattr(info, "trade_freeze_level", 0) or 0),
    ]

    return max(levels) * float(info.point)


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

    if (
        not trend_valid
        or not entry_filters_are_valid(
            data,
            len(data) - 1,
            direction
        )
    ):
        return None

    price = round(float(pending["entry"]), DIGITS)
    sl = round(float(pending["sl"]), DIGITS)
    risk = price - sl if direction == "BUY" else sl - price

    if risk <= 0 or risk > MAX_SL_DISTANCE_PRICE:
        add_log(
            f"[{tf_label}] {direction} limit setup invalid: "
            f"entry={price}, sl={sl}"
        )
        return None

    tp = round(
        price + TP_R * risk
        if direction == "BUY"
        else price - TP_R * risk,
        DIGITS
    )

    pending["entry"] = price
    pending["tp"] = tp

    order = get_pending_order(symbol, magic, direction)
    tick = mt5.symbol_info_tick(symbol)

    if tick is None:
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
        return None

    if order is None:

        if pending.get("order_ticket") is not None:
            return None

        if (
            direction == "BUY" and price >= float(tick.ask)
        ) or (
            direction == "SELL" and price <= float(tick.bid)
        ):
            return None

        result = Meta.PlacePendingOrder(
            symbol,
            lot,
            direction == "BUY",
            direction == "SELL",
            price,
            sl,
            tp,
            magic=magic,
            comment=f"SP2L {tf_label} {direction} LIMIT"
        )

        if result is not None and getattr(result, "retcode", None) in (
            mt5.TRADE_RETCODE_DONE,
            mt5.TRADE_RETCODE_PLACED,
        ):
            pending["order_ticket"] = getattr(
                result,
                "order",
                None
            )
            add_log(
                f"[{tf_label}] {direction} LIMIT placed at {price}",
                send_telegram=True
            )

        return result

    old_price = round(float(getattr(order, "price_open", price)), DIGITS)

    if old_price == price:
        return order

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

    if result is not None and getattr(result, "retcode", None) in (
        mt5.TRADE_RETCODE_DONE,
        mt5.TRADE_RETCODE_PLACED,
    ):
        add_log(
            f"[{tf_label}] {direction} LIMIT moved "
            f"from {old_price} to {price}",
            send_telegram=True
        )

    return result


def check_pending_buy(
    data,
    pending
):

    if len(data) < 2:
        return None

    sl = pending["sl"]

    current_low = float(
        data["low"].iloc[-1]
    )

    previous_low = float(
        data["low"].iloc[-2]
    )

    if current_low >= previous_low:
        return None

    risk = current_low - sl

    if risk <= 0:
        return None

    if risk > MAX_SL_DISTANCE_PRICE:
        return "INVALID"

    # Find the setup candle in the current data.
    try:
        start_pos = data.index.get_loc(
            pending["setup_pos_time"]
        )
    except KeyError:
        return None

    entry_pos = len(data) - 1

    if not buy_trend_is_valid(
        data,
        start_pos,
        entry_pos
    ):
        return None

    if not entry_filters_are_valid(
        data,
        entry_pos,
        "BUY"
    ):
        return None

    return {
        "direction": "BUY",
        "entry": current_low,
        "sl": sl,
        "risk": risk,
        "setup_time": pending["setup_time"],
        "entry_time": data.index[-1],
        "spike_body": pending["spike_body"]
    }


# ============================================================
# FIND FIRST VALID SELL ENTRY
# ============================================================

def check_pending_sell(
    data,
    pending
):

    if len(data) < 2:
        return None

    sl = pending["sl"]

    current_high = float(
        data["high"].iloc[-1]
    )

    previous_high = float(
        data["high"].iloc[-2]
    )

    if current_high <= previous_high:
        return None

    risk = sl - current_high

    if risk <= 0:
        return None

    if risk > MAX_SL_DISTANCE_PRICE:
        return "INVALID"

    try:
        start_pos = data.index.get_loc(
            pending["setup_pos_time"]
        )
    except KeyError:
        return None

    entry_pos = len(data) - 1

    if not sell_trend_is_valid(
        data,
        start_pos,
        entry_pos
    ):
        return None

    if not entry_filters_are_valid(
        data,
        entry_pos,
        "SELL"
    ):
        return None

    return {
        "direction": "SELL",
        "entry": current_high,
        "sl": sl,
        "risk": risk,
        "setup_time": pending["setup_time"],
        "entry_time": data.index[-1],
        "spike_body": pending["spike_body"]
    }


# ============================================================
# SECOND ENTRY
# ============================================================

def get_second_entry(
    direction,
    entry,
    risk
):

    if direction == "BUY":

        return entry - risk / 2

    return entry + risk / 2


# ============================================================
# TRADE STATE
# ============================================================

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


# ============================================================
# ADVANCED STRATEGY
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
# pending_setup is deliberately kept separate from status.
# A pending setup is NOT an open position.
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
    # This is the key difference from Simple Trader.
    # The setup waits for the first valid entry.
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
    # A setup is only created here.
    # It is NOT an entry.
    # ========================================================

    buy = detect_buy_setup(data)
    sell = detect_sell_setup(data)

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


# ============================================================
# SYMBOLS
# ============================================================

symbols_list = {
    SYMBOL: [SYMBOL, LOT],
}


# ============================================================
# INITIAL STATE
#
# One independent state block per enabled timeframe so M1, M5
# and M15 never share status / pending setups / magic numbers.
# ============================================================

tf_states = {}

for tf_name, tf_cfg in TIMEFRAMES.items():

    tf_states[tf_name] = {
        "buy": False,
        "sell": False,
        "status": False,
        "pending_setup": None,
        # Per-timeframe cooldown after a position closes. This
        # pauses only this timeframe, not the whole loop.
        "cooldown_until": None,
    }

# Used to prevent detecting the same live setup repeatedly.
last_setup_time = None

# Used to prevent processing the same live candle repeatedly.
last_processed_candle = None

# Throttle for the "MT5 disconnected" notice so it does not spam.
last_disconnect_log_time = None
DISCONNECT_LOG_INTERVAL = 30.0

# Next allowed removal attempt per live order. The ticket is included
# so a new order is never throttled by an older failed removal.
pending_remove_retry_after = {}

# ============================================================
# MAIN LOOP
# ============================================================

while True:

    if mt5_is_connected() is True:

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
                # PER-TIMEFRAME COOLDOWN
                #
                # After a position closes, this timeframe pauses
                # for a while. Other timeframes keep scanning.
                # =============================================

                if cooldown_until is not None:

                    if datetime.now() < cooldown_until:
                        continue

                    cooldown_until = None

                # =============================================
                # CHECK EXISTING POSITION (for this magic)
                # =============================================

                position_exists, row = get_trade_state(
                    symbol,
                    magic
                )

                # ---------------------------------------------
                # Stop loss / position closed
                # ---------------------------------------------

                if not position_exists and status:

                    status = False
                    buy = False
                    sell = False
                    pending_setup = None

                    add_log(
                        f"[{tf_label}] Strategy "
                        f"{Fore.YELLOW}"
                        f"Position closed / SL or TP hit!"
                        f"{Style.RESET_ALL}",
                        send_telegram=True
                    )

                    # Pause only this timeframe for 60 seconds.
                    cooldown_until = (
                        datetime.now()
                        + timedelta(seconds=60)
                    )

                    tf_states[tf_label] = {
                        "buy": buy,
                        "sell": sell,
                        "status": status,
                        "pending_setup": pending_setup,
                        "cooldown_until": cooldown_until,
                    }

                    continue

                # ---------------------------------------------
                # Abnormal open position
                # ---------------------------------------------

                elif position_exists and not status:

                    add_log(
                        f"[{tf_label}] Abnormally position: "
                        "you have an open position "
                        "with Advanced Trader "
                        "but the status key is False!!",
                        send_telegram=True
                    )

                    status = True
                    pending_setup = None

                # =============================================
                # STRATEGY
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
                # previous candle low/high and move it only when
                # the new candle improves that level.
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
                # EXECUTE ENTRY 1
                # =============================================

                if trade_setup is not None:

                    direction = trade_setup["direction"]

                    entry = trade_setup["entry"]
                    sl = trade_setup["sl"]
                    tp = trade_setup["tp"]

                    print()
                    print("-" * 75)
                    print(
                        f"{Fore.GREEN if buy == True else Fore.RED}"
                        f"[{tf_label}] VALID {direction} ENTRY"
                        f"{Style.RESET_ALL}"
                    )

                    print(
                        "Setup time :",
                        trade_setup["setup_time"]
                    )

                    print(
                        "Entry time :",
                        trade_setup["entry_time"]
                    )

                    print(
                        "Entry      :",
                        round(entry, DIGITS)
                    )

                    print(
                        "SL         :",
                        round(sl, DIGITS)
                    )

                    print(
                        "TP         :",
                        round(tp, DIGITS)
                    )

                    print(
                        "Risk       :",
                        round(
                            trade_setup["risk"],
                            DIGITS
                        )
                    )

                    print(
                        "Second     :",
                        round(
                            trade_setup["second_entry"],
                            DIGITS
                        )
                    )

                    print("-" * 75)

                    add_log(
                        f"{Fore.GREEN if buy == True else Fore.RED}"
                        f"[{tf_label}] VALID {direction} ENTRY"
                        f"{Style.RESET_ALL} | "
                        f"Setup: {trade_setup['setup_time']} | "
                        f"Entry: {round(entry, DIGITS)} | "
                        f"SL: {round(sl, DIGITS)} | "
                        f"TP: {round(tp, DIGITS)} | "
                        f"Risk: {round(trade_setup['risk'], DIGITS)} | "
                        f"Second: {round(trade_setup['second_entry'], DIGITS)}",
                        send_telegram=True
                    )

                    Meta.SetExecutionBasedTP(
                        enabled=True,
                        tp_r=TP_R,
                        signal_entry=entry
                    )

                    Meta.run(
                        symbol,
                        buy,
                        sell,
                        lot,
                        tp,
                        sl,
                        magic,
                        stopLossPure=True
                    )

                    # =========================================
                    # SECOND ENTRY
                    #
                    # This is intentionally optional.
                    # Default = False.
                    #
                    # If enabled, the actual second-entry order
                    # must be handled by the same Meta execution
                    # layer used by the existing trader
                    # environment.
                    # =========================================

                    if USE_SECOND_ENTRY:

                        add_log(
                            f"{Fore.MAGENTA}"
                            f"[{tf_label}] Second entry is ENABLED. "
                            f"Price: "
                            f"{round(trade_setup['second_entry'], DIGITS)} | "
                            f"Volume: "
                            f"{lot * SECOND_ENTRY_VOLUME_MULTIPLIER}"
                            f"{Style.RESET_ALL}",
                            send_telegram=True
                        )

                    trade_setup = None

                # =============================================
                # PERSIST THIS TIMEFRAME'S STATE
                # =============================================

                tf_states[tf_label] = {
                    "buy": buy,
                    "sell": sell,
                    "status": status,
                    "pending_setup": pending_setup,
                    "cooldown_until": cooldown_until,
                }

    else:

        # MT5 is not connected. Log a throttled notice so the
        # console does not get spammed every loop iteration.
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
                f"{Style.RESET_ALL}"
            )

            last_disconnect_log_time = now

    time_module.sleep(
        LOOP_SECONDS
    )
