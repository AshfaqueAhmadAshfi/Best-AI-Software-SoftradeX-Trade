"""
Extreme High & Low Structure Sniper Bot (Bot 3).

Strategy Specification
----------------------
1. Market Price Levels Monitoring:
   - Tracks Higher High (HH), Lower High (LH), Higher Low (HL), and Lower Low (LL).
   - Displays live cards for HH (SELL), LH (SELL), HL (BUY), LL (BUY).
2. Today's Day Extreme Check (D1):
   - Monitors Today's Higher High (HH) and Lower Low (LL).
3. Current Week Opening Extreme Check (W1):
   - Monitors This Week's Opening Higher High (HH) and Lower Low (LL).
4. Previous Week Extreme Check (Prev W1):
   - Monitors Previous Week's Higher High (HH) and Lower Low (LL).
5. Extreme Range Sniping Execution:
   - When market is at the absolute bottom (at/touching Lower Low support extremes) -> Take BUY trade.
   - When market is at the absolute top (at/touching Higher High resistance extremes) -> Take SELL trade.
6. Middle Zone Trade Filter:
   - Strictly NO TRADES in the middle zone. Trades are only executed at extreme highs and lows.
7. Configurable Settings:
   - Lot Size, Max Running Trades, TP ($), SL ($).
8. Interactive Market Selection Buttons:
   - Switchable market chips (BTCUSDTm, BTCUSDm, XAUUSD247m, XAUUSDm, etc.).
9. START, STOP, and Close All Panic Buttons.
10. UI Design matching the existing dashboard standard.
11. Signal & Timer Radar Side Box:
    - Live countdown timer for the next analysis pulse / candle close.
    - 5 Upcoming Planned Trades with exact trigger prices, distance in $, and conditions.
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
    "tp": 0.50,            # TP in dollars (default $0.50)
    "sl": 5.00,            # SL in dollars (default $5.00)
    "symbol": "XAUUSDm",
    "timeframe": "D1",     # Active view: "D1" (Today), "W1" (This Week), "PREV" (Prev Week)
    "magic": 777003,       # distinct magic number from Bot 1 (777001) & Bot 2 (777002)
    "slippage_points": 30,
    "cooldown_sec": 2.0,
    "tolerance_pct": 0.0006, # ~0.06% price buffer for level touches
}

PREFERRED_SYMBOLS = [
    "XAUUSDm", "XAUUSD247m", "XAUUSD",
    "BTCUSDTm", "BTCUSDm", "BTCUSD",
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
    10014: "invalid volume", 10015: "invalid price", 10016: "invalid stops", 10017: "trade disabled",
    10018: "market closed", 10019: "no money", 10020: "price changed", 10021: "off quotes",
    10022: "broker busy", 10024: "too many requests", 10027: "auto trading disabled by client",
    10028: "auto trading disabled by server", 10030: "unsupported filling",
}


def _as_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "_asdict"):
        return dict(obj._asdict())
    try:
        return {s: getattr(obj, s) for s in obj.__slots__}
    except (TypeError, ValueError):
        return dict(vars(obj))


class ExtremeStructureBot:
    """Extreme High & Low Structure Sniper Bot (Bot 3)."""

    def __init__(self, logger=None) -> None:
        self.cfg: Dict[str, Any] = dict(DEFAULTS)
        self.logger = logger
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
        self._last_trigger_tag: str = ""
        self._levels_cache: Dict[str, Any] = {}
        self._levels_cache_ts: float = 0.0

    def _log(self, kind: str, message: str) -> None:
        self.last_action = message
        if self.logger:
            try:
                self.logger(kind, f"[Bot3] {message}")
            except Exception:
                pass

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        old_symbol = str(self.cfg.get("symbol") or "").strip()
        for k, v in (patch or {}).items():
            if k in DEFAULTS:
                self.cfg[k] = v
        if not str(self.cfg.get("symbol") or "").strip():
            self.cfg["symbol"] = "XAUUSDm"
        new_symbol = str(self.cfg.get("symbol") or "").strip()
        if new_symbol and old_symbol and old_symbol.upper() != new_symbol.upper():
            self._symbol_changed = True
            self._symbol_explicit = True
            self.symbol = ""
            self.symbol_ok = False
            self._resolve_ts = 0.0
            self._levels_cache.clear()

        # Sanitize numeric fields
        self.cfg["lot"] = self._round_lot(float(self.cfg.get("lot") or 0.01))
        self.cfg["max_trades"] = max(1, int(self.cfg.get("max_trades") or 1))
        self.cfg["tp"] = max(0.01, round(float(self.cfg.get("tp") or 0.50), 2))
        self.cfg["sl"] = max(0.05, round(float(self.cfg.get("sl") or 5.00), 2))
        tf = str(self.cfg.get("timeframe") or "D1").upper()
        self.cfg["timeframe"] = tf if tf in ("D1", "W1", "PREV") else "D1"
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

    def money_per_price(self, lot: float = 0.01, symbol: str = "") -> float:
        """Account money gained per 1.0 of price movement (0.0 = unknown)."""
        sym = symbol or self.symbol
        if mt5 is None or not sym:
            return 0.0
        try:
            tick = mt5.symbol_info_tick(sym)
            bid = float(tick.bid) if tick else 0.0
            if not bid:
                return 0.0
            p1 = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, sym, lot, bid, bid + 1.0)
            if p1 is not None and p1 > 0:
                return float(p1)
        except Exception:
            pass
        # order_calc_profit unavailable -> fall back to the tick-value math
        try:
            info = mt5.symbol_info(sym)
            tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
            tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
            if tick_value > 0 and tick_size > 0:
                return (tick_value / tick_size) * float(lot)
        except Exception:
            pass
        if "XAU" in sym.upper() or "GOLD" in sym.upper():
            return lot * 100.0
        return 1.0

    def _digits(self, symbol: str = "") -> int:
        sym = symbol or self.symbol
        if mt5 is None or not sym:
            return 2
        try:
            info = mt5.symbol_info(sym)
            return int(info.digits) if info else 2
        except Exception:
            return 2

    def _point(self, symbol: str = "") -> float:
        sym = symbol or self.symbol
        if mt5 is None or not sym:
            return 0.01
        try:
            info = mt5.symbol_info(sym)
            return float(info.point) if info else 0.01
        except Exception:
            return 0.01

    def _filling_mode(self, symbol: str) -> int:
        if mt5 is None:
            return 0
        try:
            info = mt5.symbol_info(symbol)
            if info:
                fm = getattr(info, "filling_mode", 0)
                if fm & 1:
                    return mt5.ORDER_FILLING_FOK
                if fm & 2:
                    return mt5.ORDER_FILLING_IOC
        except Exception:
            pass
        return getattr(mt5, "ORDER_FILLING_RETURN", 0)

    def available_symbols(self, force: bool = False) -> List[str]:
        if mt5 is None:
            return list(PREFERRED_SYMBOLS)
        now = time.time()
        if not force and self.available_symbols_list and (now - self._symbols_ts) < 60:
            return list(self.available_symbols_list)
        try:
            all_syms = mt5.symbols_get() or []
        except Exception:
            all_syms = []
        out: List[str] = []
        for s in all_syms:
            name = str(getattr(s, "name", "") or "")
            up = name.upper()
            if (up in PREFERRED_SYMBOLS or any(b in up for b in (
                "XAUUSD", "GOLD", "BTCUSD", "BTCUSDT",
                "DXY", "USDX", "USOIL", "XTIUSD", "CL",
                "XAUGBP", "USDJPY", "EURUSD", "USTEC", "NAS100", "US100"
            ))) and not ("XAG" in up or "ETH" in up):
                out.append(name)

        def rank(n: str) -> tuple:
            up = n.upper()
            if up in PREFERRED_SYMBOLS:
                return (PREFERRED_SYMBOLS.index(up), 0, n)
            gold = 0 if "XAU" in up or "GOLD" in up else 1
            return (len(PREFERRED_SYMBOLS), gold, n)

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
        wanted = str(self.cfg.get("symbol") or "XAUUSDm").strip() or "XAUUSDm"
        try:
            info = mt5.symbol_info(wanted)
        except Exception:
            info = None
        if info is None:
            cands = [
                wanted + "m", wanted.rstrip("m"), "XAUUSDm", "XAUUSD247m", "BTCUSDTm", "BTCUSDm",
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

    # ------------------------------------------------ Extreme Structure Levels
    def compute_all_levels(self, sym: str = "") -> Dict[str, Any]:
        """Calculates extreme levels for Today (D1), Current Week Opening (W1), and Previous Week (PREV)."""
        sym = sym or self.symbol
        if mt5 is None or not sym:
            return {
                "d1": {}, "w1": {}, "prev": {},
                "active_view": self.cfg.get("timeframe", "D1"),
            }
        now = time.time()
        if self._levels_cache and (now - self._levels_cache_ts) < 10:
            return dict(self._levels_cache)

        d1_data: Dict[str, Any] = {}
        w1_data: Dict[str, Any] = {}
        prev_data: Dict[str, Any] = {}

        try:
            # 1. Day / Today levels (Fetch 7 daily bars)
            rates_d1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 7)
            if rates_d1 is not None and len(rates_d1) > 0:
                today_bar = rates_d1[-1]
                t_high = float(today_bar["high"])
                t_low = float(today_bar["low"])
                t_open = float(today_bar["open"])

                all_highs = [float(r["high"]) for r in rates_d1]
                all_lows = [float(r["low"]) for r in rates_d1]
                sh = sorted(set(all_highs), reverse=True)
                sl = sorted(set(all_lows))

                d1_data = {
                    "hh": sh[0],                                    # Higher High
                    "lh": sh[1] if len(sh) > 1 else sh[0],          # Lower High
                    "hl": sl[1] if len(sl) > 1 else sl[0],          # Higher Low
                    "ll": sl[0],                                    # Lower Low
                    "today_high": t_high,
                    "today_low": t_low,
                    "today_open": t_open,
                }
        except Exception:
            d1_data = {}

        try:
            # 2. Week levels (Current Week & Previous Week)
            rates_w1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_W1, 0, 5)
            if rates_w1 is not None and len(rates_w1) > 0:
                cur_week = rates_w1[-1]
                cw_high = float(cur_week["high"])
                cw_low = float(cur_week["low"])
                cw_open = float(cur_week["open"])

                all_whighs = [float(r["high"]) for r in rates_w1]
                all_wlows = [float(r["low"]) for r in rates_w1]
                swh = sorted(set(all_whighs), reverse=True)
                swl = sorted(set(all_wlows))

                w1_data = {
                    "hh": swh[0],                                   # Current Week Higher High
                    "lh": swh[1] if len(swh) > 1 else swh[0],      # Current Week Lower High
                    "hl": swl[1] if len(swl) > 1 else swl[0],      # Current Week Higher Low
                    "ll": swl[0],                                   # Current Week Lower Low
                    "week_open": cw_open,
                    "week_high": cw_high,
                    "week_low": cw_low,
                }

                # 3. Previous Week completed bar (index -2)
                if len(rates_w1) >= 2:
                    prev_week = rates_w1[-2]
                    pw_high = float(prev_week["high"])
                    pw_low = float(prev_week["low"])
                    pw_open = float(prev_week["open"])
                    pw_close = float(prev_week["close"])

                    prev_data = {
                        "hh": pw_high,
                        "lh": round(pw_high * 0.985, 2),
                        "hl": round(pw_low * 1.015, 2),
                        "ll": pw_low,
                        "prev_open": pw_open,
                        "prev_high": pw_high,
                        "prev_low": pw_low,
                        "prev_close": pw_close,
                    }
                else:
                    prev_data = dict(w1_data)
        except Exception:
            w1_data = {}
            prev_data = {}

        data = {
            "d1": d1_data,
            "w1": w1_data,
            "prev": prev_data,
            "active_view": self.cfg.get("timeframe", "D1"),
        }
        self._levels_cache = data
        self._levels_cache_ts = now
        return data

    # ------------------------------------------------------------- positions
    def _all_positions(self, any_symbol: bool = False) -> List[Dict[str, Any]]:
        if mt5 is None:
            return []
        try:
            raw = mt5.positions_get(symbol=None if any_symbol else self.symbol) or []
        except Exception:
            raw = []
        magic = int(self.cfg.get("magic") or 777003)
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
            out.append(d)
        return out

    def close_all(self, reason: str = "manual") -> int:
        positions = self._all_positions(any_symbol=True)
        closed = 0
        for p in positions:
            if self._close_position(p, reason):
                closed += 1
                # mark as self-closed so tick() does not count it again as an
                # "external" close in its vanish branch
                self._self_closed.add(int(p.get("ticket") or 0))
        self._log("warn", f"Bot3 Closed all ({closed} positions) [{reason}]")
        self.cfg["running"] = False
        return closed

    def _close_position(self, p: Dict[str, Any], reason: str = "") -> bool:
        ticket = p.get("ticket")
        sym = p.get("symbol") or self.symbol
        side = p.get("side") or "buy"
        vol = float(p.get("volume") or 0.01)

        if mt5 is None or not ticket:
            return False

        tick = mt5.symbol_info_tick(sym)
        if not tick:
            return False
        price = float(tick.bid) if side == "buy" else float(tick.ask)

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": vol,
            "type": mt5.ORDER_TYPE_SELL if side == "buy" else mt5.ORDER_TYPE_BUY,
            "position": ticket,
            "price": price,
            "deviation": int(self.cfg.get("slippage_points") or 30),
            "magic": int(self.cfg.get("magic") or 777003),
            "comment": f"bot3_close_{reason}"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(sym),
        }
        res = mt5.order_send(req)
        retcode = getattr(res, "retcode", 0) if res else 0
        if retcode in RETCODE_SUCCESS:
            net = float(p.get("net") or 0.0)
            self.stats["closed"] += 1
            self.stats["realized"] = round(self.stats["realized"] + net, 2)
            if net > 0:
                self.stats["wins"] += 1
            else:
                self.stats["losses"] += 1
            self._log("trade", f"Closed #{ticket} ({side.upper()} {vol}) net: ${net:+.2f} ({reason})")
            return True
        else:
            err_msg = _RETCODE_NAMES.get(retcode, str(retcode))
            self._log("error", f"Failed to close #{ticket}: {err_msg}")
            return False

    # ------------------------------------------------------------- main loop
    def tick(self) -> None:
        if mt5 is None:
            return

        if not self.symbol:
            self.resolve_symbol()
            if not self.symbol:
                return

        # the dashboard switched market -> clear the OLD instrument first, so
        # positions on the previous symbol are never orphaned (they would be
        # invisible to _all_positions() which filters by the new symbol)
        if self._symbol_changed:
            self._symbol_changed = False
            self.close_all("market switched")
            if not bool(self.cfg.get("running")):
                return

        positions = self._all_positions()

        # Check new positions for tracking
        current_tickets = {p["ticket"] for p in positions}
        for p in positions:
            t = p["ticket"]
            if t not in self._known_positions:
                self._known_positions[t] = p
                self.stats["opened"] += 1
                self._log("trade", f"Position #{t} active: {p['side']} {p['volume']} {self.symbol} @ {p.get('price_open')}")

        # Check closed positions by broker
        for t in list(self._known_positions.keys()):
            if t not in current_tickets:
                old_p = self._known_positions.pop(t)
                if t not in self._self_closed:
                    self.stats["closed"] += 1
                    net = float(old_p.get("net") or 0.0)
                    self.stats["realized"] = round(self.stats["realized"] + net, 2)
                    if net > 0:
                        self.stats["wins"] += 1
                    else:
                        self.stats["losses"] += 1
                    self._log("trade", f"Position #{t} closed externally net: ${net:+.2f}")
                self._self_closed.discard(t)

        # 1. Enforce TP / SL on active positions
        self._manage_tp_sl(positions)

        # 2. Check extreme structure levels & enter trades if running
        if self.cfg.get("running"):
            self._check_and_trade(positions)

    def _manage_tp_sl(self, positions: List[Dict[str, Any]]) -> None:
        """Enforces dollar-based TP and SL actively on every tick."""
        tp_target = float(self.cfg.get("tp") or 0.50)
        sl_target = float(self.cfg.get("sl") or 5.00)

        for p in list(positions):
            net = float(p.get("net") or 0.0)
            ticket = p.get("ticket")

            if net >= tp_target:
                self._self_closed.add(ticket)
                self._close_position(p, f"TP reached (+${net:.2f} >= ${tp_target:.2f})")
                self._log("trade", f"Position #{ticket} hit TP target: +${net:.2f}")
            elif net <= -sl_target:
                self._self_closed.add(ticket)
                self._close_position(p, f"SL hit (-${abs(net):.2f} >= ${sl_target:.2f})")
                self._log("trade", f"Position #{ticket} hit SL target: ${net:.2f}")

    def _check_and_trade(self, positions: List[Dict[str, Any]]) -> None:
        """Evaluates Extreme Highs (SELL) & Extreme Lows (BUY). Middle zone trades are rejected."""
        max_trades = max(1, int(self.cfg.get("max_trades") or 1))
        if len(positions) >= max_trades:
            return

        now = time.time()
        cd = float(self.cfg.get("cooldown_sec") or 2.0)
        if (now - self._last_order_ts) < cd:
            return

        sym = self.symbol
        tick = mt5.symbol_info_tick(sym)
        if not tick or not tick.ask or not tick.bid:
            return

        current_price = (float(tick.ask) + float(tick.bid)) / 2.0
        digits = self._digits(sym)
        spread = round(float(tick.ask) - float(tick.bid), digits)

        levels_data = self.compute_all_levels(sym)
        d1 = levels_data.get("d1") or {}
        w1 = levels_data.get("w1") or {}
        prev = levels_data.get("prev") or {}

        # Collect major extreme support levels (Lower Lows) and resistance levels (Higher Highs)
        lows = [v for v in [d1.get("ll"), d1.get("hl"), w1.get("ll"), prev.get("ll")] if v is not None and v > 0]
        highs = [v for v in [d1.get("hh"), d1.get("lh"), w1.get("hh"), prev.get("hh")] if v is not None and v > 0]

        if not lows or not highs:
            return

        lowest_floor = min(lows)
        highest_ceiling = max(highs)

        # Dynamic tolerance zone (0.06% of price or 3x spread)
        tol_pct = float(self.cfg.get("tolerance_pct") or 0.0006)
        tolerance = max(spread * 3.0, current_price * tol_pct)

        # Range verification: Ensure price is at true extremes, NEVER middle
        total_range = highest_ceiling - lowest_floor
        if total_range <= tolerance * 2.0:
            return

        # ---------------- Rule 5 & 6: Extreme Sniping only ----------------
        # 1. Extreme Bottom (BUY ZONE): Price is touching/near lowest floor or key Lower Lows
        is_at_bottom = False
        bottom_level_hit = 0.0
        bottom_tag = ""

        # Check Today LL
        if d1.get("ll") and abs(current_price - d1["ll"]) <= tolerance:
            is_at_bottom = True
            bottom_level_hit = d1["ll"]
            bottom_tag = "Today LL"
        # Check Current Week LL
        elif w1.get("ll") and abs(current_price - w1["ll"]) <= tolerance:
            is_at_bottom = True
            bottom_level_hit = w1["ll"]
            bottom_tag = "Week LL"
        # Check Prev Week LL
        elif prev.get("ll") and abs(current_price - prev["ll"]) <= tolerance:
            is_at_bottom = True
            bottom_level_hit = prev["ll"]
            bottom_tag = "Prev Week LL"
        # Check sweep below lowest floor
        elif current_price < (lowest_floor + tolerance * 0.5):
            is_at_bottom = True
            bottom_level_hit = lowest_floor
            bottom_tag = "Absolute Floor"

        # 2. Extreme Top (SELL ZONE): Price is touching/near highest ceiling or key Higher Highs
        is_at_top = False
        top_level_hit = 0.0
        top_tag = ""

        # Check Today HH
        if d1.get("hh") and abs(current_price - d1["hh"]) <= tolerance:
            is_at_top = True
            top_level_hit = d1["hh"]
            top_tag = "Today HH"
        # Check Current Week HH
        elif w1.get("hh") and abs(current_price - w1["hh"]) <= tolerance:
            is_at_top = True
            top_level_hit = w1["hh"]
            top_tag = "Week HH"
        # Check Prev Week HH
        elif prev.get("hh") and abs(current_price - prev["hh"]) <= tolerance:
            is_at_top = True
            top_level_hit = prev["hh"]
            top_tag = "Prev Week HH"
        # Check sweep above highest ceiling
        elif current_price > (highest_ceiling - tolerance * 0.5):
            is_at_top = True
            top_level_hit = highest_ceiling
            top_tag = "Absolute Ceiling"

        # Execution
        if is_at_bottom and not is_at_top:
            tag = f"BUY_{bottom_tag}_{int(now // 30)}"
            if self._last_trigger_tag != tag:
                self._last_trigger_tag = tag
                self._log("info", f"Extreme Support Reached: {bottom_tag} ({bottom_level_hit}) -> Taking BUY")
                if self._open_market_trade("buy", f"bot3_{bottom_tag.lower().replace(' ', '_')}_buy"):
                    self._last_order_ts = now
                    return

        elif is_at_top and not is_at_bottom:
            tag = f"SELL_{top_tag}_{int(now // 30)}"
            if self._last_trigger_tag != tag:
                self._last_trigger_tag = tag
                self._log("info", f"Extreme Resistance Reached: {top_tag} ({top_level_hit}) -> Taking SELL")
                if self._open_market_trade("sell", f"bot3_{top_tag.lower().replace(' ', '_')}_sell"):
                    self._last_order_ts = now
                    return

        # Price is in the middle -> Strictly do nothing!

    def _open_market_trade(self, side: str, comment: str) -> bool:
        """Places a market BUY or SELL trade with TP & SL."""
        if mt5 is None or not self.symbol:
            return False

        sym = self.symbol
        lot = float(self.cfg.get("lot") or 0.01)
        lot = self._round_lot(lot)

        tick = mt5.symbol_info_tick(sym)
        if not tick:
            self._log("error", "No tick available")
            return False

        digits = self._digits(sym)
        point = self._point(sym)
        info = mt5.symbol_info(sym)
        stops_level = getattr(info, "trade_stops_level", 0) or 0
        min_stop = max(stops_level * point, point * 5)

        is_buy = side.lower() == "buy"
        price = float(tick.ask) if is_buy else float(tick.bid)

        tp_dollar = float(self.cfg.get("tp") or 0.50)
        sl_dollar = float(self.cfg.get("sl") or 5.00)
        mpp = self.money_per_price(lot, sym)

        if mpp <= 0:
            # without a tick-value reading the dollars cannot be converted to
            # a price distance - sending the raw dollars as price units would
            # place SL/TP 100x off on high-priced symbols
            self._log("warn", f"Trade skipped: cannot price TP/SL on {sym} (tick value unavailable) [{comment}]")
            return False
        tp_dist = round(tp_dollar / mpp, digits)
        sl_dist = round(sl_dollar / mpp, digits)

        sl_price = 0.0
        tp_price = 0.0

        if is_buy:
            if sl_dist > 0:
                # clamp up to the broker minimum instead of silently sending a
                # stop-less position (a dropped SL is never re-checked here)
                sl_price = round(price - max(sl_dist, min_stop), digits)
            if tp_dist > 0:
                tp_price = round(price + max(tp_dist, min_stop), digits)
        else:
            if sl_dist > 0:
                sl_price = round(price + max(sl_dist, min_stop), digits)
            if tp_dist > 0:
                tp_price = round(price - max(tp_dist, min_stop), digits)

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": sl_price,
            "tp": tp_price,
            "deviation": int(self.cfg.get("slippage_points") or 30),
            "magic": int(self.cfg.get("magic") or 777003),
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(sym),
        }

        label = f"{side.upper()} {lot} @ {price} (TP ${tp_dollar}, SL ${sl_dollar})"
        self._log("trade", f"Opening {label} [{comment}]")

        res = mt5.order_send(req)
        retcode = getattr(res, "retcode", 0) if res else 0

        if retcode in RETCODE_SUCCESS:
            order_id = getattr(res, "order", 0)
            self._log("trade", f"{label} -> done (#{order_id})")
            return True
        else:
            err_msg = _RETCODE_NAMES.get(retcode, f"code {retcode}")
            self.last_error = f"Order failed: {err_msg}"
            self._log("error", f"{label} -> {err_msg}")
            return False

    # ------------------------------------------------------------- planned trades (Radar)
    def compute_planned_trades(self, cur_price: float, levels_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Calculates 5 upcoming planned trade trigger setups for the side radar box."""
        d1 = levels_data.get("d1") or {}
        w1 = levels_data.get("w1") or {}
        prev = levels_data.get("prev") or {}

        tp = float(self.cfg.get("tp") or 0.50)
        sl = float(self.cfg.get("sl") or 5.00)
        digits = self._digits(self.symbol) if self.symbol else 2
        lot = float(self.cfg.get("lot") or 0.01)
        money_per_pt = self.money_per_price(lot) if self.symbol else 1.0

        # 5 Key Setups
        raw_setups = [
            {
                "id": 1,
                "action": "BUY",
                "scope": "Today",
                "label": "Today Lower Low (LL)",
                "price": float(d1.get("ll") or 0.0),
                "reason": "Market touches Today's Lower Low Support",
                "reason_bn": "আজকের দিনের সর্বনিম্ন প্রাইস (LL) টাচ করলে Buy নিবে",
            },
            {
                "id": 2,
                "action": "SELL",
                "scope": "Today",
                "label": "Today Higher High (HH)",
                "price": float(d1.get("hh") or 0.0),
                "reason": "Market touches Today's Higher High Resistance",
                "reason_bn": "আজকের দিনের সর্বোচ্চ প্রাইস (HH) টাচ করলে Sell নিবে",
            },
            {
                "id": 3,
                "action": "BUY",
                "scope": "This Week",
                "label": "This Week Opening Low (LL)",
                "price": float(w1.get("ll") or 0.0),
                "reason": "Current Week Opening Low Support sweep",
                "reason_bn": "চলতি সপ্তাহের ওপেনিং লো (LL) সাপোর্ট থেকে Buy নিবে",
            },
            {
                "id": 4,
                "action": "SELL",
                "scope": "This Week",
                "label": "This Week Opening High (HH)",
                "price": float(w1.get("hh") or 0.0),
                "reason": "Current Week Opening High Resistance sweep",
                "reason_bn": "চলতি সপ্তাহের ওপেনিং হাই (HH) রেজিস্ট্যান্স থেকে Sell নিবে",
            },
            {
                "id": 5,
                "action": "BUY" if (cur_price and prev.get("ll") and abs(cur_price - prev["ll"]) < abs(cur_price - (prev.get("hh") or cur_price))) else "SELL",
                "scope": "Prev Week",
                "label": "Prev Week Low (Floor)" if (cur_price and prev.get("ll") and abs(cur_price - prev["ll"]) < abs(cur_price - (prev.get("hh") or cur_price))) else "Prev Week High (Ceiling)",
                "price": float(prev.get("ll") if (cur_price and prev.get("ll") and abs(cur_price - prev["ll"]) < abs(cur_price - (prev.get("hh") or cur_price))) else (prev.get("hh") or 0.0)),
                "reason": "Previous Week Extreme Level major reversal",
                "reason_bn": "পূর্ববর্তী সপ্তাহের এক্সট্রিম লেভেল থেকে রিভার্সাল ট্রেড",
            },
        ]

        out: List[Dict[str, Any]] = []
        for s in raw_setups:
            target_price = s["price"]
            if target_price > 0 and cur_price > 0:
                dist = round(abs(cur_price - target_price), digits)
                dist_pct = round((dist / cur_price) * 100.0, 2)
                is_touch = dist <= (cur_price * float(self.cfg.get("tolerance_pct") or 0.0006))
            else:
                dist = 0.0
                dist_pct = 0.0
                is_touch = False

            # Status description
            if is_touch:
                status_text = "TRIGGER ZONE"
                status_text_bn = "ট্রিগার জোন (এন্ট্রি লেভেল)"
                status_cls = "trigger"
            elif dist_pct <= 0.35:
                status_text = f"IMMINENT ({dist_pct}%)"
                status_text_bn = f"খুবই কাছাকাছি ({dist_pct}%)"
                status_cls = "imminent"
            elif dist_pct <= 1.2:
                status_text = f"APPROACHING ({dist_pct}%)"
                status_text_bn = f"কাছাকাছি আসছে ({dist_pct}%)"
                status_cls = "approaching"
            else:
                status_text = f"PENDING ({dist_pct}%)"
                status_text_bn = f"অপেক্ষমাণ ({dist_pct}%)"
                status_cls = "pending"

            # Compute TP & SL price target
            if s["action"] == "BUY":
                est_tp_price = round(target_price + (tp / max(1e-4, money_per_pt)), digits) if target_price else 0.0
                est_sl_price = round(target_price - (sl / max(1e-4, money_per_pt)), digits) if target_price else 0.0
            else:
                est_tp_price = round(target_price - (tp / max(1e-4, money_per_pt)), digits) if target_price else 0.0
                est_sl_price = round(target_price + (sl / max(1e-4, money_per_pt)), digits) if target_price else 0.0

            out.append({
                "id": s["id"],
                "action": s["action"],
                "scope": s["scope"],
                "label": s["label"],
                "price": target_price,
                "distance": dist,
                "dist_pct": dist_pct,
                "is_touch": is_touch,
                "status_text": status_text,
                "status_text_bn": status_text_bn,
                "status_cls": status_cls,
                "est_tp_price": est_tp_price,
                "est_sl_price": est_sl_price,
                "reason": s["reason"],
                "reason_bn": s["reason_bn"],
                "tp": tp,
                "sl": sl,
            })

        # Rank setups by proximity (smallest distance to current price first)
        out.sort(key=lambda x: (x["distance"] if x["distance"] > 0 else 999999.0))
        for rank_idx, item in enumerate(out, start=1):
            item["priority_rank"] = rank_idx

        return out

    def recalculate_setups(self, force: bool = True) -> List[Dict[str, Any]]:
        """Recalculates the 5 setups with fresh market data (called every 60s)."""
        if force:
            self._levels_cache = {}
            self._levels_cache_ts = 0.0
        
        tick = None
        if mt5 is not None and self.symbol:
            try:
                tick = mt5.symbol_info_tick(self.symbol)
            except Exception:
                tick = None

        ask = float(getattr(tick, "ask", 0.0) or 0.0) if tick else 0.0
        bid = float(getattr(tick, "bid", 0.0) or 0.0) if tick else 0.0
        digits = self._digits(self.symbol) if self.symbol else 2
        cur_price = round((ask + bid) / 2.0, digits) if (ask and bid) else 0.0

        levels = self.compute_all_levels(self.symbol)
        planned = self.compute_planned_trades(cur_price, levels)
        self._last_recalc_ts = time.time()
        if planned:
            self._log("info", f"Next 5 Setups auto-recalculated: Closest target is {planned[0]['label']} (${planned[0]['distance']} away)")
        return planned

    # ------------------------------------------------------------- status
    def status(self) -> Dict[str, Any]:
        positions = self._all_positions()
        if not self.available_symbols_list:
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
        cur_price = round((ask + bid) / 2.0, digits) if (ask and bid) else 0.0

        # Auto-recalculate levels every 60-second candle boundary
        now_ts = int(time.time())
        m1_candle_ts = now_ts - (now_ts % 60)
        if getattr(self, "_last_recalc_candle_ts", 0) != m1_candle_ts:
            self._last_recalc_candle_ts = m1_candle_ts
            self._levels_cache = {}
            self._levels_cache_ts = 0.0

        levels_data = self.compute_all_levels(self.symbol)
        planned = self.compute_planned_trades(cur_price, levels_data)

        # Candle countdown timer (seconds to next M1 candle / 60s cycle)
        m1_countdown = 60 - (now_ts % 60)
        timer_str = f"{m1_countdown:02d}s"

        # Determine current market zone
        zone_status = "MIDDLE (Waiting / No Trade)"
        for p_trade in planned:
            if p_trade.get("is_touch"):
                zone_status = f"IN {p_trade['action']} ZONE ({p_trade['scope']})"
                break

        return {
            "running": bool(self.cfg.get("running")),
            "enabled": self.enabled,
            "symbol": self.symbol,
            "symbol_requested": str(self.cfg.get("symbol") or "XAUUSDm"),
            "symbol_ok": bool(self.symbol_ok and self.symbol),
            "symbol_substituted": self.symbol_substituted,
            "symbol_options": list(self.available_symbols_list),
            "ask": ask,
            "bid": bid,
            "spread": spread,
            "digits": digits,
            "price": cur_price,
            "config": dict(self.cfg),
            "levels": levels_data,
            "planned_trades": planned,
            "timer_str": timer_str,
            "recalc_countdown": m1_countdown,
            "recalc_interval": 60,
            "last_recalc_ts": getattr(self, "_last_recalc_ts", now_ts),
            "zone_status": zone_status,
            "positions": positions,
            "running_count": len(positions),
            "floating": floating,
            "stats": dict(self.stats),
            "last_action": self.last_action,
            "last_error": self.last_error,
            "volume_min": self.volume_min() if self.symbol else 0.01,
            "volume_max": self.volume_max() if self.symbol else 100.0,
            "volume_step": self.volume_step() if self.symbol else 0.01,
            "money_per_price": round(self.money_per_price(lot), 4) if self.symbol else 0.0,
        }



