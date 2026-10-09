//+------------------------------------------------------------------+
//|                                                      SP2LBot.mq5 |
//|        Converted from the Python "SP2L / Advanced Trader" bot.   |
//|                                                                  |
//|  The original Python bot traded XAUUSD on M1/M5/M15 from a single |
//|  process, one magic per timeframe.  This EA is single-timeframe:  |
//|  attach it to a chart (or backtest it) once per timeframe and set |
//|  a distinct InpMagic for each.                                    |
//|                                                                  |
//|  Execution path reproduced here: the PENDING LIMIT order path.    |
//|  In the original, Strategy() always returned trade_setup = None,  |
//|  so the "EXECUTE ENTRY 1" / Meta.run() market-entry block was     |
//|  dead code and only sync_pending_limit_order() ever traded.       |
//+------------------------------------------------------------------+
#property copyright "MetaTrader Assistant"
#property version   "1.10"
#property description "SP2L spike + pullback limit-order strategy (XAUUSD)."

#include <Trade\Trade.mqh>

//--------------------------------------------------------------------
// Bar index convention (series indexing, index 0 = current forming bar)
//
//   0 -> pandas iloc[-1] : current / forming candle
//   1 -> pandas iloc[-2] : candle AFTER the spike
//   2 -> pandas iloc[-3] : the SPIKE candle
//   3 -> pandas iloc[-4] : candle BEFORE the spike
//--------------------------------------------------------------------

//====================================================================
// ENUMS
//====================================================================

enum ENUM_SP2L_SIDE
{
   SP2L_BOTH       = 0,   // Both directions
   SP2L_LONG_ONLY  = 1,   // Long (buy) only
   SP2L_SHORT_ONLY = 2    // Short (sell) only
};

//====================================================================
// INPUTS
//====================================================================

input group "=== General ==="
input long   InpMagic               = 8;      // Magic number (use one per timeframe)
input double InpLot                 = 0.1;    // Lot size
input int    InpDataBars            = 500;    // Bars used per evaluation

input group "=== Setup detection ==="
input double InpSpikeCandleSize     = 1.5;    // Spike body multiplier
input int    InpGapPoints           = 100;    // Minimum gap (broker points)
input int    InpMaxSlDistancePoints = 1000;   // Maximum SL distance (broker points)
input double InpTpR                 = 1.0;    // Take profit, in R

input group "=== Filters ==="
input bool   InpUseEmaFilter        = true;   // EMA trend filter
input int    InpEmaPeriod           = 60;     // EMA period
input bool   InpUseTrendFilter      = true;   // Higher-low / lower-high filter
input int    InpMaxOppositeMoves    = 2;      // Max consecutive opposite highs/lows
input bool   InpUseRangeFilter      = true;   // ADX range filter
input int    InpAdxPeriod           = 14;     // ADX period
input double InpMinAdx              = 20.0;   // Minimum ADX

input group "=== Session filter (disabled by default) ==="
input bool   InpUseSessionFilter    = false;  // Enable New York session filter
input int    InpSessionStartHour    = 1;      // New York session start hour
input int    InpSessionEndHour      = 5;      // New York session end hour
input int    InpServerGmtOffset     = 3;      // Broker server time GMT offset

input group "=== Behaviour ==="
input bool   InpUseSecondEntry      = true;   // Second entry (NOT implemented - see docs)
input double InpSecondEntryMult     = 2.0;    // Second entry volume multiplier
input int    InpCooldownSeconds     = 60;     // Cooldown after a position closes
input int    InpRemoveRetrySeconds  = 60;     // Retry delay after a failed removal
input int    InpSlippagePoints      = 20;     // Deviation in points for market ops
input bool   InpVerboseLog          = true;   // Verbose journal logging

input group "=== Direction filter ==="
input ENUM_SP2L_SIDE InpTradeSide   = SP2L_BOTH; // Allowed trade direction

input group "=== Server-hour filter ==="
input bool   InpUseHourFilter       = false;  // Enable server-hour filter
input int    InpHourFrom            = 14;     // Allowed from (server hour, inclusive)
input int    InpHourTo              = 18;     // Allowed to (server hour, exclusive)

