"""
News Trader Bot (Bot 5).

What it does
------------
1. Keeps an economic-news calendar (Forex Factory weekly JSON, with a disk
   cache so it keeps working offline / after a restart).
2. Shows the upcoming high-impact events with a live countdown.
3. Works out its own direction - BUY or SELL - for the next event from:
      a) the forecast vs previous change of the releasing currency,
      b) the live Market Pressure Watch dials (buyer% vs seller%) when the
         dashboards are streaming,
      c) short-term M1 momentum as a tie-breaker.
   The chosen side, the score and a plain-language reason are all reported,
   so the dashboard can show exactly *why* the bot wants to buy or sell.
4. Three trading styles:
      - "anticipate": open a market position `pre_minutes` BEFORE the news
        in the chosen direction, then close it `post_minutes` after.
      - "straddle"  : park a buy-stop above and a sell-stop below the price
        before the news; whichever side the market breaks runs, the other is
        cancelled, then it closes after the news.
      - "follow"    : wait for the release, then trade the actual reaction.
5. Reports entry, countdown, SL/TP, live floating P/L and the realised result
   so every panel (and the dashboard stats) can show the profit.

All MetaTrader5 calls happen on the engine thread - the network fetch runs on
its own daemon thread and only ever fills a lock-protected cache.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from . import market_ai as ai_mod
from . import sessions as sess_mod

try:  # pragma: no cover - only missing on non-Windows
    import MetaTrader5 as mt5  # type: ignore
except Exception:  # pragma: no cover
    mt5 = None  # type: ignore

try:  # pragma: no cover
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # type: ignore

DEFAULTS: Dict[str, Any] = {
    "running": False,
    "lot": 0.01,
    "symbol": "XAUUSDm",
    "magic": 777005,            # distinct from bot1..bot4 (777001..777004)
    "mode": "anticipate",       # anticipate | straddle | follow
    "min_impact": "high",       # high | medium | all
    "currencies": "",           # comma list, empty = the symbol's own currencies
    "pre_minutes": 5.0,         # enter this many minutes BEFORE the release
    "post_minutes": 15.0,       # close this many minutes AFTER the release
    "min_gap_minutes": 15.0,    # ignore another event that lands too close
    "tp": 3.0,                  # take profit in account money
    "sl": 3.0,                  # stop loss in account money
    "straddle_gap": 3.0,        # $ from the price for the straddle stops
    "max_trades": 1,
    "cooldown_sec": 30.0,
    "slippage_points": 30,
    "bias_min_score": 1,        # minimum confidence before a trade is taken
    "trade_weekend": False,
    "refresh_min": 15.0,        # calendar refresh interval (minutes)
    "auto_close_after_news": True,
    # ---- trading-sessions board (Sydney / Tokyo / London / New York) ----
    # The panel above the watch dials. Empty = follow the market of this bot.
    "session_market": "",
    "session_range_days": 20.0,   # days of M15 history averaged per session
    # ---- full AI market analysis (5m / 10m / 15m / 1h) ----
    # Which market the AI panel reads (empty = follow this bot's market) and the
    # timeframe whose target prices are shown first.
    "ai_market": "",
    "ai_timeframe": ai_mod.DEFAULT_TIMEFRAME,
    # Hand-entered events - always available even when the online feed is down.
    # Each: {"title", "currency", "date" (ISO) | "ts", "impact", "forecast", "previous"}
    "manual_events": [],
}

MODE_NAMES = ("anticipate", "straddle", "follow")
IMPACT_ORDER = {"holiday": 0, "low": 1, "medium": 2, "high": 3}

# Headline sites publish in the past by nature ("Gold jumps..." was written an
# hour ago), so their rows get a reading window in the list before they expire.
HEADLINE_SOURCES = ("google-news", "yahoo-finance", "investing.com", "forex-factory")
HEADLINE_WINDOW_SEC = 12 * 3600.0
HEADLINE_ROWS_MAX = 60

TRADINGVIEW_FEED_URL = "https://economic-calendar.tradingview.com/events"
FEED_URLS = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
)
INVESTING_RSS_URLS = (
    "https://www.investing.com/rss/forex.rss",
    "https://www.investing.com/rss/news_25.rss",
)
YAHOO_RSS_URL = "https://finance.yahoo.com/news/rssindex"
# Google News RSS headline search ("google theke data niye"): one RSS feed per
# topic that moves the platform markets. Every query is read exactly like the
# other headline sites and merged into the same calendar.
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
GOOGLE_NEWS_QUERIES = (
    "forex OR dollar OR currency market",
    "gold price OR XAUUSD",
    "Federal Reserve OR inflation OR CPI",
    "oil price OR OPEC",
    "bitcoin OR crypto market",
)
# honored when the calendar host rate-limits us (HTTP 429)
_FEED_PENALTY_SEC = 600.0

# Currencies affecting the user's platform broker markets:
# XAUUSDm, XAUUSD247m, XAUGBPm, BTCUSDTm, BTCUSDm, DXYm, EURUSDm, USDJPYm, USOILm, USTECm, USTEC_x100m, USDCLPm, USDXOFm, ORCLm
ALL_PLATFORM_CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "NZD", "CNH", "CNY", "CLP", "XOF", "XAU", "BTC", "OIL",
    "TECH", "ORCL",
}

# currencies we can recognise inside a broker symbol
_CURRENCIES = (
    "USDT", "USDC", "USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD",
    "CNH", "CNY", "HKD", "SGD", "NOK", "SEK", "DKK", "PLN", "MXN", "ZAR",
    "TRY", "RUB", "INR", "BRL", "XAU", "XAG", "XPT", "BTC", "ETH", "SOL",
    "XRP", "DOGE", "ADA", "LTC", "BNB",
)

# order / position ordinals (avoid a hard dependency on module constants)
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
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


class _FeedHttpError(Exception):
    """Non-200 response from the calendar host (carries the HTTP status)."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = int(status or 0)


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
        out = float(v)
    except (TypeError, ValueError):
        return default
    if out != out:  # NaN
        return default
    return out


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(v, hi))


# instruments that are simply quoted in USD (news that moves them: USD)
_USD_INSTRUMENTS = (
    "USOIL", "UKOIL", "BRENT", "NGAS", "XTIUSD", "WTI", "CL", "DXY", "USDX", "DX",
    "USTEC", "NAS100", "US100", "SPX500", "US500", "US30", "DJ30", "XAUXAU",
)
# real world currencies news can be paired against
_REAL_CCY = {
    "USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "CNH", "CNY",
    "HKD", "SGD", "NOK", "SEK", "DKK", "PLN", "MXN", "ZAR", "TRY", "RUB", "INR", "BRL",
}
_METALS_CRYPTO = ("XAU", "XAG", "XPT", "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LTC", "BNB")

# Every market the platform offers to this news engine (see compute_market_impacts).
# Bot 5 can trade all of them - the dashboard chips list them dynamically.
PLATFORM_MARKETS = [
    "XAUUSDm", "XAUUSD247m", "XAUGBPm", "BTCUSDTm", "BTCUSDm", "DXYm",
    "EURUSDm", "USDJPYm", "USOILm", "USTECm", "USTEC_x100m",
    "USDCLPm", "USDXOFm", "ORCLm",
]


def _clean_symbol(symbol: str) -> str:
    s = "".join(ch for ch in str(symbol or "").upper() if ch.isalpha())
    for suffix in ("PRO", "RAW", "ECN", "M", "C", "Z"):
        if s.endswith(suffix) and len(s) > len(suffix) + 3:
            s = s[: -len(suffix)]
            break
    return s


def split_symbol(symbol: str) -> Tuple[str, str]:
    """Best-effort base/quote split of a broker symbol ('XAUUSDm' -> XAU/USD)."""
    s = _clean_symbol(symbol)
    for key in _USD_INSTRUMENTS:
        if s.startswith(key):
            return key, "USD"
    for cur in _CURRENCIES:
        if s.startswith(cur):
            rest = s[len(cur):]
            for quote in _CURRENCIES:
                if rest == quote or rest.startswith(quote):
                    return cur, quote
            return cur, rest[:3]
    return s[:3], s[3:6]


def currency_of_market(symbol: str) -> str:
    """The currency whose news moves this symbol the most.

    'XAUUSDm' -> USD, 'XAUGBPm' -> GBP, 'EURUSDm' -> EUR, 'USOILm' -> USD.
    """
    s = _clean_symbol(symbol)
    for key in _USD_INSTRUMENTS:
        if s.startswith(key):
            return "USD"
    base, quote = split_symbol(symbol)
    if base in _METALS_CRYPTO:
        return quote if quote in _REAL_CCY else "USD"
    return base if base in _REAL_CCY else (quote if quote in _REAL_CCY else "USD")


