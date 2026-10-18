"""
Unit tests for ReversalCandleBot (Bot 7 - Reversal Candle Bot).

Covers: config sanitation, the reversal trigger (bull candle breaks back down
=> SELL, bear candle breaks back up => BUY), instant market entry, the Watch
gate, one trade per closed candle, and the same TP / SL rules as Bot 6.
"""
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.reversal_bot import DEFAULTS, ReversalCandleBot, _as_dict, _rate_field
from backend import config as app_config

try:                    # MetaTrader5 requires numpy, so this is always there
    import numpy as np
except Exception:       # pragma: no cover - keeps the suite portable
    np = None

CANDLE_TIME = 1700000600

# the dtype mt5.copy_rates_from_pos actually returns
RATES_DTYPE = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
               ("close", "<f8"), ("tick_volume", "<u8"), ("spread", "<i4"),
               ("real_volume", "<u8")]


def np_rates(bull=True, o=4098.0, c=4100.0, t=CANDLE_TIME):
    """The REAL shape MT5 hands back: a numpy structured array, not dicts.

    The bot used to convert this row with vars() -> TypeError -> every tick
    died and bot 7 never traded at all. These tests are the guard for that.
    """
    older = (t - 120, 4096.0, 4098.5, 4095.5, 4098.0, 10, 2, 0)
    closed = (t, o, max(o, c) + 0.4, min(o, c) - 0.4, c, 10, 2, 0)
    forming = (t + 60, c, c + 0.3, c - 0.3, c + 0.1, 10, 2, 0)
    return np.array([older, closed, forming], dtype=RATES_DTYPE)


def fake_info():
    """Deterministic symbol_info for XAUUSDm: 0.01 lot = $1 per $1 of price."""
    return SimpleNamespace(
        name="XAUUSDm", visible=True, trade_mode=True, trade_allowed=True,
        digits=2, point=0.01, volume_min=0.01, volume_max=100.0, volume_step=0.01,
        trade_stops_level=0, trade_freeze_level=0,
        trade_tick_value=1.0, trade_tick_size=0.01, trade_contract_size=100.0,
        filling_mode=2,
    )


def make_tick(ask=4099.5, bid=4099.4):
    return SimpleNamespace(ask=ask, bid=bid, time=CANDLE_TIME)


def rates(bull=True, o=4098.0, c=4100.0, t=CANDLE_TIME):
    """Three M1 rows: [older, LAST CLOSED, forming]. rates[-2] is the trigger."""
    older = {"time": t - 120, "open": 4096.0, "high": 4098.5, "low": 4095.5, "close": 4098.0}
    closed = {"time": t, "open": o, "high": max(o, c) + 0.4, "low": min(o, c) - 0.4, "close": c}
    forming = {"time": t + 60, "open": c, "high": c + 0.3, "low": c - 0.3, "close": c + 0.1}
    return [older, closed, forming]


def pos(ticket, side, price, net=0.0, volume=0.01, sl=0.0, tp=0.0):
    return {
        "ticket": ticket, "symbol": "XAUUSDm",
        "type": 0 if side == "buy" else 1,
        "side": side, "volume": volume, "price_open": price,
        "sl": sl, "tp": tp, "profit": net, "swap": 0.0, "net": round(net, 2),
        "magic": 777007, "comment": "bot7_rev",
    }


def watch(buyer=30.0, seller=75.0, dominant="SELLER", age=0.0):
    return {"buyer_pct": buyer, "seller_pct": seller, "dominant": dominant,
            "updated_at": time.time() - age, "live": True}