input group "=== Spread filter ==="
input bool   InpUseSpreadFilter     = false;  // Enable max spread filter
input int    InpMaxSpreadPoints     = 50;     // Max spread (broker points)

input group "=== Position management ==="
input bool   InpUseBreakeven        = false;  // Move SL to breakeven
input double InpBreakevenAtR        = 1.0;    // Breakeven trigger (in R)
input int    InpBreakevenBufferPts  = 10;     // Breakeven SL buffer (points)
input bool   InpUsePartialTp        = false;  // Partial take profit
input double InpPartialAtR          = 1.0;    // Partial TP trigger (in R)
input double InpPartialPercent      = 50.0;   // Partial TP volume (%)
input bool   InpUseTrailStop        = false;  // Trailing stop
input double InpTrailStartR         = 1.0;    // Trailing start (in R)
input double InpTrailDistanceR      = 1.0;    // Trailing distance (in R)
input int    InpMaxHoldingMinutes   = 0;      // Max holding time in minutes (0 = off)

//====================================================================
// GLOBALS
//====================================================================

CTrade   g_trade;
MqlRates g_rates[];
int      g_ratesCount = 0;

int      g_emaHandle  = INVALID_HANDLE;
int      g_adxHandle  = INVALID_HANDLE;

double   g_pGapPrice  = 0.0;   // InpGapPoints in price terms
double   g_maxSlPrice = 0.0;   // InpMaxSlDistancePoints in price terms

//--- pending setup (one per instance == one timeframe in the original)
int      g_pendingDir       = 0;        // +1 buy, -1 sell, 0 none
datetime g_pendingSetupTime = 0;
double   g_pendingEntry     = 0.0;
double   g_pendingSl        = 0.0;
double   g_pendingTp        = 0.0;
double   g_pendingSpikeBody = 0.0;
ulong    g_pendingTicket    = 0;

//--- runtime state
bool     g_status            = false;   // an EA position is open
datetime g_cooldownUntil     = 0;       // per-timeframe pause after a close
ulong    g_removeRetryTicket = 0;       // throttle for failed order removals
datetime g_removeRetryAfter  = 0;

//--- open-position management state
ulong    g_mgmtTicket        = 0;       // ticket the management state belongs to
bool     g_partialDone       = false;   // partial TP already executed for g_mgmtTicket
bool     g_beDiagDone        = false;   // diagnostic already printed for g_mgmtTicket

//====================================================================
// LOGGING
//====================================================================

void Log(const string msg)
{
   if(InpVerboseLog)
      Print("[SP2L] ", msg);
}

void LogAlways(const string msg)
{
   Print("[SP2L] ", msg);
}

//====================================================================
// SMALL HELPERS
//====================================================================

double NormalizeVolume(double volume)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   if(step <= 0.0)
      step = 0.01;

   volume = MathRound(volume / step) * step;

   if(volume < vmin)
      volume = vmin;
   if(volume > vmax)
      volume = vmax;

   return NormalizeDouble(volume, 8);
}

//--- max(trade_stops_level, trade_freeze_level) in price terms
double MinOrderDistance()
{
   double stops  = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double freeze = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);

   return MathMax(stops, freeze) * _Point;
}

string DirName(const int dir)
{
   return (dir > 0) ? "BUY" : "SELL";
}

//====================================================================
// MARKET DATA
//====================================================================

bool LoadRates()
{
   ArraySetAsSeries(g_rates, true);

   int copied = CopyRates(_Symbol, PERIOD_CURRENT, 0, InpDataBars, g_rates);

   if(copied < 5)
   {
      g_ratesCount = 0;
      return false;
   }

   g_ratesCount = copied;
   return true;
}

//--- equivalent of pandas data.index.get_loc(setup_time); -1 == KeyError
int FindBarByTime(const datetime time)
{
   for(int i = 0; i < g_ratesCount; i++)
      if(g_rates[i].time == time)
         return i;

   return -1;
}

//====================================================================
// POSITION / ORDER LOOKUP
//====================================================================

bool PositionExists()
{
   int total = PositionsTotal();

   for(int i = total - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;

      if((long)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;

      return true;
   }

   return false;
}

ulong FindPendingOrder(const int dir)
{
   ENUM_ORDER_TYPE want = (dir > 0) ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT;

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0)
         continue;

      if(OrderGetString(ORDER_SYMBOL) != _Symbol)
         continue;

      if((long)OrderGetInteger(ORDER_MAGIC) != InpMagic)
         continue;

      if((ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE) != want)
         continue;

      return ticket;
   }

   return 0;
}

