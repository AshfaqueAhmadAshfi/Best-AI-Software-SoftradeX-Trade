"""
Unit tests for NewsBot (Bot 5 - News Trader).

Runs as a plain script (no pytest needed):

    C:/Python312/python.exe tests/test_news_bot.py
"""
import datetime
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.news_bot import (DEFAULTS, NewsBot, _parse_event, currency_of_market,
                              split_symbol, compute_market_impacts, predict_dollar_move,
                              _importance_fallback, HEADLINE_SOURCES)
from backend import config as app_config


def fake_info(sym="XAUUSDm"):
    """Deterministic symbol_info under a mocked MT5."""
    return SimpleNamespace(
        name=sym, visible=True, trade_mode=True, trade_allowed=True,
        digits=2, point=0.01, volume_min=0.01, volume_max=100.0, volume_step=0.01,
        trade_stops_level=0, trade_freeze_level=0,
        trade_tick_value=1.0, trade_tick_size=0.01, trade_contract_size=100.0,
    )


def make_tick(ask=4100.0, bid=4099.8):
    return SimpleNamespace(ask=ask, bid=bid, time=1700000000)


def live_pos(ticket, side, price, net=0.0, volume=0.01, sl=0.0, tp=0.0, opened=None):
    return {
        "ticket": ticket, "symbol": "XAUUSDm", "magic": 777005,
        "type": 0 if side == "buy" else 1, "side": side,
        "volume": volume, "price_open": price, "price_current": price,
        "sl": sl, "tp": tp, "profit": net, "swap": 0.0, "net": round(net, 2),
        "time": int(opened or time.time()), "comment": "bot5_news",
    }


def pending_order(ticket, side, price, volume=0.01):
    return {
        "ticket": ticket, "symbol": "XAUUSDm", "magic": 777005, "state": 1,
        "type": 4 if side == "buy" else 5, "price_open": price,
        "volume_initial": volume, "volume_current": volume, "sl": 0.0, "tp": 0.0,
    }


def rates(rising=True):
    base = 4100.0
    out = []
    for i in range(6):
        close = base + (i * 1.5 if rising else -i * 1.5)
        out.append({"close": close, "open": base})
    return out


