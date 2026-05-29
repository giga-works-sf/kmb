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
                    SUM(CASE WHEN status='active' THEN num_people ELSE 0 END) AS booked,
                    SUM(CASE WHEN status='active' AND confirmed=1 THEN num_people ELSE 0 END) AS confirmed_count,
                    SUM(CASE WHEN status='pending_verify' THEN num_people ELSE 0 END) AS pending_count
                FROM reservation
                WHERE date IN ({placeholders}) AND status IN ('active','pending_verify')
                GROUP BY date, rotation""",
            dates,
        ).fetchall()
    return {(r["date"], r["rotation"]): {
                "booked":          r["booked"],
                "confirmed_count": r["confirmed_count"],
                "pending_count":   r["pending_count"],
            }
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
) -> Optional[int]:
    """Insert reservation (pending_verify) inside a transaction with re-check.
    Returns reservation id or None if capacity exceeded.
    Phone should be in E.164 format (+819012345678).
    OTP is stored separately via store_sms_otp()."""
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
            "status,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (date, rotation, name, num_people, phone, email, note or None,
             "pending_verify", ts, ts),
        )
        rid = cur.lastrowid
        conn.commit()
        return rid


def store_verification_token(rid: int) -> str:
    """Generate and store email verification token (10min expiry). Returns token."""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE reservation SET verification_token=?, token_expires_at=?, updated_at=? WHERE id=?",
            (token, expires, now(), rid),
        )
        conn.commit()
    return token


def store_sms_otp(rid: int, phone_e164: str, dev_code: Optional[str]) -> None:
    """Store E.164 phone and OTP info after reservation creation.
    dev_code is the plaintext OTP in dev mode (None in production).
    Sets token_expires_at to 10 minutes from now."""
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE reservation SET phone=?, verification_token=?, token_expires_at=?, updated_at=? "
            "WHERE id=?",
            (phone_e164, dev_code, expires, now(), rid),
        )
        conn.commit()


def activate_reservation(rid: int) -> Optional[dict]:
    """Activate a pending_verify reservation after successful SMS OTP verification.
    Returns the activated reservation dict, or None if not found / expired."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM reservation WHERE id=? AND status='pending_verify'", (rid,)
        ).fetchone()
        if not row:
            return None
        res = dict(row)
        if res.get("token_expires_at"):
            expires = datetime.strptime(
                res["token_expires_at"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires:
                return None
        conn.execute(
            "UPDATE reservation SET status='active', verification_token=NULL, "
            "token_expires_at=NULL, updated_at=? WHERE id=?",
            (now(), rid),
        )
        conn.commit()
        res["status"] = "active"
        return res


def activate_by_token(token: str) -> Optional[dict]:
    """Verify email token and activate reservation. Returns reservation dict or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM reservation WHERE verification_token=? AND status='pending_verify'",
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
            "UPDATE reservation SET status='active', verification_token=NULL, "
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


def list_reservations_for_month(year: int, month: int) -> dict[str, list[dict]]:
    """Returns {date_str: [reservations]} for all active+pending_verify in a month."""
    import calendar as _cal
    last_day = _cal.monthrange(year, month)[1]
    start = f"{year:04d}-{month:02d}-01"
    end   = f"{year:04d}-{month:02d}-{last_day:02d}"
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reservation WHERE date BETWEEN ? AND ? "
            "AND status IN ('active','pending_verify') "
            "ORDER BY date, rotation, created_at",
            (start, end),
        ).fetchall()
    result: dict[str, list[dict]] = {}
    for row in rows:
        d = dict(row)
        result.setdefault(d["date"], []).append(d)
    return result


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


def admin_create_reservation(
    date: str, rotation: int, name: str, num_people: int,
    phone: str, email: str, note: Optional[str],
) -> Optional[int]:
    """Admin manually creates a reservation directly as 'active' (phone-received).
    Returns reservation id, or None if capacity exceeded."""
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
            "status,confirmed,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (date, rotation, name, num_people, phone, email or "",
             note or None, "active", 0, ts, ts),
        )
        rid = cur.lastrowid
        conn.commit()
        return rid


def admin_activate_reservation(rid: int) -> None:
    """Admin manually activates a pending_verify reservation (no token needed)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE reservation SET status='active', verification_token=NULL, "
            "token_expires_at=NULL, updated_at=? "
            "WHERE id=? AND status='pending_verify'",
            (now(), rid),
        )
        conn.commit()


def set_confirmed(rid: int, confirmed: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE reservation SET confirmed=?,updated_at=? WHERE id=?",
            (confirmed, now(), rid),
        )
        conn.commit()


# ── Survey ────────────────────────────────────────────────────────────────────

def save_survey(rid: int, data: dict) -> None:
    """Upsert survey response for a reservation."""
    ts = now()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO survey_response
                (reservation_id, source, source_other, visit_count, is_member,
                 looking_forward, allergy, disliked_food, nonalcoholic_count,
                 info_preference, info_other, other_questions,
                 payment_method, transfer_name, terms_agreed,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(reservation_id) DO UPDATE SET
                source=excluded.source,
                source_other=excluded.source_other,
                visit_count=excluded.visit_count,
                is_member=excluded.is_member,
                looking_forward=excluded.looking_forward,
                allergy=excluded.allergy,
                disliked_food=excluded.disliked_food,
                nonalcoholic_count=excluded.nonalcoholic_count,
                info_preference=excluded.info_preference,
                info_other=excluded.info_other,
                other_questions=excluded.other_questions,
                payment_method=excluded.payment_method,
                transfer_name=excluded.transfer_name,
                terms_agreed=excluded.terms_agreed,
                updated_at=excluded.updated_at
        """, (
            rid,
            data.get("source"), data.get("source_other"),
            data.get("visit_count"),
            int(data.get("is_member") or 0),
            data.get("looking_forward") or None,
            data.get("allergy") or None,
            data.get("disliked_food") or None,
            int(data.get("nonalcoholic_count") or 0),
            data.get("info_preference"), data.get("info_other"),
            data.get("other_questions") or None,
            data.get("payment_method"),
            data.get("transfer_name") or None,
            int(data.get("terms_agreed") or 0),
            ts, ts,
        ))
        conn.commit()


def get_survey(rid: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM survey_response WHERE reservation_id=?", (rid,)
        ).fetchone()
    return dict(row) if row else None


def get_surveys_for_date(date_str: str) -> dict[int, dict]:
    """Returns {reservation_id: survey_dict} for all surveys on a given date."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT sr.* FROM survey_response sr
               JOIN reservation r ON sr.reservation_id = r.id
               WHERE r.date = ?""",
            (date_str,),
        ).fetchall()
    return {r["reservation_id"]: dict(r) for r in rows}


RATE_LIMIT_EXCLUDE = {"27.84.160.69"}
RATE_LIMIT_PER_DAY = 5


def check_rate_limit(ip: str) -> bool:
    """Return True if within daily limit, False if exceeded. Increments counter.
    IPs in RATE_LIMIT_EXCLUDE are always allowed."""
    if ip in RATE_LIMIT_EXCLUDE:
        return True
    today = _date_cls.today().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT count FROM rate_limit WHERE ip=? AND date=?", (ip, today)
        ).fetchone()
        if row:
            if row["count"] >= RATE_LIMIT_PER_DAY:
                return False
            conn.execute(
                "UPDATE rate_limit SET count=count+1 WHERE ip=? AND date=?",
                (ip, today),
            )
        else:
            conn.execute(
                "INSERT INTO rate_limit (ip, date, count) VALUES (?,?,1)", (ip, today)
            )
        conn.commit()
    return True


def cleanup_expired_pending() -> int:
    """Delete pending_verify reservations past their verification deadline.
    Safe to call from cron. Returns number of rows deleted."""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM reservation "
            "WHERE status='pending_verify' "
            "AND (token_expires_at IS NULL OR token_expires_at < ?)",
            (now(),),
        )
        conn.commit()
        return cur.rowcount


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
        if not res or res["status"] not in ("active", "pending_verify"):
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
