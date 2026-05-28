"""Business logic: calendar data, booking window, default propagation."""
from __future__ import annotations
import calendar as cal_mod
from datetime import date, timedelta
from typing import Optional

from dateutil.relativedelta import relativedelta

from app import models
from app.config import CALENDAR_START, CALENDAR_END, BOOKING_AHEAD_MONTHS


def get_booking_window(today: Optional[date] = None) -> tuple[date, date]:
    if today is None:
        today = date.today()
    cal_start = date.fromisoformat(CALENDAR_START)
    cal_end   = date.fromisoformat(CALENDAR_END)
    # 当日・翌日はTel対応のため、オンライン予約は明後日から
    earliest = max(today + timedelta(days=2), cal_start)
    latest   = min(today + relativedelta(months=BOOKING_AHEAD_MONTHS), cal_end)
    return earliest, latest


def build_customer_calendar(year: int, month: int,
                              today: Optional[date] = None) -> list[list[Optional[dict]]]:
    if today is None:
        today = date.today()
    earliest, latest = get_booking_window(today)
    all_defaults = models.get_all_defaults()
    day_cfgs     = models.get_all_day_configs()

    month_dates = [
        date(year, month, d).isoformat()
        for d in range(1, cal_mod.monthrange(year, month)[1] + 1)
    ]
    inventory = models.get_inventory_bulk(month_dates)

    weeks = []
    for week in cal_mod.monthcalendar(year, month):
        row = []
        for day_num in week:
            if day_num == 0:
                row.append(None)
                continue
            d = date(year, month, day_num)
            cfg = models.get_effective_config(d.isoformat(), all_defaults, day_cfgs)
            row.append(_customer_cell(d, cfg, inventory, earliest, latest, today))
        weeks.append(row)
    return weeks


def _customer_cell(d: date, cfg: dict, inventory: dict,
                    earliest: date, latest: date, today: date) -> dict:
    cell: dict = {"date": d.isoformat(), "day": d.day,
                  "kind": "normal", "display": "", "bookable_rotations": []}

    # 過去の日付: グレーアウト ✕
    if d < today:
        cell.update(kind="past", display="✕")
        return cell

    # 当日・翌日: Tel のみ
    if d <= today + timedelta(days=1):
        cell.update(kind="tel", display="Tel")
        return cell

    if d < earliest or d > latest:
        cell.update(kind="out_of_range", display="")
        return cell

    if cfg["is_closed"]:
        cell.update(kind="closed", display="☓")
        return cell

    available_times = []
    for rot in (1, 2):
        t = cfg["start_time_1"] if rot == 1 else cfg["start_time_2"]
        c = cfg["capacity_1"]   if rot == 1 else cfg["capacity_2"]
        if t is None or not c:
            continue
        inv = inventory.get((d.isoformat(), rot), {"booked": 0})
        if c - inv["booked"] > 0:
            available_times.append(f"{t}-")
            cell["bookable_rotations"].append(rot)

    if not available_times:
        cell.update(kind="full", display="☓")
        return cell

    cell.update(kind="available", display=" / ".join(available_times))
    return cell


_WEEKDAY_NAMES = ["月", "火", "水", "木", "金", "土", "日"]


def build_admin_day_list(year: int, month: int) -> list[dict]:
    """Return a flat list of day dicts with per-seat reservation details."""
    today = date.today()
    all_defaults = models.get_all_defaults()
    day_cfgs     = models.get_all_day_configs()
    res_by_date  = models.list_reservations_for_month(year, month)

    days = []
    for day_num in range(1, cal_mod.monthrange(year, month)[1] + 1):
        d     = date(year, month, day_num)
        d_str = d.isoformat()
        cfg   = models.get_effective_config(d_str, all_defaults, day_cfgs)

        all_res = res_by_date.get(d_str, [])
        active  = [r for r in all_res if r["status"] == "active"]
        pending = [r for r in all_res if r["status"] == "pending_verify"]

        rotations = []
        if not cfg["is_closed"]:
            for rot in (1, 2):
                t = cfg["start_time_1"] if rot == 1 else cfg["start_time_2"]
                c = cfg["capacity_1"]   if rot == 1 else cfg["capacity_2"]
                if t is None or not c:
                    continue

                rot_active  = [r for r in active  if r["rotation"] == rot]
                rot_pending = [r for r in pending if r["rotation"] == rot]

                # Expand reservations to individual seat rows
                seats: list[dict] = []
                seat_num = 0
                for res in rot_active:
                    seat_num += 1
                    seats.append({
                        "seat_num": seat_num, "type": "occupied",
                        "name": res["name"], "is_leader": True,
                        "num_people": res["num_people"],
                        "confirmed": bool(res["confirmed"]),
                        "res_id": res["id"],
                    })
                    for _ in range(res["num_people"] - 1):
                        seat_num += 1
                        seats.append({
                            "seat_num": seat_num, "type": "occupied",
                            "name": res["name"], "is_leader": False,
                            "confirmed": bool(res["confirmed"]),
                            "res_id": res["id"],
                        })
                for _ in range(max(0, c - seat_num)):
                    seat_num += 1
                    seats.append({"seat_num": seat_num, "type": "empty"})

                rotations.append({
                    "rotation": rot, "time": t, "capacity": c,
                    "seats": seats, "pending": rot_pending,
                })

        days.append({
            "date": d_str,
            "weekday": _WEEKDAY_NAMES[d.weekday()],
            "is_past":   d < today,
            "is_today":  d == today,
            "is_closed": cfg["is_closed"],
            "rotations": rotations,
        })
    return days


