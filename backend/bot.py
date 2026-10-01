"""
Gold auto-trading bot (XAUUSD only).

Strategy the user asked for
---------------------------
ARM (pre-trade straddle, placed around the current market price):
    Buy Stop  @ ask + D      with SL = bid - D
    Sell Stop @ bid - D      with SL = ask + D
  -> each side's stop-loss sits on the opposite pending-order level (2*D away).

MANAGE (as soon as one of them triggers):
    * profit >= lock_trigger (default 1.00 money)   -> SL locks lock_amount (0.10)
    * profit >= trail_start  (default 1.50 money)   -> SL = entry +/- profit/2,
      re-computed on every tick and only ever moved in the profitable direction
      (sell: down, buy: up).

STOP: pending orders are cancelled, an already-open position is kept and its
stop-loss keeps being trailed until it closes (a position is never left
unprotected).

All MetaTrader5 calls happen on the engine thread - this class never calls MT5
from anywhere else.
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
    "lot": 0.01,
    "distance": 2.0,        # "gap" in dollars above/below the market
    "sl_distance": 0.0,     # initial SL distance from entry (0 = auto: 2 x gap)
    "lock_trigger": 0.4,    # from $0.40 profit the SL locks lock_amount
    "lock_amount": 0.2,     # ...which is $0.20
    "trail_start": 0.8,     # from $0.80 profit the SL follows trail_pct
    "trail_pct": 75.0,      # 75% of the profit (SL never moves back)
    "symbol": "XAUUSDm",    # gold only - this exact symbol by default
    "magic": 777001,
    "max_spread": 0.0,      # 0 = no spread guard
    "cooldown_sec": 1,      # re-arm, essentially immediately, after a close
    "arm_on_candle": True,  # open the pre-trade at the start of each new M1 candle
    "slippage_points": 30,
}

DEFAULT_SYMBOL = "XAUUSDm"

GOLD_CANDIDATES = [
    "XAUUSDm", "XAUUSD", "XAUUSD247", "XAUUSDc", "XAUUSDz", "XAUUSD.", "XAUUSD_i", "XAUUSD_",
    "GOLD", "GOLDm",
]

PREFERRED_SYMBOLS = [
    "XAUUSDm", "XAUUSD247m", "XAUUSD247", "XAUUSD",
    "BTCUSDTm", "BTCUSDm", "BTCUSD",
    "DXY", "DXYm", "USDX", "USDXm",
    "USOILm", "USOIL", "XTIUSDm", "XTIUSD", "CLm", "CL",
    "XAUGBPm", "XAUGBP",
    "USDJPYm", "USDJPY",
    "EURUSDm", "EURUSD",
    "USTECm", "USTEC", "NAS100m", "NAS100",
]

# order / position ordinals (avoid hard dependency on module constants)
ORDER_TYPE_BUY_STOP = 4
ORDER_TYPE_SELL_STOP = 5
ORDER_STATE_PLACED = 1
ORDER_STATE_PARTIAL = 3
DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1

_OK_RETCODES = {10008, 10009, 10010, 10011, 10012, 10013}   # PLACED, DONE, DONE_PARTIAL, ERROR, TIMEOUT, INVALID, INVALID_VOLUME?
_RETCODE_NAMES = {
    10004: "requote", 10006: "rejected", 10007: "cancelled", 10008: "placed", 10009: "done",
    10010: "done partially", 10011: "error", 10012: "timeout", 10013: "invalid request",
    10014: "invalid volume", 10015: "invalid price", 10016: "invalid stops", 10017: "trading disabled",
    10018: "market closed", 10019: "no money", 10020: "price changed", 10021: "price off",
    10022: "invalid expiration", 10023: "order changed", 10024: "too many requests",
    10025: "no changes", 10026: "server disabled", 10027: "client disabled",
    10030: "invalid fill", 10031: "connection lost", 10033: "limit orders only",
    10034: "volume limit", 10036: "position closed", 10038: "close only", 10039: "fifo close only",
    10040: "hedge prohibited", 10041: "prohibited by fifo rule",
}
# only these count as "the request reached the trade server successfully"
RETCODE_SUCCESS = {10008, 10009, 10010}


def _tname(retcode: int) -> str:
    return _RETCODE_NAMES.get(int(retcode), f"retcode {retcode}")


def _as_dict(obj: Any) -> Dict[str, Any]:
    """MT5 returns namedtuples; accept dicts / plain objects too."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    asdict = getattr(obj, "_asdict", None)
    if callable(asdict):
        return dict(asdict())
    try:
        return dict(obj)
    except (TypeError, ValueError):
        return dict(vars(obj))


