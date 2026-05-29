"""Admin router: calendar, day edit, reservation ops, settings."""
from __future__ import annotations
import calendar as cal_mod
import logging
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import models, services
from app.auth import require_admin
from app.config import SHOP_NAME, CALENDAR_START, CALENDAR_END, get_time_slots

router = APIRouter(dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_SLOTS = get_time_slots()
_COMMON = {"shop_name": SHOP_NAME}
_WEEKDAY_NAMES = ["月", "火", "水", "木", "金", "土", "日"]


def _tpl(name: str, request: Request, **ctx):
    return templates.TemplateResponse(request, name, {**_COMMON, **ctx})


# ── Maintenance ───────────────────────────────────────────────────────────────

@router.post("/maintenance/cleanup-pending", response_class=HTMLResponse)
async def maintenance_cleanup(request: Request):
    """Delete expired pending_verify reservations. For cron use."""
    deleted = models.cleanup_expired_pending()
    logger.info("Cleanup: deleted %d expired pending_verify reservations", deleted)
    return JSONResponse({"deleted": deleted})


# ── Calendar ──────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_home(request: Request):
    today = date.today()
    return RedirectResponse(f"/kmb/admin/calendar/{today.year}/{today.month}")


@router.get("/calendar/{year}/{month}", response_class=HTMLResponse)
async def admin_calendar(request: Request, year: int, month: int):
    days  = services.build_admin_day_list(year, month)
    nav   = services.month_nav(year, month)
    cal_start = date.fromisoformat(CALENDAR_START)
    cal_end   = date.fromisoformat(CALENDAR_END)
    prev_d = date(nav["prev_year"], nav["prev_month"], 1)
    next_d = date(nav["next_year"], nav["next_month"], 1)
    return _tpl("admin/calendar.html", request,
                days=days, nav=nav,
                show_prev=prev_d >= date(cal_start.year, cal_start.month, 1),
                show_next=next_d <= date(cal_end.year, cal_end.month, 1),
                year=year, month=month)


# ── Day Edit ──────────────────────────────────────────────────────────────────

@router.get("/day/{target_date}", response_class=HTMLResponse)
async def day_edit(request: Request, target_date: str, msg: Optional[str] = None):
    all_defaults = models.get_all_defaults()
    dc  = models.get_day_config(target_date)
    cfg = models.get_effective_config(target_date, all_defaults)
    reservations = models.list_reservations_for_date(target_date)
    surveys  = models.get_surveys_for_date(target_date)
    weekday_name = _WEEKDAY_NAMES[date.fromisoformat(target_date).weekday()]
    is_past = date.fromisoformat(target_date) < date.today()
    return _tpl("admin/day_edit.html", request,
                target_date=target_date, weekday_name=weekday_name,
                dc=dc, cfg=cfg, is_past=is_past,
                reservations=reservations, surveys=surveys,
                slots=_SLOTS, msg=msg, error=None)


@router.post("/day/{target_date}", response_class=HTMLResponse)
async def day_edit_save(request: Request, target_date: str):
    if date.fromisoformat(target_date) < date.today():
        return RedirectResponse(
            f"/kmb/admin/day/{target_date}?msg=過去の日付は変更できません",
            status_code=303,
        )
    form = await request.form()
    closed        = form.get("is_closed") == "1"
    course        = (form.get("course") or "").strip() or None
    start_time_1  = form.get("start_time_1") or None
    capacity_1    = form.get("capacity_1")
    start_time_2  = form.get("start_time_2") or None
    capacity_2    = form.get("capacity_2")

    errors = []

    for rot, cap_str in ((1, capacity_1), (2, capacity_2 if start_time_2 else None)):
        if cap_str is None:
            continue
        try:
            cap = int(cap_str)
        except ValueError:
            errors.append(f"席数{rot}は整数で入力してください")
            continue
        inv = models.get_inventory_bulk([target_date]).get(
            (target_date, rot), {"booked": 0}
        )
        if cap < inv["booked"]:
            errors.append(
                f"回転{rot}の席数（{cap}）は現在の予約数（{inv['booked']}人）を"
                "下回るため設定できません"
            )

    if closed and models.count_active_for_date(target_date) > 0:
        errors.append(f"{target_date} は予約があるため定休日に設定できません")

    if errors:
        all_defaults = models.get_all_defaults()
        dc  = models.get_day_config(target_date)
        cfg = models.get_effective_config(target_date, all_defaults)
        reservations = models.list_reservations_for_date(target_date)
        weekday_name = _WEEKDAY_NAMES[date.fromisoformat(target_date).weekday()]
        return _tpl("admin/day_edit.html", request,
                    target_date=target_date, weekday_name=weekday_name,
                    dc=dc, cfg=cfg,
                    reservations=reservations, slots=_SLOTS,
                    msg=None, error=" / ".join(errors))

    models.upsert_day_config(
        target_date,
        is_closed=1 if closed else 0,
        is_manual_override=1,
        course=course,
        start_time_1=start_time_1,
        capacity_1=int(capacity_1) if capacity_1 else None,
        start_time_2=start_time_2,
        capacity_2=int(capacity_2) if capacity_2 and start_time_2 else None,
    )
    return RedirectResponse(
        f"/kmb/admin/day/{target_date}?msg=保存しました", status_code=303
    )


# ── Reservation operations ────────────────────────────────────────────────────

@router.get("/api/day/{target_date}/config")
async def day_config_api(target_date: str):
    """Return rotation config for this date (for JS use in move form)."""
    all_defaults = models.get_all_defaults()
    cfg = models.get_effective_config(target_date, all_defaults)
    wd = _WEEKDAY_NAMES[date.fromisoformat(target_date).weekday()]
    return JSONResponse({
        "weekday":       wd,
        "has_rotation_2": cfg.get("start_time_2") is not None,
        "start_time_1":  cfg.get("start_time_1"),
        "start_time_2":  cfg.get("start_time_2"),
    })


@router.post("/day/{target_date}/reservation", response_class=HTMLResponse)
async def admin_add_reservation(request: Request, target_date: str):
    """Admin manually adds a reservation (phone-received) directly as active."""
    form = await request.form()
    name  = (form.get("name")  or "").strip()
    phone = (form.get("phone") or "").strip()
    email = (form.get("email") or "").strip()
    note  = (form.get("note")  or "").strip() or None

    errors = []
    if not name:
        errors.append("名前は必須です")
    if not phone:
        errors.append("電話番号は必須です")
    try:
        num_people = int(form.get("num_people") or 1)
        if num_people < 1:
            raise ValueError
    except ValueError:
        errors.append("人数を正しく入力してください")
        num_people = 1
    try:
        rotation = int(form.get("rotation") or 1)
    except ValueError:
        rotation = 1

    if not errors:
        rid = models.admin_create_reservation(
            target_date, rotation, name, num_people, phone, email, note
        )
        if rid is None:
            errors.append("席数が不足しています")

    msg = "予約を追加しました" if not errors else "　".join(errors)
    return RedirectResponse(
        f"/kmb/admin/day/{target_date}?msg={msg}", status_code=303
    )


@router.post("/reservation/{rid}/activate", response_class=HTMLResponse)
async def reservation_activate(request: Request, rid: int):
    """Admin manually activates a pending_verify reservation."""
    form = await request.form()
    models.admin_activate_reservation(rid)
    return RedirectResponse(f"/kmb/admin/day/{form['back_date']}", status_code=303)


@router.post("/reservation/{rid}/confirm", response_class=HTMLResponse)
async def reservation_confirm(request: Request, rid: int):
    form = await request.form()
    models.set_confirmed(rid, int(form["confirmed"]))
    return RedirectResponse(f"/kmb/admin/day/{form['back_date']}", status_code=303)


@router.post("/reservation/{rid}/cancel", response_class=HTMLResponse)
async def reservation_cancel(request: Request, rid: int):
    form = await request.form()
    models.cancel_reservation(rid)
    return RedirectResponse(f"/kmb/admin/day/{form['back_date']}", status_code=303)


@router.post("/reservation/{rid}/move", response_class=HTMLResponse)
async def reservation_move(request: Request, rid: int):
    form = await request.form()
    ok, err = models.move_reservation(
        rid, str(form["new_date"]), int(form["new_rotation"])
    )
    if not ok:
        return RedirectResponse(
            f"/kmb/admin/day/{form['back_date']}?msg={err}", status_code=303
        )
    return RedirectResponse(f"/kmb/admin/day/{form['new_date']}", status_code=303)


# ── Settings ──────────────────────────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request, msg: Optional[str] = None):
    all_defaults = models.get_all_defaults()
    today = date.today()
    weeks = _holiday_calendar(today.year, today.month)
    nav   = services.month_nav(today.year, today.month)
    return _tpl("admin/settings.html", request,
                all_defaults=all_defaults, slots=_SLOTS,
                weekday_names=_WEEKDAY_NAMES,
                weeks=weeks, nav=nav,
                year=today.year, month=today.month, msg=msg)


@router.get("/settings/calendar/{year}/{month}", response_class=HTMLResponse)
async def settings_calendar(request: Request, year: int, month: int,
                              msg: Optional[str] = None):
    all_defaults = models.get_all_defaults()
    weeks = _holiday_calendar(year, month)
    nav   = services.month_nav(year, month)
    return _tpl("admin/settings.html", request,
                all_defaults=all_defaults, slots=_SLOTS,
                weekday_names=_WEEKDAY_NAMES,
                weeks=weeks, nav=nav, year=year, month=month, msg=msg)


@router.post("/settings", response_class=HTMLResponse)
async def settings_post(request: Request):
    form = await request.form()
    course = (form.get("course") or "").strip() or None

    weekday_settings = []
    for wd in range(7):
        cap1 = form.get(f"wd_{wd}_capacity_1")
        cap2 = form.get(f"wd_{wd}_capacity_2")
        weekday_settings.append({
            "weekday":      wd,
            "is_closed":    1 if form.get(f"wd_{wd}_is_closed") == "1" else 0,
            "start_time_1": form.get(f"wd_{wd}_start_time_1") or None,
            "capacity_1":   int(cap1) if cap1 else None,
            "start_time_2": form.get(f"wd_{wd}_start_time_2") or None,
            "capacity_2":   int(cap2) if cap2 else None,
        })

    old_all_defaults = models.get_all_defaults()
    protected = services.apply_default_propagation(old_all_defaults)
    models.save_defaults(course)
    models.save_weekday_defaults(weekday_settings)

    today = date.today()
    msg = "保存しました。"
    if protected:
        msg += "、".join(protected) + " は予約があるため変更されませんでした。"
    return RedirectResponse(
        f"/kmb/admin/settings/calendar/{today.year}/{today.month}?msg={msg}",
        status_code=303,
    )


@router.post("/holiday", response_class=HTMLResponse)
async def holiday_toggle(request: Request):
    form = await request.form()
    target_date = str(form["target_date"])
    is_closed   = int(form["is_closed"])
    year        = int(form["year"])
    month       = int(form["month"])

    if is_closed:
        count = models.count_active_for_date(target_date)
        if count > 0:
            msg = f"{target_date} は {count} 名の予約があるため休日にできません"
            return RedirectResponse(
                f"/kmb/admin/settings/calendar/{year}/{month}?msg={msg}",
                status_code=303,
            )

    dc = models.get_day_config(target_date)
    models.upsert_day_config(
        target_date,
        is_closed=is_closed,
        is_manual_override=dc["is_manual_override"] if dc else 0,
        course=dc.get("course") if dc else None,
        start_time_1=dc["start_time_1"] if dc else None,
        capacity_1=dc["capacity_1"] if dc else None,
        start_time_2=dc["start_time_2"] if dc else None,
        capacity_2=dc["capacity_2"] if dc else None,
    )
    return RedirectResponse(
        f"/kmb/admin/settings/calendar/{year}/{month}", status_code=303
    )


def _holiday_calendar(year: int, month: int) -> list:
    all_defaults = models.get_all_defaults()
    day_cfgs     = models.get_all_day_configs()
    weeks = []
    for week in cal_mod.monthcalendar(year, month):
        row = []
        for d_num in week:
            if d_num == 0:
                row.append(None)
                continue
            d_str = date(year, month, d_num).isoformat()
            cfg   = models.get_effective_config(d_str, all_defaults, day_cfgs)
            count = models.count_active_for_date(d_str)
            row.append({
                "date": d_str, "day": d_num,
                "is_closed": cfg["is_closed"],
                "has_reservations": count > 0,
            })
        weeks.append(row)
    return weeks