//====================================================================
// SESSION FILTER (New York)
//====================================================================

int FirstWeekdayOfMonth(const int year, const int month, const int weekday)
{
   MqlDateTime d;
   d.year  = year;
   d.mon   = month;
   d.day   = 1;
   d.hour  = 0;
   d.min   = 0;
   d.sec   = 0;
   d.day_of_week = 0;
   d.day_of_year = 0;

   MqlDateTime out;
   TimeToStruct(StructToTime(d), out);

   int firstDow = out.day_of_week;           // 0 = Sunday
   int offset   = (weekday - firstDow + 7) % 7;

   return 1 + offset;
}

bool IsUsDst(const MqlDateTime &t)
{
   if(t.mon < 3 || t.mon > 11)
      return false;

   if(t.mon > 3 && t.mon < 11)
      return true;

   if(t.mon == 3)
   {
      // DST starts 2nd Sunday of March at 02:00
      int day = FirstWeekdayOfMonth(t.year, 3, 0) + 7;

      if(t.day > day)
         return true;
      if(t.day < day)
         return false;

      return (t.hour >= 2);
   }

   // DST ends 1st Sunday of November at 02:00
   int day = FirstWeekdayOfMonth(t.year, 11, 0);

   if(t.day < day)
      return true;
   if(t.day > day)
      return false;

   return (t.hour < 2);
}

bool InNewYorkSession(const datetime serverTime)
{
   datetime utc = serverTime - (datetime)(InpServerGmtOffset * 3600);

   MqlDateTime utcStruct;
   TimeToStruct(utc, utcStruct);

   int nyOffset = IsUsDst(utcStruct) ? -4 : -5;

   MqlDateTime nyStruct;
   TimeToStruct(utc + (datetime)(nyOffset * 3600), nyStruct);

   return (nyStruct.hour >= InpSessionStartHour && nyStruct.hour < InpSessionEndHour);
}

//====================================================================
// SETUP DETECTION
//====================================================================

bool DetectBuySetup()
{
   if(g_ratesCount < 5)
      return false;

   bool buy1 = (g_rates[1].close > g_rates[2].close);
   bool buy2 = (g_rates[1].open  > g_rates[2].open);
   bool buy3 = (g_rates[2].close > g_rates[3].close);
   bool buy4 = (g_rates[2].open  > g_rates[3].open);
   bool buy5 = (g_rates[1].close > g_rates[1].open);
   bool buy6 = (g_rates[2].close > g_rates[2].open);
   bool buy7 = (g_rates[3].close > g_rates[3].open);

   bool pGapBuy = (g_rates[1].low > g_rates[3].high + g_pGapPrice);

   double spikeBody = g_rates[2].close - g_rates[2].open;

   bool spikeBuy = (spikeBody > InpSpikeCandleSize * (g_rates[1].close - g_rates[1].open))
                && (spikeBody > InpSpikeCandleSize * (g_rates[3].close - g_rates[3].open))
                && (spikeBody > InpSpikeCandleSize * (g_rates[0].close - g_rates[0].open));

   return (buy1 && buy2 && buy3 && buy4 && buy5 && buy6 && buy7 && pGapBuy && spikeBuy);
}

bool DetectSellSetup()
{
   if(g_ratesCount < 5)
      return false;

   bool sell1 = (g_rates[1].close < g_rates[2].close);
   bool sell2 = (g_rates[1].open  < g_rates[2].open);
   bool sell3 = (g_rates[2].close < g_rates[3].close);
   bool sell4 = (g_rates[2].open  < g_rates[3].open);
   bool sell5 = (g_rates[1].close < g_rates[1].open);
   bool sell6 = (g_rates[2].close < g_rates[2].open);
   bool sell7 = (g_rates[3].close < g_rates[3].open);

   bool pGapSell = (g_rates[1].high < g_rates[3].low - g_pGapPrice);

   double spikeBody = g_rates[2].open - g_rates[2].close;

   bool spikeSell = (spikeBody > InpSpikeCandleSize * (g_rates[1].open - g_rates[1].close))
                 && (spikeBody > InpSpikeCandleSize * (g_rates[3].open - g_rates[3].close))
                 && (spikeBody > InpSpikeCandleSize * (g_rates[0].open - g_rates[0].close));

   return (sell1 && sell2 && sell3 && sell4 && sell5 && sell6 && sell7 && pGapSell && spikeSell);
}

