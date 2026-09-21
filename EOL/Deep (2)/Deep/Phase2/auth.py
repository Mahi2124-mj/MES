"""
auth.py
=======
JWT-based authentication with role support (admin, production, operator,
department, plant_head).  `plant_head` has the same permissions as
admin.  `department` is a generic "department user" — at click-time the
frontend asks which department (Maintenance / Quality / Production)
they're acting as.

To change JWT secret → edit SECRET_KEY
To change token expiry → edit TOKEN_EXPIRE_HOURS
"""

from datetime import datetime, timedelta
from typing import Optional

import os
import uuid
import time as _time
import threading as _threading
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel

from database import get_conn, dict_cursor
import psycopg2

# ── Config ─────────────────────────────────────────────────────
SECRET_KEY          = os.environ.get("MES_JWT_SECRET", "mes-tbdi-bawal-2024-secret-key-change-in-production")
ALGORITHM           = "HS256"
# 2026-07-03 — sessions no longer expire on a timer.  A token stays valid
# until the user DELIBERATELY logs out (POST /api/auth/logout) or the API
# server restarts/crashes (the in-memory _active_sessions registry below is
# empty on boot, so every previously-issued token is invalidated).  This exp
# is only a far-future safety cap so the JWT is never rejected purely on age.
TOKEN_EXPIRE_HOURS  = 24 * 3650      # ~10 years (effectively "never" — see registry)

# ── Login rate-limit / lockout (anti-brute-force, 2026-06-18) ──────────
# Keyed per-USERNAME — safe behind the cloudflared/Caddy proxy where the
# client IP is the proxy (per-IP would lock out everyone).  In-memory;
# resets on API restart.  8 fails / 5 min -> 15 min lockout.
_login_fails = {}          # username_lc -> [count, window_start, locked_until]
_login_lock  = _threading.Lock()
_LOGIN_MAX_FAILS = 8
_LOGIN_WINDOW    = 300
_LOGIN_LOCKOUT   = 900


def _login_locked_for(username: str) -> int:
    with _login_lock:
        rec = _login_fails.get((username or "").lower())
        if rec and rec[2] and _time.time() < rec[2]:
            return int(rec[2] - _time.time())
    return 0


def _login_note_fail(username: str) -> None:
    u = (username or "").lower()
    with _login_lock:
        now = _time.time()
        rec = _login_fails.get(u)
        if not rec or (now - rec[1]) > _LOGIN_WINDOW:
            rec = [0, now, 0]
        rec[0] += 1
        if rec[0] >= _LOGIN_MAX_FAILS:
            rec[2] = now + _LOGIN_LOCKOUT
        _login_fails[u] = rec


def _login_note_ok(username: str) -> None:
    with _login_lock:
        _login_fails.pop((username or "").lower(), None)


# ── Active-session registry (2026-07-03, made durable 2026-08-14) ─────
# A token is valid ONLY while its jti (unique per login) is registered.  There
# is no short time-based expiry; a session ends when the user logs out
# (POST /api/auth/logout).
#
# 2026-08-14 — THE REGISTRY IS NOW IN POSTGRES, not just this process.
# It used to be an in-memory set only, with the documented consequence that
# "the API restarts -> this set is empty on boot, so EVERY token issued before
# the restart is rejected".  In practice that meant every operator on the floor
# was thrown back to the login screen on every deploy, every crash and every
# config change that needed a bounce — with nothing on screen to explain why,
# because the token itself was still valid for ~10 years and the frontend only
# saw a bare 401.  Six restarts in the recent log = six mass logouts.
#
# The set stays as a read-through CACHE (hot path is every single request, and
# uvicorn runs one worker), but the truth now survives a restart.  Deliberate
# logout still revokes immediately, so the security model is unchanged: this
# only stops the process lifetime from silently ending everyone's session.
_active_sessions = set()            # set[str] — cache of known-good jti values
_sessions_lock   = _threading.Lock()
_SESSION_TABLE_READY = False