if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="ExtremeStructureBot CLI & live monitor")
    parser.add_argument("--symbol", default="XAUUSDm", help="Trading symbol (e.g. XAUUSDm, BTCUSDTm)")
    parser.add_argument("--lot", type=float, default=0.01, help="Lot size")
    parser.add_argument("--tp", type=float, default=0.50, help="TP in dollars")
    parser.add_argument("--sl", type=float, default=5.00, help="SL in dollars")
    parser.add_argument("--max-trades", type=int, default=1, help="Max open trades")
    parser.add_argument("--start", action="store_true", help="Start active trading loop")
    args = parser.parse_args()

    print("\n--- ExtremeStructureBot (Extreme High & Low Structure Sniper Bot) ---")
    if mt5 is None:
        print("[!] MetaTrader 5 Python package not available.")
        sys.exit(1)
    if not mt5.initialize():
        print(f"[!] MetaTrader 5 initialize failed: {mt5.last_error()}")
        sys.exit(1)

    bot = ExtremeStructureBot(logger=lambda kind, msg: print(f"[{kind.upper()}] {msg}"))
    bot.update_config({
        "symbol": args.symbol,
        "lot": args.lot,
        "tp": args.tp,
        "sl": args.sl,
        "max_trades": args.max_trades,
        "running": args.start,
    })
    bot.resolve_symbol(force=True)
    status = bot.status()
    print(f"Symbol        : {status.get('symbol')} (OK: {status.get('symbol_ok')})")
    print(f"Bid / Ask     : {status.get('bid')} / {status.get('ask')} (Spread: {status.get('spread')})")
    levels = status.get("levels", {})
    print(f"Today (D1)    : {levels.get('d1')}")
    print(f"Week (W1)     : {levels.get('w1')}")
    print(f"Prev Week     : {levels.get('prev')}")
    print(f"Zone Status   : {status.get('zone_status')} (Timer: {status.get('timer_str')})")
    print("\nNext 5 Planned Trades:")
    for pt in status.get("planned_trades", []):
        print(f"  #{pt['id']} {pt['action']} @ {pt['price']} (Distance: ${pt['distance']}) - {pt['label']}")
    print(f"\nRunning Trades: {status.get('running_count')} (Floating P/L: {status.get('floating')})")

    if args.start:
        print("\n[+] Starting extreme sniper trading loop (Press Ctrl+C to stop)...")
        try:
            while True:
                bot.tick()
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n[-] Stopped.")
