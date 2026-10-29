"""
Central configuration for the Exness Trading Dashboard.

Everything is stored in a small JSON file next to the project so the
dashboard keeps its settings between restarts.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict

APP_NAME = "Exness Trading Dashboard"
VERSION = "1.0.0"

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
EA_DIR = BASE_DIR / "ea"

CONFIG_FILE = CONFIG_DIR / "config.json"
DB_FILE = DATA_DIR / "trades.db"


# ---------------------------------------------------------------- hosted mode
# cPanel (Passenger), Docker and any plain Linux box have no MetaTrader 5
# terminal - the bridge package is Windows-only. In that case the same
# dashboard is served in hosted mode: prices come from the public market feeds
# (Binance + Yahoo REST), the browser polls over HTTP instead of a WebSocket,
# and nothing waits for a terminal that cannot exist on that machine.
def _env_flag(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in ("1", "true", "yes", "on")


def detect_hosted() -> bool:
    mode = str(os.environ.get("SOFTRADEX_MODE", "")).strip().lower()
    if mode in ("public", "hosted", "web"):
        return True
    if mode in ("mt5", "local", "desktop"):
        return False
    if _env_flag("SOFTRADEX_HOSTED"):
        return True
    # The MT5 bridge is Windows-only, so every Linux host (cPanel, Docker, VPS)
    # is hosted mode. A Windows PC keeps the desktop behaviour even if the
    # package is not installed yet - the login page then explains how to set up
    # the terminal instead of pretending to be a public feed dashboard.
    return os.name != "nt"


HOSTED = detect_hosted()

# Exness MT5 servers offered on the login page (from the user's own list).
EXNESS_SERVERS: Dict[str, list] = {
    "real": [
        "Exness-MT5Real6",
        "Exness-MT5Real7",
        "Exness-MT5Real17",
        "Exness-MT5Real18",
        "Exness-MT5Real20",
        "Exness-MT5Real24",
        "Exness-MT5Real26",
        "Exness-MT5Real28",
        "Exness-MT5Real29",
        "Exness-MT5Real38",
    ],
    "trial": [
        "Exness-MT5Trial6",
        "Exness-MT5Trial7",
        "Exness-MT5Trial14",
        "Exness-MT5Trial16",
        "Exness-MT5Trial17",
    ],
}


def server_list() -> list:
    """Flat list of every server name, real ones first."""
    out = []
    for key in ("real", "trial"):
        out.extend(EXNESS_SERVERS.get(key, []))
    return out


def known_servers() -> list:
    """Wider suggestion list for the free-text 'other server' box.

    The dropdown shows exactly the user's list; this one only feeds the
    browser datalist so typing also suggests the rest of the Exness range.
    """
    wide = []
    for i in range(1, 41):
        wide.append("Exness-MT5Real" if i == 1 else "Exness-MT5Real%d" % i)
    for i in range(1, 21):
        wide.append("Exness-MT5Trial%d" % i)
    first = server_list()
    return first + [s for s in wide if s not in first]


DEFAULTS: Dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8777,
    # live data refresh (account + floating P/L). 400 ms = smooth but light.
    "poll_interval_ms": 400,
    # fast tick stream feeding the market pressure watch dials (price + dial clock).
    # The dedicated stream thread sleeps this long between frames. The MT5 tick
    # read itself costs a few milliseconds, so 30 ms lands at roughly 18-20 price
    # updates per second, all pushed over the WebSocket - there is no UI timer.
    # Live tunable through POST /api/settings {"watch_tick_ms": 30}.
    "watch_tick_ms": 30,
    # how often closed-deal history is re-read from the terminal
    "history_interval_ms": 2000,
    # how many days of closed deals are kept / loaded
    "history_days": 60,
    # magics / sources that the bridge accepts (empty = accept all)
    "ea_secret": "",
    "terminal_path": "",
    "remember_login": True,
    "saved_login": "",
    "saved_server": "",
    "spread_history_days": 90,
    # ---- gold auto-trading bot (see backend/bot.py) ----
    "bot": {
        "running": False,
        "lot": 0.01,
        "distance": 2.0,       # dollars above/below the market for the straddle
        "lock_trigger": 1.0,   # money profit that locks the first SL
        "lock_amount": 0.1,    # money locked at that moment
        "trail_start": 1.5,    # money profit where SL = profit/2 trailing starts
        "symbol": "XAUUSDm",   # gold only - this exact symbol
        "magic": 777001,
        "max_spread": 0.0,     # 0 = no spread guard
        "cooldown_sec": 1,     # re-arm almost immediately after a close
        "slippage_points": 30,
    },
    # ---- candle momentum & structure auto-trading bot (see backend/trend_bot.py) ----
    "bot2": {
        "running": False,
        "lot": 0.01,
        "max_trades": 1,
        "tp": 0.10,            # TP in dollars (default $0.10)
        "sl": 2.00,            # SL in dollars (default $2.00)
        "symbol": "BTCUSDTm",
        "timeframe": "D1",     # Day (D1) or Weekend/Week (W1)
        "magic": 777002,
        "slippage_points": 30,
        "cooldown_sec": 5.0,
        "trend_mode": True,    # If True, trade strictly in the market trend direction
        "trend_check_interval": 2.5,   # 2.5s periodic trend checking
        "watch_min_threshold_pct": 60.0,  # Watch dial pressure threshold (>60% to trade)
        # 1-minute candle time-window no-trade blocks (seconds). User-editable
        # from the Bot 2 panel - the entered values are authoritative.
        "block_start_sec": 10.0,   # no trade in the FIRST N seconds of a candle
        "block_end_sec": 10.0,     # no trade in the LAST N seconds of a candle
        "block_mid_from": 25.0,    # no trade from this second ...
        "block_mid_to": 35.0,      # ... up to this second of the candle
    },
    # ---- extreme high & low structure sniper bot (see backend/extreme_bot.py) ----
    "bot3": {
        "running": False,
        "lot": 0.01,
        "max_trades": 1,
        "tp": 0.50,            # TP in dollars (default $0.50)
        "sl": 5.00,            # SL in dollars (default $5.00)
        "symbol": "XAUUSDm",
        "timeframe": "D1",     # Active view: D1 (Today), W1 (This Week), PREV (Prev Week)
        "magic": 777003,
        "slippage_points": 30,
        "cooldown_sec": 2.0,
        "tolerance_pct": 0.0006,
    },
    # ---- pre-trade ladder bot: N buys above + N sells below (see backend/pretrade_bot.py) ----
    "bot4": {
        "running": False,
        "lot": 0.01,           # same lot for every pre-trade (unless numbering)
        "price_gap": 1.0,      # $ from the market price to the FIRST pre-trade
        "pre_trade_gap": 0.5,  # $ between two successive pre-trade levels
        "pre_trade_value": 5,  # N levels per side (N buy above + N sell below)
        "numbering_lot": False, # ON -> lots auto-numbered 0.01, 0.02, 0.03 ...
        "symbol": "XAUUSDm",
        "magic": 777004,
        "slippage_points": 30,
        "cooldown_sec": 2.0,
        "lock_pct": 60.0,      # SL locks 60% of the current profit
    },
    # ---- news trader: trades the economic calendar (see backend/news_bot.py) ----
    "bot5": {
        "running": False,
        "lot": 0.01,
        "symbol": "XAUUSDm",
        "magic": 777005,        # distinct from bot1..bot4 (777001..777004)
        "mode": "anticipate",   # anticipate | straddle | follow
        "min_impact": "high",   # high | medium | all
        "currencies": "",       # empty = the selected market's own currencies
        "pre_minutes": 5.0,     # enter this many minutes BEFORE the release
        "post_minutes": 15.0,   # close this many minutes AFTER the release
        "min_gap_minutes": 15.0,
        "tp": 3.0,             # take profit in account money
        "sl": 3.0,             # stop loss in account money
        "straddle_gap": 3.0,    # $ from the price for the straddle stops
        "max_trades": 1,
        "cooldown_sec": 30.0,
        "slippage_points": 30,
        "bias_min_score": 1,
        "trade_weekend": False,
        "refresh_min": 15.0,
        "auto_close_after_news": True,
        # ---- trading sessions board (Sydney / Tokyo / London / New York) ----
        "session_market": "",        # empty = the market of this bot
        "session_range_days": 20.0,   # days of M15 history averaged per session
        # ---- full AI market analysis (5m / 10m / 15m / 1h, see market_ai.py) ----
        "ai_market": "",             # empty = the market of this bot
        "ai_timeframe": "M15",        # timeframe whose target prices are shown first
        "manual_events": [],
    },
    # ---- pre-trade entry bot: one pre-trade at a Distance, Watch-gated
    #      (see backend/entry_bot.py) ----
    "bot6": {
        "running": False,
        "lot": 0.01,
        "distance": 1.0,        # $ from the market price to the pre-trade
        "symbol": "XAUUSDm",
        "magic": 777006,         # distinct from bot1..bot5 (777001..777005)
        "side": "watch",         # watch | buy | sell | both
        "watch_gate": True,      # False = use "side" without the watch dials
        "watch_min_pct": 60.0,   # dial pressure required to take the trade
        "ai_gate": True,         # the AI market-direction board also approves the side
        "tp": 0.0,               # 0 = no take profit
        "sl": 0.0,               # 0 = 70% profit-lock trail
        "lock_pct": 70.0,
        "lock_min_profit": 0.80,
        "cooldown_sec": 2.0,
        "slippage_points": 30,
    },
    # ---- reversal candle bot: trades the instant a candle reverses
    #      (see backend/reversal_bot.py) ----
    "bot7": {
        "running": False,
        "lot": 0.01,
        "symbol": "XAUUSDm",
        "magic": 777007,          # distinct from bot1..bot6
        "timeframe": "M1",        # M1 | M5 | M15
        "reverse_dist": 0.0,      # $ back against the candle; 0 = its own body
        "min_body": 0.0,          # $ the closed candle must have moved
        "tp": 0.0,                # 0 = no take profit
        "sl": 0.0,                # 0 = 70% profit-lock trail
        "lock_pct": 70.0,
        "lock_min_profit": 0.80,
        "watch_gate": True,
        "watch_min_pct": 60.0,
        "ai_gate": True,          # the AI market-direction board also approves the side
        "max_trades": 1,
        "cooldown_sec": 3.0,
        "slippage_points": 30,
    },
}


def _ensure_dirs() -> None:
    for d in (DATA_DIR, CONFIG_DIR, EA_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, Any]:
    _ensure_dirs()
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                cfg.update(stored)
        except Exception:
            pass
    if not cfg.get("ea_secret"):
        cfg["ea_secret"] = secrets.token_urlsafe(24)
        save_config(cfg)
    return cfg


# one writer at a time: update_config() is called from the engine thread and
# from FastAPI request handlers, so the read-modify-write below must be atomic
_CONFIG_LOCK = threading.Lock()


def save_config(cfg: Dict[str, Any]) -> None:
    _ensure_dirs()
    # unique temp file per write: two concurrent writers each get their own tmp
    # and the final os.replace is atomic, so config.json is never truncated
    fd, tmp_name = tempfile.mkstemp(prefix="config.", suffix=".json.tmp", dir=str(CONFIG_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
        os.replace(tmp_name, CONFIG_FILE)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


CONFIG: Dict[str, Any] = load_config()


def update_config(**kwargs: Any) -> Dict[str, Any]:
    with _CONFIG_LOCK:
        CONFIG.update(kwargs)
        save_config(CONFIG)
        return dict(CONFIG)