class TestNewsBot(unittest.TestCase):
    def setUp(self):
        self.bot = NewsBot()
        self.sent = []
        self.ticket = 9000

    # ------------------------------------------------------------- helpers
    def _mt5(self, m, positions=None, orders=None, rates_data=None, retcode=10009):
        m.TRADE_ACTION_DEAL = 1
        m.TRADE_ACTION_PENDING = 5
        m.TRADE_ACTION_REMOVE = 8
        m.TRADE_ACTION_SLTP = 7
        m.ORDER_TYPE_BUY = 0
        m.ORDER_TYPE_SELL = 1
        m.TIMEFRAME_M1 = 1
        m.ORDER_TIME_GTC = 0
        m.symbol_info.return_value = fake_info()
        m.symbol_info_tick.return_value = make_tick()
        m.symbol_select.return_value = True
        m.positions_get.return_value = positions or []
        m.orders_get.return_value = orders or []
        m.copy_rates_from_pos.return_value = rates_data if rates_data is not None else rates(True)

        def send(req):
            self.sent.append(dict(req))
            self.ticket += 1
            return SimpleNamespace(retcode=retcode, order=self.ticket - 1, deal=self.ticket - 1)

        m.order_send.side_effect = send

    def _manual(self, bot, seconds_from_now, title="Test Event", currency="USD",
                impact="high", forecast=100.0, previous=50.0, pre=5.0, post=15.0):
        now = time.time()
        bot.update_config({
            "manual_events": [{
                "title": title, "currency": currency, "ts": now + seconds_from_now,
                "impact": impact, "forecast": forecast, "previous": previous,
            }],
            "pre_minutes": pre, "post_minutes": post,
        })
        return now

    # -------------------------------------------------------------- config
    def test_default_config(self):
        st = self.bot.status()
        c = st["config"]
        self.assertFalse(st["running"])
        self.assertEqual(c["lot"], 0.01)
        self.assertEqual(c["magic"], 777005)
        self.assertEqual(c["mode"], "anticipate")
        self.assertEqual(c["min_impact"], "high")
        self.assertEqual(c["pre_minutes"], 5.0)
        self.assertEqual(c["post_minutes"], 15.0)
        self.assertEqual(c["tp"], 3.0)
        self.assertEqual(c["sl"], 3.0)
        self.assertEqual(st["open"], 0)
        self.assertEqual(st["positions"], [])

    def test_update_config_sanitize(self):
        out = self.bot.update_config({
            "lot": 0.05, "mode": "STRADDLE", "min_impact": "MEDIUM",
            "pre_minutes": 3, "post_minutes": 20, "tp": 5, "sl": 4,
            "currencies": "usd, eur", "bias_min_score": 2,
        })
        self.assertEqual(out["lot"], 0.05)
        self.assertEqual(out["mode"], "straddle")
        self.assertEqual(out["min_impact"], "medium")
        self.assertEqual(out["pre_minutes"], 3.0)
        self.assertEqual(out["post_minutes"], 20.0)
        self.assertEqual(out["currencies"], "USD,EUR")
        self.assertEqual(out["bias_min_score"], 2)
        # garbage / out-of-range falls back safely
        self.assertEqual(self.bot.update_config({"mode": "nonsense"})["mode"], "anticipate")
        self.assertEqual(self.bot.update_config({"min_impact": "x"})["min_impact"], "high")
        self.assertEqual(self.bot.update_config({"pre_minutes": 9999})["pre_minutes"], 240.0)
        self.assertEqual(self.bot.update_config({"tp": -5})["tp"], 0.0)
        self.assertEqual(self.bot.update_config({"max_trades": 0})["max_trades"], 1)
        self.assertEqual(self.bot.update_config({"bias_min_score": 99})["bias_min_score"], 5)

    def test_config_module_has_matching_bot5_defaults(self):
        self.assertIn("bot5", app_config.DEFAULTS)
        b5 = app_config.DEFAULTS["bot5"]
        for key, value in DEFAULTS.items():
            self.assertIn(key, b5, f"config.py bot5 is missing '{key}'")
            self.assertEqual(type(b5[key]), type(value), f"type mismatch for '{key}'")
            self.assertEqual(b5[key], value, f"default mismatch for '{key}'")
        self.assertEqual(b5["magic"], 777005)

    # ------------------------------------------------------------- symbols
    def test_symbol_currency_resolution(self):
        self.assertEqual(split_symbol("XAUUSDm"), ("XAU", "USD"))
        self.assertEqual(split_symbol("BTCUSDTm"), ("BTC", "USDT"))
        self.assertEqual(currency_of_market("XAUUSDm"), "USD")
        self.assertEqual(currency_of_market("EURUSDm"), "EUR")
        self.assertEqual(currency_of_market("XAUGBPm"), "GBP")
        self.assertEqual(currency_of_market("USDJPYm"), "USD")
        self.assertEqual(currency_of_market("USOILm"), "USD")
        self.assertEqual(currency_of_market("USTECm"), "USD")

        b = NewsBot()
        b.symbol = "XAUUSDm"
        self.assertEqual(b._currencies(), ["USD"])
        b.symbol = "EURUSDm"
        self.assertEqual(b._currencies(), ["EUR", "USD"])
        b.symbol = "XAUGBPm"
        self.assertEqual(b._currencies(), ["GBP"])

    # ---------------------------------------------------------------- feed
    def test_parse_event_normalises_and_skips_junk(self):
        ev = _parse_event({"title": "CPI", "country": "usd",
                           "date": "2026-10-06T08:30:00-04:00",
                           "impact": "High", "forecast": "2.5%", "previous": "2.1%"})
        self.assertIsNotNone(ev)
        self.assertEqual(ev["country"], "USD")
        self.assertEqual(ev["impact"], "high")
        self.assertEqual(ev["forecast"], 2.5)
        self.assertEqual(ev["previous"], 2.1)
        self.assertIsNone(_parse_event({"title": "", "date": "2026-10-06T08:30:00Z"}))
        self.assertIsNone(_parse_event({"title": "Holiday", "date": "2026-10-06T08:30:00Z",
                                        "impact": "Holiday"}))
        # 1.2K / 3M suffixes
        big = _parse_event({"title": "NFP", "country": "USD", "date": "2026-10-06T08:30:00Z",
                            "impact": "High", "forecast": "180K", "previous": "1.5M"})
        self.assertEqual(big["forecast"], 180000.0)
        self.assertEqual(big["previous"], 1500000.0)

    def test_manual_events_are_merged_and_filtered(self):
        now = self._manual(self.bot, 600)
        self.bot.symbol = "XAUUSDm"
        evs = self.bot._snapshot_events(now)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["title"], "Test Event")
        self.assertTrue(evs[0].get("manual"))
        # stale events are dropped
        self.bot.update_config({"manual_events": [
            {"title": "Old", "currency": "USD", "ts": now - 99999, "impact": "high"}]})
        self.assertEqual(self.bot._snapshot_events(now), [])

    def test_snapshot_unfiltered_returns_every_event(self):
        """The dashboard list must show ALL news, not only the tradeable ones."""
        now = time.time()
        self.bot.symbol = "XAUUSDm"
        self.bot.update_config({"min_impact": "high", "manual_events": [
            {"title": "US High", "currency": "USD", "ts": now + 600, "impact": "high"},
            {"title": "US Low", "currency": "USD", "ts": now + 700, "impact": "low"},
            {"title": "EU High", "currency": "EUR", "ts": now + 800, "impact": "high"},
            {"title": "ZA Medium", "currency": "ZAR", "ts": now + 900, "impact": "medium"},
        ]})
        filtered = [e["title"] for e in self.bot._snapshot_events(now)]
        self.assertEqual(filtered, ["US High"])
        unfiltered = [e["title"] for e in self.bot._snapshot_events(now, unfiltered=True)]
        self.assertEqual(unfiltered, ["US High", "US Low", "EU High", "ZA Medium"])
        # an expired event is gone from both views
        self.bot.update_config({"manual_events": [
            {"title": "Expired", "currency": "USD", "ts": now - 60, "impact": "high"}]})
        self.assertEqual(self.bot._snapshot_events(now, unfiltered=True), [])

    def test_status_lists_all_news_with_qualified_flag(self):
        now = time.time()
        bot = self.bot
        bot.symbol = "XAUUSDm"
        bot.update_config({"min_impact": "high", "manual_events": [
            {"title": "US High", "currency": "USD", "ts": now + 600, "impact": "high"},
            {"title": "EU Low", "currency": "EUR", "ts": now + 700, "impact": "low"},
        ]})
        st = bot.status()
        titles = [n["title"] for n in st["news"]]
        self.assertIn("US High", titles)
        self.assertIn("EU Low", titles)          # every currency/impact is listed
        self.assertEqual(st["news_total"], 2)
        by_title = {n["title"]: n for n in st["news"]}
        self.assertTrue(by_title["US High"]["qualified"])
        self.assertFalse(by_title["EU Low"]["qualified"])   # shown, but not tradeable

    def test_importance_fallback_tiers(self):
        """TV's importance=-1 rows must still tier from the title so HIGH news
        never flattens into the low-impact stream."""
        self.assertEqual(_importance_fallback("Core CPI m/m"), "high")
        self.assertEqual(_importance_fallback("Interest Rate Decision"), "high")
        self.assertEqual(_importance_fallback("Non-Farm Employment Change"), "high")
        self.assertEqual(_importance_fallback("Crude Oil Inventories"), "high")
        self.assertEqual(_importance_fallback("Retail Sales MoM"), "high")
        self.assertEqual(_importance_fallback("182-Day T-Bill Auction"), "low")
        self.assertEqual(_importance_fallback("ECB Lane Speech"), "low")
        self.assertEqual(_importance_fallback("Liberation Day"), "low")
        self.assertEqual(_importance_fallback("National Day of Prayer"), "low")
        self.assertEqual(_importance_fallback("Michigan 5 Year Inflation Expectations Final"), "medium")
        self.assertEqual(_importance_fallback("ZEW Current Conditions"), "medium")
        self.assertEqual(_importance_fallback("EIA Distillate Stocks Change"), "medium")
        self.assertEqual(_importance_fallback("GDP Growth Rate YoY"), "medium")
        self.assertEqual(_importance_fallback("Tertiary Industry Index MoM"), "low")
        self.assertIn("forex-factory", HEADLINE_SOURCES)   # FF releases show with effects

    def test_status_shows_effects_for_watch_only_rows(self):
        """Low/medium/high rows read alike: every row carries its effect map."""
        bot = self.bot
        bot.symbol = "XAUUSDm"
        now = time.time()
        bot.update_config({"min_impact": "high", "manual_events": [
            {"title": "EU Low Watch", "currency": "EUR", "ts": now + 700, "impact": "low"},
        ]})
        st = bot.status()
        row = next(n for n in st["news"] if n["title"] == "EU Low Watch")
        self.assertFalse(row["qualified"])                 # not tradeable, still listed
        self.assertTrue(row["impacted_markets"])           # effect map on display rows
        self.assertTrue(any(m.get("side") for m in row["impacted_markets"]))
        self.assertGreater(row.get("impacted_total", 0), 0)
        self.assertTrue(row.get("predicted_move"))         # move for it too

    def test_status_keeps_high_news_when_low_stream_floods(self):
        """450 low rows must not push the HIGH events out of the 400-row list."""
        bot = self.bot
        bot.symbol = "XAUUSDm"
        now = time.time()
        manual = [{"title": "50-Year Bond Auction %d" % i, "currency": "USD",
                   "ts": now + 600 + i * 60, "impact": "low"} for i in range(450)]
        manual.append({"title": "US CPI Flash", "currency": "USD", "ts": now + 99999, "impact": "high"})
        manual.append({"title": "EU PMI Revision", "currency": "EUR", "ts": now + 1000, "impact": "medium"})
        bot.update_config({"manual_events": manual})
        st = bot.status()
        titles = [n["title"] for n in st["news"]]
        self.assertEqual(st["news_total"], 452)
        self.assertIn("US CPI Flash", titles)
        self.assertIn("EU PMI Revision", titles)
        self.assertEqual(len(st["news"]), 400)             # cap still holds
        self.assertEqual(titles[0], "50-Year Bond Auction 0")  # chronological order kept

    def test_status_exposes_market_price_and_symbol_options(self):
        bot = self.bot
        bot.symbol, bot.symbol_ok = "XAUUSDm", True
        st = bot.status()
        self.assertIn("ask", st)
        self.assertIn("bid", st)
        self.assertIn("spread", st)
        self.assertIn("digits", st)
        opts = st["symbol_options"]
        self.assertTrue(isinstance(opts, list) and opts)
        self.assertIn("XAUUSDm", opts)           # the whole All-Market set is offered
        self.assertIn("BTCUSDTm", opts)

    def test_impact_and_currency_filters(self):
        now = time.time()
        self.bot.symbol = "XAUUSDm"
        self.bot.update_config({"min_impact": "high", "manual_events": [
            {"title": "US High", "currency": "USD", "ts": now + 600, "impact": "high"},
            {"title": "US Low", "currency": "USD", "ts": now + 700, "impact": "low"},
            {"title": "EU High", "currency": "EUR", "ts": now + 800, "impact": "high"},
        ]})
        titles = [e["title"] for e in self.bot._snapshot_events(now)]
        self.assertEqual(titles, ["US High"])
        # 'medium' means medium-or-higher, so the low row stays out
        self.bot.update_config({"min_impact": "medium"})
        titles = [e["title"] for e in self.bot._snapshot_events(now)]
        self.assertEqual(titles, ["US High"])
        # 'all' opens the low rows up as well (EUR still filtered by currency)
        self.bot.update_config({"min_impact": "all"})
        titles = [e["title"] for e in self.bot._snapshot_events(now)]
        self.assertEqual(titles, ["US High", "US Low"])
        # explicit currencies widen it to EUR too
        self.bot.update_config({"currencies": "USD,EUR"})
        titles = [e["title"] for e in self.bot._snapshot_events(now)]
        self.assertEqual(titles, ["US High", "US Low", "EU High"])
        # a holiday row is never tradeable
        self.bot.update_config({"manual_events": [
            {"title": "Holiday", "currency": "USD", "ts": now + 100, "impact": "holiday"}]})
        self.assertEqual(self.bot._snapshot_events(now), [])

    # ---------------------------------------------------------------- bias
    def test_forecast_bias_direction(self):
        self.bot.symbol = "XAUUSDm"
        # USD forecast > previous -> USD strengthens -> gold goes DOWN -> SELL
        worse = {"country": "USD", "forecast": 200.0, "previous": 100.0}
        self.assertEqual(self.bot._forecast_bias(worse)[0], -1)
        # USD forecast < previous -> USD weakens -> gold UP -> BUY
        better = {"country": "USD", "forecast": 50.0, "previous": 100.0}
        self.assertEqual(self.bot._forecast_bias(better)[0], 1)
        # no change -> no signal
        flat = {"country": "USD", "forecast": 100.0, "previous": 100.0}
        self.assertEqual(self.bot._forecast_bias(flat)[0], 0)
        # unrelated currency -> no signal
        other = {"country": "JPY", "forecast": 5.0, "previous": 1.0}
        self.assertEqual(self.bot._forecast_bias(other)[0], 0)

    def test_decide_bias_is_readable_and_confident(self):
        self.bot.symbol = "XAUUSDm"
        ev = {"title": "NFP", "country": "USD", "ts": time.time() + 600,
              "impact": "high", "forecast": 200.0, "previous": 100.0}
        d = self.bot.decide_bias(ev)
        self.assertEqual(d["side"], "sell")
        self.assertTrue(d["confident"])
        self.assertLess(d["score"], 0)
        self.assertIn("USD", d["reason"])
        self.assertIn("NFP", d["headline"])

    def test_decide_bias_unknown_is_not_confident(self):
        # A real MT5 terminal must not leak into this test - otherwise a live
        # falling market adds momentum and |score| crosses bias_min_score.
        with patch("backend.news_bot.mt5", None):
            self.bot.symbol = "XAUUSDm"
            self.bot.update_config({"bias_min_score": 3})
            ev = {"title": "X", "country": "USD", "ts": time.time() + 60,
                  "impact": "high", "forecast": 200.0, "previous": 100.0}
            d = self.bot.decide_bias(ev)
            self.assertEqual(d["side"], "sell")
            self.assertFalse(d["confident"])       # |score| 2 < 3

    # ------------------------------------------------------------- trading
    def test_anticipate_opens_once_in_the_entry_window(self):
        with patch("backend.news_bot.mt5") as m:
            self._mt5(m)
            bot = self.bot
            bot.symbol, bot.symbol_ok = "XAUUSDm", True
            now = time.time()
            bot.update_config({
                "running": True, "mode": "anticipate", "pre_minutes": 5, "post_minutes": 15,
                "cooldown_sec": 0, "manual_events": [{
                    "title": "NFP", "currency": "USD", "ts": now + 120, "impact": "high",
                    "forecast": 200, "previous": 100}],
            })
            # momentum rises against the news signal: the forecast must still win
            m.copy_rates_from_pos.return_value = rates(True)
            bot.tick()
            deals = [r for r in self.sent if r["action"] == m.TRADE_ACTION_DEAL]
            self.assertEqual(len(deals), 1)
            self.assertEqual(deals[0]["type"], m.ORDER_TYPE_SELL)   # USD strong -> gold sell
            self.assertEqual(deals[0]["magic"], 777005)
            self.assertGreater(deals[0]["sl"], 0.0)
            self.assertGreater(deals[0]["tp"], 0.0)
            self.assertGreater(bot._exit_at, 0.0)

            # a second tick must NOT double-enter the same event
            m.positions_get.return_value = [live_pos(9001, "sell", 4099.0)]
            bot.tick()
            deals = [r for r in self.sent if r["action"] == m.TRADE_ACTION_DEAL]
            self.assertEqual(len(deals), 1)

    def test_anticipate_returns_before_the_window(self):
        with patch("backend.news_bot.mt5") as m:
            self._mt5(m)
            bot = self.bot
            bot.symbol, bot.symbol_ok = "XAUUSDm", True
            now = time.time()
            bot.update_config({
                "running": True, "mode": "anticipate", "pre_minutes": 5, "cooldown_sec": 0,
                "manual_events": [{
                    "title": "NFP", "currency": "USD", "ts": now + 3600, "impact": "high",
                    "forecast": 200, "previous": 100}],
            })
            bot.tick()
            self.assertEqual([r for r in self.sent if r["action"] == m.TRADE_ACTION_DEAL], [])
            self.assertIn("NFP", bot.status()["plan"])   # a plan is still reported

    def test_straddle_parks_both_stops(self):
        with patch("backend.news_bot.mt5") as m:
            self._mt5(m)
            bot = self.bot
            bot.symbol, bot.symbol_ok = "XAUUSDm", True
            now = time.time()
            bot.update_config({
                "running": True, "mode": "straddle", "pre_minutes": 5, "straddle_gap": 3.0,
                "cooldown_sec": 0, "manual_events": [{
                    "title": "CPI", "currency": "USD", "ts": now + 120, "impact": "high"}],
            })
            bot.tick()
            pend = [r for r in self.sent if r["action"] == m.TRADE_ACTION_PENDING]
            self.assertEqual(len(pend), 2)
            types = sorted(r["type"] for r in pend)
            self.assertEqual(types, [4, 5])                       # buy stop above, sell stop below
            self.assertGreater(pend[0]["price"], 4100.0)
            self.assertLess(min(r["price"] for r in pend), 4100.0)
            st = bot.status()
            self.assertTrue(st["straddle"])

    def test_straddle_cancels_the_untouched_side_after_a_fill(self):
        with patch("backend.news_bot.mt5") as m:
            self._mt5(m)
            bot = self.bot
            bot.symbol, bot.symbol_ok = "XAUUSDm", True
            now = time.time()
            bot.update_config({
                "running": True, "mode": "straddle", "pre_minutes": 5, "cooldown_sec": 0,
                "manual_events": [{"title": "CPI", "currency": "USD", "ts": now + 120,
                                   "impact": "high"}],
            })
            bot.tick()
            placed = [r for r in self.sent if r["action"] == m.TRADE_ACTION_PENDING]
            buy, sell = placed[0], placed[1]
            # the buy stop filled -> it disappears from orders, the sell stop stays
            m.orders_get.return_value = [pending_order(self.ticket, "sell", sell["price"])]
            m.positions_get.return_value = [live_pos(9100, "buy", buy["price"])]
            bot.tick()
            removes = [r for r in self.sent if r["action"] == m.TRADE_ACTION_REMOVE]
            self.assertEqual(len(removes), 1)
            self.assertEqual(bot.status()["straddle"], {})
            self.assertGreater(bot._exit_at, 0.0)

    def test_follow_mode_trades_the_reaction(self):
        with patch("backend.news_bot.mt5") as m:
            self._mt5(m, rates_data=rates(rising=True))
            bot = self.bot
            bot.symbol, bot.symbol_ok = "XAUUSDm", True
            now = time.time()
            bot.update_config({
                "running": True, "mode": "follow", "post_minutes": 15, "cooldown_sec": 0,
                "manual_events": [{"title": "CPI", "currency": "USD", "ts": now - 60,
                                   "impact": "high"}],
            })
            bot.tick()
            deals = [r for r in self.sent if r["action"] == m.TRADE_ACTION_DEAL]
            self.assertEqual(len(deals), 1)
            self.assertEqual(deals[0]["type"], m.ORDER_TYPE_BUY)   # rising M1 momentum

    def test_position_is_closed_after_the_news_window(self):
        with patch("backend.news_bot.mt5") as m:
            self._mt5(m)
            bot = self.bot
            bot.symbol, bot.symbol_ok = "XAUUSDm", True
            now = time.time()
            bot.update_config({
                "running": True, "mode": "anticipate", "pre_minutes": 5, "post_minutes": 15,
                "cooldown_sec": 0, "auto_close_after_news": True,
                "manual_events": [{"title": "NFP", "currency": "USD", "ts": now + 120,
                                   "impact": "high", "forecast": 200, "previous": 100}],
            })
            m.positions_get.return_value = [live_pos(9001, "sell", 4099.0)]
            bot.tick()
            bot._exit_at = now - 1          # the news window is over
            bot.tick()
            closes = [r for r in self.sent if r["action"] == m.TRADE_ACTION_DEAL
                      and r.get("position") == 9001]
            self.assertEqual(len(closes), 1)
            self.assertEqual(bot._exit_at, 0.0)

    def test_close_all_closes_positions_and_cancels_stops(self):
        with patch("backend.news_bot.mt5") as m:
            self._mt5(m)
            bot = self.bot
            bot.symbol, bot.symbol_ok = "XAUUSDm", True
            m.positions_get.return_value = [live_pos(9001, "buy", 4100.0)]
            m.orders_get.return_value = [pending_order(7001, "buy", 4110.0),
                                         pending_order(7002, "sell", 4090.0)]
            closed = bot.close_all("dashboard")
            self.assertEqual(closed, 1)
            removes = [r for r in self.sent if r["action"] == m.TRADE_ACTION_REMOVE]
            self.assertEqual(len(removes), 2)
            self.assertEqual(bot._straddle, {})
            self.assertEqual(bot._exit_at, 0.0)

    def test_rejected_order_is_reported_not_crashed(self):
        logs = []
        bot = NewsBot(logger=lambda k, msg: logs.append((k, msg)))
        bot.symbol, bot.symbol_ok = "XAUUSDm", True
        with patch("backend.news_bot.mt5") as m:
            self._mt5(m, retcode=10016)          # invalid stops
            now = time.time()
            bot.update_config({
                "running": True, "cooldown_sec": 0,
                "manual_events": [{"title": "NFP", "currency": "USD", "ts": now + 120,
                                   "impact": "high", "forecast": 200, "previous": 100}],
            })
            bot.tick()
            self.assertEqual(bot._exit_at, 0.0)
            self.assertIn("rejected", bot.last_error.lower())
            self.assertEqual(bot._last_event_key, "")   # free to retry
            self.assertTrue(any("rejected" in msg.lower() for _k, msg in logs))

    # -------------------------------------------------------------- status
    def test_status_exposes_news_and_bias(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        now = self._manual(bot, 600)
        st = bot.status()
        self.assertEqual(st["symbol"], "XAUUSDm")
        self.assertIn(st["mode"], ("anticipate", "straddle", "follow"))
        self.assertEqual(len(st["news"]), 1)
        self.assertEqual(st["news"][0]["title"], "Test Event")
        self.assertEqual(st["news"][0]["side"], "sell")
        self.assertEqual(st["active_event"]["title"], "Test Event")
        self.assertAlmostEqual(st["active_event"]["minutes_to"], 10.0, delta=0.2)
        self.assertEqual(st["currencies"], ["USD"])
        self.assertIn("plan", st)
        self.assertIn("stats", st)

    def test_stats_track_a_closed_position(self):
        bot = self.bot
        bot.symbol, bot.symbol_ok = "XAUUSDm", True
        with patch("backend.news_bot.mt5") as m:
            self._mt5(m)
            m.positions_get.return_value = [live_pos(9001, "buy", 4100.0, net=4.25)]
            bot.tick()                      # learns the position
            m.positions_get.return_value = []
            bot.tick()                      # sees it gone -> books the result
        self.assertEqual(bot.stats["closed"], 1)
        self.assertEqual(bot.stats["wins"], 1)
        self.assertAlmostEqual(bot.stats["realized"], 4.25, places=2)


    def test_compute_market_impacts(self):
        # USD Bullish -> Gold SELL, DXY BUY, USDJPY BUY, EURUSD SELL, BTC SELL
        usd_bull = {"title": "ISM Services PMI", "country": "USD", "forecast": 55.7, "previous": 55.4}
        impacts = {item["market"]: item["side"] for item in compute_market_impacts(usd_bull)}
        self.assertEqual(impacts.get("XAUUSDm"), "SELL")
        self.assertEqual(impacts.get("DXYm"), "BUY")
        self.assertEqual(impacts.get("USDJPYm"), "BUY")
        self.assertEqual(impacts.get("EURUSDm"), "SELL")
        self.assertEqual(impacts.get("BTCUSDm"), "SELL")
        self.assertEqual(impacts.get("USTECm"), "SELL")
        self.assertEqual(impacts.get("USOILm"), "SELL")

        # USD Bearish (Jobless claims rising) -> Gold BUY, DXY SELL
        usd_bear = {"title": "Initial Jobless Claims", "country": "USD", "forecast": 250000.0, "previous": 220000.0}
        impacts_bear = {item["market"]: item["side"] for item in compute_market_impacts(usd_bear)}
        self.assertEqual(impacts_bear.get("XAUUSDm"), "BUY")
        self.assertEqual(impacts_bear.get("DXYm"), "SELL")
        self.assertEqual(impacts_bear.get("EURUSDm"), "BUY")

        # EUR Bullish -> EURUSD BUY, DXY SELL
        eur_bull = {"title": "ECB Interest Rate Decision", "country": "EUR", "forecast": 4.0, "previous": 3.75}
        impacts_eur = {item["market"]: item["side"] for item in compute_market_impacts(eur_bull)}
        self.assertEqual(impacts_eur.get("EURUSDm"), "BUY")
        self.assertEqual(impacts_eur.get("DXYm"), "SELL")

        # JPY Bullish (Yen strengthens) -> USDJPY SELL
        jpy_bull = {"title": "BOJ Rate Decision", "country": "JPY", "forecast": 0.5, "previous": 0.25}
        impacts_jpy = {item["market"]: item["side"] for item in compute_market_impacts(jpy_bull)}
        self.assertEqual(impacts_jpy.get("USDJPYm"), "SELL")


    # -------------------------------------------------------- news sources
    def test_headline_currency_and_impact(self):
        from backend.news_bot import _headline_currency, _headline_impact
        self.assertEqual(_headline_currency("Gold price jumps as the dollar slips"), "XAU")
        self.assertEqual(_headline_currency("Bitcoin rallies past 70k"), "BTC")
        self.assertEqual(_headline_currency("Nasdaq futures drop on Fed fears"), "TECH")
        self.assertEqual(_headline_currency("Euro slides to a 17-month low"), "EUR")
        self.assertEqual(_headline_currency("Oil steady ahead of OPEC meeting"), "OIL")
        self.assertEqual(_headline_currency("US dollar closes lower on Taipei forex market"), "USD")
        self.assertEqual(_headline_impact("Fed holds rates, CPI in focus"), "high")
        self.assertEqual(_headline_impact("Gold price drifts sideways"), "medium")

    def test_google_news_feed_is_parsed(self):
        from backend.news_bot import NewsFeed
        # Dates are built from "now": a fixed calendar date would silently
        # expire once the parser's 3-day freshness window passes it.
        from email.utils import formatdate
        now = time.time()
        d1 = formatdate(now - 3600, usegmt=True)
        d2 = formatdate(now - 3000, usegmt=True)
        xml = ("<?xml version=\"1.0\"?>\n<rss version=\"2.0\"><channel>\n"
               "<item><title>Gold jumps as Fed signals a rate cut</title>"
               f"<pubDate>{d1}</pubDate>"
               "<source url=\"https://www.reuters.com\">Reuters</source></item>"
               "<item><title>Euro slides to a 17-month low</title>"
               f"<pubDate>{d2}</pubDate></item>"
               "<item><title>Item without a date</title></item>\n"
               "</channel></rss>").encode("utf-8")
        rows = NewsFeed()._parse_headline_rss(xml, "google-news")
        self.assertEqual(len(rows), 2)                       # undated item dropped
        self.assertEqual(rows[0]["country"], "XAU")
        self.assertEqual(rows[0]["impact"], "high")
        self.assertEqual(rows[0]["publisher"], "Reuters")
        self.assertEqual(rows[0]["source"], "google-news")
        self.assertEqual(rows[1]["country"], "EUR")

    def test_fetch_once_reads_google_and_counts_every_site(self):
        from backend.news_bot import NewsFeed
        feed = NewsFeed()
        now = time.time()
        tv = [{"title": "CPI", "country": "USD", "ts": now + 3600, "impact": "high",
               "forecast": None, "previous": None, "actual": None, "source": "tradingview"}]
        g = [{"title": "Gold jumps", "country": "XAU", "ts": now + 7200, "impact": "medium",
              "forecast": None, "previous": None, "actual": None, "source": "google-news"}]
        with patch.object(feed, "_fetch_tradingview", return_value=tv), \
                patch.object(feed, "_fetch_forexfactory", return_value=[]), \
                patch.object(feed, "_fetch_investing_rss", return_value=[]), \
                patch.object(feed, "_fetch_yahoo_rss", return_value=[]), \
                patch.object(feed, "_fetch_google_news", return_value=g):
            n = feed.fetch_once()
        self.assertEqual(n, 2)
        self.assertEqual(feed.source_counts.get("google-news"), 1)
        self.assertEqual(feed.source_counts.get("tradingview"), 1)
        self.assertIn("google-news", feed.source)

    def test_status_exposes_source_counts_and_market_outlook(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        # the outlook board always reads the NEXT day (18th shows the 19th):
        # put the event at tomorrow noon so the verdict is deterministic
        # whatever hour the suite runs at
        tomorrow_noon = (datetime.datetime.now()
                         .replace(hour=12, minute=0, second=0, microsecond=0)
                         + datetime.timedelta(days=1))
        bot.update_config({"manual_events": [
            {"title": "Fed Rate Decision", "currency": "USD", "ts": tomorrow_noon.timestamp(),
             "impact": "high", "forecast": 5.0, "previous": 4.5},
        ]})
        bot.feed.source = "tradingview+google-news"
        bot.feed.source_counts = {"tradingview": 10, "google-news": 4}
        st = bot.status()
        counts = {s["name"]: s["count"] for s in st["feed_sources"]}
        self.assertEqual(counts.get("google-news"), 4)
        self.assertEqual(st["news"][0]["source"], "calendar")   # manual event, no site
        verdicts = {o["market"]: o["verdict"] for o in st["outlook"]}
        self.assertEqual(verdicts.get("XAUUSDm"), "SELL")       # strong USD -> gold offered
        self.assertEqual(verdicts.get("DXYm"), "BUY")
        gold = next(o for o in st["outlook"] if o["market"] == "XAUUSDm")
        self.assertEqual(gold["signals"], 1)
        self.assertIn("Fed Rate Decision", gold["top_event"])

    def test_status_keeps_published_headlines_with_expiry(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        now = time.time()
        bot.feed.events = [
            {"title": "Gold jumps as Fed signals a cut", "country": "XAU", "ts": now - 2 * 3600,
             "impact": "high", "forecast": None, "previous": None, "actual": None,
             "source": "google-news", "publisher": "Reuters"},
            {"title": "Old headline outside the window", "country": "XAU", "ts": now - 40 * 3600,
             "impact": "medium", "forecast": None, "previous": None, "actual": None,
             "source": "google-news"},
        ]
        st = bot.status()
        rows = [r for r in st["news"] if r.get("past")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "google-news")
        self.assertEqual(rows[0]["publisher"], "Reuters")
        self.assertGreater(rows[0]["expire_ts"], now)      # still inside the 12h window
        self.assertTrue(rows[0]["impacted_markets"])       # direction is shown for it
        titles = [r["title"] for r in st["news"]]
        self.assertNotIn("Old headline outside the window", titles)

    def test_predict_dollar_move_gold(self):
        cpi = {"title": "CPI m/m", "country": "USD", "impact": "high", "forecast": 0.4, "previous": 0.2}
        pred_buy = predict_dollar_move(cpi, market="XAUUSDm", side="BUY")
        self.assertEqual(pred_buy["direction"], "UP")
        self.assertGreater(pred_buy["move_dollar"], 15.0)
        self.assertTrue(pred_buy["text_en"].endswith("UP"))
        self.assertIn("উপরে", pred_buy["text_bn"])
        self.assertIn("$", pred_buy["val_str"])

        pred_sell = predict_dollar_move(cpi, market="XAUUSDm", side="SELL")
        self.assertEqual(pred_sell["direction"], "DOWN")
        self.assertLess(pred_sell["move_dollar"], -15.0)
        self.assertTrue(pred_sell["text_en"].endswith("DOWN"))
        self.assertIn("নিচে", pred_sell["text_bn"])

    def test_predict_dollar_move_btc_oil(self):
        fed = {"title": "FOMC Statement & Rate Decision", "country": "USD", "impact": "high"}
        pred_btc = predict_dollar_move(fed, market="BTCUSDm", side="BUY")
        self.assertEqual(pred_btc["direction"], "UP")
        self.assertGreater(pred_btc["move_dollar"], 500.0)

        pred_oil = predict_dollar_move({"title": "Crude Oil Inventories", "country": "USD", "impact": "high"},
                                       market="USOILm", side="SELL")
        self.assertEqual(pred_oil["direction"], "DOWN")
        self.assertLess(pred_oil["move_dollar"], -0.5)
        self.assertIn("নিচে", pred_oil["text_bn"])

    def test_status_exposes_predicted_moves(self):
        bot = self.bot
        bot.symbol = "XAUUSDm"
        self._manual(bot, 600, title="Non-Farm Employment Change", forecast=220.0, previous=150.0)
        st = bot.status()
        self.assertTrue(st["news"])
        row = st["news"][0]
        self.assertIn("predicted_move", row)
        self.assertTrue(row["predicted_move"]["text_en"])
        self.assertTrue(row["predicted_move"]["text_bn"])
        # Impacted markets carry individual dollar predictions
        self.assertTrue(row["impacted_markets"])
        m0 = row["impacted_markets"][0]
        self.assertIn("move_text_en", m0)
        self.assertIn("move_text_bn", m0)
        # Active next event also exposes predicted move
        self.assertIn("predicted_move", st["active_event"])
        self.assertTrue(st["active_event"]["predicted_move"]["text_en"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
