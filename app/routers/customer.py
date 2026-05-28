from __future__ import annotations
import re
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import models, services, mailer, sms
from app.config import SHOP_NAME, NOTE_MAX_LEN, CALENDAR_START, CALENDAR_END

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_COMMON = {"shop_name": SHOP_NAME}
_WEEKDAY_NAMES = ["月", "火", "水", "木", "金", "土", "日"]


def _tpl(name: str, request: Request, **ctx):
    return templates.TemplateResponse(request, name, {**_COMMON, **ctx})


@router.get("/", response_class=HTMLResponse)
async def customer_calendar(request: Request):
    today = date.today()
    return RedirectResponse(f"/kmb/calendar/{today.year}/{today.month}")


@router.get("/calendar/{year}/{month}", response_class=HTMLResponse)
async def customer_calendar_month(request: Request, year: int, month: int):
    today = date.today()
    weeks = services.build_customer_calendar(year, month, today)
    nav   = services.month_nav(year, month)
    cal_start = date.fromisoformat(CALENDAR_START)
    cal_end   = date.fromisoformat(CALENDAR_END)
    prev_d = date(nav["prev_year"], nav["prev_month"], 1)
    next_d = date(nav["next_year"], nav["next_month"], 1)
    return _tpl("customer/calendar.html", request,
                weeks=weeks, nav=nav,
                show_prev=prev_d >= date(cal_start.year, cal_start.month, 1),
                show_next=next_d <= date(cal_end.year, cal_end.month, 1),
                year=year, month=month)


@router.get("/book/{target_date}", response_class=HTMLResponse)
async def booking_form(request: Request, target_date: str,
                        rotation: Optional[int] = None):
    today = date.today()
    earliest, latest = services.get_booking_window(today)
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        return RedirectResponse("/kmb/")

    if d < earliest or d > latest:
        return RedirectResponse("/kmb/")

    all_defaults = models.get_all_defaults()
    cfg = models.get_effective_config(target_date, all_defaults)

    if cfg["is_closed"]:
        return RedirectResponse("/kmb/")

    rotations = _available_rotations(target_date, cfg)
    if not rotations:
        return RedirectResponse("/kmb/")

    if rotation is None or rotation not in [r["rotation"] for r in rotations]:
        rotation = rotations[0]["rotation"]

    sel = next(r for r in rotations if r["rotation"] == rotation)

    weekday_name = _WEEKDAY_NAMES[d.weekday()]
    return _tpl("customer/booking.html", request,
                target_date=target_date, weekday_name=weekday_name, cfg=cfg,
                rotations=rotations, selected_rotation=rotation,
                remaining=sel["remaining"],
                error=None)


@router.post("/book", response_class=HTMLResponse)
async def booking_submit(
    request: Request,
    target_date: str   = Form(...),
    rotation: int       = Form(...),
    num_people: int     = Form(...),
    name: str           = Form(...),
    phone_country: str  = Form("+81"),
    phone: str          = Form(...),
    email: str          = Form(...),
    note: str           = Form(""),
):
    today = date.today()
    earliest, latest = services.get_booking_window(today)

    errors = []
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        return RedirectResponse("/kmb/")

    if not (earliest <= d <= latest):
        errors.append("受付期間外の日付です")
    if not name.strip():
        errors.append("名前は必須です")
    if not phone.strip():
        errors.append("電話番号は必須です")
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        errors.append("メールアドレスの形式が正しくありません")
    if num_people < 1:
        errors.append("人数は1名以上で入力してください")
    if len(note) > NOTE_MAX_LEN:
        errors.append(f"備考は{NOTE_MAX_LEN}文字以内で入力してください")

    all_defaults = models.get_all_defaults()
    cfg = models.get_effective_config(target_date, all_defaults)

    if errors:
        rotations = _available_rotations(target_date, cfg)
        sel = next((r for r in rotations if r["rotation"] == rotation),
                   rotations[0] if rotations else None)
        weekday_name = _WEEKDAY_NAMES[d.weekday()]
        return _tpl("customer/booking.html", request,
                    target_date=target_date, weekday_name=weekday_name, cfg=cfg,
                    rotations=rotations, selected_rotation=rotation,
                    remaining=sel["remaining"] if sel else 0,
                    error=" / ".join(errors))

    phone_e164 = sms.to_e164(phone_country, phone)

    rid = models.create_reservation(
        target_date, rotation, name.strip(), num_people,
        phone_e164, email.strip(), note.strip() or None,
    )

    if rid is None:
        rotations = _available_rotations(target_date, cfg)
        sel = next((r for r in rotations if r["rotation"] == rotation),
                   rotations[0] if rotations else None)
        weekday_name = _WEEKDAY_NAMES[d.weekday()]
        return _tpl("customer/booking.html", request,
                    target_date=target_date, weekday_name=weekday_name, cfg=cfg,
                    rotations=rotations, selected_rotation=rotation,
                    remaining=sel["remaining"] if sel else 0,
                    error="申し訳ありません、ご希望の人数分の空席がなくなりました。")

    dev_code = sms.send_otp(phone_e164)
    models.store_sms_otp(rid, phone_e164, dev_code)
    return RedirectResponse(f"/kmb/verify-sms/{rid}", status_code=303)


