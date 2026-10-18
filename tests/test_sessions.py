"""
Unit tests for the trading-sessions board (Sydney / Tokyo / London / New York).

Runs as a plain script (no pytest needed):

    python tests/test_sessions.py

What is covered
---------------
* the live clock: daylight saving (summer vs winter UTC windows), the Monday to
  Friday week, countdowns and the London + New York "peak" overlap
* the measured AI reading: M15 bars clipped per session window, the average
  dollars up / down / range / drift, the BUY / SELL / MIXED verdict, the money
  conversion and the "this market moves the most here" summary
* the NewsBot integration (fetch, collect, cache, payload) under a mocked MT5
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import sessions as S                     # noqa: E402
from backend.news_bot import NewsBot, PLATFORM_MARKETS  # noqa: E402

UTC = S.UTC


def ts(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp()


def utc_hhmm(epoch):
    return datetime.fromtimestamp(epoch, UTC).strftime("%H:%M")


def row(rows, key):
    return next(r for r in rows if r["key"] == key)


def session_def(key):
    return next(s for s in S.SESSIONS if s["key"] == key)


def path_prices(n, p0, up, down, drift):
    """A 3-segment M15 path: +up, then -down, then close at p0 + drift."""
    seg = max(1, n // 3)
    out = []
    for i in range(n):
        if i < seg:
            p = p0 + up * (i / max(1, seg - 1))
        elif i < 2 * seg:
            p = p0 + up - (up + down) * ((i - seg) / max(1, seg - 1))
        else:
            p = p0 - down + (down + drift) * ((i - 2 * seg) / max(1, n - 2 * seg - 1))
        out.append(round(p, 2))
    return out


def window_start(sess, ref_ts):
    """Start of the session window whose local day is the day of ref_ts (None=weekend)."""
    zone = S._zone(str(sess["tz"]))
    day = datetime.fromtimestamp(ref_ts, zone).date()
    if day.weekday() >= 5:
        return None
    return datetime(day.year, day.month, day.day,
                    int(sess["open_hour"]), int(sess["open_min"]), tzinfo=zone).timestamp()


def window_bars(key, now, days=6, up=10.0, down=3.0, drift=7.0, p0=4000.0, bar_count=None):
    """36 M15 bars inside the last `days` completed windows of one session."""
    sess = session_def(key)
    n = int(sess["hours"] * 4) if bar_count is None else int(bar_count)
    span = float(sess["hours"]) * 3600.0
    starts = []
    for k in range(1, 16):
        if len(starts) >= int(days):
            break
        start = window_start(sess, now - k * 86400.0)
        if start is None or start in starts or start + span > now:
            continue
        starts.append(start)
    bars = []
    for i, start in enumerate(sorted(starts)):
        prices = path_prices(n, p0 + i, up, down, drift)
        for j, p in enumerate(prices):
            bars.append((start + j * 900, p, p, p, p))
    bars.sort(key=lambda b: b[0])
    return bars


def fake_info(sym="XAUUSDm"):
    return SimpleNamespace(
        name=sym, visible=True, trade_mode=True, trade_allowed=True,
        digits=2, point=0.01, volume_min=0.01, volume_max=100.0, volume_step=0.01,
        trade_stops_level=0, trade_freeze_level=0,
        trade_tick_value=1.0, trade_tick_size=0.01, trade_contract_size=100.0,
    )


# ------------------------------------------------------------------- the clock
class TestSessionClock(unittest.TestCase):
    def test_summer_utc_windows(self):
        """October (London on BST, New York on EDT)."""
        now = ts(2026, 10, 15, 13, 30)
        rows = S.session_clock(now)
        self.assertEqual([r["key"] for r in rows], ["sydney", "tokyo", "london", "newyork"])
        self.assertEqual((row(rows, "london")["open_utc"], row(rows, "london")["close_utc"]), ("07:00", "16:00"))
        self.assertEqual((row(rows, "newyork")["open_utc"], row(rows, "newyork")["close_utc"]), ("12:00", "21:00"))
        self.assertEqual((row(rows, "tokyo")["open_utc"], row(rows, "tokyo")["close_utc"]), ("00:00", "09:00"))
        self.assertEqual((row(rows, "sydney")["open_utc"], row(rows, "sydney")["close_utc"]), ("21:00", "06:00"))
        # local business hours never move
        for key in ("sydney", "tokyo", "london", "newyork"):
            self.assertEqual(row(rows, key)["open_local"], "09:00" if key == "tokyo" else "08:00")
            self.assertEqual(row(rows, key)["close_local"], "18:00" if key == "tokyo" else "17:00")

    def test_winter_utc_windows(self):
        """January (London on GMT, New York on EST) - one hour later in UTC."""
        rows = S.session_clock(ts(2026, 1, 15, 13, 30))
        self.assertEqual(row(rows, "london")["open_utc"], "08:00")
        self.assertEqual(row(rows, "london")["close_utc"], "17:00")
        self.assertEqual(row(rows, "newyork")["open_utc"], "13:00")
        self.assertEqual(row(rows, "newyork")["close_utc"], "22:00")

    def test_open_and_closed_states(self):
        now = ts(2026, 10, 15, 13, 30)          # Thursday, London + New York open
        rows = S.session_clock(now)
        self.assertEqual(row(rows, "london")["state"], "open")
        self.assertEqual(row(rows, "newyork")["state"], "open")
        self.assertEqual(row(rows, "tokyo")["state"], "closed")
        self.assertEqual(row(rows, "sydney")["state"], "closed")

    def test_countdowns_match_the_absolute_epochs(self):
        now = ts(2026, 10, 15, 13, 30)
        for r in S.session_clock(now):
            if r["state"] == "open":
                self.assertAlmostEqual(r["closes_in_sec"], r["close_ts"] - now, delta=1)
                self.assertEqual(r["opens_in_sec"], 0)
                self.assertTrue(r["close_ts"] > r["open_ts"] > 0)
            else:
                self.assertAlmostEqual(r["opens_in_sec"], r["open_ts"] - now, delta=1)
                self.assertGreater(r["open_ts"], now)
                self.assertGreater(r["close_ts"], r["open_ts"])

    def test_progress_bar_only_while_open(self):
        now = ts(2026, 10, 15, 13, 30)
        rows = S.session_clock(now)
        self.assertGreater(row(rows, "london")["progress"], 0.6)   # 07:00-16:00, it is 13:30
        self.assertEqual(row(rows, "tokyo")["progress"], 0.0)

    def test_peak_overlap_is_london_plus_new_york(self):
        rows = S.session_clock(ts(2026, 10, 15, 13, 30))
        self.assertTrue(row(rows, "london")["peak"])
        self.assertTrue(row(rows, "newyork")["peak"])
        self.assertFalse(row(rows, "tokyo")["peak"])
        self.assertIn("newyork", row(rows, "london")["also_open"])
        # 09:30 UTC - London alone
        rows2 = S.session_clock(ts(2026, 10, 15, 9, 30))
        self.assertFalse(row(rows2, "london")["peak"])

    def test_weekend_has_no_open_session_and_points_at_monday(self):
        sat = ts(2026, 10, 17, 12, 0)          # Saturday
        rows = S.session_clock(sat)
        self.assertTrue(all(r["state"] == "closed" for r in rows))
        self.assertTrue(all(r["weekend"] for r in rows))
        monday = row(rows, "london")["open_ts"]
        self.assertEqual(datetime.fromtimestamp(monday, UTC).weekday(), 0)
        self.assertEqual(utc_hhmm(monday), "07:00")

    def test_sunday_evening_sydney_reopens_the_week(self):
        """Sydney's Monday 08:00 local IS the FX week reopen (Sunday 21:00 UTC)."""
        sun = ts(2026, 10, 18, 20, 0)          # Sunday 20:00 UTC
        rows = S.session_clock(sun)
        self.assertEqual(datetime.fromtimestamp(row(rows, "sydney")["open_ts"], UTC).weekday(), 6)
        self.assertEqual(utc_hhmm(row(rows, "sydney")["open_ts"]), "21:00")

    def test_bogus_timezone_falls_back_without_raising(self):
        zone = S._zone("Nowhere/Fake")
        self.assertIsNotNone(zone)
        self.assertEqual(zone.utcoffset(datetime(2026, 1, 1)), timezone.utc.utcoffset(None))


