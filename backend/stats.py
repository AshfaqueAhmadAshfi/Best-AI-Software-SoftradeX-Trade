"""
Trade statistics engine.

Pure functions -> easy to test and very fast (a few hundred trades is
nothing for the CPU, so the dashboard can recompute on every tick).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# Result buckets
WIN = "win"
LOSS = "loss"
BREAKEVEN = "be"


def trade_result(net_profit: float, be_epsilon: float = 0.005) -> str:
    """Classify a closed trade. Tiny cent-level noise counts as breakeven."""
    if net_profit > be_epsilon:
        return WIN
    if net_profit < -be_epsilon:
        return LOSS
    return BREAKEVEN


def _safe_div(a: float, b: float) -> Optional[float]:
    if not b:
        return None
    return a / b


def compute_stats(
    closed: Iterable[Dict[str, Any]],
    positions: Iterable[Dict[str, Any]] = (),
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
) -> Dict[str, Any]:
    """Aggregate performance numbers.

    closed     : list of closed-trade dicts (needs close_time + net_profit)
    positions  : currently open positions (for floating P/L)
    start_ts   : inclusive lower bound on close_time (epoch seconds, server clock)
    end_ts     : exclusive upper bound on close_time
    """
    trades: List[Dict[str, Any]] = []
    for t in closed:
        ct = t.get("close_time") or 0
        if start_ts is not None and ct < start_ts:
            continue
        if end_ts is not None and ct >= end_ts:
            continue
        trades.append(t)

    wins = [t for t in trades if trade_result(t.get("net_profit", 0.0)) == WIN]
    losses = [t for t in trades if trade_result(t.get("net_profit", 0.0)) == LOSS]
    bes = [t for t in trades if trade_result(t.get("net_profit", 0.0)) == BREAKEVEN]

    gross_profit = sum(float(t.get("net_profit", 0.0)) for t in wins)
    gross_loss = abs(sum(float(t.get("net_profit", 0.0)) for t in losses))
    net = sum(float(t.get("net_profit", 0.0)) for t in trades)
    volume = sum(float(t.get("volume", 0.0)) for t in trades)

    open_list = list(positions)
    floating = sum(float(p.get("profit", 0.0)) + float(p.get("swap", 0.0)) for p in open_list)

    best = max(trades, key=lambda t: float(t.get("net_profit", 0.0)), default=None)
    worst = min(trades, key=lambda t: float(t.get("net_profit", 0.0)), default=None)

    avg_win = _safe_div(gross_profit, len(wins))
    avg_loss = _safe_div(gross_loss, len(losses))

    pf = _safe_div(gross_profit, gross_loss)
    if pf is None:
        pf = float("inf") if gross_profit > 0 else 0.0

    expectancy = _safe_div(net, len(trades)) or 0.0

    # longest live streaks (most recent first is more useful, computed over time order)
    ordered = sorted(trades, key=lambda t: t.get("close_time") or 0)
    cur_w = cur_l = max_w = max_l = 0
    for t in ordered:
        r = trade_result(t.get("net_profit", 0.0))
        if r == WIN:
            cur_w += 1
            cur_l = 0
            max_w = max(max_w, cur_w)
        elif r == LOSS:
            cur_l += 1
            cur_w = 0
            max_l = max(max_l, cur_l)
        else:
            cur_w = cur_l = 0

    total = len(trades)
    win_rate = (len(wins) / total * 100.0) if total else 0.0

    return {
        "trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(bes),
        "win_rate": round(win_rate, 2),
        "net_profit": round(net, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": (round(pf, 3) if pf != float("inf") else None),
        "expectancy": round(expectancy, 2),
        "avg_win": round(avg_win, 2) if avg_win is not None else None,
        "avg_loss": round(avg_loss, 2) if avg_loss is not None else None,
        "best": (round(float(best.get("net_profit", 0.0)), 2) if best else None),
        "best_symbol": (best.get("symbol") if best else None),
        "worst": (round(float(worst.get("net_profit", 0.0)), 2) if worst else None),
        "worst_symbol": (worst.get("symbol") if worst else None),
        "volume": round(volume, 2),
        "max_win_streak": max_w,
        "max_loss_streak": max_l,
        "floating": round(floating, 2),
        "open_count": len(open_list),
        "open_volume": round(sum(float(p.get("volume", 0.0)) for p in open_list), 2),
    }
