"""
Thin, defensive wrapper around the official MetaTrader5 Python package.

Design notes
------------
* Every MT5 call happens on the engine thread (see engine.py) - the MT5 IPC
  bridge is not thread safe, so this class is never touched concurrently.
* MT5 returns "server clock" epochs. We keep them as-is and label them
  "server time" in the UI (this is exactly what the MT5 Terminal shows).
* History is queried with deliberately wide windows because the Python
  package normalises naive datetimes to UTC; a wide window removes any
  timezone interpretation doubt and the store de-duplicates by position id.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import MetaTrader5 as mt5  # type: ignore
except Exception:  # pragma: no cover - only if package missing
    mt5 = None  # type: ignore

# The MT5 IPC bridge is a single pipe into the terminal and is not thread safe.
# The engine owns one thread, but the watch stream thread and FastAPI handlers
# also touch the package - every mt5.* call site takes this lock so two calls
# can never interleave on the wire. RLock so nested helpers stay simple.
IPC_LOCK = threading.RLock()

# The terminal the dashboard is (or was) attached to. Read-only helpers such as
# the chart feed use it so they always attach to the SAME Exness terminal as
# the trading engine instead of letting MetaTrader5 auto-pick one.
_preferred_terminal_path: str = ""


def set_preferred_terminal(path: str) -> None:
    global _preferred_terminal_path
    _preferred_terminal_path = str(path or "")


def preferred_terminal_path() -> str:
    return _preferred_terminal_path

# ----------------------------------------------------------------- constants
DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = 1
DEAL_ENTRY_INOUT = 2
DEAL_ENTRY_OUT_BY = 3

DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1

POSITION_TYPE_BUY = 0

TERMINAL_EXE = "terminal64.exe"

_SEARCH_ROOTS = [
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
    os.path.expandvars(r"%APPDATA%"),
    "C:\\",
    "D:\\",
    "E:\\",
]


def _mt5(fn, *args, **kwargs):
    """Run a single MetaTrader5 IPC call under the global IPC lock."""
    with IPC_LOCK:
        return fn(*args, **kwargs)


def package_available() -> bool:
    return mt5 is not None


def _as_dict(obj: Any) -> Dict[str, Any]:
    """MT5 returns namedtuples; be liberal and accept dicts / plain objects too."""
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


def package_version() -> str:
    try:
        return getattr(mt5, "__version__", "unknown")
    except Exception:
        return "unknown"


# ------------------------------------------------------------ terminal lookup
_terminal_cache: List[str] = []
_terminal_cache_ts: float = 0.0
_full_scan_ts: float = 0.0
_FULL_SCAN_INTERVAL = 600.0  # the brute-force drive scan is expensive - throttle it


def _scan_for_terminals(roots: List[str], max_depth: int = 3) -> List[str]:
    found: List[str] = []
    seen: set[str] = set()
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        stack: List[Tuple[Path, int]] = [(root_path, 0)]
        while stack:
            cur, depth = stack.pop()
            if depth > max_depth:
                continue
            try:
                entries = list(os.scandir(cur))
            except (PermissionError, OSError):
                continue
            for e in entries:
                try:
                    if e.is_file(follow_symlinks=False) and e.name.lower() == TERMINAL_EXE:
                        p = str(Path(e.path))
                        if p.lower() not in seen:
                            seen.add(p.lower())
                            found.append(p)
                    elif e.is_dir(follow_symlinks=False) and depth < max_depth:
                        name = e.name.lower()
                        if name.startswith("$") or name in ("windows", "system32", "winsxs", "node_modules"):
                            continue
                        stack.append((Path(e.path), depth + 1))
                except OSError:
                    continue
    return found


def invalidate_terminal_cache() -> None:
    """Forget the cached terminal scan (used right after a failed login).

    Also resets the full-scan throttle: a failed login usually means a terminal
    was just installed or moved, so the next lookup must search the drive again.
    """
    global _terminal_cache_ts, _full_scan_ts
    _terminal_cache_ts = 0.0
    _full_scan_ts = 0.0


def has_exness_terminal(terminals: Optional[List[str]] = None) -> bool:
    terms = terminals if terminals is not None else find_terminals()
    return any("exness" in str(t).lower() for t in terms)


def find_terminals(force: bool = False) -> List[str]:
    """Locate every MT5 terminal on the machine.

    The roaming-folder lookup is cheap and runs whenever the 60 s cache is
    stale. The brute-force scan of the whole drive only runs when the roaming
    folder knows nothing AND the last full scan is older than 10 minutes - a
    single-terminal machine must not pay a multi-second C:\\ scan every minute.
    """
    global _terminal_cache, _terminal_cache_ts, _full_scan_ts
    if not force and _terminal_cache and (time.time() - _terminal_cache_ts) < 60:
        return list(_terminal_cache)

    found: List[str] = []

    # 1) terminals registered in the MetaQuotes roaming folder (has install path)
    try:
        roaming = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
        if roaming.exists():
            for d in roaming.iterdir():
                origin = d / "origin.txt"
                if not origin.exists():
                    continue
                try:
                    raw = origin.read_text(encoding="utf-8", errors="ignore").strip().strip("\x00")
                except OSError:
                    continue
                if not raw:
                    continue
                exe = Path(raw) / TERMINAL_EXE
                if exe.exists():
                    found.append(str(exe))
    except OSError:
        pass

    # 2) brute-force shallow scan of the usual install roots - rare and throttled
    if not found:
        if force or (time.time() - _full_scan_ts) >= _FULL_SCAN_INTERVAL:
            _full_scan_ts = time.time()
            found.extend(_scan_for_terminals(_SEARCH_ROOTS, max_depth=3))
        elif _terminal_cache:
            # the scan is rate-limited and this machine's terminal is only
            # discoverable through it - keep serving the previous list instead
            # of letting an empty roaming result blank the cache out
            _terminal_cache_ts = time.time()
            return list(_terminal_cache)

    # de-dupe, keep order, prefer Exness-branded terminals first
    uniq: List[str] = []
    seen: set[str] = set()
    for p in found:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(p)

    def rank(p: str) -> int:
        low = p.lower()
        if "exness" in low:
            return 0
        if "metatrader" in low or "mt5" in low:
            return 1
        return 2

    uniq.sort(key=rank)
    _terminal_cache = uniq
    _terminal_cache_ts = time.time()
    return list(uniq)


# ------------------------------------------------------------- error mapping
_ERROR_HINTS = {
    -6: "Authorization failed - wrong login/password, or the server name does not belong to this terminal.",
    -10000: "Terminal could not be initialised (wrong path to terminal64.exe?).",
    -10001: "Failed to add the terminal to the IPC queue.",
    -10002: "Terminal is not responding (the MT5 window may be blocked by a dialog).",
    -10003: "Invalid parameters.",
    -10004: "Trade timeout.",
    -10005: "IPC timeout - the terminal is busy or still starting. Try again.",
    -10006: "Terminal is not authorised.",
    -10007: "Account is not authorised on this server.",
    -10009: "Invalid terminal path.",
    -10010: "The terminal is not connected to the trade server.",
    -10011: "Market is closed.",
    -10012: "Account is disabled / read-only.",
    -10013: "Account is not authorised for trading.",
    -10014: "Invalid account.",
    -10015: "Invalid server name.",
    -10016: "Connection to the trade server was closed.",
    -10017: "Terminal is already initialised.",
    -10018: "Terminal path does not point to terminal64.exe.",
}


def describe_error(err: Any) -> Dict[str, Any]:
    code, msg = (err if isinstance(err, (tuple, list)) and len(err) == 2 else (0, str(err)))
    try:
        code = int(code)
    except Exception:
        code = 0
    hint = _ERROR_HINTS.get(code, "")
    text = str(msg)
    if not hint:
        low = text.lower()
        if "authoriz" in low or "invalid account" in low:
            hint = _ERROR_HINTS[-6]
        elif "timeout" in low:
            hint = _ERROR_HINTS[-10005]
        elif "server" in low:
            hint = "Check the server name (e.g. Exness-MT5Real / Exness-MT5Real6) and that an Exness MT5 terminal is installed."
    return {"code": code, "message": text, "hint": hint}


# ----------------------------------------------------------------- the client
class MT5Client:
    def __init__(self) -> None:
        self.connected = False
        self.terminal_path: str = ""
        self.login: int = 0
        self.server: str = ""
        self._time_offset: float = 0.0          # server_epoch - local_epoch
        self._offset_locked: bool = False
        self.last_error: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------- lifecycle
    def connect(self, login: int, password: str, server: str, path: str = "",
                timeout_ms: int = 60000) -> Dict[str, Any]:
        if mt5 is None:
            return {"ok": False, "error": {"code": -1, "message": "MetaTrader5 python package is not installed",
                                           "hint": "pip install MetaTrader5"}}
        self.shutdown()

        candidates: List[str] = []
        if path:
            candidates.append(path)
        if self.terminal_path:
            candidates.append(self.terminal_path)
        found = find_terminals()
        if not found:
            # nothing cached - the user may have installed MT5 a moment ago
            found = find_terminals(force=True)
        candidates.extend(found)
        # last resort: let the package auto-detect from the registry
        candidates.append("")

        last_err: Any = None
        for cand in candidates:
            if cand and not Path(cand).exists():
                continue
            kwargs: Dict[str, Any] = {}
            if cand:
                kwargs["path"] = cand
            if login and password:
                kwargs["login"] = int(login)
                kwargs["password"] = str(password)
                if server:
                    kwargs["server"] = str(server)
                kwargs["timeout"] = timeout_ms
            try:
                ok = _mt5(mt5.initialize, **kwargs)
            except Exception as exc:  # pragma: no cover
                ok = False
                last_err = (-1, str(exc))
            if not ok:
                last_err = _mt5(mt5.last_error)
                info = self._terminal_info()
                # If this terminal simply does not know the server, try the next one
                try:
                    _mt5(mt5.shutdown)
                except Exception:
                    pass
                if info and info.get("connected") and not path and len(candidates) > 1:
                    continue
                continue

            # initialize() with credentials usually logs in already; login() is a safety net
            auth = _mt5(mt5.account_info)
            if auth is not None and login and int(auth.login) != int(login) and not password:
                # never silently trade the wrong account: without a password we
                # cannot switch the terminal to the requested login
                last_err = (-1, "Terminal already logged in as account %s. Enter the password for %s to switch." % (auth.login, login))
                try:
                    _mt5(mt5.shutdown)
                except Exception:
                    pass
                continue
            if auth is None or (login and int(auth.login) != int(login)):
                if password:
                    if not _mt5(mt5.login, int(login), password=str(password), server=server or None, timeout=timeout_ms):
                        last_err = _mt5(mt5.last_error)
                        try:
                            _mt5(mt5.shutdown)
                        except Exception:
                            pass
                        continue
                    auth = _mt5(mt5.account_info)
                elif auth is None:
                    last_err = _mt5(mt5.last_error)
                    try:
                        _mt5(mt5.shutdown)
                    except Exception:
                        pass
                    continue
            if auth is None:
                last_err = _mt5(mt5.last_error)
                try:
                    _mt5(mt5.shutdown)
                except Exception:
                    pass
                continue

            self.connected = True
            self.terminal_path = cand or self._detect_running_path()
            set_preferred_terminal(self.terminal_path)
            self.login = int(auth.login)
            self.server = str(getattr(auth, "server", server) or server)
            self._time_offset = 0.0
            self._offset_locked = False
            self._calibrate_clock()
            self.last_error = None
            return {"ok": True, "account": self.account(), "terminal": self.terminal_path}

        self.connected = False
        invalidate_terminal_cache()
        self.last_error = describe_error(last_err or _mt5(mt5.last_error))
        # Verified on a real machine: any MetaTrader 5 terminal can log into an
        # Exness account as long as the server name is passed explicitly (MT5
        # resolves it through the global MetaQuotes access-point directory).
        # So only complain when there is no terminal at all.
        if not any(c for c in candidates if c):
            note = ("No MetaTrader 5 terminal was found on this PC. Any MT5 build works "
                    "(Exness MT5, or the plain one from metatrader5.com) - install it, then press "
                    "Login again; the dashboard starts the terminal for you.")
            self.last_error["hint"] = (self.last_error["hint"] + " " + note) if self.last_error.get("hint") else note
        return {"ok": False, "error": self.last_error}

    def shutdown(self) -> None:
        self.connected = False
        if mt5 is None:
            return
        try:
            _mt5(mt5.shutdown)
        except Exception:
            pass

    def try_recover(self) -> bool:
        """One bounded auto re-attach attempt to the Exness terminal.

        After a quiet IPC loss (terminal restart, update, or the user closing
        and re-opening it) the MetaTrader5 shim drops its attach. A re-login is
        impossible without stored credentials on purpose, but the terminal
        itself remembers the account: re-initializing the SAME terminal
        restores the full session without a password. Only a terminal that is
        genuinely gone for good (and does not auto-login) needs the user.
        """
        if mt5 is None or not self.login:
            return False
        path = self.terminal_path
        try:
            info = None
            try:
                info = _mt5(mt5.terminal_info)
            except Exception:
                info = None
            if info is not None:
                # attach is actually alive; the caller just hit a hiccup
                self.connected = True
                self.last_error = None
                if not path:
                    live_path = str(getattr(info, "path", "") or "")
                    if live_path:
                        exe = Path(live_path) / TERMINAL_EXE
                        self.terminal_path = str(exe) if exe.exists() else live_path
                if self.terminal_path:
                    set_preferred_terminal(self.terminal_path)
                return True
            # pipe really is dead: drop the stale shim and re-attach.
            # initialize() with a path re-launches / re-attaches the terminal.
            try:
                _mt5(mt5.shutdown)
            except Exception:
                pass
            kwargs: Dict[str, Any] = {"timeout": 20000}
            if path and Path(path).exists():
                kwargs["path"] = path
            if not _mt5(mt5.initialize, **kwargs):
                return False
            auth: Any = None
            try:
                auth = _mt5(mt5.account_info)
            except Exception:
                auth = None
            if auth is None:
                return False
            if int(getattr(auth, "login", 0) or 0) != int(self.login):
                # the terminal came back on a different account - never
                # silently trade the wrong account; let the user decide
                try:
                    _mt5(mt5.shutdown)
                except Exception:
                    pass
                return False
            self._time_offset = 0.0
            self._offset_locked = False
            try:
                self._calibrate_clock()
            except Exception:
                pass
            # the re-attach succeeded: restore the live flag too, otherwise a
            # caller that dropped it (or started with it cleared) would keep
            # treating the session as dead while the pipe is actually healthy
            if not self.terminal_path:
                self.terminal_path = self._detect_running_path()
            if self.terminal_path:
                set_preferred_terminal(self.terminal_path)
            self.connected = True
            self.last_error = None
            return True
        except Exception:
            return False

    # ----------------------------------------------------------------- info
    @staticmethod
    def _terminal_info() -> Optional[Dict[str, Any]]:
        if mt5 is None:
            return None
        try:
            ti = _mt5(mt5.terminal_info)
        except Exception:
            return None
        if ti is None:
            return None
        return _as_dict(ti)

    def _detect_running_path(self) -> str:
        info = self._terminal_info()
        if info:
            p = info.get("path") or ""
            if p:
                exe = Path(p) / TERMINAL_EXE
                return str(exe) if exe.exists() else p
        return ""

    # --------------------------------------------------------------- clock
    def _calibrate_clock(self, max_symbols: int = 60) -> None:
        """Estimate server_epoch - local_epoch from a fresh quote.

        Only quote timestamps may define the offset: a position's last-update
        time would rewind the clock every poll while a trade sits untouched.
        """
        if mt5 is None:
            return
        local_arrival = time.time()
        best: Optional[int] = None
        try:
            symbols = _mt5(mt5.symbols_get)
        except Exception:
            symbols = None
        names: List[str] = []
        if symbols:
            for s in list(symbols)[:max_symbols]:
                try:
                    if getattr(s, "visible", False):
                        names.append(s.name)
                except Exception:
                    continue
        if not names:
            names = ["EURUSD", "XAUUSDm", "XAUUSD", "GBPUSD", "USDJPY"]
        for name in names[:max_symbols]:
            try:
                tick = _mt5(mt5.symbol_info_tick, name)
            except Exception:
                continue
            if tick and tick.time:
                if best is None or tick.time > best:
                    best = int(tick.time)
        if best:
            off = best - local_arrival
            if abs(off) < 26 * 3600:
                self._time_offset = off
                self._offset_locked = True

    _RECALIBRATE_INTERVAL = 900.0  # quote drift check twice an hour is plenty

    def _maybe_recalibrate(self) -> None:
        if mt5 is None or not self.connected:
            return
        now = time.time()
        if now - getattr(self, "_last_calib_ts", 0.0) < self._RECALIBRATE_INTERVAL:
            return
        self._last_calib_ts = now
        try:
            self._calibrate_clock(max_symbols=12)
        except Exception:
            pass

    def server_now(self) -> float:
        return time.time() + self._time_offset

    def server_offset(self) -> float:
        return self._time_offset

    # ------------------------------------------------------------- snapshot
    def account(self) -> Optional[Dict[str, Any]]:
        if mt5 is None or not self.connected:
            return None
        try:
            ai = _mt5(mt5.account_info)
        except Exception:
            return None
        if ai is None:
            return None
        d = _as_dict(ai)
        keys = [
            "login", "name", "server", "currency", "leverage", "balance", "equity",
            "margin", "margin_free", "margin_level", "profit", "credit",
            "trade_mode", "trade_allowed", "trade_expert", "company",
        ]
        out = {k: d.get(k) for k in keys if k in d}
        self._maybe_recalibrate()
        return out

    def positions(self) -> List[Dict[str, Any]]:
        if mt5 is None or not self.connected:
            return []
        try:
            pos = _mt5(mt5.positions_get)
        except Exception:
            return []
        if not pos:
            return []
        out: List[Dict[str, Any]] = []
        for p in pos:
            d = _as_dict(p)
            d["side"] = "buy" if int(d.get("type", 0)) == POSITION_TYPE_BUY else "sell"
            d["profit"] = float(d.get("profit") or 0.0)
            d["swap"] = float(d.get("swap") or 0.0)
            d["net"] = round(d["profit"] + d["swap"], 2)
            d["volume"] = float(d.get("volume") or 0.0)
            out.append(d)
        # NOTE: position timestamps are deliberately NOT used to adjust the
        # clock - an untouched position would drag server_now() backwards
        # forever. See _calibrate_clock / _maybe_recalibrate.
        out.sort(key=lambda x: (x.get("symbol") or "", x.get("ticket") or 0))
        return out

    def symbol_price(self, symbol: str) -> Optional[Dict[str, Any]]:
        if mt5 is None or not self.connected:
            return None
        try:
            tick = _mt5(mt5.symbol_info_tick, symbol)
        except Exception:
            return None
        if not tick:
            return None
        return {"bid": tick.bid, "ask": tick.ask, "time": tick.time}

    def orders(self) -> List[Dict[str, Any]]:
        """Pending orders (buy/sell stop & limit) currently on the account."""
        if mt5 is None or not self.connected:
            return []
        try:
            raw = _mt5(mt5.orders_get)
        except Exception:
            return []
        if not raw:
            return []
        kinds = {0: "buy", 1: "sell", 2: "buy limit", 3: "sell limit",
                 4: "buy stop", 5: "sell stop", 6: "buy stop limit", 7: "sell stop limit"}
        out: List[Dict[str, Any]] = []
        for o in raw:
            d = _as_dict(o)
            d["kind"] = kinds.get(int(d.get("type", -1)), "")
            d["volume"] = float(d.get("volume_current") or d.get("volume_initial") or 0.0)
            d["price_open"] = float(d.get("price_open") or 0.0)
            d["sl"] = float(d.get("sl") or 0.0)
            d["tp"] = float(d.get("tp") or 0.0)
            out.append(d)
        out.sort(key=lambda x: (x.get("symbol") or "", x.get("price_open") or 0.0))
        return out

    # -------------------------------------------------------------- history
    def fetch_closed_trades(self, days: float = 3.0) -> List[Dict[str, Any]]:
        """Return closed trades built from MT5 deals, newest first."""
        if mt5 is None or not self.connected:
            return []
        now = datetime.now()
        date_from = now - timedelta(days=max(days, 0.25))
        date_to = now + timedelta(days=2)
        try:
            deals = _mt5(mt5.history_deals_get, date_from, date_to)
        except Exception:
            deals = None
        if not deals:
            return []
        return self._build_trades(deals)

    @staticmethod
    def _build_trades(deals: Any) -> List[Dict[str, Any]]:
        groups: Dict[int, Dict[str, Any]] = {}
        for d in deals:
            dd = _as_dict(d)
            dtype = int(dd.get("type", -1))
            if dtype not in (DEAL_TYPE_BUY, DEAL_TYPE_SELL):
                continue  # skip balance / credit operations
            pid = int(dd.get("position_id") or 0)
            if not pid:
                continue
            g = groups.get(pid)
            if g is None:
                g = {
                    "position_id": str(pid),
                    "ticket": int(dd.get("order") or pid),
                    "symbol": dd.get("symbol") or "",
                    "magic": int(dd.get("magic") or 0),
                    "comment": dd.get("comment") or "",
                    "open_time": 0,
                    "close_time": 0,
                    "open_price": None,
                    "close_price": None,
                    "volume": 0.0,
                    "close_volume": 0.0,
                    "profit": 0.0,
                    "commission": 0.0,
                    "swap": 0.0,
                    "side": "",
                    "source": "mt5",
                    "ticket_in": 0,
                    "_pv": 0.0,  # sum(price * volume) over entry deals
                }
                groups[pid] = g
            entry = int(dd.get("entry", -1))
            vol = float(dd.get("volume") or 0.0)
            g["profit"] += float(dd.get("profit") or 0.0)
            g["commission"] += float(dd.get("commission") or 0.0)
            g["swap"] += float(dd.get("swap") or 0.0)
            if g["comment"] == "" and dd.get("comment"):
                g["comment"] = dd["comment"]
            if entry == DEAL_ENTRY_IN:
                g["volume"] += vol
                t_in = int(dd.get("time") or 0)
                if not g["open_time"] or (t_in and t_in < g["open_time"]):
                    g["open_time"] = t_in
                g["_pv"] += float(dd.get("price") or 0.0) * vol
                g["side"] = "buy" if dtype == DEAL_TYPE_BUY else "sell"
                g["ticket_in"] = int(dd.get("ticket") or 0)
            elif entry in (DEAL_ENTRY_OUT, DEAL_ENTRY_INOUT, DEAL_ENTRY_OUT_BY):
                g["close_volume"] += vol
                t = int(dd.get("time") or 0)
                if t >= g["close_time"]:
                    g["close_time"] = t
                    g["close_price"] = float(dd.get("price") or 0.0)
                if not g["side"]:
                    # closing a buy means the closing deal is a sell
                    g["side"] = "buy" if dtype == DEAL_TYPE_SELL else "sell"
            if g["ticket"] == pid and dd.get("order"):
                g["ticket"] = int(dd["order"])

        out: List[Dict[str, Any]] = []
        for g in groups.values():
            if g["close_time"] <= 0:
                continue  # still open
            if g["volume"] > 0 and g["close_volume"] + 1e-9 < g["volume"]:
                continue  # partially closed -> keep tracking as open
            # scale-ins: report the volume-weighted average entry price, not
            # the price of whichever entry deal happened to come last
            if g["volume"] > 0:
                g["open_price"] = g["_pv"] / g["volume"]
            g["net_profit"] = round(g["profit"] + g["commission"] + g["swap"], 5)
            g["profit"] = round(g["profit"], 5)
            g["commission"] = round(g["commission"], 5)
            g["swap"] = round(g["swap"], 5)
            if not g["open_time"]:
                g["open_time"] = g["close_time"]
            out.append(g)
        out.sort(key=lambda x: x["close_time"], reverse=True)
        return out
