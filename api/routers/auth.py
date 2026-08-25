"""Registration, login, and session validation. This is the only router
that doesn't require Depends(get_current_user) on every route — /me does,
since it's the frontend's "is my stored token still valid" check."""
from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from api.schemas import LoginRequest, RegisterRequest, TokenResponse, UserPublic
from auth import create_access_token, get_current_user, hash_password, verify_password
from db import UserRow, UserStore

router = APIRouter(prefix="/api/auth", tags=["auth"])

_user_store = UserStore()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD_LENGTH = 8


def _token_response(user_id: int, email: str) -> TokenResponse:
    token = create_access_token(user_id, email)
    return TokenResponse(access_token=token, user=UserPublic(id=user_id, email=email))


@router.post("/register", response_model=TokenResponse)
def register(request: RegisterRequest):
    email = request.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    if len(request.password) < _MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"Password must be at least {_MIN_PASSWORD_LENGTH} characters"
        )

    hashed = hash_password(request.password)
    try:
        user_id = _user_store.create_user(email, hashed)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    # Auto-login on register — standard UX, avoids a redundant login step
    # right after signing up.
    return _token_response(user_id, email)


@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest):
    email = request.email.strip().lower()
    user = _user_store.get_by_email(email)
    # Generic message either way — don't leak whether the email exists,
    # which is itself information an attacker could use to enumerate
    # registered accounts.
    if user is None or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    return _token_response(user.id, user.email)


@router.get("/me", response_model=UserPublic)
def me(current_user: UserRow = Depends(get_current_user)):
    return UserPublic(id=current_user.id, email=current_user.email)
