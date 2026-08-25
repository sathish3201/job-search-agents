"""Password hashing, JWT issuance/verification, and the FastAPI dependency
that resolves the current authenticated user from a request. Single
responsibility: identity, not accounts (db.py owns the user table) and not
HTTP routing (api/routers/auth.py owns the endpoints)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from jose import JWTError, jwt
from passlib.context import CryptContext

from db import UserRow, UserStore

# Fail fast rather than silently signing tokens with a guessable default
# in production — but allow local dev to work without setting anything.
# Render (production) MUST have JWT_SECRET set as a real env var; if it
# isn't, this raises at import time instead of quietly issuing forgeable
# tokens.
_DEV_DEFAULT_SECRET = "dev-only-insecure-secret-do-not-use-in-production"
JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET:
    if os.getenv("RENDER"):  # Render sets this env var on every deploy
        raise RuntimeError(
            "JWT_SECRET is not set. Set a real random secret in the Render dashboard "
            "before deploying — without it, auth tokens would be forgeable."
        )
    print(
        "[auth] WARNING: JWT_SECRET not set, using an insecure dev-only default. "
        "This is fine for local development, never for a real deployment.",
        flush=True,
    )
    JWT_SECRET = _DEV_DEFAULT_SECRET

JWT_ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 7  # no refresh-token flow for phase 1 — re-login weekly

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
_user_store = UserStore()


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _pwd_context.verify(password, hashed)


def create_access_token(user_id: int, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "email": email, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Raises jose.JWTError on any invalid/expired/malformed token —
    callers (get_current_user below) catch this and turn it into a 401."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def get_current_user(request: Request) -> UserRow:
    """FastAPI dependency: every protected route adds
    Depends(get_current_user) and receives a UserRow with .id/.email.
    Reads Authorization: Bearer <token> — never a cookie (see
    web/src/api/client.js's comment on why localStorage+header was chosen
    over cross-origin cookies for this deployment topology)."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = auth_header.removeprefix("Bearer ").strip()
    try:
        payload = decode_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = _user_store.get_by_id(int(user_id))
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")

    return user
