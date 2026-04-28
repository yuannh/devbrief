import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from . import config as cfg

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    project_path TEXT NOT NULL,
    project_name TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    title        TEXT,
    problem      TEXT,
    approach     TEXT,
    outcome      TEXT,
    summary      TEXT,
    raw_path     TEXT,
    created_at   TEXT NOT NULL
);
"""


@dataclass
class Session:
    session_id: str
    project_path: str
    project_name: str
    started_at: datetime
    title: str
    problem: str
    approach: str
    outcome: str
    summary: str
    raw_path: str


def _connect() -> sqlite3.Connection:
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def upsert(session: Session) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions
                (session_id, project_path, project_name, started_at, title,
                 problem, approach, outcome, summary, raw_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                title=excluded.title, problem=excluded.problem,
                approach=excluded.approach, outcome=excluded.outcome,
                summary=excluded.summary
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
                datetime.now(tz=timezone.utc).isoformat(),
            ),
        )


def exists(session_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ? AND title IS NOT NULL",
            (session_id,),
        ).fetchone()
        return row is not None


def get_all(limit: int = 50) -> list[Session]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE title IS NOT NULL ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


def get_by_date_range(start: date, end: date) -> list[Session]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM sessions
               WHERE title IS NOT NULL
                 AND date(started_at) BETWEEN ? AND ?
               ORDER BY started_at DESC""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


def get(session_id: str) -> Session | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    return _row_to_session(row) if row else None


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(
        session_id=row["session_id"],
        project_path=row["project_path"],
        project_name=row["project_name"],
        started_at=datetime.fromisoformat(row["started_at"]),
        title=row["title"] or "",
        problem=row["problem"] or "",
        approach=row["approach"] or "",
        outcome=row["outcome"] or "",
        summary=row["summary"] or "",
        raw_path=row["raw_path"] or "",
    )
