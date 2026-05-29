from __future__ import annotations
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import models, services, mailer, sms
from app.config import SHOP_NAME, NOTE_MAX_LEN, CALENDAR_START, CALENDAR_END, USE_SMS_VERIFICATION

logger = logging.getLogger(__name__)


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host or "unknown"

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
from app.sms import format_phone_display
templates.env.filters["format_phone"] = format_phone_display
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
    background_tasks: BackgroundTasks,
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

    # IP ベースのレート制限
    client_ip = _get_client_ip(request)
    if not models.check_rate_limit(client_ip):
        logger.warning("Rate limit exceeded: ip=%s", client_ip)
        weekday_name = _WEEKDAY_NAMES[d.weekday()]
        rotations = _available_rotations(target_date, all_defaults)
        sel = next((r for r in rotations if r["rotation"] == rotation),
                   rotations[0] if rotations else None)
        return _tpl("customer/booking.html", request,
                    target_date=target_date, weekday_name=weekday_name, cfg=cfg,
                    rotations=rotations, selected_rotation=rotation,
                    remaining=sel["remaining"] if sel else 0,
                    error="1日の予約リクエスト上限（5回）に達しました。お電話にてお問い合わせください。")

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

    res = models.get_reservation(rid)
    # 管理者通知メールはバックグラウンドで送信（レスポンスをブロックしない）
    background_tasks.add_task(mailer.send_admin_notification, res, cfg)

    if USE_SMS_VERIFICATION:
        # SMS 認証フロー
        dev_code = sms.send_otp(phone_e164)
        models.store_sms_otp(rid, phone_e164, dev_code)
        return RedirectResponse(f"/kmb/verify-sms/{rid}", status_code=303)
    else:
        # メール認証フロー（デフォルト）
        token = models.store_verification_token(rid)
        res = models.get_reservation(rid)
        # 確認メールもバックグラウンドで送信（レスポンスをブロックしない）
        background_tasks.add_task(mailer.send_verification, res, cfg, token)
        return RedirectResponse(f"/kmb/complete/{rid}", status_code=303)


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

    dev_code = res.get("verification_token")  # None in production, plaintext in dev mode
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

    return RedirectResponse(f"/kmb/survey/{activated['id']}", status_code=303)


@router.get("/survey/{rid}", response_class=HTMLResponse)
async def survey_form(request: Request, rid: int):
    res = models.get_reservation(rid)
    if not res or res["status"] != "active":
        return RedirectResponse("/kmb/")
    today = date.today()
    res_date = date.fromisoformat(res["date"])
    can_edit = res_date >= today
    survey = models.get_survey(rid)
    all_defaults = models.get_all_defaults()
    cfg = models.get_effective_config(res["date"], all_defaults)
    start_time = cfg["start_time_1"] if res["rotation"] == 1 else cfg["start_time_2"]
    weekday_name = _WEEKDAY_NAMES[res_date.weekday()]
    return _tpl("customer/survey.html", request,
                res=res, survey=survey, can_edit=can_edit,
                start_time=start_time, weekday_name=weekday_name, error=None)


@router.post("/survey/{rid}", response_class=HTMLResponse)
async def survey_submit(request: Request, rid: int):
    res = models.get_reservation(rid)
    if not res or res["status"] != "active":
        return RedirectResponse("/kmb/")
    if date.fromisoformat(res["date"]) < date.today():
        return RedirectResponse(f"/kmb/verified/{rid}")
    form = await request.form()
    payment_method = form.get("payment_method")
    transfer_name  = (form.get("transfer_name") or "").strip() or None

    # 事前振込を選択した場合は振込人名義が必須
    if payment_method == "transfer" and not transfer_name:
        all_defaults = models.get_all_defaults()
        cfg = models.get_effective_config(res["date"], all_defaults)
        start_time = cfg["start_time_1"] if res["rotation"] == 1 else cfg["start_time_2"]
        res_date = date.fromisoformat(res["date"])
        weekday_name = _WEEKDAY_NAMES[res_date.weekday()]
        survey = models.get_survey(rid)
        return _tpl("customer/survey.html", request,
                    res=res, survey=survey, can_edit=True,
                    start_time=start_time, weekday_name=weekday_name,
                    error="事前振込を選択された場合は振込人名義（カタカナ）を入力してください。")

    models.save_survey(rid, {
        "source":             form.get("source"),
        "source_other":       (form.get("source_other") or "").strip() or None,
        "visit_count":        form.get("visit_count"),
        "is_member":          1 if form.get("is_member") == "1" else 0,
        "looking_forward":    (form.get("looking_forward") or "").strip() or None,
        "allergy":            (form.get("allergy") or "").strip() or None,
        "disliked_food":      (form.get("disliked_food") or "").strip() or None,
        "nonalcoholic_count": int(form.get("nonalcoholic_count") or 0),
        "info_preference":    form.get("info_preference"),
        "other_questions":    (form.get("other_questions") or "").strip() or None,
        "payment_method":     payment_method,
        "transfer_name":      transfer_name,
        "terms_agreed":       1,
    })
    return RedirectResponse(f"/kmb/verified/{rid}", status_code=303)


