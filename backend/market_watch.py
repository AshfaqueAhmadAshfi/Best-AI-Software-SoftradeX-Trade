"""Market Watch Dial & Pressure Engine:
Calculates real-time Buyer vs. Seller pressure (Order Flow / Candle Volume Delta)
for all active trading markets on every engine tick (live stream, no polling timer).
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

PREFERRED_WATCH_SYMBOLS = [
    # Serial 1 (Top row: 5 markets ending with DXY)
    "BTCUSDTm",
    "BTCUSDm",
    "XAUUSD247m",
    "XAUUSDm",
    "DXY",
    # Serial 2 (Bottom row: 5 markets)
    "USOILm",     # Crude Oil
    "XAUGBPm",
    "USDJPYm",
    "EURUSDm",
    "USTECm",
]

SYMBOL_CANDIDATES: Dict[str, List[str]] = {
    "BTCUSDTm": ["BTCUSDTm", "BTCUSDT", "BTCUSDT."],
    "BTCUSDm": ["BTCUSDm", "BTCUSD", "BTCUSD."],
    "XAUUSD247m": ["XAUUSD247m", "XAUUSD247"],
    "XAUUSDm": ["XAUUSDm", "XAUUSD", "GOLDm", "GOLD"],
    "DXY": ["DXY", "DXYm", "USDX", "USDXm", "DXY.", "USD_INDEX"],
    "USOILm": ["USOILm", "USOIL", "XTIUSDm", "XTIUSD", "CLm", "CL", "Crude Oil"],
    "XAUGBPm": ["XAUGBPm", "XAUGBP", "XAUGBP."],
    "USDJPYm": ["USDJPYm", "USDJPY", "USDJPY."],
    "EURUSDm": ["EURUSDm", "EURUSD", "EURUSD."],
    "USTECm": ["USTECm", "USTEC", "NAS100m", "NAS100", "US100m", "US100"],
}

# Freshness window only - this is NOT a UI refresh timer. The engine loop
# (400 ms / 250 ms / 150 ms) recomputes watches every cycle and pushes every
# change over the WebSocket, so the dials move continuously. The tiny cache
# only prevents several callers inside the same tick from re-reading MT5.
WATCH_CACHE_SEC: float = 0.5

_cache_lock_ts: float = 0.0
_cached_watches: Dict[str, Any] = {}
_digits_cache: Dict[str, int] = {}


def get_live_quotes(symbols: List[str]) -> List[Dict[str, Any]]:
    """Ultra-light tick snapshot (bid / ask / spread) for the watch dials.

    Called by the engine about 20 times per second, so it deliberately skips the
    candle-history read: the heavier buyer/seller pressure is computed on the
    normal engine tick and merged into the same WebSocket frame.
    """
    if mt5 is None:
        return []
    out: List[Dict[str, Any]] = []
    for sym in symbols:
        if not sym:
            continue
        try:
            tick = mt5.symbol_info_tick(sym)
        except Exception:
            continue
        if not tick:
            continue
        digits = _digits_cache.get(sym)
        if digits is None:
            try:
                info = mt5.symbol_info(sym)
                digits = int(getattr(info, "digits", 2)) if info else 2
            except Exception:
                digits = 2
            _digits_cache[sym] = digits
        ask = float(tick.ask)
        bid = float(tick.bid)
        out.append({
            "symbol": sym,
            "bid": bid,
            "ask": ask,
            "spread": round(ask - bid, digits if digits <= 4 else 4),
            "digits": digits,
            "tick_time": int(getattr(tick, "time", 0) or 0),
            "updated_at": time.time(),
        })
    return out


def _calc_pressure_for_rates(tick_bid: float, tick_ask: float, rates: Any, digits: int = 2) -> Dict[str, Any]:
    if rates is None or len(rates) == 0:
        return {
            "buyer_pct": 50.0,
            "seller_pct": 50.0,
            "dominant": "NEUTRAL",
            "strength": "BALANCED",
            "needle_deg": 0.0,
            "candle_flow": "SIDEWAYS",
            "details": "Insufficient candle rates",
        }

    b_vol = 0.0
    s_vol = 0.0
    total_tick_vol = 0.0
    num_rates = len(rates)

    for i, r in enumerate(rates):
        o = float(r["open"])
        h = float(r["high"])
        l = float(r["low"])
        c = float(r["close"])
        vol = max(1.0, float(r["tick_volume"]))
        total_tick_vol += vol

        rng = max(1e-5, h - l)
        body = abs(c - o)
        u_wick = max(0.0, h - max(o, c))
        l_wick = max(0.0, min(o, c) - l)

        # Recency weight: more recent candles count more
        recency = 0.5 + 0.5 * ((i + 1) / num_rates)
        w_vol = vol * recency

        if c >= o:
            # Bullish candle: body + lower wick buying vs upper wick selling
            b_val = (body + l_wick * 0.7 + (rng - u_wick) * 0.3) / rng
            s_val = (u_wick * 0.8) / rng
        else:
            # Bearish candle: body + upper wick selling vs lower wick buying
            s_val = (body + u_wick * 0.7 + (rng - l_wick) * 0.3) / rng
            b_val = (l_wick * 0.8) / rng

        b_vol += max(0.01, b_val) * w_vol
        s_vol += max(0.01, s_val) * w_vol

    # Factor in the current tick position relative to latest candle range
    last_r = rates[-1]
    curr_rng = max(1e-5, float(last_r["high"]) - float(last_r["low"]))
    pos_ratio = (tick_bid - float(last_r["low"])) / curr_rng
    pos_ratio = max(0.0, min(1.0, pos_ratio))
    avg_vol = (total_tick_vol / num_rates) if num_rates else 50.0
    b_vol += pos_ratio * avg_vol * 0.6
    s_vol += (1.0 - pos_ratio) * avg_vol * 0.6

    total = max(1e-5, b_vol + s_vol)
    buyer_pct = round((b_vol / total) * 100.0, 1)
    seller_pct = round(100.0 - buyer_pct, 1)

    # Determine dominance & strength
    delta = buyer_pct - seller_pct
    if delta >= 12.0:
        dominant = "BUYER"
        strength = "STRONG" if delta >= 26.0 else "MODERATE"
    elif delta <= -12.0:
        dominant = "SELLER"
        strength = "STRONG" if delta <= -26.0 else "MODERATE"
    else:
        dominant = "NEUTRAL"
        strength = "BALANCED"

    # Candle flow
    first_c = float(rates[0]["open"])
    last_c = float(rates[-1]["close"])
    candle_flow = "BULLISH" if last_c > first_c else ("BEARISH" if last_c < first_c else "SIDEWAYS")

    # Needle degree:
    # 0 deg = 12 o'clock (50/50 Neutral)
    # -65 deg = Left (Buyer 100%)
    # +65 deg = Right (Seller 100%)
    needle_deg = round(((seller_pct - buyer_pct) / 100.0) * 65.0, 1)

    details = f"Buyer: {buyer_pct}% · Seller: {seller_pct}% ({strength} {dominant})"

    return {
        "buyer_pct": buyer_pct,
        "seller_pct": seller_pct,
        "dominant": dominant,
        "strength": strength,
        "needle_deg": needle_deg,
        "candle_flow": candle_flow,
        "details": details,
    }


def _display_name(sym: str) -> str:
    up = (sym or "").upper()
    if any(k in up for k in ("USOIL", "XTIUSD", "CRUDE", "CL")):
        return "Crude Oil"
    if "DXY" in up or "USDX" in up:
        return "DXY"
    if "XAUGBP" in up:
        return "XAUGBP"
    if "USDJPY" in up:
        return "USDJPY"
    if "EURUSD" in up:
        return "EURUSD"
    if "USTEC" in up or "NAS100" in up or "US100" in up:
        return "USTEC"
    if "XAUUSD247" in up:
        return "XAUUSD247m"
    if "XAUUSD" in up or "GOLD" in up:
        return "XAUUSDm"
    if "BTCUSDT" in up:
        return "BTCUSDTm"
    if "BTCUSD" in up:
        return "BTCUSDm"
    return sym


def calculate_symbol_watch(symbol: str) -> Dict[str, Any]:
    """Calculates pressure and market stats for a single symbol."""
    sym = symbol.strip()
    if mt5 is None:
        # Fallback simulated response
        return _fallback_symbol_watch(sym)

    try:
        if not mt5.terminal_info():
            mt5.initialize()
    except Exception:
        pass

    candidates = [sym] + SYMBOL_CANDIDATES.get(sym, [])
    tick = None
    info = None
    resolved_sym = sym
    for c in candidates:
        try:
            t = mt5.symbol_info_tick(c)
            i = mt5.symbol_info(c)
            if not t:
                mt5.symbol_select(c, True)
                t = mt5.symbol_info_tick(c)
                i = mt5.symbol_info(c)
            if t and i:
                tick = t
                info = i
                resolved_sym = c
                break
        except Exception:
            continue

    if not tick:
        return _fallback_symbol_watch(sym)

    digits = int(getattr(info, "digits", 2)) if info else 2
    ask = float(tick.ask)
    bid = float(tick.bid)
    spread = round(ask - bid, digits if digits <= 4 else 4)

    # Fetch last 10 M1 rates
    try:
        rates = mt5.copy_rates_from_pos(resolved_sym, mt5.TIMEFRAME_M1, 0, 10)
    except Exception:
        rates = None

    pressure = _calc_pressure_for_rates(bid, ask, rates, digits)

    return {
        "symbol": resolved_sym,
        "displayName": _display_name(sym),
        "ask": ask,
        "bid": bid,
        "spread": spread,
        "digits": digits,
        "buyer_pct": pressure["buyer_pct"],
        "seller_pct": pressure["seller_pct"],
        "dominant": pressure["dominant"],
        "strength": pressure["strength"],
        "needle_deg": pressure["needle_deg"],
        "candle_flow": pressure["candle_flow"],
        "details": pressure["details"],
        "updated_at": time.time(),
        "live": True,
    }


def _fallback_symbol_watch(sym: str) -> Dict[str, Any]:
    """Generates realistic fallback data if MT5 is offline/uninitialized."""
    now = time.time()
    seed = sum(ord(c) for c in sym) + int(now // 5)
    ratio = (math.sin(seed * 0.7) + 1.0) / 2.0
    buyer_pct = round(20.0 + ratio * 60.0, 1)
    seller_pct = round(100.0 - buyer_pct, 1)

    delta = buyer_pct - seller_pct
    dominant = "BUYER" if delta >= 10.0 else ("SELLER" if delta <= -10.0 else "NEUTRAL")
    strength = "STRONG" if abs(delta) >= 25.0 else ("MODERATE" if abs(delta) >= 10.0 else "BALANCED")
    needle_deg = round(((seller_pct - buyer_pct) / 100.0) * 65.0, 1)

    up = sym.upper()
    if "BTC" in up:
        digits = 2
        base_price = 83650.0
        spread = 10.0
    elif "DXY" in up or "USDX" in up:
        digits = 2
        base_price = 104.50
        spread = 0.05
    elif any(k in up for k in ("USOIL", "XTIUSD", "CRUDE", "CL")):
        digits = 2
        base_price = 71.20
        spread = 0.04
    elif "XAUGBP" in up:
        digits = 2
        base_price = 2045.0
        spread = 0.40
    elif "USDJPY" in up or "JPY" in up:
        digits = 3
        base_price = 152.350
        spread = 0.015
    elif "EURUSD" in up:
        digits = 5
        base_price = 1.08550
        spread = 0.00015
    elif any(k in up for k in ("USTEC", "NAS100", "US100")):
        digits = 2
        base_price = 19850.0
        spread = 1.50
    elif "XAU" in up or "GOLD" in up:
        digits = 2
        base_price = 2650.0
        spread = 0.25
    else:
        digits = 2
        base_price = 100.0
        spread = 0.10

    bid = round(base_price + math.cos(seed) * (base_price * 0.0008), digits)
    ask = round(bid + spread, digits)

    return {
        "symbol": sym,
        "displayName": _display_name(sym),
        "ask": ask,
        "bid": bid,
        "spread": spread,
        "digits": digits,
        "buyer_pct": buyer_pct,
        "seller_pct": seller_pct,
        "dominant": dominant,
        "strength": strength,
        "needle_deg": needle_deg,
        "candle_flow": "BULLISH" if delta > 0 else "BEARISH",
        "details": f"Buyer: {buyer_pct}% · Seller: {seller_pct}% (Demo/Fallback)",
        "updated_at": now,
        "live": False,
    }


def get_all_market_watches(custom_symbols: Optional[List[str]] = None, force: bool = False) -> Dict[str, Any]:
    """Gathers buyer vs seller pressure for all available markets.
    Recomputed on every engine tick (WATCH_CACHE_SEC freshness window).
    """
    global _cache_lock_ts, _cached_watches
    now = time.time()

    if not force and _cached_watches and (now - _cache_lock_ts) < WATCH_CACHE_SEC:
        return _cached_watches

    symbols_to_check: List[str] = []
    if custom_symbols:
        symbols_to_check = [s.strip() for s in custom_symbols if s.strip()]

    if not symbols_to_check:
        # Discover available markets
        symbols_to_check = list(PREFERRED_WATCH_SYMBOLS)
        if mt5 is not None:
            try:
                allsym = mt5.symbols_get()
                if allsym:
                    for s in allsym:
                        name = str(getattr(s, "name", "") or "")
                        up = name.upper()
                        if any(b in up for b in ("BTCUSD", "BTCUSDT", "XAUUSD", "GOLD", "DXY", "USDX", "USOIL", "XTIUSD", "XAUGBP", "USDJPY", "EURUSD", "USTEC", "NAS100")) and not any(x in up for x in ("XAG", "ETH")):
                            if name not in symbols_to_check:
                                symbols_to_check.append(name)
            except Exception:
                pass

    # Keep unique symbols
    seen = set()
    ordered_syms: List[str] = []
    for s in symbols_to_check:
        if s and s not in seen:
            seen.add(s)
            ordered_syms.append(s)

    # Calculate watch data for each symbol (top 10 to cover both 5-market rows)
    watches = [calculate_symbol_watch(s) for s in ordered_syms[:10]]


    res = {
        "ok": True,
        "updated_at": now,
        "interval_sec": WATCH_CACHE_SEC,
        "live_stream": True,
        "count": len(watches),
        "watches": watches,
    }

    _cached_watches = res
    _cache_lock_ts = now
    return res
