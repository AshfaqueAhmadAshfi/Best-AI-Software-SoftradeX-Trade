"""
Unit tests for ExtremeStructureBot (Bot 3 - Extreme High & Low Structure Sniper Bot).
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.extreme_bot import ExtremeStructureBot


class TestExtremeStructureBot(unittest.TestCase):
    def setUp(self):
        self.bot = ExtremeStructureBot()

    def test_default_config(self):
        st = self.bot.status()
        self.assertFalse(st["running"])
        self.assertEqual(st["config"]["lot"], 0.01)
        self.assertEqual(st["config"]["max_trades"], 1)
        self.assertEqual(st["config"]["tp"], 0.50)
        self.assertEqual(st["config"]["sl"], 5.00)
        self.assertEqual(st["config"]["symbol"], "XAUUSDm")
        self.assertEqual(st["config"]["magic"], 777003)

    def test_update_config(self):
        cfg = self.bot.update_config({
            "lot": 0.05,
            "max_trades": 3,
            "tp": 1.00,
            "sl": 10.00,
            "symbol": "BTCUSDTm",
            "timeframe": "W1",
        })
        self.assertEqual(cfg["lot"], 0.05)
        self.assertEqual(cfg["max_trades"], 3)
        self.assertEqual(cfg["tp"], 1.00)
        self.assertEqual(cfg["sl"], 10.00)
        self.assertEqual(cfg["symbol"], "BTCUSDTm")
        self.assertEqual(cfg["timeframe"], "W1")

    def test_max_trades_limit_enforced(self):
        self.bot.cfg["running"] = True
        self.bot.cfg["max_trades"] = 1
        self.bot.symbol = "XAUUSDm"
        self.bot.symbol_ok = True

        fake_positions = [
            {"ticket": 301, "side": "buy", "volume": 0.01, "price_open": 4150.0, "net": 0.10, "magic": 777003},
        ]

        with patch.object(self.bot, "_open_market_trade") as mock_open:
            self.bot._check_and_trade(fake_positions)
            mock_open.assert_not_called()

    def test_tp_sl_management_auto_close(self):
        self.bot.cfg["tp"] = 0.50
        self.bot.cfg["sl"] = 5.00

        p_win = {"ticket": 401, "side": "buy", "volume": 0.01, "price_open": 4150.0, "net": 0.65, "magic": 777003}
        p_loss = {"ticket": 402, "side": "sell", "volume": 0.01, "price_open": 4150.0, "net": -5.20, "magic": 777003}
        p_run = {"ticket": 403, "side": "buy", "volume": 0.01, "price_open": 4150.0, "net": 0.10, "magic": 777003}

        with patch.object(self.bot, "_close_position") as mock_close:
            self.bot._manage_tp_sl([p_win, p_loss, p_run])
            self.assertEqual(mock_close.call_count, 2)
            mock_close.assert_any_call(p_win, "TP reached (+$0.65 >= $0.50)")
            mock_close.assert_any_call(p_loss, "SL hit (-$5.20 >= $5.00)")

    def test_middle_zone_no_trade(self):
        """When price is in the middle between extremes, NO trade is taken."""
        self.bot.cfg["running"] = True
        self.bot.cfg["max_trades"] = 1
        self.bot.symbol = "XAUUSDm"
        self.bot.symbol_ok = True

        levels_data = {
            "d1": {"hh": 4300.0, "lh": 4250.0, "hl": 4150.0, "ll": 4100.0},
            "w1": {"hh": 4400.0, "lh": 4350.0, "hl": 4120.0, "ll": 4050.0},
            "prev": {"hh": 4350.0, "ll": 4080.0},
            "active_view": "D1",
        }

        with patch.object(self.bot, "compute_all_levels", return_value=levels_data), \
             patch("backend.extreme_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "_open_market_trade") as mock_open:

            mock_tick = MagicMock()
            mock_tick.ask = 4200.0  # Dead middle between 4050 and 4400
            mock_tick.bid = 4199.8
            mock_mt5.symbol_info_tick.return_value = mock_tick

            self.bot._check_and_trade([])
            # Strictly NO trade in middle
            mock_open.assert_not_called()

    def test_extreme_low_takes_buy(self):
        """When price touches extreme Lower Low, it takes BUY trade."""
        self.bot.cfg["running"] = True
        self.bot.cfg["max_trades"] = 1
        self.bot.symbol = "XAUUSDm"
        self.bot.symbol_ok = True

        levels_data = {
            "d1": {"hh": 4300.0, "lh": 4250.0, "hl": 4150.0, "ll": 4100.0},
            "w1": {"hh": 4400.0, "lh": 4350.0, "hl": 4120.0, "ll": 4050.0},
            "prev": {"hh": 4350.0, "ll": 4080.0},
            "active_view": "D1",
        }

        with patch.object(self.bot, "compute_all_levels", return_value=levels_data), \
             patch("backend.extreme_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open:

            mock_tick = MagicMock()
            mock_tick.ask = 4100.5  # Touching Today Lower Low (4100.0)
            mock_tick.bid = 4100.1
            mock_mt5.symbol_info_tick.return_value = mock_tick

            self.bot._check_and_trade([])
            mock_open.assert_called_once_with("buy", "bot3_today_ll_buy")

    def test_extreme_high_takes_sell(self):
        """When price touches extreme Higher High, it takes SELL trade."""
        self.bot.cfg["running"] = True
        self.bot.cfg["max_trades"] = 1
        self.bot.symbol = "XAUUSDm"
        self.bot.symbol_ok = True

        levels_data = {
            "d1": {"hh": 4300.0, "lh": 4250.0, "hl": 4150.0, "ll": 4100.0},
            "w1": {"hh": 4400.0, "lh": 4350.0, "hl": 4120.0, "ll": 4050.0},
            "prev": {"hh": 4350.0, "ll": 4080.0},
            "active_view": "D1",
        }

        with patch.object(self.bot, "compute_all_levels", return_value=levels_data), \
             patch("backend.extreme_bot.mt5") as mock_mt5, \
             patch.object(self.bot, "_open_market_trade", return_value=True) as mock_open:

            mock_tick = MagicMock()
            mock_tick.ask = 4300.2  # Touching Today Higher High (4300.0)
            mock_tick.bid = 4299.8
            mock_mt5.symbol_info_tick.return_value = mock_tick

            self.bot._check_and_trade([])
            mock_open.assert_called_once_with("sell", "bot3_today_hh_sell")

    def test_planned_trades_has_5_setups(self):
        levels_data = {
            "d1": {"hh": 4300.0, "ll": 4100.0},
            "w1": {"hh": 4400.0, "ll": 4050.0},
            "prev": {"hh": 4380.0, "ll": 4080.0},
        }
        planned = self.bot.compute_planned_trades(4180.0, levels_data)
        self.assertEqual(len(planned), 5)
        actions = [p["action"] for p in planned]
        self.assertIn("BUY", actions)
        self.assertIn("SELL", actions)
        for p in planned:
            self.assertGreater(p["price"], 0)
            self.assertGreaterEqual(p["distance"], 0)

    def test_close_all_closes_and_stops(self):
        fake_positions = [
            {"ticket": 501, "symbol": "XAUUSDm", "side": "buy", "volume": 0.01, "net": 0.20, "magic": 777003},
            {"ticket": 502, "symbol": "XAUUSDm", "side": "sell", "volume": 0.01, "net": -0.10, "magic": 777003},
        ]
        self.bot.cfg["running"] = True
        with patch.object(self.bot, "_all_positions", return_value=fake_positions), \
             patch.object(self.bot, "_close_position", return_value=True) as mock_close:
            closed = self.bot.close_all("dashboard")
            self.assertEqual(closed, 2)
            self.assertFalse(self.bot.cfg["running"])


if __name__ == "__main__":
    unittest.main()
