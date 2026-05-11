# -*- coding: utf-8 -*-
"""
מנהל Cache - שכבת ניהול נתונים עם PostgreSQL.
החיבורים נלקחים מה-pool של db_config.get_conn ומוחזרים אליו אחרי כל מתודה.
ה-class עצמו אינו מחזיק state של חיבור.
"""
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
from psycopg2.extras import execute_values
from db_config import get_conn
from logger import logger


class CacheManager:
    def __init__(self):
        # אין יותר self.conn — get_conn() דואג לחיבור-לפעולה.
        pass

    def connect(self):
        """תאימות לאחור — לא נדרש כיום, אבל לא להסיר עד שכל הקוראים עברו."""
        return None

    def close(self):
        """תאימות לאחור — אין משאב לסגור."""
        return None

    def get_cached_months(self, data_type):
        """מחזיר רשימת חודשים שכבר נשמרו ב-cache"""
        with get_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT year_month, start_date, end_date FROM cache_metadata WHERE data_type = %s",
                    (data_type,)
                )
                return {row[0]: {'start': row[1], 'end': row[2]} for row in cursor.fetchall()}

    def save_documents(self, documents, year_month):
        """שמירת מסמכים ב-cache (bulk insert עם execute_values)."""
        if not documents:
            return

        rows = [
            (
                doc.get('DOCNO'),
                doc.get('CURDATE'),
                doc.get('CUSTNAME'),
                doc.get('CUSTDES'),
                doc.get('CDES'),
                doc.get('DETAILS'),
                doc.get('STATDES'),
                doc.get('OWNERLOGIN'),
                doc.get('BRANCHNAME'),
                doc.get('RETL_DETAILS1'),
            )
            for doc in documents
        ]

        with get_conn() as conn:
            with conn.cursor() as cursor:
                execute_values(cursor, """
                    INSERT INTO documents (docno, curdate, custname, custdes, cdes, details,
                                         statdes, ownerlogin, branchname, retl_details1)
                    VALUES %s
                    ON CONFLICT (docno) DO NOTHING
                """, rows, page_size=500)

    def save_logfile(self, logfile_records, year_month):
        """שמירת תנועות ב-cache (bulk insert עם execute_values).
        מדלג על שורות בלי LOGDOCNO (ה-unique index הוא partial WHERE logdocno IS NOT NULL).
        """
        if not logfile_records:
            return

        rows = []
        skipped = 0
        for log in logfile_records:
            logdocno = log.get('LOGDOCNO')
            if logdocno is None or logdocno == '':
                skipped += 1
                continue
            rows.append((
                logdocno,
                log.get('CURDATE'),
                log.get('PARTNAME'),
                log.get('TOPARTDES'),
                log.get('TQUANT'),
                log.get('UCOST'),
                log.get('CUSTNAME'),
            ))

        if rows:
            # ה-WHERE כאן חייב להיות זהה ל-WHERE של ה-partial unique index
            # (uq_logfile_row), אחרת PostgreSQL לא יזהה אותו ל-ON CONFLICT.
            with get_conn() as conn:
                with conn.cursor() as cursor:
                    execute_values(cursor, """
                        INSERT INTO logfile (logdocno, curdate, partname, topartdes,
                                            tquant, ucost, custname)
                        VALUES %s
                        ON CONFLICT (logdocno, partname, topartdes, tquant, ucost, curdate)
                          WHERE logdocno IS NOT NULL
                          DO NOTHING
                    """, rows, page_size=500)

        if skipped:
            logger.info("save_logfile: skipped %d rows with null LOGDOCNO", skipped)

    def update_metadata(self, data_type, year_month, start_date, end_date, count):
        """עדכון מטא-דאטה"""
        with get_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO cache_metadata (data_type, year_month, start_date, end_date, record_count)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (data_type, year_month)
                    DO UPDATE SET record_count = %s, fetched_at = NOW()
                """, (data_type, year_month, start_date, end_date, count, count))

    def get_documents(self, start_date, end_date):
        """שליפת מסמכים מ-cache"""
        with get_conn() as conn:
            query = """
                SELECT docno, curdate, custname, custdes, cdes, details,
                       statdes, ownerlogin, branchname, retl_details1
                FROM documents
                WHERE curdate >= %s AND curdate <= %s
            """
            df = pd.read_sql_query(query, conn, params=(start_date, end_date))
        # המרה למבנה API (שמות עמודות באותיות גדולות)
        return [{
            'DOCNO': row['docno'],
            'CURDATE': row['curdate'],
            'CUSTNAME': row['custname'],
            'CUSTDES': row['custdes'],
            'CDES': row['cdes'],
            'DETAILS': row['details'],
            'STATDES': row['statdes'],
            'OWNERLOGIN': row['ownerlogin'],
            'BRANCHNAME': row['branchname'],
            'RETL_DETAILS1': row['retl_details1']
        } for _, row in df.iterrows()]

    def get_logfile(self, start_date, end_date):
        """שליפת תנועות מ-cache"""
        with get_conn() as conn:
            query = """
                SELECT logdocno, curdate, partname, topartdes, tquant, ucost, custname
                FROM logfile
                WHERE curdate >= %s AND curdate <= %s
            """
            df = pd.read_sql_query(query, conn, params=(start_date, end_date))
        # המרה למבנה API (שמות עמודות באותיות גדולות)
        return [{
            'LOGDOCNO': row['logdocno'],
            'CURDATE': row['curdate'],
            'PARTNAME': row['partname'],
            'TOPARTDES': row['topartdes'],
            'TQUANT': row['tquant'],
            'UCOST': row['ucost'],
            'CUSTNAME': row['custname']
        } for _, row in df.iterrows()]

    def get_missing_months(self, start_date, end_date, data_type):
        """מחזיר רשימת חודשים שחסרים ב-cache"""
        cached = self.get_cached_months(data_type)

        current = datetime.strptime(start_date, '%Y-%m-%d').date()
        end = datetime.strptime(end_date, '%Y-%m-%d').date()

        missing = []
        while current <= end:
            year_month = current.strftime('%Y-%m')
            if year_month not in cached:
                missing.append(year_month)
            current = (current + relativedelta(months=1)).replace(day=1)

        return missing

    def clear_month_data(self, year_month):
        """מוחק נתונים של חודש ספציפי מה-cache"""
        with get_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    DELETE FROM documents
                    WHERE TO_CHAR(curdate, 'YYYY-MM') = %s
                """, (year_month,))

                cursor.execute("""
                    DELETE FROM logfile
                    WHERE TO_CHAR(curdate, 'YYYY-MM') = %s
                """, (year_month,))

                cursor.execute("""
                    DELETE FROM cache_metadata
                    WHERE year_month = %s
                """, (year_month,))

        logger.info("נתוני חודש %s נמחקו מה-cache", year_month)
