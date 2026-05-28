"""All SQLite CRUD operations. No business logic — just read/write."""
from __future__ import annotations
import secrets
from datetime import date as _date_cls, datetime, timezone, timedelta
from typing import Optional
from app.db import get_conn, now


# ── Defaults ──────────────────────────────────────────────────────────────────

def get_all_defaults() -> dict:
    """Returns {"course": str|None, "weekday": {0: {...}, ..., 6: {...}}}"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM defaults WHERE id=1").fetchone()
        wd_rows = conn.execute(
            "SELECT * FROM weekday_defaults ORDER BY weekday"
        ).fetchall()
    return {
        "course": dict(row)["course"] if row else None,
        "weekday": {r["weekday"]: dict(r) for r in wd_rows},
    }


def save_defaults(course: Optional[str]) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE defaults SET course=?, updated_at=? WHERE id=1",
            (course or None, now()),
        )
        conn.commit()


def save_weekday_defaults(settings: list[dict]) -> None:
    """Upsert 7 weekday rows. Each dict: weekday, is_closed, start_time_1,
    capacity_1, start_time_2, capacity_2."""
    with get_conn() as conn:
        ts = now()
        for s in settings:
            conn.execute(
                """INSERT INTO weekday_defaults
                   (weekday, is_closed, start_time_1, capacity_1,
                    start_time_2, capacity_2, updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(weekday) DO UPDATE SET
                     is_closed=excluded.is_closed,
                     start_time_1=excluded.start_time_1,
                     capacity_1=excluded.capacity_1,
                     start_time_2=excluded.start_time_2,
                     capacity_2=excluded.capacity_2,
                     updated_at=excluded.updated_at""",
                (s["weekday"], s["is_closed"], s.get("start_time_1"),
                 s.get("capacity_1"), s.get("start_time_2") or None,
                 s.get("capacity_2"), ts),
            )
        conn.commit()


# ── DayConfig ─────────────────────────────────────────────────────────────────

def get_day_config(date: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM day_config WHERE date=?", (date,)
        ).fetchone()
    return dict(row) if row else None


def get_all_day_configs() -> dict[str, dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM day_config").fetchall()
    return {r["date"]: dict(r) for r in rows}


def upsert_day_config(
    date: str,
    is_closed: int = 0,
    is_manual_override: int = 1,
    course: Optional[str] = None,
    start_time_1: Optional[str] = None,
    capacity_1: Optional[int] = None,
    start_time_2: Optional[str] = None,
    capacity_2: Optional[int] = None,
    conn=None,
) -> None:
    sql = """
        INSERT INTO day_config
          (date, is_closed, is_manual_override, course,
           start_time_1, capacity_1, start_time_2, capacity_2, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            is_closed=excluded.is_closed,
            is_manual_override=excluded.is_manual_override,
            course=excluded.course,
            start_time_1=excluded.start_time_1,
            capacity_1=excluded.capacity_1,
            start_time_2=excluded.start_time_2,
            capacity_2=excluded.capacity_2,
            updated_at=excluded.updated_at
    """
    params = (date, is_closed, is_manual_override, course or None,
              start_time_1, capacity_1, start_time_2 or None, capacity_2, now())
    if conn is not None:
        conn.execute(sql, params)
    else:
        with get_conn() as c:
            c.execute(sql, params)
            c.commit()


def get_effective_config(date_str: str, all_defaults: dict,
                          day_configs: Optional[dict[str, dict]] = None) -> dict:
    """Merge day_config → weekday_defaults → course. Returns resolved config dict."""
    weekday = _date_cls.fromisoformat(date_str).weekday()  # 0=Mon, 6=Sun
    wd = all_defaults["weekday"].get(weekday, {})

    dc = day_configs.get(date_str) if day_configs is not None else get_day_config(date_str)

    if dc is None:
        return {
            "course":        all_defaults["course"],
            "start_time_1":  wd.get("start_time_1"),
            "capacity_1":    wd.get("capacity_1"),
            "start_time_2":  wd.get("start_time_2"),
            "capacity_2":    wd.get("capacity_2"),
            "is_closed":     bool(wd.get("is_closed", 0)),
            "is_manual_override": False,
        }

    def _pick(dc_val, wd_val):
        return dc_val if dc_val is not None else wd_val

    return {
        "course":        _pick(dc.get("course"),       all_defaults["course"]),
        "start_time_1":  _pick(dc.get("start_time_1"), wd.get("start_time_1")),
        "capacity_1":    _pick(dc.get("capacity_1"),   wd.get("capacity_1")),
        "start_time_2":  _pick(dc.get("start_time_2"), wd.get("start_time_2")),
        "capacity_2":    _pick(dc.get("capacity_2"),   wd.get("capacity_2")),
        "is_closed":     bool(dc.get("is_closed", wd.get("is_closed", 0))),
        "is_manual_override": bool(dc.get("is_manual_override", 0)),
    }


# ── Reservations ──────────────────────────────────────────────────────────────

def get_inventory_bulk(dates: list[str]) -> dict[tuple[str, int], dict]:
    if not dates:
        return {}
    placeholders = ",".join("?" * len(dates))
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT date, rotation,
                    SUM(num_people) AS booked,
                    SUM(CASE WHEN confirmed=1 THEN num_people ELSE 0 END) AS confirmed_count
                FROM reservation
                WHERE date IN ({placeholders}) AND status='active'
                GROUP BY date, rotation""",
            dates,
        ).fetchall()
    return {(r["date"], r["rotation"]): {"booked": r["booked"],
                                          "confirmed_count": r["confirmed_count"]}
            for r in rows}


def count_active_for_date(date: str) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COALESCE(SUM(num_people),0) FROM reservation "
            "WHERE date=? AND status='active'", (date,)
        ).fetchone()[0]


