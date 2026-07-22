"""
Shared SQLite access layer for the DNS proxy and the dashboard.
WAL mode lets the proxy (reader/writer) and the dashboard (reader/writer)
touch the database concurrently from separate processes without locking
each other out.
"""

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE = Path(__file__).parent
DATA_DIR = BASE / "data"
DB_PATH = DATA_DIR / "adblock.db"
SCHEMA_PATH = BASE / "schema.sql"

DECISIONS_MAX_ROWS = 50_000  # trim threshold, keeps the log table bounded


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection):
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── domain list lookups / mutations ──────────────────────────────────────

def _candidate_labels(domain: str) -> list[str]:
    """['a.b.example.com', 'b.example.com', 'example.com', 'com']"""
    d = domain.lower().rstrip(".")
    parts = d.split(".")
    return [".".join(parts[i:]) for i in range(len(parts))]


def lookup(conn: sqlite3.Connection, domain: str) -> tuple[str, Optional[str], str]:
    """
    Returns (verdict, matched_domain, source).
    verdict is 'BLOCK' or 'ALLOW'. matched_domain is None on default-allow.
    Walks from the full domain up through parent suffixes so a blocklist
    entry for 'doubleclick.net' also blocks 'ads.doubleclick.net'.
    """
    for label in _candidate_labels(domain):
        row = conn.execute(
            "SELECT list_type, source FROM domains WHERE domain = ?", (label,)
        ).fetchone()
        if row:
            verdict = "BLOCK" if row["list_type"] == "block" else "ALLOW"
            return verdict, label, row["source"]
    return "ALLOW", None, "default"


def add_domain(conn: sqlite3.Connection, domain: str, list_type: str, source: str = "manual"):
    domain = domain.strip().lower()
    conn.execute(
        """INSERT INTO domains (domain, list_type, source, added_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(domain) DO UPDATE SET list_type=excluded.list_type,
                                              source=excluded.source,
                                              added_at=excluded.added_at""",
        (domain, list_type, source, now_iso()),
    )
    conn.commit()


def remove_domain(conn: sqlite3.Connection, domain: str, list_type: Optional[str] = None):
    domain = domain.strip().lower()
    if list_type:
        conn.execute("DELETE FROM domains WHERE domain = ? AND list_type = ?", (domain, list_type))
    else:
        conn.execute("DELETE FROM domains WHERE domain = ?", (domain,))
    conn.commit()


def list_domains(conn: sqlite3.Connection, list_type: str) -> list[str]:
    rows = conn.execute(
        "SELECT domain FROM domains WHERE list_type = ? ORDER BY domain", (list_type,)
    ).fetchall()
    return [r["domain"] for r in rows]


def import_domains(conn: sqlite3.Connection, domains: list[str], list_type: str, source: str) -> int:
    """Bulk import, skipping domains that already exist (manual overrides win)."""
    ts = now_iso()
    rows = [(d.strip().lower(), list_type, source, ts) for d in domains if d.strip()]
    cur = conn.executemany(
        "INSERT OR IGNORE INTO domains (domain, list_type, source, added_at) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return cur.rowcount


# ─── decision log ──────────────────────────────────────────────────────────

_write_count = 0


def log_decision(conn: sqlite3.Connection, domain: str, verdict: str, matched: Optional[str], source: str):
    global _write_count
    conn.execute(
        "INSERT INTO decisions (ts, domain, verdict, matched_domain, source) VALUES (?, ?, ?, ?, ?)",
        (now_iso(), domain, verdict, matched, source),
    )
    conn.commit()
    _write_count += 1
    if _write_count % 1000 == 0:
        _trim_decisions(conn)


def _trim_decisions(conn: sqlite3.Connection):
    conn.execute(
        "DELETE FROM decisions WHERE id NOT IN "
        "(SELECT id FROM decisions ORDER BY id DESC LIMIT ?)",
        (DECISIONS_MAX_ROWS,),
    )
    conn.commit()
