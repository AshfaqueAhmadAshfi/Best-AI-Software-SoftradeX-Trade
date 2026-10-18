"""
Unit tests for the full AI market analysis board (5m / 10m / 15m / 1h).

Runs as a plain script (no pytest needed):

    python tests/test_market_ai.py

What is covered
---------------
* the OHLC plumbing: parsing every row shape, sorting, and dropping the candle
  that is still forming (no repainting)
* the four readings of one timeframe (EMA trend, momentum vs ATR, swing break,
  RSI) and the BUY / SELL / NEUTRAL thresholds
* the price targets: the ATR travel up / down, the swing levels and the money
  conversion for the lot
* the cross-timeframe vote: the 1h timeframe on its own can call a side, a lone
  fast timeframe cannot, conflicts land on NEUTRAL / low confidence
* the NewsBot integration (candles cache, live price, payload, allow-lists)
"""
import json
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import market_ai as A                     # noqa: E402
from backend.news_bot import NewsBot                   # noqa: E402

NOW = 1_800_000_000.0


# ------------------------------------------------------------------- builders
def bar(ts, o, h, l, c):
    return {"time": ts, "open": o, "high": h, "low": l, "close": c}


def series(step=1.0, n=140, start=4000.0, minutes=15, vol=0.4, now=NOW):
    """A straight ramp: +step per candle (negative step = falling market)."""
    out = []
    price = start
    for i in range(n):
        ts = now - (n - i) * minutes * 60
        o, c = price, price + step
        out.append(bar(ts, o, max(o, c) + vol, min(o, c) - vol, c))
        price = c
    return out


def flat(n=140, price=4000.0, minutes=15, vol=0.2, now=NOW):
    return [bar(now - (n - i) * minutes * 60, price, price + vol, price - vol, price)
            for i in range(n)]


def frames(**kwargs):
    """One bar series per timeframe key."""
    return {key: series(minutes=A.timeframe(key)["minutes"], **kwargs) for key in A.TIMEFRAME_KEYS}


def row(rows, key):
    return next(r for r in rows if r["key"] == key)


def vrow(key, score, weight, verdict):
    """A hand-made timeframe row (for the combine() rules)."""
    return {"key": key, "score": score, "weight": weight, "verdict": verdict,
            "confidence": min(100, abs(score) * 25), "ready": True}


# ------------------------------------------------------------------ plumbing
class Mt5Row:
    """What MetaTrader5 actually hands back: a subscriptable rate row."""

    def __init__(self, **kw):
        self._d = kw

    def __getitem__(self, key):
        return self._d[key]


class TestBars(unittest.TestCase):
    def test_every_row_shape_is_accepted_and_sorted(self):
        rows = A.parse_bars([
            [30, 5, 6, 4, 5.5],                       # tuple
            bar(10, 1, 2, 0.5, 1.5),                  # dict
            Mt5Row(time=20, open=3, high=4, low=2, close=3.5),   # MT5 row
        ])
        self.assertEqual([r[0] for r in rows], [10, 20, 30])
        self.assertEqual(rows[-1][4], 5.5)

    def test_junk_rows_are_skipped(self):
        rows = A.parse_bars([{"nope": 1}, None, ("x",), bar(1, 1, 1, 1, 1)])
        self.assertEqual(len(rows), 1)

    def test_the_forming_candle_is_dropped(self):
        bars = [bar(NOW - 1800, 1, 2, 0.5, 1.5), bar(NOW - 900, 2, 3, 1.5, 2.5),
                bar(NOW - 100, 3, 4, 2.5, 3.5)]      # last one is still open
        closed = A.closed_bars(bars, 15, NOW)
        self.assertEqual([b[0] for b in closed], [NOW - 1800, NOW - 900])

    def test_timeframe_lookup_normalises_input(self):
        self.assertEqual(A.timeframe("h1")["key"], "H1")
        self.assertEqual(A.timeframe("nonsense")["key"], A.DEFAULT_TIMEFRAME)


