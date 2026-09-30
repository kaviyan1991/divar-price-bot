"""SQLite storage."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS ads (
  token TEXT PRIMARY KEY,
  title TEXT, brand_model TEXT, brand TEXT, model TEXT,
  year INTEGER, mileage INTEGER, fuel TEXT, gearbox TEXT, body TEXT,
  customs TEXT, color TEXT, zero_km INTEGER DEFAULT 0, city TEXT,
  image_url TEXT, image_count INTEGER,
  list_price INTEGER, list_mileage INTEGER, first_price INTEGER, current_price INTEGER,
  status TEXT DEFAULT 'pending',       -- pending | active | removed | skipped
  first_seen TEXT, last_seen TEXT, last_checked TEXT,
  miss_count INTEGER DEFAULT 0, removed_at TEXT,
  duplicate_of TEXT, below_market INTEGER DEFAULT 0,
  post_eligible INTEGER DEFAULT 0, message_id INTEGER, has_photo INTEGER DEFAULT 0,
  photo_url TEXT, seller_type TEXT
);
CREATE TABLE IF NOT EXISTS price_history (
  token TEXT, seen_at TEXT, price INTEGER
);
CREATE INDEX IF NOT EXISTS ix_hist ON price_history(token);
CREATE INDEX IF NOT EXISTS ix_group ON ads(brand_model, year);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS watches (chat_id INTEGER, query TEXT, created_at TEXT,
  PRIMARY KEY (chat_id, query));
"""


def connect(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(ads)")}
    for name, kind in (("list_mileage", "INTEGER"), ("has_photo", "INTEGER DEFAULT 0"),
                       ("photo_url", "TEXT"), ("seller_type", "TEXT")):
        if name not in cols:  # upgrade databases created by older versions
            con.execute(f"ALTER TABLE ads ADD COLUMN {name} {kind}")
    return con


def get_state(con, key, default=None):
    r = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_state(con, key, value):
    con.execute("INSERT INTO state(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def add_price(con, token, when, price):
    con.execute("INSERT INTO price_history(token,seen_at,price) VALUES(?,?,?)",
                (token, when, price))
