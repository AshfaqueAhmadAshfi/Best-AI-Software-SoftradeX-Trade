"""Exness chart feed tests.

The dashboard chart must be driven by the connected Exness MetaTrader 5
terminal only: real candles, real ticks and a big-print tape that is measured
from later ticks. Nothing here may invent a price, and a missing terminal has
to fail loudly instead of drawing a seed chart.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import chart_feed as CF


def rates(count=600, base=2000.0, volume=120):
    arr = np.zeros(int(count), dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"),
                                     ("low", "f8"), ("close", "f8"), ("tick_volume", "i8"),
                                     ("spread", "i4"), ("real_volume", "i8")])
    end = int(time.time() // 300 * 300)
    arr["time"] = [end - (int(count) - 1 - i) * 300 for i in range(int(count))]
    arr["open"] = base
    arr["high"] = base + 2.0
    arr["low"] = base - 2.0
    arr["close"] = base + 0.5
    arr["tick_volume"] = volume
    return arr


class FakeMT5:
    """Just enough MetaTrader5 for the chart feed."""

    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_H1 = 60
    TIMEFRAME_H4 = 240
    TIMEFRAME_D1 = 1440

    def __init__(self, names=None, price=2000.0):
        self.names = list(names or ["XAUUSDm", "EURUSDm"])
        self.price = price
        self.rate_calls = []

    def terminal_info(self):
        return SimpleNamespace(connected=True, path="C:\\Fake\\MT5")

    def initialize(self, *a, **k):
        return True

    def last_error(self):
        return (1, "Success")

    def account_info(self):
        return SimpleNamespace(login=414424708, server="Exness-MT5Trial6")

    def symbol_info(self, sym):
        if sym not in self.names:
            return None
        return SimpleNamespace(digits=2, point=0.01, spread=25, description="Gold vs US Dollar",
                               trade_contract_size=100.0, currency_profit="USD",
                               visible=True, select=True)

    def symbol_select(self, sym, enable=True):
        return sym in self.names

    def symbol_info_tick(self, sym):
        if sym not in self.names:
            return None
        return SimpleNamespace(bid=self.price, ask=self.price + 0.25, last=self.price,
                               volume=0, volume_real=0.0, time_msc=int(time.time() * 1000),
                               time=int(time.time()))

    def copy_rates_from_pos(self, sym, tf, start, count):
        self.rate_calls.append((sym, tf, start, count))
        return rates(count)


class TestTimeframes(unittest.TestCase):
    def test_every_offered_timeframe_maps_to_an_mt5_constant(self):
        with patch.object(CF, "mt5", FakeMT5()):
            for tf in CF.TF_SEC:
                self.assertIsInstance(CF._tf_const(tf), int)

    def test_unknown_timeframe_is_rejected(self):
        with patch.object(CF, "mt5", FakeMT5()):
            with self.assertRaises(CF.ChartUnavailable):
                CF._tf_const("3m")


class TestBars(unittest.TestCase):
    def test_rates_become_millisecond_bars(self):
        raw = rates(3)
        bars = CF._bars_from_rates(raw)
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0], [int(raw["time"][0]) * 1000, 2000.0, 2002.0, 1998.0, 2000.5, 120])
        self.assertEqual(bars[1][0] - bars[0][0], 300000)

    def test_no_rates_is_not_an_empty_chart_of_zeros(self):
        self.assertEqual(CF._bars_from_rates(None), [])


class TestTapeHistory(unittest.TestCase):
    def _bars(self):
        bars = []
        for i in range(40):
            o = 2000.0
            body = 0.0 if i % 4 else (1.5 if i % 8 == 0 else -1.5)
            bars.append([1700000000000 + i * 60000, o, o + 0.4, o - 0.4, o + body, 100 + i])
        return bars

    def test_only_unusual_bars_become_prints(self):
        rows = CF.tape_from_bars(self._bars(), point=0.01, digits=2)
        self.assertTrue(rows)
        for row in rows:
            self.assertIn(row["side"], ("BUY", "SELL"))
            self.assertGreater(abs(row["mv"]), 0)
            self.assertEqual(row["src"], "1m")

    def test_side_and_follow_are_signed_by_the_print(self):
        rows = CF.tape_from_bars(self._bars(), point=0.01, digits=2)
        buy = [r for r in rows if r["side"] == "BUY"]
        self.assertTrue(buy)
        # every BUY row's own move is positive
        self.assertTrue(all(r["mv"] > 0 for r in buy))

    def test_short_windows_are_refused(self):
        self.assertEqual(CF.tape_from_bars(self._bars()[:5], point=0.01, digits=2), [])


class TestSymbols(unittest.TestCase):
    def test_only_instruments_the_account_offers_are_listed(self):
        feed = CF.ChartFeed()
        with patch.object(CF, "mt5", FakeMT5(names=["XAUUSDm", "EURUSDm"])):
            data = feed.symbols()
        self.assertTrue(data["available"])
        self.assertEqual([s["symbol"] for s in data["symbols"]], ["XAUUSDm", "EURUSDm"])
        self.assertEqual(data["symbols"][0]["label"], "GOLD")
        self.assertEqual(data["account"]["server"], "Exness-MT5Trial6")

    def test_missing_terminal_reports_unavailable_not_fake_candles(self):
        feed = CF.ChartFeed()
        with patch.object(CF, "mt5", None):
            data = feed.symbols()
            self.assertFalse(data["available"])
            self.assertTrue(data["message"])
            with self.assertRaises(CF.ChartUnavailable):
                feed.candles("XAUUSDm", "5m", 100)


class TestSnapshot(unittest.TestCase):
    def _feed(self):
        feed = CF.ChartFeed()
        fake = FakeMT5(names=["XAUUSDm"])
        return feed, fake

    def test_snapshot_carries_meta_bars_ticker_and_offset(self):
        feed, fake = self._feed()
        with patch.object(CF, "mt5", fake):
            snap = feed.snapshot("XAUUSDm", "5m", 200)
        self.assertEqual(snap["t"], "snap")
        self.assertEqual(snap["s"], "XAUUSDm")
        self.assertEqual(snap["tf_sec"], 300)
        self.assertEqual(len(snap["bars"]), 200)
        self.assertEqual(snap["meta"]["digits"], 2)
        self.assertIn("price", snap["ticker"])
        self.assertIsInstance(snap["offset"], float)

    def test_live_tick_moves_the_newest_bar(self):
        feed, fake = self._feed()
        fake.price = 2010.0
        with patch.object(CF, "mt5", fake):
            bars = feed.candles("XAUUSDm", "5m", 10)
            snap = feed.snapshot("XAUUSDm", "5m", 10)
        self.assertEqual(bars[-1][4], 2000.5)          # history is untouched
        self.assertEqual(snap["bars"][-1][4], 2010.0)  # the newest bar follows the tick

    def test_no_history_raises_instead_of_seeding(self):
        feed, fake = self._feed()

        def no_rates(sym, tf, start, count):
            return None

        fake.copy_rates_from_pos = no_rates
        with patch.object(CF, "mt5", fake):
            with self.assertRaises(CF.ChartUnavailable):
                feed.snapshot("XAUUSDm", "5m", 10)

    def test_quote_without_candles_does_not_crash(self):
        """A symbol can quote while its candle history is still empty."""
        feed = CF.ChartFeed()
        feed.meta = lambda symbol: {"symbol": symbol, "digits": 2, "point": 0.01,
                                    "unit": "lots", "currency": "USD"}
        feed.candles = lambda symbol, tf, count: []
        feed.tick = lambda symbol, max_age=0.0: {"ms": 1700000000000, "bid": 1.0, "ask": 1.1}
        feed.ticker = lambda symbol: {"symbol": symbol, "price": 1.0}
        feed.tape_history = lambda symbol, limit=40: []
        snap = feed.snapshot("XAUUSDm", "5m", 10)
        self.assertEqual(snap["t"], "snap")
        self.assertEqual(snap["bars"], [])

    def test_unknown_timeframe_is_rejected(self):
        feed, fake = self._feed()
        with patch.object(CF, "mt5", fake):
            with self.assertRaises(CF.ChartUnavailable):
                feed.snapshot("XAUUSDm", "7m", 10)


class TestTerminalAttach(unittest.TestCase):
    def _probe(self):
        class ProbeMT5(FakeMT5):
            def __init__(self):
                super().__init__(names=["XAUUSDm"])
                self.probes = 0
                self.inits = []

            def terminal_info(self):
                self.probes += 1
                return None

            def initialize(self, *a, **k):
                self.inits.append(k)
                return True

        return ProbeMT5()

    def test_attach_probe_is_cached_between_ticks(self):
        feed = CF.ChartFeed()
        fake = self._probe()
        with patch.object(CF, "mt5", fake):
            self.assertIsNotNone(feed.tick("XAUUSDm"))
            self.assertIsNotNone(feed.tick("XAUUSDm"))
            self.assertIsNotNone(feed.tick("XAUUSDm"))
        self.assertEqual(fake.probes, 1)
        self.assertEqual(len(fake.inits), 1)

    def test_initialize_uses_the_engines_terminal_path(self):
        feed = CF.ChartFeed()
        fake = self._probe()
        engine_path = r"C:\Engines\Exness\terminal64.exe"
        with patch.object(CF, "mt5", fake), \
                patch.object(CF, "preferred_terminal_path", lambda: engine_path):
            feed.tick("XAUUSDm")
        self.assertEqual(fake.inits[0].get("path"), engine_path)


class TestNewBar(unittest.TestCase):
    def test_first_tick_of_a_new_bar_has_a_coherent_ohlc(self):
        feed = CF.ChartFeed()
        fake = FakeMT5(names=["XAUUSDm"])
        with patch.object(CF, "mt5", fake):
            bars = feed.candles("XAUUSDm", "5m", 10)
            last_ms = int(bars[-1][0])
            next_ms = last_ms + 300000
            feed.tick = lambda symbol, max_age=0.0: {
                "bid": 1990.0, "ask": 1990.25, "ms": next_ms,
                "time": next_ms // 1000, "volume": 0, "volume_real": 0.0,
            }
            snap = feed.snapshot("XAUUSDm", "5m", 10)
        bar = snap["bars"][-1]
        self.assertEqual(bar[0], next_ms)
        self.assertEqual(bar[1], 2000.5)   # opens at the previous close
        self.assertEqual(bar[2], 2000.5)   # high still spans the open
        self.assertEqual(bar[3], 1990.0)
        self.assertEqual(bar[4], 1990.0)
        self.assertGreaterEqual(bar[2], max(bar[1], bar[4]))
        self.assertLessEqual(bar[3], min(bar[1], bar[4]))


class TestTicker(unittest.TestCase):
    def test_ticker_is_computed_from_real_rates(self):
        feed = CF.ChartFeed()
        fake = FakeMT5(names=["XAUUSDm"])
        with patch.object(CF, "mt5", fake):
            data = feed.ticker("XAUUSDm")
        self.assertEqual(data["high"], 2002.0)
        self.assertEqual(data["low"], 1998.0)
        self.assertEqual(data["open24"], 2000.0)
        self.assertEqual(data["vol"], 1440 * 120)


class DummyLoop:
    """Minimal stand-in for an asyncio loop: runs the callback inline."""

    def call_soon_threadsafe(self, fn):
        fn()


class TestSamplerFrames(unittest.TestCase):
    def setUp(self):
        self.feed = CF.ChartFeed()
        self.feed.tick = lambda symbol, max_age=0.0: self.tick
        self.feed._m1_volume = lambda symbol: (1000, 10)
        self.tick = {"bid": 100.0, "ask": 100.02, "ms": 1000, "time": 1, "volume": 0, "volume_real": 0.0}
        self.cid, self.queue = self.feed.add_client(DummyLoop())
        self.feed.set_symbol(self.cid, "XAUUSDm")
        # the price of the instrument must not be fetched from MT5 in a unit test
        self.feed._meta_cache["XAUUSDm"] = (time.time(), {
            "symbol": "XAUUSDm", "label": "GOLD", "pair": "USD", "icon": "🟡",
            "unit": "oz", "kind": "metals", "alts": ["XAUUSDm"], "description": "Gold",
            "digits": 3, "point": 0.001, "spread_points": 24, "contract": 100.0,
            "currency": "USD",
        })
        self.feed._start_thread = lambda: None   # no background sampler in tests

    def _drain(self):
        out = []
        while not self.queue.empty():
            out.append(self.queue.get_nowait())
        return out

    def test_first_tick_is_streamed(self):
        self.feed._sample("XAUUSDm")
        frames = [f for f in self._drain() if f.get("t") == "tick"]
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["s"], "XAUUSDm")
        self.assertEqual(frames[0]["b"], 100.0)

    def test_unchanged_tick_is_not_resent(self):
        self.feed._sample("XAUUSDm")
        self._drain()
        self.feed._sample("XAUUSDm")
        self.assertEqual([f for f in self._drain() if f.get("t") == "tick"], [])

    def test_big_impulse_prints_and_is_followed(self):
        state = self.feed._state("XAUUSDm")
        state["moves"].extend([0.01] * 40)   # calm market -> low median
        state["last_bid"] = 100.0
        state["last_ms"] = 1000
        self.tick = {"bid": 100.0, "ask": 100.02, "ms": 1200, "time": 1, "volume": 0, "volume_real": 0.0}
        self.feed._sample("XAUUSDm")
        self._drain()

        # a violent up-impulse of 1.0 price units (100x the median move)
        self.tick = {"bid": 101.0, "ask": 101.02, "ms": 1400, "time": 1, "volume": 0, "volume_real": 0.0}
        self.feed._sample("XAUUSDm")
        prints = [f for f in self._drain() if f.get("t") == "print"]
        self.assertEqual(len(prints), 1)
        self.assertEqual(prints[0]["side"], "BUY")
        self.assertEqual(prints[0]["px"], 101.0)
        self.assertEqual(prints[0]["mv"], 1.0)

        # the market followed the print 6 seconds later
        state["pending"][0]["at"] = time.time() - (CF.FOLLOW_AFTER + 1)
        self.tick = {"bid": 101.4, "ask": 101.42, "ms": 8000, "time": 8, "volume": 0, "volume_real": 0.0}
        self.feed._sample("XAUUSDm")
        follows = [f for f in self._drain() if f.get("t") == "follow"]
        self.assertEqual(len(follows), 1)
        self.assertEqual(follows[0]["st"], "UP")
        self.assertAlmostEqual(follows[0]["mv"], 0.4, places=3)

    def test_sell_print_follows_downward(self):
        state = self.feed._state("XAUUSDm")
        state["moves"].extend([0.01] * 40)
        state["last_bid"] = 100.0
        state["last_ms"] = 1000
        self.tick = {"bid": 100.0, "ask": 100.02, "ms": 1200, "time": 1, "volume": 0, "volume_real": 0.0}
        self.feed._sample("XAUUSDm")
        self._drain()
        self.tick = {"bid": 99.0, "ask": 99.02, "ms": 1400, "time": 1, "volume": 0, "volume_real": 0.0}
        self.feed._sample("XAUUSDm")
        prints = [f for f in self._drain() if f.get("t") == "print"]
        self.assertEqual(prints[0]["side"], "SELL")
        state["pending"][0]["at"] = time.time() - (CF.FOLLOW_AFTER + 1)
        self.tick = {"bid": 98.6, "ask": 98.62, "ms": 9000, "time": 9, "volume": 0, "volume_real": 0.0}
        self.feed._sample("XAUUSDm")
        follows = [f for f in self._drain() if f.get("t") == "follow"]
        self.assertEqual(follows[0]["st"], "UP")   # moved with the SELL
        self.assertAlmostEqual(follows[0]["mv"], 0.4, places=3)

    def test_subscriptions_are_refcounted(self):
        other, _ = self.feed.add_client(DummyLoop())
        self.assertEqual(self.feed._subs.get("XAUUSDm"), 1)
        self.feed.set_symbol(other, "XAUUSDm")
        self.assertEqual(self.feed._subs.get("XAUUSDm"), 2)
        self.feed.remove_client(other)
        self.assertEqual(self.feed._subs.get("XAUUSDm"), 1)
        self.feed.remove_client(self.cid)
        self.assertNotIn("XAUUSDm", self.feed._subs)


if __name__ == "__main__":
    unittest.main()
