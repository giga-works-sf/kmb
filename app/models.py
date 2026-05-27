"""All SQLite CRUD operations. No business logic — just read/write."""
from __future__ import annotations
from typing import Optional
from app.db import get_conn, now


# ── Defaults ──────────────────────────────────────────────────────────────────

def get_defaults() -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM defaults WHERE id=1").fetchone()
    return dict(row)


def save_defaults(
    course_1: Optional[str],
    course_2: Optional[str],
    course_3: Optional[str],
    start_time_1: str,
    capacity_1: int,
) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE defaults SET course_1=?,course_2=?,course_3=?,"
            "start_time_1=?,capacity_1=?,updated_at=? WHERE id=1",
            (course_1 or None, course_2 or None, course_3 or None,
             start_time_1, capacity_1, now()),
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
    course_1: Optional[str] = None,
    course_2: Optional[str] = None,
    course_3: Optional[str] = None,
    start_time_1: Optional[str] = None,
    capacity_1: Optional[int] = None,
    start_time_2: Optional[str] = None,
    capacity_2: Optional[int] = None,
    conn=None,
) -> None:
    sql = """
        INSERT INTO day_config (date,is_closed,is_manual_override,course_1,course_2,course_3,
            start_time_1,capacity_1,start_time_2,capacity_2,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            is_closed=excluded.is_closed,
            is_manual_override=excluded.is_manual_override,
            course_1=excluded.course_1, course_2=excluded.course_2,
            course_3=excluded.course_3, start_time_1=excluded.start_time_1,
            capacity_1=excluded.capacity_1, start_time_2=excluded.start_time_2,
            capacity_2=excluded.capacity_2, updated_at=excluded.updated_at
    """
    params = (date, is_closed, is_manual_override,
              course_1 or None, course_2 or None, course_3 or None,
              start_time_1, capacity_1, start_time_2 or None,
              capacity_2, now())
    if conn is not None:
        conn.execute(sql, params)
    else:
        with get_conn() as c:
            c.execute(sql, params)
            c.commit()


def get_effective_config(date: str, defaults: dict,
                          day_configs: Optional[dict[str, dict]] = None) -> dict:
    """Merge day_config row (if exists) with defaults. Returns resolved config dict."""
    dc = day_configs.get(date) if day_configs is not None else get_day_config(date)

    def _pick(dc_val, default_val):
        return dc_val if dc_val is not None else default_val

    if dc is None:
        return {
            "course_1": defaults["course_1"],
            "course_2": defaults["course_2"],
            "course_3": defaults["course_3"],
            "start_time_1": defaults["start_time_1"],
            "capacity_1": defaults["capacity_1"],
            "start_time_2": None,
            "capacity_2": None,
            "is_closed": False,
            "is_manual_override": False,
        }
    return {
        "course_1":          _pick(dc["course_1"],   defaults["course_1"]),
        "course_2":          _pick(dc["course_2"],   defaults["course_2"]),
        "course_3":          _pick(dc["course_3"],   defaults["course_3"]),
        "start_time_1":      _pick(dc["start_time_1"], defaults["start_time_1"]),
        "capacity_1":        _pick(dc["capacity_1"], defaults["capacity_1"]),
        "start_time_2":      dc["start_time_2"],
        "capacity_2":        dc["capacity_2"],
        "is_closed":         bool(dc["is_closed"]),
        "is_manual_override": bool(dc["is_manual_override"]),
    }


# ── Reservations ──────────────────────────────────────────────────────────────

def get_inventory_bulk(dates: list[str]) -> dict[tuple[str, int], dict]:
    """One query: returns {(date, rotation): {booked, confirmed_count}} for all dates."""
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


def create_reservation(
    date: str, rotation: int, name: str, num_people: int,
    phone: str, email: str, note: Optional[str],
) -> Optional[int]:
    """Insert reservation inside a transaction with re-check. Returns id or None if capacity exceeded."""
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Re-read capacity inside transaction
        defaults = dict(conn.execute("SELECT * FROM defaults WHERE id=1").fetchone())
        dc_row = conn.execute("SELECT * FROM day_config WHERE date=?", (date,)).fetchone()
        dc = dict(dc_row) if dc_row else None

        if rotation == 1:
            cap = (dc["capacity_1"] if dc and dc["capacity_1"] is not None
                   else defaults["capacity_1"])
        else:
            cap = dc["capacity_2"] if dc and dc["capacity_2"] is not None else 0

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
            "INSERT INTO reservation (date,rotation,name,num_people,phone,email,note,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (date, rotation, name, num_people, phone, email,
             note or None, ts, ts),
        )
        rid = cur.lastrowid
        conn.commit()
        return rid


def get_reservation(rid: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM reservation WHERE id=?", (rid,)).fetchone()
    return dict(row) if row else None


def list_reservations_for_date(date: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reservation WHERE date=? AND status='active' "
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
    """Move reservation with inventory re-check. Returns (ok, error_msg)."""
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        res = conn.execute("SELECT * FROM reservation WHERE id=?", (rid,)).fetchone()
        if not res or res["status"] != "active":
            conn.execute("ROLLBACK")
            return False, "予約が見つかりません"

        defaults = dict(conn.execute("SELECT * FROM defaults WHERE id=1").fetchone())
        dc_row = conn.execute(
            "SELECT * FROM day_config WHERE date=?", (new_date,)
        ).fetchone()
        dc = dict(dc_row) if dc_row else None

        if new_rotation == 1:
            cap = (dc["capacity_1"] if dc and dc["capacity_1"] is not None
                   else defaults["capacity_1"])
        else:
            cap = dc["capacity_2"] if dc and dc["capacity_2"] is not None else 0

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
