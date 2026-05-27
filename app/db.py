from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.config import DB_PATH

_SCHEMA = Path(__file__).parent / "schema.sql"

_SEED = """
INSERT INTO defaults (id, course_1, course_2, course_3, start_time_1, capacity_1, updated_at)
VALUES (1,
    'コース内容はデフォルト設定画面で変更できます',
    NULL, NULL, '19:00', 6, :ts)
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(_SCHEMA.read_text("utf-8"))
        if conn.execute("SELECT COUNT(*) FROM defaults").fetchone()[0] == 0:
            conn.execute(_SEED, {"ts": now()})
        conn.commit()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()