//====================================================================
// PENDING SETUP
//====================================================================

void CreatePendingBuy()
{
   g_pendingDir       = 1;
   g_pendingSetupTime = g_rates[0].time;
   g_pendingEntry     = g_rates[1].low;    // limit starts on previous candle low
   g_pendingSl        = g_rates[3].low;    // SL = low before the spike
   g_pendingSpikeBody = MathAbs(g_rates[2].close - g_rates[2].open);
   g_pendingTp        = 0.0;
   g_pendingTicket    = 0;                 // original recreated the dict without order_ticket
}

void CreatePendingSell()
{
   g_pendingDir       = -1;
   g_pendingSetupTime = g_rates[0].time;
   g_pendingEntry     = g_rates[1].high;   // limit starts on previous candle high
   g_pendingSl        = g_rates[3].high;   // SL = high before the spike
   g_pendingSpikeBody = MathAbs(g_rates[2].open - g_rates[2].close);
   g_pendingTp        = 0.0;
   g_pendingTicket    = 0;
}

void ClearPending()
{
   g_pendingDir       = 0;
   g_pendingSetupTime = 0;
   g_pendingEntry     = 0.0;
   g_pendingSl        = 0.0;
   g_pendingTp        = 0.0;
   g_pendingSpikeBody = 0.0;
   g_pendingTicket    = 0;
}

//--- BUY limit follows higher lows, SELL limit follows lower highs.
//--- A new lower low / higher high never turns into a market entry.
void TrailPendingLimit()
{
   if(g_pendingDir > 0)
   {
      if(g_rates[0].low > g_pendingEntry)
         g_pendingEntry = g_rates[0].low;
   }
   else if(g_pendingDir < 0)
   {
      if(g_rates[0].high < g_pendingEntry)
         g_pendingEntry = g_rates[0].high;
   }
}

bool PendingIsInvalid()
{
   if(g_pendingDir > 0)
   {
      double risk = g_pendingEntry - g_pendingSl;

      return ((g_rates[0].low <= g_pendingSl) || (risk > g_maxSlPrice));
   }

   double risk = g_pendingSl - g_pendingEntry;

   return ((g_rates[0].high >= g_pendingSl) || (risk > g_maxSlPrice));
}

//====================================================================
// FILTERS
//====================================================================

bool TrendIsValid(const int setupSeries)
{
   if(!InpUseTrendFilter)
      return true;

   if(setupSeries < 0 || setupSeries >= g_ratesCount)
      return true;

   int opposite = 0;

   // Walk forward in time from just after the setup bar to the current bar.
   for(int i = setupSeries - 1; i >= 0; i--)
   {
      bool madeProgress;

      if(g_pendingDir > 0)
         madeProgress = (g_rates[i].high > g_rates[i + 1].high);
      else
         madeProgress = (g_rates[i].low < g_rates[i + 1].low);

      if(madeProgress)
      {
         opposite = 0;
      }
      else
      {
         opposite++;

         if(opposite > InpMaxOppositeMoves)
            return false;
      }
   }

   return true;
}

bool EntryFiltersAreValid(const int bar)
{
   if(bar < 0 || bar >= g_ratesCount)
      return false;

   //--- EMA filter
   if(InpUseEmaFilter)
   {
      double ema[1];

      if(CopyBuffer(g_emaHandle, 0, bar, 1, ema) != 1)
         return false;

      if(!MathIsValidNumber(ema[0]))
         return false;

      double closePrice = g_rates[bar].close;

      if(g_pendingDir > 0)
      {
         if(closePrice <= ema[0])
            return false;
      }
      else
      {
         if(closePrice >= ema[0])
            return false;
      }
   }

   //--- Range / ADX filter
   if(InpUseRangeFilter)
   {
      double adx[1];

      if(CopyBuffer(g_adxHandle, 0, bar, 1, adx) != 1)
         return false;

      if(!MathIsValidNumber(adx[0]))
         return false;

      if(adx[0] < InpMinAdx)
         return false;
   }

   //--- Server-hour filter
   if(InpUseHourFilter)
   {
      MqlDateTime st;
      TimeToStruct(g_rates[bar].time, st);

      if(st.hour < InpHourFrom || st.hour >= InpHourTo)
         return false;
   }

   //--- New York session filter
   if(InpUseSessionFilter)
   {
      if(!InNewYorkSession(g_rates[bar].time))
         return false;
   }

   return true;
}

