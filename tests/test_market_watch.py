"""Market Watch dial set tests.

The dashboard pipeline follows one market, so the watch engine must always
compute a row for that market - even when it is none of the ten serial dials -
without changing the ten rows themselves or their order.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import market_watch as MW


class FakeMT5:
    """Just enough MetaTrader5 for the watch engine."""

    TIMEFRAME_M1 = 1

    def __init__(self, names=None):
        self.names = list(names or ["XAUUSDm", "EURUSDm", "USDXOFm", "ORCLm"])

    def terminal_info(self):
        return SimpleNamespace(connected=True, path="C:\\Fake\\MT5")

    def symbols_get(self):
        return [SimpleNamespace(name=n) for n in self.names]

    def symbol_select(self, sym, enable=True):
        return sym in self.names

    def symbol_info(self, sym):
        if sym not in self.names:
            return None
        return SimpleNamespace(digits=2, point=0.01, spread=25, volume_min=0.01,
                               volume_max=100.0, volume_step=0.01)

    def symbol_info_tick(self, sym):
        if sym not in self.names:
            return None
        return SimpleNamespace(time=int(time.time()), bid=2000.0, ask=2000.25, last=2000.0)

    def copy_rates_from_pos(self, sym, tf, start, count):
        arr = np.zeros(max(1, int(count)), dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"),
                                                  ("low", "f8"), ("close", "f8"), ("tick_volume", "i8"),
                                                  ("spread", "i4"), ("real_volume", "i8")])
        arr["open"] = 2000.0
        arr["close"] = 2000.4
        arr["high"] = 2000.6
        arr["low"] = 1999.8
        arr["tick_volume"] = 120
        return arr


class TestWatchSet(unittest.TestCase):
    def setUp(self):
        self._mt5 = MW.mt5
        MW.mt5 = FakeMT5()
        MW._cached_watches = {}
        MW._cache_lock_ts = 0.0
        MW._digits_cache = {}

    def tearDown(self):
        MW.mt5 = self._mt5
        MW._cached_watches = {}

    def _symbols(self, extra=None):
        res = MW.get_all_market_watches(force=True, extra=extra)
        return [w["symbol"] for w in res["watches"]]

    def test_the_ten_serial_dials_come_first(self):
        rows = self._symbols()
        self.assertEqual(rows[:10], MW.PREFERRED_WATCH_SYMBOLS)

    def test_the_flow_market_always_gets_a_dial(self):
        """A market outside the default set is appended, never dropped."""
        base = self._symbols()
        rows = self._symbols(extra=["ORCLm"])
        self.assertEqual(rows[:10], base[:10])
        self.assertIn("ORCLm", rows)
        row = MW.get_all_market_watches(extra=["ORCLm"])["watches"][-1]
        self.assertEqual(row["symbol"], "ORCLm")
        self.assertIn("buyer_pct", row)
        self.assertTrue(row["live"])

    def test_an_extra_that_is_already_a_dial_is_not_duplicated(self):
        rows = self._symbols(extra=["XAUUSDm", "EURUSDm"])
        self.assertEqual(rows.count("XAUUSDm"), 1)
        self.assertEqual(rows.count("EURUSDm"), 1)
        self.assertEqual(len(rows), 10)

    def test_two_extras_keep_their_order(self):
        rows = self._symbols(extra=["USDXOFm", "ORCLm"])
        self.assertEqual(rows[10:12], ["USDXOFm", "ORCLm"])

    def test_the_cache_does_not_mix_two_different_selections(self):
        first = self._symbols(extra=["ORCLm"])
        second = [w["symbol"] for w in MW.get_all_market_watches(extra=["USDXOFm"])["watches"]]
        self.assertIn("ORCLm", first)
        self.assertNotIn("ORCLm", second)
        self.assertIn("USDXOFm", second)

    def test_without_a_terminal_the_dials_still_answer(self):
        MW.mt5 = None
        rows = MW.get_all_market_watches(force=True, extra=["ORCLm"])["watches"]
        self.assertEqual([w["symbol"] for w in rows][:10], MW.PREFERRED_WATCH_SYMBOLS)
        self.assertTrue(all(w["live"] is False for w in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
