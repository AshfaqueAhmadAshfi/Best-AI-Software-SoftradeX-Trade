"""
Pre-Trade Entry Bot (Bot 6).

Strategy Specification
----------------------
1. The user picks a market, a Lot Size, a Distance ($) and optionally TP / SL.
2. START places ONE pre-trade (a pending stop order) at Distance from the
   current market price - above the market for a BUY, below it for a SELL.
3. Direction comes from the Market Pressure Watch dials:
   - Watch shows BUYER / SELLER pressure  -> that side is taken.
   - Watch is NEUTRAL (or the pressure is below the Min Pressure %) -> no
     trade is taken; the bot keeps waiting for the dial to wake up.
   - The Min Pressure % field is the threshold (default 60%).
   - With the Watch gate switched OFF the two fixed sides are used instead
     (Buy only / Sell only / Both sides).
4. TP: a value > 0 is a FIXED take profit in account money. 0 = no TP at all.
5. SL: a value > 0 is a FIXED stop loss in account money.
   SL = 0 switches on the PROFIT-LOCK rule:
   - Once the trade is at least $0.80 in profit the SL is placed at 70% of
     that profit.
   - As the profit grows the SL follows, always at 70% of the *current*
     profit -> the trade can never give back more than 30%.
   - If the profit shrinks the SL is NEVER pulled back.
6. STOP cancels the pre-trade. An already-open position keeps its SL/TP and
   is still managed (a position is never left unprotected).

Every MetaTrader5 call happens on the engine thread - this class never calls
MT5 from anywhere else.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

try:  # pragma: no cover - only missing on non-Windows
    import MetaTrader5 as mt5  # type: ignore
except Exception:  # pragma: no cover
    mt5 = None  # type: ignore

DEFAULTS: Dict[str, Any] = {
    "running": False,
    "lot": 0.01,
    "distance": 1.0,          # $ from the market price to the pre-trade
    "symbol": "XAUUSDm",
    "magic": 777006,          # distinct from bot1..bot5 (777001..777005)
    "side": "watch",          # watch | buy | sell | both
    "watch_gate": True,       # False = the side above is used without the dials
    "watch_min_pct": 60.0,    # dial pressure required to take a pre-trade
    "ai_gate": True,          # consult the AI market-direction outlook too
    "tp": 0.0,                # 0 = no take profit (fixed value otherwise)
    "sl": 0.0,                # 0 = 70% profit-lock trail (fixed value otherwise)
    "lock_pct": 70.0,         # profit-lock % applied when sl = 0
    "lock_min_profit": 0.80,  # $ profit before the lock is placed at all
    "cooldown_sec": 2.0,
    "slippage_points": 30,
}

# config keys that change what/where the pre-trade is -> re-place when flat
_LAYOUT_KEYS = ("lot", "distance", "side", "watch_gate", "watch_min_pct", "ai_gate",
                "tp", "sl")

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

ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP = 4
ORDER_TYPE_SELL_STOP = 5
ORDER_STATE_PLACED = 1
ORDER_STATE_PARTIAL = 3

RETCODE_SUCCESS = {10008, 10009, 10010}
_RETCODE_NAMES = {
    10004: "requote", 10006: "rejected", 10007: "cancelled", 10008: "placed", 10009: "done",
    10010: "done partially", 10011: "error", 10012: "timeout", 10013: "invalid request",
    10014: "invalid volume", 10015: "invalid price", 10016: "invalid stops", 10017: "trading disabled",
    10018: "market closed", 10019: "no money", 10020: "price changed", 10021: "off quotes",
    10022: "broker busy", 10024: "too many requests", 10027: "auto trading disabled by client",
    10028: "auto trading disabled by server", 10030: "unsupported filling",
    10031: "connection lost", 10033: "limit orders only", 10034: "volume limit",
    10036: "position closed", 10038: "close only", 10039: "fifo close only",
    10040: "hedge prohibited", 10041: "prohibited by fifo rule",
}


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


def _f(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _same(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return bool(a) == bool(b)


class PreTradeEntryBot:
    """Pre-Trade Entry Bot (Bot 6)."""

    def __init__(self, logger=None, watch_provider: Optional[Callable[[str], Dict[str, Any]]] = None) -> None:
        self.cfg: Dict[str, Any] = dict(DEFAULTS)
        self.logger = logger
        self._watch_provider = watch_provider
        self._ai_provider = None
        self.symbol: str = ""
        self.symbol_ok: bool = False
        self.available_symbols_list: List[str] = []
        self._symbols_ts: float = 0.0
        self.enabled: bool = False
        self.last_action: str = ""
        self.last_error: str = ""
        self.stats: Dict[str, Any] = {"opened": 0, "closed": 0, "wins": 0, "losses": 0, "realized": 0.0}
        # last AI outlook read (shown on the panel)
        self.ai: Dict[str, Any] = {"market": "", "verdict": "", "allowed": True,
                                   "reason": "not read yet"}
        # pending pre-trades: [{"side","price","lot","ticket","state","sl","tp"}]
        self._entries: List[Dict[str, Any]] = []
        # position ticket -> {"side","lot","entry","tp","sl","placed_sltp"}
        self._pos_meta: Dict[int, Dict[str, Any]] = {}
        self._known_positions: Dict[int, Dict[str, Any]] = {}
        self._self_closed: set = set()
        self._cycle_opened: bool = False
        self._relayout: bool = False
        self._symbol_changed: bool = False
        self._resolve_ts: float = 0.0
        self._last_order_ts: float = 0.0
        self._last_sl_send_ts: float = 0.0
        self._retry_after: float = 0.0
        self._stop_cancel_ts: float = 0.0
        self._fail_streak: int = 0
        self._wait_log_ts: float = 0.0
        # last watch read (shown on the panel)
        self.watch: Dict[str, Any] = {
            "buyer_pct": 0.0, "seller_pct": 0.0, "dominant": "NEUTRAL",
            "side": "", "allowed": False, "reason": "not read yet",
        }

    def _log(self, kind: str, message: str) -> None:
        self.last_action = message
        if self.logger:
            try:
                self.logger(kind, f"[Bot6] {message}")
            except Exception:
                pass

    def set_watch_provider(self, provider) -> None:
        """Injects the live watch-dial source: fn(symbol) -> {buyer_pct, ...}."""
        self._watch_provider = provider

    def set_ai_provider(self, provider) -> None:
        """Injects the AI market-direction source: fn(symbol) -> {market, verdict, ...}."""
        self._ai_provider = provider

    # --------------------------------------------------------- AI direction
    def read_ai(self, side: str) -> Dict[str, Any]:
        """The news engine's AI verdict for this market, checked before entering.

        The AI board reads every news source and publishes one verdict per
        market. Only a *clear opposite* verdict blocks the trade: MIXED, or no
        outlook at all for the market, is left to the watch dials.
        """
        out: Dict[str, Any] = {"market": "", "verdict": "", "allowed": True,
                               "reason": "no AI outlook for this market"}
        if not bool(self.cfg.get("ai_gate", True)):
            out["reason"] = "AI check OFF"
            self.ai = out
            return out
        if self._ai_provider is None or not self.symbol:
            self.ai = out
            return out
        try:
            row = self._ai_provider(self.symbol) or {}
        except Exception as exc:
            out["reason"] = f"AI outlook error: {exc}"
            self.ai = out
            return out
        if not row:
            self.ai = out
            return out
        verdict = str(row.get("verdict") or "").upper()
        out.update(market=str(row.get("market") or ""), verdict=verdict)
        if verdict in ("BUY", "SELL"):
            want = "BUY" if str(side).lower() == "buy" else "SELL"
            if verdict == want:
                out["reason"] = f"AI {verdict} agrees ({row.get('confidence', 0)}% confidence)"
            else:
                out["allowed"] = False
                out["reason"] = f"AI {verdict} opposes {want}"
        else:
            out["reason"] = f"AI {verdict or 'no verdict'} (allowed)"
        self.ai = out
        return out

    # ------------------------------------------------------------- config
    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        old_symbol = str(self.cfg.get("symbol") or "").strip()
        before = {k: self.cfg.get(k) for k in _LAYOUT_KEYS}
        for k, v in (patch or {}).items():
            if k in DEFAULTS:
                self.cfg[k] = v
        if not str(self.cfg.get("symbol") or "").strip():
            self.cfg["symbol"] = "XAUUSDm"
        new_symbol = str(self.cfg.get("symbol") or "").strip()
        if new_symbol and old_symbol and old_symbol.upper() != new_symbol.upper():
            self._symbol_changed = True
            self.symbol = ""
            self.symbol_ok = False
            self._resolve_ts = 0.0

        self.cfg["lot"] = self._round_lot(_f(self.cfg.get("lot"), 0.01))
        self.cfg["distance"] = max(0.0, round(_f(self.cfg.get("distance"), 1.0), 6))
        self.cfg["tp"] = max(0.0, round(_f(self.cfg.get("tp"), 0.0), 4))
        self.cfg["sl"] = max(0.0, round(_f(self.cfg.get("sl"), 0.0), 4))
        self.cfg["lock_pct"] = min(95.0, max(0.0, _f(self.cfg.get("lock_pct"), 70.0)))
        self.cfg["lock_min_profit"] = max(0.0, round(_f(self.cfg.get("lock_min_profit"), 0.80), 4))
        self.cfg["watch_min_pct"] = min(99.0, max(0.0, _f(self.cfg.get("watch_min_pct"), 60.0)))
        self.cfg["watch_gate"] = bool(self.cfg.get("watch_gate"))
        self.cfg["side"] = self._clean_side(self.cfg.get("side"))
        self.cfg["cooldown_sec"] = max(0.0, _f(self.cfg.get("cooldown_sec"), 2.0))
        self.cfg["slippage_points"] = max(0, int(_f(self.cfg.get("slippage_points"), 30)))
        self.cfg["magic"] = int(_f(self.cfg.get("magic"), 777006))

        for k in _LAYOUT_KEYS:
            if patch and k in patch and not _same(patch.get(k), before.get(k)):
                # note: a TP / SL edit is picked up by _apply_protection on the
                # live position too, so the change never waits for a new cycle
                self._relayout = True
        return dict(self.cfg)

    @staticmethod
    def _clean_side(value: Any) -> str:
        side = str(value or "watch").strip().lower()
        return side if side in ("watch", "buy", "sell", "both") else "watch"

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
        """Money (account currency) gained per 1.0 of price movement."""
        sym = symbol or self.symbol
        if mt5 is None or not sym:
            return 0.0
        try:
            info = mt5.symbol_info(sym)
            tick_value = float(info.trade_tick_value)
            tick_size = float(info.trade_tick_size) or self._point(sym)
            if tick_value > 0 and tick_size > 0:
                return (tick_value / tick_size) * float(lot)
            contract = float(info.trade_contract_size)
            return contract * float(lot)
        except Exception:
            return 0.0

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

    def _min_stop_distance(self, sym: str = "") -> float:
        """Broker's minimum SL / pending distance from the current price."""
        sym = sym or self.symbol
        if mt5 is None or not sym:
            return 0.0
        try:
            info = mt5.symbol_info(sym)
            stops = max(int(getattr(info, "trade_stops_level", 0) or 0),
                        int(getattr(info, "trade_freeze_level", 0) or 0))
            return stops * self._point(sym)
        except Exception:
            return 0.0

    def _filling_mode(self, symbol: str) -> int:
        # pending orders on market-execution symbols must use RETURN
        return 2

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
        if not getattr(info, "visible", True):
            try:
                mt5.symbol_select(wanted, True)
            except Exception:
                pass
        self.symbol = wanted
        self.symbol_ok = True
        self._resolve_ts = now
        self.last_error = ""
        return wanted

    # ------------------------------------------------------------ watch gate
    def read_watch(self) -> Dict[str, Any]:
        """Fresh dial read: what side (if any) the Watch allows right now."""
        out: Dict[str, Any] = {
            "buyer_pct": 0.0, "seller_pct": 0.0, "dominant": "NEUTRAL",
            "side": "", "allowed": False, "reason": "",
        }
        if self._watch_provider is None or not self.symbol:
            out["reason"] = "no watch data"
            self.watch = out
            return out
        try:
            w = self._watch_provider(self.symbol) or {}
        except Exception as exc:
            out["reason"] = f"watch error: {exc}"
            self.watch = out
            return out
        if not w:
            out["reason"] = f"{self.symbol} has no live dial - strictly blocked"
            self.watch = out
            return out
        buyer = float(w.get("buyer_pct") or 0.0)
        seller = float(w.get("seller_pct") or 0.0)
        dominant = str(w.get("dominant") or "NEUTRAL").upper()
        out.update(buyer_pct=buyer, seller_pct=seller, dominant=dominant)
        ts = float(w.get("updated_at") or 0.0)
        if ts > 0 and (time.time() - ts) > 30.0:
            out["reason"] = "watch data is stale - blocked"
            self.watch = out
            return out
        min_pct = float(self.cfg.get("watch_min_pct") or 60.0)
        if buyer >= min_pct and buyer >= seller:
            out.update(side="buy", allowed=True, reason=f"Watch BUY {buyer:.0f}% >= {min_pct:.0f}%")
        elif seller >= min_pct:
            out.update(side="sell", allowed=True, reason=f"Watch SELL {seller:.0f}% >= {min_pct:.0f}%")
        else:
            out["reason"] = (f"Watch NEUTRAL ({dominant}) buyer {buyer:.0f}% / seller {seller:.0f}% "
                             f"< {min_pct:.0f}% - no trade")
        self.watch = out
        return out

    def _decide_sides(self) -> tuple[List[str], str]:
        """Which side(s) the pre-trade should be placed on right now.

        watch gate ON  -> the chosen side still needs its dial pressure to be
                          at (or above) the Min Pressure % field; a NEUTRAL
                          watch never trades.
        watch gate OFF -> the chosen side is used as-is.
        """
        side = self._clean_side(self.cfg.get("side"))
        gate = bool(self.cfg.get("watch_gate", True))
        if not gate:
            if side == "watch":
                return ([], "Watch gate is OFF but the side field is 'Watch' - pick Buy / Sell / Both")
            return (["buy"] if side == "buy" else ["sell"] if side == "sell" else ["buy", "sell"],
                    f"watch gate OFF - fixed {side}")

        w = self.read_watch()
        min_pct = float(self.cfg.get("watch_min_pct") or 60.0)
        buyer = float(w.get("buyer_pct") or 0.0)
        seller = float(w.get("seller_pct") or 0.0)
        if side == "watch":
            if not w.get("allowed"):
                return ([], w.get("reason") or "watch NEUTRAL")
            chosen = [str(w.get("side"))]
            why = w.get("reason") or ""
        elif side == "both":
            chosen = []
            if buyer >= min_pct:
                chosen.append("buy")
            if seller >= min_pct:
                chosen.append("sell")
            if not chosen:
                return ([], f"Watch buyer {buyer:.0f}% / seller {seller:.0f}% < {min_pct:.0f}%")
            why = f"Watch buyer {buyer:.0f}% / seller {seller:.0f}%"
        elif side == "sell":
            if seller < min_pct:
                return ([], f"Watch SELL {seller:.0f}% < {min_pct:.0f}%")
            chosen, why = ["sell"], f"Watch SELL {seller:.0f}% >= {min_pct:.0f}%"
        else:
            if buyer < min_pct:
                return ([], f"Watch BUY {buyer:.0f}% < {min_pct:.0f}%")
            chosen, why = ["buy"], f"Watch BUY {buyer:.0f}% >= {min_pct:.0f}%"

        # the AI market-direction board has the final say on the side
        kept: List[str] = []
        blocked = ""
        for s in chosen:
            ai = self.read_ai(s)
            if ai.get("allowed"):
                kept.append(s)
            else:
                blocked = ai.get("reason") or blocked
        if not kept:
            return ([], blocked or "AI outlook blocked the trade")
        return (kept, why)

    # ------------------------------------------------------------ exposure
    def _all_positions(self, any_symbol: bool = False) -> List[Dict[str, Any]]:
        if mt5 is None:
            return []
        try:
            raw = mt5.positions_get(symbol=None if any_symbol else self.symbol) or []
        except Exception:
            raw = []
        magic = int(self.cfg.get("magic") or 777006)
        out: List[Dict[str, Any]] = []
        for p in raw:
            d = _as_dict(p)
            if magic and int(d.get("magic") or 0) != magic:
                continue
            t = d.get("type")
            d["side"] = "buy" if t == 0 or str(t).lower() == "buy" else "sell"
            net = float(d.get("profit") or 0.0) + float(d.get("swap") or 0.0)
            d["net"] = round(net, 2)
            d["volume"] = float(d.get("volume") or 0.0)
            out.append(d)
        return out

    def _all_orders(self, any_symbol: bool = False) -> List[Dict[str, Any]]:
        if mt5 is None:
            return []
        try:
            raw = mt5.orders_get()
        except Exception:
            return []
        magic = int(self.cfg.get("magic") or 777006)
        out: List[Dict[str, Any]] = []
        for o in raw or []:
            d = _as_dict(o)
            if magic and int(d.get("magic") or 0) != magic:
                continue
            if not any_symbol and self.symbol and d.get("symbol") != self.symbol:
                continue
            if int(d.get("state", 0)) not in (ORDER_STATE_PLACED, ORDER_STATE_PARTIAL):
                continue
            otype = int(d.get("type", -1))
            d["kind"] = {2: "buy limit", 3: "sell limit", 4: "buy stop", 5: "sell stop"}.get(otype, f"type {otype}")
            d["volume"] = float(d.get("volume_current") or d.get("volume_initial") or 0.0)
            d["price_open"] = float(d.get("price_open") or 0.0)
            out.append(d)
        return out

    # --------------------------------------------------------------- trades
    def _close_position(self, p: Dict[str, Any], reason: str = "") -> bool:
        ticket = p.get("ticket")
        sym = p.get("symbol") or self.symbol
        side = p.get("side") or "buy"
        vol = float(p.get("volume") or 0.01)
        if mt5 is None or not ticket:
            return False
        try:
            tick = mt5.symbol_info_tick(sym)
        except Exception:
            tick = None
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
            "magic": int(self.cfg.get("magic") or 777006),
            "comment": "bot6_close"[:31],
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
            self._self_closed.add(int(ticket))
            self._log("trade", f"Closed #{ticket} ({side.upper()} {vol}) net: ${net:+.2f} ({reason})")
            return True
        err_msg = _RETCODE_NAMES.get(retcode, str(retcode))
        self.last_error = f"Close failed: {err_msg}"
        self._log("error", f"Failed to close #{ticket}: {err_msg}")
        return False

    def _cancel_orders(self, orders: List[Dict[str, Any]], why: str = "") -> int:
        cancelled = 0
        failed = 0
        first_err = ""
        for o in orders:
            req = {"action": mt5.TRADE_ACTION_REMOVE, "order": int(o.get("ticket") or o.get("order") or 0)}
            if not req["order"]:
                continue
            try:
                res = mt5.order_send(req)
                code = int(res.retcode) if res else -1
            except Exception:
                code = -1
            if code in RETCODE_SUCCESS:
                cancelled += 1
            else:
                failed += 1
                first_err = first_err or _RETCODE_NAMES.get(code, str(code))
        if cancelled:
            self._log("info", f"{cancelled} pre-trade(s) cancelled ({why})")
        if failed:
            self._log("warn", f"{failed} pre-trade(s) could not be cancelled ({first_err})")
        return cancelled

    def close_all(self, reason: str = "manual") -> int:
        """Panic button: close every bot position at market + cancel pre-trades."""
        closed = 0
        for p in self._all_positions(any_symbol=True):
            if self._close_position(p, reason):
                closed += 1
        orders = self._all_orders(any_symbol=True)
        if orders:
            self._cancel_orders(orders, why=reason)
        for e in self._entries:
            if e.get("state") == "pending":
                e["state"] = "cancelled"
                e["ticket"] = 0
        self.cfg["running"] = False
        self.enabled = False
        self._relayout = False
        self._log("warn", f"Bot6 Closed all ({closed} positions) [{reason}]")
        return closed

    def _cleanup_all(self, reason: str = "") -> None:
        orders = self._all_orders(any_symbol=True)
        if orders:
            self._cancel_orders(orders, why=reason)
        for p in self._all_positions(any_symbol=True):
            self._close_position(p, reason)
        self._entries = []
        self._pos_meta.clear()
        self._known_positions.clear()
        self._self_closed.clear()
        self._cycle_opened = False

    # ----------------------------------------------------------- placement
    def _money_offset(self, money: float, lot: float, digits: int) -> float:
        """Price offset that equals `money` account money at `lot`."""
        if money <= 0:
            return 0.0
        mpp = self.money_per_price(lot)
        if mpp <= 0:
            return 0.0
        return round(money / mpp, digits)

    def _place_entry(self, sides: List[str]) -> int:
        """Place the pre-trade(s) `distance` away from the market price."""
        sym = self.symbol
        if mt5 is None or not sym or not sides:
            return 0
        try:
            info = mt5.symbol_info(sym)
            tick = mt5.symbol_info_tick(sym)
        except Exception:
            info, tick = None, None
        if not info or not tick:
            return 0
        if not getattr(info, "trade_mode", 1) or not getattr(info, "trade_allowed", True):
            self._log("warn", "trading is disabled for this symbol - pre-trade not placed")
            return 0
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        if not ask or not bid:
            return 0

        digits = self._digits(sym)
        min_dist = self._min_stop_distance(sym)
        dist = max(0.0, _f(self.cfg.get("distance"), 1.0))
        if min_dist:
            dist = max(dist, min_dist)
        lot = self._round_lot(_f(self.cfg.get("lot"), 0.01))
        tp_money = max(0.0, _f(self.cfg.get("tp"), 0.0))
        sl_money = max(0.0, _f(self.cfg.get("sl"), 0.0))
        tp_off = self._money_offset(tp_money, lot, digits)
        sl_off = self._money_offset(sl_money, lot, digits)

        placed = 0
        first_err = ""
        entries: List[Dict[str, Any]] = []
        for side in sides[:2]:
            if side == "buy":
                price = round(ask + dist, digits)
                otype = ORDER_TYPE_BUY_STOP
                tp = round(price + tp_off, digits) if tp_off else 0.0
                sl = round(price - sl_off, digits) if sl_off else 0.0
            else:
                price = round(bid - dist, digits)
                otype = ORDER_TYPE_SELL_STOP
                tp = round(price - tp_off, digits) if tp_off else 0.0
                sl = round(price + sl_off, digits) if sl_off else 0.0
            entry: Dict[str, Any] = {
                "side": side, "price": price, "lot": lot,
                "ticket": 0, "state": "failed", "tp": tp, "sl": sl,
                "dist": round(dist, digits),
            }
            entries.append(entry)
            req = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": sym,
                "volume": lot,
                "type": otype,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": int(self.cfg.get("slippage_points") or 30),
                "magic": int(self.cfg.get("magic") or 777006),
                "comment": f"bot6_{side[0]}"[:31],
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling_mode(sym),
            }
            try:
                res = mt5.order_send(req)
                retcode = getattr(res, "retcode", 0) if res else 0
            except Exception as exc:
                retcode = 0
                self.last_error = str(exc)
            if retcode in RETCODE_SUCCESS:
                entry["ticket"] = int(getattr(res, "order", 0) or 0)
                entry["state"] = "pending"
                placed += 1
            else:
                err = _RETCODE_NAMES.get(retcode, str(retcode))
                first_err = first_err or err
                self.last_error = f"Pre-trade rejected: {err}"

        self._entries = entries
        self._last_order_ts = time.time()
        if placed:
            self._fail_streak = 0
            self._retry_after = 0.0
            first = next((e for e in entries if e.get("state") == "pending"), {})
            self._log("success", f"Pre-trade placed {placed}x {', '.join(s.upper() for s in sides[:2])} "
                                 f"@ {first.get('price')} ({dist:g} away from the market, lot {lot}, "
                                 f"TP {first.get('tp') or 'off'}, SL {first.get('sl') or 'profit-lock'})")
        else:
            self._fail_streak += 1
            backoff = min(60.0, 10.0 * (2 ** min(self._fail_streak - 1, 3)))
            self._retry_after = time.time() + backoff
            self._log("error", f"Pre-trade not placed ({first_err}) - retrying in {int(backoff)}s")
        return placed

    # ------------------------------------------------------------ bookkeeping
    def _track(self, positions: List[Dict[str, Any]]) -> None:
        current = {int(p["ticket"]): p for p in positions if p.get("ticket")}
        for ticket, p in list(self._known_positions.items()):
            if ticket not in current:
                net = float(p.get("net") or 0.0)
                if ticket in self._self_closed:
                    self._self_closed.discard(ticket)
                else:
                    self.stats["closed"] += 1
                    self.stats["realized"] = round(self.stats["realized"] + net, 2)
                    if net >= 0:
                        self.stats["wins"] += 1
                    else:
                        self.stats["losses"] += 1
                self._pos_meta.pop(ticket, None)
                self._log("trade", f"Position #{ticket} closed ({net:+.2f})")
        for ticket, p in current.items():
            if ticket not in self._known_positions:
                self.stats["opened"] += 1
                self._log("trade", f"Position #{ticket} opened: {p.get('side')} {p.get('volume')} "
                                   f"@ {p.get('price_open')}")
        self._known_positions = current

    def _sync(self, positions: List[Dict[str, Any]],
              orders: List[Dict[str, Any]]) -> None:
        live_orders = {int(o["ticket"]) for o in orders if o.get("ticket")}
        pos_by_ticket = {int(p["ticket"]): p for p in positions if p.get("ticket")}
        point = self._point()
        tol = max(point * 10.0, 1e-9)

        # 1) a pending pre-trade that vanished was either filled or cancelled
        for e in self._entries:
            if e.get("state") != "pending" or not e.get("ticket"):
                continue
            if int(e["ticket"]) in live_orders:
                continue
            e["ticket"] = 0
            pos = self._match_fill(str(e.get("side") or ""), e, pos_by_ticket, tol)
            if pos is not None:
                e["state"] = "filled"
                self._register_fill(pos)
            else:
                e["state"] = "cancelled"
                self._log("info", f"Pre-trade {str(e.get('side') or '').upper()} gone (cancelled)")

        # 2) adopt a bot position we cannot map (restart / manual)
        for t, p in pos_by_ticket.items():
            if t in self._pos_meta:
                continue
            self._pos_meta[t] = {
                "side": p.get("side") or "buy",
                "lot": float(p.get("volume") or 0.0),
                "entry": float(p.get("price_open") or 0.0),
                "managed": True,
            }
            self._log("info", f"Adopted existing position #{t} - TP/SL management active")

    def _match_fill(self, side: str, entry: Dict[str, Any],
                    pos_by_ticket: Dict[int, Dict[str, Any]], tol: float) -> Optional[Dict[str, Any]]:
        tag = f"bot6_{side[0]}" if side else "bot6"
        best: Optional[Dict[str, Any]] = None
        best_d = 1e18
        for t, p in pos_by_ticket.items():
            if t in self._pos_meta:
                continue
            if side and (p.get("side") or "buy") != side:
                continue
            if tag in str(p.get("comment") or ""):
                return p
            d = abs(float(p.get("price_open") or 0.0) - float(entry.get("price") or 0.0))
            if d < best_d:
                best_d, best = d, p
        if best is not None and best_d <= max(tol, 1e-6):
            return best
        return None

    def _register_fill(self, pos: Dict[str, Any]) -> None:
        ticket = int(pos["ticket"])
        self._pos_meta[ticket] = {
            "side": pos.get("side") or "buy",
            "lot": float(pos.get("volume") or 0.0),
            "entry": float(pos.get("price_open") or 0.0),
            "managed": True,
        }
        self._cycle_opened = True
        self.enabled = True
        self._log("trade", f"Pre-trade {str(pos.get('side') or '').upper()} HIT: "
                           f"{pos.get('volume')} @ {pos.get('price_open')}")

    # --------------------------------------------------------- TP / SL engine
    def _apply_protection(self, positions: List[Dict[str, Any]]) -> None:
        """Fixed TP/SL when set; otherwise the 70% profit-lock trail."""
        if not positions or not self._pos_meta:
            return
        now = time.time()
        if (now - self._last_sl_send_ts) < 0.4:      # broker rate-limit guard
            return
        digits = self._digits()
        point = self._point()
        min_dist = self._min_stop_distance()
        try:
            tick = mt5.symbol_info_tick(self.symbol)
        except Exception:
            tick = None
        if not tick:
            return
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)

        tp_money = max(0.0, _f(self.cfg.get("tp"), 0.0))
        sl_money = max(0.0, _f(self.cfg.get("sl"), 0.0))
        lock_pct = min(95.0, max(0.0, _f(self.cfg.get("lock_pct"), 70.0)))
        lock_min = max(0.0, _f(self.cfg.get("lock_min_profit"), 0.80))

        by_ticket = {int(p["ticket"]): p for p in positions if p.get("ticket")}
        for ticket, meta in list(self._pos_meta.items()):
            p = by_ticket.get(ticket)
            if not p:
                continue
            side = meta.get("side") or p.get("side") or "buy"
            is_buy = side == "buy"
            lot = float(p.get("volume") or meta.get("lot") or 0.01)
            entry = float(p.get("price_open") or meta.get("entry") or 0.0)
            cur_sl = float(p.get("sl") or 0.0)
            cur_tp = float(p.get("tp") or 0.0)
            if entry <= 0:
                continue
            mpp = self.money_per_price(lot)
            if mpp <= 0:
                continue

            new_sl = cur_sl
            new_tp = cur_tp

            # ---- take profit: entered value is fixed, 0 = no TP
            if tp_money > 0 and cur_tp <= 0:
                off = self._money_offset(tp_money, lot, digits)
                if off > 0:
                    new_tp = round(entry + off, digits) if is_buy else round(entry - off, digits)

            # ---- stop loss: entered value is fixed, 0 = 70% profit lock
            if sl_money > 0:
                if cur_sl <= 0:
                    off = self._money_offset(sl_money, lot, digits)
                    if off > 0:
                        new_sl = round(entry - off, digits) if is_buy else round(entry + off, digits)
            else:
                net = float(p.get("net") or 0.0)
                if net >= lock_min and lock_min >= 0:
                    lock_money = net * lock_pct / 100.0
                    off = self._money_offset(lock_money, lot, digits)
                    if off > 0:
                        cand = round(entry + off, digits) if is_buy else round(entry - off, digits)
                        # broker minimum distance: clamp to the closest allowed
                        # price instead of skipping (a fast market must still lock)
                        if is_buy:
                            max_allowed = round(bid - min_dist, digits) if (bid and min_dist) else cand
                            cand = min(cand, max_allowed)
                            if cand > entry and (cur_sl <= 0 or cand > cur_sl + point):
                                new_sl = cand
                        else:
                            min_allowed = round(ask + min_dist, digits) if (ask and min_dist) else cand
                            cand = max(cand, min_allowed)
                            if cand < entry and (cur_sl <= 0 or cand < cur_sl - point):
                                new_sl = cand
            if _same(new_sl, cur_sl) and _same(new_tp, cur_tp):
                continue
            if self._modify_sltp(p, new_sl, new_tp):
                self._last_sl_send_ts = time.time()
                bits = []
                if not _same(new_sl, cur_sl):
                    bits.append(f"SL -> {new_sl} ({'fixed' if sl_money > 0 else f'locks {lock_pct:.0f}% of profit'})")
                if not _same(new_tp, cur_tp):
                    bits.append(f"TP -> {new_tp} (fixed)")
                self._log("info", f"{side.upper()} #{ticket} " + " · ".join(bits))

    def _modify_sltp(self, p: Dict[str, Any], new_sl: float, new_tp: float) -> bool:
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": p.get("symbol") or self.symbol,
            "position": int(p.get("ticket") or 0),
            "sl": float(new_sl or 0.0),
            "tp": float(new_tp or 0.0),
            "magic": int(self.cfg.get("magic") or 777006),
        }
        try:
            res = mt5.order_send(req)
        except Exception as exc:
            self._log("error", f"SL/TP modify raised: {exc}")
            return False
        if res is None:
            self._log("error", f"SL/TP modify failed: {mt5.last_error()}")
            return False
        if int(res.retcode) in RETCODE_SUCCESS:
            return True
        self._log("warn", f"SL/TP modify rejected: {_RETCODE_NAMES.get(int(res.retcode), res.retcode)}")
        return False

    # --------------------------------------------------------------- main loop
    def tick(self) -> None:
        if mt5 is None:
            return
        if not self.symbol:
            self.resolve_symbol()
            if not self.symbol:
                return

        if self._symbol_changed:
            self._symbol_changed = False
            self._cleanup_all("market switched")
            if not bool(self.cfg.get("running")):
                return

        positions = self._all_positions()
        orders = self._all_orders()

        self._track(positions)
        self._sync(positions, orders)
        # protect first: an open position is always managed, running or not
        self._apply_protection(positions)
        self.enabled = bool(positions)

        running = bool(self.cfg.get("running"))

        # STOP -> cancel the pre-trade, keep managing an open position
        if not running:
            cur = self._all_orders()
            if cur and (time.time() - self._stop_cancel_ts) >= 5.0:
                self._stop_cancel_ts = time.time()
                self._cancel_orders(cur, why="stopped")
                for e in self._entries:
                    if e.get("state") == "pending":
                        e["state"] = "cancelled"
                        e["ticket"] = 0
            return

        # an open trade owns the cycle - one trade at a time
        if positions:
            return

        # the pre-trade is still parked and waiting for the market
        if any(e.get("state") == "pending" and e.get("ticket") for e in self._entries) and orders:
            return

        # flat: a fresh cycle starts after the previous trades closed
        if self._cycle_opened:
            self._cycle_opened = False
            self._entries = []
            left = self._all_orders()
            if left:
                self._cancel_orders(left, why="cycle closed")

        if self._relayout:
            self._relayout = False
            left = self._all_orders()
            if left:
                self._cancel_orders(left, why="settings changed")

        if time.time() < self._retry_after:
            return
        if (time.time() - self._last_order_ts) < max(_f(self.cfg.get("cooldown_sec"), 2.0), 0.5):
            return

        sides, why = self._decide_sides()
        if not sides:
            if (time.time() - self._wait_log_ts) >= 20.0:
                self._wait_log_ts = time.time()
                self._log("info", f"Waiting: {why}")
            return
        self._place_entry(sides)

    # ------------------------------------------------------------- status
    def status(self) -> Dict[str, Any]:
        positions = self._all_positions()
        orders = self._all_orders()
        if not self.available_symbols_list:
            try:
                self.available_symbols()
            except Exception:
                pass
        floating = round(sum(float(p.get("net") or 0.0) for p in positions), 2)
        lot = _f(self.cfg.get("lot"), 0.01)

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

        entries = [dict(e) for e in self._entries]
        for e in entries:
            e["dist"] = round(float(e.get("price") or 0.0) - cur_price, digits) if cur_price else 0.0
        e = entries[0] if entries else {}

        shown_positions = []
        for p in positions:
            meta = self._pos_meta.get(int(p.get("ticket") or 0)) or {}
            d = dict(p)
            d["locked"] = bool(float(p.get("sl") or 0.0) > 0)
            d["entry"] = float(p.get("price_open") or 0.0)
            d["managed"] = bool(meta.get("managed"))
            shown_positions.append(d)

        lock_pct = float(self.cfg.get("lock_pct") or 70.0)
        sl_money = float(self.cfg.get("sl") or 0.0)
        tp_money = float(self.cfg.get("tp") or 0.0)

        return {
            "running": bool(self.cfg.get("running")),
            "enabled": bool(positions),
            "symbol": self.symbol,
            "symbol_requested": str(self.cfg.get("symbol") or "XAUUSDm"),
            "symbol_ok": bool(self.symbol_ok and self.symbol),
            "symbol_substituted": "",
            "symbol_options": list(self.available_symbols_list),
            "ask": ask,
            "bid": bid,
            "spread": spread,
            "digits": digits,
            "price": cur_price,
            "config": dict(self.cfg),
            "entry": e,
            "entries": entries,
            "pending_count": sum(1 for x in entries if x.get("state") == "pending"),
            "filled_count": sum(1 for x in entries if x.get("state") == "filled"),
            "positions": shown_positions,
            "running_count": len(positions),
            "floating": floating,
            "stats": dict(self.stats),
            "watch": dict(self.watch),
            "ai": dict(self.ai),
            "watch_min_pct": float(self.cfg.get("watch_min_pct") or 60.0),
            "rules": {
                "tp": "fixed" if tp_money > 0 else "off",
                "sl": "fixed" if sl_money > 0 else f"profit-lock {lock_pct:.0f}% (min ${float(self.cfg.get('lock_min_profit') or 0.8):.2f})",
                "tp_money": tp_money,
                "sl_money": sl_money,
                "lock_pct": lock_pct,
                "lock_min_profit": float(self.cfg.get("lock_min_profit") or 0.8),
            },
            "last_action": self.last_action,
            "last_error": self.last_error,
            "volume_min": self.volume_min() if self.symbol else 0.01,
            "volume_max": self.volume_max() if self.symbol else 100.0,
            "volume_step": self.volume_step() if self.symbol else 0.01,
            "money_per_price": round(self.money_per_price(lot), 4) if self.symbol else 0.0,
        }
