"""
Reversal Candle Bot (Bot 7).

Strategy Specification
----------------------
1. The bot reads the last CLOSED candle of the chosen timeframe (M1 by default)
   and notes the direction the market just moved:
       bullish candle  -> the market moved UP
       bearish candle  -> the market moved DOWN
2. It then watches the live price. The reversal is measured from the point the
   market TURNED from: the armed candle's own close, ratcheted to the deepest
   low (bear candle) / highest high (bull candle) while the price keeps running
   that way. So a candle that drops, extends its drop and then suddenly snaps
   back fires from the real turning point instead of from a stale close.
   When the price travels back against that move by the Reversal Distance ($),
   the trade is fired IMMEDIATELY in the new (reversed) direction:
       bullish candle, price breaks BACK DOWN  -> SELL now
       bearish candle, price breaks BACK UP    -> BUY  now
   Distances inside the same candle never repeat: each closed candle can only
   produce one reversal trade.
3. Lot Size and TP / SL work exactly like Bot 6:
   - TP > 0 = fixed take profit in account money, 0 = no TP at all.
   - SL > 0 = fixed stop loss in account money.
   - SL = 0 = 70% profit lock: from $0.80 of profit the SL sits at 70% of the
     current profit and follows it up, never back down.
4. The Market Pressure Watch is honored: a NEUTRAL dial (or pressure below the
   Min Pressure % field) blocks the trade, so the reversal must be confirmed by
   the dials as well.
5. STOP stops opening new trades; an open position keeps its SL/TP and is still
   managed.

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
    "symbol": "XAUUSDm",
    "magic": 777007,          # distinct from bot1..bot6 (777001..777006)
    "timeframe": "M1",        # M1 | M5 | M15
    "reverse_dist": 0.0,      # $ back off the turning point; 0 = the candle's own body
    "min_body": 0.0,          # $ the closed candle must have moved (0 = any)
    "tp": 0.0,                # 0 = no take profit (fixed value otherwise)
    "sl": 0.0,                # 0 = 70% profit-lock trail (fixed value otherwise)
    "lock_pct": 70.0,
    "lock_min_profit": 0.80,
    "watch_gate": True,
    "watch_min_pct": 60.0,
    "ai_gate": True,          # consult the AI market-direction outlook too
    "max_trades": 1,
    "cooldown_sec": 3.0,
    "slippage_points": 30,
}

# config keys a change of which should clear a parked trigger
_LAYOUT_KEYS = ("lot", "reverse_dist", "min_body", "timeframe", "tp", "sl",
                "watch_gate", "watch_min_pct", "ai_gate")

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
    """Best-effort record -> dict.

    MT5 hands back namedtuples for positions/orders (they carry _asdict) and
    numpy structured rows for candles (fields live in obj.dtype.names). The old
    last resort was dict(vars(obj)), which raises TypeError on a numpy row - so
    a single candle read killed every tick of this bot. Nothing here may raise.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    asdict = getattr(obj, "_asdict", None)
    if callable(asdict):
        try:
            return dict(asdict())
        except Exception:
            pass
    # numpy structured row / scalar: read the declared field names
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
        return dict(vars(obj))          # plain object carrying a __dict__
    except TypeError:
        return {}


def _rate_field(row: Any, name: str, index: int, default: float = 0.0) -> float:
    """One OHLC/time field out of an MT5 candle row.

    numpy structured rows answer row["close"], namedtuples answer row.close and
    a plain sequence answers row[4] - the project's other bots read candles by
    name, this bot was the only one routing the row through vars()/dict().
    """
    for getter in (lambda: row[name], lambda: getattr(row, name), lambda: row[index]):
        try:
            return float(getter())
        except (TypeError, ValueError, KeyError, IndexError, AttributeError):
            continue
    return float(default)


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


