from __future__ import annotations
import os
from pathlib import Path

CALENDAR_START = "2026-06-01"
CALENDAR_END   = "2039-12-31"
BOOKING_AHEAD_MONTHS = 2

TIME_SLOT_START    = "11:00"
TIME_SLOT_END      = "23:30"
TIME_SLOT_STEP_MIN = 30

NOTE_MAX_LEN = 500

MAIL_FROM        = os.getenv("MAIL_FROM", "")
MAIL_RELAY_HOST  = os.getenv("MAIL_RELAY_HOST", "")
MAIL_RELAY_PORT  = int(os.getenv("MAIL_RELAY_PORT", "587"))
ADMIN_EMAIL      = os.getenv("ADMIN_EMAIL", "")
SHOP_NAME        = os.getenv("SHOP_NAME", "KanpAI")
APP_BASE_URL     = os.getenv("APP_BASE_URL", "http://localhost:8000")

# Twilio SMS 認証 (.env に記載)
TWILIO_ACCOUNT_SID      = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN       = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_VERIFY_SERVICE_SID = os.getenv("TWILIO_VERIFY_SERVICE_SID", "")

ROOT_DIR   = Path(__file__).parent.parent
OUTBOX_DIR = ROOT_DIR / "outbox"
DB_PATH    = ROOT_DIR / "data.db"


def get_time_slots() -> list[str]:
    """Return list of HH:MM strings from TIME_SLOT_START to TIME_SLOT_END inclusive."""
    h, m = map(int, TIME_SLOT_START.split(":"))
    end_h, end_m = map(int, TIME_SLOT_END.split(":"))
    slots: list[str] = []
    while (h, m) <= (end_h, end_m):
        slots.append(f"{h:02d}:{m:02d}")
        m += TIME_SLOT_STEP_MIN
        if m >= 60:
            m -= 60
            h += 1
    return slots
