# auth.py
# JWT validation, session cookie management, FastAPI dependency.

import os
import logging
from typing import Optional
from fastapi import Request, HTTPException, Cookie
import jwt as pyjwt

import user_store

logger = logging.getLogger(__name__)

JWT_SECRET   = os.getenv("JWT_SHARED_SECRET", "change-me-in-production")
COOKIE_NAME  = "cai_session"
COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN", None)   # set to .circulants.ai in prod


# ── JWT (short-lived token from a2) ───────────────────────────────────────────

def validate_sso_token(token: str) -> Optional[dict]:
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload
    except pyjwt.ExpiredSignatureError:
        logger.warning("SSO token expired")
        return None
    except pyjwt.InvalidTokenError as e:
        logger.warning("Invalid SSO token: %s", e)
        return None


# ── Session cookie helpers ─────────────────────────────────────────────────────

def set_session_cookie(response, token: str):
    kwargs = dict(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=os.getenv("NODE_ENV", "development") == "production",
        samesite="lax",
        max_age=8 * 3600,
    )
    if COOKIE_DOMAIN:
        kwargs["domain"] = COOKIE_DOMAIN
    response.set_cookie(**kwargs)


def clear_session_cookie(response):
    kwargs = dict(key=COOKIE_NAME, value="", httponly=True, max_age=0)
    if COOKIE_DOMAIN:
        kwargs["domain"] = COOKIE_DOMAIN
    response.set_cookie(**kwargs)


# ── FastAPI dependency ─────────────────────────────────────────────────────────

def get_current_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session = user_store.get_session(token)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired — please log in again")
    return {
        "user_id":    session["user_id"],
        "email":      session["email"],
        "first_name": session["first_name"],
        "last_name":  session["last_name"],
        "persona":    session.get("persona"),
        "token":      token,
    }


def get_current_user_id(request: Request) -> str:
    return get_current_user(request)["user_id"]


def get_optional_user(request: Request) -> Optional[dict]:
    try:
        return get_current_user(request)
    except HTTPException:
        return None
