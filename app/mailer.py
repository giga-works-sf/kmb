"""Send confirmation email to customer. Falls back to outbox/ in dev mode."""
from __future__ import annotations
import smtplib
from datetime import datetime
from email.message import EmailMessage

from app.config import (MAIL_RELAY_HOST, MAIL_RELAY_PORT,
                         MAIL_FROM, SHOP_NAME, OUTBOX_DIR)


def send_confirmation(reservation: dict, effective_cfg: dict) -> None:
    """Send booking confirmation. Errors are printed to stderr, never raised."""
    courses = [
        c for c in [
            effective_cfg.get("course_1"),
            effective_cfg.get("course_2"),
            effective_cfg.get("course_3"),
        ] if c
    ]
    rotation = reservation["rotation"]
    start_time = (effective_cfg["start_time_1"] if rotation == 1
                  else effective_cfg["start_time_2"])

    body = (
        f"【予約確認】{SHOP_NAME}\n\n"
        f"以下の内容でご予約を承りました。\n\n"
        f"日付　　: {reservation['date']}\n"
        f"開始時間: {start_time}\n"
        f"人数　　: {reservation['num_people']}名\n"
        f"代表者名: {reservation['name']} 様\n"
        f"電話番号: {reservation['phone']}\n"
    )
    if reservation.get("note"):
        body += f"備考　　: {reservation['note']}\n"
    if courses:
        body += "\n【コース内容】\n" + "\n".join(f"・{c}" for c in courses) + "\n"
    body += f"\n変更・キャンセルのご連絡はお電話にてお願いいたします。\n{SHOP_NAME}\n"

    msg = EmailMessage()
    msg["Subject"] = f"【予約確認】{reservation['date']} {start_time}〜 {SHOP_NAME}"
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
