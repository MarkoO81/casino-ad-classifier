"""SQLite persistence layer for scan history and ad records."""
from __future__ import annotations
import hashlib
import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "config" / "casino_ads.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    sources      TEXT,
    duration_s   REAL DEFAULT 0,
    total        INTEGER DEFAULT 0,
    flagged_high INTEGER DEFAULT 0,
    flagged_review INTEGER DEFAULT 0,
    licensed     INTEGER DEFAULT 0,
    not_casino   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ads (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key        TEXT UNIQUE NOT NULL,
    ad_id            TEXT,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    first_scan_id    INTEGER REFERENCES scans(id),
    last_scan_id     INTEGER REFERENCES scans(id),
    scan_count       INTEGER DEFAULT 1,
    advertiser       TEXT,
    paid_for_by      TEXT,
    text             TEXT,
    url              TEXT,
    ad_permalink     TEXT,
    impressions      TEXT,
    spend_range      TEXT,
    country_delivery TEXT,
    platforms        TEXT,
    start_date       TEXT,
    source           TEXT,
    label            TEXT,
    score            REAL,
    final_domain     TEXT,
    country          TEXT,
    query            TEXT,
    search_url       TEXT,
    raw_signals      TEXT
);

CREATE INDEX IF NOT EXISTS idx_ads_label     ON ads(label);
CREATE INDEX IF NOT EXISTS idx_ads_source    ON ads(source);
CREATE INDEX IF NOT EXISTS idx_ads_last_seen ON ads(last_seen);
CREATE INDEX IF NOT EXISTS idx_ads_advertiser ON ads(advertiser);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def insert_scan(conn: sqlite3.Connection, ts: str, sources: list,
                duration_s: float, counts: dict) -> int:
    cur = conn.execute(
        """INSERT INTO scans (ts, sources, duration_s, total, flagged_high,
           flagged_review, licensed, not_casino)
           VALUES (?,?,?,?,?,?,?,?)""",
        (ts, json.dumps(sources), round(duration_s, 1),
         counts.get("total", 0), counts.get("flagged_high", 0),
         counts.get("flagged_review", 0), counts.get("licensed", 0),
         counts.get("not_casino", 0)),
    )
    conn.commit()
    return cur.lastrowid


def upsert_ads(conn: sqlite3.Connection, ads: list[dict],
               scan_id: int, ts: str) -> tuple[int, int]:
    """Insert new ads or update existing ones. Returns (inserted, updated)."""
    inserted = updated = 0
    for ad in ads:
        key = _dedup_key(ad)
        existing = conn.execute(
            "SELECT id, scan_count FROM ads WHERE dedup_key=?", (key,)
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE ads SET last_seen=?, last_scan_id=?, scan_count=scan_count+1,
                   label=?, score=?, impressions=?, spend_range=?, country_delivery=?,
                   raw_signals=?
                   WHERE dedup_key=?""",
                (ts, scan_id, ad.get("label"), ad.get("score"),
                 ad.get("impressions"), ad.get("spend_range"),
                 ad.get("country_delivery"),
                 json.dumps(ad.get("raw_signals") or []), key),
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO ads (dedup_key, ad_id, first_seen, last_seen,
                   first_scan_id, last_scan_id, scan_count,
                   advertiser, paid_for_by, text, url, ad_permalink,
                   impressions, spend_range, country_delivery, platforms,
                   start_date, source, label, score, final_domain,
                   country, query, search_url, raw_signals)
                   VALUES (?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, ad.get("ad_id"), ts, ts, scan_id, scan_id,
                 ad.get("advertiser"), ad.get("paid_for_by"),
                 ad.get("ad_text") or ad.get("text"),
                 ad.get("landing_url") or ad.get("url"),
                 ad.get("ad_permalink"), ad.get("impressions"),
                 ad.get("spend_range"), ad.get("country_delivery"),
                 ad.get("platforms"), ad.get("start_date"),
                 ad.get("source"), ad.get("label"), ad.get("score"),
                 ad.get("final_domain"), ad.get("country"),
                 ad.get("page_name") or ad.get("query"),
                 ad.get("search_url"),
                 json.dumps(ad.get("raw_signals") or [])),
            )
            inserted += 1

    conn.commit()
    return inserted, updated


def query_ads(label: str = "", source: str = "", advertiser: str = "",
              days: int = 0, limit: int = 200, offset: int = 0) -> list[dict]:
    conn = connect()
    clauses, params = [], []
    if label:
        clauses.append("label=?"); params.append(label)
    if source:
        clauses.append("source=?"); params.append(source)
    if advertiser:
        clauses.append("advertiser LIKE ?"); params.append(f"%{advertiser}%")
    if days:
        clauses.append("last_seen >= datetime('now', ?)")
        params.append(f"-{days} days")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM ads {where} ORDER BY last_seen DESC LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def count_ads(label: str = "", source: str = "", days: int = 0) -> int:
    conn = connect()
    clauses, params = [], []
    if label:
        clauses.append("label=?"); params.append(label)
    if source:
        clauses.append("source=?"); params.append(source)
    if days:
        clauses.append("last_seen >= datetime('now', ?)"); params.append(f"-{days} days")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    n = conn.execute(f"SELECT COUNT(*) FROM ads {where}", params).fetchone()[0]
    conn.close()
    return n


def query_scans(limit: int = 50) -> list[dict]:
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM scans ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trend(days: int = 30) -> list[dict]:
    """Return daily counts of flagged ads for the trend chart."""
    conn = connect()
    rows = conn.execute("""
        SELECT date(last_seen) as day,
               SUM(CASE WHEN label='casino_high_confidence' THEN 1 ELSE 0 END) as high,
               SUM(CASE WHEN label='casino_review' THEN 1 ELSE 0 END) as review,
               COUNT(*) as total
        FROM ads
        WHERE last_seen >= datetime('now', ?)
        GROUP BY day ORDER BY day
    """, (f"-{days} days",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _dedup_key(ad: dict) -> str:
    ad_id = (ad.get("ad_id") or "").strip()
    if ad_id:
        return ad_id
    raw = f"{ad.get('advertiser','')}{(ad.get('ad_text') or ad.get('text',''))[:120]}"
    return "hash:" + hashlib.md5(raw.encode()).hexdigest()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["raw_signals"] = json.loads(d.get("raw_signals") or "[]")
    except Exception:
        d["raw_signals"] = []
    # Template-compatibility aliases (results.html expects these names)
    d["landing_url"] = d.get("url") or ""
    d["ad_text"]     = d.get("text") or ""
    d["page_name"]   = d.get("query") or ""
    d["ts"]          = d.get("last_seen") or ""
    return d
