"""
Unit tests for PreTradeEntryBot (Bot 6 - Pre-Trade Entry Bot).

Covers: config sanitation, the Watch direction gate (NEUTRAL never trades),
the pre-trade placement at the entered Distance, fixed TP / SL, and the
70% profit-lock rule (arms at $0.80, follows up, never moves back).
"""
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.entry_bot import DEFAULTS, PreTradeEntryBot
from backend import config as app_config


def fake_info():
    """Deterministic symbol_info for XAUUSDm: 0.01 lot = $1 per $1 of price."""
    return SimpleNamespace(
        name="XAUUSDm", visible=True, trade_mode=True, trade_allowed=True,
        digits=2, point=0.01, volume_min=0.01, volume_max=100.0, volume_step=0.01,
        trade_stops_level=0, trade_freeze_level=0,
        trade_tick_value=1.0, trade_tick_size=0.01, trade_contract_size=100.0,
        filling_mode=2,
    )


def make_tick(ask=4100.0, bid=4099.8):
    return SimpleNamespace(ask=ask, bid=bid, time=1700000000)


def pos(ticket, side, price, net=0.0, volume=0.01, sl=0.0, tp=0.0, comment="bot6_b"):
    return {
        "ticket": ticket, "symbol": "XAUUSDm",
        "type": 0 if side == "buy" else 1,
        "side": side, "volume": volume, "price_open": price,
        "sl": sl, "tp": tp, "profit": net, "swap": 0.0, "net": round(net, 2),
        "magic": 777006, "comment": comment,
    }


def watch(buyer=70.0, seller=30.0, dominant="BUYER", age=0.0):
    return {"buyer_pct": buyer, "seller_pct": seller, "dominant": dominant,
            "updated_at": time.time() - age, "live": True}


