"""BD (Dhaka Stock Exchange) synthetic market feed.

Exness MT5 has no DSE instruments, so the BD Stock Market dashboard is powered
by this self-contained, deterministic feed:

* ``symbols()``    - the BD instrument catalogue the BD chart can show
* ``candles()``    - OHLCV bars for one timeframe (a pure function of time, so
                     every browser sees the same history after any restart)
* ``tick()``       - the live bid/ask the chart + flow terminal use
* ``watches()``    - buyer vs seller pressure rows (same shape as market_watch)
* ``state()``      - everything the BD pipeline stages read (AI board,
                     next-day outlook, setups, sessions, next event, curve)

The price engine is octave value-noise over seconds, so a bar generated ten
minutes ago always equals the one generated now (no storage, no drift). DSE
hours are respected: Sun-Thu, pre-open 09:30, continuous 10:00-14:00, post
close 14:00-14:30 (Asia/Dhaka = UTC+6). Outside trading hours the feed keeps
streaming (low simulation amplitude) but the rows report the real DSE status.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

TF_SEC: Dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

# DSE hour windows in Asia/Dhaka local seconds (UTC+6, no DST).
BDT_OFFSET = 6 * 3600
PRE_OPEN_START = 9 * 3600 + 30 * 60      # 09:30
OPEN_START = 10 * 3600                   # 10:00
OPEN_END = 14 * 3600                     # 14:00
POST_END = 14 * 3600 + 30 * 60           # 14:30


def _is_weekend(dhaka_day: int) -> bool:
    # 1970-01-01 was a Thursday; map so 0=Sunday .. 5=Friday, 6=Saturday.
    dow = (dhaka_day + 4) % 7
    return dow in (5, 6)


# ---------------------------------------------------------------------------
# Deterministic noise engine (no RNG state, no storage, always reproducible)
# ---------------------------------------------------------------------------

def _mix32(x: int) -> int:
    x &= 0xFFFFFFFF
    x ^= x >> 16
    x = (x * 2246822519) & 0xFFFFFFFF
    x ^= x >> 13
    x = (x * 3266489917) & 0xFFFFFFFF
    x ^= x >> 16
    return x & 0xFFFFFFFF


def _seed_of(text: str) -> int:
    x = 2166136261
    for b in str(text).encode("utf-8", "ignore"):
        x = ((x ^ b) * 16777619) & 0xFFFFFFFF
    return _mix32(x)


# ---------------------------------------------------------------------------
# Instrument catalogue: DSE indices + the big DSE scrips + the interbank pair.
# ---------------------------------------------------------------------------
_CATALOG_DEFINITIONS: List[Dict[str, Any]] = [
    {"symbol": "DSEX",       "label": "DSEX",    "pair": "INDEX", "icon": "🇧🇩", "unit": "pts",   "kind": "index",
     "base": 5820.0, "vol": 0.0035, "digits": 2, "desc": "Dhaka Stock Exchange Broad Index"},
    {"symbol": "DSE30",      "label": "DSE30",   "pair": "INDEX", "icon": "🇧🇩", "unit": "pts",   "kind": "index",
     "base": 2210.0, "vol": 0.0040, "digits": 2, "desc": "DSE-30 Blue Chip Index"},
    {"symbol": "GP",         "label": "GP",      "pair": "BDT",   "icon": "📱", "unit": "share", "kind": "stock",
     "base": 316.5,  "vol": 0.011, "digits": 2, "desc": "Grameenphone Ltd"},
    {"symbol": "BATBC",      "label": "BATBC",   "pair": "BDT",   "icon": "🚬", "unit": "share", "kind": "stock",
     "base": 382.0,  "vol": 0.010, "digits": 2, "desc": "BAT Bangladesh"},
    {"symbol": "SQURPHARMA", "label": "SQUR PH", "pair": "BDT",   "icon": "💊", "unit": "share", "kind": "stock",
     "base": 205.4,  "vol": 0.010, "digits": 2, "desc": "Square Pharmaceuticals"},
    {"symbol": "BRACBANK",   "label": "BRAC",    "pair": "BDT",   "icon": "🏦", "unit": "share", "kind": "stock",
     "base": 58.3,   "vol": 0.013, "digits": 2, "desc": "BRAC Bank"},
    {"symbol": "WALTONHPL",  "label": "WALTON",  "pair": "BDT",   "icon": "❄️", "unit": "share", "kind": "stock",
     "base": 1180.0, "vol": 0.014, "digits": 2, "desc": "Walton Hi-Tech Industries"},
    {"symbol": "RENATA",     "label": "RENATA",  "pair": "BDT",   "icon": "🧪", "unit": "share", "kind": "stock",
     "base": 878.0,  "vol": 0.012, "digits": 2, "desc": "Renata Ltd"},
    {"symbol": "LHBL",       "label": "LHBL",    "pair": "BDT",   "icon": "🏗️", "unit": "share", "kind": "stock",
     "base": 74.6,   "vol": 0.012, "digits": 2, "desc": "LafargeHolcim Bangladesh"},
    {"symbol": "UPGDCL",     "label": "UPGDCL",  "pair": "BDT",   "icon": "⚡", "unit": "share", "kind": "stock",
     "base": 152.0,  "vol": 0.013, "digits": 2, "desc": "United Power Generation"},
    {"symbol": "BEXIMCO",    "label": "BEXIMCO", "pair": "BDT",   "icon": "🧵", "unit": "share", "kind": "stock",
     "base": 91.5,   "vol": 0.016, "digits": 2, "desc": "Beximco Ltd"},
    {"symbol": "CITYBANK",   "label": "CITY",    "pair": "BDT",   "icon": "🏦", "unit": "share", "kind": "stock",
     "base": 24.3,   "vol": 0.015, "digits": 2, "desc": "The City Bank"},
    {"symbol": "DUTCHBANGL", "label": "DBBL",    "pair": "BDT",   "icon": "🏦", "unit": "share", "kind": "stock",
     "base": 48.7,   "vol": 0.014, "digits": 2, "desc": "Dutch-Bangla Bank"},
    {"symbol": "ALARABANK",  "label": "ALARAI",  "pair": "BDT",   "icon": "🕌", "unit": "share", "kind": "stock",
     "base": 21.6,   "vol": 0.016, "digits": 2, "desc": "Al-Arafah Islami Bank"},
    {"symbol": "MTB",        "label": "MTB",     "pair": "BDT",   "icon": "💰", "unit": "share", "kind": "stock",
     "base": 19.4,   "vol": 0.015, "digits": 2, "desc": "Mutual Trust Bank"},
    {"symbol": "DBH",        "label": "DBH",     "pair": "BDT",   "icon": "🏠", "unit": "share", "kind": "stock",
     "base": 75.4,   "vol": 0.012, "digits": 2, "desc": "DBH Finance"},
    {"symbol": "OLYMPIC",    "label": "OLYMPIC", "pair": "BDT",   "icon": "🏃", "unit": "share", "kind": "stock",
     "base": 105.0,  "vol": 0.013, "digits": 2, "desc": "Olympic Industries"},
    {"symbol": "ICB",        "label": "ICB",     "pair": "BDT",   "icon": "🏛️", "unit": "share", "kind": "stock",
     "base": 162.5,  "vol": 0.012, "digits": 2, "desc": "Investment Corporation of Bangladesh"},
    {"symbol": "AMCLPRAN",   "label": "PRAN",    "pair": "BDT",   "icon": "🥤", "unit": "share", "kind": "stock",
     "base": 158.0,  "vol": 0.012, "digits": 2, "desc": "AMCL (Pran)"},
    {"symbol": "NCCBANK",    "label": "NCC",     "pair": "BDT",   "icon": "📇", "unit": "share", "kind": "stock",
     "base": 15.2,   "vol": 0.015, "digits": 2, "desc": "NCC Bank"},
    {"symbol": "SOUTHEASTB", "label": "SEBL",    "pair": "BDT",   "icon": "🧮", "unit": "share", "kind": "stock",
     "base": 14.9,   "vol": 0.016, "digits": 2, "desc": "Southeast Bank"},
    {"symbol": "USDBDT",     "label": "USD/BDT", "pair": "BDT",   "icon": "💱", "unit": "rate",  "kind": "currency",
     "base": 122.6,  "vol": 0.0022, "digits": 3, "desc": "Interbank USD / BDT rate"},
]

_CATALOG: Dict[str, Dict[str, Any]] = {d["symbol"]: d for d in _CATALOG_DEFINITIONS}
for _d in _CATALOG.values():
    _d["seed"] = _seed_of("bd:" + _d["symbol"])
BD_SYMBOLS: List[str] = list(_CATALOG.keys())
BD_ALTS: Dict[str, List[str]] = {}
for _d in _CATALOG_DEFINITIONS:
    for _alt in (_d["symbol"], _d["label"].replace("/", "")):
        BD_ALTS.setdefault(_alt.upper(), []).append(_d["symbol"])


def is_bd_symbol(symbol: str) -> bool:
    s = str(symbol or "").strip().upper()
    return bool(s) and (s in _CATALOG or s in BD_ALTS or s.startswith("DSE"))


def _spec(symbol: str) -> Dict[str, Any]:
    s = str(symbol or "").strip().upper()
    if s in _CATALOG:
        return _CATALOG[s]
    if s in BD_ALTS:
        return _CATALOG[BD_ALTS[s][0]]
    return _CATALOG_DEFINITIONS[0]


# ---------------------------------------------------------------------------
# Deterministic noise engine: continuous functions of (symbol, time)
# ---------------------------------------------------------------------------

def _vnoise(t: float, period: float, seed: int) -> float:
    """Cosine-blended integer-noise value in -1..1, continuous everywhere."""
    if period <= 0:
        return 0.0
    n = t / period
    i = math.floor(n)
    f = n - i
    x1 = _mix32(seed * 0x85EBCA6B + int(i) * 0x9E3779B1)
    x2 = _mix32(seed * 0xC2B2AE3D + (int(i) + 1) * 0x27D4EB2F)
    a = (x1 & 0xFFFF) / 65535.0
    b = (x2 & 0xFFFF) / 65535.0
    s = (1.0 - math.cos(math.pi * f)) * 0.5
    v = a + (b - a) * s
    return v * 2.0 - 1.0


# Octaves: (period seconds, relative strength) - slow trend to live tape noise.
_OCTAVES: Tuple[Tuple[float, float], ...] = (
    (172800.0, 2.6), (86400.0, 1.8), (21600.0, 1.35), (5400.0, 0.8),
    (900.0, 0.45), (150.0, 0.22), (35.0, 0.12),
)


def _session_factor(t: float) -> float:
    """Liquidity / volume multiplier by DSE phase (Sun-Thu, Asia/Dhaka)."""
    ls = int(t) + BDT_OFFSET
    day = math.floor(ls / 86400.0)
    tod = ls - day * 86400.0
    if _is_weekend(int(day)):
        return 0.10
    if PRE_OPEN_START <= tod < OPEN_START:
        return 0.28
    if OPEN_START <= tod < OPEN_END:
        return 1.0
    if OPEN_END <= tod < POST_END:
        return 0.30
    return 0.06


def _amp_scale(t: float) -> float:
    """Smooth price-noise amplitude scaler (1.0 open .. 0.06 closed)."""
    ls = int(t) + BDT_OFFSET
    day = math.floor(ls / 86400.0)
    tod = ls - day * 86400.0
    pts = ((0.0, 0.10), (30600.0, 0.18), (34200.0, 0.50), (36000.0, 1.0), (46800.0, 0.95),
           (50400.0, 0.45), (52200.0, 0.18), (54000.0, 0.11), (90000.0, 0.10))
    prev_t, prev_v = pts[0]
    for ti, vi in pts:
        if tod < ti:
            f = (tod - prev_t) / max(1.0, ti - prev_t)
            f = f * f * (3.0 - 2.0 * f)
            v = prev_v + (vi - prev_v) * f
            break
        prev_t, prev_v = ti, vi
    else:
        v = pts[-1][1]
    if _is_weekend(int(day)):
        v *= 0.4
    return v


def _log_price(symbol: str, t: float) -> float:
    """Continuous log-price; also used by the BD watch dials and AI board."""
    spec = _spec(symbol)
    lp = math.log(spec["base"])
    seed = int(spec["seed"])
    mod = _amp_scale(t)
    for period, strength in _OCTAVES:
        amp = strength * (mod if period <= 21600.0 else 1.0)
        lp += _vnoise(t, period, seed) * amp * spec["vol"]
    return lp


def price_at(symbol: str, t: float) -> float:
    return max(0.05, math.exp(_log_price(symbol, t)))


def _round(value: float, digits: int) -> float:
    return round(float(value) + 1e-12, int(digits))


def tick(symbol: str) -> Optional[Dict[str, Any]]:
    """Live BD tick: bid/ask around the deterministic price."""
    if not is_bd_symbol(symbol):
        return None
    spec = _spec(symbol)
    digits = int(spec["digits"])
    t = time.time()
    px = price_at(symbol, t)
    point = 10.0 ** -digits
    wobble = point * (1.0 + 3.0 * abs(_vnoise(t, 90.0, int(spec["seed"]) ^ 0x5DEECE66)))
    half = max(point * 0.5, wobble * 0.5)
    spread = _round(half * 2.0, digits)
    bid = _round(px - half, digits)
    ask = _round(px + half, digits)
    vol = max(1.0, _bar_volume(symbol, t, 60.0) * _session_factor(t) / 60.0)
    return {
        "bid": bid,
        "ask": ask,
        "ms": int(t * 1000),
        "time": int(t),
        "volume": int(vol),
        "volume_real": float(px),
    }


def _bar_volume(symbol: str, t: float, span: float) -> float:
    """Deterministic participation volume for one bar of ``span`` seconds."""
    spec = _spec(symbol)
    seed = int(spec["seed"])
    prev = price_at(symbol, t - float(span))
    move = abs(price_at(symbol, t) / max(1e-9, prev) - 1.0)
    intensity = 0.55 + 9.0 * move / max(1e-6, spec["vol"]) + 0.5 * max(0.0, _vnoise(t, max(60.0, span * 5.0), seed ^ 0xA5A5A5A5))
    scale = (_mix32(seed + 0x71ED270B) & 0xFFFFFF) / float(0x1000000)
    base = 300.0 + 5200.0 * scale
    if spec["kind"] == "index":
        base *= 1.4
    return max(1.0, base * intensity * max(0.35, _session_factor(t)))


def bars(symbol: str, tf: str, count: int) -> List[List[float]]:
    """OHLCV bars [[ms, open, high, low, close, tick_vol], ...] for one tf."""
    if not is_bd_symbol(symbol):
        raise LookupError("not a BD symbol: %s" % symbol)
    if tf not in TF_SEC:
        raise LookupError("unknown timeframe: %s" % tf)
    spec = _spec(symbol)
    digits = int(spec["digits"])
    span = TF_SEC[tf]
    now = int(time.time())
    last_open = now - (now % span)
    out: List[List[float]] = []
    for i in range(int(count)):
        start = last_open - (int(count) - 1 - i) * span
        end = min(start + span, now)
        if end <= start:
            end = start + 1
        samples = max(2, min(12, span // 45))
        step = (end - start) / float(samples)
        highs: List[float] = []
        for k in range(samples):
            td = start + step * (k + 0.5)
            highs.append(price_at(symbol, td))
        o = highs[0]
        c = highs[-1]
        h = max(max(highs), o)
        l = min(min(highs), o)
        bar_t = float(int((start + end) / 2))
        vol = _bar_volume(symbol, bar_t, span) * max(0.06, _session_factor(bar_t)) * span / 60.0
        out.append([
            start * 1000,
            _round(o, digits),
            _round(h, digits),
            _round(l, digits),
            _round(c, digits),
            int(max(1, vol)),
        ])
    return out


def candles(symbol: str, tf: str, count: int) -> List[List[float]]:
    return bars(symbol, tf, count)


def m1_volume(symbol: str) -> Tuple[int, int]:
    """(current M1 bar time, its tick volume) - the participation counter."""
    if not is_bd_symbol(symbol):
        return 0, 0
    now = int(time.time())
    bar_time = now - (now % 60)
    try:
        row = bars(symbol, "1m", 1)[0]
        return int(row[0] // 1000), int(row[5])
    except Exception:
        return bar_time, 0


# ---------------------------------------------------------------------------
# chart-feed style payloads (same shapes the dashboard already knows)
# ---------------------------------------------------------------------------

def meta(symbol: str) -> Dict[str, Any]:
    spec = _spec(symbol)
    digits = int(spec["digits"])
    return {
        "symbol": spec["symbol"],
        "label": spec["label"],
        "pair": spec["pair"],
        "icon": spec["icon"],
        "unit": spec["unit"],
        "kind": spec["kind"],
        "alts": [spec["symbol"], spec["label"]],
        "description": spec["desc"],
        "digits": digits,
        "point": 10.0 ** -digits,
        "spread_points": max(1, int(round(_spread(symbol) / (10.0 ** -digits)))),
        "contract": 1.0,
        "currency": spec["pair"],
    }


def _spread(symbol: str) -> float:
    spec = _spec(symbol)
    digits = int(spec["digits"])
    point = 10.0 ** -digits
    return max(point, _round(point * (1.0 + 0.5 * ((_mix32(int(spec["seed"]) + 0x51ED270B) & 0xFFFFFF) / float(0x1000000))), digits) * 2.0)


def ticker(symbol: str) -> Dict[str, Any]:
    spec = _spec(symbol)
    digits = int(spec["digits"])
    rows = bars(symbol, "1m", 1440)
    high = max(r[2] for r in rows)
    low = min(r[3] for r in rows)
    open24 = rows[0][1]
    vol = sum(int(r[5]) for r in rows)
    tk = tick(symbol) or {}
    price = float(tk.get("bid") or rows[-1][4])
    change = ((price - open24) / open24 * 100.0) if open24 else 0.0
    return {
        "symbol": spec["symbol"],
        "price": _round(price, digits),
        "open24": _round(open24, digits),
        "change": round(change, 2),
        "high": _round(high, digits),
        "low": _round(low, digits),
        "vol": int(vol),
        "digits": digits,
        "unit": spec["unit"],
        "currency": spec["pair"],
        "updated_at": int(time.time() * 1000),
    }


def snapshot(symbol: str, tf: str = "5m", count: int = 600) -> Dict[str, Any]:
    if tf not in TF_SEC:
        raise LookupError("unknown timeframe: %s" % tf)
    t = time.time()
    tk = tick(symbol) or {}
    rows = bars(symbol, tf, count)
    offset = 0.0
    if rows:
        last = rows[-1]
        price = float(tk.get("bid") or last[4])
        bucket = int(float(tk.get("ms") or t * 1000.0) // TF_SEC[tf] * TF_SEC[tf])
        if bucket == int(last[0]):
            last[2] = max(float(last[2]), price)
            last[3] = min(float(last[3]), price)
            last[4] = price
        elif bucket > int(last[0]):
            open_px = float(last[4])
            rows.append([bucket, open_px, max(open_px, price), min(open_px, price), price, 0])
    return {
        "t": "snap",
        "s": _spec(symbol)["symbol"],
        "tf": tf,
        "tf_sec": TF_SEC[tf],
        "offset": round(offset, 3),
        "meta": meta(symbol),
        "bars": rows,
        "ticker": ticker(symbol),
        "tape": [],
        "now_ms": int(t * 1000),
    }


def symbols() -> Dict[str, Any]:
    rows = [meta(sym) for sym in BD_SYMBOLS]
    return {
        "available": bool(rows),
        "message": "" if rows else "No BD instruments available",
        "symbols": rows,
        "count": len(rows),
        "account": {"login": 0, "server": ""},
        "server_time": int(time.time()),
    }


# ---------------------------------------------------------------------------
# DSE sessions
# ---------------------------------------------------------------------------

def session_info(ts: Optional[float] = None) -> Dict[str, Any]:
    t = float(ts if ts is not None else time.time())
    ls = int(t) + BDT_OFFSET
    day = int(math.floor(ls / 86400.0))
    tod = ls - day * 86400.0
    weekend = _is_weekend(day)

    def next_of(hour: int, minute: int, span_days: int = 0) -> int:
        target = hour * 3600 + minute * 60
        for d in range(span_days, span_days + 7):
            day2 = day + d
            if not _is_weekend(day2):
                cand = (day2 + 1) * 86400 - BDT_OFFSET + target
                if cand > t:
                    return int(cand)
        return int(t + 86400)

    if weekend:
        phase = "WEEKEND"
        status = "CLOSED"
        reason = "WEEKEND"
        message = "DSE closed - Friday & Saturday are the weekend"
        nxt = next_of(9, 30)
    elif tod < PRE_OPEN_START:
        phase = "AFTER_HOURS"
        status = "CLOSED"
        reason = "AFTER_HOURS"
        message = "DSE closed - pre-open starts 09:30"
        nxt = int(day * 86400 - BDT_OFFSET) + PRE_OPEN_START
        if nxt <= t:
            nxt = next_of(9, 30, 1)
    elif tod < OPEN_START:
        phase = "PRE_OPEN"
        status = "PRE-OPEN"
        reason = "PRE_OPEN"
        message = "DSE pre-open auction - continuous trading starts 10:00"
        nxt = int(day * 86400 - BDT_OFFSET) + OPEN_START
    elif tod < OPEN_END:
        phase = "OPEN"
        status = "OPEN"
        reason = "TRADING_HOURS"
        message = "DSE continuous trading - Sun to Thu 10:00-14:00"
        nxt = int(day * 86400 - BDT_OFFSET) + OPEN_END
    elif tod < POST_END:
        phase = "POST_CLOSE"
        status = "POST"
        reason = "POST_CLOSE"
        message = "DSE in post-close auction (14:00-14:30)"
        nxt = int(day * 86400 - BDT_OFFSET) + POST_END
    else:
        phase = "AFTER_HOURS"
        status = "CLOSED"
        reason = "AFTER_HOURS"
        message = "DSE closed for the day - pre-open starts 09:30 tomorrow"
        nxt = next_of(9, 30, 1)

    return {
        "phase": phase,
        "status": status,
        "reason": reason,
        "message": message,
        "open": status in ("PRE-OPEN", "OPEN", "POST"),
        "bd_epoch": ls,
        "weekend": weekend,
        "next_ts": nxt,
        "windows": {
            "pre_open": {"start": PRE_OPEN_START, "end": OPEN_START},
            "open": {"start": OPEN_START, "end": OPEN_END},
            "post": {"start": OPEN_END, "end": POST_END},
        },
    }


# ---------------------------------------------------------------------------
# Watch rows (same field names as market_watch rows)
# ---------------------------------------------------------------------------

def _pressure(symbol: str) -> Dict[str, Any]:
    rows = bars(symbol, "1m", 12)
    up = sum(1 for r in rows if r[4] >= r[1])
    dn = sum(1 for r in rows if r[4] < r[1])
    hh = max(r[2] for r in rows)
    ll = min(r[3] for r in rows)
    first = rows[0][1]
    last = rows[-1][4]
    net = (last - first) / max(1e-9, hh - ll)
    body_bias = (up - dn) / max(1.0, len(rows))
    buyer = 50.0 + 32.0 * max(-1.0, min(1.0, 0.6 * net + 0.4 * body_bias))
    buyer += (_vnoise(time.time(), 45.0, int(_spec(symbol)["seed"]) ^ 0x0B16B00B)) * 3.0
    buyer = round(min(93.0, max(7.0, buyer)), 1)
    seller = round(100.0 - buyer, 1)
    delta = buyer - seller
    dominant = "BUYER" if delta >= 10.0 else ("SELLER" if delta <= -10.0 else "NEUTRAL")
    strength = "STRONG" if abs(delta) >= 25.0 else ("MODERATE" if abs(delta) >= 10.0 else "BALANCED")
    needle_deg = round(((seller - buyer) / 100.0) * 65.0, 1)
    return {
        "buyer_pct": buyer,
        "seller_pct": seller,
        "dominant": dominant,
        "strength": strength,
        "needle_deg": needle_deg,
        "candle_flow": "BULLISH" if delta > 0 else "BEARISH",
        "direction": "UP" if delta > 3.0 else ("DOWN" if delta < -3.0 else "FLAT"),
    }


def watches(only: Optional[List[str]] = None, extra: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    picks: List[str] = []
    for s in (only or BD_SYMBOLS):
        s = str(s).strip().upper()
        if is_bd_symbol(s) and s not in picks:
            picks.append(_spec(s)["symbol"])
    for s in (extra or []):
        s = str(s).strip().upper()
        if is_bd_symbol(s):
            s = _spec(s)["symbol"]
            if s not in picks:
                picks.append(s)
    if not picks:
        picks = list(BD_SYMBOLS)
    sess = session_info()
    rows: List[Dict[str, Any]] = []
    for sym in picks:
        spec = _spec(sym)
        digits = int(spec["digits"])
        tk = tick(sym) or {}
        bid = float(tk.get("bid") or 0)
        ask = float(tk.get("ask") or 0)
        spread = _round(max(0.0, ask - bid), digits)
        pr = _pressure(sym)
        try:
            open_d = bars(sym, "1d", 1)[0][1]
            change_pct = ((bid - open_d) / open_d * 100.0) if open_d else 0.0
        except Exception:
            change_pct = 0.0
        rows.append({
            "symbol": spec["symbol"],
            "displayName": spec["label"] + " · BD",
            "ask": ask,
            "bid": bid,
            "spread": spread,
            "digits": digits,
            "buyer_pct": pr["buyer_pct"],
            "seller_pct": pr["seller_pct"],
            "dominant": pr["dominant"],
            "strength": pr["strength"],
            "needle_deg": pr["needle_deg"],
            "candle_flow": pr["candle_flow"],
            "direction": pr["direction"],
            "change_pct": round(change_pct, 2),
            "details": "DSE SIM · Buyer: %s%% · Seller: %s%%" % (pr["buyer_pct"], pr["seller_pct"]),
            "updated_at": time.time(),
            "live": True,
            "market_open": sess["open"],
            "market_status": sess["status"],
            "market_reason": sess["reason"],
            "market_message": sess["message"],
        })
    return rows


def watch_payload() -> Dict[str, Any]:
    now = time.time()
    hit = _WATCH_CACHE.get("w")
    if hit and now - hit[0] < 0.9:
        return hit[1]
    rows = watches()
    statuses = {}
    for w in rows:
        if w.get("symbol"):
            statuses[w["symbol"]] = {
                "open": w.get("market_open", False),
                "status": w.get("market_status", "CLOSED"),
                "reason": w.get("market_reason", ""),
                "message": w.get("market_message", ""),
            }
    payload = {
        "ok": True,
        "updated_at": now,
        "interval_sec": 1.0,
        "live_stream": True,
        "count": len(rows),
        "watches": rows,
        "market_statuses": statuses,
    }
    _WATCH_CACHE["w"] = (now, payload)
    return payload


_STATE_PAYLOAD_CACHE: Dict[Optional[str], Tuple[float, Dict[str, Any]]] = {}
_WATCH_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


# ---------------------------------------------------------------------------
# BD pipeline stages: hero / AI board / outlook / setups / sessions / event
# ---------------------------------------------------------------------------

def _grade(score: float) -> str:
    if score >= 22:
        return "STRONG BUY"
    if score >= 7:
        return "BUY"
    if score > -7:
        return "NEUTRAL"
    if score > -22:
        return "SELL"
    return "STRONG SELL"


def _tf_grade(symbol: str, tf: str) -> Dict[str, Any]:
    rows = bars(symbol, tf, 40)
    closes = [r[4] for r in rows]
    n = len(closes)
    first = closes[0]
    last = closes[-1]
    trend = (last - first) / max(1e-9, first) * 100.0
    ups = sum(1 for r in rows if r[4] >= r[1])
    hi = max(r[2] for r in rows)
    lo = min(r[3] for r in rows)
    pos = (last - lo) / max(1e-9, hi - lo)
    score = trend * 6.0 + (ups / float(n) - 0.5) * 40.0 + (pos - 0.5) * 30.0
    if n >= 25:
        sma7 = sum(closes[-7:]) / 7.0
        sma25 = sum(closes[-25:]) / 25.0
        score += 10.0 if sma7 > sma25 else -10.0
    return {
        "tf": tf,
        "grade": _grade(score),
        "score": round(score, 1),
        "note": "trend %+.2f%% · %d bull / %d bear bars" % (trend, ups, n - ups),
    }


def _atr(symbol: str, tf: str, n: int = 14) -> float:
    rows = bars(symbol, tf, n + 2)
    span = [max(1e-9, r[2] - r[3]) for r in rows[-n:]]
    return sum(span) / len(span)


_GRADE_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}


def _tf_grade_cached(symbol: str, tf: str, ttl: float = 10.0) -> Dict[str, Any]:
    key = (symbol, tf)
    now = time.time()
    hit = _GRADE_CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    g = _tf_grade(symbol, tf)
    if len(_GRADE_CACHE) > 400:
        _GRADE_CACHE.clear()
    _GRADE_CACHE[key] = (now, g)
    return g


def _ai_board(symbol: str) -> Dict[str, Any]:
    specs = ["1m", "5m", "15m", "1h", "4h", "1d"]
    grades = [_tf_grade_cached(symbol, tf) for tf in specs]
    by_tf = {g["tf"]: g["score"] for g in grades}
    core = (by_tf.get("5m", 0.0) + by_tf.get("15m", 0.0) + by_tf.get("1h", 0.0)) / 3.0
    higher = (by_tf.get("4h", 0.0) + by_tf.get("1d", 0.0)) / 2.0
    score = core * 0.6 + higher * 0.4
    try:
        buyer = _pressure(symbol)["buyer_pct"]
        score += (buyer - 50.0) * 0.35
    except Exception:
        pass
    rows = bars(symbol, "15m", 20)
    last = float(rows[-1][4])
    atr = _atr(symbol, "15m", 14)
    side = "BUY" if score >= 7 else ("SELL" if score <= -7 else "NEUTRAL")
    targets: Dict[str, float] = {}
    if side == "BUY":
        targets = {"t1": _round(last + atr * 1.6, 2), "t2": _round(last + atr * 3.0, 2),
                   "stop": _round(last - atr * 1.5, 2)}
    else:
        targets = {"t1": _round(last - atr * 1.6, 2), "t2": _round(last - atr * 3.0, 2),
                   "stop": _round(last + atr * 1.5, 2)}
    return {
        "tf_grades": grades,
        "verdict": side,
        "score": round(score, 1),
        "confidence": int(round(50.0 + min(48.0, abs(score)))),
        "targets": targets,
    }


def _outlook() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for sym in BD_SYMBOLS:
        spec = _spec(sym)
        g = _tf_grade_cached(sym, "15m")
        try:
            open_d = bars(sym, "1d", 1)[0][1]
            tk = tick(sym) or {}
            price = float(tk.get("bid") or 0)
            change_pct = ((price - open_d) / open_d * 100.0) if price and open_d else 0.0
        except Exception:
            price, change_pct = 0.0, 0.0
        if g["score"] >= 6:
            direction = "UP"
        elif g["score"] <= -6:
            direction = "DOWN"
        else:
            direction = "FLAT"
        rows.append({
            "symbol": spec["symbol"],
            "label": spec["label"],
            "icon": spec["icon"],
            "direction": direction,
            "grade": g["grade"],
            "score": g["score"],
            "note": g["note"],
            "change_pct": round(change_pct, 2),
        })
    return rows


def _setups(symbol: str) -> List[Dict[str, Any]]:
    rows1h = bars(symbol, "1h", 48)
    spec = _spec(symbol)
    digits = int(spec["digits"])
    lows = [r[3] for r in rows1h[-24:]]
    highs = [r[2] for r in rows1h[-24:]]
    closes = [r[4] for r in rows1h]
    swing_low = min(lows)
    swing_high = max(highs)
    mean_px = sum(closes[-12:]) / 12.0
    atr = _atr(symbol, "1h", 14)
    last = float(closes[-1])

    def row(side: str, title: str, entry: float, sl: float, tp: float, note: str) -> Dict[str, Any]:
        rr = abs(tp - entry) / max(1e-9, abs(entry - sl))
        dist = abs(entry - last)
        return {
            "id": "%s-%s" % (side.lower(), title.split(" ")[1].lower()),
            "side": side,
            "title": title,
            "entry": _round(entry, digits),
            "sl": _round(sl, digits),
            "tp": _round(tp, digits),
            "rr": round(rr, 2),
            "tf": "1H",
            "distance_pt": round(dist, digits),
            "note": note,
        }

    out = [
        row("BUY", "BUY SET 1 · swing retest", swing_low + atr * 0.2, swing_low - atr * 0.6,
            swing_low + atr * 2.6, "24h swing low holds, momentum flips"),
        row("SELL", "SELL SET 1 · supply fade", swing_high - atr * 0.2, swing_high + atr * 0.6,
            swing_high - atr * 2.6, "24h swing high rejects price"),
        row("BUY", "BUY SET 2 · mean reversion", mean_px - atr * 0.5, mean_px - atr * 1.6,
            mean_px + atr * 1.7, "price back to the 12h mean"),
        row("SELL", "SELL SET 2 · stop-run fade", swing_high + atr * 0.35, swing_high + atr * 0.95,
            swing_high - atr * 1.2, "stop run above highs gets sold"),
        row("BUY", "BUY SET 3 · breakout", swing_high + atr * 0.15, swing_high - atr * 0.5,
            swing_high + atr * 2.2, "clean breakout above 24h high"),
    ]
    out.sort(key=lambda r: r["distance_pt"])
    return out


def _session_cards(sess: Dict[str, Any]) -> List[Dict[str, Any]]:
    base = int(sess["bd_epoch"]) - int(sess["bd_epoch"]) % 86400
    w = sess["windows"]
    cards: List[Dict[str, Any]] = []
    for name, key, phase_label in (
        ("PRE-OPEN AUCTION", "pre_open", "PRE_OPEN"),
        ("CONTINUOUS TRADING", "open", "OPEN"),
        ("POST-CLOSE AUCTION", "post", "POST_CLOSE"),
    ):
        c = w[key]
        cards.append({
            "name": name,
            "range": "%02d:%02d - %02d:%02d (BD)" % (c["start"] // 3600, (c["start"] % 3600) // 60,
                                                     c["end"] // 3600, (c["end"] % 3600) // 60),
            "active": sess["phase"] == phase_label,
            "start_ts": int(base - BDT_OFFSET + c["start"]),
            "end_ts": int(base - BDT_OFFSET + c["end"]),
        })
    cards.append({
        "name": "NEXT SESSION",
        "range": "next DSE pre-open (BD)",
        "active": False,
        "start_ts": int(sess["next_ts"]),
        "end_ts": 0,
    })
    return cards


def _next_event(symbol: str, sess: Dict[str, Any]) -> Dict[str, Any]:
    target = int(sess["next_ts"])
    ls = target + BDT_OFFSET
    tod = ls - int(math.floor(ls / 86400.0)) * 86400
    if abs(tod - OPEN_START) < 61:
        title, impact = "DSE CONTINUOUS MARKET OPEN", "HIGH"
    elif abs(tod - OPEN_END) < 61:
        title, impact = "DSE MARKET CLOSE", "HIGH"
    elif abs(tod - PRE_OPEN_START) < 61:
        title, impact = "DSE PRE-OPEN AUCTION", "MEDIUM"
    else:
        title, impact = "DSE POST-CLOSE AUCTION END", "LOW"
    rows: List[Dict[str, Any]] = []
    for sym in ("DSEX", "GP", "BATBC", "SQURPHARMA", "BRACBANK"):
        g = _tf_grade_cached(sym, "15m")
        rows.append({
            "symbol": sym,
            "label": _spec(sym)["label"],
            "direction": "BUY" if g["score"] >= 6 else ("SELL" if g["score"] <= -6 else "WAIT"),
        })
    return {
        "title": title,
        "impact": impact,
        "ts": target,
        "countdown_sec": max(0, target - int(time.time())),
        "status": "WAITING" if target > time.time() else "LIVE",
        "impacted": rows,
        "plan": ("Flow projects a %s bias into the DSE %s: index pairs first, then the big scrips."
                 % ("BUYER", title.lower() if "open" in title.lower() else "close")),
    }


def state(symbol: str = "DSEX") -> Dict[str, Any]:
    if not is_bd_symbol(symbol):
        symbol = "DSEX"
    key = _spec(symbol)["symbol"]
    now = time.time()
    hit = _STATE_PAYLOAD_CACHE.get(key)
    if hit and now - hit[0] < 4.0:
        return hit[1]
    spec = _spec(symbol)
    sess = session_info()
    tk = tick(symbol) or {}
    digits = int(spec["digits"])
    try:
        open_d = bars(symbol, "1d", 1)[0][1]
    except Exception:
        open_d = spec["base"]
    price = float(tk.get("bid") or open_d)
    day_rows = bars(symbol, "1m", 375)
    high = max(r[2] for r in day_rows) if day_rows else price
    low = min(r[3] for r in day_rows) if day_rows else price
    ai = _ai_board(symbol)
    curve_rows = bars(symbol, "1m", 240)
    payload = {
        "ok": True,
        "symbol": spec["symbol"],
        "updated_at": time.time(),
        "hero": {
            "symbol": spec["symbol"],
            "label": spec["label"],
            "pair": spec["pair"],
            "icon": spec["icon"],
            "digits": digits,
            "price": _round(price, digits),
            "bid": _round(float(tk.get("bid") or price), digits),
            "ask": _round(float(tk.get("ask") or price), digits),
            "spread": _round(max(0.0, float(tk.get("ask") or 0) - float(tk.get("bid") or 0)), digits),
            "change_pct": round((price - open_d) / max(1e-9, open_d) * 100.0, 2),
            "change_abs": _round(price - open_d, digits),
            "high": _round(high, digits),
            "low": _round(low, digits),
            "open24": _round(open_d, digits),
            "market_status": sess["status"],
            "market_message": sess["message"],
            "buy_pct": _pressure(symbol)["buyer_pct"],
        },
        "ai": ai,
        "outlook": _outlook(),
        "setups": _setups(symbol),
        "sessions": {
            "phase": sess["phase"],
            "status": sess["status"],
            "message": sess["message"],
            "bd_epoch": int(sess["bd_epoch"]),
            "next_ts": int(sess["next_ts"]),
            "cards": _session_cards(sess),
            "summary": "DSE Sun-Thu · %s" % sess["message"],
        },
        "event": _next_event(symbol, sess),
        "curve": [[int(r[0]), float(r[4])] for r in curve_rows],
    }
    _STATE_PAYLOAD_CACHE[key] = (now, payload)
    return payload