class ReversalCandleBot:
    """Reversal Candle Bot (Bot 7)."""

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
        self.ai: Dict[str, Any] = {"market": "", "verdict": "", "allowed": True,
                                   "reason": "not read yet"}
        self._pos_meta: Dict[int, Dict[str, Any]] = {}
        self._known_positions: Dict[int, Dict[str, Any]] = {}
        self._self_closed: set = set()
        self._last_candle_time: int = 0        # the closed candle currently armed
        self._last_trigger_candle: int = 0     # candle time that already fired
        self._move_ref: float = 0.0            # the point the market turned from
        self._gate_note: str = ""              # a reversal was found but blocked
        self._gate_side: str = ""
        self._candle: Dict[str, Any] = {}      # view of the armed candle
        self._symbol_changed: bool = False
        self._resolve_ts: float = 0.0
        self._last_order_ts: float = 0.0
        self._last_sl_send_ts: float = 0.0
        self._retry_after: float = 0.0
        self._fail_streak: int = 0
        self._wait_log_ts: float = 0.0
        self._rates_error: str = ""
        self.watch: Dict[str, Any] = {
            "buyer_pct": 0.0, "seller_pct": 0.0, "dominant": "NEUTRAL",
            "side": "", "allowed": False, "reason": "not read yet",
        }

    def _log(self, kind: str, message: str) -> None:
        self.last_action = message
        if self.logger:
            try:
                self.logger(kind, f"[Bot7] {message}")
            except Exception:
                pass

    def set_watch_provider(self, provider) -> None:
        """Injects the live watch-dial source: fn(symbol) -> {buyer_pct, ...}."""
        self._watch_provider = provider

    def set_ai_provider(self, provider) -> None:
        """Injects the AI market-direction source: fn(symbol) -> {market, verdict, ...}."""
        self._ai_provider = provider

    def read_ai(self, side: str) -> Dict[str, Any]:
        """The news engine's AI verdict for this market, checked before entering.

        Only a clear opposite verdict blocks the reversal: MIXED, or no outlook
        for the market, leaves the decision to the watch dials.
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
        self.cfg["reverse_dist"] = max(0.0, round(_f(self.cfg.get("reverse_dist"), 0.0), 6))
        self.cfg["min_body"] = max(0.0, round(_f(self.cfg.get("min_body"), 0.0), 6))
        self.cfg["tp"] = max(0.0, round(_f(self.cfg.get("tp"), 0.0), 4))
        self.cfg["sl"] = max(0.0, round(_f(self.cfg.get("sl"), 0.0), 4))
        self.cfg["lock_pct"] = min(95.0, max(0.0, _f(self.cfg.get("lock_pct"), 70.0)))
        self.cfg["lock_min_profit"] = max(0.0, round(_f(self.cfg.get("lock_min_profit"), 0.80), 4))
        self.cfg["watch_min_pct"] = min(99.0, max(0.0, _f(self.cfg.get("watch_min_pct"), 60.0)))
        self.cfg["watch_gate"] = bool(self.cfg.get("watch_gate"))
        tf = str(self.cfg.get("timeframe") or "M1").strip().upper()
        self.cfg["timeframe"] = tf if tf in ("M1", "M5", "M15") else "M1"
        try:
            self.cfg["max_trades"] = min(20, max(1, int(_f(self.cfg.get("max_trades"), 1))))
        except (TypeError, ValueError):
            self.cfg["max_trades"] = 1
        self.cfg["cooldown_sec"] = max(0.0, _f(self.cfg.get("cooldown_sec"), 3.0))
        self.cfg["slippage_points"] = max(0, int(_f(self.cfg.get("slippage_points"), 30)))
        self.cfg["magic"] = int(_f(self.cfg.get("magic"), 777007))

        for k in _LAYOUT_KEYS:
            if patch and k in patch and not _same(patch.get(k), before.get(k)):
                # a new setting must not fire on the old candle
                self._last_trigger_candle = self._last_candle_time
                self._move_ref = 0.0        # re-arm the turning point cleanly
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

    # ------------------------------------------------------------- candles
    def _tf(self) -> Any:
        tf = str(self.cfg.get("timeframe") or "M1").upper()
        return getattr(mt5, f"TIMEFRAME_{tf}", getattr(mt5, "TIMEFRAME_M1", 1))

    def read_candle(self) -> Dict[str, Any]:
        """The last CLOSED candle = the move the market just made."""
        out: Dict[str, Any] = {
            "time": 0, "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0,
            "body": 0.0, "bull": True, "ready": False, "price": 0.0,
        }
        if mt5 is None or not self.symbol:
            return out
        try:
            rates = mt5.copy_rates_from_pos(self.symbol, self._tf(), 0, 3)
        except Exception as exc:
            self._rates_error = str(exc)
            return out
        if rates is None or len(rates) < 2:
            self._rates_error = "no candles from the terminal yet"
            return out
        # rates[-1] is the forming candle, rates[-2] is the last closed one.
        # Read the fields BY NAME (numpy structured row) - the project's other
        # bots all do this; converting the row with vars()/dict() is exactly
        # what raised TypeError and killed every tick of this bot.
        row = rates[-2]
        o = _rate_field(row, "open", 1)
        c = _rate_field(row, "close", 4)
        h = _rate_field(row, "high", 2)
        l = _rate_field(row, "low", 3)
        t = int(_rate_field(row, "time", 0))
        if o <= 0 or c <= 0 or h <= 0 or l <= 0:
            self._rates_error = "candle row has no usable prices"
            return out
        out.update(time=t, open=o, close=c, high=h, low=l,
                   body=abs(c - o), bull=(c >= o), ready=True)
        self._rates_error = ""
        if t != self._last_candle_time:
            # a new candle closed: it becomes the armed reversal source and the
            # turning point starts exactly at its close
            self._last_candle_time = t
            self._candle = dict(out)
            self._move_ref = c
        return out

    def reversal_trigger(self, candle: Dict[str, Any], price: float) -> str:
        """Which side (if any) the live price has reversed to.

        The reference is the turning point (candle["ref"] = the candle's close,
        ratcheted to the real extreme in tick()) so a drop that goes deeper and
        then snaps back still fires - measured from where the market turned.

        bullish candle -> price travelling BACK DOWN (+ reverse distance) = SELL
        bearish candle -> price travelling BACK UP   (+ reverse distance) = BUY
        """
        if not candle.get("ready") or price <= 0:
            return ""
        body = float(candle.get("body") or 0.0)
        min_body = float(self.cfg.get("min_body") or 0.0)
        if min_body > 0 and body < min_body:
            return ""
        dist = float(self.cfg.get("reverse_dist") or 0.0)
        if dist <= 0:
            # auto: the candle's own body is the reversal distance (tiny floor)
            dist = body if body > 0 else max(self._min_stop_distance(), 1e-9)
        close = float(candle.get("close") or 0.0)
        ref = float(candle.get("ref") or 0.0) or close
        if candle.get("bull"):
            return "sell" if (ref - price) >= dist else ""
        return "buy" if (price - ref) >= dist else ""

    def trigger_levels(self, candle: Dict[str, Any]) -> Dict[str, float]:
        """The live turning point, the trigger price and the distance left."""
        out = {"ref": 0.0, "dist": 0.0, "need": 0.0}
        if not candle.get("ready"):
            return out
        body = float(candle.get("body") or 0.0)
        close = float(candle.get("close") or 0.0)
        dist = float(self.cfg.get("reverse_dist") or 0.0) or body
        ref = float(self._move_ref or 0.0) or close
        need = (ref - dist) if candle.get("bull") else (ref + dist)
        out.update(ref=ref, dist=dist, need=need)
        return out

    # ------------------------------------------------------------ watch gate
    def read_watch(self, side: str = "") -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "buyer_pct": 0.0, "seller_pct": 0.0, "dominant": "NEUTRAL",
            "side": side, "allowed": False, "reason": "",
        }
        if not bool(self.cfg.get("watch_gate", True)):
            out.update(allowed=True, reason="watch gate OFF")
            self.watch = out
            return out
        if self._watch_provider is None or not self.symbol:
            out["reason"] = "no watch data - blocked"
            self.watch = out
            return out
        try:
            w = self._watch_provider(self.symbol) or {}
        except Exception as exc:
            out["reason"] = f"watch error: {exc}"
            self.watch = out
            return out
        if not w:
            out["reason"] = f"{self.symbol} has no live dial - blocked"
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
        if side == "buy":
            if buyer >= min_pct:
                out.update(allowed=True, reason=f"Watch BUY {buyer:.0f}% >= {min_pct:.0f}%")
            else:
                out["reason"] = f"Watch BUY {buyer:.0f}% < {min_pct:.0f}% - reversal blocked"
        elif side == "sell":
            if seller >= min_pct:
                out.update(allowed=True, reason=f"Watch SELL {seller:.0f}% >= {min_pct:.0f}%")
            else:
                out["reason"] = f"Watch SELL {seller:.0f}% < {min_pct:.0f}% - reversal blocked"
        else:
            if buyer >= min_pct:
                out.update(side="buy", allowed=True, reason=f"Watch BUY {buyer:.0f}%")
            elif seller >= min_pct:
                out.update(side="sell", allowed=True, reason=f"Watch SELL {seller:.0f}%")
            else:
                out["reason"] = (f"Watch NEUTRAL ({dominant}) buyer {buyer:.0f}% / "
                                 f"seller {seller:.0f}% < {min_pct:.0f}%")
        self.watch = out
        return out

    # ------------------------------------------------------------ exposure
    def _all_positions(self, any_symbol: bool = False) -> List[Dict[str, Any]]:
        if mt5 is None:
            return []
        try:
            raw = mt5.positions_get(symbol=None if any_symbol else self.symbol) or []
        except Exception:
            raw = []
        magic = int(self.cfg.get("magic") or 777007)
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
        magic = int(self.cfg.get("magic") or 777007)
        out: List[Dict[str, Any]] = []
        for o in raw or []:
            d = _as_dict(o)
            if magic and int(d.get("magic") or 0) != magic:
                continue
            if not any_symbol and self.symbol and d.get("symbol") != self.symbol:
                continue
            if int(d.get("state", 0)) not in (ORDER_STATE_PLACED, ORDER_STATE_PARTIAL):
                continue
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
            "symbol": sym, "volume": vol,
            "type": mt5.ORDER_TYPE_SELL if side == "buy" else mt5.ORDER_TYPE_BUY,
            "position": ticket, "price": price,
            "deviation": int(self.cfg.get("slippage_points") or 30),
            "magic": int(self.cfg.get("magic") or 777007),
            "comment": "bot7_close"[:31],
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
        if cancelled:
            self._log("info", f"{cancelled} order(s) cancelled ({why})")
        return cancelled

    def close_all(self, reason: str = "manual") -> int:
        closed = 0
        for p in self._all_positions(any_symbol=True):
            if self._close_position(p, reason):
                closed += 1
        orders = self._all_orders(any_symbol=True)
        if orders:
            self._cancel_orders(orders, why=reason)
        self.cfg["running"] = False
        self.enabled = False
        self._log("warn", f"Bot7 Closed all ({closed} positions) [{reason}]")
        return closed

    def _cleanup_all(self, reason: str = "") -> None:
        orders = self._all_orders(any_symbol=True)
        if orders:
            self._cancel_orders(orders, why=reason)
        for p in self._all_positions(any_symbol=True):
            self._close_position(p, reason)
        self._pos_meta.clear()
        self._known_positions.clear()
        self._self_closed.clear()

    def _open_trade(self, side: str, reason: str = "") -> bool:
        """Immediate market order - the reversal has to be caught now."""
        if mt5 is None or not self.symbol:
            return False
        try:
            info = mt5.symbol_info(self.symbol)
            tick = mt5.symbol_info_tick(self.symbol)
        except Exception:
            info, tick = None, None
        if not info or not tick:
            return False
        if not getattr(info, "trade_mode", 1) or not getattr(info, "trade_allowed", True):
            self._log("warn", "trading is disabled for this symbol - trade skipped")
            return False
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        if not ask or not bid:
            return False
        digits = self._digits()
        lot = self._round_lot(_f(self.cfg.get("lot"), 0.01))
        price = ask if side == "buy" else bid
        tp_money = max(0.0, _f(self.cfg.get("tp"), 0.0))
        sl_money = max(0.0, _f(self.cfg.get("sl"), 0.0))
        tp_off = self._money_offset(tp_money, lot, digits)
        sl_off = self._money_offset(sl_money, lot, digits)
        tp = round(price + tp_off, digits) if (tp_off and side == "buy") else \
            (round(price - tp_off, digits) if tp_off else 0.0)
        sl = round(price - sl_off, digits) if (sl_off and side == "buy") else \
            (round(price + sl_off, digits) if sl_off else 0.0)
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": sl, "tp": tp,
            "deviation": int(self.cfg.get("slippage_points") or 30),
            "magic": int(self.cfg.get("magic") or 777007),
            "comment": "bot7_rev"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(self.symbol),
        }
        try:
            res = mt5.order_send(req)
            retcode = getattr(res, "retcode", 0) if res else 0
        except Exception as exc:
            retcode = 0
            self.last_error = str(exc)
            res = None
        self._last_order_ts = time.time()
        if retcode in RETCODE_SUCCESS:
            self._fail_streak = 0
            self._retry_after = 0.0
            self._log("success", f"REVERSAL {side.upper()} opened {lot} @ {price} "
                                 f"({reason}) · TP {tp or 'off'} · SL {sl or 'profit-lock'}")
            return True
        err = _RETCODE_NAMES.get(retcode, str(retcode))
        self._fail_streak += 1
        self._retry_after = time.time() + min(60.0, 5.0 * (2 ** min(self._fail_streak - 1, 3)))
        self.last_error = f"Reversal trade rejected: {err}"
        self._log("error", f"Reversal {side.upper()} rejected: {err}")
        return False

    def _money_offset(self, money: float, lot: float, digits: int) -> float:
        if money <= 0:
            return 0.0
        mpp = self.money_per_price(lot)
        if mpp <= 0:
            return 0.0
        return round(money / mpp, digits)

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

    def _sync(self, positions: List[Dict[str, Any]]) -> None:
        for p in positions:
            ticket = int(p.get("ticket") or 0)
            if ticket and ticket not in self._pos_meta:
                self._pos_meta[ticket] = {
                    "side": p.get("side") or "buy",
                    "lot": float(p.get("volume") or 0.0),
                    "entry": float(p.get("price_open") or 0.0),
                    "managed": True,
                }

    # --------------------------------------------------------- TP / SL engine
    def _apply_protection(self, positions: List[Dict[str, Any]]) -> None:
        """Fixed TP/SL when set; otherwise the 70% profit-lock trail."""
        if not positions or not self._pos_meta:
            return
        now = time.time()
        if (now - self._last_sl_send_ts) < 0.4:
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
            new_sl, new_tp = cur_sl, cur_tp

            if tp_money > 0 and cur_tp <= 0:
                off = self._money_offset(tp_money, lot, digits)
                if off > 0:
                    new_tp = round(entry + off, digits) if is_buy else round(entry - off, digits)

            if sl_money > 0:
                if cur_sl <= 0:
                    off = self._money_offset(sl_money, lot, digits)
                    if off > 0:
                        new_sl = round(entry - off, digits) if is_buy else round(entry + off, digits)
            else:
                net = float(p.get("net") or 0.0)
                if net >= lock_min:
                    off = self._money_offset(net * lock_pct / 100.0, lot, digits)
                    if off > 0:
                        cand = round(entry + off, digits) if is_buy else round(entry - off, digits)
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
            "magic": int(self.cfg.get("magic") or 777007),
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
            self._last_candle_time = 0
            self._last_trigger_candle = 0
            self._move_ref = 0.0
            self._candle = {}
            if not bool(self.cfg.get("running")):
                return

        positions = self._all_positions()
        self._track(positions)
        self._sync(positions)
        # protect first: an open position is always managed, running or not
        self._apply_protection(positions)
        self.enabled = bool(positions)

        if not bool(self.cfg.get("running")):
            return

        try:
            tick = mt5.symbol_info_tick(self.symbol)
        except Exception:
            tick = None
        if not tick:
            return
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        if not bid or not ask:
            return
        price = (bid + ask) / 2.0

        candle = self.read_candle()
        if not candle.get("ready"):
            return

        # one trade at a time, like every other bot
        if len(positions) >= int(self.cfg.get("max_trades") or 1):
            return
        if time.time() < self._retry_after:
            return
        if (time.time() - self._last_order_ts) < max(_f(self.cfg.get("cooldown_sec"), 3.0), 0.5):
            return
        # the same closed candle never fires twice
        if self._last_trigger_candle and self._last_trigger_candle == self._last_candle_time:
            return

        # the turning point follows the real extreme: starting at the armed
        # candle's close it ratchets down (bear) / up (bull) while the price
        # keeps running that way, so "candle drops, keeps dropping, then snaps
        # back up" is measured from the bottom it actually turned from.
        ref = float(self._move_ref or 0.0) or float(candle.get("close") or 0.0)
        ref = max(ref, price) if candle.get("bull") else min(ref, price)
        self._move_ref = ref
        view = dict(candle, ref=ref)

        side = self.reversal_trigger(view, price)
        if not side:
            self._gate_note = ""
            self._gate_side = ""
            return
        gate = self.read_watch(side)
        if not gate.get("allowed"):
            self._gate_side = side
            self._gate_note = str(gate.get("reason") or "watch blocked")
            if (time.time() - self._wait_log_ts) >= 20.0:
                self._wait_log_ts = time.time()
                self._log("info", f"Reversal {side.upper()} found but {gate.get('reason') or 'blocked'}")
            return
        ai = self.read_ai(side)
        if not ai.get("allowed"):
            self._gate_side = side
            self._gate_note = str(ai.get("reason") or "AI blocked")
            if (time.time() - self._wait_log_ts) >= 20.0:
                self._wait_log_ts = time.time()
                self._log("info", f"Reversal {side.upper()} found but {ai.get('reason') or 'AI blocked'}")
            return
        self._gate_note = ""
        self._gate_side = ""
        body = float(candle.get("body") or 0.0)
        why = (f"{self.cfg.get('timeframe')} candle "
               f"{'UP' if candle.get('bull') else 'DOWN'} body {body:g} reversed "
               f"from {ref:g}, {gate.get('reason') or ''}").strip()
        if self._open_trade(side, why):
            self._last_trigger_candle = self._last_candle_time

    # ------------------------------------------------------------- status
    def status(self) -> Dict[str, Any]:
        # keep the radar alive even while the bot is stopped: the last closed
        # candle, the turning point and the distance to the trigger stay
        # visible, so the panel never sits on "waiting for candles" while the
        # terminal is right there handing candles out
        if not bool(self.cfg.get("running")):
            try:
                if not self.symbol:
                    self.resolve_symbol()
                self.read_candle()
            except Exception:
                pass
        positions = self._all_positions()
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

        shown_positions = []
        for p in positions:
            d = dict(p)
            d["locked"] = bool(float(p.get("sl") or 0.0) > 0)
            d["entry"] = float(p.get("price_open") or 0.0)
            shown_positions.append(d)

        lock_pct = float(self.cfg.get("lock_pct") or 70.0)
        sl_money = float(self.cfg.get("sl") or 0.0)
        tp_money = float(self.cfg.get("tp") or 0.0)
        # live radar numbers: where the market turned from, the trigger price
        # and how far the price still is from firing
        levels = self.trigger_levels(self._candle)
        need = float(levels.get("need") or 0.0)
        left = 0.0
        if cur_price > 0 and float(levels.get("dist") or 0.0) > 0:
            left = (cur_price - need) if self._candle.get("bull") else (need - cur_price)
            left = float(round(left, digits))
        return {
            "running": bool(self.cfg.get("running")),
            "enabled": bool(positions),
            "symbol": self.symbol,
            "symbol_requested": str(self.cfg.get("symbol") or "XAUUSDm"),
            "symbol_ok": bool(self.symbol_ok and self.symbol),
            "symbol_substituted": "",
            "symbol_options": list(self.available_symbols_list),
            "ask": ask, "bid": bid, "spread": spread, "digits": digits, "price": cur_price,
            "config": dict(self.cfg),
            "candle": dict(self._candle),
            "trigger": {
                "candle_time": int(self._last_candle_time or 0),
                "fired": bool(self._last_trigger_candle and self._last_trigger_candle == self._last_candle_time),
                "reverse_dist": float(self.cfg.get("reverse_dist") or 0.0),
                "dist": float(levels.get("dist") or 0.0),
                "ref": round(float(levels.get("ref") or 0.0), digits),
                "need": round(need, digits),
                "left": max(0.0, left),
                "blocked": self._gate_note,
                "blocked_side": self._gate_side,
                "rates_error": self._rates_error,
            },
            "positions": shown_positions,
            "running_count": len(positions),
            "floating": floating,
            "stats": dict(self.stats),
            "watch": dict(self.watch),
            "ai": dict(self.ai),
            "watch_min_pct": float(self.cfg.get("watch_min_pct") or 60.0),
            "rules": {
                "tp": "fixed" if tp_money > 0 else "off",
                "sl": ("fixed" if sl_money > 0 else
                       f"profit-lock {lock_pct:.0f}% (min ${float(self.cfg.get('lock_min_profit') or 0.8):.2f})"),
                "tp_money": tp_money, "sl_money": sl_money, "lock_pct": lock_pct,
            },
            "last_action": self.last_action,
            "last_error": self.last_error,
            "volume_min": self.volume_min() if self.symbol else 0.01,
            "volume_max": self.volume_max() if self.symbol else 100.0,
            "volume_step": self.volume_step() if self.symbol else 0.01,
            "money_per_price": round(self.money_per_price(lot), 4) if self.symbol else 0.0,
        }
