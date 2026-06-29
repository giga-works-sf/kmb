"""Send confirmation email to customer. Falls back to outbox/ in dev mode."""
from __future__ import annotations
import logging
import smtplib
from datetime import date as _date_cls, datetime
from email.message import EmailMessage

from app.config import (MAIL_RELAY_HOST, MAIL_RELAY_PORT,
                         MAIL_FROM, SHOP_NAME, OUTBOX_DIR, ADMIN_EMAIL)
from app.sms import format_phone_display

_WEEKDAY_NAMES = ["月", "火", "水", "木", "金", "土", "日"]
logger = logging.getLogger(__name__)


def _course_line(reservation: dict) -> str:
    name = reservation.get("course_name")
    if not name:
        return ""
    price = reservation.get("course_price")
    text = f"\n【コース内容】\n{name}"
    if price:
        text += f"　{price}"
    return text + "\n"


def send_verification(reservation: dict, effective_cfg: dict, token: str) -> None:
    """Send email verification link. Errors are printed to stderr, never raised."""
    from app.config import APP_BASE_URL
    verify_url = f"{APP_BASE_URL}/kmb/verify/{token}"

    rotation = reservation["rotation"]
    start_time = (effective_cfg["start_time_1"] if rotation == 1
                  else effective_cfg["start_time_2"])

    weekday = _WEEKDAY_NAMES[_date_cls.fromisoformat(reservation["date"]).weekday()]
    date_str = f"{reservation['date']}（{weekday}）"

    body = (
        f"【予約リクエスト確認】{SHOP_NAME}\n\n"
        f"以下のURLをクリックすることで予約が確定いたします（有効期限10分）\n\n"
        f"{verify_url}\n\n"
        f"──── ご予約内容 ────\n"
        f"日付　　: {date_str}\n"
        f"開始時間: {start_time}\n"
        f"人数　　: {reservation['num_people']}名\n"
        f"代表者名: {reservation['name']} 様\n"
        f"電話番号: {format_phone_display(reservation['phone'])}\n"
    )
    if reservation.get("note"):
        body += f"備考　　: {reservation['note']}\n"
    body += _course_line(reservation)
    body += f"\n{SHOP_NAME}\n"

    msg = EmailMessage()
    msg["Subject"] = f"【メール確認】{date_str} {start_time}〜 {SHOP_NAME}"
    msg["From"]    = MAIL_FROM or "noreply@example.com"
    msg["To"]      = reservation["email"]
    msg.set_content(body)

    _dispatch(msg, reservation["id"])


def send_reservation_info(reservation: dict, effective_cfg: dict) -> None:
    """SMS認証完了後、予約情報をお知らせするメール（URL・トークンなし）。"""
    rotation = reservation["rotation"]
    start_time = (effective_cfg["start_time_1"] if rotation == 1
                  else effective_cfg["start_time_2"])

    weekday = _WEEKDAY_NAMES[_date_cls.fromisoformat(reservation["date"]).weekday()]
    date_str = f"{reservation['date']}（{weekday}）"

    body = (
        f"{reservation['name']} 様\n\n"
        f"SMS認証が完了し、ご予約が確定いたしました。\n\n"
        f"──── ご予約内容 ────\n"
        f"日付　　: {date_str}\n"
        f"開始時間: {start_time}\n"
        f"人数　　: {reservation['num_people']}名\n"
        f"代表者名: {reservation['name']} 様\n"
        f"電話番号: {format_phone_display(reservation['phone'])}\n"
    )
    if reservation.get("note"):
        body += f"備考　　: {reservation['note']}\n"
    body += _course_line(reservation)

    msg = EmailMessage()
    msg["Subject"] = f"【ご予約確定】{date_str} {start_time}〜 {SHOP_NAME}"
    msg["From"]    = MAIL_FROM or "noreply@example.com"
    msg["To"]      = reservation["email"]
    msg.set_content(body)

    _dispatch(msg, reservation["id"])


