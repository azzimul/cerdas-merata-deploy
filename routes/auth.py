import secrets

from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash, check_password_hash

from db.connection import USE_SQLITE, get_conn
from helpers import _execute, _now_iso, _get_auth_user

bp = Blueprint("auth", __name__)


@bp.post("/api/auth/register")
def auth_register():
    data = request.get_json(silent=True) or {}
    username  = (data.get("username") or "").strip().lower()
    email     = (data.get("email") or "").strip().lower()
    full_name = (data.get("full_name") or "").strip()
    password  = data.get("password") or ""

    if not username or not email or not full_name or not password:
        return jsonify({"error": "All fields are required."}), 400
    if len(username) < 3:
        return jsonify({"error": "Username must be at least 3 characters."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400
    if "@" not in email:
        return jsonify({"error": "Invalid email address."}), 400

    from datetime import datetime, timezone, timedelta
    pw_hash = generate_password_hash(password)
    token   = secrets.token_hex(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

    try:
        conn = get_conn()
    except Exception as e:
        return jsonify({"error": f"Database connection failed: {e}"}), 503

    try:
        cur = conn.cursor()
        # Check uniqueness
        _execute(cur, "SELECT id FROM users WHERE username = %s OR email = %s", (username, email))
        if cur.fetchone():
            return jsonify({"error": "Username or email already registered."}), 409

        _execute(cur, """
            INSERT INTO users (username, email, full_name, password_hash)
            VALUES (%s, %s, %s, %s)
        """, (username, email, full_name, pw_hash))

        if USE_SQLITE:
            cur.execute("SELECT last_insert_rowid()")
        else:
            cur.execute("SELECT lastval()")
        user_id = cur.fetchone()[0]

        _execute(cur, """
            INSERT INTO user_sessions (token, user_id, expires_at) VALUES (%s, %s, %s)
        """, (token, user_id, expires))
        conn.commit()

        return jsonify({"token": token, "username": username, "full_name": full_name})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@bp.post("/api/auth/login")
def auth_login():
    data     = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "Username and password are required."}), 400

    try:
        conn = get_conn()
    except Exception as e:
        return jsonify({"error": f"Database connection failed: {e}"}), 503

    try:
        cur = conn.cursor()
        _execute(cur, "SELECT id, username, full_name, password_hash FROM users WHERE username = %s OR email = %s",
                 (username, username))
        row = cur.fetchone()
        if not row or not check_password_hash(row[3], password):
            return jsonify({"error": "Invalid username or password."}), 401

        user_id   = row[0]
        uname     = row[1]
        full_name = row[2]

        from datetime import datetime, timezone, timedelta
        token   = secrets.token_hex(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

        _execute(cur, """
            INSERT INTO user_sessions (token, user_id, expires_at) VALUES (%s, %s, %s)
        """, (token, user_id, expires))
        conn.commit()

        return jsonify({"token": token, "username": uname, "full_name": full_name})
    finally:
        conn.close()


@bp.post("/api/auth/logout")
def auth_logout():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        conn = get_conn()
        try:
            cur = conn.cursor()
            _execute(cur, "DELETE FROM user_sessions WHERE token = %s", (token,))
            conn.commit()
        finally:
            conn.close()
    return jsonify({"ok": True})


@bp.get("/api/auth/me")
def auth_me():
    try:
        conn = get_conn()
    except Exception as e:
        return jsonify({"error": f"Database connection failed: {e}"}), 503
    try:
        user = _get_auth_user(conn)
        if not user:
            return jsonify({"error": "Not authenticated."}), 401

        cur = conn.cursor()
        _execute(cur, "SELECT id FROM applications WHERE user_id = %s", (user["id"],))
        app_row = cur.fetchone()
        user["has_application"] = app_row is not None
        user["app_id"] = app_row[0] if app_row else None
        return jsonify(user)
    finally:
        conn.close()
