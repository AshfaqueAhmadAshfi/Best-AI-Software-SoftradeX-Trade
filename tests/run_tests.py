"""
Offline test-suite for the dashboard backend (no terminal / no broker needed).

Run:  python tests/run_tests.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import mt5_client as mc           # noqa: E402
from backend.engine import Engine, account_kind  # noqa: E402
from backend.mt5_client import MT5Client       # noqa: E402
from backend.stats import compute_stats        # noqa: E402
from backend.store import Store                # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [ok]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


# --------------------------------------------------------------- fake MT5
def deal(position_id, entry, dtype, volume, price, t, profit=0.0, commission=0.0, swap=0.0,
         symbol="XAUUSDm", magic=7, comment="t", order=1, ticket=1):
    return SimpleNamespace(position_id=position_id, entry=entry, type=dtype, volume=volume,
                           price=price, time=t, profit=profit, commission=commission, swap=swap,
                           symbol=symbol, magic=magic, comment=comment, order=order, ticket=ticket)


class FakeMT5:
    """Minimal stand-in for the MetaTrader5 python module."""

    DEAL_ENTRY_IN, DEAL_ENTRY_OUT = 0, 1
    __version__ = "fake"

    def __init__(self, init_ok=True, login_ok=True, trade_mode=2, login=12345678):
        self.init_ok = init_ok
        self.login_ok = login_ok
        self.trade_mode = trade_mode
        self.account_login = login
        self.errors = []
        self._t = int(time.time())

    def initialize(self, **kw):
        self.kw = kw
        return self.init_ok

    def login(self, login, password=None, server=None, timeout=0):
        return self.login_ok

    def last_error(self):
        return self.errors.pop() if self.errors else (-6, "Authorization failed")

    def account_info(self):
        return SimpleNamespace(login=self.account_login, server="Exness-MT5Real", currency="USD",
                               balance=1000.0, equity=1025.0, margin=10.0, margin_free=1015.0,
                               margin_level=10250.0, leverage=2000, profit=25.0, name="TEST",
                               credit=0.0, trade_mode=self.trade_mode, trade_allowed=True,
                               company="Exness")

    def positions_get(self):
        return [
            SimpleNamespace(ticket=201, symbol="XAUUSDm", type=0, volume=0.10, price_open=2600.0,
                            price_current=2605.0, sl=0.0, tp=0.0, profit=50.0, swap=-0.5,
                            time=self._t - 600, time_update=self._t, magic=1, comment="a"),
            SimpleNamespace(ticket=202, symbol="EURUSDm", type=1, volume=0.05, price_open=1.09,
                            price_current=1.095, sl=0.0, tp=0.0, profit=-25.0, swap=0.0,
                            time=self._t - 300, time_update=self._t, magic=2, comment="b"),
        ]

    def symbols_get(self):
        return []

    def symbol_info_tick(self, s):
        return SimpleNamespace(time=self._t, bid=1.0, ask=1.0001)

    def history_deals_get(self, a, b):
        t0 = int(time.time()) - 3600
        return [
            deal(101, 0, 0, 0.10, 2640.0, t0, order=11, ticket=11),
            deal(101, 1, 1, 0.10, 2645.0, t0 + 600, profit=50.0, commission=-0.7, swap=-0.1, order=12, ticket=12),
            deal(102, 0, 1, 0.05, 2650.0, t0, order=13, ticket=13),
            deal(102, 1, 0, 0.05, 2655.0, t0 + 300, profit=-25.0, commission=-0.35, swap=0.0, order=14, ticket=14),
            deal(103, 0, 0, 0.01, 1.085, t0, order=15, ticket=15),  # still open -> must be skipped
        ]

    def shutdown(self):
        return True

    def terminal_info(self):
        return SimpleNamespace(connected=True, path="C:\\Fake\\MT5")


# ------------------------------------------------------------------ tests
print("\n1. stats engine")
trades = [
    {"close_time": 1000, "net_profit": 50.0, "volume": 0.1, "symbol": "XAUUSDm"},
    {"close_time": 2000, "net_profit": -20.0, "volume": 0.2, "symbol": "EURUSDm"},
    {"close_time": 3000, "net_profit": 30.0, "volume": 0.1, "symbol": "XAUUSDm"},
]
st = compute_stats(trades)
check("counts trades", st["trades"] == 3, st)
check("wins", st["wins"] == 2, st)
check("losses", st["losses"] == 1, st)
check("net profit", st["net_profit"] == 60.0, st)
check("win rate 66.67", st["win_rate"] == 66.67, st)
check("profit factor 4.0", st["profit_factor"] == 4.0, st)
check("best / worst", st["best"] == 50.0 and st["worst"] == -20.0, st)
st_today = compute_stats(trades, start_ts=2500)
check("time window filter", st_today["trades"] == 1 and st_today["net_profit"] == 30.0, st_today)
st_be = compute_stats([{"close_time": 1, "net_profit": 0.0}])
check("breakeven bucket", st_be["breakeven"] == 1 and st_be["wins"] == 0, st_be)
check("floating from positions", compute_stats([], [{"profit": 5.0, "swap": -1.0}])["floating"] == 4.0)

print("\n2. account kind from MT5 trade_mode")
check("trade_mode 2 -> real", account_kind({"trade_mode": 2}) == "real")
check("trade_mode 0 -> demo", account_kind({"trade_mode": 0}) == "demo")
check("trade_mode 1 -> contest", account_kind({"trade_mode": 1}) == "contest")
check("no account -> None", account_kind(None) is None)

print("\n3. deal -> trade builder")
built = MT5Client._build_trades(FakeMT5().history_deals_get(None, None))
by_id = {t["position_id"]: t for t in built}
check("open position skipped", "103" not in by_id, by_id.keys())
check("two closed trades", len(built) == 2, len(built))
check("win trade net = 50-0.7-0.1", by_id["101"]["net_profit"] == 49.2, by_id["101"])
check("loss trade net = -25-0.35", by_id["102"]["net_profit"] == -25.35, by_id["102"])
check("side from entry deal", by_id["101"]["side"] == "buy" and by_id["102"]["side"] == "sell", by_id)
check("sorted newest first", built[0]["close_time"] >= built[1]["close_time"], built)

print("\n4. MT5Client.connect error path")
fake = FakeMT5(init_ok=False)
mc.mt5 = fake
c = MT5Client()
res = c.connect(999, "pw", "Exness-MT5Real", path="")
check("connect fails cleanly", res["ok"] is False, res)
check("error carries a hint", bool(res["error"].get("hint")), res["error"])
check("hint mentions authorization", "Authorization" in res["error"]["hint"], res["error"])
# with no terminal at all the hint must say so
_orig_find0 = mc.find_terminals
mc.find_terminals = lambda force=False: []
try:
    mc.mt5 = FakeMT5(init_ok=False)
    c_noterm = MT5Client()
    r_noterm = c_noterm.connect(999, "pw", "Exness-MT5Trial8", path="")
    check("hint asks for MetaTrader when no terminal exists",
          "MetaTrader 5 terminal" in r_noterm["error"]["hint"], r_noterm["error"]["hint"])
    check("no false Exness-branding claim in the hint",
          "another broker" not in r_noterm["error"]["hint"].lower(), r_noterm["error"]["hint"])
finally:
    mc.find_terminals = _orig_find0

print("\n5. MT5Client.connect success path")
fake = FakeMT5(init_ok=True)
mc.mt5 = fake
c = MT5Client()
res = c.connect(12345678, "pw", "Exness-MT5Real", path="")
check("connect ok", res["ok"] is True, res)
check("account parsed", res["account"]["login"] == 12345678 and res["account"]["currency"] == "USD", res)
check("client marked connected", c.connected is True)
acc = c.account()
check("account() works", acc and acc["balance"] == 1000.0, acc)
check("positions parsed with side/net",
      sorted((p["side"], p["net"]) for p in c.positions()) == [("buy", 49.5), ("sell", -25.0)],
      [(p["side"], p["net"]) for p in c.positions()])
c.shutdown()
check("shutdown clears state", c.connected is False)

print("\n5b. Exness terminal auto-discovery (fresh install case)")
# a real file so the "does this path exist?" guard lets it through, like a
# genuine Exness install would
_ex_dir = Path(tempfile.mkdtemp()) / "Exness MetaTrader 5"
_ex_dir.mkdir(parents=True, exist_ok=True)
_ex_path = _ex_dir / "terminal64.exe"
_ex_path.write_bytes(b"")
_calls = []
_orig_find = mc.find_terminals
_orig_has = mc.has_exness_terminal


def _fake_find(force=False):
    _calls.append(bool(force))
    if force:
        return [str(_ex_path)]
    return []          # nothing cached yet - the user just installed MT5


def _fake_has(terms=None):
    terms = terms if terms is not None else _fake_find()
    return any("exness" in str(t).lower() for t in terms)


mc.find_terminals = _fake_find
mc.has_exness_terminal = _fake_has
try:
    engine_fake = FakeMT5(init_ok=True)
    mc.mt5 = engine_fake
    c2 = MT5Client()
    r = c2.connect(12345678, "pw", "Exness-MT5Trial8", path="")
    check("connect re-scans when nothing was cached", True in _calls, _calls)
    check("connect succeeds on the freshly installed terminal", r.get("ok") is True, r)
    check("initialize() was pointed at the found terminal",
          str(getattr(engine_fake, "kw", {}).get("path", "")) == str(_ex_path),
          getattr(engine_fake, "kw", {}))
    check("terminal path is reported back", r.get("terminal") == str(_ex_path), r.get("terminal"))
    c2.shutdown()
finally:
    mc.find_terminals = _orig_find
    mc.has_exness_terminal = _orig_has
check("has_exness_terminal detects an Exness path",
      mc.has_exness_terminal([r"C:\Program Files\Exness MetaTrader 5\terminal64.exe"]) is True)
check("has_exness_terminal ignores other brokers",
      mc.has_exness_terminal([r"C:\Program Files\Other\terminal64.exe"]) is False)

# a stale registry entry (terminal uninstalled/moved) must not block the login
_stale = str(_ex_dir / "gone.exe")
mc.find_terminals = lambda force=False: [_stale]
mc.has_exness_terminal = lambda terms=None: True
try:
    f2 = FakeMT5(init_ok=True)
    mc.mt5 = f2
    c3 = MT5Client()
    r3 = c3.connect(12345678, "pw", "Exness-MT5Real6", path="")
    check("a stale terminal path is skipped and the login still works",
          r3.get("ok") is True and "path" not in getattr(f2, "kw", {}), getattr(f2, "kw", {}))
    c3.shutdown()
finally:
    mc.find_terminals = _orig_find
    mc.has_exness_terminal = _orig_has

print("\n6. store: idempotent upsert + per-account separation")
tmp = Path(tempfile.mkdtemp()) / "t.db"
store = Store(tmp)
rows = [{"position_id": "1", "symbol": "XAUUSDm", "side": "buy", "volume": 0.1, "net_profit": 5.0,
         "close_time": int(time.time()), "source": "mt5"}]
store.upsert_trades(rows, account="111")
store.upsert_trades(rows, account="111")
check("no duplicate rows", store.count_trades() == 1, store.count_trades())
rows[0]["net_profit"] = 9.0
store.upsert_trades(rows, account="111")
got = store.get_trades(limit=10, account="111")
check("update in place", len(got) == 1 and got[0]["net_profit"] == 9.0, got)
store.upsert_trades([{"position_id": "2", "symbol": "EURUSDm", "net_profit": -3.0,
                      "close_time": int(time.time()), "source": "mt5"}], account="222")
check("account 111 sees only its own", [t["position_id"] for t in store.get_trades(account="111")] == ["1"],
      store.get_trades(account="111"))
check("account 222 sees only its own", [t["position_id"] for t in store.get_trades(account="222")] == ["2"],
      store.get_trades(account="222"))
check("count per account", store.count_trades("111") == 1 and store.count_trades("222") == 1)
check("count all", store.count_trades() == 2)
check("tagged rows carry the account", got[0]["account"] == "111", got[0])

print("\n7. EA bridge normalisation")
pos = Engine._norm_ea_position({"ticket": 5, "symbol": "EURUSDm", "type": "BUY", "volume": 0.02,
                                "price_open": 1.1, "price": 1.105, "profit": 10.0, "swap": -0.2}, {})
check("position normalised", pos["side"] == "buy" and pos["net"] == 9.8, pos)
tr = Engine._norm_ea_trade({"position_id": "5", "symbol": "EURUSDm", "profit": 10.0,
                            "commission": -0.14, "swap": -0.2}, {})
check("trade normalised net", tr["net_profit"] == 9.66, tr)
check("trade needs an id", Engine._norm_ea_trade({}, {}) is None)

print("\n8. engine: login gate + live session with a mocked terminal")
mc.mt5 = FakeMT5(init_ok=True, trade_mode=2)
store2 = Store(Path(tempfile.mkdtemp()) / "t2.db")
sent = []
eng = Engine(store2, publisher=lambda payload, force: sent.append(payload))
eng.start()
time.sleep(0.6)

r = eng.submit("connect", {"login": "", "server": "Exness-MT5Real"}, timeout=10)
check("login is refused without an account number", r.get("ok") is False, r)
check("no data before login", eng.snapshot().get("source") == "none", eng.snapshot().get("source"))
check("dashboard stays locked before login", eng.snapshot().get("connected") is False)
check("no history before login", len(eng.snapshot().get("history") or []) == 0)

r = eng.submit("connect", {"login": 12345678, "password": "pw", "server": "Exness-MT5Real"}, timeout=30)
check("login ok", r.get("ok") is True, r)
time.sleep(1.0)
snap = eng.snapshot()
check("snapshot connected", snap.get("connected") is True, snap.get("status"))
check("source is the MT5 terminal", snap.get("source") == "mt5", snap.get("source"))
check("real account flagged", snap.get("account_kind") == "real", snap.get("account_kind"))
check("account present", (snap.get("account") or {}).get("login") == 12345678, snap.get("account"))
check("positions present", len(snap.get("positions") or []) == 2, len(snap.get("positions") or []))
check("floating P/L computed", snap["stats"]["today"]["floating"] == 24.5, snap["stats"]["today"]["floating"])
check("history loaded for this account", len(snap.get("history") or []) == 2, len(snap.get("history") or []))
check("history rows tagged", all(t.get("account") == "12345678" for t in snap["history"]), snap["history"])
check("history_total scoped to account", snap.get("history_total") == 2, snap.get("history_total"))
check("server clock string", len(snap.get("server_time", "")) == 19, snap.get("server_time"))
check("publisher received data", len(sent) > 0, len(sent))
check("bot6 exposed in the snapshot", isinstance(snap.get("bot6"), dict) and "config" in snap["bot6"],
      list((snap.get("bot6") or {}).keys())[:6])
check("bot7 exposed in the snapshot", isinstance(snap.get("bot7"), dict) and "config" in snap["bot7"],
      list((snap.get("bot7") or {}).keys())[:6])
check("bot6 has its own magic", (snap.get("bot6") or {}).get("config", {}).get("magic") == 777006,
      (snap.get("bot6") or {}).get("config", {}).get("magic"))
check("bot7 has its own magic", (snap.get("bot7") or {}).get("config", {}).get("magic") == 777007,
      (snap.get("bot7") or {}).get("config", {}).get("magic"))

# another account's trades must never leak into this session
store2.upsert_trades([{"position_id": "999", "symbol": "GBPUSDm", "net_profit": 500.0,
                       "close_time": int(time.time()), "source": "mt5"}], account="555")
time.sleep(0.8)
snap = eng.snapshot()
check("other account's trades are hidden",
      all(str(t.get("position_id")) != "999" for t in snap["history"]), [t["position_id"] for t in snap["history"]])
check("other account's total is hidden", snap.get("history_total") == 2, snap.get("history_total"))

r = eng.submit("ea_event", {"type": "close", "account": 12345678, "position_id": "777001", "ticket": 777001,
                            "symbol": "XAUUSDm", "side": "sell", "volume": 0.05,
                            "profit": -12.5, "commission": -0.35, "swap": 0.0}, timeout=15)
check("ea close accepted", r.get("ok") is True, r)
time.sleep(0.6)
stored = store2.get_trades(symbol="XAUUSDm", limit=100, account="12345678")
check("ea trade persisted to the right account",
      any(str(t["position_id"]) == "777001" for t in stored), [t["position_id"] for t in stored][:5])

print("\n9. engine: disconnect wipes the whole session and locks the dashboard")
r = eng.submit("disconnect", {}, timeout=15)
check("disconnect ok", r.get("ok") is True, r)
time.sleep(1.0)
snap = eng.snapshot()
check("logged out", snap.get("connected") is False, snap.get("status"))
check("dashboard is locked", snap.get("locked") is True, snap.get("locked"))
check("source back to none", snap.get("source") == "none", snap.get("source"))
check("account cleared", not snap.get("account"), snap.get("account"))
check("positions cleared", len(snap.get("positions") or []) == 0, snap.get("positions"))
check("history cleared from the live view", len(snap.get("history") or []) == 0, len(snap.get("history") or []))
check("statistics cleared", snap["stats"]["today"]["trades"] == 0, snap["stats"]["today"])
check("history_total cleared", snap.get("history_total") == 0, snap.get("history_total"))
check("status is disconnected", snap.get("status") == "disconnected", snap.get("status"))

# the EA may keep pushing after a logout - it must NOT reopen the dashboard
eng.submit("ea_event", {"type": "snapshot", "account": {"login": 12345678, "balance": 9999},
                        "positions": [{"ticket": 9, "symbol": "XAUUSDm", "profit": 5.0}]}, timeout=15)
eng.submit("ea_event", {"type": "heartbeat", "ea_name": "x"}, timeout=15)
time.sleep(0.8)
snap = eng.snapshot()
check("EA traffic cannot unlock a logged-out dashboard", snap.get("connected") is False, snap.get("status"))
check("EA positions stay hidden while logged out", len(snap.get("positions") or []) == 0, snap.get("positions"))
check("status stays disconnected after EA traffic", snap.get("status") == "disconnected", snap.get("status"))

r = eng.submit("connect", {"login": 12345678, "password": "pw", "server": "Exness-MT5Real"}, timeout=30)
time.sleep(1.0)
snap = eng.snapshot()
check("re-login unlocks the dashboard", snap.get("locked") is False and snap.get("connected") is True, snap.get("status"))
check("re-login restores the account history", len(snap.get("history") or []) == 3, len(snap.get("history") or []))
eng.stop()

print("\n10. demo mode is gone")
check("no simulator module", not (ROOT / "backend" / "engine_sim.py").exists())
check("engine has no demo branch", not hasattr(eng, "sim"))
import backend.app as backend_app  # noqa: E402
src = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
check("api/connect validates the login", "Exness account login din" in src)
check("api/connect never consults a simulator", "engine_sim" not in src and "sim" != getattr(backend_app.engine, "mode", ""))
html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
check("no demo button in the UI", "demo-btn" not in html and "start-demo" not in html)
js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
check("no demo simulator reference in JS", "demoMode" not in js and "'demo'" not in js.replace("account_kind === 'demo'", ""))

print("\n11. Exness server list offered at connect time")
from backend.config import EXNESS_SERVERS, server_list   # noqa: E402

EXPECT_REAL = ["Exness-MT5Real6", "Exness-MT5Real7", "Exness-MT5Real17", "Exness-MT5Real18",
               "Exness-MT5Real20", "Exness-MT5Real24", "Exness-MT5Real26", "Exness-MT5Real28",
               "Exness-MT5Real29", "Exness-MT5Real38"]
EXPECT_TRIAL = ["Exness-MT5Trial6", "Exness-MT5Trial7", "Exness-MT5Trial14",
                "Exness-MT5Trial16", "Exness-MT5Trial17"]
check("real servers match the user's list", EXNESS_SERVERS["real"] == EXPECT_REAL, EXNESS_SERVERS["real"])
check("trial servers match the user's list", EXNESS_SERVERS["trial"] == EXPECT_TRIAL, EXNESS_SERVERS["trial"])
check("no duplicate server names", len(server_list()) == len(set(server_list())), server_list())
cfg_payload = asyncio.run(backend_app.api_config())
check("/api/config exposes grouped servers",
      cfg_payload.get("servers", {}).get("real") == EXPECT_REAL, cfg_payload.get("servers"))
check("/api/config exposes the flat list", cfg_payload.get("common_servers") == server_list(),
      cfg_payload.get("common_servers"))
html2 = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
check("login page has a server dropdown", 'id="server-select"' in html2 and "<select" in html2)
check("login page keeps a custom-server input", 'id="server"' in html2)
js2 = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
check("frontend builds the dropdown from the API", "buildServerSelect" in js2 and "CFG.servers" in js2)
check("server choice is remembered in the browser", "exdash.server" in js2)

print("\n12. MetaTrader requirement is surfaced honestly")
check("/api/config reports MT5 readiness", "mt5_ready" in cfg_payload, list(cfg_payload)[:6])
check("/api/config treats any MT5 build as ready",
      cfg_payload["mt5_ready"] == bool(cfg_payload["terminals"]), cfg_payload["mt5_ready"])
check("/api/config no longer requires Exness branding",
      "mt5_exness_branded" in cfg_payload, list(cfg_payload))
check("/api/config lists Exness terminals", "exness_terminals" in cfg_payload, cfg_payload.get("exness_terminals"))
check("/api/config offers the Exness download link",
      str(cfg_payload.get("exness_download", "")).startswith("https://www.exness.com/"), cfg_payload.get("exness_download"))
check("login page shows an MT5 status strip", 'id="mt5-status"' in html2)
check("frontend renders the MT5 status", "renderMt5Status" in js2 and "exness_terminals" in js2)
check("MT5 status is translated (EN + BN)",
      all(k in js2 for k in ("mt5Ready:", "mt5NoExness:", "mt5Missing:", "mt5Download:")))
check("login page auto-rechecks for a newly installed terminal",
      "loadConfig(true)" in js2 and "mt5-recheck" in js2)
check("unlimited leverage is labelled, not shown as 1:2000000000",
      "1e9" in js2 and "unlimited:" in js2)
check("no false 'another broker cannot log in' claim in the UI",
      "cannot log in to an Exness account" not in js2 and "cannot log into an Exness" not in js2)
check("Exness terminal is pre-selected when found", "exness_terminals[0]" in js2)

# ---------------------------------------------------------- fake broker
class FakeBrokerMT5:
    """A tiny MT5 stand-in that really fills stop orders and hits stop-losses."""

    TRADE_ACTION_PENDING, TRADE_ACTION_SLTP, TRADE_ACTION_REMOVE, TRADE_ACTION_DEAL = 5, 6, 8, 1
    ORDER_TIME_GTC = 0
    __version__ = "fake-broker"

    def __init__(self, symbol="XAUUSDm", bid=2650.00, ask=2650.20, symbol_names=None):
        self.symbol = symbol
        self.symbol_names = symbol_names if symbol_names is not None else [symbol]
        self.bid = bid
        self.ask = ask
        self.positions: Dict[int, dict] = {}
        self.orders: Dict[int, dict] = {}
        self.deals: List[dict] = []
        self._ticket = 900000
        self.requests: List[dict] = []
        self.rejected = 0
        self._t = int(time.time())
        self.tick_step = 60

    # ---- market data -------------------------------------------------
    def symbol_info(self, name):
        if name != self.symbol and name not in self.symbol_names:
            return None
        return SimpleNamespace(
            name=name, digits=2, point=0.01, visible=True, trade_mode=4, trade_allowed=True,
            trade_tick_value=1.0, trade_tick_size=0.01, trade_contract_size=100.0,
            volume_min=0.01, volume_max=50.0, volume_step=0.01,
            trade_stops_level=0, trade_freeze_level=0, filling_mode=1,
        )

    def symbol_select(self, name, enable=True):
        return True

    def symbols_get(self, *a, **k):
        return [SimpleNamespace(name=n) for n in self.symbol_names]

    def symbol_info_tick(self, name):
        self._t += self.tick_step
        return SimpleNamespace(bid=self.bid, ask=self.ask, time=self._t)

    def last_error(self):
        return (0, "ok")
    # ---- account state -----------------------------------------------
    def positions_get(self, symbol=None):
        return [SimpleNamespace(**p) for p in self.positions.values()]

    def orders_get(self, symbol=None):
        return [SimpleNamespace(**o) for o in self.orders.values()]

    def _recompute(self):
        for p in self.positions.values():
            price = self.bid if p["type"] == 0 else self.ask
            mult = 100.0 * p["volume"]
            p["price_current"] = price
            p["profit"] = round((price - p["price_open"]) * mult if p["type"] == 0
                                else (p["price_open"] - price) * mult, 2)

    def set_price(self, bid, ask=None):
        self.bid = bid
        self.ask = (ask if ask is not None else bid + 0.20)
        self._recompute()
        # trigger pending stop orders
        for ticket, o in list(self.orders.items()):
            hit = (o["type"] == 4 and self.ask >= o["price_open"]) or \
                  (o["type"] == 5 and self.bid <= o["price_open"])
            if hit:
                self.orders.pop(ticket, None)
                self.positions[ticket] = {
                    "ticket": ticket, "symbol": o["symbol"], "type": 0 if o["type"] == 4 else 1,
                    "volume": o["volume_current"], "price_open": o["price_open"],
                    "price_current": o["price_open"], "sl": o["sl"], "tp": 0.0,
                    "profit": 0.0, "swap": 0.0, "magic": o["magic"], "comment": "filled",
                    "time": int(time.time()), "time_update": int(time.time()),
                }
        # hit stop-losses
        for ticket, p in list(self.positions.items()):
            sl = float(p.get("sl") or 0.0)
            if not sl:
                continue
            hit = (p["type"] == 0 and self.bid <= sl) or (p["type"] == 1 and self.ask >= sl)
            if hit:
                self.positions.pop(ticket, None)
                mult = 100.0 * p["volume"]
                pnl = round((sl - p["price_open"]) * mult if p["type"] == 0
                            else (p["price_open"] - sl) * mult, 2)
                self.deals.append({"position_id": ticket, "profit": pnl, "price": sl})
        self._recompute()

    # ---- trading -----------------------------------------------------
    def order_send(self, req):
        self.requests.append(dict(req))
        action = req.get("action")
        if action == self.TRADE_ACTION_PENDING:
            self._ticket += 1
            self.orders[self._ticket] = {
                "ticket": self._ticket, "symbol": req["symbol"], "type": req["type"],
                "state": 1, "volume_current": req["volume"], "volume_initial": req["volume"],
                "price_open": req["price"], "sl": req["sl"], "tp": req.get("tp", 0.0),
                "magic": req.get("magic", 0), "comment": req.get("comment", ""),
                "price_current": req["price"], "time_setup": int(time.time()),
            }
            return SimpleNamespace(retcode=10008, order=self._ticket, comment="placed")
        if action == self.TRADE_ACTION_SLTP:
            tid = int(req["position"])
            if tid in self.positions:
                self.positions[tid]["sl"] = float(req["sl"])
                return SimpleNamespace(retcode=10009, comment="sltp")
            return SimpleNamespace(retcode=10013, comment="no position")
        if action == self.TRADE_ACTION_REMOVE:
            self.orders.pop(int(req["order"]), None)
            return SimpleNamespace(retcode=10009, comment="removed")
        if action == self.TRADE_ACTION_DEAL:
            tid = int(req["position"])
            if tid in self.positions:
                p = self.positions.pop(tid)
                mult = 100.0 * p["volume"]
                price = req["price"]
                pnl = round((price - p["price_open"]) * mult if p["type"] == 0
                            else (p["price_open"] - price) * mult, 2)
                self.deals.append({"position_id": tid, "profit": pnl, "price": price})
                return SimpleNamespace(retcode=10009, comment="closed")
            return SimpleNamespace(retcode=10013, comment="no position")
        self.rejected += 1
        return SimpleNamespace(retcode=10013, comment="bad action")


print("\n14. gold auto-trading bot (strategy simulation)")

import backend.bot as botmod  # noqa: E402
from backend.bot import GoldBot  # noqa: E402

_orig_bot_mt5 = botmod.mt5
fake = FakeBrokerMT5()
botmod.mt5 = fake
try:
    bot = GoldBot()
    check("gold symbol auto-detected", bot.resolve_symbol() == "XAUUSDm", bot.symbol)
    check("symbol is flagged as available", bot.symbol_ok is True)
    check("only XAUUSDm is traded by default", bot.cfg["symbol"] == "XAUUSDm", bot.cfg["symbol"])
    bot.update_config({"running": True, "lot": 0.01, "distance": 2.0, "cooldown_sec": 0})
    bot.tick()

    orders = list(fake.orders.values())
    check("two pending orders armed", len(orders) == 2, orders)
    buy_stop = next((o for o in orders if o["type"] == 4), None)
    sell_stop = next((o for o in orders if o["type"] == 5), None)
    check("BuyStop = ask + $2", buy_stop and buy_stop["price_open"] == 2652.20, buy_stop and buy_stop["price_open"])
    check("SellStop = bid - $2", sell_stop and sell_stop["price_open"] == 2648.00, sell_stop and sell_stop["price_open"])
    check("BuyStop SL sits on the SellStop level", buy_stop and buy_stop["sl"] == 2648.00, buy_stop and buy_stop["sl"])
    check("SellStop SL sits on the BuyStop level", sell_stop and sell_stop["sl"] == 2652.20, sell_stop and sell_stop["sl"])
    check("money per $1 move = $1 at 0.01 lot", round(bot.money_per_price(0.01), 4) == 1.0,
          bot.money_per_price(0.01))
    check("risk per trade = $4.00", bot.risk_per_trade() == 4.0, bot.risk_per_trade())

    # ---- the SL box sets both legs' stop-loss distance (0 = old opposite-level rule)
    bot.update_config({"sl_distance": 3.0})
    fake.orders.clear(); fake.positions.clear(); fake.set_price(2650.00, 2650.20)
    bot.tick()
    sls = sorted((o["type"], o["sl"]) for o in fake.orders.values())
    check("SL box $3.00 -> BuyStop SL = entry - 3.00",
          any(t == 4 and abs(s - 2649.20) < 1e-6 for t, s in sls), sls)
    check("SL box $3.00 -> SellStop SL = entry + 3.00",
          any(t == 5 and abs(s - 2651.00) < 1e-6 for t, s in sls), sls)
    check("risk follows the SL box ($3.00 at 0.01 lot)", bot.risk_per_trade() == 3.0, bot.risk_per_trade())
    bot.update_config({"sl_distance": 0})
    check("SL box empty -> back to 2 x gap", bot.effective_sl_distance() == 4.0, bot.effective_sl_distance())
    # re-arm so the following strategy checks start from a clean straddle
    bot.update_config({"lot": 0.01, "distance": 2.0})
    bot._reset_until = 0.0
    bot.cooldown_until = 0.0
    bot._known_positions.clear()
    fake.orders.clear(); fake.positions.clear()
    bot.tick()
    check("re-armed for the next checks", len(fake.orders) == 2, fake.orders)

    # ---- the buy stop triggers (entry 2652.20, original SL 2648.00)
    fake.set_price(2652.30, 2652.40)
    pos = list(fake.positions.values())
    check("BuyStop triggered into a buy position", len(pos) == 1 and pos[0]["type"] == 0, pos)
    bot.tick()

    # profit +0.80 -> below the $1 lock trigger, SL must stay where it was
    fake.set_price(2653.00, 2653.10)
    bot.tick()
    check("$0.80 profit -> SL = 75% (2652.80)", fake.positions[pos[0]["ticket"]]["sl"] == 2652.80,
          fake.positions[pos[0]["ticket"]]["sl"])

    # profit +1.10 -> first lock: SL = entry + $0.10
    fake.set_price(2653.30, 2653.40)
    bot.tick()
    sl = fake.positions[pos[0]["ticket"]]["sl"]
    check("profit $1.10 -> SL = 75% (2653.02)", sl == 2653.02, sl)

    # profit +4.10 -> trailing: SL = entry + profit/2 = 2654.25
    fake.set_price(2656.30, 2656.40)
    bot.tick()
    sl = fake.positions[pos[0]["ticket"]]["sl"]
    check("profit $4.10 -> SL = 75% (2655.27)", sl == 2655.27, sl)

    # profit falls back to +3.10 -> trailing must NOT move back down
    fake.set_price(2655.30, 2655.40)
    bot.tick()
    sl = fake.positions[pos[0]["ticket"]]["sl"]
    check("trailing never moves backwards", sl == 2655.27, sl)

    # ---- STOP must cancel the leftover SellStop
    bot.update_config({"running": False})
    bot.tick()
    check("STOP cancels the pending orders", len(fake.orders) == 0, fake.orders)
    check("STOP keeps trailing the open position", pos[0]["ticket"] in fake.positions)

    # ---- virtual SL hit closes the position and books the win
    fake.set_price(2654.00, 2654.10)     # bid <= SL 2654.25 -> stopped out with profit
    bot.tick()
    check("position closed on its stop-loss", len(fake.positions) == 0, fake.positions)
    check("closed trade booked as a win", bot.stats["closed"] == 1 and bot.stats["wins"] == 1, bot.stats)
    check("realized money is positive", bot.stats["realized"] > 0, bot.stats)

    # ---- re-arm after the cooldown
    bot.update_config({"running": True, "cooldown_sec": 0})
    bot.tick()
    check("re-arms the straddle after a close", len(fake.orders) == 2, fake.orders)

    # ---- panic close
    fake.set_price(2660.00, 2660.20)     # buy stop triggers again
    fake.set_price(2662.50, 2662.60)
    bot.tick()
    n = bot.close_all(reason="test")
    check("close-now closes the position", n == 1 and len(fake.positions) == 0, (n, fake.positions))
    check("close-now also cancels pending orders", len(fake.orders) == 0, fake.orders)

    # ---- STOP must not arm anything
    bot.update_config({"running": False})
    fake.orders.clear(); fake.positions.clear()
    bot.tick()
    check("stopped bot places no orders", len(fake.orders) == 0, fake.orders)

    # ---- desired_lock() matrix
    bot.update_config({"lock_trigger": 0.4, "lock_amount": 0.2, "trail_start": 0.8, "trail_pct": 75})
    check("lock matrix: $0.30 -> nothing", bot.desired_lock(0.30) == 0.0)
    check("lock matrix: $0.40 -> $0.20", abs(bot.desired_lock(0.40) - 0.20) < 1e-9)
    check("lock matrix: $0.70 -> $0.20 (unchanged)", abs(bot.desired_lock(0.70) - 0.20) < 1e-9)
    check("lock matrix: $0.80 -> $0.60 (75%)", abs(bot.desired_lock(0.80) - 0.60) < 1e-9)
    check("lock matrix: $6.00 -> $4.50 (75%)", abs(bot.desired_lock(6.00) - 4.50) < 1e-9)

    # ---- lot rounding respects the broker limits
    bot.update_config({"lot": 0.017})
    check("lot rounded to the broker step", bot.cfg["lot"] == 0.02, bot.cfg["lot"])
    bot.update_config({"lot": 999})
    check("lot clamped to volume_max", bot.cfg["lot"] == 50.0, bot.cfg["lot"])

    # ---- the Gap field drives the straddle distance
    fake.orders.clear(); fake.positions.clear()
    bot.update_config({"lot": 0.01, "distance": 3.5, "running": True, "cooldown_sec": 0})
    bot.cooldown_until = 0
    bot.tick()
    prices = sorted(o["price_open"] for o in fake.orders.values())
    check("Gap $3.5 -> BuyStop at ask + $3.5",
          any(abs(p - (fake.ask + 3.5)) < 1e-6 for p in prices), (prices, fake.ask))
    check("Gap $3.5 -> SellStop at bid - $3.5",
          any(abs(p - (fake.bid - 3.5)) < 1e-6 for p in prices), (prices, fake.bid))
    check("risk follows the gap ($7.00 at 0.01 lot)", bot.risk_per_trade() == 7.0, bot.risk_per_trade())
    bot.update_config({"distance": 2.0})
    fake.orders.clear(); fake.positions.clear()

    # ---- SL may only ever move in the profit direction
    fake.orders.clear(); fake.positions.clear()
    fake.set_price(2650.00, 2650.20)
    bot.update_config({"running": True, "lot": 0.01, "distance": 2.0,
                       "lock_trigger": 0.4, "lock_amount": 0.2, "trail_start": 0.8, "trail_pct": 75,
                       "cooldown_sec": 0})
    bot.cooldown_until = 0.0
    bot._reset_until = 0.0
    bot._known_positions.clear()
    bot.tick()                                    # arms
    fake.set_price(2652.30, 2652.40)              # buy leg fills
    bot.tick()
    ticket = list(fake.positions.keys())[0]

    fake.set_price(2653.30, 2653.40)              # +$1.10 -> first lock
    bot.tick()
    sl_lock = fake.positions[ticket]["sl"]
    check("profit $1.10 -> SL = 75% (2653.02)", sl_lock == 2653.02, sl_lock)

    fake.set_price(2653.80, 2653.90)              # +$1.60 -> half of profit
    bot.tick()
    sl_half = fake.positions[ticket]["sl"]
    check("profit $1.60 -> SL = 75% ($1.20)", sl_half == 2653.40, sl_half)

    fake.set_price(2653.60, 2653.70)              # profit falls back to $1.40 (above the SL)
    bot.tick()
    check("profit falling back keeps the last SL (no give-back)",
          fake.positions[ticket]["sl"] == sl_half, fake.positions[ticket]["sl"])

    fake.set_price(2653.50, 2653.60)              # profit falls to $1.30, still above the SL
    bot.tick()
    check("profit below the lock keeps the last SL",
          fake.positions[ticket]["sl"] == sl_half, fake.positions[ticket]["sl"])

    fake.set_price(2657.00, 2657.10)              # +$4.80 -> SL = +$2.40
    bot.tick()
    sl_new = fake.positions[ticket]["sl"]
    check("a bigger profit moves the SL further up (2655.80)", sl_new == 2655.80, sl_new)
    check("SL only ever increased", sl_lock < sl_half < sl_new, (sl_lock, sl_half, sl_new))
    fake.orders.clear(); fake.positions.clear()
    bot._reset_until = 0.0
    bot._known_positions.clear()

    # ---- SELL side: same half-of-profit rule, SL only ever moves DOWN
    fake.orders.clear(); fake.positions.clear()
    fake.set_price(2660.00, 2660.20)
    bot.update_config({"running": True, "lot": 0.01, "distance": 2.0, "sl_distance": 0,
                       "trail_start": 1.5, "cooldown_sec": 0})
    bot._reset_until = 0.0
    bot.cooldown_until = 0.0
    bot._known_positions.clear()
    bot.tick()                                    # arms: Buy 2662.20 / Sell 2658.00
    fake.set_price(2657.90, 2658.00)              # SellStop fills
    bot.tick()
    check("sell leg filled", len(fake.positions) == 1 and list(fake.positions.values())[0]["type"] == 1,
          fake.positions)
    st = list(fake.positions.keys())[0]

    fake.set_price(2656.30, 2656.40)              # sell profit = (2658.00-2656.40)*1 = $1.60
    bot.tick()
    sl1 = fake.positions[st]["sl"]
    check("sell: $1.60 profit -> SL = 75% ($1.20)", sl1 == 2656.80, sl1)

    fake.set_price(2652.00, 2652.10)              # profit = $5.90 -> SL = entry - 2.95
    bot.tick()
    sl2 = fake.positions[st]["sl"]
    check("sell: bigger profit moves the SL further down (2653.57)", sl2 == 2653.57, sl2)
    check("sell SL only ever moved down", sl2 < sl1, (sl1, sl2))

    fake.set_price(2653.00, 2653.10)              # profit shrinks to $5.00 (stays below the SL)
    bot.tick()
    check("sell: profit shrinking keeps the last SL (no give-back)",
          fake.positions[st]["sl"] == sl2, fake.positions[st]["sl"])
    fake.orders.clear(); fake.positions.clear()
    bot._reset_until = 0.0
    bot._known_positions.clear()

    # ---- pre-trade opens once per new M1 candle (60 s)
    fake.orders.clear(); fake.positions.clear()
    fake.tick_step = 0                       # stay inside the same candle
    bot.update_config({"running": True, "lot": 0.01, "distance": 2.0, "sl_distance": 0,
                       "arm_on_candle": True, "cooldown_sec": 0})
    bot._reset_until = 0.0; bot.cooldown_until = 0.0; bot._last_bar = 0
    bot._known_positions.clear()
    bot.tick()
    check("first call takes the pre-trade on the current candle", len(fake.orders) == 2, fake.orders)
    fake.orders.clear()
    bot.tick()
    check("no second pre-trade inside the same candle", len(fake.orders) == 0, fake.orders)
    fake._t += 60                            # a new 1-minute candle opens
    bot.tick()
    check("a new candle opens a fresh pre-trade", len(fake.orders) == 2, fake.orders)
    fake.tick_step = 60
    bot.update_config({"arm_on_candle": False})
    fake.orders.clear(); bot._reset_until = 0.0; bot._known_positions.clear()
    # ---- re-arm is immediate (cooldown 1 s by default)
    check("default re-arm cooldown is 1 s", GoldBot().cfg["cooldown_sec"] == 1, GoldBot().cfg["cooldown_sec"])

    # ---- SL hit: BOTH sides of the old setup are cleared, then a brand new
    #      straddle is built from the live price
    fake.orders.clear(); fake.positions.clear()
    fake.set_price(2650.00, 2650.20)
    bot.update_config({"running": True, "lot": 0.01, "distance": 2.0, "cooldown_sec": 0})
    bot.cooldown_until = 0.0
    bot._reset_until = 0.0
    bot._known_positions.clear()
    bot.tick()
    check("cycle armed for the reset test", len(fake.orders) == 2, fake.orders)

    fake.set_price(2652.30, 2652.40)          # BuyStop fills
    bot.tick()
    check("buy leg filled, sell stop still pending",
          len(fake.positions) == 1 and len(fake.orders) == 1, (fake.positions, fake.orders))

    old_level = list(fake.orders.values())[0]["price_open"]
    fake.set_price(2647.90, 2648.00)          # buy's SL hit + SellStop fills at once
    check("the stop-out produced a leftover sell position",
          len(fake.positions) == 1 and list(fake.positions.values())[0]["type"] == 1, fake.positions)

    bot.tick()                                # detect stop-out -> clear both sides
    check("stop-out closed the surviving position", len(fake.positions) == 0, fake.positions)
    check("stop-out cancelled the leftover pending order", len(fake.orders) == 0, fake.orders)

    bot.tick()                                # -> fresh straddle, current price 2647.90/2648.00
    prices = sorted(o["price_open"] for o in fake.orders.values())
    check("fresh BuyStop = live ask + gap", any(abs(p - 2650.00) < 1e-6 for p in prices), prices)
    check("fresh SellStop = live bid - gap", any(abs(p - 2645.90) < 1e-6 for p in prices), prices)
    check("the old level is not reused", all(abs(p - old_level) > 0.01 for p in prices), (prices, old_level))
    check("the reset cycle is not counted as a new stop-out",
          bot.stats["closed"] >= 2, bot.stats)
    fake.orders.clear(); fake.positions.clear()
    bot._reset_until = 0.0

    # ---- XAUUSDm missing -> same instrument under another spelling is used, openly
    other = FakeBrokerMT5(symbol="XAUUSD")
    botmod.mt5 = other
    bot3 = GoldBot()
    bot3.update_config({"running": True, "symbol": "XAUUSDm"})
    got = bot3.resolve_symbol(force=True)
    check("XAUUSDm missing -> falls back to the same gold instrument", got == "XAUUSD", got)
    check("the fallback is reported, not hidden", bot3.symbol_substituted == "XAUUSDm",
          bot3.symbol_substituted)
    check("the fallback counts as tradeable", bot3.symbol_ok is True)
    bot3.tick()
    check("it arms on the fallback symbol", len(other.orders) == 2, other.orders)
    check("gold symbol list is exposed for the picker", "XAUUSD" in bot3.available_gold_symbols(),
          bot3.gold_symbols)

    # ---- no gold at all -> refuse to trade and say why
    no_gold = FakeBrokerMT5(symbol="EURUSD")
    botmod.mt5 = no_gold
    bot4 = GoldBot()
    bot4.update_config({"running": True, "symbol": "XAUUSDm"})
    check("no gold at all -> refuses to trade", bot4.resolve_symbol(force=True) == "", bot4.symbol)
    check("the refusal is flagged", bot4.symbol_ok is False)
    check("the error says the symbol is not available", "not available" in bot4.last_error, bot4.last_error)
    # ---- BTC can be selected and is traded instead of gold
    dual = FakeBrokerMT5(symbol="XAUUSD", symbol_names=["XAUUSD", "XAUUSDm", "XAUUSD247", "BTCUSD",
                                                        "BTCUSDm", "EURUSD", "XAGUSD"])
    botmod.mt5 = dual
    bot5 = GoldBot()
    bot5.update_config({"running": True, "lot": 0.01, "symbol": "XAUUSD"})
    bot5.resolve_symbol(force=True)
    opts = bot5.available_gold_symbols(force=True)
    check("picker offers gold AND BTC", "XAUUSD" in opts and "BTCUSD" in opts, opts)
    check("silver is not offered", all("XAG" not in o for o in opts), opts)
    check("gold is listed before BTC", opts.index("XAUUSD") < opts.index("BTCUSD"), opts)
    check("XAUUSD247 (24/7 gold) is offered near the top", opts.index("XAUUSD247") <= 2, opts)

    bot5.update_config({"symbol": "XAUUSD247"})
    bot5.resolve_symbol(force=True)
    check("XAUUSD247 can be selected and traded", bot5.symbol == "XAUUSD247", bot5.symbol)
    bot5.tick(); bot5.tick()
    check("the bot arms on XAUUSD247",
          len(dual.orders) == 2 and all(o["symbol"] == "XAUUSD247" for o in dual.orders.values()),
          {t: o["symbol"] for t, o in dual.orders.items()})
    dual.orders.clear(); dual.positions.clear()

    bot5.update_config({"symbol": "BTCUSD"})
    bot5.resolve_symbol(force=True)
    check("choosing BTCUSD switches the traded symbol", bot5.symbol == "BTCUSD", bot5.symbol)
    bot5.tick(); bot5.tick()
    check("the bot arms on BTCUSD, not gold",
          len(dual.orders) == 2 and all(o["symbol"] == "BTCUSD" for o in dual.orders.values()),
          {t: o["symbol"] for t, o in dual.orders.items()})

    # and BTCUSDm falls back to BTCUSD when only that spelling exists
    btc_only = FakeBrokerMT5(symbol="BTCUSD", bid=64000.0, ask=64010.0,
                             symbol_names=["BTCUSD"])
    botmod.mt5 = btc_only
    bot6 = GoldBot()
    bot6.update_config({"symbol": "BTCUSDm"})
    check("an explicitly picked market is never silently swapped",
          bot6.resolve_symbol(force=True) == "", bot6.symbol)
    check("the error lists what is available", "not available" in bot6.last_error, bot6.last_error)
finally:
    botmod.mt5 = _orig_bot_mt5

print("\n15. frontend files exist")
for f in ("web/index.html", "web/app.js", "web/styles.css", "ea/DashboardBridge.mq5",
          "ea/DashboardBridge.mqh", "start.bat", "README.md", "backend/trend_bot.py", "backend/extreme_bot.py"):
    check(f"file {f}", (ROOT / f).exists())

print("\n16. candle momentum & market structure bot (Bot 2)")
from backend.trend_bot import TrendStructureBot
import backend.trend_bot as tbmod

tbot = TrendStructureBot()
st = tbot.status()
check("bot2 default magic is 777002", st["config"]["magic"] == 777002)
check("bot2 default tp is 0.10", st["config"]["tp"] == 0.10)
check("bot2 default sl is 2.00", st["config"]["sl"] == 2.00)
check("bot2 default max trades is 1", st["config"]["max_trades"] == 1)
check("bot2 default timeframe is D1", st["config"]["timeframe"] == "D1")

# config update
upd = tbot.update_config({"lot": 0.05, "max_trades": 3, "tp": 0.20, "sl": 1.50, "timeframe": "W1"})
check("bot2 update lot", upd["lot"] == 0.05)
check("bot2 update max_trades", upd["max_trades"] == 3)
check("bot2 update tp", upd["tp"] == 0.20)
check("bot2 update sl", upd["sl"] == 1.50)
check("bot2 update timeframe", upd["timeframe"] == "W1")

# max trades limit test
tbot.cfg["running"] = True
tbot.cfg["max_trades"] = 2
tbot.symbol = "BTCUSDTm"
tbot.symbol_ok = True
fake_pos = [
    {"ticket": 1, "side": "buy", "volume": 0.01, "price_open": 83000.0, "net": 0.02, "magic": 777002},
    {"ticket": 2, "side": "sell", "volume": 0.01, "price_open": 83050.0, "net": -0.05, "magic": 777002},
]
called = []
tbot._open_market_trade = lambda side, comment: called.append((side, comment))
tbot._check_and_trade(fake_pos)
check("max running trades limit blocks new entry", len(called) == 0, called)

# TP and SL active management
tbot.cfg["tp"] = 0.10
tbot.cfg["sl"] = 2.00
closed_calls = []
tbot._close_position = lambda p, reason: closed_calls.append((p["ticket"], reason))
p_win = {"ticket": 10, "side": "buy", "volume": 0.01, "net": 0.12, "magic": 777002}
p_loss = {"ticket": 11, "side": "sell", "volume": 0.01, "net": -2.05, "magic": 777002}
p_ok = {"ticket": 12, "side": "buy", "volume": 0.01, "net": 0.05, "magic": 777002}
tbot._manage_positions([p_win, p_loss, p_ok])
check("TP reached auto-closes trade at profit >= $0.10", any(c[0] == 10 and "TP reached" in c[1] for c in closed_calls), closed_calls)
check("SL hit auto-closes trade at loss <= -$2.00", any(c[0] == 11 and "SL hit" in c[1] for c in closed_calls), closed_calls)
check("Normal trade not closed", not any(c[0] == 12 for c in closed_calls))

# WATCH OFF: candle-follow direction checks (down -> sell, up -> buy)
tbot._last_order_ts = 0
tbot.cfg["max_trades"] = 1
tbot.cfg["watch_enabled"] = False
tbot.cfg["trend_mode"] = False
tbot.compute_structure = lambda sym: {"d1": {}, "w1": {}}
tbot._digits = lambda sym: 2
tbot._point = lambda sym: 0.01

from unittest.mock import MagicMock
mock_tick = MagicMock()
mock_tick.ask = 83000.0
mock_tick.bid = 82990.0
tbmod.mt5 = MagicMock()
tbmod.mt5.symbol_info_tick.return_value = mock_tick
tbmod.mt5.copy_rates_from_pos.return_value = [{"open": 83050.0, "time": 1700000000}]  # DOWN

candle_called = []
tbot._open_market_trade = lambda side, comment: candle_called.append((side, comment))
tbot._check_and_trade([])
check("watch OFF: candle down enters SELL (follow)", any(c[0] == "sell" and "candle_down_sell" in c[1] for c in candle_called), candle_called)

candle_called.clear()
tbot._last_order_ts = 0
tbmod.mt5.copy_rates_from_pos.return_value = [{"open": 82900.0, "time": 1700000000}]  # UP (current ~82995 > open 82900)
tbot._check_and_trade([])
check("watch OFF: candle up enters BUY (follow)", any(c[0] == "buy" and "candle_up_buy" in c[1] for c in candle_called), candle_called)

# Watch % field is authoritative + Reverse flips the real order side
import time as _t
tbot.cfg["watch_enabled"] = True
tbot.cfg["trend_mode"] = True
tbot.cfg["watch_min_pct"] = 70.0
tbot._watch_provider = lambda sym: {"buyer_pct": 65.0, "seller_pct": 35.0, "updated_at": _t.time()}
_w_allowed, _w_reason = tbot._check_watch_threshold("buy")
check("watch % field blocks 65% when the field says 70%", not _w_allowed, _w_reason)
tbot.cfg["watch_min_pct"] = 50.0
_w_allowed, _w_reason = tbot._check_watch_threshold("buy")
check("watch % field allows 65% when the field says 50%", _w_allowed, _w_reason)
tbot.cfg["reverse"] = True
check("reverse flips a BUY decision to SELL", tbot._apply_reverse("buy") == "sell")
check("reverse flips a SELL decision to BUY", tbot._apply_reverse("sell") == "buy")
tbot.cfg["reverse"] = False
tbot.cfg["watch_min_pct"] = 60.0
tbot._watch_provider = None

# Trend Mode testing: "Market er Trand check korbe Timer ja bolbe oi Trand a trade nibe"
tbot.cfg["trend_mode"] = True
tbot.cfg["max_trades"] = 5
tbot.compute_market_trend = lambda sym: "UP"
candle_called.clear()
tbot._last_order_ts = 0
tbmod.mt5.copy_rates_from_pos.return_value = [{"open": 82900.0, "time": 1700000000}]  # UP candle
tbot._check_and_trade([])
check("trend mode UP: enters BUY trade", any(c[0] == "buy" and any(k in c[1] for k in ("bot2_trend_up_buy", "bot2_watch_buyer_buy")) for c in candle_called), candle_called)
check("trend mode UP: strictly blocks SELL trade", not any(c[0] == "sell" for c in candle_called), candle_called)

# Sequential 5s entry connected with max_trades
candle_called.clear()
tbot._last_order_ts = time.time()  # just traded (< 5s ago)
tbot._check_and_trade([])
check("trend mode: waits 2.5s before next trade", len(candle_called) == 0, candle_called)

tbot.compute_market_trend = lambda sym: "DOWN"
candle_called.clear()
tbot._last_order_ts = 0
tbmod.mt5.copy_rates_from_pos.return_value = [{"open": 83050.0, "time": 1700000000}]  # DOWN candle
tbot._check_and_trade([])
check("trend mode DOWN: enters SELL trade", any(c[0] == "sell" and any(k in c[1] for k in ("bot2_trend_down_sell", "bot2_watch_seller_sell")) for c in candle_called), candle_called)
check("trend mode DOWN: strictly blocks BUY trade", not any(c[0] == "buy" for c in candle_called), candle_called)
tbot.cfg["trend_mode"] = False

# Panic close-all button
tbot._all_positions = lambda any_symbol=False: [p_win, p_loss]
tbot._close_position = lambda p, reason: True
closed_cnt = tbot.close_all("panic")
check("close_all closes all bot2 positions", closed_cnt == 2, closed_cnt)
check("close_all stops bot2", tbot.cfg["running"] is False)

print("\n17. extreme structure sniper bot (Bot 3)")
from backend.extreme_bot import ExtremeStructureBot
import backend.extreme_bot as ebmod

ebot = ExtremeStructureBot()
c3 = ebot.cfg
check("bot3 default magic is 777003", c3["magic"] == 777003, c3["magic"])
check("bot3 default tp is 0.50", c3["tp"] == 0.50, c3["tp"])
check("bot3 default sl is 5.00", c3["sl"] == 5.00, c3["sl"])
check("bot3 default max trades is 1", c3["max_trades"] == 1, c3["max_trades"])
check("bot3 default timeframe is D1", c3["timeframe"] == "D1", c3["timeframe"])

ebot.update_config({"lot": 0.05, "max_trades": 5, "tp": 1.50, "sl": 12.00, "timeframe": "W1"})
check("bot3 update lot", ebot.cfg["lot"] == 0.05, ebot.cfg["lot"])
check("bot3 update max_trades", ebot.cfg["max_trades"] == 5, ebot.cfg["max_trades"])
check("bot3 update tp", ebot.cfg["tp"] == 1.50, ebot.cfg["tp"])
check("bot3 update sl", ebot.cfg["sl"] == 12.00, ebot.cfg["sl"])
check("bot3 update timeframe", ebot.cfg["timeframe"] == "W1", ebot.cfg["timeframe"])

# Middle range filter: price between extremes -> NO TRADE
ebot.cfg["running"] = True
ebot.cfg["max_trades"] = 1
ebot.symbol = "XAUUSDm"
ebot.symbol_ok = True
ebot._last_order_ts = 0
ebot._digits = lambda s="": 2
ebot._point = lambda s="": 0.01

levels_sample = {
    "d1": {"hh": 4300.0, "lh": 4250.0, "hl": 4150.0, "ll": 4100.0},
    "w1": {"hh": 4400.0, "ll": 4050.0},
    "prev": {"hh": 4350.0, "ll": 4080.0},
    "active_view": "D1",
}
ebot.compute_all_levels = lambda s="": levels_sample

calls3 = []
ebot._open_market_trade = lambda side, comment: calls3.append((side, comment))

eb_tick = MagicMock()
eb_tick.ask = 4200.0
eb_tick.bid = 4199.8
ebmod.mt5 = MagicMock()
ebmod.mt5.symbol_info_tick.return_value = eb_tick

ebot._check_and_trade([])
check("middle zone strictly takes NO trade", len(calls3) == 0, calls3)

# Extreme Lower Low touch -> BUY
calls3.clear()
ebot._last_order_ts = 0
eb_tick.ask = 4100.2
eb_tick.bid = 4099.9
ebot._check_and_trade([])
check("extreme lower low touches trigger BUY", any(c[0] == "buy" for c in calls3), calls3)

# Extreme Higher High touch -> SELL
calls3.clear()
ebot._last_order_ts = 0
eb_tick.ask = 4300.2
eb_tick.bid = 4299.9
ebot._check_and_trade([])
check("extreme higher high touches trigger SELL", any(c[0] == "sell" for c in calls3), calls3)

# 5 planned trades computation
planned = ebot.compute_planned_trades(4180.0, levels_sample)
check("planned trades list contains 5 setups", len(planned) == 5, len(planned))
p1_ll = next((p for p in planned if p.get("id") == 1), None)
p2_hh = next((p for p in planned if p.get("id") == 2), None)
check("planned trade #1 is BUY @ LL", p1_ll is not None and p1_ll["action"] == "BUY" and p1_ll["price"] == 4100.0, p1_ll)
check("planned trade #2 is SELL @ HH", p2_hh is not None and p2_hh["action"] == "SELL" and p2_hh["price"] == 4300.0, p2_hh)

# Close all
p3_win = {"ticket": 801, "side": "buy", "volume": 0.01, "price_open": 4150.0, "net": 1.50, "magic": 777003}
ebot._all_positions = lambda any_symbol=False: [p3_win]
ebot._close_position = lambda p, reason: True
c3_closed = ebot.close_all("panic")
check("close_all closes bot3 positions", c3_closed == 1, c3_closed)
check("close_all stops bot3", ebot.cfg["running"] is False)

print("\n15. unittest suites (news / trend / extreme / pretrade / entry / reversal / sessions / market-ai / chart-feed / public-feed / order-flow / mt5-heal)")
import unittest  # noqa: E402
loader = unittest.TestLoader()
suite = unittest.TestSuite()
for mod in ("test_news_bot", "test_trend_bot", "test_extreme_bot", "test_pretrade_bot",
            "test_entry_bot", "test_reversal_bot", "test_sessions", "test_market_ai",
            "test_chart_feed", "test_public_feed", "test_order_flow_feed",
            "test_mt5_heal"):
    tests_dir = str(ROOT / "tests")
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    suite.addTests(loader.loadTestsFromName(mod))
runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w"))
result = runner.run(suite)
check("bot unittest suites pass", result.wasSuccessful(), f"{result.testsRun} tests")

print(f"\n{'=' * 46}\nPASSED {PASS}   FAILED {FAIL}\n{'=' * 46}")
sys.exit(1 if FAIL else 0)