def _weekday_cap(conn, date_str: str, rotation: int, dc: Optional[dict]) -> int:
    """Resolve capacity for a rotation, falling back to weekday_defaults."""
    if rotation == 1:
        if dc and dc.get("capacity_1") is not None:
            return dc["capacity_1"]
    else:
        if dc and dc.get("capacity_2") is not None:
            return dc["capacity_2"]

    weekday = _date_cls.fromisoformat(date_str).weekday()
    wd_row = conn.execute(
        "SELECT * FROM weekday_defaults WHERE weekday=?", (weekday,)
    ).fetchone()
    if wd_row is None:
        return 0
    return wd_row["capacity_1"] if rotation == 1 else (wd_row["capacity_2"] or 0)


def create_reservation(
    date: str, rotation: int, name: str, num_people: int,
    phone: str, email: str, note: Optional[str],
) -> Optional[tuple[int, str]]:
    """Insert reservation (pending_verify) inside a transaction with re-check.
    Returns (id, email_token) or None if capacity exceeded."""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        dc_row = conn.execute(
            "SELECT * FROM day_config WHERE date=?", (date,)
        ).fetchone()
        dc = dict(dc_row) if dc_row else None
        cap = _weekday_cap(conn, date, rotation, dc)

        booked = conn.execute(
            "SELECT COALESCE(SUM(num_people),0) FROM reservation "
            "WHERE date=? AND rotation=? AND status='active'",
            (date, rotation),
        ).fetchone()[0]

        if booked + num_people > cap:
            conn.execute("ROLLBACK")
            return None

        ts = now()
        cur = conn.execute(
            "INSERT INTO reservation "
            "(date,rotation,name,num_people,phone,email,note,"
            "status,email_token,token_expires_at,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (date, rotation, name, num_people, phone, email, note or None,
             "pending_verify", token, expires, ts, ts),
        )
        rid = cur.lastrowid
        conn.commit()
        return rid, token


def activate_by_token(token: str) -> Optional[dict]:
    """Verify email token and activate reservation. Returns reservation dict or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM reservation WHERE email_token=? AND status='pending_verify'",
            (token,),
        ).fetchone()
        if not row:
            return None
        res = dict(row)
        expires = datetime.strptime(
            res["token_expires_at"], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires:
            return None  # 期限切れ — レコードはそのまま残す
        conn.execute(
            "UPDATE reservation SET status='active', email_token=NULL, "
            "token_expires_at=NULL, updated_at=? WHERE id=?",
            (now(), res["id"]),
        )
        conn.commit()
        res["status"] = "active"
        return res


def get_reservation(rid: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM reservation WHERE id=?", (rid,)).fetchone()
    return dict(row) if row else None


def list_reservations_for_date(date: str) -> list[dict]:
    """Returns active + pending_verify reservations for admin view."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reservation "
            "WHERE date=? AND status IN ('active','pending_verify') "
            "ORDER BY rotation, created_at",
            (date,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_confirmed(rid: int, confirmed: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE reservation SET confirmed=?,updated_at=? WHERE id=?",
            (confirmed, now(), rid),
        )
        conn.commit()


def cancel_reservation(rid: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE reservation SET status='cancelled',updated_at=? WHERE id=?",
            (now(), rid),
        )
        conn.commit()


def move_reservation(rid: int, new_date: str, new_rotation: int) -> tuple[bool, str]:
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        res = conn.execute(
            "SELECT * FROM reservation WHERE id=?", (rid,)
        ).fetchone()
        if not res or res["status"] != "active":
            conn.execute("ROLLBACK")
            return False, "予約が見つかりません"

        dc_row = conn.execute(
            "SELECT * FROM day_config WHERE date=?", (new_date,)
        ).fetchone()
        dc = dict(dc_row) if dc_row else None
        cap = _weekday_cap(conn, new_date, new_rotation, dc)

        booked = conn.execute(
            "SELECT COALESCE(SUM(num_people),0) FROM reservation "
            "WHERE date=? AND rotation=? AND status='active' AND id!=?",
            (new_date, new_rotation, rid),
        ).fetchone()[0]

        if booked + res["num_people"] > cap:
            conn.execute("ROLLBACK")
            return False, "移動先の席数が不足しています"

        conn.execute(
            "UPDATE reservation SET date=?,rotation=?,updated_at=? WHERE id=?",
            (new_date, new_rotation, now(), rid),
        )
        conn.commit()
        return True, ""
