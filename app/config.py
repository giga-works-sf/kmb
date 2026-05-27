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

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
MAIL_FROM = os.getenv("MAIL_FROM", "")
SHOP_NAME = os.getenv("SHOP_NAME", "KanpAI")
BASE_URL  = os.getenv("BASE_URL", "http://localhost:8000")

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
