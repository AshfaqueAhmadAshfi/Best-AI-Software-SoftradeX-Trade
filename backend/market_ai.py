"""Full AI market analysis: 5m / 10m / 15m / 1h → one BUY / SELL verdict and
the price the market is expected to travel to (up and down).

Why this module exists
---------------------
The dashboard already reads the news (Bot 5 outlook) and the live order-flow
dials, and it measures how far each trading session usually travels. What it
could not answer was the plainest question of all: *on which timeframe do I buy
or sell this market right now, and where does the price go?* This module is
that answer:

1. Every timeframe is measured on its own closed candles - no repainting, the
   in-progress candle is always dropped - with four independent readings:
       - trend     : EMA(9) vs EMA(21) and where the price sits against them
       - momentum  : the move of the last 5 closed candles against the ATR
       - structure : a break of the 20-candle swing high / swing low
       - RSI(14)   : confirming, not leading
   Each reading is -1 / 0 / +1, so a timeframe scores -4 … +4 and turns into
   BUY (score >= +2), SELL (score <= -2) or NEUTRAL.

2. The timeframe verdicts are then voted together with their timeframe weights
   (5m < 10m < 15m < 1h) plus the news bias and the live watch-dial pressure as
   tie-breakers. A side needs real support to be called: either the 1-hour
   timeframe on its own, or at least two faster timeframes leaning the same way
   (``_MIN_SIDE_WEIGHT``) - this is what stops the 5m timeframe from flipping a
   clean 1h trend.

3. The price targets. Expected travel = ATR(14) * ``_MOVE_MULT`` of the chosen
   timeframe, which is the honest "how far does this timeframe move when it
   moves". ``up_price`` / ``down_price`` are that projection from the live
   price, ``level_up`` / ``level_down`` are the real swing levels the price
   would have to clear first, and the money fields turn the distance into
   account currency for the given lot.

Everything here is pure math on plain OHLC rows, so it is testable without a
terminal attached (see tests/test_market_ai.py).

Run the tests:  python -m unittest tests.test_market_ai
"""
from __future__ import annotations

from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Sequence

# The six timeframes of the panel, lightest first: 1m, 5m, 15m, 1h, 4h, 1Day.
# ``weight`` is how much a timeframe's vote counts.
TIMEFRAMES: List[Dict[str, Any]] = [
    {"key": "M1", "minutes": 1, "weight": 0.8},
    {"key": "M5", "minutes": 5, "weight": 1.0},
    {"key": "M15", "minutes": 15, "weight": 1.5},
    {"key": "H1", "minutes": 60, "weight": 2.0},
    {"key": "H4", "minutes": 240, "weight": 2.5},
    {"key": "D1", "minutes": 1440, "weight": 3.0},
]
TIMEFRAME_KEYS = tuple(tf["key"] for tf in TIMEFRAMES)
DEFAULT_TIMEFRAME = "M15"

BARS_WANTED = 140        # closed candles requested per timeframe
MIN_BARS = 40            # below this a timeframe has no reading at all
_EMA_FAST, _EMA_SLOW = 9, 21
_RSI_N, _ATR_N = 14, 14
_MOMENTUM_BARS = 5       # candles used for the momentum reading
_SWING = 20              # candles used for the swing high / low (structure)
_ATR_MOMENTUM = 0.25     # momentum must beat this share of the ATR to vote
_EMA_TOL = 0.05          # EMAs must be this far apart (share of the ATR) to trend
_RSI_BULL, _RSI_BEAR = 58.0, 42.0
_SIDE_SCORE = 2          # score needed before a timeframe calls BUY / SELL
_MOVE_MULT = 2.0         # expected travel = ATR * this
_MIN_SIDE_WEIGHT = 2.0   # weight a side needs to be called
_MIN_ALIGN = 0.15        # below this the timeframes are simply pulling apart

NEUTRAL = "NEUTRAL"


# --------------------------------------------------------------------- helpers
def timeframe(key: str) -> Dict[str, Any]:
    """The timeframe record for a key (falls back to the default)."""
    want = str(key or "").strip().upper()
    for tf in TIMEFRAMES:
        if tf["key"] == want:
            return dict(tf)
    return dict(next(tf for tf in TIMEFRAMES if tf["key"] == DEFAULT_TIMEFRAME))


def parse_bars(bars: Optional[Iterable[Any]]) -> List[Sequence[float]]:
    """Normalise any OHLC rows into (ts, open, high, low, close), oldest first.

    Accepts MT5 rate rows, dicts and plain tuples, exactly like the sessions
    module, so the same data can be fed from the terminal or from a test.
    """
    out: List[Sequence[float]] = []
    for bar in bars or []:
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
        if ts <= 0:
            continue
        out.append((ts, o, h, l, c))
    out.sort(key=lambda b: b[0])
    return out


