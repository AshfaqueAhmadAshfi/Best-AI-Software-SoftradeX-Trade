"""
Unit tests for PreTradeBot (Bot 4 - Pre-Trade Ladder Bot).
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.pretrade_bot import DEFAULTS, PreTradeBot
from backend import config as app_config


def fake_info():
    """Deterministic symbol_info for XAUUSDm under a mocked MT5."""
    return SimpleNamespace(
        name="XAUUSDm", visible=True, trade_mode=True, trade_allowed=True,
        digits=2, point=0.01, volume_min=0.01, volume_max=100.0, volume_step=0.01,
        trade_stops_level=0, trade_freeze_level=0,
        trade_tick_value=1.0, trade_tick_size=0.01, trade_contract_size=100.0,
        filling_mode=2,
    )


def make_tick(ask=4100.0, bid=4099.8):
    return SimpleNamespace(ask=ask, bid=bid, time=1700000000)


def pos(ticket, side, price, net=0.0, volume=0.01, comment="", sl=0.0):
    # mirrors what PreTradeBot._all_positions() returns for a live position
    return {
        "ticket": ticket, "symbol": "XAUUSDm",
        "type": 0 if side == "buy" else 1,
        "side": side,
        "volume": volume, "price_open": price, "sl": sl, "tp": 0.0,
        "profit": net, "swap": 0.0, "net": round(net, 2),
        "magic": 777004, "comment": comment,
    }


def pending(ticket, side, price, volume=0.01):
    return {
        "ticket": ticket, "symbol": "XAUUSDm", "magic": 777004, "state": 1,
        "type": 4 if side == "buy" else 5, "price_open": price,
        "volume_initial": volume, "volume_current": volume,
    }


def ladder_entry(level, price, ticket=0, state="pending", lot=0.01):
    return {"level": level, "price": price, "lot": lot,
            "ticket": ticket, "state": state, "pos": 0}


class TestPreTradeBot(unittest.TestCase):
    def setUp(self):
        self.bot = PreTradeBot()

    # ------------------------------------------------------------- config
    def test_default_config(self):
        st = self.bot.status()
        c = st["config"]
        self.assertFalse(st["running"])
        self.assertEqual(c["lot"], 0.01)
        self.assertEqual(c["price_gap"], 1.0)
        self.assertEqual(c["pre_trade_gap"], 0.5)
        self.assertEqual(c["pre_trade_value"], 5)
        self.assertFalse(c["numbering_lot"])
        self.assertEqual(c["magic"], 777004)
        self.assertEqual(c["lock_pct"], 60.0)
        self.assertEqual(st["ladder"]["value"], 5)
        self.assertEqual(st["pending_count"], 0)

    def test_update_config_sanitize(self):
        out = self.bot.update_config({
            "lot": 0.05, "price_gap": 2.5, "pre_trade_gap": 0.25,
            "pre_trade_value": 10, "numbering_lot": True, "symbol": "BTCUSDTm",
        })
        self.assertEqual(out["lot"], 0.05)
        self.assertEqual(out["price_gap"], 2.5)
        self.assertEqual(out["pre_trade_gap"], 0.25)
        self.assertEqual(out["pre_trade_value"], 10)
        self.assertTrue(out["numbering_lot"])
        self.assertEqual(out["symbol"], "BTCUSDTm")
        # clamps - the entered values are bounded, garbage falls back safely
        self.assertEqual(self.bot.update_config({"pre_trade_value": 0})["pre_trade_value"], 1)
        self.assertEqual(self.bot.update_config({"pre_trade_value": 999})["pre_trade_value"], 50)
        self.assertEqual(self.bot.update_config({"price_gap": -3})["price_gap"], 0.0)
        self.assertEqual(self.bot.update_config({"lot": 0})["lot"], 0.01)
        self.assertEqual(self.bot.update_config({"lot": "abc"})["lot"], 0.01)

    def test_config_module_has_matching_bot4_defaults(self):
        self.assertIn("bot4", app_config.DEFAULTS)
        b4 = app_config.DEFAULTS["bot4"]
        for k in ("running", "lot", "price_gap", "pre_trade_gap", "pre_trade_value",
                  "numbering_lot", "symbol", "magic", "slippage_points", "lock_pct"):
            self.assertIn(k, b4)
            self.assertEqual(b4[k], DEFAULTS[k], f"bot4 default mismatch on {k}")

    def test_layout_change_arms_relayout(self):
        self.bot.update_config({"running": True, "lot": 0.01})   # matches defaults
        self.assertFalse(self.bot._relayout)
        self.bot.update_config({"lot": 0.03})
        self.assertTrue(self.bot._relayout)
        # reloading the same persisted config must NOT re-arm the ladder
        self.bot._relayout = False
        self.bot.update_config({"lot": 0.03})
        self.assertFalse(self.bot._relayout)

    # ------------------------------------------------------------ ladder
    def _mt5(self, m, sent):
        """Common mocked MT5 wiring. Records (req, ticket) into sent."""
        m.symbol_info.return_value = fake_info()
        m.symbol_info_tick.return_value = make_tick()
        m.positions_get.return_value = []
        m.orders_get.return_value = []
        counter = {"n": 1000}

        def send(req):
            counter["n"] += 1
            saved = dict(req)
            sent.append((saved, counter["n"]))
            return SimpleNamespace(retcode=10009, order=counter["n"], comment="done")

        m.order_send.side_effect = send
        return m

    def _start(self, m, sent, **kw):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        cfg_patch = {"running": True, "pre_trade_value": 3}
        cfg_patch.update(kw)
        bot.update_config(cfg_patch)
        bot.tick()
        return bot

    def test_ladder_places_n_buy_and_n_sell_with_gaps(self):
        sent = []
        with patch("backend.pretrade_bot.mt5") as m:
            self._mt5(m, sent)
            self._start(m, sent, price_gap=1.0, pre_trade_gap=0.5, lot=0.02)

            pendings = [(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_PENDING]
            self.assertEqual(len(pendings), 6)                      # 3 up + 3 down
            buys = [r for (r, t) in pendings if r["type"] == 4]
            sells = [r for (r, t) in pendings if r["type"] == 5]
            self.assertEqual(len(buys), 3)
            self.assertEqual(len(sells), 3)

            # Price Gap = market -> first level, Pre Trade Gap = between levels
            self.assertEqual([round(r["price"], 2) for r in buys], [4101.00, 4101.50, 4102.00])
            self.assertEqual([round(r["price"], 2) for r in sells], [4098.80, 4098.30, 4097.80])
            # same Lot Size on every pre-trade
            for r, t in pendings:
                self.assertEqual(r["volume"], 0.02)
                self.assertEqual(r["magic"], 777004)

        st = self.bot.status()
        self.assertEqual(len(st["ladder"]["buy"]), 3)
        self.assertEqual(len(st["ladder"]["sell"]), 3)
        self.assertEqual(st["pending_count"], 6)

    def test_numbering_lot_size_auto_numbers_both_ladders(self):
        sent = []
        with patch("backend.pretrade_bot.mt5") as m:
            self._mt5(m, sent)
            self._start(m, sent, numbering_lot=True, lot=0.05, pre_trade_value=4)

            pendings = [(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_PENDING]
            self.assertEqual(len(pendings), 8)
            volumes = sorted(r["volume"] for (r, t) in pendings)
            # both ladders numbered 0.01, 0.02, 0.03, 0.04
            self.assertEqual(volumes, [0.01, 0.01, 0.02, 0.02, 0.03, 0.03, 0.04, 0.04])

    def test_stop_cancels_every_pending_order(self):
        sent = []
        with patch("backend.pretrade_bot.mt5") as m:
            self._mt5(m, sent)
            bot = self._start(m, sent)
            placed = [(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_PENDING]
            m.orders_get.return_value = [
                pending(t, "buy" if r["type"] == 4 else "sell", r["price"], r["volume"])
                for (r, t) in placed
            ]

            bot.update_config({"running": False})
            bot.tick()

            removes = [(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_REMOVE]
            self.assertEqual(len(removes), len(placed))
            self.assertFalse(bot.cfg["running"])
            # no new ladder while stopped
            self.assertEqual(
                len([(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_PENDING]),
                len(placed),
            )

    # -------------------------------------------------------- pair rules
    def test_pair1_both_sides_may_stay_open(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        bot.cfg["running"] = True
        bot.cfg["pre_trade_value"] = 2
        bot._ladder = {
            "buy": [ladder_entry(1, 4101.0, ticket=21), ladder_entry(2, 4102.0, ticket=22)],
            "sell": [ladder_entry(1, 4098.8, ticket=23), ladder_entry(2, 4097.8, ticket=24)],
        }
        positions = [pos(111, "buy", 4101.0), pos(112, "sell", 4098.8)]
        orders = [pending(22, "buy", 4102.0), pending(24, "sell", 4097.8)]

        with patch("backend.pretrade_bot.mt5"), \
             patch.object(bot, "_close_position", return_value=True) as mock_close:
            bot._track(positions)
            bot._sync(positions, orders)

        mock_close.assert_not_called()
        self.assertEqual(bot._pos_meta[111]["level"], 1)
        self.assertEqual(bot._pos_meta[112]["level"], 1)
        # nothing locked yet - #2 levels are still waiting
        self.assertFalse(bot._pos_meta[111]["armed"])
        self.assertFalse(bot._pos_meta[112]["armed"])

    def test_pair2_hit_closes_opposite_and_arms_pair1_sl(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        bot.cfg["running"] = True
        bot.cfg["pre_trade_value"] = 2
        bot._ladder = {
            "buy": [ladder_entry(1, 4101.0, ticket=21), ladder_entry(2, 4102.0, ticket=22)],
            "sell": [ladder_entry(1, 4098.8, ticket=23), ladder_entry(2, 4097.8, ticket=24)],
        }
        # both #2 pre-trades got hit (whipsaw) -> the opposite #2 must close
        positions = [pos(111, "buy", 4101.0), pos(113, "buy", 4102.0),
                     pos(112, "sell", 4098.8), pos(114, "sell", 4097.8)]
        orders = []

        with patch("backend.pretrade_bot.mt5"), \
             patch.object(bot, "_close_position", return_value=True) as mock_close:
            bot._track(positions)
            bot._sync(positions, orders)

        # exactly one auto-close: the opposite side's #2 (pair #1 hedges untouched)
        self.assertEqual(mock_close.call_count, 1)
        closed_ticket = mock_close.call_args[0][0]["ticket"]
        self.assertEqual(closed_ticket, 113)
        # 1number SL armed when 2number hit (both sides)
        self.assertTrue(bot._pos_meta[111]["armed"])
        self.assertTrue(bot._pos_meta[112]["armed"])

    def test_lock_cascade_sets_sl_to_60_percent_of_profit(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        bot._pos_meta = {111: {"side": "buy", "level": 1, "price": 4101.0,
                               "lot": 0.01, "armed": True}}
        # entry 4101.0, market at 4102.0 -> net +1.00, mpp = 1.0
        positions = [pos(111, "buy", 4101.0, net=1.00, sl=0.0)]
        sent = []
        with patch("backend.pretrade_bot.mt5") as m:
            self._mt5(m, sent)
            m.symbol_info_tick.return_value = make_tick(ask=4102.2, bid=4102.0)
            bot._last_sl_send_ts = 0.0
            bot._apply_locks(positions)

        sltp = [(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_SLTP]
        self.assertEqual(len(sltp), 1)
        req = sltp[0][0]
        self.assertEqual(req["position"], 111)
        # 60% of $1.00 profit = $0.60 -> 0.60 / (1.0 per price) = 0.60 above entry
        self.assertAlmostEqual(req["sl"], 4101.60, places=2)

    def test_lock_never_moves_backward(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.symbol_ok = True
        bot._pos_meta = {111: {"side": "buy", "level": 1, "price": 4101.0,
                               "lot": 0.01, "armed": True}}
        # current SL already locks more than 60% of this profit -> no modify
        positions = [pos(111, "buy", 4101.0, net=1.00, sl=4101.90)]
        sent = []
        with patch("backend.pretrade_bot.mt5") as m:
            self._mt5(m, sent)
            m.symbol_info_tick.return_value = make_tick(ask=4102.2, bid=4102.0)
            bot._last_sl_send_ts = 0.0
            bot._apply_locks(positions)
        self.assertEqual([ (r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_SLTP ], [])

    # ------------------------------------------------- cycle / rebuild
    def test_rebuild_fresh_ladder_after_cycle_closes(self):
        sent = []
        with patch("backend.pretrade_bot.mt5") as m:
            self._mt5(m, sent)
            bot = self._start(m, sent, pre_trade_value=2, price_gap=1.0, pre_trade_gap=0.5)
            first = [(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_PENDING]
            self.assertEqual(len(first), 4)

            # tick 2: #1 of both sides filled, #2 still pending
            # #1 levels filled (positions exist), #2 levels still pending
            m.orders_get.return_value = [
                pending(t, "buy" if r["type"] == 4 else "sell", r["price"], r["volume"])
                for (r, t) in first if round(r["price"], 2) in (4101.50, 4098.30)
            ]
            m.positions_get.return_value = [
                pos(501, "buy", 4101.0), pos(502, "sell", 4098.8),
            ]
            bot.tick()
            self.assertTrue(bot._cycle_opened)

            # tick 3: every trade of the cycle closed -> leftovers cancelled,
            # fresh ladder from the new market price
            m.positions_get.return_value = []
            bot.tick()

            pendings = [(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_PENDING]
            removes = [(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_REMOVE]
            self.assertEqual(len(removes), 2)          # the two leftover #2 pre-trades
            self.assertEqual(len(pendings), 8)         # 4 first + 4 rebuilt
            self.assertFalse(bot._cycle_opened)
            self.assertEqual(len(bot._ladder["buy"]), 2)
            self.assertEqual(len(bot._ladder["sell"]), 2)

    def test_close_all_closes_positions_and_cancels_pendings(self):
        sent = []
        with patch("backend.pretrade_bot.mt5") as m:
            self._mt5(m, sent)
            bot = self._start(m, sent, pre_trade_value=2)
            placed = [(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_PENDING]
            m.orders_get.return_value = [
                pending(t, "buy" if r["type"] == 4 else "sell", r["price"], r["volume"])
                for (r, t) in placed
            ]
            m.positions_get.return_value = [pos(601, "buy", 4101.0)]

            closed = bot.close_all("dashboard")

            self.assertEqual(closed, 1)
            self.assertFalse(bot.cfg["running"])
            removes = [(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_REMOVE]
            self.assertEqual(len(removes), 4)     # every pre-trade cancelled
            deals = [(r, t) for (r, t) in sent if r["action"] == m.TRADE_ACTION_DEAL]
            self.assertEqual(len(deals), 1)

    def test_failed_placement_backs_off_with_one_log_line(self):
        """Market closed: single summary log + no retry storm on the next tick."""
        logs = []
        bot = PreTradeBot(logger=lambda k, m: logs.append((k, m)))
        sent = []
        with patch("backend.pretrade_bot.mt5") as m:
            self._mt5(m, sent)
            m.order_send.side_effect = lambda req: (sent.append((dict(req), 0)),
                                                     SimpleNamespace(retcode=10018, order=0,
                                                                     comment="market closed"))[1]
            bot.symbol = "XAUUSDm"
            bot.symbol_ok = True
            bot.update_config({"running": True, "pre_trade_value": 3})
            bot.tick()

            attempts = len(sent)
            self.assertGreater(attempts, 0)
            self.assertEqual(bot.status()["pending_count"], 0)
            # one summary line instead of one per pre-trade
            ladder_errs = [msg for (kind, msg) in logs if "Ladder not placed" in msg]
            self.assertEqual(len(ladder_errs), 1)
            self.assertEqual(len([msg for (k, msg) in logs if "rejected" in msg and "Ladder" not in msg]), 0)
            self.assertGreater(bot._retry_after, 0.0)

            # immediate next tick must NOT hammer the broker again
            bot.tick()
            self.assertEqual(len(sent), attempts)

    def test_status_exposes_ladder_view(self):
        sent = []
        with patch("backend.pretrade_bot.mt5") as m:
            self._mt5(m, sent)
            self._start(m, sent, pre_trade_value=3)
            st = self.bot.status()
        self.assertEqual(st["running"], True)
        self.assertEqual(st["ladder"]["value"], 3)
        self.assertEqual([e["level"] for e in st["ladder"]["buy"]], [1, 2, 3])
        self.assertEqual([e["level"] for e in st["ladder"]["sell"]], [1, 2, 3])
        self.assertTrue(all(e["state"] == "pending" for e in st["ladder"]["buy"]))
        self.assertEqual(st["pending_count"], 6)
        self.assertEqual(st["filled_count"], 0)
        self.assertEqual(st["running_count"], 0)
        self.assertIn("config", st)


if __name__ == "__main__":
    unittest.main()
