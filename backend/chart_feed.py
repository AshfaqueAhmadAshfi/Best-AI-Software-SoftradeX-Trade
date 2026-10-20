"""Exness (MetaTrader 5) chart + big-print feed.

The dashboard chart is powered by the *same* Exness account the bots trade on,
not by an external crypto exchange. This module is the single place that talks
to MetaTrader 5 for market data:

* ``symbols()``   - the Exness instruments the chart can show
* ``candles()``   - real OHLCV bars for one timeframe
* ``ticker()``    - 24h high / low / change / tick volume + live bid-ask
* ``snapshot()``  - one frame with meta + bars + ticker, used by the WS
* ``ChartFeed``   - a ~20 fps sampler that streams ticks to every browser and
                    detects unusually large price impulses ("big prints")

Every MT5 call is serialised through ``mt5_client.IPC_LOCK`` because the
terminal has a single IPC pipe shared with the engine. Nothing here places an
order, logs in or writes to the store; it is read-only market data.
"""
from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

try:  # pragma: no cover - only missing when the package is not installed
    import MetaTrader5 as mt5  # type: ignore
except Exception:  # pragma: no cover
    mt5 = None  # type: ignore

from .mt5_client import IPC_LOCK, preferred_terminal_path

try:  # pragma: no cover - keeps the Exness dashboard intact if the BD module is absent
    from .bd_feed import is_bd_symbol

    def _bd_candles(symbol: str, tf: str, count: int) -> List[List[float]]:
        from . import bd_feed

        return bd_feed.candles(symbol, tf, count)

    def _bd_tick(symbol: str) -> Optional[Dict[str, Any]]:
        from . import bd_feed

        return bd_feed.tick(symbol)

    def _bd_meta(symbol: str) -> Optional[Dict[str, Any]]:
        from . import bd_feed

        return bd_feed.meta(symbol)

    def _bd_m1_volume(symbol: str) -> Tuple[int, int]:
        from . import bd_feed

        return bd_feed.m1_volume(symbol)
except Exception:  # pragma: no cover
    def is_bd_symbol(symbol: str) -> bool:  # type: ignore
        return False
    _bd_candles = _bd_tick = _bd_meta = None
    _bd_m1_volume = None

# Timeframes the chart offers -> (seconds per bar, MT5 constant name)
TF_SEC: Dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}
_TF_NAME = {
    "1m": "TIMEFRAME_M1",
    "5m": "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15",
    "1h": "TIMEFRAME_H1",
    "4h": "TIMEFRAME_H4",
    "1d": "TIMEFRAME_D1",
}

# Exness instrument catalogue for the chart. The first candidate that exists on
# the connected account wins; a missing market is simply not offered.
CATALOG: List[Dict[str, Any]] = [
    {"symbol": "XAUUSDm", "alts": ["XAUUSDm", "XAUUSD247m", "XAUUSD"],
     "label": "GOLD", "pair": "USD", "icon": "🟡", "unit": "oz", "kind": "metals"},
    {"symbol": "BTCUSDTm", "alts": ["BTCUSDTm", "BTCUSDm"],
     "label": "BTC", "pair": "USDT", "icon": "🟠", "unit": "BTC", "kind": "crypto"},
    {"symbol": "ETHUSDm", "alts": ["ETHUSDm", "ETHUSDTm"],
     "label": "ETH", "pair": "USD", "icon": "🔷", "unit": "ETH", "kind": "crypto"},
    {"symbol": "EURUSDm", "alts": ["EURUSDm", "EURUSD"],
     "label": "EUR", "pair": "USD", "icon": "💶", "unit": "lots", "kind": "forex"},
    {"symbol": "GBPUSDm", "alts": ["GBPUSDm", "GBPUSD"],
     "label": "GBP", "pair": "USD", "icon": "💷", "unit": "lots", "kind": "forex"},
    {"symbol": "USDJPYm", "alts": ["USDJPYm", "USDJPY"],
     "label": "USD", "pair": "JPY", "icon": "💴", "unit": "lots", "kind": "forex"},
    {"symbol": "USOILm", "alts": ["USOILm", "XTIUSDm", "USOIL", "XTIUSD"],
     "label": "OIL", "pair": "USD", "icon": "🛢", "unit": "bbl", "kind": "energy"},
    {"symbol": "USTECm", "alts": ["USTECm", "NAS100m", "US100m", "USTEC"],
     "label": "US100", "pair": "USD", "icon": "📈", "unit": "index", "kind": "indices"},
    {"symbol": "XAGUSDm", "alts": ["XAGUSDm", "XAGUSD"],
     "label": "SILVER", "pair": "USD", "icon": "⚪", "unit": "oz", "kind": "metals"},
]