def closed_bars(bars: Optional[Iterable[Any]], minutes: float, now: float) -> List[Sequence[float]]:
    """Only fully closed candles - the in-progress one is dropped.

    MT5 hands back the forming candle as the last row; reading it would make
    every verdict flicker. A candle is closed when its open time plus its own
    length is already in the past.
    """
    span = max(1.0, float(minutes or 1)) * 60.0
    out: List[Sequence[float]] = []
    for bar in parse_bars(bars):
        if float(bar[0]) + span <= float(now):
            out.append(bar)
    return out


def _ema(values: Sequence[float], n: int) -> float:
    if not values:
        return 0.0
    if len(values) < n or n <= 1:
        return float(mean(values))
    k = 2.0 / (n + 1.0)
    ema = float(mean(values[:n]))
    for v in values[n:]:
        ema = float(v) * k + ema * (1.0 - k)
    return ema


def _rsi(closes: Sequence[float], n: int = _RSI_N) -> float:
    """Wilder's RSI, 0-100 (50 when there is not enough history)."""
    if len(closes) < n + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = float(closes[i]) - float(closes[i - 1])
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_gain, avg_loss = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = float(closes[i]) - float(closes[i - 1])
        avg_gain = (avg_gain * (n - 1) + max(d, 0.0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-d, 0.0)) / n
    if avg_loss <= 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
         n: int = _ATR_N) -> float:
    """Wilder's ATR - the average distance the price covers per candle."""
    if len(closes) < n + 1:
        return 0.0
    trs: List[float] = []
    for i in range(1, len(closes)):
        trs.append(max(float(highs[i]) - float(lows[i]),
                       abs(float(highs[i]) - float(closes[i - 1])),
                       abs(float(lows[i]) - float(closes[i - 1]))))
    atr = float(mean(trs[:n]))
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------- analysis
def analyze(bars: Optional[Iterable[Any]], price: float = 0.0, digits: int = 2,
            key: str = DEFAULT_TIMEFRAME, move_mult: float = _MOVE_MULT) -> Dict[str, Any]:
    """One timeframe read: verdict, confidence, targets and the reasons why."""
    tf = timeframe(key)
    rows = parse_bars(bars)
    count = len(rows)
    px = _f(price, 0.0)
    if px <= 0 and rows:
        px = _f(rows[-1][4], 0.0)

    row: Dict[str, Any] = {
        "key": tf["key"],
        "minutes": int(tf["minutes"]),
        "weight": float(tf["weight"]),
        "bars": count,
        "ready": False,
        "verdict": NEUTRAL,
        "score": 0,
        "confidence": 0,
        "strength": "none",
        "price": round(px, digits),
        "close": round(_f(rows[-1][4], 0.0), digits) if rows else 0.0,
        "rsi": 0.0,
        "ema_fast": 0.0,
        "ema_slow": 0.0,
        "atr": 0.0,
        "momentum": 0.0,
        "swing_high": 0.0,
        "swing_low": 0.0,
        "up_price": 0.0, "down_price": 0.0,
        "up_distance": 0.0, "down_distance": 0.0,
        "level_up": 0.0, "level_down": 0.0,
        "reasons": [],
    }
    if count < MIN_BARS:
        row["reasons"] = ["not enough closed candles"]
        return row

    ts = [float(b[0]) for b in rows]
    highs = [float(b[2]) for b in rows]
    lows = [float(b[3]) for b in rows]
    closes = [float(b[4]) for b in rows]
    last = closes[-1]

    ema_fast = _ema(closes, _EMA_FAST)
    ema_slow = _ema(closes, _EMA_SLOW)
    rsi = _rsi(closes)
    atr = _atr(highs, lows, closes)
    # swing levels exclude the newest candle - a level the price is already
    # breaking must not be compared with itself
    swing_high = max(highs[-(_SWING + 1):-1]) if count > _SWING else max(highs[:-1])
    swing_low = min(lows[-(_SWING + 1):-1]) if count > _SWING else min(lows[:-1])
    idx = max(0, count - 1 - _MOMENTUM_BARS)
    momentum = last - closes[idx]

    score = 0
    reasons: List[str] = []

    # a market that is simply standing still must not read as a micro-trend, so
    # the two averages have to be visibly apart (a share of the ATR) before the
    # cross counts
    sep = ema_fast - ema_slow
    tol = max(atr * _EMA_TOL, abs(last) * 1e-9, 1e-9)
    if sep > tol and last > ema_slow:
        score += 1
        reasons.append(f"trend up (EMA{_EMA_FAST} > EMA{_EMA_SLOW})")
    elif -sep > tol and last < ema_slow:
        score -= 1
        reasons.append(f"trend down (EMA{_EMA_FAST} < EMA{_EMA_SLOW})")
    else:
        reasons.append("price inside the EMAs (no trend)")

    thr = atr * _ATR_MOMENTUM
    if thr > 0 and momentum > thr:
        score += 1
        reasons.append(f"momentum +{momentum:.{digits}f} in {_MOMENTUM_BARS} candles")
    elif thr > 0 and momentum < -thr:
        score -= 1
        reasons.append(f"momentum -{abs(momentum):.{digits}f} in {_MOMENTUM_BARS} candles")
    else:
        reasons.append("momentum flat")

    if last > swing_high:
        score += 1
        reasons.append(f"broke the {_SWING}-candle high {swing_high:.{digits}f}")
    elif last < swing_low:
        score -= 1
        reasons.append(f"broke the {_SWING}-candle low {swing_low:.{digits}f}")
    else:
        reasons.append(f"inside the {_SWING}-candle range")

    if rsi >= _RSI_BULL:
        score += 1
        reasons.append(f"RSI {rsi:.0f} bullish")
    elif rsi <= _RSI_BEAR:
        score -= 1
        reasons.append(f"RSI {rsi:.0f} bearish")
    else:
        reasons.append(f"RSI {rsi:.0f} neutral")

    verdict = "BUY" if score >= _SIDE_SCORE else "SELL" if score <= -_SIDE_SCORE else NEUTRAL
    strength = "strong" if abs(score) >= 3 else "medium" if abs(score) == 2 else "weak"

    # ---- the traffic light: how far this timeframe travels when it moves ----
    move = atr * max(0.1, _f(move_mult, _MOVE_MULT))
    up_price = px + move
    down_price = px - move
    level_up = swing_high if swing_high > px else up_price
    level_down = swing_low if 0 < swing_low < px else down_price

    row.update({
        "ready": True,
        "verdict": verdict,
        "score": int(score),
        "confidence": int(min(100, round(100.0 * abs(score) / (_SIDE_SCORE * 2)))),
        "strength": strength,
        "price": round(px, digits),
        "close": round(last, digits),
        "rsi": round(rsi, 1),
        "ema_fast": round(ema_fast, digits),
        "ema_slow": round(ema_slow, digits),
        "atr": round(atr, digits),
        "momentum": round(momentum, digits),
        "swing_high": round(swing_high, digits),
        "swing_low": round(swing_low, digits),
        "up_price": round(up_price, digits),
        "down_price": round(down_price, digits),
        "up_distance": round(up_price - px, digits),
        "down_distance": round(px - down_price, digits),
        "level_up": round(level_up, digits),
        "level_down": round(level_down, digits),
        "reasons": reasons,
    })
    return row


