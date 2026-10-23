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
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import config as cfg
from .bot import GoldBot
from .trend_bot import TrendStructureBot
from .extreme_bot import ExtremeStructureBot
from .pretrade_bot import PreTradeBot
from .entry_bot import PreTradeEntryBot
from .reversal_bot import ReversalCandleBot
from .news_bot import NewsBot
from . import market_ai as ai_mod
from . import sessions as sess_mod
from .mt5_client import MT5Client, describe_error, find_terminals, package_available, package_version, mt5, IPC_LOCK, set_preferred_terminal
from .market_watch import get_all_market_watches, get_live_quotes, check_symbol_market_status
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


def _num(v: Any, default: float = 0.0) -> float:
    """Best-effort float() that never raises."""
    try:
        out = float(v)
    except (TypeError, ValueError):
        return default
    if out != out:  # NaN
        return default
    return out


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
        self.bot2 = TrendStructureBot(logger=self._log_event, clock_provider=self.mt5.server_now)
        # Bot 2's trend system is fed by the Market Pressure Watch dials:
        # BUYER -> buy, SELLER -> sell, NEUTRAL -> no trade.
        self.bot2.set_watch_provider(self._watch_pressure_for)
        self.bot2_status: Dict[str, Any] = {}
        self.bot3 = ExtremeStructureBot(logger=self._log_event)
        self.bot3_status: Dict[str, Any] = {}
        self.bot4 = PreTradeBot(logger=self._log_event)
        self.bot4_status: Dict[str, Any] = {}
        # Bot 6 takes ONE pre-trade at a Distance, and only in the direction the
        # Market Pressure Watch dials allow (NEUTRAL / weak pressure = no trade).
        self.bot6 = PreTradeEntryBot(logger=self._log_event)
        self.bot6.set_watch_provider(self._watch_pressure_for)
        self.bot6.set_ai_provider(self._ai_outlook_for)
        self.bot6_status: Dict[str, Any] = {}
        # Bot 7 fires the moment a closed candle's move reverses.
        self.bot7 = ReversalCandleBot(logger=self._log_event)
        self.bot7.set_watch_provider(self._watch_pressure_for)
        self.bot7.set_ai_provider(self._ai_outlook_for)
        self.bot7_status: Dict[str, Any] = {}
        self.bot5 = NewsBot(logger=self._log_event,
                            cache_path=cfg.DATA_DIR / "news_cache.json",
                            watch_provider=self._watch_pressure_for,
                            clock_provider=self.mt5.server_now)
        self.bot5_status: Dict[str, Any] = {}
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
        try:
            self._refresh_static()
            self._compute()
            self._publish(force=True)
        except Exception as exc:
            # a first-snapshot failure must not kill the engine thread: that
            # would leave the dashboard answering "waiting..." forever with no
            # error anywhere (the loop below has the same guard)
            self._log_event("error", f"Engine startup snapshot failed: {exc}")
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                # command handlers and the live tick all place MT5 IPC calls
                # (the bots call mt5.* directly) - holding the bridge lock for
                # these two phases keeps the single terminal pipe exclusive
                # against the watch-stream and API threads
                with IPC_LOCK:
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
            elif self.mode == "live" and (self.bot6.cfg.get("running") or self.bot7.cfg.get("running")):
                # bot 6 (pre-trade) and bot 7 (reversal) need the fast loop: a
                # reversal has to be caught inside the candle it appears in
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
                elif cmd == "bot4_config":
                    reply.set(self._do_bot4_config(payload))
                elif cmd == "bot4_close":
                    reply.set(self._do_bot4_close())
                elif cmd == "bot5_config":
                    reply.set(self._do_bot5_config(payload))
                elif cmd == "bot5_close":
                    reply.set(self._do_bot5_close())
                elif cmd == "bot6_config":
                    reply.set(self._do_bot6_config(payload))
                elif cmd == "bot6_close":
                    reply.set(self._do_bot6_close())
                elif cmd == "bot7_config":
                    reply.set(self._do_bot7_config(payload))
                elif cmd == "bot7_close":
                    reply.set(self._do_bot7_close())
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
                # force a fresh symbol check on every login; also unstick the
                # operator-picked flag so the configured market resolves (and
                # falls back) exactly like a fresh start instead of keeping a
                # stale explicit choice from the previous session
                self.bot.symbol = ""
                self.bot._resolve_ts = 0.0
                self.bot._symbol_explicit = False
                self.bot.resolve_symbol()
                self.bot2.update_config(cfg.CONFIG.get("bot2") or {})
                self.bot2.symbol = ""
                self.bot2._resolve_ts = 0.0
                self.bot2._symbol_explicit = False
                self.bot2.resolve_symbol()
                self.bot3.update_config(cfg.CONFIG.get("bot3") or {})
                self.bot3.symbol = ""
                self.bot3._resolve_ts = 0.0
                self.bot3._symbol_explicit = False
                self.bot3.resolve_symbol()
                self.bot4.update_config(cfg.CONFIG.get("bot4") or {})
                self.bot4.symbol = ""
                self.bot4._resolve_ts = 0.0
                self.bot4.resolve_symbol()
                self.bot5.update_config(cfg.CONFIG.get("bot5") or {})
                self.bot5.symbol = ""
                self.bot5._resolve_ts = 0.0
                self.bot5.resolve_symbol()
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
        if self.bot4.cfg.get("running"):
            persisted4 = dict(cfg.CONFIG.get("bot4") or {})
            persisted4["running"] = False
            cfg.update_config(bot4=persisted4)
        self.bot4.cfg["running"] = False
        self.bot4.enabled = False
        self.bot4_status = self._bot4_stub()
        if self.bot5.cfg.get("running"):
            persisted5 = dict(cfg.CONFIG.get("bot5") or {})
            persisted5["running"] = False
            cfg.update_config(bot5=persisted5)
        self.bot5.cfg["running"] = False
        self.bot5.enabled = False
        self.bot5_status = self._bot5_stub()
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
            set_preferred_terminal(self.mt5.terminal_path)
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

    def _is_market_open_for_symbol(self, sym: str) -> Tuple[bool, str]:
        if not sym or self.mode != "live" or mt5 is None:
            return True, ""
        try:
            with IPC_LOCK:
                info = mt5.symbol_info(sym)
                tick = mt5.symbol_info_tick(sym)
            m_stat = check_symbol_market_status(sym, info, tick)
            if not m_stat.get("open", True):
                return False, m_stat.get("message", "Market Closed")
        except Exception:
            pass
        return True, ""

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
        if self.mode == "live" and patch.get("running"):
            sym = str(patch.get("symbol") or self.bot.symbol or "")
            is_open, msg = self._is_market_open_for_symbol(sym)
            if not is_open:
                return {"ok": False, "error": {"code": -10011,
                                               "message": f"{sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে ({msg})",
                                               "hint": f"Market for {sym} is closed. Cannot start bot."}}
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
        # The switches the dashboard shows as buttons (Watch ON/OFF, Watch %,
        # Reverse) must come from the *saved* config: the bot object still holds
        # its defaults until the account is logged in, and a stale switch would
        # contradict the button the user pressed before the restart.
        persisted = {**self.bot2.cfg, **(cfg.CONFIG.get("bot2") or {})}
        watch_on = bool(persisted.get("watch_enabled", True)) and bool(persisted.get("trend_mode", True))
        try:
            watch_pct = min(99.0, max(1.0, float(persisted.get("watch_min_pct")
                                                or persisted.get("watch_min_threshold_pct") or 60.0)))
        except (TypeError, ValueError):
            watch_pct = 60.0
        reverse = bool(persisted.get("reverse"))
        return {
            "running": bool(persisted.get("running")),
            "enabled": False,
            "symbol": "",
            "symbol_requested": str(persisted.get("symbol") or "BTCUSDTm"),
            "symbol_ok": False,
            "symbol_options": [],
            "ask": 0.0, "bid": 0.0, "spread": 0.0, "digits": 2,
            "config": persisted,
            "structure": {"d1": {}, "w1": {}, "timeframe": "D1"},
            "positions": [],
            "running_count": 0,
            "floating": 0.0,
            "stats": dict(self.bot2.stats),
            "last_action": self.bot2.last_action,
            "last_error": self.bot2.last_error,
            "candle_dir": "",
            "trend_mode": watch_on,
            "market_trend": "NEUTRAL",
            "trend_details": ("Awaiting 2.5s watch check" if watch_on
                               else "Watch OFF - candle flow decides"),
            "trend_score": 0,
            "watch_pressure": self.bot2._watch_pressure,
            "watch_buyer_pct": round(self.bot2._watch_buyer_pct, 1),
            "watch_seller_pct": round(self.bot2._watch_seller_pct, 1),
            "watch_enabled": watch_on,
            "watch_min_pct": watch_pct,
            "watch_min_threshold_pct": watch_pct,
            "reverse": reverse,
            "next_side": self.bot2._next_side(),
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
        if "watch_enabled" in p:
            patch["watch_enabled"] = bool(p["watch_enabled"])
        if "reverse" in p:
            patch["reverse"] = bool(p["reverse"])
        for key in ("lot", "max_trades", "tp", "sl", "symbol", "timeframe", "magic", "cooldown_sec", "slippage_points", "trend_mode", "watch_enabled", "watch_min_pct", "reverse", "trend_check_interval", "block_start_sec", "block_end_sec", "block_mid_from", "block_mid_to", "watch_min_threshold_pct"):
            if key in p and p[key] is not None and p[key] != "":
                patch[key] = p[key]
        if self.mode != "live" and patch.get("running"):
            return {"ok": False, "error": {"code": -1,
                                           "message": "Exness account login kora nei - age login korun",
                                           "hint": "The bot can only trade while the terminal is connected."}}
        if self.mode == "live" and patch.get("running"):
            sym = str(patch.get("symbol") or self.bot2.symbol or "")
            is_open, msg = self._is_market_open_for_symbol(sym)
            if not is_open:
                return {"ok": False, "error": {"code": -10011,
                                               "message": f"{sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে ({msg})",
                                               "hint": f"Market for {sym} is closed. Cannot start bot."}}
        self.bot2.update_config(patch)
        persisted = dict(cfg.CONFIG.get("bot2") or {})
        persisted.update({k: self.bot2.cfg[k] for k in patch})
        # the Watch switch and the Watch % have legacy aliases - persist both so
        # the saved config can never contradict the button the user pressed
        if "watch_enabled" in patch or "trend_mode" in patch:
            persisted["watch_enabled"] = bool(self.bot2.cfg.get("watch_enabled"))
            persisted["trend_mode"] = bool(self.bot2.cfg.get("trend_mode"))
        if "watch_min_pct" in patch or "watch_min_threshold_pct" in patch:
            persisted["watch_min_pct"] = float(self.bot2.cfg.get("watch_min_pct") or 60.0)
            persisted["watch_min_threshold_pct"] = persisted["watch_min_pct"]
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
        elif "watch_enabled" in patch or "trend_mode" in patch:
            self._log_event("info", f"Bot 2 Watch: {'ON - dial pressure decides the direction' if self.bot2.cfg.get('watch_enabled') else 'OFF - following the candle flow'}")
        elif "reverse" in patch:
            self._log_event("info", f"Bot 2 Reverse: {'ON - every trade is flipped' if self.bot2.cfg.get('reverse') else 'OFF'}")
        elif "watch_min_pct" in patch:
            self._log_event("info", f"Bot 2 Watch %: trades only at/above {self.bot2.cfg.get('watch_min_pct')}%")
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
        sym = str(self.bot3.cfg.get("symbol") or "XAUUSDm")
        live_price = 0.0
        live_ask = 0.0
        live_bid = 0.0
        live_levels: Dict[str, Any] = {}
        if mt5 is not None:
            try:
                if not self.bot3.symbol:
                    self.bot3.resolve_symbol()
                actual_symbol = self.bot3.symbol or sym
                tick = mt5.symbol_info_tick(actual_symbol)
                live_ask = float(getattr(tick, "ask", 0.0) or 0.0) if tick else 0.0
                live_bid = float(getattr(tick, "bid", 0.0) or 0.0) if tick else 0.0
                digits = self.bot3._digits(actual_symbol)
                live_price = round((live_ask + live_bid) / 2.0, digits) if live_ask and live_bid else 0.0
                live_levels = self.bot3.compute_all_levels(actual_symbol)
                if live_price and live_levels:
                    planned = self.bot3.compute_planned_trades(live_price, live_levels)
                else:
                    planned = []
            except Exception:
                live_levels = {}
                planned = []
        else:
            planned = []
        live_digits = self.bot3._digits(self.bot3.symbol or sym) if mt5 is not None else 2
        live_symbol = self.bot3.symbol or sym
        if live_price <= 0:
            live_price = 0.0
            live_ask = 0.0
            live_bid = 0.0
        fallback_planned = [
            {
                "id": 1, "priority_rank": 1, "action": "BUY", "scope": "Today",
                "label": "Today Lower Low (LL)", "price": 2642.50, "distance": 8.20, "dist_pct": 0.31,
                "is_touch": False, "status_text": "APPROACHING (0.31%)", "status_text_bn": "কাছাকাছি আসছে (0.31%)",
                "status_cls": "approaching", "est_tp_price": 2647.50, "est_sl_price": 2637.50,
                "reason": "Market touches Today's Lower Low Support",
                "reason_bn": "আজকের দিনের সর্বনিম্ন প্রাইস (LL) সাপোর্ট থেকে Buy নিবে",
                "tp": 0.50, "sl": 5.00
            },
            {
                "id": 2, "priority_rank": 2, "action": "SELL", "scope": "Today",
                "label": "Today Higher High (HH)", "price": 2668.00, "distance": 17.30, "dist_pct": 0.65,
                "is_touch": False, "status_text": "PENDING (0.65%)", "status_text_bn": "অপেক্ষমাণ (0.65%)",
                "status_cls": "pending", "est_tp_price": 2663.00, "est_sl_price": 2673.00,
                "reason": "Market touches Today's Higher High Resistance",
                "reason_bn": "আজকের দিনের সর্বোচ্চ প্রাইস (HH) রেজিস্ট্যান্স থেকে Sell নিবে",
                "tp": 0.50, "sl": 5.00
            },
            {
                "id": 3, "priority_rank": 3, "action": "BUY", "scope": "This Week",
                "label": "This Week Opening Low (LL)", "price": 2635.00, "distance": 15.70, "dist_pct": 0.59,
                "is_touch": False, "status_text": "PENDING (0.59%)", "status_text_bn": "অপেক্ষমাণ (0.59%)",
                "status_cls": "pending", "est_tp_price": 2640.00, "est_sl_price": 2630.00,
                "reason": "Current Week Opening Low Support sweep",
                "reason_bn": "চলতি সপ্তাহের ওপেনিং লো (LL) সাপোর্ট থেকে Buy নিবে",
                "tp": 0.50, "sl": 5.00
            },
            {
                "id": 4, "priority_rank": 4, "action": "SELL", "scope": "This Week",
                "label": "This Week Opening High (HH)", "price": 2685.20, "distance": 34.50, "dist_pct": 1.30,
                "is_touch": False, "status_text": "PENDING (1.30%)", "status_text_bn": "অপেক্ষমাণ (1.30%)",
                "status_cls": "pending", "est_tp_price": 2680.20, "est_sl_price": 2690.20,
                "reason": "Current Week Opening High Resistance sweep",
                "reason_bn": "চলতি সপ্তাহের ওপেনিং হাই (HH) রেজিস্ট্যান্স থেকে Sell নিবে",
                "tp": 0.50, "sl": 5.00
            },
            {
                "id": 5, "priority_rank": 5, "action": "BUY", "scope": "Prev Week",
                "label": "Prev Week Low (Floor)", "price": 2618.40, "distance": 32.30, "dist_pct": 1.22,
                "is_touch": False, "status_text": "PENDING (1.22%)", "status_text_bn": "অপেক্ষমাণ (1.22%)",
                "status_cls": "pending", "est_tp_price": 2623.40, "est_sl_price": 2613.40,
                "reason": "Previous Week Extreme Level major reversal",
                "reason_bn": "পূর্ববর্তী সপ্তাহের এক্সট্রিম লেভেল থেকে রিভার্সাল ট্রেড",
                "tp": 0.50, "sl": 5.00
            }
        ]
        if not planned:
            planned = fallback_planned if live_price <= 0 else []
        levels = live_levels or {"d1": {}, "w1": {}, "prev": {}, "active_view": "D1"}
        return {
            "running": bool((cfg.CONFIG.get("bot3") or {}).get("running")),
            "enabled": False,
            "symbol": live_symbol,
            "symbol_requested": sym,
            "symbol_ok": bool(live_price),
            "symbol_options": [],
            "ask": live_ask, "bid": live_bid,
            "spread": round(live_ask - live_bid, live_digits) if live_ask and live_bid else 0.0,
            "digits": live_digits, "price": live_price,
            "config": {**self.bot3.cfg, **(cfg.CONFIG.get("bot3") or {})},
            "levels": levels,
            "planned_trades": planned,
            "timer_str": "60s",
            "zone_status": "MIDDLE (Waiting)",
            "positions": [],
            "running_count": 0,
            "floating": 0.0,
            "stats": dict(self.bot3.stats),
            "last_action": self.bot3.last_action,
            "last_error": self.bot3.last_error,
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
            "money_per_price": 1.0,
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
        if self.mode == "live" and patch.get("running"):
            sym = str(patch.get("symbol") or self.bot3.symbol or "")
            is_open, msg = self._is_market_open_for_symbol(sym)
            if not is_open:
                return {"ok": False, "error": {"code": -10011,
                                               "message": f"{sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে ({msg})",
                                               "hint": f"Market for {sym} is closed. Cannot start bot."}}
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

    # ------------------------------------------------------- bot 4 (pre-trade ladder)
    def _bot4_stub(self) -> Dict[str, Any]:
        return {
            "running": bool((cfg.CONFIG.get("bot4") or {}).get("running")),
            "enabled": False,
            "symbol": "",
            "symbol_requested": str(self.bot4.cfg.get("symbol") or "XAUUSDm"),
            "symbol_ok": False,
            "symbol_substituted": "",
            "symbol_options": [],
            "ask": 0.0, "bid": 0.0, "spread": 0.0, "digits": 2, "price": 0.0,
            "config": {**self.bot4.cfg, **(cfg.CONFIG.get("bot4") or {})},
            "ladder": {"buy": [], "sell": [], "value": int((cfg.CONFIG.get("bot4") or {}).get("pre_trade_value") or 5),
                       "price_gap": float((cfg.CONFIG.get("bot4") or {}).get("price_gap") or 1.0),
                       "pre_trade_gap": float((cfg.CONFIG.get("bot4") or {}).get("pre_trade_gap") or 0.5),
                       "numbering_lot": bool((cfg.CONFIG.get("bot4") or {}).get("numbering_lot")),
                       "anchor": 0.0},
            "pending_count": 0,
            "filled_count": 0,
            "positions": [],
            "running_count": 0,
            "floating": 0.0,
            "stats": dict(self.bot4.stats),
            "last_action": self.bot4.last_action,
            "last_error": self.bot4.last_error,
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
            "money_per_price": 0.0,
        }

    def _do_bot4_config(self, p: Dict[str, Any]) -> Dict[str, Any]:
        """Start / stop the pre-trade ladder bot or change its settings."""
        patch: Dict[str, Any] = {}
        if "running" in p:
            patch["running"] = bool(p["running"])
        if "numbering_lot" in p:
            patch["numbering_lot"] = bool(p["numbering_lot"])
        for key in ("lot", "price_gap", "pre_trade_gap", "pre_trade_value", "symbol",
                    "magic", "cooldown_sec", "slippage_points", "lock_pct"):
            if key in p and p[key] is not None and p[key] != "":
                patch[key] = p[key]
        if self.mode != "live" and patch.get("running"):
            return {"ok": False, "error": {"code": -1,
                                           "message": "Exness account login kora nei - age login korun",
                                           "hint": "The bot can only trade while the terminal is connected."}}
        if self.mode == "live" and patch.get("running"):
            sym = str(patch.get("symbol") or self.bot4.symbol or "")
            is_open, msg = self._is_market_open_for_symbol(sym)
            if not is_open:
                return {"ok": False, "error": {"code": -10011,
                                               "message": f"{sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে ({msg})",
                                               "hint": f"Market for {sym} is closed. Cannot start bot."}}
        self.bot4.update_config(patch)
        persisted = dict(cfg.CONFIG.get("bot4") or {})
        persisted.update({k: self.bot4.cfg[k] for k in patch})
        cfg.update_config(bot4=persisted)

        if self.mode == "live":
            try:
                if "symbol" in patch:
                    self.bot4.resolve_symbol(force=True)
                if not self.bot4.cfg.get("running") and hasattr(self.bot4, "on_stop"):
                    self.bot4.on_stop()
                self.bot4.tick()
            except Exception as exc:
                self._log_event("error", f"Bot4 config tick failed: {exc}")
            self.bot4_status = self.bot4.status()
        else:
            self.bot4_status = self._bot4_stub()
        if "running" in patch:
            self._log_event("info", ("Bot 4 STARTED" if self.bot4.cfg.get("running") else "Bot 4 STOPPED")
                            + f" (lot {self.bot4.cfg.get('lot')}, N {self.bot4.cfg.get('pre_trade_value')}, "
                              f"gap {self.bot4.cfg.get('price_gap')})")
        elif "numbering_lot" in patch:
            self._log_event("info", f"Bot 4 Numbering Lot Size: "
                                    f"{'ACTIVE' if self.bot4.cfg.get('numbering_lot') else 'DEACTIVATED'}")
        elif "symbol" in patch:
            self._log_event("info", f"Bot 4 Market selected: {self.bot4.symbol or patch['symbol']}")
        self._compute()
        self._publish(force=True)
        return {"ok": True, "bot4": self.bot4_status}

    def _do_bot4_close(self) -> Dict[str, Any]:
        closed = 0
        if self.mode == "live":
            try:
                closed = self.bot4.close_all(reason="dashboard")
            except Exception as exc:
                self._log_event("error", f"Bot4 Close all failed: {exc}")
            self.bot4_status = self.bot4.status()
        else:
            self.bot4_status = self._bot4_stub()
        persisted = dict(cfg.CONFIG.get("bot4") or {})
        persisted["running"] = False
        cfg.update_config(bot4=persisted)
        self.bot4.cfg["running"] = False
        self._log_event("warn", f"Bot 4 Close-now pressed: {closed} position(s) closed")
        self._compute()
        self._publish(force=True)
        return {"ok": True, "closed": closed, "bot4": self.bot4_status}

    # ------------------------------------------------------- bot 5 (news trader)
    def _bot5_stub(self) -> Dict[str, Any]:
        persisted = dict(cfg.CONFIG.get("bot5") or {})
        try:
            st = self.bot5.status()
            if persisted:
                st["config"] = {**st.get("config", {}), **persisted}
            return st
        except Exception:
            pass
        return {
            "running": bool(persisted.get("running")),
            "enabled": False,
            "symbol": "",
            "symbol_requested": str(self.bot5.cfg.get("symbol") or "XAUUSDm"),
            "symbol_ok": False,
            "symbol_substituted": "",
            "symbol_options": [],
            "ask": 0.0, "bid": 0.0, "spread": 0.0, "digits": 2,
            "news_total": 0,
            "feed_sources": [],
            "outlook": [],
            "sessions": (lambda: (self.bot5.session_board(time.time()) if hasattr(self.bot5, "session_board") else sess_mod.build_payload(
                time.time(),
                symbol=str(self.bot5.cfg.get("session_market") or self.bot5.cfg.get("symbol") or ""),
                lot=_num(self.bot5.cfg.get("lot"), 0.01),
                markets=self.bot5.available_symbols(),
                range_days=int(_num(self.bot5.cfg.get("session_range_days"), 20.0) or 20),
                source="clock")))(),
            "ai": ai_mod.build_payload(
                time.time(),
                symbol=str(self.bot5.cfg.get("ai_market") or self.bot5.cfg.get("symbol") or ""),
                lot=_num(self.bot5.cfg.get("lot"), 0.01),
                timeframe_key=str(self.bot5.cfg.get("ai_timeframe") or ai_mod.DEFAULT_TIMEFRAME),
                markets=self.bot5.available_symbols(),
                source="no terminal"),
            "mode": str(self.bot5.cfg.get("mode") or "anticipate"),
            "config": {**self.bot5.cfg, **persisted},
            "news": [],
            "active_event": None,
            "bias": {"side": "", "score": 0, "confident": False, "reason": "", "headline": ""},
            "plan": "",
            "straddle": {},
            "positions": [],
            "open": 0,
            "pending": 0,
            "floating": 0.0,
            "stats": dict(self.bot5.stats),
            "feed_source": self.bot5.feed.source,
            "feed_updated": int(self.bot5.feed.updated_at),
            "feed_error": self.bot5.feed.error,
            "feed_count": len(self.bot5.feed.events),
            "currencies": [],
            "last_action": self.bot5.last_action,
            "last_error": self.bot5.last_error,
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
            "money_per_price": 0.0,
        }

    def _do_bot5_config(self, p: Dict[str, Any]) -> Dict[str, Any]:
        """Start / stop the news trader or change its settings."""
        patch: Dict[str, Any] = {}
        if "running" in p:
            patch["running"] = bool(p["running"])
        if "auto_close_after_news" in p:
            patch["auto_close_after_news"] = bool(p["auto_close_after_news"])
        if "trade_weekend" in p:
            patch["trade_weekend"] = bool(p["trade_weekend"])
        if "manual_events" in p and isinstance(p["manual_events"], list):
            patch["manual_events"] = p["manual_events"]
        for key in ("lot", "symbol", "magic", "mode", "min_impact", "currencies",
                    "pre_minutes", "post_minutes", "min_gap_minutes", "tp", "sl",
                    "straddle_gap", "max_trades", "cooldown_sec", "slippage_points",
                    "bias_min_score", "refresh_min",
                    "session_market", "session_range_days",
                    "ai_market", "ai_timeframe"):
            # session_market / ai_market accept "" on purpose: it means "follow
            # the market of this bot" and must be settable back from the dashboard
            if key in p and p[key] is not None and (p[key] != "" or key in ("session_market", "ai_market")):
                patch[key] = p[key]
        if self.mode != "live" and patch.get("running"):
            return {"ok": False, "error": {"code": -1,
                                           "message": "Exness account login kora nei - age login korun",
                                           "hint": "The bot can only trade while the terminal is connected."}}
        if self.mode == "live" and patch.get("running"):
            sym = str(patch.get("symbol") or self.bot5.symbol or "")
            is_open, msg = self._is_market_open_for_symbol(sym)
            if not is_open:
                return {"ok": False, "error": {"code": -10011,
                                               "message": f"{sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে ({msg})",
                                               "hint": f"Market for {sym} is closed. Cannot start bot."}}
        self.bot5.update_config(patch)
        persisted = dict(cfg.CONFIG.get("bot5") or {})
        persisted.update({k: self.bot5.cfg[k] for k in patch})
        cfg.update_config(bot5=persisted)

        if self.mode == "live":
            try:
                if "symbol" in patch:
                    self.bot5.resolve_symbol(force=True)
                if not self.bot5.cfg.get("running") and hasattr(self.bot5, "on_stop"):
                    self.bot5.on_stop()
                self.bot5.tick()
            except Exception as exc:
                self._log_event("error", f"Bot5 config tick failed: {exc}")
            self.bot5_status = self.bot5.status()
        else:
            self.bot5_status = self._bot5_stub()
        if "running" in patch:
            self._log_event("info", ("Bot 5 STARTED" if self.bot5.cfg.get("running") else "Bot 5 STOPPED")
                            + f" (lot {self.bot5.cfg.get('lot')}, mode {self.bot5.cfg.get('mode')}, "
                              f"impact {self.bot5.cfg.get('min_impact')})")
        elif "symbol" in patch:
            self._log_event("info", f"Bot 5 Market selected: {self.bot5.symbol or patch['symbol']}")
        elif "mode" in patch:
            self._log_event("info", f"Bot 5 Trading style: {self.bot5.cfg.get('mode')}")
        self._compute()
        self._publish(force=True)
        return {"ok": True, "bot5": self.bot5_status}

    def _do_bot5_close(self) -> Dict[str, Any]:
        closed = 0
        if self.mode == "live":
            try:
                closed = self.bot5.close_all(reason="dashboard")
            except Exception as exc:
                self._log_event("error", f"Bot5 Close all failed: {exc}")
            self.bot5_status = self.bot5.status()
        else:
            self.bot5_status = self._bot5_stub()
        persisted = dict(cfg.CONFIG.get("bot5") or {})
        persisted["running"] = False
        cfg.update_config(bot5=persisted)
        self.bot5.cfg["running"] = False
        self._log_event("warn", f"Bot 5 Close-now pressed: {closed} position(s) closed")
        self._compute()
        self._publish(force=True)
        return {"ok": True, "closed": closed, "bot5": self.bot5_status}

    # ------------------------------------------------- bot 6 (pre-trade entry)
    def _bot6_stub(self) -> Dict[str, Any]:
        persisted = dict(cfg.CONFIG.get("bot6") or {})
        try:
            st = self.bot6.status()
            if persisted:
                st["config"] = {**st.get("config", {}), **persisted}
            return st
        except Exception:
            pass
        return {
            "running": bool(persisted.get("running")),
            "enabled": False,
            "symbol": "",
            "symbol_requested": str(self.bot6.cfg.get("symbol") or "XAUUSDm"),
            "ai": dict(getattr(self.bot6, "ai", {}) or {}),
            "symbol_ok": False,
            "symbol_substituted": "",
            "symbol_options": [],
            "ask": 0.0, "bid": 0.0, "spread": 0.0, "digits": 2, "price": 0.0,
            "config": {**self.bot6.cfg, **persisted},
            "entry": {},
            "entries": [],
            "pending_count": 0,
            "filled_count": 0,
            "positions": [],
            "running_count": 0,
            "floating": 0.0,
            "stats": dict(self.bot6.stats),
            "watch": dict(getattr(self.bot6, "watch", {}) or {}),
            "watch_min_pct": float((persisted or self.bot6.cfg).get("watch_min_pct") or 60.0),
            "rules": {"tp": "off", "sl": "profit-lock 70% (min $0.80)",
                      "tp_money": 0.0, "sl_money": 0.0, "lock_pct": 70.0, "lock_min_profit": 0.8},
            "last_action": self.bot6.last_action,
            "last_error": self.bot6.last_error,
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
            "money_per_price": 0.0,
        }

    def _do_bot6_config(self, p: Dict[str, Any]) -> Dict[str, Any]:
        """Start / stop the pre-trade entry bot or change its fields."""
        patch: Dict[str, Any] = {}
        if "running" in p:
            patch["running"] = bool(p["running"])
        if "watch_gate" in p:
            patch["watch_gate"] = bool(p["watch_gate"])
        if "ai_gate" in p:
            patch["ai_gate"] = bool(p["ai_gate"])
        for key in ("lot", "distance", "symbol", "magic", "side", "watch_min_pct",
                    "ai_gate", "tp", "sl", "lock_pct", "lock_min_profit", "cooldown_sec",
                    "slippage_points"):
            if key in p and p[key] is not None and p[key] != "":
                patch[key] = p[key]
        if self.mode != "live" and patch.get("running"):
            return {"ok": False, "error": {"code": -1,
                                           "message": "Exness account login kora nei - age login korun",
                                           "hint": "The bot can only trade while the terminal is connected."}}
        if self.mode == "live" and patch.get("running"):
            sym = str(patch.get("symbol") or self.bot6.symbol or "")
            is_open, msg = self._is_market_open_for_symbol(sym)
            if not is_open:
                return {"ok": False, "error": {"code": -10011,
                                               "message": f"{sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে ({msg})",
                                               "hint": f"Market for {sym} is closed. Cannot start bot."}}
        self.bot6.update_config(patch)
        persisted = dict(cfg.CONFIG.get("bot6") or {})
        persisted.update({k: self.bot6.cfg[k] for k in patch})
        cfg.update_config(bot6=persisted)

        if self.mode == "live":
            try:
                if "symbol" in patch:
                    self.bot6.resolve_symbol(force=True)
                self.bot6.tick()
            except Exception as exc:
                self._log_event("error", f"Bot6 config tick failed: {exc}")
            self.bot6_status = self.bot6.status()
        else:
            self.bot6_status = self._bot6_stub()
        if "running" in patch:
            self._log_event("info", ("Bot 6 STARTED" if self.bot6.cfg.get("running") else "Bot 6 STOPPED")
                            + f" (lot {self.bot6.cfg.get('lot')}, distance {self.bot6.cfg.get('distance')}, "
                              f"side {self.bot6.cfg.get('side')})")
        elif "symbol" in patch:
            self._log_event("info", f"Bot 6 Market selected: {self.bot6.symbol or patch['symbol']}")
        self._compute()
        self._publish(force=True)
        return {"ok": True, "bot6": self.bot6_status}

    def _do_bot6_close(self) -> Dict[str, Any]:
        closed = 0
        if self.mode == "live":
            try:
                closed = self.bot6.close_all(reason="dashboard")
            except Exception as exc:
                self._log_event("error", f"Bot6 Close all failed: {exc}")
            self.bot6_status = self.bot6.status()
        else:
            self.bot6_status = self._bot6_stub()
        persisted = dict(cfg.CONFIG.get("bot6") or {})
        persisted["running"] = False
        cfg.update_config(bot6=persisted)
        self.bot6.cfg["running"] = False
        self._log_event("warn", f"Bot 6 Close-now pressed: {closed} position(s) closed")
        self._compute()
        self._publish(force=True)
        return {"ok": True, "closed": closed, "bot6": self.bot6_status}

    # ------------------------------------------------- bot 7 (reversal candle)
    def _bot7_stub(self) -> Dict[str, Any]:
        persisted = dict(cfg.CONFIG.get("bot7") or {})
        try:
            st = self.bot7.status()
            if persisted:
                st["config"] = {**st.get("config", {}), **persisted}
            return st
        except Exception:
            pass
        return {
            "running": bool(persisted.get("running")),
            "enabled": False,
            "symbol": "",
            "symbol_requested": str(self.bot7.cfg.get("symbol") or "XAUUSDm"),
            "ai": dict(getattr(self.bot7, "ai", {}) or {}),
            "symbol_ok": False,
            "symbol_substituted": "",
            "symbol_options": [],
            "ask": 0.0, "bid": 0.0, "spread": 0.0, "digits": 2, "price": 0.0,
            "config": {**self.bot7.cfg, **persisted},
            "candle": {},
            "trigger": {"candle_time": 0, "fired": False, "reverse_dist": 0.0, "rates_error": ""},
            "positions": [],
            "running_count": 0,
            "floating": 0.0,
            "stats": dict(self.bot7.stats),
            "watch": dict(getattr(self.bot7, "watch", {}) or {}),
            "watch_min_pct": float((persisted or self.bot7.cfg).get("watch_min_pct") or 60.0),
            "rules": {"tp": "off", "sl": "profit-lock 70% (min $0.80)",
                      "tp_money": 0.0, "sl_money": 0.0, "lock_pct": 70.0},
            "last_action": self.bot7.last_action,
            "last_error": self.bot7.last_error,
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
            "money_per_price": 0.0,
        }

    def _do_bot7_config(self, p: Dict[str, Any]) -> Dict[str, Any]:
        """Start / stop the reversal candle bot or change its fields."""
        patch: Dict[str, Any] = {}
        if "running" in p:
            patch["running"] = bool(p["running"])
        if "watch_gate" in p:
            patch["watch_gate"] = bool(p["watch_gate"])
        if "ai_gate" in p:
            patch["ai_gate"] = bool(p["ai_gate"])
        for key in ("lot", "symbol", "magic", "timeframe", "reverse_dist", "min_body",
                    "tp", "sl", "lock_pct", "lock_min_profit", "watch_min_pct", "ai_gate",
                    "max_trades", "cooldown_sec", "slippage_points"):
            if key in p and p[key] is not None and p[key] != "":
                patch[key] = p[key]
        if self.mode != "live" and patch.get("running"):
            return {"ok": False, "error": {"code": -1,
                                           "message": "Exness account login kora nei - age login korun",
                                           "hint": "The bot can only trade while the terminal is connected."}}
        if self.mode == "live" and patch.get("running"):
            sym = str(patch.get("symbol") or self.bot7.symbol or "")
            is_open, msg = self._is_market_open_for_symbol(sym)
            if not is_open:
                return {"ok": False, "error": {"code": -10011,
                                               "message": f"{sym} মার্কেটটি বর্তমানে বন্ধ রয়েছে ({msg})",
                                               "hint": f"Market for {sym} is closed. Cannot start bot."}}
        self.bot7.update_config(patch)
        persisted = dict(cfg.CONFIG.get("bot7") or {})
        persisted.update({k: self.bot7.cfg[k] for k in patch})
        cfg.update_config(bot7=persisted)

        if self.mode == "live":
            try:
                if "symbol" in patch:
                    self.bot7.resolve_symbol(force=True)
                self.bot7.tick()
            except Exception as exc:
                self._log_event("error", f"Bot7 config tick failed: {exc}")
            self.bot7_status = self.bot7.status()
        else:
            self.bot7_status = self._bot7_stub()
        if "running" in patch:
            self._log_event("info", ("Bot 7 STARTED" if self.bot7.cfg.get("running") else "Bot 7 STOPPED")
                            + f" (lot {self.bot7.cfg.get('lot')}, {self.bot7.cfg.get('timeframe')}, "
                              f"reverse {self.bot7.cfg.get('reverse_dist')})")
        elif "symbol" in patch:
            self._log_event("info", f"Bot 7 Market selected: {self.bot7.symbol or patch['symbol']}")
        self._compute()
        self._publish(force=True)
        return {"ok": True, "bot7": self.bot7_status}

    def _do_bot7_close(self) -> Dict[str, Any]:
        closed = 0
        if self.mode == "live":
            try:
                closed = self.bot7.close_all(reason="dashboard")
            except Exception as exc:
                self._log_event("error", f"Bot7 Close all failed: {exc}")
            self.bot7_status = self.bot7.status()
        else:
            self.bot7_status = self._bot7_stub()
        persisted = dict(cfg.CONFIG.get("bot7") or {})
        persisted["running"] = False
        cfg.update_config(bot7=persisted)
        self.bot7.cfg["running"] = False
        self._log_event("warn", f"Bot 7 Close-now pressed: {closed} position(s) closed")
        self._compute()
        self._publish(force=True)
        return {"ok": True, "closed": closed, "bot7": self.bot7_status}

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
                    # the EA may send `account` as a dict ({login: ...}) or a
                    # scalar login - resolve the dict first or a stringified
                    # dict becomes the account key and the trade vanishes
                    acct = str((account or {}).get("login") or p.get("account") or self._account_key())
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
            # A transient IPC drop can clear the flag. Try the same quiet
            # re-attach before parking the user on an error screen.
            if not self._auto_heal_terminal():
                self.status = "error"
                self.error = {"code": -1, "message": "Connection to the MT5 terminal was lost",
                              "hint": "Re-open the terminal, then log in again."}
                self.mode = None
                self.account_cache = None
                self.positions_cache = []
                return
        acc = self.mt5.account()
        if acc is None:
            # The meta bridge can drop silently (terminal restart/update).
            # Re-attach a few times per minute before naming it an error.
            if self._auto_heal_terminal():
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

        # ---------------- auto-trading bot 4 (pre-trade ladder) ----------------
        try:
            self.bot4.update_config((cfg.CONFIG.get("bot4") or {}))
            if not self.bot4.symbol:
                self.bot4.resolve_symbol()
            if self.bot4.cfg.get("running") or self.bot4.enabled:
                self.bot4.tick()
            self.bot4_status = self.bot4.status()
        except Exception as exc:
            self._log_event("error", f"Bot4 error: {exc}")
            self.bot4_status = {"running": bool(self.bot4.cfg.get("running")), "error": str(exc)}

        # ---------------- auto-trading bot 6 (pre-trade entry) ----------------
        try:
            self.bot6.update_config((cfg.CONFIG.get("bot6") or {}))
            if not self.bot6.symbol:
                self.bot6.resolve_symbol()
            if self.bot6.cfg.get("running") or self.bot6.enabled:
                self.bot6.tick()
            self.bot6_status = self.bot6.status()
        except Exception as exc:
            self._log_event("error", f"Bot6 error: {exc}")
            self.bot6_status = {"running": bool(self.bot6.cfg.get("running")), "error": str(exc)}

        # ---------------- auto-trading bot 7 (reversal candle) ----------------
        try:
            self.bot7.update_config((cfg.CONFIG.get("bot7") or {}))
            if not self.bot7.symbol:
                self.bot7.resolve_symbol()
            if self.bot7.cfg.get("running") or self.bot7.enabled:
                self.bot7.tick()
            self.bot7_status = self.bot7.status()
        except Exception as exc:
            self._log_event("error", f"Bot7 error: {exc}")
            self.bot7_status = {"running": bool(self.bot7.cfg.get("running")), "error": str(exc)}

        # ---------------- auto-trading bot 5 (news trader) ----------------
        try:
            self.bot5.update_config((cfg.CONFIG.get("bot5") or {}))
            if not self.bot5.symbol:
                self.bot5.resolve_symbol()
            # the calendar keeps refreshing even while the bot is stopped, so the
            # dashboard always shows live news from every site (incl. Google)
            self.bot5.feed.ensure_started()
            if self.bot5.cfg.get("running") or self.bot5.enabled:
                self.bot5.tick()
            self.bot5_status = self.bot5.status()
        except Exception as exc:
            self._log_event("error", f"Bot5 error: {exc}")
            self.bot5_status = {"running": bool(self.bot5.cfg.get("running")), "error": str(exc)}

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
            if self.status != "connected":
                self.status = "disconnected"
            self.ea_positions = []
            return
        if self.ea_last_seen and (time.time() - self.ea_last_seen) > EA_STALE_SECONDS:
            if self.ea_positions:
                self._log_event("warn", "EA bridge went quiet - open positions cleared")
            self.ea_positions = []
        if self.mode is None and self.status not in ("connecting", "connected"):
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
        # broker epochs are already "server wall clock" numbers - flooring to
        # UTC midnight of that number gives broker midnight. Interpreting the
        # number in the *local* timezone (fromtimestamp) shifts "today" by the
        # local-vs-broker offset.
        epoch = int(ts if ts is not None else self._server_now())
        return epoch - (epoch % DAY)

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
            "bot4": self.bot4_status or self._bot4_stub(),
            "bot5": self.bot5_status or self._bot5_stub(),
            "bot6": self.bot6_status or self._bot6_stub(),
            "bot7": self.bot7_status or self._bot7_stub(),
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
            "market_watch": (_mw_res := get_all_market_watches(extra=self._flow_market_list())).get("watches", []),
            "market_statuses": _mw_res.get("market_statuses", {}),
            "now_ms": int(time.time() * 1000),
        }
        self._attach_price_maps(self._snapshot_data)

    # ------------------------------------------------------- price map helper
    def _build_map(self, magic: int, symbol: str, price: float,
                   positions: List[Dict[str, Any]], orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Every level a bot has 'set' on this market: pending orders + open positions."""
        if not magic:
            return []
        rows: List[Dict[str, Any]] = []
        for o in orders or []:
            if int(o.get("magic") or 0) != magic:
                continue
            if symbol and o.get("symbol") != symbol:
                continue
            kind = str(o.get("kind") or "pending")
            side = "buy" if kind.startswith("buy") else "sell" if kind.startswith("sell") else ""
            po = float(o.get("price_open") or 0.0)
            rows.append({
                "kind": side, "type": "pending", "label": kind.upper(),
                "price": po, "lot": float(o.get("volume") or 0.0),
                "sl": float(o.get("sl") or 0.0), "tp": float(o.get("tp") or 0.0),
                "state": "pending", "net": 0.0,
                "dist": round(po - price, 6) if price else 0.0,
                "comment": str(o.get("comment") or ""),
            })
        for p in positions or []:
            if int(p.get("magic") or 0) != magic:
                continue
            if symbol and p.get("symbol") != symbol:
                continue
            side = str(p.get("side") or "")
            po = float(p.get("price_open") or 0.0)
            rows.append({
                "kind": side, "type": "position", "label": f"{side.upper()} OPEN".strip(),
                "price": po, "lot": float(p.get("volume") or 0.0),
                "sl": float(p.get("sl") or 0.0), "tp": float(p.get("tp") or 0.0),
                "state": "open", "net": float(p.get("net") or 0.0),
                "dist": round(po - price, 6) if price else 0.0,
                "comment": str(p.get("comment") or ""),
            })
        rows.sort(key=lambda r: r["price"], reverse=True)
        return rows

    def _attach_price_maps(self, snap: Dict[str, Any]) -> None:
        positions = snap.get("positions") or []
        orders = snap.get("orders") or []
        for key in ("bot", "bot2", "bot3", "bot4", "bot5", "bot6", "bot7"):
            status = snap.get(key)
            if not isinstance(status, dict):
                continue
            conf = status.get("config") or {}
            magic = int(_num(conf.get("magic"), 0))
            symbol = str(status.get("symbol") or "")
            price = _num(status.get("price"), 0.0) or _num(status.get("ask"), 0.0)
            if not price:
                for p in positions:
                    if p.get("symbol") == symbol:
                        price = _num(p.get("price_current"), 0.0)
                        if price:
                            break
            status["map"] = self._build_map(magic, symbol, price, positions, orders)
            status["map_price"] = round(price, 6)

    _HEAL_MIN_GAP = 15.0

    def _auto_heal_terminal(self) -> bool:
        """Quietly re-attaches the dashboard to the Exness terminal.

        The MetaTrader5 IPC bridge can silently lose its attach (terminal
        restart / update / brief close). Re-initializing the SAME terminal
        restores the session without a stored password, so the dashboard keeps
        streaming instead of parking the user on the login screen. Rate-limited
        so a genuinely missing terminal stays a calm retry, never a busy loop.
        """
        now = time.time()
        if now - getattr(self, "_last_heal_at", 0.0) < self._HEAL_MIN_GAP:
            return False
        self._last_heal_at = now
        try:
            ok = self.mt5.try_recover()
        except Exception:
            ok = False
        if ok:
            self.status = "connected"
            self.error = None
            self._log_event("success", "Exness terminal re-connected (auto)")
        else:
            self._log_event("warn",
                            "Exness terminal offline - auto re-connect will keep trying every 15s")
        return ok

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
        bot5 = data.get("bot5") or {}
        bot6 = data.get("bot6") or {}
        bot7 = data.get("bot7") or {}
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
            # bot 5: news list changes (events expiring / new ones arriving) and
            # its market price must reach the browser immediately
            bot5.get("symbol"), bot5.get("running"), bot5.get("ask"),
            bot5.get("news_total"), len(bot5.get("news") or []),
            bot5.get("plan"),
            tuple((o.get("market"), o.get("verdict")) for o in (bot5.get("outlook") or [])),
            # sessions board: selected market, live state of every session and the
            # 10-minute AI refresh must reach the browser without waiting for a tick
            (bot5.get("sessions") or {}).get("symbol"),
            (bot5.get("sessions") or {}).get("updated"),
            (bot5.get("sessions") or {}).get("source"),
            tuple((s.get("key"), s.get("state"), s.get("open_ts"))
                  for s in ((bot5.get("sessions") or {}).get("sessions") or [])),
            # AI market analysis: the selected market / timeframe, its per-timeframe
            # verdicts and every target price must reach the browser live
            (bot5.get("ai") or {}).get("symbol"),
            (bot5.get("ai") or {}).get("timeframe"),
            (bot5.get("ai") or {}).get("updated"),
            (bot5.get("ai") or {}).get("price"),
            (bot5.get("ai") or {}).get("source"),
            tuple((r.get("key"), r.get("verdict"), r.get("score"), r.get("rsi"), r.get("atr"))
                  for r in ((bot5.get("ai") or {}).get("rows") or [])),
            ((bot5.get("ai") or {}).get("combined") or {}).get("verdict"),
            ((bot5.get("ai") or {}).get("combined") or {}).get("confidence"),
            ((bot5.get("ai") or {}).get("target") or {}).get("up_price"),
            ((bot5.get("ai") or {}).get("target") or {}).get("down_price"),
            # bot 6 / bot 7: their live price, state and parked levels must reach
            # the browser on the same frame as everything else
            bot6.get("symbol"), bot6.get("symbol_requested"), bot6.get("running"),
            bot6.get("ask"), bot6.get("pending_count"), len(bot6.get("positions") or []),
            bot6.get("floating"), bot6.get("watch", {}).get("allowed"),
            (bot6.get("ai") or {}).get("verdict"), (bot6.get("ai") or {}).get("allowed"),
            bot7.get("symbol"), bot7.get("symbol_requested"), bot7.get("running"),
            bot7.get("ask"), len(bot7.get("positions") or []), bot7.get("floating"),
            bot7.get("trigger", {}).get("candle_time"),
            (bot7.get("ai") or {}).get("verdict"), (bot7.get("ai") or {}).get("allowed"),
        )
        if not force and sig == self._last_signature and (time.time() - self._last_publish_ts) < 1.0:
            return
        self._last_signature = sig
        self._last_publish_ts = time.time()
        # The news calendar is ~1 MB (460 rows of headlines, forecasts and
        # impacted markets). It changes a few times a minute, yet it rode along
        # on every state frame - 90% of all socket traffic, serialised on the
        # event loop, which is what made the dashboard stutter. Publish it only
        # when it actually changed; the browser keeps the previous list.
        wire = payload
        b5 = payload.get("bot5")
        if isinstance(b5, dict):
            news = b5.get("news")
            if isinstance(news, list):
                first = news[0] if news else {}
                news_sig = (len(news), b5.get("feed_updated"), b5.get("news_total"),
                            first.get("ts"), first.get("side"), first.get("traded"),
                            first.get("past"))
                if news_sig == getattr(self, "_last_news_sig", None):
                    wire = dict(payload)
                    thin = dict(b5)
                    thin.pop("news", None)
                    thin["news_omitted"] = True
                    wire["bot5"] = thin
                else:
                    self._last_news_sig = news_sig
        try:
            self.publisher(wire, force)
        except Exception:
            pass

    def _flow_market_list(self) -> List[str]:
        """The markets the flow pipeline is currently analysing.

        The dashboard's pipeline follows one market: the AI board market is the
        user's selection, and the sessions board can be set separately. Both are
        added to the watch set so the single dial that is shown always receives
        live order-flow numbers instead of an empty card.
        """
        try:
            out: List[str] = []
            for key in ("ai_market", "session_market"):
                sym = str(self.bot5.cfg.get(key) or "").strip()
                if sym and sym not in out:
                    out.append(sym)
            return out
        except Exception:
            return []

    def _watch_pressure_for(self, symbol: str) -> Dict[str, Any]:
        """Live watch-dial pressure lookup for the trading bots.

        Returns the freshest dial row for symbol; empty dict when the dial has no
        data (the bot then treats it as NEUTRAL and stays flat). Synthetic
        fallback rows (live=False, fabricated while the terminal glitches) are
        withheld - a bot must never act on invented pressure numbers.
        """
        sym = str(symbol or "").strip().upper()
        if not sym:
            return {}
        data = getattr(self, "_snapshot_data", None) or {}
        watches = data.get("market_watch") or []
        # 1. Exact symbol match
        for w in watches:
            if str(w.get("symbol") or "").strip().upper() == sym:
                if w.get("live") is False:
                    return {}
                return w
        # 2. Display name match
        for w in watches:
            wdname = str(w.get("displayName") or "").strip().upper().replace(" ", "")
            if wdname == sym.replace(" ", ""):
                if w.get("live") is False:
                    return {}
                return w
        # 3. Normalized match (strip suffix like 'M', '.', '247')
        sym_norm = sym.rstrip("M.").replace("247", "")
        for w in watches:
            wsym = str(w.get("symbol") or "").strip().upper().rstrip("M.").replace("247", "")
            if wsym == sym_norm:
                if w.get("live") is False:
                    return {}
                return w
        return {}

    def _ai_outlook_for(self, symbol: str) -> Dict[str, Any]:
        """The news engine's AI market-direction verdict for this symbol.

        The news trader (bot 5) reads every news source and publishes one verdict
        per market. Bots 6 and 7 ask for it before entering so a trade is never
        taken straight against everything the news is saying. An unknown market
        returns {} - unknown simply means "not blocked".
        """
        sym = str(symbol or "").strip().upper()
        if not sym:
            return {}
        data = getattr(self, "_snapshot_data", None) or {}
        rows = ((data.get("bot5") or {}).get("outlook")) or []

        def norm(v: str) -> str:
            return str(v or "").strip().upper().rstrip("M.").replace("247", "")

        target = norm(sym)
        for row in rows:
            market = str(row.get("market") or "")
            if not market:
                continue
            if market.upper() == sym or norm(market) == target:
                return row
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
