"""
FastAPI application: REST + WebSocket + static dashboard.

Run:  python backend/app.py       (or start.bat)
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

# allow "python backend/app.py" as well as "uvicorn backend.app:app"
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from backend import config as cfg
    from backend.engine import Engine
    from backend.mt5_client import find_terminals, package_available
    from backend.store import Store
    from backend.market_watch import get_all_market_watches
    from backend.chart_feed import ChartUnavailable, TF_SEC as CHART_TFS, get_feed
    from backend import bd_feed
else:
    from . import config as cfg
    from .engine import Engine
    from .mt5_client import find_terminals, package_available
    from .store import Store
    from .market_watch import get_all_market_watches
    from .chart_feed import ChartUnavailable, TF_SEC as CHART_TFS, get_feed
    from . import bd_feed

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

if sys.platform == "win32":
    # Prevent Windows ProactorEventLoop ConnectionResetError [WinError 10054] crash on browser tab close/reload
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport

        _orig_call_conn_lost = _ProactorBasePipeTransport._call_connection_lost

        def _patched_call_conn_lost(self, exc):
            try:
                _orig_call_conn_lost(self, exc)
            except (ConnectionResetError, BrokenPipeError):
                pass
            except OSError as e:
                if getattr(e, "winerror", None) == 10054:
                    pass
                else:
                    raise

        _ProactorBasePipeTransport._call_connection_lost = _patched_call_conn_lost
    except Exception:
        pass

# Prevent websockets legacy protocol AssertionError in _drain_helper during concurrent close/write
try:
    import websockets.legacy.protocol as _ws_proto

    _orig_drain_helper = _ws_proto.WebSocketCommonProtocol._drain_helper

    async def _patched_drain_helper(self):
        if self.connection_lost_waiter.done():
            raise ConnectionResetError("Connection lost")
        if not self._paused:
            return
        waiter = self._drain_waiter
        if waiter is not None and not waiter.cancelled() and not waiter.done():
            try:
                await waiter
            except Exception:
                pass
            return
        waiter = self.loop.create_future()
        self._drain_waiter = waiter
        await waiter

    _ws_proto.WebSocketCommonProtocol._drain_helper = _patched_drain_helper
except Exception:
    pass

store = Store(cfg.DB_FILE)


class Hub:
    """Fan-out engine snapshots to every connected browser."""

    def __init__(self) -> None:
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.clients: set[WebSocket] = set()
        self._locks: dict[WebSocket, asyncio.Lock] = {}
        self._pending_state: dict[WebSocket, Dict[str, Any]] = {}
        self._in_flight: set[WebSocket] = set()
        self._last: Optional[Dict[str, Any]] = None
        self._last_watch: Optional[Dict[str, Any]] = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    async def register(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        self._locks[ws] = asyncio.Lock()
        if self._last:
            try:
                await self._send_safe(ws, {"type": "state", "data": self._last})
            except Exception:
                pass
        if self._last_watch:
            try:
                await self._send_safe(ws, {"type": "watch", "data": self._last_watch})
            except Exception:
                pass

    def unregister(self, ws: WebSocket) -> None:
        self.clients.discard(ws)
        self._locks.pop(ws, None)
        self._pending_state.pop(ws, None)
        self._in_flight.discard(ws)

    async def _send_safe(self, ws: WebSocket, msg: Dict[str, Any]) -> None:
        if ws not in self.clients:
            self._in_flight.discard(ws)
            return
        lock = self._locks.get(ws)
        if not lock:
            self._in_flight.discard(ws)
            return
        try:
            async with lock:
                if ws in self.clients:
                    await ws.send_json(msg)
                while ws in self.clients and ws in self._pending_state:
                    pending = self._pending_state.pop(ws, None)
                    if pending and ws in self.clients:
                        await ws.send_json(pending)
        except Exception:
            self.unregister(ws)
        finally:
            self._in_flight.discard(ws)

    # called from the engine thread
    def publish(self, payload: Dict[str, Any], force: bool = False) -> None:
        self._last = payload
        if not self.loop or not self.clients:
            return
        msg = {"type": "state", "data": payload}
        try:
            self.loop.call_soon_threadsafe(self._broadcast, msg)
        except Exception:
            pass

    # called from the engine thread
    def publish_watch(self, payload: Dict[str, Any]) -> None:
        """Light live frame carrying only the market pressure watch dials."""
        self._last_watch = payload
        if not self.loop or not self.clients:
            return
        msg = {"type": "watch", "data": payload}
        try:
            self.loop.call_soon_threadsafe(self._broadcast, msg)
        except Exception:
            pass

    def _broadcast(self, msg: Dict[str, Any]) -> None:
        if not self.clients:
            return
        mtype = msg.get("type")
        for ws in list(self.clients):
            if ws not in self.clients:
                continue
            if ws in self._in_flight:
                # If socket is busy sending, drop high-frequency watch frames
                if mtype == "watch":
                    continue
                # If state snapshot arrives, buffer only the latest state
                if mtype == "state":
                    self._pending_state[ws] = msg
                continue
            self._in_flight.add(ws)
            asyncio.create_task(self._send_safe(ws, msg))

    def push_event(self, kind: str, message: str) -> None:
        if not self.loop or not self.clients:
            return
        msg = {"type": "notify", "data": {"kind": kind, "message": message, "ts": int(time.time() * 1000)}}
        try:
            self.loop.call_soon_threadsafe(self._broadcast, msg)
        except Exception:
            pass



hub = Hub()
engine = Engine(store, publisher=hub.publish, watch_publisher=hub.publish_watch,
                watch_active=lambda: len(hub.clients) > 0)


def current_account_key() -> str:
    """Login of the account currently on screen ('' when logged out)."""
    snap = engine.snapshot()
    acc = snap.get("account") or {}
    return str(acc.get("login") or "")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    hub.bind(asyncio.get_running_loop())
    engine.start()
    # warm the store so history is visible immediately after a restart
    engine.submit("reload_history", {}, timeout=20)
    try:
        yield
    finally:
        engine.stop()
        chart_feed.stop()


app = FastAPI(title=cfg.APP_NAME, version=cfg.VERSION, docs_url="/api/docs",
              redoc_url=None, lifespan=lifespan)
# No CORS middleware on purpose: the dashboard is served by this same server
# (same-origin) and the EA is a non-browser client, so nothing legitimate needs
# cross-origin access. Without it, a browser sandbox blocks every cross-origin
# read of the local API - an open web page can no longer probe /api/* or drive
# the live account.


@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in ("/", "/bd", "/bd.html") or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/api/config")
async def api_config() -> Dict[str, Any]:
    # the lookup can hit a (rate-limited) full drive scan - keep it off the
    # event loop so the dashboard never stalls while it runs
    terminals = await asyncio.to_thread(find_terminals)
    exness_terminals = [p for p in terminals if "exness" in p.lower()]
    return {
        "app": cfg.APP_NAME,
        "version": cfg.VERSION,
        "terminal_path": cfg.CONFIG.get("terminal_path", ""),
        "terminals": terminals,
        "exness_terminals": exness_terminals,
        "mt5_ready": bool(terminals),
        "mt5_any_terminal": bool(terminals),
        "mt5_exness_branded": bool(exness_terminals),
        "mt5_package": package_available(),
        "exness_download": "https://www.exness.com/metatrader-5/",
        "mt5_download": "https://www.metatrader5.com/en/download",
        "remember_login": cfg.CONFIG.get("remember_login", True),
        "bots_enabled": BOT_TRADING_ENABLED,
        "saved_login": cfg.CONFIG.get("saved_login", ""),
        "saved_server": cfg.CONFIG.get("saved_server", ""),
        "poll_interval_ms": cfg.CONFIG.get("poll_interval_ms", 400),
        "history_days": cfg.CONFIG.get("history_days", 60),
        "port": cfg.CONFIG.get("port", 8777),
        "servers": cfg.EXNESS_SERVERS,
        "common_servers": cfg.server_list(),
        "known_servers": cfg.known_servers(),
    }


@app.post("/api/ea-token")
async def api_ea_token() -> Dict[str, Any]:
    """EA bridge token for the DashboardBridge inputs.

    POST-only on purpose: a cross-origin page cannot send a JSON body without a
    preflight (there is no CORS middleware), so the token cannot be skimmed by
    drive-by scripts the way it could from a plain GET.
    """
    return {"ea_secret": cfg.CONFIG.get("ea_secret", "")}


@app.get("/api/state")
async def api_state() -> Dict[str, Any]:
    snap = engine.snapshot()
    if not snap:
        # engine has not produced a snapshot yet
        await asyncio.sleep(0.25)
        snap = engine.snapshot()
    return snap


@app.get("/api/terminals")
async def api_terminals(refresh: bool = False) -> Dict[str, Any]:
    return {"terminals": await asyncio.to_thread(find_terminals, refresh)}


# ------------------------------------------------------------------ market watch
BD_PAGE = cfg.WEB_DIR / "bd.html"


@app.get("/bd")
@app.get("/bd.html")
async def bd_page() -> FileResponse:
    """BD Stock Market dashboard - shares the codebase of the Exness
    dashboard but never touches the Exness page or its data sources."""
    return FileResponse(str(BD_PAGE), media_type="text/html; charset=utf-8")


@app.get("/api/bd/watch")
async def api_bd_watch() -> Dict[str, Any]:
    """Buyer vs seller pressure rows for every BD market (light 1 Hz frame)."""
    return await asyncio.to_thread(bd_feed.watch_payload)


@app.get("/api/bd/state")
async def api_bd_state(symbol: str = Query("DSEX")) -> Dict[str, Any]:
    """Hero + AI board + outlook + setups + sessions + next event + curve."""
    return await asyncio.to_thread(bd_feed.state, symbol)


@app.get("/api/market-watch")
async def api_market_watch(force: bool = False) -> Dict[str, Any]:
    """Buyer vs seller pressure for all active markets.

    The dashboard normally gets this data pushed live inside the WebSocket state
    snapshot every engine tick; this endpoint is only a one-shot seed / manual
    refresh path (force=1 recomputes immediately).
    """
    extra = engine._flow_market_list() if hasattr(engine, "_flow_market_list") else []
    return await asyncio.to_thread(get_all_market_watches, None, force, extra)


# ------------------------------------------------------------------ account
@app.post("/api/connect")
async def api_connect(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Log in to an Exness MT5 account (real or demo). Nothing else gets you in."""
    login = str(payload.get("login") or "").strip()
    server = str(payload.get("server") or "").strip()
    if not login:
        raise HTTPException(400, "Exness account login din")
    if not server:
        raise HTTPException(400, "Server din (example: Exness-MT5Real6)")
    res = await asyncio.to_thread(engine.submit, "connect", payload, 120.0)
    if not res.get("ok"):
        # a failed attempt must not be remembered, otherwise a wrong server
        # would keep pre-filling the form
        return JSONResponse(status_code=400, content=res)
    if payload.get("remember", True):
        cfg.update_config(saved_login=login, saved_server=server, remember_login=True,
                          terminal_path=str(payload.get("path", "") or cfg.CONFIG.get("terminal_path", "")))
    return res