class TestReversalBot(unittest.TestCase):
    def setUp(self):
        self.bot = ReversalCandleBot()

    # ------------------------------------------------------------- config
    def test_default_config(self):
        st = self.bot.status()
        c = st["config"]
        self.assertFalse(st["running"])
        self.assertEqual(c["lot"], 0.01)
        self.assertEqual(c["timeframe"], "M1")
        self.assertEqual(c["reverse_dist"], 0.0)
        self.assertEqual(c["tp"], 0.0)
        self.assertEqual(c["sl"], 0.0)
        self.assertEqual(c["lock_pct"], 70.0)
        self.assertEqual(c["magic"], 777007)
        self.assertEqual(st["running_count"], 0)
        self.assertIn("trigger", st)
        self.assertIn("candle", st)

    def test_update_config_sanitize(self):
        out = self.bot.update_config({
            "lot": 0.04, "timeframe": "m5", "reverse_dist": 0.75, "min_body": 0.2,
            "tp": 1.25, "sl": 3.5, "max_trades": 99, "watch_min_pct": -5,
        })
        self.assertEqual(out["lot"], 0.04)
        self.assertEqual(out["timeframe"], "M5")
        self.assertEqual(out["reverse_dist"], 0.75)
        self.assertEqual(out["min_body"], 0.2)
        self.assertEqual(out["tp"], 1.25)
        self.assertEqual(out["sl"], 3.5)
        self.assertEqual(out["max_trades"], 20)          # clamped
        self.assertEqual(out["watch_min_pct"], 0.0)      # clamped
        self.assertEqual(self.bot.update_config({"timeframe": "H4"})["timeframe"], "M1")
        self.assertEqual(self.bot.update_config({"reverse_dist": -1})["reverse_dist"], 0.0)

    def test_config_module_has_matching_bot7_defaults(self):
        self.assertIn("bot7", app_config.DEFAULTS)
        b7 = app_config.DEFAULTS["bot7"]
        for k in DEFAULTS:
            self.assertIn(k, b7, f"bot7 default missing {k}")
            self.assertEqual(b7[k], DEFAULTS[k], f"bot7 default mismatch on {k}")

    # ------------------------------------------------------ reversal logic
    def test_bull_candle_reversing_down_triggers_a_sell(self):
        bot = self.bot
        bot.update_config({"reverse_dist": 1.0})
        candle = {"ready": True, "bull": True, "body": 2.0, "close": 4100.0, "open": 4098.0}
        self.assertEqual(bot.reversal_trigger(candle, 4099.5), "")     # 0.5 back -> not yet
        self.assertEqual(bot.reversal_trigger(candle, 4099.0), "sell")  # 1.0 back -> SELL
        self.assertEqual(bot.reversal_trigger(candle, 4097.0), "sell")

    def test_bear_candle_reversing_up_triggers_a_buy(self):
        bot = self.bot
        bot.update_config({"reverse_dist": 0.5})
        candle = {"ready": True, "bull": False, "body": 2.0, "close": 4096.0, "open": 4098.0}
        self.assertEqual(bot.reversal_trigger(candle, 4096.2), "")
        self.assertEqual(bot.reversal_trigger(candle, 4096.5), "buy")
        self.assertEqual(bot.reversal_trigger(candle, 4099.0), "buy")

    def test_zero_reverse_distance_uses_the_candle_body(self):
        bot = self.bot
        bot.update_config({"reverse_dist": 0.0})
        candle = {"ready": True, "bull": True, "body": 4.0, "close": 4100.0, "open": 4096.0}
        self.assertEqual(bot.reversal_trigger(candle, 4097.0), "")     # 3.0 < body 4.0
        self.assertEqual(bot.reversal_trigger(candle, 4096.0), "sell")  # 4.0 == body

    def test_min_body_filters_small_candles(self):
        bot = self.bot
        bot.update_config({"reverse_dist": 0.5, "min_body": 2.0})
        tiny = {"ready": True, "bull": True, "body": 0.5, "close": 4100.0}
        self.assertEqual(bot.reversal_trigger(tiny, 4099.0), "")
        big = {"ready": True, "bull": True, "body": 3.0, "close": 4100.0}
        self.assertEqual(bot.reversal_trigger(big, 4099.0), "sell")

    def test_not_ready_candle_never_triggers(self):
        bot = self.bot
        bot.update_config({"reverse_dist": 1.0})
        self.assertEqual(bot.reversal_trigger({"ready": False, "bull": True}, 4000.0), "")

    # ------------------------------------------------------------ mock mt5
    def _mt5(self, m, sent, rows):
        m.symbol_info.return_value = fake_info()
        m.symbol_info_tick.return_value = make_tick()
        m.positions_get.return_value = []
        m.orders_get.return_value = []
        m.copy_rates_from_pos.return_value = rows
        counter = {"n": 3000}

        def send(req):
            counter["n"] += 1
            sent.append((dict(req), counter["n"]))
            return SimpleNamespace(retcode=10009, order=counter["n"], comment="done")

        m.order_send.side_effect = send
        return m

    def _ready(self, **cfg):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        patch_cfg = {"running": True}
        patch_cfg.update(cfg)
        bot.update_config(patch_cfg)
        return bot

    # ----------------------------------------------------------- trading
    def test_tick_fires_a_sell_the_moment_the_bull_candle_reverses(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=True, o=4098.0, c=4100.0))
            # reverse distance 0.5 -> price 4099.45 is already 0.55 back
            self.bot.set_watch_provider(lambda sym: watch(buyer=25.0, seller=72.0, dominant="SELLER"))
            bot = self._ready(reverse_dist=0.5)
            bot.tick()
            deals = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL]
            self.assertEqual(len(deals), 1)
            self.assertEqual(deals[0]["type"], m.ORDER_TYPE_SELL)
            self.assertEqual(deals[0]["magic"], 777007)
            self.assertEqual(deals[0]["volume"], 0.01)
            self.assertEqual(bot._last_trigger_candle, CANDLE_TIME)
            self.assertIn("REVERSAL SELL", bot.last_action)

    def test_tick_fires_a_buy_when_the_bear_candle_reverses_up(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=False, o=4100.0, c=4096.0))
            m.symbol_info_tick.return_value = make_tick(ask=4096.70, bid=4096.60)
            self.bot.set_watch_provider(lambda sym: watch(buyer=74.0, seller=26.0, dominant="BUYER"))
            bot = self._ready(reverse_dist=0.5)
            bot.tick()
            deals = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL]
            self.assertEqual(len(deals), 1)
            self.assertEqual(deals[0]["type"], m.ORDER_TYPE_BUY)
            self.assertIn("REVERSAL BUY", bot.last_action)

    def test_no_reversal_no_trade(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=True, o=4098.0, c=4100.0))
            m.symbol_info_tick.return_value = make_tick(ask=4100.80, bid=4100.70)
            self.bot.set_watch_provider(lambda sym: watch(buyer=25.0, seller=72.0))
            bot = self._ready(reverse_dist=0.5)
            bot.tick()
            self.assertEqual([r for (r, _t) in sent], [])
            self.assertEqual(bot._last_trigger_candle, 0)

    def test_neutral_watch_blocks_the_reversal(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=True, o=4098.0, c=4100.0))
            self.bot.set_watch_provider(
                lambda sym: watch(buyer=50.0, seller=50.0, dominant="NEUTRAL"))
            bot = self._ready(reverse_dist=0.5)
            bot.tick()
            self.assertEqual([r for (r, _t) in sent], [])
            self.assertEqual(bot._last_trigger_candle, 0)
            self.assertFalse(bot.watch["allowed"])

    def test_watch_gate_off_trades_without_the_dials(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=True, o=4098.0, c=4100.0))
            self.bot.set_watch_provider(
                lambda sym: watch(buyer=50.0, seller=50.0, dominant="NEUTRAL"))
            bot = self._ready(reverse_dist=0.5, watch_gate=False)
            bot.tick()
            self.assertEqual(len([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL]), 1)

    def test_one_closed_candle_fires_only_once(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=True, o=4098.0, c=4100.0))
            self.bot.set_watch_provider(lambda sym: watch(buyer=25.0, seller=72.0))
            bot = self._ready(reverse_dist=0.5)
            bot.tick()
            self.assertEqual(len([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL]), 1)
            bot._cooldown_done = True
            bot.tick()      # same closed candle -> nothing new
            self.assertEqual(len([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL]), 1)

    def test_new_candle_can_fire_again(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=True, o=4098.0, c=4100.0))
            self.bot.set_watch_provider(lambda sym: watch(buyer=25.0, seller=72.0))
            bot = self._ready(reverse_dist=0.5)
            bot.tick()
            self.assertEqual(len([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL]), 1)
            # a newer candle closes -> the radar arms again
            m.copy_rates_from_pos.return_value = rates(bull=True, o=4098.0, c=4100.0,
                                                      t=CANDLE_TIME + 60)
            bot._last_order_ts = 0.0
            bot.tick()
            self.assertEqual(len([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL]), 2)

    def test_max_trades_blocks_a_second_position(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=True, o=4098.0, c=4100.0))
            self.bot.set_watch_provider(lambda sym: watch(buyer=25.0, seller=72.0))
            bot = self._ready(reverse_dist=0.5)
            m.positions_get.return_value = [pos(701, "sell", 4099.5, net=0.30)]
            bot.tick()
            self.assertEqual([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL], [])

    # ------------------------------------------------------------- AI gate
    def test_ai_opposite_verdict_blocks_the_reversal(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=True, o=4098.0, c=4100.0))
            self.bot.set_watch_provider(lambda sym: watch(buyer=25.0, seller=72.0))
            # the reversal wants a SELL but every news source points up
            self.bot.set_ai_provider(lambda sym: {"market": "XAUUSDm", "verdict": "BUY",
                                                  "confidence": 75, "buy": 4, "sell": 1})
            bot = self._ready(reverse_dist=0.5)
            bot.tick()
            self.assertEqual([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL], [])
            self.assertFalse(bot.ai["allowed"])
            self.assertEqual(bot._last_trigger_candle, 0)

    def test_ai_agreeing_verdict_allows_the_reversal(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=True, o=4098.0, c=4100.0))
            self.bot.set_watch_provider(lambda sym: watch(buyer=25.0, seller=72.0))
            self.bot.set_ai_provider(lambda sym: {"market": "XAUUSDm", "verdict": "SELL",
                                                  "confidence": 60, "buy": 1, "sell": 3})
            bot = self._ready(reverse_dist=0.5)
            bot.tick()
            self.assertEqual(len([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL]), 1)
            self.assertTrue(bot.ai["allowed"])

    def test_ai_gate_off_ignores_the_board(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=True, o=4098.0, c=4100.0))
            self.bot.set_watch_provider(lambda sym: watch(buyer=25.0, seller=72.0))
            self.bot.set_ai_provider(lambda sym: {"market": "XAUUSDm", "verdict": "BUY"})
            bot = self._ready(reverse_dist=0.5, ai_gate=False)
            bot.tick()
            self.assertEqual(len([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL]), 1)

    # ------------------------------------------- profit lock / TP / SL
    def test_profit_lock_arms_from_80_cents_and_locks_70_percent(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        bot._pos_meta = {111: {"side": "buy", "lot": 0.01, "entry": 4100.0, "managed": True}}
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates())
            m.symbol_info_tick.return_value = make_tick(ask=4101.2, bid=4101.0)
            bot._last_sl_send_ts = 0.0
            bot._apply_protection([pos(111, "buy", 4100.0, net=1.00)])
        sltp = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_SLTP]
        self.assertEqual(len(sltp), 1)
        self.assertAlmostEqual(sltp[0]["sl"], 4100.70, places=2)
        self.assertAlmostEqual(sltp[0]["tp"], 0.0, places=2)

    def test_profit_lock_stays_off_below_80_cents(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        bot._pos_meta = {111: {"side": "buy", "lot": 0.01, "entry": 4100.0, "managed": True}}
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates())
            m.symbol_info_tick.return_value = make_tick(ask=4100.7, bid=4100.5)
            bot._last_sl_send_ts = 0.0
            bot._apply_protection([pos(111, "buy", 4100.0, net=0.60)])
        self.assertEqual([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_SLTP], [])

    def test_profit_lock_never_moves_backward(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        bot._pos_meta = {111: {"side": "sell", "lot": 0.01, "entry": 4100.0, "managed": True}}
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates())
            m.symbol_info_tick.return_value = make_tick(ask=4098.8, bid=4098.6)
            bot._last_sl_send_ts = 0.0
            # $0.50 profit now, SL already locks more -> untouched
            bot._apply_protection([pos(111, "sell", 4100.0, net=0.50, sl=4098.50)])
        self.assertEqual([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_SLTP], [])

    def test_fixed_tp_and_sl_attach_to_the_open_trade(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        bot._pos_meta = {111: {"side": "sell", "lot": 0.01, "entry": 4100.0, "managed": True}}
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates())
            m.symbol_info_tick.return_value = make_tick(ask=4099.0, bid=4098.8)
            bot.update_config({"tp": 0.5, "sl": 2.0})
            bot._last_sl_send_ts = 0.0
            bot._apply_protection([pos(111, "sell", 4100.0, net=0.0)])
        sltp = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_SLTP]
        self.assertEqual(len(sltp), 1)
        self.assertAlmostEqual(sltp[0]["tp"], 4099.50, places=2)
        self.assertAlmostEqual(sltp[0]["sl"], 4102.00, places=2)

    def test_market_order_carries_fixed_tp_and_sl(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=True, o=4098.0, c=4100.0))
            self.bot.set_watch_provider(lambda sym: watch(buyer=25.0, seller=72.0))
            bot = self._ready(reverse_dist=0.5, tp=1.0, sl=2.0, lot=0.01)
            bot.tick()
            deal = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL][0]
            self.assertAlmostEqual(deal["price"], 4099.40, places=2)   # sell @ bid
            self.assertAlmostEqual(deal["tp"], 4098.40, places=2)     # - $1.00
            self.assertAlmostEqual(deal["sl"], 4101.40, places=2)     # + $2.00

    # ------------------------------------------------------------ close all
    def test_close_all_closes_the_position(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates())
            bot = self._ready()
            m.positions_get.return_value = [pos(801, "sell", 4099.8, net=0.40)]
            m.orders_get.return_value = []
            closed = bot.close_all("dashboard")
            self.assertEqual(closed, 1)
            self.assertFalse(bot.cfg["running"])
            self.assertEqual(len([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL]), 1)

    def test_rates_error_is_reported_and_blocks_trading(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, None)
            m.copy_rates_from_pos.return_value = None
            self.bot.set_watch_provider(lambda sym: watch())
            bot = self._ready(reverse_dist=0.5)
            bot.tick()
            self.assertEqual([r for (r, _t) in sent], [])
            self.assertTrue(bot.status()["trigger"]["rates_error"])