def _ensure_session_table() -> None:
    global _SESSION_TABLE_READY
    if _SESSION_TABLE_READY:
        return
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mes_active_sessions (
                    jti        TEXT PRIMARY KEY,
                    user_id    INTEGER,
                    username   TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            conn.commit()
        _SESSION_TABLE_READY = True
    except Exception as exc:
        # Never block login on this.  If the table cannot be created we fall
        # back to the old in-memory-only behaviour rather than locking the
        # plant out of its dashboards.
        print(f"[AUTH] session table unavailable, sessions are process-local: {exc}")


def _session_add(jti: str, user_id: Optional[int] = None,
                 username: Optional[str] = None) -> None:
    with _sessions_lock:
        _active_sessions.add(jti)
    _ensure_session_table()
    try:
        with get_conn() as conn:
            conn.cursor().execute("""
                INSERT INTO mes_active_sessions (jti, user_id, username)
                VALUES (%s, %s, %s) ON CONFLICT (jti) DO NOTHING
            """, (jti, user_id, username))
            conn.commit()
    except Exception:
        pass                        # cached in memory; degrades to old behaviour


def _session_active(jti: Optional[str]) -> bool:
    if not jti:
        return False
    with _sessions_lock:
        if jti in _active_sessions:
            return True             # hot path — no DB hit for a warm session
    # Cache miss.  Either this process just started (the common case after a
    # deploy) or the jti is bogus.  Ask the durable registry once, then cache.
    _ensure_session_table()
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM mes_active_sessions WHERE jti = %s", (jti,))
            found = cur.fetchone() is not None
    except Exception:
        # 2026-08-17 — FAIL OPEN, not closed.  A transient DB error here (pool
        # contention, a connection left in an aborted transaction by an
        # unrelated failing query, the table mid-create) used to return False
        # → 401 → the frontend redirected to /login, i.e. an auto-logout.
        # After a restart the in-memory cache is empty, so EVERY active session
        # re-validates against the DB at once; a single hiccup in that burst
        # logged users out on every restart — exactly what the durable registry
        # was added to prevent.  The JWT signature is already verified by the
        # caller, so a validly-signed token is trusted while the registry is
        # momentarily unreadable.  Not cached, so a genuine logout is still
        # enforced the instant the DB is reachable again.
        return True
    if found:
        with _sessions_lock:
            _active_sessions.add(jti)
    return found


def _session_remove(jti: Optional[str]) -> None:
    if not jti:
        return
    with _sessions_lock:
        _active_sessions.discard(jti)
    _ensure_session_table()
    try:
        with get_conn() as conn:
            conn.cursor().execute(
                "DELETE FROM mes_active_sessions WHERE jti = %s", (jti,))
            conn.commit()
    except Exception:
        pass


# ── Crypto ─────────────────────────────────────────────────────
# Use bcrypt directly (passlib 1.7.4 is incompatible with bcrypt >= 4.1).
oauth2_scheme          = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
# Same as oauth2_scheme but tolerates missing/invalid tokens — returns None
# instead of raising 401.  Used by endpoints that are PUBLICLY readable
# (Fullscreen TV display) but ALSO benefit from a logged-in user context
# (e.g. operator-line restriction) when the caller is authenticated.
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login",
                                              auto_error=False)


# ── Schemas ────────────────────────────────────────────────────
class Token(BaseModel):
    access_token: str
    token_type:   str
    username:     str
    user_id:      int
    role:         str
    expires_in:   int   # seconds


class TokenData(BaseModel):
    username: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────
def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed:
        return False
    try:
        # bcrypt truncates at 72 bytes; encode both sides consistently.
        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def create_token(username: str, role: str, user_id: int) -> str:
    # Unique session id — registered as active NOW; stays valid until the user
    # logs out (see _active_sessions; the registry is durable across restarts
    # as of 2026-08-14).  The exp is only a far-future cap so jwt.decode never
    # rejects the token purely on age.
    jti = uuid.uuid4().hex
    _session_add(jti, user_id=user_id, username=username)
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": username, "exp": expire, "role": role, "id": user_id, "jti": jti},
        SECRET_KEY,
        algorithm=ALGORITHM
    )


def get_user_from_db(username: str) -> Optional[dict]:
    with get_conn() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT * FROM mes_admin WHERE username = %s",
            (username,)
        )
        return cur.fetchone()


# Roles that are inherently line-scoped: with NO mes_operator_lines assignment
# they see NOTHING (not everything).  admin / plant_head / department /
# quality_incharge stay GLOBAL when unassigned, so heads keep their cross-line
# view.  A user WITH assignments is always limited to those, regardless of role.
# (2026-08-30 operator decision — an unassigned 'operator' was seeing all lines.)
FLOOR_SCOPED_ROLES = {"operator", "leader", "shift_incharge", "production",
                      "section_incharge", "production_incharge"}


