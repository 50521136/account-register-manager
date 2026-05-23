from __future__ import annotations

from fastapi import Header, HTTPException

from account_register_manager.config import config


def require_admin(authorization: str | None = Header(default=None)) -> None:
    scheme, _, token = str(authorization or "").partition(" ")
    expected = config.auth_key
    if not expected:
        raise HTTPException(status_code=500, detail={"error": "auth_key is not configured"})
    if scheme.lower() != "bearer" or token.strip() != expected:
        raise HTTPException(status_code=401, detail={"error": "invalid auth token"})

