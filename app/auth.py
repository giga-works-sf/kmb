"""Authentication stub. Insert real logic here when auth is needed."""
from fastapi import Request


async def require_admin(request: Request) -> None:
    """Future auth middleware. Currently a no-op pass-through."""
    pass