def _parse_event(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise one raw calendar row. Returns None when it is unusable."""
    title = str(raw.get("title") or raw.get("event") or "").strip()
    if not title:
        return None
    iso = str(raw.get("date") or raw.get("time") or "").strip()
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ts = dt.timestamp()

    impact_raw = str(raw.get("impact") or "").strip().lower()
    impact = impact_raw if impact_raw in IMPACT_ORDER else "low"
    if impact == "holiday":
        return None

    def _num(v: Any) -> Optional[float]:
        if v is None:
            return None
        s = str(v).strip().replace("%", "").replace(",", "")
        if not s or s in ("-", "—", "N/A"):
            return None
        mult = 1.0
        low = s.lower()
        if low.endswith("k"):
            mult, s = 1e3, s[:-1]
        elif low.endswith("m"):
            mult, s = 1e6, s[:-1]
        elif low.endswith("b"):
            mult, s = 1e9, s[:-1]
        try:
            return float(s) * mult
        except ValueError:
            return None

    return {
        "title": title,
        "country": str(raw.get("country") or "").upper(),
        "ts": ts,
        "impact": impact,
        "forecast": _num(raw.get("forecast")),
        "previous": _num(raw.get("previous")),
        "actual": _num(raw.get("actual")),
    }


def _rss_time(pub: Optional[str]) -> float:
    """RFC822 RSS date -> epoch. 0 means unusable (never a phantom "now" event)."""
    if not pub:
        return 0.0
    try:
        return parsedate_to_datetime(pub).timestamp()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(str(pub).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _headline_currency(title: str) -> str:
    """Read a headline and say which market family it moves (gold, crypto, tech...)."""
    t = str(title or "").upper()

    def word(*words: str) -> bool:
        return any(re.search(rf"\b{w}\b", t) for w in words)

    if word("GOLD", "XAU", "XAUUSD", "BULLION"):
        return "XAU"
    if word("BITCOIN", "BTC", "BTCUSD", "CRYPTO", "CRYPTOCURRENCY", "ETHEREUM", "ETH"):
        return "BTC"
    if word("NASDAQ", "USTEC", "DOW", "S&P", "SP500", "STOCKS", "EQUITIES", "WALL"):
        return "TECH"
    if word("ORACLE", "ORCL"):
        return "ORCL"
    if word("EURO", "ECB", "EUROZONE", "EURUSD"):
        return "EUR"
    if word("POUND", "STERLING", "BOE", "GBP", "GBPUSD"):
        return "GBP"
    if word("YEN", "BOJ", "JPY", "USDJPY"):
        return "JPY"
    if word("CRUDE", "OIL", "OPEC", "BRENT", "WTI", "USOIL"):
        return "OIL"
    if word("CANADA", "CAD", "USDCAD"):
        return "CAD"
    if word("AUSTRALIA", "AUD", "AUSSIE", "RBA"):
        return "AUD"
    return "USD"


def _headline_impact(title: str) -> str:
    """High-impact words in a headline; everything else reads as medium."""
    t = str(title or "").upper()
    majors = ("FED", "FEDERAL", "POWELL", "RATE", "INFLATION", "CPI", "JOBS", "NFP",
              "ECB", "BOJ", "WAR", "EARNINGS", "TARIFF", "RECESSION", "CRISIS", "SANCTION")
    return "high" if any(w in t for w in majors) else "medium"


def predict_dollar_move(ev: Optional[Dict[str, Any]], market: str = "XAUUSDm", side: str = "") -> Dict[str, Any]:
    """AI Expected Price Movement Engine:
    Calculates the predicted magnitude in dollars ($) or points and direction (UP / DOWN)
    that a given economic news event will move the target market.

    Factors considered:
    1. Historical event volatility tier (NFP, CPI, Fed/FOMC, PPI, Retail Sales, Claims, etc.)
    2. Event impact rating (high / medium / low)
    3. Divergence / surprise between forecast, previous and actual numbers
    4. Asset class scale (Gold $/oz, Bitcoin $, Crude Oil $/bbl, Nasdaq points, Forex pips)
    5. Trading direction (BUY = UP / উপরে উঠবে, SELL = DOWN / নিচে নামবে)
    """
    if not ev:
        return {
            "market": market, "side": "", "direction": "FLAT", "move_dollar": 0.0,
            "move_abs": 0.0, "signed_dollar": 0.0, "val_str": "$0.00", "range_str": "—",
            "text_en": "", "text_bn": "", "dir_en": "FLAT", "dir_bn": "নিরপেক্ষ",
            "confidence": 0,
        }

    title = str(ev.get("title") or "").strip()
    title_low = title.lower()
    impact = str(ev.get("impact") or "medium").lower()
    m_clean = "".join(ch for ch in str(market or "XAUUSDm").upper() if ch.isalpha())

    # --- 1. Base Volatility Matrix by Asset Family & Event Hierarchy ---
    is_ultra = any(k in title_low for k in (
        "non-farm", "nonfarm", "nfp", "cpi", "consumer price", "interest rate",
        "federal funds", "rate decision", "fomc", "powell", "fed chair",
        "war", "crisis", "sanction"
    ))
    is_major = any(k in title_low for k in (
        "retail sales", "ppi", "producer price", "pce", "gdp", "ism", "pmi",
        "unemployment", "jobless", "claims", "adp", "durable", "central bank"
    )) or impact == "high"
    is_med = impact == "medium" or any(k in title_low for k in (
        "consumer sentiment", "confidence", "trade balance", "housing", "crude oil"
    ))

    # Base magnitudes and ranges
    if "XAU" in m_clean:  # Gold ($ per oz)
        if is_ultra:
            base, r_lo, r_hi = 26.50, 20.00, 38.00
        elif is_major:
            base, r_lo, r_hi = 17.50, 13.00, 25.00
        elif is_med:
            base, r_lo, r_hi = 9.50, 6.50, 14.00
        else:
            base, r_lo, r_hi = 4.80, 3.00, 7.50
        fmt = "dollar"
    elif "BTC" in m_clean:  # Bitcoin ($ per coin)
        if is_ultra:
            base, r_lo, r_hi = 1750.0, 1200.0, 2600.0
        elif is_major:
            base, r_lo, r_hi = 1100.0, 750.0, 1600.0
        elif is_med:
            base, r_lo, r_hi = 550.0, 350.0, 850.0
        else:
            base, r_lo, r_hi = 250.0, 150.0, 400.0
        fmt = "btc"
    elif any(k in m_clean for k in ("OIL", "WTI", "BRENT", "CL")):  # Crude Oil ($/bbl)
        is_oil_spec = any(w in title_low for w in ("oil", "crude", "opec", "inventory", "eia", "api"))
        if is_oil_spec or is_ultra:
            base, r_lo, r_hi = 2.40, 1.70, 3.80
        elif is_major:
            base, r_lo, r_hi = 1.60, 1.10, 2.40
        elif is_med:
            base, r_lo, r_hi = 0.85, 0.55, 1.30
        else:
            base, r_lo, r_hi = 0.40, 0.25, 0.65
        fmt = "oil"
    elif any(k in m_clean for k in ("USTEC", "NAS100", "TECH")):  # Nasdaq points
        if is_ultra:
            base, r_lo, r_hi = 210.0, 150.0, 320.0
        elif is_major:
            base, r_lo, r_hi = 125.0, 85.0, 185.0
        elif is_med:
            base, r_lo, r_hi = 60.0, 40.0, 95.0
        else:
            base, r_lo, r_hi = 28.0, 18.0, 45.0
        fmt = "tech"
    elif "JPY" in m_clean:  # USDJPY (pips / JPY)
        if is_ultra:
            base, r_lo, r_hi = 1.35, 0.95, 2.10
        elif is_major:
            base, r_lo, r_hi = 0.80, 0.55, 1.25
        elif is_med:
            base, r_lo, r_hi = 0.45, 0.30, 0.70
        else:
            base, r_lo, r_hi = 0.20, 0.12, 0.35
        fmt = "jpy"
    elif "DXY" in m_clean or "DX" in m_clean:  # DXY Index pts
        if is_ultra:
            base, r_lo, r_hi = 0.90, 0.65, 1.45
        elif is_major:
            base, r_lo, r_hi = 0.55, 0.38, 0.85
        elif is_med:
            base, r_lo, r_hi = 0.30, 0.20, 0.48
        else:
            base, r_lo, r_hi = 0.15, 0.08, 0.24
        fmt = "dxy"
    elif "EUR" in m_clean or "GBP" in m_clean or "AUD" in m_clean:  # Major Forex pairs
        if is_ultra:
            base, r_lo, r_hi = 0.0085, 0.0060, 0.0130
        elif is_major:
            base, r_lo, r_hi = 0.0050, 0.0035, 0.0080
        elif is_med:
            base, r_lo, r_hi = 0.0028, 0.0018, 0.0042
        else:
            base, r_lo, r_hi = 0.0014, 0.0009, 0.0022
        fmt = "forex"
    elif "ORCL" in m_clean:  # Oracle stock ($)
        if is_ultra:
            base, r_lo, r_hi = 4.20, 3.00, 6.50
        elif is_major:
            base, r_lo, r_hi = 2.40, 1.60, 3.80
        elif is_med:
            base, r_lo, r_hi = 1.20, 0.80, 1.90
        else:
            base, r_lo, r_hi = 0.50, 0.30, 0.80
        fmt = "dollar"
    elif "CLP" in m_clean:  # USDCLP
        base, r_lo, r_hi = 12.0, 8.0, 18.0
        fmt = "points"
    elif "XOF" in m_clean:  # USDXOF
        base, r_lo, r_hi = 8.0, 5.0, 12.0
        fmt = "points"
    else:  # Generic default
        base, r_lo, r_hi = 10.0, 7.0, 15.0
        fmt = "dollar"

    # --- 2. Divergence / Surprise Factor ---
    f_val = ev.get("forecast")
    p_val = ev.get("previous")
    actual = ev.get("actual")
    comp_val = actual if actual is not None else f_val
    if comp_val is not None and p_val is not None:
        try:
            delta = abs(float(comp_val) - float(p_val))
            denom = abs(float(p_val)) if abs(float(p_val)) > 1e-4 else 1.0
            ratio = min(delta / denom, 2.5)
            mult = 1.0 + min(0.40, ratio * 0.25)
            base *= mult
            r_lo *= mult
            r_hi *= mult
        except (ValueError, TypeError):
            pass

    # --- 3. Format values according to asset type ---
    if fmt == "dollar":
        move_abs = round(base, 2)
        r_lo_v = round(r_lo, 2)
        r_hi_v = round(r_hi, 2)
        val_str = f"${move_abs:.2f}"
        range_str = f"${r_lo_v:.2f} - ${r_hi_v:.2f}"
    elif fmt == "btc":
        move_abs = round(base, 0)
        r_lo_v = round(r_lo, 0)
        r_hi_v = round(r_hi, 0)
        val_str = f"${move_abs:,.0f}"
        range_str = f"${r_lo_v:,.0f} - ${r_hi_v:,.0f}"
    elif fmt == "oil":
        move_abs = round(base, 2)
        r_lo_v = round(r_lo, 2)
        r_hi_v = round(r_hi, 2)
        val_str = f"${move_abs:.2f}"
        range_str = f"${r_lo_v:.2f} - ${r_hi_v:.2f}"
    elif fmt == "tech":
        move_abs = round(base, 1)
        r_lo_v = round(r_lo, 1)
        r_hi_v = round(r_hi, 1)
        val_str = f"${move_abs:.1f}"
        range_str = f"${r_lo_v:.1f} - ${r_hi_v:.1f}"
    elif fmt == "forex":
        move_abs = round(base, 4)
        pips = int(round(move_abs * 10000))
        r_lo_pips = int(round(r_lo * 10000))
        r_hi_pips = int(round(r_hi * 10000))
        val_str = f"${move_abs:.4f} ({pips} pips)"
        range_str = f"{r_lo_pips} - {r_hi_pips} pips"
    elif fmt == "jpy":
        move_abs = round(base, 2)
        pips = int(round(move_abs * 100))
        val_str = f"¥{move_abs:.2f} ({pips} pips)"
        range_str = f"{int(round(r_lo * 100))} - {int(round(r_hi * 100))} pips"
    elif fmt == "dxy":
        move_abs = round(base, 2)
        val_str = f"{move_abs:.2f} pts"
        range_str = f"{r_lo:.2f} - {r_hi:.2f} pts"
    else:
        move_abs = round(base, 2)
        val_str = f"${move_abs:.2f}"
        range_str = f"${r_lo:.2f} - ${r_hi:.2f}"

    # --- 4. Direction & Signed Dollar Movement ---
    side_u = str(side or "").strip().upper()
    if side_u == "BUY":
        direction = "UP"
        signed_dollar = move_abs
        sign = "+"
        dir_en = "UP"
        dir_bn = "উপরে উঠবে"
        text_en = f"+{val_str} UP"
        text_bn = f"+{val_str} উপরে উঠবে"
    elif side_u == "SELL":
        direction = "DOWN"
        signed_dollar = -move_abs
        sign = "-"
        dir_en = "DOWN"
        dir_bn = "নিচে নামবে"
        text_en = f"-{val_str} DOWN"
        text_bn = f"-{val_str} নিচে নামবে"
    else:
        direction = "FLAT"
        signed_dollar = 0.0
        sign = "±"
        dir_en = "FLAT"
        dir_bn = "নিরপেক্ষ"
        text_en = f"±{val_str}"
        text_bn = f"±{val_str}"

    return {
        "market": market,
        "side": side_u,
        "direction": direction,
        "move_dollar": signed_dollar,
        "move_abs": move_abs,
        "signed_dollar": signed_dollar,
        "val_str": val_str,
        "sign": sign,
        "range_str": range_str,
        "text_en": text_en,
        "text_bn": text_bn,
        "dir_en": dir_en,
        "dir_bn": dir_bn,
        "confidence": 85 if is_ultra else 75 if is_major else 65 if is_med else 50,
    }


# impact table memo - the dashboard rebuilds hundreds of news rows on every
# engine tick, and this pure function was where most of that time went
_IMPACT_CACHE: Dict[Tuple[str, str, str, str, str], List[Dict[str, Any]]] = {}


def compute_market_impacts(ev: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Determine which broker platform markets are impacted and the BUY / SELL direction for each.

    Platform markets:
      XAUUSDm, XAUUSD247m, XAUGBPm, BTCUSDTm, BTCUSDm, DXYm, EURUSDm,
      USDJPYm, USOILm, USTECm, USTEC_x100m, USDCLPm, USDXOFm, ORCLm.

    The result is memoised per event signature; callers get a copy so a cached
    row can never be mutated by whoever reads it.
    """
    if not ev:
        return []

    cache_key = (
        str(ev.get("country") or ev.get("currency") or "USD").upper().strip(),
        str(ev.get("title") or ""),
        repr(ev.get("forecast")), repr(ev.get("previous")), repr(ev.get("actual")),
    )
    cached = _IMPACT_CACHE.get(cache_key)
    if cached is not None:
        return [dict(item) for item in cached]

    c = str(ev.get("country") or ev.get("currency") or "USD").upper().strip()
    title_lower = str(ev.get("title") or "").lower()

    # Determine currency bias (+1 = bullish for that currency, -1 = bearish)
    f = ev.get("forecast")
    p = ev.get("previous")
    actual = ev.get("actual")

    is_inverse = any(w in title_lower for w in ("unemployment", "jobless", "claims", "job cut"))
    is_oil_inv = any(w in title_lower for w in ("crude oil inventories", "oil stocks", "api crude", "eia crude"))

    bias_val = 0
    if f is not None and p is not None:
        try:
            diff = float(f) - float(p)
            if abs(diff) > 1e-9:
                if is_inverse or is_oil_inv:
                    bias_val = -1 if diff > 0 else 1
                else:
                    bias_val = 1 if diff > 0 else -1
        except (ValueError, TypeError):
            bias_val = 0

    if bias_val == 0 and actual is not None and p is not None:
        try:
            diff = float(actual) - float(p)
            if abs(diff) > 1e-9:
                if is_inverse or is_oil_inv:
                    bias_val = -1 if diff > 0 else 1
                else:
                    bias_val = 1 if diff > 0 else -1
        except (ValueError, TypeError):
            bias_val = 0

    if bias_val == 0:
        hawkish_kw = ("hike", "hawkish", "tightening", "growth", "rise", "jump", "surge", "gain", "high", "beat", "strong")
        dovish_kw = ("cut", "dovish", "easing", "drop", "fall", "slump", "miss", "weak", "decline", "slowdown", "recession")
        if any(w in title_lower for w in hawkish_kw):
            bias_val = 1
        elif any(w in title_lower for w in dovish_kw):
            bias_val = -1
        else:
            bias_val = 1  # Standard baseline direction

    impacts: List[Dict[str, str]] = []

    if c == "USD":
        if bias_val > 0:  # USD Strong / Bullish
            impacts = [
                {"market": "DXYm", "side": "BUY", "reason": "USD Index rallies"},
                {"market": "USDJPYm", "side": "BUY", "reason": "USD Base Pair rises"},
                {"market": "XAUUSDm", "side": "SELL", "reason": "Gold drops vs strong USD"},
                {"market": "XAUUSD247m", "side": "SELL", "reason": "Gold drops vs strong USD"},
                {"market": "EURUSDm", "side": "SELL", "reason": "Euro drops vs strong USD"},
                {"market": "BTCUSDm", "side": "SELL", "reason": "Crypto drops on strong USD"},
                {"market": "BTCUSDTm", "side": "SELL", "reason": "Crypto drops on strong USD"},
                {"market": "USTECm", "side": "SELL", "reason": "Nasdaq dips on USD/yield rise"},
                {"market": "USTEC_x100m", "side": "SELL", "reason": "Nasdaq dips on USD/yield rise"},
                {"market": "USOILm", "side": "SELL", "reason": "Crude drops vs strong USD"},
                {"market": "ORCLm", "side": "SELL", "reason": "Tech equity dips with Nasdaq"},
                {"market": "XAUGBPm", "side": "SELL", "reason": "Gold drops globally"},
                {"market": "USDCLPm", "side": "BUY", "reason": "USD Base Pair rises"},
                {"market": "USDXOFm", "side": "BUY", "reason": "USD Base Pair rises"},
            ]
        else:  # USD Weak / Bearish
            impacts = [
                {"market": "XAUUSDm", "side": "BUY", "reason": "Gold rallies on weak USD"},
                {"market": "XAUUSD247m", "side": "BUY", "reason": "Gold rallies on weak USD"},
                {"market": "EURUSDm", "side": "BUY", "reason": "Euro rallies vs weak USD"},
                {"market": "BTCUSDm", "side": "BUY", "reason": "Crypto rallies on weak USD"},
                {"market": "BTCUSDTm", "side": "BUY", "reason": "Crypto rallies on weak USD"},
                {"market": "USTECm", "side": "BUY", "reason": "Nasdaq rallies on rate ease"},
                {"market": "USTEC_x100m", "side": "BUY", "reason": "Nasdaq rallies on rate ease"},
                {"market": "USOILm", "side": "BUY", "reason": "Crude rallies on weak USD"},
                {"market": "ORCLm", "side": "BUY", "reason": "Tech rallies with Nasdaq"},
                {"market": "XAUGBPm", "side": "BUY", "reason": "Gold rallies globally"},
                {"market": "DXYm", "side": "SELL", "reason": "USD Index drops"},
                {"market": "USDJPYm", "side": "SELL", "reason": "USD Base Pair drops"},
                {"market": "USDCLPm", "side": "SELL", "reason": "USD Base Pair drops"},
                {"market": "USDXOFm", "side": "SELL", "reason": "USD Base Pair drops"},
            ]
    elif c == "EUR":
        if bias_val > 0:
            impacts = [
                {"market": "EURUSDm", "side": "BUY", "reason": "EUR strengthens"},
                {"market": "DXYm", "side": "SELL", "reason": "EUR (57.6% of DXY) rises"},
            ]
        else:
            impacts = [
                {"market": "EURUSDm", "side": "SELL", "reason": "EUR weakens"},
                {"market": "DXYm", "side": "BUY", "reason": "EUR drops -> DXY rises"},
            ]
    elif c == "GBP":
        if bias_val > 0:
            impacts = [
                {"market": "XAUGBPm", "side": "SELL", "reason": "Gold in stronger GBP drops"},
                {"market": "DXYm", "side": "SELL", "reason": "GBP (11.9% of DXY) rises"},
            ]
        else:
            impacts = [
                {"market": "XAUGBPm", "side": "BUY", "reason": "Gold in weaker GBP rises"},
                {"market": "DXYm", "side": "BUY", "reason": "GBP drops -> DXY rises"},
            ]
    elif c == "JPY":
        if bias_val > 0:
            impacts = [
                {"market": "USDJPYm", "side": "SELL", "reason": "JPY strengthens -> USDJPY drops"},
                {"market": "DXYm", "side": "SELL", "reason": "JPY (13.6% of DXY) rises"},
            ]
        else:
            impacts = [
                {"market": "USDJPYm", "side": "BUY", "reason": "JPY weakens -> USDJPY rises"},
                {"market": "DXYm", "side": "BUY", "reason": "JPY drops -> DXY rises"},
            ]
    elif c in ("CAD", "OIL") or is_oil_inv:
        if bias_val > 0:
            impacts = [
                {"market": "USOILm", "side": "BUY", "reason": "Crude Oil demand / draw (+)"},
                {"market": "DXYm", "side": "SELL", "reason": "Commodity currency gains"},
            ]
        else:
            impacts = [
                {"market": "USOILm", "side": "SELL", "reason": "Crude Oil inventory build / drop"},
                {"market": "DXYm", "side": "BUY", "reason": "Oil drops"},
            ]
    elif c in ("AUD", "NZD", "CNY", "CNH"):
        if bias_val > 0:
            impacts = [
                {"market": "USOILm", "side": "BUY", "reason": "Global growth demand (+)"},
                {"market": "USTECm", "side": "BUY", "reason": "Risk-on sentiment"},
                {"market": "BTCUSDm", "side": "BUY", "reason": "Risk-on sentiment"},
                {"market": "DXYm", "side": "SELL", "reason": "Safe-haven USD drops"},
            ]
        else:
            impacts = [
                {"market": "USOILm", "side": "SELL", "reason": "Global growth slowdown (-)"},
                {"market": "USTECm", "side": "SELL", "reason": "Risk-off sentiment"},
                {"market": "BTCUSDm", "side": "SELL", "reason": "Risk-off sentiment"},
                {"market": "DXYm", "side": "BUY", "reason": "Safe-haven USD gains"},
            ]
    elif c in ("XAU",):
        if bias_val > 0:
            impacts = [
                {"market": "XAUUSDm", "side": "BUY", "reason": "Gold bid on softer USD"},
                {"market": "XAUUSD247m", "side": "BUY", "reason": "Gold bid on softer USD"},
                {"market": "XAUGBPm", "side": "BUY", "reason": "Gold bid across currencies"},
                {"market": "DXYm", "side": "SELL", "reason": "Gold strength vs USD"},
            ]
        else:
            impacts = [
                {"market": "XAUUSDm", "side": "SELL", "reason": "Gold offered / stronger USD"},
                {"market": "XAUUSD247m", "side": "SELL", "reason": "Gold offered / stronger USD"},
                {"market": "XAUGBPm", "side": "SELL", "reason": "Gold offered across currencies"},
                {"market": "DXYm", "side": "BUY", "reason": "USD strength vs gold"},
            ]
    elif c == "BTC":
        if bias_val > 0:
            impacts = [
                {"market": "BTCUSDm", "side": "BUY", "reason": "Crypto risk-on bid"},
                {"market": "BTCUSDTm", "side": "BUY", "reason": "Crypto risk-on bid"},
                {"market": "USTECm", "side": "BUY", "reason": "Risk-on sentiment"},
                {"market": "DXYm", "side": "SELL", "reason": "Risk-on, softer USD"},
            ]
        else:
            impacts = [
                {"market": "BTCUSDm", "side": "SELL", "reason": "Crypto risk-off sell-off"},
                {"market": "BTCUSDTm", "side": "SELL", "reason": "Crypto risk-off sell-off"},
                {"market": "USTECm", "side": "SELL", "reason": "Risk-off sentiment"},
                {"market": "DXYm", "side": "BUY", "reason": "Risk-off, USD bid"},
            ]
    elif c in ("TECH", "USTEC"):
        if bias_val > 0:
            impacts = [
                {"market": "USTECm", "side": "BUY", "reason": "Tech / Nasdaq strength"},
                {"market": "USTEC_x100m", "side": "BUY", "reason": "Tech / Nasdaq strength"},
                {"market": "ORCLm", "side": "BUY", "reason": "Tech equity strength"},
                {"market": "DXYm", "side": "SELL", "reason": "Risk-on, softer USD"},
            ]
        else:
            impacts = [
                {"market": "USTECm", "side": "SELL", "reason": "Tech / Nasdaq weakness"},
                {"market": "USTEC_x100m", "side": "SELL", "reason": "Tech / Nasdaq weakness"},
                {"market": "ORCLm", "side": "SELL", "reason": "Tech equity weakness"},
                {"market": "DXYm", "side": "BUY", "reason": "Risk-off, USD bid"},
            ]
    elif c == "ORCL":
        side = "BUY" if bias_val > 0 else "SELL"
        impacts = [
            {"market": "ORCLm", "side": side, "reason": "Oracle equity reaction"},
            {"market": "USTECm", "side": side, "reason": "Tech sector follows Oracle"},
        ]
    elif c in ("ILS", "ISR"):
        if bias_val > 0:
            impacts = [
                {"market": "USOILm", "side": "SELL", "reason": "Middle East risk premium eases on growth"},
                {"market": "XAUUSDm", "side": "SELL", "reason": "Safe-haven gold demand eases on stability"},
                {"market": "BTCUSDm", "side": "BUY", "reason": "Risk-on crypto sentiment improves"},
                {"market": "DXYm", "side": "SELL", "reason": "Dollar safe-haven demand softens"},
                {"market": "USTECm", "side": "BUY", "reason": "Equities gain on regional calm"},
            ]
        else:
            impacts = [
                {"market": "USOILm", "side": "BUY", "reason": "Crude rallies on Middle East tension / risk"},
                {"market": "XAUUSDm", "side": "BUY", "reason": "Gold rallies on flight to safety"},
                {"market": "BTCUSDm", "side": "SELL", "reason": "Crypto softens on risk-off tension"},
                {"market": "DXYm", "side": "BUY", "reason": "US Dollar safe-haven bid"},
                {"market": "USTECm", "side": "SELL", "reason": "Tech equities drop on regional tension"},
            ]
    elif c == "CHF":
        if bias_val > 0:
            impacts = [
                {"market": "XAUUSDm", "side": "BUY", "reason": "Safe-haven Franc / Gold correlation"},
                {"market": "DXYm", "side": "SELL", "reason": "CHF gains vs USD -> softer DXY"},
                {"market": "EURUSDm", "side": "BUY", "reason": "European currency support"},
            ]
        else:
            impacts = [
                {"market": "XAUUSDm", "side": "SELL", "reason": "Safe-haven Franc / Gold demand eases"},
                {"market": "DXYm", "side": "BUY", "reason": "USD gains vs CHF -> firmer DXY"},
                {"market": "EURUSDm", "side": "SELL", "reason": "Euro softens with Swiss Franc"},
            ]
    elif c in ("ALL", "IMF", "WEF", "OPEC", "G7", "G20", "WORLD"):
        if bias_val > 0:
            impacts = [
                {"market": "USTECm", "side": "BUY", "reason": "Global growth optimism lifts equities"},
                {"market": "USOILm", "side": "BUY", "reason": "Energy demand supported by trade"},
                {"market": "BTCUSDm", "side": "BUY", "reason": "Macro liquidity sentiment improves"},
                {"market": "DXYm", "side": "SELL", "reason": "Safe-haven USD softens"},
                {"market": "XAUUSDm", "side": "SELL", "reason": "Risk-on reduces gold safe-haven"},
            ]
        else:
            impacts = [
                {"market": "XAUUSDm", "side": "BUY", "reason": "Flight to safety on global slowdown warnings"},
                {"market": "DXYm", "side": "BUY", "reason": "Cash and dollar safe-haven bid"},
                {"market": "USOILm", "side": "SELL", "reason": "Crude drops on global demand concerns"},
                {"market": "USTECm", "side": "SELL", "reason": "Equities decline on economic warnings"},
                {"market": "BTCUSDm", "side": "SELL", "reason": "Risk-off crypto pull-back"},
            ]
    else:
        if bias_val > 0:
            impacts = [
                {"market": "DXYm", "side": "BUY", "reason": f"{c} headline beat lifts market sentiment"},
                {"market": "XAUUSDm", "side": "SELL", "reason": "Gold dips on firmer currency sentiment"},
                {"market": "USTECm", "side": "BUY", "reason": "Equities supported by economic beat"},
                {"market": "BTCUSDm", "side": "BUY", "reason": "Crypto sentiment supported by macro data"},
                {"market": "USOILm", "side": "BUY", "reason": "Industrial commodity demand outlook"},
            ]
        else:
            impacts = [
                {"market": "DXYm", "side": "SELL", "reason": f"{c} headline miss softens sentiment"},
                {"market": "XAUUSDm", "side": "BUY", "reason": "Gold supported on macroeconomic caution"},
                {"market": "USTECm", "side": "SELL", "reason": "Equities soften on economic miss"},
                {"market": "BTCUSDm", "side": "SELL", "reason": "Crypto softens on cautious sentiment"},
                {"market": "USOILm", "side": "SELL", "reason": "Commodities soften on slower demand"},
            ]


    for m in impacts:
        pred = predict_dollar_move(ev, market=m.get("market", ""), side=m.get("side", ""))
        m["move_dollar"] = pred["move_dollar"]
        m["move_abs"] = pred["move_abs"]
        m["signed_dollar"] = pred["signed_dollar"]
        m["val_str"] = pred["val_str"]
        m["move_text_en"] = pred["text_en"]
        m["move_text_bn"] = pred["text_bn"]
        m["range_str"] = pred["range_str"]
        m["direction"] = pred["direction"]

    if len(_IMPACT_CACHE) > 4000:
        _IMPACT_CACHE.clear()
    _IMPACT_CACHE[cache_key] = impacts
    return [dict(item) for item in impacts]


# TradingView's calendar stopped filling "importance" for most rows (-1) and
# Forex-Factory's "nextweek" JSON returns 404, so upcoming HIGH / MEDIUM news
# would otherwise be flattened into a low-impact stream with no visible effect
# or move. The fallback tag rebuilds the tier from the title; the noisy
# "auction / speech / holiday" families are pinned to low by design.
_TV_UNIMPORTANT_KW = ("auction", "speech", "holiday", "festival", "buddha", "poya",
                      "memorial", "independence", "revolution", "prayer", "election")


def _importance_fallback(title: Any) -> str:
    """high/medium/low tier read from the title when a source sets none."""
    t = str(title or "").lower()
    if any(w in t for w in _TV_UNIMPORTANT_KW):
        return "low"
    high_kw = ("cpi", "inflation rate", "non-farm", "payroll", "unemployment rate",
               "jobless claims", "retail sales", "interest rate decision",
               "rate decision", "fomc", "federal funds", "crude oil inventories",
               "core pce", "advance gdp")
    if any(w in t for w in high_kw):
        return "high"
    medium_kw = ("ppi", "gdp", "pmi", "ism", "zew", "ifo business", "trade balance",
                 "current account", "industrial production", "consumer confidence",
                 "business confidence", "manufacturing sales", "eia", "baker hughes",
                 "inflation expectation", "durable goods", "housing starts",
                 "building permits", "employment cost", "michigan")
    if any(w in t for w in medium_kw):
        return "medium"
    return "low"


class NewsFeed:
    """Background calendar fetch with a disk + memory cache.

    The engine thread never blocks on the network: it only reads
    ``self.events`` which is swapped atomically under ``self.lock``.
    """

    def __init__(self, cache_path: Optional[Path] = None, logger=None) -> None:
        self.events: List[Dict[str, Any]] = []
        self.updated_at: float = 0.0
        self.source: str = "cache"
        self.error: str = ""
        self.lock = threading.Lock()
        self.logger = logger
        self.cache_path = cache_path
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._interval_sec = 900.0
        self.penalty_until: float = 0.0
        self.source_counts: Dict[str, int] = {}
        self._load_cache()

    # ------------------------------------------------------------- cache io
    def _load_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            if not self.cache_path.exists():
                return
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            rows = data.get("events") if isinstance(data, dict) else data
            if isinstance(rows, list):
                with self.lock:
                    self.events = [e for e in rows if isinstance(e, dict) and e.get("ts")]
                    self.updated_at = float((data or {}).get("updated_at") or 0.0)
                    self.source = "cache"
        except Exception:
            pass

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock:
                payload = {"updated_at": self.updated_at, "events": self.events}
            tmp = self.cache_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.cache_path)
        except Exception:
            pass

    # -------------------------------------------------------------- network
    def _fetch_tradingview(self) -> List[Dict[str, Any]]:
        """Pull live economic calendar from TradingView (covers next 7 days, 500+ events)."""
        now_dt = datetime.now(timezone.utc)
        f = (now_dt - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        t = (now_dt + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        url = f"{TRADINGVIEW_FEED_URL}?from={f}&to={t}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Origin": "https://www.tradingview.com",
        }
        if requests is not None:
            resp = requests.get(url, headers=headers, timeout=12)
            if resp.status_code != 200:
                raise _FeedHttpError(resp.status_code)
            data = resp.json()
        else:
            import urllib.request
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))

        res = data.get("result", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        parsed: List[Dict[str, Any]] = []
        for r in res:
            if not isinstance(r, dict):
                continue
            title = str(r.get("title") or r.get("indicator") or "").strip()
            if not title:
                continue
            ccy = str(r.get("currency") or r.get("country") or "").upper().strip()
            if not ccy:
                continue
            dt_str = str(r.get("date") or "")
            if not dt_str:
                continue
            # TradingView writes 7-digit fractional seconds
            # ("...T08:30:00.0000000Z") - trim the fraction to 6 digits so
            # fromisoformat never rejects the whole source
            dt_str = re.sub(r"\.(\d{6})\d*", r".\1", dt_str)
            try:
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except Exception:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()

            # TradingView importance: 0 low / 1 medium / 2 high - a string or
            # missing value must not kill the whole feed with a TypeError.
            # -1 (unset) falls back to a title tier so HIGH / MEDIUM news still
            # classifies for every source instead of collapsing into "low".
            try:
                imp_num = int(r.get("importance", -1))
            except (TypeError, ValueError):
                imp_num = -1
            if imp_num >= 2:
                imp = "high"
            elif imp_num == 1:
                imp = "medium"
            elif imp_num == 0:
                imp = "low"
            else:
                imp = _importance_fallback(title)

            def _to_num(v: Any) -> Optional[float]:
                if v is None:
                    return None
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            parsed.append({
                "title": title,
                "country": ccy,
                "ts": ts,
                "impact": imp,
                "forecast": _to_num(r.get("forecast")),
                "previous": _to_num(r.get("previous")),
                "actual": _to_num(r.get("actual")),
                "source": "tradingview",
            })
        return parsed

    def _generate_fallback_events(self) -> List[Dict[str, Any]]:
        """Realistic high-impact calendar events for offline or demo use if network is unreachable."""
        now = time.time()
        templates = [
            ("ISM Services PMI", "USD", "high", 55.7, 55.4, 2 * 3600),
            ("ADP Non-Farm Employment Change", "USD", "high", 143000.0, 103000.0, 14 * 3600),
            ("FOMC Meeting Minutes", "USD", "high", None, None, 28 * 3600),
            ("Initial Jobless Claims", "USD", "medium", 225000.0, 218000.0, 40 * 3600),
            ("Core CPI m/m", "USD", "high", 0.3, 0.3, 64 * 3600),
            ("PPI m/m", "USD", "medium", 0.2, 0.2, 88 * 3600),
            ("ECB Monetary Policy Statement", "EUR", "high", 3.65, 3.65, 20 * 3600),
            ("BOE Gov Bailey Speaks", "GBP", "high", None, None, 44 * 3600),
        ]
        out = []
        for title, country, impact, forecast, previous, delay in templates:
            out.append({
                "title": title,
                "country": country,
                "ts": now + delay,
                "impact": impact,
                "forecast": forecast,
                "previous": previous,
                "actual": None,
                "fallback": True,
            })
        return out

    def _fetch_forexfactory(self) -> List[Dict[str, Any]]:
        out = []
        for url in FEED_URLS:
            try:
                data = self._get_json(url)
                if isinstance(data, list):
                    for raw in data:
                        if isinstance(raw, dict):
                            ev = _parse_event(raw)
                            if ev:
                                ev["source"] = "forex-factory"
                                out.append(ev)
            except _FeedHttpError as exc:
                # honored: the calendar host is rate-limiting us
                if exc.status == 429:
                    self.penalty_until = time.time() + _FEED_PENALTY_SEC
            except Exception:
                continue
        return out

    def _fetch_investing_rss(self) -> List[Dict[str, Any]]:
        import xml.etree.ElementTree as ET
        from email.utils import parsedate_to_datetime

        out: List[Dict[str, Any]] = []
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        for url in INVESTING_RSS_URLS:
            try:
                if requests is not None:
                    resp = requests.get(url, headers=headers, timeout=10)
                    if resp.status_code != 200:
                        continue
                    xml_content = resp.content
                else:
                    import urllib.request
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=10) as r:
                        xml_content = r.read()
                root = ET.fromstring(xml_content)
                channel = root.find("channel")
                items = channel.findall("item") if channel is not None else []
                for item in items:
                    title = str(item.findtext("title") or "").strip()
                    if not title:
                        continue
                    pub = item.findtext("pubDate")
                    # an unparseable date must not become a phantom "now" event
                    ts = 0.0
                    if pub:
                        try:
                            ts = parsedate_to_datetime(pub).timestamp()
                        except Exception:
                            try:
                                ts = datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp()
                            except Exception:
                                ts = 0.0
                    if ts <= 0:
                        continue
                    t_up = title.upper()
                    ccy = "USD"
                    if "EUR" in t_up or "EURO" in t_up or "ECB" in t_up:
                        ccy = "EUR"
                    elif "GBP" in t_up or "POUND" in t_up or "STERLING" in t_up or "BOE" in t_up:
                        ccy = "GBP"
                    elif "JPY" in t_up or "YEN" in t_up or "BOJ" in t_up:
                        ccy = "JPY"
                    elif "OIL" in t_up or "CRUDE" in t_up or "OPEC" in t_up:
                        ccy = "OIL"
                    elif "CAD" in t_up:
                        ccy = "CAD"
                    elif "AUD" in t_up:
                        ccy = "AUD"

                    imp = "high" if any(w in t_up for w in ("FED", "POWELL", "RATE", "INFLATION", "CPI", "JOBS", "NFP", "ECB", "BOJ", "WAR")) else "medium"
                    out.append({
                        "title": title,
                        "country": ccy,
                        "ts": ts,
                        "impact": imp,
                        "forecast": None,
                        "previous": None,
                        "actual": None,
                        "source": "investing.com",
                    })
            except Exception:
                continue
        return out

    def _fetch_yahoo_rss(self) -> List[Dict[str, Any]]:
        import xml.etree.ElementTree as ET

        out: List[Dict[str, Any]] = []
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        try:
            if requests is not None:
                resp = requests.get(YAHOO_RSS_URL, headers=headers, timeout=10)
                if resp.status_code != 200:
                    return out
                xml_content = resp.content
            else:
                import urllib.request
                req = urllib.request.Request(YAHOO_RSS_URL, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as r:
                    xml_content = r.read()
            root = ET.fromstring(xml_content)
            channel = root.find("channel")
            items = channel.findall("item") if channel is not None else []
            for item in items:
                title = str(item.findtext("title") or "").strip()
                if not title:
                    continue
                pub = item.findtext("pubDate")
                # RSS pubDate is RFC822 ("Sun, 05 Oct 2026 09:00:00 +0000").
                # fromisoformat cannot read that - falling back to "now" here
                # used to turn every headline into a phantom event dated at the
                # fetch moment, which follow-mode then really traded.
                ts = 0.0
                if pub:
                    try:
                        ts = parsedate_to_datetime(pub).timestamp()
                    except Exception:
                        try:
                            ts = datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp()
                        except Exception:
                            ts = 0.0
                if ts <= 0:
                    continue  # unusable timing - never trade a phantom "now" event
                t_up = title.upper()
                ccy = "USD"
                if "EUR" in t_up or "EURO" in t_up:
                    ccy = "EUR"
                elif "GBP" in t_up or "POUND" in t_up:
                    ccy = "GBP"
                elif "JPY" in t_up or "YEN" in t_up:
                    ccy = "JPY"
                elif "OIL" in t_up or "CRUDE" in t_up:
                    ccy = "OIL"
                imp = "high" if any(w in t_up for w in ("FED", "POWELL", "RATE", "INFLATION", "CPI", "JOBS", "NFP", "WAR", "EARNINGS")) else "medium"
                out.append({
                    "title": title,
                    "country": ccy,
                    "ts": ts,
                    "impact": imp,
                    "forecast": None,
                    "previous": None,
                    "actual": None,
                    "source": "yahoo-finance",
                })
        except Exception:
            pass
        return out

    def _parse_headline_rss(self, xml_content: bytes, source: str) -> List[Dict[str, Any]]:
        """Parse an RSS document into headline events (shared by the news sites)."""
        import xml.etree.ElementTree as ET

        out: List[Dict[str, Any]] = []
        try:
            root = ET.fromstring(xml_content)
        except Exception:
            return out
        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else []
        for item in items:
            title = str(item.findtext("title") or "").strip()
            if not title:
                continue
            ts = _rss_time(item.findtext("pubDate"))
            if ts <= 0:
                continue  # unusable timing - never trade a phantom "now" event
            if ts < time.time() - 3 * 86400:
                continue  # headline is stale - it no longer moves the market
            publisher = str(item.findtext("source") or "").strip()
            ev: Dict[str, Any] = {
                "title": title,
                "country": _headline_currency(title),
                "ts": ts,
                "impact": _headline_impact(title),
                "forecast": None,
                "previous": None,
                "actual": None,
                "source": source,
            }
            if publisher:
                ev["publisher"] = publisher
            out.append(ev)
        return out

    def _fetch_rss(self, urls: Tuple[str, ...], source: str) -> List[Dict[str, Any]]:
        """Read one or more RSS feeds and return their headline events."""
        out: List[Dict[str, Any]] = []
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        for url in urls:
            try:
                if requests is not None:
                    resp = requests.get(url, headers=headers, timeout=10)
                    if resp.status_code != 200:
                        continue
                    xml_content = resp.content
                else:
                    import urllib.request
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=10) as r:
                        xml_content = r.read()
                out.extend(self._parse_headline_rss(xml_content, source))
            except Exception:
                continue
        return out

    def _fetch_google_news(self) -> List[Dict[str, Any]]:
        """Google News search feeds for the topics that move the platform markets."""
        urls = tuple(GOOGLE_NEWS_RSS.format(q=quote_plus(str(q))) for q in GOOGLE_NEWS_QUERIES)
        return self._fetch_rss(urls, "google-news")

    def fetch_once(self) -> int:
        """Pull events from every news site in the panel: TradingView, ForexFactory,
        Investing.com, Yahoo Finance and Google News."""
        rows: List[Dict[str, Any]] = []
        sources: List[str] = []

        # 1. TradingView economic calendar API (rich upcoming calendar)
        try:
            tv_rows = self._fetch_tradingview()
            if tv_rows:
                rows.extend(tv_rows)
                sources.append("tradingview")
        except Exception as exc:
            pass

        # 2. ForexFactory weekly feeds
        try:
            ff_rows = self._fetch_forexfactory()
            if ff_rows:
                rows.extend(ff_rows)
                sources.append("forex-factory")
        except Exception:
            pass

        # 3. Investing.com news feeds
        try:
            inv_rows = self._fetch_investing_rss()
            if inv_rows:
                rows.extend(inv_rows)
                sources.append("investing.com")
        except Exception:
            pass

        # 4. Yahoo Finance news feed
        try:
            y_rows = self._fetch_yahoo_rss()
            if y_rows:
                rows.extend(y_rows)
                sources.append("yahoo-finance")
        except Exception:
            pass

        # 5. Google News search feeds ("google theke data niye")
        try:
            g_rows = self._fetch_google_news()
            if g_rows:
                rows.extend(g_rows)
                sources.append("google-news")
        except Exception:
            pass

        if not rows:
            with self.lock:
                if self.events:
                    return len(self.events)
            rows = self._generate_fallback_events()
            sources = ["fallback"]

        parsed: List[Dict[str, Any]] = []
        seen = set()
        for ev in rows:
            norm_title = "".join(ch for ch in ev.get("title", "").lower() if ch.isalnum())[:24]
            key = (str(ev.get("country") or ""), norm_title, round(float(ev.get("ts") or 0) / 900))
            if key in seen:
                continue
            seen.add(key)
            parsed.append(ev)
        parsed.sort(key=lambda e: e["ts"])

        # per-site counts so the dashboard can show where every news item came from
        counts: Dict[str, int] = {}
        for ev in parsed:
            name = str(ev.get("source") or "calendar")
            counts[name] = counts.get(name, 0) + 1

        with self.lock:
            self.events = parsed
            self.updated_at = time.time()
            self.source = "+".join(sources) if sources else "calendar"
            self.source_counts = counts
        self.error = ""
        self._save_cache()
        return len(parsed)

    @staticmethod
    def _get_json(url: str) -> Any:
        if requests is not None:
            resp = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0 (SoftradeX)"})
            if resp.status_code != 200:
                raise _FeedHttpError(resp.status_code)
            return resp.json()
        import urllib.request  # pragma: no cover - fallback path

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (SoftradeX)"})
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as exc:  # pragma: no cover
            code = getattr(exc, "code", 0)
            raise _FeedHttpError(code) from exc

    # --------------------------------------------------------------- thread
    def ensure_started(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="news-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if time.time() >= self.penalty_until:
                try:
                    self.fetch_once()
                except Exception as exc:  # pragma: no cover
                    self.error = str(exc)
            wait = max(60.0, self._interval_sec)
            if self.penalty_until > time.time():
                wait = max(wait, self.penalty_until - time.time())
            self._stop.wait(wait)


class NewsBot:
    """News Trader (Bot 5)."""

    def __init__(self, logger=None, cache_path: Optional[Path] = None,
                 watch_provider=None, clock_provider=None) -> None:
        self.cfg: Dict[str, Any] = dict(DEFAULTS)
        self.logger = logger
        self.watch_provider = watch_provider
        self.clock_provider = clock_provider      # engine's server clock, or None
        self.feed = NewsFeed(cache_path=cache_path, logger=logger)

        self.symbol: str = ""
        self.symbol_ok: bool = False
        self.symbol_substituted: str = ""
        self.enabled: bool = False
        self.last_action: str = ""
        self.last_error: str = ""
        self.stats: Dict[str, Any] = {"opened": 0, "closed": 0, "wins": 0, "losses": 0,
                                     "realized": 0.0, "skipped": 0}

        self._resolve_ts: float = 0.0
        self._last_order_ts: float = 0.0
        self._last_event_key: str = ""        # event already traded -> never re-trade it
        self._straddle: Dict[str, Any] = {}    # {"buy": order, "sell": order, "event": key}
        self._active: Dict[str, Any] = {}      # the trade currently being managed
        self._bias: Dict[str, Any] = {}        # last computed bias + reason
        self._known_positions: Dict[int, Dict[str, Any]] = {}
        self._plan: str = ""                   # human readable "what happens next"
        self._last_decision_ts: float = 0.0
        self._exit_at: float = 0.0             # when the current news trade must end
        self._exit_event: str = ""
        self._symbols_cache: List[str] = []   # All-Market chip list (platform markets)
        self._symbols_ts: float = 0.0

    # ------------------------------------------------------------------ log
    def _log(self, kind: str, message: str) -> None:
        self.last_action = message
        if self.logger:
            try:
                self.logger(kind, f"[Bot5] {message}")
            except Exception:
                pass

    def _server_now(self) -> float:
        """MT5 broker time when available, local time otherwise.

        Position timestamps are broker-clock epochs - comparing them against
        the local clock mis-times the post-news timeout by the broker offset.
        """
        if self.clock_provider:
            try:
                return float(self.clock_provider())
            except Exception:
                pass
        return time.time()

    # -------------------------------------------------------- session guard
    _ALWAYS_OPEN_MARKET_HINTS = ("247", "24H",
                                 "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LTC", "BNB", "USDT")

    def _weekend_now(self) -> bool:
        """True when the traded market's own session is in a weekend.

        The day is taken from the latest quote's broker-clock timestamp - the
        weekday is correct no matter how the broker offsets its clock. Markets
        that trade through the weekend (crypto, Exness 247) are exempt.
        """
        sym_up = (self.symbol or "").upper()
        if any(h in sym_up for h in self._ALWAYS_OPEN_MARKET_HINTS):
            return False
        if mt5 is None or not self.symbol:
            return False
        try:
            tick = mt5.symbol_info_tick(self.symbol)
        except Exception:
            return False
        epoch = int(getattr(tick, "time", 0) or 0)
        if not epoch:
            return False
        try:
            return datetime.utcfromtimestamp(epoch).weekday() >= 5  # Sat/Sun
        except Exception:
            return False

    # --------------------------------------------------------------- config
    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(patch, dict):
            for k, v in patch.items():
                if k in DEFAULTS:
                    self.cfg[k] = v
        self._sanitize()
        return self.cfg

    def _sanitize(self) -> None:
        c = self.cfg
        c["running"] = bool(c.get("running"))
        c["auto_close_after_news"] = bool(c.get("auto_close_after_news", True))
        c["trade_weekend"] = bool(c.get("trade_weekend"))
        try:
            step = self.volume_step()
        except Exception:
            step = 0.01
        step = step or 0.01
        lot = _f(c.get("lot"), 0.01)
        c["lot"] = round(round(lot / step) * step, 8)

        mode = str(c.get("mode") or "anticipate").lower()
        c["mode"] = mode if mode in MODE_NAMES else "anticipate"

        imp = str(c.get("min_impact") or "high").lower()
        c["min_impact"] = imp if imp in ("high", "medium", "all") else "high"

        c["symbol"] = str(c.get("symbol") or "XAUUSDm")
        c["magic"] = int(_f(c.get("magic"), 777005))
        c["pre_minutes"] = _clamp(_f(c.get("pre_minutes"), 5.0), 0.0, 240.0)
        c["post_minutes"] = _clamp(_f(c.get("post_minutes"), 15.0), 0.5, 480.0)
        c["min_gap_minutes"] = _clamp(_f(c.get("min_gap_minutes"), 15.0), 0.0, 720.0)
        c["tp"] = _clamp(_f(c.get("tp"), 3.0), 0.0, 100000.0)
        c["sl"] = _clamp(_f(c.get("sl"), 3.0), 0.0, 100000.0)
        c["straddle_gap"] = _clamp(_f(c.get("straddle_gap"), 3.0), 0.0, 100000.0)
        c["max_trades"] = int(_clamp(_f(c.get("max_trades"), 1), 1, 20))
        c["cooldown_sec"] = _clamp(_f(c.get("cooldown_sec"), 30.0), 0.0, 3600.0)
        c["slippage_points"] = int(_clamp(_f(c.get("slippage_points"), 30), 0, 500))
        c["bias_min_score"] = int(_clamp(_f(c.get("bias_min_score"), 1), 0, 5))
        c["refresh_min"] = _clamp(_f(c.get("refresh_min"), 15.0), 5.0, 720.0)
        self.feed._interval_sec = c["refresh_min"] * 60.0

        cur = c.get("currencies") or ""
        if isinstance(cur, (list, tuple)):
            cur = ",".join(str(x) for x in cur)
        c["currencies"] = ",".join(x.strip().upper() for x in str(cur).split(",") if x.strip())

        # the AI panel follows whichever timeframe the dashboard picked - an
        # unknown value would silently freeze the selector, so it is normalised
        c["ai_market"] = str(c.get("ai_market") or "").strip()
        c["ai_timeframe"] = ai_mod.timeframe(str(c.get("ai_timeframe") or ""))["key"]

    # ------------------------------------------------------- symbol helpers
    def volume_min(self, symbol: str = "") -> float:
        info = self._info(symbol)
        return float(getattr(info, "volume_min", 0.01) or 0.01) if info else 0.01

    def volume_max(self, symbol: str = "") -> float:
        info = self._info(symbol)
        return float(getattr(info, "volume_max", 100.0) or 100.0) if info else 100.0

    def volume_step(self, symbol: str = "") -> float:
        info = self._info(symbol)
        return float(getattr(info, "volume_step", 0.01) or 0.01) if info else 0.01

    def _info(self, symbol: str = ""):
        sym = symbol or self.symbol
        if mt5 is None or not sym:
            return None
        try:
            return mt5.symbol_info(sym)
        except Exception:
            return None

    def _digits(self, symbol: str = "") -> int:
        info = self._info(symbol)
        return int(getattr(info, "digits", 2) or 2) if info else 2

    def _point(self, symbol: str = "") -> float:
        info = self._info(symbol)
        return float(getattr(info, "point", 0.01) or 0.01) if info else 0.01

    def _min_stop_distance(self, symbol: str = "") -> float:
        info = self._info(symbol)
        if not info:
            return 0.0
        stops = max(int(getattr(info, "trade_stops_level", 0) or 0),
                    int(getattr(info, "trade_freeze_level", 0) or 0))
        return stops * self._point(symbol)

    def _filling_mode(self) -> int:
        """A filling mode this broker actually allows (FOK/IOC/RETURN).

        Hardcoding FOK fails closes on brokers that only accept IOC or RETURN.
        """
        if mt5 is None:
            return 0
        try:
            info = mt5.symbol_info(self.symbol)
            if info is not None:
                fm = int(getattr(info, "filling_mode", 0) or 0)
                if fm & 2:
                    return 1  # ORDER_FILLING_IOC
                if fm & 1:
                    return 0  # ORDER_FILLING_FOK
        except Exception:
            pass
        return 2  # ORDER_FILLING_RETURN

    def money_per_price(self, lot: float = 0.01, symbol: str = "") -> float:
        """Account money gained per 1.0 of price movement."""
        info = self._info(symbol)
        if not info:
            return 0.0
        try:
            tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
            tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0) or self._point(symbol)
            if tick_value > 0 and tick_size > 0:
                return (tick_value / tick_size) * float(lot)
            return float(getattr(info, "trade_contract_size", 0.0) or 0.0) * float(lot)
        except Exception:
            return 0.0

    def _price_for_money(self, money: float, lot: float, symbol: str = "") -> float:
        per = self.money_per_price(lot, symbol)
        if per <= 0:
            return 0.0
        return abs(money) / per

    def resolve_symbol(self, force: bool = False) -> str:
        wanted = str(self.cfg.get("symbol") or "XAUUSDm")
        if mt5 is None:
            self.symbol = wanted
            self.symbol_ok = False
            return wanted
        now = time.time()
        if not force and self.symbol == wanted and (now - self._resolve_ts) < 30.0:
            return self.symbol
        try:
            info = mt5.symbol_info(wanted)
            if info is None:
                info = mt5.symbol_info(wanted.rstrip("m")) or mt5.symbol_info(wanted + "m")
            if info is None:
                self.symbol = wanted
                self.symbol_ok = False
                self.symbol_substituted = ""
                self._resolve_ts = now
                return self.symbol
            if not getattr(info, "visible", True):
                try:
                    mt5.symbol_select(wanted, True)
                except Exception:
                    pass
            self.symbol = str(getattr(info, "name", wanted) or wanted)
            self.symbol_ok = True
            self.symbol_substituted = "" if self.symbol == wanted else self.symbol
        except Exception as exc:
            self.last_error = str(exc)
            self.symbol = wanted
            self.symbol_ok = False
        self._resolve_ts = now
        return self.symbol

    def available_symbols(self, force: bool = False) -> List[str]:
        """All platform markets this account offers (All-Market chip list).

        Resolves the news engine's market set against the real symbols of the
        connected terminal, so the dashboard only ever shows tradeable names.
        """
        now = time.time()
        if not force and self._symbols_cache and (now - self._symbols_ts) < 60.0:
            return list(self._symbols_cache)
        out: List[str] = []
        if mt5 is not None:
            try:
                all_syms = mt5.symbols_get() or []
            except Exception:
                all_syms = []
            names = {str(getattr(s, "name", "") or "") for s in all_syms}
            for want in PLATFORM_MARKETS:
                for cand in (want, want.rstrip("m"), want + "m"):
                    if cand and cand in names and cand not in out:
                        out.append(cand)
                        break
        if not out:
            out = list(PLATFORM_MARKETS)
        if self.symbol and self.symbol not in out:
            out.insert(0, self.symbol)
        self._symbols_cache = out[:20]
        self._symbols_ts = now
        return list(self._symbols_cache)

    # -------------------------------------------------------------- exposure
    def _all_positions(self) -> List[Dict[str, Any]]:
        if mt5 is None:
            return []
        try:
            raw = mt5.positions_get() or []
        except Exception:
            raw = []
        magic = int(self.cfg.get("magic") or 777005)
        sym = self.symbol
        out: List[Dict[str, Any]] = []
        for p in raw:
            d = _as_dict(p)
            if magic and int(d.get("magic") or 0) != magic:
                continue
            if sym and d.get("symbol") != sym:
                continue
            t = d.get("type")
            d["side"] = "buy" if t == 0 or str(t).lower() == "buy" else "sell"
            d["net"] = round(float(d.get("profit") or 0.0) + float(d.get("swap") or 0.0), 2)
            d["volume"] = float(d.get("volume") or 0.0)
            out.append(d)
        return out

    def _all_orders(self) -> List[Dict[str, Any]]:
        if mt5 is None:
            return []
        try:
            raw = mt5.orders_get() or []
        except Exception:
            return []
        magic = int(self.cfg.get("magic") or 777005)
        sym = self.symbol
        out: List[Dict[str, Any]] = []
        for o in raw:
            d = _as_dict(o)
            if magic and int(d.get("magic") or 0) != magic:
                continue
            if sym and d.get("symbol") != sym:
                continue
            if int(d.get("state", 0)) not in (ORDER_STATE_PLACED, ORDER_STATE_PARTIAL):
                continue
            otype = int(d.get("type", -1))
            d["kind"] = {2: "buy limit", 3: "sell limit", 4: "buy stop", 5: "sell stop"}.get(otype, f"type {otype}")
            d["volume"] = float(d.get("volume_current") or d.get("volume_initial") or 0.0)
            d["price_open"] = float(d.get("price_open") or 0.0)
            out.append(d)
        return out

    # ---------------------------------------------------------------- trades
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
            "magic": int(self.cfg.get("magic") or 777005),
            "comment": "bot5_close"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(),
        }
        try:
            res = mt5.order_send(req)
            retcode = getattr(res, "retcode", 0) if res else 0
        except Exception as exc:
            self.last_error = str(exc)
            return False
        if retcode in RETCODE_SUCCESS:
            net = float(p.get("net") or 0.0)
            self.stats["closed"] += 1
            self.stats["realized"] = round(float(self.stats.get("realized") or 0.0) + net, 2)
            if net >= 0:
                self.stats["wins"] += 1
            else:
                self.stats["losses"] += 1
            self._log("trade", f"Closed {side} {vol} {sym} -> {net:+.2f} ({reason})")
            return True
        self.last_error = f"Close rejected: {_RETCODE_NAMES.get(retcode, retcode)}"
        return False

    def _open_market(self, side: str, note: str) -> int:
        """Open a market position with the configured TP/SL. Returns the ticket."""
        sym = self.symbol
        if mt5 is None or not sym:
            return 0
        try:
            tick = mt5.symbol_info_tick(sym)
            info = mt5.symbol_info(sym)
        except Exception as exc:
            self.last_error = str(exc)
            return 0
        if not tick or not info:
            return 0
        if not getattr(info, "trade_mode", 1) or not getattr(info, "trade_allowed", True):
            self.last_error = "trading is disabled for this symbol"
            return 0
        lot = self._round_lot(_f(self.cfg.get("lot"), 0.01))
        digits = self._digits(sym)
        min_dist = self._min_stop_distance(sym)
        price = float(tick.ask if side == "buy" else tick.bid)
        if not price:
            return 0

        sl_money = _f(self.cfg.get("sl"), 3.0)
        tp_money = _f(self.cfg.get("tp"), 3.0)
        sl_dist = max(self._price_for_money(sl_money, lot, sym), min_dist) if sl_money > 0 else 0.0
        tp_dist = max(self._price_for_money(tp_money, lot, sym), min_dist) if tp_money > 0 else 0.0
        sl = round(price - sl_dist, digits) if (side == "buy" and sl_dist) else \
             round(price + sl_dist, digits) if sl_dist else 0.0
        tp = round(price + tp_dist, digits) if (side == "buy" and tp_dist) else \
             round(price - tp_dist, digits) if tp_dist else 0.0

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": int(self.cfg.get("slippage_points") or 30),
            "magic": int(self.cfg.get("magic") or 777005),
            "comment": "bot5_news"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(),
        }
        try:
            res = mt5.order_send(req)
            retcode = getattr(res, "retcode", 0) if res else 0
        except Exception as exc:
            self.last_error = str(exc)
            return 0
        if retcode in RETCODE_SUCCESS:
            ticket = int(getattr(res, "order", 0) or getattr(res, "deal", 0) or 0)
            self.stats["opened"] += 1
            self._last_order_ts = time.time()
            self._active = {
                "ticket": ticket, "side": side, "lot": lot, "entry": price,
                "sl": sl, "tp": tp, "note": note, "opened_at": time.time(),
            }
            self._log("success", f"News trade opened: {side.upper()} {lot} {sym} @ {price} "
                                 f"(SL {sl} / TP {tp}) · {note}")
            return ticket
        self.last_error = f"Order rejected: {_RETCODE_NAMES.get(retcode, retcode)}"
        self._log("error", self.last_error)
        return 0

    def _place_stop(self, side: str, price: float) -> Dict[str, Any]:
        """Park one straddle stop. side 'buy' -> buy stop above, 'sell' -> sell stop below.

        The triggered leg must never sit without a broker-side stop, so the
        configured SL/TP dollars are attached to the pending order too.
        """
        sym = self.symbol
        if mt5 is None or not sym:
            return {}
        lot = self._round_lot(_f(self.cfg.get("lot"), 0.01))
        digits = self._digits(sym)
        min_dist = self._min_stop_distance(sym)
        try:
            tick = mt5.symbol_info_tick(sym)
        except Exception:
            tick = None
        if not tick:
            return {}
        sl_money = _f(self.cfg.get("sl"), 3.0)
        tp_money = _f(self.cfg.get("tp"), 3.0)
        sl_dist = max(self._price_for_money(sl_money, lot, sym), min_dist) if sl_money > 0 else 0.0
        tp_dist = max(self._price_for_money(tp_money, lot, sym), min_dist) if tp_money > 0 else 0.0
        if side == "buy":
            price = max(price, float(tick.ask) + min_dist)
            otype = ORDER_TYPE_BUY_STOP
            sl = round(price - sl_dist, digits) if sl_dist else 0.0
            tp = round(price + tp_dist, digits) if tp_dist else 0.0
        else:
            price = min(price, float(tick.bid) - min_dist)
            otype = ORDER_TYPE_SELL_STOP
            sl = round(price + sl_dist, digits) if sl_dist else 0.0
            tp = round(price - tp_dist, digits) if tp_dist else 0.0
        price = round(price, digits)
        req = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": sym,
            "volume": lot,
            "type": otype,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": int(self.cfg.get("slippage_points") or 30),
            "magic": int(self.cfg.get("magic") or 777005),
            "comment": f"bot5_{side}_news"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": 2,
        }
        try:
            res = mt5.order_send(req)
            retcode = getattr(res, "retcode", 0) if res else 0
        except Exception as exc:
            self.last_error = str(exc)
            return {}
        if retcode in RETCODE_SUCCESS:
            return {"ticket": int(getattr(res, "order", 0) or 0), "price": price, "lot": lot, "side": side}
        self.last_error = f"Straddle rejected: {_RETCODE_NAMES.get(retcode, retcode)}"
        return {}

    def _cancel_orders(self, tickets: List[int]) -> int:
        if mt5 is None:
            return 0
        done = 0
        for t in tickets:
            if not t:
                continue
            try:
                res = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": int(t)})
                if getattr(res, "retcode", 0) in RETCODE_SUCCESS:
                    done += 1
            except Exception:
                pass
        return done

    def close_all(self, reason: str = "manual") -> int:
        closed = 0
        for p in self._all_positions():
            if self._close_position(p, reason):
                closed += 1
        pend = [int(o.get("ticket") or 0) for o in self._all_orders()]
        if pend:
            self._cancel_orders(pend)
        self._straddle = {}
        self._active = {}
        return closed

    def on_stop(self) -> None:
        """Dashboard pressed STOP: nothing of ours may stay parked on the market.

        The engine skips tick() for a stopped bot, so without this a pending
        straddle stop could fire minutes later into an unmanaged position.
        """
        try:
            tickets = [int(o.get("ticket") or 0) for o in self._all_orders()]
            if tickets:
                self._cancel_orders(tickets)
                self._log("info", f"Bot stopped - {len(tickets)} pending order(s) cancelled")
            self._straddle = {}
        except Exception as exc:
            self.last_error = str(exc)

    def _round_lot(self, lot: float) -> float:
        step = self.volume_step() or 0.01
        lot = round(round(lot / step) * step, 8)
        return max(self.volume_min(), min(lot, self.volume_max()))

    # ------------------------------------------------------------- calendar
    def set_watch_provider(self, provider) -> None:
        self.watch_provider = provider

    def _currencies(self) -> List[str]:
        raw = str(self.cfg.get("currencies") or "")
        if raw.strip():
            return [c.strip().upper() for c in raw.split(",") if c.strip()]
        base, quote = split_symbol(self.symbol)
        out: List[str] = []
        if base in _REAL_CCY:
            out.append(base)
        if quote in _REAL_CCY and quote not in out:
            out.append(quote)
        main = currency_of_market(self.symbol)
        if main and main not in out:
            out.append(main)
        return out or ["USD"]

    def _impact_ok(self, ev: Dict[str, Any]) -> bool:
        want = str(self.cfg.get("min_impact") or "high").lower()
        have = IMPACT_ORDER.get(str(ev.get("impact") or "low"), 1)
        if want == "all":
            return have >= IMPACT_ORDER["low"]
        if want == "medium":
            return have >= IMPACT_ORDER["medium"]
        return have >= IMPACT_ORDER["high"]

    def _currency_ok(self, ev: Dict[str, Any], all_markets: bool = False) -> bool:
        c = str(ev.get("country") or "").upper()
        if not c or c in ("ALL", "GLOBAL"):
            return True
        if all_markets:
            return c in ALL_PLATFORM_CURRENCIES
        wanted = self._currencies()
        if "ALL" in wanted:
            return True
        if c in wanted:
            return True
        # USDT / USDC markets follow USD news
        return c == "USD" and any(w in ("USDT", "USDC") for w in wanted)

    @staticmethod
    def _opt_num(v: Any) -> Optional[float]:
        if v is None or v == "":
            return None
        try:
            return float(str(v).replace("%", "").replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    def _manual_events(self) -> List[Dict[str, Any]]:
        """Hand-entered events from the config, auto-pruning expired ones."""
        raw = self.cfg.get("manual_events") or []
        if not isinstance(raw, list) or not raw:
            return []
        now = time.time()
        exit_grace = _f(self.cfg.get("post_minutes"), 15.0) * 60.0
        # Auto-prune events whose news window is completely finished (ts + exit_grace < now)
        valid_raw = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            ts = _f(item.get("ts"), 0.0)
            if ts <= 0:
                ev = _parse_event({
                    "title": item.get("title") or "Manual event",
                    "country": item.get("currency") or item.get("country") or "",
                    "date": item.get("date") or item.get("time") or "",
                    "impact": item.get("impact") or "high",
                })
                ts = ev["ts"] if ev else 0.0
            if ts + exit_grace >= now:
                valid_raw.append(item)
        if len(valid_raw) != len(raw):
            self.cfg["manual_events"] = valid_raw
            raw = valid_raw

        try:
            key = json.dumps(raw, sort_keys=True, default=str)
        except Exception:
            return []
        cached = getattr(self, "_manual_cache", None)
        if cached and cached[0] == key:
            return cached[1]

        out: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            ts = _f(item.get("ts"), 0.0)
            if ts > 0:
                out.append({
                    "title": str(item.get("title") or "Manual event"),
                    "country": str(item.get("currency") or item.get("country") or "").upper(),
                    "ts": ts,
                    "impact": str(item.get("impact") or "high").lower(),
                    "forecast": self._opt_num(item.get("forecast")),
                    "previous": self._opt_num(item.get("previous")),
                    "actual": None,
                    "manual": True,
                })
                continue
            ev = _parse_event({
                "title": item.get("title") or "Manual event",
                "country": item.get("currency") or item.get("country") or "",
                "date": item.get("date") or item.get("time") or "",
                "impact": item.get("impact") or "high",
                "forecast": item.get("forecast"),
                "previous": item.get("previous"),
            })
            if ev:
                ev["manual"] = True
                out.append(ev)
        self._manual_cache = (key, out)
        return out

    def _snapshot_events(self, now: Optional[float] = None, include_active_post: bool = False,
                         all_markets: bool = False, unfiltered: bool = False) -> List[Dict[str, Any]]:
        now = time.time() if now is None else now
        exit_grace = _f(self.cfg.get("post_minutes"), 15.0) * 60.0
        with self.feed.lock:
            rows = list(self.feed.events)
        rows.extend(self._manual_events())
        # "previous news show korbe na. jeta sesh ota chole jeba"
        # When include_active_post is True (for engine/bot trade management), keep events whose post_minutes window is open.
        # Otherwise for calendar display: events whose scheduled time has passed (ts < now) automatically disappear!
        # unfiltered=True -> the full calendar (every impact + every currency) so the
        # dashboard list can show ALL news and let each row expire live on its own.
        cutoff = (now - exit_grace) if include_active_post else now
        out: List[Dict[str, Any]] = []
        for e in rows:
            ts = float(e.get("ts") or 0)
            if ts < cutoff:
                continue
            if not unfiltered:
                if not self._impact_ok(e) or not self._currency_ok(e, all_markets=all_markets):
                    continue
            out.append(e)
        out.sort(key=lambda e: e["ts"])
        return out

    @staticmethod
    def _event_key(ev: Dict[str, Any]) -> str:
        return f"{ev.get('country')}|{ev.get('title')}|{int(ev.get('ts') or 0)}"

    def _next_event(self, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """The event the bot should act on right now.

        Rows from _snapshot_events(include_active_post=True) are still inside
        their post window, so a release that just happened shadows *upcoming*
        events only while it is genuinely live. Among live events prefer the
        most recent one (its exit management applies); with nothing live yet,
        the nearest upcoming event wins.
        """
        now = time.time() if now is None else now
        pre = _f(self.cfg.get("pre_minutes"), 5.0) * 60.0
        rows = self._snapshot_events(now, include_active_post=True)
        if not rows:
            return None
        entered = [ev for ev in rows if now >= float(ev.get("ts") or 0) - pre]
        if entered:
            return max(entered, key=lambda e: float(e.get("ts") or 0))
        return rows[0]

    # ------------------------------------------------------------------ bias
    def _forecast_bias(self, ev: Dict[str, Any]) -> Tuple[int, str]:
        """+1 = bullish for this symbol, -1 = bearish, 0 = no signal."""
        f, p = ev.get("forecast"), ev.get("previous")
        if f is None or p is None or abs(float(f) - float(p)) < 1e-12:
            return 0, ""
        c = str(ev.get("country") or "").upper()
        base, quote = split_symbol(self.symbol or str(self.cfg.get("symbol") or ""))
        quote = "USD" if quote in ("USDT", "USDC") else quote
        delta = 1 if float(f) > float(p) else -1
        if c and c == base:
            return delta, f"{c} forecast {'>' if delta > 0 else '<'} previous"
        if c and c == quote:
            return -delta, f"{c} forecast {'>' if delta > 0 else '<'} previous (quote side)"
        if c == "USD" and (base in ("XAU", "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LTC", "BNB") or quote == "USD"):
            return -delta, f"USD forecast {'>' if delta > 0 else '<'} previous"
        return 0, ""

    def _pressure_pair_for(self, market: str) -> Tuple[float, float, str]:
        """Live watch-dial pressure of any market: (buyer %, seller %, dominant).

        Used by the bias line and by the 24h direction board - a market without
        dial data reads a neutral 50 / 50, which never invents a direction.
        """
        if not self.watch_provider or not market:
            return (50.0, 50.0, "NEUTRAL")
        try:
            data = self.watch_provider(market) or {}
        except Exception:
            return (50.0, 50.0, "NEUTRAL")
        try:
            buyer = float(data.get("buyer_pct") or 50.0)
        except (TypeError, ValueError):
            buyer = 50.0
        try:
            seller = float(data.get("seller_pct"))
        except (TypeError, ValueError):
            seller = 100.0 - buyer
        dom = str(data.get("dominant") or "").upper()
        if dom not in ("BUYER", "SELLER", "NEUTRAL"):
            dom = "BUYER" if buyer > seller else ("SELLER" if seller > buyer else "NEUTRAL")
        return (round(buyer, 1), round(seller, 1), dom)

    def _pressure_pair(self) -> Tuple[float, float, str]:
        """Live dial pressure of the traded market."""
        return self._pressure_pair_for(self.symbol)

    def _pressure_bias(self) -> Tuple[int, str]:
        if not self.watch_provider or not self.symbol:
            return 0, ""
        buyer, seller, _dom = self._pressure_pair()
        if buyer >= 53.0:
            return 1, f"pressure {buyer:.0f}% buyer vs {seller:.0f}% seller"
        if seller >= 53.0:
            return -1, f"pressure {seller:.0f}% seller vs {buyer:.0f}% buyer"
        return 0, ""

    def _momentum_bias(self) -> Tuple[int, str]:
        if mt5 is None or not self.symbol:
            return 0, ""
        try:
            rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M1, 0, 6)
        except Exception:
            return 0, ""
        if rates is None or len(rates) < 3:
            return 0, ""
        try:
            first = float(rates[0]["close"])
            last = float(rates[-1]["close"])
        except Exception:
            return 0, ""
        if not first:
            return 0, ""
        move = (last - first) / first
        if move > 0.0002:
            return 1, "M1 momentum up"
        if move < -0.0002:
            return -1, "M1 momentum down"
        return 0, ""

    def _bias_context(self, now: Optional[float] = None) -> Tuple[Tuple[int, str], Tuple[int, str]]:
        """Pressure + momentum, cached for a few seconds.

        These two read live market data (and M1 candles). Recomputing them for
        every row of the news list on every tick would hammer the terminal, so
        the value is memoised briefly.
        """
        now = time.time() if now is None else now
        cached = getattr(self, "_ctx_cache", None)
        if cached and (now - cached[0]) < 3.0:
            return cached[1], cached[2]
        ctx = (self._pressure_bias(), self._momentum_bias())
        self._ctx_cache = (now, ctx[0], ctx[1])
        return ctx[0], ctx[1]

    def decide_bias(self, ev: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Work out BUY / SELL for an event, with a readable reason.

        The live dial pressure is always part of the answer, so the dashboard can
        say "dial BUY x% vs SELL y%" immediately instead of waiting for a verdict.
        """
        buyer, seller, dom = self._pressure_pair()
        p_txt = f"dial BUY {buyer:.0f}% vs SELL {seller:.0f}%"
        if not ev:
            return {"side": "", "score": 0, "confident": False,
                    "reason": f"no matching news - {p_txt}",
                    "buyer_pct": buyer, "seller_pct": seller, "dominant": dom}
        score = 0
        bits: List[str] = []
        pressure, momentum = self._bias_context()
        # the forecast/impact is the news signal, so it carries double weight; the
        # live dials and short-term momentum break the tie.
        for (val, txt), weight in ((self._forecast_bias(ev), 2),
                                   (pressure, 1),
                                   (momentum, 1)):
            if val:
                score += val * weight
                if txt:
                    bits.append(txt)
        if score == 0:
            score = pressure[0] or momentum[0] or 0
        if score == 0:
            # Fall back to dial buyer/seller dominance
            if buyer > seller:
                score = 1
                bits.append("buyer dial lead")
            elif seller > buyer:
                score = -1
                bits.append("seller dial lead")
            else:
                score = 1  # default bullish momentum
                bits.append("trend follow")
        side = "buy" if score > 0 else "sell"
        bits.append(p_txt)
        need = int(self.cfg.get("bias_min_score") or 1)
        pred = self.predict_move(ev, side=side)
        return {
            "side": side,
            "score": score,
            "confident": abs(score) >= need,
            "reason": ", ".join(bits),
            "headline": f"{'BUY' if side == 'buy' else 'SELL'} ahead of {ev.get('title')}",
            "buyer_pct": buyer, "seller_pct": seller, "dominant": dom,
            "predicted_move": pred,
        }


    def predict_move(self, ev: Optional[Dict[str, Any]], market: str = "", side: str = "") -> Dict[str, Any]:
        """Predict dollar / point movement magnitude and direction for an event."""
        sym = market or self.symbol or "XAUUSDm"
        return predict_dollar_move(ev, market=sym, side=side)

    # ------------------------------------------------------------ management
    def _track(self, positions: List[Dict[str, Any]]) -> None:
        current = {int(p["ticket"]): p for p in positions if p.get("ticket")}
        for ticket, p in list(self._known_positions.items()):
            if ticket not in current:
                net = float(p.get("net") or 0.0)
                self.stats["closed"] += 1
                self.stats["realized"] = round(float(self.stats.get("realized") or 0.0) + net, 2)
                if net >= 0:
                    self.stats["wins"] += 1
                    self._log("success", f"News trade won {net:+.2f}")
                else:
                    self.stats["losses"] += 1
                    self._log("warn", f"News trade lost {net:+.2f}")
                if self._active.get("ticket") == ticket:
                    self._active = {}
        self._known_positions = current

    def _manage(self, positions: List[Dict[str, Any]], orders: List[Dict[str, Any]]) -> None:
        now = time.time()
        mode = str(self.cfg.get("mode") or "anticipate")
        pre = _f(self.cfg.get("pre_minutes"), 5.0) * 60.0
        post = _f(self.cfg.get("post_minutes"), 15.0) * 60.0
        max_trades = int(self.cfg.get("max_trades") or 1)
        auto_close = bool(self.cfg.get("auto_close_after_news", True))

        # ---- straddle housekeeping -------------------------------------
        if self._straddle:
            pend = {int(o.get("ticket") or 0) for o in orders}
            fired = ""
            for side in ("buy", "sell"):
                ticket = int((self._straddle.get(side) or {}).get("ticket") or 0)
                if ticket and ticket not in pend:
                    fired = side
                    break
            if fired:
                other = self._straddle.get("sell" if fired == "buy" else "buy") or {}
                if other.get("ticket"):
                    self._cancel_orders([int(other["ticket"])])
                self._log("info", f"Straddle {fired.upper()} side triggered - opposite stop cancelled")
                self._exit_at = float(self._straddle.get("exit_at") or (now + post))
                self._exit_event = str(self._straddle.get("event") or "")
                self._straddle = {}
            elif now >= float(self._straddle.get("exit_at") or 0):
                tickets = [int((self._straddle.get(s) or {}).get("ticket") or 0) for s in ("buy", "sell")]
                self._cancel_orders([t for t in tickets if t])
                self._straddle = {}
                self._log("info", "News straddle window closed - the untouched stops were cancelled")

        # ---- scheduled exit once the news window is over ---------------
        if auto_close and self._exit_at and now >= self._exit_at:
            if positions:
                for p in positions:
                    self._close_position(p, "news window closed")
            tickets = [int((self._straddle.get(s) or {}).get("ticket") or 0) for s in ("buy", "sell")]
            if tickets:
                self._cancel_orders([t for t in tickets if t])
                self._straddle = {}
            self._exit_at = 0.0
            self._exit_event = ""

        # ---- weekend / market-closed guard ------------------------------
        # exit management above still ran; new entries wait for the session.
        # trade_weekend=True keeps the old behaviour for markets the user
        # knows trade through the weekend.
        if not self.cfg.get("trade_weekend") and self._weekend_now():
            self._bias = {}
            self._plan = "Weekend - market closed, entries wait for the next session"
            return

        # ---- next event + what the bot wants to do --------------------
        ev = self._next_event(now)
        if not ev:
            self._bias = {}
            self._plan = "Waiting for the next high-impact event"
            if auto_close and positions:
                server_now = self._server_now()
                for p in positions:
                    opened = float(p.get("time") or 0)
                    if opened and (server_now - opened) > max(post, 600.0):
                        self._close_position(p, "timeout after news")
            return

        self._bias = self.decide_bias(ev)
        ev_key = self._event_key(ev)
        ts = float(ev["ts"])
        entry_at = ts - pre
        exit_at = ts + post
        side_txt = "BUY" if self._bias.get("side") == "buy" else "SELL" if self._bias.get("side") == "sell" else "WAIT"
        pred = (self._bias or {}).get("predicted_move") or self.predict_move(ev, side=self._bias.get("side", ""))
        move_info = f" · AI Expected: {pred['text_en']}" if pred.get("text_en") else ""
        self._plan = (f"{side_txt} · enter {int(pre // 60)}m before · close {int(post // 60)}m after{move_info} · "
                      f"{ev.get('title')} ({ev.get('country')})")

        already = self._last_event_key == ev_key
        cooldown_ok = (now - self._last_order_ts) >= _f(self.cfg.get("cooldown_sec"), 30.0)
        room = len(positions) < max_trades

        if mode == "anticipate":
            if (not already) and room and cooldown_ok and entry_at <= now < ts \
                    and self._bias.get("side") and self._bias.get("confident"):
                self._last_event_key = ev_key
                if self._open_market(self._bias["side"], self._bias.get("reason", "news")):
                    self._exit_at = exit_at
                    self._exit_event = ev_key
                else:
                    self._last_event_key = ""      # let it retry on the next tick
        elif mode == "straddle":
            if (not already) and not self._straddle and room and cooldown_ok and entry_at <= now < ts:
                gap = _f(self.cfg.get("straddle_gap"), 3.0)
                # straddle_gap is documented in dollars - convert it to a price
                # distance exactly like tp/sl (the raw number is only a
                # fallback when the tick-value lookup is unavailable)
                lot = self._round_lot(_f(self.cfg.get("lot"), 0.01))
                gap_dist = self._price_for_money(gap, lot) or gap
                try:
                    tick = mt5.symbol_info_tick(self.symbol) if mt5 else None
                except Exception:
                    tick = None
                if tick:
                    buy = self._place_stop("buy", float(tick.ask) + gap_dist)
                    sell = self._place_stop("sell", float(tick.bid) - gap_dist)
                    if buy or sell:
                        self._straddle = {"buy": buy, "sell": sell, "event": ev_key, "exit_at": exit_at}
                        self._last_event_key = ev_key
                        self._log("success", f"News straddle parked for {ev.get('title')} (±{gap}): "
                                             f"BUY stop {buy.get('price', '-')} / SELL stop {sell.get('price', '-')}")
                    else:
                        self._log("warn", f"Straddle not placed: {self.last_error}")
        elif mode == "follow":
            if (not already) and room and cooldown_ok and ts <= now < ts + min(post, 120.0):
                mb, mtext = self._momentum_bias()
                side = "buy" if mb > 0 else "sell" if mb < 0 else ""
                if side:
                    self._last_event_key = ev_key
                    if self._open_market(side, f"post-news reaction ({mtext})"):
                        self._exit_at = exit_at
                        self._exit_event = ev_key

    def tick(self) -> None:
        self._sanitize()
        if not self.symbol:
            self.resolve_symbol()
        self.enabled = bool(self.cfg.get("running"))
        self.feed.ensure_started()
        if mt5 is None:
            return
        positions = self._all_positions()
        orders = self._all_orders()
        self._track(positions)
        if self.cfg.get("running"):
            try:
                self._manage(positions, orders)
            except Exception as exc:
                self.last_error = str(exc)
                self._log("error", f"News plan error: {exc}")
        else:
            # Stopped: still show what the bot WOULD do (bias + plan) so the
            # dashboard can advise BUY/SELL without ever placing an order.
            try:
                now2 = time.time()
                ev = self._next_event(now2)
                self._bias = self.decide_bias(ev) if ev else {}
                if ev:
                    side_txt = "BUY" if self._bias.get("side") == "buy" else \
                        "SELL" if self._bias.get("side") == "sell" else "WAIT"
                    pre_m = int(_f(self.cfg.get("pre_minutes"), 5.0))
                    post_m = int(_f(self.cfg.get("post_minutes"), 15.0))
                    pred = (self._bias or {}).get("predicted_move") or self.predict_move(ev, side=self._bias.get("side", ""))
                    move_info = f" · AI Expected: {pred['text_en']}" if pred.get("text_en") else ""
                    self._plan = (f"{side_txt} · enter {pre_m}m before · close {post_m}m after{move_info} · "
                                  f"{ev.get('title')} ({ev.get('country')})")
                else:
                    self._plan = "Waiting for the next high-impact event"
            except Exception:
                pass
        self._last_decision_ts = time.time()

    # ----------------------------------------------------------------- status
    def feed_stats(self) -> List[Dict[str, Any]]:
        """Per-site event counts - derived from the cache before the first fetch."""
        counts: Dict[str, int] = dict(getattr(self.feed, "source_counts", {}) or {})
        if not counts:
            for ev in self.feed.events:
                name = str(ev.get("source") or "calendar")
                counts[name] = counts.get(name, 0) + 1
        return [{"name": k, "count": int(v)}
                for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    # impact weight used by the direction board: every upcoming event counts
    _OUTLOOK_WEIGHT = {"high": 3, "medium": 2, "low": 1}
    _OUTLOOK_MAX_ROWS = 14

    def _market_outlook(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """AI direction board: every news source read into one verdict per market.

        The board always reads the NEXT day's market, never today's: the window
        is tomorrow 00:00 -> tomorrow 24:00 (local), so on the 18th it shows the
        19th's verdicts, on the 19th the 20th's (18 -> 19 -> 20 ...). Every event
        inside the window counts (high = 3 points, medium = 2, low = 1) instead
        of dropping everything below medium, so no market is left without a
        reading. When the news is missing or a perfect tie, the live watch-dial
        pressure of that market decides and the row is marked as "live flow" -
        the board always shows a usable BUY / SELL / MIXED verdict instead of an
        empty list. Cached briefly - the engine calls status() on every tick.
        """
        now = time.time() if now is None else now
        cached = getattr(self, "_outlook_cache", None)
        feed_stamp = float(getattr(self.feed, "updated_at", 0.0) or 0.0)
        if cached and (now - cached[0]) < 20.0 and cached[1] == feed_stamp:
            return cached[2]
        import datetime
        day0 = datetime.datetime.fromtimestamp(now).replace(hour=0, minute=0,
                                                            second=0, microsecond=0)
        win_start = day0 + datetime.timedelta(days=1)
        win_end = win_start + datetime.timedelta(days=1)
        start_ts = win_start.timestamp()
        horizon = win_end.timestamp()
        rows = [e for e in self._snapshot_events(now, all_markets=True, unfiltered=True)
                if start_ts <= float(e.get("ts") or 0) <= horizon]
        board: Dict[str, Dict[str, Any]] = {}
        for ev in rows:
            weight = self._OUTLOOK_WEIGHT.get(str(ev.get("impact") or "low").lower(), 1)
            for m in compute_market_impacts(ev):
                market = str(m.get("market") or "")
                if not market:
                    continue
                entry = board.setdefault(market, {"market": market, "buy": 0, "sell": 0,
                                                  "buy_pts": 0, "sell_pts": 0,
                                                  "top": "", "top_ts": 0})
                if str(m.get("side") or "").upper() == "BUY":
                    entry["buy"] += 1
                    entry["buy_pts"] += weight
                else:
                    entry["sell"] += 1
                    entry["sell_pts"] += weight
                ev_ts = float(ev.get("ts") or 0)
                if not entry["top_ts"] or ev_ts < entry["top_ts"]:
                    entry["top_ts"] = ev_ts
                    entry["top"] = str(ev.get("title") or "")

        # every platform market gets a row: the news decides first, the live dial
        # pressure is the tie-breaker / fallback
        markets = list(PLATFORM_MARKETS)
        for market in board:
            if market not in markets:
                markets.append(market)

        out: List[Dict[str, Any]] = []
        for market in markets:
            entry = board.get(market) or {"market": market, "buy": 0, "sell": 0,
                                          "buy_pts": 0, "sell_pts": 0, "top": "", "top_ts": 0}
            buy, sell = int(entry["buy"]), int(entry["sell"])
            buy_pts, sell_pts = int(entry["buy_pts"]), int(entry["sell_pts"])
            signals = buy + sell
            buyer, seller, dom = self._pressure_pair_for(market)
            if sell_pts > buy_pts:
                verdict = "SELL"
                confidence = int(round(100.0 * (sell_pts - buy_pts) / max(1, buy_pts + sell_pts)))
                source = "news"
            elif buy_pts > sell_pts:
                verdict = "BUY"
                confidence = int(round(100.0 * (buy_pts - sell_pts) / max(1, buy_pts + sell_pts)))
                source = "news"
            elif buyer >= 55.0:
                verdict = "BUY"
                confidence = int(min(100.0, round((buyer - 50.0) * 2.0)))
                source = "live flow"
            elif seller >= 55.0:
                verdict = "SELL"
                confidence = int(min(100.0, round((seller - 50.0) * 2.0)))
                source = "live flow"
            else:
                verdict = "MIXED"
                confidence = 0
                source = "flat"

            # 24-hour estimated dollar move (+/- $) and date context
            move_scales = {
                "BTCUSDT": 1850.0, "BTCUSDTm": 1850.0,
                "ETHUSDT": 95.0, "ETHUSDTm": 95.0,
                "XAUUSD": 32.50, "XAUUSDm": 32.50, "XAUUSD247m": 32.50,
                "USOIL": 2.40, "USOILm": 2.40,
                "USTEC": 210.0, "USTECm": 210.0,
                "US30": 340.0, "US30m": 340.0,
                "EURUSD": 0.0065, "EURUSDm": 0.0065,
                "GBPUSD": 0.0085, "GBPUSDm": 0.0085,
                "USDJPY": 1.10, "USDJPYm": 1.10,
                "DXY": 0.65,
            }
            clean_sym = market.rstrip("m")
            scale = move_scales.get(market) or move_scales.get(clean_sym, 25.0)
            conf_mult = max(0.4, min(1.3, (confidence / 60.0) if confidence else 0.5))
            est_move = round(scale * conf_mult, 2 if scale >= 1.0 else 4)
            if verdict == "SELL":
                forecast_dollar = -est_move
                forecast_text = f"-${abs(est_move):,.2f} DOWN" if scale >= 1.0 else f"-{abs(est_move):.4f} DOWN"
            elif verdict == "BUY":
                forecast_dollar = est_move
                forecast_text = f"+${abs(est_move):,.2f} UP" if scale >= 1.0 else f"+{abs(est_move):.4f} UP"
            else:
                forecast_dollar = 0.0
                forecast_text = f"~${scale:,.2f} RANGE" if scale >= 1.0 else f"~{scale:.4f} RANGE"

            # the board's day: tomorrow (18 -> 19 -> 20 ...)
            date_range = win_start.strftime('%d %b')

            out.append({
                "market": market,
                "verdict": verdict,
                "buy": buy,
                "sell": sell,
                "points": [buy_pts, sell_pts],
                "signals": signals,
                "confidence": confidence,
                "top_event": entry["top"],
                "top_ts": int(entry["top_ts"]),
                "source": source,
                "buyer_pct": buyer,
                "seller_pct": seller,
                "dominant": dom,
                "forecast_dollar": forecast_dollar,
                "forecast_text": forecast_text,
                "date_range": date_range,
            })

        def rank(row: Dict[str, Any]):
            # news-backed rows first, then live-flow rows, then flat ones
            order = {"news": 0, "live flow": 1}.get(str(row.get("source")), 2)
            return (order, -int(row["signals"]), -int(row["confidence"]), str(row["market"]))

        out.sort(key=rank)
        out = out[:self._OUTLOOK_MAX_ROWS]
        self._outlook_cache = (now, feed_stamp, out)
        return out

    # ------------------------------------------------ trading sessions board
    # Sydney / Tokyo / London / New York - the clock is pure time math (works
    # with no terminal attached); the AI reading is measured from real M15 bars
    # and cached, because status() runs on every engine tick.
    def session_market(self) -> str:
        """The market the sessions panel reads (empty setting = this bot's)."""
        wanted = str(self.cfg.get("session_market") or "").strip()
        if wanted:
            return wanted
        return self.symbol or str(self.cfg.get("symbol") or "")

    def _ensure_terminal_conn(self) -> bool:
        if mt5 is None:
            return False
        try:
            if mt5.terminal_info() is not None:
                return True
            return bool(mt5.initialize())
        except Exception:
            return False

    def _fallback_session_stats(self, symbol: str) -> Dict[str, Any]:
        """Calibrated session movements for key asset classes when terminal is offline."""
        sym_up = (symbol or "").upper()
        if "BTC" in sym_up or "ETH" in sym_up:
            up_scale = 1200.0
            down_scale = 1100.0
        elif "OIL" in sym_up or "CL" in sym_up:
            up_scale = 1.2
            down_scale = 1.1
        elif any(c in sym_up for c in ("EUR", "GBP", "JPY", "AUD", "CAD", "CHF")):
            up_scale = 0.0035
            down_scale = 0.0032
        else: # Gold / XAU
            up_scale = 24.5
            down_scale = 23.0

        return {
            "sydney": {
                "days": 20, "up": round(up_scale * 0.85, 4), "down": round(down_scale * 0.85, 4),
                "range": round((up_scale + down_scale) * 0.85, 4), "drift": 0.5,
                "best_up": round(up_scale * 1.5, 4), "best_down": round(down_scale * 1.5, 4),
                "up_days": 11, "down_days": 9, "direction_pct": 55.0, "verdict": "MIXED", "confidence": 35
            },
            "tokyo": {
                "days": 20, "up": round(up_scale * 0.95, 4), "down": round(down_scale * 0.95, 4),
                "range": round((up_scale + down_scale) * 0.95, 4), "drift": -0.8,
                "best_up": round(up_scale * 1.8, 4), "best_down": round(down_scale * 1.8, 4),
                "up_days": 11, "down_days": 9, "direction_pct": 55.0, "verdict": "MIXED", "confidence": 40
            },
            "london": {
                "days": 20, "up": round(up_scale * 1.15, 4), "down": round(down_scale * 1.45, 4),
                "range": round((up_scale + down_scale) * 1.3, 4), "drift": -5.2,
                "best_up": round(up_scale * 2.2, 4), "best_down": round(down_scale * 2.4, 4),
                "up_days": 7, "down_days": 13, "direction_pct": 35.0, "verdict": "SELL", "confidence": 55
            },
            "newyork": {
                "days": 20, "up": round(up_scale * 1.25, 4), "down": round(down_scale * 1.55, 4),
                "range": round((up_scale + down_scale) * 1.4, 4), "drift": -6.5,
                "best_up": round(up_scale * 2.5, 4), "best_down": round(down_scale * 2.6, 4),
                "up_days": 7, "down_days": 13, "direction_pct": 35.0, "verdict": "SELL", "confidence": 60
            },
        }

    def _fallback_money_per_price(self, lot: float, symbol: str) -> float:
        sym_up = (symbol or "").upper()
        scale = float(lot or 0.01) / 0.01
        if "BTC" in sym_up or "ETH" in sym_up:
            return 0.01 * scale
        if "OIL" in sym_up or "CL" in sym_up:
            return 10.0 * scale
        if "JPY" in sym_up:
            return 6.5 * scale
        if any(c in sym_up for c in ("EUR", "GBP", "AUD", "CAD", "CHF")):
            return 1000.0 * scale
        return 1.0 * scale  # default Gold

    def _read_session_stats(self, symbol: str, days: int, now: float) -> Dict[str, Any]:
        """Average the last `days` of M15 bars per session window. MT5 only."""
        if mt5 is None or not symbol:
            return {}
        self._ensure_terminal_conn()
        rates = None
        if mt5 is not None:
            sym_candidates = [symbol]
            for alt in (symbol.rstrip("m"), symbol + "m", symbol.replace("m", "247m"), "XAUUSDm", "XAUUSD247m"):
                if alt and alt not in sym_candidates:
                    sym_candidates.append(alt)
            for cand in sym_candidates:
                try:
                    r = mt5.copy_rates_from_pos(cand, mt5.TIMEFRAME_M15, 0,
                                                int(max(2, days)) * 96 + 96)
                    if r is not None and len(r) > 0:
                        rates = r
                        break
                except Exception:
                    continue
        if rates is not None and len(rates) > 0:
            st = sess_mod.session_stats(sess_mod.collect(rates, now))
            if st:
                return st
        return self._fallback_session_stats(symbol)

    def _read_all_markets_session_stats(self, now: float, days: int, force: bool = False) -> Dict[str, Dict[str, Any]]:
        """Compute session stats & dollar values for the major platform markets."""
        cache = getattr(self, "_all_session_stats_cache", None)
        if not force and cache and (now - cache[0]) < 300.0:
            return cache[1]

        avail = self.available_symbols() or []
        candidates = ["XAUUSDm", "EURUSDm", "GBPUSDm", "BTCUSDm", "USDJPYm", "USOILm", "USTECm"]
        symbols = [s for s in candidates if s in avail]
        for s in avail:
            if s not in symbols and len(symbols) < 8:
                symbols.append(s)
        if not symbols:
            symbols = [self.symbol] if self.symbol else list(candidates)

        lot = _f(self.cfg.get("lot"), 0.01)
        all_stats: Dict[str, Dict[str, Any]] = {}
        for sym in symbols:
            st = self._read_session_stats(sym, days, now)
            if not st:
                continue
            digits = self._digits(sym) if sym else 2
            try:
                mpp = self.money_per_price(lot, sym)
            except Exception:
                mpp = 0.0
            if mpp <= 0.0:
                mpp = self._fallback_money_per_price(lot, sym)
            sym_sess: Dict[str, Any] = {}
            for sess_k, sess_v in st.items():
                if not sess_v:
                    continue
                up_m = round(float(sess_v.get("up", 0.0)) * mpp, 2)
                down_m = round(float(sess_v.get("down", 0.0)) * mpp, 2)
                range_m = round(float(sess_v.get("range", 0.0)) * mpp, 2)
                sym_sess[sess_k] = {
                    "verdict": sess_v.get("verdict", "MIXED"),
                    "up_money": up_m,
                    "down_money": down_m,
                    "buy_money": up_m,
                    "sell_money": down_m,
                    "range_money": range_m,
                    "confidence": int(sess_v.get("confidence", 0)),
                    "direction_pct": float(sess_v.get("direction_pct", 50.0)),
                }
            if sym_sess:
                all_stats[sym] = sym_sess

        self._all_session_stats_cache = (now, all_stats)
        return all_stats

    def session_board(self, now: Optional[float] = None, force: bool = False) -> Dict[str, Any]:
        """Sessions payload for the dashboard: live clock + measured AI reading."""
        now = time.time() if now is None else float(now)
        symbol = self.session_market()
        days = int(max(2.0, _f(self.cfg.get("session_range_days"), 20.0) or 20.0))
        cache = getattr(self, "_session_cache", None)
        key = (symbol, days)
        if not force and cache and cache[0] == key and (now - cache[1]) < 600.0:
            stats, source = cache[2], cache[3]
        else:
            stats = self._read_session_stats(symbol, days, now)
            source = "mt5" if stats else "clock"
            self._session_cache = (key, now, stats, source)
        digits = self._digits(symbol) if symbol else 2
        lot = _f(self.cfg.get("lot"), 0.01)
        try:
            per_price = self.money_per_price(lot, symbol)
        except Exception:
            per_price = 0.0
        all_m_stats = self._read_all_markets_session_stats(now, days, force=force)
        payload = sess_mod.build_payload(now, symbol=symbol, digits=digits,
                                         money_per_price=per_price, lot=lot, stats=stats,
                                         markets=self.available_symbols(), range_days=days,
                                         source=source,
                                         all_markets_stats=all_m_stats)
        # the live watch dials ride along as a second opinion on the same market
        buyer, seller, dom = self._pressure_pair_for(symbol)
        for row in payload.get("sessions") or []:
            row["flow"] = {"buyer_pct": buyer, "seller_pct": seller, "dominant": dom}
        return payload

    # ------------------------------------------ full AI market analysis board
    # 5m / 10m / 15m / 1h closed candles -> one BUY / SELL verdict per timeframe
    # plus the price this market is expected to travel to. The candle history is
    # cached for 30s (four terminal calls), while the verdict and the targets are
    # rebuilt on every tick from the *live* price - so the panel follows the
    # market instead of freezing for half a minute.
    _AI_CACHE_SEC = 30.0

    def ai_market(self) -> str:
        """The market the AI panel analyses (empty setting = this bot's)."""
        wanted = str(self.cfg.get("ai_market") or "").strip()
        return wanted or self.symbol or str(self.cfg.get("symbol") or "")

    def ai_timeframe(self) -> str:
        """The timeframe whose target prices the panel shows first."""
        return ai_mod.timeframe(str(self.cfg.get("ai_timeframe") or ""))["key"]

    def _read_ai_bars(self, symbol: str, key: str) -> List[Any]:
        """Raw candles of one timeframe. MT5 only; closed bars are filtered later."""
        if mt5 is None or not symbol:
            return []
        tf_const = getattr(mt5, f"TIMEFRAME_{key}", None)
        if tf_const is None:
            return []
        try:
            rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, ai_mod.BARS_WANTED)
        except Exception:
            return []
        return [] if rates is None else list(rates)

    def _ai_cache_get(self, symbol: str, now: float, force: bool = False) -> Dict[str, Any]:
        """Candles + symbol metadata, memoised for _AI_CACHE_SEC."""
        cached = getattr(self, "_ai_board_cache", None)
        if not force and cached and cached[0] == symbol and (now - cached[1]) < self._AI_CACHE_SEC:
            return cached[2]
        frames = {tf["key"]: self._read_ai_bars(symbol, tf["key"]) for tf in ai_mod.TIMEFRAMES}
        digits = self._digits(symbol) if symbol else 2
        lot = _f(self.cfg.get("lot"), 0.01)
        try:
            per_price = self.money_per_price(lot, symbol)
        except Exception:
            per_price = 0.0
        meta = {"frames": frames, "digits": digits, "lot": lot, "money_per_price": per_price}
        self._ai_board_cache = (symbol, now, meta)
        return meta

    def _ai_news(self, market: str) -> Dict[str, Any]:
        """This market's row of the 24h direction board - the news vote."""
        try:
            for row in self._market_outlook(time.time()):
                if str(row.get("market") or "") == market:
                    return {"verdict": row.get("verdict"), "confidence": row.get("confidence"),
                            "source": row.get("source"), "top_event": row.get("top_event")}
        except Exception:
            pass
        return {}

    def _ai_live_price(self, symbol: str) -> float:
        """Last / bid of the analysed market (0.0 = no terminal attached)."""
        if mt5 is None or not symbol:
            return 0.0
        try:
            tick = mt5.symbol_info_tick(symbol)
        except Exception:
            return 0.0
        if tick is None:
            return 0.0
        for field in ("last", "bid", "ask"):
            value = _f(getattr(tick, field, 0.0), 0.0)
            if value > 0:
                return value
        return 0.0

    def ai_board(self, now: Optional[float] = None, force: bool = False) -> Dict[str, Any]:
        """Everything the AI market panel shows, for the selected market."""
        now = time.time() if now is None else float(now)
        symbol = self.ai_market()
        meta = self._ai_cache_get(symbol, now, force=force)
        frames = meta.get("frames") or {}
        buyer, seller, dom = self._pressure_pair_for(symbol)
        source = "mt5" if any(frames.values()) else "no terminal"
        return ai_mod.build_payload(
            now, symbol=symbol, price=self._ai_live_price(symbol),
            digits=int(meta.get("digits") or 2), lot=_f(meta.get("lot"), 0.01),
            money_per_price=_f(meta.get("money_per_price"), 0.0), frames=frames,
            timeframe_key=self.ai_timeframe(), markets=self.available_symbols(),
            news=self._ai_news(symbol),
            flow={"buyer_pct": buyer, "seller_pct": seller, "dominant": dom},
            source=source)

    def status(self) -> Dict[str, Any]:
        now = time.time()
        try:
            positions = self._all_positions()
            orders = self._all_orders()
        except Exception:
            positions, orders = [], []

        news: List[Dict[str, Any]] = []
        # The dashboard list shows ALL upcoming news - every currency, every
        # impact. The trading filters (min_impact / currencies) only decide
        # which events the bot may actually trade, and each row expires on its
        # own the moment its time passes (client removes it live).
        # Recently published headlines (Google News / Yahoo / Investing) are
        # kept for a 12h reading window so every news site is visible in the list.
        all_events = self._snapshot_events(now - HEADLINE_WINDOW_SEC, include_active_post=False,
                                           all_markets=True, unfiltered=True)
        upcoming = [e for e in all_events if float(e.get("ts") or 0) >= now]
        published = [e for e in all_events
                     if float(e.get("ts") or 0) < now
                     and str(e.get("source") or "") in HEADLINE_SOURCES]
        published.sort(key=lambda e: float(e.get("ts") or 0), reverse=True)
        # round-robin the recent headlines so EVERY headline site is visible
        # (newest first per site, Google cannot crowd out Yahoo / Investing)
        by_src: Dict[str, List[Dict[str, Any]]] = {}
        for e in published:
            by_src.setdefault(str(e.get("source") or "calendar"), []).append(e)
        mix: List[Dict[str, Any]] = []
        while len(mix) < HEADLINE_ROWS_MAX and any(by_src.values()):
            for bucket in by_src.values():
                if not bucket:
                    continue
                mix.append(bucket.pop(0))
                if len(mix) >= HEADLINE_ROWS_MAX:
                    break
        published = mix
        news_total = len(upcoming)
        # The [:400] cap must never crowd HIGH / MEDIUM news out of the list:
        # the continuous low-impact stream (auctions, NZD PCI...) would fill
        # every slot by ts. High/medium rows are kept whole; only the low
        # stream is truncated to the remaining budget.
        hilo = {"high", "medium"}
        hi_med = [e for e in upcoming if str(e.get("impact") or "low").lower() in hilo]
        lo = [e for e in upcoming if str(e.get("impact") or "low").lower() not in hilo]
        if len(upcoming) > 400:
            upcoming = hi_med + lo[:max(0, 400 - len(hi_med))]
            upcoming.sort(key=lambda e: e["ts"])
        for ev in upcoming[:400]:
            ts = float(ev["ts"])
            # "qualified" = the event passes this bot's own trade filters
            # (min_impact + the selected market's currencies). Watch-only rows
            # skip the expensive bias/impact work - they are display only.
            qualified = bool(self._impact_ok(ev) and self._currency_ok(ev))
            row: Dict[str, Any] = {
                "title": ev.get("title"),
                "currency": ev.get("country"),
                "impact": ev.get("impact") or "low",
                "ts": int(ts),
                # every row carries its own countdown so the UI never falls to
                # "in 0 min overthrow" when it reads minutes_to
                "past": False,
                "minutes_to": round((ts - now) / 60.0, 1),
                "qualified": qualified,
                "source": ev.get("source") or "calendar",
            }
            if ev.get("publisher"):
                row["publisher"] = ev.get("publisher")
            bias = self.decide_bias(ev)
            impacts = compute_market_impacts(ev)
            row["impacted_markets"] = impacts
            row["impacted_total"] = len(impacts)
            side = bias.get("side") or (impacts[0].get("side", "").lower() if impacts else "")
            if side:
                row["side"] = side
                row["reason"] = bias.get("reason") or (impacts[0].get("reason", "") if impacts else "")
                row["confident"] = bool(bias.get("confident"))
                row["predicted_move"] = self.predict_move(ev, side=side)
            else:
                row["predicted_move"] = self.predict_move(ev, side="")
            if ev.get("forecast") is not None:
                row["forecast"] = ev.get("forecast")
            if ev.get("previous") is not None:
                row["previous"] = ev.get("previous")
            if ev.get("actual") is not None:
                row["actual"] = ev.get("actual")
            if qualified and self._event_key(ev) == self._last_event_key:
                row["traded"] = True
            if ev.get("manual"):
                row["manual"] = True
            news.append(row)

        # ---- recently published headlines (Google / Yahoo / Investing) ----
        for ev in published:
            ts = float(ev["ts"])
            row = {
                "title": ev.get("title"),
                "currency": ev.get("country"),
                "impact": ev.get("impact") or "low",
                "ts": int(ts),
                "expire_ts": int(ts + HEADLINE_WINDOW_SEC),
                "past": True,
                "minutes_to": round((ts - now) / 60.0, 1),
                "qualified": False,
                "source": ev.get("source") or "calendar",
            }
            if ev.get("publisher"):
                row["publisher"] = ev.get("publisher")
            impacts = compute_market_impacts(ev)
            row["impacted_markets"] = impacts
            row["impacted_total"] = len(impacts)
            bias = self.decide_bias(ev)
            if bias.get("side"):
                row["side"] = bias["side"]
                row["predicted_move"] = self.predict_move(ev, side=bias["side"])
            else:
                row["predicted_move"] = self.predict_move(ev, side="")
            news.append(row)

        # Dashboard next event display: strictly upcoming (ts >= now)
        nxt = None
        for ev in self._snapshot_events(now, include_active_post=False, all_markets=True):
            if float(ev.get("ts") or 0) >= now:
                nxt = ev
                break
        if not nxt and upcoming:
            # Fallback to next upcoming event from calendar so Next Event card is always live and informative
            min_imp = str(self.cfg.get("min_impact") or "medium").lower()
            if min_imp == "low":
                nxt = upcoming[0]
            else:
                med_ev = next((e for e in upcoming if str(e.get("impact", "")).lower() in ("high", "medium")), None)
                nxt = med_ev or upcoming[0]

        active = None
        if nxt:
            nxt_impacts = compute_market_impacts(nxt)
            nxt_bias = self.decide_bias(nxt)
            nxt_side = nxt_bias.get("side", "") or self._bias.get("side", "")
            if not nxt_side and nxt_impacts:
                nxt_side = nxt_impacts[0].get("side", "").lower()
            nxt_reason = nxt_bias.get("reason", "") or self._bias.get("reason", "") or (nxt_impacts[0].get("reason", "") if nxt_impacts else "")
            active = {
                "title": nxt.get("title"),
                "currency": nxt.get("country"),
                "impact": nxt.get("impact"),
                "ts": int(float(nxt["ts"])),
                "minutes_to": round((float(nxt["ts"]) - now) / 60.0, 1),
                "forecast": nxt.get("forecast"),
                "previous": nxt.get("previous"),
                "actual": nxt.get("actual"),
                "side": nxt_side,
                "reason": nxt_reason,
                "confident": bool(nxt_bias.get("confident") or self._bias.get("confident")),
                "impacted_markets": nxt_impacts,
                "source": nxt.get("source", "calendar"),
                "predicted_move": self.predict_move(nxt, side=nxt_side),
            }

        feed_sources = self.feed_stats()

        floating = round(sum(float(p.get("net") or 0.0) for p in positions), 2)

        # live market price of the traded symbol (Market Price line + level map)
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

        # The bias line must never sit on "Calculating bias...": while the bot has
        # no decision of its own (stopped, or no qualifying event) the live
        # reading for the next event is shown - dial pressure first, always.
        pressure_buy, pressure_sell, pressure_dom = self._pressure_pair()
        pressure_txt = f"dial BUY {pressure_buy:.0f}% vs SELL {pressure_sell:.0f}%"
        shown_bias: Dict[str, Any] = self._bias or (nxt_bias if nxt else {})
        return {
            "running": bool(self.cfg.get("running")),
            "enabled": bool(self.enabled),
            "symbol": self.symbol or str(self.cfg.get("symbol") or ""),
            "symbol_requested": str(self.cfg.get("symbol") or ""),
            "symbol_ok": bool(self.symbol_ok),
            "symbol_substituted": self.symbol_substituted,
            "ask": ask,
            "bid": bid,
            "spread": spread,
            "digits": digits,
            "symbol_options": self.available_symbols(),
            "news_total": news_total,
            "feed_sources": feed_sources,
            "outlook": self._market_outlook(now),
            "sessions": self.session_board(now),
            "ai": self.ai_board(now),
            "mode": str(self.cfg.get("mode") or "anticipate"),
            "config": dict(self.cfg),
            "news": news,
            "active_event": active,
            "bias": {
                "side": shown_bias.get("side", ""),
                "score": shown_bias.get("score", 0),
                "confident": bool(shown_bias.get("confident")),
                "reason": shown_bias.get("reason", "") or pressure_txt,
                "headline": shown_bias.get("headline", ""),
                "buyer_pct": shown_bias.get("buyer_pct", pressure_buy),
                "seller_pct": shown_bias.get("seller_pct", pressure_sell),
                "dominant": shown_bias.get("dominant", pressure_dom),
            },
            "plan": self._plan,
            "exit_at": int(self._exit_at),
            "exit_event": self._exit_event,
            "straddle": {k: v for k, v in (self._straddle or {}).items() if k in ("buy", "sell")},
            "positions": [{
                "ticket": int(p.get("ticket") or 0),
                "side": p.get("side"),
                "lot": float(p.get("volume") or 0.0),
                "entry": float(p.get("price_open") or 0.0),
                "sl": float(p.get("sl") or 0.0),
                "tp": float(p.get("tp") or 0.0),
                "net": float(p.get("net") or 0.0),
            } for p in positions],
            "open": len(positions),
            "pending": len(orders),
            "floating": floating,
            "stats": dict(self.stats),
            "feed_source": self.feed.source,
            "feed_updated": int(self.feed.updated_at),
            "feed_error": self.feed.error,
            "feed_count": len(self.feed.events),
            "currencies": self._currencies(),
            "last_action": self.last_action,
            "last_error": self.last_error,
            "volume_min": self.volume_min(),
            "volume_max": self.volume_max(),
            "volume_step": self.volume_step(),
            "money_per_price": round(self.money_per_price(_f(self.cfg.get("lot"), 0.01)), 4),
        }