//====================================================================
// ORDER MANAGEMENT
//====================================================================

bool RemovePendingOrder(const int dir)
{
   ulong ticket = FindPendingOrder(dir);

   if(ticket == 0)
      return true;

   if(ticket == g_removeRetryTicket && TimeCurrent() < g_removeRetryAfter)
      return false;

   if(g_trade.OrderDelete(ticket))
   {
      g_removeRetryTicket = 0;
      g_removeRetryAfter  = 0;

      LogAlways(StringFormat("Removed invalid %s LIMIT order #%I64u", DirName(dir), ticket));
      return true;
   }

   g_removeRetryTicket = ticket;
   g_removeRetryAfter  = TimeCurrent() + InpRemoveRetrySeconds;

   LogAlways(StringFormat("Failed to remove %s LIMIT order #%I64u (retcode=%d %s); retrying in %ds",
                          DirName(dir), ticket,
                          g_trade.ResultRetcode(), g_trade.ResultComment(),
                          InpRemoveRetrySeconds));

   return false;
}

//--- broker-side minimum distance for SL / TP relative to the order price
bool StopsAreValid(const double price, const double sl, const double tp)
{
   double stops = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;

   if(stops <= 0.0)
      return true;

   if(MathAbs(price - sl) < stops)
   {
      Log(StringFormat("%s limit skipped: SL too close to price (%.5f < %.5f)",
                       DirName(g_pendingDir), MathAbs(price - sl), stops));
      return false;
   }

   if(MathAbs(tp - price) < stops)
   {
      Log(StringFormat("%s limit skipped: TP too close to price (%.5f < %.5f)",
                       DirName(g_pendingDir), MathAbs(tp - price), stops));
      return false;
   }

   return true;
}

