"""Public market-data fallback for the chart terminal.

The Exness chart normally streams candles through the MetaTrader 5 Python API,
which can only read prices from a logged-in MT5 terminal running on the same
PC. When that terminal is closed the dashboard would otherwise answer
``No candles are available for XAUUSDm in Exness MT5`` and the chart stays
empty.

This module keeps the chart alive with real, keyless public data instead:

* **Binance spot** - crypto klines / 24h stats, and ``PAXGUSDT`` as the gold
  proxy (PAXG is tokenised spot gold, so it tracks XAU/USD closely).
* **Yahoo Finance chart API** - metals, forex, indices, energy, stocks.

Nothing here invents a price: every bar and tick is read from one of those
APIs, and a network failure raises ``PublicFeedError`` (the caller turns that
into the chart's normal "unavailable" message). Results are cached per
timeframe so the 10 fps sampler never floods the providers.
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

TF_SEC: Dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

_HTTP_TIMEOUT = 8.0
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Exness instrument -> Yahoo Finance symbol (the Binance proxy is used first
# for gold and crypto). Everything else is derived from the catalogue kind.
_YAHOO_METALS = {
    "XAUUSDm": "GC=F",
    "XAGUSDm": "SI=F",
    "XPTUSDm": "PL=F",
    "XPDUSDm": "PA=F",
}
_YAHOO_INDEX = {
    "USTECm": "^NDX",
    "US500m": "^GSPC",
    "US30m": "^DJI",
    "DE30m": "^GDAXI",
    "UK100m": "^FTSE",
    "FR40m": "^FCHI",
    "JP225m": "^N225",
    "AUS200m": "^AXJO",
    "HK50m": "^HSI",
    "STOXX50m": "^STOXX50E",
    "US2000m": "^RUT",
    "ChinaHm": "^HSCE",
}
_YAHOO_ENERGY = {
    "USOILm": "CL=F",
    "UKOILm": "BZ=F",
    "XNGUSDm": "NG=F",
}
# Exness tickers whose Yahoo name is not simply the symbol without its "m".
_YAHOO_STOCK_ALIAS = {"NEFLm": "NFLX"}

# Binance's kline page is limited to 1000 rows.
_BINANCE_PAGE = 1000

# How long a fetched series is trusted. Intraday charts refresh quickly; daily
# bars do not need another network round-trip every few seconds.
_TTL = {"1m": 8.0, "5m": 20.0, "15m": 45.0, "1h": 120.0, "4h": 240.0, "1d": 600.0}

_CANDLES: Dict[Tuple[str, str, int], Tuple[float, List[List[float]]]] = {}
_TICKS: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_TICKERS: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_VOLUME: Dict[str, Tuple[float, Tuple[int, int]]] = {}


class PublicFeedError(RuntimeError):
    """Raised when no public provider can serve a symbol right now."""


@lru_cache(maxsize=None)
def _spec(symbol: str) -> Optional[Dict[str, Any]]:
    # Imported lazily: chart_feed imports this module at load time.
    from .chart_feed import CATALOG

    wanted = str(symbol or "").strip()
    if not wanted:
        return None
    for spec in CATALOG:
        if wanted == spec["symbol"] or wanted in spec.get("alts", []):
            return spec
    return None


def canonical(symbol: str) -> str:
    spec = _spec(symbol)
    return str(spec["symbol"]) if spec else str(symbol or "").strip()


def _binance_symbol(symbol: str) -> Optional[str]:
    spec = _spec(symbol)
    if not spec:
        return None
    canon = str(spec["symbol"])
    if canon == "XAUUSDm":
        return "PAXGUSDT"
    if spec.get("kind") != "crypto":
        return None
    base = canon[:-1] if canon.endswith("m") else canon
    for quote in ("USDT", "USD"):
        if base.endswith(quote):
            base = base[: -len(quote)]
            break
    return base + "USDT"


def _yahoo_symbol(symbol: str) -> Optional[str]:
    spec = _spec(symbol)
    if not spec:
        return None
    canon = str(spec["symbol"])
    if canon in _YAHOO_METALS:
        return _YAHOO_METALS[canon]
    if canon in _YAHOO_INDEX:
        return _YAHOO_INDEX[canon]
    if canon in _YAHOO_ENERGY:
        return _YAHOO_ENERGY[canon]
    base = canon[:-1] if canon.endswith("m") else canon
    kind = spec.get("kind")
    if kind == "forex":
        return base + "=X"
    if kind == "stocks":
        return _YAHOO_STOCK_ALIAS.get(canon, base)
    if kind == "crypto":
        for quote in ("USDT", "USD"):
            if base.endswith(quote):
                base = base[: -len(quote)]
                break
        return base + "-USD"
    return None


def supports(symbol: str) -> bool:
    """True when a public provider can offer real candles for the symbol."""
    return bool(_binance_symbol(symbol) or _yahoo_symbol(symbol))


def source_of(symbol: str) -> str:
    if _binance_symbol(symbol):
        return "binance"
    if _yahoo_symbol(symbol):
        return "yahoo"
    return ""


def _chain(symbol: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    b = _binance_symbol(symbol)
    y = _yahoo_symbol(symbol)
    spec = _spec(symbol)
    # Binance is the fastest source for crypto and for the gold proxy; Yahoo
    # is the only option for everything else.
    if b and (spec is None or spec.get("kind") == "crypto" or spec.get("symbol") == "XAUUSDm"):
        out.append(("binance", b))
    if y:
        out.append(("yahoo", y))
    if b and (not out or out[0][0] != "binance"):
        out.insert(0, ("binance", b))
    return out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "application/json,text/plain,*/*",
    })
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------

def _fetch_binance(symbol: str, tf: str, count: int) -> List[List[float]]:
    rows: List[List[float]] = []
    remaining = int(count)
    end_time: Optional[int] = None
    while remaining > 0:
        limit = min(_BINANCE_PAGE, remaining)
        url = ("https://api.binance.com/api/v3/klines?symbol=%s&interval=%s&limit=%d"
               % (urllib.parse.quote(symbol), urllib.parse.quote(tf), limit))
        if end_time is not None:
            url += "&endTime=%d" % int(end_time - 1)
        page = _http_json(url)
        if not isinstance(page, list) or not page:
            break
        parsed: List[List[float]] = []
        for row in page:
            if not isinstance(row, list) or len(row) < 9:
                continue
            o, h, l, c = (_num(row[1]), _num(row[2]), _num(row[3]), _num(row[4]))
            if c <= 0:
                continue
            parsed.append([int(_num(row[0])), o, h, l, c,
                           int(_num(row[8]))])
        if not parsed:
            break
        rows = parsed + rows
        end_time = int(_num(page[0][0]))
        remaining -= len(page)
        if len(page) < limit:
            break
    return rows[-int(count):] if rows else []


def _yahoo_range(interval: str, tf: str = "") -> str:
    if interval == "1m":
        return "7d"
    if interval in ("5m", "15m"):
        # Yahoo rejects 15m requests longer than 60 days (HTTP 422).
        return "1mo"
    if interval == "60m":
        return "2y" if tf == "4h" else "6mo"
    return "10y"


def _fetch_yahoo(symbol: str, tf: str, count: int) -> List[List[float]]:
    # Yahoo has no native 4h interval; build it from hourly bars.
    interval = "60m" if tf in ("1h", "4h") else ("1d" if tf == "1d" else tf)
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=%s&range=%s&includePrePost=true"
           % (urllib.parse.quote(symbol), interval, _yahoo_range(interval, tf)))
    data = _http_json(url)
    chart = (data or {}).get("chart") or {}
    result = (chart.get("result") or [None])[0]
    if not result:
        err = (chart.get("error") or {}).get("description") or "no result"
        raise PublicFeedError(str(err))
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    bars: List[List[float]] = []
    for i, ts in enumerate(stamps):
        close = _num(closes[i] if i < len(closes) else None)
        if close <= 0:
            continue
        open_ = _num(opens[i] if i < len(opens) else None, close) or close
        high = _num(highs[i] if i < len(highs) else None, close) or close
        low = _num(lows[i] if i < len(lows) else None, close) or close
        vol = int(_num(volumes[i] if i < len(volumes) else None))
        bars.append([int(ts) * 1000, open_, max(high, open_, close),
                     min(low, open_, close), close, max(0, vol)])
    if tf == "4h" and bars:
        bars = _aggregate(bars, 14400)
    return bars[-int(count):] if bars else []


def _aggregate(bars: List[List[float]], span_sec: int) -> List[List[float]]:
    out: List[List[float]] = []
    for bar in bars:
        bucket = int(bar[0]) // (span_sec * 1000) * (span_sec * 1000)
        if out and int(out[-1][0]) == bucket:
            last = out[-1]
            last[2] = max(float(last[2]), float(bar[2]))
            last[3] = min(float(last[3]), float(bar[3]))
            last[4] = float(bar[4])
            last[5] = int(last[5]) + int(bar[5])
        else:
            out.append([bucket, float(bar[1]), float(bar[2]), float(bar[3]),
                        float(bar[4]), int(bar[5])])
    return out


def candles(symbol: str, tf: str = "5m", count: int = 600) -> List[List[float]]:
    """Real OHLCV bars from Binance/Yahoo, cached per timeframe."""
    if tf not in TF_SEC:
        tf = "5m"
    canon = canonical(symbol)
    count = max(1, min(int(count), 2000))
    key = (canon, tf, count)
    now = time.time()
    hit = _CANDLES.get(key)
    if hit and now < hit[0]:
        return hit[1]
    errors: List[str] = []
    for provider, psym in _chain(canon):
        try:
            bars = (_fetch_binance(psym, tf, count) if provider == "binance"
                    else _fetch_yahoo(psym, tf, count))
        except Exception as exc:  # network, geo block, rate limit, bad symbol
            errors.append("%s: %s" % (provider, exc))
            continue
        if bars:
            _CANDLES[key] = (now + _TTL.get(tf, 20.0), bars)
            return bars
        errors.append("%s: empty series" % provider)
    if hit:
        # A provider hiccup must not blank a chart that already has history.
        return hit[1]
    raise PublicFeedError("public feed could not return %s candles (%s)"
                          % (canon, "; ".join(errors) or "no provider"))


# ---------------------------------------------------------------------------
# Live price + 24h stats
# ---------------------------------------------------------------------------

def _binance_price(symbol: str) -> Dict[str, Any]:
    data = _http_json("https://api.binance.com/api/v3/ticker/price?symbol=%s"
                      % urllib.parse.quote(symbol))
    price = _num((data or {}).get("price"))
    if price <= 0:
        raise PublicFeedError("binance returned no price")
    ms = int(time.time() * 1000)
    return {"bid": price, "ask": price, "ms": ms, "time": ms // 1000,
            "volume": 0, "volume_real": 0.0}


def _yahoo_price(symbol: str) -> Dict[str, Any]:
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1m&range=1d"
           % urllib.parse.quote(symbol))
    data = _http_json(url)
    chart = (data or {}).get("chart") or {}
    result = (chart.get("result") or [None])[0]
    if not result:
        err = (chart.get("error") or {}).get("description") or "no result"
        raise PublicFeedError(str(err))
    meta = result.get("meta") or {}
    price = _num(meta.get("regularMarketPrice"))
    if price <= 0:
        closes = (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
        price = _num(closes[-1] if closes else None)
    if price <= 0:
        raise PublicFeedError("yahoo returned no price")
    stamp = int(_num(meta.get("regularMarketTime")) or time.time())
    return {"bid": price, "ask": price, "ms": stamp * 1000, "time": stamp,
            "volume": 0, "volume_real": 0.0}


def tick(symbol: str) -> Dict[str, Any]:
    """Last traded public price as a chart tick (bid == ask; no fake spread)."""
    canon = canonical(symbol)
    now = time.time()
    hit = _TICKS.get(canon)
    if hit and now < hit[0]:
        return hit[1]
    errors: List[str] = []
    for provider, psym in _chain(canon):
        try:
            row = _binance_price(psym) if provider == "binance" else _yahoo_price(psym)
        except Exception as exc:
            errors.append("%s: %s" % (provider, exc))
            continue
        _TICKS[canon] = (now + 2.5, row)
        return row
    if hit:
        return hit[1]
    raise PublicFeedError("public feed has no live price for %s (%s)"
                          % (canon, "; ".join(errors) or "no provider"))


def _binance_ticker(symbol: str) -> Dict[str, Any]:
    data = _http_json("https://api.binance.com/api/v3/ticker/24hr?symbol=%s"
                      % urllib.parse.quote(symbol))
    price = _num((data or {}).get("lastPrice"))
    open24 = _num((data or {}).get("openPrice"))
    high = _num((data or {}).get("highPrice"))
    low = _num((data or {}).get("lowPrice"))
    if price <= 0:
        raise PublicFeedError("binance returned no 24h price")
    vol = int(_num((data or {}).get("count")) or _num((data or {}).get("volume")))
    return {"price": price, "open24": open24, "high": high, "low": low,
            "vol": vol, "change": ((price - open24) / open24 * 100.0) if open24 else 0.0}


def _yahoo_ticker(symbol: str) -> Dict[str, Any]:
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=5d"
           % urllib.parse.quote(symbol))
    data = _http_json(url)
    chart = (data or {}).get("chart") or {}
    result = (chart.get("result") or [None])[0]
    if not result:
        err = (chart.get("error") or {}).get("description") or "no result"
        raise PublicFeedError(str(err))
    meta = result.get("meta") or {}
    price = _num(meta.get("regularMarketPrice"))
    open24 = _num(meta.get("previousClose")) or _num(meta.get("chartPreviousClose"))
    high = _num(meta.get("regularMarketDayHigh"))
    low = _num(meta.get("regularMarketDayLow"))
    vol = int(_num(meta.get("regularMarketVolume")))
    if price <= 0:
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = [_num(v) for v in (quote.get("close") or []) if _num(v) > 0]
        price = closes[-1] if closes else 0.0
        open24 = open24 or _num((quote.get("open") or [None])[-1])
        high = high or max([_num(v) for v in (quote.get("high") or [])] or [price])
        low = low or min([_num(v) for v in (quote.get("low") or []) if _num(v)] or [price])
    if price <= 0:
        raise PublicFeedError("yahoo returned no 24h price")
    return {"price": price, "open24": open24 or price, "high": high or price,
            "low": low or price, "vol": vol,
            "change": ((price - open24) / open24 * 100.0) if open24 else 0.0}


def ticker(symbol: str) -> Dict[str, Any]:
    """24h price/high/low/volume, cached for 15 seconds."""
    canon = canonical(symbol)
    now = time.time()
    hit = _TICKERS.get(canon)
    if hit and now < hit[0]:
        return hit[1]
    errors: List[str] = []
    for provider, psym in _chain(canon):
        try:
            row = _binance_ticker(psym) if provider == "binance" else _yahoo_ticker(psym)
        except Exception as exc:
            errors.append("%s: %s" % (provider, exc))
            continue
        _TICKERS[canon] = (now + 15.0, row)
        return row
    if hit:
        return hit[1]
    raise PublicFeedError("public feed has no 24h stats for %s (%s)"
                          % (canon, "; ".join(errors) or "no provider"))


def m1_volume(symbol: str) -> Tuple[int, int]:
    """(current M1 bar time, its tick/trade count) for the participation meter."""
    canon = canonical(symbol)
    now = time.time()
    hit = _VOLUME.get(canon)
    if hit and now < hit[0]:
        return hit[1]
    try:
        bars = candles(canon, "1m", 2)
    except Exception:
        return 0, 0
    if not bars:
        return 0, 0
    last = bars[-1]
    out = (int(int(last[0]) // 1000), int(last[5]))
    _VOLUME[canon] = (now + 8.0, out)
    return out


def reset_cache() -> None:
    """Drop every cached series (tests / manual refresh)."""
    _CANDLES.clear()
    _TICKS.clear()
    _TICKERS.clear()
    _VOLUME.clear()
