"""User management — hashed passwords stored in config/users.json."""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)

USERS_PATH = Path(__file__).parent.parent / "config" / "users.json"


def _load() -> list[dict]:
    if USERS_PATH.exists():
        try:
            return json.loads(USERS_PATH.read_text())
        except Exception:
            pass
    return []


def _save(users: list[dict]) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    USERS_PATH.write_text(json.dumps(users, indent=2))


def init_default() -> None:
    """Create default admin from env vars on first run if no users exist."""
    if _load():
        return
    import secrets as _secrets
    username = os.environ.get("ADMIN_USER", "admin")
    password = os.environ.get("ADMIN_PASS", "")
    generated = False
    if len(password) < 8:
        password = _secrets.token_urlsafe(12)
        generated = True
    create_user(username, password, role="admin")
    if generated:
        logger.warning(
            "ADMIN_PASS not set or too short — generated a random password.\n"
            "  Username : %s\n"
            "  Password : %s\n"
            "Change it in Settings → Users after first login.",
            username, password,
        )
    else:
        logger.info("Default admin user '%s' created from environment.", username)


def verify(username: str, password: str) -> dict | None:
    """Return user dict (without hash) if credentials are valid, else None."""
    for u in _load():
        if u["username"] == username and check_password_hash(u["password_hash"], password):
            return {"username": u["username"], "role": u.get("role", "viewer")}
    return None


def list_users() -> list[dict]:
    return [{"username": u["username"], "role": u.get("role", "viewer")}
            for u in _load()]


def create_user(username: str, password: str, role: str = "viewer") -> None:
    if not username or not password:
        raise ValueError("Username and password are required.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    users = _load()
    if any(u["username"] == username for u in users):
        raise ValueError(f"User '{username}' already exists.")
    users.append({
        "username": username,
        "password_hash": generate_password_hash(password),
        "role": role,
    })
    _save(users)
    logger.info("User '%s' created (role=%s).", username, role)


def update_password(username: str, new_password: str) -> None:
    if len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    users = _load()
    for u in users:
        if u["username"] == username:
            u["password_hash"] = generate_password_hash(new_password)
            _save(users)
            logger.info("Password updated for '%s'.", username)
            return
    raise ValueError(f"User '{username}' not found.")


def delete_user(username: str) -> None:
    users = [u for u in _load() if u["username"] != username]
    _save(users)
    logger.info("User '%s' deleted.", username)


def admin_count() -> int:
    return sum(1 for u in _load() if u.get("role") == "admin")
