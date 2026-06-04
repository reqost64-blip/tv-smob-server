//+------------------------------------------------------------------+
//| ORB_UNIFIED_PRO.mq5                                               |
//| Unified Native MT5 Expert Advisor                                 |
//| ORB + VWAP + RSI + Order Flow Proxy                               |
//| Auto-detects NAS100/SP500/DJ30/BTCUSD/GER40 profile               |
//| Native Render/Telegram alerts + screenshots                        |
//+------------------------------------------------------------------+
#property strict
#property version   "2.60"
#property description "Unified ORB/VWAP/RSI/OF EA for NAS100/SP500/DJ30/BTCUSD/GER40 with auto profile detection, BE touch exit"
#define EA_VERSION "2.6.0"
#define EA_BUILD   "20260604"

#include <Trade/Trade.mqh>

CTrade trade;

//====================================================================
// ENUMS - English labels are shown in MT5 through comments
//====================================================================
enum ENUM_DIRECTION_MODE
{
   DIR_BOTH       = 0,   // Trade Long and Short
   DIR_LONG_ONLY  = 1,   // Long only
   DIR_SHORT_ONLY = -1   // Short only
};

enum ENUM_STOP_MODE
{
   STOP_OPPOSITE_ORB = 0, // Stop beyond opposite ORB boundary
   STOP_SIGNAL       = 1, // Stop beyond signal candle
   STOP_ATR          = 2, // ATR stop
   STOP_ORB_RANGE    = 3, // Stop by ORB range size
   STOP_VWAP         = 4  // VWAP stop
};

enum ENUM_TP_MODE
{
   TP_R_MULTIPLE  = 0, // Take profit by R multiple
   TP_ATR         = 1, // Take profit by ATR
   TP_ORB_RANGE   = 2, // Take profit by ORB range size
   TP_FIXED       = 3  // Fixed take profit
};

enum ENUM_FIXED_DISTANCE_TYPE
{
   DIST_PRICE_POINTS = 0, // Price points as in TradingView: 9 = 9.00 price units
   DIST_TICKS        = 1  // Broker ticks: 9 = 9 minimum ticks
};

enum ENUM_DISPLAY_MODE
{
   DISPLAY_ACTIVE_ONLY = 0, // Show active trade only
   DISPLAY_ALL_HISTORY = 1  // Show active and past trades
};

enum ENUM_PRESET_MODE
{
   PRESET_AUTO   = 0, // AUTO — detect instrument by symbol name
   PRESET_NAS100 = 1, // NAS100 preset
   PRESET_SP500  = 2, // SP500 preset
   PRESET_DJ30   = 3, // DJ30 preset
   PRESET_BTCUSD = 4, // BTCUSD preset
   PRESET_GER40  = 5, // GER40 preset
   PRESET_MANUAL = 6  // MANUAL — use all inputs as-is
};

//====================================================================
// INPUTS - ПРОФИЛЬ / PRESET
//====================================================================
input group "00. ПРОФИЛЬ / PRESET"
input ENUM_PRESET_MODE InpPresetMode              = PRESET_AUTO;   // Профиль инструмента (AUTO = автоопределение по символу)
input bool   InpUsePresetTradingSettings          = false;         // Применять жёсткие торговые настройки пресета (false = брать из MT5 inputs)
input bool   InpClearOldChartObjectsOnInit        = true;          // Удалить объекты всех старых профилей при старте

//====================================================================
// INPUTS - СИМВОЛ / ТОРГОВЛЯ
//====================================================================
input group "01. СИМВОЛ / ТОРГОВЛЯ"
sinput string InpVersion                  = EA_VERSION;  // Версия советника
sinput string InpBuild                    = EA_BUILD;  // Дата сборки советника
input string InpTradeSymbol               = "";  // Торговый символ, пусто = текущий график
input double InpLot                       = 5.00;  // Размер лота
input int    InpMagicNumber               = 26043001;  // Magic Number советника
input int    InpDeviationPoints           = 30;  // Максимальное проскальзывание в пунктах
input bool   InpOnePositionOnly           = true;  // Только одна позиция по этому боту
input bool   InpCloseOppositePositions    = true;  // Закрывать противоположную позицию перед новым входом
input bool   InpVerboseLogs               = true;  // Подробные логи в Experts

//====================================================================
// INPUTS - ORB НАСТРОЙКИ
//====================================================================
input group "02. ORB НАСТРОЙКИ"
input int    InpNYUtcOffsetHours          = -4;  // Смещение времени Нью-Йорка от UTC, часов
input int    InpOrbStartHourNY            = 9;  // Час начала ORB по Нью-Йорку
input int    InpOrbStartMinuteNY          = 30;  // Минута начала ORB по Нью-Йорку
input int    InpOrbEndHourNY              = 9;  // Час окончания ORB по Нью-Йорку
input int    InpOrbEndMinuteNY            = 55;  // Минута окончания ORB по Нью-Йорку
input bool   InpShowOrbLines              = true;  // Показывать линии верхней и нижней границы ORB
input bool   InpShowOrbZone               = true;  // Показывать зону ORB на графике
input bool   InpOneSignalPerDirectionDay  = true;  // Только один сигнал в каждом направлении за день

//====================================================================
// INPUTS - FILTERS
//====================================================================
input group "03. ФИЛЬТРЫ"
input bool   InpUseVWAPFilter             = true;  // Использовать VWAP как фильтр входа
input bool   InpRequireRetest             = true;  // Требовать ретест ORB перед входом
input bool   InpUseBiasRSIFilter          = true;  // Использовать RSI старшего фильтра
input bool   InpUseEntryRSIFilter         = true;  // Использовать RSI входа
input ENUM_TIMEFRAMES InpEntryRsiTF       = PERIOD_M1;  // Таймфрейм RSI входа

//====================================================================
// INPUTS - RSI LEVELS
//====================================================================
input group "04. RSI УРОВНИ"
input int    InpBiasRsiLen                = 14;  // Период RSI старшего фильтра
input int    InpEntryRsiLen               = 9;  // Период RSI входа
input double InpBiasBullLevel             = 50.0;  // Уровень RSI старшего фильтра для покупок
input double InpBiasBearLevel             = 50.0;  // Уровень RSI старшего фильтра для продаж
input double InpEntryBullLevel            = 50.0;  // Уровень RSI входа для покупок
input double InpEntryBearLevel            = 50.0;  // Уровень RSI входа для продаж

//====================================================================
// INPUTS - ORDER FLOW PROXY
//====================================================================
input group "05. ORDER FLOW PROXY"
input bool   InpUseOrderFlowFilter        = false;  // Использовать Order Flow proxy как фильтр
input int    InpDeltaSmoothingLength      = 8;  // Период сглаживания delta для диагностики Order Flow
input double InpMinSmoothedDelta          = 0.0;  // Минимальная сглаженная delta для Order Flow proxy

//====================================================================
// INPUTS - SESSION / DIRECTION
//====================================================================
input group "06. СЕССИЯ / НАПРАВЛЕНИЕ"
input ENUM_DIRECTION_MODE InpDirection    = DIR_BOTH;  // Разрешённое направление торговли
input bool   InpUseSessionFilter          = false;  // Ограничить торговлю торговой сессией
input int    InpSessionStartHourNY        = 7;  // Час начала торговой сессии по Нью-Йорку
input int    InpSessionStartMinuteNY      = 0;  // Минута начала торговой сессии по Нью-Йорку
input int    InpSessionEndHourNY          = 19;  // Час окончания торговой сессии по Нью-Йорку
input int    InpSessionEndMinuteNY        = 0;  // Минута окончания торговой сессии по Нью-Йорку
input bool   InpCloseAtSessionEnd         = false;  // Закрывать позицию при окончании торговой сессии
input bool   InpBlockWeekendNewTrades = false;  // Не открывать новые сделки в выходные

//====================================================================
// INPUTS - STOP LOSS
//====================================================================
input group "07. STOP LOSS"
input ENUM_STOP_MODE InpStopMode          = STOP_VWAP;  // Режим Stop Loss
input int    InpAtrStopLen                = 14;  // Период ATR для Stop Loss
input double InpAtrStopMult               = 1.0;  // Множитель ATR для Stop Loss
input double InpOrbRangeStopMult          = 1.0;  // Множитель диапазона ORB для Stop Loss
input ENUM_FIXED_DISTANCE_TYPE InpSLOffsetType = DIST_TICKS;  // Тип дополнительного смещения Stop Loss
input double InpStopOffsetValue           = 0.0;  // Значение дополнительного смещения Stop Loss

//====================================================================
// INPUTS - TAKE PROFITS
//====================================================================
input group "08. ТЕЙК-ПРОФИТЫ"
input ENUM_TP_MODE InpTPMode              = TP_FIXED;  // Режим Take Profit
input ENUM_FIXED_DISTANCE_TYPE InpFixedTPType = DIST_PRICE_POINTS;  // Тип фиксированной дистанции TP
input int    InpAtrTpLen                  = 14;  // Период ATR для Take Profit
input bool   InpEnableTP1                 = true;  // Включить TP1
input bool   InpEnableTP2                 = true;  // Включить TP2
input bool   InpEnableTP3                 = false;  // Включить TP3
input double InpTP1R                      = 1.0;  // Дистанция TP1 в R
input double InpTP2R                      = 1.5;  // Дистанция TP2 в R
input double InpTP3R                      = 2.5;  // Дистанция TP3 в R
input double InpTP1ATR                    = 1.0;  // Множитель ATR для TP1
input double InpTP2ATR                    = 2.0;  // Множитель ATR для TP2
input double InpTP3ATR                    = 3.0;  // Множитель ATR для TP3
input double InpTP1ORB                    = 1.0;  // Множитель ORB для TP1
input double InpTP2ORB                    = 2.0;  // Множитель ORB для TP2
input double InpTP3ORB                    = 3.0;  // Множитель ORB для TP3
input double InpTP1Fixed                  = 9.0;  // Фиксированная дистанция TP1
input double InpTP2Fixed                  = 40.0;  // Фиксированная дистанция TP2
input double InpTP3Fixed                  = 25.0;  // Фиксированная дистанция TP3
input double InpTP1Percent                = 75.0;  // Процент позиции для закрытия на TP1
input double InpTP2Percent                = 25.0;  // Процент позиции для закрытия на TP2
input double InpTP3Percent                = 1.0;  // Процент позиции для закрытия на TP3

//====================================================================
// INPUTS - EXIT MANAGEMENT
//====================================================================
input group "09. СОПРОВОЖДЕНИЕ ВЫХОДА"
input bool   InpMoveStopToBEAfterTP1      = true;  // Перенести Stop Loss в безубыток после TP1
input bool   InpCloseAtBEOnTouch          = true;   // Закрывать позицию при касании цены входа (BE touch)
input int    InpBETriggerBufferPoints     = 0;      // Буфер BE touch в пунктах (0 = точное касание)
input bool   InpUseReturnInsideOrbExit    = false;  // Закрывать при возврате внутрь ORB
input bool   InpUseVWAPInvalidExit        = false;  // Закрывать при пробое VWAP против позиции

//====================================================================
// INPUTS - VISUALS
//====================================================================
input group "10. ВИЗУАЛИЗАЦИЯ"
input ENUM_DISPLAY_MODE InpDisplayMode    = DISPLAY_ALL_HISTORY;  // Режим отображения истории и текущих уровней
input bool   InpShowVWAP                  = true;  // Показывать линию VWAP
input bool   InpShowSignals               = true;  // Показывать метки сигналов на графике
input bool   InpShowTradeLevels           = true;  // Показывать уровни Entry, SL и TP
input bool   InpShowHistoryTrades         = false;  // Показывать исторические сделки на графике
input bool   InpShowStatusPanel           = false;  // Показывать большую диагностическую панель статуса
input bool   InpShowStatsPanel            = false;  // Показывать панель виртуальной статистики
input int    InpHistoryDaysToDraw         = 7;  // Сколько дней истории рисовать на графике
input int    InpLineExtendBars            = 100;  // На сколько баров продлевать линии уровней
input int    InpPanelX                    = 12;  // X-позиция диагностических панелей
input int    InpPanelY                    = 18;  // Y-позиция диагностических панелей
input color  InpVWAPColor                 = clrGold;  // Цвет линии VWAP
input color  InpOrbHighColor              = clrLime;  // Цвет верхней границы ORB
input color  InpOrbLowColor               = clrTomato;  // Цвет нижней границы ORB
input color  InpOrbZoneColor              = clrDarkSlateGray;  // Цвет зоны ORB
input color  InpEntryColor                = clrWhite;  // Цвет линии входа
input color  InpSLColor                   = clrRed;  // Цвет линии Stop Loss
input color  InpTPColor                   = clrLimeGreen;  // Цвет линий Take Profit
input color  InpBEColor                   = clrGold;  // Цвет линии безубытка
input color  InpPanelTextColor            = clrWhite;  // Цвет текста диагностических панелей
input color  InpPanelGoodColor            = clrLime;  // Цвет положительного статуса панели
input color  InpPanelBadColor             = clrTomato;  // Цвет отрицательного статуса панели

//====================================================================
// INPUTS - VIRTUAL HISTORY
//====================================================================
input group "11. ВИРТУАЛЬНАЯ ИСТОРИЯ"
input bool   InpCalculateVirtualHistory   = false;  // Рассчитывать виртуальную историю сделок
input int    InpVirtualHistoryDays        = 20;  // Глубина виртуальной истории, дней
input double InpVirtualStartBalance       = 10000.0;  // Стартовый баланс виртуальной истории
input double InpVirtualLot                = 0.01;  // Лот для виртуальной истории
input bool   InpVirtualUseCurrentSpread   = true;  // Использовать текущий спред в виртуальной истории
input bool   InpSendBacktestOnStart       = false;  // Отправить виртуальный backtest при запуске
input bool   InpSendFullHistory           = false;  // Отправлять полную историю сделок

//====================================================================
// INPUTS - TELEGRAM / RENDER
//====================================================================
input group "12. TELEGRAM / RENDER"
input bool   InpEnableNotifications       = true;  // Отправлять события в Telegram/Render
input string InpRenderBaseUrl             = "https://tv-smob-server-1.onrender.com";  // URL Render-сервера
input string InpNativeSecret              = "";  // MT5_NATIVE_SECRET из Render
input string InpBotId                     = "NAS100_ORB_VWAP_RSI_OF";  // ID бота для Telegram/Render
input bool   InpSendAccountOnEvents       = true;  // Отправлять состояние счёта вместе с событиями
input int    InpSendAccountEveryMinutes   = 1;  // Отправлять состояние счёта раз в N минут

//====================================================================
// INPUTS - SCREENSHOTS
//====================================================================
input group "13. СКРИНШОТЫ"
input bool   InpSendScreenshots           = true;  // Отправлять скриншоты в Telegram
input int    InpScreenshotWidth           = 1280;  // Ширина скриншота, пикселей
input int    InpScreenshotHeight          = 720;  // Высота скриншота, пикселей
input bool   InpScreenshotOnOpen          = true;  // Отправлять скриншот при открытии сделки
input bool   InpScreenshotOnTP1           = true;  // Отправлять скриншот при TP1
input bool   InpScreenshotOnTP2           = false;  // Отправлять скриншот при TP2
input bool   InpScreenshotOnClose         = true;  // Отправлять скриншот при закрытии сделки

//====================================================================
// INPUTS - ENTRY DIAGNOSTICS
//====================================================================
input group "14. ДИАГНОСТИКА ВХОДА"
input bool   InpShowEntryDiagnostics      = true;  // Показывать диагностику входа в большой панели
input bool   InpLogEntryDiagnostics       = true;  // Логировать диагностику входа в Experts
input bool   InpUseVisualSignalEngine     = true;  // Использовать визуальный движок сигналов для истории

//====================================================================
// INPUTS - REMOTE CONTROL
//====================================================================
input group "15. УДАЛЁННОЕ УПРАВЛЕНИЕ"
input bool InpEnableRemoteControl = true;  // Включить удалённое управление через Telegram/Render
input int  InpRemoteConfigEverySeconds = 30;  // Частота проверки удалённых настроек, секунд
input bool InpBlockNewTradesIfConfigFails = false;  // Блокировать новые сделки, если конфиг Render недоступен

input group "16. LIVE RETRY / DEBUG"
input bool   InpRetryMissedLiveSignal  = true;  // Повторить live-сигнал при временной ошибке открытия
input int    InpRetrySignalSeconds     = 45;  // Сколько секунд повторять live-сигнал
input bool   InpRetryOnlySameBar       = true;  // Повторять сигнал только в пределах того же бара
input bool   InpExecutionDryCheckOnly  = false;  // Только проверять возможность входа без открытия сделки
input bool   InpDebugForcePrintSignalTrace = true;  // Принудительно печатать trace сигнала
input bool   InpDebugDoNotOpenTrades = false;  // Debug-режим: сигнал есть, но сделку не открывать

input group "17. СИНХРОНИЗАЦИЯ ИСТОРИИ / СТАТИСТИКА"
input bool   InpSendFullHistoryOnStart = false;  // Отправить всю историю при запуске
input int    InpFullHistoryDays = 3650;  // Глубина полной истории для отправки, дней
input int    InpHistoryBatchSize = 100;  // Максимум сделок в одной отправке истории
input bool   InpSendIncrementalHistory = true;  // Отправлять новые сделки из истории инкрементально
input int    InpHistorySyncEveryMinutes = 15;  // Частота синхронизации истории, минут
//====================================================================
// GLOBALS
//====================================================================
// Active profile settings — set by ApplyPreset() in OnInit
string g_activePrefixBase     = "ORBVRSI_UNIFIED_PRO_";
int    g_activeMagic          = 26043001;
double g_activeLot            = 5.0;
string g_activeBotId          = "NAS100_ORB_VWAP_RSI_OF";
int    g_activeOrbStartHour   = 9;
int    g_activeOrbStartMinute = 30;
int    g_activeOrbEndHour     = 9;
int    g_activeOrbEndMinute   = 55;
bool   g_activeUseVWAP        = true;
double g_activeBiasBullLevel  = 50.0;
double g_activeBiasBearLevel  = 50.0;
double g_activeEntryBullLevel = 50.0;
double g_activeEntryBearLevel = 50.0;
double g_activeTP1Fixed       = 9.0;
double g_activeTP2Fixed       = 40.0;
double g_activeTP3Fixed       = 25.0;
double g_activeTP1Percent     = 75.0;
double g_activeTP2Percent     = 25.0;
double g_activeTP3Percent     = 1.0;
bool   g_activeEnableTP3      = false;
bool   g_activeReturnOrb      = false;
bool   g_activeVWAPExit       = false;
bool   g_activeBlockWeekend   = false;
bool   g_beClosedByTouch      = false;

struct PositionPlan
{
   bool     active;
   ulong    ticket;
   ulong    position_id;
   int      side;
   double   entry;
   double   sl;
   double   tp1;
   double   tp2;
   double   tp3;
   bool     tp1_done;
   bool     tp2_done;
   bool     tp3_done;
   bool     be_done;
   double   original_lot;
   datetime opened_at;
   string   trade_uid;
   bool     opened_sent;
   bool     tp1_sent;
   bool     tp2_recorded;
   bool     tp3_recorded;
   bool     closed_sent;
};

struct DealInfo
{
   ulong    ticket;
   ulong    order_ticket;
   ulong    position_id;
   datetime time;
   double   price;
   double   volume;
   double   profit;
   double   commission;
   double   swap;
   double   net;
   string   comment;
   int      deal_type;
   int      entry_type;
};

struct DealSummary
{
   int      count;
   double   total_profit;
   double   total_commission;
   double   total_swap;
   double   total_net;
   double   total_volume;
   double   exit_price;
   datetime closed_at;
   ulong    last_deal_ticket;
   string   partials_json;
};

struct VirtualStats
{
   int    trades;
   int    wins;
   int    losses;
   int    breakeven;
   double gross_profit;
   double gross_loss;
   double net_pnl;
   double best_trade;
   double worst_trade;
   double balance;
};
struct VirtualHistoryTrade
{
   int      side;
   double   entry;
   double   sl;
   double   tp1;
   double   tp2;
   double   tp3;
   datetime openTime;
   datetime closeTime;
   double   exitPrice;
   double   profit;
};
struct SignalDecision
{
   bool     signal;
   int      side;

   string   signal_reason;
   string   block_reason;

   bool     orb_locked;
   bool     session_ok;
   bool     remote_ok;
   bool     position_ok;

   bool     long_break;
   bool     short_break;
   bool     long_armed;
   bool     short_armed;
   bool     long_retest;
   bool     short_retest;

   bool     vwap_long_ok;
   bool     vwap_short_ok;
   bool     bias_long_ok;
   bool     bias_short_ok;
   bool     entry_long_ok;
   bool     entry_short_ok;
   bool     orderflow_long_ok;
   bool     orderflow_short_ok;

   bool     long_filter;
   bool     short_filter;
   bool     long_raw_signal;
   bool     short_raw_signal;
   bool     long_signal;
   bool     short_signal;

   bool     valid_long_risk;
   bool     valid_short_risk;

   bool     breakout_ok;
   bool     retest_ok;
   bool     vwap_ok;
   bool     bias_rsi_ok;
   bool     entry_rsi_ok;
   bool     orderflow_ok;
   bool     direction_ok;
   bool     one_signal_ok;
   bool     one_position_ok;
   bool     trade_permission_ok;

   double   entry_ref;
   double   sl_ref;
   double   tp1_ref;
   double   tp2_ref;
   double   tp3_ref;

   datetime signal_time;
};

PositionPlan g_plan;
VirtualStats g_stats;
VirtualHistoryTrade g_virtualHistory[];

string g_symbol;
int    g_digits;
double g_point;
double g_tickSize;
double g_tickValue;

datetime g_lastBarTime = 0;
datetime g_lastAccountSend = 0;
int g_lastDateKey = -1;

bool   g_orbLocked = false;
double g_orbHigh = 0.0;
double g_orbLow = 0.0;
bool   g_longTriggeredToday = false;
bool   g_shortTriggeredToday = false;
bool   g_longArmed = false;
bool   g_shortArmed = false;

double g_vwap = 0.0;
double g_vwapPV = 0.0;
double g_vwapVol = 0.0;
double g_cvd = 0.0;
double g_prevCvd = 0.0;
double g_deltaEma = 0.0;
double g_lastDeltaRaw = 0.0;
double g_lastKnownPositionProfit = 0.0;
double g_initialRiskMoney = 0.0;
double g_openEntryPrice = 0.0;
double g_openLots = 0.0;
datetime g_openTime = 0;
double g_tp1ProfitMoney = 0.0;
double g_tp2ProfitMoney = 0.0;
bool g_backtestHistorySent = false;
datetime g_lastHistorySync = 0;

int g_biasRsiHandle = INVALID_HANDLE;
int g_entryRsiHandle = INVALID_HANDLE;
int g_atrStopHandle = INVALID_HANDLE;
int g_atrTpHandle = INVALID_HANDLE;

