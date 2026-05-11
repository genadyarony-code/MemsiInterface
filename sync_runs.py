# -*- coding: utf-8 -*-
"""
sync_runs.py — שכבת access לטבלת sync_runs.

מתעד כל ריצה של nightly_sync.py: התחלה, סיום, מה נמשך, איזה שגיאות התרחשו.
ה-GUI קורא את השורה האחרונה כדי להציג ב-status-bar "נתונים נכון ל-{ts}".
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from psycopg2.extras import RealDictCursor
from db_config import get_conn
from logger import logger


def start_run(triggered_by: str = 'scheduler') -> int:
    """פותח ריצה חדשה, מחזיר run_id."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sync_runs (triggered_by, status, records_pulled)
                VALUES (%s, 'running', '{}'::jsonb)
                RETURNING run_id
            """, (triggered_by,))
            run_id = cur.fetchone()[0]
    logger.info("sync_run %d started (triggered_by=%s)", run_id, triggered_by)
    return run_id


def update_progress(run_id: int, records_pulled: dict):
    """מעדכן את records_pulled לפני הסיום (כדי שאם ה-job יקרוס, נדע מה הספיק)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE sync_runs
                SET records_pulled = %s::jsonb
                WHERE run_id = %s
            """, (json.dumps(records_pulled), run_id))


def finish_run(
    run_id: int,
    status: str,                    # 'ok' / 'partial' / 'failed'
    records_pulled: dict | None = None,
    errors_count: int = 0,
    last_error_text: str | None = None,
):
    """סוגר ריצה. מחשב duration_seconds מהפרש מ-started_at."""
    if status not in ('ok', 'partial', 'failed'):
        raise ValueError(f"invalid status: {status!r}")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE sync_runs
                SET finished_at      = NOW(),
                    status           = %s,
                    records_pulled   = COALESCE(%s::jsonb, records_pulled),
                    errors_count     = %s,
                    last_error_text  = %s,
                    duration_seconds = EXTRACT(EPOCH FROM (NOW() - started_at))::int
                WHERE run_id = %s
            """, (
                status,
                json.dumps(records_pulled) if records_pulled is not None else None,
                errors_count,
                (last_error_text or '')[:4000],
                run_id,
            ))
    logger.info("sync_run %d finished: status=%s errors=%d", run_id, status, errors_count)


def get_latest_successful() -> dict | None:
    """מחזיר את הריצה האחרונה שהסתיימה בהצלחה ('ok' או 'partial').
    משמש את ה-status-bar של ה-GUI ל-'נתונים נכון ל-X'."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT run_id, started_at, finished_at, status,
                       records_pulled, duration_seconds
                FROM sync_runs
                WHERE status IN ('ok','partial') AND finished_at IS NOT NULL
                ORDER BY finished_at DESC
                LIMIT 1
            """)
            row = cur.fetchone()
            return dict(row) if row else None


def get_recent(limit: int = 30) -> list[dict]:
    """היסטוריה של ריצות אחרונות, לטובת UI / debug."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM sync_runs
                ORDER BY started_at DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