def combine(rows: Sequence[Dict[str, Any]], news: Optional[Dict[str, Any]] = None,
            flow: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Vote the timeframe reads (plus news and dials) into one verdict.

    Weights decide the winner, but a side is only called when it has real
    support (see ``_MIN_SIDE_WEIGHT``). Confidence blends how *aligned* the
    timeframes are (do they pull the same way?) with how much of the board is
    actually voting (one timeframe alone can never be a confident call), and
    the news bias / dial pressure can nudge it up when they agree.
    """
    bull = bear = 0.0
    bull_w = bear_w = 0.0
    reasons: List[str] = []
    voters = 0
    total_weight = 0.0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        weight = _f(row.get("weight"), 1.0)
        total_weight += weight
        score = _f(row.get("score"), 0.0)
        if score > 0:
            bull += weight * score
            bull_w += weight
            voters += 1
        elif score < 0:
            bear += weight * abs(score)
            bear_w += weight
            voters += 1

    total = bull + bear
    news_side = str((news or {}).get("verdict") or "").upper()
    news_conf = _f((news or {}).get("confidence"), 0.0)
    flow_side = str((flow or {}).get("dominant") or "").upper()
    buyer = _f((flow or {}).get("buyer_pct"), 50.0)

    if total <= 0:
        # nothing at all on the chart - the news and the dials still get a say
        if news_side in ("BUY", "SELL") and news_conf >= 50:
            verdict = news_side
            confidence = int(min(60, round(news_conf * 0.6)))
            reasons.append(f"news bias {news_side} {news_conf:.0f}%")
        elif flow_side in ("BUYER", "SELLER"):
            verdict = "BUY" if flow_side == "BUYER" else "SELL"
            confidence = int(min(50, round(abs(buyer - 50.0) * 2.0)))
            reasons.append(f"live dial pressure {flow_side} {buyer:.0f}%")
        else:
            verdict = NEUTRAL
            confidence = 0
            reasons.append("all timeframes flat")
        return {"verdict": verdict, "confidence": confidence, "score": 0.0,
                "buy_score": round(bull, 2), "sell_score": round(bear, 2),
                "bull_weight": round(bull_w, 2), "sell_weight": round(bear_w, 2),
                "votes": voters, "align": 0.0, "reasons": reasons}

    side = "BUY" if bull > bear else "SELL" if bear > bull else NEUTRAL
    side_w = bull_w if side == "BUY" else bear_w
    align = abs(bull - bear) / total
    participation = (side_w / total_weight) if total_weight > 0 else 0.0
    reasons.append(" · ".join(f"{r['key']} {r['verdict']}" for r in rows or []
                              if isinstance(r, dict) and r.get("verdict") != NEUTRAL))

    confidence = 100.0 * align * participation
    if side == "BUY" and news_side == "BUY":
        confidence += min(10.0, news_conf * 0.1)
        reasons.append(f"news agrees (BUY {news_conf:.0f}%)")
    elif side == "SELL" and news_side == "SELL":
        confidence += min(10.0, news_conf * 0.1)
        reasons.append(f"news agrees (SELL {news_conf:.0f}%)")
    if side == "BUY" and flow_side == "BUYER":
        confidence += min(5.0, max(0.0, (buyer - 50.0) * 0.2))
        reasons.append(f"dials agree (buyers {buyer:.0f}%)")
    elif side == "SELL" and flow_side == "SELLER":
        confidence += min(5.0, max(0.0, (50.0 - buyer) * 0.2))
        reasons.append(f"dials agree (sellers {100.0 - buyer:.0f}%)")

    if side_w < _MIN_SIDE_WEIGHT:
        verdict = NEUTRAL
        confidence = min(confidence, 20.0)
        reasons.append("only one fast timeframe leans this way - waiting for a second")
    elif align < _MIN_ALIGN:
        verdict = NEUTRAL
        confidence = min(confidence, 20.0)
        reasons.append("timeframes are pulling apart")
    else:
        verdict = side

    return {
        "verdict": verdict,
        "confidence": int(max(0, min(100, round(confidence)))),
        "score": round(bull - bear, 2),
        "buy_score": round(bull, 2),
        "sell_score": round(bear, 2),
        "bull_weight": round(bull_w, 2),
        "sell_weight": round(bear_w, 2),
        "votes": voters,
        "align": round(align, 3),
        "side_weight": round(side_w, 2),
        "reasons": [r for r in reasons if r],
    }


def _row_for(rows: Sequence[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    for row in rows or []:
        if isinstance(row, dict) and row.get("key") == key and row.get("ready"):
            return row
    for row in rows or []:
        if isinstance(row, dict) and row.get("ready"):
            return row
    return None


def targets(row: Optional[Dict[str, Any]], verdict: str, price: float = 0.0,
            digits: int = 2, money_per_price: float = 0.0, lot: float = 0.01,
            timeframe_key: str = DEFAULT_TIMEFRAME) -> Dict[str, Any]:
    """The price the market is expected to reach - up and down - in one block.

    ``primary_*`` is the leg the verdict actually trades (up for BUY, down for
    SELL), ``risk_*`` is the other side of the same measured travel. For a
    NEUTRAL read both sides are shown as the expected range.
    """
    px = _f(price, 0.0) or _f((row or {}).get("price"), 0.0)
    mpp = _f(money_per_price, 0.0)
    out: Dict[str, Any] = {
        "side": str(verdict or NEUTRAL).upper(),
        "timeframe": str(timeframe_key or DEFAULT_TIMEFRAME),
        "lot": _f(lot, 0.01),
        "ready": False,
        "entry": round(px, digits),
        "atr": 0.0, "rsi": 0.0, "move_mult": _MOVE_MULT,
        "up_price": 0.0, "down_price": 0.0,
        "up_distance": 0.0, "down_distance": 0.0,
        "up_money": 0.0, "down_money": 0.0,
        "level_up": 0.0, "level_down": 0.0,
        "primary_side": "", "primary_price": 0.0, "primary_distance": 0.0, "primary_money": 0.0,
        "risk_price": 0.0, "risk_distance": 0.0, "risk_money": 0.0,
    }
    if not row or not row.get("ready") or px <= 0:
        return out

    up_price = _f(row.get("up_price"), 0.0)
    down_price = _f(row.get("down_price"), 0.0)
    up_dist = max(0.0, up_price - px)
    down_dist = max(0.0, px - down_price)
    up_price, down_price = px + up_dist, px - down_dist
    out.update({
        "ready": True,
        "entry": round(px, digits),
        "atr": round(_f(row.get("atr"), 0.0), digits),
        "rsi": _f(row.get("rsi"), 0.0),
        "move_mult": round(up_dist / _f(row.get("atr"), 1.0), 2) if _f(row.get("atr"), 0.0) else _MOVE_MULT,
        "up_price": round(up_price, digits),
        "down_price": round(down_price, digits),
        "up_distance": round(up_dist, digits),
        "down_distance": round(down_dist, digits),
        "up_money": round(up_dist * mpp, 2),
        "down_money": round(down_dist * mpp, 2),
        "level_up": round(_f(row.get("level_up"), up_price), digits),
        "level_down": round(_f(row.get("level_down"), down_price), digits),
    })
    if out["side"] == "BUY":
        out.update({"primary_side": "BUY", "primary_price": out["up_price"],
                    "primary_distance": out["up_distance"], "primary_money": out["up_money"],
                    "risk_price": out["down_price"], "risk_distance": out["down_distance"],
                    "risk_money": out["down_money"]})
    elif out["side"] == "SELL":
        out.update({"primary_side": "SELL", "primary_price": out["down_price"],
                    "primary_distance": out["down_distance"], "primary_money": out["down_money"],
                    "risk_price": out["up_price"], "risk_distance": out["up_distance"],
                    "risk_money": out["up_money"]})
    else:
        out.update({"primary_side": "", "primary_price": out["up_price"],
                    "primary_distance": out["up_distance"], "primary_money": out["up_money"],
                    "risk_price": out["down_price"], "risk_distance": out["down_distance"],
                    "risk_money": out["down_money"]})
    return out


def build_payload(now: float, symbol: str = "", price: float = 0.0, digits: int = 2,
                  lot: float = 0.01, money_per_price: float = 0.0,
                  frames: Optional[Dict[str, Any]] = None, timeframe_key: str = DEFAULT_TIMEFRAME,
                  markets: Optional[Sequence[str]] = None, news: Optional[Dict[str, Any]] = None,
                  flow: Optional[Dict[str, Any]] = None, source: str = "mt5",
                  move_mult: float = _MOVE_MULT) -> Dict[str, Any]:
    """Everything the AI market panel needs, in one JSON-safe dict."""
    frames = frames or {}
    tf_key = timeframe(timeframe_key)["key"]
    closed: Dict[str, List[Sequence[float]]] = {
        tf["key"]: closed_bars(frames.get(tf["key"]) or [], tf["minutes"], now)
        for tf in TIMEFRAMES
    }
    # the live tick is the price every target is measured from; without a tick
    # the last closed candle of the selected timeframe is the honest fallback
    px = _f(price, 0.0)
    if px <= 0:
        for key in (tf_key, *TIMEFRAME_KEYS):
            bars = closed.get(key) or []
            if bars:
                px = _f(bars[-1][4], 0.0)
                break

    rows: List[Dict[str, Any]] = [
        analyze(closed[tf["key"]], price=px, digits=digits, key=tf["key"], move_mult=move_mult)
        for tf in TIMEFRAMES
    ]

    combined = combine(rows, news=news, flow=flow)
    sel = _row_for(rows, tf_key) or {}
    target = targets(sel, combined["verdict"], price=px, digits=digits,
                     money_per_price=money_per_price, lot=lot, timeframe_key=tf_key)

    ready_rows = [r for r in rows if r.get("ready")]
    return {
        "symbol": str(symbol or ""),
        "symbols": list(markets or []),
        "digits": int(digits),
        "lot": _f(lot, 0.01),
        "money_per_price": round(_f(money_per_price, 0.0), 6),
        "source": str(source or "mt5"),
        "now_utc": int(now),
        "updated": int(now),
        "price": round(px, digits),
        "timeframe": tf_key,
        "timeframes": [{"key": tf["key"], "minutes": int(tf["minutes"]),
                        "weight": float(tf["weight"])} for tf in TIMEFRAMES],
        "rows": rows,
        "combined": combined,
        "target": target,
        "news": dict(news or {}),
        "flow": dict(flow or {}),
        "ready": bool(ready_rows),
    }