# ------------------------------------------------------- window classification
class TestWindowClassification(unittest.TestCase):
    def test_window_of_matches_the_containing_window(self):
        sess = session_def("newyork")
        now = ts(2026, 10, 15, 23, 0)                     # after the NY close
        inside = ts(2026, 10, 15, 14, 0)
        start = S.window_of(sess, inside)
        self.assertEqual(utc_hhmm(start), "12:00")
        self.assertEqual(datetime.fromtimestamp(start, UTC).day, 15)

    def test_window_of_returns_none_in_the_weekend_gap(self):
        sess = session_def("newyork")
        self.assertIsNone(S.window_of(sess, ts(2026, 10, 17, 14, 0)))   # Saturday

    def test_a_bar_is_clipped_once_per_session(self):
        now = ts(2026, 10, 15, 23, 0)
        bars = window_bars("newyork", now, days=5)
        bucketed = S.collect(bars, now)
        self.assertEqual(len(bucketed["newyork"]), 5)
        self.assertEqual(sum(w["n"] for w in bucketed["newyork"].values()), len(bars))
        # London overlaps 12:00-16:00 UTC, so the same bars must also feed it
        self.assertTrue(bucketed.get("london"))


# ------------------------------------------------------------- the AI reading
class TestSessionStats(unittest.TestCase):
    def setUp(self):
        self.now = ts(2026, 10, 15, 23, 0)

    def _stats(self, key, **kw):
        bars = window_bars(key, self.now, **kw)
        return S.session_stats(S.collect(bars, self.now))

    def test_average_up_down_range_and_drift(self):
        st = self._stats("newyork", days=6, up=10.0, down=3.0, drift=7.0)["newyork"]
        self.assertEqual(st["days"], 6)
        self.assertAlmostEqual(st["up"], 10.0, places=2)
        self.assertAlmostEqual(st["down"], 3.0, places=2)
        self.assertAlmostEqual(st["range"], 13.0, places=2)
        self.assertAlmostEqual(st["drift"], 7.0, places=2)

    def test_rising_session_is_a_buy_and_falling_is_a_sell(self):
        up = self._stats("newyork", days=6, drift=7.0)["newyork"]
        down = self._stats("newyork", days=6, up=3.0, down=10.0, drift=-7.0)["newyork"]
        self.assertEqual(up["verdict"], "BUY")
        self.assertEqual(down["verdict"], "SELL")
        self.assertGreater(up["confidence"], 0)
        self.assertEqual(up["up_days"], 6)
        self.assertEqual(down["up_days"], 0)
        self.assertEqual(down["down_days"], 6)

    def test_a_flat_session_stays_mixed(self):
        st = self._stats("newyork", days=6, up=6.5, down=6.5, drift=0.0)["newyork"]
        self.assertEqual(st["verdict"], "MIXED")
        self.assertEqual(st["confidence"], 0)

    def test_best_up_and_best_down_are_kept(self):
        st = self._stats("newyork", days=6, up=10.0, down=3.0, drift=7.0)["newyork"]
        self.assertAlmostEqual(st["best_up"], 10.0, places=2)
        self.assertAlmostEqual(st["best_down"], 3.0, places=2)

    def test_too_little_history_has_no_verdict(self):
        st = self._stats("newyork", days=3, drift=7.0)["newyork"]
        self.assertIsNone(st)

    def test_holiday_windows_with_too_few_bars_are_ignored(self):
        """A 10-bar (2.5h) window is not a session and must not skew the average."""
        st = self._stats("newyork", days=6, bar_count=10)["newyork"]
        self.assertIsNone(st)

    def test_the_open_window_is_not_counted(self):
        """A window that has not finished yet must never enter the average."""
        start = S.window_of(session_def("newyork"), ts(2026, 10, 15, 14, 0))
        bars = [(start + i * 900, 4000.0, 4000.0, 4000.0, 4000.0) for i in range(36)]
        buckets = S.collect(bars, start + 3600.0)       # only 1h into the window
        self.assertIsNone(S.session_stats(buckets)["newyork"])


