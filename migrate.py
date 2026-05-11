# -*- coding: utf-8 -*-
"""
runner מיגרציות בסיסי.

מריץ את כל הקבצים ב-migrations/ לפי סדר האלפבית, מתעד את מה שכבר רץ
בטבלת schema_version, ומונע הרצה כפולה.

תומך בשני סוגי קבצים:
  *.sql  מבוצע ישירות עם psycopg2
  *.py   מיובא ומריץ run(conn) מתוכו

שימוש:
    python migrate.py            מריץ מיגרציות שעוד לא רצו
    python migrate.py --status   מציג מה רץ ומה לא
    python migrate.py --dry-run  בדיקה בלי לבצע
"""
import os
import sys
import argparse
import importlib.util
from pathlib import Path

from db_config import get_conn
from logger import logger

MIGRATIONS_DIR = Path(__file__).parent / 'migrations'

CREATE_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMP DEFAULT NOW(),
    checksum    TEXT
);
"""


def _list_migrations() -> list[Path]:
    if not MIGRATIONS_DIR.exists():
        return []
    files = sorted(
        f for f in MIGRATIONS_DIR.iterdir()
        if f.is_file()
        and f.suffix in ('.sql', '.py')
        and not f.name.startswith('_')
    )
    return files


def _applied(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(CREATE_SCHEMA_VERSION)
        conn.commit()
        cur.execute("SELECT filename FROM schema_version")
        return {row[0] for row in cur.fetchall()}


def _apply_sql(conn, path: Path):
    sql = path.read_text(encoding='utf-8')
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def _apply_py(conn, path: Path):
    spec = importlib.util.spec_from_file_location(f"_mig_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, 'run'):
        raise RuntimeError(f"{path.name}: missing run(conn) function")
    mod.run(conn)


def _record(conn, filename: str):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO schema_version (filename) VALUES (%s) ON CONFLICT DO NOTHING",
            (filename,)
        )
    conn.commit()


def run_all(dry_run: bool = False) -> int:
    # autocommit=True: migrate.py מנהל את ה-commit/rollback בעצמו פר-מיגרציה,
    # כך שהוא לא ירוקלל אם אחת מצליחה ושנייה נכשלת.
    with get_conn(autocommit=True) as conn:
        applied = _applied(conn)
        files = _list_migrations()
        pending = [f for f in files if f.name not in applied]

        if not pending:
            print(f"All migrations applied ({len(applied)}/{len(files)}).")
            return 0

        print(f"Pending: {len(pending)}")
        for f in pending:
            print(f"  - {f.name}")

        if dry_run:
            print("\n[dry-run] not applying.")
            return 0

        for f in pending:
            print(f"\n>>> {f.name}")
            try:
                if f.suffix == '.sql':
                    _apply_sql(conn, f)
                else:
                    _apply_py(conn, f)
                _record(conn, f.name)
                print(f"    ok")
                logger.info("migration applied: %s", f.name)
            except Exception as e:
                conn.rollback()
                logger.exception("migration failed: %s", f.name)
                print(f"    FAILED: {type(e).__name__}: {e}")
                return 1

        return 0


def show_status() -> int:
    with get_conn(autocommit=True) as conn:
        applied = _applied(conn)
        files = _list_migrations()
        print(f"{len(applied)} applied, {len(files)} total\n")
        for f in files:
            mark = '[x]' if f.name in applied else '[ ]'
            print(f"  {mark} {f.name}")
        unknown = applied - {f.name for f in files}
        if unknown:
            print("\nApplied but file missing:")
            for u in sorted(unknown):
                print(f"  ??  {u}")
        return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--status', action='store_true', help='show what is applied')
    ap.add_argument('--dry-run', action='store_true', help='list pending without applying')
    args = ap.parse_args()

    if args.status:
        return show_status()
    return run_all(dry_run=args.dry_run)


if __name__ == '__main__':
    sys.exit(main())
