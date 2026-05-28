"""Send confirmation email to customer. Falls back to outbox/ in dev mode."""
from __future__ import annotations
import smtplib
from datetime import date as _date_cls, datetime
from email.message import EmailMessage

from app.config import (MAIL_RELAY_HOST, MAIL_RELAY_PORT,
                         MAIL_FROM, SHOP_NAME, OUTBOX_DIR)

_WEEKDAY_NAMES = ["月", "火", "水", "木", "金", "土", "日"]


def send_verification(reservation: dict, effective_cfg: dict, token: str) -> None:
    """Send email verification link. Errors are printed to stderr, never raised."""
    from app.config import APP_BASE_URL
    verify_url = f"{APP_BASE_URL}/kmb/verify/{token}"

    course = effective_cfg.get("course")
    rotation = reservation["rotation"]
    start_time = (effective_cfg["start_time_1"] if rotation == 1
                  else effective_cfg["start_time_2"])

    weekday = _WEEKDAY_NAMES[_date_cls.fromisoformat(reservation["date"]).weekday()]
    date_str = f"{reservation['date']}（{weekday}）"

    body = (
        f"【予約リクエスト確認】{SHOP_NAME}\n\n"
        f"以下のURLをクリックすることで予約が確定いたします（有効期限2時間）\n\n"
        f"{verify_url}\n\n"
        f"──── ご予約内容 ────\n"
        f"日付　　: {date_str}\n"
        f"開始時間: {start_time}\n"
        f"人数　　: {reservation['num_people']}名\n"
        f"代表者名: {reservation['name']} 様\n"
        f"電話番号: {reservation['phone']}\n"
    )
    if reservation.get("note"):
        body += f"備考　　: {reservation['note']}\n"
    if course:
        body += f"\n【コース内容】\n{course}\n"
    body += f"\n{SHOP_NAME}\n"

    msg = EmailMessage()
    msg["Subject"] = f"【メール確認】{date_str} {start_time}〜 {SHOP_NAME}"
    msg["From"]    = MAIL_FROM or "noreply@example.com"
    msg["To"]      = reservation["email"]
    msg.set_content(body)

    if not MAIL_RELAY_HOST:
        _save_to_outbox(msg, reservation["id"])
        return

    try:
        # GWS SMTP relay — IP アドレスで認証済みのため login() 不要
        with smtplib.SMTP(MAIL_RELAY_HOST, MAIL_RELAY_PORT) as s:
            s.starttls()
            s.send_message(msg)
    except Exception as exc:
        import sys
        print(f"[mailer] SMTP error: {exc}", file=sys.stderr)
        _save_to_outbox(msg, reservation["id"])


def _save_to_outbox(msg: EmailMessage, rid: int) -> None:
    OUTBOX_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTBOX_DIR / f"{ts}_{rid}.eml"
    path.write_bytes(bytes(msg))
    print(f"[mailer] saved to {path}")
