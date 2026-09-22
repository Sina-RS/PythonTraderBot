import pandas as pd
import numpy as np
from Meta import *

from datetime import time
from zoneinfo import ZoneInfo


# ============================================================
# SETTINGS
# ============================================================

SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M1
NUMBER_OF_DATA = 10000

SPIKE_CANDLE_SIZE = 1.5

PGAP_POINTS = 100
MAX_SL_DISTANCE_POINTS = 1000

INITIAL_CASH = 10000.0

# ------------------------------------------------------------
# Risk model
# ------------------------------------------------------------

RISK_PER_TRADE = 100.0

# ------------------------------------------------------------
# 1R take profit
# ------------------------------------------------------------

TP_R = 4.0

# ------------------------------------------------------------
# Second entry
# ------------------------------------------------------------

USE_SECOND_ENTRY = False

SECOND_ENTRY_VOLUME_MULTIPLIER = 2.0

# ============================================================
# OPTIONAL EMA FILTER
# ============================================================

USE_EMA_FILTER = True

EMA_PERIOD = 60

# ============================================================
# OPTIONAL TREND STRUCTURE FILTER
# ============================================================

USE_TREND_FILTER = True

MAX_OPPOSITE_MOVES = 1

# ============================================================
# OPTIONAL RANGE / ADX FILTER
#
# If True:
#   Entry is allowed only when ADX >= MIN_ADX.
#
# If False:
#   No ADX/range restriction is applied.
# ============================================================

USE_RANGE_FILTER = False

ADX_PERIOD = 14
MIN_ADX = 20.0

# ============================================================
# OPTIONAL NEW YORK SESSION FILTER
#
# If True:
#   Only Entry 1 signals occurring between SESSION_START_HOUR
#   and SESSION_END_HOUR in New York time are allowed.
#
# The end hour is exclusive:
#
#   01:00 <= Entry time < 05:00
#
# If False:
#   No session/time restriction is applied.
#
# The timezone is handled with IANA timezone data so New York
# daylight-saving changes are handled automatically.
# ============================================================

USE_SESSION_FILTER = False

SESSION_START_HOUR = 1
SESSION_END_HOUR = 5

SESSION_TIMEZONE = "America/New_York"


# ============================================================
# MT5 INITIALIZE
# ============================================================

if not mt5.initialize():

    raise RuntimeError(
        f"MT5 initialize failed: {mt5.last_error()}"
    )


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

    raise RuntimeError(
        "Invalid broker point."
    )


P_GAP_PRICE = (
    PGAP_POINTS * BROKER_POINT
)

MAX_SL_DISTANCE_PRICE = (
    MAX_SL_DISTANCE_POINTS * BROKER_POINT
)


# ============================================================
# PRINT SETTINGS
# ============================================================

print()
print("========================================")
print("BACKTEST SETTINGS")
print("========================================")

print("Symbol              :", SYMBOL)
print("Point               :", BROKER_POINT)
print("Digits              :", DIGITS)
print("Spike multiplier    :", SPIKE_CANDLE_SIZE)
print("Gap points          :", PGAP_POINTS)
print("Gap price           :", P_GAP_PRICE)
print("Max SL points       :", MAX_SL_DISTANCE_POINTS)
print("Max SL price        :", MAX_SL_DISTANCE_PRICE)
print("Initial cash        :", INITIAL_CASH)
print("Risk / trade        :", RISK_PER_TRADE)
print("TP                  :", f"{TP_R}R")
print("Second entry        :", USE_SECOND_ENTRY)
print("EMA filter          :", USE_EMA_FILTER)
print("EMA period          :", EMA_PERIOD)
print("Trend filter        :", USE_TREND_FILTER)
print("Range filter        :", USE_RANGE_FILTER)
print("ADX period          :", ADX_PERIOD)
print("Minimum ADX         :", MIN_ADX)
print("Session filter      :", USE_SESSION_FILTER)
print("Session timezone    :", SESSION_TIMEZONE)
print(
    "Session             :",
    f"{SESSION_START_HOUR:02d}:00 - {SESSION_END_HOUR:02d}:00"
)


# ============================================================
# GET RAW DATA
# ============================================================

df = Meta.GetRates(
    symbol=SYMBOL,
    number_of_data=NUMBER_OF_DATA,
    timeFrame=TIMEFRAME
).copy()


