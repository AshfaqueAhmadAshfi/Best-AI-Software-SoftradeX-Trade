"""
Central configuration for the Exness Trading Dashboard.

Everything is stored in a small JSON file next to the project so the
dashboard keeps its settings between restarts.
"""
from __future__ import annotations

import json
import os
import secrets
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


def save_config(cfg: Dict[str, Any]) -> None:
    _ensure_dirs()
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)


CONFIG: Dict[str, Any] = load_config()


def update_config(**kwargs: Any) -> Dict[str, Any]:
    CONFIG.update(kwargs)
    save_config(CONFIG)
    return CONFIG
