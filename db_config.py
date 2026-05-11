# -*- coding: utf-8 -*-
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse, unquote
from dotenv import load_dotenv
from psycopg2 import pool as _pool

# טוען .env.local קודם (לעדיפות מקומית), נופל ל-.env לתאימות לאחור
_HERE = Path(__file__).parent
if (_HERE / '.env.local').exists():
    load_dotenv(_HERE / '.env.local')
else:
    load_dotenv(_HERE / '.env')


def _build_db_config() -> dict:
    """
    בונה את DB_CONFIG. עדיפות: DATABASE_URL → משתני DB_* בודדים → ברירות-מחדל.
    DATABASE_URL בפורמט postgres://user:password@host:port/dbname.
    """
    url = os.environ.get('DATABASE_URL')
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in ('postgres', 'postgresql'):
            raise ValueError(
                f"DATABASE_URL scheme לא נתמך: {parsed.scheme!r} (חייב postgres/postgresql)"
            )
        return {
            'host':     parsed.hostname or 'localhost',
            'port':     parsed.port or 5432,
            'database': (parsed.path or '/').lstrip('/') or 'priority_cache',
            'user':     unquote(parsed.username) if parsed.username else 'postgres',
            'password': unquote(parsed.password) if parsed.password else '',
        }
    return {
        'host':     os.environ.get('DB_HOST',     'localhost'),
        'port':     int(os.environ.get('DB_PORT', 5432)),
        'database': os.environ.get('DB_NAME',     'priority_cache'),
        'user':     os.environ.get('DB_USER',     'postgres'),
        'password': os.environ.get('DB_PASSWORD', ''),
    }


DB_CONFIG = _build_db_config()

# ============================================================
#  Connection pool — עם lazy init כדי שייבוא הקובץ לא ייכשל
#  כש-DB לא זמין (למשל בזמן בנייה של PyInstaller).
# ============================================================
_POOL_MIN = int(os.environ.get('DB_POOL_MIN', 1))
_POOL_MAX = int(os.environ.get('DB_POOL_MAX', 10))
_pool_instance: _pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> _pool.ThreadedConnectionPool:
    global _pool_instance
    if _pool_instance is None:
        with _pool_lock:
            if _pool_instance is None:
                _pool_instance = _pool.ThreadedConnectionPool(
                    _POOL_MIN, _POOL_MAX, **DB_CONFIG
                )
    return _pool_instance


@contextmanager
def get_conn(*, autocommit: bool = False):
    """
    Context manager שמחזיר חיבור מה-pool, מבצע commit אם הבלוק הסתיים בהצלחה
    ו-rollback אם נזרקה חריגה. החיבור תמיד מוחזר ל-pool ב-finally.

    שימוש:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT ...")
        # commit אוטומטי ביציאה תקינה; rollback אם נזרקה חריגה.

    autocommit=True מבטל את ה-commit/rollback האוטומטי ומעביר את האחריות לקורא
    (שימושי למיגרציות או למצבי DDL מיוחדים).
    """
    p = _get_pool()
    conn = p.getconn()
    try:
        yield conn
    except Exception:
        if not autocommit:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    else:
        if not autocommit:
            try:
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    finally:
        p.putconn(conn)


def close_pool():
    """לסגירה נקייה ביציאה מהאפליקציה."""
    global _pool_instance
    if _pool_instance is not None:
        with _pool_lock:
            if _pool_instance is not None:
                _pool_instance.closeall()
                _pool_instance = None