# ============================================================
# NORMALIZE COLUMN NAMES
# ============================================================

df.columns = [
    str(c).lower()
    for c in df.columns
]


required = [
    "open",
    "high",
    "low",
    "close"
]


for col in required:

    if col not in df.columns:

        mt5.shutdown()

        raise RuntimeError(
            f"Missing required column: {col}\n"
            f"Available columns: {list(df.columns)}"
        )


# ============================================================
# DATETIME INDEX
# ============================================================

if not isinstance(df.index, pd.DatetimeIndex):

    possible_time_columns = [
        "time",
        "datetime",
        "date",
        "local time"
    ]

    found_time = None

    for c in possible_time_columns:

        if c in df.columns:

            found_time = c
            break

    if found_time is not None:

        df[found_time] = pd.to_datetime(
            df[found_time]
        )

        df.set_index(
            found_time,
            inplace=True
        )


# ============================================================
# SORT
# ============================================================

df.sort_index(
    inplace=True
)


# ============================================================
# FORCE NUMERIC OHLC
# ============================================================

for col in required:

    df[col] = pd.to_numeric(
        df[col],
        errors="coerce"
    )


df.dropna(
    subset=required,
    inplace=True
)


# ============================================================
# CREATE SIGNAL COLUMNS
# ============================================================

df["position"] = 0

df["sl_buy"] = np.nan
df["sl_sell"] = np.nan

df["entry_buy"] = np.nan
df["entry_sell"] = np.nan

df["entry_buy_2x"] = np.nan
df["entry_sell_2x"] = np.nan

df["spike_index"] = pd.Series(
    index=df.index,
    dtype="object"
)

df["spike_body"] = np.nan
df["sl_distance"] = np.nan


# ============================================================
# EMA CALCULATION
# ============================================================

df["EMA"] = (
    df["close"]
    .ewm(
        span=EMA_PERIOD,
        adjust=False
    )
    .mean()
)


# ============================================================
# ADX CALCULATION
#
# Wilder-style smoothed ADX.
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

    denominator = (
        plus_di + minus_di
    )

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


if USE_RANGE_FILTER:

    df["ADX"] = calculate_adx(
        df,
        ADX_PERIOD
    )

else:

    df["ADX"] = np.nan


# ============================================================
# NEW YORK SESSION HELPER
# ============================================================

NEW_YORK_TZ = ZoneInfo(
    SESSION_TIMEZONE
)


def is_in_new_york_session(timestamp):
    """
    Returns True when timestamp falls inside:

        SESSION_START_HOUR <= time < SESSION_END_HOUR

    in New York local time.

    Naive timestamps are interpreted as UTC+3, matching the
    timestamp convention used by the supplied XAUUSD data.
    """

    if pd.isna(timestamp):

        return False

    ts = pd.Timestamp(timestamp)

    if ts.tzinfo is None:

        # The supplied dataset timestamps are treated as UTC+3.
        ts = ts.tz_localize(
            "Etc/GMT-3"
        )

    else:

        ts = ts.tz_convert(
            "UTC"
        )

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
# REFERENCE CANDLES
# ============================================================

for lag in [1, 2, 3]:

    df[f"open_{lag}"] = (
        df["open"].shift(lag)
    )

    df[f"high_{lag}"] = (
        df["high"].shift(lag)
    )

    df[f"low_{lag}"] = (
        df["low"].shift(lag)
    )

    df[f"close_{lag}"] = (
        df["close"].shift(lag)
    )


# ============================================================
# REMOVE FIRST 3 INVALID ROWS
# ============================================================

df = df.dropna(
    subset=[
        "open_1",
        "high_1",
        "low_1",
        "close_1",
        "open_2",
        "high_2",
        "low_2",
        "close_2",
        "open_3",
        "high_3",
        "low_3",
        "close_3"
    ]
).copy()


# ============================================================
# SETUP CONDITIONS
# ============================================================

buy_setup = (

    (
        df["close_1"]
        >
        df["close_2"]
    )

    &

    (
        df["open_1"]
        >
        df["open_2"]
    )

    &

    (
        df["close_2"]
        >
        df["close_3"]
    )

    &

    (
        df["open_2"]
        >
        df["open_3"]
    )

    &

    (
        df["close_1"]
        >
        df["open_1"]
    )

    &

    (
        df["close_2"]
        >
        df["open_2"]
    )

    &

    (
        df["close_3"]
        >
        df["open_3"]
    )

    &

    (
        df["low_1"]
        >
        df["high_3"] + P_GAP_PRICE
    )

)


