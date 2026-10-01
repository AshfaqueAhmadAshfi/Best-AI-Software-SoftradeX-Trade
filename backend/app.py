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
else:
    from . import config as cfg
    from .engine import Engine
    from .mt5_client import find_terminals, package_available
    from .store import Store
    from .market_watch import get_all_market_watches

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

store = Store(cfg.DB_FILE)


class Hub:
    """Fan-out engine snapshots to every connected browser."""

    def __init__(self) -> None:
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.clients: set[WebSocket] = set()
        self._locks: dict[WebSocket, asyncio.Lock] = {}
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

    async def _send_safe(self, ws: WebSocket, msg: Dict[str, Any]) -> None:
        lock = self._locks.get(ws)
        if not lock:
            return
        try:
            async with lock:
                await ws.send_json(msg)
        except Exception:
            self.unregister(ws)

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
        for ws in list(self.clients):
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


app = FastAPI(title=cfg.APP_NAME, version=cfg.VERSION, docs_url="/api/docs",
              redoc_url=None, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/api/config")
async def api_config() -> Dict[str, Any]:
    terminals = find_terminals()
    exness_terminals = [p for p in terminals if "exness" in p.lower()]
    return {
        "app": cfg.APP_NAME,
        "version": cfg.VERSION,
        "ea_secret": cfg.CONFIG.get("ea_secret", ""),
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
        "saved_login": cfg.CONFIG.get("saved_login", ""),
        "saved_server": cfg.CONFIG.get("saved_server", ""),
        "poll_interval_ms": cfg.CONFIG.get("poll_interval_ms", 400),
        "history_days": cfg.CONFIG.get("history_days", 60),
        "port": cfg.CONFIG.get("port", 8777),
        "servers": cfg.EXNESS_SERVERS,
        "common_servers": cfg.server_list(),
        "known_servers": cfg.known_servers(),
    }


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
@app.get("/api/market-watch")
async def api_market_watch(force: bool = False) -> Dict[str, Any]:
    """Buyer vs seller pressure for all active markets.

    The dashboard normally gets this data pushed live inside the WebSocket state
    snapshot every engine tick; this endpoint is only a one-shot seed / manual
    refresh path (force=1 recomputes immediately).
    """
    return await asyncio.to_thread(get_all_market_watches, None, force)


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


# ------------------------------------------------------------------ history
@app.get("/api/history")
async def api_history(
    days: int = Query(0, ge=0, le=3650),
    since: Optional[int] = None,
    until: Optional[int] = None,
    symbol: Optional[str] = None,
    result: Optional[str] = Query(None, pattern="^(win|loss)$"),
    limit: int = Query(500, ge=1, le=20000),
    offset: int = Query(0, ge=0),
    order: str = Query("DESC", pattern="^(?i)(asc|desc)$"),
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
    since_ts = int(time.time() - days * 86400) if days else None
    rows = await asyncio.to_thread(store.get_trades, since_ts, None, symbol, None, 100000, 0, "DESC", acc or None)
    buf = io.StringIO()
    cols = ["close_time", "symbol", "side", "volume", "open_price", "close_price",
            "profit", "commission", "swap", "net_profit", "magic", "comment", "source", "position_id"]
    w = csv.writer(buf)
    w.writerow(["close_time_utc", *cols[1:]])
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
@app.get("/api/bot")
async def api_bot() -> Dict[str, Any]:
    snap = engine.snapshot()
    bot = snap.get("bot") or engine._bot_stub()
    return {"ok": True, "connected": bool(snap.get("connected")), "bot": bot}


@app.post("/api/bot/start")
async def api_bot_start(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Green: the bot arms the gold straddle and starts trading."""
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
                        "symbol", "max_spread", "cooldown_sec", "slippage_points")}
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
    patch: Dict[str, Any] = {"running": True}
    for k in ("lot", "max_trades", "tp", "sl", "symbol", "timeframe", "magic", "cooldown_sec", "slippage_points", "trend_mode", "trend_check_interval", "block_start_sec", "block_end_sec", "block_mid_from", "block_mid_to"):
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
               if k in ("lot", "max_trades", "tp", "sl", "symbol", "timeframe", "magic", "cooldown_sec", "slippage_points", "trend_mode", "trend_check_interval", "block_start_sec", "block_end_sec", "block_mid_from", "block_mid_to")}
    return await asyncio.to_thread(engine.submit, "bot2_config", allowed, 30.0)


@app.post("/api/bot2/check-trend")
async def api_bot2_check_trend() -> Dict[str, Any]:
    """Trigger manual 60s trend re-check immediately."""
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
async def api_ea_ping() -> Dict[str, Any]:
    return {"ok": True, "service": cfg.APP_NAME, "version": cfg.VERSION, "time": int(time.time())}


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
                await ws.send_json({"type": "pong", "ts": int(time.time() * 1000)})
            elif data.get("type") == "refresh":
                snap = engine.snapshot()
                if snap:
                    await ws.send_json({"type": "state", "data": snap})
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
    uvicorn.run(app, host=host, port=port, log_level="warning", ws_ping_interval=20, ws_ping_timeout=20)


if __name__ == "__main__":
    main()