# ── Dependencies ───────────────────────────────────────────────
def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """
    FastAPI dependency that returns the current authenticated user.
    Contains keys: id, username, role, last_login, created_at.
    """
    creds_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise creds_exc
    except JWTError:
        raise creds_exc

    # Session must still be active, i.e. the user has not logged out.  Since
    # 2026-08-14 the registry is durable (mes_active_sessions), so a restart no
    # longer ends everyone's session — only a deliberate logout does.
    if not _session_active(payload.get("jti")):
        raise creds_exc

    user = get_user_from_db(username)
    if not user:
        raise creds_exc
    return dict(user)


def username_from_token(token: Optional[str]) -> Optional[str]:
    """Resolve a RAW token string to a username, or None.

    2026-08-14 — needed because navigator.sendBeacon cannot set an
    Authorization header, so the UI-timing beacons arrived anonymous and every
    one of the 2841 rows recorded had username NULL.  The beacon now carries the
    token in its JSON body and this resolves it.  Same validation as
    get_current_user (signature + live session); it just never raises, because
    telemetry must not fail a request.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if not _session_active(payload.get("jti")):
        return None
    return payload.get("sub") or None


def get_current_user_optional(token: Optional[str] = Depends(oauth2_scheme_optional)
                              ) -> Optional[dict]:
    """
    Optional variant of get_current_user.  Returns the authenticated user
    when a valid `Authorization: Bearer <token>` header is present, else
    returns None — never raises 401.

    Use on PUBLIC read-only endpoints that the Fullscreen TV display polls
    without ever logging in.  Endpoints can still branch on `if user:` to
    apply per-user filters when an authenticated request comes in.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            return None
    except JWTError:
        return None
    # A logged-out / pre-restart token grants no user context (returns None,
    # never 401, so public endpoints keep working).
    if not _session_active(payload.get("jti")):
        return None
    user = get_user_from_db(username)
    return dict(user) if user else None


def require_admin(user: dict = Depends(get_current_user)):
    """Dependency that raises 403 if user is not admin (or plant_head,
    which is admin-equivalent per spec)."""
    if user["role"] not in ("admin", "plant_head"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )
    return user


def require_read_only(user: dict = Depends(get_current_user)):
    """Any authenticated user can read."""
    return user


# Legacy: keep get_current_admin for backward compatibility (same as get_current_user)
get_current_admin = get_current_user


# ── Router ─────────────────────────────────────────────────────
from fastapi import APIRouter

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


@auth_router.post("/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends()):
    """
    Exchange username+password for a JWT token.
    Returns token with user id, role, and expiry.
    """
    # 2026-06-18 — brute-force lockout: too many recent failures for this
    # username -> 429 (generic message, independent of whether user exists).
    if _login_locked_for(form.username) > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Please try again later.",
        )
    # 2026-06-14 — DB unreachable ≠ bad credentials.  A connection failure
    # here must surface as a distinct 503 so the login UI shows "Server not
    # connected" instead of the misleading "Invalid credentials".
    try:
        user = get_user_from_db(form.username)
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as _exc:
        print(f"[LOGIN] DB unreachable: {_exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server not connected — database unreachable.",
        )
    if not user or not verify_password(form.password, user["password_hash"]):
        _login_note_fail(form.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    _login_note_ok(form.username)

    # Update last_login + write AUTH_LOGIN audit row in one round-trip
    # 2026-05-18 — Operator audit-log spec: every successful login lands
    # in mes_audit_log so the "every user · last login" top card on the
    # Audit page and the per-user activity trail both work.  user_id +
    # username columns were added in the same release.
    with get_conn() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE mes_admin SET last_login = NOW() WHERE username = %s",
            (form.username,)
        )
        try:
            c.execute(
                """INSERT INTO mes_audit_log
                       (action, entity_type, entity_id, details,
                        user_id, username)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                ("AUTH_LOGIN", "user", user["id"],
                 f"role={user['role']}",
                 user["id"], form.username)
            )
        except Exception as _exc:
            # Audit failure must never block login — log and continue
            print(f"[AUDIT] login write failed: {_exc}")

    token = create_token(form.username, user["role"], user["id"])
    return Token(
        access_token=token,
        token_type="bearer",
        username=form.username,
        user_id=user["id"],
        role=user["role"],
        expires_in=TOKEN_EXPIRE_HOURS * 3600,
    )


@auth_router.post("/logout")
def logout(token: str = Depends(oauth2_scheme)):
    """Deliberate logout — invalidate THIS token's session server-side so it
    can never be reused, even if the raw JWT was copied.  (The frontend should
    call this on logout in addition to clearing its local token.)  Idempotent:
    an unknown / already-removed jti is a harmless no-op."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM],
                             options={"verify_exp": False})
        _session_remove(payload.get("jti"))
    except JWTError:
        pass
    return {"ok": True, "message": "Logged out"}