buy_spike_body = (
    df["close_2"]
    -
    df["open_2"]
)

buy_before_body = (
    df["close_3"]
    -
    df["open_3"]
)

buy_after_body = (
    df["close_1"]
    -
    df["open_1"]
)


buy_spike = (

    buy_spike_body
    >
    SPIKE_CANDLE_SIZE
    *
    buy_before_body

) & (

    buy_spike_body
    >
    SPIKE_CANDLE_SIZE
    *
    buy_after_body

)


buy_setup = (
    buy_setup
    &
    buy_spike
)


# ============================================================
# SELL SETUP
# ============================================================

sell_setup = (

    (
        df["close_1"]
        <
        df["close_2"]
    )

    &

    (
        df["open_1"]
        <
        df["open_2"]
    )

    &

    (
        df["close_2"]
        <
        df["close_3"]
    )

    &

    (
        df["open_2"]
        <
        df["open_3"]
    )

    &

    (
        df["close_1"]
        <
        df["open_1"]
    )

    &

    (
        df["close_2"]
        <
        df["open_2"]
    )

    &

    (
        df["close_3"]
        <
        df["open_3"]
    )

    &

    (
        df["high_1"]
        <
        df["low_3"] - P_GAP_PRICE
    )

)


sell_spike_body = (
    df["open_2"]
    -
    df["close_2"]
)

sell_before_body = (
    df["open_3"]
    -
    df["close_3"]
)

sell_after_body = (
    df["open_1"]
    -
    df["close_1"]
)


sell_spike = (

    sell_spike_body
    >
    SPIKE_CANDLE_SIZE
    *
    sell_before_body

) & (

    sell_spike_body
    >
    SPIKE_CANDLE_SIZE
    *
    sell_after_body

)


sell_setup = (
    sell_setup
    &
    sell_spike
)


# ============================================================
# FIND FIRST VALID ENTRY AFTER C
# ============================================================

buy_setup_idx = df.index[buy_setup]
sell_setup_idx = df.index[sell_setup]


index_to_pos = {
    idx: pos
    for pos, idx in enumerate(df.index)
}