# ------------------------------------------------------------------ one timeframe
class TestTimeframeRead(unittest.TestCase):
    def test_a_rising_market_is_a_buy_with_targets_above(self):
        r = A.analyze(series(step=1.0, minutes=15), price=4200.0, digits=2, key="M15")
        self.assertTrue(r["ready"])
        self.assertEqual(r["verdict"], "BUY")
        self.assertEqual(r["score"], 4)
        self.assertGreater(r["up_price"], 4200.0)
        self.assertLess(r["down_price"], 4200.0)
        self.assertAlmostEqual(r["up_distance"], round(r["up_price"] - 4200.0, 2), places=2)
        self.assertAlmostEqual(r["atr"], round(r["atr"], 2), places=2)
        self.assertGreater(r["rsi"], 55)
        self.assertIn("trend up", " ".join(r["reasons"]))

    def test_a_falling_market_is_a_sell(self):
        r = A.analyze(series(step=-1.0, minutes=15), price=3800.0, digits=2, key="H1")
        self.assertEqual(r["verdict"], "SELL")
        self.assertEqual(r["score"], -4)
        self.assertGreater(r["down_distance"], 0)
        self.assertIn("trend down", " ".join(r["reasons"]))

    def test_a_flat_market_has_no_direction(self):
        r = A.analyze(flat(), price=4000.0, digits=2, key="M10")
        self.assertEqual(r["verdict"], A.NEUTRAL)
        self.assertEqual(r["score"], 0)
        self.assertIn("price inside the EMAs", " ".join(r["reasons"]))

    def test_not_enough_candles_means_no_reading(self):
        r = A.analyze(series(n=12, minutes=5), price=4000.0, key="M5")
        self.assertFalse(r["ready"])
        self.assertEqual(r["verdict"], A.NEUTRAL)
        self.assertEqual(r["up_price"], 0.0)

    def test_no_price_argument_falls_back_to_the_last_close(self):
        bars = series(step=1.0, minutes=15)
        r = A.analyze(bars, digits=2, key="M15")
        self.assertAlmostEqual(r["price"], round(bars[-1]["close"], 2), places=2)

    def test_targets_scale_with_atr_and_the_move_multiplier(self):
        bars = series(step=1.0, minutes=15, vol=1.0)
        one = A.analyze(bars, price=4000.0, digits=2, key="M15", move_mult=1.0)
        two = A.analyze(bars, price=4000.0, digits=2, key="M15", move_mult=2.0)
        self.assertAlmostEqual(two["up_distance"], round(one["up_distance"] * 2, 2), places=2)

    def test_swing_levels_stay_around_the_price(self):
        """After a breakout there is no level above, so the projection is used."""
        r = A.analyze(series(step=1.0, minutes=15, vol=0.5), price=4000.0, digits=2, key="M15")
        self.assertGreater(r["level_up"], 4000.0)
        self.assertLess(r["level_down"], 4000.0)