string g_lastBlockReason = "waiting";
datetime g_lastOpenFailedNotify = 0;
string g_lastOpenFailedReason = "";
string g_lastOpenError = "";
int g_lastSignalSide = 0;
datetime g_lastSignalTime = 0;
string g_lastSignalReason = "";
bool g_pendingSignalActive = false;
int g_pendingSignalSide = 0;
datetime g_pendingSignalTime = 0;
datetime g_pendingSignalBarTime = 0;
double g_pendingSignalEntryRef = 0.0;
double g_pendingSignalSLRef = 0.0;
string g_pendingSignalReason = "";
bool g_signalEngineHistoryMode = false;
double g_historyBiasRsi = 50.0;
double g_historyEntryRsi = 50.0;
double g_historyAtrStop = 0.0;
double g_historyAtrTp = 0.0;
bool     g_remoteTradingEnabled = true;
bool     g_remoteConfigOk = false;
datetime g_lastRemoteConfigCheck = 0;
string   g_remoteBlockReason = "remote not checked";
string   g_remoteLastError = "";
int      g_remoteLastHttpCode = 0;
bool g_diagLongBreak = false;
bool g_diagLongRetest = false;
bool g_diagLongFilter = false;
bool g_diagLongSignal = false;
bool g_diagShortBreak = false;
bool g_diagShortRetest = false;
bool g_diagShortFilter = false;
bool g_diagShortSignal = false;
bool g_diagVwapLongOk = false;
bool g_diagVwapShortOk = false;
bool g_diagBiasLongOk = false;
bool g_diagBiasShortOk = false;
bool g_diagEntryLongOk = false;
bool g_diagEntryShortOk = false;
bool g_diagOfLongOk = false;
bool g_diagOfShortOk = false;
bool g_diagSessionOk = false;
bool g_diagPositionOk = false;
bool g_diagDirectionLongOk = false;
bool g_diagDirectionShortOk = false;
bool g_diagOneLongOk = false;
bool g_diagOneShortOk = false;
bool g_diagRiskLongOk = false;
bool g_diagRiskShortOk = false;
double g_diagBiasRsiValue = 0.0;
double g_diagEntryRsiValue = 0.0;
bool g_diagIsESorNAS = false;

//====================================================================
// BASIC UTILS
//====================================================================
void Log(string msg)
{
   if(InpVerboseLogs)
   {
      string p = g_activeBotId;
      if(p == "")
         p = g_symbol;
      Print(p + " " + msg);
   }
}

void SetBlockReason(string reason)
{
   g_lastBlockReason = reason;
}

bool CanNotifyOpenFailed(string reason)
{
   datetime now = TimeCurrent();
   if(reason == g_lastOpenFailedReason && now - g_lastOpenFailedNotify < 60)
      return false;

   g_lastOpenFailedReason = reason;
   g_lastOpenFailedNotify = now;
   return true;
}

void NotifyOpenFailed(string reason, int side, bool sendAccountSnapshot = true)
{
   if(CanNotifyOpenFailed(reason))
   {
      SendNativeEvent("open_failed", reason, 0.0, side);
      if(sendAccountSnapshot)
         SendNativeAccount();
   }
}

bool IsTradingAllowedNow(string &reason)
{
   reason = "";

   if(TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) == 0)
   {
      reason = "terminal algo trading disabled";
      return false;
   }

   if(MQLInfoInteger(MQL_TRADE_ALLOWED) == 0)
   {
      reason = "EA algo trading disabled in properties";
      return false;
   }

   if(AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) == 0)
   {
      reason = "account trading not allowed";
      return false;
   }

   if(SymbolInfoInteger(g_symbol, SYMBOL_SELECT) == 0)
   {
      reason = "symbol not selected";
      return false;
   }

   long tradeMode = SymbolInfoInteger(g_symbol, SYMBOL_TRADE_MODE);

   if(tradeMode == SYMBOL_TRADE_MODE_DISABLED)
   {
      reason = "symbol trading disabled by broker";
      return false;
   }

   if(tradeMode == SYMBOL_TRADE_MODE_CLOSEONLY)
   {
      reason = "symbol close-only mode";
      return false;
   }

   double ask = SymbolInfoDouble(g_symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(g_symbol, SYMBOL_BID);

   if(ask <= 0.0 || bid <= 0.0)
   {
      reason = "invalid bid/ask";
      return false;
   }

   return true;
}

string Prefix()
{
   return g_activePrefixBase + IntegerToString(g_activeMagic) + "_";
}

string ObjName(string suffix)
{
   return Prefix() + suffix;
}

ENUM_PRESET_MODE NormalizeSymbolToProfile(string sym)
{
   string s = sym;
   StringToUpper(s);
   if(StringFind(s, "NAS") >= 0 || StringFind(s, "NQ") >= 0 || StringFind(s, "US100") >= 0
      || StringFind(s, "NASDAQ") >= 0 || StringFind(s, "USTEC") >= 0)
      return PRESET_NAS100;
   if(StringFind(s, "SP500") >= 0 || StringFind(s, "US500") >= 0 || StringFind(s, "SPX") >= 0
      || StringFind(s, "SPY") >= 0)
      return PRESET_SP500;
   if(StringFind(s, "DJ30") >= 0 || StringFind(s, "DOW") >= 0 || StringFind(s, "US30") >= 0
      || StringFind(s, "DJIA") >= 0 || StringFind(s, "WALL") >= 0)
      return PRESET_DJ30;
   if(StringFind(s, "BTC") >= 0 || StringFind(s, "BITCOIN") >= 0)
      return PRESET_BTCUSD;
   if(StringFind(s, "GER40") >= 0 || StringFind(s, "GER") >= 0 || StringFind(s, "DAX") >= 0
      || StringFind(s, "DE40") >= 0)
      return PRESET_GER40;
   return PRESET_MANUAL;
}

// Level B helper: copy all trading settings from MT5 inputs into g_active* variables.
// Called when InpUsePresetTradingSettings=false (default) or mode=PRESET_MANUAL.
void ApplyTradingInputs()
{
   g_activeLot            = InpLot;
   g_activeOrbStartHour   = InpOrbStartHourNY;
   g_activeOrbStartMinute = InpOrbStartMinuteNY;
   g_activeOrbEndHour     = InpOrbEndHourNY;
   g_activeOrbEndMinute   = InpOrbEndMinuteNY;
   g_activeUseVWAP        = InpUseVWAPFilter;
   g_activeBiasBullLevel  = InpBiasBullLevel;
   g_activeBiasBearLevel  = InpBiasBearLevel;
   g_activeEntryBullLevel = InpEntryBullLevel;
   g_activeEntryBearLevel = InpEntryBearLevel;
   g_activeTP1Fixed       = InpTP1Fixed;
   g_activeTP2Fixed       = InpTP2Fixed;
   g_activeTP3Fixed       = InpTP3Fixed;
   g_activeTP1Percent     = InpTP1Percent;
   g_activeTP2Percent     = InpTP2Percent;
   g_activeTP3Percent     = InpTP3Percent;
   g_activeEnableTP3      = InpEnableTP3;
   g_activeReturnOrb      = InpUseReturnInsideOrbExit;
   g_activeVWAPExit       = InpUseVWAPInvalidExit;
   g_activeBlockWeekend   = InpBlockWeekendNewTrades;
}

// Level B helper: apply hardcoded preset trading defaults.
// Called only when InpUsePresetTradingSettings=true AND mode != PRESET_MANUAL.
void ApplyTradingPresetDefaults(ENUM_PRESET_MODE mode)
{
   if(mode == PRESET_NAS100)
   {
      g_activeLot = 5.0;
      g_activeOrbStartHour = 9;  g_activeOrbStartMinute = 30;
      g_activeOrbEndHour   = 9;  g_activeOrbEndMinute   = 55;
      g_activeUseVWAP = true;
      g_activeBiasBullLevel = 50.0; g_activeBiasBearLevel = 50.0;
      g_activeEntryBullLevel = 50.0; g_activeEntryBearLevel = 50.0;
      g_activeTP1Fixed = 9.0;  g_activeTP2Fixed = 40.0; g_activeTP3Fixed = 25.0;
      g_activeTP1Percent = 75.0; g_activeTP2Percent = 25.0; g_activeTP3Percent = 1.0;
      g_activeEnableTP3 = false;
      g_activeReturnOrb = false; g_activeVWAPExit = false; g_activeBlockWeekend = false;
   }
   else if(mode == PRESET_SP500)
   {
      g_activeLot = 5.0;
      g_activeOrbStartHour = 9;  g_activeOrbStartMinute = 30;
      g_activeOrbEndHour   = 10; g_activeOrbEndMinute   = 0;
      g_activeUseVWAP = true;
      g_activeBiasBullLevel = 55.0; g_activeBiasBearLevel = 45.0;
      g_activeEntryBullLevel = 55.0; g_activeEntryBearLevel = 45.0;
      g_activeTP1Fixed = 7.0;  g_activeTP2Fixed = 14.0; g_activeTP3Fixed = 21.0;
      g_activeTP1Percent = 70.0; g_activeTP2Percent = 20.0; g_activeTP3Percent = 10.0;
      g_activeEnableTP3 = true;
      g_activeReturnOrb = true; g_activeVWAPExit = true; g_activeBlockWeekend = false;
   }
   else if(mode == PRESET_DJ30)
   {
      g_activeLot = 5.0;
      g_activeOrbStartHour = 9;  g_activeOrbStartMinute = 30;
      g_activeOrbEndHour   = 9;  g_activeOrbEndMinute   = 55;
      g_activeUseVWAP = false;
      g_activeBiasBullLevel = 50.0; g_activeBiasBearLevel = 50.0;
      g_activeEntryBullLevel = 50.0; g_activeEntryBearLevel = 50.0;
      g_activeTP1Fixed = 30.0; g_activeTP2Fixed = 60.0; g_activeTP3Fixed = 90.0;
      g_activeTP1Percent = 75.0; g_activeTP2Percent = 25.0; g_activeTP3Percent = 1.0;
      g_activeEnableTP3 = false;
      g_activeReturnOrb = false; g_activeVWAPExit = false; g_activeBlockWeekend = false;
   }
   else if(mode == PRESET_BTCUSD)
   {
      g_activeLot = 1.0;
      g_activeOrbStartHour = 9;  g_activeOrbStartMinute = 15;
      g_activeOrbEndHour   = 10; g_activeOrbEndMinute   = 0;
      g_activeUseVWAP = true;
      g_activeBiasBullLevel = 50.0; g_activeBiasBearLevel = 50.0;
      g_activeEntryBullLevel = 50.0; g_activeEntryBearLevel = 50.0;
      g_activeTP1Fixed = 25.0; g_activeTP2Fixed = 50.0; g_activeTP3Fixed = 80.0;
      g_activeTP1Percent = 75.0; g_activeTP2Percent = 25.0; g_activeTP3Percent = 1.0;
      g_activeEnableTP3 = false;
      g_activeReturnOrb = true; g_activeVWAPExit = true; g_activeBlockWeekend = true;
   }
   else if(mode == PRESET_GER40)
   {
      g_activeLot = 3.0;
      g_activeOrbStartHour = 9;  g_activeOrbStartMinute = 30;
      g_activeOrbEndHour   = 10; g_activeOrbEndMinute   = 0;
      g_activeUseVWAP = false;
      g_activeBiasBullLevel = 50.0; g_activeBiasBearLevel = 50.0;
      g_activeEntryBullLevel = 50.0; g_activeEntryBearLevel = 50.0;
      g_activeTP1Fixed = 8.0;  g_activeTP2Fixed = 15.0; g_activeTP3Fixed = 25.0;
      g_activeTP1Percent = 75.0; g_activeTP2Percent = 25.0; g_activeTP3Percent = 3.0;
      g_activeEnableTP3 = false;
      g_activeReturnOrb = false; g_activeVWAPExit = false; g_activeBlockWeekend = false;
   }
   else
   {
      ApplyTradingInputs();
   }
}

void ApplyPreset()
{
   ENUM_PRESET_MODE mode = InpPresetMode;
   if(mode == PRESET_AUTO)
      mode = NormalizeSymbolToProfile(g_symbol);

   // Level A: Identity — magic/bot_id/prefix always from preset for correct Telegram/Render routing.
   if(mode == PRESET_NAS100)
   {
      g_activeMagic      = 26043001;
      g_activeBotId      = "NAS100_ORB_VWAP_RSI_OF";
      g_activePrefixBase = "ORBVRSI_NAS100_PRO_";
   }
   else if(mode == PRESET_SP500)
   {
      g_activeMagic      = 26043003;
      g_activeBotId      = "SP500_ORB_VWAP_RSI_OF";
      g_activePrefixBase = "ORBVRSI_SP500_PRO_";
   }
   else if(mode == PRESET_DJ30)
   {
      g_activeMagic      = 26043002;
      g_activeBotId      = "DJ30_ORB_VWAP_RSI_OF";
      g_activePrefixBase = "ORBVRSI_DJ30_PRO_";
   }
   else if(mode == PRESET_BTCUSD)
   {
      g_activeMagic      = 26043005;
      g_activeBotId      = "BTCUSD_ORB_VWAP_RSI_OF";
      g_activePrefixBase = "ORBVRSI_BTCUSD_PRO_";
   }
   else if(mode == PRESET_GER40)
   {
      g_activeMagic      = 26043004;
      g_activeBotId      = "GER40_ORB_VWAP_RSI_OF";
      g_activePrefixBase = "ORBVRSI_GER40_PRO_";
   }
   else // PRESET_MANUAL
   {
      g_activeMagic      = InpMagicNumber;
      g_activeBotId      = InpBotId;
      g_activePrefixBase = "ORBVRSI_UNIFIED_PRO_";
   }

   // Level B: Trading settings — source controlled by InpUsePresetTradingSettings.
   // PRESET_MANUAL always uses inputs regardless of that flag.
   if(mode == PRESET_MANUAL || !InpUsePresetTradingSettings)
      ApplyTradingInputs();
   else
      ApplyTradingPresetDefaults(mode);

   trade.SetExpertMagicNumber(g_activeMagic);
   Print("ApplyPreset: mode=", EnumToString(InpPresetMode),
         " resolved=", EnumToString(mode),
         " usePresetTrading=", (string)InpUsePresetTradingSettings,
         " magic=", g_activeMagic,
         " lot=", g_activeLot,
         " bot_id=", g_activeBotId,
         " orb=", g_activeOrbStartHour, ":", g_activeOrbStartMinute,
         "-", g_activeOrbEndHour, ":", g_activeOrbEndMinute);
}

int DateKey(datetime t)
{
   MqlDateTime dt;
   TimeToStruct(t, dt);
   return dt.year * 10000 + dt.mon * 100 + dt.day;
}

string DateKeyToString(int dk)
{
   return StringFormat("%04d.%02d.%02d", dk / 10000, (dk / 100) % 100, dk % 100);
}

datetime DayStartFromDateKey(int dk)
{
   return StringToTime(DateKeyToString(dk) + " 00:00");
}

datetime ServerTimeFromNY(int hourNY, int minuteNY, int dateKey)
{
   MqlDateTime ny;
   ny.year = dateKey / 10000;
   ny.mon  = (dateKey / 100) % 100;
   ny.day  = dateKey % 100;
   ny.hour = hourNY;
   ny.min  = minuteNY;
   ny.sec  = 0;

   datetime nyAsUtc = StructToTime(ny) - InpNYUtcOffsetHours * 3600;
   int serverOffset = (int)(TimeTradeServer() - TimeGMT());
   return nyAsUtc + serverOffset;
}

bool IsTimeInNYWindow(datetime t, int startH, int startM, int endH, int endM)
{
   int dk = DateKey(t);
   datetime start = ServerTimeFromNY(startH, startM, dk);
   datetime end   = ServerTimeFromNY(endH, endM, dk);
   return (t >= start && t < end);
}

bool HasPassedNYTime(datetime t, int h, int m)
{
   int dk = DateKey(t);
   datetime mark = ServerTimeFromNY(h, m, dk);
   return t >= mark;
}

string UtcIsoTime()
{
   MqlDateTime dt;
   TimeToStruct(TimeGMT(), dt);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ", dt.year, dt.mon, dt.day, dt.hour, dt.min, dt.sec);
}

double NormalizePrice(double price)
{
   return NormalizeDouble(price, g_digits);
}

double DistanceByType(double value, ENUM_FIXED_DISTANCE_TYPE type)
{
   if(type == DIST_TICKS)
      return value * g_tickSize;

   return value;
}

double NormalizeVolume(double vol)
{
   double minVol = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);
   double maxVol = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MAX);
   double step   = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);

   if(step <= 0.0)
      step = minVol;

   if(vol < minVol)
      return 0.0;

   if(vol > maxVol)
      vol = maxVol;

   double normalized = MathFloor(vol / step) * step;

   int volDigits = 2;
   for(int i = 0; i <= 8; i++)
   {
      double scaled = step * MathPow(10.0, i);
      if(MathAbs(scaled - MathRound(scaled)) < 0.0000001)
      {
         volDigits = i;
         break;
      }
   }

   normalized = NormalizeDouble(normalized, volDigits);
   if(normalized < minVol)
      return 0.0;

   return normalized;
}

bool CopyOne(int handle, int buffer, int shift, double &value)
{
   double arr[];
   ArraySetAsSeries(arr, true);
   if(CopyBuffer(handle, buffer, shift, 1, arr) != 1)
      return false;
   value = arr[0];
   return true;
}

double GetATR(int handle, int shift)
{
   double v = 0.0;
   if(!CopyOne(handle, 0, shift, v))
      return 0.0;
   return v;
}

double GetRSI(int handle, int shift)
{
   double v = 50.0;
   if(!CopyOne(handle, 0, shift, v))
      return 50.0;
   return v;
}
bool CopyIndicatorAtTime(int handle, datetime t, double &value)
{
   double buffer[];
   ArraySetAsSeries(buffer, true);
   if(CopyBuffer(handle, 0, t, 1, buffer) <= 0)
      return false;
   value = buffer[0];
   return true;
}

string PriceStr(double v)
{
   if(v <= 0.0)
      return "n/a";
   return DoubleToString(v, g_digits);
}

string MoneyStr(double v)
{
   string sign = v > 0.0 ? "+" : "";
   return sign + DoubleToString(v, 2);
}

string YesNo(bool v)
{
   return v ? "YES" : "NO";
}

string YN(bool v)
{
   return v ? "Y" : "N";
}

string TF(bool v)
{
   return v ? "true" : "false";
}

string SideToString(int side)
{
   if(side == 1) return "buy";
   if(side == -1) return "sell";
   return "none";
}

string JsonEscape(string text)
{
   StringReplace(text, "\\", "\\\\");
   StringReplace(text, "\"", "\\\"");
   StringReplace(text, "\r", "\\r");
   StringReplace(text, "\n", "\\n");
   StringReplace(text, "\t", "\\t");
   return text;
}

string JsonStr(string v)
{
   return "\"" + JsonEscape(v) + "\"";
}

string JsonNum(double v)
{
   return DoubleToString(v, 8);
}

string JsonPriceOrNull(double v, bool enabled)
{
   if(!enabled || v <= 0.0)
      return "null";
   return DoubleToString(v, g_digits);
}

string UrlEncode(string s)
{
   string out = "";
   int len = StringLen(s);

   for(int i = 0; i < len; i++)
   {
      ushort ch = StringGetCharacter(s, i);

      bool ok =
         (ch >= 'A' && ch <= 'Z') ||
         (ch >= 'a' && ch <= 'z') ||
         (ch >= '0' && ch <= '9') ||
         ch == '-' || ch == '_' || ch == '.' || ch == '~';

      if(ok)
         out += ShortToString(ch);
      else if(ch == ' ')
         out += "%20";
      else
         out += StringFormat("%%%02X", ch);
   }

   return out;
}

bool SendHttpGet(string endpointWithQuery, string &response, int &httpCode)
{
   response = "";
   httpCode = 0;

   string base = InpRenderBaseUrl;
   while(StringLen(base) > 0 && StringSubstr(base, StringLen(base) - 1, 1) == "/")
      base = StringSubstr(base, 0, StringLen(base) - 1);

   string url = base + endpointWithQuery;

   char data[];
   ArrayResize(data, 0);

   char result[];
   string resultHeaders = "";
   string headers = "Content-Type: application/json\r\n";

   ResetLastError();

   int code = WebRequest("GET", url, headers, 15000, data, result, resultHeaders);
   int err = GetLastError();

   httpCode = code;
   response = CharArrayToString(result, 0, -1, CP_UTF8);

   if(code < 200 || code >= 300)
   {
      if(code == -1)
         g_remoteLastError = "WebRequest failed. Check MT5 allowed URL. http=-1 GetLastError=" + IntegerToString(err);
      else
         g_remoteLastError = "Remote config failed. http=" + IntegerToString(code) + " GetLastError=" + IntegerToString(err);
      return false;
   }

   return true;
}

bool JsonHasBoolValue(string json, string key, bool expected)
{
   string compact = json;
   StringReplace(compact, " ", "");
   StringReplace(compact, "\r", "");
   StringReplace(compact, "\n", "");
   StringReplace(compact, "\t", "");

   string pattern = "\"" + key + "\":" + (expected ? "true" : "false");
   return StringFind(compact, pattern) >= 0;
}

bool ExtractEnabledFromConfig(string json, bool &enabled)
{
   if(JsonHasBoolValue(json, "enabled", true))
   {
      enabled = true;
      return true;
   }

   if(JsonHasBoolValue(json, "enabled", false))
   {
      enabled = false;
      return true;
   }

   return false;
}

bool RefreshRemoteConfig(bool force = false)
{
   if(!InpEnableRemoteControl)
   {
      g_remoteTradingEnabled = true;
      g_remoteConfigOk = true;
      g_remoteBlockReason = "remote control disabled in inputs";
      return true;
   }

   datetime now = TimeCurrent();

   if(!force && g_lastRemoteConfigCheck > 0 && now - g_lastRemoteConfigCheck < InpRemoteConfigEverySeconds)
      return g_remoteConfigOk;

   g_lastRemoteConfigCheck = now;

   if(StringLen(InpNativeSecret) <= 0)
   {
      g_remoteConfigOk = false;
      g_remoteLastError = "InpNativeSecret is empty";
      g_remoteLastHttpCode = 0;

      if(InpBlockNewTradesIfConfigFails)
      {
         g_remoteTradingEnabled = false;
         g_remoteBlockReason = "remote config failed: empty secret";
      }
      else
      {
         g_remoteTradingEnabled = true;
         g_remoteBlockReason = "InpNativeSecret is empty";
         Log("WARNING: remote config failed but fail-open mode allows trading");
      }

      return false;
   }

   string endpoint = "/api/mt5/native-config?secret=" + UrlEncode(InpNativeSecret)
                     + "&bot_id=" + UrlEncode(g_activeBotId);

   string response = "";
   int httpCode = 0;

   bool ok = SendHttpGet(endpoint, response, httpCode);
   g_remoteLastHttpCode = httpCode;

   if(!ok)
   {
      g_remoteConfigOk = false;

      if(InpBlockNewTradesIfConfigFails)
      {
         g_remoteTradingEnabled = false;
         g_remoteBlockReason = "remote config failed: block new trades";
      }
      else
      {
         g_remoteTradingEnabled = true;
         g_remoteBlockReason = "remote config failed: fail-open";
      }

      Log("REMOTE CONFIG FAILED. http=" + IntegerToString(httpCode)
          + " GetLastError=" + g_remoteLastError
          + " bot_id=" + g_activeBotId
          + " reason=" + g_remoteBlockReason);
      return false;
   }

   bool enabled = true;
   if(!ExtractEnabledFromConfig(response, enabled))
   {
      g_remoteConfigOk = false;
      g_remoteLastError = "enabled not found in config response";

      if(InpBlockNewTradesIfConfigFails)
      {
         g_remoteTradingEnabled = false;
         g_remoteBlockReason = "remote config parse failed";
      }
      else
      {
         g_remoteTradingEnabled = true;
         g_remoteBlockReason = "remote config parse failed, fail-open";
      }

      return false;
   }

   g_remoteConfigOk = true;
   g_remoteTradingEnabled = enabled;
   g_remoteLastError = "";
   g_remoteBlockReason = enabled ? "remote enabled" : "remote disabled by Telegram/Render";

   return true;
}