def build_admin_calendar(year: int, month: int) -> list[list[Optional[dict]]]:
    all_defaults = models.get_all_defaults()
    day_cfgs     = models.get_all_day_configs()

    month_dates = [
        date(year, month, d).isoformat()
        for d in range(1, cal_mod.monthrange(year, month)[1] + 1)
    ]
    inventory = models.get_inventory_bulk(month_dates)

    weeks = []
    for week in cal_mod.monthcalendar(year, month):
        row = []
        for day_num in week:
            if day_num == 0:
                row.append(None)
                continue
            d = date(year, month, day_num)
            cfg = models.get_effective_config(d.isoformat(), all_defaults, day_cfgs)
            row.append(_admin_cell(d, cfg, inventory))
        weeks.append(row)
    return weeks


def _admin_cell(d: date, cfg: dict, inventory: dict) -> dict:
    cell: dict = {
        "date": d.isoformat(), "day": d.day,
        "is_closed": cfg["is_closed"],
        "is_manual_override": cfg["is_manual_override"],
        "is_past": d < date.today(),
        "rotations": [], "color": "neutral",
    }

    if cfg["is_closed"]:
        return cell

    total_booked = total_confirmed = 0
    for rot in (1, 2):
        t = cfg["start_time_1"] if rot == 1 else cfg["start_time_2"]
        c = cfg["capacity_1"]   if rot == 1 else cfg["capacity_2"]
        if t is None:
            continue
        inv = inventory.get((d.isoformat(), rot),
                            {"booked": 0, "confirmed_count": 0, "pending_count": 0})
        b, conf, pend = inv["booked"], inv["confirmed_count"], inv["pending_count"]
        total_booked += b
        total_confirmed += conf
        cell["rotations"].append(
            {"rotation": rot, "time": t, "capacity": c,
             "booked": b, "confirmed": conf, "pending": pend}
        )

    if total_booked > 0:
        cell["color"] = "green" if total_confirmed == total_booked else "red"
    return cell


def apply_default_propagation(old_all_defaults: dict) -> list[str]:
    """Snapshot effective config for future reserved dates not already overridden.
    Call BEFORE saving new defaults. Returns list of protected date strings."""
    from app.db import get_conn, now as _now

    today = date.today()
    cal_end = date.fromisoformat(CALENDAR_END)
    protected: list[str] = []

    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")

        rows = conn.execute(
            "SELECT DISTINCT date FROM reservation "
            "WHERE status='active' AND date > ?",
            (today.isoformat(),),
        ).fetchall()
        future_reserved = {r["date"] for r in rows}

        if future_reserved:
            ph = ",".join("?" * len(future_reserved))
            dc_rows = conn.execute(
                f"SELECT * FROM day_config WHERE date IN ({ph})",
                list(future_reserved),
            ).fetchall()
            dc_map = {r["date"]: dict(r) for r in dc_rows}
        else:
            dc_map = {}

        for date_str in future_reserved:
            d = date.fromisoformat(date_str)
            if d > cal_end:
                continue
            dc = dc_map.get(date_str)
            if dc and dc["is_manual_override"]:
                continue

            wd = old_all_defaults["weekday"].get(d.weekday(), {})

            def _pick(dc_v, wd_v, _dc=dc):
                return dc_v if _dc and dc_v is not None else wd_v

            models.upsert_day_config(
                date_str,
                is_closed=dc["is_closed"] if dc else wd.get("is_closed", 0),
                is_manual_override=0,
                course=_pick(dc.get("course") if dc else None, old_all_defaults["course"]),
                start_time_1=_pick(dc["start_time_1"] if dc else None, wd.get("start_time_1")),
                capacity_1=_pick(dc["capacity_1"] if dc else None, wd.get("capacity_1")),
                start_time_2=_pick(dc["start_time_2"] if dc else None, wd.get("start_time_2")),
                capacity_2=_pick(dc["capacity_2"] if dc else None, wd.get("capacity_2")),
                conn=conn,
            )
            protected.append(date_str)

        conn.commit()
    return sorted(protected)


def month_nav(year: int, month: int) -> dict:
    prev = date(year, month, 1) - timedelta(days=1)
    nxt  = date(year, month, cal_mod.monthrange(year, month)[1]) + timedelta(days=1)
    return {
        "prev_year": prev.year, "prev_month": prev.month,
        "next_year": nxt.year,  "next_month": nxt.month,
        "label": f"{year}年{month}月",
    }