void SyncPendingLimitOrder()
{
   int setupSeries = FindBarByTime(g_pendingSetupTime);

   if(setupSeries < 0)
      return;

   if(!TrendIsValid(setupSeries))
      return;

   if(!EntryFiltersAreValid(0))
      return;

   double price = NormalizeDouble(g_pendingEntry, _Digits);
   double sl    = NormalizeDouble(g_pendingSl, _Digits);

   double risk = (g_pendingDir > 0) ? (price - sl) : (sl - price);

   if(risk <= 0.0 || risk > g_maxSlPrice)
   {
      Log(StringFormat("%s limit setup invalid: entry=%.5f sl=%.5f", DirName(g_pendingDir), price, sl));
      return;
   }

   double tp = (g_pendingDir > 0) ? (price + InpTpR * risk) : (price - InpTpR * risk);
   tp = NormalizeDouble(tp, _Digits);

   g_pendingEntry = price;
   g_pendingTp    = tp;

   MqlTick tick;

   if(!SymbolInfoTick(_Symbol, tick))
      return;

   //--- spread filter: do not (re)place limits while the spread is too wide
   if(InpUseSpreadFilter)
   {
      long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);

      if(spread > InpMaxSpreadPoints)
         return;
   }

   double minDistance = MinOrderDistance();

   double marketDistance = (g_pendingDir > 0) ? (tick.ask - price) : (price - tick.bid);

   if(marketDistance < minDistance)
      return;

   ulong ticket = FindPendingOrder(g_pendingDir);

   if(ticket == 0)
   {
      // Original refused to re-place while a previously recorded ticket was pending.
      if(g_pendingTicket != 0)
         return;

      if((g_pendingDir > 0 && price >= tick.ask) || (g_pendingDir < 0 && price <= tick.bid))
         return;

      if(!StopsAreValid(price, sl, tp))
         return;

      double volume = NormalizeVolume(InpLot);
      string comment = StringFormat("SP2L %s LIMIT", DirName(g_pendingDir));

      bool sent;

      if(g_pendingDir > 0)
         sent = g_trade.BuyLimit(volume, price, _Symbol, sl, tp, ORDER_TIME_GTC, 0, comment);
      else
         sent = g_trade.SellLimit(volume, price, _Symbol, sl, tp, ORDER_TIME_GTC, 0, comment);

      uint retcode = g_trade.ResultRetcode();

      if(sent && (retcode == TRADE_RETCODE_DONE || retcode == TRADE_RETCODE_PLACED))
      {
         g_pendingTicket = g_trade.ResultOrder();

         LogAlways(StringFormat("%s LIMIT placed at %.5f (SL %.5f, TP %.5f, #%I64u)",
                                DirName(g_pendingDir), price, sl, tp, g_pendingTicket));
      }
      else
      {
         LogAlways(StringFormat("%s LIMIT failed: retcode=%d %s",
                                DirName(g_pendingDir), retcode, g_trade.ResultComment()));
      }

      return;
   }

   //--- order already exists: move it only when the limit price improved
   if(!OrderSelect(ticket))
      return;

   double oldPrice = NormalizeDouble(OrderGetDouble(ORDER_PRICE_OPEN), _Digits);

   if(MathAbs(oldPrice - price) < _Point / 2.0)
      return;

   if(!StopsAreValid(price, sl, tp))
      return;

   if(g_trade.OrderModify(ticket, price, sl, tp, ORDER_TIME_GTC, 0))
   {
      LogAlways(StringFormat("%s LIMIT moved from %.5f to %.5f (#%I64u)",
                             DirName(g_pendingDir), oldPrice, price, ticket));
   }
   else
   {
      Log(StringFormat("%s LIMIT modify failed: retcode=%d %s",
                       DirName(g_pendingDir), g_trade.ResultRetcode(), g_trade.ResultComment()));
   }
}

//====================================================================
// OPEN POSITION MANAGEMENT
//====================================================================

//--- selects the EA position (symbol + magic); leaves it selected on success
bool SelectEaPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);

      if(ticket == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;

      if((long)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;

      return true;
   }

   return false;
}

//--- initial risk in price terms, recovered from the fixed TP distance
double PositionRisk(const double entry, const double tp)
{
   if(InpTpR <= 0.0)
      return 0.0;

   return MathAbs(tp - entry) / InpTpR;
}

//--- minimum broker stop distance check for position SL/TP
bool PositionStopsOk(const double price, const double sl)
{
   double stops = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;

   if(stops <= 0.0)
      return true;

   return (MathAbs(price - sl) >= stops);
}