def buy_trend_is_valid(start_pos, entry_pos):

    if not USE_TREND_FILTER:
        return True

    consecutive_opposite = 0

    for pos in range(
        start_pos + 1,
        entry_pos + 1
    ):

        current_high = float(
            df.iloc[pos]["high"]
        )

        previous_high = float(
            df.iloc[pos - 1]["high"]
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


def sell_trend_is_valid(start_pos, entry_pos):

    if not USE_TREND_FILTER:
        return True

    consecutive_opposite = 0

    for pos in range(
        start_pos + 1,
        entry_pos + 1
    ):

        current_low = float(
            df.iloc[pos]["low"]
        )

        previous_low = float(
            df.iloc[pos - 1]["low"]
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


def entry_filters_are_valid(
    entry_pos,
    direction
):

    entry_idx = df.index[entry_pos]

    # --------------------------------------------------------
    # EMA FILTER
    # --------------------------------------------------------

    if USE_EMA_FILTER:

        entry_close = float(
            df.iloc[entry_pos]["close"]
        )

        entry_ema = float(
            df.iloc[entry_pos]["EMA"]
        )

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
            df.iloc[entry_pos]["ADX"]
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


def find_first_buy_entry(
    start_pos,
    sl
):

    for entry_pos in range(
        start_pos + 1,
        len(df)
    ):

        current_low = float(
            df.iloc[entry_pos]["low"]
        )

        previous_low = float(
            df.iloc[entry_pos - 1]["low"]
        )

        if current_low < previous_low:

            risk = (
                current_low - sl
            )

            if risk <= 0:

                return None

            if (
                risk
                >
                MAX_SL_DISTANCE_PRICE
            ):

                return None

            if not buy_trend_is_valid(
                start_pos,
                entry_pos
            ):
                continue

            if not entry_filters_are_valid(
                entry_pos,
                "BUY"
            ):
                continue

            return (
                entry_pos,
                current_low,
                risk
            )

    return None


def find_first_sell_entry(
    start_pos,
    sl
):

    for entry_pos in range(
        start_pos + 1,
        len(df)
    ):

        current_high = float(
            df.iloc[entry_pos]["high"]
        )

        previous_high = float(
            df.iloc[entry_pos - 1]["high"]
        )

        if current_high > previous_high:

            risk = (
                sl - current_high
            )

            if risk <= 0:

                return None

            if (
                risk
                >
                MAX_SL_DISTANCE_PRICE
            ):

                return None

            if not sell_trend_is_valid(
                start_pos,
                entry_pos
            ):
                continue

            if not entry_filters_are_valid(
                entry_pos,
                "SELL"
            ):
                continue

            return (
                entry_pos,
                current_high,
                risk
            )

    return None


# ============================================================
# BUILD DETECTED SIGNALS
# ============================================================

detected_buy = []
detected_sell = []


# ------------------------------------------------------------
# BUY SETUPS
# ------------------------------------------------------------

for c_idx in buy_setup_idx:

    c_pos = index_to_pos[c_idx]

    sl = float(
        df.iloc[c_pos - 2]["low"]
    )

    result = find_first_buy_entry(
        c_pos,
        sl
    )

    if result is None:

        continue

    entry_pos, entry, risk = result

    entry_idx = df.index[entry_pos]

    detected_buy.append(
        (
            entry_idx,
            entry,
            sl,
            risk,
            c_idx,
            c_pos - 1
        )
    )


# ------------------------------------------------------------
# SELL SETUPS
# ------------------------------------------------------------

for c_idx in sell_setup_idx:

    c_pos = index_to_pos[c_idx]

    sl = float(
        df.iloc[c_pos - 2]["high"]
    )

    result = find_first_sell_entry(
        c_pos,
        sl
    )

    if result is None:

        continue

    entry_pos, entry, risk = result

    entry_idx = df.index[entry_pos]

    detected_sell.append(
        (
            entry_idx,
            entry,
            sl,
            risk,
            c_idx,
            c_pos - 1
        )
    )


# ============================================================
# WRITE BUY SIGNAL INFORMATION
# ============================================================

for (
    idx,
    entry,
    sl,
    risk,
    c_idx,
    spike_pos
) in detected_buy:

    if (
        df.loc[idx, "position"] != 0
    ):

        continue

    df.loc[idx, "position"] = 1

    df.loc[idx, "entry_buy"] = entry

    df.loc[idx, "sl_buy"] = sl

    df.loc[idx, "sl_distance"] = risk

    df.loc[idx, "spike_body"] = abs(
        float(
            df.iloc[spike_pos]["close"]
        )
        -
        float(
            df.iloc[spike_pos]["open"]
        )
    )

    df.loc[idx, "spike_index"] = (
        df.index[spike_pos]
    )

    df.loc[idx, "entry_buy_2x"] = (
        entry - risk / 2
    )


# ============================================================
# WRITE SELL SIGNAL INFORMATION
# ============================================================

for (
    idx,
    entry,
    sl,
    risk,
    c_idx,
    spike_pos
) in detected_sell:

    if (
        df.loc[idx, "position"] != 0
    ):

        continue

    df.loc[idx, "position"] = -1

    df.loc[idx, "entry_sell"] = entry

    df.loc[idx, "sl_sell"] = sl

    df.loc[idx, "sl_distance"] = risk

    df.loc[idx, "spike_body"] = abs(
        float(
            df.iloc[spike_pos]["open"]
        )
        -
        float(
            df.iloc[spike_pos]["close"]
        )
    )

    df.loc[idx, "spike_index"] = (
        df.index[spike_pos]
    )

    df.loc[idx, "entry_sell_2x"] = (
        entry + risk / 2
    )


# ============================================================
# REMOVE TEMPORARY COLUMNS
# ============================================================

df.drop(
    columns=[
        "open_1",
        "high_1",
        "low_1",
        "close_1",
        "open_2",
        "high_2",
        "low_2",
        "close_2",
        "open_3",
        "high_3",
        "low_3",
        "close_3"
    ],
    inplace=True
)


# ============================================================
# SIGNAL SUMMARY
# ============================================================

buy_count = int(
    (df["position"] == 1).sum()
)

sell_count = int(
    (df["position"] == -1).sum()
)

signal_count = (
    buy_count + sell_count
)


print()
print("========================================")
print("SIGNAL DETECTION")
print("========================================")

print("BUY signals :", buy_count)
print("SELL signals:", sell_count)
print("TOTAL       :", signal_count)


# ============================================================
#  OHLC BACKTEST
# ============================================================

trades = []

equity = INITIAL_CASH

active_setup = None


# ============================================================
# CLOSE ONE TRADE LEG
# ============================================================

def close_leg(
    leg,
    exit_price,
    exit_time,
    exit_reason
):

    global equity

    direction = leg["direction"]

    entry = leg["entry"]

    sl = leg["sl"]

    tp = leg["tp"]

    risk = leg["risk"]

    base_risk = leg["base_risk"]

    volume_multiplier = leg["volume_multiplier"]

    if direction == "BUY":

        price_r = (
            exit_price - entry
        ) / risk

    else:

        price_r = (
            entry - exit_price
        ) / risk

    r_multiple = (
        price_r
        *
        volume_multiplier
        *
        (
            risk / base_risk
        )
    )

    pnl = (
        r_multiple
        *
        RISK_PER_TRADE
    )

    equity += pnl

    trades.append({

        "setup_id":
            leg["setup_id"],

        "entry_number":
            leg["entry_number"],

        "entry_time":
            leg["entry_time"],

        "activation_time":
            leg["activation_time"],

        "exit_time":
            exit_time,

        "direction":
            direction,

        "entry":
            entry,

        "sl":
            sl,

        "tp":
            tp,

        "risk":
            risk,

        "base_risk":
            base_risk,

        "volume_multiplier":
            volume_multiplier,

        "exit":
            exit_price,

        "R":
            r_multiple,

        "PnL":
            pnl,

        "exit_reason":
            exit_reason,

        "spike_body":
            leg["spike_body"],

        "sl_points":
            base_risk / BROKER_POINT

    })


# ============================================================
# PROCESS CANDLES
# ============================================================

for i in range(len(df)):

    idx = df.index[i]

    row = df.iloc[i]

    candle_low = float(
        row["low"]
    )

    candle_high = float(
        row["high"]
    )


    # ========================================================
    # MANAGE ACTIVE SETUP
    # ========================================================

    if active_setup is not None:

        direction = active_setup["direction"]

        sl = active_setup["sl"]

        tp = active_setup["tp"]

        legs = active_setup["legs"]


        exit_price = None

        exit_reason = None


        # ====================================================
        # BUY
        # ====================================================

        if direction == "BUY":

            if (
                candle_low <= sl
                and
                candle_high >= tp
            ):

                exit_price = sl

                exit_reason = (
                    "SL_and_TP_same_bar_SL_first"
                )

            elif candle_low <= sl:

                exit_price = sl

                exit_reason = "SL"

            elif candle_high >= tp:

                exit_price = tp

                exit_reason = "TP"


        # ====================================================
        # SELL
        # ====================================================

        else:

            if (
                candle_high >= sl
                and
                candle_low <= tp
            ):

                exit_price = sl

                exit_reason = (
                    "SL_and_TP_same_bar_SL_first"
                )

            elif candle_high >= sl:

                exit_price = sl

                exit_reason = "SL"

            elif candle_low <= tp:

                exit_price = tp

                exit_reason = "TP"


        # ====================================================
        # CLOSE ALL ACTIVE LEGS
        # ====================================================

        if exit_price is not None:

            for leg in legs:

                close_leg(
                    leg,
                    exit_price,
                    idx,
                    exit_reason
                )

            active_setup = None

            continue


        # ====================================================
        # SECOND ENTRY ACTIVATION
        # ====================================================

        if (
            USE_SECOND_ENTRY
            and
            not active_setup["second_entry_active"]
        ):

            second_entry = (
                active_setup["second_entry"]
            )

            second_entry_touched = False

            if direction == "BUY":

                second_entry_touched = (
                    candle_low
                    <=
                    second_entry
                )

            else:

                second_entry_touched = (
                    candle_high
                    >=
                    second_entry
                )

            if second_entry_touched:

                second_risk = abs(
                    second_entry - sl
                )

                if second_risk > 0:

                    legs.append({

                        "setup_id":
                            active_setup["setup_id"],

                        "entry_number":
                            2,

                        "entry_time":
                            active_setup["signal_time"],

                        "activation_time":
                            idx,

                        "direction":
                            direction,

                        "entry":
                            second_entry,

                        "sl":
                            sl,

                        "tp":
                            tp,

                        "risk":
                            second_risk,

                        "base_risk":
                            active_setup["base_risk"],

                        "volume_multiplier":
                            SECOND_ENTRY_VOLUME_MULTIPLIER,

                        "spike_body":
                            active_setup["spike_body"]

                    })

                    active_setup[
                        "second_entry_active"
                    ] = True


                    # =========================================
                    # SAME CANDLE TP CHECK
                    # =========================================

                    if direction == "BUY":

                        if candle_high >= tp:

                            for leg in legs:

                                close_leg(
                                    leg,
                                    tp,
                                    idx,
                                    "TP"
                                )

                            active_setup = None

                            continue

                    else:

                        if candle_low <= tp:

                            for leg in legs:

                                close_leg(
                                    leg,
                                    tp,
                                    idx,
                                    "TP"
                                )

                            active_setup = None

                            continue

        continue


    # ========================================================
    # NO ACTIVE SETUP
    # LOOK FOR NEW SIGNAL
    # ========================================================

    signal = int(
        row["position"]
    )


    # ========================================================
    # BUY
    # ========================================================

    if signal == 1:

        entry = float(
            row["entry_buy"]
        )

        sl = float(
            row["sl_buy"]
        )

        base_risk = (
            entry - sl
        )

        if base_risk <= 0:

            continue

        if base_risk > MAX_SL_DISTANCE_PRICE:

            continue

        tp = (
            entry
            +
            TP_R * base_risk
        )

        second_entry = float(
            row["entry_buy_2x"]
        )

        active_setup = {

            "setup_id":
                i,

            "signal_time":
                idx,

            "direction":
                "BUY",

            "base_risk":
                base_risk,

            "sl":
                sl,

            "tp":
                tp,

            "second_entry":
                second_entry,

            "second_entry_active":
                False,

            "spike_body":
                float(
                    row["spike_body"]
                ),

            "legs": [

                {

                    "setup_id":
                        i,

                    "entry_number":
                        1,

                    "entry_time":
                        idx,

                    "activation_time":
                        idx,

                    "direction":
                        "BUY",

                    "entry":
                        entry,

                    "sl":
                        sl,

                    "tp":
                        tp,

                    "risk":
                        base_risk,

                    "base_risk":
                        base_risk,

                    "volume_multiplier":
                        1.0,

                    "spike_body":
                        float(
                            row["spike_body"]
                        )

                }

            ]

        }


    # ========================================================
    # SELL
    # ========================================================

    elif signal == -1:

        entry = float(
            row["entry_sell"]
        )

        sl = float(
            row["sl_sell"]
        )

        base_risk = (
            sl - entry
        )

        if base_risk <= 0:

            continue

        if base_risk > MAX_SL_DISTANCE_PRICE:

            continue

        tp = (
            entry
            -
            TP_R * base_risk
        )

        second_entry = float(
            row["entry_sell_2x"]
        )

        active_setup = {

            "setup_id":
                i,

            "signal_time":
                idx,

            "direction":
                "SELL",

            "base_risk":
                base_risk,

            "sl":
                sl,

            "tp":
                tp,

            "second_entry":
                second_entry,

            "second_entry_active":
                False,

            "spike_body":
                float(
                    row["spike_body"]
                ),

            "legs": [

                {

                    "setup_id":
                        i,

                    "entry_number":
                        1,

                    "entry_time":
                        idx,

                    "activation_time":
                        idx,

                    "direction":
                        "SELL",

                    "entry":
                        entry,

                    "sl":
                        sl,

                    "tp":
                        tp,

                    "risk":
                        base_risk,

                    "base_risk":
                        base_risk,

                    "volume_multiplier":
                        1.0,

                    "spike_body":
                        float(
                            row["spike_body"]
                        )

                }

            ]

        }


# ============================================================
# CLOSE REMAINING OPEN SETUP
# ============================================================

if active_setup is not None:

    last_idx = df.index[-1]

    last_close = float(
        df.iloc[-1]["close"]
    )

    for leg in active_setup["legs"]:

        close_leg(
            leg,
            last_close,
            last_idx,
            "END_OF_DATA"
        )

    active_setup = None


# ============================================================
# TRADES DATAFRAME
# ============================================================

trades_df = pd.DataFrame(
    trades
)


# ============================================================
# STATISTICS
# ============================================================

if len(trades_df) > 0:

    wins = (
        trades_df["R"] > 0
    )

    losses = (
        trades_df["R"] <= 0
    )

    win_count = int(
        wins.sum()
    )

    loss_count = int(
        losses.sum()
    )

    total_trades = len(
        trades_df
    )

    win_rate = (
        win_count
        /
        total_trades
        *
        100
    )

    total_R = (
        trades_df["R"].sum()
    )

    total_pnl = (
        trades_df["PnL"].sum()
    )


    # ========================================================
    # ENTRY 1 / ENTRY 2 STATISTICS
    # ========================================================

    entry1_trades = trades_df[
        trades_df["entry_number"] == 1
    ]

    entry2_trades = trades_df[
        trades_df["entry_number"] == 2
    ]

    entry2_activations = len(
        entry2_trades
    )

    entry2_wins = int(
        (
            entry2_trades["R"] > 0
        ).sum()
    )

    entry2_losses = int(
        (
            entry2_trades["R"] <= 0
        ).sum()
    )

    entry2_R = (
        entry2_trades["R"].sum()
    )

    if entry2_activations > 0:

        entry2_win_rate = (
            entry2_wins
            /
            entry2_activations
            *
            100
        )

    else:

        entry2_win_rate = 0.0


    setup_R = (
        trades_df
        .groupby("setup_id")["R"]
        .sum()
    )

    setup_count = len(
        setup_R
    )

    setup_wins = int(
        (
            setup_R > 0
        ).sum()
    )

    if setup_count > 0:

        setup_win_rate = (
            setup_wins
            /
            setup_count
            *
            100
        )

    else:

        setup_win_rate = 0.0


    print()
    print("========================================")
    print("ENTRY 1 / ENTRY 2")
    print("========================================")

    print(
        "Entry 1 trades   :",
        len(entry1_trades)
    )

    print(
        "Entry 1 Total R  :",
        round(
            entry1_trades["R"].sum(),
            2
        )
    )

    print(
        "Entry 2 active   :",
        entry2_activations
    )

    print(
        "Entry 2 wins     :",
        entry2_wins
    )

    print(
        "Entry 2 losses   :",
        entry2_losses
    )

    print(
        "Entry 2 win rate :",
        round(
            entry2_win_rate,
            2
        ),
        "%"
    )

    print(
        "Entry 2 Total R  :",
        round(
            entry2_R,
            2
        )
    )

    print(
        "Setups           :",
        setup_count
    )

    print(
        "Setup win rate   :",
        round(
            setup_win_rate,
            2
        ),
        "%"
    )


    # ========================================================
    # BUY / SELL
    # ========================================================

    buy_trades = trades_df[
        trades_df["direction"] == "BUY"
    ]

    buy_wins = (
        buy_trades["R"] > 0
    ).sum()

    buy_R = (
        buy_trades["R"].sum()
    )

    sell_trades = trades_df[
        trades_df["direction"] == "SELL"
    ]

    sell_wins = (
        sell_trades["R"] > 0
    ).sum()

    sell_R = (
        sell_trades["R"].sum()
    )


    # ========================================================
    # PROFIT FACTOR
    # ========================================================

    gross_profit = trades_df.loc[
        trades_df["PnL"] > 0,
        "PnL"
    ].sum()

    gross_loss = abs(
        trades_df.loc[
            trades_df["PnL"] < 0,
            "PnL"
        ].sum()
    )

    if gross_loss > 0:

        profit_factor = (
            gross_profit
            /
            gross_loss
        )

    else:

        profit_factor = np.inf


    # ========================================================
    # MAX DRAWDOWN
    # ========================================================

    cumulative_equity = (
        INITIAL_CASH
        +
        trades_df["PnL"].cumsum()
    )

    equity_peak = (
        cumulative_equity.cummax()
    )

    drawdown = (
        cumulative_equity
        -
        equity_peak
    )

    max_drawdown = (
        drawdown.min()
    )


    # ========================================================
    # FINAL RESULT
    # ========================================================

    print()
    print("========================================")
    print(" BACKTEST")
    print("========================================")

    print(
        "Signals:",
        signal_count
    )

    print(
        "Trades :",
        total_trades
    )

    print()
    print("========================================")
    print("TRADE STATISTICS")
    print("========================================")

    print(
        "Wins       :",
        win_count
    )

    print(
        "Losses     :",
        loss_count
    )

    print(
        "Win rate   :",
        round(win_rate, 2),
        "%"
    )

    print(
        "Total R    :",
        round(total_R, 2)
    )

    print(
        "Total PnL  :",
        round(total_pnl, 2)
    )

    print(
        "Final cash :",
        round(equity, 2)
    )

    print(
        "Return     :",
        round(
            total_pnl
            /
            INITIAL_CASH
            *
            100,
            2
        ),
        "%"
    )

    print(
        "Profit factor:",
        round(
            profit_factor,
            3
        )
        if np.isfinite(profit_factor)
        else "inf"
    )

    print(
        "Max drawdown:",
        round(
            max_drawdown,
            2
        )
    )


    print()
    print("BUY:")

    print(
        "  Trades:",
        len(buy_trades)
    )

    print(
        "  Wins:",
        int(buy_wins)
    )

    print(
        "  Total R:",
        round(buy_R, 2)
    )


    print()
    print("SELL:")

    print(
        "  Trades:",
        len(sell_trades)
    )

    print(
        "  Wins:",
        int(sell_wins)
    )

    print(
        "  Total R:",
        round(sell_R, 2)
    )


    # ========================================================
    # SPIKE ANALYSIS
    # ========================================================

    bins = [
        -np.inf,
        0.75,
        1.0,
        1.5,
        2.0,
        3.0,
        4.0,
        np.inf
    ]

    labels = [
        "<0.75",
        "0.75-1",
        "1-1.5",
        "1.5-2",
        "2-3",
        "3-4",
        ">4"
    ]

    trades_df["spike_bucket"] = pd.cut(
        trades_df["spike_body"],
        bins=bins,
        labels=labels
    )

    spike_stats = (
        trades_df
        .groupby(
            "spike_bucket",
            observed=False
        )
        .agg(
            setups=("R", "count"),

            wins=(
                "R",
                lambda x:
                int((x > 0).sum())
            ),

            losses=(
                "R",
                lambda x:
                int((x <= 0).sum())
            ),

            total_R=("R", "sum")
        )
    )

    spike_stats["win_rate"] = (
        spike_stats["wins"]
        /
        spike_stats["setups"]
        *
        100
    )

    print()
    print("========================================")
    print("SPIKE SIZE VS RESULT")
    print("========================================")

    print(
        spike_stats.round(2)
    )


    # ========================================================
    # SL DISTANCE ANALYSIS
    # ========================================================

    if len(trades_df) >= 4:

        try:

            trades_df["sl_bucket"] = pd.qcut(
                trades_df["sl_points"],
                q=4,
                duplicates="drop"
            )

            sl_stats = (
                trades_df
                .groupby(
                    "sl_bucket",
                    observed=False
                )
                .agg(
                    setups=("R", "count"),

                    wins=(
                        "R",
                        lambda x:
                        int((x > 0).sum())
                    ),

                    losses=(
                        "R",
                        lambda x:
                        int((x <= 0).sum())
                    ),

                    total_R=("R", "sum")
                )
            )

            sl_stats["win_rate"] = (
                sl_stats["wins"]
                /
                sl_stats["setups"]
                *
                100
            )

            print()
            print("========================================")
            print("SL DISTANCE VS RESULT")
            print("========================================")

            print(
                sl_stats.round(2)
            )

        except Exception as e:

            print()
            print(
                "SL bucket analysis skipped:",
                e
            )


    # ========================================================
    # EXIT REASON
    # ========================================================

    print()
    print("========================================")
    print("EXIT REASONS")
    print("========================================")

    print(
        trades_df["exit_reason"]
        .value_counts()
    )


else:

    print()
    print("========================================")
    print("BACKTEST RESULT")
    print("========================================")

    print(
        "Signals were detected, "
        "but no valid trades were produced."
    )


# ============================================================
# OBJECTS CREATED
# ============================================================

all_signals = df[
    df["position"] != 0
].copy()


print()
print("========================================")
print("OBJECTS CREATED")
print("========================================")

print(
    "df          -> complete detection dataframe"
)

print(
    "all_signals -> detected signals"
)

print(
    "trades_df   -> completed  backtest trade legs"
)

print()
print("DONE.")