# How often the sampler wakes up (20 fps). MT5 itself groups ticks, so this is
# the fastest rate that still produces new data on the Exness demo feed.
TICK_INTERVAL = 0.05
# Tick-volume (participation) is read from the live M1 bar at this rate.
VOLUME_INTERVAL = 0.1
# A big print needs a price impulse this many times larger than the recent
# median tick move, and at least this many spreads wide.
IMPULSE_MEDIAN_FACTOR = 2.5
IMPULSE_SPREAD_FACTOR = 1.0
PRINT_COOLDOWN = 0.8
FOLLOW_AFTER = 6.0


class ChartUnavailable(RuntimeError):
    """Raised when the Exness terminal / symbol cannot provide data."""


def _tf_const(tf: str) -> int:
    if mt5 is None:
        raise ChartUnavailable("MetaTrader5 package is not installed")
    name = _TF_NAME.get(tf)
    if not name:
        raise ChartUnavailable(f"unknown timeframe: {tf}")
    const = getattr(mt5, name, None)
    if const is None:
        raise ChartUnavailable(f"MetaTrader5 has no {name}")
    return int(const)


# How long an attach probe is trusted before terminal_info() is asked again.
_ATTACH_TTL = 1.0
_attach_checked_at: float = 0.0
_attach_checked_for: Any = None


def _ensure_terminal() -> None:
    """Attach to the running Exness terminal once; never logs in or out.

    The attach probe is cached for ``_ATTACH_TTL`` seconds: the sampler calls
    this up to 20 times per second per symbol, and an uncached
    ``terminal_info()`` on every frame would steal IPC time from the trading
    engine (the chart then lags behind the engine's own reads).
    """
    global _attach_checked_at, _attach_checked_for
    if mt5 is None:
        raise ChartUnavailable("MetaTrader5 package is not installed")
    now = time.monotonic()
    if (_attach_checked_for is mt5
            and (now - _attach_checked_at) < _ATTACH_TTL):
        return
    with IPC_LOCK:
        info = mt5.terminal_info()
        if info is not None:
            _attach_checked_for = mt5
            _attach_checked_at = now
            return
        # Prefer the terminal the trading engine is logged into - a blind
        # initialize() could auto-attach a different terminal/account.
        kwargs: Dict[str, Any] = {}
        path = preferred_terminal_path()
        if path:
            kwargs["path"] = path
        if not mt5.initialize(**kwargs):
            code, msg = mt5.last_error()
            raise ChartUnavailable(
                "Exness terminal is not reachable (%s: %s)" % (code, msg)
            )
    _attach_checked_for = mt5
    _attach_checked_at = now


def _symbol_info(symbol: str) -> Optional[Any]:
    _ensure_terminal()
    with IPC_LOCK:
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        if not getattr(info, "visible", True) or not getattr(info, "select", True):
            try:
                mt5.symbol_select(symbol, True)
            except Exception:
                pass
        return info


