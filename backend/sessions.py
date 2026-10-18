"""Trading sessions: Sydney (Australian), Tokyo (Asian), London (European),
New York (North American) - the live clock plus the AI board that goes with it.

Two halves, both importable without MetaTrader5:

1. The clock. Every session is defined as *local* business hours (London
   08:00-17:00 Europe/London, Tokyo 09:00-18:00 Asia/Tokyo, ...), so the UTC
   window follows daylight saving on its own - exactly like a trader's session
   table. Sessions run Monday to Friday local, which is also precisely the FX
   week (New York closes Friday 17:00 local = 21:00/22:00 UTC, Sydney reopens
   Monday 08:00 local = Sunday 21:00/22:00 UTC). Each row carries absolute open
   and close epochs, so the dashboard runs its own 1 Hz countdown between
   pushes.

2. The AI reading, which is measured instead of guessed. The last ~N days of
   M15 bars of the selected market are clipped to each session window and the
   average upward excursion (high - open), downward excursion (open - low),
   total range and net drift are averaged per session. From that every session
   gets a BUY / SELL verdict, the dollars it usually travels up and down, and
   how many of the sampled days closed in that direction.

Run the tests:  python -m unittest tests.test_sessions
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:  # Python 3.9+; on Windows this needs the `tzdata` package
    from zoneinfo import ZoneInfo  # type: ignore
except Exception:  # pragma: no cover - only on very old interpreters
    ZoneInfo = None  # type: ignore

UTC = timezone.utc

# Standard (winter) UTC offsets, used only when the IANA database is missing -
# the clock still works, it just cannot follow daylight saving.
_FALLBACK_OFFSET = {
    "Australia/Sydney": 10.0,
    "Asia/Tokyo": 9.0,
    "Europe/London": 0.0,
    "America/New_York": -5.0,
}

# Ordered by the UTC open of the session, so the dashboard reads left to right
# like the trading day: Sydney -> Tokyo -> London -> New York.
SESSIONS: List[Dict[str, Any]] = [
    {"key": "sydney", "name_en": "Sydney", "region_en": "Australian",
     "tz": "Australia/Sydney", "open_hour": 8, "open_min": 0, "hours": 9},
    {"key": "tokyo", "name_en": "Tokyo", "region_en": "Asian",
     "tz": "Asia/Tokyo", "open_hour": 9, "open_min": 0, "hours": 9},
    {"key": "london", "name_en": "London", "region_en": "European",
     "tz": "Europe/London", "open_hour": 8, "open_min": 0, "hours": 9},
    {"key": "newyork", "name_en": "New York", "region_en": "North American",
     "tz": "America/New_York", "open_hour": 8, "open_min": 0, "hours": 9},
]

# The heaviest liquidity of the day: London and New York open together.
PEAK_KEYS = ("london", "newyork")

# M15 bars per session window (9h * 4). A window with fewer bars than
# _MIN_BARS is a holiday / half day and is left out of the averages.
_BARS_PER_WINDOW = 36
_MIN_BARS = 24
_MIN_DAYS = 4            # below this many sampled days a session has no verdict
_DRIFT_TOL = 0.15        # net drift must beat 15% of the range to call a side

_ZONE_CACHE: Dict[str, Any] = {}


def _zone(name: str) -> Any:
    """tzinfo for an IANA name, with a fixed-offset fallback."""
    if name in _ZONE_CACHE:
        return _ZONE_CACHE[name]
    zone: Any = None
    if ZoneInfo is not None:
        try:
            zone = ZoneInfo(name)
        except Exception:
            zone = None
    if zone is None:
        zone = timezone(timedelta(hours=_FALLBACK_OFFSET.get(name, 0.0)))
    _ZONE_CACHE[name] = zone
    return zone


def tz_available() -> bool:
    """True when real timezone data is loaded (daylight saving is honoured)."""
    if ZoneInfo is None:
        return False
    try:
        return getattr(_zone("Europe/London"), "key", "") == "Europe/London"
    except Exception:
        return False


def _windows_around(sess: Dict[str, Any], ts: float) -> List[Tuple[datetime, datetime, bool]]:
    """Every Monday-Friday local window of one session around ``ts``.

    Returns (start, end, complete) triples sorted by start time. ``complete``
    is False while the window is still running.
    """
    zone = _zone(str(sess["tz"]))
    local_day = datetime.fromtimestamp(ts, zone).date()
    out: List[Tuple[datetime, datetime, bool]] = []
    for delta in (-1, 0, 1, 2):
        day = local_day + timedelta(days=delta)
        if day.weekday() >= 5:      # Saturday / Sunday local - market closed
            continue
        start = datetime(day.year, day.month, day.day,
                         int(sess["open_hour"]), int(sess["open_min"]), tzinfo=zone)
        end = start + timedelta(hours=float(sess["hours"]))
        out.append((start, end, end.timestamp() <= ts))
    out.sort(key=lambda w: w[0].timestamp())
    return out


def window_of(sess: Dict[str, Any], ts: float) -> Optional[float]:
    """Epoch of the window start that contains ``ts`` (None when closed)."""
    for start, end, _done in _windows_around(sess, ts):
        if start.timestamp() <= ts < end.timestamp():
            return start.timestamp()
    return None


def _clock(now: float) -> Dict[str, Any]:
    """Working fields of every session at ``now``: state, epochs, local times."""
    out: Dict[str, Any] = {}
    for sess in SESSIONS:
        zone = _zone(str(sess["tz"]))
        live: Optional[Tuple[datetime, datetime]] = None
        nxt: Optional[Tuple[datetime, datetime]] = None
        for start, end, _done in _windows_around(sess, now):
            if start.timestamp() <= now < end.timestamp():
                live = (start, end)
            elif start.timestamp() > now:
                if nxt is None or start.timestamp() < nxt[0].timestamp():
                    nxt = (start, end)
        if live is not None:
            start, end = live
            state, opens_in, closes_in = "open", 0.0, end.timestamp() - now
            open_ts, close_ts = start.timestamp(), end.timestamp()
            progress = max(0.0, min(1.0, (now - start.timestamp()) / max(1.0, end.timestamp() - start.timestamp())))
        elif nxt is not None:
            start, end = nxt
            state, opens_in, closes_in = "closed", start.timestamp() - now, 0.0
            open_ts, close_ts = start.timestamp(), end.timestamp()
            progress = 0.0
        else:  # nothing in the next 48h - only possible without tz data
            state, opens_in, closes_in, open_ts, close_ts, progress = "closed", 0.0, 0.0, 0.0, 0.0, 0.0
        out[sess["key"]] = {
            "key": sess["key"],
            "name_en": sess["name_en"],
            "region_en": sess["region_en"],
            "tz": sess["tz"],
            "local_day": datetime.fromtimestamp(open_ts, zone).strftime("%a") if open_ts else "",
            "open_local": datetime.fromtimestamp(open_ts, zone).strftime("%H:%M") if open_ts else "—",
            "close_local": datetime.fromtimestamp(close_ts, zone).strftime("%H:%M") if close_ts else "—",
            "tz_offset": datetime.fromtimestamp(open_ts, zone).strftime("%z")[:3] if open_ts else "",
            "open_utc": datetime.fromtimestamp(open_ts, UTC).strftime("%H:%M") if open_ts else "—",
            "close_utc": datetime.fromtimestamp(close_ts, UTC).strftime("%H:%M") if close_ts else "—",
            "state": state,
            "open_ts": int(open_ts),
            "close_ts": int(close_ts),
            "opens_in_sec": int(max(0.0, opens_in)),
            "closes_in_sec": int(max(0.0, closes_in)),
            "progress": round(progress, 4),
            # more than a day away always means the FX week itself is closed
            "weekend": bool(state == "closed" and opens_in > 24 * 3600),
        }
    return out


def session_clock(now: float) -> List[Dict[str, Any]]:
    """The four sessions with live state, start/end times and countdowns."""
    rows = [dict(row) for row in _clock(now).values()]
    open_keys = [r["key"] for r in rows if r["state"] == "open"]
    peak = all(k in open_keys for k in PEAK_KEYS)
    for row in rows:
        row["also_open"] = [k for k in open_keys if k != row["key"]]
        row["peak"] = bool(peak and row["key"] in PEAK_KEYS)
    # declaration order = the standard session table (Sydney -> Tokyo ->
    # London -> New York), not the order the UTC opens happen to sort into
    order = {s["key"]: i for i, s in enumerate(SESSIONS)}
    rows.sort(key=lambda r: order.get(r["key"], 99))
    return rows


def collect(bars: Iterable[Any], now: float) -> Dict[str, Dict[float, Dict[str, Any]]]:
    """Clip bars into session windows.

    ``bars`` may be MT5 rate rows (subscriptable) or (ts, o, h, l, c) tuples,
    oldest first. Returns {session key: {window start epoch: window record}}.
    """
    buckets: Dict[str, Dict[float, Dict[str, Any]]] = {}
    for bar in bars:
        try:
            if isinstance(bar, dict):
                ts, o, h, l, c = (float(bar["time"]), float(bar["open"]), float(bar["high"]),
                                  float(bar["low"]), float(bar["close"]))
            elif isinstance(bar, (tuple, list)):
                ts, o, h, l, c = (float(bar[0]), float(bar[1]), float(bar[2]),
                                  float(bar[3]), float(bar[4]))
            else:
                ts, o, h, l, c = (float(bar["time"]), float(bar["open"]), float(bar["high"]),
                                  float(bar["low"]), float(bar["close"]))
        except Exception:
            continue
        if ts >= now:
            continue
        for sess in SESSIONS:
            start_ts = window_of(sess, ts)
            if start_ts is None:
                continue
            day = buckets.setdefault(sess["key"], {})
            rec = day.get(start_ts)
            if rec is None:
                day[start_ts] = {"start": start_ts, "open_ts": ts, "o": o, "h": h, "l": l, "c": c,
                                 "close_ts": ts, "n": 1,
                                 "complete": start_ts + float(sess["hours"]) * 3600.0 <= now}
                continue
            rec["n"] += 1
            rec["h"] = max(rec["h"], h)
            rec["l"] = min(rec["l"], l)
            if ts < rec["open_ts"]:
                rec["open_ts"], rec["o"] = ts, o
            if ts > rec["close_ts"]:
                rec["close_ts"], rec["c"] = ts, c
    return buckets


def session_stats(buckets: Dict[str, Dict[float, Dict[str, Any]]],
                  min_days: int = _MIN_DAYS) -> Dict[str, Optional[Dict[str, Any]]]:
    """Average up / down travel, range and drift per session, in price units."""
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for sess in SESSIONS:
        rows = [w for w in (buckets.get(sess["key"]) or {}).values()
                if w.get("complete") and int(w.get("n") or 0) >= _MIN_BARS]
        n = len(rows)
        if n < min_days:
            out[sess["key"]] = None
            continue
        ups = [w["h"] - w["o"] for w in rows]
        downs = [w["o"] - w["l"] for w in rows]
        ranges = [w["h"] - w["l"] for w in rows]
        drifts = [w["c"] - w["o"] for w in rows]
        up, down, span, drift = mean(ups), mean(downs), mean(ranges), mean(drifts)
        up_days = sum(1 for d in drifts if d > 0)
        tol = span * _DRIFT_TOL
        if drift > tol:
            verdict = "BUY"
        elif drift < -tol:
            verdict = "SELL"
        else:
            verdict = "MIXED"
        conf = 0 if verdict == "MIXED" else int(min(100, round(100.0 * abs(drift) / max(span / 2.0, 1e-9))))
        out[sess["key"]] = {
            "days": n,
            "up": up, "down": down, "range": span, "drift": drift,
            "best_up": max(ups), "best_down": max(downs),
            "up_days": up_days, "down_days": n - up_days,
            "direction_pct": round(100.0 * up_days / n, 1),
            "verdict": verdict, "confidence": conf,
        }
    return out


def _fmt(value: float, digits: int) -> str:
    return f"{value:,.{digits}f}"


def merge(clock_rows: List[Dict[str, Any]], stats: Optional[Dict[str, Any]],
          digits: int = 2, money_per_price: float = 0.0, lot: float = 0.01) -> List[Dict[str, Any]]:
    """Attach the AI reading to each session row (money in the account currency)."""
    stats = stats or {}
    for row in clock_rows:
        st = stats.get(row["key"]) if isinstance(stats, dict) else None
        row["stats"] = None
        if not st:
            continue
        mpp = float(money_per_price or 0.0)
        up_m = round(float(st["up"]) * mpp, 2)
        down_m = round(float(st["down"]) * mpp, 2)
        range_m = round(float(st["range"]) * mpp, 2)
        drift_m = round(abs(float(st["drift"])) * mpp, 2)
        row["stats"] = {
            "days": int(st["days"]),
            "lot": float(lot),
            "up_move": round(float(st["up"]), digits),
            "down_move": round(float(st["down"]), digits),
            "range_move": round(float(st["range"]), digits),
            "drift_move": round(float(st["drift"]), digits),
            "best_up_move": round(float(st["best_up"]), digits),
            "best_down_move": round(float(st["best_down"]), digits),
            "up_txt": _fmt(float(st["up"]), digits),
            "down_txt": _fmt(float(st["down"]), digits),
            "range_txt": _fmt(float(st["range"]), digits),
            "drift_txt": _fmt(abs(float(st["drift"])), digits),
            "up_money": up_m,
            "down_money": down_m,
            "buy_money": up_m,
            "sell_money": down_m,
            "range_money": range_m,
            "drift_money": drift_m,
            "drift_side": "up" if float(st["drift"]) > 0 else "down",
            "up_days": int(st["up_days"]),
            "down_days": int(st["down_days"]),
            "direction_pct": float(st["direction_pct"]),
            "verdict": str(st["verdict"]),
            "confidence": int(st["confidence"]),
        }
    return clock_rows


def summary(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The session this market travels the most in - the one-line AI answer."""
    best = None
    for row in rows:
        st = row.get("stats")
        if not st:
            continue
        if best is None or float(st["range_money"]) > float(best["stats"]["range_money"]):
            best = row
    if best is None:
        return None
    st = best["stats"]
    return {
        "key": best["key"],
        "name_en": best["name_en"],
        "region_en": best["region_en"],
        "verdict": st["verdict"],
        "up_money": st["up_money"],
        "down_money": st["down_money"],
        "buy_money": st.get("buy_money", st["up_money"]),
        "sell_money": st.get("sell_money", st["down_money"]),
        "range_money": st["range_money"],
        "up_move": st["up_move"],
        "down_move": st["down_move"],
        "drift_move": st["drift_move"],
        "drift_side": st["drift_side"],
        "confidence": st["confidence"],
        "direction_pct": st["direction_pct"],
        "days": st["days"],
    }


