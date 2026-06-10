"""Twilio Verify integration for SMS OTP.

Dev mode (TWILIO_ACCOUNT_SID unset):
  - Generates a local 6-digit code
  - Logs it to stdout
  - Stores it in reservation.email_token for admin panel display
"""
from __future__ import annotations
import random
import re
import logging
from app.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_VERIFY_SERVICE_SID

logger = logging.getLogger(__name__)

# DEV_MODE: 認証情報が未設定の場合はローカルOTPを使用
DEV_MODE = not TWILIO_ACCOUNT_SID

if DEV_MODE:
    logger.warning("SMS DEV MODE: TWILIO_ACCOUNT_SID 未設定 → ローカルOTPを使用")
else:
    logger.info("Twilio有効: SID=%s... VERIFY_SID=%s...",
                TWILIO_ACCOUNT_SID[:8],
                TWILIO_VERIFY_SERVICE_SID[:8] if TWILIO_VERIFY_SERVICE_SID else "未設定")


# ── Phone normalization ────────────────────────────────────────────────────────

def to_e164(country_code: str, local_phone: str) -> str:
    """Combine country code and local number into E.164 format.

    Examples:
        to_e164("+81", "090-1234-5678")  -> "+819012345678"
        to_e164("+1",  "415-555-0100")   -> "+14155550100"
    """
    code = country_code.strip()
    if not code.startswith("+"):
        code = "+" + code
    digits = re.sub(r"[^0-9]", "", local_phone)
    # Strip leading 0 (Japanese mobile: 090 → 90 after +81)
    digits = digits.lstrip("0")
    return code + digits


def format_phone_display(phone: str) -> str:
    """Format stored phone for human-readable display.
    +819012345678 → 090-1234-5678  (Japanese +81)
    Other country codes are shown as-is."""
    if not phone:
        return phone
    if phone.startswith("+81"):
        local = "0" + phone[3:]
        digits = re.sub(r"[^0-9]", "", local)
        n = len(digits)
        if n == 11:  # 携帯 090/080/070
            return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
        if n == 10:
            if digits[:2] in ("03", "06"):  # 大都市固定
                return f"{digits[:2]}-{digits[2:6]}-{digits[6:]}"
            return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
        return local  # それ以外はそのまま
    return phone  # 海外番号はそのまま


def mask_phone(phone: str) -> str:
    """Return masked phone for display. +819012345678 → +81 ***-****-5678"""
    if phone.startswith("+"):
        cc_end = 3 if len(phone) > 4 and phone[3].isdigit() else 2
        cc = phone[:cc_end]
        local = phone[cc_end:]
        if len(local) >= 4:
            return cc + " " + "*" * (len(local) - 4) + local[-4:]
        return cc + " " + "*" * len(local)
    if len(phone) >= 4:
        return "*" * (len(phone) - 4) + phone[-4:]
    return phone


# ── OTP send / check ──────────────────────────────────────────────────────────

def send_otp(phone_e164: str) -> str | None:
    """Send OTP via Twilio Verify (or generate locally in dev mode).

    Returns:
        The OTP code string in dev mode (to be stored in DB for admin display).
        None in production mode (Twilio manages the code).
    """
    if DEV_MODE:
        code = f"{random.randint(0, 999999):06d}"
        logger.info("SMS DEV OTP for %s: %s", phone_e164, code)
        return code

    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        verification = client.verify.v2.services(TWILIO_VERIFY_SERVICE_SID) \
                             .verifications.create(to=phone_e164, channel="sms")
        logger.info("Twilio SMS sent: to=%s status=%s", phone_e164, verification.status)
    except Exception as exc:
        logger.error("Twilio send error: %s: %s", type(exc).__name__, exc)
        if hasattr(exc, "code"):
            logger.error("  Twilio error code: %s", exc.code)  # type: ignore
    return None


def check_otp(phone_e164: str, code: str, dev_code: str | None = None) -> bool:
    """Verify OTP code.

    In dev mode, compares against dev_code stored in DB.
    In production mode, verifies via Twilio Verify API.
    """
    code = code.strip()
    if DEV_MODE:
        return bool(dev_code and code == dev_code)

    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        result = client.verify.v2.services(TWILIO_VERIFY_SERVICE_SID) \
                       .verification_checks.create(to=phone_e164, code=code)
        logger.info("Twilio verify result: to=%s status=%s", phone_e164, result.status)
        return result.status == "approved"
    except Exception as exc:
        logger.error("Twilio verify error: %s: %s", type(exc).__name__, exc)
        return False