@app.post("/api/disconnect")
async def api_disconnect() -> Dict[str, Any]:
    """Full logout: MT5 session closed, live state wiped, UI returns to login."""
    return await asyncio.to_thread(engine.submit, "disconnect", {}, 20.0)


@app.post("/api/settings")
async def api_settings(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    allowed = {k: v for k, v in payload.items()
               if k in ("poll_interval_ms", "history_interval_ms", "history_days", "terminal_path",
                        "watch_tick_ms")}
    return await asyncio.to_thread(engine.submit, "settings", allowed, 20.0)


# ---------------------------------------------------------------- history
@app.get("/api/history")
async def api_history(
    days: int = Query(0, ge=0, le=3650),
    since: Optional[int] = None,
    until: Optional[int] = None,
    symbol: Optional[str] = None,
    result: Optional[str] = Query(None, pattern="^(win|loss)$"),
    limit: int = Query(500, ge=1, le=20000),
    offset: int = Query(0, ge=0),
    order: str = Query("DESC", pattern="^(?i:asc|desc)$"),
    all_accounts: bool = False,
) -> Dict[str, Any]:
    """Closed trades of the account that is currently logged in."""
    acc = current_account_key()
    if not acc and not all_accounts:
        return {"trades": [], "count": 0, "total_ever": 0, "symbols": [], "account": None}
    since_ts = since
    if since_ts is None and days:
        since_ts = int(time.time() - days * 86400)
    account_filter = None if all_accounts else acc
    rows = await asyncio.to_thread(
        store.get_trades, since_ts, until, symbol, result, limit, offset, order, account_filter
    )
    total = await asyncio.to_thread(store.count_trades, account_filter)
    return {"trades": rows, "count": len(rows), "total_ever": total, "account": acc,
            "symbols": await asyncio.to_thread(store.symbols, account_filter)}


@app.get("/api/export.csv")
async def api_export(days: int = 90, symbol: Optional[str] = None) -> PlainTextResponse:
    acc = current_account_key()
    buf = io.StringIO()
    cols = ["close_time", "symbol", "side", "volume", "open_price", "close_price",
            "profit", "commission", "swap", "net_profit", "magic", "comment", "source", "position_id"]
    w = csv.writer(buf)
    w.writerow(["close_time_utc", *cols[1:]])
    if acc:
        # same scoping rule as /api/history: logged-out exports stay empty,
        # never "every account in the database"
        since_ts = int(time.time() - days * 86400) if days else None
        rows = await asyncio.to_thread(store.get_trades, since_ts, None, symbol, None, 100000, 0, "DESC", acc)
        for r in rows:
            ts = int(r.get("close_time") or 0)
            row = [time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts)) if ts else ""]
            row += [r.get(c) for c in cols[1:]]
            w.writerow(row)
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="trade_history.csv"'},
    )