# ------------------------------------------------------------------ the vote
class TestCombine(unittest.TestCase):
    def test_all_four_timeframes_agreeing_is_a_confident_buy(self):
        rows = [vrow("M5", 3, 1.0, "BUY"), vrow("M10", 4, 1.2, "BUY"),
                vrow("M15", 4, 1.5, "BUY"), vrow("H1", 4, 2.0, "BUY")]
        c = A.combine(rows)
        self.assertEqual(c["verdict"], "BUY")
        self.assertEqual(c["confidence"], 100)

    def test_two_fast_timeframes_agreeing_can_call_a_side(self):
        rows = [vrow("M5", 3, 1.0, "BUY"), vrow("M10", 3, 1.2, "BUY"),
                vrow("M15", 0, 1.5, A.NEUTRAL), vrow("H1", 0, 2.0, A.NEUTRAL)]
        c = A.combine(rows)
        self.assertEqual(c["verdict"], "BUY")
        self.assertGreater(c["confidence"], 0)
        self.assertLess(c["confidence"], 60)      # a small slice of the board only

    def test_one_fast_timeframe_alone_cannot_call_a_side(self):
        rows = [vrow("M5", 4, 1.0, "BUY"), vrow("M10", 0, 1.2, A.NEUTRAL),
                vrow("M15", 0, 1.5, A.NEUTRAL), vrow("H1", 0, 2.0, A.NEUTRAL)]
        c = A.combine(rows)
        self.assertEqual(c["verdict"], A.NEUTRAL)
        self.assertLessEqual(c["confidence"], 20)

    def test_the_hourly_timeframe_alone_can_call_a_side(self):
        rows = [vrow("M5", 0, 1.0, A.NEUTRAL), vrow("M10", 0, 1.2, A.NEUTRAL),
                vrow("M15", 0, 1.5, A.NEUTRAL), vrow("H1", -4, 2.0, "SELL")]
        c = A.combine(rows)
        self.assertEqual(c["verdict"], "SELL")
        self.assertGreater(c["confidence"], 20)
        self.assertLess(c["confidence"], 60)

    def test_the_hourly_timeframe_beats_the_five_minute_one(self):
        rows = [vrow("M5", -4, 1.0, "SELL"), vrow("M10", 0, 1.2, A.NEUTRAL),
                vrow("M15", 0, 1.5, A.NEUTRAL), vrow("H1", 4, 2.0, "BUY")]
        c = A.combine(rows)
        self.assertEqual(c["verdict"], "BUY")
        self.assertLess(c["confidence"], 60)      # right side, but not a clean board

    def test_a_broken_board_has_no_verdict(self):
        rows = [vrow("M5", 4, 1.0, "BUY"), vrow("M10", -4, 1.2, "SELL"),
                vrow("M15", -4, 1.5, "SELL"), vrow("H1", 4, 2.0, "BUY")]
        c = A.combine(rows)
        self.assertEqual(c["verdict"], A.NEUTRAL)
        self.assertLessEqual(c["confidence"], 20)

    def test_nothing_on_the_chart_leaves_the_news_and_the_dials_to_decide(self):
        rows = [vrow(k, 0, 1.0, A.NEUTRAL) for k in A.TIMEFRAME_KEYS]
        news = A.combine(rows, news={"verdict": "BUY", "confidence": 80})
        self.assertEqual(news["verdict"], "BUY")
        self.assertGreater(news["confidence"], 0)
        flow = A.combine(rows, flow={"dominant": "SELLER", "buyer_pct": 22.0})
        self.assertEqual(flow["verdict"], "SELL")
        self.assertEqual(A.combine(rows)["verdict"], A.NEUTRAL)

    def test_the_news_and_the_dials_pull_the_confidence_up(self):
        rows = [vrow("M5", 2, 1.0, "BUY"), vrow("M10", 2, 1.2, "BUY"),
                vrow("M15", 2, 1.5, "BUY"), vrow("H1", 2, 2.0, "BUY")]
        plain = A.combine(rows)
        boosted = A.combine(rows, news={"verdict": "BUY", "confidence": 90},
                            flow={"dominant": "BUYER", "buyer_pct": 75.0})
        self.assertEqual(boosted["verdict"], "BUY")
        self.assertGreaterEqual(boosted["confidence"], plain["confidence"])
        self.assertLessEqual(boosted["confidence"], 100)

    def test_an_empty_board_is_neutral(self):
        c = A.combine([])
        self.assertEqual(c["verdict"], A.NEUTRAL)
        self.assertEqual(c["confidence"], 0)


