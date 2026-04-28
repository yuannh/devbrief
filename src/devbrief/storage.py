import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from . import config as cfg

# ── schema ────────────────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    project_path    TEXT NOT NULL DEFAULT '',
    project_name    TEXT NOT NULL DEFAULT '',
    started_at      TEXT NOT NULL DEFAULT '',
    title           TEXT,
    problem         TEXT,
    approach        TEXT,
    outcome         TEXT,
    summary         TEXT,
    raw_path        TEXT,
    created_at      TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    user_turn_count INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT,
    raw_json        TEXT,
    digested_at     TEXT
);
"""

# Columns added after initial schema — applied via ALTER TABLE if missing.
_NEW_COLUMNS: list[tuple[str, str]] = [
    ("status",          "TEXT NOT NULL DEFAULT 'pending'"),
    ("user_turn_count", "INTEGER NOT NULL DEFAULT 0"),
    ("updated_at",      "TEXT"),
    ("raw_json",        "TEXT"),
    ("digested_at",     "TEXT"),
]


# ── dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class Session:
    session_id: str
    project_path: str           # kept as primary cwd field for backward compat
    project_name: str
    started_at: datetime
    title: str
    problem: str
    approach: str
    outcome: str
    summary: str
    raw_path: str               # kept as primary jsonl path for backward compat
    status: str = "digested"
    user_turn_count: int = 0
    updated_at: Optional[datetime] = None
    raw_json: str = ""
    digested_at: Optional[datetime] = None


# ── connection & migration ────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.DB_FILE)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    return conn


def _apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_CREATE_TABLE)

    # Add new columns safely (ignore if already present).
    existing = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    for col_name, col_def in _NEW_COLUMNS:
        if col_name not in existing:
            try:
                conn.execute(
                    f"ALTER TABLE sessions ADD COLUMN {col_name} {col_def}"
                )
            except sqlite3.OperationalError:
                pass  # column already exists in another process

    # Backfill: mark old rows that have a title as 'digested'.
    conn.execute(
        "UPDATE sessions SET status = 'digested' "
        "WHERE title IS NOT NULL AND title != '' AND status = 'pending'"
    )
    conn.commit()


# ── writes ────────────────────────────────────────────────────────────────────

def upsert(session: Session) -> None:
    """Insert or update a fully-digested session."""
    now = datetime.now(tz=timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions
                (session_id, project_path, project_name, started_at, title,
                 problem, approach, outcome, summary, raw_path, created_at,
                 status, user_turn_count, updated_at, raw_json, digested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                project_path    = excluded.project_path,
                project_name    = excluded.project_name,
                title           = excluded.title,
                problem         = excluded.problem,
                approach        = excluded.approach,
                outcome         = excluded.outcome,
                summary         = excluded.summary,
                raw_path        = excluded.raw_path,
                status          = excluded.status,
                user_turn_count = excluded.user_turn_count,
                updated_at      = excluded.updated_at,
                digested_at     = excluded.digested_at
            """,
            (
                session.session_id,
                session.project_path,
                session.project_name,
                session.started_at.isoformat(),
                session.title,
                session.problem,
                session.approach,
                session.outcome,
                session.summary,
                session.raw_path,
                now,
                session.status,
                session.user_turn_count,
                (session.updated_at.isoformat() if session.updated_at else now),
                session.raw_json or "",
                (session.digested_at.isoformat() if session.digested_at else now),
            ),
        )


def upsert_pending(
    session_id: str,
    jsonl_path: str,
    project_name: str,
    cwd: str,
    created_at: str,
    updated_at: str,
    user_turn_count: int,
) -> None:
    """Insert a capture-only (pending) session row.

    Never overwrites an already-digested row's title/summary/status.
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions
                (session_id, project_path, project_name, started_at,
                 raw_path, created_at, status, user_turn_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                project_path    = excluded.project_path,
                project_name    = excluded.project_name,
                raw_path        = excluded.raw_path,
                user_turn_count = excluded.user_turn_count,
                updated_at      = excluded.updated_at
            WHERE sessions.status = 'pending'
            """,
            (
                session_id,
                cwd,
                project_name,
                created_at,
                jsonl_path,
                now,
                user_turn_count,
                updated_at or now,
            ),
        )


# ── reads ─────────────────────────────────────────────────────────────────────

def exists(session_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ? AND title IS NOT NULL AND title != ''",
            (session_id,),
        ).fetchone()
        return row is not None


def get_all(limit: int = 50, include_pending: bool = True) -> list[Session]:
    where = "" if include_pending else "WHERE status = 'digested'"
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM sessions {where} ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


def get_digested(limit: int = 50) -> list[Session]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE status = 'digested' "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


def get_by_date_range(start: date, end: date) -> list[Session]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM sessions
               WHERE status = 'digested'
                 AND date(started_at) BETWEEN ? AND ?
               ORDER BY started_at DESC""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


def get(session_id: str) -> Optional[Session]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return _row_to_session(row) if row else None


def find_by_prefix(session_id_prefix: str) -> list[Session]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE session_id LIKE ? ORDER BY started_at DESC",
            (f"{session_id_prefix}%",),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


def count_all() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]


def count_digested() -> int:
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE status = 'digested'"
        ).fetchone()[0]


# ── internal ─────────────────────────────────────────────────────────────────

def _row_to_session(row: sqlite3.Row) -> Session:
    keys = row.keys()

    def _dt(val: str | None) -> datetime:
        if val:
            try:
                return datetime.fromisoformat(val)
            except ValueError:
                pass
        return datetime.now(tz=timezone.utc)

    def _opt_dt(val: str | None) -> Optional[datetime]:
        if val:
            try:
                return datetime.fromisoformat(val)
            except ValueError:
                pass
        return None

    return Session(
        session_id=row["session_id"],
        project_path=row["project_path"] or "",
        project_name=row["project_name"] or "",
        started_at=_dt(row["started_at"]),
        title=row["title"] or "",
        problem=row["problem"] or "",
        approach=row["approach"] or "",
        outcome=row["outcome"] or "",
        summary=row["summary"] or "",
        raw_path=row["raw_path"] or "",
        status=row["status"] if "status" in keys else "digested",
        user_turn_count=row["user_turn_count"] if "user_turn_count" in keys else 0,
        updated_at=_opt_dt(row["updated_at"] if "updated_at" in keys else None),
        raw_json=row["raw_json"] if "raw_json" in keys else "",
        digested_at=_opt_dt(row["digested_at"] if "digested_at" in keys else None),
    )