def build_payload(now: float, symbol: str = "", digits: int = 2, money_per_price: float = 0.0,
                  lot: float = 0.01, stats: Optional[Dict[str, Any]] = None,
                  markets: Optional[List[str]] = None, range_days: int = 20,
                  source: str = "clock",
                  all_markets_stats: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Everything the sessions panel needs, in one JSON-safe dict."""
    rows = merge(session_clock(now), stats, digits=digits,
                 money_per_price=money_per_price, lot=lot)
    if all_markets_stats and isinstance(all_markets_stats, dict):
        for row in rows:
            sess_k = row["key"]
            m_list = []
            for sym, s_data in all_markets_stats.items():
                if isinstance(s_data, dict) and sess_k in s_data and s_data[sess_k]:
                    m_item = s_data[sess_k]
                    m_list.append({
                        "symbol": sym,
                        "verdict": str(m_item.get("verdict", "MIXED")),
                        "buy_money": float(m_item.get("buy_money", m_item.get("up_money", 0.0))),
                        "sell_money": float(m_item.get("sell_money", m_item.get("down_money", 0.0))),
                        "range_money": float(m_item.get("range_money", 0.0)),
                        "confidence": int(m_item.get("confidence", 0)),
                        "direction_pct": float(m_item.get("direction_pct", 50.0)),
                    })
            row["all_markets"] = m_list
            buy_n = sum(1 for m in m_list if m["verdict"] == "BUY")
            sell_n = sum(1 for m in m_list if m["verdict"] == "SELL")
            mixed_n = sum(1 for m in m_list if m["verdict"] == "MIXED")
            row["market_summary"] = {
                "buy_count": buy_n,
                "sell_count": sell_n,
                "mixed_count": mixed_n,
                "total": len(m_list),
                "dominant": "BUY" if buy_n > sell_n else ("SELL" if sell_n > buy_n else "MIXED")
            }
    return {
        "symbol": symbol,
        "symbols": list(markets or []),
        "digits": int(digits),
        "lot": float(lot),
        "range_days": int(range_days),
        "money_per_price": round(float(money_per_price or 0.0), 6),
        "source": source,
        "tz_data": tz_available(),
        "now_utc": int(now),
        "updated": int(now),
        "summary": summary(rows),
        "sessions": rows,
        "all_markets_stats": all_markets_stats or {},
    }