class GoldBot:
    """The strategy. One instance lives on the engine thread."""

    def __init__(self, logger=None) -> None:
        self.cfg: Dict[str, Any] = dict(DEFAULTS)
        self.logger = logger
        self.symbol: str = ""
        self.symbol_ok: bool = False        # is the requested symbol really there?
        self.symbol_substituted: str = ""   # set when XAUUSDm -> XAUUSD fallback was used
        self.gold_symbols: List[str] = []   # gold symbols the broker offers
        self._gold_ts: float = 0.0
        self.enabled: bool = False          # True while there is something to manage
        self.last_action: str = ""
        self.last_error: str = ""
        self.armed_at: float = 0.0
        self.cooldown_until: float = 0.0
        self.stats: Dict[str, Any] = {"opened": 0, "closed": 0, "wins": 0, "losses": 0, "realized": 0.0}
        self._known_positions: Dict[int, Dict[str, Any]] = {}
        self._last_sl: Dict[int, float] = {}
        self._self_closed: set = set()      # tickets WE closed (cycle reset / panic)
        self._reset_until: float = 0.0      # stop-out cleanup window
        self._symbol_changed: bool = False  # market switched on the dashboard
        self._symbol_explicit: bool = False  # operator picked this exact market
        self._last_bar: int = 0             # last M1 candle we armed on
        self._resolve_ts: float = 0.0
        self._error_count: int = 0

    # ------------------------------------------------------------------ util
    def _log(self, kind: str, message: str) -> None:
        self.last_action = message
        if self.logger:
            try:
                self.logger(kind, message)
            except Exception:
                pass

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        old_symbol = str(self.cfg.get("symbol") or "").strip()
        for k, v in (patch or {}).items():
            if k in DEFAULTS:
                self.cfg[k] = v
        if not str(self.cfg.get("symbol") or "").strip():
            self.cfg["symbol"] = DEFAULT_SYMBOL
        new_symbol = str(self.cfg.get("symbol") or "").strip()
        sym_differs_old = bool(new_symbol and old_symbol and old_symbol.upper() != new_symbol.upper())
        sym_differs_cur = bool(new_symbol and self.symbol and self.symbol.upper() != new_symbol.upper())
        if sym_differs_old or sym_differs_cur:
            # switching market: drop the old instrument's orders, never swap an
            # explicit pick, and force a fresh symbol lookup (the 30 s cache
            # would otherwise keep trading the OLD market)
            self._symbol_changed = True
            self._symbol_explicit = True
            self.symbol = ""
            self.symbol_ok = False
            self._resolve_ts = 0.0
            self._last_bar = 0
        # numeric sanity
        self.cfg["lot"] = self._round_lot(float(self.cfg.get("lot") or 0.01))
        self.cfg["distance"] = max(float(self.cfg.get("distance") or 2.0), 0.1)
        self.cfg["sl_distance"] = max(float(self.cfg.get("sl_distance") or 0.0), 0.0)
        self.cfg["lock_trigger"] = max(float(self.cfg.get("lock_trigger") or 0.0), 0.0)
        self.cfg["lock_amount"] = max(float(self.cfg.get("lock_amount") or 0.0), 0.0)
        self.cfg["trail_start"] = max(float(self.cfg.get("trail_start") or 0.0), 0.0)
        return dict(self.cfg)

    def _round_lot(self, lot: float) -> float:
        step = self.volume_step()
        if step <= 0:
            step = 0.01
        lot = round(round(lot / step) * step, 8)
        return max(self.volume_min(), min(lot, self.volume_max()))

    # ------------------------------------------------------------ symbol info
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

    def available_gold_symbols(self, force: bool = False) -> List[str]:
        """Gold + BTC symbols this broker actually offers (cached 60 s)."""
        if mt5 is None:
            return []
        now = time.time()
        if self.gold_symbols and not force and (now - self._gold_ts) < 60:
            return list(self.gold_symbols)
        out: List[str] = []
        try:
            allsym = mt5.symbols_get()
        except Exception:
            allsym = None
        for s in allsym or []:
            name = str(getattr(s, "name", "") or "")
            if self._is_supported_symbol(name):
                out.append(name)

        def rank(n: str) -> tuple:
            up = n.upper()
            if up in PREFERRED_SYMBOLS:
                return (PREFERRED_SYMBOLS.index(up), 0, n)
            metal = 0 if ("XAU" in up or "GOLD" in up) else 1
            return (len(PREFERRED_SYMBOLS), metal, n)

        out.sort(key=rank)
        self.gold_symbols = out[:25]
        self._gold_ts = now
        return list(self.gold_symbols)

    @staticmethod
    def _is_supported_symbol(name: str) -> bool:
        """Symbols supported for trading on Bot 1."""
        up = (name or "").upper()
        if not up or "XAG" in up or "ETH" in up:
            return False
        if up in PREFERRED_SYMBOLS:
            return True
        return any(base in up for base in (
            "XAUUSD", "BTCUSD", "BTCUSDT", "GOLD",
            "DXY", "USDX",
            "USOIL", "XTIUSD", "CL",
            "XAUGBP", "USDJPY", "EURUSD",
            "USTEC", "NAS100", "US100"
        ))

    def resolve_symbol(self, force: bool = False) -> str:
        """Trade one exact symbol only (XAUUSDm by default).

        If that symbol is not on the account we do NOT silently trade a
        different one - we report it, together with the gold symbols the broker
        does offer, so it can be picked from the dashboard.
        """
        if mt5 is None:
            return ""
        now = time.time()
        if not force and (now - self._resolve_ts) < 30:
            return self.symbol          # "" is a cached "not available" answer too
        wanted = str(self.cfg.get("symbol") or DEFAULT_SYMBOL).strip() or DEFAULT_SYMBOL
        # if the operator picked this market himself (or it is not the default),
        # never silently swap it for a look-alike name
        explicit = self._symbol_explicit or (wanted.upper() != DEFAULT_SYMBOL.upper())
        try:
            info = mt5.symbol_info(wanted)
        except Exception:
            info = None
        if info is None and not explicit:
            # same instrument under another spelling? (XAUUSDm -> XAUUSD)
            sub = self._equivalent_symbol(wanted)
            if sub:
                try:
                    info = mt5.symbol_info(sub)
                except Exception:
                    info = None
            if info is not None:
                if not info.visible:
                    try:
                        mt5.symbol_select(sub, True)
                    except Exception:
                        pass
                self.symbol = sub
                self.symbol_ok = True
                self.symbol_substituted = wanted
                self._resolve_ts = now
                self.last_error = ""
                self._log("warn", f"{wanted} is not on this account - using {sub} (same market)")
                return sub
        if info is None:
            # explicitly chosen market that does not exist (or no equivalent):
            # refuse to trade and say what IS available - never swap silently
            avail = self.available_gold_symbols(force=True)
            self.symbol = ""
            self.symbol_ok = False
            self.symbol_substituted = ""
            self._resolve_ts = now
            note = f" - available: {', '.join(avail[:8])}" if avail else ""
            self.last_error = f"{wanted} is not available on this account{note}"
            return ""
        if not info.visible:
            try:
                mt5.symbol_select(wanted, True)
            except Exception:
                pass
        self.symbol = wanted
        self.symbol_ok = True
        self.symbol_substituted = ""
        self._resolve_ts = now
        if self.last_error.startswith(wanted + " is not available"):
            self.last_error = ""
        return wanted

    def _equivalent_symbol(self, wanted: str) -> str:
        """XAUUSDm -> XAUUSD and similar (same instrument, gold vs USD)."""
        cands: List[str] = []
        w = wanted.strip()
        if w.lower().endswith("m") and len(w) > 1:
            cands.append(w[:-1])
        if w.lower().endswith(".m"):
            cands.append(w[:-2])
        base = w.split(".")[0].split("_")[0].split("m")[0]
        for c in (base, base + "z", base + "c", base + ".", base + "_i"):
            if c and c not in cands and c != w:
                cands.append(c)
        for c in cands:
            try:
                if mt5.symbol_info(c) is not None:
                    return c
            except Exception:
                continue
        return ""

    def _digits(self, sym: str) -> int:
        try:
            return int(mt5.symbol_info(sym).digits)
        except Exception:
            return 2

    def _point(self, sym: str) -> float:
        try:
            return float(mt5.symbol_info(sym).point) or 0.01
        except Exception:
            return 0.01

    def _min_stop_distance(self, sym: str) -> float:
        """Broker's minimum SL distance from the current price (price units)."""
        try:
            info = mt5.symbol_info(sym)
            stops = max(int(getattr(info, "trade_stops_level", 0) or 0),
                        int(getattr(info, "trade_freeze_level", 0) or 0))
            return stops * self._point(sym)
        except Exception:
            return 0.0

    def money_per_price(self, volume: float, sym: str = "") -> float:
        """Money (account currency) gained per 1.0 of price movement."""
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

    def effective_sl_distance(self) -> float:
        """Initial SL distance from entry: the SL box, or 2 x gap when unset."""
        sl = float(self.cfg.get("sl_distance") or 0.0)
        if sl > 0:
            return sl
        return 2.0 * float(self.cfg.get("distance") or 2.0)
    def risk_per_trade(self, sym: str = "") -> float:
        """Money at risk if the initial stop-loss is hit."""
        sym = sym or self.symbol
        if not sym:
            return 0.0
        mpp = self.money_per_price(float(self.cfg.get("lot") or 0.01), sym)
        return round(self.effective_sl_distance() * mpp, 2)

    def _filling_mode(self, sym: str) -> int:
        """Pick a filling mode the symbol actually allows."""
        try:
            info = mt5.symbol_info(sym)
            mode = int(info.filling_mode)
        except Exception:
            return 0
        # bit flags: 1 = FOK, 2 = IOC
        if mode & 1:
            return 0   # ORDER_FILLING_FOK
        if mode & 2:
            return 1   # ORDER_FILLING_IOC
        return 2       # ORDER_FILLING_RETURN

    def _pending_filling(self, sym: str) -> int:
        # pending orders on market-execution symbols must use RETURN
        return 2

    # ------------------------------------------------------------- exposure
    def _all_positions(self, any_symbol: bool = False) -> List[Dict[str, Any]]:
        if mt5 is None:
            return []
        try:
            raw = mt5.positions_get()
        except Exception:
            return []
        out = []
        magic = int(self.cfg.get("magic") or 0)
        for p in raw or []:
            d = _as_dict(p)
            if magic and int(d.get("magic") or 0) != magic:
                continue
            if not any_symbol and self.symbol and d.get("symbol") != self.symbol:
                continue
            d["side"] = "buy" if int(d.get("type", 0)) == 0 else "sell"
            d["profit"] = float(d.get("profit") or 0.0)
            d["swap"] = float(d.get("swap") or 0.0)
            d["net"] = round(d["profit"] + d["swap"], 2)
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
        out = []
        magic = int(self.cfg.get("magic") or 0)
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

    def has_exposure(self) -> bool:
        return bool(self._all_positions() or self._all_orders())

    def has_open_position(self) -> bool:
        return bool(self._all_positions())

    def active_management(self) -> bool:
        """True when we must keep re-checking at high frequency."""
        if not self.enabled:
            return False
        return bool(self._all_positions())

    # ------------------------------------------------------------------ tick
    def tick(self) -> None:
        if mt5 is None or not self.symbol:
            if not self.resolve_symbol():
                return

        # 0) the dashboard switched market -> clear the OLD instrument first,
        #    otherwise its pending orders would keep trading on their own
        if self._symbol_changed:
            self._symbol_changed = False
            self._cleanup_all("market switched")
            if not bool(self.cfg.get("running")):
                return

        positions = self._all_positions()
        orders = self._all_orders()

        # safety: never run Buy and Sell at the same time. If both legs filled
        # together, wipe them and arm a fresh straddle (same rule as an SL hit).
        if len(positions) > 1:
            self._log("warn", f"{len(positions)} positions open at once -> closing both, re-arming fresh")
            self._reset_until = time.time() + 5.0
            self._reset_cycle(positions, orders)
            return

        # 1) protect first - never leave an open position unmanaged
        for p in positions:
            try:
                self._manage_position(p)
            except Exception as exc:  # pragma: no cover
                self._log("error", f"SL manage failed: {exc}")

        # 2) bookkeeping for closed positions (a stop-out restarts the cycle)
        closed_any = self._track(positions)

        running = bool(self.cfg.get("running"))

        # 3) user pressed STOP -> drop the pending orders, keep trailing the position
        if not running:
            if orders:
                self._cancel_orders(orders, why="stopped")
            self.enabled = bool(positions)
            return

        # 4) STOP-OUT: clear the whole old buy/sell setup (the leftover pending
        #    order *and* the opposite position that just opened), then arm fresh
        #    from the live price on the next tick.
        if closed_any:
            self._reset_until = time.time() + 5.0
            self._log("warn", "SL hit -> clearing the old buy/sell setup")
        if time.time() < self._reset_until:
            if positions or orders:
                self._reset_cycle(positions, orders)
                return
            self._reset_until = 0.0

        # 5) arm / re-arm when flat
        if positions or orders:
            self.enabled = True
            return
        if time.time() < self.cooldown_until:
            return
        # one pre-trade per new 1-minute candle
        if self.cfg.get("arm_on_candle", True):
            bar = self._m1_bar()
            if bar and bar == self._last_bar:
                return                      # wait for the next candle to open
            self._last_bar = bar
        self._arm()

    def _m1_bar(self) -> int:
        """Open time of the current M1 candle (broker clock)."""
        if mt5 is None or not self.symbol:
            return 0
        try:
            tick = mt5.symbol_info_tick(self.symbol)
        except Exception:
            tick = None
        if not tick:
            return 0
        t = int(getattr(tick, "time", 0) or time.time())
        return (t // 60) * 60

    def _cleanup_all(self, reason: str = "") -> None:
        """Cancel every bot order and close every bot position on ANY symbol."""
        orders = self._all_orders(any_symbol=True)
        if orders:
            self._cancel_orders(orders, why=reason)
        for p in self._all_positions(any_symbol=True):
            self._self_closed.add(int(p.get("ticket") or 0))
            self._close_position(p, reason)
        self._known_positions.clear()
        self._reset_until = 0.0
        self.cooldown_until = 0.0
        if orders:
            self._log("warn", f"{reason}: {len(orders)} old pending order(s) cancelled")

    def _reset_cycle(self, positions: List[Dict[str, Any]], orders: List[Dict[str, Any]]) -> None:
        """Wipe everything left from the previous buy/sell cycle."""
        if orders:
            self._cancel_orders(orders, why="SL hit - cycle reset")
        for p in positions:
            self._self_closed.add(int(p.get("ticket") or 0))
            self._close_position(p, "cycle reset")
        self.cooldown_until = 0.0

    def _close_position(self, p: Dict[str, Any], reason: str = "") -> bool:
        """Close one position at market (used by the reset and the panic button)."""
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
            "type": DEAL_TYPE_SELL if is_buy else DEAL_TYPE_BUY,
            "price": price,
            "deviation": int(self.cfg.get("slippage_points") or 30),
            "magic": int(self.cfg.get("magic") or 0),
            "comment": f"bot {reason}"[:31],
            "type_filling": self._filling_mode(sym),
        }
        return self._send(req, f"Close #{p['ticket']} {p.get('side')} {p.get('volume')} "
                               f"@ {price} ({reason})")

    # ------------------------------------------------------------- SL rules
    def desired_lock(self, profit: float) -> float:
        """How much money the SL should lock at this profit level.

        $0.40 .. $0.80 -> fixed $0.20
        $0.80 and up   -> 75% of the profit, always the highest reached
        """
        trail_start = float(self.cfg.get("trail_start") or 0.0)
        trail_pct = float(self.cfg.get("trail_pct") or 0.0)
        if trail_start > 0 and profit >= trail_start and trail_pct > 0:
            return profit * trail_pct / 100.0
        lock_trigger = float(self.cfg.get("lock_trigger") or 0.0)
        if lock_trigger > 0 and profit >= lock_trigger:
            return float(self.cfg.get("lock_amount") or 0.0)
        return 0.0

    def _manage_position(self, p: Dict[str, Any]) -> None:
        sym = p.get("symbol") or self.symbol
        profit = float(p.get("profit") or 0.0) + float(p.get("swap") or 0.0)
        lock = self.desired_lock(profit)
        if lock <= 0:
            return

        volume = float(p.get("volume") or 0.0)
        mpp = self.money_per_price(volume, sym)
        if mpp <= 0:
            return

        is_buy = p.get("side") == "buy"
        entry = float(p.get("price_open") or 0.0)
        cur_sl = float(p.get("sl") or 0.0)
        offset = lock / mpp
        new_sl = entry + offset if is_buy else entry - offset
        new_sl = round(new_sl, self._digits(sym))

        # monotonic: sell -> SL only goes down, buy -> SL only goes up
        buf = self._point(sym)
        if is_buy:
            if cur_sl > 0 and new_sl <= cur_sl + buf:
                return
        else:
            if cur_sl > 0 and new_sl >= cur_sl - buf:
                return

        # keep the broker's minimum distance from the live price
        try:
            tick = mt5.symbol_info_tick(sym)
        except Exception:
            tick = None
        if not tick:
            return
        min_dist = self._min_stop_distance(sym)
        if is_buy and new_sl > tick.bid - min_dist:
            return
        if (not is_buy) and new_sl < tick.ask + min_dist:
            return

        res = self._modify_sl(p, new_sl)
        if res:
            self._last_sl[int(p["ticket"])] = new_sl
            self._log("trade", f"SL trail {p['side']} {sym} -> {new_sl:.{self._digits(sym)}f} "
                               f"(profit {profit:+.2f}, locking {lock:.2f})")

    def _modify_sl(self, p: Dict[str, Any], new_sl: float) -> bool:
        sym = p.get("symbol") or self.symbol
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": sym,
            "position": int(p["ticket"]),
            "sl": float(new_sl),
            "tp": float(p.get("tp") or 0.0),
            "magic": int(self.cfg.get("magic") or 0),
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
        self._log("warn", f"SL modify rejected: {_tname(res.retcode)}")
        return False

    # --------------------------------------------------------------- trades
    def _arm(self) -> None:
        sym = self.symbol
        try:
            info = mt5.symbol_info(sym)
            tick = mt5.symbol_info_tick(sym)
        except Exception as exc:
            self._log("error", f"arm read failed: {exc}")
            return
        if not info or not tick:
            return
        if not info.trade_mode or not getattr(info, "trade_allowed", True):
            self._log("warn", "trading is disabled for this symbol")
            return

        spread = float(tick.ask) - float(tick.bid)
        max_spread = float(self.cfg.get("max_spread") or 0.0)
        if max_spread > 0 and spread > max_spread:
            self._log("warn", f"spread {spread:.2f} > limit {max_spread:.2f} - not arming")
            return

        d = float(self.cfg.get("distance") or 2.0)
        sl_field = float(self.cfg.get("sl_distance") or 0.0)
        lot = self._round_lot(float(self.cfg.get("lot") or 0.01))
        digits = self._digits(sym)
        buy_price = round(float(tick.ask) + d, digits)
        sell_price = round(float(tick.bid) - d, digits)
        if sl_field > 0:
            # operator set the SL box: both legs get that distance from their entry
            buy_sl = round(buy_price - sl_field, digits)
            sell_sl = round(sell_price + sl_field, digits)
        else:
            # default: each side's SL sits on the opposite pending-order level
            buy_sl = sell_price
            sell_sl = buy_price

        min_dist = self._min_stop_distance(sym)
        if min_dist and (buy_price - float(tick.ask)) < min_dist:
            self._log("warn", f"distance {d} is below the broker stop level - not arming")
            return
        if min_dist and (float(tick.bid) - sell_price) < min_dist:
            self._log("warn", f"distance {d} is below the broker stop level - not arming")
            return

        comment = "goldbot"
        req_buy = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": sym,
            "volume": lot,
            "type": ORDER_TYPE_BUY_STOP,
            "price": buy_price,
            "sl": buy_sl,
            "tp": 0.0,
            "deviation": int(self.cfg.get("slippage_points") or 30),
            "magic": int(self.cfg.get("magic") or 0),
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._pending_filling(sym),
        }
        req_sell = dict(req_buy)
        req_sell.update({"type": ORDER_TYPE_SELL_STOP, "price": sell_price, "sl": sell_sl})

        ok_buy = self._send(req_buy, f"BuyStop {lot} @ {buy_price} (SL {buy_sl})")
        ok_sell = self._send(req_sell, f"SellStop {lot} @ {sell_price} (SL {sell_sl})")
        if ok_buy or ok_sell:
            self.armed_at = time.time()
            self._log("success", f"Armed: BuyStop @ {buy_price} (SL {buy_sl}) / "
                                 f"SellStop @ {sell_price} (SL {sell_sl}) - lot {lot}, gap {d}, "
                                 f"SL {'auto (opposite level)' if sl_field <= 0 else sl_field}")

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

    def _cancel_orders(self, orders: List[Dict[str, Any]], why: str = "") -> None:
        for o in orders:
            req = {"action": mt5.TRADE_ACTION_REMOVE, "order": int(o["ticket"])}
            try:
                res = mt5.order_send(req)
                code = int(res.retcode) if res else -1
            except Exception:
                code = -1
            if code in RETCODE_SUCCESS:
                self._log("info", f"Pending order {o['ticket']} cancelled ({why})")
            else:
                self._log("warn", f"Could not cancel order {o['ticket']}: {_tname(code)}")

    def close_all(self, reason: str = "manual") -> int:
        """Panic button: close every bot position at market."""
        closed = 0
        for p in self._all_positions():
            self._self_closed.add(int(p.get("ticket") or 0))
            if self._close_position(p, reason):
                closed += 1
        orders = self._all_orders()
        if orders:
            self._cancel_orders(orders, why="close all")
        self.cfg["running"] = False
        self.enabled = False
        self._reset_until = 0.0
        return closed

    # ---------------------------------------------------------- bookkeeping
    def _track(self, positions: List[Dict[str, Any]]) -> bool:
        """Book closed positions. Returns True when the market stopped us out."""
        current = {int(p["ticket"]): p for p in positions}
        stop_out = False
        for ticket, p in list(self._known_positions.items()):
            if ticket not in current:
                net = float(p.get("net") or 0.0)
                self.stats["closed"] += 1
                self.stats["realized"] = round(self.stats["realized"] + net, 2)
                if net >= 0:
                    self.stats["wins"] += 1
                else:
                    self.stats["losses"] += 1
                self._log("trade", f"Position #{ticket} closed ({net:+.2f})")
                self._last_sl.pop(ticket, None)
                if ticket in self._self_closed:
                    self._self_closed.discard(ticket)     # we closed it ourselves
                else:
                    stop_out = True                       # SL hit (or manual close)
                cd = self.cfg.get("cooldown_sec")
                cd = 3.0 if cd is None else max(float(cd), 0.0)
                self.cooldown_until = time.time() + cd
        for ticket, p in current.items():
            if ticket not in self._known_positions:
                self._log("trade", f"Position #{ticket} opened: {p['side']} {p['volume']} {p.get('symbol')} "
                                   f"@ {p.get('price_open')} (SL {p.get('sl')})")
                self.stats["opened"] += 1
        self._known_positions = current
        return stop_out

    # --------------------------------------------------------------- status
    def status(self) -> Dict[str, Any]:
        positions = self._all_positions()
        orders = self._all_orders()
        # always publish the pickable instrument list (cached 60 s) so the
        # dashboard dropdown is never empty while we are connected
        if not self.gold_symbols and mt5 is not None:
            try:
                self.available_gold_symbols()
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
        return {
            "running": bool(self.cfg.get("running")),
            "enabled": self.enabled,
            "symbol": self.symbol,
            "symbol_requested": str(self.cfg.get("symbol") or DEFAULT_SYMBOL),
            "symbol_ok": bool(self.symbol_ok and self.symbol),
            "symbol_substituted": self.symbol_substituted,
            "symbol_options": list(self.gold_symbols),
            "gold_symbols": list(self.gold_symbols),
            "ask": ask,
            "bid": bid,
            "spread": spread,
            "digits": digits,
            "gap": float(self.cfg.get("distance") or 0.0),
            "sl_distance": self.effective_sl_distance(),
            "config": dict(self.cfg),
            "positions": positions,
            "orders": orders,
            "floating": floating,
            "stats": dict(self.stats),
            "last_action": self.last_action,
            "last_error": self.last_error,
            "risk_per_trade": self.risk_per_trade(),
            "volume_min": self.volume_min() if self.symbol else 0.01,
            "volume_max": self.volume_max() if self.symbol else 100.0,
            "volume_step": self.volume_step() if self.symbol else 0.01,
            "money_per_price": round(self.money_per_price(lot), 4) if self.symbol else 0.0,
        }