@router.get("/verified/{rid}", response_class=HTMLResponse)
async def verified_page(request: Request, rid: int):
    res = models.get_reservation(rid)
    if not res or res["status"] != "active":
        return RedirectResponse("/kmb/")
    today = date.today()
    res_date = date.fromisoformat(res["date"])
    survey = models.get_survey(rid)
    # アンケート未回答かつ未来日は必須
    if not survey and res_date >= today:
        return RedirectResponse(f"/kmb/survey/{rid}")
    all_defaults = models.get_all_defaults()
    cfg = models.get_effective_config(res["date"], all_defaults)
    start_time = cfg["start_time_1"] if res["rotation"] == 1 else cfg["start_time_2"]
    weekday_name = _WEEKDAY_NAMES[res_date.weekday()]
    can_edit_survey = res_date >= today
    return _tpl("customer/verified.html", request,
                res=res, cfg=cfg, start_time=start_time,
                weekday_name=weekday_name, rid=rid,
                can_edit_survey=can_edit_survey)


@router.post("/verify-sms/{rid}/resend", response_class=HTMLResponse)
async def verify_sms_resend(request: Request, rid: int):
    res = models.get_reservation(rid)
    if not res or res["status"] != "pending_verify":
        return RedirectResponse("/kmb/")
    dev_code = sms.send_otp(res["phone"])
    models.store_sms_otp(rid, res["phone"], dev_code)
    return RedirectResponse(f"/kmb/verify-sms/{rid}?resent=true", status_code=303)


@router.get("/complete/{rid}", response_class=HTMLResponse)
async def booking_complete(request: Request, rid: int, resent: bool = False):
    res = models.get_reservation(rid)
    if not res or res["status"] not in ("active", "pending_verify"):
        return RedirectResponse("/kmb/")
    all_defaults = models.get_all_defaults()
    cfg = models.get_effective_config(res["date"], all_defaults)
    start_time = cfg["start_time_1"] if res["rotation"] == 1 else cfg["start_time_2"]
    weekday_name = _WEEKDAY_NAMES[date.fromisoformat(res["date"]).weekday()]
    return _tpl("customer/complete.html", request,
                res=res, cfg=cfg, start_time=start_time,
                weekday_name=weekday_name, resent=resent)


@router.post("/resend-email/{rid}", response_class=HTMLResponse)
async def resend_email(request: Request, rid: int):
    res = models.get_reservation(rid)
    if not res or res["status"] != "pending_verify":
        return RedirectResponse("/kmb/")
    all_defaults = models.get_all_defaults()
    cfg = models.get_effective_config(res["date"], all_defaults)
    token = models.store_verification_token(rid)
    mailer.send_verification(res, cfg, token)
    logger.info("Email verification resent: rid=%s", rid)
    return RedirectResponse(f"/kmb/complete/{rid}?resent=true", status_code=303)


@router.get("/verify/{token}", response_class=HTMLResponse)
async def verify_email(request: Request, token: str):
    res = models.activate_by_token(token)
    if res is None:
        return _tpl("customer/verify_error.html", request)
    return RedirectResponse(f"/kmb/survey/{res['id']}", status_code=303)


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
