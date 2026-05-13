"""Append-only audit and login log stored in the main SQLite DB."""
from __future__ import annotations
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "config" / "casino_ads.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    event    TEXT NOT NULL,
    username TEXT,
    ip       TEXT,
    detail   TEXT
);
CREATE INDEX IF NOT EXISTS audit_log_ts    ON audit_log(ts    DESC);
CREATE INDEX IF NOT EXISTS audit_log_event ON audit_log(event);
"""

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    for stmt in _SCHEMA.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
    return conn


def log(event: str, username: str | None = None, ip: str | None = None, **detail) -> None:
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    detail_json = json.dumps(detail, ensure_ascii=False) if detail else None
    try:
        conn = _connect()
        conn.execute(
            "INSERT INTO audit_log (ts, event, username, ip, detail) VALUES (?,?,?,?,?)",
            (ts, event, username, ip, detail_json),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error("audit_log write failed: %s", exc)


def query(event: str = "", username: str = "", limit: int = 200, offset: int = 0) -> list[dict]:
    conn = _connect()
    where, params = _build_where(event, username)
    rows = conn.execute(
        f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count(event: str = "", username: str = "") -> int:
    conn = _connect()
    where, params = _build_where(event, username)
    n = conn.execute(f"SELECT COUNT(*) FROM audit_log {where}", params).fetchone()[0]
    conn.close()
    return n


def distinct_events() -> list[str]:
    conn = _connect()
    rows = conn.execute("SELECT DISTINCT event FROM audit_log ORDER BY event").fetchall()
    conn.close()
    return [r[0] for r in rows]


def _build_where(event: str, username: str) -> tuple[str, list]:
    clauses, params = [], []
    if event:
        clauses.append("event = ?")
        params.append(event)
    if username:
        clauses.append("username LIKE ?")
        params.append(f"%{username}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params
