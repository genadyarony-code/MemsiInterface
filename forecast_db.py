# -*- coding: utf-8 -*-
import pandas as pd
import os
from db_config import get_conn

EVENTS_CSV = os.path.join(os.path.dirname(__file__), 'forecast_events.csv')

CREATE_FORECAST_HISTORY = """
CREATE TABLE IF NOT EXISTS forecast_history (
    id              SERIAL PRIMARY KEY,
    branch          TEXT NOT NULL,
    luggage_type    TEXT NOT NULL,
    year_month      TEXT NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE (branch, luggage_type, year_month)
);
"""

CREATE_FORECAST_EVENTS = """
CREATE TABLE IF NOT EXISTS forecast_events (
    year_month       TEXT PRIMARY KEY,
    is_war           SMALLINT DEFAULT 0,
    is_military_op   SMALLINT DEFAULT 0,
    is_ceasefire     SMALLINT DEFAULT 0,
    jewish_holiday   SMALLINT DEFAULT 0,
    season           SMALLINT DEFAULT 0,
    is_summer_peak   SMALLINT DEFAULT 0,
    travel_impact    TEXT DEFAULT 'normal',
    notes            TEXT DEFAULT ''
);
"""


class ForecastDB:
    """
    שכבת גישה ל-forecast_history ו-forecast_events.
    החל מ-A1: כל מתודה לוקחת חיבור מה-pool של db_config.get_conn ומחזירה אותו
    מיד אחרי השימוש. ה-class אינו מחזיק state של חיבור.

    שמירה על תאימות-לאחור: __enter__/__exit__ נשמרים כדי שהקוראים הקיימים
    שמשתמשים ב-`with ForecastDB() as fdb:` ימשיכו לעבוד; הם פשוט no-op.
    """

    def __init__(self):
        # אין יותר חיבור-לכל-החיים.
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # אין משאב לסגור — get_conn מטפל לכל מתודה בנפרד.
        return False

    def close(self):
        return None

    def setup_tables(self):
        """יצירת טבלאות אם לא קיימות + טעינת אירועים מ-CSV"""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(CREATE_FORECAST_HISTORY)
                cur.execute(CREATE_FORECAST_EVENTS)
        self._load_events_from_csv()

    def _load_events_from_csv(self):
        """טוען forecast_events.csv לטבלה — מדלג על שורות קיימות"""
        if not os.path.exists(EVENTS_CSV):
            return
        df = pd.read_csv(EVENTS_CSV)
        with get_conn() as conn:
            with conn.cursor() as cur:
                for _, row in df.iterrows():
                    cur.execute("""
                        INSERT INTO forecast_events
                            (year_month, is_war, is_military_op, is_ceasefire,
                             jewish_holiday, season, is_summer_peak, travel_impact, notes)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (year_month) DO NOTHING
                    """, (
                        str(row['year_month']),
                        int(row['is_war']),
                        int(row['is_military_op']),
                        int(row['is_ceasefire']),
                        int(row['jewish_holiday']),
                        int(row['season']),
                        int(row['is_summer_peak']),
                        str(row['travel_impact']),
                        str(row['notes']),
                    ))

    # ------------------------------------------------------------------ #
    #  forecast_history                                                    #
    # ------------------------------------------------------------------ #

    def upsert_history(self, branch: str, luggage_type: str,
                       year_month: str, quantity: int):
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO forecast_history (branch, luggage_type, year_month, quantity)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (branch, luggage_type, year_month)
                    DO UPDATE SET quantity = EXCLUDED.quantity, updated_at = NOW()
                """, (branch, luggage_type, year_month, quantity))

    def bulk_upsert_history(self, records: list[dict]):
        """records: [{'branch','luggage_type','year_month','quantity'}]"""
        with get_conn() as conn:
            with conn.cursor() as cur:
                for r in records:
                    cur.execute("""
                        INSERT INTO forecast_history (branch, luggage_type, year_month, quantity)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (branch, luggage_type, year_month)
                        DO UPDATE SET quantity = EXCLUDED.quantity, updated_at = NOW()
                    """, (r['branch'], r['luggage_type'], r['year_month'], r['quantity']))

    def get_history(self, branches: list[str] | None = None,
                    luggage_types: list[str] | None = None) -> pd.DataFrame:
        """מחזיר DataFrame: branch, luggage_type, year_month, quantity"""
        query = "SELECT branch, luggage_type, year_month, quantity FROM forecast_history"
        conditions, params = [], []
        if branches:
            conditions.append("branch = ANY(%s)")
            params.append(branches)
        if luggage_types:
            conditions.append("luggage_type = ANY(%s)")
            params.append(luggage_types)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY branch, luggage_type, year_month"
        with get_conn() as conn:
            return pd.read_sql_query(query, conn, params=params or None)

    def get_covered_months(self) -> set[str]:
        """חודשים שכבר קיימים ב-forecast_history"""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT year_month FROM forecast_history")
                return {row[0] for row in cur.fetchall()}

    def get_branches(self) -> list[str]:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT branch FROM forecast_history ORDER BY branch")
                return [r[0] for r in cur.fetchall()]

    def get_active_branches(self, inactive_months: int = 5) -> list[str]:
        """
        מחזיר סניפים שהיו פעילים בתוך inactive_months האחרונים ביחס
        לחודש האחרון שיש בו נתונים בכלל ב-forecast_history.
        משמש לסינון רשימת הסניפים בממשק התחזיות.
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(year_month) FROM forecast_history")
                latest = cur.fetchone()[0]
                if not latest:
                    return []
                from datetime import datetime
                from dateutil.relativedelta import relativedelta
                cutoff = (datetime.strptime(latest + "-01", "%Y-%m-%d")
                          - relativedelta(months=inactive_months - 1)
                          ).strftime("%Y-%m")
                cur.execute("""
                    SELECT DISTINCT branch
                    FROM forecast_history
                    WHERE year_month >= %s
                    ORDER BY branch
                """, (cutoff,))
                return [r[0] for r in cur.fetchall()]

    def get_months_for_branch(self, branch: str) -> list[str]:
        """מחזיר רשימת חודשים שיש בהם נתונים לסניף הנתון"""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT year_month FROM forecast_history WHERE branch = %s",
                    (branch,)
                )
                return [r[0] for r in cur.fetchall()]

    def delete_branch_history(self, branch: str):
        """מוחק את כל ההיסטוריה של סניף מסוים"""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM forecast_history WHERE branch = %s", (branch,))

    # ------------------------------------------------------------------ #
    #  forecast_events                                                     #
    # ------------------------------------------------------------------ #

    def get_events(self) -> pd.DataFrame:
        with get_conn() as conn:
            return pd.read_sql_query(
                "SELECT * FROM forecast_events ORDER BY year_month",
                conn
            )

    def get_event(self, year_month: str) -> dict | None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM forecast_events WHERE year_month = %s", (year_month,))
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))

    def upsert_event(self, year_month: str, **kwargs):
        fields = ['is_war', 'is_military_op', 'is_ceasefire',
                  'jewish_holiday', 'season', 'is_summer_peak',
                  'travel_impact', 'notes']
        data = {f: kwargs.get(f) for f in fields if f in kwargs}
        if not data:
            return
        set_clause = ", ".join(f"{k} = %s" for k in data)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    INSERT INTO forecast_events (year_month, {', '.join(data.keys())})
                    VALUES (%s, {', '.join(['%s'] * len(data))})
                    ON CONFLICT (year_month) DO UPDATE SET {set_clause}
                """, [year_month] + list(data.values()) + list(data.values()))