# ------------------------------------------------------------------- the board
class TestBoardPayload(unittest.TestCase):
    def setUp(self):
        self.now = ts(2026, 10, 15, 23, 0)
        self.samples = {"newyork": 12.5, "tokyo": 3.0}

    def _payload(self):
        bars = window_bars("newyork", self.now, days=6, up=10.0, down=3.0, drift=7.0)
        stats = S.session_stats(S.collect(bars, self.now))
        return S.build_payload(self.now, symbol="XAUUSDm", digits=2, money_per_price=1.0,
                               lot=0.01, stats=stats, markets=["XAUUSDm", "BTCUSDTm"],
                               range_days=6, source="mt5")

    def test_payload_shape(self):
        p = self._payload()
        self.assertEqual(p["symbol"], "XAUUSDm")
        self.assertEqual(p["symbols"], ["XAUUSDm", "BTCUSDTm"])
        self.assertEqual(p["source"], "mt5")
        self.assertEqual(p["now_utc"], int(self.now))
        self.assertEqual(p["range_days"], 6)
        self.assertEqual([r["key"] for r in p["sessions"]], ["sydney", "tokyo", "london", "newyork"])

    def test_money_conversion_for_the_lot(self):
        p = self._payload()
        st = row(p["sessions"], "newyork")["stats"]
        # 0.01 lot of gold = $1 per $1 move, so money == price travel here
        self.assertAlmostEqual(st["up_money"], 10.0, places=2)
        self.assertAlmostEqual(st["down_money"], 3.0, places=2)
        self.assertAlmostEqual(st["range_money"], 13.0, places=2)
        self.assertEqual(st["up_txt"], "10.00")
        self.assertEqual(st["lot"], 0.01)

    def test_sessions_without_history_carry_no_stats(self):
        p = self._payload()
        self.assertIsNone(row(p["sessions"], "tokyo")["stats"])
        self.assertIsNotNone(row(p["sessions"], "newyork")["stats"])

    def test_summary_is_the_biggest_travelling_session(self):
        p = self._payload()
        self.assertEqual(p["summary"]["key"], "newyork")
        self.assertEqual(p["summary"]["verdict"], "BUY")
        self.assertAlmostEqual(p["summary"]["up_money"], 10.0, places=2)

    def test_clock_only_payload_without_a_terminal(self):
        p = S.build_payload(self.now, symbol="XAUUSDm", source="clock")
        self.assertEqual(p["source"], "clock")
        self.assertIsNone(p["summary"])
        self.assertTrue(all(r["stats"] is None for r in p["sessions"]))
        self.assertEqual(len(p["sessions"]), 4)