void ResetEntryDiagnostics(string reason = "waiting")
{
   g_lastBlockReason = reason;
   g_diagLongBreak = false;
   g_diagLongRetest = false;
   g_diagLongFilter = false;
   g_diagLongSignal = false;
   g_diagShortBreak = false;
   g_diagShortRetest = false;
   g_diagShortFilter = false;
   g_diagShortSignal = false;
   g_diagVwapLongOk = false;
   g_diagVwapShortOk = false;
   g_diagBiasLongOk = false;
   g_diagBiasShortOk = false;
   g_diagEntryLongOk = false;
   g_diagEntryShortOk = false;
   g_diagOfLongOk = false;
   g_diagOfShortOk = false;
   g_diagSessionOk = false;
   g_diagPositionOk = false;
   g_diagDirectionLongOk = false;
   g_diagDirectionShortOk = false;
   g_diagOneLongOk = false;
   g_diagOneShortOk = false;
   g_diagRiskLongOk = false;
   g_diagRiskShortOk = false;
   g_diagBiasRsiValue = 0.0;
   g_diagEntryRsiValue = 0.0;
   g_diagIsESorNAS = false;
}

//====================================================================
// WEBREQUEST / NATIVE RENDER API
//====================================================================
bool SendJsonPost(string endpoint, string json, string &response)
{
   response = "";

   if(StringLen(InpNativeSecret) <= 0)
   {
      Log("Native secret is empty. Event not sent.");
      return false;
   }

   string base = InpRenderBaseUrl;
   while(StringLen(base) > 0 && StringSubstr(base, StringLen(base) - 1, 1) == "/")
      base = StringSubstr(base, 0, StringLen(base) - 1);

   string url = base + endpoint;
   string headers = "Content-Type: application/json\r\n";

   char data[];
   int n = StringToCharArray(json, data, 0, WHOLE_ARRAY, CP_UTF8);
   if(n > 0)
      ArrayResize(data, n - 1);

   char result[];
   string resultHeaders = "";
   ResetLastError();

   int code = WebRequest("POST", url, headers, 15000, data, result, resultHeaders);
   int err = GetLastError();

   response = CharArrayToString(result, 0, -1, CP_UTF8);

   if(code < 200 || code >= 300)
   {
      Log("WebRequest failed. code=" + IntegerToString(code) + " err=" + IntegerToString(err) + " response=" + response);
      return false;
   }

   return true;
}

bool GetCurrentPositionData(double &volume, double &profit, long &type)
{
   ulong ticket = 0;
   double openPrice = 0.0, sl = 0.0;
   if(FindOurPosition(ticket, volume, openPrice, sl, type))
   {
      profit = PositionGetDouble(POSITION_PROFIT);
      return true;
   }

   volume = 0.0;
   profit = 0.0;
   type = -1;
   return false;
}

double GetLastDealProfit(string symbol, ulong magic)
{
   double profit = 0;
   double commission = 0;
   double swap = 0;

   HistorySelect(TimeCurrent() - 86400, TimeCurrent());

   int total = HistoryDealsTotal();
   ulong last_deal = 0;
   datetime last_time = 0;

   for(int i = total - 1; i >= 0; i--)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;

      string deal_symbol = HistoryDealGetString(ticket, DEAL_SYMBOL);
      ulong deal_magic = (ulong)HistoryDealGetInteger(ticket, DEAL_MAGIC);
      ENUM_DEAL_ENTRY deal_entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY);
      datetime deal_time = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);

      if(deal_symbol == symbol && deal_magic == magic &&
         (deal_entry == DEAL_ENTRY_OUT || deal_entry == DEAL_ENTRY_INOUT) &&
         deal_time > last_time)
      {
         last_deal = ticket;
         last_time = deal_time;
      }
   }

   if(last_deal > 0)
   {
      profit     = HistoryDealGetDouble(last_deal, DEAL_PROFIT);
      commission = HistoryDealGetDouble(last_deal, DEAL_COMMISSION);
      swap       = HistoryDealGetDouble(last_deal, DEAL_SWAP);
   }

   return profit + commission + swap;
}

double GetLastDealPrice(string symbol, ulong magic)
{
   HistorySelect(TimeCurrent() - 86400, TimeCurrent());
   int total = HistoryDealsTotal();
   ulong last_deal = 0;
   datetime last_time = 0;

   for(int i = total - 1; i >= 0; i--)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;

      string deal_symbol = HistoryDealGetString(ticket, DEAL_SYMBOL);
      ulong deal_magic = (ulong)HistoryDealGetInteger(ticket, DEAL_MAGIC);
      ENUM_DEAL_ENTRY deal_entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY);
      datetime deal_time = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);

      if(deal_symbol == symbol && deal_magic == magic &&
         (deal_entry == DEAL_ENTRY_OUT || deal_entry == DEAL_ENTRY_INOUT) &&
         deal_time > last_time)
      {
         last_deal = ticket;
         last_time = deal_time;
      }
   }

   if(last_deal > 0)
      return HistoryDealGetDouble(last_deal, DEAL_PRICE);
   return 0.0;
}

ulong GetSelectedPositionId()
{
   long identifier = PositionGetInteger(POSITION_IDENTIFIER);
   if(identifier > 0)
      return (ulong)identifier;
   long ticket = PositionGetInteger(POSITION_TICKET);
   return ticket > 0 ? (ulong)ticket : 0;
}

string TradeUid(ulong positionId, ulong positionTicket = 0)
{
   ulong id = positionId > 0 ? positionId : positionTicket;
   return g_activeBotId + "_" + g_symbol + "_" + IntegerToString(g_activeMagic) + "_" + StringFormat("%I64u", id);
}

string CurrentTradeUid()
{
   if(StringLen(g_plan.trade_uid) > 0)
      return g_plan.trade_uid;
   if(g_plan.position_id > 0 || g_plan.ticket > 0)
      return TradeUid(g_plan.position_id, g_plan.ticket);
   return "";
}

string EventId(string eventType, ulong dealTicket = 0)
{
   string uid = CurrentTradeUid();
   if(StringLen(uid) <= 0)
      uid = g_activeBotId + "_" + g_symbol + "_" + IntegerToString(g_activeMagic);
   if(dealTicket > 0)
      return uid + "_" + eventType + "_" + StringFormat("%I64u", dealTicket);
   return uid + "_" + eventType + "_" + TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS);
}

string GvPrefix()
{
   return "ORBVRSI_" + g_activeBotId + "_" + CurrentTradeUid() + "_";
}

bool GvFlag(string name)
{
   string key = GvPrefix() + name;
   return GlobalVariableCheck(key) && GlobalVariableGet(key) > 0.5;
}

void SetGvFlag(string name)
{
   string key = GvPrefix() + name;
   GlobalVariableSet(key, 1.0);
}

bool IsClosingDeal(ulong ticket, ulong positionId, datetime fromTime, datetime toTime)
{
   if(ticket == 0)
      return false;
   string deal_symbol = HistoryDealGetString(ticket, DEAL_SYMBOL);
   long deal_magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
   ENUM_DEAL_ENTRY entry_type = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY);
   datetime deal_time = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
   ulong deal_pos_id = (ulong)HistoryDealGetInteger(ticket, DEAL_POSITION_ID);
   if(deal_symbol != g_symbol || deal_magic != g_activeMagic)
      return false;
   if(entry_type != DEAL_ENTRY_OUT && entry_type != DEAL_ENTRY_INOUT)
      return false;
   if(positionId > 0 && deal_pos_id != positionId)
      return false;
   if(positionId == 0 && (deal_time < fromTime || deal_time > toTime))
      return false;
   return true;
}

void FillDealInfo(ulong ticket, DealInfo &deal)
{
   deal.ticket = ticket;
   deal.order_ticket = (ulong)HistoryDealGetInteger(ticket, DEAL_ORDER);
   deal.position_id = (ulong)HistoryDealGetInteger(ticket, DEAL_POSITION_ID);
   deal.time = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
   deal.price = HistoryDealGetDouble(ticket, DEAL_PRICE);
   deal.volume = HistoryDealGetDouble(ticket, DEAL_VOLUME);
   deal.profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);
   deal.commission = HistoryDealGetDouble(ticket, DEAL_COMMISSION);
   deal.swap = HistoryDealGetDouble(ticket, DEAL_SWAP);
   deal.net = deal.profit + deal.commission + deal.swap;
   deal.comment = HistoryDealGetString(ticket, DEAL_COMMENT);
   deal.deal_type = (int)HistoryDealGetInteger(ticket, DEAL_TYPE);
   deal.entry_type = (int)HistoryDealGetInteger(ticket, DEAL_ENTRY);
}

bool FindNewestClosingDeal(ulong positionId, datetime fromTime, DealInfo &deal)
{
   datetime toTime = TimeCurrent() + 60;
   if(!HistorySelect(fromTime - 60, toTime))
      return false;
   ulong best = 0;
   datetime bestTime = 0;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(!IsClosingDeal(ticket, positionId, fromTime - 60, toTime))
         continue;
      datetime dealTime = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
      if(dealTime >= fromTime && (best == 0 || dealTime > bestTime || (dealTime == bestTime && ticket > best)))
      {
         best = ticket;
         bestTime = dealTime;
      }
   }
   if(best == 0)
      return false;
   FillDealInfo(best, deal);
   return true;
}

bool CollectPositionClosingDeals(ulong positionId, datetime fromTime, datetime toTime, DealSummary &summary)
{
   summary.count = 0;
   summary.total_profit = 0.0;
   summary.total_commission = 0.0;
   summary.total_swap = 0.0;
   summary.total_net = 0.0;
   summary.total_volume = 0.0;
   summary.exit_price = 0.0;
   summary.closed_at = 0;
   summary.last_deal_ticket = 0;
   summary.partials_json = "[";

   if(!HistorySelect(fromTime - 86400, toTime + 300))
      return false;

   bool first = true;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(!IsClosingDeal(ticket, positionId, fromTime - 86400, toTime + 300))
         continue;

      DealInfo d;
      FillDealInfo(ticket, d);
      summary.count++;
      summary.total_profit += d.profit;
      summary.total_commission += d.commission;
      summary.total_swap += d.swap;
      summary.total_net += d.net;
      summary.total_volume += d.volume;
      if(d.time >= summary.closed_at)
      {
         summary.closed_at = d.time;
         summary.exit_price = d.price;
         summary.last_deal_ticket = d.ticket;
      }
      if(!first)
         summary.partials_json += ",";
      first = false;
      summary.partials_json += "{";
      summary.partials_json += "\"deal_ticket\":" + StringFormat("%I64u", d.ticket);
      summary.partials_json += ",\"close_price\":" + JsonNum(d.price);
      summary.partials_json += ",\"close_volume\":" + JsonNum(d.volume);
      summary.partials_json += ",\"profit\":" + JsonNum(d.profit);
      summary.partials_json += ",\"commission\":" + JsonNum(d.commission);
      summary.partials_json += ",\"swap\":" + JsonNum(d.swap);
      summary.partials_json += ",\"net\":" + JsonNum(d.net);
      summary.partials_json += "}";
   }
   summary.partials_json += "]";
   return summary.count > 0;
}

string DealExtraJson(string eventType, DealInfo &deal, int tpIndex, double tpPrice, double closedPercent, bool telegramSilent, bool slMovedToBE = false)
{
   double remaining = 0.0;
   long type = -1;
   double currentVolume = 0.0, currentProfit = 0.0;
   if(GetCurrentPositionData(currentVolume, currentProfit, type) && g_plan.original_lot > 0.0)
      remaining = 100.0 * currentVolume / g_plan.original_lot;

   string extra = "\"schema_version\":2";
   extra += ",\"event_id\":" + JsonStr(EventId(eventType, deal.ticket));
   extra += ",\"trade_uid\":" + JsonStr(CurrentTradeUid());
   extra += ",\"position_id\":" + StringFormat("%I64u", g_plan.position_id);
   extra += ",\"position_ticket\":" + StringFormat("%I64u", g_plan.ticket);
   extra += ",\"deal_ticket\":" + StringFormat("%I64u", deal.ticket);
   extra += ",\"order_ticket\":" + StringFormat("%I64u", deal.order_ticket);
   extra += ",\"deal_time\":" + JsonStr(TimeToString(deal.time, TIME_DATE | TIME_SECONDS));
   extra += ",\"tp_index\":" + IntegerToString(tpIndex);
   extra += ",\"tp_price\":" + JsonNum(tpPrice);
   extra += ",\"close_price\":" + JsonNum(deal.price);
   extra += ",\"close_volume\":" + JsonNum(deal.volume);
   extra += ",\"closed_percent\":" + JsonNum(closedPercent);
   extra += ",\"profit\":" + JsonNum(deal.profit);
   extra += ",\"commission\":" + JsonNum(deal.commission);
   extra += ",\"swap\":" + JsonNum(deal.swap);
   extra += ",\"net\":" + JsonNum(deal.net);
   extra += ",\"realized_net\":" + JsonNum(deal.net);
   extra += ",\"remaining_volume\":" + JsonNum(currentVolume);
   extra += ",\"remaining_percent\":" + JsonNum(remaining);
   extra += ",\"telegram_silent\":" + (telegramSilent ? "true" : "false");
   if(tpIndex == 1)
   {
      extra += ",\"sl_moved_to_be\":" + (slMovedToBE ? "true" : "false");
      extra += ",\"be_price\":" + JsonNum(g_plan.entry);
      extra += ",\"r_multiple\":" + JsonNum(g_initialRiskMoney > 0.0 ? deal.net / g_initialRiskMoney : 0.0);
   }
   return extra;
}

double GetInitialRisk(double entry, double sl, double lots, string symbol)
{
   double tick_value = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double risk_points = MathAbs(entry - sl);
   if(tick_size > 0)
      return (risk_points / tick_size) * tick_value * lots;
   return 0;
}

double GetExpectedProfit(double entry, double target, double lots, string symbol)
{
   double tick_value = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double pts = MathAbs(target - entry);
   if(tick_size > 0)
      return (pts / tick_size) * tick_value * lots;
   return 0;
}

double GetDailyPnL()
{
   datetime day_start = StringToTime(TimeToString(TimeCurrent(), TIME_DATE));
   HistorySelect(day_start, TimeCurrent());
   double daily = 0.0;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      ENUM_DEAL_ENTRY entry_type = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if(entry_type == DEAL_ENTRY_OUT || entry_type == DEAL_ENTRY_INOUT)
      {
         daily += HistoryDealGetDouble(ticket, DEAL_PROFIT);
         daily += HistoryDealGetDouble(ticket, DEAL_COMMISSION);
         daily += HistoryDealGetDouble(ticket, DEAL_SWAP);
      }
   }
   return daily;
}

void AppendVirtualHistoryTrade(int side, double entry, double sl, double tp1, double tp2, double tp3, datetime openTime, datetime closeTime, double exitPrice, double profit)
{
   int n = ArraySize(g_virtualHistory);
   ArrayResize(g_virtualHistory, n + 1);
   g_virtualHistory[n].side = side;
   g_virtualHistory[n].entry = entry;
   g_virtualHistory[n].sl = sl;
   g_virtualHistory[n].tp1 = tp1;
   g_virtualHistory[n].tp2 = tp2;
   g_virtualHistory[n].tp3 = tp3;
   g_virtualHistory[n].openTime = openTime;
   g_virtualHistory[n].closeTime = closeTime;
   g_virtualHistory[n].exitPrice = exitPrice;
   g_virtualHistory[n].profit = profit;
}

bool SendNativeHeartbeat()
{
   if(!InpEnableNotifications)
      return false;

   bool hasPosition = (PositionsTotal() > 0);
   bool algoOn = (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0 && MQLInfoInteger(MQL_TRADE_ALLOWED) != 0);
   string json = "{";
   json += "\"secret\":" + JsonStr(InpNativeSecret);
   json += ",\"source\":\"mt5_native\"";
   json += ",\"bot_id\":" + JsonStr(g_activeBotId);
   json += ",\"symbol\":" + JsonStr(g_symbol);
   json += ",\"magic_number\":" + IntegerToString(g_activeMagic);
   json += ",\"status\":" + JsonStr(hasPosition ? "position_open" : "idle");
   json += ",\"enabled\":" + (g_remoteTradingEnabled ? "true" : "false");
   json += ",\"ea_version\":" + JsonStr(EA_VERSION);
   json += ",\"ea_build\":" + JsonStr(EA_BUILD);
   json += ",\"has_position\":" + (hasPosition ? "true" : "false");
   json += ",\"algo_on\":" + (algoOn ? "true" : "false");
   json += ",\"remote_on\":" + (g_remoteTradingEnabled ? "true" : "false");
   json += ",\"last_seen\":" + JsonStr(UtcIsoTime());
   json += ",\"timestamp\":" + JsonStr(TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS));
   json += "}";

   string response;
   return SendJsonPost("/api/mt5/native-heartbeat", json, response);
}

void SendFullHistory()
{
   int days = MathMax(1, InpFullHistoryDays);
   datetime from = TimeCurrent() - (datetime)(days * 86400);
   datetime to   = TimeCurrent();

   if(!HistorySelect(from, to))
   {
      Print("HistorySelect failed");
      return;
   }

   int total = HistoryDealsTotal();
   if(total == 0)
   {
      Print("No deals in history");
      return;
   }

   int sent = 0;
   int batchIndex = 0;
   int batchSize = MathMax(1, InpHistoryBatchSize);
   string dealsJson = "";
   int inBatch = 0;
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;

      ENUM_DEAL_ENTRY entry_type = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if(entry_type != DEAL_ENTRY_OUT && entry_type != DEAL_ENTRY_INOUT) continue;

      ENUM_DEAL_TYPE deal_type = (ENUM_DEAL_TYPE)HistoryDealGetInteger(ticket, DEAL_TYPE);
      if(deal_type != DEAL_TYPE_BUY && deal_type != DEAL_TYPE_SELL) continue;

      string symbol      = HistoryDealGetString(ticket, DEAL_SYMBOL);
      long magic         = HistoryDealGetInteger(ticket, DEAL_MAGIC);
      if(symbol != g_symbol || magic != g_activeMagic)
         continue;
      double profit      = HistoryDealGetDouble(ticket, DEAL_PROFIT);
      double commission  = HistoryDealGetDouble(ticket, DEAL_COMMISSION);
      double swap        = HistoryDealGetDouble(ticket, DEAL_SWAP);
      double volume      = HistoryDealGetDouble(ticket, DEAL_VOLUME);
      double price       = HistoryDealGetDouble(ticket, DEAL_PRICE);
      datetime deal_time = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
      string comment     = HistoryDealGetString(ticket, DEAL_COMMENT);
      ulong position_id  = (ulong)HistoryDealGetInteger(ticket, DEAL_POSITION_ID);
      ulong order_ticket = (ulong)HistoryDealGetInteger(ticket, DEAL_ORDER);

      double total_profit = profit + commission + swap;
      string side = (deal_type == DEAL_TYPE_SELL) ? "sell" : "buy";
      string status = (total_profit >= 0) ? "win" : "loss";

      if(inBatch > 0) dealsJson += ",";
      inBatch++;
      sent++;

      dealsJson += "{";
      dealsJson += "\"deal_ticket\":" + StringFormat("%I64u", ticket);
      dealsJson += ",\"ticket\":" + StringFormat("%I64u", ticket);
      dealsJson += ",\"order_ticket\":" + StringFormat("%I64u", order_ticket);
      dealsJson += ",\"position_id\":" + StringFormat("%I64u", position_id);
      dealsJson += ",\"symbol\":" + JsonStr(symbol);
      dealsJson += ",\"side\":" + JsonStr(side);
      dealsJson += ",\"entry_type\":" + JsonStr(EnumToString(entry_type));
      dealsJson += ",\"deal_type\":" + JsonStr(EnumToString(deal_type));
      dealsJson += ",\"volume\":" + JsonNum(volume);
      dealsJson += ",\"lots\":" + JsonNum(volume);
      dealsJson += ",\"price\":" + JsonNum(price);
      dealsJson += ",\"exit_price\":" + JsonNum(price);
      dealsJson += ",\"deal_time\":" + JsonStr(TimeToString(deal_time, TIME_DATE | TIME_SECONDS));
      dealsJson += ",\"close_time\":" + JsonStr(TimeToString(deal_time, TIME_DATE | TIME_SECONDS));
      dealsJson += ",\"profit\":" + JsonNum(profit);
      dealsJson += ",\"commission\":" + JsonNum(commission);
      dealsJson += ",\"swap\":" + JsonNum(swap);
      dealsJson += ",\"net\":" + JsonNum(total_profit);
      dealsJson += ",\"total_profit\":" + JsonNum(total_profit);
      dealsJson += ",\"status\":" + JsonStr(status);
      dealsJson += ",\"magic\":" + IntegerToString((int)magic);
      dealsJson += ",\"magic_number\":" + IntegerToString((int)magic);
      dealsJson += ",\"comment\":" + JsonStr(comment);
      dealsJson += ",\"source\":\"mt5_history\"";
      dealsJson += "}";

      if(inBatch >= batchSize)
      {
         SendHistoryBatch("full", batchIndex, dealsJson, inBatch);
         batchIndex++;
         dealsJson = "";
         inBatch = 0;
      }
   }

   if(inBatch > 0)
      SendHistoryBatch("full", batchIndex, dealsJson, inBatch);

   Print("Full history sync queued deals: ", sent, "/", total);
}

bool SendHistoryBatch(string syncType, int batchIndex, string dealsJson, int batchCount)
{
   string json = "{";
   json += "\"secret\":" + JsonStr(InpNativeSecret);
   json += ",\"schema_version\":2";
   json += ",\"bot_id\":" + JsonStr(g_activeBotId);
   json += ",\"symbol\":" + JsonStr(g_symbol);
   json += ",\"magic_number\":" + IntegerToString(g_activeMagic);
   json += ",\"account_login\":" + IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN));
   json += ",\"server\":" + JsonStr(AccountInfoString(ACCOUNT_SERVER));
   json += ",\"sync_type\":" + JsonStr(syncType);
   json += ",\"batch_index\":" + IntegerToString(batchIndex);
   json += ",\"batch_size\":" + IntegerToString(batchCount);
   json += ",\"telegram_silent\":true";
   json += ",\"deals\":[" + dealsJson + "]";
   json += "}";

   string response;
   return SendJsonPost("/api/mt5/native-history", json, response);
}

