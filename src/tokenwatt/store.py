import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts          REAL PRIMARY KEY,
    interval_s  REAL NOT NULL,
    cpu_pct     REAL NOT NULL,
    claude_cpu  REAL,
    ram_pct     REAL NOT NULL,
    ram_used    INTEGER NOT NULL,
    swap_pct    REAL,
    freq_mhz    REAL,
    gpu_pct     REAL,
    watts       REAL,
    watts_src   TEXT,
    watts_est   REAL NOT NULL,
    on_battery  INTEGER
);

CREATE TABLE IF NOT EXISTS tokens (
    msg_id       TEXT PRIMARY KEY,
    ts           REAL NOT NULL,
    model        TEXT,
    session      TEXT,
    project      TEXT,
    input        INTEGER NOT NULL DEFAULT 0,
    output       INTEGER NOT NULL DEFAULT 0,
    cache_write  INTEGER NOT NULL DEFAULT 0,
    cache_read   INTEGER NOT NULL DEFAULT 0,
    source       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tokens_ts ON tokens(ts);

CREATE TABLE IF NOT EXISTS seen_files (
    path    TEXT PRIMARY KEY,
    size    INTEGER NOT NULL,
    mtime   REAL NOT NULL,
    offset  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


def default_db_path():
    env = os.environ.get("TOKENWATT_DB")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".tokenwatt" / "tokenwatt.db"


def connect(path=None):
    path = Path(path) if path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    cols = {r["name"] for r in con.execute("PRAGMA table_info(seen_files)")}
    if "offset" not in cols:  # databases from before 1.1
        con.execute("ALTER TABLE seen_files ADD COLUMN offset INTEGER NOT NULL DEFAULT 0")
        con.commit()
    return con


def set_meta(con, key, value):
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(value)))
    con.commit()


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def insert_sample(con, s):
    con.execute(
        """INSERT OR REPLACE INTO samples
           (ts, interval_s, cpu_pct, claude_cpu, ram_pct, ram_used, swap_pct, freq_mhz,
            gpu_pct, watts, watts_src, watts_est, on_battery)
           VALUES (:ts, :interval_s, :cpu_pct, :claude_cpu, :ram_pct, :ram_used, :swap_pct,
                   :freq_mhz, :gpu_pct, :watts, :watts_src, :watts_est, :on_battery)""",
        s,
    )
    con.commit()


def upsert_tokens(con, rows):
    # A streamed reply can be split across two reads of a growing log, so keep
    # the earliest timestamp and the latest counts.
    con.executemany(
        """INSERT INTO tokens
           (msg_id, ts, model, session, project, input, output, cache_write, cache_read, source)
           VALUES (:msg_id, :ts, :model, :session, :project, :input, :output,
                   :cache_write, :cache_read, :source)
           ON CONFLICT(msg_id) DO UPDATE SET
               ts = MIN(ts, excluded.ts),
               model = excluded.model,
               input = excluded.input,
               output = excluded.output,
               cache_write = excluded.cache_write,
               cache_read = excluded.cache_read""",
        rows,
    )
    con.commit()


def samples_between(con, start=None, end=None):
    q, args = "SELECT * FROM samples", []
    q, args = _range(q, args, start, end)
    return [dict(r) for r in con.execute(q + " ORDER BY ts", args)]


def tokens_between(con, start=None, end=None):
    q, args = "SELECT * FROM tokens", []
    q, args = _range(q, args, start, end)
    return [dict(r) for r in con.execute(q + " ORDER BY ts", args)]


def _range(q, args, start, end):
    conds = []
    if start is not None:
        conds.append("ts >= ?")
        args.append(start)
    if end is not None:
        conds.append("ts < ?")
        args.append(end)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    return q, args
