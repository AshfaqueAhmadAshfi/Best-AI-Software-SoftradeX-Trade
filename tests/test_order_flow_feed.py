"""Order-flow proxy tests.

The six extra venues are read server-side because their public trade APIs do
not send CORS headers. These tests fake only the HTTP payloads and verify the
normalisation the browser terminals depend on: side, size, notional and time.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import order_flow_feed as OF


class Base(unittest.TestCase):
    def setUp(self):
        OF.reset_cache()


class TestCatalogue(Base):
    def test_all_six_venues_are_registered(self):
        ids = {item["id"] for item in OF.catalogue()}
        self.assertEqual(ids, {"kucoin", "bitfinex", "mexc", "gemini",
                               "deribit", "htx"})

    def test_gold_maps_to_each_venues_tokenised_metal_pair(self):
        self.assertEqual(OF.VENUES["kucoin"]["pairs"]["XAU"], "PAXG-USDT")
        self.assertEqual(OF.VENUES["bitfinex"]["pairs"]["XAU"], "XAUT:USD")
        self.assertEqual(OF.VENUES["gemini"]["pairs"]["XAU"], "paxgusd")
        self.assertEqual(OF.VENUES["htx"]["pairs"]["XAU"], "xautusdt")
        self.assertNotIn("XAU", OF.VENUES["mexc"]["pairs"])
        self.assertNotIn("XAU", OF.VENUES["deribit"]["pairs"])

    def test_unknown_venue_is_rejected(self):
        with self.assertRaises(OF.OrderFlowUnavailable):
            OF.trades("not-a-venue", "BTC")


class TestKuCoin(Base):
    def test_nanosecond_time_and_sides(self):
        payload = {"code": "200000", "data": [
            {"tradeId": "2", "price": "101.5", "size": "0.2",
             "side": "sell", "time": 1792830429302000000},
            {"tradeId": "1", "price": "101.0", "size": "0.1",
             "side": "buy", "time": 1792830429000000000},
        ]}
        with patch.object(OF, "_http_json", return_value=payload):
            out = OF.trades("kucoin", "BTC", 80)
        self.assertEqual([row["id"] for row in out["trades"]], ["1", "2"])
        self.assertEqual(out["trades"][0]["ts"], 1792830429000)
        self.assertEqual(out["trades"][0]["side"], "BUY")
        self.assertAlmostEqual(out["trades"][1]["value"], 20.3)
        self.assertEqual(out["pair"], "BTC-USDT")


class TestBitfinex(Base):
    def test_signed_amount_sets_the_taker_side(self):
        payload = [
            [3, 1792830474905, -0.5, 100.0],
            [2, 1792830474000, 0.25, 101.0],
        ]
        with patch.object(OF, "_http_json", return_value=payload):
            out = OF.trades("bitfinex", "BTC", 80)
        self.assertEqual([row["side"] for row in out["trades"]], ["BUY", "SELL"])
        self.assertAlmostEqual(out["trades"][1]["qty"], 0.5)
        self.assertAlmostEqual(out["trades"][1]["value"], 50.0)


class TestMexc(Base):
    def test_maker_flag_decides_the_taker_side_and_ids_are_stable(self):
        payload = [
            {"a": None, "p": "100.0", "q": "2.0", "T": 1792830484727, "m": True},
            {"a": None, "p": "100.5", "q": "1.0", "T": 1792830484000, "m": False},
        ]
        with patch.object(OF, "_http_json", return_value=payload) as http:
            first = OF.trades("mexc", "BTC", 80)
            OF.reset_cache()
            second = OF.trades("mexc", "BTC", 80)
        self.assertEqual([row["side"] for row in first["trades"]], ["BUY", "SELL"])
        self.assertEqual([row["id"] for row in first["trades"]],
                         [row["id"] for row in second["trades"]])


class TestGemini(Base):
    def test_millisecond_field_is_preferred_over_seconds(self):
        payload = [
            {"tid": 7, "price": "4006.05", "amount": "0.005",
             "type": "buy", "timestamp": 1792819205,
             "timestampms": 1792819205709},
        ]
        with patch.object(OF, "_http_json", return_value=payload):
            out = OF.trades("gemini", "XAU", 80)
        self.assertEqual(out["pair"], "paxgusd")
        self.assertEqual(out["trades"][0]["ts"], 1792819205709)
        self.assertAlmostEqual(out["trades"][0]["value"], 20.03025)


class TestDeribit(Base):
    def test_btc_amount_is_usd_and_sol_amount_is_base_units(self):
        btc = {"result": {"trades": [
            {"trade_seq": 9, "price": 80000.0, "amount": 400.0,
             "direction": "buy", "timestamp": 1792830493297},
        ]}}
        sol = {"result": {"trades": [
            {"trade_seq": 4, "price": 100.0, "amount": 2.0,
             "direction": "sell", "timestamp": 1792830493000},
        ]}}
        with patch.object(OF, "_http_json", return_value=btc):
            out = OF.trades("deribit", "BTC", 80)
        self.assertAlmostEqual(out["trades"][0]["qty"], 0.005)
        self.assertAlmostEqual(out["trades"][0]["value"], 400.0)

        OF.reset_cache()
        with patch.object(OF, "_http_json", return_value=sol):
            out = OF.trades("deribit", "SOL", 80)
        self.assertAlmostEqual(out["trades"][0]["qty"], 2.0)
        self.assertAlmostEqual(out["trades"][0]["value"], 200.0)

    def test_deribit_has_no_gold_tape(self):
        with self.assertRaises(OF.OrderFlowUnavailable):
            OF.trades("deribit", "GOLD")


class TestHtx(Base):
    def test_nested_trade_groups_are_flattened(self):
        payload = {"status": "ok", "data": [
            {"id": 1, "data": [
                {"trade-id": 11, "price": 100.0, "amount": 1.5,
                 "direction": "buy", "ts": 1792830605238},
            ]},
            {"id": 2, "data": [
                {"trade-id": 12, "price": 99.5, "amount": 2.0,
                 "direction": "sell", "ts": 1792830605000},
            ]},
        ]}
        with patch.object(OF, "_http_json", return_value=payload):
            out = OF.trades("htx", "BTC", 80)
        self.assertEqual([row["id"] for row in out["trades"]], ["12", "11"])
        self.assertEqual([row["side"] for row in out["trades"]], ["SELL", "BUY"])

    def test_gold_uses_xaut(self):
        self.assertEqual(OF.VENUES["htx"]["pairs"]["XAU"], "xautusdt")


class TestCache(Base):
    def test_second_call_inside_the_ttl_does_not_hit_the_network(self):
        payload = {"data": [
            {"tradeId": "1", "price": "1.0", "size": "1.0",
             "side": "buy", "time": 1792830429000000000},
        ]}
        with patch.object(OF, "_http_json", return_value=payload) as http:
            OF.trades("kucoin", "BTC", 80)
            OF.trades("kucoin", "BTC", 80)
        self.assertEqual(http.call_count, 1)


class TestErrors(Base):
    def test_a_provider_failure_becomes_a_readable_error(self):
        with patch.object(OF, "_http_json", side_effect=OSError("network down")):
            with self.assertRaises(OF.OrderFlowUnavailable) as ctx:
                OF.trades("mexc", "BTC", 80)
        self.assertIn("MEXC", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