void SendIncrementalHistory()
{
   if(!InpSendIncrementalHistory)
      return;
   string key = "ORBVRSI_" + g_activeBotId + "_LAST_HISTORY_DEAL";
   double lastTicketValue = GlobalVariableCheck(key) ? GlobalVariableGet(key) : 0.0;
   ulong lastTicket = (ulong)lastTicketValue;
   datetime from = TimeCurrent() - 86400 * 30;
   datetime to = TimeCurrent();
   if(!HistorySelect(from, to))
      return;

   string dealsJson = "";
   int count = 0;
   ulong maxTicket = lastTicket;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0 || ticket <= lastTicket)
         continue;
      ENUM_DEAL_ENTRY entry_type = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if(entry_type != DEAL_ENTRY_OUT && entry_type != DEAL_ENTRY_INOUT)
         continue;
      string symbol = HistoryDealGetString(ticket, DEAL_SYMBOL);
      long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
      if(symbol != g_symbol || magic != g_activeMagic)
         continue;
      ENUM_DEAL_TYPE deal_type = (ENUM_DEAL_TYPE)HistoryDealGetInteger(ticket, DEAL_TYPE);
      if(deal_type != DEAL_TYPE_BUY && deal_type != DEAL_TYPE_SELL)
         continue;

      double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);
      double commission = HistoryDealGetDouble(ticket, DEAL_COMMISSION);
      double swap = HistoryDealGetDouble(ticket, DEAL_SWAP);
      double net = profit + commission + swap;
      double volume = HistoryDealGetDouble(ticket, DEAL_VOLUME);
      double price = HistoryDealGetDouble(ticket, DEAL_PRICE);
      datetime deal_time = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
      ulong position_id = (ulong)HistoryDealGetInteger(ticket, DEAL_POSITION_ID);
      ulong order_ticket = (ulong)HistoryDealGetInteger(ticket, DEAL_ORDER);
      string side = (deal_type == DEAL_TYPE_SELL) ? "sell" : "buy";
      if(count > 0) dealsJson += ",";
      count++;
      if(ticket > maxTicket) maxTicket = ticket;
      dealsJson += "{";
      dealsJson += "\"deal_ticket\":" + StringFormat("%I64u", ticket);
      dealsJson += ",\"ticket\":" + StringFormat("%I64u", ticket);
      dealsJson += ",\"order_ticket\":" + StringFormat("%I64u", order_ticket);
      dealsJson += ",\"position_id\":" + StringFormat("%I64u", position_id);
      dealsJson += ",\"symbol\":" + JsonStr(symbol);
      dealsJson += ",\"side\":" + JsonStr(side);
      dealsJson += ",\"entry_type\":" + JsonStr(EnumToString(entry_type));
      dealsJson += ",\"deal_type\":" + JsonStr(EnumToString(deal_type));
      dealsJson += ",\"volume\":" + JsonNum(volume);
      dealsJson += ",\"lots\":" + JsonNum(volume);
      dealsJson += ",\"price\":" + JsonNum(price);
      dealsJson += ",\"exit_price\":" + JsonNum(price);
      dealsJson += ",\"deal_time\":" + JsonStr(TimeToString(deal_time, TIME_DATE | TIME_SECONDS));
      dealsJson += ",\"close_time\":" + JsonStr(TimeToString(deal_time, TIME_DATE | TIME_SECONDS));
      dealsJson += ",\"profit\":" + JsonNum(profit);
      dealsJson += ",\"commission\":" + JsonNum(commission);
      dealsJson += ",\"swap\":" + JsonNum(swap);
      dealsJson += ",\"net\":" + JsonNum(net);
      dealsJson += ",\"total_profit\":" + JsonNum(net);
      dealsJson += ",\"status\":" + JsonStr(net >= 0.0 ? "win" : "loss");
      dealsJson += ",\"magic\":" + IntegerToString((int)magic);
      dealsJson += ",\"magic_number\":" + IntegerToString((int)magic);
      dealsJson += ",\"comment\":" + JsonStr(HistoryDealGetString(ticket, DEAL_COMMENT));
      dealsJson += ",\"source\":\"mt5_history\"";
      dealsJson += "}";
   }
   if(count > 0 && SendHistoryBatch("incremental", 0, dealsJson, count))
      GlobalVariableSet(key, (double)maxTicket);
}
void SendBacktestHistory()
{
   int total = ArraySize(g_virtualHistory);
   if(total == 0)
   {
      Print("No virtual history to send");
      return;
   }

   string json = "{\"secret\":" + JsonStr(InpNativeSecret)
              + ",\"bot_id\":" + JsonStr(g_activeBotId)
              + ",\"ea_version\":" + JsonStr(EA_VERSION)
              + ",\"ea_build\":" + JsonStr(EA_BUILD)
              + ",\"trades\":[";

   for(int i = 0; i < total; i++)
   {
      if(i > 0) json += ",";
      json += "{";
      bool tp1Hit = (g_virtualHistory[i].side == 1) ? (g_virtualHistory[i].exitPrice >= g_virtualHistory[i].tp1) : (g_virtualHistory[i].exitPrice <= g_virtualHistory[i].tp1);
      bool tp2Hit = (g_virtualHistory[i].side == 1) ? (g_virtualHistory[i].exitPrice >= g_virtualHistory[i].tp2) : (g_virtualHistory[i].exitPrice <= g_virtualHistory[i].tp2);

      json += "\"symbol\":" + JsonStr(g_symbol);
      json += ",\"side\":" + JsonStr(g_virtualHistory[i].side == 1 ? "buy" : "sell");
      json += ",\"lots\":" + JsonNum(InpVirtualLot);
      json += ",\"entry_price\":" + JsonNum(g_virtualHistory[i].entry);
      json += ",\"sl_price\":" + JsonNum(g_virtualHistory[i].sl);
      json += ",\"tp1_price\":" + JsonNum(g_virtualHistory[i].tp1);
      json += ",\"tp2_price\":" + JsonNum(g_virtualHistory[i].tp2);
      json += ",\"open_time\":" + JsonStr(TimeToString(g_virtualHistory[i].openTime, TIME_DATE | TIME_SECONDS));
      json += ",\"close_time\":" + JsonStr(TimeToString(g_virtualHistory[i].closeTime, TIME_DATE | TIME_SECONDS));
      json += ",\"exit_price\":" + JsonNum(g_virtualHistory[i].exitPrice);
      json += ",\"profit_money\":" + JsonNum(g_virtualHistory[i].profit);
      json += ",\"status\":" + JsonStr(g_virtualHistory[i].profit > 0.0 ? "win" : "loss");
      json += ",\"tp1_hit\":" + (tp1Hit ? "true" : "false");
      json += ",\"tp2_hit\":" + (tp2Hit ? "true" : "false");
      json += ",\"source\":\"backtest\"";
      json += "}";
   }

   json += "]}";

   string response;
   if(SendJsonPost("/api/mt5/native-backtest", json, response))
      Print("Backtest history sent: ", total, " trades");
   else
      Print("Backtest send failed. response=", response);
}

bool SendNativeEvent(string eventType, string message = "", double eventProfit = 0.0, int sideOverride = 0, string extraJson = "")
{
   if(!InpEnableNotifications)
      return false;

   double volume, currentProfit;
   long posType;
   bool hasPos = GetCurrentPositionData(volume, currentProfit, posType);

   int side = sideOverride != 0 ? sideOverride : g_plan.side;
   double lot = hasPos ? volume : (g_plan.original_lot > 0.0 ? g_plan.original_lot : g_activeLot);
   double profit = MathAbs(eventProfit) > 0.0000001 ? eventProfit : (hasPos ? currentProfit : g_lastKnownPositionProfit);

   double balance = InpSendAccountOnEvents ? AccountInfoDouble(ACCOUNT_BALANCE) : 0.0;
   double equity  = InpSendAccountOnEvents ? AccountInfoDouble(ACCOUNT_EQUITY) : 0.0;

   string json = "{";
   json += "\"secret\":" + JsonStr(InpNativeSecret);
   json += ",\"source\":\"mt5_native\"";
   json += ",\"bot_id\":" + JsonStr(g_activeBotId);
   json += ",\"ea_version\":" + JsonStr(EA_VERSION);
   json += ",\"ea_build\":" + JsonStr(EA_BUILD);
   json += ",\"symbol\":" + JsonStr(g_symbol);
   json += ",\"magic_number\":" + IntegerToString(g_activeMagic);
   json += ",\"event_type\":" + JsonStr(eventType);
   if(StringFind(extraJson, "\"schema_version\"") < 0)
      json += ",\"schema_version\":2";
   if(StringFind(extraJson, "\"trade_uid\"") < 0)
      json += ",\"trade_uid\":" + JsonStr(CurrentTradeUid());
   if(StringFind(extraJson, "\"event_id\"") < 0)
      json += ",\"event_id\":" + JsonStr(EventId(eventType));
   if(StringFind(extraJson, "\"position_id\"") < 0)
      json += ",\"position_id\":" + StringFormat("%I64u", g_plan.position_id);
   if(StringFind(extraJson, "\"position_ticket\"") < 0)
      json += ",\"position_ticket\":" + StringFormat("%I64u", g_plan.ticket);
   if(StringFind(extraJson, "\"telegram_silent\"") < 0)
      json += ",\"telegram_silent\":" + ((StringFind(eventType, "_silent") >= 0 || eventType == "history_sync") ? "true" : "false");
   json += ",\"side\":" + JsonStr(SideToString(side));
   json += ",\"lot\":" + JsonNum(lot);
   json += ",\"entry\":" + JsonPriceOrNull(g_plan.entry, g_plan.entry > 0.0);
   json += ",\"sl\":" + JsonPriceOrNull(g_plan.sl, g_plan.sl > 0.0);
   json += ",\"tp1\":" + JsonPriceOrNull(g_plan.tp1, InpEnableTP1);
   json += ",\"tp2\":" + JsonPriceOrNull(g_plan.tp2, InpEnableTP2);
   json += ",\"tp3\":" + JsonPriceOrNull(g_plan.tp3, g_activeEnableTP3);
   json += ",\"profit\":" + JsonNum(profit);
   json += ",\"balance\":" + JsonNum(balance);
   json += ",\"equity\":" + JsonNum(equity);
   json += ",\"time\":" + JsonStr(UtcIsoTime());
   json += ",\"message\":" + JsonStr(message);
   if(StringLen(extraJson) > 0)
      json += "," + extraJson;
   json += "}";

   string response;
   bool ok = SendJsonPost("/api/mt5/native-event", json, response);
   if(ok)
      Log("Native event sent: " + eventType);
   return ok;
}

bool SendNativeAccount()
{
   if(!InpEnableNotifications)
      return false;

   ulong ticket = 0;
   double volume = 0.0, openPrice = 0.0, sl = 0.0;
   long type = -1;
   bool hasPos = FindOurPosition(ticket, volume, openPrice, sl, type);

   string json = "{";
   json += "\"secret\":" + JsonStr(InpNativeSecret);
   json += ",\"source\":\"mt5_native\"";
   json += ",\"bot_id\":" + JsonStr(g_activeBotId);
   json += ",\"ea_version\":" + JsonStr(EA_VERSION);
   json += ",\"ea_build\":" + JsonStr(EA_BUILD);
   json += ",\"symbol\":" + JsonStr(g_symbol);
   json += ",\"magic_number\":" + IntegerToString(g_activeMagic);
   json += ",\"balance\":" + JsonNum(AccountInfoDouble(ACCOUNT_BALANCE));
   json += ",\"equity\":" + JsonNum(AccountInfoDouble(ACCOUNT_EQUITY));
   json += ",\"margin\":" + JsonNum(AccountInfoDouble(ACCOUNT_MARGIN));
   json += ",\"free_margin\":" + JsonNum(AccountInfoDouble(ACCOUNT_MARGIN_FREE));
   json += ",\"open_positions\":" + IntegerToString(hasPos ? 1 : 0);
   json += ",\"daily_pnl\":" + JsonNum(GetDailyPnL());
   json += ",\"has_position\":" + (PositionsTotal() > 0 ? "true" : "false");
   json += ",\"time\":" + JsonStr(UtcIsoTime());
   json += "}";

   string response;
   return SendJsonPost("/api/mt5/native-account", json, response);
}

//====================================================================
// SCREENSHOTS
//====================================================================
bool MakeChartScreenshot(string filename)
{
   ChartRedraw(0);
   Sleep(400);
   ResetLastError();

   bool ok = ChartScreenShot(0, filename, InpScreenshotWidth, InpScreenshotHeight, ALIGN_RIGHT);
   if(!ok)
      Log("ChartScreenShot failed. err=" + IntegerToString(GetLastError()));

   return ok;
}

bool ReadFileToBase64(string filename, string &base64)
{
   base64 = "";

   int handle = FileOpen(filename, FILE_READ | FILE_BIN);
   if(handle == INVALID_HANDLE)
   {
      Log("Cannot open screenshot file. err=" + IntegerToString(GetLastError()));
      return false;
   }

   ulong size = FileSize(handle);
   if(size == 0 || size > 7000000)
   {
      FileClose(handle);
      Log("Screenshot file size invalid: " + StringFormat("%I64u", size));
      return false;
   }

   int sizeInt = (int)size;
   uchar data[];
   ArrayResize(data, sizeInt);
   uint read = FileReadArray(handle, data, 0, sizeInt);
   FileClose(handle);

   if(read <= 0)
   {
      Log("Failed to read screenshot file");
      return false;
   }

   uchar key[];
   uchar encoded[];
   ArrayResize(key, 0);

   if(!CryptEncode(CRYPT_BASE64, data, key, encoded))
   {
      Log("Base64 encode failed. err=" + IntegerToString(GetLastError()));
      return false;
   }

   base64 = CharArrayToString(encoded, 0, -1, CP_UTF8);
   return StringLen(base64) > 0;
}

bool SendNativeScreenshot(string eventType, string caption, int sideOverride = 0)
{
   if(!InpEnableNotifications || !InpSendScreenshots)
      return false;

   if(StringLen(InpNativeSecret) <= 0)
   {
      Log("Native secret is empty. Screenshot not sent.");
      return false;
   }

   string file = "orbrsi_" + g_symbol + "_" + eventType + "_" + IntegerToString((int)TimeLocal()) + ".png";
   StringReplace(file, ":", "_");
   StringReplace(file, "/", "_");
   StringReplace(file, "\\", "_");

   if(!MakeChartScreenshot(file))
      return false;

   string b64;
   if(!ReadFileToBase64(file, b64))
      return false;

   double volume, currentProfit;
   long posType;
   bool hasPos = GetCurrentPositionData(volume, currentProfit, posType);

   int side = sideOverride != 0 ? sideOverride : g_plan.side;
   double lot = hasPos ? volume : (g_plan.original_lot > 0.0 ? g_plan.original_lot : g_activeLot);
   double profit = hasPos ? currentProfit : g_lastKnownPositionProfit;

   string json = "{";
   json += "\"secret\":" + JsonStr(InpNativeSecret);
   json += ",\"source\":\"mt5_native\"";
   json += ",\"bot_id\":" + JsonStr(g_activeBotId);
   json += ",\"ea_version\":" + JsonStr(EA_VERSION);
   json += ",\"ea_build\":" + JsonStr(EA_BUILD);
   json += ",\"symbol\":" + JsonStr(g_symbol);
   json += ",\"magic_number\":" + IntegerToString(g_activeMagic);
   json += ",\"event_type\":" + JsonStr(eventType);
   json += ",\"schema_version\":2";
   json += ",\"trade_uid\":" + JsonStr(CurrentTradeUid());
   json += ",\"event_id\":" + JsonStr(EventId(eventType));
   json += ",\"position_id\":" + StringFormat("%I64u", g_plan.position_id);
   json += ",\"position_ticket\":" + StringFormat("%I64u", g_plan.ticket);
   json += ",\"side\":" + JsonStr(SideToString(side));
   json += ",\"lot\":" + JsonNum(lot);
   json += ",\"entry\":" + JsonPriceOrNull(g_plan.entry, g_plan.entry > 0.0);
   json += ",\"sl\":" + JsonPriceOrNull(g_plan.sl, g_plan.sl > 0.0);
   json += ",\"tp1\":" + JsonPriceOrNull(g_plan.tp1, InpEnableTP1);
   json += ",\"tp2\":" + JsonPriceOrNull(g_plan.tp2, InpEnableTP2);
   json += ",\"tp3\":" + JsonPriceOrNull(g_plan.tp3, g_activeEnableTP3);
   json += ",\"profit\":" + JsonNum(profit);
   json += ",\"balance\":" + JsonNum(AccountInfoDouble(ACCOUNT_BALANCE));
   json += ",\"equity\":" + JsonNum(AccountInfoDouble(ACCOUNT_EQUITY));
   json += ",\"caption\":" + JsonStr(caption);
   json += ",\"image_base64\":" + JsonStr(b64);
   json += ",\"time\":" + JsonStr(UtcIsoTime());
   json += "}";

   string response;
   bool ok = SendJsonPost("/api/mt5/native-screenshot", json, response);
   if(ok)
      Log("Screenshot sent: " + eventType);
   return ok;
}

//====================================================================
// OBJECT HELPERS
//====================================================================
void DeleteObjects()
{
   int total = ObjectsTotal(0, 0, -1);
   string p = Prefix();

   for(int i = total - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i, 0, -1);
      if(StringFind(name, p) == 0)
         ObjectDelete(0, name);
   }
}

// Remove chart objects left by other profiles so stale ORB zones don't persist across restarts.
void ClearOldPrefixObjects()
{
   string oldPrefixes[6];
   oldPrefixes[0] = "ORBVRSI_UNIFIED_PRO_";
   oldPrefixes[1] = "ORBVRSI_NAS100_PRO_";
   oldPrefixes[2] = "ORBVRSI_SP500_PRO_";
   oldPrefixes[3] = "ORBVRSI_DJ30_PRO_";
   oldPrefixes[4] = "ORBVRSI_BTCUSD_PRO_";
   oldPrefixes[5] = "ORBVRSI_GER40_PRO_";

   string current = g_activePrefixBase;
   int deleted = 0;

   for(int p = 0; p < 6; p++)
   {
      if(oldPrefixes[p] == current)
         continue;
      int total = ObjectsTotal(0, 0, -1);
      for(int i = total - 1; i >= 0; i--)
      {
         string name = ObjectName(0, i, 0, -1);
         if(StringFind(name, oldPrefixes[p]) == 0)
         {
            ObjectDelete(0, name);
            deleted++;
         }
      }
   }

   if(deleted > 0)
      Print("ClearOldPrefixObjects: removed ", deleted, " stale chart objects from old profiles");
}

void DeleteLiveObjects()
{
   int total = ObjectsTotal(0, 0, -1);
   string p = Prefix() + "LIVE_";

   for(int i = total - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i, 0, -1);
      if(StringFind(name, p) == 0)
         ObjectDelete(0, name);
   }
}

void DrawHLine(string name, double price, color clr, int width = 1, ENUM_LINE_STYLE style = STYLE_DOT)
{
   string obj = ObjName(name);
   if(ObjectFind(0, obj) < 0)
      ObjectCreate(0, obj, OBJ_HLINE, 0, 0, price);

   ObjectSetDouble(0, obj, OBJPROP_PRICE, price);
   ObjectSetInteger(0, obj, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, obj, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, obj, OBJPROP_STYLE, style);
   ObjectSetInteger(0, obj, OBJPROP_SELECTABLE, false);
}

void DrawTrendLine(string name, datetime t1, double p1, datetime t2, double p2, color clr, int width = 1, ENUM_LINE_STYLE style = STYLE_SOLID)
{
   string obj = ObjName(name);
   if(ObjectFind(0, obj) < 0)
      ObjectCreate(0, obj, OBJ_TREND, 0, t1, p1, t2, p2);

   ObjectMove(0, obj, 0, t1, p1);
   ObjectMove(0, obj, 1, t2, p2);
   ObjectSetInteger(0, obj, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, obj, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, obj, OBJPROP_STYLE, style);
   ObjectSetInteger(0, obj, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, obj, OBJPROP_SELECTABLE, false);
}

void DrawRectangle(string name, datetime t1, double p1, datetime t2, double p2, color clr)
{
   string obj = ObjName(name);
   if(ObjectFind(0, obj) < 0)
      ObjectCreate(0, obj, OBJ_RECTANGLE, 0, t1, p1, t2, p2);

   ObjectMove(0, obj, 0, t1, p1);
   ObjectMove(0, obj, 1, t2, p2);
   ObjectSetInteger(0, obj, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, obj, OBJPROP_BACK, true);
   ObjectSetInteger(0, obj, OBJPROP_FILL, true);
   ObjectSetInteger(0, obj, OBJPROP_SELECTABLE, false);
}

void DrawText(string name, datetime t, double price, string text, color clr, int fontSize = 8)
{
   string obj = ObjName(name);
   if(ObjectFind(0, obj) < 0)
      ObjectCreate(0, obj, OBJ_TEXT, 0, t, price);

   ObjectMove(0, obj, 0, t, price);
   ObjectSetString(0, obj, OBJPROP_TEXT, text);
   ObjectSetInteger(0, obj, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, obj, OBJPROP_FONTSIZE, fontSize);
   ObjectSetString(0, obj, OBJPROP_FONT, "Consolas");
   ObjectSetInteger(0, obj, OBJPROP_SELECTABLE, false);
}

void DrawLabel(string name, int x, int y, string text, color clr, int fontSize = 8)
{
   string obj = ObjName(name);
   if(ObjectFind(0, obj) < 0)
      ObjectCreate(0, obj, OBJ_LABEL, 0, 0, 0);

   ObjectSetInteger(0, obj, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, obj, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, obj, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, obj, OBJPROP_TEXT, text);
   ObjectSetInteger(0, obj, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, obj, OBJPROP_FONTSIZE, fontSize);
   ObjectSetString(0, obj, OBJPROP_FONT, "Consolas");
   ObjectSetInteger(0, obj, OBJPROP_SELECTABLE, false);
}

//====================================================================
// PROFIT / VOLUME HELPERS
//====================================================================
double CalcProfitMoney(int side, double lot, double entry, double exitPrice)
{
   double profit = 0.0;
   ENUM_ORDER_TYPE type = side == 1 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(OrderCalcProfit(type, g_symbol, lot, entry, exitPrice, profit))
      return profit;

   if(g_tickSize <= 0.0)
      return 0.0;

   double ticks = (exitPrice - entry) / g_tickSize;
   if(side == -1)
      ticks = -ticks;

   return ticks * g_tickValue * lot;
}

//====================================================================
// POSITION HELPERS
//====================================================================
bool FindOurPosition(ulong &ticket, double &volume, double &openPrice, double &sl, long &type)
{
   ticket = 0;
   volume = 0.0;
   openPrice = 0.0;
   sl = 0.0;
   type = -1;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0)
         continue;

      string sym = PositionGetString(POSITION_SYMBOL);
      long magic = PositionGetInteger(POSITION_MAGIC);

      if(sym != g_symbol || (int)magic != g_activeMagic)
         continue;

      ticket = t;
      volume = PositionGetDouble(POSITION_VOLUME);
      openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      sl = PositionGetDouble(POSITION_SL);
      type = PositionGetInteger(POSITION_TYPE);
      return true;
   }

   return false;
}

bool HasOurPosition()
{
   ulong ticket = 0;
   double volume = 0.0, openPrice = 0.0, sl = 0.0;
   long type = -1;
   return FindOurPosition(ticket, volume, openPrice, sl, type);
}

void CloseOppositeIfNeeded(int side)
{
   if(!InpCloseOppositePositions)
      return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      string sym = PositionGetString(POSITION_SYMBOL);
      long magic = PositionGetInteger(POSITION_MAGIC);
      long type = PositionGetInteger(POSITION_TYPE);

      if(sym != g_symbol || (int)magic != g_activeMagic)
         continue;

      bool isBuy = type == POSITION_TYPE_BUY;
      bool opposite = (side == 1 && !isBuy) || (side == -1 && isBuy);

      if(opposite)
      {
         if(trade.PositionClose(ticket))
            Log("Closed opposite position: " + StringFormat("%I64u", ticket));
         else
            Log("Failed to close opposite: " + trade.ResultRetcodeDescription());
      }
   }
}

//====================================================================
// DAILY STATE / INDICATOR CALCULATION
//====================================================================
void ResetDayState(datetime t)
{
   g_orbLocked = false;
   g_orbHigh = 0.0;
   g_orbLow = 0.0;
   g_longTriggeredToday = false;
   g_shortTriggeredToday = false;
   g_longArmed = false;
   g_shortArmed = false;
   g_vwap = 0.0;
   g_vwapPV = 0.0;
   g_vwapVol = 0.0;
   g_cvd = 0.0;
   g_prevCvd = 0.0;
   g_deltaEma = 0.0;
   g_lastDeltaRaw = 0.0;
   g_lastDateKey = DateKey(t);
}

