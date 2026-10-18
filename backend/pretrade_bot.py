"""
Pre-Trade Ladder Bot (Bot 4).

Strategy Specification
----------------------
1. On START the bot places two ladders of pending pre-trades around the
   current market price:
   - BUY pre-trades ABOVE the price:  first one Price Gap away, then each
     next one Pre Trade Gap further up.
   - SELL pre-trades BELOW the price:  mirrored downward with the same gaps.
   - Pre Trade Value (N) = how many levels each side gets (N buy + N sell).
2. Lot Size is used for every pre-trade. With "Numbering Lot Size" ON the
   lots auto-number 0.01, 0.02, 0.03 ... per level, on BOTH ladders.
3. Pairing: buy #k and sell #k form a pair.
   - Pair #1 may stay hedged: if #1 of one side is hit and later #1 of the
     opposite side is hit, the market just continues.
   - From #2 up: when a side's #k pre-trade is hit, the OPPOSITE side's #k
     is cleared automatically (open position -> closed at market,
     pending order -> cancelled).
4. SL lock cascade: when a side's level k+1 is hit, the open level-k
   position gets its SL set to lock 60% of its current profit (lock_pct).
   The same rule applies to every level as the market walks through the
   ladder, and the SL only ever moves in the profitable direction.
5. When the cycle's trades are all closed the bot cancels whatever is left
   and builds a fresh buy + sell ladder from the new market price.
6. STOP cancels the pre-trades; an already-open position keeps its trailed
   SL until it closes (a position is never left unprotected).

All MetaTrader5 calls happen on the engine thread - this class never calls
MT5 from anywhere else.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - only missing on non-Windows
    import MetaTrader5 as mt5  # type: ignore
except Exception:  # pragma: no cover
    mt5 = None  # type: ignore

DEFAULTS: Dict[str, Any] = {
    "running": False,
    "lot": 0.01,            # same lot for every pre-trade (unless numbering)
    "price_gap": 1.0,       # $ from the market price to the FIRST pre-trade
    "pre_trade_gap": 0.5,   # $ between two successive pre-trade levels
    "pre_trade_value": 5,   # N = levels per side (N buy above + N sell below)
    "numbering_lot": False, # ON -> lots auto-numbered 0.01, 0.02, 0.03 ...
    "symbol": "XAUUSDm",
    "magic": 777004,        # distinct from bot1 (777001), bot2 (777002), bot3 (777003)
    "slippage_points": 30,
    "cooldown_sec": 2.0,
    "lock_pct": 60.0,       # SL locks this % of the current profit
}

# config keys that change the layout of the ladder -> re-place when flat
_LAYOUT_KEYS = ("lot", "price_gap", "pre_trade_gap", "pre_trade_value", "numbering_lot")

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

# order / position ordinals (avoid hard dependency on module constants)
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


class PreTradeBot:
    """Pre-Trade Ladder Bot (Bot 4)."""

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
        # ladder state: {"buy": [entry...], "sell": [entry...]}
        # entry = {"level", "price", "lot", "ticket" (pending order), "state", "pos"}
        self._ladder: Dict[str, List[Dict[str, Any]]] = {"buy": [], "sell": []}
        # position ticket -> {"side", "level", "price", "lot", "armed"}
        self._pos_meta: Dict[int, Dict[str, Any]] = {}
        self._filled: Dict[str, set] = {"buy": set(), "sell": set()}
        self._known_positions: Dict[int, Dict[str, Any]] = {}
        self._self_closed: set = set()
        self._cycle_opened: bool = False
        self._relayout: bool = False
        self._symbol_changed: bool = False
        self._resolve_ts: float = 0.0
        self._last_order_ts: float = 0.0
        self._last_sl_send_ts: float = 0.0
        self._retry_after: float = 0.0      # placement backoff (market closed etc.)
        self._stop_cancel_ts: float = 0.0   # cancel-on-stop backoff
        self._fail_streak: int = 0

    def _log(self, kind: str, message: str) -> None:
        self.last_action = message
        if self.logger:
            try:
                self.logger(kind, f"[Bot4] {message}")
            except Exception:
                pass

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

        # ---- sanitize numeric fields
        self.cfg["lot"] = self._round_lot(_f(self.cfg.get("lot"), 0.01))
        self.cfg["price_gap"] = max(0.0, round(_f(self.cfg.get("price_gap"), 1.0), 6))
        self.cfg["pre_trade_gap"] = max(0.0, round(_f(self.cfg.get("pre_trade_gap"), 0.5), 6))
        try:
            pv = int(float(self.cfg.get("pre_trade_value")))
        except (TypeError, ValueError):
            pv = 5
        self.cfg["pre_trade_value"] = min(50, max(1, pv))
        self.cfg["numbering_lot"] = bool(self.cfg.get("numbering_lot"))
        self.cfg["lock_pct"] = min(95.0, max(0.0, _f(self.cfg.get("lock_pct"), 60.0)))
        self.cfg["cooldown_sec"] = max(0.0, _f(self.cfg.get("cooldown_sec"), 2.0))
        self.cfg["slippage_points"] = max(0, int(_f(self.cfg.get("slippage_points"), 30)))
        self.cfg["magic"] = int(_f(self.cfg.get("magic"), 777004))

        # ---- did the ladder layout change through an explicit patch?
        for k in _LAYOUT_KEYS:
            if patch and k in patch and not _same(patch.get(k), before.get(k)):
                self._relayout = True
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
        """Broker's minimum SL distance from the current price (price units)."""
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

    # ------------------------------------------------------------ exposure
    def _all_positions(self, any_symbol: bool = False) -> List[Dict[str, Any]]:
        if mt5 is None:
            return []
        try:
            raw = mt5.positions_get(symbol=None if any_symbol else self.symbol) or []
        except Exception:
            raw = []
        magic = int(self.cfg.get("magic") or 777004)
        out: List[Dict[str, Any]] = []
        for p in raw:
            d = _as_dict(p)
            if magic and int(d.get("magic") or 0) != magic:
                continue
            t = d.get("type")
            d["side"] = "buy" if t == 0 or str(t).lower() == "buy" else "sell"
            profit = float(d.get("profit") or 0.0)
            swap = float(d.get("swap") or 0.0)
            d["net"] = round(profit + swap, 2)
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
        magic = int(self.cfg.get("magic") or 777004)
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
            "magic": int(self.cfg.get("magic") or 777004),
            "comment": f"bot4_close"[:31],
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
        # ONE line per cancel action - a 100-level ladder must not flood the feed
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
        for side in ("buy", "sell"):
            for e in self._ladder[side]:
                if e["state"] == "pending":
                    e["state"] = "cancelled"
                    e["ticket"] = 0
        self.cfg["running"] = False
        self.enabled = False
        self._relayout = False
        self._log("warn", f"Bot4 Closed all ({closed} positions) [{reason}]")
        return closed

    def _cleanup_all(self, reason: str = "") -> None:
        """Cancel every bot order and close every bot position on ANY symbol."""
        orders = self._all_orders(any_symbol=True)
        if orders:
            self._cancel_orders(orders, why=reason)
        for p in self._all_positions(any_symbol=True):
            self._close_position(p, reason)
        self._ladder = {"buy": [], "sell": []}
        self._pos_meta.clear()
        self._filled = {"buy": set(), "sell": set()}
        self._known_positions.clear()
        self._self_closed.clear()
        self._cycle_opened = False

    # --------------------------------------------------------------- ladder
    def _n(self) -> int:
        try:
            return min(50, max(1, int(self.cfg.get("pre_trade_value") or 5)))
        except (TypeError, ValueError):
            return 5

    def _lot_for_level(self, level: int) -> float:
        if self.cfg.get("numbering_lot"):
            lot = 0.01 * float(level)          # 0.01, 0.02, 0.03 ... per level
        else:
            lot = _f(self.cfg.get("lot"), 0.01)
        return self._round_lot(lot)

    def _place_ladder(self) -> int:
        """Place N buy stops above the market and N sell stops below it."""
        sym = self.symbol
        if mt5 is None or not sym:
            return 0
        try:
            info = mt5.symbol_info(sym)
            tick = mt5.symbol_info_tick(sym)
        except Exception:
            info, tick = None, None
        if not info or not tick:
            return 0
        if not getattr(info, "trade_mode", 1) or not getattr(info, "trade_allowed", True):
            self._log("warn", "trading is disabled for this symbol - ladder not placed")
            return 0
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        if not ask or not bid:
            return 0

        digits = self._digits(sym)
        point = self._point(sym)
        min_dist = self._min_stop_distance(sym)
        gap = max(0.0, _f(self.cfg.get("price_gap"), 1.0))
        step = max(0.0, _f(self.cfg.get("pre_trade_gap"), 0.5))
        n = self._n()

        self._ladder = {"buy": [], "sell": []}
        placed = 0
        total = 0
        first_err = ""
        for i in range(1, n + 1):
            buy_price = round(ask + gap + (i - 1) * step, digits)
            sell_price = round(bid - gap - (i - 1) * step, digits)
            # never sit closer than the broker's minimum stop distance - clamp
            # only the floor so the ladder keeps its spacing between levels
            if min_dist:
                buy_price = max(buy_price, round(ask + min_dist, digits))
                sell_price = min(sell_price, round(bid - min_dist, digits))

            for side, price, otype in (("buy", buy_price, ORDER_TYPE_BUY_STOP),
                                       ("sell", sell_price, ORDER_TYPE_SELL_STOP)):
                lot = self._lot_for_level(i)
                entry: Dict[str, Any] = {
                    "level": i, "price": price, "lot": lot,
                    "ticket": 0, "state": "failed", "pos": 0,
                }
                self._ladder[side].append(entry)
                total += 1
                req = {
                    "action": mt5.TRADE_ACTION_PENDING,
                    "symbol": sym,
                    "volume": lot,
                    "type": otype,
                    "price": price,
                    "sl": 0.0,
                    "tp": 0.0,
                    "deviation": int(self.cfg.get("slippage_points") or 30),
                    "magic": int(self.cfg.get("magic") or 777004),
                    "comment": f"bot4_{side[0]}{i}"[:31],
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

        self._last_order_ts = time.time()
        if placed:
            self._fail_streak = 0
            self._retry_after = 0.0
            self._log("success", f"Ladder placed: {placed} pre-trades "
                                 f"(Price Gap {gap}, Pre Trade Gap {step}, N={n})"
                                 + (" · numbered lots" if self.cfg.get("numbering_lot") else f" · lot {self._lot_for_level(1)}"))
            if placed < total:
                self._log("warn", f"{total - placed} pre-trade(s) rejected: {first_err}")
        else:
            # market closed / trading disabled: ONE summary line and back off
            # (15s -> 30s -> 60s) so the event feed is not flooded
            self._fail_streak += 1
            backoff = min(60.0, 15.0 * (2 ** min(self._fail_streak - 1, 3)))
            self._retry_after = time.time() + backoff
            self._log("error", f"Ladder not placed: all {total} pre-trades rejected "
                               f"({first_err}) - retrying in {int(backoff)}s")
        return placed

    # ------------------------------------------------------------ bookkeeping
    def _track(self, positions: List[Dict[str, Any]]) -> None:
        """Stats for opened / externally closed positions + ladder state updates."""
        current = {int(p["ticket"]): p for p in positions if p.get("ticket")}
        for ticket, p in list(self._known_positions.items()):
            if ticket not in current:
                net = float(p.get("net") or 0.0)
                if ticket in self._self_closed:
                    self._self_closed.discard(ticket)     # we closed it ourselves
                else:
                    self.stats["closed"] += 1
                    self.stats["realized"] = round(self.stats["realized"] + net, 2)
                    if net >= 0:
                        self.stats["wins"] += 1
                    else:
                        self.stats["losses"] += 1
                meta = self._pos_meta.pop(ticket, None)
                if meta:
                    for e in self._ladder.get(meta["side"], []):
                        if e["level"] == meta["level"] and e["pos"] == ticket:
                            e["state"] = "closed"
                            e["pos"] = 0
                self._log("trade", f"Position #{ticket} closed ({net:+.2f})")
        for ticket, p in current.items():
            if ticket not in self._known_positions:
                self.stats["opened"] += 1
                self._log("trade", f"Position #{ticket} opened: {p.get('side')} {p.get('volume')} "
                                   f"@ {p.get('price_open')}")
        self._known_positions = current

    def _sync(self, positions: List[Dict[str, Any]], orders: List[Dict[str, Any]]) -> None:
        """Detect filled pre-trades and apply the pair rules + lock cascade."""
        live_orders = {int(o["ticket"]) for o in orders if o.get("ticket")}
        pos_by_ticket = {int(p["ticket"]): p for p in positions if p.get("ticket")}
        point = self._point()
        tol = max(point * 10.0, 1e-9)

        # 1) vanished pending orders -> filled (matching position) or cancelled
        for side in ("buy", "sell"):
            for e in self._ladder[side]:
                if e["state"] != "pending" or not e["ticket"]:
                    continue
                if e["ticket"] in live_orders:
                    continue
                e["ticket"] = 0
                pos = self._match_fill(side, e, pos_by_ticket, tol)
                if pos is not None:
                    e["state"] = "filled"
                    e["pos"] = int(pos["ticket"])
                    self._register_fill(side, e["level"], pos, positions)
                else:
                    e["state"] = "cancelled"
                    self._log("info", f"Pre-trade {side.upper()} #{e['level']} gone (cancelled)")

        # 2) a bot position we cannot map (restart / manual) -> adopt it safely
        for t, p in pos_by_ticket.items():
            if t in self._pos_meta:
                continue
            self._pos_meta[t] = {
                "side": p.get("side") or "buy", "level": 0,
                "price": float(p.get("price_open") or 0.0),
                "lot": float(p.get("volume") or 0.0), "armed": True,
            }
            self._log("info", f"Adopted existing position #{t} (unknown level) - SL lock active")

        # 3) once a side cannot go deeper, its deepest open level locks too
        self._refresh_locks()

    def _match_fill(self, side: str, entry: Dict[str, Any],
                    pos_by_ticket: Dict[int, Dict[str, Any]], tol: float) -> Optional[Dict[str, Any]]:
        tag = f"bot4_{side[0]}{entry['level']}"
        best: Optional[Dict[str, Any]] = None
        best_d = 1e18
        for t, p in pos_by_ticket.items():
            if t in self._pos_meta:
                continue
            if (p.get("side") or "buy") != side:
                continue
            if tag in str(p.get("comment") or ""):
                return p
            d = abs(float(p.get("price_open") or 0.0) - float(entry["price"]))
            if d < best_d:
                best_d, best = d, p
        if best is not None and best_d <= tol:
            return best
        return None

    def _register_fill(self, side: str, level: int, pos: Dict[str, Any],
                       positions: List[Dict[str, Any]]) -> None:
        ticket = int(pos["ticket"])
        self._pos_meta[ticket] = {
            "side": side, "level": level,
            "price": float(pos.get("price_open") or 0.0),
            "lot": float(pos.get("volume") or 0.0), "armed": False,
        }
        self._filled[side].add(level)
        self._cycle_opened = True
        self._log("trade", f"Pre-trade {side.upper()} #{level} HIT: {pos.get('volume')} @ {pos.get('price_open')}")
        self._on_fill(side, level, positions)

    def _open_ticket(self, side: str, level: int) -> int:
        for t, m in self._pos_meta.items():
            if m.get("side") == side and int(m.get("level") or 0) == level:
                return t
        return 0

    def _on_fill(self, side: str, level: int, positions: List[Dict[str, Any]]) -> None:
        opp = "sell" if side == "buy" else "buy"

        # ---- pair rule: from #2 up the opposite side of the SAME pair clears.
        #      (pair #1 may stay hedged - only #2 and deeper behave this way)
        if level >= 2:
            opp_ticket = self._open_ticket(opp, level)
            if opp_ticket:
                pos = next((p for p in positions if int(p.get("ticket") or 0) == opp_ticket), None)
                if pos:
                    if self._close_position(pos, f"pair #{level} opposite hit"):
                        self._log("info", f"Pair #{level}: opposite {opp.upper()} #{level} "
                                          f"closed automatically")
            else:
                # the opposite side may still be a pending stop order - the
                # pairing rule says it must be cleared, otherwise both sides
                # can end up filled
                opp_entry = next((e for e in self._ladder[opp]
                                  if e["level"] == level and e["state"] == "pending"), None)
                if opp_entry and opp_entry.get("ticket"):
                    if self._cancel_orders([opp_entry], why=f"pair #{level} opposite hit"):
                        opp_entry["state"] = "cancelled"
                        opp_entry["ticket"] = 0
                        self._log("info", f"Pair #{level}: opposite {opp.upper()} #{level} "
                                          f"pending order cancelled")

        # ---- SL lock cascade: level k hit -> lock the open level below it
        lower = [lv for lv in self._filled.get(side, set()) if lv < level]
        if lower:
            prev_level = max(lower)
            prev_ticket = self._open_ticket(side, prev_level)
            if prev_ticket and prev_ticket in self._pos_meta and not self._pos_meta[prev_ticket]["armed"]:
                self._pos_meta[prev_ticket]["armed"] = True
                self._log("info", f"{side.upper()} #{prev_level} SL armed: locks "
                                  f"{self.cfg.get('lock_pct', 60):.0f}% of profit (#{level} hit)")
        self._refresh_locks()

    def _refresh_locks(self) -> None:
        """Deepest filled level of each side locks once nothing deeper waits."""
        for side in ("buy", "sell"):
            filled = self._filled.get(side) or set()
            if not filled:
                continue
            deepest = max(filled)
            deeper_waiting = any(e["state"] == "pending" and e["level"] > deepest
                                 for e in self._ladder[side])
            if deeper_waiting:
                continue
            t = self._open_ticket(side, deepest)
            if t and t in self._pos_meta and not self._pos_meta[t]["armed"]:
                self._pos_meta[t]["armed"] = True
                self._log("info", f"{side.upper()} #{deepest} SL armed: locks "
                                  f"{self.cfg.get('lock_pct', 60):.0f}% of profit (deepest level)")

    def _apply_locks(self, positions: List[Dict[str, Any]]) -> None:
        """SL = lock_pct% of the current profit, only ever moved forward."""
        armed = {t: m for t, m in self._pos_meta.items() if m.get("armed")}
        if not armed or not positions:
            return
        now = time.time()
        if (now - self._last_sl_send_ts) < 0.4:      # broker rate-limit guard
            return
        pos_by_ticket = {int(p["ticket"]): p for p in positions if p.get("ticket")}
        pct = _f(self.cfg.get("lock_pct"), 60.0)
        digits = self._digits()
        point = self._point()
        try:
            tick = mt5.symbol_info_tick(self.symbol)
        except Exception:
            tick = None
        if not tick:
            return
        min_dist = self._min_stop_distance()

        for ticket, meta in armed.items():
            p = pos_by_ticket.get(ticket)
            if not p:
                continue
            net = float(p.get("net") or 0.0)
            if net <= 0:
                continue
            lot = float(p.get("volume") or meta.get("lot") or 0.01)
            mpp = self.money_per_price(lot)
            if mpp <= 0:
                continue
            entry = float(p.get("price_open") or meta.get("price") or 0.0)
            if not entry:
                continue
            lock_money = net * pct / 100.0
            offset = round(lock_money / mpp, digits)
            if offset <= 0:
                continue
            is_buy = (meta.get("side") == "buy")
            cur_sl = float(p.get("sl") or 0.0)
            if is_buy:
                new_sl = round(entry + offset, digits)
                if cur_sl > 0 and new_sl <= cur_sl + point:      # monotonic
                    continue
                if new_sl <= entry:
                    continue
                # broker distance: clamp to the closest allowed price instead
                # of skipping - in a fast market the lock would otherwise
                # silently never be placed
                max_allowed = round(float(tick.bid) - min_dist, digits)
                if new_sl > max_allowed:
                    new_sl = max_allowed
                    if cur_sl > 0 and new_sl <= cur_sl + point:
                        continue
                    if new_sl <= entry:
                        continue
            else:
                new_sl = round(entry - offset, digits)
                if cur_sl > 0 and new_sl >= cur_sl - point:
                    continue
                if new_sl >= entry:
                    continue
                min_allowed = round(float(tick.ask) + min_dist, digits)
                if new_sl < min_allowed:
                    new_sl = min_allowed
                    if cur_sl > 0 and new_sl >= cur_sl - point:
                        continue
                    if new_sl >= entry:
                        continue
            if self._modify_sl(p, new_sl):
                self._last_sl_send_ts = time.time()
                self._log("info", f"SL lock {meta['side']} #{meta.get('level') or '?'} -> {new_sl} "
                                  f"(profit {net:+.2f}, locking {lock_money:.2f})")

    def _modify_sl(self, p: Dict[str, Any], new_sl: float) -> bool:
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": p.get("symbol") or self.symbol,
            "position": int(p.get("ticket") or 0),
            "sl": float(new_sl),
            "tp": float(p.get("tp") or 0.0),
            "magic": int(self.cfg.get("magic") or 777004),
        }
        try:
            res = mt5.order_send(req)
        except Exception as exc:
            self._log("error", f"SL modify raised: {exc}")
            return False
        if res is None:
            self._log("error", f"SL modify failed: {mt5.last_error()}")
            return False
        if int(res.retcode) in RETCODE_SUCCESS:
            return True
        self._log("warn", f"SL modify rejected: {_RETCODE_NAMES.get(int(res.retcode), res.retcode)}")
        return False

    # --------------------------------------------------------------- main loop
    def tick(self) -> None:
        if mt5 is None:
            return
        if not self.symbol:
            self.resolve_symbol()
            if not self.symbol:
                return

        # the dashboard switched market -> clear the OLD instrument first
        if self._symbol_changed:
            self._symbol_changed = False
            self._cleanup_all("market switched")
            if not bool(self.cfg.get("running")):
                return

        positions = self._all_positions()
        orders = self._all_orders()

        # 1) stats: opened / externally closed positions
        self._track(positions)

        # 2) fills -> pair rules -> arm the 60% SL lock cascade
        self._sync(positions, orders)

        # 3) protect first: armed positions keep their trailing SL
        #    (also after STOP - a position is never left unprotected)
        self._apply_locks(positions)

        running = bool(self.cfg.get("running"))

        # 4) STOP -> cancel the pre-trades, keep open positions managed
        #    (a rejected cancel is retried with a short backoff, not every tick)
        if not running:
            cur = self._all_orders()
            if cur and (time.time() - self._stop_cancel_ts) >= 5.0:
                self._stop_cancel_ts = time.time()
                self._cancel_orders(cur, why="stopped")
            self.enabled = bool(positions)
            return

        # 5) ladder maintenance
        if positions:
            self._cycle_opened = True
            self.enabled = True
            return

        self.enabled = False
        if self._cycle_opened:
            # rule 16: the cycle's trades closed -> fresh ladder from the
            # new market price
            self._relayout = False
            left = self._all_orders()
            if left:
                self._cancel_orders(left, why="cycle closed - rebuild")
            self._reset_cycle()
            self._place_ladder()
            return

        if self._relayout:
            self._relayout = False
            cur = self._all_orders()
            if cur:
                self._cancel_orders(cur, why="settings changed")
            self._reset_cycle()
            self._place_ladder()
            return

        if not self._all_orders() and not self._has_pending_entries():
            # ladder missing (failed placement / cancelled by broker) -> retry
            if time.time() >= self._retry_after and \
                    (time.time() - self._last_order_ts) >= max(_f(self.cfg.get("cooldown_sec"), 2.0), 0.5):
                self._place_ladder()

    def _has_pending_entries(self) -> bool:
        return any(e["state"] == "pending" and e.get("ticket")
                   for e in self._ladder["buy"] + self._ladder["sell"])

    def _reset_cycle(self) -> None:
        self._ladder = {"buy": [], "sell": []}
        self._pos_meta.clear()
        self._filled = {"buy": set(), "sell": set()}
        self._known_positions = {}
        self._self_closed.clear()
        self._cycle_opened = False

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

        def _view(side: str) -> List[Dict[str, Any]]:
            out = []
            for e in self._ladder.get(side, []):
                out.append({
                    "level": e["level"], "price": e["price"], "lot": e["lot"],
                    "state": e["state"], "pos": e.get("pos") or 0,
                    "armed": bool((self._pos_meta.get(int(e.get("pos") or 0)) or {}).get("armed")),
                })
            return out

        buy_view, sell_view = _view("buy"), _view("sell")
        pending = sum(1 for e in (buy_view + sell_view) if e["state"] == "pending")
        filled = sum(1 for e in (buy_view + sell_view) if e["state"] == "filled")

        shown_positions = []
        for p in positions:
            meta = self._pos_meta.get(int(p.get("ticket") or 0)) or {}
            d = dict(p)
            d["level"] = meta.get("level")
            d["armed"] = bool(meta.get("armed"))
            shown_positions.append(d)

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
            "ladder": {
                "buy": buy_view,
                "sell": sell_view,
                "value": self._n(),
                "price_gap": _f(self.cfg.get("price_gap"), 1.0),
                "pre_trade_gap": _f(self.cfg.get("pre_trade_gap"), 0.5),
                "numbering_lot": bool(self.cfg.get("numbering_lot")),
                "anchor": cur_price,
            },
            "pending_count": pending,
            "filled_count": filled,
            "positions": shown_positions,
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