def _meta_from_info(symbol: str, spec: Dict[str, Any], info: Any) -> Dict[str, Any]:
    digits = int(getattr(info, "digits", 2) or 2)
    point = float(getattr(info, "point", 0.0) or (10 ** -digits))
    return {
        "symbol": symbol,
        "label": spec["label"],
        "pair": spec["pair"],
        "icon": spec["icon"],
        "unit": spec["unit"],
        "kind": spec["kind"],
        "alts": [str(a) for a in spec.get("alts", [])],
        "description": str(getattr(info, "description", "") or symbol),
        "digits": digits,
        "point": point,
        "spread_points": int(getattr(info, "spread", 0) or 0),
        "contract": float(getattr(info, "trade_contract_size", 1.0) or 1.0),
        "currency": str(getattr(info, "currency_profit", "USD") or "USD"),
    }


def _round_price(value: float, digits: int) -> float:
    try:
        return round(float(value), int(digits))
    except Exception:
        return 0.0


def _tick_dict(tick: Any) -> Optional[Dict[str, Any]]:
    if tick is None:
        return None
    bid = float(getattr(tick, "bid", 0.0) or 0.0)
    ask = float(getattr(tick, "ask", 0.0) or 0.0)
    if bid <= 0 and ask <= 0:
        return None
    ms = int(getattr(tick, "time_msc", 0) or 0)
    if ms <= 0:
        ms = int(getattr(tick, "time", 0) or 0) * 1000
    return {
        "bid": bid,
        "ask": ask,
        "ms": ms,
        "time": int(getattr(tick, "time", 0) or 0),
        "volume": int(getattr(tick, "volume", 0) or 0),
        "volume_real": float(getattr(tick, "volume_real", 0.0) or 0.0),
    }


def _bars_from_rates(rates: Any) -> List[List[float]]:
    if rates is None:
        return []
    out: List[List[float]] = []
    for r in rates:
        try:
            out.append([
                int(r["time"]) * 1000,
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
                int(r["tick_volume"]),
            ])
        except Exception:
            continue
    return out


def tape_from_bars(bars: List[List[float]], point: float, digits: int,
                   limit: int = 40) -> List[Dict[str, Any]]:
    """Seeds the tape with the biggest 1m bars of the last two hours.

    This is real history, not a simulation: a row exists only when the bar's
    body is at least 1.8x the average body of the window. ``mv`` is the bar's
    own move and ``fv`` the move of the bar right after it (both in price
    units), so the tape immediately shows whether the market followed.
    """
    if not bars or len(bars) < 12:
        return []
    window = bars[-120:]
    bodies = [abs(float(b[4]) - float(b[1])) for b in window]
    spreads = [max(1e-9, float(b[2]) - float(b[3])) for b in window]
    mean_body = statistics.fmean(bodies) if bodies else 0.0
    mean_range = statistics.fmean(spreads) if spreads else 0.0
    floor = max(mean_body * 1.8, mean_range * 0.6, point * 5)
    out: List[Dict[str, Any]] = []
    for i, b in enumerate(window[:-1]):
        o, h, l, c, vol = (float(b[1]), float(b[2]), float(b[3]),
                           float(b[4]), int(b[5]))
        body = c - o
        if abs(body) < floor:
            continue
        side = "BUY" if body >= 0 else "SELL"
        nxt = window[i + 1]
        follow = (float(nxt[4]) - c) * (1.0 if side == "BUY" else -1.0)
        out.append({
            "id": "h%s" % int(b[0]),
            "t": "print",
            "s": "",
            "tm": int(b[0]),
            "px": _round_price(c, digits),
            "side": side,
            "ticks": vol,
            "mv": _round_price(abs(body), digits),
            "fv": _round_price(follow, digits),
            "src": "1m",
        })
    out.sort(key=lambda r: r["tm"], reverse=True)
    return out[:limit]