# ------------------------------------------------------------------ the payload
class TestPayload(unittest.TestCase):
    def _payload(self, **kwargs):
        kwargs.setdefault("frames", frames(step=1.0))
        kwargs.setdefault("price", 4084.2)
        kwargs.setdefault("symbol", "XAUUSDm")
        kwargs.setdefault("digits", 2)
        kwargs.setdefault("lot", 0.01)
        kwargs.setdefault("money_per_price", 1.0)
        kwargs.setdefault("markets", ["XAUUSDm", "BTCUSDTm"])
        return A.build_payload(NOW, **kwargs)

    def test_shape_and_json_safety(self):
        p = self._payload()
        self.assertEqual(p["symbol"], "XAUUSDm")
        self.assertEqual(p["symbols"], ["XAUUSDm", "BTCUSDTm"])
        self.assertEqual(p["now_utc"], int(NOW))
        self.assertEqual(p["updated"], int(NOW))
        self.assertEqual([r["key"] for r in p["rows"]], list(A.TIMEFRAME_KEYS))
        self.assertTrue(p["ready"])
        json.dumps(p)                      # must survive the websocket frame

    def test_all_timeframes_rising_gives_a_buy_and_an_up_target(self):
        p = self._payload()
        self.assertEqual(p["combined"]["verdict"], "BUY")
        self.assertEqual(p["target"]["side"], "BUY")
        self.assertEqual(p["target"]["primary_side"], "BUY")
        self.assertGreater(p["target"]["primary_price"], p["price"])
        self.assertLess(p["target"]["risk_price"], p["price"])
        for r in p["rows"]:
            self.assertEqual(r["verdict"], "BUY")

    def test_all_timeframes_falling_gives_a_sell_and_a_down_target(self):
        p = self._payload(frames=frames(step=-1.0))
        self.assertEqual(p["combined"]["verdict"], "SELL")
        self.assertEqual(p["target"]["primary_side"], "SELL")
        self.assertLess(p["target"]["primary_price"], p["price"])
        self.assertGreater(p["target"]["risk_price"], p["price"])

    def test_money_uses_the_lot_conversion(self):
        p = self._payload(money_per_price=2.5)
        t = p["target"]
        self.assertAlmostEqual(t["up_money"], round(t["up_distance"] * 2.5, 2), places=2)
        self.assertAlmostEqual(t["down_money"], round(t["down_distance"] * 2.5, 2), places=2)

    def test_the_selected_timeframe_drives_the_targets(self):
        slow = self._payload(frames={"M5": series(step=-1.0, minutes=5),
                                     "M10": series(step=-1.0, minutes=10),
                                     "M15": series(step=-1.0, minutes=15),
                                     "H1": series(step=1.0, minutes=60)},
                             timeframe_key="H1")
        self.assertEqual(slow["timeframe"], "H1")
        self.assertEqual(slow["target"]["timeframe"], "H1")
        # the 1h board is the one that is rising here
        self.assertEqual(row(slow["rows"], "H1")["verdict"], "BUY")

    def test_without_a_tick_the_last_close_is_the_entry(self):
        p = self._payload(price=0.0, timeframe_key="M15")
        last = row(p["rows"], "M15")["close"]
        self.assertAlmostEqual(p["price"], round(last, 2), places=2)
        self.assertAlmostEqual(p["target"]["entry"], round(last, 2), places=2)

    def test_no_candles_at_all_is_an_empty_but_valid_payload(self):
        p = self._payload(frames={}, price=0.0, source="no terminal")
        self.assertFalse(p["ready"])
        self.assertFalse(p["target"]["ready"])
        self.assertEqual(p["combined"]["verdict"], A.NEUTRAL)
        self.assertEqual(len(p["rows"]), len(A.TIMEFRAME_KEYS))
        json.dumps(p)

    def test_the_forming_candles_never_leak_in(self):
        """The last (open) candle of every timeframe is ignored."""
        bars = series(step=1.0, minutes=15)
        bars.append(bar(NOW - 60, 9999.0, 9999.0, 9998.0, 9999.0))   # still forming
        p = self._payload(frames={"M15": bars}, timeframe_key="M15", price=4200.0)
        m15 = row(p["rows"], "M15")
        self.assertEqual(m15["close"], round(bars[-2]["close"], 2))
        self.assertLess(m15["up_price"], 9998.0)


# --------------------------------------------------------------- NewsBot wiring
def fake_info(sym="XAUUSDm"):
    return SimpleNamespace(
        name=sym, visible=True, trade_mode=True, trade_allowed=True,
        digits=2, point=0.01, volume_min=0.01, volume_max=100.0, volume_step=0.01,
        trade_stops_level=0, trade_freeze_level=0,
        trade_tick_value=1.0, trade_tick_size=0.01, trade_contract_size=100.0,
    )


