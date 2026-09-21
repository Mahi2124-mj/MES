"""
auth.py — User authentication + JWT token management + route protection decorator.
"""
import functools
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import jwt
from flask import g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

USERS_FILE = "users.json"
ROLES = ["admin", "supervisor", "operator"]

_JWT_ALGORITHM = "HS256"
_JWT_EXPIRY_HOURS = 8  # token lasts one shift
_SECRET_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".jwt_secret")


def _load_jwt_secret() -> str:
    """
    Load JWT secret from env var (production) or a local key file (dev).
    Generates and saves a new random key on first run.
    Never uses a hardcoded fallback.
    """
    env_secret = os.environ.get("TB_JWT_SECRET")
    if env_secret:
        return env_secret
    if os.path.exists(_SECRET_KEY_FILE):
        with open(_SECRET_KEY_FILE, "r") as f:
            return f.read().strip()
    import secrets as _secrets
    key = _secrets.token_hex(32)
    with open(_SECRET_KEY_FILE, "w") as f:
        f.write(key)
    return key


_JWT_SECRET = _load_jwt_secret()


# ─── password helpers ─────────────────────────────────────────────────────────

def _hash(pw: str) -> str:
    """Secure hash using werkzeug (scrypt). Use this for all new passwords."""
    return generate_password_hash(pw)


def _legacy_sha256(pw: str) -> str:
    """SHA-256 hash used by old records — only for migration detection."""
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


def _check_password(stored_hash: str, password: str) -> bool:
    """
    Check password against stored hash. Handles both legacy SHA-256 hashes
    (64-char hex) and modern werkzeug hashes. Auto-upgrades legacy hashes
    by returning a new hash when the old one matches.
    """
    if re.fullmatch(r"[0-9a-f]{64}", stored_hash):
        return stored_hash == _legacy_sha256(password)
    return check_password_hash(stored_hash, password)


# ─── token helpers ────────────────────────────────────────────────────────────

def create_token(user: Dict) -> str:
    """Create a signed JWT for the given user dict (no password_hash)."""
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user["id"],
        "username": user["username"],
        "role": user["role"],
        "display_name": user.get("display_name", user["username"]),
        "iat": now,
        "exp": now + timedelta(hours=_JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def decode_token(token: str) -> Optional[Dict]:
    """Decode and verify a JWT. Returns payload dict or None on failure."""
    try:
        return jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def require_auth(fn):
    """
    Route decorator — validates Authorization: Bearer <token> header.
    Sets g.current_user to the decoded token payload on success.
    Returns 401 JSON error on missing / invalid / expired tokens.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"ok": False, "message": "Authentication required"}), 401
        token = auth_header[7:]
        payload = decode_token(token)
        if not payload:
            return jsonify({"ok": False, "message": "Token expired or invalid — please log in again"}), 401
        g.current_user = payload
        return fn(*args, **kwargs)
    return wrapper


def require_role(*roles):
    """
    Route decorator — limits endpoint to specific roles (after require_auth).
    Usage: @require_role('admin', 'supervisor')
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            user = getattr(g, "current_user", {})
            if user.get("role") not in roles:
                return jsonify({"ok": False, "message": f"Role '{user.get('role')}' is not allowed here"}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ─── user file helpers ────────────────────────────────────────────────────────

def _users_path(base_dir: Optional[str] = None) -> str:
    root = base_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, USERS_FILE)


def _default_users() -> List[Dict]:
    # Default passwords are intentionally weak for first-run convenience.
    # Change them immediately after first login via the Admin panel.
    return [
        {"id": "user_admin", "username": "admin", "password_hash": _hash("TbAdmin@2024!"),
         "role": "admin", "display_name": "Administrator"},
        {"id": "user_supervisor", "username": "supervisor", "password_hash": _hash("TbSuper@2024!"),
         "role": "supervisor", "display_name": "Supervisor"},
        {"id": "user_operator", "username": "operator", "password_hash": _hash("TbOper@2024!"),
         "role": "operator", "display_name": "Operator"},
    ]


def load_users(base_dir: Optional[str] = None) -> List[Dict]:
    path = _users_path(base_dir)
    if not os.path.exists(path):
        users = _default_users()
        _save_users(users, base_dir)
        return users
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_users(users: List[Dict], base_dir: Optional[str] = None) -> None:
    with open(_users_path(base_dir), "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)


# ─── public API ───────────────────────────────────────────────────────────────

def authenticate(username: str, password: str, base_dir: Optional[str] = None) -> Tuple[bool, Optional[Dict]]:
    users = load_users(base_dir)
    for u in users:
        if u.get("username") != username:
            continue
        stored = u.get("password_hash", "")
        if not _check_password(stored, password):
            return False, None
        # Auto-upgrade legacy SHA-256 hash to werkzeug on successful login
        if re.fullmatch(r"[0-9a-f]{64}", stored):
            u["password_hash"] = _hash(password)
            _save_users(users, base_dir)
        return True, u
    return False, None


def get_user_by_id(user_id: str, base_dir: Optional[str] = None) -> Optional[Dict]:
    for u in load_users(base_dir):
        if u.get("id") == user_id:
            return u
    return None


def list_users(base_dir: Optional[str] = None) -> List[Dict]:
    return [
        {"id": u["id"], "username": u["username"], "role": u["role"],
         "display_name": u.get("display_name", u["username"])}
        for u in load_users(base_dir)
    ]


def add_user(
    username: str,
    password: str,
    role: str,
    display_name: str = "",
    base_dir: Optional[str] = None,
) -> Tuple[bool, str]:
    username = username.strip()[:64]
    if not username:
        return False, "Username required"
    if role not in ROLES:
        return False, f"Role must be one of: {', '.join(ROLES)}"
    users = load_users(base_dir)
    if any(u.get("username") == username for u in users):
        return False, "Username already exists"
    uid = f"user_{re.sub(r'[^a-z0-9]', '_', username.lower())}_{int(time.time())}"
    users.append({
        "id": uid,
        "username": username,
        "password_hash": _hash(password),
        "role": role,
        "display_name": (display_name or username)[:64],
    })
    _save_users(users, base_dir)
    return True, f"User '{username}' added"


def delete_user(user_id: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    users = load_users(base_dir)
    if len(users) <= 1:
        return False, "Cannot delete the last user"
    updated = [u for u in users if u.get("id") != user_id]
    if len(updated) == len(users):
        return False, "User not found"
    _save_users(updated, base_dir)
    return True, "User deleted"


def change_password(user_id: str, new_password: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
    users = load_users(base_dir)
    for u in users:
        if u.get("id") == user_id:
            u["password_hash"] = _hash(new_password)
            _save_users(users, base_dir)
            return True, "Password updated"
    return False, "User not found"