@auth_router.post("/change-password")
def change_password(
    body: dict,
    user=Depends(get_current_user)
):
    """Change password for the authenticated user."""
    if not verify_password(body.get("current_password", ""), user["password_hash"]):
        raise HTTPException(400, "Current password is incorrect")

    new_hash = hash_password(body["new_password"])
    with get_conn() as conn:
        conn.cursor().execute(
            "UPDATE mes_admin SET password_hash = %s WHERE username = %s",
            (new_hash, user["username"])
        )
        # Audit-trail
        try:
            conn.cursor().execute(
                """INSERT INTO mes_audit_log
                       (action, entity_type, entity_id, details,
                        user_id, username)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                ("PASSWORD_CHANGED", "user", user["id"],
                 "self-service password change",
                 user["id"], user["username"])
            )
        except Exception as _exc:
            print(f"[AUDIT] password-change write failed: {_exc}")
    return {"ok": True, "message": "Password changed successfully"}


@auth_router.get("/me")
def me(user=Depends(get_current_user)):
    """Return current user info (no password hash).  Joins department row
    so the frontend can render '{DeptName} Panel' in the slide-nav for
    department users without a separate fetch.

    Also returns the explicit per-page permission map so AuthContext's
    canAccess() / canWrite() can honor admin-configured overrides
    without an extra round-trip on every page load."""
    dept_id   = user.get("department_id")
    dept_name = None
    dept_slug = None
    permissions = {}    # { page_key: perm_level }
    with get_conn() as conn:
        cur = dict_cursor(conn)
        if dept_id:
            cur.execute("SELECT name, slug FROM mes_departments WHERE id = %s",
                        (dept_id,))
            r = cur.fetchone()
            if r:
                dept_name = r["name"]
                dept_slug = r["slug"]
        # Permissions table may not exist yet — wrap in try/except so
        # /me never blows up on a fresh install.
        try:
            cur.execute("""
                SELECT page_key, perm_level
                  FROM mes_user_page_permissions
                 WHERE user_id = %s
            """, (user["id"],))
            for row in cur.fetchall():
                permissions[row["page_key"]] = row["perm_level"]
        except Exception:
            pass
        # 2026-07-31 — LINE SCOPING.  mes_operator_lines already mapped users to
        # lines but only an ADMIN could read it, so the frontend had no way to
        # know "this login belongs to YNC-SS".  Return it here (self-service) so
        # the dashboard can default to — and restrict itself to — that line: the
        # user stops pulling 17 lines' data they'll never look at.  An EMPTY list
        # means unrestricted (admins and anyone with no assignment), so nothing
        # changes for existing logins.
        try:
            cur.execute("""
                SELECT ol.line_id, l.line_code, l.line_name
                  FROM mes_operator_lines ol
                  JOIN mes_lines l ON l.id = ol.line_id
                 WHERE ol.admin_id = %s
                 ORDER BY l.line_code
            """, (user["id"],))
            assigned_lines = [
                {"id": r["line_id"], "lineCode": r["line_code"], "lineName": r["line_name"]}
                for r in cur.fetchall()
            ]
        except Exception:
            assigned_lines = []
        # 2026-08-14 — the level that goes WITH those lines, plus per-machine
        # scope.  assigned_lines above is membership only ("may see line 12");
        # this says whether that is read or full, and narrows it to individual
        # machines.  Same invariant: an EMPTY map means unrestricted, so a login
        # that has never been scoped behaves exactly as before.
        scope = {"lines": {}, "machines": {}}
        try:
            cur.execute("""
                SELECT scope_type, scope_id, perm_level
                  FROM mes_user_scope_permissions
                 WHERE user_id = %s
            """, (user["id"],))
            for row in cur.fetchall():
                bucket = "lines" if row["scope_type"] == "line" else "machines"
                scope[bucket][str(row["scope_id"])] = row["perm_level"]
        except Exception:
            pass
    return {
        "id":              user["id"],
        "username":        user["username"],
        "role":            user["role"],
        "department_id":   dept_id,
        "department_name": dept_name,
        "department_slug": dept_slug,
        "last_login":      user["last_login"],
        "created_at":      user["created_at"],
        "permissions":     permissions,
        "assigned_lines":  assigned_lines,
        "scope":           scope,
    }