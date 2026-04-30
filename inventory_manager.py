# -*- coding: utf-8 -*-
import requests
import pandas as pd
import psycopg2
from datetime import date
from dateutil.relativedelta import relativedelta
from db_config import DB_CONFIG

AUTH_HEADER = "Basic QVBJUjowMDAx"
PARTBAL_URL = "https://priority.newcinema.co.il/odata/Priority/tabula.ini/ncinema/PARTBAL"

SUPPLIER_NAMES = [
    'AMERICAN TRAVEL', 'BENETTON', 'IT', 'JEEP', 'KIIP',
    'MANDARINA DUCK', 'MEMSI', 'SAMSONITE', 'Travelite',
    'אביזרים ממסי', 'דיאנה נטו', 'דיאנה רגיל', 'דלסי מזוודות',
    'מזוודות NORD BLUE', 'תיקי CAT',
]

def get_active_warehouses_from_db():
    """מחזיר רשימת מחסנים פעילים מהשלושה חודשים האחרונים שקיימים במסד הנתונים."""
    three_months_ago = (date.today() - relativedelta(months=3)).strftime('%Y-%m-%d')
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT branchname
        FROM documents
        WHERE curdate >= %s
          AND branchname IS NOT NULL
          AND branchname != ''
        ORDER BY branchname
    """, (three_months_ago,))
    warehouses = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return warehouses

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

    all_records = []
    while True:
        response = requests.get(PARTBAL_URL, headers=headers, params=params)
        if response.status_code != 200:
            raise Exception(f"שגיאת API ({response.status_code}): {response.text[:300]}")
        batch = response.json().get('value', [])
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
