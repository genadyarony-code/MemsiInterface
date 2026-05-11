# -*- coding: utf-8 -*-
import requests
import pandas as pd
from datetime import date
from dateutil.relativedelta import relativedelta
from db_config import get_conn

import os
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# הערה: load_dotenv כבר רץ ב-db_config.py בעת import. אין צורך לקרוא לו שוב.

AUTH_HEADER = os.environ['PRIORITY_AUTH_HEADER']
_BASE_URL   = os.environ.get('PRIORITY_BASE_URL', 'https://priority.newcinema.co.il/odata/Priority/tabula.ini/ncinema')
PARTBAL_URL = f"{_BASE_URL}/PARTBAL"

_RETRY = retry(
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)

SUPPLIER_NAMES = [
    'AMERICAN TRAVEL', 'BENETTON', 'IT', 'JEEP', 'KIIP',
    'MANDARINA DUCK', 'MEMSI', 'SAMSONITE', 'Travelite',
    'אביזרים ממסי', 'דיאנה נטו', 'דיאנה רגיל', 'דלסי מזוודות',
    'מזוודות NORD BLUE', 'תיקי CAT',
]

def get_active_warehouses_from_db():
    """מחזיר רשימת מחסנים פעילים מהשלושה חודשים האחרונים שקיימים במסד הנתונים."""
    three_months_ago = (date.today() - relativedelta(months=3)).strftime('%Y-%m-%d')
    with get_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT branchname
                FROM documents
                WHERE curdate >= %s
                  AND branchname IS NOT NULL
                  AND branchname != ''
                ORDER BY branchname
            """, (three_months_ago,))
            return [row[0] for row in cursor.fetchall()]

def fetch_partbal_inventory(warehouse_filter=None, progress_callback=None):
    """
    מושך מלאי מ-PARTBAL לכל הספקים המוגדרים.
    מסנן לפי מחסנים נבחרים (מחרוזות כמו '05', '12') אם סופק.
    לא מאחסן – מידע דינמי בלבד.
    """
    headers = {"Authorization": AUTH_HEADER, "Accept": "application/json"}

    supplier_filter = ' or '.join(
        [f"Y_2075_5_ESH eq '{s}'" for s in SUPPLIER_NAMES]
    )

    params = {
        '$filter': f"({supplier_filter})",
        '$select': 'WARHSNAME,PARTNAME,PARTDES,TBALANCE,Y_2074_5_ESH,Y_2075_5_ESH',
        '$top': 1000,
        '$skip': 0,
    }

    @_RETRY
    def _fetch_page(p):
        r = requests.get(PARTBAL_URL, headers=headers, params=p, timeout=30)
        if r.status_code != 200:
            raise Exception(f"שגיאת API ({r.status_code}): {r.text[:300]}")
        return r.json().get('value', [])

    all_records = []
    while True:
        batch = _fetch_page(params)
        all_records.extend(batch)
        if progress_callback:
            progress_callback(len(all_records))
        if len(batch) < 1000:
            break
        params['$skip'] += 1000

    if not all_records:
        return pd.DataFrame(columns=['מחסן', 'מקט', 'תיאור מוצר', 'יתרה', 'קוד ספק', 'ספק'])

    df = pd.DataFrame(all_records)

    if warehouse_filter:
        df = df[df['WARHSNAME'].isin(warehouse_filter)]

    df.rename(columns={
        'WARHSNAME': 'מחסן',
        'PARTNAME': 'מקט',
        'PARTDES': 'תיאור מוצר',
        'TBALANCE': 'יתרה',
        'Y_2074_5_ESH': 'קוד ספק',
        'Y_2075_5_ESH': 'ספק',
    }, inplace=True)

    return df[['מחסן', 'מקט', 'תיאור מוצר', 'יתרה', 'קוד ספק', 'ספק']]