class TestEntryBot(unittest.TestCase):
    def setUp(self):
        self.bot = PreTradeEntryBot()

    # ------------------------------------------------------------- config
    def test_default_config(self):
        st = self.bot.status()
        c = st["config"]
        self.assertFalse(st["running"])
        self.assertEqual(c["lot"], 0.01)
        self.assertEqual(c["distance"], 1.0)
        self.assertEqual(c["side"], "watch")
        self.assertTrue(c["watch_gate"])
        self.assertEqual(c["watch_min_pct"], 60.0)
        self.assertEqual(c["tp"], 0.0)
        self.assertEqual(c["sl"], 0.0)
        self.assertEqual(c["lock_pct"], 70.0)
        self.assertEqual(c["lock_min_profit"], 0.80)
        self.assertEqual(c["magic"], 777006)
        self.assertEqual(st["pending_count"], 0)
        self.assertEqual(st["rules"]["tp"], "off")
        self.assertIn("70%", st["rules"]["sl"])

    def test_update_config_sanitize(self):
        out = self.bot.update_config({
            "lot": 0.05, "distance": 2.5, "tp": 1.5, "sl": 3.0,
            "side": "SELL", "watch_min_pct": 250, "symbol": "BTCUSDTm",
        })
        self.assertEqual(out["lot"], 0.05)
        self.assertEqual(out["distance"], 2.5)
        self.assertEqual(out["tp"], 1.5)
        self.assertEqual(out["sl"], 3.0)
        self.assertEqual(out["side"], "sell")
        self.assertEqual(out["watch_min_pct"], 99.0)      # clamped
        self.assertEqual(out["symbol"], "BTCUSDTm")
        # garbage falls back, never crashes
        self.assertEqual(self.bot.update_config({"distance": -5})["distance"], 0.0)
        self.assertEqual(self.bot.update_config({"tp": "abc"})["tp"], 0.0)
        self.assertEqual(self.bot.update_config({"side": "banana"})["side"], "watch")
        self.assertEqual(self.bot.update_config({"sl": -2})["sl"], 0.0)

    def test_config_module_has_matching_bot6_defaults(self):
        self.assertIn("bot6", app_config.DEFAULTS)
        b6 = app_config.DEFAULTS["bot6"]
        for k in DEFAULTS:
            self.assertIn(k, b6, f"bot6 default missing {k}")
            self.assertEqual(b6[k], DEFAULTS[k], f"bot6 default mismatch on {k}")

    # ------------------------------------------------------------ mock mt5
    def _mt5(self, m, sent):
        m.symbol_info.return_value = fake_info()
        m.symbol_info_tick.return_value = make_tick()
        m.positions_get.return_value = []
        m.orders_get.return_value = []
        counter = {"n": 2000}

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

    # -------------------------------------------------------- watch gate
    def test_neutral_watch_never_places_a_pre_trade(self):
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            self.bot.set_watch_provider(lambda sym: watch(buyer=50.0, seller=50.0, dominant="NEUTRAL"))
            self._ready()
            self.bot.tick()

            pendings = [r for (r, t) in sent if r["action"] == m.TRADE_ACTION_PENDING]
            self.assertEqual(pendings, [])
            self.assertEqual(self.bot.status()["pending_count"], 0)
            self.assertFalse(self.bot.watch["allowed"])
            self.assertIn("NEUTRAL", self.bot.watch["reason"] or "NEUTRAL")

    def test_no_dial_data_blocks_the_trade(self):
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            self.bot.set_watch_provider(lambda sym: {})
            self._ready()
            self.bot.tick()
            self.assertEqual([r for (r, _t) in sent], [])
            self.assertFalse(self.bot.watch["allowed"])

    def test_pressure_below_threshold_blocks_the_trade(self):
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            self.bot.set_watch_provider(lambda sym: watch(buyer=55.0, seller=45.0, dominant="NEUTRAL"))
            self._ready(watch_min_pct=60.0)
            self.bot.tick()
            self.assertEqual([r for (r, _t) in sent], [])

    def test_buyer_pressure_places_a_buy_stop_at_the_distance(self):
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            self.bot.set_watch_provider(lambda sym: watch(buyer=72.0, seller=28.0))
            self._ready(lot=0.01, distance=1.5)
            self.bot.tick()

            pendings = [(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_PENDING]
            self.assertEqual(len(pendings), 1)
            req = pendings[0][0]
            self.assertEqual(req["type"], 4)                 # BUY STOP
            self.assertAlmostEqual(req["price"], 4101.50, places=2)   # ask 4100 + 1.5
            self.assertEqual(req["volume"], 0.01)
            self.assertEqual(req["magic"], 777006)
            self.assertEqual(req["sl"], 0.0)                 # SL 0 -> profit-lock arm
            self.assertEqual(req["tp"], 0.0)                 # TP 0 -> no TP
            st = self.bot.status()
            self.assertEqual(st["pending_count"], 1)
            self.assertEqual(st["entries"][0]["side"], "buy")

    def test_seller_pressure_places_a_sell_stop_below_the_market(self):
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            self.bot.set_watch_provider(lambda sym: watch(buyer=25.0, seller=75.0, dominant="SELLER"))
            self._ready(distance=2.0)
            self.bot.tick()
            pendings = [(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_PENDING]
            self.assertEqual(len(pendings), 1)
            self.assertEqual(pendings[0][0]["type"], 5)      # SELL STOP
            self.assertAlmostEqual(pendings[0][0]["price"], 4097.80, places=2)  # bid 4099.8 - 2.0

    def test_fixed_tp_and_sl_become_price_levels(self):
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            self.bot.set_watch_provider(lambda sym: watch())
            # 0.01 lot on this symbol = $1 per $1 of price
            self._ready(lot=0.01, distance=1.0, tp=0.50, sl=2.00)
            self.bot.tick()
            req = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_PENDING][0]
            self.assertAlmostEqual(req["price"], 4101.00, places=2)
            self.assertAlmostEqual(req["tp"], 4101.50, places=2)   # + $0.50
            self.assertAlmostEqual(req["sl"], 4099.00, places=2)   # - $2.00

    def test_watch_gate_off_uses_the_fixed_side(self):
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            # a NEUTRAL dial must not matter once the gate is OFF
            self.bot.set_watch_provider(lambda sym: watch(buyer=50.0, seller=50.0, dominant="NEUTRAL"))
            self._ready(side="buy", watch_gate=False)
            self.bot.tick()
            pendings = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_PENDING]
            self.assertEqual(len(pendings), 1)
            self.assertEqual(pendings[0]["type"], 4)

    def test_both_sides_places_two_pre_trades(self):
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            self.bot.set_watch_provider(lambda sym: watch(buyer=65.0, seller=70.0, dominant="SELLER"))
            self._ready(side="both")
            self.bot.tick()
            pendings = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_PENDING]
            self.assertEqual(len(pendings), 2)
            self.assertEqual(sorted(r["type"] for r in pendings), [4, 5])
            self.assertEqual(self.bot.status()["pending_count"], 2)

    # ------------------------------------------------------------ stop
    def test_stop_cancels_the_pre_trade(self):
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            self.bot.set_watch_provider(lambda sym: watch())
            bot = self._ready(distance=1.0)
            bot.tick()
            placed = [(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_PENDING]
            self.assertEqual(len(placed), 1)
            m.orders_get.return_value = [{
                "ticket": placed[0][1], "symbol": "XAUUSDm", "magic": 777006, "state": 1,
                "type": 4, "price_open": 4101.0, "volume_current": 0.01,
            }]
            bot.update_config({"running": False})
            bot._stop_cancel_ts = 0.0
            bot.tick()
            removes = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_REMOVE]
            self.assertEqual(len(removes), 1)
            self.assertFalse(bot.cfg["running"])
            # no extra pre-trade while stopped
            self.assertEqual(len([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_PENDING]), 1)

    # ------------------------------------------- profit lock / TP / SL
    def test_profit_lock_arms_from_80_cents_and_locks_70_percent(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        bot._pos_meta = {111: {"side": "buy", "lot": 0.01, "entry": 4100.0, "managed": True}}
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            m.symbol_info_tick.return_value = make_tick(ask=4101.2, bid=4101.0)
            positions = [pos(111, "buy", 4100.0, net=1.00)]
            bot._last_sl_send_ts = 0.0
            bot._apply_protection(positions)
        sltp = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_SLTP]
        self.assertEqual(len(sltp), 1)
        self.assertAlmostEqual(sltp[0]["sl"], 4100.70, places=2)   # 70% of $1.00
        self.assertEqual(sltp[0]["position"], 111)

    def test_profit_lock_stays_off_below_80_cents(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        bot._pos_meta = {111: {"side": "buy", "lot": 0.01, "entry": 4100.0, "managed": True}}
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            m.symbol_info_tick.return_value = make_tick(ask=4100.7, bid=4100.5)
            bot._last_sl_send_ts = 0.0
            bot._apply_protection([pos(111, "buy", 4100.0, net=0.50)])
        self.assertEqual([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_SLTP], [])

    def test_profit_lock_never_moves_backward(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        bot._pos_meta = {111: {"side": "buy", "lot": 0.01, "entry": 4100.0, "managed": True}}
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            m.symbol_info_tick.return_value = make_tick(ask=4101.2, bid=4101.0)
            bot._last_sl_send_ts = 0.0
            # profit fell back to $0.50 but the SL already locks $0.90 -> untouched
            bot._apply_protection([pos(111, "buy", 4100.0, net=0.50, sl=4100.90)])
        self.assertEqual([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_SLTP], [])

    def test_profit_lock_follows_a_growing_profit(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        bot._pos_meta = {111: {"side": "buy", "lot": 0.01, "entry": 4100.0, "managed": True}}
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            m.symbol_info_tick.return_value = make_tick(ask=4103.2, bid=4103.0)
            bot._last_sl_send_ts = 0.0
            bot._apply_protection([pos(111, "buy", 4100.0, net=3.00, sl=4100.70)])
        sltp = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_SLTP]
        self.assertEqual(len(sltp), 1)
        self.assertAlmostEqual(sltp[0]["sl"], 4102.10, places=2)   # 70% of $3.00

    def test_fixed_sl_and_tp_are_applied_to_the_open_trade(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        bot._pos_meta = {111: {"side": "sell", "lot": 0.01, "entry": 4100.0, "managed": True}}
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            m.symbol_info_tick.return_value = make_tick(ask=4099.0, bid=4098.8)
            bot.update_config({"sl": 2.0, "tp": 0.5})
            bot._last_sl_send_ts = 0.0
            bot._apply_protection([pos(111, "sell", 4100.0, net=0.0)])
        sltp = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_SLTP]
        self.assertEqual(len(sltp), 1)
        self.assertAlmostEqual(sltp[0]["sl"], 4102.00, places=2)   # sell: SL above
        self.assertAlmostEqual(sltp[0]["tp"], 4099.50, places=2)   # sell: TP below

    def test_tp_zero_means_no_take_profit_is_ever_set(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        bot._pos_meta = {111: {"side": "buy", "lot": 0.01, "entry": 4100.0, "managed": True}}
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            m.symbol_info_tick.return_value = make_tick(ask=4100.4, bid=4100.2)
            bot._last_sl_send_ts = 0.0
            bot._apply_protection([pos(111, "buy", 4100.0, net=0.10)])
        sltp = [r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_SLTP]
        self.assertEqual(sltp, [])

    # ------------------------------------------------------------ close all
    def test_close_all_closes_trades_and_cancels_the_pre_trade(self):
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            self.bot.set_watch_provider(lambda sym: watch())
            bot = self._ready()
            bot.tick()
            ticket = [t for (r, t) in sent if r["action"] == m.TRADE_ACTION_PENDING][0]
            m.orders_get.return_value = [{
                "ticket": ticket, "symbol": "XAUUSDm", "magic": 777006, "state": 1,
                "type": 4, "price_open": 4101.0, "volume_current": 0.01,
            }]
            m.positions_get.return_value = [pos(901, "buy", 4101.0, net=0.25)]
            closed = bot.close_all("dashboard")
            self.assertEqual(closed, 1)
            self.assertFalse(bot.cfg["running"])
            self.assertEqual(len([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_REMOVE]), 1)
            self.assertEqual(len([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_DEAL]), 1)

    # ------------------------------------------------------------- AI gate
    def test_ai_opposite_verdict_blocks_the_pre_trade(self):
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            self.bot.set_watch_provider(lambda sym: watch(buyer=75.0, seller=25.0))
            # the AI board is bullish while the watch dials say BUY too - a
            # SELL here is impossible, so force the clash through the side field
            self.bot.set_ai_provider(lambda sym: {"market": "XAUUSDm", "verdict": "SELL",
                                                  "confidence": 80, "buy": 0, "sell": 3})
            self._ready(side="buy", watch_gate=True)
            self.bot.tick()
            self.assertEqual([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_PENDING], [])
            self.assertFalse(self.bot.ai["allowed"])
            self.assertIn("opposes", self.bot.ai["reason"])

    def test_ai_agreeing_verdict_allows_the_trade(self):
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            self.bot.set_watch_provider(lambda sym: watch(buyer=75.0, seller=25.0))
            self.bot.set_ai_provider(lambda sym: {"market": "XAUUSDm", "verdict": "BUY",
                                                  "confidence": 66, "buy": 3, "sell": 1})
            self._ready(side="buy")
            self.bot.tick()
            self.assertEqual(len([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_PENDING]), 1)
            self.assertTrue(self.bot.ai["allowed"])
            self.assertIn("agrees", self.bot.ai["reason"])

    def test_ai_mixed_or_unknown_market_does_not_block(self):
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            self.bot.set_watch_provider(lambda sym: watch(buyer=75.0, seller=25.0))
            self.bot.set_ai_provider(lambda sym: {"market": "XAUUSDm", "verdict": "MIXED"})
            self._ready(side="buy")
            self.bot.tick()
            self.assertEqual(len([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_PENDING]), 1)
            self.assertTrue(self.bot.ai["allowed"])

    def test_ai_gate_off_ignores_the_board(self):
        sent = []
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            self.bot.set_watch_provider(lambda sym: watch(buyer=75.0, seller=25.0))
            self.bot.set_ai_provider(lambda sym: {"market": "XAUUSDm", "verdict": "SELL"})
            bot = self._ready(side="buy", ai_gate=False)
            bot.tick()
            self.assertEqual(len([r for (r, _t) in sent if r["action"] == m.TRADE_ACTION_PENDING]), 1)
            self.assertIn("OFF", bot.ai["reason"])

    def test_failed_placement_backs_off(self):
        sent = []
        bot = PreTradeEntryBot()
        with patch("backend.entry_bot.mt5") as m:
            self._mt5(m, sent)
            m.order_send.side_effect = lambda req: (sent.append((dict(req), 0)),
                                                    SimpleNamespace(retcode=10018, order=0))[1]
            bot.symbol = "XAUUSDm"
            bot.symbol_ok = True
            bot.set_watch_provider(lambda sym: watch())
            bot.update_config({"running": True, "distance": 1.0})
            bot.tick()
            attempts = len(sent)
            self.assertGreater(attempts, 0)
            self.assertGreater(bot._retry_after, 0.0)
            bot.tick()                      # the retry backoff must hold
            self.assertEqual(len(sent), attempts)
            self.assertEqual(bot.status()["pending_count"], 0)


if __name__ == "__main__":
    unittest.main()