# --------------------------------------------------------------- NewsBot wiring
class TestNewsBotSessions(unittest.TestCase):
    def setUp(self):
        self.bot = NewsBot()
        self.now = ts(2026, 10, 15, 23, 0)

    def _mt5(self, m, bars):
        m.TIMEFRAME_M15 = 15
        m.symbol_info.return_value = fake_info()
        m.copy_rates_from_pos.return_value = bars
        m.symbols_get.return_value = []

        def info(sym):
            return fake_info(sym)

        m.symbol_info.side_effect = info

    def test_clock_only_board_without_a_terminal(self):
        with patch("backend.news_bot.mt5", None):
            board = self.bot.session_board(now=self.now)
        self.assertEqual(board["source"], "clock")
        self.assertEqual(len(board["sessions"]), 4)
        self.assertTrue(all(r["stats"] is None for r in board["sessions"]))
        # 23:00 UTC: Sydney is running, Tokyo opens in a few hours
        sydney = row(board["sessions"], "sydney")
        tokyo = row(board["sessions"], "tokyo")
        self.assertEqual(sydney["state"], "open")
        self.assertEqual(tokyo["state"], "closed")
        self.assertGreater(tokyo["open_ts"], int(self.now))

    def test_default_market_follows_the_bot_symbol(self):
        self.assertEqual(self.bot.session_market(), "XAUUSDm")
        self.bot.update_config({"session_market": "BTCUSDTm"})
        self.assertEqual(self.bot.session_market(), "BTCUSDTm")

    def test_measured_board_from_m15_bars(self):
        bars = window_bars("newyork", self.now, days=6, up=12.0, down=4.0, drift=8.0)
        m = MagicMock()
        self._mt5(m, bars)
        with patch("backend.news_bot.mt5", m):
            board = self.bot.session_board(now=self.now, force=True)
        self.assertEqual(board["source"], "mt5")
        st = row(board["sessions"], "newyork")["stats"]
        self.assertIsNotNone(st)
        self.assertEqual(st["verdict"], "BUY")
        self.assertAlmostEqual(st["up_move"], 12.0, places=2)
        self.assertAlmostEqual(st["down_move"], 4.0, places=2)
        self.assertAlmostEqual(st["up_money"], 12.0, places=2)   # 0.01 lot gold
        self.assertEqual(st["days"], 6)
        self.assertEqual(board["summary"]["key"], "newyork")

    def test_stats_are_cached_between_ticks(self):
        bars = window_bars("newyork", self.now, days=6)
        m = MagicMock()
        self._mt5(m, bars)
        with patch("backend.news_bot.mt5", m):
            self.bot.session_board(now=self.now, force=True)
            first = m.copy_rates_from_pos.call_count
            self.bot.session_board(now=self.now + 5.0)
            self.bot.session_board(now=self.now + 9.0)
            self.assertEqual(m.copy_rates_from_pos.call_count, first)
            self.bot.session_board(now=self.now + 700.0)          # cache expires
            self.assertGreater(m.copy_rates_from_pos.call_count, first)

    def test_status_carries_the_sessions_board(self):
        with patch("backend.news_bot.mt5", None):
            st = self.bot.status()
        board = st["sessions"]
        self.assertEqual(board["symbol"], "XAUUSDm")
        self.assertEqual(len(board["sessions"]), 4)
        for r in board["sessions"]:
            for field in ("key", "state", "open_ts", "close_ts", "opens_in_sec",
                          "closes_in_sec", "progress", "open_utc", "close_utc",
                          "open_local", "close_local", "weekend", "peak", "flow"):
                self.assertIn(field, r)

    def test_dashboard_can_switch_the_sessions_market(self):
        """The picker posts to /api/bot5/config, so the keys must survive both
        allow-lists - a filtered key would silently do nothing (found live)."""
        root = Path(__file__).resolve().parent.parent
        app_src = (root / "backend" / "app.py").read_text(encoding="utf-8")
        api_block = app_src.split("BOT5_KEYS = (", 1)[1].split("\n\n", 1)[0]
        eng_src = (root / "backend" / "engine.py").read_text(encoding="utf-8")
        # the bot-5 patch allow-list is the block right after its own keys
        eng_block = eng_src.split('"bias_min_score", "refresh_min",', 1)[1][:220]
        for key in ("session_market", "session_range_days"):
            self.assertIn(key, api_block, f"{key} missing from the API allow-list")
            self.assertIn(key, eng_block, f"{key} missing from the engine patch allow-list")
        # and the bot itself must accept them
        self.bot.update_config({"session_market": "BTCUSDTm", "session_range_days": 10.0})
        self.assertEqual(self.bot.session_market(), "BTCUSDTm")
        self.assertEqual(self.bot.cfg["session_range_days"], 10.0)

    def test_market_list_is_what_the_account_can_trade(self):
        with patch("backend.news_bot.mt5", None):
            board = self.bot.session_board(now=self.now)
        self.assertTrue(board["symbols"])
        for sym in board["symbols"]:
            self.assertIn(sym, PLATFORM_MARKETS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