void RecalculateIntradayState()
{
   MqlRates rates[];
   ArraySetAsSeries(rates, false);

   datetime now = TimeCurrent();
   int dk = DateKey(now);
   datetime dayStart = DayStartFromDateKey(dk);

   int copied = CopyRates(g_symbol, PERIOD_CURRENT, dayStart, now, rates);
   if(copied <= 0)
      return;

   g_orbLocked = false;
   g_orbHigh = 0.0;
   g_orbLow = 0.0;
   g_vwapPV = 0.0;
   g_vwapVol = 0.0;
   g_vwap = 0.0;
   g_cvd = 0.0;
   g_prevCvd = 0.0;
   g_deltaEma = 0.0;
   g_lastDeltaRaw = 0.0;

   double alpha = 2.0 / (InpDeltaSmoothingLength + 1.0);

   for(int i = 0; i < copied; i++)
   {
      datetime bt = rates[i].time;
      double typ = (rates[i].high + rates[i].low + rates[i].close) / 3.0;
      double vol = (double)rates[i].tick_volume;
      if(vol <= 0.0)
         vol = 1.0;

      g_vwapPV += typ * vol;
      g_vwapVol += vol;
      g_vwap = g_vwapVol > 0.0 ? g_vwapPV / g_vwapVol : rates[i].close;

      double sign = rates[i].close > rates[i].open ? 1.0 : rates[i].close < rates[i].open ? -1.0 : 0.0;
      double delta = vol * sign;
      g_lastDeltaRaw = delta;
      g_prevCvd = g_cvd;
      g_cvd += delta;
      g_deltaEma = (i == 0) ? delta : alpha * delta + (1.0 - alpha) * g_deltaEma;

      if(IsTimeInNYWindow(bt, g_activeOrbStartHour, g_activeOrbStartMinute, g_activeOrbEndHour, g_activeOrbEndMinute))
      {
         if(g_orbHigh == 0.0 && g_orbLow == 0.0)
         {
            g_orbHigh = rates[i].high;
            g_orbLow = rates[i].low;
         }
         else
         {
            g_orbHigh = MathMax(g_orbHigh, rates[i].high);
            g_orbLow = MathMin(g_orbLow, rates[i].low);
         }
      }
   }

   if(g_orbHigh > 0.0 && g_orbLow > 0.0 && HasPassedNYTime(now, g_activeOrbEndHour, g_activeOrbEndMinute))
      g_orbLocked = true;
}
void RecalculateIntradayStateToClosedBar(datetime closedTime)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, false);

   int dk = DateKey(closedTime);
   datetime dayStart = DayStartFromDateKey(dk);
   datetime stopTime = closedTime + PeriodSeconds(PERIOD_CURRENT) - 1;

   int copied = CopyRates(g_symbol, PERIOD_CURRENT, dayStart, stopTime, rates);
   if(copied <= 0)
      return;

   g_orbLocked = false;
   g_orbHigh = 0.0;
   g_orbLow = 0.0;
   g_vwapPV = 0.0;
   g_vwapVol = 0.0;
   g_vwap = 0.0;
   g_cvd = 0.0;
   g_prevCvd = 0.0;
   g_deltaEma = 0.0;
   g_lastDeltaRaw = 0.0;

   double alpha = 2.0 / (InpDeltaSmoothingLength + 1.0);
   int used = 0;

   for(int i = 0; i < copied; i++)
   {
      if(rates[i].time > closedTime)
         continue;

      datetime bt = rates[i].time;
      double typ = (rates[i].high + rates[i].low + rates[i].close) / 3.0;
      double vol = (double)rates[i].tick_volume;
      if(vol <= 0.0)
         vol = 1.0;

      g_vwapPV += typ * vol;
      g_vwapVol += vol;
      g_vwap = g_vwapVol > 0.0 ? g_vwapPV / g_vwapVol : rates[i].close;

      double sign = rates[i].close > rates[i].open ? 1.0 : rates[i].close < rates[i].open ? -1.0 : 0.0;
      double delta = vol * sign;
      g_lastDeltaRaw = delta;
      g_prevCvd = g_cvd;
      g_cvd += delta;
      g_deltaEma = (used == 0) ? delta : alpha * delta + (1.0 - alpha) * g_deltaEma;
      used++;

      if(IsTimeInNYWindow(bt, g_activeOrbStartHour, g_activeOrbStartMinute, g_activeOrbEndHour, g_activeOrbEndMinute))
      {
         if(g_orbHigh == 0.0 && g_orbLow == 0.0)
         {
            g_orbHigh = rates[i].high;
            g_orbLow = rates[i].low;
         }
         else
         {
            g_orbHigh = MathMax(g_orbHigh, rates[i].high);
            g_orbLow = MathMin(g_orbLow, rates[i].low);
         }
      }
   }

   if(g_orbHigh > 0.0 && g_orbLow > 0.0 && HasPassedNYTime(closedTime, g_activeOrbEndHour, g_activeOrbEndMinute))
      g_orbLocked = true;
}
bool IsSessionActive(datetime t)
{
   if(!InpUseSessionFilter)
      return true;
   return IsTimeInNYWindow(t, InpSessionStartHourNY, InpSessionStartMinuteNY, InpSessionEndHourNY, InpSessionEndMinuteNY);
}
bool IsWeekendBlocked(datetime t)
{
   if(!g_activeBlockWeekend)
      return false;
   MqlDateTime dt;
   TimeToStruct(t, dt);
   return (dt.day_of_week == 0 || dt.day_of_week == 6);
}


//====================================================================
// STOP / TP CALCULATIONS
//====================================================================
double StopOffsetDistance()
{
   return DistanceByType(InpStopOffsetValue, InpSLOffsetType);
}

double ComputeStop(int side, double entry, MqlRates &bar)
{
   double atr = g_signalEngineHistoryMode ? g_historyAtrStop : GetATR(g_atrStopHandle, 1);
   double orbRange = MathAbs(g_orbHigh - g_orbLow);
   double offset = StopOffsetDistance();
   double sl = 0.0;

   if(side == 1)
   {
      if(InpStopMode == STOP_OPPOSITE_ORB)      sl = g_orbLow;
      else if(InpStopMode == STOP_SIGNAL)       sl = bar.low;
      else if(InpStopMode == STOP_ATR)          sl = entry - atr * InpAtrStopMult;
      else if(InpStopMode == STOP_ORB_RANGE)    sl = entry - orbRange * InpOrbRangeStopMult;
      else                                      sl = g_vwap;
      sl -= offset;
   }
   else
   {
      if(InpStopMode == STOP_OPPOSITE_ORB)      sl = g_orbHigh;
      else if(InpStopMode == STOP_SIGNAL)       sl = bar.high;
      else if(InpStopMode == STOP_ATR)          sl = entry + atr * InpAtrStopMult;
      else if(InpStopMode == STOP_ORB_RANGE)    sl = entry + orbRange * InpOrbRangeStopMult;
      else                                      sl = g_vwap;
      sl += offset;
   }

   return NormalizePrice(sl);
}

double TpDistance(double risk, int idx)
{
   double atr = g_signalEngineHistoryMode ? g_historyAtrTp : GetATR(g_atrTpHandle, 1);
   double orbRange = MathAbs(g_orbHigh - g_orbLow);

   double rMult = idx == 1 ? InpTP1R : idx == 2 ? InpTP2R : InpTP3R;
   double atrMult = idx == 1 ? InpTP1ATR : idx == 2 ? InpTP2ATR : InpTP3ATR;
   double orbMult = idx == 1 ? InpTP1ORB : idx == 2 ? InpTP2ORB : InpTP3ORB;
   double fixedVal = idx == 1 ? g_activeTP1Fixed : idx == 2 ? g_activeTP2Fixed : g_activeTP3Fixed;

   if(InpTPMode == TP_R_MULTIPLE) return MathAbs(risk) * rMult;
   if(InpTPMode == TP_ATR)        return MathAbs(atr) * atrMult;
   if(InpTPMode == TP_ORB_RANGE)  return MathAbs(orbRange) * orbMult;

   return DistanceByType(fixedVal, InpFixedTPType);
}

void ComputeTPs(int side, double entry, double sl, double &tp1, double &tp2, double &tp3)
{
   double risk = MathAbs(entry - sl);

   if(side == 1)
   {
      tp1 = NormalizePrice(entry + TpDistance(risk, 1));
      tp2 = NormalizePrice(entry + TpDistance(risk, 2));
      tp3 = NormalizePrice(entry + TpDistance(risk, 3));
   }
   else
   {
      tp1 = NormalizePrice(entry - TpDistance(risk, 1));
      tp2 = NormalizePrice(entry - TpDistance(risk, 2));
      tp3 = NormalizePrice(entry - TpDistance(risk, 3));
   }
}

bool ValidateTPsAgainstMarket(int side, double market, double tp1, double tp2, double tp3)
{
   if(side == 1)
   {
      if(InpEnableTP1 && tp1 <= market) return false;
      if(InpEnableTP2 && tp2 <= market) return false;
      if(g_activeEnableTP3 && tp3 <= market) return false;
   }
   else
   {
      if(InpEnableTP1 && tp1 >= market) return false;
      if(InpEnableTP2 && tp2 >= market) return false;
      if(g_activeEnableTP3 && tp3 >= market) return false;
   }
   return true;
}

