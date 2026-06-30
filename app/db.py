from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.config import DB_PATH

_SCHEMA = Path(__file__).parent / "schema.sql"

_SEED_DEFAULTS = """
INSERT INTO defaults (id, course, updated_at)
VALUES (1, 'コース内容はデフォルト設定画面で変更できます', :ts)
"""

# 初期曜日設定: 月〜金 19:00 / 土日 13:00+18:00 / 水 定休
_WEEKDAY_SEED = [
    # (weekday, is_closed, time1,    cap1, time2,   cap2)
    (0, 0, "19:00", 6, None,    None),  # 月
    (1, 0, "19:00", 6, None,    None),  # 火
    (2, 1, None,    None, None, None),  # 水 (定休)
    (3, 0, "19:00", 6, None,    None),  # 木
    (4, 0, "19:00", 6, None,    None),  # 金
    (5, 0, "13:00", 6, "18:00", 6),     # 土
    (6, 0, "13:00", 6, "18:00", 6),     # 日
]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(_SCHEMA.read_text("utf-8"))

        # ── マイグレーション: 旧スキーマからの列追加 ──────────────────────────
        for sql in [
            "ALTER TABLE defaults ADD COLUMN course TEXT",
            "ALTER TABLE day_config ADD COLUMN course TEXT",
            "ALTER TABLE reservation ADD COLUMN email_token TEXT",
            "ALTER TABLE reservation ADD COLUMN token_expires_at TEXT",
            "ALTER TABLE reservation RENAME COLUMN email_token TO verification_token",
            "ALTER TABLE defaults ADD COLUMN course1_name TEXT",
            "ALTER TABLE defaults ADD COLUMN course1_price TEXT",
            "ALTER TABLE defaults ADD COLUMN course2_name TEXT",
            "ALTER TABLE defaults ADD COLUMN course2_price TEXT",
            "ALTER TABLE defaults ADD COLUMN course3_name TEXT",
            "ALTER TABLE defaults ADD COLUMN course3_price TEXT",
            "ALTER TABLE reservation ADD COLUMN course_name TEXT",
            "ALTER TABLE reservation ADD COLUMN course_price TEXT",
            # 支払方法は廃止（カード決済のみ）→ 不要列を削除（SQLite 3.35+）
            "ALTER TABLE survey_response DROP COLUMN payment_method",
            "ALTER TABLE survey_response DROP COLUMN transfer_name",
            # 入金確認による「確定」作業は廃止（SMS認証完了=確定）→ confirmed列を削除
            "ALTER TABLE reservation DROP COLUMN confirmed",
        ]:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # 既存列はスキップ

        # 旧 course_1 → course へのデータ移行
        try:
            conn.execute(
                "UPDATE defaults SET course = course_1 "
                "WHERE course IS NULL AND course_1 IS NOT NULL"
            )
        except sqlite3.OperationalError:
            pass

        # ── マイグレーション: reservation.status CHECK に pending_verify を追加 ──
        # SQLite は CHECK 制約を ALTER で変更できないためテーブルを再作成する
        res_schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='reservation'"
        ).fetchone()
        if res_schema and "pending_verify" not in (res_schema[0] or ""):
            conn.executescript("""
                ALTER TABLE reservation RENAME TO _reservation_old;
                CREATE TABLE reservation (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    date        TEXT NOT NULL,
                    rotation    INTEGER NOT NULL CHECK (rotation IN (1, 2)),
                    name        TEXT NOT NULL,
                    num_people  INTEGER NOT NULL CHECK (num_people >= 1),
                    phone       TEXT NOT NULL,
                    email       TEXT NOT NULL,
                    note        TEXT,
                    course_name  TEXT,
                    course_price TEXT,
                    status      TEXT NOT NULL DEFAULT 'pending_verify'
                                CHECK (status IN ('active', 'cancelled', 'pending_verify')),
                    verification_token TEXT,
                    token_expires_at  TEXT,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );
                INSERT INTO reservation
                    SELECT id, date, rotation, name, num_people, phone, email, note,
                           NULL, NULL,
                           status, verification_token, token_expires_at,
                           created_at, updated_at
                    FROM _reservation_old;
                DROP TABLE _reservation_old;
                CREATE INDEX IF NOT EXISTS idx_res_date_status ON reservation(date, status);
            """)

        # ── シード ────────────────────────────────────────────────────────────
        if conn.execute("SELECT COUNT(*) FROM defaults").fetchone()[0] == 0:
            conn.execute(_SEED_DEFAULTS, {"ts": now()})

        ts = now()
        for wd, closed, t1, c1, t2, c2 in _WEEKDAY_SEED:
            conn.execute(
                """INSERT OR IGNORE INTO weekday_defaults
                   (weekday, is_closed, start_time_1, capacity_1,
                    start_time_2, capacity_2, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (wd, closed, t1, c1, t2, c2, ts),
            )

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