class TestNewsBotAiBoard(unittest.TestCase):
    def setUp(self):
        self.bot = NewsBot()
        self.now = NOW

    def _mock(self, m, rising=True):
        m.TIMEFRAME_M1, m.TIMEFRAME_M5, m.TIMEFRAME_M15 = 1, 5, 15
        m.TIMEFRAME_H1, m.TIMEFRAME_H4, m.TIMEFRAME_D1 = 60, 240, 1440
        m.symbol_info.side_effect = lambda sym: fake_info(sym)
        m.symbols_get.return_value = []

        def rates(sym, tf, pos, count):
            minutes = {1: 1, 5: 5, 15: 15, 60: 60, 240: 240, 1440: 1440}.get(int(tf), 15)
            return series(step=1.0 if rising else -1.0, minutes=minutes, now=self.now)

        m.copy_rates_from_pos.side_effect = rates
        m.symbol_info_tick.return_value = SimpleNamespace(last=4084.2, bid=4084.1, ask=4084.3)
        return m

    def test_board_without_a_terminal(self):
        with patch("backend.news_bot.mt5", None):
            board = self.bot.ai_board(now=self.now)
        self.assertEqual(board["source"], "no terminal")
        self.assertFalse(board["ready"])
        self.assertEqual(len(board["rows"]), len(A.TIMEFRAMES))
        self.assertEqual(board["symbol"], "XAUUSDm")      # falls back to the bot market

    def test_board_reads_all_four_timeframes_and_the_live_price(self):
        m = MagicMock()
        with patch("backend.news_bot.mt5", self._mock(m)):
            board = self.bot.ai_board(now=self.now, force=True)
        self.assertEqual(board["source"], "mt5")
        self.assertTrue(board["ready"])
        self.assertAlmostEqual(board["price"], 4084.2, places=2)
        self.assertEqual(board["combined"]["verdict"], "BUY")
        self.assertGreater(board["target"]["up_price"], 4084.2)
        self.assertEqual(m.copy_rates_from_pos.call_count, len(A.TIMEFRAMES))

    def test_the_candle_history_is_cached_between_ticks(self):
        m = MagicMock()
        with patch("backend.news_bot.mt5", self._mock(m)):
            self.bot.ai_board(now=self.now, force=True)
            first = m.copy_rates_from_pos.call_count
            self.bot.ai_board(now=self.now + 5.0)
            self.bot.ai_board(now=self.now + 20.0)
            self.assertEqual(m.copy_rates_from_pos.call_count, first)
            self.bot.ai_board(now=self.now + 40.0)         # cache expired
            self.assertGreater(m.copy_rates_from_pos.call_count, first)

    def test_the_verdict_follows_a_falling_market(self):
        m = MagicMock()
        with patch("backend.news_bot.mt5", self._mock(m, rising=False)):
            board = self.bot.ai_board(now=self.now, force=True)
        self.assertEqual(board["combined"]["verdict"], "SELL")
        self.assertLess(board["target"]["down_price"], board["price"])

    def test_status_carries_the_ai_board(self):
        with patch("backend.news_bot.mt5", None):
            st = self.bot.status()
        ai = st["ai"]
        self.assertEqual(ai["symbol"], self.bot.ai_market())
        self.assertEqual([r["key"] for r in ai["rows"]], list(A.TIMEFRAME_KEYS))
        for field in ("verdict", "confidence", "up_price", "down_price", "reasons"):
            self.assertIn(field, ai["rows"][0])
        for field in ("side", "up_price", "down_price", "up_money", "down_money",
                      "primary_side", "risk_price", "level_up", "level_down"):
            self.assertIn(field, ai["target"])
        self.assertIn("verdict", ai["combined"])

    def test_the_dashboard_can_switch_market_and_timeframe(self):
        """The pickers post to /api/bot5/config, so the keys must survive both
        allow-lists - a filtered key would silently do nothing (found live)."""
        root = Path(__file__).resolve().parent.parent
        app_src = (root / "backend" / "app.py").read_text(encoding="utf-8")
        api_block = app_src.split("BOT5_KEYS = (", 1)[1].split("\n\n", 1)[0]
        eng_src = (root / "backend" / "engine.py").read_text(encoding="utf-8")
        eng_block = eng_src.split('"bias_min_score", "refresh_min",', 1)[1][:260]
        for key in ("ai_market", "ai_timeframe"):
            self.assertIn(key, api_block, f"{key} missing from the API allow-list")
            self.assertIn(key, eng_block, f"{key} missing from the engine patch allow-list")
        # the bot itself must accept them, and an unknown timeframe must not stick
        self.bot.update_config({"ai_market": "BTCUSDTm", "ai_timeframe": "h1"})
        self.assertEqual(self.bot.ai_market(), "BTCUSDTm")
        self.assertEqual(self.bot.ai_timeframe(), "H1")
        self.bot.update_config({"ai_market": "", "ai_timeframe": "3d"})
        self.assertEqual(self.bot.ai_market(), "XAUUSDm")      # back to the bot's own market
        self.assertEqual(self.bot.ai_timeframe(), A.DEFAULT_TIMEFRAME)

    def test_the_stub_keeps_the_panel_alive_while_logged_out(self):
        """The engine builds a bot-5 stub every tick while logged out - it must
        carry a full AI payload or the panel would sit empty."""
        from backend import engine as eng
        stub = eng.Engine.__dict__["_bot5_stub"]
        engine_obj = object.__new__(eng.Engine)
        engine_obj.bot5 = self.bot
        engine_obj.bot5_status = {}
        with patch("backend.news_bot.mt5", None):
            engine_obj.mode = "ea"
            data = stub(engine_obj)
        self.assertIn("ai", data)
        self.assertEqual(len(data["ai"]["rows"]), len(A.TIMEFRAMES))
        self.assertEqual(data["ai"]["source"], "no terminal")


if __name__ == "__main__":
    unittest.main(verbosity=2)
