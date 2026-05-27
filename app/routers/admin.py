"""Admin router: calendar, day edit, reservation ops, settings (stubs for Task 9)."""
from __future__ import annotations
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import models, services
from app.auth import require_admin
from app.config import SHOP_NAME, CALENDAR_START, CALENDAR_END, get_time_slots

router = APIRouter(dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_SLOTS = get_time_slots()
_COMMON = {"shop_name": SHOP_NAME}


def _tpl(name: str, request: Request, **ctx):
    return templates.TemplateResponse(request, name, {**_COMMON, **ctx})


# ── Calendar ──────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_home(request: Request):
    today = date.today()
    return RedirectResponse(f"/kmb/admin/calendar/{today.year}/{today.month}")


@router.get("/calendar/{year}/{month}", response_class=HTMLResponse)
async def admin_calendar(request: Request, year: int, month: int):
    weeks = services.build_admin_calendar(year, month)
    nav   = services.month_nav(year, month)
    cal_start = date.fromisoformat(CALENDAR_START)
    cal_end   = date.fromisoformat(CALENDAR_END)
    prev_d = date(nav["prev_year"], nav["prev_month"], 1)
    next_d = date(nav["next_year"], nav["next_month"], 1)
    return _tpl("admin/calendar.html", request,
                weeks=weeks, nav=nav,
                show_prev=prev_d >= date(cal_start.year, cal_start.month, 1),
                show_next=next_d <= date(cal_end.year, cal_end.month, 1),
                year=year, month=month)


# ── Day Edit ──────────────────────────────────────────────────────────────────

@router.get("/day/{target_date}", response_class=HTMLResponse)
async def day_edit(request: Request, target_date: str, msg: Optional[str] = None):
    defaults = models.get_defaults()
    dc = models.get_day_config(target_date)
    cfg = models.get_effective_config(target_date, defaults)
    reservations = models.list_reservations_for_date(target_date)
    return _tpl("admin/day_edit.html", request,
                target_date=target_date, dc=dc, cfg=cfg,
                defaults=defaults, reservations=reservations,
                slots=_SLOTS, msg=msg, error=None)


@router.post("/day/{target_date}", response_class=HTMLResponse)
async def day_edit_save(
    request: Request,
    target_date: str,
    is_closed: Optional[str]   = Form(None),
    course_1: str               = Form(""),
    course_2: str               = Form(""),
    course_3: str               = Form(""),
    start_time_1: str           = Form(""),
    capacity_1: Optional[str]   = Form(None),
    start_time_2: str           = Form(""),
    capacity_2: Optional[str]   = Form(None),
):
    closed = is_closed == "1"
    errors = []

    # Validate capacity vs existing reservations
    for rot, cap_str in ((1, capacity_1), (2, capacity_2 if start_time_2 else None)):
        if cap_str is None:
            continue
        try:
            cap = int(cap_str)
        except ValueError:
            errors.append(f"席数{rot}は整数で入力してください")
            continue
        inv = models.get_inventory_bulk([target_date]).get((target_date, rot), {"booked": 0})
        if cap < inv["booked"]:
            errors.append(
                f"回転{rot}の席数（{cap}）は現在の予約数（{inv['booked']}人）を下回るため設定できません"
            )

    if closed and models.count_active_for_date(target_date) > 0:
        errors.append(f"{target_date} は予約があるため定休日に設定できません")

    if errors:
        defaults = models.get_defaults()
        dc = models.get_day_config(target_date)
        cfg = models.get_effective_config(target_date, defaults)
        reservations = models.list_reservations_for_date(target_date)
        return _tpl("admin/day_edit.html", request,
                    target_date=target_date, dc=dc, cfg=cfg, defaults=defaults,
                    reservations=reservations, slots=_SLOTS,
                    msg=None, error=" / ".join(errors))

    models.upsert_day_config(
        target_date,
        is_closed=1 if closed else 0,
        is_manual_override=1,
        course_1=course_1.strip() or None,
        course_2=course_2.strip() or None,
        course_3=course_3.strip() or None,
        start_time_1=start_time_1 or None,
        capacity_1=int(capacity_1) if capacity_1 else None,
        start_time_2=start_time_2.strip() or None,
        capacity_2=int(capacity_2) if capacity_2 and start_time_2 else None,
    )
    return RedirectResponse(f"/kmb/admin/day/{target_date}?msg=保存しました", status_code=303)


# ── Reservation operations ────────────────────────────────────────────────────

@router.post("/reservation/{rid}/confirm", response_class=HTMLResponse)
async def reservation_confirm(
    request: Request, rid: int,
    confirmed: int  = Form(...),
    back_date: str  = Form(...),
):
    models.set_confirmed(rid, confirmed)
    return RedirectResponse(f"/kmb/admin/day/{back_date}", status_code=303)


@router.post("/reservation/{rid}/cancel", response_class=HTMLResponse)
async def reservation_cancel(
    request: Request, rid: int,
    back_date: str = Form(...),
):
    models.cancel_reservation(rid)
    return RedirectResponse(f"/kmb/admin/day/{back_date}", status_code=303)


@router.post("/reservation/{rid}/move", response_class=HTMLResponse)
async def reservation_move(
    request: Request, rid: int,
    new_date: str      = Form(...),
    new_rotation: int  = Form(...),
    back_date: str     = Form(...),
):
    ok, err = models.move_reservation(rid, new_date, new_rotation)
    if not ok:
        return RedirectResponse(
            f"/kmb/admin/day/{back_date}?msg={err}", status_code=303
        )
    return RedirectResponse(f"/kmb/admin/day/{new_date}", status_code=303)


# ── Settings ──────────────────────────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request, msg: Optional[str] = None):
    defaults = models.get_defaults()
    today = date.today()
    weeks = _holiday_calendar(today.year, today.month)
    nav   = services.month_nav(today.year, today.month)
    return _tpl("admin/settings.html", request,
                defaults=defaults, slots=_SLOTS,
                weeks=weeks, nav=nav,
                year=today.year, month=today.month, msg=msg)


@router.get("/settings/calendar/{year}/{month}", response_class=HTMLResponse)
async def settings_calendar(request: Request, year: int, month: int,
                              msg: Optional[str] = None):
    defaults = models.get_defaults()
    weeks = _holiday_calendar(year, month)
    nav   = services.month_nav(year, month)
    return _tpl("admin/settings.html", request,
                defaults=defaults, slots=_SLOTS,
                weeks=weeks, nav=nav, year=year, month=month, msg=msg)


@router.post("/settings", response_class=HTMLResponse)
async def settings_post(
    request: Request,
    course_1: str     = Form(""),
    course_2: str     = Form(""),
    course_3: str     = Form(""),
    start_time_1: str  = Form(...),
    capacity_1: int    = Form(...),
):
    old_defaults = models.get_defaults()
    protected = services.apply_default_propagation(old_defaults)
    models.save_defaults(
        course_1.strip() or None,
        course_2.strip() or None,
        course_3.strip() or None,
        start_time_1,
        capacity_1,
    )
    today = date.today()
    if protected:
        msg = "保存しました。" + "、".join(protected) + " は予約があるため変更されませんでした。"
    else:
        msg = "保存しました。"
    return RedirectResponse(
        f"/kmb/admin/settings/calendar/{today.year}/{today.month}?msg={msg}",
        status_code=303,
    )


@router.post("/holiday", response_class=HTMLResponse)
async def holiday_toggle(
    request: Request,
    target_date: str = Form(...),
    is_closed: int    = Form(...),
    year: int         = Form(...),
    month: int        = Form(...),
):
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
        course_1=dc["course_1"] if dc else None,
        course_2=dc["course_2"] if dc else None,
        course_3=dc["course_3"] if dc else None,
        start_time_1=dc["start_time_1"] if dc else None,
        capacity_1=dc["capacity_1"] if dc else None,
        start_time_2=dc["start_time_2"] if dc else None,
        capacity_2=dc["capacity_2"] if dc else None,
    )
    return RedirectResponse(
        f"/kmb/admin/settings/calendar/{year}/{month}", status_code=303
    )


def _holiday_calendar(year: int, month: int) -> list:
    """Calendar grid for holiday settings — shows all dates with closed/reservation status."""
    import calendar as cal_mod
    defaults = models.get_defaults()
    day_cfgs = models.get_all_day_configs()
    weeks = []
    for week in cal_mod.monthcalendar(year, month):
        row = []
        for d_num in week:
            if d_num == 0:
                row.append(None)
                continue
            d_str = date(year, month, d_num).isoformat()
            cfg   = models.get_effective_config(d_str, defaults, day_cfgs)
            count = models.count_active_for_date(d_str)
            row.append({
                "date": d_str, "day": d_num,
                "is_closed": cfg["is_closed"],
                "has_reservations": count > 0,
            })
        weeks.append(row)
    return weeks
