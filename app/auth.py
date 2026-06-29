"""Admin authentication: fixed-password HTTP Basic auth."""
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "kome2025"

_security = HTTPBasic()


async def require_admin(credentials: HTTPBasicCredentials = Depends(_security)) -> None:
    user_ok = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    pass_ok = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="認証情報が正しくありません",
            headers={"WWW-Authenticate": "Basic"},
        )