void ManageOpenPosition()
{
   if(!SelectEaPosition())
      return;

   ulong    ticket = (ulong)PositionGetInteger(POSITION_TICKET);
   long     type   = PositionGetInteger(POSITION_TYPE);
   double   entry  = PositionGetDouble(POSITION_PRICE_OPEN);
   double   sl     = PositionGetDouble(POSITION_SL);
   double   tp     = PositionGetDouble(POSITION_TP);
   double   volume = PositionGetDouble(POSITION_VOLUME);
   datetime opened = (datetime)PositionGetInteger(POSITION_TIME);

   if(ticket != g_mgmtTicket)
   {
      g_mgmtTicket  = ticket;
      g_partialDone = false;
      g_beDiagDone  = false;
   }

   double risk = PositionRisk(entry, tp);

   if(risk <= 0.0)
      return;

   MqlTick tick;

   if(!SymbolInfoTick(_Symbol, tick))
      return;

   double price = (type == POSITION_TYPE_BUY) ? tick.bid : tick.ask;
   double rMult = (type == POSITION_TYPE_BUY) ? (price - entry) / risk : (entry - price) / risk;

   //--- 1. maximum holding time
   if(InpMaxHoldingMinutes > 0 && (TimeCurrent() - opened) >= (long)InpMaxHoldingMinutes * 60)
   {
      if(g_trade.PositionClose(ticket, InpSlippagePoints))
         LogAlways(StringFormat("Position #%I64u closed: max holding time (%d min) reached",
                                ticket, InpMaxHoldingMinutes));
      return;
   }

   //--- 2. partial take profit
   if(InpUsePartialTp && !g_partialDone && rMult >= InpPartialAtR)
   {
      double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);

      if(step <= 0.0)
         step = 0.01;

      //--- floor the partial volume to the step grid: never round up to the full position
      double part = MathFloor((volume * InpPartialPercent / 100.0) / step + 0.5) * step;
      part = NormalizeDouble(part, 8);

      double rest = NormalizeDouble(volume - part, 8);

      if(part >= vmin && rest >= vmin)
      {
         if(g_trade.PositionClosePartial(ticket, part, InpSlippagePoints) ||
            g_trade.ResultRetcode() == TRADE_RETCODE_DONE)
         {
            g_partialDone = true;
            LogAlways(StringFormat("Position #%I64u partial close %.2f lots at %.2fR", ticket, part, rMult));
         }
         else
         {
            LogAlways(StringFormat("Position #%I64u partial close failed: retcode=%d %s",
                                   ticket, g_trade.ResultRetcode(), g_trade.ResultComment()));
         }
      }
      else
      {
         //--- position too small to split: never retry on every tick
         g_partialDone = true;
      }
   }

   //--- 3. breakeven
   if(InpUseBreakeven && rMult >= InpBreakevenAtR)
   {
      double buffer = InpBreakevenBufferPts * _Point;
      double newSl  = (type == POSITION_TYPE_BUY) ? (entry + buffer) : (entry - buffer);
      newSl = NormalizeDouble(newSl, _Digits);

      bool improves = (type == POSITION_TYPE_BUY) ? (newSl > sl + _Point / 2.0)
                                                  : ((sl == 0.0) || (newSl < sl - _Point / 2.0));

      if(improves && PositionStopsOk(price, newSl))
      {
         if(g_trade.PositionModify(ticket, newSl, tp) ||
            g_trade.ResultRetcode() == TRADE_RETCODE_DONE)
         {
            LogAlways(StringFormat("Position #%I64u SL -> breakeven %.5f", ticket, newSl));
         }
         else if(!g_beDiagDone)
         {
            g_beDiagDone = true;
            LogAlways(StringFormat("Position #%I64u BE modify FAILED retcode=%d %s (price=%.5f sl=%.5f newSl=%.5f)",
                                   ticket, g_trade.ResultRetcode(), g_trade.ResultComment(),
                                   price, sl, newSl));
         }
      }
   }

   //--- 4. trailing stop
   if(InpUseTrailStop && rMult >= InpTrailStartR)
   {
      double newSl = (type == POSITION_TYPE_BUY) ? (price - InpTrailDistanceR * risk)
                                                 : (price + InpTrailDistanceR * risk);
      newSl = NormalizeDouble(newSl, _Digits);

      bool improves = (type == POSITION_TYPE_BUY) ? (newSl > sl + _Point / 2.0)
                                                  : ((sl == 0.0) || (newSl < sl - _Point / 2.0));

      if(improves && PositionStopsOk(price, newSl))
      {
         if(g_trade.PositionModify(ticket, newSl, tp) ||
            g_trade.ResultRetcode() == TRADE_RETCODE_DONE)
            LogAlways(StringFormat("Position #%I64u SL trailed to %.5f", ticket, newSl));
      }
   }
}

//====================================================================
// STRATEGY (mirrors Python Strategy() + main-loop post-processing)
//====================================================================

void RunStrategy()
{
   //--- 1. trail the existing limit first (update_pending_limit)
   if(g_pendingDir != 0)
      TrailPendingLimit();

   //--- 2. detect a setup on the current window; a fresh setup overwrites
   bool buy  = (InpTradeSide != SP2L_SHORT_ONLY) && DetectBuySetup();
   bool sell = (InpTradeSide != SP2L_LONG_ONLY)  && DetectSellSetup();

   if(buy && !sell)
      CreatePendingBuy();
   else if(sell && !buy)
      CreatePendingSell();

   //--- 3. invalidate or sync
   if(g_pendingDir != 0 && !g_status)
   {
      if(PendingIsInvalid())
      {
         if(RemovePendingOrder(g_pendingDir))
            ClearPending();
      }
      else
      {
         SyncPendingLimitOrder();
      }
   }
}