bool CheckStopsDistance(int side, double price, double sl)
{
   long stops = SymbolInfoInteger(g_symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minDist = stops * g_point;
   if(minDist <= 0.0)
      return true;

   if(side == 1 && (price - sl) < minDist) return false;
   if(side == -1 && (sl - price) < minDist) return false;
   return true;
}

//====================================================================
// ENTRY LOGIC / UNIFIED SIGNAL ENGINE
//====================================================================
void InitSignalDecision(SignalDecision &d)
{
   d.signal = false;
   d.side = 0;
   d.signal_reason = "";
   d.block_reason = "waiting";
   d.orb_locked = false;
   d.session_ok = false;
   d.remote_ok = true;
   d.position_ok = true;
   d.long_break = false;
   d.short_break = false;
   d.long_armed = false;
   d.short_armed = false;
   d.long_retest = false;
   d.short_retest = false;
   d.vwap_long_ok = false;
   d.vwap_short_ok = false;
   d.bias_long_ok = false;
   d.bias_short_ok = false;
   d.entry_long_ok = false;
   d.entry_short_ok = false;
   d.orderflow_long_ok = false;
   d.orderflow_short_ok = false;
   d.long_filter = false;
   d.short_filter = false;
   d.long_raw_signal = false;
   d.short_raw_signal = false;
   d.long_signal = false;
   d.short_signal = false;
   d.valid_long_risk = false;
   d.valid_short_risk = false;
   d.breakout_ok = false;
   d.retest_ok = false;
   d.vwap_ok = false;
   d.bias_rsi_ok = false;
   d.entry_rsi_ok = false;
   d.orderflow_ok = false;
   d.direction_ok = false;
   d.one_signal_ok = false;
   d.one_position_ok = true;
   d.trade_permission_ok = true;
   d.entry_ref = 0.0;
   d.sl_ref = 0.0;
   d.tp1_ref = 0.0;
   d.tp2_ref = 0.0;
   d.tp3_ref = 0.0;
   d.signal_time = 0;
}

void SetSignalPrices(SignalDecision &d, MqlRates &closed)
{
   if(d.side == 0)
      return;

   d.entry_ref = NormalizePrice(closed.close);
   d.sl_ref = ComputeStop(d.side, d.entry_ref, closed);
   ComputeTPs(d.side, d.entry_ref, d.sl_ref, d.tp1_ref, d.tp2_ref, d.tp3_ref);
}

bool IsESorNASSymbol()
{
   string sym = g_symbol;
   StringToUpper(sym);
   if(StringFind(sym, "NAS") >= 0 || StringFind(sym, "NQ") >= 0 || StringFind(sym, "NASDAQ") >= 0)
      return true;
   if(StringFind(sym, "SP500") >= 0 || StringFind(sym, "US500") >= 0 || StringFind(sym, "SPX") >= 0 || StringFind(sym, "ES") >= 0)
      return true;
   return false;
}

string SignalBlockFromDecision(SignalDecision &d)
{
   if(d.long_break || d.long_retest || d.long_armed)
   {
      if(!g_diagDirectionLongOk) return "long filter false: direction disabled";
      if(!g_diagOneLongOk) return "long filter false: one signal per day";
      if(InpRequireRetest && !d.long_retest) return "retest required";
      if(!d.valid_long_risk) return "invalid risk";
      if(!d.orderflow_long_ok) return "orderflow false";
      if(!d.vwap_long_ok) return "long filter false: VWAP";
      if(!d.bias_long_ok) return "long filter false: RSI bias";
      if(!d.entry_long_ok) return "RSI filter false";
      if(!d.long_filter) return "long filter false";
   }

   if(d.short_break || d.short_retest || d.short_armed)
   {
      if(!g_diagDirectionShortOk) return "short filter false: direction disabled";
      if(!g_diagOneShortOk) return "short filter false: one signal per day";
      if(InpRequireRetest && !d.short_retest) return "retest required";
      if(!d.valid_short_risk) return "invalid risk";
      if(!d.orderflow_short_ok) return "orderflow false";
      if(!d.vwap_short_ok) return "short filter false: VWAP";
      if(!d.bias_short_ok) return "short filter false: RSI bias";
      if(!d.entry_short_ok) return "RSI filter false";
      if(!d.short_filter) return "short filter false";
   }

   return "waiting";
}

void UpdateLiveDiagnostics(SignalDecision &d, double biasRsi, double entryRsi, bool isESorNAS)
{
   g_diagLongBreak = d.long_break;
   g_diagLongRetest = d.long_retest;
   g_diagLongFilter = d.long_filter;
   g_diagLongSignal = d.long_signal;
   g_diagShortBreak = d.short_break;
   g_diagShortRetest = d.short_retest;
   g_diagShortFilter = d.short_filter;
   g_diagShortSignal = d.short_signal;
   g_diagVwapLongOk = d.vwap_long_ok;
   g_diagVwapShortOk = d.vwap_short_ok;
   g_diagBiasLongOk = d.bias_long_ok;
   g_diagBiasShortOk = d.bias_short_ok;
   g_diagEntryLongOk = d.entry_long_ok;
   g_diagEntryShortOk = d.entry_short_ok;
   g_diagOfLongOk = d.orderflow_long_ok;
   g_diagOfShortOk = d.orderflow_short_ok;
   g_diagSessionOk = d.session_ok;
   g_diagPositionOk = d.position_ok;
   g_diagRiskLongOk = d.valid_long_risk;
   g_diagRiskShortOk = d.valid_short_risk;
   g_diagBiasRsiValue = biasRsi;
   g_diagEntryRsiValue = entryRsi;
   g_diagIsESorNAS = isESorNAS;
}

SignalDecision EvaluatePineLikeSignal(MqlRates &closed, MqlRates &prev, bool liveMode)
{
   SignalDecision d;
   InitSignalDecision(d);
   d.signal_time = closed.time;

   d.orb_locked = g_orbLocked;
   if(!d.orb_locked)
   {
      d.block_reason = "ORB not locked";
      return d;
   }

   d.session_ok = IsSessionActive(closed.time);
   if(!d.session_ok)
   {
      d.block_reason = "session inactive";
      return d;
   }

   if(IsWeekendBlocked(closed.time))
   {
      d.block_reason = "weekend blocked";
      return d;
   }

   if(liveMode)
   {
      RefreshRemoteConfig(false);
      d.remote_ok = (!InpEnableRemoteControl || g_remoteTradingEnabled);
      if(!d.remote_ok)
      {
         d.block_reason = g_remoteBlockReason;
         return d;
      }

      d.position_ok = (!InpOnePositionOnly || !HasOurPosition());
      d.one_position_ok = d.position_ok;
      if(!d.position_ok)
      {
         d.block_reason = "existing position";
         return d;
      }

      string permissionReason = "";
      d.trade_permission_ok = IsTradingAllowedNow(permissionReason);
      if(!d.trade_permission_ok)
      {
         d.block_reason = permissionReason;
         return d;
      }
   }

   double biasRsi = g_signalEngineHistoryMode ? g_historyBiasRsi : GetRSI(g_biasRsiHandle, 1);
   double entryRsi = g_signalEngineHistoryMode ? g_historyEntryRsi : GetRSI(g_entryRsiHandle, 1);
   bool bullBias = biasRsi > g_activeBiasBullLevel;
   bool bearBias = biasRsi < g_activeBiasBearLevel;
   bool entryLong = entryRsi > g_activeEntryBullLevel;
   bool entryShort = entryRsi < g_activeEntryBearLevel;
   bool aboveVWAP = closed.close > g_vwap;
   bool belowVWAP = closed.close < g_vwap;
   bool isESorNAS = IsESorNASSymbol();
   bool orderFlowActive = false;

   d.vwap_long_ok = !g_activeUseVWAP || aboveVWAP;
   d.vwap_short_ok = !g_activeUseVWAP || belowVWAP;
   d.bias_long_ok = !InpUseBiasRSIFilter || bullBias;
   d.bias_short_ok = !InpUseBiasRSIFilter || bearBias;
   d.entry_long_ok = !InpUseEntryRSIFilter || entryLong;
   d.entry_short_ok = !InpUseEntryRSIFilter || entryShort;
   d.orderflow_long_ok = true;
   d.orderflow_short_ok = true;
   d.long_filter = d.bias_long_ok && d.vwap_long_ok;
   d.short_filter = d.bias_short_ok && d.vwap_short_ok;
   d.long_break = prev.close <= g_orbHigh && closed.close > g_orbHigh;
   d.short_break = prev.close >= g_orbLow && closed.close < g_orbLow;

   if(d.long_break && d.long_filter)
   {
      g_longArmed = true;
      g_shortArmed = false;
   }
   if(d.short_break && d.short_filter)
   {
      g_shortArmed = true;
      g_longArmed = false;
   }

   d.long_armed = g_longArmed;
   d.short_armed = g_shortArmed;
   d.long_retest = g_longArmed && closed.low <= g_orbHigh && closed.close > g_orbHigh;
   d.short_retest = g_shortArmed && closed.high >= g_orbLow && closed.close < g_orbLow;

   double longEntry = NormalizePrice(closed.close);
   double shortEntry = NormalizePrice(closed.close);
   double longSl = ComputeStop(1, longEntry, closed);
   double shortSl = ComputeStop(-1, shortEntry, closed);
   d.valid_long_risk = (longEntry - longSl) > g_tickSize;
   d.valid_short_risk = (shortSl - shortEntry) > g_tickSize;

   bool longSignalNoRetest = d.long_break && d.long_filter && d.entry_long_ok;
   bool shortSignalNoRetest = d.short_break && d.short_filter && d.entry_short_ok;
   bool longSignalRetest = d.long_retest && d.long_filter && d.entry_long_ok;
   bool shortSignalRetest = d.short_retest && d.short_filter && d.entry_short_ok;
   d.long_raw_signal = InpRequireRetest ? longSignalRetest : longSignalNoRetest;
   d.short_raw_signal = InpRequireRetest ? shortSignalRetest : shortSignalNoRetest;

   bool allowLong = (InpDirection == DIR_BOTH || InpDirection == DIR_LONG_ONLY);
   bool allowShort = (InpDirection == DIR_BOTH || InpDirection == DIR_SHORT_ONLY);
   bool oneLongOk = !InpOneSignalPerDirectionDay || !g_longTriggeredToday;
   bool oneShortOk = !InpOneSignalPerDirectionDay || !g_shortTriggeredToday;
   g_diagDirectionLongOk = allowLong;
   g_diagDirectionShortOk = allowShort;
   g_diagOneLongOk = oneLongOk;
   g_diagOneShortOk = oneShortOk;
   d.long_signal = allowLong && d.session_ok && d.long_raw_signal && oneLongOk && d.valid_long_risk;
   d.short_signal = allowShort && d.session_ok && d.short_raw_signal && oneShortOk && d.valid_short_risk;

   if(liveMode)
      UpdateLiveDiagnostics(d, biasRsi, entryRsi, isESorNAS);

   if(d.long_signal)
   {
      d.signal = true;
      d.side = 1;
      d.signal_reason = "BUY pine strategy clone";
      d.block_reason = "signal ready";
      d.breakout_ok = d.long_break;
      d.retest_ok = d.long_retest;
      d.vwap_ok = d.vwap_long_ok;
      d.bias_rsi_ok = d.bias_long_ok;
      d.entry_rsi_ok = d.entry_long_ok;
      d.orderflow_ok = d.orderflow_long_ok;
      d.direction_ok = allowLong;
      d.one_signal_ok = oneLongOk;
      SetSignalPrices(d, closed);
      return d;
   }

   if(d.short_signal)
   {
      d.signal = true;
      d.side = -1;
      d.signal_reason = "SELL pine strategy clone";
      d.block_reason = "signal ready";
      d.breakout_ok = d.short_break;
      d.retest_ok = d.short_retest;
      d.vwap_ok = d.vwap_short_ok;
      d.bias_rsi_ok = d.bias_short_ok;
      d.entry_rsi_ok = d.entry_short_ok;
      d.orderflow_ok = d.orderflow_short_ok;
      d.direction_ok = allowShort;
      d.one_signal_ok = oneShortOk;
      SetSignalPrices(d, closed);
      return d;
   }

   d.block_reason = SignalBlockFromDecision(d);
   return d;
}

SignalDecision EvaluateSignalEngine(MqlRates &closed, MqlRates &prev, bool liveMode)
{
   return EvaluatePineLikeSignal(closed, prev, liveMode);
}

void LogSignalTrace(SignalDecision &d, MqlRates &closed)
{
   if(!InpDebugForcePrintSignalTrace)
      return;

   Log("SIGNAL TRACE"
       + " bot_id=" + g_activeBotId
       + " symbol=" + g_symbol
       + " bar_time=" + TimeToString(closed.time, TIME_DATE|TIME_MINUTES)
       + " close=" + PriceStr(closed.close)
       + " orbLocked=" + TF(d.orb_locked)
       + " orbHigh=" + PriceStr(g_orbHigh)
       + " orbLow=" + PriceStr(g_orbLow)
       + " vwap=" + PriceStr(g_vwap)
       + " biasRsi=" + DoubleToString(g_diagBiasRsiValue, 2)
       + " entryRsi=" + DoubleToString(g_diagEntryRsiValue, 2)
       + " deltaRaw=" + DoubleToString(g_lastDeltaRaw, 0)
       + " deltaSmooth=" + DoubleToString(g_deltaEma, 2)
       + " cvd=" + DoubleToString(g_cvd, 0)
       + " cvdPrev=" + DoubleToString(g_prevCvd, 0)
       + " isESorNAS=" + TF(g_diagIsESorNAS)
       + " longOrderFlowOK=" + TF(d.orderflow_long_ok)
       + " shortOrderFlowOK=" + TF(d.orderflow_short_ok)
       + " longFilter=" + TF(d.long_filter)
       + " shortFilter=" + TF(d.short_filter)
       + " longBreak=" + TF(d.long_break)
       + " shortBreak=" + TF(d.short_break)
       + " longArmed=" + TF(d.long_armed)
       + " shortArmed=" + TF(d.short_armed)
       + " longRetest=" + TF(d.long_retest)
       + " shortRetest=" + TF(d.short_retest)
       + " longSignalRaw=" + TF(d.long_raw_signal)
       + " shortSignalRaw=" + TF(d.short_raw_signal)
       + " longSignal=" + TF(d.long_signal)
       + " shortSignal=" + TF(d.short_signal)
       + " validLongRisk=" + TF(d.valid_long_risk)
       + " validShortRisk=" + TF(d.valid_short_risk)
       + " requestDir=" + SideToString(d.side)
       + " block=" + d.block_reason);
}
string BuildOpenFailedMessage(int side, double lot, double entry, double sl, double tp1, double tp2, double tp3, string reason, int retcode = 0, int lastError = 0)
{
   string tradeReason = "";
   bool tradeOk = IsTradingAllowedNow(tradeReason);
   bool algoOn = (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0 && MQLInfoInteger(MQL_TRADE_ALLOWED) != 0);

   string msg = "Open error\n";
   msg += "Bot: " + g_activeBotId + "\n";
   msg += "Symbol: " + g_symbol + "\n";
   msg += "Side: " + SideToString(side) + "\n";
   msg += "Lot: " + DoubleToString(lot, 2) + "\n";
   msg += "Entry: " + PriceStr(entry) + "\n";
   msg += "SL: " + PriceStr(sl) + "\n";
   msg += "TP1: " + PriceStr(tp1) + "\n";
   msg += "TP2: " + PriceStr(tp2) + "\n";
   msg += "TP3: " + PriceStr(tp3) + "\n";
   msg += "Reason: " + reason + "\n";
   msg += "Retcode: " + IntegerToString(retcode) + "\n";
   msg += "LastError: " + IntegerToString(lastError) + "\n";
   msg += "Remote: " + (g_remoteTradingEnabled ? "ON" : "OFF") + "\n";
   msg += "Cfg: " + (g_remoteConfigOk ? "OK" : "FAIL") + "\n";
   msg += "Algo: " + (algoOn ? "ON" : "OFF") + "\n";
   msg += "Trade: " + (tradeOk ? "OK" : "BLOCKED");
   if(!tradeOk)
      msg += " (" + tradeReason + ")";
   return msg;
}

void NotifyDetailedOpenFailed(int side, double lot, double entry, double sl, double tp1, double tp2, double tp3, string reason, int retcode = 0, int lastError = 0)
{
   g_lastOpenError = reason;
   NotifyOpenFailed(BuildOpenFailedMessage(side, lot, entry, sl, tp1, tp2, tp3, reason, retcode, lastError), side);
}


ENUM_ORDER_TYPE_FILLING GetSymbolFillingMode(string symbol)
{
   int filling = (int)SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);

   if((filling & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK)
      return ORDER_FILLING_FOK;

   if((filling & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC)
      return ORDER_FILLING_IOC;

   return ORDER_FILLING_RETURN;
}

string FillingModeToString(ENUM_ORDER_TYPE_FILLING mode)
{
   if(mode == ORDER_FILLING_FOK)
      return "FOK";
   if(mode == ORDER_FILLING_IOC)
      return "IOC";
   if(mode == ORDER_FILLING_RETURN)
      return "RETURN";
   return IntegerToString((int)mode);
}
bool CanOpenRealTrade(int side, double lot, double entry, double sl, string &reason)
{
   reason = "";
   if(!IsTradingAllowedNow(reason))
      return false;

   double ask = SymbolInfoDouble(g_symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(g_symbol, SYMBOL_BID);
   if(ask <= 0.0 || bid <= 0.0 || ask < bid)
   {
      reason = "invalid spread/bid/ask";
      return false;
   }

   double minVol = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);
   double maxVol = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0)
      step = minVol;

   if(lot < minVol)
   {
      reason = "volume below SYMBOL_VOLUME_MIN";
      return false;
   }

   if(lot > maxVol)
   {
      reason = "volume above SYMBOL_VOLUME_MAX";
      return false;
   }

   double steps = lot / step;
   if(MathAbs(steps - MathRound(steps)) > 0.000001)
   {
      reason = "volume does not match SYMBOL_VOLUME_STEP";
      return false;
   }

   if(side == 1 && sl >= entry)
   {
      reason = "invalid BUY SL";
      return false;
   }

   if(side == -1 && sl <= entry)
   {
      reason = "invalid SELL SL";
      return false;
   }

   if(!CheckStopsDistance(side, entry, sl))
   {
      reason = "SL too close for broker stops level";
      return false;
   }

   long freeze = SymbolInfoInteger(g_symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   double freezeDist = freeze * g_point;
   if(freezeDist > 0.0 && MathAbs(entry - sl) < freezeDist)
   {
      reason = "SL inside broker freeze level";
      return false;
   }

   double margin = 0.0;
   ENUM_ORDER_TYPE type = side == 1 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(!OrderCalcMargin(type, g_symbol, lot, entry, margin))
   {
      reason = "OrderCalcMargin failed: " + IntegerToString(GetLastError());
      return false;
   }

   if(margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE))
   {
      reason = "not enough free margin";
      return false;
   }

   return true;
}

bool RunOrderCheck(int side, double lot, double entry, double sl, string &reason, uint &retcode)
{
   reason = "";
   retcode = 0;

   MqlTradeRequest request;
   MqlTradeCheckResult check;
   ZeroMemory(request);
   ZeroMemory(check);

   request.action = TRADE_ACTION_DEAL;
   request.symbol = g_symbol;
   request.magic = g_activeMagic;
   request.type = side == 1 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   request.volume = lot;
   request.price = side == 1 ? SymbolInfoDouble(g_symbol, SYMBOL_ASK) : SymbolInfoDouble(g_symbol, SYMBOL_BID);
   request.sl = sl;
   request.tp = 0.0;
   request.deviation = InpDeviationPoints;
   request.type_filling = GetSymbolFillingMode(g_symbol);
   request.comment = "ORBVRSI_NATIVE_CHECK";

   ResetLastError();
   bool callOk = OrderCheck(request, check);
   int lastError = GetLastError();
   retcode = check.retcode;

   if(!callOk)
   {
      reason = "OrderCheck CALL FAILED retcode=" + IntegerToString((int)check.retcode)
             + " comment=" + check.comment
             + " last_error=" + IntegerToString(lastError)
             + " filling=" + FillingModeToString(request.type_filling);
      return false;
   }

   string comment = check.comment;
   bool commentDone = (StringFind(comment, "Done") >= 0 || StringFind(comment, "done") >= 0 || StringFind(comment, "DONE") >= 0);
   bool retcodeOk = (check.retcode == 0 || check.retcode == TRADE_RETCODE_DONE || check.retcode == TRADE_RETCODE_PLACED);

   if(retcodeOk || (lastError == 0 && commentDone))
   {
      Log("OrderCheck OK: " + comment
          + " filling=" + FillingModeToString(request.type_filling)
          + " retcode=" + IntegerToString((int)check.retcode)
          + " last_error=" + IntegerToString(lastError));
      return true;
   }

   reason = "OrderCheck FAILED retcode=" + IntegerToString((int)check.retcode)
          + " comment=" + comment
          + " last_error=" + IntegerToString(lastError)
          + " filling=" + FillingModeToString(request.type_filling);
   return false;
}

bool IsTemporaryOpenFailure(string reason)
{
   return StringFind(reason, "terminal algo trading disabled") >= 0
       || StringFind(reason, "EA algo trading disabled") >= 0
       || StringFind(reason, "remote config") >= 0
       || StringFind(reason, "WebRequest") >= 0
       || StringFind(reason, "trade permission") >= 0
       || StringFind(reason, "order sent but position not found") >= 0
       || StringFind(reason, "invalid bid/ask") >= 0;
}

void StorePendingSignal(SignalDecision &d)
{
   if(!InpRetryMissedLiveSignal || !IsTemporaryOpenFailure(g_lastOpenError))
      return;

   g_pendingSignalActive = true;
   g_pendingSignalSide = d.side;
   g_pendingSignalTime = TimeCurrent();
   g_pendingSignalBarTime = d.signal_time;
   g_pendingSignalEntryRef = d.entry_ref;
   g_pendingSignalSLRef = d.sl_ref;
   g_pendingSignalReason = g_lastOpenError;
}

void ProcessPendingSignal()
{
   if(!g_pendingSignalActive || !InpRetryMissedLiveSignal)
      return;

   datetime now = TimeCurrent();
   if(now - g_pendingSignalTime > InpRetrySignalSeconds)
   {
      g_pendingSignalActive = false;
      SetBlockReason("pending signal expired");
      return;
   }

   if(InpRetryOnlySameBar)
   {
      datetime currentBar = iTime(g_symbol, PERIOD_CURRENT, 0);
      int sec = PeriodSeconds(PERIOD_CURRENT);
      if(currentBar > g_pendingSignalBarTime + sec)
      {
         g_pendingSignalActive = false;
         SetBlockReason("pending signal expired");
         return;
      }
   }

   if(HasOurPosition())
   {
      g_pendingSignalActive = false;
      return;
   }

   MqlRates r[];
   ArraySetAsSeries(r, true);
   if(CopyRates(g_symbol, PERIOD_CURRENT, 0, 3, r) < 3)
      return;

   double lot = NormalizeVolume(g_activeLot);
   double entry = g_pendingSignalSide == 1 ? SymbolInfoDouble(g_symbol, SYMBOL_ASK) : SymbolInfoDouble(g_symbol, SYMBOL_BID);
   double sl = ComputeStop(g_pendingSignalSide, entry, r[1]);
   string reason = "";
   if(!CanOpenRealTrade(g_pendingSignalSide, lot, entry, sl, reason))
   {
      SetBlockReason(reason);
      return;
   }

   if(OpenTrade(g_pendingSignalSide, r[1]))
      g_pendingSignalActive = false;
}

void EvaluateEntries()
{
   SetBlockReason("waiting");

   MqlRates r[];
   ArraySetAsSeries(r, true);
   if(CopyRates(g_symbol, PERIOD_CURRENT, 0, 4, r) < 4)
   {
      SetBlockReason("not enough bars");
      return;
   }

   MqlRates closed = r[1];
   MqlRates prev = r[2];
   RecalculateIntradayStateToClosedBar(closed.time);
   SignalDecision d = EvaluatePineLikeSignal(closed, prev, true);
   LogSignalTrace(d, closed);

   if(!d.signal)
   {
      SetBlockReason(d.block_reason);
      return;
   }

   g_lastSignalSide = d.side;
   g_lastSignalTime = d.signal_time;
   g_lastSignalReason = d.signal_reason;
   SetBlockReason(d.signal_reason);

   Log("LIVE SIGNAL CONFIRMED"
       + " bot_id=" + g_activeBotId
       + " symbol=" + g_symbol
       + " side=" + SideToString(d.side)
       + " entry_ref=" + PriceStr(d.entry_ref)
       + " sl_ref=" + PriceStr(d.sl_ref)
       + " tp1=" + PriceStr(d.tp1_ref)
       + " tp2=" + PriceStr(d.tp2_ref)
       + " tp3=" + PriceStr(d.tp3_ref));

   if(InpDebugDoNotOpenTrades)
   {
      string msg = "DEBUG: trade would be opened";
      g_lastOpenError = msg;
      Log(msg + " bot_id=" + g_activeBotId + " symbol=" + g_symbol + " side=" + SideToString(d.side));
      SendNativeEvent("open_failed", msg, 0.0, d.side);
      SendNativeAccount();
      return;
   }

   if(InpExecutionDryCheckOnly)
   {
      string reason = "";
      double lot = NormalizeVolume(g_activeLot);
      double marketEntry = d.side == 1 ? SymbolInfoDouble(g_symbol, SYMBOL_ASK) : SymbolInfoDouble(g_symbol, SYMBOL_BID);
      double marketSl = ComputeStop(d.side, marketEntry, closed);
      uint checkRetcode = 0;
      bool canOpen = CanOpenRealTrade(d.side, lot, marketEntry, marketSl, reason);
      if(canOpen)
         canOpen = RunOrderCheck(d.side, lot, marketEntry, marketSl, reason, checkRetcode);

      if(canOpen)
      {
         string msg = "DRY CHECK: signal ok, trade would be opened";
         Log(msg + " bot_id=" + g_activeBotId + " symbol=" + g_symbol + " side=" + SideToString(d.side));
         SendNativeEvent("open_failed", msg, 0.0, d.side);
      }
      else
      {
         SetBlockReason(reason);
         g_lastOpenError = reason;
         NotifyDetailedOpenFailed(d.side, lot, marketEntry, marketSl, d.tp1_ref, d.tp2_ref, d.tp3_ref, reason, (int)checkRetcode, GetLastError());
      }
      return;
   }

   bool opened = OpenTrade(d.side, closed);
   if(!opened)
      StorePendingSignal(d);
}

//====================================================================
// TRADE EXECUTION
//====================================================================//====================================================================
bool OpenTrade(int side, MqlRates &bar)
{
   g_lastOpenError = "";

   double lot = NormalizeVolume(g_activeLot);
   double ask = SymbolInfoDouble(g_symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(g_symbol, SYMBOL_BID);
   double entry = side == 1 ? ask : bid;
   double sl = ComputeStop(side, entry, bar);
   double tp1, tp2, tp3;
   ComputeTPs(side, entry, sl, tp1, tp2, tp3);

   string precheckReason = "";
   if(!CanOpenRealTrade(side, lot, entry, sl, precheckReason))
   {
      SetBlockReason(precheckReason);
      g_lastOpenError = precheckReason;
      Log("Open blocked: " + precheckReason + " bot_id=" + g_activeBotId);
      NotifyDetailedOpenFailed(side, lot, entry, sl, tp1, tp2, tp3, precheckReason, 0, GetLastError());
      return false;
   }

   if(!ValidateTPsAgainstMarket(side, entry, tp1, tp2, tp3))
   {
      string reason = "TP invalid against current market";
      SetBlockReason(reason);
      g_lastOpenError = reason;
      Log(reason + " entry=" + PriceStr(entry) + " tp1=" + PriceStr(tp1) + " tp2=" + PriceStr(tp2) + " tp3=" + PriceStr(tp3));
      NotifyDetailedOpenFailed(side, lot, entry, sl, tp1, tp2, tp3, reason, 0, GetLastError());
      return false;
   }

   uint checkRetcode = 0;
   string checkReason = "";
   if(!RunOrderCheck(side, lot, entry, sl, checkReason, checkRetcode))
   {
      SetBlockReason(checkReason);
      g_lastOpenError = checkReason;
      Log("ORDER CHECK BLOCKED bot_id=" + g_activeBotId
          + " symbol=" + g_symbol
          + " side=" + SideToString(side)
          + " lot=" + DoubleToString(lot, 2)
          + " entry=" + PriceStr(entry)
          + " sl=" + PriceStr(sl)
          + " tp1=" + PriceStr(tp1)
          + " tp2=" + PriceStr(tp2)
          + " tp3=" + PriceStr(tp3)
          + " filling_raw=" + IntegerToString((int)SymbolInfoInteger(g_symbol, SYMBOL_FILLING_MODE))
          + " selected_filling=" + FillingModeToString(GetSymbolFillingMode(g_symbol))
          + " retcode=" + IntegerToString((int)checkRetcode)
          + " comment=" + checkReason
          + " last_error=" + IntegerToString(GetLastError()));
      NotifyDetailedOpenFailed(side, lot, entry, sl, tp1, tp2, tp3, checkReason, (int)checkRetcode, GetLastError());
      return false;
   }

   CloseOppositeIfNeeded(side);

   trade.SetExpertMagicNumber(g_activeMagic);
   trade.SetDeviationInPoints(InpDeviationPoints);
   ENUM_ORDER_TYPE_FILLING fillingMode = GetSymbolFillingMode(g_symbol);
   trade.SetTypeFilling(fillingMode);
   trade.SetAsyncMode(false);

   bool ok = false;
   string comment = "ORBVRSI_NATIVE";
   ResetLastError();

   if(side == 1)
      ok = trade.Buy(lot, g_symbol, 0.0, sl, 0.0, comment);
   else
      ok = trade.Sell(lot, g_symbol, 0.0, sl, 0.0, comment);

   uint retcode = trade.ResultRetcode();
   if(!ok || (retcode != TRADE_RETCODE_DONE && retcode != TRADE_RETCODE_PLACED && retcode != TRADE_RETCODE_DONE_PARTIAL))
   {
      int lastError = GetLastError();
      string err = "TRADE SEND FAILED: " + trade.ResultRetcodeDescription();
      SetBlockReason(err);
      g_lastOpenError = err;
      Log("TRADE SEND FAILED bot_id=" + g_activeBotId
          + " symbol=" + g_symbol
          + " side=" + SideToString(side)
          + " lot=" + DoubleToString(lot, 2)
          + " entry=" + PriceStr(entry)
          + " sl=" + PriceStr(sl)
          + " tp1=" + PriceStr(tp1)
          + " tp2=" + PriceStr(tp2)
          + " tp3=" + PriceStr(tp3)
          + " filling_raw=" + IntegerToString((int)SymbolInfoInteger(g_symbol, SYMBOL_FILLING_MODE))
          + " selected_filling=" + FillingModeToString(GetSymbolFillingMode(g_symbol))
          + " retcode=" + IntegerToString((int)retcode)
          + " desc=" + trade.ResultRetcodeDescription()
          + " last_error=" + IntegerToString(lastError));
      NotifyDetailedOpenFailed(side, lot, entry, sl, tp1, tp2, tp3, err, (int)retcode, lastError);
      return false;
   }

   ulong ticket = 0;
   double volume = 0.0, openPrice = 0.0, currentSL = 0.0;
   long type = -1;
   bool foundPosition = false;

   for(int wait = 0; wait < 150; wait++)
   {
      Sleep(150);
      if(FindOurPosition(ticket, volume, openPrice, currentSL, type))
      {
         foundPosition = true;
         break;
      }
   }

   if(!foundPosition)
   {
      int lastError = GetLastError();
      string err = "ORDER SENT BUT POSITION NOT FOUND";
      SetBlockReason(err);
      g_lastOpenError = err;
      Log("ORDER SENT BUT POSITION NOT FOUND bot_id=" + g_activeBotId
          + " symbol=" + g_symbol
          + " side=" + SideToString(side)
          + " retcode=" + IntegerToString((int)trade.ResultRetcode())
          + " desc=" + trade.ResultRetcodeDescription()
          + " order=" + StringFormat("%I64u", trade.ResultOrder())
          + " deal=" + StringFormat("%I64u", trade.ResultDeal())
          + " result_price=" + PriceStr(trade.ResultPrice())
          + " last_error=" + IntegerToString(lastError));
      NotifyDetailedOpenFailed(side, lot, entry, sl, tp1, tp2, tp3, err, (int)trade.ResultRetcode(), lastError);
      return false;
   }

   g_beClosedByTouch = false;
   g_plan.active = true;
   g_plan.ticket = ticket;
   g_plan.position_id = GetSelectedPositionId();
   g_plan.trade_uid = TradeUid(g_plan.position_id, ticket);
   g_plan.side = side;
   g_plan.entry = NormalizePrice(openPrice);
   g_plan.sl = sl;
   ComputeTPs(side, g_plan.entry, g_plan.sl, g_plan.tp1, g_plan.tp2, g_plan.tp3);
   g_plan.tp1_done = false;
   g_plan.tp2_done = false;
   g_plan.tp3_done = false;
   g_plan.be_done = false;
   g_plan.opened_sent = GvFlag("OPENED_SENT");
   g_plan.tp1_sent = GvFlag("TP1_SENT");
   g_plan.tp2_recorded = GvFlag("TP2_RECORDED");
   g_plan.tp3_recorded = GvFlag("TP3_RECORDED");
   g_plan.closed_sent = GvFlag("CLOSED_SENT");
   g_plan.original_lot = volume;
   g_plan.opened_at = TimeCurrent();
   g_lastKnownPositionProfit = 0.0;
   g_initialRiskMoney = GetInitialRisk(g_plan.entry, g_plan.sl, g_plan.original_lot, g_symbol);
   g_openEntryPrice = g_plan.entry;
   g_openLots = g_plan.original_lot;
   g_openTime = TimeCurrent();
   g_tp1ProfitMoney = 0.0;
   g_tp2ProfitMoney = 0.0;
   SetBlockReason(side == 1 ? "REAL BUY opened" : "REAL SELL opened");

   if(side == 1)
   {
      g_longTriggeredToday = true;
      g_longArmed = false;
   }
   else
   {
      g_shortTriggeredToday = true;
      g_shortArmed = false;
   }

   if(InpDisplayMode == DISPLAY_ACTIVE_ONLY)
      DeleteLiveObjects();

   DrawTradeVisuals("LIVE", g_plan.side, g_plan.entry, g_plan.sl, g_plan.tp1, g_plan.tp2, g_plan.tp3, g_plan.opened_at, true);
   DrawMainVisuals();

   {
      double _rm = g_initialRiskMoney;
      double _tp1e = (InpEnableTP1 && g_plan.tp1 > 0.0) ? GetExpectedProfit(g_plan.entry, g_plan.tp1, g_plan.original_lot * g_activeTP1Percent / 100.0, g_symbol) : 0.0;
      double _tp2e = (InpEnableTP2 && g_plan.tp2 > 0.0) ? GetExpectedProfit(g_plan.entry, g_plan.tp2, g_plan.original_lot * g_activeTP2Percent / 100.0, g_symbol) : 0.0;
      double _tp3e = (g_activeEnableTP3 && g_plan.tp3 > 0.0 && g_activeTP3Percent > 0.0) ? GetExpectedProfit(g_plan.entry, g_plan.tp3, g_plan.original_lot * g_activeTP3Percent / 100.0, g_symbol) : 0.0;
      double _maxp = _tp1e + _tp2e + _tp3e;
      string _tpPlan = "[";
      if(InpEnableTP1)
         _tpPlan += "{\"index\":1,\"enabled\":true,\"price\":" + JsonNum(g_plan.tp1) + ",\"percent\":" + JsonNum(g_activeTP1Percent) + ",\"volume_planned\":" + JsonNum(g_plan.original_lot * g_activeTP1Percent / 100.0) + ",\"points\":" + JsonNum(MathAbs(g_plan.tp1 - g_plan.entry)) + "}";
      if(InpEnableTP2)
      {
         if(StringLen(_tpPlan) > 1) _tpPlan += ",";
         _tpPlan += "{\"index\":2,\"enabled\":true,\"price\":" + JsonNum(g_plan.tp2) + ",\"percent\":" + JsonNum(g_activeTP2Percent) + ",\"volume_planned\":" + JsonNum(g_plan.original_lot * g_activeTP2Percent / 100.0) + ",\"points\":" + JsonNum(MathAbs(g_plan.tp2 - g_plan.entry)) + "}";
      }
      if(g_activeEnableTP3)
      {
         if(StringLen(_tpPlan) > 1) _tpPlan += ",";
         _tpPlan += "{\"index\":3,\"enabled\":true,\"price\":" + JsonNum(g_plan.tp3) + ",\"percent\":" + JsonNum(g_activeTP3Percent) + ",\"volume_planned\":" + JsonNum(g_plan.original_lot * g_activeTP3Percent / 100.0) + ",\"points\":" + JsonNum(MathAbs(g_plan.tp3 - g_plan.entry)) + "}";
      }
      _tpPlan += "]";
      string _ex = "\"event_id\":" + JsonStr(EventId("opened"))
                 + ",\"trade_uid\":" + JsonStr(CurrentTradeUid())
                 + ",\"position_id\":" + StringFormat("%I64u", g_plan.position_id)
                 + ",\"position_ticket\":" + StringFormat("%I64u", g_plan.ticket)
                 + ",\"lot_initial\":" + JsonNum(g_plan.original_lot)
                 + ",\"opened_at\":" + JsonStr(TimeToString(g_plan.opened_at, TIME_DATE | TIME_SECONDS))
                 + ",\"telegram_silent\":false"
                 + ",\"tp_plan\":" + _tpPlan
                 + ",\"risk_money\":" + JsonNum(_rm)
                 + ",\"tp1_expected\":" + JsonNum(_tp1e)
                 + ",\"tp2_expected\":" + JsonNum(_tp2e)
                 + ",\"tp3_expected\":" + JsonNum(_tp3e)
                 + ",\"max_profit\":" + JsonNum(_maxp)
                 + ",\"orb_high\":" + JsonNum(g_orbHigh)
                 + ",\"orb_low\":" + JsonNum(g_orbLow)
                 + ",\"orb_range\":" + JsonNum(g_orbHigh - g_orbLow)
                 + ",\"vwap\":" + JsonNum(g_vwap)
                 + ",\"rsi_bias\":" + JsonNum(g_diagBiasRsiValue)
                 + ",\"rsi_entry\":" + JsonNum(g_diagEntryRsiValue);
      if(!g_plan.opened_sent)
      {
         SendNativeEvent("opened", "opened", 0.0, side, _ex);
         g_plan.opened_sent = true;
         SetGvFlag("OPENED_SENT");
      }
   }
   SendNativeAccount();
   if(InpScreenshotOnOpen)
      SendNativeScreenshot("opened", "opened", side);

   Log("Trade opened. Actual entry=" + DoubleToString(g_plan.entry, g_digits) + " ticket=" + StringFormat("%I64u", ticket));
   return true;
}
bool ClosePartial(double percent, DealInfo &deal)
{
   ulong ticket = 0;
   double volume = 0.0, openPrice = 0.0, currentSL = 0.0;
   long type = -1;

   if(!FindOurPosition(ticket, volume, openPrice, currentSL, type))
      return false;

   double minVol = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);
   double rawClose = g_plan.original_lot * percent / 100.0;

   if(rawClose < minVol)
      rawClose = MathMin(volume, minVol);

   if(volume - rawClose < minVol)
      rawClose = volume;

   double closeVol = NormalizeVolume(rawClose);

   if(closeVol <= 0.0)
      return false;

   datetime requestTime = TimeCurrent();
   if(!trade.PositionClosePartial(ticket, closeVol))
   {
      string err = trade.ResultRetcodeDescription();
      Log("Partial close failed: " + err);
      SendNativeEvent("close_failed", err, 0.0, g_plan.side);
      return false;
   }

   for(int wait = 0; wait < 40; wait++)
   {
      Sleep(150);
      if(FindNewestClosingDeal(g_plan.position_id, requestTime, deal))
         return true;
   }

   Log("Partial close deal not found in MT5 history");
   return false;
}

bool MoveStopToBE()
{
   if(g_plan.be_done || !InpMoveStopToBEAfterTP1)
      return g_plan.be_done;

   ulong ticket = 0;
   double volume = 0.0, openPrice = 0.0, currentSL = 0.0;
   long type = -1;

   if(!FindOurPosition(ticket, volume, openPrice, currentSL, type))
      return false;

   double be = NormalizePrice(g_plan.entry);

   if(trade.PositionModify(ticket, be, 0.0))
   {
      g_plan.be_done = true;
      g_plan.sl = be;
      DrawHLine("LIVE_BE", be, InpBEColor, 1, STYLE_DOT);
      DrawText("LIVE_BE_TXT", TimeCurrent(), be, "ENTRY " + PriceStr(be) + "  BE", InpBEColor, 9);
      Log("BE moved to actual MT5 entry " + PriceStr(be));
      return true;
   }
   else
   {
      string err = trade.ResultRetcodeDescription();
      Log("BE move failed: " + err);
      SendNativeEvent("close_failed", "BE move failed: " + err, 0.0, g_plan.side);
   }
   return false;
}

void ManageOpenPositionOnTick()
{
   ulong ticket = 0;
   double volume = 0.0, openPrice = 0.0, currentSL = 0.0;
   long type = -1;

   bool found = FindOurPosition(ticket, volume, openPrice, currentSL, type);

   if(!found)
   {
      if(g_plan.active)
      {
         DrawMainVisuals();
         {
            DealSummary summary;
            bool summaryReady = CollectPositionClosingDeals(g_plan.position_id, g_plan.opened_at, TimeCurrent(), summary);
            if(!summaryReady || summary.count <= 0)
            {
               Log("Close summary not ready yet. Waiting for MT5 history deals.");
               return;
            }
            double _pm = summary.total_net;
            double _rm = g_initialRiskMoney;
            double _pr = (_rm > 0.0) ? _pm / _rm : 0.0;
            double _ep = summary.exit_price;
            double _durSec = g_plan.opened_at > 0 ? (double)(TimeCurrent() - g_plan.opened_at) : 0.0;
            string _cr;
            if(g_beClosedByTouch)                        _cr = "be_touch";
            else if(g_plan.tp2_done || g_plan.tp3_done)  _cr = "tp2";
            else if(g_plan.be_done && _pm >= -0.5)        _cr = "be";
            else if(_pm < -0.5)                           _cr = "sl";
            else                                          _cr = "manual";
            string _etype = _pm >= 0.0 ? "closed_profit" : "closed_loss";
            string _ex = "\"event_id\":" + JsonStr(EventId(_etype, summary.last_deal_ticket))
                       + ",\"trade_uid\":" + JsonStr(CurrentTradeUid())
                       + ",\"position_id\":" + StringFormat("%I64u", g_plan.position_id)
                       + ",\"position_ticket\":" + StringFormat("%I64u", g_plan.ticket)
                       + ",\"lot_initial\":" + JsonNum(g_plan.original_lot)
                       + ",\"opened_at\":" + JsonStr(TimeToString(g_plan.opened_at, TIME_DATE | TIME_SECONDS))
                       + ",\"closed_at\":" + JsonStr(TimeToString(summary.closed_at > 0 ? summary.closed_at : TimeCurrent(), TIME_DATE | TIME_SECONDS))
                       + ",\"duration_seconds\":" + JsonNum(_durSec)
                       + ",\"profit_money\":" + JsonNum(_pm)
                       + ",\"risk_money\":" + JsonNum(_rm)
                       + ",\"total_profit\":" + JsonNum(summary.total_profit)
                       + ",\"total_commission\":" + JsonNum(summary.total_commission)
                       + ",\"total_swap\":" + JsonNum(summary.total_swap)
                       + ",\"total_net\":" + JsonNum(summary.total_net)
                       + ",\"r_total\":" + JsonNum(_pr)
                       + ",\"tp1_profit\":" + JsonNum(g_tp1ProfitMoney)
                       + ",\"tp2_profit\":" + JsonNum(g_tp2ProfitMoney)
                       + ",\"exit_price\":" + JsonNum(_ep)
                       + ",\"close_reason\":" + JsonStr(_cr)
                       + ",\"partials\":" + summary.partials_json
                       + ",\"daily_pnl\":" + JsonNum(GetDailyPnL());
            if(!g_plan.closed_sent && !GvFlag("CLOSED_SENT"))
            {
               SendNativeEvent(_etype, "closed", summary.total_net, g_plan.side, _ex);
               g_plan.closed_sent = true;
               SetGvFlag("CLOSED_SENT");
            }
         }
         SendNativeAccount();
         if(InpScreenshotOnClose)
            SendNativeScreenshot("closed", "closed", g_plan.side);
      }
      g_plan.active = false;
      return;
   }

   double posProfit = PositionGetDouble(POSITION_PROFIT);
   g_lastKnownPositionProfit = posProfit;

   if(!g_plan.active)
   {
      g_plan.active = true;
      g_plan.ticket = ticket;
      g_plan.position_id = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
      g_plan.trade_uid = TradeUid(g_plan.position_id, ticket);
      g_plan.side = type == POSITION_TYPE_BUY ? 1 : -1;
      g_plan.entry = NormalizePrice(openPrice);
      g_plan.sl = NormalizePrice(currentSL);
      ComputeTPs(g_plan.side, g_plan.entry, g_plan.sl, g_plan.tp1, g_plan.tp2, g_plan.tp3);
      g_plan.original_lot = volume;
      g_plan.opened_at = (datetime)PositionGetInteger(POSITION_TIME);
      if(g_plan.opened_at <= 0)
         g_plan.opened_at = TimeCurrent();
      if(MathAbs(g_plan.entry - g_plan.sl) <= g_tickSize)
         Log("initial risk restored approximately");
      g_initialRiskMoney = GetInitialRisk(g_plan.entry, g_plan.sl, g_plan.original_lot, g_symbol);
      g_openEntryPrice = g_plan.entry;
      g_openLots = g_plan.original_lot;
      g_openTime = g_plan.opened_at;
      g_plan.opened_sent = GvFlag("OPENED_SENT");
      g_plan.tp1_sent = GvFlag("TP1_SENT");
      g_plan.tp2_recorded = GvFlag("TP2_RECORDED");
      g_plan.tp3_recorded = GvFlag("TP3_RECORDED");
      g_plan.closed_sent = GvFlag("CLOSED_SENT");
      DrawTradeVisuals("LIVE", g_plan.side, g_plan.entry, g_plan.sl, g_plan.tp1, g_plan.tp2, g_plan.tp3, g_plan.opened_at, true);
   }

   double bid = SymbolInfoDouble(g_symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(g_symbol, SYMBOL_ASK);
   double px = g_plan.side == 1 ? bid : ask;

   bool hitTp1 = (g_plan.side == 1) ? (px >= g_plan.tp1) : (px <= g_plan.tp1);
   bool hitTp2 = (g_plan.side == 1) ? (px >= g_plan.tp2) : (px <= g_plan.tp2);
   bool hitTp3 = (g_plan.side == 1) ? (px >= g_plan.tp3) : (px <= g_plan.tp3);

   if(InpEnableTP1 && !g_plan.tp1_done && hitTp1)
   {
      DealInfo deal;
      if(ClosePartial(g_activeTP1Percent, deal))
      {
         g_plan.tp1_done = true;
         DrawText("LIVE_TP1_DONE", TimeCurrent(), g_plan.tp1, "TP1 OK", InpTPColor, 9);
         bool beMoved = MoveStopToBE();
         g_tp1ProfitMoney = deal.net;
         string _ex = DealExtraJson("tp1_be", deal, 1, g_plan.tp1, g_activeTP1Percent, false, beMoved);
         if(!g_plan.tp1_sent && !GvFlag("TP1_SENT"))
         {
            SendNativeEvent("tp1_be", "TP1 BE", deal.net, g_plan.side, _ex);
            g_plan.tp1_sent = true;
            SetGvFlag("TP1_SENT");
         }
         SendNativeAccount();
         if(InpScreenshotOnTP1)
            SendNativeScreenshot("tp1_be", "tp1_be", g_plan.side);
      }
   }

   if(InpEnableTP2 && !g_plan.tp2_done && hitTp2)
   {
      DealInfo deal;
      if(ClosePartial(g_activeTP2Percent, deal))
      {
         g_plan.tp2_done = true;
         DrawText("LIVE_TP2_DONE", TimeCurrent(), g_plan.tp2, "TP2 OK", InpTPColor, 9);
         g_tp2ProfitMoney = deal.net;
         string _ex = DealExtraJson("tp2_silent", deal, 2, g_plan.tp2, g_activeTP2Percent, true, false);
         if(!g_plan.tp2_recorded && !GvFlag("TP2_RECORDED"))
         {
            SendNativeEvent("tp2_silent", "TP2", deal.net, g_plan.side, _ex);
            g_plan.tp2_recorded = true;
            SetGvFlag("TP2_RECORDED");
         }
         SendNativeAccount();
      }
   }

   if(g_activeEnableTP3 && !g_plan.tp3_done && hitTp3)
   {
      DealInfo deal;
      double pct = g_activeTP3Percent <= 0.0 ? 100.0 : g_activeTP3Percent;
      if(ClosePartial(pct, deal))
      {
         g_plan.tp3_done = true;
         DrawText("LIVE_TP3_DONE", TimeCurrent(), g_plan.tp3, "TP3 OK", InpTPColor, 9);
         string _ex = DealExtraJson("tp3_silent", deal, 3, g_plan.tp3, pct, true, false);
         if(!g_plan.tp3_recorded && !GvFlag("TP3_RECORDED"))
         {
            SendNativeEvent("tp3_silent", "TP3", deal.net, g_plan.side, _ex);
            g_plan.tp3_recorded = true;
            SetGvFlag("TP3_RECORDED");
         }
         SendNativeAccount();
      }
   }

   CheckBreakEvenTouchExit();
   CheckInvalidExits();
}

void CheckBreakEvenTouchExit()
{
   if(!InpCloseAtBEOnTouch || !g_plan.active || !g_plan.be_done || !g_plan.tp1_done)
      return;

   double buffer = InpBETriggerBufferPoints * g_point;
   double bid = SymbolInfoDouble(g_symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(g_symbol, SYMBOL_ASK);

   bool touched = false;
   if(g_plan.side == 1)
      touched = (bid <= g_plan.entry + buffer);
   else
      touched = (ask >= g_plan.entry - buffer);

   if(!touched)
      return;

   ulong ticket = 0;
   double volume = 0.0, openPrice = 0.0, currentSL = 0.0;
   long type = -1;

   if(!FindOurPosition(ticket, volume, openPrice, currentSL, type))
      return;

   double p = PositionGetDouble(POSITION_PROFIT);
   if(trade.PositionClose(ticket))
   {
      g_beClosedByTouch = true;
      g_lastKnownPositionProfit = p;
      Log("BE touch exit: side=" + SideToString(g_plan.side)
          + " entry=" + PriceStr(g_plan.entry)
          + " bid=" + PriceStr(bid)
          + " ask=" + PriceStr(ask));
   }
   else
      Log("BE touch close failed: " + trade.ResultRetcodeDescription());
}

void CheckInvalidExits()
{
   if(!g_plan.active)
      return;

   MqlRates r[];
   ArraySetAsSeries(r, true);
   if(CopyRates(g_symbol, PERIOD_CURRENT, 0, 2, r) < 2)
      return;

   double close = r[1].close;
   bool closeIt = false;
   string reason = "";

   if(g_activeReturnOrb)
   {
      if(g_plan.side == 1 && close < g_orbHigh)
      {
         closeIt = true;
         reason = "return inside ORB";
      }

      if(g_plan.side == -1 && close > g_orbLow)
      {
         closeIt = true;
         reason = "return inside ORB";
      }
   }

   if(g_activeVWAPExit)
   {
      if(g_plan.side == 1 && close < g_vwap)
      {
         closeIt = true;
         reason = "VWAP invalid";
      }

      if(g_plan.side == -1 && close > g_vwap)
      {
         closeIt = true;
         reason = "VWAP invalid";
      }
   }

   if(InpCloseAtSessionEnd && InpUseSessionFilter && !IsSessionActive(TimeCurrent()))
   {
      closeIt = true;
      reason = "session close";
   }

   if(closeIt)
   {
      ulong ticket = 0;
      double volume = 0.0, openPrice = 0.0, currentSL = 0.0;
      long type = -1;

      if(FindOurPosition(ticket, volume, openPrice, currentSL, type))
      {
         double p = PositionGetDouble(POSITION_PROFIT);
         if(trade.PositionClose(ticket))
         {
            Log("Closed by " + reason);
            g_lastKnownPositionProfit = p;
            DrawMainVisuals();
            {
               DealSummary summary;
               bool summaryReady = CollectPositionClosingDeals(g_plan.position_id, g_plan.opened_at, TimeCurrent(), summary);
               if(!summaryReady || summary.count <= 0)
               {
                  Log("Close summary not ready yet. Waiting for MT5 history deals.");
                  return;
               }
               double _pm = summary.total_net;
               double _rm = g_initialRiskMoney;
               double _pr = (_rm > 0.0) ? _pm / _rm : 0.0;
               double _ep = summary.exit_price;
               double _durSec = g_plan.opened_at > 0 ? (double)(TimeCurrent() - g_plan.opened_at) : 0.0;
               string _cr = (_pm < -0.5) ? "sl" : "manual";
               string _etype = _pm >= 0.0 ? "closed_profit" : "closed_loss";
               string _ex = "\"event_id\":" + JsonStr(EventId(_etype, summary.last_deal_ticket))
                          + ",\"trade_uid\":" + JsonStr(CurrentTradeUid())
                          + ",\"position_id\":" + StringFormat("%I64u", g_plan.position_id)
                          + ",\"position_ticket\":" + StringFormat("%I64u", g_plan.ticket)
                          + ",\"lot_initial\":" + JsonNum(g_plan.original_lot)
                          + ",\"opened_at\":" + JsonStr(TimeToString(g_plan.opened_at, TIME_DATE | TIME_SECONDS))
                          + ",\"closed_at\":" + JsonStr(TimeToString(summary.closed_at > 0 ? summary.closed_at : TimeCurrent(), TIME_DATE | TIME_SECONDS))
                          + ",\"duration_seconds\":" + JsonNum(_durSec)
                          + ",\"profit_money\":" + JsonNum(_pm)
                          + ",\"risk_money\":" + JsonNum(_rm)
                          + ",\"total_profit\":" + JsonNum(summary.total_profit)
                          + ",\"total_commission\":" + JsonNum(summary.total_commission)
                          + ",\"total_swap\":" + JsonNum(summary.total_swap)
                          + ",\"total_net\":" + JsonNum(summary.total_net)
                          + ",\"r_total\":" + JsonNum(_pr)
                          + ",\"tp1_profit\":" + JsonNum(g_tp1ProfitMoney)
                          + ",\"tp2_profit\":" + JsonNum(g_tp2ProfitMoney)
                          + ",\"exit_price\":" + JsonNum(_ep)
                          + ",\"close_reason\":" + JsonStr(_cr)
                          + ",\"partials\":" + summary.partials_json
                          + ",\"daily_pnl\":" + JsonNum(GetDailyPnL());
               if(!g_plan.closed_sent && !GvFlag("CLOSED_SENT"))
               {
                  SendNativeEvent(_etype, reason, summary.total_net, g_plan.side, _ex);
                  g_plan.closed_sent = true;
                  SetGvFlag("CLOSED_SENT");
               }
            }
            SendNativeAccount();
            if(InpScreenshotOnClose)
               SendNativeScreenshot("closed", "closed", g_plan.side);
            g_plan.active = false;
         }
         else
         {
            string err = trade.ResultRetcodeDescription();
            Log("Close failed: " + err);
            SendNativeEvent("close_failed", err, 0.0, g_plan.side);
         }
      }
   }
}

//====================================================================
// VISUALS
//====================================================================
void DrawVersionLabel()
{
   string obj = ObjName("EA_Version_Label");
   if(ObjectFind(0, obj) < 0)
      ObjectCreate(0, obj, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, obj, OBJPROP_CORNER, CORNER_RIGHT_LOWER);
   ObjectSetInteger(0, obj, OBJPROP_XDISTANCE, 10);
   ObjectSetInteger(0, obj, OBJPROP_YDISTANCE, 10);
   ObjectSetString(0, obj, OBJPROP_TEXT, "v" + EA_VERSION + " (" + EA_BUILD + ")");
   ObjectSetInteger(0, obj, OBJPROP_COLOR, clrGray);
   ObjectSetInteger(0, obj, OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, obj, OBJPROP_SELECTABLE, false);
}

void DrawMainVisuals()
{
   datetime now = TimeCurrent();
   int dk = DateKey(now);
   datetime orbStart = ServerTimeFromNY(g_activeOrbStartHour, g_activeOrbStartMinute, dk);
   datetime orbEnd = ServerTimeFromNY(g_activeOrbEndHour, g_activeOrbEndMinute, dk);
   datetime right = now + PeriodSeconds(PERIOD_CURRENT) * InpLineExtendBars;

   if(g_orbLocked && g_orbHigh > 0.0 && g_orbLow > 0.0)
   {
      if(InpShowOrbLines)
      {
         DrawTrendLine("ORB_HIGH", orbEnd, g_orbHigh, right, g_orbHigh, InpOrbHighColor, 2, STYLE_SOLID);
         DrawTrendLine("ORB_LOW", orbEnd, g_orbLow, right, g_orbLow, InpOrbLowColor, 2, STYLE_SOLID);
         DrawText("ORB_HIGH_TXT", right, g_orbHigh, "ORB H " + PriceStr(g_orbHigh), InpOrbHighColor, 8);
         DrawText("ORB_LOW_TXT", right, g_orbLow, "ORB L " + PriceStr(g_orbLow), InpOrbLowColor, 8);
      }

      if(InpShowOrbZone)
         DrawRectangle("ORB_ZONE", orbStart, g_orbHigh, right, g_orbLow, InpOrbZoneColor);
   }

   if(InpShowVWAP && g_vwap > 0.0)
   {
      DrawHLine("VWAP", g_vwap, InpVWAPColor, 2, STYLE_SOLID);
      DrawText("VWAP_TXT", right, g_vwap, "VWAP " + PriceStr(g_vwap), InpVWAPColor, 8);
   }

   if(InpShowStatusPanel)
      DrawStatusPanel();

   if(InpShowStatsPanel)
      DrawStatsPanel();

   DrawCompactStatusIndicator();
   DrawVersionLabel();
}

void DrawCompactStatusIndicator()
{
   ObjectDelete(0, ObjName("REMOTE_CONTROL_PANEL"));

   string tradeReason = "";
   bool tradeOk = IsTradingAllowedNow(tradeReason);
   long tradeMode = SymbolInfoInteger(g_symbol, SYMBOL_TRADE_MODE);
   double bid = SymbolInfoDouble(g_symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(g_symbol, SYMBOL_ASK);

   bool ok = StringLen(InpNativeSecret) > 0
             && g_remoteConfigOk
             && g_remoteTradingEnabled
             && tradeOk
             && tradeMode != SYMBOL_TRADE_MODE_DISABLED
             && tradeMode != SYMBOL_TRADE_MODE_CLOSEONLY
             && bid > 0.0
             && ask > 0.0;

   string obj = ObjName("COMPACT_STATUS_INDICATOR");
   if(ObjectFind(0, obj) < 0)
      ObjectCreate(0, obj, OBJ_LABEL, 0, 0, 0);

   ObjectSetInteger(0, obj, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, obj, OBJPROP_XDISTANCE, 12);
   ObjectSetInteger(0, obj, OBJPROP_YDISTANCE, 18);
   ObjectSetString(0, obj, OBJPROP_TEXT, ok ? "● работает" : "● ошибка");
   ObjectSetInteger(0, obj, OBJPROP_COLOR, ok ? clrLime : clrTomato);
   ObjectSetInteger(0, obj, OBJPROP_FONTSIZE, 10);
   ObjectSetString(0, obj, OBJPROP_FONT, "Consolas");
   ObjectSetInteger(0, obj, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, obj, OBJPROP_HIDDEN, false);
   ObjectSetInteger(0, obj, OBJPROP_BACK, false);
}
void DrawTradeVisuals(string tag, int side, double entry, double sl, double tp1, double tp2, double tp3, datetime entryTime, bool live)
{
   if(!InpShowTradeLevels)
      return;

   if(!live && !InpShowHistoryTrades)
      return;

   datetime right = entryTime + PeriodSeconds(PERIOD_CURRENT) * InpLineExtendBars;
   string cleanTime = TimeToString(entryTime, TIME_DATE | TIME_MINUTES);
   StringReplace(cleanTime, ":", "");
   StringReplace(cleanTime, ".", "");
   StringReplace(cleanTime, " ", "_");

   string s = tag + "_" + cleanTime;
   color entryClr = live ? InpEntryColor : clrSilver;
   color slClr = live ? InpSLColor : clrDimGray;
   color tpClr = live ? InpTPColor : clrDimGray;
   color vertClr = live ? clrWhite : clrDimGray;

   DrawTrendLine(s + "_ENTRY", entryTime, entry, right, entry, entryClr, 1, STYLE_DOT);
   DrawTrendLine(s + "_SL", entryTime, sl, right, sl, slClr, 1, STYLE_DOT);

   if(InpEnableTP1) DrawTrendLine(s + "_TP1", entryTime, tp1, right, tp1, tpClr, 1, STYLE_DOT);
   if(InpEnableTP2) DrawTrendLine(s + "_TP2", entryTime, tp2, right, tp2, tpClr, 1, STYLE_DOT);
   if(g_activeEnableTP3) DrawTrendLine(s + "_TP3", entryTime, tp3, right, tp3, tpClr, 1, STYLE_DOT);

   double top = MathMax(entry, sl);
   double bot = MathMin(entry, sl);
   if(InpEnableTP1) { top = MathMax(top, tp1); bot = MathMin(bot, tp1); }
   if(InpEnableTP2) { top = MathMax(top, tp2); bot = MathMin(bot, tp2); }
   if(g_activeEnableTP3) { top = MathMax(top, tp3); bot = MathMin(bot, tp3); }

   DrawTrendLine(s + "_VERT", entryTime, top, entryTime, bot, vertClr, 1, STYLE_DOT);

   string baseSide = side == 1 ? "BUY" : "SELL";
   string sideText = live ? ("REAL " + baseSide) : ("BACKTEST " + baseSide);
   color sideColor = live ? (side == 1 ? clrLime : clrTomato) : clrSilver;

   if(InpShowSignals)
      DrawText(s + "_SIDE", entryTime, entry, sideText, sideColor, 10);

   DrawText(s + "_ENTRY_TXT", right, entry, (live ? "REAL ENTRY " : "BT ENTRY ") + PriceStr(entry), entryClr, 8);
   DrawText(s + "_SL_TXT", right, sl, "SL " + PriceStr(sl), slClr, 8);
   if(InpEnableTP1) DrawText(s + "_TP1_TXT", right, tp1, "TP1 " + PriceStr(tp1), tpClr, 8);
   if(InpEnableTP2) DrawText(s + "_TP2_TXT", right, tp2, "TP2 " + PriceStr(tp2), tpClr, 8);
   if(g_activeEnableTP3) DrawText(s + "_TP3_TXT", right, tp3, "TP3 " + PriceStr(tp3), tpClr, 8);
}

void DrawStatusPanel()
{
   double biasRsi = GetRSI(g_biasRsiHandle, 1);
   double entryRsi = GetRSI(g_entryRsiHandle, 1);
   double close1 = iClose(g_symbol, PERIOD_CURRENT, 1);

   string vwapBias = g_vwap <= 0.0 ? "n/a" : (close1 > g_vwap ? "ABV" : "BLW");
   string orderFlow = "OFF";
   string mode = InpRequireRetest ? "RETEST" : "BREAK";
   
   ulong ticket = 0;
   double volume = 0.0, openPrice = 0.0, currentSL = 0.0;
   long type = -1;
   bool pos = FindOurPosition(ticket, volume, openPrice, currentSL, type);
   double pnl = pos ? PositionGetDouble(POSITION_PROFIT) : 0.0;

   string text = g_activeBotId + " v" + EA_VERSION + "\n";
   text += "Preset:" + EnumToString(InpPresetMode) + "  ORB:" + (g_orbLocked ? "ON" : "WAIT") + "  VWAP:" + vwapBias + "\n";
   text += "RSI:" + DoubleToString(biasRsi, 1) + "/" + DoubleToString(entryRsi, 1) + "  OF:" + orderFlow + "\n";
   text += "Mode:" + mode + "  CVD:" + DoubleToString(g_cvd, 0) + "\n";
   text += "Pos:" + (pos ? (type == POSITION_TYPE_BUY ? "BUY" : "SELL") : "NONE") + "  Lot:" + (pos ? DoubleToString(volume, 2) : "0.00") + "\n";
   text += "PnL:" + MoneyStr(pnl) + "  Eq:" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + "\n";
   string panelBlock = ((!g_remoteConfigOk || !g_remoteTradingEnabled) ? g_remoteBlockReason : g_lastBlockReason);
   text += "Remote: " + (g_remoteTradingEnabled ? "ON" : "OFF") + "  Cfg: " + (g_remoteConfigOk ? "OK" : "FAIL") + "\n";
   text += "Block: " + panelBlock + "\n";

   if(InpShowEntryDiagnostics)
   {
      text += "L Brk:" + YN(g_diagLongBreak) + " Ret:" + YN(g_diagLongRetest) + " F:" + YN(g_diagLongFilter) + " S:" + YN(g_diagLongSignal) + "\n";
      text += "S Brk:" + YN(g_diagShortBreak) + " Ret:" + YN(g_diagShortRetest) + " F:" + YN(g_diagShortFilter) + " S:" + YN(g_diagShortSignal) + "\n";
      text += "VWAP L/S:" + YN(g_diagVwapLongOk) + "/" + YN(g_diagVwapShortOk) + " OF:OFF\n";
      text += "RSI B:" + YN(g_diagBiasLongOk) + "/" + YN(g_diagBiasShortOk) + " E:" + YN(g_diagEntryLongOk) + "/" + YN(g_diagEntryShortOk) + "\n";
   }

   color pnlColor = pnl > 0.0 ? InpPanelGoodColor : pnl < 0.0 ? InpPanelBadColor : InpPanelTextColor;
   DrawLabel("STATUS_PANEL", InpPanelX, InpPanelY, text, pnlColor, 8);
}

void DrawStatsPanel()
{
   if(!InpCalculateVirtualHistory)
      return;

   double winrate = g_stats.trades > 0 ? (double)g_stats.wins / (double)g_stats.trades * 100.0 : 0.0;
   double pf = g_stats.gross_loss > 0.0 ? g_stats.gross_profit / g_stats.gross_loss : (g_stats.gross_profit > 0.0 ? 999.0 : 0.0);
   string pfText = pf >= 999.0 ? "INF" : DoubleToString(pf, 2);

   string text = IntegerToString(InpVirtualHistoryDays) + "d: " + IntegerToString(g_stats.trades) + "T  " + DoubleToString(winrate, 0) + "%\n";
   text += "PnL:" + MoneyStr(g_stats.net_pnl) + "  PF:" + pfText + "\n";
   text += "Bal:" + DoubleToString(g_stats.balance, 2) + "\n";

   color c = g_stats.net_pnl > 0.0 ? InpPanelGoodColor : g_stats.net_pnl < 0.0 ? InpPanelBadColor : InpPanelTextColor;
   DrawLabel("STATS_PANEL", InpPanelX, InpPanelY + 150, text, c, 8);
}

//====================================================================
// VIRTUAL HISTORY
//====================================================================
void ResetStats()
{
   ArrayResize(g_virtualHistory, 0);
   g_stats.trades = 0;
   g_stats.wins = 0;
   g_stats.losses = 0;
   g_stats.breakeven = 0;
   g_stats.gross_profit = 0.0;
   g_stats.gross_loss = 0.0;
   g_stats.net_pnl = 0.0;
   g_stats.best_trade = -DBL_MAX;
   g_stats.worst_trade = DBL_MAX;
   g_stats.balance = InpVirtualStartBalance;
}

void AddVirtualTrade(double pnl)
{
   g_stats.trades++;
   g_stats.net_pnl += pnl;
   g_stats.balance += pnl;

   if(pnl > 0.0)
   {
      g_stats.wins++;
      g_stats.gross_profit += pnl;
   }
   else if(pnl < 0.0)
   {
      g_stats.losses++;
      g_stats.gross_loss += MathAbs(pnl);
   }
   else
   {
      g_stats.breakeven++;
   }

   g_stats.best_trade = MathMax(g_stats.best_trade, pnl);
   g_stats.worst_trade = MathMin(g_stats.worst_trade, pnl);
}

void CalculateVirtualHistory()
{
   if(!InpCalculateVirtualHistory)
      return;

   ResetStats();
   DeleteLiveObjects();

   MqlRates rates[];
   ArraySetAsSeries(rates, false);

   datetime to = TimeCurrent();
   datetime from = to - InpVirtualHistoryDays * 86400;
   int copied = CopyRates(g_symbol, PERIOD_CURRENT, from, to, rates);
   if(copied < 10)
      return;

   double vLot = NormalizeVolume(InpVirtualLot);
   if(vLot <= 0.0)
      vLot = NormalizeVolume(g_activeLot);
   if(vLot <= 0.0)
      return;

   bool saveOrbLocked = g_orbLocked;
   double saveOrbHigh = g_orbHigh;
   double saveOrbLow = g_orbLow;
   bool saveLongArmed = g_longArmed;
   bool saveShortArmed = g_shortArmed;
   bool saveLongDone = g_longTriggeredToday;
   bool saveShortDone = g_shortTriggeredToday;
   double saveVwap = g_vwap;
   double saveVwapPV = g_vwapPV;
   double saveVwapVol = g_vwapVol;
   double saveCvd = g_cvd;
   double savePrevCvd = g_prevCvd;
   double saveDeltaEma = g_deltaEma;
   bool saveHistoryMode = g_signalEngineHistoryMode;
   double saveHistBias = g_historyBiasRsi;
   double saveHistEntry = g_historyEntryRsi;
   double saveHistAtrStop = g_historyAtrStop;
   double saveHistAtrTp = g_historyAtrTp;

   int dayKey = 0;
   double dayOrbH = 0.0;
   double dayOrbL = 0.0;
   double dayVWAPPV = 0.0;
   double dayVWAPVol = 0.0;
   double dayVWAP = 0.0;
   double dayCvd = 0.0;
   double dayPrevCvd = 0.0;
   double dayDeltaEma = 0.0;
   bool orbLocked = false;
   bool longArmed = false;
   bool shortArmed = false;
   bool longDone = false;
   bool shortDone = false;
   bool virtActive = false;
   int virtSide = 0;
   double entry = 0.0, sl = 0.0, tp1 = 0.0, tp2 = 0.0, tp3 = 0.0;
   bool tp1Done = false, tp2Done = false, tp3Done = false;
   double closedPct = 0.0;
   double pnlAcc = 0.0;
   datetime entryTime = 0;
   double alpha = 2.0 / (InpDeltaSmoothingLength + 1.0);

   for(int i = 2; i < copied; i++)
   {
      int dk = DateKey(rates[i].time);
      if(dk != dayKey)
      {
         dayKey = dk;
         dayOrbH = 0.0;
         dayOrbL = 0.0;
         dayVWAPPV = 0.0;
         dayVWAPVol = 0.0;
         dayVWAP = 0.0;
         dayCvd = 0.0;
         dayPrevCvd = 0.0;
         dayDeltaEma = 0.0;
         orbLocked = false;
         longArmed = false;
         shortArmed = false;
         longDone = false;
         shortDone = false;
         virtActive = false;
      }

      double typ = (rates[i].high + rates[i].low + rates[i].close) / 3.0;
      double vol = (double)rates[i].tick_volume;
      if(vol <= 0.0)
         vol = 1.0;

      dayVWAPPV += typ * vol;
      dayVWAPVol += vol;
      dayVWAP = dayVWAPVol > 0.0 ? dayVWAPPV / dayVWAPVol : rates[i].close;

      double sign = rates[i].close > rates[i].open ? 1.0 : rates[i].close < rates[i].open ? -1.0 : 0.0;
      double delta = vol * sign;
      dayPrevCvd = dayCvd;
      dayCvd += delta;
      dayDeltaEma = (i == 2) ? delta : alpha * delta + (1.0 - alpha) * dayDeltaEma;

      if(IsTimeInNYWindow(rates[i].time, g_activeOrbStartHour, g_activeOrbStartMinute, g_activeOrbEndHour, g_activeOrbEndMinute))
      {
         if(dayOrbH == 0.0 && dayOrbL == 0.0)
         {
            dayOrbH = rates[i].high;
            dayOrbL = rates[i].low;
         }
         else
         {
            dayOrbH = MathMax(dayOrbH, rates[i].high);
            dayOrbL = MathMin(dayOrbL, rates[i].low);
         }
      }

      if(dayOrbH > 0.0 && dayOrbL > 0.0 && HasPassedNYTime(rates[i].time, g_activeOrbEndHour, g_activeOrbEndMinute))
         orbLocked = true;

      if(virtActive)
      {
         if(virtSide == 1)
         {
            if(InpEnableTP1 && !tp1Done && rates[i].high >= tp1)
            {
               double partLot = vLot * g_activeTP1Percent / 100.0;
               pnlAcc += CalcProfitMoney(virtSide, partLot, entry, tp1);
               closedPct += g_activeTP1Percent;
               tp1Done = true;
               if(InpMoveStopToBEAfterTP1) sl = entry;
            }
            if(InpEnableTP2 && !tp2Done && rates[i].high >= tp2)
            {
               double partLot = vLot * g_activeTP2Percent / 100.0;
               pnlAcc += CalcProfitMoney(virtSide, partLot, entry, tp2);
               closedPct += g_activeTP2Percent;
               tp2Done = true;
            }
            if(g_activeEnableTP3 && !tp3Done && g_activeTP3Percent > 0.0 && rates[i].high >= tp3)
            {
               double partLot = vLot * g_activeTP3Percent / 100.0;
               pnlAcc += CalcProfitMoney(virtSide, partLot, entry, tp3);
               closedPct += g_activeTP3Percent;
               tp3Done = true;
            }
            if(rates[i].low <= sl)
            {
               double remainPct = MathMax(0.0, 100.0 - closedPct);
               double remainLot = vLot * remainPct / 100.0;
               pnlAcc += CalcProfitMoney(virtSide, remainLot, entry, sl);
               AppendVirtualHistoryTrade(virtSide, entry, sl, tp1, tp2, tp3, entryTime, rates[i].time, sl, pnlAcc);
               AddVirtualTrade(pnlAcc);
               if(InpShowHistoryTrades) DrawTradeVisuals("BACKTEST", virtSide, entry, sl, tp1, tp2, tp3, entryTime, false);
               virtActive = false;
            }
            else if(!g_activeEnableTP3 && tp2Done)
            {
               AppendVirtualHistoryTrade(virtSide, entry, sl, tp1, tp2, tp3, entryTime, rates[i].time, tp2, pnlAcc);
               AddVirtualTrade(pnlAcc);
               if(InpShowHistoryTrades) DrawTradeVisuals("BACKTEST", virtSide, entry, sl, tp1, tp2, tp3, entryTime, false);
               virtActive = false;
            }
         }
         else
         {
            if(InpEnableTP1 && !tp1Done && rates[i].low <= tp1)
            {
               double partLot = vLot * g_activeTP1Percent / 100.0;
               pnlAcc += CalcProfitMoney(virtSide, partLot, entry, tp1);
               closedPct += g_activeTP1Percent;
               tp1Done = true;
               if(InpMoveStopToBEAfterTP1) sl = entry;
            }
            if(InpEnableTP2 && !tp2Done && rates[i].low <= tp2)
            {
               double partLot = vLot * g_activeTP2Percent / 100.0;
               pnlAcc += CalcProfitMoney(virtSide, partLot, entry, tp2);
               closedPct += g_activeTP2Percent;
               tp2Done = true;
            }
            if(g_activeEnableTP3 && !tp3Done && g_activeTP3Percent > 0.0 && rates[i].low <= tp3)
            {
               double partLot = vLot * g_activeTP3Percent / 100.0;
               pnlAcc += CalcProfitMoney(virtSide, partLot, entry, tp3);
               closedPct += g_activeTP3Percent;
               tp3Done = true;
            }
            if(rates[i].high >= sl)
            {
               double remainPct = MathMax(0.0, 100.0 - closedPct);
               double remainLot = vLot * remainPct / 100.0;
               pnlAcc += CalcProfitMoney(virtSide, remainLot, entry, sl);
               AppendVirtualHistoryTrade(virtSide, entry, sl, tp1, tp2, tp3, entryTime, rates[i].time, sl, pnlAcc);
               AddVirtualTrade(pnlAcc);
               if(InpShowHistoryTrades) DrawTradeVisuals("BACKTEST", virtSide, entry, sl, tp1, tp2, tp3, entryTime, false);
               virtActive = false;
            }
            else if(!g_activeEnableTP3 && tp2Done)
            {
               AppendVirtualHistoryTrade(virtSide, entry, sl, tp1, tp2, tp3, entryTime, rates[i].time, tp2, pnlAcc);
               AddVirtualTrade(pnlAcc);
               if(InpShowHistoryTrades) DrawTradeVisuals("BACKTEST", virtSide, entry, sl, tp1, tp2, tp3, entryTime, false);
               virtActive = false;
            }
         }
      }

      if(virtActive || !orbLocked)
         continue;

      g_signalEngineHistoryMode = true;
      CopyIndicatorAtTime(g_biasRsiHandle, rates[i].time, g_historyBiasRsi);
      CopyIndicatorAtTime(g_entryRsiHandle, rates[i].time, g_historyEntryRsi);
      if(!CopyIndicatorAtTime(g_atrStopHandle, rates[i].time, g_historyAtrStop)) g_historyAtrStop = 0.0;
      if(!CopyIndicatorAtTime(g_atrTpHandle, rates[i].time, g_historyAtrTp)) g_historyAtrTp = 0.0;

      g_orbLocked = orbLocked;
      g_orbHigh = dayOrbH;
      g_orbLow = dayOrbL;
      g_vwap = dayVWAP;
      g_vwapPV = dayVWAPPV;
      g_vwapVol = dayVWAPVol;
      g_cvd = dayCvd;
      g_prevCvd = dayPrevCvd;
      g_deltaEma = dayDeltaEma;
      g_longArmed = longArmed;
      g_shortArmed = shortArmed;
      g_longTriggeredToday = longDone;
      g_shortTriggeredToday = shortDone;

      SignalDecision d = EvaluatePineLikeSignal(rates[i], rates[i - 1], false);
      longArmed = g_longArmed;
      shortArmed = g_shortArmed;

      if(d.signal)
      {
         virtActive = true;
         virtSide = d.side;
         entry = d.entry_ref;
         sl = d.sl_ref;
         tp1 = d.tp1_ref;
         tp2 = d.tp2_ref;
         tp3 = d.tp3_ref;
         entryTime = rates[i].time;
         tp1Done = false;
         tp2Done = false;
         tp3Done = false;
         closedPct = 0.0;
         pnlAcc = 0.0;
         if(d.side == 1)
         {
            longDone = true;
            longArmed = false;
         }
         else
         {
            shortDone = true;
            shortArmed = false;
         }
      }
   }

   g_orbLocked = saveOrbLocked;
   g_orbHigh = saveOrbHigh;
   g_orbLow = saveOrbLow;
   g_longArmed = saveLongArmed;
   g_shortArmed = saveShortArmed;
   g_longTriggeredToday = saveLongDone;
   g_shortTriggeredToday = saveShortDone;
   g_vwap = saveVwap;
   g_vwapPV = saveVwapPV;
   g_vwapVol = saveVwapVol;
   g_cvd = saveCvd;
   g_prevCvd = savePrevCvd;
   g_deltaEma = saveDeltaEma;
   g_signalEngineHistoryMode = saveHistoryMode;
   g_historyBiasRsi = saveHistBias;
   g_historyEntryRsi = saveHistEntry;
   g_historyAtrStop = saveHistAtrStop;
   g_historyAtrTp = saveHistAtrTp;
}
//====================================================================
// MT5 EVENTS
//====================================================================
int OnInit()
{
   g_symbol = InpTradeSymbol == "" ? _Symbol : InpTradeSymbol;

   if(!SymbolSelect(g_symbol, true))
   {
      Print("Cannot select symbol: ", g_symbol);
      return INIT_FAILED;
   }

   ApplyPreset();

   if(InpClearOldChartObjectsOnInit)
      ClearOldPrefixObjects();

   g_digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   g_point = SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   g_tickSize = SymbolInfoDouble(g_symbol, SYMBOL_TRADE_TICK_SIZE);
   g_tickValue = SymbolInfoDouble(g_symbol, SYMBOL_TRADE_TICK_VALUE);

   if(g_tickSize <= 0.0)
      g_tickSize = g_point;

   g_biasRsiHandle = iRSI(g_symbol, PERIOD_M5, InpBiasRsiLen, PRICE_CLOSE);
   g_entryRsiHandle = iRSI(g_symbol, InpEntryRsiTF, InpEntryRsiLen, PRICE_CLOSE);
   g_atrStopHandle = iATR(g_symbol, PERIOD_CURRENT, InpAtrStopLen);
   g_atrTpHandle = iATR(g_symbol, PERIOD_CURRENT, InpAtrTpLen);

   if(g_biasRsiHandle == INVALID_HANDLE || g_entryRsiHandle == INVALID_HANDLE || g_atrStopHandle == INVALID_HANDLE || g_atrTpHandle == INVALID_HANDLE)
   {
      Print("Indicator handle creation failed");
      return INIT_FAILED;
   }

   trade.SetExpertMagicNumber(g_activeMagic);
   trade.SetDeviationInPoints(InpDeviationPoints);
   ENUM_ORDER_TYPE_FILLING fillingMode = GetSymbolFillingMode(g_symbol);
   trade.SetTypeFilling(fillingMode);
   trade.SetAsyncMode(false);

   g_plan.active = false;
   ResetDayState(TimeCurrent());
   ResetEntryDiagnostics("init");
   RecalculateIntradayState();
   CalculateVirtualHistory();
   if(InpSendBacktestOnStart && !g_backtestHistorySent)
   {
      SendBacktestHistory();
      g_backtestHistorySent = true;
   }
   if(InpSendFullHistory || InpSendFullHistoryOnStart)
   {
      SendFullHistory();
   }
   RefreshRemoteConfig(true);
   SendNativeAccount();
   DrawCompactStatusIndicator();
   ChartRedraw(0);

   EventSetTimer(5);
   Log("REMOTE PANEL BUILD ACTIVE. bot_id=" + g_activeBotId);
   Log("Started on " + g_symbol + " preset=" + EnumToString(InpPresetMode) + " magic=" + IntegerToString(g_activeMagic));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   DeleteObjects();

   if(g_biasRsiHandle != INVALID_HANDLE) IndicatorRelease(g_biasRsiHandle);
   if(g_entryRsiHandle != INVALID_HANDLE) IndicatorRelease(g_entryRsiHandle);
   if(g_atrStopHandle != INVALID_HANDLE) IndicatorRelease(g_atrStopHandle);
   if(g_atrTpHandle != INVALID_HANDLE) IndicatorRelease(g_atrTpHandle);
}

void OnTimer()
{
   DrawMainVisuals();
   RefreshRemoteConfig(false);
   static datetime s_lastHeartbeat = 0;
   if(TimeCurrent() - s_lastHeartbeat >= 60)
   {
      s_lastHeartbeat = TimeCurrent();
      SendNativeHeartbeat();
   }
   DrawCompactStatusIndicator();
   ChartRedraw(0);

   if(InpSendAccountEveryMinutes > 0)
   {
      datetime now = TimeCurrent();
      if(g_lastAccountSend == 0 || now - g_lastAccountSend >= InpSendAccountEveryMinutes * 60)
      {
         if(SendNativeAccount())
            g_lastAccountSend = now;
      }
   }

   if(InpSendIncrementalHistory && InpHistorySyncEveryMinutes > 0)
   {
      datetime now = TimeCurrent();
      if(g_lastHistorySync == 0 || now - g_lastHistorySync >= InpHistorySyncEveryMinutes * 60)
      {
         SendIncrementalHistory();
         g_lastHistorySync = now;
      }
   }
}

void OnTick()
{
   
   ProcessPendingSignal();
datetime currentBar = iTime(g_symbol, PERIOD_CURRENT, 0);

   if(currentBar != g_lastBarTime)
   {
      g_lastBarTime = currentBar;

      int dk = DateKey(currentBar);
      if(dk != g_lastDateKey)
      {
         ResetDayState(currentBar);
         CalculateVirtualHistory();
      }
      EvaluateEntries();
      DrawMainVisuals();
   }

   ManageOpenPositionOnTick();
}
//+------------------------------------------------------------------+


