@unittest.skipIf(np is None, "numpy not installed")
class TestNumpyCandleRows(unittest.TestCase):
    """Regression: bot 7 died on every tick reading a real MT5 candle row.

    Live symptom: "Bot7 error: vars() argument must have __dict__ attribute"
    repeated forever, /api/bot7 answering a dead stub and no trade ever taken.
    """

    def setUp(self):
        self.bot = ReversalCandleBot()

    def _mt5(self, m, sent, rows):
        m.symbol_info.return_value = fake_info()
        m.symbol_info_tick.return_value = make_tick()
        m.positions_get.return_value = []
        m.orders_get.return_value = []
        m.copy_rates_from_pos.return_value = rows
        counter = {"n": 5000}

        def send(req):
            counter["n"] += 1
            sent.append((dict(req), counter["n"]))
            return SimpleNamespace(retcode=10009, order=counter["n"], comment="done")

        m.order_send.side_effect = send
        return m

    def test_as_dict_survives_a_numpy_row_and_a_bare_object(self):
        row = np_rates()[-2]
        d = _as_dict(row)                     # used to raise TypeError
        self.assertEqual(d["open"], 4098.0)
        self.assertEqual(d["close"], 4100.0)
        self.assertIn("time", d)
        self.assertEqual(_as_dict(object()), {})      # no __dict__, no __slots__
        self.assertEqual(_as_dict(None), {})
        self.assertEqual(_as_dict({"a": 1}), {"a": 1})

    def test_rate_field_reads_name_attribute_and_position(self):
        row = np_rates()[-2]
        self.assertEqual(_rate_field(row, "close", 4), 4100.0)
        self.assertEqual(_rate_field({"close": 7.5}, "close", 4), 7.5)
        self.assertEqual(_rate_field((1, 2, 3, 4, 5), "close", 4), 5.0)
        self.assertEqual(_rate_field(object(), "close", 4), 0.0)   # never raises

    def test_read_candle_accepts_a_numpy_row(self):
        with patch("backend.reversal_bot.mt5") as m:
            m.symbol_info.return_value = fake_info()
            m.symbol_info_tick.return_value = make_tick()
            m.positions_get.return_value = []
            m.orders_get.return_value = []
            m.copy_rates_from_pos.return_value = np_rates(bull=False, o=4100.0, c=4096.0)
            bot = self._armed()
            candle = bot.read_candle()
            self.assertTrue(candle["ready"])
            self.assertAlmostEqual(candle["close"], 4096.0, places=2)
            self.assertAlmostEqual(candle["body"], 4.0, places=2)
            self.assertFalse(candle["bull"])
            self.assertEqual(bot.status()["trigger"]["rates_error"], "")

    def test_tick_trades_instead_of_dying_on_numpy_rows(self):
        """End to end: numpy rows + a reversal must place the order."""
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, np_rates(bull=False, o=4100.0, c=4096.0))
            m.symbol_info_tick.return_value = make_tick(ask=4096.70, bid=4096.60)
            self.bot.set_watch_provider(lambda sym: watch(buyer=74.0, seller=26.0, dominant="BUYER"))
            bot = self._armed(reverse_dist=0.5)
            bot.tick()
            deals = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL]
            self.assertEqual(len(deals), 1)
            self.assertEqual(deals[0]["type"], m.ORDER_TYPE_BUY)
            self.assertIn("REVERSAL BUY", bot.last_action)

    def _armed(self, **cfg):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        patch_cfg = {"running": True}
        patch_cfg.update(cfg)
        bot.update_config(patch_cfg)
        return bot


