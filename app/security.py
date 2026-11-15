"""Passwords, sign-in sessions, roles and request-forgery protection."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time

from fastapi import Request

from . import db

ROLES = ["admin", "editor", "viewer"]
ROLE_LABELS = {"admin": "Administrator", "editor": "Editor", "viewer": "Viewer"}
ROLE_HELP = {
    "admin": "Everything, including users, thresholds and deleting data.",
    "editor": "Import files, generate reports and edit feedback.",
    "viewer": "View pages and download reports only.",
}
_RANK = {"viewer": 1, "editor": 2, "admin": 3}
MIN_PASSWORD_LENGTH = 8

# scrypt work factors (about 16 MB of memory and a few hundredths of a second per check).
_N, _R, _P = 2 ** 14, 8, 1


# ---- passwords -------------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def password_problem(password: str, confirm: str | None = None) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters for the password."
    if len(password) > 200:
        return "That password is too long."
    if confirm is not None and password != confirm:
        return "The two passwords do not match."
    return None


def _stamp(password_hash: str) -> str:
    """Short fingerprint of the password hash: changing a password signs every old session out."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


# ---- sessions --------------------------------------------------------------------------
class NeedLogin(Exception):
    pass


class Forbidden(Exception):
    def __init__(self, message: str = "Your account is not allowed to do that."):
        self.message = message


def has_users() -> bool:
    return bool(db.scalar("SELECT EXISTS (SELECT 1 FROM users)"))


def sign_in(request: Request, user: dict) -> None:
    csrf = request.session.get("csrf")
    request.session.clear()
    request.session.update({"uid": user["id"], "stamp": _stamp(user["password_hash"]),
                            "csrf": csrf or secrets.token_urlsafe(32)})
    db.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user["id"],))


def sign_out(request: Request) -> None:
    request.session.clear()


def refresh_session(request: Request, password_hash: str) -> None:
    """Keep the current session valid after its owner changed their own password."""
    request.session["stamp"] = _stamp(password_hash)


def current_user(request: Request) -> dict | None:
    if hasattr(request.state, "user"):
        return request.state.user
    user = None
    uid = request.session.get("uid")
    if uid:
        row = db.query_one("SELECT * FROM users WHERE id = %s AND is_active", (uid,))
        if row and hmac.compare_digest(_stamp(row["password_hash"]), str(request.session.get("stamp", ""))):
            user = row
        else:
            request.session.clear()
    request.state.user = user
    return user


def can(user: dict | None, role: str) -> bool:
    return bool(user) and _RANK[user["role"]] >= _RANK[role]


def require(role: str = "viewer"):
    """FastAPI dependency: a signed-in user holding at least this role."""
    def dependency(request: Request) -> dict:
        user = current_user(request)
        if not user:
            raise NeedLogin()
        if not can(user, role):
            raise Forbidden()
        return user
    return dependency


# ---- request-forgery protection --------------------------------------------------------
def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def csrf_protect(request: Request) -> None:
    """FastAPI dependency for every POST: the page's token must come back with the request."""
    sent = request.headers.get("x-csrf-token")
    if not sent:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            sent = form.get("csrf_token")
    expected = request.session.get("csrf")
    if not expected or not isinstance(sent, str) or not hmac.compare_digest(sent, expected):
        raise Forbidden("This page has expired. Reload it and try again.")


# ---- sign-in throttling ----------------------------------------------------------------
_MAX_FAILURES, _LOCK_SECONDS = 5, 300
_failures: dict[str, tuple[int, float]] = {}
_failures_lock = threading.Lock()


def locked_for(key: str) -> int:
    """Seconds this sign-in key is still locked, 0 when it may try."""
    with _failures_lock:
        count, until = _failures.get(key, (0, 0.0))
        remaining = int(until - time.time())
        if remaining <= 0 and until:
            _failures.pop(key, None)
        return max(remaining, 0)


def note_failure(key: str) -> None:
    with _failures_lock:
        count, _ = _failures.get(key, (0, 0.0))
        count += 1
        _failures[key] = (count, time.time() + _LOCK_SECONDS if count >= _MAX_FAILURES else 0.0)
        if len(_failures) > 5000:
            _failures.clear()


def note_success(key: str) -> None:
    with _failures_lock:
        _failures.pop(key, None)
