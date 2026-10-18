"""
Unit tests for TrendStructureBot (Bot 2 - Candle Momentum & Structure Bot).
"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.trend_bot import TrendStructureBot


class TestTrendStructureBot(unittest.TestCase):
    def setUp(self):
        self.bot = TrendStructureBot()

    def test_default_config(self):
        st = self.bot.status()
        self.assertFalse(st["running"])
        self.assertEqual(st["config"]["lot"], 0.01)
        self.assertEqual(st["config"]["max_trades"], 1)
        self.assertEqual(st["config"]["tp"], 0.10)
        self.assertEqual(st["config"]["sl"], 2.00)
        self.assertEqual(st["config"]["symbol"], "BTCUSDTm")
        self.assertEqual(st["config"]["magic"], 777002)

    def test_update_config(self):
        cfg = self.bot.update_config({
            "lot": 0.05,
            "max_trades": 3,
            "tp": 0.25,
            "sl": 1.50,
            "symbol": "XAUUSDm",
            "timeframe": "W1",
        })
        self.assertEqual(cfg["lot"], 0.05)
        self.assertEqual(cfg["max_trades"], 3)
        self.assertEqual(cfg["tp"], 0.25)
        self.assertEqual(cfg["sl"], 1.50)
        self.assertEqual(cfg["symbol"], "XAUUSDm")
        self.assertEqual(cfg["timeframe"], "W1")

    def test_max_trades_limit_enforced(self):
        self.bot.cfg["running"] = True
        self.bot.cfg["max_trades"] = 2
        self.bot.symbol = "BTCUSDTm"
        self.bot.symbol_ok = True

        fake_positions = [
            {"ticket": 101, "side": "buy", "volume": 0.01, "price_open": 83000.0, "net": 0.02, "magic": 777002},
            {"ticket": 102, "side": "sell", "volume": 0.01, "price_open": 83050.0, "net": -0.05, "magic": 777002},
        ]

        with patch.object(self.bot, "_open_market_trade") as mock_open:
            self.bot._check_and_trade(fake_positions)
            # Limit of 2 reached, should NOT open new trade
            mock_open.assert_not_called()

    def test_tp_sl_management_auto_close(self):
        self.bot.cfg["tp"] = 0.10
        self.bot.cfg["sl"] = 2.00

        # Position 1: Profit reached TP ($0.15 >= $0.10)
        p_win = {"ticket": 201, "side": "buy", "volume": 0.01, "price_open": 83000.0, "net": 0.15, "magic": 777002}
        # Position 2: Loss reached SL (-$2.10 <= -$2.00)
        p_loss = {"ticket": 202, "side": "sell", "volume": 0.01, "price_open": 83000.0, "net": -2.10, "magic": 777002}
        # Position 3: Running inside normal bounds
        p_run = {"ticket": 203, "side": "buy", "volume": 0.01, "price_open": 83000.0, "net": 0.04, "magic": 777002}

        with patch.object(self.bot, "_close_position") as mock_close:
            self.bot._manage_positions([p_win, p_loss, p_run])
            self.assertEqual(mock_close.call_count, 2)
            mock_close.assert_any_call(p_win, "TP reached (+$0.15 >= $0.10)")
            mock_close.assert_any_call(p_loss, "SL hit (-$2.10 >= $2.00)")

    def test_structure_level_trading_overrides_candle_momentum(self):
        """When price is at Lower Low (LL) with BUYER watch pressure, it takes BUY and bypasses step 4."""
        self.bot.cfg["running"] = True
        self.bot.cfg["max_trades"] = 1
        self.bot.symbol = "BTCUSDTm"
        self.bot.symbol_ok = True
        self._set_watch(70.0, 30.0, "BUYER")  # BUYER pressure allows BUY direction

        struct_data = {
            "d1": {"hh": 85000.0, "lh": 84500.0, "hl": 82800.0, "ll": 82000.0},
            "w1": {},
            "timeframe": "D1",
        }

        with patch.object(self.bot, "compute_structure", return_value=struct_data), \
             patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open:

            mock_tick = MagicMock()
            mock_tick.ask = 82005.0  # Touching Lower Low (82000.0)
            mock_tick.bid = 81995.0
            mock_mt5.symbol_info_tick.return_value = mock_tick

            self.bot._check_and_trade([])
            mock_open.assert_called_once_with("buy", "bot2_struct_ll_buy")

    def test_watch_off_candle_follow_down_takes_sell(self):
        """Watch OFF: the candle is falling (DOWN), the bot follows it -> SELL."""
        self.bot.cfg["running"] = True
        self.bot.cfg["max_trades"] = 1
        self.bot.cfg["watch_enabled"] = False
        self.bot.cfg["trend_mode"] = False
        self.bot.symbol = "BTCUSDTm"
        self.bot.symbol_ok = True
        self._set_watch(30.0, 70.0, "SELLER")  # the dial must be ignored while Watch is OFF

        fake_rates = [{"open": 83050.0, "time": 1700000000}]  # bar open = 83050
        with patch.object(self.bot, "compute_structure", return_value={"d1": {}, "w1": {}}), \
             patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open:

            mock_tick = MagicMock()
            mock_tick.ask = 83000.0  # current price ~82995, falling 50 points below open
            mock_tick.bid = 82990.0
            mock_mt5.symbol_info_tick.return_value = mock_tick
            mock_mt5.copy_rates_from_pos.return_value = fake_rates

            self.bot._check_and_trade([])
            mock_open.assert_called_once_with("sell", "bot2_candle_down_sell")

    def test_watch_off_candle_follow_up_takes_buy(self):
        """Watch OFF: the candle is rising (UP), the bot follows it -> BUY."""
        self.bot.cfg["running"] = True
        self.bot.cfg["max_trades"] = 1
        self.bot.cfg["watch_enabled"] = False
        self.bot.cfg["trend_mode"] = False
        self.bot.symbol = "BTCUSDTm"
        self.bot.symbol_ok = True
        self._set_watch(70.0, 30.0, "BUYER")  # the dial must be ignored while Watch is OFF

        fake_rates = [{"open": 83000.0, "time": 1700000000}]  # bar open = 83000
        with patch.object(self.bot, "compute_structure", return_value={"d1": {}, "w1": {}}), \
             patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open:

            mock_tick = MagicMock()
            mock_tick.ask = 83060.0  # current price ~83055, rising 55 points above open
            mock_tick.bid = 83050.0
            mock_mt5.symbol_info_tick.return_value = mock_tick
            mock_mt5.copy_rates_from_pos.return_value = fake_rates

            self.bot._check_and_trade([])
            mock_open.assert_called_once_with("buy", "bot2_candle_up_buy")

    def test_close_all_panic_button(self):
        p1 = {"ticket": 301, "side": "buy", "volume": 0.01, "price_open": 83000.0, "net": 0.0, "magic": 777002}
        p2 = {"ticket": 302, "side": "sell", "volume": 0.01, "price_open": 83100.0, "net": 0.0, "magic": 777002}

        with patch.object(self.bot, "_all_positions", return_value=[p1, p2]), \
             patch.object(self.bot, "_close_position", return_value=True) as mock_close:
            closed = self.bot.close_all("panic")
            self.assertEqual(closed, 2)
            self.assertFalse(self.bot.cfg["running"])

    # ---------------------------- Watch switch / Watch % field / Reverse button
    def test_watch_off_ignores_the_dial_completely(self):
        """Watch OFF -> both sides are allowed whatever the dial says."""
        self.bot.symbol = "BTCUSDTm"
        self.bot.cfg["watch_enabled"] = False
        self.bot.cfg["trend_mode"] = False
        self._set_watch(51.0, 49.0, "NEUTRAL")   # would block everything while Watch is ON
        self.assertTrue(self.bot._check_watch_threshold("buy")[0])
        self.assertTrue(self.bot._check_watch_threshold("sell")[0])

    def test_watch_pct_field_is_authoritative_not_60(self):
        """The Watch % field decides what is traded - 60% is only the starting value."""
        self.bot.symbol = "BTCUSDTm"
        self._set_watch(65.0, 35.0)
        self.assertTrue(self.bot._check_watch_threshold("buy")[0])     # 65 >= 60 (default)

        self.bot.update_config({"watch_min_pct": 70.0})
        self.assertFalse(self.bot._check_watch_threshold("buy")[0])    # 65 < 70 -> blocked

        self.bot.update_config({"watch_min_pct": 50.0})
        self.assertTrue(self.bot._check_watch_threshold("buy")[0])     # 65 >= 50 -> allowed
        self.assertEqual(self.bot.status()["watch_min_pct"], 50.0)
        self.assertEqual(self.bot.cfg["watch_min_threshold_pct"], 50.0)  # legacy alias follows

    def test_watch_switch_syncs_with_legacy_trend_mode(self):
        """The Watch button and the old trend_mode key are one single setting."""
        cfg = self.bot.update_config({"watch_enabled": False})
        self.assertFalse(cfg["watch_enabled"])
        self.assertFalse(cfg["trend_mode"])

        cfg = self.bot.update_config({"trend_mode": True})
        self.assertTrue(cfg["watch_enabled"])
        self.assertTrue(cfg["trend_mode"])

        st = self.bot.status()
        self.assertTrue(st["watch_enabled"])
        self.assertIn("reverse", st)
        self.assertIn("next_side", st)

    def test_watch_off_trend_follows_the_candle(self):
        """Watch OFF: check_trend reads the running candle and never the dial."""
        self.bot.symbol = "BTCUSDTm"
        self.bot.cfg["watch_enabled"] = False
        self.bot.cfg["trend_mode"] = False
        reads = []
        self.bot.set_watch_provider(lambda sym: reads.append(sym) or {
            "buyer_pct": 80.0, "seller_pct": 20.0, "dominant": "BUYER", "updated_at": time.time()})

        with patch("backend.trend_bot.mt5") as mock_mt5:
            tick = MagicMock()
            tick.ask = 85100.0
            tick.bid = 85090.0
            mock_mt5.symbol_info_tick.return_value = tick

            mock_mt5.copy_rates_from_pos.return_value = [{"open": 85000.0}]
            self.assertEqual(self.bot.check_trend(force=True), "UP")

            mock_mt5.copy_rates_from_pos.return_value = [{"open": 85200.0}]
            self.bot._last_trend_check_ts = 0.0
            self.assertEqual(self.bot.check_trend(force=True), "DOWN")

            # an 80% BUYER dial never even gets read while the Watch switch is OFF
            self.assertEqual(reads, [])

    def test_reverse_button_flips_the_order_side(self):
        """Reverse ON: a BUY decision reaches the broker as a SELL, and vice versa."""
        self.bot.symbol = "BTCUSDTm"
        self.bot.cfg["reverse"] = True
        self._set_watch(70.0, 30.0, "BUYER")   # the dial confirms BUY, then it is flipped

        captured = {}

        def fake_send(req, label):
            captured["req"] = req
            return True

        with patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "_send", side_effect=fake_send), \
             patch.object(self.bot, "money_per_price", return_value=1.0):
            mock_tick = MagicMock()
            mock_tick.ask = 85000.0
            mock_tick.bid = 84990.0
            mock_mt5.symbol_info_tick.return_value = mock_tick
            mock_mt5.TRADE_ACTION_DEAL = 1
            mock_mt5.ORDER_TYPE_BUY = 0
            mock_mt5.ORDER_TYPE_SELL = 1
            mock_mt5.ORDER_TIME_GTC = 0

            self.assertTrue(self.bot._open_market_trade("buy", "test"))
            self.assertEqual(captured["req"]["type"], mock_mt5.ORDER_TYPE_SELL)
            self.assertEqual(captured["req"]["price"], 84990.0)   # the SELL hits the bid

            self.bot.cfg["reverse"] = False
            self.assertTrue(self.bot._open_market_trade("buy", "test2"))
            self.assertEqual(captured["req"]["type"], mock_mt5.ORDER_TYPE_BUY)

    def test_next_side_reports_the_reversed_direction(self):
        """The dashboard hint shows the direction the bot would really take."""
        self.bot.cfg["reverse"] = False
        self.bot._current_trend = "UP"
        self.bot._last_candle_dir = ""
        self.assertEqual(self.bot._next_side(), "BUY")

        self.bot.cfg["reverse"] = True
        self.assertEqual(self.bot._next_side(), "SELL")

        # candle-follow branch (Watch OFF) flips too
        self.bot.cfg["watch_enabled"] = False
        self.bot.cfg["trend_mode"] = False
        self.bot._current_trend = "NEUTRAL"
        self.bot._last_candle_dir = "UP"
        self.assertEqual(self.bot._next_side(), "SELL")
        self.bot.cfg["reverse"] = False
        self.assertEqual(self.bot._next_side(), "BUY")

    def _set_watch(self, buyer, seller, dominant=""):
        """Wires a fake watch-dial provider onto the bot (fresh timestamp)."""
        dom = dominant or ("BUYER" if buyer >= seller + 5 else ("SELLER" if seller >= buyer + 5 else "NEUTRAL"))
        self.bot.set_watch_provider(lambda sym: {
            "symbol": sym, "buyer_pct": buyer, "seller_pct": seller,
            "dominant": dom, "updated_at": time.time(),
        })

    def test_trend_check_2_5s_interval(self):
        """Verifies that check_trend reads the watch dials every 2.5s and caches between."""
        self.bot.symbol = "BTCUSDTm"
        self.bot._last_trend_check_ts = 1000.0
        self.bot._current_trend = "UP"
        reads = []

        def provider(sym):
            reads.append(sym)
            return {"buyer_pct": 62.0, "seller_pct": 38.0, "dominant": "BUYER", "updated_at": 1000.0}

        self.bot.set_watch_provider(provider)

        with patch("time.time", return_value=1002.0):
            # 2 seconds elapsed (< 2.5s interval): should NOT re-read the dial
            t = self.bot.check_trend(force=False)
            self.assertEqual(t, "UP")
            self.assertEqual(reads, [])

        with patch("time.time", return_value=1006.0):
            # 6 seconds elapsed (>= 2.5s interval): should re-read the dial -> BUYER -> UP
            t = self.bot.check_trend(force=False)
            self.assertEqual(t, "UP")
            self.assertEqual(self.bot._last_trend_check_ts, 1006.0)
            self.assertEqual(reads, ["BTCUSDTm"])

        # SELLER pressure -> DOWN
        self._set_watch(38.0, 62.0)
        with patch("time.time", return_value=1008.0):
            t = self.bot.check_trend(force=True)
            self.assertEqual(t, "DOWN")
            self.assertEqual(self.bot._last_trend_check_ts, 1008.0)

        # NEUTRAL pressure -> NEUTRAL (no trade state)
        self._set_watch(51.0, 49.0)
        with patch("time.time", return_value=1010.0):
            t = self.bot.check_trend(force=True)
            self.assertEqual(t, "NEUTRAL")

    def test_watch_pressure_rules(self):
        """Rules 1-3: BUYER->buy dir, SELLER->sell dir, NEUTRAL->no-trade dir."""
        self.bot.symbol = "BTCUSDTm"

        self._set_watch(70.0, 30.0, "BUYER")
        self.assertEqual(self.bot.check_trend(force=True), "UP")

        self._set_watch(30.0, 70.0, "SELLER")
        self.assertEqual(self.bot.check_trend(force=True), "DOWN")

        self._set_watch(50.0, 50.0, "NEUTRAL")
        self.assertEqual(self.bot.check_trend(force=True), "NEUTRAL")

    def test_watch_stale_data_is_neutral(self):
        """A stale dial (>30s old) must read NEUTRAL so the bot stays flat."""
        self.bot.symbol = "BTCUSDTm"
        self.bot.set_watch_provider(lambda sym: {
            "buyer_pct": 80.0, "seller_pct": 20.0, "dominant": "BUYER", "updated_at": 900.0,
        })
        with patch("time.time", return_value=1000.0):  # 100s stale
            self.assertEqual(self.bot.check_trend(force=True), "NEUTRAL")

    def test_no_watch_provider_is_neutral(self):
        """With no dial source at all, the bot must not trade (NEUTRAL)."""
        self.bot.symbol = "BTCUSDTm"
        self.assertEqual(self.bot.check_trend(force=True), "NEUTRAL")

    def test_trend_calculation_bullish_and_bearish(self):
        """Tests that _calculate_market_trend correctly identifies UP vs DOWN."""
        self.bot.symbol = "BTCUSDTm"

        # 1. Bullish scenario: Price 85000, D1 open 84000 (cur > open), M1 candle bullish (close > open)
        with patch("backend.trend_bot.mt5") as mock_mt5:
            mock_tick = MagicMock()
            mock_tick.bid = 85000.0
            mock_mt5.symbol_info_tick.return_value = mock_tick
            mock_mt5.TIMEFRAME_D1 = 16408
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.TIMEFRAME_M1 = 1
            mock_mt5.copy_rates_from_pos.side_effect = lambda sym, tf, start, count: (
                [{"open": 84000.0, "close": 84500.0, "high": 85100.0, "low": 83900.0}] if tf == 16408
                else [{"open": 84200.0, "close": 84300.0}] * count if tf == 16385
                else [{"open": 84800.0, "close": 84900.0, "high": 84950.0, "low": 84750.0}] * count
            )

            trend, score, details = self.bot._calculate_market_trend("BTCUSDTm")
            self.assertEqual(trend, "UP")
            self.assertGreater(score, 0)

        # 2. Bearish scenario: Price 82000, D1 open 84000 (cur < open), M1 candle bearish (close < open)
        with patch("backend.trend_bot.mt5") as mock_mt5:
            mock_tick = MagicMock()
            mock_tick.bid = 82000.0
            mock_mt5.symbol_info_tick.return_value = mock_tick
            mock_mt5.TIMEFRAME_D1 = 16408
            mock_mt5.TIMEFRAME_H1 = 16385
            mock_mt5.TIMEFRAME_M1 = 1
            mock_mt5.copy_rates_from_pos.side_effect = lambda sym, tf, start, count: (
                [{"open": 84000.0, "close": 83000.0, "high": 84200.0, "low": 81900.0}] if tf == 16408
                else [{"open": 83500.0, "close": 83200.0}] * count if tf == 16385
                else [{"open": 82500.0, "close": 82200.0, "high": 82600.0, "low": 82100.0}] * count
            )

            trend, score, details = self.bot._calculate_market_trend("BTCUSDTm")
            self.assertEqual(trend, "DOWN")
            self.assertLess(score, 0)

    def test_status_contains_5s_trend_metadata(self):
        """Status dict includes 5s trend metadata and countdown."""
        self.bot.symbol = "BTCUSDTm"
        self.bot._current_trend = "DOWN"
        self.bot._trend_details = "Bearish test"
        self.bot._trend_score = -2
        self.bot._last_trend_check_ts = 5000.0

        with patch("time.time", return_value=5002.0):
            st = self.bot.status()
            self.assertEqual(st["market_trend"], "DOWN")
            self.assertEqual(st["trend_interval"], 2.5)
            self.assertEqual(st["trend_next_check_in"], 0.5)  # 2.5 - 2.0 = 0.5s
            self.assertEqual(st["trend_details"], "Bearish test")
            self.assertEqual(st["trend_score"], -2)

    def test_trend_2_5s_interval_between_trades(self):
        """Verifies that consecutive trades in trend mode are spaced by at least 2.5 seconds."""
        self.bot.cfg["running"] = True
        self.bot.cfg["trend_mode"] = True
        self.bot.symbol = "BTCUSDTm"
        self.bot.symbol_ok = True
        self.bot._current_trend = "UP"
        self._set_watch(70.0, 30.0, "BUYER")  # BUYER pressure -> buy direction
        self.bot._last_order_ts = 1000.0  # Trade 1 opened at t=1000.0s

        with patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "compute_structure", return_value={"d1": {}}), \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open:

            mock_tick = MagicMock()
            mock_tick.ask = 85000.0
            mock_tick.bid = 84990.0
            mock_mt5.symbol_info_tick.return_value = mock_tick
            mock_mt5.copy_rates_from_pos.return_value = [{"open": 84980.0, "time": 1700000000}]

            # At t=1001.0s (only 1s elapsed): must NOT trade!
            with patch("time.time", return_value=1001.0):
                self.bot._check_and_trade([])
                mock_open.assert_not_called()

            # At t=1002.4s (only 2.4s elapsed): must NOT trade!
            with patch("time.time", return_value=1002.4):
                self.bot._check_and_trade([])
                mock_open.assert_not_called()

            # At t=1002.6s (>= 2.5s elapsed): MUST trade on BUYER pressure!
            with patch("time.time", return_value=1002.6):
                self.bot._check_and_trade([])
                mock_open.assert_called_once_with("buy", "bot2_watch_buyer_buy")

    def test_no_simultaneous_trades_when_max_trades_reached(self):
        """Verifies that no trade is taken when active positions reach max_trades."""
        self.bot.cfg["running"] = True
        self.bot.cfg["trend_mode"] = True
        self.bot.cfg["max_trades"] = 2
        self.bot.symbol = "BTCUSDTm"
        self.bot.symbol_ok = True
        self.bot._current_trend = "UP"
        self.bot._last_order_ts = 900.0  # long ago (> 2.5s)

        running_pos = [
            {"ticket": 501, "side": "buy", "volume": 0.01, "price_open": 85000.0, "net": 0.05, "magic": 777002},
            {"ticket": 502, "side": "buy", "volume": 0.01, "price_open": 85010.0, "net": 0.02, "magic": 777002},
        ]

        with patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "_open_market_trade") as mock_open:

            mock_tick = MagicMock()
            mock_tick.ask = 85010.0
            mock_tick.bid = 85000.0
            mock_mt5.symbol_info_tick.return_value = mock_tick

            # 2 of 2 positions currently running -> strictly NO trade allowed
            with patch("time.time", return_value=1000.0):
                self.bot._check_and_trade(running_pos)
                mock_open.assert_not_called()

    def test_trend_mode_sequential_2_5s_trading_up_to_max_trades(self):
        """Verifies:
        1. 2.5 second a check korbe eita
        2. check kore trade nibe bot (BUYER watch pressure -> BUY)
        3. Eitar shaty max trades er system er connect thakbe:
           opens 1 trade every 2.5s until max_trades (e.g. 5) is reached.
        """
        self.bot.cfg["running"] = True
        self.bot.cfg["trend_mode"] = True
        self.bot.cfg["max_trades"] = 5
        self.bot.symbol = "BTCUSDTm"
        self.bot.symbol_ok = True
        self.bot._current_trend = "UP"
        self._set_watch(70.0, 30.0, "BUYER")  # BUYER pressure -> buy direction
        self.bot._last_order_ts = 1000.0  # trade 1 opened at t=1000s

        # 1 position running (1 / 5)
        pos_1 = [{"ticket": 601, "side": "buy", "volume": 0.01, "price_open": 85000.0, "net": 0.01, "magic": 777002}]

        with patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "compute_structure", return_value={"d1": {}}), \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open:

            mock_tick = MagicMock()
            mock_tick.ask = 85000.0
            mock_tick.bid = 84990.0
            mock_mt5.symbol_info_tick.return_value = mock_tick
            mock_mt5.copy_rates_from_pos.return_value = [{"open": 85000.0, "time": 1700000000}]

            # At t=1001.5s (< 2.5s cooldown): must NOT open trade 2 yet!
            with patch("time.time", return_value=1001.5):
                self.bot._check_and_trade(pos_1)
                mock_open.assert_not_called()

            # At t=1002.6s (>= 2.5s since last trade): MUST open trade 2 on BUYER pressure!
            with patch("time.time", return_value=1002.6):
                self.bot._check_and_trade(pos_1)
                mock_open.assert_called_once_with("buy", "bot2_watch_buyer_buy")

    def test_trend_mode_down_trend_takes_sell_trade(self):
        """Verifies SELLER watch pressure leads to a SELL trade after the 2.5s timer."""
        self.bot.cfg["running"] = True
        self.bot.cfg["trend_mode"] = True
        self.bot.cfg["max_trades"] = 3
        self.bot.symbol = "BTCUSDTm"
        self.bot.symbol_ok = True
        self.bot._current_trend = "DOWN"
        self._set_watch(30.0, 70.0, "SELLER")  # SELLER pressure -> sell direction
        self.bot._last_order_ts = 990.0  # > 2.5s ago

        with patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "compute_structure", return_value={"d1": {}}), \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open:

            mock_tick = MagicMock()
            mock_tick.ask = 85000.0
            mock_tick.bid = 84990.0
            mock_mt5.symbol_info_tick.return_value = mock_tick
            mock_mt5.copy_rates_from_pos.return_value = [{"open": 85000.0, "time": 1700000000}]

            with patch("time.time", return_value=1000.0):
                self.bot._check_and_trade([])
                mock_open.assert_called_once_with("sell", "bot2_watch_seller_sell")

    # --------------------------------- 1-minute candle time-window block rules
    def _make_rates(self, bar_time):
        """Mock M1 rates payload whose only bar opened at bar_time (epoch)."""
        return [{"open": 85000.0, "time": bar_time}]

    def _allow_trading(self, trend_mode=True):
        """Common setup: BUYER pressure + fresh 2.5s timer so only the window gate can block."""
        self.bot.cfg["running"] = True
        self.bot.cfg["trend_mode"] = trend_mode
        self.bot.symbol = "BTCUSDTm"
        self.bot.symbol_ok = True
        self._set_watch(70.0, 30.0, "BUYER")
        self.bot._last_order_ts = 900.0  # long ago - only the window gate can block

    def _patch_candle_env(self, mock_mt5, now):
        """Patches the MT5 tick and the M1 rates (bar opened exactly at now - 0s handled by caller)."""
        mock_tick = MagicMock()
        mock_tick.ask = 85000.0
        mock_tick.tick = 85000.0
        mock_tick.bid = 84990.0
        mock_mt5.symbol_info_tick.return_value = mock_tick

    def test_block_config_fields_accept_user_values(self):
        """Rule 4: the window fields are user-authoritative; update_config stores them as given."""
        cfg = self.bot.update_config({
            "block_start_sec": 3,
            "block_end_sec": 7,
 "block_mid_from": 20,
            "block_mid_to": 40,
        })
        self.assertEqual(cfg["block_start_sec"], 3.0)
        self.assertEqual(cfg["block_end_sec"], 7.0)
        self.assertEqual(cfg["block_mid_from"], 20.0)
        self.assertEqual(cfg["block_mid_to"], 40.0)

    def test_no_trade_in_first_10s_of_candle(self):
        """Rule 1: 5s into the candle -> no trade (first-window block)."""
        self._allow_trading()
        bar_time = 1700000000.0
        with patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "compute_structure", return_value={"d1": {}}), \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open, \
             patch("time.time", return_value=bar_time + 5.0):
            self._patch_candle_env(mock_mt5, bar_time)
            mock_mt5.copy_rates_from_pos.return_value = self._make_rates(bar_time)
            self.bot._check_and_trade([])
            mock_open.assert_not_called()

    def test_no_trade_in_last_10s_of_candle(self):
        """Rule 2: 55s into the candle -> no trade (last-window block)."""
        self._allow_trading()
        bar_time = 1700000000.0
        with patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "compute_structure", return_value={"d1": {}}), \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open, \
             patch("time.time", return_value=bar_time + 55.0):
            self._patch_candle_env(mock_mt5, bar_time)
            mock_mt5.copy_rates_from_pos.return_value = self._make_rates(bar_time)
            self.bot._check_and_trade([])
            mock_open.assert_not_called()

    def test_no_trade_in_middle_window_of_candle(self):
        """Rule 3: 30s into the candle -> no trade (middle-window block)."""
        self._allow_trading()
        bar_time = 1700000000.0
        with patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "compute_structure", return_value={"d1": {}}), \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open, \
             patch("time.time", return_value=bar_time + 30.0):
            self._patch_candle_env(mock_mt5, bar_time)
            mock_mt5.copy_rates_from_pos.return_value = self._make_rates(bar_time)
            self.bot._check_and_trade([])
            mock_open.assert_not_called()

    def test_trade_allowed_between_windows_15s(self):
        """15s into the candle (outside all blocked windows) -> trade taken."""
        self._allow_trading()
        bar_time = 1700000000.0
        with patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "compute_structure", return_value={"d1": {}}), \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open, \
             patch("time.time", return_value=bar_time + 15.0):
            self._patch_candle_env(mock_mt5, bar_time)
            mock_mt5.copy_rates_from_pos.return_value = self._make_rates(bar_time)
            self.bot._check_and_trade([])
            mock_open.assert_called_once_with("buy", "bot2_watch_buyer_buy")

    def test_trade_allowed_between_windows_40s(self):
        """40s into the candle (outside all blocked windows) -> trade taken."""
        self._allow_trading()
        bar_time = 1700000000.0
        with patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "compute_structure", return_value={"d1": {}}), \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open, \
             patch("time.time", return_value=bar_time + 40.0):
            self._patch_candle_env(mock_mt5, bar_time)
            mock_mt5.copy_rates_from_pos.return_value = self._make_rates(bar_time)
            self.bot._check_and_trade([])
            mock_open.assert_called_once_with("buy", "bot2_watch_buyer_buy")

    def test_window_gate_blocks_structure_trades_too(self):
        """LL touch with BUYER pressure inside a blocked window -> still no trade."""
        self._allow_trading(trend_mode=False)
        self.bot._current_trend = "UP"
        bar_time = 1700000000.0
        struct_data = {"d1": {"hh": 85000.0, "lh": 84500.0, "hl": 82800.0, "ll": 82000.0}, "w1": {}, "timeframe": "D1"}
        with patch.object(self.bot, "compute_structure", return_value=struct_data), \
             patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "_open_market_trade") as mock_open, \
             patch("time.time", return_value=bar_time + 5.0):
            mock_tick = MagicMock()
            mock_tick.ask = 82005.0  # touching Lower Low -> would BUY without the window gate
            mock_tick.bid = 81995.0
            mock_mt5.symbol_info_tick.return_value = mock_tick
            mock_mt5.copy_rates_from_pos.return_value = self._make_rates(bar_time)
            self.bot._check_and_trade([])
            mock_open.assert_not_called()

    def test_window_gate_uses_field_values_not_fixed(self):
        """Field values are authoritative: 12s into candle with first-block=15 -> blocked."""
        self._allow_trading()
        self.bot.cfg["block_start_sec"] = 15.0
        bar_time = 1700000000.0
        with patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "compute_structure", return_value={"d1": {}}), \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open, \
             patch("time.time", return_value=bar_time + 12.0):
            self._patch_candle_env(mock_mt5, bar_time)
            mock_mt5.copy_rates_from_pos.return_value = self._make_rates(bar_time)
            self.bot._check_and_trade([])
            mock_open.assert_not_called()

    def test_window_gate_inactive_when_fields_zero(self):
        """All window fields set to 0 -> no blocking at any second of the candle."""
        self._allow_trading()
        self.bot.cfg["block_start_sec"] = 0.0
        self.bot.cfg["block_end_sec"] = 0.0
        self.bot.cfg["block_mid_from"] = 0.0
        self.bot.cfg["block_mid_to"] = 0.0
        bar_time = 1700000000.0
        with patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "compute_structure", return_value={"d1": {}}), \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open, \
             patch("time.time", return_value=bar_time + 3.0):
            self._patch_candle_env(mock_mt5, bar_time)
            mock_mt5.copy_rates_from_pos.return_value = self._make_rates(bar_time)
            self.bot._check_and_trade([])
            mock_open.assert_called_once_with("buy", "bot2_watch_buyer_buy")

    def test_neutral_pressure_blocks_structure_buy(self):
        """Rule 1 extends to structure trades: LL touch + NEUTRAL pressure -> no trade."""
        self.bot.cfg["running"] = True
        self.bot.symbol = "BTCUSDTm"
        self.bot.symbol_ok = True
        self._set_watch(51.0, 49.0, "NEUTRAL")
        self.bot._last_order_ts = 900.0

        struct_data = {"d1": {"hh": 85000.0, "lh": 84500.0, "hl": 82800.0, "ll": 82000.0}, "w1": {}, "timeframe": "D1"}
        with patch.object(self.bot, "compute_structure", return_value=struct_data), \
             patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "_open_market_trade") as mock_open:

            mock_tick = MagicMock()
            mock_tick.ask = 82005.0  # touching Lower Low
            mock_tick.bid = 81995.0
            mock_mt5.symbol_info_tick.return_value = mock_tick

            self.bot._check_and_trade([])
            mock_open.assert_not_called()

    def test_buyer_pressure_blocks_sell_structure(self):
        """Rule 2 direction lock: HH touch + BUYER pressure -> no SELL trade."""
        self.bot.cfg["running"] = True
        self.bot.symbol = "BTCUSDTm"
        self.bot.symbol_ok = True
        self._set_watch(70.0, 30.0, "BUYER")
        self.bot._last_order_ts = 900.0

        struct_data = {"d1": {"hh": 85000.0, "lh": 84500.0, "hl": 82800.0, "ll": 82000.0}, "w1": {}, "timeframe": "D1"}
        with patch.object(self.bot, "compute_structure", return_value=struct_data), \
             patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "_open_market_trade") as mock_open:

            mock_tick = MagicMock()
            mock_tick.ask = 85005.0  # above Higher High -> would SELL without the gate
            mock_tick.bid = 84995.0
            mock_mt5.symbol_info_tick.return_value = mock_tick

            self.bot._check_and_trade([])
            mock_open.assert_not_called()

    def test_tp_sl_distances_never_shrunk_by_broker_stops(self):
        """Rules 5-6: configured TP/SL dollars are used exactly, whatever the broker stop level."""
        self.bot.symbol = "BTCUSDTm"
        self.bot.cfg["tp"] = 0.10
        self.bot.cfg["sl"] = 2.00
        self._set_watch(70.0, 30.0, "BUYER")

        captured = {}
        def fake_send(req, label):
            captured["req"] = req
            return True

        with patch("backend.trend_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "_send", side_effect=fake_send), \
             patch.object(self.bot, "money_per_price", return_value=1.0), \
             patch.object(self.bot, "_min_stop_distance", return_value=100.0):

            mock_tick = MagicMock()
            mock_tick.ask = 85000.0
            mock_tick.bid = 84990.0
            mock_mt5.symbol_info_tick.return_value = mock_tick
            mock_mt5.TRADE_ACTION_DEAL = 1
            mock_mt5.ORDER_TYPE_BUY = 0
            mock_mt5.ORDER_TYPE_SELL = 1
            mock_mt5.ORDER_TIME_GTC = 0

            self.bot._open_market_trade("buy", "test")
            req = captured["req"]
            # mpp = 1.0 -> $0.10 TP = 0.10 price distance, $2.00 SL = 2.00 distance
            self.assertAlmostEqual(req["tp"], 85000.0 + 0.10, places=2)
            self.assertAlmostEqual(req["sl"], 85000.0 - 2.00, places=2)


    def test_watch_60_percent_threshold_gate(self):
        """Rule: Trade only taken if watch BUY or SELL is >= 60%; blocked if < 60%."""
        self.bot.symbol = "BTCUSDTm"

        # Case 1: Exactly 60.0% BUY pressure (60% ba 60% er beshi) -> BUY allowed, SELL blocked
        self._set_watch(60.0, 40.0)
        allow_buy, reason_buy = self.bot._check_watch_threshold("buy")
        allow_sell, reason_sell = self.bot._check_watch_threshold("sell")
        self.assertTrue(allow_buy)
        self.assertFalse(allow_sell)

        # Case 2: SELL pressure >= 60% (65% sell) -> SELL allowed, BUY blocked
        self._set_watch(35.0, 65.0)
        allow_buy, reason_buy = self.bot._check_watch_threshold("buy")
        allow_sell, reason_sell = self.bot._check_watch_threshold("sell")
        self.assertFalse(allow_buy)
        self.assertTrue(allow_sell)

        # Case 3: Both < 60% (e.g. 59.9% buy vs 40.1% sell) -> Both strictly blocked
        self._set_watch(59.9, 40.1)
        allow_buy, _ = self.bot._check_watch_threshold("buy")
        allow_sell, _ = self.bot._check_watch_threshold("sell")
        self.assertFalse(allow_buy)
        self.assertFalse(allow_sell)

        # Case 4: Empty watch data -> strictly blocked
        self.bot.set_watch_provider(lambda sym: {})
        allow_buy, _ = self.bot._check_watch_threshold("buy")
        self.assertFalse(allow_buy)

        # Case 5: _open_market_trade blocks when < 60%
        self._set_watch(59.0, 41.0)
        with patch.object(self.bot, "_send") as mock_send, \
             patch("backend.trend_bot.mt5") as mock_mt5:
            mock_tick = MagicMock()
            mock_tick.ask = 85000.0
            mock_tick.bid = 84990.0
            mock_mt5.symbol_info_tick.return_value = mock_tick
            
            # 59% BUY -> blocked, _send not called
            res = self.bot._open_market_trade("buy", "test_blocked")
            self.assertFalse(res)
            mock_send.assert_not_called()

            # 60% BUY -> allowed, _send is called
            self._set_watch(60.0, 40.0)
            res = self.bot._open_market_trade("buy", "test_allowed")
            mock_send.assert_called_once()


if __name__ == "__main__":
    unittest.main()

