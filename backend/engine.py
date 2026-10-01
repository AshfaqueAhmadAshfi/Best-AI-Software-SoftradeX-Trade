"""
The engine: one background thread that owns every MT5 call, merges data
from the two supported sources (live MT5 terminal, EA bridge), keeps a stats
snapshot in memory and pushes it to the WebSocket hub.

Only this thread touches MetaTrader5 / the SQLite store, which keeps
everything race-free while still being fast enough for sub-second updates.
Exception: the watch-stream thread below calls one read-only market function
(symbol_info_tick) to stream watch-dial prices at ~20 fps - that is the only MT5
access outside the engine thread, and it never places orders or logs in/out.

Notes
-----
* There is no simulator: the dashboard only ever shows real data. A login
  must succeed before any data is published.
* Every stored trade is tagged with its account login, and history is always
  filtered by the currently connected account.
* Disconnecting wipes the whole live state (account, positions, history,
  statistics) so the UI returns to the login page cleanly.
"""
from __future__ import annotations

import queue
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from . import config as cfg
from .bot import GoldBot
from .trend_bot import TrendStructureBot
from .extreme_bot import ExtremeStructureBot
from .mt5_client import MT5Client, describe_error, find_terminals, package_available, package_version
from .market_watch import get_all_market_watches, get_live_quotes
from .stats import compute_stats
from .store import Store

DAY = 86400
WINDOWS = [("today", 0), ("7d", 7), ("30d", 30), ("90d", 90)]
MAX_EVENTS = 120
HISTORY_LIMIT = 1000
EA_STALE_SECONDS = 20.0
# Fast price stream for the market pressure watch dials: the dedicated stream
# thread sleeps this long between frames, which lands at about 20 updates per
# second once the MT5 read time is added (configurable with "watch_tick_ms").
WATCH_TICK_SEC = 0.03

TRADE_MODE_DEMO = 0
TRADE_MODE_CONTEST = 1
TRADE_MODE_REAL = 2


def account_kind(account: Optional[Dict[str, Any]]) -> Optional[str]:
    """'real' / 'demo' / 'contest' from the MT5 account trade_mode."""
    if not account:
        return None
    tm = account.get("trade_mode")
    if tm is None:
        kind = str(account.get("account_type") or "").lower()
        return kind or None
    try:
        tm = int(tm)
    except (TypeError, ValueError):
        return None
    if tm == TRADE_MODE_REAL:
        return "real"
    if tm == TRADE_MODE_DEMO:
        return "demo"
    if tm == TRADE_MODE_CONTEST:
        return "contest"
    return "unknown"


class _Reply:
    __slots__ = ("event", "result")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Dict[str, Any] = {}

    def set(self, result: Dict[str, Any]) -> None:
        self.result = result
        self.event.set()

    def wait(self, timeout: float = 90.0) -> Dict[str, Any]:
        self.event.wait(timeout)
        return self.result or {"ok": False, "error": {"code": -1, "message": "engine timeout",
                                                      "hint": "The MT5 terminal did not answer in time."}}