@router.get("/verify-sms/{rid}", response_class=HTMLResponse)
async def verify_sms_form(request: Request, rid: int, error: Optional[str] = None,
                           resent: bool = False):
    res = models.get_reservation(rid)
    if not res or res["status"] != "pending_verify":
        return RedirectResponse("/kmb/")
    from datetime import datetime, timezone as _tz
    expired = False
    if res.get("token_expires_at"):
        expires = datetime.strptime(res["token_expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz.utc)
        expired = datetime.now(_tz.utc) > expires
    return _tpl("customer/verify_sms.html", request,
                rid=rid, masked_phone=sms.mask_phone(res["phone"]),
                error=error, expired=expired, resent=resent)


@router.post("/verify-sms/{rid}", response_class=HTMLResponse)
async def verify_sms_submit(request: Request, rid: int):
    form = await request.form()
    code = (form.get("code") or "").strip()
    res = models.get_reservation(rid)
    if not res or res["status"] != "pending_verify":
        return RedirectResponse("/kmb/")

    dev_code = res.get("email_token")  # None in production, plaintext in dev mode
    ok = sms.check_otp(res["phone"], code, dev_code)

    if not ok:
        return _tpl("customer/verify_sms.html", request,
                    rid=rid, masked_phone=sms.mask_phone(res["phone"]),
                    error="コードが正しくありません", expired=False, resent=False)

    activated = models.activate_reservation(rid)
    if activated is None:
        return _tpl("customer/verify_sms.html", request,
                    rid=rid, masked_phone=sms.mask_phone(res["phone"]),
                    error="有効期限が切れています。「コードを再送」してください。",
                    expired=True, resent=False)

    all_defaults = models.get_all_defaults()
    cfg = models.get_effective_config(activated["date"], all_defaults)
    start_time = cfg["start_time_1"] if activated["rotation"] == 1 else cfg["start_time_2"]
    weekday_name = _WEEKDAY_NAMES[date.fromisoformat(activated["date"]).weekday()]
    return _tpl("customer/verified.html", request,
                res=activated, cfg=cfg, start_time=start_time, weekday_name=weekday_name)


@router.post("/verify-sms/{rid}/resend", response_class=HTMLResponse)
async def verify_sms_resend(request: Request, rid: int):
    res = models.get_reservation(rid)
    if not res or res["status"] != "pending_verify":
        return RedirectResponse("/kmb/")
    dev_code = sms.send_otp(res["phone"])
    models.store_sms_otp(rid, res["phone"], dev_code)
    return RedirectResponse(f"/kmb/verify-sms/{rid}?resent=true", status_code=303)


@router.get("/complete/{rid}", response_class=HTMLResponse)
async def booking_complete(request: Request, rid: int):
    res = models.get_reservation(rid)
    if not res or res["status"] not in ("active", "pending_verify"):
        return RedirectResponse("/kmb/")
    all_defaults = models.get_all_defaults()
    cfg = models.get_effective_config(res["date"], all_defaults)
    start_time = cfg["start_time_1"] if res["rotation"] == 1 else cfg["start_time_2"]
    weekday_name = _WEEKDAY_NAMES[date.fromisoformat(res["date"]).weekday()]
    return _tpl("customer/complete.html", request,
                res=res, cfg=cfg, start_time=start_time, weekday_name=weekday_name)


@router.get("/verify/{token}", response_class=HTMLResponse)
async def verify_email(request: Request, token: str):
    res = models.activate_by_token(token)
    if res is None:
        return _tpl("customer/verify_error.html", request)
    all_defaults = models.get_all_defaults()
    cfg = models.get_effective_config(res["date"], all_defaults)
    start_time = cfg["start_time_1"] if res["rotation"] == 1 else cfg["start_time_2"]
    weekday_name = _WEEKDAY_NAMES[date.fromisoformat(res["date"]).weekday()]
    return _tpl("customer/verified.html", request,
                res=res, cfg=cfg, start_time=start_time, weekday_name=weekday_name)


def _available_rotations(target_date: str, cfg: dict) -> list[dict]:
    inventory = models.get_inventory_bulk([target_date])
    rotations = []
    for rot in (1, 2):
        t = cfg["start_time_1"] if rot == 1 else cfg["start_time_2"]
        c = cfg["capacity_1"]   if rot == 1 else cfg["capacity_2"]
        if t is None or not c:
            continue
        inv = inventory.get((target_date, rot), {"booked": 0})
        remaining = c - inv["booked"]
        if remaining > 0:
            rotations.append({"rotation": rot, "time": t, "remaining": remaining})
    return rotations