//====================================================================
// EVENT HANDLERS
//====================================================================

int OnInit()
{
   //--- input validation
   if(InpLot <= 0.0)
   {
      Print("[SP2L] InpLot must be > 0");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpGapPoints < 0 || InpMaxSlDistancePoints <= 0)
   {
      Print("[SP2L] InpGapPoints must be >= 0 and InpMaxSlDistancePoints > 0");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpEmaPeriod < 1 || InpAdxPeriod < 1)
   {
      Print("[SP2L] InpEmaPeriod and InpAdxPeriod must be >= 1");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpUseSessionFilter &&
      (InpSessionStartHour < 0 || InpSessionEndHour > 24 ||
       InpSessionStartHour >= InpSessionEndHour))
   {
      Print("[SP2L] Invalid session hours");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpUseHourFilter &&
      (InpHourFrom < 0 || InpHourTo > 24 || InpHourFrom >= InpHourTo))
   {
      Print("[SP2L] Invalid server-hour filter range");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpUsePartialTp && (InpPartialPercent <= 0.0 || InpPartialPercent >= 100.0))
   {
      Print("[SP2L] InpPartialPercent must be > 0 and < 100");
      return INIT_PARAMETERS_INCORRECT;
   }

   //--- trading object
   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpSlippagePoints);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetAsyncMode(false);

   //--- indicator handles
   g_emaHandle = iMA(_Symbol, PERIOD_CURRENT, InpEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);

   if(g_emaHandle == INVALID_HANDLE)
   {
      Print("[SP2L] Failed to create EMA handle");
      return INIT_FAILED;
   }

   g_adxHandle = iADX(_Symbol, PERIOD_CURRENT, InpAdxPeriod);

   if(g_adxHandle == INVALID_HANDLE)
   {
      Print("[SP2L] Failed to create ADX handle");
      IndicatorRelease(g_emaHandle);
      g_emaHandle = INVALID_HANDLE;
      return INIT_FAILED;
   }

   //--- price equivalents of the point-based settings
   g_pGapPrice  = (double)InpGapPoints * _Point;
   g_maxSlPrice = (double)InpMaxSlDistancePoints * _Point;

   //--- reset state
   ClearPending();
   g_status            = false;
   g_cooldownUntil     = 0;
   g_removeRetryTicket = 0;
   g_removeRetryAfter  = 0;
   g_mgmtTicket        = 0;
   g_partialDone       = false;
   g_beDiagDone        = false;

   PrintFormat("[SP2L] init: %s %s point=%.5f digits=%d gap=%.5f maxSL=%.5f",
               _Symbol, EnumToString((ENUM_TIMEFRAMES)Period()), _Point, _Digits,
               g_pGapPrice, g_maxSlPrice);

   if(InpUseSecondEntry)
   {
      Print("[SP2L] NOTE: InpUseSecondEntry is enabled, but the original bot never ",
            "placed a second order (it only logged it). This EA reproduces that: ",
            "no second entry is executed.");
   }

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(g_emaHandle != INVALID_HANDLE)
   {
      IndicatorRelease(g_emaHandle);
      g_emaHandle = INVALID_HANDLE;
   }

   if(g_adxHandle != INVALID_HANDLE)
   {
      IndicatorRelease(g_adxHandle);
      g_adxHandle = INVALID_HANDLE;
   }
}

void OnTick()
{
   if(!LoadRates())
      return;

   //--- existing position for this magic: nothing else to do
   if(PositionExists())
   {
      if(!g_status)
      {
         LogAlways("Abnormal state: position exists but status was false - resynchronised");
         g_status = true;
         ClearPending();
      }

      ManageOpenPosition();
      return;
   }

   //--- position just closed
   if(g_status)
   {
      g_status = false;
      ClearPending();

      g_mgmtTicket  = 0;
      g_partialDone = false;

      g_cooldownUntil = TimeCurrent() + InpCooldownSeconds;

      LogAlways("Position closed / SL or TP hit - cooldown started");

      return;
   }

   //--- per-timeframe cooldown
   if(TimeCurrent() < g_cooldownUntil)
      return;

   RunStrategy();
}
//+------------------------------------------------------------------+