class Engine:
    def __init__(self, store: Store, publisher: Optional[Callable[[Dict[str, Any], bool], None]] = None,
                 watch_publisher: Optional[Callable[[Dict[str, Any]], None]] = None,
                 watch_active: Optional[Callable[[], bool]] = None) -> None:
        self.store = store
        self.publisher = publisher
        # Light, high-frequency channel for the market pressure watch dials only.
        # The dials are pushed live on every engine tick - the browser needs no timer.
        self.watch_publisher = watch_publisher
        # Tells the engine whether anybody is watching the dials (skip the fast
        # tick stream with no browser attached).
        self.watch_active = watch_active
        self.watch_tick_sec = WATCH_TICK_SEC
        self._last_watch_sig: tuple = ()
        self.mt5 = MT5Client()
        self.bot = GoldBot(logger=self._log_event)
        self.bot_status: Dict[str, Any] = {}
        self.bot2 = TrendStructureBot(logger=self._log_event)
        # Bot 2's trend system is fed by the Market Pressure Watch dials:
        # BUYER -> buy, SELLER -> sell, NEUTRAL -> no trade.
        self.bot2.set_watch_provider(self._watch_pressure_for)
        self.bot2_status: Dict[str, Any] = {}
        self.bot3 = ExtremeStructureBot(logger=self._log_event)
        self.bot3_status: Dict[str, Any] = {}
        self.orders_cache: List[Dict[str, Any]] = []
        self.bot_fast = False

        self.mode: Optional[str] = None          # None | 'live'
        self.status = "disconnected"             # disconnected | connecting | connected | error
        self.connecting = False
        self.error: Optional[Dict[str, Any]] = None
        self.connected_at: Optional[int] = None
        # The dashboard is locked (login page) until an Exness account logs in.
        # An explicit disconnect locks it again and nothing else can unlock it.
        self.locked = True

        self.ea_positions: List[Dict[str, Any]] = []
        self.ea_account: Optional[Dict[str, Any]] = None
        self.ea_last_seen: float = 0.0
        self.ea_events: int = 0

        self.closed_cache: List[Dict[str, Any]] = []
        self.positions_cache: List[Dict[str, Any]] = []
        self.account_cache: Optional[Dict[str, Any]] = None
        self.events: List[Dict[str, Any]] = []
        self._seen_open: set[str] = set()
        self._seen_closed: set[str] = set()
        self._session_start_ts: int = 0

        self._commands: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._snapshot_lock = threading.Lock()
        self._snapshot: Dict[str, Any] = {}
        self._last_history_ts = 0.0
        self._last_publish_ts = 0.0
        self._last_signature: tuple = ()
        self.latency_ms = 0.0
        self.poll_interval = max(cfg.CONFIG.get("poll_interval_ms", 400) / 1000.0, 0.1)
        self.history_interval = max(cfg.CONFIG.get("history_interval_ms", 2000) / 1000.0, 0.5)
        self.history_days = int(cfg.CONFIG.get("history_days", 60))

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
        self._thread.start()
        self._start_watch_stream()

    def stop(self) -> None:
        self._stop.set()
        self.submit("stop")
        if self._thread:
            self._thread.join(timeout=5)

    def submit(self, cmd: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 90.0) -> Dict[str, Any]:
        reply = _Reply()
        self._commands.put((cmd, payload or {}, reply))
        if cmd == "stop":
            return {"ok": True}
        return reply.wait(timeout)

    # --------------------------------------------------------------- public
    def snapshot(self) -> Dict[str, Any]:
        with self._snapshot_lock:
            return self._snapshot

    def recent_events(self, limit: int = 40) -> List[Dict[str, Any]]:
        with self._snapshot_lock:
            return list(self.events[:limit])

    # ----------------------------------------------------------- main loop
    def _run(self) -> None:
        self._log_event("engine", "Engine started")
        self._refresh_static()
        self._compute()
        self._publish(force=True)
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                self._drain_commands()
                if self.mode == "live":
                    self._tick_live()
                self._tick_ea()
                self._compute()
                self._publish()
            except Exception as exc:  # never let the loop die
                self._log_event("error", f"Engine loop error: {exc}")
            self.latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
            # the bot gets a faster loop while a position is open (SL trailing)
            interval = self.poll_interval
            if self.mode == "live" and self.bot_fast:
                interval = min(interval, 0.15)
            elif self.mode == "live" and self.bot.enabled:
                interval = min(interval, 0.25)
            elapsed = time.perf_counter() - started
            self._stop.wait(max(interval - elapsed, 0.02))

        try:
            self.mt5.shutdown()
        except Exception:
            pass
        self._log_event("engine", "Engine stopped")

    def _drain_commands(self) -> None:
        while True:
            try:
                cmd, payload, reply = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                if cmd == "connect":
                    reply.set(self._do_connect(payload))
                elif cmd == "disconnect":
                    reply.set(self._do_disconnect())
                elif cmd == "ea_event":
                    reply.set(self._do_ea_event(payload))
                elif cmd == "settings":
                    reply.set(self._do_settings(payload))
                elif cmd == "bot_config":
                    reply.set(self._do_bot_config(payload))
                elif cmd == "bot_close":
                    reply.set(self._do_bot_close())
                elif cmd == "bot2_config":
                    reply.set(self._do_bot2_config(payload))
                elif cmd == "bot2_close":
                    reply.set(self._do_bot2_close())
                elif cmd == "bot2_check_trend":
                    reply.set(self._do_bot2_check_trend())
                elif cmd == "bot3_config":
                    reply.set(self._do_bot3_config(payload))
                elif cmd == "bot3_close":
                    reply.set(self._do_bot3_close())
                elif cmd == "bot3_recalculate":
                    reply.set(self._do_bot3_recalculate())
                elif cmd == "scan_terminals":
                    reply.set({"ok": True, "terminals": find_terminals(force=True)})
                elif cmd == "reload_history":
                    self._backfill_history(force=True)
                    reply.set({"ok": True})
                elif cmd == "stop":
                    self._stop.set()
                    reply.set({"ok": True})
                else:
                    reply.set({"ok": False, "error": {"message": f"unknown command {cmd}"}})
            except Exception as exc:
                reply.set({"ok": False, "error": {"code": -1, "message": str(exc), "hint": ""}})

    # -------------------------------------------------------------- connect
    def _do_connect(self, p: Dict[str, Any]) -> Dict[str, Any]:
        login = int(p.get("login") or 0)
        password = p.get("password") or ""
        server = (p.get("server") or "").strip()
        path = (p.get("path") or "").strip()

        if not login:
            return {"ok": False, "error": {"code": -1, "message": "Exness account login din",
                                           "hint": "Login = your MT5 account number."}}
        if not server:
            return {"ok": False, "error": {"code": -1, "message": "Server din",
                                           "hint": "Example: Exness-MT5Real6"}}
        self._do_disconnect(quiet=True)
        self.status = "connecting"
        self.error = None
        self.connecting = True
        self._log_event("info", f"Logging in to account {login} on {server}...")
        self._compute()
        self._publish(force=True)

        result: Dict[str, Any]
        try:
            res = self.mt5.connect(login, password, server, path)
            if not res.get("ok"):
                self.mode = None
                self.status = "error"
                self.locked = True
                self.error = res.get("error") or {"code": -1, "message": "Login failed", "hint": ""}
                self._log_event("error", f"Login failed: {self.error.get('message')}")
                result = {"ok": False, "error": self.error}
            else:
                self.mode = "live"
                self.locked = False
                self.status = "connected"
                self.connected_at = int(time.time())
                self._session_start_ts = self._server_day_start()
                self.account_cache = self.mt5.account()
                self.positions_cache = self.mt5.positions() or []
                acc = self.account_cache or {}
                if acc:
                    self.store.touch_account(str(acc.get("login", login)), str(acc.get("server", server)),
                                             str(acc.get("currency", "")), str(acc.get("name", "")))
                kind = account_kind(self.account_cache)
                label = {"real": "REAL", "demo": "DEMO", "contest": "CONTEST"}.get(kind or "", "")
                self.bot.update_config(cfg.CONFIG.get("bot") or {})
                # force a fresh symbol check on every login
                self.bot.symbol = ""
                self.bot._resolve_ts = 0.0
                self.bot.resolve_symbol()
                self.bot2.update_config(cfg.CONFIG.get("bot2") or {})
                self.bot2.symbol = ""
                self.bot2._resolve_ts = 0.0
                self.bot2.resolve_symbol()
                self.bot3.update_config(cfg.CONFIG.get("bot3") or {})
                self.bot3.symbol = ""
                self.bot3._resolve_ts = 0.0
                self.bot3.resolve_symbol()
                self._log_event("success",
                                f"Logged in: {acc.get('login', login)} · {acc.get('server', server)}"
                                + (f" · {label}" if label else ""))
                self._backfill_history(force=True)
                result = {"ok": True, "mode": "live", "account": self.account_cache,
                          "account_kind": kind, "terminal": self.mt5.terminal_path}
        except Exception as exc:
            self.mode = None
            self.status = "error"
            self.error = {"code": -1, "message": str(exc), "hint": ""}
            self._log_event("error", f"Login failed: {exc}")
            result = {"ok": False, "error": self.error}
        finally:
            self.connecting = False
            self._compute()
            self._publish(force=True)
        return result

    def _do_disconnect(self, quiet: bool = False) -> Dict[str, Any]:
        """Full session reset - nothing from the old account survives.

        quiet=True is used internally before a new login attempt and leaves the
        lock state untouched; a user-facing disconnect locks the dashboard again.
        """
        try:
            if self.mode == "live":
                self.mt5.shutdown()
        except Exception:
            pass
        self.mode = None
        self.status = "disconnected"
        self.connected_at = None
        self.account_cache = None
        self.positions_cache = []
        self.orders_cache = []
        self.closed_cache = []
        self.ea_positions = []
        self.ea_last_seen = 0.0
        # safety: logging out always stops the auto-trader
        if self.bot.cfg.get("running"):
            persisted = dict(cfg.CONFIG.get("bot") or {})
            persisted["running"] = False
            cfg.update_config(bot=persisted)
        self.bot.cfg["running"] = False
        self.bot.enabled = False
        self.bot_fast = False
        self.bot_status = self._bot_stub()
        if self.bot2.cfg.get("running"):
            persisted2 = dict(cfg.CONFIG.get("bot2") or {})
            persisted2["running"] = False
            cfg.update_config(bot2=persisted2)
        self.bot2.cfg["running"] = False
        self.bot2.enabled = False
        self.bot2_status = self._bot2_stub()
        if self.bot3.cfg.get("running"):
            persisted3 = dict(cfg.CONFIG.get("bot3") or {})
            persisted3["running"] = False
            cfg.update_config(bot3=persisted3)
        self.bot3.cfg["running"] = False
        self.bot3.enabled = False
        self.bot3_status = self._bot3_stub()
        self._seen_open.clear()
        self._seen_closed.clear()
        self._session_start_ts = 0
        self._last_history_ts = 0.0
        if not quiet:
            self.locked = True
            self._log_event("info", "Disconnected - Exness account login din")
        self._compute()
        self._publish(force=True)
        return {"ok": True}

    def _do_settings(self, p: Dict[str, Any]) -> Dict[str, Any]:
        if "poll_interval_ms" in p:
            self.poll_interval = max(float(p["poll_interval_ms"]) / 1000.0, 0.1)
        if "history_interval_ms" in p:
            self.history_interval = max(float(p["history_interval_ms"]) / 1000.0, 0.5)
        if "history_days" in p:
            self.history_days = max(int(p["history_days"]), 1)
        if "terminal_path" in p:
            self.mt5.terminal_path = str(p["terminal_path"] or "")
        cfg.update_config(**{k: v for k, v in p.items() if k in cfg.CONFIG})
        self._refresh_static()
        self._compute()
        self._publish(force=True)
        return {"ok": True}

    # ---------------------------------------------------------- auto trading
    def _bot_stub(self) -> Dict[str, Any]:
        return {
            "running": bool((cfg.CONFIG.get("bot") or {}).get("running")),
            "enabled": False,
            "symbol": "",
            "symbol_requested": str(self.bot.cfg.get("symbol") or "XAUUSDm"),
            "symbol_ok": False,
            "gold_symbols": [],
            "gap": float(self.bot.cfg.get("distance") or 0.0),
            "sl_distance": self.bot.effective_sl_distance(),
            "config": {**self.bot.cfg, **(cfg.CONFIG.get("bot") or {})},
            "positions": [], "orders": [], "floating": 0.0,
            "stats": dict(self.bot.stats),
            "last_action": self.bot.last_action,
            "last_error": self.bot.last_error,
            "risk_per_trade": 0.0,
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
            "money_per_price": 0.0,
            "live": False,
        }

    def _do_bot_config(self, p: Dict[str, Any]) -> Dict[str, Any]:
        """Start / stop the gold bot or change its settings."""
        patch: Dict[str, Any] = {}
        if "running" in p:
            patch["running"] = bool(p["running"])
        for key in ("lot", "distance", "lock_trigger", "lock_amount", "trail_start",
                    "symbol", "magic", "max_spread", "cooldown_sec", "slippage_points", "sl_distance"):
            if key in p and p[key] is not None and p[key] != "":
                patch[key] = p[key]
        if self.mode != "live" and patch.get("running"):
            return {"ok": False, "error": {"code": -1,
                                           "message": "Exness account login kora nei - age login korun",
                                           "hint": "The bot can only trade while the terminal is connected."}}
        self.bot.update_config(patch)
        persisted = dict(cfg.CONFIG.get("bot") or {})
        persisted.update({k: self.bot.cfg[k] for k in patch})
        cfg.update_config(bot=persisted)

        if self.mode == "live":
            try:
                if "symbol" in patch:
                    self.bot.resolve_symbol(force=True)
                self.bot.tick()
            except Exception as exc:
                self._log_event("error", f"Bot config tick failed: {exc}")
            self.bot_status = self.bot.status()
        else:
            self.bot_status = self._bot_stub()
        if "running" in patch:
            self._log_event("info", ("Bot STARTED" if self.bot.cfg.get("running") else "Bot STOPPED")
                            + f" (lot {self.bot.cfg.get('lot')})")
        elif "symbol" in patch:
            self._log_event("info", f"Market selected: {self.bot.symbol or patch['symbol']}")
        self._compute()
        self._publish(force=True)
        return {"ok": True, "bot": self.bot_status}

    def _do_bot_close(self) -> Dict[str, Any]:
        closed = 0
        if self.mode == "live":
            try:
                closed = self.bot.close_all(reason="dashboard")
            except Exception as exc:
                self._log_event("error", f"Close all failed: {exc}")
            self.bot_status = self.bot.status()
        else:
            self.bot_status = self._bot_stub()
        persisted = dict(cfg.CONFIG.get("bot") or {})
        persisted["running"] = False
        cfg.update_config(bot=persisted)
        self.bot.cfg["running"] = False
        self._log_event("warn", f"Close-now pressed: {closed} position(s) closed, pending orders cancelled")
        self._compute()
        self._publish(force=True)
        return {"ok": True, "closed": closed, "bot": self.bot_status}

    # -------------------------------------------------------- bot 2 (trend & structure)
    def _bot2_stub(self) -> Dict[str, Any]:
        return {
            "running": bool((cfg.CONFIG.get("bot2") or {}).get("running")),
            "enabled": False,
            "symbol": "",
            "symbol_requested": str(self.bot2.cfg.get("symbol") or "BTCUSDTm"),
            "symbol_ok": False,
            "symbol_options": [],
            "ask": 0.0, "bid": 0.0, "spread": 0.0, "digits": 2,
            "config": {**self.bot2.cfg, **(cfg.CONFIG.get("bot2") or {})},
            "structure": {"d1": {}, "w1": {}, "timeframe": "D1"},
            "positions": [],
            "running_count": 0,
            "floating": 0.0,
            "stats": dict(self.bot2.stats),
            "last_action": self.bot2.last_action,
            "last_error": self.bot2.last_error,
            "candle_dir": "",
            "trend_mode": bool((cfg.CONFIG.get("bot2") or {}).get("trend_mode")),
            "market_trend": "NEUTRAL",
            "trend_details": "Awaiting 2.5s watch check",
            "trend_score": 0,
            "watch_pressure": self.bot2._watch_pressure,
            "watch_buyer_pct": round(self.bot2._watch_buyer_pct, 1),
            "watch_seller_pct": round(self.bot2._watch_seller_pct, 1),
            "watch_min_threshold_pct": 52.0,
            "trend_checked_ts": 0.0,
            "trend_interval": 2.5,
            "trend_next_check_in": 2.5,
            "trend_history": [],
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
            "money_per_price": 0.0,
        }

    def _do_bot2_config(self, p: Dict[str, Any]) -> Dict[str, Any]:
        """Start / stop the trend & structure bot or change its settings."""
        patch: Dict[str, Any] = {}
        if "running" in p:
            patch["running"] = bool(p["running"])
        if "trend_mode" in p:
            patch["trend_mode"] = bool(p["trend_mode"])
        for key in ("lot", "max_trades", "tp", "sl", "symbol", "timeframe", "magic", "cooldown_sec", "slippage_points", "trend_mode", "trend_check_interval", "block_start_sec", "block_end_sec", "block_mid_from", "block_mid_to"):
            if key in p and p[key] is not None and p[key] != "":
                patch[key] = p[key]
        if self.mode != "live" and patch.get("running"):
            return {"ok": False, "error": {"code": -1,
                                           "message": "Exness account login kora nei - age login korun",
                                           "hint": "The bot can only trade while the terminal is connected."}}
        self.bot2.update_config(patch)
        persisted = dict(cfg.CONFIG.get("bot2") or {})
        persisted.update({k: self.bot2.cfg[k] for k in patch})
        cfg.update_config(bot2=persisted)

        if self.mode == "live":
            try:
                if "symbol" in patch:
                    self.bot2.resolve_symbol(force=True)
                self.bot2.tick()
            except Exception as exc:
                self._log_event("error", f"Bot2 config tick failed: {exc}")
            self.bot2_status = self.bot2.status()
        else:
            self.bot2_status = self._bot2_stub()
        if "running" in patch:
            self._log_event("info", ("Bot 2 STARTED" if self.bot2.cfg.get("running") else "Bot 2 STOPPED")
                            + f" (lot {self.bot2.cfg.get('lot')}, max {self.bot2.cfg.get('max_trades')})")
        elif "trend_mode" in patch:
            self._log_event("info", f"Bot 2 Trend Mode: {'ACTIVE' if self.bot2.cfg.get('trend_mode') else 'DEACTIVATED'}")
        elif "symbol" in patch:
            self._log_event("info", f"Bot 2 Market selected: {self.bot2.symbol or patch['symbol']}")
        self._compute()
        self._publish(force=True)
        return {"ok": True, "bot2": self.bot2_status}

    def _do_bot2_check_trend(self) -> Dict[str, Any]:
        """Manually triggers an immediate 60s trend check for bot2."""
        if self.mode == "live":
            try:
                if not self.bot2.symbol:
                    self.bot2.resolve_symbol()
                trend = self.bot2.check_trend(force=True)
                self.bot2_status = self.bot2.status()
                self._log_event("info", f"Bot 2 Manual Trend Check: {trend} [{self.bot2_status.get('trend_details', '')}]")
            except Exception as exc:
                self._log_event("error", f"Bot2 check_trend failed: {exc}")
                self.bot2_status = self.bot2.status()
        else:
            self.bot2_status = self._bot2_stub()
        self._compute()
        self._publish(force=True)
        return {"ok": True, "trend": self.bot2_status.get("market_trend", "UP"), "bot2": self.bot2_status}

    def _do_bot2_close(self) -> Dict[str, Any]:
        closed = 0
        if self.mode == "live":
            try:
                closed = self.bot2.close_all(reason="dashboard")
            except Exception as exc:
                self._log_event("error", f"Bot2 Close all failed: {exc}")
            self.bot2_status = self.bot2.status()
        else:
            self.bot2_status = self._bot2_stub()
        persisted = dict(cfg.CONFIG.get("bot2") or {})
        persisted["running"] = False
        cfg.update_config(bot2=persisted)
        self.bot2.cfg["running"] = False
        self._log_event("warn", f"Bot 2 Close-now pressed: {closed} position(s) closed")
        self._compute()
        self._publish(force=True)
        return {"ok": True, "closed": closed, "bot2": self.bot2_status}

    # -------------------------------------------------------- bot 3 (extreme high & low structure)
    def _bot3_stub(self) -> Dict[str, Any]:
        return {
            "running": bool((cfg.CONFIG.get("bot3") or {}).get("running")),
            "enabled": False,
            "symbol": "",
            "symbol_requested": str(self.bot3.cfg.get("symbol") or "XAUUSDm"),
            "symbol_ok": False,
            "symbol_options": [],
            "ask": 0.0, "bid": 0.0, "spread": 0.0, "digits": 2, "price": 0.0,
            "config": {**self.bot3.cfg, **(cfg.CONFIG.get("bot3") or {})},
            "levels": {"d1": {}, "w1": {}, "prev": {}, "active_view": "D1"},
            "planned_trades": [],
            "timer_str": "60s",
            "zone_status": "MIDDLE (Waiting)",
            "positions": [],
            "running_count": 0,
            "floating": 0.0,
            "stats": dict(self.bot3.stats),
            "last_action": self.bot3.last_action,
            "last_error": self.bot3.last_error,
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
            "money_per_price": 0.0,
        }

    def _do_bot3_config(self, p: Dict[str, Any]) -> Dict[str, Any]:
        """Start / stop extreme structure sniper bot or change its settings."""
        patch: Dict[str, Any] = {}
        if "running" in p:
            patch["running"] = bool(p["running"])
        for key in ("lot", "max_trades", "tp", "sl", "symbol", "timeframe", "magic", "cooldown_sec", "slippage_points", "tolerance_pct"):
            if key in p and p[key] is not None and p[key] != "":
                patch[key] = p[key]
        if self.mode != "live" and patch.get("running"):
            return {"ok": False, "error": {"code": -1,
                                           "message": "Exness account login kora nei - age login korun",
                                           "hint": "The bot can only trade while the terminal is connected."}}
        self.bot3.update_config(patch)
        persisted = dict(cfg.CONFIG.get("bot3") or {})
        persisted.update({k: self.bot3.cfg[k] for k in patch})
        cfg.update_config(bot3=persisted)

        if self.mode == "live":
            try:
                if "symbol" in patch:
                    self.bot3.resolve_symbol(force=True)
                self.bot3.tick()
            except Exception as exc:
                self._log_event("error", f"Bot3 config tick failed: {exc}")
            self.bot3_status = self.bot3.status()
        else:
            self.bot3_status = self._bot3_stub()
        if "running" in patch:
            self._log_event("info", ("Bot 3 STARTED" if self.bot3.cfg.get("running") else "Bot 3 STOPPED")
                            + f" (lot {self.bot3.cfg.get('lot')}, max {self.bot3.cfg.get('max_trades')})")
        elif "symbol" in patch:
            self._log_event("info", f"Bot 3 Market selected: {self.bot3.symbol or patch['symbol']}")
        self._compute()
        self._publish(force=True)
        return {"ok": True, "bot3": self.bot3_status}

    def _do_bot3_close(self) -> Dict[str, Any]:
        closed = 0
        if self.mode == "live":
            try:
                closed = self.bot3.close_all(reason="dashboard")
            except Exception as exc:
                self._log_event("error", f"Bot3 Close all failed: {exc}")
            self.bot3_status = self.bot3.status()
        else:
            self.bot3_status = self._bot3_stub()
        persisted = dict(cfg.CONFIG.get("bot3") or {})
        persisted["running"] = False
        cfg.update_config(bot3=persisted)
        self.bot3.cfg["running"] = False
        self._log_event("warn", f"Bot 3 Close-now pressed: {closed} position(s) closed")
        self._compute()
        self._publish(force=True)
        return {"ok": True, "closed": closed, "bot3": self.bot3_status}

    def _do_bot3_recalculate(self) -> Dict[str, Any]:
        """Recalculates the 5 upcoming extreme execution trade setups with fresh market data."""
        if self.mode == "live":
            try:
                self.bot3.recalculate_setups(force=True)
            except Exception as exc:
                self._log_event("error", f"Bot3 recalculate failed: {exc}")
            self.bot3_status = self.bot3.status()
        else:
            self.bot3_status = self._bot3_stub()
        self._compute()
        self._publish(force=True)
        return {
            "ok": True,
            "bot3": self.bot3_status,
            "planned_trades": self.bot3_status.get("planned_trades", []),
            "recalculated_at": int(time.time()),
        }

    # ------------------------------------------------------------ EA bridge
    def _do_ea_event(self, p: Dict[str, Any]) -> Dict[str, Any]:
        kind = (p.get("type") or "heartbeat").lower()
        if self.locked:
            # Logged out: the EA may keep talking, but nothing is shown until
            # an Exness account logs in again.
            return {"ok": True, "received": "ignored", "reason": "logged out"}
        self.ea_last_seen = time.time()
        self.ea_events += 1
        account = p.get("account") if isinstance(p.get("account"), dict) else None

        if kind == "heartbeat":
            if not self.mode:
                self._log_event("ea", f"EA alive: {p.get('ea_name') or 'unknown'}")
        elif kind == "account":
            acc = account or {}
            if acc:
                acc = dict(acc)
                acc.setdefault("server", p.get("server") or "EA Bridge")
                acc.setdefault("currency", "USD")
                self.ea_account = acc
                self.store.touch_account(str(acc.get("login", "")), str(acc.get("server", "")),
                                         str(acc.get("currency", "")), str(acc.get("name", "")))
                self._log_event("ea", f"EA account snapshot: {acc.get('login')} ({acc.get('server')})")
                self._reload_closed()
        elif kind == "snapshot":
            self.ea_positions = [self._norm_ea_position(x, p) for x in (p.get("positions") or [])]
            if account:
                acc = dict(account)
                acc.setdefault("server", p.get("server") or "EA Bridge")
                self.ea_account = acc
                self.store.touch_account(str(acc.get("login", "")), str(acc.get("server", "")),
                                         str(acc.get("currency", "")), str(acc.get("name", "")))
            self._log_event("ea", f"EA snapshot: {len(self.ea_positions)} open position(s)")
        elif kind in ("open", "close"):
            trade = p.get("trade") or p
            if kind == "close":
                t = self._norm_ea_trade(trade, p)
                if t:
                    acct = str(p.get("account") or (account or {}).get("login") or self._account_key())
                    t["account"] = acct
                    self.store.upsert_trades([t], account=acct)
                    self._reload_closed()
                    self._log_event("trade", f"EA closed {t['symbol']} {t['volume']} -> {t['net_profit']:+.2f}")
            else:
                pos = self._norm_ea_position(trade, p)
                if pos:
                    existing = [x for x in self.ea_positions if str(x.get("ticket")) != str(pos.get("ticket"))]
                    existing.append(pos)
                    self.ea_positions = existing
                    self._log_event("trade", f"EA opened {pos['symbol']} {pos['volume']} {pos['side']}")
        else:
            return {"ok": False, "error": {"message": f"unknown ea event type '{kind}'"}}
        self._compute()
        self._publish(force=True)
        return {"ok": True, "received": kind}

    @staticmethod
    def _norm_ea_position(x: Dict[str, Any], outer: Dict[str, Any]) -> Dict[str, Any]:
        now = int(time.time())
        vol = float(x.get("volume") or 0.0)
        prof = float(x.get("profit") or 0.0)
        swap = float(x.get("swap") or 0.0)
        return {
            "ticket": x.get("ticket") or x.get("position_id") or x.get("order") or 0,
            "symbol": x.get("symbol") or outer.get("symbol") or "",
            "side": str(x.get("side") or x.get("type") or "buy").lower(),
            "volume": vol,
            "price_open": float(x.get("price_open") or x.get("open_price") or 0.0),
            "price_current": float(x.get("price_current") or x.get("price") or 0.0),
            "sl": float(x.get("sl") or 0.0),
            "tp": float(x.get("tp") or 0.0),
            "profit": round(prof, 2),
            "swap": round(swap, 2),
            "net": round(prof + swap, 2),
            "time": int(x.get("time") or now),
            "time_update": int(x.get("time_update") or now),
            "magic": int(x.get("magic") or 0),
            "comment": x.get("comment") or "",
            "source": "ea",
        }

    @staticmethod
    def _norm_ea_trade(x: Dict[str, Any], outer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pid = x.get("position_id") or x.get("ticket") or outer.get("position_id")
        if not pid:
            return None
        now = int(time.time())
        profit = float(x.get("profit") or 0.0)
        comm = float(x.get("commission") or 0.0)
        swap = float(x.get("swap") or 0.0)
        net = x.get("net_profit")
        net = float(net) if net is not None else round(profit + comm + swap, 5)
        return {
            "position_id": str(pid),
            "ticket": int(x.get("ticket") or pid),
            "symbol": x.get("symbol") or outer.get("symbol") or "",
            "side": str(x.get("side") or x.get("type") or "").lower(),
            "volume": float(x.get("volume") or 0.0),
            "open_price": float(x.get("open_price") or x.get("price_open") or 0.0),
            "open_time": int(x.get("open_time") or x.get("time") or 0),
            "close_price": float(x.get("close_price") or x.get("price") or 0.0),
            "close_time": int(x.get("close_time") or now),
            "profit": profit,
            "commission": comm,
            "swap": swap,
            "net_profit": net,
            "magic": int(x.get("magic") or 0),
            "comment": x.get("comment") or "",
            "source": "ea",
        }

    # ---------------------------------------------------------------- ticks
    def _tick_live(self) -> None:
        if not self.mt5.connected:
            self.status = "error"
            self.error = {"code": -1, "message": "Connection to the MT5 terminal was lost",
                          "hint": "Re-open the terminal, then log in again."}
            self.mode = None
            self.account_cache = None
            self.positions_cache = []
            return
        acc = self.mt5.account()
        if acc is None:
            self.status = "error"
            self.error = {"code": -1, "message": "The terminal lost the account connection",
                          "hint": "Check that the MT5 terminal is still running and logged in."}
            self.account_cache = None
            self.positions_cache = []
            return
        self.account_cache = acc
        self.status = "connected"
        self.error = None
        self.positions_cache = self.mt5.positions() or []
        self.orders_cache = self.mt5.orders() or []

        # ---------------- auto-trading bot (gold straddle) ----------------
        try:
            self.bot.update_config((cfg.CONFIG.get("bot") or {}))
            if not self.bot.symbol:
                self.bot.resolve_symbol()          # so risk / symbol show before start
            if self.bot.cfg.get("running") or self.bot.enabled:
                self.bot.tick()
            self.bot_status = self.bot.status()
            magic = int(self.bot.cfg.get("magic") or 0)
            self.bot_fast = bool(self.bot.enabled) and any(
                int(p.get("magic") or 0) == magic for p in self.positions_cache
            )
        except Exception as exc:
            self._log_event("error", f"Bot error: {exc}")
            self.bot_status = {"running": bool(self.bot.cfg.get("running")), "error": str(exc)}

        # ---------------- auto-trading bot 2 (candle momentum & structure) ----------------
        try:
            self.bot2.update_config((cfg.CONFIG.get("bot2") or {}))
            if not self.bot2.symbol:
                self.bot2.resolve_symbol()
            if self.bot2.cfg.get("running") or self.bot2.enabled:
                self.bot2.tick()
            self.bot2_status = self.bot2.status()
        except Exception as exc:
            self._log_event("error", f"Bot2 error: {exc}")
            self.bot2_status = {"running": bool(self.bot2.cfg.get("running")), "error": str(exc)}

        # ---------------- auto-trading bot 3 (extreme high & low structure) ----------------
        try:
            self.bot3.update_config((cfg.CONFIG.get("bot3") or {}))
            if not self.bot3.symbol:
                self.bot3.resolve_symbol()
            if self.bot3.cfg.get("running") or self.bot3.enabled:
                self.bot3.tick()
            self.bot3_status = self.bot3.status()
        except Exception as exc:
            self._log_event("error", f"Bot3 error: {exc}")
            self.bot3_status = {"running": bool(self.bot3.cfg.get("running")), "error": str(exc)}

        now = time.time()
        if now - self._last_history_ts > self.history_interval:
            self._last_history_ts = now
            days = 3.0 if self.closed_cache else float(self.history_days)
            trades = self.mt5.fetch_closed_trades(days=days)
            if trades:
                self.store.upsert_trades(trades, account=self._account_key())
            self._reload_closed()

    def _tick_ea(self) -> None:
        if self.mode == "live":
            return
        if self.locked:
            self.status = "disconnected"
            self.ea_positions = []
            return
        if self.ea_last_seen and (time.time() - self.ea_last_seen) > EA_STALE_SECONDS:
            if self.ea_positions:
                self._log_event("warn", "EA bridge went quiet - open positions cleared")
            self.ea_positions = []
        if self.mode is None and self.status != "connecting":
            self.status = "connected" if (self.ea_last_seen and (time.time() - self.ea_last_seen) <= EA_STALE_SECONDS) else "disconnected"

    def _backfill_history(self, force: bool = False) -> None:
        if not (self.mode == "live" and self.mt5.connected):
            return
        days = float(self.history_days if force else 3)
        trades = self.mt5.fetch_closed_trades(days=days)
        if trades:
            self.store.upsert_trades(trades, account=self._account_key())
        self._reload_closed()
        self._log_event("info", f"History synced for account {self._account_key() or '?'}: {len(trades)} deal(s)")

    def _account_key(self) -> str:
        """Login of the account whose data is currently on screen."""
        acc = None
        if self.mode == "live":
            acc = self.account_cache
        elif self.ea_account:
            acc = self.ea_account
        if not acc:
            acc = self.ea_account or self.account_cache
        if not acc:
            return ""
        return str(acc.get("login") or "")

    def _reload_closed(self) -> None:
        key = self._account_key()
        if not key:
            self.closed_cache = []
            return
        since = int(self._server_now() - (self.history_days + 5) * DAY)
        self.closed_cache = self.store.get_trades(since_ts=since, limit=20000, order="DESC", account=key)

    # -------------------------------------------------------------- compute
    def _server_now(self) -> float:
        if self.mode == "live":
            return self.mt5.server_now()
        return time.time()

    def _server_day_start(self, ts: Optional[float] = None) -> int:
        sdt = datetime.fromtimestamp(ts if ts is not None else self._server_now())
        return int(sdt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    def _effective_positions(self) -> List[Dict[str, Any]]:
        if self.locked:
            return []
        if self.mode == "live":
            return self.positions_cache
        if self.ea_last_seen and (time.time() - self.ea_last_seen) <= EA_STALE_SECONDS:
            return self.ea_positions
        return []

    def _effective_account(self) -> Optional[Dict[str, Any]]:
        if self.locked:
            return None
        if self.mode == "live":
            return self.account_cache
        if self.ea_last_seen and (time.time() - self.ea_last_seen) <= EA_STALE_SECONDS:
            return self.ea_account
        return None

    def _compute(self) -> None:
        server_now = self._server_now()
        positions = list(self._effective_positions())
        account = self._effective_account()

        if self.mode != "live":
            for p in positions:
                p["net"] = round(float(p.get("profit", 0.0)) + float(p.get("swap", 0.0)), 2)

        day_start = self._server_day_start(server_now)
        stats: Dict[str, Any] = {}
        for name, days in WINDOWS:
            start = day_start if name == "today" else int(server_now - days * DAY)
            stats[name] = compute_stats(self.closed_cache, positions if name == "today" else (), start_ts=start)
        stats["session"] = compute_stats(self.closed_cache, positions, start_ts=self._session_start_ts or day_start)
        stats["all"] = compute_stats(self.closed_cache, positions)

        # trade activity -> events (toasts)
        current_open = {str(p.get("ticket")) for p in positions}
        for ticket in current_open - self._seen_open:
            p = next((x for x in positions if str(x.get("ticket")) == ticket), None)
            if p:
                self._log_event("trade", f"Opened {p.get('symbol')} {p.get('side')} {p.get('volume')}")
        self._seen_open = current_open

        current_closed = {str(t.get("position_id")) for t in self.closed_cache[:200]}
        if self._seen_closed:
            for pid in current_closed - self._seen_closed:
                t = next((x for x in self.closed_cache if str(x.get("position_id")) == pid), None)
                if t:
                    self._log_event("trade",
                                    f"Closed {t.get('symbol')} {t.get('side')} {t.get('volume')} -> {t.get('net_profit', 0):+.2f}")
        self._seen_closed |= current_closed

        source = "none"
        if not self.locked:
            if self.mode == "live":
                source = "mt5"
            elif self.ea_last_seen and (time.time() - self.ea_last_seen) <= EA_STALE_SECONDS:
                source = "ea"

        key = self._account_key()
        self._snapshot_data = {
            "connected": bool(account) and source != "none",
            "locked": self.locked,
            "status": self.status,
            "mode": self.mode or ("ea" if source == "ea" else None),
            "source": source,
            "connecting": self.connecting,
            "error": self.error,
            "account": account,
            "account_kind": account_kind(account),
            "positions": positions,
            "orders": self.orders_cache,
            "bot": self.bot_status or self._bot_stub(),
            "bot2": self.bot2_status or self._bot2_stub(),
            "bot3": self.bot3_status or self._bot3_stub(),
            "stats": stats,
            "history": self.closed_cache[:HISTORY_LIMIT],
            "history_total": self.store.count_trades(account=key) if key else 0,
            "events": self.events[:MAX_EVENTS],
            "server_time": datetime.utcfromtimestamp(server_now).strftime("%Y-%m-%d %H:%M:%S"),
            "server_epoch": int(server_now),
            "server_utc_offset": self.mt5.server_offset() if self.mode == "live" else 0.0,
            "day_start": day_start,
            "session_start": self._session_start_ts,
            "connected_at": self.connected_at,
            "terminal": self.mt5.terminal_path,
            "terminals": find_terminals(),
            "engine_latency_ms": self.latency_ms,
            "ea": {"connected": bool(self.ea_last_seen and (time.time() - self.ea_last_seen) <= EA_STALE_SECONDS),
                   "last_seen": self.ea_last_seen, "events": self.ea_events},
            "env": {"mt5_package": package_available(), "mt5_version": package_version()},
            "windows": [w[0] for w in WINDOWS],
            "market_watch": get_all_market_watches().get("watches", []),
            "now_ms": int(time.time() * 1000),
        }

    def _publish_watch(self, watches: List[Dict[str, Any]]) -> None:
        """Pushes only the market pressure watch dials whenever they move.

        This is the live feed behind the dashboard dials: the browser patches the
        gauges straight from it, so nothing there is timer driven. The full state
        snapshot above keeps its own (much slower) change gate.
        """
        if self.watch_publisher is None or not watches:
            return
        sig = tuple(
            (w.get("symbol"), w.get("bid"), w.get("ask"), w.get("spread"),
             w.get("buyer_pct"), w.get("seller_pct"), w.get("needle_deg"),
             w.get("candle_flow"), w.get("live"))
            for w in watches
        )
        if sig == self._last_watch_sig:
            return
        self._last_watch_sig = sig
        try:
            self.watch_publisher({
                "watches": watches,
                "updated_at": time.time(),
                "now_ms": int(time.time() * 1000),
            })
        except Exception:
            pass

    def _publish_quotes(self) -> None:
        """~20 fps tick stream for the watch dials (price / spread / dial clock).

        Only bid & ask are re-read here; the buyer/seller pressure of the last full
        computation rides along inside the same frame, so a dial always shows a
        consistent pair of numbers while its price moves on every real tick.
        """
        if self.watch_publisher is None:
            return
        if self.watch_active is not None:
            try:
                if not self.watch_active():
                    return
            except Exception:
                pass
        data = getattr(self, "_snapshot_data", None) or {}
        base = data.get("market_watch") or []
        if not base:
            return
        live = [w for w in base if w.get("symbol") and w.get("live") is not False]
        if not live:
            return
        try:
            quotes = {q["symbol"]: q for q in get_live_quotes([str(w.get("symbol")) for w in live])}
        except Exception:
            return
        if not quotes:
            return
        frames: List[Dict[str, Any]] = []
        for w in base:
            q = quotes.get(str(w.get("symbol") or ""))
            if not q:
                frames.append(w)
                continue
            merged = dict(w)
            merged.update(bid=q["bid"], ask=q["ask"], spread=q["spread"],
                          digits=q["digits"], updated_at=q["updated_at"], live=True)
            frames.append(merged)
        try:
            self.watch_publisher({
                "watches": frames,
                "updated_at": time.time(),
                "now_ms": int(time.time() * 1000),
                "fast": True,
            })
        except Exception:
            pass

    def _start_watch_stream(self) -> None:
        """Starts the tiny tick thread that feeds the watch dials at ~20 fps.

        The main engine loop needs ~0.5 s per pass (account, positions, history,
        stats, bots), so it cannot deliver a 50 ms price stream. This thread does
        nothing but read the latest tick of the watched symbols - no order, no
        login, no store access - and it stays idle while no browser is attached.
        """
        if self.watch_publisher is None:
            return
        if self._watch_thread and self._watch_thread.is_alive():
            return
        self._watch_thread = threading.Thread(target=self._watch_stream_loop,
                                              name="watch-stream", daemon=True)
        self._watch_thread.start()

    def _watch_stream_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.watch_active is None or self.watch_active():
                    self._publish_quotes()
            except Exception:
                pass
            self._stop.wait(self.watch_tick_sec)

    def _publish(self, force: bool = False) -> None:
        data = getattr(self, "_snapshot_data", None)
        if data is None:
            return
        # dials first: they get their own light frame on every tick, even when the
        # full state below is still inside its change gate
        self._publish_watch(data.get("market_watch") or [])
        payload = dict(data)
        payload["events"] = data["events"][:30]
        payload["history"] = data["history"][:150]
        with self._snapshot_lock:
            self._snapshot = payload
            self._snapshot_full = data
        if self.publisher is None:
            return

        acc = data.get("account") or {}
        hist = data.get("history") or []
        pos = data.get("positions") or []
        bot = data.get("bot") or {}
        bot2 = data.get("bot2") or {}
        sig = (
            data.get("status"), data.get("source"), data.get("mode"), data.get("account_kind"),
            len(pos), round(sum(float(p.get("net") or 0.0) for p in pos), 2),
            acc.get("login"), acc.get("equity"), acc.get("balance"), acc.get("margin_free"),
            len(hist), (hist[0].get("position_id") if hist else ""),
            round(sum(float(t.get("net_profit") or 0.0) for t in hist[:40]), 2),
            (data["events"][0]["ts"] if data.get("events") else 0),
            (data.get("error") or {}).get("message"),
            # bot state: symbol changes, running state, pending orders, positions, and live price
            bot.get("symbol"), bot.get("symbol_requested"), bot.get("running"),
            len(bot.get("orders") or []), len(bot.get("positions") or []),
            bot.get("floating"), bot.get("ask"),
            bot2.get("symbol"), bot2.get("symbol_requested"), bot2.get("running"),
            bot2.get("trend_mode"), bot2.get("market_trend"),
            len(bot2.get("positions") or []), bot2.get("floating"), bot2.get("ask"),
        )
        if not force and sig == self._last_signature and (time.time() - self._last_publish_ts) < 1.0:
            return
        self._last_signature = sig
        self._last_publish_ts = time.time()
        try:
            self.publisher(payload, force)
        except Exception:
            pass

    def _watch_pressure_for(self, symbol: str) -> Dict[str, Any]:
        """Live watch-dial pressure lookup for the trading bots.

        Returns the freshest dial row for symbol; empty dict when the dial has no
        data (the bot then treats it as NEUTRAL and stays flat).
        """
        sym = str(symbol or "").strip().upper()
        if not sym:
            return {}
        data = getattr(self, "_snapshot_data", None) or {}
        for w in (data.get("market_watch") or []):
            if str(w.get("symbol") or "").strip().upper() == sym:
                return w
        return {}

    # --------------------------------------------------------------- events
    def _log_event(self, kind: str, message: str) -> None:
        # wall-clock stamp (not the broker clock) so the feed stays in order
        ev = {"ts": int(time.time() * 1000),
              "server_time": datetime.utcfromtimestamp(time.time()).strftime("%H:%M:%S"),
              "kind": kind, "message": message}
        self.events.insert(0, ev)
        del self.events[MAX_EVENTS:]
        try:
            self.store.log_event(kind, message)
        except Exception:
            pass

    def _refresh_static(self) -> None:
        self.poll_interval = max(cfg.CONFIG.get("poll_interval_ms", 400) / 1000.0, 0.1)
        self.watch_tick_sec = max(cfg.CONFIG.get("watch_tick_ms", WATCH_TICK_SEC * 1000) / 1000.0, 0.02)
        self.history_interval = max(cfg.CONFIG.get("history_interval_ms", 2000) / 1000.0, 0.5)
        self.history_days = int(cfg.CONFIG.get("history_days", 60))
        if not self.mt5.terminal_path:
            self.mt5.terminal_path = cfg.CONFIG.get("terminal_path", "") or ""
