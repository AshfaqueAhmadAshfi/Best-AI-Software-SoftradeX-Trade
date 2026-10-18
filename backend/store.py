"""
SQLite persistence for closed trades.

The dashboard must survive restarts and MT5's own history can be slow to
query, so every closed trade we ever see is upserted here (idempotent by
position id) and the UI reads history from this store.

Every row is tagged with the account login it belongs to, so logging into
account B never shows account A's history.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    position_id TEXT NOT NULL,
    account     TEXT NOT NULL DEFAULT '',
    ticket      INTEGER,
    symbol      TEXT,
    side        TEXT,
    volume      REAL,
    open_price  REAL,
    open_time   INTEGER,
    close_price REAL,
    close_time  INTEGER,
    profit      REAL,
    commission  REAL,
    swap        REAL,
    net_profit  REAL,
    magic       INTEGER,
    comment     TEXT,
    source      TEXT,
    updated_at  INTEGER,
    PRIMARY KEY (account, position_id)
);
CREATE INDEX IF NOT EXISTS idx_trades_close_time ON trades(close_time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_source ON trades(source);

CREATE TABLE IF NOT EXISTS accounts (
    login      TEXT PRIMARY KEY,
    server     TEXT,
    currency   TEXT,
    name       TEXT,
    last_seen  INTEGER
);

CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       INTEGER,
    kind     TEXT,
    payload  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
"""


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the first release + drop dead rows."""
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(trades)")}
        if "account" not in cols:
            self._conn.execute("ALTER TABLE trades ADD COLUMN account TEXT")
        try:
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_account ON trades(account)")
        except sqlite3.Error:
            pass
        # Demo mode was removed, and history is now always scoped to an account
        # login, so rows that cannot be attributed to an account can never be
        # displayed - purge them so old data can never leak into a session.
        self._conn.execute("DELETE FROM trades WHERE source = 'demo'")
        self._conn.execute("DELETE FROM trades WHERE account IS NULL OR account = ''")
        # Position ids are per-server counters: two accounts can hold the same
        # id, so the key must be (account, position_id). Older databases keyed
        # on position_id alone would let one account's trade overwrite the
        # other's - rebuild such tables once.
        info = list(self._conn.execute("PRAGMA table_info(trades)"))
        pk_cols = [r[1] for r in sorted((r for r in info if r[5] > 0), key=lambda r: r[5])]
        if pk_cols and pk_cols != ["account", "position_id"]:
            self._rebuild_trades_composite_pk()
        self._conn.commit()

    _PK_COLS = ("position_id, account, ticket, symbol, side, volume, open_price, open_time, "
                "close_price, close_time, profit, commission, swap, net_profit, magic, "
                "comment, source, updated_at")

    def _rebuild_trades_composite_pk(self) -> None:
        self._conn.executescript("""
            DROP INDEX IF EXISTS idx_trades_close_time;
            DROP INDEX IF EXISTS idx_trades_symbol;
            DROP INDEX IF EXISTS idx_trades_source;
            DROP INDEX IF EXISTS idx_trades_account;
            DROP TABLE IF EXISTS trades_new;
            CREATE TABLE trades_new (
                position_id TEXT NOT NULL,
                account     TEXT NOT NULL DEFAULT '',
                ticket      INTEGER,
                symbol      TEXT,
                side        TEXT,
                volume      REAL,
                open_price  REAL,
                open_time   INTEGER,
                close_price REAL,
                close_time  INTEGER,
                profit      REAL,
                commission  REAL,
                swap        REAL,
                net_profit  REAL,
                magic       INTEGER,
                comment     TEXT,
                source      TEXT,
                updated_at  INTEGER,
                PRIMARY KEY (account, position_id)
            );
        """)
        # ascending updated_at: on a (account, position_id) collision the most
        # recently refreshed row wins
        self._conn.execute(
            f"INSERT OR REPLACE INTO trades_new ({self._PK_COLS}) "
            f"SELECT {self._PK_COLS} FROM trades ORDER BY updated_at ASC"
        )
        self._conn.execute("DROP TABLE trades")
        self._conn.execute("ALTER TABLE trades_new RENAME TO trades")
        self._conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_trades_close_time ON trades(close_time DESC);
            CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_trades_source ON trades(source);
        """)

    # ------------------------------------------------------------------ trades
    def upsert_trades(self, trades: List[Dict[str, Any]], account: str = "") -> int:
        if not trades:
            return 0
        now = int(time.time())
        rows = []
        for t in trades:
            pid = t.get("position_id") or t.get("ticket")
            if not pid:
                continue
            rows.append((
                str(pid),
                str(t.get("account") or account or ""),
                int(t.get("ticket") or 0),
                t.get("symbol") or "",
                t.get("side") or "",
                float(t.get("volume") or 0.0),
                t.get("open_price"),
                int(t.get("open_time") or 0),
                t.get("close_price"),
                int(t.get("close_time") or 0),
                float(t.get("profit") or 0.0),
                float(t.get("commission") or 0.0),
                float(t.get("swap") or 0.0),
                float(t.get("net_profit") or 0.0),
                int(t.get("magic") or 0),
                t.get("comment") or "",
                t.get("source") or "mt5",
                now,
            ))
        if not rows:
            return 0
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO trades (position_id, account, ticket, symbol, side, volume,
                                    open_price, open_time, close_price, close_time,
                                    profit, commission, swap, net_profit, magic,
                                    comment, source, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(account, position_id) DO UPDATE SET
                    symbol      = excluded.symbol,
                    side        = excluded.side,
                    volume      = excluded.volume,
                    open_price  = COALESCE(excluded.open_price, trades.open_price),
                    open_time   = CASE WHEN excluded.open_time > 0 THEN excluded.open_time ELSE trades.open_time END,
                    close_price = COALESCE(excluded.close_price, trades.close_price),
                    close_time  = CASE WHEN excluded.close_time > 0 THEN excluded.close_time ELSE trades.close_time END,
                    profit      = excluded.profit,
                    commission  = excluded.commission,
                    swap        = excluded.swap,
                    net_profit  = excluded.net_profit,
                    magic       = excluded.magic,
                    comment     = excluded.comment,
                    source      = excluded.source,
                    updated_at  = excluded.updated_at
                """,
                rows,
            )
            self._conn.commit()
        return len(rows)

    def get_trades(
        self,
        since_ts: Optional[int] = None,
        until_ts: Optional[int] = None,
        symbol: Optional[str] = None,
        result: Optional[str] = None,
        limit: int = 2000,
        offset: int = 0,
        order: str = "DESC",
        account: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        q = "SELECT * FROM trades WHERE close_time > 0"
        args: List[Any] = []
        if account is not None:
            q += " AND account = ?"
            args.append(str(account))
        if since_ts:
            q += " AND close_time >= ?"
            args.append(int(since_ts))
        if until_ts:
            q += " AND close_time < ?"
            args.append(int(until_ts))
        if symbol:
            q += " AND symbol = ?"
            args.append(symbol)
        if result == "win":
            q += " AND net_profit > 0"
        elif result == "loss":
            q += " AND net_profit < 0"
        q += f" ORDER BY close_time {('ASC' if order.upper() == 'ASC' else 'DESC')} LIMIT ? OFFSET ?"
        args.extend([int(limit), int(offset)])
        with self._lock:
            cur = self._conn.execute(q, args)
            return [dict(r) for r in cur.fetchall()]

    def count_trades(self, account: Optional[str] = None) -> int:
        with self._lock:
            if account is None:
                cur = self._conn.execute("SELECT COUNT(*) FROM trades")
            else:
                cur = self._conn.execute("SELECT COUNT(*) FROM trades WHERE account = ?", (str(account),))
            return int(cur.fetchone()[0])

    def symbols(self, account: Optional[str] = None) -> List[str]:
        with self._lock:
            if account is None:
                cur = self._conn.execute("SELECT symbol, COUNT(*) c FROM trades GROUP BY symbol ORDER BY c DESC")
            else:
                cur = self._conn.execute(
                    "SELECT symbol, COUNT(*) c FROM trades WHERE account = ? GROUP BY symbol ORDER BY c DESC",
                    (str(account),),
                )
            return [r["symbol"] for r in cur.fetchall() if r["symbol"]]

    # ---------------------------------------------------------------- accounts
    def touch_account(self, login: str, server: str, currency: str = "", name: str = "") -> None:
        if not login:
            return
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO accounts (login, server, currency, name, last_seen)
                VALUES (?,?,?,?,?)
                ON CONFLICT(login) DO UPDATE SET
                    server = excluded.server,
                    currency = excluded.currency,
                    name = excluded.name,
                    last_seen = excluded.last_seen
                """,
                (str(login), server, currency, name, int(time.time())),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ events
    def log_event(self, kind: str, payload: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, kind, payload) VALUES (?,?,?)",
                (int(time.time()), kind, payload[:4000]),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