class ChartFeed:
    """Owns the MT5 market-data cache, the sampler thread and the WS fan-out."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._clients: Dict[int, Dict[str, Any]] = {}
        self._subs: Dict[str, int] = {}
        self._states: Dict[str, Dict[str, Any]] = {}
        self._next_id = 1
        self._meta_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._symbols_cache: Tuple[float, Dict[str, Any]] = (0.0, {})
        self._rates_cache: Dict[Tuple[str, str, int], Tuple[float, List[List[float]]]] = {}
        self._tick_cache: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}
        self._ticker_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._tape_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ terminal
    def symbols(self) -> Dict[str, Any]:
        now = time.time()
        cached_at, cached = self._symbols_cache
        if cached and now - cached_at < 30.0 and cached.get("available"):
            return cached
        try:
            _ensure_terminal()
        except ChartUnavailable as exc:
            payload = {
                "available": False,
                "message": str(exc),
                "symbols": [],
                "count": 0,
            }
            self._symbols_cache = (now, payload)
            return payload
        rows: List[Dict[str, Any]] = []
        try:
            with IPC_LOCK:
                acc = mt5.account_info()
        except Exception:
            acc = None
        for spec in CATALOG:
            for cand in spec.get("alts", [spec["symbol"]]):
                try:
                    info = _symbol_info(cand)
                except ChartUnavailable:
                    raise
                except Exception:
                    info = None
                if info is None:
                    continue
                rows.append(_meta_from_info(cand, spec, info))
                break
        payload = {
            "available": bool(rows),
            "message": "" if rows else "No Exness instruments available",
            "symbols": rows,
            "count": len(rows),
            "account": {
                "login": int(getattr(acc, "login", 0) or 0) if acc else 0,
                "server": str(getattr(acc, "server", "") or "") if acc else "",
            },
            "server_time": int(time.time()),
        }
        self._symbols_cache = (now, payload)
        return payload

    def meta(self, symbol: str) -> Dict[str, Any]:
        symbol = str(symbol or "").strip()
        if not symbol:
            raise ChartUnavailable("no symbol")
        if is_bd_symbol(symbol) and _bd_meta is not None:
            try:
                return _bd_meta(symbol)
            except Exception:
                pass
        now = time.time()
        hit = self._meta_cache.get(symbol)
        if hit and now - hit[0] < 60.0:
            return hit[1]
        info = _symbol_info(symbol)
        if info is None:
            raise ChartUnavailable("Exness does not offer %s" % symbol)
        spec = next((s for s in CATALOG if symbol in s.get("alts", []) or s["symbol"] == symbol),
                    {"symbol": symbol, "label": symbol.replace("m", "").upper(),
                     "pair": "USD", "icon": "📊", "unit": "lots", "kind": "other",
                     "alts": [symbol]})
        meta = _meta_from_info(symbol, spec, info)
        self._meta_cache[symbol] = (now, meta)
        return meta

    # --------------------------------------------------------------- data
    def _read_rates(self, symbol: str, tf: str, count: int) -> List[List[float]]:
        if is_bd_symbol(symbol) and _bd_candles is not None:
            return _bd_candles(symbol, tf, count)
        _ensure_terminal()
        const = _tf_const(tf)
        with IPC_LOCK:
            rates = mt5.copy_rates_from_pos(symbol, const, 0, int(count))
        return _bars_from_rates(rates)

    def candles(self, symbol: str, tf: str = "5m", count: int = 600) -> List[List[float]]:
        if tf not in TF_SEC:
            raise ChartUnavailable("unknown timeframe: %s" % tf)
        key = (symbol, tf, int(count))
        now = time.time()
        hit = self._rates_cache.get(key)
        if hit and now - hit[0] < 1.5:
            return hit[1]
        bars = self._read_rates(symbol, tf, count)
        if not bars:
            raise ChartUnavailable("Exness returned no candles for %s %s" % (symbol, tf))
        self._rates_cache[key] = (now, bars)
        return bars

    def tick(self, symbol: str, max_age: float = 0.02) -> Optional[Dict[str, Any]]:
        now = time.time()
        hit = self._tick_cache.get(symbol)
        if hit and now - hit[0] < max_age:
            return hit[1]
        if is_bd_symbol(symbol) and _bd_tick is not None:
            tick = _bd_tick(symbol)
            self._tick_cache[symbol] = (now, tick)
            return tick
        _ensure_terminal()
        with IPC_LOCK:
            raw = mt5.symbol_info_tick(symbol)
        tick = _tick_dict(raw)
        self._tick_cache[symbol] = (now, tick)
        return tick

    def ticker(self, symbol: str) -> Dict[str, Any]:
        now = time.time()
        hit = self._ticker_cache.get(symbol)
        if hit and now - hit[0] < 8.0:
            return hit[1]
        meta = self.meta(symbol)
        tick = self.tick(symbol, max_age=0.2)
        rates = self._read_rates(symbol, "1m", 1440)
        if not rates:
            raise ChartUnavailable("Exness returned no 24h history for %s" % symbol)
        high = max(float(b[2]) for b in rates)
        low = min(float(b[3]) for b in rates)
        open24 = float(rates[0][1])
        vol = sum(int(b[5]) for b in rates)
        price = float(tick["bid"]) if tick else float(rates[-1][4])
        change = ((price - open24) / open24 * 100.0) if open24 else 0.0
        digits = int(meta["digits"])
        payload = {
            "symbol": symbol,
            "price": _round_price(price, digits),
            "open24": _round_price(open24, digits),
            "change": round(change, 2),
            "high": _round_price(high, digits),
            "low": _round_price(low, digits),
            "vol": int(vol),
            "digits": digits,
            "unit": meta["unit"],
            "currency": meta["currency"],
            "updated_at": int(now * 1000),
        }
        self._ticker_cache[symbol] = (now, payload)
        return payload

    def snapshot(self, symbol: str, tf: str = "5m", count: int = 600) -> Dict[str, Any]:
        symbol = str(symbol or "").strip()
        if tf not in TF_SEC:
            raise ChartUnavailable("unknown timeframe: %s" % tf)
        meta = self.meta(symbol)
        # The rate cache is shared between browsers: never write into it. Only the
        # trailing bar is copied, and only that copy follows the live tick.
        bars = list(self.candles(symbol, tf, count))
        if bars:
            bars[-1] = list(bars[-1])
        tick = self.tick(symbol, max_age=0.1)
        ticker = self.ticker(symbol)
        if tick:
            offset = float(tick["ms"]) / 1000.0 - time.time()
            last_bar = bars[-1]
            bucket = int(float(tick["ms"]) / 1000.0 // TF_SEC[tf] * TF_SEC[tf] * 1000)
            price = float(tick["bid"])
            if bucket == int(last_bar[0]):
                last_bar[2] = max(float(last_bar[2]), price)
                last_bar[3] = min(float(last_bar[3]), price)
                last_bar[4] = price
            elif bucket > int(last_bar[0]):
                # First print of a fresh bar: open at the previous close. High
                # and low must both span open <-> price, otherwise the candle
                # would draw an inverted wick when the tick is below the close.
                open_px = float(last_bar[4])
                bars.append([bucket, open_px, max(open_px, price), min(open_px, price), price, 0])
        else:
            offset = 0.0
        return {
            "t": "snap",
            "s": symbol,
            "tf": tf,
            "tf_sec": TF_SEC[tf],
            "offset": round(offset, 3),
            "meta": meta,
            "bars": bars,
            "ticker": ticker,
            "tape": self.tape_history(symbol),
            "now_ms": int(time.time() * 1000),
        }

    def tape_history(self, symbol: str, limit: int = 40) -> List[Dict[str, Any]]:
        now = time.time()
        hit = self._tape_cache.get(symbol)
        if hit and now - hit[0] < 20.0:
            return hit[1]
        meta = self.meta(symbol)
        bars = self._read_rates(symbol, "1m", 180)
        rows = tape_from_bars(bars, float(meta["point"]), int(meta["digits"]), limit)
        for row in rows:
            row["s"] = symbol
        self._tape_cache[symbol] = (now, rows)
        return rows

    def _m1_volume(self, symbol: str) -> Tuple[int, int]:
        """(current M1 bar time, its tick volume) - the participation counter."""
        if is_bd_symbol(symbol) and _bd_m1_volume is not None:
            try:
                return _bd_m1_volume(symbol)
            except Exception:
                return 0, 0
        _ensure_terminal()
        with IPC_LOCK:
            rates = mt5.copy_rates_from_pos(symbol, _tf_const("1m"), 0, 2)
        if rates is None or len(rates) == 0:
            return 0, 0
        last = rates[-1]
        return int(last["time"]), int(last["tick_volume"])

    # ------------------------------------------------------------- clients
    def add_client(self, loop: Any) -> Tuple[int, Any]:
        import asyncio

        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=300)
        with self._lock:
            cid = self._next_id
            self._next_id += 1
            self._clients[cid] = {"loop": loop, "queue": queue, "symbol": None}
        self._start_thread()
        return cid, queue

    def set_symbol(self, cid: int, symbol: str) -> None:
        symbol = str(symbol or "").strip()
        with self._lock:
            client = self._clients.get(cid)
            if not client:
                return
            old = client.get("symbol")
            if old == symbol:
                return
            if old:
                self._subs[old] = max(0, self._subs.get(old, 0) - 1)
                if self._subs[old] == 0:
                    self._subs.pop(old, None)
                    self._states.pop(old, None)
            client["symbol"] = symbol
            if symbol:
                self._subs[symbol] = self._subs.get(symbol, 0) + 1

    def remove_client(self, cid: int) -> None:
        with self._lock:
            client = self._clients.pop(cid, None)
            if not client:
                return
            old = client.get("symbol")
            if old:
                self._subs[old] = max(0, self._subs.get(old, 0) - 1)
                if self._subs[old] == 0:
                    self._subs.pop(old, None)
                    self._states.pop(old, None)

    def _push(self, loop: Any, queue: Any, frame: Dict[str, Any]) -> None:
        def put() -> None:
            try:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except Exception:
                        pass
                queue.put_nowait(frame)
            except Exception:
                pass

        try:
            loop.call_soon_threadsafe(put)
        except Exception:
            pass

    def _broadcast(self, symbol: str, frame: Dict[str, Any]) -> None:
        with self._lock:
            targets = [c for c in self._clients.values() if c.get("symbol") == symbol]
        for client in targets:
            self._push(client["loop"], client["queue"], frame)

    # ------------------------------------------------------------- sampler
    def _start_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="chart-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                symbols = [s for s, n in self._subs.items() if n > 0]
            backoff = 0.0
            for symbol in symbols:
                try:
                    self._sample(symbol)
                except ChartUnavailable:
                    # The Exness terminal went away. Back off instead of
                    # hammering a dead pipe at 20 fps - _ensure_terminal in
                    # the next sample re-attaches automatically once the
                    # terminal (or its auto-login) returns.
                    backoff = 0.5
                except Exception:
                    pass
            self._stop.wait(backoff if backoff else TICK_INTERVAL)

    def _state(self, symbol: str) -> Dict[str, Any]:
        state = self._states.get(symbol)
        if state is not None:
            return state
        meta = self.meta(symbol)
        state = {
            "symbol": symbol,
            "point": float(meta["point"]),
            "digits": int(meta["digits"]),
            "spread": 0.0,
            "last_bid": None,
            "last_ms": 0,
            "bar_time": 0,
            "tickvol": 0,
            "vol_at": 0.0,
            "moves": deque(maxlen=400),
            "deltas": deque(maxlen=200),
            "acc_ticks": 0,
            "last_print": 0.0,
            "pending": deque(maxlen=20),
            "prints": deque(maxlen=80),
            "print_id": 0,
        }
        self._states[symbol] = state
        return state

    def _sample(self, symbol: str) -> None:
        state = self._state(symbol)
        now = time.time()
        tick = self.tick(symbol, max_age=TICK_INTERVAL * 0.9)
        if not tick:
            return
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        ms = int(tick["ms"])
        digits = int(state["digits"])
        point = float(state["point"]) or (10 ** -digits)
        spread = max(0.0, ask - bid)
        state["spread"] = spread
        # Follow-through is measured on wall-clock time, so it must run even
        # while the feed is quiet (the Exness demo feed groups ticks).
        self._followups(state, bid, now)

        changed = (ms != state["last_ms"]) or (bid != state["last_bid"])
        if not changed:
            return

        # --- participation: how many ticks printed in this slice -------------
        delta = 0
        if now - float(state["vol_at"]) >= VOLUME_INTERVAL:
            try:
                bar_time, tickvol = self._m1_volume(symbol)
            except Exception:
                bar_time, tickvol = 0, 0
            if bar_time:
                if state["bar_time"] == bar_time:
                    delta = max(0, tickvol - int(state["tickvol"]))
                else:
                    # New minute: the whole new-bar volume belongs to this slice.
                    delta = max(0, min(tickvol, 200))
            state["bar_time"] = bar_time
            state["tickvol"] = tickvol
            state["vol_at"] = now
            if delta:
                state["deltas"].append((now, delta))
                state["acc_ticks"] = min(999, int(state["acc_ticks"]) + delta)

        self._broadcast(symbol, {
            "t": "tick",
            "s": symbol,
            "b": _round_price(bid, digits),
            "a": _round_price(ask, digits),
            "tm": ms,
            "d": delta,
        })

        last_bid = state["last_bid"]
        last_print = float(state["last_print"])
        if last_bid is None:
            state["last_bid"] = bid
            state["last_ms"] = ms
            return

        move = bid - float(last_bid)
        state["moves"].append(abs(move))

        median = statistics.median(state["moves"]) if len(state["moves"]) >= 20 else 0.0
        threshold = max(spread * IMPULSE_SPREAD_FACTOR, median * IMPULSE_MEDIAN_FACTOR,
                        point * 2.0)
        if abs(move) >= threshold and now - last_print >= PRINT_COOLDOWN:
            side = "BUY" if move > 0 else "SELL"
            state["print_id"] = int(state["print_id"]) + 1
            pid = "%s-%d" % (symbol, state["print_id"])
            ticks = int(state["acc_ticks"])
            state["acc_ticks"] = 0
            print_row = {
                "t": "print",
                "id": pid,
                "s": symbol,
                "tm": int(now * 1000),
                "px": _round_price(bid, digits),
                "side": side,
                "ticks": ticks,
                "mv": _round_price(abs(move), digits),
                "src": "live",
            }
            state["prints"].appendleft(print_row)
            state["pending"].append({
                "id": pid,
                "at": now,
                "px": bid,
                "side": side,
                "spread": spread,
            })
            state["last_print"] = now
            self._broadcast(symbol, print_row)

        state["last_bid"] = bid
        state["last_ms"] = ms

    def _followups(self, state: Dict[str, Any], bid: float, now: float) -> None:
        """Reports whether price moved with each big print, 6s after it."""
        if not state["pending"]:
            return
        symbol = str(state["symbol"])
        digits = int(state["digits"])
        point = float(state["point"]) or (10 ** -digits)
        for pending in list(state["pending"]):
            if now - float(pending["at"]) < FOLLOW_AFTER:
                continue
            direction = 1.0 if pending["side"] == "BUY" else -1.0
            follow = (bid - float(pending["px"])) * direction
            floor = max(float(pending["spread"]), point * 2.0)
            if follow > floor:
                verdict = "UP"
            elif follow < -floor:
                verdict = "DOWN"
            else:
                verdict = "FLAT"
            self._broadcast(symbol, {
                "t": "follow",
                "id": pending["id"],
                "s": symbol,
                "mv": _round_price(follow, digits),
                "st": verdict,
                "px": _round_price(bid, digits),
            })
            try:
                state["pending"].remove(pending)
            except ValueError:
                pass

    def recent_prints(self, symbol: str, limit: int = 30) -> List[Dict[str, Any]]:
        state = self._states.get(symbol)
        if not state:
            return []
        return list(state["prints"])[:limit]


_FEED: Optional[ChartFeed] = None
_FEED_LOCK = threading.Lock()


def get_feed() -> ChartFeed:
    global _FEED
    with _FEED_LOCK:
        if _FEED is None:
            _FEED = ChartFeed()
        return _FEED
