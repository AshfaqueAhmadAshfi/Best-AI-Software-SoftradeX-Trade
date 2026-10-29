"""Public (no-MT5) chart feed tests.

The chart must keep working when no MetaTrader 5 terminal is logged in. The
fallback reads real Binance / Yahoo data, so these tests fake only the HTTP
payloads - never the prices the parser is supposed to understand.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import chart_feed as CF
from backend import public_feed as PF


def bar(ts_ms, o, h, l, c, vol=10):
    return [int(ts_ms), float(o), float(h), float(l), float(c), int(vol)]


class TestSymbolMapping(unittest.TestCase):
    def test_gold_uses_the_paxg_spot_proxy_first(self):
        self.assertEqual(PF._binance_symbol("XAUUSDm"), "PAXGUSDT")
        self.assertEqual(PF._yahoo_symbol("XAUUSDm"), "GC=F")

    def test_crypto_maps_to_binance_and_a_yahoo_fallback(self):
        self.assertEqual(PF._binance_symbol("BTCUSDTm"), "BTCUSDT")
        self.assertEqual(PF._yahoo_symbol("BTCUSDTm"), "BTC-USD")
        self.assertEqual(PF._binance_symbol("ETHUSDm"), "ETHUSDT")

    def test_forex_indices_energy_and_stocks_have_yahoo_symbols(self):
        self.assertEqual(PF._yahoo_symbol("EURUSDm"), "EURUSD=X")
        self.assertEqual(PF._yahoo_symbol("USTECm"), "^NDX")
        self.assertEqual(PF._yahoo_symbol("USOILm"), "CL=F")
        self.assertEqual(PF._yahoo_symbol("AAPLm"), "AAPL")
        self.assertEqual(PF._yahoo_symbol("NEFLm"), "NFLX")

    def test_gold_crosses_are_not_advertised_without_a_real_source(self):
        self.assertFalse(PF.supports("XAUEURm"))
        self.assertTrue(PF.supports("XAUUSDm"))


class TestBinanceBars(unittest.TestCase):
    def setUp(self):
        PF.reset_cache()

    def test_klines_become_millisecond_bars_with_trade_counts(self):
        page = [
            [1700000000000, "100.0", "101.0", "99.5", "100.5", "12.0",
             1700000299999, "0", 42, "0", "0", "0"],
            [1700000300000, "100.5", "102.0", "100.0", "101.5", "8.0",
             1700000599999, "0", 17, "0", "0", "0"],
        ]
        with patch.object(PF, "_http_json", return_value=page):
            bars = PF.candles("BTCUSDTm", "5m", 2)
        self.assertEqual(bars, [
            [1700000000000, 100.0, 101.0, 99.5, 100.5, 42],
            [1700000300000, 100.5, 102.0, 100.0, 101.5, 17],
        ])

    def test_a_bad_kline_row_is_skipped(self):
        page = [
            [1700000000000, "0", "0", "0", "0", "0", 0, "0", 0, "0", "0", "0"],
            [1700000300000, "100.5", "102.0", "100.0", "101.5", "8.0",
             1700000599999, "0", 17, "0", "0", "0"],
        ]
        with patch.object(PF, "_http_json", return_value=page):
            bars = PF.candles("BTCUSDTm", "5m", 5)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0][4], 101.5)


class TestYahooBars(unittest.TestCase):
    def setUp(self):
        PF.reset_cache()

    def _payload(self):
        return {"chart": {"result": [{
            "meta": {"regularMarketPrice": 1.2},
            "timestamp": [1700000000, 1700000300, 1700000600],
            "indicators": {"quote": [{
                "open": [1.1, None, 1.2],
                "high": [1.15, None, 1.25],
                "low": [1.05, None, 1.15],
                "close": [1.12, None, 1.22],
                "volume": [0, None, 0],
            }]},
        }]}}

    def test_null_candles_are_dropped_and_prices_are_real(self):
        with patch.object(PF, "_http_json", return_value=self._payload()):
            bars = PF.candles("EURUSDm", "5m", 10)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0], [1700000000000, 1.1, 1.15, 1.05, 1.12, 0])
        self.assertEqual(bars[1][4], 1.22)

    def test_4h_candles_are_aggregated_from_hourly_bars(self):
        rows = [
            bar(1700000000000, 10, 12, 9, 11, 5),
            bar(1700001800000, 11, 13, 10, 12, 6),
            bar(1700003600000, 12, 12.5, 8, 9, 7),
            bar(1700007200000, 9, 10, 8.5, 9.5, 8),
        ]
        out = PF._aggregate(rows, 14400)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0][:2], [1699992000000, 10.0])
        self.assertEqual(out[0][2], 13.0)
        self.assertEqual(out[0][3], 8.0)
        self.assertEqual(out[0][4], 9.0)
        self.assertEqual(out[0][5], 18)
        self.assertEqual(out[1][4], 9.5)


class TestPublicQuotes(unittest.TestCase):
    def setUp(self):
        PF.reset_cache()

    def test_binance_24h_stats_are_used_for_the_ticker(self):
        payload = {"lastPrice": "101.5", "openPrice": "100.0",
                   "highPrice": "103.0", "lowPrice": "99.0",
                   "volume": "500", "count": 321}
        with patch.object(PF, "_http_json", return_value=payload):
            row = PF.ticker("BTCUSDTm")
        self.assertEqual(row["price"], 101.5)
        self.assertEqual(row["open24"], 100.0)
        self.assertEqual(row["vol"], 321)
        self.assertAlmostEqual(row["change"], 1.5)

    def test_yahoo_ticker_reads_the_chart_meta(self):
        payload = {"chart": {"result": [{
            "meta": {"regularMarketPrice": 1.2, "previousClose": 1.0,
                     "regularMarketDayHigh": 1.25, "regularMarketDayLow": 0.95,
                     "regularMarketVolume": 0},
            "timestamp": [], "indicators": {"quote": [{}]},
        }]}}
        with patch.object(PF, "_http_json", return_value=payload):
            row = PF.ticker("EURUSDm")
        self.assertEqual(row["price"], 1.2)
        self.assertEqual(row["high"], 1.25)
        self.assertAlmostEqual(row["change"], 20.0)

    def test_a_network_error_keeps_the_last_good_series(self):
        page = [[1700000000000, "100.0", "101.0", "99.5", "100.5", "12.0",
                 1700000299999, "0", 42, "0", "0", "0"]]
        with patch.object(PF, "_http_json", return_value=page):
            first = PF.candles("BTCUSDTm", "5m", 1)
        # expire the cache, then break every provider
        key = ("BTCUSDTm", "5m", 1)
        PF._CANDLES[key] = (time.time() - 1, first)
        with patch.object(PF, "_http_json", side_effect=OSError("offline")):
            second = PF.candles("BTCUSDTm", "5m", 1)
        self.assertEqual(second, first)

    def test_no_provider_raises_instead_of_inventing_a_chart(self):
        with patch.object(PF, "_http_json", side_effect=OSError("offline")):
            with self.assertRaises(PF.PublicFeedError):
                PF.candles("EURUSDm", "5m", 10)


class TestChartFeedFallback(unittest.TestCase):
    def setUp(self):
        PF.reset_cache()

    def test_catalogue_is_offered_when_mt5_is_missing(self):
        feed = CF.ChartFeed(public_fallback=True)
        with patch.object(CF, "mt5", None):
            data = feed.symbols()
        self.assertTrue(data["available"])
        self.assertEqual(data["source"], "public")
        self.assertFalse(data["connected"])
        self.assertIn("XAUUSDm", [s["symbol"] for s in data["symbols"]])
        self.assertNotIn("XAUEURm", [s["symbol"] for s in data["symbols"]])

    def test_snapshot_uses_public_candles_price_and_stats(self):
        feed = CF.ChartFeed(public_fallback=True)
        bars = [bar(int(time.time() // 300 * 300 * 1000), 4000, 4005, 3995, 4001, 50)]
        tick = {"bid": 4002.0, "ask": 4002.0, "ms": int(time.time() * 1000),
                "time": int(time.time()), "volume": 0, "volume_real": 0.0}
        stats = {"price": 4002.0, "open24": 3990.0, "high": 4010.0,
                 "low": 3980.0, "vol": 1234, "change": 0.3}
        with patch.object(CF, "mt5", None), \
                patch.object(CF.public_feed, "candles", return_value=bars), \
                patch.object(CF.public_feed, "tick", return_value=tick), \
                patch.object(CF.public_feed, "ticker", return_value=stats), \
                patch.object(CF.public_feed, "m1_volume", return_value=(1000, 7)):
            snap = feed.snapshot("XAUUSDm", "5m", 10)
        self.assertEqual(snap["t"], "snap")
        self.assertEqual(len(snap["bars"]), 1)
        self.assertEqual(snap["bars"][-1][4], 4002.0)   # follows the public tick
        self.assertEqual(snap["ticker"]["price"], 4002.0)
        self.assertEqual(snap["ticker"]["source"], "public")
        self.assertEqual(snap["meta"]["source"], "public")

    def test_fallback_can_be_switched_off(self):
        feed = CF.ChartFeed()
        with patch.object(CF, "mt5", None):
            with self.assertRaises(CF.ChartUnavailable):
                feed.candles("XAUUSDm", "5m", 10)


if __name__ == "__main__":
    unittest.main()
