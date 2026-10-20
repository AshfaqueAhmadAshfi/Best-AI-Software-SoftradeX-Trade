"""Exness self-heal tests.

The dashboard must quietly re-attach to the running Exness MetaTrader 5
terminal after a silent IPC loss (terminal restart / update) instead of
parking the user on the login screen. No password is ever stored, so a heal
may only re-initialize the SAME terminal and only while it still reports the
SAME account - never a different one.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import mt5_client as MC
from backend.engine import Engine
from backend.store import Store


class FakeMT5:
    """Enough of the MetaTrader5 module for recovery attempts."""

    def __init__(self, info_on=True, init_ok=True, account_login=12345678):
        self.info_on = info_on
        self.init_ok = init_ok
        self.account_login = account_login
        self.calls = {"initialize": [], "shutdown": 0}
        self.last_error = (-1, "no error")

    def terminal_info(self):
        if not self.info_on:
            return None
        return SimpleNamespace(connected=True, path="C:\\Fake\\MT5")

    def initialize(self, **kw):
        self.calls["initialize"].append(kw)
        return self.init_ok

    def shutdown(self):
        self.calls["shutdown"] += 1
        return True

    def account_info(self):
        return SimpleNamespace(login=self.account_login, server="Exness-MT5Real")

    def last_error(self):
        return self.last_error


class TryRecoverTests(unittest.TestCase):
    def _client(self, mt5mod):
        self.addCleanup(setattr, MC, "mt5", MC.mt5)
        MC.mt5 = mt5mod
        client = MC.MT5Client()
        client.connected = True
        client.login = 12345678
        client.terminal_path = ""
        return client

    def test_false_when_package_missing(self):
        self.addCleanup(setattr, MC, "mt5", MC.mt5)
        MC.mt5 = None
        self.assertFalse(MC.MT5Client().try_recover())

    def test_noop_while_attach_is_alive(self):
        mt5mod = FakeMT5(info_on=True)
        client = self._client(mt5mod)
        self.assertTrue(client.try_recover())
        self.assertEqual(mt5mod.calls["initialize"], [])
        self.assertEqual(mt5mod.calls["shutdown"], 0)

    def test_false_without_a_saved_login(self):
        mt5mod = FakeMT5(info_on=False)
        client = self._client(mt5mod)
        client.login = 0
        self.assertFalse(client.try_recover())
        self.assertEqual(mt5mod.calls["shutdown"], 0)

    def test_reattaches_and_verifies_same_account(self):
        mt5mod = FakeMT5(info_on=False)
        client = self._client(mt5mod)
        with patch.object(MC.MT5Client, "_calibrate_clock", lambda self, *a, **k: None):
            self.assertTrue(client.try_recover())
        self.assertEqual(client.login, 12345678)
        self.assertEqual(len(mt5mod.calls["initialize"]), 1)

    def test_reattach_restores_the_connected_flag(self):
        mt5mod = FakeMT5(info_on=False)
        client = self._client(mt5mod)
        client.connected = False
        with patch.object(MC.MT5Client, "_calibrate_clock", lambda self, *a, **k: None):
            self.assertTrue(client.try_recover())
        self.assertTrue(client.connected)

    def test_alive_attach_restores_the_connected_flag(self):
        mt5mod = FakeMT5(info_on=True)
        client = self._client(mt5mod)
        client.connected = False
        self.assertTrue(client.try_recover())
        self.assertTrue(client.connected)
        self.assertEqual(mt5mod.calls["initialize"], [])

    def test_passes_the_saved_terminal_path(self):
        mt5mod = FakeMT5(info_on=False)
        client = self._client(mt5mod)
        client.terminal_path = r"C:\Fake\Exness\terminal64.exe"
        with patch.object(MC.MT5Client, "_calibrate_clock", lambda self, *a, **k: None), \
             patch.object(Path, "exists", lambda self: True):
            self.assertTrue(client.try_recover())
        self.assertTrue(mt5mod.calls["initialize"])

    def test_stale_terminal_path_is_not_passed(self):
        mt5mod = FakeMT5(info_on=False)
        client = self._client(mt5mod)
        client.terminal_path = r"C:\Fake\Exness\gone.exe"
        with patch.object(MC.MT5Client, "_calibrate_clock", lambda self, *a, **k: None):
            # the dead cached path is skipped; initialize auto-detects instead
            self.assertTrue(client.try_recover())
        self.assertNotIn("path", mt5mod.calls["initialize"][0])

    def test_false_when_initialize_fails(self):
        mt5mod = FakeMT5(info_on=False, init_ok=False)
        client = self._client(mt5mod)
        self.assertFalse(client.try_recover())
        self.assertEqual(len(mt5mod.calls["initialize"]), 1)

    def test_false_when_terminal_reports_a_different_account(self):
        mt5mod = FakeMT5(info_on=False, account_login=999)
        client = self._client(mt5mod)
        self.assertFalse(client.try_recover())
        self.assertTrue(mt5mod.calls["shutdown"] >= 1)


class EngineHealTests(unittest.TestCase):
    def _engine(self):
        store = Store(Path(tempfile.mkdtemp()) / "heal.db")
        eng = Engine(store, publisher=lambda payload, force: None)
        return eng

    def test_heal_succeeds_and_logs(self):
        eng = self._engine()
        eng.start()
        self.addCleanup(eng.stop)
        eng._last_heal_at = 0.0
        with patch.object(eng.mt5, "try_recover", return_value=True):
            self.assertTrue(eng._auto_heal_terminal())
            self.assertEqual(eng.status, "connected")
        kinds = [e.get("kind") for e in eng.events[:2]]
        self.assertIn("success", kinds)
        msgs = [e.get("message", "") for e in eng.events[:2]]
        self.assertTrue(any("re-connected (auto)" in m for m in msgs), msgs)

    def test_heal_rate_limit(self):
        eng = self._engine()
        eng.start()
        self.addCleanup(eng.stop)
        eng._last_heal_at = 0.0
        with patch.object(eng.mt5, "try_recover", return_value=True) as rec:
            self.assertTrue(eng._auto_heal_terminal())
            self.assertEqual(rec.call_count, 1)
            # a second attempt in the same window is refused, never spammed
            self.assertFalse(eng._auto_heal_terminal())
        again = Engine._HEAL_MIN_GAP + 1
        existing = getattr(eng, "_last_heal_at", 0.0)
        eng._last_heal_at = existing - again
        with patch.object(eng.mt5, "try_recover", return_value=True) as rec2:
            self.assertTrue(eng._auto_heal_terminal())
            self.assertEqual(rec2.call_count, 1)

    def test_tick_live_marks_error_when_heal_fails(self):
        eng = self._engine()
        eng.start()
        self.addCleanup(eng.stop)
        # simulate a live session whose IPC attach has quietly dropped
        eng.mode = "live"
        eng.mt5.connected = True
        eng.mt5.account = lambda: None
        eng._last_heal_at = 0.0
        with patch.object(eng.mt5, "try_recover", return_value=False):
            eng._tick_live()
        self.assertEqual(eng.status, "error")
        self.assertTrue(any("auto re-connect" in (e.get("message") or "")
                            for e in eng.events[:2]), eng.events[:2])


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