@app.get("/api/events")
async def api_events(limit: int = 60) -> Dict[str, Any]:
    return {"events": engine.recent_events(limit)}


# ------------------------------------------------------------- auto trading
BOT_TRADING_ENABLED = bool(cfg.CONFIG.get("bots_enabled", False))


def bot_disabled_response() -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"ok": False, "error": "Automated bots are disabled."},
    )


@app.get("/api/bot")
async def api_bot() -> Dict[str, Any]:
    snap = engine.snapshot()
    bot = snap.get("bot") or engine._bot_stub()
    return {"ok": True, "connected": bool(snap.get("connected")), "bot": bot}


@app.post("/api/bot/start")
async def api_bot_start(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Green: the bot arms the gold straddle and starts trading."""
    if not BOT_TRADING_ENABLED:
        return bot_disabled_response()
    patch: Dict[str, Any] = {"running": True}
    if "lot" in payload:
        patch["lot"] = payload["lot"]
    for k in ("distance", "sl_distance", "lock_trigger", "lock_amount", "trail_start", "trail_pct", "symbol",
              "max_spread", "cooldown_sec", "slippage_points"):
        if k in payload:
            patch[k] = payload[k]
    res = await asyncio.to_thread(engine.submit, "bot_config", patch, 30.0)
    if not res.get("ok"):
        return JSONResponse(status_code=400, content=res)
    return res


@app.post("/api/bot/stop")
async def api_bot_stop() -> Dict[str, Any]:
    """Red: cancel the pending orders (an open position keeps its stop-loss)."""
    return await asyncio.to_thread(engine.submit, "bot_config", {"running": False}, 30.0)


@app.post("/api/bot/config")
async def api_bot_config(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    allowed = {k: v for k, v in payload.items()
               if k in ("lot", "distance", "sl_distance", "lock_trigger", "lock_amount", "trail_start",
                        "trail_pct", "symbol", "max_spread", "cooldown_sec", "slippage_points")}
    return await asyncio.to_thread(engine.submit, "bot_config", allowed, 30.0)


@app.post("/api/bot/close")
async def api_bot_close() -> Dict[str, Any]:
    """Panic button: close the bot position at market and cancel its orders."""
    return await asyncio.to_thread(engine.submit, "bot_close", {}, 30.0)


# ------------------------------------------------------------- bot 2 (trend & structure)
@app.get("/api/bot2")
async def api_bot2() -> Dict[str, Any]:
    snap = engine.snapshot()
    bot2 = snap.get("bot2") or engine._bot2_stub()
    return {"ok": True, "connected": bool(snap.get("connected")), "bot2": bot2}


@app.post("/api/bot2/start")
async def api_bot2_start(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Green: bot 2 starts candle momentum & structure auto trading."""
    if not BOT_TRADING_ENABLED:
        return bot_disabled_response()
    patch: Dict[str, Any] = {"running": True}
    for k in ("lot", "max_trades", "tp", "sl", "symbol", "timeframe", "magic", "cooldown_sec", "slippage_points", "trend_mode", "watch_enabled", "watch_min_pct", "reverse", "trend_check_interval", "block_start_sec", "block_end_sec", "block_mid_from", "block_mid_to"):
        if k in payload:
            patch[k] = payload[k]
    res = await asyncio.to_thread(engine.submit, "bot2_config", patch, 30.0)
    if not res.get("ok"):
        return JSONResponse(status_code=400, content=res)
    return res


@app.post("/api/bot2/stop")
async def api_bot2_stop() -> Dict[str, Any]:
    """Red: stop bot 2 trading."""
    return await asyncio.to_thread(engine.submit, "bot2_config", {"running": False}, 30.0)


@app.post("/api/bot2/config")
async def api_bot2_config(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    allowed = {k: v for k, v in payload.items()
               if k in ("lot", "max_trades", "tp", "sl", "symbol", "timeframe", "magic", "cooldown_sec", "slippage_points", "trend_mode", "watch_enabled", "watch_min_pct", "reverse", "trend_check_interval", "block_start_sec", "block_end_sec", "block_mid_from", "block_mid_to")}
    return await asyncio.to_thread(engine.submit, "bot2_config", allowed, 30.0)


@app.post("/api/bot2/check-trend")
async def api_bot2_check_trend() -> Dict[str, Any]:
    """Trigger a manual 2.5s direction re-check immediately (watch dial or candle flow)."""
    res = await asyncio.to_thread(engine.submit, "bot2_check_trend", {}, 30.0)
    if not res.get("ok"):
        return JSONResponse(status_code=400, content=res)
    return res


@app.post("/api/bot2/close")
async def api_bot2_close() -> Dict[str, Any]:
    """Panic button: close all bot 2 positions at market."""
    return await asyncio.to_thread(engine.submit, "bot2_close", {}, 30.0)


# ----------------------------------------------------------------- bot 3
@app.get("/api/bot3")
async def api_bot3() -> Dict[str, Any]:
    snap = engine.snapshot()
    bot3 = snap.get("bot3") or engine._bot3_stub()
    return {"ok": True, "connected": bool(snap.get("connected")), "bot3": bot3}


@app.post("/api/bot3/start")
async def api_bot3_start(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Green: bot 3 starts extreme structure sniping auto trading."""
    if not BOT_TRADING_ENABLED:
        return bot_disabled_response()
    patch: Dict[str, Any] = {"running": True}
    for k in ("lot", "max_trades", "tp", "sl", "symbol", "timeframe", "magic", "cooldown_sec", "slippage_points", "tolerance_pct"):
        if k in payload:
            patch[k] = payload[k]
    res = await asyncio.to_thread(engine.submit, "bot3_config", patch, 30.0)
    if not res.get("ok"):
        return JSONResponse(status_code=400, content=res)
    return res


@app.post("/api/bot3/stop")
async def api_bot3_stop() -> Dict[str, Any]:
    """Red: stop bot 3 trading."""
    return await asyncio.to_thread(engine.submit, "bot3_config", {"running": False}, 30.0)


@app.post("/api/bot3/config")
async def api_bot3_config(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    allowed = {k: v for k, v in payload.items()
               if k in ("lot", "max_trades", "tp", "sl", "symbol", "timeframe", "magic", "cooldown_sec", "slippage_points", "tolerance_pct")}
    return await asyncio.to_thread(engine.submit, "bot3_config", allowed, 30.0)


@app.post("/api/bot3/close")
async def api_bot3_close() -> Dict[str, Any]:
    """Panic button: close all bot 3 positions at market."""
    return await asyncio.to_thread(engine.submit, "bot3_close", {}, 30.0)


@app.post("/api/bot3/recalculate")
@app.get("/api/bot3/recalculate")
async def api_bot3_recalculate() -> Dict[str, Any]:
    """Recalculates the Next 5 Extreme Execution Setups (called automatically every 60s)."""
    return await asyncio.to_thread(engine.submit, "bot3_recalculate", {}, 30.0)


# ----------------------------------------------------------------- bot 4
BOT4_KEYS = ("lot", "price_gap", "pre_trade_gap", "pre_trade_value", "numbering_lot",
             "symbol", "magic", "cooldown_sec", "slippage_points", "lock_pct")


@app.get("/api/bot4")
async def api_bot4() -> Dict[str, Any]:
    snap = engine.snapshot()
    bot4 = snap.get("bot4") or engine._bot4_stub()
    return {"ok": True, "connected": bool(snap.get("connected")), "bot4": bot4}


@app.post("/api/bot4/start")
async def api_bot4_start(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Green: bot 4 starts placing the buy / sell pre-trade ladders."""
    if not BOT_TRADING_ENABLED:
        return bot_disabled_response()
    patch: Dict[str, Any] = {"running": True}
    for k in BOT4_KEYS:
        if k in payload:
            patch[k] = payload[k]
    res = await asyncio.to_thread(engine.submit, "bot4_config", patch, 30.0)
    if not res.get("ok"):
        return JSONResponse(status_code=400, content=res)
    return res


@app.post("/api/bot4/stop")
async def api_bot4_stop() -> Dict[str, Any]:
    """Red: stop bot 4 (pre-trades cancelled, open positions keep their SL)."""
    return await asyncio.to_thread(engine.submit, "bot4_config", {"running": False}, 30.0)


@app.post("/api/bot4/config")
async def api_bot4_config(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    allowed = {k: v for k, v in payload.items() if k in BOT4_KEYS}
    return await asyncio.to_thread(engine.submit, "bot4_config", allowed, 30.0)


@app.post("/api/bot4/close")
async def api_bot4_close() -> Dict[str, Any]:
    """Panic button: close all bot 4 positions at market + cancel pre-trades."""
    return await asyncio.to_thread(engine.submit, "bot4_close", {}, 30.0)


# ----------------------------------------------------------------- bot 5 (news trader)
BOT5_KEYS = ("lot", "symbol", "magic", "mode", "min_impact", "currencies",
             "pre_minutes", "post_minutes", "min_gap_minutes", "tp", "sl",
             "straddle_gap", "max_trades", "cooldown_sec", "slippage_points",
             "bias_min_score", "refresh_min", "trade_weekend",
             "auto_close_after_news", "manual_events",
             # sessions board: which market the Sydney/Tokyo/London/New York panel
             # reads, and how many days of M15 history it averages
             "session_market", "session_range_days",
             # full AI market analysis: which market the 5m/10m/15m/1h panel
             # reads, and which timeframe shows its target prices first
             "ai_market", "ai_timeframe")


@app.get("/api/bot5")
async def api_bot5() -> Dict[str, Any]:
    snap = engine.snapshot()
    bot5 = snap.get("bot5") or engine._bot5_stub()
    return {"ok": True, "connected": bool(snap.get("connected")), "bot5": bot5}


@app.post("/api/bot5/start")
async def api_bot5_start(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Green: the news trader starts watching the calendar and trading events."""
    if not BOT_TRADING_ENABLED:
        return bot_disabled_response()
    patch: Dict[str, Any] = {"running": True}
    for k in BOT5_KEYS:
        if k in payload:
            patch[k] = payload[k]
    res = await asyncio.to_thread(engine.submit, "bot5_config", patch, 30.0)
    if not res.get("ok"):
        return JSONResponse(status_code=400, content=res)
    return res


@app.post("/api/bot5/stop")
async def api_bot5_stop() -> Dict[str, Any]:
    """Red: stop the news trader (open positions keep their SL/TP)."""
    return await asyncio.to_thread(engine.submit, "bot5_config", {"running": False}, 30.0)


@app.post("/api/bot5/config")
async def api_bot5_config(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    allowed = {k: v for k, v in payload.items() if k in BOT5_KEYS}
    return await asyncio.to_thread(engine.submit, "bot5_config", allowed, 30.0)


@app.post("/api/bot5/close")
async def api_bot5_close() -> Dict[str, Any]:
    """Panic button: close all bot 5 positions at market + cancel its stops."""
    return await asyncio.to_thread(engine.submit, "bot5_close", {}, 30.0)


@app.post("/api/bot5/refresh")
@app.get("/api/bot5/refresh")
async def api_bot5_refresh() -> Dict[str, Any]:
    """Pull the economic calendar now.

    This runs on its own thread - never on the engine thread - because a cold
    fetch can take a few seconds and must not stall the bots.
    """
    try:
        count = await asyncio.to_thread(engine.bot5.feed.fetch_once)
    except Exception as exc:
        count = 0
        engine.bot5.feed.error = str(exc)
    snap = engine.snapshot()
    return {"ok": True, "count": count, "bot5": snap.get("bot5") or engine._bot5_stub()}



# ------------------------------------------------- bot 6 (pre-trade entry)
BOT6_KEYS = ("lot", "distance", "symbol", "magic", "side", "watch_gate",
             "watch_min_pct", "ai_gate", "tp", "sl", "lock_pct", "lock_min_profit",
             "cooldown_sec", "slippage_points")


@app.get("/api/bot6")
async def api_bot6() -> Dict[str, Any]:
    snap = engine.snapshot()
    bot6 = snap.get("bot6") or engine._bot6_stub()
    return {"ok": True, "connected": bool(snap.get("connected")), "bot6": bot6}


@app.post("/api/bot6/start")
async def api_bot6_start(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Green: the pre-trade entry bot parks its pre-trade at the set Distance."""
    if not BOT_TRADING_ENABLED:
        return bot_disabled_response()
    patch: Dict[str, Any] = {"running": True}
    for k in BOT6_KEYS:
        if k in payload:
            patch[k] = payload[k]
    res = await asyncio.to_thread(engine.submit, "bot6_config", patch, 30.0)
    if not res.get("ok"):
        return JSONResponse(status_code=400, content=res)
    return res


@app.post("/api/bot6/stop")
async def api_bot6_stop() -> Dict[str, Any]:
    """Red: stop bot 6 (the pre-trade is cancelled, an open trade keeps its SL/TP)."""
    return await asyncio.to_thread(engine.submit, "bot6_config", {"running": False}, 30.0)


@app.post("/api/bot6/config")
async def api_bot6_config(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    allowed = {k: v for k, v in payload.items() if k in BOT6_KEYS}
    return await asyncio.to_thread(engine.submit, "bot6_config", allowed, 30.0)


@app.post("/api/bot6/close")
async def api_bot6_close() -> Dict[str, Any]:
    """Panic button: close all bot 6 trades at market + cancel the pre-trade."""
    return await asyncio.to_thread(engine.submit, "bot6_close", {}, 30.0)


# ------------------------------------------------- bot 7 (reversal candle)
BOT7_KEYS = ("lot", "symbol", "magic", "timeframe", "reverse_dist", "min_body",
             "tp", "sl", "lock_pct", "lock_min_profit", "watch_gate",
             "watch_min_pct", "ai_gate", "max_trades", "cooldown_sec", "slippage_points")


@app.get("/api/bot7")
async def api_bot7() -> Dict[str, Any]:
    snap = engine.snapshot()
    bot7 = snap.get("bot7") or engine._bot7_stub()
    return {"ok": True, "connected": bool(snap.get("connected")), "bot7": bot7}


@app.post("/api/bot7/start")
async def api_bot7_start(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Green: the reversal bot starts hunting candle reversals."""
    if not BOT_TRADING_ENABLED:
        return bot_disabled_response()
    patch: Dict[str, Any] = {"running": True}
    for k in BOT7_KEYS:
        if k in payload:
            patch[k] = payload[k]
    res = await asyncio.to_thread(engine.submit, "bot7_config", patch, 30.0)
    if not res.get("ok"):
        return JSONResponse(status_code=400, content=res)
    return res


@app.post("/api/bot7/stop")
async def api_bot7_stop() -> Dict[str, Any]:
    """Red: stop bot 7 (an open reversal trade keeps its SL/TP)."""
    return await asyncio.to_thread(engine.submit, "bot7_config", {"running": False}, 30.0)


@app.post("/api/bot7/config")
async def api_bot7_config(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    allowed = {k: v for k, v in payload.items() if k in BOT7_KEYS}
    return await asyncio.to_thread(engine.submit, "bot7_config", allowed, 30.0)


@app.post("/api/bot7/close")
async def api_bot7_close() -> Dict[str, Any]:
    """Panic button: close all bot 7 trades at market."""
    return await asyncio.to_thread(engine.submit, "bot7_close", {}, 30.0)


# --------------------------------------------------------------- EA bridge
def _check_secret(request: Request, payload: Optional[Dict[str, Any]] = None) -> bool:
    secret = cfg.CONFIG.get("ea_secret", "")
    if not secret:
        return True
    supplied = request.headers.get("x-ea-token") or request.headers.get("x-ea-secret")
    if not supplied and payload:
        supplied = payload.get("secret")
    if not supplied:
        supplied = request.query_params.get("secret")
    return bool(supplied) and supplied == secret


@app.post("/api/ea/event")
async def api_ea_event(request: Request, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    if not _check_secret(request, payload):
        raise HTTPException(401, "invalid EA token")
    return await asyncio.to_thread(engine.submit, "ea_event", payload, 15.0)


@app.get("/api/ea/event")
async def api_ea_event_get(request: Request) -> Dict[str, Any]:
    """Query-string variant - handy for a minimal MQL5 WebRequest."""
    if not _check_secret(request):
        raise HTTPException(401, "invalid EA token")
    q = dict(request.query_params)
    payload: Dict[str, Any] = {"type": q.get("type", "heartbeat")}
    for k, v in q.items():
        if k in ("type", "secret"):
            continue
        if k in ("profit", "commission", "swap", "net_profit", "volume", "price", "price_open",
                 "open_price", "close_price", "sl", "tp", "ticket", "position_id", "magic", "time",
                 "open_time", "close_time"):
            try:
                payload[k] = float(v)
            except ValueError:
                payload[k] = v
        else:
            payload[k] = v
    return await asyncio.to_thread(engine.submit, "ea_event", payload, 15.0)


@app.get("/api/ea/ping")
async def api_ea_ping(request: Request) -> Dict[str, Any]:
    # when the caller presents a token, validate it right here - the EA status
    # line then shows OFFLINE for a wrong token instead of pushing blind 401s
    supplied = request.headers.get("x-ea-token") or request.headers.get("x-ea-secret")
    secret = cfg.CONFIG.get("ea_secret", "")
    if secret and supplied and supplied != secret:
        raise HTTPException(401, "invalid EA token")
    return {"ok": True, "service": cfg.APP_NAME, "version": cfg.VERSION, "time": int(time.time())}


# ------------------------------------------------------- exness chart feed
# The chart is served straight from the connected Exness terminal. The browser
# gets one snapshot (meta + bars + ticker + recent big prints) on subscribe and
# then a tick stream at ~20 fps on /ws/chart.
chart_feed = get_feed()


@app.get("/api/chart/symbols")
async def api_chart_symbols(market: str = Query("")) -> Dict[str, Any]:
    """Instrument catalogue. ``market=bd`` serves the BD (DSE) feed for the BD
    Stock Market dashboard; without it the exact Exness listing is returned."""
    if str(market).strip().lower() == "bd":
        return await asyncio.to_thread(bd_feed.symbols)
    return await asyncio.to_thread(chart_feed.symbols)


@app.get("/api/chart/candles")
async def api_chart_candles(symbol: str = Query(...), tf: str = Query("5m"),
                           count: int = Query(600, ge=50, le=2000)) -> Dict[str, Any]:
    try:
        bars = await asyncio.to_thread(chart_feed.candles, symbol, tf, count)
        meta = await asyncio.to_thread(chart_feed.meta, symbol)
    except ChartUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"symbol": symbol, "tf": tf, "meta": meta, "bars": bars}


@app.get("/api/chart/ticker")
async def api_chart_ticker(symbol: str = Query(...)) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(chart_feed.ticker, symbol)
    except ChartUnavailable as exc:
        raise HTTPException(503, str(exc))


@app.get("/api/chart/tape")
async def api_chart_tape(symbol: str = Query(...), limit: int = Query(40, ge=1, le=120)) -> Dict[str, Any]:
    try:
        rows = await asyncio.to_thread(chart_feed.tape_history, symbol, limit)
        meta = await asyncio.to_thread(chart_feed.meta, symbol)
    except ChartUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"symbol": symbol, "meta": meta, "prints": rows,
            "recent": chart_feed.recent_prints(symbol, limit)}


@app.websocket("/ws/chart")
async def ws_chart(ws: WebSocket) -> None:
    await ws.accept()
    loop = asyncio.get_running_loop()
    cid, queue = chart_feed.add_client(loop)
    pending: Dict[str, Any] = {}
    retry: Dict[str, Any] = {}

    async def reader() -> None:
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("type") in ("sub", "subscribe", "chart"):
                    symbol = str(msg.get("symbol") or "").strip()
                    tf = str(msg.get("tf") or "5m")
                    if symbol and tf in CHART_TFS:
                        pending["symbol"] = symbol
                        pending["tf"] = tf
                        retry.clear()
                        try:
                            queue.put_nowait({"t": "__wake__"})
                        except asyncio.QueueFull:
                            pass
                elif msg.get("type") == "ping":
                    pending["pong"] = True
                    try:
                        queue.put_nowait({"t": "__wake__"})
                    except asyncio.QueueFull:
                        pass
        except Exception:
            pass

    async def send_snapshot(symbol: str, tf: str) -> bool:
        chart_feed.set_symbol(cid, "")
        while not queue.empty():
            try:
                queue.get_nowait()
            except Exception:
                break
        try:
            # The canvas shows about 55 bars. Keep a modest history window so
            # the first MT5 IPC request and every browser repaint stay quick.
            snap = await asyncio.to_thread(chart_feed.snapshot, symbol, tf, 360)
        except ChartUnavailable as exc:
            snap = {"t": "error", "s": symbol, "message": str(exc)}
        except Exception as exc:  # pragma: no cover - unexpected MT5 failure
            snap = {"t": "error", "s": symbol, "message": str(exc)}
        try:
            await ws.send_json(snap)
        except Exception:
            return False
        if snap.get("t") == "snap":
            chart_feed.set_symbol(cid, symbol)
            return True
        return False

    async def send_hb() -> bool:
        try:
            await ws.send_json({"t": "hb", "tm": int(time.time() * 1000)})
            return True
        except Exception:
            return False

    task = asyncio.create_task(reader())
    try:
        while True:
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                frame = None
            if pending:
                pong = bool(pending.pop("pong", False))
                if "symbol" in pending:
                    symbol = str(pending.pop("symbol", ""))
                    tf = str(pending.pop("tf", "5m"))
                    retry = {}
                    if pong:
                        await send_hb()
                    # A failed snapshot (terminal busy / briefly offline) keeps
                    # its request alive and is retried below so the chart heals
                    # itself instead of staying empty until a manual refresh.
                    if symbol and not await send_snapshot(symbol, tf):
                        retry = {"symbol": symbol, "tf": tf, "at": time.time() + 3.0}
                    continue
                if pong:
                    if not await send_hb():
                        break
                    continue
            if retry and time.time() >= float(retry.get("at") or 0.0):
                symbol = str(retry.get("symbol") or "")
                tf = str(retry.get("tf") or "5m")
                retry = {}
                if symbol and not await send_snapshot(symbol, tf):
                    retry = {"symbol": symbol, "tf": tf, "at": time.time() + 3.0}
                continue
            if frame is None:
                if not await send_hb():
                    break
                continue
            if frame.get("t") == "__wake__":
                continue
            await ws.send_json(frame)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        task.cancel()
        chart_feed.remove_client(cid)


@app.get("/api/health")
async def api_health() -> Dict[str, Any]:
    snap = engine.snapshot()
    return {
        "ok": True,
        "connected": bool(snap.get("connected")),
        "source": snap.get("source"),
        "clients": len(hub.clients),
        "uptime_engine": bool(engine._thread and engine._thread.is_alive()),
    }


# -------------------------------------------------------------- websocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await hub.register(ws)
    try:
        while True:
            msg = await ws.receive_text()
            try:
                data = json.loads(msg)
            except Exception:
                continue
            if data.get("type") == "ping":
                await hub._send_safe(ws, {"type": "pong", "ts": int(time.time() * 1000)})
            elif data.get("type") == "refresh":
                snap = engine.snapshot()
                if snap:
                    await hub._send_safe(ws, {"type": "state", "data": snap})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.unregister(ws)


# ----------------------------------------------------------------- static
INDEX = cfg.WEB_DIR / "index.html"
app.mount("/static", StaticFiles(directory=str(cfg.WEB_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(INDEX), media_type="text/html; charset=utf-8")


@app.get("/favicon.ico")
async def favicon() -> FileResponse:
    fav = cfg.WEB_DIR / "logo.jpg"
    return FileResponse(str(fav), media_type="image/jpeg")


@app.get("/logo.jpg")
async def logo_file() -> FileResponse:
    fav = cfg.WEB_DIR / "logo.jpg"
    return FileResponse(str(fav), media_type="image/jpeg")


def main() -> None:
    import uvicorn

    host = cfg.CONFIG.get("host", "127.0.0.1")
    port = int(cfg.CONFIG.get("port", 8777))
    print(f"\n  {cfg.APP_NAME} v{cfg.VERSION}")
    print(f"  Dashboard : http://{host}:{port}/")
    print(f"  API docs  : http://{host}:{port}/api/docs")
    print(f"  EA bridge : http://{host}:{port}/api/ea/event")
    print(f"  EA token  : {cfg.CONFIG.get('ea_secret', '')}\n")
    uvicorn.run(app, host=host, port=port, log_level="warning", ws_ping_interval=None, ws_ping_timeout=None)


if __name__ == "__main__":
    main()
