"""
Candle Momentum & Market Structure Auto-Trading Bot (Bot 2).

Strategy Specification
----------------------
1. Configurable Lot Size (respects broker min/max/step).
2. Max Running Trades Limit:
   - At any time, running trades cannot exceed this user-defined count.
3. Market Selection & Live Price:
   - Selection chips (e.g. BTCUSDTm, BTCUSDm, XAUUSDm, etc.) and live Ask / Bid / Spread.
4. Watch Pressure Trend System (connected to the Market Pressure Watch Dials):
   - The Watch button switches this system ON / OFF.
   - ON: every trend_check_interval seconds (default 2.5s) the bot reads the live
     buyer/seller pressure of the watch dial for its symbol and trades only when
     that side's pressure is >= the Watch % field (nothing is fixed - whatever
     the user enters is what is checked).
   - BUYER pressure >= Watch %  -> BUY trade only.
   - SELLER pressure >= Watch % -> SELL trade only.
   - NEUTRAL / below the Watch % -> strictly NO trade.
   - OFF: the dial is ignored completely and the bot follows the candle - the
     side the 1-minute candle is actually moving to (candle UP -> BUY, candle
     DOWN -> SELL), one trade per 2.5s timer tick.
   - Reverse button: whatever direction the bot would take, it takes the exact
     opposite (BUY -> SELL, SELL -> BUY).
5. Market Structure Levels (Day & Weekend):
   - Shows Higher High (HH), Lower High (LH), Higher Low (HL), Lower Low (LL) for both
     Day (D1) and Weekend/Week (W1).
   - Key interaction rules (watch pressure still gates the direction):
     * Lower Low (LL) / Higher Low (HL) price  -> BUY  (only with BUYER pressure)
     * Higher High (HH) / Lower High (LH) price -> SELL (only with SELLER pressure)
     * NEUTRAL pressure (while Watch is ON) blocks structure trades too.
     * While Watch is OFF, structure levels are traded in their own natural
       direction (LL/HL -> BUY, HH/LH -> SELL) with no dial gate.
6. Continuous Trading:
   - As price and pressure move, it continues to take trades as long as running
     trades < Max Trades limit, always one trade per 2.5s timer tick.
7. Fixed TP / SL in dollars:
   - The TP ($) and SL ($) fields are authoritative: the dollar amounts are never
     widened or shrunk by broker stop-level constraints. The distance is computed
     from the configured dollar value and attached as-is when the broker accepts
     it; the same dollar targets are additionally enforced tick-by-tick in
     _manage_positions(), so the effective TP/SL always equals the fields.
8. Conditions displayed clearly in Bengali & English.
9. START, STOP, and Close All panic buttons.
10. 1-Minute Candle Time Windows:
   - No trade in the FIRST N seconds of the running 1-minute candle.
   - No trade in the LAST N seconds of the running 1-minute candle.
   - No trade inside the middle window (N-M seconds) of the candle.
   - Every window value comes from the Bot 2 UI fields - nothing is hardcoded.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

try:  # pragma: no cover
    import MetaTrader5 as mt5  # type: ignore
except Exception:  # pragma: no cover
    mt5 = None  # type: ignore

DEFAULTS: Dict[str, Any] = {
    "running": False,
    "lot": 0.01,
    "max_trades": 1,
    "tp": 0.10,          # TP in dollars (default $0.10)
    "sl": 2.00,          # SL in dollars (default $2.00)
    "symbol": "BTCUSDTm",
    "timeframe": "D1",   # "D1" for Day or "W1" for Weekend/Week
    "magic": 777002,     # distinct magic number from Bot 1 (777001)
    "slippage_points": 30,
    "cooldown_sec": 2.5,
    "trend_mode": True,    # legacy alias of watch_enabled - one switch, kept in sync
    "trend_check_interval": 2.5,   # Periodic trend check interval (seconds, default 2.5s)
    "watch_enabled": True,   # Watch ON/OFF switch for this bot (the UI button)
    "watch_min_pct": 60.0,   # Watch pressure % the user enters - trades only at/above it
    "reverse": False,        # Reverse button: flip every trade to the opposite side
    "watch_min_threshold_pct": 60.0,  # legacy alias of watch_min_pct (older configs)
    # 1-minute candle time-window no-trade blocks. These defaults are only the
    # initial display values - the user's UI field entries are authoritative and
    # nothing here is enforced as a fixed system value.
    "block_start_sec": 10.0,   # no trade in the FIRST N seconds of a candle
    "block_end_sec": 10.0,     # no trade in the LAST N seconds of a candle
    "block_mid_from": 25.0,    # no trade from this second ...
    "block_mid_to": 35.0,      # ... up to this second of the candle
}

PREFERRED_SYMBOLS = [
    "BTCUSDTm", "BTCUSDm", "BTCUSD",
    "XAUUSDm", "XAUUSD247m", "XAUUSD",
    "DXY", "DXYm", "USDX", "USDXm",
    "USOILm", "USOIL", "XTIUSDm", "XTIUSD", "CLm", "CL",
    "XAUGBPm", "XAUGBP",
    "USDJPYm", "USDJPY",
    "EURUSDm", "EURUSD",
    "USTECm", "USTEC", "NAS100m", "NAS100",
]

RETCODE_SUCCESS = {10008, 10009, 10010}
_RETCODE_NAMES = {
    10004: "requote", 10006: "rejected", 10007: "cancelled", 10008: "placed", 10009: "done",
    10010: "done partially", 10011: "error", 10012: "timeout", 10013: "invalid request",
    10014: "invalid volume", 10015: "invalid price", 10016: "invalid stops", 10017: "trading disabled",
    10018: "market closed", 10019: "no money", 10020: "price changed", 10021: "price off",
}


def _tname(retcode: int) -> str:
    return _RETCODE_NAMES.get(int(retcode), f"retcode {retcode}")


def _as_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    asdict = getattr(obj, "_asdict", None)
    if callable(asdict):
        return dict(asdict())
    # numpy structured row (MT5 candles) / slots object: read the real fields;
    # the old dict(vars(obj)) last resort raised TypeError on a numpy row and
    # killed the caller's whole tick, so nothing here may raise any more.
    names = getattr(getattr(obj, "dtype", None), "names", None)
    if names:
        out: Dict[str, Any] = {}
        for n in names:
            try:
                out[str(n)] = obj[n]
            except Exception:
                continue
        if out:
            return out
    slots = getattr(obj, "__slots__", None) or getattr(obj, "_fields", None)
    if slots:
        out = {}
        for s in slots:
            try:
                out[str(s)] = getattr(obj, s)
            except Exception:
                continue
        if out:
            return out
    try:
        return dict(obj)
    except (TypeError, ValueError):
        pass
    try:
        return dict(vars(obj))          # plain object carrying a __dict__
    except TypeError:
        return {}


class TrendStructureBot:
    """Candle Momentum & Market Structure Bot."""

    def __init__(self, logger=None, clock_provider=None) -> None:
        self.cfg: Dict[str, Any] = dict(DEFAULTS)
        self.logger = logger
        self.clock_provider = clock_provider      # engine's server clock, or None
        self.symbol: str = ""
        self.symbol_ok: bool = False
        self.symbol_substituted: str = ""
        self.available_symbols_list: List[str] = []
        self._symbols_ts: float = 0.0
        self.enabled: bool = False
        self.last_action: str = ""
        self.last_error: str = ""
        self.stats: Dict[str, Any] = {"opened": 0, "closed": 0, "wins": 0, "losses": 0, "realized": 0.0}
        self._known_positions: Dict[int, Dict[str, Any]] = {}
        self._self_closed: set = set()
        self._symbol_changed: bool = False
        self._symbol_explicit: bool = False
        self._resolve_ts: float = 0.0
        self._last_order_ts: float = 0.0
        self._last_close_ts: float = 0.0
        self._order_in_progress: bool = False
        self._last_candle_time: int = 0
        self._last_candle_dir: str = ""  # "UP" or "DOWN"
        self._last_structure_action: str = ""
        self._structure_cache: Dict[str, Any] = {}
        self._structure_cache_ts: float = 0.0
        # Watch Pressure Trend System (source of truth: market watch dials)
        self._last_trend_check_ts: float = 0.0
        self._current_trend: str = "NEUTRAL"
        self._trend_score: int = 0
        self._trend_details: str = "Awaiting 2.5s watch check (NEUTRAL)"
        self._trend_history: List[Dict[str, Any]] = []
        # Live watch pressure: "BUYER" | "SELLER" | "NEUTRAL"
        self._watch_pressure: str = "NEUTRAL"
        self._watch_buyer_pct: float = 50.0
        self._watch_seller_pct: float = 50.0
        self._watch_ts: float = 0.0
        self._watch_provider = None  # set by the engine: fn(symbol) -> dict

    def _log(self, kind: str, message: str) -> None:
        self.last_action = message
        if self.logger:
            try:
                self.logger(kind, f"[Bot2] {message}")
            except Exception:
                pass

    def _server_now(self) -> float:
        """MT5 broker time when available - bar timestamps are broker epochs."""
        if self.clock_provider:
            try:
                return float(self.clock_provider())
            except Exception:
                pass
        return time.time()

    # ------------------------------------------------- watch switch & reverse
    def _watch_on(self) -> bool:
        """Watch switch: True = the dials decide the direction, False = candle flow.

        `trend_mode` is the older name of exactly this switch, so either one being
        off means the Watch is off (and update_config keeps them in sync).
        """
        return bool(self.cfg.get("watch_enabled", True)) and bool(self.cfg.get("trend_mode", True))

    def _watch_min_pct(self) -> float:
        """The Watch % from the UI field - whatever the user typed is what gates trades."""
        val = self.cfg.get("watch_min_pct")
        if val in (None, ""):
            val = self.cfg.get("watch_min_threshold_pct")
        try:
            return min(99.0, max(1.0, float(val)))
        except (TypeError, ValueError):
            return 60.0

    def _apply_reverse(self, side: str) -> str:
        """Reverse switch: buys become sells and sells become buys."""
        s = str(side or "").lower()
        if s not in ("buy", "sell"):
            return s
        if bool(self.cfg.get("reverse")):
            return "sell" if s == "buy" else "buy"
        return s

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        old_symbol = str(self.cfg.get("symbol") or "").strip()
        for k, v in (patch or {}).items():
            if k in DEFAULTS:
                self.cfg[k] = v
        if not str(self.cfg.get("symbol") or "").strip():
            self.cfg["symbol"] = "BTCUSDTm"
        new_symbol = str(self.cfg.get("symbol") or "").strip()
        if new_symbol and old_symbol and old_symbol.upper() != new_symbol.upper():
            self._symbol_changed = True
            self._symbol_explicit = True
            self.symbol = ""
            self.symbol_ok = False
            self._resolve_ts = 0.0
            self._structure_cache.clear()
            self._last_trend_check_ts = 0.0
            self._trend_history.clear()

        # Sanitize numeric fields
        self.cfg["lot"] = self._round_lot(float(self.cfg.get("lot") or 0.01))
        self.cfg["max_trades"] = max(1, int(self.cfg.get("max_trades") or 1))
        self.cfg["tp"] = max(0.01, round(float(self.cfg.get("tp") or 0.10), 2))
        self.cfg["sl"] = max(0.05, round(float(self.cfg.get("sl") or 2.00), 2))
        self.cfg["trend_check_interval"] = max(1.0, float(self.cfg.get("trend_check_interval") or 2.5))
        # lock trend_check_interval to the strict 2.5s trade timer unless set deliberately
        if "trend_check_interval" not in (patch or {}):
            self.cfg["trend_check_interval"] = 2.5
        # 1-minute candle time-window blocks (values straight from the UI fields)
        try:
            self.cfg["block_start_sec"] = max(0.0, float(self.cfg.get("block_start_sec") or 0.0))
            self.cfg["block_end_sec"] = max(0.0, float(self.cfg.get("block_end_sec") or 0.0))
            self.cfg["block_mid_from"] = max(0.0, float(self.cfg.get("block_mid_from") or 0.0))
            self.cfg["block_mid_to"] = max(0.0, float(self.cfg.get("block_mid_to") or 0.0))
        except (TypeError, ValueError):
            pass
        tf = str(self.cfg.get("timeframe") or "D1").upper()
        self.cfg["timeframe"] = tf if tf in ("D1", "W1") else "D1"
        # Watch switch & Watch % : the UI keys (watch_enabled / watch_min_pct) and
        # the older config keys (trend_mode / watch_min_threshold_pct) are two names
        # for the same settings - they must never disagree.
        patch = patch or {}
        if "watch_enabled" in patch:
            self.cfg["watch_enabled"] = bool(self.cfg.get("watch_enabled"))
        elif "trend_mode" in patch:
            self.cfg["watch_enabled"] = bool(self.cfg.get("trend_mode"))
        self.cfg["watch_enabled"] = bool(self.cfg.get("watch_enabled", True))
        self.cfg["trend_mode"] = self.cfg["watch_enabled"]
        self.cfg["watch_min_pct"] = self._watch_min_pct()
        self.cfg["watch_min_threshold_pct"] = self.cfg["watch_min_pct"]
        self.cfg["reverse"] = bool(self.cfg.get("reverse", False))
        return dict(self.cfg)

    # ------------------------------------------------------------ symbol info
    def _round_lot(self, lot: float) -> float:
        step = self.volume_step()
        if step <= 0:
            step = 0.01
        lot = round(round(lot / step) * step, 8)
        return max(self.volume_min(), min(lot, self.volume_max()))

    def volume_min(self, symbol: str = "") -> float:
        sym = symbol or self.symbol
        if mt5 is None or not sym:
            return 0.01
        try:
            info = mt5.symbol_info(sym)
            return float(info.volume_min) if info else 0.01
        except Exception:
            return 0.01

    def volume_max(self, symbol: str = "") -> float:
        sym = symbol or self.symbol
        if mt5 is None or not sym:
            return 100.0
        try:
            info = mt5.symbol_info(sym)
            return float(info.volume_max) if info else 100.0
        except Exception:
            return 100.0

    def volume_step(self, symbol: str = "") -> float:
        sym = symbol or self.symbol
        if mt5 is None or not sym:
            return 0.01
        try:
            info = mt5.symbol_info(sym)
            return float(info.volume_step) if info else 0.01
        except Exception:
            return 0.01

    def _digits(self, sym: str) -> int:
        if mt5 is None or not sym:
            return 2
        try:
            return int(mt5.symbol_info(sym).digits)
        except Exception:
            return 2

    def _point(self, sym: str) -> float:
        if mt5 is None or not sym:
            return 0.01
        try:
            return float(mt5.symbol_info(sym).point) or 0.01
        except Exception:
            return 0.01

    def _min_stop_distance(self, sym: str) -> float:
        if mt5 is None or not sym:
            return 0.0
        try:
            info = mt5.symbol_info(sym)
            stops = max(int(getattr(info, "trade_stops_level", 0) or 0),
                        int(getattr(info, "trade_freeze_level", 0) or 0))
            return stops * self._point(sym)
        except Exception:
            return 0.0

    def money_per_price(self, volume: float, sym: str = "") -> float:
        sym = sym or self.symbol
        if mt5 is None or not sym:
            return 0.0
        try:
            info = mt5.symbol_info(sym)
            tick_value = float(info.trade_tick_value)
            tick_size = float(info.trade_tick_size) or self._point(sym)
            if tick_value > 0 and tick_size > 0:
                return (tick_value / tick_size) * float(volume)
            contract = float(info.trade_contract_size)
            return contract * float(volume)
        except Exception:
            return 0.0

    def _filling_mode(self, sym: str) -> int:
        if mt5 is None or not sym:
            return 0
        try:
            info = mt5.symbol_info(sym)
            mode = int(info.filling_mode)
        except Exception:
            return 0
        if mode & 1:
            return 0  # ORDER_FILLING_FOK
        if mode & 2:
            return 1  # ORDER_FILLING_IOC
        return 2      # ORDER_FILLING_RETURN

    def available_symbols(self, force: bool = False) -> List[str]:
        if mt5 is None:
            return []
        now = time.time()
        if self.available_symbols_list and not force and (now - self._symbols_ts) < 60:
            return list(self.available_symbols_list)
        out: List[str] = []
        try:
            allsym = mt5.symbols_get()
        except Exception:
            allsym = None
        for s in allsym or []:
            name = str(getattr(s, "name", "") or "")
            up = name.upper()
            if (up in PREFERRED_SYMBOLS or any(b in up for b in (
                "BTCUSD", "BTCUSDT", "XAUUSD", "GOLD",
                "DXY", "USDX", "USOIL", "XTIUSD", "CL",
                "XAUGBP", "USDJPY", "EURUSD", "USTEC", "NAS100", "US100"
            ))) and not ("XAG" in up or "ETH" in up):
                out.append(name)

        def rank(n: str) -> tuple:
            up = n.upper()
            if up in PREFERRED_SYMBOLS:
                return (PREFERRED_SYMBOLS.index(up), 0, n)
            crypto = 0 if "BTC" in up else 1
            return (len(PREFERRED_SYMBOLS), crypto, n)

        out.sort(key=rank)
        self.available_symbols_list = out[:25]
        self._symbols_ts = now
        return list(self.available_symbols_list)

    def resolve_symbol(self, force: bool = False) -> str:
        if mt5 is None:
            return ""
        now = time.time()
        if not force and (now - self._resolve_ts) < 30 and self.symbol:
            return self.symbol
        wanted = str(self.cfg.get("symbol") or "BTCUSDTm").strip() or "BTCUSDTm"
        try:
            info = mt5.symbol_info(wanted)
        except Exception:
            info = None
        if info is None:
            cands = [
                wanted + "m", wanted.rstrip("m"), "BTCUSDTm", "BTCUSDm", "XAUUSDm",
                "DXY", "USOILm", "XAUGBPm", "USDJPYm", "EURUSDm", "USTECm"
            ]
            for c in cands:
                try:
                    if mt5.symbol_info(c) is not None:
                        wanted = c
                        info = mt5.symbol_info(c)
                        break
                except Exception:
                    pass
        if info is None:
            self.symbol = ""
            self.symbol_ok = False
            self.last_error = f"{wanted} is not available on this account"
            return ""
        if not info.visible:
            try:
                mt5.symbol_select(wanted, True)
            except Exception:
                pass
        self.symbol = wanted
        self.symbol_ok = True
        self._resolve_ts = now
        self.last_error = ""
        return wanted

    # ------------------------------------------------ Market Structure Levels
    def compute_structure(self, sym: str = "") -> Dict[str, Any]:
        """Calculates Higher High (HH), Lower High (LH), Higher Low (HL), Lower Low (LL)."""
        sym = sym or self.symbol
        if mt5 is None or not sym:
            return {"d1": {}, "w1": {}, "active_level": "", "active_action": ""}
        now = time.time()
        if self._structure_cache and (now - self._structure_cache_ts) < 15:
            return dict(self._structure_cache)

        def calc_levels(tf, count):
            rates = mt5.copy_rates_from_pos(sym, tf, 0, count)
            if rates is None or len(rates) == 0:
                return {}
            highs = [float(r["high"]) for r in rates]
            lows = [float(r["low"]) for r in rates]
            sh = sorted(set(highs), reverse=True)
            sl = sorted(set(lows))
            hh = sh[0]
            lh = sh[1] if len(sh) > 1 else hh
            ll = sl[0]
            hl = sl[1] if len(sl) > 1 else ll
            return {"hh": hh, "lh": lh, "hl": hl, "ll": ll}

        try:
            d1_levels = calc_levels(mt5.TIMEFRAME_D1, 7)
            w1_levels = calc_levels(mt5.TIMEFRAME_W1, 5)
        except Exception:
            d1_levels = {}
            w1_levels = {}

        data = {
            "d1": d1_levels,
            "w1": w1_levels,
            "timeframe": self.cfg.get("timeframe", "D1"),
        }
        self._structure_cache = data
        self._structure_cache_ts = now
        return data

    # --------------------------------------- Watch Pressure Trend Check System
    def set_watch_provider(self, provider) -> None:
        """Injects the live watch-dial source: fn(symbol) -> {buyer_pct, seller_pct, dominant, ...}."""
        self._watch_provider = provider

    def _read_watch_pressure(self, sym: str) -> str:
        """Reads buyer/seller pressure for sym from the Market Pressure Watch dials.

        Returns "BUYER", "SELLER" or "NEUTRAL". Falls back to NEUTRAL when the
        dial has no fresh data - NEUTRAL never trades, so a broken feed is safe.
        """
        if self._watch_provider is None:
            return "NEUTRAL"
        try:
            w = self._watch_provider(sym) or {}
        except Exception:
            return "NEUTRAL"
        if not w:
            return "NEUTRAL"
        buyer = float(w.get("buyer_pct") or 50.0)
        seller = float(w.get("seller_pct") or 50.0)
        dominant = str(w.get("dominant") or "NEUTRAL").upper()
        ts = float(w.get("updated_at") or 0.0)
        self._watch_buyer_pct = buyer
        self._watch_seller_pct = seller
        if ts > 0:
            self._watch_ts = ts
        # stale dial data (older than 30s) is treated as NEUTRAL: no trading on stale facts
        if ts > 0 and (time.time() - ts) > 30.0:
            self._watch_pressure = "NEUTRAL"
            return self._watch_pressure

        # Watch threshold rule (the value comes from the Watch % field - nothing
        # here is fixed): the side whose pressure is >= the entered % is dominant.
        min_pct = self._watch_min_pct()
        if buyer >= min_pct:
            self._watch_pressure = "BUYER"
        elif seller >= min_pct:
            self._watch_pressure = "SELLER"
        else:
            self._watch_pressure = "NEUTRAL"
        return self._watch_pressure

    def _check_watch_threshold(self, side: str, sym: str = "") -> tuple[bool, str]:
        """Checks the watch pressure of the requested side against the Watch % field.

        Rule (while the Watch switch is ON):
        1. Trade neyar age, Watch market check korbe: Buy ba Sell joto % deya hobe
           (Watch field) seto % ba tar beshi hole buy ba sell nibe.
        2. Field er kom hole trade nibe na.
        Watch switch OFF -> the dial is not consulted at all (candle flow decides).
        """
        if not self._watch_on():
            return (True, "watch off - dial not consulted")
        if self._watch_provider is None:
            return (True, "no_watch_provider")
        sym = sym or self.symbol
        if not sym:
            return (False, "no symbol")
        try:
            w = self._watch_provider(sym) or {}
        except Exception as exc:
            return (False, f"watch provider error: {exc}")
        if not w:
            return (False, f"no watch data for {sym} - strictly blocked")

        buyer = float(w.get("buyer_pct") or 50.0)
        seller = float(w.get("seller_pct") or 50.0)
        self._watch_buyer_pct = buyer
        self._watch_seller_pct = seller
        # stale dial data (older than 30s) blocks the trade, same rule as the
        # direction gate: no trading on stale facts
        ts = float(w.get("updated_at") or 0.0)
        if ts > 0 and (time.time() - ts) > 30.0:
            return (False, f"stale watch data ({time.time() - ts:.0f}s old)")

        min_pct = self._watch_min_pct()
        is_buy = str(side).lower() == "buy"
        if is_buy:
            if buyer >= min_pct:
                return (True, f"Watch BUY {buyer:.1f}% >= {min_pct:.0f}% (allowed)")
            else:
                return (False, f"Watch BUY {buyer:.1f}% < {min_pct:.0f}% (blocked: below Watch %)")
        else:
            if seller >= min_pct:
                return (True, f"Watch SELL {seller:.1f}% >= {min_pct:.0f}% (allowed)")
            else:
                return (False, f"Watch SELL {seller:.1f}% < {min_pct:.0f}% (blocked: below Watch %)")

    def check_trend(self, force: bool = False, sym: str = "") -> str:
        """Checks the watch-dial pressure trend: UP (= BUYER) or DOWN (= SELLER).

        Runs periodically every 2.5 seconds (or immediately if force=True).
        The direction comes straight from the Market Pressure Watch dials -
        NEUTRAL pressure (<= 60%) means 'no direction', which blocks every trade.
        """
        sym = sym or self.symbol
        if not sym:
            return self._current_trend

        now = time.time()
        interval = float(self.cfg.get("trend_check_interval") or 2.5)

        # Skip if within 2.5s cycle unless forced or never checked
        if not force and self._last_trend_check_ts > 0 and (now - self._last_trend_check_ts) < interval:
            return self._current_trend

        if not self._watch_on():
            # Watch OFF: the dials decide nothing. The direction (and the pill on
            # the dashboard) follows the running candle instead: price above the
            # candle open = UP, below = DOWN, otherwise NEUTRAL.
            return self._refresh_candle_trend(sym, now)

        pressure = self._read_watch_pressure(sym)
        min_pct = self._watch_min_pct()

        # Watch pressure -> trend: BUYER = UP, SELLER = DOWN (both at/above the
        # Watch % field), NEUTRAL = no trade (below the field value)
        if pressure == "BUYER":
            new_trend, score, details = "UP", 1, f"Watch: BUYER {self._watch_buyer_pct:.1f}% (>= {min_pct:.0f}%) vs SELLER {self._watch_seller_pct:.1f}%"
        elif pressure == "SELLER":
            new_trend, score, details = "DOWN", -1, f"Watch: SELLER {self._watch_seller_pct:.1f}% (>= {min_pct:.0f}%) vs BUYER {self._watch_buyer_pct:.1f}%"
        else:
            new_trend, score, details = "NEUTRAL", 0, f"Watch: NEUTRAL (BUY {self._watch_buyer_pct:.1f}% / SELL {self._watch_seller_pct:.1f}% < {min_pct:.0f}%) - no trade"

        old_trend = self._current_trend
        self._current_trend = new_trend
        self._trend_score = score
        self._trend_details = details
        self._last_trend_check_ts = now

        # Add to history
        price = 0.0
        if mt5 is not None:
            try:
                tick = mt5.symbol_info_tick(sym)
                price = float(tick.bid) if (tick and getattr(tick, "bid", None)) else 0.0
            except Exception:
                price = 0.0

        self._trend_history.append({
            "time": now,
            "trend": new_trend,
            "score": score,
            "price": price,
            "details": details,
        })
        if len(self._trend_history) > 10:
            self._trend_history.pop(0)

        if new_trend != old_trend:
            self._log("info", f"2.5s Trend Update: {new_trend} (Score: {score:+d} | {details})")
        elif now - getattr(self, "_trend_confirmed_last_log", 0) >= 300:
            self._trend_confirmed_last_log = now
            self._log("info", f"2.5s Trend Confirmed: {new_trend} (Score: {score:+d})")

        return new_trend

    def _candle_flow_trend(self, sym: str) -> tuple[str, int, str]:
        """Direction of the running 1-minute candle (used while Watch is OFF).

        Follow mode: the side the candle is actually moving to is the side traded
        - candle up -> BUY, candle down -> SELL. A flat candle never trades.
        """
        if mt5 is None or not sym:
            return ("NEUTRAL", 0, "Candle flow: no terminal data")
        try:
            tick = mt5.symbol_info_tick(sym)
            rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 1)
        except Exception as exc:
            return ("NEUTRAL", 0, f"Candle flow error: {exc}")
        if not tick or rates is None or len(rates) == 0:
            return ("NEUTRAL", 0, "Candle flow: waiting for the candle")
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        price = (ask + bid) / 2.0 if (ask and bid) else (bid or ask)
        try:
            bar_open = float(rates[-1]["open"])
        except (KeyError, TypeError, ValueError, IndexError):
            return ("NEUTRAL", 0, "Candle flow: unreadable candle")
        if not price or not bar_open:
            return ("NEUTRAL", 0, "Candle flow: no price")
        threshold = max(abs(ask - bid) * 0.5, self._point(sym))
        diff = price - bar_open
        if diff > threshold:
            return ("UP", 1, f"Candle flow UP (price {price:g} above candle open {bar_open:g})")
        if diff < -threshold:
            return ("DOWN", -1, f"Candle flow DOWN (price {price:g} below candle open {bar_open:g})")
        return ("NEUTRAL", 0, f"Candle flow flat (price {price:g} around candle open {bar_open:g})")

    def _refresh_candle_trend(self, sym: str, now: float) -> str:
        """Stores the candle-flow trend with the same history/logging as the dial check."""
        new_trend, score, details = self._candle_flow_trend(sym)
        old_trend = self._current_trend
        self._current_trend = new_trend
        self._trend_score = score
        self._trend_details = details
        self._last_trend_check_ts = now
        self._trend_history.append({"time": now, "trend": new_trend, "score": score,
                                    "price": 0.0, "details": details})
        if len(self._trend_history) > 10:
            self._trend_history.pop(0)
        if new_trend != old_trend:
            self._log("info", f"2.5s Candle Flow: {new_trend} ({details})")
        return new_trend

    def _calculate_market_trend(self, sym: str) -> tuple[str, int, str]:
        """Legacy candle/H1/D1 trend analysis (kept for diagnostics / fallback UI).

        The trade direction comes from the watch dials via check_trend(); this
        method is no longer called by the trading path.

        Returns (trend: 'UP' | 'DOWN', score: int, details: str).
        """
        if mt5 is None or not sym:
            return ("UP", 0, "Default: UP")

        try:
            score = 0
            reasons: List[str] = []

            # 1. Fetch current price
            tick = mt5.symbol_info_tick(sym)
            cur = float(tick.bid) if (tick and getattr(tick, "bid", None)) else 0.0

            # 2. D1 (Day) Open bias
            rates_d1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 1)
            d1_open = 0.0
            if rates_d1 is not None and len(rates_d1) > 0:
                d1_open = float(rates_d1[0]["open"])
                if cur > 0 and d1_open > 0:
                    if cur > d1_open:
                        score += 1
                        reasons.append("Price > D1 Open")
                    elif cur < d1_open:
                        score -= 1
                        reasons.append("Price < D1 Open")

            # 3. H1 (1-Hour) Moving Average
            rates_h1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 5)
            if rates_h1 is not None and len(rates_h1) > 0:
                h1_avg = sum(float(r["close"]) for r in rates_h1) / len(rates_h1)
                if cur > 0 and h1_avg > 0:
                    if cur > h1_avg:
                        score += 1
                        reasons.append("Price > H1 Avg")
                    elif cur < h1_avg:
                        score -= 1
                        reasons.append("Price < H1 Avg")

            # 4. M1 (1-Minute / 60s) Candle Analysis
            # Since check is every 60s, analyzing 1-minute bars gives direct 60s price momentum!
            rates_m1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 10)
            if rates_m1 is not None and len(rates_m1) >= 2:
                # Previous completed 1m candle (60-second bar)
                last_closed = rates_m1[-2]
                c_open = float(last_closed["open"])
                c_close = float(last_closed["close"])
                if c_close > c_open:
                    score += 1
                    reasons.append("M1 Candle Bullish")
                elif c_close < c_open:
                    score -= 1
                    reasons.append("M1 Candle Bearish")

                # Short-term 5-period moving average on M1
                recent_closes = [float(r["close"]) for r in rates_m1[-5:]]
                m1_avg = sum(recent_closes) / len(recent_closes)
                if cur > 0 and m1_avg > 0:
                    if cur > m1_avg:
                        score += 1
                        reasons.append("Price > M1 MA")
                    elif cur < m1_avg:
                        score -= 1
                        reasons.append("Price < M1 MA")

                # Price structure: compare last 3 closed M1 highs and lows
                if len(rates_m1) >= 4:
                    h1_bar = float(rates_m1[-2]["high"])
                    h2_bar = float(rates_m1[-3]["high"])
                    l1_bar = float(rates_m1[-2]["low"])
                    l2_bar = float(rates_m1[-3]["low"])
                    if h1_bar > h2_bar and l1_bar >= l2_bar:
                        score += 1
                        reasons.append("M1 Higher Highs")
                    elif l1_bar < l2_bar and h1_bar <= h2_bar:
                        score -= 1
                        reasons.append("M1 Lower Lows")

            # Determine final trend
            if score > 0:
                trend = "UP"
            elif score < 0:
                trend = "DOWN"
            else:
                if cur > 0 and d1_open > 0:
                    trend = "UP" if cur >= d1_open else "DOWN"
                else:
                    trend = self._current_trend or "UP"
                reasons.append("Neutral Tie-break")

            details_str = " · ".join(reasons) if reasons else ("Bullish Bias" if trend == "UP" else "Bearish Bias")
            return (trend, score, details_str)

        except Exception as exc:
            return ("UP", 0, f"Trend fallback: {exc}")

    def compute_market_trend(self, sym: str = "") -> str:
        """Watch-dial trend for trading decisions: UP (BUYER), DOWN (SELLER) or NEUTRAL."""
        return self.check_trend(force=False, sym=sym)

    # ------------------------------------------------------------- positions
    def _all_positions(self, any_symbol: bool = False) -> List[Dict[str, Any]]:
        if mt5 is None:
            return []
        try:
            raw = mt5.positions_get(symbol=None if any_symbol else self.symbol) or []
        except Exception:
            raw = []
        magic = int(self.cfg.get("magic") or 777002)
        out: List[Dict[str, Any]] = []
        for p in raw:
            d = _as_dict(p)
            if int(d.get("magic") or 0) != magic:
                continue
            t = d.get("type")
            d["side"] = "buy" if t == 0 or str(t).lower() == "buy" else "sell"
            profit = float(d.get("profit") or 0.0)
            swap = float(d.get("swap") or 0.0)
            d["net"] = round(profit + swap, 2)
            d["ticket"] = int(d.get("ticket") or 0)
            d["volume"] = float(d.get("volume") or 0.0)
            d["price_open"] = float(d.get("price_open") or 0.0)
            out.append(d)
        return out

    def _next_side(self) -> str:
        """Which side this bot would take right now (Reverse already applied)."""
        side = ""
        if self._watch_on() and self._current_trend in ("UP", "DOWN"):
            side = "buy" if self._current_trend == "UP" else "sell"
        elif self._last_candle_dir == "UP":
            side = "buy"
        elif self._last_candle_dir == "DOWN":
            side = "sell"
        return self._apply_reverse(side).upper() if side else ""

    # ------------------------------------------------------------------ tick
    def tick(self) -> None:
        if mt5 is None or not self.symbol:
            if not self.resolve_symbol():
                return

        # 60-Second periodic trend check
        self.check_trend(force=False)

        if self._symbol_changed:
            self._symbol_changed = False
            self.close_all("market switched")
            if not bool(self.cfg.get("running")):
                return

        positions = self._all_positions()

        # 1) Manage open positions: Check TP ($) and SL ($)
        self._manage_positions(positions)

        # 2) Track closed/opened positions for stats
        self._track(positions)

        running = bool(self.cfg.get("running"))
        if not running:
            self.enabled = bool(positions)
            return

        self.enabled = True

        # 3) Check if we can enter new trades
        self._check_and_trade(positions)

    def _manage_positions(self, positions: List[Dict[str, Any]]) -> None:
        """Enforces dollar-based TP and SL actively on every tick."""
        tp_target = float(self.cfg.get("tp") or 0.10)
        sl_target = float(self.cfg.get("sl") or 2.00)

        for p in list(positions):
            net = float(p.get("net") or 0.0)
            ticket = p.get("ticket")

            if net >= tp_target:
                # Take profit reached!
                self._self_closed.add(ticket)
                self._close_position(p, f"TP reached (+${net:.2f} >= ${tp_target:.2f})")
                self._log("trade", f"Position #{ticket} hit TP target: +${net:.2f}")
            elif net <= -sl_target:
                # Stop loss reached!
                self._self_closed.add(ticket)
                self._close_position(p, f"SL hit (-${abs(net):.2f} >= ${sl_target:.2f})")
                self._log("trade", f"Position #{ticket} hit SL target: ${net:.2f}")

    def _check_and_trade(self, positions: List[Dict[str, Any]]) -> None:
        """Evaluates Structure Levels (Step 5) & Trend Execution.
        Strictly enforces:
        1. 'akshaty trade nibe na': Never open simultaneous trades.
           Only 1 trade can be active at a time.
        2. '5 second por por trade nibe bot':
           At least 5.0 seconds between consecutive trades,
           and at least 5.0 seconds after previous trade closes.
        """
        # `trend_mode` is the legacy name of the Watch switch - one setting, two names.
        trend_mode = self._watch_on()
        max_trades = max(1, int(self.cfg.get("max_trades") or 1))
        current_running = len(positions)

        # 1. Limit check: Connected directly to max_trades setting (up to max_trades concurrent trades)
        if current_running >= max_trades:
            return

        # 2. Concurrency guard: if an order is in-flight, return immediately
        if getattr(self, "_order_in_progress", False):
            return

        # 3. 2.5-Second Interval rule ('2.5 second a check korbe, check kore trade nibe bot')
        now = time.time()
        interval = float(self.cfg.get("trend_check_interval") or 2.5)
        cd = interval if trend_mode else max(2.5, float(self.cfg.get("cooldown_sec") or 2.5))

        # At least 2.5 seconds since last trade was opened (ensures sequential 1 trade per 2.5s)
        if self._last_order_ts > 0 and (now - self._last_order_ts) < cd:
            return

        # Only enforce close cooldown in non-trend mode
        if not trend_mode and self._last_close_ts > 0 and (now - self._last_close_ts) < cd:
            return

        sym = self.symbol

        # 1-Minute candle time-window gate: no trade in the first N seconds, the
        # last N seconds or the middle window of the running candle. Blocks every
        # entry type (structure + trend/candle); values come from the UI fields.
        win = self._candle_window_block(sym)
        if win:
            self._log("info", f"Candle time-window block - no trade ({win})")
            return

        tick = mt5.symbol_info_tick(sym)
        if not tick or not tick.ask or not tick.bid:
            return

        current_price = (float(tick.ask) + float(tick.bid)) / 2.0
        digits = self._digits(sym)
        spread = round(float(tick.ask) - float(tick.bid), digits)

        # Step 5: Check Market Structure Levels (Day / Weekend)
        struct = self.compute_structure(sym)
        tf = self.cfg.get("timeframe", "D1")
        levels = struct.get(tf.lower()) or struct.get("d1") or {}

        hh = levels.get("hh")
        lh = levels.get("lh")
        hl = levels.get("hl")
        ll = levels.get("ll")

        # Dynamic tolerance zone for structural touches (0.05% of price or 3x spread)
        tolerance = max(spread * 3.0, current_price * 0.0005)
        market_trend = self.compute_market_trend(sym)

        # Watch switch ON: the dial decides the direction and NEUTRAL pressure
        # (below the Watch % field) blocks every trade. Watch switch OFF: the dial
        # is ignored completely and the candle flow below decides.
        min_pct = self._watch_min_pct()
        watch_on = self._watch_on()
        if watch_on and self._watch_provider is not None:
            if market_trend == "NEUTRAL":
                self._log("info", f"Watch pressure NEUTRAL (< {min_pct:.0f}%) - no trade ({self._trend_details})")
                return
            watch_dir_allows_buy = (market_trend == "UP")
            watch_dir_allows_sell = (market_trend == "DOWN")
        else:
            watch_dir_allows_buy = True
            watch_dir_allows_sell = True

        if hh is not None and lh is not None and hl is not None and ll is not None:
            # * Lower Low price -> BUY (only with BUYER pressure in trend mode)
            if abs(current_price - ll) <= tolerance or current_price < ll:
                if watch_dir_allows_buy:
                    if self._last_structure_action != f"LL_{int(now // 60)}":
                        self._last_structure_action = f"LL_{int(now // 60)}"
                        self._log("info", f"Structure Touch: Lower Low ({ll}) -> Taking BUY [Watch: BUYER {self._watch_buyer_pct:.0f}%]")
                        if self._open_market_trade("buy", "bot2_struct_ll_buy"):
                            self._last_order_ts = now
                            return

            # * Higher High price -> SELL (only with SELLER pressure in trend mode)
            elif abs(current_price - hh) <= tolerance or current_price > hh:
                if watch_dir_allows_sell:
                    if self._last_structure_action != f"HH_{int(now // 60)}":
                        self._last_structure_action = f"HH_{int(now // 60)}"
                        self._log("info", f"Structure Touch: Higher High ({hh}) -> Taking SELL [Watch: SELLER {self._watch_seller_pct:.0f}%]")
                        if self._open_market_trade("sell", "bot2_struct_hh_sell"):
                            self._last_order_ts = now
                            return

            # * Lower High price -> SELL (only with SELLER pressure in trend mode)
            elif abs(current_price - lh) <= tolerance:
                if watch_dir_allows_sell:
                    if self._last_structure_action != f"LH_{int(now // 60)}":
                        self._last_structure_action = f"LH_{int(now // 60)}"
                        self._log("info", f"Structure Touch: Lower High ({lh}) -> Taking SELL [Watch: SELLER {self._watch_seller_pct:.0f}%]")
                        if self._open_market_trade("sell", "bot2_struct_lh_sell"):
                            self._last_order_ts = now
                            return

            # * Higher Low price -> BUY (only with BUYER pressure in trend mode)
            elif abs(current_price - hl) <= tolerance:
                if watch_dir_allows_buy:
                    if self._last_structure_action != f"HL_{int(now // 60)}":
                        self._last_structure_action = f"HL_{int(now // 60)}"
                        self._log("info", f"Structure Touch: Higher Low ({hl}) -> Taking BUY [Watch: BUYER {self._watch_buyer_pct:.0f}%]")
                        if self._open_market_trade("buy", "bot2_struct_hl_buy"):
                            self._last_order_ts = now
                            return

        # Step 4: Candle Momentum
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 2)
        if rates is None or len(rates) == 0:
            return

        current_bar = rates[-1]
        bar_open = float(current_bar["open"])
        bar_time = int(current_bar["time"])
        price_diff = current_price - bar_open

        # Minimum movement threshold to register clear candle momentum (at least 1 point/spread)
        threshold = max(spread * 0.5, self._point(sym))

        candle_dir = "FLAT"
        if price_diff > threshold:
            candle_dir = "UP"
        elif price_diff < -threshold:
            candle_dir = "DOWN"

        self._last_candle_dir = candle_dir

        if trend_mode:
            # WATCH ON - 2.5-SECOND TIMER WATCH-PRESSURE EXECUTION:
            # 1. NEUTRAL watch pressure (below the Watch % field) -> strictly no trade
            # 2. BUYER pressure >= the Watch % -> BUY trade
            # 3. SELLER pressure >= the Watch % -> SELL trade
            # 4. one trade per 2.5s timer tick, up to max_trades concurrent trades
            if market_trend == "UP":
                if self._open_market_trade("buy", "bot2_watch_buyer_buy"):
                    self._last_order_ts = now
                    self._last_candle_time = bar_time
                else:
                    self._last_order_ts = now  # wait a full cooldown before retrying
            elif market_trend == "DOWN":
                if self._open_market_trade("sell", "bot2_watch_seller_sell"):
                    self._last_order_ts = now
                    self._last_candle_time = bar_time
                else:
                    self._last_order_ts = now  # wait a full cooldown before retrying
            # NEUTRAL already returned above - nothing to do
        else:
            # WATCH OFF - follow the candle: the side the candle is actually moving
            # to is the side traded (candle UP -> BUY, candle DOWN -> SELL), one
            # trade per 2.5s timer tick, up to max_trades concurrent trades.
            if candle_dir == "UP":
                if self._open_market_trade("buy", "bot2_candle_up_buy"):
                    self._last_order_ts = now
                    self._last_candle_time = bar_time
            elif candle_dir == "DOWN":
                if self._open_market_trade("sell", "bot2_candle_down_sell"):
                    self._last_order_ts = now
                    self._last_candle_time = bar_time

    def _candle_window_block(self, sym: str) -> str:
        """Returns a blocking reason string when the running 1-minute candle is
        inside a no-trade time window, or "" when trading is allowed.

        The windows come purely from the UI fields (block_start_sec,
        block_end_sec, block_mid_from, block_mid_to) - nothing is hardcoded.
        A failed/missing rates fetch never blocks trading by itself, and the
        gate only applies while the bar looks like the running minute candle
        (0 <= elapsed < 60) - a stale or future-dated bar never blocks trading.
        """
        if mt5 is None or not sym:
            return ""
        try:
            rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 1)
        except Exception:
            return ""
        if rates is None or len(rates) == 0:
            return ""
        try:
            bar_time = int(rates[-1]["time"])
        except (KeyError, TypeError, ValueError):
            return ""
        if bar_time <= 0:
            return ""
        # bar timestamps are MT5 *server* wall-clock epochs; comparing them
        # against the local clock (time.time) shifts the whole window by the
        # broker offset and silently kills the gate on non-GMT+0 servers
        elapsed = self._server_now() - bar_time
        if elapsed < 0.0 or elapsed >= 60.0:
            # not the running candle (stale/future feed) - window gate does not apply
            return ""
        first = float(self.cfg.get("block_start_sec") or 0.0)
        last = float(self.cfg.get("block_end_sec") or 0.0)
        mid_from = float(self.cfg.get("block_mid_from") or 0.0)
        mid_to = float(self.cfg.get("block_mid_to") or 0.0)
        if first > 0 and elapsed < first:
            return f"first {first:.0f}s of candle (elapsed {elapsed:.1f}s)"
        if last > 0 and elapsed > (60.0 - last):
            return f"last {last:.0f}s of candle (elapsed {elapsed:.1f}s)"
        if mid_to > mid_from and mid_from <= elapsed <= mid_to:
            return f"middle window {mid_from:.0f}s-{mid_to:.0f}s (elapsed {elapsed:.1f}s)"
        return ""

    def _open_market_trade(self, side: str, comment: str) -> bool:
        """Places a market BUY or SELL trade with TP & SL.

        The Watch gate checks the side the bot decided on (the dials confirm the
        direction), then the Reverse switch flips the order that really reaches
        the broker.
        """
        if getattr(self, "_order_in_progress", False):
            return False

        wanted = str(side or "").lower()

        # Rule: trade neyar age, Watch market check korbe (Watch field er % ba tar
        # beshi hole trade). The gate validates the decided side - Reverse then
        # flips the order that is placed, so the button always works.
        allowed, reason = self._check_watch_threshold(wanted, self.symbol)
        if not allowed:
            self._log("info", f"Watch Gate: {reason} [{comment}]")
            return False

        side = self._apply_reverse(wanted)
        if side != wanted:
            comment = f"{comment}_rev"

        self._order_in_progress = True
        try:
            sym = self.symbol
            lot = self._round_lot(float(self.cfg.get("lot") or 0.01))
            digits = self._digits(sym)

            tick = mt5.symbol_info_tick(sym)
            if not tick:
                return False

            is_buy = side.lower() == "buy"
            price = float(tick.ask) if is_buy else float(tick.bid)

            # Dollar to price distance conversion - FIXED TP/SL (rules 5 & 6):
            # the dollar fields are authoritative and must never be widened or
            # shrunk (no broker min-stop adjustment here; the dollar targets are
            # additionally enforced every tick in _manage_positions).
            tp_dollar = float(self.cfg.get("tp") or 0.10)
            sl_dollar = float(self.cfg.get("sl") or 2.00)
            mpp = self.money_per_price(lot, sym)

            if mpp > 0:
                tp_dist = round(tp_dollar / mpp, digits)
                sl_dist = round(sl_dollar / mpp, digits)
            else:
                # without a tick-value reading the dollars cannot be converted
                # to a price distance - trading with the raw dollars as price
                # units would place SL/TP 100x off on high-priced symbols
                self._log("warn", f"Trade skipped: cannot price TP/SL on {sym} "
                                  f"(tick value unavailable) [{comment}]")
                return False

            sl_price = 0.0
            tp_price = 0.0

            # Attach the exact configured TP/SL distances to the order.
            if is_buy:
                if sl_dist > 0:
                    sl_price = round(price - sl_dist, digits)
                if tp_dist > 0:
                    tp_price = round(price + tp_dist, digits)
            else:
                if sl_dist > 0:
                    sl_price = round(price + sl_dist, digits)
                if tp_dist > 0:
                    tp_price = round(price - tp_dist, digits)

            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": sym,
                "volume": lot,
                "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
                "price": price,
                "sl": sl_price,
                "tp": tp_price,
                "deviation": int(self.cfg.get("slippage_points") or 30),
                "magic": int(self.cfg.get("magic") or 777002),
                "comment": comment[:31],
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling_mode(sym),
            }

            label = f"{side.upper()} {lot} @ {price} (TP ${tp_dollar}, SL ${sl_dollar})"
            ok = self._send(req, label)
            if ok:
                self._last_order_ts = time.time()
                self._log("trade", f"Opened {label} [{comment}]")
            return ok
        finally:
            self._order_in_progress = False

    def _close_position(self, p: Dict[str, Any], reason: str = "") -> bool:
        """Closes an open position at market."""
        sym = p.get("symbol") or self.symbol
        is_buy = p.get("side") == "buy"
        try:
            tick = mt5.symbol_info_tick(sym)
        except Exception:
            tick = None
        if not tick:
            return False
        price = float(tick.bid) if is_buy else float(tick.ask)
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": int(p["ticket"]),
            "symbol": sym,
            "volume": float(p["volume"]),
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "price": price,
            "deviation": int(self.cfg.get("slippage_points") or 30),
            "magic": int(self.cfg.get("magic") or 777002),
            "comment": f"bot2 {reason}"[:31],
            "type_filling": self._filling_mode(sym),
        }
        ok = self._send(req, f"Close #{p['ticket']} {p.get('side')} {p.get('volume')} @ {price} ({reason})")
        if ok:
            self._last_close_ts = time.time()
        return ok

    def _send(self, req: Dict[str, Any], label: str) -> bool:
        try:
            res = mt5.order_send(req)
        except Exception as exc:
            self._log("error", f"{label} raised: {exc}")
            return False
        if res is None:
            err = mt5.last_error()
            self._log("error", f"{label} failed: {err}")
            return False
        code = int(res.retcode)
        if code in RETCODE_SUCCESS:
            self._log("trade", f"{label} -> {_tname(code)}")
            return True
        self._log("error", f"{label} rejected: {_tname(code)} {getattr(res, 'comment', '')}")
        return False

    def close_all(self, reason: str = "manual") -> int:
        """Panic button: Closes all Bot 2 positions immediately."""
        closed = 0
        for p in self._all_positions(any_symbol=True):
            self._self_closed.add(int(p.get("ticket") or 0))
            if self._close_position(p, reason):
                closed += 1
        self.cfg["running"] = False
        self.enabled = False
        self._log("warn", f"Bot2 Closed all ({closed} positions) [{reason}]")
        return closed

    def _track(self, positions: List[Dict[str, Any]]) -> None:
        """Books opened and closed positions."""
        current = {int(p["ticket"]): p for p in positions}
        for ticket, p in list(self._known_positions.items()):
            if ticket not in current:
                self._last_close_ts = time.time()
                net = float(p.get("net") or 0.0)
                self.stats["closed"] += 1
                self.stats["realized"] = round(self.stats["realized"] + net, 2)
                if net >= 0:
                    self.stats["wins"] += 1
                else:
                    self.stats["losses"] += 1
                self._log("trade", f"Position #{ticket} closed ({net:+.2f})")
                self._self_closed.discard(ticket)
        for ticket, p in current.items():
            if ticket not in self._known_positions:
                self._log("trade", f"Position #{ticket} opened: {p['side']} {p['volume']} {p.get('symbol')} @ {p.get('price_open')}")
                self.stats["opened"] += 1
        self._known_positions = current

    # --------------------------------------------------------------- status
    def status(self) -> Dict[str, Any]:
        positions = self._all_positions()
        if not self.available_symbols_list and mt5 is not None:
            try:
                self.available_symbols()
            except Exception:
                pass
        floating = round(sum(float(p.get("net") or 0.0) for p in positions), 2)
        lot = float(self.cfg.get("lot") or 0.01)

        tick = None
        if mt5 is not None and self.symbol:
            try:
                tick = mt5.symbol_info_tick(self.symbol)
            except Exception:
                tick = None

        ask = float(getattr(tick, "ask", 0.0) or 0.0) if tick else 0.0
        bid = float(getattr(tick, "bid", 0.0) or 0.0) if tick else 0.0
        digits = self._digits(self.symbol) if self.symbol else 2
        spread = round(ask - bid, digits) if (ask and bid) else 0.0

        struct = self.compute_structure(self.symbol)

        now = time.time()
        interval = float(self.cfg.get("trend_check_interval") or 2.5)
        if self.symbol and (not self._last_trend_check_ts or (now - self._last_trend_check_ts) >= interval):
            try:
                self.check_trend(force=False)
            except Exception:
                pass

        next_check_in = max(0.0, round(interval - (now - self._last_trend_check_ts), 1)) if self._last_trend_check_ts > 0 else round(interval, 1)

        return {
            "running": bool(self.cfg.get("running")),
            "enabled": self.enabled,
            "symbol": self.symbol,
            "symbol_requested": str(self.cfg.get("symbol") or "BTCUSDTm"),
            "symbol_ok": bool(self.symbol_ok and self.symbol),
            "symbol_substituted": self.symbol_substituted,
            "symbol_options": list(self.available_symbols_list),
            "ask": ask,
            "bid": bid,
            "spread": spread,
            "digits": digits,
            "config": dict(self.cfg),
            "structure": struct,
            "positions": positions,
            "running_count": len(positions),
            "floating": floating,
            "stats": dict(self.stats),
            "last_action": self.last_action,
            "last_error": self.last_error,
            "candle_dir": self._last_candle_dir,
            "trend_mode": bool(self.cfg.get("trend_mode")),
            "market_trend": self._current_trend,
            "trend_details": self._trend_details or ("Bullish Bias" if self._current_trend == "UP" else "Bearish Bias"),
            "trend_score": self._trend_score,
            "watch_pressure": self._watch_pressure,
            "watch_buyer_pct": round(self._watch_buyer_pct, 1),
            "watch_seller_pct": round(self._watch_seller_pct, 1),
            "watch_enabled": self._watch_on(),
            "watch_min_pct": self._watch_min_pct(),
            "watch_min_threshold_pct": self._watch_min_pct(),
            "reverse": bool(self.cfg.get("reverse")),
            "next_side": self._next_side(),
            "trend_checked_ts": self._last_trend_check_ts,
            "trend_interval": interval,
            "trend_next_check_in": next_check_in,
            "trend_history": list(self._trend_history[-5:]),
            "volume_min": self.volume_min() if self.symbol else 0.01,
            "volume_max": self.volume_max() if self.symbol else 100.0,
            "volume_step": self.volume_step() if self.symbol else 0.01,
            "money_per_price": round(self.money_per_price(lot), 4) if self.symbol else 0.0,
        }