class TestTurningPoint(unittest.TestCase):
    """The reversal is measured from where the market actually turned.

    "candle namtesa, aro namtesa, hotat kore uthe gelo" - the bounce must be
    measured from the deepest low, not from the stale candle close.
    """

    def setUp(self):
        self.bot = ReversalCandleBot()

    def _mt5(self, m, sent, rows):
        m.symbol_info.return_value = fake_info()
        m.symbol_info_tick.return_value = make_tick()
        m.positions_get.return_value = []
        m.orders_get.return_value = []
        m.copy_rates_from_pos.return_value = rows
        counter = {"n": 4000}

        def send(req):
            counter["n"] += 1
            sent.append((dict(req), counter["n"]))
            return SimpleNamespace(retcode=10009, order=counter["n"], comment="done")

        m.order_send.side_effect = send
        return m

    def test_turn_point_follows_a_deeper_low_then_fires_on_the_bounce(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            # bear candle closing at 4096 that then keeps falling to 4094
            self._mt5(m, sent, rates(bull=False, o=4100.0, c=4096.0))
            self.bot.set_watch_provider(lambda sym: watch(buyer=74.0, seller=26.0, dominant="BUYER"))
            bot = self.bot
            bot.symbol = "XAUUSDm"
            bot.symbol_ok = True
            bot.update_config({"running": True, "reverse_dist": 0.5})

            # tick 1: price is deeper than the candle close -> arm the low only
            m.symbol_info_tick.return_value = make_tick(ask=4094.05, bid=4093.95)
            bot.tick()
            self.assertEqual([r for (r, _t) in sent], [])
            self.assertAlmostEqual(bot._move_ref, 4094.0, places=2)
            triggers = bot.status()["trigger"]
            self.assertAlmostEqual(triggers["ref"], 4094.0, places=2)
            self.assertAlmostEqual(triggers["need"], 4094.5, places=2)

            # tick 2: a sudden bounce off that low -> BUY fires
            m.symbol_info_tick.return_value = make_tick(ask=4094.70, bid=4094.60)
            bot.tick()
            deals = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL]
            self.assertEqual(len(deals), 1)
            self.assertEqual(deals[0]["type"], m.ORDER_TYPE_BUY)
            self.assertIn("reversed from", bot.last_action)

    def test_radar_shows_the_candle_even_while_the_bot_is_stopped(self):
        """A stopped bot must still fill the radar - no "waiting for candles"."""
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, [], np_rates(bull=False, o=4100.0, c=4096.0))
            bot = self.bot
            bot.symbol = "XAUUSDm"
            bot.symbol_ok = True
            bot.update_config({"running": False, "reverse_dist": 0.5})
            st = bot.status()          # no tick() at all
            self.assertTrue(st["candle"]["ready"])
            self.assertAlmostEqual(st["candle"]["close"], 4096.0, places=2)
            self.assertAlmostEqual(st["trigger"]["ref"], 4096.0, places=2)
            self.assertAlmostEqual(st["trigger"]["need"], 4096.5, places=2)
            self.assertEqual(st["trigger"]["rates_error"], "")

    def test_deeper_low_without_a_bounce_never_trades(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=False, o=4100.0, c=4096.0))
            self.bot.set_watch_provider(lambda sym: watch(buyer=74.0, seller=26.0, dominant="BUYER"))
            bot = self.bot
            bot.symbol = "XAUUSDm"
            bot.symbol_ok = True
            bot.update_config({"running": True, "reverse_dist": 0.5})
            m.symbol_info_tick.return_value = make_tick(ask=4093.05, bid=4092.95)
            bot.tick()
            m.symbol_info_tick.return_value = make_tick(ask=4093.40, bid=4093.30)
            bot.tick()          # only 0.4 back from the low -> still no trade
            self.assertEqual([r for (r, _t) in sent], [])

    def test_gate_block_is_reported_so_the_user_can_see_why(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=True, o=4098.0, c=4100.0))
            self.bot.set_watch_provider(
                lambda sym: watch(buyer=50.0, seller=50.0, dominant="NEUTRAL"))
            bot = self.bot
            bot.symbol = "XAUUSDm"
            bot.symbol_ok = True
            bot.update_config({"running": True, "reverse_dist": 0.5})
            bot.tick()
            trig = bot.status()["trigger"]
            self.assertIn("Watch SELL", trig["blocked"])
            self.assertEqual(trig["blocked_side"], "sell")
            self.assertFalse(trig["fired"])

    def test_new_candle_rearms_the_turning_point(self):
        sent = []
        with patch("backend.reversal_bot.mt5") as m:
            self._mt5(m, sent, rates(bull=False, o=4100.0, c=4096.0))
            self.bot.set_watch_provider(lambda sym: watch(buyer=74.0, seller=26.0, dominant="BUYER"))
            bot = self.bot
            bot.symbol = "XAUUSDm"
            bot.symbol_ok = True
            bot.update_config({"running": True, "reverse_dist": 0.5})
            m.symbol_info_tick.return_value = make_tick(ask=4094.05, bid=4093.95)
            bot.tick()
            self.assertAlmostEqual(bot._move_ref, 4094.0, places=2)
            # a fresh candle closes -> the turning point starts at its close again
            m.copy_rates_from_pos.return_value = rates(bull=False, o=4100.0, c=4098.0,
                                                      t=CANDLE_TIME + 60)
            m.symbol_info_tick.return_value = make_tick(ask=4098.05, bid=4097.95)
            bot.tick()
            self.assertAlmostEqual(bot._move_ref, 4098.0, places=2)
            self.assertEqual([r for (r, _t) in sent], [])


if __name__ == "__main__":
    unittest.main()