def send_booking_confirmed(reservation: dict, effective_cfg: dict, survey: dict | None) -> None:
    """Send booking confirmation email to customer after survey completion."""
    rotation = reservation["rotation"]
    start_time = (effective_cfg["start_time_1"] if rotation == 1
                  else effective_cfg["start_time_2"])

    weekday = _WEEKDAY_NAMES[_date_cls.fromisoformat(reservation["date"]).weekday()]
    date_str = f"{reservation['date']}（{weekday}）"

    # 支払い方法
    if survey:
        pm = survey.get("payment_method")
        if pm == "transfer":
            payment_line = f"お支払い方法: 事前振込割　8,800円\n振込人名義　: {survey.get('transfer_name') or ''}\n"
        elif pm == "in_store":
            payment_line = "お支払い方法: 店頭払い　9,000円（キャッシュレスのみ）\n"
        else:
            payment_line = ""
    else:
        payment_line = ""

    body = (
        f"【予約確定】{SHOP_NAME}\n\n"
        f"{reservation['name']} 様\n\n"
        f"ご予約が確定しました。当日のご来店をお待ちしております。\n\n"
        f"──── ご予約内容 ────\n"
        f"日付　　: {date_str}\n"
        f"開始時間: {start_time}\n"
        f"人数　　: {reservation['num_people']}名\n"
        f"代表者名: {reservation['name']} 様\n"
        f"電話番号: {format_phone_display(reservation['phone'])}\n"
    )
    if payment_line:
        body += payment_line
    body += _course_line(reservation)
    body += (
        f"\n──── キャンセルポリシー ────\n"
        f"キャンセル・人数変更は【7日前まで】にお電話にてご連絡ください。\n"
        f"6〜3日前: 50% ／ 2〜1日前: 80% ／ 当日: 100%\n\n"
        f"{SHOP_NAME}\n"
    )

    msg = EmailMessage()
    msg["Subject"] = f"【予約確定】{date_str} {start_time}〜 {SHOP_NAME}"
    msg["From"]    = MAIL_FROM or "noreply@example.com"
    msg["To"]      = reservation["email"]
    msg.set_content(body)

    _dispatch(msg, reservation["id"])


def send_admin_notification(reservation: dict, effective_cfg: dict) -> None:
    """Notify admin of a new booking request. No-op if ADMIN_EMAIL is unset."""
    if not ADMIN_EMAIL:
        logger.warning("ADMIN_EMAIL not set — skipping admin notification")
        return

    rotation = reservation["rotation"]
    start_time = (effective_cfg["start_time_1"] if rotation == 1
                  else effective_cfg["start_time_2"])
    weekday = _WEEKDAY_NAMES[_date_cls.fromisoformat(reservation["date"]).weekday()]
    date_str = f"{reservation['date']}（{weekday}）"

    body = (
        f"【新規予約リクエスト】{SHOP_NAME}\n\n"
        f"日付　　: {date_str}\n"
        f"開始時間: {start_time}\n"
        f"人数　　: {reservation['num_people']}名\n"
        f"代表者名: {reservation['name']} 様\n"
        f"電話番号: {format_phone_display(reservation['phone'])}\n"
        f"メール　: {reservation.get('email','')}\n"
    )
    if reservation.get("note"):
        body += f"備考　　: {reservation['note']}\n"
    body += _course_line(reservation)
    body += f"\n管理画面: {reservation['date']} の予約状況を確認してください。\n"

    msg = EmailMessage()
    msg["Subject"] = f"【新規予約リクエスト】{date_str} {start_time}〜 {SHOP_NAME}"
    msg["From"]    = MAIL_FROM or "noreply@example.com"
    msg["To"]      = ADMIN_EMAIL
    msg.set_content(body)
    _dispatch(msg, reservation["id"])


def _dispatch(msg: EmailMessage, rid: int) -> None:
    """Send or save to outbox."""
    if not MAIL_RELAY_HOST:
        _save_to_outbox(msg, rid)
        return
    try:
        with smtplib.SMTP(MAIL_RELAY_HOST, MAIL_RELAY_PORT) as s:
            s.starttls()
            s.send_message(msg)
        logger.info("Mail sent to %s (rid=%s)", msg["To"], rid)
    except Exception as exc:
        logger.error("SMTP error (rid=%s): %s", rid, exc)
        _save_to_outbox(msg, rid)


def _save_to_outbox(msg: EmailMessage, rid: int) -> None:
    OUTBOX_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTBOX_DIR / f"{ts}_{rid}.eml"
    path.write_bytes(bytes(msg))
    logger.info("Mail saved to outbox: %s", path)
